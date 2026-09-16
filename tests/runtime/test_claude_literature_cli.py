"""Native scoped literature web tools; not WorkflowHost literature closure."""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.server import HTTPServer
from pathlib import Path

import pytest

from hypoloom.agent_aci import AgentBudget, EmbeddedInput, LiteratureSummaryCandidate, validate_postflight
from hypoloom.claude_agent_host import _output_schema, _provider_response
from hypoloom.full_cc_runtime import full_cc_argv, full_cc_environment, full_cc_instruction, host_tool_configuration
from hypoloom.grounding_background import transcript_web_results
from hypoloom.grounding_web import GroundingWeb
from hypoloom.host_tools_server import AgentToolServer
from hypoloom.provider_relay import ProviderRelay

from test_agent_sessions import _manager
from test_claude_agent_host import _claude_literature_main_request
from test_claude_file_tool_cli import _handler_for, _installed_claude, _result_text, _tool_results
from test_git_authoring_workspace import make_target
from test_grounding_web import DOC, SEARCH, Transport, _response


_MODEL = "claude-sonnet-4-6"


class _LiteratureScenario:
    def __init__(self, request, tools: tuple[str, ...], nonce: str):
        self.tools, self.nonce = tools, nonce
        self.steps = [("read_packet", "Read", {"file_path": "/inputs/0001.txt"})]
        if "web_search" in tools:
            self.steps.append(("search_sources", "mcp__host-tools__web_search", {"query": "project evaluation", "max_results": 2}))
        if "web_fetch" in tools:
            self.steps.append(("fetch_source", "mcp__host-tools__web_fetch", {"url": DOC, "max_chars": 4096}))
        script = ("python3 - <<'PY'\nimport errno\nfrom pathlib import Path\n"
            "for path in ('/workspace/src/value.txt', '/inputs/0001.txt'):\n"
            "    try:\n        Path(path).write_text('forbidden')\n"
            "    except OSError as error:\n        assert error.errno == errno.EROFS\n"
            "    else:\n        raise AssertionError('literature source was writable')\n"
            + ("assert Path('/host-tools.sock').exists()\n" if tools else "assert not Path('/host-tools.sock').exists()\n")
            + "print('LITERATURE_READONLY_SCOPE')\nPY")
        self.steps.append(("check_scope", "Bash", {"command": script, "timeout": 10000}))
        packet = next(item.ref for item in request.input_artifacts if item.ref.endswith("--literature-packet"))
        self.candidate = {"typed_candidate": {"task_kind": "LITERATURE_REVIEW",
            "summary": f"Actual retrieved document states {nonce}; initial packet is supplemental." if tools else
                "Profile disables autonomous web tools; the empty supplied packet cannot establish external literature findings.",
            "supporting_refs": [packet], "web_sources": [DOC] if tools else []},
            "declared_effects": [{"effect": effect} for effect in request.effect_requirements]}
        self.requests, self.errors = [], []

    def record(self, document):
        self.requests.append(document)
        step = len(self.requests)
        try:
            names = {tool["name"] for tool in document["tools"]}
            assert {name for name in names if name.startswith("mcp__")} == {
                "mcp__host-tools__" + tool for tool in self.tools}
            assert {"Read", "Bash"} <= names
            rendered = json.dumps(document["messages"])
            fetch_steps = [index + 1 for index, item in enumerate(self.steps) if item[0] == "fetch_source"]
            fetched = bool(fetch_steps and step > fetch_steps[0])
            assert (self.nonce in rendered) is fetched, "source body appeared before its actual web_fetch reply"
            if step > 1:
                previous = self.steps[step - 2][0]
                result = _tool_results(document)[previous]
                assert not result.get("is_error"), result
                if previous == "fetch_source":
                    assert self.nonce in _result_text(result)
                if previous == "check_scope":
                    assert "LITERATURE_READONLY_SCOPE" in _result_text(result)
        except (AssertionError, IndexError, KeyError) as exc:
            self.errors.append(f"step {step}: {exc}")
        return step

    def tool_calls(self, step):
        if self.errors:
            return ()
        return (self.steps[step - 1],) if step <= len(self.steps) else (("finish", "StructuredOutput", self.candidate),)


@pytest.mark.parametrize("setting,expected", (({}, ("web_search", "web_fetch")),
    ({"enabled": True, "tools": ["web_fetch"]}, ("web_fetch",)),
    ({"enabled": False}, ())), ids=("default-search-fetch", "fetch-only", "disabled"))
def test_native_literature_profile_scope_fetch_provenance_and_typed_summary(tmp_path: Path, setting: dict, expected: tuple):
    executable = _installed_claude()
    request = _claude_literature_main_request()
    config = EmbeddedInput.from_bytes("node--expert-config", "application/json",
        json.dumps({"node_runtime": {"literature": setting}}).encode())
    request = replace(request, schema_version=14, required_model_identity=_MODEL,
        input_artifacts=(*request.input_artifacts, config),
        budget=AgentBudget(max_turns=12, max_tokens=20000, max_output_bytes=131072, wall_seconds=40, max_cost="1"))
    schema = _output_schema(request)
    assert "web_sources" in schema["properties"]["typed_candidate"]["properties"]
    assert host_tool_configuration(request) == (("literature", expected) if expected else (None, ()))
    _, target = make_target(tmp_path)
    manager = _manager(tmp_path)
    workspace = manager.prepare(request, target)
    nonce = "native-literature-source-" + secrets.token_hex(12)
    transport = Transport({SEARCH: _response(SEARCH, (
        '<a class="result__a" href="https://example.org/docs">Project evaluation</a>'
        '<a class="result__snippet">Read the complete document.</a>').encode()),
        DOC: _response(DOC, f"<title>Evaluation evidence</title><p>{nonce}</p>".encode())})
    web = GroundingWeb(transport)
    scenario = _LiteratureScenario(request, expected, nonce)
    provider = HTTPServer(("127.0.0.1", 0), _handler_for(scenario))
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    key = "host-only-literature-key-" + secrets.token_hex(12)
    relay = ProviderRelay(tmp_path / "provider.sock", base_url=f"http://127.0.0.1:{provider.server_port}",
        provider_environment={"ANTHROPIC_API_KEY": key}, model=_MODEL,
        max_request_bytes=4*1024*1024, max_response_bytes=1024*1024,
        max_requests=10, deadline_at=time.monotonic()+45)
    handled, owner = [], threading.get_ident()
    def handler(attempt, tool, arguments):
        assert attempt == request.attempt_id and threading.get_ident() == owner
        assert tool in expected
        result = getattr(web, tool)(**arguments)
        handled.append({"attempt_id": attempt, "tool": tool, "arguments": arguments, "result": result})
        return result
    endpoint = AgentToolServer(tmp_path / "tools.sock", request.attempt_id, handler,
        scope="literature", tools=expected) if expected else None
    argv = full_cc_argv(bwrap=Path("/usr/bin/bwrap"), executable=executable,
        request=request, model=_MODEL, workspace=workspace, provider_socket=relay.socket_path,
        host_socket=endpoint.socket_path if endpoint else None, output_schema=schema, extra_paths=())
    (tmp_path / "argv.json").write_text(json.dumps(argv))
    (tmp_path / "request.json").write_text(json.dumps(request.to_dict(), indent=2))
    process = None
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=full_cc_environment(), start_new_session=True)
        prompt = full_cc_instruction(request) + " Read /inputs/0001.txt. The known public document, if tools permit retrieval, is https://example.org/docs."
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(process.communicate, prompt.encode(), timeout=45)
            deadline = time.monotonic()+45
            while not future.done() and time.monotonic() < deadline:
                if endpoint is not None:
                    endpoint.service()
                time.sleep(0.005)
            stdout, stderr = future.result(timeout=1)
        (tmp_path / "stdout.jsonl").write_bytes(stdout)
        (tmp_path / "stderr.txt").write_bytes(stderr)
        assert not scenario.errors, (scenario.errors, stdout.decode(), stderr.decode())
        assert process.returncode == 0, (stdout.decode(), stderr.decode())
        result = next(json.loads(line) for line in stdout.splitlines() if json.loads(line).get("type") == "result")
        assert result["structured_output"] == scenario.candidate
        response, failure, diagnostics = _provider_response(request, _MODEL, stdout)
        assert failure is None and response is not None, (failure, diagnostics)
        validated = validate_postflight(request, response)
        assert isinstance(validated.candidate, LiteratureSummaryCandidate)
        assert validated.candidate.web_sources == ((DOC,) if expected else ())
        assert [item["tool"] for item in handled] == list(expected)
        assert [call[1] for call in transport.calls if call[0] == "fetch"] == ([SEARCH, DOC] if len(expected) == 2 else [DOC] if expected else [])
        metadata = workspace.finish_node()
        (tmp_path / "node-metadata.json").write_text(json.dumps(metadata, indent=2))
        (tmp_path / "scripted-source-calls.json").write_text(json.dumps(transport.calls, indent=2))
        (tmp_path / "handled-tools.json").write_text(json.dumps(handled, indent=2))
        transcript = (workspace.session_root / f"{workspace.binding['session_id']}.jsonl").read_bytes()
        assert (nonce.encode() in transcript) is bool(expected)
        assert key.encode() not in transcript + stdout + stderr
        assert [item["tool"] for item in transcript_web_results(transcript,
            session_id=workspace.binding["session_id"])] == list(expected)
        assert metadata["transcript_complete"] is True
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=5)
        if endpoint is not None:
            endpoint.close()
        relay.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
        workspace.cleanup()
