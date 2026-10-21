"""Source-independent Connector-first acquisition with explicit legacy fallbacks."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit

from .connector_first import try_connector, save_receipt, FALLBACK_STAGE
from .library_portal import identify_pdf, write_json


def fallback_route(record: dict, requested: str = "auto") -> str:
    if requested != "auto":
        return requested
    source = record.get("portal") or record.get("source") or ""
    if source in {"cnki", "wanfang"}:
        return source
    return "doi" if record.get("doi") else "url"


def decision(result: dict) -> str:
    if result.get("status") == "verified_pdf" and result.get("row", {}).get("verified_match") is True:
        return "reuse_verified_pdf"
    if not result.get("fallback_allowed", False):
        return "pause_no_duplicate_save"
    if result.get("zotero_item_key"):
        return "fallback_attachment_to_existing_parent"
    return "fallback_download"


def run_fallback(record: dict, output: Path, route: str, *, institution: str = "",
                 library_config: Path | None = None) -> list[dict]:
    """The original downloaders remain intact; never create a Zotero parent here."""
    output.mkdir(parents=True, exist_ok=True)
    prefix = [sys.executable, "-m", "instsci.cli"]
    if route == "doi":
        path = output / "request.txt"
        path.write_text(record["doi"] + "\n", encoding="utf-8")
        argv = prefix + ["papers", str(path), "--output", str(output), "--publisher", "auto", "--no-retry"]
        if institution:
            argv += ["--institution", institution]
        manifest = output / "complete" / "manifest.json"
    elif route in {"cnki", "wanfang"}:
        path = output / "request.json"
        row = {**record, "record_id": record.get("record_id") or record.get("id") or record.get("doi") or "paper"}
        write_json(path, [row])
        argv = prefix + [route + "-batch", str(path), "--output", str(output)]
        if route == "cnki":
            argv += ["--navigation-mode", "search"]
        manifest = output / "manifest.json"
    elif route == "library":
        if library_config is None:
            raise ValueError("Library route requires --library-config")
        path = output / "request.json"
        write_json(path, [record])
        argv = prefix + ["library-papers", "--library-config", str(library_config),
                         "--records", str(path), "--output", str(output / "library")]
        manifest = output / "library" / "complete" / "manifest.json"
    elif route == "url":
        # Legal public author/arXiv links use the existing fetcher fallback.
        from .config import Config
        from .fetcher import PaperFetcher
        from .models import Paper
        from .sources.arxiv import extract_arxiv_id
        url = record.get("url", "")
        if urlsplit(url).scheme not in {"http", "https"}:
            raise ValueError("URL fallback needs an observed HTTP(S) article URL")
        cfg = Config.load()
        cfg.output_dir = str(output)
        fetcher = PaperFetcher(cfg)
        fallback_token = FALLBACK_STAGE.set(True)
        try:
            arxiv_id = extract_arxiv_id(url) if urlsplit(url).hostname in {"arxiv.org", "export.arxiv.org"} else None
            paper = fetcher._fetch_arxiv(arxiv_id, Paper(url=url)) if arxiv_id else fetcher.fetch(url)
            return [{"title": paper.title, "doi": paper.doi, "pdf_path": paper.pdf_path,
                     "file_status": "success" if paper.pdf_path else "missing",
                     "result_evidence": "oa_direct" if arxiv_id else "not_verified"}]
        finally:
            FALLBACK_STAGE.reset(fallback_token)
            fetcher.close()
    else:
        raise ValueError("Unknown fallback route")
    child_env = {**os.environ, "PYTHONUTF8": "1", "INSTSCI_CONNECTOR_FALLBACK": "1"}
    # No shell, no credentials or browser cookies in argv or fallback logs.
    child = subprocess.run(argv, env=child_env, capture_output=True)
    write_json(output / "fallback_receipt.json", {"route": route, "exit_code": child.returncode,
                                                   "manifest_exists": manifest.is_file()})
    if not manifest.is_file():
        return []
    rows = json.loads(manifest.read_text(encoding="utf-8-sig"))
    if isinstance(rows, list):
        for row in rows:
            if row.get("pdf_path") and not Path(row["pdf_path"]).is_absolute():
                row["pdf_path"] = str((manifest.parent / row["pdf_path"]).resolve())
    return rows if isinstance(rows, list) else []


def acquire(records: list[dict], output: Path, *, fallback: str = "auto", institution: str = "",
            library_config: Path | None = None, connector_extension: str = "",
            resume: bool = False) -> dict:
    from .config import Config
    if not isinstance(records, list) or not records or any(not isinstance(r, dict) or not (r.get("doi") or r.get("url") or
                             (r.get("title") and r.get("first_author"))) for r in records):
        raise ValueError("Each request needs DOI, an observed URL, or exact title plus first author")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cfg = Config.load()
    if connector_extension:
        cfg.zotero_connector_extension = connector_extension
    import hashlib
    library_hash = hashlib.sha256(library_config.read_bytes()).hexdigest() if library_config else ""
    lock = output / ".connector_acquire.lock"
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(fd)
    state_path = output / "acquisition_state.json"
    complete = output / "complete"
    complete.mkdir(exist_ok=True)
    state = {"schema": "instsci.connector_first.v1", "strategy": "connector_first",
             "records": records, "fallback": fallback, "institution": institution,
             "connector_save_allowed": os.environ.get("INSTSCI_CONNECTOR_READ_ONLY") != "1",
             "library_config": str(library_config.resolve()) if library_config else "",
             "library_config_sha256": library_hash, "results": []}

    def checkpoint():
        write_json(state_path, state)
        write_json(complete / "manifest.json", state["results"])

    try:
        if state_path.exists():
            if not resume:
                raise ValueError("Existing run; inspect before using --resume")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if (state["records"] != records or state["fallback"] != fallback
                    or state.get("institution", "") != institution
                    or state.get("library_config", "") != (str(library_config.resolve()) if library_config else "")
                    or state.get("library_config_sha256", "") != library_hash):
                raise ValueError("Resume request differs from original records or fallback route")
            if state.get("connector_save_allowed", True) != (os.environ.get("INSTSCI_CONNECTOR_READ_ONLY") != "1"):
                raise ValueError("Resume Connector write policy differs from original run")
            # Do not replay an uncertain plugin save merely because its caller stopped.
            if "active_connector_request" in state:
                raise ValueError("Previous save may still be active; inspect Zotero and receipt before resuming")
            for row in state["results"]:
                if row.get("file_status") == "success":
                    import hashlib
                    p = Path(row["pdf_path"])
                    if hashlib.sha256(p.read_bytes()).hexdigest() != row.get("sha256"):
                        raise ValueError("Previously accepted PDF bytes changed")
        checkpoint()
        for index, record in enumerate(records):
            if index < len(state["results"]) and state["results"][index].get("file_status") == "success":
                continue
            item_dir = output / f"item_{index + 1:04d}"
            plugin_dir = item_dir / "connector"
            route = fallback_route(record, fallback)
            state["active_connector_request"] = index
            checkpoint()
            result = try_connector(record, plugin_dir, config=cfg,
                                   before_save=_quota_hook(record, cfg, route))
            save_receipt(plugin_dir, record, result)
            state.pop("active_connector_request", None)
            choice = decision(result)
            if choice == "reuse_verified_pdf":
                row = {**result["row"], "connector_status": result["status"], "fallback_used": False}
            elif choice == "pause_no_duplicate_save":
                state["active_connector_request"] = index
                state["pause_reason"] = result["status"]
                checkpoint()
                break
            else:
                parent_key = result.get("zotero_item_key") or record.get("zotero_item_key", "")
                fallback_record = {**record, "zotero_item_key": parent_key} if parent_key else record
                candidates = run_fallback(fallback_record, item_dir / "fallback", route,
                                          institution=institution, library_config=library_config)
                row = {**record, "status": "missing", "file_status": "missing",
                       "standard_status": "capture_failed", "verified_match": False,
                       "connector_status": result["status"], "fallback_used": True,
                       "zotero_item_key": parent_key, "fallback_route": route}
                for candidate in candidates:
                    p = Path(candidate.get("pdf_path") or "")
                    if candidate.get("file_status", candidate.get("status")) != "success" or not p.is_file():
                        continue
                    expected = {"doi": record.get("doi", ""), "title": record.get("title") or candidate.get("title", "")}
                    if not expected["title"] or not identify_pdf(p, [expected]):
                        continue
                    import hashlib
                    raw = p.read_bytes()
                    row.update(candidate)
                    row.update(status="success", file_status="success", standard_status="success",
                               verified_match=True, pdf_path=str(p.resolve()), sha256=hashlib.sha256(raw).hexdigest(),
                               size_bytes=len(raw), connector_status=result["status"], fallback_used=True)
                    if parent_key:
                        row["zotero_item_key"] = parent_key
                        row["zotero_status"] = "existing_parent_attachment_pending"
                    break
            if index < len(state["results"]):
                state["results"][index] = row
            else:
                state["results"].append(row)
            checkpoint()
        state["success"] = sum(r.get("file_status") == "success" for r in state["results"])
        state["complete"] = state["success"] == len(records)
        checkpoint()
        return state
    finally:
        lock.unlink(missing_ok=True)


def _quota_hook(record: dict, config, route: str):
    route = record.get("portal") or route
    if route not in {"cnki", "wanfang"}:
        return None

    def reserve():
        from .chinese_download_quota import ChineseDownloadPolicy, reserve_chinese_download
        policy = ChineseDownloadPolicy(warning_threshold=config.chinese_download_warning_threshold,
            combined_daily_limit=config.chinese_download_combined_daily_limit,
            portal_daily_limit=config.cnki_daily_download_limit if route == "cnki" else config.wanfang_daily_download_limit)
        quota = reserve_chinese_download(Path(config.cache_dir) / "chinese_download_quota.json",
                                        portal=route, record_id=record.get("record_id") or record.get("id") or record.get("title", ""), policy=policy)
        return quota.allowed
    return reserve
