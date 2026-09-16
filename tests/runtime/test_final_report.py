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
