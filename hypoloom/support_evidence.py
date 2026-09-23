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
        while True:
            previous = set(selected)
            for item in attempts:
                if item.get("retry_of_attempt_id") in selected:
                    selected.add(item["attempt_id"])
                if item["attempt_id"] in selected and item.get("retry_of_attempt_id") in by_id:
                    selected.add(item["retry_of_attempt_id"])
            if previous == selected:
                break
    else:
        selected = set(by_id)
    supporting = set()
    if recent_failures and attempt_id is None:
        first = projection["summary"].get("first_unrecovered_failure_attempt_id")
        if first in by_id:
            supporting.add(first)
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
        item.get("execution_id") in executions]
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
            fidelity.setdefault("source_terminal_evidence", fidelity.get("terminal_evidence"))
            if "observation" not in item:
                fidelity["terminal_evidence"] = "SUMMARY_ONLY"
                fidelity["pure_contract_input"] = "NOT_INCLUDED"
            elif item["observation"] != original.get("observation") or any(
                    isinstance(value, str) and (OMITTED_EMBEDDED_CONTENT in value or REDACTED in value)
                    for value in _walk(item["observation"])):
                fidelity["terminal_evidence"] = "SANITIZED_RECORDED_OUTPUT"
                fidelity["pure_contract_input"] = "SANITIZED_COPY"
            else:
                continue
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
