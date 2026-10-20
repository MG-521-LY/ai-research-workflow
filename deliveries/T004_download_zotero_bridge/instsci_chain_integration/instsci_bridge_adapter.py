#!/usr/bin/env python3
"""
InstSci Topic Library to T004 Bridge Adapter (instsci_bridge_adapter.py)

Consumes canonical InstSci-topic-library task directory / export manifests,
preserves upstream task ID, candidate ID, stable DOI/source identities and content hashes,
resolves aliases, enforces deduplication against existing Zotero libraries,
and drives safe T004 ingestion without redundant network POSTs.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

# Add T004 root to sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
T004_DIR = SCRIPT_DIR.parent
if str(T004_DIR) not in sys.path:
    sys.path.insert(0, str(T004_DIR))

from pipeline_runner import (
    LedgerManager,
    verify_pdf_fulltext,
    compute_item_key,
    normalize_doi,
    now_iso,
    execute_single_ingest,
    find_existing_zotero_item_by_evidence,
    check_connector_ping,
    ZOTERO_DEFAULT_DB,
    ZOTERO_DEFAULT_STORAGE
)


def compute_stable_source_id(doi, title, file_sha):
    """
    Computes a deterministic, collision-resistant global source identity
    derived from canonical DOI, or SHA256 content hash, or normalized title.
    Prevents volatile serial numbers (like instsci_supp_001) from colliding across batches.
    """
    norm_d = normalize_doi(doi)
    if norm_d:
        # e.g., doi_10.2478_phr-2018-0022
        safe_doi = re.sub(r"[^a-zA-Z0-9_.-]", "_", norm_d)
        return f"doi_{safe_doi}"
    if file_sha:
        return f"sha_{file_sha[:16]}"
    norm_t = re.sub(r"\s+", " ", title.strip().lower())
    return "title_" + hashlib.sha256(norm_t.encode("utf-8")).hexdigest()[:16]


def adapt_instsci_task(task_dir, output_bridge_dir, sample_role="instsci_supplement", commit=False,
                       connector_url="http://127.0.0.1:23119/connector/import",
                       storage_dir=ZOTERO_DEFAULT_STORAGE, db_path=ZOTERO_DEFAULT_DB):
    task_p = Path(task_dir).resolve()
    bridge_p = Path(output_bridge_dir).resolve()
    bridge_p.mkdir(parents=True, exist_ok=True)

    topic_file = task_p / "topic.json"
    zotero_manifest_file = task_p / "zotero_manifest.json"

    if not topic_file.is_file():
        raise FileNotFoundError(f"Missing upstream topic.json in {task_p}")

    topic_data = json.loads(topic_file.read_text(encoding="utf-8"))
    upstream_task_id = topic_data.get("task_id", "unknown_task")

    # Read exported zotero_manifest or reconstruct from verified batches
    records_to_process = []
    if zotero_manifest_file.is_file():
        manifest_rows = json.loads(zotero_manifest_file.read_text(encoding="utf-8"))
    else:
        # Fallback to topic.json export
        manifest_rows = []

    ledger = LedgerManager(bridge_p)
    processed_items = []
    to_ingest_keys = []

    for row in manifest_rows:
        doi = row.get("doi", "")
        norm_doi = normalize_doi(doi)
        title = row.get("title", "").strip()
        authors = row.get("authors", "")
        if isinstance(authors, str):
            authors = [a.strip() for a in authors.split(";") if a.strip()]
        year = str(row.get("year", ""))
        pdf_path_raw = row.get("pdf_path")
        if not pdf_path_raw:
            continue
        pdf_p = Path(pdf_path_raw).resolve()

        val_ok, val_msg, val_info = verify_pdf_fulltext(pdf_p)
        if not val_ok:
            print(f"[WARN] PDF corrupt or invalid: {pdf_p} ({val_msg})", file=sys.stderr)
            continue

        file_sha = val_info["sha256"]
        file_bytes = val_info["bytes"]

        upstream_cand_id = row.get("topic_candidate_id") or row.get("record_id") or ""
        stable_source_id = compute_stable_source_id(norm_doi, title, file_sha)
        item_key = compute_item_key(stable_source_id, file_sha, title)

        # Cross-batch alias tracking: link volatile names and upstream IDs
        aliases = [stable_source_id]
        if upstream_cand_id:
            aliases.append(f"cand_{upstream_cand_id}")
        legacy_id = row.get("legacy_paper_id")
        if not legacy_id and pdf_p.stem.startswith("instsci_supp_"):
            legacy_id = pdf_p.stem
        if legacy_id and legacy_id not in aliases:
            aliases.append(legacy_id)

        # Check existing record in ledger
        existing_rec = ledger.data["records"].get(item_key)
        if existing_rec and existing_rec.get("ingest_status") == "VERIFIED":
            print(f"[INFO] 条目 {title[:30]} 已在台账中核验通过 (Key: {existing_rec.get('zotero_parent_key')})，安全跳过重复提交。")
            processed_items.append(existing_rec)
            continue

        # Check if already present in Zotero SQLite library
        actual_db = db_path or ZOTERO_DEFAULT_DB
        found_z, p_k, a_k, a_pdf = find_existing_zotero_item_by_evidence(
            actual_db, storage_dir, title, file_sha, file_bytes
        )

        provenance_data = {
            "upstream_task_id": upstream_task_id,
            "upstream_candidate_id": upstream_cand_id,
            "upstream_route": row.get("result_evidence", "oa_direct"),
            "retrieval_route": "open_access_direct" if row.get("result_evidence") == "oa_direct" else "instsci_browser",
            "source_record_sha256": hashlib.sha256(json.dumps(row, sort_keys=True).encode("utf-8")).hexdigest()
        }

        if found_z:
            print(f"[INFO] 条目 {title[:30]} 经核验证实已存在于 Zotero (Parent: {p_k}, Att: {a_k})，复用条目。")
            rec = {
                "item_key": item_key,
                "source_id": stable_source_id,
                "paper_id": stable_source_id,
                "aliases": aliases,
                "title": title,
                "authors": authors,
                "year": year,
                "doi": norm_doi,
                "original_pdf_path": str(pdf_p),
                "working_pdf_path": str(pdf_p),
                "file_bytes": file_bytes,
                "sha256": file_sha,
                "download_status": "reused_existing",
                "ingest_status": "VERIFIED",
                "zotero_parent_key": p_k,
                "zotero_attachment_dir": a_k,
                "zotero_stored_pdf_path": str(a_pdf) if a_pdf else str(Path(storage_dir) / a_k / pdf_p.name),
                "sample_role": sample_role,
                "provenance": provenance_data,
                "last_error": None,
                "created_at": now_iso()
            }
            ledger.upsert_record(rec)
            processed_items.append(rec)
            continue

        # Queued for fresh ingestion
        rec = {
            "item_key": item_key,
            "source_id": stable_source_id,
            "paper_id": stable_source_id,
            "aliases": aliases,
            "title": title,
            "authors": authors,
            "year": year,
            "doi": norm_doi,
            "original_pdf_path": str(pdf_p),
            "working_pdf_path": None,
            "file_bytes": file_bytes,
            "sha256": file_sha,
            "download_status": "instsci_task_imported",
            "ingest_status": "QUEUED",
            "zotero_parent_key": None,
            "zotero_attachment_dir": None,
            "zotero_stored_pdf_path": None,
            "sample_role": sample_role,
            "provenance": provenance_data,
            "last_error": None,
            "created_at": now_iso()
        }
        ledger.upsert_record(rec)
        to_ingest_keys.append(item_key)

    ledger.save()

    if commit and to_ingest_keys:
        for it_key in to_ingest_keys:
            r = ledger.data["records"][it_key]
            work_dir = bridge_p / "items" / it_key
            success, updated_rec, exit_code, msg = execute_single_ingest(
                item_rec=r,
                item_work_dir=work_dir,
                connector_url=connector_url,
                storage_dir=storage_dir,
                db_path=db_path or ZOTERO_DEFAULT_DB
            )
            if not success:
                updated_rec["ingest_status"] = "FAILED"
                updated_rec["last_error"] = msg
            ledger.upsert_record(updated_rec)
            ledger.save()
            processed_items.append(updated_rec)

    ledger.export_ingest_result(input_count=len(manifest_rows), sample_role=sample_role)
    return {
        "upstream_task_id": upstream_task_id,
        "input_count": len(manifest_rows),
        "verified_count": len([x for x in ledger.data["records"].values() if x.get("ingest_status") == "VERIFIED"]),
        "records": ledger.data["records"]
    }


def main():
    parser = argparse.ArgumentParser(description="InstSci Topic Library to T004 Bridge Adapter")
    parser.add_argument("--task", required=True, help="Path to upstream InstSci topic task directory")
    parser.add_argument("--output", required=True, help="T004 bridge output directory")
    parser.add_argument("--commit", action="store_true", help="Commit un-ingested papers to Zotero")
    parser.add_argument("--sample-role", default="instsci_supplement", help="Sample role identifier")
    parser.add_argument("--connector-url", default="http://127.0.0.1:23119/connector/import")
    parser.add_argument("--storage-dir", default=ZOTERO_DEFAULT_STORAGE)
    parser.add_argument("--db-path", default=ZOTERO_DEFAULT_DB)

    args = parser.parse_args()
    res = adapt_instsci_task(
        task_dir=args.task,
        output_bridge_dir=args.output,
        sample_role=args.sample_role,
        commit=args.commit,
        connector_url=args.connector_url,
        storage_dir=args.storage_dir,
        db_path=args.db_path
    )
    print(json.dumps({
        "status": "success",
        "upstream_task_id": res["upstream_task_id"],
        "input_count": res["input_count"],
        "verified_count": res["verified_count"]
    }, indent=2))


if __name__ == "__main__":
    main()




