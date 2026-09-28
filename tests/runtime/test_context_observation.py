import json
from dataclasses import replace

import pytest

from hypoloom.agent_context import agent_retry_class
from hypoloom.context_management import context_policy, request_capacity
from hypoloom.context_observation import native_status, read_transcript_metrics, transcript_metrics
from hypoloom.provider_observation import provider_failure, read_exchanges
from hypoloom.runtime_diagnostics import RuntimeDiagnostics
from test_context_management import request24
from test_observer_summary import _attempt, _summary
from test_provider_relay import upstream, _relay, _request


def _transcript():
    def row(**value):
        return {"sessionId": "own", "timestamp": "2026-01-01T00:00:10Z", **value}
    rows = [row(type="system", subtype="compact_boundary", uuid="old", timestamp="2026-01-01T00:00:00Z", compactMetadata={"preTokens": 999}),
        row(type="system", subtype="compact_boundary", uuid="c", compactMetadata={"preTokens": 900, "postTokens": 300, "durationMs": 50})]
    rows.append(rows[-1])  # Replayed UUID must not inflate observed events.
    rows.append({"type": "cost-state", "sessionId": "own", "totalCostUSD": 999})
    for identity, offset, error, path in [("a", 1, False, "/inputs/0000.txt"), ("b", 1, False, "/inputs/0000.txt"),
            ("c", 2, False, "/inputs/0000.txt"), ("d", 1, True, "/inputs/0000.txt"), ("e", 1, False, "/workspace/code.py")]:
        rows.extend([row(message={"content": [{"type": "tool_use", "id": identity, "name": "Read", "input": {"file_path": path, "offset": offset, "limit": 10}}]}),
            row(message={"content": [{"type": "tool_result", "tool_use_id": identity, "is_error": error, "content": "secret source text"}]})])
    rows.append({**rows[-1], "sessionId": "foreign"})
    return b"\n".join(json.dumps(value).encode() for value in rows)


def test_native_observation_scopes_invocation_deduplicates_events_and_counts_exact_successful_reads(tmp_path):
    metrics = transcript_metrics(_transcript(), "own", 1767225605, ("stable-frozen-ref",))
    assert metrics["coverage"] == "complete"
    assert metrics["native_compaction_count"] == 1
    assert metrics["last_native_compaction"] == {"timestamp": 1767225610.0, "pre_tokens": 900, "post_tokens": 300, "duration_ms": 50}
    assert metrics["read_calls"] == 4 and metrics["failed_read_calls"] == 1
    assert metrics["immutable_read_calls"] == 3 and metrics["exact_reread_calls"] == 1
    assert "secret source text" not in json.dumps(metrics)
    assert transcript_metrics(_transcript() + b'\n{"partial', "own", 1767225605)["coverage"] == "partial"
    path = tmp_path / "session.jsonl"
    path.write_bytes(_transcript())
    assert read_transcript_metrics(path, "own", 1767225605, ("stable-frozen-ref",)) == metrics
    assert read_transcript_metrics(path, "own", 1767225605, limit=100)["coverage"] == "partial"
    assert read_transcript_metrics(path.parent / "missing", "own", 0)["coverage"] == "unavailable"


def test_compacting_requires_an_explicit_unfinished_status_event():
    started = b'{"type":"system","subtype":"status","status":"compacting"}\n'
    assert native_status(started) == "compacting"
    for end in [b'{"type":"system","subtype":"status","status":null}', b'{"type":"system","subtype":"compact_boundary"}', b'{"type":"result"}']:
        assert native_status(started + end) is None
    assert native_status(b'{"type":"assistant","message":{"usage":{"input_tokens":3}}}') is None


def test_context_costs_are_disjoint_and_unknown_billing_is_not_zero_spend(tmp_path):
    rows = [_attempt("first", command_id="node", start=10, end=15, reason="FAILED"),
        _attempt("second", command_id="node", start=20, end=25, reason="FAILED"),
        _attempt("third", command_id="node", start=30, end=35),
        _attempt("open", command_id="node", start=40, state="OPEN")]
    for index, row in enumerate(rows):
        value = json.loads(row["observation_json"])
        value["request"]["budget"] = {"max_cost": "10", "max_tokens": 100, "max_turns": 10}
        diagnostic = value["diagnostics"] = {"context_observation": transcript_metrics(_transcript(), "own", 1767225605, ("stable-frozen-ref",))}
        if index < 2:
            diagnostic["provider_failure"] = {"code": "context_capacity"}
        if index != 1:
            diagnostic["provider_accounting"] = {"token_usage": {"total_tokens": 10}, "turn_count": 1, "cost": str(index + 1), "cost_currency": "USD"}
        row["observation_json"] = json.dumps(value)
    metrics = _summary(tmp_path, attempts=list(reversed(rows)))["experts"][0]["stages"][0]["context_recovery"]
    assert metrics["initial_failure_reported_cost_usd"] == "1"
    assert metrics["recovery_reported_cost_usd"] == "3"  # OPEN stale cost excluded.
    assert metrics["recovery_unknown_cost_attempts"] == 2
    assert metrics["recovery_attempts"] == 3 and metrics["context_failure_attempts"] == 2
    assert metrics["recovery_wall_seconds"] == 10


@pytest.mark.parametrize("ordinal,forwarded,code,action", [(1, 0, "context_capacity", False), (2, 0, "context_capacity", False),
    (3, 0, "context_capacity", True), (4, 0, "context_capacity", True), (3, 1, "context_capacity", False),
    (3, 0, "input_too_long", False)])
def test_only_minimum_inline_first_local_refusal_ends_futile_recovery(ordinal, forwarded, code, action):
    policy = context_policy(replace(request24(working_context_tokens=1000), attempt_ordinal=ordinal))
    exchange = {"request_path": "/v1/messages", "failure_reason": code, "request_body_complete": True,
        "request_bytes_forwarded": forwarded, "context_capacity": request_capacity({"messages": [{"role": "user", "content": "x" * 2000}]}, policy)}
    failure = provider_failure([exchange])
    assert (failure.get("recovery_action") == "input_reconfiguration_required") is action
    assert agent_retry_class("provider_not_success", {"diagnostics": {"provider_failure": failure}}, retryable=True, bounded_context=True) == (None if action else "INFRASTRUCTURE")
    assert agent_retry_class("session_archive_failed", {"diagnostics": {"provider_failure": failure}}, retryable=False, bounded_context=True) is None


def test_real_relay_first_refusal_at_minimum_has_no_upstream_request(tmp_path, upstream):
    policy = context_policy(replace(request24(working_context_tokens=1000), attempt_ordinal=3))
    relay = _relay(tmp_path, upstream, context_policy=policy, diagnostics=RuntimeDiagnostics(tmp_path / "diagnostics"))
    try:
        body = json.dumps({"model": relay.model, "messages": [{"role": "user", "content": "x" * 2000}], "max_tokens": 10}).encode()
        assert _request(relay, body=body)[0] == 400
        assert _request(relay)[0] == 400
        assert upstream.calls == []
    finally:
        relay.close()
    exchanges = read_exchanges(relay.diagnostics.root / "provider")
    assert provider_failure(exchanges)["recovery_action"] == "input_reconfiguration_required"


def test_prior_progress_and_missing_capture_preserve_recovery():
    policy = context_policy(replace(request24(working_context_tokens=1000), attempt_ordinal=3))
    capacity = request_capacity({"messages": ["x" * 2000]}, policy)
    refused = {"request_path": "/v1/messages", "failure_reason": "context_capacity", "request_bytes_forwarded": 0,
        "request_body_complete": True, "context_capacity": capacity}
    progressed = {"request_path": "/v1/messages", "request_bytes_forwarded": 100, "request_body_complete": True}
    for exchanges in ([progressed, refused], [{**refused, "request_body_complete": False}], [{**refused, "context_capacity": {}}]):
        assert "recovery_action" not in provider_failure(exchanges)


@pytest.mark.parametrize("fresh_session", [True, False, None])
def test_current_initial_refusal_stops_only_proven_fresh_unchanged_retry(fresh_session):
    policy = context_policy(replace(request24(working_context_tokens=1000), schema_version=25))
    capacity = request_capacity({"messages": [{"role": "user", "content": "x" * 2000}]}, policy)
    refused = {"request_path": "/v1/messages", "failure_reason": "context_capacity",
        "request_bytes_forwarded": 0, "request_body_complete": True, "context_capacity": capacity}
    assert policy["max_inline_input_bytes"] > policy["minimum_inline_input_bytes"]
    failure = provider_failure([refused], fresh_session=fresh_session)
    assert (failure.get("recovery_action") == "input_reconfiguration_required") is (fresh_session is True)
    progressed = {"request_path": "/v1/messages", "upstream_status": 200,
        "request_bytes_forwarded": 100, "request_body_complete": True}
    for exchanges in ([progressed, refused], [{**refused, "request_body_complete": False}],
            [{**refused, "request_bytes_forwarded": None}], [{**refused, "context_capacity": {}}]):
        assert "recovery_action" not in provider_failure(exchanges, fresh_session=fresh_session)


def test_recovery_cost_order_uses_attempt_ordinals_despite_wall_clock_step(tmp_path):
    first = _attempt("z", command_id="node", start=20, end=25, reason="FAILED")
    second = _attempt("a", command_id="node", start=10, end=15)
    for ordinal, row in enumerate((first, second), 1):
        row["attempt_ordinal"] = ordinal
        value = json.loads(row["observation_json"])
        value["request"]["budget"] = {"max_cost": "10", "max_tokens": 100, "max_turns": 10}
        value["diagnostics"] = {"provider_accounting": {"token_usage": {"total_tokens": 10},
            "turn_count": 1, "cost": str(ordinal), "cost_currency": "USD"}}
        if ordinal == 1:
            value["diagnostics"]["provider_failure"] = {"code": "context_capacity"}
        row["observation_json"] = json.dumps(value)
    summary = _summary(tmp_path, attempts=[second, first])
    metric = summary["context_recovery"]
    assert summary["experts"][0]["stages"][0]["attempt_id"] == "a"
    assert metric["recovery_attempts"] == 1
    assert metric["initial_failure_reported_cost_usd"] == "1"
    assert metric["recovery_reported_cost_usd"] == "2"


def test_native_session_pricing_uncertainty_does_not_rewrite_charges():
    row = {"sessionId": "own", "type": "cost-state", "hasUnknownModelCost": True,
        "totalCostUSD": 999}
    metrics = transcript_metrics(json.dumps(row).encode(), "own", 1767225605)
    assert metrics["cli_unknown_model_pricing"] is True
    assert metrics["coverage"] == "complete"
    assert "999" not in json.dumps(metrics)
    row["sessionId"] = "foreign"
    assert transcript_metrics(json.dumps(row).encode(), "own", 0)["cli_unknown_model_pricing"] is None


def test_compaction_start_without_boundary_is_not_completed_or_provider_failure():
    from hypoloom.context_observation import exchange_context_metrics
    rows = [{"type": "system", "subtype": "status", "status": "compacting", "sessionId": "own",
             "uuid": "start", "timestamp": "2026-09-28T01:00:00Z"}]
    metrics = transcript_metrics(b"\n".join(json.dumps(row).encode() for row in rows * 2), "own", 0)
    assert metrics["native_compaction_started_count"] == 1
    assert metrics["native_compaction_count"] == 0 and metrics["native_compaction_completion_unobserved"]
    observed = exchange_context_metrics([{"request_path": "/v1/messages", "failure_reason": "context_capacity", "request_bytes_forwarded": 0}])
    assert observed["local_context_refusal_count"] == 1 and observed["provider_context_failure_count"] == 0
    assert observed["compaction_failure_attribution"].startswith("unknown")
    rows.append({"type": "system", "subtype": "compact_boundary", "sessionId": "own", "uuid": "done", "timestamp": "2026-09-28T01:00:05Z"})
    metrics = transcript_metrics(b"\n".join(json.dumps(row).encode() for row in rows), "own", 0)
    assert metrics["native_compaction_count"] == 1 and not metrics["native_compaction_completion_unobserved"]


def test_stream_events_observe_compaction_attempt_even_when_transcript_has_no_boundary(tmp_path):
    from hypoloom.context_observation import read_native_events
    path = tmp_path / "stdout.log"
    events = [{"type": "system", "subtype": "status", "status": "compacting"},
              {"type": "system", "subtype": "status", "status": None},
              {"type": "result", "is_error": True}]
    path.write_text("\n".join(json.dumps(row) for row in events))
    metrics = read_native_events(path, {"native_compaction_count": 0})
    assert metrics["native_compaction_started_count"] == 1 and metrics["native_compaction_count"] == 0
    assert metrics["native_compaction_completion_unobserved"]
    assert metrics["native_compaction_start_coverage"] == "stdout_complete"


def test_partial_stream_matches_pending_start_within_its_own_event_window(tmp_path):
    from hypoloom.context_observation import read_native_events
    path = tmp_path / "stdout.log"
    earlier = {"type": "system", "subtype": "compact_boundary"}
    pending = {"type": "system", "subtype": "status", "status": "compacting"}
    # The original start and other events are outside the bounded stdout tail.
    path.write_text(json.dumps({"text": "x" * (1024 * 1024)}) + "\n"
        + json.dumps(earlier) + "\n" + json.dumps(pending) + "\n")
    transcript = {"native_compaction_count": 3, "coverage": "complete"}
    metrics = read_native_events(path, transcript)
    assert metrics["native_compaction_started_count"] == 1
    assert metrics["native_compaction_count"] == 3
    assert metrics["native_compaction_start_coverage"] == "stdout_partial"
    assert metrics["native_compaction_completion_unobserved"] is True
    with path.open("a") as stream:
        stream.write(json.dumps(earlier) + "\n")
    metrics = read_native_events(path, transcript)
    assert metrics["native_compaction_completion_unobserved"] is None  # Earlier coverage is unknown.
