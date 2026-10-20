
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

from workflow_runner import cmd_recover


class TestCorruptRecoveryCodexScenario(unittest.TestCase):
    """
    Verifies that cmd_recover correctly rejects corrupt zotero_mapping.json:
    When batch_state is COMPLETED but zotero_mapping.json is 'not valid json',
    it must NOT report verified_consistent_dirs=1 and must NOT report recovered.
    """

    def test_corrupt_mapping_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp_bridge:
            b_p = Path(tmp_bridge)
            item_dir = b_p / "items" / "item_001"
            item_dir.mkdir(parents=True)

            # Write valid empty ledger.json
            (b_p / "ledger.json").write_text(json.dumps({
                "schema_version": "1.0",
                "records": {},
                "last_updated": "2026-10-17T00:00:00Z"
            }), encoding="utf-8")

            # Write COMPLETED state
            (item_dir / "batch_state.json").write_text(json.dumps({
                "status": "COMPLETED",
                "item_key": "item_001"
            }), encoding="utf-8")

            # Write corrupt mapping file as specified in Codex check
            (item_dir / "zotero_mapping.json").write_text("not valid json", encoding="utf-8")

            res = cmd_recover(b_p)
            self.assertEqual(res["verified_consistent_dirs"], 0)
            self.assertEqual(len(res["inconsistent_or_corrupt_dirs"]), 1)
            self.assertEqual(res["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
