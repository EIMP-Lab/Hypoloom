"""A bounded MCP wait may return before the actual Process closes."""
import json
from dataclasses import replace
from types import SimpleNamespace

from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import default_request
from test_claude_multichain_g3 import _BatchScenario


def test_native_script_polls_nonterminal_wait_with_fresh_tool_identity(tmp_path, monkeypatch):
    scenario = _BatchScenario(tmp_path)
    request = default_request(AgentCommand.EXPERIMENT_EXECUTION)
    task = replace(request.task_context, key=replace(request.task_context.key, chain="chain-a"))
    request = replace(request, task_context=task, command_id=task.command_id)
    monkeypatch.setattr("test_claude_multichain_g3._request_from_provider", lambda _: request)
    scenario.reviser = SimpleNamespace(invoke=lambda _: SimpleNamespace(typed_candidate={}))
    scenario.steps[request.attempt_id] = 2
    identity = scenario._identity(request)
    scenario.experiments[identity] = "actual-exp"
    prefix = "probe_" + request.attempt_id[-16:] + "__"

    def message(tool_id, terminal):
        result = {"exp_id": "actual-exp", "terminal": terminal, "state": "TERMINAL" if terminal else "EXECUTING"}
        if terminal:
            result["outcome"] = {"outcome": "SUCCESS"}
        return {"messages": [{"role": "user", "content": [
            {"type": "text", "text": scenario.chain_nonces["chain-a"]},
            {"type": "tool_result", "tool_use_id": prefix + tool_id, "content": json.dumps(result)}]}]}

    scenario.record(message("batch_wait_2_call", False))
    assert not scenario.errors
    assert identity not in scenario.execution_results
    calls = scenario.tool_calls(1)
    assert calls == ((prefix + "batch_wait_3_call", "mcp__host-tools__wait_experiment",
        {"exp_id": "actual-exp", "max_wait_seconds": 10}),)
    scenario.record(message("batch_wait_3_call", True))
    assert not scenario.errors
    assert scenario.execution_results[identity]["outcome"]["outcome"] == "SUCCESS"
