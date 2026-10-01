"""Bounded narrative output derived from the existing accepted science rows."""
from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path


_COMPARABLE_OUTCOMES = frozenset({"SUCCESS", "CLEAN_REFUTATION"})


def declared_measurement_inputs(facts, resolve_raw, *, max_bytes):
    """Deliver bounded published text, without interpreting task-specific metrics."""
    from .agent_aci import EmbeddedInput

    inputs, included, omitted = [], [], []
    remaining = max_bytes
    for row in facts:
        if row.get("fact_type") != "CanonicalOutcomeAccepted":
            continue
        payload = row.get("payload") or json.loads(row["payload_json"])
        outcome_id, artifact_id = payload["outcome_id"], payload["artifact_id"]
        for ordinal, (relative, path) in enumerate(resolve_raw(outcome_id, artifact_id), 1):
            ref = f"{outcome_id}--declared-raw-measurement-{ordinal}"
            entry = {"outcome_id": outcome_id, "artifact_id": artifact_id,
                "canonical_outcome": payload["outcome"], "path": relative, "ref": ref}
            if Path(relative).suffix.lower() not in {".json", ".jsonl", ".csv", ".tsv", ".txt"}:
                omitted.append({**entry, "reason": "non_text_artifact"})
                continue
            try:
                with Path(path).open("rb") as stream:
                    content = stream.read(remaining + 1)
                if len(content) > remaining:
                    omitted.append({**entry, "reason": "raw_byte_budget_exceeded"})
                    continue
                content.decode("utf-8")
            except (OSError, UnicodeError):
                omitted.append({**entry, "reason": "source_unavailable_or_not_utf8"})
                continue
            inputs.append(EmbeddedInput.from_bytes(ref, "text/plain", content))
            included.append(entry)
            remaining -= len(content)
    return tuple(inputs), {"evidence_scope": "declared_raw_observations_not_new_verdicts",
        "byte_limit": max_bytes, "bytes_included": max_bytes - remaining,
        "included": included, "omitted": omitted}


def accepted_evaluation_summary_inputs(facts, resolve_logical, *, max_bytes):
    """Materialize bounded accepted summaries through the Host's artifact resolver."""
    from .agent_aci import EmbeddedInput

    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("evaluation summary byte limit must be a positive integer")
    inputs = []
    included = []
    omitted = []
    remaining = max_bytes
    for row in facts:
        if row.get("fact_type") != "CanonicalOutcomeAccepted":
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            try:
                payload = json.loads(row["payload_json"])
            except (KeyError, TypeError, json.JSONDecodeError):
                continue
        if payload.get("outcome") not in _COMPARABLE_OUTCOMES:
            continue
        logical = payload.get("logical_artifacts")
        if not isinstance(logical, (list, tuple)) or not any(
            isinstance(item, dict) and item.get("logical_artifact") == "evaluation_summary"
            for item in logical
        ):
            continue
        outcome_id = payload.get("outcome_id")
        artifact_id = payload.get("artifact_id")
        if not isinstance(outcome_id, str) or not isinstance(artifact_id, str):
            continue
        paths = tuple(resolve_logical(outcome_id, artifact_id, "evaluation_summary"))
        refs = []
        for index, raw_path in enumerate(paths, 1):
            path = Path(raw_path)
            ref = f"{outcome_id}--accepted-evaluation-summary"
            if len(paths) > 1:
                ref += f"-{index}"
            try:
                with path.open("rb") as stream:
                    content = stream.read(remaining + 1)
            except OSError:
                omitted.append({"outcome_id": outcome_id, "artifact_id": artifact_id,
                    "ref": ref, "reason": "source_unavailable"})
                continue
            if len(content) > remaining:
                omitted.append({"outcome_id": outcome_id, "artifact_id": artifact_id,
                    "ref": ref, "reason": "summary_byte_budget_exceeded"})
                continue
            inputs.append(EmbeddedInput.from_bytes(ref, "application/json", content))
            refs.append(ref)
            remaining -= len(content)
        if refs:
            included.append({"outcome_id": outcome_id, "artifact_id": artifact_id,
                "refs": refs})
    return tuple(inputs), {"byte_limit": max_bytes, "bytes_included": max_bytes - remaining,
        "included": included, "omitted": omitted}


def run_outcome_context(facts, attempts, final_failures, *, artifacts=()):
    """Summarize existing rows for the report; this accepts no new outcome."""
    from .agent_budget import fold_agent_attempts
    from .domain import ResearchFactType

    counts = Counter(row["fact_type"] for row in facts)
    by_id = {row["attempt_id"]: row for row in attempts}
    artifact_owners = {(row["artifact_id"], row["execution_id"]): row["attempt_id"] for row in artifacts}
    outcomes_by_attempt = {}
    for row in facts:
        if row.get("fact_type") != ResearchFactType.CANONICAL_OUTCOME_ACCEPTED.value:
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            try:
                payload = json.loads(row["payload_json"])
            except (KeyError, TypeError, json.JSONDecodeError):
                continue
        owner = artifact_owners.get((payload.get("artifact_id"), payload.get("execution_id")))
        if owner is not None:
            outcomes_by_attempt[owner] = payload
    nodes = set()
    agent_states = Counter()
    unsuccessful = []
    successful_commands = {row.get("command_id") for row in attempts
        if row.get("kind") == "AGENT" and row.get("terminal_reason") == "SUCCEEDED"}
    for row in attempts:
        if row["kind"] != "AGENT":
            continue
        observation = row["observation"] if "observation" in row else json.loads(row["observation_json"])
        request = observation.get("request", {})
        agent_states[row.get("terminal_reason") or row.get("state") or "UNKNOWN"] += 1
        if row.get("state") == "CLOSED" and row.get("terminal_reason") != "SUCCEEDED":
            unsuccessful.append({"attempt_id": row["attempt_id"], "command": request.get("command"),
                "terminal_reason": row.get("terminal_reason"), "failure_code": observation.get("failure_code"),
                "failure_stage": observation.get("failure_stage"),
                "command_has_successful_attempt": row.get("command_id") in successful_commands})
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
        "reported_tokens": sum(item.tokens for item in charges if item.source != "declared" and item.tokens is not None),
        "reserved_tokens": sum(item.tokens for item in reserved if item.tokens is not None),
        "unavailable_token_attempts": sum(item.tokens is None for item in charges),
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
        outcome = outcomes_by_attempt.get(row["attempt_id"])
        raw_resource = observation.get("resource_usage")
        resources = ([raw_resource] if isinstance(raw_resource, dict)
            else raw_resource if isinstance(raw_resource, list)
            and all(isinstance(item, dict) for item in raw_resource) else [])
        gpu_device_monitored = any(isinstance(item.get("gpu_uuid"), str) and bool(item["gpu_uuid"])
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
            "gpu_device_monitored": gpu_device_monitored,
            "evidence_scope": ("accepted_scientific_evidence" if scientific
                else "raw_diagnostic_only" if completed else "no_completed_process"),
        }
        if resources:
            bounded_resources = [{name: item.get(name) for name in (
                "gpu_uuid", "sample_count", "gpu_minimum_free_memory_mib", "pressure_exceeded", "owned_memory")}
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
        "agent_attempts_by_status": dict(agent_states),
        "unsuccessful_agent_attempts": unsuccessful,
        "final_failures_scope": "nodes_with_exhausted_or_nonretryable_failure",
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
            outcome = json.loads(item.content_bytes()).get("run_outcome", {})
            for failure in (*outcome.get("final_failures", []), *outcome.get("unsuccessful_agent_attempts", [])):
                references = [failure[key] for key in ("attempt_id", "failure_code") if failure.get(key)]
                if any(ref not in report for ref in references):
                    missing = [ref for ref in references if ref not in report]
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
