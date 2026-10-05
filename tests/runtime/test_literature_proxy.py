from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace

import pytest

from hypoloom.literature_proxy import LiteratureProxyRouter, LiteratureProxyError, validate_response_peer
from tests.runtime.test_network_proxy import PUBLIC, _Transport, _proxy, _upstream, tls_pair
from hypoloom.network_proxy import NetworkBridge
from hypoloom.network_io import NetworkOperationError, run_network_operation
from tests.runtime.test_document_fallback import system as document_system
from tests.runtime.test_elsevier import api as elsevier_api


def test_proxy_is_explicit_exact_domain_and_never_ambient(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://ambient-private.invalid:7891")
    assert LiteratureProxyRouter().for_url("https://example.org/paper") is None
    router = LiteratureProxyRouter(proxy_domains=["Example.ORG"], proxy_url="http://127.0.0.1:7891")
    assert router.for_url("https://example.org/paper") == "http://127.0.0.1:7891"
    assert router.for_url("https://example.org.evil.test/paper") is None
    assert router.for_url("https://sub.example.org/paper") is None
    assert router.for_url("https://other.test/redirect") is None
    assert router.configuration == {"proxy_domains": ["example.org"], "proxy_policy": "proxy_only"}
    assert "127.0.0.1" not in repr(router) + json.dumps(router.configuration)


@pytest.mark.parametrize("url", ["https://127.0.0.1:7891", "socks5://127.0.0.1:7891",
    "http://name:private-password@127.0.0.1:7891", "http://127.0.0.1:7891/path", "http://127.0.0.1:7891/?secret",
    "http://127.0.0.1:7891\nInjected:header"])
def test_proxy_url_reuses_explicit_credential_free_origin_validation(url):
    with pytest.raises(LiteratureProxyError) as error:
        LiteratureProxyRouter(proxy_domains=["example.org"], proxy_url=url)
    assert url not in str(error.value) and "private-password" not in str(error.value)


@pytest.mark.parametrize("domains", ["example.org", ["*.example.org"], ["example.org/path"],
    ["https://example.org"], ["example.org."], ["user@example.org"], ["127.0.0.1"], [""],
    ["localhost"], ["service.internal"], ["example.org", "EXAMPLE.ORG"]])
def test_proxy_requires_unambiguous_exact_dns_names(domains):
    with pytest.raises(LiteratureProxyError):
        LiteratureProxyRouter(proxy_domains=domains, proxy_url="http://127.0.0.1:7891")


def test_proxy_loader_failure_never_falls_back_direct_or_leaks_private_text():
    value = ["http://127.0.0.1:7891"]
    calls = []
    def loader():
        calls.append(True)
        if value[0] is None: raise ValueError("private source description")
        return value[0]
    router = LiteratureProxyRouter(proxy_domains=["example.org"], proxy_url_loader=loader)
    assert router.for_url("https://other.org/paper") is None and calls == []
    assert router.for_url("https://example.org/paper") == value[0]
    value[0] = "http://127.0.0.1:7892"
    assert router.for_url("https://example.org/paper") == value[0]
    value[0] = None
    with pytest.raises(LiteratureProxyError) as error:
        router.for_url("https://example.org/paper")
    assert "private source" not in str(error.value)


@pytest.mark.parametrize("options", [
    {"proxy_domains": ["example.org"]},
    {"proxy_url": "http://127.0.0.1:7891"},
    {"proxy_domains": ["example.org"], "proxy_url": "http://127.0.0.1:7891", "proxy_url_loader": lambda: "http://127.0.0.1:7892"},
])
def test_proxy_partial_or_ambiguous_configuration_is_rejected(options):
    with pytest.raises(LiteratureProxyError):
        LiteratureProxyRouter(**options)


def test_private_args_source_validates_before_network_and_rereads(tmp_path, monkeypatch):
    from hypoloom.literature_proxy import proxy_router_from_args
    monkeypatch.setenv("HYPOLOOM_LITERATURE_PROXY", "http://ambient.invalid:7891")
    assert proxy_router_from_args(SimpleNamespace()) is None
    source = tmp_path / "credentials.env"
    source.write_text("HYPOLOOM_LITERATURE_PROXY=http://127.0.0.1:7891\n")
    source.chmod(0o600)
    args = SimpleNamespace(credentials_file=str(source), literature_proxy_domain=["example.org"])
    router = proxy_router_from_args(args)
    assert router.for_url("https://example.org/") == "http://127.0.0.1:7891"
    source.write_text("HYPOLOOM_LITERATURE_PROXY=http://127.0.0.1:7892\n")
    assert router.for_url("https://example.org/") == "http://127.0.0.1:7892"
    source.write_text("HYPOLOOM_LITERATURE_PROXY=https://private-invalid-proxy\n")
    with pytest.raises(LiteratureProxyError): proxy_router_from_args(args)
    source.write_text("OTHER=value\n")
    with pytest.raises(LiteratureProxyError): router.for_url("https://example.org/")
    with pytest.raises(LiteratureProxyError): proxy_router_from_args(args)
    source.write_text("HYPOLOOM_LITERATURE_PROXY=http://127.0.0.1:7891\n")
    with pytest.raises(LiteratureProxyError): proxy_router_from_args(SimpleNamespace(credentials_file=str(source)))


@pytest.mark.parametrize("provider,host,target", [
    ("openalex", "api.openalex.org", "/works?search=science"),
    ("semantic", "api.semanticscholar.org", "/graph/v1/paper/DOI%3A10.1234%2Ftest?fields=externalIds%2CopenAccessPdf"),
    ("unpaywall", "api.unpaywall.org", "/v2/10.1234/test?email=fixture%40example.org"),
])
def test_metadata_transports_share_domain_route_and_exact_origin_auth(monkeypatch, provider, host, target):
    from hypoloom import literature as literature
    from hypoloom import network_io
    import base64
    router = LiteratureProxyRouter(proxy_domains=[host], proxy_url="http://127.0.0.1:7891")
    calls = []
    def operation(name, payload, **bounds):
        calls.append((name, payload, bounds))
        return {"status_code": 200, "media_type": "application/json", "content_length": 2,
            "body_b64": base64.b64encode(b"{}").decode(), "retry_after": "7"}
    monkeypatch.setattr(network_io, "run_network_operation", operation)
    cls = {"openalex": literature.StdlibOpenAlexTransport, "semantic": literature.StdlibSemanticScholarTransport,
           "unpaywall": literature.StdlibUnpaywallTransport}[provider]
    transport = cls(proxy_router=router)
    headers = {"Authorization": "Bearer origin-sentinel"} if provider == "openalex" else {"x-api-key": "origin-sentinel"}
    assert transport.get(host=host, target=target, headers=headers, timeout_seconds=1, max_bytes=1024).body == b"{}"
    assert len(calls) == 1 and calls[0][1]["proxy_url"] == "http://127.0.0.1:7891"
    assert calls[0][1]["headers"] == headers
    assert calls[0][1]["uri"] == "https://" + host + target
    with pytest.raises(literature.LiteratureError):
        transport.get(host="unapproved.example.org", target=target, headers=headers, timeout_seconds=1, max_bytes=1024)
    assert len(calls) == 1


def test_public_target_binding_never_claims_proxy_peer_is_publisher_peer():
    proxied = SimpleNamespace(peer_address="127.0.0.1", proxy_tunnel_address="93.184.216.34")
    validate_response_peer(proxied, ("93.184.216.34",), proxy_expected=True)
    assert proxied.peer_address == "127.0.0.1"
    with pytest.raises(ValueError):
        validate_response_peer(proxied, ("93.184.216.34",), proxy_expected=False)
    with pytest.raises(ValueError):
        validate_response_peer(proxied, ("8.8.8.8",), proxy_expected=True)
    with pytest.raises(ValueError):
        validate_response_peer(SimpleNamespace(peer_address="127.0.0.1", proxy_tunnel_address="10.0.0.1"),
            ("10.0.0.1",), proxy_expected=True)
    validate_response_peer(SimpleNamespace(peer_address="8.8.8.8", proxy_tunnel_address=None), ("8.8.8.8",))


def _worker(proxy, *, certificate=None, uri="https://example.org/paper", seconds=3, addresses=(PUBLIC,)):
    deadline = time.monotonic() + seconds
    return run_network_operation("tests.runtime.literature_proxy_worker:fetch",
        {"uri": uri, "headers": {"Authorization": "Bearer private-origin-key", "X-Api-Key": "private-origin-key"},
         "approved_addresses": list(addresses), "proxy_url": proxy, "max_bytes": 4096,
         "deadline": deadline, "fixture_ca": str(certificate) if certificate else None},
        deadline=deadline, max_output_bytes=16384)


def test_real_connect_uses_public_ip_but_tls_sni_and_credentials_use_origin(tmp_path, monkeypatch, tls_pair):
    context, certificate = tls_pair
    with _upstream(tls=context) as (port, origin_requests):
        transport = _Transport(port)
        proxy = _proxy(tmp_path, transport)
        connect_requests = []
        original = proxy.server.RequestHandlerClass.do_CONNECT
        def observe(handler):
            connect_requests.append((handler.path, dict(handler.headers)))
            return original(handler)
        monkeypatch.setattr(proxy.server.RequestHandlerClass, "do_CONNECT", observe)
        try:
            with NetworkBridge(str(proxy.socket_path), timeout_seconds=3) as bridge:
                result = _worker(bridge.proxy_url, certificate=certificate)
            assert result["status_code"] == 200
            assert result["peer_address"] == "127.0.0.1"
            assert result["proxy_tunnel_address"] == PUBLIC
            assert connect_requests == [(PUBLIC + ":443", {"Host": PUBLIC + ":443"})]
            assert transport.connections == [(PUBLIC, 443, (PUBLIC,))]
            assert origin_requests[0][1]["Host"] == "example.org"
            assert origin_requests[0][1]["Authorization"] == "Bearer private-origin-key"
            assert origin_requests[0][1]["X-Api-Key"] == "private-origin-key"
            assert "private-origin-key" not in json.dumps(connect_requests)
        finally:
            proxy.close()


@pytest.mark.parametrize("failure", ["untrusted_ca", "wrong_hostname"])
def test_proxy_cannot_disable_origin_tls_verification(tmp_path, tls_pair, failure):
    context, certificate = tls_pair
    with _upstream(tls=context) as (port, origin_requests):
        transport = _Transport(port)
        proxy = _proxy(tmp_path, transport)
        try:
            with NetworkBridge(str(proxy.socket_path), timeout_seconds=3) as bridge:
                with pytest.raises(NetworkOperationError):
                    _worker(bridge.proxy_url, certificate=certificate if failure == "wrong_hostname" else None,
                        uri="https://wrong.example.org/paper" if failure == "wrong_hostname" else "https://example.org/paper")
            assert origin_requests == []
            assert len(transport.connections) == 1
        finally:
            proxy.close()


def test_proxy_cannot_turn_private_target_into_a_public_request(tmp_path, tls_pair):
    context, certificate = tls_pair
    with _upstream(tls=context) as (port, origin_requests):
        transport = _Transport(port)
        proxy = _proxy(tmp_path, transport)
        try:
            with NetworkBridge(str(proxy.socket_path), timeout_seconds=3) as bridge:
                with pytest.raises(NetworkOperationError):
                    _worker(bridge.proxy_url, certificate=certificate, addresses=("127.0.0.1",))
            assert transport.connections == [] and origin_requests == []
        finally:
            proxy.close()


def test_stalled_proxy_is_bounded_and_worker_reaped(tmp_path, monkeypatch):
    import socketserver
    import threading
    from hypoloom import network_io
    entered = threading.Event()
    release = threading.Event()
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(4096)
            entered.set()
            release.wait(3)
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    children = []
    original = network_io.subprocess.Popen
    def observe(*args, **kwargs):
        child = original(*args, **kwargs); children.append(child); return child
    monkeypatch.setattr(network_io.subprocess, "Popen", observe)
    started = time.monotonic()
    try:
        with pytest.raises(NetworkOperationError) as error:
            _worker(f"http://127.0.0.1:{server.server_address[1]}", seconds=0.65)
        assert error.value.code == "timed_out" and entered.is_set()
        assert time.monotonic() - started < 1.6
        assert len(children) == 1 and children[0].poll() is not None
        with pytest.raises(ProcessLookupError): os.kill(children[0].pid, 0)
    finally:
        release.set(); server.shutdown(); server.server_close(); thread.join(timeout=2)


@pytest.mark.parametrize("status,challenge", [(403, True), (403, False), (429, False), (503, True)])
def test_upstream_denial_does_not_trigger_another_route(tmp_path, monkeypatch, tls_pair, status, challenge):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    context, certificate = tls_pair
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            seen.append(self.path)
            self.send_response(status)
            if challenge: self.send_header("Cf-Mitigated", "challenge")
            self.send_header("Content-Length", "10000000" if challenge else "0")
            self.end_headers()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    transport = _Transport(server.server_port)
    proxy = _proxy(tmp_path, transport)
    try:
        with NetworkBridge(str(proxy.socket_path), timeout_seconds=3) as bridge:
            if challenge:
                with pytest.raises(NetworkOperationError) as error:
                    _worker(bridge.proxy_url, certificate=certificate)
                assert error.value.code == "challenge" and error.value.http_status == status
            else:
                assert _worker(bridge.proxy_url, certificate=certificate)["status_code"] == status
        assert seen == ["/paper"] and len(transport.connections) == 1
    finally:
        proxy.close(); server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_public_transport_reselects_route_after_cross_domain_redirect(monkeypatch):
    import base64
    from hypoloom import grounding_web as web
    routed = []
    router = LiteratureProxyRouter(proxy_domains=["example.org"], proxy_url="http://127.0.0.1:7891")
    def operation(name, payload, **bounds):
        if name.endswith(":_resolve_in_process"): return {"addresses": [PUBLIC]}
        routed.append((payload["uri"], payload["proxy_url"], dict(payload["headers"])))
        first = payload["uri"] == "https://example.org/paper"
        return {"requested_uri": payload["uri"], "status_code": 302 if first else 200,
            "peer_address": "127.0.0.1" if first else PUBLIC, "proxy_tunnel_address": PUBLIC if first else None,
            "content_length": 4, "media_type": "text/plain", "location": "https://other.org/copy" if first else None,
            "body_b64": base64.b64encode(b"safe").decode(), "retry_after": None}
    class PrivateTransport(web.PublicHttpsTransport):
        def request_headers(self, uri):
            return {"Authorization": "Bearer private-origin-key"} if uri == "https://example.org/paper" else {}
    monkeypatch.setattr(web, "run_network_operation", operation)
    reader = web.GroundingWeb(PrivateTransport(proxy_router=router))
    assert reader.web_fetch("https://example.org/paper")["text"] == "safe"
    assert routed == [("https://example.org/paper", "http://127.0.0.1:7891", {"Authorization": "Bearer private-origin-key"}),
                      ("https://other.org/copy", None, {})]


def test_host_proxy_failure_can_use_other_oa_source_without_repeating_publisher(document_system, tmp_path):
    from hypoloom.grounding_web import GroundingWebError
    from hypoloom.paper_sources import web_tool
    from tests.runtime.test_document_fallback import URL, LOOKUPS, OA, document
    host, request, metadata, public, backend, papers = document_system
    original = public.document
    def read(url):
        if url == URL:
            public.calls.append(url)
            raise GroundingWebError("proxy_unavailable", "configured proxy could not connect")
        return original(url)
    public.document = read
    result = web_tool(host, request, "web_fetch", {"mode": "document", "url": URL})
    assert result["status"] == "available" and result["url"] == OA
    assert public.calls == [URL, *LOOKUPS, OA] and not backend.calls
    assert len(metadata.calls) == 1 and len(papers.sources) == 1
    archive = tmp_path / "proxy-fallback-archive"
    archive.mkdir()
    papers.archive(archive)
    assert (archive / result["original_path"].lstrip("/")).read_bytes() == document()["content"]


@pytest.mark.parametrize("status", [403, 503])
def test_elsevier_explicit_challenge_never_retries_another_representation(elsevier_api, status):
    from hypoloom.grounding_web import GroundingWebError
    from tests.runtime.test_elsevier import read
    client, _, _, _ = elsevier_api
    requested = []
    original = client.transport_factory
    class Challenge(original):
        def fetch(self, uri, **kwargs):
            requested.append((uri, self.accept))
            raise GroundingWebError("challenge", "requires verification", http_status=status)
    client.transport_factory = Challenge
    with pytest.raises(GroundingWebError) as error:
        read(client)
    assert error.value.code == "challenge" and error.value.http_status == status
    assert len(requested) == 1 and requested[0][1] == "application/pdf"
