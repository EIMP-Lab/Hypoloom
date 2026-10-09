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
