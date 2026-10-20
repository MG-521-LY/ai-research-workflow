"""Public-entry regressions with generated PDFs; no private fixtures or services."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "workflow.py"


def make_pdf(path, text="Workflow fixture. This is synthetic, not a research paper."):
    with fitz.open() as doc:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
        doc.save(path)


class WorkflowCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="workflow test ")
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.papers = self.work / "papers with spaces"
        self.papers.mkdir()
        self.pdf = self.papers / "sample.PDF"
        make_pdf(self.pdf)
        self.manifest = self.work / "manifest.json"

    def cli(self, *args, expected=0):
        # Deny networking in the actual child process, including imported code.
        wrapper = (
            "import runpy,socket,sys\n"
            "def blocked(*a,**k): raise RuntimeError('Network disabled in offline test')\n"
            "socket.socket.connect=blocked\nsocket.create_connection=blocked\n"
            "socket.getaddrinfo=blocked\nsys.argv="
            + repr([str(CLI), *map(str, args)])
            + "\nrunpy.run_path(sys.argv[0],run_name='__main__')"
        )
        proc = subprocess.run(
            [sys.executable, "-B", "-c", wrapper], cwd=self.work,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(proc.returncode, expected, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def prepare(self):
        return self.cli("manifest", "--input-dir", self.papers, "--output", self.manifest,
                        "--question", "What can this fixture establish?")

    def test_doctor_works_without_site_packages_and_prints_no_secret(self):
        proc = subprocess.run(
            [sys.executable, "-S", "-B", str(CLI), "doctor", "--profile", "research"],
            env={**os.environ, "PYTHONUTF8": "1", "DEEPSEEK_API_KEY": "DO_NOT_ECHO_SECRET"},
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        report = json.loads(proc.stdout)
        self.assertIn("pypdfium2", report["missing_required"])
        self.assertIn("tantivy", report["missing_required"])
        self.assertNotIn("DO_NOT_ECHO_SECRET", proc.stdout + proc.stderr)

    def test_manifest_deduplicates_bytes_and_marks_metadata_unreviewed(self):
        shutil.copyfile(self.pdf, self.papers / "duplicate.pdf")
        report = self.prepare()
        data = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(report["unique_files"], 1)
        self.assertEqual(report["duplicate_files"], 1)
        self.assertEqual(data["items"][0]["metadata_status"], "needs_review")
        self.assertIsNone(data["items"][0]["title"])
        self.assertNotIn("zotero_parent_key", data["items"][0])
        self.assertEqual(data["items"][0]["sha256"], hashlib.sha256(self.pdf.read_bytes()).hexdigest())

    def test_valid_manifest_resolves_paths_from_manifest_not_cwd(self):
        self.prepare()
        data = json.loads(self.manifest.read_text(encoding="utf-8"))
        data["items"][0]["pdf_path"] = "papers with spaces/sample.PDF"
        self.manifest.write_text(json.dumps(data), encoding="utf-8")
        result = self.cli("validate", "--manifest", self.manifest)
        self.assertTrue(result["ok"])
        self.assertEqual(result["valid_files"], 1)

    def test_changed_pdf_fails_validation(self):
        self.prepare()
        self.pdf.unlink()
        make_pdf(self.pdf, "Changed bytes and content.")
        result = self.cli("validate", "--manifest", self.manifest, expected=1)
        self.assertTrue(any("sha256" in e for e in result["errors"]))

    def test_missing_pdf_fails_validation(self):
        self.prepare()
        self.pdf.unlink()
        result = self.cli("validate", "--manifest", self.manifest, expected=1)
        self.assertEqual(result["valid_files"], 0)

    def test_duplicate_paper_id_fails_validation(self):
        self.prepare()
        second = self.papers / "different.pdf"
        make_pdf(second, "Another synthetic fixture.")
        data = json.loads(self.manifest.read_text(encoding="utf-8"))
        item = {**data["items"][0], "pdf_path": str(second),
                "sha256": hashlib.sha256(second.read_bytes()).hexdigest(),
                "file_bytes": second.stat().st_size}
        data["items"].append(item)
        self.manifest.write_text(json.dumps(data), encoding="utf-8")
        result = self.cli("validate", "--manifest", self.manifest, expected=1)
        self.assertTrue(any("paper_id" in e for e in result["errors"]))

    def test_existing_manifest_is_never_overwritten(self):
        self.prepare()
        before = self.manifest.read_bytes()
        self.cli("manifest", "--input-dir", self.papers, "--output", self.manifest,
                 "--question", "Different question", expected=1)
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_invalid_pdf_does_not_publish_partial_manifest(self):
        (self.papers / "broken.pdf").write_bytes(b"Not a PDF")
        result = self.cli("manifest", "--input-dir", self.papers, "--output", self.manifest,
                          "--question", "Question", expected=1)
        self.assertFalse(result["ok"])
        self.assertFalse(self.manifest.exists())

    def test_image_only_pdf_is_flagged_for_ocr(self):
        self.pdf.unlink()
        make_pdf(self.pdf, "")
        self.prepare()
        result = self.cli("validate", "--manifest", self.manifest)
        self.assertTrue(any("OCR" in w for w in result["warnings"]))

    def test_empty_directory_cannot_produce_a_manifest(self):
        self.pdf.unlink()
        self.cli("manifest", "--input-dir", self.papers, "--output", self.manifest,
                 "--question", "Question", expected=1)
        self.assertFalse(self.manifest.exists())

    def test_bad_json_is_a_structured_failure(self):
        self.manifest.write_text("{bad json", encoding="utf-8")
        result = self.cli("validate", "--manifest", self.manifest, expected=1)
        self.assertFalse(result["ok"])

    def test_missing_digest_does_not_count_as_validated(self):
        self.prepare()
        data = json.loads(self.manifest.read_text(encoding="utf-8"))
        data["items"][0].pop("sha256")
        self.manifest.write_text(json.dumps(data), encoding="utf-8")
        self.cli("validate", "--manifest", self.manifest, expected=1)

    def test_explicitly_rejected_input_cannot_pass_preflight(self):
        self.prepare()
        data = json.loads(self.manifest.read_text(encoding="utf-8"))
        data["items"][0]["status"] = "UNVERIFIED"
        self.manifest.write_text(json.dumps(data), encoding="utf-8")
        self.cli("validate", "--manifest", self.manifest, expected=1)

    def test_false_hash_verification_cannot_pass_preflight(self):
        self.prepare()
        data = json.loads(self.manifest.read_text(encoding="utf-8"))
        data["items"][0]["hash_verified"] = False
        self.manifest.write_text(json.dumps(data), encoding="utf-8")
        self.cli("validate", "--manifest", self.manifest, expected=1)

    def test_status_reports_partial_without_mutating_run(self):
        run = self.work / "run"
        run.mkdir()
        path = run / "run_result.json"
        path.write_text(json.dumps({"execution_status": "PARTIAL", "status": "partial",
                                   "execution_finished": True, "stages_status": {"qa": "failed"}}), encoding="utf-8")
        before = path.read_bytes()
        result = self.cli("status", "--output", run, expected=1)
        self.assertEqual(result["execution_status"], "PARTIAL")
        self.assertFalse(result["execution_success"])
        self.assertEqual(path.read_bytes(), before)

    def test_completed_status_requires_matching_report_bytes(self):
        run = self.work / "run"
        run.mkdir()
        report = run / "RESEARCH_SYNTHESIS_REPORT.md"
        report.write_text("Fixture only", encoding="utf-8")
        stages = {name: "completed" for name in (
            "manifest_and_corpus", "retrieval_and_qa", "single_paper_cards", "evidence_cross_check",
            "cross_paper_matrix", "hypotheses_generation", "external_search_critique",
            "verification_plan", "synthesis_report")}
        state = {"execution_status": "COMPLETED", "status": "completed", "execution_finished": True,
                 "stages_status": stages, "report_path": str(report),
                 "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest()}
        (run / "run_result.json").write_text(json.dumps(state), encoding="utf-8")
        self.assertTrue(self.cli("status", "--output", run)["execution_success"])
        report.write_text("Changed after completion", encoding="utf-8")
        self.assertFalse(self.cli("status", "--output", run, expected=1)["execution_success"])

    def test_demo_runs_real_corpus_and_keeps_downstream_pending(self):
        result = self.cli("demo")
        self.assertTrue(result["ok"])
        self.assertEqual(result["valid_papers"], 1)
        self.assertGreater(result["total_chunks"], 0)
        self.assertEqual(result["downstream_executed"], False)
        self.assertEqual(result["network"], "blocked")


if __name__ == "__main__":
    unittest.main()
