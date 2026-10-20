#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
T004 批量下载与 Zotero 自动衔接隔离测试套件 (T004 Test Suite)
任务编号: T004-DOWNLOAD-ZOTERO-BRIDGE
执行角色: DeepSeek Harness (dsh)
"""

import os
import sys
import json
import shutil
import tempfile
import subprocess
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

ROOT = Path(r"E:\AI\AI_reserch")
DELIVERY_T004 = ROOT / "deliveries" / "T004_download_zotero_bridge"
PROGRAM = DELIVERY_T004 / "pipeline_runner.py"

class MockServer:
    def __init__(self, response_provider=None, status=201):
        self.posts = 0
        self.response_provider = response_provider or (lambda body: [])
        self.status = status
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Zotero is running")

            def do_POST(self):
                owner.posts += 1
                length = int(self.headers.get('Content-Length', '0'))
                body = self.rfile.read(length).decode('utf-8', errors='ignore')
                items = owner.response_provider(body)
                payload = json.dumps(items, ensure_ascii=False).encode('utf-8')
                self.send_response(owner.status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.url = f"http://127.0.0.1:{self.server.server_port}/connector/import"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

def run_cli(*args):
    cmd = [sys.executable, "-B", str(PROGRAM)] + list(args)
    env = dict(os.environ, PYTHONIOENCODING='utf-8', NO_PROXY='127.0.0.1,localhost')
    p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                       errors='replace', timeout=40, env=env)
    return {'exit_code': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}

def make_dummy_pdf(path, label="workflow_test"):
    if isinstance(label, (bytes, bytearray)):
        lbl = label.decode('utf-8', errors='ignore')
    else:
        lbl = str(label)
    stream = f'BT /F1 12 Tf 40 700 Td ({lbl}) Tj ET'.encode()
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
    content = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(content))
        content.extend(f'{index} 0 obj\n'.encode() + obj + b'\nendobj\n')
    xref = len(content)
    content.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]:
        content.extend(f'{offset:010d} 00000 n \n'.encode())
    content.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(bytes(content))
    return str(p)

def main():
    test_results = {}
    print("=== 开始 T004 独立隔离自动化测试 ===")

    with tempfile.TemporaryDirectory(prefix="t004_test_") as td:
        base = Path(td)
        out_dir = base / "bridge_output"
        out_dir.mkdir()

        # ----------------------------------------------------
        # 1. 模拟下载文件完成后，通过包装入口 record-download 自动入库并生成正确父子映射
        # ----------------------------------------------------
        print("\n--- TEST 1: record-download 自动触发入库与映射 ---")
        task_dir1 = base / "task1"
        task_dir1.mkdir()
        pdf1 = make_dummy_pdf(task_dir1 / "papers" / "PAPER_001.pdf", b"%PDF-1.4\ntest paper 1\n%%EOF")
        
        # 写入任务 manifest.json
        manifest_data1 = {
            "papers": [
                {
                    "id": "PAPER_001",
                    "title": "测试论文1：推力分配优化方法",
                    "authors": "张三;李四",
                    "year": 2026,
                    "source": "水下机器人工程",
                    "url": "https://example.com/p1",
                    "status": "pending"
                }
            ]
        }
        (task_dir1 / "manifest.json").write_text(json.dumps(manifest_data1, ensure_ascii=False), encoding='utf-8')

        store1 = base / "store1"
        store1.mkdir()
        att_dir1 = store1 / "ATT_001"
        att_dir1.mkdir(parents=True)
        shutil.copyfile(pdf1, att_dir1 / Path(pdf1).name)

        def resp_provider1(body):
            return [{"title": "测试论文1：推力分配优化方法", "key": "PARENT_001", "attachments": [{"key": "ATT_001"}]}]

        with MockServer(resp_provider1) as server:
            r1 = run_cli("record-download", "--task", str(task_dir1), "--id", "PAPER_001",
                         "--file", pdf1, "--output", str(out_dir), "--commit",
                         "--connector-url", server.url, "--storage-dir", str(store1))
            print("Run exit_code:", r1['exit_code'])
            print("Stdout snippet:", r1['stdout'].strip())
            
            ledger1 = json.loads((out_dir / "ledger.json").read_text(encoding='utf-8'))
            manifest1 = json.loads((out_dir / "papers_manifest.json").read_text(encoding='utf-8'))
            
            rec1 = list(ledger1["records"].values())[0]
            t1_pass = (r1['exit_code'] == 0 and server.posts == 1 and 
                       rec1["ingest_status"] == "VERIFIED" and rec1["zotero_parent_key"] == "PARENT_001" and
                       len(manifest1) == 1 and manifest1[0]["zotero_key"] == "PARENT_001")
            print("Test 1 Passed?", t1_pass)
            test_results["1_record_download_auto_ingest"] = t1_pass

        # ----------------------------------------------------
        # 2. 已有本地清单走相同入库路径；缺题录不 POST，有效记录正常入库
        # ----------------------------------------------------
        print("\n--- TEST 2: import-local 本地清单接入与缺题录处理 ---")
        pdf2_valid = make_dummy_pdf(base / "local_papers" / "paper2.pdf", b"%PDF-1.4\ntest paper 2\n%%EOF")
        pdf2_notitle = make_dummy_pdf(base / "local_papers" / "paper_notitle.pdf", b"%PDF-1.4\nno title\n%%EOF")

        local_manifest = [
            {
                "source_id": "LOCAL_VALID",
                "title": "测试论文2：无感FOC闭环控制",
                "authors": ["王五"],
                "year": "2026",
                "source": "控制理论与应用",
                "pdf_path": pdf2_valid
            },
            {
                "source_id": "LOCAL_NOTITLE",
                "title": "",
                "pdf_path": pdf2_notitle
            }
        ]
        manifest_file2 = base / "local_manifest.json"
        manifest_file2.write_text(json.dumps(local_manifest, ensure_ascii=False), encoding='utf-8')

        att_dir2 = store1 / "ATT_002"
        att_dir2.mkdir(parents=True)
        shutil.copyfile(pdf2_valid, att_dir2 / Path(pdf2_valid).name)

        def resp_provider2(body):
            return [{"title": "测试论文2：无感FOC闭环控制", "key": "PARENT_002", "attachments": [{"key": "ATT_002"}]}]

        with MockServer(resp_provider2) as server:
            r2 = run_cli("import-local", "--manifest", str(manifest_file2), "--output", str(out_dir),
                         "--commit", "--connector-url", server.url, "--storage-dir", str(store1))
            print("Run exit_code:", r2['exit_code'])
            ledger2 = json.loads((out_dir / "ledger.json").read_text(encoding='utf-8'))
            
            notitle_rec = [r for r in ledger2["records"].values() if r.get("source_id") == "LOCAL_NOTITLE"][0]
            valid_rec = [r for r in ledger2["records"].values() if r.get("source_id") == "LOCAL_VALID"][0]

            t2_pass = (r2['exit_code'] == 0 and server.posts == 1 and
                       notitle_rec["ingest_status"] == "TITLE_MISSING" and
                       valid_rec["ingest_status"] == "VERIFIED" and valid_rec["zotero_parent_key"] == "PARENT_002")
            print("Test 2 Passed?", t2_pass)
            test_results["2_local_manifest_missing_title_handled"] = t2_pass

        # ----------------------------------------------------
        # 3. 同一任务重复同步不增 POST；追加一篇只提交新增篇目
        # ----------------------------------------------------
        print("\n--- TEST 3: sync-downloads 重复同步防重入与增量追加 ---")
        task_dir3 = base / "task3"
        task_dir3.mkdir()
        pdf3_a = make_dummy_pdf(task_dir3 / "papers" / "P3_A.pdf", b"%PDF-1.4\npaper 3a\n%%EOF")
        
        m_data3 = {
            "papers": [
                {"id": "P3_A", "title": "增量论文A", "authors": "赵六", "status": "downloaded", "file": pdf3_a}
            ]
        }
        (task_dir3 / "manifest.json").write_text(json.dumps(m_data3, ensure_ascii=False), encoding='utf-8')

        att_dir3_a = store1 / "ATT_003A"
        att_dir3_a.mkdir(parents=True)
        shutil.copyfile(pdf3_a, att_dir3_a / Path(pdf3_a).name)

        def resp_provider3(body):
            if "增量论文A" in body:
                return [{"title": "增量论文A", "key": "PARENT_003A", "attachments": [{"key": "ATT_003A"}]}]
            if "增量论文B" in body:
                return [{"title": "增量论文B", "key": "PARENT_003B", "attachments": [{"key": "ATT_003B"}]}]
            return []

        with MockServer(resp_provider3) as server:
            # 首次同步并提交 P3_A
            r3_1 = run_cli("sync-downloads", "--task", str(task_dir3), "--output", str(out_dir),
                           "--commit", "--connector-url", server.url, "--storage-dir", str(store1))
            posts_run1 = server.posts
            print("Run 1 posts:", posts_run1)

            # 重复同步 P3_A
            r3_2 = run_cli("sync-downloads", "--task", str(task_dir3), "--output", str(out_dir),
                           "--commit", "--connector-url", server.url, "--storage-dir", str(store1))
            posts_run2 = server.posts
            print("Run 2 posts (duplicate sync):", posts_run2)

            # 追加一篇 P3_B
            pdf3_b = make_dummy_pdf(task_dir3 / "papers" / "P3_B.pdf", b"%PDF-1.4\npaper 3b\n%%EOF")
            att_dir3_b = store1 / "ATT_003B"
            att_dir3_b.mkdir(parents=True)
            shutil.copyfile(pdf3_b, att_dir3_b / Path(pdf3_b).name)
            
            m_data3["papers"].append({"id": "P3_B", "title": "增量论文B", "authors": "赵六", "status": "downloaded", "file": pdf3_b})
            (task_dir3 / "manifest.json").write_text(json.dumps(m_data3, ensure_ascii=False), encoding='utf-8')

            r3_3 = run_cli("sync-downloads", "--task", str(task_dir3), "--output", str(out_dir),
                           "--commit", "--connector-url", server.url, "--storage-dir", str(store1))
            posts_run3 = server.posts
            print("Run 3 posts (incremental B):", posts_run3)

            t3_pass = (posts_run1 == 1 and posts_run2 == 1 and posts_run3 == 2)
            print("Test 3 Passed?", t3_pass)
            test_results["3_incremental_sync_and_dedup"] = t3_pass

        # ----------------------------------------------------
        # 4. 同PDF在新路径/来源重复出现复用映射；版本冲突有明确状态
        # ----------------------------------------------------
        print("\n--- TEST 4: 哈希复用与版本冲突拦截 ---")
        # 4.1 相同哈希文件以新路径传入
        pdf4_dup = make_dummy_pdf(base / "another_folder" / "copied_p1.pdf", b"%PDF-1.4\ntest paper 1\n%%EOF")
        dup_manifest = [
            {
                "source_id": "NEW_SOURCE_ALIAS",
                "title": "测试论文1别名引用",
                "pdf_path": pdf4_dup
            }
        ]
        manifest_file4 = base / "dup_manifest.json"
        manifest_file4.write_text(json.dumps(dup_manifest, ensure_ascii=False), encoding='utf-8')

        with MockServer() as server:
            r4_1 = run_cli("import-local", "--manifest", str(manifest_file4), "--output", str(out_dir),
                           "--commit", "--connector-url", server.url, "--storage-dir", str(store1))
            print("Duplicate hash import exit code:", r4_1['exit_code'], "Posts:", server.posts)
            ledger4 = json.loads((out_dir / "ledger.json").read_text(encoding='utf-8'))
            dup_rec = [r for r in ledger4["records"].values() if r.get("source_id") == "NEW_SOURCE_ALIAS"][0]
            print("Reused parent key:", dup_rec.get("zotero_parent_key"))

            # 4.2 版本冲突：相同 source_id (P3_A) 但哈希变化为新内容
            pdf_conflict = make_dummy_pdf(base / "conflict" / "p3_a_v2.pdf", b"%PDF-1.4\nnew version content\n%%EOF")
            conflict_manifest = [
                {
                    "source_id": "P3_A",
                    "title": "增量论文A版本2",
                    "pdf_path": pdf_conflict
                }
            ]
            manifest_file_conf = base / "conflict_manifest.json"
            manifest_file_conf.write_text(json.dumps(conflict_manifest, ensure_ascii=False), encoding='utf-8')

            r4_2 = run_cli("import-local", "--manifest", str(manifest_file_conf), "--output", str(out_dir),
                           "--commit", "--connector-url", server.url, "--storage-dir", str(store1))
            print("Conflict import exit code:", r4_2['exit_code'], "Posts:", server.posts)
            ledger4_after = json.loads((out_dir / "ledger.json").read_text(encoding='utf-8'))
            conf_rec = [r for r in ledger4_after["records"].values() if r.get("ingest_status") == "VERSION_CONFLICT"][0]

            t4_pass = (server.posts == 0 and dup_rec.get("zotero_parent_key") == "PARENT_001" and 
                       conf_rec is not None and conf_rec.get("ingest_status") == "VERSION_CONFLICT")
            print("Test 4 Passed?", t4_pass)
            test_results["4_hash_reuse_and_version_conflict"] = t4_pass

        # ----------------------------------------------------
        # 5. 未决状态阻断：resume 严禁重新提交结果未决的操作
        # ----------------------------------------------------
        print("\n--- TEST 5: resume 安全阻断未决请求 ---")
        pdf5 = make_dummy_pdf(base / "pending_test" / "p5.pdf", b"%PDF-1.4\npending 5\n%%EOF")
        p5_manifest = [
            {"source_id": "P5_PENDING", "title": "未决论文5", "pdf_path": pdf5}
        ]
        mf5 = base / "mf5.json"
        mf5.write_text(json.dumps(p5_manifest, ensure_ascii=False), encoding='utf-8')

        # 首次导入：网络连接断开 (HTTP 500)
        with MockServer(status=500) as server:
            r5_1 = run_cli("import-local", "--manifest", str(mf5), "--output", str(out_dir),
                           "--commit", "--connector-url", server.url, "--storage-dir", str(store1))
            posts_initial = server.posts
            print("Initial 500 run posts:", posts_initial)
            ledger5 = json.loads((out_dir / "ledger.json").read_text(encoding='utf-8'))
            p5_rec = [r for r in ledger5["records"].values() if r.get("source_id") == "P5_PENDING"][0]
            print("P5 record status:", p5_rec.get("ingest_status"))

            # 运行 resume：必须识别 PENDING_VERIFY 并安全阻断，POST 计数保持不变！
            r5_res = run_cli("resume", "--output", str(out_dir), "--commit",
                             "--connector-url", server.url, "--storage-dir", str(store1))
            posts_after_resume = server.posts
            print("Resume run posts:", posts_after_resume)
            print("Resume stdout snippet:\n", r5_res['stdout'].strip())

            t5_pass = (p5_rec.get("ingest_status") == "PENDING_VERIFY" and 
                       posts_initial == 1 and posts_after_resume == 1)
            print("Test 5 Passed?", t5_pass)
            test_results["5_resume_blocks_pending_requests"] = t5_pass


        # ----------------------------------------------------
        # 6. 读取已有 T001 历史映射作种子，Connector POST 为 0，5 条 Key 和附件不变
        # ----------------------------------------------------
        print("\n--- TEST 6: seed-history 读取既有 T001 种子映射 ---")
        t001_mapping = ROOT / "deliveries" / "T001_zotero_ingest" / "zotero_mapping.json"
        
        with MockServer() as server:
            r6 = run_cli("seed-history", "--mapping", str(t001_mapping), "--output", str(out_dir))
            print("Seed history exit code:", r6['exit_code'])
            print("Mock server POSTs during seed-history:", server.posts)
            ledger6 = json.loads((out_dir / "ledger.json").read_text(encoding='utf-8'))
            
            # 校验 5 条原有 Key
            orig_keys = {"DR5JU2FS", "T8LRTJYA", "8Y3AM4LZ", "YZS58AHK", "6VYKBKDI"}
            seeded_keys = {r.get("zotero_parent_key") for r in ledger6["records"].values() if r.get("download_status") == "seeded"}
            print("Found seeded keys match orig keys?", orig_keys.issubset(seeded_keys))
            
            t6_pass = (r6['exit_code'] == 0 and server.posts == 0 and orig_keys.issubset(seeded_keys))
            print("Test 6 Passed?", t6_pass)
            test_results["6_seed_history_reads_t001_zero_post"] = t6_pass

        # ----------------------------------------------------
        # 7. 损坏 PDF 不发起 POST，其他有效记录正常处理
        # ----------------------------------------------------
        print("\n--- TEST 7: 损坏 PDF 校验拦截 ---")
        p7_p = base / "corrupt_test" / "p7_corrupt.pdf"; p7_p.parent.mkdir(parents=True, exist_ok=True); p7_p.write_bytes(b"not-a-pdf-corrupt"); pdf7_corrupt = str(p7_p)
        manifest_corrupt = [
            {"source_id": "P7_CORRUPT", "title": "损坏PDF测试", "pdf_path": pdf7_corrupt}
        ]
        mf7 = base / "mf7.json"
        mf7.write_text(json.dumps(manifest_corrupt, ensure_ascii=False), encoding='utf-8')

        with MockServer() as server:
            r7 = run_cli("import-local", "--manifest", str(mf7), "--output", str(out_dir),
                         "--commit", "--connector-url", server.url, "--storage-dir", str(store1))
            print("Corrupt run posts:", server.posts)
            ledger7 = json.loads((out_dir / "ledger.json").read_text(encoding='utf-8'))
            p7_rec = [r for r in ledger7["records"].values() if r.get("source_id") == "P7_CORRUPT"][0]
            print("P7 record status:", p7_rec.get("ingest_status"))

            t7_pass = (server.posts == 0 and p7_rec.get("ingest_status") == "FAILED" and "PDF损坏" in str(p7_rec.get("last_error")))
            print("Test 7 Passed?", t7_pass)
            test_results["7_corrupt_pdf_no_post"] = t7_pass

    print("\n=== T004 自动化测试汇总 ===")
    print(json.dumps(test_results, indent=2))
    all_ok = all(test_results.values())
    print("All Passed?", all_ok)
    if not all_ok:
        sys.exit(1)

if __name__ == "__main__":
    main()
