
import unittest
import tempfile
import json
import os
import sys
from pathlib import Path

MODULE_DIR = Path(r"E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_default_workflow")
T004_DIR = MODULE_DIR.parent
sys.path.insert(0, str(MODULE_DIR))
sys.path.insert(0, str(T004_DIR))

from workflow_runner import cmd_run, cmd_sync, cmd_from_task, cmd_import_local, cmd_recover, cmd_status
from mutex_lock import WorkflowLock, WorkflowLockError


class TestCrossEntrySelfLockResolution(unittest.TestCase):
    """
    Verifies that calling one public entry point (run/sync/from-task/import-local)
    never self-locks when internally executing workflows, while cross-process
    and concurrent inter-instance lock protection remains strictly enforced.
    """

    def test_sync_into_from_task_no_self_lock(self):
        """sync passing a task directory internally calls from_task logic without self-locking."""
        with tempfile.TemporaryDirectory() as tmp_task, tempfile.TemporaryDirectory() as tmp_bridge:
            t_p = Path(tmp_task)
            (t_p / "topic.json").write_text(json.dumps({
                "schema": "instsci.topic.v1",
                "task_id": "test_sync_task",
                "papers": [],
                "batches": []
            }))
            (t_p / "zotero_manifest.json").write_text("[]")

            # Must succeed without WorkflowLockError
            rc, res = cmd_sync(manifest_or_task=t_p, output_dir=tmp_bridge, commit=False)
            self.assertEqual(rc, 0)
            self.assertEqual(res["status"], "empty")

    def test_sync_into_import_local_no_self_lock(self):
        """sync passing a manifest file internally calls import_local logic without self-locking."""
        with tempfile.TemporaryDirectory() as tmp_dir, tempfile.TemporaryDirectory() as tmp_bridge:
            man_file = Path(tmp_dir) / "manifest.json"
            man_file.write_text("[]")

            rc, res = cmd_sync(manifest_or_task=man_file, output_dir=tmp_bridge, commit=False)
            self.assertEqual(rc, 0)
            self.assertEqual(res["status"], "dry_run")
            self.assertEqual(res["total_input"], 0)

    def test_concurrent_instance_is_strictly_blocked(self):
        """Cross-instance or concurrent process acquiring the same target is blocked."""
        with tempfile.TemporaryDirectory() as tmp_bridge:
            lock1 = WorkflowLock(tmp_bridge, command="worker_1")
            lock1.acquire()
            try:
                lock2 = WorkflowLock(tmp_bridge, command="worker_2")
                with self.assertRaises(WorkflowLockError):
                    lock2.acquire()
            finally:
                lock1.release()


if __name__ == "__main__":
    unittest.main()
