"""Native Claude plugin/Skill compatibility, real CLI and loopback only."""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import replace
from http.server import HTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from hypoloom.agent_aci import AgentBudget, EmbeddedInput
from hypoloom.expert_plugin import ExpertPluginStore
from hypoloom.fake_agent import default_request
from hypoloom.full_cc_runtime import full_cc_argv, full_cc_environment
from hypoloom.provider_relay import ProviderRelay

from test_claude_file_tool_cli import (
    _handler_for,
    _installed_claude,
    _result_text,
    _tool_results,
)


_MODEL = "claude-sonnet-4-6"
_SCHEMA = {
    "type": "object",
    "properties": {"plugin_loaded": {"type": "boolean"}},
    "required": ["plugin_loaded"],
    "additionalProperties": False,
}


class _PluginScenario:
    def __init__(self, role: str, nonce: str, effort: str) -> None:
        self.role, self.nonce = role, nonce
        self.effort = effort
        self.skill_name = f"hypoloom-{role}:experiment-report"
        self.requests: list[Mapping[str, Any]] = []
        self.errors: list[str] = []

    def record(self, document: Mapping[str, Any]) -> int:
        self.requests.append(document)
        step = len(self.requests)
        try:
            assert "Skill" in {tool["name"] for tool in document["tools"]}
            assert document.get("output_config", {}).get("effort") == self.effort, (
                document.get("output_config")
            )
            rendered = json.dumps(document)
            results = _tool_results(document)
            if step == 1:
                assert self.skill_name in rendered
                assert self.nonce not in rendered, (
                    "skill body was injected before native invocation"
                )
            elif step == 2:
                assert results["invoke_skill"].get("is_error") is not True, results
                assert self.nonce in rendered, "native Skill did not load its body"
            elif step == 3:
                assert results["apply_skill"].get("is_error") is not True, results
                assert self.nonce in _result_text(results["apply_skill"])
            else:
                raise AssertionError("unexpected provider request")
        except (AssertionError, KeyError) as exc:
            self.errors.append(f"step {step}: {exc}")
        return step

    def tool_calls(self, step: int) -> tuple[tuple[str, str, Mapping[str, Any]], ...]:
        if self.errors:
            return ()
        if step == 1:
            return (("invoke_skill", "Skill", {"skill": self.skill_name}),)
        if step == 2:
            script = (
                "import errno, os\nfrom pathlib import Path\n"
                "path = Path('/expert-plugin/skills/experiment-report/SKILL.md')\n"
                f"assert {self.nonce!r} in path.read_text()\n"
                "try:\n"
                "    fd = os.open(path, os.O_WRONLY | os.O_APPEND)\n"
                "except OSError as error:\n"
                "    assert error.errno == errno.EROFS, error\n"
                "else:\n"
                "    os.close(fd)\n"
                "    raise AssertionError('plugin unexpectedly writable')\n"
                "assert not Path('/other-expert-plugin').exists()\n"
                f"Path('/workspace/plugin-report.txt').write_text({self.nonce!r})\n"
                f"print({self.nonce!r})\n"
            )
            return (
                (
                    "apply_skill",
                    "Bash",
                    {"command": "python3 - <<'PY'\n" + script + "PY", "timeout": 10000},
                ),
            )
        return (("finish", "StructuredOutput", {"plugin_loaded": True}),)


@pytest.mark.parametrize(
    "role,effort,model,rejected",
    (
        ("alpha", "low", _MODEL, False),
        ("beta", "medium", _MODEL, False),
        ("gamma", "high", _MODEL, False),
        ("beta", "max", _MODEL, False),
        ("alpha", "xhigh", _MODEL, True),
        ("alpha", "xhigh", "claude-opus-4-6", True),
        ("beta", "xhigh", "claude-opus-4-7", False),
    ),
)
def test_native_plugin_skill_is_invoked_by_real_sandboxed_cli(
    tmp_path: Path, role: str, effort: str, model: str, rejected: bool
) -> None:
    executable = _installed_claude()
    nonce = "native-skill-body-" + secrets.token_hex(12)
    scenario = _PluginScenario(role, nonce, effort)
    store = ExpertPluginStore(tmp_path / "plugin-state")
    bindings = {
        expert: store.ensure(
            "plugin-run",
            expert,
            nonce if expert == role else f"Private {expert} focus must not leak",
        )
        for expert in ("alpha", "beta", "gamma")
    }
    snapshot = store.read(
        "plugin-run",
        role,
        commit=bindings[role].commit,
        attempt_id="plugin-probe",
        max_bytes=131072,
    )
    plugin = snapshot.root
    provider = HTTPServer(("127.0.0.1", 0), _handler_for(scenario))
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    key = "host-only-plugin-key-" + secrets.token_hex(12)
    relay = ProviderRelay(
        tmp_path / "provider.sock",
        base_url=f"http://127.0.0.1:{provider.server_port}",
        provider_environment={"ANTHROPIC_API_KEY": key},
        model=model,
        expected_effort=effort,
        max_request_bytes=4 * 1024 * 1024,
        max_response_bytes=1024 * 1024,
        max_requests=8,
        deadline_at=time.monotonic() + 45,
    )
    session_id = str(uuid.uuid4())
    workspace = SimpleNamespace(
        state_root=tmp_path / "host-state",
        session_root=tmp_path / "sessions",
        worktree_root=tmp_path / "workspace",
        input_root=tmp_path / "inputs",
        plugin_root=plugin,
        writable=True,
        session_arguments=("--session-id", session_id),
    )
    for path in (
        workspace.state_root,
        workspace.session_root,
        workspace.worktree_root,
        workspace.input_root,
    ):
        path.mkdir()
    (workspace.worktree_root / ".git").write_text("gitdir: /host-git-not-mounted\n")
    request = replace(
        default_request(),
        input_artifacts=(
            EmbeddedInput.from_bytes(
                "plugin-probe--expert-config",
                "application/json",
                json.dumps({"node_runtime": {"effort": effort}}).encode(),
            ),
        ),
        immutable_context_refs=("plugin-probe--expert-config",),
        budget=AgentBudget(
            max_turns=8,
            max_tokens=20000,
            max_output_bytes=131072,
            wall_seconds=40,
            max_cost="1",
        ),
    )
    argv = list(
        full_cc_argv(
            bwrap=Path("/usr/bin/bwrap"),
            executable=executable,
            request=request,
            model=model,
            workspace=workspace,
            provider_socket=relay.socket_path,
            host_socket=None,
            output_schema=_SCHEMA,
            extra_paths=(),
        )
    )
    assert "Skill" in argv[argv.index("--tools") + 1].split(",")
    assert "Skill" in argv
    assert "--disable-slash-commands" not in argv
    assert argv[argv.index("--plugin-dir") + 1] == "/expert-plugin"
    assert argv[argv.index("--effort") + 1] == effort
    (tmp_path / "argv.json").write_text(json.dumps(argv))
    process = None
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=full_cc_environment(),
            start_new_session=True,
        )
        stdout, stderr = process.communicate(
            f"Use the native {scenario.skill_name} skill, follow its instructions, then finish.".encode(),
            timeout=45,
        )
        (tmp_path / "stdout.jsonl").write_bytes(stdout)
        (tmp_path / "stderr.txt").write_bytes(stderr)
        if rejected:
            assert process.returncode != 0, (stdout.decode(), stderr.decode())
            assert not scenario.requests, "effort mismatch reached the upstream provider"
            assert b"provider relay effort mismatch" in stdout + stderr
            assert key.encode() not in stdout + stderr
            return
        assert not scenario.errors, scenario.errors
        assert process.returncode == 0, (stdout.decode(), stderr.decode())
        results = [
            json.loads(line)
            for line in stdout.splitlines()
            if json.loads(line).get("type") == "result"
        ]
        assert len(results) == 1 and results[0].get("subtype") == "success", results
        assert results[0]["structured_output"] == {"plugin_loaded": True}
        assert len(scenario.requests) == 3
        assert (workspace.worktree_root / "plugin-report.txt").read_text() == nonce
        transcript = (workspace.session_root / f"{session_id}.jsonl").read_text()
        assert '"name":"Skill"' in transcript
        assert scenario.skill_name in transcript and nonce in transcript
        assert key not in transcript
        for expert in bindings:
            if expert != role:
                assert f"Private {expert} focus must not leak" not in transcript
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=5)
        relay.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
        snapshot.close()
