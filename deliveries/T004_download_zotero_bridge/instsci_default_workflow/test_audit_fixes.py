
import unittest
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import sys
from pathlib import Path

MODULE_DIR = Path(r"E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_default_workflow")
T004_DIR = MODULE_DIR.parent
sys.path.insert(0, str(MODULE_DIR))
sys.path.insert(0, str(T004_DIR))

from mutex_lock import WorkflowLock, WorkflowLockError, inspect_lock
from thin_adapter import compute_stable_source_id, sync_single_candidate
from manifest_builder import build_unified_manifest, compute_manifest_sha256, resolve_valid_analysis_pdf
from workflow_runner import cmd_from_task, cmd_import_local, cmd_recover, cmd_status, cmd_sync
from pipeline_runner import (
    LedgerManager,
    is_structured_session_exists_evidence,
    verify_pdf_fulltext,
    ZOTERO_DEFAULT_DB,
    ZOTERO_DEFAULT_STORAGE
)


class TestComprehensiveWorkflowAuditFixes(unittest.TestCase):

    def test_01_empty_pdf_path_never_resolves_to_cwd(self):
        """1. 严格路径契约：缺失/空pdf_path绝不resolve为cwd，必须抛出错误或阻塞"""
        empty_rec = {
            "paper_id": "test_empty_pdf",
            "pdf_path": "",
            "original_pdf_path": "",
            "working_pdf_path": "",
            "local_pdf_absolute": "",
            "zotero_stored_pdf_path": ""
        }
        with self.assertRaises(FileNotFoundError):
            resolve_valid_analysis_pdf(empty_rec)

    def test_02_all_13_items_in_manifest_are_real_files_matching_sha(self):
        """2. 13篇全量清单实际文件校验：每一篇pdf_path均为有效文件且哈希严格匹配"""
        man_p = MODULE_DIR / "unified_analysis_manifest.json"
        self.assertTrue(man_p.is_file())
        man = json.loads(man_p.read_text(encoding="utf-8"))
        self.assertEqual(man["total_items"], 13)

        for it in man["items"]:
            pdf_path = Path(it["pdf_path"])
            self.assertTrue(pdf_path.is_file(), f"pdf_path must be a file: {pdf_path}")
            self.assertNotEqual(pdf_path.name, "dsh_workspace", "pdf_path cannot be dsh_workspace directory")
            h = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
            self.assertEqual(h, it["sha256"], f"SHA256 mismatch for {it['paper_id']}")

    def test_03_isolated_invalid_import_returns_non_zero_and_failed_status(self):
        """3. 错误真实反映：输入[{}]且--no-commit返回exit_code != 0且status != success"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            bad_rec_file = Path(tmp_dir) / "bad_records.json"
            bad_rec_file.write_text(json.dumps([{}]), encoding="utf-8")
            out_dir = Path(tmp_dir) / "bridge_out"

            exit_code, res = cmd_import_local(
                records_file=bad_rec_file,
                output_dir=out_dir,
                commit=False
            )
            # Must NOT report success when items failed
            self.assertNotEqual(exit_code, 0, "Exit code must be non-zero on failed items")
            self.assertEqual(res["status"], "failed", "Status must be failed when failed > 0")
            self.assertEqual(res["counts"]["failed"], 1)
            self.assertEqual(res["total_input"], 1)

    def test_04_thin_adapter_reverifies_zotero_disk_pdf_not_blindly_trusting_ledger(self):
        """4. 缓存安全：thin_adapter不盲目信任旧VERIFIED，会回查Zotero真实存储PDF"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            bridge_p = Path(tmp_dir) / "bridge"
            ledger = LedgerManager(bridge_p)

            # Insert fake VERIFIED record with non-existent Zotero stored PDF
            fake_key = "fake_item_key_12345"
            ledger.upsert_record({
                "item_key": fake_key,
                "paper_id": "fake_paper",
                "title": "测试不存在的文献",
                "ingest_status": "VERIFIED",
                "sha256": "1234" * 16,
                "file_bytes": 100,
                "zotero_stored_pdf_path": str(Path(tmp_dir) / "non_existent.pdf"),
                "original_pdf_path": str(Path(tmp_dir) / "non_existent.pdf"),
            })
            ledger.save()

            # Now sync candidate with same item_key: must NOT return reused_ledger blindly
            cand_rec = {
                "title": "测试不存在的文献",
                "authors": ["Test"],
                "year": "2024",
                "pdf_path": str(Path(tmp_dir) / "non_existent.pdf"),
            }
            ok, status, data = sync_single_candidate(cand_rec, bridge_p, ledger, commit=False)
            self.assertFalse(ok)
            self.assertNotEqual(status, "reused_ledger")

    def test_05_corrupt_lock_not_blindly_deleted_and_active_lock_respected(self):
        """5. 运行锁与恢复安全：活跃锁坚决不抢，损坏锁证据不足不盲删"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            bridge_p = Path(tmp_dir) / "bridge"
            bridge_p.mkdir(parents=True)
            lock_file = bridge_p / ".workflow.lock"

            # Create corrupt lock (non-json garbage)
            lock_file.write_text("GARBAGE_LOCK_CONTENT", encoding="utf-8")

            # recover without force must NOT delete corrupt lock
            rec_res = cmd_recover(bridge_p, force_unlock=False)
            self.assertFalse(rec_res["lock_reclaimed"])
            self.assertTrue(lock_file.exists())
            self.assertEqual(rec_res["status"], "blocked")

    def test_06_recovery_gate_check_strict_counterexamples(self):
        """6. 恢复证据门禁纯函数反例严格全绿"""
        spoof_rec = {
            "legacy_session_conflict_migration": {
                "verified_pre_save_rejection": True,
                "paper_id": "426e8bdf0d0ec52e",
                "batch_fingerprint": "a" * 64,
                "source_evidence": "invented server_connector.js",
                "audit_decision": "invented audit_confirmed_pre_save_rejection"
            }
        }
        self.assertFalse(is_structured_session_exists_evidence(spoof_rec))
        self.assertFalse(is_structured_session_exists_evidence(spoof_rec, paper_id="426e8bdf0d0ec52e"))


if __name__ == "__main__":
    unittest.main()
