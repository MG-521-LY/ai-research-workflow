from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

import pymupdf

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "instsci-topic-library" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import topic_library as topic


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "task"
        topic.init(self.root, "Synthetic fixture: research topic screening", 2)

    def cmd(self, name, *args):
        return topic.run(topic.parser().parse_args([name, "--task", str(self.root), *map(str, args)]))

    def input(self, name, value):
        path = Path(self.tmp.name) / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def candidates(self, n=3, portal=""):
        rows = [{"title": f"Synthetic paper {i}", "doi": f"10.1007/fixture-{i}",
                 "authors": [f"Author {i}"], "abstract": f"Observed synthetic abstract {i}", "portal": portal}
                for i in range(n)]
        result = self.cmd("ingest", "--input", self.input("candidates.json", rows))
        ids = [p["id"] for p in result["papers"]]
        self.cmd("review", "--input", self.input("reviews.json", [
            {"id": pid, "decision": "include", "relevance": 99-i, "evidence": "abstract", "reason": f"Synthetic fixture reason {i}"}
            for i, pid in enumerate(ids)]))
        return ids

    def pdf(self, label):
        path = Path(self.tmp.name) / f"{label}.pdf"
        with pymupdf.open() as doc:
            doc.new_page().insert_text((72, 72), f"Synthetic fixture only: {label}")
            doc.save(path)
        return path

    def complete(self, batch, statuses=None, same_pdf=False, evidence="oa_direct"):
        with topic.transaction(self.root) as data:
            b = topic.batch_by_id(data, batch["id"])
            b.update(phase="finished", started_at=topic.screening.now())
            rows = []
            common = self.pdf("common") if same_pdf else None
            for index, pid in enumerate(b["ids"]):
                p = topic.screening.find(data, pid)
                status = (statuses or ["success"] * len(b["ids"]))[index]
                rows.append({"doi": p.get("doi", ""), "record_id": pid, "title": p["title"],
                             "first_author": p.get("provenance", {}).get("first_author", ""),
                             "file_status": "success" if status == "success" else "missing", "standard_status": status,
                             "result_evidence": evidence, "verified_match": status == "success", "title_match": True,
                             "pdf_path": str(common or self.pdf(pid)) if status == "success" else ""})
        path = Path(batch["manifest"])
        if batch["route"] == "doi":
            # Exercise InstSci's real manifest writer. These are labeled local fixtures, not downloads.
            from instsci.cli import _write_papers_manifest
            _write_papers_manifest(path.parent.parent, rows)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            topic.screening.write_json(path, rows)
        return path

    def test_failed_candidate_is_replaced_up_to_target(self):
        self.candidates()
        first = self.cmd("plan", "--limit", 2)["batch"]
        self.complete(first, ["success", "capture_failed"])
        self.assertEqual(self.cmd("status")["summary"]["verified_downloaded"], 1)
        second = self.cmd("plan", "--limit", 10)["batch"]
        self.assertEqual(len(second["ids"]), 1)
        self.assertFalse(set(first["ids"]) & set(second["ids"]))
        self.complete(second)
        self.assertTrue(self.cmd("status")["summary"]["complete"])
        self.assertIsNone(self.cmd("plan")["batch"])

    def test_reservation_prevents_double_plan(self):
        self.candidates()
        self.cmd("plan")
        self.assertIsNone(self.cmd("plan")["batch"])

    def test_doi_input_is_read_by_actual_upstream_parser(self):
        self.candidates(2)
        b = self.cmd("plan", "--limit", 2)["batch"]
        from instsci.cli import _read_paper_records
        actual = _read_paper_records(Path(b["argv"][4]))
        self.assertEqual([p.doi for p in actual], ["10.1007/fixture-0", "10.1007/fixture-1"])
        metadata = json.loads(Path(b["argv"][4]).with_name("selected_metadata.json").read_text())
        self.assertEqual(metadata[0]["title"], "Synthetic paper 0")

    def test_target_reduction_keeps_files_and_add_uses_current_success(self):
        self.candidates()
        b = self.cmd("plan", "--limit", 2)["batch"]
        self.complete(b)
        self.cmd("status")
        self.assertTrue(self.cmd("set-target", "--count", 1)["complete"])
        self.assertIsNone(self.cmd("plan")["batch"])
        self.assertEqual(self.cmd("set-target", "--add", 3)["target"], 5)

    def test_removed_pdf_is_no_longer_success(self):
        self.candidates()
        path = self.complete(self.cmd("plan")["batch"])
        self.cmd("status")
        Path(json.loads(path.read_text())[0]["pdf_path"]).unlink()
        self.assertEqual(self.cmd("status")["summary"]["verified_downloaded"], 0)

    def test_valid_but_changed_pdf_does_not_silently_replace_baseline(self):
        self.candidates()
        path = self.complete(self.cmd("plan")["batch"])
        self.cmd("status")
        pdf = Path(json.loads(path.read_text())[0]["pdf_path"])
        pdf.write_bytes(self.pdf("replacement").read_bytes())
        self.assertEqual(self.cmd("status")["summary"]["verified_downloaded"], 0)

    def test_duplicate_pdf_content_is_counted_once(self):
        self.candidates()
        self.complete(self.cmd("plan", "--limit", 2)["batch"], same_pdf=True)
        self.assertEqual(self.cmd("status")["summary"]["verified_downloaded"], 1)

    def test_http_preflight_is_not_final_success(self):
        self.candidates()
        self.complete(self.cmd("plan")["batch"], evidence="http_preflight")
        self.assertEqual(self.cmd("status")["summary"]["verified_downloaded"], 0)

    def test_bad_manifest_does_not_overwrite_task(self):
        self.candidates()
        b = self.cmd("plan")["batch"]
        manifest = self.complete(b)
        before = (self.root / "topic.json").read_bytes()
        manifest.write_text('{"broken": true}', encoding="utf-8")
        with self.assertRaises(ValueError):
            self.cmd("status")
        self.assertEqual((self.root / "topic.json").read_bytes(), before)

    def test_unexpected_success_is_not_counted_and_blocks_new_batch(self):
        self.candidates()
        path = self.complete(self.cmd("plan")["batch"])
        rows = json.loads(path.read_text())
        rows[0]["doi"] = "10.1007/not-selected"
        topic.screening.write_json(path, rows)
        s = self.cmd("status")["summary"]
        self.assertEqual(s["verified_downloaded"], 0)
        self.assertTrue(s["integrity_issues"])
        self.assertIsNone(self.cmd("plan")["batch"])

    def test_missing_manifest_is_not_assumed_to_mean_no_download(self):
        self.candidates()
        b = self.cmd("plan")["batch"]
        with topic.transaction(self.root) as data:
            topic.batch_by_id(data, b["id"]).update(phase="finished", started_at="synthetic interrupted run")
        self.assertTrue(self.cmd("status")["summary"]["integrity_issues"])
        self.assertIsNone(self.cmd("plan")["batch"])
        self.cmd("close-batch", "--batch", b["id"], "--reason", "Synthetic inspection confirms no further process")
        replacement = self.cmd("plan")["batch"]
        self.assertNotEqual(replacement["ids"], b["ids"])

    def test_auth_blocker_pauses_replacement_until_explicit_retry(self):
        ids = self.candidates()
        b = self.cmd("plan")["batch"]
        self.complete(b, ["auth_required"], evidence="browser_verified")
        self.assertIsNone(self.cmd("plan")["batch"])
        self.cmd("retry", "--id", ids[0], "--reason", "Synthetic user verification has completed")
        self.assertEqual(self.cmd("plan")["batch"]["ids"], b["ids"])

    def test_cannot_release_running_process(self):
        self.candidates()
        b = self.cmd("plan")["batch"]
        with topic.transaction(self.root) as data:
            topic.batch_by_id(data, b["id"]).update(phase="started", runner_pid=os.getpid(), started_at="fixture")
        with self.assertRaises(ValueError):
            self.cmd("close-batch", "--batch", b["id"], "--reason", "cannot close active fixture")

    def test_acquire_refuses_stale_target_without_running_backend(self):
        self.candidates()
        b = self.cmd("plan", "--limit", 2)["batch"]
        self.cmd("set-target", "--count", 1)
        with self.assertRaisesRegex(ValueError, "shrank"):
            self.cmd("acquire", "--batch", b["id"])

    def test_cannot_reexecute_started_batch(self):
        self.candidates()
        b = self.cmd("plan")["batch"]
        with topic.transaction(self.root) as data:
            topic.batch_by_id(data, b["id"]).update(phase="started", started_at="fixture")
        with self.assertRaisesRegex(ValueError, "already started"):
            self.cmd("acquire", "--batch", b["id"])

    def test_abstract_evidence_requires_real_abstract_transactionally(self):
        row = {"title": "Only a synthetic title", "doi": "10.1007/title"}
        pid = self.cmd("ingest", "--input", self.input("one.json", [row]))["papers"][0]["id"]
        before = (self.root / "topic.json").read_bytes()
        invalid = [{"id": pid, "decision": "include", "evidence": "abstract", "relevance": 95, "reason": "fixture"}]
        with self.assertRaises(ValueError):
            self.cmd("review", "--input", self.input("badreview.json", invalid))
        self.assertEqual((self.root / "topic.json").read_bytes(), before)
        invalid[0]["evidence"] = "title"
        self.cmd("review", "--input", self.input("title-review.json", invalid))
        self.assertEqual(self.cmd("status")["papers"][0]["relevance"], 70)

    def test_metadata_change_requires_review_again(self):
        self.candidates(1)
        b = self.cmd("plan")["batch"]
        self.complete(b)
        self.assertEqual(self.cmd("status")["summary"]["verified_downloaded"], 1)
        self.cmd("ingest", "--input", self.input("updated.json", [{"title": "Synthetic paper 0", "doi": "10.1007/fixture-0", "abstract": "Changed synthetic content"}]))
        self.assertEqual(self.cmd("status")["summary"]["verified_downloaded"], 0)

    def test_conflicting_dois_are_not_merged(self):
        rows = [{"title": "Same title", "authors": ["Same author"], "doi": f"10.1007/other-{i}"} for i in range(2)]
        self.cmd("ingest", "--input", self.input("same.json", rows))
        self.assertEqual(self.cmd("status")["summary"]["candidates"], 2)

    def test_doi_merge_preserves_multiple_sources_and_provider_counts(self):
        self.cmd("ingest", "--input", self.input("s1.json", [{"title": "Same fixture", "doi": "10.1007/one", "sources": ["crossref"], "citation_counts": {"crossref": 2}}]))
        self.cmd("ingest", "--input", self.input("s2.json", [{"title": "Same fixture", "doi": "https://doi.org/10.1007/one", "sources": ["openalex"], "citation_counts": {"openalex": 5}}]))
        papers = self.cmd("status")["papers"]
        self.assertEqual(len(papers), 1)
        self.assertEqual(set(papers[0]["provenance"]["sources"]), {"crossref", "openalex"})
        self.assertEqual(papers[0]["provenance"]["citation_counts"], {"crossref": 2, "openalex": 5})

    def test_heat_is_optional_and_unknown_metrics_are_neutral(self):
        self.candidates(1)
        p = self.cmd("status")["papers"][0]
        self.assertEqual(p["score"], p["relevance"])
        self.assertEqual(p["heat"], .5)

    def test_chinese_records_preserve_title_author_and_use_browser_route(self):
        self.candidates(1, "cnki")
        b = self.cmd("plan")["batch"]
        records = json.loads(Path(b["argv"][4]).read_text(encoding="utf-8"))
        self.assertEqual(b["route"], "cnki")
        self.assertEqual(records[0]["first_author"], "Author 0")
        self.assertIn("--navigation-mode", b["argv"])
        path = self.complete(b, evidence="browser_verified")
        self.assertEqual(self.cmd("status")["summary"]["verified_downloaded"], 1)
        rows = json.loads(path.read_text())
        rows[0]["title_match"] = False
        topic.screening.write_json(path, rows)
        self.assertEqual(self.cmd("status")["summary"]["verified_downloaded"], 0)

    def test_zotero_export_only_includes_current_eligible_verified_papers(self):
        ids = self.candidates()
        b = self.cmd("plan", "--limit", 2)["batch"]
        self.complete(b)
        self.cmd("review", "--input", self.input("exclude.json", [{"id": ids[0], "decision": "exclude", "evidence": "abstract", "relevance": 20, "reason": "Synthetic hard criterion excludes this paper"}]))
        result = self.cmd("export")
        self.assertEqual(result["zotero_selected"], 1)
        self.assertEqual(result["zotero_actions"], 1)
        rows = json.loads((self.root / "zotero_manifest.json").read_text())
        self.assertEqual(rows[0]["topic_candidate_id"], ids[1])

    def test_chinese_zotero_handoff_recovers_known_doi_from_candidate(self):
        self.candidates(1, "cnki")
        path = self.complete(self.cmd("plan")["batch"], evidence="browser_verified")
        rows = json.loads(path.read_text())
        rows[0].pop("doi")  # Actual CNKI/Wanfang manifests do not preserve it.
        topic.screening.write_json(path, rows)
        original = path.read_bytes()
        self.assertEqual(self.cmd("export")["zotero_actions"], 1)
        self.assertEqual(path.read_bytes(), original)

    def test_doi_batches_are_separated_by_upstream_publisher(self):
        ids = self.candidates(1)
        result = self.cmd("ingest", "--input", self.input("elsevier.json", [{"title": "Another fixture", "authors": ["A"], "doi": "10.1016/j.fixture.2026.01", "abstract": "Fixture abstract"}]))
        pid = result["papers"][0]["id"]
        self.cmd("review", "--input", self.input("elsevier-review.json", [{"id": pid, "decision": "include", "relevance": 98, "evidence": "abstract", "reason": "Fixture"}]))
        batch = self.cmd("plan", "--limit", 10)["batch"]
        self.assertEqual(batch["ids"], ids)
        from instsci.cli import _read_paper_records, _resolve_papers_profile
        self.assertIsNotNone(_resolve_papers_profile(_read_paper_records(Path(batch["input_file"])), "auto"))

    def test_lost_task_state_cannot_be_silently_reinitialized(self):
        (self.root / "topic.json").unlink()
        with self.assertRaises(ValueError):
            topic.init(self.root, "Must not erase previous ownership", 2)

    def test_changed_batch_input_is_rejected_before_backend_launch(self):
        self.candidates(1)
        batch = self.cmd("plan")["batch"]
        Path(batch["input_file"]).write_text("10.1016/wrong\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "input changed"):
            self.cmd("acquire", "--batch", batch["id"])

    def test_updated_first_author_invalidates_old_chinese_pdf_identity(self):
        ids = self.candidates(1, "cnki")
        path = self.complete(self.cmd("plan")["batch"], evidence="browser_verified")
        rows = json.loads(path.read_text())
        rows[0].update(author_disambiguation_used=True, author_match=True)
        topic.screening.write_json(path, rows)
        self.assertEqual(self.cmd("status")["summary"]["verified_downloaded"], 1)
        self.cmd("ingest", "--input", self.input("author-update.json", [{"title": "Synthetic paper 0", "doi": "10.1007/fixture-0", "authors": ["Corrected author"]}]))
        self.cmd("review", "--input", self.input("author-review.json", [{"id": ids[0], "decision": "include", "relevance": 95, "evidence": "abstract", "reason": "Updated author fixture"}]))
        self.assertEqual(self.cmd("status")["summary"]["verified_downloaded"], 0)

    def test_changed_chinese_identity_requires_new_reservation(self):
        ids = self.candidates(1, "cnki")
        batch = self.cmd("plan")["batch"]
        self.cmd("ingest", "--input", self.input("reserved-author.json", [{"title": "Synthetic paper 0", "doi": "10.1007/fixture-0", "authors": ["Another author"]}]))
        self.cmd("review", "--input", self.input("reserved-author-review.json", [{"id": ids[0], "decision": "include", "relevance": 95, "evidence": "abstract", "reason": "Updated fixture"}]))
        with self.assertRaisesRegex(ValueError, "identity changed"):
            self.cmd("acquire", "--batch", batch["id"])

    def test_wanfang_fallback_does_not_carry_cnki_url(self):
        self.candidates(1, "cnki")
        self.cmd("ingest", "--input", self.input("cnki-url.json", [{"title": "Synthetic paper 0", "doi": "10.1007/fixture-0", "url": "https://kns.cnki.net/fixture"}]))
        batch = self.cmd("plan", "--route", "wanfang")["batch"]
        from instsci.wanfang_session import load_wanfang_batch
        records = load_wanfang_batch(Path(batch["input_file"]))
        self.assertEqual(records[0]["url"], "")


if __name__ == "__main__":
    unittest.main()
