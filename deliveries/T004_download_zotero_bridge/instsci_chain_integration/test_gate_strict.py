import sys, json, hashlib, re
from pathlib import Path

runner_path = Path(r"E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\pipeline_runner.py")
sys.path.insert(0, str(runner_path.parent))
from pipeline_runner import is_structured_session_exists_evidence

# 1. missing_context_migration_rejected -> True (i.e. returns False when paper_id is missing)
mig_payload = {
    "legacy_session_conflict_migration": {
        "verified_pre_save_rejection": True,
        "paper_id": "426e8bdf0d0ec52e",
        "batch_fingerprint": "bdf4f127f97fd992397cc9260a9fc3bebdd35b7b25b69e9b6f4155451f56dc85",
        "source_evidence": "server_connector.js SessionManager.create",
        "audit_decision": "Pre-save rejection definitively proven"
    }
}
res_no_ctx = is_structured_session_exists_evidence(mig_payload)
assert res_no_ctx is False, f"Expected False without context, got {res_no_ctx}"
print("[PASS] missing_context_migration_rejected = True (successfully rejected when context missing)")

# 2. arbitrary strings in fingerprint / evidence / audit_decision -> rejected
bad_fp = dict(mig_payload["legacy_session_conflict_migration"])
bad_fp["batch_fingerprint"] = "arbitrary_short_string"
assert is_structured_session_exists_evidence({"legacy_session_conflict_migration": bad_fp}, paper_id="426e8bdf0d0ec52e") is False
print("[PASS] Arbitrary short fingerprint rejected")

bad_ev = dict(mig_payload["legacy_session_conflict_migration"])
bad_ev["source_evidence"] = "arbitrary fake evidence without connector source"
assert is_structured_session_exists_evidence({"legacy_session_conflict_migration": bad_ev}, paper_id="426e8bdf0d0ec52e") is False
print("[PASS] Arbitrary fake source evidence rejected")

bad_dec = dict(mig_payload["legacy_session_conflict_migration"])
bad_dec["audit_decision"] = "arbitrary decision"
assert is_structured_session_exists_evidence({"legacy_session_conflict_migration": bad_dec}, paper_id="426e8bdf0d0ec52e") is False
print("[PASS] Arbitrary audit decision rejected")

# 3. Context mismatch -> rejected
assert is_structured_session_exists_evidence(mig_payload, paper_id="a968866c50f19a55") is False
print("[PASS] Paper ID context mismatch rejected")

# 4. Correct context and authentic audit proof -> accepted
assert is_structured_session_exists_evidence(mig_payload, paper_id="426e8bdf0d0ec52e") is True
print("[PASS] Authentic audit proof with matching paper_id accepted")

# 5. Top-level SESSION_EXISTS -> accepted
assert is_structured_session_exists_evidence({"error_response_body": json.dumps({"error": "SESSION_EXISTS"})}) is True
print("[PASS] Top-level SESSION_EXISTS accepted")

# 6. Nested SESSION_EXISTS -> rejected
assert is_structured_session_exists_evidence({"error_response_body": json.dumps({"error": "OTHER_ERROR", "diagnostic": {"error": "SESSION_EXISTS"}})}) is False
print("[PASS] Nested SESSION_EXISTS rejected")

# 7. Unparseable / unknown 409 -> rejected
assert is_structured_session_exists_evidence({"pending_reason": "HTTP错误: 409 Conflict"}) is False
print("[PASS] Unknown 409 rejected")

print("\n[ALL 7 GATE TESTS PASSED CLEANLY!]")
