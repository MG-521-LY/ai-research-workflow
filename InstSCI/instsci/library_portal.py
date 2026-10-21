"""User/agent-assisted downloads starting at the user's official library website.

No credentials, institution guessing, publisher redirects or cookie export. The
same visible CloakBrowser context handles navigation and native PDF downloads.
"""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
import unicodedata
from urllib.parse import urlsplit
import uuid


def public_url(value: str) -> str:
    parts = urlsplit(value)
    if (any(c.isspace() for c in value) or "\\" in value
            or parts.scheme != "https" or not parts.hostname or parts.username
            or parts.password or parts.query or parts.fragment):
        raise ValueError("Use a canonical HTTPS public library URL without credentials, query or fragment")
    return value


def read_request(config_path: Path, records_path: Path) -> tuple[dict, list[dict]]:
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict):
        raise ValueError("Library configuration must be a JSON object")
    institution = str(config.get("institution", "")).strip()
    if not institution:
        raise ValueError("Set your own institution; no institution is selected by default")
    spec = {"institution": institution,
            "library_homepage": public_url(str(config.get("library_homepage", "")))}
    if config.get("database_directory"):
        spec["database_directory"] = public_url(config["database_directory"])
    raw = json.loads(records_path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("Records must be a nonempty JSON list of exact titles and DOIs")
    records, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Each record must be a JSON object")
        doi = str(item.get("doi", "")).strip().removeprefix("https://doi.org/").lower()
        title = str(item.get("title", "")).strip()
        if not re.fullmatch(r"10\.\d{4,9}/\S+", doi) or len(normalize(title)) < 12:
            raise ValueError("Each record needs a valid DOI and a substantive exact title")
        if doi in seen:
            raise ValueError("Duplicate DOI in records; distinguish versions before downloading")
        seen.add(doi)
        records.append({"doi": doi, "title": title})
    return spec, records


def normalize(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold() if c.isalnum())


def identify_pdf(path: Path, records: list[dict]) -> dict | None:
    """Require the full expected title in first-page front matter, not references.

    Scans and layout/title variants fail closed for manual inspection. A DOI in
    the bibliography cannot turn an unrelated file into a successful download.
    """
    import pymupdf
    try:
        with pymupdf.open(path) as doc:
            if not len(doc) or doc.needs_pass:
                return None
            text = doc[0].get_text(sort=True)
    except Exception:
        return None
    front = re.split(r"(?im)^\s*(?:abstract\b|摘要|references\b|参考文献|"
                     r"(?:[1I][.．]?\s+)?introduction\b)", text, maxsplit=1)[0]
    # No-abstract papers: a conservative bounded title/signature area.
    front = "\n".join(front.splitlines()[:35])[:5000]
    normalized_front = normalize(front)
    matches = [r for r in records if normalize(r["title"]) and normalize(r["title"]) in normalized_front]
    if len(matches) != 1:
        return None
    candidate = matches[0]
    # If a different DOI is printed before the abstract, do not accept by title.
    printed = {x.rstrip(".,;)").lower() for x in re.findall(r"10\.\d{4,9}/[^\s<>]+", front)}
    if printed and candidate["doi"] and candidate["doi"] not in printed:
        return None
    return candidate


def build_plan(spec: dict, records: list[dict], output: Path) -> dict:
    return {"route": "institution_library_portal", "status": "planned_not_downloaded",
            "institution": spec["institution"], "library_homepage": spec["library_homepage"],
            "database_directory": spec.get("database_directory", ""),
            "output": str(output), "count": len(records), "records": records,
            "navigation": "official library -> database navigation -> authorized login -> article -> PDF",
            "navigation_operator": "user_or_host_agent_in_visible_browser",
            "authentication_operator": "human_only",
            "download_capture": "native_download_in_same_live_cloakbrowser_context",
            "entitlement_verified": False, "downloaded": 0,
            "note": "Configured URLs and a plan do not prove site identity, login, entitlement or PDF acquisition. "
                    "Verify the institution's official website before using the links. "
                    "PDF/CAJ viewers without a native PDF download are not silently fetched by HTTP."}


def write_json(path: Path, value) -> None:
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def prepare_state(output: Path, spec: dict, records: list[dict], resume: bool) -> dict:
    path = output / "library_state.json"
    if resume:
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("connector_save_unresolved"):
            raise ValueError("A Connector save is unresolved; inspect Zotero before resuming to avoid duplicates")
        if state["spec"] != spec or state["records"] != records:
            raise ValueError("Resume inputs differ from the original library/institution/records")
        seen = set()
        for row in state["downloads"]:
            if (row.get("file_status") != "success" or row.get("verified_match") is not True
                    or row.get("doi") in seen):
                raise ValueError("Saved successful-download list contains unverified or duplicate rows")
            seen.add(row.get("doi"))
            p = Path(row["pdf_path"])
            if not p.resolve().is_relative_to(output.resolve()):
                raise ValueError("Saved PDF path escapes the run directory")
            data = p.read_bytes()
            if (hashlib.sha256(data).hexdigest() != row["sha256"]
                    or len(data) != row["size_bytes"]
                    or identify_pdf(p, records) != {"doi": row["doi"], "title": row["title"]}):
                raise ValueError("Saved PDF changed or failed identity verification; preserve and inspect the run")
        return state
    if path.exists() or any(p.name != ".library_run.lock" for p in output.iterdir()):
        raise ValueError("Output is not empty; use --resume with identical inputs or a new output directory")
    state = {"schema": "instsci.library_portal.v1", "spec": spec, "records": records,
             "status": "not_started", "downloads": [], "rejected": 0,
             "library_opened": False, "entitlement_verified": False}
    checkpoint(output, state)
    return state


def checkpoint(output: Path, state: dict) -> None:
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(output / "library_state.json", state)
    complete = output / "complete"
    complete.mkdir(exist_ok=True)
    write_json(complete / "manifest.json", state["downloads"])


def receive_pdf(path: Path, output: Path, state: dict, evidence: str,
                *, source_origin: str = "", pdf_origin: str = "") -> bool:
    """Accept only a requested, unique, parseable native download with evidence."""
    matched = identify_pdf(path, state["records"])
    done = {x["doi"] for x in state["downloads"]}
    if not matched or matched["doi"] in done or not evidence or not Path(evidence).is_file():
        state["rejected"] += 1
        checkpoint(output, state)
        return False
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    dest = output / "complete" / "pdfs" / f"{digest}.pdf"
    dest.parent.mkdir(exist_ok=True)
    if dest.exists():
        raise ValueError("Unexpected existing target PDF; do not overwrite")
    path.replace(dest)
    state["downloads"].append({**matched, "status": "success", "file_status": "success",
        "standard_status": "success", "result_evidence": "browser_verified",
        "verified_match": True, "pdf_path": str(dest.resolve()), "size_bytes": len(data),
        "sha256": digest, "md5": hashlib.md5(data).hexdigest(),
        "route_attempted": "institution_library_portal", "institution": state["spec"]["institution"],
        "library_homepage": state["spec"]["library_homepage"], "evidence": evidence,
        "source_origin": source_origin, "pdf_origin": pdf_origin,
        "acquisition_method": "user_or_agent_navigation_native_browser_download",
        "route_chain_verified": False,
        "note": "PDF identity and native capture verified. Library-to-database navigation "
                "must be visually reviewed; no inference of subscription coverage or method read."})
    prior = state.get("connector_attempts", {}).get(matched["doi"], {})
    if prior.get("zotero_item_key"):
        state["downloads"][-1].update(zotero_item_key=prior["zotero_item_key"],
                                      zotero_status="existing_parent_attachment_pending")
    checkpoint(output, state)
    return True


def run_library(spec: dict, records: list[dict], output: Path, *, resume: bool = False,
                wait_seconds: int = 900, close_on_timeout: bool = False) -> dict:
    if wait_seconds < 1:
        raise ValueError("wait_seconds must be positive")
    output.mkdir(parents=True, exist_ok=True)
    lock = output / ".library_run.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise ValueError("Another run or an unreviewed stale lock exists; inspect before resuming") from None
    context = None
    try:
        os.close(fd)
        state = prepare_state(output, spec, records, resume)
        if len(state["downloads"]) == len(records):
            state["status"] = "completed_pdf_identity_only"
            checkpoint(output, state)
            return state
        from .config import Config
        from .publisher_batch import PublisherBatchDownloader
        profile_key = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:20]
        # Reuse a dedicated institution profile; never export cookies or read
        # internal authentication databases. Normal user configuration is local.
        profile = Path.home() / ".instsci" / "library-profiles" / profile_key
        browser_cfg = Config.load()
        browser_cfg.chrome_profile_dir = str(profile)
        broker = PublisherBatchDownloader(browser_cfg)
        state["status"] = "browser_starting"
        checkpoint(output, state)
        try:
            context = broker._launch_context()
        except Exception as exc:
            state.update(status="browser_start_failed", error_type=type(exc).__name__)
            checkpoint(output, state)
            return state
        pending = deque()
        attached = set()

        def attach(page):
            if id(page) not in attached:
                attached.add(id(page))
                page.on("download", lambda download: pending.append((page, download)))

        context.on("page", attach)
        page = context.new_page()
        attach(page)
        evidence_dir = output / "private-evidence"
        evidence_dir.mkdir(exist_ok=True)
        try:
            page.goto(spec["library_homepage"], wait_until="domcontentloaded", timeout=60_000)
            page.screenshot(path=str(evidence_dir / "library_homepage.png"), full_page=False)
            state["library_opened"] = True
            checkpoint(output, state)
            if spec.get("database_directory"):
                page.goto(spec["database_directory"], wait_until="domcontentloaded", timeout=60_000)
                page.screenshot(path=str(evidence_dir / "database_directory.png"), full_page=False)
            state.update(status="awaiting_browser_navigation_and_download", library_opened=True)
        except Exception as exc:
            state.update(status="library_navigation_failed", error_type=type(exc).__name__)
            checkpoint(output, state)
            return state
        checkpoint(output, state)
        state.setdefault("connector_attempts", {})
        from .connector_first import try_connector, page_matches, extension_directory, save_receipt
        from .config import Config
        connector_cfg = Config.load()
        if not extension_directory(connector_cfg.zotero_connector_extension) or os.environ.get("INSTSCI_CONNECTOR_FALLBACK") == "1":
            for record in records:
                state["connector_attempts"].setdefault(record["doi"], {
                    "status": "connector_unavailable" if not extension_directory(connector_cfg.zotero_connector_extension) else "already_attempted_before_fallback",
                    "fallback_allowed": True})
        print("请在可见浏览器中从本校图书馆进入数据库，按题名/DOI定位并下载下列 PDF。", flush=True)
        print("学校登录/验证码由您手动完成；已下载项不会重复计入。", flush=True)
        for record in records:
            if record["doi"] not in {x["doi"] for x in state["downloads"]}:
                print(f"  {record['title']} | {record['doi']}", flush=True)
        deadline = time.monotonic() + wait_seconds
        while len(state["downloads"]) < len(records):
            pages = [p for p in context.pages if not p.is_closed()]
            if not pages:
                state["status"] = "browser_closed_incomplete"
                break
            for record in records:
                if record["doi"] in state["connector_attempts"] or record["doi"] in {d["doi"] for d in state["downloads"]}:
                    continue
                article = next((p for p in pages if page_matches(p, record)), None)
                if article is None:
                    continue
                state["connector_save_unresolved"] = record["doi"]
                checkpoint(output, state)
                from .acquisition import _quota_hook
                host = urlsplit(article.url).hostname or ""
                portal = "cnki" if "cnki" in host else ("wanfang" if "wanfang" in host else "library")
                item_dir = output / "connector_first" / hashlib.sha256(record["doi"].encode()).hexdigest()[:20]
                outcome = try_connector(record, item_dir, config=connector_cfg, context=context, page=article,
                                        before_save=_quota_hook(record, connector_cfg, portal))
                save_receipt(item_dir, record, outcome)
                state["connector_attempts"][record["doi"]] = outcome
                if outcome["status"] == "verified_pdf":
                    state["downloads"].append(outcome["row"])
                    state.pop("connector_save_unresolved", None)
                elif outcome.get("fallback_allowed"):
                    state.pop("connector_save_unresolved", None)
                else:
                    state["status"] = outcome["status"]
                checkpoint(output, state)
            if state.get("connector_save_unresolved"):
                break
            try:
                pages[0].wait_for_timeout(250)
            except Exception:
                state["status"] = "browser_closed_incomplete"
                break
            while pending:
                source_page, download = pending.popleft()
                token = uuid.uuid4().hex
                candidates = output / "candidates"
                candidates.mkdir(exist_ok=True)
                candidate = candidates / f"{token}.pdf"
                shot = evidence_dir / f"download_{token}.png"
                try:
                    download.save_as(str(candidate))
                    source_page.screenshot(path=str(shot), full_page=False)
                    accepted = receive_pdf(candidate, output, state, str(shot.resolve()),
                                           source_origin=safe_origin(source_page.url),
                                           pdf_origin=safe_origin(download.url))
                    print("已核验并保存目标 PDF。" if accepted else
                          "下载文件未通过题名/身份核验或重复，已保留候选供检查。", flush=True)
                except Exception as exc:
                    state.update(status="download_capture_failed", error_type=type(exc).__name__)
                    checkpoint(output, state)
            if time.monotonic() >= deadline:
                state["status"] = "capture_timeout_not_login_verdict"
                checkpoint(output, state)
                if close_on_timeout:
                    break
                print("尚未收齐：进度已保存，浏览器继续等待；可继续下载，或关闭浏览器后用 --resume 接续。", flush=True)
                deadline = float("inf")
        if len(state["downloads"]) == len(records):
            state["status"] = "completed_pdf_identity_only"
        checkpoint(output, state)
        return state
    except KeyboardInterrupt:
        if 'state' in locals():
            state["status"] = "interrupted_incomplete"
            checkpoint(output, state)
            return state
        raise
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        lock.unlink(missing_ok=True)


def safe_origin(url: str) -> str:
    """Retain a source host, never signed query strings, tokens or userinfo."""
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.hostname}" if parsed.hostname else ""


def library_main(*, config: Path, records: Path, output: Path, plan_only: bool = False,
                 resume: bool = False, wait_seconds: int = 900,
                 close_on_timeout: bool = False) -> dict:
    spec, items = read_request(config, records)
    if plan_only:
        return build_plan(spec, items, output)
    return run_library(spec, items, output.resolve(), resume=resume,
                       wait_seconds=wait_seconds, close_on_timeout=close_on_timeout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-config", type=Path, required=True)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--wait-seconds", type=int, default=900)
    parser.add_argument("--close-on-timeout", action="store_true")
    args = vars(parser.parse_args())
    args['config'] = args.pop('library_config')
    try:
        result = library_main(**args)
    except (ValueError, OSError, ImportError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "invalid_or_unavailable", "error_type": type(exc).__name__}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] in {'planned_not_downloaded', 'completed_pdf_identity_only'} else 2


if __name__ == "__main__":
    raise SystemExit(main())
