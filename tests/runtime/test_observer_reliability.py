"""Read-side metrics must distinguish delivery, repair and uncertain causation."""
from __future__ import annotations

import json

import pytest

from test_observer_summary import _attempt, _fact, _summary, _transition


def row(identity, *, command="node", ordinal=1, reason="SUCCEEDED", correction=None,
        model="model-a", cost="0.5", **kwargs):
    value = _attempt(identity, command_id=command, reason=reason, **kwargs)
    value.update(workflow_id="workflow", attempt_ordinal=ordinal)
    obs = json.loads(value["observation_json"])
    obs["request"].update(required_model_identity=model,
        budget={"max_cost": "2", "max_tokens": 100, "max_turns": 10})
    obs["diagnostics"] = {}
    if cost is not None:
        obs["diagnostics"]["provider_accounting"] = {"token_usage": {"total_tokens": 10},
            "turn_count": 1, "cost": cost, "cost_currency": "USD"}
    if correction is not None:
        obs["diagnostics"]["structured_output_correction"] = correction
    value["observation_json"] = json.dumps(obs)
    return value


def correction(rejected=0, *, accepted=True):
    rounds = [{"ordinal": i + 1, "status": "rejected", "validation_error": "invalid JSON"}
        for i in range(rejected)]
    if accepted:
        rounds.append({"ordinal": len(rounds) + 1, "status": "accepted", "validation_error": None})
    return {"scope": "native_stop_validation", "accepted": accepted, "rejected_count": rejected,
        "call_count": len(rounds), "rounds": rounds,
        "status": "recovered" if rejected and accepted else "first_pass" if accepted else "unresolved"}


def evidence(_root, _run, item):
    return {"coverage": "complete", "source": "runtime_diagnostics", "tool_calls": 2,
        "tool_errors": 1, "required_action": {"status": "not_applicable", "name": None}}


def build(rows, *, terminal=False, reader=evidence):
    from hypoloom.observer_reliability import build_reliability
    return build_reliability("/unused", "r", rows, run_terminal=terminal, evidence_reader=reader)


def test_summary_exposes_shared_reliability_metrics_and_expert_subset(tmp_path):
    summary = _summary(tmp_path, attempts=[row("a", correction=correction())])
    assert summary["reliability"]["metrics"]["output_first_pass"]["numerator"] == 1
    assert summary["experts"][0]["reliability"]["counts"]["agent_attempts"] == 1


def test_same_call_repair_does_not_count_as_host_retry():
    result = build([row("a", correction=correction(2))])
    metrics = result["metrics"]
    assert metrics["output_first_pass"]["rate"] == 0
    assert metrics["correction_recovery"]["rate"] == 1
    assert metrics["node_retry"]["rate"] == 0
    assert metrics["node_delivery"]["rate"] == 1
    assert result["costs"]["retry_attempts"] == 0
    assert result["costs"]["correction_cost_usd"] is None


def test_stop_success_is_not_host_delivery_or_recovery():
    result = build([row("a", reason="FAILED", correction=correction(1))], terminal=True)
    assert result["metrics"]["correction_recovery"]["numerator"] == 0
    assert result["metrics"]["node_delivery"]["rate"] == 0


def test_retries_group_by_command_and_workflow_and_preserve_timeline():
    rows = [row("first", reason="FAILED", correction=correction(1, accepted=False)),
        row("second", ordinal=2, start=30, end=45, correction=correction())]
    result = build(rows)
    assert len(result["nodes"]) == 1
    assert result["nodes"][0]["attempt_ids"] == ["first", "second"]
    assert result["metrics"]["node_retry"]["rate"] == 1
    assert result["metrics"]["output_first_pass"]["denominator"] == 2
    assert result["costs"]["retry_reported_usd"] == "0.5"
    assert result["costs"]["retry_reported_tokens"] == 10
    assert result["costs"]["retry_reserved_tokens"] == 0
    assert result["costs"]["retry_elapsed_seconds"] == 15
    peer = row("peer")
    peer["workflow_id"] = "other"
    assert len(build(rows + [peer])["nodes"]) == 2


def test_failed_closed_attempt_is_pending_until_run_ends():
    value = row("first", reason="FAILED", correction=correction(1, accepted=False))
    active = build([value])
    assert active["nodes"][0]["status"] == "pending"
    assert active["metrics"]["node_delivery"]["rate"] is None
    assert active["metrics"]["node_delivery"]["unknown"] == 1
    assert build([value], terminal=True)["metrics"]["node_delivery"]["rate"] == 0


def test_open_and_cancelled_are_not_failed_quality_samples():
    result = build([row("open", command="open", state="OPEN", correction=correction(1)),
        row("cancel", command="cancel", reason="CANCELLED", correction=correction(1))])
    assert result["metrics"]["output_first_pass"] == {
        "numerator": 0, "denominator": 0, "rate": None, "unknown": 1, "excluded": 1}
    assert result["counts"]["open_attempts"] == 1
    assert result["counts"]["cancelled_attempts"] == 1


def test_absent_old_correction_evidence_is_unknown_not_first_pass():
    result = build([row("old")])
    assert result["metrics"]["output_first_pass"]["unknown"] == 1
    assert result["metrics"]["output_first_pass"]["rate"] is None
    assert result["metrics"]["node_delivery"]["rate"] == 1


@pytest.mark.parametrize("bad", [{}, {"rejected_count": True}, {"rejected_count": -1},
    {"rejected_count": 0, "accepted": True, "rounds": []},
    {"rejected_count": 0, "accepted": True, "call_count": 2, "rounds": [{"status": "rejected"}]}])
def test_malformed_corrections_are_unknown(bad):
    assert build([row("bad", correction=bad)])["metrics"]["output_first_pass"]["rate"] is None


def test_timeout_routing_is_not_causal_model_or_provider_verdict():
    value = row("timeout", reason="TIMED_OUT", correction=correction(2, accepted=False), cost=None)
    obs = json.loads(value["observation_json"])
    obs.update(failure_code="timed_out", failure_stage="provider_process", retry_class="INFRASTRUCTURE")
    value["observation_json"] = json.dumps(obs)
    attempt = build([value], terminal=True)["nodes"][0]["attempts"][0]
    assert attempt["retry_class"] == "INFRASTRUCTURE"
    assert attempt["attribution"]["category"] == "undetermined"
    assert "output_rejected" in {item["kind"] for item in attempt["observations"]}


def test_suspected_output_attribution_is_not_confirmed_intelligence_score():
    value = row("bad", reason="FAILED", correction=correction(1, accepted=False))
    obs = json.loads(value["observation_json"])
    obs.update(failure_code="invalid_candidate", failure_stage="host_candidate_validation", retry_class="MODEL")
    value["observation_json"] = json.dumps(obs)
    attribution = build([value])["nodes"][0]["attempts"][0]["attribution"]
    assert attribution["category"] == "model_output"
    assert attribution["confidence"] == "suspected"


def test_unknown_retry_cost_preserves_existing_reserve_not_partial_cli_bill():
    result = build([row("a", reason="FAILED"), row("b", ordinal=2, reason="TIMED_OUT", cost=None)])
    assert result["costs"]["retry_reported_usd"] == "0"
    assert result["costs"]["retry_reserved_usd"] == "2"
    assert result["costs"]["retry_unknown_closed_attempts"] == 1
    assert result["costs"]["retry_reported_tokens"] == 0
    assert result["costs"]["retry_reserved_tokens"] == 100


def test_unknown_ordinals_do_not_invent_zero_retries():
    value = row("legacy")
    value.pop("attempt_ordinal")
    result = build([value])
    assert result["metrics"]["node_retry"]["unknown"] == 1
    assert result["costs"]["retry_unknown_nodes"] == 1


def test_model_role_and_node_grouping_exposes_mixed_model_nodes():
    rows = [row("a", reason="FAILED"), row("b", ordinal=2, model="model-b"),
        row("c", command="other", role="other", node="GROUNDING")]
    result = build(rows)
    assert len(result["groups"]) == 2
    mixed = next(item for item in result["groups"] if item["role"] == "scientist")
    assert mixed["model"].startswith("mixed:")
    assert mixed["metrics"]["node_retry"]["rate"] == 1
    assert {item["model"] for item in result["nodes"][0]["attempts"]} == {"model-a", "model-b"}


def test_required_action_misses_do_not_rewrite_succeeded_attempts():
    def traces(root, run, item):
        return {**evidence(root, run, item), "required_action": {
            "status": "missed" if item["attempt_id"] == "missed" else "met", "name": "resource-planning"}}
    rows = [row("missed", command="one"), row("met", command="two")]
    before = json.dumps(rows)
    result = build(rows, reader=traces)
    assert result["metrics"]["required_actions"]["rate"] == .5
    assert result["metrics"]["node_delivery"]["rate"] == 1
    assert all(item["status"] == "delivered" for item in result["nodes"])
    assert json.dumps(rows) == before


def test_partial_trace_tool_error_counts_are_observations_not_model_failures():
    def traces(root, run, item):
        return {**evidence(root, run, item), "coverage": "partial",
            "required_action": {"status": "unknown"}}
    result = build([row("ok")], reader=traces)
    assert result["counts"]["tool_errors"] == 1
    assert result["counts"]["trace_unknown_attempts"] == 1
    assert result["metrics"]["required_actions"]["unknown"] == 1
    assert result["nodes"][0]["attempts"][0]["attribution"]["category"] == "undetermined"


def test_process_review_other_run_and_scientific_results_do_not_pollute_counts():
    rows = [row("agent", correction=correction()),
        {**row("process", reason="FAILED"), "kind": "PROCESS"},
        {**row("review", reason="FAILED"), "kind": "REVIEW"},
        {**row("foreign", reason="FAILED"), "run_id": "other"}]
    assert build(rows)["counts"]["agent_attempts"] == 1


def test_terminal_run_summary_uses_transitions_to_close_delivery_samples(tmp_path):
    result = _summary(tmp_path, attempts=[row("failed", reason="FAILED")],
        transitions=[_transition(1, state="FAILED")])["reliability"]
    assert result["metrics"]["node_delivery"]["rate"] == 0


def test_goal_stop_censors_unfinished_nodes_not_the_later_final_report(tmp_path):
    result = _summary(tmp_path, attempts=[row("old", command="design", reason="FAILED"),
        row("report", command="report", node="FINAL_REPORT", reason="FAILED", start=40)],
        facts=[_fact("GoalVerdictRecorded", "verdict", {"verdict": "GOAL_MET"}, 30)],
        transitions=[_transition(1, state="COMPLETED", at=60)])["reliability"]
    assert result["metrics"]["node_delivery"] == {
        "numerator": 0, "denominator": 1, "rate": 0, "unknown": 0, "excluded": 1}


def test_run_cancellation_does_not_promote_a_previously_failed_attempt_to_final_failure(tmp_path):
    result = _summary(tmp_path, attempts=[row("old", reason="FAILED")],
        transitions=[_transition(1, state="CANCELLED", at=40)])["reliability"]
    assert result["metrics"]["node_delivery"]["excluded"] == 1


def test_legacy_structured_tool_feedback_is_separate_from_host_acceptance():
    report = {"scope": "cc_tool_observation", "call_count": 2, "rejected_count": 1,
        "status": "recovered", "unknown_count": 0, "truncated": False,
        "rounds": [{"status": "rejected"}, {"status": "tool_succeeded"}]}
    result = build([row("old", reason="FAILED", correction=report)], terminal=True)
    assert result["metrics"]["output_first_pass"]["rate"] == 0
    assert result["metrics"]["correction_recovery"]["rate"] == 0
