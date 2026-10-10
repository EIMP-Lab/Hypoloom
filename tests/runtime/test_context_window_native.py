"""Actual CC reports plus real Host/relay/tools/CPU closure, all loopback."""
import json
import subprocess
import threading

import pytest

from hypoloom.context_management import context_policy
from hypoloom.context_window import resolve_window
from hypoloom.agent_aci import AgentRequest
from test_claude_file_tool_cli import _installed_claude, _send_response
from test_context_window import MAX, DISABLE, COMPACT


@pytest.mark.parametrize("model,environment,expected", [
    ("glm-5.3-flash", {MAX: "1000000", COMPACT: "1000000"}, 1000000),
    ("mimo-v2.6-pro", {MAX: "1000000"}, 1000000),
    ("deepseek-v4-pro", {MAX: "1000000"}, 1000000),
    ("k3-256k", {MAX: "262144", COMPACT: "1000000"}, 262144),
    ("k3[1m]", {MAX: "262144"}, 1000000),
    ("kimi-for-coding[1M]", {MAX: "262144"}, 1000000),
    ("k3[1m]", {MAX: "262144", DISABLE: "1"}, 262144),
    ("claude-sonnet-4-6", {MAX: "1000000"}, 200000),
    ("claude-opus-4-8", {}, 1000000),
    ("claude-opus-4-8", {DISABLE: "1"}, 200000),
    ("k3[1m]", {MAX: "262144", "DISABLE_COMPACT": "1"}, 262144),
    ("claude-private@20250101", {MAX: "1000000"}, 200000),
    ("claude-fable-5", {}, 1000000),
])
def test_actual_cc_window_matches_resolved_controls(tmp_path, model, environment, expected):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.structured_output_as_text = True
            _send_response(self, request_number=1, model=body["model"],
                tool_calls=(("local-answer", "StructuredOutput", {"ok": True}),))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
    thread.start()
    try:
        window = resolve_window(model, {}, environment, {"context_metadata": False})
        result = subprocess.run([str(_installed_claude()), "--bare", "--print", "--verbose", "--output-format", "stream-json",
            "--model", model, "--setting-sources", "", "--settings", "{}", "--no-session-persistence", "--max-turns", "2", "local fixture"],
            env={"HOME": str(tmp_path), "CLAUDE_CONFIG_DIR": str(tmp_path), "PATH": "/usr/bin:/bin",
                "ANTHROPIC_API_KEY": "loopback-only", "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1", **window["cc_environment"]},
            cwd=tmp_path, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        final = next(row for row in reversed(rows) if row.get("type") == "result")
        windows = [usage["contextWindow"] for usage in final["modelUsage"].values()]
        assert windows == [expected]
        assert window["cc_context_tokens"] == expected
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def test_imported_window_reaches_real_isolated_cc_and_cpu_closure(tmp_path, monkeypatch):
    import test_full_cc_host_g3 as native
    from hypoloom.oracle import Oracle
    original = native._new_host
    monkeypatch.setattr(native, "_MODEL", "glm-5.3-flash")
    def configured(*args, **kwargs):
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"env": {MAX: "1000000", COMPACT: "1000000",
            "UNRELATED_PRIVATE_CONTEXT_TEST": "must-not-enter-agent"}}))
        return original(*args, **kwargs)
    monkeypatch.setattr(native, "_new_host", configured)
    native._exercise_full_cc_host(tmp_path, timeout_seconds=240)
    observations = [json.loads(row["observation_json"]) for row in native._rows(
        tmp_path / "state/state.sqlite", "SELECT * FROM execution_attempts WHERE kind='AGENT'")]
    assert observations
    for observation in observations:
        request = AgentRequest.from_dict(observation["request"])
        assert context_policy(request)["effective_context_tokens"] == 1000000
        assert observation["diagnostics"]["context"]["effective_context_tokens"] == 1000000
        stdout = tmp_path / "state/diagnostics/agents" / request.run_id / request.attempt_id / "stdout.log"
        rows = [json.loads(line) for line in stdout.read_text().splitlines()]
        final = next(row for row in reversed(rows) if row.get("type") == "result")
        assert {entry["contextWindow"] for entry in final["modelUsage"].values()} == {1000000}
        assert "must-not-enter-agent" not in json.dumps(observation)
    audit = Oracle(tmp_path / "state/state.sqlite", tmp_path / "state/artifacts").audit("full-cc-host-g3")
    assert audit.ok, audit.to_dict()
    assert audit.open_attempt_count == audit.pending_effect_count == 0
    assert not audit.owned_process_residue


def test_reader_window_inheritance_through_real_host_and_cpu(tmp_path, monkeypatch):
    import test_native_paper_reading as native
    import test_full_cc_host_g3 as host_native
    model = "kimi-for-coding[1M]"
    monkeypatch.setattr(host_native, "_MODEL", model)
    original_host, original_payload = native._new_host, native._payload
    def configured(*args, **kwargs):
        (tmp_path / "settings.json").write_text(json.dumps({"env": {MAX: "1000000"}}))
        return original_host(*args, **kwargs)
    def payload(*args, **kwargs):
        value = original_payload(*args, **kwargs)
        product = value["research_contract"]["methodology"]["controls"]["product"]
        product["experts"]["defaults"]["model"] = model
        product["methodology"]["memory"] = {
            "working_context_tokens": 180000, "context_metadata": False}
        return value
    monkeypatch.setattr(native, "_new_host", configured)
    monkeypatch.setattr(native, "_payload", payload)
    native.test_native_three_readers_original_sources_parent_collects_and_cpu_closes(
        tmp_path, monkeypatch, False, False, False, False)
    import sqlite3
    with sqlite3.connect(tmp_path / "state/state.sqlite") as db:
        observations = [json.loads(row[0]) for row in db.execute(
            "SELECT observation_json FROM execution_attempts WHERE kind='AGENT'")]
    readers = [row for row in observations if row["request"]["command"] == "PAPER_READ"]
    assert len(readers) == 3
    for row in readers:
        request = AgentRequest.from_dict(row["request"])
        policy = context_policy(request)
        assert policy["cc_context_tokens"] == 1000000
        assert policy["effective_context_tokens"] == policy["working_context_tokens"] == 180000
        stdout = tmp_path / "state/diagnostics/agents" / request.run_id / request.attempt_id / "stdout.log"
        final = next(value for value in reversed([json.loads(line) for line in stdout.read_text().splitlines()]) if value.get("type") == "result")
        assert {value["contextWindow"] for value in final["modelUsage"].values()} == {1000000}
