"""Run-local effects use accepted identities, whole retry chains and unknowns."""
import json

import pytest
from test_observer_details import _database, _plugin
from test_observer_reliability import correction
from test_observer_skills import _bound, _calls, _capture

from hypoloom.observer_details import _request_plugin, read_observer_detail
from hypoloom.skill_observations import (
    followups,
    outcome_nodes,
    runtime_feedback,
    scorecards,
)
from hypoloom.state_store import StateStore


def attempt(identity, commit, *, start=1, ordinal=1, command_id=None, reason="FAILED",
            model="model-a", command="EXPERIMENT_DESIGN_IMPLEMENT", cycle=1, chain="a", diagnostic="frozen measurement",
            update=None):
    row = _bound(identity, commit, start=start, command=command, update=update)
    row.update(attempt_ordinal=ordinal, terminal_reason=reason)
    obs = json.loads(row["observation_json"])
    obs["request"].update(command_id=command_id or identity, required_model_identity=model)
    obs["request"]["task_context"]["key"].update(cycle=cycle, chain=chain)
    obs["diagnostics"] = {"structured_output_correction": correction(0 if reason == "SUCCEEDED" else 1, accepted=reason == "SUCCEEDED"),
        "provider_accounting": {"token_usage": {"total_tokens": 10}, "turn_count": 1, "cost": "0.5", "cost_currency": "USD"}}
    if reason == "FAILED":
        obs.update(failure_code="invalid_candidate", failure_stage="host_candidate_validation")
        obs["diagnostics"]["validation_error"] = {"code": "invalid_candidate", "message": diagnostic}
    row["observation_json"] = json.dumps(obs)
    return row


def transition(payload, **kwargs):
    return {"run_id": "run", "workflow_kind": "RUN", "payload": payload, **kwargs}


def nodes(root, rows, transitions=()):
    bindings = {r["attempt_id"]: _request_plugin(r, "run") for r in rows if r["run_id"] == "run"}
    return outcome_nodes(root, "run", rows, bindings, transitions=transitions)


def event(base, commit, ended=10):
    return {"status": "accepted_change", "role": "researcher", "attempt_id": "evolve", "base_commit": base,
        "commit": commit, "ended_at": ended, "skills": [{"name": "resource-planning"}]}


def test_final_failure_closes_one_node_and_retries_cost_once(tmp_path):
    rows = [attempt("first", "a" * 40, command_id="design"),
        attempt("second", "a" * 40, start=3, ordinal=2, command_id="design")]
    assert nodes(tmp_path, rows)[0]["metrics"]["node_delivery"]["unknown"] == 1
    result = nodes(tmp_path, rows, [transition({"agent_final_failure": True, "attempt_id": "second"})])
    calls = [{"attempt_id": "second", "name": "resource-planning"}] * 4
    cards = scorecards(result, calls)["groups"]
    assert len(cards) == 2
    for card in cards:
        assert card["metrics"]["node_delivery"] == {"numerator": 0, "denominator": 1, "rate": 0, "unknown": 0, "excluded": 0}
        assert card["counts"]["agent_attempts"] == 2
        assert card["metrics"]["node_retry"]["rate"] == 1
        assert card["costs"]["retry_reported_usd"] == "0.5"
        assert card["costs"]["retry_reported_tokens"] == 10
        assert card["costs"]["retry_elapsed_seconds"] == 1
    assert {c["basis"] for c in cards} == {"bound_version", "observed_call"}


def test_missing_capture_not_a_zero_use_or_zero_first_pass(tmp_path):
    row = attempt("a", "a" * 40, reason="SUCCEEDED")
    obs = json.loads(row["observation_json"]); obs.pop("diagnostics")
    row["observation_json"] = json.dumps(obs)
    cards = scorecards(nodes(tmp_path, [row]))["groups"]
    assert len(cards) == 1 and cards[0]["basis"] == "bound_version"
    assert cards[0]["counts"]["trace_unknown_attempts"] == 1
    assert cards[0]["metrics"]["output_first_pass"]["rate"] is None
    assert cards[0]["metrics"]["node_delivery"]["rate"] == 1


def test_later_run_cancellation_does_not_erase_recorded_final_failure(tmp_path):
    row = attempt("final-failed", "a" * 40)
    terminal = [transition({"agent_final_failure": True, "attempt_id": "final-failed"}),
        transition({"stop_disposition": "CANCELLED"}, state="CANCELLED", created_at=30)]
    assert nodes(tmp_path, [row], terminal)[0]["metrics"]["node_delivery"]["rate"] == 0
    # A failure that had no durable exhaustion still follows the existing stop semantics.
    assert nodes(tmp_path, [row], terminal[1:])[0]["metrics"]["node_delivery"]["excluded"] == 1


def test_filters_do_not_recast_last_retry_as_first_or_mix_versions(tmp_path):
    rows = [attempt("first", "a" * 40, command_id="design"),
        attempt("second", "a" * 40, start=3, ordinal=2, command_id="design", reason="SUCCEEDED")]
    result = scorecards(nodes(tmp_path, rows), selected_ids={"second"})
    assert result["groups"] == [] and result["partial_nodes_excluded"] == 1
    obs = json.loads(rows[1]["observation_json"])
    obs["session"]["expert_resources"]["plugin"]["commit"] = "b" * 40
    rows[1]["observation_json"] = json.dumps(obs)
    result = scorecards(nodes(tmp_path, rows))
    assert result["groups"] == [] and result["unassigned_nodes"] == 1


@pytest.mark.parametrize("later_model,diagnostic,reason,status", [
    ("model-a", "frozen measurement", "FAILED", "recurrence_observed"),
    ("model-b", "frozen measurement", "FAILED", "awaiting_samples"),
    ("unknown", "frozen measurement", "FAILED", "awaiting_samples"),
    ("model-a", "different failure", "FAILED", "awaiting_samples"),
    ("model-a", "", "SUCCEEDED", "not_observed"),
])
def test_followup_observes_recurrence_not_causal_improvement(tmp_path, later_model, diagnostic, reason, status):
    rows = [attempt("base", "a" * 40), attempt("later", "b" * 40, start=20, cycle=2,
        model=later_model, diagnostic=diagnostic, reason=reason)]
    result = followups(nodes(tmp_path, rows), [event("a" * 40, "b" * 40)])["followups"][0]
    assert result["comparisons"][0]["status"] == status
    assert "resolved" not in json.dumps(result)
    assert result["comparisons"][0]["baseline_attempt_ids"] == ["base"]
    if status == "recurrence_observed":
        assert result["comparisons"][0]["later_attempt_ids"] == ["later"]
    if status == "not_observed":
        assert result["status"] == "observations_available"


def test_failure_backtrace_requires_exact_accepted_source_and_owner(tmp_path):
    source = attempt("proposal", "a" * 40, command="HYPOTHESIS_PROPOSAL", reason="SUCCEEDED", chain=None)
    failed = attempt("failed", "a" * 40, start=3)
    accepted = {"agent_attempt_id": "proposal", "node_command_id": "proposal", "hypothesis_id": "h1",
        "role": "researcher", "component_id": "component", "chain": "a"}
    original = nodes(tmp_path, [source, failed], [transition(accepted)])[1]["failures"][0]
    assert original["source_proposals"] == [{"attempt_id": "proposal", "hypothesis_id": "h1"}]
    for altered in [dict(accepted, node_command_id="other"), dict(accepted, role="peer"), dict(accepted, chain="b")]:
        failed_view = nodes(tmp_path, [source, failed], [transition(altered)])[1]["failures"][0]
        assert failed_view["source_proposals"] == []
    assert nodes(tmp_path, [source, failed], [transition(accepted, run_id="foreign")])[1]["failures"][0]["source_proposals"] == []


def test_http_projection_keeps_new_revision_unvalidated_and_calls_separate(tmp_path):
    _, base, commit = _plugin(tmp_path)
    rows = [attempt("failed", base), attempt("evolve", base, start=5, command="SELF_EVOLVE", reason="SUCCEEDED",
        update={"base_commit": base, "commit": commit})]
    _database(tmp_path, rows, transitions=[("run", "run", "RUN", 1, "final", 3,
        json.dumps({"agent_final_failure": True, "attempt_id": "failed"}))])
    _capture(tmp_path, rows[0], _calls())
    before = (tmp_path / "state.sqlite").read_bytes()
    library = read_observer_detail(tmp_path, "run", "skills")["activity"]["effects"]
    followup = library["followups"][0]
    assert followup["status"] == "awaiting_samples" and followup["later_nodes"] == 0
    cards = [g for g in library["groups"] if g["stage"] == "EXPERIMENT_DESIGN_IMPLEMENT"]
    assert {g["basis"] for g in cards} == {"bound_version", "observed_call"}
    assert all(g["metrics"]["node_delivery"]["rate"] == 0 for g in cards)
    detail = read_observer_detail(tmp_path, "run", "skill", role="researcher", name="resource-planning", commit=commit)
    assert detail["effects"]["groups"] == []
    assert detail["effects"]["followups"][0] == followup
    assert (tmp_path / "state.sqlite").read_bytes() == before


def test_followup_continues_across_later_commits_but_only_changed_skills(tmp_path):
    rows = [attempt("base", "a" * 40), attempt("later", "c" * 40, start=30)]
    observed = nodes(tmp_path, rows)
    assert followups(observed, [event("a" * 40, "b" * 40)], name="untouched")["followups"] == []
    match = followups(observed, [event("a" * 40, "b" * 40)])["followups"][0]
    assert match["status"] == "recurrence_observed"
    assert match["comparisons"][0]["later_commits"] == ["c" * 40]


def test_missing_diagnostics_cannot_establish_recurrence_or_resolution(tmp_path):
    rows = [attempt("before", "a" * 40, reason="LOST"), attempt("after", "b" * 40, reason="LOST", start=20)]
    result = followups(nodes(tmp_path, rows), [event("a" * 40, "b" * 40)])["followups"][0]
    assert result["status"] == "awaiting_samples"
    assert result["comparisons"][0]["later_failed_attempts"] == 0


def test_later_successful_samples_are_observations_without_baseline_failure(tmp_path):
    rows = [attempt("before", "a" * 40, reason="SUCCEEDED"), attempt("after", "b" * 40, reason="SUCCEEDED", start=20)]
    result = followups(nodes(tmp_path, rows), [event("a" * 40, "b" * 40)])["followups"][0]
    assert result["status"] == "observations_available" and result["comparisons"] == []


def test_skill_context_reuses_goal_stop_exclusion_and_ignores_foreign_facts(tmp_path):
    rows = [attempt("failed", "a" * 40)]
    bindings = {"failed": _request_plugin(rows[0], "run")}
    facts = [{"run_id": "run", "fact_type": "GoalVerdictRecorded", "created_at": 5, "payload": {"verdict": "GOAL_MET"}}]
    result = outcome_nodes(tmp_path, "run", rows, bindings, transitions=[transition({}, state="COMPLETED")], facts=facts)
    assert result[0]["metrics"]["node_delivery"]["excluded"] == 1
    facts[0]["run_id"] = "other"
    result = outcome_nodes(tmp_path, "run", rows, bindings, transitions=[transition({}, state="COMPLETED")], facts=facts)
    assert result[0]["metrics"]["node_delivery"]["rate"] == 0


def test_runtime_feedback_is_bounded_readonly_metadata_and_owner_scoped(tmp_path, monkeypatch):
    rows = [attempt(f"a{i}", "a" * 40, start=i * 3, command_id=f"node-{i // 2}", ordinal=i % 2 + 1,
        diagnostic="Authorization: Bearer private-secret") for i in range(8)]
    peer = attempt("peer", "b" * 40, start=100)
    obs = json.loads(peer["observation_json"]); obs["request"]["component_id"] = "other"
    peer["observation_json"] = json.dumps(obs)
    _database(tmp_path, rows + [peer])
    # Extend only this old minimal test fixture to the production transition columns.
    import sqlite3
    with sqlite3.connect(tmp_path / "state.sqlite") as db:
        db.execute("ALTER TABLE workflow_transitions ADD COLUMN state TEXT")
    db.close()
    monkeypatch.setattr("hypoloom.observer_evidence.read_attempt_evidence", lambda *args: pytest.fail("runtime scanned transcript"))
    monkeypatch.setattr("hypoloom.observer_details._files", lambda *args: pytest.fail("runtime read Git"))
    before = (tmp_path / "state.sqlite").read_bytes()
    result = runtime_feedback(StateStore(tmp_path), tmp_path, "run", "researcher", "component", max_attempts=5, max_bytes=6000)
    assert result["history_truncated"] and result["attempts_observed"] == 5
    assert result["incomplete_nodes_excluded"] == 1
    assert result["groups"] and result["groups"][0]["counts"]["agent_attempts"] == 4
    encoded = json.dumps(result, ensure_ascii=False).encode()
    assert len(encoded) <= 6000
    assert b"private-secret" not in encoded and b"peer" not in encoded
    assert (tmp_path / "state.sqlite").read_bytes() == before
