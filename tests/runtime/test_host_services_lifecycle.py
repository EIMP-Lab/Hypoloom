from contextlib import contextmanager

import pytest

import hypoloom.workflow_host as module


def test_queued_control_clients_share_one_scheduler_tick(tmp_path, monkeypatch):
    import socket
    from hypoloom.control_protocol import ControlRequest, ControlResponse, send_message
    handled, ticks, clients = [], [], []

    class Host:
        stop_requested = False
        operational_halt = None
        institutional_access = None
        def __init__(self, *args, **kwargs): pass
        def initialize(self): pass
        def close(self): pass
        def handle(self, request):
            handled.append(request.request_id)
            return ControlResponse.failure(request.request_id, "rejected", "fixture", "fixture")
        def tick(self):
            ticks.append(len(handled))
            self.stop_requested = True

    @contextmanager
    def services():
        try:
            for index in range(12):
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.settimeout(2)
                client.connect(str(tmp_path / "control.sock"))
                send_message(client, ControlRequest(schema_version=1, request_id=f"status-{index}",
                    expected_revision=None, command="STATUS", payload={}).to_mapping())
                clients.append(client)
            yield
        finally:
            for client in clients: client.close()

    monkeypatch.setattr(module, "WorkflowHost", Host)
    monkeypatch.setattr(module.signal, "signal", lambda *args: None)
    assert module.serve(tmp_path, services_factory=services) == 0
    assert ticks == [12]
    assert handled == [f"status-{index}" for index in range(12)]


@pytest.mark.parametrize("exit_error", [False, True])
def test_services_enter_only_after_single_writer_and_socket_and_close_before_host(tmp_path, monkeypatch, exit_error):
    events = []

    class Host:
        stop_requested = True
        operational_halt = None
        institutional_access = None
        def __init__(self, *args, **kwargs): pass
        def initialize(self): events.append("initialize")
        def close(self): events.append("host-close")

    monkeypatch.setattr(module, "WorkflowHost", Host)
    monkeypatch.setattr(module.signal, "signal", lambda *args: None)

    @contextmanager
    def services():
        assert events == ["initialize"]
        assert (tmp_path / "control.sock").is_socket()
        with pytest.raises(module.HostError, match="another Workflow Host"):
            module.serve(tmp_path, services_factory=lambda: pytest.fail("second service started"))
        events.append("services-open")
        yield
        events.append("services-close")
        if exit_error:
            raise RuntimeError("shutdown diagnostic")

    if exit_error:
        with pytest.raises(RuntimeError, match="shutdown diagnostic"):
            module.serve(tmp_path, services_factory=services)
    else:
        assert module.serve(tmp_path, services_factory=services) == 0
    assert events == ["initialize", "services-open", "services-close", "host-close"]
    assert not (tmp_path / "control.sock").exists()
    assert module.serve(tmp_path) == 0  # old default and released controller lock


def test_failed_initialize_never_starts_observer_services(tmp_path, monkeypatch):
    events = []

    class Host:
        def __init__(self, *args, **kwargs): pass
        def initialize(self): raise RuntimeError("unreadable state")
        def close(self): events.append("closed")

    monkeypatch.setattr(module, "WorkflowHost", Host)
    with pytest.raises(RuntimeError, match="unreadable state"):
        module.serve(tmp_path, services_factory=lambda: pytest.fail("services started on unreadable state"))
    assert events == ["closed"]
