from dataclasses import replace
from threading import Event

import pytest

from hypoloom.control_protocol import ControlRequest
from hypoloom.institutional_access import InstitutionalAccess, scope_for
from hypoloom.institutional_controls import bind
from tests.runtime.test_institutional_access import ALL_PROVIDERS_AUTO, Backend, URL, SECOND, document, pump
from tests.runtime.test_paper_reading import reading_host


@pytest.mark.parametrize("mode", ["public_only", "disabled"])
def test_run_choices_are_transactional_idempotent_and_recover_without_reopening_login(reading_host, mode):
    host, parent, *_ = reading_host
    access = InstitutionalAccess(Backend(), provider_modes=ALL_PROVIDERS_AUTO)
    host.institutional_access = access
    bind(host)
    scope = scope_for(parent)
    access.request(scope, URL)
    revision = host.store.latest_transition(parent.run_id)["revision"]
    request = ControlRequest(1, "skip-paper", revision, "LITERATURE_ACCESS",
        {"run_id": parent.run_id, "provider": "Springer Nature", "action": "skip", "url": URL})
    accepted = host.handle(request)
    assert accepted.status == "accepted"
    assert host.handle(request).revision == accepted.revision
    assert host.handle(replace(request, payload={**request.payload, "action": "restore"})).status == "conflict"
    assert host.handle(replace(request, request_id="stale-revision")).status == "conflict"
    alias = "https://doi.org/10.1038/" + URL.rsplit("/", 1)[-1]
    assert access.request((parent.run_id, "another-expert", "another"), alias)["reason"] == "skipped"
    assert access.request(("other-run", "expert", "a"), URL)["status"] == "pending"
    # A new queue, as on Host recovery, has no login window and reads the committed choice.
    access.close()
    restored = InstitutionalAccess(Backend(), provider_modes=ALL_PROVIDERS_AUTO)
    host.institutional_access = restored
    bind(host)
    assert restored.request(scope, alias)["reason"] == "skipped"
    assert not restored._manual_until
    restored._items.clear()  # eviction cannot discard operator intent
    assert restored.request(scope, URL)["reason"] == "skipped"
    response = host.handle(ControlRequest(1, "restore-paper", accepted.revision, "LITERATURE_ACCESS",
        {**request.payload, "action": "restore"}))
    assert response.status == "accepted"
    assert restored.request(scope, URL)["status"] == "pending"
    changed = host.handle(ControlRequest(1, "public-only", response.revision, "LITERATURE_ACCESS",
        {"run_id": parent.run_id, "provider": "Springer Nature", "action": "mode", "mode": mode}))
    assert changed.status == "accepted"
    assert restored.request(scope, SECOND)["reason"] == mode
    assert restored.configuration["provider_modes"]["Springer Nature"] == "auto", "frozen launch configuration stays unchanged"
    assert len(host.store.inspect("SELECT * FROM workflow_commands WHERE command_type='LITERATURE_ACCESS'")) == 3


def test_skip_during_download_discards_result_but_allows_explicit_import(reading_host):
    host, parent, *_ = reading_host
    entered, release = Event(), Event()
    class Slow(Backend):
        def fetch(self, url, **kwargs):
            entered.set()
            assert release.wait(5)
            return {"state": "available", "document": document(url)}
    access = InstitutionalAccess(Slow(), provider_modes=ALL_PROVIDERS_AUTO)
    host.institutional_access = access
    bind(host)
    scope = scope_for(parent)
    try:
        access.request(scope, URL)
        access.tick({parent.run_id})
        assert entered.wait(1)
        result = host.handle(ControlRequest(1, "skip-inflight", host.store.latest_transition(parent.run_id)["revision"],
            "LITERATURE_ACCESS", {"run_id": parent.run_id, "provider": "Springer Nature", "action": "skip", "url": URL}))
        assert result.status == "accepted"
        release.set()
        pump(access, lambda: access._flight is None, runs=(parent.run_id,))
        assert access.peek(scope, URL)["reason"] == "skipped"
        access.control(parent.run_id, "Springer Nature", "import", url=URL, path="/fixture.pdf")
        pump(access, lambda: access.status(parent.run_id)["available"] == 1, runs=(parent.run_id,))
        assert access.peek(scope, URL)["content"] == document()["content"]
    finally:
        release.set()


def test_real_host_restart_recovers_choices_without_replaying_manual_window(tmp_path):
    from pathlib import Path
    import tempfile
    from hypoloom.workflow_host import WorkflowHost
    from hypoloom.oracle import Oracle
    from tests.runtime.test_expert_loop_host import _pilot_payload
    with tempfile.TemporaryDirectory(prefix="hl-inst-restart-", dir="/var/tmp") as target:
        access = InstitutionalAccess(Backend(), provider_modes=ALL_PROVIDERS_AUTO)
        host = WorkflowHost(tmp_path / "state", institutional_access=access)
        host.initialize()
        run_id = "institutional-restart"
        try:
            reply = host.handle(ControlRequest(1, "create", 0, "CREATE_RUN", _pilot_payload(tmp_path, Path(target), run_id)))
            assert reply.status == "accepted", reply
            reply = host.handle(ControlRequest(1, "skip", reply.revision, "LITERATURE_ACCESS",
                {"run_id": run_id, "provider": "Springer Nature", "action": "skip", "url": URL}))
            assert reply.status == "accepted", reply
            reply = host.handle(ControlRequest(1, "mode", reply.revision, "LITERATURE_ACCESS",
                {"run_id": run_id, "provider": "IEEE", "action": "mode", "mode": "public_only"}))
            assert reply.status == "accepted", reply
            host.institutional_access._manual_until[(run_id, "Springer Nature")] = host.institutional_access.clock() + 600
        finally:
            host.close()
        restarted = WorkflowHost(tmp_path / "state", institutional_access=InstitutionalAccess(Backend(), provider_modes=ALL_PROVIDERS_AUTO))
        restarted.initialize()
        try:
            restored = restarted.institutional_access
            assert not restored._manual_until
            assert restored.request((run_id, "reader", "a"), URL)["reason"] == "skipped"
            assert restored.request((run_id, "reader", "a"), "https://ieeexplore.ieee.org/document/11006507")["reason"] == "public_only"
            assert len(restarted.store.inspect("SELECT * FROM workflow_commands WHERE command_type='LITERATURE_ACCESS'")) == 2
            result = restarted.handle(ControlRequest(1, "cleanup", None, "CANCEL", {"run_id": run_id, "cause": "isolated test cleanup"}))
            assert result.status == "accepted"
            for _ in range(10): restarted.tick()
            assert restarted.store.latest_transition(run_id)["state"] == "CANCELLED"
            assert Oracle(restarted.store.database_path, restarted.publisher.root).audit(run_id).ok
        finally:
            restarted.close()


@pytest.mark.parametrize("args", [["--institutional-provider-mode", "IEEE=no"],
    ["--institutional-provider-mode", "IEEE=auto", "--institutional-provider-mode", "IEEE=public_only"],
    ["--institutional-acquisition-seconds", "nan"], ["--institutional-manual-seconds", "1801"]])
def test_launch_rejects_invalid_modes_and_budgets(args):
    from hypoloom.cli import build_parser
    from hypoloom.institutional_options import build
    parsed = build_parser().parse_args(["serve", "--institutional-browser-url", "http://127.0.0.1:9222", *args])
    with pytest.raises(ValueError): build(parsed)


def test_web_preference_retry_preserves_request_id_and_revision(tmp_path, monkeypatch):
    from hypoloom.control_protocol import ControlResponse
    from hypoloom.web_observer import create_server
    from test_web_observer import _TOKEN, _start, _stop
    import http.client
    import json
    from urllib.parse import urlsplit, parse_qs
    calls = []
    monkeypatch.setattr("hypoloom.control_protocol.request_over_socket", lambda path, request, **kwargs:
        calls.append(request) or ControlResponse.accepted(request.request_id, 7, {}))
    server = create_server(tmp_path, "run", port=0, token=_TOKEN)
    thread = _start(server)
    try:
        location = urlsplit(server.literature_access_url())
        tokens = parse_qs(location.fragment)
        values = {"provider": "IEEE", "action": "mode", "mode": "public_only",
                  "request_id": "same-click", "expected_revision": 6}
        headers = {"Authorization": "Bearer " + _TOKEN,
            "X-Hypoloom-Literature-Token": tokens["literature_token"][0],
            "Content-Type": "application/json", "Origin": f"http://{location.netloc}"}
        for _ in range(2):
            connection = http.client.HTTPConnection(location.hostname, location.port)
            connection.request("POST", "/api/literature-access", json.dumps(values), headers)
            reply = connection.getresponse(); assert reply.status == 200, reply.read()
            reply.read(); connection.close()
        assert calls[0] == calls[1]
        assert calls[0].expected_revision == 6 and calls[0].request_id == "same-click"
    finally:
        _stop(server, thread)
