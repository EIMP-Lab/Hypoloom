"""The sole declarative lifecycle transition definition for Hypoloom."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, TypeAlias
from .domain import EffectKind, FactId, ResearchFactType
WORKFLOW_DEFINITION_VERSION = "hypoloom-lifecycle-v11"
class Machine(str, Enum):
    RUN_WORKFLOW, EXPERIMENT_WORKFLOW, HUMAN_DECISION, EFFECT_ATTEMPT = (
        "RunWorkflow", "ExperimentWorkflow", "HumanDecision", "EffectAttempt",
    )
class RunState(str, Enum):
    CREATED, ACTIVE, WAITING_HUMAN, COMPLETING = "CREATED", "ACTIVE", "WAITING_HUMAN", "COMPLETING"
    STOPPING, COMPLETED, FAILED, CANCELLED = "STOPPING", "COMPLETED", "FAILED", "CANCELLED"
class ExperimentState(str, Enum):
    DRAFTED, PREREGISTERED, READY = "DRAFTED", "PREREGISTERED", "READY"
    EXECUTING, EVALUATING, TERMINAL = "EXECUTING", "EVALUATING", "TERMINAL"
class HumanDecisionState(str, Enum):
    OPEN, RESOLVED = "OPEN", "RESOLVED"
class EffectAttemptState(str, Enum):
    OPEN, CLOSED = "OPEN", "CLOSED"
class Command(str, Enum):
    LITERATURE_ACCESS = "LITERATURE_ACCESS"
    SELECT_PLUGIN = "SELECT_PLUGIN"
    PAUSE_PARTICIPANT = "PAUSE_PARTICIPANT"
    RESUME_PARTICIPANT = "RESUME_PARTICIPANT"
    INJECT_NOTE = "INJECT_NOTE"
    RESOURCE_BLOCKED = "RESOURCE_BLOCKED"
    RESOURCE_RECHECK = "RESOURCE_RECHECK"
    PROVIDER_RETRY_CLEAR = "PROVIDER_RETRY_CLEAR"
    ACCEPT_ADJUDICATION = "ACCEPT_ADJUDICATION"
    ACCEPT_GRAPH_EVOLUTION = "ACCEPT_GRAPH_EVOLUTION"
    ACCEPT_EXPERT_INTERACTION = "ACCEPT_EXPERT_INTERACTION"
    FINAL_REPORT_FAILED = "FINAL_REPORT_FAILED"
    (
        CREATE_RUN, ACTIVATE, REQUEST_HYPOTHESIS_CANDIDATE, ACCEPT_HYPOTHESIS, REQUEST_RENDEZVOUS, RESOLVE_RENDEZVOUS, REQUEST_RESEARCH_NODE, REQUEST_AUXILIARY, AGENT_ATTEMPT_FINAL_FAILURE, LINK_EVIDENCE, RECORD_ASSESSMENT, RECORD_GOAL_VERDICT, REQUEST_HUMAN, RESOLVE_CONTINUE, RESOLVE_CANCEL, BEGIN_COMPLETION, REQUIRE_MORE_EVIDENCE, COMPLETE_CLEAN, CANCEL, FAIL_INTEGRITY, CLEANUP_OBSERVED, FINISH_STOP,
        CREATE_EXPERIMENT, REQUEST_SPEC_CANDIDATE, VALIDATE_SPEC_CANDIDATE, OBSERVE_SPEC_VALIDATION, PREREGISTER, PREPARE, REJECT_INTEGRITY, EXECUTE, OPEN_RETRY_ATTEMPT, REQUEST_CANCEL, OBSERVE_ATTEMPT_TERMINAL, RETRY_EVALUATOR, RESOLVE_HUMAN, ACCEPT_OUTCOME, OPEN_DECISION, RESOLVE, EXPIRE, CANCEL_DECISION, OPEN_ATTEMPT, OBSERVE_TERMINAL, MARK_LOST,
    ) = """CREATE_RUN ACTIVATE REQUEST_HYPOTHESIS_CANDIDATE ACCEPT_HYPOTHESIS REQUEST_RENDEZVOUS RESOLVE_RENDEZVOUS REQUEST_RESEARCH_NODE REQUEST_AUXILIARY
        AGENT_ATTEMPT_FINAL_FAILURE LINK_EVIDENCE RECORD_ASSESSMENT RECORD_GOAL_VERDICT REQUEST_HUMAN RESOLVE_CONTINUE RESOLVE_CANCEL BEGIN_COMPLETION
        REQUIRE_MORE_EVIDENCE COMPLETE_CLEAN CANCEL FAIL_INTEGRITY CLEANUP_OBSERVED FINISH_STOP CREATE_EXPERIMENT REQUEST_SPEC_CANDIDATE VALIDATE_SPEC_CANDIDATE OBSERVE_SPEC_VALIDATION PREREGISTER PREPARE
        REJECT_INTEGRITY EXECUTE OPEN_RETRY_ATTEMPT REQUEST_CANCEL OBSERVE_ATTEMPT_TERMINAL RETRY_EVALUATOR RESOLVE_HUMAN ACCEPT_OUTCOME OPEN_DECISION RESOLVE
        EXPIRE CANCEL_DECISION OPEN_ATTEMPT OBSERVE_TERMINAL MARK_LOST""".split()
State: TypeAlias = RunState | ExperimentState | HumanDecisionState | EffectAttemptState
@dataclass(frozen=True, slots=True)
class Transition:
    machine: Machine
    current_state: State | None
    command: Command
    next_states: tuple[State, ...]
    guard: str
    durable_effect: str
    fact_type: ResearchFactType | None = None
    external_effect: EffectKind | None = None
    @property
    def effect_required(self) -> bool:
        return self.external_effect is not None
    @property
    def effect(self) -> str:
        return self.durable_effect
    @property
    def dispatches_external(self) -> bool:
        return self.effect_required
    def as_dict(self) -> dict[str, Any]:
        return {
            "definition_version": WORKFLOW_DEFINITION_VERSION,
            "machine": self.machine.value,
            "current_state": None if self.current_state is None else self.current_state.value,
            "command": self.command.value,
            "next_states": [state.value for state in self.next_states],
            "guard": self.guard,
            "durable_effect": self.durable_effect,
            "fact_type": None if self.fact_type is None else self.fact_type.value,
            "external_effect": None if self.external_effect is None else self.external_effect.value,
            "effect_required": self.effect_required,
        }
def _t(
    machine: Machine,
    current: State | None,
    command: Command,
    next_state: State | tuple[State, ...],
    guard: str,
    effect: str,
    fact: ResearchFactType | None = None,
    external: EffectKind | None = None,
) -> Transition:
    next_states = next_state if isinstance(next_state, tuple) else (next_state,)
    return Transition(machine, current, command, next_states, guard, effect, fact, external)
R, X, H, A = Machine
TRANSITIONS: tuple[Transition, ...] = (
    _t(R, None, Command.CREATE_RUN, RunState.CREATED, "run_id_unused_and_goal_version_valid", "append_run_transition_goal", ResearchFactType.GOAL_DEFINED),
    _t(R, RunState.CREATED, Command.ACTIVATE, RunState.ACTIVE, "version_available_and_goal_exists", "append_transition_and_first_due_command"),
    *(_t(R, state, command, state, "authorized_participant_scope_and_reason", "append_participant_control")
      for state in (RunState.CREATED, RunState.ACTIVE)
      for command in (Command.PAUSE_PARTICIPANT, Command.RESUME_PARTICIPANT, Command.INJECT_NOTE)),
    _t(R, RunState.ACTIVE, Command.RESOURCE_BLOCKED, RunState.ACTIVE, "host_observed_pre_submit_pool_failure", "append_resource_blocked"),
    *(_t(R, state, Command.RESOURCE_RECHECK, state, "operator_rechecked_pool_and_closed_source_attempts", "append_resource_recheck")
      for state in (RunState.CREATED, RunState.ACTIVE)),
    *(_t(R, state, Command.LITERATURE_ACCESS, state, "operator_institutional_preference", "append_literature_access_control")
      for state in (RunState.CREATED, RunState.ACTIVE)),
    *(_t(R, state, Command.PROVIDER_RETRY_CLEAR, state, "operator_confirmed_provider_recovery", "append_provider_retry_clear")
      for state in (RunState.CREATED, RunState.ACTIVE)),
    *(_t(R, state, Command.SELECT_PLUGIN, state, "aci14_owner_native_commit_and_future_cycle_valid", "append_plugin_selection")
      for state in (RunState.CREATED, RunState.ACTIVE)),
    _t(R, RunState.ACTIVE, Command.REQUEST_HYPOTHESIS_CANDIDATE, RunState.ACTIVE, "agent_dispatch_allowed", "append_command_and_open_agent_attempt", external=EffectKind.AGENT),
    _t(R, RunState.ACTIVE, Command.ACCEPT_HYPOTHESIS, RunState.ACTIVE, "first_selected_hypothesis_candidate_valid", "close_attempt_append_fact_and_continue", ResearchFactType.HYPOTHESIS_PROPOSED),
    _t(R, RunState.ACTIVE, Command.ACCEPT_ADJUDICATION, RunState.ACTIVE, "all_batch_chains_terminal_and_exact_tip_refs_valid", "close_attempt_and_append_adjudicated_selection"),
    _t(R, RunState.ACTIVE, Command.ACCEPT_GRAPH_EVOLUTION, RunState.ACTIVE, "manager_source_and_admitted_fork_limits_valid", "close_attempt_and_append_graph_control"),
    _t(R, RunState.ACTIVE, Command.ACCEPT_EXPERT_INTERACTION, RunState.ACTIVE, "actual_observer_source_and_participant_scope_valid", "append_expert_interaction_control"),
    _t(R, RunState.ACTIVE, Command.FINAL_REPORT_FAILED, RunState.FAILED, "final_report_attempts_exhausted_and_effects_closed", "append_honest_final_report_failure"),
    _t(R, RunState.ACTIVE, Command.REQUEST_RENDEZVOUS, RunState.ACTIVE, "typed_peer_request_target_and_refs_valid", "append_command_and_open_agent_attempt", external=EffectKind.AGENT),
    _t(R, RunState.ACTIVE, Command.RESOLVE_RENDEZVOUS, RunState.ACTIVE, "typed_peer_reply_or_terminal_disposition_valid", "close_attempt_and_append_local_resolution"),
    *(_t(machine, state, Command.REQUEST_RESEARCH_NODE, state, "typed_node_key_ready_and_agent_dispatch_allowed", "append_stable_command_and_open_agent_attempt", external=EffectKind.AGENT) for machine, state in ((R, RunState.ACTIVE), (X, ExperimentState.DRAFTED), (X, ExperimentState.EVALUATING))),
    *(_t(machine, state, Command.REQUEST_AUXILIARY, state, "typed_auxiliary_scope_safe_and_agent_dispatch_allowed", "append_stable_command_and_open_agent_attempt", external=EffectKind.AGENT) for machine, state in ((R, RunState.ACTIVE), (X, ExperimentState.DRAFTED), (X, ExperimentState.EVALUATING))),
    _t(R, RunState.ACTIVE, Command.AGENT_ATTEMPT_FINAL_FAILURE, RunState.ACTIVE, "final_agent_failure_selected", "close_attempt_and_schedule_local_failure"),
    _t(R, RunState.ACTIVE, Command.LINK_EVIDENCE, RunState.ACTIVE, "evidence_refs_valid", "append_fact_and_transition", ResearchFactType.EVIDENCE_LINKED),
    _t(R, RunState.ACTIVE, Command.RECORD_ASSESSMENT, RunState.ACTIVE, "assessment_inputs_complete", "append_fact_and_transition", ResearchFactType.HYPOTHESIS_ASSESSED),
    _t(R, RunState.ACTIVE, Command.RECORD_GOAL_VERDICT, (RunState.ACTIVE, RunState.STOPPING), "goal_rule_and_assessments_complete_and_shared_success_stops", "append_fact_and_transition", ResearchFactType.GOAL_VERDICT_RECORDED),
    _t(R, RunState.ACTIVE, Command.REQUEST_HUMAN, RunState.WAITING_HUMAN, "run_decision_can_open", "open_decision_and_transition"),
    _t(R, RunState.WAITING_HUMAN, Command.RESOLVE_CONTINUE, RunState.ACTIVE, "authorized_continue_resolution", "resolve_decision_and_continue"),
    _t(R, RunState.WAITING_HUMAN, Command.RESOLVE_CANCEL, RunState.STOPPING, "authorized_cancel_resolution", "resolve_decision_and_persist_cancel_stop"),
    _t(R, RunState.ACTIVE, Command.BEGIN_COMPLETION, RunState.COMPLETING, "all_experiments_terminal_and_no_open_obligation", "append_closure_check"),
    _t(R, RunState.COMPLETING, Command.REQUIRE_MORE_EVIDENCE, RunState.ACTIVE, "named_goal_obligation_remains", "append_named_research_continuation"),
    _t(R, RunState.COMPLETING, Command.COMPLETE_CLEAN, RunState.COMPLETED, "goal_verdict_and_terminal_clean", "append_terminal_transition"),
    *(_t(R, state, Command.CANCEL, RunState.STOPPING, "authorized_cancellation", "persist_cancel_stop_and_suppress_dispatch") for state in (RunState.CREATED, RunState.ACTIVE, RunState.WAITING_HUMAN, RunState.COMPLETING)),
    *(_t(R, state, Command.FAIL_INTEGRITY, RunState.STOPPING, "typed_integrity_failure", "persist_failed_stop_and_suppress_dispatch") for state in (RunState.CREATED, RunState.ACTIVE, RunState.WAITING_HUMAN, RunState.COMPLETING)),
    _t(R, RunState.STOPPING, Command.CANCEL, RunState.STOPPING, "cleanup_incomplete", "retain_stop_without_failed_downgrade"),
    _t(R, RunState.STOPPING, Command.FAIL_INTEGRITY, RunState.STOPPING, "integrity_failure_during_cleanup", "retain_cleanup_and_escalate_failed"),
    _t(R, RunState.STOPPING, Command.RESOLVE_RENDEZVOUS, RunState.STOPPING, "pending_peer_request_cancelled", "append_local_cancel_resolution"),
    _t(R, RunState.STOPPING, Command.RESUME_PARTICIPANT, RunState.STOPPING, "shared_goal_met_releases_existing_interaction_reason", "append_participant_control"),
    _t(R, RunState.STOPPING, Command.CLEANUP_OBSERVED, RunState.STOPPING, "observation_owned_by_open_obligation", "close_owned_cleanup_obligation"),
    _t(R, RunState.STOPPING, Command.REQUEST_AUXILIARY, RunState.STOPPING, "aci14_final_report_only_after_other_effects_drained", "open_unique_stop_report_attempt", external=EffectKind.AGENT),
    _t(R, RunState.STOPPING, Command.AGENT_ATTEMPT_FINAL_FAILURE, RunState.STOPPING, "final_report_attempts_exhausted", "record_actual_report_failure"),
    _t(R, RunState.STOPPING, Command.FINAL_REPORT_FAILED, RunState.STOPPING, "final_report_attempts_exhausted_and_effects_closed", "retain_stop_and_escalate_failed"),
    _t(R, RunState.STOPPING, Command.FINISH_STOP, (RunState.FAILED, RunState.CANCELLED, RunState.COMPLETED), "terminal_clean_and_disposition_matches", "append_matching_terminal_transition"),
    _t(X, None, Command.CREATE_EXPERIMENT, ExperimentState.DRAFTED, "active_run_hypothesis_exists_and_id_unused", "append_experiment_transition"),
    _t(X, ExperimentState.DRAFTED, Command.REQUEST_SPEC_CANDIDATE, ExperimentState.DRAFTED, "experiment_agent_dispatch_allowed", "append_command_and_open_agent_attempt", external=EffectKind.AGENT),
    _t(X, ExperimentState.DRAFTED, Command.VALIDATE_SPEC_CANDIDATE, ExperimentState.DRAFTED, "selected_spec_candidate_and_process_dispatch_allowed", "append_validation_command_and_open_process_attempt", external=EffectKind.PROCESS),
    _t(X, ExperimentState.DRAFTED, Command.OBSERVE_SPEC_VALIDATION, ExperimentState.DRAFTED, "terminal_validation_observation_matches_selected_candidate_and_process_absent", "close_validation_attempt_and_persist_observation"),
    _t(X, ExperimentState.DRAFTED, Command.REQUEST_RENDEZVOUS, ExperimentState.DRAFTED, "typed_preflight_request_target_and_refs_valid", "append_command_and_open_agent_attempt", external=EffectKind.AGENT),
    _t(X, ExperimentState.DRAFTED, Command.RESOLVE_RENDEZVOUS, ExperimentState.DRAFTED, "typed_preflight_reply_or_terminal_disposition_valid", "close_attempt_and_append_local_resolution"),
    _t(X, ExperimentState.DRAFTED, Command.AGENT_ATTEMPT_FINAL_FAILURE, ExperimentState.DRAFTED, "final_agent_failure_selected", "close_attempt_and_leave_local_continuation"),
    _t(X, ExperimentState.DRAFTED, Command.PREREGISTER, ExperimentState.PREREGISTERED, "first_selected_spec_candidate_valid", "close_attempt_append_fact_and_transition", ResearchFactType.EXPERIMENT_PREREGISTERED),
    _t(X, ExperimentState.DRAFTED, Command.CANCEL, ExperimentState.DRAFTED, "draft_obligation_still_open", "persist_cancel_and_drain_draft_obligation"),
    _t(X, ExperimentState.DRAFTED, Command.CANCEL, ExperimentState.TERMINAL, "unpreregistered_draft_clean", "append_discarded_terminal_transition"),
    _t(X, ExperimentState.PREREGISTERED, Command.PREPARE, ExperimentState.READY, "immutable_inputs_valid", "record_inputs_and_due_launch"),
    *(
        _t(X, state, Command.REJECT_INTEGRITY, ExperimentState.EVALUATING, "frozen_spec_or_input_rejected", "persist_integrity_rejected_candidate")
        for state in (ExperimentState.PREREGISTERED, ExperimentState.READY)
    ),
    *(
        _t(X, state, Command.CANCEL, ExperimentState.EVALUATING, "authorized_cancel", "resolve_decision_persist_cancel_suppress_retry_and_select_if_empty")
        for state in (ExperimentState.PREREGISTERED, ExperimentState.READY, ExperimentState.EVALUATING)
    ),
    _t(X, ExperimentState.READY, Command.EXECUTE, ExperimentState.EXECUTING, "process_dispatch_allowed", "append_process_command_and_open_attempt", external=EffectKind.PROCESS),
    _t(X, ExperimentState.EXECUTING, Command.OPEN_RETRY_ATTEMPT, ExperimentState.EXECUTING, "next_process_ordinal_allowed", "append_next_open_process_attempt", external=EffectKind.PROCESS),
    _t(X, ExperimentState.EXECUTING, Command.REQUEST_CANCEL, ExperimentState.EXECUTING, "authorized_cancel", "persist_cancel_suppress_retry_and_request_group_cancel"),
    _t(X, ExperimentState.EXECUTING, Command.OBSERVE_ATTEMPT_TERMINAL, ExperimentState.EVALUATING, "first_terminal_candidate_selected_and_process_absent", "close_attempt_persist_candidate_and_transition"),
    _t(X, ExperimentState.EVALUATING, Command.REQUEST_RENDEZVOUS, ExperimentState.EVALUATING, "typed_analysis_request_target_and_refs_valid", "append_command_and_open_agent_attempt", external=EffectKind.AGENT),
    _t(X, ExperimentState.EVALUATING, Command.RESOLVE_RENDEZVOUS, ExperimentState.EVALUATING, "typed_analysis_reply_or_terminal_disposition_valid", "close_attempt_and_append_local_resolution"),
    _t(X, ExperimentState.EVALUATING, Command.RETRY_EVALUATOR, ExperimentState.EVALUATING, "evaluator_retry_allowed", "append_due_evaluator_command"),
    *(
        _t(X, state, Command.REQUEST_HUMAN, state, "experiment_decision_can_open", "open_decision_and_self_transition")
        for state in (ExperimentState.DRAFTED, ExperimentState.PREREGISTERED, ExperimentState.READY, ExperimentState.EVALUATING)
    ),
    _t(X, ExperimentState.DRAFTED, Command.RESOLVE_HUMAN, (ExperimentState.DRAFTED, ExperimentState.PREREGISTERED, ExperimentState.TERMINAL), "authorized_resolution_maps_to_declared_owner_command", "resolve_decision_and_apply_owner_continuation"),
    _t(X, ExperimentState.PREREGISTERED, Command.RESOLVE_HUMAN, (ExperimentState.PREREGISTERED, ExperimentState.READY, ExperimentState.EVALUATING), "authorized_resolution_maps_to_declared_owner_command", "resolve_decision_and_apply_owner_continuation"),
    _t(X, ExperimentState.READY, Command.RESOLVE_HUMAN, (ExperimentState.READY, ExperimentState.EXECUTING, ExperimentState.EVALUATING), "authorized_resolution_maps_to_declared_owner_command", "resolve_decision_and_apply_owner_continuation"),
    _t(X, ExperimentState.EVALUATING, Command.RESOLVE_HUMAN, (ExperimentState.EVALUATING, ExperimentState.TERMINAL), "authorized_resolution_maps_to_declared_owner_command", "resolve_decision_and_apply_owner_continuation"),
    _t(X, ExperimentState.EVALUATING, Command.ACCEPT_OUTCOME, ExperimentState.TERMINAL, "selected_outcome_candidate_valid_and_unique", "append_outcome_fact_and_terminal_transition", ResearchFactType.CANONICAL_OUTCOME_ACCEPTED),
    _t(H, None, Command.OPEN_DECISION, HumanDecisionState.OPEN, "decision_can_open", "append_decision_request_with_owner_edge"),
    _t(H, HumanDecisionState.OPEN, Command.RESOLVE, HumanDecisionState.RESOLVED, "authorized_resolution_at_expected_revision", "store_resolution_and_owner_continuation"),
    _t(H, HumanDecisionState.OPEN, Command.EXPIRE, HumanDecisionState.RESOLVED, "deadline_reached_and_unresolved", "store_timeout_and_owner_continuation"),
    _t(H, HumanDecisionState.OPEN, Command.CANCEL_DECISION, HumanDecisionState.RESOLVED, "owner_entering_stop_cleanup", "store_cancelled_and_owner_cleanup"),
    _t(A, None, Command.OPEN_ATTEMPT, EffectAttemptState.OPEN, "legal_owner_and_next_ordinal_allowed", "append_open_attempt"),
    _t(A, EffectAttemptState.OPEN, Command.OBSERVE_TERMINAL, EffectAttemptState.CLOSED, "typed_observation_matches_exact_owner", "append_terminal_observation_and_owner_continuation"),
    _t(A, EffectAttemptState.OPEN, Command.MARK_LOST, EffectAttemptState.CLOSED, "prior_host_dead_and_effect_proven_absent", "append_lost_observation_and_owner_continuation"),
)
class LifecycleError(ValueError):
    """Base class for fail-closed lifecycle validation errors."""
class UnknownLifecycleValue(LifecycleError): pass
class UnknownTransition(LifecycleError): pass
def _coerce(enum_type: type[Enum], value: Enum | str, label: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    if isinstance(value, Enum) or not isinstance(value, str):
        raise UnknownLifecycleValue(f"unknown {label}: {value!r}")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise UnknownLifecycleValue(f"unknown {label}: {value!r}") from exc
def _state_type(machine: Machine) -> type[Enum]:
    if machine is Machine.RUN_WORKFLOW:
        return RunState
    if machine is Machine.EXPERIMENT_WORKFLOW:
        return ExperimentState
    if machine is Machine.HUMAN_DECISION:
        return HumanDecisionState
    return EffectAttemptState
def _coerce_machine(machine: Machine | str) -> Machine:
    return _coerce(Machine, machine, "machine")  # type: ignore[return-value]
def _coerce_state(machine: Machine, state: State | str | None, *, allow_none: bool) -> State | None:
    if state is None:
        if allow_none:
            return None
        raise UnknownLifecycleValue("next_state cannot be None")
    return _coerce(_state_type(machine), state, f"{machine.value} state")  # type: ignore[return-value]
def declared_transitions(machine: Machine | str | None = None) -> tuple[Transition, ...]:
    if machine is None:
        return TRANSITIONS
    selected = _coerce_machine(machine)
    return tuple(transition for transition in TRANSITIONS if transition.machine is selected)
def declared_states(machine: Machine | str) -> tuple[State, ...]:
    selected = _coerce_machine(machine)
    seen = {
        state
        for transition in declared_transitions(selected)
        for state in ((transition.current_state,) + transition.next_states)
        if state is not None
    }
    return tuple(state for state in _state_type(selected) if state in seen)  # type: ignore[misc]
def initial_states(machine: Machine | str) -> tuple[State, ...]:
    selected = _coerce_machine(machine)
    return tuple(
        state
        for transition in declared_transitions(selected)
        if transition.current_state is None
        for state in transition.next_states
    )
def terminal_states(machine: Machine | str) -> tuple[State, ...]:
    selected = _coerce_machine(machine)
    outgoing = {
        transition.current_state
        for transition in declared_transitions(selected)
        if transition.current_state is not None
    }
    return tuple(state for state in declared_states(selected) if state not in outgoing)
def legal_commands(machine: Machine | str, state: State | str | None) -> tuple[Command, ...]:
    selected = _coerce_machine(machine)
    current = _coerce_state(selected, state, allow_none=True)
    present = {
        transition.command
        for transition in declared_transitions(selected)
        if transition.current_state is current
    }
    return tuple(command for command in Command if command in present)
def next_states(machine: Machine | str, state: State | str | None, command: Command | str) -> tuple[State, ...]:
    selected = _coerce_machine(machine)
    current = _coerce_state(selected, state, allow_none=True)
    selected_command = _coerce(Command, command, "command")
    found: list[State] = []
    for transition in declared_transitions(selected):
        if transition.current_state is current and transition.command is selected_command:
            for candidate in transition.next_states:
                if candidate not in found:
                    found.append(candidate)
    return tuple(found)
def validate_transition(
    machine: Machine | str,
    current_state: State | str | None,
    command: Command | str,
    next_state: State | str,
) -> Transition:
    """Return the unique declaration permitting an exact edge, or fail closed."""
    selected = _coerce_machine(machine)
    current = _coerce_state(selected, current_state, allow_none=True)
    selected_command = _coerce(Command, command, "command")
    following = _coerce_state(selected, next_state, allow_none=False)
    matches = tuple(
        transition
        for transition in declared_transitions(selected)
        if transition.current_state is current
        and transition.command is selected_command
        and following in transition.next_states
    )
    if len(matches) != 1:
        raise UnknownTransition(
            f"undeclared transition: {selected.value} {current_state!r} "
            f"{selected_command.value} -> {next_state!r}"
        )
    return matches[0]
def is_terminal(machine: Machine | str, state: State | str) -> bool:
    selected = _coerce_machine(machine)
    candidate = _coerce_state(selected, state, allow_none=False)
    return candidate in terminal_states(selected)
def lifecycle_definition() -> dict[str, Any]:
    """Serialize states and rows directly from ``TRANSITIONS`` for docs/checks."""
    return {
        "workflow_definition_version": WORKFLOW_DEFINITION_VERSION,
        "projections": [
            {
                "machine": machine.value,
                "states": [state.value for state in declared_states(machine)],
                "initial_states": [state.value for state in initial_states(machine)],
                "terminal_states": [state.value for state in terminal_states(machine)],
            }
            for machine in Machine
        ],
        "transitions": [transition.as_dict() for transition in TRANSITIONS],
    }
def experiment_terminal_is_valid(
    state: ExperimentState | str,
    *,
    preregistered: bool,
    outcome_fact_id: FactId | None,
    discarded_before_preregistration: bool,
) -> bool:
    """Distinguish a clean discarded draft from a post-preregistration outcome."""
    candidate = _coerce_state(Machine.EXPERIMENT_WORKFLOW, state, allow_none=False)
    if candidate is not ExperimentState.TERMINAL:
        return False
    if preregistered:
        return outcome_fact_id is not None and not discarded_before_preregistration
    return outcome_fact_id is None and discarded_before_preregistration
def run_terminal_is_valid(
    state: RunState | str,
    *,
    experiment_states: Iterable[ExperimentState | str],
    open_residue_count: int,
    has_goal_verdict: bool,
) -> bool:
    """Apply the terminal child, verdict and zero-residue model invariant."""
    candidate = _coerce_state(Machine.RUN_WORKFLOW, state, allow_none=False)
    if candidate not in terminal_states(Machine.RUN_WORKFLOW):
        return False
    if type(open_residue_count) is not int or open_residue_count != 0:
        return False
    if candidate is RunState.COMPLETED and not has_goal_verdict:
        return False
    return all(
        _coerce_state(Machine.EXPERIMENT_WORKFLOW, child, allow_none=False)
        is ExperimentState.TERMINAL
        for child in experiment_states
    )
def _validate_registry() -> None:
    state_count = sum(len(declared_states(machine)) for machine in Machine)
    if len(Machine) != 4 or state_count != 18 or any(set(declared_states(machine)) != set(_state_type(machine)) for machine in Machine):
        raise RuntimeError("lifecycle definition must contain exactly 4 projections / 18 states")
    if {transition.fact_type for transition in TRANSITIONS if transition.fact_type} != set(ResearchFactType):
        raise RuntimeError("lifecycle definition must cause all and only the seven Research facts")
    if {transition.command for transition in TRANSITIONS} != set(Command):
        raise RuntimeError("every declared command must occur in the lifecycle definition")
    exact_edges: set[tuple[Machine, State | None, Command, State]] = set()
    for transition in TRANSITIONS:
        if not transition.guard or not transition.durable_effect or not transition.next_states:
            raise RuntimeError("every transition requires a guard, durable effect and next state")
        state_type = _state_type(transition.machine)
        if transition.current_state is not None and not isinstance(transition.current_state, state_type):
            raise RuntimeError("transition current state belongs to the wrong projection")
        for following in transition.next_states:
            if not isinstance(following, state_type):
                raise RuntimeError("transition next state belongs to the wrong projection")
            edge = (transition.machine, transition.current_state, transition.command, following)
            if edge in exact_edges:
                raise RuntimeError(f"duplicate lifecycle edge: {edge!r}")
            exact_edges.add(edge)
    for machine in Machine:
        roots = initial_states(machine)
        if len(roots) != 1:
            raise RuntimeError(f"{machine.value} must have exactly one initial state")
        reachable = set(roots)
        changed = True
        while changed:
            changed = False
            for transition in declared_transitions(machine):
                if transition.current_state in reachable:
                    before = len(reachable)
                    reachable.update(transition.next_states)
                    changed |= len(reachable) != before
        if reachable != set(declared_states(machine)):
            raise RuntimeError(f"{machine.value} contains an unreachable state")
_validate_registry()
