"""Real CLI compatibility only: isolated Bash, fork/resume and structured output.

The provider is a deterministic local Unix-socket server.  A sandbox-local HTTP
bridge reaches that socket with the network namespace otherwise unshared.  This
does not qualify WorkflowHost experiment submission, recovery or publication.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import socketserver
import subprocess
import threading
import time
import uuid
from http.server import HTTPServer
from pathlib import Path
from typing import Any, Mapping

import pytest

from hypoloom import host_tools_server, provider_bridge
from hypoloom.agent_aci import WorkspaceArtifact
from hypoloom.host_tools_server import AgentToolServer
from hypoloom.provider_relay import ProviderRelay

from test_claude_file_tool_cli import (
    _handler_for,
    _installed_claude,
    _result_text,
    _tool_results,
    native_output_schema,
)


_MODEL = "claude-sonnet-4-6"
_SCHEMA = {
    "type": "object",
    "properties": {"stage": {"type": "string"}, "ok": {"type": "boolean"}},
    "required": ["stage", "ok"],
    "additionalProperties": False,
}


class _SessionScenario:
    def __init__(self) -> None:
        self.parent_context = "proposal-context-" + secrets.token_hex(12)
        self.chain_context = "chain-context-" + secrets.token_hex(12)
        self.sibling_context = "sibling-context-" + secrets.token_hex(12)
        self.stage = "seed"
        self.stage_step = 0
        self.requests: list[Mapping[str, Any]] = []
        self.errors: list[str] = []
        self.tool_evidence: dict[str, Mapping[str, Any]] = {}
        self.broken_source = (
            "def answer():\n    return 41\n\n"
            "if __name__ == '__main__':\n"
            "    assert answer() == 42, 'repair-required'\n"
            "    print('REPAIRED_TEST_PASSED')\n"
        )

    def record(self, document: Mapping[str, Any]) -> int:
        self.requests.append(document)
        self.stage_step += 1
        try:
            messages = json.dumps(document.get("messages"), ensure_ascii=True)
            assert self.parent_context in messages
            if self.stage in {"design", "analysis"}:
                assert self.chain_context in messages
                assert self.sibling_context not in messages
            elif self.stage == "sibling":
                assert self.chain_context not in messages
                assert self.sibling_context in messages
            names = {tool["name"] for tool in document.get("tools", [])}
            assert {
                "Read",
                "Write",
                "Edit",
                "Glob",
                "Grep",
                "Bash",
            } <= names
            native_output_schema(document)
            if self.stage == "design" and self.stage_step > 1:
                results = _tool_results(document)
                expected = (
                    "write_broken",
                    "test_failed",
                    "repair_source",
                    "test_passed",
                )[self.stage_step - 2]
                result = results[expected]
                self.tool_evidence[expected] = result
                if expected == "test_failed":
                    assert result.get("is_error") is True, result
                    assert "AssertionError: repair-required" in _result_text(result), (
                        result
                    )
                else:
                    assert result.get("is_error") is not True, result
                    if expected == "test_passed":
                        assert "REPAIRED_TEST_PASSED" in _result_text(result), result
            if self.stage == "analysis":
                assert "REPAIRED_TEST_PASSED" in messages
                assert "AssertionError: repair-required" in messages
        except (AssertionError, KeyError, IndexError) as exc:
            self.errors.append(f"{self.stage}/{self.stage_step}: {exc}")
        return len(self.requests)

    def tool_calls(
        self, _request_number: int
    ) -> tuple[tuple[str, str, Mapping[str, Any]], ...]:
        if self.stage == "design":
            steps = (
                (
                    "write_broken",
                    "Write",
                    {
                        "file_path": "/workspace/experiment.py",
                        "content": self.broken_source,
                    },
                ),
                (
                    "test_failed",
                    "Bash",
                    {"command": "python3 /workspace/experiment.py", "timeout": 10000},
                ),
                (
                    "repair_source",
                    "Edit",
                    {
                        "file_path": "/workspace/experiment.py",
                        "old_string": "return 41",
                        "new_string": "return 42",
                    },
                ),
                (
                    "test_passed",
                    "Bash",
                    {"command": "python3 /workspace/experiment.py", "timeout": 10000},
                ),
            )
            if self.stage_step <= len(steps):
                return (steps[self.stage_step - 1],)
        return (
            (
                "structured_" + self.stage,
                "StructuredOutput",
                {"stage": self.stage, "ok": True},
            ),
        )


def _runtime_mounts() -> list[tuple[Path, str]]:
    python = Path("/usr/bin/python3")
    if not python.is_file() or not Path("/usr/bin/bwrap").is_file():
        pytest.skip("this CLI compatibility lane needs system Python and bubblewrap")
    stdlib = subprocess.check_output(
        [str(python), "-c", "import sysconfig; print(sysconfig.get_path('stdlib'))"],
        text=True,
        timeout=10,
    ).strip()
    mounts = [(Path(stdlib), stdlib)]
    for location in ("/lib", "/lib64"):
        if Path(location).exists():
            mounts.append((Path(location), location))
    for name in (
        "bash",
        "env",
        "timeout",
        "uname",
        "cat",
        "sleep",
        "pwd",
        "head",
        "tail",
        "tr",
        "sed",
        "wc",
        "mkdir",
        "rm",
        "touch",
        "date",
        "which",
    ):
        source = Path("/usr/bin") / name
        if not source.exists():
            source = Path("/bin") / name
        if source.exists():
            mounts.append(
                (
                    source.resolve(),
                    "/bin/bash" if name == "bash" else "/usr/bin/" + name,
                )
            )
    mounts.append((python.resolve(), "/usr/bin/python3"))
    return mounts


def _invoke(
    tmp_path: Path,
    *,
    executable: Path,
    socket_path: Path,
    sessions: Path,
    workspace: Path,
    stage: str,
    session_arguments: tuple[str, ...],
    prompt: str,
    host_tools: AgentToolServer | None = None,
) -> tuple[str, list[Mapping[str, Any]]]:
    argv = [
        "/usr/bin/bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--tmpfs",
        "/home",
        "--dir",
        "/home/agent/.claude/projects",
        "--dir",
        "/runtime",
        "--dir",
        "/bin",
        "--dir",
        "/usr/bin",
        "--dir",
        "/usr/lib",
        "--ro-bind",
        str(executable),
        "/runtime/claude",
        "--ro-bind",
        str(Path(provider_bridge.__file__).resolve()),
        "/runtime/provider_bridge.py",
        "--ro-bind",
        str(socket_path),
        "/provider.sock",
        "--bind",
        str(sessions),
        "/home/agent/.claude/projects/-workspace",
        "--bind",
        str(workspace),
        "/workspace",
    ]
    mcp_config: dict[str, Any] = {"mcpServers": {}}
    if host_tools is not None:
        argv.extend(
            (
                "--ro-bind",
                str(Path(host_tools_server.__file__).resolve()),
                "/runtime/host_tools_server.py",
                "--ro-bind",
                str(host_tools.socket_path),
                "/host-tools.sock",
            )
        )
        mcp_config["mcpServers"]["host-tools"] = {
            "command": "/usr/bin/python3",
            "args": ["/runtime/host_tools_server.py", "--socket", "/host-tools.sock"],
        }
    for source, destination in _runtime_mounts():
        argv.extend(("--ro-bind", str(source), destination))
    argv.extend(("--symlink", "/bin/bash", "/bin/sh", "--chdir", "/workspace", "--"))
    argv.extend(
        (
            "/usr/bin/python3",
            "/runtime/provider_bridge.py",
            "--socket",
            "/provider.sock",
            "--",
            "/runtime/claude",
        )
    )
    argv.extend(
        (
            "--print",
            "--input-format",
            "text",
            "--output-format",
            "stream-json",
            "--verbose",
            "--json-schema",
            json.dumps(_SCHEMA),
            "--permission-mode",
            "acceptEdits",
            "--tools",
            "Read,Write,Edit,Glob,Grep,Bash",
            "--allowedTools",
            "Bash",
            "Read",
            "Write",
            "Edit",
            "Glob",
            "Grep",
            "mcp__host-tools__submit_experiment",
            "mcp__host-tools__wait_experiment",
            "mcp__host-tools__get_experiment_log",
            "mcp__host-tools__cancel_experiment",
            "mcp__host-tools__list_my_experiments",
            "--setting-sources",
            "",
            "--settings",
            "{}",
            "--disable-slash-commands",
            "--strict-mcp-config",
            "--mcp-config",
            json.dumps(mcp_config),
            "--max-turns",
            "12",
            "--max-budget-usd",
            "1",
            "--model",
            _MODEL,
            "--prompt-suggestions",
            "false",
            *session_arguments,
        )
    )
    environment = {
        "HOME": "/home/agent",
        "CLAUDE_CONFIG_DIR": "/home/agent/.claude",
        "PATH": "/runtime:/usr/bin:/bin",
        "SHELL": "/bin/bash",
        "TMPDIR": "/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "ANTHROPIC_API_KEY": "loopback-only-dummy-key",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_MAX_RETRIES": "0",
        "MAX_STRUCTURED_OUTPUT_RETRIES": "1",
    }
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 50
        pending_input: bytes | None = prompt.encode()
        while True:
            if host_tools is not None:
                host_tools.service()
            try:
                stdout, stderr = process.communicate(pending_input, timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                pending_input = None
                if time.monotonic() >= deadline:
                    raise
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate(timeout=5)
        pytest.fail(
            f"{stage} CLI timeout: {stdout.decode(errors='replace')} {stderr.decode(errors='replace')}"
        )
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    (tmp_path / f"{stage}.stdout.jsonl").write_bytes(stdout)
    (tmp_path / f"{stage}.stderr.txt").write_bytes(stderr)
    assert process.returncode == 0, (stage, stdout.decode(), stderr.decode())
    records = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    results = [record for record in records if record.get("type") == "result"]
    assert len(results) == 1, records
    result = results[0]
    assert result.get("subtype") == "success", result
    assert result.get("structured_output") == {"stage": stage, "ok": True}, result
    session_id = result["session_id"]
    assert (sessions / f"{session_id}.jsonl").is_file()
    return session_id, records


def test_real_claude_cli_forks_resumes_and_repairs_with_bash(tmp_path: Path) -> None:
    executable = _installed_claude()
    scenario = _SessionScenario()
    socket_path = tmp_path / "provider.sock"
    server = socketserver.UnixStreamServer(str(socket_path), _handler_for(scenario))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    parent_id, chain_id, sibling_id = (str(uuid.uuid4()) for _ in range(3))
    parent = tmp_path / "parent-sessions"
    chain = tmp_path / "chain-sessions"
    sibling = tmp_path / "sibling-sessions"
    workspace = tmp_path / "workspace"
    sibling_workspace = tmp_path / "sibling-workspace"
    for directory in (parent, chain, sibling, workspace, sibling_workspace):
        directory.mkdir()

    def invoke(
        stage: str,
        sessions: Path,
        arguments: tuple[str, ...],
        prompt: str,
        working: Path = workspace,
    ) -> str:
        scenario.stage, scenario.stage_step = stage, 0
        session_id, _records = _invoke(
            tmp_path,
            executable=executable,
            socket_path=socket_path,
            sessions=sessions,
            workspace=working,
            stage=stage,
            session_arguments=arguments,
            prompt=prompt,
        )
        assert scenario.errors == []
        return session_id

    try:
        assert (
            invoke(
                "seed",
                parent,
                ("--session-id", parent_id),
                "Remember this proposal context: " + scenario.parent_context,
            )
            == parent_id
        )
        parent_transcript = parent / f"{parent_id}.jsonl"
        original_parent = parent_transcript.read_bytes()
        for directory in (chain, sibling):
            shutil.copyfile(parent_transcript, directory / parent_transcript.name)
        assert (
            invoke(
                "design",
                chain,
                ("--resume", parent_id, "--fork-session", "--session-id", chain_id),
                "Implement and test this chain. Private context: "
                + scenario.chain_context,
            )
            == chain_id
        )
        assert "return 42" in (workspace / "experiment.py").read_text()
        assert (
            invoke(
                "sibling",
                sibling,
                ("--resume", parent_id, "--fork-session", "--session-id", sibling_id),
                "Assess a separate chain: " + scenario.sibling_context,
                sibling_workspace,
            )
            == sibling_id
        )
        assert (
            invoke(
                "analysis",
                chain,
                ("--resume", chain_id),
                "Analyze the earlier test failure and successful repair using this conversation.",
            )
            == chain_id
        )
        assert len(scenario.requests) == 8
        assert set(scenario.tool_evidence) == {
            "write_broken",
            "test_failed",
            "repair_source",
            "test_passed",
        }
        assert parent_transcript.read_bytes() == original_parent
        transcript = (chain / f"{chain_id}.jsonl").read_text()
        assert "repair-required" in transcript
        assert "REPAIRED_TEST_PASSED" in transcript
        assert scenario.sibling_context not in transcript
        sibling_transcript = (sibling / f"{sibling_id}.jsonl").read_text()
        assert scenario.chain_context not in sibling_transcript
        assert not (sibling_workspace / "experiment.py").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    "credential_relay", (False, True), ids=("direct-wire", "credential-relay")
)
def test_real_claude_cli_long_context_selector_uses_base_wire_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, credential_relay: bool
) -> None:
    # The configured CC selector is not necessarily the Anthropic JSON model.
    monkeypatch.setitem(globals(), "_MODEL", "claude-sonnet-4-6[1m]")
    scenario = _SessionScenario()
    headers: list[Mapping[str, str]] = []
    base_handler = _handler_for(scenario)

    class Handler(base_handler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib API
            headers.append(dict(self.headers))
            super().do_POST()

    socket_path = tmp_path / "provider.sock"
    server = (
        HTTPServer(("127.0.0.1", 0), Handler)
        if credential_relay
        else socketserver.UnixStreamServer(str(socket_path), Handler)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host_key = "host-only-context-key-" + secrets.token_hex(16)
    relay = (
        ProviderRelay(
            socket_path,
            base_url=f"http://127.0.0.1:{server.server_port}",
            provider_environment={"ANTHROPIC_API_KEY": host_key},
            model=_MODEL,
            max_request_bytes=4 * 1024 * 1024,
            max_response_bytes=1024 * 1024,
            max_requests=2,
            deadline_at=time.monotonic() + 30,
        )
        if credential_relay
        else None
    )
    sessions, workspace = tmp_path / "sessions", tmp_path / "workspace"
    sessions.mkdir()
    workspace.mkdir()
    try:
        _invoke(
            tmp_path,
            executable=_installed_claude(),
            socket_path=socket_path,
            sessions=sessions,
            workspace=workspace,
            stage="seed",
            session_arguments=("--session-id", str(uuid.uuid4())),
            prompt="Remember this context: " + scenario.parent_context,
        )
        assert not scenario.errors, scenario.errors
        assert len(scenario.requests) == 1
        assert scenario.requests[0]["model"] == "claude-sonnet-4-6"
        normalized_headers = {name.lower(): value for name, value in headers[0].items()}
        assert "context-1m-2025-08-07" in normalized_headers["anthropic-beta"].split(
            ","
        )
        if credential_relay:
            assert normalized_headers["x-api-key"] == host_key
            assert host_key not in (tmp_path / "seed.stdout.jsonl").read_text()
            assert host_key not in json.dumps(scenario.requests)
    finally:
        if relay is not None:
            relay.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class _McpScenario:
    def __init__(self) -> None:
        self.requests: list[Mapping[str, Any]] = []
        self.errors: list[str] = []

    def record(self, document: Mapping[str, Any]) -> int:
        self.requests.append(document)
        step = len(self.requests)
        try:
            if step == 1:
                names = {tool["name"] for tool in document.get("tools", [])}
                assert {
                    "mcp__host-tools__" + tool
                    for tool in (
                        "submit_experiment",
                        "wait_experiment",
                        "get_experiment_log",
                        "cancel_experiment",
                        "list_my_experiments",
                    )
                } <= names, names
            elif step == 2:
                result = _tool_results(document)["inspect_auth"]
                assert result.get("is_error") is not True, result
                assert provider_bridge.LOCAL_PROVIDER_KEY in _result_text(result), (
                    result
                )
                assert "None" in _result_text(result), result
            elif step in {3, 4}:
                result = _tool_results(document)[
                    "submit_local" if step == 3 else "wait_local"
                ]
                assert result.get("is_error") is not True, result
                assert "loopback-exp" in _result_text(result), result
                if step == 4:
                    assert "completed" in _result_text(result), result
            else:
                raise AssertionError("unexpected model request")
        except (AssertionError, KeyError) as exc:
            self.errors.append(f"MCP step {step}: {exc}")
        return step

    def tool_calls(self, step: int) -> tuple[tuple[str, str, Mapping[str, Any]], ...]:
        if step == 1:
            return (
                (
                    "inspect_auth",
                    "Bash",
                    {
                        "command": 'python3 -c \'import os; print(os.environ.get("ANTHROPIC_API_KEY")); print(os.environ.get("ANTHROPIC_AUTH_TOKEN"))\'',
                        "timeout": 10000,
                    },
                ),
            )
        if step == 2:
            return (
                (
                    "submit_local",
                    "mcp__host-tools__submit_experiment",
                    {"candidate": {"description": "loopback compatibility only"}},
                ),
            )
        if step == 3:
            return (
                (
                    "wait_local",
                    "mcp__host-tools__wait_experiment",
                    {"exp_id": "loopback-exp", "max_wait_seconds": 1},
                ),
            )
        return (("structured_mcp", "StructuredOutput", {"stage": "mcp", "ok": True}),)


def test_real_claude_cli_calls_scoped_host_mcp_and_waits_without_model_calls(
    tmp_path: Path,
) -> None:
    executable = _installed_claude()
    scenario = _McpScenario()
    socket_path = tmp_path / "provider.sock"
    host_key = "host-only-key-" + secrets.token_hex(16)
    base_handler = _handler_for(scenario)

    class AuthenticatedHandler(base_handler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            if self.headers.get("x-api-key") != host_key:
                scenario.errors.append(
                    "upstream did not receive the Host-held credential"
                )
            super().do_POST()

    provider = HTTPServer(("127.0.0.1", 0), AuthenticatedHandler)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    relay = ProviderRelay(
        socket_path,
        base_url=f"http://127.0.0.1:{provider.server_port}",
        provider_environment={"ANTHROPIC_API_KEY": host_key},
        model=_MODEL,
        max_request_bytes=4 * 1024 * 1024,
        max_response_bytes=1024 * 1024,
        max_requests=8,
        deadline_at=time.monotonic() + 50,
    )
    workspace, sessions = tmp_path / "workspace", tmp_path / "sessions"
    workspace.mkdir()
    sessions.mkdir()
    source = b"print('sandbox-source')\n"
    (workspace / "experiment.py").write_bytes(source)
    calls: list[tuple[str, str, Mapping[str, Any]]] = []
    polls = 0

    def handler(
        attempt_id: str, tool: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        nonlocal polls
        calls.append((attempt_id, tool, arguments))
        if tool == "submit_experiment":
            assert arguments["workspace_files"] == [
                WorkspaceArtifact.from_bytes("experiment.py", source).to_dict()
            ]
            return {"exp_id": "loopback-exp", "queue_position": 0}
        assert tool == "wait_experiment"
        polls += 1
        return {
            "exp_id": "loopback-exp",
            "status": "completed" if polls >= 3 else "running",
            "terminal": polls >= 3,
        }

    host_tools = AgentToolServer(
        tmp_path / "host-tools.sock",
        "attempt-loopback",
        handler,
        capture_workspace=lambda _candidate: [
            WorkspaceArtifact.from_bytes(
                "experiment.py", (workspace / "experiment.py").read_bytes()
            ).to_dict()
        ],
    )
    session_id = str(uuid.uuid4())
    try:
        actual_id, records = _invoke(
            tmp_path,
            executable=executable,
            socket_path=socket_path,
            sessions=sessions,
            workspace=workspace,
            stage="mcp",
            session_arguments=("--session-id", session_id),
            prompt="Submit the test candidate through Host tools, wait for it, and report completion.",
            host_tools=host_tools,
        )
        assert actual_id == session_id
        assert scenario.errors == []
        assert len(scenario.requests) == 4
        assert host_key not in json.dumps(scenario.requests)
        assert polls == 3
        assert len(calls) == 4
        assert all(attempt == "attempt-loopback" for attempt, _, _ in calls)
        assert any(record.get("type") == "result" for record in records)
        transcript = (sessions / f"{session_id}.jsonl").read_text()
        assert "mcp__host-tools__submit_experiment" in transcript
        assert "mcp__host-tools__wait_experiment" in transcript
        assert host_key not in transcript
        assert provider_bridge.LOCAL_PROVIDER_KEY in transcript
    finally:
        host_tools.close()
        relay.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
