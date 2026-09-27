from __future__ import annotations

import base64
import json
from dataclasses import replace

import pytest

from hypoloom.agent_aci import CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION
from hypoloom.expert_loop import (
    AuxiliaryRequestKind,
    AuxiliaryTask,
    AuxiliaryTaskView,
    DerivedWorkState,
    ExpertLineage,
    ExpertLineageInput,
    HypothesisUpdateDisposition,
    ResearchNodeKey,
    ResearchNodeKind,
    ResearchNodeSubkind,
    ResearchNodeTask,
    ResearchNodeView,
    ScientificNodeRefs,
    derive_research_node_views,
    reduce_expert_lineage,
    reduce_expert_lineages,
    task_context_from_dict,
)


def node_key(**changes) -> ResearchNodeKey:
    values = dict(
        run_id="run-1", component_id="primary.score", expert_slot="scientist",
        cycle=1, node_kind=ResearchNodeKind.LITERATURE_REVIEW,
        subkind=ResearchNodeSubkind.MAIN,
    )
    values.update(changes)
    return ResearchNodeKey(**values)


def test_closed_node_vocabulary_and_stable_key_exclude_attempt_identity() -> None:
    assert tuple(item.value for item in ResearchNodeKind) == (
        "LITERATURE_REVIEW", "HYPOTHESIS_PROPOSAL", "EXPERIMENT_DESIGN_IMPLEMENT",
        "EXPERIMENT_EXECUTION", "RESULT_ANALYSIS", "HYPOTHESIS_UPDATE",
        "CYCLE_REPORT",
        "ADJUDICATE",
        "SELF_EVOLVE",
    )
    key = node_key()
    assert ResearchNodeKey.from_dict(key.to_dict()) == key
    assert key.command_id == node_key().command_id
    legacy_identity = key.to_dict()
    legacy_identity.pop("experiment_ordinal")
    encoded = base64.urlsafe_b64encode(
        json.dumps(
            legacy_identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).decode("ascii").rstrip("=")
    assert key.command_id == f"research-node-v1.{encoded}"
    assert "attempt" not in key.to_dict()
    assert replace(key, cycle=2).command_id != key.command_id


def test_experiment_bound_key_requires_and_serializes_positive_ordinal() -> None:
    experiment_nodes = (
        ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT,
        ResearchNodeKind.EXPERIMENT_EXECUTION,
        ResearchNodeKind.RESULT_ANALYSIS,
    )
    for kind in experiment_nodes:
        with pytest.raises(ValueError, match="experiment_ordinal"):
            node_key(node_kind=kind)
        key = node_key(node_kind=kind, experiment_ordinal=2)
        assert key.experiment_ordinal == 2
        assert ResearchNodeKey.from_dict(key.to_dict()) == key
        assert replace(key, experiment_ordinal=3).command_id != key.command_id

    for invalid in (False, 0, -1):
        with pytest.raises(ValueError, match="experiment_ordinal"):
            node_key(
                node_kind=ResearchNodeKind.EXPERIMENT_EXECUTION,
                experiment_ordinal=invalid,
            )
    with pytest.raises(ValueError, match="only experiment-bound"):
        node_key(experiment_ordinal=1)

    missing = node_key().to_dict()
    del missing["experiment_ordinal"]
    with pytest.raises(ValueError, match="fields are not exact"):
        ResearchNodeKey.from_dict(missing)


def test_only_literature_and_proposal_have_specialized_subkinds() -> None:
    assert node_key(subkind=ResearchNodeSubkind.QUERY_PLAN).subkind is (
        ResearchNodeSubkind.QUERY_PLAN
    )
    for subkind in (
        ResearchNodeSubkind.FORWARD,
        ResearchNodeSubkind.BACKWARD,
        ResearchNodeSubkind.SYNTHESIS,
    ):
        assert node_key(
            node_kind=ResearchNodeKind.HYPOTHESIS_PROPOSAL,
            subkind=subkind,
        ).subkind is subkind
    with pytest.raises(ValueError, match="does not use"):
        node_key(subkind=ResearchNodeSubkind.FORWARD)
    with pytest.raises(ValueError, match="does not use"):
        node_key(node_kind=ResearchNodeKind.HYPOTHESIS_PROPOSAL)
    with pytest.raises(ValueError, match="does not use"):
        node_key(
            node_kind=ResearchNodeKind.RESULT_ANALYSIS,
            subkind=ResearchNodeSubkind.QUERY_PLAN,
            experiment_ordinal=1,
        )


def test_task_context_round_trip_and_role_neutral_grounding() -> None:
    task = ResearchNodeTask(key=node_key())
    assert task_context_from_dict(task.to_dict()) == task
    grounding = AuxiliaryTask(kind=AuxiliaryRequestKind.GROUNDING, run_id="run-1")
    assert task_context_from_dict(grounding.to_dict()) == grounding
    query_plan = AuxiliaryTask(
        kind=AuxiliaryRequestKind.GROUNDING_QUERY_PLAN, run_id="run-1",
    )
    assert task_context_from_dict(query_plan.to_dict()) == query_plan
    for kind in (
        AuxiliaryRequestKind.GROUNDING_QUERY_PLAN,
        AuxiliaryRequestKind.GROUNDING,
    ):
        with pytest.raises(ValueError, match="role-neutral"):
            AuxiliaryTask(
                kind=kind, run_id="run-1", expert_slot="scientist",
            )


def test_projection_reuses_one_command_across_lost_retry_and_success() -> None:
    key = node_key()
    commands = ({"command_id": key.command_id},)
    lost = ({
        "command_id": key.command_id, "attempt_id": "attempt-1",
        "attempt_ordinal": 1, "state": "CLOSED", "terminal_reason": "LOST",
    },)
    (ready,) = derive_research_node_views((key,), commands, lost)
    assert ready.state is DerivedWorkState.READY
    assert ready.command_id == key.command_id
    open_retry = (*lost, {
        "command_id": key.command_id, "attempt_id": "attempt-2",
        "attempt_ordinal": 2, "state": "OPEN", "terminal_reason": None,
    })
    (opened,) = derive_research_node_views((key,), commands, open_retry)
    assert opened.state is DerivedWorkState.OPEN
    succeeded = (*lost, {
        "command_id": key.command_id, "attempt_id": "attempt-2",
        "attempt_ordinal": 2, "state": "CLOSED", "terminal_reason": "SUCCEEDED",
    })
    (closed,) = derive_research_node_views((key,), commands, reversed(succeeded))
    assert closed.state is DerivedWorkState.CLOSED
    assert closed.attempt_ids == ("attempt-1", "attempt-2")


def test_projection_is_empty_for_legacy_runs_and_additively_maps_existing_refs() -> None:
    key = node_key()
    command = ({"command_id": key.command_id},)
    assert derive_research_node_views((key,), command, (), agent_aci_version="6") == ()
    attempt = ({
        "command_id": key.command_id, "attempt_id": "attempt-1",
        "attempt_ordinal": 1, "state": "CLOSED", "terminal_reason": "SUCCEEDED",
    },)
    facts = ({"fact_id": "fact-1"},)
    transitions = ({
        "fact_id": "fact-1", "payload": {"node_command_id": key.command_id},
    },)
    artifacts = ({"artifact_id": "artifact-1", "attempt_id": "attempt-1"},)
    (view,) = derive_research_node_views((key,), command, attempt, transitions, facts, artifacts)
    assert view.fact_ids == ("fact-1",)
    assert view.artifact_ids == ("artifact-1",)
    assert derive_research_node_views(
        (key,), command, attempt, agent_aci_version="7"
    )[0].state is DerivedWorkState.CLOSED
    assert derive_research_node_views(
        (key,), command, attempt, agent_aci_version="8"
    )[0].state is DerivedWorkState.CLOSED
    assert derive_research_node_views(
        (key,), command, attempt, agent_aci_version="9"
    )[0].state is DerivedWorkState.CLOSED
    for version in (CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION, str(CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION)):
        assert derive_research_node_views(
            (key,), command, attempt, agent_aci_version=version
        )[0].state is DerivedWorkState.CLOSED
    assert derive_research_node_views(
        (key,), command, attempt, agent_aci_version=CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION + 1
    ) == ()


def test_projection_rejects_missing_command_and_noncontiguous_attempts() -> None:
    key = node_key()
    attempt = {
        "command_id": key.command_id, "attempt_id": "attempt-2",
        "attempt_ordinal": 2, "state": "CLOSED", "terminal_reason": "FAILED",
    }
    with pytest.raises(ValueError, match="not contiguous"):
        derive_research_node_views((key,), ({"command_id": key.command_id},), (attempt,))
    with pytest.raises(ValueError, match="absent command"):
        derive_research_node_views((key,), (), ({**attempt, "attempt_ordinal": 1},))


NODE_ORDER = (
    (ResearchNodeKind.LITERATURE_REVIEW, ResearchNodeSubkind.QUERY_PLAN),
    (ResearchNodeKind.LITERATURE_REVIEW, ResearchNodeSubkind.MAIN),
    (ResearchNodeKind.HYPOTHESIS_PROPOSAL, ResearchNodeSubkind.FORWARD),
    (ResearchNodeKind.HYPOTHESIS_PROPOSAL, ResearchNodeSubkind.BACKWARD),
    (ResearchNodeKind.HYPOTHESIS_PROPOSAL, ResearchNodeSubkind.SYNTHESIS),
    (ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT, ResearchNodeSubkind.MAIN),
    (ResearchNodeKind.EXPERIMENT_EXECUTION, ResearchNodeSubkind.MAIN),
    (ResearchNodeKind.RESULT_ANALYSIS, ResearchNodeSubkind.MAIN),
    (ResearchNodeKind.HYPOTHESIS_UPDATE, ResearchNodeSubkind.MAIN),
)


def lineage(slot: str = "scientist", **changes) -> ExpertLineage:
    values = {
        "run_id": "run-loop",
        "component_id": "primary.score",
        "expert_slot": slot,
    }
    values.update(changes)
    return ExpertLineage(**values)


def grounding(state: DerivedWorkState = DerivedWorkState.CLOSED) -> AuxiliaryTaskView:
    return AuxiliaryTaskView(
        task=AuxiliaryTask(
            kind=AuxiliaryRequestKind.GROUNDING,
            run_id="run-loop",
        ),
        state=state,
        attempt_ids=("grounding-attempt",),
        artifact_ids=("grounding-packet",) if state is DerivedWorkState.CLOSED else (),
    )


def closed_node(
    active: ExpertLineage,
    node_kind: ResearchNodeKind,
    subkind: ResearchNodeSubkind,
    experiment_ordinal: int | None = None,
) -> ResearchNodeView:
    key = active.key(
        node_kind,
        subkind,
        experiment_ordinal=experiment_ordinal,
    )
    return ResearchNodeView(
        key=key,
        command_id=key.command_id,
        state=DerivedWorkState.CLOSED,
        attempt_ids=(f"{key.command_id}-attempt",),
        artifact_ids=(f"{key.command_id}-artifact",),
    )


def completed_prefix(
    active: ExpertLineage,
    length: int,
    *,
    experiment_ordinal: int = 1,
) -> tuple[ResearchNodeView, ...]:
    return tuple(
        closed_node(
            active,
            *node,
            experiment_ordinal=(
                experiment_ordinal
                if node[0]
                in {
                    ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT,
                    ResearchNodeKind.EXPERIMENT_EXECUTION,
                    ResearchNodeKind.RESULT_ANALYSIS,
                }
                else None
            ),
        )
        for node in NODE_ORDER[:length]
    )


def science_for_prefix(
    length: int,
    disposition: HypothesisUpdateDisposition | None = None,
    *,
    hypothesis_id: str = "hypothesis-current",
) -> ScientificNodeRefs:
    return ScientificNodeRefs(
        hypothesis_id=hypothesis_id if length >= 5 else None,
        experiment_id="experiment-current" if length >= 6 else None,
        experiment_ordinal=1 if length >= 6 else None,
        outcome_id="outcome-current" if length >= 7 else None,
        assessment_id="assessment-current" if length >= 8 else None,
        update_disposition=disposition if length >= 9 else None,
    )


def test_reducer_orders_grounding_and_every_node_including_both_pincer_wings() -> None:
    active = lineage()
    item = ExpertLineageInput(lineage=active)
    before_grounding = reduce_expert_lineage(item, (), None)
    assert isinstance(before_grounding.next_task, AuxiliaryTask)
    assert before_grounding.next_task.kind is AuxiliaryRequestKind.GROUNDING

    for completed in range(len(NODE_ORDER)):
        item = ExpertLineageInput(
            lineage=active,
            scientific_refs=science_for_prefix(completed),
        )
        decision = reduce_expert_lineage(
            item,
            reversed(completed_prefix(active, completed)),
            grounding(),
        )
        assert isinstance(decision.next_task, ResearchNodeTask)
        expected_kind, expected_subkind = NODE_ORDER[completed]
        assert decision.next_task.key == active.key(
            expected_kind,
            expected_subkind,
            experiment_ordinal=(
                1
                if expected_kind
                in {
                    ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT,
                    ResearchNodeKind.EXPERIMENT_EXECUTION,
                    ResearchNodeKind.RESULT_ANALYSIS,
                }
                else None
            ),
        )


def test_scientific_experiment_identity_requires_id_and_ordinal_together() -> None:
    with pytest.raises(ValueError, match="same presence"):
        ScientificNodeRefs(hypothesis_id="hypothesis", experiment_id="experiment")
    with pytest.raises(ValueError, match="same presence"):
        ScientificNodeRefs(hypothesis_id="hypothesis", experiment_ordinal=1)
    with pytest.raises(ValueError, match="positive integer"):
        ScientificNodeRefs(
            hypothesis_id="hypothesis",
            experiment_id="experiment",
            experiment_ordinal=0,
        )


def test_reducer_binds_each_experiment_revision_to_a_distinct_node_identity() -> None:
    active = lineage()
    pre_experiment = completed_prefix(active, 5)
    first = reduce_expert_lineage(
        ExpertLineageInput(
            lineage=active,
            scientific_refs=ScientificNodeRefs(hypothesis_id="hypothesis"),
        ),
        pre_experiment,
        grounding(),
    )
    assert isinstance(first.next_task, ResearchNodeTask)
    assert first.next_task.key.node_kind is ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT
    assert first.next_task.key.experiment_ordinal == 1

    old_design = closed_node(
        active,
        ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT,
        ResearchNodeSubkind.MAIN,
        experiment_ordinal=1,
    )
    revision_two = ScientificNodeRefs(
        hypothesis_id="hypothesis",
        experiment_id="experiment-2",
        experiment_ordinal=2,
    )
    redesigned = reduce_expert_lineage(
        ExpertLineageInput(lineage=active, scientific_refs=revision_two),
        (*pre_experiment, old_design),
        grounding(),
    )
    assert isinstance(redesigned.next_task, ResearchNodeTask)
    assert redesigned.next_task.key.node_kind is ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT
    assert redesigned.next_task.key.experiment_ordinal == 2
    assert redesigned.next_task.key.command_id != old_design.command_id

    current_design = closed_node(
        active,
        ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT,
        ResearchNodeSubkind.MAIN,
        experiment_ordinal=2,
    )
    execution = reduce_expert_lineage(
        ExpertLineageInput(lineage=active, scientific_refs=revision_two),
        (*pre_experiment, old_design, current_design),
        grounding(),
    )
    assert isinstance(execution.next_task, ResearchNodeTask)
    assert execution.next_task.key.node_kind is ResearchNodeKind.EXPERIMENT_EXECUTION
    assert execution.next_task.key.experiment_ordinal == 2


def test_projection_sorts_experiment_revisions_by_ordinal() -> None:
    first = node_key(
        node_kind=ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT,
        experiment_ordinal=1,
    )
    second = replace(first, experiment_ordinal=2)
    views = derive_research_node_views(
        (second, first),
        ({"command_id": first.command_id}, {"command_id": second.command_id}),
        (),
    )
    assert [view.key.experiment_ordinal for view in views] == [1, 2]


def test_failed_attempt_retries_the_same_stable_node() -> None:
    active = lineage()
    key = active.key(
        ResearchNodeKind.LITERATURE_REVIEW,
        ResearchNodeSubkind.QUERY_PLAN,
    )
    failed = ResearchNodeView(
        key=key,
        command_id=key.command_id,
        state=DerivedWorkState.READY,
        attempt_ids=("failed-attempt",),
    )
    decision = reduce_expert_lineage(
        ExpertLineageInput(lineage=active),
        (failed,),
        grounding(),
    )
    assert decision.task_state is DerivedWorkState.READY
    assert decision.next_task == ResearchNodeTask(key=key)
    assert decision.next_task.command_id == key.command_id


def test_missing_backward_wing_cannot_be_hidden_by_completed_synthesis() -> None:
    active = lineage()
    query_plan, literature, forward, backward, synthesis = completed_prefix(active, 5)
    decision = reduce_expert_lineage(
        ExpertLineageInput(
            lineage=active,
            scientific_refs=ScientificNodeRefs(hypothesis_id="forged-early"),
        ),
        (synthesis, literature, query_plan, forward),
        grounding(),
    )
    assert isinstance(decision.next_task, ResearchNodeTask)
    assert decision.next_task.key == backward.key
    assert decision.task_state is DerivedWorkState.READY


def test_two_lineages_advance_independently_and_ignore_input_order() -> None:
    alpha = lineage("alpha")
    beta = lineage("beta")
    alpha_literature = completed_prefix(alpha, 2)
    inputs = (
        ExpertLineageInput(lineage=beta),
        ExpertLineageInput(lineage=alpha),
    )
    first = reduce_expert_lineages(
        inputs,
        alpha_literature,
        (grounding(),),
    )
    second = reduce_expert_lineages(
        reversed(inputs),
        reversed(alpha_literature),
        (grounding(),),
    )
    assert first == second
    assert [decision.source_lineage.expert_slot for decision in first] == [
        "alpha",
        "beta",
    ]
    assert [decision.next_task.key.subkind for decision in first] == [  # type: ignore[union-attr]
        ResearchNodeSubkind.FORWARD,
        ResearchNodeSubkind.QUERY_PLAN,
    ]


def test_revise_opens_cycle_two_with_exact_parent_and_terminate_stops_it() -> None:
    first_cycle = lineage()
    revised = reduce_expert_lineage(
        ExpertLineageInput(
            lineage=first_cycle,
            scientific_refs=science_for_prefix(
                9,
                HypothesisUpdateDisposition.REVISE_NEXT_CYCLE,
                hypothesis_id="hypothesis-cycle-1",
            ),
        ),
        reversed(completed_prefix(first_cycle, 9)),
        grounding(),
    )
    assert revised.terminated is False
    assert revised.next_lineage == lineage(
        cycle=2,
        parent_hypothesis_id="hypothesis-cycle-1",
    )
    assert isinstance(revised.next_task, ResearchNodeTask)
    assert revised.next_task.key.cycle == 2
    assert revised.next_task.key.node_kind is ResearchNodeKind.LITERATURE_REVIEW
    assert revised.next_task.key.subkind is ResearchNodeSubkind.QUERY_PLAN

    assert revised.next_lineage is not None
    second_cycle = revised.next_lineage
    terminated = reduce_expert_lineage(
        ExpertLineageInput(
            lineage=second_cycle,
            scientific_refs=science_for_prefix(
                9,
                HypothesisUpdateDisposition.TERMINATE_LINEAGE,
                hypothesis_id="hypothesis-cycle-2",
            ),
        ),
        completed_prefix(second_cycle, 9),
        grounding(),
    )
    assert terminated.terminated is True
    assert terminated.next_task is None
    assert terminated.next_lineage is None
