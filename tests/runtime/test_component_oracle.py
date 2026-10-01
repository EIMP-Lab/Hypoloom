from __future__ import annotations

import ast
import base64
import copy
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator, Mapping

import pytest

from hypoloom.agent_aci import (
    AgentCommand,
    AgentResponse,
    EmbeddedInput,
    WorkspaceArtifact,
    canonical_json,
)
from hypoloom.control_protocol import (
    ControlRequest,
    ControlResponse,
    request_over_socket,
)
from hypoloom.oracle import (
    Oracle,
    OracleIssue,
    _ComponentContract,
    _ComponentInput,
    _ComponentResult,
    _GoalComponentContract,
    _audit_component_rendezvous_origins,
    _audit_component_spec,
    _audit_component_fact_references,
    _audit_component_artifact_validators,
    _audit_component_result,
    _audit_component_verdicts,
    _audit_component_successor_chains,
    _audit_primary_implementation_lineage,
    _audit_agent_attempts,
    _audit_attempt_identities,
    _audit_execution_integrity,
    _audit_gpu_attempt_overlaps,
    _audit_resource_contract,
    _oracle_cpu_only_artifact,
    _audit_process_resource_observation,
    _artifact_materialized_root,
    _artifact_validator_contracts,
    _component_artifact_map,
    _component_apply_check_available,
    _component_metric_from_trials,
    _canonical_bwrap_argv,
    _oracle_component_execution,
    _oracle_component_environment_matches,
    _oracle_component_mounts,
    _oracle_implementation_sha256,
    _repairable_component_result_code,
    _result_analysis_inputs_match,
    _OracleComponentExecution,
    _OracleComponentMounts,
    _OracleMountBinding,
)
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.profile_admission import admit_profile


# tests/runtime/test_oracle.py is the revision-1 scalar/slot fixture and is a
# retirement candidate.  New component-contract coverage belongs here instead
# of extending that roughly 2,900-line historical fixture.

_REPOSITORY = Path(__file__).resolve().parents[2]
_COMPONENT_SCIENCE_CODES = {
    "accepted_agent_fact_identity_mismatch",
    "accept_outcome_candidate_mismatch",
    "assessment_component_identity_mismatch",
    "component_artifact_inventory_mismatch",
    "component_artifact_path_invalid",
    "component_artifact_validator_mismatch",
    "component_assessment_semantics_mismatch",
    "component_contract_invalid",
    "component_dependency_closure_mismatch",
    "expert_dependency_authorization_mismatch",
    "component_dependency_cycle",
    "component_dependency_invalid",
    "component_evidence_assessment_closure_mismatch",
    "component_fact_payload_identity_mismatch",
    "component_fact_ref_mismatch",
    "component_goal_count",
    "component_identity_invalid",
    "component_input_binding_mismatch",
    "component_input_contract_invalid",
    "component_metric_contract_invalid",
    "component_outcome_evidence_closure_mismatch",
    "component_preregistration_contract_mismatch",
    "component_protocol_mismatch",
    "component_result_attempt_mismatch",
    "component_result_identity_invalid",
    "component_result_json_invalid",
    "component_successor_branch",
    "component_successor_cycle",
    "component_successor_lineage_mismatch",
    "component_successor_ordinal_mismatch",
    "component_successor_tip_assessment_mismatch",
    "component_required_metric_mismatch",
    "component_trial_completeness_mismatch",
    "component_trial_invalid",
    "component_trial_metric_missing",
    "component_trial_plan_invalid",
    "component_trial_plan_mismatch",
    "duplicate_component_assessment",
    "duplicate_component_experiment",
    "duplicate_component_outcome",
    "duplicate_component_proposal",
    "evidence_component_identity_mismatch",
    "experiment_component_identity_mismatch",
    "hypothesis_component_identity_mismatch",
    "hypothesis_parent_identity_mismatch",
    "missing_terminal_experiment_outcome",
    "operational_outcome_causation_mismatch",
    "outcome_component_evaluator_mismatch",
    "outcome_component_identity_mismatch",
    "outcome_logical_artifact_mismatch",
    "outcome_method_family_mismatch",
    "outcome_metric_value_mismatch",
    "outcome_scientific_verdict_mismatch",
    "primary_implementation_lineage_mismatch",
    "process_attempt_ordinal_mismatch",
    "same_gpu_attempt_overlap",
    "unstable_artifact_path",
    "verdict_component_conjunction_mismatch",
    "verdict_required_component_coverage_mismatch",
}


def test_result_analysis_interpretation_preserves_v6_fact_ref() -> None:
    request = default_request(AgentCommand.RESULT_ANALYSIS)
    outcome = {
        "fact_id": "fact-current-outcome",
        "fact_type": "CanonicalOutcomeAccepted",
    }
    payloads = {
        "fact-current-outcome": {
            "component_id": request.component_id,
            "outcome_id": "outcome-current",
        }
    }
    old_projection = EmbeddedInput.from_bytes(
        "fact-current-outcome",
        "application/json",
        canonical_json(
            {
                "fact_id": "fact-current-outcome",
                "fact_type": "CanonicalOutcomeAccepted",
                "payload": payloads["fact-current-outcome"],
            }
        ),
    )
    request = request.__class__.from_dict(
        {
            **request.to_dict(),
            "immutable_context_refs": [old_projection.ref],
            "input_artifacts": [old_projection.to_dict()],
        }
    )

    assert _result_analysis_inputs_match(
        request, outcome, outcome, payloads, (), (), {}, "component-result/6",
    )
    assert not _result_analysis_inputs_match(
        request, outcome, outcome, payloads, (), (), {}, "component-result/7",
    )


def test_result_analysis_v7_rebuilds_current_execution_inputs(
    tmp_path: Path,
) -> None:
    request = default_request(AgentCommand.RESULT_ANALYSIS)
    experiment_id = "experiment-current"
    execution_id = "execution-current"
    process_attempt_id = "process-attempt"
    source_attempt_id = "source-attempt"
    spec = {
        "fact_id": "fact-current-spec",
        "fact_type": "ExperimentPreregistered",
        "experiment_id": experiment_id,
        "execution_id": execution_id,
    }
    outcome = {
        "fact_id": "fact-current-outcome",
        "fact_type": "CanonicalOutcomeAccepted",
        "experiment_id": experiment_id,
        "execution_id": execution_id,
        "artifact_id": "artifact-current",
    }
    payloads = {
        "fact-current-spec": {
            "component_id": request.component_id,
            "execution_contract": {"source_agent_attempt_id": source_attempt_id},
        },
        "fact-current-outcome": {
            "component_id": request.component_id,
            "outcome": "SUCCESS",
        },
    }
    transition = {
        "workflow_id": experiment_id,
        "revision": 7,
        "fact_id": "fact-current-outcome",
    }
    evaluation = {"outcome": "SUCCESS", "validation_error": None}
    process_observation = {
        "exit_code": 0,
        "stdout_path": "logs/stdout.log",
        "stderr_path": "logs/stderr.log",
    }
    source_bytes = b"source-prefix\n" * 6_000 + b"source-tail\n"
    source_response = FakeAgent().invoke(
        default_request(AgentCommand.RESULT_ANALYSIS)
    )
    assert isinstance(source_response, AgentResponse)
    source_response = replace(
        source_response,
        proposed_workspace_artifacts=(
            WorkspaceArtifact.from_bytes("experiment.py", source_bytes),
        ),
    )
    attempts = (
        {
            "attempt_id": source_attempt_id,
            "run_id": request.run_id,
            "kind": "AGENT",
            "state": "CLOSED",
            "terminal_reason": "SUCCEEDED",
        },
        {
            "attempt_id": process_attempt_id,
            "run_id": request.run_id,
            "kind": "PROCESS",
            "state": "CLOSED",
            "terminal_reason": "SUCCEEDED",
            "workflow_id": experiment_id,
            "experiment_id": experiment_id,
            "execution_id": execution_id,
        },
    )
    attempt_payloads = {
        source_attempt_id: {"response": source_response.to_dict()},
        process_attempt_id: process_observation,
    }
    artifact = {
        "artifact_id": outcome["artifact_id"],
        "run_id": request.run_id,
        "experiment_id": experiment_id,
        "execution_id": execution_id,
        "attempt_id": process_attempt_id,
        "artifact_kind": "result",
        "path": (
            f"{request.run_id}/{experiment_id}/{execution_id}/"
            f"{process_attempt_id}/result"
        ),
    }
    artifact_root = tmp_path / "artifacts"
    publication_root = artifact_root.joinpath(*artifact["path"].split("/"))
    publication_root.mkdir(parents=True)
    result_bytes = b"result-prefix\n" * 6_000 + b"result-tail\n"
    result_path = publication_root / "result.json"
    result_path.write_bytes(result_bytes)
    log_root = artifact_root / "result-staging" / process_attempt_id / "logs"
    log_root.mkdir(parents=True)
    stdout_bytes = b"stdout-prefix\n" * 6_000 + b"stdout-tail\n"
    stderr_bytes = b"stderr-tail\n"
    stdout_path = log_root / "stdout.log"
    stderr_path = log_root / "stderr.log"
    stdout_path.write_bytes(stdout_bytes)
    stderr_path.write_bytes(stderr_bytes)

    expected_inputs = {
        f"{request.command_id}--current-spec": (
            "application/json",
            canonical_json({
                **spec,
                "payload": payloads["fact-current-spec"],
                "refs": [],
            }),
        ),
        f"{request.command_id}--current-outcome": (
            "application/json",
            canonical_json({
                **outcome,
                "payload": payloads["fact-current-outcome"],
                "refs": [],
            }),
        ),
        f"{request.command_id}--current-evaluation": (
            "application/json",
            canonical_json(evaluation),
        ),
        f"{request.command_id}--current-process": (
            "application/json",
            canonical_json(process_observation),
        ),
        f"{request.command_id}--current-experiment.py": (
            "text/x-python",
            source_bytes[-65_536:],
        ),
        f"{request.command_id}--current-result.json": (
            "application/json",
            result_bytes[-65_536:],
        ),
        f"{request.command_id}--current-stdout": (
            "text/plain",
            stdout_bytes[-65_536:],
        ),
        f"{request.command_id}--current-stderr": (
            "text/plain",
            stderr_bytes,
        ),
    }

    def projected_request(
        *,
        overrides: Mapping[str, bytes] | None = None,
        include_result: bool = True,
    ) -> object:
        selected = {
            ref: (media_type, (overrides or {}).get(ref, content))
            for ref, (media_type, content) in expected_inputs.items()
            if include_result or not ref.endswith("--current-result.json")
        }
        inputs = tuple(
            EmbeddedInput.from_bytes(ref, media_type, content)
            for ref, (media_type, content) in selected.items()
        )
        return request.__class__.from_dict({
            **request.to_dict(),
            "immutable_context_refs": [item.ref for item in inputs],
            "input_artifacts": [item.to_dict() for item in inputs],
            "max_input_bytes": 1_000_000,
        })

    def matches(candidate: object) -> bool:
        return _result_analysis_inputs_match(
            candidate,
            spec,
            outcome,
            payloads,
            (),
            (transition,),
            {
                f"{experiment_id}@7": {
                    "attempt_id": process_attempt_id,
                    "outcome": evaluation,
                }
            },
            "component-result/7",
            attempts=attempts,
            attempt_payloads=attempt_payloads,
            artifacts=(artifact,),
            artifact_root=artifact_root,
        )

    valid_request = projected_request()
    assert matches(valid_request)
    for suffix in (
        "process",
        "experiment.py",
        "result.json",
        "stdout",
        "stderr",
    ):
        ref = f"{request.command_id}--current-{suffix}"
        assert not matches(projected_request(overrides={ref: b"tampered"})), ref

    result_path.unlink()
    assert matches(projected_request(include_result=False))
    assert not matches(valid_request)
    stdout_path.unlink()
    stderr_path.unlink()
    expected_inputs[f"{request.command_id}--current-stdout"] = ("text/plain", b"")
    expected_inputs[f"{request.command_id}--current-stderr"] = ("text/plain", b"")
    assert matches(projected_request(include_result=False))

def _metric_component(
    *,
    component_id: str,
    metric: str,
    seeds: tuple[int, ...] = (),
    arms: tuple[str, ...] = (),
    required_metrics: tuple[str, ...] = (),
    checkpoint_count: int | None = None,
) -> _ComponentContract:
    inputs = (
        (
            _ComponentInput(
                resource="checkpoint-input",
                producer_component="primary.final",
                logical_artifact="checkpoints",
                min_count=checkpoint_count,
            ),
        )
        if checkpoint_count is not None
        else ()
    )
    return _ComponentContract(
        component_id=component_id,
        tier="primary",
        owner_role="researcher",
        proposer_roles=("researcher",),
        dependencies=("primary.final",) if inputs else (),
        required_inputs=inputs,
        result_kind="metric",
        metric_name=metric,
        metric_direction="MAXIMIZE",
        metric_target="0",
        seeds=seeds,
        arms=arms,
        trial_ids=(
            tuple(f"{arm}:{seed}" for arm in arms for seed in seeds)
            if arms
            else tuple(str(seed) for seed in seeds)
        ),
        required_artifacts=(),
        artifact_counts={},
        required_metrics=required_metrics,
    )


@pytest.mark.parametrize(
    ("evaluator_version", "accepted_outcome", "expect_mismatch"),
    [
        ("component-result/7", "EXECUTION_FAILURE", False),
        ("component-result/7", "INTEGRITY_REJECTED", True),
        ("component-result/6", "EXECUTION_FAILURE", False),
        ("component-result/6", "INTEGRITY_REJECTED", True),
        ("component-result/5", "EXECUTION_FAILURE", False),
        ("component-result/5", "INTEGRITY_REJECTED", True),
        ("component-result/4", "EXECUTION_FAILURE", False),
        ("component-result/3", "EXECUTION_FAILURE", False),
        ("component-result/2", "EXECUTION_FAILURE", False),
        ("component-result/1", "EXECUTION_FAILURE", True),
        ("component-result/1", "INTEGRITY_REJECTED", False),
    ],
)
def test_repairable_result_envelope_causation_is_versioned(
    tmp_path: Path,
    evaluator_version: str,
    accepted_outcome: str,
    expect_mismatch: bool,
) -> None:
    component = _metric_component(component_id="primary.scalar", metric="score")
    artifact = {
        "run_id": "run",
        "experiment_id": "experiment",
        "execution_id": "execution",
        "attempt_id": "attempt",
        "artifact_kind": "RESULT",
        "path": "run/experiment/execution/attempt/RESULT",
    }
    publication = tmp_path / artifact["path"]
    publication.mkdir(parents=True)
    (publication / "result.json").write_text(
        '{"artifacts":{}}', encoding="utf-8"
    )
    outcome = {"fact_id": "outcome-fact"}
    candidate = {
        "outcome": accepted_outcome,
        "evaluator_version": evaluator_version,
        "metric_value": None,
        "criterion_met": None,
        "validation_error": "invalid_result_shape@result.json: missing trials",
        "execution_integrity_error": None,
    }
    issues: list[OracleIssue] = []

    _audit_component_result(
        tmp_path,
        component,
        {},
        {"evaluator_version": evaluator_version},
        outcome,
        {
            "outcome": accepted_outcome,
            "metric_value": None,
            "logical_artifacts": [],
            "evaluator_version": evaluator_version,
        },
        artifact,
        {"attempt": {"terminal_reason": "SUCCEEDED"}},
        None,
        [outcome],
        {"outcome-fact": {"outcome": candidate}},
        issues,
    )

    assert (
        "operational_outcome_causation_mismatch" in {issue.code for issue in issues}
    ) is expect_mismatch
    if not expect_mismatch:
        assert issues == []


@pytest.mark.parametrize(
    (
        "evaluator_version",
        "dependencies",
        "forged_diagnostic",
        "accepted_outcome",
        "expect_mismatch",
    ),
    [
        ("component-result/7", (), False, "EXECUTION_FAILURE", False),
        ("component-result/7", (), False, "INTEGRITY_REJECTED", True),
        ("component-result/6", (), False, "EXECUTION_FAILURE", False),
        ("component-result/6", (), False, "INTEGRITY_REJECTED", True),
        ("component-result/5", (), False, "INTEGRITY_REJECTED", False),
        ("component-result/5", (), False, "EXECUTION_FAILURE", True),
        (
            "component-result/6",
            ("must.upstream",),
            False,
            "EXECUTION_FAILURE",
            True,
        ),
        ("component-result/6", (), True, "EXECUTION_FAILURE", True),
    ],
)
def test_unbound_optional_identity_repair_causation_is_versioned(
    tmp_path: Path,
    evaluator_version: str,
    dependencies: tuple[str, ...],
    forged_diagnostic: bool,
    accepted_outcome: str,
    expect_mismatch: bool,
) -> None:
    component = _ComponentContract(
        component_id="must.report",
        tier="must",
        owner_role="researcher",
        proposer_roles=("researcher",),
        dependencies=dependencies,
        required_inputs=(),
        result_kind="artifact",
        metric_name=None,
        metric_direction=None,
        metric_target=None,
        seeds=(),
        arms=(),
        trial_ids=(),
        required_artifacts=("report",),
        artifact_counts={"report": 1},
        required_metrics=(),
    )
    artifact = {
        "run_id": "run",
        "experiment_id": "experiment",
        "execution_id": "execution",
        "attempt_id": "attempt",
        "artifact_kind": "RESULT",
        "path": "run/experiment/execution/attempt/RESULT",
    }
    publication = tmp_path / artifact["path"]
    publication.mkdir(parents=True)
    (publication / "report.md").write_text("complete report\n", encoding="utf-8")
    (publication / "result.json").write_text(
        json.dumps({
            "artifacts": {"report": "report.md"},
            "identity": {
                "method_family_id": "family-a",
                "implementation_sha256": "pending_integration",
                "core_config_sha256": "pending_integration",
            },
        }),
        encoding="utf-8",
    )
    outcome = {"fact_id": "outcome-fact"}
    diagnostic = (
        "invalid_identity@result.json.identity: "
        "digests must be 64 lowercase hex"
    )
    candidate = {
        "outcome": accepted_outcome,
        "evaluator_version": evaluator_version,
        "metric_value": None,
        "criterion_met": None,
        "validation_error": (
            "invalid_identity@result.json.identity: forged diagnostic"
            if forged_diagnostic else diagnostic
        ),
        "execution_integrity_error": None,
    }
    issues: list[OracleIssue] = []

    _audit_component_result(
        tmp_path,
        component,
        {},
        {
            "evaluator_version": evaluator_version,
            "method_family_id": "family-a",
        },
        outcome,
        {
            "outcome": accepted_outcome,
            "metric_value": None,
            "logical_artifacts": [],
            "evaluator_version": evaluator_version,
        },
        artifact,
        {"attempt": {"terminal_reason": "SUCCEEDED"}},
        None,
        [outcome],
        {"outcome-fact": {"outcome": candidate}},
        issues,
    )

    assert (
        "operational_outcome_causation_mismatch" in {issue.code for issue in issues}
    ) is expect_mismatch


def test_unbound_optional_identity_oracle_fails_closed_on_bound_or_bad_inputs(
    tmp_path: Path,
) -> None:
    base = _ComponentContract(
        component_id="must.report",
        tier="must",
        owner_role="researcher",
        proposer_roles=("researcher",),
        dependencies=(),
        required_inputs=(),
        result_kind="artifact",
        metric_name=None,
        metric_direction=None,
        metric_target=None,
        seeds=(),
        arms=(),
        trial_ids=(),
        required_artifacts=("report",),
        artifact_counts={"report": 1},
        required_metrics=(),
    )
    publication = tmp_path / "publication"
    publication.mkdir()
    (publication / "report.md").write_text("complete report\n", encoding="utf-8")

    def write_identity(identity: dict[str, object]) -> None:
        (publication / "result.json").write_text(
            json.dumps({
                "artifacts": {"report": "report.md"},
                "identity": identity,
            }),
            encoding="utf-8",
        )

    valid_shape = {
        "method_family_id": "family-a",
        "implementation_sha256": "pending_integration",
    }
    write_identity(valid_shape)
    diagnostic = (
        "invalid_identity@result.json.identity: "
        "digests must be 64 lowercase hex"
    )
    assert _repairable_component_result_code(
        publication, base, "component-result/6", "family-a"
    ) == ("invalid_identity", diagnostic)

    for identity in (
        {"implementation_sha256": "pending_integration"},
        {**valid_shape, "method_family_id": "family-b"},
    ):
        write_identity(identity)
        assert _repairable_component_result_code(
            publication, base, "component-result/6", "family-a"
        ) is None

    write_identity(valid_shape)
    required_input = _ComponentInput(
        resource="upstream",
        producer_component="must.upstream",
        logical_artifact="report",
        min_count=1,
    )
    bound = _ComponentContract(
        **{
            field: getattr(base, field)
            for field in base.__dataclass_fields__
            if field not in {"dependencies", "required_inputs"}
        },
        dependencies=("must.upstream",),
        required_inputs=(required_input,),
    )
    assert _repairable_component_result_code(
        publication, bound, "component-result/6", "family-a"
    ) is None

    patch_bound = _ComponentContract(
        **{
            field: getattr(base, field)
            for field in base.__dataclass_fields__
            if field not in {"required_artifacts", "artifact_counts"}
        },
        required_artifacts=("implementation_patch",),
        artifact_counts={"implementation_patch": 1},
    )
    patch_publication = tmp_path / "patch-publication"
    patch_publication.mkdir()
    (patch_publication / "candidate.patch").write_text(
        "--- a/a\n+++ b/a\n@@ -1 +1 @@\n-a\n+b\n", encoding="utf-8"
    )
    (patch_publication / "result.json").write_text(
        json.dumps({
            "artifacts": {"implementation_patch": "candidate.patch"},
            "identity": valid_shape,
        }),
        encoding="utf-8",
    )
    assert _repairable_component_result_code(
        patch_publication, patch_bound, "component-result/6", "family-a"
    ) is None

    diff_bound = _ComponentContract(
        **{
            field: getattr(base, field)
            for field in base.__dataclass_fields__
            if field != "artifact_validators"
        },
        artifact_validators=({
            "artifact": "report",
            "type": "unified_diff",
            "apply_check": False,
        },),
    )
    diff_publication = tmp_path / "diff-publication"
    diff_publication.mkdir()
    (diff_publication / "report.md").write_text(
        "--- a/a\n+++ b/a\n@@ -1 +1 @@\n-a\n+b\n", encoding="utf-8"
    )
    (diff_publication / "result.json").write_text(
        json.dumps({
            "artifacts": {"report": "report.md"},
            "identity": valid_shape,
        }),
        encoding="utf-8",
    )
    assert _repairable_component_result_code(
        diff_publication, diff_bound, "component-result/6", "family-a"
    ) is None

    invalid_text = _ComponentContract(
        **{
            field: getattr(base, field)
            for field in base.__dataclass_fields__
            if field != "artifact_validators"
        },
        artifact_validators=({
            "artifact": "report",
            "type": "text_contract",
            "required_terms": ["missing words"],
        },),
    )
    assert _repairable_component_result_code(
        publication, invalid_text, "component-result/6", "family-a"
    ) is None
    (publication / "report.md").unlink()
    assert _repairable_component_result_code(
        publication, base, "component-result/6", "family-a"
    ) is None


def test_v7_reconstructs_only_exact_unified_diff_shape_failure(
    tmp_path: Path,
) -> None:
    component = _ComponentContract(
        component_id="must.implementation",
        tier="must",
        owner_role="researcher",
        proposer_roles=("researcher",),
        dependencies=(),
        required_inputs=(),
        result_kind="artifact",
        metric_name=None,
        metric_direction=None,
        metric_target=None,
        seeds=(),
        arms=(),
        trial_ids=(),
        required_artifacts=("implementation_patch",),
        artifact_counts={"implementation_patch": 1},
        required_metrics=(),
        artifact_validators=({
            "artifact": "implementation_patch",
            "type": "unified_diff",
            "apply_check": True,
        },),
    )
    publication = tmp_path / "publication"
    publication.mkdir()
    patch_path = publication / "implementation.patch"

    def write_candidate(payload: bytes) -> None:
        patch_path.write_bytes(payload)
        (publication / "result.json").write_text(
            json.dumps({
                "artifacts": {
                    "implementation_patch": "implementation.patch",
                },
                "identity": {
                    "method_family_id": "family-a",
                    "implementation_sha256": hashlib.sha256(payload).hexdigest(),
                },
            }),
            encoding="utf-8",
        )

    write_candidate(
        b"--- /dev/null+++ b/src/model.py@@ -0,0 +1 @@+candidate\n"
    )
    diagnostic = (
        "invalid_unified_diff@implementation.patch: "
        "must contain paired headers and a text hunk"
    )
    assert _repairable_component_result_code(
        publication,
        component,
        "component-result/7",
        "family-a",
        apply_check_available=True,
    ) == ("invalid_unified_diff", diagnostic)
    assert _repairable_component_result_code(
        publication,
        component,
        "component-result/7",
        "family-a",
        apply_check_available=False,
    ) is None
    assert _repairable_component_result_code(
        publication,
        component,
        "component-result/6",
        "family-a",
    ) is None

    implementation_bound = _ComponentContract(
        **{
            field: getattr(component, field)
            for field in component.__dataclass_fields__
            if field != "target_overlay_policy"
        },
        target_overlay_policy="latest",
    )
    assert _repairable_component_result_code(
        publication,
        implementation_bound,
        "component-result/7",
        "family-a",
        apply_check_available=True,
    ) is None

    write_candidate(
        b"--- ../../outside.py+++ b/src/model.py@@ -1 +1 @@-old+new\n"
    )
    assert _repairable_component_result_code(
        publication,
        component,
        "component-result/7",
        "family-a",
        apply_check_available=True,
    ) is None

    write_candidate(
        b"--- ../../outside.py\n+++ b/src/model.py\n@@ -1 +1 @@\n-old\n+new\n"
    )
    assert _repairable_component_result_code(
        publication,
        component,
        "component-result/7",
        "family-a",
        apply_check_available=True,
    ) is None

    write_candidate(b"\xff\xfe")
    assert _repairable_component_result_code(
        publication,
        component,
        "component-result/7",
        "family-a",
        apply_check_available=True,
    ) is None

    multiple = _ComponentContract(
        component_id="must.patches",
        tier="must",
        owner_role="researcher",
        proposer_roles=("researcher",),
        dependencies=(),
        required_inputs=(),
        result_kind="artifact",
        metric_name=None,
        metric_direction=None,
        metric_target=None,
        seeds=(),
        arms=(),
        trial_ids=(),
        required_artifacts=("patches",),
        artifact_counts={"patches": 1},
        required_metrics=(),
        artifact_validators=({
            "artifact": "patches",
            "type": "unified_diff",
            "apply_check": False,
        },),
    )
    patch_path.write_text(
        "--- a/src/model.py\n+++ b/src/model.py\n@@ -1 +1 @@\n-old\n+new\n",
        encoding="utf-8",
    )
    malformed_path = publication / "malformed.patch"
    malformed_path.write_text(
        "--- /dev/null+++ b/src/new.py@@ -0,0 +1 @@+new\n",
        encoding="utf-8",
    )
    (publication / "result.json").write_text(
        json.dumps({
            "artifacts": {
                "patches": ["malformed.patch", "implementation.patch"],
            },
            "identity": {"method_family_id": "family-a"},
        }),
        encoding="utf-8",
    )
    assert _repairable_component_result_code(
        publication,
        multiple,
        "component-result/7",
        "family-a",
        apply_check_available=False,
    ) is None


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ([["HYPOLOOM_TARGET_ROOT", "/frozen/checkout"]], True),
        ([], False),
        ({"HYPOLOOM_TARGET_ROOT": "/not-the-frozen-shape"}, False),
        ([
            ["HYPOLOOM_TARGET_ROOT", "/first"],
            ["HYPOLOOM_TARGET_ROOT", "/second"],
        ], False),
    ],
)
def test_v7_apply_check_availability_comes_from_the_exact_preregistered_spec(
    environment: object,
    expected: bool,
) -> None:
    assert _component_apply_check_available({
        "execution_contract": {"environment": environment},
    }) is expected


@pytest.mark.parametrize(
    ("logical_artifacts", "expect_mismatch"),
    [
        ([], False),
        ([{"logical_artifact": "report", "paths": ["report.md"]}], True),
        ({"report": ["report.md"]}, True),
        (None, True),
    ],
)
def test_noncomparable_outcome_requires_exact_empty_logical_artifacts(
    tmp_path: Path,
    logical_artifacts: object,
    expect_mismatch: bool,
) -> None:
    component = _metric_component(component_id="primary.scalar", metric="score")
    artifact = {
        "run_id": "run",
        "experiment_id": "experiment",
        "execution_id": "execution",
        "attempt_id": "attempt",
        "artifact_kind": "RESULT",
        "path": "run/experiment/execution/attempt/RESULT",
    }
    outcome = {"fact_id": "outcome-fact"}
    issues: list[OracleIssue] = []

    _audit_component_result(
        tmp_path,
        component,
        {},
        {"evaluator_version": "component-result/6"},
        outcome,
        {
            "outcome": "EXECUTION_FAILURE",
            "metric_value": None,
            "logical_artifacts": logical_artifacts,
            "evaluator_version": "component-result/6",
        },
        artifact,
        {"attempt": {"terminal_reason": "FAILED"}},
        None,
        [outcome],
        {"outcome-fact": {"outcome": {}}},
        issues,
    )

    assert (
        "outcome_logical_artifact_mismatch" in {issue.code for issue in issues}
    ) is expect_mismatch


@pytest.mark.parametrize(
    ("accepted_evaluator", "expect_mismatch"),
    [
        ("component-result/6", False),
        ("component-result/5", True),
        (None, True),
    ],
)
def test_noncomparable_outcome_preserves_frozen_evaluator_identity(
    tmp_path: Path,
    accepted_evaluator: object,
    expect_mismatch: bool,
) -> None:
    component = _metric_component(component_id="primary.scalar", metric="score")
    artifact = {
        "run_id": "run",
        "experiment_id": "experiment",
        "execution_id": "execution",
        "attempt_id": "attempt",
        "artifact_kind": "RESULT",
        "path": "run/experiment/execution/attempt/RESULT",
    }
    outcome = {"fact_id": "outcome-fact"}
    issues: list[OracleIssue] = []

    _audit_component_result(
        tmp_path,
        component,
        {},
        {"evaluator_version": "component-result/6"},
        outcome,
        {
            "outcome": "EXECUTION_FAILURE",
            "metric_value": None,
            "logical_artifacts": [],
            "evaluator_version": accepted_evaluator,
        },
        artifact,
        {"attempt": {"terminal_reason": "FAILED"}},
        None,
        [outcome],
        {"outcome-fact": {"outcome": {}}},
        issues,
    )

    assert (
        "outcome_component_evaluator_mismatch" in {issue.code for issue in issues}
    ) is expect_mismatch


@pytest.mark.parametrize(
    (
        "evaluator_version",
        "report",
        "identity",
        "validation_error",
        "expect_mismatch",
    ),
    [
        (
            "component-result/4",
            "This report deliberately says tail-class instead of the required words.",
            None,
            "text_contract_failed@report.md: missing required text 'tail class'",
            False,
        ),
        (
            "component-result/3",
            "This report deliberately says tail-class instead of the required words.",
            None,
            "text_contract_failed@report.md: missing required text 'tail class'",
            False,
        ),
        (
            "component-result/2",
            "This report deliberately says tail-class instead of the required words.",
            None,
            "text_contract_failed@report.md: missing required text 'tail class'",
            True,
        ),
        (
            "component-result/3",
            "This report contains the required tail class words and is valid.",
            None,
            "text_contract_failed@report.md: missing required text 'tail class'",
            True,
        ),
        (
            "component-result/3",
            "This report deliberately says tail-class instead of the required words.",
            {"method_family_id": "family-a"},
            "text_contract_failed@report.md: missing required text 'tail class'",
            True,
        ),
        (
            "component-result/4",
            "This report deliberately says tail-class instead of the required words.",
            {
                "method_family_id": "family-a",
                "implementation_sha256": "a" * 64,
                "core_config_sha256": "b" * 64,
            },
            "text_contract_failed@report.md: missing required text 'tail class'",
            False,
        ),
        (
            "component-result/4",
            "This report deliberately says tail-class instead of the required words.",
            {"method_family_id": "family-b"},
            "text_contract_failed@report.md: missing required text 'tail class'",
            True,
        ),
        (
            "component-result/4",
            "This report deliberately says tail-class instead of the required words.",
            {"method_family_id": "invalid family"},
            "text_contract_failed@report.md: missing required text 'tail class'",
            True,
        ),
        (
            "component-result/4",
            "This report deliberately says tail-class instead of the required words.",
            {"implementation_sha256": "not-a-digest"},
            "text_contract_failed@report.md: missing required text 'tail class'",
            True,
        ),
        (
            "component-result/3",
            "This report deliberately says tail-class instead of the required words.",
            None,
            "text_contract_failed@report.md: missing required text 'other words'",
            True,
        ),
    ],
)
def test_text_contract_failure_causation_is_narrow_and_versioned(
    tmp_path: Path,
    evaluator_version: str,
    report: str,
    identity: Mapping[str, str] | None,
    validation_error: str,
    expect_mismatch: bool,
) -> None:
    component = _ComponentContract(
        component_id="must.report",
        tier="must",
        owner_role="researcher",
        proposer_roles=("researcher",),
        dependencies=(),
        required_inputs=(),
        result_kind="artifact",
        metric_name=None,
        metric_direction=None,
        metric_target=None,
        seeds=(),
        arms=(),
        trial_ids=(),
        required_artifacts=("report",),
        artifact_counts={"report": 1},
        required_metrics=(),
        artifact_validators=(
            {
                "artifact": "report",
                "type": "text_contract",
                "min_chars": 32,
                "required_terms": ["tail class"],
            },
        ),
    )
    artifact = {
        "run_id": "run",
        "experiment_id": "experiment",
        "execution_id": "execution",
        "attempt_id": "attempt",
        "artifact_kind": "RESULT",
        "path": "run/experiment/execution/attempt/RESULT",
    }
    publication = tmp_path / artifact["path"]
    publication.mkdir(parents=True)
    (publication / "report.md").write_text(report, encoding="utf-8")
    envelope: dict[str, object] = {"artifacts": {"report": "report.md"}}
    if identity is not None:
        envelope["identity"] = identity
    (publication / "result.json").write_text(
        json.dumps(envelope),
        encoding="utf-8",
    )
    outcome = {"fact_id": "outcome-fact"}
    candidate = {
        "outcome": "EXECUTION_FAILURE",
        "evaluator_version": evaluator_version,
        "metric_value": None,
        "criterion_met": None,
        "validation_error": validation_error,
        "execution_integrity_error": None,
    }
    issues: list[OracleIssue] = []

    _audit_component_result(
        tmp_path,
        component,
        {},
        {
            "evaluator_version": evaluator_version,
            "method_family_id": "family-a",
        },
        outcome,
        {"outcome": "EXECUTION_FAILURE", "metric_value": None},
        artifact,
        {"attempt": {"terminal_reason": "SUCCEEDED"}},
        None,
        [outcome],
        {"outcome-fact": {"outcome": candidate}},
        issues,
    )

    assert (
        "operational_outcome_causation_mismatch" in {
            issue.code for issue in issues
        }
    ) is expect_mismatch


@pytest.mark.parametrize(
    ("implementation_identity", "expect_mismatch"),
    [
        ("matching", False),
        ("mismatched", True),
        ("missing", True),
    ],
)
@pytest.mark.parametrize(
    "evaluator_version", ["component-result/4", "component-result/5"]
)
def test_v4_v5_text_contract_repair_checks_implementation_identity(
    tmp_path: Path,
    implementation_identity: str,
    expect_mismatch: bool,
    evaluator_version: str,
) -> None:
    component = _ComponentContract(
        component_id="must.report",
        tier="must",
        owner_role="researcher",
        proposer_roles=("researcher",),
        dependencies=(),
        required_inputs=(),
        result_kind="artifact",
        metric_name=None,
        metric_direction=None,
        metric_target=None,
        seeds=(),
        arms=(),
        trial_ids=(),
        required_artifacts=("report", "implementation_patch"),
        artifact_counts={"report": 1, "implementation_patch": 1},
        required_metrics=(),
        artifact_validators=(
            {
                "artifact": "report",
                "type": "text_contract",
                "required_terms": ["tail class"],
            },
        ),
    )
    artifact = {
        "run_id": "run",
        "experiment_id": "experiment",
        "execution_id": "execution",
        "attempt_id": "attempt",
        "artifact_kind": "RESULT",
        "path": "run/experiment/execution/attempt/RESULT",
    }
    publication = tmp_path / artifact["path"]
    publication.mkdir(parents=True)
    (publication / "report.md").write_text(
        "This report deliberately says tail-class.", encoding="utf-8"
    )
    patch = b"candidate implementation bytes\n"
    (publication / "implementation.patch").write_bytes(patch)
    identity: dict[str, str] = {"method_family_id": "family-a"}
    if implementation_identity != "missing":
        identity["implementation_sha256"] = (
            hashlib.sha256(patch).hexdigest()
            if implementation_identity == "matching"
            else "0" * 64
        )
    (publication / "result.json").write_text(
        json.dumps(
            {
                "artifacts": {
                    "report": "report.md",
                    "implementation_patch": "implementation.patch",
                },
                "identity": identity,
            }
        ),
        encoding="utf-8",
    )
    outcome = {"fact_id": "outcome-fact"}
    candidate = {
        "outcome": "EXECUTION_FAILURE",
        "evaluator_version": evaluator_version,
        "metric_value": None,
        "criterion_met": None,
        "validation_error": (
            "text_contract_failed@report.md: "
            "missing required text 'tail class'"
        ),
        "execution_integrity_error": None,
    }
    issues: list[OracleIssue] = []

    _audit_component_result(
        tmp_path,
        component,
        {},
        {
            "evaluator_version": evaluator_version,
            "method_family_id": "family-a",
        },
        outcome,
        {"outcome": "EXECUTION_FAILURE", "metric_value": None},
        artifact,
        {"attempt": {"terminal_reason": "SUCCEEDED"}},
        None,
        [outcome],
        {"outcome-fact": {"outcome": candidate}},
        issues,
    )

    assert (
        "operational_outcome_causation_mismatch" in {
            issue.code for issue in issues
        }
    ) is expect_mismatch


@pytest.mark.parametrize("later_kind", ["json_contract", "unified_diff"])
def test_v4_text_contract_failure_cannot_mask_later_integrity_validator(
    tmp_path: Path,
    later_kind: str,
) -> None:
    later_artifact = "manifest" if later_kind == "json_contract" else "patch"
    later_validator = (
        {
            "artifact": later_artifact,
            "type": "json_contract",
            "required_paths": ["required"],
        }
        if later_kind == "json_contract"
        else {
            "artifact": later_artifact,
            "type": "unified_diff",
            "apply_check": True,
        }
    )
    component = _ComponentContract(
        component_id="must.report",
        tier="must",
        owner_role="researcher",
        proposer_roles=("researcher",),
        dependencies=(),
        required_inputs=(),
        result_kind="artifact",
        metric_name=None,
        metric_direction=None,
        metric_target=None,
        seeds=(),
        arms=(),
        trial_ids=(),
        required_artifacts=("report", later_artifact),
        artifact_counts={"report": 1, later_artifact: 1},
        required_metrics=(),
        artifact_validators=(
            {
                "artifact": "report",
                "type": "text_contract",
                "required_terms": ["tail class"],
            },
            later_validator,
        ),
    )
    artifact = {
        "run_id": "run",
        "experiment_id": "experiment",
        "execution_id": "execution",
        "attempt_id": "attempt",
        "artifact_kind": "RESULT",
        "path": "run/experiment/execution/attempt/RESULT",
    }
    publication = tmp_path / artifact["path"]
    publication.mkdir(parents=True)
    (publication / "report.md").write_text(
        "This report deliberately says tail-class.", encoding="utf-8"
    )
    later_name = "manifest.json" if later_kind == "json_contract" else "change.patch"
    (publication / later_name).write_text("invalid", encoding="utf-8")
    (publication / "result.json").write_text(
        json.dumps(
            {
                "artifacts": {
                    "report": "report.md",
                    later_artifact: later_name,
                },
                "identity": {"method_family_id": "family-a"},
            }
        ),
        encoding="utf-8",
    )
    outcome = {"fact_id": "outcome-fact"}
    candidate = {
        "outcome": "EXECUTION_FAILURE",
        "evaluator_version": "component-result/4",
        "metric_value": None,
        "criterion_met": None,
        "validation_error": (
            "text_contract_failed@report.md: "
            "missing required text 'tail class'"
        ),
        "execution_integrity_error": None,
    }
    issues: list[OracleIssue] = []

    _audit_component_result(
        tmp_path,
        component,
        {},
        {
            "evaluator_version": "component-result/4",
            "method_family_id": "family-a",
        },
        outcome,
        {"outcome": "EXECUTION_FAILURE", "metric_value": None},
        artifact,
        {"attempt": {"terminal_reason": "SUCCEEDED"}},
        None,
        [outcome],
        {"outcome-fact": {"outcome": candidate}},
        issues,
    )

    assert "operational_outcome_causation_mismatch" in {
        issue.code for issue in issues
    }


def _extra_file_inventory_component(
    *,
    report_count: int = 1,
    apply_check: bool = True,
) -> _ComponentContract:
    return _ComponentContract(
        component_id="must.report",
        tier="must",
        owner_role="researcher",
        proposer_roles=("researcher",),
        dependencies=(),
        required_inputs=(),
        result_kind="artifact",
        metric_name=None,
        metric_direction=None,
        metric_target=None,
        seeds=(),
        arms=(),
        trial_ids=(),
        required_artifacts=("report", "manifest", "implementation_patch"),
        artifact_counts={
            "report": report_count,
            "manifest": 1,
            "implementation_patch": 1,
        },
        required_metrics=(),
        artifact_validators=(
            {
                "artifact": "report",
                "type": "text_contract",
                "required_terms": ["tail class"],
            },
            {
                "artifact": "manifest",
                "type": "json_contract",
                "required_paths": ["status"],
                "allowed_values": {"status": ["validated"]},
            },
            {
                "artifact": "implementation_patch",
                "type": "unified_diff",
                "apply_check": apply_check,
            },
        ),
    )


def _audit_extra_file_inventory_causation(
    tmp_path: Path,
    *,
    evaluator_version: str = "component-result/5",
    mutation: str = "none",
    apply_check: bool = True,
    validation_error: str = (
        "artifact_inventory_mismatch@artifact_dir: "
        "undeclared=['scratch.py'], missing=[]"
    ),
) -> set[str]:
    component = _extra_file_inventory_component(
        report_count=2 if mutation == "under_count" else 1,
        apply_check=apply_check,
    )
    artifact = {
        "run_id": "run",
        "experiment_id": "experiment",
        "execution_id": "execution",
        "attempt_id": "attempt",
        "artifact_kind": "RESULT",
        "path": "run/experiment/execution/attempt/RESULT",
    }
    publication = tmp_path / artifact["path"]
    publication.mkdir(parents=True)
    report = publication / "report.md"
    report.write_text(
        "This report contains the required tail class words.", encoding="utf-8"
    )
    manifest = publication / "manifest.json"
    manifest.write_text('{"status":"validated"}', encoding="utf-8")
    patch = publication / "implementation.patch"
    patch.write_bytes(
        b"--- a/missing_target.py\n"
        b"+++ b/missing_target.py\n"
        b"@@ -1 +1 @@\n"
        b"-old\n"
        b"+new\n"
    )
    extra = publication / "scratch.py"
    extra.write_text("temporary candidate source\n", encoding="utf-8")
    envelope: dict[str, object] = {
        "artifacts": {
            "report": "report.md",
            "manifest": "manifest.json",
            "implementation_patch": "implementation.patch",
        },
        "identity": {
            "method_family_id": "family-a",
            "implementation_sha256": hashlib.sha256(patch.read_bytes()).hexdigest(),
            "core_config_sha256": "b" * 64,
        },
    }

    if mutation == "missing":
        report.unlink()
    elif mutation == "logical_id":
        artifacts = envelope["artifacts"]
        assert isinstance(artifacts, dict)
        artifacts["wrong"] = artifacts.pop("report")
    elif mutation == "unsafe_path":
        (tmp_path / "outside.md").write_text("tail class", encoding="utf-8")
        artifacts = envelope["artifacts"]
        assert isinstance(artifacts, dict)
        artifacts["report"] = "../outside.md"
    elif mutation == "symlink_extra":
        extra.unlink()
        outside = tmp_path / "outside.py"
        outside.write_text("outside\n", encoding="utf-8")
        extra.symlink_to(outside)
    elif mutation == "aliased_extra":
        extra.unlink()
        os.link(report, extra)
    elif mutation == "bad_family":
        identity = envelope["identity"]
        assert isinstance(identity, dict)
        identity["method_family_id"] = "family-b"
    elif mutation == "mismatched_digest":
        identity = envelope["identity"]
        assert isinstance(identity, dict)
        identity["implementation_sha256"] = "0" * 64
    elif mutation == "extra_digest":
        identity = envelope["identity"]
        assert isinstance(identity, dict)
        identity["implementation_sha256"] = hashlib.sha256(extra.read_bytes()).hexdigest()
    elif mutation == "standalone_digest":
        extra.unlink()
        identity = envelope["identity"]
        assert isinstance(identity, dict)
        identity["implementation_sha256"] = "0" * 64
    elif mutation == "standalone_apply":
        extra.unlink()
    elif mutation == "standalone_missing_digest":
        extra.unlink()
        identity = envelope["identity"]
        assert isinstance(identity, dict)
        del identity["implementation_sha256"]
    elif mutation == "standalone_invalid_digest":
        extra.unlink()
        identity = envelope["identity"]
        assert isinstance(identity, dict)
        identity["implementation_sha256"] = "not-a-digest"
    elif mutation == "invalid_digest":
        identity = envelope["identity"]
        assert isinstance(identity, dict)
        identity["implementation_sha256"] = "not-a-digest"
    elif mutation == "missing_digest":
        identity = envelope["identity"]
        assert isinstance(identity, dict)
        del identity["implementation_sha256"]
    elif mutation == "bad_validator":
        report.write_text("This report omits the required phrase.", encoding="utf-8")
    elif mutation == "bad_envelope":
        envelope["unsupported"] = True
    elif mutation not in {"none", "under_count"}:
        raise AssertionError(f"unknown mutation {mutation}")

    (publication / "result.json").write_text(
        json.dumps(envelope),
        encoding="utf-8",
    )
    outcome = {"fact_id": "outcome-fact"}
    candidate = {
        "outcome": "EXECUTION_FAILURE",
        "evaluator_version": evaluator_version,
        "metric_value": None,
        "criterion_met": None,
        "validation_error": validation_error,
        "execution_integrity_error": None,
    }
    issues: list[OracleIssue] = []

    _audit_component_result(
        tmp_path,
        component,
        {},
        {
            "evaluator_version": evaluator_version,
            "method_family_id": "family-a",
        },
        outcome,
        {"outcome": "EXECUTION_FAILURE", "metric_value": None},
        artifact,
        {"attempt": {"terminal_reason": "SUCCEEDED"}},
        None,
        [outcome],
        {"outcome-fact": {"outcome": candidate}},
        issues,
    )
    return {issue.code for issue in issues}


def test_v5_extra_inventory_first_failure_does_not_run_later_git_apply(
    tmp_path: Path,
) -> None:
    assert "operational_outcome_causation_mismatch" not in (
        _audit_extra_file_inventory_causation(
            tmp_path,
            mutation="extra_digest",
        )
    )


@pytest.mark.parametrize("mutation", ["none", "mismatched_digest", "bad_validator"])
def test_v5_extra_inventory_first_failure_has_independent_causation(
    tmp_path: Path,
    mutation: str,
) -> None:
    assert "operational_outcome_causation_mismatch" not in (
        _audit_extra_file_inventory_causation(
            tmp_path,
            mutation=mutation,
        )
    )


def test_v5_standalone_patch_digest_mismatch_has_independent_causation(
    tmp_path: Path,
) -> None:
    assert "operational_outcome_causation_mismatch" not in (
        _audit_extra_file_inventory_causation(
            tmp_path,
            mutation="standalone_digest",
            apply_check=False,
            validation_error=(
                "implementation_digest_mismatch@implementation_sha256: "
                "bytes do not match"
            ),
        )
    )


def test_v5_standalone_patch_digest_mismatch_requiring_apply_is_not_repairable(
    tmp_path: Path,
) -> None:
    assert "operational_outcome_causation_mismatch" in (
        _audit_extra_file_inventory_causation(
            tmp_path,
            mutation="standalone_digest",
            apply_check=True,
            validation_error=(
                "implementation_digest_mismatch@implementation_sha256: "
                "bytes do not match"
            ),
        )
    )


@pytest.mark.parametrize(
    ("mutation", "validation_error"),
    [
        (
            "none",
            "artifact_inventory_mismatch@artifact_dir: "
            "undeclared=['forged.py'], missing=[]",
        ),
        (
            "none",
            "artifact_inventory_mismatch@result.json.artifacts: "
            "undeclared=['scratch.py'], missing=[]",
        ),
        (
            "missing",
            "artifact_inventory_mismatch@artifact_dir: "
            "undeclared=['scratch.py'], missing=['report.md']",
        ),
        (
            "logical_id",
            "artifact_inventory_mismatch@result.json.artifacts: forged",
        ),
        ("unsafe_path", "artifact_inventory_mismatch@artifact_dir: forged"),
        ("symlink_extra", "artifact_inventory_mismatch@artifact_dir: forged"),
        ("aliased_extra", "artifact_inventory_mismatch@artifact_dir: forged"),
        ("under_count", "artifact_inventory_mismatch@artifact_dir: forged"),
        ("bad_envelope", "artifact_inventory_mismatch@artifact_dir: forged"),
        ("bad_family", "artifact_inventory_mismatch@artifact_dir: forged"),
        ("invalid_digest", "artifact_inventory_mismatch@artifact_dir: forged"),
        ("missing_digest", "artifact_inventory_mismatch@artifact_dir: forged"),
        (
            "standalone_digest",
            "implementation_digest_mismatch@implementation_digest_check: forged",
        ),
        (
            "standalone_missing_digest",
            "missing_implementation_digest@implementation_sha256: is required",
        ),
        (
            "standalone_invalid_digest",
            "invalid_identity@result.json.identity: forged",
        ),
    ],
)
def test_v5_extra_file_inventory_causation_rejects_unsafe_or_forged_cases(
    tmp_path: Path,
    mutation: str,
    validation_error: str,
) -> None:
    assert "operational_outcome_causation_mismatch" in (
        _audit_extra_file_inventory_causation(
            tmp_path,
            mutation=mutation,
            validation_error=validation_error,
        )
    )


def test_v5_standalone_apply_failure_is_not_repairable_causation(
    tmp_path: Path,
) -> None:
    assert "operational_outcome_causation_mismatch" in (
        _audit_extra_file_inventory_causation(
            tmp_path,
            mutation="standalone_apply",
            validation_error=(
                "apply_check_failed@implementation.patch: patch does not apply"
            ),
        )
    )


@pytest.mark.parametrize(
    ("mutation", "validation_error"),
    [
        (
            "none",
            "artifact_inventory_mismatch@artifact_dir: "
            "undeclared=['scratch.py'], missing=[]",
        ),
        (
            "extra_digest",
            "artifact_inventory_mismatch@artifact_dir: "
            "undeclared=['scratch.py'], missing=[]",
        ),
        (
            "standalone_digest",
            "implementation_digest_mismatch@implementation_sha256: bytes do not match",
        ),
    ],
)
def test_v4_does_not_accept_v5_candidate_metadata_causation(
    tmp_path: Path,
    mutation: str,
    validation_error: str,
) -> None:
    assert "operational_outcome_causation_mismatch" in (
        _audit_extra_file_inventory_causation(
            tmp_path,
            evaluator_version="component-result/4",
            mutation=mutation,
            validation_error=validation_error,
        )
    )


@pytest.mark.parametrize(
    "artifacts",
    [
        {"wrong": {"content": "inline"}},
        {"first": [], "second": "../outside.txt"},
        {"second": "../outside.txt", "first": []},
    ],
)
def test_nonrepairable_artifact_maps_cannot_spoof_execution_failure(
    tmp_path: Path,
    artifacts: dict[str, object],
) -> None:
    component = _ComponentContract(
        component_id="must.report",
        tier="must",
        owner_role="researcher",
        proposer_roles=("researcher",),
        dependencies=(),
        required_inputs=(),
        result_kind="artifact",
        metric_name=None,
        metric_direction=None,
        metric_target=None,
        seeds=(),
        arms=(),
        trial_ids=(),
        required_artifacts=("first", "second"),
        artifact_counts={"first": 1, "second": 1},
        required_metrics=(),
    )
    artifact = {
        "run_id": "run",
        "experiment_id": "experiment",
        "execution_id": "execution",
        "attempt_id": "attempt",
        "artifact_kind": "RESULT",
        "path": "run/experiment/execution/attempt/RESULT",
    }
    publication = tmp_path / artifact["path"]
    publication.mkdir(parents=True)
    (publication / "result.json").write_text(
        json.dumps({"artifacts": artifacts}), encoding="utf-8"
    )
    outcome = {"fact_id": "outcome-fact"}
    candidate = {
        "outcome": "EXECUTION_FAILURE",
        "evaluator_version": "component-result/2",
        "metric_value": None,
        "criterion_met": None,
        "validation_error": "invalid_artifact_map@result.json.artifacts: forged",
        "execution_integrity_error": None,
    }
    issues: list[OracleIssue] = []

    _audit_component_result(
        tmp_path,
        component,
        {},
        {"evaluator_version": "component-result/2"},
        outcome,
        {"outcome": "EXECUTION_FAILURE", "metric_value": None},
        artifact,
        {"attempt": {"terminal_reason": "SUCCEEDED"}},
        None,
        [outcome],
        {"outcome-fact": {"outcome": candidate}},
        issues,
    )

    assert "operational_outcome_causation_mismatch" in {
        issue.code for issue in issues
    }


@pytest.mark.parametrize(
    ("component", "trials", "checkpoint_paths", "expected"),
    [
        (
            _metric_component(component_id="primary.singleton", metric="score"),
            [{"seed": "only", "metrics": {"score": 3}, "clean": True}],
            None,
            "3",
        ),
        (
            _metric_component(
                component_id="must.paired",
                metric="paired_weighted_f1_gain_vs_baseline",
                seeds=(0, 1, 2),
                arms=("control", "treatment"),
                required_metrics=("paired_delta",),
            ),
            [
                {
                    "seed": seed,
                    "arm": arm,
                    "metrics": {
                        "weighted_f1": control if arm == "control" else treatment
                    },
                    "clean": True,
                }
                for seed, control, treatment in (
                    (0, "0.5", "0.6"),
                    (1, "0.4", "0.6"),
                    (2, "0.3", "0.6"),
                )
                for arm in ("control", "treatment")
            ],
            None,
            "0.2",
        ),
        (
            _metric_component(
                component_id="primary.final",
                metric="weighted_f1",
                seeds=(0, 1, 2, 3, 4),
                required_metrics=("weighted_f1", "n_successful_seeds"),
            ),
            [
                {
                    "seed": seed,
                    "metrics": {
                        "weighted_f1": seed + 1,
                        "n_successful_seeds": 5,
                    },
                    "clean": True,
                }
                for seed in range(5)
            ],
            None,
            "3",
        ),
        (
            _metric_component(
                component_id="must.causal",
                metric="max_future_perturbation_logit_diff",
                checkpoint_count=5,
            ),
            [
                {
                    "seed": check,
                    "metrics": {"max_future_perturbation_logit_diff": value},
                    "clean": True,
                }
                for check, value in (
                    (0, "0.001"),
                    (1, "0.009"),
                    (2, "0.004"),
                    (3, "0.003"),
                    (4, "0.002"),
                )
            ],
            tuple(f"artifacts/checkpoints-{index}.ckpt" for index in range(1, 6)),
            "0.009",
        ),
    ],
    ids=("singleton", "paired-n3", "mean-n5", "causal-max"),
)
def test_raw_trial_aggregates_are_independently_recomputed(
    component: _ComponentContract,
    trials: list[dict[str, object]],
    checkpoint_paths: tuple[str, ...] | None,
    expected: str,
) -> None:
    issues: list[OracleIssue] = []

    actual = _component_metric_from_trials(
        component,
        trials,
        "fact-outcome",
        issues,
        checkpoint_paths=checkpoint_paths,
    )

    assert actual == expected
    assert issues == []

def test_raw_trial_membership_must_match_the_exact_seed_and_arm_plan() -> None:
    paired = _metric_component(
        component_id="must.paired",
        metric="paired_score_gain_vs_baseline",
        seeds=(0, 1, 2),
        arms=("control", "treatment"),
        required_metrics=("paired_delta",),
    )
    trials = [
        {
            "seed": seed,
            "arm": arm,
            "metrics": {"score": 1},
            "clean": True,
        }
        for seed in range(3)
        for arm in ("control", "treatment")
    ]
    issues: list[OracleIssue] = []

    assert (
        _component_metric_from_trials(paired, trials[:-1], "fact-outcome", issues)
        is None
    )
    assert {issue.code for issue in issues} == {"component_trial_completeness_mismatch"}

    causal = _metric_component(
        component_id="must.causal",
        metric="max_future_perturbation_logit_diff",
        checkpoint_count=5,
    )
    checkpoint_paths = tuple(
        f"artifacts/checkpoints-{index}.ckpt" for index in range(1, 6)
    )
    issues = []
    assert (
        _component_metric_from_trials(
            causal,
            [
                {
                    "seed": "only-one-checkpoint",
                    "metrics": {"max_future_perturbation_logit_diff": "0.001"},
                    "clean": True,
                }
            ],
            "fact-causal",
            issues,
            checkpoint_paths=checkpoint_paths,
        )
        is None
    )
    assert {issue.code for issue in issues} == {"component_trial_completeness_mismatch"}

    issues = []
    assert (
        _component_metric_from_trials(
            causal,
            [
                {
                    "seed": seed,
                    "metrics": {"max_future_perturbation_logit_diff": "0.001"},
                    "clean": True,
                }
                for seed in range(5)
            ],
            "fact-causal",
            issues,
            checkpoint_paths=checkpoint_paths + ("artifacts/checkpoints-6.ckpt",),
        )
        is None
    )
    assert {issue.code for issue in issues} == {"component_trial_completeness_mismatch"}

    issues = []
    assert (
        _component_metric_from_trials(
            causal,
            [
                {
                    "seed": seed,
                    "metrics": {"max_future_perturbation_logit_diff": "0.001"},
                    "clean": True,
                }
                for seed in range(1, 6)
            ],
            "fact-causal",
            issues,
            checkpoint_paths=checkpoint_paths,
        )
        is None
    )
    assert {issue.code for issue in issues} == {"component_trial_completeness_mismatch"}


@pytest.mark.parametrize("successful_count", [None, 4])
def test_each_required_metric_is_checked_against_raw_trials(
    successful_count: int | None,
) -> None:
    component = _metric_component(
        component_id="primary.final",
        metric="score",
        seeds=(0, 1, 2, 3, 4),
        required_metrics=("score", "n_successful_seeds"),
    )
    trials = []
    for seed in range(5):
        metrics = {"score": seed + 1}
        if successful_count is not None:
            metrics["n_successful_seeds"] = successful_count
        trials.append({"seed": seed, "metrics": metrics, "clean": True})
    issues: list[OracleIssue] = []

    assert (
        _component_metric_from_trials(component, trials, "fact-final", issues) is None
    )
    assert {issue.code for issue in issues} == {"component_required_metric_mismatch"}


def test_nondecision_required_metric_must_be_present_on_every_trial() -> None:
    component = _metric_component(
        component_id="primary.final",
        metric="score",
        seeds=(0, 1, 2, 3, 4),
        required_metrics=("score", "calibration_error"),
    )
    trials = [
        {"seed": seed, "metrics": {"score": seed + 1}, "clean": True}
        for seed in range(5)
    ]
    issues: list[OracleIssue] = []

    assert (
        _component_metric_from_trials(component, trials, "fact-final", issues) is None
    )
    assert {issue.code for issue in issues} == {"component_required_metric_mismatch"}


def test_logical_artifacts_must_be_complete_regular_publication_files(
    tmp_path: Path,
) -> None:
    root = tmp_path / "publication"
    (root / "artifacts").mkdir(parents=True)
    (root / "result.json").write_text("{}", encoding="utf-8")
    (root / "artifacts" / "report.json").write_text("{}", encoding="utf-8")
    component = _ComponentContract(
        component_id="must.report",
        tier="must",
        owner_role="researcher",
        proposer_roles=("researcher",),
        dependencies=(),
        required_inputs=(),
        result_kind="artifact",
        metric_name=None,
        metric_direction=None,
        metric_target=None,
        seeds=(),
        arms=(),
        trial_ids=(),
        required_artifacts=("report",),
        artifact_counts={"report": 1},
        required_metrics=(),
    )
    issues: list[OracleIssue] = []

    logical, family, implementation, config = _component_artifact_map(
        root,
        {
            "artifacts": {"report": "artifacts/report.json"},
            "identity": {"method_family_id": "family-a"},
        },
        component,
        "fact-outcome",
        issues,
    )

    assert logical == {"report": ("artifacts/report.json",)}
    assert (family, implementation, config) == ("family-a", None, None)
    assert issues == []

    issues = []
    _component_artifact_map(
        root,
        {
            "artifacts": {"report": "../outside.json"},
            "identity": {"method_family_id": "family-a"},
        },
        component,
        "fact-outcome",
        issues,
    )
    assert "component_artifact_path_invalid" in {issue.code for issue in issues}

    alias_root = tmp_path / "alias-publication"
    alias_root.mkdir()
    (alias_root / "result.json").write_text("{}", encoding="utf-8")
    (alias_root / "first.json").write_text("{}", encoding="utf-8")
    os.link(alias_root / "first.json", alias_root / "second.json")
    alias_component = _ComponentContract(
        component_id="must.alias",
        tier="must",
        owner_role="researcher",
        proposer_roles=("researcher",),
        dependencies=(),
        required_inputs=(),
        result_kind="artifact",
        metric_name=None,
        metric_direction=None,
        metric_target=None,
        seeds=(),
        arms=(),
        trial_ids=(),
        required_artifacts=("first", "second"),
        artifact_counts={"first": 1, "second": 1},
        required_metrics=(),
    )
    issues = []
    _component_artifact_map(
        alias_root,
        {
            "artifacts": {"first": "first.json", "second": "second.json"},
            "identity": {"method_family_id": "family-a"},
        },
        alias_component,
        "fact-alias",
        issues,
    )
    assert "component_artifact_path_invalid" in {issue.code for issue in issues}

    artifact_root = tmp_path / "artifacts"
    outside = tmp_path / "outside"
    outside.mkdir()
    relative = "run-1/experiment-1/execution-1/attempt-1/result"
    publication = artifact_root / relative
    publication.parent.mkdir(parents=True)
    publication.symlink_to(outside, target_is_directory=True)
    row = {
        "run_id": "run-1",
        "experiment_id": "experiment-1",
        "execution_id": "execution-1",
        "attempt_id": "attempt-1",
        "artifact_kind": "result",
        "path": relative,
    }
    assert _artifact_materialized_root(artifact_root, row) is None


@pytest.mark.parametrize(
    ("invalid_relative", "invalid_payload"),
    [
        (
            "artifacts/manifest.json",
            b'{"family":"same","nested":{"family":"same"},'
            b'"datasets":{"MELD":{}},"status":"draft"}',
        ),
        (
            "artifacts/report.md",
            b"Component closure contains a forbidden shortcut.",
        ),
        (
            "artifacts/change.patch",
            b"--- ../../outside.py\n+++ b/model.py\n@@ -1 +1 @@\n-old\n+new\n",
        ),
    ],
    ids=("json", "text", "unified-diff"),
)
def test_contract_artifact_validators_recheck_publication_bytes(
    tmp_path: Path,
    invalid_relative: str,
    invalid_payload: bytes,
) -> None:
    root = tmp_path / "publication"
    (root / "artifacts").mkdir(parents=True)
    (root / "result.json").write_text("{}", encoding="utf-8")
    valid_files = {
        "artifacts/manifest.json": (
            b'{"family":"same","nested":{"family":"same"},'
            b'"datasets":{"MELD":{}},"status":"validated"}'
        ),
        "artifacts/report.md": (
            b"# Component Closure\nMELD uses the shared mechanism and adapter.\n"
        ),
        "artifacts/change.patch": (
            b"--- a/model.py\n+++ b/model.py\n@@ -1 +1 @@\n-old\n+new\n"
        ),
    }
    for relative, payload in valid_files.items():
        (root / relative).write_bytes(payload)
    raw_validators = [
        {
            "type": "json_contract",
            "artifact": "manifest",
            "required_paths": ["family", "nested.family"],
            "equal_groups": [["family", "nested.family"]],
            "exact_key_sets": {"datasets": ["MELD"]},
            "allowed_values": {"status": ["validated"]},
        },
        {
            "type": "text_contract",
            "artifact": "report",
            "min_chars": 32,
            "required_sections": ["Component Closure"],
            "required_terms": ["MELD", "shared mechanism", "adapter"],
            "forbidden_terms": ["forbidden shortcut"],
        },
        {
            "type": "unified_diff",
            "artifact": "patch",
            "apply_check": True,
        },
    ]
    validators = _artifact_validator_contracts(
        raw_validators, ("manifest", "report", "patch")
    )
    assert validators is not None
    component = _ComponentContract(
        component_id="must.publication",
        tier="must",
        owner_role="researcher",
        proposer_roles=("researcher",),
        dependencies=(),
        required_inputs=(),
        result_kind="artifact",
        metric_name=None,
        metric_direction=None,
        metric_target=None,
        seeds=(),
        arms=(),
        trial_ids=(),
        required_artifacts=("manifest", "report", "patch"),
        artifact_counts={"manifest": 1, "report": 1, "patch": 1},
        required_metrics=(),
        artifact_validators=validators,
    )
    envelope = {
        "artifacts": {
            "manifest": "artifacts/manifest.json",
            "report": "artifacts/report.md",
            "patch": "artifacts/change.patch",
        },
        "identity": {"method_family_id": "family-a"},
    }
    issues: list[OracleIssue] = []
    logical, *_ = _component_artifact_map(
        root, envelope, component, "fact-publication", issues
    )
    assert logical is not None
    assert _audit_component_artifact_validators(
        root, logical, component, "fact-publication", issues
    )
    assert issues == []

    (root / invalid_relative).write_bytes(invalid_payload)
    issues = []
    assert not _audit_component_artifact_validators(
        root, logical, component, "fact-publication", issues
    )
    assert {issue.code for issue in issues} == {"component_artifact_validator_mismatch"}


def _contract_for_verdicts() -> _GoalComponentContract:
    def component(component_id: str, tier: str) -> _ComponentContract:
        return _ComponentContract(
            component_id=component_id,
            tier=tier,
            owner_role="researcher",
            proposer_roles=("researcher",),
            dependencies=(),
            required_inputs=(),
            result_kind="metric",
            metric_name="score",
            metric_direction="MAXIMIZE",
            metric_target="1",
            seeds=(),
            arms=(),
            trial_ids=(),
            required_artifacts=(),
            artifact_counts={},
            required_metrics=("score",),
        )

    return _GoalComponentContract(
        fact_id="fact-goal",
        goal_id="goal-1",
        roles=("researcher",),
        components=(
            component("primary.a", "primary"),
            component("primary.b", "primary"),
            component("must.audit", "must"),
            component("should.ablation", "should"),
        ),
    )


def test_required_conjunction_uses_every_required_assessment_and_one_family() -> None:
    contract = _contract_for_verdicts()
    assessment_rows = {
        component_id: {
            "fact_id": f"fact-assessment-{component_id}",
            "subject_id": f"assessment-{component_id}",
        }
        for component_id in (
            "primary.a",
            "primary.b",
            "must.audit",
            "should.ablation",
        )
    }
    by_id = {str(row["subject_id"]): row for row in assessment_rows.values()}
    payloads = {
        str(row["fact_id"]): {
            "component_id": component_id,
            "method_family_id": "family-a",
            "assessment": "SUPPORTED",
        }
        for component_id, row in assessment_rows.items()
    }
    results = {
        str(row["subject_id"]): _ComponentResult("SUCCESS", True, "1", "a" * 64, None)
        for row in assessment_rows.values()
    }
    verdict = {"fact_type": "GoalVerdictRecorded", "fact_id": "fact-verdict"}
    facts = [verdict]
    transitions = []
    transition_payloads = {}
    for component_id in ("primary.a", "primary.b", "must.audit"):
        hypothesis_id = f"hypothesis-{component_id}"
        fact_id = f"fact-hypothesis-{component_id}"
        workflow_id = f"experiment-{component_id}"
        facts.append({
            "fact_type": "HypothesisProposed",
            "fact_id": fact_id,
            "hypothesis_id": hypothesis_id,
        })
        payloads[fact_id] = {
            "component_id": component_id,
            "method_family_id": "family-a",
        }
        transitions.append({
            "workflow_kind": "EXPERIMENT",
            "revision": 1,
            "workflow_id": workflow_id,
        })
        transition_payloads[f"{workflow_id}@1"] = {
            "component_id": component_id,
            "experiment_ordinal": 1,
            "hypothesis_id": hypothesis_id,
        }
    payloads["fact-verdict"] = {
        "assessment_ids": [
            "assessment-primary.a",
            "assessment-primary.b",
            "assessment-must.audit",
        ],
        "method_family_id": "family-a",
        "verdict": "GOAL_MET",
    }
    issues: list[OracleIssue] = []

    _audit_component_verdicts(
        contract,
        facts,  # type: ignore[arg-type]
        payloads,
        by_id,  # type: ignore[arg-type]
        assessment_rows,  # type: ignore[arg-type]
        results,
        issues,
        transitions=transitions,  # type: ignore[arg-type]
        transition_payloads=transition_payloads,
    )

    assert issues == []

    payloads["fact-assessment-primary.b"]["method_family_id"] = "family-b"
    issues = []
    _audit_component_verdicts(
        contract,
        [verdict],  # type: ignore[list-item]
        payloads,
        by_id,  # type: ignore[arg-type]
        assessment_rows,  # type: ignore[arg-type]
        results,
        issues,
    )
    assert {issue.code for issue in issues} == {
        "verdict_component_conjunction_mismatch"
    }

    payloads["fact-assessment-primary.b"]["method_family_id"] = "family-a"
    results["assessment-primary.b"] = _ComponentResult("SUCCESS", True, "1", None, None)
    issues = []
    _audit_component_verdicts(
        contract,
        [verdict],  # type: ignore[list-item]
        payloads,
        by_id,  # type: ignore[arg-type]
        assessment_rows,  # type: ignore[arg-type]
        results,
        issues,
    )
    assert {issue.code for issue in issues} == {
        "verdict_component_conjunction_mismatch"
    }


def _partial_assessment_verdict_issues(
    *, selected_families: tuple[str, str], verdict_family: str | None
) -> list[OracleIssue]:
    contract = _contract_for_verdicts()
    assessment = {
        "fact_id": "fact-assessment-primary.a",
        "subject_id": "assessment-primary.a",
    }
    facts = [
        {
            "fact_type": "HypothesisProposed",
            "fact_id": "fact-hypothesis-primary.a",
            "hypothesis_id": "hypothesis-primary.a",
        },
        {
            "fact_type": "HypothesisProposed",
            "fact_id": "fact-hypothesis-primary.b",
            "hypothesis_id": "hypothesis-primary.b",
        },
        {"fact_type": "GoalVerdictRecorded", "fact_id": "fact-verdict"},
    ]
    payloads = {
        "fact-hypothesis-primary.a": {
            "component_id": "primary.a",
            "method_family_id": selected_families[0],
        },
        "fact-hypothesis-primary.b": {
            "component_id": "primary.b",
            "method_family_id": selected_families[1],
        },
        "fact-assessment-primary.a": {
            "component_id": "primary.a",
            "method_family_id": selected_families[0],
            "assessment": "SUPPORTED",
        },
        "fact-verdict": {
            "assessment_ids": ["assessment-primary.a"],
            "method_family_id": verdict_family,
            "verdict": "INCONCLUSIVE",
        },
    }
    transitions = [
        {
            "workflow_kind": "EXPERIMENT",
            "revision": 1,
            "workflow_id": "experiment-primary.a",
        },
        {
            "workflow_kind": "EXPERIMENT",
            "revision": 1,
            "workflow_id": "experiment-primary.b",
        },
    ]
    transition_payloads = {
        "experiment-primary.a@1": {
            "component_id": "primary.a",
            "experiment_ordinal": 1,
            "hypothesis_id": "hypothesis-primary.a",
        },
        "experiment-primary.b@1": {
            "component_id": "primary.b",
            "experiment_ordinal": 1,
            "hypothesis_id": "hypothesis-primary.b",
        },
    }
    issues: list[OracleIssue] = []

    _audit_component_verdicts(
        contract,
        facts,  # type: ignore[arg-type]
        payloads,
        {"assessment-primary.a": assessment},  # type: ignore[arg-type]
        {"primary.a": assessment},  # type: ignore[arg-type]
        {
            "assessment-primary.a": _ComponentResult(
                "SUCCESS", True, "1", "a" * 64, None
            )
        },
        issues,
        transitions=transitions,  # type: ignore[arg-type]
        transition_payloads=transition_payloads,
    )
    return issues


def test_partial_assessment_with_mixed_selected_families_accepts_null_family() -> None:
    issues = _partial_assessment_verdict_issues(
        selected_families=("family-a", "family-b"),
        verdict_family=None,
    )

    assert issues == []


def test_partial_assessment_with_one_selected_family_requires_that_family() -> None:
    issues = _partial_assessment_verdict_issues(
        selected_families=("family-a", "family-a"),
        verdict_family="family-a",
    )
    assert issues == []

    missing_family = _partial_assessment_verdict_issues(
        selected_families=("family-a", "family-a"),
        verdict_family=None,
    )
    assert {issue.code for issue in missing_family} == {
        "verdict_component_conjunction_mismatch"
    }


def test_verdict_family_conjunction_uses_required_input_dataflow() -> None:
    def component(
        component_id: str,
        tier: str,
        *,
        dependencies: tuple[str, ...] = (),
        input_producer: str | None = None,
    ) -> _ComponentContract:
        required_inputs = (
            ()
            if input_producer is None
            else (
                _ComponentInput(
                    resource=f"{component_id}-input",
                    producer_component=input_producer,
                    logical_artifact="candidate_manifest",
                    min_count=1,
                ),
            )
        )
        return _ComponentContract(
            component_id=component_id,
            tier=tier,
            owner_role="researcher",
            proposer_roles=("researcher",),
            dependencies=dependencies,
            required_inputs=required_inputs,
            result_kind="metric",
            metric_name="score",
            metric_direction="MAXIMIZE",
            metric_target="1",
            seeds=(),
            arms=(),
            trial_ids=(),
            required_artifacts=("candidate_manifest",),
            artifact_counts={"candidate_manifest": 1},
            required_metrics=("score",),
        )

    contract = _GoalComponentContract(
        fact_id="fact-goal",
        goal_id="goal-1",
        roles=("researcher",),
        components=(
            component("must.search-a", "must"),
            component("must.search-b", "must"),
            component(
                "must.implementation",
                "must",
                dependencies=("must.search-a", "must.search-b"),
            ),
            component(
                "primary.final",
                "primary",
                dependencies=("must.implementation",),
                input_producer="must.implementation",
            ),
        ),
    )
    families = {
        "must.search-a": "search-family-a",
        "must.search-b": "search-family-b",
        "must.implementation": "selected-family",
        "primary.final": "selected-family",
    }
    assessments = {
        component_id: {
            "fact_id": f"fact-assessment-{component_id}",
            "subject_id": f"assessment-{component_id}",
        }
        for component_id in families
    }
    facts: list[dict[str, object]] = [
        {"fact_type": "GoalVerdictRecorded", "fact_id": "fact-verdict"}
    ]
    payloads: dict[str, dict[str, object]] = {
        "fact-verdict": {
            "assessment_ids": [
                f"assessment-{component_id}" for component_id in families
            ],
            "method_family_id": "selected-family",
            "verdict": "GOAL_MET",
        }
    }
    transitions: list[dict[str, object]] = []
    transition_payloads: dict[str, dict[str, object]] = {}
    for component_id, family in families.items():
        hypothesis_id = f"hypothesis-{component_id}"
        hypothesis_fact_id = f"fact-hypothesis-{component_id}"
        workflow_id = f"experiment-{component_id}"
        facts.append(
            {
                "fact_type": "HypothesisProposed",
                "fact_id": hypothesis_fact_id,
                "hypothesis_id": hypothesis_id,
            }
        )
        payloads[hypothesis_fact_id] = {
            "component_id": component_id,
            "method_family_id": family,
        }
        payloads[f"fact-assessment-{component_id}"] = {
            "component_id": component_id,
            "method_family_id": family,
            "assessment": "SUPPORTED",
        }
        transitions.append(
            {
                "workflow_kind": "EXPERIMENT",
                "revision": 1,
                "workflow_id": workflow_id,
            }
        )
        transition_payloads[f"{workflow_id}@1"] = {
            "component_id": component_id,
            "experiment_ordinal": 1,
            "hypothesis_id": hypothesis_id,
        }
    issues: list[OracleIssue] = []

    _audit_component_verdicts(
        contract,
        facts,  # type: ignore[arg-type]
        payloads,
        {str(row["subject_id"]): row for row in assessments.values()},  # type: ignore[arg-type]
        assessments,  # type: ignore[arg-type]
        {
            str(row["subject_id"]): _ComponentResult(
                "SUCCESS", True, "1", None, None
            )
            for row in assessments.values()
        },
        issues,
        transitions=transitions,  # type: ignore[arg-type]
        transition_payloads=transition_payloads,
    )

    assert issues == []

    cross_family_inconclusive_payloads = {
        **payloads,
        "fact-verdict": {
            **payloads["fact-verdict"],
            "method_family_id": None,
            "verdict": "INCONCLUSIVE",
        },
    }
    cross_family_inconclusive_issues: list[OracleIssue] = []
    _audit_component_verdicts(
        contract,
        facts,  # type: ignore[arg-type]
        cross_family_inconclusive_payloads,
        {str(row["subject_id"]): row for row in assessments.values()},  # type: ignore[arg-type]
        assessments,  # type: ignore[arg-type]
        {
            str(row["subject_id"]): _ComponentResult(
                "SUCCESS", True, "1", None, None
            )
            for row in assessments.values()
        },
        cross_family_inconclusive_issues,
        transitions=transitions,  # type: ignore[arg-type]
        transition_payloads=transition_payloads,
        expert_lineages_terminated=False,
    )
    assert cross_family_inconclusive_issues == []

    retained_v6_issues: list[OracleIssue] = []
    _audit_component_verdicts(
        contract,
        facts,  # type: ignore[arg-type]
        payloads,
        {str(row["subject_id"]): row for row in assessments.values()},  # type: ignore[arg-type]
        assessments,  # type: ignore[arg-type]
        {
            str(row["subject_id"]): _ComponentResult(
                "SUCCESS", True, "1", None, None
            )
            for row in assessments.values()
        },
        retained_v6_issues,
        transitions=transitions,  # type: ignore[arg-type]
        transition_payloads=transition_payloads,
        selected_family_dataflow=False,
    )
    assert {issue.code for issue in retained_v6_issues} == {
        "verdict_component_conjunction_mismatch"
    }


def test_analysis_rendezvous_accepts_a_failed_canonical_process_source() -> None:
    run_id = "run-analysis"
    component_id = "primary.score"
    experiment_id = "primary.score--experiment"
    execution_id = "primary.score--execution"
    attempt_id = "primary.score--process-1"
    signal = {
        "kind": "HELP",
        "question": "What does this operational failure imply?",
        "context_refs": [],
    }
    origin = {
        "rendezvous_id": f"{attempt_id}--rendezvous",
        "requester_role": "researcher",
        "requester_slot": 1,
        "source_attempt_id": attempt_id,
        "target_role": "analyst",
        "expert_signal": signal,
        "component_id": component_id,
        "phase": "analyze",
    }
    goal = {
        "fact_id": "fact-goal",
        "fact_type": "GoalDefined",
        "run_id": run_id,
    }
    outcome = {
        "fact_id": "fact-outcome",
        "fact_type": "CanonicalOutcomeAccepted",
        "run_id": run_id,
        "experiment_id": experiment_id,
        "execution_id": execution_id,
    }
    payloads = {
        "fact-goal": {
            "research_contract": {
                "roles": [{"id": "researcher"}, {"id": "analyst"}],
                "components": [
                    {
                        "id": component_id,
                        "owner": "researcher",
                        "proposers": ["researcher"],
                    }
                ],
                "methodology": {
                    "controls": {
                        "experiment_campaigns": {
                            "enabled": True,
                            "required_roles": ["analyze"],
                            "role_owners": {"*": {"analyze": "analyst"}},
                        }
                    }
                },
            }
        },
        "fact-outcome": {
            "component_id": component_id,
            "outcome": "EXECUTION_FAILURE",
        },
    }
    attempts = [
        {
            "attempt_id": attempt_id,
            "run_id": run_id,
            "workflow_id": experiment_id,
            "kind": "PROCESS",
            "state": "CLOSED",
            "terminal_reason": "FAILED",
            "experiment_id": experiment_id,
            "execution_id": execution_id,
        }
    ]
    requests = [
        {
            "identifier": "transition-analysis-request",
            "run_id": run_id,
            "workflow_id": experiment_id,
            "workflow_kind": "EXPERIMENT",
            "state": "EVALUATING",
            "payload": {
                "rendezvous_origin": origin,
                "rendezvous_id": origin["rendezvous_id"],
                "component_id": component_id,
                "role": "researcher",
            },
            "wire": {
                "rendezvous_id": origin["rendezvous_id"],
                "run_id": run_id,
                "requester_role": "researcher",
                "requester_slot": 1,
                "target_role": "analyst",
                "source_attempt_id": attempt_id,
                **signal,
            },
        }
    ]
    issues: list[OracleIssue] = []

    linked = _audit_component_rendezvous_origins(
        [goal, outcome],  # type: ignore[list-item]
        payloads,
        attempts,  # type: ignore[arg-type]
        {},
        {},
        requests,
        {run_id: ("researcher", "analyst")},
        issues,
    )

    assert linked == {"transition-analysis-request"}
    assert issues == []


def test_primary_lineage_ignores_empty_comparable_result_set() -> None:
    implementation = _ComponentContract(
        component_id="must.implementation",
        tier="must",
        owner_role="researcher",
        proposer_roles=("researcher",),
        dependencies=(),
        required_inputs=(),
        result_kind="artifact",
        metric_name=None,
        metric_direction=None,
        metric_target=None,
        seeds=(),
        arms=(),
        trial_ids=(),
        required_artifacts=("implementation_patch",),
        artifact_counts={"implementation_patch": 1},
        required_metrics=(),
    )
    contract = _GoalComponentContract(
        fact_id="fact-goal",
        goal_id="goal-1",
        roles=("researcher",),
        components=(
            implementation,
            _metric_component(component_id="primary.final", metric="score"),
        ),
    )
    result = _ComponentResult(
        "EXECUTION_FAILURE", False, None, None, None
    )
    outcome = {"fact_id": "fact-outcome-primary"}
    issues: list[OracleIssue] = []

    _audit_primary_implementation_lineage(
        contract,
        {"primary.final": result},
        {"primary.final": outcome},  # type: ignore[arg-type]
        {"fact-outcome-primary": {"outcome": "EXECUTION_FAILURE"}},
        issues,
    )

    assert issues == []


def test_primary_lineage_requires_implementation_but_allows_omitted_core_config() -> None:
    component = replace(
        _metric_component(component_id="primary.final", metric="score"),
        required_artifacts=("implementation_patch",),
        artifact_counts={"implementation_patch": 1},
    )
    contract = _GoalComponentContract(
        fact_id="fact-goal",
        goal_id="goal-1",
        roles=("researcher",),
        components=(component,),
    )
    outcome = {"fact_id": "fact-outcome-primary"}
    payloads = {"fact-outcome-primary": {"outcome": "SUCCESS"}}

    issues: list[OracleIssue] = []
    _audit_primary_implementation_lineage(
        contract,
        {"primary.final": _ComponentResult(
            "SUCCESS", True, "1", "a" * 64, None)},
        {"primary.final": outcome},  # type: ignore[arg-type]
        payloads,
        issues,
    )
    assert issues == []

    _audit_primary_implementation_lineage(
        contract,
        {"primary.final": _ComponentResult(
            "SUCCESS", True, "1", None, None)},
        {"primary.final": outcome},  # type: ignore[arg-type]
        payloads,
        issues,
    )
    assert [issue.code for issue in issues] == [
        "primary_implementation_lineage_mismatch"
    ]


def _successor_fact_rows() -> tuple[
    list[dict[str, object]], dict[str, dict[str, object]]
]:
    run_id = "run-successor"
    base = f"{run_id}--component-1"
    hypothesis_id = f"{base}--hypothesis-1"
    rows: list[dict[str, object]] = []
    payloads: dict[str, dict[str, object]] = {}

    def add(
        fact_id: str,
        fact_type: str,
        subject_id: str,
        raw_order: int,
        **indexed: object,
    ) -> None:
        rows.append(
            {
                "fact_id": fact_id,
                "fact_type": fact_type,
                "subject_id": subject_id,
                "run_id": run_id,
                "schema_version": 5,
                "raw_order": raw_order,
                **indexed,
            }
        )

    add(
        "fact-experiment-1",
        "ExperimentPreregistered",
        f"{base}--experiment",
        1,
        hypothesis_id=hypothesis_id,
        experiment_id=f"{base}--experiment",
        execution_id=f"{base}--execution",
    )
    payloads["fact-experiment-1"] = {
        "schema_version": 5,
        "component_id": "primary.score",
        "method_family_id": "family-a",
        "revision_of_outcome_id": None,
    }
    add(
        "fact-outcome-1",
        "CanonicalOutcomeAccepted",
        f"{base}--outcome",
        2,
        experiment_id=f"{base}--experiment",
        execution_id=f"{base}--execution",
        outcome_id=f"{base}--outcome",
        artifact_id=f"{base}--artifact",
    )
    payloads["fact-outcome-1"] = {
        "schema_version": 5,
        "component_id": "primary.score",
        "method_family_id": "family-a",
        "outcome": "EXECUTION_FAILURE",
    }
    add(
        "fact-evidence-1",
        "EvidenceLinked",
        f"{base}--evidence",
        3,
        hypothesis_id=hypothesis_id,
        outcome_id=f"{base}--outcome",
        artifact_id=f"{base}--artifact",
    )
    payloads["fact-evidence-1"] = {
        "schema_version": 5,
        "component_id": "primary.score",
        "method_family_id": "family-a",
    }
    add(
        "fact-experiment-2",
        "ExperimentPreregistered",
        f"{base}--experiment-2",
        4,
        hypothesis_id=hypothesis_id,
        experiment_id=f"{base}--experiment-2",
        execution_id=f"{base}--execution-2",
    )
    payloads["fact-experiment-2"] = {
        "schema_version": 5,
        "component_id": "primary.score",
        "method_family_id": "family-a",
        "revision_of_outcome_id": f"{base}--outcome",
    }
    add(
        "fact-outcome-2",
        "CanonicalOutcomeAccepted",
        f"{base}--outcome-2",
        5,
        experiment_id=f"{base}--experiment-2",
        execution_id=f"{base}--execution-2",
        outcome_id=f"{base}--outcome-2",
        artifact_id=f"{base}--artifact-2",
    )
    payloads["fact-outcome-2"] = {
        "schema_version": 5,
        "component_id": "primary.score",
        "method_family_id": "family-a",
        "outcome": "SUCCESS",
    }
    add(
        "fact-evidence-2",
        "EvidenceLinked",
        f"{base}--evidence-2",
        6,
        hypothesis_id=hypothesis_id,
        outcome_id=f"{base}--outcome-2",
        artifact_id=f"{base}--artifact-2",
    )
    payloads["fact-evidence-2"] = {
        "schema_version": 5,
        "component_id": "primary.score",
        "method_family_id": "family-a",
    }
    add(
        "fact-assessment",
        "HypothesisAssessed",
        f"{base}--assessment",
        7,
        hypothesis_id=hypothesis_id,
    )
    payloads["fact-assessment"] = {
        "schema_version": 5,
        "component_id": "primary.score",
        "method_family_id": "family-a",
        "evidence_ids": [f"{base}--evidence", f"{base}--evidence-2"],
    }
    return rows, payloads


@pytest.mark.parametrize(
    "research_contract_version",
    ("hypoloom-research-v5", "hypoloom-research-v6", "hypoloom-research-v7"),
)
def test_v5_v6_successor_facts_project_one_linear_tip(
    research_contract_version: str,
) -> None:
    facts, payloads = _successor_fact_rows()
    issues: list[OracleIssue] = []

    specs, outcomes, evidence = _audit_component_successor_chains(
        research_contract_version,
        facts,  # type: ignore[arg-type]
        payloads,
        issues,
        completed=True,
    )

    assert issues == []
    assert specs["primary.score"]["subject_id"].endswith("--experiment-2")
    assert outcomes["primary.score"]["subject_id"].endswith("--outcome-2")
    assert evidence["primary.score"]["subject_id"].endswith("--evidence-2")


def test_v4_keeps_duplicate_component_experiment_and_outcome_errors() -> None:
    facts, payloads = _successor_fact_rows()
    for fact in facts:
        fact["schema_version"] = 4
    for payload in payloads.values():
        payload["schema_version"] = 4
    payloads["fact-experiment-2"].pop("revision_of_outcome_id")
    issues: list[OracleIssue] = []

    _audit_component_successor_chains(
        "hypoloom-research-v4", facts, payloads, issues, completed=True  # type: ignore[arg-type]
    )

    assert {
        "duplicate_component_experiment",
        "duplicate_component_outcome",
    } <= {issue.code for issue in issues}


def test_v5_successor_payload_requires_the_existing_revision_outcome_ref() -> None:
    all_facts, all_payloads = _successor_fact_rows()
    base = "run-successor--component-1"
    hypothesis = {
        "fact_id": "fact-hypothesis",
        "fact_type": "HypothesisProposed",
        "subject_id": f"{base}--hypothesis-1",
        "hypothesis_id": f"{base}--hypothesis-1",
        "schema_version": 5,
    }
    selected_ids = {
        "fact-experiment-1",
        "fact-outcome-1",
        "fact-experiment-2",
    }
    facts = [hypothesis] + [
        row for row in all_facts if row["fact_id"] in selected_ids
    ]
    payloads: dict[str, Mapping[str, object]] = {
        "fact-hypothesis": {"schema_version": 5},
        **{
            fact_id: payload
            for fact_id, payload in all_payloads.items()
            if fact_id in selected_ids
        },
    }
    refs = [
        {
            "fact_id": "fact-hypothesis",
            "role": "goal",
            "ordinal": 0,
            "ref_fact_id": "fact-goal",
        },
        {
            "fact_id": "fact-experiment-1",
            "role": "hypothesis",
            "ordinal": 0,
            "ref_fact_id": "fact-hypothesis",
        },
        {
            "fact_id": "fact-outcome-1",
            "role": "experiment",
            "ordinal": 0,
            "ref_fact_id": "fact-experiment-1",
        },
        {
            "fact_id": "fact-experiment-2",
            "role": "hypothesis",
            "ordinal": 0,
            "ref_fact_id": "fact-hypothesis",
        },
        {
            "fact_id": "fact-experiment-2",
            "role": "revision_of_outcome",
            "ordinal": 0,
            "ref_fact_id": "fact-outcome-1",
        },
    ]
    issues: list[OracleIssue] = []

    _audit_component_fact_references(
        _contract_for_verdicts(),
        facts,  # type: ignore[arg-type]
        payloads,
        refs,  # type: ignore[arg-type]
        issues,
    )

    assert issues == []

    issues = []
    _audit_component_fact_references(
        _contract_for_verdicts(),
        facts,  # type: ignore[arg-type]
        payloads,
        refs[:-1],  # type: ignore[arg-type]
        issues,
    )
    assert {issue.code for issue in issues} == {"component_fact_ref_mismatch"}


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("branch", "component_successor_branch"),
        ("cycle", "component_successor_cycle"),
        ("cross-lineage", "component_successor_lineage_mismatch"),
        ("old-tip-assessment", "component_successor_tip_assessment_mismatch"),
        ("missing-evidence", "component_outcome_evidence_closure_mismatch"),
        ("wrong-ordinal", "component_successor_ordinal_mismatch"),
    ],
)
def test_v5_successor_chain_rejects_non_linear_or_non_tip_closure(
    mutation: str, expected_code: str
) -> None:
    facts, payloads = _successor_fact_rows()
    base = "run-successor--component-1"
    if mutation == "branch":
        facts.append(
            {
                **next(
                    row for row in facts if row["fact_id"] == "fact-experiment-2"
                ),
                "fact_id": "fact-experiment-3",
                "subject_id": f"{base}--experiment-3",
                "experiment_id": f"{base}--experiment-3",
                "execution_id": f"{base}--execution-3",
                "raw_order": 8,
            }
        )
        payloads["fact-experiment-3"] = {
            **payloads["fact-experiment-2"],
            "revision_of_outcome_id": f"{base}--outcome",
        }
    elif mutation == "cycle":
        payloads["fact-experiment-1"]["revision_of_outcome_id"] = (
            f"{base}--outcome-2"
        )
        payloads["fact-outcome-2"]["outcome"] = "EXECUTION_FAILURE"
    elif mutation == "cross-lineage":
        payloads["fact-experiment-2"]["component_id"] = "primary.other"
    elif mutation == "old-tip-assessment":
        payloads["fact-assessment"]["evidence_ids"] = [f"{base}--evidence"]
    elif mutation == "missing-evidence":
        facts[:] = [row for row in facts if row["fact_id"] != "fact-evidence-1"]
        payloads.pop("fact-evidence-1")
    elif mutation == "wrong-ordinal":
        spec = next(row for row in facts if row["fact_id"] == "fact-experiment-2")
        outcome = next(row for row in facts if row["fact_id"] == "fact-outcome-2")
        spec["experiment_id"] = spec["subject_id"] = f"{base}--experiment-3"
        outcome["experiment_id"] = f"{base}--experiment-3"
    issues: list[OracleIssue] = []

    _audit_component_successor_chains(
        "hypoloom-research-v5", facts, payloads, issues, completed=True  # type: ignore[arg-type]
    )

    assert expected_code in {issue.code for issue in issues}


def test_raw_fact_refs_close_the_seven_fact_component_lineage() -> None:
    contract = _contract_for_verdicts()
    facts: list[dict[str, object]] = [
        {
            "fact_id": "fact-goal",
            "fact_type": "GoalDefined",
            "subject_id": "goal-1",
        },
        {
            "fact_id": "fact-hypothesis",
            "fact_type": "HypothesisProposed",
            "hypothesis_id": "hypothesis-1",
            "subject_id": "hypothesis-1",
        },
        {
            "fact_id": "fact-experiment",
            "fact_type": "ExperimentPreregistered",
            "hypothesis_id": "hypothesis-1",
            "experiment_id": "experiment-1",
            "subject_id": "experiment-1",
        },
        {
            "fact_id": "fact-outcome",
            "fact_type": "CanonicalOutcomeAccepted",
            "experiment_id": "experiment-1",
            "subject_id": "outcome-1",
        },
        {
            "fact_id": "fact-evidence",
            "fact_type": "EvidenceLinked",
            "hypothesis_id": "hypothesis-1",
            "outcome_id": "outcome-1",
            "subject_id": "evidence-1",
        },
        {
            "fact_id": "fact-assessment",
            "fact_type": "HypothesisAssessed",
            "hypothesis_id": "hypothesis-1",
            "subject_id": "assessment-1",
        },
        {
            "fact_id": "fact-verdict",
            "fact_type": "GoalVerdictRecorded",
            "subject_id": "verdict-1",
        },
    ]
    payloads: dict[str, Mapping[str, object]] = {
        "fact-hypothesis": {},
        "fact-experiment": {"upstream_outcomes": []},
        "fact-assessment": {"evidence_ids": ["evidence-1"]},
        "fact-verdict": {"assessment_ids": ["assessment-1"]},
    }
    refs = [
        {
            "fact_id": "fact-hypothesis",
            "role": "goal",
            "ordinal": 0,
            "ref_fact_id": "fact-goal",
        },
        {
            "fact_id": "fact-experiment",
            "role": "hypothesis",
            "ordinal": 0,
            "ref_fact_id": "fact-hypothesis",
        },
        {
            "fact_id": "fact-outcome",
            "role": "experiment",
            "ordinal": 0,
            "ref_fact_id": "fact-experiment",
        },
        {
            "fact_id": "fact-evidence",
            "role": "hypothesis",
            "ordinal": 0,
            "ref_fact_id": "fact-hypothesis",
        },
        {
            "fact_id": "fact-evidence",
            "role": "outcome",
            "ordinal": 0,
            "ref_fact_id": "fact-outcome",
        },
        {
            "fact_id": "fact-assessment",
            "role": "hypothesis",
            "ordinal": 0,
            "ref_fact_id": "fact-hypothesis",
        },
        {
            "fact_id": "fact-assessment",
            "role": "evidence",
            "ordinal": 0,
            "ref_fact_id": "fact-evidence",
        },
        {
            "fact_id": "fact-verdict",
            "role": "goal",
            "ordinal": 0,
            "ref_fact_id": "fact-goal",
        },
        {
            "fact_id": "fact-verdict",
            "role": "assessment",
            "ordinal": 0,
            "ref_fact_id": "fact-assessment",
        },
    ]
    issues: list[OracleIssue] = []

    _audit_component_fact_references(
        contract,
        facts,  # type: ignore[arg-type]
        payloads,
        refs,  # type: ignore[arg-type]
        issues,
    )
    assert issues == []

    issues = []
    _audit_component_fact_references(
        contract,
        facts,  # type: ignore[arg-type]
        payloads,
        refs[:-1],  # type: ignore[arg-type]
        issues,
    )
    assert {issue.code for issue in issues} == {"component_fact_ref_mismatch"}


def test_process_ordinals_and_gpu_uuid_overlap_are_counted_from_raw_rows() -> None:
    attempts = [
        {
            "attempt_id": "attempt-1",
            "run_id": "run-a",
            "workflow_id": "experiment-a",
            "command_id": "command-1",
            "kind": "PROCESS",
            "experiment_id": "experiment-a",
            "execution_id": "execution-a",
            "attempt_ordinal": 1,
        },
        {
            "attempt_id": "attempt-3",
            "run_id": "run-a",
            "workflow_id": "experiment-a",
            "command_id": "command-3",
            "kind": "PROCESS",
            "experiment_id": "experiment-a",
            "execution_id": "execution-a",
            "attempt_ordinal": 3,
        },
    ]
    issues: list[OracleIssue] = []

    _audit_attempt_identities(attempts, issues)  # type: ignore[arg-type]

    assert "process_attempt_ordinal_mismatch" in {issue.code for issue in issues}

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(
            """
            CREATE TABLE execution_attempts(
                attempt_id TEXT, run_id TEXT, kind TEXT, execution_id TEXT,
                started_at REAL, ended_at REAL
            );
            CREATE TABLE research_facts(
                run_id TEXT, fact_type TEXT, execution_id TEXT, payload_json TEXT
            );
            """
        )
        for run_id, execution_id, attempt_id, started, ended in (
            ("run-a", "execution-a", "attempt-a", 1.0, 3.0),
            ("run-b", "execution-b", "attempt-b", 2.0, 4.0),
        ):
            connection.execute(
                "INSERT INTO execution_attempts VALUES (?,?,?,?,?,?)",
                (attempt_id, run_id, "PROCESS", execution_id, started, ended),
            )
            connection.execute(
                "INSERT INTO research_facts VALUES (?,?,?,?)",
                (
                    run_id,
                    "ExperimentPreregistered",
                    execution_id,
                    json.dumps(
                        {
                            "execution_contract": {
                                "resource_grant": {
                                    "kind": "GPU",
                                    "gpu_uuid": "GPU-shared",
                                }
                            }
                        }
                    ),
                ),
            )
        issues = []
        _audit_gpu_attempt_overlaps(connection, "run-a", issues)
    finally:
        connection.close()

    assert {issue.code for issue in issues} == {"same_gpu_attempt_overlap"}


def test_candidate_validation_process_has_intent_and_terminal_observation_causality(
    tmp_path: Path,
) -> None:
    run_id = "run-validation"
    experiment_id = f"{run_id}--component-1--experiment"
    agent_attempt_id = f"{experiment_id}--agent-1"
    process_attempt_id = f"{experiment_id}--spec-validation-1"
    execution_id = f"{experiment_id}--candidate-validation"
    request = {
        "attempt_id": process_attempt_id,
        "attempt_ordinal": 1,
        "execution_id": execution_id,
        "argv": ["python", "-c", "compile(b'pass','experiment.py','exec')"],
        "cwd": "/output",
        "env": {"HYPOLOOM_RESULT_PATH": "result.json"},
        "timeout_seconds": 5.0,
        "terminate_grace_seconds": 1.0,
        "kill_grace_seconds": 1.0,
        "stdout_path": None,
        "stderr_path": None,
    }
    attempts = [
        {
            "attempt_id": agent_attempt_id,
            "run_id": run_id,
            "workflow_id": experiment_id,
            "kind": "AGENT",
            "state": "CLOSED",
            "terminal_reason": "SUCCEEDED",
        },
        {
            "attempt_id": process_attempt_id,
            "run_id": run_id,
            "workflow_id": experiment_id,
            "command_id": "command-validate",
            "kind": "PROCESS",
            "state": "CLOSED",
            "terminal_reason": "SUCCEEDED",
            "experiment_id": experiment_id,
            "execution_id": execution_id,
            "attempt_ordinal": 1,
        },
    ]
    attempt_payloads = {
        agent_attempt_id: {
            "request": {
                "command": "DRAFT_EXPERIMENT_SPEC",
                "component_id": "primary.score",
                "role": "researcher",
            },
            "response": {"output_digest": "digest-agent-output"},
        },
        process_attempt_id: {
            "attempt_id": process_attempt_id,
            "attempt_ordinal": 1,
            "execution_id": execution_id,
            "terminal_reason": "SUCCEEDED",
            "process_request": request,
            "source_agent_attempt_id": agent_attempt_id,
            "source_agent_output_digest": "digest-agent-output",
        },
    }
    transitions = [
        {
            "run_id": run_id,
            "workflow_id": experiment_id,
            "workflow_kind": "EXPERIMENT",
            "revision": 2,
            "command_id": "command-validate",
            "state": "DRAFTED",
        },
        {
            "run_id": run_id,
            "workflow_id": experiment_id,
            "workflow_kind": "EXPERIMENT",
            "revision": 3,
            "command_id": "command-observe-validation",
            "state": "DRAFTED",
        },
    ]
    transition_payloads = {
        f"{experiment_id}@2": {
            "attempt_id": process_attempt_id,
            "agent_attempt_id": agent_attempt_id,
            "agent_output_digest": "digest-agent-output",
            "process_request": request,
            "role": "researcher",
            "component_id": "primary.score",
        },
        f"{experiment_id}@3": {
            "attempt_id": process_attempt_id,
            "agent_attempt_id": agent_attempt_id,
            "component_id": "primary.score",
            "validation_succeeded": True,
        },
    }
    commands = [
        {
            "command_id": "command-validate",
            "command_type": "VALIDATE_SPEC_CANDIDATE",
        },
        {
            "command_id": "command-observe-validation",
            "command_type": "OBSERVE_SPEC_VALIDATION",
        },
    ]
    issues: list[OracleIssue] = []

    _audit_execution_integrity(
        tmp_path,
        [],
        {},
        transitions,  # type: ignore[arg-type]
        transition_payloads,
        commands,  # type: ignore[arg-type]
        attempts,  # type: ignore[arg-type]
        attempt_payloads,
        [],
        issues,
    )

    assert issues == []

    issues = []
    _audit_execution_integrity(
        tmp_path,
        [],
        {},
        transitions[:1],  # type: ignore[arg-type]
        {f"{experiment_id}@2": transition_payloads[f"{experiment_id}@2"]},
        commands,
        attempts,  # type: ignore[arg-type]
        attempt_payloads,
        [],
        issues,
    )
    assert {issue.code for issue in issues} == {
        "process_validation_observation_mismatch"
    }


@pytest.mark.parametrize(
    ("preference", "minimum", "reserve", "busy", "expected_kind", "expected_uuid"),
    [
        ("AUTO", 8_000, 2_000, (), "GPU", "GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        ("AUTO", 20_000, 2_000, (), "CPU", None),
        (
            "GPU_REQUIRED",
            20_000,
            2_000,
            (),
            "GPU",
            "GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        ),
        ("GPU_REQUIRED", 0, 0, ("GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",),
         "GPU", "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        ("GPU_REQUIRED", 0, 0, ("GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                                 "GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
         "GPU", "GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        ("AUTO", 0, 0, ("GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                         "GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"), "CPU", None),
    ],
)
def test_oracle_independently_resolves_greatest_free_and_capacity_policy(
    preference: str,
    minimum: int,
    reserve: int,
    busy: tuple[str, ...],
    expected_kind: str,
    expected_uuid: str | None,
) -> None:
    capabilities = {
        "schema_version": 3,
        "busy_gpu_uuids": list(busy),
        "gpus": [
            {
                "index": 0,
                "uuid": "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "name": "a",
                "total_memory_mib": 24_576,
                "free_memory_mib": 10_000,
                "driver_version": "1",
            },
            {
                "index": 1,
                "uuid": "GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "name": "b",
                "total_memory_mib": 24_576,
                "free_memory_mib": 12_000,
                "driver_version": "1",
            },
        ],
    }
    encoded = json.dumps(
        capabilities, sort_keys=True, separators=(",", ":")
    ).encode()
    request = {
        "input_artifacts": [
            {
                "ref": "run--compute-capabilities",
                "media_type": "application/json",
                "content_base64": base64.b64encode(encoded).decode(),
            },
            {
                "ref": "run--resource-planning-skill",
                "media_type": "text/markdown",
                "content_base64": base64.b64encode(b"current skill").decode(),
            },
            {
                "ref": "run--resource-experience",
                "media_type": "application/json",
                "content_base64": base64.b64encode(b"{}").decode(),
            },
        ]
    }
    environment = [
        ["CUDA_DEVICE_ORDER", "PCI_BUS_ID"],
        ["CUDA_VISIBLE_DEVICES", expected_uuid or ""],
    ]
    contract = {
        "resource_preference": preference,
        "resource_grant": {"kind": expected_kind, "gpu_uuid": expected_uuid},
        "sandbox_profile": (
            "bwrap-gpu-single-no-network/1"
            if expected_kind == "GPU"
            else "bwrap-cpu-no-network/2"
        ),
        "environment": environment,
    }
    issues: list[OracleIssue] = []

    resource = _audit_resource_contract(
        "fact-resource",
        {"schema_version": 4, "resource_preference": preference},
        contract,
        request,
        {"typed_candidate": {"resource_preference": preference}},
        {
            "min_vram_per_experiment": minimum,
            "gpu_vram_reserve_mb": reserve,
        },
        issues,
    )

    assert issues == []
    assert resource is not None
    assert resource["expected_kind"] == expected_kind
    assert resource["gpu_uuid"] == expected_uuid
    assert resource["reserve_memory_mib"] == reserve
    assert resource["telemetry_required"] is True


def test_oracle_rejects_malformed_busy_snapshot_and_busy_device_grant() -> None:
    gpu_a = {
        "index": 0,
        "uuid": "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "name": "a",
        "total_memory_mib": 24_576,
        "free_memory_mib": 10_000,
        "driver_version": "1",
    }
    gpu_b = {**gpu_a, "index": 1, "uuid": "GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
             "name": "b", "free_memory_mib": 12_000}

    def request(capabilities: dict[str, object]) -> dict[str, object]:
        encoded = json.dumps(capabilities, sort_keys=True, separators=(",", ":")).encode()
        return {"input_artifacts": [{
            "ref": "run--compute-capabilities",
            "media_type": "application/json",
            "content_base64": base64.b64encode(encoded).decode(),
        }]}

    cpu_contract = {
        "resource_preference": "AUTO",
        "resource_grant": {"kind": "CPU", "gpu_uuid": None},
        "sandbox_profile": "bwrap-cpu-no-network/2",
        "environment": [["CUDA_DEVICE_ORDER", "PCI_BUS_ID"], ["CUDA_VISIBLE_DEVICES", ""]],
    }
    malformed = [
        {"schema_version": 3, "gpus": [gpu_a]},
        {"schema_version": 3, "gpus": [gpu_a], "busy_gpu_uuids": [gpu_a["uuid"], gpu_a["uuid"]]},
        {"schema_version": 3, "gpus": [gpu_a], "busy_gpu_uuids": [gpu_b["uuid"]]},
    ]
    for capabilities in malformed:
        issues: list[OracleIssue] = []
        _audit_resource_contract(
            "fact-resource", {"schema_version": 4, "resource_preference": "AUTO"},
            cpu_contract, request(capabilities),
            {"typed_candidate": {"resource_preference": "AUTO"}},
            {"min_vram_per_experiment": 0, "gpu_vram_reserve_mb": 0}, issues,
        )
        assert "compute_capability_snapshot_invalid" in {issue.code for issue in issues}

    valid = {"schema_version": 3, "gpus": [gpu_a, gpu_b],
             "busy_gpu_uuids": [gpu_b["uuid"]]}
    wrong_gpu_contract = {
        "resource_preference": "GPU_REQUIRED",
        "resource_grant": {"kind": "GPU", "gpu_uuid": gpu_b["uuid"]},
        "sandbox_profile": "bwrap-gpu-single-no-network/1",
        "environment": [["CUDA_DEVICE_ORDER", "PCI_BUS_ID"],
                        ["CUDA_VISIBLE_DEVICES", gpu_b["uuid"]]],
    }
    issues = []
    _audit_resource_contract(
        "fact-resource", {"schema_version": 4, "resource_preference": "GPU_REQUIRED"},
        wrong_gpu_contract, request(valid),
        {"typed_candidate": {"resource_preference": "GPU_REQUIRED"}},
        {"min_vram_per_experiment": 0, "gpu_vram_reserve_mb": 0}, issues,
    )
    assert "resource_resolution_mismatch" in {issue.code for issue in issues}


@pytest.mark.parametrize("component_kind,preference", [
    ("artifact", "AUTO"), ("metric", "AUTO"), ("metric", "GPU_REQUIRED"),
])
def test_oracle_models_cpu_only_override_for_nonproduction_artifact_work(
    component_kind, preference,
) -> None:
    gpu = {
        "index": 0,
        "uuid": "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "name": "a",
        "total_memory_mib": 24_576,
        "free_memory_mib": 20_000,
        "driver_version": "1",
    }
    capabilities = {"schema_version": 3, "busy_gpu_uuids": [], "gpus": [gpu]}
    encoded = json.dumps(capabilities, sort_keys=True, separators=(",", ":")).encode()
    request = {"input_artifacts": [{
        "ref": "run--compute-capabilities",
        "media_type": "application/json",
        "content_base64": base64.b64encode(encoded).decode(),
    }]}
    cpu_contract = {
        "resource_preference": preference,
        "resource_grant": {"kind": "CPU", "gpu_uuid": None},
        "sandbox_profile": "bwrap-cpu-no-network/2",
        "environment": [["CUDA_DEVICE_ORDER", "PCI_BUS_ID"], ["CUDA_VISIBLE_DEVICES", ""]],
    }
    # A metric component may publish artifact-shaped GOAL results without a
    # local measurement. Storage shape must not change its GPU eligibility.
    payload = {"schema_version": 4, "resource_preference": preference,
               "result_kind": "artifact", "component_id": "comp-x"}
    component = {"id": "comp-x", "protocol": "p1", "result": {"kind": component_kind}}
    nonproduction = {"components": [component],
                     "delivery_contracts": {"protocols": {}}}
    contract = cpu_contract if component_kind == "artifact" else {
        "resource_preference": preference,
        "resource_grant": {"kind": "GPU", "gpu_uuid": gpu["uuid"]},
        "sandbox_profile": "bwrap-gpu-single-no-network/1",
        "environment": [["CUDA_DEVICE_ORDER", "PCI_BUS_ID"], ["CUDA_VISIBLE_DEVICES", gpu["uuid"]]],
    }
    issues: list[OracleIssue] = []
    resource = _audit_resource_contract(
        "fact-resource", payload, contract, request,
        {"typed_candidate": {"resource_preference": preference}},
        {"min_vram_per_experiment": 0, "gpu_vram_reserve_mb": 0}, issues,
        cpu_only_artifact=_oracle_cpu_only_artifact(nonproduction, payload),
    )
    assert issues == []
    assert resource is not None and resource["expected_kind"] == contract["resource_grant"]["kind"]

    production = {"components": [component],
                  "delivery_contracts": {"protocols": {"p1": {"production": True}}}}
    assert _oracle_cpu_only_artifact(production, payload) is False
    issues = []
    _audit_resource_contract(
        "fact-resource", payload, cpu_contract, request,
        {"typed_candidate": {"resource_preference": preference}},
        {"min_vram_per_experiment": 0, "gpu_vram_reserve_mb": 0}, issues,
        cpu_only_artifact=_oracle_cpu_only_artifact(production, payload),
    )
    assert "resource_resolution_mismatch" in {issue.code for issue in issues}


def test_current_gpu_terminal_requires_usage_but_legacy_and_prestart_remain_readable(
) -> None:
    gpu_uuid = "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    current_resource = {
        "expected_kind": "GPU",
        "gpu_uuid": gpu_uuid,
        "reserve_memory_mib": 4_096,
        "telemetry_required": True,
    }
    missing_usage = {
        "terminal_reason": "SUCCEEDED",
        "process_group_id": 41_001,
    }
    issues: list[OracleIssue] = []

    _audit_process_resource_observation(
        missing_usage, current_resource, "attempt-current", issues
    )

    assert {issue.code for issue in issues} == {
        "process_resource_observation_mismatch"
    }

    issues = []
    _audit_process_resource_observation(
        missing_usage,
        {**current_resource, "telemetry_required": False},
        "attempt-legacy",
        issues,
    )
    assert issues == []

    issues = []
    _audit_process_resource_observation(
        {"terminal_reason": "FAILED", "synthetic_before_spawn": True},
        current_resource,
        "attempt-prestart",
        issues,
    )
    assert issues == []

    _audit_process_resource_observation(
        {"terminal_reason": "LOST", "absence_proven": True},
        current_resource,
        "attempt-lost",
        issues,
    )
    assert issues == []


def test_legacy_gpu_contract_without_current_resource_context_keeps_missing_usage_readable(
) -> None:
    gpu_uuid = "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    capabilities = {
        "schema_version": 2,
        "gpus": [
            {
                "index": 0,
                "uuid": gpu_uuid,
                "name": "legacy-gpu",
                "total_memory_mib": 24_576,
                "free_memory_mib": 12_000,
                "driver_version": "1",
            }
        ],
    }
    encoded = json.dumps(
        capabilities, sort_keys=True, separators=(",", ":")
    ).encode()
    request = {
        "input_artifacts": [
            {
                "ref": "legacy--compute-capabilities",
                "media_type": "application/json",
                "content_base64": base64.b64encode(encoded).decode(),
            }
        ]
    }
    contract = {
        "resource_preference": "GPU_REQUIRED",
        "resource_grant": {"kind": "GPU", "gpu_uuid": gpu_uuid},
        "sandbox_profile": "bwrap-gpu-single-no-network/1",
        "environment": [
            ["CUDA_DEVICE_ORDER", "PCI_BUS_ID"],
            ["CUDA_VISIBLE_DEVICES", gpu_uuid],
        ],
    }
    issues: list[OracleIssue] = []
    resource = _audit_resource_contract(
        "fact-legacy-resource",
        {"schema_version": 4, "resource_preference": "GPU_REQUIRED"},
        contract,
        request,
        {"typed_candidate": {"resource_preference": "GPU_REQUIRED"}},
        {
            "min_vram_per_experiment": 4_096,
            "gpu_vram_reserve_mb": 2_048,
        },
        issues,
    )

    assert issues == []
    assert resource is not None
    assert resource["telemetry_required"] is False
    _audit_process_resource_observation(
        {"terminal_reason": "SUCCEEDED", "process_group_id": 41_001},
        resource,
        "attempt-legacy",
        issues,
    )
    assert issues == []


def test_oracle_rejects_inconsistent_gpu_pressure_observations() -> None:
    gpu_uuid = "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    resource = {
        "expected_kind": "GPU",
        "gpu_uuid": gpu_uuid,
        "reserve_memory_mib": 4_096,
        "telemetry_required": True,
    }
    valid = {
        "terminal_reason": "FAILED",
        "resource_usage": {
            "gpu_uuid": gpu_uuid,
            "total_memory_mib": 24_576,
            "initial_free_memory_mib": 8_192,
            "minimum_free_memory_mib": 1_024,
            "latest_free_memory_mib": 1_024,
            "latest_used_memory_mib": 23_552,
            "latest_utilization_percent": 99,
            "sample_count": 2,
            "pressure_exceeded": True,
        },
        "resource_pressure": {
            "reason": "sustained_free_memory_below_reserve",
            "reserve_memory_mib": 4_096,
        },
    }
    issues: list[OracleIssue] = []
    _audit_process_resource_observation(valid, resource, "attempt", issues)
    assert issues == []

    counterexamples = [
        {**valid, "resource_usage": {**valid["resource_usage"], "gpu_uuid": "GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}},
        {**valid, "resource_usage": {**valid["resource_usage"], "sample_count": 0}},
        {**valid, "resource_usage": {**valid["resource_usage"], "minimum_free_memory_mib": 8_192,
                                      "latest_free_memory_mib": 8_192, "latest_used_memory_mib": 16_384}},
        {**valid, "resource_pressure": None},
        {**valid, "terminal_reason": "CANCELLED"},
    ]
    for observation in counterexamples:
        issues = []
        _audit_process_resource_observation(observation, resource, "attempt", issues)
        assert {issue.code for issue in issues} == {
            "process_resource_observation_mismatch"
        }

    nonpressure_with_malformed_reason = {
        **valid,
        "terminal_reason": "SUCCEEDED",
        "resource_usage": {**valid["resource_usage"], "pressure_exceeded": False},
        "resource_pressure": {"reason": "not-a-pressure-cause", "reserve_memory_mib": 4_096},
    }
    issues = []
    _audit_process_resource_observation(
        nonpressure_with_malformed_reason, resource, "attempt", issues
    )
    assert {issue.code for issue in issues} == {
        "process_resource_observation_mismatch"
    }


def test_gpu_pressure_reserve_must_match_goal_budget() -> None:
    gpu_uuid = "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    resource = {
        "expected_kind": "GPU",
        "gpu_uuid": gpu_uuid,
        "reserve_memory_mib": 4_096,
        "telemetry_required": True,
    }
    observation = {
        "terminal_reason": "FAILED",
        "resource_usage": {
            "gpu_uuid": gpu_uuid,
            "total_memory_mib": 24_576,
            "initial_free_memory_mib": 8_192,
            "minimum_free_memory_mib": 1_024,
            "latest_free_memory_mib": 1_024,
            "latest_used_memory_mib": 23_552,
            "latest_utilization_percent": 99,
            "sample_count": 2,
            "pressure_exceeded": True,
        },
        "resource_pressure": {
            "reason": "sustained_free_memory_below_reserve",
            "reserve_memory_mib": 2_048,
        },
    }
    issues: list[OracleIssue] = []

    _audit_process_resource_observation(
        observation, resource, "attempt-wrong-reserve", issues
    )

    assert {issue.code for issue in issues} == {
        "process_resource_observation_mismatch"
    }


def renamed_pressure_usage(gpu_uuid: str, attribution: str | None) -> dict[str, object]:
    usage: dict[str, object] = {
        "gpu_uuid": gpu_uuid,
        "gpu_total_memory_mib": 24_576,
        "gpu_initial_free_memory_mib": 8_192,
        "gpu_minimum_free_memory_mib": 1_024,
        "gpu_latest_free_memory_mib": 1_024,
        "gpu_latest_used_memory_mib": 23_552,
        "gpu_latest_utilization_percent": 99,
        "sample_count": 2,
        "pressure_exceeded": True,
    }
    if attribution is not None:
        usage["pressure_attribution"] = attribution
    return usage


@pytest.mark.parametrize("attribution,owned", [("self", 552), ("unknown", None)])
def test_oracle_accepts_renamed_attributed_pressure_cancel(attribution, owned) -> None:
    gpu_uuid = "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    resource = {
        "expected_kind": "GPU",
        "gpu_uuid": gpu_uuid,
        "reserve_memory_mib": 4_096,
        "telemetry_required": True,
    }
    observation = {
        "terminal_reason": "FAILED",
        "resource_usage": renamed_pressure_usage(gpu_uuid, attribution),
        "resource_pressure": {
            "reason": "sustained_free_gpu_memory_below_reserve",
            "gpu_reserve_memory_mib": 4_096,
            "owned_memory_mib_at_decision": owned,
            "attribution": attribution,
            "terminate_grace_seconds": 30.0,
        },
    }
    issues: list[OracleIssue] = []
    _audit_process_resource_observation(observation, resource, "attempt", issues)
    assert issues == []


@pytest.mark.parametrize(
    "change",
    [
        "legacy_reason",
        "legacy_reserve_key",
        "missing_attribution",
        "missing_owned_field",
        "missing_grace",
        "invalid_grace",
        "usage_without_attribution",
        "external_with_payload",
        "self_without_payload",
        "wrong_reserve",
    ],
)
def test_oracle_rejects_rewritten_renamed_pressure_decisions(change) -> None:
    gpu_uuid = "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    resource = {
        "expected_kind": "GPU",
        "gpu_uuid": gpu_uuid,
        "reserve_memory_mib": 4_096,
        "telemetry_required": True,
    }
    valid = {
        "terminal_reason": "FAILED",
        "resource_usage": renamed_pressure_usage(gpu_uuid, "self"),
        "resource_pressure": {
            "reason": "sustained_free_gpu_memory_below_reserve",
            "gpu_reserve_memory_mib": 4_096,
            "owned_memory_mib_at_decision": 552,
            "attribution": "self",
            "terminate_grace_seconds": 30.0,
        },
    }
    if change == "legacy_reason":
        observation = {
            **valid,
            "resource_pressure": {
                **valid["resource_pressure"],
                "reason": "sustained_free_memory_below_reserve",
            },
        }
    elif change == "legacy_reserve_key":
        observation = {
            **valid,
            "resource_pressure": {
                "reason": "sustained_free_memory_below_reserve",
                "reserve_memory_mib": 4_096,
            },
        }
    elif change == "missing_attribution":
        observation = copy.deepcopy(valid)
        observation["resource_pressure"].pop("attribution")
    elif change == "missing_owned_field":
        observation = copy.deepcopy(valid)
        observation["resource_pressure"].pop("owned_memory_mib_at_decision")
    elif change == "missing_grace":
        observation = copy.deepcopy(valid)
        observation["resource_pressure"].pop("terminate_grace_seconds")
    elif change == "invalid_grace":
        observation = copy.deepcopy(valid)
        observation["resource_pressure"]["terminate_grace_seconds"] = -1
    elif change == "usage_without_attribution":
        observation = {**valid, "resource_usage": renamed_pressure_usage(gpu_uuid, None)}
    elif change == "external_with_payload":
        observation = {
            **valid,
            "resource_usage": renamed_pressure_usage(gpu_uuid, "external"),
        }
    elif change == "self_without_payload":
        observation = {**valid, "resource_pressure": None}
    elif change == "wrong_reserve":
        observation = copy.deepcopy(valid)
        observation["resource_pressure"]["gpu_reserve_memory_mib"] = 2_048
    issues: list[OracleIssue] = []
    _audit_process_resource_observation(observation, resource, "attempt", issues)
    assert {issue.code for issue in issues} == {
        "process_resource_observation_mismatch"
    }


def test_external_pressure_attribution_keeps_the_owned_process_alive() -> None:
    gpu_uuid = "GPU-e96b09e9-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    sample = {
        "gpu_uuid": gpu_uuid,
        "root_process_group_id": 100,
        "root_start_time_ticks": 1000,
        "complete": True,
        "reason": None,
        "observed_at": 15.0,
        "duration_seconds": 0.01,
        "resident_bytes": 552 * 1024**2,
        "processes": [
            {
                "pid": 102,
                "resident_bytes": 552 * 1024**2,
                "ancestry": [
                    {"pid": 102, "ppid": 101, "start_time_ticks": 1002},
                    {"pid": 101, "ppid": 100, "start_time_ticks": 1001},
                    {"pid": 100, "ppid": 1, "start_time_ticks": 1000},
                ],
            }
        ],
    }
    resource = {
        "expected_kind": "GPU",
        "gpu_uuid": gpu_uuid,
        "reserve_memory_mib": 4_096,
        "telemetry_required": True,
        "attributable": True,
        "attribution_attempt": {
            "process_group_id": 100,
            "started_at": 10.0,
            "ended_at": 20.0,
            "terminal_reason": "SUCCEEDED",
        },
    }
    observation = {
        "terminal_reason": "SUCCEEDED",
        "returncode": 0,
        "resource_usage": {
            "gpu_uuid": gpu_uuid,
            "gpu_total_memory_mib": 24_576,
            "gpu_initial_free_memory_mib": 19_579,
            "gpu_minimum_free_memory_mib": 3_901,
            "gpu_latest_free_memory_mib": 3_901,
            "gpu_latest_used_memory_mib": 20_675,
            "gpu_latest_utilization_percent": 53,
            "sample_count": 339,
            "pressure_exceeded": True,
            "pressure_attribution": "external",
            "owned_memory": {
                "sample_count": 339,
                "unknown_sample_count": 0,
                "latest": sample,
                "peak": copy.deepcopy(sample),
            },
        },
    }
    issues: list[OracleIssue] = []
    _audit_process_resource_observation(observation, resource, "attempt-exp-10", issues)
    assert issues == []


def test_oracle_independently_recomputes_all_host_batch_commands(
    component_batch_profile: Path,
) -> None:
    admitted = admit_profile(
        component_batch_profile,
        run_id="oracle-batch-projection",
        allow_gpu=True,
    )
    assert admitted.admitted, admitted.to_dict()
    assert admitted.create_run_payload is not None
    document = admitted.create_run_payload["research_contract"]
    representatives = {
        "must.dataset_alpha_screen_completed": (
            "paired_control_treatment",
            6,
        ),
        "primary.dataset_alpha_final_score": ("seeded_trials", 5),
        "must.dataset_alpha_audit": ("checkpoint_audit", 5),
    }

    for component_id, (shape, trial_count) in representatives.items():
        execution = _oracle_component_execution(
            document,
            component_id,
            method_family_id="family-a",
            implementation_sha256="1" * 64,
            device="cuda",
        )
        assert execution is not None
        assert execution.argv[:2] == (
            "python",
            ".hypoloom/trial_driver.py",
        )
        encoded = execution.argv[2]
        plan = json.loads(encoded)
        assert encoded == json.dumps(
            plan,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        assert plan["shape"] == shape
        assert plan["identity"] == {
            "method_family_id": "family-a",
            "implementation_sha256": "1" * 64,
        }
        expected_metrics = (
            {"audit.max_integrity_violation": "max_integrity_violation"}
            if shape == "checkpoint_audit"
            else {"metrics.quality_score": "quality_score"}
        )
        assert plan["metric_paths"] == expected_metrics
        if shape == "seeded_trials":
            assert plan["checkpoint_torch_load"] is True
        assert len(plan["trials"]) == trial_count
        assert all(
            trial["raw_result"].startswith("raw/")
            and not Path(trial["raw_result"]).is_absolute()
            and trial["target_argv"]
            and "--device" in trial["target_argv"]
            and trial["target_argv"][trial["target_argv"].index("--device") + 1]
            == "cuda"
            for trial in plan["trials"]
        )

    paired = json.loads(
        _oracle_component_execution(
            document,
            "must.dataset_alpha_screen_completed",
            method_family_id="family-a",
            implementation_sha256="1" * 64,
            device="cuda",
        ).argv[2]  # type: ignore[union-attr]
    )
    assert [trial["arm"] for trial in paired["trials"]] == [
        "control",
        "treatment",
    ] * 3
    assert all(
        trial["pythonpath"].startswith("/hypoloom-control-target/")
        for trial in paired["trials"][::2]
    )
    seeded = json.loads(
        _oracle_component_execution(
            document,
            "primary.dataset_alpha_final_score",
            method_family_id="family-a",
            implementation_sha256="1" * 64,
            device="cuda",
        ).argv[2]  # type: ignore[union-attr]
    )
    assert [trial["checkpoint"] for trial in seeded["trials"]] == [
        f"raw-checkpoints/trial.seed{seed}.pt" for seed in range(5)
    ]
    audit = json.loads(
        _oracle_component_execution(
            document,
            "must.dataset_alpha_audit",
            method_family_id="family-a",
            implementation_sha256="1" * 64,
            device="cuda",
        ).argv[2]  # type: ignore[union-attr]
    )
    assert [trial["checkpoint"] for trial in audit["trials"]] == [
        f"/hypoloom-inputs/1/{index}" for index in range(1, 6)
    ]
    assert all("{checkpoint}" in trial["target_argv"] for trial in audit["trials"])


def test_host_driver_process_request_requires_exact_control_data_and_upstream_sources(
    tmp_path: Path,
) -> None:
    attempt_id = "experiment--process-1"
    work = tmp_path / "work" / attempt_id
    upstream = tmp_path / "publication" / "candidate.json"
    upstream.parent.mkdir()
    upstream.write_text("{}", encoding="utf-8")
    other = tmp_path / "publication" / "other.json"
    other.write_text("{}", encoding="utf-8")
    data = tmp_path / "dataset"
    data.mkdir()
    target = "/frozen/target"
    inputs = {"candidate": ["/hypoloom-inputs/1/1"]}
    environment = {
        "HYPOLOOM_READ_ROOTS": "[]",
        "HYPOLOOM_REQUIRED_INPUTS": json.dumps(
            inputs, sort_keys=True, separators=(",", ":")
        ),
        "HYPOLOOM_TARGET_ROOT": target,
    }
    mounts = _OracleComponentMounts(
        bindings=(
            _OracleMountBinding(destination=target, attempt_leaf="target"),
            _OracleMountBinding(
                destination="/hypoloom-control-target",
                attempt_leaf="control-target",
            ),
            _OracleMountBinding(
                destination="/hypoloom-data/cache",
                source=str(data.resolve()),
            ),
            _OracleMountBinding(
                destination="/hypoloom-inputs/1/1",
                source=str(upstream.resolve()),
            ),
        ),
        read_roots=(),
        required_inputs=(("candidate", ("/hypoloom-inputs/1/1",)),),
        target_root=target,
    )
    plan = json.dumps({"schema_version": 1}, sort_keys=True, separators=(",", ":"))
    argv = [
        "/usr/bin/bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/runtime",
        "--ro-bind",
        "/runtime-prefix",
        "/runtime",
        "--dir",
        "/input",
        "--dir",
        "/output",
        "--ro-bind",
        str(work / "input"),
        "/input",
        "--bind",
        str(work / "output"),
        "/output",
        "--ro-bind",
        str(work / "target"),
        target,
        "--ro-bind",
        str(work / "control-target"),
        "/hypoloom-control-target",
        "--ro-bind",
        str(data.resolve()),
        "/hypoloom-data/cache",
        "--ro-bind",
        str(upstream.resolve()),
        "/hypoloom-inputs/1/1",
    ]
    for name, value in sorted(environment.items()):
        argv.extend(("--setenv", name, value))
    argv.extend(
        (
            "--chdir",
            "/output",
            "--",
            "/runtime/bin/python",
            "/input/.hypoloom/trial_driver.py",
            plan,
        )
    )
    arguments = {
        "attempt_id": attempt_id,
        "source_relative_path": ".hypoloom/trial_driver.py",
        "execution_argv": (
            "python",
            ".hypoloom/trial_driver.py",
            plan,
        ),
        "expected_bindings": (),
        "expected_environment": environment,
        "expected_component_mounts": mounts,
    }

    assert _canonical_bwrap_argv(argv, **arguments)
    substituted = list(argv)
    substituted[substituted.index(str(upstream.resolve()))] = str(other.resolve())
    assert not _canonical_bwrap_argv(substituted, **arguments)
    wrong_control = list(argv)
    wrong_control[wrong_control.index(str(work / "control-target"))] = str(
        work / "target"
    )
    assert not _canonical_bwrap_argv(wrong_control, **arguments)


def test_oracle_derives_patch_identity_and_input_mount_from_committed_publication(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    publication = artifact_root / "run-a/impl-exp/impl-exec/impl-attempt/result"
    publication.mkdir(parents=True)
    patch = publication / "implementation.patch"
    patch.write_bytes(b"diff --git a/a b/a\n")
    candidate = publication / "candidate.json"
    candidate.write_text("{}", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    reads = tmp_path / "reads"
    reads.mkdir()
    document = {
        "components": [
            {
                "id": "must.implementation",
                "dependencies": [],
                "result": {"kind": "artifact"},
                "required_inputs": [],
            },
            {
                "id": "must.paired",
                "dependencies": ["must.implementation"],
                "result": {
                    "kind": "metric",
                    "target_overlay_policy": "latest",
                },
                "required_inputs": [
                    {
                        "resource": "candidate-manifest",
                        "produced_by": "must.implementation",
                        "artifact": "candidate_manifest",
                        "min_count": 1,
                    }
                ],
            },
        ],
        "safety": {"read_roots": [str(reads)]},
    }
    outcome = {
        "fact_id": "outcome-fact",
        "fact_type": "CanonicalOutcomeAccepted",
        "raw_order": 2,
        "outcome_id": "outcome-impl",
        "artifact_id": "artifact-impl",
        "experiment_id": "impl-exp",
    }
    spec = {"fact_id": "spec-fact", "raw_order": 3}
    payloads = {
        "outcome-fact": {
            "component_id": "must.implementation",
            "method_family_id": "family-a",
            "outcome": "SUCCESS",
            "logical_artifacts": [
                {
                    "logical_artifact": "implementation_patch",
                    "paths": ["implementation.patch"],
                },
                {
                    "logical_artifact": "candidate_manifest",
                    "paths": ["candidate.json"],
                },
            ],
        }
    }
    artifacts = [
        {
            "artifact_id": "artifact-impl",
            "run_id": "run-a",
            "experiment_id": "impl-exp",
            "execution_id": "impl-exec",
            "attempt_id": "impl-attempt",
            "artifact_kind": "result",
            "path": "run-a/impl-exp/impl-exec/impl-attempt/result",
        }
    ]
    digest = _oracle_implementation_sha256(
        document,
        "must.paired",
        "family-a",
        spec,  # type: ignore[arg-type]
        [outcome],  # type: ignore[list-item]
        payloads,
        artifacts,  # type: ignore[arg-type]
        artifact_root,
    )
    assert digest == hashlib.sha256(patch.read_bytes()).hexdigest()

    execution = _OracleComponentExecution(
        argv=("python", ".hypoloom/trial_driver.py", "{}"),
        source_relative_path=".hypoloom/trial_driver.py",
        target_root=str(target),
        data_bindings=((str(data), "/hypoloom-data/cache"),),
        control_target=True,
    )
    spec_payload = {
        "method_family_id": "family-a",
        "upstream_outcomes": [
            {
                "resource": "candidate-manifest",
                "produced_by_component": "must.implementation",
                "logical_artifact": "candidate_manifest",
                "min_count": 1,
                "outcome_id": "outcome-impl",
                "artifact_id": "artifact-impl",
            }
        ],
    }
    mounts = _oracle_component_mounts(
        document,
        "must.paired",
        execution,
        spec,  # type: ignore[arg-type]
        spec_payload,
        [outcome],  # type: ignore[list-item]
        payloads,
        artifacts,  # type: ignore[arg-type]
        artifact_root,
        {"impl-exp@1": {"experiment_ordinal": 1}},
    )
    assert mounts is not None
    assert mounts.bindings == (
        _OracleMountBinding(destination=str(target.resolve()), attempt_leaf="target"),
        _OracleMountBinding(
            destination="/hypoloom-control-target",
            attempt_leaf="control-target",
        ),
        _OracleMountBinding(
            destination="/hypoloom-data/cache", source=str(data.resolve())
        ),
        _OracleMountBinding(
            destination=str(reads.resolve()), source=str(reads.resolve())
        ),
        _OracleMountBinding(
            destination="/hypoloom-inputs/1/1",
            source=str(candidate.resolve()),
        ),
    )
    environment = {
        "HYPOLOOM_READ_ROOTS": json.dumps(
            [str(reads.resolve())], separators=(",", ":")
        ),
        "HYPOLOOM_REQUIRED_INPUTS": json.dumps(
            {"candidate-manifest": ["/hypoloom-inputs/1/1"]},
            sort_keys=True,
            separators=(",", ":"),
        ),
        "HYPOLOOM_TARGET_ROOT": str(target.resolve()),
        "HYPOLOOM_RESULT_PATH": "result.json",
    }
    assert _oracle_component_environment_matches(
        [list(item) for item in sorted(environment.items())], mounts
    )


def test_oracle_input_mounts_skip_operational_outcomes_and_bind_greatest_tip(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    target = tmp_path / "target"
    target.mkdir()
    reads = tmp_path / "reads"
    reads.mkdir()
    document = {
        "components": [
            {
                "id": "must.implementation",
                "dependencies": [],
                "result": {"kind": "artifact"},
                "required_inputs": [],
            },
            {
                "id": "must.paired",
                "dependencies": ["must.implementation"],
                "result": {
                    "kind": "metric",
                    "target_overlay_policy": "latest",
                },
                "required_inputs": [
                    {
                        "resource": "candidate-manifest",
                        "produced_by": "must.implementation",
                        "artifact": "candidate_manifest",
                        "min_count": 1,
                    }
                ],
            },
        ],
        "safety": {"read_roots": [str(reads)]},
    }
    facts: list[dict[str, object]] = []
    payloads: dict[str, dict[str, object]] = {}
    artifacts: list[dict[str, object]] = []
    transitions: dict[str, dict[str, object]] = {}
    # One producer family whose same-family experiments accumulated
    # operational outcomes (execution failure, whitelisted integrity
    # revision) before two accepted successes, mirroring a repaired
    # implementation component history.
    history = (
        (1, "EXECUTION_FAILURE", False),
        (2, "INTEGRITY_REJECTED", False),
        (3, "SUCCESS", True),
        (4, "SUCCESS", True),
    )
    for ordinal, outcome_kind, comparable in history:
        relative_root = (
            f"run/experiment-{ordinal}/execution-{ordinal}/"
            f"attempt-{ordinal}/result"
        )
        publication = artifact_root / relative_root
        publication.mkdir(parents=True)
        (publication / "candidate.json").write_text("{}", encoding="utf-8")
        (publication / "implementation.patch").write_bytes(
            f"candidate revision {ordinal}\n".encode()
        )
        fact_id = f"outcome-fact-{ordinal}"
        facts.append(
            {
                "fact_id": fact_id,
                "fact_type": "CanonicalOutcomeAccepted",
                "raw_order": ordinal,
                "outcome_id": f"outcome-{ordinal}",
                "artifact_id": f"artifact-{ordinal}",
                "experiment_id": f"experiment-{ordinal}",
            }
        )
        payloads[fact_id] = {
            "component_id": "must.implementation",
            "method_family_id": "family-a",
            "outcome": outcome_kind,
            "logical_artifacts": (
                [
                    {
                        "logical_artifact": "implementation_patch",
                        "paths": ["implementation.patch"],
                    },
                    {
                        "logical_artifact": "candidate_manifest",
                        "paths": ["candidate.json"],
                    },
                ]
                if comparable
                else []
            ),
        }
        artifacts.append(
            {
                "artifact_id": f"artifact-{ordinal}",
                "run_id": "run",
                "experiment_id": f"experiment-{ordinal}",
                "execution_id": f"execution-{ordinal}",
                "attempt_id": f"attempt-{ordinal}",
                "artifact_kind": "result",
                "path": relative_root,
            }
        )
        transitions[f"experiment-{ordinal}@1"] = {
            "experiment_ordinal": ordinal
        }
    execution = _OracleComponentExecution(
        argv=("python", ".hypoloom/trial_driver.py", "{}"),
        source_relative_path=".hypoloom/trial_driver.py",
        target_root=str(target),
        data_bindings=(),
        control_target=False,
    )
    spec = {"fact_id": "spec-fact", "raw_order": 9}

    def mounts_for(binding_ordinal: int) -> object:
        return _oracle_component_mounts(
            document,
            "must.paired",
            execution,
            spec,  # type: ignore[arg-type]
            {
                "method_family_id": "family-a",
                "upstream_outcomes": [
                    {
                        "resource": "candidate-manifest",
                        "produced_by_component": "must.implementation",
                        "logical_artifact": "candidate_manifest",
                        "min_count": 1,
                        "outcome_id": f"outcome-{binding_ordinal}",
                        "artifact_id": f"artifact-{binding_ordinal}",
                    }
                ],
            },
            facts,  # type: ignore[arg-type]
            payloads,
            artifacts,  # type: ignore[arg-type]
            artifact_root,
            transitions,
        )

    latest = mounts_for(4)
    assert latest is not None
    assert latest.required_inputs == (
        ("candidate-manifest", ("/hypoloom-inputs/1/1",)),
    )
    source = next(
        binding.source
        for binding in latest.bindings
        if binding.destination == "/hypoloom-inputs/1/1"
    )
    assert Path(str(source)) == (
        artifact_root
        / "run/experiment-4/execution-4/attempt-4/result/candidate.json"
    ).resolve()
    # A preregistration that bound a superseded tip is not derivable.
    assert mounts_for(3) is None


def test_component_input_binding_accepts_clean_refutation_artifacts(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    publication = artifact_root / "run-a/upstream/execution/attempt/result"
    publication.mkdir(parents=True)
    (publication / "candidate.json").write_text("{}", encoding="utf-8")
    component = _ComponentContract(
        component_id="primary.consumer",
        tier="primary",
        owner_role="researcher",
        proposer_roles=("researcher",),
        dependencies=("must.upstream",),
        required_inputs=(
            _ComponentInput(
                resource="upstream-candidate",
                producer_component="must.upstream",
                logical_artifact="candidate_manifest",
                min_count=1,
            ),
        ),
        result_kind="artifact",
        metric_name=None,
        metric_direction=None,
        metric_target=None,
        seeds=(),
        arms=(),
        trial_ids=(),
        required_artifacts=("report",),
        artifact_counts={"report": 1},
        required_metrics=(),
    )
    fact = {"fact_id": "fact-spec", "raw_order": 2}
    payload = {
        "result_kind": "artifact",
        "metric": None,
        "trial_plan": {"seeds": [], "arms": [], "trial_ids": []},
        "required_artifacts": ["report"],
        "evaluator_version": "component-result/6",
        "execution_contract": {},
        "method_family_id": "selected-family",
        "upstream_outcomes": [
            {
                "resource": "upstream-candidate",
                "produced_by_component": "must.upstream",
                "logical_artifact": "candidate_manifest",
                "min_count": 1,
                "outcome_id": "outcome-upstream",
                "artifact_id": "artifact-upstream",
            }
        ],
    }
    outcome = {
        "fact_id": "fact-outcome-upstream",
        "raw_order": 1,
        "artifact_id": "artifact-upstream",
    }
    artifact = {
        "artifact_id": "artifact-upstream",
        "run_id": "run-a",
        "experiment_id": "upstream",
        "execution_id": "execution",
        "attempt_id": "attempt",
        "artifact_kind": "result",
        "path": "run-a/upstream/execution/attempt/result",
    }
    issues: list[OracleIssue] = []

    _audit_component_spec(
        fact,  # type: ignore[arg-type]
        payload,
        component,
        "component-result/6",
        {"outcome-upstream": outcome},  # type: ignore[dict-item]
        {
            "fact-outcome-upstream": {
                "component_id": "must.upstream",
                "method_family_id": "selected-family",
                "outcome": "CLEAN_REFUTATION",
                "logical_artifacts": [
                    {
                        "logical_artifact": "candidate_manifest",
                        "paths": ["candidate.json"],
                    }
                ],
            }
        },
        {"artifact-upstream": artifact},  # type: ignore[dict-item]
        artifact_root,
        issues,
    )

    assert issues == []

    retained_v6_issues: list[OracleIssue] = []
    _audit_component_spec(
        fact,  # type: ignore[arg-type]
        payload,
        component,
        "component-result/6",
        {"outcome-upstream": outcome},  # type: ignore[dict-item]
        {
            "fact-outcome-upstream": {
                "component_id": "must.upstream",
                "method_family_id": "selected-family",
                "outcome": "CLEAN_REFUTATION",
                "logical_artifacts": [
                    {
                        "logical_artifact": "candidate_manifest",
                        "paths": ["candidate.json"],
                    }
                ],
            }
        },
        {"artifact-upstream": artifact},  # type: ignore[dict-item]
        artifact_root,
        retained_v6_issues,
        allow_clean_refutation_inputs=False,
    )
    assert {issue.code for issue in retained_v6_issues} == {
        "component_input_binding_mismatch"
    }


def test_oracle_implementation_patch_selects_latest_outcome_per_ancestor_component(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    document = {
        "components": [
            {
                "id": "must.implementation",
                "dependencies": [],
                "result": {"kind": "artifact"},
            },
            {
                "id": "primary.score",
                "dependencies": ["must.implementation"],
                "result": {
                    "kind": "metric",
                    "target_overlay_policy": "latest",
                },
            },
        ]
    }
    facts: list[dict[str, object]] = []
    payloads: dict[str, dict[str, object]] = {}
    artifacts: list[dict[str, object]] = []
    patches: list[Path] = []
    for ordinal in (1, 2):
        relative_root = f"run/experiment-{ordinal}/execution-{ordinal}/attempt-{ordinal}/result"
        publication = artifact_root / relative_root
        publication.mkdir(parents=True)
        patch = publication / "implementation.patch"
        patch.write_bytes(f"candidate revision {ordinal}\n".encode())
        patches.append(patch)
        fact_id = f"outcome-fact-{ordinal}"
        artifact_id = f"artifact-{ordinal}"
        facts.append(
            {
                "fact_id": fact_id,
                "fact_type": "CanonicalOutcomeAccepted",
                "raw_order": ordinal,
                "outcome_id": f"outcome-{ordinal}",
                "artifact_id": artifact_id,
            }
        )
        payloads[fact_id] = {
            "component_id": "must.implementation",
            "method_family_id": "family-a",
            "logical_artifacts": [
                {
                    "logical_artifact": "implementation_patch",
                    "paths": ["implementation.patch"],
                }
            ],
        }
        artifacts.append(
            {
                "artifact_id": artifact_id,
                "run_id": "run",
                "experiment_id": f"experiment-{ordinal}",
                "execution_id": f"execution-{ordinal}",
                "attempt_id": f"attempt-{ordinal}",
                "artifact_kind": "result",
                "path": relative_root,
            }
        )

    digest = _oracle_implementation_sha256(
        document,
        "primary.score",
        "family-a",
        {"fact_id": "spec-fact", "raw_order": 3},  # type: ignore[arg-type]
        facts,  # type: ignore[arg-type]
        payloads,
        artifacts,  # type: ignore[arg-type]
        artifact_root,
    )

    assert digest == hashlib.sha256(patches[-1].read_bytes()).hexdigest()


def _role() -> dict[str, object]:
    return {
        "id": "researcher",
        "focus": "",
        "autonomy": None,
        "protocols": {"default": None, "allowed": []},
        "budget": {},
    }


def _raw_component_contract() -> dict[str, object]:
    def component(
        component_id: str,
        tier: str,
        name: str,
        result: Mapping[str, object],
        protocol: str,
        *,
        seeds: list[int] | None = None,
        artifacts: list[str] | None = None,
        dependencies: list[str] | None = None,
        inputs: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        return {
            "id": component_id,
            "tier": tier,
            "name": name,
            "owner": "researcher",
            "proposers": ["researcher"],
            "dependencies": dependencies or [],
            "required_inputs": inputs or [],
            "protocol": protocol,
            "result": dict(result),
            "seeds": seeds or [],
            "required_artifacts": artifacts or [],
        }

    components = [
        component(
            "must.bundle",
            "must",
            "bundle",
            {"kind": "artifact", "evaluation_kind": "artifact_check"},
            "bundle",
            artifacts=["bundle"],
        ),
        component(
            "must.paired",
            "must",
            "paired",
            {
                "kind": "metric",
                "metric": "paired_score_gain_vs_baseline",
                "direction": "MAXIMIZE",
                "target": "1",
            },
            "paired",
            seeds=[0, 1, 2],
            artifacts=["per_trial_metrics"],
        ),
        component(
            "primary.final",
            "primary",
            "final",
            {
                "kind": "metric",
                "metric": "score",
                "direction": "MAXIMIZE",
                "target": "1",
            },
            "final",
            seeds=[0, 1, 2, 3, 4],
            artifacts=["per_trial_metrics", "checkpoints"],
            dependencies=["must.bundle"],
            inputs=[
                {
                    "resource": "bundle-input",
                    "produced_by": "must.bundle",
                    "artifact": "bundle",
                    "min_count": 1,
                }
            ],
        ),
        component(
            "must.causal",
            "must",
            "causal",
            {
                "kind": "metric",
                "metric": "max_future_perturbation_logit_diff",
                "direction": "MINIMIZE",
                "target": "0.01",
            },
            "causal",
            artifacts=["causal_audit"],
            dependencies=["primary.final"],
            inputs=[
                {
                    "resource": "checkpoint-input",
                    "produced_by": "primary.final",
                    "artifact": "checkpoints",
                    "min_count": 5,
                }
            ],
        ),
    ]
    protocols = {
        "bundle": {
            "result_kind": "artifact",
            "requirements": {
                "required_metrics": [],
                "required_artifacts": ["bundle"],
                "artifact_validators": [],
            },
        },
        "paired": {
            "result_kind": "metric",
            "requirements": {
                "required_metrics": [
                    "paired_delta",
                    "paired_score_gain_vs_baseline",
                ],
                "required_artifacts": ["per_trial_metrics"],
                "artifact_validators": [],
            },
        },
        "final": {
            "result_kind": "metric",
            "requirements": {
                "required_metrics": ["score", "n_successful_seeds"],
                "required_artifacts": [
                    "per_trial_metrics",
                    {"artifact": "checkpoints", "min_count": 5},
                ],
                "artifact_validators": [],
            },
        },
        "causal": {
            "result_kind": "metric",
            "requirements": {
                "required_metrics": ["max_future_perturbation_logit_diff"],
                "required_artifacts": ["causal_audit"],
                "artifact_validators": [],
            },
        },
    }
    return {
        "schema_version": 1,
        "goal": {
            "objective": "Exercise the independent component Oracle",
            "language": "",
            "verdict_rule": "required_conjunction",
            "summary_metric": {
                "name": "required_component_completion",
                "direction": "MAXIMIZE",
                "target": "4",
                "display_only": True,
            },
        },
        "roles": [_role()],
        "support_role_budgets": {},
        "components": components,
        "protocols": protocols,
        "delivery_contracts": {
            "enabled": True,
            "schema_version": 1,
            "protocols": {"paired": {"execution_shape": "paired_control_treatment"}},
        },
        "methodology": {"type": "hg", "tier": None, "controls": {}},
        "runtime": {},
        "budget": {
            "agent_invocation": {
                "max_turns": 2,
                "max_tokens": 512,
                "max_output_bytes": 16384,
                "wall_seconds": 5,
                "max_cost": "0",
                "token_enforcement_mode": "enforce",
            }
        },
        "safety": {"read_roots": [], "sandbox": None},
        "observer": {"notify": {"channels": [], "digest": False, "digest_model": None}},
    }


@contextmanager
def _running_host(state_root: Path) -> Iterator[subprocess.Popen[str]]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "hypoloom.cli",
            "serve",
            "--state-root",
            str(state_root),
        ],
        cwd=_REPOSITORY,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    socket_path = state_root / "control.sock"
    deadline = time.monotonic() + 8
    try:
        while time.monotonic() < deadline:
            if socket_path.is_socket():
                break
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(f"Host exited before serving: {stdout=} {stderr=}")
            time.sleep(0.02)
        else:
            raise AssertionError("Host did not create control.sock")
        yield process
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)


def _host_request(
    state_root: Path,
    command: str,
    payload: Mapping[str, object],
    *,
    revision: int | None,
    request_id: str,
) -> ControlResponse:
    return request_over_socket(
        state_root / "control.sock",
        ControlRequest(
            schema_version=1,
            request_id=request_id,
            expected_revision=revision,
            command=command,
            payload=payload,
        ),
        timeout=5,
    )


def _drive_component_run(
    state_root: Path, process: subprocess.Popen[str], run_id: str
) -> None:
    deadline = time.monotonic() + 20
    ordinal = 0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"Host exited while driving {run_id}: {stdout=} {stderr=}"
            )
        ordinal += 1
        status = _host_request(
            state_root,
            "STATUS",
            {"run_id": run_id},
            revision=None,
            request_id=f"status-{ordinal}",
        )
        assert status.status == "accepted" and status.result is not None
        if status.result["run_state"] == "COMPLETED":
            return
        time.sleep(0.005)
    raise AssertionError(f"component run {run_id!r} did not complete")


def _redigest_agent_observation(observation: dict[str, object]) -> str:
    request = observation["request"]
    response = observation["response"]
    assert isinstance(request, dict) and isinstance(response, dict)
    canonical_request = json.dumps(
        request,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    response["input_digest"] = hashlib.sha256(canonical_request).hexdigest()
    canonical_response = json.dumps(
        {key: value for key, value in response.items() if key != "output_digest"},
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    response["output_digest"] = hashlib.sha256(canonical_response).hexdigest()
    return json.dumps(
        observation,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def test_oracle_accepts_frozen_aci5_implicit_enforce_without_mode() -> None:
    raw_request = default_request().to_dict()
    raw_request["schema_version"] = 5
    budget = raw_request["budget"]
    assert isinstance(budget, dict)
    budget.pop("token_enforcement_mode")
    goal = {
        "fact_id": "goal-1",
        "fact_type": "GoalDefined",
        "run_id": raw_request["run_id"],
    }
    command = {
        "command_id": raw_request["command_id"],
        "run_id": raw_request["run_id"],
        "workflow_id": raw_request["owner_workflow_id"],
        "command_type": "REQUEST_HYPOTHESIS_CANDIDATE",
    }
    attempt = {
        "attempt_id": raw_request["attempt_id"],
        "attempt_ordinal": raw_request["attempt_ordinal"],
        "run_id": raw_request["run_id"],
        "workflow_id": raw_request["owner_workflow_id"],
        "command_id": raw_request["command_id"],
        "kind": "AGENT",
        "state": "OPEN",
        "process_group_id": None,
    }
    issues: list[OracleIssue] = []

    _audit_agent_attempts(
        "hypoloom-research-v5",
        "5",
        [goal],  # type: ignore[list-item]
        {
            "goal-1": {
                "research_contract": {
                    "roles": [{"id": raw_request["role"]}],
                    "components": [],
                    "budget": {"agent_invocation": dict(budget)},
                }
            }
        },
        [],
        {},
        [command],  # type: ignore[list-item]
        [attempt],  # type: ignore[list-item]
        {str(raw_request["attempt_id"]): {"request": raw_request}},
        issues,
    )

    assert issues == []


def test_raw_sqlite_filesystem_oracle_closes_components_and_rejects_tampering(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "component-oracle"
    run_id = "component-oracle"
    with _running_host(state_root) as process:
        created = _host_request(
            state_root,
            "CREATE_RUN",
            {
                "run_id": run_id,
                "research_contract": _raw_component_contract(),
                "execution": {
                    "delay_seconds": 0.0,
                    "allow_gpu": False,
                    "target": None,
                    "runtime_prefix": None,
                    "read_roots": [],
                },
            },
            revision=0,
            request_id="create-component-oracle",
        )
        assert created.status == "accepted", created
        _drive_component_run(state_root, process, run_id)

    database = state_root / "state.sqlite"
    artifact_root = state_root / "artifacts"
    report = Oracle(database, artifact_root).audit(run_id)
    assert not ({issue.code for issue in report.issues} & _COMPONENT_SCIENCE_CODES), (
        report.issues
    )
    assert report.open_attempt_count == 0
    assert report.owned_process_residue == ()

    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            "SELECT fact_type,payload_json FROM research_facts "
            "WHERE run_id=? AND fact_type IN "
            "('HypothesisAssessed','GoalVerdictRecorded') ORDER BY rowid",
            (run_id,),
        ).fetchall()
        assessments = [
            json.loads(payload)
            for fact_type, payload in rows
            if fact_type == "HypothesisAssessed"
        ]
        verdict = next(
            json.loads(payload)
            for fact_type, payload in rows
            if fact_type == "GoalVerdictRecorded"
        )
        assert {item["component_id"] for item in assessments} == {
            "must.bundle",
            "must.paired",
            "primary.final",
            "must.causal",
        }
        assert {item["assessment"] for item in assessments} == {"SUPPORTED"}
        assert verdict["verdict"] == "GOAL_MET"

        connection.execute("DROP TRIGGER immutable_research_fact_refs_delete")
        connection.execute(
            "DELETE FROM research_fact_refs WHERE rowid=("
            "SELECT r.rowid FROM research_fact_refs r "
            "JOIN research_facts f ON f.fact_id=r.fact_id "
            "WHERE f.run_id=? AND r.role='producer_outcome' LIMIT 1)",
            (run_id,),
        )
        connection.execute("DROP TRIGGER immutable_research_facts_update")
        evidence_fact_id, evidence_json = connection.execute(
            "SELECT fact_id,payload_json FROM research_facts "
            "WHERE run_id=? AND fact_type='EvidenceLinked' ORDER BY rowid LIMIT 1",
            (run_id,),
        ).fetchone()
        evidence_payload = json.loads(evidence_json)
        evidence_payload["outcome_id"] = "missing-outcome"
        connection.execute(
            "UPDATE research_facts SET outcome_id='missing-outcome',payload_json=? "
            "WHERE fact_id=?",
            (
                json.dumps(
                    evidence_payload,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                evidence_fact_id,
            ),
        )
        assessment_fact_id, assessment_json = connection.execute(
            "SELECT fact_id,payload_json FROM research_facts "
            "WHERE run_id=? AND fact_type='HypothesisAssessed' "
            "AND EXISTS(SELECT 1 FROM json_each(payload_json,'$.evidence_ids') "
            "WHERE value=?) LIMIT 1",
            (run_id, evidence_payload["evidence_id"]),
        ).fetchone()
        assessment_payload = json.loads(assessment_json)
        assessment_payload["evidence_ids"] = []
        connection.execute(
            "UPDATE research_facts SET payload_json=? WHERE fact_id=?",
            (
                json.dumps(
                    assessment_payload,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                assessment_fact_id,
            ),
        )
        verdict["verdict"] = "INCONCLUSIVE"
        connection.execute(
            "UPDATE research_facts SET payload_json=? "
            "WHERE run_id=? AND fact_type='GoalVerdictRecorded'",
            (
                json.dumps(
                    verdict,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                run_id,
            ),
        )
        connection.execute("DROP TRIGGER execution_attempt_update_rules")
        agent_attempt_id, observation_json = connection.execute(
            "SELECT attempt_id,observation_json FROM execution_attempts "
            "WHERE run_id=? AND kind='AGENT' "
            "AND json_extract(observation_json,'$.request.command')="
            "'DRAFT_EXPERIMENT_SPEC' ORDER BY started_at LIMIT 1",
            (run_id,),
        ).fetchone()
        observation = json.loads(observation_json)
        observation["request"]["role"] = "intruder"
        observation["request"]["budget"]["token_enforcement_mode"] = "observe"
        observation["response"]["typed_candidate"]["owner_role"] = "intruder"
        connection.execute(
            "UPDATE execution_attempts SET observation_json=? WHERE attempt_id=?",
            (_redigest_agent_observation(observation), agent_attempt_id),
        )
        schema_attempt_id, schema_observation_json = connection.execute(
            "SELECT attempt_id,observation_json FROM execution_attempts "
            "WHERE run_id=? AND kind='AGENT' AND terminal_reason='SUCCEEDED' "
            "AND json_extract(observation_json,'$.request.command')="
            "'PROPOSE_HYPOTHESIS' ORDER BY started_at LIMIT 1",
            (run_id,),
        ).fetchone()
        schema_observation = json.loads(schema_observation_json)
        schema_observation["request"]["schema_version"] = 5
        schema_observation["request"]["budget"].pop("token_enforcement_mode")
        schema_observation["response"]["schema_version"] = 5
        connection.execute(
            "UPDATE execution_attempts SET observation_json=? WHERE attempt_id=?",
            (
                _redigest_agent_observation(schema_observation),
                schema_attempt_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    tampered = Oracle(database, artifact_root).audit(run_id)
    tampered_codes = {issue.code for issue in tampered.issues}
    assert "component_fact_ref_mismatch" in tampered_codes
    assert "component_evidence_assessment_closure_mismatch" in tampered_codes
    assert "component_outcome_evidence_closure_mismatch" in tampered_codes
    assert "verdict_component_conjunction_mismatch" in tampered_codes
    assert "accepted_agent_fact_identity_mismatch" in tampered_codes
    assert "agent_request_schema_version_mismatch" in tampered_codes
    assert "agent_response_schema_version_mismatch" in tampered_codes
    assert "agent_token_enforcement_mode_mismatch" in tampered_codes


def test_oracle_imports_no_component_reducer_or_validator_authority() -> None:
    source = (_REPOSITORY / "hypoloom" / "oracle.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    forbidden = {
        "hypoloom.domain",
        "hypoloom.outcome_adapter",
        "hypoloom.policy",
        "hypoloom.research_kernel",
        "hypoloom.workflow_host",
        "domain",
        "outcome_adapter",
        "policy",
        "research_kernel",
        "workflow_host",
        "hypoloom",
    }

    assert imported.isdisjoint(forbidden)
    assert not any(name.startswith("hypoloom.") for name in imported)
    assert "component_execution_plan" not in source
    assert "expert_roles" not in source
    assert "experiment_budget" not in source
    assert "slot_ordinal" not in source
