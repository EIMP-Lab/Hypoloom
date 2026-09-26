"""Native CLI/relay/bwrap evidence is explainable from an offline support copy.

The reused rehearsals execute real Host and CLI processes; only model choices
and literature are scripted locally. No paid model or GPU is used.
"""
from __future__ import annotations

import json
from pathlib import Path

from hypoloom.support_bundle import export_support_bundle
from test_claude_shared_acceptance_g3 import _BROKEN, _REPAIRED, _RUN
from test_claude_shared_final_retry_g3 import (
    _INVALID_REF,
    test_native_shared_final_report_retries_with_one_common_input_and_same_session as _final_retry,
)
from test_full_cc_host_g3 import (
    _rows,
    test_full_cc_host_recovers_committed_submit_and_reconstructs_context as _recover_submit,
)


def _json_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_native_support_copy_explains_tool_repair_and_schema_rejection(tmp_path: Path):
    _final_retry(tmp_path)
    evidence = json.loads((tmp_path / "shared-final-retry-evidence.json").read_text())
    output = tmp_path.parent / (tmp_path.name + "-support")
    report = export_support_bundle(tmp_path / "state", _RUN, output, include_private_data=True)
    assert report["source_read_only"] and report["non_authoritative"]
    diagnostic_root = output / "files/diagnostics/agents" / _RUN
    failed = diagnostic_root / evidence["final_attempts"][0]
    launch = json.loads((failed / "launch.json").read_text())
    assert "--json-schema" not in launch["argv"] and "--session-id" in launch["argv"]
    assert "/output-validation.sock" in launch["argv"]
    resumed = json.loads((diagnostic_root / evidence["final_attempts"][1] / "launch.json").read_text())
    assert "--session-id" in resumed["argv"] and "--resume" not in resumed["argv"]
    assert (failed / "prompt.txt").stat().st_size > 0
    envelope = next(row for row in reversed(_json_lines(failed / "stdout.log")) if row.get("type") == "result")
    assert envelope["subtype"] == "success" and envelope["is_error"] is False
    assert json.loads(envelope["result"])["typed_candidate"]["supporting_refs"] == [_INVALID_REF]
    assert _INVALID_REF in (failed / "stdout.log").read_text()
    assert [event["kind"] for event in _json_lines(failed / "events.jsonl")] == ["prepared", "started", "ended"]
    assert json.loads((failed / "result.json").read_text())["failure_code"] == "invalid_structured_output"
    validation = json.loads((failed / "native-output/000001.json").read_text())
    assert validation["last_assistant_message"] == envelope["result"]
    assert "/typed_candidate/supporting_refs/0" in validation["error"]
    attempts = [json.loads((path / "request.json").read_text()) for path in diagnostic_root.iterdir()]
    grounding = next(row for row in attempts if row["command"] == "GROUNDING")
    ground = diagnostic_root / grounding["attempt_id"]
    transcript = output / "files/artifacts" / _RUN / "agent" / grounding["attempt_id"] / "transcript/transcript.jsonl"
    assert _BROKEN in transcript.read_text() and _REPAIRED in transcript.read_text()
    assert any(row.get("type") == "result" for row in _json_lines(ground / "stdout.log"))
    requests = list(ground.glob("provider/*/request.body"))
    assert requests and all(not any(tool["name"] == "StructuredOutput"
        for tool in json.loads(path.read_text()).get("tools", [])) for path in requests)
    from test_claude_file_tool_cli import native_output_schema
    assert native_output_schema(json.loads(requests[0].read_text())) == json.loads((ground / "launch.json").read_text())["output_schema"]
    assert list(ground.glob("provider/*/response.body"))
    for path in output.rglob("*"):
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
    (tmp_path / "diagnostic-g3-evidence.json").write_text(json.dumps({
        "support_copy": str(output), "agent_count": evidence["agent_count"],
        "process_count": evidence["process_count"], "oracle": evidence["oracle"],
        "failed_final": str(failed), "grounding_transcript": str(transcript),
        "paid_model": False, "gpu": False}, indent=2))


def test_native_host_process_loss_keeps_partial_stdio_and_recovery_identity(tmp_path: Path):
    _recover_submit(tmp_path)  # Actual child Host exits 86 after commit, before reply.
    root = tmp_path / "state"
    run_id = "full-cc-host-recovery-g3"
    attempts = _rows(root / "state.sqlite", "SELECT * FROM execution_attempts ORDER BY started_at")
    lost = next(row for row in attempts if row["terminal_reason"] == "LOST")
    assert lost["kind"] == "AGENT"
    output = tmp_path.parent / (tmp_path.name + "-support")
    report = export_support_bundle(root, run_id, output, attempt_id=lost["attempt_id"], include_private_data=True)
    partial = output / "files/diagnostics/agents" / run_id / lost["attempt_id"]
    assert (partial / "stdout.log").stat().st_size > 0
    assert (partial / "launch.json").is_file() and (partial / "prompt.txt").is_file()
    events = _json_lines(partial / "events.jsonl")
    assert [event["kind"] for event in events] == ["prepared", "started"]
    assert not (partial / "result.json").exists()
    assert any("Missing end records" in text for text in report["limitations"])
    assert report["agent_captures"][0]["status"] == "interrupted_or_unknown"
    assert "result.json" in report["agent_captures"][0]["missing_files"]
    observed = json.loads(lost["observation_json"])
    assert observed["session"]["archive_after_loss"] is True
    recovered = next(row for row in attempts if row["command_id"] == lost["command_id"] and row["terminal_reason"] == "SUCCEEDED")
    assert json.loads(recovered["observation_json"])["session"]["session_id"] != observed["session"]["session_id"]
    assert json.loads(recovered["observation_json"])["session"]["branch"] == observed["session"]["branch"]
    assert len(_rows(root / "state.sqlite", "SELECT * FROM workflow_transitions WHERE workflow_kind='EXPERIMENT' AND revision=1")) == 2
    (tmp_path / "diagnostic-loss-evidence.json").write_text(json.dumps({
        "host_exit": 86, "not_sigkill": True, "lost_attempt_id": lost["attempt_id"],
        "recovered_attempt_id": recovered["attempt_id"], "support_copy": str(output),
        "partial_stdio": str(partial / "stdout.log"), "end_record_present": False,
        "paid_model": False, "gpu": False}, indent=2))
