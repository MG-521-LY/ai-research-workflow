"""Drive the actual Zotero browser extension, then independently read back files.

No /connector/saveItems emulation, private database access or cookie extraction.
Only normal read-only Local API requests are used for identity/attachment checks.
"""
from __future__ import annotations

import hashlib
from contextvars import ContextVar
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .library_portal import identify_pdf, normalize, write_json

BLOCKED = {"connector_pending", "readback_uncertain", "ambiguous_parent", "identity_conflict"}
FALLBACK_STAGE = ContextVar("instsci_connector_fallback", default=False)


def extension_directory(configured: str = "") -> Path | None:
    value = configured or os.environ.get("ZOTERO_CONNECTOR_EXTENSION", "")
    if not value:
        return None
    p = Path(value).expanduser().resolve()
    try:
        m = json.loads((p / "manifest.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    # Loading an arbitrary extension is not a way to claim Zotero availability.
    if m.get("name") != "Zotero Connector" or m.get("manifest_version") not in {2, 3}:
        return None
    return p


def _doi(value) -> str:
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", str(value or ""), flags=re.I).strip().lower()


def matching_parents(items: list[dict], record: dict) -> list[dict]:
    matches = []
    expected_doi = _doi(record.get("doi"))
    title = normalize(record.get("title", ""))
    author = normalize(record.get("first_author", ""))
    for item in items:
        d = item.get("data", item)
        if d.get("itemType") in {"attachment", "note", "annotation"}:
            continue
        if expected_doi:
            match = _doi(d.get("DOI")) == expected_doi
        else:
            creators = d.get("creators") or []
            first = creators[0] if creators else {}
            first_names = {normalize(first.get("name", "")),
                           normalize(first.get("firstName", "") + first.get("lastName", "")),
                           normalize(first.get("lastName", "") + first.get("firstName", ""))} - {""}
            # Non-DOI Chinese records require exact title + first-author identity.
            match = bool(title and author and title == normalize(d.get("title", "")) and author in first_names)
        if match:
            matches.append(item)
    return matches


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ZoteroReadback:
    """Read-only normal Local API, pinned to loopback; optional key only in memory."""
    def __init__(self, base: str = "http://127.0.0.1:23119"):
        parts = urllib.parse.urlsplit(base)
        if parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost", "::1"} or parts.path not in {"", "/"} or parts.query or parts.fragment or parts.username:
            raise ValueError("Zotero Local API must be a plain loopback origin")
        self.base = base.rstrip("/")
        self.headers = {"Zotero-API-Version": "3"}
        key = os.environ.get("ZOTERO_LOCAL_API_KEY", "")
        if key:
            self.headers["Zotero-API-Key"] = key
        # Never send a local API key or response through a configured proxy.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def get(self, path: str):
        req = urllib.request.Request(self.base + path, headers=self.headers)
        return self.opener.open(req, timeout=3)

    def json(self, path: str):
        with self.get(path) as response:
            return json.load(response)

    def parents(self, record: dict) -> list[dict]:
        query = record.get("doi") or record.get("title", "")
        rows = self.json("/api/users/0/items?" + urllib.parse.urlencode({"q": query, "qmode": "everything", "format": "json", "limit": 100}))
        if not isinstance(rows, list):
            raise ValueError("Unexpected Local API items response")
        return matching_parents(rows, record)

    def attachment_bytes(self, child: dict) -> bytes:
        data = child.get("data", child)
        key = child.get("key") or data.get("key", "")
        if not re.fullmatch(r"[A-Z0-9]{8}", key):
            raise ValueError("Invalid attachment key")
        try:
            with self.get(f"/api/users/0/items/{key}/file") as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code != 302:
                raise
            url = exc.headers.get("Location", "")
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"} or parsed.query or parsed.fragment:
                raise ValueError("Unexpected registered attachment redirect") from None
            p = Path(urllib.request.url2pathname(urllib.parse.unquote(parsed.path)))
            # Imported attachments must bind to the exact API key and filename.
            if (data.get("linkMode") not in {"imported_file", "imported_url"}
                    or key not in p.parts or p.name != data.get("filename") or not p.is_file()):
                raise ValueError("Registered local file does not match attachment identity") from None
            return p.read_bytes()

    def inspect(self, record: dict, output: Path) -> dict:
        parents = self.parents(record)
        if len(parents) > 1:
            return {"status": "ambiguous_parent", "fallback_allowed": False}
        if not parents:
            return {"status": "not_saved", "fallback_allowed": True}
        parent = parents[0]
        data = parent.get("data", parent)
        key = parent.get("key") or data.get("key", "")
        if not re.fullmatch(r"[A-Z0-9]{8}", key):
            raise ValueError("Invalid parent key")
        try:
            children = self.json(f"/api/users/0/items/{key}/children?format=json")
        except Exception as exc:
            return {"status": "readback_uncertain", "fallback_allowed": False,
                    "zotero_item_key": key, "error_type": type(exc).__name__}
        if not isinstance(children, list):
            raise ValueError("Unexpected Local API children response")
        for child in children:
            d = child.get("data", child)
            if d.get("contentType") != "application/pdf":
                continue
            if d.get("parentItem") != key:
                return {"status": "identity_conflict", "fallback_allowed": False, "zotero_item_key": key}
            try:
                raw = self.attachment_bytes(child)
            except Exception as exc:
                return {"status": "readback_uncertain", "fallback_allowed": False,
                        "zotero_item_key": key, "error_type": type(exc).__name__}
            output.mkdir(parents=True, exist_ok=True)
            candidate = output / (uuid.uuid4().hex + ".pdf")
            candidate.write_bytes(raw)
            expected = {"doi": _doi(record.get("doi")), "title": record.get("title") or data.get("title", "")}
            identified = identify_pdf(candidate, [expected])
            if identified and not expected["doi"] and record.get("first_author"):
                import pymupdf
                from .chinese_literature import first_author_from_pdf_signature, normalize_author_name
                with pymupdf.open(candidate) as doc:
                    found = first_author_from_pdf_signature(doc[0].get_text(), title=expected["title"],
                                                           expected_author=record["first_author"])
                if normalize_author_name(found) != normalize_author_name(record["first_author"]):
                    identified = None
            if not identified:
                continue  # Preserve conflicting candidates; never claim success by attachment name.
            digest = hashlib.sha256(raw).hexdigest()
            final = output / (digest + ".pdf")
            if final.exists():
                if hashlib.sha256(final.read_bytes()).hexdigest() != digest:
                    raise ValueError("Existing verified file changed")
                candidate.unlink()
            else:
                candidate.replace(final)
            return {"status": "verified_pdf", "fallback_allowed": False,
                    "row": {**record, "title": expected["title"], "status": "success", "file_status": "success",
                            "standard_status": "success", "verified_match": True,
                            "result_evidence": "local_verified_reuse", "acquisition_method": "zotero_connector_readback",
                            "pdf_path": str(final.resolve()), "size_bytes": len(raw), "sha256": digest,
                            "md5": hashlib.md5(raw).hexdigest(), "zotero_item_key": key,
                            "zotero_attachment_key": child.get("key") or d.get("key"),
                            "zotero_status": "existing_item_and_pdf_verified"}}
        return {"status": "metadata_only", "fallback_allowed": True, "zotero_item_key": key}


def _runtime(context):
    for worker in list(context.service_workers) + list(context.background_pages):
        if not worker.url.startswith("chrome-extension://"):
            continue
        try:
            if worker.evaluate("() => typeof Zotero !== 'undefined' && !!Zotero.Connector_Browser"):
                return worker
        except Exception:
            continue
    return None


def page_matches(page, record: dict) -> bool:
    try:
        identity = page.evaluate("""() => ({
          doi: document.querySelector('meta[name="citation_doi"]')?.content || '',
          title: document.querySelector('meta[name="citation_title"]')?.content || document.title,
          author: document.querySelector('meta[name="citation_author"]')?.content || ''
        })""")
    except Exception:
        return False
    if record.get("doi") and _doi(identity.get("doi")) == _doi(record["doi"]):
        return True
    title = normalize(record.get("title", ""))
    matched = bool(len(title) >= 12 and title in normalize(identity.get("title", "")))
    if not record.get("doi"):
        matched = matched and bool(record.get("first_author")) and normalize(identity.get("author", "")) == normalize(record["first_author"])
    return matched


def try_connector(record: dict, output: Path, *, config=None, context=None, page=None,
                  wait_seconds: int = 90, before_save=None) -> dict:
    """Attempt the real installed extension once; return a verifiable fallback decision."""
    path = extension_directory(getattr(config, "zotero_connector_extension", ""))
    if path is None:
        return {"status": "connector_unavailable", "reason": "extension_not_configured", "fallback_allowed": True}
    api = ZoteroReadback((os.environ.get("ZOTERO_LOCAL_API_URL") or "http://127.0.0.1:23119"))
    try:
        initial = api.inspect(record, output)
    except Exception as exc:
        return {"status": "connector_unavailable", "reason": "zotero_readback_unavailable",
                "error_type": type(exc).__name__, "fallback_allowed": True}
    if initial["status"] != "not_saved":
        return initial  # Existing metadata/PDF: never trigger another parent save.
    if os.environ.get("INSTSCI_CONNECTOR_READ_ONLY") == "1":
        return {"status": "connector_save_disabled", "reason": "explicit_no_commit_mode", "fallback_allowed": True}
    own_context = context is None
    triggered = False
    try:
        if context is None:
            from .config import Config
            from .publisher_batch import PublisherBatchDownloader
            cfg = config or Config()
            cfg.zotero_connector_extension = str(path)
            context = PublisherBatchDownloader(cfg)._launch_context()
            page = context.new_page()
            url = record.get("url") or ("https://doi.org/" + record["doi"] if record.get("doi") else "")
            if not url:
                return {"status": "connector_unavailable", "reason": "article_page_not_located", "fallback_allowed": True}
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        deadline = time.monotonic() + wait_seconds
        worker = None
        while time.monotonic() < deadline:
            worker = _runtime(context)
            if worker and page_matches(page, record):
                ready = worker.evaluate("""async url=>{
                  const tab=(await chrome.tabs.query({})).find(t=>t.url===url);
                  return !!(tab && Zotero.Connector_Browser.getTabInfo(tab.id).translators?.length);
                }""", page.url)
                if ready:
                    break
            page.wait_for_timeout(500)
        if worker is None:
            return {"status": "connector_unavailable", "reason": "extension_not_loaded", "fallback_allowed": True}
        if not page_matches(page, record):
            return {"status": "connector_unavailable", "reason": "article_identity_not_confirmed", "fallback_allowed": True}
        output.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(output / "before_connector.png"), full_page=False)
        # Match a real active extension tab, not a fabricated saveItems payload.
        token = uuid.uuid4().hex
        # If evaluate loses its response, the extension may already be saving.
        # Treat delivery as uncertain until it explicitly says not started.
        triggered = before_save is None
        result = worker.evaluate("""async ({url, token}) => {
          const tabs = await chrome.tabs.query({});
          const tab = tabs.find(t => t.url === url);
          if (!tab) return {started:false, reason:'extension_tab_not_found'};
          const info = Zotero.Connector_Browser.getTabInfo(tab.id);
          if (!info.translators?.length) return {started:false, reason:'translator_unavailable'};
          globalThis.__researchWorkflowSaves ||= {};
          globalThis.__researchWorkflowSaves[token] = 'pending';
          Promise.resolve(Zotero.Connector_Browser.saveWithTranslator(tab, 0, {fallbackOnFailure:false}))
            .then(()=>globalThis.__researchWorkflowSaves[token]='finished')
            .catch(()=>globalThis.__researchWorkflowSaves[token]='failed');
          return {started:true};
        }""", {"url": page.url, "token": token}) if before_save is None else None
        if before_save is not None:
            # Chinese portal quota reservation must precede the plugin save.
            try:
                allowed = before_save()
            except Exception as exc:
                return {"status": "quota_state_error", "error_type": type(exc).__name__, "fallback_allowed": False}
            if allowed is False:
                return {"status": "daily_limit_reached", "fallback_allowed": False}
            triggered = True
            result = worker.evaluate("""async ({url,token})=>{
              const tab=(await chrome.tabs.query({})).find(t=>t.url===url);
              if(!tab || !Zotero.Connector_Browser.getTabInfo(tab.id).translators?.length)
                return {started:false,reason:'translator_unavailable'};
              globalThis.__researchWorkflowSaves ||= {};
              globalThis.__researchWorkflowSaves[token]='pending';
              Promise.resolve(Zotero.Connector_Browser.saveWithTranslator(tab,0,{fallbackOnFailure:false}))
                .then(()=>globalThis.__researchWorkflowSaves[token]='finished')
                .catch(()=>globalThis.__researchWorkflowSaves[token]='failed');
              return {started:true};
            }""", {"url": page.url, "token": token})
        if not result.get("started"):
            triggered = False
            return {"status": "connector_failed", "reason": result.get("reason"), "fallback_allowed": True}
        triggered = True
        deadline = time.monotonic() + wait_seconds
        latest = initial
        last_check = 0.0
        while time.monotonic() < deadline:
            page.wait_for_timeout(300)
            if time.monotonic() - last_check < 1:
                continue
            last_check = time.monotonic()
            latest = api.inspect(record, output)
            if latest["status"] in {"verified_pdf", "ambiguous_parent"}:
                page.screenshot(path=str(output / "after_connector.png"), full_page=False)
                if latest.get("row"):
                    latest["row"].update(result_evidence="browser_verified",
                                         evidence=str((output / "after_connector.png").resolve()))
                return {**latest, "plugin_triggered": True}
        save_status = worker.evaluate("token=>globalThis.__researchWorkflowSaves?.[token]", token)
        if save_status == "pending":
            return {"status": "connector_pending", "fallback_allowed": False, "plugin_triggered": True}
        return {**latest, "plugin_triggered": True, "save_status": save_status}
    except Exception as exc:
        return {"status": "readback_uncertain" if triggered else "connector_failed",
                "error_type": type(exc).__name__, "fallback_allowed": not triggered,
                "plugin_triggered": triggered}
    finally:
        if own_context and context is not None:
            context.close()


def save_receipt(output: Path, record: dict, result: dict) -> None:
    output.mkdir(parents=True, exist_ok=True)
    request = {k: v for k, v in record.items() if k != "url"}
    if record.get("url"):
        from .library_portal import safe_origin
        request["source_origin"] = safe_origin(record["url"])
    write_json(output / "connector_receipt.json", {"request": request, "connector": result})


def fetcher_connector(fetcher, identifier: str):
    """Gate the legacy fetch/MCP/OA entry before any new network PDF retrieval."""
    if FALLBACK_STAGE.get() or os.environ.get("INSTSCI_CONNECTOR_FALLBACK") == "1":
        return None
    # Missing extension is an explicit capability miss, not a pretend save.
    record = {"doi": fetcher._parse_doi(identifier) or "", "url": fetcher._parse_url(identifier) or ""}
    output = Path(fetcher.config.output_dir) / "connector_first" / hashlib.sha256(identifier.encode()).hexdigest()[:20]
    result = try_connector(record, output, config=fetcher.config)
    save_receipt(output, record, result)
    if not result.get("fallback_allowed") and result["status"] != "verified_pdf":
        raise RuntimeError("Zotero Connector save unresolved; inspect readback before another save or fallback")
    if result["status"] != "verified_pdf":
        return None
    from .models import Paper
    from .extractors.pdf_extractor import extract_text
    row = result["row"]
    return Paper(doi=row.get("doi", ""), title=row.get("title", ""), pdf_path=row["pdf_path"],
                 full_text=extract_text(row["pdf_path"]), source="zotero_connector", url=record["url"],
                 zotero_item_key=row.get("zotero_item_key", ""),
                 zotero_attachment_key=row.get("zotero_attachment_key", ""))
