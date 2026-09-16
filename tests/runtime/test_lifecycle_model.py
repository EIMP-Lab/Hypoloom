from __future__ import annotations

import random

import pytest

from hypoloom.domain import FactId
from hypoloom.lifecycle import (
    Command,
    ExperimentState,
    Machine,
    RunState,
    TRANSITIONS,
    declared_states,
    declared_transitions,
    experiment_terminal_is_valid,
    initial_states,
    is_terminal,
    next_states,
    run_terminal_is_valid,
    terminal_states,
    validate_transition,
)


def reachable(machine: Machine, start) -> set:
    found = {start}
    changed = True
    while changed:
        changed = False
        for transition in declared_transitions(machine):
            if transition.current_state in found:
                before = len(found)
                found.update(transition.next_states)
                changed |= before != len(found)
    return found


def can_reach_any_terminal(machine: Machine, start) -> bool:
    return bool(reachable(machine, start) & set(terminal_states(machine)))


def test_every_state_is_reachable_and_can_reach_a_terminal_state() -> None:
    for machine in Machine:
        (start,) = initial_states(machine)
        assert reachable(machine, start) == set(declared_states(machine))
        assert all(can_reach_any_terminal(machine, state) for state in declared_states(machine))


def test_terminal_states_are_absorbing() -> None:
    for machine in Machine:
        for state in terminal_states(machine):
            assert is_terminal(machine, state)
            assert not any(
                transition.current_state is state for transition in declared_transitions(machine)
            )


def test_run_cancel_and_integrity_failure_enter_stopping_before_terminal() -> None:
    for state in (
        RunState.CREATED,
        RunState.ACTIVE,
        RunState.WAITING_HUMAN,
        RunState.COMPLETING,
    ):
        assert next_states(Machine.RUN_WORKFLOW, state, Command.CANCEL) == (RunState.STOPPING,)
        assert next_states(Machine.RUN_WORKFLOW, state, Command.FAIL_INTEGRITY) == (
            RunState.STOPPING,
        )
    assert next_states(Machine.RUN_WORKFLOW, RunState.STOPPING, Command.CANCEL) == (
        RunState.STOPPING,
    )
    assert next_states(Machine.RUN_WORKFLOW, RunState.STOPPING, Command.FINISH_STOP) == (
        RunState.FAILED,
        RunState.CANCELLED,
        RunState.COMPLETED,
    )


def test_stopping_only_dispatches_the_aci14_report_after_other_work_drains() -> None:
    rows = [
        transition
        for transition in declared_transitions(Machine.RUN_WORKFLOW)
        if transition.current_state is RunState.STOPPING
    ]
    assert rows
    effects = [transition for transition in rows if transition.effect_required]
    assert len(effects) == 1 and effects[0].command is Command.REQUEST_AUXILIARY
    assert effects[0].guard == "aci14_final_report_only_after_other_effects_drained"
    assert effects[0].next_states == (RunState.STOPPING,)


def test_every_nonterminal_experiment_phase_has_a_declared_cancel_route() -> None:
    assert ExperimentState.TERMINAL in next_states(
        Machine.EXPERIMENT_WORKFLOW, ExperimentState.DRAFTED, Command.CANCEL
    )
    for state in (ExperimentState.PREREGISTERED, ExperimentState.READY, ExperimentState.EVALUATING):
        assert next_states(Machine.EXPERIMENT_WORKFLOW, state, Command.CANCEL) == (
            ExperimentState.EVALUATING,
        )
        assert ExperimentState.TERMINAL in reachable(Machine.EXPERIMENT_WORKFLOW, state)
    assert next_states(
        Machine.EXPERIMENT_WORKFLOW, ExperimentState.EXECUTING, Command.REQUEST_CANCEL
    ) == (ExperimentState.EXECUTING,)
    assert ExperimentState.TERMINAL in reachable(
        Machine.EXPERIMENT_WORKFLOW, ExperimentState.EXECUTING
    )


def test_experiment_terminal_model_distinguishes_discard_from_outcome() -> None:
    assert experiment_terminal_is_valid(
        ExperimentState.TERMINAL,
        preregistered=False,
        outcome_fact_id=None,
        discarded_before_preregistration=True,
    )
    assert experiment_terminal_is_valid(
        ExperimentState.TERMINAL,
        preregistered=True,
        outcome_fact_id=FactId("outcome-fact"),
        discarded_before_preregistration=False,
    )
    invalid = (
        dict(preregistered=False, outcome_fact_id=FactId("outcome"), discarded_before_preregistration=True),
        dict(preregistered=True, outcome_fact_id=None, discarded_before_preregistration=False),
        dict(preregistered=True, outcome_fact_id=FactId("outcome"), discarded_before_preregistration=True),
    )
    for kwargs in invalid:
        assert not experiment_terminal_is_valid(ExperimentState.TERMINAL, **kwargs)
    assert not experiment_terminal_is_valid(
        ExperimentState.EVALUATING,
        preregistered=True,
        outcome_fact_id=FactId("outcome"),
        discarded_before_preregistration=False,
    )


@pytest.mark.parametrize("state", [RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED])
def test_terminal_run_requires_terminal_children_and_zero_residue(state: RunState) -> None:
    has_verdict = state is RunState.COMPLETED
    assert run_terminal_is_valid(
        state,
        experiment_states=(ExperimentState.TERMINAL,),
        open_residue_count=0,
        has_goal_verdict=has_verdict,
    )
    assert not run_terminal_is_valid(
        state,
        experiment_states=(ExperimentState.EVALUATING,),
        open_residue_count=0,
        has_goal_verdict=has_verdict,
    )
    assert not run_terminal_is_valid(
        state,
        experiment_states=(ExperimentState.TERMINAL,),
        open_residue_count=1,
        has_goal_verdict=has_verdict,
    )
    if state is RunState.COMPLETED:
        assert not run_terminal_is_valid(
            state,
            experiment_states=(),
            open_residue_count=0,
            has_goal_verdict=False,
        )


def test_deterministic_random_walks_use_only_declared_edges_and_stop_at_terminals() -> None:
    for seed in range(100):
        rng = random.Random(seed)
        machine = rng.choice(tuple(Machine))
        (state,) = initial_states(machine)
        revision = 1
        accepted_outcomes = 0
        for _ in range(100):
            rows = [row for row in TRANSITIONS if row.machine is machine and row.current_state is state]
            if not rows:
                assert state in terminal_states(machine)
                break
            row = rng.choice(rows)
            following = rng.choice(row.next_states)
            assert validate_transition(machine, state, row.command, following) is row
            revision += 1
            if row.command is Command.ACCEPT_OUTCOME:
                accepted_outcomes += 1
            state = following
        assert revision >= 1
        assert accepted_outcomes <= 1
