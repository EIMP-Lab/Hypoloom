"""Disposable bounded verifier views from accepted commands and Attempts."""
from __future__ import annotations

import base64
import json
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


def verification_view(contract, commands, attempts, *, limit=20, offset=0, expert=None):
    configured = contract.get("methodology", {}).get("controls", {}).get("product", {}).get("experts", {}).get("verifier")
    jobs = []
    for row in commands:
        request = _object(row.get("request_json", row.get("request")))
        if value := request.get("implementation_verification"):
            jobs.append({**value, "command_id": row["command_id"]})
    by_command, reports, delivered, responded = defaultdict(list), {}, set(), set()
    opened, probe_seconds, rejections, repairs = [], 0.0, {}, set()
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
            if row.get("ended_at") is not None and row.get("started_at") is not None:
                probe_seconds += max(0, float(row["ended_at"]) - float(row["started_at"]))
    by_expert, deliveries, resolved = {}, {}, set()
    for job in jobs:
        role = job["subject_expert"]
        subject = by_expert.setdefault(role, {"expert": role, "deliveries": 0, "triggers": 0, "reuse": 0,
            "first_pass": {"passed": 0, "completed": 0}, "findings": [], "recent": []})
        delivery = deliveries.setdefault((role, job["delivery_id"]), {"delivery_id": job["delivery_id"], "expert": role, "revisions": {}})
        revision = delivery["revisions"].setdefault(job["experiment_id"], {"experiment_id": job["experiment_id"],
            "source_output_digest": job["source_output_digest"], "state": "queued", "reports": []})
        if job["phase"] == "inspect":
            subject["reuse" if job.get("reused_from") else "triggers"] += 1
        if job.get("reused_from"):
            revision.update(state="reused", reused_from=job["reused_from"])
        for attempt, observation in by_command[job["command_id"]]:
            if attempt["state"] == "OPEN": revision.update(state=job["phase"], attempt_id=attempt["attempt_id"])
            if attempt.get("terminal_reason") != "SUCCEEDED" or "response" not in observation: continue
            report = observation["response"]["typed_candidate"]
            ref = attempt["attempt_id"]
            stage = "responded" if ref in responded else "delivered" if ref in delivered else "generated"
            report_view = {"report_ref": ref, "phase": job["phase"], "verdict": report["verdict"], "summary": report["summary"],
                "delivery_state": stage, "ended_at": attempt.get("ended_at"), "findings": []}
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
        if len(revisions) > 1:
            repairs.update(r["experiment_id"] for r in revisions[:-1])
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
    pending = any(d["state"] in {"queued", "inspect", "review", "awaiting_probe"} for s in subjects for d in s["recent"])
    return {"supported": bool(configured), "model": (configured or {}).get("model"), "effort": (configured or {}).get("effort"),
        "state": "unsupported" if not configured else "active" if opened else "probing" if probes else "queued" if pending else "idle",
        "attempt_ids": opened, "probe_attempt_ids": [r["attempt_id"] for r in probes], "probe_seconds": probe_seconds,
        "experts": subjects, "delivery_count": len(deliveries), "offset": offset, "limit": limit,
        "attribution": "Subject breakdown only; verifier spend is billed once in the global verifier group."}
