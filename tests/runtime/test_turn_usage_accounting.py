"""Current native turn normalization preserves budgets and complete accounting."""

import copy
from dataclasses import replace
from decimal import Decimal

import pytest

from hypoloom.claude_agent_host import _provider_response
from tests.runtime.test_claude_agent_host import _claude_request, _provider_ndjson
from tests.runtime.test_normal_output_adapter import _normal_result
from tests.runtime.test_partial_cli_accounting import _assistant, _charge


def _request(limit=2):
    request = _claude_request()
    return replace(request, budget=replace(request.budget, max_turns=limit, max_cost="1"))


def _result():
    outer, _ = _normal_result()
    outer.update(num_turns=5, total_cost_usd=0.01)
    return outer


def _messages():
    return [_assistant("first", {"input_tokens": 1})] * 4 + [
        _assistant("second", {"input_tokens": 1})]


@pytest.mark.parametrize("failure", [None, "invalid-json", "native-error", "cost"])
def test_native_same_message_count_drives_success_rejection_and_charge(failure):
    request = _request()
    outer = _result()
    if failure == "invalid-json":
        outer["result"] = "invalid JSON"
    elif failure == "native-error":
        outer.update(subtype="error_max_turns", is_error=True)
    elif failure == "cost":
        outer["total_cost_usd"] = 2
    before = copy.deepcopy(outer)
    response, rejected, diagnostics = _provider_response(request, "stub-model",
        _provider_ndjson(*_messages(), outer), final_text_output=True)
    assert rejected == {None: None, "invalid-json": "invalid_structured_output",
        "native-error": "provider_not_success", "cost": "cost_budget_exceeded"}[failure]
    assert diagnostics["num_turns"] == 5
    assert diagnostics["provider_turn_usage_normalization"] == {
        "source": "assistant_message_ids", "reported_num_turns": 5, "turn_count": 2}
    assert diagnostics["provider_accounting"]["turn_count"] == 2
    charge = _charge(request, response, diagnostics)
    assert charge.turns == 2
    assert charge.cost == Decimal(str(outer["total_cost_usd"]))
    if failure is None:
        assert response.turn_count == 2 and charge.source == "response"
    else:
        assert response is None and charge.source == "provider"
    assert outer == before


@pytest.mark.parametrize("usage", [None, {}, {"input_tokens": True}])
def test_assistant_identity_count_does_not_depend_on_optional_usage(usage):
    messages = [_assistant("first", usage), _assistant("second", usage)]
    response, failure, diagnostics = _provider_response(_request(), "stub-model",
        _provider_ndjson(*messages, _result()), final_text_output=True)
    assert failure is None and response.turn_count == 2
    assert diagnostics["provider_accounting"]["turn_count"] == 2


@pytest.mark.parametrize("identity", [None, "", 3, True])
def test_missing_any_assistant_identity_never_substitutes_partial_count(identity):
    messages = [*_messages(), _assistant(identity, {"input_tokens": 1})]
    response, failure, diagnostics = _provider_response(_request(), "stub-model",
        _provider_ndjson(*messages, _result()), final_text_output=True)
    assert response is None and failure == "turn_budget_exceeded"
    assert "provider_observed_turn_count" not in diagnostics
    assert "provider_turn_usage_normalization" not in diagnostics
    assert diagnostics["provider_accounting"]["turn_count"] == 5


def test_nested_assistant_events_do_not_count_as_main_agent_iterations():
    nested = _assistant("nested", {"input_tokens": 1})
    nested["parent_tool_use_id"] = "task-tool"
    response, failure, diagnostics = _provider_response(_request(), "stub-model",
        _provider_ndjson(*_messages(), nested, _result()), final_text_output=True)
    assert failure is None and response.turn_count == 2
    assert diagnostics["provider_accounting"]["turn_count"] == 2


def test_normalized_main_iterations_still_enforce_the_declared_cap():
    response, failure, diagnostics = _provider_response(_request(1), "stub-model",
        _provider_ndjson(*_messages(), _result()), final_text_output=True)
    assert response is None and failure == "turn_budget_exceeded"
    assert diagnostics["provider_accounting"]["turn_count"] == 2


@pytest.mark.parametrize("raw", [True, "5", -1, None])
def test_observed_message_ids_cannot_repair_malformed_raw_native_turn_usage(raw):
    outer = _result()
    outer["num_turns"] = raw
    response, failure, diagnostics = _provider_response(_request(), "stub-model",
        _provider_ndjson(*_messages(), outer), final_text_output=True)
    assert response is None and failure == "invalid_turn_usage"
    assert "provider_accounting" not in diagnostics


def test_terminal_only_normal_output_keeps_existing_raw_count_fallback():
    response, failure, diagnostics = _provider_response(_request(), "stub-model",
        _provider_ndjson(_result()), final_text_output=True)
    assert response is None and failure == "turn_budget_exceeded"
    assert diagnostics["provider_accounting"]["turn_count"] == 5


def test_legacy_structured_output_keeps_its_prior_turn_count():
    response, failure, diagnostics = _provider_response(_request(), "stub-model",
        _provider_ndjson(*_messages(), _result()))
    assert response is None and failure == "turn_budget_exceeded"
    assert diagnostics["provider_accounting"]["turn_count"] == 5
    assert "provider_turn_usage_normalization" not in diagnostics


def test_normalized_turns_cannot_settle_an_incomplete_token_aggregate():
    outer = _result()
    outer["usage"]["input_tokens"] = 1
    response, failure, diagnostics = _provider_response(_request(), "stub-model",
        _provider_ndjson(*_messages(), outer), final_text_output=True)
    assert response is None and failure == "invalid_provider_metadata"
    assert "provider_accounting" not in diagnostics
    assert _charge(_request(), response, diagnostics).source == "declared"
