from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from hypoloom.agent_aci import AgentCommand, EmbeddedInput, canonical_json
from hypoloom.agent_context import (
    RETRYABLE_MODEL_FAILURES,
    agent_failure_stage,
    agent_retry_class,
    candidate_failure_retryable,
    predecessor_review_inputs,
    preflight_origin,
    rendezvous_inputs,
    result_analysis_inputs,
)
from hypoloom.artifacts import ArtifactPublisher
from hypoloom.collaboration import (
    ExpertSignal,
    ExpertSignalKind,
    RendezvousDisposition,
    RendezvousOrigin,
    RendezvousReply,
)
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.expert_loop import (
    ResearchNodeKey,
    ResearchNodeKind,
    ResearchNodeTask,
)


def test_retry_classification_separates_candidate_and_model_output_failures() -> None:
    assert candidate_failure_retryable("invalid_candidate")
    assert candidate_failure_retryable("candidate_method_family")
    assert not candidate_failure_retryable("task_candidate_mismatch")
    assert {
        "invalid_workspace",
        "method_family_id_mismatch",
        "owner_role_mismatch",
        "task_candidate_mismatch",
    } <= RETRYABLE_MODEL_FAILURES
    assert agent_retry_class("candidate_metrics", {}, retryable=True) == "MODEL"
    assert agent_retry_class("execution_plan", {}, retryable=True) == "INFRASTRUCTURE"
    assert agent_retry_class("invalid_turn_usage", {}, retryable=True) == "INFRASTRUCTURE"
    assert agent_retry_class(
        "invalid_provider_metadata", {}, retryable=True,
    ) == "INFRASTRUCTURE"
    assert agent_failure_stage(
        "invalid_tool_workspace",
        {"diagnostics": {"workspace_error": "missing experiment.py"}},
    ) == "agent_workspace"


def test_cli_compaction_diagnostics_keep_existing_classification_rules() -> None:
    stateless = {"diagnostics": {
        "provider_diagnostic": "Prompt is too long",
        "provider_failure": {"code": "input_too_long", "http_status": None, "source": "cli_compaction"}}}
    assert agent_retry_class("nonzero_exit", stateless, retryable=True) is None
    assert agent_failure_stage("nonzero_exit", stateless) == "provider_admission"
    stateful = {"diagnostics": {
        "provider_diagnostic": "Prompt is too long",
        "provider_failure_hint": {"code": "input_too_long", "source": "cli_compaction"}}}
    assert agent_retry_class("nonzero_exit", stateful, retryable=True) == "INFRASTRUCTURE"
    assert agent_failure_stage("nonzero_exit", stateful) == "provider_process"


def test_allowed_supporting_refs_projection_is_bounded_and_safe() -> None:
    from hypoloom.agent_context import _allowed_supporting_refs

    base = default_request(AgentCommand.ANSWER_RENDEZVOUS)
    refs = ("run--fact-goal", "agent-1--response")
    request = replace(base, immutable_context_refs=refs)
    assert _allowed_supporting_refs(request) == refs

    empty = replace(base, immutable_context_refs=())
    assert _allowed_supporting_refs(empty) is None

    credential_ref = replace(
        base, immutable_context_refs=("api_key=sk-live-example",)
    )
    assert _allowed_supporting_refs(credential_ref) is None

    oversized = replace(
        base, immutable_context_refs=("x" * 1024,) * 17
    )
    assert _allowed_supporting_refs(oversized) is None

    bounded = replace(base, immutable_context_refs=("x" * 1024,) * 16)
    assert _allowed_supporting_refs(bounded) == ("x" * 1024,) * 16

    too_long_ref = replace(base, immutable_context_refs=("x" * 1025,))
    assert _allowed_supporting_refs(too_long_ref) is None


class _ValidationStore:
    def __init__(self, source_attempt_id: str, workflow_id: str) -> None:
        self.validation = {
            "attempt_id": f"{workflow_id}--spec-validation-1",
            "state": "CLOSED",
            "observation_json": json.dumps(
                {
                    "source_agent_attempt_id": source_attempt_id,
                    "validation_version": "candidate-compile/3",
                    "terminal_reason": "SUCCEEDED",
                }
            ),
        }

    def list_attempts(self, *, execution_id: str):
        assert execution_id.endswith("--candidate-validation")
        return [self.validation]


class _AnalysisStore:
    def __init__(
        self,
        spec: dict[str, object],
        artifact: dict[str, object],
        source_attempt: dict[str, object],
    ) -> None:
        self.spec = spec
        self.artifact = artifact
        self.source_attempt = source_attempt

    def find_fact(self, run_id: str, fact_type: str, subject_id: str):
        assert run_id == "run-analysis"
        assert (fact_type, subject_id) == (
            "ExperimentPreregistered",
            "experiment-analysis",
        )
        return self.spec

    def inspect(self, query: str, parameters: tuple[str, ...]):
        if "FROM execution_artifacts" in query:
            assert parameters == ("process-analysis-1",)
            return [self.artifact]
        assert "FROM workflow_transitions" in query
        assert parameters == ("run-analysis", "experiment-analysis")
        return [{
            "payload_json": json.dumps({
                "rendezvous_origin": {
                    "source_attempt_id": "process-analysis-1",
                },
                "outcome": {
                    "outcome": "INTEGRITY_REJECTED",
                    "validation_error": (
                        "text_contract_failed@report.md: "
                        "missing required text 'tail class'"
                    ),
                    "execution_integrity_error": None,
                },
            })
        }]

    def get_attempt(self, attempt_id: str):
        assert attempt_id == self.source_attempt["attempt_id"]
        return self.source_attempt


def _draft_source_attempt(attempt_id: str) -> dict[str, object]:
    response = FakeAgent().invoke(default_request(AgentCommand.DRAFT_EXPERIMENT_SPEC))
    return {
        "attempt_id": attempt_id,
        "observation_json": json.dumps({"response": response.to_dict()}),
    }


def _review_origin(
    rendezvous_id: str, phase: str, source_attempt_id: str
) -> RendezvousOrigin:
    kind = (
        ExpertSignalKind.HELP
        if phase == "preflight"
        else ExpertSignalKind.DEBATE
    )
    return RendezvousOrigin(
        rendezvous_id=rendezvous_id,
        requester_role="owner",
        requester_slot=1,
        source_attempt_id=source_attempt_id,
        target_role="reviewer",
        signal=ExpertSignal(
            schema_version=1,
            kind=kind,
            target_role_hint="reviewer",
            question=f"Review {phase}.",
            context_refs=(f"{rendezvous_id}--context",),
        ),
        component_id="component-1",
        phase=phase,
    )


def _review_reply(origin: RendezvousOrigin, summary: str) -> RendezvousReply:
    return RendezvousReply(
        schema_version=1,
        rendezvous_id=origin.rendezvous_id,
        kind=origin.signal.kind,
        disposition=RendezvousDisposition.ANSWERED,
        responder_role="reviewer",
        summary=summary,
        supporting_refs=origin.signal.context_refs,
    )


def test_predecessor_review_inputs_select_exact_lineage_in_phase_order() -> None:
    stale_preflight = _review_origin(
        "stale-draft--preflight-rendezvous", "preflight", "stale-draft"
    )
    current_analyze = _review_origin(
        "current-process--analyze-rendezvous", "analyze", "current-process"
    )
    optional = _review_origin(
        "current-draft--optional-rendezvous", "optional", "current-draft"
    )
    current_preflight = _review_origin(
        "current-draft--preflight-rendezvous", "preflight", "current-draft"
    )
    stale_analyze = _review_origin(
        "stale-process--analyze-rendezvous", "analyze", "stale-process"
    )
    origins = (
        stale_preflight,
        current_analyze,
        optional,
        current_preflight,
        stale_analyze,
    )
    replies = {
        origin.rendezvous_id: _review_reply(origin, f"summary for {origin.rendezvous_id}")
        for origin in origins
    }

    inputs = predecessor_review_inputs(
        origins,
        replies,
        source_attempt_id="current-draft",
        process_attempt_id="current-process",
    )

    assert [item.ref for item in inputs] == [
        "current-draft--preflight-rendezvous--reply",
        "current-process--analyze-rendezvous--reply",
    ]
    assert len(inputs) == 2
    assert [json.loads(item.content_bytes())["summary"] for item in inputs] == [
        "summary for current-draft--preflight-rendezvous",
        "summary for current-process--analyze-rendezvous",
    ]


def _analysis_context(
    tmp_path: Path,
) -> tuple[
    _AnalysisStore,
    ArtifactPublisher,
    dict[str, object],
    dict[str, object],
]:
    process_attempt_id = "process-analysis-1"
    source_attempt = _draft_source_attempt("draft-analysis-1")
    spec = {
        "fact_id": "spec-analysis",
        "payload": {
            "execution_contract": {
                "source_agent_attempt_id": source_attempt["attempt_id"]
            }
        },
    }
    artifact = {
        "artifact_id": "artifact-analysis",
        "path": "run-analysis/experiment-analysis/execution-analysis/"
        f"{process_attempt_id}/result",
    }
    publisher = ArtifactPublisher(tmp_path / "artifacts")
    publication_root = publisher.root.joinpath(*str(artifact["path"]).split("/"))
    publication_root.mkdir(parents=True)
    (publication_root / "result.json").write_bytes(b'{"outcome":"SUCCESS"}')
    source = {
        "kind": "PROCESS",
        "attempt_id": process_attempt_id,
        "experiment_id": "experiment-analysis",
        "workflow_id": "experiment-analysis",
    }
    observation = {
        "attempt_id": process_attempt_id,
        "terminal_reason": "SUCCEEDED",
    }
    return (
        _AnalysisStore(spec, artifact, source_attempt),
        publisher,
        source,
        observation,
    )


class _ResultAnalysisStore:
    def __init__(
        self,
        *,
        specs: dict[str, dict[str, object]],
        outcomes: tuple[dict[str, object], ...],
        creations: tuple[dict[str, object], ...],
        transitions: dict[str, dict[str, object]],
        attempts: dict[str, dict[str, object]],
        artifacts: dict[str, dict[str, object]],
    ) -> None:
        self.specs = specs
        self.outcomes = outcomes
        self.creations = creations
        self.transitions = transitions
        self.attempts = attempts
        self.artifacts = artifacts

    def find_fact(self, run_id: str, fact_type: str, subject_id: str):
        assert run_id == "run-current-analysis"
        assert fact_type == "ExperimentPreregistered"
        return self.specs.get(subject_id)

    def list_facts(self, run_id: str, fact_type: str):
        assert run_id == "run-current-analysis"
        assert fact_type == "CanonicalOutcomeAccepted"
        return self.outcomes

    def inspect(self, query: str, parameters: tuple[str, ...]):
        if "workflow_kind='EXPERIMENT'" in query and "revision=1" in query:
            assert parameters == ("run-current-analysis",)
            return self.creations
        if "FROM workflow_transitions" in query:
            assert parameters[0] == "run-current-analysis"
            experiment_id = parameters[1]
            return [{"payload_json": json.dumps(self.transitions[experiment_id])}]
        assert "FROM execution_artifacts" in query
        attempt_id = parameters[0]
        artifact = self.artifacts.get(attempt_id)
        return [] if artifact is None else [artifact]

    def get_attempt(self, attempt_id: str):
        return self.attempts.get(attempt_id)


def test_result_analysis_inputs_select_exact_current_experiment_and_diagnostics(
    tmp_path: Path,
) -> None:
    run_id = "run-current-analysis"
    component_id = "must.general_method_implemented"
    role = "experiment-engineering"
    old_experiment_id = f"{run_id}--component-1--experiment"
    current_experiment_id = f"{run_id}--component-1--experiment-6"
    old_process_id = f"{old_experiment_id}--process-1"
    current_process_id = f"{current_experiment_id}--process-1"
    source_attempt_id = "agent-current-design-1"
    source_response = FakeAgent().invoke(
        default_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT)
    )
    specs = {
        old_experiment_id: {
            "fact_id": "fact-spec-old",
            "fact_type": "ExperimentPreregistered",
            "experiment_id": old_experiment_id,
            "execution_id": "execution-old",
            "payload": {
                "component_id": component_id,
                "execution_contract": {
                    "source_agent_attempt_id": "agent-old-design-1",
                },
            },
        },
        current_experiment_id: {
            "fact_id": "fact-spec-current",
            "fact_type": "ExperimentPreregistered",
            "experiment_id": current_experiment_id,
            "execution_id": "execution-current",
            "payload": {
                "component_id": component_id,
                "execution_contract": {
                    "source_agent_attempt_id": source_attempt_id,
                },
            },
        },
    }
    outcomes = (
        {
            "fact_id": "fact-outcome-old",
            "fact_type": "CanonicalOutcomeAccepted",
            "outcome_id": "outcome-old",
            "experiment_id": old_experiment_id,
            "execution_id": "execution-old",
            "artifact_id": "artifact-old",
            "payload": {
                "component_id": component_id,
                "outcome": "EXECUTION_FAILURE",
            },
        },
        {
            "fact_id": "fact-outcome-current",
            "fact_type": "CanonicalOutcomeAccepted",
            "outcome_id": "outcome-current",
            "experiment_id": current_experiment_id,
            "execution_id": "execution-current",
            "artifact_id": "artifact-current",
            "payload": {
                "component_id": component_id,
                "outcome": "INTEGRITY_REJECTED",
            },
        },
    )
    creations = (
        {
            "workflow_id": old_experiment_id,
            "payload_json": json.dumps({
                "component_id": component_id,
                "role": role,
                "expert_cycle": 1,
                "experiment_ordinal": 1,
            }),
        },
        {
            "workflow_id": current_experiment_id,
            "payload_json": json.dumps({
                "component_id": component_id,
                "role": role,
                "expert_cycle": 1,
                "experiment_ordinal": 6,
            }),
        },
    )
    old_evaluation = {
        "outcome": "EXECUTION_FAILURE",
        "validation_error": None,
        "execution_integrity_error": "FileNotFoundError: stale experiment error",
    }
    current_evaluation = {
        "outcome": "INTEGRITY_REJECTED",
        "validation_error": (
            "invalid_unified_diff@implementation.patch: "
            "must contain paired headers and a text hunk"
        ),
        "execution_integrity_error": None,
    }
    transitions = {
        old_experiment_id: {
            "attempt_id": old_process_id,
            "outcome": old_evaluation,
        },
        current_experiment_id: {
            "attempt_id": current_process_id,
            "outcome": current_evaluation,
        },
    }
    current_observation = {
        "attempt_id": current_process_id,
        "attempt_ordinal": 1,
        "execution_id": "execution-current",
        "terminal_reason": "SUCCEEDED",
        "returncode": 0,
    }
    attempts = {
        current_process_id: {
            "attempt_id": current_process_id,
            "kind": "PROCESS",
            "state": "CLOSED",
            "workflow_id": current_experiment_id,
            "experiment_id": current_experiment_id,
            "execution_id": "execution-current",
            "terminal_reason": "SUCCEEDED",
            "observation_json": json.dumps(current_observation),
        },
        source_attempt_id: {
            "attempt_id": source_attempt_id,
            "kind": "AGENT",
            "state": "CLOSED",
            "terminal_reason": "SUCCEEDED",
            "observation_json": json.dumps({
                "response": source_response.to_dict(),
            }),
        },
    }
    artifact = {
        "artifact_id": "artifact-current",
        "attempt_id": current_process_id,
        "path": (
            f"{run_id}/{current_experiment_id}/execution-current/"
            f"{current_process_id}/result"
        ),
    }
    store = _ResultAnalysisStore(
        specs=specs,
        outcomes=outcomes,
        creations=creations,
        transitions=transitions,
        attempts=attempts,
        artifacts={current_process_id: artifact},
    )
    publisher = ArtifactPublisher(tmp_path / "artifacts")
    result_root = publisher.root.joinpath(*str(artifact["path"]).split("/"))
    result_root.mkdir(parents=True)
    result_bytes = b'{"artifacts":{"implementation_patch":"implementation.patch"}}'
    (result_root / "result.json").write_bytes(result_bytes)
    log_root = publisher.root / "result-staging" / current_process_id / "logs"
    log_root.mkdir(parents=True)
    stdout = b"discarded stdout head\n" + b"s" * 65_536 + b"\ncurrent stdout tail\n"
    stderr = b"discarded stderr head\n" + b"e" * 65_536 + b"\ncurrent stderr tail\n"
    (log_root / "stdout.log").write_bytes(stdout)
    (log_root / "stderr.log").write_bytes(stderr)
    task = ResearchNodeTask(key=ResearchNodeKey(
        run_id=run_id,
        component_id=component_id,
        expert_slot=role,
        cycle=1,
        node_kind=ResearchNodeKind.RESULT_ANALYSIS,
        experiment_ordinal=6,
    ))

    inputs = result_analysis_inputs(store, publisher, run_id, task)
    by_ref = {item.ref: item for item in inputs}
    prefix = task.command_id

    assert json.loads(by_ref[f"{prefix}--current-spec"].content_bytes()) == specs[
        current_experiment_id
    ]
    assert json.loads(by_ref[f"{prefix}--current-outcome"].content_bytes()) == outcomes[1]
    assert json.loads(
        by_ref[f"{prefix}--current-evaluation"].content_bytes()
    ) == current_evaluation
    assert json.loads(by_ref[f"{prefix}--current-process"].content_bytes()) == (
        current_observation
    )
    assert by_ref[f"{prefix}--current-result.json"].content_bytes() == result_bytes
    assert by_ref[f"{prefix}--current-stdout"].content_bytes() == stdout[-65_536:]
    assert by_ref[f"{prefix}--current-stderr"].content_bytes() == stderr[-65_536:]
    expected_source = next(
        item.content_bytes()
        for item in source_response.proposed_workspace_artifacts
        if item.path == "experiment.py"
    )
    assert by_ref[f"{prefix}--current-experiment.py"].content_bytes() == (
        expected_source[-65_536:]
    )
    projected = b"\n".join(item.content_bytes() for item in inputs)
    assert b"invalid_unified_diff" in projected
    assert b"FileNotFoundError" not in projected

    # GOAL-only runs may have a local placeholder and a real common result.
    # Both the actual common output and raw file presence must reach analysis.
    specs[current_experiment_id]["payload"]["goal_evaluation"] = {
        "scope": "GOAL", "acceptance": {"raw_artifacts": ["predictions.json", "causal_audits.json"]}}
    (result_root / "goal").mkdir()
    (result_root / "raw").mkdir()
    common = {"measurements": {"alpha": 0.4}, "checks": {"complete": True}}
    (result_root / "goal/result.json").write_text(json.dumps(common))
    (result_root / "raw/predictions.json").write_text("[]")
    (result_root / "result.json").write_text('{"parser_result_unavailable":true}')
    shared_inputs = {item.ref: item for item in result_analysis_inputs(store, publisher, run_id, task)}
    assert json.loads(shared_inputs[f"{prefix}--current-goal-result.json"].content_bytes()) == common
    files = json.loads(shared_inputs[f"{prefix}--current-result-files"].content_bytes())
    assert files["available"] == ["goal/result.json", "raw/predictions.json", "result.json"]
    assert files["missing"] == ["raw/causal_audits.json"]
    assert files["common_result_path"] == "goal/result.json"

    (result_root / "result.json").unlink()
    failure_evaluation = {
        "outcome": "EXECUTION_FAILURE",
        "validation_error": None,
        "execution_integrity_error": None,
    }
    outcomes[1]["payload"]["outcome"] = "EXECUTION_FAILURE"
    transitions[current_experiment_id]["outcome"] = failure_evaluation
    attempts[current_process_id]["terminal_reason"] = "FAILED"
    attempts[current_process_id]["observation_json"] = json.dumps({
        **current_observation,
        "terminal_reason": "FAILED",
        "returncode": 1,
    })
    failed_inputs = result_analysis_inputs(store, publisher, run_id, task)
    failed_by_ref = {item.ref: item for item in failed_inputs}
    assert f"{prefix}--current-result.json" not in failed_by_ref
    assert {
        f"{prefix}--current-{suffix}"
        for suffix in (
            "spec", "outcome", "evaluation", "process", "experiment.py",
            "stdout", "stderr",
        )
    } <= set(failed_by_ref)
    assert json.loads(
        failed_by_ref[f"{prefix}--current-evaluation"].content_bytes()
    ) == failure_evaluation
    assert json.loads(
        failed_by_ref[f"{prefix}--current-process"].content_bytes()
    )["terminal_reason"] == "FAILED"

    for field, wrong_value in (
        ("component_id", "must.some_other_component"),
        ("role", "some-other-role"),
        ("expert_cycle", 2),
    ):
        wrong_identity = json.loads(creations[1]["payload_json"])
        wrong_identity[field] = wrong_value
        store.creations = (
            creations[0],
            {**creations[1], "payload_json": json.dumps(wrong_identity)},
        )
        with pytest.raises(
            ValueError, match="result analysis current experiment is unavailable"
        ):
            result_analysis_inputs(store, publisher, run_id, task)
    store.creations = creations


def test_preflight_projects_draft_context_without_losing_scientific_inputs() -> None:
    component_id = "must.dual_dataset_mechanism_search_recorded"
    component = {
        "id": component_id,
        "owner": "ml-expert",
        "protocol": "dual-dataset-mechanism-search",
        "result": {"kind": "artifact"},
        "required_artifacts": ["mechanism_search", "dual_dataset_design"],
    }
    protocol = {
        "result_kind": "artifact",
        "requirements": {
            "required_artifacts": ["mechanism_search", "dual_dataset_design"],
        },
    }
    selected_delivery = {
        "production": False,
        "proof_obligations": ["preserve both dataset predictions"],
    }
    goal = {
        "objective": "Compare causal-safe mechanisms for MELD and IEMOCAP.",
        "language": "Chinese",
        "verdict_rule": "required_conjunction",
    }
    required_spec = {
        "evaluator_version": "component-result/2",
        "workspace_artifacts": ["experiment.py"],
        "result_kind": "artifact",
        "result_contract": {
            "required_top_level_keys": ["outcome", "artifacts"],
            "artifact_ids": ["mechanism_search", "dual_dataset_design"],
        },
        "component": component,
        "protocol": protocol,
        "method_family_id": "family-causal-safe",
    }
    config = {
        "execution": {"allow_gpu": True, "target": {"commit": "a" * 40}},
        "component": component,
        "protocol": protocol,
        "protocol_catalog": {
            component["protocol"]: protocol,
            "unrelated-large-protocol": {"padding": "x" * 8_000},
        },
        "delivery_contracts": {
            "enabled": True,
            "schema_version": 1,
            "protocols": {
                component["protocol"]: selected_delivery,
                "unrelated-large-delivery": {"padding": "y" * 8_000},
            },
        },
        "goal": goal,
        "required_experiment_spec": required_spec,
    }
    goal_ref = "run-merc--fact-goal"
    hypothesis_refs = {
        "run-merc--fact-hypothesis-ml",
        "run-merc--fact-hypothesis-affective",
    }
    config_ref = "run-merc--component-3-config"
    compute_ref = "run-merc--component-3--compute-capabilities"
    skill_ref = "run-merc--component-3--resource-planning-skill"
    experience_ref = "run-merc--component-3--resource-experience"
    goal_fact = {
        "fact_id": goal_ref,
        "fact_type": "GoalDefined",
        "payload": {
            "research_contract": {
                "goal": goal,
                "components": [component, {"id": "unrelated"}],
                "protocols": config["protocol_catalog"],
                "delivery_contracts": config["delivery_contracts"],
            }
        },
    }
    hypothesis_payloads = {
        ref: {
            "fact_id": ref,
            "fact_type": "HypothesisProposed",
            "payload": {
                "component_id": component_id,
                "method_family_id": "family-causal-safe",
                "claim": f"claim from {ref.rsplit('-', 1)[-1]}",
            },
        }
        for ref in hypothesis_refs
    }
    inputs = (
        EmbeddedInput.from_bytes(goal_ref, "application/json", canonical_json(goal_fact)),
        *(EmbeddedInput.from_bytes(ref, "application/json", canonical_json(payload))
          for ref, payload in hypothesis_payloads.items()),
        EmbeddedInput.from_bytes(config_ref, "application/json", canonical_json(config)),
        EmbeddedInput.from_bytes(
            compute_ref, "application/json", b'{"gpus":[{"uuid":"GPU-1"}]}'
        ),
        EmbeddedInput.from_bytes(skill_ref, "text/markdown", b"Use CPU when valid."),
        EmbeddedInput.from_bytes(experience_ref, "application/json", b'{"records":[]}'),
    )
    base_request = default_request(AgentCommand.DRAFT_EXPERIMENT_SPEC)
    request = replace(
        base_request,
        run_id="run-merc",
        owner_workflow_id="experiment-merc",
        attempt_id="draft-attempt-1",
        role="ml-expert",
        component_id=component_id,
        method_family_id="family-causal-safe",
        immutable_context_refs=tuple(item.ref for item in inputs),
        input_artifacts=inputs,
    )
    response = FakeAgent().invoke(base_request)
    source = {
        "attempt_id": request.attempt_id,
        "workflow_id": request.owner_workflow_id,
        "kind": "AGENT",
    }
    observation = {"request": request.to_dict(), "response": response.to_dict()}
    store = _ValidationStore(request.attempt_id, request.owner_workflow_id)

    by_ref = rendezvous_inputs(store, None, request.run_id, source, observation)
    origin, _ = preflight_origin(
        request,
        SimpleNamespace(role_ids=("ml-expert", "experiment-engineering-expert")),
        store.validation["attempt_id"],
        hypothesis_refs,
        "experiment-engineering-expert",
    )

    assert goal_ref in origin.signal.context_refs
    assert hypothesis_refs <= set(origin.signal.context_refs)
    assert {config_ref, compute_ref, skill_ref, experience_ref} <= set(
        origin.signal.context_refs
    )

    projected_config = json.loads(by_ref[config_ref].content_bytes())
    assert projected_config == {
        "execution": config["execution"],
        "component": component,
        "protocol": protocol,
        "delivery_contract": {
            "enabled": True,
            "schema_version": 1,
            "protocol_id": component["protocol"],
            "contract": selected_delivery,
        },
        "required_experiment_spec": {
            key: value
            for key, value in required_spec.items()
            if key not in {"component", "protocol"}
        },
    }
    assert "protocol_catalog" not in projected_config
    assert "delivery_contracts" not in projected_config
    assert "goal" not in projected_config
    assert json.loads(by_ref[goal_ref].content_bytes()) == {
        "fact_id": goal_ref,
        "fact_type": "GoalDefined",
        "goal": goal,
    }

    response_ref = f"{request.attempt_id}--response"
    projected_response = json.loads(by_ref[response_ref].content_bytes())
    assert projected_response == {"typed_candidate": response.typed_candidate}
    assert "proposed_workspace_artifacts" not in projected_response

    source_ref = f"{request.attempt_id}--experiment.py"
    source_bytes = response.proposed_workspace_artifacts[0].content_bytes()
    assert by_ref[source_ref].content_bytes() == source_bytes
    assert response.proposed_workspace_artifacts[0].content_base64.encode() not in by_ref[
        response_ref
    ].content_bytes()
    for ref in hypothesis_refs | {compute_ref, skill_ref, experience_ref}:
        original = next(item for item in inputs if item.ref == ref)
        assert by_ref[ref].content_bytes() == original.content_bytes()


def test_analysis_rendezvous_reads_bounded_process_logs_from_result_staging(
    tmp_path: Path,
) -> None:
    store, publisher, source, observation = _analysis_context(tmp_path)
    log_root = (
        publisher.root
        / "result-staging"
        / str(source["attempt_id"])
        / "logs"
    )
    log_root.mkdir(parents=True)
    stdout = b"discarded-stdout-head\n" + (b"s" * 65_536) + b"\nstdout-tail\n"
    stderr = b"discarded-stderr-head\n" + (b"e" * 65_536) + b"\nstderr-tail\n"
    (log_root / "stdout.log").write_bytes(stdout)
    (log_root / "stderr.log").write_bytes(stderr)

    by_ref = rendezvous_inputs(
        store,
        publisher,
        "run-analysis",
        source,
        observation,
    )

    stdout_input = by_ref[f"{source['attempt_id']}--stdout"].content_bytes()
    stderr_input = by_ref[f"{source['attempt_id']}--stderr"].content_bytes()
    assert stdout_input == stdout[-65_536:]
    assert stderr_input == stderr[-65_536:]
    assert stdout_input.endswith(b"stdout-tail\n")
    assert stderr_input.endswith(b"stderr-tail\n")
    assert stdout_input
    assert stderr_input
    assert json.loads(
        by_ref[f"{source['attempt_id']}--evaluation"].content_bytes()
    ) == {
        "outcome": "INTEGRITY_REJECTED",
        "validation_error": (
            "text_contract_failed@report.md: "
            "missing required text 'tail class'"
        ),
        "execution_integrity_error": None,
    }


def test_analysis_rendezvous_keeps_empty_refs_for_missing_process_logs(
    tmp_path: Path,
) -> None:
    store, publisher, source, observation = _analysis_context(tmp_path)

    by_ref = rendezvous_inputs(
        store,
        publisher,
        "run-analysis",
        source,
        observation,
    )

    assert by_ref[f"{source['attempt_id']}--stdout"].content_bytes() == b""
    assert by_ref[f"{source['attempt_id']}--stderr"].content_bytes() == b""
