from dataclasses import replace

import pytest

from hypoloom.domain import HypothesisAssessment, ScientificOutcome
from hypoloom.policy import ResearchPolicy
from hypoloom.shared_acceptance import SharedAcceptance
from tests.runtime.test_component_policy import assessed, component, snapshot
from tests.runtime.test_shared_acceptance import acceptance_dict


def observation(*, alpha=".7", beta=".8", causal=True, source="grounding-1"):
    return {"source_attempt_id": source, "measurements": {"alpha": alpha, "beta": beta}, "checks": {"causal": causal}}


def grounded(*components, ready=True):
    return snapshot(*components, completion_mode="shared_goal", goal_evaluation_required=True,
        shared_acceptance=SharedAcceptance.from_dict(acceptance_dict()) if ready else None,
        shared_acceptance_source_attempt_id="grounding-1" if ready else None)


def test_local_supported_without_shared_result_is_not_goal_success_or_refutation():
    item = assessed(component("primary.a"), HypothesisAssessment.SUPPORTED)
    decision = ResearchPolicy().next_command(grounded(item))
    assert decision.command == "RECORD_GOAL_VERDICT"
    assert decision.payload["verdict"] == "INCONCLUSIVE"


def test_shared_success_does_not_require_local_support_and_stops_immediately():
    item = replace(assessed(component("primary.a"), HypothesisAssessment.REFUTED), goal_evaluation=observation())
    peer = component("primary.b")
    decision = ResearchPolicy().next_command(grounded(item, peer))
    assert decision.command == "RECORD_GOAL_VERDICT"
    assert decision.payload["verdict"] == "GOAL_MET"
    assert decision.payload["assessment_ids"] == [item.assessment_id]


def test_local_success_at_strict_shared_boundary_does_not_win():
    item = replace(assessed(component("primary.a"), HypothesisAssessment.SUPPORTED), goal_evaluation=observation(alpha=".6674"))
    decision = ResearchPolicy().next_command(grounded(item))
    assert decision.payload["verdict"] == "GOAL_NOT_MET"


def test_invalid_shared_evidence_is_not_scientific_goal_refutation():
    item = replace(assessed(component("primary.a"), HypothesisAssessment.REFUTED), goal_evaluation=observation(causal=False))
    assert ResearchPolicy().next_command(grounded(item)).payload["verdict"] == "INCONCLUSIVE"


def test_no_common_standard_blocks_dispatch_and_direct_submission():
    item = component("primary.a")
    view = grounded(item, ready=False)
    assert ResearchPolicy().next_command(view) is None
    with pytest.raises(ValueError, match="Grounding"):
        ResearchPolicy().admit_experiment_submission(view, item.component_id, None)


@pytest.mark.parametrize("exhaustion", ["grounding_failure", "budget"])
def test_unresolved_common_standard_can_close_exhaustion_without_claiming_science(exhaustion):
    item = component("primary.a")
    if exhaustion == "grounding_failure":
        item = replace(item, proposals=tuple(replace(proposal, final_failure=True)
            for proposal in item.proposals))
    view = replace(grounded(item, ready=False), budget_exhausted=exhaustion == "budget")
    policy = ResearchPolicy()
    decision = policy.next_command(view)
    assert decision is not None, "exhausted Grounding must not leave ACTIVE with no work"
    assert decision.command == "RECORD_GOAL_VERDICT"
    assert decision.payload == {"verdict": "INCONCLUSIVE", "assessment_ids": [],
        "component_ids": [], "method_family_id": None}
    assert policy.next_command(replace(view, has_goal_verdict=True)).command == "BEGIN_COMPLETION"
    with pytest.raises(ValueError, match="Grounding"):
        policy.admit_experiment_submission(view, item.component_id, None)


@pytest.mark.parametrize("blocker", ["effect", "peer", "human", "pause"])
def test_unresolved_common_standard_does_not_close_viable_or_inflight_work(blocker):
    item = component("primary.a")
    item = replace(item, proposals=tuple(replace(proposal, final_failure=True)
        for proposal in item.proposals))
    view = grounded(item, ready=False)
    if blocker == "effect":
        view = replace(view, live_effect_count=1)
    elif blocker == "peer":
        view = grounded(item, component("primary.b"), ready=False)
    elif blocker == "human":
        view = replace(view, run_state="WAITING_HUMAN")
    else:
        peer = component("primary.b")
        peer = replace(peer, proposals=tuple(replace(proposal, dispatch_paused=True)
            for proposal in peer.proposals))
        view = grounded(item, peer, ready=False)
    assert ResearchPolicy().next_command(view) is None


def test_shared_miss_does_not_stop_other_useful_expert_work():
    item = replace(assessed(component("primary.a"), HypothesisAssessment.SUPPORTED), goal_evaluation=observation(beta=".7"))
    peer = component("primary.b")
    decision = ResearchPolicy().next_command(grounded(item, peer))
    assert decision.command == "REQUEST_HYPOTHESIS_CANDIDATE"
    assert decision.payload["component_id"] == "primary.b"


def test_policy_never_accepts_cross_source_vector():
    item = replace(assessed(component("primary.a"), HypothesisAssessment.SUPPORTED), goal_evaluation=observation(source="wrong"))
    with pytest.raises(ValueError, match="source"):
        ResearchPolicy().next_command(grounded(item))


def test_legacy_shared_goal_still_uses_its_own_supported_rule():
    item = assessed(component("primary.a"), HypothesisAssessment.SUPPORTED)
    assert ResearchPolicy().next_command(snapshot(item, completion_mode="shared_goal")).payload["verdict"] == "GOAL_MET"


@pytest.mark.parametrize("reason", [ScientificOutcome.INTEGRITY_REJECTED, ScientificOutcome.CANCELLED])
def test_rejected_or_cancelled_evaluation_cannot_qualify_even_with_passing_numbers(reason):
    item = replace(assessed(component("primary.a"), HypothesisAssessment.INCONCLUSIVE),
        outcome=reason, goal_evaluation=observation())
    assert ResearchPolicy().next_command(grounded(item)).payload["verdict"] == "INCONCLUSIVE"
