"""Bounded verifier reading views over accepted, immutable reports."""
from __future__ import annotations

import json


def history_view(store, run_id, commission, command_id):
    # Old blockers belong to a particular delivery. Include that entire chain,
    # plus recent reports the verifier could already consult for this component.
    # Extract only the fields needed for the index, never old probe programs.
    rows = store.inspect("""
        WITH scoped AS (
            SELECT rowid AS n, command_id, json_extract(request_json,
                   '$.implementation_verification') AS v
            FROM workflow_commands WHERE run_id=? AND command_type='REQUEST_AUXILIARY'
              AND command_id<>? AND json_extract(request_json,'$.implementation_verification.subject_expert')=?
              AND json_extract(request_json,'$.implementation_verification.component_id')=?
        ), recent AS (SELECT n FROM scoped ORDER BY n DESC LIMIT 16)
        SELECT s.v, s.n IN recent AS recent, a.attempt_id AS ref, json_extract(a.observation_json,
            '$.response.typed_candidate.verdict','$.response.typed_candidate.findings',
            '$.response.typed_candidate.resolved_refs') AS fields
        FROM scoped s JOIN execution_attempts a ON a.attempt_id=(
            SELECT attempt_id FROM execution_attempts
            WHERE command_id=s.command_id AND kind='AGENT' AND state='CLOSED' AND terminal_reason='SUCCEEDED'
            ORDER BY attempt_ordinal DESC LIMIT 1)
        WHERE s.n IN recent OR json_extract(s.v,'$.delivery_id')=? ORDER BY s.n
    """, (run_id, command_id, commission["subject_expert"], commission["component_id"], commission["delivery_id"]))
    reports, findings, blockers, resolved = [], [], {}, set()
    for row in rows:
        value = json.loads(row["v"])
        verdict, items, resolutions = json.loads(row["fields"])
        scoped = value["delivery_id"] == commission["delivery_id"]
        if scoped and verdict == "passed":
            resolved.update(resolutions)
        reports.append((bool(row["recent"]), {"report_ref": row["ref"], "experiment_id": value["experiment_id"],
                        "phase": value["phase"], "verdict": verdict}))
        for item in items:
            ref = row["ref"] + "#" + item["id"]
            if row["recent"]:
                findings.append(ref)
            if scoped and item["severity"] == "blocker":
                blockers[ref] = (row["ref"], item)
    opened = {ref: pair for ref, pair in blockers.items() if ref not in resolved}
    older = {source for source, _ in opened.values()}
    return {"prior_reports": [report for recent, report in reports if recent or report["report_ref"] in older],
            "finding_refs": list(dict.fromkeys([*findings, *opened])),
            "open_blocker_refs": list(opened),
            "open_blockers": [{"ref": ref, "finding": item} for ref, (_, item) in opened.items()]}


def report_page(store, request, arguments):
    from .agent_aci import AgentACIError
    from .verification_contract import context
    ref = arguments.get("report_ref")
    offset = arguments.get("offset", 0)
    if (set(arguments) - {"report_ref", "offset"} or not isinstance(ref, str)
            or type(offset) is not int or not 0 <= offset <= 2**31 - 1):
        raise AgentACIError("tool_arguments", "Use report_ref from prior_reports and an optional character offset >= 0.")
    if ref not in {row["report_ref"] for row in context(request)["prior_reports"]}:
        raise AgentACIError("tool_caller_scope", "Use a report_ref listed in this invocation's prior_reports.")
    rows = store.inspect("""WITH accepted(report) AS (
        SELECT json_extract(observation_json,'$.response.typed_candidate') FROM execution_attempts
        WHERE run_id=? AND attempt_id=? AND kind='AGENT' AND state='CLOSED' AND terminal_reason='SUCCEEDED')
        SELECT length(report) AS size, substr(report, ?, 8192) AS page FROM accepted""", (request.run_id, ref, offset + 1))
    if not rows or not rows[0]["size"]:
        raise AgentACIError("report_unavailable", "The indexed accepted report is unavailable.")
    end = offset + 8192
    return {"report_ref": ref, "offset": offset, "text": rows[0]["page"],
            "next_offset": end if end < rows[0]["size"] else None}
