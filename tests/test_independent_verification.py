"""Portable public checks for independent implementation verification."""
from copy import deepcopy
from dataclasses import replace
import json
import sqlite3

import pytest

from hypoloom.agent_aci import AgentACIError, validate_postflight, with_output_digest
from hypoloom.agent_capabilities import AGENT_CAPABILITY_MATRIX
from hypoloom.agent_sessions import AgentSessions
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.profile_v4 import resolve_node_runtime
from hypoloom.verification_observation import read_verification_view, verification_view


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


def test_verifier_summary_counts_current_issues_across_paginated_history():
    contract = {"methodology": {"controls": {"product": {"experts": {"verifier": {"model": "review-model"}}}}}}
    commands, attempts = [], []
    rows = [
        ("d1", "old", "inspect", "changes_required", "blocker", None),
        ("d1", "new", "inspect", "probe_required", "advisory", "report-0#issue"),
        ("d1", "new", "review", "inconclusive", "advisory", "report-1#issue"),
        ("d2", "second", "inspect", "changes_required", "blocker", None),
        ("d2", "second", "review", "changes_required", "blocker", "report-3#issue"),
    ]
    for n, (delivery, experiment, phase, verdict, severity, followup) in enumerate(rows):
        commands.append({"command_id": str(n), "request": {"implementation_verification": {
            "experiment_id": experiment, "phase": phase, "subject_expert": "alice", "component_id": "code",
            "delivery_id": delivery, "source_output_digest": "a" * 64}}})
        attempts.append({"attempt_id": f"report-{n}", "command_id": str(n), "experiment_id": None,
            "execution_id": None, "kind": "AGENT", "state": "CLOSED", "terminal_reason": "SUCCEEDED",
            "started_at": n * 2, "ended_at": n * 2 + 1,
            "observation": {"request": {"command": "VERIFY_IMPLEMENTATION"}, "response": {"typed_candidate": {
                "verdict": verdict, "summary": "Detailed evidence", "resolved_refs": [],
                "findings": [{"id": "issue", "severity": severity, "message": "Detailed finding",
                    "resolution_condition": "Verified correction", "followup_of": followup}]}}}})
    full = verification_view(contract, commands, attempts)
    paged = verification_view(contract, commands, attempts, limit=1, offset=1)
    expected = {"inconclusive": 1, "changes_required": 1}
    for subject in (full["experts"][0], paged["experts"][0]):
        assert subject["state_counts"] == expected
        assert subject["current_blockers"] == subject["current_advisories"] == 1
        assert subject["blockers"] == 3 and subject["advisories"] == 2
        assert subject["finding_count"] == 5
    assert len(paged["experts"][0]["recent"]) == 1
    assert len(full["experts"][0]["findings"]) == 5
    # The observer's lightweight SQL projection must retain follow-up lineage,
    # including older reports whose verbose bodies are outside the current page.
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE workflow_commands(command_id,run_id,command_type,request_json)")
        db.execute("CREATE TABLE execution_attempts(attempt_id,command_id,run_id,experiment_id,execution_id,kind,state,"
            "started_at,ended_at,terminal_reason,observation_json)")
        for row in commands:
            db.execute("INSERT INTO workflow_commands VALUES(?,?,?,?)", (row["command_id"], "run", "REQUEST_AUXILIARY", json.dumps(row["request"])))
        for row in attempts:
            db.execute("INSERT INTO execution_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (row["attempt_id"], row["command_id"], "run", row["experiment_id"], row["execution_id"], row["kind"],
                 row["state"], row["started_at"], row["ended_at"], row["terminal_reason"], json.dumps(row["observation"])))
        subject = read_verification_view(db, "run", contract, limit=1, offset=1)["experts"][0]
        assert subject["state_counts"] == expected
        assert subject["current_blockers"] == subject["current_advisories"] == 1


def test_tui_verifier_card_stays_compact_with_large_report_history(monkeypatch):
    pytest.importorskip("textual")
    from hypoloom import tui
    monkeypatch.setattr(tui, "tr", lambda value, *args: value.format(*args))
    subjects = [{"expert": name, "state_counts": {"review": 1, "passed": 3},
        "current_blockers": 2, "current_advisories": 1,
        "findings": [{"message": "VERY_LONG_FINDING " * 1000, "finding_ref": "INTERNAL_ID"}] * 200,
        "recent": [{"reports": [{"summary": "VERBOSE_REPORT " * 1000}]}] * 100}
        for name in ("alice", "bob", "carol", "dave")]
    view = {"state": "active", "model": "review-model", "effort": "high", "experts": subjects}
    lines = tui._verification_summary_lines(view)
    # State and model identity each have one row; findings remain in history.
    assert len(lines) == len(subjects) + 3 and len("\n".join(lines)) < 600
    assert lines[1] == "Model: review-model"
    assert all(any(name in line and "blockers 2" in line for line in lines) for name in ("alice", "bob", "carol", "dave"))
    assert "VERY_LONG_FINDING" not in "\n".join(lines) and "INTERNAL_ID" not in "\n".join(lines)
    assert "v · Reports" in lines[-1]
    assert tui._verification_summary_lines(view, host_available=False)[0].startswith("OPEN?")
