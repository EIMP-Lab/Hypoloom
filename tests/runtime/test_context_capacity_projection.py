"""Measured-history regression cases use synthetic text, never private reports."""
import copy
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from hypoloom.context_management import (
    context_policy, counting_material, latest_context, request_capacity, usage_anchor,
)
from hypoloom.provider_observation import read_exchanges
from hypoloom.runtime_diagnostics import RuntimeDiagnostics
from test_context_management import request24
from test_provider_relay import _connection, _relay, _request, upstream


def policy25(**memory):
    return context_policy(replace(request24(**memory), schema_version=25))


def document(text, *, model="claude-sonnet-4-6"):
    return {"model": model, "max_tokens": 32000, "stream": True,
            "messages": [{"role": "user", "content": text}]}


def response(inputs, *, output=12, **cache):
    return json.dumps({"type": "message", "model": "claude-sonnet-4-6",
        "stop_reason": "end_turn", "usage": {"input_tokens": inputs,
        "output_tokens": output, **cache}}).encode()


def append(doc, text):
    result = copy.deepcopy(doc)
    result["messages"].extend([{"role": "assistant", "content": "read next source"},
        {"role": "user", "content": text}])
    return result


@pytest.mark.parametrize("observed,added", [(51311, 7000), (59326, 12000), (68699, 8000)])
def test_representative_successful_text_history_is_not_recounted_as_bytes(observed, added):
    old = document("x" * 200000)
    new = append(old, "中" * (added // 3))
    policy = policy25()
    anchor = usage_anchor(old, {"input_tokens": observed, "output_tokens": 20}, policy, exchange=7)
    assert request_capacity(old, policy)["fits"]
    legacy = request_capacity(new, context_policy(request24()))
    assert not legacy["fits"]
    capacity = request_capacity(new, policy, anchor=anchor)
    assert capacity["fits"] and capacity["estimated_input_tokens"] < observed + added + 512
    assert capacity["estimate_source"] == "provider_usage_plus_delta_estimate"
    assert capacity["anchor_exchange"] == 7
    assert capacity["request_input_bytes"] == legacy["estimated_input_tokens"]
    # The old ACI keeps its byte-based rule, including when offered an anchor.
    assert request_capacity(new, context_policy(request24()), anchor=anchor) == legacy


def test_only_single_equivalent_cached_text_block_is_normalized():
    old = document([{"type": "text", "text": "known text", "cache_control": {"type": "ephemeral"}}])
    new = append(document("known text"), "new result")
    before = copy.deepcopy(old)
    policy = policy25()
    anchor = usage_anchor(old, {"input_tokens": 5, "output_tokens": 2}, policy)
    assert request_capacity(new, policy, anchor=anchor)["estimate_reason"] == "unchanged_prefix"
    assert old == before  # Measurement cannot alter cached or signed wire blocks.
    for block in [{"type": "thinking", "thinking": "known text", "signature": "sig"},
                  {"type": "text", "text": "known text", "citations": []},
                  {"type": "text", "text": "known text", "cache_control": {"type": "unknown"}}]:
        other = usage_anchor(document([block]), {"input_tokens": 5, "output_tokens": 2}, policy)
        assert request_capacity(new, policy, anchor=other)["estimate_reason"] == "history_or_configuration_changed"


@pytest.mark.parametrize("change", ["history", "system", "tools", "model", "thinking", "compact"])
def test_history_configuration_and_native_compaction_invalidate_projection(change):
    old = document("original")
    old.update(system="instructions", tools=[{"name": "Read", "input_schema": {"type": "object"}}],
               thinking={"type": "enabled", "budget_tokens": 1000})
    new = append(old, "result")
    if change == "history": new["messages"][0]["content"] = "changed"
    if change == "system": new["system"] = "other instructions"
    if change == "tools": new["tools"] = []
    if change == "model": new["model"] = "different"
    if change == "thinking": new["thinking"]["budget_tokens"] = 2000
    if change == "compact": new["messages"] = [{"role": "user", "content": "summary"}]
    policy = policy25()
    anchor = usage_anchor(old, {"input_tokens": 10, "output_tokens": 2}, policy)
    capacity = request_capacity(new, policy, anchor=anchor)
    assert capacity["estimate_source"] == "utf8_bytes_conservative_estimate"
    assert capacity["estimate_reason"] == "history_or_configuration_changed"


def test_unknown_cache_semantics_partial_usage_and_new_multimodal_data_do_not_get_projection():
    old = document("original")
    policy = policy25(input_usage_semantics="unknown")
    assert usage_anchor(old, {"input_tokens": 1, "output_tokens": 1}, policy) is None
    assert request_capacity(old, policy)["estimate_reason"] == "unknown_usage_semantics"
    policy = policy25()
    for usage in ({"input_tokens": 1}, {"output_tokens": 1},
                  {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": None}):
        assert usage_anchor(old, usage, policy) is None
    anchor = usage_anchor(old, {"input_tokens": 1, "output_tokens": 1}, policy)
    new = append(old, [{"type": "image", "source": {"type": "url", "url": "https://example.invalid/x"}}])
    assert request_capacity(new, policy, anchor=anchor)["estimate_reason"] == "unsupported_added_content"
    opaque = append(old, [{"type": "redacted_thinking", "data": "opaque-provider-data"}])
    assert request_capacity(opaque, policy, anchor=anchor)["estimate_reason"] == "unsupported_added_content"


def test_cache_accounting_and_actual_overflow_keep_reserves():
    old = document("known")
    policy = policy25()
    anchor = usage_anchor(old, {"input_tokens": 5000, "output_tokens": 12,
        "cache_read_input_tokens": 200000, "cache_creation_input_tokens": 1000}, policy)
    capacity = request_capacity(append(old, "more"), policy, anchor=anchor)
    assert not capacity["fits"]
    assert capacity["available_input_tokens"] == 205568
    assert capacity["output_reserve_tokens"] == 32000
    includes = usage_anchor(old, {"input_tokens": 100, "output_tokens": 12, "cache_read_input_tokens": 80},
                            policy25(input_usage_semantics="includes_cache"))
    assert includes["input_tokens"] == 100


def test_aci25_inline_allowance_does_not_shrink_on_ordinary_attempts():
    base = replace(request24(), schema_version=25)
    assert {context_policy(replace(base, attempt_ordinal=i))["max_inline_input_bytes"]
            for i in range(1, 6)} == {49152}


def test_relay_reuses_complete_same_request_count_without_new_probe(tmp_path, upstream):
    policy = policy25()
    doc = document("x" * 220000)
    root = tmp_path / "diagnostics"
    relay = _relay(tmp_path, upstream, context_policy=policy, max_request_bytes=500000,
                   diagnostics=RuntimeDiagnostics(root))
    try:
        upstream.body = b'{"input_tokens":60000}'
        count = {key: value for key, value in doc.items() if key not in {"stream", "max_tokens"}}
        assert _request(relay, path="/v1/messages/count_tokens", body=json.dumps(count).encode())[0] == 200
        upstream.body = response(60000)
        assert _request(relay, body=json.dumps(doc).encode())[0] == 200
    finally:
        relay.close()
    exchanges = read_exchanges(root / "provider")
    capacity = exchanges[-1]["context_capacity"]
    assert capacity["estimate_source"] == "provider_count_tokens"
    assert capacity["estimated_input_tokens"] == 60000
    assert len(upstream.calls) == 2


def test_relay_uses_anchor_and_preserves_wire_then_latches_true_capacity_refusal(tmp_path, upstream):
    root = tmp_path / "diagnostics"
    relay = _relay(tmp_path, upstream, context_policy=policy25(), max_request_bytes=1000000,
                   diagnostics=RuntimeDiagnostics(root))
    old = document([{"type": "text", "text": "x" * 200000, "cache_control": {"type": "ephemeral"}}])
    new = append(document("x" * 200000), "result" * 3000)
    try:
        upstream.body = response(50000)
        assert _request(relay, body=json.dumps(old).encode())[0] == 200
        upstream.body = response(60000)
        wire = json.dumps(new).encode()
        assert _request(relay, body=wire)[0] == 200
        assert upstream.calls[-1][2] == wire
        huge = append(new, "z" * 200000)
        assert _request(relay, body=json.dumps(huge).encode())[0] == 400
        assert _request(relay, body=json.dumps(old).encode())[0] == 400
    finally:
        relay.close()
    exchanges = read_exchanges(root / "provider")
    assert len(upstream.calls) == 2 and relay.context_limit_reached
    assert exchanges[1]["context_capacity"]["estimate_source"] == "provider_usage_plus_delta_estimate"
    latest = latest_context(exchanges, policy25())
    assert latest["last_observed_input_tokens"] == 60000 and latest["last_observed_exchange"] == 2
    assert latest["input_tokens"] is None and latest["current_request_exchange"] == 4


@pytest.mark.parametrize("failure", ["count404", "invalid_cache", "partial", "protocol"])
def test_count_failure_invalid_usage_or_protocol_change_cannot_admit_large_history(tmp_path, upstream, failure):
    relay = _relay(tmp_path, upstream, context_policy=policy25(), max_request_bytes=500000)
    old, new = document("x" * 200000), document("x" * 220000)
    if failure == "protocol": new = append(old, "z" * 20000)
    try:
        if failure == "count404":
            upstream.status = 404
            assert _request(relay, path="/v1/messages/count_tokens", body=json.dumps(new).encode())[0] == 404
            upstream.status = 200
        else:
            upstream.body = (response(50000, cache_read_input_tokens=None) if failure == "invalid_cache"
                             else b'{"type":"message","usage":{"input_tokens":50000,"output_tokens":1}}'
                             if failure == "partial" else response(50000))
            assert _request(relay, body=json.dumps(old).encode())[0] == 200
        code, body = _request(relay, body=json.dumps(new).encode(),
                             headers={"anthropic-version": "different"} if failure == "protocol" else None)
        assert code == 400 and b"cannot confirm" in body
        assert len(upstream.calls) == 1  # No automatic count probe or hidden generation.
    finally:
        relay.close()


def test_native_shrink_replaces_anchor_only_after_success_and_notice_uses_recent_observation(tmp_path, upstream):
    root = tmp_path / "diagnostics"
    relay = _relay(tmp_path, upstream, context_policy=policy25(working_context_tokens=2000),
                   diagnostics=RuntimeDiagnostics(root))
    old = document("x" * 1200)
    old["max_tokens"] = 10
    shrink = document("brief summary")
    shrink["max_tokens"] = 10
    try:
        upstream.body = response(1500)
        assert _request(relay, body=json.dumps(old).encode())[0] == 200
        notice = relay.context_notice()
        assert "last complete provider input: 1500" in notice
        assert "existing summary fields" in notice and "permitted writable authoring space" in notice
        upstream.body = response(50)
        assert _request(relay, body=json.dumps(shrink).encode())[0] == 200
        assert relay.context_notice() is None
        assert _request(relay, body=json.dumps(append(shrink, "y" * 1000)).encode())[0] == 200
    finally:
        relay.close()
    exchanges = read_exchanges(root / "provider")
    assert exchanges[1]["context_capacity"]["estimate_reason"] == "history_or_configuration_changed"
    assert exchanges[2]["context_capacity"]["anchor_input_tokens"] == 50


def test_next_generation_waits_for_complete_http_usage_under_existing_deadline(tmp_path, upstream):
    stopped, release = threading.Event(), threading.Event()
    response_events = (b'data: {"type":"message_start","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":100,"output_tokens":1}}}\n\n'
        b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":5}}\n\n'
        b'data: {"type":"message_stop"}\n\n')
    def script(handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.end_headers()
        handler.wfile.write(response_events)
        handler.wfile.flush()
        if len(upstream.calls) == 1:
            stopped.set()
            release.wait(2)
    upstream.script = script
    relay = _relay(tmp_path, upstream, context_policy=policy25(working_context_tokens=2000),
                   deadline_at=time.monotonic() + 5)
    old = document("x" * 1200)
    old["max_tokens"] = 10
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(_request, relay, body=json.dumps(old).encode())
            assert stopped.wait(1)
            second = executor.submit(_request, relay, body=json.dumps(append(old, "y" * 800)).encode())
            release.set()
            assert first.result(timeout=3)[0] == second.result(timeout=3)[0] == 200
        assert len(upstream.calls) == 2
    finally:
        release.set()
        relay.close()


@pytest.mark.parametrize("case", ["different_input", "count_exceeds_limit", "token_budget", "request_bytes"])
def test_measurement_does_not_bypass_other_limits_or_count_identity(tmp_path, upstream, case):
    doc = document("x" * 220000)
    options = {"context_policy": policy25(), "max_request_bytes": 500000}
    if case == "token_budget": options["token_budget"] = 100
    if case == "request_bytes": options["max_request_bytes"] = 230000
    relay = _relay(tmp_path, upstream, **options)
    try:
        if case == "token_budget":
            small = document("small")
            upstream.body = response(100)
            assert _request(relay, body=json.dumps(small).encode())[0] == 200
            doc = append(small, "x" * 220000)
        upstream.body = json.dumps({"input_tokens": 500000 if case == "count_exceeds_limit" else 200}).encode()
        assert _request(relay, path="/v1/messages/count_tokens", body=json.dumps(doc).encode())[0] == 200
        if case == "different_input": doc["system"] = "different instructions"
        if case == "request_bytes": doc["messages"][0]["content"] += "x" * 10000
        calls = len(upstream.calls)
        if case == "request_bytes":
            connection = _connection(relay)
            try:
                # The byte guard rejects the declared size before reading a
                # body; sending the forbidden body races its deliberate close.
                connection.request("POST", "/v1/messages", body=None,
                    headers={"Content-Length": str(len(json.dumps(doc).encode()))})
                result = connection.getresponse()
                assert result.status == 413
                result.read()
            finally:
                connection.close()
        else:
            assert _request(relay, body=json.dumps(doc).encode())[0] == 400
        assert len(upstream.calls) == calls
        if case == "token_budget": assert relay.token_limit_reached
    finally:
        relay.close()


def test_wait_for_response_completion_is_bounded_by_attempt_deadline(tmp_path, upstream):
    stopped, release = threading.Event(), threading.Event()
    def script(handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.end_headers()
        handler.wfile.write(b': ' + b'padding' * 20 + b'\n\ndata: {"type":"message_stop"}\n\n')
        handler.wfile.flush()
        stopped.set()
        release.wait(2)
    upstream.script = script
    relay = _relay(tmp_path, upstream, context_policy=policy25(), deadline_at=time.monotonic() + .5)
    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(_request, relay)
            assert stopped.wait(.3)
            second = executor.submit(_request, relay)
            assert first.result(timeout=2)[0] == 200
            assert second.result(timeout=2)[0] == 504
        assert time.monotonic() - started < 1.5
        assert len(upstream.calls) == 1 and relay._context_anchor is None
    finally:
        release.set()
        relay.close()


def test_malformed_block_metadata_does_not_crash_or_normalize_unknown_material():
    old = document([{"type": "text", "text": "known", "cache_control": {"type": "ephemeral", "ttl": []}}])
    policy = policy25()
    anchor = usage_anchor(old, {"input_tokens": 1, "output_tokens": 2}, policy)
    capacity = request_capacity(append(old, [{"type": [], "text": "unknown"}]), policy, anchor=anchor)
    assert capacity["estimate_reason"] == "unsupported_added_content"
    assert counting_material(old)["messages"][0]["content"] == old["messages"][0]["content"]


def test_malformed_usage_is_not_a_complete_measurement_for_current_or_last_observation():
    policy = policy25()
    previous = {"request_path": "/v1/messages", "exchange": 1, "upstream_status": 200,
        "message_complete": True, "response_body_complete": True,
        "usage": {"input_tokens": 100, "output_tokens": 1}, "usage_valid": True}
    invalid = {**previous, "exchange": 2, "usage": {"input_tokens": 1}, "usage_valid": False}
    latest = latest_context([previous, invalid], policy)
    assert latest["input_tokens"] is None
    assert latest["last_observed_input_tokens"] == 100 and latest["last_observed_exchange"] == 1


def test_soft_notice_labels_unknown_usage_as_estimate_without_count_probe(tmp_path, upstream):
    relay = _relay(tmp_path, upstream, context_policy=policy25(working_context_tokens=2000))
    doc = document("x" * 1450)
    doc["max_tokens"] = 10
    try:
        assert _request(relay, body=json.dumps(doc).encode())[0] == 200
        notice = relay.context_notice()
        assert "unconfirmed byte-based input estimate" in notice
        assert "last complete provider input" not in notice
        assert len(upstream.calls) == 1
    finally:
        relay.close()


def test_close_releases_active_and_queued_measurement_requests(tmp_path, upstream):
    import http.client
    stopped, release = threading.Event(), threading.Event()
    def script(handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.end_headers()
        handler.wfile.write(b': ' + b'padding' * 20 + b'\n\n')
        handler.wfile.flush()
        stopped.set()
        release.wait(3)
    def request_or_disconnect():
        try:
            return _request(relay)[0]
        except (OSError, http.client.HTTPException):
            return "disconnected"
    upstream.script = script
    relay = _relay(tmp_path, upstream, context_policy=policy25(), deadline_at=time.monotonic() + 30)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(request_or_disconnect)
            assert stopped.wait(1)
            second = executor.submit(request_or_disconnect)
            started = time.monotonic()
            relay.close()
            first.result(timeout=2)
            second.result(timeout=2)
            assert time.monotonic() - started < 1.5
        assert len(upstream.calls) == 1 and relay._context_anchor is None
    finally:
        release.set()
        relay.close()


def test_multiblock_text_cache_marker_moves_preserve_measurement_without_rewriting_content():
    policy = policy25()
    old = document([
        {"type": "text", "text": "a" * 200000},
        {"type": "text", "text": "old query", "cache_control": {"type": "ephemeral"}},
        {"type": "thinking", "thinking": "private reasoning", "signature": "unchanged-signature"},
    ])
    original = copy.deepcopy(old)
    new = append(old, "z" * 12000)
    del new["messages"][0]["content"][1]["cache_control"]
    new["messages"][0]["content"][0]["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
    anchor = usage_anchor(old, {"input_tokens": 50000, "output_tokens": 5}, policy)
    result = request_capacity(new, policy, anchor=anchor)
    assert result["estimate_reason"] == "unchanged_prefix"
    assert result["fits"] and result["estimated_input_tokens"] < 63000
    assert old == original
    measured = counting_material(new)["messages"][0]["content"]
    assert isinstance(measured, list) and len(measured) == 3
    assert measured[2] == original["messages"][0]["content"][2]
    for mutation in ("text", "order", "signature", "unknown_cache"):
        changed = copy.deepcopy(new)
        blocks = changed["messages"][0]["content"]
        if mutation == "text": blocks[1]["text"] = "changed query"
        if mutation == "order": blocks[0], blocks[1] = blocks[1], blocks[0]
        if mutation == "signature": blocks[2]["signature"] = "changed-signature"
        if mutation == "unknown_cache": blocks[0]["cache_control"] = {"type": "ephemeral", "ttl": "unknown"}
        assert request_capacity(changed, policy, anchor=anchor)["estimate_reason"] == "history_or_configuration_changed"


def test_relay_multiblock_cache_marker_removal_does_not_erase_usable_history(tmp_path, upstream):
    relay = _relay(tmp_path, upstream, context_policy=policy25(), max_request_bytes=500000)
    old = document([{"type": "text", "text": "a" * 190000},
                    {"type": "text", "text": "instructions", "cache_control": {"type": "ephemeral"}}])
    new = append(old, "z" * 22000)
    del new["messages"][0]["content"][1]["cache_control"]
    wire = json.dumps(new).encode()
    try:
        upstream.body = response(50000)
        assert _request(relay, body=json.dumps(old).encode())[0] == 200
        assert _request(relay, body=wire)[0] == 200
        assert upstream.calls[-1][2] == wire and len(upstream.calls) == 2
    finally:
        relay.close()


def test_nine_exchanges_preserve_text_and_tool_result_cache_equivalence(tmp_path, upstream):
    root = tmp_path / "diagnostics"
    relay = _relay(tmp_path, upstream, context_policy=policy25(), max_request_bytes=500000,
                   max_requests=9, diagnostics=RuntimeDiagnostics(root))
    doc = document([{"type": "text", "text": "instructions"},
        {"type": "text", "text": "x" * 190000, "cache_control": {"type": "ephemeral"}}])
    try:
        for index in range(9):
            if index:
                # Match native CLI's moving cache boundary. Already-returned
                # tool results retain every byte and id, losing only the known
                # cache annotation once a later user result becomes current.
                for message in doc["messages"]:
                    if isinstance(message["content"], list):
                        for block in message["content"]:
                            if block.get("type") in {"text", "tool_result"}:
                                block.pop("cache_control", None)
                doc["messages"].extend([
                    {"role": "assistant", "content": [{"type": "tool_use", "id": f"read-{index}",
                        "name": "Read", "input": {"file_path": f"source-{index}.txt"}}]},
                    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"read-{index}",
                        "content": "result " * 1800, "is_error": False, "cache_control": {"type": "ephemeral"}}]},
                ])
            upstream.body = response(50000 + index * 2000)
            wire = json.dumps(doc).encode()
            assert _request(relay, body=wire)[0] == 200, f"cache annotation falsely rejected exchange {index + 1}"
            assert upstream.calls[-1][2] == wire
        assert len(upstream.calls) == 9
    finally:
        relay.close()
    exchanges = read_exchanges(root / "provider")
    assert len(exchanges) == 9
    assert all(row["context_capacity"]["estimate_source"] == "provider_usage_plus_delta_estimate"
               for row in exchanges[1:])
    assert exchanges[-1]["context_capacity"]["request_input_bytes"] > 205568


def test_tool_result_cache_equivalence_preserves_id_error_content_and_unknown_metadata():
    old = document([{"type": "tool_result", "tool_use_id": "read-1", "content": "observed result",
                     "is_error": False, "cache_control": {"type": "ephemeral"}}])
    policy = policy25()
    anchor = usage_anchor(old, {"input_tokens": 10, "output_tokens": 2}, policy)
    new = append(old, "next")
    del new["messages"][0]["content"][0]["cache_control"]
    assert request_capacity(new, policy, anchor=anchor)["estimate_reason"] == "unchanged_prefix"
    for key, value in (("tool_use_id", "other"), ("content", "other result"), ("is_error", True),
                       ("cache_control", {"type": "ephemeral", "unknown": True})):
        changed = copy.deepcopy(new)
        changed["messages"][0]["content"][0][key] = value
        assert request_capacity(changed, policy, anchor=anchor)["estimate_reason"] == "history_or_configuration_changed"


def test_aci28_projects_appended_text_and_moved_cache_markers_without_recount():
    policy = context_policy(replace(request24(), schema_version=28))
    old = document([{"type": "tool_result", "tool_use_id": "read", "content": "x" * 590000,
                     "cache_control": {"type": "ephemeral"}}])
    old["system"] = [{"type": "text", "text": "unchanged system"}]
    old["messages"].insert(0, {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "signed reasoning", "signature": "exact signature"},
        {"type": "tool_use", "id": "read", "name": "Read", "input": {"file_path": "/inputs/0000.txt"}}]})
    new = copy.deepcopy(old)
    new["system"] = "unchanged system"
    new["messages"][0]["content"][-1]["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
    del new["messages"][-1]["content"][0]["cache_control"]
    new["messages"][-1]["content"].append({"type": "text", "text": "summary guidance " * 400})
    before = copy.deepcopy((old, new))
    anchor = usage_anchor(old, {"input_tokens": 155000, "output_tokens": 10}, policy, exchange=64)
    capacity = request_capacity(new, policy, anchor=anchor)
    assert capacity["fits"] and 155000 < capacity["estimated_input_tokens"] < 162000
    assert capacity["estimate_reason"] == "message_text_appended"
    assert capacity["anchor_exchange"] == 64
    assert (old, new) == before
    legacy = policy25()
    assert not request_capacity(new, legacy, anchor=usage_anchor(old,
        {"input_tokens": 155000, "output_tokens": 10}, legacy))["fits"]
    # Repeated projections are always relative to the original measured request.
    assert request_capacity(new, policy, anchor=anchor) == capacity
    new["messages"][0]["content"][0]["signature"] = "changed signature"
    assert request_capacity(new, policy, anchor=anchor)["estimate_reason"] == "history_or_configuration_changed"


@pytest.mark.parametrize("mutation", ["history", "system", "tools", "model", "role", "media", "cache"])
def test_aci28_does_not_treat_arbitrary_mutation_as_append(mutation):
    policy = context_policy(replace(request24(), schema_version=28))
    old = document("x" * 300000)
    anchor = usage_anchor(old, {"input_tokens": 80000, "output_tokens": 10}, policy)
    new = copy.deepcopy(old)
    new["messages"][0]["content"] += " extra text"
    if mutation == "history": new["messages"][0]["content"] = "changed" + new["messages"][0]["content"][7:]
    elif mutation in {"system", "tools", "model"}: new[mutation] = "different"
    elif mutation == "role": new["messages"][0]["role"] = "assistant"
    elif mutation == "media": new["messages"][0]["content"] = [{"type": "text", "text": old["messages"][0]["content"]}, {"type": "image", "source": "opaque"}]
    elif mutation == "cache": new["messages"][0]["content"] = [{"type": "text", "text": new["messages"][0]["content"], "cache_control": {"type": "unknown"}}]
    result = request_capacity(new, policy, anchor=anchor)
    assert not result["fits"] and result["estimate_reason"] == "history_or_configuration_changed"
