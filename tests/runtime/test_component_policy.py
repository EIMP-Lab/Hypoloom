from __future__ import annotations

from dataclasses import replace

from hypoloom.domain import GoalVerdict, HypothesisAssessment, ScientificOutcome
from hypoloom.lifecycle import ExperimentState, RunState
from hypoloom.policy import (
    POLICY_VERSION,
    ComponentSnapshot,
    PolicySnapshot,
    ProposalSnapshot,
    ResearchPolicy,
)


def component(
    component_id: str,
    *,
    tier: str = "must",
    owner: str = "ml",
    proposers: tuple[str, ...] = ("ml",),
    dependencies: tuple[str, ...] = (),
    input_dependencies: tuple[str, ...] = (),
) -> ComponentSnapshot:
    return ComponentSnapshot(
        component_id=component_id,
        tier=tier,
        owner_role=owner,
        proposals=tuple(ProposalSnapshot(role=role) for role in proposers),
        dependencies=dependencies,
        input_dependencies=input_dependencies,
    )


def snapshot(*components: ComponentSnapshot, **changes: object) -> PolicySnapshot:
    values = {
        "run_id": "run-1",
        "run_state": RunState.ACTIVE,
        "run_revision": 2,
        "roster": ("ml", "systems", "affective"),
        "components": tuple(components),
    }
    values.update(changes)
    return PolicySnapshot(**values)  # type: ignore[arg-type]


def accepted(item: ComponentSnapshot, *, family: str = "family-a") -> ComponentSnapshot:
    proposals = tuple(
        replace(proposal, hypothesis_id=f"h-{item.component_id}-{proposal.role}")
        for proposal in item.proposals
    )
    return replace(
        item,
        proposals=proposals,
        experiment_id=f"x-{item.component_id}",
        selected_hypothesis_id=next(
            proposal.hypothesis_id for proposal in proposals if proposal.role == item.owner_role
        ),
        method_family_id=family,
        experiment_revision=1,
        experiment_state=ExperimentState.DRAFTED,
    )


def assessed(
    item: ComponentSnapshot,
    assessment: HypothesisAssessment,
    *,
    outcome: ScientificOutcome | None = None,
    criterion_met: bool | None = None,
    family: str = "family-a",
) -> ComponentSnapshot:
    item = accepted(item, family=family)
    if outcome is None:
        outcome = ScientificOutcome.SUCCESS if assessment is HypothesisAssessment.SUPPORTED else (
            ScientificOutcome.CLEAN_REFUTATION
            if assessment is HypothesisAssessment.REFUTED
            else ScientificOutcome.EXECUTION_FAILURE
        )
    if criterion_met is None and outcome in {
        ScientificOutcome.SUCCESS,
        ScientificOutcome.CLEAN_REFUTATION,
    }:
        criterion_met = assessment is HypothesisAssessment.SUPPORTED
    return replace(
        item,
        experiment_revision=7,
        experiment_state=ExperimentState.TERMINAL,
        outcome=outcome,
        criterion_met=criterion_met,
        evidence_linked=True,
        assessment=assessment,
        assessment_id=f"a-{item.component_id}",
    )


def test_component_proposers_are_independent_and_owner_proposal_is_selected() -> None:
    policy = ResearchPolicy()
    item = component("must.search", proposers=("ml", "affective"))

    first = policy.next_command(snapshot(item))
    assert first is not None
    assert first.command == "REQUEST_HYPOTHESIS_CANDIDATE"
    assert first.payload == {"component_id": "must.search", "role": "ml"}

    item = replace(
        item,
        proposals=(
            ProposalSnapshot(role="ml", hypothesis_id="h-owner"),
            ProposalSnapshot(role="affective"),
        ),
    )
    second = policy.next_command(snapshot(item))
    assert second is not None
    assert second.payload == {"component_id": "must.search", "role": "affective"}

    item = replace(
        item,
        proposals=(
            ProposalSnapshot(role="ml", hypothesis_id="h-owner"),
            ProposalSnapshot(role="affective", hypothesis_id="h-peer"),
        ),
    )
    create = policy.next_command(snapshot(item))
    assert create is not None
    assert create.command == "CREATE_EXPERIMENT"
    assert create.payload["hypothesis_id"] == "h-owner"
    assert create.payload["role"] == "ml"


def test_busy_role_does_not_serialize_an_independent_role() -> None:
    policy = ResearchPolicy()
    first = component("must.first", owner="ml", proposers=("ml",))
    second = component("must.second", owner="systems", proposers=("systems",))

    decision = policy.next_command(
        snapshot(first, second, busy_roles=("ml",), live_effect_count=1)
    )

    assert decision is not None
    assert decision.payload == {"component_id": "must.second", "role": "systems"}


def test_role_experiment_budget_closes_only_that_component_path() -> None:
    policy = ResearchPolicy()
    exhausted = replace(component("must.exhausted"), creation_budget_exhausted=True)
    available = component("must.available", owner="systems", proposers=("systems",))

    decision = policy.next_command(snapshot(exhausted, available))
    assert decision is not None
    assert decision.command == "REQUEST_HYPOTHESIS_CANDIDATE"
    assert decision.payload == {"component_id": "must.available", "role": "systems"}

    terminal = policy.next_command(snapshot(exhausted))
    assert terminal is not None
    assert terminal.command == "RECORD_GOAL_VERDICT"
    assert terminal.payload["verdict"] == GoalVerdict.INCONCLUSIVE.value


def test_explicit_dependency_requires_support_but_input_dependency_accepts_clean_result() -> None:
    policy = ResearchPolicy()
    upstream = assessed(
        component("primary.final", tier="primary"),
        HypothesisAssessment.REFUTED,
    )
    explicit = component("must.explicit", dependencies=("primary.final",))
    input_consumer = component(
        "must.audit",
        owner="systems",
        proposers=("systems",),
        dependencies=("primary.final",),
        input_dependencies=("primary.final",),
    )

    decision = policy.next_command(snapshot(upstream, explicit, input_consumer))

    assert decision is not None
    assert decision.command == "REQUEST_HYPOTHESIS_CANDIDATE"
    assert decision.payload["component_id"] == "must.audit"

    blocked_only = policy.next_command(snapshot(upstream, explicit))
    assert blocked_only is not None
    assert blocked_only.command == "RECORD_GOAL_VERDICT"
    assert blocked_only.payload["verdict"] == GoalVerdict.INCONCLUSIVE.value


def test_dependency_waits_for_the_expert_lineage_to_terminate() -> None:
    policy = ResearchPolicy()
    upstream = replace(
        assessed(
            component("must.search"),
            HypothesisAssessment.SUPPORTED,
        ),
        expert_lineage_terminated=False,
    )
    downstream = component(
        "primary.confirm",
        tier="primary",
        dependencies=("must.search",),
    )

    blocked = policy.next_command(snapshot(upstream, downstream))

    assert blocked is not None
    assert blocked.command == "RECORD_GOAL_VERDICT"
    assert blocked.payload["verdict"] == GoalVerdict.INCONCLUSIVE.value

    released = policy.next_command(
        snapshot(replace(upstream, expert_lineage_terminated=True), downstream)
    )
    assert released is not None
    assert released.command == "REQUEST_HYPOTHESIS_CANDIDATE"
    assert released.payload["component_id"] == "primary.confirm"


def test_required_inputs_share_one_family_but_ordinary_dependencies_may_differ() -> None:
    policy = ResearchPolicy()
    first = assessed(
        component("must.first"),
        HypothesisAssessment.SUPPORTED,
        family="family-a",
    )
    second = assessed(
        component("must.second", owner="systems", proposers=("systems",)),
        HypothesisAssessment.SUPPORTED,
        family="family-b",
    )
    input_consumer = component(
        "primary.input-consumer",
        tier="primary",
        dependencies=("must.first", "must.second"),
        input_dependencies=("must.first", "must.second"),
    )
    ordinary_consumer = component(
        "primary.synthesis",
        tier="primary",
        owner="affective",
        proposers=("affective",),
        dependencies=("must.first", "must.second"),
    )

    decision = policy.next_command(
        snapshot(first, second, input_consumer, ordinary_consumer)
    )

    assert decision is not None
    assert decision.command == "REQUEST_HYPOTHESIS_CANDIDATE"
    assert decision.payload["component_id"] == "primary.synthesis"

    same_family = replace(second, method_family_id="family-a")
    input_only = policy.next_command(snapshot(first, same_family, input_consumer))
    assert input_only is not None
    assert input_only.command == "REQUEST_HYPOTHESIS_CANDIDATE"
    assert input_only.payload["component_id"] == "primary.input-consumer"


def test_goal_met_requires_every_required_expert_lineage_to_terminate() -> None:
    primary = assessed(
        component("primary.metric", tier="primary"),
        HypothesisAssessment.SUPPORTED,
    )
    must = replace(
        assessed(component("must.delivery"), HypothesisAssessment.SUPPORTED),
        expert_lineage_terminated=False,
    )

    decision = ResearchPolicy().next_command(snapshot(primary, must))

    assert decision is not None
    assert decision.command == "RECORD_GOAL_VERDICT"
    assert decision.policy_version == POLICY_VERSION == "component-conjunction/11"
    assert decision.payload["verdict"] == GoalVerdict.INCONCLUSIVE.value
    assert decision.payload["method_family_id"] == "family-a"


def test_goal_family_conjunction_uses_required_input_dataflow_not_search_roots() -> None:
    search_a = assessed(
        component("must.search-a"),
        HypothesisAssessment.SUPPORTED,
        family="search-family-a",
    )
    search_b = assessed(
        component("must.search-b", owner="systems", proposers=("systems",)),
        HypothesisAssessment.SUPPORTED,
        family="search-family-b",
    )
    implementation = assessed(
        component(
            "must.implementation",
            owner="affective",
            proposers=("affective",),
            dependencies=("must.search-a", "must.search-b"),
        ),
        HypothesisAssessment.SUPPORTED,
        family="selected-family",
    )
    primary = assessed(
        component(
            "primary.final",
            tier="primary",
            dependencies=("must.implementation",),
            input_dependencies=("must.implementation",),
        ),
        HypothesisAssessment.SUPPORTED,
        family="selected-family",
    )

    decision = ResearchPolicy().next_command(
        snapshot(search_a, search_b, implementation, primary)
    )

    assert decision is not None
    assert decision.command == "RECORD_GOAL_VERDICT"
    assert decision.payload["verdict"] == GoalVerdict.GOAL_MET.value
    assert decision.payload["method_family_id"] == "selected-family"


def test_cross_family_inconclusive_verdict_has_no_single_family_identity() -> None:
    search_a = assessed(
        component("must.search-a"),
        HypothesisAssessment.SUPPORTED,
        family="search-family-a",
    )
    search_b = assessed(
        component("must.search-b", owner="systems", proposers=("systems",)),
        HypothesisAssessment.SUPPORTED,
        family="search-family-b",
    )
    implementation = assessed(
        component(
            "must.implementation",
            owner="affective",
            proposers=("affective",),
            dependencies=("must.search-a", "must.search-b"),
        ),
        HypothesisAssessment.SUPPORTED,
        family="selected-family",
    )
    primary = replace(
        assessed(
            component(
                "primary.final",
                tier="primary",
                dependencies=("must.implementation",),
                input_dependencies=("must.implementation",),
            ),
            HypothesisAssessment.SUPPORTED,
            family="selected-family",
        ),
        expert_lineage_terminated=False,
    )

    decision = ResearchPolicy().next_command(
        snapshot(search_a, search_b, implementation, primary)
    )

    assert decision is not None
    assert decision.command == "RECORD_GOAL_VERDICT"
    assert decision.payload["verdict"] == GoalVerdict.INCONCLUSIVE.value
    assert set(decision.payload["assessment_ids"]) == {
        "a-must.search-a",
        "a-must.search-b",
        "a-must.implementation",
        "a-primary.final",
    }
    assert decision.payload["method_family_id"] is None


def test_experiment_lifecycle_commands_use_component_owner_and_identity() -> None:
    policy = ResearchPolicy()
    drafted = accepted(component("primary.metric", tier="primary"))
    request = policy.next_command(snapshot(drafted))
    assert request is not None
    assert request.command == "REQUEST_SPEC_CANDIDATE"
    assert request.owner_id == "x-primary.metric"
    assert request.payload["role"] == "ml"

    preregistered = replace(
        drafted,
        experiment_revision=3,
        experiment_state=ExperimentState.PREREGISTERED,
    )
    prepare = policy.next_command(snapshot(preregistered))
    assert prepare is not None and prepare.command == "PREPARE"

    ready = replace(preregistered, experiment_revision=4, experiment_state=ExperimentState.READY)
    execute = policy.next_command(snapshot(ready))
    assert execute is not None and execute.command == "EXECUTE"

    evaluating = replace(ready, experiment_revision=6, experiment_state=ExperimentState.EVALUATING)
    evaluate = policy.next_command(snapshot(evaluating))
    assert evaluate is not None and evaluate.command == "ACCEPT_OUTCOME"


def test_final_spec_failure_waits_for_the_in_flight_redraft() -> None:
    redrafting = replace(
        accepted(component("must.repair")),
        experiment_revision=4,
        spec_final_failure=True,
        effect_in_flight=True,
    )

    decision = ResearchPolicy().next_command(
        snapshot(redrafting, busy_roles=("ml",), live_effect_count=1)
    )

    assert decision is None


def test_outcome_is_linked_and_assessed_from_validator_criterion() -> None:
    policy = ResearchPolicy()
    terminal = replace(
        accepted(component("primary.metric", tier="primary")),
        experiment_revision=7,
        experiment_state=ExperimentState.TERMINAL,
        outcome=ScientificOutcome.CLEAN_REFUTATION,
        criterion_met=False,
    )
    link = policy.next_command(snapshot(terminal))
    assert link is not None and link.command == "LINK_EVIDENCE"

    assess = policy.next_command(snapshot(replace(terminal, evidence_linked=True)))
    assert assess is not None and assess.command == "RECORD_ASSESSMENT"
    assert assess.payload["assessment"] == HypothesisAssessment.REFUTED.value


def revisionable_failure() -> ComponentSnapshot:
    return replace(
        accepted(component("primary.metric", tier="primary")),
        experiment_revision=7,
        experiment_state=ExperimentState.TERMINAL,
        outcome=ScientificOutcome.EXECUTION_FAILURE,
        evidence_linked=True,
        revision_of_outcome_id="outcome-primary.metric-1",
        revision_tip=True,
    )


def test_linked_execution_failure_creates_successor_before_assessment() -> None:
    failed = revisionable_failure()

    decision = ResearchPolicy().next_command(snapshot(failed))

    assert decision is not None
    assert decision.command == "CREATE_EXPERIMENT"
    assert decision.owner_id == "run-1"
    assert decision.expected_revision == 2
    assert decision.payload == {
        "component_id": "primary.metric",
        "hypothesis_id": "h-primary.metric-ml",
        "role": "ml",
        "revision_of_outcome_id": "outcome-primary.metric-1",
    }


def test_failed_outcome_must_be_linked_and_be_the_chain_tip_for_revision() -> None:
    failed = revisionable_failure()
    unlinked = replace(failed, evidence_linked=False)
    link = ResearchPolicy().next_command(snapshot(unlinked))
    assert link is not None and link.command == "LINK_EVIDENCE"

    non_tip = ResearchPolicy().next_command(snapshot(replace(failed, revision_tip=False)))
    assert non_tip is not None and non_tip.command == "RECORD_ASSESSMENT"

    missing_predecessor = ResearchPolicy().next_command(
        snapshot(replace(failed, revision_of_outcome_id=None))
    )
    assert missing_predecessor is not None
    assert missing_predecessor.command == "RECORD_ASSESSMENT"


def test_successor_creation_respects_component_and_global_creation_budgets() -> None:
    failed = revisionable_failure()

    component_exhausted = ResearchPolicy().next_command(
        snapshot(replace(failed, creation_budget_exhausted=True))
    )
    assert component_exhausted is not None
    assert component_exhausted.command == "RECORD_ASSESSMENT"

    global_exhausted = ResearchPolicy().next_command(
        snapshot(failed, budget_exhausted=True)
    )
    assert global_exhausted is not None
    assert global_exhausted.command == "RECORD_ASSESSMENT"


def test_revision_circuit_breaker_trips_after_thirty_revisions_in_one_cycle() -> None:
    failed = revisionable_failure()

    at_limit = ResearchPolicy().next_command(
        snapshot(replace(failed, current_cycle_revisions=30))
    )
    assert at_limit is not None and at_limit.command == "CREATE_EXPERIMENT"
    assert at_limit.payload["revision_of_outcome_id"] == "outcome-primary.metric-1"

    tripped = ResearchPolicy().next_command(
        snapshot(replace(failed, current_cycle_revisions=31))
    )
    assert tripped is not None
    assert tripped.command == "RECORD_ASSESSMENT"
    assert tripped.payload["assessment"] == "INCONCLUSIVE"


def test_non_operational_terminal_outcomes_do_not_create_successors() -> None:
    failed = revisionable_failure()
    for outcome in (
        ScientificOutcome.SUCCESS,
        ScientificOutcome.CLEAN_REFUTATION,
        ScientificOutcome.INCONCLUSIVE,
        ScientificOutcome.CANCELLED,
    ):
        candidate = replace(
            failed,
            outcome=outcome,
            criterion_met=False if outcome in {
                ScientificOutcome.SUCCESS,
                ScientificOutcome.CLEAN_REFUTATION,
            } else None,
        )
        decision = ResearchPolicy().next_command(snapshot(candidate))
        assert decision is not None and decision.command == "RECORD_ASSESSMENT"

    # INTEGRITY_REJECTED without a whitelisted predecessor link stays
    # non-revisionable; the snapshot only sets revision_of_outcome_id for
    # REVISIONABLE_INTEGRITY_VALIDATION_CODES.
    rejected = replace(
        failed,
        outcome=ScientificOutcome.INTEGRITY_REJECTED,
        revision_of_outcome_id=None,
    )
    decision = ResearchPolicy().next_command(snapshot(rejected))
    assert decision is not None and decision.command == "RECORD_ASSESSMENT"


def test_whitelisted_integrity_rejection_creates_same_hypothesis_successor() -> None:
    rejected = replace(
        revisionable_failure(),
        outcome=ScientificOutcome.INTEGRITY_REJECTED,
    )

    decision = ResearchPolicy().next_command(snapshot(rejected))

    assert decision is not None
    assert decision.command == "CREATE_EXPERIMENT"
    assert decision.owner_id == "run-1"
    assert decision.payload == {
        "component_id": "primary.metric",
        "hypothesis_id": "h-primary.metric-ml",
        "role": "ml",
        "revision_of_outcome_id": "outcome-primary.metric-1",
    }


def test_required_conjunction_goal_met_and_optional_leaf_never_blocks() -> None:
    policy = ResearchPolicy()
    primary_a = assessed(component("primary.a", tier="primary"), HypothesisAssessment.SUPPORTED)
    primary_b = assessed(component("primary.b", tier="primary"), HypothesisAssessment.SUPPORTED)
    must = assessed(component("must.delivery"), HypothesisAssessment.SUPPORTED)
    optional = component("should.ablation", tier="should")

    decision = policy.next_command(snapshot(primary_a, primary_b, must, optional))

    assert decision is not None and decision.command == "RECORD_GOAL_VERDICT"
    assert decision.payload["verdict"] == GoalVerdict.GOAL_MET.value
    assert set(decision.payload["component_ids"]) == {
        "primary.a",
        "primary.b",
        "must.delivery",
    }


def test_one_primary_below_target_is_settled_negative_only_after_must_closure() -> None:
    policy = ResearchPolicy()
    primary_a = assessed(component("primary.a", tier="primary"), HypothesisAssessment.SUPPORTED)
    primary_b = assessed(component("primary.b", tier="primary"), HypothesisAssessment.REFUTED)
    must = assessed(component("must.delivery"), HypothesisAssessment.SUPPORTED)

    decision = policy.next_command(snapshot(primary_a, primary_b, must))
    assert decision is not None
    assert decision.payload["verdict"] == GoalVerdict.GOAL_NOT_MET.value

    incomplete = policy.next_command(
        snapshot(primary_a, primary_b, replace(must, assessment=HypothesisAssessment.INCONCLUSIVE))
    )
    assert incomplete is not None
    assert incomplete.payload["verdict"] == GoalVerdict.INCONCLUSIVE.value


def test_mixed_method_families_and_budget_exhaustion_are_inconclusive() -> None:
    policy = ResearchPolicy()
    a = assessed(component("primary.a", tier="primary"), HypothesisAssessment.SUPPORTED, family="a")
    b = assessed(component("primary.b", tier="primary"), HypothesisAssessment.SUPPORTED, family="b")
    mixed = policy.next_command(snapshot(a, b))
    assert mixed is not None
    assert mixed.payload["verdict"] == GoalVerdict.INCONCLUSIVE.value

    unfinished = component("must.unfinished")
    exhausted = policy.next_command(snapshot(unfinished, budget_exhausted=True))
    assert exhausted is not None
    assert exhausted.command == "RECORD_GOAL_VERDICT"
    assert exhausted.payload["verdict"] == GoalVerdict.INCONCLUSIVE.value


def test_live_effect_limit_blocks_new_dispatch_without_fabricating_a_verdict() -> None:
    policy = ResearchPolicy()
    item = replace(
        component("must.search"),
        proposals=(ProposalSnapshot(role="ml", effect_in_flight=True),),
    )
    assert policy.next_command(
        snapshot(item, busy_roles=("ml",), live_effect_count=3)
    ) is None


def test_rendezvous_blocks_only_its_component_while_an_independent_owner_advances() -> None:
    waiting = replace(
        accepted(component("must.waiting")),
        rendezvous_blocked=True,
    )
    independent = accepted(
        component("must.independent", owner="systems", proposers=("systems",))
    )

    decision = ResearchPolicy().next_command(snapshot(waiting, independent))

    assert decision is not None
    assert decision.command == "REQUEST_SPEC_CANDIDATE"
    assert decision.payload["component_id"] == "must.independent"
    assert decision.owner_id == "x-must.independent"


def test_answered_preflight_and_pending_analysis_use_the_existing_component_lifecycle() -> None:
    drafted = replace(
        accepted(component("must.reviewed")),
        spec_candidate_ready=True,
    )
    preregister = ResearchPolicy().next_command(snapshot(drafted))
    assert preregister is not None and preregister.command == "PREREGISTER"

    evaluating = replace(
        drafted,
        spec_candidate_ready=False,
        experiment_revision=6,
        experiment_state=ExperimentState.EVALUATING,
        rendezvous_blocked=True,
    )
    other = accepted(
        component("must.other", owner="systems", proposers=("systems",))
    )
    decision = ResearchPolicy().next_command(snapshot(evaluating, other))
    assert decision is not None
    assert decision.command == "REQUEST_SPEC_CANDIDATE"
    assert decision.payload["component_id"] == "must.other"
