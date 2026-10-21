#!/usr/bin/env python3
"""Thin Adapter for InstSci Topic Library to Zotero & T004 Pipeline.

Handles:
- Primary integration with InstSci native Zotero sync capabilities
- Verification against read-only Zotero SQLite snapshot:
  * Verifies parent item exists in items table
  * Verifies parent item is NOT in deletedItems table
  * Verifies attachment item exists and is NOT in deletedItems table
  * Verifies attachment directory and physical stored PDF match size and SHA256
- Zero redundant HTTP POSTs and zero net database writes on existing literature
- Strict fallback handling for Chinese literature without DOI or missing item keys
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    from .mutex_lock import WorkflowLock, WorkflowLockError, now_iso
    from .workflow_config import (
        ZOTERO_CONNECTOR_URL,
        ZOTERO_DEFAULT_DB,
        ZOTERO_DEFAULT_STORAGE,
        T004_DIR,
    )
except ImportError:
    from mutex_lock import WorkflowLock, WorkflowLockError, now_iso
    from workflow_config import (
        ZOTERO_CONNECTOR_URL,
        ZOTERO_DEFAULT_DB,
        ZOTERO_DEFAULT_STORAGE,
        T004_DIR,
    )

# Insert T004 to sys.path
if str(T004_DIR) not in sys.path:
    sys.path.insert(0, str(T004_DIR))

from pipeline_runner import (
    LedgerManager,
    verify_pdf_fulltext,
    compute_item_key,
    normalize_doi,
    execute_single_ingest,
    sha256_file,
    check_connector_ping,
)


def verify_zotero_item_and_pdf(
    db_path: Path | str,
    storage_dir: Path | str,
    title: str,
    file_sha: str,
    file_bytes: int,
    expected_parent_key: str | None = None,
    expected_attachment_key: str | None = None,
) -> tuple[bool, str | None, str | None, str | None]:
    """
    Strictly verifies against read-only snapshot of Zotero sqlite:
    1. Parent item exists and is NOT deleted.
    2. Attachment exists, links to parent, and is NOT deleted.
    3. Physical PDF exists under storage/<att_key>/, matches file_bytes and file_sha.
    Returns (verified: bool, parent_key, attachment_key, stored_pdf_path)
    """
    if not db_path or not os.path.exists(db_path):
        return False, None, None, None

    temp_dir = tempfile.mkdtemp(prefix="z_ver_")
    try:
        src_dir = os.path.dirname(db_path)
        base_name = os.path.basename(db_path)
        for ext in ["", "-wal", "-shm"]:
            fpath = os.path.join(src_dir, base_name + ext)
            if os.path.exists(fpath):
                shutil.copyfile(fpath, os.path.join(temp_dir, base_name + ext))
        copied_db = os.path.join(temp_dir, base_name)
        conn = sqlite3.connect(f"file:{copied_db}?mode=ro", uri=True)
        cur = conn.cursor()

        # Check deleted items table
        has_del = bool(cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='deletedItems'").fetchone())

        del_clause_parent = "AND i.itemID NOT IN (SELECT itemID FROM deletedItems)" if has_del else ""
        del_clause_att = "AND i_att.itemID NOT IN (SELECT itemID FROM deletedItems)" if has_del else ""

        # Query items by title
        rows = cur.execute(f"""
            SELECT i.key, i.itemID
            FROM items i
            JOIN itemData id ON i.itemID = id.itemID
            JOIN fields f ON id.fieldID = f.fieldID
            JOIN itemDataValues idv ON id.valueID = idv.valueID
            WHERE f.fieldName = 'title' AND idv.value = ?
            {del_clause_parent}
        """, (title,)).fetchall()

        for p_key, p_id in rows:
            if expected_parent_key and p_key != expected_parent_key:
                continue

            att_rows = cur.execute(f"""
                SELECT i_att.key
                FROM itemAttachments a
                JOIN items i_att ON a.itemID = i_att.itemID
                WHERE a.parentItemID = ?
                {del_clause_att}
            """, (p_id,)).fetchall()

            for (att_key,) in att_rows:
                if expected_attachment_key and att_key != expected_attachment_key:
                    continue

                cand_dir = Path(storage_dir) / att_key
                if cand_dir.exists():
                    for cand_pdf in cand_dir.glob("*.pdf"):
                        try:
                            if cand_pdf.stat().st_size == file_bytes and sha256_file(str(cand_pdf)) == file_sha:
                                conn.close()
                                return True, p_key, att_key, str(cand_pdf)
                        except OSError:
                            continue

        conn.close()
        return False, None, None, None
    except Exception as e:
        print(f"[DEBUG] verify_zotero_item_and_pdf error: {e}", file=sys.stderr)
        return False, None, None, None
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def compute_stable_source_id(doi: str | None, title: str, file_sha: str) -> str:
    """Computes deterministic global source identity from normalized DOI or file SHA256."""
    norm_d = normalize_doi(doi) if doi else ""
    if norm_d:
        safe_doi = re.sub(r"[^a-zA-Z0-9_.-]", "_", norm_d)
        return f"doi_{safe_doi}"
    if file_sha:
        return f"sha_{file_sha[:16]}"
    norm_t = re.sub(r"\s+", " ", title.strip().lower())
    return "title_" + hashlib.sha256(norm_t.encode("utf-8")).hexdigest()[:16]


def sync_single_candidate(
    record: dict[str, Any],
    bridge_dir: Path,
    ledger: LedgerManager,
    commit: bool = True,
    connector_url: str = ZOTERO_CONNECTOR_URL,
    storage_dir: Path = ZOTERO_DEFAULT_STORAGE,
    db_path: Path = ZOTERO_DEFAULT_DB,
) -> tuple[bool, str, dict[str, Any]]:
    """
    Evaluates a candidate record, verifies PDF, checks existing Zotero entry,
    and if needed, imports safely without duplicate POSTs.
    Returns (success: bool, status_code: str, item_data: dict)
    """
    title = str(record.get("title") or "").strip()
    if not title:
        return False, "missing_title", {"error": "Missing title"}

    pdf_raw = record.get("pdf_path") or record.get("local_pdf_absolute") or record.get("working_pdf_path")
    if not pdf_raw:
        return False, "missing_pdf_path", {"error": "No PDF path specified"}
    pdf_p = Path(pdf_raw).resolve()

    val_ok, val_msg, val_info = verify_pdf_fulltext(pdf_p)
    if not val_ok:
        return False, "invalid_pdf", {"error": val_msg, "pdf_path": str(pdf_p)}

    file_sha = val_info["sha256"]
    file_bytes = val_info["bytes"]

    raw_doi = record.get("doi") or ""
    norm_doi = normalize_doi(raw_doi) if raw_doi else ""
    stable_id = compute_stable_source_id(norm_doi, title, file_sha)
    item_key = compute_item_key(stable_id, file_sha, title)

    # Collect aliases
    aliases = [stable_id]
    cand_id = record.get("topic_candidate_id") or record.get("id") or record.get("record_id")
    if cand_id:
        aliases.append(f"cand_{cand_id}")
    legacy_pid = record.get("paper_id") or record.get("legacy_paper_id")
    if legacy_pid and legacy_pid not in aliases:
        aliases.append(legacy_pid)
    if pdf_p.stem.startswith("instsci_supp_") and pdf_p.stem not in aliases:
        aliases.append(pdf_p.stem)

    authors = record.get("authors", [])
    if isinstance(authors, str):
        authors = [a.strip() for a in authors.split(";") if a.strip()]

    year = str(record.get("year") or "")
    url = str(record.get("url") or "")
    source = str(record.get("source") or record.get("journal") or "")

    provenance = {
        "upstream_task_id": record.get("upstream_task_id") or record.get("task_id", ""),
        "upstream_candidate_id": cand_id or "",
        "upstream_batch_id": record.get("batch") or record.get("batch_id") or "",
        "retrieval_route": record.get("result_evidence") or record.get("retrieval_route") or "unspecified_route",
        "source_record_sha256": hashlib.sha256(json.dumps(record, sort_keys=True).encode("utf-8")).hexdigest(),
    }

    # 1. Check ledger with live verification of active non-deleted Zotero entry and disk file
    if item_key in ledger.data["records"]:
        rec = ledger.data["records"][item_key]
        if rec.get("ingest_status") == "VERIFIED":
            p_k = rec.get("zotero_parent_key")
            a_k = rec.get("zotero_attachment_dir")
            z_ok, _, _, _ = verify_zotero_item_and_pdf(
                db_path, storage_dir, title, file_sha, file_bytes,
                expected_parent_key=p_k, expected_attachment_key=a_k
            )
            stored_p = Path(rec.get("zotero_stored_pdf_path") or "")
            orig_p = Path(rec.get("original_pdf_path") or rec.get("working_pdf_path") or "")
            file_ok = (stored_p.is_file() and stored_p.stat().st_size == file_bytes) or (orig_p.is_file() and orig_p.stat().st_size == file_bytes)

            if z_ok and file_ok:
                return True, "reused_ledger", rec
            else:
                print(f"[WARN] Ledger record {item_key} failed live Zotero/disk check. Invalidating cache.", file=sys.stderr)

    # 2. Check Zotero SQLite read-only snapshot
    found_z, p_key, a_key, a_pdf = verify_zotero_item_and_pdf(
        db_path, storage_dir, title, file_sha, file_bytes
    )

    if found_z:
        rec = {
            "item_key": item_key,
            "source_id": stable_id,
            "paper_id": stable_id,
            "aliases": aliases,
            "title": title,
            "authors": authors,
            "year": year,
            "doi": norm_doi or None,
            "url": url,
            "source": source,
            "original_pdf_path": str(pdf_p),
            "working_pdf_path": str(pdf_p),
            "file_bytes": file_bytes,
            "sha256": file_sha,
            "download_status": "reused_existing",
            "ingest_status": "VERIFIED",
            "zotero_parent_key": p_key,
            "zotero_attachment_dir": a_key,
            "zotero_stored_pdf_path": a_pdf or str(Path(storage_dir) / a_key / pdf_p.name),
            "evidence_grade": provenance["retrieval_route"],
            "provenance": provenance,
            "last_error": None,
            "created_at": now_iso(),
        }
        ledger.upsert_record(rec)
        ledger.save()
        return True, "reused_existing", rec

    # A prior Connector save can have metadata without the matching PDF, or
    # the live DB snapshot may not yet show a newly saved attachment. Never
    # create another parent merely because this byte-level check did not pass.
    connector_parent = record.get("zotero_item_key") or record.get("zotero_parent_key")
    if connector_parent:
        rec = {"item_key": item_key, "title": title, "doi": norm_doi or None,
               "original_pdf_path": str(pdf_p), "file_bytes": file_bytes, "sha256": file_sha,
               "zotero_parent_key": connector_parent, "ingest_status": "PENDING_EXISTING_PARENT_ATTACHMENT",
               "download_status": "downloaded", "provenance": provenance,
               "last_error": "Existing Zotero parent must be re-read/attached via normal sync; refusing duplicate parent import"}
        ledger.upsert_record(rec)
        ledger.save()
        return False, "existing_parent_attachment_required", rec

    # 3. Not in Zotero: Check if metadata is sufficient
    if not authors and not norm_doi and not year:
        rec = {
            "item_key": item_key,
            "source_id": stable_id,
            "paper_id": stable_id,
            "aliases": aliases,
            "title": title,
            "authors": authors,
            "year": year,
            "doi": norm_doi or None,
            "url": url,
            "source": source,
            "original_pdf_path": str(pdf_p),
            "file_bytes": file_bytes,
            "sha256": file_sha,
            "download_status": "pending_metadata",
            "ingest_status": "PENDING_METADATA",
            "zotero_parent_key": None,
            "zotero_attachment_dir": None,
            "evidence_grade": provenance["retrieval_route"],
            "provenance": provenance,
            "last_error": "Missing authors, DOI, and year for bibliographic registration",
            "created_at": now_iso(),
        }
        ledger.upsert_record(rec)
        ledger.save()
        return False, "pending_metadata", rec

    if not commit:
        rec = {
            "item_key": item_key,
            "source_id": stable_id,
            "paper_id": stable_id,
            "aliases": aliases,
            "title": title,
            "authors": authors,
            "year": year,
            "doi": norm_doi or None,
            "url": url,
            "source": source,
            "original_pdf_path": str(pdf_p),
            "file_bytes": file_bytes,
            "sha256": file_sha,
            "download_status": "dry_run",
            "ingest_status": "QUEUED",
            "zotero_parent_key": None,
            "zotero_attachment_dir": None,
            "evidence_grade": provenance["retrieval_route"],
            "provenance": provenance,
            "last_error": None,
            "created_at": now_iso(),
        }
        return True, "queued_dry_run", rec

    # 4. New ingest: check connector ping first
    if not check_connector_ping(ZOTERO_CONNECTOR_URL.replace("/connector/import", "/connector/ping")):
        return False, "connector_unavailable", {
            "error": "Zotero Connector endpoint unavailable",
            "item_key": item_key
        }

    # Ingest via single ingest runner
    item_work_dir = bridge_dir / "items" / item_key
    raw_rec = {
        "item_key": item_key,
        "source_id": stable_id,
        "paper_id": stable_id,
        "title": title,
        "authors": authors,
        "year": year,
        "source": source,
        "url": url,
        "original_pdf_path": str(pdf_p),
        "file_bytes": file_bytes,
        "sha256": file_sha,
    }
    success, updated_rec, exit_code, msg = execute_single_ingest(
        item_rec=raw_rec,
        item_work_dir=item_work_dir,
        connector_url=connector_url,
        storage_dir=storage_dir,
        db_path=db_path,
    )
    if success:
        updated_rec["aliases"] = aliases
        updated_rec["doi"] = norm_doi or None
        updated_rec["evidence_grade"] = provenance["retrieval_route"]
        updated_rec["provenance"] = provenance
        ledger.upsert_record(updated_rec)
        ledger.save()
        return True, "imported_new", updated_rec
    else:
        updated_rec["aliases"] = aliases
        updated_rec["doi"] = norm_doi or None
        updated_rec["last_error"] = msg
        updated_rec["ingest_status"] = "FAILED"
        ledger.upsert_record(updated_rec)
        ledger.save()
        return False, "import_failed", updated_rec
