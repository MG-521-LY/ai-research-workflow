import unittest
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import sys
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
T004_DIR = MODULE_DIR.parent
sys.path.insert(0, str(MODULE_DIR))
sys.path.insert(0, str(T004_DIR))

from mutex_lock import WorkflowLock, WorkflowLockError, inspect_lock
from thin_adapter import compute_stable_source_id, sync_single_candidate
from manifest_builder import build_unified_manifest, compute_manifest_sha256
from workflow_runner import cmd_from_task, cmd_import_local, cmd_recover, cmd_status
from pipeline_runner import (
    LedgerManager,
    is_structured_session_exists_evidence,
    verify_pdf_fulltext,
    ZOTERO_DEFAULT_DB,
    ZOTERO_DEFAULT_STORAGE
)


class TestInstSciDefaultWorkflow(unittest.TestCase):

    def test_01_lock_acquire_release_and_stale_detection(self):
        """1. 运行锁并发冲突检测与死进程陈旧锁自动清理"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock1 = WorkflowLock(tmp_dir, command="test-cmd-1")
            lock1.acquire()
            self.assertTrue(lock1.acquired)
            self.assertTrue((Path(tmp_dir) / ".workflow.lock").is_file())

            # Concurrent acquisition by another instance must fail
            lock2 = WorkflowLock(tmp_dir, command="test-cmd-2")
            with self.assertRaises(WorkflowLockError):
                lock2.acquire()

            # Release lock1
            lock1.release()
            self.assertFalse(lock1.acquired)
            self.assertFalse((Path(tmp_dir) / ".workflow.lock").is_file())

            # Simulate stale lock from dead PID (9999999)
            stale_lock_file = Path(tmp_dir) / ".workflow.lock"
            stale_lock_file.write_text(json.dumps({
                "pid": 9999999,
                "host": "localhost",
                "command": "dead-cmd",
                "started_at": "2026-10-17T00:00:00Z"
            }), encoding="utf-8")

            # Inspect lock
            info = inspect_lock(tmp_dir)
            self.assertFalse(info["locked"])
            self.assertTrue(info["stale"])

            # Lock3 acquiring over stale lock must succeed
            lock3 = WorkflowLock(tmp_dir, command="test-cmd-3")
            lock3.acquire()
            self.assertTrue(lock3.acquired)
            lock3.release()

    def test_02_zero_write_and_zero_post_deduplication(self):
        """2. 真实既有库只读核对与零重复写入事实"""
        db_live = Path.home() / "Zotero/zotero.sqlite"
        if not db_live.is_file():
            self.skipTest("Local Zotero database not found")

        # Snapshot count before
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp_db:
            tmp_db_path = Path(tmp_db.name)
        try:
            shutil.copy2(db_live, tmp_db_path)
            conn = sqlite3.connect(tmp_db_path)
            count_before = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            conn.close()
        finally:
            if tmp_db_path.exists():
                tmp_db_path.unlink()

        # Run from-task against upstream_topic_task
        task_dir = T004_DIR / "instsci_chain_integration" / "upstream_topic_task"
        if not task_dir.is_dir():
            self.skipTest("upstream_topic_task directory not found")

        with tempfile.TemporaryDirectory() as tmp_bridge:
            exit_code, res = cmd_from_task(
                task_dir=task_dir,
                output_dir=tmp_bridge,
                commit=True
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["total_input"], 3)
            self.assertEqual(res["counts"]["reused"], 3)
            self.assertEqual(res["counts"]["imported"], 0)

        # Snapshot count after
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp_db2:
            tmp_db2_path = Path(tmp_db2.name)
        try:
            shutil.copy2(db_live, tmp_db2_path)
            conn2 = sqlite3.connect(tmp_db2_path)
            count_after = conn2.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            conn2.close()
        finally:
            if tmp_db2_path.exists():
                tmp_db2_path.unlink()

        # Net database writes must be exactly zero
        self.assertEqual(count_after, count_before, "Database item count must be unchanged (0 writes)")

    def test_03_serial_collision_isolation_and_aliases(self):
        """3. 同序号不同论文不覆盖（跨批别名与全局确定性身份隔离）"""
        doi_1 = "10.2478/phr-2018-0022"
        sha_1 = "7a976bb52dfcfadfb8357f226d31dabbc1c0b56e09e839f14d6a027200118769"
        title_1 = "The practical aspects of implementation of the thrust allocation..."

        doi_2 = "10.1109/OCEANS.2024.9999999"
        sha_2 = "deadbeef1234567890abcdef1234567890abcdef1234567890abcdef12345678"
        title_2 = "Adaptive Fault-Tolerant Thrust Reconfiguration for Deep-Sea Submersibles"

        id_1 = compute_stable_source_id(doi_1, title_1, sha_1)
        id_2 = compute_stable_source_id(doi_2, title_2, sha_2)

        self.assertNotEqual(id_1, id_2)
        self.assertTrue(id_1.startswith("doi_10.2478_phr-2018-0022"))
        self.assertTrue(id_2.startswith("doi_10.1109_oceans.2024.9999999"))

    def test_04_chinese_paper_without_doi_thin_adapter(self):
        """4. 中文无DOI薄适配：已存在匹配不伪造DOI，元数据不全置pending_metadata"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            bridge_p = Path(tmp_dir) / "bridge"
            ledger = LedgerManager(bridge_p)

            # Test A: Incomplete metadata
            dummy_pdf = Path(tmp_dir) / "test.pdf"
            dummy_pdf.write_bytes(b"%PDF-1.4\n" + b"0" * 600 + b"\n%%EOF\n")

            incomplete_rec = {
                "title": "测试中文无DOI题录",
                "authors": [],
                "year": "",
                "doi": "",
                "pdf_path": str(dummy_pdf)
            }
            ok, status, data = sync_single_candidate(
                record=incomplete_rec,
                bridge_dir=bridge_p,
                ledger=ledger,
                commit=False
            )
            self.assertFalse(ok)
            self.assertEqual(status, "pending_metadata")
            self.assertEqual(data["ingest_status"], "PENDING_METADATA")
            self.assertIsNone(data["doi"])

    def test_05_failure_and_corrupt_pdf_rejection(self):
        """5. 异常文件严格拒绝，绝不伪造COMPLETED"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            bridge_p = Path(tmp_dir) / "bridge"
            ledger = LedgerManager(bridge_p)

            bad_pdf = Path(tmp_dir) / "corrupt.pdf"
            bad_pdf.write_bytes(b"<html><body>403 Forbidden</body></html>" + b" " * 600)

            rec = {
                "title": "损坏文献测试",
                "authors": ["张三"],
                "year": "2024",
                "pdf_path": str(bad_pdf)
            }
            ok, status, data = sync_single_candidate(
                record=rec,
                bridge_dir=bridge_p,
                ledger=ledger,
                commit=False
            )
            self.assertFalse(ok)
            self.assertEqual(status, "invalid_pdf")

    def test_06_immutable_downstream_manifest_and_sha256(self):
        """6. 下游统一不可变分析清单生成与确定性哈希"""
        records = [
            {
                "paper_id": "doi_10.2478_phr-2018-0022",
                "source_id": "doi_10.2478_phr-2018-0022",
                "aliases": ["instsci_supp_001", "cand_001"],
                "title": "Paper One",
                "authors": ["Author A"],
                "year": "2018",
                "doi": "10.2478/phr-2018-0022",
                "original_pdf_path": str(Path(__file__).resolve()),
                "file_bytes": 1024,
                "sha256": "7a976bb52dfcfadfb8357f226d31dabbc1c0b56e09e839f14d6a027200118769",
                "zotero_parent_key": "KEY1",
                "zotero_attachment_dir": "ATT1",
                "ingest_status": "VERIFIED"
            },
            {
                "paper_id": "doi_10.48550_arxiv.2609.21046",
                "source_id": "doi_10.48550_arxiv.2609.21046",
                "aliases": ["instsci_supp_003"],
                "title": "Constraint-Unified MPC for Over-Actuated Surface Vehicles",
                "authors": ["Sebastian Burmester"],
                "year": "2026",
                "doi": "10.48550/arxiv.2609.21046",
                "original_pdf_path": str(Path(__file__).resolve()),
                "file_bytes": 2048,
                "sha256": "c61386819e2edeac64b444c4264d929a0182c8a3e62c40f3708459da0c13ebdc",
                "zotero_parent_key": "KEY2",
                "zotero_attachment_dir": "ATT2",
                "ingest_status": "VERIFIED"
            }
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_json = Path(tmp_dir) / "manifest.json"
            manifest = build_unified_manifest(records, output_path=out_json, strict_file_verify=False)
            self.assertEqual(manifest["total_items"], 2)
            self.assertEqual(manifest["schema"], "instsci.unified_analysis_manifest.v1")
            self.assertTrue(manifest["manifest_sha256"])

            # Verify ASV cross-domain transfer notice
            item_asv = next(it for it in manifest["items"] if "instsci_supp_003" in it["aliases"])
            self.assertEqual(item_asv["scope_and_boundary"]["domain"], "surface_vehicle_asv")
            self.assertIn("跨域参考边界", item_asv["scope_and_boundary"]["transfer_boundary"])

    def test_07_recovery_gate_check_strict_counterexamples(self):
        """7. 恢复证据门禁纯函数反例严格全绿"""
        spoof_rec = {
            "legacy_session_conflict_migration": {
                "verified_pre_save_rejection": True,
                "paper_id": "426e8bdf0d0ec52e",
                "batch_fingerprint": "a" * 64,
                "source_evidence": "invented server_connector.js",
                "audit_decision": "invented audit_confirmed_pre_save_rejection"
            }
        }
        # 1. no_context_rejected
        self.assertFalse(is_structured_session_exists_evidence(spoof_rec))
        # 2. arbitrary_audit_keyword_spoof_rejected
        self.assertFalse(is_structured_session_exists_evidence(spoof_rec, paper_id="426e8bdf0d0ec52e"))
        # 3. wrong_batch_rejected
        valid_rec = {
            "legacy_session_conflict_migration": {
                "verified_pre_save_rejection": True,
                "paper_id": "426e8bdf0d0ec52e",
                "batch_fingerprint": "bdf4f127f97fd992397cc9260a9fc3bebdd35b7b25b69e9b6f4155451f56dc85",
                "source_evidence": "server_connector.js SessionManager.create",
                "audit_decision": "Pre-save rejection definitively proven"
            }
        }
        self.assertFalse(is_structured_session_exists_evidence(valid_rec, paper_id="426e8bdf0d0ec52e", batch_fingerprint="b" * 64))
        # 4. allow_legacy_migration=False
        self.assertFalse(is_structured_session_exists_evidence(valid_rec, paper_id="426e8bdf0d0ec52e", allow_legacy_migration=False))
        # 5. authorized record passed
        self.assertTrue(is_structured_session_exists_evidence(valid_rec, paper_id="426e8bdf0d0ec52e"))


if __name__ == "__main__":
    unittest.main()
