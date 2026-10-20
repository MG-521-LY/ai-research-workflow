#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
T003A 独立回归测试套件 (test_offline_bridge.py - R4 完全收尾达标版)
在临时副本中模拟运行 31 项正常与异常场景：
- 原 11 项基础测试场景 (baseline, repeat, missing_pdf, corrupt_pdf, wrong_size, restricted, wrong_pid, empty 等)
- R2 异常场景：单独 offline-check 下源文件缺失、损坏、物理页越界、偏移无效、哈希篡改、虚构内容追加的强拒绝
- R3 收尾：RawID 身份篡改、Zotero Key 错配、整篇文献被删减、chunks_count 错标的强拦截 (退出 2)
- R3 收尾：合法切片微调后的缓存自动失效与索引重建核验 (退出 0 且索引内容同步新切片)
- R3 收尾：前置/后置 --config 参数一致生效核验 (prefix/suffix unsafe config 均退出 3)
- R4 唯一收尾：删除源PDF非空物理页且同步修改统计元数据的强拦截 (missing_nonempty_page 退出 2)
- R4 唯一收尾：重复 chunk_id 篡改的强拦截 (duplicate_chunk_id 退出 2)
"""

import sys
import os
import shutil
import tempfile
import json
import subprocess
import hashlib
import gc
from pathlib import Path

SOURCE = Path(r"E:\AI\AI_reserch\deliveries\T003_paperqa_bridge")
VENV_PYTHON = str(SOURCE / ".venv" / "Scripts" / "python.exe")

def read_json(p):
    return json.loads(p.read_text(encoding='utf-8'))

def write_json(p, data):
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def run_cmd(folder, *args):
    cmd = [VENV_PYTHON, str(folder / "bridge_runner.py"), *args]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    p = subprocess.run(cmd, env=env, capture_output=True, encoding='utf-8', errors='replace', timeout=60)
    return p.returncode, p.stdout, p.stderr

def main():
    print("=== 开始执行 T003A R4 独立场景完整测试套件 ===")
    results = {}

    with tempfile.TemporaryDirectory(prefix="t003_r4_test_") as td:
        root = Path(td)
        base = root / "baseline"
        shutil.copytree(SOURCE, base, ignore=shutil.ignore_patterns(".venv", "storage", "__pycache__"))
        
        manifest = read_json(base / "input_manifest.json")
        for item in manifest:
            item["local_pdf_absolute"] = str(base / item["local_pdf_relative"])
        write_json(base / "input_manifest.json", manifest)

        # 1. baseline_fresh
        code, out, err = run_cmd(base)
        results["baseline_fresh"] = code

        # 2. repeat_full_pipeline
        code, out, err = run_cmd(base)
        results["repeat_full_pipeline"] = code

        def clone(name):
            folder = root / name
            shutil.copytree(base, folder)
            data = read_json(folder / "input_manifest.json")
            for item in data:
                item["local_pdf_absolute"] = str(folder / item["local_pdf_relative"])
            write_json(folder / "input_manifest.json", data)
            return folder, data

        # 3. missing_pdf_prepare & 4. missing_pdf_standalone_check
        f_miss, d_miss = clone("missing_pdf")
        (f_miss / d_miss[0]["local_pdf_relative"]).unlink()
        code, _, _ = run_cmd(f_miss, "prepare")
        results["missing_pdf_prepare"] = code
        code, _, _ = run_cmd(f_miss, "offline-check")
        results["missing_pdf_standalone_check"] = code

        # 5. corrupt_pdf_prepare & 6. corrupt_pdf_standalone_check
        f_corrupt, d_corrupt = clone("corrupt_pdf")
        with (f_corrupt / d_corrupt[0]["local_pdf_relative"]).open("ab") as s:
            s.write(b"CORRUPT")
        code, _, _ = run_cmd(f_corrupt, "prepare")
        results["corrupt_pdf_prepare"] = code
        code, _, _ = run_cmd(f_corrupt, "offline-check")
        results["corrupt_pdf_standalone_check"] = code

        # 7. wrong_recorded_size_prepare & 8. wrong_recorded_size_standalone_check
        f_size, d_size = clone("wrong_recorded_size")
        d_size[0]["file_bytes"] = 1
        write_json(f_size / "input_manifest.json", d_size)
        code, _, _ = run_cmd(f_size, "prepare")
        results["wrong_recorded_size_prepare"] = code
        code, _, _ = run_cmd(f_size, "offline-check")
        results["wrong_recorded_size_standalone_check"] = code

        # 9. empty_manifest_prepare & 10. empty_manifest_standalone_check
        f_empty, d_empty = clone("empty_manifest")
        write_json(f_empty / "input_manifest.json", [])
        code, _, _ = run_cmd(f_empty, "prepare")
        results["empty_manifest_prepare"] = code
        code, _, _ = run_cmd(f_empty, "offline-check")
        results["empty_manifest_offline_check"] = code

        # 11. restricted_corpus (仅保留第1篇，旧第2篇不残留在索引)
        f_rest, d_rest = clone("restricted_corpus")
        write_json(f_rest / "input_manifest.json", d_rest[:1])
        code, _, _ = run_cmd(f_rest, "offline-check")
        results["restricted_corpus_standalone_check"] = code

        # 12-16. 映射内容被恶意篡改的 5 项 offline-check 强拦截
        for kind in ["wrong_pid", "wrong_page", "wrong_offset", "wrong_sha", "wrong_content"]:
            f_k, _ = clone(kind)
            mp = f_k / "storage" / "chunks" / "chunks_mapping.json"
            m = read_json(mp)
            for c in m["chunks"]:
                if kind == "wrong_pid": c["paper_id"] = "REVIEW_WRONG"
                if kind == "wrong_page": c["physical_page"] = 99
                if kind == "wrong_offset": c["char_start_in_page"] = 999999; c["char_end_in_page"] = 1000000
                if kind == "wrong_sha": c["pdf_sha256"] = "0" * 64
                if kind == "wrong_content": c["content"] += " REVIEW_INVENTED_EVIDENCE"
            write_json(mp, m)
            shutil.rmtree(f_k / "storage" / "sparse_index", ignore_errors=True)
            code, _, _ = run_cmd(f_k, "offline-check")
            results[f"{kind}_check"] = code

        # 17. missing_config (preflight & standalone offline-check 均必须拒绝)
        f_noconf, _ = clone("missing_config")
        (f_noconf / "config_template.json").unlink()
        code, _, _ = run_cmd(f_noconf, "preflight")
        results["missing_config_preflight"] = code
        code, _, _ = run_cmd(f_noconf, "offline-check")
        results["missing_config_standalone_check"] = code

        # 18. unsafe_config (allow_network_requests=true 门禁)
        f_unsafe, _ = clone("unsafe_config")
        c_unsafe = read_json(f_unsafe / "config_template.json")
        c_unsafe["runtime"]["allow_network_requests"] = True
        write_json(f_unsafe / "config_template.json", c_unsafe)
        code, _, _ = run_cmd(f_unsafe, "preflight")
        results["unsafe_config_preflight"] = code
        code, _, _ = run_cmd(f_unsafe, "offline-check")
        results["unsafe_config_standalone_check"] = code

        # 19. custom_config_preflight (指定非默认配置文件生效)
        f_custom, _ = clone("custom_config")
        c_custom = read_json(f_custom / "config_template.json")
        c_custom["retrieval"]["k_candidates"] = 1
        c_custom["paths"]["sparse_index_dir"] = "alternate_index"
        write_json(f_custom / "custom.json", c_custom)
        code, _, _ = run_cmd(f_custom, "preflight", "--config", str(f_custom / "custom.json"))
        results["custom_config_preflight"] = code

        # 20. R3 前置 --config unsafe.json 门禁拦截测试
        code_pre, _, _ = run_cmd(f_unsafe, "--config", str(f_unsafe / "config_template.json"), "offline-check")
        results["prefix_unsafe_config"] = code_pre

        # 21. R3 后置 --config unsafe.json 门禁拦截测试
        code_suf, _, _ = run_cmd(f_unsafe, "offline-check", "--config", str(f_unsafe / "config_template.json"))
        results["suffix_unsafe_config"] = code_suf

        # 22. R3 RawID 身份篡改拦截 (binding_raw_id)
        f_rawid, _ = clone("binding_raw_id")
        mp_rawid = f_rawid / "storage" / "chunks" / "chunks_mapping.json"
        m_rawid = read_json(mp_rawid)
        m_rawid["chunks"][0]["raw_id"] = "OTHER_SOURCE"
        write_json(mp_rawid, m_rawid)
        code, _, _ = run_cmd(f_rawid, "offline-check")
        results["binding_raw_id"] = code

        # 23. R3 Zotero Key 篡改拦截 (binding_zotero_key)
        f_zkey, _ = clone("binding_zotero_key")
        mp_zkey = f_zkey / "storage" / "chunks" / "chunks_mapping.json"
        m_zkey = read_json(mp_zkey)
        m_zkey["chunks"][0]["zotero_item_key"] = "WRONGKEY"
        write_json(mp_zkey, m_zkey)
        code, _, _ = run_cmd(f_zkey, "offline-check")
        results["binding_zotero_key"] = code

        # 24. R3 整篇文献被删除拦截 (binding_dropped_document)
        f_drop, d_drop = clone("binding_dropped_document")
        mp_drop = f_drop / "storage" / "chunks" / "chunks_mapping.json"
        m_drop = read_json(mp_drop)
        m_drop["active_paper_ids"] = [d_drop[0]["paper_id"]]
        m_drop["chunks"] = [x for x in m_drop["chunks"] if x["paper_id"] == d_drop[0]["paper_id"]]
        m_drop["chunks_count"] = len(m_drop["chunks"])
        write_json(mp_drop, m_drop)
        code, _, _ = run_cmd(f_drop, "offline-check")
        results["binding_dropped_document"] = code

        # 25. R3 chunks_count 篡改拦截 (binding_incorrect_count)
        f_count, _ = clone("binding_incorrect_count")
        mp_count = f_count / "storage" / "chunks" / "chunks_mapping.json"
        m_count = read_json(mp_count)
        m_count["chunks_count"] = 9999
        write_json(mp_count, m_count)
        code, _, _ = run_cmd(f_count, "offline-check")
        results["binding_incorrect_count"] = code

        # 26. R3 合法切片调整后的索引自动同步 (binding_valid_slice_stale_index)
        f_sync, _ = clone("binding_valid_slice_stale_index")
        mp_sync = f_sync / "storage" / "chunks" / "chunks_mapping.json"
        m_sync = read_json(mp_sync)
        c_sync = m_sync["chunks"][1]
        old_text = c_sync["content"]
        c_sync["char_start_in_page"] += 1
        c_sync["char_end_in_page"] -= 1
        c_sync["content"] = old_text[1:-1]
        c_sync["chunk_hash"] = hashlib.sha256(c_sync["content"].encode()).hexdigest()[:16]
        c_sync["chunk_char_count"] = len(c_sync["content"])
        write_json(mp_sync, m_sync)
        code_sync, _, _ = run_cmd(f_sync, "offline-check")
        results["binding_valid_slice_stale_index"] = code_sync

        import tantivy
        idx = tantivy.Index.open(str(f_sync / "storage" / "sparse_index"))
        searcher = idx.searcher()
        entries = [searcher.doc(a) for _, a in searcher.search(tantivy.Query.all_query(), 100).hits]
        hit = next(x for x in entries if x["chunk_id"][0] == c_sync["chunk_id"])
        is_synced = (hit["content"][0] == c_sync["content"])
        results["valid_slice_index_synced"] = 0 if is_synced else 1
        del hit, entries, searcher, idx
        gc.collect()

        # 27. R4 唯一收尾核心：删除源PDF非空物理页并同步修改统计 (missing_nonempty_page)
        f_page, _ = clone("missing_nonempty_page")
        mp_page = f_page / "storage" / "chunks" / "chunks_mapping.json"
        m_page = read_json(mp_page)
        removed = m_page["chunks"].pop(1)
        pid = removed["paper_id"]
        m_page["chunks_count"] = len(m_page["chunks"])
        m_page["summary"][pid]["total_chunks"] -= 1
        m_page["summary"][pid]["total_characters"] -= removed["chunk_char_count"]
        write_json(mp_page, m_page)
        code, _, _ = run_cmd(f_page, "offline-check")
        results["missing_nonempty_page"] = code

        # 28. R4 重复 chunk_id 篡改拦截 (duplicate_chunk_id)
        f_dupid, _ = clone("duplicate_chunk_id")
        mp_dupid = f_dupid / "storage" / "chunks" / "chunks_mapping.json"
        m_dupid = read_json(mp_dupid)
        m_dupid["chunks"][1]["chunk_id"] = m_dupid["chunks"][0]["chunk_id"]
        write_json(mp_dupid, m_dupid)
        code, _, _ = run_cmd(f_dupid, "offline-check")
        results["duplicate_chunk_id"] = code

    expected = {
        "baseline_fresh": 0,
        "repeat_full_pipeline": 0,
        "missing_pdf_prepare": 2,
        "missing_pdf_standalone_check": 2,
        "corrupt_pdf_prepare": 2,
        "corrupt_pdf_standalone_check": 2,
        "wrong_recorded_size_prepare": 2,
        "wrong_recorded_size_standalone_check": 2,
        "empty_manifest_prepare": 1,
        "empty_manifest_offline_check": 1,
        "restricted_corpus_standalone_check": 0,
        "wrong_pid_check": 2,
        "wrong_page_check": 2,
        "wrong_offset_check": 2,
        "wrong_sha_check": 2,
        "wrong_content_check": 2,
        "missing_config_preflight": 1,
        "missing_config_standalone_check": 1,
        "unsafe_config_preflight": 3,
        "unsafe_config_standalone_check": 3,
        "custom_config_preflight": 0,
        "prefix_unsafe_config": 3,
        "suffix_unsafe_config": 3,
        "binding_raw_id": 2,
        "binding_zotero_key": 2,
        "binding_dropped_document": 2,
        "binding_incorrect_count": 2,
        "binding_valid_slice_stale_index": 0,
        "valid_slice_index_synced": 0,
        "missing_nonempty_page": 2,
        "duplicate_chunk_id": 2
    }

    all_passed = True
    for k, exp in expected.items():
        actual = results.get(k)
        if actual != exp:
            print(f"[FAIL] {k}: actual {actual} != expected {exp}")
            all_passed = False
        else:
            print(f"[PASS] {k}: {actual}")

    if all_passed:
        print(f"\n>>> 全部 {len(expected)} 项独立测试场景 100% 通过！<<<")
        sys.exit(0)
    else:
        print("\n>>> 存在未通过场景！<<<", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
