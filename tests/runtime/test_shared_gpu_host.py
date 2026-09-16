"""Host pool adapter with real socket/SQLite and synthetic GPU observations."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from hypoloom.domain import AttemptTerminalReason, ResourceIntent, ResourceKind
from hypoloom.gpu_pool import CapacitySample, PoolError
from hypoloom.gpu_pool_server import PoolServer
from hypoloom.shared_gpu_host import SharedGpuHost

from test_gpu_pool import A, B, World, device
from test_state_transactions import command, open_store, run_version


def intent(*, allowed=(A,), count=1, budget=4000):
    return ResourceIntent(allowed_gpu_uuids=allowed, gpu_count=count,
                          workload_memory_mib=budget, reserve_memory_mib=1000)


@pytest.fixture
def setup(tmp_path):
    world = World()
    pool = world.pool(tmp_path / "pool", members={os.getuid(): (A, B)})
    store = open_store(tmp_path)
    host = SimpleNamespace(store=store, resource_waits={})
    host._run_config = lambda run: json.loads(store.inspect(
        "SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND revision=1", (run,))[0]["payload_json"])
    helper = SharedGpuHost(host, tmp_path / "pool.sock", "root-a", server_uid=os.getuid())
    with PoolServer(pool, helper.configuration["socket_path"]):
        try:
            yield host, helper, world, pool
        finally:
            store.close()


def transition(store, workflow, state, *, kind="EXPERIMENT", payload=None, next_command=None):
    with store.transaction() as tx:
        head = tx.latest_transition(workflow)
        revision = 1 if head is None else head["revision"] + 1
        owner = f"{workflow}--transition-{revision}"
        command(tx, owner, workflow_id=workflow)
        tx.append_transition({"workflow_id": workflow, "revision": revision,
            "run_id": "run-1", "workflow_kind": kind, "previous_state": None if head is None else head["state"],
            "state": state, "command_id": owner, "payload": payload or {}, "next_command": next_command})


def seed(host, helper, *, state="READY", run_state="ACTIVE", resource_intent=True, configured=True):
    store = host.store
    store.create_run("run-1", run_version())
    transition(store, "run-1", run_state, kind="RUN",
               payload={"gpu_pool": helper.configuration} if configured else {})
    contract = {"resource_intent": intent().to_dict()} if resource_intent else {
        "resource_grant": {"kind": "CPU", "gpu_uuid": None}}
    with store.transaction() as tx:
        tx.append_fact({"fact_id": "spec-1", "run_id": "run-1", "fact_type": "ExperimentPreregistered",
            "schema_version": 1, "subject_id": "exp-1", "hypothesis_id": "hyp-1",
            "experiment_id": "exp-1", "execution_id": "execution-1", "payload": {"execution_contract": contract}})
    transition(store, "exp-1", state)
    return helper.key("run-1", "execution-1", "exp-1--process-1")


def attempt(host, helper, *, closed=True, synthetic=False, dispatch_key=None):
    key = helper.key("run-1", "execution-1", "exp-1--process-1")
    if not synthetic:
        transition(host.store, "exp-1", "EXECUTING", payload={
            "attempt_id": key["attempt_id"], "gpu_pool_key": key if dispatch_key is None else dispatch_key})
    head = host.store.latest_transition("exp-1")
    with host.store.transaction() as tx:
        tx.open_attempt({"attempt_id": key["attempt_id"], "run_id": "run-1", "workflow_id": "exp-1",
            "command_id": head["command_id"], "kind": "PROCESS", "experiment_id": "exp-1",
            "execution_id": "execution-1", "attempt_ordinal": 1, "host_generation": "generation-1", "started_at": 1.0})
        if closed:
            tx.close_attempt(key["attempt_id"], terminal_reason=AttemptTerminalReason.CANCELLED if synthetic else AttemptTerminalReason.LOST,
                ended_at=2.0, payload={"synthetic_before_spawn": True} if synthetic else {})
    return key


def test_configuration_is_absolute_validated_and_cannot_be_mutated_through_copy(tmp_path):
    helper = SharedGpuHost(SimpleNamespace(), tmp_path / "pool.sock", "root-a", server_uid=123)
    assert helper.configuration == {"socket_path": str(tmp_path / "pool.sock"), "namespace": "root-a", "server_uid": 123}
    helper.configuration["namespace"] = "changed"
    assert helper.key("run", "execution", "exp--process-1")["namespace"] == "root-a"


@pytest.mark.parametrize("kwargs", [{"namespace": "/private/root"}, {"namespace": ""}, {"server_uid": True}, {"server_uid": -1}])
def test_invalid_pool_configuration_fails_before_connection(tmp_path, kwargs):
    with pytest.raises(ValueError):
        SharedGpuHost(SimpleNamespace(), tmp_path / "pool.sock", **{"namespace": "root-a", **kwargs})


def test_reserve_returns_exact_plural_grant_using_server_live_choice(setup):
    host, helper, world, _ = setup
    key = helper.key("run-1", "execution-1", "exp-1--process-1")
    world.sample = CapacitySample((device(A, 10000), device(B, 11000)))
    grant = helper.reserve(intent(allowed=(A, B), count=2), key)
    assert grant.kind is ResourceKind.GPU and grant.device_uuids == (A, B)
    assert helper.reserve(intent(allowed=(A, B), count=2), key) == grant
    assert host.resource_waits == {}


def test_capacity_wait_opens_no_host_attempt_and_preserves_future_key(setup):
    host, helper, world, pool = setup
    key = seed(host, helper)
    world.sample = CapacitySample((device(A, 3000),))
    assert helper.reserve(intent(), key) is None
    assert host.resource_waits["exp-1"] == ("run-1", "capacity_unavailable")
    assert host.store.list_attempts() == ()
    assert helper.reconcile() is True
    assert pool.handle(os.getuid(), "status", {"key": key})["state"] == "QUEUED"
    world.sample = CapacitySample((device(A, 10000),))
    assert helper.reserve(intent(), key).device_uuids == (A,)
    assert "exp-1" not in host.resource_waits


def test_missing_service_is_observable_wait_without_creating_private_state(tmp_path):
    host = SimpleNamespace(resource_waits={})
    helper = SharedGpuHost(host, tmp_path / "absent" / "pool.sock", "root-a")
    assert helper.reserve(intent(), helper.key("run-1", "execution-1", "exp-1--process-1")) is None
    assert "unavailable" in host.resource_waits["exp-1"][1]
    assert list(tmp_path.iterdir()) == []


def test_activation_capacity_loss_does_not_authorize_exec(setup):
    host, helper, world, pool = setup
    key = seed(host, helper)
    assert helper.reserve(intent(), key) is not None
    world.sample = CapacitySample((device(A, 4000),))
    with pytest.raises(PoolError) as error:
        helper.activate(key, 99999)
    assert error.value.code == "resource_wait"
    assert pool.handle(os.getuid(), "status", {"key": key})["process_group_id"] is None


def test_activation_replay_and_release_are_exact_and_process_absence_gated(setup):
    _, helper, world, _ = setup
    key = helper.key("run-1", "execution-1", "exp-1--process-1")
    helper.reserve(intent(), key)
    helper.activate(key, 99999)
    helper.activate(key, 99999)
    with pytest.raises(PoolError) as error:
        helper.activate(key, 99998)
    assert error.value.code == "process_conflict"
    assert helper.release(key) is False
    world.present = False
    assert helper.release(key) is True
    with pytest.raises(PoolError) as error:
        helper.activate(key, 99999)
    assert error.value.code == "invalid_state"


@pytest.mark.parametrize("change", [{"gpu_uuids": [B]}, {"gpu_uuids": [A, A]}, {"gpu_uuids": []},
    {"key": {"namespace": "foreign"}}, {"process_group_id": 99}, {"state": "BOUND"}])
def test_malformed_or_foreign_reservation_response_never_grants(setup, monkeypatch, change):
    _, helper, _, _ = setup
    key = helper.key("run-1", "execution-1", "exp-1--process-1")
    response = {"key": key, "state": "RESERVED", "gpu_uuids": [A], "process_group_id": None, "reason": None}
    monkeypatch.setattr(helper.client, "request", lambda *_: {**response, **change})
    with pytest.raises(PoolError):
        helper.reserve(intent(), key)


@pytest.mark.parametrize("closed", [False, True])
def test_reconcile_releases_only_closed_attempts_and_does_not_mutate_host_rows(setup, closed):
    host, helper, world, pool = setup
    key = seed(host, helper)
    helper.reserve(intent(), key)
    helper.activate(key, 99999)
    attempt(host, helper, closed=closed)
    before = host.store.list_attempts(), host.store.inspect("SELECT * FROM workflow_transitions")
    world.present = False
    assert helper.reconcile() is True
    assert pool.handle(os.getuid(), "status", {"key": key})["state"] == ("RELEASED" if closed else "BOUND")
    assert (host.store.list_attempts(), host.store.inspect("SELECT * FROM workflow_transitions")) == before


def test_reconcile_retries_live_closed_binding_without_forgetting_it(setup):
    host, helper, world, pool = setup
    key = seed(host, helper)
    helper.reserve(intent(), key)
    helper.activate(key, 99999)
    attempt(host, helper)
    assert helper.reconcile() is False
    assert "process_present" in host.resource_waits["exp-1"][1]
    world.present = False
    restarted = SharedGpuHost(host, helper.configuration["socket_path"], "root-a", server_uid=os.getuid())
    assert restarted.reconcile() is True
    assert pool.handle(os.getuid(), "status", {"key": key})["state"] == "RELEASED"


@pytest.mark.parametrize("state,run_state", [("TERMINAL", "ACTIVE"), ("READY", "STOPPING"), ("READY", "COMPLETED")])
def test_reconcile_withdraws_unopened_reservation_only_after_stop_or_terminal(setup, state, run_state):
    host, helper, _, pool = setup
    key = seed(host, helper, state=state, run_state=run_state)
    helper.reserve(intent(), key)
    assert helper.reconcile() is True
    assert host.store.list_attempts() == ()
    assert pool.handle(os.getuid(), "status", {"key": key})["state"] == "RELEASED"


def test_reconcile_withdraws_cancelled_queue_before_attempt_opens(setup):
    host, helper, world, pool = setup
    key = seed(host, helper)
    world.sample = CapacitySample((device(A, 3000),))
    helper.reserve(intent(), key)
    with host.store.transaction() as tx:
        command(tx, "exp-1--agent-cancel", workflow_id="exp-1", command_type="CANCEL")
    assert helper.reconcile() is True
    assert pool.handle(os.getuid(), "status", {"key": key})["state"] == "RELEASED"


def test_synthetic_cancellation_releases_prior_future_key_without_inventing_grant(setup):
    host, helper, world, pool = setup
    key = seed(host, helper)
    world.sample = CapacitySample((device(A, 3000),))
    helper.reserve(intent(), key)
    attempt(host, helper, synthetic=True)
    transition(host.store, "exp-1", "TERMINAL")
    assert helper.reconcile() is True
    assert pool.handle(os.getuid(), "status", {"key": key})["state"] == "RELEASED"


@pytest.mark.parametrize("configured,resource_intent", [(False, True), (True, False)])
def test_nonparticipating_or_cpu_work_does_not_contact_pool(setup, monkeypatch, configured, resource_intent):
    host, helper, _, _ = setup
    seed(host, helper, state="TERMINAL", configured=configured, resource_intent=resource_intent)
    monkeypatch.setattr(helper.client, "request", lambda *_: pytest.fail("CPU or nonparticipating run contacted pool"))
    assert helper.reconcile() is True


def test_reconcile_refuses_foreign_dispatch_owner_key(setup):
    host, helper, _, pool = setup
    key = seed(host, helper)
    helper.reserve(intent(), key)
    attempt(host, helper, dispatch_key={**key, "namespace": "foreign-root"})
    assert helper.reconcile() is False
    assert "owner" in host.resource_waits["exp-1"][1]
    assert pool.handle(os.getuid(), "status", {"key": key})["state"] == "RESERVED"


def test_reconcile_service_failure_keeps_closed_cleanup_visible_and_retryable(setup, monkeypatch):
    host, helper, _, _ = setup
    key = seed(host, helper)
    helper.reserve(intent(), key)
    attempt(host, helper)
    original = helper.client.request
    with monkeypatch.context() as patch:
        def unavailable(*_):
            raise PoolError("unavailable", "service stopped")
        patch.setattr(helper.client, "request", unavailable)
        assert helper.reconcile() is False
        assert "unavailable" in host.resource_waits["exp-1"][1]
    assert helper.client.request == original
    assert helper.reconcile() is True


def test_reconcile_future_ordinal_ignores_another_runs_execution_attempts(setup):
    host, helper, _, pool = setup
    key = seed(host, helper, state="TERMINAL")
    helper.reserve(intent(), key)
    with host.store.transaction() as tx:
        tx.create_run("run-2", run_version())
        command(tx, "other-command", run_id="run-2", workflow_id="exp-2")
        tx.append_transition({"workflow_id": "run-2", "run_id": "run-2", "revision": 1,
            "workflow_kind": "RUN", "state": "ACTIVE", "previous_state": None,
            "command_id": "other-command", "payload": {}})
        tx.open_attempt({"attempt_id": "exp-2--process-1", "run_id": "run-2", "workflow_id": "exp-2",
            "command_id": "other-command", "kind": "PROCESS", "experiment_id": "exp-2",
            "execution_id": "execution-1", "attempt_ordinal": 1, "host_generation": "other", "started_at": 1.0})
    assert helper.reconcile() is True
    assert pool.handle(os.getuid(), "status", {"key": key})["state"] == "RELEASED"
