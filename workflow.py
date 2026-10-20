#!/usr/bin/env python3
"""Portable local entry point. No model, publisher, Zotero or DSH operations."""
import argparse
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
SCHEMA = "instsci.unified_analysis_manifest.v1"
PDF_FIELDS = ("snapshot_pdf", "local_pdf_absolute", "zotero_stored_pdf_path",
              "local_pdf_relative", "pdf_path")
STAGES = ("manifest_and_corpus", "retrieval_and_qa", "single_paper_cards",
          "evidence_cross_check", "cross_paper_matrix", "hypotheses_generation",
          "external_search_critique", "verification_plan", "synthesis_report")
DEPENDENCIES = {
    "pymupdf": "PyMuPDF", "yaml": "PyYAML", "requests": "requests",
    "jsonschema": "jsonschema", "pypdfium2": "pypdfium2", "tantivy": "tantivy",
    "paperqa": "paper-qa (optional)", "mcp": "mcp (optional)",
}


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeError) as exc:
        # Do not echo file content, which might contain private configuration.
        raise ValueError(f"Cannot read JSON: {path} ({type(exc).__name__})") from None


def inspect_pdf(path):
    try:
        import pymupdf
    except ImportError:
        raise ValueError("Install PyMuPDF first: python -m pip install -r requirements.txt") from None
    with path.open("rb") as handle:
        if b"%PDF-" not in handle.read(1024):
            raise ValueError(f"Not a PDF: {path}")
    try:
        with pymupdf.open(path) as doc:
            if doc.is_encrypted:
                raise ValueError("Password-protected PDF; decrypt your own copy before analysis")
            if not doc.is_pdf or doc.page_count < 1:
                raise ValueError("PDF has no readable pages")
            chars = sum(len(page.get_text().strip()) for page in doc)
            return {"page_count": doc.page_count, "text_characters": chars,
                    "text_status": "text_available" if chars else "needs_ocr"}
    except Exception as exc:
        raise ValueError(f"Unreadable PDF: {path} ({type(exc).__name__})") from None


def doctor(profile):
    required = {"pymupdf"}
    if profile in {"research", "paperqa"}:
        required |= {"yaml", "requests", "jsonschema", "pypdfium2", "tantivy"}
    if profile == "paperqa":
        required.add("paperqa")
    if profile == "dsh":
        required = {"mcp"}
    installed = {name: importlib.util.find_spec(name) is not None for name in DEPENDENCIES}
    missing = sorted(name for name in required if not installed[name])
    python_ok = sys.version_info >= (3, 10)
    platform_ok = profile != "dsh" or sys.platform == "win32"
    return {"ok": python_ok and platform_ok and not missing, "profile": profile,
            "python": sys.version.split()[0], "python_supported": python_ok,
            "platform_supported_for_profile": platform_ok,
            "packages": {name: {"distribution": DEPENDENCIES[name], "installed": found,
                                "required": name in required} for name, found in installed.items()},
            "missing_required": missing, "external_operations": 0,
            "note": "Presence check only; service access, native imports and credentials are not verified."}


def create_manifest(input_dir, output, question, topic=""):
    if output.exists():
        raise ValueError(f"Output already exists; choose a new filename: {output}")
    if not question.strip():
        raise ValueError("A non-empty research question is required")
    if not input_dir.is_dir():
        raise ValueError(f"Input directory not found: {input_dir}")
    items, seen, errors, duplicate_paths = [], {}, [], []
    paths = sorted((p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"),
                   key=lambda p: p.as_posix())
    for path in paths:
        try:
            path.resolve().relative_to(input_dir.resolve())
            info = inspect_pdf(path)
            sha = digest(path)
            if sha in seen:
                duplicate_paths.append({"path": str(path), "same_bytes_as": seen[sha]})
                continue
            seen[sha] = str(path)
            try:
                relative = Path(os.path.relpath(path, output.parent)).as_posix()
            except ValueError:  # Different Windows volumes cannot share a relative path.
                relative = path.as_posix()
            items.append({"paper_id": "paper_" + sha, "title": None, "authors": [], "year": None,
                          "source_filename": path.name, "metadata_status": "needs_review",
                          "pdf_path": relative, "sha256": sha, "file_bytes": path.stat().st_size,
                          **info})
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    if not paths:
        errors.append("Input contains no PDF files")
    if errors:
        return {"ok": False, "errors": errors, "output_written": False}
    data = {"schema": SCHEMA, "schema_version": "1.0.0", "question": question.strip(),
            "topic": topic.strip(), "metadata_review_required": True, "items": items,
            "duplicate_files": duplicate_paths}
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents silently replacing another task's reviewed input.
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return {"ok": True, "manifest": str(output), "unique_files": len(items),
            "duplicate_files": len(duplicate_paths), "output_written": True,
            "note": "File integrity only. Review titles, authors, years and versions before research."}


def validate_manifest(path):
    data = read_json(path)
    if isinstance(data, dict):
        if data.get("schema", SCHEMA) != SCHEMA:
            raise ValueError("Unsupported manifest schema")
        items = data.get("items")
    else:
        items = data  # Compatibility with historical array manifests.
    if not isinstance(items, list) or not items:
        raise ValueError("Manifest items must be a non-empty array")
    errors, warnings, ids, hashes = [], [], set(), set()
    valid = 0
    for idx, item in enumerate(items, 1):
        prefix = f"item {idx}"
        start_errors = len(errors)
        if not isinstance(item, dict):
            errors.append(f"{prefix}: expected an object")
            continue
        if str(item.get("status", "")).upper() in {"FAILED", "ERROR", "PENDING_METADATA", "UNVERIFIED"}:
            errors.append(f"{prefix}: input status is explicitly rejected by T005")
        if item.get("hash_verified") is False:
            errors.append(f"{prefix}: hash_verified is false")
        pid = item.get("paper_id") or item.get("id")
        if not isinstance(pid, str) or not pid.strip() or pid in ids:
            errors.append(f"{prefix}: missing or duplicate paper_id")
        else:
            ids.add(pid)
        sha = item.get("sha256")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha):
            errors.append(f"{prefix}: missing or invalid sha256")
        elif sha.lower() in hashes:
            errors.append(f"{prefix}: duplicate PDF bytes (sha256)")
        else:
            hashes.add(sha.lower())
        selected = None
        for field in PDF_FIELDS:
            value = item.get(field)
            if isinstance(value, str) and value.strip():
                candidate = Path(value)
                if not candidate.is_absolute():
                    candidate = path.parent / candidate
                if candidate.is_file():
                    selected = candidate
                    break
        if selected is None:
            errors.append(f"{prefix}: no existing PDF file in supported path fields")
            continue
        try:
            actual_sha = digest(selected)
            if isinstance(sha, str) and actual_sha != sha.lower():
                errors.append(f"{prefix}: sha256 mismatch")
            size = item.get("file_bytes", item.get("size"))
            if size is not None and (type(size) is not int or size != selected.stat().st_size):
                errors.append(f"{prefix}: file_bytes mismatch or invalid type")
            info = inspect_pdf(selected)
            if info["text_status"] == "needs_ocr":
                warnings.append(f"{prefix}: no extractable text; OCR required before text analysis")
            if not item.get("title") or item.get("metadata_status") == "needs_review":
                warnings.append(f"{prefix}: metadata identity/version still needs human review")
            if len(errors) == start_errors:
                valid += 1
        except (OSError, ValueError) as exc:
            errors.append(f"{prefix}: {exc}")
    return {"ok": not errors, "input_items": len(items), "valid_files": valid,
            "errors": errors, "warnings": warnings, "external_operations": 0,
            "note": "Passing file checks does not verify paper identity, method interpretation or novelty."}


def run_status(output):
    state = read_json(output / "run_result.json")
    if not isinstance(state, dict):
        raise ValueError("run_result.json must be an object")
    stages = state.get("stages_status", {})
    if not isinstance(stages, dict):
        raise ValueError("stages_status must be an object")
    issues = []
    report_ok = False
    report_path = state.get("report_path")
    if isinstance(report_path, str) and report_path:
        path = Path(report_path)
        if not path.is_absolute():
            path = output / path
        report_ok = path.is_file() and digest(path) == state.get("report_sha256")
    declared_success = state.get("execution_status") == "COMPLETED"
    consistent = (state.get("status") == "completed" and state.get("execution_finished") is True
                  and all(stages.get(name) == "completed" for name in STAGES))
    if declared_success and not consistent:
        issues.append("Declared completion disagrees with finished flag or required stage statuses")
    if declared_success and not report_ok:
        issues.append("Completed report is missing or its SHA256 does not match")
    success = declared_success and consistent and report_ok
    return {"ok": success, "execution_success": success,
            "execution_status": state.get("execution_status", "UNKNOWN"),
            "execution_finished": state.get("execution_finished", False),
            "evidence_status": state.get("evidence_status"),
            "research_maturity": state.get("research_maturity"),
            "stages_status": stages, "report_bytes_verified": report_ok, "issues": issues,
            "note": "Read-only execution snapshot. Scientific claims still require independent review."}


def demo():
    """Exercise actual T005 PDF extraction and Tantivy indexing, stopping before QA."""
    required = doctor("research")
    if not required["ok"]:
        return required
    original = (socket.socket.connect, socket.create_connection, socket.getaddrinfo)
    attempts = []

    def blocked(*args, **kwargs):
        attempts.append(True)
        raise RuntimeError("Network disabled in offline demo")

    socket.socket.connect = socket.create_connection = socket.getaddrinfo = blocked
    try:
        import pymupdf
        with tempfile.TemporaryDirectory(prefix="research_workflow_demo_") as tmp:
            work = Path(tmp)
            papers = work / "papers"
            papers.mkdir()
            with pymupdf.open() as doc:
                page = doc.new_page()
                page.insert_text((72, 72), "Synthetic workflow fixture. Not a published paper.\n"
                                 "This document tests file hashing, extraction and local indexing only.")
                doc.save(papers / "synthetic.pdf")
            manifest = work / "manifest.json"
            create_manifest(papers, manifest, "What does this synthetic workflow fixture say?")
            runner_path = ROOT / "deliveries/T005_research_pipeline/research_runner.py"
            spec = importlib.util.spec_from_file_location("workflow_demo_runner", runner_path)
            module = importlib.util.module_from_spec(spec)
            with redirect_stdout(io.StringIO()):
                spec.loader.exec_module(module)
                pipeline = module.ResearchPipeline(manifest, "What does this synthetic fixture say?",
                                                   work / "run", backend="gemini_proxy")
                normalized, mapping = pipeline.step1_prepare_corpus()
            downstream = any(pipeline.state["stages"][s]["status"] != "pending" for s in STAGES[1:])
            ok = (len(normalized) == 1 and mapping["total_chunks"] > 0 and not downstream
                  and not attempts and pipeline.state["stages"][STAGES[0]]["status"] == "completed")
            return {"ok": ok, "scope": "actual_T005_corpus_only", "synthetic_fixture": True,
                    "valid_papers": len(normalized), "total_chunks": mapping["total_chunks"],
                    "downstream_executed": downstream, "network": "blocked",
                    "network_attempts": len(attempts), "persistent_outputs": False,
                    "note": "No model answers, literature review or scientific conclusions were generated."}
    finally:
        socket.socket.connect, socket.create_connection, socket.getaddrinfo = original


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local research workflow tools (no external operations)")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("doctor", help="Check dependencies without importing services or reading credentials")
    p.add_argument("--profile", choices=("local", "research", "paperqa", "dsh"), default="local")
    p = sub.add_parser("manifest", help="Hash/deduplicate PDFs and create an unreviewed analysis manifest")
    p.add_argument("--input-dir", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--topic", default="")
    p = sub.add_parser("validate", help="Read-only verification of PDF bytes and parseability")
    p.add_argument("--manifest", type=Path, required=True)
    p = sub.add_parser("status", help="Read-only T005 result, stage and report-integrity check")
    p.add_argument("--output", type=Path, required=True)
    sub.add_parser("demo", help="Generate a temporary PDF and exercise real T005 extraction/indexing offline")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor(args.profile)
        elif args.command == "manifest":
            result = create_manifest(args.input_dir.resolve(), args.output.resolve(), args.question, args.topic)
        elif args.command == "validate":
            result = validate_manifest(args.manifest.resolve())
        elif args.command == "status":
            result = run_status(args.output.resolve())
        else:
            result = demo()
    except (OSError, ValueError, ImportError) as exc:
        result = {"ok": False, "errors": [str(exc)], "command": args.command}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
