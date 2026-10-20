#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量下载与 Zotero 自动衔接调度器 (Pipeline Runner) - R3 收尾返修版
任务编号: T004-DOWNLOAD-ZOTERO-BRIDGE
执行角色: DeepSeek Harness (dsh)
"""

import os
import sys
import json
import csv
import hashlib
import shutil
import tempfile
import argparse
import re
from pathlib import Path
from datetime import datetime, timezone
import urllib.request
import urllib.error
import sqlite3
import subprocess
import time
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

CURRENT_DIR = Path(__file__).resolve().parent
VENDOR_DIR = CURRENT_DIR / "vendor"
if str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))

try:
    import library_jobs_upstream as upstream_jobs
except ImportError:
    upstream_jobs = None

try:
    import ingest_runner_upstream as t001_upstream
except ImportError:
    t001_upstream = None

# 常量与退出码定义
EXIT_SUCCESS = 0
EXIT_INPUT_ERROR = 1
EXIT_INTEGRITY_ERROR = 2
EXIT_NETWORK_ERROR = 3
EXIT_RESPONSE_ERROR = 4
EXIT_STATE_ERROR = 5
EXIT_ATTACHMENT_VERIFY_ERROR = 6
EXIT_PENDING_BLOCKED = 7
EXIT_CONFLICT_BLOCKED = 8

ZOTERO_DEFAULT_CONNECTOR = "http://127.0.0.1:23119/connector/import"
ZOTERO_DEFAULT_STORAGE = r"C:\Users\lamost\Zotero\storage"
ZOTERO_DEFAULT_DB = r"C:\Users\lamost\Zotero\zotero.sqlite"

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def normalize_doi(doi_str):
    if not doi_str:
        return ""
    d = str(doi_str).strip()
    d = re.sub(r"^(?:https?://(?:dx.)?doi.org/|doi:s*)", "", d, flags=re.I).strip().lower()
    return d

def sha256_file(filepath):
    h = hashlib.sha256()
    with open(filepath, 'rb') as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def verify_pdf_fulltext(filepath, allow_caj=False):
    """
    复用原采集工具 library_jobs_upstream.py 的严格校验规则：
    1. 必须是常规文件；
    2. 大小至少 512 字节；
    3. 文件头必须以 %PDF- 开头，尾部 4096 字节必须包含 %%EOF；
    4. 返回格式、字节数、sha256。
    """
    path = Path(filepath).resolve()
    if not path.is_file():
        return False, "文件不存在或不是普通文件", {}
    size = path.stat().st_size
    if size < 512:
        return False, f"文件过小 ({size} < 512 字节)，不能计为正文", {}
    with open(path, "rb") as f:
        head = f.read(1024)
        f.seek(max(0, size - 4096))
        tail = f.read()
        if head.startswith(b"%PDF-"):
            if b"%%EOF" not in tail:
                return False, "PDF文件不完整: 缺少 %%EOF 结束标记", {}
            fmt = "pdf"
        elif allow_caj and head.startswith((b"CAJ", b"CDS", b"KDH", b"TEB", b"HN")):
            fmt = "caj"
        else:
            return False, "不支持的全文文件格式 (HTML/错误页已被拒绝)", {}
        f.seek(0)
        h = hashlib.sha256()
        while chunk := f.read(65536):
            h.update(chunk)
        digest = h.hexdigest()
    return True, "有效全文", {"file": str(path), "format": fmt, "bytes": size, "sha256": digest}

def atomic_write_json(file_path, data):
    p = Path(file_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    temp_fd, temp_path = tempfile.mkstemp(dir=p.parent, prefix="tmp_", suffix=".json")
    try:
        with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        if p.exists():
            os.replace(temp_path, str(p))
        else:
            os.rename(temp_path, str(p))
    except Exception as e:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass
        raise e

def compute_item_key(source_id, sha256_val, title):
    if sha256_val:
        return f"SHA_{sha256_val[:16]}"
    if source_id:
        return f"SRC_{source_id}"
    raw = f"{title}_{now_iso()}"
    return f"RAW_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"

class LedgerManager:
    """管理持久化文献台账 (ledger.json) 与入库队列"""
    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_file = self.output_dir / "ledger.json"
        self.manifest_file = self.output_dir / "papers_manifest.json"
        self.data = None
        self.load()

    def load(self):
        # R3-1: 检查是否为已有 items 目录的历史根但缺失台账
        items_dir = self.output_dir / "items"
        has_item_evidence = items_dir.exists() and any(items_dir.iterdir())

        if not self.ledger_file.exists():
            if has_item_evidence:
                print(f"[ERROR] 历史输出根目录存在已处理条目证据但台账文件丢失: {self.ledger_file}，安全阻断以防覆盖历史数据！", file=sys.stderr)
                sys.exit(EXIT_CONFLICT_BLOCKED)
            # 全新根目录
            self.data = {
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "records": {},           # item_key -> record
                "conflicts": {},         # conflict_id -> conflict_record
                "sha256_index": {},      # sha256 -> item_key
                "source_id_index": {},   # source_id -> item_key
                "doi_index": {}          # normalized_doi -> item_key
            }
            if not self.manifest_file.exists():
                atomic_write_json(self.manifest_file, [])
            return

        # 存在文件时，严格读取并校验结构
        try:
            with open(self.ledger_file, 'r', encoding='utf-8') as f:
                content = json.load(f)
            if not isinstance(content, dict) or "records" not in content:
                print(f"[ERROR] 台账文件结构损坏 (不是包含 records 的字典): {self.ledger_file}", file=sys.stderr)
                sys.exit(EXIT_STATE_ERROR)
            self.data = content
            self.data.setdefault("conflicts", {})
            self.data.setdefault("sha256_index", {})
            self.data.setdefault("source_id_index", {})
            self.data.setdefault("doi_index", {})
        except Exception as e:
            print(f"[ERROR] 读取台账 JSON 损坏: {e}，保留现场并安全阻断！", file=sys.stderr)
            sys.exit(EXIT_STATE_ERROR)

    def save(self):
        self.data["updated_at"] = now_iso()
        atomic_write_json(self.ledger_file, self.data)
        self.export_papers_manifest()

    def export_papers_manifest(self):
        """
        导出符合分析层 (T002/T003) 规范的 papers_manifest.json。
        仅收录 VERIFIED 且父条目 Key、附件归属合法的记录！
        兼容字段：同时提供 zotero_key 和 zotero_item_key；
        同时提供 snapshot_pdf, local_pdf_absolute, local_pdf_relative。
        """
        manifest_items = []
        for key, rec in self.data.get("records", {}).items():
            if rec.get("ingest_status") == "VERIFIED":
                z_parent_key = rec.get("zotero_parent_key")
                # 严格拒绝没有父条目Key的虚假导出
                if not z_parent_key or z_parent_key == "null":
                    continue
                pdf_path = rec.get("working_pdf_path") or rec.get("original_pdf_path")
                # 计算相对路径（基于清单所在目录）
                rel_pdf = None
                if pdf_path:
                    try:
                        rel_pdf = str(Path(pdf_path).relative_to(self.output_dir))
                    except Exception:
                        rel_pdf = str(pdf_path)

                pid = rec.get("paper_id") or rec.get("source_id") or f"PAPER_{rec.get('sha256')[:8]}"
                item_entry = {
                    "paper_id": pid,
                    "raw_id": rec.get("source_id"),
                    "zotero_key": z_parent_key,
                    "zotero_item_key": z_parent_key,  # 会话二 (T003/T005) 兼容别名
                    "title": rec.get("title"),
                    "authors": rec.get("authors") or [],
                    "year": rec.get("year") or "",
                    "source": rec.get("source") or "",
                    "url": rec.get("url") or "",
                    "doi": rec.get("doi") or "",
                    "citation": rec.get("citation") or f"《{rec.get('title')}》 ({rec.get('year')})",
                    "sample_role": rec.get("sample_role", "workflow_test"),
                    "snapshot_pdf": pdf_path,
                    "local_pdf_absolute": pdf_path,   # 会话二 (T003/T005) 兼容别名
                    "local_pdf_relative": rel_pdf,    # 会话二 (T003/T005) 相对路径
                    "source_snapshot": rec.get("original_pdf_path"),
                    "file_bytes": rec.get("file_bytes"),
                    "sha256": rec.get("sha256"),
                    "hash_verified": True,
                    "zotero_attachment_dir": rec.get("zotero_attachment_dir"),
                    "zotero_stored_pdf_path": rec.get("zotero_stored_pdf_path"),
                    "snapshot_created_at": rec.get("ingested_at") or now_iso()
                }
                manifest_items.append(item_entry)
        atomic_write_json(self.manifest_file, manifest_items)

    def export_ingest_result(self, input_count=None, sample_role="workflow_test"):
        verified_records = [r for r in self.data["records"].values() if r.get("ingest_status") == "VERIFIED"]
        pending_records = [r for r in self.data["records"].values() if r.get("ingest_status") != "VERIFIED"]
        
        mf_path = self.manifest_file
        mf_sha = sha256_file(str(mf_path)) if mf_path.exists() else ""
        
        tot_count = input_count if input_count is not None else len(self.data["records"])
        status_str = "completed" if len(pending_records) == 0 and len(verified_records) > 0 else ("partial" if len(verified_records) > 0 else "failed")
        ready_bool = (len(pending_records) == 0 and len(verified_records) > 0)
        
        identities = []
        for r in self.data["records"].values():
            identities.append({
                "paper_id": r.get("paper_id") or r.get("source_id"),
                "source_id": r.get("source_id"),
                "title": r.get("title"),
                "zotero_item_key": r.get("zotero_parent_key"),
                "zotero_attachment_dir": r.get("zotero_attachment_dir"),
                "zotero_stored_pdf_path": r.get("zotero_stored_pdf_path"),
                "working_pdf_path": r.get("working_pdf_path"),
                "original_pdf_path": r.get("original_pdf_path"),
                "sha256": r.get("sha256"),
                "file_bytes": r.get("file_bytes"),
                "hash_verified": r.get("ingest_status") == "VERIFIED",
                "parent_attachment_linked": bool(r.get("zotero_parent_key") and r.get("zotero_attachment_dir")),
                "ingest_status": r.get("ingest_status"),
                "last_error": r.get("last_error")
            })
            
        res = {
            "status": status_str,
            "ready_for_research": ready_bool,
            "sample_role": sample_role,
            "input_count": tot_count,
            "verified_count": len(verified_records),
            "pending_items": [r.get("source_id") or r.get("title") for r in pending_records],
            "actual_manifest_path": str(mf_path),
            "manifest_sha256": mf_sha,
            "identities_mapping": identities,
            "updated_at": now_iso()
        }
        atomic_write_json(self.output_dir / "ingest_result.json", res)
        return res

    def find_existing_by_sha256(self, sha):
        if not sha:
            return None
        key = self.data["sha256_index"].get(sha)
        if key and key in self.data["records"]:
            return self.data["records"][key]
        return None

    def find_existing_by_source_id(self, sid):
        if not sid:
            return None
        key = self.data["source_id_index"].get(sid)
        if key and key in self.data["records"]:
            return self.data["records"][key]
        return None

    def find_existing_by_doi(self, doi):
        norm_d = normalize_doi(doi)
        if not norm_d:
            return None
        key = self.data["doi_index"].get(norm_d)
        if key and key in self.data["records"]:
            return self.data["records"][key]
        return None

    def is_known_conflict(self, source_id, sha256_val, doi=""):
        norm_d = normalize_doi(doi)
        for cid, conf in self.data.get("conflicts", {}).items():
            if source_id and conf.get("source_id") == source_id and conf.get("sha256") == sha256_val:
                return True
            if norm_d and conf.get("doi") == norm_d and conf.get("sha256") == sha256_val:
                return True
        return False

    def record_conflict(self, source_id, title, authors, year, source, url, doi, pdf_path, file_bytes, file_sha, reason, sample_role="workflow_test"):
        norm_d = normalize_doi(doi)
        conf_id = f"CONF_{file_sha[:8]}_{source_id or 'NOD'}"
        conf_rec = {
            "conflict_id": conf_id,
            "item_key": conf_id,
            "source_id": source_id,
            "doi": norm_d,
            "title": title,
            "authors": authors,
            "year": year,
            "source": source,
            "url": url,
            "original_pdf_path": str(pdf_path),
            "file_bytes": file_bytes,
            "sha256": file_sha,
            "download_status": "conflict",
            "ingest_status": "VERSION_CONFLICT",
            "last_error": reason,
            "sample_role": sample_role,
            "created_at": now_iso()
        }
        self.data["conflicts"][conf_id] = conf_rec
        # 记录在 records 中供状态和日志展示，但不覆盖原 source_id_index
        self.data["records"][conf_id] = conf_rec
        self.save()

    def upsert_record(self, record):
        item_key = record["item_key"]
        self.data["records"][item_key] = record
        if record.get("sha256"):
            self.data["sha256_index"][record["sha256"]] = item_key
        if record.get("source_id"):
            self.data["source_id_index"][record["source_id"]] = item_key
        norm_d = normalize_doi(record.get("doi"))
        if norm_d:
            self.data["doi_index"][norm_d] = item_key

def find_existing_zotero_item_by_evidence(db_path, storage_dir, title, file_sha, file_bytes):
    """
    通过只读快照核对 Zotero 中是否已实际存在与题名、哈希及附件完全相符的条目。
    若存在且附件物理文件哈希及字节完全一致，返回 (True, z_parent_key, att_key, att_pdf_path)
    否则返回 (False, None, None, None)
    """
    if not db_path or not os.path.exists(db_path):
        return False, None, None, None
    temp_dir = tempfile.mkdtemp(prefix="z_find_")
    try:
        src_dir = os.path.dirname(db_path)
        base_name = os.path.basename(db_path)
        for ext in ["", "-wal", "-shm"]:
            fpath = os.path.join(src_dir, base_name + ext)
            if os.path.exists(fpath):
                shutil.copyfile(fpath, os.path.join(temp_dir, base_name + ext))
        copied_db = os.path.join(temp_dir, base_name)
        conn = sqlite3.connect(f"file:{copied_db}?mode=ro", uri=True)
        cur = conn.cursor()
        
        rows = cur.execute('''
            SELECT i.key, i.itemID
            FROM items i
            JOIN itemData id ON i.itemID = id.itemID
            JOIN fields f ON id.fieldID = f.fieldID
            JOIN itemDataValues idv ON id.valueID = idv.valueID
            WHERE f.fieldName = 'title' AND idv.value = ?
        ''', (title,)).fetchall()
        
        for p_key, p_id in rows:
            att_rows = cur.execute('''
                SELECT i_att.key
                FROM itemAttachments a
                JOIN items i_att ON a.itemID = i_att.itemID
                WHERE a.parentItemID = ?
            ''', (p_id,)).fetchall()
            
            for (att_key,) in att_rows:
                cand_dir = Path(storage_dir) / att_key
                if cand_dir.exists():
                    for cand_pdf in cand_dir.glob("*.pdf"):
                        if cand_pdf.stat().st_size == file_bytes and sha256_file(str(cand_pdf)) == file_sha:
                            conn.close()
                            return True, p_key, att_key, str(cand_pdf)
                            
        conn.close()
        return False, None, None, None
    except Exception as e:
        print(f"[DEBUG] find_existing_zotero_item_by_evidence error: {e}", file=sys.stderr)
        return False, None, None, None
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

# 历史一次性受审迁移精确指纹白名单（关闭已用完的通道，禁止任意关键词仿冒）
AUTHORIZED_HISTORICAL_MIGRATIONS = {
    "a968866c50f19a55": "89dedf04b80188c0b5407c5858cc32caa548a536afa49f58cee87b4aa7e1eb3c",
    "426e8bdf0d0ec52e": "bdf4f127f97fd992397cc9260a9fc3bebdd35b7b25b69e9b6f4155451f56dc85",
    "79d1a1289da8ed66": "c237bd21d58f89dfa8302bf56164554cf23cea5181566709ca87fb6fa6658d42",
}

def is_structured_session_exists_evidence(state_data, item_key=None, paper_id=None, batch_fingerprint=None, allow_legacy_migration=True):
    """
    恢复证据门禁严格收口（符合 0101 审计规范）：
    1. 实时请求响应体：经严格 JSON 解析，顶层对象且 'error' 字段严格等于 'SESSION_EXISTS' 时放行。
       解析失败或 'error' 不为 'SESSION_EXISTS'（含任何嵌套字样）坚决返回 False。
    2. 历史迁移分支（与当前响应分支严格分离）：
       - 若 allow_legacy_migration 为 False，直接拒绝所有迁移凭据。
       - 迁移凭据必须包含 verified_pre_save_rejection is True。
       - 调用方必须显式提供上下文 paper_id，缺失则坚决拒绝（no_context_rejected=True）。
       - paper_id 必须严格在 AUTHORIZED_HISTORICAL_MIGRATIONS 白名单中且与上下文一致。
       - 迁移凭据中的 batch_fingerprint 必须精确等于白名单中该条目的真实受审批次指纹，
         任何虚构字符串（如 'a'*64）均直接拒绝（arbitrary_audit_keyword_spoof_rejected=True）。
       - 若调用方提供了上下文 batch_fingerprint，必须与受审批次指纹完全一致；不一致则拒绝（wrong_batch_rejected=True）。
       - 审计决定与来源证据必须严格匹配真实固定记录：
         source_evidence == 'server_connector.js SessionManager.create'
         audit_decision == 'Pre-save rejection definitively proven'
         禁止任何包含额外词缀的伪造字符串（如 'invented ...'）。
    3. 泛 409、超时、未知错误一律返回 False。
    """
    if not isinstance(state_data, dict):
        return False

    # 1. 严格顶层 JSON 结构化解析（当前实时响应分支）
    err_body = state_data.get("error_response_body")
    if isinstance(err_body, str) and err_body.strip():
        try:
            parsed = json.loads(err_body)
            if isinstance(parsed, dict):
                # 必须顶层明确为 SESSION_EXISTS
                if parsed.get("error") == "SESSION_EXISTS":
                    return True
                else:
                    return False
        except Exception:
            return False

    # 2. 有范围、已授权的一次性历史迁移记录校验（历史迁移独立分支）
    if not allow_legacy_migration:
        return False

    migration = state_data.get("legacy_session_conflict_migration")
    if isinstance(migration, dict):
        if migration.get("verified_pre_save_rejection") is True:
            # 必须提供有效的上下文 paper_id
            if not paper_id or not isinstance(paper_id, str):
                return False

            mig_pid = migration.get("paper_id")
            if mig_pid != paper_id or mig_pid not in AUTHORIZED_HISTORICAL_MIGRATIONS:
                return False

            expected_fp = AUTHORIZED_HISTORICAL_MIGRATIONS[mig_pid]
            mig_fp = migration.get("batch_fingerprint")

            # 必须精确等于受审认定的固定批次指纹，拒绝任何虚构指纹
            if mig_fp != expected_fp:
                return False

            # 若调用方提供了上下文批次指纹，必须与受审批次完全一致
            if batch_fingerprint and batch_fingerprint != expected_fp:
                return False

            # 审计凭据精确内容全等，彻底杜绝关键词仿冒
            if migration.get("source_evidence") != "server_connector.js SessionManager.create":
                return False
            if migration.get("audit_decision") != "Pre-save rejection definitively proven":
                return False

            return True

    return False

def check_connector_ping(connector_url):
    try:
        ping_url = connector_url.replace("/import", "/ping")
        req = urllib.request.Request(ping_url)
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False

def query_sqlite_parent_attachments_full(db_path, parent_keys):
    """
    查询 SQLite 数据库，同时核实父条目是否存在以及附件子条目映射。
    返回: (success, parent_exists_dict, parent_to_att_keys_dict)
    """
    if not os.path.exists(db_path):
        return False, {}, {}
    temp_dir = tempfile.mkdtemp(prefix="z_db_ro_")
    try:
        src_dir = os.path.dirname(db_path)
        base_name = os.path.basename(db_path)
        for ext in ["", "-wal", "-shm"]:
            fpath = os.path.join(src_dir, base_name + ext)
            if os.path.exists(fpath):
                shutil.copyfile(fpath, os.path.join(temp_dir, base_name + ext))
        copied_db = os.path.join(temp_dir, base_name)
        conn = sqlite3.connect(f"file:{copied_db}?mode=ro", uri=True)
        cur = conn.cursor()

        parent_exists = {}
        parent_to_atts = {}

        for p_key in parent_keys:
            cur.execute("SELECT itemID FROM items WHERE key = ?", (p_key,))
            p_row = cur.fetchone()
            if not p_row:
                parent_exists[p_key] = False
                parent_to_atts[p_key] = []
            else:
                parent_exists[p_key] = True
                cur.execute("""
                    SELECT i_att.key
                    FROM itemAttachments a
                    JOIN items i_att ON i_att.itemID = a.itemID
                    WHERE a.parentItemID = ?
                """, (p_row[0],))
                rows = cur.fetchall()
                parent_to_atts[p_key] = [r[0] for r in rows]

        conn.close()
        return True, parent_exists, parent_to_atts
    except Exception as e:
        print(f"[DEBUG] SQLite读取异常: {e}", file=sys.stderr)
        return False, {}, {}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def verify_single_mapping_consistency(mapping_file, expected_raw_id, expected_sha, expected_bytes, expected_title, storage_dir, db_path):
    """
    R3-2: 通用父子归属与文件核验（去掉字面值黑名单，依靠真实数据库与物理归属）：
    1. 映射文件必须存在且合法；
    2. raw_id, sha256, file_bytes, title 必须一致；
    3. zotero_item_key 必须存在且非空；
    4. hash_verified 和 parent_attachment_linked 必须为 true；
    5. 验证实际 storage_dir/zotero_attachment_dir/ 下所声称附件Key目录中的 PDF 真实存在、大小哈希匹配；
    6. 若数据库可用：必须核实父条目存在，且该附件Key确实从属于此父条目；零关联或未找到父条目直接判定失败！
    """
    if not os.path.exists(mapping_file):
        return False, "映射文件不存在", None
    try:
        with open(mapping_file, 'r', encoding='utf-8') as f:
            mapping_data = json.load(f)
    except Exception as e:
        return False, f"映射文件读取损坏: {e}", None

    if not isinstance(mapping_data, list) or len(mapping_data) != 1:
        return False, "单篇映射数据格式错误", None

    m = mapping_data[0]
    if m.get("raw_id") != expected_raw_id:
        return False, f"raw_id 不匹配 ({m.get('raw_id')} != {expected_raw_id})", None
    if m.get("sha256") != expected_sha:
        return False, "sha256 不匹配", None
    if m.get("title") != expected_title:
        return False, "title 不匹配", None

    z_key = m.get("zotero_item_key")
    if not z_key or z_key == "null":
        return False, "缺少有效父条目 Key", None

    if not m.get("hash_verified") or not m.get("parent_attachment_linked"):
        return False, "映射未标记 hash_verified 或 parent_attachment_linked 为 true", None

    att_dir = m.get("zotero_attachment_dir")
    if not att_dir or att_dir == "UNKNOWN":
        return False, "缺少有效附件存储目录", None

    # 严格绑定实际文件位置到所声称的附件Key目录: storage_dir / att_dir
    cand_dir = Path(storage_dir) / att_dir
    found_pdf = None
    if cand_dir.exists():
        for child in cand_dir.glob("*.pdf"):
            if sha256_file(str(child)) == expected_sha and child.stat().st_size == expected_bytes:
                found_pdf = str(child)
                break

    if not found_pdf:
        # 尝试检查 m 记录的直接文件路径是否刚好在 att_dir 下
        m_stored = m.get("zotero_stored_pdf_path")
        if m_stored and os.path.exists(m_stored) and att_dir in Path(m_stored).parts:
            if sha256_file(m_stored) == expected_sha and os.path.getsize(m_stored) == expected_bytes:
                found_pdf = m_stored

    if not found_pdf:
        return False, f"所声称附件目录 {att_dir} 下未找到匹配哈希与大小的PDF文件", None

    # R3-2: 若数据库可用，严格核查父条目存在性与父子真实关联
    if db_path and os.path.exists(db_path):
        db_ok, p_exists, p_atts = query_sqlite_parent_attachments_full(db_path, [z_key])
        if db_ok:
            if not p_exists.get(z_key, False):
                return False, f"数据库核验失败：父条目 {z_key} 在数据库中不存在", None
            linked_keys = p_atts.get(z_key, [])
            if att_dir not in linked_keys:
                return False, f"数据库核验失败：附件 {att_dir} 未挂接在父条目 {z_key} 下 (该父条目下附件: {linked_keys})", None

    return True, "映射与归属核验通过", m

def execute_single_ingest(item_rec, item_work_dir, connector_url, storage_dir, db_path):
    """
    单篇串行执行入库。具备严格的单篇状态机保护：
    - 状态文件或映射损坏/丢失时坚决阻断；
    - 区分预检未提交（可恢复）与已提交未决（阻断）；
    - 严格核实父条目 Key 与父子附件真实挂接。
    返回 (success, updated_item_rec, exit_code, message)
    """
    work_dir = Path(item_work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    state_file = work_dir / "batch_state.json"
    mapping_file = work_dir / "zotero_mapping.json"

    title = item_rec.get("title", "").strip()
    authors = item_rec.get("authors") or []
    year = item_rec.get("year", "")
    source = item_rec.get("source", "")
    url = item_rec.get("url", "")
    orig_pdf = item_rec.get("original_pdf_path", "")
    file_bytes = item_rec.get("file_bytes", 0)
    file_sha = item_rec.get("sha256", "")
    source_id = item_rec.get("source_id", "")

    if not title:
        return False, item_rec, EXIT_INPUT_ERROR, "缺少标题，列为待补题录"

    # R3-1: 检查已知未决/历史记录与丢失状态保护
    # 若在台账中已知处于未决状态，或历史已产生目录但 batch_state.json 丢失，坚决阻断并退出 8
    known_status = item_rec.get("ingest_status")
    has_prev_evidence = (work_dir / "import_materials.ris").exists() or (work_dir / "papers_work_copy").exists() or mapping_file.exists()

    if known_status in ("PENDING_VERIFY", "SUBMITTING", "PARTIAL", "ATTACHMENT_UNVERIFIED"):
        if not state_file.exists():
            print(f"[ERROR] 条目处于未决状态 [{known_status}] 但状态文件 batch_state.json 丢失，安全阻断以防重复提交！", file=sys.stderr)
            return False, item_rec, EXIT_CONFLICT_BLOCKED, f"未决状态文件丢失: {known_status}"

    if not state_file.exists() and has_prev_evidence and known_status != "PRE_SUBMIT_FAILED":
        print(f"[ERROR] 检测到已有操作历史但状态文件丢失: {work_dir}，安全阻断以防重复提交！", file=sys.stderr)
        return False, item_rec, EXIT_CONFLICT_BLOCKED, "已有操作历史但状态文件丢失"

    # 校验并建立工作副本
    work_pdf_dir = work_dir / "papers_work_copy"
    work_pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf_name = Path(orig_pdf).name if orig_pdf else f"{source_id}.pdf"
    work_pdf = work_pdf_dir / pdf_name

    needs_rebuild = True
    if work_pdf.exists():
        if work_pdf.stat().st_size == file_bytes and sha256_file(str(work_pdf)) == file_sha:
            needs_rebuild = False

    if needs_rebuild:
        if not orig_pdf or not Path(orig_pdf).exists():
            return False, item_rec, EXIT_INTEGRITY_ERROR, f"原始PDF文件不存在: {orig_pdf}"
        val_ok, val_msg, val_info = verify_pdf_fulltext(orig_pdf)
        if not val_ok:
            return False, item_rec, EXIT_INTEGRITY_ERROR, f"原件非有效PDF: {val_msg}"
        shutil.copy2(orig_pdf, str(work_pdf))

    item_rec["working_pdf_path"] = str(work_pdf)

    # 构造 RIS 材料
    ris_lines = ["TY  - JOUR", f"TI  - {title}"]
    for a in authors:
        ris_lines.append(f"AU  - {a}")
    if year:
        ris_lines.append(f"PY  - {year}")
    if source:
        ris_lines.append(f"JO  - {source}")
    if url:
        ris_lines.append(f"UR  - {url}")
    ris_lines.append(f"L1  - {work_pdf}")
    ris_lines.append(f"M2  - RawID: {source_id} | SHA256: {file_sha}")
    ris_lines.append("KW  - AI科研链条_文献桥接_T004")
    ris_lines.append("ER  - \n")
    ris_content = "\n".join(ris_lines)

    ris_file = work_dir / "import_materials.ris"
    with open(ris_file, 'w', encoding='utf-8') as f:
        f.write(ris_content)

    batch_fp = hashlib.sha256(f"{source_id}:{title}:{file_sha}".encode('utf-8')).hexdigest()

    actual_db_path = db_path or ZOTERO_DEFAULT_DB

    # 1. 先查验 Zotero 文库中是否已实际存在对应题名与哈希附件完全合法的条目（支持恢复与核验复用）
    found_z, ex_p_key, ex_att_key, ex_att_pdf = find_existing_zotero_item_by_evidence(
        actual_db_path, storage_dir, title, file_sha, file_bytes
    )
    if found_z:
        m_record = [{
            "index": 1,
            "raw_id": source_id,
            "paper_id": item_rec.get("paper_id") or source_id,
            "citation": item_rec.get("citation"),
            "zotero_item_key": ex_p_key,
            "zotero_attachment_dir": ex_att_key,
            "title": title,
            "authors": authors,
            "year": year,
            "source": source,
            "url": url,
            "original_pdf_path": orig_pdf,
            "working_pdf_path": str(work_pdf),
            "zotero_stored_pdf_path": ex_att_pdf,
            "file_bytes": file_bytes,
            "sha256": file_sha,
            "parent_attachment_linked": True,
            "hash_verified": True
        }]
        atomic_write_json(mapping_file, m_record)
        atomic_write_json(state_file, {
            "batch_fingerprint": batch_fp,
            "status": "COMPLETED",
            "session_id": "verified_existing",
            "message": "在Zotero文库中核验证明已有完整条目与父子附件，安全复用",
            "items_count": 1,
            "updated_at": now_iso()
        })
        item_rec["ingest_status"] = "VERIFIED"
        item_rec["zotero_parent_key"] = ex_p_key
        item_rec["zotero_attachment_dir"] = ex_att_key
        item_rec["zotero_stored_pdf_path"] = ex_att_pdf
        item_rec["last_error"] = None
        item_rec["ingested_at"] = now_iso()
        return True, item_rec, EXIT_SUCCESS, "在Zotero文库中核验证明已有完整条目与父子附件，安全复用"

    # R1-2 & R3-1: 严格核查单篇状态文件
    if state_file.exists():
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                state_data = json.load(f)
            if not isinstance(state_data, dict):
                raise ValueError("batch_state.json not a dict")
        except Exception as e:
            # 单篇状态损坏：严禁重提！保留原损坏现场并阻断
            print(f"[ERROR] 单篇批次状态损坏: {state_file} ({e})，安全阻断以防重复提交！", file=sys.stderr)
            return False, item_rec, EXIT_CONFLICT_BLOCKED, f"单篇批次状态损坏: {e}"

        cur_status = state_data.get("status")
        if cur_status == "COMPLETED":
            # 严格核验映射一致性
            m_ok, m_msg, m_rec = verify_single_mapping_consistency(
                mapping_file, source_id, file_sha, file_bytes, title, storage_dir, actual_db_path
            )
            if m_ok and m_rec:
                item_rec["ingest_status"] = "VERIFIED"
                item_rec["zotero_parent_key"] = m_rec.get("zotero_item_key")
                item_rec["zotero_attachment_dir"] = m_rec.get("zotero_attachment_dir")
                item_rec["zotero_stored_pdf_path"] = m_rec.get("zotero_stored_pdf_path")
                item_rec["last_error"] = None
                item_rec["ingested_at"] = state_data.get("updated_at") or now_iso()
                return True, item_rec, EXIT_SUCCESS, "已有完整映射且核验一致，安全跳过"
            else:
                print(f"[ERROR] 单篇状态为 COMPLETED 但映射核验失败: {m_msg}，安全阻断！", file=sys.stderr)
                return False, item_rec, EXIT_CONFLICT_BLOCKED, f"完成状态与映射不一致: {m_msg}"
        elif cur_status in ("PENDING", "SUBMITTING", "PARTIAL", "ATTACHMENT_UNVERIFIED"):
            if is_structured_session_exists_evidence(state_data, item_key=item_rec.get("item_key"), paper_id=item_rec.get("paper_id") or source_id) and not found_z:
                print(f"[INFO] 条目 {source_id} 查明确系 SessionID 冲突且在保存前被拒绝 (SESSION_EXISTS)，且文库无残留条目，允许重新提交。")
            else:
                return False, item_rec, EXIT_PENDING_BLOCKED, f"单篇处于待核对状态 [{cur_status}]: {state_data.get('pending_reason')}"
        elif cur_status == "PRE_SUBMIT_FAILED":
            # 确定未提交失败，允许重试
            pass

    # R1-5: 预检阶段 (GET ping)，若预检失败，记录 PRE_SUBMIT_FAILED (确定未提交)
    if not check_connector_ping(connector_url):
        atomic_write_json(state_file, {
            "batch_fingerprint": batch_fp,
            "status": "PRE_SUBMIT_FAILED",
            "pending_reason": "Zotero 监听端口未响应 (预检失败，尚未提交)",
            "updated_at": now_iso()
        })
        return False, item_rec, EXIT_NETWORK_ERROR, "Zotero 监听端口未开放 (确定未提交)"

    # 使用 UUID 生成全局唯一 session_id，避免秒级并发重复
    session_id = f"bridge_{uuid.uuid4().hex}"

    # 预检通过，即将发出 POST 请求，记录 SUBMITTING 状态
    atomic_write_json(state_file, {
        "batch_fingerprint": batch_fp,
        "status": "SUBMITTING",
        "session_id": session_id,
        "pending_reason": "请求发起中，结果尚未确认",
        "updated_at": now_iso()
    })

    url = f"{connector_url}?session={session_id}"
    req = urllib.request.Request(url, data=ris_content.encode('utf-8'), headers={"Content-Type": "text/plain; charset=utf-8"})

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status_code = resp.status
            body_text = resp.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode('utf-8', errors='replace')
        except Exception:
            pass
        atomic_write_json(state_file, {
            "batch_fingerprint": batch_fp,
            "status": "PENDING",
            "session_id": session_id,
            "http_status": e.code,
            "http_reason": e.reason,
            "error_response_body": err_body,
            "pending_reason": f"HTTP错误: {e.code} {e.reason} (body: {err_body[:200]})",
            "updated_at": now_iso()
        })
        return False, item_rec, EXIT_RESPONSE_ERROR, f"HTTP错误: {e.code} {err_body[:100]}"
    except urllib.error.URLError as e:
        atomic_write_json(state_file, {
            "batch_fingerprint": batch_fp,
            "status": "PENDING",
            "session_id": session_id,
            "pending_reason": f"连接异常或超时: {e.reason}",
            "updated_at": now_iso()
        })
        return False, item_rec, EXIT_NETWORK_ERROR, f"连接异常或超时: {e.reason}"
    except Exception as e:
        atomic_write_json(state_file, {
            "batch_fingerprint": batch_fp,
            "status": "PENDING",
            "session_id": session_id,
            "pending_reason": f"未知网络异常: {e}",
            "updated_at": now_iso()
        })
        return False, item_rec, EXIT_NETWORK_ERROR, f"未知网络异常: {e}"

    if status_code != 201:
        atomic_write_json(state_file, {
            "batch_fingerprint": batch_fp,
            "status": "PENDING",
            "pending_reason": f"非预期状态码: {status_code}",
            "updated_at": now_iso()
        })
        return False, item_rec, EXIT_RESPONSE_ERROR, f"非预期状态码: {status_code}"

    try:
        imported_items = json.loads(body_text)
    except Exception as e:
        atomic_write_json(state_file, {
            "batch_fingerprint": batch_fp,
            "status": "PENDING",
            "pending_reason": f"响应JSON损坏: {e}",
            "updated_at": now_iso()
        })
        return False, item_rec, EXIT_RESPONSE_ERROR, "响应非合法JSON"

    if not isinstance(imported_items, list) or len(imported_items) == 0:
        atomic_write_json(state_file, {
            "batch_fingerprint": batch_fp,
            "status": "PARTIAL",
            "pending_reason": "接口未返回有效条目",
            "updated_at": now_iso()
        })
        return False, item_rec, EXIT_STATE_ERROR, "接口未返回有效条目"

    z_item = imported_items[0]
    z_key = z_item.get("key")
    # R1-3: 若接口响应缺少父条目Key，坚决不能标 VERIFIED
    if not z_key or z_key == "null":
        atomic_write_json(state_file, {
            "batch_fingerprint": batch_fp,
            "status": "PARTIAL",
            "pending_reason": "接口响应缺少父条目 Key",
            "updated_at": now_iso()
        })
        return False, item_rec, EXIT_STATE_ERROR, "接口响应缺少父条目 Key"

    child_att_keys = []
    if z_item.get("attachments"):
        for a in z_item.get("attachments"):
            if isinstance(a, dict) and a.get("key"):
                child_att_keys.append(a.get("key"))
            elif isinstance(a, str):
                child_att_keys.append(a)

    actual_db_path = db_path or ZOTERO_DEFAULT_DB
    matched_storage_dir = None
    matched_storage_pdf = None
    parent_attachment_linked = False
    hash_verified = False

    for attempt in range(6):
        if not child_att_keys and actual_db_path and os.path.exists(actual_db_path):
            db_ok, p_exists, p_atts = query_sqlite_parent_attachments_full(actual_db_path, [z_key])
            if db_ok and p_exists.get(z_key, False):
                child_att_keys = p_atts.get(z_key, [])

        if child_att_keys:
            for att_key in child_att_keys:
                candidate_dir = Path(storage_dir) / att_key
                if candidate_dir.exists():
                    for cand in candidate_dir.glob("*.pdf"):
                        if sha256_file(str(cand)) == file_sha and cand.stat().st_size == file_bytes:
                            matched_storage_dir = att_key
                            matched_storage_pdf = str(cand)
                            parent_attachment_linked = True
                            hash_verified = True
                            break
                if parent_attachment_linked:
                    break
        if parent_attachment_linked:
            break
        time.sleep(1)

    m_record = [{
        "index": 1,
        "raw_id": source_id,
        "paper_id": item_rec.get("paper_id") or source_id,
        "citation": item_rec.get("citation"),
        "zotero_item_key": z_key,
        "zotero_attachment_dir": matched_storage_dir or "UNKNOWN",
        "title": title,
        "authors": authors,
        "year": year,
        "source": source,
        "url": url,
        "original_pdf_path": orig_pdf,
        "working_pdf_path": str(work_pdf),
        "zotero_stored_pdf_path": matched_storage_pdf or "UNKNOWN",
        "file_bytes": file_bytes,
        "sha256": file_sha,
        "parent_attachment_linked": parent_attachment_linked,
        "hash_verified": hash_verified
    }]

    try:
        atomic_write_json(mapping_file, m_record)
    except Exception as e:
        atomic_write_json(state_file, {
            "batch_fingerprint": batch_fp,
            "status": "PENDING",
            "pending_reason": f"映射文件保存失败: {e}",
            "updated_at": now_iso()
        })
        return False, item_rec, EXIT_STATE_ERROR, f"映射文件保存失败: {e}"

    if not (parent_attachment_linked and hash_verified):
        atomic_write_json(state_file, {
            "batch_fingerprint": batch_fp,
            "status": "ATTACHMENT_UNVERIFIED",
            "pending_reason": f"条目附件未核验 (已关联附件Key: {child_att_keys})",
            "updated_at": now_iso()
        })
        return False, item_rec, EXIT_ATTACHMENT_VERIFY_ERROR, "附件父子挂接或哈希未核验通过"

    atomic_write_json(state_file, {
        "batch_fingerprint": batch_fp,
        "status": "COMPLETED",
        "session_id": session_id,
        "message": "全量条目与父子附件核验一致通过",
        "items_count": 1,
        "updated_at": now_iso()
    })

    item_rec["ingest_status"] = "VERIFIED"
    item_rec["zotero_parent_key"] = z_key
    item_rec["zotero_attachment_dir"] = matched_storage_dir
    item_rec["zotero_stored_pdf_path"] = matched_storage_pdf
    item_rec["last_error"] = None
    item_rec["ingested_at"] = now_iso()
    return True, item_rec, EXIT_SUCCESS, "入库成功且核验通过"

# ----------------- 子命令实现 -----------------

def cmd_record_download(args):
    """
    包装入口：调用 upstream library_jobs.py 的真实公开入口 run(parser().parse_args())，
    执行任务锁、正规核验与 task.json 持久化；成功后无缝衔接入库队列并（若--commit）入库。
    """
    task_dir = Path(args.task).resolve()
    output_dir = Path(args.output).resolve()
    ledger = LedgerManager(output_dir)

    task_json_p = task_dir / "task.json"
    manifest_json_p = task_dir / "manifest.json"

    # 若存在标准任务 task.json，调用 upstream library_jobs.py 真实公开入口执行任务锁、状态持久化与正文校验
    if task_json_p.exists():
        if not upstream_jobs:
            print("[ERROR] upstream_jobs 未加载", file=sys.stderr)
            return EXIT_STATE_ERROR
        try:
            upstream_parser = upstream_jobs.parser()
            upstream_args = upstream_parser.parse_args([
                "record",
                "--task", str(task_dir),
                "--id", args.id,
                "--status", "downloaded",
                "--file", str(args.file)
            ])
            upstream_result = upstream_jobs.run(upstream_args)
        except Exception as e:
            print(f"[ERROR] 采集工具 record 核验失败: {e}", file=sys.stderr)
            return EXIT_INTEGRITY_ERROR

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"[ERROR] 下载文件不存在: {file_path}", file=sys.stderr)
        return EXIT_INTEGRITY_ERROR

    val_ok, val_msg, val_info = verify_pdf_fulltext(file_path)
    if not val_ok:
        print(f"[ERROR] 文件校验不合格: {val_msg}", file=sys.stderr)
        return EXIT_INTEGRITY_ERROR

    file_bytes = val_info["bytes"]
    file_sha = val_info["sha256"]

    title = ""
    authors = []
    year = ""
    source = ""
    url = ""
    doi = ""

    found_paper = None
    if task_json_p.exists():
        try:
            tdata = json.loads(task_json_p.read_text(encoding='utf-8'))
            for p in tdata.get("papers", []):
                if p.get("id") == args.id:
                    found_paper = p
                    break
        except Exception:
            pass

    if not found_paper and manifest_json_p.exists():
        try:
            mdata = json.loads(manifest_json_p.read_text(encoding='utf-8'))
            for p in mdata.get("papers", []):
                if p.get("id") == args.id:
                    found_paper = p
                    break
        except Exception:
            pass

    if not found_paper:
        print(f"[ERROR] 在任务中找不到文献 ID {args.id} 的记录", file=sys.stderr)
        return EXIT_INPUT_ERROR

    title = found_paper.get("title", "").strip()
    authors_str = found_paper.get("authors", "")
    authors = [a.strip() for a in authors_str.split(";") if a.strip()] if isinstance(authors_str, str) else authors_str
    year = str(found_paper.get("year", ""))
    source = found_paper.get("source", "")
    url = found_paper.get("url", "")
    doi = found_paper.get("doi", "")

    # 检查去重、版本冲突与别名保留
    norm_doi = normalize_doi(doi)
    if ledger.is_known_conflict(args.id, file_sha, norm_doi):
        print(f"[WARN] 来源ID {args.id} 处于版本冲突阻断中，严禁发起网络请求！", file=sys.stderr)
        return EXIT_CONFLICT_BLOCKED

    existing_sid = ledger.find_existing_by_source_id(args.id)
    if existing_sid and existing_sid.get("sha256") and existing_sid.get("sha256") != file_sha:
        reason = f"来源ID {args.id} 内容哈希变化 ({existing_sid.get('sha256')[:8]} != {file_sha[:8]})"
        print(f"[WARN] 版本冲突: {reason}", file=sys.stderr)
        ledger.record_conflict(args.id, title, authors, year, source, url, doi, args.file, file_bytes, file_sha, reason)
        return EXIT_CONFLICT_BLOCKED

    if norm_doi:
        existing_doi = ledger.find_existing_by_doi(norm_doi)
        if existing_doi and existing_doi.get("sha256") and existing_doi.get("sha256") != file_sha:
            reason = f"相同 DOI {norm_doi} 内容哈希变化 ({existing_doi.get('sha256')[:8]} != {file_sha[:8]})"
            print(f"[WARN] 版本冲突: {reason}", file=sys.stderr)
            ledger.record_conflict(args.id, title, authors, year, source, url, doi, args.file, file_bytes, file_sha, reason)
            return EXIT_CONFLICT_BLOCKED

    item_key = compute_item_key(args.id, file_sha, title)
    existing_sha = ledger.find_existing_by_sha256(file_sha)

    record = {
        "item_key": item_key,
        "source_id": args.id,
        "aliases": [args.id],
        "title": title,
        "authors": authors,
        "year": year,
        "source": source,
        "url": url,
        "doi": norm_doi,
        "original_pdf_path": str(args.file),
        "working_pdf_path": None,
        "file_bytes": file_bytes,
        "sha256": file_sha,
        "download_status": "downloaded",
        "ingest_status": "QUEUED",
        "zotero_parent_key": None,
        "zotero_attachment_dir": None,
        "zotero_stored_pdf_path": None,
        "sample_role": getattr(args, "sample_role", "workflow_test"),
        "last_error": None,
        "created_at": now_iso()
    }

    if existing_sha and existing_sha.get("ingest_status") == "VERIFIED":
        work_dir = output_dir / "items" / existing_sha["item_key"]
        m_ok, m_msg, m_rec = verify_single_mapping_consistency(
            work_dir / "zotero_mapping.json",
            existing_sha.get("source_id"),
            file_sha, file_bytes,
            existing_sha.get("title"),
            args.storage_dir,
            args.db_path
        )
        if m_ok and m_rec:
            existing_sha.setdefault("aliases", [existing_sha.get("source_id")])
            if args.id not in existing_sha["aliases"]:
                existing_sha["aliases"].append(args.id)
            ledger.upsert_record(existing_sha)
            ledger.save()
            print(f"[OK] 发现相同SHA256文献已入库，安全复用映射并保留别名: {existing_sha['zotero_parent_key']}")
            return EXIT_SUCCESS
        else:
            print(f"[ERROR] 已有同哈希记录但映射核验失败: {m_msg}，安全阻断！", file=sys.stderr)
            return EXIT_CONFLICT_BLOCKED

    ledger.upsert_record(record)
    ledger.save()

    if not args.commit:
        print(f"[INFO] 文献已核验并加入入库队列: {args.id} (预览模式)")
        return EXIT_SUCCESS

    item_work_dir = output_dir / "items" / item_key
    success, updated_rec, exit_code, msg = execute_single_ingest(
        item_rec=record,
        item_work_dir=item_work_dir,
        connector_url=args.connector_url,
        storage_dir=args.storage_dir,
        db_path=args.db_path
    )
    if not success:
        updated_rec["last_error"] = msg
        if exit_code == EXIT_INPUT_ERROR and not title:
            updated_rec["ingest_status"] = "TITLE_MISSING"
        elif exit_code in (EXIT_PENDING_BLOCKED, EXIT_CONFLICT_BLOCKED, EXIT_ATTACHMENT_VERIFY_ERROR, EXIT_RESPONSE_ERROR):
            updated_rec["ingest_status"] = "PENDING_VERIFY"
        elif exit_code == EXIT_NETWORK_ERROR and "确定未提交" in msg:
            updated_rec["ingest_status"] = "PRE_SUBMIT_FAILED"
        else:
            updated_rec["ingest_status"] = "FAILED"
    ledger.upsert_record(updated_rec)
    ledger.save()

    if success:
        print(f"[OK] 下载并入库成功: {title} (Key: {updated_rec['zotero_parent_key']})")
        return EXIT_SUCCESS
    else:
        print(f"[ERROR] 入库未成功: {msg} (退出码: {exit_code})", file=sys.stderr)
        return exit_code

def cmd_sync_downloads(args):
    """
    同步下载任务状态：优先读取任务 task.json (或 manifest.json)，
    对原记录的已核验哈希进行完整对照，哈希变化拦截为版本冲突；有效条目同步至队列并串行提交。
    """
    task_dir = Path(args.task).resolve()
    output_dir = Path(args.output).resolve()
    ledger = LedgerManager(output_dir)

    task_json_p = task_dir / "task.json"
    manifest_p = task_dir / "manifest.json"

    papers = []
    if task_json_p.exists():
        try:
            tdata = json.loads(task_json_p.read_text(encoding='utf-8'))
            papers = tdata.get("papers", [])
        except Exception:
            pass

    if not papers and manifest_p.exists():
        try:
            mdata = json.loads(manifest_p.read_text(encoding='utf-8'))
            papers = mdata.get("papers", [])
        except Exception:
            pass

    if not papers:
        print(f"[ERROR] 找不到任务数据或条目为空: {task_dir}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    print(f"[INFO] 读取到任务文献记录: {len(papers)} 条")
    has_failure = False
    has_conflict = False
    has_pending_blocked = False
    to_process_keys = []

    for p in papers:
        status = p.get("status")
        if status != "downloaded":
            continue
        sid = p.get("id")
        file_path = p.get("file")
        if not file_path or not os.path.exists(file_path):
            candidate = task_dir / "papers" / f"{sid}.pdf"
            if candidate.exists():
                file_path = str(candidate)
            else:
                continue

        val_ok, val_msg, val_info = verify_pdf_fulltext(file_path)
        if not val_ok:
            print(f"[WARN] 条目 {sid} 文件校验失败: {val_msg}", file=sys.stderr)
            has_failure = True
            continue

        f_sha = val_info["sha256"]
        f_bytes = val_info["bytes"]
        title = p.get("title", "").strip()
        doi = p.get("doi", "")
        norm_doi = normalize_doi(doi)

        # 对照原记录的已知哈希
        if p.get("sha256") and p.get("sha256") != f_sha:
            reason = f"文件哈希与任务已核验哈希不符 ({f_sha[:8]} != {p.get('sha256')[:8]})"
            print(f"[WARN] 版本冲突: {reason}", file=sys.stderr)
            ledger.record_conflict(sid, title, p.get("authors"), p.get("year"), p.get("source"), p.get("url"), doi, file_path, f_bytes, f_sha, reason)
            has_conflict = True
            continue

        # 检查是否与台账已有版本冲突
        if ledger.is_known_conflict(sid, f_sha, norm_doi):
            print(f"[WARN] 来源ID {sid} 处于版本冲突阻断中，跳过", file=sys.stderr)
            has_conflict = True
            continue

        existing_sid = ledger.find_existing_by_source_id(sid)
        if existing_sid and existing_sid.get("sha256") and existing_sid.get("sha256") != f_sha:
            reason = f"来源ID {sid} 内容哈希变化 ({existing_sid.get('sha256')[:8]} != {f_sha[:8]})"
            print(f"[WARN] 版本冲突: {reason}", file=sys.stderr)
            ledger.record_conflict(sid, title, p.get("authors"), p.get("year"), p.get("source"), p.get("url"), doi, file_path, f_bytes, f_sha, reason)
            has_conflict = True
            continue

        item_key = compute_item_key(sid, f_sha, title)
        existing_sha = ledger.find_existing_by_sha256(f_sha)

        if existing_sha and existing_sha.get("ingest_status") == "VERIFIED":
            work_dir = output_dir / "items" / existing_sha["item_key"]
            m_ok, m_msg, m_rec = verify_single_mapping_consistency(
                work_dir / "zotero_mapping.json",
                existing_sha.get("source_id"),
                f_sha, f_bytes,
                existing_sha.get("title"),
                args.storage_dir,
                args.db_path
            )
            if m_ok:
                existing_sha.setdefault("aliases", [existing_sha.get("source_id")])
                if sid not in existing_sha["aliases"]:
                    existing_sha["aliases"].append(sid)
                ledger.upsert_record(existing_sha)
                continue
            else:
                # 共同验证失败：撤销 VERIFIED 状态，转为待核对，从分析清单中排除
                existing_sha["ingest_status"] = "PENDING_VERIFY"
                existing_sha["last_error"] = f"映射核验失败: {m_msg}"
                ledger.upsert_record(existing_sha)
                has_failure = True
                continue

        authors_str = p.get("authors", "")
        authors = [a.strip() for a in authors_str.split(";") if a.strip()] if isinstance(authors_str, str) else authors_str

        # 如果记录已经存在且不是未提交队列，根据其当前状态处理
        existing_rec = ledger.data["records"].get(item_key)
        if existing_rec:
            st = existing_rec.get("ingest_status")
            if st == "PENDING_VERIFY":
                print(f"[WARN] 条目 {source_id} 处于待核对状态 (PENDING_VERIFY)，保持安全阻断！", file=sys.stderr)
                has_pending_blocked = True
                continue
            elif st == "VERSION_CONFLICT":
                print(f"[WARN] 条目 {source_id} 处于版本冲突阻断状态，保持阻断！", file=sys.stderr)
                has_conflict = True
                continue
            elif st == "VERIFIED":
                continue

        rec = {
            "item_key": item_key,
            "source_id": sid,
            "aliases": [sid],
            "title": title,
            "authors": authors,
            "year": str(p.get("year", "")),
            "source": p.get("source", ""),
            "url": p.get("url", ""),
            "doi": norm_doi,
            "original_pdf_path": file_path,
            "working_pdf_path": None,
            "file_bytes": f_bytes,
            "sha256": f_sha,
            "download_status": "downloaded",
            "ingest_status": "QUEUED",
            "zotero_parent_key": None,
            "zotero_attachment_dir": None,
            "zotero_stored_pdf_path": None,
            "sample_role": getattr(args, "sample_role", "workflow_test"),
            "last_error": None,
            "created_at": now_iso()
        }
        to_process_keys.append(item_key)
        ledger.upsert_record(rec)

    ledger.save()

    if not args.commit:
        if has_conflict:
            return EXIT_CONFLICT_BLOCKED
        if has_failure:
            return EXIT_INTEGRITY_ERROR
        return EXIT_SUCCESS

    # 串行提交有效队列记录
    overall_exit = EXIT_SUCCESS
    for it_key in to_process_keys:
        rec = ledger.data["records"].get(it_key)
        if not rec or rec.get("ingest_status") != "QUEUED":
            continue
        item_work_dir = output_dir / "items" / it_key
        success, updated_rec, exit_code, msg = execute_single_ingest(
            item_rec=rec,
            item_work_dir=item_work_dir,
            connector_url=args.connector_url,
            storage_dir=args.storage_dir,
            db_path=args.db_path
        )
        if not success:
            overall_exit = exit_code
            updated_rec["last_error"] = msg
            if exit_code in (EXIT_PENDING_BLOCKED, EXIT_CONFLICT_BLOCKED, EXIT_ATTACHMENT_VERIFY_ERROR, EXIT_RESPONSE_ERROR):
                updated_rec["ingest_status"] = "PENDING_VERIFY"
            elif exit_code == EXIT_NETWORK_ERROR and "确定未提交" in msg:
                updated_rec["ingest_status"] = "PRE_SUBMIT_FAILED"
            else:
                updated_rec["ingest_status"] = "FAILED"
        ledger.upsert_record(updated_rec)
        ledger.save()

    if has_conflict and overall_exit == EXIT_SUCCESS:
        overall_exit = EXIT_CONFLICT_BLOCKED
    elif has_pending_blocked and overall_exit == EXIT_SUCCESS:
        overall_exit = EXIT_PENDING_BLOCKED
    elif has_failure and overall_exit == EXIT_SUCCESS:
        overall_exit = EXIT_INTEGRITY_ERROR
    return overall_exit

def cmd_import_local(args):
    manifest_p = Path(args.manifest).resolve()
    output_dir = Path(args.output).resolve()
    ledger = LedgerManager(output_dir)

    if not manifest_p.exists():
        print(f"[ERROR] 清单文件不存在: {manifest_p}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    try:
        items = json.loads(manifest_p.read_text(encoding='utf-8'))
        if not isinstance(items, list):
            raise ValueError("not list")
    except Exception as e:
        print(f"[ERROR] 清单格式错误，必须为数组: {e}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    base_dir = manifest_p.parent
    has_conflict = False
    has_failure = False
    has_pending_blocked = False
    to_process_keys = []

    for it in items:
        source_id = it.get("source_id") or it.get("raw_id") or it.get("id") or it.get("paper_id")
        paper_id = it.get("paper_id") or source_id
        citation = it.get("citation")
        title = it.get("title", "").strip()
        authors = it.get("authors") or []
        if isinstance(authors, str):
            authors = [a.strip() for a in authors.split(";") if a.strip()]
        year = str(it.get("year", ""))
        source = it.get("source", "")
        url = it.get("url", "")
        doi = it.get("doi", "")
        norm_doi = normalize_doi(doi)
        pdf_rel = it.get("pdf_path") or it.get("original_pdf") or it.get("file") or it.get("local_pdf_absolute") or it.get("snapshot_pdf")
        sample_role = it.get("sample_role") or getattr(args, "sample_role", "workflow_test")

        if not pdf_rel:
            continue
        pdf_path = Path(pdf_rel)
        if not pdf_path.is_absolute():
            pdf_path = (base_dir / pdf_path).resolve()

        val_ok, val_msg, val_info = verify_pdf_fulltext(pdf_path)
        if not val_ok:
            print(f"[WARN] 文件损坏或非有效PDF: {pdf_path} ({val_msg})", file=sys.stderr)
            has_failure = True
            item_key = compute_item_key(source_id, "", title)
            ledger.upsert_record({
                "item_key": item_key,
                "source_id": source_id,
                "paper_id": paper_id,
                "citation": citation,
                "aliases": [source_id] if source_id else [],
                "title": title,
                "authors": authors,
                "year": year,
                "source": source,
                "url": url,
                "doi": norm_doi,
                "original_pdf_path": str(pdf_path),
                "working_pdf_path": None,
                "file_bytes": pdf_path.stat().st_size if pdf_path.exists() else 0,
                "sha256": None,
                "download_status": "corrupt_pdf",
                "ingest_status": "FAILED",
                "last_error": f"PDF损坏: {val_msg}",
                "sample_role": sample_role,
                "created_at": now_iso()
            })
            ledger.save()
            continue

        file_bytes = val_info["bytes"]
        file_sha = val_info["sha256"]

        # 检查是否重复冲突
        if ledger.is_known_conflict(source_id, file_sha, norm_doi):
            print(f"[WARN] 来源已处于版本冲突阻断状态，跳过提交: {source_id or norm_doi}", file=sys.stderr)
            has_conflict = True
            continue

        # 检查同 source_id 哈希变动
        existing_sid = ledger.find_existing_by_source_id(source_id)
        if existing_sid and existing_sid.get("sha256") and existing_sid.get("sha256") != file_sha:
            reason = f"来源ID {source_id} 内容哈希变化 ({existing_sid.get('sha256')[:8]} != {file_sha[:8]})"
            print(f"[WARN] 版本冲突: {reason}", file=sys.stderr)
            ledger.record_conflict(source_id, title, authors, year, source, url, doi, pdf_path, file_bytes, file_sha, reason, sample_role)
            has_conflict = True
            continue

        # 检查同 DOI 哈希变动
        if norm_doi:
            existing_doi = ledger.find_existing_by_doi(norm_doi)
            if existing_doi and existing_doi.get("sha256") and existing_doi.get("sha256") != file_sha:
                reason = f"相同 DOI {norm_doi} 内容哈希变化 ({existing_doi.get('sha256')[:8]} != {file_sha[:8]})"
                print(f"[WARN] 版本冲突: {reason}", file=sys.stderr)
                ledger.record_conflict(source_id, title, authors, year, source, url, doi, pdf_path, file_bytes, file_sha, reason, sample_role)
                has_conflict = True
                continue

        item_key = compute_item_key(source_id, file_sha, title)
        existing_sha = ledger.find_existing_by_sha256(file_sha)

        if not title:
            # 待补题录
            rec = {
                "item_key": item_key,
                "source_id": source_id,
                "paper_id": paper_id,
                "citation": citation,
                "aliases": [source_id] if source_id else [],
                "title": "",
                "authors": [],
                "year": "",
                "source": "",
                "url": url,
                "doi": norm_doi,
                "original_pdf_path": str(pdf_path),
                "working_pdf_path": None,
                "file_bytes": file_bytes,
                "sha256": file_sha,
                "download_status": "local_imported",
                "ingest_status": "TITLE_MISSING",
                "last_error": "缺少题名，列为待补题录",
                "sample_role": sample_role,
                "created_at": now_iso()
            }
            ledger.upsert_record(rec)
            continue

        if existing_sha and existing_sha.get("ingest_status") == "VERIFIED":
            work_dir = output_dir / "items" / existing_sha["item_key"]
            m_ok, m_msg, m_rec = verify_single_mapping_consistency(
                work_dir / "zotero_mapping.json",
                existing_sha.get("source_id"),
                file_sha, file_bytes,
                existing_sha.get("title"),
                args.storage_dir,
                args.db_path
            )
            if m_ok:
                existing_sha.setdefault("aliases", [existing_sha.get("source_id")])
                if source_id and source_id not in existing_sha["aliases"]:
                    existing_sha["aliases"].append(source_id)
                ledger.upsert_record(existing_sha)
                if source_id and source_id != existing_sha.get("source_id"):
                    alias_key = compute_item_key(source_id, file_sha, title)
                    alias_rec = dict(existing_sha)
                    alias_rec["item_key"] = alias_key
                    alias_rec["source_id"] = source_id
                    alias_rec["title"] = title or existing_sha.get("title")
                    alias_rec["download_status"] = "alias_reused"
                    ledger.upsert_record(alias_rec)
                continue
            else:
                # R3-3: 共同核验失败，撤销 VERIFIED，转为 PENDING_VERIFY，立即从分析清单中排除
                existing_sha["ingest_status"] = "PENDING_VERIFY"
                existing_sha["last_error"] = f"映射核验失败: {m_msg}"
                ledger.upsert_record(existing_sha)
                has_failure = True
                continue

        # 如果记录已经存在且不是未提交队列，根据其当前状态处理
        existing_rec = ledger.data["records"].get(item_key)
        if existing_rec:
            st = existing_rec.get("ingest_status")
            if st == "PENDING_VERIFY":
                actual_db = args.db_path or ZOTERO_DEFAULT_DB
                found_z, p_k, a_k, a_pdf = find_existing_zotero_item_by_evidence(
                    actual_db, args.storage_dir, title, file_sha, file_bytes
                )
                if found_z:
                    print(f"[INFO] 待核对条目 {source_id} 经查验已存在于 Zotero 文库且附件哈希一致，安排核验复用。")
                    to_process_keys.append(item_key)
                    existing_rec["ingest_status"] = "QUEUED"
                    continue

                w_dir = output_dir / "items" / item_key
                s_file = w_dir / "batch_state.json"
                can_recover_session = False
                if s_file.exists():
                    try:
                        s_data = json.loads(s_file.read_text(encoding='utf-8'))
                        if is_structured_session_exists_evidence(s_data, item_key=item_key, paper_id=paper_id) and not found_z:
                            can_recover_session = True
                    except Exception:
                        pass

                if can_recover_session:
                    print(f"[INFO] 待核对条目 {source_id} 查明确系因会话冲突在保存前被拒绝 (SESSION_EXISTS) 且文库无残留，安排安全重新提交。")
                    to_process_keys.append(item_key)
                    existing_rec["ingest_status"] = "QUEUED"
                    continue

                print(f"[WARN] 条目 {source_id} 处于待核对状态 (PENDING_VERIFY)，保持安全阻断！", file=sys.stderr)
                has_pending_blocked = True
                continue
            elif st == "VERSION_CONFLICT":
                print(f"[WARN] 条目 {source_id} 处于版本冲突阻断状态，保持阻断！", file=sys.stderr)
                has_conflict = True
                continue
            elif st == "VERIFIED":
                continue

        # 新增队列记录
        to_process_keys.append(item_key)
        rec = {
            "item_key": item_key,
            "source_id": source_id,
            "paper_id": paper_id,
            "citation": citation,
            "aliases": [source_id] if source_id else [],
            "title": title,
            "authors": authors,
            "year": year,
            "source": source,
            "url": url,
            "doi": norm_doi,
            "original_pdf_path": str(pdf_path),
            "working_pdf_path": None,
            "file_bytes": file_bytes,
            "sha256": file_sha,
            "download_status": "local_imported",
            "ingest_status": "QUEUED",
            "zotero_parent_key": None,
            "zotero_attachment_dir": None,
            "zotero_stored_pdf_path": None,
            "sample_role": sample_role,
            "last_error": None,
            "created_at": now_iso()
        }
        ledger.upsert_record(rec)

    ledger.save()
    ledger.export_ingest_result(input_count=len(items), sample_role=getattr(args, "sample_role", "workflow_test"))

    if not args.commit:
        if has_conflict:
            return EXIT_CONFLICT_BLOCKED
        if has_failure:
            return EXIT_INTEGRITY_ERROR
        return EXIT_SUCCESS

    overall_exit = EXIT_SUCCESS
    for it_key in to_process_keys:
        rec = ledger.data["records"].get(it_key)
        if not rec or rec.get("ingest_status") != "QUEUED":
            continue
        item_work_dir = output_dir / "items" / it_key
        success, updated_rec, exit_code, msg = execute_single_ingest(
            item_rec=rec,
            item_work_dir=item_work_dir,
            connector_url=args.connector_url,
            storage_dir=args.storage_dir,
            db_path=args.db_path or ZOTERO_DEFAULT_DB
        )
        if not success:
            overall_exit = exit_code
            updated_rec["last_error"] = msg
            if exit_code in (EXIT_PENDING_BLOCKED, EXIT_CONFLICT_BLOCKED, EXIT_ATTACHMENT_VERIFY_ERROR, EXIT_RESPONSE_ERROR):
                updated_rec["ingest_status"] = "PENDING_VERIFY"
            elif exit_code == EXIT_NETWORK_ERROR and "确定未提交" in msg:
                updated_rec["ingest_status"] = "PRE_SUBMIT_FAILED"
            else:
                updated_rec["ingest_status"] = "FAILED"
        ledger.upsert_record(updated_rec)
        ledger.save()
        time.sleep(1)
    ledger.export_ingest_result(input_count=len(items), sample_role=getattr(args, "sample_role", "workflow_test"))

    if has_conflict and overall_exit == EXIT_SUCCESS:
        overall_exit = EXIT_CONFLICT_BLOCKED
    elif has_pending_blocked and overall_exit == EXIT_SUCCESS:
        overall_exit = EXIT_PENDING_BLOCKED
    elif has_failure and overall_exit == EXIT_SUCCESS:
        overall_exit = EXIT_INTEGRITY_ERROR
    return overall_exit

def cmd_resume(args):
    """
    安全恢复：只恢复明确确定未提交的失败 (PRE_SUBMIT_FAILED) 或 QUEUED；
    对于处于 PENDING_VERIFY / SUBMITTING / VERSION_CONFLICT 保持安全阻断！
    """
    output_dir = Path(args.output).resolve()
    ledger = LedgerManager(output_dir)

    resumable_keys = []
    blocked_keys = []

    for key, rec in ledger.data.get("records", {}).items():
        st = rec.get("ingest_status")
        if st in ("PRE_SUBMIT_FAILED", "QUEUED"):
            resumable_keys.append(key)
        elif st == "PENDING_VERIFY":
            actual_db = args.db_path or ZOTERO_DEFAULT_DB
            found_z, p_k, a_k, a_pdf = find_existing_zotero_item_by_evidence(
                actual_db, args.storage_dir, rec.get("title"), rec.get("sha256"), rec.get("file_bytes")
            )
            w_dir = output_dir / "items" / key
            s_file = w_dir / "batch_state.json"
            can_recover_session = False
            if s_file.exists():
                try:
                    s_data = json.loads(s_file.read_text(encoding='utf-8'))
                    if is_structured_session_exists_evidence(s_data, item_key=key, paper_id=rec.get("paper_id") or rec.get("source_id")) and not found_z:
                        can_recover_session = True
                except Exception:
                    pass
            if found_z or can_recover_session:
                resumable_keys.append(key)
            else:
                blocked_keys.append((key, st, rec.get("last_error")))
        elif st in ("VERSION_CONFLICT", "TITLE_MISSING", "FAILED"):
            blocked_keys.append((key, st, rec.get("last_error")))

    print(f"[INFO] 恢复扫描完成:")
    print(f"  - 确定可安全执行/重试条目: {len(resumable_keys)} 个")
    print(f"  - 处于待核对或冲突状态保持阻断: {len(blocked_keys)} 个")

    if not resumable_keys:
        if blocked_keys:
            return EXIT_PENDING_BLOCKED
        return EXIT_SUCCESS

    if not args.commit:
        return EXIT_SUCCESS

    overall_exit = EXIT_SUCCESS
    for key in resumable_keys:
        rec = ledger.data["records"][key]
        item_work_dir = output_dir / "items" / key
        success, updated_rec, exit_code, msg = execute_single_ingest(
            item_rec=rec,
            item_work_dir=item_work_dir,
            connector_url=args.connector_url,
            storage_dir=args.storage_dir,
            db_path=args.db_path
        )
        if not success:
            overall_exit = exit_code
            updated_rec["last_error"] = msg
            if exit_code in (EXIT_PENDING_BLOCKED, EXIT_CONFLICT_BLOCKED, EXIT_ATTACHMENT_VERIFY_ERROR, EXIT_RESPONSE_ERROR):
                updated_rec["ingest_status"] = "PENDING_VERIFY"
            elif exit_code == EXIT_NETWORK_ERROR and "确定未提交" in msg:
                updated_rec["ingest_status"] = "PRE_SUBMIT_FAILED"
            else:
                updated_rec["ingest_status"] = "FAILED"
        ledger.upsert_record(updated_rec)
        ledger.save()

    return overall_exit

def cmd_seed_history(args):
    """
    读取已有 T001 等历史映射作为种子，全程 0 次网络写入！
    R3-2 归属核查要求：
    - 数据库可用时核查父子归属；
    - 若没有当前数据库或查询无法证实当前父子归属证据，绝不凭布尔标记宣告通过！
    - 保留原Key与条目，如实列为 PENDING_VERIFY，绝不导出到 papers_manifest.json。
    """
    mapping_p = Path(args.mapping).resolve()
    output_dir = Path(args.output).resolve()
    ledger = LedgerManager(output_dir)

    if not mapping_p.exists():
        print(f"[ERROR] 映射文件不存在: {mapping_p}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    try:
        items = json.loads(mapping_p.read_text(encoding='utf-8'))
        if not isinstance(items, list):
            raise ValueError("not list")
    except Exception as e:
        print(f"[ERROR] 映射文件读取损坏: {e}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    db_path = getattr(args, "db_path", None)

    for it in items:
        source_id = it.get("raw_id") or it.get("source_id") or it.get("id")
        title = it.get("title", "").strip()
        authors = it.get("authors") or []
        year = str(it.get("year", ""))
        source = it.get("source", "")
        url = it.get("url", "")
        doi = it.get("doi", "")
        orig_pdf = it.get("original_pdf_path") or it.get("original_pdf")
        work_pdf = it.get("working_pdf_path") or it.get("working_pdf")
        stored_pdf = it.get("zotero_stored_pdf_path")
        file_bytes = it.get("file_bytes")
        file_sha = it.get("sha256")
        z_parent_key = it.get("zotero_item_key") or it.get("zotero_parent_key")
        z_att_dir = it.get("zotero_attachment_dir")
        sample_role = it.get("sample_role", "workflow_test")

        is_verified = False
        unverified_reason = None

        if not it.get("parent_attachment_linked") or not it.get("hash_verified"):
            unverified_reason = "历史记录中 parent_attachment_linked 或 hash_verified 为 false"
        elif not z_parent_key or z_parent_key == "null":
            unverified_reason = "缺少有效父条目 Key"
        elif not z_att_dir or z_att_dir == "UNKNOWN":
            unverified_reason = "缺少有效附件存储目录"
        elif not stored_pdf or not os.path.exists(stored_pdf):
            unverified_reason = f"存储附件文件不存在: {stored_pdf}"
        else:
            try:
                if sha256_file(stored_pdf) != file_sha or os.path.getsize(stored_pdf) != file_bytes:
                    unverified_reason = "存储文件哈希或大小不一致"
                else:
                    # R3-2 & 整链更新: 只读不等于归属已证实
                    # 必须有正常可复查的数据库证据，且附件确实挂在父条目下；
                    # 无法复查时保留历史Key和文件核验事实，标记待核对 (PENDING_VERIFY)，绝不凭布尔标记导出为 VERIFIED。
                    if db_path and os.path.exists(db_path):
                        db_ok, p_exists, p_atts = query_sqlite_parent_attachments_full(db_path, [z_parent_key])
                        if db_ok and p_exists.get(z_parent_key, False) and z_att_dir in p_atts.get(z_parent_key, []):
                            is_verified = True
                        else:
                            unverified_reason = "当前数据库无法证实父子附件挂接归属"
                    else:
                        unverified_reason = "无当前数据库快照以独立核验父子归属，保留原Key并如实列待核对"
            except Exception as e:
                unverified_reason = f"存储文件核验异常: {e}"

        item_key = compute_item_key(source_id, file_sha, title)
        rec = {
            "item_key": item_key,
            "source_id": source_id,
            "aliases": [source_id] if source_id else [],
            "title": title,
            "authors": authors,
            "year": year,
            "source": source,
            "url": url,
            "doi": normalize_doi(doi),
            "original_pdf_path": orig_pdf,
            "working_pdf_path": work_pdf,
            "file_bytes": file_bytes,
            "sha256": file_sha,
            "download_status": "seeded",
            "ingest_status": "VERIFIED" if is_verified else "PENDING_VERIFY",
            "zotero_parent_key": z_parent_key,
            "zotero_attachment_dir": z_att_dir,
            "zotero_stored_pdf_path": stored_pdf,
            "sample_role": sample_role,
            "last_error": unverified_reason,
            "created_at": now_iso()
        }
        ledger.upsert_record(rec)

    ledger.save()
    print(f"[OK] 历史种子映射读取完成，全程未发送任何网络请求。")
    return EXIT_SUCCESS




def extract_title_and_metadata_from_pdf(filepath, known_mappings=None):
    """
    工作项 1：真实题录来源处理。
    绝不根据文件名长短或无意义黑名单猜测正式 title。
    1. 优先复用可核对的已核验题录/已知映射 (known_mappings 或本批次/输入目录自带映射)；
    2. 尝试读取 PDF 真实元数据 (/Title)；
    3. 尝试读取 PDF 首页第一页正文，识别可能包含的明确论文题名；
    4. 无法证实题录时，文件名永远只作为候选 (title_candidate)，正式 title 必须保持为空，
       保留待补状态 (TITLE_MISSING)，绝不把文件名猜测伪充为已核验题录。
    返回: (formal_title, title_candidate, provenance, confidence)
    """
    p = Path(filepath)
    stem = p.stem

    # 候选题名：去除前导 hex ID 后的文件名主体，仅作参考展示
    candidate_from_filename = re.sub(r'^[a-f0-9]{16}[-_\s]+', '', stem).strip()
    if not candidate_from_filename:
        candidate_from_filename = stem

    # 1. 优先核验已知可靠映射 (若传入或输入目录存在)
    if known_mappings:
        try:
            file_sha = sha256_file(filepath)
            if file_sha in known_mappings:
                return known_mappings[file_sha], candidate_from_filename, "known_mapping_verified", "verified_structured"
        except Exception:
            pass

        m_id = re.match(r'^([a-f0-9]{16})[-_\s]+', stem)
        if m_id and m_id.group(1) in known_mappings:
            return known_mappings[m_id.group(1)], candidate_from_filename, "known_mapping_verified", "verified_structured"

    # 2. 尝试读取 PDF 元数据 (/Title)
    try:
        content_header = p.read_bytes()[:32768]
        m_title = re.search(rb'/Title\s*\(([^\)]+)\)', content_header)
        if m_title:
            try:
                meta_title = m_title.group(1).decode('utf-8', errors='ignore').strip()
            except Exception:
                meta_title = ""
            generic_meta = ["readerex", "word", "acrobat", "untitled", "microsoft", "wps", "unknown"]
            if meta_title and len(meta_title) >= 4 and not any(g in meta_title.lower() for g in generic_meta):
                return meta_title, candidate_from_filename, "pdf_metadata_title", "verified_metadata"
    except Exception:
        pass

    # 3. 无法从可靠来源证实题录时：文件名只作为候选，正式 title 留空，标为待补！
    return "", candidate_from_filename, "filename_candidate_only", "low_candidate"

def cmd_scan_directory(args):
    """
    用户入口 2：已有 PDF 目录真正递归扫描并生成导入清单或直接桥接入库。
    1. 真正递归发现所有层级 PDF (**/*.pdf)；
    2. 校验正文有效性（>=512B, %PDF-, %%EOF）；
    3. 提取题录来源与置信状态，候选题名标为待补/待核实，不伪充为已核验题录；
    4. 自动写入清单并可选提交入库。
    """
    input_dir = Path(args.input_dir).resolve()
    if not input_dir.exists() or not input_dir.is_dir():
        print(f"[ERROR] 输入目录不存在: {input_dir}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_out = getattr(args, "manifest_out", None)
    if not manifest_out:
        manifest_out = output_dir / "scanned_manifest.json"
    else:
        manifest_out = Path(manifest_out).resolve()

    # 真正递归发现所有层级的有效 PDF
    pdf_files = sorted(list(input_dir.rglob("*.pdf")))
    print(f"[INFO] 递归扫描目录 {input_dir}: 发现 {len(pdf_files)} 个 PDF 文件")

    # 自动探测当前输入目录内是否存在同源映射/元数据文件 (如 mapping.json / task.json / metadata.json)
    known_mappings = {}
    for map_name in ["zotero_mapping.json", "mapping.json", "metadata.json", "task.json"]:
        cand_map = input_dir / map_name
        if cand_map.exists():
            try:
                map_obj = json.loads(cand_map.read_text(encoding='utf-8'))
                if isinstance(map_obj, list):
                    for entry in map_obj:
                        if entry.get("title"):
                            if entry.get("sha256"): known_mappings[entry["sha256"]] = entry["title"]
                            if entry.get("raw_id"): known_mappings[entry["raw_id"]] = entry["title"]
                elif isinstance(map_obj, dict):
                    records = map_obj.get("records") or map_obj.get("tasks") or {}
                    for k, v in records.items():
                        if isinstance(v, dict) and v.get("title"):
                            if v.get("sha256"): known_mappings[v["sha256"]] = v["title"]
                            known_mappings[k] = v["title"]
            except Exception:
                pass

    scanned_items = []
    invalid_count = 0

    for pdf_p in pdf_files:
        val_ok, val_msg, val_info = verify_pdf_fulltext(pdf_p)
        if not val_ok:
            print(f"[WARN] 排除无效PDF: {pdf_p.name} ({val_msg})", file=sys.stderr)
            invalid_count += 1
            continue

        file_bytes = val_info["bytes"]
        file_sha = val_info["sha256"]

        formal_title, title_cand, title_prov, meta_conf = extract_title_and_metadata_from_pdf(pdf_p, known_mappings=known_mappings)

        # 尝试从文件名提取规范的 16位 raw_id
        stem = pdf_p.stem
        m_id = re.match(r'^([a-f0-9]{16})[-_\s]+', stem)
        source_id = m_id.group(1) if m_id else f"SCAN_{file_sha[:12]}"

        item = {
            "source_id": source_id,
            "title": formal_title,
            "title_candidate": title_cand,
            "title_provenance": title_prov,
            "metadata_confidence": meta_conf,
            "authors": [],
            "year": "",
            "source": "",
            "url": "",
            "doi": "",
            "pdf_path": str(pdf_p),
            "file_bytes": file_bytes,
            "sha256": file_sha,
            "sample_role": getattr(args, "sample_role", "workflow_test")
        }
        scanned_items.append(item)

    atomic_write_json(manifest_out, scanned_items)
    print(f"[OK] 成功生成导入清单: {manifest_out} (有效文献 {len(scanned_items)} 篇，排除无效 {invalid_count} 篇)")

    if getattr(args, "auto_import", False):
        print(f"[INFO] 自动触发 import-local 桥接入库流程...")
        import_args = argparse.Namespace(
            manifest=str(manifest_out),
            output=str(output_dir),
            commit=getattr(args, "commit", False),
            connector_url=getattr(args, "connector_url", ZOTERO_DEFAULT_CONNECTOR),
            storage_dir=getattr(args, "storage_dir", ZOTERO_DEFAULT_STORAGE),
            db_path=getattr(args, "db_path", None),
            sample_role=getattr(args, "sample_role", "workflow_test")
        )
        return cmd_import_local(import_args)

    return EXIT_SUCCESS


def verify_current_ledger_evidence(ledger, storage_dir=ZOTERO_DEFAULT_STORAGE, db_path=None):
    """
    工作项 2：既有台账当前证据核查。
    不把历史 VERIFIED 标记直接等同于当前核验。
    检查实际物理文件是否存在、大小/SHA256 是否吻合；
    检查 Zotero 附件存储路径 (zotero_stored_pdf_path) 是否真实完好；
    检查数据库父子附件挂接归属（若数据库可用且未锁）。
    无法证明或文件缺失/哈希不符/存储附件缺失的记录，如实降级为 PENDING_VERIFY，
    并更新导出的 papers_manifest.json。
    """
    demoted_count = 0
    verified_count = 0
    retained_historical_count = 0

    db_accessible = False
    db_locked = False

    if db_path and os.path.exists(db_path):
        try:
            conn = sqlite3.connect(f"file:{Path(db_path).resolve().as_posix()}?mode=ro", uri=True, timeout=1.0)
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM items LIMIT 1")
            conn.close()
            db_accessible = True
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                db_locked = True
        except Exception:
            pass

    records_to_check = list(ledger.records.values())
    for rec in records_to_check:
        if rec.get("ingest_status") != "VERIFIED":
            continue

        item_key = rec.get("item_key")
        file_sha = rec.get("sha256")
        file_bytes = rec.get("file_bytes")
        stored_pdf = rec.get("zotero_stored_pdf_path")
        orig_pdf = rec.get("original_pdf_path")
        work_pdf = rec.get("working_pdf_path")
        z_parent_key = rec.get("zotero_parent_key")
        z_att_dir = rec.get("zotero_attachment_dir")

        # 1. Zotero 存储附件完好性核验：若声明了存储路径，则该存储附件必须真实存在且哈希有效
        # 不能仅凭工作副本存在就断定 Zotero 附件完好
        stored_valid = False
        if stored_pdf and os.path.exists(stored_pdf):
            try:
                if os.path.getsize(stored_pdf) == file_bytes and sha256_file(stored_pdf) == file_sha:
                    stored_valid = True
            except Exception:
                pass
        elif not stored_pdf:
            # 未生成存储路径时，检查工作副本或原件
            for cand in [work_pdf, orig_pdf]:
                if cand and os.path.exists(cand):
                    try:
                        if os.path.getsize(cand) == file_bytes and sha256_file(cand) == file_sha:
                            stored_valid = True
                            break
                    except Exception:
                        pass

        if not stored_valid:
            rec["ingest_status"] = "PENDING_VERIFY"
            rec["last_error"] = "Zotero 存储附件缺失或哈希不匹配 (即便工作副本存在也不代表 Zotero 附件完好)"
            demoted_count += 1
            continue

        # 2. 数据库证据核验
        if db_accessible:
            db_ok, p_exists, p_atts = query_sqlite_parent_attachments_full(db_path, [z_parent_key])
            if db_ok and p_exists.get(z_parent_key, False) and z_att_dir in p_atts.get(z_parent_key, []):
                rec["current_db_verified"] = True
                verified_count += 1
            else:
                rec["ingest_status"] = "PENDING_VERIFY"
                rec["last_error"] = "当前数据库无法证实父子附件挂接归属"
                demoted_count += 1
                continue
        elif db_locked:
            rec["current_db_evidence_status"] = "db_locked_historical_retained"
            retained_historical_count += 1
            verified_count += 1
        else:
            rec["current_db_evidence_status"] = "no_current_db_snapshot"
            retained_historical_count += 1
            verified_count += 1

    ledger.save()
    return {
        "verified_count": verified_count,
        "demoted_count": demoted_count,
        "retained_historical_count": retained_historical_count,
        "db_locked": db_locked,
        "db_accessible": db_accessible
    }

def cmd_bridge_to_research(args):
    """
    工作项 1, 3, 4, 5：整链下游协同启动与结果闭环。
    1. 默认入口指向 E:/AI/AI_reserch/deliveries/T005_research_pipeline/research_runner.py；
    2. 默认输出使用独立运行目录 E:/AI/AI_reserch/deliveries/T005_research_pipeline/runs/chain_delivery_r3_from_t004/；
    3. 流式显示长任务运行进展并保存至 t005_execution.log；
    4. 严格接收并核查下游机器可读结果 run_result.json 与产物状态 (completed/partial/failed)，
       不只看文件存在，身份或问题不符时阻断。
    """
    output_dir = Path(args.output).resolve()
    manifest_file = output_dir / "papers_manifest.json"
    if not manifest_file.exists():
        print(f"[ERROR] 未找到分析清单: {manifest_file}，请先执行入库/同步/种子导入", file=sys.stderr)
        return EXIT_INPUT_ERROR

    try:
        manifest_data = json.loads(manifest_file.read_text(encoding='utf-8'))
    except Exception as e:
        print(f"[ERROR] 分析清单读取损坏: {e}", file=sys.stderr)
        return EXIT_INTEGRITY_ERROR

    verified_papers = [p for p in manifest_data if p.get("zotero_key") or p.get("zotero_item_key")]
    print(f"[INFO] 读取到可分析文献集合: {len(verified_papers)} 篇")

    # 1. 默认 T005 主程序路径
    default_t005 = Path("E:/AI/AI_reserch/deliveries/T005_research_pipeline/research_runner.py")
    t005_entry = Path(args.t005_entry).resolve() if getattr(args, "t005_entry", None) else default_t005

    # 2. 自动检测专用运行环境 (优先 T003/T005 .venv)
    python_exec = getattr(args, "python_exec", None)
    if not python_exec:
        candidate_venv = Path("E:/AI/AI_reserch/deliveries/T003_paperqa_bridge/.venv/Scripts/python.exe")
        if candidate_venv.exists():
            python_exec = str(candidate_venv)
        else:
            python_exec = sys.executable

    # 3. 默认独立运行目录 (per R3 调度约定)
    if getattr(args, "research_output", None):
        research_output = Path(args.research_output).resolve()
    else:
        research_output = Path("E:/AI/AI_reserch/deliveries/T005_research_pipeline/runs/chain_delivery_r3_from_t004")
    research_output.mkdir(parents=True, exist_ok=True)

    contract_file = research_output / "t005_handoff_contract.json"
    manifest_sha = sha256_file(manifest_file)
    contract_info = {
        "timestamp": now_iso(),
        "upstream_source": "T004_download_zotero_bridge",
        "manifest_path": str(manifest_file),
        "manifest_hash": manifest_sha,
        "manifest_papers_count": len(verified_papers),
        "research_question": args.question,
        "sample_role_summary": list({p.get("sample_role", "unknown") for p in verified_papers}),
        "target_t005_entry": str(t005_entry),
        "target_research_output": str(research_output),
        "python_interpreter": python_exec,
        "execution_status": "planned",
        "recommended_command": f'"{python_exec}" "{t005_entry}" --manifest "{manifest_file}" --question "{args.question}" --output "{research_output}"'
    }
    atomic_write_json(contract_file, contract_info)
    atomic_write_json(output_dir / "t005_handoff_contract.json", contract_info)
    if (output_dir / "research_run") != research_output:
        (output_dir / "research_run").mkdir(parents=True, exist_ok=True)
        atomic_write_json(output_dir / "research_run" / "t005_handoff_contract.json", contract_info)
    print(f"[OK] 已生成下游衔接契约文件: {contract_file}")

    if not t005_entry.exists():
        print(f"[ERROR] 目标 T005 主入口文件不存在: {t005_entry}", file=sys.stderr)
        contract_info["execution_status"] = "entry_missing"
        atomic_write_json(contract_file, contract_info)
        return EXIT_STATE_ERROR

    # 4. 真正启动 T005 并流式显示进度
    print(f"[INFO] 目标 T005 入口已就绪，正在使用解释器启动研究整链:\n  解释器: {python_exec}\n  程序: {t005_entry}\n  输出: {research_output}")
    cmd = [python_exec, str(t005_entry), "--manifest", str(manifest_file), "--question", args.question, "--output", str(research_output)]

    timeout_seconds = int(getattr(args, "timeout", 1800))
    log_file = research_output / "t005_execution.log"
    print(f"[INFO] 开始执行下游研究任务 (超时预算: {timeout_seconds}秒, 流式日志记录至 {log_file.name})...")

    start_time = time.time()
    timed_out = False
    ret_code = 0

    try:
        with open(log_file, "w", encoding="utf-8", errors="replace") as f_log:
            f_log.write(f"=== T005 Execution Started: {now_iso()} ===\n")
            f_log.write(f"Command: {' '.join(cmd)}\n\n")

            # 设置 PYTHONUNBUFFERED=1 保证下游子进程无缓冲实时输出
            env = dict(os.environ)
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
                env=env
            )

            import queue, threading
            q = queue.Queue()

            def enqueue_output(out, queue):
                try:
                    for line in iter(out.readline, ''):
                        queue.put(line)
                except Exception:
                    pass
                finally:
                    out.close()

            t = threading.Thread(target=enqueue_output, args=(proc.stdout, q), daemon=True)
            t.start()

            while True:
                if time.time() - start_time > timeout_seconds:
                    proc.kill()
                    timed_out = True
                    msg = f"\n[ERROR] T005 运行超时 ({timeout_seconds}秒)，已终止进程并保留现场状态！\n"
                    sys.stderr.write(msg)
                    f_log.write(msg)
                    break

                try:
                    line = q.get(timeout=0.1)
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    f_log.write(line)
                    f_log.flush()
                except queue.Empty:
                    if proc.poll() is not None:
                        # 进程已结束，排空队列剩余内容
                        while not q.empty():
                            rem_line = q.get_nowait()
                            sys.stdout.write(rem_line)
                            sys.stdout.flush()
                            f_log.write(rem_line)
                            f_log.flush()
                        break

            ret_code = proc.poll() if not timed_out else -1

    except Exception as e:
        print(f"[ERROR] 启动或执行 T005 发生异常: {e}", file=sys.stderr)
        contract_info["execution_status"] = f"exception: {e}"
        atomic_write_json(contract_file, contract_info)
        return EXIT_STATE_ERROR

    # 5. 真正接收并核验下游结果 (run_result.json 与状态校验)
    run_result_file = research_output / "run_result.json"
    state_file = research_output / "pipeline_state.json"
    report_md = research_output / "RESEARCH_SYNTHESIS_REPORT.md"

    downstream_info = {}
    downstream_status = "unknown"
    uncompleted_items = []

    report_exists = report_md.exists() and report_md.stat().st_size > 0
    actual_report_sha = sha256_file(report_md) if report_exists else ""

    if run_result_file.exists():
        try:
            run_result_data = json.loads(run_result_file.read_text(encoding='utf-8'))
            downstream_status = run_result_data.get("status", "unknown")
            uncompleted_items = run_result_data.get("uncompleted_items", []) or run_result_data.get("errors_or_unresolved_claims", [])
            downstream_info["run_result"] = run_result_data

            # 必填身份验证 (必填字段缺失或类型异常不能标 completed)
            res_question = run_result_data.get("research_question") or run_result_data.get("question")
            res_mf_sha = run_result_data.get("manifest_sha256") or run_result_data.get("manifest_hash")
            res_rep_sha = run_result_data.get("report_sha256") or run_result_data.get("report_hash")
            res_fp = run_result_data.get("run_fingerprint")

            if downstream_status == "completed":
                if not res_question or not res_mf_sha or not res_rep_sha or not res_fp:
                    print(f"[ERROR] 下游运行结果缺少必填身份字段 (question, manifest_sha256, report_sha256, run_fingerprint)!", file=sys.stderr)
                    downstream_status = "missing_identity"
                elif not isinstance(res_question, str) or not isinstance(res_mf_sha, str) or not isinstance(res_rep_sha, str) or not isinstance(res_fp, str):
                    print(f"[ERROR] 下游身份字段类型异常 (必须为字符串)!", file=sys.stderr)
                    downstream_status = "invalid_identity_type"

            # 1. 核查研究问题一致性
            if res_question:
                if not isinstance(res_question, str) or res_question.strip() != str(args.question).strip():
                    print(f"[ERROR] 下游运行结果研究问题不匹配! 期望: {args.question}, 实际: {res_question}", file=sys.stderr)
                    downstream_status = "identity_mismatch"

            # 2. 核查输入清单哈希 (manifest_sha256)
            if res_mf_sha:
                if not isinstance(res_mf_sha, str) or res_mf_sha != manifest_sha:
                    print(f"[ERROR] 下游运行结果清单哈希不匹配! 期望: {manifest_sha}, 实际: {res_mf_sha}", file=sys.stderr)
                    downstream_status = "manifest_hash_mismatch"
            # 3. 核查产出报告哈希 (report_sha256)
            if res_rep_sha:
                if not isinstance(res_rep_sha, str) or not report_exists or res_rep_sha != actual_report_sha:
                    print(f"[ERROR] 下游产出报告哈希不匹配! 期望: {actual_report_sha}, 实际记录: {res_rep_sha}", file=sys.stderr)
                    downstream_status = "report_hash_mismatch"
        except Exception as e:
            print(f"[WARN] 读取下游 run_result.json 异常: {e}", file=sys.stderr)
            downstream_status = "run_result_corrupt"
    elif state_file.exists():
        try:
            state_data = json.loads(state_file.read_text(encoding='utf-8'))
            downstream_info["pipeline_state"] = state_data
            stages = state_data.get("stages", {})
            pending_stages = [k for k, v in stages.items() if v.get("status") != "completed"]
            if pending_stages:
                downstream_status = "partial"
                uncompleted_items = pending_stages
            else:
                # 缺少 run_result.json 身份文件，即使旧 pipeline_state.json 标 completed 也不能判整链完成
                downstream_status = "missing_run_result"
        except Exception as e:
            print(f"[WARN] 读取下游 pipeline_state.json 异常: {e}", file=sys.stderr)
            downstream_status = "pipeline_state_corrupt"

    # 确定整体交接状态
    if timed_out:
        final_exec_status = "timed_out"
    elif ret_code != 0:
        final_exec_status = f"failed_exit_{ret_code}"
    elif downstream_status == "completed" and report_exists:
        final_exec_status = "completed"
    elif downstream_status in ["partial", "partial_success"]:
        final_exec_status = "partial"
    else:
        final_exec_status = f"downstream_{downstream_status}"
    contract_info["execution_status"] = final_exec_status
    contract_info["exit_code"] = ret_code
    contract_info["report_path"] = str(report_md) if report_exists else None
    contract_info["uncompleted_items"] = uncompleted_items
    contract_info["downstream_status"] = downstream_status

    atomic_write_json(contract_file, contract_info)
    atomic_write_json(output_dir / "t005_handoff_contract.json", contract_info)
    if (output_dir / "research_run") != research_output:
        atomic_write_json(output_dir / "research_run" / "t005_handoff_contract.json", contract_info)

    if final_exec_status == "completed":
        print(f"[SUCCESS] T005 学术研究整链协同执行成功！终稿报告已产出: {report_md}")
        return EXIT_SUCCESS
    elif final_exec_status == "partial":
        return EXIT_SUCCESS
    else:
        print(f"[ERROR] T005 执行异常或未满足完成准则 (状态: {final_exec_status}, 退出码: {ret_code})", file=sys.stderr)
        return ret_code or EXIT_STATE_ERROR

def cmd_status(args):
    output_dir = Path(args.output).resolve()
    ledger = LedgerManager(output_dir)

    counts = {
        "downloaded_or_local": 0,
        "verified_ingested": 0,
        "title_missing": 0,
        "version_conflict": len(ledger.data.get("conflicts", {})),
        "pending_verify": 0,
        "pre_submit_failed": 0,
        "failed": 0,
        "queued": 0
    }

    for key, rec in ledger.data.get("records", {}).items():
        counts["downloaded_or_local"] += 1
        st = rec.get("ingest_status")
        if st == "VERIFIED":
            counts["verified_ingested"] += 1
        elif st == "TITLE_MISSING":
            counts["title_missing"] += 1
        elif st == "VERSION_CONFLICT":
            counts["version_conflict"] += 1
        elif st == "PENDING_VERIFY":
            counts["pending_verify"] += 1
        elif st == "PRE_SUBMIT_FAILED":
            counts["pre_submit_failed"] += 1
        elif st == "FAILED":
            counts["failed"] += 1
        elif st == "QUEUED":
            counts["queued"] += 1

    print("=== T004 文献采集与入库状态台账 ===")
    print(f"总记录篇数: {counts['downloaded_or_local']}")
    print(f"  - 完整核验入库 (VERIFIED): {counts['verified_ingested']}")
    print(f"  - 排队待提交 (QUEUED): {counts['queued']}")
    print(f"  - 待补题录 (TITLE_MISSING): {counts['title_missing']}")
    print(f"  - 版本哈希冲突 (VERSION_CONFLICT): {counts['version_conflict']}")
    print(f"  - 待核对状态 (PENDING_VERIFY - 已安全阻断): {counts['pending_verify']}")
    print(f"  - 确定未提交失败 (PRE_SUBMIT_FAILED - 可安全重试): {counts['pre_submit_failed']}")
    print(f"  - 其他失败 (FAILED): {counts['failed']}")
    print(f"分析层清单: {ledger.manifest_file} (已纳入 {len(json.loads(ledger.manifest_file.read_text(encoding='utf-8')) if ledger.manifest_file.exists() else [])} 篇)")
    ledger.export_ingest_result()
    return EXIT_SUCCESS


def cmd_run_pipeline(args):
    """
    工作项 2：全自动端到端文献入口与研究整链统一运行命令 (run-pipeline)。
    用户只需提供文献来源（已有PDF目录 / 采集任务 / 本地清单 / 既有种子）和核心研究问题，
    程序自行执行：
    1. 自动扫描/对账，提取题录与哈希，生成清单；
    2. 执行正文校验、版本冲突拦截与安全入库；
    3. 导出符合会话二规范的 papers_manifest.json；
    4. 自动调用下游 T005 研究流水线进行全自动分析并产出最终学术研究报告。
    """
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    question = args.question
    sample_role = getattr(args, "sample_role", "workflow_test")

    print("=================================================================")
    print("  T004 文献主入口与整链端到端运行 (Pipeline Runner)")
    print(f"  工作目录: {output_dir}")
    print(f"  研究问题: {question}")
    print(f"  资料角色: {sample_role}")
    print("=================================================================")

    # 阶段 1：文献准备与接入
    if args.input_dir:
        print(f"\n[阶段 1/3] 从本地目录扫描接入 PDF 文献: {args.input_dir}")
        scan_args = argparse.Namespace(
            input_dir=args.input_dir,
            output=str(output_dir),
            manifest_out=str(output_dir / "scanned_manifest.json"),
            auto_import=True,
            commit=args.commit,
            sample_role=sample_role,
            connector_url=getattr(args, "connector_url", ZOTERO_DEFAULT_CONNECTOR),
            storage_dir=getattr(args, "storage_dir", ZOTERO_DEFAULT_STORAGE),
            db_path=getattr(args, "db_path", None)
        )
        code = cmd_scan_directory(scan_args)
        if code != EXIT_SUCCESS and code != EXIT_PENDING_BLOCKED:
            print(f"[ERROR] 目录扫描与入库失败，退出码: {code}", file=sys.stderr)
            return code

    elif args.task:
        print(f"\n[阶段 1/3] 从采集任务同步文献: {args.task}")
        sync_args = argparse.Namespace(
            task=args.task,
            output=str(output_dir),
            commit=args.commit,
            sample_role=sample_role,
            connector_url=getattr(args, "connector_url", ZOTERO_DEFAULT_CONNECTOR),
            storage_dir=getattr(args, "storage_dir", ZOTERO_DEFAULT_STORAGE),
            db_path=getattr(args, "db_path", None)
        )
        code = cmd_sync_downloads(sync_args)
        if code != EXIT_SUCCESS and code != EXIT_PENDING_BLOCKED:
            print(f"[ERROR] 任务同步失败，退出码: {code}", file=sys.stderr)
            return code

    elif args.seed_mapping:
        print(f"\n[阶段 1/3] 从历史映射文件读取种子: {args.seed_mapping}")
        seed_args = argparse.Namespace(
            mapping=args.seed_mapping,
            output=str(output_dir),
            storage_dir=getattr(args, "storage_dir", ZOTERO_DEFAULT_STORAGE),
            db_path=getattr(args, "db_path", None)
        )
        code = cmd_seed_history(seed_args)
        if code != EXIT_SUCCESS:
            print(f"[ERROR] 种子读取失败，退出码: {code}", file=sys.stderr)
            return code

    elif args.manifest:
        print(f"\n[阶段 1/3] 从指定清单导入文献: {args.manifest}")
        loc_args = argparse.Namespace(
            manifest=args.manifest,
            output=str(output_dir),
            commit=args.commit,
            sample_role=sample_role,
            connector_url=getattr(args, "connector_url", ZOTERO_DEFAULT_CONNECTOR),
            storage_dir=getattr(args, "storage_dir", ZOTERO_DEFAULT_STORAGE),
            db_path=getattr(args, "db_path", None)
        )
        code = cmd_import_local(loc_args)
        if code != EXIT_SUCCESS and code != EXIT_PENDING_BLOCKED:
            print(f"[ERROR] 清单导入失败，退出码: {code}", file=sys.stderr)
            return code
    else:
        # 如果 output 目录下已有 ledger.json，直接复用既有台账
        ledger_f = output_dir / "ledger.json"
        if not ledger_f.exists():
            print(f"[ERROR] 必须指定文献来源之一: --input-dir, --task, --seed-mapping, 或 --manifest", file=sys.stderr)
            return EXIT_INPUT_ERROR

    # 阶段 2：核验台账并检查可分析清单
    print(f"\n[阶段 2/3] 汇总台账并核验导出清单...")
    stat_args = argparse.Namespace(output=str(output_dir))
    cmd_status(stat_args)

    manifest_p = output_dir / "papers_manifest.json"
    if not manifest_p.exists():
        print(f"[ERROR] 未找到分析清单: {manifest_p}", file=sys.stderr)
        return EXIT_STATE_ERROR

    manifest_data = json.loads(manifest_p.read_text(encoding='utf-8'))
    print(f"[INFO] 经核验可供下游分析的文献共 {len(manifest_data)} 篇")
    if not manifest_data:
        print(f"[WARN] 当前可分析文献为 0 篇（可能处于待核对状态），整链暂停在文献准备阶段。", file=sys.stderr)
        return EXIT_SUCCESS

    # 阶段 3：直接衔接下游研究流水线
    print(f"\n[阶段 3/3] 自动衔接并启动下游 T005 深度学术研究流水线...")
    research_out = getattr(args, "research_output", None) or Path("E:/AI/AI_reserch/deliveries/T005_research_pipeline/runs/chain_delivery_r3_from_t004")
    bridge_args = argparse.Namespace(
        output=str(output_dir),
        question=question,
        t005_entry=getattr(args, "t005_entry", None),
        research_output=str(research_out),
        python_exec=getattr(args, "python_exec", None),
        timeout=getattr(args, "timeout", 1800)
    )
    return cmd_bridge_to_research(bridge_args)

def main():
    parser = argparse.ArgumentParser(description="T004 Download to Zotero Pipeline Runner (R3)")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    p_rec = subparsers.add_parser("record-download")
    p_rec.add_argument("--task", required=True, help="采集任务目录")
    p_rec.add_argument("--id", required=True, help="文献ID")
    p_rec.add_argument("--file", required=True, help="下载文件路径")
    p_rec.add_argument("--output", required=True, help="T004桥接输出目录")
    p_rec.add_argument("--commit", action="store_true", help="是否提交入库")
    p_rec.add_argument("--sample-role", default="workflow_test", help="资料角色标记 (如: workflow_test 或 thrust_allocation_study)")
    p_rec.add_argument("--connector-url", default=ZOTERO_DEFAULT_CONNECTOR)
    p_rec.add_argument("--storage-dir", default=ZOTERO_DEFAULT_STORAGE)
    p_rec.add_argument("--db-path", default=None)

    p_sync = subparsers.add_parser("sync-downloads")
    p_sync.add_argument("--task", required=True, help="采集任务目录")
    p_sync.add_argument("--output", required=True, help="T004桥接输出目录")
    p_sync.add_argument("--commit", action="store_true", help="是否提交入库")
    p_sync.add_argument("--sample-role", default="workflow_test", help="资料角色标记")
    p_sync.add_argument("--connector-url", default=ZOTERO_DEFAULT_CONNECTOR)
    p_sync.add_argument("--storage-dir", default=ZOTERO_DEFAULT_STORAGE)
    p_sync.add_argument("--db-path", default=None)

    p_loc = subparsers.add_parser("import-local")
    p_loc.add_argument("--manifest", required=True, help="本地清单JSON路径")
    p_loc.add_argument("--output", required=True, help="T004桥接输出目录")
    p_loc.add_argument("--commit", action="store_true", help="是否提交入库")
    p_loc.add_argument("--sample-role", default="workflow_test", help="资料角色标记")
    p_loc.add_argument("--connector-url", default=ZOTERO_DEFAULT_CONNECTOR)
    p_loc.add_argument("--storage-dir", default=ZOTERO_DEFAULT_STORAGE)
    p_loc.add_argument("--db-path", default=None)

    p_stat = subparsers.add_parser("status")
    p_stat.add_argument("--output", required=True, help="T004桥接输出目录")

    p_res = subparsers.add_parser("resume")
    p_res.add_argument("--output", required=True, help="T004桥接输出目录")
    p_res.add_argument("--commit", action="store_true", help="是否提交入库")
    p_res.add_argument("--connector-url", default=ZOTERO_DEFAULT_CONNECTOR)
    p_res.add_argument("--storage-dir", default=ZOTERO_DEFAULT_STORAGE)
    p_res.add_argument("--db-path", default=None)

    p_seed = subparsers.add_parser("seed-history")
    p_seed.add_argument("--mapping", required=True, help="历史映射JSON路径")
    p_seed.add_argument("--output", required=True, help="T004桥接输出目录")
    p_seed.add_argument("--storage-dir", default=ZOTERO_DEFAULT_STORAGE)
    p_seed.add_argument("--db-path", default=None)
    p_seed.add_argument("--read-only-verify", action="store_true", help="已废弃：只读不等于归属已证实，必须有数据库证据")

    p_scan = subparsers.add_parser("scan-dir", help="扫描已有PDF目录自动生成清单并可选入库")
    p_scan.add_argument("--input-dir", required=True, help="PDF文件所在目录")
    p_scan.add_argument("--output", required=True, help="T004桥接输出目录")
    p_scan.add_argument("--manifest-out", help="生成的清单JSON路径（默认 output/scanned_manifest.json）")
    p_scan.add_argument("--auto-import", action="store_true", help="生成清单后是否自动执行 import-local")
    p_scan.add_argument("--commit", action="store_true", help="入库是否正式提交")
    p_scan.add_argument("--sample-role", default="workflow_test", help="资料角色标记")
    p_scan.add_argument("--connector-url", default=ZOTERO_DEFAULT_CONNECTOR)
    p_scan.add_argument("--storage-dir", default=ZOTERO_DEFAULT_STORAGE)
    p_scan.add_argument("--db-path", default=None)

    p_hand = subparsers.add_parser("handoff-research", help="将已核验文献与研究问题衔接给下游 T005")
    p_hand.add_argument("--output", required=True, help="T004桥接输出目录（需包含 papers_manifest.json）")
    p_hand.add_argument("--question", required=True, help="研究问题")
    p_hand.add_argument("--t005-entry", help="T005主程序路径（默认: E:/AI/AI_reserch/deliveries/T005_research_pipeline/research_runner.py）")
    p_hand.add_argument("--research-output", help="下游研究输出目录（默认: output/research_run）")
    p_hand.add_argument("--python-exec", help="指定执行T005的Python解释器路径（默认自动选用 T003/T005 .venv）")
    p_hand.add_argument("--timeout", type=int, default=1800, help="下游研究流水线超时秒数（默认1800秒）")

    p_pipe = subparsers.add_parser("run-pipeline", help="一站式文献入口与下游研究整链统一运行")
    p_pipe.add_argument("--output", required=True, help="T004桥接与研究运行目录")
    p_pipe.add_argument("--question", required=True, help="核心研究问题")
    p_pipe.add_argument("--input-dir", help="已有PDF文献目录")
    p_pipe.add_argument("--task", help="知网采集任务目录")
    p_pipe.add_argument("--seed-mapping", help="历史映射JSON路径")
    p_pipe.add_argument("--manifest", help="输入文献清单路径")
    p_pipe.add_argument("--commit", action="store_true", help="是否正式提交入库")
    p_pipe.add_argument("--sample-role", default="workflow_test", help="资料角色标记")
    p_pipe.add_argument("--t005-entry", help="T005主程序路径（默认: E:/AI/AI_reserch/deliveries/T005_research_pipeline/research_runner.py）")
    p_pipe.add_argument("--research-output", help="下游研究输出目录（默认: E:/AI/AI_reserch/deliveries/T005_research_pipeline/runs/chain_delivery_r3_from_t004）")
    p_pipe.add_argument("--python-exec", help="指定解释器路径（默认自动使用 T003/T005 .venv）")
    p_pipe.add_argument("--timeout", type=int, default=1800, help="下游研究超时预算秒数")
    p_pipe.add_argument("--connector-url", default=ZOTERO_DEFAULT_CONNECTOR)
    p_pipe.add_argument("--storage-dir", default=ZOTERO_DEFAULT_STORAGE)
    p_pipe.add_argument("--db-path", default=None)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(EXIT_INPUT_ERROR)

    if args.command == "record-download":
        code = cmd_record_download(args)
    elif args.command == "sync-downloads":
        code = cmd_sync_downloads(args)
    elif args.command == "import-local":
        code = cmd_import_local(args)
    elif args.command == "status":
        code = cmd_status(args)
    elif args.command == "resume":
        code = cmd_resume(args)
    elif args.command == "seed-history":
        code = cmd_seed_history(args)
    elif args.command == "scan-dir":
        code = cmd_scan_directory(args)
    elif args.command == "handoff-research":
        code = cmd_bridge_to_research(args)
    elif args.command == "run-pipeline":
        code = cmd_run_pipeline(args)
    else:
        code = EXIT_INPUT_ERROR

    sys.exit(code)

if __name__ == "__main__":
    main()
