#!/usr/bin/env python3
"""Builds immutable, deterministic downstream analysis manifests."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    from .workflow_config import SCHEMA_VERSION, UNIFIED_MANIFEST_SCHEMA
except ImportError:
    from workflow_config import SCHEMA_VERSION, UNIFIED_MANIFEST_SCHEMA


def compute_manifest_sha256(items: list[dict[str, Any]]) -> str:
    """Computes SHA256 over deterministic JSON representation of items (content hash)."""
    canonical_json = json.dumps(items, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def compute_file_sha256(path: Path | str) -> str:
    """Computes SHA256 of file bytes on disk."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def resolve_valid_analysis_pdf(r: dict[str, Any]) -> tuple[str, str, int, str]:
    """
    Strictly resolves and verifies an existing, readable PDF file for analysis.
    Checks candidate paths in priority order:
    1. r['pdf_path'] (if present and already a file)
    2. r['working_pdf_path']
    3. r['original_pdf_path']
    4. r['local_pdf_absolute']
    5. r['zotero_stored_pdf_path'] (verified stored PDF in Zotero)

    NEVER resolves an empty string to current working directory (cwd).
    Returns (resolved_path_str, source_type, file_bytes, actual_sha256)
    Raises FileNotFoundError or ValueError if no valid readable file matches.
    """
    claimed_sha = str(r.get("sha256") or "").strip().lower()

    candidate_specs = [
        ("pdf_path", r.get("pdf_path")),
        ("working_pdf_path", r.get("working_pdf_path")),
        ("original_pdf_path", r.get("original_pdf_path")),
        ("local_pdf_absolute", r.get("local_pdf_absolute")),
        ("zotero_stored_pdf_path", r.get("zotero_stored_pdf_path")),
    ]

    for source_type, cand in candidate_specs:
        if not cand:
            continue
        cand_str = str(cand).strip()
        if not cand_str:
            continue
        cand_p = Path(cand_str)
        # Prevent treating directory as PDF
        if cand_p.is_file():
            actual_size = cand_p.stat().st_size
            actual_sha = compute_file_sha256(cand_p)
            if claimed_sha and actual_sha.lower() != claimed_sha:
                # Corrupted or modified file, skip or raise
                continue
            return str(cand_p.resolve()), source_type, actual_size, actual_sha

    raise FileNotFoundError(
        f"Unable to resolve valid readable PDF file for paper_id={r.get('paper_id') or r.get('source_id')}. "
        f"Checked candidates: {[c[1] for c in candidate_specs if c[1]]}"
    )


def build_unified_manifest(
    records: list[dict[str, Any]],
    scope_boundaries: dict[str, Any] | None = None,
    output_path: str | Path | None = None,
    strict_file_verify: bool = True,
) -> dict[str, Any]:
    """
    Constructs the canonical downstream analysis manifest.
    Ensures global stable IDs, preserves aliases, attaches domain/boundary metadata,
    strictly validates that pdf_path is a real, readable file matching claimed SHA256,
    and calculates an immutable content manifest_sha256.
    """
    scope_boundaries = scope_boundaries or {}
    items = []

    for r in records:
        item_status = r.get("status") or r.get("ingest_status")
        if item_status not in ("VERIFIED", "reused_existing") and r.get("download_status") != "reused_existing":
            # Only include verified items in analysis manifest
            continue

        paper_id = r.get("paper_id") or r.get("source_id") or ""
        aliases = list(dict.fromkeys(r.get("aliases", [paper_id])))

        # Determine domain & transfer boundary
        boundary_info = scope_boundaries.get(paper_id) or scope_boundaries.get(r.get("doi")) or {}
        if not boundary_info:
            for alias in aliases:
                if alias in scope_boundaries:
                    boundary_info = scope_boundaries[alias]
                    break

        # Check special case for instsci_supp_003 / ASV paper
        if not boundary_info:
            if "instsci_supp_003" in aliases or "10.48550/arxiv.2609.21046" in str(r.get("doi", "")).lower():
                boundary_info = {
                    "domain": "surface_vehicle_asv",
                    "transfer_boundary": (
                        "【跨域参考边界】：本论文为自主水面无人艇（ASV/USV）外场湖试实测，"
                        "非水下潜器（ROV/AUV）深水高压与垂直自由度耦合工况；其在单次统一QP中集成推力饱和、"
                        "推力变化率限值与故障重构矩阵退化标志位的方法具有极强控制理论与优化架构迁移价值，"
                        "但不可作为水下深水环境的直接实验证据。"
                    )
                }
            else:
                boundary_info = {
                    "domain": "underwater_vehicle_rov_auv",
                    "transfer_boundary": "水下机器人推力分配与控制本体研究"
                }

        authors = r.get("authors") or []
        if isinstance(authors, str):
            authors = [a.strip() for a in authors.split(";") if a.strip()]

        # Strictly resolve PDF path
        if strict_file_verify:
            valid_pdf_path, path_source, actual_bytes, actual_sha = resolve_valid_analysis_pdf(r)
        else:
            valid_pdf_path = str(r.get("pdf_path") or "")
            path_source = "unverified"
            actual_bytes = int(r.get("file_bytes") or 0)
            actual_sha = str(r.get("sha256") or "")

        provenance = dict(r.get("provenance") or {})
        provenance["pdf_path_source"] = path_source

        item = {
            "paper_id": paper_id,
            "source_id": r.get("source_id") or paper_id,
            "aliases": aliases,
            "title": r.get("title", ""),
            "authors": authors,
            "year": str(r.get("year") or ""),
            "doi": r.get("doi") or None,
            "url": r.get("url") or "",
            "pdf_path": valid_pdf_path,
            "file_bytes": actual_bytes,
            "sha256": actual_sha,
            "zotero_parent_key": r.get("zotero_parent_key") or r.get("zotero_key") or "",
            "zotero_attachment_key": r.get("zotero_attachment_dir") or r.get("zotero_attachment_key") or "",
            "zotero_stored_pdf_path": str(r.get("zotero_stored_pdf_path") or ""),
            "evidence_grade": r.get("evidence_grade") or r.get("result_evidence") or "oa_direct",
            "provenance": provenance,
            "scope_and_boundary": boundary_info,
            "status": "VERIFIED"
        }
        items.append(item)

    # Sort items deterministically by paper_id
    items.sort(key=lambda x: x["paper_id"])

    manifest_hash = compute_manifest_sha256(items)
    manifest = {
        "schema": UNIFIED_MANIFEST_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "manifest_sha256": manifest_hash,
        "total_items": len(items),
        "items": items
    }

    if output_path:
        out_p = Path(output_path).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        manifest_text = json.dumps(manifest, indent=2, ensure_ascii=False)
        out_p.write_text(manifest_text, encoding="utf-8")
        file_bytes_sha256 = hashlib.sha256(out_p.read_bytes()).hexdigest()
        print(f"[INFO] Unified analysis manifest saved to {out_p}")
        print(f"       Items: {len(items)} | Content SHA256: {manifest_hash} | File bytes SHA256: {file_bytes_sha256}")

    return manifest
