"""Wire-final input floor: provider start/final usage skew never fails a complete bill.

The k3 endpoint's stream-start usage can run a few tokens above its final
per-request usage. Transcript assistant events carry that start snapshot, so
the transcript lower bound can exceed the exact billing ledger. When every
generation exchange is complete and single-model, the wire-final usage is the
floor the ledger must cover; the transcript bound remains the fallback.
"""

from __future__ import annotations

import json
import threading
from http.server import HTTPServer
from pathlib import Path

import pytest

from hypoloom.claude_agent_host import (
    AgentHostTerminalReason,
    ClaudeAgentHost,
    _provider_response,
)
from hypoloom.execution_workspace import ExecutionWorkspace, GitTargetBinding
from hypoloom.provider_observation import observed_input_floor

import test_claude_file_tool_cli as fixture
from tests.runtime.test_claude_agent_host import _claude_request, _provider_ndjson
from tests.runtime.test_normal_output_adapter import _normal_result
from tests.runtime.test_partial_cli_accounting import _assistant


def _exchange(*, input_tokens, output_tokens=1, cache_read=0, model="served-model",
              complete=True, path="/v1/messages", status=200):
    return {
        "request_path": path,
        "upstream_status": status,
        "message_complete": complete,
        "response_body_complete": complete,
        "served_models": [model],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": cache_read},
    }


def test_observed_input_floor_sums_complete_single_model_generation():
    floor = observed_input_floor([
        _exchange(input_tokens=3, cache_read=5),
        _exchange(input_tokens=7, cache_read=11),
    ])
    assert floor == 3 + 5 + 7 + 11


@pytest.mark.parametrize("exchanges", [
    [],
    [_exchange(input_tokens=3, complete=False)],
    [_exchange(input_tokens=3, status=429)],
    [_exchange(input_tokens=3), _exchange(input_tokens=4, model="other-model")],
    [_exchange(input_tokens=3, path="/v1/messages/count_tokens")],
])
def test_observed_input_floor_requires_complete_single_model_generation(exchanges):
    assert observed_input_floor(exchanges) is None


def _skewed_stream():
    """Two assistant messages billed 99 each; transcript snapshots say 100 each."""
    outer, _ = _normal_result()
    outer["usage"] = {"input_tokens": 198, "output_tokens": 4, "total_tokens": 202}
    outer["num_turns"] = 2
    messages = [
        _assistant("msg-first", {"input_tokens": 100}),
        _assistant("msg-second", {"input_tokens": 100}),
    ]
    return _provider_ndjson(*messages, outer)


def test_wire_floor_accepts_provider_start_final_skew():
    response, failure, diagnostics = _provider_response(
        _claude_request(), "stub-model", _skewed_stream(),
        final_text_output=True, observed_wire_input_floor=198)
    assert failure is None and response is not None
    assert diagnostics["provider_accounting_floor"] == "wire_exchange"
    assert diagnostics["provider_observed_input_tokens_lower_bound"] == 200
    assert diagnostics["provider_accounting"]["token_usage"]["input_tokens"] == 198


def test_wire_floor_still_rejects_a_ledger_below_the_wire():
    response, failure, diagnostics = _provider_response(
        _claude_request(), "stub-model", _skewed_stream(),
        final_text_output=True, observed_wire_input_floor=199)
    assert response is None and failure == "invalid_provider_metadata"
    assert diagnostics["provider_accounting_floor"] == "wire_exchange"
    assert "provider_accounting" not in diagnostics


@pytest.mark.parametrize("floor", [None, -1, True, "198", 198.0])
def test_non_integral_wire_floor_keeps_the_transcript_guard(floor):
    response, failure, diagnostics = _provider_response(
        _claude_request(), "stub-model", _skewed_stream(),
        final_text_output=True, observed_wire_input_floor=floor)
    assert response is None and failure == "invalid_provider_metadata"
    assert "provider_accounting_floor" not in diagnostics


def test_real_cli_skewed_provider_stream_closes_through_the_wire_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = fixture._installed_claude()

    real_send_event = fixture._send_event

    def skewed_send_event(handler, event, data):
        if event == "message_start":
            data = json.loads(json.dumps(data))
            data["message"]["usage"] = {"input_tokens": 2, "output_tokens": 0}
        elif event == "message_delta":
            data = json.loads(json.dumps(data))
            data["usage"] = {"input_tokens": 1,
                             "output_tokens": data["usage"]["output_tokens"]}
        return real_send_event(handler, event, data)

    monkeypatch.setattr(fixture, "_send_event", skewed_send_event)

    import secrets
    target = tmp_path / "target-checkout"
    target.mkdir()
    (target / "canary.py").write_text("TARGET_UNCHANGED = True\n", encoding="utf-8")
    fixture._git(target, "init", "-q")
    fixture._git(target, "add", "canary.py")
    fixture._git(target, "-c", "user.name=Hypoloom Loopback",
                 "-c", "user.email=loopback@invalid", "commit", "-qm", "loopback target")
    commit = fixture._git(target, "rev-parse", "HEAD")

    from hypoloom.agent_sessions import AgentSessions
    from hypoloom.artifacts import ArtifactPublisher
    from hypoloom.expert_loop import ResearchNodeSubkind
    from hypoloom.fake_agent import AgentResponse, FakeAgent
    from test_claude_agent_host import _claude_expert_design_request, _claude_pincer_request
    from test_claude_session_cli import _MODEL
    from test_full_cc_adapter import _AdapterScenario, _request

    state_root = tmp_path / "state"
    state_root.mkdir()
    sessions = AgentSessions(state_root, ArtifactPublisher(state_root / "artifacts"))
    scenario = _AdapterScenario((target, state_root))
    server = HTTPServer(("127.0.0.1", 0), fixture._handler_for(scenario))
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    adapter = ClaudeAgentHost(claude_executable=executable)
    provider_environment = {
        "ANTHROPIC_API_KEY": "loopback-only-dummy-key",
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
    }

    import time

    def invoke(request, workspace):
        generated = FakeAgent().invoke(request)
        assert isinstance(generated, AgentResponse)
        scenario.structured_output = {
            "typed_candidate": generated.typed_candidate,
            "declared_effects": [{"effect": item.effect} for item in generated.declared_effects],
        }
        scenario.stage_step = 0
        handle = adapter.start(
            request, model=_MODEL, provider_environment=provider_environment,
            bind_process_group=lambda _binding: None, tool_workspace=workspace,
            diagnostics_root=state_root,
        )
        try:
            deadline = time.monotonic() + 90
            while handle.returncode is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert handle.returncode is not None
            observation = adapter.wait(handle, timeout=5)
        finally:
            adapter.cleanup(handle)
        assert observation.terminal_reason is AgentHostTerminalReason.SUCCEEDED, (
            observation.failure_code, observation.diagnostics)
        workspace.finish_node()
        return observation

    binding = GitTargetBinding(checkout=target, commit=commit)
    try:
        scenario.stage = "seed"
        seed = _request(_claude_pincer_request(ResearchNodeSubkind.SYNTHESIS),
                        "seed", scenario.parent_context)
        invoke(seed, sessions.prepare(seed, binding))
        scenario.stage = "design"
        design = _request(_claude_expert_design_request(), "design", scenario.chain_context)
        before_design = len(scenario.requests)
        observation = invoke(design, sessions.prepare(design, binding))
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)

    assert scenario.errors == []
    diagnostics = observation.diagnostics
    assert observation.terminal_reason is AgentHostTerminalReason.SUCCEEDED, (
        observation.failure_code, diagnostics)
    assert diagnostics["provider_accounting_floor"] == "wire_exchange"
    wire = diagnostics["provider_observed_usage"]
    wire_input = sum(wire[key] for key in (
        "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    assert wire["completed_requests"] == len(scenario.requests) - before_design
    assert wire["unmeasured_requests"] == 0
    ledger = diagnostics["provider_accounting"]["token_usage"]
    assert wire_input <= ledger["input_tokens"]
    transcript_bound = diagnostics["provider_observed_input_tokens_lower_bound"]
    raw = (Path(diagnostics["runtime_capture"]["root"]) / "stdout.log").read_bytes()
    replay, replay_failure, _ = _provider_response(
        design, _MODEL, raw, final_text_output=True)
    if transcript_bound > ledger["input_tokens"]:
        assert replay is None and replay_failure == "invalid_provider_metadata"
    else:
        assert replay_failure is None and replay is not None
