"""Capture rejection must not discard a complete CLI-reported usage envelope."""

import json
from decimal import Decimal

import pytest

from hypoloom.agent_budget import fold_agent_attempts
from hypoloom.agent_context import agent_retry_class
from hypoloom.claude_agent_host import AgentHostTerminalReason
from tests.runtime.test_claude_agent_host import (
    _claude_draft_request, _draft_provider_output, _draft_workspace, _host, _start,
)


def _output():
    outer = json.loads(_draft_provider_output())
    outer.update(num_turns=2, total_cost_usd=0.108265,
        usage={"input_tokens": 7, "cache_creation_input_tokens": 13,
               "cache_read_input_tokens": 17, "output_tokens": 11})
    return outer


def _observe(tmp_path, raw):
    host = _host(tmp_path)
    request = _claude_draft_request()
    workspace = _draft_workspace(tmp_path, request.attempt_id)
    # The real isolated stub returns the complete envelope but does not create
    # experiment.py: production capture, not a mocked validator, rejects it.
    result = host.wait(_start(host, request=request, tool_workspace=workspace,
        extra_environment={"STUB_VALID_OUTPUT": raw}))
    return request, result


def _charge(request, observed):
    return fold_agent_attempts([{"attempt_id": request.attempt_id, "kind": "AGENT", "state": "CLOSED",
        "observation_json": json.dumps({"request": request.to_dict(),
            "failure_code": observed.failure_code, "diagnostics": observed.diagnostics})}])[0]


def _assert_rejected(observed):
    assert observed.terminal_reason is AgentHostTerminalReason.INVALID_OUTPUT
    assert observed.failure_code == "invalid_tool_workspace"
    assert observed.response is None
    assert "experiment.py" in observed.diagnostics["workspace_error"]
    assert agent_retry_class(observed.failure_code, observed.diagnostics, retryable=True) == "MODEL"


@pytest.mark.parametrize("streamed", [False, True])
def test_capture_rejection_preserves_reported_usage_without_accepting_candidate(tmp_path, streamed):
    raw = json.dumps(_output())
    if streamed:
        raw = json.dumps({"type": "assistant", "message": {"role": "assistant", "content": []}}) + "\n" + raw
    request, observed = _observe(tmp_path, raw)
    _assert_rejected(observed)
    assert observed.diagnostics.get("provider_accounting") == {
        "token_usage": {"input_tokens": 37, "output_tokens": 11, "total_tokens": 48},
        "turn_count": 2, "cost": "0.108265", "cost_currency": "USD"}
    charge = _charge(request, observed)
    assert (charge.source, charge.tokens, charge.turns, charge.cost) == ("provider", 48, 2, Decimal("0.108265"))


@pytest.mark.parametrize("damage", ["missing-usage", "bad-tokens", "bad-cost", "bad-turns",
                                   "truncated", "partial-tail", "non-result"])
def test_capture_rejection_leaves_invalid_or_incomplete_usage_unknown(tmp_path, damage):
    outer = _output()
    if damage == "missing-usage":
        del outer["usage"]
        outer["modelUsage"] = {"stub-model": {"inputTokens": 37, "outputTokens": 11}}
    elif damage == "bad-tokens":
        outer["usage"]["output_tokens"] = True
    elif damage == "bad-cost":
        outer["total_cost_usd"] = None
    elif damage == "bad-turns":
        outer["num_turns"] = True
    elif damage == "non-result":
        outer["type"] = "system"
    raw = json.dumps(outer)
    if damage == "truncated":
        raw = raw[:-1]
    elif damage == "partial-tail":
        raw += '\n{"type":'
    request, observed = _observe(tmp_path, raw)
    _assert_rejected(observed)
    assert "provider_accounting" not in observed.diagnostics
    charge = _charge(request, observed)
    assert charge.source == "declared"
    assert charge.cost == Decimal(request.budget.max_cost)


def test_oversized_transport_is_not_parsed_for_workspace_accounting(tmp_path, monkeypatch):
    from hypoloom import claude_agent_host

    monkeypatch.setattr(claude_agent_host, "_transport_output_limit", lambda request: 64)
    def unexpected_accounting(*args, **kwargs):
        pytest.fail("over-limit output must not be parsed for accounting")
    monkeypatch.setattr(claude_agent_host, "_provider_accounting", unexpected_accounting)
    request, observed = _observe(tmp_path, json.dumps(_output()))
    assert observed.failure_code == "transport_output_too_large"
    assert observed.response is None
    assert "provider_accounting" not in observed.diagnostics
    assert _charge(request, observed).source == "declared"


@pytest.mark.parametrize("subtype", ["success", "error_max_structured_output_retries"])
def test_capture_rejection_cannot_settle_positive_but_incomplete_cli_accounting(tmp_path, subtype):
    outer = _output()
    outer.update(subtype=subtype, is_error=subtype != "success")
    messages = [{"type": "assistant", "message": {"id": message_id,
        "role": "assistant", "content": [], "usage": {"input_tokens": input_tokens}}}
        for message_id, input_tokens in [("first", 37), ("first", 37), ("second", 26)]]
    request, observed = _observe(tmp_path,
        "\n".join(json.dumps(item) for item in [*messages, outer]))
    _assert_rejected(observed)
    assert "provider_accounting" not in observed.diagnostics
    assert observed.diagnostics["provider_observed_input_tokens_lower_bound"] == 63
    assert "input" in observed.diagnostics["provider_accounting_error"]
    assert observed.diagnostics["provider_reported_lower_bound"] == {
        "token_usage": {"input_tokens": 37, "output_tokens": 11, "total_tokens": 48},
        "turn_count": 2, "cost": "0.108265", "cost_currency": "USD"}
    charge = _charge(request, observed)
    assert (charge.source, charge.tokens, charge.turns, charge.cost) == (
        "declared", request.budget.max_tokens, request.budget.max_turns,
        Decimal(request.budget.max_cost))
