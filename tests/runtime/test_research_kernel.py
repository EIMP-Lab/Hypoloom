from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

import pytest

from hypoloom.domain import (
    CanonicalOutcomeAccepted,
    ComponentResultKind,
    EvidenceLinked,
    ExecutionContract,
    ExperimentPreregistered,
    GoalDefined,
    GoalVerdict,
    GoalVerdictRecorded,
    HypothesisAssessment,
    HypothesisAssessed,
    HypothesisProposed,
    LogicalArtifact,
    MetricDirection,
    MetricSpec,
    ProducerOutcomeBinding,
    ResearchFactType,
    ResourceGrant,
    ResourceKind,
    ResourcePreference,
    ScientificOutcome,
    TrialPlan,
)
from hypoloom.research_kernel import (
    FactMutation,
    ResearchConflict,
    ResearchKernel,
    ResearchValidationError,
)


class MemoryResearchView:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add(self, mutation: FactMutation) -> None:
        row = mutation.as_record()
        row["refs"] = list(row["refs"])
        self.rows.append(row)

    def get_fact(self, fact_id: str) -> Mapping[str, Any] | None:
        return next((row for row in self.rows if row["fact_id"] == fact_id), None)

    def find_fact(
        self,
        run_id: str,
        fact_type: ResearchFactType,
        subject_id: str | None = None,
    ) -> Mapping[str, Any] | None:
        expected = (
            fact_type.value if isinstance(fact_type, ResearchFactType) else fact_type
        )
        return next(
            (
                row
                for row in self.rows
                if row["run_id"] == run_id
                and row["fact_type"] == expected
                and (subject_id is None or row["subject_id"] == subject_id)
            ),
            None,
        )

    def list_facts(
        self, run_id: str, fact_type: ResearchFactType | None = None
    ) -> list[Mapping[str, Any]]:
        expected = (
            fact_type.value if isinstance(fact_type, ResearchFactType) else fact_type
        )
        return [
            row
            for row in self.rows
            if row["run_id"] == run_id
            and (expected is None or row["fact_type"] == expected)
        ]


def _role() -> dict[str, object]:
    return {
        "id": "researcher",
        "focus": "",
        "autonomy": None,
        "protocols": {"default": None, "allowed": []},
        "budget": {},
    }


def _contract(
    components: list[dict[str, object]], protocols: dict[str, object]
) -> dict[str, object]:
    required = [item for item in components if item["tier"] in {"primary", "must"}]
    only = required[0] if len(required) == 1 else None
    result = None if only is None else only["result"]
    if isinstance(result, dict) and result.get("kind") == "metric":
        summary = {
            "name": result["metric"],
            "direction": result["direction"],
            "target": result["target"],
            "display_only": True,
        }
    else:
        summary = {
            "name": "required_component_completion",
            "direction": "MAXIMIZE",
            "target": str(len(required)),
            "display_only": True,
        }
    return {
        "schema_version": 1,
        "goal": {
            "objective": "Exercise typed component lineage",
            "language": "",
            "verdict_rule": "required_conjunction",
            "summary_metric": summary,
        },
        "roles": [_role()],
        "support_role_budgets": {},
        "components": components,
        "protocols": protocols,
        "delivery_contracts": {
            "enabled": False,
            "schema_version": None,
            "protocols": {},
        },
        "methodology": {"type": "hg", "tier": None, "controls": {}},
        "runtime": {},
        "budget": {},
        "safety": {"read_roots": [], "sandbox": None},
        "observer": {
            "notify": {"channels": [], "digest": False, "digest_model": None}
        },
    }


def scalar_contract() -> dict[str, object]:
    return _contract(
        [
            {
                "id": "primary.score",
                "tier": "primary",
                "name": "score",
                "owner": "researcher",
                "proposers": ["researcher"],
                "dependencies": [],
                "required_inputs": [],
                "protocol": None,
                "result": {
                    "kind": "metric",
                    "metric": "score",
                    "direction": "MAXIMIZE",
                    "target": "1",
                },
                "seeds": [],
                "required_artifacts": [],
            }
        ],
        {},
    )


def producer_contract() -> dict[str, object]:
    return _contract(
        [
            {
                "id": "primary.score",
                "tier": "primary",
                "name": "score",
                "owner": "researcher",
                "proposers": ["researcher"],
                "dependencies": ["must.bundle"],
                "required_inputs": [
                    {
                        "resource": "bundle-input",
                        "produced_by": "must.bundle",
                        "artifact": "bundle",
                        "min_count": 2,
                    }
                ],
                "protocol": "score",
                "result": {
                    "kind": "metric",
                    "metric": "score",
                    "direction": "MAXIMIZE",
                    "target": "1",
                },
                "seeds": [0, 1],
                "required_artifacts": ["metrics"],
            },
            {
                "id": "must.bundle",
                "tier": "must",
                "name": "bundle",
                "owner": "researcher",
                "proposers": ["researcher"],
                "dependencies": [],
                "required_inputs": [],
                "protocol": "bundle",
                "result": {"kind": "artifact", "evaluation_kind": "artifact_check"},
                "seeds": [],
                "required_artifacts": ["bundle"],
            },
        ],
        {
            "score": {"result_kind": "metric", "requirements": {}},
            "bundle": {"result_kind": "artifact", "requirements": {}},
        },
    )


def execution_contract(attempt: str = "agent-attempt-1") -> ExecutionContract:
    return ExecutionContract(
        source_agent_attempt_id=attempt,
        source_agent_output_digest="b" * 64,
        argv=("python", "experiment.py"),
        environment=(("RESULT_PATH", "result.json"),),
        data_splits=("split-v1",),
        sandbox_profile="bwrap-cpu-no-network/2",
        timeout_seconds=10,
        resource_preference=ResourcePreference.CPU,
        resource_grant=ResourceGrant(kind=ResourceKind.CPU),
    )


def candidates() -> tuple[object, ...]:
    digest = "a" * 64
    metric = MetricSpec(
        name="score", direction=MetricDirection.MAXIMIZE, target="1"
    )
    return (
        GoalDefined(
            fact_id="fact-goal",
            run_id="run-1",
            goal_id="goal-1",
            research_contract=scalar_contract(),  # type: ignore[arg-type]
        ),
        HypothesisProposed(
            fact_id="fact-hypothesis",
            run_id="run-1",
            hypothesis_id="hypothesis-1",
            goal_id="goal-1",
            component_id="primary.score",
            proposer_role="researcher",
            method_family_id="family-1",
            claim="The change improves score",
        ),
        ExperimentPreregistered(
            fact_id="fact-experiment",
            run_id="run-1",
            experiment_id="experiment-1",
            hypothesis_id="hypothesis-1",
            execution_id="execution-1",
            component_id="primary.score",
            method_family_id="family-1",
            method="Run the deterministic fixture",
            result_kind=ComponentResultKind.METRIC,
            metric=metric,
            trial_plan=TrialPlan(),
            acceptance_criteria=("score >= 1",),
            evaluator_version="evaluator-1",
            required_artifacts=(),
            upstream_outcomes=(),
            resource_preference=ResourcePreference.CPU,
            execution_contract=execution_contract(),
        ),
        CanonicalOutcomeAccepted(
            fact_id="fact-outcome",
            run_id="run-1",
            outcome_id="outcome-1",
            experiment_id="experiment-1",
            execution_id="execution-1",
            artifact_id="artifact-1",
            component_id="primary.score",
            method_family_id="family-1",
            result_kind=ComponentResultKind.METRIC,
            outcome=ScientificOutcome.SUCCESS,
            artifact_set_digest=digest,
            evaluator_version="evaluator-1",
            metric_value="1",
            logical_artifacts=(),
        ),
        EvidenceLinked(
            fact_id="fact-evidence",
            run_id="run-1",
            evidence_id="evidence-1",
            hypothesis_id="hypothesis-1",
            outcome_id="outcome-1",
            artifact_id="artifact-1",
            component_id="primary.score",
            method_family_id="family-1",
            evidence_kind="deterministic_metric",
        ),
        HypothesisAssessed(
            fact_id="fact-assessment",
            run_id="run-1",
            assessment_id="assessment-1",
            hypothesis_id="hypothesis-1",
            component_id="primary.score",
            method_family_id="family-1",
            evidence_ids=("evidence-1",),
            assessment=HypothesisAssessment.SUPPORTED,
        ),
        GoalVerdictRecorded(
            fact_id="fact-verdict",
            run_id="run-1",
            verdict_id="verdict-1",
            goal_id="goal-1",
            method_family_id="family-1",
            assessment_ids=("assessment-1",),
            verdict=GoalVerdict.GOAL_MET,
        ),
    )


def build_until(
    count: int,
) -> tuple[ResearchKernel, MemoryResearchView, tuple[object, ...]]:
    kernel = ResearchKernel()
    view = MemoryResearchView()
    facts = candidates()
    for candidate in facts[:count]:
        view.add(kernel.construct_fact(candidate, view))  # type: ignore[arg-type]
    return kernel, view, facts


def _add(view: MemoryResearchView, kernel: ResearchKernel, *facts: object) -> None:
    for fact in facts:
        view.add(kernel.construct_fact(fact, view))  # type: ignore[arg-type]


def _component_hypothesis(
    component_id: str, *, suffix: str, family: str = "family-1"
) -> HypothesisProposed:
    return HypothesisProposed(
        fact_id=f"fact-hypothesis-{suffix}",
        run_id="run-1",
        hypothesis_id=f"hypothesis-{suffix}",
        goal_id="goal-1",
        component_id=component_id,
        proposer_role="researcher",
        method_family_id=family,
        claim=f"Claim for {component_id}",
    )


def _bundle_spec(*, suffix: str, family: str = "family-1") -> ExperimentPreregistered:
    return ExperimentPreregistered(
        fact_id=f"fact-experiment-{suffix}",
        run_id="run-1",
        experiment_id=f"experiment-{suffix}",
        hypothesis_id=f"hypothesis-{suffix}",
        execution_id=f"execution-{suffix}",
        component_id="must.bundle",
        method_family_id=family,
        method="Produce the immutable bundle",
        result_kind=ComponentResultKind.ARTIFACT,
        metric=None,
        trial_plan=TrialPlan(),
        acceptance_criteria=("bundle validates",),
        evaluator_version="evaluator-1",
        required_artifacts=("bundle",),
        upstream_outcomes=(),
        resource_preference=ResourcePreference.CPU,
        execution_contract=execution_contract(f"agent-attempt-{suffix}"),
    )


def _bundle_outcome(
    *,
    suffix: str,
    family: str = "family-1",
    paths: tuple[str, ...] = ("a", "b"),
    outcome: ScientificOutcome = ScientificOutcome.SUCCESS,
) -> CanonicalOutcomeAccepted:
    return CanonicalOutcomeAccepted(
        fact_id=f"fact-outcome-{suffix}",
        run_id="run-1",
        outcome_id=f"outcome-{suffix}",
        experiment_id=f"experiment-{suffix}",
        execution_id=f"execution-{suffix}",
        artifact_id=f"artifact-{suffix}",
        component_id="must.bundle",
        method_family_id=family,
        result_kind=ComponentResultKind.ARTIFACT,
        outcome=outcome,
        artifact_set_digest="c" * 64,
        evaluator_version="evaluator-1",
        metric_value=None,
        logical_artifacts=(LogicalArtifact(logical_artifact="bundle", paths=paths),),
    )


def _consumer_spec(*, outcome_id: str, family: str = "family-1") -> ExperimentPreregistered:
    return ExperimentPreregistered(
        fact_id="fact-experiment-consumer",
        run_id="run-1",
        experiment_id="experiment-consumer",
        hypothesis_id="hypothesis-consumer",
        execution_id="execution-consumer",
        component_id="primary.score",
        method_family_id=family,
        method="Consume the accepted bundle",
        result_kind=ComponentResultKind.METRIC,
        metric=MetricSpec(
            name="score", direction=MetricDirection.MAXIMIZE, target="1"
        ),
        trial_plan=TrialPlan(seeds=(0, 1)),
        acceptance_criteria=("score >= 1",),
        evaluator_version="evaluator-1",
        required_artifacts=("metrics",),
        upstream_outcomes=(
            ProducerOutcomeBinding(
                resource="bundle-input",
                produced_by_component="must.bundle",
                logical_artifact="bundle",
                min_count=2,
                outcome_id=outcome_id,
                artifact_id="artifact-producer",
            ),
        ),
        resource_preference=ResourcePreference.CPU,
        execution_contract=execution_contract("agent-attempt-consumer"),
    )


def _producer_view(
    *,
    family: str = "family-1",
    paths: tuple[str, ...] = ("a", "b"),
    outcome: ScientificOutcome = ScientificOutcome.SUCCESS,
) -> tuple[ResearchKernel, MemoryResearchView]:
    kernel = ResearchKernel()
    view = MemoryResearchView()
    goal = GoalDefined(
        fact_id="fact-goal",
        run_id="run-1",
        goal_id="goal-1",
        research_contract=producer_contract(),  # type: ignore[arg-type]
    )
    producer_hypothesis = _component_hypothesis(
        "must.bundle", suffix="producer", family=family
    )
    _add(
        view,
        kernel,
        goal,
        producer_hypothesis,
        _bundle_spec(suffix="producer", family=family),
        _bundle_outcome(
            suffix="producer", family=family, paths=paths, outcome=outcome
        ),
        _component_hypothesis(
            "primary.score", suffix="consumer", family="family-1"
        ),
    )
    return kernel, view


def test_seven_fact_chain_is_typed_explicit_and_causally_linked() -> None:
    kernel, view, facts = build_until(0)
    observed: list[FactMutation] = []
    for candidate in facts:
        mutation = kernel.construct_fact(candidate, view)  # type: ignore[arg-type]
        view.add(mutation)
        observed.append(mutation)

    assert [mutation.fact_type for mutation in observed] == [
        fact_type.value for fact_type in ResearchFactType
    ]
    assert observed[0].refs == ()
    assert [(ref.role, ref.ref_fact_id) for ref in observed[3].refs] == [
        ("experiment", "fact-experiment")
    ]
    assert [(ref.role, ref.ref_fact_id) for ref in observed[4].refs] == [
        ("hypothesis", "fact-hypothesis"),
        ("outcome", "fact-outcome"),
    ]
    assert [(ref.role, ref.ordinal) for ref in observed[6].refs] == [
        ("goal", 0),
        ("assessment", 0),
    ]
    assert observed[3].payload["component_id"] == "primary.score"
    assert observed[6].subject_id == "verdict-1"


def test_kernel_rejects_untyped_or_operational_fact_payloads() -> None:
    kernel = ResearchKernel()
    with pytest.raises(TypeError, match="seven typed"):
        kernel.construct_fact(  # type: ignore[arg-type]
            {
                "fact_type": "GoalDefined",
                "fact_id": "fact-goal",
                "run_id": "run-1",
                "pid": 1234,
            },
            MemoryResearchView(),
        )


def test_missing_unknown_component_and_wrong_proposer_are_rejected() -> None:
    kernel, view, facts = build_until(0)
    with pytest.raises(ResearchValidationError, match="missing goal"):
        kernel.construct_fact(facts[1], view)  # type: ignore[arg-type]
    view.add(kernel.construct_fact(facts[0], view))  # type: ignore[arg-type]
    with pytest.raises(ResearchValidationError, match="unknown component"):
        kernel.construct_fact(
            replace(facts[1], component_id="primary.ghost"), view  # type: ignore[arg-type]
        )
    with pytest.raises(ResearchValidationError, match="proposer"):
        kernel.construct_fact(
            replace(facts[1], proposer_role="reviewer"), view  # type: ignore[arg-type]
        )
    other = replace(facts[0], fact_id="other-fact", goal_id="other-goal")  # type: ignore[arg-type]
    with pytest.raises(ResearchConflict, match="another GoalDefined"):
        kernel.construct_fact(other, view)


def test_preregistration_rejects_component_method_trial_and_artifact_drift() -> None:
    kernel, view, facts = build_until(2)
    cases = (
        (replace(facts[2], component_id="must.ghost"), "component"),
        (replace(facts[2], method_family_id="family-2"), "method-family"),
        (replace(facts[2], trial_plan=TrialPlan(trial_ids=("extra",))), "trial plan"),
        (replace(facts[2], required_artifacts=("extra",)), "required artifacts"),
    )
    for candidate, message in cases:
        with pytest.raises(ResearchValidationError, match=message):
            kernel.construct_fact(candidate, view)  # type: ignore[arg-type]


def test_preregistration_execution_identity_is_not_reassigned() -> None:
    kernel, view, facts = build_until(3)
    other_hypothesis = replace(
        facts[1],
        fact_id="fact-hypothesis-2",
        hypothesis_id="hypothesis-2",
    )
    view.add(kernel.construct_fact(other_hypothesis, view))  # type: ignore[arg-type]
    other_experiment = replace(
        facts[2],
        fact_id="fact-experiment-2",
        experiment_id="experiment-2",
        hypothesis_id="hypothesis-2",
    )
    with pytest.raises(ResearchConflict, match="execution_id"):
        kernel.construct_fact(other_experiment, view)  # type: ignore[arg-type]


def _successor_spec(spec: object, **changes: object) -> ExperimentPreregistered:
    values = {
        "fact_id": "fact-experiment-2",
        "experiment_id": "experiment-2",
        "execution_id": "execution-2",
        "revision_of_outcome_id": "outcome-1",
    }
    values.update(changes)
    return replace(spec, **values)  # type: ignore[arg-type,return-value]


def _failed_predecessor() -> tuple[
    ResearchKernel, MemoryResearchView, tuple[object, ...]
]:
    kernel, view, facts = build_until(3)
    failed = replace(
        facts[3],
        outcome=ScientificOutcome.EXECUTION_FAILURE,
        metric_value=None,
    )
    view.add(kernel.construct_fact(failed, view))  # type: ignore[arg-type]
    return kernel, view, facts


def test_revision_preregistration_binds_one_terminal_failed_predecessor() -> None:
    kernel, view, facts = _failed_predecessor()

    mutation = kernel.construct_fact(_successor_spec(facts[2]), view)

    assert mutation.payload["revision_of_outcome_id"] == "outcome-1"
    assert [(ref.role, ref.ref_fact_id) for ref in mutation.refs] == [
        ("hypothesis", "fact-hypothesis"),
        ("revision_of_outcome", "fact-outcome"),
    ]


def test_revision_preregistration_rejects_unknown_or_nonfailure_predecessor() -> None:
    kernel, view, facts = build_until(3)
    with pytest.raises(ResearchValidationError, match="missing predecessor outcome"):
        kernel.construct_fact(
            _successor_spec(facts[2], revision_of_outcome_id="outcome-missing"),
            view,
        )

    view.add(kernel.construct_fact(facts[3], view))  # type: ignore[arg-type]
    with pytest.raises(ResearchValidationError, match="operational failure"):
        kernel.construct_fact(_successor_spec(facts[2]), view)


def test_revision_preregistration_rejects_cross_hypothesis_and_branching() -> None:
    kernel, view, facts = _failed_predecessor()
    other_hypothesis = replace(
        facts[1],
        fact_id="fact-hypothesis-2",
        hypothesis_id="hypothesis-2",
    )
    view.add(kernel.construct_fact(other_hypothesis, view))  # type: ignore[arg-type]
    with pytest.raises(ResearchValidationError, match="predecessor lineage"):
        kernel.construct_fact(
            _successor_spec(facts[2], hypothesis_id="hypothesis-2"),
            view,
        )

    first = kernel.construct_fact(_successor_spec(facts[2]), view)
    view.add(first)
    assert kernel.construct_fact(_successor_spec(facts[2]), view) == first
    with pytest.raises(ResearchConflict, match="already has a successor"):
        kernel.construct_fact(
            _successor_spec(
                facts[2],
                fact_id="fact-experiment-3",
                experiment_id="experiment-3",
                execution_id="execution-3",
            ),
            view,
        )


@pytest.mark.parametrize(
    ("fact_type", "location", "value"),
    [
        (ResearchFactType.EXPERIMENT_PREREGISTERED, "component_id", "primary.other"),
        (ResearchFactType.CANONICAL_OUTCOME_ACCEPTED, "method_family_id", "family-2"),
        (ResearchFactType.CANONICAL_OUTCOME_ACCEPTED, "execution_id", "execution-other"),
    ],
)
def test_revision_preregistration_rejects_corrupt_predecessor_lineage(
    fact_type: ResearchFactType, location: str, value: str
) -> None:
    kernel, view, facts = _failed_predecessor()
    subject = "experiment-1" if fact_type is ResearchFactType.EXPERIMENT_PREREGISTERED else "outcome-1"
    row = view.find_fact("run-1", fact_type, subject)
    assert isinstance(row, dict)
    if location == "execution_id":
        row[location] = value
    else:
        row["payload"][location] = value

    with pytest.raises(ResearchValidationError, match="predecessor lineage"):
        kernel.construct_fact(_successor_spec(facts[2]), view)


def test_revision_preregistration_requires_same_run_and_unique_terminal() -> None:
    kernel, view, facts = _failed_predecessor()
    outcome = view.find_fact(
        "run-1", ResearchFactType.CANONICAL_OUTCOME_ACCEPTED, "outcome-1"
    )
    assert isinstance(outcome, dict)
    outcome["run_id"] = "run-other"
    with pytest.raises(ResearchValidationError, match="missing predecessor outcome"):
        kernel.construct_fact(_successor_spec(facts[2]), view)

    outcome["run_id"] = "run-1"
    duplicate = {**outcome, "fact_id": "fact-outcome-duplicate", "subject_id": "outcome-duplicate",
                 "outcome_id": "outcome-duplicate"}
    duplicate["payload"] = {
        **outcome["payload"],
        "fact_id": "fact-outcome-duplicate",
        "outcome_id": "outcome-duplicate",
    }
    view.rows.append(duplicate)
    with pytest.raises(ResearchValidationError, match="exactly one terminal canonical outcome"):
        kernel.construct_fact(_successor_spec(facts[2]), view)


def test_artifact_producer_success_and_consumer_use_existing_outcome_ref() -> None:
    kernel, view = _producer_view()
    mutation = kernel.construct_fact(
        _consumer_spec(outcome_id="outcome-producer"), view
    )
    assert [(ref.role, ref.ref_fact_id) for ref in mutation.refs] == [
        ("hypothesis", "fact-hypothesis-consumer"),
        ("producer_outcome", "fact-outcome-producer"),
    ]
    binding = mutation.payload["upstream_outcomes"][0]
    assert binding == {
        "resource": "bundle-input",
        "produced_by_component": "must.bundle",
        "logical_artifact": "bundle",
        "min_count": 2,
        "outcome_id": "outcome-producer",
        "artifact_id": "artifact-producer",
    }
    assert not {"digest", "artifact_set_digest"} & set(binding)
    producer = view.find_fact(
        "run-1", ResearchFactType.CANONICAL_OUTCOME_ACCEPTED, "outcome-producer"
    )
    assert producer is not None
    assert producer["payload"]["result_kind"] == "artifact"
    assert producer["payload"]["metric_value"] is None


def test_artifact_consumer_accepts_clean_refutation_with_required_publication() -> None:
    kernel, view = _producer_view(outcome=ScientificOutcome.CLEAN_REFUTATION)

    mutation = kernel.construct_fact(
        _consumer_spec(outcome_id="outcome-producer"), view
    )

    assert [(ref.role, ref.ref_fact_id) for ref in mutation.refs] == [
        ("hypothesis", "fact-hypothesis-consumer"),
        ("producer_outcome", "fact-outcome-producer"),
    ]


def test_non_comparable_artifact_outcome_may_omit_required_outputs() -> None:
    kernel = ResearchKernel()
    view = MemoryResearchView()
    goal = GoalDefined(
        fact_id="fact-goal",
        run_id="run-1",
        goal_id="goal-1",
        research_contract=producer_contract(),  # type: ignore[arg-type]
    )
    hypothesis = _component_hypothesis("must.bundle", suffix="producer")
    spec = _bundle_spec(suffix="producer")
    _add(view, kernel, goal, hypothesis, spec)
    failed = replace(
        _bundle_outcome(suffix="producer"),
        outcome=ScientificOutcome.EXECUTION_FAILURE,
        logical_artifacts=(),
    )
    mutation = kernel.construct_fact(failed, view)
    assert mutation.payload["logical_artifacts"] == []


def test_consumer_rejects_unknown_cross_component_and_incomplete_producer() -> None:
    kernel, view = _producer_view(paths=("only-one",))
    with pytest.raises(ResearchValidationError, match="required logical artifact count"):
        kernel.construct_fact(_consumer_spec(outcome_id="outcome-producer"), view)
    with pytest.raises(ResearchValidationError, match="missing producer outcome"):
        kernel.construct_fact(_consumer_spec(outcome_id="outcome-unknown"), view)

    wrong_publication = _consumer_spec(outcome_id="outcome-producer")
    wrong_publication = replace(
        wrong_publication,
        upstream_outcomes=(
            replace(wrong_publication.upstream_outcomes[0], artifact_id="artifact-other"),
        ),
    )
    with pytest.raises(ResearchValidationError, match="artifact publication"):
        kernel.construct_fact(wrong_publication, view)

    producer = view.find_fact(
        "run-1", ResearchFactType.CANONICAL_OUTCOME_ACCEPTED, "outcome-producer"
    )
    assert isinstance(producer, dict)
    producer["payload"]["component_id"] = "primary.score"
    with pytest.raises(ResearchValidationError, match="another component"):
        kernel.construct_fact(_consumer_spec(outcome_id="outcome-producer"), view)


def test_consumer_rejects_inconsistent_method_family_lineage() -> None:
    kernel, view = _producer_view(family="family-2")
    with pytest.raises(ResearchValidationError, match="method-family"):
        kernel.construct_fact(_consumer_spec(outcome_id="outcome-producer"), view)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("execution_id", "wrong-execution"),
        ("evaluator_version", "wrong-evaluator"),
        ("component_id", "primary.other"),
        ("method_family_id", "family-2"),
    ],
)
def test_outcome_must_match_frozen_preregistration(field: str, value: str) -> None:
    kernel, view, facts = build_until(3)
    outcome = replace(facts[3], **{field: value})  # type: ignore[arg-type]
    with pytest.raises(ResearchValidationError, match="preregistration"):
        kernel.construct_fact(outcome, view)  # type: ignore[arg-type]


def test_evidence_assessment_and_verdict_reject_cross_lineage() -> None:
    kernel, view, facts = build_until(4)
    for field, value in (
        ("artifact_id", "artifact-other"),
        ("component_id", "primary.other"),
        ("method_family_id", "family-2"),
    ):
        with pytest.raises(ResearchValidationError, match="lineage"):
            kernel.construct_fact(
                replace(facts[4], **{field: value}), view  # type: ignore[arg-type]
            )

    view.add(kernel.construct_fact(facts[4], view))  # type: ignore[arg-type]
    with pytest.raises(ResearchValidationError, match="lineage"):
        kernel.construct_fact(
            replace(facts[5], component_id="primary.other"), view  # type: ignore[arg-type]
        )
    view.add(kernel.construct_fact(facts[5], view))  # type: ignore[arg-type]
    with pytest.raises(ResearchValidationError, match="component lineage"):
        kernel.construct_fact(
            replace(facts[6], method_family_id="family-2"), view  # type: ignore[arg-type]
        )


def test_conclusive_verdict_cannot_reduce_component_contract_to_one_leaf() -> None:
    kernel, view = _producer_view()
    producer_outcome = view.find_fact(
        "run-1", ResearchFactType.CANONICAL_OUTCOME_ACCEPTED, "outcome-producer"
    )
    assert producer_outcome is not None
    evidence = EvidenceLinked(
        fact_id="fact-evidence-producer",
        run_id="run-1",
        evidence_id="evidence-producer",
        hypothesis_id="hypothesis-producer",
        outcome_id="outcome-producer",
        artifact_id=producer_outcome["artifact_id"],
        component_id="must.bundle",
        method_family_id="family-1",
        evidence_kind="artifact",
    )
    assessment = HypothesisAssessed(
        fact_id="fact-assessment-producer",
        run_id="run-1",
        assessment_id="assessment-producer",
        hypothesis_id="hypothesis-producer",
        component_id="must.bundle",
        method_family_id="family-1",
        evidence_ids=("evidence-producer",),
        assessment=HypothesisAssessment.SUPPORTED,
    )
    _add(view, kernel, evidence, assessment)
    verdict = GoalVerdictRecorded(
        fact_id="fact-verdict",
        run_id="run-1",
        verdict_id="verdict-1",
        goal_id="goal-1",
        method_family_id="family-1",
        assessment_ids=("assessment-producer",),
        verdict=GoalVerdict.GOAL_MET,
    )
    with pytest.raises(ResearchValidationError, match="every required component"):
        kernel.construct_fact(verdict, view)


def test_same_subject_with_different_fact_identity_conflicts() -> None:
    kernel, view, facts = build_until(2)
    duplicate = replace(facts[1], fact_id="different-fact")  # type: ignore[arg-type]
    with pytest.raises(ResearchConflict, match="another fact_id"):
        kernel.construct_fact(duplicate, view)  # type: ignore[arg-type]
