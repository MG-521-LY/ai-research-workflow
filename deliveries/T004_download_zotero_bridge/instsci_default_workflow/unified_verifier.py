#!/usr/bin/env python3
"""Unified Canonical Literature Verifier.

Single source of truth for:
- PDF fulltext validation (%PDF magic header, size > 1KB, EOF marker, integrity)
- Physical SHA256 computation
- Declared SHA256 conflict detection
- Content-based stable paper identity computation (SHA256 content key)
- Canonical literature verification used across cmd_run target counting, sync, and manifest generation.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

try:
    from .thin_adapter import compute_stable_source_id, verify_zotero_item_and_pdf
    from .workflow_config import ZOTERO_DEFAULT_DB, ZOTERO_DEFAULT_STORAGE
except ImportError:
    from thin_adapter import compute_stable_source_id, verify_zotero_item_and_pdf
    from workflow_config import ZOTERO_DEFAULT_DB, ZOTERO_DEFAULT_STORAGE


def verify_pdf_binary(path: Path | str) -> tuple[bool, str, dict[str, Any]]:
    """
    Validates that a file is a genuine, valid PDF:
    1. Exists and is regular file
    2. Has at least 1024 bytes (not empty, not tiny stub)
    3. Starts with '%PDF-' magic bytes
    4. Has EOF marker '%%EOF' in the tail 2048 bytes
    5. Computes exact SHA256 and byte size.
    Returns (ok: bool, message: str, info: dict)
    """
    p = Path(path).resolve()
    if not p.is_file():
        return False, f"File does not exist or is not a regular file: {p}", {}

    size = p.stat().st_size
    if size < 1024:
        return False, f"File size too small for a valid academic PDF ({size} bytes, min 1024 required)", {}

    with open(p, "rb") as f:
        head = f.read(1024)
        if not head.startswith(b"%PDF-"):
            return False, "Invalid PDF header: missing '%PDF-' magic bytes (not a valid PDF)", {}
        
        # Seek tail for EOF
        tail_read = min(size, 4096)
        f.seek(size - tail_read)
        tail = f.read(tail_read)
        if b"%%EOF" not in tail:
            # Tolerant EOF check
            return False, "Corrupted PDF: missing '%%EOF' marker in file trailer", {}

        # Compute SHA256
        f.seek(0)
        h = hashlib.sha256()
        while chunk := f.read(65536):
            h.update(chunk)
        actual_sha = h.hexdigest()

    return True, "valid_pdf", {
        "path": str(p),
        "bytes": size,
        "sha256": actual_sha
    }


class CanonicalPaperRecord:
    def __init__(
        self,
        stable_id: str,
        content_sha256: str,
        title: str,
        doi: str | None,
        pdf_path: str,
        file_bytes: int,
        raw_record: dict[str, Any]
    ):
        self.stable_id = stable_id
        self.content_sha256 = content_sha256
        self.title = title
        self.doi = doi
        self.pdf_path = pdf_path
        self.file_bytes = file_bytes
        self.raw_record = raw_record

    def to_dict(self) -> dict[str, Any]:
        return {
            "stable_id": self.stable_id,
            "content_sha256": self.content_sha256,
            "title": self.title,
            "doi": self.doi,
            "pdf_path": self.pdf_path,
            "file_bytes": self.file_bytes
        }


def verify_canonical_item(
    item: dict[str, Any],
    base_dir: Path | None = None
) -> tuple[bool, str, CanonicalPaperRecord | None]:
    """
    Strictly verifies an academic paper entry:
    1. Validates standard_status / file_status
    2. Resolves pdf_path (relative to base_dir if needed)
    3. Verifies PDF binary integrity
    4. Checks declared SHA256 if present; blocks if hash conflicts
    5. Computes stable content identity.
    Returns (is_valid: bool, reason: str, canonical_record: CanonicalPaperRecord | None)
    """
    is_success = (
        item.get("standard_status") in ("success", "VERIFIED") or
        item.get("file_status") in ("success", "VERIFIED") or
        item.get("status") in ("success", "VERIFIED")
    )
    if not is_success:
        return False, f"Item status not successful: standard_status={item.get('standard_status')}, file_status={item.get('file_status')}", None

    raw_pdf = item.get("pdf_path") or item.get("local_pdf_absolute") or item.get("working_pdf_path")
    if not raw_pdf:
        return False, "Missing pdf_path in item", None

    pdf_p = Path(raw_pdf)
    if not pdf_p.is_absolute() and base_dir:
        pdf_p = (base_dir / pdf_p).resolve()
    else:
        pdf_p = pdf_p.resolve()

    val_ok, val_msg, val_info = verify_pdf_binary(pdf_p)
    if not val_ok:
        return False, f"PDF verification failed: {val_msg}", None

    actual_sha = val_info["sha256"]
    actual_bytes = val_info["bytes"]

    # Declared SHA check
    claimed_sha = str(item.get("sha256") or "").strip().lower()
    if claimed_sha and claimed_sha != actual_sha.lower():
        return False, f"SHA256 conflict: claimed={claimed_sha}, actual={actual_sha}", None

    title = str(item.get("title") or "").strip()
    doi = item.get("doi")
    stable_id = compute_stable_source_id(doi, title, actual_sha)

    canon = CanonicalPaperRecord(
        stable_id=stable_id,
        content_sha256=actual_sha,
        title=title,
        doi=doi,
        pdf_path=str(pdf_p),
        file_bytes=actual_bytes,
        raw_record=item
    )
    return True, "verified", canon


def collect_canonical_verified_set(task_dir: Path) -> tuple[dict[str, CanonicalPaperRecord], list[dict[str, Any]]]:
    """
    Scans task_dir (acquisition/batch_*/complete/manifest.json, zotero_manifest.json)
    Deduplicates strictly by content SHA256 (not title string or raw DOI string, to prevent
    counting the same paper under different titles or alias DOIs).
    Returns (verified_by_content_sha: dict[sha256, CanonicalPaperRecord], unverified_or_failed_items: list[dict])
    """
    verified: dict[str, CanonicalPaperRecord] = {}
    unverified: list[dict[str, Any]] = []

    # 1. Check acquisition complete manifests
    acq_dir = task_dir / "acquisition"
    if acq_dir.is_dir():
        for b_dir in acq_dir.iterdir():
            comp_man = b_dir / "complete" / "manifest.json"
            if comp_man.is_file():
                try:
                    items = json.loads(comp_man.read_text(encoding="utf-8"))
                    if isinstance(items, list):
                        for it in items:
                            ok, reason, canon = verify_canonical_item(it, base_dir=comp_man.parent)
                            if ok and canon:
                                # Deduplicate by content_sha256!
                                if canon.content_sha256 not in verified:
                                    verified[canon.content_sha256] = canon
                            else:
                                unverified.append({"item": it, "reason": reason, "source": str(comp_man)})
                except Exception as e:
                    unverified.append({"source": str(comp_man), "error": f"JSON parse error: {e}"})

    # 2. Check zotero_manifest.json if acq produced nothing
    if not verified:
        zot_man = task_dir / "zotero_manifest.json"
        if zot_man.is_file():
            try:
                items = json.loads(zot_man.read_text(encoding="utf-8"))
                if isinstance(items, list):
                    for it in items:
                        ok, reason, canon = verify_canonical_item(it, base_dir=zot_man.parent)
                        if ok and canon:
                            if canon.content_sha256 not in verified:
                                verified[canon.content_sha256] = canon
                        else:
                            unverified.append({"item": it, "reason": reason, "source": str(zot_man)})
            except Exception as e:
                unverified.append({"source": str(zot_man), "error": f"JSON parse error: {e}"})

    return verified, unverified
