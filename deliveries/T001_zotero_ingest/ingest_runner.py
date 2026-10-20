#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文献入库标准化与导入工具 (Ingest Preparation and Import Runner) - R2 修订版
任务编号: T001-ZOTERO-INGEST (Revision: R2)
"""

import os
import sys
import csv
import json
import hashlib
import shutil
import urllib.request
import urllib.error
import tempfile
import sqlite3

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

DEFAULT_CSV_PATH = r"C:\Users\lamost\Documents\Codex\2026-10-14\yo\outputs\cnki-ai-self-learning-2026-10-14\已下载文献清单.csv"
DEFAULT_LIB_PATH = r"C:\Users\lamost\Documents\Codex\2026-10-14\yo\outputs\cnki-ai-self-learning-2026-10-14\task\library.csv"
DEFAULT_DELIVERY_DIR = r"E:\AI\AI_reserch\deliveries\T001_zotero_ingest"
DEFAULT_ZOTERO_STORAGE_DIR = r"C:\Users\lamost\Zotero\storage"
DEFAULT_ZOTERO_DB_PATH = r"C:\Users\lamost\Zotero\zotero.sqlite"
ZOTERO_CONNECTOR_URL = "http://127.0.0.1:23119/connector/import"

# 退出码定义
EXIT_SUCCESS = 0
EXIT_ALREADY_COMPLETED = 0
EXIT_INPUT_ERROR = 1
EXIT_INTEGRITY_ERROR = 2
EXIT_NETWORK_ERROR = 3
EXIT_RESPONSE_ERROR = 4
EXIT_STATE_ERROR = 5
EXIT_ATTACHMENT_VERIFY_ERROR = 6
EXIT_PENDING_BLOCKED = 7
EXIT_CONFLICT_BLOCKED = 8

def compute_sha256(filepath):
    h = hashlib.sha256()
    with open(filepath, 'rb') as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def compute_batch_fingerprint(standardized_items):
    """根据本批所有输入条目的RawID、题名、SHA256生成批次指纹"""
    tokens = []
    for item in sorted(standardized_items, key=lambda x: str(x.get('raw_id'))):
        tokens.append(f"{item.get('raw_id')}:{item.get('title')}:{item.get('sha256')}")
    raw_str = "|".join(tokens)
    return hashlib.sha256(raw_str.encode('utf-8')).hexdigest()

def check_zotero_connector(url="http://127.0.0.1:23119/connector/ping"):
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False

def atomic_write_json(file_path, data):
    """原子化安全保存 JSON 文件，避免半写入文件 (R1-1)"""
    dirname = os.path.dirname(os.path.abspath(file_path))
    os.makedirs(dirname, exist_ok=True)
    temp_fd, temp_path = tempfile.mkstemp(dir=dirname, prefix="tmp_", suffix=".json")
    try:
        with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        if os.path.exists(file_path):
            os.replace(temp_path, file_path)
        else:
            os.rename(temp_path, file_path)
    except Exception as e:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass
        raise e

def prepare_materials(csv_path, lib_path, delivery_dir):
    """
    读取并清洗元数据，构建工作副本，生成标准化导入材料。
    包含工作副本完整性检验与安全重建逻辑 (R1-2)。
    """
    if not os.path.exists(csv_path):
        print(f"[ERROR] 原始清单文件不存在: {csv_path}", file=sys.stderr)
        sys.exit(EXIT_INPUT_ERROR)

    os.makedirs(delivery_dir, exist_ok=True)
    papers_work_dir = os.path.join(delivery_dir, "papers_work_copy")
    os.makedirs(papers_work_dir, exist_ok=True)

    lib_map = {}
    if os.path.exists(lib_path):
        try:
            with open(lib_path, 'r', encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    if row.get('status') == 'downloaded':
                        lib_map[row['title']] = row
        except Exception as e:
            print(f"[WARN] 读取 library.csv 异常: {e}", file=sys.stderr)

    entries = []
    try:
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                entries.append(row)
    except Exception as e:
        print(f"[ERROR] 读取 CSV 清单失败: {e}", file=sys.stderr)
        sys.exit(EXIT_INPUT_ERROR)

    if not entries:
        print(f"[ERROR] 清单内容为空: {csv_path}", file=sys.stderr)
        sys.exit(EXIT_INPUT_ERROR)

    print(f"[INFO] 成功读取输入清单: {len(entries)} 条记录")

    standardized_items = []
    ris_blocks = []
    bib_blocks = []

    for idx, item in enumerate(entries):
        title = item.get('标题', '').strip()
        authors = [a.strip() for a in item.get('作者', '').split(';') if a.strip()]
        year = item.get('年份', '').strip()
        source = item.get('来源', '').strip()
        orig_pdf = item.get('本地PDF', '').strip()
        url = item.get('书目链接', '').strip()
        orig_sha256 = item.get('SHA256', '').strip()
        try:
            orig_bytes = int(item.get('文件字节数', '0').strip())
        except ValueError:
            orig_bytes = 0

        if not os.path.exists(orig_pdf):
            print(f"[ERROR] 原始PDF不存在: {orig_pdf}", file=sys.stderr)
            sys.exit(EXIT_INTEGRITY_ERROR)

        orig_calc_sha256 = compute_sha256(orig_pdf)
        orig_calc_bytes = os.path.getsize(orig_pdf)

        if orig_calc_sha256 != orig_sha256 or orig_calc_bytes != orig_bytes:
            print(f"[ERROR] 原始PDF校验失败: {title} (记录SHA={orig_sha256}, 计算SHA={orig_calc_sha256})", file=sys.stderr)
            sys.exit(EXIT_INTEGRITY_ERROR)

        lib_row = lib_map.get(title, {})
        raw_id = lib_row.get('id', f"RAW_{idx+1:03d}")

        # R1-2: 检查并验证工作副本完整性；若损坏则从原件安全重建
        pdf_filename = os.path.basename(orig_pdf)
        work_pdf = os.path.join(papers_work_dir, pdf_filename)

        needs_copy = True
        if os.path.exists(work_pdf):
            work_bytes = os.path.getsize(work_pdf)
            if work_bytes == orig_calc_bytes:
                work_sha = compute_sha256(work_pdf)
                if work_sha == orig_calc_sha256:
                    needs_copy = False
                else:
                    print(f"[WARN] 工作副本哈希不匹配，正在从原件安全重建: {pdf_filename}")
            else:
                print(f"[WARN] 工作副本大小不匹配 ({work_bytes} != {orig_calc_bytes})，正在从原件安全重建: {pdf_filename}")

        if needs_copy:
            try:
                shutil.copy2(orig_pdf, work_pdf)
            except Exception as e:
                print(f"[ERROR] 复制工作副本失败: {e}", file=sys.stderr)
                sys.exit(EXIT_INTEGRITY_ERROR)

        final_work_bytes = os.path.getsize(work_pdf)
        final_work_sha = compute_sha256(work_pdf)
        if final_work_bytes != orig_calc_bytes or final_work_sha != orig_calc_sha256:
            print(f"[ERROR] 工作副本核验依然失败: {work_pdf}", file=sys.stderr)
            sys.exit(EXIT_INTEGRITY_ERROR)

        entry_dict = {
            "index": idx + 1,
            "raw_id": raw_id,
            "title": title,
            "authors": authors,
            "year": year,
            "source": source,
            "url": url,
            "citations": item.get('被引', ''),
            "downloads": item.get('下载量', ''),
            "reason": item.get('入选理由', ''),
            "original_pdf": orig_pdf,
            "working_pdf": work_pdf,
            "file_bytes": final_work_bytes,
            "sha256": final_work_sha,
            "tag": "AI科研链条_入库验证_T001"
        }
        standardized_items.append(entry_dict)

        ris_entry = [
            "TY  - JOUR",
            f"TI  - {title}"
        ]
        for a in authors:
            ris_entry.append(f"AU  - {a}")
        if year:
            ris_entry.append(f"PY  - {year}")
        if source:
            ris_entry.append(f"JO  - {source}")
        if url:
            ris_entry.append(f"UR  - {url}")
        ris_entry.append(f"L1  - {work_pdf}")
        ris_entry.append(f"M2  - RawID: {raw_id} | SHA256: {final_work_sha}")
        ris_entry.append("KW  - AI科研链条_入库验证_T001")
        ris_entry.append("ER  - ")
        ris_blocks.append("\n".join(ris_entry))

        bib_key = f"cnki_{raw_id}"
        bib_authors = " and ".join(authors)
        norm_work_pdf = work_pdf.replace("\\", "/")
        bib_file_field = f"{title}:{norm_work_pdf}:application/pdf"
        bib_entry = [
            f"@article{{{bib_key},",
            f"  title = {{{{{title}}}}},",
            f"  author = {{{bib_authors}}},",
            f"  journal = {{{{{source}}}}},",
            f"  year = {{{year}}},",
            f"  url = {{{url}}},",
            f"  keywords = {{AI科研链条_入库验证_T001}},",
            f"  note = {{RawID: {raw_id}, SHA256: {final_work_sha}}},",
            f"  file = {{{bib_file_field}}}",
            "}"
        ]
        bib_blocks.append("\n".join(bib_entry))

    json_path = os.path.join(delivery_dir, "standardized_items.json")
    atomic_write_json(json_path, standardized_items)

    ris_path = os.path.join(delivery_dir, "import_materials.ris")
    with open(ris_path, 'w', encoding='utf-8') as f:
        f.write("\n\n".join(ris_blocks) + "\n")

    bib_path = os.path.join(delivery_dir, "import_materials.bib")
    with open(bib_path, 'w', encoding='utf-8') as f:
        f.write("\n\n".join(bib_blocks) + "\n")

    print(f"[OK] 入库材料准备完成:")
    print(f"  - 题录结构数据: {json_path}")
    print(f"  - RIS导入文件: {ris_path}")
    print(f"  - BibTeX导入文件: {bib_path}")
    print(f"  - PDF工作副本: {papers_work_dir}")
    return standardized_items, ris_path

def check_batch_state(delivery_dir, current_fingerprint):
    """
    R2-B: 检查批次状态文件 batch_state.json。
    返回 (can_proceed, status, info_dict, message)
    """
    state_file = os.path.join(delivery_dir, "batch_state.json")
    if not os.path.exists(state_file):
        return True, "FRESH", {}, "新批次"

    try:
        with open(state_file, 'r', encoding='utf-8') as f:
            state_data = json.load(f)
    except Exception as e:
        return False, "CORRUPT", {}, f"批次状态文件损坏: {e}"

    fp = state_data.get('batch_fingerprint')
    if fp != current_fingerprint:
        return False, "MISMATCH", state_data, f"目录存在不同批次的记录 (fingerprint mismatch)"

    status = state_data.get('status')
    if status == "COMPLETED":
        return True, "COMPLETED", state_data, "本批次已全部核验完成"
    elif status in ("PENDING", "SUBMITTED", "SUBMITTING", "PARTIAL", "ATTACHMENT_UNVERIFIED"):
        reason = state_data.get('pending_reason', '存在未决状态')
        return False, status, state_data, f"批次处于待核对状态 [{status}]: {reason}"
    else:
        return False, "UNKNOWN", state_data, f"未知批次状态: {status}"

def check_existing_mapping_completed(mapping_file, standardized_items):
    """
    R1-1 & R2-A: 核对已有映射记录中的身份、附件父子归属与文件哈希。
    返回 (is_completed, reason)
    """
    if not os.path.exists(mapping_file):
        return False, "映射文件不存在"

    try:
        with open(mapping_file, 'r', encoding='utf-8') as f:
            mapping_data = json.load(f)
    except Exception as e:
        return False, f"映射文件读取损坏: {e}"

    if not isinstance(mapping_data, list) or len(mapping_data) != len(standardized_items):
        return False, f"映射条目数量与当前输入不符 ({len(mapping_data) if isinstance(mapping_data, list) else 0} != {len(standardized_items)})"

    mapped_by_raw_id = {m.get('raw_id'): m for m in mapping_data}
    for item in standardized_items:
        raw_id = item['raw_id']
        if raw_id not in mapped_by_raw_id:
            return False, f"缺少条目 {raw_id} 的映射记录"
        m = mapped_by_raw_id[raw_id]
        if m.get('title') != item['title']:
            return False, f"条目 {raw_id} 题名不匹配"
        if m.get('sha256') != item['sha256']:
            return False, f"条目 {raw_id} 哈希不匹配"
        if not m.get('zotero_item_key'):
            return False, f"条目 {raw_id} 缺少有效 Zotero Key"
        if not m.get('hash_verified'):
            return False, f"条目 {raw_id} 附件尚未通过核验 (hash_verified is false)"
        if not m.get('parent_attachment_linked'):
            return False, f"条目 {raw_id} 附件未证明挂接在本次父条目下 (parent_attachment_linked is false)"

    return True, "全量条目身份、父子附件归属与哈希核验一致通过"

def query_zotero_db_parent_attachments(db_path, parent_keys):
    """
    通过只读连接查询 Zotero SQLite 数据库快照，获取父条目实际挂接的附件子条目 Key 列表。
    """
    if not os.path.exists(db_path):
        return {}

    parent_to_attachments = {k: [] for k in parent_keys}
    temp_dir = tempfile.mkdtemp(prefix="zotero_db_ro_")
    try:
        # 安全复制 sqlite + wal + shm 防止锁冲突
        src_dir = os.path.dirname(db_path)
        base_name = os.path.basename(db_path)
        for ext in ["", "-wal", "-shm", "-journal"]:
            fpath = os.path.join(src_dir, base_name + ext)
            if os.path.exists(fpath):
                shutil.copyfile(fpath, os.path.join(temp_dir, base_name + ext))

        copied_db = os.path.join(temp_dir, base_name)
        conn = sqlite3.connect(f"file:{copied_db}?mode=ro", uri=True)
        cur = conn.cursor()

        for p_key in parent_keys:
            cur.execute("""
                SELECT i_att.key
                FROM items i_parent
                JOIN itemAttachments a ON a.parentItemID = i_parent.itemID
                JOIN items i_att ON i_att.itemID = a.itemID
                WHERE i_parent.key = ?
            """, (p_key,))
            rows = cur.fetchall()
            parent_to_attachments[p_key] = [r[0] for r in rows]

        conn.close()
    except Exception as e:
        print(f"[DEBUG] SQLite读取附件关联异常: {e}", file=sys.stderr)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return parent_to_attachments

def match_and_build_mapping(standardized_items, imported_items, storage_dir=DEFAULT_ZOTERO_STORAGE_DIR, db_path=DEFAULT_ZOTERO_DB_PATH):
    """
    R2-A: 根据接口返回的导入条目列表和存储目录构建映射。
    严格核查父子归属：
    1. 优先通过 imported_item['attachments'] 提取子附件 Key；
    2. 其次通过数据库快照核对 parentItemID -> itemAttachments 真实归属；
    3. 严禁通过全 storage 扫描不验证父子关系就判定为通过。
    """
    if not imported_items:
        raise ValueError("接口返回条目列表为空")

    imported_by_title = {it.get('title'): it for it in imported_items}

    # 获取所有返回的 parent keys
    parent_keys = [it.get('key') for it in imported_items if it.get('key')]
    db_parent_att_map = query_zotero_db_parent_attachments(db_path, parent_keys)

    mappings = []
    unverified_reasons = []

    for item in standardized_items:
        title = item['title']
        if title not in imported_by_title:
            unverified_reasons.append(f"返回结果缺少题录: {title}")
            continue

        z_item = imported_by_title[title]
        z_key = z_item.get('key')
        if not z_key:
            unverified_reasons.append(f"题录缺少 Key: {title}")
            continue

        # 获取挂接在此父条目下的附件 Key 列表
        child_att_keys = []
        raw_atts = z_item.get('attachments')
        if raw_atts and isinstance(raw_atts, list):
            for a in raw_atts:
                if isinstance(a, dict) and a.get('key'):
                    child_att_keys.append(a.get('key'))
                elif isinstance(a, str):
                    child_att_keys.append(a)

        # 结合数据库快照
        if not child_att_keys and z_key in db_parent_att_map:
            child_att_keys = db_parent_att_map[z_key]

        pdf_name = os.path.basename(item['working_pdf'])
        matched_storage_dir = None
        matched_storage_pdf = None
        parent_attachment_linked = False
        hash_verified = False

        if child_att_keys:
            # 严格核对挂在此父条目下的附件目录
            for att_key in child_att_keys:
                candidate_pdf = os.path.join(storage_dir, att_key, pdf_name)
                if os.path.exists(candidate_pdf):
                    c_sha = compute_sha256(candidate_pdf)
                    if c_sha == item['sha256']:
                        matched_storage_dir = att_key
                        matched_storage_pdf = candidate_pdf
                        parent_attachment_linked = True
                        hash_verified = True
                        break

        # 如果没有合法的父子挂接附件，绝不全storage盲目匹配无关旧副本
        if not parent_attachment_linked:
            unverified_reasons.append(f"条目 '{title}' (Key: {z_key}) 未挂接匹配的PDF附件 (已关联附件Key: {child_att_keys})")

        m_record = {
            "index": item['index'],
            "raw_id": item['raw_id'],
            "zotero_item_key": z_key,
            "zotero_attachment_dir": matched_storage_dir or "UNKNOWN",
            "title": title,
            "authors": item['authors'],
            "year": item['year'],
            "source": item['source'],
            "url": item['url'],
            "original_pdf_path": item['original_pdf'],
            "working_pdf_path": item['working_pdf'],
            "zotero_stored_pdf_path": matched_storage_pdf or "UNKNOWN",
            "file_bytes": item['file_bytes'],
            "sha256": item['sha256'],
            "parent_attachment_linked": parent_attachment_linked,
            "hash_verified": hash_verified
        }
        mappings.append(m_record)

    all_verified = (len(mappings) == len(standardized_items) and len(unverified_reasons) == 0 and all(m['hash_verified'] and m['parent_attachment_linked'] for m in mappings))
    return mappings, all_verified, unverified_reasons

def import_to_zotero(ris_path, standardized_items, session_id, delivery_dir,
                     force=False, connector_url=ZOTERO_CONNECTOR_URL,
                     storage_dir=DEFAULT_ZOTERO_STORAGE_DIR, db_path=DEFAULT_ZOTERO_DB_PATH):
    """
    R2-A & R2-B: 向 Zotero 发起导入请求，全程维护批次状态，阻断盲目重复提交，严格核验附件归属。
    """
    mapping_file = os.path.join(delivery_dir, "zotero_mapping.json")
    state_file = os.path.join(delivery_dir, "batch_state.json")

    batch_fp = compute_batch_fingerprint(standardized_items)

    # 1. 检查批次状态与映射完整性 (R2 收尾：修复完成状态与映射缺失/损坏的冲突恢复)
    if not force:
        can_proceed, state_status, state_data, state_msg = check_batch_state(delivery_dir, batch_fp)
        if not can_proceed:
            print(f"[WARN] 批次处于待核对或不匹配状态，安全阻断自动重复提交: {state_msg}", file=sys.stderr)
            print("[INFO] 请人工核查或带 --force 重新运行。", file=sys.stderr)
            return EXIT_PENDING_BLOCKED

        # 若状态记录已标记 COMPLETED
        if state_status == "COMPLETED":
            completed, reason = check_existing_mapping_completed(mapping_file, standardized_items)
            if completed:
                print(f"[INFO] 检测到本批文献已在 Zotero 中成功导入且全量核验通过 ({reason})。")
                print("[INFO] 为避免产生重复题录与附件，导入流程已安全跳过。若需强制重导请使用 --force。")
                return EXIT_ALREADY_COMPLETED
            else:
                # 状态标记已完成，但映射缺失或损坏：安全阻断，保留原状态并非零退出，提示只读核对，禁止自动重提！
                print(f"[ERROR] 批次状态已记录为 COMPLETED，但映射文件无法核实 ({reason})！", file=sys.stderr)
                print(f"[ERROR] 为防止对已入库条目发起重复导入，流程已安全阻断。请只读核查映射文件或使用 --force 显式覆盖。", file=sys.stderr)
                return EXIT_CONFLICT_BLOCKED
        else:
            # 新批次 (FRESH)：进一步核对是否已有完整映射
            completed, reason = check_existing_mapping_completed(mapping_file, standardized_items)
            if completed:
                print(f"[INFO] 检测到本批文献已在 Zotero 中成功导入且全量核验通过 ({reason})。")
                print("[INFO] 为避免产生重复题录与附件，导入流程已安全跳过。若需强制重导请使用 --force。")
                atomic_write_json(state_file, {
                    "batch_fingerprint": batch_fp,
                    "status": "COMPLETED",
                    "message": reason,
                    "items_count": len(standardized_items)
                })
                return EXIT_ALREADY_COMPLETED

    # 3. 记录 SUBMITTING 初始状态
    atomic_write_json(state_file, {
        "batch_fingerprint": batch_fp,
        "status": "SUBMITTING",
        "session_id": session_id,
        "items_count": len(standardized_items),
        "pending_reason": "请求发起中，结果尚未确认"
    })

    # 4. 连通性测试
    ping_url = connector_url.replace("/import", "/ping")
    if not check_zotero_connector(ping_url):
        print(f"[ERROR] 未检测到 Zotero 监听端口 ({ping_url} 未响应)。", file=sys.stderr)
        atomic_write_json(state_file, {
            "batch_fingerprint": batch_fp,
            "status": "PENDING",
            "session_id": session_id,
            "pending_reason": f"网络连通失败: {ping_url} 未响应"
        })
        return EXIT_NETWORK_ERROR

    try:
        with open(ris_path, 'r', encoding='utf-8') as f:
            ris_data = f.read().encode('utf-8')
    except Exception as e:
        print(f"[ERROR] 读取 RIS 材料失败: {e}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    url = f"{connector_url}?session={session_id}"
    req = urllib.request.Request(url, data=ris_data, headers={"Content-Type": "text/plain; charset=utf-8"})

    # 5. 发送请求
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status_code = resp.status
            body_text = resp.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        print(f"[ERROR] 导入接口 HTTP 异常: {e.code} {e.reason}", file=sys.stderr)
        atomic_write_json(state_file, {
            "batch_fingerprint": batch_fp,
            "status": "PENDING",
            "session_id": session_id,
            "pending_reason": f"HTTP错误: {e.code} {e.reason}"
        })
        return EXIT_RESPONSE_ERROR
    except urllib.error.URLError as e:
        print(f"[ERROR] 网络连接异常/超时: {e.reason}", file=sys.stderr)
        atomic_write_json(state_file, {
            "batch_fingerprint": batch_fp,
            "status": "PENDING",
            "session_id": session_id,
            "pending_reason": f"连接异常或超时: {e.reason}"
        })
        return EXIT_NETWORK_ERROR
    except Exception as e:
        print(f"[ERROR] 请求发生未知错误: {e}", file=sys.stderr)
        atomic_write_json(state_file, {
            "batch_fingerprint": batch_fp,
            "status": "PENDING",
            "session_id": session_id,
            "pending_reason": f"未知错误: {e}"
        })
        return EXIT_NETWORK_ERROR

    if status_code != 201:
        print(f"[ERROR] 接口返回非预期状态码: {status_code}, 响应: {body_text}", file=sys.stderr)
        atomic_write_json(state_file, {
            "batch_fingerprint": batch_fp,
            "status": "PENDING",
            "session_id": session_id,
            "pending_reason": f"非预期状态码 {status_code}"
        })
        return EXIT_RESPONSE_ERROR

    try:
        imported_items = json.loads(body_text)
    except Exception as e:
        print(f"[ERROR] 解析接口返回 JSON 失败: {e}", file=sys.stderr)
        atomic_write_json(state_file, {
            "batch_fingerprint": batch_fp,
            "status": "PENDING",
            "session_id": session_id,
            "pending_reason": f"响应JSON损坏: {e}"
        })
        return EXIT_RESPONSE_ERROR

    # 6. R2-B: 检查是否部分返回
    if not isinstance(imported_items, list) or len(imported_items) != len(standardized_items):
        err_msg = f"接口仅部分返回条目: 期望 {len(standardized_items)}, 实际返回 {len(imported_items) if isinstance(imported_items, list) else 0}"
        print(f"[ERROR] {err_msg}", file=sys.stderr)
        atomic_write_json(state_file, {
            "batch_fingerprint": batch_fp,
            "status": "PARTIAL",
            "session_id": session_id,
            "pending_reason": err_msg,
            "partial_returned_count": len(imported_items) if isinstance(imported_items, list) else 0
        })
        return EXIT_STATE_ERROR

    # 7. R2-A: 构建映射并执行严格父子附件核验
    try:
        mappings, all_verified, unverified_reasons = match_and_build_mapping(
            standardized_items, imported_items, storage_dir=storage_dir, db_path=db_path
        )
    except Exception as e:
        print(f"[ERROR] 构建映射异常: {e}", file=sys.stderr)
        atomic_write_json(state_file, {
            "batch_fingerprint": batch_fp,
            "status": "PENDING",
            "session_id": session_id,
            "pending_reason": f"构建映射失败: {e}"
        })
        return EXIT_STATE_ERROR

    # 保存映射（无论是否全核验通过，都保存当前核验现场供排查，但不作为COMPLETED）
    try:
        atomic_write_json(mapping_file, mappings)
    except Exception as e:
        print(f"[ERROR] 保存映射文件失败: {e}", file=sys.stderr)
        atomic_write_json(state_file, {
            "batch_fingerprint": batch_fp,
            "status": "PENDING",
            "session_id": session_id,
            "pending_reason": f"映射文件保存失败: {e}"
        })
        return EXIT_STATE_ERROR

    # 8. R2-A: 核验附件结果判定
    if not all_verified:
        err_detail = "; ".join(unverified_reasons)
        print(f"[ERROR] 附件父子挂接或哈希核验未通过: {err_detail}", file=sys.stderr)
        atomic_write_json(state_file, {
            "batch_fingerprint": batch_fp,
            "status": "ATTACHMENT_UNVERIFIED",
            "session_id": session_id,
            "pending_reason": f"附件未核验: {err_detail}",
            "unverified_items": unverified_reasons
        })
        return EXIT_ATTACHMENT_VERIFY_ERROR

    # 9. 完整成功
    atomic_write_json(state_file, {
        "batch_fingerprint": batch_fp,
        "status": "COMPLETED",
        "session_id": session_id,
        "message": "全量条目与父子附件核验一致通过",
        "items_count": len(mappings)
    })
    print(f"[OK] 成功导入 {len(mappings)} 个条目，附件父子归属与哈希核验全部通过，状态已保存: {mapping_file}")
    return EXIT_SUCCESS

def main():
    import argparse
    import time

    parser = argparse.ArgumentParser(description="T001 Zotero Ingest Tool (R2)")
    parser.add_argument("--prepare-only", action="store_true", help="仅生成标准化导入材料，不执行实际导入")
    parser.add_argument("--import-only", action="store_true", help="仅执行导入（需已有材料）")
    parser.add_argument("--force", action="store_true", help="忽略已有导入映射与未决状态，强制重新导入")
    parser.add_argument("--csv", default=DEFAULT_CSV_PATH, help="原始CSV路径")
    parser.add_argument("--lib", default=DEFAULT_LIB_PATH, help="原始library.csv路径")
    parser.add_argument("--delivery", default=DEFAULT_DELIVERY_DIR, help="交付输出目录")
    parser.add_argument("--connector-url", default=ZOTERO_CONNECTOR_URL, help="Zotero导入接口URL (用于测试注入)")
    parser.add_argument("--storage-dir", default=DEFAULT_ZOTERO_STORAGE_DIR, help="Zotero存储目录 (用于测试注入)")
    parser.add_argument("--db-path", default=DEFAULT_ZOTERO_DB_PATH, help="Zotero数据库路径 (用于测试注入)")
    args = parser.parse_args()

    delivery_dir = os.path.abspath(args.delivery)
    ris_path = os.path.join(delivery_dir, "import_materials.ris")
    json_path = os.path.join(delivery_dir, "standardized_items.json")

    standardized_items = None
    if not args.import_only:
        standardized_items, ris_path = prepare_materials(args.csv, args.lib, delivery_dir)
    else:
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                standardized_items = json.load(f)
        else:
            print(f"[ERROR] 找不到标准化数据: {json_path}，无法执行 --import-only", file=sys.stderr)
            sys.exit(EXIT_INPUT_ERROR)

    if args.prepare_only:
        sys.exit(EXIT_SUCCESS)

    session_id = f"ingest_{int(time.time())}"
    exit_code = import_to_zotero(
        ris_path=ris_path,
        standardized_items=standardized_items,
        session_id=session_id,
        delivery_dir=delivery_dir,
        force=args.force,
        connector_url=args.connector_url,
        storage_dir=args.storage_dir,
        db_path=args.db_path
    )

    sys.exit(exit_code)

if __name__ == "__main__":
    main()
