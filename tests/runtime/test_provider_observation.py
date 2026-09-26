import json

from hypoloom.provider_observation import INPUT_TOO_LONG_PHRASES, ProviderObservation


def test_input_too_long_phrase_constant_is_the_single_shared_source():
    assert "prompt is too long" in INPUT_TOO_LONG_PHRASES
    assert len(INPUT_TOO_LONG_PHRASES) == len(set(INPUT_TOO_LONG_PHRASES))
    for phrase in INPUT_TOO_LONG_PHRASES:
        parser = ProviderObservation()
        parser.feed(json.dumps({"type": "error", "error": {"type": "invalid_request_error",
            "message": phrase.upper()}}).encode())
        assert parser.finish()["provider_error_code"] == "input_too_long"


def test_split_sse_records_served_label_usage_and_actual_completion():
    parser = ProviderObservation()
    events = [
        {"type": "message_start", "message": {"model": "served-model", "usage": {"input_tokens": 12, "cache_read_input_tokens": 40}}},
        {"type": "content_block_delta", "delta": {"text": "private output"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 7}},
        {"type": "message_stop"},
    ]
    wire = b"".join(b"data: " + json.dumps(event).encode() + b"\r\n\r\n" for event in events)
    for offset in range(0, len(wire), 3):
        parser.feed(wire[offset:offset + 3])
    result = parser.finish()
    assert result["served_models"] == ["served-model"]
    assert result["usage"] == {"input_tokens": 12, "cache_read_input_tokens": 40, "output_tokens": 7}
    assert result["message_complete"] is True and result["first_content_at"] is not None
    assert "private output" not in json.dumps(result)


def test_incomplete_sse_never_invents_usage_or_a_complete_message():
    parser = ProviderObservation()
    parser.feed(b'data: {"type":"message_start","message":{"model":"model-a"}}\n\n')
    parser.feed(b'data: {"type":"message_delta"')
    result = parser.finish()
    assert result["served_models"] == ["model-a"]
    assert result["usage"] == {} and result["message_complete"] is False


def test_nonstreaming_message_and_malformed_usage():
    parser = ProviderObservation()
    parser.feed(b" \n")
    parser.feed(json.dumps({"type": "message", "model": "model-json", "stop_reason": "end_turn",
        "usage": {"input_tokens": True, "cache_read_input_tokens": -1, "output_tokens": 0}}).encode())
    result = parser.finish()
    assert result["message_complete"] is True
    assert result["usage"] == {"output_tokens": 0}


def test_old_http_failure_without_body_keeps_exchange_metadata(tmp_path):
    from hypoloom.provider_observation import read_exchanges
    directory = tmp_path / "000001"
    directory.mkdir()
    metadata = {"upstream_status": 503, "response_body_complete": False}
    (directory / "exchange.json").write_text(json.dumps(metadata))
    assert read_exchanges(tmp_path) == [metadata]


def test_live_context_reads_latest_request_and_last_complete_usage_without_older_bodies(tmp_path, monkeypatch):
    from pathlib import Path
    from hypoloom.provider_observation import read_exchanges
    completed = {"request_path": "/v1/messages", "upstream_status": 200,
        "response_body_complete": True, "message_complete": True,
        "served_models": ["model"], "usage": {"input_tokens": 59326, "output_tokens": 100}}
    rows = [completed, completed, {**completed, "usage_valid": False}, {**completed, "message_complete": False},
        {"request_path": "/v1/messages/count_tokens", "upstream_status": 404},
        {"request_path": "/v1/messages", "failure_reason": "context_capacity"}]
    for index, row in enumerate(rows, 1):
        directory = tmp_path / f"{index:06d}"
        directory.mkdir()
        (directory / "exchange.json").write_text(json.dumps({"exchange": index, **row}))
    read = Path.read_bytes
    def refuse_older(path):
        assert path.parent.name != "000001", "live observation must stop at the last complete usage"
        return read(path)
    monkeypatch.setattr(Path, "read_bytes", refuse_older)
    recent = read_exchanges(tmp_path, latest_context_only=True)
    assert [item["exchange"] for item in recent] == [2, 3, 4, 6]
    assert recent[0]["usage"]["input_tokens"] == 59326
    assert recent[-1]["failure_reason"] == "context_capacity"
    assert [item["exchange"] for item in read_exchanges(tmp_path, latest_generation_only=True)] == [6]


def test_usage_lower_bound_counts_complete_generations_only():
    from hypoloom.provider_observation import summarize_exchanges
    complete = {"request_path": "/v1/messages", "upstream_status": 200,
        "response_body_complete": True, "message_complete": True,
        "usage": {"input_tokens": 10, "output_tokens": 3, "cache_read_input_tokens": 40}}
    result = summarize_exchanges([complete, {**complete, "message_complete": False},
        {**complete, "request_path": "/v1/messages/count_tokens"},
        {**complete, "usage": {"input_tokens": True, "output_tokens": 2}},
        {"request_path": "/v1/messages", "upstream_status": 429}])
    assert result == {"input_tokens": 10, "output_tokens": 3,
        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 40,
        "total_tokens": 53, "completed_requests": 1, "unmeasured_requests": 3}


def test_cache_inclusive_wire_observations_do_not_double_count_or_invent_unknown_usage():
    from hypoloom.provider_observation import summarize_exchanges, observed_input_floor
    from hypoloom.context_management import latest_context
    exchange = {"request_path": "/v1/messages", "upstream_status": 200,
        "response_body_complete": True, "message_complete": True, "served_models": ["model"],
        "usage": {"input_tokens": 100, "output_tokens": 5, "cache_read_input_tokens": 70},
        "context_capacity": {"input_usage_semantics": "includes_cache"}}
    assert summarize_exchanges([exchange])["total_tokens"] == 105
    assert observed_input_floor([exchange]) == 100
    assert latest_context([exchange])["input_tokens"] == 100
    unknown = {**exchange, "context_capacity": {"input_usage_semantics": "unknown"}}
    assert summarize_exchanges([unknown])["unmeasured_requests"] == 1
    assert observed_input_floor([unknown]) is None
    assert latest_context([unknown])["input_tokens"] is None


def test_provider_failures_project_only_safe_classification_and_retry_time():
    from hypoloom.provider_observation import provider_failure
    parser = ProviderObservation()
    parser.feed(json.dumps({"type": "error", "error": {"type": "invalid_request_error",
        "message": "prompt is too long: 842000 tokens; private secret"}}).encode())
    observed = parser.finish()
    assert observed["provider_error_code"] == "input_too_long"
    assert "private" not in json.dumps(observed)
    assert provider_failure([{**observed, "upstream_status": 400, "ended_at": 100}])["code"] == "input_too_long"
    assert provider_failure([{"upstream_status": 429, "ended_at": 100,
        "response_headers": {"retry-after": "120"}}])["retry_at"] == 220
    assert provider_failure([{"upstream_status": 429, "ended_at": 100,
        "response_headers": {"retry-after": "Thu, 01 Jan 1970 00:05:00 GMT"}}])["retry_at"] == 300
    assert provider_failure([{"upstream_status": 429, "ended_at": 100},
        {"upstream_status": 200, "ended_at": 200}]) is None


def test_auth_rejection_is_classified_without_message_sniffing():
    from hypoloom.provider_observation import provider_failure
    for status in (401, 403):
        failure = provider_failure([{"upstream_status": status, "ended_at": 100}])
        assert failure == {"code": "auth_failed", "http_status": status}
    # The Host owns the cooldown length; a relay header never sets it here.
    assert "retry_at" not in provider_failure([{"upstream_status": 403, "ended_at": 100,
        "response_headers": {"retry-after": "30"}}])
    # A later success clears a transient rejection, as with other classes.
    assert provider_failure([{"upstream_status": 403, "ended_at": 100},
        {"upstream_status": 200, "ended_at": 200}]) is None
    assert provider_failure([{"upstream_status": 500, "ended_at": 100}]) is None


def test_provider_retry_gate_episodes_clear_and_disabled():
    from hypoloom.provider_observation import provider_retry_gate
    rows = [{"attempt_id": "a1", "ended_at": 100.0, "code": "auth_failed", "http_status": 403},
        {"attempt_id": "a2", "ended_at": 200.0, "code": "auth_failed", "http_status": 403}]
    gate = provider_retry_gate(rows, cooldown_seconds=18000.0, cleared_at=None, now=300.0)
    assert gate["pending"] and gate["gates"][-1]["until"] == 200.0 + 18000.0
    assert not provider_retry_gate(rows, cooldown_seconds=18000.0, cleared_at=None, now=200.0 + 18001.0)["pending"]
    # An operator clear supersedes only failures recorded before it.
    assert not provider_retry_gate(rows, cooldown_seconds=18000.0, cleared_at=250.0, now=300.0)["pending"]
    assert provider_retry_gate(rows, cooldown_seconds=18000.0, cleared_at=150.0, now=300.0)["pending"]
    # Zero removes the timed auth delay; repeated recovery is still bounded.
    assert not provider_retry_gate(rows, cooldown_seconds=0.0, cleared_at=None, now=300.0)["pending"]
    rate = [{"attempt_id": "r1", "ended_at": 100.0, "code": "rate_limited", "retry_at": 400.0, "http_status": 429}]
    assert provider_retry_gate(rate, cooldown_seconds=0.0, cleared_at=None, now=300.0)["pending"]
    assert not provider_retry_gate(rate, cooldown_seconds=0.0, cleared_at=200.0, now=300.0)["pending"]
    # Malformed rows never fabricate a gate.
    assert not provider_retry_gate([{"attempt_id": "x", "ended_at": "soon", "code": "auth_failed"}],
        cooldown_seconds=18000.0, cleared_at=None, now=300.0)["pending"]
