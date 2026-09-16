"""Independent manager session identity and native archive checks."""
import copy
from dataclasses import replace

import pytest

from hypoloom.session_oracle import audit_sessions
from hypoloom.oracle import _raw_artifact_digest
from test_agent_sessions import _manager, _transcript
from test_grounding_manager import _request, _with_plugin


@pytest.fixture
def manager_history(tmp_path):
    manager = _manager(tmp_path)
    rows, payloads = [], {}
    for cycle in (1, 2):
        _, _, request = _with_plugin(manager, replace(_request(cycle), attempt_id=f"manager-{cycle}",
            required_adapter_version="claude-code-bwrap/36"))
        workspace = manager.prepare(request, None)
        _transcript(workspace, f"Actual graph observation for trigger {cycle}.")
        rows.append({"attempt_id": request.attempt_id, "kind": "AGENT", "state": "CLOSED",
            "terminal_reason": "SUCCEEDED", "started_at": cycle * 2, "ended_at": cycle * 2 + 1,
            "process_group_id": 12345})
        payloads[request.attempt_id] = {"request": request.to_dict(), "session_binding": workspace.binding,
            "session": workspace.finish_node()}
        workspace.cleanup()
    return manager, rows, payloads


def test_independent_manager_oracle_accepts_cross_trigger_resume_and_separate_archives(manager_history):
    manager, rows, payloads = manager_history
    assert payloads["manager-2"]["session"]["launch_arguments"] == [
        "--resume", payloads["manager-1"]["session_binding"]["session_id"]]
    assert audit_sessions(manager.publisher.root, rows, payloads, _raw_artifact_digest) == ([], 2)


@pytest.mark.parametrize("tamper", ["role", "component", "task_owner", "scope", "cycle", "changed_session"])
def test_manager_session_owner_scope_and_restart_identity_cannot_drift(manager_history, tamper):
    manager, rows, original = manager_history
    payloads = copy.deepcopy(original); second = payloads["manager-2"]
    if tamper == "role": second["request"]["role"] = "expert"
    elif tamper == "component": second["request"]["component_id"] = "primary.score"
    elif tamper == "task_owner": second["request"]["task_context"]["expert_slot"] = "expert"
    elif tamper == "scope": second["session_binding"]["scope"] = "synthesis"
    elif tamper == "cycle": second["session_binding"]["cycle"] = 2
    else: second["session_binding"]["session_id"] = "abababab-abab-4bab-abab-abababababab"
    issues, _ = audit_sessions(manager.publisher.root, rows, payloads, _raw_artifact_digest)
    assert {item[0] for item in issues} & {"session_binding_invalid", "session_restart_identity"}
