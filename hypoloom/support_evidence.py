"""Bounded selections of existing diagnostic records; no new run authority."""
from __future__ import annotations

import json
from collections.abc import Mapping

from .debug_mode import attempt_time_key


def _objects(value):
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _objects(child)
        if (value.get("media_type") == "application/json"
                and isinstance(value.get("ref"), str) and value["ref"].endswith("--shared-acceptance")):
            from .runtime_diagnostics import decode_embedded_content
            decoded = decode_embedded_content(value)
            if decoded is not None:
                yield from _objects(decoded)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _objects(child)
    elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            yield from _objects(json.loads(value))
        except ValueError:
            pass


_TERMINAL_FACTS = {"GoalDefined", "CanonicalOutcomeAccepted", "GoalVerdictRecorded"}


def _final_report(item):
    return item.get("module") in {"FINAL_REPORT", "auxiliary/final_report", "agent/final_report"}


def run_evidence_summary(projection):
    """Small recorded identities/statuses; no research prose or inferred verdict."""
    records = projection.get("related_records", {})
    facts = []
    for row in records.get("facts", []):
        if row.get("fact_type") not in _TERMINAL_FACTS:
            continue
        item = {key: row.get(key) for key in
            ("fact_id", "fact_type", "execution_id", "experiment_id", "artifact_id")}
        payload = row.get("payload", {})
        for key in ("verdict", "outcome"):
            if key in payload:
                item[key] = payload[key]
        facts.append(item)
    return {"scope": "run", "facts": facts,
        "final_report_attempt_ids": [a["attempt_id"] for a in projection["attempts"]
            if _final_report(a)],
        "artifacts": [{key: row.get(key) for key in
            ("artifact_id", "attempt_id", "experiment_id", "execution_id", "artifact_kind")}
            for row in records.get("artifacts", [])]}


def _retry_closure(attempts, seeds):
    neighbors = {item["attempt_id"]: set() for item in attempts}
    for item in attempts:
        previous = item.get("retry_of_attempt_id")
        if previous in neighbors:
            neighbors[item["attempt_id"]].add(previous)
            neighbors[previous].add(item["attempt_id"])
    found, pending = set(seeds), list(seeds)
    while pending:
        for identity in neighbors[pending.pop()] - found:
            found.add(identity)
            pending.append(identity)
    return found


def select_evidence(projection, attempt_id, recent_failures):
    """Select details plus one hop of their recorded sources, not a history graph."""
    attempts = sorted(projection["attempts"], key=attempt_time_key)
    by_id = {item["attempt_id"]: item for item in attempts}
    if attempt_id is not None:
        if attempt_id not in by_id:
            raise ValueError("attempt not found in run")
        selected = {attempt_id}
    elif recent_failures:
        failed = [item for item in attempts if
            item["terminal_reason"] not in {None, "SUCCEEDED", "CANCELLED"}
            or (item.get("structured_output_correction") or {}).get("rejected_count", 0)]
        commands = set(list(dict.fromkeys(item["command_id"] for item in reversed(failed)))[:3])
        selected = {item["attempt_id"] for item in attempts if item["command_id"] in commands}
        if not selected:
            selected = {item["attempt_id"] for item in attempts[-3:]}
        selected = _retry_closure(attempts, selected)
    else:
        selected = set(by_id)
    supporting = set()
    if recent_failures and attempt_id is None:
        first = projection["summary"].get("first_unrecovered_failure_attempt_id")
        if first in by_id:
            supporting.update(_retry_closure(attempts, {first}))
        supporting.update(item["attempt_id"] for item in attempts if _final_report(item))
    owners = [by_id[key] for key in selected | supporting]
    executions = {item.get("execution_id") for item in owners} - {None}
    command_ids = {item["command_id"] for item in owners}
    records = projection.get("related_records", {})
    owner_ids = selected | supporting
    for fact in records.get("facts", []):
        contract = fact.get("payload", {}).get("execution_contract", {})
        if contract.get("source_agent_attempt_id") in owner_ids and fact.get("execution_id"):
            executions.add(fact["execution_id"])
    supporting.update(item["attempt_id"] for item in attempts if item.get("execution_id") in executions)
    facts = [item for item in records.get("facts", []) if
        item.get("execution_id") in executions or
        (attempt_id is None and recent_failures and item.get("fact_type") in _TERMINAL_FACTS)]
    # One direct fact-reference hop retains preregistration/outcome dependencies.
    references = {ref["ref_fact_id"] for fact in facts for ref in fact.get("refs", [])}
    facts += [item for item in records.get("facts", []) if
              item.get("fact_id") in references and item not in facts]
    for node in _objects([owners, facts]):
        for field in ("source_attempt_id", "source_agent_attempt_id"):
            source = node.get(field)
            if isinstance(source, str) and source in by_id:
                supporting.add(source)
        source_command = node.get("source_submit_command_id")
        if isinstance(source_command, str):
            command_ids.add(source_command)
    supporting -= selected
    evidence_ids = selected | supporting
    command_ids.update(by_id[key]["command_id"] for key in supporting)
    fact_ids = {item["fact_id"] for item in facts}
    transitions = []
    for item in records.get("transitions", []):
        if item.get("command_id") not in command_ids and item.get("fact_id") not in fact_ids:
            continue
        payload = json.loads(item.get("payload_json", "{}"))
        if payload.get("attempt_id") not in {None, *evidence_ids}:
            continue
        transitions.append(item)
    projection["related_records"] = {
        "commands": [item for item in records.get("commands", []) if item["command_id"] in command_ids],
        "transitions": transitions, "facts": facts,
        "artifacts": [item for item in records.get("artifacts", []) if item["attempt_id"] in evidence_ids],
    }
    if attempt_id is None and not recent_failures:
        projection["related_records"] = records
    projection["selected_attempt_id"] = attempt_id
    projection["attempts"] = [item for item in attempts if item["attempt_id"] in selected]
    projection["supporting_attempts"] = [item for item in attempts if item["attempt_id"] in supporting]
    if attempt_id is None:
        fields = {"attempt_id", "kind", "state", "terminal_reason", "command_id", "command_type",
            "module", "started_at", "ended_at", "failure_code", "failure_stage", "diagnostic_summary",
            "duration_seconds", "accounting", "task_identity",
            "retry_of_attempt_id", "source_attempt_id", "recovered_by_later_attempt", "final_failure_transition"}
        projection["run_attempts"] = [{key: value for key, value in item.items() if key in fields}
            for item in attempts]
        for item in projection["run_attempts"]:
            item["detail_included"] = item["attempt_id"] in evidence_ids
    else:
        projection["summary"] = {"scope": "attempt", "attempt_count": 1}
        projection["evaluator_retries"] = [item for item in projection.get("evaluator_retries", [])
            if item.get("source_attempt_id") == attempt_id]
    projection["oracle"]["scope"] = "run"
    included = evidence_ids | {projection.get("run_id")}
    for rows in projection["related_records"].values():
        for row in rows:
            included.update(row.get(key) for key in ("command_id", "fact_id", "artifact_id"))
    projection["oracle"]["identifiers_without_selected_records"] = sorted({
        identity for issue in projection["oracle"].get("issues", [])
        for identity in issue.get("identifiers", []) if identity not in included})
    return selected, supporting, len(attempts)


def mark_export_fidelity(source, exported, reason):
    """Keep source availability distinct from the evidence actually exported."""
    from .runtime_diagnostics import OMITTED_EMBEDDED_CONTENT, REDACTED
    from .support_bundle import _walk
    for group in ("attempts", "supporting_attempts"):
        originals = {item["attempt_id"]: item for item in source.get(group, [])}
        for item in exported.get(group, []):
            original = originals[item["attempt_id"]]
            fidelity = item.get("reconstructability")
            if not isinstance(fidelity, dict):
                continue
            original_fidelity = original.get("reconstructability", {})
            fidelity.setdefault("source_terminal_evidence", original_fidelity.get("terminal_evidence"))
            observation = item.get("observation", {})
            source_observation = original.get("observation", {})

            def changed(value, source_value):
                return value != source_value or any(isinstance(v, str) and
                    (OMITTED_EMBEDDED_CONTENT in v or REDACTED in v) for v in _walk(value))

            inputs = {k: v for k, v in observation.items() if k in {"request", "process_request"}}
            source_inputs = {k: v for k, v in source_observation.items() if k in {"request", "process_request"}}
            if not inputs:
                fidelity["pure_contract_input"] = "NOT_INCLUDED" if source_inputs or "observation" not in item else original_fidelity.get("pure_contract_input")
            else:
                fidelity["pure_contract_input"] = "SANITIZED_COPY" if changed(inputs, source_inputs) else original_fidelity.get("pure_contract_input")
            outputs = {k: v for k, v in source_observation.items()
                if k in {"response", "rejected_response", "discarded_response"} and isinstance(v, Mapping)}
            if "observation" not in item or (outputs and not any(k in observation for k in outputs)):
                fidelity["terminal_evidence"] = "SUMMARY_ONLY"
            else:
                original_output = outputs or {k: v for k, v in source_observation.items() if k not in source_inputs}
                exported_output = ({k: observation[k] for k in outputs if k in observation} if outputs else
                    {k: v for k, v in observation.items() if k not in inputs})
                fidelity["terminal_evidence"] = "SANITIZED_RECORDED_OUTPUT" if changed(exported_output, original_output) else original_fidelity.get("terminal_evidence")
            if changed(observation, source_observation):
                fidelity["omissions"] = list(dict.fromkeys([*fidelity.get("omissions", []), reason]))


def trim_payloads(projection, budget, encode):
    """Drop the largest duplicate bodies individually; retain small causal facts."""
    omissions = []
    if len(encode(projection)) <= budget:
        return omissions
    candidates = []
    for group in ("attempts", "supporting_attempts"):
        for item in projection.get(group, []):
            observation = item.get("observation", {})
            for key, value in observation.items():
                candidates.append((len(encode(value)), observation, key,
                    {"attempt_id": item["attempt_id"], "field": "observation." + key}))
            if "command" in item:
                candidates.append((len(encode(item["command"])), item, "command",
                    {"attempt_id": item["attempt_id"], "field": "command"}))
    for kind, records in projection.get("related_records", {}).items():
        for row in records:
            candidates.append((len(encode(row)), records, row,
                {"field": "related_records." + kind, "command_id": row.get("command_id"),
                 "fact_id": row.get("fact_id"), "workflow_id": row.get("workflow_id"),
                 "revision": row.get("revision")}))
    for _, parent, key, omission in sorted(candidates, key=lambda item: item[0], reverse=True):
        if isinstance(parent, list):
            parent.remove(key)
        else:
            del parent[key]
        omissions.append({**omission, "reason": "export_size_limit"})
        if len(encode(projection)) <= budget:
            break
    return omissions
