from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import pytest

from hypoloom.network_io import NetworkOperationError, run_network_operation


OPERATION = "tests.runtime.network_fault_worker:request"


def absent(pid):
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize("mode", ["dns", "tls_handshake", "response_headers", "response_body"])
def test_deadline_stops_real_worker_and_preserves_phase(tmp_path, mode):
    pid_file = tmp_path / "pid"
    started = time.monotonic()
    deadline = started + 0.65
    with pytest.raises(NetworkOperationError) as error:
        run_network_operation(OPERATION, {"mode": mode, "pid_file": str(pid_file), "deadline": deadline},
            deadline=deadline, max_output_bytes=2048)
    assert error.value.code == "timed_out"
    assert error.value.transport_phase == mode
    assert 0.4 < time.monotonic() - started < 1.5
    assert error.value.received_bytes > 0 if mode == "response_body" else error.value.received_bytes == 0
    absent(int(pid_file.read_text()))


@pytest.mark.parametrize("mode", ["hang", "closed_stdout"])
def test_cancel_reaps_active_worker_before_return(tmp_path, mode):
    event = threading.Event()
    pid_file = tmp_path / "pid"
    def cancel():
        deadline = time.monotonic() + 3
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.1)
        event.set()
    thread = threading.Thread(target=cancel)
    thread.start()
    with pytest.raises(NetworkOperationError) as error:
        run_network_operation(OPERATION, {"mode": mode, "pid_file": str(pid_file)},
            deadline=time.monotonic() + 10, max_output_bytes=2048, cancel_event=event)
    thread.join(timeout=2)
    assert not thread.is_alive() and error.value.code == "cancelled"
    absent(int(pid_file.read_text()))


def test_repeated_failures_leave_no_fds_threads_or_live_workers(tmp_path):
    before_fds = len(tuple(Path("/proc/self/fd").iterdir()))
    before_threads = {thread.ident for thread in threading.enumerate()}
    for index in range(4):
        pid_file = tmp_path / str(index)
        with pytest.raises(NetworkOperationError):
            run_network_operation(OPERATION, {"mode": "hang", "pid_file": str(pid_file)},
                deadline=time.monotonic() + 0.45, max_output_bytes=2048)
        absent(int(pid_file.read_text()))
    assert len(tuple(Path("/proc/self/fd").iterdir())) == before_fds
    assert {thread.ident for thread in threading.enumerate()} == before_threads


def test_public_wrapper_passes_remaining_deadline_headers_and_cancellation(monkeypatch):
    import base64
    from hypoloom import grounding_web as web

    observed = []
    event = threading.Event()
    def operation(name, payload, **limits):
        observed.append((name, payload, limits))
        if name.endswith(":_resolve_in_process"):
            return {"addresses": ["8.8.8.8"]}
        return {"requested_uri": payload["uri"], "status_code": 200, "peer_address": "8.8.8.8",
                "media_type": "text/plain", "content_length": 4, "location": None,
                "body_b64": base64.b64encode(b"safe").decode(), "retry_after": None}
    monkeypatch.setattr(web, "run_network_operation", operation)
    transport = web.PublicHttpsTransport()
    started = time.monotonic()
    assert transport.resolve("example.org", timeout_seconds=0.5, cancel_event=event) == ("8.8.8.8",)
    result = transport.fetch("https://example.org/paper", approved_addresses=("8.8.8.8",),
        max_bytes=10, timeout_seconds=0.4, cancel_event=event)
    assert result.body == b"safe"
    assert started <= observed[0][2]["deadline"] <= time.monotonic() + 0.5
    assert observed[1][1]["deadline"] == observed[1][2]["deadline"]
    assert observed[1][2]["deadline"] <= time.monotonic() + 0.4
    assert all(item[2]["cancel_event"] is event for item in observed)
    assert set(observed[1][1]["headers"]) == {"Accept", "User-Agent"}


def test_grounding_dns_and_fetch_share_outer_window(monkeypatch):
    from hypoloom import grounding_web as web
    now = [100.0]
    calls = []
    def operation(name, payload, **limits):
        calls.append(limits["deadline"])
        if name.endswith(":_resolve_in_process"):
            now[0] += 0.6
            return {"addresses": ["8.8.8.8"]}
        raise NetworkOperationError("timed_out", transport_phase="response_headers")
    monkeypatch.setattr(web.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(web, "run_network_operation", operation)
    reader = web.GroundingWeb(max_seconds=1, clock=lambda: now[0])
    with pytest.raises(web.GroundingWebError) as error:
        reader.web_fetch("https://example.org/paper")
    assert error.value.code == "timed_out"
    assert error.value.transport_phase == "response_headers"
    assert calls == [101.0, 101.0]


def test_private_input_and_clean_environment_and_no_error_text(tmp_path, monkeypatch):
    monkeypatch.setenv("HYP_NETWORK_TEST_SECRET", "private-test-key")
    pid_file = tmp_path / "pid"
    result = run_network_operation(OPERATION,
        {"mode": "environment", "secret": "private-test-key", "pid_file": str(pid_file)},
        deadline=time.monotonic() + 3, max_output_bytes=2048)
    assert result == {"secret_seen": True, "ambient_seen": False}
    absent(int(pid_file.read_text()))
    with pytest.raises(NetworkOperationError) as error:
        run_network_operation(OPERATION, {"mode": "exception", "pid_file": str(pid_file)},
            deadline=time.monotonic() + 3, max_output_bytes=2048)
    assert "private-test-key" not in str(error.value) + repr(error.value.__dict__)
    absent(int(pid_file.read_text()))


def test_output_bound_reaps_and_next_request_works(tmp_path):
    pid_file = tmp_path / "pid"
    with pytest.raises(NetworkOperationError) as error:
        run_network_operation(OPERATION, {"mode": "flood", "pid_file": str(pid_file)},
            deadline=time.monotonic() + 3, max_output_bytes=2048)
    assert error.value.code == "response_limit"
    absent(int(pid_file.read_text()))
    result = run_network_operation(OPERATION,
        {"mode": "environment", "secret": "private-test-key", "pid_file": str(pid_file)},
        deadline=time.monotonic() + 3, max_output_bytes=2048)
    assert result["secret_seen"]
    absent(int(pid_file.read_text()))


def test_dns_error_keeps_progress_without_raw_exception(tmp_path):
    pid_file = tmp_path / "pid"
    with pytest.raises(NetworkOperationError) as error:
        run_network_operation(OPERATION, {"mode": "dns_error", "pid_file": str(pid_file)},
            deadline=time.monotonic() + 3, max_output_bytes=2048)
    assert error.value.transport_phase == "dns"
    assert "private" not in str(error.value) + repr(error.value.__dict__)
    absent(int(pid_file.read_text()))


@pytest.mark.skipif(sys.platform != "linux", reason="Linux subreaper observes orphan worker cleanup")
def test_parent_crash_closes_worker_and_subreaper_collects_it(tmp_path):
    # A test-owned subreaper provides the OS reaping normally supplied by init;
    # the production worker must itself exit after losing its private pipe.
    source = r'''
import ctypes, json, os, signal, subprocess, sys, time
from pathlib import Path
ctypes.CDLL(None).prctl(36, 1, 0, 0, 0)
pid_file = Path(sys.argv[1])
code = "from hypoloom.network_io import run_network_operation; import time; run_network_operation('tests.runtime.network_fault_worker:request', {'mode':'hang','pid_file':%r}, deadline=time.monotonic()+30,max_output_bytes=2048)" % str(pid_file)
parent = subprocess.Popen([sys.executable, '-c', code])
try:
    deadline = time.monotonic() + 5
    while not pid_file.exists() and time.monotonic() < deadline: time.sleep(.01)
    worker = int(pid_file.read_text())
    os.kill(parent.pid, signal.SIGKILL)
    parent.wait(timeout=2)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        collected, status = os.waitpid(worker, os.WNOHANG)
        if collected:
            print(json.dumps({'worker':worker,'collected':collected})); break
        time.sleep(.01)
    else: raise RuntimeError('network worker survived parent crash')
finally:
    if parent.poll() is None: parent.kill(); parent.wait()
    if 'worker' in locals():
        try: os.kill(worker, signal.SIGKILL)
        except ProcessLookupError: pass
'''
    result = subprocess.run([sys.executable, "-c", source, str(tmp_path / "pid")],
        cwd=Path(__file__).parents[2], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["worker"] == observed["collected"]
    absent(observed["worker"])
