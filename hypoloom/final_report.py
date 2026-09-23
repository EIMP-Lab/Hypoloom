"""Bounded narrative output derived from the existing accepted science rows."""
from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal


def run_outcome_context(facts, attempts, final_failures):
    """Summarize existing rows for the report; this accepts no new outcome."""
    from .agent_budget import fold_agent_attempts
    from .domain import ResearchFactType

    counts = Counter(row["fact_type"] for row in facts)
    by_id = {row["attempt_id"]: row for row in attempts}
    outcomes_by_execution = {}
    for row in facts:
        if row.get("fact_type") != ResearchFactType.CANONICAL_OUTCOME_ACCEPTED.value:
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            try:
                payload = json.loads(row["payload_json"])
            except (KeyError, TypeError, json.JSONDecodeError):
                continue
        execution_id = payload.get("execution_id")
        if isinstance(execution_id, str) and execution_id:
            outcomes_by_execution[execution_id] = payload
    nodes = set()
    for row in attempts:
        if row["kind"] != "AGENT":
            continue
        request = (row["observation"] if "observation" in row else json.loads(row["observation_json"])).get("request", {})
        context = request.get("task_context")
        if isinstance(context, dict) and "key" in context:
            nodes.add(request.get("command_id", row["command_id"]))
    failures = []
    for failure in final_failures:
        row = by_id[failure["attempt_id"]]
        observation = (row["observation"] if "observation" in row else json.loads(row["observation_json"]))
        request = observation["request"]
        failures.append({"attempt_id": row["attempt_id"], "command": request["command"],
            "failure_code": failure["failure_code"], "failure_stage": observation.get("failure_stage"),
            "provider_subtype": observation.get("diagnostics", {}).get("provider_subtype")})
    charges = fold_agent_attempts({**row,
        "observation_json": row.get("observation_json") or json.dumps(row.get("observation", {}))}
        for row in attempts)
    reported = [item for item in charges if item.source != "declared" and item.cost is not None]
    reserved = [item for item in charges if item.source == "declared"]
    agent_accounting = {
        "scope": "durable_attempts_before_final_report",
        "reported_cost_usd": str(sum((item.cost for item in reported), Decimal("0"))),
        "reserved_cost_usd": str(sum(
            (item.cost for item in reserved if item.cost is not None), Decimal("0"))),
        "unknown_closed_attempts": sum(item.state == "CLOSED" for item in reserved),
        "open_attempts": sum(item.state == "OPEN" for item in charges),
    }
    process_details = []
    for row in attempts:
        if row.get("kind") != "PROCESS":
            continue
        try:
            observation = json.loads(row.get("observation_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            observation = {}
        if not isinstance(observation, dict):
            observation = {}
        execution_id = row.get("execution_id") or observation.get("execution_id")
        outcome = outcomes_by_execution.get(execution_id)
        raw_resource = observation.get("resource_usage")
        resources = ([raw_resource] if isinstance(raw_resource, dict)
            else raw_resource if isinstance(raw_resource, list)
            and all(isinstance(item, dict) for item in raw_resource) else [])
        gpu_observed = any(isinstance(item.get("gpu_uuid"), str) and bool(item["gpu_uuid"])
            and type(item.get("sample_count")) is int and item["sample_count"] > 0
            for item in resources)
        completed = (row.get("state") == "CLOSED"
            and row.get("terminal_reason") == "SUCCEEDED"
            and observation.get("returncode") == 0)
        scientific = (isinstance(outcome, dict)
            and outcome.get("outcome") in {"SUCCESS", "CLEAN_REFUTATION"})
        detail = {
            "attempt_id": row.get("attempt_id"),
            "execution_id": execution_id,
            "terminal_reason": row.get("terminal_reason"),
            "returncode": observation.get("returncode"),
            "completed_process": completed,
            "gpu_observed": gpu_observed,
            "evidence_scope": ("accepted_scientific_evidence" if scientific
                else "raw_diagnostic_only" if completed else "no_completed_process"),
        }
        if resources:
            bounded_resources = [{name: item.get(name) for name in (
                "gpu_uuid", "sample_count", "minimum_free_memory_mib", "pressure_exceeded")}
                for item in resources]
            detail["resource_usage"] = (bounded_resources[0]
                if isinstance(raw_resource, dict) else bounded_resources)
        if isinstance(outcome, dict):
            detail["canonical_outcome"] = {name: outcome.get(name) for name in (
                "outcome_id", "outcome", "validation_code", "metric_value")}
        process_details.append(detail)
    return {"fact_counts": {kind.value: counts[kind.value] for kind in ResearchFactType},
        "expert_nodes_started": len(nodes),
        "process_attempts": sum(row["kind"] == "PROCESS" for row in attempts),
        "process_attempt_details": process_details,
        "agent_accounting": agent_accounting,
        "final_failures": failures}


def report_sources(request):
    inputs = [item for item in request.input_artifacts if item.ref.endswith("--final-report-context")]
    if len(inputs) != 1:
        raise ValueError("final report requires its exact accepted source context")
    context = json.loads(inputs[0].content_bytes())
    if (not isinstance(context, dict) or not isinstance(context.get("facts"), list)
            or not isinstance(context.get("stop_reason"), str) or not context["stop_reason"].strip()):
        raise ValueError("final report requires actual facts and an explicit stop reason")
    facts = []
    for row in context["facts"]:
        payload = json.loads(row["payload_json"]) if "payload_json" in row else row.get("payload", row)
        if any(row.get(key) != payload.get(key) for key in ("fact_id", "fact_type", "run_id")) or row.get("run_id") != request.run_id:
            raise ValueError("final report source belongs to another run or fact")
        facts.append(payload)
    verdicts = [row for row in facts if row["fact_type"] == "GoalVerdictRecorded"]
    if len(verdicts) > 1: raise ValueError("final report cannot select among contradictory verdicts")
    citations = [(row["outcome_id"], row["outcome"]) for row in facts if row["fact_type"] == "CanonicalOutcomeAccepted"]
    verdict = (verdicts[0]["fact_id"], verdicts[0]["verdict"]) if verdicts else ("NO_ACCEPTED_VERDICT", None)
    return verdict, citations, context["stop_reason"]


def validate_final_report(files, request):
    if set(files) != {"FINAL_REPORT.md"}:
        raise ValueError("final report requires exactly FINAL_REPORT.md")
    contents = files["FINAL_REPORT.md"]
    if not contents or len(contents) > request.budget.max_output_bytes:
        raise ValueError("final report is empty or exceeds its output budget")
    try: report = contents.decode("utf-8")
    except UnicodeError as exc: raise ValueError("final report must be UTF-8 text") from exc
    verdict, outcomes, _ = report_sources(request)
    missing = [identity for identity in (verdict[0], *(item[0] for item in outcomes)) if identity not in report]
    if missing:
        raise ValueError("final report omits an accepted verdict/outcome reference; "
                         f"missing_reference_ids={json.dumps(missing)}")
    if verdict[1] is not None and verdict[1] not in report:
        raise ValueError("final report omits the actual accepted verdict")
    for item in request.input_artifacts:
        if item.ref.endswith("--final-report-context"):
            for failure in json.loads(item.content_bytes()).get("run_outcome", {}).get("final_failures", []):
                if any(failure[key] not in report for key in ("attempt_id", "failure_code")):
                    missing = [failure[key] for key in ("attempt_id", "failure_code") if failure[key] not in report]
                    raise ValueError("final report omits its actual terminal Agent failure; "
                                     f"missing_reference_ids={json.dumps(missing)}")
        if item.ref.endswith("--final-failure"):
            source = json.loads(item.content_bytes())["attempt"]
            if (json.loads(source["observation_json"])["request"]["command"] == "CYCLE_REPORT"
                    and source["attempt_id"] not in report):
                raise ValueError("final report omits its actual failed cycle-report reference")


def accepted_report_view(run_id, attempts):
    """One bound CLOSED report for human export/API; sources remain as-of input."""
    from .agent_aci import AgentRequest, validate_postflight
    result = None
    for row in attempts:
        if (row["run_id"] != run_id or row["kind"] != "AGENT" or row["state"] != "CLOSED"
                or row["terminal_reason"] != "SUCCEEDED"): continue
        observation = (row["observation"] if "observation" in row else json.loads(row["observation_json"]))
        if observation.get("request", {}).get("command") != "FINAL_REPORT": continue
        request = AgentRequest.from_dict(observation["request"])
        if (request.run_id, request.attempt_id, request.owner_workflow_id, request.command_id, request.attempt_ordinal) != (
                run_id, row["attempt_id"], row["workflow_id"], row["command_id"], row["attempt_ordinal"]):
            raise ValueError("final report does not match its stored Attempt")
        if result is not None: raise ValueError("multiple accepted final reports")
        report = validate_postflight(request, observation["response"]).workspace_files["FINAL_REPORT.md"]
        verdict, outcomes, stop = report_sources(request)
        result = {"attempt_id": request.attempt_id, "markdown": report.decode("utf-8"),
            "verdict_ref": verdict[0], "verdict": verdict[1], "outcome_refs": [item[0] for item in outcomes], "stop_reason": stop}
    return result
