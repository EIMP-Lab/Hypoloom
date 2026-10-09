"""Optional routing follows actual transport outcomes without changing budgets."""
import json
import time
from types import SimpleNamespace

import pytest

from hypoloom import literature_proxy
from hypoloom.literature_proxy import LiteratureProxyRouter, validate_response_peer
from hypoloom.network_io import NetworkOperationError

PROXY = "http://127.0.0.1:17891"
URI = "https://papers.example.org/private-path?api_key=secret"


def test_auto_uses_one_deadline_and_reuses_then_expires_successful_fallback(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(literature_proxy.time, "monotonic", lambda: clock[0])
    router = LiteratureProxyRouter(default_route="auto", proxy_url=PROXY)
    calls = []
    def call(proxy, deadline):
        calls.append((proxy, deadline))
        if proxy is None:
            clock[0] = deadline
            raise NetworkOperationError("timed_out", transport_phase="tcp_connect")
        clock[0] += 1
        return {"status_code": 200}
    result = router.request(URI, call, deadline=110)
    assert calls == [(None, 105), (PROXY, 110)] and clock[0] == 106
    assert [r["route"] for r in result["network_routes"]] == ["direct", "proxy"]
    assert all(s not in json.dumps(result) for s in ("secret", "private-path", PROXY))
    calls.clear()
    router.request(URI, call, deadline=116)
    assert calls == [(PROXY, 111)]
    clock[0] = 500
    calls.clear()
    router.request(URI, call, deadline=510)
    assert calls[0] == (None, 505)


@pytest.mark.parametrize("status", [200, 301, 400, 401, 403, 404, 429, 500, 502, 503])
def test_http_responses_do_not_rotate_routes(status):
    router = LiteratureProxyRouter(default_route="auto", proxy_url=PROXY)
    calls = []
    def call(proxy, deadline):
        calls.append(proxy)
        return {"status_code": status}
    assert router.request(URI, call, deadline=time.monotonic()+5)["status_code"] == status
    assert calls == [None]


@pytest.mark.parametrize("code,phase,status", [
    ("cancelled", "tcp_connect", None), ("unsafe_url", "dns", None),
    ("tls_error", "tls_handshake", None), ("challenge", "response_headers", 403),
    ("response_limit", "response_body", 200), ("cleanup_failed", "tcp_connect", None),
    ("timed_out", "dns", None), ("timed_out", "response_body", 429),
    ("worker_failed", None, None), ("transport_failed", "response_headers", None),
])
def test_non_network_or_unclassified_failures_do_not_rotate(code, phase, status):
    router = LiteratureProxyRouter(default_route="auto", proxy_url=PROXY)
    calls = []
    def call(proxy, deadline):
        calls.append(proxy)
        raise NetworkOperationError(code, transport_phase=phase, http_status=status)
    with pytest.raises(NetworkOperationError) as caught:
        router.request(URI, call, deadline=time.monotonic()+5)
    assert caught.value.code == code and calls == [None]


@pytest.mark.parametrize("policy,domain,expected", [
    ("direct", "unselected.example.org", None), ("direct", "required.example.org", PROXY),
    ("auto", "direct.example.org", None), ("proxy_only", "direct.example.org", None),
    ("proxy_only", "unselected.example.org", PROXY), ("auto", "required.example.org", PROXY),
])
def test_explicit_domain_overrides_win_and_never_fall_back(policy, domain, expected):
    router = LiteratureProxyRouter(default_route=policy, proxy_url=PROXY,
        proxy_domains=["required.example.org"], direct_domains=["direct.example.org"])
    calls = []
    def call(proxy, deadline):
        calls.append(proxy)
        raise NetworkOperationError("timed_out", transport_phase="tcp_connect")
    with pytest.raises(NetworkOperationError): router.request("https://"+domain, call, deadline=time.monotonic()+5)
    assert calls == [expected]


def test_auto_peer_checks_allow_only_approved_direct_or_configured_tunnel():
    router = LiteratureProxyRouter(default_route="auto", proxy_url=PROXY)
    for peer, tunnel in [("8.8.8.8", None), ("127.0.0.1", "8.8.8.8")]:
        validate_response_peer(SimpleNamespace(peer_address=peer, proxy_tunnel_address=tunnel),
            ("8.8.8.8",), proxy_expected=router.peer_policy(URI))
    for peer, tunnel in [("127.0.0.1", None), ("127.0.0.1", "1.1.1.1")]:
        with pytest.raises(ValueError):
            validate_response_peer(SimpleNamespace(peer_address=peer, proxy_tunnel_address=tunnel),
                ("8.8.8.8",), proxy_expected=router.peer_policy(URI))


@pytest.mark.parametrize("transport", ["metadata", "pdf", "web"])
def test_production_transports_use_auto_router_and_keep_safe_observations(monkeypatch, transport):
    from hypoloom import literature, grounding_web, network_io
    router = LiteratureProxyRouter(default_route="auto", proxy_url=PROXY)
    calls = []
    def worker(operation, payload, **bounds):
        calls.append((payload, bounds))
        if payload["proxy_url"] is None:
            raise NetworkOperationError("connection_failed", transport_phase="tcp_connect")
        return {"requested_uri": payload["uri"], "status_code": 200, "media_type": "application/pdf",
            "content_length": 2, "body_b64": "e30=", "retry_after": None, "peer_address": "127.0.0.1",
            "proxy_tunnel_address": "8.8.8.8", "location": None}
    monkeypatch.setattr(network_io, "run_network_operation", worker)
    monkeypatch.setattr(grounding_web, "run_network_operation", worker)
    if transport == "metadata":
        response = literature.StdlibOpenAlexTransport(proxy_router=router).get(host="api.openalex.org",
            target="/works?search=example", headers={"Authorization": "Bearer test-secret"}, timeout_seconds=3, max_bytes=100)
    elif transport == "pdf":
        fetcher = object.__new__(literature.OpenAlexLiteratureFetcher)
        fetcher.timeout_seconds, fetcher._cancel_event, fetcher._proxy_router = 3, None, router
        response = fetcher._fetch_pdf_https(URI, approved_addresses=("8.8.8.8",), max_bytes=100)
    else:
        response = grounding_web.PublicHttpsTransport(3, proxy_router=router).fetch(URI,
            approved_addresses=("8.8.8.8",), max_bytes=100)
    assert response.body == b"{}" and len(calls) == 2
    assert calls[0][1]["deadline"] < calls[1][1]["deadline"]
    assert [row["route"] for row in response.network_routes] == ["direct", "proxy"]
    assert "secret" not in json.dumps(response.network_routes)
