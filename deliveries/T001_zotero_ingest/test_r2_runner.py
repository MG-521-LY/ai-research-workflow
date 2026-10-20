
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

ROOT = Path(r'E:\AI\AI_reserch')
DELIVERY = ROOT / 'deliveries' / 'T001_zotero_ingest'
SOURCE = Path(r'C:\Users\lamost\Documents\Codex\2026-10-14\yo\outputs\cnki-ai-self-learning-2026-10-14')
PROGRAM = DELIVERY / 'ingest_runner.py'

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

class MockServer:
    def __init__(self, items, status=201):
        self.posts = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'Zotero is running')

            def do_POST(self):
                owner.posts += 1
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                payload = json.dumps(items, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.url = f'http://127.0.0.1:{self.server.server_port}/connector/import'

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

def run(work, *args, endpoint=None, storage=None, db_path=None):
    cmd = [sys.executable, str(PROGRAM), '--delivery', str(work)]
    if endpoint:
        cmd += ['--connector-url', endpoint]
    if storage:
        cmd += ['--storage-dir', str(storage)]
    if db_path:
        cmd += ['--db-path', str(db_path)]
    cmd += list(args)
    env = dict(os.environ, PYTHONIOENCODING='utf-8', NO_PROXY='127.0.0.1,localhost')
    p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                       errors='replace', timeout=45, env=env)
    return {'exit_code': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}

def prepare(work):
    result = run(work, '--prepare-only')
    if result['exit_code']:
        raise RuntimeError(result)
    return json.loads((work / 'standardized_items.json').read_text(encoding='utf-8'))

test_summary = {}

with tempfile.TemporaryDirectory(prefix='r2_test_') as td:
    base = Path(td)

    # ----------------------------------------------------
    # Case 1 (R2-A 复现二验证): 有关旧存储，但返回新父条目没有挂接子附件 -> 必须失败，且不能将旧附件误认为成功
    # ----------------------------------------------------
    print("\n=== TEST 1: R2-A Unrelated storage must not be falsely verified ===")
    t1_dir = base / 't1_unrelated'
    t1_items = prepare(t1_dir)
    t1_store = base / 't1_old_store'
    for i, x in enumerate(t1_items, 1):
        f = t1_store / f'OLD{i:05d}'
        f.mkdir(parents=True)
        shutil.copyfile(x['working_pdf'], f / Path(x['working_pdf']).name)

    mock_items_no_att = [{'title': x['title'], 'key': f'NEW{i:05d}', 'attachments': []}
                         for i, x in enumerate(t1_items, 1)]
    with MockServer(mock_items_no_att) as server:
        res1_a = run(t1_dir, '--import-only', endpoint=server.url, storage=t1_store)
        print("Run 1 exit code:", res1_a['exit_code'], "(Expected: 6)")
        res1_map = json.loads((t1_dir / 'zotero_mapping.json').read_text(encoding='utf-8'))
        print("Run 1 verified states:", [m['hash_verified'] for m in res1_map], "(Expected all False)")
        print("Run 1 parent_attachment_linked:", [m['parent_attachment_linked'] for m in res1_map], "(Expected all False)")

        res1_b = run(t1_dir, '--import-only', endpoint=server.url, storage=t1_store)
        print("Run 2 (retry) exit code:", res1_b['exit_code'], "(Expected: 7)")
        print("Mock server total POSTs:", server.posts, "(Expected: 1)")

        test_summary['R2_A_unrelated_storage_rejected'] = (
            res1_a['exit_code'] == 6 and
            all(not m['hash_verified'] for m in res1_map) and
            all(not m['parent_attachment_linked'] for m in res1_map) and
            res1_b['exit_code'] == 7 and
            server.posts == 1
        )

    # ----------------------------------------------------
    # Case 2 (R2-A 真实父子关联验证): 返回父条目且 attachments 包含对应子附件 Key -> 验证成功通过
    # ----------------------------------------------------
    print("\n=== TEST 2: R2-A True parent-child attachment verification ===")
    t2_dir = base / 't2_valid'
    t2_items = prepare(t2_dir)
    t2_store = base / 't2_store'
    mock_items_valid = []
    for i, x in enumerate(t2_items, 1):
        att_key = f'ATT{i:05d}'
        folder = t2_store / att_key
        folder.mkdir(parents=True)
        shutil.copyfile(x['working_pdf'], folder / Path(x['working_pdf']).name)
        mock_items_valid.append({
            'title': x['title'],
            'key': f'P{i:05d}',
            'attachments': [{'key': att_key, 'title': Path(x['working_pdf']).name}]
        })

    with MockServer(mock_items_valid) as server:
        res2_a = run(t2_dir, '--import-only', endpoint=server.url, storage=t2_store)
        print("Run 1 exit code:", res2_a['exit_code'], "(Expected: 0)")
        res2_map = json.loads((t2_dir / 'zotero_mapping.json').read_text(encoding='utf-8'))
        print("Run 1 verified states:", [m['hash_verified'] for m in res2_map], "(Expected all True)")
        print("Run 1 parent_attachment_linked:", [m['parent_attachment_linked'] for m in res2_map], "(Expected all True)")

        res2_b = run(t2_dir, '--import-only', endpoint=server.url, storage=t2_store)
        print("Run 2 (skip) exit code:", res2_b['exit_code'], "(Expected: 0)")
        print("Mock server total POSTs:", server.posts, "(Expected: 1)")

        test_summary['R2_A_valid_attachment_verified'] = (
            res2_a['exit_code'] == 0 and
            all(m['hash_verified'] for m in res2_map) and
            all(m['parent_attachment_linked'] for m in res2_map) and
            res2_b['exit_code'] == 0 and
            server.posts == 1
        )

    # ----------------------------------------------------
    # Case 3 (R2-B 部分返回阻断): 接口只返回 4 条记录 -> 首次非零退出，再次运行安全阻断，POST 计数保持 1
    # ----------------------------------------------------
    print("\n=== TEST 3: R2-B Partial response blocked from retry ===")
    t3_dir = base / 't3_partial'
    t3_items = prepare(t3_dir)
    with MockServer(mock_items_valid[:4]) as server:
        res3_a = run(t3_dir, '--import-only', endpoint=server.url, storage=t2_store)
        print("Run 1 exit code:", res3_a['exit_code'], "(Expected: 5)")
        state_file = json.loads((t3_dir / 'batch_state.json').read_text(encoding='utf-8'))
        print("Batch status:", state_file.get('status'), "(Expected: PARTIAL)")

        res3_b = run(t3_dir, '--import-only', endpoint=server.url, storage=t2_store)
        print("Run 2 exit code:", res3_b['exit_code'], "(Expected: 7)")
        print("Mock server total POSTs:", server.posts, "(Expected: 1)")

        test_summary['R2_B_partial_response_blocked'] = (
            res3_a['exit_code'] == 5 and
            state_file.get('status') == 'PARTIAL' and
            res3_b['exit_code'] == 7 and
            server.posts == 1
        )

    # ----------------------------------------------------
    # Case 4 (R2-B 存储为空附件缺失): 首次报告附件核验失败，再次运行安全阻断，POST 计数保持 1
    # ----------------------------------------------------
    print("\n=== TEST 4: R2-B Missing attachment blocked from retry ===")
    t4_dir = base / 't4_empty_store'
    t4_items = prepare(t4_dir)
    t4_empty = base / 't4_empty'
    t4_empty.mkdir()
    with MockServer(mock_items_valid) as server:
        res4_a = run(t4_dir, '--import-only', endpoint=server.url, storage=t4_empty)
        print("Run 1 exit code:", res4_a['exit_code'], "(Expected: 6)")
        state_file4 = json.loads((t4_dir / 'batch_state.json').read_text(encoding='utf-8'))
        print("Batch status:", state_file4.get('status'), "(Expected: ATTACHMENT_UNVERIFIED)")

        res4_b = run(t4_dir, '--import-only', endpoint=server.url, storage=t4_empty)
        print("Run 2 exit code:", res4_b['exit_code'], "(Expected: 7)")
        print("Mock server total POSTs:", server.posts, "(Expected: 1)")

        test_summary['R2_B_missing_attachment_blocked'] = (
            res4_a['exit_code'] == 6 and
            state_file4.get('status') == 'ATTACHMENT_UNVERIFIED' and
            res4_b['exit_code'] == 7 and
            server.posts == 1
        )

    # ----------------------------------------------------
    # Case 5 (收尾阻塞项回归 1): 完成状态但映射文件缺失 -> 再次运行非零退出，POST 保持 1，禁止自动重新提交
    # ----------------------------------------------------
    print("\n=== TEST 5: Wrap-up completed state with mapping file missing ===")
    t5_dir = base / 't5_completed_map_missing'
    t5_items = prepare(t5_dir)
    t5_store = base / 't5_store'
    t5_mock_items = []
    for i, x in enumerate(t5_items, 1):
        att_key = f'ATT5_{i:05d}'
        folder = t5_store / att_key
        folder.mkdir(parents=True)
        shutil.copyfile(x['working_pdf'], folder / Path(x['working_pdf']).name)
        t5_mock_items.append({
            'title': x['title'],
            'key': f'P5_{i:05d}',
            'attachments': [{'key': att_key, 'title': Path(x['working_pdf']).name}]
        })

    with MockServer(t5_mock_items) as server:
        res5_a = run(t5_dir, '--import-only', endpoint=server.url, storage=t5_store)
        print("Run 1 (initial success) exit code:", res5_a['exit_code'], "(Expected: 0)")
        print("Run 1 post count:", server.posts, "(Expected: 1)")
        state_file5 = json.loads((t5_dir / 'batch_state.json').read_text(encoding='utf-8'))
        print("Run 1 batch status:", state_file5.get('status'), "(Expected: COMPLETED)")

        # 模拟外部意外删除了 mapping 文件
        map_path = t5_dir / 'zotero_mapping.json'
        map_path.unlink()
        print("Mapping file removed.")

        # 第二次运行：必须安全阻断，非零退出，POST 保持 1，状态依然保留 COMPLETED
        res5_b = run(t5_dir, '--import-only', endpoint=server.url, storage=t5_store)
        print("Run 2 (missing mapping retry) exit code:", res5_b['exit_code'], "(Expected: 8)")
        print("Run 2 post count:", server.posts, "(Expected: 1)")
        state_file5_after = json.loads((t5_dir / 'batch_state.json').read_text(encoding='utf-8'))
        print("Run 2 batch status after retry:", state_file5_after.get('status'), "(Expected: COMPLETED)")

        test_summary['WrapUp_completed_mapping_missing_blocked'] = (
            res5_a['exit_code'] == 0 and
            res5_b['exit_code'] == 8 and
            server.posts == 1 and
            state_file5_after.get('status') == 'COMPLETED'
        )

    # ----------------------------------------------------
    # Case 6 (收尾阻塞项回归 2): 完成状态但映射文件损坏 -> 再次运行非零退出，POST 保持 1，禁止自动重新提交
    # ----------------------------------------------------
    print("\n=== TEST 6: Wrap-up completed state with mapping file corrupted ===")
    t6_dir = base / 't6_completed_map_corrupt'
    t6_items = prepare(t6_dir)
    t6_store = base / 't6_store'
    t6_mock_items = []
    for i, x in enumerate(t6_items, 1):
        att_key = f'ATT6_{i:05d}'
        folder = t6_store / att_key
        folder.mkdir(parents=True)
        shutil.copyfile(x['working_pdf'], folder / Path(x['working_pdf']).name)
        t6_mock_items.append({
            'title': x['title'],
            'key': f'P6_{i:05d}',
            'attachments': [{'key': att_key, 'title': Path(x['working_pdf']).name}]
        })

    with MockServer(t6_mock_items) as server:
        res6_a = run(t6_dir, '--import-only', endpoint=server.url, storage=t6_store)
        print("Run 1 (initial success) exit code:", res6_a['exit_code'], "(Expected: 0)")
        print("Run 1 post count:", server.posts, "(Expected: 1)")
        state_file6 = json.loads((t6_dir / 'batch_state.json').read_text(encoding='utf-8'))
        print("Run 1 batch status:", state_file6.get('status'), "(Expected: COMPLETED)")

        # 模拟外部映射文件损坏（非合法 JSON）
        map_path = t6_dir / 'zotero_mapping.json'
        map_path.write_text('{broken json content', encoding='utf-8')
        print("Mapping file corrupted with broken json.")

        # 第二次运行：必须安全阻断，非零退出，POST 保持 1，状态依然保留 COMPLETED
        res6_b = run(t6_dir, '--import-only', endpoint=server.url, storage=t6_store)
        print("Run 2 (corrupt mapping retry) exit code:", res6_b['exit_code'], "(Expected: 8)")
        print("Run 2 post count:", server.posts, "(Expected: 1)")
        state_file6_after = json.loads((t6_dir / 'batch_state.json').read_text(encoding='utf-8'))
        print("Run 2 batch status after retry:", state_file6_after.get('status'), "(Expected: COMPLETED)")

        test_summary['WrapUp_completed_mapping_corrupt_blocked'] = (
            res6_a['exit_code'] == 0 and
            res6_b['exit_code'] == 8 and
            server.posts == 1 and
            state_file6_after.get('status') == 'COMPLETED'
        )

print("\n=== SUMMARY OF ALL ISOLATED TESTS ===")
print(json.dumps(test_summary, indent=2))
all_passed = all(test_summary.values())
print("All passed?", all_passed)
if not all_passed:
    sys.exit(1)
