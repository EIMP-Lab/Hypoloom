"""Real CC turn limits use model iterations, including Stop corrections.

Only a local scripted provider participates. The production adapter, native CLI,
provider relay, sandbox, Stop validator and session archive are real.
"""

from __future__ import annotations

import copy
import json
import secrets
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.agent_aci import AgentResponse, EmbeddedInput
from hypoloom.agent_budget import fold_agent_attempts
from hypoloom.claude_agent_host import ClaudeAgentHost, process_group_exists
from hypoloom.expert_loop import ResearchNodeSubkind
from hypoloom.fake_agent import FakeAgent

from test_agent_sessions import _manager
from test_claude_agent_host import _claude_pincer_request
from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results
from test_full_cc_adapter import _MODEL, _request
from test_full_cc_host_g3 import _start_provider
from test_git_authoring_workspace import make_target


class _TurnScenario:
    def __init__(self, request, *, correction):
        response = FakeAgent().invoke(request)
        assert isinstance(response, AgentResponse)
        self.output = {"typed_candidate": response.typed_candidate,
            "declared_effects": [{"effect": effect.effect} for effect in response.declared_effects]}
        self.correction = correction
        self.requests, self.errors = [], []

    def record(self, document):
        self.requests.append(document)
        if len(self.requests) == 2:
            for index in range(4):
                result = _tool_results(document)[f"read_{index}"]
                assert not result.get("is_error")
                assert "base" in _result_text(result)
        return len(self.requests)

    def tool_calls(self, step):
        if step == 1:
            return tuple((f"read_{index}", "Read", {"file_path": "/workspace/src/value.txt"})
                for index in range(4))
        output = copy.deepcopy(self.output)
        if step == 2 and self.correction:
            output.pop("declared_effects")
        return (("final", "StructuredOutput", output),)


def _run_native(tmp_path: Path, *, limit: int, correction: bool):
    _, target = make_target(tmp_path)
    manager = _manager(tmp_path)
    base = _request(_claude_pincer_request(ResearchNodeSubkind.SYNTHESIS), "turns", None)
    config = EmbeddedInput.from_bytes("turns--expert-config", "application/json",
        json.dumps({"node_runtime": {"output_correction": {"max_failed_validations": 3}}}).encode())
    request = replace(base, budget=replace(base.budget, max_turns=limit),
        input_artifacts=(*base.input_artifacts, config))
    scenario = _TurnScenario(request, correction=correction)
    key = "local-turn-test-" + secrets.token_hex(12)
    provider, thread = _start_provider(scenario, key)
    adapter = ClaudeAgentHost(claude_executable=_installed_claude())
    workspace = manager.prepare(request, target)
    handle = None
    try:
        handle = adapter.start(request, model=_MODEL,
            provider_environment={"ANTHROPIC_BASE_URL": f"http://127.0.0.1:{provider.server_port}",
                "ANTHROPIC_API_KEY": key}, bind_process_group=lambda _: None,
            tool_workspace=workspace, diagnostics_root=manager.state_root)
        observed = adapter.wait(handle, timeout=45)
        metadata = workspace.finish_node()
        logs = manager.state_root / "diagnostics/agents" / request.run_id / request.attempt_id
        events = [json.loads(line) for line in (logs / "stdout.log").read_text().splitlines()]
        terminal = events[-1]
        assistants = [event["message"] for event in events if event.get("type") == "assistant"]
        observation = {"request": request.to_dict(), "failure_code": observed.failure_code,
            "diagnostics": dict(observed.diagnostics)}
        if observed.response is not None:
            observation["response"] = observed.response.to_dict()
        charge = fold_agent_attempts([{"kind": "AGENT", "state": "CLOSED", "attempt_id": request.attempt_id,
            "observation_json": json.dumps(observation)}])[0]
        evidence = {"paid_model": False, "gpu": False, "max_turns": limit,
            "provider_requests": len(scenario.requests), "pgid": handle.process_group_id,
            "terminal": terminal, "assistant_unique_ids": len({message["id"] for message in assistants}),
            "observation": observation, "charge": {"turns": charge.turns, "cost": str(charge.cost),
                "source": charge.source}, "session": metadata, "logs": str(logs)}
        (tmp_path / "turn-evidence.json").write_text(json.dumps(evidence, indent=2))
        assert not scenario.errors
        assert metadata["transcript_complete"] is True
        return evidence
    finally:
        if handle is not None:
            adapter.cleanup(handle)
            assert not process_group_exists(handle.process_group_id)
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
        assert workspace.resources == []


@pytest.mark.parametrize("correction", [False, True], ids=["parallel-tools", "stop-correction"])
def test_native_parallel_tools_report_within_model_turn_budget(tmp_path, correction):
    expected_turns = 3 if correction else 2
    evidence = _run_native(tmp_path, limit=expected_turns, correction=correction)
    assert evidence["terminal"]["subtype"] == "success"
    assert evidence["terminal"]["is_error"] is False
    assert evidence["terminal"]["num_turns"] > expected_turns
    assert evidence["provider_requests"] == evidence["assistant_unique_ids"] == expected_turns
    assert evidence["observation"]["failure_code"] is None
    assert evidence["observation"]["response"]["turn_count"] == expected_turns
    assert evidence["charge"]["turns"] == expected_turns


def test_native_actual_turn_exhaustion_remains_rejected(tmp_path):
    evidence = _run_native(tmp_path, limit=1, correction=False)
    assert evidence["provider_requests"] == evidence["assistant_unique_ids"] == 1
    assert evidence["terminal"]["subtype"] == "error_max_turns"
    assert evidence["terminal"]["is_error"] is True
    assert evidence["observation"]["failure_code"] == "nonzero_exit"
    assert "response" not in evidence["observation"]
    assert evidence["charge"]["turns"] == 1
