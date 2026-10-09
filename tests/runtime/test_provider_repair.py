"""Reproduce mixed compaction/fallback billing and empty terminal streams."""
import json
from dataclasses import replace
from decimal import Decimal

import pytest

from hypoloom.claude_agent_host import _provider_response
from hypoloom.context_management import latest_context
from hypoloom.provider_observation import (
    ProviderObservation, observed_input_floor, provider_failure, provider_retry_gate,
    read_exchanges, summarize_exchanges,
)
from hypoloom.runtime_diagnostics import RuntimeDiagnostics
from test_claude_agent_host import _provider_ndjson
from test_context_capacity_projection import append, document, policy25, response
from test_partial_cli_accounting import _assistant, _charge, _request as agent_request, _result
from test_provider_relay import _relay, _request, upstream
from test_provider_traffic import ProviderTraffic, acquire, success


EMPTY_STREAM = (b'event: message_delta\ndata: {"type":"message_delta",'
    b'"delta":{"stop_reason":"end_turn"},"usage":{"input_tokens":0,'
    b'"output_tokens":0,"cache_read_input_tokens":0}}\n\n'
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n')


def empty_exchange(model="stub-model"):
    parser = ProviderObservation()
    parser.feed(EMPTY_STREAM)
    return {"request_path": "/v1/messages", "requested_model": model,
        "upstream_status": 200, "response_body_complete": True,
        "response_headers": {"content-type": "text/event-stream"},
        "failure_reason": None, **parser.finish()}


@pytest.mark.parametrize("event", [
    {"type": "content_block_start", "content_block": {"type": "text", "text": "answer"}},
    {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "call", "name": "Read", "input": {}}},
    {"type": "message_start", "message": {"content": [{"type": "text", "text": "answer"}]}},
])
def test_content_in_start_event_is_not_an_empty_zero_usage_response(event):
    from hypoloom.provider_observation import empty_generation_response
    parser = ProviderObservation()
    parser.feed(b'data: ' + json.dumps(event).encode() + b'\n\n' + EMPTY_STREAM)
    exchange = {**empty_exchange(), **parser.finish()}
    assert not empty_generation_response(exchange)
    assert exchange["first_content_at"] is not None


def mixed_bill(subtype="success"):
    # Exact model totals include compaction. Outer usage omits it, and adds
    # the input of the last ordinary answer again after the empty stream.
    usages = [dict(input_tokens=i, cache_read_input_tokens=c, output_tokens=o)
        for i, c, o in [(7, 11, 3), (2, 40, 9), (5, 13, 4), (3, 17, 2)]]
    exchanges = [{"request_path": "/v1/messages", "requested_model": "stub-model",
        "upstream_status": 200, "message_complete": True, "response_body_complete": True,
        "usage_valid": True, "served_models": ["stub-model"], "usage": u} for u in usages]
    exchanges.insert(3, empty_exchange())
    outer = _result(subtype)
    outer["usage"] = {"input_tokens": 20, "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 54, "output_tokens": 9}
    outer["modelUsage"] = {"stub-model": {"inputTokens": 17, "cacheCreationInputTokens": 0,
        "cacheReadInputTokens": 81, "outputTokens": 18, "costUSD": 0.01}}
    messages = [_assistant(str(i), usages[i]) for i in (0, 2, 3)]
    return outer, messages, exchanges


@pytest.mark.parametrize("subtype", ["success", "error_max_structured_output_retries"])
def test_mixed_compaction_and_empty_fallback_bill_uses_exact_wire_corroboration(subtype):
    outer, messages, exchanges = mixed_bill(subtype)
    request = agent_request()
    result, failure, diagnostics = _provider_response(request, "stub-model",
        _provider_ndjson(*messages, outer), observed_wire_input_floor=98,
        observed_wire_exchanges=exchanges)
    assert failure == (None if subtype == "success" else "provider_not_success")
    assert diagnostics["provider_usage_normalization"]["corroborated_by"] == "complete_wire_exchanges"
    assert diagnostics["provider_accounting"]["token_usage"] == {
        "input_tokens": 98, "output_tokens": 18, "total_tokens": 116}
    charge = _charge(request, result, diagnostics)
    assert charge.source == ("response" if subtype == "success" else "provider")
    assert charge.tokens == 116 and charge.cost == Decimal("0.01")


@pytest.mark.parametrize("damage", ["missing-wire", "model", "cost", "input", "output",
    "cache", "incomplete", "invalid-usage", "empty-model", "empty-input", "empty-output",
    "empty-cache", "empty-content", "empty-partial", "empty-error", "empty-unknown",
    "lower-output", "explicit-total"])
def test_mixed_bill_keeps_incomplete_or_inconsistent_metadata_rejected(damage):
    outer, messages, exchanges = mixed_bill()
    if damage == "missing-wire": exchanges = []
    elif damage == "model": exchanges[0]["served_models"] = ["another-model"]
    elif damage == "cost": outer["modelUsage"]["stub-model"]["costUSD"] = 0.02
    elif damage in {"input", "output", "cache"}:
        key = {"input": "input_tokens", "output": "output_tokens", "cache": "cache_read_input_tokens"}[damage]
        exchanges[0]["usage"][key] += 1
    elif damage == "incomplete": exchanges[0]["response_body_complete"] = False
    elif damage == "invalid-usage": exchanges[0]["usage_valid"] = False
    elif damage == "empty-model": exchanges[3]["requested_model"] = "another-model"
    elif damage in {"empty-input", "empty-output", "empty-cache"}:
        key = {"empty-input": "input_tokens", "empty-output": "output_tokens",
            "empty-cache": "cache_read_input_tokens"}[damage]
        exchanges[3]["usage"][key] = 1
    elif damage == "empty-content": exchanges[3]["first_content_at"] = 1
    elif damage == "empty-partial": exchanges[3]["response_body_complete"] = False
    elif damage == "empty-error": exchanges[3]["failure_reason"] = "transport_error"
    elif damage == "empty-unknown": del exchanges[3]["usage"]["input_tokens"]
    elif damage == "lower-output": outer["usage"]["output_tokens"] = 19
    else: outer["usage"]["total_tokens"] = 117
    result, failure, diagnostics = _provider_response(agent_request(), "stub-model",
        _provider_ndjson(*messages, outer), observed_wire_input_floor=98,
        observed_wire_exchanges=exchanges)
    assert result is None
    assert failure == ("invalid_token_usage" if damage == "explicit-total" else "invalid_provider_metadata")
    if damage == "explicit-total":
        assert diagnostics["provider_accounting"]["token_usage"]["total_tokens"] == 117
    else:
        assert "provider_usage_normalization" not in diagnostics
        assert "provider_accounting" not in diagnostics


@pytest.mark.parametrize("limit", [115, 116])
def test_mixed_bill_obeys_original_token_budget(limit):
    outer, messages, exchanges = mixed_bill()
    request = agent_request()
    request = replace(request, budget=replace(request.budget, max_tokens=limit))
    result, failure, diagnostics = _provider_response(request, "stub-model",
        _provider_ndjson(*messages, outer), observed_wire_input_floor=98,
        observed_wire_exchanges=exchanges)
    assert failure == ("token_budget_exceeded" if limit == 115 else None)
    assert (result is None) == (limit == 115)
    assert diagnostics["provider_accounting"]["token_usage"]["total_tokens"] == 116


def test_empty_terminal_stream_is_neither_successful_generation_nor_context_measurement(tmp_path):
    empty = {**empty_exchange(), "exchange": 2, "context_capacity": policy25()}
    measured = {**empty, "exchange": 1, "served_models": ["stub-model"],
        "usage": {"input_tokens": 100, "output_tokens": 7}}
    assert summarize_exchanges([empty])["completed_requests"] == 0
    assert observed_input_floor([empty]) is None
    latest = latest_context([measured, empty])
    assert latest["input_tokens"] is None and latest["source"] == "estimate"
    assert latest["last_observed_exchange"] == 1 and latest["last_observed_input_tokens"] == 100
    for data in [measured, empty]:
        p = tmp_path / f'{data["exchange"]:06d}'
        p.mkdir()
        (p / "exchange.json").write_text(json.dumps(data))
    assert len(read_exchanges(tmp_path, latest_context_only=True)) == 2
    traffic = ProviderTraffic()
    traffic.finish(acquire(traffic), empty)
    assert traffic.recovered_after(0) is None
    traffic.finish(acquire(traffic), success())
    assert traffic.recovered_after(0) is not None


def test_real_relay_empty_stream_cannot_replace_measured_input_with_zero(tmp_path, upstream):
    root = tmp_path / "diagnostics"
    relay = _relay(tmp_path, upstream, context_policy=policy25(), max_request_bytes=1000000,
        diagnostics=RuntimeDiagnostics(root))
    old = document("x" * 190000)
    try:
        upstream.body = response(190000)
        assert _request(relay, body=json.dumps(old).encode())[0] == 200
        upstream.body = EMPTY_STREAM
        def send_empty(handler):
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Content-Length", str(len(EMPTY_STREAM)))
            handler.end_headers()
            handler.wfile.write(EMPTY_STREAM)
        upstream.script = send_empty
        assert _request(relay, body=json.dumps(old).encode())[0] == 200
        large = append(old, "y" * 25000)
        assert _request(relay, body=json.dumps(large).encode())[0] == 400
    finally:
        relay.close()
    assert len(upstream.calls) == 2
    exchanges = read_exchanges(root / "provider")
    assert exchanges[-1]["context_capacity"]["anchor_exchange"] == 1
    assert exchanges[-1]["context_capacity"]["estimated_input_tokens"] > 205568


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_transient_server_failure_uses_existing_backoff_without_exhausting_node(status):
    failure = provider_failure([{"request_path": "/v1/messages", "upstream_status": status,
        "ended_at": 100, "response_headers": {"retry-after": "120"}}])
    assert failure["code"] == "service_unavailable" and failure["retry_at"] == 220
    rows = [{**failure, "attempt_id": str(i), "started_at": 1000 * i,
        "ended_at": 1000 * i + 1, "retry_at": 1000 * i + 121} for i in range(6)]
    waiting = provider_retry_gate(rows, cooldown_seconds=18000, cleared_at=None, now=5002, attempt_limit=3)
    assert waiting["state"] == "cooldown" and not waiting["requires_operator"]
    assert waiting["next_retry_at"] == 5901
    recovered = provider_retry_gate([*rows, {"attempt_id": "recovered", "started_at": 5902,
        "ended_at": 5903, "provider_success": True}], cooldown_seconds=18000,
        cleared_at=None, now=5904, attempt_limit=3)
    assert recovered["state"] == "ready"


@pytest.mark.parametrize("exchange", [
    {"relay_status": 502, "failure_reason": "transport_error", "failure_stage": "credential_scan"},
    {"relay_status": 504, "failure_reason": "relay_closed"},
    {"upstream_status": 200, "response_body_complete": False, "failure_reason": "transport_error"},
    {"upstream_status": 501}, {"upstream_status": 404},
])
def test_local_failures_and_other_statuses_do_not_claim_temporary_server_outage(exchange):
    assert provider_failure([exchange]) is None


@pytest.mark.parametrize("window", [False, True])
def test_server_failure_defers_next_real_relay_request_without_forwarding(tmp_path, upstream, window):
    from hypoloom.provider_observation import provider_status_text
    from test_provider_observation import WINDOW_ERROR
    traffic = ProviderTraffic()
    root = tmp_path / "diagnostics"
    relay = _relay(tmp_path, upstream, diagnostics=RuntimeDiagnostics(root),
        traffic_binding=(traffic, ("run", "grounding"), None))
    try:
        upstream.status = 403 if window else 503
        upstream.body = json.dumps(WINDOW_ERROR).encode() if window else b'{"error":{"type":"ServiceUnavailableError"}}'
        upstream.extra_headers = {"Retry-After": "120"}
        assert _request(relay)[0] == upstream.status
        assert _request(relay)[0] == (429 if window else 503)
    finally:
        relay.close()
    assert len(upstream.calls) == 1
    assert traffic.snapshot()["in_flight"] == traffic.snapshot()["queued"] == 0
    exchanges = read_exchanges(root / "provider")
    assert exchanges[1]["request_bytes_forwarded"] == 0
    code = "usage_window_exhausted" if window else "service_unavailable"
    assert exchanges[1]["failure_reason"] == code
    failure = provider_failure(exchanges)
    assert failure["code"] == code and failure["retry_after_seconds"] <= 120
    assert exchanges[1]["response_headers"]["retry-after"] == "120"
    status = {"state": "cooldown", "reason": code, "next_retry_at": 220}
    assert ("usage window quota exhausted" if window else "provider temporarily unavailable") in provider_status_text(status)
    assert ("当前时间窗口额度已耗尽" if window else "模型服务暂时不可用") in provider_status_text(status, language="zh-CN")
