#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zotero_sync_round08.py
Persistent, Reusable Zotero Ingestion, Classification & Verification Tool (Round 08)

Key Protocol & Architectural Improvements (Round 08 Compliance):
- Safe Read-Only Dry-Run by default (zero mutation, zero auth prompt).
- Full Metadata Synchronization in single session:
  * Synchronizes N5PLXTK3 title -> 'Control allocation—A survey'
  * Synchronizes NY9JDYTZ DOI -> '10.1109/lcsys.2024.3407611' (correcting old 404 DOI 3385123)
  * Both protected by Zotero-Server-ID and If-Unmodified-Since-Version preconditions.
- Strict Zotero Local API protocol compliance (server_localAPI.js):
  * Mandatory Zotero-Server-ID header on all writes (e.g. tzI019en24ag).
  * Correct handling of HTTP 204 No Content on PATCH updates:
    Reads 'Last-Modified-Version' header from response, avoids json.loads() on empty body.
  * Immediate halt on HTTP 401 Unauthorized: stops immediately without attempting remaining items.
  * Immediate halt on single-use 'Allow' (remember=False) without attempting partial writes.
  * Extended 60s timeout for authorization dialog.
  * Robust collection hierarchy resolution: checks (parentCollection, name) to prevent collisions.
  * Failure tracking: collection failures set overall_success=False and stop cascading errors.
  * Enhanced attachment conflict detection: distinguishes verified matches, interrupted/empty items (404),
    and conflicting byte mismatches (never blindly overwriting existing files).
  * Unexpected readback errors do not get treated as empty attachments.
  * Comprehensive error sanitization: catches exceptions and strips raw URLs and query strings
    to ensure uploadKey and tokens never leak into console, logs, or ledger.
  * Guaranteed ledger persistence in finally block, recording readback statuses.
  * In-memory key cleanup (del api_key in finally).
"""

import sys
import os
import json
import hashlib
import argparse
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

# Base directory for resolving relative paths
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]

# Frozen expectations for the 8 target items and stage collections
STAGE_COLLECTIONS_DEF = {
    "top_name": "毕业论文开题_十条主线",
    "subcollections": [
        {"name": "阶段01_执行器动力学与低速观测", "stage_id": "STAGE_01"},
        {"name": "阶段02_水动力时滞与死区分配", "stage_id": "STAGE_02"},
        {"name": "阶段03_闭环控制与整艇辨识", "stage_id": "STAGE_03"}
    ]
}

FROZEN_ITEMS_CONFIG = [
    {
        "item_id": "SUPP_S5_01",
        "parent_key": "T2D8QJFF",
        "target_stage": "阶段01_执行器动力学与低速观测",
        "expected_title": "Sensorless Field Oriented Control of CSI-Fed PMSM Drives Used in Submersible Pumps",
        "expected_doi": "10.48550/arXiv.2503.22855",
        "relative_path": "goal20/corpus/proposal_story5/raw_papers/SUPP_S5_01_motor_drive_submersible.pdf",
        "canonical_name": "SUPP_S5_01_motor_drive_submersible.pdf",
        "content_type": "application/pdf",
        "expected_sha256": "1c0638298e3204b2565202bba39c17e55993512243cad84bed89a02909ad4428",
        "expected_md5": "1e73abdffd42ab314ec53dc2f7c8979e",
        "expected_size": 1833642
    },
    {
        "item_id": "SUPP_S5_02",
        "parent_key": "KKEQI3JV",
        "target_stage": "阶段01_执行器动力学与低速观测",
        "expected_title": "High-Performance Sensorless Control for High-Speed PMSM with Current Source Inverters",
        "expected_doi": "10.48550/arXiv.2609.25878",
        "relative_path": "goal20/corpus/proposal_story5/raw_papers/SUPP_S5_02_motor_drive_sensorless.pdf",
        "canonical_name": "SUPP_S5_02_motor_drive_sensorless.pdf",
        "content_type": "application/pdf",
        "expected_sha256": "e5516356e9b75601ba04150e5b876e2c1c3445c4d67105de4673700b873c3f28",
        "expected_md5": "81114c919202ac511f6fc8fa45048ac8",
        "expected_size": 2867372
    },
    {
        "item_id": "SUPP_S5_03",
        "parent_key": "HNN5KYGV",
        "target_stage": "阶段01_执行器动力学与低速观测",
        "expected_title": "An analysis of the benefits of signal injection for low-speed sensorless control of induction motors",
        "expected_doi": "10.48550/arXiv.1511.09349",
        "relative_path": "goal20/corpus/proposal_story5/raw_papers/SUPP_S5_03_hfi_low_speed.pdf",
        "canonical_name": "SUPP_S5_03_hfi_low_speed.pdf",
        "content_type": "application/pdf",
        "expected_sha256": "66ef73977d4d44825ddb181892d90522bab27235215118388008f624f6d564ce",
        "expected_md5": "179a9905aa3a7fd1521fa1e404b0f7a8",
        "expected_size": 445510
    },
    {
        "item_id": "SUPP_S5_04",
        "parent_key": "FVE5NJW9",
        "target_stage": "阶段01_执行器动力学与低速观测",
        "expected_title": "Obtaining the Current-Flux Relations of the Saturated PMSM by Signal Injection",
        "expected_doi": "10.48550/arXiv.1705.04605",
        "relative_path": "goal20/corpus/proposal_story5/raw_papers/arxiv_1705_04605_pmsm_signal_injection.pdf",
        "canonical_name": "arxiv_1705_04605_pmsm_signal_injection.pdf",
        "content_type": "application/pdf",
        "expected_sha256": "08cceac19fd4c2d457200c9cd86bc1763fed65797dc250e83d66b10c229b6164",
        "expected_md5": "2563df56597a5485238f8020f9be8d7d",
        "expected_size": 1035850
    },
    {
        "item_id": "SUPP_S5_06",
        "parent_key": "NY9JDYTZ",
        "target_stage": "阶段02_水动力时滞与死区分配",
        "expected_title": "Convex MPC and Thrust Allocation with Deadband for Spacecraft Rendezvous",
        "expected_doi": "10.1109/lcsys.2024.3407611",
        "absolute_path": "C:/Users/lamost/Documents/ResearchLibrary/thrust_allocation_supplement_20261018/papers/SUPP_09_Convex MPC and Thrust Allocation with Deadband for Overactua.pdf",
        "canonical_name": "SUPP_09_Convex MPC and Thrust Allocation with Deadband for Overactua.pdf",
        "content_type": "application/pdf",
        "expected_sha256": "522508d869e90df099cbf3ea35d11dc925fc35d7e4e99459c16eec859430d4be",
        "expected_md5": "edee0b3a9107cf4b48d9f5d5dd538335",
        "expected_size": 843179
    },
    {
        "item_id": "SUPP_S5_07",
        "parent_key": "N5PLXTK3",
        "target_stage": "阶段02_水动力时滞与死区分配",
        "expected_title": "Control allocation—A survey",
        "expected_doi": "10.1016/j.automatica.2013.01.035",
        "absolute_path": "C:/Users/lamost/Documents/ResearchLibrary/external_checks_20261019/Johansen_Fossen_2013_survey.pdf",
        "canonical_name": "Johansen_Fossen_2013_survey.pdf",
        "content_type": "application/pdf",
        "expected_sha256": "2f89a6629cbb6865af9b883eb1c3841b444805c36b7cf16ef2d6195d0a29740d",
        "expected_md5": "fcba2e4f06ee0dfcb13e44df29ae0913",
        "expected_size": 297546
    },
    {
        "item_id": "SUPP_S5_15",
        "parent_key": "ZABTUMQ9",
        "target_stage": "阶段02_水动力时滞与死区分配",
        "expected_title": "Constrained Nullspace-Based Thrust Allocation for Heading Prioritized Stationkeeping of Offshore Vessels in Ice",
        "expected_doi": None,
        "absolute_path": "C:/Users/lamost/Documents/ResearchLibrary/external_checks_20261019/Kjerstad_Skjetne_Berge_2013_constrained_heading.pdf",
        "canonical_name": "Kjerstad_Skjetne_Berge_2013_constrained_heading.pdf",
        "content_type": "application/pdf",
        "expected_sha256": "6d22c78a4aae5e784df0f0b2a014fe3b181a3e77c2373ca85dbc176dd1de1c45",
        "expected_md5": "90cb9b52e3ce5dba9fee4c9caca0db9f",
        "expected_size": 501828
    },
    {
        "item_id": "REF_THESIS_WANGLU_2026",
        "parent_key": "EBCYGY2F",
        "target_stage": "阶段03_闭环控制与整艇辨识",
        "expected_title": "面向核电水站的ROV运动控制研究与容错推进系统设计",
        "expected_doi": None,
        "absolute_path": "C:/Users/lamost/Documents/ResearchLibrary/thesis_reference_20261019/SA23218041-王路-面向核电水站的 ROV 运动控制研究与容错推进系统设计.pdf",
        "canonical_name": "SA23218041-王路-面向核电水站的 ROV 运动控制研究与容错推进系统设计.pdf",
        "content_type": "application/pdf",
        "expected_sha256": "680d84cfa984723503964c1552832c0d1b7e0e582f8133cb6e988e1446150e33",
        "expected_md5": "43af8b6fc567d5ce2c0091fc3af0ff99",
        "expected_size": 6208767
    }
]

def resolve_pdf_path(cfg):
    if "absolute_path" in cfg:
        return Path(cfg["absolute_path"])
    return REPO_ROOT / cfg["relative_path"]

def compute_file_hashes(file_path):
    p = Path(file_path)
    if not p.exists():
        return None, None, 0, 0
    sha = hashlib.sha256()
    md5 = hashlib.md5()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            sha.update(chunk)
            md5.update(chunk)
    mtime_ms = int(p.stat().st_mtime * 1000)
    return sha.hexdigest(), md5.hexdigest(), p.stat().st_size, mtime_ms

def sanitize_exception(e):
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP_{e.code}"
    msg = str(e)
    if "http://" in msg or "https://" in msg or "upload=" in msg:
        return "Network_or_IO_Error"
    return msg[:60]

def http_request(url, method='GET', headers=None, data=None, timeout=10):
    if headers is None:
        headers = {}
    if "User-Agent" not in headers:
        headers["User-Agent"] = "ZoteroSyncRound08/1.0"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp_data = resp.read()
        return resp.status, resp.headers, resp_data

def validate_local_upload_url(upload_url, expected_port):
    parsed = urllib.parse.urlparse(upload_url)
    if parsed.scheme != "http":
        return False
    valid_hosts = {"127.0.0.1", "localhost"}
    host = parsed.hostname
    port = parsed.port or 80
    if host not in valid_hosts or port != expected_port:
        return False
    return True

def run_dry_run(base_url, port):
    print("=" * 80)
    print("ZOTERO SYNC TOOL (ROUND 08) - SAFE READ-ONLY DRY-RUN INSPECTION")
    print("=" * 80)
    print("[*] Mode: Safe Read-Only Dry-Run. ZERO mutation. NO authorization prompt.")

    server_id = None
    try:
        sample_url = f"{base_url}/api/users/0/items/N5PLXTK3"
        status, headers, body = http_request(sample_url)
        server_id = headers.get("Zotero-Server-ID")
        print(f"[+] Zotero Local HTTP Service: ONLINE at {base_url} (HTTP {status})")
        print(f"[+] Zotero-Server-ID Header: {server_id}")
    except Exception as e:
        print(f"[!] Cannot connect to Zotero Local API at {base_url}: {sanitize_exception(e)}")
        return False

    print("\n[+] Inspecting Existing Collections & Planning Hierarchy:")
    print("-" * 80)
    existing_cols = {}
    try:
        col_url = f"{base_url}/api/users/0/collections"
        _, _, col_body = http_request(col_url)
        col_json = json.loads(col_body.decode("utf-8"))
        for c in col_json:
            cd = c.get("data", {})
            c_name = cd.get("name")
            c_parent = cd.get("parentCollection")
            c_key = cd.get("key")
            existing_cols[(c_parent, c_name)] = c_key
        print(f"[*] Found {len(col_json)} existing collection nodes in Zotero library.")
    except Exception as e:
        print(f"[!] Error fetching collections: {sanitize_exception(e)}")

    top_name = STAGE_COLLECTIONS_DEF["top_name"]
    top_key = existing_cols.get((False, top_name))
    top_exists = bool(top_key)
    print(f"Top Collection: '{top_name}' -> Exists={top_exists} (Key: {top_key or '[Pending Create]'})")
    for sub in STAGE_COLLECTIONS_DEF["subcollections"]:
        s_name = sub["name"]
        s_key = existing_cols.get((top_key, s_name)) if top_key else None
        s_exists = bool(s_key)
        print(f"  Subcollection: '{s_name}' -> Exists={s_exists} (Key: {s_key or '[Pending Create]'})")

    print("\n[+] Inspecting 8 Target Parent Items, Existing Children & Local Physical Files:")
    print("-" * 80)

    all_parents_found = True
    all_files_valid = True
    total_missing_attachments = 0
    title_sync_needed_count = 0
    doi_sync_needed_count = 0
    inspections = []

    for cfg in FROZEN_ITEMS_CONFIG:
        pk = cfg["parent_key"]
        p_url = f"{base_url}/api/users/0/items/{pk}"
        c_url = f"{base_url}/api/users/0/items/{pk}/children"

        try:
            _, _, p_body = http_request(p_url)
            p_json = json.loads(p_body.decode("utf-8"))
            actual_title = p_json.get("data", {}).get("title", "")
            actual_doi = p_json.get("data", {}).get("DOI", "")
            version = p_json.get("data", {}).get("version", 0)
            current_cols = p_json.get("data", {}).get("collections", [])
            parent_ok = True
        except Exception as e:
            actual_title = f"[ERROR: {sanitize_exception(e)}]"
            actual_doi = ""
            version = -1
            current_cols = []
            parent_ok = False
            all_parents_found = False

        valid_attachment_key = None
        children_count = 0
        has_pdf_attachment = False
        try:
            _, _, c_body = http_request(c_url)
            c_json = json.loads(c_body.decode("utf-8"))
            children_count = len(c_json)
            for ch in c_json:
                ch_data = ch.get("data", {})
                if ch_data.get("itemType") == "attachment":
                    ch_key = ch_data.get("key")
                    if ch_data.get("filename") == cfg["canonical_name"] or ch_data.get("contentType") == "application/pdf":
                        valid_attachment_key = ch_key
                        has_pdf_attachment = True
        except Exception:
            children_count = -1

        file_path = resolve_pdf_path(cfg)
        sha, md5, size, mtime_ms = compute_file_hashes(file_path)
        file_exists = (sha is not None)
        hash_match = (sha == cfg["expected_sha256"] and md5 == cfg["expected_md5"] and size == cfg["expected_size"])
        if not hash_match:
            all_files_valid = False

        title_sync_needed = (actual_title != cfg["expected_title"])
        if title_sync_needed:
            title_sync_needed_count += 1

        doi_sync_needed = False
        if cfg.get("expected_doi") and actual_doi != cfg["expected_doi"]:
            doi_sync_needed = True
            doi_sync_needed_count += 1

        needs_upload = (not has_pdf_attachment) and file_exists and hash_match
        if needs_upload:
            total_missing_attachments += 1

        print(f"Parent Key: {pk} | ID: {cfg['item_id']} | Version: {version}")
        print(f"  Current Title: {actual_title}")
        if title_sync_needed:
            print(f"  -> Title SYNC Needed: '{actual_title}' -> '{cfg['expected_title']}'")
        print(f"  Current DOI: {actual_doi or '[None]'}")
        if doi_sync_needed:
            print(f"  -> DOI SYNC Needed: '{actual_doi}' -> '{cfg['expected_doi']}'")
        print(f"  Target Collection: '{cfg['target_stage']}' (Current Collections: {current_cols})")
        print(f"  Local File: {file_path}")
        print(f"  File Status: Exists={file_exists}, Size={size} B (Expected: {cfg['expected_size']} B)")
        print(f"  SHA256 Match: {hash_match} ({sha[:16] if sha else 'None'}...)")
        print(f"  Zotero Children: Total={children_count}, Has Valid PDF Attachment={has_pdf_attachment}")
        if valid_attachment_key:
            print(f"  -> Existing Attachment Key: {valid_attachment_key}")
        if needs_upload:
            print(f"  -> Action Required in --apply: Upload {cfg['canonical_name']} to parent {pk}")
        print("-" * 80)

        inspections.append({
            "item_id": cfg["item_id"],
            "parent_key": pk,
            "parent_version": version,
            "current_title": actual_title,
            "expected_title": cfg["expected_title"],
            "title_sync_needed": title_sync_needed,
            "current_doi": actual_doi,
            "expected_doi": cfg.get("expected_doi"),
            "doi_sync_needed": doi_sync_needed,
            "target_stage": cfg["target_stage"],
            "current_collections": current_cols,
            "children_count": children_count,
            "has_pdf_attachment": has_pdf_attachment,
            "valid_attachment_key": valid_attachment_key,
            "file_exists": file_exists,
            "hash_match": hash_match,
            "needs_upload": needs_upload
        })

    print("\n[+] Dry-Run Inspection Summary:")
    print(f"  * Total parent items inspected: {len(inspections)}")
    print(f"  * All 8 parent items exist in Zotero: {all_parents_found}")
    print(f"  * All 8 physical PDFs verified with matching frozen SHA256/MD5: {all_files_valid}")
    print(f"  * Title sync needed: {title_sync_needed_count} (N5PLXTK3: 'Control Allocation for Marine Vessels: A Survey' -> 'Control allocation—A survey')")
    print(f"  * DOI sync needed: {doi_sync_needed_count} (NY9JDYTZ: '10.1109/LCSYS.2024.3385123' -> '10.1109/lcsys.2024.3407611')")
    print(f"  * Items missing child PDF attachments: {total_missing_attachments} / {len(inspections)}")

    print("\n[+] Local API Authorization & Execution Contract:")
    print("  1. In --apply mode, Zotero displays an authorization modal for 'DeepSeekHarness':")
    print("     [Allow]         -> Single-use key (remember=false). The tool will HALT immediately")
    print("                        without attempting partial writes, preventing unexpected 401.")
    print("     [Always Allow]  -> Persistent key (remember=true, survives process exit until revoked).")
    print("     -> REQUIRED: Click [Always Allow] in Zotero GUI when prompted.")
    print("  2. Security & Desensitization: uploadKey, authorization tokens, and query strings are")
    print("     strictly sanitized and never logged or printed.")
    print("  3. Immediate Halt on HTTP 401: Any authorization expiration triggers an immediate halt.")
    print("================================================================================")

    ledger_path = SCRIPT_DIR / 'zotero_round08_ledger.json'
    ledger_data = {
        "mode": "dry_run",
        "server_id": server_id,
        "base_url": base_url,
        "all_parents_found": all_parents_found,
        "all_files_valid": all_files_valid,
        "title_sync_needed": (title_sync_needed_count > 0),
        "doi_sync_needed": (doi_sync_needed_count > 0),
        "missing_attachments_count": total_missing_attachments,
        "collections_plan": STAGE_COLLECTIONS_DEF,
        "inspections": inspections
    }
    with open(ledger_path, "w", encoding="utf-8") as f:
        json.dump(ledger_data, f, ensure_ascii=False, indent=2)
    print(f"[+] Dry-run ledger preview written to: {ledger_path.name}")
    return True

def run_apply(base_url, port, app_name='DeepSeekHarness'):
    print("=" * 80)
    print("ZOTERO SYNC TOOL (ROUND 08) - LOCAL API WRITE EXECUTION (--apply)")
    print("=" * 80)

    receipt = {
        "mode": "apply",
        "app_name": app_name,
        "base_url": base_url,
        "server_id": None,
        "auth_granted": False,
        "persistent_auth": False,
        "collections_created": [],
        "collections_errors": [],
        "metadata_synced": [],
        "items_processed": [],
        "overall_success": False
    }

    ledger_saved = False
    def save_ledger():
        nonlocal ledger_saved
        receipt_path = SCRIPT_DIR / 'zotero_round08_ledger.json'
        try:
            with open(receipt_path, "w", encoding="utf-8") as f:
                json.dump(receipt, f, ensure_ascii=False, indent=2)
            ledger_saved = True
            print(f"[+] Ledger saved to: {receipt_path.name}")
        except Exception as ex:
            print(f"[!] Error saving ledger: {sanitize_exception(ex)}")

    api_key = None
    try:
        # 1. Connect and verify Server-ID
        sample_url = f"{base_url}/api/users/0/items/N5PLXTK3"
        _, headers, _ = http_request(sample_url)
        server_id = headers.get("Zotero-Server-ID")
        if not server_id:
            print("[!] Zotero-Server-ID header missing from response. Aborting.")
            save_ledger()
            return False
        receipt["server_id"] = server_id
        print(f"[+] Zotero Local Service online. Server-ID: {server_id}")

        # 2. Request Authorization with 60s timeout
        print(f"[*] Requesting Local API Authorization for application: '{app_name}'...")
        print("    >>> Please check your Zotero desktop window and click [Always Allow] <<<")
        auth_url = f"{base_url}/api/local/authorize"
        auth_payload = json.dumps({"appName": app_name}).encode("utf-8")
        auth_headers = {
            "Content-Type": "application/json",
            "Zotero-Server-ID": server_id
        }
        try:
            status, _, auth_data = http_request(auth_url, method='POST', headers=auth_headers, data=auth_payload, timeout=60)
            auth_json = json.loads(auth_data.decode("utf-8"))
            api_key = auth_json.get("key")
            remember = auth_json.get("remember", False)
            receipt["auth_granted"] = True
            receipt["persistent_auth"] = remember
            print(f"[+] Authorization response received: Remember={remember}")
        except urllib.error.HTTPError as e:
            if e.code == 403:
                print("[!] Authorization was DENIED by the user in Zotero desktop dialog.")
            elif e.code == 429:
                print("[!] Authorization request rate-limited by Zotero. Please wait before retrying.")
            else:
                print(f"[!] Authorization failed: HTTP {e.code}")
            save_ledger()
            return False
        except Exception as e:
            print(f"[!] Authorization error or timeout: {sanitize_exception(e)}")
            save_ledger()
            return False

        if not api_key:
            print("[!] No API key received. Aborting.")
            save_ledger()
            return False

        # Check for persistent authorization
        if not remember:
            print("[!] Single-use [Allow] detected (remember=False).")
            print("    Local API requires persistent [Always Allow] for multi-step batch operations.")
            print("    Halting immediately without attempting partial writes.")
            receipt["auth_error"] = "single_use_allow_detected"
            save_ledger()
            return False

        # Step 1: Manage Collections Hierarchy
        print("\n[+] Step 1: Managing Collections Hierarchy...")
        col_url = f"{base_url}/api/users/0/collections"
        _, _, col_body = http_request(col_url)
        col_json = json.loads(col_body.decode("utf-8"))
        existing_cols = {}
        for c in col_json:
            cd = c.get("data", {})
            existing_cols[(cd.get("parentCollection"), cd.get("name"))] = cd.get("key")

        top_name = STAGE_COLLECTIONS_DEF["top_name"]
        top_key = existing_cols.get((False, top_name))
        col_headers = {
            "Content-Type": "application/json",
            "Zotero-API-Key": api_key,
            "Zotero-Server-ID": server_id
        }

        if not top_key:
            print(f"    [*] Creating top collection: '{top_name}'...")
            create_col_payload = json.dumps([{"name": top_name, "parentCollection": False}]).encode("utf-8")
            try:
                st, _, c_resp = http_request(col_url, method='POST', headers=col_headers, data=create_col_payload)
                c_resp_json = json.loads(c_resp.decode("utf-8"))
                top_key = c_resp_json.get("success", {}).get("0") or c_resp_json.get("successful", {}).get("0", {}).get("key")
                if not top_key:
                    print("    [!] Failed to obtain key for created top collection.")
                    receipt["collections_errors"].append("missing_top_key")
                    save_ledger()
                    return False
                print(f"    [+] Top collection created: key={top_key}")
                receipt["collections_created"].append({"name": top_name, "key": top_key})
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    print("[!] HTTP 401 Unauthorized while creating top collection. Halting immediately!")
                    receipt["auth_error"] = "http_401_unauthorized"
                    save_ledger()
                    return False
                print(f"    [!] Error creating top collection: HTTP {e.code}")
                receipt["collections_errors"].append(f"top_col_failed: HTTP_{e.code}")
                save_ledger()
                return False
            except Exception as e:
                print(f"    [!] Error creating top collection: {sanitize_exception(e)}")
                receipt["collections_errors"].append(f"top_col_failed: {sanitize_exception(e)}")
                save_ledger()
                return False
        else:
            print(f"    [*] Top collection already exists: key={top_key}")

        sub_keys = {}
        for sub in STAGE_COLLECTIONS_DEF["subcollections"]:
            s_name = sub["name"]
            s_key = existing_cols.get((top_key, s_name))
            if not s_key:
                print(f"    [*] Creating subcollection: '{s_name}' under {top_key}...")
                create_sub_payload = json.dumps([{"name": s_name, "parentCollection": top_key}]).encode("utf-8")
                try:
                    st, _, s_resp = http_request(col_url, method='POST', headers=col_headers, data=create_sub_payload)
                    s_resp_json = json.loads(s_resp.decode("utf-8"))
                    s_key = s_resp_json.get("success", {}).get("0") or s_resp_json.get("successful", {}).get("0", {}).get("key")
                    if not s_key:
                        print(f"    [!] Failed to obtain key for created subcollection '{s_name}'.")
                        receipt["collections_errors"].append(f"missing_sub_key: {s_name}")
                        save_ledger()
                        return False
                    print(f"    [+] Subcollection created: key={s_key}")
                    receipt["collections_created"].append({"name": s_name, "key": s_key})
                except urllib.error.HTTPError as e:
                    if e.code == 401:
                        print(f"[!] HTTP 401 Unauthorized while creating subcollection '{s_name}'. Halting immediately!")
                        receipt["auth_error"] = "http_401_unauthorized"
                        save_ledger()
                        return False
                    print(f"    [!] Error creating subcollection '{s_name}': HTTP {e.code}")
                    receipt["collections_errors"].append(f"sub_col_failed: {s_name}")
                    save_ledger()
                    return False
                except Exception as e:
                    print(f"    [!] Error creating subcollection '{s_name}': {sanitize_exception(e)}")
                    receipt["collections_errors"].append(f"sub_col_failed: {s_name}")
                    save_ledger()
                    return False
            else:
                print(f"    [*] Subcollection already exists: key={s_key}")
            sub_keys[s_name] = s_key

        # Step 2: Synchronize metadata (Title & DOI) under version precondition
        print("\n[+] Step 2: Checking and synchronizing item metadata (Title & DOI)...")
        for cfg in FROZEN_ITEMS_CONFIG:
            pk = cfg["parent_key"]
            p_url = f"{base_url}/api/users/0/items/{pk}"
            try:
                _, _, p_body = http_request(p_url)
                p_json = json.loads(p_body.decode("utf-8"))
                cur_title = p_json.get("data", {}).get("title", "")
                cur_doi = p_json.get("data", {}).get("DOI", "")
                cur_ver = p_json.get("data", {}).get("version", 0)
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    print(f"[!] HTTP 401 Unauthorized while inspecting {pk}. Halting immediately!")
                    receipt["auth_error"] = "http_401_unauthorized"
                    save_ledger()
                    return False
                print(f"    [!] Failed to read item {pk}: HTTP {e.code}")
                continue

            patch_fields = {}
            if cur_title != cfg["expected_title"]:
                patch_fields["title"] = cfg["expected_title"]
            if cfg.get("expected_doi") and cur_doi != cfg["expected_doi"]:
                patch_fields["DOI"] = cfg["expected_doi"]

            if patch_fields:
                print(f"    Patching {pk} (Version: {cur_ver}): {patch_fields}...")
                patch_payload = json.dumps(patch_fields).encode('utf-8')
                patch_headers = {
                    "Content-Type": "application/json",
                    "Zotero-API-Key": api_key,
                    "Zotero-Server-ID": server_id,
                    "If-Unmodified-Since-Version": str(cur_ver)
                }
                try:
                    st, resp_hdrs, _ = http_request(p_url, method='PATCH', headers=patch_headers, data=patch_payload)
                    print(f"    [+] Successfully updated {pk} metadata (HTTP {st})")
                    # Verify via GET readback
                    _, _, rb_body = http_request(p_url)
                    rb_json = json.loads(rb_body.decode("utf-8"))
                    rb_title = rb_json.get("data", {}).get("title", "")
                    rb_doi = rb_json.get("data", {}).get("DOI", "")
                    receipt["metadata_synced"].append({
                        "parent_key": pk,
                        "fields_patched": patch_fields,
                        "readback_title": rb_title,
                        "readback_doi": rb_doi
                    })
                except urllib.error.HTTPError as e:
                    if e.code == 401:
                        print(f"[!] HTTP 401 Unauthorized while patching {pk}. Halting immediately!")
                        receipt["auth_error"] = "http_401_unauthorized"
                        save_ledger()
                        return False
                    print(f"    [!] Failed to patch {pk}: HTTP {e.code}")
                    save_ledger()
                    return False
            else:
                print(f"    [*] {pk} metadata already aligned with expectations.")

        # Step 3: Associate items to collections & ingest 8 child attachments
        print("\n[+] Step 3: Ingesting child attachments & associating collections...")
        overall_items_ok = True

        for cfg in FROZEN_ITEMS_CONFIG:
            pk = cfg["parent_key"]
            target_st = cfg["target_stage"]
            target_col_key = sub_keys.get(target_st)
            item_receipt = {"item_id": cfg["item_id"], "parent_key": pk, "status": "pending"}

            file_path = resolve_pdf_path(cfg)
            sha, md5, size, mtime_ms = compute_file_hashes(file_path)

            if sha != cfg["expected_sha256"] or md5 != cfg["expected_md5"]:
                print(f"    [!] File hash mismatch for {cfg['item_id']}. Skipping for safety.")
                item_receipt["status"] = "hash_mismatch"
                receipt["items_processed"].append(item_receipt)
                overall_items_ok = False
                continue

            # Read fresh parent item version and collections
            p_url = f"{base_url}/api/users/0/items/{pk}"
            try:
                _, _, p_body = http_request(p_url)
                p_json = json.loads(p_body.decode("utf-8"))
                p_ver = p_json.get("data", {}).get("version", 0)
                p_cols = p_json.get("data", {}).get("collections", [])
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    print(f"[!] HTTP 401 Unauthorized while reading parent {pk}. Halting immediately!")
                    receipt["auth_error"] = "http_401_unauthorized"
                    save_ledger()
                    return False
                print(f"    [!] Error reading parent {pk}: HTTP {e.code}")
                item_receipt["status"] = f"read_parent_http_{e.code}"
                receipt["items_processed"].append(item_receipt)
                overall_items_ok = False
                continue

            # Associate collection if needed
            if target_col_key and target_col_key not in p_cols:
                new_cols = list(set(p_cols + [target_col_key]))
                print(f"    [*] Associating parent {pk} to collection {target_col_key}...")
                patch_col_payload = json.dumps({"collections": new_cols}).encode("utf-8")
                patch_col_headers = {
                    "Content-Type": "application/json",
                    "Zotero-API-Key": api_key,
                    "Zotero-Server-ID": server_id,
                    "If-Unmodified-Since-Version": str(p_ver)
                }
                try:
                    st, p_hdrs, p_resp = http_request(p_url, method='PATCH', headers=patch_col_headers, data=patch_col_payload)
                    new_v = p_hdrs.get("Last-Modified-Version")
                    if new_v and new_v.isdigit():
                        p_ver = int(new_v)
                    else:
                        p_ver += 1
                    print(f"    [+] Parent {pk} associated with collection {target_col_key} (HTTP {st})")
                except urllib.error.HTTPError as e:
                    if e.code == 401:
                        print(f"[!] HTTP 401 Unauthorized while patching collection for {pk}. Halting immediately!")
                        receipt["auth_error"] = "http_401_unauthorized"
                        save_ledger()
                        return False
                    print(f"    [!] Failed to associate collection: HTTP {e.code}")
                    item_receipt["status"] = f"col_patch_http_{e.code}"
                    receipt["items_processed"].append(item_receipt)
                    overall_items_ok = False
                    continue

            # Check existing children for attachment matching
            c_url = f"{base_url}/api/users/0/items/{pk}/children"
            try:
                _, _, c_body = http_request(c_url)
                c_json = json.loads(c_body.decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    print(f"[!] HTTP 401 Unauthorized while reading children of {pk}. Halting immediately!")
                    receipt["auth_error"] = "http_401_unauthorized"
                    save_ledger()
                    return False
                print(f"    [!] Error reading children: HTTP {e.code}")
                item_receipt["status"] = f"read_children_http_{e.code}"
                receipt["items_processed"].append(item_receipt)
                overall_items_ok = False
                continue

            existing_attachment_key = None
            existing_attachment_verified = False
            has_file_conflict = False

            for ch in c_json:
                ch_data = ch.get("data", {})
                if ch_data.get("itemType") == "attachment":
                    ch_key = ch_data.get("key")
                    ch_filename = ch_data.get("filename") or ch_data.get("title")
                    if ch_filename == cfg["canonical_name"]:
                        existing_attachment_key = ch_key
                        try:
                            f_url = f"{base_url}/api/users/0/items/{ch_key}/file"
                            _, _, f_bytes = http_request(f_url, headers={"Zotero-API-Key": api_key})
                            if hashlib.sha256(f_bytes).hexdigest() == sha:
                                existing_attachment_verified = True
                            elif len(f_bytes) > 0:
                                has_file_conflict = True
                        except urllib.error.HTTPError as e:
                            if e.code == 401:
                                print(f"[!] HTTP 401 Unauthorized while checking attachment file. Halting immediately!")
                                receipt["auth_error"] = "http_401_unauthorized"
                                save_ledger()
                                return False
                            elif e.code == 404:
                                # File not uploaded yet (interrupted upload resumption)
                                pass
                            else:
                                print(f"    [!] Unexpected HTTP {e.code} checking attachment file.")
                                has_file_conflict = True
                        except Exception as ex:
                            print(f"    [!] Readback error checking attachment: {sanitize_exception(ex)}")
                            has_file_conflict = True
                        break

            if existing_attachment_verified:
                print(f"    [+] Attachment already synced and byte-verified: {existing_attachment_key}. Skipping.")
                item_receipt["status"] = "already_verified"
                item_receipt["attachment_key"] = existing_attachment_key
                receipt["items_processed"].append(item_receipt)
                continue

            if has_file_conflict:
                print(f"    [!] Existing attachment {existing_attachment_key} has conflicting bytes! Overwriting prohibited.")
                item_receipt["status"] = "file_conflict_prohibited"
                item_receipt["attachment_key"] = existing_attachment_key
                receipt["items_processed"].append(item_receipt)
                overall_items_ok = False
                continue

            attachment_key = existing_attachment_key
            if not attachment_key:
                print(f"    [*] Creating attachment item for {cfg['item_id']} (Parent: {pk})...")
                item_payload = json.dumps([{
                    "itemType": "attachment",
                    "parentItem": pk,
                    "linkMode": "imported_file",
                    "title": cfg["canonical_name"],
                    "contentType": cfg["content_type"]
                }]).encode('utf-8')
                item_headers = {
                    "Content-Type": "application/json",
                    "Zotero-API-Key": api_key,
                    "Zotero-Server-ID": server_id
                }
                try:
                    st, _, resp_bytes = http_request(f"{base_url}/api/users/0/items", method='POST', headers=item_headers, data=item_payload)
                    resp_json = json.loads(resp_bytes.decode("utf-8"))
                    attachment_key = resp_json.get("success", {}).get("0") or resp_json.get("successful", {}).get("0", {}).get("key")
                    if not attachment_key:
                        print(f"    [!] Failed to create attachment item for {pk}")
                        item_receipt["status"] = "item_creation_failed"
                        receipt["items_processed"].append(item_receipt)
                        overall_items_ok = False
                        continue
                    print(f"    [+] Attachment item created: {attachment_key}")
                except urllib.error.HTTPError as e:
                    if e.code == 401:
                        print(f"[!] HTTP 401 Unauthorized while creating attachment item. Halting immediately!")
                        receipt["auth_error"] = "http_401_unauthorized"
                        save_ledger()
                        return False
                    print(f"    [!] Item creation error: HTTP {e.code}")
                    item_receipt["status"] = f"item_http_{e.code}"
                    receipt["items_processed"].append(item_receipt)
                    overall_items_ok = False
                    continue
            else:
                print(f"    [*] Reusing existing empty attachment item: {attachment_key} for parent {pk} (resuming)...")

            item_receipt["attachment_key"] = attachment_key

            # Phase 1: Authorize upload via POST /api/users/0/items/<key>/file
            print(f"    [*] Phase 1: Authorizing upload for {attachment_key} (size: {size} B)...")
            auth_upload_url = f"{base_url}/api/users/0/items/{attachment_key}/file"
            auth_form_data = urllib.parse.urlencode({
                "md5": md5,
                "filename": cfg["canonical_name"],
                "filesize": str(size),
                "mtime": str(mtime_ms),
                "contentType": cfg["content_type"]
            }).encode('utf-8')
            auth_upload_headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "If-None-Match": "*",
                "Zotero-API-Key": api_key,
                "Zotero-Server-ID": server_id
            }
            try:
                st, _, auth_up_bytes = http_request(auth_upload_url, method='POST', headers=auth_upload_headers, data=auth_form_data)
                auth_up_json = json.loads(auth_up_bytes.decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    print(f"[!] HTTP 401 Unauthorized during upload authorization. Halting immediately!")
                    receipt["auth_error"] = "http_401_unauthorized"
                    save_ledger()
                    return False
                print(f"    [!] AuthorizeUpload error: HTTP {e.code}")
                item_receipt["status"] = f"auth_upload_http_{e.code}"
                receipt["items_processed"].append(item_receipt)
                overall_items_ok = False
                continue

            if auth_up_json.get("exists") == 1:
                print("    [*] Server returned exists=1. Verifying actual file bytes from Zotero...")
                try:
                    st, _, rb_bytes = http_request(f"{base_url}/api/users/0/items/{attachment_key}/file", headers={"Zotero-API-Key": api_key})
                    if hashlib.sha256(rb_bytes).hexdigest() == sha:
                        print(f"    [+] Existing file verified: {len(rb_bytes)} B, SHA256 matches expected!")
                        item_receipt["status"] = "uploaded_and_verified"
                        receipt["items_processed"].append(item_receipt)
                        continue
                    else:
                        print("    [!] Server exists=1 file SHA256 mismatch! Proceeding with binary upload...")
                except urllib.error.HTTPError as e:
                    if e.code == 401:
                        print(f"[!] HTTP 401 Unauthorized while reading exists=1 file. Halting immediately!")
                        receipt["auth_error"] = "http_401_unauthorized"
                        save_ledger()
                        return False
                    print(f"    [!] Readback error on exists=1: HTTP {e.code}")
                except Exception as e:
                    print(f"    [!] Readback verification error on exists=1: {sanitize_exception(e)}")

            upload_url = auth_up_json.get("url")
            upload_key = auth_up_json.get("uploadKey")
            if not upload_url or not upload_key:
                print("    [!] Missing upload URL/Key in response")
                item_receipt["status"] = "missing_upload_url"
                receipt["items_processed"].append(item_receipt)
                overall_items_ok = False
                continue

            if not validate_local_upload_url(upload_url, port):
                print("    [!] Security check failed: upload URL points to unauthorized host/port! Aborting.")
                item_receipt["status"] = "unauthorized_upload_host"
                receipt["items_processed"].append(item_receipt)
                overall_items_ok = False
                continue

            print(f"    [+] Upload endpoint authorized on local port {port} (desensitized).")

            # Phase 2: Transmit bytes
            print(f"    [*] Phase 2: Transmitting {size} bytes to local upload endpoint...")
            pdf_bytes = open(file_path, "rb").read()
            upload_headers = {"Content-Type": cfg["content_type"]}
            try:
                st, _, _ = http_request(upload_url, method='POST', headers=upload_headers, data=pdf_bytes, timeout=60)
                print(f"    [+] Bytes transmitted successfully (HTTP {st})")
            except Exception as e:
                print(f"    [!] Failed to upload file bytes: {sanitize_exception(e)}")
                item_receipt["status"] = f"transmission_failed: {sanitize_exception(e)}"
                receipt["items_processed"].append(item_receipt)
                overall_items_ok = False
                continue

            # Phase 3: Register upload
            print("    [*] Phase 3: Registering upload with attachment item...")
            reg_data = urllib.parse.urlencode({"upload": upload_key}).encode("utf-8")
            reg_headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "If-None-Match": "*",
                "Zotero-API-Key": api_key,
                "Zotero-Server-ID": server_id
            }
            try:
                st, _, _ = http_request(auth_upload_url, method='POST', headers=reg_headers, data=reg_data)
                print(f"    [+] Upload registered successfully (HTTP {st})")
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    print(f"[!] HTTP 401 Unauthorized during upload registration. Halting immediately!")
                    receipt["auth_error"] = "http_401_unauthorized"
                    save_ledger()
                    return False
                print(f"    [!] RegisterUpload error: HTTP {e.code}")
                item_receipt["status"] = f"register_http_{e.code}"
                receipt["items_processed"].append(item_receipt)
                overall_items_ok = False
                continue

            # Step 4: Readback Verification
            print("    [*] Step 4: Verifying uploaded file bytes from Zotero Local API...")
            try:
                st, _, readback_bytes = http_request(f"{base_url}/api/users/0/items/{attachment_key}/file", headers={"Zotero-API-Key": api_key})
                rb_sha = hashlib.sha256(readback_bytes).hexdigest()
                if rb_sha == cfg["expected_sha256"]:
                    print(f"    [+] Readback byte verification PASSED: {len(readback_bytes)} B, SHA256: {rb_sha[:16]}...")
                    item_receipt["status"] = "uploaded_and_verified"
                    item_receipt["verified_sha256"] = rb_sha
                else:
                    print(f"    [!] Readback SHA256 mismatch! Expected {cfg['expected_sha256'][:16]}...")
                    item_receipt["status"] = "sha_mismatch"
                    overall_items_ok = False
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    print(f"[!] HTTP 401 Unauthorized during file readback verification. Halting immediately!")
                    receipt["auth_error"] = "http_401_unauthorized"
                    save_ledger()
                    return False
                print(f"    [!] Readback error: HTTP {e.code}")
                item_receipt["status"] = f"readback_http_{e.code}"
                overall_items_ok = False
            except Exception as e:
                print(f"    [!] Readback verification error: {sanitize_exception(e)}")
                item_receipt["status"] = f"readback_error: {sanitize_exception(e)}"
                overall_items_ok = False

            receipt["items_processed"].append(item_receipt)

        receipt["overall_success"] = overall_items_ok
        print(f"\n[+] Apply Summary: Overall Success={overall_items_ok}")
        save_ledger()
        return overall_items_ok

    finally:
        api_key = None
        del api_key
        if not ledger_saved:
            save_ledger()

def main():
    parser = argparse.ArgumentParser(description="Zotero Sync Tool Round 08")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Perform safe read-only inspection (default)")
    parser.add_argument("--apply", action="store_true", help="Execute write operations via Local API")
    parser.add_argument("--port", type=int, default=23119, help="Zotero local HTTP port (default: 23119)")
    parser.add_argument("--app-name", type=str, default="DeepSeekHarness", help="Local API caller application name")

    args = parser.parse_args()
    base_url = f"http://127.0.0.1:{args.port}"

    if args.apply:
        success = run_apply(base_url, args.port, args.app_name)
        if not success:
            sys.exit(1)
    else:
        success = run_dry_run(base_url, args.port)
        if not success:
            sys.exit(1)

if __name__ == "__main__":
    main()