from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypoloom.domain import EffectKind, ResearchFactType
from hypoloom.lifecycle import (
    Command,
    ExperimentState,
    Machine,
    RunState,
    TRANSITIONS,
    WORKFLOW_DEFINITION_VERSION,
    UnknownLifecycleValue,
    UnknownTransition,
    declared_states,
    declared_transitions,
    initial_states,
    legal_commands,
    lifecycle_definition,
    terminal_states,
    validate_transition,
)


def test_final_report_failure_is_run_control_not_a_scientific_integrity_fact() -> None:
    transition = validate_transition(Machine.RUN_WORKFLOW, RunState.ACTIVE, Command.FINAL_REPORT_FAILED, RunState.FAILED)
    assert transition.fact_type is None
    assert transition.external_effect is None


def test_exact_projection_and_state_budget_comes_from_registry() -> None:
    assert all(command.name == command.value for command in Command)
    counts = {machine: len(declared_states(machine)) for machine in Machine}
    assert counts == {
        Machine.RUN_WORKFLOW: 8,
        Machine.EXPERIMENT_WORKFLOW: 6,
        Machine.HUMAN_DECISION: 2,
        Machine.EFFECT_ATTEMPT: 2,
    }
    assert len(counts) == 4
    assert sum(counts.values()) == 18
    assert all(len(initial_states(machine)) == 1 for machine in Machine)
    assert {
        machine.value: tuple(state.value for state in declared_states(machine)) for machine in Machine
    } == {
        "RunWorkflow": (
            "CREATED", "ACTIVE", "WAITING_HUMAN", "COMPLETING", "STOPPING",
            "COMPLETED", "FAILED", "CANCELLED",
        ),
        "ExperimentWorkflow": (
            "DRAFTED", "PREREGISTERED", "READY", "EXECUTING", "EVALUATING", "TERMINAL",
        ),
        "HumanDecision": ("OPEN", "RESOLVED"),
        "EffectAttempt": ("OPEN", "CLOSED"),
    }


def test_exact_workflow_core_stays_within_the_accepted_physical_loc_budget() -> None:
    root = Path(__file__).resolve().parents[2]
    # ADR-0022 explicitly reallocates authority, including new session lifecycle.
    # Optional dispatch extraction is counted if introduced, never free space.
    # Provider auth cooldown (operator recovery command + durable dispatch gate)
    # added workflow core authority: workflow_host 4500→4550, lifecycle 550→500.
    # ACI-21 moves bounded view scheduling into the sole-writer Host. Transfer
    # 150 lines from StateStore (361 actual); the counted files and total
    # 27,550-line authority budget remain unchanged.
    # Resource recovery reuses transitions: transfer 200 lines from shared_gpu_host
    # to the Host (50) and the counted resource_recovery helper (150).
    # Institutional controls reuse the journal; count the extracted helper by
    # transferring 100 lines from workflow_host, preserving the same total.
    # API appointments extract provider recovery beside host_settings; transfer
    # 150 lines from its allocation so the new helper is counted, not free space.
    allocations = {"domain": 1100, "lifecycle": 500, "ports": 100,
        "state_store": 400, "research_kernel": 750, "workflow_host": 4650, "institutional_controls": 100,
        "control_protocol": 300, "recovery": 100, "policy": 650,
        "outcome_adapter": 850, "expert_loop": 1100,
        "expert_batch": 700, "agent_sessions": 1100,
        "expert_plugin": 700, "expert_memory": 700, "measurement_contract": 600, "profile_v4": 650,
        "agent_budget": 650, "participant_controls": 500, "expert_interaction": 650,
        "manager_context": 400, "interaction_host": 1200, "debate_host": 1200, "profile_permissions": 150,
        "session_archive": 400, "grounding_dialogue": 500,
        "shared_acceptance": 850, "shared_acceptance_host": 700,
        "compute_resources": 1200, "gpu_pool": 1100, "shared_gpu_host": 500, "resource_recovery": 150,
        "gpu_attribution": 800, "resource_experience": 700, "host_settings": 350, "provider_recovery": 150}
    paths = tuple(f"hypoloom/{name}.py" for name in allocations
        if name not in {"expert_batch", "expert_plugin", "expert_memory", "measurement_contract", "expert_interaction", "interaction_host", "debate_host", "profile_permissions"}
        or (root / f"hypoloom/{name}.py").exists())
    migrations = tuple(str(path.relative_to(root)) for path in
        sorted((root / "hypoloom/migrations").glob("*.sql")))
    paths += migrations
    counts = {relative: sum(1 for _ in (root / relative).open(encoding="utf-8")) for relative in paths}
    for relative, count in counts.items():
        if relative.endswith(".py"):
            assert count <= allocations[Path(relative).stem], counts
    assert sum(counts[path] for path in migrations) <= 350, counts
    assert sum(allocations.values()) + 350 == 27_550
    assert sum(counts.values()) <= sum(allocations.values()) + 350, counts


def test_every_declared_edge_validates_to_its_registry_row() -> None:
    for transition in TRANSITIONS:
        for next_state in transition.next_states:
            assert validate_transition(
                transition.machine, transition.current_state, transition.command, next_state
            ) is transition


def test_every_undeclared_state_command_next_state_combination_fails() -> None:
    declared = {
        (transition.machine, transition.current_state, transition.command, next_state)
        for transition in TRANSITIONS
        for next_state in transition.next_states
    }
    for machine in Machine:
        states = declared_states(machine)
        for current in (None, *states):
            for command in Command:
                for following in states:
                    edge = (machine, current, command, following)
                    if edge in declared:
                        continue
                    with pytest.raises(UnknownTransition):
                        validate_transition(machine, current, command, following)


@pytest.mark.parametrize(
    "args",
    [
        ("UnknownMachine", None, Command.CREATE_RUN, RunState.CREATED),
        (Machine.RUN_WORKFLOW, "UNKNOWN", Command.ACTIVATE, RunState.ACTIVE),
        (Machine.RUN_WORKFLOW, RunState.CREATED, "UNKNOWN", RunState.ACTIVE),
        (Machine.RUN_WORKFLOW, RunState.CREATED, Command.ACTIVATE, "UNKNOWN"),
        (Machine.RUN_WORKFLOW, ExperimentState.DRAFTED, Command.ACTIVATE, RunState.ACTIVE),
    ],
)
def test_unknown_or_cross_projection_values_fail_before_transition_lookup(args) -> None:
    with pytest.raises(UnknownLifecycleValue):
        validate_transition(*args)


def test_terminal_states_have_no_commands_and_are_derived_not_copied() -> None:
    for machine in Machine:
        for state in terminal_states(machine):
            assert legal_commands(machine, state) == ()


def test_all_transitions_name_guards_and_durable_work() -> None:
    assert all(transition.guard for transition in TRANSITIONS)
    assert all(transition.durable_effect for transition in TRANSITIONS)
    assert all(transition.effect == transition.durable_effect for transition in TRANSITIONS)
    assert all(transition.dispatches_external == transition.effect_required for transition in TRANSITIONS)
    assert {transition.fact_type for transition in TRANSITIONS if transition.fact_type} == set(
        ResearchFactType
    )
    fact_rows = [transition for transition in TRANSITIONS if transition.fact_type]
    assert len(fact_rows) == 7


def test_external_effect_requirements_are_declared_on_rows() -> None:
    effect_rows = [transition for transition in TRANSITIONS if transition.effect_required]
    assert [(row.machine, row.command, row.external_effect) for row in effect_rows] == [
        (Machine.RUN_WORKFLOW, Command.REQUEST_HYPOTHESIS_CANDIDATE, EffectKind.AGENT),
        (Machine.RUN_WORKFLOW, Command.REQUEST_RENDEZVOUS, EffectKind.AGENT),
        (Machine.RUN_WORKFLOW, Command.REQUEST_RESEARCH_NODE, EffectKind.AGENT),
        (Machine.EXPERIMENT_WORKFLOW, Command.REQUEST_RESEARCH_NODE, EffectKind.AGENT),
        (Machine.EXPERIMENT_WORKFLOW, Command.REQUEST_RESEARCH_NODE, EffectKind.AGENT),
        (Machine.RUN_WORKFLOW, Command.REQUEST_AUXILIARY, EffectKind.AGENT),
        (Machine.EXPERIMENT_WORKFLOW, Command.REQUEST_AUXILIARY, EffectKind.AGENT),
        (Machine.EXPERIMENT_WORKFLOW, Command.REQUEST_AUXILIARY, EffectKind.AGENT),
        (Machine.RUN_WORKFLOW, Command.REQUEST_AUXILIARY, EffectKind.AGENT),
        (Machine.EXPERIMENT_WORKFLOW, Command.REQUEST_SPEC_CANDIDATE, EffectKind.AGENT),
        (Machine.EXPERIMENT_WORKFLOW, Command.VALIDATE_SPEC_CANDIDATE, EffectKind.PROCESS),
        (Machine.EXPERIMENT_WORKFLOW, Command.REQUEST_RENDEZVOUS, EffectKind.AGENT),
        (Machine.EXPERIMENT_WORKFLOW, Command.EXECUTE, EffectKind.PROCESS),
        (Machine.EXPERIMENT_WORKFLOW, Command.OPEN_RETRY_ATTEMPT, EffectKind.PROCESS),
        (Machine.EXPERIMENT_WORKFLOW, Command.REQUEST_RENDEZVOUS, EffectKind.AGENT),
    ]


def test_drafted_candidate_validation_is_a_persisted_process_self_loop() -> None:
    validate = validate_transition(
        Machine.EXPERIMENT_WORKFLOW,
        ExperimentState.DRAFTED,
        Command.VALIDATE_SPEC_CANDIDATE,
        ExperimentState.DRAFTED,
    )
    observe = validate_transition(
        Machine.EXPERIMENT_WORKFLOW,
        ExperimentState.DRAFTED,
        Command.OBSERVE_SPEC_VALIDATION,
        ExperimentState.DRAFTED,
    )

    assert WORKFLOW_DEFINITION_VERSION == "hypoloom-lifecycle-v11"
    assert validate.external_effect is EffectKind.PROCESS
    assert validate.effect_required
    assert observe.external_effect is None
    assert not observe.effect_required


def test_registry_serialization_is_direct_and_complete() -> None:
    rendered = lifecycle_definition()
    assert json.loads(json.dumps(rendered, sort_keys=True)) == rendered
    assert rendered["transitions"] == [transition.as_dict() for transition in TRANSITIONS]
    assert [projection["machine"] for projection in rendered["projections"]] == [
        machine.value for machine in Machine
    ]
    for machine, projection in zip(Machine, rendered["projections"], strict=True):
        assert projection["states"] == [state.value for state in declared_states(machine)]
        assert projection["terminal_states"] == [state.value for state in terminal_states(machine)]


def test_guarded_same_command_alternatives_remain_distinct_rows() -> None:
    rows = [
        transition
        for transition in declared_transitions(Machine.EXPERIMENT_WORKFLOW)
        if transition.current_state is ExperimentState.DRAFTED and transition.command is Command.CANCEL
    ]
    assert len(rows) == 2
    assert {row.next_states for row in rows} == {
        (ExperimentState.DRAFTED,),
        (ExperimentState.TERMINAL,),
    }


def test_provider_retry_clear_is_an_operator_run_control_without_state_change() -> None:
    for state in (RunState.CREATED, RunState.ACTIVE):
        transition = validate_transition(Machine.RUN_WORKFLOW, state, Command.PROVIDER_RETRY_CLEAR, state)
        assert transition.fact_type is None
        assert transition.external_effect is None
    for state in (RunState.WAITING_HUMAN, RunState.COMPLETING, RunState.STOPPING,
                  RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED):
        with pytest.raises(UnknownTransition):
            validate_transition(Machine.RUN_WORKFLOW, state, Command.PROVIDER_RETRY_CLEAR, state)
