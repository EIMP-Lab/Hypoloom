from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.domain import AgentCommand
from hypoloom.agent_aci import EmbeddedInput
from hypoloom.oracle import _raw_artifact_digest
from hypoloom.session_oracle import audit_sessions
from test_agent_sessions import _chain_request, _manager, _request, _seed, _transcript
from test_git_authoring_workspace import make_target


@pytest.fixture(params=[(11, "claude-code-bwrap/32"), (11, "claude-code-bwrap/33"),
                        (12, "claude-code-bwrap/34"), (13, "claude-code-bwrap/34")])
def closed_sessions(tmp_path: Path, request):
    schema_version, adapter_version = request.param
    _, target = make_target(tmp_path)
    manager = _manager(tmp_path)
    seed = _seed(manager, target)
    design = manager.prepare(_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, "design"), target)
    (design.worktree_root / "experiment.py").write_text("print('tested')\n")
    _transcript(design, "same-chain test evidence")
    rows, payloads = [], {}
    for index, workspace in enumerate((seed, design)):
        request = replace(workspace.request, required_adapter_version=adapter_version)
        rows.append({"attempt_id": request.attempt_id, "kind": "AGENT", "state": "CLOSED",
            "terminal_reason": "SUCCEEDED", "started_at": index * 2,
            "ended_at": index * 2 + 1, "process_group_id": 12345})
        # A future envelope is not newly admitted here: its raw durable session
        # evidence must simply not disappear from this independent audit.
        payloads[request.attempt_id] = {"request": {**request.to_dict(), "schema_version": schema_version},
            "session_binding": workspace.binding, "session": workspace.finish_node()}
    return manager, rows, payloads


def test_independent_oracle_counts_archives_and_real_parentage(closed_sessions) -> None:
    manager, rows, payloads = closed_sessions
    issues, count = audit_sessions(manager.publisher.root, rows, payloads, _raw_artifact_digest)
    assert issues == []
    assert count == 2


@pytest.mark.parametrize("legacy", [True, False])
def test_session_oracle_keeps_pre_full_cc_and_fake_adapter_boundaries(closed_sessions, legacy):
    manager, rows, payloads = closed_sessions
    for observation in payloads.values():
        if legacy:
            observation["request"]["schema_version"] = 10
        else:
            observation["request"]["required_adapter_version"] = "fake-agent/1"
        observation.pop("session_binding")
        observation.pop("session")
    issues, count = audit_sessions(manager.publisher.root, rows, payloads, _raw_artifact_digest)
    assert issues == []
    assert count == 0


def test_corrupt_task_is_reported_without_crashing_oracle(closed_sessions) -> None:
    manager, rows, payloads = closed_sessions
    payloads["design"]["request"]["task_context"] = None
    issues, _ = audit_sessions(manager.publisher.root, rows, payloads, _raw_artifact_digest)
    assert "session_binding_invalid" in {item[0] for item in issues}


@pytest.mark.parametrize("limit", [1, 0, True, "100"])
def test_oracle_enforces_frozen_node_transcript_budget(closed_sessions, limit) -> None:
    manager, rows, payloads = closed_sessions
    payloads["design"]["request"]["input_artifacts"] = [EmbeddedInput.from_bytes(
        "node--expert-config", "application/json", json.dumps({"node_runtime": {
            "memory": {"max_transcript_bytes": limit}}}).encode()).to_dict()]
    issues, count = audit_sessions(manager.publisher.root, rows, payloads, _raw_artifact_digest)
    assert "session_archive_invalid" in {item[0] for item in issues}
    assert count == 1


def test_oracle_accepts_profile_transcript_budget_above_legacy_default(closed_sessions) -> None:
    manager, rows, payloads = closed_sessions
    observation = payloads["design"]
    directory = manager.publisher.root / observation["session"]["transcript"]["path"]
    content = json.dumps({"sessionId": observation["session_binding"]["session_id"],
                          "message": "a" * (64 * 1024 * 1024)}).encode() + b"\n"
    (directory / "transcript.jsonl").write_bytes(content)
    observation["session"]["transcript"]["digest"] = _raw_artifact_digest(directory)
    observation["request"]["input_artifacts"] = [EmbeddedInput.from_bytes(
        "node--expert-config", "application/json", json.dumps({"node_runtime": {
            "memory": {"max_transcript_bytes": len(content)}}}).encode()).to_dict()]
    issues, count = audit_sessions(manager.publisher.root, rows, payloads, _raw_artifact_digest)
    assert issues == []
    assert count == 2


@pytest.mark.parametrize("tamper", [None, "alias", "owner", "request_chain"])
def test_oracle_independently_checks_explicit_chain_scope_without_legacy_alias(tmp_path, tamper):
    _, target = make_target(tmp_path)
    manager = _manager(tmp_path)
    workspaces = [_seed(manager, target)]
    for index, chain in enumerate((None, "1", "chain-a")):
        workspace = manager.prepare(_chain_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, f"design-{index}", chain), target)
        _transcript(workspace, f"private chain {chain}")
        workspaces.append(workspace)
    rows, payloads = [], {}
    for index, workspace in enumerate(workspaces):
        request = replace(workspace.request, required_adapter_version="claude-code-bwrap/34")
        rows.append({"attempt_id": request.attempt_id, "kind": "AGENT", "state": "CLOSED", "terminal_reason": "SUCCEEDED",
                     "started_at": index * 2, "ended_at": index * 2 + 1, "process_group_id": 123})
        payloads[request.attempt_id] = {"request": request.to_dict(), "session_binding": workspace.binding, "session": workspace.finish_node()}
    if tamper == "alias": payloads["design-1"]["session_binding"]["scope"] = "chain-1"
    elif tamper == "owner": payloads["design-1"]["session_binding"]["session_id"] = payloads["design-0"]["session_binding"]["session_id"]
    elif tamper == "request_chain": payloads["design-1"]["request"]["task_context"]["key"]["chain"] = "foreign"
    issues, count = audit_sessions(manager.publisher.root, rows, payloads, _raw_artifact_digest)
    if tamper is None:
        assert issues == [] and count == 4
    else:
        assert {"session_binding_invalid", "session_cross_owner"} & {item[0] for item in issues}


@pytest.mark.parametrize(("mutation", "code"), [
    ("parent", "session_parent_mismatch"),
    ("binding", "session_close_binding_mismatch"),
    ("archive", "session_archive_missing"),
    ("bytes", "session_archive_invalid"),
    ("launch", "session_launch_mismatch"),
])
def test_oracle_rejects_tampered_session_evidence(closed_sessions, mutation: str, code: str) -> None:
    manager, rows, original = closed_sessions
    payloads = copy.deepcopy(original)
    observation = payloads["design"]
    if mutation == "parent":
        observation["session_binding"]["parent_session_id"] = "8276ce0c-74dc-451e-87c5-3283829dd9b9"
    elif mutation == "binding":
        observation["session"]["session_id"] = "8276ce0c-74dc-451e-87c5-3283829dd9b9"
    elif mutation == "archive":
        observation["session"]["transcript"] = None
    elif mutation == "bytes":
        path = manager.publisher.root / observation["session"]["transcript"]["path"] / "transcript.jsonl"
        path.write_text('{"sessionId":"foreign"}\n')
    else:
        observation["session"]["launch_arguments"] = ["--session-id", observation["session_binding"]["session_id"]]
    issues, _ = audit_sessions(manager.publisher.root, rows, payloads, _raw_artifact_digest)
    assert code in {item[0] for item in issues}
