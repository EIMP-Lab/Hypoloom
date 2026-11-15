"""Probe pool lifecycle with real SQLite/socket ownership and CPU children.

Capacity samples are synthetic; these tests never use CUDA or a research pool.
"""
import os
import signal
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from hypoloom.compute_resources import GpuRuntimeObservation
from hypoloom.domain import AttemptTerminalReason, ResourceIntent, RunVersion
from hypoloom.gpu_pool import CapacitySample, GpuPool, PoolError
from hypoloom.gpu_pool_server import PoolServer
from hypoloom.oracle import audit_gpu_pool
from hypoloom.shared_gpu_host import SharedGpuHost
from hypoloom.state_store import HostWriterCapability, StateStore


GPU = "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.fixture
def runtime(tmp_path):
    store = StateStore(tmp_path / "state", HostWriterCapability.issue()).initialize()
    host = SimpleNamespace(store=store, resource_waits={})
    helper = SharedGpuHost(host, tmp_path / "p.sock", "owner", os.getuid())
    host._run_config = lambda _: {"gpu_pool": helper.configuration}
    version = RunVersion(**{field: sqlite3.sqlite_version if field == "sqlite_runtime_version" else "fixture"
                            for field in RunVersion.__dataclass_fields__})
    store.create_run("run", version)
    intent = ResourceIntent(allowed_gpu_uuids=(GPU,), gpu_count=1,
                            workload_memory_mib=12000, reserve_memory_mib=0, sharing="shared")
    pool = GpuPool(tmp_path / "pool", members={os.getuid(): (GPU,)}, reserve_memory_mib=0,
        sampler=lambda: CapacitySample((GpuRuntimeObservation(uuid=GPU, total_memory_mib=16000,
            free_memory_mib=16000, used_memory_mib=0, utilization_percent=0),)))
    value = SimpleNamespace(host=host, helper=helper, store=store, pool=pool, intent=intent)
    transition(value, "run", "ACTIVE", kind="RUN")
    transition(value, "experiment", "READY")
    with store.transaction() as tx:
        tx.append_fact({"fact_id": "spec", "run_id": "run", "fact_type": "ExperimentPreregistered",
            "schema_version": 1, "subject_id": "experiment", "hypothesis_id": "hypothesis",
            "experiment_id": "experiment", "execution_id": "training",
            "payload": {"execution_contract": {"resource_intent": intent.to_dict()}}})
    with PoolServer(pool, tmp_path / "p.sock"):
        try:
            yield value
        finally:
            store.close()


def transition(env, workflow, state, *, kind="EXPERIMENT", payload=None, next_command=None):
    with env.store.transaction() as tx:
        head = tx.latest_transition(workflow)
        revision = 1 if head is None else head["revision"] + 1
        command = f"{workflow}:{revision}"
        tx.record_command({"command_id": command, "run_id": "run", "workflow_id": workflow,
            "command_type": "TEST", "request": {}, "status": "accepted", "result": {}})
        tx.append_transition({"workflow_id": workflow, "run_id": "run", "revision": revision,
            "workflow_kind": kind, "previous_state": None if head is None else head["state"],
            "state": state, "command_id": command, "payload": payload or {}, "next_command": next_command})


def key(env, *, probe=True, ordinal=1):
    return env.helper.key("run", "experiment--implementation-probe" if probe else "training",
                          f"experiment--{'probe' if probe else 'process'}-{ordinal}")


def record(env, identity, *, reason=None, synthetic=False, dispatch_key=None):
    transition(env, "experiment", "EXECUTING", payload={} if synthetic else {
        "attempt_id": identity["attempt_id"], "gpu_pool_key": dispatch_key or identity})
    command = env.store.latest_transition("experiment")["command_id"]
    previous = env.store.list_attempts(execution_id=identity["execution_id"])
    if previous:
        command = previous[0]["command_id"]
    with env.store.transaction() as tx:
        tx.open_attempt({"attempt_id": identity["attempt_id"], "run_id": "run", "workflow_id": "experiment",
            "command_id": command, "kind": "PROCESS", "experiment_id": "experiment",
            "execution_id": identity["execution_id"], "attempt_ordinal": int(identity["attempt_id"].rsplit("-", 1)[1]),
            "host_generation": "fixture", "started_at": 1.0})
        if reason:
            tx.close_attempt(identity["attempt_id"], terminal_reason=AttemptTerminalReason(reason), ended_at=2.0,
                             payload={"synthetic_before_spawn": True} if synthetic else {})


def status(env, identity):
    return env.helper.client.request("status", {"key": identity})["state"]


def raw_host_rows(env):
    return tuple(env.store.inspect("SELECT * FROM " + name) for name in (
        "workflow_runs", "workflow_commands", "workflow_transitions", "research_facts", "execution_attempts"))


@pytest.mark.parametrize("reason", ["SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED", "LOST"])
def test_closed_probe_releases_capacity_for_another_owner_without_rewriting_host(runtime, reason):
    env = runtime
    identity = key(env)
    assert env.helper.reserve(env.intent, identity) is not None
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    try:
        env.helper.activate(identity, child.pid)
        record(env, identity, reason=reason)
        before = raw_host_rows(env)
        # Closed bookkeeping alone cannot release a still-live group.
        assert env.helper.reconcile() is False
        assert env.host.resource_waits["experiment"] == ("run", "process_present")
        assert status(env, identity) == "BOUND"
        os.killpg(child.pid, signal.SIGTERM)
        child.wait(timeout=5)
        second = SharedGpuHost(SimpleNamespace(resource_waits={}), env.helper.socket_path, "second", os.getuid())
        second_key = second.key("other", "training", "other--process-1")
        assert second.reserve(env.intent, second_key) is None
        assert env.helper.reconcile() is True
        assert status(env, identity) == "RELEASED"
        assert second.reserve(env.intent, second_key) is not None
        assert env.helper.reconcile() is True  # exact release replay
        assert second.release(second_key)
        assert raw_host_rows(env) == before
        assert audit_gpu_pool(env.pool.root)["ok"]
        assert env.helper.client.request("snapshot", {})["devices"][0]["active_commitments"] == 0
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)


@pytest.mark.parametrize("synthetic", [False, True])
def test_prestart_probe_cleanup_releases_its_exact_reservation(runtime, synthetic):
    env = runtime
    identity = key(env)
    env.helper.reserve(env.intent, identity)
    record(env, identity, reason="FAILED", synthetic=synthetic)
    assert env.helper.reconcile()
    assert status(env, identity) == "RELEASED"


@pytest.mark.parametrize("stop", ["ACTIVE", "STOPPING", "CANCELLED", "COMPLETED", "EXPERIMENT", "AGENT"])
def test_unopened_probe_queue_is_preserved_until_its_owner_stops(runtime, stop):
    env = runtime
    holder = env.helper.key("holder", "training", "holder--process-1")
    env.helper.reserve(env.intent, holder)
    identity = key(env)
    assert env.helper.reserve(env.intent, identity) is None
    if stop in {"STOPPING", "CANCELLED", "COMPLETED"}:
        transition(env, "run", stop, kind="RUN")
    elif stop == "EXPERIMENT":
        transition(env, "experiment", "TERMINAL")
    elif stop == "AGENT":
        with env.store.transaction() as tx:
            tx.record_command({"command_id": "experiment--agent-cancel", "run_id": "run",
                "workflow_id": "experiment", "command_type": "CANCEL", "request": {},
                "status": "accepted", "result": {}})
    before = raw_host_rows(env)
    assert env.helper.reconcile()
    assert status(env, identity) == ("QUEUED" if stop == "ACTIVE" else "RELEASED")
    assert raw_host_rows(env) == before
    env.helper.release(identity)
    env.helper.release(holder)


def test_open_probe_is_not_released_even_when_process_is_absent(runtime):
    env = runtime
    identity = key(env)
    env.helper.reserve(env.intent, identity)
    record(env, identity)
    assert env.helper.reconcile()
    assert status(env, identity) == "RESERVED"
    env.helper.release(identity)


def test_unknown_process_liveness_keeps_closed_probe_bound(runtime, monkeypatch):
    env = runtime
    identity = key(env)
    env.helper.reserve(env.intent, identity)
    env.helper.activate(identity, 99999999)
    record(env, identity, reason="LOST")
    with monkeypatch.context() as patch:
        patch.setattr(env.pool, "process_present", lambda _: None)
        assert env.helper.reconcile() is False
        assert env.host.resource_waits["experiment"] == ("run", "process_unknown")
        assert status(env, identity) == "BOUND"
    assert env.helper.reconcile()
    assert status(env, identity) == "RELEASED"


def test_probe_and_training_future_ordinals_are_independent(runtime):
    env = runtime
    for probe, count in ((True, 2), (False, 1)):
        for ordinal in range(1, count + 1):
            record(env, key(env, probe=probe, ordinal=ordinal), reason="FAILED", synthetic=True)
    probe_key, training_key = key(env, ordinal=3), key(env, probe=False, ordinal=2)
    env.helper.reserve(env.intent, probe_key)
    assert env.helper.reserve(env.intent, training_key) is None
    transition(env, "run", "STOPPING", kind="RUN")
    assert env.helper.reconcile()
    assert status(env, probe_key) == status(env, training_key) == "RELEASED"


@pytest.mark.parametrize("change", [{"namespace": "foreign"}, {"execution_id": "training"}, {"attempt_id": "wrong"}])
def test_mismatched_probe_dispatch_never_releases(runtime, change):
    env = runtime
    identity = key(env)
    env.helper.reserve(env.intent, identity)
    record(env, identity, reason="SUCCEEDED", dispatch_key={**identity, **change})
    assert env.helper.reconcile() is False
    assert "owner key" in env.host.resource_waits["experiment"][1]
    assert status(env, identity) == "RESERVED"
    env.helper.release(identity)


def test_pool_transport_failure_is_retryable_for_closed_probe(runtime, monkeypatch):
    env = runtime
    identity = key(env)
    env.helper.reserve(env.intent, identity)
    record(env, identity, reason="SUCCEEDED")
    original = env.helper.client.request
    def unavailable(*args, **kwargs):
        raise PoolError("unavailable", "test outage")
    with monkeypatch.context() as patch:
        patch.setattr(env.helper.client, "request", unavailable)
        assert env.helper.reconcile() is False
        assert "unavailable" in env.host.resource_waits["experiment"][1]
    assert env.helper.client.request == original
    assert env.helper.reconcile()
    assert status(env, identity) == "RELEASED"
