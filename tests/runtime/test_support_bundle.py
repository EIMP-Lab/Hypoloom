"""Support copies preserve scope and disclose missing/sanitized observations."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from hypoloom.cli import main
from hypoloom.debug_mode import build_debug_report, write_debug_fixture
from hypoloom.state_store import HostWriterCapability, StateStore
from hypoloom.support_bundle import SupportBundleError, export_support_bundle
from test_debug_mode import _seed_agent_retry


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _tree(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _attachment(output, report, name):
    return output / next(item["path"] for item in report["attachments"]
                         if item.get("path", "").endswith(name))


def test_older_run_reports_unavailable_capture_without_inventing_completeness(tmp_path):
    root = tmp_path / "state"
    run_id, first, second = _seed_agent_retry(root)
    output = tmp_path / "bundle"
    report = export_support_bundle(root, run_id, output)
    assert report == json.loads((output / "report.json").read_text())
    assert report["source_read_only"] and not report["restorable_backup"]
    assert report["completeness"] == "observed_files_only"
    assert all(item["status"] == "unavailable" for item in report["agent_captures"])
    assert {a["source"] for a in report["attachments"] if a["status"] == "unavailable"} >= {
        "private-attachment", "host.jsonl"}
    assert {item["attempt_id"] for item in report["agent_captures"]} == {"attempt-1", "attempt-2"}
    projection = json.loads((output / "debug.json").read_text())
    assert len(projection["attempts"]) == 2
    assert all("observation" not in item for item in projection["attempts"])
    for path in (output, *output.rglob("*")):
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)


def test_selected_private_bundle_copies_only_attempt_and_filtered_host_records(tmp_path):
    root = tmp_path / "state"
    run_id, first, second = _seed_agent_retry(root)
    selected = root / f"diagnostics/agents/{run_id}/{first}"
    _write(selected / "prompt.txt", "selected private research")
    _write(selected / "events.jsonl", json.dumps({"kind": "start", "attempt_id": first}) + "\n")
    _write(root / f"diagnostics/agents/{run_id}/{second}/prompt.txt", "other attempt private text")
    _write(root / "diagnostics/agents/other-run/attempt/prompt.txt", "unrelated run secret")
    _write(root / "diagnostics/host.jsonl", "\n".join(json.dumps(item) for item in [
        {"run_id": run_id, "attempt_id": first, "exception_type": "ValueError", "traceback": "selected stack"},
        {"run_id": run_id, "attempt_id": None, "exception_type": "RuntimeError", "traceback": "before attempt"},
        {"run_id": run_id, "attempt_id": second, "traceback": "other attempt stack"},
        {"run_id": "other-run", "traceback": "unrelated run stack"},
    ]) + "\n")
    canonical = root / f"artifacts/{run_id}/agent/{first}/transcript"
    _write(canonical / "transcript.jsonl", '{"text":"tool failed then fixed"}\n')
    _write(canonical / "evaluator/evaluate.py", "print('common evaluator')\n")
    output = tmp_path / "private"
    report = export_support_bundle(root, run_id, output, attempt_id=first, include_private_data=True)
    exported = b"\n".join(_tree(output).values())
    for expected in (b"selected private research", b"selected stack", b"before attempt", b"common evaluator"):
        assert expected in exported
    for forbidden in (b"other attempt private text", b"other attempt stack", b"unrelated run"):
        assert forbidden not in exported
    for path in (output, *output.rglob("*")):
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
    assert len(json.loads((output / "debug.json").read_text())["attempts"]) == 1
    assert any(a["status"] == "included" and a.get("path", "").endswith("evaluate.py") for a in report["attachments"])


def test_default_bundle_omits_raw_data_and_keeps_safe_failure_metadata(tmp_path):
    root = tmp_path / "state"
    run_id, first, _ = _seed_agent_retry(root)
    selected = root / f"diagnostics/agents/{run_id}/{first}"
    for name in ("prompt.txt", "request.json", "stdout.log", "stderr.log", "result.json", "provider/000001/response.body"):
        _write(selected / name, "private research data")
    _write(selected / "events.jsonl", '{"kind":"start","prompt":"private research data"}\n')
    _write(selected / "provider/000001/exchange.json", '{"status":429,"failure_stage":"upstream_response","response_bytes":123}')
    output = tmp_path / "default"
    report = export_support_bundle(root, run_id, output, attempt_id=first)
    assert b"private research data" not in b"\n".join(_tree(output).values())
    assert sum(item["reason"] == "requires_include_private_data" for item in report["attachments"]) == 6
    exchange = json.loads(_attachment(output, report, "exchange.json").read_text())
    assert exchange["status"] == 429


@pytest.mark.parametrize("private", [False, True])
def test_support_context_keeps_measured_and_estimated_requests_separate_without_credentials(tmp_path, private):
    root = tmp_path / "state"
    run_id, first, _ = _seed_agent_retry(root)
    selected = root / f"diagnostics/agents/{run_id}/{first}"
    context = {"input_tokens": None, "last_observed_exchange": 5, "last_observed_input_tokens": 59326,
        "current_request_exchange": 6, "current_request_estimated_input_tokens": 211464,
        "current_request_estimate_source": "provider_usage_plus_delta_estimate",
        "current_request_estimate_reason": "unchanged_prefix", "current_request_fits": False,
        "context_recovery": {"recovery_reported_tokens": 1234, "recovery_unknown_token_attempts": 1}}
    capacity = {"capacity_measurement_version": 25, "request_input_bytes": 300000,
        "estimate_reason": "unchanged_prefix", "anchor_exchange": 5,
        "anchor_input_tokens": 59326, "added_input_bytes": 152138, "estimated_input_tokens": 211464}
    _write(selected / "events.jsonl", json.dumps({"kind": "ended", "context": context,
        "api_key": "synthetic-context-secret", "prompt": "private research"}) + "\n")
    _write(selected / "provider/000006/exchange.json", json.dumps({"context_capacity": capacity,
        "authorization": "Bearer synthetic-context-secret", "request_body": {"messages": ["private research"]}}))
    output = tmp_path / "export"
    report = export_support_bundle(root, run_id, output, attempt_id=first, include_private_data=private)
    copied_context = json.loads(_attachment(output, report, "events.jsonl").read_text())["context"]
    assert copied_context == context
    assert json.loads(_attachment(output, report, "exchange.json").read_text())["context_capacity"] == capacity
    exported = b"\n".join(_tree(output).values())
    assert b"synthetic-context-secret" not in exported
    if not private:
        assert b"private research" not in exported


def test_mcp_traceback_requires_private_opt_in_and_remains_redacted(tmp_path):
    root = tmp_path / "state"
    run_id, first, _ = _seed_agent_retry(root)
    relative = f"diagnostics/agents/{run_id}/{first}/events.jsonl"
    events = [
        {"kind": "started", "attempt_id": first},
        {"kind": "mcp_exception", "stage": "handler", "tool": "submit_experiment",
         "attempt_id": first, "exception_type": "OSError", "api_key": "support-test-secret",
         "message": "private project failure support-test-secret",
         "traceback": "Traceback (most recent call last):\nprivate_project.py: failure support-test-secret"},
    ]
    _write(root / relative, "".join(json.dumps(event) + "\n" for event in events))
    for private in (False, True):
        output = tmp_path / ("private" if private else "default")
        report = export_support_bundle(root, run_id, output, attempt_id=first,
                                       include_private_data=private)
        copied = _attachment(output, report, "events.jsonl").read_text()
        records = [json.loads(line) for line in copied.splitlines()]
        assert len(records) == 2
        assert records[1]["kind"] == "mcp_exception"
        assert records[1]["exception_type"] == "OSError"
        assert "support-test-secret" not in copied
        if private:
            assert records[1]["tool"] == "submit_experiment"
            assert "private_project.py" in records[1]["traceback"]
            assert records[1]["message"] == "private project failure [REDACTED]"
            assert any(item["source"] == relative and
                       item["sensitivity"] == "private_research_data"
                       for item in report["attachments"])
        else:
            assert "traceback" not in records[1] and "message" not in records[1]
            assert "private_project.py" not in copied


def test_interrupted_stdio_and_exchange_remain_explicit_in_default_report(tmp_path):
    root = tmp_path / "state"
    run_id, first, _ = _seed_agent_retry(root)
    selected = root / f"diagnostics/agents/{run_id}/{first}"
    _write(selected / "events.jsonl", '{"kind":"started","pid":123}\n{"kind":"end')
    _write(selected / "stdout.log", "partial CLI output")
    _write(selected / "provider/000001/exchange.json", json.dumps({
        "upstream_status": 200, "response_body_complete": False,
        "response_partial_reason": "interrupted", "response_bytes_received": 17,
        "response_capture": {"complete": False, "received_bytes": 17, "private": "must stay private"},
        "response_headers": {"x-request-id": "selected-request", "authorization": "must stay private"},
        "request_body": {"prompt": "must stay private"}}))
    output = tmp_path / "bundle"
    report = export_support_bundle(root, run_id, output, attempt_id=first)
    capture = report["agent_captures"][0]
    assert capture["status"] == "interrupted_or_unknown"
    assert capture["started_recorded"] and not capture["ended_recorded"]
    assert "result.json" in capture["missing_files"]
    assert any(item["source"] == "result.json" and item["status"] == "unavailable" for item in report["attachments"])
    metadata = json.loads(_attachment(output, report, "exchange.json").read_text())
    assert metadata["response_body_complete"] is False
    assert metadata["response_partial_reason_omitted"] is True
    assert metadata["response_headers"] == {"x-request-id": "selected-request"}
    assert metadata["response_capture"]["complete"] is False
    assert "request_body" not in metadata
    exported = b"\n".join(_tree(output).values())
    assert b"must stay private" not in exported
    events = _attachment(output, report, "events.jsonl").read_text()
    assert json.loads(events)["kind"] == "started"


def test_private_copy_redacts_nested_json_base64_credentials_and_omits_environment(tmp_path):
    root = tmp_path / "state"
    embedded = {"api_key": "embedded-key-123", "reference": "embedded-key-123"}
    inputs = [
        {"media_type": "application/json", "content_base64": base64.b64encode(json.dumps(embedded).encode()).decode()},
        {"media_type": "text/plain", "content_base64": base64.b64encode(b"Authorization: Bearer text-secret-123").decode()},
        {"media_type": "application/octet-stream", "content_base64": base64.b64encode(b"opaque-private-token").decode()},
    ]
    run_id, first, _ = _seed_agent_retry(root, first_diagnostics={"support_test_inputs": inputs,
        "process_request": {"env": {"PLAIN_DATA": "environment-must-not-export"}},
        "json_record": json.dumps({"authorization": "json-secret-123"})})
    selected = root / f"diagnostics/agents/{run_id}/{first}"
    _write(selected / "stdout.log", 'Authorization: Bearer cli-secret-123\n{"api_key":"line-secret-123"}\n{"note":"line-secret-123"}\n')
    _write(selected / "provider/000001/response.body", 'data: {"api_key":"body-secret-123"}\n\ndata: {"note":"body-secret-123"}\n\n')
    output = tmp_path / "private"
    report = export_support_bundle(root, run_id, output, attempt_id=first, include_private_data=True)
    encoded = b"\n".join(_tree(output).values())
    for secret in ("embedded-key-123", "text-secret-123", "opaque-private-token", "environment-must-not-export",
                   "json-secret-123", "cli-secret-123", "line-secret-123", "body-secret-123"):
        assert secret.encode() not in encoded
        assert base64.b64encode(secret.encode()) not in encoded
    debug = json.loads((output / "debug.json").read_text())
    copied_inputs = debug["attempts"][0]["observation"]["diagnostics"]["support_test_inputs"]
    assert "embedded-key-123" not in base64.b64decode(copied_inputs[0]["content_base64"]).decode()
    assert "text-secret-123" not in base64.b64decode(copied_inputs[1]["content_base64"]).decode()
    assert "OMITTED" in copied_inputs[2]["content_base64"]
    stdout_lines = (output / f"files/diagnostics/agents/{run_id}/{first}/stdout.log").read_text().splitlines()
    assert len(stdout_lines) == 3
    assert json.loads(stdout_lines[1])["api_key"] == "[REDACTED]"
    assert json.loads(stdout_lines[2])["note"] == "[REDACTED]"
    assert {"credential_redacted", "environment_omitted", "opaque_embedded_content_omitted"} <= set(report["attachments"][0]["redactions"])
    assert debug["attempts"][0]["reconstructability"]["terminal_evidence"] == "SANITIZED_RECORDED_OUTPUT"


@pytest.mark.parametrize("kind", ["inside", "same", "existing", "parent_symlink", "source_symlink", "input_root"])
def test_invalid_output_cannot_write_source_or_follow_links(tmp_path, kind):
    root = tmp_path / "state"
    run_id, first, _ = _seed_agent_retry(root, first_diagnostics=(
        {"support_input": {"target_repo": str(tmp_path / "project")}} if kind == "input_root" else None))
    output = tmp_path / "output"
    if kind == "inside": output = root / "output"
    elif kind == "same": output = root
    elif kind == "existing": output.mkdir()
    elif kind == "parent_symlink":
        (tmp_path / "alias").symlink_to(tmp_path, target_is_directory=True)
        output = tmp_path / "alias/output"
    elif kind == "source_symlink":
        (tmp_path / "source-alias").symlink_to(root, target_is_directory=True)
        root = tmp_path / "source-alias"
    elif kind == "input_root":
        project = tmp_path / "project"
        project.mkdir()
        output = project / "output"
    with pytest.raises(SupportBundleError):
        export_support_bundle(root, run_id, output)
    if kind not in {"same", "existing"}:
        assert not output.exists()


def test_unsafe_attachment_symlink_and_binary_are_reported_not_followed(tmp_path):
    root = tmp_path / "state"
    run_id, first, _ = _seed_agent_retry(root)
    selected = root / f"diagnostics/agents/{run_id}/{first}"
    selected.mkdir(parents=True)
    outside = tmp_path / "outside"
    _write(outside / "private.txt", "external must not export")
    (selected / "stdout.log").symlink_to(outside / "private.txt")
    (selected / "provider").symlink_to(outside, target_is_directory=True)
    (selected / "binary.body").write_bytes(b"\xff\x00binary")
    output = tmp_path / "bundle"
    report = export_support_bundle(root, run_id, output, include_private_data=True)
    assert b"external must not export" not in b"\n".join(_tree(output).values())
    assert any(a["reason"] == "binary_content_cannot_be_safely_redacted" for a in report["attachments"])
    assert any("symlink" in a["reason"] for a in report["attachments"])


def test_process_bundle_uses_selected_attempt_logs_and_existing_artifact_identity(tmp_path):
    root = tmp_path / "state"
    run_id, _, _ = _seed_agent_retry(root)
    attempt_id = "process-selected"
    identity = {"run_id": run_id, "experiment_id": "experiment", "execution_id": "execution",
                "attempt_id": attempt_id, "artifact_kind": "result"}
    relative = "/".join(identity.values())
    store = StateStore(root, HostWriterCapability.issue()).initialize()
    try:
        with store.transaction() as tx:
            tx.record_command({"command_id": "process-command", "run_id": run_id, "workflow_id": "experiment",
                "expected_revision": 0, "command_type": "EXECUTE", "request": {}, "status": "accepted",
                "result": {}, "revision": 1})
            tx.open_attempt({**identity, "workflow_id": "experiment", "command_id": "process-command",
                "kind": "PROCESS", "attempt_ordinal": 1, "host_generation": "support-test", "started_at": 1.0})
            tx.close_attempt(attempt_id, terminal_reason="FAILED", ended_at=2.0, payload={"failure_code": "process_failed"})
            tx.record_artifact({**identity, "artifact_id": ":".join(identity.values()), "path": relative,
                "schema_version": "2", "digest_algorithm": "sha256", "digest": "0" * 64})
    finally:
        store.close()
    _write(root / f"artifacts/work/{attempt_id}/logs/stdout.log", "selected process stdout")
    _write(root / f"artifacts/result-staging/{attempt_id}/logs/stderr.log", "selected staged stderr")
    _write(root / "artifacts/work/other-attempt/logs/stdout.log", "unrelated process output")
    _write(root / f"artifacts/{relative}/source/experiment.py", "selected canonical source")
    output = tmp_path / "bundle"
    export_support_bundle(root, run_id, output, attempt_id=attempt_id, include_private_data=True)
    data = b"\n".join(_tree(output).values())
    assert b"selected process stdout" in data and b"selected staged stderr" in data
    assert b"selected canonical source" in data and b"unrelated process output" not in data


@pytest.mark.parametrize("live_writer", [False, True])
def test_source_database_and_artifacts_are_unchanged(tmp_path, live_writer):
    root = tmp_path / "state"
    run_id, first, _ = _seed_agent_retry(root)
    _write(root / f"diagnostics/agents/{run_id}/{first}/stdout.log", "retained bytes")
    store = StateStore(root, HostWriterCapability.issue()).initialize() if live_writer else None
    try:
        def authoritative_snapshot():
            return {name: data for name, data in _tree(root).items()
                    if name not in {"state.sqlite-wal", "state.sqlite-shm"}}
        before = authoritative_snapshot()
        export_support_bundle(root, run_id, tmp_path / "bundle", include_private_data=True)
        assert authoritative_snapshot() == before
    finally:
        if store is not None:
            store.close()


def test_cli_legacy_fixture_json_is_exactly_unchanged_and_bundle_attempt_optional(tmp_path, monkeypatch, capsys):
    import hypoloom.debug_mode as debug
    root = tmp_path / "state"
    run_id, first, _ = _seed_agent_retry(root)
    monkeypatch.setattr(debug.time, "time", lambda: 1234.0)
    expected = tmp_path / "expected.json"
    write_debug_fixture(expected, build_debug_report(root, run_id, attempt_id=first, include_payloads=True))
    actual = tmp_path / "actual.json"
    assert main(["debug", "export", "--state-root", str(root), "--run-id", run_id, "--attempt-id", first, "--output", str(actual)]) == 0
    assert actual.read_bytes() == expected.read_bytes()
    assert json.loads(capsys.readouterr().out) == {"status": "exported", "non_authoritative": True,
        "path": str(actual), "run_id": run_id, "attempt_id": first}
    output = tmp_path / "bundle"
    assert main(["debug", "export", "--state-root", str(root), "--run-id", run_id,
                 "--output", str(output), "--support-bundle"]) == 0
    assert json.loads(capsys.readouterr().out)["kind"] == "support_bundle"
    assert (output / "report.json").is_file()
