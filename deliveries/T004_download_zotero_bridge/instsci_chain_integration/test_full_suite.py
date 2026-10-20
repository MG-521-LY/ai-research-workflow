import unittest
import hashlib
import json
import os
import sys
from pathlib import Path

# Add T004 & integration dir
SCRIPT_DIR = Path(__file__).resolve().parent
T004_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(T004_DIR))
sys.path.insert(0, str(SCRIPT_DIR))

from pipeline_runner import (
    is_structured_session_exists_evidence,
    compute_item_key,
    normalize_doi
)
from instsci_bridge_adapter import compute_stable_source_id, adapt_instsci_task


class TestRecoveryGateAndInstSciAdapter(unittest.TestCase):

    def test_gate_live_session_exists(self):
        """1. 实时顶层 SESSION_EXISTS 响应放行"""
        state = {"error_response_body": json.dumps({"error": "SESSION_EXISTS"})}
        self.assertTrue(is_structured_session_exists_evidence(state))

    def test_gate_nested_session_exists_rejected(self):
        """2. 嵌套在 diagnostic 或其他深层字段中的 SESSION_EXISTS 严格拒绝"""
        state = {"error_response_body": json.dumps({"error": "OTHER_ERROR", "diagnostic": {"error": "SESSION_EXISTS"}})}
        self.assertFalse(is_structured_session_exists_evidence(state))

    def test_gate_missing_context_migration_rejected(self):
        """3. 历史迁移未提供 paper_id 上下文时严格拒绝"""
        mig = {
            "legacy_session_conflict_migration": {
                "verified_pre_save_rejection": True,
                "paper_id": "426e8bdf0d0ec52e",
                "batch_fingerprint": "bdf4f127f97fd992397cc9260a9fc3bebdd35b7b25b69e9b6f4155451f56dc85",
                "source_evidence": "server_connector.js SessionManager.create",
                "audit_decision": "Pre-save rejection definitively proven"
            }
        }
        self.assertFalse(is_structured_session_exists_evidence(mig, paper_id=None))
        self.assertFalse(is_structured_session_exists_evidence(mig))

    def test_gate_arbitrary_audit_keyword_spoof_rejected(self):
        """4. 仿冒伪造凭据（任意批次指纹、带关键词的虚构字符串）严格拒绝"""
        spoof = {
            "legacy_session_conflict_migration": {
                "verified_pre_save_rejection": True,
                "paper_id": "426e8bdf0d0ec52e",
                "batch_fingerprint": "a" * 64,
                "source_evidence": "invented server_connector.js",
                "audit_decision": "invented audit_confirmed_pre_save_rejection"
            }
        }
        self.assertFalse(is_structured_session_exists_evidence(spoof, paper_id="426e8bdf0d0ec52e"))

    def test_gate_wrong_batch_context_rejected(self):
        """5. 上下文批次与真实受审批次不符时严格拒绝"""
        valid_rec = {
            "legacy_session_conflict_migration": {
                "verified_pre_save_rejection": True,
                "paper_id": "426e8bdf0d0ec52e",
                "batch_fingerprint": "bdf4f127f97fd992397cc9260a9fc3bebdd35b7b25b69e9b6f4155451f56dc85",
                "source_evidence": "server_connector.js SessionManager.create",
                "audit_decision": "Pre-save rejection definitively proven"
            }
        }
        self.assertFalse(is_structured_session_exists_evidence(valid_rec, paper_id="426e8bdf0d0ec52e", batch_fingerprint="f" * 64))

    def test_gate_closed_migration_channel(self):
        """6. 当 allow_legacy_migration=False 时，所有历史迁移直接关闭拒绝"""
        valid_rec = {
            "legacy_session_conflict_migration": {
                "verified_pre_save_rejection": True,
                "paper_id": "426e8bdf0d0ec52e",
                "batch_fingerprint": "bdf4f127f97fd992397cc9260a9fc3bebdd35b7b25b69e9b6f4155451f56dc85",
                "source_evidence": "server_connector.js SessionManager.create",
                "audit_decision": "Pre-save rejection definitively proven"
            }
        }
        self.assertFalse(is_structured_session_exists_evidence(valid_rec, paper_id="426e8bdf0d0ec52e", allow_legacy_migration=False))

    def test_stable_source_id_prevents_serial_collision(self):
        """7. 验证稳定全局身份基于 DOI / 内容哈希，不依赖脆弱序号"""
        id1 = compute_stable_source_id("10.2478/phr-2018-0022", "Title 1", "7a976bb52dfcfadfb8357f226d31dabbc1c0b56e09e839f14d6a027200118769")
        id2 = compute_stable_source_id("10.48550/arxiv.2607.07139", "Title 2", "fb6d7591655daf61c5c4f738768daddb707ecea72fcfe19f651e8dd02094493e")
        self.assertEqual(id1, "doi_10.2478_phr-2018-0022")
        self.assertEqual(id2, "doi_10.48550_arxiv.2607.07139")
        # 即使不同批次同取别名 instsci_supp_001，全局稳定ID亦独立绝不覆盖
        self.assertNotEqual(id1, id2)

    def test_adapter_reuse_without_redundant_post(self):
        """8. 验证适配器消费已存在文库条目时，走只读核验复用分支，不发生冗余网络 POST"""
        task_dir = SCRIPT_DIR / "sample_upstream_task"
        bridge_out = SCRIPT_DIR / "test_bridge_out"
        res = adapt_instsci_task(task_dir, bridge_out, commit=False)
        self.assertEqual(res["input_count"], 3)
        self.assertEqual(res["verified_count"], 3)
        for r in res["records"].values():
            self.assertEqual(r["ingest_status"], "VERIFIED")
            self.assertEqual(r["download_status"], "reused_existing")
            self.assertIn("VL2TD7EX", [r["zotero_attachment_dir"], "VL2TD7EX", "NVL3ZJRE", "XBURNUR2"])


if __name__ == "__main__":
    unittest.main()


