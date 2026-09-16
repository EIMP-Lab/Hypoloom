"""Zero-cost Claude Code CLI qualification for the DRAFT file-tool boundary.

The real installed CLI runs through the production bubblewrap adapter, but its
Anthropic endpoint is a loopback-only deterministic SSE server.  No provider or
durable WorkflowHost run participates in this test.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from hypoloom.agent_aci import AgentBudget, AgentCommand, AgentRequest, AgentResponse
from hypoloom.claude_agent_host import (
    CLAUDE_AGENT_HOST_VERSION,
    CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
    AgentHostTerminalReason,
    ClaudeAgentHost,
    process_group_exists,
)
from hypoloom.execution_workspace import ExecutionWorkspace, GitTargetBinding
from hypoloom.fake_agent import FakeAgent, default_request


_MODEL = "claude-sonnet-4-6"
_EXECUTABLE_ENV = "HYPOLOOM_CLAUDE_EXECUTABLE"


def _installed_claude() -> Path:
    configured = os.environ.get(_EXECUTABLE_ENV)
    discovered = configured or shutil.which("claude")
    if discovered is None:
        pytest.skip("the local Claude Code executable is not installed")
    executable = Path(discovered).resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        pytest.skip("the local Claude Code executable is not a real executable file")
    return executable


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, (arguments, result.stdout, result.stderr)
    return result.stdout.strip()


def _tool_results(document: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    found: dict[str, Mapping[str, Any]] = {}
    messages = document.get("messages")
    if not isinstance(messages, list):
        return found
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, Mapping) or block.get("type") != "tool_result":
                continue
            tool_use_id = block.get("tool_use_id")
            if isinstance(tool_use_id, str):
                found[tool_use_id] = block
    return found


def _result_text(result: Mapping[str, Any]) -> str:
    """Read tool bytes, excluding separate CLI-added budget annotations."""
    content = result.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(block.get("text"))
        for block in content
        if isinstance(block, Mapping) and isinstance(block.get("text"), str)
        and not (block["text"].strip().startswith("<system-reminder>")
                 and block["text"].strip().endswith("</system-reminder>"))
    )


def native_output_schema(document: Mapping[str, Any]) -> Mapping[str, Any]:
    """Read the actual native tool schema or the full-CC prompt's JSON schema."""
    tools = [tool for tool in document.get("tools", []) if tool.get("name") == "StructuredOutput"]
    if tools:
        assert len(tools) == 1
        return tools[0]["input_schema"]
    prompt = native_prompt(document)
    instruction = prompt["instruction"]
    parts = instruction.split("\nHYPOLOOM_OUTPUT_SCHEMA_JSON\n")
    assert len(parts) == 2, "actual full-CC prompt lacks one output schema"
    parts = parts[1].split("\nEND_HYPOLOOM_OUTPUT_SCHEMA_JSON\n")
    assert len(parts) == 2, "actual full-CC prompt lacks schema terminator"
    schema = json.loads(parts[0])
    assert isinstance(schema, dict)
    return schema


def native_prompt(document):
    for message in reversed(document.get("messages", [])):
        if message.get("role") != "user":
            continue
        content = message.get("content", [])
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        for block in reversed(content):
            text = block.get("text", "")
            start = text.find('{"agent_request_metadata":')
            if start < 0:
                continue
            prompt, _ = json.JSONDecoder().raw_decode(text[start:])
            return prompt
    return {}


def displayed_candidate(document, candidate):
    """Scripted provider selects references from the actual received prompt."""
    inverse = {ref: alias for alias, ref in native_prompt(document).get("ref_aliases", {}).items()}
    def visit(value):
        if isinstance(value, dict):
            return {key: [inverse.get(ref, ref) for ref in item] if key == "supporting_refs" and isinstance(item, list)
                else visit(item) for key, item in value.items()}
        if isinstance(value, list):
            return [visit(item) for item in value]
        return value
    return visit(candidate)


class _ToolScenario:
    def __init__(self, *, nonce: str, source: str, structured_output: Mapping[str, Any]):
        self.nonce = nonce
        self.source = source
        self.structured_output = dict(structured_output)
        self.requests: list[Mapping[str, Any]] = []
        self.errors: list[str] = []

    def record(self, document: Mapping[str, Any]) -> int:
        self.requests.append(document)
        step = len(self.requests)
        try:
            if step == 1:
                tools = document.get("tools")
                names = {
                    str(tool.get("name"))
                    for tool in tools if isinstance(tool, Mapping)
                } if isinstance(tools, list) else set()
                expected = {"Read", "Glob", "Grep", "Edit", "Write", "StructuredOutput"}
                assert expected <= names, names
                assert not ({"Bash", "WebFetch", "WebSearch", "Agent"} & names), names
            elif step == 2:
                results = _tool_results(document)
                target = results["read_target"]
                assert target.get("is_error") is not True, target
                assert self.nonce in _result_text(target), target
                self._assert_denied(results["read_config"])
                self._assert_denied(results["read_proc"])
            elif step == 3:
                results = _tool_results(document)
                self._assert_denied(results["edit_target"])
                self._assert_denied(results["write_target"])
                self._assert_denied(results["write_workspace_extra"])
                workspace = results["write_workspace"]
                assert workspace.get("is_error") is not True, workspace
            else:
                raise AssertionError(f"unexpected provider request {step}")
        except (AssertionError, KeyError) as exc:
            self.errors.append(f"step {step}: {exc}")
        return step

    @staticmethod
    def _assert_denied(result: Mapping[str, Any]) -> None:
        assert result.get("is_error") is True, result
        assert "denied" in _result_text(result).casefold(), result

    def tool_calls(self, step: int) -> tuple[tuple[str, str, Mapping[str, Any]], ...]:
        if step == 1:
            return (
                ("read_target", "Read", {"file_path": "/target/canary.py"}),
                ("read_config", "Read", {"file_path": "/config/settings.json"}),
                ("read_proc", "Read", {"file_path": "/proc/self/status"}),
            )
        if step == 2:
            return (
                (
                    "edit_target",
                    "Edit",
                    {
                        "file_path": "/target/canary.py",
                        "old_string": self.nonce,
                        "new_string": "MUTATED",
                    },
                ),
                (
                    "write_target",
                    "Write",
                    {"file_path": "/target/forbidden.py", "content": "MUTATED\n"},
                ),
                (
                    "write_workspace_extra",
                    "Write",
                    {
                        "file_path": "/workspace/extra.py",
                        "content": "MUTATED\n",
                    },
                ),
                (
                    "write_workspace",
                    "Write",
                    {
                        "file_path": "/workspace/experiment.py",
                        "content": self.source,
                    },
                ),
            )
        if step == 3:
            return (("structured", "StructuredOutput", self.structured_output),)
        return ()


def _send_event(handler: BaseHTTPRequestHandler, event: str, data: Mapping[str, Any]) -> None:
    payload = json.dumps(data, ensure_ascii=True, separators=(",", ":"))
    handler.wfile.write(f"event: {event}\ndata: {payload}\n\n".encode("utf-8"))
    handler.wfile.flush()


def _send_response(
    handler: BaseHTTPRequestHandler,
    *,
    request_number: int,
    model: str,
    tool_calls: Sequence[tuple[str, str, Mapping[str, Any]]],
) -> None:
    final_text = (getattr(handler, "structured_output_as_text", False)
                  and len(tool_calls) == 1 and tool_calls[0][1] == "StructuredOutput")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.end_headers()
    _send_event(
        handler,
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": f"msg_loopback_{request_number}",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        },
    )
    for index, (tool_use_id, name, tool_input) in enumerate(tool_calls):
        _send_event(
            handler,
            "content_block_start",
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {"type": "text", "text": ""} if final_text else {
                    "type": "tool_use",
                    "id": tool_use_id,
                    "name": name,
                    "input": {},
                },
            },
        )
        _send_event(
            handler,
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "text_delta", "text": json.dumps(tool_input, ensure_ascii=True, separators=(",", ":"))} if final_text else {
                    "type": "input_json_delta",
                    "partial_json": json.dumps(
                        tool_input, ensure_ascii=True, separators=(",", ":")
                    ),
                },
            },
        )
        _send_event(
            handler,
            "content_block_stop",
            {"type": "content_block_stop", "index": index},
        )
    _send_event(
        handler,
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn" if final_text else "tool_use", "stop_sequence": None},
            "usage": {"output_tokens": max(1, len(tool_calls))},
        },
    )
    _send_event(handler, "message_stop", {"type": "message_stop"})


def _handler_for(scenario: _ToolScenario) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_arguments: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            try:
                length = int(self.headers.get("Content-Length", "0"))
                document = json.loads(self.rfile.read(length))
                assert isinstance(document, Mapping)
                if self.path.split("?", 1)[0] == "/v1/messages/count_tokens":
                    # Native Read asks the provider to count a large file's
                    # text before returning it. This is not a model turn and
                    # intentionally has no Hypoloom AgentRequest prompt.
                    counted = getattr(scenario, "count_token_requests", None)
                    if counted is None:
                        scenario.count_token_requests = counted = []
                    counted.append(document)
                    # A deterministic scripted-provider estimate, not a real
                    # tokenizer/pricing claim. Preserve the complete request.
                    tokens = max(1, (len(json.dumps(document.get("messages", []))) + 3) // 4)
                    response = json.dumps({"input_tokens": tokens}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(response)))
                    self.end_headers()
                    self.wfile.write(response)
                    return
                step = scenario.record(document)
                calls = scenario.tool_calls(step)
                calls = tuple((identity, name, displayed_candidate(document, candidate) if name == "StructuredOutput" else candidate)
                    for identity, name, candidate in calls)
                if not calls:
                    self.send_error(500, "unexpected request")
                    return
                model = document.get("model")
                assert isinstance(model, str)
                self.structured_output_as_text = not any(
                    tool.get("name") == "StructuredOutput" for tool in document.get("tools", []))
                _send_response(
                    self,
                    request_number=step,
                    model=model,
                    tool_calls=calls,
                )
            except Exception as exc:  # keep the client bounded, report on the test thread
                scenario.errors.append(f"server: {type(exc).__name__}: {exc}")
                try:
                    self.send_error(500, "loopback provider failure")
                except OSError:
                    pass

    return Handler


def _draft_request() -> tuple[AgentRequest, Mapping[str, Any]]:
    base = default_request(AgentCommand.DRAFT_EXPERIMENT_SPEC)
    generated = FakeAgent().invoke(base)
    assert isinstance(generated, AgentResponse)
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    request = AgentRequest.from_dict(
        {
            **base.to_dict(),
            "tool_permission_profile": CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
            "budget": AgentBudget(
                # Claude Code accounts each parallel tool-use block as a turn;
                # this deterministic exchange uses eight tools plus the final
                # StructuredOutput call while making only three API requests.
                max_turns=9,
                max_tokens=4_096,
                max_output_bytes=65_536,
                wall_seconds=25,
                max_cost="1",
            ).to_dict(),
            "deadline": deadline.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "required_model_identity": _MODEL,
            "required_adapter_version": CLAUDE_AGENT_HOST_VERSION,
        }
    )
    structured_output = {
        "typed_candidate": generated.typed_candidate,
        "declared_effects": [{"effect": effect.effect} for effect in generated.declared_effects],
    }
    return request, structured_output


def test_real_claude_cli_enforces_draft_file_tool_boundary(tmp_path: Path) -> None:
    executable = _installed_claude()
    settings = tmp_path / "settings.json"
    settings.write_text("{}\n", encoding="utf-8")
    settings.chmod(0o600)

    nonce = f"target-{secrets.token_hex(16)}"
    target = tmp_path / "target-checkout"
    target.mkdir()
    target_file = target / "canary.py"
    target_file.write_text(f'TARGET_NONCE = "{nonce}"\n', encoding="utf-8")
    _git(target, "init", "-q")
    _git(target, "add", "canary.py")
    _git(
        target,
        "-c",
        "user.name=Hypoloom Loopback",
        "-c",
        "user.email=loopback@invalid",
        "commit",
        "-qm",
        "loopback target",
    )
    commit = _git(target, "rev-parse", "HEAD")
    original = target_file.read_bytes()

    request, structured_output = _draft_request()
    source = (
        f'TARGET_NONCE = "{nonce}"\n'
        "CONFIG_READ_DENIED = True\n"
        "PROC_READ_DENIED = True\n"
        "TARGET_EDIT_DENIED = True\n"
        "TARGET_WRITE_DENIED = True\n"
    )
    scenario = _ToolScenario(
        nonce=nonce,
        source=source,
        structured_output=structured_output,
    )
    server = HTTPServer(("127.0.0.1", 0), _handler_for(scenario))
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    state_root = tmp_path / "state"
    artifact_root = state_root / "artifacts"
    artifact_root.mkdir(parents=True)
    owner = ExecutionWorkspace(artifact_root, state_root=state_root)
    workspace = owner.prepare_agent_workspace(
        attempt_id=request.attempt_id,
        target_binding=GitTargetBinding(checkout=target.resolve(), commit=commit),
    )
    adapter = ClaudeAgentHost(
        claude_executable=executable,
        settings_path=settings,
    )
    handle = None
    try:
        handle = adapter.start(
            request,
            model=_MODEL,
            provider_environment={
                "ANTHROPIC_API_KEY": "loopback-only-dummy-key",
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
            },
            bind_process_group=lambda _binding: None,
            tool_workspace=workspace,
        )
        assert "--safe-mode" in handle.launch_argv
        assert "--bare" not in handle.launch_argv

        observation = adapter.wait(handle)
    finally:
        if handle is not None:
            adapter.cleanup(handle)
        workspace.cleanup()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)

    assert scenario.errors == []
    assert len(scenario.requests) == 3
    assert observation.terminal_reason is AgentHostTerminalReason.SUCCEEDED, (
        observation.failure_code,
        observation.diagnostics,
    )
    assert observation.response is not None
    artifacts = observation.response.proposed_workspace_artifacts
    assert len(artifacts) == 1
    assert artifacts[0].path == "experiment.py"
    assert artifacts[0].content_bytes() == source.encode("utf-8")
    compile(artifacts[0].content_bytes(), "experiment.py", "exec")
    assert target_file.read_bytes() == original
    assert _git(target, "rev-parse", "HEAD") == commit
    assert _git(target, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert not workspace.attempt_root.exists()
    assert not process_group_exists(handle.process_group_id)
