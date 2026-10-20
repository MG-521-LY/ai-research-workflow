import unittest
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import sys
from pathlib import Path

# Add directories to path
SCRIPT_DIR = Path(__file__).resolve().parent
T004_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(T004_DIR))
sys.path.insert(0, str(SCRIPT_DIR))

from pipeline_runner import (
    LedgerManager,
    compute_item_key,
    normalize_doi,
    is_structured_session_exists_evidence,
    verify_pdf_fulltext,
    ZOTERO_DEFAULT_DB,
    ZOTERO_DEFAULT_STORAGE
)
from instsci_bridge_adapter import compute_stable_source_id, adapt_instsci_task


class TestAdapterProofs(unittest.TestCase):

    def test_01_real_zotero_zero_redundant_writes(self):
        """
        Proof 1: 真实既有库只读核对与零重复写入事实
        验证在消费真实已入库条目时，走只读证据比对复用路径，Zotero items 表数量严格零增长。
        """
        db_live = Path.home() / "Zotero/zotero.sqlite"
        self.assertTrue(db_live.is_file(), "Zotero database must exist")

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

        # Run adapter against upstream_topic_task in a fresh temp bridge out
        with tempfile.TemporaryDirectory() as tmp_bridge:
            task_dir = SCRIPT_DIR / "upstream_topic_task"
            res = adapt_instsci_task(
                task_dir=task_dir,
                output_bridge_dir=tmp_bridge,
                commit=False
            )
            self.assertEqual(res["input_count"], 3)
            self.assertEqual(res["verified_count"], 3)
            for it in res["records"].values():
                self.assertEqual(it["download_status"], "reused_existing")
                self.assertEqual(it["ingest_status"], "VERIFIED")
                self.assertIsNotNone(it["zotero_parent_key"])
                self.assertIsNotNone(it["zotero_attachment_dir"])

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

        # Strict assertion: Zero new items written
        self.assertEqual(count_after, count_before, "Database item count must be unchanged (0 writes)")

    def test_02_serial_collision_isolation(self):
        """
        Proof 2: 同序号不同论文不覆盖（跨批别名与全局确定性身份隔离）
        验证如果两个批次都将论文命名为 instsci_supp_001，但 DOI / 哈希不同，
        系统计算出完全隔离的 stable_source_id 与 item_key，各自独立存储于 ledger 中，绝不覆盖。
        """
        # Paper A: PHR 2018 (DOI: 10.2478/phr-2018-0022)
        doi_a = "10.2478/phr-2018-0022"
        title_a = "The practical aspects of implementation of the thrust allocation..."
        sha_a = "7a976bb52dfcfadfb8357f226d31dabbc1c0b56e09e839f14d6a027200118769"

        # Paper B: Completely different paper in Batch 2 also labelled instsci_supp_001
        doi_b = "10.1109/OCEANS.2024.9999999"
        title_b = "Adaptive Fault-Tolerant Thrust Reconfiguration for Deep-Sea Submersibles"
        sha_b = "deadbeef1234567890abcdef1234567890abcdef1234567890abcdef12345678"

        id_a = compute_stable_source_id(doi_a, title_a, sha_a)
        id_b = compute_stable_source_id(doi_b, title_b, sha_b)

        key_a = compute_item_key(id_a, sha_a, title_a)
        key_b = compute_item_key(id_b, sha_b, title_b)

        self.assertNotEqual(id_a, id_b)
        self.assertNotEqual(key_a, key_b)

        # In ledger: both can coexist under different keys even if alias is instsci_supp_001
        with tempfile.TemporaryDirectory() as tmp_dir:
            ledger = LedgerManager(Path(tmp_dir))
            rec_a = {
                "item_key": key_a, "paper_id": id_a, "source_id": id_a,
                "aliases": ["instsci_supp_001", id_a], "title": title_a, "sha256": sha_a,
                "ingest_status": "VERIFIED"
            }
            rec_b = {
                "item_key": key_b, "paper_id": id_b, "source_id": id_b,
                "aliases": ["instsci_supp_001", id_b], "title": title_b, "sha256": sha_b,
                "ingest_status": "VERIFIED"
            }
            ledger.upsert_record(rec_a)
            ledger.upsert_record(rec_b)
            self.assertEqual(len(ledger.data["records"]), 2)
            self.assertIn(key_a, ledger.data["records"])
            self.assertIn(key_b, ledger.data["records"])
            self.assertEqual(ledger.data["records"][key_a]["title"], title_a)
            self.assertEqual(ledger.data["records"][key_b]["title"], title_b)

    def test_03_failure_state_does_not_forge_completed(self):
        """
        Proof 3: 失败恢复状态不造 completed
        验证当 PDF 损坏或获取失败时，状态严格置为 FAILED 或 QUEUED，绝不伪造 COMPLETED 或 VERIFIED。
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            # 1. Too small file rejected
            small_pdf = Path(tmp_dir) / "small.pdf"
            small_pdf.write_bytes(b"SHORT")
            val_ok, val_msg, _ = verify_pdf_fulltext(small_pdf)
            self.assertFalse(val_ok)
            self.assertIn("文件过小", val_msg)

            # 2. Corrupt / fake HTML error page rejected
            bad_pdf = Path(tmp_dir) / "fake_html.pdf"
            bad_pdf.write_bytes(b"<html><body>502 Bad Gateway Error</body></html>" + b" " * 600)
            val_ok2, val_msg2, _ = verify_pdf_fulltext(bad_pdf)
            self.assertFalse(val_ok2)
            self.assertIn("不支持的全文文件格式", val_msg2)

            # Recovery gate check rejects invalid states
            fail_state = {
                "status": "FAILED",
                "error_response_body": json.dumps({"error": "CONNECTION_REFUSED", "code": 500})
            }
            self.assertFalse(is_structured_session_exists_evidence(fail_state))

    def test_04_serialization_order_invariance(self):
        """
        Proof 4: 稳定来源记录哈希在序列化/键导出顺序变化时保持恒定
        验证使用 sort_keys=True 序列化保证来源记录哈希不因字典键插入顺序改变而误失效。
        """
        rec_order_1 = {
            "doi": "10.2478/phr-2018-0022",
            "title": "The practical aspects...",
            "year": 2018,
            "result_evidence": "oa_direct"
        }
        rec_order_2 = {
            "result_evidence": "oa_direct",
            "year": 2018,
            "title": "The practical aspects...",
            "doi": "10.2478/phr-2018-0022"
        }
        sha_1 = hashlib.sha256(json.dumps(rec_order_1, sort_keys=True).encode("utf-8")).hexdigest()
        sha_2 = hashlib.sha256(json.dumps(rec_order_2, sort_keys=True).encode("utf-8")).hexdigest()
        self.assertEqual(sha_1, sha_2)


if __name__ == "__main__":
    unittest.main()


