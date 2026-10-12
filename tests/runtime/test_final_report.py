"""The final narrative is an actual bounded file, never a new scientific fact."""
from dataclasses import replace
import json

import pytest

from hypoloom.agent_aci import EmbeddedInput, WorkspaceArtifact, validate_postflight, with_output_digest
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import FakeAgent, default_request


def report_request():
    facts = [
        {"fact_id": "verdict-fact", "run_id": "run-fake", "fact_type": "GoalVerdictRecorded", "payload_json": json.dumps({
            "fact_id": "verdict-fact", "run_id": "run-fake", "fact_type": "GoalVerdictRecorded", "verdict": "GOAL_MET"})},
        {"fact_id": "outcome-fact", "run_id": "run-fake", "fact_type": "CanonicalOutcomeAccepted", "payload_json": json.dumps({
            "fact_id": "outcome-fact", "run_id": "run-fake", "fact_type": "CanonicalOutcomeAccepted", "outcome_id": "outcome-accepted", "outcome": "SUCCESS"})},
    ]
    context = EmbeddedInput.from_bytes("final--final-report-context", "application/json",
        json.dumps({"facts": facts, "stop_reason": "The accepted goal verdict closed research."}).encode())
    request = default_request(AgentCommand.FINAL_REPORT)
    return replace(request, input_artifacts=(context,), immutable_context_refs=(context.ref,))


def test_final_report_has_its_own_run_scoped_auxiliary_and_fake_file():
    request = report_request()
    assert request.schema_version == 14
    assert request.task_context.component_id is request.task_context.expert_slot is request.task_context.cycle is None
    validated = validate_postflight(request, FakeAgent().invoke(request))
    assert set(validated.workspace_files) == {"FINAL_REPORT.md"}
    assert b"outcome-accepted" in validated.workspace_files["FINAL_REPORT.md"]
    assert b"verdict-fact" in validated.workspace_files["FINAL_REPORT.md"]


@pytest.mark.parametrize("contents", [b"", b"# Unsupported report\n", b"verdict-fact GOAL_MET\n", b"verdict-fact GOAL_NOT_MET outcome-accepted\n", b"\xff"])
def test_final_report_rejects_missing_verdict_outcome_and_invalid_text(contents):
    request = report_request(); response = FakeAgent().invoke(request)
    response = with_output_digest(replace(response, proposed_workspace_artifacts=(WorkspaceArtifact.from_bytes("FINAL_REPORT.md", contents),)))
    with pytest.raises(ValueError): validate_postflight(request, response)


def test_final_report_error_identifies_the_exact_missing_references():
    from hypoloom.final_report import validate_final_report
    request = report_request()
    with pytest.raises(ValueError) as failure:
        validate_final_report({"FINAL_REPORT.md": b"# Report\n"}, request)
    assert 'missing_reference_ids=["verdict-fact", "outcome-accepted"]' in str(failure.value)
    validate_final_report({"FINAL_REPORT.md": b"verdict-fact GOAL_MET outcome-accepted"}, request)


def test_final_report_captures_and_archives_only_its_private_report_directory(tmp_path):
    from test_agent_sessions import _manager
    from hypoloom.full_cc_runtime import full_cc_argv
    from pathlib import Path
    manager = _manager(tmp_path); request = report_request()
    prepared = manager.prepare(request, None)
    try:
        assert not prepared.writable
        contents = b"# Final report\n\nverdict-fact: GOAL_MET. outcome-accepted: SUCCESS.\n"
        (prepared.final_report_root / "FINAL_REPORT.md").write_bytes(contents)
        captured = prepared.capture_workspace()
        assert [WorkspaceArtifact.from_dict(item).content_bytes() for item in captured] == [contents]
        argv = full_cc_argv(bwrap=Path("/usr/bin/bwrap"), executable=tmp_path / "claude", request=request,
            model="test-model", workspace=prepared, provider_socket=tmp_path / "provider.sock", host_socket=None,
            output_schema={}, extra_paths=())
        assert ("--bind", str(prepared.final_report_root), "/report") in set(zip(argv, argv[1:], argv[2:]))
        transcript = prepared.session_root / f"{prepared.binding['session_id']}.jsonl"
        transcript.write_text(json.dumps({"type": "user", "sessionId": prepared.binding["session_id"], "message": "Write the actual report."}) + "\n")
        metadata = prepared.finish_node()
        assert (manager.publisher.root / metadata["transcript"]["path"] / "FINAL_REPORT.md").read_bytes() == contents
        (prepared.final_report_root / "unrequested.txt").write_text("not accepted")
        from hypoloom.git_authoring_workspace import GitAuthoringIntegrityError
        with pytest.raises(GitAuthoringIntegrityError, match="report"): prepared.capture_workspace()
    finally: prepared.cleanup()


def test_human_view_includes_only_accepted_bound_final_report(tmp_path):
    from hypoloom.research_export import research_view_files, ResearchExportError
    from hypoloom.monitor import build_monitor_snapshot
    from test_monitor import _create_fresh_run
    request = report_request(); response = FakeAgent().invoke(request)
    root = tmp_path / "state"; _create_fresh_run(root, request.run_id)
    snapshot = build_monitor_snapshot(root, request.run_id)
    row = {"run_id": request.run_id, "kind": "AGENT", "state": "CLOSED", "terminal_reason": "SUCCEEDED",
        "attempt_id": request.attempt_id, "workflow_id": request.owner_workflow_id,
        "command_id": request.command_id, "attempt_ordinal": request.attempt_ordinal,
        "observation_json": json.dumps({"request": request.to_dict(), "response": response.to_dict()})}
    files = research_view_files(snapshot, [row])
    assert files["FINAL_REPORT.md"] == response.proposed_workspace_artifacts[0].content_bytes()
    assert "FINAL_REPORT.md" not in research_view_files(snapshot, [{**row, "terminal_reason": "FAILED"}])
    with pytest.raises(ResearchExportError): research_view_files(snapshot, [{**row, "attempt_id": "foreign"}])


def test_report_api_view_retains_frozen_as_of_sources_and_plain_markdown():
    from hypoloom.final_report import accepted_report_view
    request = report_request(); response = FakeAgent().invoke(request)
    row = {"run_id": request.run_id, "kind": "AGENT", "state": "CLOSED", "terminal_reason": "SUCCEEDED",
        "attempt_id": request.attempt_id, "workflow_id": request.owner_workflow_id,
        "command_id": request.command_id, "attempt_ordinal": request.attempt_ordinal,
        "observation_json": json.dumps({"request": request.to_dict(), "response": response.to_dict()})}
    view = accepted_report_view(request.run_id, [row])
    assert view["attempt_id"] == request.attempt_id
    assert view["markdown"].encode() == response.proposed_workspace_artifacts[0].content_bytes()
    assert view["verdict"] == "GOAL_MET" and view["verdict_ref"] == "verdict-fact"
    assert view["outcome_refs"] == ["outcome-accepted"]
    assert view["stop_reason"] == "The accepted goal verdict closed research."
    assert accepted_report_view(request.run_id, [{**row, "state": "OPEN"}]) is None
    with pytest.raises(ValueError): accepted_report_view(request.run_id, [{**row, "attempt_id": "foreign"}])
    with pytest.raises(ValueError): accepted_report_view(request.run_id, [row, row])


def test_report_counts_research_nodes_once_and_accepts_contextless_reviews():
    from hypoloom.final_report import run_outcome_context
    attempts = [{"attempt_id": str(index), "kind": "AGENT", "command_id": command,
        "observation_json": json.dumps({"request": {"command_id": command, "task_context": context}})}
        for index, (command, context) in enumerate([
            ("research", {"key": {}}), ("research", {"key": {}}),
            ("review", None), ("grounding", {"kind": "AUXILIARY"})])]
    result = run_outcome_context([], attempts, [])
    assert result["expert_nodes_started"] == 1
    assert result["process_attempts"] == 0
    assert result["process_attempt_details"] == []


def test_report_preserves_recovered_failures_and_requires_their_citations():
    from hypoloom.final_report import run_outcome_context, validate_final_report
    attempts = [{"attempt_id": identity, "command_id": "same-command", "kind": "AGENT",
        "state": "CLOSED", "terminal_reason": terminal,
        "observation_json": json.dumps({"request": {"command": "GROUNDING"},
            **({"failure_code": "timed_out"} if terminal == "TIMED_OUT" else {})})}
        for identity, terminal in (("failed-call", "TIMED_OUT"), ("recovered-call", "SUCCEEDED"))]
    outcome = run_outcome_context([], attempts, [])
    assert outcome["final_failures"] == []
    assert outcome["agent_attempts_by_status"] == {"TIMED_OUT": 1, "SUCCEEDED": 1}
    assert outcome["unsuccessful_agent_attempts"][0]["command_has_successful_attempt"]
    request = report_request()
    payload = json.loads(request.input_artifacts[0].content_bytes())
    payload["run_outcome"] = outcome
    context = EmbeddedInput.from_bytes(request.input_artifacts[0].ref, "application/json", json.dumps(payload).encode())
    request = replace(request, input_artifacts=(context,))
    with pytest.raises(ValueError, match="failed-call"):
        validate_final_report({"FINAL_REPORT.md": b"verdict-fact GOAL_MET outcome-accepted"}, request)
    validate_postflight(request, FakeAgent().invoke(request))


def test_declared_raw_report_inputs_preserve_scope_and_omissions(tmp_path):
    from hypoloom.final_report import declared_measurement_inputs
    raw = tmp_path / "metrics.json"
    raw.write_text('{"validation": 0.7, "test": 0.6}')
    oversized = tmp_path / "large.json"
    oversized.write_bytes(b" " * 100)
    facts = [{"fact_type": "CanonicalOutcomeAccepted", "payload": {
        "outcome_id": "outcome-local", "artifact_id": "artifact-local", "outcome": "EXECUTION_FAILURE"}}]
    def resolve(outcome, artifact):
        assert (outcome, artifact) == ("outcome-local", "artifact-local")
        return (("raw/metrics.json", raw), ("raw/large.json", oversized),
            ("raw/checkpoint.bin", tmp_path / "checkpoint.bin"), ("raw/missing.csv", tmp_path / "missing.csv"))
    inputs, index = declared_measurement_inputs(facts, resolve, max_bytes=40)
    assert len(inputs) == 1 and inputs[0].content_bytes() == raw.read_bytes()
    assert index["included"][0]["canonical_outcome"] == "EXECUTION_FAILURE"
    assert index["included_by_outcome"] == {"EXECUTION_FAILURE": 1}
    assert index["omitted_by_outcome"] == {"EXECUTION_FAILURE": 3}
    assert {item["reason"] for item in index["omitted"]} == {
        "raw_byte_budget_exceeded", "non_text_artifact", "source_unavailable_or_not_utf8"}


def test_accepted_evaluation_summaries_are_comparable_and_bounded(tmp_path):
    from hypoloom.final_report import accepted_evaluation_summary_inputs

    first = tmp_path / "first.json"
    first.write_bytes(b'{"cells":["qwen","llama"]}')
    second = tmp_path / "second.json"
    second.write_bytes(b'{"cells":["too-large"]}')
    calls = []

    def resolve(outcome_id, artifact_id, logical_id):
        calls.append((outcome_id, artifact_id, logical_id))
        return (first if outcome_id == "outcome-one" else second,)

    def outcome(identity, status):
        return {"fact_type": "CanonicalOutcomeAccepted", "payload": {
            "outcome_id": identity,
            "artifact_id": f"artifact-{identity}",
            "outcome": status,
            "logical_artifacts": [{"logical_artifact": "evaluation_summary",
                "paths": ["evaluation_summary.json"]}],
        }}

    inputs, index = accepted_evaluation_summary_inputs([
        outcome("outcome-one", "SUCCESS"),
        outcome("outcome-rejected", "INTEGRITY_REJECTED"),
        outcome("outcome-two", "CLEAN_REFUTATION"),
    ], resolve, max_bytes=len(first.read_bytes()))

    assert [item.ref for item in inputs] == [
        "outcome-one--accepted-evaluation-summary"
    ]
    assert inputs[0].content_bytes() == first.read_bytes()
    assert calls == [
        ("outcome-one", "artifact-outcome-one", "evaluation_summary"),
        ("outcome-two", "artifact-outcome-two", "evaluation_summary"),
    ]
    assert index == {
        "byte_limit": len(first.read_bytes()),
        "bytes_included": len(first.read_bytes()),
        "included": [{"outcome_id": "outcome-one",
            "artifact_id": "artifact-outcome-one",
            "refs": ["outcome-one--accepted-evaluation-summary"]}],
        "omitted": [{"outcome_id": "outcome-two",
            "artifact_id": "artifact-outcome-two",
            "ref": "outcome-two--accepted-evaluation-summary",
            "reason": "summary_byte_budget_exceeded"}],
    }


def _report_gpu_usage(uuid="GPU-11111111-1111-1111-1111-111111111111", *, free=50_901,
                      owned_bytes=4096, incomplete=False):
    from hypoloom.compute_resources import GpuPressureTracker, GpuRuntimeObservation
    from hypoloom.gpu_attribution import OwnedMemoryAccumulator

    tracker = GpuPressureTracker(initial_observation=GpuRuntimeObservation(
        uuid=uuid, total_memory_mib=64_000, free_memory_mib=free,
        used_memory_mib=64_000 - free, utilization_percent=0),
        reserve_memory_mib=1000, grace_seconds=30)
    usage = tracker.summary.to_dict()
    owned = OwnedMemoryAccumulator()
    owned.sample({"gpu_uuid": uuid, "root_process_group_id": 100, "complete": True,
        "resident_bytes": owned_bytes, "reason": None})
    if incomplete:
        owned.sample({"gpu_uuid": uuid, "root_process_group_id": 100, "complete": False,
            "resident_bytes": None, "reason": "process_unavailable"})
    usage["owned_memory"] = owned.summary
    return usage


def test_report_context_distinguishes_rejected_gpu_execution_from_accepted_science():
    from hypoloom.final_report import run_outcome_context

    outcome = {"fact_id": "outcome-fact", "fact_type": "CanonicalOutcomeAccepted",
        "payload": {"execution_id": "execution-one", "outcome_id": "outcome-one", "artifact_id": "artifact-one",
            "outcome": "INTEGRITY_REJECTED", "validation_code": "artifact_inventory_mismatch",
            "metric_value": None}}
    usage = _report_gpu_usage()
    process = {"attempt_id": "process-one", "kind": "PROCESS", "state": "CLOSED",
        "execution_id": "execution-one", "terminal_reason": "SUCCEEDED",
        "observation_json": json.dumps({"execution_id": "execution-one", "returncode": 0,
            "resource_usage": usage})}

    result = run_outcome_context([outcome], [process], [], artifacts=[{
        "artifact_id": "artifact-one", "execution_id": "execution-one", "attempt_id": "process-one"}])

    assert result["process_attempts"] == 1
    assert result["process_attempt_details"] == [{
        "attempt_id": "process-one",
        "execution_id": "execution-one",
        "terminal_reason": "SUCCEEDED",
        "returncode": 0,
        "completed_process": True,
        "synthetic_before_spawn": False,
        "gpu_device_monitored": True,
        "evidence_scope": "raw_diagnostic_only",
        "resource_usage": {"gpu_uuid": usage["gpu_uuid"], "sample_count": 1,
            "gpu_initial_free_memory_mib": 50_901, "gpu_latest_free_memory_mib": 50_901,
            "gpu_minimum_free_memory_mib": 50_901, "pressure_exceeded": False,
            "pressure_attribution": None,
            "owned_memory": usage["owned_memory"]},
        "canonical_outcome": {"outcome_id": "outcome-one",
            "outcome": "INTEGRITY_REJECTED", "validation_code": "artifact_inventory_mismatch",
            "metric_value": None},
    }]


def test_report_counts_cancelled_unlaunched_work_separately_from_failures():
    from hypoloom.final_report import run_outcome_context
    attempts = [{"attempt_id": name, "kind": "PROCESS", "state": "CLOSED",
        "terminal_reason": terminal, "observation_json": json.dumps({
            "returncode": code, "synthetic_before_spawn": synthetic})}
        for name, terminal, code, synthetic in (
            ("measured", "SUCCEEDED", 0, False), ("error", "FAILED", 70, False),
            ("pressure", "FAILED", -15, False), ("stopped", "CANCELLED", -15, False),
            ("queued", "CANCELLED", None, True))]
    attempts.append({"attempt_id": "api", "command_id": "design", "kind": "AGENT",
        "state": "CLOSED", "terminal_reason": "FAILED", "observation_json": json.dumps({
            "request": {"command": "EXPERIMENT_DESIGN_IMPLEMENT"},
            "diagnostics": {"provider_subtype": "error", "provider_diagnostic": "response disconnected"}})})
    result = run_outcome_context([], attempts, [])
    assert result['process_attempts_by_status'] == {'SUCCEEDED': 1, 'FAILED': 2, 'CANCELLED': 2}
    assert result['process_attempts_by_returncode'] == {'0': 1, '70': 1, '-15': 2, 'None': 1}
    assert result['process_attempts_by_evidence_scope'] == {'raw_diagnostic_only': 1, 'no_completed_process': 4}
    assert result['processes_completed'] == result['processes_cancelled_before_spawn'] == 1
    assert result['unsuccessful_agent_attempts'][0]['provider_subtype'] == 'error'
    assert result['unsuccessful_agent_attempts'][0]['provider_diagnostic'] == 'response disconnected'


def test_report_context_recognizes_multi_gpu_process_telemetry():
    from hypoloom.final_report import run_outcome_context

    usage = [_report_gpu_usage(free=40_000, owned_bytes=0),
        _report_gpu_usage("GPU-22222222-2222-2222-2222-222222222222", free=39_000, incomplete=True)]
    process = {"attempt_id": "process-two", "kind": "PROCESS", "state": "CLOSED",
        "execution_id": "execution-two", "terminal_reason": "SUCCEEDED",
        "observation_json": json.dumps({"execution_id": "execution-two", "returncode": 0,
            "resource_usage": usage})}

    detail = run_outcome_context([], [process], [])["process_attempt_details"][0]

    assert detail["completed_process"] is True
    assert detail["gpu_device_monitored"] is True
    assert "gpu_observed" not in detail
    assert detail["evidence_scope"] == "raw_diagnostic_only"
    assert [item["gpu_minimum_free_memory_mib"] for item in detail["resource_usage"]] == [40_000, 39_000]
    assert detail["resource_usage"][0]["owned_memory"] == usage[0]["owned_memory"]
    assert detail["resource_usage"][1]["owned_memory"] == usage[1]["owned_memory"]
    assert detail["resource_usage"][0]["owned_memory"]["peak"]["resident_bytes"] == 0
    assert detail["resource_usage"][1]["owned_memory"]["peak"]["resident_bytes"] == 4096
    assert detail["resource_usage"][1]["owned_memory"]["latest"]["resident_bytes"] is None


def test_report_device_monitoring_does_not_invent_task_gpu_usage():
    from hypoloom.final_report import run_outcome_context

    usage = _report_gpu_usage(owned_bytes=0)
    del usage["owned_memory"]
    process = {"attempt_id": "process-one", "kind": "PROCESS", "state": "CLOSED",
        "execution_id": "execution-one", "terminal_reason": "SUCCEEDED",
        "observation_json": json.dumps({"returncode": 0, "resource_usage": usage})}
    detail = run_outcome_context([], [process], [])["process_attempt_details"][0]
    assert detail["gpu_device_monitored"] is True
    assert "gpu_observed" not in detail
    assert detail["resource_usage"]["owned_memory"] is None
    assert detail["resource_usage"]["gpu_minimum_free_memory_mib"] == 50_901


@pytest.mark.parametrize("outcome_kind", ["SUCCESS", "CLEAN_REFUTATION"])
def test_report_evidence_belongs_to_published_attempt_not_every_execution_retry(outcome_kind):
    from hypoloom.final_report import run_outcome_context

    attempts = [{"attempt_id": identity, "kind": "PROCESS", "state": "CLOSED",
        "execution_id": "execution-one", "terminal_reason": reason,
        "observation_json": json.dumps({"returncode": code})}
        for identity, reason, code in [("lost", "LOST", None), ("published", "SUCCEEDED", 0)]]
    facts = [{"fact_type": "CanonicalOutcomeAccepted", "payload": {
        "execution_id": "execution-one", "artifact_id": "artifact-one",
        "outcome_id": "outcome-one", "outcome": outcome_kind, "metric_value": "0.55"}}]
    artifacts = [{"artifact_id": "artifact-one", "execution_id": "execution-one", "attempt_id": "published"}]

    details = run_outcome_context(facts, attempts, [], artifacts=artifacts)["process_attempt_details"]
    assert details[0]["evidence_scope"] == "no_completed_process"
    assert "canonical_outcome" not in details[0]
    assert details[1]["evidence_scope"] == "accepted_scientific_evidence"
    assert details[1]["canonical_outcome"]["outcome"] == outcome_kind
    # Without the publication relation, an execution-level fact cannot prove ownership.
    unbound = run_outcome_context(facts, attempts, [])["process_attempt_details"]
    assert unbound[1]["evidence_scope"] == "raw_diagnostic_only"
    assert "canonical_outcome" not in unbound[1]


def test_report_context_uses_canonical_agent_accounting_without_pricing_unknowns():
    from hypoloom.final_report import run_outcome_context

    def agent(attempt_id, state, *, response=None, max_cost="5"):
        observation = {"request": {"role": "researcher", "budget": {
            "max_tokens": 10_000, "max_turns": 20, "max_cost": max_cost}}}
        if response is not None:
            observation["response"] = response
        return {"attempt_id": attempt_id, "kind": "AGENT", "state": state,
            "command_id": attempt_id, "observation_json": json.dumps(observation)}

    attempts = [
        agent("reported", "CLOSED", response={"token_usage": {"total_tokens": 123},
            "turn_count": 2, "cost": "1.25"}),
        agent("closed-unknown", "CLOSED"),
        agent("open-reservation", "OPEN", max_cost="2"),
    ]

    accounting = run_outcome_context([], attempts, [])["agent_accounting"]

    assert accounting == {
        "scope": "durable_attempts_before_final_report",
        "reported_tokens": 123,
        "reserved_tokens": 20000,
        "unavailable_token_attempts": 0,
        "reported_cost_usd": "1.25",
        "reserved_cost_usd": "7",
        "unknown_closed_attempts": 1,
        "open_attempts": 1,
    }
