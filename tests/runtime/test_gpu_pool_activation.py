"""Atomic pre-exec activation of an existing GPU reservation and process group."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from hypoloom.gpu_pool import CapacitySample, PoolError

from test_gpu_pool import A, B, UID, World, action, device, key, reserve, rows


def activate(pool, *, run="run-a", pgid=99999):
    return action(pool, "activate", run=run, process_group_id=pgid)


def test_activation_records_fresh_capacity_and_pgid_in_one_bound_transition(tmp_path):
    world = World()
    pool = world.pool(tmp_path)
    reserve(pool, allowed=(A, B), count=2)
    world.sample = CapacitySample((device(A, 11000), device(B, 10000)))
    calls = world.calls
    result = activate(pool)
    assert result == {"key": key(), "state": "BOUND", "gpu_uuids": [A, B],
                      "process_group_id": 99999, "reason": None}
    assert world.calls == calls + 1
    stored = rows(tmp_path)[0]
    assert stored["state"] == "BOUND"
    assert stored["process_group_id"] == 99999
    decision = json.loads(stored["decision_json"])
    assert decision["phase"] == "arm"
    assert [item["free_memory_mib"] for item in decision["devices"]] == [11000, 10000]


def test_activation_waits_for_fresh_capacity_without_binding_or_discarding_grant(tmp_path):
    world = World()
    pool = world.pool(tmp_path)
    reserve(pool, allowed=(A, B), count=2, budget=8000)
    world.sample = CapacitySample((device(A, 9000), device(B, 8500)))
    waiting = activate(pool)
    assert waiting["state"] == "RESERVED"
    assert waiting["reason"] == "capacity_unavailable"
    assert waiting["process_group_id"] is None
    assert waiting["gpu_uuids"] == [A, B]
    world.sample = CapacitySample((device(A, 9000), device(B, 9000)))
    assert activate(pool, pgid=99998)["state"] == "BOUND"
    assert rows(tmp_path)[0]["process_group_id"] == 99998


def test_queued_activation_waits_then_schedules_and_binds(tmp_path):
    world = World()
    world.sample = CapacitySample((device(A, 3000),))
    pool = world.pool(tmp_path)
    assert reserve(pool)["state"] == "QUEUED"
    waiting = activate(pool)
    assert waiting["state"] == "QUEUED"
    assert waiting["reason"] == "capacity_unavailable"
    assert waiting["process_group_id"] is None
    world.sample = CapacitySample((device(A, 12000),))
    assert activate(pool)["state"] == "BOUND"


def test_queued_activation_preserves_existing_fairness(tmp_path):
    world = World()
    world.sample = CapacitySample((device(A, 1000),))
    pool = world.pool(tmp_path)
    reserve(pool, run="older", budget=8000)
    reserve(pool, run="younger", budget=1000)
    world.sample = CapacitySample((device(A, 6000),))
    waiting = activate(pool, run="younger")
    assert waiting["state"] == "QUEUED"
    assert waiting["reason"] == "fairness_wait"
    assert waiting["process_group_id"] is None


@pytest.mark.parametrize(("sample", "reason"), [
    (CapacitySample(()), "missing_inventory"),
    (CapacitySample((device(A), device(A))), "invalid_sample"),
    (RuntimeError("NVML unavailable"), "telemetry_unavailable"),
])
def test_activation_unknown_inventory_fails_closed_but_reservation_can_withdraw(
    tmp_path, sample, reason,
):
    world = World()
    pool = world.pool(tmp_path)
    reserve(pool)
    world.sample = sample
    waiting = activate(pool)
    assert waiting["state"] == "RESERVED"
    assert waiting["process_group_id"] is None
    assert waiting["reason"] == reason
    assert action(pool, "release")["state"] == "RELEASED"


def test_activation_rechecks_revoked_gpu_membership(tmp_path):
    world = World()
    pool = world.pool(tmp_path)
    reserve(pool)
    restarted = world.pool(tmp_path, members={UID: (B,)})
    waiting = activate(restarted)
    assert waiting["state"] == "RESERVED"
    assert waiting["reason"] == "unauthorized"
    assert waiting["process_group_id"] is None


def test_lost_activation_reply_replays_after_restart_and_retains_until_group_absent(tmp_path):
    world = World()
    pool = world.pool(tmp_path)
    reserve(pool)
    bound = activate(pool)
    restarted = world.pool(tmp_path)
    world.sample = RuntimeError("telemetry is not needed to replay a binding")
    assert activate(restarted) == bound
    with pytest.raises(PoolError) as error:
        activate(restarted, pgid=99998)
    assert error.value.code == "process_conflict"
    world.present = None
    assert action(restarted, "release")["reason"] == "process_unknown"
    world.present = True
    assert action(restarted, "release")["reason"] == "process_present"
    world.present = False
    released = action(restarted, "release")
    assert released["state"] == "RELEASED"
    assert activate(world.pool(tmp_path)) == released
    with pytest.raises(PoolError) as error:
        activate(restarted, pgid=99998)
    assert error.value.code == "process_conflict"


@pytest.mark.parametrize("state", ["LAUNCHING", "RELEASED"])
def test_activation_cannot_reinterpret_legacy_launch_or_withdrawn_request(tmp_path, state):
    pool = World().pool(tmp_path)
    reserve(pool)
    action(pool, "arm" if state == "LAUNCHING" else "release")
    before = rows(tmp_path)
    with pytest.raises(PoolError) as error:
        activate(pool)
    assert error.value.code == "invalid_state"
    assert rows(tmp_path) == before
    if state == "LAUNCHING":
        assert action(pool, "release")["reason"] == "recovery_required"


@pytest.mark.parametrize("change", [
    {"process_group_id": value} for value in (None, True, -1, 0, 1, 2.0, "2", 2**53)
] + [
    {"uid": UID}, {"resident_memory_mib": 1000}, {"gpu_uuids": [A]},
    {"key": {**key(), "uid": UID}}, {"key": key(namespace="/private/root")},
])
def test_invalid_activation_inputs_cannot_mutate_reservation(tmp_path, change):
    pool = World().pool(tmp_path)
    reserve(pool)
    before = rows(tmp_path)
    with pytest.raises(PoolError) as error:
        pool.handle(UID, "activate", {"key": key(), "process_group_id": 99999, **change})
    assert error.value.code == "invalid_argument"
    assert rows(tmp_path) == before


def test_activation_requires_owner_existing_identity(tmp_path):
    pool = World().pool(tmp_path)
    reserve(pool)
    with pytest.raises(PoolError) as error:
        pool.handle(2000, "activate", {"key": key(), "process_group_id": 99999})
    assert error.value.code == "not_found"
    with pytest.raises(PoolError) as error:
        activate(pool, run="unknown")
    assert error.value.code == "not_found"


def test_independent_activations_cannot_overpromise_same_gpu(tmp_path):
    world = World()
    first, second = world.pool(tmp_path), world.pool(tmp_path)
    assert reserve(first, run="one", budget=8000)["state"] == "RESERVED"
    assert reserve(second, run="two", budget=8000)["state"] == "QUEUED"
    with ThreadPoolExecutor(max_workers=2) as threads:
        results = list(threads.map(lambda item: activate(item[0], run=item[1], pgid=item[2]),
                                  [(first, "one", 99998), (second, "two", 99999)]))
    assert [result["state"] for result in results] == ["BOUND", "QUEUED"]
    stored = rows(tmp_path)
    assert [row["process_group_id"] for row in stored] == [99998, None]


def test_activation_checks_capacity_while_transaction_owns_writer_lock(tmp_path):
    world = World()
    pool = world.pool(tmp_path)
    reserve(pool)
    observed = []

    def sample():
        with sqlite3.connect(pool.database_path, timeout=0) as other:
            row = other.execute("SELECT state, process_group_id FROM requests").fetchone()
            assert row == ("RESERVED", None)
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("BEGIN IMMEDIATE")
        observed.append(True)
        return CapacitySample((device(A),))

    pool.sampler = sample
    assert activate(pool)["state"] == "BOUND"
    assert observed == [True]


def test_activation_error_before_commit_rolls_back_binding_for_restart(tmp_path, monkeypatch):
    world = World()
    pool = world.pool(tmp_path)
    reserve(pool)
    before = rows(tmp_path)

    def fail_response(row):
        assert row["state"] == "BOUND"
        assert row["process_group_id"] == 99999
        raise RuntimeError("injected failure before commit")

    monkeypatch.setattr(pool, "_response", fail_response)
    with pytest.raises(RuntimeError, match="before commit"):
        activate(pool)
    assert rows(tmp_path) == before
    assert activate(world.pool(tmp_path))["state"] == "BOUND"
