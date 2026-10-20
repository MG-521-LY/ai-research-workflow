#!/usr/bin/env python3
"""Topic screening and target management over canonical InstSci acquisition manifests."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from dataclasses import asdict
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit
from uuid import uuid4

import _screening as screening

SCHEMA = "instsci.topic.v1"
BLOCKERS = {"auth_required", "human_verification_required", "waf_blocked", "daily_limit_reached", "quota_state_error"}
EVIDENCE = {"oa_direct", "publisher_open_pdf", "browser_verified"}


def normalize_doi(value):
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", str(value or ""), flags=re.I).strip().lower()
    if value and not re.fullmatch(r"10\.\d{4,9}/\S+", value):
        raise ValueError("Invalid DOI; supply an observed DOI without whitespace")
    return value


def count(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10000:
        raise ValueError("Target must be an integer from 1 to 10000")
    return value


def route_for(paper, requested="auto"):
    portal = paper.get("provenance", {}).get("portal", "")
    if requested in {"cnki", "wanfang"}:
        return requested if portal in {"cnki", "wanfang"} else None
    if requested == "doi":
        return "doi" if paper.get("doi") else None
    return portal if portal in {"cnki", "wanfang"} else ("doi" if paper.get("doi") else None)


def acquisition_identity(paper, route):
    if route == "doi":
        return {"doi": paper.get("doi", "")}
    return {"title": screening.norm(paper["title"]),
            "first_author": screening.norm(paper.get("provenance", {}).get("first_author", ""))}


def portal_url(paper, route):
    url = paper.get("url", "")
    host = (urlsplit(url).hostname or "").lower()
    domains = ("cnki.net", "cnki.com.cn") if route == "cnki" else ("wanfangdata.com.cn",)
    return url if any(host == domain or host.endswith("." + domain) for domain in domains) else ""


def validate_state(data):
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError("Unrecognized or damaged topic task; refusing to initialize over it")
    count(data["target"])
    if not isinstance(data.get("task_id"), str) or not data["task_id"]:
        raise ValueError("Missing task ownership identity")
    if not isinstance(data["papers"], list) or not isinstance(data["batches"], list):
        raise ValueError("Damaged task arrays")
    ids = [p["id"] for p in data["papers"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate candidate identities in task")
    if len({b["id"] for b in data["batches"]}) != len(data["batches"]):
        raise ValueError("Duplicate batch identities in task")
    for b in data["batches"]:
        if b["phase"] not in {"reserved", "started", "finished", "closed"} or not set(b["ids"]).issubset(ids):
            raise ValueError("Damaged batch reservation")
    return data


@contextmanager
def transaction(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("Initialize the task first")
    with screening.task_lock(root):
        data = validate_state(screening.read_json(root / "topic.json"))
        owner = screening.read_json(root / ".topic-owner.json")
        if owner.get("task_id") != data["task_id"]:
            raise ValueError("Task identity does not match its ownership record")
        yield data
        data["updated_at"] = screening.now()
        screening.write_json(root / "topic.json", data)


def init(root, topic, target=100, plan=None, heat_weight=0.0):
    root = Path(root).resolve()
    if not str(topic).strip():
        raise ValueError("Research topic is required")
    count(target)
    screening.finite_number(heat_weight, "heat_weight", 0, 1)
    root.mkdir(parents=True, exist_ok=True)
    with screening.task_lock(root):
        if (root / "topic.json").exists() or (root / ".topic-owner.json").exists():
            raise ValueError("Task already exists")
        if any(p.name not in {".task.lock", "search-plan.json"} for p in root.iterdir()):
            raise ValueError("Use a fresh task directory; existing output must not be overwritten")
        if plan is not None and (not isinstance(plan, dict) or not isinstance(plan.get("queries"), list)):
            raise ValueError("Search plan must contain a queries array")
        data = {"schema": SCHEMA, "task_id": uuid4().hex, "topic": str(topic).strip(), "target": target,
                "min_relevance": 65, "allow_caj": False, "heat_weight": heat_weight,
                "papers": [], "search_log": [], "search_plan": screening.scrub(plan or {}),
                "batches": [], "retry_ids": [], "resolved_blockers": [], "observed_files": {},
                "created_at": screening.now()}
        screening.write_json(root / ".topic-owner.json", {"task_id": data["task_id"]})
        screening.write_json(root / "topic.json", data)
    return {"task": str(root), "target": target}


def score(data):
    # Reuse the original sample-relative metric policy, separating citation providers.
    groups = {}
    for p in data["papers"]:
        source = p.get("provenance", {}).get("metric_source", "unknown")
        groups.setdefault(source, []).append(p)
    for papers in groups.values():
        screening.score_all({"papers": papers})
        for p in papers:
            w = data["heat_weight"]
            p["score"] = round((1 - w) * p.get("relevance", 0) + 100 * w * p["heat"], 4)


def ingest(data, payload):
    rows = payload.get("results", payload.get("papers")) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("Expected a candidate array, papers object, or InstSci search results object")
    added = merged = 0
    result = []
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("Each candidate must be an object")
        incoming = dict(raw)
        incoming["doi"] = normalize_doi(raw.get("doi", ""))
        incoming["source"] = raw.get("source") or raw.get("journal", "")
        incoming["url"] = raw.get("url") or raw.get("s2_url", "")
        host = (urlsplit(incoming["url"]).hostname or "").lower()
        portal = raw.get("portal", "")
        if not portal:
            if host == "cnki.net" or host.endswith(".cnki.net") or host.endswith(".cnki.com.cn"):
                portal = "cnki"
            elif host == "wanfangdata.com.cn" or host.endswith(".wanfangdata.com.cn"):
                portal = "wanfang"
        if portal not in {"", "cnki", "wanfang"}:
            raise ValueError("Unknown Chinese literature portal")
        citations = raw.get("citation_counts") or {}
        if not isinstance(citations, dict):
            raise ValueError("citation_counts must be a provider map")
        metric_source = raw.get("metric_source") or next((s for s in ("semantic_scholar", "openalex", "crossref") if s in citations), "")
        if metric_source in citations:
            incoming["citations"] = citations[metric_source]
        elif incoming.get("citations") is not None:
            metric_source = metric_source or portal or "user_observed"
        sources = raw.get("sources") or ([portal] if portal else [])
        if not isinstance(sources, list) or any(not isinstance(s, str) for s in sources):
            raise ValueError("sources must be an array of provider names")
        one = screening.ingest(data, [incoming])
        p = screening.find(data, one["papers"][0]["id"])
        previous = p.get("provenance", {})
        authors = raw.get("authors", [])
        first = raw.get("first_author") or (authors[0] if isinstance(authors, list) and authors else "")
        author_update = "first_author" in raw or bool(authors)
        p["provenance"] = screening.scrub({
            **previous, "portal": portal or previous.get("portal", ""),
            "sources": list(dict.fromkeys(previous.get("sources", []) + sources)),
            "citation_counts": {**previous.get("citation_counts", {}), **citations},
            "metric_source": metric_source or previous.get("metric_source") or portal or "unknown",
            "first_author": first if author_update else previous.get("first_author", ""),
            "zotero_item_key": raw.get("zotero_item_key") or previous.get("zotero_item_key", "")})
        added += one["added"]
        merged += one["merged"]
        result.extend(one["papers"])
    if isinstance(payload, dict):
        logs = payload.get("search_log", [])
        if not isinstance(logs, list):
            raise ValueError("search_log must be an array")
        data["search_log"].extend(screening.scrub(logs))
    score(data)
    return {"added": added, "merged": merged, "papers": result}


def pdf_info(path):
    info = screening.verify_file(path)
    import pymupdf
    with pymupdf.open(info["file"]) as doc:
        if not doc.is_pdf or doc.needs_pass or doc.page_count < 1:
            raise ValueError("PDF is unreadable, encrypted, or empty")
        # Force parsing of every page, beyond a file signature check.
        for page in doc:
            page.get_text()
        info["pages"] = doc.page_count
    return info


def expected_key(paper, route):
    return normalize_doi(paper.get("doi")) if route == "doi" else paper["id"]


def derive(data):
    by_id = {p["id"]: p for p in data["papers"]}
    outcomes = {}
    successes = {}
    active, integrity, blockers = [], [], []
    for batch in data["batches"]:
        if batch["phase"] in {"reserved", "started"}:
            active.append(batch["id"])
        manifest = Path(batch["manifest"])
        if not manifest.exists():
            if batch["phase"] == "finished":
                integrity.append({"batch": batch["id"], "reason": "missing_manifest; inspect outputs then close-batch"})
            continue
        rows = screening.read_json(manifest)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"Invalid canonical manifest: {manifest}")
        mapping = {expected_key(by_id[i], batch["route"]): i for i in batch["ids"]}
        seen = set()
        for row in rows:
            key = normalize_doi(row.get("doi")) if batch["route"] == "doi" else str(row.get("record_id", ""))
            if key not in mapping or key in seen:
                integrity.append({"batch": batch["id"], "reason": "unexpected_or_duplicate_manifest_identifier", "identifier": key})
                continue
            seen.add(key)
            pid = mapping[key]
            state = {k: row.get(k) for k in ("file_status", "standard_status", "result_evidence", "next_action")}
            state.update(batch=batch["id"], manifest=str(manifest))
            outcomes[pid] = state
            blocker_key = f"{batch['id']}:{pid}"
            if row.get("standard_status") in BLOCKERS and blocker_key not in data["resolved_blockers"]:
                blockers.append({"id": pid, "key": blocker_key, **state})
            if row.get("file_status") != "success" or row.get("standard_status") != "success":
                continue
            try:
                if row.get("result_evidence") not in EVIDENCE or row.get("verified_match") is False:
                    raise ValueError("Manifest does not contain accepted identity/access evidence")
                if batch["route"] != "doi":
                    if row.get("result_evidence") != "browser_verified" or row.get("title_match") is not True:
                        raise ValueError("Chinese PDF requires browser evidence and exact-title verification")
                    if screening.norm(row.get("title", "")) != screening.norm(by_id[pid]["title"]):
                        raise ValueError("Requested Chinese title does not match manifest title")
                    current_author = by_id[pid].get("provenance", {}).get("first_author", "")
                    if screening.norm(row.get("first_author", "")) != screening.norm(current_author):
                        raise ValueError("First author changed since canonical Chinese identity verification")
                    if row.get("author_disambiguation_used") and row.get("author_match") is not True:
                        raise ValueError("Ambiguous first-author identity is not verified")
                path = Path(str(row.get("pdf_path") or ""))
                if not str(row.get("pdf_path") or "").strip():
                    raise ValueError("Manifest has no PDF path")
                if not path.is_absolute():
                    path = manifest.parent / path
                info = pdf_info(path)
                baseline_key = f"{batch['id']}:{pid}"
                baseline = data["observed_files"].get(baseline_key)
                if baseline and baseline != info["sha256"]:
                    raise ValueError("PDF content changed since its first verified observation")
                data["observed_files"][baseline_key] = info["sha256"]
                successes.setdefault(pid, {**state, **info, "canonical_row": row})
            except (ValueError, OSError, RuntimeError) as exc:
                state["verification_error"] = str(exc)
        if batch["phase"] == "finished" and seen != set(mapping):
            integrity.append({"batch": batch["id"], "reason": "partial_manifest; inspect outputs then close-batch"})
    verified, digests = {}, set()
    for p in screening.ranked(data):
        found = successes.get(p["id"])
        if found:
            if found["sha256"] in digests:
                outcomes[p["id"]] = {**found, "verification_error": "Duplicate PDF content; counted once"}
                continue
            digests.add(found["sha256"])
            verified[p["id"]] = found
    blockers = [b for b in blockers if b["id"] not in successes]
    return {"verified": verified, "outcomes": outcomes, "active_batches": active,
            "integrity_issues": integrity, "blockers": blockers}


def summary(data, view):
    n = len(view["verified"])
    return {"topic": data["topic"], "target": data["target"], "verified_downloaded": n,
            "remaining": max(0, data["target"] - n), "complete": n >= data["target"],
            "candidates": len(data["papers"]), "eligible": len(screening.ranked(data)),
            "unreviewed": sum(not p.get("decision") for p in data["papers"]),
            "active_batches": view["active_batches"], "integrity_issues": view["integrity_issues"],
            "waiting_user": view["blockers"], "heat_weight": data["heat_weight"],
            "unroutable": [p["id"] for p in screening.ranked(data) if not route_for(p)]}


def plan_batch(data, root, limit=1, requested_route="auto", institution=""):
    if isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("Batch limit must be between 1 and 100")
    view = derive(data)
    s = summary(data, view)
    if s["complete"] or view["active_batches"] or view["integrity_issues"] or view["blockers"]:
        return {"batch": None, "summary": s}
    attempted = {pid for b in data["batches"] if b.get("started_at") or Path(b["manifest"]).exists() for pid in b["ids"]}
    candidates = [p for p in screening.ranked(data) if p["id"] not in view["verified"]
                  and (p["id"] not in attempted or p["id"] in data["retry_ids"])
                  and route_for(p, requested_route)]
    if not candidates:
        return {"batch": None, "next_action": "expand_search_or_review_more_candidates", "summary": s}
    route = route_for(candidates[0], requested_route)
    def group(p):
        r = route_for(p, requested_route)
        if r != "doi":
            return r
        from instsci.publisher_profiles import infer_publisher_profile
        profile = infer_publisher_profile(p["doi"])
        return (r, profile.name if profile else p["id"])
    chosen = [p for p in candidates if group(p) == group(candidates[0])][:min(limit, s["remaining"])]
    bid = uuid4().hex[:16]
    input_dir = Path(root).resolve() / "plans" / bid
    run_dir = Path(root).resolve() / "acquisition" / bid
    input_dir.mkdir(parents=True)
    argv = [sys.executable, "-m", "instsci.cli"]
    if route == "doi":
        # This installed upstream papers reader accepts only one DOI per line,
        # despite broader CSV support advertised elsewhere in its documentation.
        input_file = input_dir / "selected.txt"
        screening.atomic_text(input_file, "".join(p["doi"] + "\n" for p in chosen))
        screening.write_json(input_dir / "selected_metadata.json", [
            {"id": p["id"], "doi": p["doi"], "title": p["title"], "year": p.get("year"), "url": p.get("url", "")} for p in chosen])
        argv += ["papers", str(input_file), "--publisher", "auto", "--output", str(run_dir), "--no-retry", "--concurrency", "1"]
        if institution:
            argv += ["--institution", institution]
        manifest = run_dir / "complete" / "manifest.json"
    else:
        input_file = input_dir / "selected.json"
        screening.write_json(input_file, [{"record_id": p["id"], "title": p["title"], "url": portal_url(p, route),
            "first_author": p["provenance"].get("first_author", ""),
            "zotero_item_key": p["provenance"].get("zotero_item_key", "")} for p in chosen])
        argv += [route + "-batch", str(input_file), "--output", str(run_dir)]
        if route == "cnki":
            argv += ["--navigation-mode", "search"]
        manifest = run_dir / "manifest.json"
    batch = {"id": bid, "ids": [p["id"] for p in chosen], "route": route, "phase": "reserved",
             "manifest": str(manifest), "argv": argv, "created_at": screening.now(),
             "input_file": str(input_file), "input_sha256": hashlib.sha256(input_file.read_bytes()).hexdigest(),
             "identities": {p["id"]: acquisition_identity(p, route) for p in chosen}}
    data["batches"].append(batch)
    data["retry_ids"] = [i for i in data["retry_ids"] if i not in batch["ids"]]
    screening.write_json(input_dir / "batch.json", batch)
    return {"batch": batch, "summary": s}


def batch_by_id(data, bid):
    return next(b for b in data["batches"] if b["id"] == bid)


def pid_alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return ctypes.get_last_error() != 87  # Invalid PID only; access failures fail closed.
        code = wintypes.DWORD()
        try:
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False


def acquire(root, bid):
    with transaction(root) as data:
        batch = batch_by_id(data, bid)
        if batch["phase"] != "reserved":
            raise ValueError("Batch was already started; inspect its manifest and close it before retrying")
        if hashlib.sha256(Path(batch["input_file"]).read_bytes()).hexdigest() != batch["input_sha256"]:
            raise ValueError("Batch input changed since reservation; inspect and re-plan")
        if any(acquisition_identity(screening.find(data, pid), batch["route"]) != batch["identities"][pid] for pid in batch["ids"]):
            raise ValueError("Candidate identity changed since reservation; inspect and re-plan")
        view = derive(data)
        if view["blockers"] or view["integrity_issues"] or len(view["verified"]) >= data["target"]:
            raise ValueError("Queue paused or target already reached")
        if len(batch["ids"]) > data["target"] - len(view["verified"]):
            raise ValueError("Target shrank after planning; close this reservation and plan again")
        if any(not screening.eligible(screening.find(data, pid), data) for pid in batch["ids"]):
            raise ValueError("A reserved candidate needs screening again; close the reservation")
        batch.update(phase="started", started_at=screening.now(), runner_pid=os.getpid())
        argv = list(batch["argv"])
    # No shell interpolation, no direct PDF requests; keep InstSci's browser behavior intact.
    process = subprocess.Popen(argv, env={**os.environ, "PYTHONUTF8": "1"})
    with transaction(root) as data:
        batch_by_id(data, bid)["child_pid"] = process.pid
    rc = process.wait()
    with transaction(root) as data:
        batch_by_id(data, bid).update(phase="finished", return_code=rc, finished_at=screening.now())
        s = summary(data, derive(data))
    return {"batch": bid, "backend_return_code": rc, "summary": s}


def export(data, root):
    root = Path(root)
    view = derive(data)
    s = summary(data, view)
    records = []
    zotero_rows = []
    for p in sorted(data["papers"], key=lambda p: (-p.get("score", 0), p["id"])):
        found = view["verified"].get(p["id"])
        outcome = found or view["outcomes"].get(p["id"], {})
        records.append({**{k: v for k, v in p.items() if k != "status"},
                        "counted_success": bool(found), "acquisition": {k: v for k, v in outcome.items() if k != "canonical_row"}})
        if found:
            row = found["canonical_row"]
            zotero_rows.append({**row, "topic_candidate_id": p["id"], "pdf_path": found["file"],
                "doi": row.get("doi") or p.get("doi", ""),
                "zotero_item_key": row.get("zotero_item_key") or p.get("provenance", {}).get("zotero_item_key", ""),
                "title": p["title"], "authors": p.get("authors", ""), "year": p.get("year")})
    screening.write_json(root / "report.json", {"summary": s, "search_plan": data["search_plan"], "search_log": data["search_log"], "papers": records})
    fields = ["id", "title", "authors", "year", "doi", "decision", "relevance", "evidence", "reason", "score", "counted_success", "acquisition", "provenance"]
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(fields)
    for row in records:
        writer.writerow([screening.cell(row.get(k)) for k in fields])
    screening.atomic_text(root / "library.csv", "\ufeff" + stream.getvalue())
    screening.write_json(root / "zotero_manifest.json", zotero_rows)
    from instsci.zotero_mcp import build_zotero_mcp_handoff
    handoff = build_zotero_mcp_handoff(root / "zotero_manifest.json", attach_mode="required")
    screening.write_json(root / "zotero_handoff.json", handoff)
    def safe(value):
        return str(value).replace("\n", " ").replace("\r", " ").replace("<", "&lt;")
    lines = ["# 主题文献任务报告", "", f"研究需求：{safe(data['topic'])}", "",
             f"当前核验成功：{s['verified_downloaded']}/{s['target']}；尚缺 {s['remaining']} 篇。", "",
             "计数来自 InstSci 原始清单及当前文件复核；下载成功不代表 Zotero 已入库。", "",
             "热度仅反映当前分组候选样本；不同来源被引指标分别处理，不作为学术质量指标。", "",
             "## 检索与筛选边界", "", "```json", json.dumps(data["search_plan"], ensure_ascii=False, indent=2), "```", "",
             "## 文献与筛选理由", ""]
    for p in records:
        lines.append(f"- {safe(p['title'])}：{p.get('decision', 'unreviewed')}；依据 {p.get('evidence', 'unknown')}；{safe(p.get('reason', '未评审'))}；核验计数 {p['counted_success']}")
    lines += ["", "## 待办与阻塞", "", "```json", json.dumps({k: s[k] for k in ("active_batches", "integrity_issues", "waiting_user", "unroutable")}, ensure_ascii=False, indent=2), "```"]
    screening.atomic_text(root / "report.md", "\n".join(lines) + "\n")
    return {"summary": s, "report": str(root / "report.md"), "zotero_selected": len(zotero_rows), "zotero_actions": len(handoff["actions"])}


def run(args):
    root = args.task.resolve()
    if args.command == "init":
        return init(root, args.topic, args.count, screening.read_json(args.plan) if args.plan else None, args.heat_weight)
    if args.command == "acquire":
        return acquire(root, args.batch)
    if args.command == "search":
        from instsci.multi_search import search_with_status
        response = search_with_status(args.query, limit=args.limit, year_range=args.year, sources=args.sources)
        payload = {"results": [{**asdict(p), "query": args.query} for p in response.results],
                   "search_log": [{"query": args.query, "year": args.year, "source_status": response.source_status, "at": screening.now()}]}
    with transaction(root) as data:
        if args.command in {"ingest", "search"}:
            if args.command == "ingest":
                payload = screening.read_json(args.input)
            result = ingest(data, payload)
            if args.command == "search":
                result["source_status"] = response.source_status
        elif args.command == "review":
            result = screening.review(data, screening.read_json(args.input))
            score(data)
        elif args.command == "set-target":
            before = data["target"]
            data["target"] = count(args.count if args.count is not None else len(derive(data)["verified"]) + count(args.add))
            data.setdefault("target_history", []).append({"from": before, "to": data["target"], "at": screening.now()})
            result = summary(data, derive(data))
        elif args.command == "plan":
            result = plan_batch(data, root, args.limit, args.route, args.institution)
        elif args.command == "close-batch":
            b = batch_by_id(data, args.batch)
            if b["phase"] == "started" and (pid_alive(b.get("runner_pid")) or pid_alive(b.get("child_pid"))):
                raise ValueError("Batch process is still running; do not release its reservation")
            if not args.reason.strip():
                raise ValueError("A concrete inspection/closure reason is required")
            b.update(phase="closed", closure_reason=args.reason, closed_at=screening.now())
            result = summary(data, derive(data))
        elif args.command == "retry":
            screening.find(data, args.id)
            view = derive(data)
            if view["active_batches"] or args.id in view["verified"] or not args.reason.strip():
                raise ValueError("Close active batches first; do not retry a verified paper; provide a reason")
            data["retry_ids"] = list(dict.fromkeys(data["retry_ids"] + [args.id]))
            data["resolved_blockers"] += [b["key"] for b in view["blockers"] if b["id"] == args.id]
            data.setdefault("retry_log", []).append({"id": args.id, "reason": args.reason, "at": screening.now()})
            result = summary(data, derive(data))
        elif args.command == "export":
            result = export(data, root)
        else:
            score(data)
            result = {"summary": summary(data, derive(data)), "papers": data["papers"]}
    return result


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    subs = p.add_subparsers(dest="command", required=True)
    for name in ("init", "search", "ingest", "review", "set-target", "status", "plan", "acquire", "close-batch", "retry", "export"):
        sub = subs.add_parser(name)
        sub.add_argument("--task", type=Path, required=True)
        if name == "init":
            sub.add_argument("--topic", required=True)
            sub.add_argument("--count", type=int, default=100)
            sub.add_argument("--plan", type=Path)
            sub.add_argument("--heat-weight", type=float, default=0.0)
        if name in {"ingest", "review"}:
            sub.add_argument("--input", type=Path, required=True)
        if name == "search":
            sub.add_argument("--query", required=True)
            sub.add_argument("--limit", type=int, default=30, choices=range(1, 301))
            sub.add_argument("--sources", default="semantic_scholar,openalex,crossref")
            sub.add_argument("--year", default=None)
        if name == "set-target":
            group = sub.add_mutually_exclusive_group(required=True)
            group.add_argument("--count", type=int)
            group.add_argument("--add", type=int)
        if name == "plan":
            sub.add_argument("--limit", type=int, default=1)
            sub.add_argument("--route", choices=["auto", "doi", "cnki", "wanfang"], default="auto")
            sub.add_argument("--institution", default="")
        if name in {"acquire", "close-batch"}:
            sub.add_argument("--batch", required=True)
        if name in {"retry", "close-batch"}:
            sub.add_argument("--reason", required=True)
        if name == "retry":
            sub.add_argument("--id", required=True)
    return p


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        print(json.dumps(run(parser().parse_args()), ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, RuntimeError, TypeError, KeyError, StopIteration) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
