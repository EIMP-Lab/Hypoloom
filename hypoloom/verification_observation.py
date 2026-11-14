"""Disposable bounded verifier views from accepted commands and Attempts."""
from __future__ import annotations

import base64
import json
import time
from collections import defaultdict


def _object(value):
    if isinstance(value, dict): return value
    try: return json.loads(value or "{}")
    except (ValueError, TypeError): return {}


def _input(request, suffix):
    for item in request.get("input_artifacts", ()):
        if item.get("ref", "").endswith(suffix):
            try: return _object(base64.b64decode(item["content_base64"], validate=True))
            except (ValueError, KeyError): return {}
    return {}


def verification_view(contract, commands, attempts, *, limit=20, offset=0, expert=None, now=None, delivery_refs=None):
    now = time.time() if now is None else now
    configured = contract.get("methodology", {}).get("controls", {}).get("product", {}).get("experts", {}).get("verifier")
    jobs = []
    for row in commands:
        request = _object(row.get("request_json", row.get("request")))
        if value := request.get("implementation_verification"):
            jobs.append({**value, "command_id": row["command_id"]})
    by_command, reports, delivered, responded = defaultdict(list), {}, set(), set()
    opened, probe_seconds, rejections, repairs = [], 0.0, {}, set()
    probe_views = {}
    if delivery_refs:
        delivered.update(delivery_refs.get("delivered", ()))
        responded.update(delivery_refs.get("responded", ()))
    for row in attempts:
        observation = _object(row.get("observation", row.get("observation_json")))
        request = observation.get("request", {})
        if observation.get("failure_class") == "implementation_not_verified":
            rejections[row.get("experiment_id")] = observation["implementation_verification"]
        if row.get("command_id"): by_command[row["command_id"]].append((row, observation))
        if request.get("command") == "VERIFY_IMPLEMENTATION" and row["state"] == "OPEN": opened.append(row["attempt_id"])
        for report in _input(request, "--implementation-reviews").get("reports", ()):
            delivered.add(report["report_ref"])
            if row.get("terminal_reason") == "SUCCEEDED": responded.add(report["report_ref"])
        if str(row.get("execution_id", "")).endswith("--implementation-probe"):
            probe_views[row.get("experiment_id")] = {"attempt_id": row["attempt_id"], "state": row["state"],
                "terminal_reason": row.get("terminal_reason"), "results": observation.get("check_results"),
                "error": observation.get("check_results_error"), "seconds": _elapsed(row, now)}
            if row.get("ended_at") is not None and row.get("started_at") is not None:
                probe_seconds += max(0, float(row["ended_at"]) - float(row["started_at"]))
    by_expert, deliveries, resolved = {}, {}, set()
    for job in jobs:
        role = job["subject_expert"]
        subject = by_expert.setdefault(role, {"expert": role, "deliveries": 0, "triggers": 0, "reuse": 0,
            "first_pass": {"passed": 0, "completed": 0}, "findings": [], "recent": [],
            "verification_failures": 0, "invocation_failures": 0, "protocol_rejections": 0,
            "stage_seconds": {"inspect": 0.0, "review": 0.0}, "model_runtime": None})
        delivery = deliveries.setdefault((role, job["delivery_id"]), {"delivery_id": job["delivery_id"], "expert": role, "revisions": {}})
        revision = delivery["revisions"].setdefault(job["experiment_id"], {"experiment_id": job["experiment_id"],
            "source_output_digest": job["source_output_digest"], "state": "queued", "reports": [], "stages": [],
            "probe": probe_views.get(job["experiment_id"])})
        if job["phase"] == "inspect":
            subject["reuse" if job.get("reused_from") else "triggers"] += 1
        if job.get("reused_from"):
            revision.update(state="reused", reused_from=job["reused_from"])
        for attempt, observation in by_command[job["command_id"]]:
            diagnostics = observation.get("diagnostics") or {}
            validation = diagnostics.get("structured_output_correction") or {}
            rejected = validation.get("rejected_count", 0)
            subject["protocol_rejections"] += rejected
            subject["stage_seconds"][job["phase"]] += _elapsed(attempt, now)
            if diagnostics.get("model_runtime"): subject["model_runtime"] = diagnostics["model_runtime"]
            failure = observation.get("failure_code") or observation.get("failure_class")
            if attempt["state"] == "CLOSED" and attempt.get("terminal_reason") != "SUCCEEDED":
                subject["invocation_failures"] += 1
                failure = failure or attempt.get("terminal_reason")
            revision["stages"].append({"phase": job["phase"], "attempt_id": attempt["attempt_id"],
                "seconds": _elapsed(attempt, now), "protocol_rejections": rejected, "failure": failure,
                "model_runtime": diagnostics.get("model_runtime")})
            if attempt["state"] == "OPEN": revision.update(state=job["phase"], attempt_id=attempt["attempt_id"])
            if attempt.get("terminal_reason") != "SUCCEEDED" or "response" not in observation: continue
            report = observation["response"]["typed_candidate"]
            ref = attempt["attempt_id"]
            stage = "responded" if ref in responded else "delivered" if ref in delivered else "generated"
            if delivery_refs and delivery_refs.get("partial") and ref not in delivered: stage = "unconfirmed"
            report_view = {"report_ref": ref, "phase": job["phase"], "verdict": report["verdict"], "summary": report["summary"],
                "delivery_state": stage, "ended_at": attempt.get("ended_at"), "findings": [], "coverage": report.get("coverage"),
                "failure_origin": "candidate" if report["verdict"] == "changes_required" else "verification" if report["verdict"] == "inconclusive" else None}
            for finding in report["findings"]:
                item = {**finding, "finding_ref": ref + "#" + finding["id"], "report_ref": ref,
                        "experiment_id": job["experiment_id"], "delivery_state": stage}
                report_view["findings"].append(item)
                subject["findings"].append(item)
            if report["verdict"] == "passed": resolved.update(report["resolved_refs"])
            revision["reports"].append(report_view)
            revision["state"] = "awaiting_probe" if report["verdict"] == "probe_required" else report["verdict"]
            reports[ref] = report_view
    for delivery in deliveries.values():
        subject = by_expert[delivery["expert"]]
        subject["deliveries"] += 1
        revisions = list(delivery["revisions"].values())
        first = revisions[0]
        for revision in revisions:
            rejected = rejections.get(revision["experiment_id"])
            if rejected and revision["state"] in {"queued", "inspect", "review", "awaiting_probe"}:
                revision["state"] = rejected["report"]["verdict"]
        if first["state"] in {"passed", "changes_required"}:
            subject["first_pass"]["completed"] += 1
            subject["first_pass"]["passed"] += first["state"] == "passed"
        subject["recent"].append({**delivery, "revisions": revisions, "state": revisions[-1]["state"]})
        if revisions[-1]["state"] == "inconclusive": subject["verification_failures"] += 1
        if len(revisions) > 1:
            repairs.update(r["experiment_id"] for r in revisions[:-1])
    pending = any(d["state"] in {"queued", "inspect", "review", "awaiting_probe"}
        for s in by_expert.values() for d in s["recent"])
    for subject in by_expert.values():
        for finding in subject["findings"]:
            if finding["experiment_id"] in repairs: finding["delivery_state"] = "repair_submitted"
            finding["state"] = "verified_resolved" if finding["finding_ref"] in resolved else "open"
        subject["blockers"] = sum(f["severity"] == "blocker" and f["state"] == "open" for f in subject["findings"])
        subject["advisories"] = sum(f["severity"] == "advisory" and f["state"] == "open" for f in subject["findings"])
        subject["finding_count"] = len(subject["findings"])
        subject["findings"] = subject["findings"][-limit:]
        subject["recent"] = list(reversed(subject["recent"]))[offset:offset + limit]
    subjects = [v for k, v in by_expert.items() if expert is None or k == expert]
    probes = [r for r in attempts if str(r.get("execution_id", "")).endswith("--implementation-probe") and r["state"] == "OPEN"]
    return {"supported": bool(configured), "model": (configured or {}).get("model"), "effort": (configured or {}).get("effort"),
        "state": "unsupported" if not configured else "active" if opened else "probing" if probes else "queued" if pending else "idle",
        "attempt_ids": opened, "probe_attempt_ids": [r["attempt_id"] for r in probes], "probe_seconds": probe_seconds,
        "experts": subjects, "delivery_count": len(deliveries), "offset": offset, "limit": limit,
        "attribution": "Subject breakdown only; verifier spend is billed once in the global verifier group."}


def _elapsed(row, now):
    start = row.get("started_at")
    end = row.get("ended_at") if row.get("ended_at") is not None else now
    return max(0, end - start) if type(start) in {int, float} and type(end) in {int, float} else 0.0


def read_verification_view(db, run_id, contract, *, limit=20, offset=0, expert=None, now=None):
    """Select lightweight counting fields before decoding bounded report details.

    This is a disposable projection of the same command and Attempt rows. Large
    source, probe and request bodies never enter historical counting records.
    """
    commands = [{"command_id": row["command_id"], "request": {"implementation_verification": _object(row["commission"])}}
        for row in db.execute("SELECT command_id,json_extract(request_json,'$.implementation_verification') AS commission "
            "FROM workflow_commands WHERE run_id=? AND command_type='REQUEST_AUXILIARY' "
            "AND json_type(request_json,'$.implementation_verification')='object' ORDER BY rowid", (run_id,))]
    if not commands: return verification_view(contract, [], [], limit=limit, offset=offset, expert=expert, now=now)
    deliveries, recent = defaultdict(dict), defaultdict(list)
    for command in commands:
        job = command["request"]["implementation_verification"]
        deliveries[job["subject_expert"]][job["delivery_id"]] = True
        recent[job["subject_expert"]].append(command["command_id"])
    page = {role: set(list(values)[::-1][offset:offset + limit]) for role, values in deliveries.items()
            if expert is None or role == expert}
    detail_ids, experiments = set(), set()
    for command in commands:
        job = command["request"]["implementation_verification"]
        if job["subject_expert"] in page and (job["delivery_id"] in page[job["subject_expert"]]
                or command["command_id"] in recent[job["subject_expert"]][-limit:]):
            detail_ids.add(command["command_id"])
            experiments.add(job["experiment_id"])
    # SQLite reads only countable fields; Python never decodes old source/probe
    # files just to count findings. json_each is bounded by the report contract.
    sql = """SELECT a.attempt_id,a.command_id,a.experiment_id,a.execution_id,a.kind,a.state,
        a.started_at,a.ended_at,a.terminal_reason,
        json_object('request',json_object('command','VERIFY_IMPLEMENTATION'),
          'response',json_object('typed_candidate',json_object(
            'verdict',json_extract(a.observation_json,'$.response.typed_candidate.verdict'),
            'summary','', 'resolved_refs',json(COALESCE(json_extract(a.observation_json,'$.response.typed_candidate.resolved_refs'),'[]')),
            'findings',json((SELECT json_group_array(json_object('id',json_extract(f.value,'$.id'),'severity',json_extract(f.value,'$.severity')))
                FROM json_each(a.observation_json,'$.response.typed_candidate.findings') f)))),
          'failure_code',json_extract(a.observation_json,'$.failure_code'),
          'diagnostics',json_object('structured_output_correction',json_extract(a.observation_json,'$.diagnostics.structured_output_correction')))
          AS observation_json
        FROM execution_attempts a JOIN workflow_commands c ON c.command_id=a.command_id
        WHERE a.run_id=? AND c.run_id=? AND c.command_type='REQUEST_AUXILIARY'
        AND json_type(c.request_json,'$.implementation_verification')='object'
        ORDER BY a.started_at,a.attempt_id"""
    attempts = [dict(row) for row in db.execute(sql, (run_id, run_id))]
    by_id = {a["attempt_id"]: a for a in attempts}
    owners = {c["command_id"]: c["request"]["implementation_verification"]["subject_expert"] for c in commands}
    finding_counts = defaultdict(int)
    for attempt in reversed(attempts):
        role = owners[attempt["command_id"]]
        if role not in page or finding_counts[role] >= limit: continue
        count = len(_object(attempt["observation_json"])["response"]["typed_candidate"]["findings"])
        if count:
            detail_ids.add(attempt["command_id"])
            finding_counts[role] += count
    detail_attempts = [a["attempt_id"] for a in attempts if a["command_id"] in detail_ids or a["state"] == "OPEN"]
    for start in range(0, len(detail_attempts), 200):
        ids = detail_attempts[start:start + 200]
        for row in db.execute("SELECT attempt_id,json_object('request',json_object('command','VERIFY_IMPLEMENTATION'),"
            "'response',json_object('typed_candidate',json_remove(json_extract(observation_json,'$.response.typed_candidate'),'$.probe')),"
            "'diagnostics',json_object('structured_output_correction',json_extract(observation_json,'$.diagnostics.structured_output_correction'),"
            "'model_runtime',json_extract(observation_json,'$.diagnostics.model_runtime')),"
            "'failure_code',json_extract(observation_json,'$.failure_code')) AS observation_json "
            "FROM execution_attempts WHERE run_id=? AND attempt_id IN (" + ",".join("?" for _ in ids) + ")", (run_id, *ids)):
            by_id[row["attempt_id"]]["observation_json"] = row["observation_json"]
    # Probe timings and synthetic terminal reasons are small. Check details are
    # fetched only for the selected experiments, not all historical Processes.
    for row in db.execute("SELECT attempt_id,command_id,experiment_id,execution_id,kind,state,started_at,ended_at,terminal_reason,"
        "json_object('failure_class',json_extract(observation_json,'$.failure_class'),"
        "'implementation_verification',json_extract(observation_json,'$.implementation_verification')) AS observation_json "
        "FROM execution_attempts WHERE run_id=? AND kind='PROCESS' AND "
        "(execution_id LIKE '%--implementation-probe' OR json_extract(observation_json,'$.failure_class')='implementation_not_verified') "
        "ORDER BY started_at,attempt_id", (run_id,)):
        value = dict(row)
        if value["experiment_id"] in experiments and value["execution_id"].endswith("--implementation-probe"):
            detail = db.execute("SELECT json_object('check_results',json_extract(observation_json,'$.check_results'),"
                "'check_results_error',json_extract(observation_json,'$.check_results_error')) FROM execution_attempts WHERE run_id=? AND attempt_id=?",
                (run_id, value["attempt_id"])).fetchone()
            value["observation_json"] = detail[0]
        attempts.append(value)
    # Only selected reports need delivery evidence. Bound author input reads;
    # missing evidence beyond this window is explicitly unconfirmed, never a
    # claim that a report was delivered or a repair was accepted.
    visible = [a for a in by_id.values() if a["command_id"] in detail_ids and a["ended_at"] is not None]
    refs = {"delivered": set(), "responded": set(), "partial": False}
    if visible:
        earliest = min(a["ended_at"] for a in visible)
        role_sql = ",".join("?" for _ in page)
        rows = db.execute("SELECT a.terminal_reason,i.value FROM execution_attempts a, "
            "json_each(a.observation_json,'$.request.input_artifacts') i WHERE a.run_id=? AND a.kind='AGENT' "
            "AND a.started_at>=? AND json_extract(a.observation_json,'$.request.role') IN (" + role_sql + ") "
            "AND json_extract(i.value,'$.ref') LIKE '%--implementation-reviews' ORDER BY a.started_at DESC LIMIT 201",
            (run_id, earliest, *page)).fetchall()
        refs["partial"] = len(rows) > 200
        for row in rows[:200]:
            supplied = _input({"input_artifacts": [_object(row["value"])]}, "--implementation-reviews")
            selected = {r["report_ref"] for r in supplied.get("reports", ())}
            refs["delivered"].update(selected)
            if row["terminal_reason"] == "SUCCEEDED": refs["responded"].update(selected)
    result = verification_view(contract, commands, attempts, limit=limit, offset=offset, expert=expert, now=now, delivery_refs=refs)
    result["delivery_evidence"] = "partial" if refs["partial"] else "complete"
    result["detail_attempt_count"] = len(detail_attempts)
    return result
