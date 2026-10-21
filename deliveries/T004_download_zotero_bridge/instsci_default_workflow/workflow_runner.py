#!/usr/bin/env python3
"""Unified CLI and Runner for InstSci Default Workflow.

Supported subcommands:
- run: full workflow orchestration (init -> search -> ingest -> review -> plan -> acquire -> export -> sync)
- sync: synchronize an InstSci manifest / topic directory into Zotero
- from-task: consume an existing InstSci topic-library task directory
- import-local: ingest and sync local literature records / PDFs
- recover: inspect lock, heal interrupted batch states, recover safely
- status: show workflow lock, ledger records, and verification status
- export: compile immutable downstream analysis manifest
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

MODULE_DIR = Path(__file__).resolve().parent
T004_DIR = MODULE_DIR.parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))
if str(T004_DIR) not in sys.path:
    sys.path.insert(0, str(T004_DIR))

try:
    from .mutex_lock import WorkflowLock, WorkflowLockError, inspect_lock, now_iso
    from .workflow_config import (
        DEFAULT_BRIDGE_DIR,
        DEFAULT_MANIFEST_PATH,
        DEFAULT_TASK_DIR,
        TOPIC_SCRIPT,
        ZOTERO_CONNECTOR_URL,
        ZOTERO_DEFAULT_DB,
        ZOTERO_DEFAULT_STORAGE,
    )
    from .thin_adapter import (
        compute_stable_source_id,
        sync_single_candidate,
        verify_zotero_item_and_pdf,
    )
    from .manifest_builder import build_unified_manifest
    from .unified_verifier import collect_canonical_verified_set
except ImportError:
    from mutex_lock import WorkflowLock, WorkflowLockError, inspect_lock, now_iso
    from workflow_config import (
        DEFAULT_BRIDGE_DIR,
        DEFAULT_MANIFEST_PATH,
        DEFAULT_TASK_DIR,
        TOPIC_SCRIPT,
        ZOTERO_CONNECTOR_URL,
        ZOTERO_DEFAULT_DB,
        ZOTERO_DEFAULT_STORAGE,
    )
    from thin_adapter import (
        compute_stable_source_id,
        sync_single_candidate,
        verify_zotero_item_and_pdf,
    )
    from manifest_builder import build_unified_manifest
    from unified_verifier import collect_canonical_verified_set

from pipeline_runner import LedgerManager, verify_pdf_fulltext, check_connector_ping


def run_topic_library_cmd(args: list[str], *, connector_read_only: bool = False) -> tuple[int, str, str]:
    """Invokes InstSci topic_library.py CLI."""
    py_exe = sys.executable
    cmd = [py_exe, str(TOPIC_SCRIPT)] + args
    child_env = dict(os.environ)
    if connector_read_only:
        child_env["INSTSCI_CONNECTOR_READ_ONLY"] = "1"
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=child_env)
    return proc.returncode, proc.stdout, proc.stderr


# =========================================================================
# Internal Unlocked Implementation Functions
# =========================================================================

def _impl_from_task(
    task_dir: str | Path,
    output_dir: str | Path,
    manifest_out: str | Path | None = None,
    commit: bool = True,
    scope_boundaries: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Internal core logic for from-task without acquiring lock."""
    task_p = Path(task_dir).resolve()
    bridge_p = Path(output_dir).resolve()
    bridge_p.mkdir(parents=True, exist_ok=True)

    topic_file = task_p / "topic.json"
    zotero_man_file = task_p / "zotero_manifest.json"

    if not topic_file.is_file():
        return 1, {
            "status": "failed",
            "error": f"Missing upstream topic.json in {task_p}"
        }

    topic_data = json.loads(topic_file.read_text(encoding="utf-8"))
    upstream_task_id = topic_data.get("task_id", "unknown_task")

    records_to_process = []
    if zotero_man_file.is_file():
        records_to_process = json.loads(zotero_man_file.read_text(encoding="utf-8"))
    else:
        rc, out, err = run_topic_library_cmd(["export", "--task", str(task_p)])
        if zotero_man_file.is_file():
            records_to_process = json.loads(zotero_man_file.read_text(encoding="utf-8"))

    if not records_to_process:
        return 0, {
            "status": "empty",
            "message": "No verified records to sync in upstream task (target not yet acquired or verified)",
            "upstream_task_id": upstream_task_id,
            "total_input": 0,
            "counts": {"reused": 0, "imported": 0, "pending": 0, "failed": 0}
        }

    ledger = LedgerManager(bridge_p)
    results = []
    counts = {"reused": 0, "imported": 0, "pending": 0, "failed": 0}

    for row in records_to_process:
        row["upstream_task_id"] = upstream_task_id
        ok, status_code, it_data = sync_single_candidate(
            record=row,
            bridge_dir=bridge_p,
            ledger=ledger,
            commit=commit,
        )
        results.append({"status_code": status_code, "data": it_data})
        if status_code in ("reused_existing", "reused_ledger"):
            counts["reused"] += 1
        elif status_code == "imported_new":
            counts["imported"] += 1
        elif status_code in ("pending_metadata", "queued_dry_run"):
            counts["pending"] += 1
        else:
            counts["failed"] += 1

    ledger.export_ingest_result(input_count=len(records_to_process), sample_role="instsci_workflow")

    # Determine batch status and exit code
    if counts["failed"] > 0:
        batch_status = "failed"
        exit_code = 1
    elif not commit:
        batch_status = "dry_run"
        exit_code = 0
    elif counts["pending"] > 0 and (counts["reused"] + counts["imported"] == 0):
        batch_status = "pending"
        exit_code = 0
    elif counts["pending"] > 0:
        batch_status = "partial"
        exit_code = 0
    else:
        batch_status = "success"
        exit_code = 0

    all_records = list(ledger.data["records"].values())
    man_dest = manifest_out or (bridge_p / "unified_analysis_manifest.json")
    man = build_unified_manifest(
        all_records,
        scope_boundaries=scope_boundaries,
        output_path=man_dest,
        strict_file_verify=commit
    )

    return exit_code, {
        "status": batch_status,
        "upstream_task_id": upstream_task_id,
        "total_input": len(records_to_process),
        "counts": counts,
        "manifest_sha256": man["manifest_sha256"],
        "manifest_path": str(man_dest),
    }


def _impl_import_local(
    records_file: str | Path,
    output_dir: str | Path,
    manifest_out: str | Path | None = None,
    commit: bool = True,
    scope_boundaries: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Internal core logic for import-local without acquiring lock."""
    rec_p = Path(records_file).resolve()
    bridge_p = Path(output_dir).resolve()
    bridge_p.mkdir(parents=True, exist_ok=True)

    if not rec_p.is_file():
        return 1, {
            "status": "failed",
            "error": f"Input records file not found: {rec_p}"
        }

    try:
        records = json.loads(rec_p.read_text(encoding="utf-8"))
    except Exception as e:
        return 1, {
            "status": "failed",
            "error": f"Invalid JSON in {rec_p}: {e}"
        }

    if isinstance(records, dict) and "items" in records:
        records = records["items"]
    elif not isinstance(records, list):
        return 1, {
            "status": "failed",
            "error": "Input records file must contain a JSON array or a manifest object with 'items' array"
        }

    ledger = LedgerManager(bridge_p)
    results = []
    counts = {"reused": 0, "imported": 0, "pending": 0, "failed": 0}

    for row in records:
        ok, status_code, it_data = sync_single_candidate(
            record=row,
            bridge_dir=bridge_p,
            ledger=ledger,
            commit=commit,
        )
        results.append({"status_code": status_code, "data": it_data})
        if status_code in ("reused_existing", "reused_ledger"):
            counts["reused"] += 1
        elif status_code == "imported_new":
            counts["imported"] += 1
        elif status_code in ("pending_metadata", "queued_dry_run"):
            counts["pending"] += 1
        else:
            counts["failed"] += 1

    ledger.export_ingest_result(input_count=len(records), sample_role="local_import")

    # Determine overall status and exit code
    if counts["failed"] > 0:
        batch_status = "failed"
        exit_code = 1
    elif not commit:
        batch_status = "dry_run"
        exit_code = 0
    elif counts["pending"] > 0 and (counts["reused"] + counts["imported"] == 0):
        batch_status = "pending"
        exit_code = 0
    elif counts["pending"] > 0:
        batch_status = "partial"
        exit_code = 0
    else:
        batch_status = "success"
        exit_code = 0

    all_records = list(ledger.data["records"].values())
    man_dest = manifest_out or (bridge_p / "unified_analysis_manifest.json")
    man = build_unified_manifest(
        all_records,
        scope_boundaries=scope_boundaries,
        output_path=man_dest,
        strict_file_verify=commit
    )

    return exit_code, {
        "status": batch_status,
        "total_input": len(records),
        "counts": counts,
        "manifest_sha256": man["manifest_sha256"],
        "manifest_path": str(man_dest),
    }


def _impl_sync(
    manifest_or_task: str | Path,
    output_dir: str | Path,
    manifest_out: str | Path | None = None,
    commit: bool = True,
    scope_boundaries: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Internal core logic for sync without acquiring lock."""
    cand_p = Path(manifest_or_task).resolve()
    bridge_p = Path(output_dir).resolve()
    bridge_p.mkdir(parents=True, exist_ok=True)

    if cand_p.is_dir() and (cand_p / "topic.json").exists():
        topic_file = cand_p / "topic.json"
        zotero_man_file = cand_p / "zotero_manifest.json"

        if not zotero_man_file.is_file():
            rc, out, err = run_topic_library_cmd(["export", "--task", str(cand_p)])
            if rc != 0 or not zotero_man_file.is_file():
                return 1, {
                    "status": "failed",
                    "error": f"Failed to export zotero_manifest from {cand_p}: {err}"
                }

        return _impl_from_task(
            task_dir=cand_p,
            output_dir=bridge_p,
            manifest_out=manifest_out,
            commit=commit,
            scope_boundaries=scope_boundaries
        )

    elif cand_p.is_file():
        return _impl_import_local(
            records_file=cand_p,
            output_dir=bridge_p,
            manifest_out=manifest_out,
            commit=commit,
            scope_boundaries=scope_boundaries
        )
    else:
        return 1, {
            "status": "failed",
            "error": f"Target is neither a valid topic task dir nor a manifest file: {cand_p}"
        }


# =========================================================================
# Public Entry Points (Acquire Lock Once at the Boundary)
# =========================================================================

def cmd_run(
    task_dir: str | Path,
    output_dir: str | Path,
    topic: str,
    target_count: int = 5,
    queries: list[str] | None = None,
    manifest_out: str | Path | None = None,
    commit: bool = True,
    scope_boundaries: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """
    Subcommand: run
    Orchestrates the complete topic-library workflow:
    1. init task directory
    2. search metadata (records failures without silent bypass)
    3. check decision / reviewed_at for candidates
    4. plan batch
    5. acquire batch (records failures accurately)
    6. export to zotero manifest
    7. sync to Zotero and build unified downstream manifest
    """
    task_p = Path(task_dir).resolve()
    bridge_p = Path(output_dir).resolve()
    bridge_p.mkdir(parents=True, exist_ok=True)

    with WorkflowLock(bridge_p, command="run"):
        # Consistency check for existing task directory
        if (task_p / "topic.json").exists():
            try:
                existing_tdata = json.loads((task_p / "topic.json").read_text(encoding="utf-8"))
                existing_topic = existing_tdata.get("topic", "")
                existing_target = existing_tdata.get("target")
                # If topic differs substantially, prevent silent overwrite
                if topic and existing_topic and topic.strip() != existing_topic.strip():
                    return 1, {
                        "status": "failed",
                        "error": f"Topic mismatch on existing task dir: expected '{topic}', found '{existing_topic}'. Refusing to overwrite task."
                    }
            except Exception as e:
                return 1, {"status": "failed", "error": f"Corrupt existing topic.json: {e}"}

        # Step 1: Init if not present
        if not (task_p / "topic.json").exists():
            plan_file = bridge_p / "search_plan.json"
            plan_data = {
                "queries": queries or [topic],
                "inclusion": ["Relevant to topic"],
                "exclusion": ["Irrelevant content"]
            }
            plan_file.write_text(json.dumps(plan_data, ensure_ascii=False, indent=2), encoding="utf-8")
            rc, out, err = run_topic_library_cmd([
                "init",
                "--task", str(task_p),
                "--topic", topic,
                "--count", str(target_count),
                "--plan", str(plan_file)
            ])
            if rc != 0:
                return rc, {"status": "failed", "stage": "init", "stdout": out, "stderr": err}

        # Step 2: Search only if candidates are insufficient
        topic_file = task_p / "topic.json"
        if not topic_file.is_file():
            return 1, {"status": "failed", "stage": "init", "error": "topic.json not created after init"}

        t_data = json.loads(topic_file.read_text(encoding="utf-8"))
        existing_papers = t_data.get("papers", [])

        if len(existing_papers) < target_count:
            search_failed_queries = []
            for q in (queries or [topic]):
                rc, out, err = run_topic_library_cmd([
                    "search",
                    "--task", str(task_p),
                    "--query", q,
                    "--limit", str(max(10, target_count * 2))
                ])
                if rc != 0:
                    search_failed_queries.append({"query": q, "error": err.strip()})

            if search_failed_queries and not existing_papers:
                # Search totally failed and no papers exist
                return 1, {
                    "status": "failed",
                    "stage": "search",
                    "error": "All metadata searches failed",
                    "details": search_failed_queries
                }

        # Step 3: Check candidate review status using authentic upstream schema (decision / reviewed_at)
        t_data = json.loads(topic_file.read_text(encoding="utf-8"))
        papers = t_data.get("papers", [])
        
        unreviewed = [p for p in papers if not (p.get("decision") or p.get("reviewed_at"))]
        review_file = task_p / "review_input.json"

        if review_file.is_file():
            # Apply host agent review
            rc, out, err = run_topic_library_cmd(["review", "--task", str(task_p), "--input", str(review_file)])
            if rc != 0:
                return rc, {"status": "failed", "stage": "review", "stdout": out, "stderr": err}
            # Re-read topic data
            t_data = json.loads(topic_file.read_text(encoding="utf-8"))
            unreviewed = [p for p in t_data.get("papers", []) if not (p.get("decision") or p.get("reviewed_at"))]

        if unreviewed:
            return 0, {
                "status": "awaiting_agent_review",
                "stage": "review",
                "message": (
                    f"Task initialized with {len(papers)} papers, but {len(unreviewed)} candidate(s) await abstract review. "
                    f"Host Agent should screen abstracts and provide decisions in '{review_file}', then re-run."
                ),
                "unreviewed_count": len(unreviewed),
                "task_dir": str(task_p),
                "sample_unreviewed_ids": [p["id"] for p in unreviewed[:5]]
            }

        # Step 4: Plan batch
        rc, out, err = run_topic_library_cmd(["plan", "--task", str(task_p)])
        if rc != 0:
            return rc, {"status": "failed", "stage": "plan", "stdout": out, "stderr": err}

        # Re-read topic data to find planned batches
        t_data = json.loads(topic_file.read_text(encoding="utf-8"))
        batches = t_data.get("batches", [])
        active_planned = [b for b in batches if b.get("phase") in ("reserved", "planned")]

        # Step 5: Acquire planned batches
        acquire_errors = []
        for b in active_planned:
            rc, out, err = run_topic_library_cmd(["acquire", "--task", str(task_p), "--batch", b["id"]],
                                                  connector_read_only=not commit)
            if rc != 0:
                acquire_errors.append({"batch_id": b["id"], "error": err.strip()})

        # Step 6: Check authentic acquired results from batch manifests
        t_data = json.loads(topic_file.read_text(encoding="utf-8"))
        batches = t_data.get("batches", [])
        
        # Step 6: Collect canonical verified set using unified_verifier
        # Verifies genuine PDF binary (%PDF- header, min size, %%EOF), checks declared SHA256,
        # and strictly deduplicates by physical content SHA256.
        verified_set, unverified_items = collect_canonical_verified_set(task_p)
        acquired_paper_count = len(verified_set)

        # Step 7: Export to zotero manifest
        rc, out, err = run_topic_library_cmd(["export", "--task", str(task_p)])
        if rc != 0:
            return rc, {"status": "failed", "stage": "export", "stdout": out, "stderr": err}

        # Step 8: Sync verified subset safely into Zotero
        sync_exit, sync_res = _impl_sync(
            manifest_or_task=task_p,
            output_dir=bridge_p,
            manifest_out=manifest_out,
            commit=commit,
            scope_boundaries=scope_boundaries
        )

        # Step 9: Strict target fulfillment & sync status evaluation
        # Condition 1: If sync itself failed (sync_exit != 0 or sync_res['status'] == 'failed')
        if sync_exit != 0 or (isinstance(sync_res, dict) and sync_res.get("status") == "failed"):
            return 1, {
                "status": "failed",
                "stage": "sync",
                "message": "Zotero synchronization failed for acquired candidates",
                "target_count": target_count,
                "acquired_count": acquired_paper_count,
                "sync_result": sync_res,
                "unverified_items": unverified_items
            }

        # Condition 2: Check effective sync success count (reused + imported)
        effective_synced_count = 0
        if isinstance(sync_res, dict):
            counts = sync_res.get("counts", {})
            effective_synced_count = counts.get("reused", 0) + counts.get("imported", 0)

        # If acquired papers were claimed but zero were effectively synced/reused into Zotero:
        if acquired_paper_count > 0 and effective_synced_count == 0 and not (isinstance(sync_res, dict) and sync_res.get("status") == "dry_run"):
            return 1, {
                "status": "failed",
                "stage": "sync_consistency",
                "message": "Acquired papers could not be synced or reused into Zotero library (zero effective entries).",
                "target_count": target_count,
                "acquired_count": acquired_paper_count,
                "effective_synced_count": effective_synced_count,
                "synced_result": sync_res,
                "unverified_items": unverified_items
            }

        # Condition 3: Target count not fulfilled
        if acquired_paper_count < target_count or effective_synced_count < target_count:
            overall_status = "partial" if acquired_paper_count > 0 else "failed"
            return 1, {
                "status": overall_status,
                "stage": "acquire",
                "message": (
                    f"Target count not fulfilled: requested {target_count}, but acquired {acquired_paper_count} "
                    f"and effectively synced {effective_synced_count}. Safe subset was synced."
                ),
                "target_count": target_count,
                "acquired_count": acquired_paper_count,
                "effective_synced_count": effective_synced_count,
                "acquire_errors": acquire_errors,
                "synced_subset": sync_res,
                "unverified_items": unverified_items
            }

        # Condition 4: Sync was dry_run
        if isinstance(sync_res, dict) and sync_res.get("status") == "dry_run":
            return 0, {
                "status": "dry_run",
                "target_count": target_count,
                "acquired_count": acquired_paper_count,
                "effective_synced_count": effective_synced_count,
                "synced": sync_res
            }

        # Condition 5: Target fulfilled, valid PDFs verified, and sync/reuse succeeded
        return 0, {
            "status": "complete",
            "target_count": target_count,
            "acquired_count": acquired_paper_count,
            "effective_synced_count": effective_synced_count,
            "synced": sync_res
        }


def cmd_sync(
    manifest_or_task: str | Path,
    output_dir: str | Path,
    manifest_out: str | Path | None = None,
    commit: bool = True,
    scope_boundaries: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Subcommand: sync with boundary lock acquisition."""
    bridge_p = Path(output_dir).resolve()
    with WorkflowLock(bridge_p, command="sync"):
        return _impl_sync(
            manifest_or_task=manifest_or_task,
            output_dir=bridge_p,
            manifest_out=manifest_out,
            commit=commit,
            scope_boundaries=scope_boundaries,
        )


def cmd_from_task(
    task_dir: str | Path,
    output_dir: str | Path,
    manifest_out: str | Path | None = None,
    commit: bool = True,
    scope_boundaries: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Subcommand: from-task with boundary lock acquisition."""
    bridge_p = Path(output_dir).resolve()
    with WorkflowLock(bridge_p, command="from-task"):
        return _impl_from_task(
            task_dir=task_dir,
            output_dir=bridge_p,
            manifest_out=manifest_out,
            commit=commit,
            scope_boundaries=scope_boundaries,
        )


def cmd_import_local(
    records_file: str | Path,
    output_dir: str | Path,
    manifest_out: str | Path | None = None,
    commit: bool = True,
    scope_boundaries: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Subcommand: import-local with boundary lock acquisition."""
    bridge_p = Path(output_dir).resolve()
    with WorkflowLock(bridge_p, command="import-local"):
        return _impl_import_local(
            records_file=records_file,
            output_dir=bridge_p,
            manifest_out=manifest_out,
            commit=commit,
            scope_boundaries=scope_boundaries,
        )


def cmd_recover(bridge_dir: str | Path, force_unlock: bool = False) -> dict[str, Any]:
    bridge_p = Path(bridge_dir).resolve()
    lock_info = inspect_lock(bridge_p)
    reclaimed = False
    blocked_reason = None

    if lock_info.get("corrupt"):
        if force_unlock:
            lock_file = bridge_p / ".workflow.lock"
            if lock_file.is_file():
                lock_file.unlink()
                reclaimed = True
        else:
            return {
                "status": "blocked",
                "active_lock": False,
                "lock_reclaimed": False,
                "blocked_reason": "Corrupt lock file detected. Refusing to delete without --force-unlock flag.",
            }
    elif lock_info.get("locked"):
        # Process is alive! Never steal an active lock
        holder = lock_info.get("holder", {})
        return {
            "status": "blocked",
            "active_lock": True,
            "lock_reclaimed": False,
            "blocked_reason": f"Active workflow process PID {holder.get('pid')} is currently running (command: {holder.get('command')}). Cannot steal live lock.",
        }
    elif lock_info.get("stale"):
        lock_file = bridge_p / ".workflow.lock"
        if lock_file.is_file():
            lock_file.unlink()
            reclaimed = True

    # Recheck items and verify ledger consistency with physical evidence
    ledger = LedgerManager(bridge_p)
    items_dir = bridge_p / "items"
    audited_items = 0
    verified_consistent_dirs = 0
    inconsistent_or_corrupt_dirs = []

    if items_dir.is_dir():
        for it_dir in items_dir.iterdir():
            if not it_dir.is_dir():
                continue
            audited_items += 1
            state_file = it_dir / "batch_state.json"
            mapping_file = it_dir / "zotero_mapping.json"

            if not state_file.is_file():
                continue

            try:
                s_data = json.loads(state_file.read_text(encoding="utf-8"))
            except Exception:
                inconsistent_or_corrupt_dirs.append({"dir": str(it_dir), "reason": "corrupt_batch_state"})
                continue

            cur_status = s_data.get("status")

            if cur_status == "COMPLETED":
                # Must strictly verify mapping_file is valid JSON and contains required keys
                if not mapping_file.is_file():
                    inconsistent_or_corrupt_dirs.append({"dir": str(it_dir), "reason": "missing_mapping_file"})
                    continue

                try:
                    m_data = json.loads(mapping_file.read_text(encoding="utf-8"))
                except Exception as e:
                    # Corrupt mapping JSON: Cannot verify consistency!
                    inconsistent_or_corrupt_dirs.append({"dir": str(it_dir), "reason": f"corrupt_mapping_json: {e}"})
                    continue

                p_key = m_data.get("parent_key")
                a_key = m_data.get("attachment_dir")
                f_bytes = m_data.get("file_bytes")
                f_sha = m_data.get("sha256")
                f_title = m_data.get("title")

                # Verify against live Zotero SQLite read-only snapshot
                z_ok, _, _, _ = verify_zotero_item_and_pdf(
                    db_path=ZOTERO_DEFAULT_DB,
                    storage_dir=ZOTERO_DEFAULT_STORAGE,
                    title=f_title or "",
                    file_sha=f_sha or "",
                    file_bytes=f_bytes or 0,
                    expected_parent_key=p_key,
                    expected_attachment_key=a_key,
                )

                # Also verify in ledger
                it_key = it_dir.name
                in_ledger = it_key in ledger.data.get("records", {})
                ledger_verified = in_ledger and ledger.data["records"][it_key].get("ingest_status") == "VERIFIED"

                if z_ok and in_ledger and ledger_verified:
                    verified_consistent_dirs += 1
                else:
                    # If verified in Zotero but missing in ledger, recover into ledger
                    if z_ok:
                        rec = {
                            "item_key": it_key,
                            "source_id": m_data.get("source_id", it_key),
                            "paper_id": m_data.get("source_id", it_key),
                            "title": f_title,
                            "file_bytes": f_bytes,
                            "sha256": f_sha,
                            "ingest_status": "VERIFIED",
                            "zotero_parent_key": p_key,
                            "zotero_attachment_dir": a_key,
                            "created_at": now_iso(),
                        }
                        ledger.upsert_record(rec)
                        ledger.save()
                        verified_consistent_dirs += 1
                    else:
                        inconsistent_or_corrupt_dirs.append({
                            "dir": str(it_dir),
                            "reason": "zotero_verification_failed_or_missing_ledger",
                            "z_ok": z_ok,
                            "in_ledger": in_ledger
                        })

            elif cur_status in ("PENDING", "SUBMITTING", "PARTIAL"):
                s_data["pending_reason"] = "Workflow interrupted; needs re-verification against Zotero snapshot."
                state_file.write_text(json.dumps(s_data, indent=2), encoding="utf-8")

    overall_status = "recovered"
    if inconsistent_or_corrupt_dirs and verified_consistent_dirs == 0 and audited_items > 0:
        overall_status = "blocked"
    elif inconsistent_or_corrupt_dirs:
        overall_status = "partial_recovery"

    return {
        "status": overall_status,
        "lock_reclaimed": reclaimed,
        "active_lock": False,
        "audited_item_dirs": audited_items,
        "verified_consistent_dirs": verified_consistent_dirs,
        "inconsistent_or_corrupt_dirs": inconsistent_or_corrupt_dirs,
        "total_ledger_records": len(ledger.data.get("records", {})),
        "verified_items": len([r for r in ledger.data.get("records", {}).values() if r.get("ingest_status") == "VERIFIED"]),
    }


def cmd_status(target_dir: str | Path) -> dict[str, Any]:
    target_p = Path(target_dir).resolve()
    lock_info = inspect_lock(target_p)
    ledger_file = target_p / "ledger.json"
    topic_file = target_p / "topic.json"

    status_data: dict[str, Any] = {
        "directory": str(target_p),
        "lock": lock_info,
    }

    if topic_file.is_file():
        try:
            t_data = json.loads(topic_file.read_text(encoding="utf-8"))
            status_data["topic_task"] = {
                "task_id": t_data.get("task_id"),
                "topic": t_data.get("topic"),
                "target": t_data.get("target"),
                "candidates_count": len(t_data.get("papers", [])),
                "batches_count": len(t_data.get("batches", [])),
            }
        except Exception as e:
            status_data["topic_task_error"] = str(e)

    if ledger_file.is_file():
        try:
            ledger = LedgerManager(target_p)
            status_data["ledger"] = {
                "total_records": len(ledger.data.get("records", {})),
                "verified": len([r for r in ledger.data.get("records", {}).values() if r.get("ingest_status") == "VERIFIED"]),
                "pending": len([r for r in ledger.data.get("records", {}).values() if r.get("ingest_status") != "VERIFIED"]),
            }
        except Exception as e:
            status_data["ledger_error"] = str(e)

    return status_data


def main():
    parser = argparse.ArgumentParser(description="InstSci Default Literature Workflow Runner")
    subs = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = subs.add_parser("run", help="Full workflow orchestration from topic to Zotero sync")
    p_run.add_argument("--task", required=True, help="InstSci topic task directory")
    p_run.add_argument("--output", required=True, help="Bridge output directory")
    p_run.add_argument("--topic", required=True, help="Research topic string")
    p_run.add_argument("--count", type=int, default=5, help="Target paper count (default: 5)")
    p_run.add_argument("--queries", nargs="*", help="Optional search queries")
    p_run.add_argument("--manifest-out", help="Path to output unified analysis manifest")
    p_run.add_argument("--no-commit", action="store_true", help="Dry run without committing")

    # sync
    p_sync = subs.add_parser("sync", help="Synchronize task directory or manifest into Zotero")
    p_sync.add_argument("--target", required=True, help="Task directory or manifest.json")
    p_sync.add_argument("--output", required=True, help="Bridge output directory")
    p_sync.add_argument("--manifest-out", help="Path to output unified analysis manifest")
    p_sync.add_argument("--no-commit", action="store_true", help="Dry run without committing")

    # from-task
    p_task = subs.add_parser("from-task", help="Consume an existing InstSci topic task")
    p_task.add_argument("--task", required=True, help="Path to upstream topic task directory")
    p_task.add_argument("--output", required=True, help="Bridge output directory")
    p_task.add_argument("--manifest-out", help="Path to output unified analysis manifest")
    p_task.add_argument("--no-commit", action="store_true", help="Dry run without committing")

    # import-local
    p_local = subs.add_parser("import-local", help="Ingest local literature records/PDFs")
    p_local.add_argument("--records", required=True, help="JSON file with paper records")
    p_local.add_argument("--output", required=True, help="Bridge output directory")
    p_local.add_argument("--manifest-out", help="Path to output unified analysis manifest")
    p_local.add_argument("--no-commit", action="store_true", help="Dry run without committing")

    # recover
    p_rec = subs.add_parser("recover", help="Recover interrupted workflow states and release stale locks")
    p_rec.add_argument("--bridge-dir", required=True, help="Bridge directory to inspect and recover")
    p_rec.add_argument("--force-unlock", action="store_true", help="Force release corrupt lock file")

    # status
    p_stat = subs.add_parser("status", help="Inspect status of a task or bridge directory")
    p_stat.add_argument("--dir", required=True, help="Task or bridge directory")

    # export
    p_exp = subs.add_parser("export", help="Compile unified downstream analysis manifest")
    p_exp.add_argument("--bridge-dir", required=True, help="Bridge directory containing ledger.json")
    p_exp.add_argument("--output", required=True, help="Path to output unified analysis manifest")

    args = parser.parse_args()

    if args.command == "run":
        exit_code, res = cmd_run(
            task_dir=args.task,
            output_dir=args.output,
            topic=args.topic,
            target_count=args.count,
            queries=args.queries,
            manifest_out=args.manifest_out,
            commit=not args.no_commit,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
        sys.exit(exit_code)

    elif args.command == "sync":
        exit_code, res = cmd_sync(
            manifest_or_task=args.target,
            output_dir=args.output,
            manifest_out=args.manifest_out,
            commit=not args.no_commit,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
        sys.exit(exit_code)

    elif args.command == "from-task":
        exit_code, res = cmd_from_task(
            task_dir=args.task,
            output_dir=args.output,
            manifest_out=args.manifest_out,
            commit=not args.no_commit,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
        sys.exit(exit_code)

    elif args.command == "import-local":
        exit_code, res = cmd_import_local(
            records_file=args.records,
            output_dir=args.output,
            manifest_out=args.manifest_out,
            commit=not args.no_commit,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
        sys.exit(exit_code)

    elif args.command == "recover":
        res = cmd_recover(bridge_dir=args.bridge_dir, force_unlock=args.force_unlock)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        if res.get("status") == "blocked":
            sys.exit(1)
        sys.exit(0)

    elif args.command == "status":
        res = cmd_status(target_dir=args.dir)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        sys.exit(0)

    elif args.command == "export":
        ledger = LedgerManager(Path(args.bridge_dir))
        all_recs = list(ledger.data["records"].values())
        man = build_unified_manifest(all_recs, output_path=args.output, strict_file_verify=True)
        print(json.dumps({
            "status": "success",
            "manifest_sha256": man["manifest_sha256"],
            "total_items": man["total_items"],
            "output": str(Path(args.output).resolve())
        }, indent=2, ensure_ascii=False))
        sys.exit(0)


if __name__ == "__main__":
    main()
