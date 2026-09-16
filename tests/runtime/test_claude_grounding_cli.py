"""Actual Claude Grounding web MCP through production bwrap; all sources scripted."""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.server import HTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from hypoloom.agent_aci import AgentBudget
from hypoloom.claude_agent_host import _output_schema
from hypoloom.full_cc_runtime import full_cc_argv, full_cc_environment, full_cc_instruction
from hypoloom.host_tools_server import AgentToolServer
from hypoloom.provider_relay import ProviderRelay

from test_claude_file_tool_cli import _handler_for, _installed_claude, _result_text, _tool_results
from test_grounding_aci import grounding_exchange


class _GroundingScenario:
    def __init__(self, candidate: Mapping[str, Any], nonce: str) -> None:
        self.candidate, self.nonce = candidate, nonce
        self.requests: list[Mapping[str, Any]] = []
        self.errors: list[str] = []

    def record(self, document: Mapping[str, Any]) -> int:
        self.requests.append(document)
        step = len(self.requests)
        try:
            names = {tool["name"] for tool in document["tools"]}
            assert {name for name in names if name.startswith("mcp__")} == {
                "mcp__host-tools__web_search", "mcp__host-tools__web_fetch"}
            assert "Read" in names and "Bash" in names
            results = _tool_results(document)
            if step >= 2:
                assert not results["search_sources"].get("is_error")
                assert "https://example.org/benchmark" in _result_text(results["search_sources"])
            if step == 3:
                assert not results["fetch_source"].get("is_error")
                assert self.nonce in _result_text(results["fetch_source"])
        except (AssertionError, KeyError) as exc:
            self.errors.append(f"step {step}: {exc}")
        return step

    def tool_calls(self, step: int) -> tuple[tuple[str, str, Mapping[str, Any]], ...]:
        if self.errors:
            return ()
        if step == 1:
            return (("search_sources", "mcp__host-tools__web_search", {"query": "CPU benchmark", "max_results": 2}),)
        if step == 2:
            return (("fetch_source", "mcp__host-tools__web_fetch", {"url": "https://example.org/benchmark", "max_chars": 4096}),)
        return (("finish", "StructuredOutput", self.candidate),)


@pytest.mark.parametrize("version", (12, 13))
def test_grounding_schema_and_tool_scope_are_versioned(tmp_path: Path, version: int) -> None:
    request, _ = grounding_exchange(version)
    schema = _output_schema(request)["properties"]["typed_candidate"]
    if version == 13:
        assert set(schema["required"]) == {"task_kind", "supporting_refs", "background", "web_sources"}
        assert len(schema["properties"]["background"]["required"]) == 7
        assert "summary" not in schema["properties"]
        assert "web_search/web_fetch" in full_cc_instruction(request)
    else:
        assert set(schema["required"]) == {"task_kind", "summary", "supporting_refs"}
        assert "web_sources" not in schema["properties"]


def test_real_claude_uses_only_grounding_web_mcp_and_returns_structured_background(tmp_path: Path) -> None:
    executable = _installed_claude()
    request, response = grounding_exchange()
    request = replace(request, budget=AgentBudget(max_turns=8, max_tokens=20000,
        max_output_bytes=131072, wall_seconds=40, max_cost="1"))
    nonce = "scripted-public-source-" + secrets.token_hex(12)
    scenario = _GroundingScenario({"typed_candidate": response.typed_candidate,
        "declared_effects": [{"effect": request.effect_requirements[0]}]}, nonce)
    provider = HTTPServer(("127.0.0.1", 0), _handler_for(scenario))
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    key = "host-only-grounding-key-" + secrets.token_hex(12)
    relay = ProviderRelay(tmp_path / "provider.sock", base_url=f"http://127.0.0.1:{provider.server_port}",
        provider_environment={"ANTHROPIC_API_KEY": key}, model="claude-sonnet-4-6",
        max_request_bytes=4*1024*1024, max_response_bytes=1024*1024,
        max_requests=8, deadline_at=time.monotonic()+45)
    handled = []
    owner = threading.get_ident()
    def handler(attempt: str, tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        assert attempt == request.attempt_id and threading.get_ident() == owner
        handled.append((tool, dict(arguments)))
        if tool == "web_search":
            return {"results": [{"url": "https://example.org/benchmark", "title": "CPU benchmark", "snippet": "Scripted source"}], "source_is_untrusted": True}
        assert tool == "web_fetch"
        return {"url": arguments["url"], "text": nonce, "truncated": False, "source_is_untrusted": True}
    endpoint = AgentToolServer(tmp_path / "tools.sock", request.attempt_id, handler, scope="grounding")
    session_id = str(uuid.uuid4())
    workspace = SimpleNamespace(state_root=tmp_path / "host-state", session_root=tmp_path / "sessions",
        worktree_root=tmp_path / "workspace", input_root=tmp_path / "inputs", writable=False,
        session_arguments=("--session-id", session_id))
    for path in (workspace.state_root, workspace.session_root, workspace.worktree_root, workspace.input_root):
        path.mkdir()
    (workspace.worktree_root / ".git").write_text("gitdir: /host-git-not-mounted\n")
    argv = full_cc_argv(bwrap=Path("/usr/bin/bwrap"), executable=executable,
        request=request, model="claude-sonnet-4-6", workspace=workspace,
        provider_socket=relay.socket_path, host_socket=endpoint.socket_path,
        output_schema=_output_schema(request), extra_paths=())
    (tmp_path / "argv.json").write_text(json.dumps(argv))
    process = None
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=full_cc_environment(), start_new_session=True)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(process.communicate, b"Inspect and retrieve the source, then return the complete Grounding background.", timeout=45)
            deadline = time.monotonic() + 45
            while not future.done() and time.monotonic() < deadline:
                endpoint.service()
                time.sleep(0.005)
            stdout, stderr = future.result(timeout=1)
        (tmp_path / "stdout.jsonl").write_bytes(stdout)
        (tmp_path / "stderr.txt").write_bytes(stderr)
        assert process.returncode == 0, (stdout.decode(), stderr.decode())
        assert not scenario.errors, scenario.errors
        assert len(scenario.requests) == 3
        assert [tool for tool, _ in handled] == ["web_search", "web_fetch"]
        result = next(json.loads(line) for line in stdout.splitlines() if json.loads(line).get("type") == "result")
        assert result["structured_output"] == scenario.candidate
        transcript = (workspace.session_root / f"{session_id}.jsonl").read_bytes()
        assert nonce.encode() in transcript and key.encode() not in transcript
        assert b"mcp__host-tools__web_search" in transcript and b"mcp__host-tools__web_fetch" in transcript
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=5)
        endpoint.close()
        relay.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
