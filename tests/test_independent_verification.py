"""Portable public checks for independent implementation verification."""
from copy import deepcopy
from dataclasses import replace

import pytest

from hypoloom.agent_aci import AgentACIError, validate_postflight, with_output_digest
from hypoloom.agent_capabilities import AGENT_CAPABILITY_MATRIX
from hypoloom.agent_sessions import AgentSessions
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.profile_v4 import resolve_node_runtime
from hypoloom.verification_observation import verification_view


def test_verifier_model_requires_independent_explicit_selection():
    experts = {"defaults": {"model": "author-model", "effort": "low"},
               "experts": [{"name": "author", "model": "personal-author-model"}]}
    contract = {"methodology": {"controls": {"product": {"experts": experts, "language": {}, "methodology": {}, "safety": {}}}}}
    assert "model" not in resolve_node_runtime(contract, "author", "verifier")
    experts["verifier"] = {"model": "review-model", "effort": "high"}
    selected = resolve_node_runtime(contract, "author", "verifier")
    assert selected["model"] == "review-model" and selected["effort"] == "high"


@pytest.mark.parametrize("defect", ["unexecuted_pass", "unbounded_probe", "candidate_write", "wrong_evidence"])
def test_author_claims_and_invalid_probes_cannot_approve_training(defect):
    request = default_request(AgentCommand.VERIFY_IMPLEMENTATION)
    response = FakeAgent().invoke(request)
    report = deepcopy(response.typed_candidate)
    if defect == "unexecuted_pass": report.update(verdict="passed", probe=None)
    elif defect == "unbounded_probe": report["probe"]["timeout_seconds"] = 1000
    elif defect == "candidate_write": report["probe"]["files"] = {"candidate.py": "print('changed')"}
    else: report["supporting_refs"] = ["another-run"]
    with pytest.raises(AgentACIError):
        validate_postflight(request, with_output_digest(replace(response, typed_candidate=report)))


def test_verifier_capabilities_and_sessions_are_independent():
    capability = AGENT_CAPABILITY_MATRIX[AgentCommand.VERIFY_IMPLEMENTATION]
    assert capability.built_in_tools == ("Read", "Glob", "Grep") and not capability.bash
    request = default_request(AgentCommand.VERIFY_IMPLEMENTATION)
    first = AgentSessions.binding(request)
    second = AgentSessions.binding(replace(request, attempt_id="second", attempt_ordinal=2))
    assert first["session_id"] != second["session_id"]
    assert first["parent_session_id"] is second["parent_session_id"] is None


def test_overview_separates_subjects_and_never_claims_delivery_without_evidence():
    commands, attempts = [], []
    for role in ("alice", "bob"):
        commands.append({"command_id": role, "request": {"implementation_verification": {
            "experiment_id": role, "phase": "inspect", "subject_expert": role,
            "component_id": "code", "delivery_id": role, "source_output_digest": "a" * 64}}})
        attempts.append({"attempt_id": role, "command_id": role, "kind": "AGENT", "state": "CLOSED",
            "terminal_reason": "SUCCEEDED", "observation": {"request": {"command": "VERIFY_IMPLEMENTATION"},
                "response": {"typed_candidate": {"verdict": "changes_required", "summary": "Code defect",
                    "resolved_refs": [], "findings": [{"id": "dispatch", "severity": "blocker", "message": "Wrong method called"}]}}}})
    contract = {"methodology": {"controls": {"product": {"experts": {"verifier": {"model": "review-model"}}}}}}
    view = verification_view(contract, commands, attempts)
    assert view["model"] == "review-model" and len(view["experts"]) == 2
    for subject in view["experts"]:
        assert subject["blockers"] == 1 and subject["first_pass"] == {"passed": 0, "completed": 1}
        finding = subject["findings"][0]
        assert finding["finding_ref"] == subject["expert"] + "#dispatch"
        assert finding["delivery_state"] == "generated" and finding["state"] == "open"
