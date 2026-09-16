from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, fields, replace

import pytest

from hypoloom.domain import (
    ArtifactId,
    ArtifactRef,
    AssessmentId,
    AttemptId,
    AttemptObservation,
    AttemptTerminalReason,
    BudgetFrame,
    CanonicalOutcomeAccepted,
    ComponentId,
    ComponentResultKind,
    EvidenceId,
    EvidenceLinked,
    ExecutionContract,
    ExecutionId,
    ExperimentId,
    ExperimentPreregistered,
    ExperimentSpecCandidate,
    FACT_SCHEMA_VERSION,
    FactId,
    GoalDefined,
    GoalId,
    GoalVerdict,
    GoalVerdictRecorded,
    HumanResolution,
    HypothesisAssessment,
    HypothesisAssessed,
    HypothesisCandidate,
    HypothesisId,
    HypothesisProposed,
    LogicalArtifact,
    MethodFamilyId,
    MetricDirection,
    MetricSpec,
    OutcomeId,
    ProducerOutcomeBinding,
    ResearchContract,
    ResearchFactType,
    ResourceGrant,
    ResourceKind,
    ResourcePreference,
    RunId,
    RunVersion,
    ScientificOutcome,
    StopDisposition,
    TrialPlan,
    VerdictId,
    fact_to_dict,
    normalize_metric_value,
)


def budget() -> BudgetFrame:
    return BudgetFrame(
        max_agent_attempts=2,
        max_process_attempts=3,
        max_wall_seconds=60,
        max_hypotheses=3,
        max_experiments=2,
    )


def metric() -> MetricSpec:
    return MetricSpec(name="score", direction=MetricDirection.MAXIMIZE, target="0.80")


def execution_contract() -> ExecutionContract:
    return ExecutionContract(
        source_agent_attempt_id=AttemptId("agent-attempt-1"),
        source_agent_output_digest="b" * 64,
        argv=("python", "experiment.py"),
        environment=(("RESULT_PATH", "result.json"),),
        data_splits=("split-v1",),
        sandbox_profile="bwrap-cpu-no-network/1",
        timeout_seconds=10,
        resource_preference=ResourcePreference.CPU,
        resource_grant=ResourceGrant(kind=ResourceKind.CPU),
    )


@pytest.mark.parametrize("argv", [(), [], ("python", ""), ("python", 2), (" python",)])
def test_execution_argv_keeps_shape_and_text_validation(argv):
    with pytest.raises(ValueError, match="argv"):
        replace(execution_contract(), argv=argv)


def test_argv_repetition_does_not_relax_unique_data_or_environment():
    argv = ("python", "experiment.py", "--epochs", "32", "--batch-size", "32")
    assert replace(execution_contract(), argv=argv).argv == argv
    with pytest.raises(ValueError, match="duplicates"):
        replace(execution_contract(), data_splits=("train", "train"))
    with pytest.raises(ValueError, match="unique"):
        replace(execution_contract(), environment=(("A", "1"), ("A", "2")))


def research_contract() -> dict[str, object]:
    return {
        "schema_version": 1,
        "goal": {
            "objective": "Improve the score",
            "language": "",
            "verdict_rule": "required_conjunction",
            "summary_metric": {
                "name": "score",
                "direction": "MAXIMIZE",
                "target": "0.80",
                "display_only": True,
            },
        },
        "roles": [
            {
                "id": "researcher",
                "focus": "",
                "autonomy": None,
                "protocols": {"default": None, "allowed": []},
                "budget": {},
            }
        ],
        "support_role_budgets": {},
        "components": [
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
                    "target": "0.80",
                },
                "seeds": [],
                "required_artifacts": [],
            }
        ],
        "protocols": {},
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


def all_facts() -> tuple[object, ...]:
    digest = "a" * 64
    return (
        GoalDefined(
            fact_id=FactId("fact-goal"),
            run_id=RunId("run-1"),
            goal_id=GoalId("goal-1"),
            research_contract=research_contract(),  # type: ignore[arg-type]
        ),
        HypothesisProposed(
            fact_id=FactId("fact-hyp"),
            run_id=RunId("run-1"),
            hypothesis_id=HypothesisId("hyp-1"),
            goal_id=GoalId("goal-1"),
            component_id=ComponentId("primary.score"),
            proposer_role="researcher",
            method_family_id=MethodFamilyId("family-1"),
            claim="The treatment improves score",
            assumptions=("data is fixed",),
        ),
        ExperimentPreregistered(
            fact_id=FactId("fact-spec"),
            run_id=RunId("run-1"),
            experiment_id=ExperimentId("exp-1"),
            hypothesis_id=HypothesisId("hyp-1"),
            execution_id=ExecutionId("exec-1"),
            component_id=ComponentId("primary.score"),
            method_family_id=MethodFamilyId("family-1"),
            method="run fixed script",
            result_kind=ComponentResultKind.METRIC,
            metric=metric(),
            trial_plan=TrialPlan(),
            acceptance_criteria=("score >= 0.8",),
            evaluator_version="eval-v1",
            required_artifacts=(),
            upstream_outcomes=(),
            resource_preference=ResourcePreference.CPU,
            execution_contract=execution_contract(),
        ),
        CanonicalOutcomeAccepted(
            fact_id=FactId("fact-outcome"),
            run_id=RunId("run-1"),
            outcome_id=OutcomeId("out-1"),
            experiment_id=ExperimentId("exp-1"),
            execution_id=ExecutionId("exec-1"),
            artifact_id=ArtifactId("artifact-1"),
            component_id=ComponentId("primary.score"),
            method_family_id=MethodFamilyId("family-1"),
            result_kind=ComponentResultKind.METRIC,
            outcome=ScientificOutcome.SUCCESS,
            artifact_set_digest=digest,
            evaluator_version="eval-v1",
            metric_value="0.8",
            logical_artifacts=(),
        ),
        EvidenceLinked(
            fact_id=FactId("fact-evidence"),
            run_id=RunId("run-1"),
            evidence_id=EvidenceId("evidence-1"),
            hypothesis_id=HypothesisId("hyp-1"),
            outcome_id=OutcomeId("out-1"),
            artifact_id=ArtifactId("artifact-1"),
            component_id=ComponentId("primary.score"),
            method_family_id=MethodFamilyId("family-1"),
            evidence_kind="metric",
        ),
        HypothesisAssessed(
            fact_id=FactId("fact-assessment"),
            run_id=RunId("run-1"),
            assessment_id=AssessmentId("assessment-1"),
            hypothesis_id=HypothesisId("hyp-1"),
            component_id=ComponentId("primary.score"),
            method_family_id=MethodFamilyId("family-1"),
            evidence_ids=(EvidenceId("evidence-1"),),
            assessment=HypothesisAssessment.SUPPORTED,
        ),
        GoalVerdictRecorded(
            fact_id=FactId("fact-verdict"),
            run_id=RunId("run-1"),
            verdict_id=VerdictId("verdict-1"),
            goal_id=GoalId("goal-1"),
            method_family_id=MethodFamilyId("family-1"),
            assessment_ids=(AssessmentId("assessment-1"),),
            verdict=GoalVerdict.GOAL_MET,
        ),
    )


def test_exact_seven_fact_schemas_are_v5_json_and_component_typed() -> None:
    facts = all_facts()
    assert tuple(fact_type.value for fact_type in ResearchFactType) == (
        "GoalDefined",
        "HypothesisProposed",
        "ExperimentPreregistered",
        "CanonicalOutcomeAccepted",
        "EvidenceLinked",
        "HypothesisAssessed",
        "GoalVerdictRecorded",
    )
    assert {fact.fact_type for fact in facts} == set(ResearchFactType)  # type: ignore[attr-defined]
    assert len(facts) == len(ResearchFactType) == 7
    for fact in facts:
        encoded = fact_to_dict(fact)  # type: ignore[arg-type]
        assert json.loads(json.dumps(encoded, sort_keys=True)) == encoded
        assert encoded["fact_type"] == fact.fact_type.value  # type: ignore[attr-defined]
        assert encoded["schema_version"] == FACT_SCHEMA_VERSION == 5
        with pytest.raises(FrozenInstanceError):
            fact.run_id = RunId("changed")  # type: ignore[attr-defined,misc]

    goal = fact_to_dict(facts[0])
    assert set(goal) == {
        "fact_type",
        "fact_id",
        "run_id",
        "schema_version",
        "goal_id",
        "research_contract",
    }
    assert not {"primary_metric", "success_criteria", "budget", "objective"} & set(goal)
    assert goal["research_contract"]["goal"]["summary_metric"] == {
        "name": "score",
        "direction": "MAXIMIZE",
        "target": "0.8",
        "display_only": True,
    }
    spec = fact_to_dict(facts[2])
    assert spec["result_kind"] == "metric"
    assert spec["metric"] == {
        "name": "score",
        "direction": "MAXIMIZE",
        "target": "0.8",
    }
    assert spec["trial_plan"] == {"seeds": [], "arms": [], "trial_ids": []}
    assert spec["revision_of_outcome_id"] is None


def test_preregistration_revision_reference_is_optional_and_typed() -> None:
    original = all_facts()[2]
    revised = replace(original, revision_of_outcome_id=OutcomeId("outcome-predecessor"))

    assert fact_to_dict(revised)["revision_of_outcome_id"] == "outcome-predecessor"
    with pytest.raises(ValueError, match="revision_of_outcome_id"):
        replace(original, revision_of_outcome_id=OutcomeId(""))


def test_research_contract_is_deeply_immutable_and_rejects_unknown_authority() -> None:
    source = research_contract()
    contract = ResearchContract.from_mapping(source)
    source["components"][0]["result"]["target"] = "999"  # type: ignore[index]
    assert contract.to_dict()["components"][0]["result"]["target"] == "0.8"  # type: ignore[index]
    with pytest.raises(TypeError):
        contract.document["goal"] = {}  # type: ignore[index]

    unknown_top = research_contract()
    unknown_top["parallel_goal"] = {}
    with pytest.raises(ValueError, match="unknown fields"):
        ResearchContract.from_mapping(unknown_top)

    unknown_component = research_contract()
    unknown_component["components"][0]["scalar_verdict"] = True  # type: ignore[index]
    with pytest.raises(ValueError, match="unknown fields"):
        ResearchContract.from_mapping(unknown_component)

    conflicting_summary = research_contract()
    conflicting_summary["goal"]["summary_metric"]["target"] = "1"  # type: ignore[index]
    with pytest.raises(ValueError, match="display-only"):
        ResearchContract.from_mapping(conflicting_summary)


def test_research_contract_normalizes_legacy_experiment_isolation() -> None:
    source = research_contract()
    source["methodology"]["controls"]["experiment_isolation"] = "worktree"  # type: ignore[index]

    contract = ResearchContract.from_mapping(source)

    assert contract.document["methodology"]["controls"]["experiment_isolation"] == (
        "attempt_overlay"
    )

    unknown = research_contract()
    unknown["methodology"]["controls"]["experiment_isolation"] = "container"  # type: ignore[index]
    with pytest.raises(ValueError, match="experiment_isolation"):
        ResearchContract.from_mapping(unknown)


def test_trial_and_producer_bindings_are_exact_typed_values() -> None:
    plan = TrialPlan(seeds=(0, 1), arms=("control", "treatment"))
    assert plan.trial_ids == (
        "control:0",
        "control:1",
        "treatment:0",
        "treatment:1",
    )
    binding = ProducerOutcomeBinding(
        resource="checkpoints",
        produced_by_component=ComponentId("primary.score"),
        logical_artifact="checkpoints",
        min_count=5,
        outcome_id=OutcomeId("outcome-1"),
        artifact_id=ArtifactId("artifact-1"),
    )
    assert binding.min_count == 5
    with pytest.raises(ValueError, match="trial_ids"):
        TrialPlan(seeds=(0, 1), trial_ids=("0",))
    with pytest.raises(ValueError, match="min_count"):
        ProducerOutcomeBinding(
            resource="checkpoints",
            produced_by_component=ComponentId("primary.score"),
            logical_artifact="checkpoints",
            min_count=0,
            outcome_id=OutcomeId("outcome-1"),
            artifact_id=ArtifactId("artifact-1"),
        )


def test_run_version_is_the_exact_immutable_nine_part_tuple() -> None:
    version = RunVersion(
        workflow_definition_version="workflow-v1",
        research_contract_version="research-v1",
        evaluator_version="eval-v1",
        agent_aci_version="aci-v1",
        runner_version="runner-v1",
        artifact_result_schema_version="result-v1",
        runtime_environment_id="runtime-1",
        configuration_id="config-1",
        sqlite_runtime_version="3.53.4",
    )
    assert version.as_tuple() == (
        "workflow-v1",
        "research-v1",
        "eval-v1",
        "aci-v1",
        "runner-v1",
        "result-v1",
        "runtime-1",
        "config-1",
        "3.53.4",
    )
    with pytest.raises(FrozenInstanceError):
        version.runner_version = "runner-v2"  # type: ignore[misc]
    with pytest.raises(ValueError):
        RunVersion(
            workflow_definition_version="",
            research_contract_version="research-v1",
            evaluator_version="eval-v1",
            agent_aci_version="aci-v1",
            runner_version="runner-v1",
            artifact_result_schema_version="result-v1",
            runtime_environment_id="runtime-1",
            configuration_id="config-1",
            sqlite_runtime_version="3.53.4",
        )


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [("0.8000", "0.8"), (1, "1"), (-0.0, "0"), ("1e-3", "0.001")],
)
def test_metric_values_normalize_deterministically(raw, normalized: str) -> None:
    assert normalize_metric_value(raw) == normalized


@pytest.mark.parametrize("raw", [True, "nan", "Infinity", "not-a-number", "1e1000001"])
def test_metric_values_reject_non_finite_or_non_numeric_input(raw) -> None:
    with pytest.raises(ValueError):
        normalize_metric_value(raw)


def test_metric_direction_and_budget_are_typed() -> None:
    maximum = metric()
    minimum = MetricSpec(name="loss", direction=MetricDirection.MINIMIZE, target="2")
    assert maximum.accepts("0.81") and not maximum.accepts("0.79")
    assert minimum.accepts("1.9") and not minimum.accepts("2.1")
    for kwargs in (
        {"max_agent_attempts": 0, "max_process_attempts": 1, "max_wall_seconds": 1, "max_hypotheses": 1, "max_experiments": 1},
        {"max_agent_attempts": True, "max_process_attempts": 1, "max_wall_seconds": 1, "max_hypotheses": 1, "max_experiments": 1},
        {"max_agent_attempts": 1, "max_process_attempts": 1, "max_wall_seconds": 1, "max_hypotheses": 0, "max_experiments": 1},
        {"max_agent_attempts": 1, "max_process_attempts": 1, "max_wall_seconds": 1, "max_hypotheses": 2, "max_experiments": 3},
        {"max_agent_attempts": 1, "max_process_attempts": 1, "max_wall_seconds": 1, "max_hypotheses": 4, "max_experiments": 1},
    ):
        with pytest.raises(ValueError):
            BudgetFrame(**kwargs)


def test_candidate_refs_are_immutable_and_parent_relation_is_paired() -> None:
    candidate = HypothesisCandidate(
        claim="claim",
        owner_role="expert",
        assumptions=(),
        parent_hypothesis_id=HypothesisId("parent"),
        relation="derived_from",
    )
    assert candidate.parent_hypothesis_id == "parent"
    with pytest.raises(ValueError):
        HypothesisCandidate(
            claim="claim",
            owner_role="expert",
            parent_hypothesis_id=HypothesisId("parent"),
        )
    with pytest.raises(ValueError):
        ExperimentSpecCandidate(
            method="method",
            metrics=(),
            acceptance_criteria=("criterion",),
            evaluator_version="eval-v1",
            artifact_requirements=("result.json",),
            resource_preference=ResourcePreference.CPU,
        )
    with pytest.raises(ValueError):
        HypothesisCandidate(
            claim="claim", owner_role="expert", assumptions=("same", "same")
        )


def test_resource_preference_and_grant_are_typed_and_fail_closed() -> None:
    assert ResourceGrant(kind=ResourceKind.CPU).gpu_uuid is None
    assert ResourceGrant(kind=ResourceKind.GPU, gpu_uuid="GPU-abc").gpu_uuid == "GPU-abc"
    assert ResourceGrant(
        kind=ResourceKind.GPU, gpu_uuid="MIG-GPU-abc/1/2"
    ).gpu_uuid.startswith("MIG-")
    for kwargs in (
        {"kind": ResourceKind.CPU, "gpu_uuid": "GPU-abc"},
        {"kind": ResourceKind.GPU},
        {"kind": ResourceKind.GPU, "gpu_uuid": "GPU-"},
        {"kind": ResourceKind.GPU, "gpu_uuid": "MIG-"},
        {"kind": ResourceKind.GPU, "gpu_uuid": " GPU-abc"},
        {"kind": ResourceKind.GPU, "gpu_uuid": "not-a-gpu"},
        {"kind": "GPU", "gpu_uuid": "GPU-abc"},
    ):
        with pytest.raises(ValueError):
            ResourceGrant(**kwargs)


def test_execution_contract_binds_preference_to_deterministic_grant() -> None:
    auto_gpu = ExecutionContract(
        source_agent_attempt_id=AttemptId("agent-attempt-1"),
        source_agent_output_digest="b" * 64,
        argv=("python", "experiment.py"),
        environment=(),
        data_splits=("split-v1",),
        sandbox_profile="bwrap-gpu-no-network/1",
        timeout_seconds=10,
        resource_preference=ResourcePreference.AUTO,
        resource_grant=ResourceGrant(kind=ResourceKind.GPU, gpu_uuid="GPU-abc"),
    )
    assert auto_gpu.resource_grant.kind is ResourceKind.GPU
    for preference, grant in (
        (ResourcePreference.CPU, ResourceGrant(kind=ResourceKind.GPU, gpu_uuid="GPU-abc")),
        (ResourcePreference.GPU_REQUIRED, ResourceGrant(kind=ResourceKind.CPU)),
    ):
        with pytest.raises(ValueError, match="resource"):
            ExecutionContract(
                source_agent_attempt_id=AttemptId("agent-attempt-1"),
                source_agent_output_digest="b" * 64,
                argv=("python", "experiment.py"),
                environment=(),
                data_splits=("split-v1",),
                sandbox_profile="bwrap-no-network/1",
                timeout_seconds=10,
                resource_preference=preference,
                resource_grant=grant,
            )


def test_unknown_vocabularies_and_invalid_fact_identity_fail_closed() -> None:
    for enum_type in (
        ScientificOutcome,
        HumanResolution,
        HypothesisAssessment,
        GoalVerdict,
        StopDisposition,
        ComponentResultKind,
    ):
        with pytest.raises(ValueError):
            enum_type("UNKNOWN")
    with pytest.raises(ValueError):
        GoalDefined(
            fact_id=FactId("fact"),
            run_id=RunId("run"),
            schema_version=3,
            goal_id=GoalId("goal"),
            research_contract=research_contract(),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError):
        GoalDefined(
            fact_id=FactId("fact"),
            run_id=RunId("run"),
            schema_version=True,
            goal_id=GoalId("goal"),
            research_contract=research_contract(),  # type: ignore[arg-type]
        )
    assert {item.name for item in fields(GoalDefined)} == {
        "fact_id",
        "run_id",
        "schema_version",
        "goal_id",
        "research_contract",
    }


def test_artifact_and_attempt_observation_reject_integrity_shortcuts() -> None:
    artifact = ArtifactRef(
        artifact_id=ArtifactId("artifact"), artifact_set_digest="b" * 64
    )
    observation = AttemptObservation(
        attempt_id=AttemptId("attempt"),
        reason=AttemptTerminalReason.SUCCEEDED,
        artifact=artifact,
        exit_code=0,
    )
    assert observation.artifact == artifact
    with pytest.raises(ValueError):
        ArtifactRef(
            artifact_id=ArtifactId("artifact"), artifact_set_digest="ABC"
        )
    with pytest.raises(ValueError):
        AttemptObservation(
            attempt_id=AttemptId("attempt"),
            reason=AttemptTerminalReason.LOST,
            artifact=artifact,
        )


@pytest.mark.parametrize(
    "outcome", [ScientificOutcome.SUCCESS, ScientificOutcome.CLEAN_REFUTATION]
)
def test_metric_scientific_outcomes_require_metric_value(
    outcome: ScientificOutcome,
) -> None:
    with pytest.raises(ValueError, match="metric_value"):
        CanonicalOutcomeAccepted(
            fact_id=FactId("fact"),
            run_id=RunId("run-1"),
            outcome_id=OutcomeId("out-1"),
            experiment_id=ExperimentId("exp-1"),
            execution_id=ExecutionId("exec-1"),
            artifact_id=ArtifactId("artifact-1"),
            component_id=ComponentId("primary.score"),
            method_family_id=MethodFamilyId("family-1"),
            result_kind=ComponentResultKind.METRIC,
            outcome=outcome,
            artifact_set_digest="a" * 64,
            evaluator_version="eval-v1",
            metric_value=None,
            logical_artifacts=(),
        )


def test_artifact_component_may_succeed_without_metric() -> None:
    fact = CanonicalOutcomeAccepted(
        fact_id=FactId("fact"),
        run_id=RunId("run-1"),
        outcome_id=OutcomeId("out-1"),
        experiment_id=ExperimentId("exp-1"),
        execution_id=ExecutionId("exec-1"),
        artifact_id=ArtifactId("artifact-1"),
        component_id=ComponentId("must.report"),
        method_family_id=MethodFamilyId("family-1"),
        result_kind=ComponentResultKind.ARTIFACT,
        outcome=ScientificOutcome.SUCCESS,
        artifact_set_digest="a" * 64,
        evaluator_version="eval-v1",
        metric_value=None,
        logical_artifacts=(
            LogicalArtifact(logical_artifact="report", paths=("report.md",)),
        ),
    )
    assert fact.metric_value is None
    with pytest.raises(ValueError, match="metric_value"):
        replace(fact, metric_value="1")


@pytest.mark.parametrize(
    "outcome",
    [
        ScientificOutcome.INCONCLUSIVE,
        ScientificOutcome.EXECUTION_FAILURE,
        ScientificOutcome.INTEGRITY_REJECTED,
        ScientificOutcome.CANCELLED,
    ],
)
def test_non_comparable_metric_outcomes_forbid_metric_value(
    outcome: ScientificOutcome,
) -> None:
    with pytest.raises(ValueError, match="metric_value"):
        CanonicalOutcomeAccepted(
            fact_id=FactId("fact"),
            run_id=RunId("run-1"),
            outcome_id=OutcomeId("out-1"),
            experiment_id=ExperimentId("exp-1"),
            execution_id=ExecutionId("exec-1"),
            artifact_id=ArtifactId("artifact-1"),
            component_id=ComponentId("primary.score"),
            method_family_id=MethodFamilyId("family-1"),
            result_kind=ComponentResultKind.METRIC,
            outcome=outcome,
            artifact_set_digest="a" * 64,
            evaluator_version="eval-v1",
            metric_value="1",
            logical_artifacts=(),
        )


def test_inconclusive_goal_verdict_may_omit_lineage_and_assessments() -> None:
    fact = GoalVerdictRecorded(
        fact_id=FactId("fact-verdict"),
        run_id=RunId("run-1"),
        verdict_id=VerdictId("verdict-1"),
        goal_id=GoalId("goal-1"),
        method_family_id=None,
        assessment_ids=(),
        verdict=GoalVerdict.INCONCLUSIVE,
    )
    assert fact.assessment_ids == ()
    mixed = replace(
        fact,
        fact_id=FactId("fact-verdict-mixed"),
        assessment_ids=(AssessmentId("assessment-1"),),
    )
    assert mixed.method_family_id is None
    assert mixed.assessment_ids == ("assessment-1",)
    # The context-free shape allows an aggregate negative verdict; only the
    # kernel may admit it against an actual shared-goal contract.
    assert replace(mixed, verdict=GoalVerdict.GOAL_NOT_MET).method_family_id is None
    with pytest.raises(ValueError, match="method_family_id"):
        GoalVerdictRecorded(
            fact_id=FactId("fact-verdict-2"),
            run_id=RunId("run-1"),
            verdict_id=VerdictId("verdict-2"),
            goal_id=GoalId("goal-1"),
            method_family_id=None,
            assessment_ids=(AssessmentId("assessment-1"),),
            verdict=GoalVerdict.GOAL_MET,
        )
