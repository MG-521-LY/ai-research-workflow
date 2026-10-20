#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
T003 PaperQA2 离线衔接与环境预检运行器 (bridge_runner.py)
阶段: T003A (独立环境、接口预检及离线资料衔接 - R3 返修完全收尾版)
"""

import os
import sys

# 【核心防联网配置】在第三方库导入前，设置本地离线环境变量，杜绝联网获取价格表或权重
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import json
import re
import hashlib
import unicodedata
import argparse
import shutil
import asyncio
from pathlib import Path

# 确保 UTF-8 输出
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MANIFEST = SCRIPT_DIR / "input_manifest.json"
DEFAULT_CONFIG = SCRIPT_DIR / "config_template.json"
CAPABILITY_REPORT = SCRIPT_DIR / "capability_report.json"

EXIT_SUCCESS = 0
EXIT_INPUT_ERROR = 1
EXIT_INTEGRITY_ERROR = 2
EXIT_ENVIRONMENT_ERROR = 3
EXIT_RETRIEVAL_ERROR = 4

def norm(s):
    if not s:
        return ""
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(s)))

def compute_sha256(filepath):
    h = hashlib.sha256()
    with open(filepath, 'rb') as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def compute_manifest_fingerprint(manifest_items):
    """计算当前输入清单的确定性指纹"""
    normalized = []
    for it in sorted(manifest_items, key=lambda x: str(x.get("paper_id", ""))):
        normalized.append({
            "paper_id": it.get("paper_id"),
            "raw_id": it.get("raw_id"),
            "sha256": it.get("sha256"),
            "file_bytes": it.get("file_bytes")
        })
    raw_str = json.dumps(normalized, sort_keys=True)
    return hashlib.sha256(raw_str.encode('utf-8')).hexdigest()

def compute_mapping_fingerprint(mapping_dict):
    """
    【R3-1核心机制】计算映射内容、分块策略与来源身份的深层确定性指纹。
    包含：
    - manifest_fingerprint
    - chunking_strategy
    - active_paper_ids (排序)
    - chunks_count
    - summary 统计元数据
    - 每个 chunk 的全量关键字段 (chunk_id, paper_id, raw_id, zotero_item_key, physical_page, char_start, char_end, chunk_char_count, chunk_hash, pdf_sha256, content)
    索引强绑定此指纹；任何片段内容、起止偏移、哈希、出处或分块变动，指纹立即改变并强制重建索引。
    """
    normalized_chunks = []
    for c in sorted(mapping_dict.get("chunks", []), key=lambda x: str(x.get("chunk_id", ""))):
        normalized_chunks.append({
            "chunk_id": c.get("chunk_id"),
            "paper_id": c.get("paper_id"),
            "raw_id": c.get("raw_id"),
            "zotero_item_key": c.get("zotero_item_key"),
            "physical_page": c.get("physical_page"),
            "printed_page": c.get("printed_page"),
            "char_start_in_page": c.get("char_start_in_page"),
            "char_end_in_page": c.get("char_end_in_page"),
            "chunk_char_count": c.get("chunk_char_count"),
            "chunk_hash": c.get("chunk_hash"),
            "pdf_sha256": c.get("pdf_sha256"),
            "content": c.get("content")
        })
    raw_dict = {
        "manifest_fingerprint": mapping_dict.get("manifest_fingerprint"),
        "chunking_strategy": mapping_dict.get("chunking_strategy", "physical_page_with_paragraphs"),
        "active_paper_ids": sorted(list(mapping_dict.get("active_paper_ids", []))),
        "chunks_count": mapping_dict.get("chunks_count"),
        "summary": mapping_dict.get("summary", {}),
        "chunks": normalized_chunks
    }
    raw_str = json.dumps(raw_dict, sort_keys=True)
    return hashlib.sha256(raw_str.encode('utf-8')).hexdigest()

def load_and_validate_config(config_arg=None):
    """
    【R3-2核心机制】所有入口统一调用此函数加载并验证配置。
    验证配置存在性、格式正确性及 A 阶段门禁约束；若缺少或不合要求立即退出。
    """
    c_path = Path(config_arg) if config_arg else DEFAULT_CONFIG
    if not c_path.exists():
        print(f"[ERROR] 配置文件未找到: {c_path}", file=sys.stderr)
        sys.exit(EXIT_INPUT_ERROR)

    try:
        with open(c_path, 'r', encoding='utf-8') as f:
            conf = json.load(f)
    except Exception as e:
        print(f"[ERROR] 读取或解析配置文件失败: {e}", file=sys.stderr)
        sys.exit(EXIT_INPUT_ERROR)

    allow_net = conf.get("runtime", {}).get("allow_network_requests", True)
    if allow_net:
        print("[ERROR] A阶段配置必须保持 allow_network_requests = false，禁止外部计费调用！", file=sys.stderr)
        sys.exit(EXIT_ENVIRONMENT_ERROR)

    return conf

def resolve_paths_from_config(config):
    """从配置中提取实际运行路径与参数"""
    paths_cfg = config.get("paths", {})
    manifest_p = SCRIPT_DIR / paths_cfg.get("input_manifest", "input_manifest.json")
    chunks_map_p = SCRIPT_DIR / paths_cfg.get("chunks_mapping_file", "storage/chunks/chunks_mapping.json")
    sparse_idx_p = SCRIPT_DIR / paths_cfg.get("sparse_index_dir", "storage/sparse_index")
    k_cand = config.get("retrieval", {}).get("k_candidates", 3)
    return {
        "manifest_path": manifest_p,
        "chunks_mapping_path": chunks_map_p,
        "sparse_index_dir": sparse_idx_p,
        "k_candidates": k_cand
    }

def check_environment():
    """核验当前虚拟环境、已安装库与Windows兼容性"""
    report = {
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "packages": {}
    }

    try:
        import paperqa
        report["packages"]["paperqa"] = paperqa.__version__
    except ImportError as e:
        report["packages"]["paperqa"] = f"MISSING ({e})"

    try:
        import tantivy
        report["packages"]["tantivy"] = getattr(tantivy, '__version__', 'installed')
    except ImportError as e:
        report["packages"]["tantivy"] = f"MISSING ({e})"

    try:
        import pypdfium2 as pdfium
        report["packages"]["pypdfium2"] = getattr(pdfium, '__version__', 'installed')
    except ImportError as e:
        report["packages"]["pypdfium2"] = f"MISSING ({e})"

    try:
        import pypdf
        report["packages"]["pypdf"] = getattr(pypdf, '__version__', 'installed')
    except ImportError as e:
        report["packages"]["pypdf"] = f"MISSING ({e})"

    try:
        import litellm
        report["packages"]["litellm"] = getattr(litellm, '__version__', 'installed')
    except ImportError as e:
        report["packages"]["litellm"] = f"MISSING ({e})"

    return report

def validate_input_corpus_common(manifest_path):
    """
    【R3-1核心机制】公共输入语料底层强校验流程。
    被 prepare 和 offline-check 共同调用：
    1. 必须有有效清单，非空且为列表；
    2. 校验每个条目的 paper_id 唯一性；
    3. 实际核验 PDF 存在性、物理字节数与清单声明值完全一致、实际计算 SHA256 与清单完全一致；
    4. 返回 manifest 数据与已校验的 paper_docs 字典；遇到任何异常立即返回错误码。
    """
    m_path = Path(manifest_path)
    if not m_path.exists():
        print(f"[ERROR] 清单文件不存在: {m_path}", file=sys.stderr)
        return False, EXIT_INPUT_ERROR, None, "清单文件不存在"

    try:
        with open(m_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)
    except Exception as e:
        print(f"[ERROR] 清单 JSON 解析失败: {e}", file=sys.stderr)
        return False, EXIT_INPUT_ERROR, None, "清单JSON解析失败"

    if not isinstance(manifest, list) or len(manifest) == 0:
        print("[ERROR] 输入清单为空 (无有效输入文献)，拒绝执行！", file=sys.stderr)
        return False, EXIT_INPUT_ERROR, None, "输入清单为空"

    import pypdfium2 as pdfium

    seen_pids = set()
    verified_docs = {}

    for item in manifest:
        pid = item.get("paper_id")
        if not pid or pid in seen_pids:
            print(f"[ERROR] 存在无效或重复的论文 ID: {pid}", file=sys.stderr)
            return False, EXIT_INTEGRITY_ERROR, None, f"重复或无效的论文ID: {pid}"
        seen_pids.add(pid)

        pdf_path = Path(item.get("local_pdf_absolute", ""))
        if not pdf_path.exists():
            pdf_path = SCRIPT_DIR / item.get("local_pdf_relative", "")
        if not pdf_path.exists():
            print(f"[ERROR] 输入 PDF 文件不存在: {pdf_path}", file=sys.stderr)
            return False, EXIT_INTEGRITY_ERROR, None, f"PDF文件不存在: {pdf_path}"

        actual_bytes = pdf_path.stat().st_size
        declared_bytes = item.get("file_bytes")
        if declared_bytes is not None and actual_bytes != declared_bytes:
            print(f"[ERROR] PDF 文件大小不匹配: {pid} (实际 {actual_bytes} != 声明 {declared_bytes})", file=sys.stderr)
            return False, EXIT_INTEGRITY_ERROR, None, "文件大小不匹配"

        actual_sha = compute_sha256(pdf_path)
        declared_sha = item.get("sha256")
        if declared_sha and actual_sha != declared_sha:
            print(f"[ERROR] PDF 哈希不匹配: {pid} (实际 {actual_sha} != 声明 {declared_sha})", file=sys.stderr)
            return False, EXIT_INTEGRITY_ERROR, None, "PDF哈希不匹配"

        # 打开 PDF 检查是否损坏并获取页数
        try:
            doc = pdfium.PdfDocument(str(pdf_path))
            verified_docs[pid] = {
                "manifest_item": item,
                "pdf_path": pdf_path,
                "page_count": len(doc),
                "actual_sha": actual_sha
            }
            doc.close()
        except Exception as e:
            print(f"[ERROR] PDF 文件损坏或无法被 pdfium 解析: {pdf_path} ({e})", file=sys.stderr)
            return False, EXIT_INTEGRITY_ERROR, None, f"PDF损坏: {e}"

    return True, EXIT_SUCCESS, (manifest, verified_docs), "全部源文件核验通过"

def run_preflight(config_path):
    """1. preflight 入口: 加载并核验配置模板及脱敏能力清单"""
    print("==================================================================")
    print("  T003A PaperQA2 离线预检程序 (Preflight Gate)")
    print("==================================================================")

    config = load_and_validate_config(config_path)

    env_info = check_environment()
    print(f"[环境检查] Python 解释器: {env_info['python_executable']}")
    print(f"[环境检查] Python 版本: {env_info['python_version']} ({env_info['platform']})")

    all_pkgs_ok = True
    for pkg, ver in env_info["packages"].items():
        if "MISSING" in ver:
            print(f"  [FAIL] 缺失关键依赖包: {pkg} -> {ver}", file=sys.stderr)
            all_pkgs_ok = False
        else:
            print(f"  [PASS] 依赖包就绪: {pkg:12} == {ver}")

    if not all_pkgs_ok:
        print("[ERROR] 依赖环境不完整，请激活 T003 虚拟环境并安装 requirements_locked.txt", file=sys.stderr)
        return EXIT_ENVIRONMENT_ERROR

    print("  [PASS] 离线安全门禁: allow_network_requests = False (零外部模型计费请求)")

    if CAPABILITY_REPORT.exists():
        with open(CAPABILITY_REPORT, 'r', encoding='utf-8') as f:
            cap = json.load(f)
        sources = cap.get('observed_configuration_sources', [])
        print(f"  [PASS] 接口脱敏清单: 已真实记录 {len(sources)} 处配置文件来源 (待明确调用方式及范围)")
    else:
        print("  [WARN] 未检测到 capability_report.json，将仅报告基础环境")

    print("\n[SUCCESS] Preflight 检查全部通过！离线运行与依赖环境符合 T003A 规范。")
    return EXIT_SUCCESS

def run_prepare(manifest_arg=None, config_arg=None):
    """2. prepare 入口: 统一前置校验 -> 切块 -> 建立映射指纹 -> 清空旧索引"""
    print("==================================================================")
    print("  T003A 资料切分与稳定映射程序 (Prepare Chunks & Map)")
    print("==================================================================")

    config = load_and_validate_config(config_arg)
    resolved = resolve_paths_from_config(config)

    m_path = Path(manifest_arg) if manifest_arg else resolved["manifest_path"]
    ok, code, data, msg = validate_input_corpus_common(m_path)
    if not ok:
        return code

    manifest, verified_docs = data
    chunks_map_p = resolved["chunks_mapping_path"]
    sparse_idx_p = resolved["sparse_index_dir"]

    chunks_map_p.parent.mkdir(parents=True, exist_ok=True)

    import pypdfium2 as pdfium

    all_chunks = []
    chunk_summary = {}
    manifest_fp = compute_manifest_fingerprint(manifest)

    sorted_manifest = sorted(manifest, key=lambda x: str(x.get("paper_id", "")))

    for item in sorted_manifest:
        pid = item["paper_id"]
        pdf_info = verified_docs[pid]
        pdf_path = pdf_info["pdf_path"]
        actual_sha = pdf_info["actual_sha"]

        doc = pdfium.PdfDocument(str(pdf_path))
        doc_chunks = []
        page_count = len(doc)

        for p_idx in range(page_count):
            page = doc[p_idx]
            phys_page = p_idx + 1
            raw_text = page.get_textpage().get_text_range()

            cleaned_text = raw_text.strip()
            paragraphs = [p.strip() for p in re.split(r'\n\s*\n', cleaned_text) if p.strip()]
            if not paragraphs:
                paragraphs = [cleaned_text] if cleaned_text else []

            char_offset = 0
            for para_idx, para in enumerate(paragraphs):
                p_len = len(para)
                chunk_id = f"{pid}_P{phys_page:02d}_C{para_idx+1:03d}"
                chunk_hash = hashlib.sha256(para.encode('utf-8')).hexdigest()[:16]

                chunk_entry = {
                    "chunk_id": chunk_id,
                    "paper_id": pid,
                    "raw_id": item.get("raw_id"),
                    "zotero_item_key": item.get("zotero_item_key"),
                    "pdf_sha256": actual_sha,
                    "physical_page": phys_page,
                    "printed_page": None,
                    "char_start_in_page": char_offset,
                    "char_end_in_page": char_offset + p_len,
                    "chunk_char_count": p_len,
                    "chunk_hash": chunk_hash,
                    "content": para
                }
                doc_chunks.append(chunk_entry)
                all_chunks.append(chunk_entry)
                char_offset += p_len + 2

        doc.close()
        chunk_summary[pid] = {
            "total_physical_pages": page_count,
            "total_chunks": len(doc_chunks),
            "total_characters": sum(c["chunk_char_count"] for c in doc_chunks)
        }
        print(f"  [OK] {pid}: 提取 {page_count} 物理页, 生成 {len(doc_chunks)} 个带坐标溯源片段")

    active_pids = sorted(list(verified_docs.keys()))
    mapping_payload = {
        "mapping_version": "1.3.0",
        "manifest_fingerprint": manifest_fp,
        "chunking_strategy": "physical_page_with_paragraphs",
        "active_paper_ids": active_pids,
        "summary": chunk_summary,
        "chunks_count": len(all_chunks),
        "chunks": all_chunks
    }
    mapping_fp = compute_mapping_fingerprint(mapping_payload)
    mapping_payload["mapping_fingerprint"] = mapping_fp

    with open(chunks_map_p, 'w', encoding='utf-8') as f:
        json.dump(mapping_payload, f, ensure_ascii=False, indent=2)

    # 只要生成新映射，立即清空旧索引
    if sparse_idx_p.exists():
        shutil.rmtree(sparse_idx_p, ignore_errors=True)
        print("  [INFO] 已清空旧索引目录，保证后续检索与当前资料版本严格一致。")

    print(f"\n[SUCCESS] 成功生成稳定分块映射文件: {chunks_map_p} (总计 {len(all_chunks)} 个片段)")
    return EXIT_SUCCESS

def build_or_get_tantivy_index(mapping, sparse_idx_dir, recomputed_mapping_fp, force_rebuild=False):
    """
    构建或加载本地 Tantivy 索引，强绑定由 mapping 实时计算的 recomputed_mapping_fp。
    若缓存的 mapping_fingerprint.fp 与 recomputed_mapping_fp 不一致，强制清空并重建！
    """
    import tantivy

    sparse_idx_dir.mkdir(parents=True, exist_ok=True)
    fp_file = sparse_idx_dir / "mapping_fingerprint.fp"

    can_reuse = False
    if not force_rebuild and fp_file.exists():
        cached_fp = fp_file.read_text(encoding='utf-8').strip()
        if cached_fp == recomputed_mapping_fp:
            try:
                schema_builder = tantivy.SchemaBuilder()
                schema_builder.add_text_field("chunk_id", stored=True)
                schema_builder.add_text_field("paper_id", stored=True)
                schema_builder.add_integer_field("physical_page", stored=True)
                schema_builder.add_text_field("content", stored=True)
                schema = schema_builder.build()
                index = tantivy.Index(schema, path=str(sparse_idx_dir))
                searcher = index.searcher()
                if searcher.num_docs == len(mapping.get("chunks", [])):
                    can_reuse = True
                    return index, f"复用现有索引 (映射指纹完全一致，包含 {searcher.num_docs} 个片段文档)"
            except Exception:
                pass

    # 必须重建索引
    shutil.rmtree(sparse_idx_dir, ignore_errors=True)
    sparse_idx_dir.mkdir(parents=True, exist_ok=True)

    schema_builder = tantivy.SchemaBuilder()
    schema_builder.add_text_field("chunk_id", stored=True)
    schema_builder.add_text_field("paper_id", stored=True)
    schema_builder.add_integer_field("physical_page", stored=True)
    schema_builder.add_text_field("content", stored=True)
    schema = schema_builder.build()

    index = tantivy.Index(schema, path=str(sparse_idx_dir))
    writer = index.writer(heap_size=32_000_000)
    for c in mapping.get("chunks", []):
        doc = tantivy.Document(
            chunk_id=[c["chunk_id"]],
            paper_id=[c["paper_id"]],
            physical_page=[c["physical_page"]],
            content=[c["content"]]
        )
        writer.add_document(doc)
    writer.commit()
    index.reload()
    fp_file.write_text(recomputed_mapping_fp, encoding='utf-8')

    return index, f"成功重建 Tantivy 本地稀疏索引 (已索引 {len(mapping['chunks'])} 个文档片段，绑定映射指纹 {recomputed_mapping_fp[:8]})"

async def test_paperqa2_native_sparse_bridge(chunks):
    """调用 PaperQA2 官方 Docs.aadd_texts 与 SparseEmbeddingModel 并严格断言"""
    from paperqa.docs import Docs
    from paperqa.types import Doc, Text
    from paperqa.llms import SparseEmbeddingModel

    print("[PaperQA2 官方受控接口实测] 正在通过 Docs.aadd_texts 添加受控文献片段...")
    docs = Docs()
    sparse_model = SparseEmbeddingModel()

    sample_texts = []
    for c in chunks[:4]:
        doc_obj = Doc(
            docname=c["paper_id"],
            citation=f"{c['paper_id']} P.{c['physical_page']}",
            dockey=f"dockey_{c['paper_id']}"
        )
        t = Text(
            text=c["content"],
            name=c["chunk_id"],
            doc=doc_obj
        )
        sample_texts.append(t)

    add_success = await docs.aadd_texts(sample_texts, doc=sample_texts[0].doc, embedding_model=sparse_model)
    if not add_success:
        return False, "Docs.aadd_texts 返回 False"

    retrieved = await docs.retrieve_texts("批判性思维", k=2, embedding_model=sparse_model)
    if not retrieved or len(retrieved) == 0:
        return False, "retrieve_texts 返回空列表"

    valid_names = {t.name for t in sample_texts}
    for m in retrieved:
        if m.name not in valid_names:
            return False, f"retrieve_texts 命中了未添加的片段: {m.name}"

    return True, f"成功通过 Docs.aadd_texts 添加 {len(docs.texts)} 个受控片段，并通过 retrieve_texts 检索命中 {len(retrieved)} 条 (来源校验无误)"

def run_offline_check(config_path=None):
    """
    3. offline-check 入口:
    【R3-1/R3-2核心机制】
    - 统一加载并核验配置；
    - 真实执行源 PDF 底层核验（缺失、损坏、大小不符均非零退出）；
    - 严格核验映射与输入清单的论文集合相等性（缺失整篇文献立即退出）；
    - 严格核验 chunks_count 与 summary 元数据的一致性；
    - 逐一核验 15 片段的 raw_id、zotero_item_key、出处、页码、起止切片及内容哈希；
    - 实时重新计算映射指纹；当合法切片变动时自动重新同步并重建索引；
    - 执行样本事实、真实页码及来源片段严格断言。
    """
    print("==================================================================")
    print("  T003A 离线检索路径与异常处理实测 (Offline Check)")
    print("==================================================================")

    config = load_and_validate_config(config_path)
    resolved = resolve_paths_from_config(config)

    m_path = resolved["manifest_path"]
    chunks_map_p = resolved["chunks_mapping_path"]
    sparse_idx_p = resolved["sparse_index_dir"]
    k_cand = resolved["k_candidates"]

    # 1. 深度核验源文件与输入清单
    ok, code, data, msg = validate_input_corpus_common(m_path)
    if not ok:
        print(f"[ERROR] 离线检查前置源文件核验未通过: {msg}", file=sys.stderr)
        return code

    manifest, verified_docs = data
    current_manifest_fp = compute_manifest_fingerprint(manifest)
    manifest_pids = set(verified_docs.keys())

    # 2. 检查或自动触发准备切块数据
    if not chunks_map_p.exists():
        print("[INFO] 正在自动触发 prepare 生成切块数据...")
        res = run_prepare(manifest_arg=m_path, config_arg=config_path)
        if res != EXIT_SUCCESS:
            return res

    try:
        with open(chunks_map_p, 'r', encoding='utf-8') as f:
            mapping = json.load(f)
    except Exception as e:
        print(f"[ERROR] 读取映射文件失败: {e}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    # 清单变动检测：若清单指纹不匹配，重新执行准备
    if mapping.get("manifest_fingerprint") != current_manifest_fp:
        print("[INFO] 检测到清单变更导致指纹不匹配，重新执行资料准备...", file=sys.stderr)
        res = run_prepare(manifest_arg=m_path, config_arg=config_path)
        if res != EXIT_SUCCESS:
            return res
        with open(chunks_map_p, 'r', encoding='utf-8') as f:
            mapping = json.load(f)

    # 3. 【R3-1核心核验：论文集合相等性与完整性】
    mapping_pids = set(mapping.get("active_paper_ids", []))
    if mapping_pids != manifest_pids:
        print(f"[ERROR] 映射中的活跃论文集合与输入清单不一致 (存在整篇缺失或多余): 映射={mapping_pids}, 清单={manifest_pids}", file=sys.stderr)
        return EXIT_INTEGRITY_ERROR

    # 4. 【R3-1/R4核心核验：chunks_count、summary一致性与源PDF非空页覆盖完整性】
    chunks = mapping.get("chunks", [])
    declared_count = mapping.get("chunks_count")
    if declared_count is None or declared_count != len(chunks):
        print(f"[ERROR] 映射中声明的 chunks_count ({declared_count}) 与实际片段列表数量 ({len(chunks)}) 不一致！", file=sys.stderr)
        return EXIT_INTEGRITY_ERROR

    summary = mapping.get("summary", {})
    if set(summary.keys()) != manifest_pids:
        print(f"[ERROR] 映射 summary 键与活跃论文集合不匹配！", file=sys.stderr)
        return EXIT_INTEGRITY_ERROR

    import pypdfium2 as pdfium

    for pid in manifest_pids:
        pid_chunks = [c for c in chunks if c.get("paper_id") == pid]
        if len(pid_chunks) == 0:
            print(f"[ERROR] 活跃论文 {pid} 在映射中没有任何片段！", file=sys.stderr)
            return EXIT_INTEGRITY_ERROR
        sum_info = summary.get(pid, {})
        if sum_info.get("total_chunks") != len(pid_chunks):
            print(f"[ERROR] summary[{pid}].total_chunks ({sum_info.get('total_chunks')}) 与实际片段数 ({len(pid_chunks)}) 不一致！", file=sys.stderr)
            return EXIT_INTEGRITY_ERROR
        if sum_info.get("total_physical_pages") != verified_docs[pid]["page_count"]:
            print(f"[ERROR] summary[{pid}].total_physical_pages 与 PDF 真实物理页数不符！", file=sys.stderr)
            return EXIT_INTEGRITY_ERROR
        actual_total_chars = sum(c.get("chunk_char_count", 0) for c in pid_chunks)
        sum_info["total_characters"] = actual_total_chars

        # 【R4唯一收尾核心：从源PDF推导非空物理页集合，核查映射是否完整覆盖】
        # 打开源 PDF，计算所有实际包含有效字符正文的非空物理页（1-indexed）
        pdf_path = verified_docs[pid]["pdf_path"]
        doc_tmp = pdfium.PdfDocument(str(pdf_path))
        expected_nonempty_pages = set()
        for p_idx in range(len(doc_tmp)):
            page_text = doc_tmp[p_idx].get_textpage().get_text_range()
            if page_text and page_text.strip():
                expected_nonempty_pages.add(p_idx + 1)
        doc_tmp.close()

        # 收集映射中该论文覆盖的物理页集合
        covered_pages = {c.get("physical_page") for c in pid_chunks if c.get("physical_page") is not None}
        missing_pages = expected_nonempty_pages - covered_pages
        if missing_pages:
            print(f"[ERROR] 论文 {pid} 在映射中缺失应有的非空正文物理页: {sorted(list(missing_pages))} (源PDF有正文但映射未覆盖)！", file=sys.stderr)
            return EXIT_INTEGRITY_ERROR

    # 5. 【R3-1核心核验：片段来源身份、出处与切片严格物理吻合】
    import pypdfium2 as pdfium

    opened_docs = {}
    for pid in manifest_pids:
        pdf_info = verified_docs[pid]
        opened_docs[pid] = (pdfium.PdfDocument(str(pdf_info["pdf_path"])), pdf_info["actual_sha"], pdf_info["page_count"])

    seen_cids = set()
    try:
        for c in chunks:
            cid = c.get("chunk_id")
            if not cid or cid in seen_cids:
                print(f"[ERROR] 存在无效或重复的片段 ID: {cid}", file=sys.stderr)
                return EXIT_INTEGRITY_ERROR
            seen_cids.add(cid)

            c_pid = c.get("paper_id")
            if c_pid not in opened_docs:
                print(f"[ERROR] 片段包含未知论文 ID: {c_pid}", file=sys.stderr)
                return EXIT_INTEGRITY_ERROR

            manifest_item = verified_docs[c_pid]["manifest_item"]

            # 【R3-1身份核查：RawID 与 Zotero Key 严格对应】
            if c.get("raw_id") != manifest_item.get("raw_id"):
                print(f"[ERROR] 片段 raw_id 与清单声明不一致: {cid} (实际 {c.get('raw_id')} != 预期 {manifest_item.get('raw_id')})", file=sys.stderr)
                return EXIT_INTEGRITY_ERROR

            if c.get("zotero_item_key") != manifest_item.get("zotero_item_key"):
                print(f"[ERROR] 片段 zotero_item_key 与清单声明不一致: {cid} (实际 {c.get('zotero_item_key')} != 预期 {manifest_item.get('zotero_item_key')})", file=sys.stderr)
                return EXIT_INTEGRITY_ERROR

            doc, actual_sha, page_count = opened_docs[c_pid]

            # 校验哈希
            if c.get("pdf_sha256") != actual_sha:
                print(f"[ERROR] 片段记录的 pdf_sha256 与源 PDF 实际哈希不符: {cid}", file=sys.stderr)
                return EXIT_INTEGRITY_ERROR

            # 校验物理页范围
            p_num = c.get("physical_page")
            if not isinstance(p_num, int) or p_num < 1 or p_num > page_count:
                print(f"[ERROR] 片段物理页码越界: {cid} (页码 {p_num}, 文档总页数 {page_count})", file=sys.stderr)
                return EXIT_INTEGRITY_ERROR

            # 校验原文起止区间与切片内容
            start_off = c.get("char_start_in_page")
            end_off = c.get("char_end_in_page")
            raw_text = doc[p_num - 1].get_textpage().get_text_range()

            if not isinstance(start_off, int) or not isinstance(end_off, int) or start_off < 0 or end_off > len(raw_text) or start_off > end_off:
                print(f"[ERROR] 片段原文起止偏移无效: {cid} ({start_off} - {end_off}, 单页实际长度 {len(raw_text)})", file=sys.stderr)
                return EXIT_INTEGRITY_ERROR

            page_slice = raw_text[start_off:end_off]
            if page_slice != c.get("content"):
                print(f"[ERROR] 片段原文切片与声明内容不吻合: {cid}", file=sys.stderr)
                return EXIT_INTEGRITY_ERROR

            if c.get("chunk_char_count") != len(c.get("content", "")):
                print(f"[ERROR] 片段字符长度字段与内容实际长度不符: {cid}", file=sys.stderr)
                return EXIT_INTEGRITY_ERROR

            # 校验内容哈希
            actual_content_hash = hashlib.sha256(c["content"].encode('utf-8')).hexdigest()[:16]
            if c.get("chunk_hash") != actual_content_hash:
                print(f"[ERROR] 片段内容哈希被篡改: {cid}", file=sys.stderr)
                return EXIT_INTEGRITY_ERROR
    finally:
        for doc, _, _ in opened_docs.values():
            doc.close()

    # 6. 【R3-1核心机制：从当前实际映射重算指纹并同步索引缓存】
    recomputed_mapping_fp = compute_mapping_fingerprint(mapping)

    index, msg = build_or_get_tantivy_index(mapping, sparse_idx_p, recomputed_mapping_fp)
    if not index:
        print(f"[ERROR] Tantivy 索引创建失败: {msg}", file=sys.stderr)
        return EXIT_RETRIEVAL_ERROR

    print(f"  [PASS] 本地稀疏索引检查: {msg}")

    # 7. 严格断言样本事实、预期论文ID、真实页码及来源片段
    searcher = index.searcher()
    test_queries = []
    if "PAPER_92645125" in manifest_pids:
        test_queries.append(("批判性思维", "PAPER_92645125", 5, "PAPER_92645125_P05_C001", "自主学习能力"))
    if "PAPER_cdaf1e55" in manifest_pids:
        test_queries.append(("自我调节学习", "PAPER_cdaf1e55", 1, "PAPER_cdaf1e55_P01_C001", "朱俊华"))

    for q_text, exp_pid, exp_page, exp_chunk_id, fact_keyword in test_queries:
        query = index.parse_query(q_text, ["content"])
        results = searcher.search(query, k_cand)
        if len(results.hits) == 0:
            print(f"[ERROR] 离线检索未命中任何片段: '{q_text}'", file=sys.stderr)
            return EXIT_RETRIEVAL_ERROR

        top_score, doc_address = results.hits[0]
        top_doc = searcher.doc(doc_address)
        chunk_id = top_doc["chunk_id"][0]
        found_pid = top_doc["paper_id"][0]
        phys_page = top_doc["physical_page"][0]
        content_text = top_doc["content"][0]

        if found_pid != exp_pid:
            print(f"[ERROR] 检索命中论文与预期不匹配: 期望 {exp_pid}, 实际命中 {found_pid}！", file=sys.stderr)
            return EXIT_RETRIEVAL_ERROR
        if phys_page != exp_page:
            print(f"[ERROR] 检索命中物理页码与预期不匹配: 期望 P.{exp_page}, 实际命中 P.{phys_page}！", file=sys.stderr)
            return EXIT_RETRIEVAL_ERROR
        if chunk_id != exp_chunk_id:
            print(f"[ERROR] 检索命中片段ID与预期不匹配: 期望 {exp_chunk_id}, 实际命中 {chunk_id}！", file=sys.stderr)
            return EXIT_RETRIEVAL_ERROR
        if fact_keyword not in content_text:
            print(f"[ERROR] 检索命中片段内容缺少关键事实关键词: '{fact_keyword}'", file=sys.stderr)
            return EXIT_RETRIEVAL_ERROR

        snippet = content_text[:60].replace("\n", " ")
        print(f"  [PASS] 事实定位检索 '{q_text}': Top-1 命中片段 [{chunk_id}] (物理P.{phys_page}), 匹配文献: {found_pid}, 评分: {top_score:.3f}")
        print(f"         包含样本事实关键词 '{fact_keyword}', 引文片段: \"{snippet}...\"")

    # 8. PaperQA2 官方公开受控接口添加与检索断言
    try:
        pqa_ok, pqa_msg = asyncio.run(test_paperqa2_native_sparse_bridge(chunks))
        if not pqa_ok:
            print(f"[ERROR] PaperQA2 官方受控接口测试失败: {pqa_msg}", file=sys.stderr)
            return EXIT_RETRIEVAL_ERROR
        print(f"  [PASS] PaperQA2 官方接口实测: {pqa_msg}")
    except Exception as e:
        print(f"[ERROR] PaperQA2 官方受控接口异常: {e}", file=sys.stderr)
        return EXIT_RETRIEVAL_ERROR

    print("\n[SUCCESS] 离线无网络检索实测全部通过！具备可靠的本地片段定位与坐标溯源能力。")
    return EXIT_SUCCESS

def main():
    parser = argparse.ArgumentParser(description="T003 PaperQA2 Bridge & Rigorous Offline Runner")
    parser.add_argument("--config", dest="top_config", default=None, help="全局配置文件路径")
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # preflight
    p_preflight = subparsers.add_parser("preflight", help="预检环境与脱敏能力清单")
    p_preflight.add_argument("--config", dest="sub_config", default=None, help="配置文件路径")

    # prepare
    p_prepare = subparsers.add_parser("prepare", help="核验输入并生成分块资料映射")
    p_prepare.add_argument("--manifest", default=None, help="输入清单路径")
    p_prepare.add_argument("--config", dest="sub_config", default=None, help="配置文件路径")

    # offline-check
    p_offline = subparsers.add_parser("offline-check", help="离线检索与异常处理测试")
    p_offline.add_argument("--config", dest="sub_config", default=None, help="配置文件路径")

    args = parser.parse_args()

    # 【R3-2核心修复】：子命令有指定则用子命令的配置；子命令未指定则继承顶层配置；均未指定使用默认配置
    chosen_config = getattr(args, "sub_config", None) or getattr(args, "top_config", None) or str(DEFAULT_CONFIG)

    if not args.command:
        # 默认执行全量阶段 A 流水线
        print("未指定子命令，执行全量阶段 A 流水线 (preflight -> prepare -> offline-check)...\n")
        r1 = run_preflight(chosen_config)
        if r1 != EXIT_SUCCESS:
            sys.exit(r1)
        r2 = run_prepare(config_arg=chosen_config)
        if r2 != EXIT_SUCCESS:
            sys.exit(r2)
        r3 = run_offline_check(chosen_config)
        sys.exit(r3)

    if args.command == "preflight":
        sys.exit(run_preflight(chosen_config))
    elif args.command == "prepare":
        sys.exit(run_prepare(manifest_arg=args.manifest, config_arg=chosen_config))
    elif args.command == "offline-check":
        sys.exit(run_offline_check(config_path=chosen_config))

if __name__ == "__main__":
    main()
