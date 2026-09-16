"""A positive CLI aggregate must not settle an observed incomplete invocation."""

import json
from dataclasses import replace
from decimal import Decimal

import pytest

from hypoloom.agent_budget import fold_agent_attempts
from hypoloom.claude_agent_host import _provider_response
from tests.runtime.test_claude_agent_host import (
    _claude_request, _host, _provider_ndjson, _provider_output, _start,
)


def _assistant(message_id, usage):
    return {"type": "assistant", "message": {
        "id": message_id, "role": "assistant", "content": [], "usage": usage}}


def _request():
    request = _claude_request()
    return replace(request, budget=replace(request.budget, max_cost="1"))


def _messages():
    first = _assistant("first", {"input_tokens": 7,
        "cache_creation_input_tokens": 13, "cache_read_input_tokens": 17, "output_tokens": 0})
    second = _assistant("second", {"input_tokens": 3,
        "cache_read_input_tokens": 23, "output_tokens": 0})
    # CC repeats one message's usage on separate content-block records.
    return [first, first, first, second, second, second]


def _result(subtype="error_max_structured_output_retries", *, complete=False):
    return json.loads(_provider_output(subtype=subtype, is_error=subtype != "success",
        num_turns=1, total_cost_usd=0.01,
        usage={"input_tokens": 33 if complete else 7,
               "cache_creation_input_tokens": 13, "cache_read_input_tokens": 17,
               "output_tokens": 11}))


def _charge(request, response, diagnostics):
    observation = {"request": request.to_dict(), "diagnostics": diagnostics}
    if response is not None:
        observation["response"] = response.to_dict()
    return fold_agent_attempts([{"attempt_id": request.attempt_id, "kind": "AGENT",
        "state": "CLOSED", "observation_json": json.dumps(observation)}])[0]


def _assert_partial(request, response, diagnostics):
    assert response is None
    assert "provider_accounting" not in diagnostics
    assert diagnostics["provider_observed_input_tokens_lower_bound"] == 63
    assert "input" in diagnostics["provider_accounting_error"]
    assert diagnostics["provider_reported_lower_bound"] == {
        "token_usage": {"input_tokens": 37, "output_tokens": 11, "total_tokens": 48},
        "turn_count": 1, "cost": "0.01", "cost_currency": "USD"}
    charge = _charge(request, response, diagnostics)
    assert (charge.source, charge.tokens, charge.turns, charge.cost) == (
        "declared", request.budget.max_tokens, request.budget.max_turns,
        Decimal(request.budget.max_cost))


@pytest.mark.parametrize("subtype", ["error_max_structured_output_retries", "success"])
def test_positive_partial_aggregate_cannot_release_reservation_or_create_response(subtype):
    request = _request()
    response, failure, diagnostics = _provider_response(request, "stub-model",
        _provider_ndjson(*_messages(), _result(subtype)))
    _assert_partial(request, response, diagnostics)
    assert failure == ("invalid_provider_metadata" if subtype == "success" else "provider_not_success")


@pytest.mark.parametrize("subtype", ["error_max_structured_output_retries", "success"])
def test_complete_aggregate_deduplicates_repeated_ids_and_counts_cache(subtype):
    request = _request()
    response, failure, diagnostics = _provider_response(request, "stub-model",
        _provider_ndjson(*_messages(), _result(subtype, complete=True)))
    assert diagnostics["provider_observed_input_tokens_lower_bound"] == 63
    assert "provider_reported_lower_bound" not in diagnostics
    assert "provider_accounting_error" not in diagnostics
    assert diagnostics["provider_accounting"]["token_usage"] == {
        "input_tokens": 63, "output_tokens": 11, "total_tokens": 74}
    assert failure == (None if subtype == "success" else "provider_not_success")
    charge = _charge(request, response, diagnostics)
    assert charge.source == ("response" if subtype == "success" else "provider")
    assert charge.tokens == 74 and charge.cost == Decimal("0.01")


def test_partial_message_snapshots_use_maximum_normalized_input_not_sum_or_last():
    messages = [_assistant("first", {"input_tokens": 7}),
        _assistant("first", {"input_tokens": 7, "cache_read_input_tokens": 30}),
        _assistant("first", {"input_tokens": 37}),
        _assistant("first", {"input_tokens": 9}), *_messages()[3:]]
    response, _, diagnostics = _provider_response(_request(), "stub-model",
        _provider_ndjson(*messages, _result(complete=True)))
    assert response is None
    assert diagnostics["provider_observed_input_tokens_lower_bound"] == 63
    assert "provider_accounting" in diagnostics


@pytest.mark.parametrize("message_id,usage", [
    (None, {"input_tokens": 100}), ("", {"input_tokens": 100}),
    (3, {"input_tokens": 100}), ("first", None), ("first", []),
    ("first", {}), ("first", {"input_tokens": True}),
    ("first", {"input_tokens": "100"}), ("first", {"input_tokens": -1}),
    ("first", {"input_tokens": 100, "cache_read_input_tokens": True}),
    ("first", {"input_tokens": 100, "cache_creation_input_tokens": -1}),
])
def test_unsupported_message_usage_supplies_no_invented_bound(message_id, usage):
    _, _, diagnostics = _provider_response(_request(), "stub-model",
        _provider_ndjson(_assistant(message_id, usage), _result()))
    assert diagnostics.get("provider_observed_input_tokens_lower_bound", 0) == 0
    assert "provider_accounting" in diagnostics
    assert "provider_reported_lower_bound" not in diagnostics


@pytest.mark.parametrize("subtype", ["error_max_structured_output_retries", "success"])
def test_result_only_compatibility_does_not_claim_observed_input(subtype):
    response, failure, diagnostics = _provider_response(_request(), "stub-model",
        _provider_ndjson(_result(subtype)))
    assert diagnostics.get("provider_observed_input_tokens_lower_bound", 0) == 0
    assert "provider_accounting" in diagnostics
    assert failure == (None if subtype == "success" else "provider_not_success")
    assert (response is not None) == (subtype == "success")


@pytest.mark.parametrize("damage", ["input", "output", "cache", "cost", "turns", "model"])
def test_invalid_aggregate_metadata_is_not_relabelled_valid_partial_accounting(damage):
    result = _result()
    if damage in {"input", "output", "cache"}:
        key = {"input": "input_tokens", "output": "output_tokens",
               "cache": "cache_read_input_tokens"}[damage]
        result["usage"][key] = True
    elif damage == "cost":
        result["total_cost_usd"] = "NaN"
    elif damage == "turns":
        result["num_turns"] = True
    else:
        del result["usage"]
        result["modelUsage"] = {"other-model": {"inputTokens": 37, "outputTokens": 11}}
    response, _, diagnostics = _provider_response(_request(), "stub-model",
        _provider_ndjson(*_messages(), result))
    assert response is None
    assert "provider_accounting" not in diagnostics
    assert "provider_reported_lower_bound" not in diagnostics
    assert "provider_accounting_error" in diagnostics


def test_model_usage_fallback_cannot_hide_observed_missing_input():
    result = _result()
    result["usage"] = {"input_tokens": 0, "output_tokens": 0}
    result["modelUsage"] = {"stub-model": {"inputTokens": 37, "outputTokens": 11}}
    request = _request()
    response, _, diagnostics = _provider_response(request, "stub-model",
        _provider_ndjson(*_messages(), result))
    _assert_partial(request, response, diagnostics)


def test_nonzero_cli_partial_accounting_preserves_failure_and_unknown_reservation(tmp_path):
    host = _host(tmp_path)
    request = _request()
    observed = host.wait(_start(host, request=request, mode="diagnostic",
        extra_environment={"STUB_STDOUT_DIAGNOSTIC":
            _provider_ndjson(*_messages(), _result()).decode(), "STUB_DIAGNOSTIC": ""}))
    assert observed.failure_code == "nonzero_exit"
    _assert_partial(request, observed.response, observed.diagnostics)
