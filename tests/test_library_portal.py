"""Real generated-PDF identity, resume and privacy regressions; no services."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "InstSCI"))
from instsci.library_portal import (build_plan, identify_pdf, prepare_state,
                                    public_url, read_request, receive_pdf, safe_origin)

TITLE = "A synthetic study of underwater actuator allocation"
RECORDS = [{"title": TITLE, "doi": "10.1234/workflow.fixture"}]
SPEC = {"institution": "Test institution", "library_homepage": "https://library.example.edu/"}


class LibraryPortalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.run = self.work / "run"
        self.run.mkdir()

    def pdf(self, text, name="candidate.pdf"):
        p = self.work / name
        with pymupdf.open() as d:
            page = d.new_page()
            page.insert_text((40, 50), text, fontsize=10)
            d.save(p)
        return p

    def test_title_in_references_does_not_match(self):
        p = self.pdf("An unrelated synthetic paper\nAbstract\nOther content\nReferences\n" + TITLE)
        self.assertIsNone(identify_pdf(p, RECORDS))

    def test_target_doi_in_references_does_not_match(self):
        p = self.pdf("A different paper\nAbstract\nReferences\n10.1234/workflow.fixture")
        self.assertIsNone(identify_pdf(p, RECORDS))

    def test_wrong_doi_with_same_title_is_unverified(self):
        p = self.pdf(TITLE + "\n10.1234/wrong\nAbstract\nTest")
        self.assertIsNone(identify_pdf(p, RECORDS))

    def test_corrupt_and_scan_fail_closed(self):
        p = self.work / "corrupt.pdf"
        p.write_bytes(b"<html>login</html>")
        self.assertIsNone(identify_pdf(p, RECORDS))
        self.assertIsNone(identify_pdf(self.pdf(""), RECORDS))

    def test_identity_and_bytes_and_duplicate_rejection(self):
        p = self.pdf(TITLE + "\nAuthor One\n10.1234/workflow.fixture\nAbstract\nTest")
        data = p.read_bytes()
        self.assertEqual(identify_pdf(p, RECORDS), RECORDS[0])
        state = prepare_state(self.run, SPEC, RECORDS, False)
        shot = self.work / "checkpoint.png"
        shot.write_bytes(b"private synthetic evidence marker")
        self.assertTrue(receive_pdf(p, self.run, state, str(shot)))
        row = state["downloads"][0]
        self.assertEqual(row["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(Path(row["pdf_path"]).read_bytes(), data)
        self.assertFalse(row["route_chain_verified"])
        self.assertFalse(state["entitlement_verified"])
        duplicate = self.pdf(TITLE + "\nAuthor One\nAbstract\nTest", "duplicate.pdf")
        self.assertFalse(receive_pdf(duplicate, self.run, state, str(shot)))
        self.assertEqual(len(state["downloads"]), 1)
        self.assertEqual(prepare_state(self.run, SPEC, RECORDS, True)["downloads"], state["downloads"])
        Path(row["pdf_path"]).write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            prepare_state(self.run, SPEC, RECORDS, True)

    def test_resume_rejects_swapped_record_identity(self):
        p = self.pdf(TITLE + "\nAbstract\nTest")
        state = prepare_state(self.run, SPEC, RECORDS, False)
        shot = self.work / "evidence.png"
        shot.write_bytes(b"evidence")
        self.assertTrue(receive_pdf(p, self.run, state, str(shot)))
        state["downloads"][0]["doi"] = "10.1234/another"
        (self.run / "library_state.json").write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(ValueError):
            prepare_state(self.run, SPEC, RECORDS, True)

    def test_receipt_requires_visual_checkpoint(self):
        p = self.pdf(TITLE + "\nAbstract\nTest")
        state = prepare_state(self.run, SPEC, RECORDS, False)
        self.assertFalse(receive_pdf(p, self.run, state, ""))

    def test_resume_cannot_promote_unverified_rows_to_complete(self):
        state = prepare_state(self.run, SPEC, RECORDS, False)
        state["downloads"] = [{"file_status": "unverified", "doi": RECORDS[0]["doi"]}]
        (self.run / "library_state.json").write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(ValueError):
            prepare_state(self.run, SPEC, RECORDS, True)

    def test_resume_different_school_and_no_overwrite(self):
        prepare_state(self.run, SPEC, RECORDS, False)
        with self.assertRaises(ValueError):
            prepare_state(self.run, SPEC, RECORDS, False)
        with self.assertRaises(ValueError):
            prepare_state(self.run, {**SPEC, "institution": "Other school"}, RECORDS, True)

    def test_public_entry_rejects_auth_urls(self):
        for url in ["file:///secret", "https://user:pass@example.edu/", "https://example.edu/?token=secret", ""]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                public_url(url)

    def test_signed_download_locations_are_not_exported(self):
        self.assertEqual(safe_origin('https://user:private@publisher.example/file/secret?token=private#private'),
                         'https://publisher.example')

    def test_no_institution_default_and_duplicate_records(self):
        conf, rec = self.work / "library.json", self.work / "papers.json"
        conf.write_text(json.dumps(SPEC), encoding="utf-8")
        rec.write_text(json.dumps(RECORDS), encoding="utf-8")
        self.assertEqual(read_request(conf, rec), (SPEC, RECORDS))
        rec.write_text(json.dumps(RECORDS * 2), encoding="utf-8")
        with self.assertRaises(ValueError):
            read_request(conf, rec)
        conf.write_text(json.dumps({"library_homepage": SPEC["library_homepage"]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            read_request(conf, rec)

    def test_plan_is_offline_and_does_not_claim_download_or_login(self):
        conf, rec = self.work / "library.json", self.work / "papers.json"
        conf.write_text(json.dumps(SPEC), encoding="utf-8")
        rec.write_text(json.dumps(RECORDS), encoding="utf-8")
        script = (
            "import runpy,socket,sys\n"
            "def deny(*a,**k): raise RuntimeError('network denied')\n"
            "socket.socket.connect=deny; socket.create_connection=deny\n"
            "sys.argv=" + repr(["library_portal", "--library-config", str(conf), "--records", str(rec),
                                "--output", str(self.work / "not-created"), "--plan-only"]) + "\n"
            "sys.path.insert(0," + repr(str(ROOT / "InstSCI")) + ")\n"
            "runpy.run_module('instsci.library_portal',run_name='__main__')"
        )
        result = subprocess.run([sys.executable, "-B", "-c", script], capture_output=True,
                                text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["status"], "planned_not_downloaded")
        self.assertEqual(value["downloaded"], 0)
        self.assertFalse(value["entitlement_verified"])
        self.assertFalse((self.work / "not-created").exists())


if __name__ == "__main__":
    unittest.main()
