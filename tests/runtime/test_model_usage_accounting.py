"""Native CLI's per-model ledger includes fallback JSON absent from stream usage."""

from dataclasses import replace
from decimal import Decimal

import pytest

from hypoloom.claude_agent_host import _provider_response, _session_usage_view
from tests.runtime.test_claude_agent_host import _host, _provider_ndjson, _start
from tests.runtime.test_partial_cli_accounting import _charge, _messages, _request, _result
from tests.runtime.test_partial_cli_accounting import _assistant
from tests.runtime.test_rejected_workspace_accounting import (
    _assert_rejected, _charge as _workspace_charge, _observe, _output,
)


def _ledger_result(subtype="success"):
    result = _result(subtype)
    result["modelUsage"] = {"stub-model": {
        "inputTokens": 10, "cacheCreationInputTokens": 13,
        "cacheReadInputTokens": 40, "outputTokens": 19, "costUSD": "0.0100",
    }}
    return result


def _duplicated_fallback_usage(subtype="success"):
    # CC 2.1.289 counts the interrupted stream-start input in outer usage,
    # then counts the fallback JSON again. Its model ledger bills it once.
    result = _ledger_result(subtype)
    result["usage"] = {"input_tokens": 38, "cache_creation_input_tokens": 6,
        "cache_read_input_tokens": 10, "output_tokens": 11}
    result["modelUsage"]["stub-model"].update(inputTokens=21,
        cacheCreationInputTokens=3, cacheReadInputTokens=5, outputTokens=11)
    ordinary = {"input_tokens": 1, "output_tokens": 1}
    fallback = {"input_tokens": 17, "cache_creation_input_tokens": 3,
        "cache_read_input_tokens": 5, "output_tokens": 7}
    messages = [_assistant(str(i), ordinary) for i in range(4)] + [_assistant("fallback", fallback)]
    exchanges = [{"request_path": "/v1/messages", "upstream_status": 200,
        "message_complete": True, "response_body_complete": True,
        "usage_valid": True, "served_models": ["stub-model"], "usage": dict(usage)}
        for usage in [ordinary] * 4 + [fallback, fallback]]
    exchanges[-2]["message_complete"] = False
    exchanges[-2]["usage"]["output_tokens"] = 0
    return result, messages, exchanges


@pytest.mark.parametrize("subtype", ["success", "error_max_structured_output_retries"])
def test_wire_corroborated_model_ledger_removes_duplicate_partial_stream_input(subtype):
    result, messages, exchanges = _duplicated_fallback_usage(subtype)
    request = _request()
    response, failure, diagnostics = _provider_response(request, "stub-model",
        _provider_ndjson(*messages, result), observed_wire_exchanges=exchanges)
    assert failure == (None if subtype == "success" else "provider_not_success")
    assert diagnostics["provider_observed_input_tokens_lower_bound"] == 29
    assert diagnostics["provider_usage_normalization"] == {
        "source": "modelUsage", "outer_input_tokens": 54, "outer_output_tokens": 11,
        "selected_input_tokens": 29, "selected_output_tokens": 11,
        "corroborated_by": "complete_wire_exchanges"}
    assert diagnostics["provider_accounting"]["token_usage"] == {
        "input_tokens": 29, "output_tokens": 11, "total_tokens": 40}
    charge = _charge(request, response, diagnostics)
    assert (charge.source, charge.tokens, charge.cost) == (
        "response" if subtype == "success" else "provider", 40, Decimal("0.01"))
    if response is not None:
        assert response.token_usage == diagnostics["provider_accounting"]["token_usage"]
        assert [response.diagnostics[key] for key in (
            "uncached_input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")] == [21, 3, 5]


@pytest.mark.parametrize("damage", ["absent", "different-model", "multiple-models", "incomplete",
    "invalid-usage", "different-cache-semantics", "invalid-context", "different-input", "different-output",
    "different-cache", "ledger-cost", "explicit-total"])
def test_duplicate_input_correction_requires_exact_wire_corroboration_and_preserves_total(damage):
    result, messages, exchanges = _duplicated_fallback_usage()
    if damage == "absent": exchanges = None
    elif damage == "different-model": exchanges[-1]["served_models"] = ["another-model"]
    elif damage == "multiple-models": exchanges[-1]["served_models"] += ["another-model"]
    elif damage == "incomplete": exchanges[-1]["response_body_complete"] = False
    elif damage == "invalid-usage": exchanges[-1]["usage_valid"] = False
    elif damage == "different-cache-semantics":
        exchanges[-1]["context_capacity"] = {"input_usage_semantics": "input_includes_cache"}
    elif damage == "invalid-context": exchanges[-1]["context_capacity"] = "unknown"
    elif damage == "different-input": exchanges[-1]["usage"]["input_tokens"] += 1
    elif damage == "different-output": exchanges[-1]["usage"]["output_tokens"] += 1
    elif damage == "different-cache":
        exchanges[-1]["usage"]["input_tokens"] += 1
        exchanges[-1]["usage"]["cache_read_input_tokens"] -= 1
    elif damage == "ledger-cost": result["modelUsage"]["stub-model"]["costUSD"] = "0.02"
    else: result["usage"]["total_tokens"] = 65
    response, failure, diagnostics = _provider_response(_request(), "stub-model",
        _provider_ndjson(*messages, result), observed_wire_exchanges=exchanges)
    if damage == "explicit-total":
        assert response is None and failure == "invalid_token_usage"
        assert diagnostics["provider_accounting"]["token_usage"] == {
            "input_tokens": 29, "output_tokens": 11, "total_tokens": 65}
    else:
        assert failure is None and response is not None
        assert "provider_usage_normalization" not in diagnostics
        assert response.token_usage == {"input_tokens": 54, "output_tokens": 11, "total_tokens": 65}


def test_wire_corroboration_does_not_release_an_unaccounted_assistant_reservation():
    result, messages, exchanges = _duplicated_fallback_usage()
    messages.append(_assistant("unaccounted", {"input_tokens": 30, "output_tokens": 1}))
    request = _request()
    response, failure, diagnostics = _provider_response(request, "stub-model",
        _provider_ndjson(*messages, result), observed_wire_exchanges=exchanges)
    assert response is None and failure == "invalid_provider_metadata"
    assert diagnostics["provider_observed_input_tokens_lower_bound"] == 59
    assert "provider_accounting" not in diagnostics
    assert diagnostics["provider_reported_lower_bound"]["token_usage"]["total_tokens"] == 40
    assert _charge(request, response, diagnostics).source == "declared"


@pytest.mark.parametrize("maximum", [39, 40])
def test_corrected_model_bill_remains_subject_to_exact_token_budget(maximum):
    result, messages, exchanges = _duplicated_fallback_usage()
    request = _request()
    request = replace(request, budget=replace(request.budget, max_tokens=maximum))
    response, failure, diagnostics = _provider_response(request, "stub-model",
        _provider_ndjson(*messages, result), observed_wire_exchanges=exchanges)
    assert failure == ("token_budget_exceeded" if maximum == 39 else None)
    assert (response is None) == (maximum == 39)
    assert diagnostics["provider_accounting"]["token_usage"]["total_tokens"] == 40


@pytest.mark.parametrize("subtype", ["success", "error_max_structured_output_retries"])
def test_complete_model_ledger_normalizes_missing_final_json_usage_and_preserves_reported_cost(subtype):
    request = _request()
    response, failure, diagnostics = _provider_response(request, "stub-model",
        _provider_ndjson(*_messages(), _ledger_result(subtype)))
    assert failure == (None if subtype == "success" else "provider_not_success")
    assert diagnostics["provider_observed_input_tokens_lower_bound"] == 63
    assert "provider_reported_lower_bound" not in diagnostics
    assert diagnostics["provider_accounting"] == {
        "token_usage": {"input_tokens": 63, "output_tokens": 19, "total_tokens": 82},
        "turn_count": 1, "cost": "0.01", "cost_currency": "USD",
    }
    if response is not None:
        assert response.token_usage == diagnostics["provider_accounting"]["token_usage"]
        assert response.diagnostics["uncached_input_tokens"] == 10
        assert response.diagnostics["cache_creation_input_tokens"] == 13
        assert response.diagnostics["cache_read_input_tokens"] == 40
    charge = _charge(request, response, diagnostics)
    assert (charge.source, charge.tokens, charge.cost) == (
        "response" if subtype == "success" else "provider", 82, Decimal("0.01"))


@pytest.mark.parametrize("damage", [
    "missing-input", "missing-output", "missing-creation", "missing-read", "missing-cost",
    "boolean-input", "string-output", "negative-creation", "null-read",
    "invalid-cost", "mismatch-cost", "multiple-models", "wrong-model",
    "lower-input", "lower-output", "lower-creation", "lower-read",
])
def test_unusable_model_ledger_keeps_partial_outer_accounting_unknown(damage):
    result = _ledger_result()
    model = result["modelUsage"]["stub-model"]
    fields = {"input": "inputTokens", "output": "outputTokens",
              "creation": "cacheCreationInputTokens", "read": "cacheReadInputTokens",
              "cost": "costUSD"}
    if damage.startswith("missing-"):
        del model[fields[damage.removeprefix("missing-")]]
    elif damage.startswith("lower-"):
        field = damage.removeprefix("lower-")
        model[fields[field]] = {"input": 6, "output": 10, "creation": 12, "read": 16}[field]
        # Keep the total high enough that only the regressing component disqualifies it.
        model["cacheReadInputTokens" if field != "read" else "inputTokens"] = 100
    elif damage == "multiple-models":
        result["modelUsage"]["other-model"] = dict(model)
    elif damage == "wrong-model":
        result["modelUsage"] = {"other-model": model}
    else:
        field, value = {"boolean-input": ("inputTokens", True),
            "string-output": ("outputTokens", "19"),
            "negative-creation": ("cacheCreationInputTokens", -1),
            "null-read": ("cacheReadInputTokens", None),
            "invalid-cost": ("costUSD", "NaN"),
            "mismatch-cost": ("costUSD", "0.02")}[damage]
        model[field] = value
    request = _request()
    response, failure, diagnostics = _provider_response(request, "stub-model",
        _provider_ndjson(*_messages(), result))
    assert response is None and failure == "invalid_provider_metadata"
    assert "provider_accounting" not in diagnostics
    assert diagnostics["provider_reported_lower_bound"]["token_usage"] == {
        "input_tokens": 37, "output_tokens": 11, "total_tokens": 48}
    assert _charge(request, response, diagnostics).source == "declared"


@pytest.mark.parametrize("field,value", [
    ("input_tokens", True), ("output_tokens", "11"),
    ("cache_creation_input_tokens", -1), ("cache_read_input_tokens", None),
    ("total_tokens", False),
])
def test_good_model_ledger_cannot_replace_malformed_outer_usage(field, value):
    result = _ledger_result()
    result["usage"][field] = value
    response, failure, diagnostics = _provider_response(_request(), "stub-model",
        _provider_ndjson(*_messages(), result))
    assert response is None and failure == "invalid_provider_metadata"
    assert "provider_accounting" not in diagnostics
    assert "provider_reported_lower_bound" not in diagnostics
    assert "provider_accounting_error" in diagnostics


def test_complete_model_ledger_still_obeys_observed_input_lower_bound():
    result = _ledger_result()
    result["modelUsage"]["stub-model"]["cacheReadInputTokens"] = 39
    request = _request()
    response, failure, diagnostics = _provider_response(request, "stub-model",
        _provider_ndjson(*_messages(), result))
    assert response is None and failure == "invalid_provider_metadata"
    assert "provider_accounting" not in diagnostics
    assert diagnostics["provider_reported_lower_bound"]["token_usage"] == {
        "input_tokens": 62, "output_tokens": 19, "total_tokens": 81}
    assert _charge(request, response, diagnostics).source == "declared"


@pytest.mark.parametrize("total", [48, 99])
def test_model_ledger_never_reduces_valid_outer_total_tokens(total):
    result = _ledger_result()
    result["usage"]["total_tokens"] = total
    response, failure, diagnostics = _provider_response(_request(), "stub-model",
        _provider_ndjson(*_messages(), result))
    accounting = diagnostics["provider_accounting"]["token_usage"]
    assert accounting["total_tokens"] == max(82, total)
    if total > 82:
        assert response is None and failure == "invalid_token_usage"
    else:
        assert failure is None and response is not None
        assert response.token_usage == accounting


@pytest.mark.parametrize("kind,expected", [
    ("token", "token_budget_exceeded"), ("cost", "cost_budget_exceeded"),
    ("turn", "turn_budget_exceeded"),
])
def test_complete_model_ledger_remains_subject_to_existing_budgets(kind, expected):
    request = _request()
    result = _ledger_result()
    if kind == "token":
        request = replace(request, budget=replace(request.budget, max_tokens=81))
    elif kind == "cost":
        result["total_cost_usd"] = 2
        result["modelUsage"]["stub-model"]["costUSD"] = 2
    else:
        result["num_turns"] = request.budget.max_turns + 1
    response, failure, diagnostics = _provider_response(request, "stub-model",
        _provider_ndjson(*_messages(), result))
    assert response is None and failure == expected
    assert diagnostics["provider_accounting"]["token_usage"]["total_tokens"] == 82


@pytest.mark.parametrize("outer_usage", [None, {"input_tokens": 0, "output_tokens": 0}])
def test_error_fallback_counts_uncached_and_optional_cache_buckets_once(outer_usage):
    result = _ledger_result("error_max_structured_output_retries")
    result["usage"] = outer_usage
    model = result["modelUsage"]["stub-model"]
    del model["costUSD"]
    response, failure, diagnostics = _provider_response(_request(), "stub-model",
        _provider_ndjson(*_messages(), result))
    assert response is None and failure == "provider_not_success"
    assert diagnostics["provider_accounting"]["token_usage"] == {
        "input_tokens": 63, "output_tokens": 19, "total_tokens": 82}


@pytest.mark.parametrize("cost", [True, "NaN", "0.02"])
def test_error_fallback_rejects_present_invalid_or_conflicting_model_cost(cost):
    result = _ledger_result("error_max_structured_output_retries")
    result["usage"] = {"input_tokens": 0, "output_tokens": 0}
    result["modelUsage"]["stub-model"]["costUSD"] = cost
    response, failure, diagnostics = _provider_response(_request(), "stub-model",
        _provider_ndjson(*_messages(), result))
    assert response is None and failure == "provider_not_success"
    assert "provider_accounting" not in diagnostics
    assert "provider_reported_lower_bound" not in diagnostics
    assert "provider_accounting_error" in diagnostics


def test_nonzero_exit_preserves_failure_and_normalized_model_accounting(tmp_path):
    host = _host(tmp_path)
    request = _request()
    observed = host.wait(_start(host, request=request, mode="diagnostic",
        extra_environment={"STUB_STDOUT_DIAGNOSTIC": _provider_ndjson(
            *_messages(), _ledger_result("error_max_structured_output_retries")).decode(),
            "STUB_DIAGNOSTIC": ""}))
    assert observed.failure_code == "nonzero_exit" and observed.response is None
    assert _charge(request, observed.response, observed.diagnostics).tokens == 82


def test_workspace_capture_rejection_preserves_model_usage_without_accepting_candidate(tmp_path):
    result = _output()
    result["modelUsage"] = _ledger_result()["modelUsage"]
    result["modelUsage"]["stub-model"]["costUSD"] = result["total_cost_usd"]
    request, observed = _observe(tmp_path,
        _provider_ndjson(*_messages(), result).decode().rstrip("\n"))
    _assert_rejected(observed)
    charge = _workspace_charge(request, observed)
    assert (charge.source, charge.tokens, charge.cost) == (
        "provider", 82, Decimal("0.108265"))


@pytest.mark.parametrize("subtype", ["success", "error_max_structured_output_retries"])
def test_resumed_cumulative_ledger_bills_only_the_invocation_after_deducting_carry(subtype):
    # Since CC 2.1.277 a --resume/--fork-session result ledger accumulates over
    # the transcript's saved cost-state record; the recorded baseline restores
    # the exact per-Attempt bill, and an unused carried model drops out before
    # the one-exact-identity validation.
    result = _ledger_result(subtype)
    result["total_cost_usd"] = 0.021
    result["modelUsage"] = {
        "stub-model": {"inputTokens": 16, "cacheCreationInputTokens": 13,
            "cacheReadInputTokens": 40, "outputTokens": 25, "costUSD": "0.0200"},
        "carried-model": {"inputTokens": 1, "outputTokens": 1, "costUSD": 0.001},
    }
    baseline = {"type": "cost-state", "totalCostUSD": 0.011, "modelUsage": {
        "stub-model": {"inputTokens": 6, "outputTokens": 6, "costUSD": 0.01},
        "carried-model": {"inputTokens": 1, "outputTokens": 1, "costUSD": 0.001}}}
    request = _request()
    response, failure, diagnostics = _provider_response(request, "stub-model",
        _provider_ndjson(*_messages(), result), session_usage_baseline=baseline)
    assert failure == (None if subtype == "success" else "provider_not_success")
    assert diagnostics["provider_session_carryover"] == {
        "source": "session_cost_state",
        "model_usage": {
            "stub-model": {"inputTokens": 6, "outputTokens": 6,
                "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0},
            "carried-model": {"inputTokens": 1, "outputTokens": 1,
                "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0}},
        "total_cost_usd": 0.011, "clamped": []}
    assert diagnostics["provider_accounting"] == {
        "token_usage": {"input_tokens": 63, "output_tokens": 19, "total_tokens": 82},
        "turn_count": 1, "cost": "0.01", "cost_currency": "USD"}
    if response is not None:
        assert response.token_usage == diagnostics["provider_accounting"]["token_usage"]
    charge = _charge(request, response, diagnostics)
    assert (charge.source, charge.tokens, charge.cost) == (
        "response" if subtype == "success" else "provider", 82, Decimal("0.01"))
    # The same cumulative ledger without its recorded carry is a different bill.
    response, failure, diagnostics = _provider_response(request, "stub-model",
        _provider_ndjson(*_messages(), result))
    assert response is None and failure is not None
    assert "provider_session_carryover" not in diagnostics
    accounting = diagnostics.get("provider_accounting")
    assert accounting is None or accounting["token_usage"] != {
        "input_tokens": 63, "output_tokens": 19, "total_tokens": 82}


def test_carryover_clamped_flag_marks_only_negative_deductions():
    # A carry exceeding the invocation's own ledger is an inconsistent bill:
    # the deduction still floors at zero, but the summary names what clamped.
    result = _ledger_result()
    baseline = {"type": "cost-state", "totalCostUSD": 0.005, "modelUsage": {
        "stub-model": {"inputTokens": 0, "outputTokens": 0, "costUSD": 0.005}}}
    request = _request()
    response, failure, diagnostics = _provider_response(request, "stub-model",
        _provider_ndjson(*_messages(), result), session_usage_baseline=baseline)
    assert failure is None and response is not None
    assert diagnostics["provider_session_carryover"]["clamped"] == []
    assert diagnostics["provider_accounting"] == {
        "token_usage": {"input_tokens": 63, "output_tokens": 19, "total_tokens": 82},
        "turn_count": 1, "cost": "0.005", "cost_currency": "USD"}

    result = _ledger_result()
    baseline = {"type": "cost-state", "totalCostUSD": 0.02, "modelUsage": {
        "stub-model": {"inputTokens": 11, "outputTokens": 6, "costUSD": 0.005}}}
    response, failure, diagnostics = _provider_response(request, "stub-model",
        _provider_ndjson(*_messages(), result), session_usage_baseline=baseline)
    summary = diagnostics["provider_session_carryover"]
    # inputTokens 10 - 11 and total 0.01 - 0.02 both underflow; costUSD and
    # outputTokens still deduct cleanly, so only those two are named.
    assert summary["clamped"] == ["stub-model", "total_cost_usd"]
    model = summary["model_usage"]["stub-model"]
    assert model == {"inputTokens": 11, "outputTokens": 6,
        "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0}
    # The inconsistent ledger still fails closed on the existing guards.
    assert response is None and failure == "invalid_provider_metadata"

    # Deduction semantics are unchanged: clamped fields floor at zero.
    view = _session_usage_view(result, baseline)
    adjusted = view["modelUsage"]["stub-model"]
    assert (adjusted["inputTokens"], adjusted["outputTokens"]) == (0, 13)
    assert view["total_cost_usd"] == 0.0
    assert adjusted["costUSD"] == 0.005
