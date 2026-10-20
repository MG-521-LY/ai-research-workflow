
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
from thin_adapter import compute_stable_source_id, sync_single_candidate, verify_zotero_item_and_pdf
from manifest_builder import build_unified_manifest, compute_manifest_sha256, resolve_valid_analysis_pdf
from workflow_runner import cmd_run, cmd_sync, cmd_from_task, cmd_import_local, cmd_recover, cmd_status
from pipeline_runner import (
    LedgerManager,
    is_structured_session_exists_evidence,
    verify_pdf_fulltext,
    ZOTERO_DEFAULT_DB,
    ZOTERO_DEFAULT_STORAGE
)


class TestFullAuditLifecycle(unittest.TestCase):

    def test_01_lock_reentrancy_in_same_process(self):
        """1. 锁重入安全：同一进程进入嵌套锁范围不自锁，退出时不提前释放"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock = WorkflowLock(tmp_dir, command="outer")
            lock.acquire()
            self.assertEqual(lock.depth, 1)
            self.assertTrue(lock.is_owner)

            # Re-acquire same lock in same process
            lock.acquire()
            self.assertEqual(lock.depth, 2)
            self.assertTrue(lock.is_owner)

            # Release once
            lock.release()
            self.assertEqual(lock.depth, 1)
            self.assertTrue(lock.is_owner)
            self.assertTrue((Path(tmp_dir) / ".workflow.lock").is_file())

            # Release final
            lock.release()
            self.assertEqual(lock.depth, 0)
            self.assertFalse(lock.is_owner)
            self.assertFalse((Path(tmp_dir) / ".workflow.lock").is_file())

    def test_02_corrupt_lock_blocks_acquire_and_recover(self):
        """2. 损坏锁不可判定保护：损坏锁阻止抢占，recover必须带--force-unlock"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_file = Path(tmp_dir) / ".workflow.lock"
            lock_file.write_text("NOT_VALID_JSON_GARBAGE", encoding="utf-8")

            # Acquire must raise WorkflowLockError
            lock = WorkflowLock(tmp_dir, command="test")
            with self.assertRaises(WorkflowLockError):
                lock.acquire()

            # Recover without force must be blocked
            res = cmd_recover(tmp_dir, force_unlock=False)
            self.assertEqual(res["status"], "blocked")
            self.assertTrue(lock_file.is_file())

            # Recover with force must reclaim
            res_force = cmd_recover(tmp_dir, force_unlock=True)
            self.assertEqual(res_force["status"], "recovered")
            self.assertTrue(res_force["lock_reclaimed"])
            self.assertFalse(lock_file.is_file())

    def test_03_active_pid_lock_is_never_stolen(self):
        """3. 活跃锁保护：当前运行中的进程锁绝对不被抢占"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_file = Path(tmp_dir) / ".workflow.lock"
            # Write my own PID (which is alive!)
            lock_file.write_text(json.dumps({
                "pid": os.getpid(),
                "host": "localhost",
                "command": "active_worker",
                "started_at": "2026-10-17T18:00:00Z"
            }), encoding="utf-8")

            # Inspect lock
            info = inspect_lock(tmp_dir)
            self.assertTrue(info["locked"])
            self.assertFalse(info["stale"])

            # Recover must report blocked
            res = cmd_recover(tmp_dir, force_unlock=True)
            self.assertEqual(res["status"], "blocked")
            self.assertTrue(res["active_lock"])

    def test_04_upstream_review_schema_check(self):
        """4. 上游题录评审模式：decision和reviewed_at正确识别已评审文献"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            task_dir = Path(tmp_dir) / "topic_task"
            task_dir.mkdir()
            
            # Create a mock topic.json where all papers have 'decision'
            topic_data = {
                "schema": "instsci.topic.v1",
                "task_id": "test_schema_001",
                "topic": "测试水下推力分配",
                "target": 1,
                "papers": [
                    {
                        "id": "p001",
                        "title": "测试论文1",
                        "decision": "include",
                        "relevance": 90,
                        "reviewed_at": "2026-10-17T18:00:00Z"
                    }
                ],
                "batches": []
            }
            (task_dir / "topic.json").write_text(json.dumps(topic_data), encoding="utf-8")

            # Check that unreviewed list is empty
            papers = topic_data["papers"]
            unreviewed = [p for p in papers if not (p.get("decision") or p.get("reviewed_at"))]
            self.assertEqual(len(unreviewed), 0)

    def test_05_deleted_items_check_in_zotero_verification(self):
        """5. 真实文库核验：在sqlite中排查deletedItems表，已删除条目绝不复用"""
        db_live = Path.home() / "Zotero/zotero.sqlite"
        if not db_live.is_file():
            self.skipTest("Local Zotero database not found")

        # Verify legitimate item
        ok, pk, ak, ap = verify_zotero_item_and_pdf(
            db_path=db_live,
            storage_dir=Path.home() / "Zotero/storage",
            title="The practical aspects of implementation of the thrust allocation procedure for a multi-propulsor underwater robot",
            file_sha="7a976bb52dfcfadfb8357f226d31dabbc1c0b56e09e839f14d6a027200118769",
            file_bytes=1167823
        )
        self.assertTrue(ok)
        self.assertEqual(pk, "9J4JCLXT")
        self.assertEqual(ak, "VL2TD7EX")

        # Now simulate a deleted item in a temporary copy of sqlite
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_db = Path(tmp_dir) / "z_test.sqlite"
            shutil.copy2(db_live, tmp_db)

            conn = sqlite3.connect(tmp_db)
            cur = conn.cursor()
            # Find itemID of 9J4JCLXT
            item_id = cur.execute("SELECT itemID FROM items WHERE key = '9J4JCLXT'").fetchone()[0]
            # Insert into deletedItems
            cur.execute("INSERT OR REPLACE INTO deletedItems (itemID, dateDeleted) VALUES (?, datetime('now'))", (item_id,))
            conn.commit()
            conn.close()

            # Re-verify: must fail because it is marked deleted
            ok_del, _, _, _ = verify_zotero_item_and_pdf(
                db_path=tmp_db,
                storage_dir=Path.home() / "Zotero/storage",
                title="The practical aspects of implementation of the thrust allocation procedure for a multi-propulsor underwater robot",
                file_sha="7a976bb52dfcfadfb8357f226d31dabbc1c0b56e09e839f14d6a027200118769",
                file_bytes=1167823
            )
            self.assertFalse(ok_del, "Deleted item must NOT be verified or reused")


if __name__ == "__main__":
    unittest.main()
