"""Deterministic bounded component research policy."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping
from .domain import GoalVerdict, HypothesisAssessment, ScientificOutcome
from .lifecycle import Command, ExperimentState, Machine, RunState, is_terminal, legal_commands
from .shared_acceptance import SharedAcceptance, evaluate_shared_acceptance, normalize_goal_evaluation_observation, normalize_shared_observation
POLICY_VERSION = "component-conjunction/11"
_COMPARABLE = {ScientificOutcome.SUCCESS, ScientificOutcome.CLEAN_REFUTATION}; _REVISIONABLE_OUTCOMES = {ScientificOutcome.EXECUTION_FAILURE, ScientificOutcome.INTEGRITY_REJECTED}; _REVISION_BREAKER_WARN = 10; _REVISION_BREAKER_SEVERE = 20; _REVISION_BREAKER_MAX = 30
_TIERS = ("primary", "must", "should")
def _text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip(): raise ValueError(f"{name} must be non-empty trimmed text")
def _texts(value: object, name: str, *, empty: bool = False) -> None:
    if not isinstance(value, tuple) or (not empty and not value): raise ValueError(f"{name} must be a tuple")
    for item in value:
        _text(item, f"{name} item")
    if len(value) != len(set(value)): raise ValueError(f"{name} must not contain duplicates")
@dataclass(frozen=True, slots=True, kw_only=True)
class ProposalSnapshot:
    role: str
    hypothesis_id: str | None = None
    final_failure: bool = False
    effect_in_flight: bool = False
    dispatch_paused: bool = False
    def __post_init__(self) -> None:
        _text(self.role, "role")
        if self.hypothesis_id is not None: _text(self.hypothesis_id, "hypothesis_id")
        if self.final_failure and self.hypothesis_id is not None: raise ValueError("a failed proposal cannot have a hypothesis")
        if not all(isinstance(value, bool) for value in (self.final_failure, self.effect_in_flight, self.dispatch_paused)): raise ValueError("proposal flags must be boolean")
@dataclass(frozen=True, slots=True, kw_only=True)
class ComponentSnapshot:
    component_id: str
    tier: str
    owner_role: str
    proposals: tuple[ProposalSnapshot, ...]
    dependencies: tuple[str, ...] = ()
    input_dependencies: tuple[str, ...] = ()
    experiment_id: str | None = None
    selected_hypothesis_id: str | None = None
    method_family_id: str | None = None
    experiment_revision: int | None = None
    experiment_state: ExperimentState | None = None
    spec_final_failure: bool = False
    spec_candidate_ready: bool = False
    outcome: ScientificOutcome | None = None
    criterion_met: bool | None = None
    evidence_linked: bool = False; revision_of_outcome_id: str | None = None; revision_tip: bool = False
    assessment: HypothesisAssessment | None = None; assessment_id: str | None = None
    effect_in_flight: bool = False; resource_blocked: bool = False; rendezvous_blocked: bool = False
    creation_budget_exhausted: bool = False
    successor_budget_state: str = "available"; expert_lineage_terminated: bool = True; current_cycle_revisions: int = 0
    execution_controller_open: bool = False
    tool_execution: bool = False
    preflight_rejected: bool = False
    execution_final_failure: bool = False
    pending_hypothesis_id: str | None = None
    creation_paused: bool = False
    batch_pending: bool = False
    goal_evaluation: Mapping[str, Any] | None = None
    def __post_init__(self) -> None:
        _text(self.component_id, "component_id")
        _text(self.owner_role, "owner_role")
        if self.method_family_id is not None: _text(self.method_family_id, "method_family_id")
        if self.pending_hypothesis_id is not None: _text(self.pending_hypothesis_id, "pending_hypothesis_id")
        if self.tier not in _TIERS: raise ValueError("tier must be primary, must or should")
        if not isinstance(self.proposals, tuple) or not self.proposals: raise ValueError("proposals must be a non-empty tuple")
        if not all(isinstance(item, ProposalSnapshot) for item in self.proposals): raise ValueError("proposals must contain ProposalSnapshot values")
        roles = tuple(item.role for item in self.proposals)
        if len(roles) != len(set(roles)) or self.owner_role not in roles: raise ValueError("proposal roles must be unique and include the owner")
        _texts(self.dependencies, "dependencies", empty=True)
        _texts(self.input_dependencies, "input_dependencies", empty=True)
        if not set(self.input_dependencies) <= set(self.dependencies): raise ValueError("input_dependencies must be dependencies")
        experiment_fields = (self.experiment_id is not None, self.selected_hypothesis_id is not None,
            self.method_family_id is not None, self.experiment_revision is not None, self.experiment_state is not None)
        if any(experiment_fields) and not all(experiment_fields): raise ValueError("experiment identity, hypothesis, family, revision and state travel together")
        if self.experiment_id is not None:
            _text(self.experiment_id, "experiment_id")
            _text(self.selected_hypothesis_id, "selected_hypothesis_id")
            if self.selected_hypothesis_id not in {item.hypothesis_id for item in self.proposals}: raise ValueError("selected hypothesis must be an accepted component proposal")
        if self.experiment_revision is not None and (isinstance(self.experiment_revision, bool)
                or not isinstance(self.experiment_revision, int) or self.experiment_revision < 0): raise ValueError("experiment_revision must be nonnegative")
        if isinstance(self.experiment_state, str): object.__setattr__(self, "experiment_state", ExperimentState(self.experiment_state))
        if isinstance(self.outcome, str): object.__setattr__(self, "outcome", ScientificOutcome(self.outcome))
        if isinstance(self.assessment, str): object.__setattr__(self, "assessment", HypothesisAssessment(self.assessment))
        if self.spec_final_failure and self.experiment_state is not ExperimentState.DRAFTED: raise ValueError("spec_final_failure requires a drafted experiment")
        if self.criterion_met is not None and self.outcome not in _COMPARABLE: raise ValueError("criterion_met requires a comparable outcome")
        if self.evidence_linked and self.outcome is None: raise ValueError("evidence requires an outcome")
        if self.revision_of_outcome_id is not None: _text(self.revision_of_outcome_id, "revision_of_outcome_id")
        if self.revision_of_outcome_id is not None and (self.experiment_id is None or self.outcome is None): raise ValueError("revision predecessor outcome requires an experiment outcome")
        if (self.assessment is None) != (self.assessment_id is None): raise ValueError("assessment and assessment_id travel together")
        if self.assessment_id is not None: _text(self.assessment_id, "assessment_id")
        flags = (self.spec_candidate_ready, self.effect_in_flight, self.resource_blocked,
                 self.rendezvous_blocked, self.creation_budget_exhausted, self.revision_tip, self.expert_lineage_terminated,
                 self.execution_controller_open, self.execution_final_failure, self.tool_execution, self.preflight_rejected, self.creation_paused, self.batch_pending)
        if not all(isinstance(value, bool) for value in flags): raise ValueError("component blocker flags must be boolean")
        if self.successor_budget_state not in {"available", "blocked", "exhausted"}: raise ValueError("invalid successor budget state")
        if self.goal_evaluation is not None:
            object.__setattr__(self, "goal_evaluation", normalize_goal_evaluation_observation(self.goal_evaluation))
            if self.outcome is None: raise ValueError("goal evaluation requires an accepted outcome")
    @property
    def proposal_terminal(self) -> bool:
        return all(item.hypothesis_id is not None or item.final_failure for item in self.proposals)
    @property
    def accepted_hypotheses(self) -> tuple[str, ...]:
        return tuple(item.hypothesis_id for item in self.proposals if item.hypothesis_id is not None)
    @property
    def selected_or_default(self) -> str | None:
        if self.selected_hypothesis_id is not None:
            return self.selected_hypothesis_id
        owner = next(item for item in self.proposals if item.role == self.owner_role)
        return owner.hypothesis_id or next(iter(self.accepted_hypotheses), None)
@dataclass(frozen=True, slots=True, kw_only=True)
class PolicySnapshot:
    run_id: str
    run_state: RunState
    run_revision: int
    roster: tuple[str, ...]
    components: tuple[ComponentSnapshot, ...]
    busy_roles: tuple[str, ...] = ()
    live_effect_count: int = 0
    live_effect_limit: int = 3
    budget_exhausted: bool = False
    has_goal_verdict: bool = False
    open_obligation_count: int = 0
    completion_mode: str = "required_conjunction"
    goal_evaluation_required: bool = False
    shared_acceptance: SharedAcceptance | None = None
    shared_acceptance_source_attempt_id: str | None = None
    def __post_init__(self) -> None:
        _text(self.run_id, "run_id")
        if self.completion_mode not in {"required_conjunction", "explicit_signals", "shared_goal"}:
            raise ValueError("unknown goal completion mode")
        if type(self.goal_evaluation_required) is not bool: raise ValueError("goal_evaluation_required must be boolean")
        if (self.shared_acceptance is None) != (self.shared_acceptance_source_attempt_id is None):
            raise ValueError("shared acceptance and source Attempt travel together")
        if self.shared_acceptance is not None:
            if not self.goal_evaluation_required or not isinstance(self.shared_acceptance, SharedAcceptance):
                raise ValueError("shared acceptance requires the explicit Grounding evaluation mode")
            _text(self.shared_acceptance_source_attempt_id, "shared acceptance source Attempt")
        if isinstance(self.run_state, str):
            object.__setattr__(self, "run_state", RunState(self.run_state))
        if not isinstance(self.run_state, RunState):
            raise ValueError("run_state must be RunState")
        if isinstance(self.run_revision, bool) or not isinstance(self.run_revision, int) or self.run_revision < 0:
            raise ValueError("run_revision must be nonnegative")
        _texts(self.roster, "roster")
        if not 1 <= len(self.roster) <= 3:
            raise ValueError("roster must contain one to three roles")
        _texts(self.busy_roles, "busy_roles", empty=True)
        if not set(self.busy_roles) <= set(self.roster):
            raise ValueError("busy_roles must belong to roster")
        if not isinstance(self.components, tuple) or not self.components:
            raise ValueError("components must be a non-empty tuple")
        if not all(isinstance(item, ComponentSnapshot) for item in self.components):
            raise ValueError("components must contain ComponentSnapshot values")
        ids = tuple(item.component_id for item in self.components)
        if len(ids) != len(set(ids)):
            raise ValueError("component IDs must be unique")
        if any(item.owner_role not in self.roster for item in self.components):
            raise ValueError("component owner must belong to roster")
        if any(not {proposal.role for proposal in item.proposals} <= set(self.roster) for item in self.components):
            raise ValueError("component proposer must belong to roster")
        if any(not set(item.dependencies) <= set(ids) or item.component_id in item.dependencies for item in self.components):
            raise ValueError("component dependency is unknown or self-referential")
        for name in ("live_effect_count", "live_effect_limit", "open_obligation_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be nonnegative")
        if self.live_effect_limit < 1 or self.live_effect_count > self.live_effect_limit:
            raise ValueError("live effect values exceed the bounded limit")
    def revision_for(self, owner_id: str) -> int:
        if owner_id == self.run_id:
            return self.run_revision
        component = next((item for item in self.components if item.experiment_id == owner_id), None)
        if component is None or component.experiment_revision is None:
            raise ValueError(f"unknown policy owner: {owner_id!r}")
        return component.experiment_revision
@dataclass(frozen=True, slots=True, kw_only=True)
class PolicyDecision:
    command: str
    owner_id: str
    expected_revision: int
    payload: Mapping[str, Any]
    reason: str
    policy_version: str = POLICY_VERSION
    def to_dict(self) -> dict[str, Any]:
        return {"command": self.command, "owner_id": self.owner_id,
                "expected_revision": self.expected_revision, "payload": dict(self.payload),
                "reason": self.reason, "policy_version": self.policy_version}
class ResearchPolicy:
    """Choose one durable action while independent component effects continue."""
    def admit_experiment_submission(self, view: PolicySnapshot, component_id: str,
                                    predecessor_experiment_id: str | None) -> PolicyDecision:
        """Apply the ordinary creation rules to an explicitly requested effect."""
        if view.goal_evaluation_required and view.shared_acceptance is None:
            raise ValueError("Grounding common acceptance is unresolved")
        if view.run_state is not RunState.ACTIVE or view.budget_exhausted:
            raise ValueError("the run cannot admit another experiment")
        component = next((item for item in view.components if item.component_id == component_id), None)
        if component is None or component.tier == "should" or component.creation_budget_exhausted or component.creation_paused:
            raise ValueError("the component cannot admit another experiment")
        if not _dependencies_ready(component, {item.component_id: item for item in view.components}):
            raise ValueError("the component dependencies are not ready")
        if predecessor_experiment_id is None:
            successor = component.pending_hypothesis_id is not None and _terminal(component) and component.assessment is not None
            if (component.experiment_id is not None and not successor) or not component.proposal_terminal or component.selected_or_default is None:
                raise ValueError("the initial experiment is not eligible")
            return _decision(view, "CREATE_EXPERIMENT", view.run_id, "the execution Agent submitted its initial candidate",
                {"component_id": component_id, "hypothesis_id": component.pending_hypothesis_id or component.selected_or_default, "role": component.owner_role})
        if (component.tool_execution and component.preflight_rejected and _terminal(component)
                and component.experiment_id == predecessor_experiment_id and component.outcome is None
                and component.assessment is None and component.successor_budget_state == "available"
                and component.current_cycle_revisions <= _REVISION_BREAKER_MAX):
            return _decision(view, "CREATE_EXPERIMENT", view.run_id, "the execution Agent repaired its declined unpreregistered submission",
                {"component_id": component_id, "hypothesis_id": component.selected_hypothesis_id,
                 "role": component.owner_role, "preflight_repair_of_experiment_id": predecessor_experiment_id})
        if (component.experiment_id != predecessor_experiment_id or not _terminal(component)
                or component.outcome not in _REVISIONABLE_OUTCOMES or not component.evidence_linked
                or component.assessment is not None or not component.revision_tip
                or component.revision_of_outcome_id is None or component.successor_budget_state != "available"
                or component.current_cycle_revisions > _REVISION_BREAKER_MAX):
            raise ValueError("the exact predecessor has no eligible operational repair")
        return _decision(view, "CREATE_EXPERIMENT", view.run_id, "the execution Agent submitted a repair of its exact linked failure",
            {"component_id": component_id, "hypothesis_id": component.selected_hypothesis_id,
             "role": component.owner_role, "revision_of_outcome_id": component.revision_of_outcome_id})

    def next_command(self, view: PolicySnapshot) -> PolicyDecision | None:
        if not isinstance(view, PolicySnapshot):
            raise TypeError("snapshot must be PolicySnapshot")
        if is_terminal(Machine.RUN_WORKFLOW, view.run_state):
            return None
        if view.run_state is RunState.CREATED:
            return _decision(view, "ACTIVATE", view.run_id, "new run is ready")
        if view.run_state is RunState.COMPLETING:
            if view.open_obligation_count:
                return _decision(view, "REQUIRE_MORE_EVIDENCE", view.run_id,
                                 "a completion obligation remains",
                                 {"open_obligation_count": view.open_obligation_count})
            return _decision(view, "COMPLETE_CLEAN", view.run_id, "verdict and residue checks are ready")
        if view.run_state in {RunState.WAITING_HUMAN, RunState.STOPPING}:
            return None
        if view.run_state is not RunState.ACTIVE:
            raise AssertionError(f"unconsumed run state {view.run_state.value}")
        by_id = {item.component_id: item for item in view.components}
        active = tuple(item for item in view.components if item.tier != "should")
        if view.goal_evaluation_required and view.shared_acceptance is None:
            # Missing Grounding forbids science, not ordinary exhausted-run
            # closure. Reuse durable final-failure/budget flags and the existing
            # INCONCLUSIVE verdict; never fabricate a criterion or assessment.
            if not view.budget_exhausted and not all(
                    proposal.final_failure for item in active for proposal in item.proposals):
                return None
        shared_satisfied = (any(_shared_qualification(view, item) is True for item in active) if view.goal_evaluation_required
            else view.completion_mode == "shared_goal" and _required_supported(active, "shared_goal"))
        if shared_satisfied and not view.has_goal_verdict:
            return _decision(view, "RECORD_GOAL_VERDICT", view.run_id,
                "one accepted shared-goal alternative is sufficient; stop remaining research",
                _goal_verdict(view, active))
        actionable = () if not view.goal_evaluation_required and view.completion_mode != "shared_goal" and _required_supported(active) else tuple(
            item for item in active if not item.batch_pending and _dependencies_ready(item, by_id)
        )
        if not view.budget_exhausted and not shared_satisfied and view.live_effect_count < view.live_effect_limit:
            for component in actionable:
                if component.creation_budget_exhausted:
                    continue
                for proposal in component.proposals:
                    if (proposal.hypothesis_id is None and not proposal.final_failure
                            and not proposal.effect_in_flight and not proposal.dispatch_paused and proposal.role not in view.busy_roles):
                        return _decision(view, "REQUEST_HYPOTHESIS_CANDIDATE", view.run_id,
                                         "an eligible component awaits a declared proposer",
                                         {"component_id": component.component_id, "role": proposal.role})
        for component in actionable:
            if (component.experiment_state is ExperimentState.DRAFTED and component.spec_final_failure
                    and not component.effect_in_flight and component.experiment_id is not None):
                return _decision(view, "CANCEL", component.experiment_id,
                                 "final spec failure closes the component experiment",
                                 {"component_id": component.component_id})
            if (component.experiment_state is ExperimentState.DRAFTED and component.spec_candidate_ready
                    and not component.rendezvous_blocked and component.experiment_id is not None):
                return _decision(view, "PREREGISTER", component.experiment_id,
                                 "the reviewed spec candidate is ready for deterministic preregistration",
                                 {"component_id": component.component_id})
            if (component.experiment_state is ExperimentState.DRAFTED and not component.spec_final_failure
                    and not component.spec_candidate_ready and not component.effect_in_flight and not component.rendezvous_blocked
                    and component.owner_role not in view.busy_roles and component.experiment_id is not None
                    and view.live_effect_count < view.live_effect_limit):
                return _decision(view, "REQUEST_SPEC_CANDIDATE", component.experiment_id,
                                 "the owner must synthesize the accepted component proposals",
                                 {"component_id": component.component_id,
                                  "hypothesis_id": component.selected_hypothesis_id,
                                  "role": component.owner_role})
            if component.experiment_state is ExperimentState.PREREGISTERED and not component.rendezvous_blocked:
                return _decision(view, "PREPARE", component.experiment_id or "",
                                 "preregistered component inputs are available",
                                 {"component_id": component.component_id})
            if (component.experiment_state is ExperimentState.READY and not component.effect_in_flight
                    and not component.resource_blocked and not component.rendezvous_blocked
                    and view.live_effect_count < view.live_effect_limit):
                return _decision(view, "EXECUTE", component.experiment_id or "",
                                 "the component is ready for bounded execution",
                                 {"component_id": component.component_id})
            if component.experiment_state is ExperimentState.EVALUATING and not component.rendezvous_blocked:
                return _decision(view, "ACCEPT_OUTCOME", component.experiment_id or "",
                                 "the terminal artifact candidate needs deterministic evaluation",
                                 {"component_id": component.component_id})
        for component in (() if view.budget_exhausted or shared_satisfied else actionable):
            if (component.experiment_id is None and not component.creation_paused and not component.creation_budget_exhausted and not component.execution_final_failure and component.proposal_terminal
                    and component.selected_or_default is not None):
                return _decision(view, "CREATE_EXPERIMENT", view.run_id, "all declared proposal slots are terminal",
                    {"component_id": component.component_id, "hypothesis_id": component.selected_or_default, "role": component.owner_role})
        for component in active:
            if _terminal(component) and component.outcome is not None and not component.evidence_linked:
                return _decision(view, "LINK_EVIDENCE", view.run_id, "a component outcome must be linked before assessment",
                                 {"component_id": component.component_id, "hypothesis_id": component.selected_hypothesis_id})
            revisionable = (_terminal(component) and component.outcome in _REVISIONABLE_OUTCOMES
                and component.evidence_linked and component.assessment is None and component.revision_tip
                and component.revision_of_outcome_id is not None)
            if revisionable and not component.tool_execution and component.successor_budget_state == "blocked":
                continue
            if (revisionable and not component.tool_execution and not component.creation_paused and not component.creation_budget_exhausted and not view.budget_exhausted
                    and component.successor_budget_state == "available" and component.current_cycle_revisions <= _REVISION_BREAKER_MAX):
                return _decision(view, "CREATE_EXPERIMENT", view.run_id, "a linked canonical repairable failure is eligible for one successor", {"component_id": component.component_id,
                    "hypothesis_id": component.selected_hypothesis_id, "role": component.owner_role, "revision_of_outcome_id": component.revision_of_outcome_id})
            if (_terminal(component) and component.outcome is not None and component.evidence_linked
                    and component.assessment is None and not component.execution_controller_open):
                return _decision(view, "RECORD_ASSESSMENT", view.run_id, "linked component evidence needs deterministic assessment",
                    {"component_id": component.component_id, "hypothesis_id": component.selected_hypothesis_id,
                     "assessment": _assessment(component).value})
        if _work_in_flight(view):
            return None
        if not view.has_goal_verdict:
            return _decision(view, "RECORD_GOAL_VERDICT", view.run_id,
                             "required component work is closed or no longer actionable",
                             _goal_verdict(view, active))
        return _decision(view, "BEGIN_COMPLETION", view.run_id,
                         "the accepted goal's scientific verdict is durable")
def _dependencies_ready(component: ComponentSnapshot, by_id: Mapping[str, ComponentSnapshot]) -> bool:
    dependencies = tuple((dependency_id, by_id[dependency_id]) for dependency_id in component.dependencies)
    if any(not dependency.expert_lineage_terminated for _, dependency in dependencies): return False
    input_families = {dependency.method_family_id for dependency_id, dependency in dependencies
        if dependency_id in component.input_dependencies and dependency.method_family_id is not None}
    return len(input_families) <= 1 and all((dependency.outcome in _COMPARABLE and dependency.method_family_id is not None)
        if dependency_id in component.input_dependencies else dependency.assessment is HypothesisAssessment.SUPPORTED for dependency_id, dependency in dependencies)
def _terminal(component: ComponentSnapshot) -> bool:
    return component.experiment_state is not None and is_terminal(
        Machine.EXPERIMENT_WORKFLOW, component.experiment_state
    )
def _assessment(component: ComponentSnapshot) -> HypothesisAssessment:
    if component.outcome is ScientificOutcome.SUCCESS and component.criterion_met is True:
        return HypothesisAssessment.SUPPORTED
    if component.outcome in _COMPARABLE and component.criterion_met is False:
        return HypothesisAssessment.REFUTED
    return HypothesisAssessment.INCONCLUSIVE
def _required_supported(components: tuple[ComponentSnapshot, ...], completion_mode: str = "required_conjunction") -> bool:
    if completion_mode == "shared_goal":
        return any(item.assessment is HypothesisAssessment.SUPPORTED for item in components)
    return bool(components) and all(item.assessment is HypothesisAssessment.SUPPORTED for item in components)
def _work_in_flight(view: PolicySnapshot) -> bool:
    if view.live_effect_count:
        return True
    for component in view.components:
        if component.tier == "should":
            continue
        if component.effect_in_flight or component.rendezvous_blocked or component.batch_pending:
            return True
        if component.experiment_id is not None and not _terminal(component):
            return True
        if any(item.dispatch_paused and item.hypothesis_id is None and not item.final_failure for item in component.proposals):
            return True
        if component.creation_paused and (not component.expert_lineage_terminated or component.pending_hypothesis_id is not None
                or component.experiment_id is None and component.selected_or_default is not None):
            return True
    return False
def _shared_qualification(view: PolicySnapshot, item: ComponentSnapshot) -> bool | None:
    observation = item.goal_evaluation
    if observation is None: return None
    if item.outcome in {ScientificOutcome.INTEGRITY_REJECTED, ScientificOutcome.CANCELLED}: return None
    if observation["source_attempt_id"] != view.shared_acceptance_source_attempt_id:
        raise ValueError("goal evaluation source differs from accepted Grounding")
    assert view.shared_acceptance is not None
    raw = normalize_shared_observation({name: observation[name] for name in ("measurements", "checks")}, view.shared_acceptance)
    if not all(raw["checks"][name] for name in view.shared_acceptance.validity_checks): return None
    if not item.evidence_linked or item.assessment_id is None or not _terminal(item): return None
    return evaluate_shared_acceptance(view.shared_acceptance, raw)
def _goal_verdict(view: PolicySnapshot, components: tuple[ComponentSnapshot, ...]) -> dict[str, Any]:
    if not view.goal_evaluation_required: return _verdict(components, view.completion_mode)
    results = tuple((item, _shared_qualification(view, item)) for item in components)
    winners = [item for item, passed in results if passed is True]
    if winners:
        winner = min(winners, key=lambda item: (item.selected_hypothesis_id or "", item.component_id))
        return {"verdict": GoalVerdict.GOAL_MET.value, "assessment_ids": [winner.assessment_id],
            "component_ids": [winner.component_id], "method_family_id": winner.method_family_id}
    assessed = sorted((item for item in components if item.assessment_id is not None), key=lambda item: item.component_id)
    refuted = bool(results) and all(item.expert_lineage_terminated and passed is False for item, passed in results)
    families = {item.method_family_id for item in assessed if item.method_family_id is not None}
    return {"verdict": (GoalVerdict.GOAL_NOT_MET if refuted else GoalVerdict.INCONCLUSIVE).value,
        "assessment_ids": [item.assessment_id for item in assessed], "component_ids": [item.component_id for item in assessed],
        "method_family_id": next(iter(families)) if len(families) == 1 else None}
def _verdict(components: tuple[ComponentSnapshot, ...], completion_mode: str = "required_conjunction") -> dict[str, Any]:
    if completion_mode == "shared_goal":
        winners = [item for item in components if item.assessment is HypothesisAssessment.SUPPORTED
            and item.assessment_id is not None]
        if winners:
            winner = min(winners, key=lambda item: (item.selected_hypothesis_id or "", item.component_id))
            return {"verdict": GoalVerdict.GOAL_MET.value, "assessment_ids": [winner.assessment_id],
                "component_ids": [winner.component_id], "method_family_id": winner.method_family_id}
        assessed = sorted((item for item in components if item.assessment_id is not None), key=lambda item: item.component_id)
        refuted = bool(components) and all(item.expert_lineage_terminated and item.outcome in _COMPARABLE
            and item.assessment is HypothesisAssessment.REFUTED and item.assessment_id is not None for item in components)
        families = {item.method_family_id for item in assessed if item.method_family_id is not None}
        return {"verdict": (GoalVerdict.GOAL_NOT_MET if refuted else GoalVerdict.INCONCLUSIVE).value,
            "assessment_ids": [item.assessment_id for item in assessed], "component_ids": [item.component_id for item in assessed],
            "method_family_id": next(iter(families)) if len(families) == 1 else None}
    assessed = tuple(item for item in components if item.assessment_id is not None); assessed_families = {item.method_family_id for item in assessed if item.method_family_id is not None}
    primary = tuple(item for item in components if item.tier == "primary")
    must = tuple(item for item in components if item.tier == "must")
    required = (*primary, *must); producers = {dependency for item in required for dependency in item.input_dependencies}
    families = {item.method_family_id for item in required if item.method_family_id is not None
        and (not producers or item.component_id in producers or item.input_dependencies)}
    one_family = len(families) == 1
    lineages_terminated = all(item.expert_lineage_terminated for item in (*primary, *must))
    if (lineages_terminated and one_family and primary
            and all(item.assessment is HypothesisAssessment.SUPPORTED for item in (*primary, *must))):
        verdict = GoalVerdict.GOAL_MET
    elif (lineages_terminated and one_family and primary
            and all(item.outcome in _COMPARABLE and item.assessment in {
                HypothesisAssessment.SUPPORTED, HypothesisAssessment.REFUTED} for item in primary)
            and all(item.assessment is HypothesisAssessment.SUPPORTED for item in must)
            and any(item.assessment is HypothesisAssessment.REFUTED for item in primary)):
        verdict = GoalVerdict.GOAL_NOT_MET
    else:
        verdict = GoalVerdict.INCONCLUSIVE
    return {"verdict": verdict.value,
            "assessment_ids": [item.assessment_id for item in assessed],
            "component_ids": [item.component_id for item in assessed],
            "method_family_id": None if verdict is GoalVerdict.INCONCLUSIVE and len(assessed_families) > 1 else next(iter(families), None) if one_family else None}
def _decision(snapshot: PolicySnapshot, command: str, owner_id: str, reason: str,
              payload: Mapping[str, Any] | None = None) -> PolicyDecision:
    selected = Command(command)
    if selected is Command.CREATE_EXPERIMENT:
        machine, state = Machine.EXPERIMENT_WORKFLOW, None
    elif owner_id == snapshot.run_id:
        machine, state = Machine.RUN_WORKFLOW, snapshot.run_state
    else:
        component = next((item for item in snapshot.components if item.experiment_id == owner_id), None)
        if component is None or component.experiment_state is None:
            raise ValueError(f"unknown policy owner: {owner_id!r}")
        machine, state = Machine.EXPERIMENT_WORKFLOW, component.experiment_state
    if selected not in legal_commands(machine, state):
        rendered = None if state is None else state.value
        raise ValueError(f"illegal policy command {selected.value} for {machine.value} {rendered}")
    return PolicyDecision(command=selected.value, owner_id=owner_id,
                          expected_revision=snapshot.revision_for(owner_id),
                          payload=dict(payload or {}), reason=reason)
__all__ = ["POLICY_VERSION", "ComponentSnapshot", "PolicyDecision", "PolicySnapshot",
           "ProposalSnapshot", "ResearchPolicy"]
