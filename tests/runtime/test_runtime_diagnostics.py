"""Private diagnostic copies retain evidence without becoming run authority."""

import base64
import json
import subprocess
import sys

import pytest

from hypoloom.claude_agent_host import _provider_accounting
from hypoloom.fake_agent import default_request


def _recorder(tmp_path, **kwargs):
    from hypoloom.runtime_diagnostics import RuntimeDiagnostics
    return RuntimeDiagnostics.for_agent(tmp_path, default_request(), **kwargs)


@pytest.mark.parametrize("field,value,explanation", [
    ("usage", None, "usage"), ("num_turns", True, "num_turns"),
    ("total_cost_usd", None, "total_cost_usd"),
    ("usage", {"input_tokens": 3, "output_tokens": True}, "output_tokens"),
])
def test_unknown_usage_names_the_failed_field(field, value, explanation):
    envelope = {"usage": {"input_tokens": 3, "output_tokens": 2},
                "num_turns": 1, "total_cost_usd": 0.5}
    envelope[field] = value
    result = _provider_accounting(envelope, "stub-model", allow_model_usage_fallback=False)
    assert "provider_accounting" not in result
    assert explanation in result.get("provider_accounting_error", "")


def test_files_are_private_and_stdio_survives_process_exit(tmp_path):
    recorder = _recorder(tmp_path)
    recorder.write_json("request.json", {"run_id": "run", "nested": [1, 2]})
    recorder.event("started", pid=123)
    with recorder.open_stdio("stdout.log") as stream:
        subprocess.run([sys.executable, "-c", "import os; os.write(1,b'partial CLI output'); os._exit(17)"],
                       stdout=stream, check=False)
    assert (recorder.root / "stdout.log").read_bytes() == b"partial CLI output"
    assert [json.loads(line)["kind"] for line in (recorder.root / "events.jsonl").read_text().splitlines()] == ["started"]
    for path in (tmp_path / "diagnostics").rglob("*"):
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
    assert recorder.summary()["capture_errors"] == []


@pytest.mark.parametrize("chunks", [(b"prefix abc", b"DEF tail"), (b"prefix ", b"a", b"bcD", b"EF tail")])
def test_stream_redacts_credentials_split_across_chunks(tmp_path, chunks):
    recorder = _recorder(tmp_path)
    child = recorder.child("provider/000001", secrets=("abcDEF",))
    with child.stream("response.body") as stream:
        for chunk in chunks:
            stream.write(chunk)
            assert b"abcDEF" not in (child.root / "response.body").read_bytes()
    assert (child.root / "response.body").read_bytes() == b"prefix [REDACTED] tail"
    assert stream.summary()["bytes_received"] == len(b"prefix abcDEF tail")
    assert stream.summary()["complete"] is True
    assert recorder.summary()["redacted"] is True


def test_json_and_text_redaction_preserves_nonsecret_context(tmp_path):
    from hypoloom.runtime_diagnostics import sanitize_payload, sanitize_text
    recorder = _recorder(tmp_path, secrets=("host-key",))
    recorder.write_json("request.json", {"message": "observed host-key error", "Authorization": "Bearer hello"})
    value = json.loads((recorder.root / "request.json").read_text())
    assert value == {"message": "observed [REDACTED] error", "Authorization": "[REDACTED]"}
    assert sanitize_payload({"input_tokens": 8, "api_key": "other"}) == {"input_tokens": 8, "api_key": "[REDACTED]"}
    text = sanitize_text("failed Authorization: Bearer xyz; url https://a:b@example.test/x?api_key=qwerty&n=2")
    assert all(secret not in text for secret in ("xyz", "a:b", "qwerty"))
    assert "failed" in text and "&n=2" in text


def test_workspace_text_is_retained_and_redacted_without_admitting_opaque_bytes():
    from hypoloom.agent_aci import WorkspaceArtifact
    from hypoloom.runtime_diagnostics import sanitize_payload, OMITTED_EMBEDDED_CONTENT
    code = b"api_key = 'fixture-secret'\nprint('evaluation', 'fixture-secret')\n"
    artifact = WorkspaceArtifact.from_bytes("parser/evaluate.py", code).to_dict()
    clean = sanitize_payload({"proposed_workspace_artifacts": [artifact]})
    decoded = base64.b64decode(clean["proposed_workspace_artifacts"][0]["content_base64"])
    assert b"evaluation" in decoded and b"fixture-secret" not in decoded
    assert b"[REDACTED]" in decoded
    for invalid in ({"content_base64": artifact["content_base64"]},
            {**artifact, "path": "../escape.py"}, {**artifact, "path": "/absolute.py"},
            {**artifact, "content_base64": base64.b64encode(b"\x00binary").decode()},
            {**artifact, "content_base64": "invalid base64"}):
        assert sanitize_payload(invalid)["content_base64"] == OMITTED_EMBEDDED_CONTENT


def test_storage_failure_is_visible_without_replacing_the_operation(tmp_path, capsys):
    (tmp_path / "diagnostics").write_text("not a directory")
    recorder = _recorder(tmp_path)
    recorder.write_json("request.json", {"run_id": "run"})
    with recorder.open_stdio("stdout.log") as stream:
        stream.write(b"parser still receives stdout")
        stream.seek(0)
        assert stream.read() == b"parser still receives stdout"
    assert recorder.summary()["capture_errors"]
    assert "diagnostic capture failed" in capsys.readouterr().err


def test_failed_stream_fsync_still_closes_descriptor(tmp_path, monkeypatch):
    import os
    stream = _recorder(tmp_path).stream('response.body')
    stream.write(b'partial diagnostic')
    descriptor = stream._output.fileno()
    def fail(_fd):
        raise OSError('synthetic disk full')
    monkeypatch.setattr(os, 'fsync', fail)
    stream.close()
    assert not stream.summary()['complete']
    with pytest.raises(OSError): os.fstat(descriptor)


def test_host_exception_preserves_traceback_and_available_identity(tmp_path):
    from hypoloom.runtime_diagnostics import record_host_exception
    try:
        raise ValueError("before Attempt creation")
    except ValueError as exc:
        record_host_exception(tmp_path, exc, run_id="run-one", command_id="final-report", phase="tick")
    record = json.loads((tmp_path / "diagnostics/host.jsonl").read_text())
    assert record["run_id"] == "run-one"
    assert record["exception_type"] == "ValueError"
    assert "test_host_exception_preserves_traceback" in record["traceback"]
    assert record.get("attempt_id") is None


def test_diagnostics_cannot_follow_symlinks_out_of_its_root(tmp_path):
    recorder = _recorder(tmp_path)
    external = tmp_path / "outside"
    external.mkdir()
    (recorder.root / "escape").symlink_to(external, target_is_directory=True)
    recorder.write_json("escape/not-created.json", {"secret": "value"})
    assert not (external / "not-created.json").exists()
    assert recorder.summary()["capture_errors"]


def test_stream_retains_a_short_file_write(tmp_path, monkeypatch):
    recorder = _recorder(tmp_path)
    original_open = recorder._open

    class ShortWriter:
        def __init__(self, output):
            self.output = output

        def write(self, value):
            return self.output.write(value[:3])

        def fileno(self):
            return self.output.fileno()

        def close(self):
            return self.output.close()

    monkeypatch.setattr(recorder, "_open", lambda path: ShortWriter(original_open(path)))
    with recorder.stream("response.body") as stream:
        stream.write(b"whole response")
    assert (recorder.root / "response.body").read_bytes() == b"whole response"
    assert stream.summary()["bytes_written"] == len(b"whole response")


@pytest.mark.parametrize("media_type,content", [
    ("application/json", json.dumps({"nested": {"note": "known-provider-key-123"}})),
    ("application/vnd.hypoloom.input+json", json.dumps({"note": "known-provider-key-123"})),
    ("text/plain", "provider observed known-provider-key-123 failure"),
    ("application/json", json.dumps({"nested": {"authorization": "other-private-key"}})),
])
def test_typed_embedded_content_is_redacted_before_persisting(tmp_path, media_type, content):
    recorder = _recorder(tmp_path, secrets=("known-provider-key-123",))
    encoded = base64.b64encode(content.encode()).decode()
    recorder.write_json("request.json", {"input_artifacts": [{"media_type": media_type, "content_base64": encoded}]})
    value = json.loads((recorder.root / "request.json").read_text())
    decoded = base64.b64decode(value["input_artifacts"][0]["content_base64"], validate=True).decode()
    assert "known-provider-key-123" not in decoded
    assert "other-private-key" not in decoded
    assert "[REDACTED]" in decoded
    assert recorder.summary()["redacted"] is True


@pytest.mark.parametrize("content", ["  {\"note\": \"safe\"}  \n", "safe plaintext\n", "null"])
def test_safe_typed_content_keeps_exact_original_encoding(tmp_path, content):
    recorder = _recorder(tmp_path, secrets=("known-provider-key-123",))
    original = {"media_type": "application/json", "content_base64": base64.b64encode(content.encode()).decode()}
    recorder.write_json("request.json", original)
    assert json.loads((recorder.root / "request.json").read_text()) == original
    assert recorder.summary()["redacted"] is False


@pytest.mark.parametrize("media_type,encoded", [
    ("application/octet-stream", "aGVsbG8="), ("text/plain", "not base64"),
])
def test_opaque_or_invalid_embedded_content_is_explicitly_omitted(tmp_path, media_type, encoded):
    recorder = _recorder(tmp_path)
    recorder.write_json("request.json", {"media_type": media_type, "content_base64": encoded})
    value = json.loads((recorder.root / "request.json").read_text())
    assert value["content_base64"] == "[OMITTED: opaque or invalid embedded content]"
