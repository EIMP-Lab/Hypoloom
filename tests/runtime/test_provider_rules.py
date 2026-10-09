"""Documented provider rules and unknown-gateway counterexamples."""
import json
import pytest

from hypoloom.provider_observation import ProviderObservation, provider_failure, provider_service, read_exchanges
from hypoloom.runtime_diagnostics import RuntimeDiagnostics
from tests.runtime.test_provider_relay import upstream, _relay, _request


@pytest.mark.parametrize("url,service", [
    ("https://api.kimi.com/coding/", "kimi_code"),
    ("https://api.kimi.ai/coding", "kimi_code"),
    ("https://api.moonshot.ai/v1", "generic"),
    ("https://api.anthropic.com", "anthropic"),
    ("https://api.xiaomimimo.com/anthropic", "mimo_api"),
    ("https://token-plan-cn.xiaomimimo.com/anthropic", "mimo_token_plan"),
    ("https://token-plan-sgp.xiaomimimo.com/anthropic", "mimo_token_plan"),
    ("https://token-plan-ams.xiaomimimo.com/anthropic", "mimo_token_plan"),
    ("https://gateway.example/kimi", "generic"),
    ("https://api.kimi.com.example/coding", "generic"),
    ("https://api.kimi.com/other", "generic"),
    ("https://api.xiaomimimo.com/other", "generic"),
    ("https://api.deepseek.com/anthropic", "deepseek"),
    ("https://open.bigmodel.cn/api/anthropic/", "glm_cn"),
    ("https://api.z.ai/api/anthropic", "glm_global"),
    ("https://api.deepseek.com.example/anthropic", "generic"),
    ("https://open.bigmodel.cn/api/paas/v4", "generic"),
    ("https://api.z.ai/other", "generic"),
    (None, "generic"),
])
def test_connection_chooses_rules_without_model_questions(url, service):
    assert provider_service(url) == service


@pytest.mark.parametrize("service,status,error,expected", [
    ("generic", 429, {"code": "insufficient_quota"}, "quota_exhausted"),
    ("generic", 401, {"code": "insufficient_quota"}, "auth_failed"),
    ("mimo_api", 402, {"type": "authentication_error"}, "auth_failed"),
    ("generic", 429, {"type": "rate_limit_error"}, "rate_limited"),
    ("generic", 200, {"type": "overloaded_error"}, "service_unavailable"),
    ("anthropic", 529, {}, "service_unavailable"),
    ("generic", 529, {}, None),
    ("mimo_api", 402, {}, "quota_exhausted"),
    ("mimo_token_plan", 402, {}, "quota_exhausted"),
    ("generic", 402, {}, None),
    ("deepseek", 402, {}, "quota_exhausted"),
    ("deepseek", 402, {"type": "authentication_error"}, "auth_failed"),
    ("mimo_token_plan", 429, {}, "rate_limited"),
    ("kimi_code", 403, {"type": "permission_error", "message": "You've reached your weekly (7-day) usage limit."}, "quota_exhausted"),
    ("kimi_code", 403, {"message": "You've reached your monthly usage limit for this billing cycle."}, "quota_exhausted"),
    ("generic", 403, {"message": "You've reached your monthly usage limit for this billing cycle."}, "auth_failed"),
    ("kimi_code", 403, {"message": "You've reached your concurrent request limit."}, "auth_failed"),
    ("kimi_code", 401, {"type": "authentication_error", "message": "You've reached your weekly (7-day) usage limit."}, "auth_failed"),
    ("kimi_code", 400, {"message": "Invalid request: Your request exceeded model token limit: 262144"}, "input_too_long"),
    ("generic", 400, {"code": "context_length_exceeded"}, "input_too_long"),
    ("generic", 400, {"type": {}, "code": [], "message": "opaque"}, None),
])
@pytest.mark.parametrize("stream", [False, True])
def test_error_shapes_normalize_without_exposing_body(service, status, error, expected, stream):
    parser = ProviderObservation(service=service)
    body = json.dumps({**({"type": "error"} if stream else {}), "error": error}).encode()
    wire = b"data: " + body + b"\n\n" if stream else body
    for offset in range(0, len(wire), 5): parser.feed(wire[offset:offset+5])
    observed = parser.finish()
    failure = provider_failure([{**observed, "provider_service": service, "upstream_status": status, "ended_at": 100}])
    assert (None if failure is None else failure["code"]) == expected
    assert "error" not in observed and "message" not in observed
    if expected == "quota_exhausted": assert "retry_at" not in failure


@pytest.mark.parametrize("service,status,error,expected", [
    ("mimo_api", 402, {}, "quota_exhausted"),
    ("anthropic", 529, {}, "service_unavailable"),
    ("kimi_code", 403, {"message": "You've reached your weekly (7-day) usage limit."}, "quota_exhausted"),
    ("deepseek", 402, {}, "quota_exhausted"),
    ("glm_cn", 429, {"code": "1113"}, "quota_exhausted"),
    ("glm_global", 429, {"code": "1309"}, "subscription_expired"),
])
def test_real_relay_retains_service_for_failure_reduction(tmp_path, upstream, monkeypatch, service, status, error, expected):
    # The fixture serves the provider protocol on localhost, without a paid call.
    monkeypatch.setattr("hypoloom.provider_observation.provider_service", lambda _: service)
    upstream.status, upstream.body = status, json.dumps({"error": error}).encode()
    relay = _relay(tmp_path, upstream, diagnostics=RuntimeDiagnostics(tmp_path / "diagnostics"))
    try: assert _request(relay)[0] == status
    finally: relay.close()
    exchanges = read_exchanges(tmp_path / "diagnostics" / "provider")
    assert exchanges[-1]["provider_service"] == service
    assert provider_failure(exchanges)["code"] == expected


@pytest.mark.parametrize("service", ["glm_cn", "glm_global"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("code,status,expected", [
    ("1000", 401, "auth_failed"), ("1001", 401, "auth_failed"),
    ("1003", 401, "auth_failed"), ("1005", 401, "auth_failed"),
    ("1113", 429, "quota_exhausted"), ("1200", 500, "service_unavailable"),
    ("1220", 403, "access_denied"), ("1230", 500, "service_unavailable"),
    ("1234", 500, "service_unavailable"), ("1261", 400, "input_too_long"),
    ("1302", 429, "rate_limited"), ("1305", 429, "service_unavailable"),
    ("1308", 429, "usage_window_exhausted"), ("1309", 429, "subscription_expired"),
    ("1310", 429, "quota_exhausted"), ("1311", 429, "access_denied"),
    ("1313", 429, "access_denied"), ("1314", 429, "subscription_expired"),
    ("1315", 429, "access_denied"), ("1316", 429, "usage_window_exhausted"),
    ("1317", 429, "quota_exhausted"), ("1318", 429, "usage_window_exhausted"),
    ("1319", 429, "quota_exhausted"), ("1320", 429, "usage_window_exhausted"),
    ("1321", 429, "quota_exhausted"), ("1210", 400, None), ("1301", 400, None),
])
def test_glm_business_codes_preserve_recovery_meaning(service, stream, code, status, expected):
    test_error_shapes_normalize_without_exposing_body(service, status,
        {"code": code, "message": "private provider detail"}, expected, stream)


@pytest.mark.parametrize("service,code,kind,expected", [
    ("generic", "1113", None, "rate_limited"),
    ("deepseek", "1309", None, "rate_limited"),
    ("glm_cn", "1113", "authentication_error", "auth_failed"),
    ("glm_global", "1113", "rate_limit_error", "quota_exhausted"),
    ("glm_cn", 1309, None, "subscription_expired"),
    ("glm_global", "new_code", None, "rate_limited"),
    ("glm_cn", ["1113"], None, "rate_limited"),
])
def test_business_codes_require_known_endpoint_and_do_not_weaken_auth(service, code, kind, expected):
    test_error_shapes_normalize_without_exposing_body(service, 429,
        {"code": code, "type": kind}, expected, False)


@pytest.mark.parametrize("service,prefix,suffix", [
    ("glm_cn", "已达到 5 小时使用上限。您的限额将在 ", " 重置。"),
    ("glm_global", "Usage limit reached. Your limit will reset at ", "."),
    ("glm_global", "Usage limit reached. Resets at ", "."),
])
@pytest.mark.parametrize("stream", [False, True])
def test_glm_reset_is_an_absolute_instant_and_retry_after_takes_precedence(service, prefix, suffix, stream):
    parser = ProviderObservation(service=service)
    body = json.dumps({"type": "error", "error": {"code": "1308",
        "message": prefix + "2026-10-10 18:30:00+08:00" + suffix}}).encode()
    wire = b"data: " + body + b"\n\n" if stream else body
    for offset in range(0, len(wire), 7): parser.feed(wire[offset:offset + 7])
    observed = parser.finish()
    from datetime import datetime
    reset = datetime.fromisoformat("2026-10-10T10:30:00+00:00").timestamp()
    exchange = {**observed, "provider_service": service, "upstream_status": 429, "ended_at": reset - 600}
    assert provider_failure([exchange])["retry_at"] == reset
    assert provider_failure([{**exchange, "response_headers": {"retry-after": "120"}}])["retry_at"] == reset - 480
    assert "message" not in observed and "18:30" not in json.dumps(observed)


@pytest.mark.parametrize("stamp", ["2026-10-10 18:30:00", "18:30", "5 hours", "{next_flush_time}",
    "2026-13-10T18:30:00+08:00", "2026-10-10T18:30:00-00:00", "2026-10-09T18:30:00Z",
    "2026-10-10T18:30:00+08:99"])
def test_ambiguous_invalid_or_stale_reset_does_not_invent_a_future_cooldown(stamp):
    parser = ProviderObservation(service="glm_global")
    parser.feed(json.dumps({"error": {"code": "1308", "message": "Resets at " + stamp + "."}}).encode())
    failure = provider_failure([{**parser.finish(), "provider_service": "glm_global",
        "upstream_status": 429, "ended_at": 1791626400}])
    assert failure["code"] == "usage_window_exhausted"
    assert failure["retry_after_seconds"] == 60


@pytest.mark.parametrize("code,expected", [("1310", "quota_exhausted"), ("1316", "usage_window_exhausted")])
def test_glm_reset_survives_real_relay_and_defers_another_request(tmp_path, upstream, monkeypatch, code, expected):
    from datetime import datetime, timezone
    import time
    from hypoloom.provider_traffic import ProviderTraffic
    monkeypatch.setattr("hypoloom.provider_observation.provider_service", lambda _: "glm_cn")
    reset = time.time() + 600
    at = datetime.fromtimestamp(reset, timezone.utc).isoformat()
    upstream.status, upstream.body = 429, json.dumps({"type": "error", "error": {
        "code": code, "message": f"您的限额将在 {at} 重置。"}}).encode()
    relay = _relay(tmp_path, upstream, diagnostics=RuntimeDiagnostics(tmp_path / "diagnostics"),
        traffic_binding=(ProviderTraffic(), ("run", "grounding"), None))
    try:
        assert _request(relay)[0] == 429
        assert _request(relay)[0] == 429
    finally: relay.close()
    exchanges = read_exchanges(tmp_path / "diagnostics" / "provider")
    assert len(upstream.calls) == 1
    remote, local = exchanges
    failure = provider_failure([remote])
    assert failure["code"] == expected
    assert failure["retry_at"] == pytest.approx(reset, abs=.001)
    assert local["failure_reason"] == expected and local["request_bytes_forwarded"] == 0
    assert provider_failure([local])["retry_at"] >= reset


@pytest.mark.parametrize("code,expected", [("1309", "subscription_expired"), ("1313", "access_denied")])
def test_operator_restriction_ignores_reset_hints_and_has_localized_status(code, expected):
    from hypoloom.provider_observation import provider_status_text, provider_retry_gate
    parser = ProviderObservation(service="glm_cn")
    parser.feed(json.dumps({"error": {"code": code, "message": "Resets at 2026-10-10T18:30:00Z."}}).encode())
    exchange = {**parser.finish(), "provider_service": "glm_cn", "upstream_status": 429,
        "ended_at": 100, "response_headers": {"retry-after": "120"}}
    failure = provider_failure([exchange])
    assert failure == {"code": expected, "http_status": 429}
    status = provider_retry_gate([{**failure, "attempt_id": "rejected", "started_at": 1,
        "ended_at": 100, "retry_at": 220}], cooldown_seconds=18000, cleared_at=None, now=999999)
    assert status["state"] == "waiting_operator" and status["next_retry_at"] is None
    assert ("套餐已到期" if code == "1309" else "访问受限") in provider_status_text(status, language="zh-CN")
    assert provider_failure([{**exchange, "upstream_status": 401}]) == {"code": "auth_failed", "http_status": 401}


def test_schedule_defers_existing_relays_without_counting_requests_or_cancelling_inflight():
    from hypoloom.provider_traffic import ProviderTraffic, ProviderDeferred
    from tests.runtime.test_provider_traffic import acquire, success
    traffic = ProviderTraffic()
    inflight = acquire(traffic)
    traffic.schedule(0)  # Only Host consumption, not a relay clock, ends the pause.
    with pytest.raises(ProviderDeferred) as exc: acquire(traffic)
    assert exc.value.code == "local_request_queue"
    assert traffic.snapshot()["in_flight"] == traffic.snapshot()["rpm"] == 1
    traffic.finish(inflight, success())
    with pytest.raises(ProviderDeferred): acquire(traffic)
    traffic.schedule(None)
    traffic.finish(acquire(traffic), success())
    assert traffic.snapshot()["rpm"] == 2
    assert traffic.snapshot()["queued"] == traffic.snapshot()["in_flight"] == 0
