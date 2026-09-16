from __future__ import annotations

import base64
import json
import sqlite3
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from hypoloom.agent_aci import EmbeddedInput, response_digest
from hypoloom.cli import main as cli_main
from hypoloom.debug_mode import (
    DebugModeError,
    build_debug_report,
    validate_attempt_payload,
    validate_debug_fixture,
    validate_debug_report,
    write_debug_fixture,
)
from hypoloom.diagnostics import bounded_diagnostic_text, diagnostic_summary
from hypoloom.domain import AgentCommand, AttemptTerminalReason, RunVersion
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.runner import ProcessObservation, ProcessRequest
from hypoloom.state_store import HostWriterCapability, StateStore
from hypoloom.workflow_host import WorkflowHost


def _version() -> RunVersion:
    return RunVersion(
        workflow_definition_version="workflow-debug-test",
        research_contract_version="research-debug-test",
        evaluator_version="evaluator-debug-test",
        agent_aci_version="9",
        runner_version="runner-debug-test",
        artifact_result_schema_version="2",
        runtime_environment_id="runtime-debug-test",
        configuration_id="configuration-debug-test",
        sqlite_runtime_version=sqlite3.sqlite_version,
    )


def _seed_agent_retry(
    state_root: Path,
    *,
    first_diagnostics: dict[str, object] | None = None,
) -> tuple[str, str, str]:
    run_id = "run-fake"
    first_id = "debug-attempt-1"
    second_id = "debug-attempt-2"
    base = replace(
        default_request(AgentCommand.GROUNDING),
        owner_workflow_id=run_id,
        attempt_id=first_id,
    )
    store = StateStore(state_root, HostWriterCapability.issue()).initialize()
    try:
        assert store.create_run(run_id, _version())
        with store.transaction() as tx:
            tx.record_command({
                "command_id": base.command_id,
                "run_id": run_id,
                "workflow_id": run_id,
                "expected_revision": 0,
                "command_type": "REQUEST_AUXILIARY",
                "request": {"task_context": base.task_context.to_dict()},
                "status": "accepted",
                "result": {"admitted": True},
                "revision": 1,
            })
            tx.append_transition({
                "workflow_id": run_id,
                "revision": 1,
                "run_id": run_id,
                "workflow_kind": "RUN",
                "previous_state": None,
                "state": "ACTIVE",
                "command_id": base.command_id,
                "payload": {"task_context": base.task_context.to_dict()},
            })
            tx.open_attempt({
                "attempt_id": first_id,
                "run_id": run_id,
                "workflow_id": run_id,
                "command_id": base.command_id,
                "kind": "AGENT",
                "attempt_ordinal": 1,
                "host_generation": "debug-host-1",
                "started_at": 1.0,
                "payload": {"request": base.to_dict()},
            })
            tx.close_attempt(
                first_id,
                terminal_reason="FAILED",
                ended_at=2.0,
                payload={
                    "failure_code": "literature_openalex_response_invalid",
                    "literature_diagnostic": "OpenAlex work omitted authors",
                    **(
                        {"diagnostics": first_diagnostics}
                        if first_diagnostics is not None
                        else {}
                    ),
                },
            )
            second = replace(base, attempt_id=second_id, attempt_ordinal=2)
            tx.open_attempt({
                "attempt_id": second_id,
                "run_id": run_id,
                "workflow_id": run_id,
                "command_id": base.command_id,
                "kind": "AGENT",
                "attempt_ordinal": 2,
                "host_generation": "debug-host-1",
                "started_at": 3.0,
                "payload": {"request": second.to_dict()},
            })
            response = FakeAgent().invoke(second)
            tx.close_attempt(
                second_id,
                terminal_reason="SUCCEEDED",
                ended_at=4.0,
                payload={"response": response.to_dict()},
            )
    finally:
        store.close()
    return run_id, first_id, second_id


def _attempt_census(state_root: Path) -> tuple[tuple[object, ...], ...]:
    with sqlite3.connect(
        f"file:{(state_root / 'state.sqlite').resolve().as_posix()}?mode=ro",
        uri=True,
    ) as connection:
        return tuple(connection.execute(
            "SELECT attempt_id,state,terminal_reason,observation_json "
            "FROM execution_attempts ORDER BY attempt_id"
        ))


def test_debug_report_localizes_recovered_module_without_writing_run(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    run_id, first_id, second_id = _seed_agent_retry(state_root)
    before = _attempt_census(state_root)

    report = build_debug_report(state_root, run_id)

    assert _attempt_census(state_root) == before
    assert report["non_authoritative"] is True
    assert report["summary"]["recovered_failure_attempt_ids"] == [first_id]
    assert report["summary"]["unrecovered_failure_attempt_ids"] == []
    attempts = {item["attempt_id"]: item for item in report["attempts"]}
    assert attempts[first_id]["module"] == "auxiliary/grounding"
    assert attempts[first_id]["diagnostic_summary"] == "OpenAlex work omitted authors"
    assert attempts[first_id]["recovered_by_later_attempt"] is True
    assert attempts[first_id]["duration_seconds"] == 1.0
    assert attempts[first_id]["accounting"] == {
        "source": "declared",
        "tokens": 512,
        "turns": 2,
        "cost_usd": "0",
        "currency": "USD",
    }
    assert attempts[first_id]["retry_of_attempt_id"] is None
    assert attempts[first_id]["retry_lineage_scope"] == "AGENT_COMMAND"
    assert attempts[first_id]["reconstructability"] == {
        "pure_contract_input": "AVAILABLE",
        "terminal_evidence": "BOUNDED_FAILURE_ONLY",
        "effectful_rehearsal": "MODULE_HANDLER_REQUIRED",
        "omissions": [
            "agent_response_not_recorded",
            "raw_literature_response_not_retained",
        ],
    }
    assert attempts[second_id]["has_response"] is True
    assert attempts[second_id]["has_rejected_response"] is False
    assert attempts[second_id]["retry_of_attempt_id"] == first_id
    assert attempts[second_id]["retry_trigger"] == {
        "terminal_reason": "FAILED",
        "failure_code": "literature_openalex_response_invalid",
        "failure_stage": "literature_retrieval",
    }
    assert attempts[second_id]["correction_inputs"] == {
        "refs": [], "count": 0, "truncated": False,
    }
    assert attempts[second_id]["accounting"] == {
        "source": "response",
        "tokens": 0,
        "turns": 1,
        "cost_usd": "0",
        "currency": "USD",
    }
    assert report["summary"]["retry_attempt_ids"] == [second_id]
    assert "observation" not in attempts[second_id]


def test_default_debug_projection_redacts_provider_diagnostic_credentials(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    run_id, first_id, _ = _seed_agent_retry(
        state_root,
        first_diagnostics={
            "provider_subtype": "Authorization: Bearer top-secret",
            "adapter_retry_policy": {
                "structured_output_failed_validation_cap": 1,
                "transport_max_retries": 0,
                "api_key": "top-secret",
            },
        },
    )

    report = build_debug_report(state_root, run_id)
    attempt = next(
        item for item in report["attempts"] if item["attempt_id"] == first_id
    )

    assert attempt["provider_subtype"] == (
        "[credential-shaped diagnostic redacted]"
    )
    assert attempt["adapter_retry_policy"] == {
        "structured_output_failed_validation_cap": 1,
        "transport_max_retries": 0,
    }
    assert "top-secret" not in json.dumps(report, sort_keys=True)


def test_debug_report_projects_evaluator_retry_from_existing_transition(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    run_id = "run-evaluator-retry"
    workflow_id = "experiment-evaluator-retry"
    command_id = "evaluator-retry-1"
    store = StateStore(state_root, HostWriterCapability.issue()).initialize()
    try:
        assert store.create_run(run_id, _version())
        with store.transaction() as tx:
            tx.record_command({
                "command_id": command_id,
                "run_id": run_id,
                "workflow_id": workflow_id,
                "expected_revision": 0,
                "command_type": "RETRY_EVALUATOR",
                "request": {"command": "RETRY_EVALUATOR"},
                "status": "accepted",
                "result": {"state": "EVALUATING"},
                "revision": 1,
            })
            tx.append_transition({
                "workflow_id": workflow_id,
                "revision": 1,
                "run_id": run_id,
                "workflow_kind": "EXPERIMENT",
                "previous_state": None,
                "state": "EVALUATING",
                "command_id": command_id,
                "next_command": "ACCEPT_OUTCOME",
                "due_at": 12.0,
                "payload": {
                    "source_attempt_id": "process-attempt-1",
                    "evaluator_retry_ordinal": 1,
                    "failure_stage": "artifact_publication",
                    "failure_class": "ArtifactDurabilityError",
                    "retry_after_seconds": 1.0,
                },
            })
    finally:
        store.close()

    report = build_debug_report(state_root, run_id)

    assert report["summary"]["evaluator_retry_count"] == 1
    event = report["evaluator_retries"][0]
    assert event == {
        "workflow_id": workflow_id,
        "revision": 1,
        "source_attempt_id": "process-attempt-1",
        "retry_ordinal": 1,
        "failure_stage": "artifact_publication",
        "failure_class": "ArtifactDurabilityError",
        "retry_after_seconds": 1.0,
        "due_at": 12.0,
        "created_at": event["created_at"],
    }


def test_debug_retry_lineage_uses_process_execution_across_commands(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    run_id = "run-process-lineage"
    workflow_id = "experiment-1"
    execution_id = "experiment-1--candidate-validation"
    store = StateStore(state_root, HostWriterCapability.issue()).initialize()
    try:
        assert store.create_run(run_id, _version())
        with store.transaction() as tx:
            for revision, command_id, attempt_id, source_id, terminal in (
                (1, "validation-command-1", "validation-attempt-1", "agent-source-1", "FAILED"),
                (2, "validation-command-2", "validation-attempt-2", "agent-source-2", "SUCCEEDED"),
            ):
                tx.record_command({
                    "command_id": command_id,
                    "run_id": run_id,
                    "workflow_id": workflow_id,
                    "expected_revision": revision - 1,
                    "command_type": "VALIDATE_SPEC_CANDIDATE",
                    "request": {"command": "VALIDATE_SPEC_CANDIDATE"},
                    "status": "accepted",
                    "result": {"state": "DRAFTED"},
                    "revision": revision,
                })
                request = ProcessRequest(
                    attempt_id=attempt_id,
                    attempt_ordinal=1,
                    execution_id=execution_id,
                    argv=("/usr/bin/true",),
                    cwd="/tmp",
                    env={},
                    timeout_seconds=5.0,
                )
                tx.open_attempt({
                    "attempt_id": attempt_id,
                    "run_id": run_id,
                    "workflow_id": workflow_id,
                    "command_id": command_id,
                    "kind": "PROCESS",
                    "experiment_id": workflow_id,
                    "execution_id": execution_id,
                    "attempt_ordinal": 1,
                    "host_generation": "debug-host-1",
                    "started_at": float(revision),
                })
                observation = ProcessObservation(
                    attempt_id=attempt_id,
                    attempt_ordinal=1,
                    execution_id=execution_id,
                    terminal_reason=AttemptTerminalReason(terminal),
                    returncode=0 if terminal == "SUCCEEDED" else 1,
                    pid=100 + revision,
                    process_group_id=100 + revision,
                    started_at=float(revision),
                    ended_at=float(revision) + 0.5,
                    stdout_path=None,
                    stderr_path=None,
                ).to_dict()
                tx.close_attempt(
                    attempt_id,
                    terminal_reason=terminal,
                    ended_at=float(revision) + 0.5,
                    process_group_id=100 + revision,
                    payload={**observation, "process_request": request.to_dict()},
                )
                tx.append_transition({
                    "workflow_id": workflow_id,
                    "revision": revision,
                    "run_id": run_id,
                    "workflow_kind": "EXPERIMENT",
                    "previous_state": None if revision == 1 else "DRAFTED",
                    "state": "DRAFTED",
                    "command_id": command_id,
                    "payload": {
                        "attempt_id": attempt_id,
                        "agent_attempt_id": source_id,
                    },
                })
    finally:
        store.close()

    report = build_debug_report(state_root, run_id)
    attempts = {item["attempt_id"]: item for item in report["attempts"]}
    assert attempts["validation-attempt-1"]["recovered_by_later_attempt"] is True
    assert attempts["validation-attempt-2"]["retry_of_attempt_id"] == (
        "validation-attempt-1"
    )
    assert attempts["validation-attempt-2"]["retry_lineage_scope"] == (
        "PROCESS_EXECUTION"
    )
    assert attempts["validation-attempt-2"]["source_attempt_id"] == "agent-source-2"
    assert attempts["validation-attempt-2"]["failure_stage"] is None


def test_debug_retry_projects_rejected_candidate_delta_without_payload_leak(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    run_id = "run-rejected-candidate"
    first_id = "candidate-attempt-1"
    second_id = "candidate-attempt-2"
    base = replace(
        default_request(AgentCommand.GROUNDING),
        owner_workflow_id=run_id,
        attempt_id=first_id,
    )
    store = StateStore(state_root, HostWriterCapability.issue()).initialize()
    try:
        assert store.create_run(run_id, _version())
        with store.transaction() as tx:
            tx.record_command({
                "command_id": base.command_id,
                "run_id": run_id,
                "workflow_id": run_id,
                "expected_revision": 0,
                "command_type": "REQUEST_AUXILIARY",
                "request": {"task_context": base.task_context.to_dict()},
                "status": "accepted",
                "result": {"admitted": True},
                "revision": 1,
            })
            tx.open_attempt({
                "attempt_id": first_id,
                "run_id": run_id,
                "workflow_id": run_id,
                "command_id": base.command_id,
                "kind": "AGENT",
                "attempt_ordinal": 1,
                "host_generation": "debug-host-1",
                "started_at": 1.0,
                "payload": {"request": base.to_dict()},
            })
            original = FakeAgent().invoke(base)
            rejected_candidate = dict(original.typed_candidate or {})
            rejected_candidate["summary"] = "host-rejected summary"
            rejected_response = replace(
                original,
                typed_candidate=rejected_candidate,
                output_digest="",
            )
            rejected_response = replace(
                rejected_response,
                output_digest=response_digest(rejected_response),
            )
            tx.close_attempt(
                first_id,
                terminal_reason="FAILED",
                ended_at=2.0,
                payload={
                    "failure_code": "invalid_candidate",
                    "failure_stage": "host_candidate_validation",
                    "rejected_response": rejected_response.to_dict(),
                },
            )
            correction = EmbeddedInput.from_bytes(
                f"{base.command_id}--candidate-validation-diagnostic-1",
                "application/json",
                b"{}",
            )
            second = replace(
                base,
                attempt_id=second_id,
                attempt_ordinal=2,
                immutable_context_refs=(correction.ref,),
                input_artifacts=(correction,),
            )
            tx.open_attempt({
                "attempt_id": second_id,
                "run_id": run_id,
                "workflow_id": run_id,
                "command_id": base.command_id,
                "kind": "AGENT",
                "attempt_ordinal": 2,
                "host_generation": "debug-host-1",
                "started_at": 3.0,
                "payload": {"request": second.to_dict()},
            })
            tx.close_attempt(
                second_id,
                terminal_reason="SUCCEEDED",
                ended_at=4.0,
                payload={"response": FakeAgent().invoke(second).to_dict()},
            )
    finally:
        store.close()

    report = build_debug_report(state_root, run_id)
    attempts = {item["attempt_id"]: item for item in report["attempts"]}
    first = attempts[first_id]
    second = attempts[second_id]
    assert first["has_rejected_response"] is True
    assert first["accounting"] == {
        "source": "rejected_response",
        "tokens": 0,
        "turns": 1,
        "cost_usd": "0",
        "currency": "USD",
    }
    assert first["reconstructability"]["terminal_evidence"] == (
        "EXACT_RECORDED_REJECTED_OUTPUT"
    )
    assert second["correction_inputs"] == {
        "refs": [correction.ref],
        "count": 1,
        "truncated": False,
    }
    assert second["candidate_delta"] == {
        "from_attempt_id": first_id,
        "changed_field_paths": ["/summary", "/supporting_refs/0"],
        "changed_field_count_lower_bound": 2,
        "truncated": False,
    }
    assert "observation" not in first


def test_exact_attempt_fixture_is_mode_0600_and_purely_validates_offline(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    run_id, _, attempt_id = _seed_agent_retry(state_root)
    report = build_debug_report(
        state_root, run_id, attempt_id=attempt_id, include_payloads=True,
    )
    assert validate_attempt_payload(report["attempts"][0]) == {
        "valid": True,
        "checks": ["agent_request", "agent_response", "agent_postflight"],
        "errors": [],
    }

    fixture = write_debug_fixture(tmp_path / "debug-fixture.json", report)
    assert fixture.stat().st_mode & 0o777 == 0o600
    validation = validate_debug_fixture(fixture, validation_mode="candidate")
    assert validation["valid"] is True
    assert validation["validation_mode"] == "candidate"
    assert validation["source_exact"] is False
    source_validation = validate_debug_fixture(
        fixture, validation_mode="source-exact",
    )
    assert source_validation["valid"] is False
    assert source_validation["source_exact_status"] == "NOT_RECONSTRUCTABLE"
    assert "not reconstructable" in source_validation["errors"][0]
    with pytest.raises(DebugModeError, match="cannot create"):
        write_debug_fixture(fixture, report)


def test_debug_cli_inspect_export_and_validate_are_read_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_root = tmp_path / "state"
    run_id, first_id, second_id = _seed_agent_retry(state_root)
    before = _attempt_census(state_root)

    assert cli_main([
        "debug", "inspect", "--state-root", str(state_root),
        "--run-id", run_id, "--attempt-id", first_id,
    ]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["attempts"][0]["failure_code"] == (
        "literature_openalex_response_invalid"
    )

    fixture = tmp_path / "fixture.json"
    assert cli_main([
        "debug", "export", "--state-root", str(state_root),
        "--run-id", run_id, "--attempt-id", second_id,
        "--output", str(fixture),
    ]) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["non_authoritative"] is True
    assert cli_main([
        "debug", "validate", "--fixture", str(fixture), "--candidate",
    ]) == 0
    validated = json.loads(capsys.readouterr().out)
    assert validated["valid"] is True
    assert _attempt_census(state_root) == before


def test_source_exact_fails_closed_without_real_host_launch_inputs(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    run_id, _, attempt_id = _seed_agent_retry(state_root)
    report = build_debug_report(
        state_root, run_id, attempt_id=attempt_id, include_payloads=True,
    )
    host = WorkflowHost(tmp_path / "uninitialized-host")
    try:
        report["run_versions"] = asdict(host.version)
    finally:
        host.close()

    result = validate_debug_report(report, validation_mode="source-exact")

    assert result["valid"] is False
    assert result["source_exact_status"] == "NOT_RECONSTRUCTABLE"


def test_debug_validation_rejects_tampered_exact_response(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    run_id, _, attempt_id = _seed_agent_retry(state_root)
    report = build_debug_report(
        state_root, run_id, attempt_id=attempt_id, include_payloads=True,
    )
    attempt = report["attempts"][0]
    attempt["observation"]["response"]["command_id"] = "wrong-command"

    result = validate_attempt_payload(attempt)

    assert result["valid"] is False
    assert result["checks"] == ["agent_request"]
    assert "wrong_invocation_identity" in result["errors"][0]

    report = build_debug_report(
        state_root, run_id, attempt_id=attempt_id, include_payloads=True,
    )
    report["attempts"][0]["terminal_reason"] = "FAILED"
    result = validate_attempt_payload(report["attempts"][0])
    assert result["valid"] is False
    assert "response requires a SUCCEEDED Attempt" in result["errors"][0]


def test_debug_validation_reuses_process_request_and_observation_parsers() -> None:
    request = ProcessRequest(
        attempt_id="process-attempt-1",
        attempt_ordinal=1,
        execution_id="execution-1",
        argv=("/usr/bin/true",),
        cwd="/tmp",
        env={"CUDA_VISIBLE_DEVICES": "0"},
        timeout_seconds=10.0,
    )
    observation = ProcessObservation(
        attempt_id=request.attempt_id,
        attempt_ordinal=request.attempt_ordinal,
        execution_id=request.execution_id,
        terminal_reason=AttemptTerminalReason.SUCCEEDED,
        returncode=0,
        pid=123,
        process_group_id=123,
        started_at=1.0,
        ended_at=2.0,
        stdout_path=None,
        stderr_path=None,
    ).to_dict()

    result = validate_attempt_payload({
        "attempt_id": request.attempt_id,
        "attempt_ordinal": request.attempt_ordinal,
        "execution_id": request.execution_id,
        "kind": "PROCESS",
        "terminal_reason": "SUCCEEDED",
        "observation": {**observation, "process_request": request.to_dict()},
    })

    assert result == {
        "valid": True,
        "checks": ["process_request", "process_observation"],
        "errors": [],
    }

    incomplete = {**observation, "process_request": request.to_dict()}
    incomplete.pop("returncode")
    rejected = validate_attempt_payload({
        "attempt_id": request.attempt_id,
        "attempt_ordinal": request.attempt_ordinal,
        "execution_id": request.execution_id,
        "kind": "PROCESS",
        "terminal_reason": "SUCCEEDED",
        "observation": incomplete,
    })
    assert rejected["valid"] is False
    assert "incomplete" in rejected["errors"][0]

    mismatched = {**observation, "process_request": request.to_dict()}
    mismatched["attempt_ordinal"] = 99
    rejected = validate_attempt_payload({
        "attempt_id": request.attempt_id,
        "attempt_ordinal": request.attempt_ordinal,
        "execution_id": request.execution_id,
        "kind": "PROCESS",
        "terminal_reason": "SUCCEEDED",
        "observation": mismatched,
    })
    assert rejected["valid"] is False
    assert "observation and request differ" in rejected["errors"][0]

    synthetic = {
        "attempt_id": request.attempt_id,
        "attempt_ordinal": request.attempt_ordinal,
        "execution_id": request.execution_id,
        "terminal_reason": "FAILED",
        "process_request": request.to_dict(),
        "synthetic_before_spawn": True,
    }
    accepted = validate_attempt_payload({
        "attempt_id": request.attempt_id,
        "attempt_ordinal": request.attempt_ordinal,
        "execution_id": request.execution_id,
        "kind": "PROCESS",
        "terminal_reason": "FAILED",
        "observation": synthetic,
    })
    assert accepted["valid"] is True
    synthetic["terminal_reason"] = "SUCCEEDED"
    rejected = validate_attempt_payload({
        "attempt_id": request.attempt_id,
        "attempt_ordinal": request.attempt_ordinal,
        "execution_id": request.execution_id,
        "kind": "PROCESS",
        "terminal_reason": "SUCCEEDED",
        "observation": synthetic,
    })
    assert rejected["valid"] is False
    assert "synthetic Process observation identity differs" in rejected["errors"][0]

    impossible = {**observation, "process_request": request.to_dict()}
    impossible["returncode"] = 17
    rejected = validate_attempt_payload({
        "attempt_id": request.attempt_id,
        "attempt_ordinal": request.attempt_ordinal,
        "execution_id": request.execution_id,
        "kind": "PROCESS",
        "terminal_reason": "SUCCEEDED",
        "observation": impossible,
    })
    assert rejected["valid"] is False
    assert "requires returncode 0" in rejected["errors"][0]


def test_exact_export_refuses_source_root_and_credential_shaped_fields(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    run_id, _, attempt_id = _seed_agent_retry(state_root)
    report = build_debug_report(
        state_root, run_id, attempt_id=attempt_id, include_payloads=True,
    )
    with pytest.raises(DebugModeError, match="outside the source state root"):
        write_debug_fixture(state_root / "debug.json", report)

    symlink = tmp_path / "requested.json"
    symlink_target = tmp_path / "actual-new.json"
    symlink.symlink_to(symlink_target)
    with pytest.raises(DebugModeError, match="must not be a symlink"):
        write_debug_fixture(symlink, report)
    assert not symlink_target.exists()

    for field in (
        {"HF_TOKEN": "must-never-export"},
        {"AWS_SECRET_ACCESS_KEY": "must-never-export"},
        {"openalex_api_key_file": "/private/key-file"},
        {
            "media_type": "application/json",
            "content_base64": base64.b64encode(json.dumps({
                "access_token": "must-never-export",
            }).encode()).decode(),
        },
        {
            "path": "experiment.py",
            "content_base64": base64.b64encode(
                b"API_KEY = 'must-never-export'\n",
            ).decode(),
        },
        json.dumps({"HF_TOKEN": "must-never-export"}),
        {"message": "Authorization: Bearer must-never-export"},
        json.dumps({"message": "Authorization: Bearer must-never-export"}),
    ):
        report["related_records"]["provider"] = field
        with pytest.raises(DebugModeError, match="credential-shaped"):
            write_debug_fixture(tmp_path / "credential.json", report)


def test_bounded_diagnostics_fail_closed_on_credential_shaped_text() -> None:
    assert bounded_diagnostic_text(
        "GPU inventory failed: Authorization: Bearer top-secret",
    ) == "[credential-shaped diagnostic redacted]"
    assert bounded_diagnostic_text(
        json.dumps({"api_key": "top-secret"}),
    ) == "[credential-shaped diagnostic redacted]"
    safe_json = json.dumps({
        "max_tokens": 20_000,
        "openalex_api_key_file": "/config/openalex.key",
    })
    assert bounded_diagnostic_text(safe_json) == safe_json
    assert bounded_diagnostic_text("OpenAlex returned HTTP 429") == (
        "OpenAlex returned HTTP 429"
    )
    assert diagnostic_summary({
        "resource_diagnostic": "Authorization: Bearer top-secret",
    }) == "[credential-shaped diagnostic redacted]"
    assert diagnostic_summary({
        "diagnostics": {
            "provider_subtype": "error_max_structured_output_retries",
        },
    }) == "error_max_structured_output_retries"
    assert diagnostic_summary({
        "diagnostics": {"workspace_error": "missing experiment.py"},
    }) == "missing experiment.py"
    assert diagnostic_summary({
        "diagnostics": {"provider_subtype": "success"},
    }) is None
