from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from threading import Event
import time
from types import SimpleNamespace

import pytest

from hypoloom.institutional_access import InstitutionalAccess, publisher, validate_document
from hypoloom.institutional_browser import PlaywrightInstitutionalBrowser
from hypoloom.grounding_web import GroundingWeb, GroundingWebError
from hypoloom.paper_sources import PaperWorkspace, web_tool
from hypoloom.fake_agent import default_request
from hypoloom.domain import AgentCommand
from tests.runtime.test_paper_reading import reading_host

URL = "https://www.nature.com/articles/s41586-026-00001-1"
SECOND = "https://www.nature.com/articles/s41586-026-00002-2"
SCOPE = ("run", "expert", "component")
PDF = b"%PDF-1.4\nexact fixture\n%%EOF"


def document(url=URL):
    doi = "10.1038/" + url.rsplit("/", 1)[-1]
    return {"content": PDF, "pages": (f"Methods: coefficient 0.83. DOI {doi}",),
            "url": url + ".pdf", "requested_url": url, "title": "A verified scholarly method",
            "identity": doi, "media_type": "application/pdf"}


class Backend:
    configuration = {"fixture": True}

    def __init__(self):
        self.calls = []
        self.state = "login_required"
        self.closed = False

    def fetch(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return {"state": self.state, "document": document(url)}

    def import_pdf(self, path, url, title):
        self.calls.append((path, url))
        return {"state": "available", "document": document(url)}

    def close(self):
        self.closed = True


def pump(access, predicate, runs=("run",)):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        access.tick(set(runs))
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError(access.status(runs[0]) if runs else "cleanup timed out")


def test_wait_deduplicates_provider_and_recovers_without_reprompting_every_paper():
    backend = Backend()
    clock = [0.]
    access = InstitutionalAccess(backend, clock=lambda: clock[0])
    try:
        access.request(SCOPE, URL)
        access.request(SCOPE, URL)
        access.request(SCOPE, SECOND)
        pump(access, lambda: access.peek(SCOPE, URL)["reason"] == "login_required")
        for _ in range(5):
            access.tick({"run"})
        assert len(backend.calls) == 1
        assert len(access.status("run")["groups"]) == 1
        assert access.status("run")["pending"] == 2
        backend.state = "available"
        clock[0] = 31
        pump(access, lambda: access.status("run")["available"] == 2)
        assert len(backend.calls) == 3
        assert access.peek(SCOPE, URL)["content"] == PDF
        assert access.peek(("run", "other-expert", "component"), URL) is None
        assert "content" not in json.dumps(access.status("run"))
    finally:
        access.close()
    assert backend.closed


def test_late_result_after_run_stop_is_discarded_and_host_poll_is_nonblocking():
    entered, release = Event(), Event()
    class Slow(Backend):
        def fetch(self, url, **kwargs):
            entered.set()
            assert release.wait(3)
            return {"state": "available", "document": document()}
    backend = Slow()
    access = InstitutionalAccess(backend)
    try:
        access.request(SCOPE, URL)
        access.tick({"run"})
        assert entered.wait(1)
        start = time.monotonic()
        access.tick(set())
        assert time.monotonic() - start < .2
        release.set()
        pump(access, lambda: access._flight is None, runs=())
        assert access.status("run")["groups"] == []
    finally:
        release.set()
        access.close()


@pytest.mark.parametrize("state", ["access_denied", "no_full_text", "identity_unconfirmed"])
def test_terminal_acquisition_failure_does_not_automatically_retry(state):
    backend = Backend(); backend.state = state
    access = InstitutionalAccess(backend, poll_seconds=0)
    try:
        access.request(SCOPE, URL)
        pump(access, lambda: access.peek(SCOPE, URL).get("reason") == state)
        for _ in range(5):
            access.tick({"run"})
        assert len(backend.calls) == 1
    finally:
        access.close()


def test_network_wait_is_retried_without_blocking_other_papers_or_leaking_diagnostics():
    class Network(Backend):
        def fetch(self, url, **kwargs):
            self.calls.append(url)
            if url == URL and len(self.calls) == 1:
                return {"state": "fetch_unavailable", "reason": "network_changed"}
            return {"state": "available", "document": document(url)}
    events, clock = [], [0.]
    backend = Network()
    access = InstitutionalAccess(backend, clock=lambda: clock[0], event_sink=events.append)
    try:
        access.request(SCOPE, URL)
        access.request(SCOPE, SECOND)
        pump(access, lambda: access.status("run")["available"] == 1)
        waiting = access.status("run")["groups"][0]["papers"][0]
        assert waiting["state"] == "fetch_unavailable" and waiting["reason_code"] == "network_changed"
        assert waiting["last_checked_at"] and backend.calls == [URL, SECOND]
        clock[0] = 31
        pump(access, lambda: access.status("run")["available"] == 2)
        transitions = [e["payload"] for e in events if e["payload"]["url"] == URL]
        assert [(e["from"], e["to"]) for e in transitions] == [
            (None, "queued"), ("queued", "checking"), ("checking", "fetch_unavailable"),
            ("fetch_unavailable", "checking"), ("checking", "available")]
        assert transitions[2]["reason_code"] == "network_changed"
        assert "content" not in json.dumps(events)
    finally:
        access.close()


def test_adapter_reason_is_allowlisted_before_status_and_logging():
    backend = Backend()
    backend.fetch = lambda *args, **kwargs: {"state": "fetch_unavailable", "reason": "https://school/sso?token=SECRET"}
    events = []
    access = InstitutionalAccess(backend, event_sink=events.append)
    try:
        access.request(SCOPE, URL)
        pump(access, lambda: access.peek(SCOPE, URL)["reason"] == "fetch_unavailable")
        assert "SECRET" not in json.dumps([access.status("run"), events])
    finally:
        access.close()


@pytest.mark.parametrize("reason", ["network_changed", "network_error", "dns_failed",
    "connection_failed", "navigation_timeout", "redirect_unresolved", "http_server_error", "http_rate_limited", "pdf_response_not_pdf"])
def test_transient_backoff_caps_and_resets_after_recovery(reason):
    backend, clock = Backend(), [0.]
    backend.fetch = lambda *args, **kwargs: {"state": "fetch_unavailable", "reason": reason}
    access = InstitutionalAccess(backend, clock=lambda: clock[0])
    try:
        access.request(SCOPE, URL)
        item = access._items[(*SCOPE, URL)]
        for delay in (30, 60, 120, 300, 300):
            pump(access, lambda: item.state == "fetch_unavailable")
            paper = access.status("run")["groups"][0]["papers"][0]
            assert paper["reason_code"] == reason and paper["retry_in_seconds"] == delay
            clock[0] += delay - 1
            access.tick({"run"})
            assert item.state == "fetch_unavailable" and access._flight is None
            clock[0] += 1
            access.tick({"run"})
            assert item.state == "checking"
        backend.fetch = lambda *args, **kwargs: {"state": "available", "document": document()}
        pump(access, lambda: item.state in {"fetch_unavailable", "available"})
        if item.state != "available":
            clock[0] = item.next_check
            pump(access, lambda: item.state == "available")
        assert item.transient_failures == 0
        assert access.status("run")["groups"][0]["papers"][0]["retry_in_seconds"] is None
    finally:
        access.close()


def test_auth_poll_and_operator_retry_reset_transient_backoff():
    backend, clock = Backend(), [0.]
    result = [{"state": "fetch_unavailable", "reason": "network_error"}]
    backend.fetch = lambda *args, **kwargs: result[0]
    access = InstitutionalAccess(backend, clock=lambda: clock[0])
    try:
        access.request(SCOPE, URL)
        item = access._items[(*SCOPE, URL)]
        pump(access, lambda: item.state == "fetch_unavailable")
        clock[0] = 30
        access.tick({"run"})
        pump(access, lambda: item.state == "fetch_unavailable")
        assert item.next_check == 90
        access.control("run", "Nature", "retry", url=URL)
        pump(access, lambda: item.state == "fetch_unavailable")
        assert item.next_check == 60, "operator retry starts a fresh backoff"
        result[0] = {"state": "login_required"}
        clock[0] = 60
        access.tick({"run"})
        pump(access, lambda: item.state == "login_required")
        assert item.next_check == 90 and item.transient_failures == 0
        result[0] = {"state": "fetch_unavailable", "reason": "network_error"}
        clock[0] = 90
        access.tick({"run"})
        pump(access, lambda: item.state == "fetch_unavailable")
        assert item.next_check == 120
    finally:
        access.close()


def test_invalid_document_and_mismatched_identity_are_rejected():
    with pytest.raises(ValueError, match="complete PDF"):
        validate_document({**document(), "content": b"<html>login</html>"})
    with pytest.raises(ValueError, match="identity_unconfirmed"):
        validate_document(document(), identity="doi:10.1038/another", title="A verified scholarly method")
    assert validate_document(document(), identity="doi:10.1038/s41586-026-00001-1")["pages"]
    with pytest.raises(ValueError, match="readable"):
        validate_document({**document(), "pages": ("",)})


def test_pdf_identity_cannot_be_borrowed_from_landing_metadata_or_references():
    expected = "doi:10.1038/s41586-026-00001-1"
    wrong = {**document(), "pages": ("Different article DOI 10.1038/s41586-026-99999-9",
        "References: " + expected)}
    with pytest.raises(ValueError, match="identity_unconfirmed"):
        validate_document(wrong, identity=expected)
    no_doi = {**document(), "pages": ("A verified scholarly method\nActual first-page content",)}
    assert validate_document(no_doi, identity=expected, title=no_doi["title"])
    with pytest.raises(ValueError, match="identity_unconfirmed"):
        validate_document({**no_doi, "pages": ("Unrelated front page", expected)}, identity=expected)


@pytest.mark.parametrize("retry", [False, True])
@pytest.mark.parametrize("cached", [False, True])
def test_rejected_public_packet_never_reenters_browser_queue_or_cache(tmp_path, retry, cached):
    from hypoloom.agent_aci import EmbeddedInput
    from hypoloom.institutional_access import scope_for
    from hypoloom.literature import FixedLiteratureIO, LITERATURE_PACKET_MEDIA_TYPE
    from hypoloom.paper_sources import prepare_sources
    from tests.runtime.test_literature import FakeFetcher, _papers, _query
    candidates = [replace(paper, landing_uri=URL, source_uri=URL) for paper in _papers()]
    fetcher = FakeFetcher(papers=candidates, resolutions={"www.nature.com": ("10.0.0.7",)})
    io = FixedLiteratureIO(fetcher)
    retrieval = io.retrieve(_query())
    assert not fetcher.fetch_calls and not retrieval.pdf_artifacts
    request = replace(default_request(AgentCommand.GROUNDING), schema_version=20,
        required_output_schema="GroundingCandidate/v3", input_artifacts=(EmbeddedInput.from_bytes(
            "test--literature-packet", LITERATURE_PACKET_MEDIA_TYPE, retrieval.packet.to_bytes()),))
    original = {"observation_json": json.dumps({"request": request.to_dict(),
        "preflight_diagnostics": {"literature_download_trace": io.download_trace}})}
    rows = [original]
    if retry:
        request = replace(request, attempt_id="reused-packet-attempt", attempt_ordinal=2)
        rows.append({"observation_json": json.dumps({"request": request.to_dict(),
            "preflight_diagnostics": {"literature_reused_from_attempt": "original"}})})
    backend = Backend(); backend.state = "available"
    access = InstitutionalAccess(backend)
    public_calls = []
    def fetch(url):
        public_calls.append(url)
        raise GroundingWebError("http_error", "403")
    workspace = SimpleNamespace(request=request, attempt_id=request.attempt_id)
    host = SimpleNamespace(state_root=tmp_path, institutional_access=access,
        store=SimpleNamespace(list_attempts=lambda **kwargs: rows),
        grounding_web=SimpleNamespace(document=fetch), literature_io=io,
        running_agents={request.attempt_id: SimpleNamespace(tool_workspace=workspace)},
        _agent_tool_request=lambda *a, **k: None, web_tool_results={})
    try:
        if cached:
            access.request(scope_for(request), URL)
            pump(access, lambda: access.status(request.run_id)["available"] == 1, runs=(request.run_id,))
        before = len(backend.calls)
        prepare_sources(host, workspace, retrieval)
        assert access.status(request.run_id)["pending"] == 0
        result = web_tool(host, request, "web_fetch", {"mode": "document", "url": URL})
        assert result["status"] == "unavailable" and result["error"]["code"] == "unsafe_url"
        assert not workspace.papers.sources
        assert not public_calls and len(backend.calls) == before
        unrelated = "https://link.springer.com/article/10.1007/example"
        assert web_tool(host, request, "web_fetch", {"mode": "document", "url": unrelated})["status"] == "pending"
        assert public_calls == [unrelated]
    finally:
        if hasattr(workspace, "papers"): workspace.papers.close()
        access.close()


@pytest.mark.parametrize("url", ["http://127.0.0.1/a", "https://nature.com.evil.example/a",
    "https://user:secret@nature.com/articles/a", "https://arxiv.org/abs/1234.56789"])
def test_browser_channel_does_not_accept_unrelated_or_credential_bearing_urls(url):
    assert publisher(url) is None


def test_supported_publishers_and_loopback_connection_validation():
    for url in (URL, "https://doi.org/10.1109/TEST.2026.1234", "https://www.sciencedirect.com/science/article/pii/1234"):
        assert publisher(url)
    with pytest.raises(ValueError, match="loopback"):
        PlaywrightInstitutionalBrowser("http://remote.example:9222")


def test_browser_exception_diagnostics_do_not_contain_session_secrets():
    class Broken(Backend):
        def fetch(self, *args, **kwargs):
            raise RuntimeError("https://school.example/sso?SAMLResponse=SECRET")
    access = InstitutionalAccess(Broken())
    try:
        access.request(SCOPE, URL)
        pump(access, lambda: access.peek(SCOPE, URL)["reason"] == "browser_unavailable")
        assert "SECRET" not in json.dumps(access.status("run"))
    finally:
        access.close()


def test_public_full_text_requires_headings_and_text_inside_article():
    from hypoloom.institutional_access import public_full_text
    prefix = '<meta name="citation_doi" content="10.1038/test">'
    summary = '<article><h2>Abstract</h2>Short summary</article>'
    unrelated = '<h2>Methods</h2>' + 'Site navigation ' * 300 + '<h2>References</h2>'
    def doc(html):
        return {"media_type": "text/html", "content": html.encode(), "pages": (html,)}
    assert not public_full_text(doc(prefix + summary + unrelated))
    assert public_full_text(doc(prefix + '<article>' + unrelated + '</article>'))


def test_import_requested_during_inflight_check_is_not_lost():
    entered, release = Event(), Event()
    class Slow(Backend):
        def fetch(self, url, **kwargs):
            entered.set()
            assert release.wait(3)
            return {"state": "login_required"}
    backend = Slow(); access = InstitutionalAccess(backend)
    try:
        access.request(SCOPE, URL)
        access.tick({"run"}); assert entered.wait(1)
        access.control("run", "Nature", "import", url=URL, path="/home/user/paper.pdf")
        release.set()
        pump(access, lambda: access.status("run")["available"] == 1)
        assert backend.calls == [("/home/user/paper.pdf", URL)]
    finally:
        release.set(); access.close()


def test_real_host_control_status_and_stop_preserve_research_revision(reading_host, monkeypatch):
    from hypoloom.control_protocol import ControlRequest
    from hypoloom.institutional_access import scope_for
    host, parent, _, _, _ = reading_host
    backend = Backend(); access = InstitutionalAccess(backend)
    host.institutional_access = access
    access.request(scope_for(parent), URL)
    head = host.store.latest_transition(parent.run_id)
    response = host.handle(ControlRequest(1, "open-literature", None, "LITERATURE_ACCESS",
        {"run_id": parent.run_id, "provider": "Nature", "action": "open"}))
    assert response.status == "accepted"
    assert host.store.latest_transition(parent.run_id) == head
    status = host.handle(ControlRequest(1, "literature-status", None, "STATUS", {"run_id": parent.run_id}))
    assert status.result["literature_access"]["pending"] == 1
    import socket
    from hypoloom.monitor import build_observer_snapshot
    monkeypatch.setattr("hypoloom.monitor.request_over_socket", lambda *args, **kwargs: status)
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(host.state_root / "control.sock"))
        observed = build_observer_snapshot(host.state_root, parent.run_id)
        assert observed.status["literature_access"] == status.result["literature_access"]
    stopped = host.handle(ControlRequest(1, "stop-literature", None, "CANCEL",
        {"run_id": parent.run_id, "cause": "fixture cleanup"}))
    assert stopped.status == "accepted"
    rejected = host.handle(ControlRequest(1, "retry-stopped-literature", None, "LITERATURE_ACCESS",
        {"run_id": parent.run_id, "provider": "Nature", "action": "retry"}))
    assert rejected.status == "rejected"
    host.running_agents.clear()
    host.tick()
    assert access.status(parent.run_id)["groups"] == []


def test_document_tool_admits_recovered_fulltext_and_archives_exact_bytes(tmp_path):
    backend = Backend()
    access = InstitutionalAccess(backend)
    request = replace(default_request(AgentCommand.GROUNDING), schema_version=20, required_output_schema="GroundingCandidate/v3")
    papers = PaperWorkspace(tmp_path, request)
    class Public:
        def document(self, url):
            raise GroundingWebError("http_error", "HTTP 403")
    host = SimpleNamespace(grounding_web=Public(), institutional_access=access, literature_io=None,
        running_agents={request.attempt_id: SimpleNamespace(tool_workspace=SimpleNamespace(papers=papers))},
        _agent_tool_request=lambda *a, **k: None, web_tool_results={})
    args = {"mode": "document", "url": URL}
    try:
        result = web_tool(host, request, "web_fetch", args)
        assert result["status"] == "pending" and not papers.sources
        backend.state = "available"
        pump(access, lambda: access.status(request.run_id)["available"] == 1, runs=(request.run_id,))
        result = web_tool(host, request, "web_fetch", args)
        assert result["status"] == "available"
        assert "0.83" in (papers.root / result["text_path"].removeprefix("/papers/")).read_text()
        archive = tmp_path / "archive"; archive.mkdir()
        papers.archive(archive)
        assert (archive / result["original_path"].lstrip("/")).read_bytes() == PDF
        assert "content" not in host.web_tool_results[request.attempt_id][-1]["result"]
        reused = web_tool(host, request, "web_fetch", {"mode": "document", "paper_ref": result["source_ref"]})
        assert reused == result and len(backend.calls) == 1
    finally:
        papers.close()
        access.close()


def test_public_pdf_never_opens_browser_and_security_failure_is_not_bypassed(tmp_path):
    backend = Backend(); access = InstitutionalAccess(backend)
    request = replace(default_request(AgentCommand.GROUNDING), schema_version=20, required_output_schema="GroundingCandidate/v3")
    papers = PaperWorkspace(tmp_path, request)
    class Public:
        bad = False
        def document(self, url):
            if self.bad:
                raise GroundingWebError("unsafe_url", "private peer")
            return document()
    public = Public()
    host = SimpleNamespace(grounding_web=public, institutional_access=access, literature_io=None,
        running_agents={request.attempt_id: SimpleNamespace(tool_workspace=SimpleNamespace(papers=papers))},
        _agent_tool_request=lambda *a, **k: None, web_tool_results={})
    try:
        assert web_tool(host, request, "web_fetch", {"mode": "document", "url": URL})["status"] == "available"
        public.bad = True
        failure = web_tool(host, request, "web_fetch", {"mode": "document", "url": SECOND})
        assert failure["error"]["code"] == "unsafe_url"
        assert not backend.calls and not access.status(request.run_id)["groups"]
    finally:
        papers.close(); access.close()


def test_browser_connect_guide_and_status_expose_endpoint_without_secrets():
    from hypoloom.literature_access_tui import browser_connect_guide
    text = browser_connect_guide("http://127.0.0.1:52929")
    assert 'chrome.exe" --remote-debugging-port=52929' in text
    assert '--user-data-dir="$env:LOCALAPPDATA\\Hypoloom\\InstitutionalChrome"' in text
    assert "ssh -N " in text and "-R 127.0.0.1:52929:127.0.0.1:52929" in text
    assert "<ssh-user>@<research-server>" in text
    assert "浏览器页面" in text
    fallback = browser_connect_guide(None)
    assert "--remote-debugging-port=<端口>" in fallback
    backend = Backend()
    backend.configuration = {"browser_endpoint": "http://127.0.0.1:52929", "library_url": None}
    access = InstitutionalAccess(backend, clock=lambda: 0.0)
    try:
        status = access.status("run")
    finally:
        access.close()
    assert status["browser_endpoint"] == "http://127.0.0.1:52929"
    assert "content" not in json.dumps(status)


def test_server_browser_status_and_guide_do_not_depend_on_laptop_chrome():
    from hypoloom.literature_access_tui import browser_connect_guide
    backend = PlaywrightInstitutionalBrowser("http://127.0.0.1:9222", ui_url="http://127.0.0.1:5800")
    access = InstitutionalAccess(backend)
    try:
        status = access.status("run")
        assert status["browser_ui_url"] == "http://127.0.0.1:5800"
        text = browser_connect_guide(status["browser_endpoint"], status["browser_ui_url"])
        assert "-L 127.0.0.1:5800:127.0.0.1:5800" in text
        assert "关闭笔记本" in text
        assert "chrome.exe" not in text and " -R " not in text
    finally:
        access.close()


@pytest.mark.parametrize("ui", ["https://public.example:5800/", "http://127.0.0.1:5800/?token=SECRET",
    "http://user:SECRET@127.0.0.1:5800/", "http://127.0.0.1:5800/#token=SECRET", "http://127.0.0.1/"])
def test_server_browser_ui_never_accepts_authentication_urls(ui):
    with pytest.raises(ValueError, match="loopback") as error:
        PlaywrightInstitutionalBrowser("http://127.0.0.1:9222", ui_url=ui)
    assert "SECRET" not in str(error.value)


def test_tui_only_sends_open_retry_and_selected_pdf_not_credentials(tmp_path, monkeypatch):
    from textual.app import App
    from textual.widgets import Input, Button
    from hypoloom.literature_access_tui import LiteratureAccessScreen
    from hypoloom.control_protocol import ControlResponse
    calls = []
    def send(path, request, **kwargs):
        calls.append(request)
        return ControlResponse.accepted(request.request_id, 1, {})
    monkeypatch.setattr("hypoloom.literature_access_tui.request_over_socket", send)
    status = {"run_state": "ACTIVE", "literature_access": {"enabled": True,
        "browser_endpoint": "http://127.0.0.1:52929", "groups": [
        {"provider": "Nature", "papers": [{"url": URL, "title": "Selected paper", "state": "fetch_unavailable",
            "reason_code": "redirect_unresolved", "retry_in_seconds": 120}]}]}}
    class TestApp(App):
        run_id = "run"
        state_root = tmp_path
        def action_refresh(self):
            pass
    async def exercise():
        app = TestApp()
        async with app.run_test(size=(100, 34)) as pilot:
            app.push_screen(LiteratureAccessScreen(status))
            await pilot.pause()
            assert len(list(app.screen.query(Input))) == 1
            assert app.screen.query_one("#literature-guide") is not None
            details = str(app.screen.query_one("#literature-pending").render())
            assert "重定向未完成（redirect_unresolved）" in details
            assert "下次检查约120秒后" in details
            await pilot.click("#literature-login")
            await pilot.click("#literature-retry")
            app.screen.query_one(Input).value = "/home/user/paper.pdf"
            await pilot.click("#literature-import")
            assert [r.payload["action"] for r in calls] == ["open", "retry", "import"]
            assert calls[-1].payload["url"] == URL
            app.screen.update_status({**status, "run_state": "STOPPING"})
            assert app.screen.query_one("#literature-login", Button).disabled
            app.save_screenshot("institutional-access.svg", path=tmp_path)
            await pilot.press("escape")
    asyncio.run(exercise())


def test_web_literature_actions_require_scoped_grant_and_send_clean_payload(tmp_path, monkeypatch):
    import http.client
    from urllib.parse import parse_qs, urlsplit
    from hypoloom.control_protocol import ControlResponse
    from hypoloom.web_observer import create_server
    from test_web_observer import _TOKEN, _start, _stop

    def post(values, *, token=_TOKEN, literature_token=None, origin=None,
             path="/api/literature-access", content_type="application/json"):
        connection = http.client.HTTPConnection(*server.server_address, timeout=10)
        try:
            headers = {"Authorization": "Bearer " + token, "Content-Type": content_type,
                "Origin": origin or f"http://127.0.0.1:{server.server_port}"}
            if literature_token is not None:
                headers["X-Hypoloom-Literature-Token"] = literature_token
            connection.request("POST", path, json.dumps(values).encode(), headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    calls = []
    def send(path, request, *, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise ConnectionError("reply lost after delivery")
        return ControlResponse.accepted(request.request_id, 6, {"run_id": "selected", "run_state": "ACTIVE"})
    monkeypatch.setattr("hypoloom.control_protocol.request_over_socket", send)
    root = tmp_path / "absent"
    server = create_server(root, "selected", port=0, token=_TOKEN)
    thread = _start(server)
    values = {"provider": "Nature", "action": "import", "url": URL, "path": "/home/user/paper.pdf"}
    try:
        assert server._literature_token is None
        assert post(values)[0] == 403
        grant = parse_qs(urlsplit(server.literature_access_url()).fragment)["literature_token"][0]
        assert grant not in server.startup_url()
        assert post(values)[0] == 403  # reader remains read-only after grant
        assert post(values, literature_token=grant, token="wrong")[0] == 401
        assert post(values, literature_token="wrong")[0] == 403
        for origin in ("https://example.invalid", "null", "http://example.invalid", "http://[broken"):
            assert post(values, literature_token=grant, origin=origin)[0] in {400, 403}
        assert post(values, literature_token=grant, content_type="text/plain")[0] == 415
        for bad in ({}, {"provider": "Nature"}, {"provider": "", "action": "open"},
                    {"provider": "Nature", "action": "fetch"},
                    {"provider": "Nature", "action": []},
                    {"provider": "Nature", "action": "import"},
                    {"provider": "Nature", "action": "import", "url": URL, "path": "relative.pdf"},
                    {"provider": "Nature", "action": "open", "url": ""},
                    {"provider": "Nature", "action": "open", "run_id": "foreign"},
                    {"provider": "Nature", "action": "open", "password": "secret"}):
            assert post(bad, literature_token=grant)[0] == 400
        assert post(values, literature_token=grant, path="/api/human-answer")[0] == 403  # scopes do not cross
        assert post(values, literature_token=grant, path="/api/provider-retry")[0] == 403
        assert not calls and not root.exists()
        status, first = post(values, literature_token=grant)
        assert status == 503 and first["error"] == "delivery_unknown"
        status, second = post(values, literature_token=grant)
        assert status == 200 and second["status"] == "accepted"
        assert len(calls) == 2 and calls[0].request_id != calls[1].request_id
        for request in calls:
            assert request.command == "LITERATURE_ACCESS"
            assert request.request_id.startswith("web-literature-")
            # run_id comes from the bound observer; no credentials travel in the payload.
            assert request.payload == {"run_id": "selected", "provider": "Nature", "action": "import",
                "url": URL, "path": "/home/user/paper.pdf"}
        assert not root.exists()
    finally:
        _stop(server, thread)


def test_web_literature_cli_opt_in_and_panel_renders_from_snapshot(tmp_path, monkeypatch):
    from pathlib import Path
    from hypoloom import cli, web_observer
    calls = []
    monkeypatch.setattr(web_observer, "run_web_observer", lambda *a, **kw: calls.append((a, kw)) or 0)
    assert cli.main(["web", "--state-root", str(tmp_path), "--run-id", "run", "--allow-literature-control"]) == 0
    assert calls[0][1]["allow_literature_control"] is True
    assert cli.main(["web", "--state-root", str(tmp_path), "--run-id", "run"]) == 0
    assert "allow_literature_control" not in calls[1][1]

    assets = Path(__file__).resolve().parents[2] / "hypoloom" / "web_observer_assets"
    index = (assets / "index.html").read_text(encoding="utf-8")
    script = (assets / "app.js").read_text(encoding="utf-8")
    assert 'data-workbench="institutional"' in index
    assert 'fragment.get("literature_token")' in script
    assert 'fetch("/api/literature-access"' in script
    assert '"X-Hypoloom-Literature-Token": literatureToken' in script
    # The panel is a snapshot-backed workbench category; it must not fetch detail pages.
    assert 'state.workbenchKind === "reliability" || state.workbenchKind === "institutional"' in script
    assert "renderInstitutional" in script
    assert "remote-debugging-port" in script
