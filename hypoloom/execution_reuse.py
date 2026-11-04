"""Explicit, same-owner publication inputs for incremental experiments.

Uses existing submissions, terminal outcomes and publication verification. It
does not decide scientific equivalence or promote retained partial results.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from .agent_aci import AgentACIError, AgentRequest


REUSE_ACI_VERSION = 32
REUSE_ENV = "HYPOLOOM_REUSE_INPUTS"


def _source(host, run_id: str, component_id: str, role: str, experiment_id: str):
    spec = host.store.find_fact(run_id, "ExperimentPreregistered", experiment_id)
    head = host.store.latest_transition(experiment_id)
    creation = host.store.inspect("SELECT payload_json FROM workflow_transitions WHERE run_id=? AND workflow_id=? AND revision=1",
                                  (run_id, experiment_id))
    identity = json.loads(creation[0]["payload_json"]) if len(creation) == 1 else {}
    if (spec is None or head is None or head["state"] != "TERMINAL"
            or identity.get("role") != role or identity.get("component_id") != component_id):
        raise AgentACIError("reuse_source_invalid", "reuse requires a terminal experiment from this run, component and owner")
    outcomes = [row for row in host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
                if row["experiment_id"] == experiment_id]
    if len(outcomes) != 1 or outcomes[0]["payload"]["outcome"] == "INTEGRITY_REJECTED":
        raise AgentACIError("reuse_source_invalid", "reuse requires a published outcome without integrity rejection")
    return spec, outcomes[0]


def submission_inputs(host, request: AgentRequest, arguments: Mapping, *, verify: bool = True) -> list[dict]:
    selected = arguments.get("reuse_experiments", [])
    if not selected:
        return []
    if request.schema_version < REUSE_ACI_VERSION:
        raise AgentACIError("wrong_schema_version", "publication reuse requires ACI 32")
    result = []
    for index, experiment_id in enumerate(selected, 1):
        _, outcome = _source(host, request.run_id, str(request.component_id), request.role, experiment_id)
        if verify:
            host._outcome_publication(request.run_id, outcome["outcome_id"], outcome["artifact_id"])
        result.append({"experiment_id": experiment_id, "outcome_id": outcome["outcome_id"],
                       "artifact_id": outcome["artifact_id"], "path": f"/hypoloom-reuse/{index}"})
    return result


def execution_mounts(host, run_id: str, spec: Mapping) -> tuple[tuple[Path, str], ...]:
    contract = spec["payload"]["execution_contract"]
    environment = dict(contract.get("environment", ()))
    submit_id = contract.get("source_submit_command_id")
    if submit_id is None:
        if REUSE_ENV in environment:
            raise ValueError("reuse inputs lack their submitted source")
        return ()
    _, request, snapshot, _ = host._submission_source(submit_id)
    expected = submission_inputs(host, request, snapshot["arguments"], verify=False)
    recorded = json.loads(environment.get(REUSE_ENV, "[]"))
    if recorded != expected:
        raise ValueError("reuse inputs differ from their frozen submission/publications")
    mounts = []
    for item in expected:
        _, root, _ = host._outcome_publication(run_id, item["outcome_id"], item["artifact_id"])
        mounts.append((root, item["path"]))
    return tuple(mounts)


def prior_executions(host, run_id: str, component_id: str, role: str) -> list[dict]:
    """Bounded context from ordinary durable facts, without a second history."""
    rows = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
    result = []
    for outcome in reversed(rows):
        if outcome["payload"].get("component_id") != component_id:
            continue
        try:
            spec, _ = _source(host, run_id, component_id, role, outcome["experiment_id"])
        except AgentACIError:
            continue
        contract = spec["payload"]["execution_contract"]
        result.append({"experiment_id": outcome["experiment_id"],
                       "outcome_id": outcome["outcome_id"], "artifact_id": outcome["artifact_id"],
                       "outcome": outcome["payload"]["outcome"],
                       "source_submit_command_id": contract.get("source_submit_command_id"),
                       "metric_value": outcome["payload"].get("metric_value"),
                       "reuse_requires_compatible_unit_context": True})
        if len(result) == 8:
            break
    return result


def compare_submission(host, request: AgentRequest, arguments: Mapping, files: Mapping) -> list[dict]:
    """Describe source differences; equality is not scientific equivalence."""
    if request.schema_version < REUSE_ACI_VERSION:
        return []
    comparisons = []
    for prior in prior_executions(host, request.run_id, str(request.component_id), request.role)[:3]:
        submit_id = prior["source_submit_command_id"]
        if submit_id is None:
            continue
        _, _, snapshot, validated = host._submission_source(submit_id)
        previous = validated.workspace_files
        changed = sorted(name for name in set(previous) | set(files) if previous.get(name) != files.get(name))
        old_arguments = snapshot["arguments"]
        old_argv = old_arguments.get("command", old_arguments["candidate"].get("execution_argv"))
        argv = arguments.get("command", arguments["candidate"].get("execution_argv"))
        comparisons.append({"experiment_id": prior["experiment_id"], "outcome": prior["outcome"],
                            "changed_workspace_files": changed[:64], "changed_file_count": len(changed),
                            "same_command": old_argv == argv,
                            "same_submitted_sources": not changed,
                            "reuse_requested": prior["experiment_id"] in arguments.get("reuse_experiments", []),
                            "interpretation": "Source comparison only; explain new evidence or explicit replication in the design."})
    return comparisons
