from dataclasses import replace

import pytest

from hypoloom.domain import HypothesisAssessment as Assessment
from hypoloom.lifecycle import ExperimentState
from hypoloom.policy import ResearchPolicy
from test_component_policy import component, assessed, accepted, snapshot


def alternative(name, assessment, family):
    return assessed(component(name, tier="primary"), assessment, family=family)


def verdict(*components):
    decision = ResearchPolicy().next_command(snapshot(*components, completion_mode="shared_goal"))
    assert decision.command == "RECORD_GOAL_VERDICT"
    return decision.payload


def test_one_accepted_shared_goal_alternative_succeeds_despite_other_expert_failure():
    winner = alternative("one", Assessment.SUPPORTED, "method-one")
    failed = alternative("two", Assessment.INCONCLUSIVE, "method-two")
    assert verdict(winner, failed) == {"verdict": "GOAL_MET", "assessment_ids": ["a-one"],
        "component_ids": ["one"], "method_family_id": "method-one"}
    legacy = ResearchPolicy().next_command(snapshot(winner, failed))
    assert legacy.payload["verdict"] == "INCONCLUSIVE"


def test_multiple_supported_alternatives_use_stable_identity_not_cross_metric_ranking():
    first = alternative("alpha", Assessment.SUPPORTED, "family-alpha")
    second = alternative("beta", Assessment.SUPPORTED, "family-beta")
    assert verdict(second, first) == verdict(first, second)
    assert verdict(first, second)["assessment_ids"] == ["a-alpha"]


@pytest.mark.parametrize("other_family,expected_family", [("family-one", "family-one"), ("family-two", None)])
def test_goal_not_met_requires_all_actual_clean_refutations(other_family, expected_family):
    first = alternative("one", Assessment.REFUTED, "family-one")
    second = alternative("two", Assessment.REFUTED, other_family)
    actual = verdict(first, second)
    assert actual["verdict"] == "GOAL_NOT_MET"
    assert actual["assessment_ids"] == ["a-one", "a-two"]
    assert actual["method_family_id"] == expected_family


@pytest.mark.parametrize("other", ["operational_failure", "missing_assessment", "lineage_open"])
def test_unresolved_alternatives_are_not_a_scientific_goal_refutation(other):
    first = alternative("one", Assessment.REFUTED, "family-one")
    second = alternative("two", Assessment.REFUTED, "family-two")
    if other == "operational_failure": second = alternative("two", Assessment.INCONCLUSIVE, "family-two")
    elif other == "missing_assessment": second = replace(component("two", tier="primary"),
        creation_budget_exhausted=True, proposals=(replace(component("two").proposals[0], final_failure=True),))
    else: second = replace(second, expert_lineage_terminated=False)
    assert verdict(first, second)["verdict"] == "INCONCLUSIVE"


def test_shared_goal_met_begins_success_stop_before_peer_collection_or_batch_closure():
    winner = replace(alternative("one", Assessment.SUPPORTED, "method-one"),
        expert_lineage_terminated=False, batch_pending=True)
    pending = replace(accepted(component("two", tier="primary")), experiment_state=ExperimentState.EVALUATING)
    decision = ResearchPolicy().next_command(snapshot(winner, pending, completion_mode="shared_goal", live_effect_count=1))
    assert decision.command == "RECORD_GOAL_VERDICT"
    assert decision.payload["verdict"] == "GOAL_MET"
    assert decision.payload["assessment_ids"] == ["a-one"]


def test_explicit_signals_still_collect_peer_outcome_before_completion():
    winner = alternative("one", Assessment.SUPPORTED, "method-one")
    pending = replace(accepted(component("two", tier="primary")), experiment_state=ExperimentState.EVALUATING)
    decision = ResearchPolicy().next_command(snapshot(winner, pending, completion_mode="explicit_signals"))
    assert decision.command == "ACCEPT_OUTCOME" and decision.owner_id == "x-two"


def test_shared_goal_met_does_not_launch_another_unstarted_alternative():
    winner = alternative("one", Assessment.SUPPORTED, "method-one")
    assert verdict(winner, component("unstarted", tier="primary"))["verdict"] == "GOAL_MET"


def test_shared_goal_snapshot_rejects_unknown_completion_mode():
    with pytest.raises(ValueError, match="completion"):
        snapshot(component("one", tier="primary"), completion_mode="agent-selected")
