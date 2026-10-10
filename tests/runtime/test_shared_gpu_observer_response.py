"""Real Host/control/pool sockets; synthetic GPU telemetry and Process jobs."""
from __future__ import annotations

import threading
import time

import pytest

import hypoloom.workflow_host as workflow_host
from hypoloom.control_protocol import ControlRequest, request_over_socket
from hypoloom.monitor import _HOST_STATUS_TIMEOUT_SECONDS
from hypoloom.oracle import Oracle, audit_gpu_pool
from test_shared_gpu_closure import _clean, _finish, _pool_rows
from test_shared_gpu_dispatch import GPU_A, GPU_B, create, specs, until
from test_shared_gpu_dispatch import environment as environment


@pytest.mark.parametrize("retired_count", [0, 11])
def test_status_remains_responsive_with_retired_history_and_slow_queued_sampling(
    environment, monkeypatch, retired_count, record_property,
):
    make, free, pool, runtime = environment
    host = make("observer")
    host.runner.complete = True
    retired_runs = [f"retired-{index}" for index in range(retired_count)]
    for run_id in retired_runs:
        create(host, runtime, name=run_id)
        until(host, lambda: host.store.latest_transition(run_id)["state"] == "COMPLETED")
    retired_attempts = host.store.list_attempts(kind="PROCESS")
    assert len(retired_attempts) == 2 * retired_count  # Validation plus experiment.
    assert sum(row["execution_id"].endswith("--candidate-validation")
               for row in retired_attempts) == retired_count
    retired_rows = _pool_rows(pool)
    assert len(retired_rows) == retired_count
    assert all(row["state"] == "RELEASED" for row in retired_rows)

    free.update({GPU_A: 1000, GPU_B: 1000})
    create(host, runtime)
    until(host, lambda: bool(specs(host)) and bool(host.resource_waits))
    assert not host.store.list_attempts(execution_id=specs(host)[0]["execution_id"])
    assert sum(row["state"] == "QUEUED" for row in _pool_rows(pool)) == 1

    tick_entered = threading.Event()
    responses, errors, elapsed = [], [], []
    sample, tick, handle = pool.sampler, host.tick, host.handle
    sample_calls = 0

    def slow_sample():
        nonlocal sample_calls
        sample_calls += 1
        time.sleep(0.4)  # Observed production sampler cost, no real GPU access.
        return sample()

    def observed_tick():
        tick_entered.set()
        return tick()

    def observed_handle(request):
        response = handle(request)
        if request.request_id == "queued-observer":
            host.stop_requested = True
        return response

    def observe():
        try:
            assert tick_entered.wait(5), "Host did not enter its scheduling tick"
            started = time.monotonic()
            responses.append(request_over_socket(host.state_root / "control.sock",
                ControlRequest(1, "queued-observer", None, "STATUS", {"run_id": "shared"}),
                timeout=_HOST_STATUS_TIMEOUT_SECONDS))
            elapsed.append(time.monotonic() - started)
        except BaseException as error:
            errors.append(error)
            host.stop_requested = True

    observer = threading.Thread(target=observe, name="queued-gpu-observer")
    with monkeypatch.context() as patch:
        patch.setattr(pool, "sampler", slow_sample)
        patch.setattr(host, "tick", observed_tick)
        patch.setattr(host, "handle", observed_handle)
        # The fixture has already initialized this real Host and generated its
        # completed runs. Enter the production serve loop on the same thread;
        # the fixture owns its final close after the queued run also completes.
        patch.setattr(workflow_host, "WorkflowHost", lambda *args, **kwargs: host)
        patch.setattr(host, "initialize", lambda: None)
        patch.setattr(host, "close", lambda: None)
        patch.setattr(workflow_host.signal, "signal", lambda *args: None)
        observer.start()
        try:
            assert workflow_host.serve(host.state_root) == 0
        finally:
            observer.join(timeout=5)
    assert not observer.is_alive()
    record_property("gpu_sampler_calls", sample_calls)
    assert not errors, errors
    assert len(responses) == 1 and responses[0].status == "accepted"
    assert responses[0].result["run_state"] == "ACTIVE"
    assert responses[0].result["resource_waits"]
    assert elapsed[0] < _HOST_STATUS_TIMEOUT_SECONDS
    assert sample_calls == 1  # The actual queued admission, independent of history.
    record_property("status_seconds", elapsed[0])

    host.stop_requested = False
    free.update({GPU_A: 18000, GPU_B: 16000})
    _finish(host)
    _clean(host)
    for run_id in retired_runs:
        assert Oracle(host.state_root / "state.sqlite", host.state_root / "artifacts").audit(run_id).ok
    attempts = host.store.list_attempts(kind="PROCESS")
    assert len(attempts) == 2 * (retired_count + 1)
    assert sum(row["execution_id"].endswith("--candidate-validation")
               for row in attempts) == retired_count + 1
    assert all(row["state"] == "CLOSED" for row in host.store.list_attempts())
    assert all(row["terminal_reason"] == "SUCCEEDED" for row in attempts)
    assert all(row["state"] == "RELEASED" for row in _pool_rows(pool))
    assert audit_gpu_pool(pool.root)["ok"]
    record_property("closed_processes", len(attempts))
    outcomes = host.store.inspect("SELECT fact_id FROM research_facts WHERE fact_type='CanonicalOutcomeAccepted'")
    assert len(outcomes) == retired_count + 1
    record_property("outcomes", len(outcomes))
