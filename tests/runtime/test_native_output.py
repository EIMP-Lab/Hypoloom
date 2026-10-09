"""Native Stop validation preserves shape, correction limits and bounded IO."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from hypoloom import native_output


SCHEMA = {
    "type": "object",
    "properties": {"typed_candidate": {
        "type": "object", "properties": {"count": {"type": "integer", "minimum": 1}},
        "required": ["count"], "additionalProperties": False}},
    "required": ["typed_candidate"], "additionalProperties": False,
}
VALID = '{"typed_candidate":{"count":1}}'
INVALID = '{"typed_candidate":"private-candidate-value"}'


def _server(tmp_path, **kwargs):
    return native_output.NativeOutputValidator(
        tmp_path / "output.sock", output_schema=SCHEMA, session_id="session-owned",
        max_failed_validations=kwargs.pop("cap", 3),
        max_request_bytes=kwargs.pop("max_bytes", 4096),
        deadline_at=kwargs.pop("deadline", time.monotonic() + 5), **kwargs)


def _call(server, text=VALID, session_id="session-owned"):
    return native_output.call_validator(
        str(server.socket_path), session_id=session_id,
        last_assistant_message=text, max_request_bytes=4096, timeout_seconds=2)


def test_repeated_stop_revalidates_current_candidate(tmp_path):
    checks = []
    server = _server(tmp_path, candidate_check=lambda output: checks.append(output))
    try:
        assert _call(server) == _call(server) == {}
        assert len(checks) == 2
        assert server.snapshot()["accepted"]
        assert server.snapshot()["rejected_count"] == 0
    finally:
        server.close()


def test_repeated_stop_revalidates_changed_text_and_delivers_exact_file(tmp_path):
    checks = []
    server = _server(tmp_path, candidate_check=lambda output: checks.append(output))
    try:
        assert _call(server) == {}
        revised = VALID.replace("1", "2")
        assert _call(server, revised) == {}
        assert [item["typed_candidate"]["count"] for item in checks] == [1, 2]
        assert server.output_path.read_text() == server.accepted_text() == revised
        assert server.output_path.stat().st_mode & 0o777 == 0o600
        server.close()
        assert server.accepted_text() == revised
    finally:
        server.close()


@pytest.mark.parametrize("change", ["source", "session", "invalid"])
def test_repeated_stop_rejects_identity_or_source_drift(tmp_path, change):
    def check(output):
        if changed[0]:
            raise ValueError("source drift")
    changed = [False]
    server = _server(tmp_path, candidate_check=check)
    try:
        assert _call(server) == {}
        changed[0] = change == "source"
        with pytest.raises(ValueError):
            _call(server, INVALID if change == "invalid" else VALID,
                  session_id="foreign" if change == "session" else "session-owned")
        assert not server.snapshot()["accepted"]
        with pytest.raises(ValueError, match="not accepted"):
            server.accepted_text()
    finally:
        server.close()


def test_unaccepted_or_missing_output_file_cannot_supply_completion(tmp_path):
    server = _server(tmp_path)
    try:
        server.output_path.write_text(VALID)
        with pytest.raises(ValueError, match="not accepted"):
            server.accepted_text()
        assert _call(server) == {}
        server.output_path.unlink()
        with pytest.raises(FileNotFoundError):
            server.accepted_text()
    finally:
        server.close()


def test_failed_replacement_write_invalidates_previous_acceptance(tmp_path, monkeypatch):
    server = _server(tmp_path)
    try:
        assert _call(server) == {}
        def failed(text):
            raise OSError("disk full")
        monkeypatch.setattr(server, "_write_output", failed)
        with pytest.raises(ValueError):
            _call(server, VALID.replace("1", "2"))
        assert not server.snapshot()["accepted"]
        assert server.snapshot()["call_count"] == 1
        with pytest.raises(ValueError, match="not accepted"):
            server.accepted_text()
    finally:
        server.close()


def test_context_advisory_does_not_accept_candidate_or_change_correction_count(tmp_path):
    server = _server(tmp_path, context_notice=lambda: "Input estimate near capacity; preserve draft paths and checks.")
    try:
        notice = native_output.call_context_notice(str(server.socket_path), session_id="session-owned",
            max_request_bytes=4096, timeout_seconds=2)
        assert notice == {"hookSpecificOutput": {"hookEventName": "PreToolUse",
            "additionalContext": "Input estimate near capacity; preserve draft paths and checks."}}
        assert server.snapshot()["accepted"] is False
        assert server.snapshot()["call_count"] == 0
        assert _call(server, INVALID)["decision"] == "block"
        assert _call(server) == {}
        assert server.snapshot()["status"] == "recovered"
    finally:
        server.close()


def test_missing_advisory_telemetry_does_not_disable_output_validation(tmp_path):
    def unavailable():
        raise RuntimeError("observation unavailable")
    server = _server(tmp_path, context_notice=unavailable)
    try:
        assert native_output.call_context_notice(str(server.socket_path), session_id="session-owned",
            max_request_bytes=4096, timeout_seconds=2) == {}
        assert _call(server, INVALID)["decision"] == "block"
        assert _call(server) == {}
    finally:
        server.close()


@pytest.mark.parametrize("capacity_notice", ["Capacity observation " * 200, None, RuntimeError("unavailable")],
    ids=["capacity", "missing", "unavailable"])
def test_grounding_deadline_advisory_keeps_validation_and_correction_budget(tmp_path, capacity_notice):
    def capacity():
        if isinstance(capacity_notice, Exception):
            raise capacity_notice
        return capacity_notice

    server = _server(tmp_path, deadline=time.monotonic() + 1000,
        acceptance_smoke=True, parser_source_root=tmp_path, context_notice=capacity)
    try:
        def notice():
            return native_output.call_context_notice(str(server.socket_path), session_id="session-owned",
                max_request_bytes=4096, timeout_seconds=2)
        early = notice().get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "existing deadline" not in early
        # Advance to the last portion without timing-dependent sleeps. The real
        # transport and ordinary Stop validator still use this future deadline.
        server.deadline_at = time.monotonic() + 100
        late = notice()["hookSpecificOutput"]["additionalContext"]
        assert "existing deadline" in late and "/acceptance" in late
        assert len(late.encode()) <= 1536
        assert server.snapshot()["call_count"] == server.snapshot()["rejected_count"] == 0
        assert not server.snapshot()["accepted"]
        assert _call(server, INVALID)["decision"] == "block"
        assert _call(server) == {}
        assert server.snapshot()["call_count"] == 2
        server.deadline_at = time.monotonic() - 1
        with pytest.raises(ValueError, match="deadline"):
            _call(server)
    finally:
        server.close()


def test_context_advisory_cannot_cross_attempt_session_boundary(tmp_path):
    server = _server(tmp_path, context_notice=lambda: "private progress")
    try:
        with pytest.raises(ValueError, match="session"):
            native_output.call_context_notice(str(server.socket_path), session_id="foreign",
                max_request_bytes=4096, timeout_seconds=2)
        assert server.snapshot()["accepted"] is False
        assert server.snapshot()["call_count"] == 0
    finally:
        server.close()


def test_advisory_cli_unavailable_is_optional_but_stop_remains_fail_closed(tmp_path):
    base = [sys.executable, str(Path(native_output.__file__).resolve()), "--socket", str(tmp_path / "missing.sock"),
        "--max-bytes", "4096", "--timeout-seconds", "1"]
    payload = json.dumps({"session_id": "session-owned", "last_assistant_message": VALID})
    notice = subprocess.run([*base, "--context-notice"], input=payload, text=True, capture_output=True, timeout=5)
    stop = subprocess.run(base, input=payload, text=True, capture_output=True, timeout=5)
    assert notice.returncode == stop.returncode == 0
    assert json.loads(notice.stdout) == {}
    assert json.loads(stop.stdout)["continue"] is False


def test_report_missing_citation_is_repaired_inside_the_same_native_call(tmp_path):
    from hypoloom.final_report import validate_final_report
    from test_final_report import report_request
    request = report_request()
    report = tmp_path / "FINAL_REPORT.md"
    report.write_text("verdict-fact GOAL_MET")
    server = _server(tmp_path, final_report_check=lambda: validate_final_report(
        {"FINAL_REPORT.md": report.read_bytes()}, request))
    try:
        rejected = _call(server)
        assert rejected["decision"] == "block" and "outcome-accepted" in rejected["reason"]
        assert not server.snapshot()["accepted"]
        report.write_text("verdict-fact GOAL_MET outcome-accepted")
        assert _call(server) == {}
        assert server.snapshot()["status"] == "recovered"
    finally:
        server.close()


@pytest.mark.parametrize("cap", [1, 3, 11])
def test_rejection_cap_then_normal_invalid_completion(tmp_path, cap):
    server = _server(tmp_path, cap=cap)
    try:
        assert server.snapshot()["status"] == "not_observed"
        for ordinal in range(1, cap + 1):
            answer = _call(server, INVALID)
            if ordinal < cap:
                assert answer["decision"] == "block"
                assert "/typed_candidate" in answer["reason"]
                assert "complete JSON object" in answer["reason"]
            else:
                assert answer == {}
        observed = server.snapshot()
        assert observed["status"] == "unresolved"
        assert observed["call_count"] == observed["rejected_count"] == cap
        assert observed["validation_source"] == "host_stop_hook"
        assert [round_["ordinal"] for round_ in observed["rounds"]] == list(range(1, cap + 1))
        assert observed["scope"] == "native_stop_validation"
        assert observed["accepted"] is False
        assert all(round_["status"] == "rejected" and round_["validation_error"]
                   for round_ in observed["rounds"])
        assert "private-candidate-value" not in json.dumps(observed)
        assert "cost" not in observed and "last_valid" not in observed
    finally:
        server.close()


@pytest.mark.parametrize("failures", [0, 2, 9])
def test_success_after_exact_same_invocation_corrections(tmp_path, failures):
    server = _server(tmp_path, cap=failures + 1)
    try:
        for _ in range(failures):
            assert _call(server, INVALID)["decision"] == "block"
        assert _call(server) == {}
        observed = server.snapshot()
        assert observed["status"] == ("first_pass" if failures == 0 else "recovered")
        assert observed["call_count"] == failures + 1
        assert observed["rejected_count"] == failures
        assert observed["accepted"] is True
        assert observed["rounds"][-1] == {
            "ordinal": failures + 1, "status": "accepted", "validation_error": None}
        observed["rounds"].clear()
        assert len(server.snapshot()["rounds"]) == failures + 1
    finally:
        server.close()


@pytest.mark.parametrize("text", [
    "{} {}", "[]", "null", '"object"', "```json\n{}\n```", "{",
    '{"typed_candidate":{"count":NaN}}', '{"typed_candidate":{"count":Infinity}}',
    '{"typed_candidate":{"count":1e999}}', '{"typed_candidate":{},"typed_candidate":{}}',
    '{"typed_candidate":{"count":true}}', '{"typed_candidate":{"count":0}}', INVALID,
])
def test_decode_rejects_invalid_json_and_schema_without_repair(text):
    with pytest.raises(ValueError):
        native_output.decode_output(text, SCHEMA)


def test_decode_accepts_complete_object_and_checks_full_schema():
    assert native_output.decode_output(" \n" + VALID + "\n", SCHEMA) == {"typed_candidate": {"count": 1}}
    with pytest.raises(ValueError, match="required"):
        native_output.decode_output("{}", SCHEMA)
    with pytest.raises(ValueError, match="additionalProperties"):
        native_output.decode_output('{"typed_candidate":{"count":1},"extra":true}', SCHEMA)
    with pytest.raises(ValueError, match="schema"):
        native_output.check_output_schema({"type": "not-a-type"})
    with pytest.raises(ValueError):
        native_output.decode_output("{}", {"$ref": "https://invalid.example/schema"})


@pytest.mark.parametrize("cap", [0, -1, True, 1.5])
def test_constructor_rejects_invalid_correction_cap(tmp_path, cap):
    with pytest.raises(ValueError):
        _server(tmp_path, cap=cap)
    assert not (tmp_path / "output.sock").exists()


def test_wrong_session_is_sticky_fail_closed(tmp_path):
    server = _server(tmp_path)
    try:
        with pytest.raises(ValueError, match="session"):
            _call(server, session_id="foreign")
        assert server.snapshot()["status"] == "validation_failed"
        assert server.snapshot()["call_count"] == 0
        with pytest.raises(ValueError):
            _call(server)
    finally:
        server.close()


@pytest.mark.parametrize("payload", [b"{broken}\n", b"{}\n", b"x" * 129 + b"\n"])
def test_invalid_or_oversized_transport_is_fail_closed(tmp_path, payload):
    server = _server(tmp_path, max_bytes=128)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(str(server.socket_path))
            client.sendall(payload)
            response = json.loads(client.makefile("rb").readline())
        assert response["ok"] is False
        assert server.snapshot()["status"] == "validation_failed"
        assert server.snapshot()["call_count"] == 0
    finally:
        server.close()


def test_deadline_exhaustion_and_close_remove_endpoint(tmp_path):
    server = _server(tmp_path, deadline=time.monotonic() - 1)
    try:
        with pytest.raises((ValueError, OSError)):
            _call(server)
        assert server.snapshot()["status"] == "validation_failed"
        assert "deadline" in server.snapshot()["error"]
    finally:
        server.close()
    server.close()
    assert not server.socket_path.exists()
    assert not server.thread.is_alive()
    with pytest.raises(OSError):
        _call(server)


def _client_process(socket_path, payload, *, timeout="2", max_bytes="4096"):
    return subprocess.run(
        [sys.executable, "-I", "-S", str(Path(native_output.__file__).resolve()),
         "--socket", str(socket_path), "--max-bytes", max_bytes, "--timeout-seconds", timeout],
        input=json.dumps(payload), text=True, capture_output=True, timeout=5,
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})


def test_standalone_stdlib_client_selects_only_hook_fields(tmp_path):
    server = _server(tmp_path)
    try:
        result = _client_process(server.socket_path, {
            "session_id": "session-owned", "last_assistant_message": VALID,
            "hook_event_name": "Stop", "stop_hook_active": True,
            "transcript_path": "/do/not/read", "other": "do not forward"})
        assert result.returncode == 0 and result.stderr == ""
        assert json.loads(result.stdout) == {}
        assert server.snapshot()["status"] == "first_pass"
    finally:
        server.close()


@pytest.mark.parametrize("payload", [{}, {"session_id": "session-owned"}, {
    "session_id": "session-owned", "last_assistant_message": "x" * 500}])
def test_standalone_client_input_failure_stops_native(tmp_path, payload):
    result = _client_process(tmp_path / "absent.sock", payload, max_bytes="128")
    answer = json.loads(result.stdout)
    assert answer["continue"] is False and answer["stopReason"]
    assert "x" * 50 not in result.stdout


def test_standalone_transport_failure_stops_native(tmp_path):
    result = _client_process(tmp_path / "absent.sock", {
        "session_id": "session-owned", "last_assistant_message": VALID})
    assert json.loads(result.stdout)["continue"] is False


def test_existing_socket_path_is_never_overwritten(tmp_path):
    path = tmp_path / "output.sock"
    path.write_text("user-owned")
    with pytest.raises(FileExistsError):
        _server(tmp_path)
    assert path.read_text() == "user-owned"


def test_partial_request_deadline_is_fatal_without_candidate_round(tmp_path):
    server = _server(tmp_path, deadline=time.monotonic() + 0.1)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(str(server.socket_path))
            client.sendall(b'{"session_id":')
            response = json.loads(client.makefile("rb").readline())
        assert response["ok"] is False
        observed = server.snapshot()
        assert observed["status"] == "validation_failed" and not observed["accepted"]
        assert observed["call_count"] == 0 and "deadline" in observed["error"]
    finally:
        server.close()


def test_close_unblocks_inflight_request(tmp_path):
    server = _server(tmp_path)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.connect(str(server.socket_path))
        client.sendall(b'{"session_id":')
        limit = time.monotonic() + 1
        while not server._connections and time.monotonic() < limit:
            time.sleep(0.005)
        started = time.monotonic()
        server.close()
        assert time.monotonic() - started < 1
        assert not server.thread.is_alive()
    finally:
        client.close()
        server.close()


def test_client_transport_timeout_stops_native(tmp_path):
    path = tmp_path / "unresponsive.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(1)
    release = threading.Event()

    def accept():
        connection, _ = listener.accept()
        with connection:
            release.wait(timeout=2)

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    try:
        result = _client_process(path, {"session_id": "session-owned",
                                       "last_assistant_message": VALID}, timeout="0.05")
        assert json.loads(result.stdout)["continue"] is False
    finally:
        release.set()
        listener.close()
        thread.join(timeout=2)


def test_raw_hook_uses_existing_private_redacting_recorder(tmp_path):
    from hypoloom.runtime_diagnostics import RuntimeDiagnostics

    diagnostics = RuntimeDiagnostics(tmp_path / "diagnostics", secrets=["private-candidate-value"])
    server = _server(tmp_path, diagnostics=diagnostics)
    try:
        _call(server, INVALID)
        recorded = json.loads((diagnostics.root / "native-output/000001.json").read_text())
        assert recorded["session_id"] == "session-owned"
        assert "[REDACTED]" in recorded["last_assistant_message"]
        assert "private-candidate-value" not in json.dumps(server.snapshot())
        assert diagnostics.summary()["capture_errors"] == []
    finally:
        server.close()


def test_arbitrary_positive_cap_is_not_limited_to_native_default(tmp_path):
    server = _server(tmp_path, cap=10**30)
    try:
        assert _call(server, INVALID)["decision"] == "block"
    finally:
        server.close()
