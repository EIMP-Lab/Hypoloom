"""Pure validation and construction boundary for the seven Research facts."""
from __future__ import annotations
import base64
import json
from dataclasses import dataclass
from typing import Any, Mapping
from .domain import (REVISIONABLE_INTEGRITY_VALIDATION_CODES,
    CanonicalOutcomeAccepted,
    ComponentTier,
    EvidenceLinked,
    ExperimentPreregistered,
    FactCandidate,
    GoalDefined,
    GoalVerdict,
    GoalVerdictRecorded,
    HypothesisAssessed,
    HypothesisProposed,
    ResearchContract,
    ResearchFactType,
    ScientificOutcome,
    fact_to_dict,
)
from .ports import ResearchStore
from .shared_acceptance import (SharedAcceptance, SharedAcceptanceError, acceptance_source_files,
    evaluate_shared_acceptance, normalize_goal_evaluation_binding, normalize_shared_observation, uses_grounding_evaluation)
class ResearchValidationError(ValueError):
    pass
class ResearchConflict(ResearchValidationError):
    pass
@dataclass(frozen=True, slots=True)
class FactReference:
    ref_fact_id: str
    role: str
    ordinal: int = 0
@dataclass(frozen=True, slots=True)
class FactMutation:
    fact_id: str
    run_id: str
    fact_type: str
    schema_version: int
    subject_id: str
    payload: Mapping[str, Any]
    refs: tuple[FactReference, ...] = ()
    goal_id: str | None = None
    hypothesis_id: str | None = None
    experiment_id: str | None = None
    execution_id: str | None = None
    outcome_id: str | None = None
    artifact_id: str | None = None
    def as_record(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "run_id": self.run_id,
            "fact_type": self.fact_type,
            "schema_version": self.schema_version,
            "subject_id": self.subject_id,
            "goal_id": self.goal_id,
            "hypothesis_id": self.hypothesis_id,
            "experiment_id": self.experiment_id,
            "execution_id": self.execution_id,
            "outcome_id": self.outcome_id,
            "artifact_id": self.artifact_id,
            "payload": dict(self.payload),
            "refs": [
                {
                    "ref_fact_id": ref.ref_fact_id,
                    "role": ref.role,
                    "ordinal": ref.ordinal,
                }
                for ref in self.refs
            ],
        }
def _need(view: ResearchStore, run_id: str, kind: ResearchFactType, subject: str,
          label: str) -> Mapping[str, Any]:
    row = view.find_fact(run_id, kind, subject)
    if row is None:
        raise ResearchValidationError(f"missing {label} {subject!r}")
    return row
def _ref(row: Mapping[str, Any], role: str, ordinal: int = 0) -> FactReference:
    return FactReference(str(row["fact_id"]), role, ordinal)
def _payload(row: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    payload = row.get("payload")
    if not isinstance(payload, Mapping):
        raise ResearchValidationError(f"{label} has no typed payload")
    return payload
def _grounding_acceptance(binding: Mapping[str, Any], view: ResearchStore, run_id: str) -> SharedAcceptance:
    """Bind one ordinary preregistration snapshot to its existing accepted source."""
    try:
        binding = normalize_goal_evaluation_binding(binding)
        selected = view.get_attempt(binding["source_attempt_id"])
        finals = []
        for row in view.list_attempts(workflow_id=run_id):
            if row["kind"] != "AGENT" or row["state"] != "CLOSED" or row["terminal_reason"] != "SUCCEEDED" or row["run_id"] != run_id:
                continue
            observation = json.loads(row["observation_json"])
            request = observation.get("request", {}); candidate = observation.get("response", {}).get("typed_candidate", {})
            if (request.get("command") == "GROUNDING" and isinstance(candidate, Mapping)
                and candidate.get("acceptance") is not None and candidate.get("background") is not None
                and candidate.get("clarification") is None):
                finals.append((row, observation))
        if selected is None or len(finals) != 1 or finals[0][0]["attempt_id"] != binding["source_attempt_id"]:
            raise ValueError("shared acceptance requires the unique successful final Grounding Attempt")
        row, observation = finals[0]; request = observation["request"]; response = observation["response"]
        if (row["workflow_id"] != run_id or request.get("run_id") != run_id or request.get("owner_workflow_id") != run_id
            or request.get("attempt_id") != row["attempt_id"] or request.get("command_id") != row["command_id"]
            or type(request.get("schema_version")) is not int or request["schema_version"] < 16):
            raise ValueError("shared acceptance source has inconsistent Grounding ownership/version")
        accepted = SharedAcceptance.from_dict(response["typed_candidate"]["acceptance"])
        if SharedAcceptance.from_dict(binding["acceptance"]) != accepted:
            raise ValueError("shared acceptance differs from the accepted Grounding declaration")
        files = {}
        for item in response.get("proposed_workspace_artifacts", ()):
            if item["path"] in files: raise ValueError("duplicate Grounding source path")
            files[item["path"]] = base64.b64decode(item["content_base64"], validate=True)
        acceptance_source_files(accepted, files)
        return accepted
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ResearchValidationError(f"invalid shared Grounding source: {exc}") from exc
def _assessment_goal_result(assessment: Mapping[str, Any], view: ResearchStore, run_id: str) -> bool | None:
    results = []
    for evidence_id in assessment["evidence_ids"]:
        evidence = _need(view, run_id, ResearchFactType.EVIDENCE_LINKED, str(evidence_id), "shared evidence")
        outcome = _need(view, run_id, ResearchFactType.CANONICAL_OUTCOME_ACCEPTED, str(evidence["outcome_id"]), "shared outcome")
        observation = _payload(outcome, "shared outcome").get("goal_evaluation")
        if observation is None: continue
        spec = _need(view, run_id, ResearchFactType.EXPERIMENT_PREREGISTERED, str(outcome["experiment_id"]), "shared experiment")
        binding = _payload(spec, "shared experiment").get("goal_evaluation")
        if not isinstance(binding, Mapping) or binding["scope"] != "GOAL" or observation["source_attempt_id"] != binding["source_attempt_id"]:
            raise ResearchValidationError("shared result differs from its GOAL preregistration")
        acceptance = _grounding_acceptance(binding, view, run_id)
        try:
            raw = normalize_shared_observation({name: observation[name] for name in ("measurements", "checks")}, acceptance)
            if all(raw["checks"][name] for name in acceptance.validity_checks):
                results.append(evaluate_shared_acceptance(acceptance, raw))
        except (KeyError, SharedAcceptanceError) as exc:
            raise ResearchValidationError(f"invalid shared result: {exc}") from exc
    return any(results) if results else None
def _goal_contract(goal: Mapping[str, Any]) -> ResearchContract:
    raw = _payload(goal, "GoalDefined").get("research_contract")
    if not isinstance(raw, Mapping):
        raise ResearchValidationError("GoalDefined lacks a research_contract")
    try:
        return ResearchContract.from_mapping(raw)
    except ValueError as exc:
        raise ResearchValidationError(f"GoalDefined research_contract is invalid: {exc}") from exc
def _component(contract: ResearchContract, component_id: str):
    component = contract.component(component_id)
    if component is None:
        raise ResearchValidationError(f"unknown component {component_id!r}")
    return component
def _logical_artifact_counts(outcome: Mapping[str, Any]) -> dict[str, int]:
    raw = _payload(outcome, "producer outcome").get("logical_artifacts")
    if not isinstance(raw, list): raise ResearchValidationError("producer outcome has invalid logical artifact inventory")
    counts: dict[str, int] = {}
    for item in raw:
        if not isinstance(item, Mapping): raise ResearchValidationError("producer outcome has invalid logical artifact inventory")
        logical_id = item.get("logical_artifact")
        paths = item.get("paths")
        if not isinstance(logical_id, str) or not isinstance(paths, list):
            raise ResearchValidationError("producer outcome has invalid logical artifact inventory")
        counts[logical_id] = len(paths)
    return counts
def _revision_ref(candidate: ExperimentPreregistered, view: ResearchStore, subject: str) -> FactReference | None:
    if candidate.revision_of_outcome_id is None: return None
    run_id = str(candidate.run_id); outcome_id = str(candidate.revision_of_outcome_id)
    outcome = _need(view, run_id, ResearchFactType.CANONICAL_OUTCOME_ACCEPTED, outcome_id, "predecessor outcome")
    predecessor_id = outcome.get("experiment_id")
    if not isinstance(predecessor_id, str): raise ResearchValidationError("revision predecessor outcome has no experiment identity")
    predecessor = _need(view, run_id, ResearchFactType.EXPERIMENT_PREREGISTERED, predecessor_id, "predecessor experiment")
    predecessor_payload = _payload(predecessor, "predecessor experiment"); outcome_payload = _payload(outcome, "predecessor outcome")
    terminals = tuple(row for row in view.list_facts(candidate.run_id, ResearchFactType.CANONICAL_OUTCOME_ACCEPTED) if row.get("experiment_id") == predecessor_id)
    if len(terminals) != 1 or terminals[0].get("subject_id") != outcome_id: raise ResearchValidationError("revision predecessor experiment must have exactly one terminal canonical outcome")
    lineage = (predecessor_id != subject, predecessor.get("hypothesis_id") == str(candidate.hypothesis_id),
        predecessor_payload.get("component_id") == outcome_payload.get("component_id") == str(candidate.component_id),
        predecessor_payload.get("method_family_id") == outcome_payload.get("method_family_id") == str(candidate.method_family_id),
        outcome.get("execution_id") == predecessor.get("execution_id"))
    if not all(lineage): raise ResearchValidationError("revision predecessor lineage differs from the successor")
    if outcome_payload.get("outcome") != ScientificOutcome.EXECUTION_FAILURE.value and not (outcome_payload.get("outcome") == ScientificOutcome.INTEGRITY_REJECTED.value and outcome_payload.get("validation_code") in REVISIONABLE_INTEGRITY_VALIDATION_CODES): raise ResearchValidationError("revision predecessor is not a canonical operational failure")
    for old in view.list_facts(candidate.run_id, ResearchFactType.EXPERIMENT_PREREGISTERED):
        if _payload(old, "existing preregistration").get("revision_of_outcome_id") == outcome_id and old.get("subject_id") != subject: raise ResearchConflict("revision predecessor already has a successor")
    return _ref(outcome, "revision_of_outcome")
class ResearchKernel:
    """Construct a storage-neutral mutation only after causal-reference checks."""
    def construct_fact(self, candidate: FactCandidate, view: ResearchStore) -> FactMutation:
        if not isinstance(candidate, (GoalDefined, HypothesisProposed, ExperimentPreregistered,
                CanonicalOutcomeAccepted, EvidenceLinked, HypothesisAssessed, GoalVerdictRecorded)):
            raise TypeError("candidate must be one of the seven typed Research facts")
        payload = fact_to_dict(candidate)
        run_id = str(candidate.run_id)
        refs: list[FactReference] = []
        index_fields: dict[str, str | None] = {"goal_id": None, "hypothesis_id": None,
            "experiment_id": None, "execution_id": None, "outcome_id": None, "artifact_id": None}
        if isinstance(candidate, GoalDefined):
            subject = str(candidate.goal_id)
            index_fields["goal_id"] = subject
            goals = view.list_facts(candidate.run_id, ResearchFactType.GOAL_DEFINED)
            if goals and goals[0]["subject_id"] != subject:
                raise ResearchConflict("run already has another GoalDefined fact")
        elif isinstance(candidate, HypothesisProposed):
            subject = str(candidate.hypothesis_id)
            index_fields.update(
                goal_id=str(candidate.goal_id), hypothesis_id=subject
            )
            goal = _need(view, run_id, ResearchFactType.GOAL_DEFINED, str(candidate.goal_id), "goal")
            contract = _goal_contract(goal)
            component = _component(contract, str(candidate.component_id))
            if component.deferred_measurement and not (uses_grounding_evaluation(contract.document) and candidate.measurement is None):
                if candidate.measurement is None: raise ResearchValidationError("deferred hypothesis requires its expert measurement")
                from .measurement_contract import assert_measurement_constraints
                raw_component = next(item for item in contract.document["components"] if item["id"] == str(candidate.component_id))
                try: assert_measurement_constraints(candidate.measurement, raw_component["result"])
                except ValueError as exc: raise ResearchValidationError(str(exc)) from exc
            elif candidate.measurement is not None:
                raise ResearchValidationError("static component cannot replace its measurement protocol")
            if candidate.proposer_role not in component.proposer_roles:
                raise ResearchValidationError(
                    "hypothesis proposer is not allowed for the selected component"
                )
            refs.append(_ref(goal, "goal"))
            if candidate.parent_hypothesis_id is not None:
                parent = _need(view, run_id, ResearchFactType.HYPOTHESIS_PROPOSED,
                               str(candidate.parent_hypothesis_id), "parent hypothesis")
                parent_payload = _payload(parent, "parent hypothesis")
                if (
                    parent.get("goal_id") != str(candidate.goal_id)
                    or parent_payload.get("component_id")
                    != str(candidate.component_id)
                ):
                    raise ResearchValidationError(
                        "parent hypothesis belongs to another goal or component"
                    )
                refs.append(_ref(parent, "parent_hypothesis"))
        elif isinstance(candidate, ExperimentPreregistered):
            subject = str(candidate.experiment_id)
            index_fields.update(
                hypothesis_id=str(candidate.hypothesis_id),
                experiment_id=subject,
                execution_id=str(candidate.execution_id),
            )
            hypothesis = _need(view, run_id, ResearchFactType.HYPOTHESIS_PROPOSED,
                               str(candidate.hypothesis_id), "hypothesis")
            hypothesis_payload = _payload(hypothesis, "hypothesis")
            goal = _need(view, run_id, ResearchFactType.GOAL_DEFINED, str(hypothesis["goal_id"]), "goal")
            contract = _goal_contract(goal)
            component = _component(contract, str(candidate.component_id))
            if hypothesis_payload.get("component_id") != str(candidate.component_id): raise ResearchValidationError("preregistration belongs to another hypothesis component")
            if hypothesis_payload.get("method_family_id") != str(candidate.method_family_id): raise ResearchValidationError("preregistration method-family lineage differs from hypothesis")
            revision_ref = _revision_ref(candidate, view, subject)
            grounded = uses_grounding_evaluation(contract.document)
            if grounded:
                if candidate.goal_evaluation is None: raise ResearchValidationError("Grounding commission requires a shared acceptance binding")
                _grounding_acceptance(candidate.goal_evaluation, view, run_id)
            elif candidate.goal_evaluation is not None:
                raise ResearchValidationError("legacy preregistration cannot adopt shared Grounding evaluation")
            goal_only = grounded and candidate.goal_evaluation["scope"] == "GOAL" and candidate.measurement is None
            if goal_only:
                if hypothesis_payload.get("measurement") is not None or candidate.metric is not None or candidate.result_kind.value != "artifact":
                    raise ResearchValidationError("GOAL-only experiment must omit local numeric measurement")
            elif component.deferred_measurement:
                if candidate.measurement is None or payload.get("measurement") != hypothesis_payload.get("measurement"):
                    raise ResearchValidationError("preregistered measurement differs from its declared hypothesis")
                if candidate.metric != candidate.measurement.metric or candidate.result_kind is not component.result_kind:
                    raise ResearchValidationError("preregistration metric differs from its measurement declaration")
                for original in view.list_facts(candidate.run_id, ResearchFactType.EXPERIMENT_PREREGISTERED):
                    if (original.get("hypothesis_id") == str(candidate.hypothesis_id)
                            and _payload(original, "first preregistration").get("measurement") != payload.get("measurement")):
                        raise ResearchValidationError("measurement is frozen by the hypothesis first preregistration")
            elif candidate.measurement is not None or candidate.result_kind is not component.result_kind or candidate.metric != component.metric:
                raise ResearchValidationError("preregistration result differs from GoalDefined component result")
            expected_trial_plan = contract.trial_plan_for(str(candidate.component_id))
            if candidate.trial_plan != expected_trial_plan: raise ResearchValidationError("preregistration trial plan differs from GoalDefined component")
            if candidate.required_artifacts != component.required_artifacts: raise ResearchValidationError("preregistration required artifacts differ from GoalDefined component")
            expected_inputs = tuple(
                (
                    item.resource,
                    str(item.produced_by_component),
                    item.logical_artifact,
                    item.min_count,
                )
                for item in component.required_inputs
            )
            actual_inputs = tuple(
                (
                    item.resource,
                    str(item.produced_by_component),
                    item.logical_artifact,
                    item.min_count,
                )
                for item in candidate.upstream_outcomes
            )
            if actual_inputs != expected_inputs:
                raise ResearchValidationError(
                    "preregistration upstream bindings differ from GoalDefined required inputs"
                )
            refs.append(_ref(hypothesis, "hypothesis"))
            if revision_ref is not None:
                refs.append(revision_ref)
            referenced_outcomes: set[str] = set()
            selected_producers: dict[str, tuple[str, str]] = {}
            for binding in candidate.upstream_outcomes:
                producer_id = str(binding.produced_by_component)
                selection = (str(binding.outcome_id), str(binding.artifact_id))
                prior = selected_producers.setdefault(producer_id, selection)
                if prior != selection:
                    raise ResearchValidationError(
                        "one producer component cannot bind multiple outcome publications"
                    )
                outcome = _need(view, run_id, ResearchFactType.CANONICAL_OUTCOME_ACCEPTED,
                                str(binding.outcome_id), "producer outcome")
                outcome_payload = _payload(outcome, "producer outcome")
                if outcome_payload.get("component_id") != str(
                    binding.produced_by_component
                ):
                    raise ResearchValidationError(
                        "producer outcome belongs to another component"
                    )
                if outcome.get("artifact_id") != str(binding.artifact_id):
                    raise ResearchValidationError(
                        "producer binding names another artifact publication"
                    )
                if outcome_payload.get("method_family_id") != str(
                    candidate.method_family_id
                ):
                    raise ResearchValidationError(
                        "producer outcome has inconsistent method-family lineage"
                    )
                if outcome_payload.get("outcome") not in {
                    ScientificOutcome.SUCCESS.value,
                    ScientificOutcome.CLEAN_REFUTATION.value,
                }:
                    raise ResearchValidationError(
                        "producer outcome is not a comparable accepted result"
                    )
                producer_spec = _need(view, run_id, ResearchFactType.EXPERIMENT_PREREGISTERED,
                                      str(outcome["experiment_id"]), "producer experiment")
                producer_spec_payload = _payload(producer_spec, "producer experiment")
                if (
                    producer_spec_payload.get("component_id")
                    != str(binding.produced_by_component)
                    or producer_spec_payload.get("method_family_id")
                    != str(candidate.method_family_id)
                ):
                    raise ResearchValidationError(
                        "producer outcome and preregistration lineage disagree"
                    )
                counts = _logical_artifact_counts(outcome)
                if counts.get(binding.logical_artifact, 0) < binding.min_count:
                    raise ResearchValidationError(
                        "producer outcome lacks the required logical artifact count"
                    )
                outcome_id = str(binding.outcome_id)
                if outcome_id not in referenced_outcomes:
                    refs.append(_ref(outcome, "producer_outcome", len(referenced_outcomes)))
                    referenced_outcomes.add(outcome_id)
            for old in view.list_facts(candidate.run_id, ResearchFactType.EXPERIMENT_PREREGISTERED):
                if (
                    old["execution_id"] == str(candidate.execution_id)
                    and old["subject_id"] != subject
                ):
                    raise ResearchConflict(
                        "execution_id already belongs to another experiment"
                    )
        elif isinstance(candidate, CanonicalOutcomeAccepted):
            subject = str(candidate.outcome_id)
            index_fields.update(
                experiment_id=str(candidate.experiment_id),
                execution_id=str(candidate.execution_id),
                outcome_id=subject,
                artifact_id=str(candidate.artifact_id),
            )
            spec = _need(view, run_id, ResearchFactType.EXPERIMENT_PREREGISTERED,
                         str(candidate.experiment_id), "experiment")
            spec_payload = _payload(spec, "preregistration")
            if candidate.goal_evaluation is not None:
                if candidate.outcome in {ScientificOutcome.INTEGRITY_REJECTED, ScientificOutcome.CANCELLED}:
                    raise ResearchValidationError("rejected or cancelled outcome cannot carry shared acceptance observations")
                binding = spec_payload.get("goal_evaluation")
                if not isinstance(binding, Mapping) or binding["scope"] != "GOAL":
                    raise ResearchValidationError("shared outcome requires an explicit GOAL preregistration")
                if candidate.goal_evaluation["source_attempt_id"] != binding["source_attempt_id"]:
                    raise ResearchValidationError("shared outcome source differs from preregistration")
                acceptance = _grounding_acceptance(binding, view, run_id)
                try: normalize_shared_observation({name: candidate.goal_evaluation[name] for name in ("measurements", "checks")}, acceptance)
                except SharedAcceptanceError as exc: raise ResearchValidationError(str(exc)) from exc
            if (
                spec["execution_id"] != str(candidate.execution_id)
                or spec_payload.get("evaluator_version")
                != candidate.evaluator_version
            ):
                raise ResearchValidationError(
                    "outcome execution/evaluator differs from preregistration"
                )
            if (
                spec_payload.get("component_id") != str(candidate.component_id)
                or spec_payload.get("method_family_id")
                != str(candidate.method_family_id)
                or spec_payload.get("result_kind") != candidate.result_kind.value
            ):
                raise ResearchValidationError(
                    "outcome component or method-family lineage differs from preregistration"
                )
            expected_artifacts = spec_payload.get("required_artifacts")
            actual_artifacts = [artifact.logical_artifact for artifact in candidate.logical_artifacts]
            comparable = candidate.outcome in {
                ScientificOutcome.SUCCESS,
                ScientificOutcome.CLEAN_REFUTATION,
            }
            if (comparable and expected_artifacts != actual_artifacts) or (not comparable
                    and not set(actual_artifacts).issubset(set(expected_artifacts or ()))):
                raise ResearchValidationError(
                    "outcome logical artifacts differ from preregistration"
                )
            refs.append(_ref(spec, "experiment"))
        elif isinstance(candidate, EvidenceLinked):
            subject = str(candidate.evidence_id)
            index_fields.update(
                hypothesis_id=str(candidate.hypothesis_id),
                outcome_id=str(candidate.outcome_id),
                artifact_id=str(candidate.artifact_id),
            )
            hypothesis = _need(view, run_id, ResearchFactType.HYPOTHESIS_PROPOSED,
                               str(candidate.hypothesis_id), "hypothesis")
            outcome = _need(view, run_id, ResearchFactType.CANONICAL_OUTCOME_ACCEPTED,
                            str(candidate.outcome_id), "outcome")
            spec = _need(view, run_id, ResearchFactType.EXPERIMENT_PREREGISTERED,
                         str(outcome["experiment_id"]), "experiment")
            hypothesis_payload = _payload(hypothesis, "hypothesis")
            outcome_payload = _payload(outcome, "outcome")
            if (
                spec["hypothesis_id"] != str(candidate.hypothesis_id)
                or outcome["artifact_id"] != str(candidate.artifact_id)
                or hypothesis_payload.get("component_id")
                != str(candidate.component_id)
                or outcome_payload.get("component_id")
                != str(candidate.component_id)
                or hypothesis_payload.get("method_family_id")
                != str(candidate.method_family_id)
                or outcome_payload.get("method_family_id")
                != str(candidate.method_family_id)
            ):
                raise ResearchValidationError(
                    "evidence does not match outcome component/artifact lineage"
                )
            refs.extend((_ref(hypothesis, "hypothesis"), _ref(outcome, "outcome")))
        elif isinstance(candidate, HypothesisAssessed):
            subject = str(candidate.assessment_id)
            index_fields["hypothesis_id"] = str(candidate.hypothesis_id)
            hypothesis = _need(view, run_id, ResearchFactType.HYPOTHESIS_PROPOSED,
                               str(candidate.hypothesis_id), "hypothesis")
            hypothesis_payload = _payload(hypothesis, "hypothesis")
            if (
                hypothesis_payload.get("component_id")
                != str(candidate.component_id)
                or hypothesis_payload.get("method_family_id")
                != str(candidate.method_family_id)
            ):
                raise ResearchValidationError(
                    "assessment differs from hypothesis component/method-family lineage"
                )
            refs.append(_ref(hypothesis, "hypothesis"))
            for ordinal, evidence_id in enumerate(candidate.evidence_ids):
                evidence = _need(view, run_id, ResearchFactType.EVIDENCE_LINKED,
                                 str(evidence_id), "evidence")
                evidence_payload = _payload(evidence, "evidence")
                if (
                    evidence["hypothesis_id"] != str(candidate.hypothesis_id)
                    or evidence_payload.get("component_id")
                    != str(candidate.component_id)
                    or evidence_payload.get("method_family_id")
                    != str(candidate.method_family_id)
                ):
                    raise ResearchValidationError(
                        "assessment cites evidence for another hypothesis or component lineage"
                    )
                refs.append(_ref(evidence, "evidence", ordinal))
        else:
            subject = str(candidate.verdict_id)
            index_fields["goal_id"] = str(candidate.goal_id)
            goal = _need(view, run_id, ResearchFactType.GOAL_DEFINED, str(candidate.goal_id), "goal")
            contract = _goal_contract(goal)
            grounded = uses_grounding_evaluation(contract.document)
            shared_goal = grounded or contract.document.get("methodology", {}).get("controls", {}).get("product", {}).get("goal", {}).get("completion_mode") == "shared_goal"
            refs.append(_ref(goal, "goal"))
            assessed_components: set[str] = set()
            assessment_values: list[Mapping[str, Any]] = []
            for ordinal, assessment_id in enumerate(candidate.assessment_ids):
                assessment = _need(view, run_id, ResearchFactType.HYPOTHESIS_ASSESSED,
                                   str(assessment_id), "assessment")
                assessment_payload = _payload(assessment, "assessment")
                hypothesis = _need(view, run_id, ResearchFactType.HYPOTHESIS_PROPOSED,
                                   str(assessment["hypothesis_id"]), "hypothesis")
                component_id = assessment_payload.get("component_id")
                if (
                    hypothesis["goal_id"] != str(candidate.goal_id)
                    or not isinstance(component_id, str)
                    or contract.component(component_id) is None
                    or (
                        candidate.method_family_id is not None
                        and assessment_payload.get("method_family_id")
                        != candidate.method_family_id
                    )
                ):
                    raise ResearchValidationError(
                        "verdict cites assessment for another goal/component lineage"
                    )
                if component_id in assessed_components:
                    raise ResearchValidationError(
                        "verdict cites multiple assessments for one component"
                    )
                assessed_components.add(component_id)
                assessment_values.append(assessment_payload)
                refs.append(_ref(assessment, "assessment", ordinal))
            if candidate.verdict is not GoalVerdict.INCONCLUSIVE:
                required = {str(component.component_id) for component in contract.components
                            if component.tier in {ComponentTier.PRIMARY, ComponentTier.MUST}}
                if not shared_goal and candidate.method_family_id is None:
                    raise ResearchValidationError("legacy conclusive verdict requires its method family")
                if shared_goal and candidate.verdict is GoalVerdict.GOAL_MET:
                    if len(assessment_values) != 1 or not assessed_components <= required:
                        raise ResearchValidationError("shared goal winner must cite one exact alternative")
                    if grounded:
                        if _assessment_goal_result(assessment_values[0], view, run_id) is not True:
                            raise ResearchValidationError("shared goal winner lacks valid common acceptance evidence")
                    elif assessment_values[0].get("assessment") != "SUPPORTED":
                        raise ResearchValidationError("shared goal winner must cite one actual SUPPORTED alternative")
                elif assessed_components != required:
                    raise ResearchValidationError(
                        "conclusive verdict must cite every required component assessment"
                    )
                if shared_goal and candidate.verdict is GoalVerdict.GOAL_NOT_MET:
                    families = {item.get("method_family_id") for item in assessment_values}
                    expected_family = next(iter(families)) if len(families) == 1 else None
                    refuted = (all(_assessment_goal_result(item, view, run_id) is False for item in assessment_values) if grounded
                        else all(item.get("assessment") == "REFUTED" for item in assessment_values))
                    if not refuted or candidate.method_family_id != expected_family:
                        raise ResearchValidationError("shared goal refutation requires every scientifically refuted alternative and its exact aggregate family")
        old = view.find_fact(candidate.run_id, candidate.fact_type, subject)
        if old is not None and old["fact_id"] != str(candidate.fact_id):
            raise ResearchConflict(
                f"{candidate.fact_type.value} subject already has another fact_id"
            )
        by_id = view.get_fact(candidate.fact_id)
        if by_id is not None and (
            by_id["fact_type"] != candidate.fact_type.value
            or by_id["subject_id"] != subject
            or by_id["payload"] != payload
        ):
            raise ResearchConflict(
                f"fact_id {candidate.fact_id!r} already has different scientific content"
            )
        return FactMutation(str(candidate.fact_id), run_id, candidate.fact_type.value,
            candidate.schema_version, subject, payload, tuple(refs), **index_fields)
    construct = construct_fact
