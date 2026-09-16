"""Transactional GPU promises with synthetic, explicitly attributable samples."""

from __future__ import annotations

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from hypoloom.compute_resources import GpuRuntimeObservation
from hypoloom import gpu_pool
from hypoloom.gpu_pool import CapacitySample, GpuPool, PoolError, RequestKey


A = "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
B = "GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
UID = 1000


def device(uuid: str = A, free: int = 12000, total: int = 16000):
    return GpuRuntimeObservation(
        uuid=uuid, total_memory_mib=total, free_memory_mib=free,
        used_memory_mib=total - free, utilization_percent=73,
    )


def key(run="run-a", attempt="attempt-1", namespace="root-a"):
    return {"namespace": namespace, "run_id": run,
            "execution_id": "execution-1", "attempt_id": attempt}


def request(run="run-a", attempt="attempt-1", *, namespace="root-a",
            allowed=(A,), count=1, budget=4000, reserve=1000):
    return {"key": key(run, attempt, namespace), "allowed_gpu_uuids": list(allowed),
            "gpu_count": count, "workload_memory_mib": budget,
            "reserve_memory_mib": reserve}


class World:
    def __init__(self):
        self.sample = CapacitySample((device(), device(B)))
        self.present: bool | None = True
        self.calls = 0

    def sample_now(self):
        self.calls += 1
        if isinstance(self.sample, Exception):
            raise self.sample
        return self.sample

    def pool(self, root: Path, members=None):
        return GpuPool(root, members=members or {UID: (A, B), 2000: (A,)},
                       reserve_memory_mib=1000, sampler=self.sample_now,
                       process_present=lambda pgid: self.present)


def reserve(pool, **kwargs):
    return pool.handle(UID, "reserve", request(**kwargs))


def action(pool, operation, run="run-a", attempt="attempt-1", **kwargs):
    return pool.handle(UID, operation, {"key": key(run, attempt), **kwargs})


def rows(root):
    with sqlite3.connect(root / "pool.sqlite3") as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT * FROM requests ORDER BY request_seq")]


def test_same_gpu_promises_are_serialized_across_independent_pool_instances(tmp_path):
    world = World()
    first, second = world.pool(tmp_path), world.pool(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as threads:
        results = list(threads.map(lambda item: reserve(item[0], run=item[1], budget=8000),
                                  [(first, "one"), (second, "two")]))
    assert sorted(result["state"] for result in results) == ["QUEUED", "RESERVED"]
    assert len(rows(tmp_path)) == 2


def test_same_gpu_overlap_and_shared_reserve_charged_once(tmp_path):
    pool = World().pool(tmp_path)
    assert reserve(pool, budget=5000, reserve=2000)["state"] == "RESERVED"
    assert reserve(pool, run="other", budget=5000, reserve=2000)["state"] == "RESERVED"
    assert reserve(pool, run="third", budget=1)["state"] == "QUEUED"


def test_request_identity_includes_namespace_owner_and_exact_intent(tmp_path):
    world = World()
    pool = world.pool(tmp_path)
    original = reserve(pool, budget=2000)
    assert reserve(pool, budget=2000) == original
    assert reserve(pool, namespace="other-root", budget=2000)["state"] == "RESERVED"
    assert pool.handle(2000, "reserve", request(budget=2000))["state"] == "RESERVED"
    with pytest.raises(PoolError) as error:
        reserve(pool, budget=2001)
    assert error.value.code == "intent_conflict"
    assert len(rows(tmp_path)) == 3


def test_release_is_durable_tombstone_and_replay_never_readmits(tmp_path):
    world = World()
    pool = world.pool(tmp_path)
    reserve(pool)
    released = action(pool, "release")
    assert released["state"] == "RELEASED"
    restarted = world.pool(tmp_path)
    assert reserve(restarted) == released
    assert action(restarted, "release") == released
    assert len(rows(tmp_path)) == 1


def test_atomic_multi_gpu_selection_uses_live_capacity_and_uuid_tie_break(tmp_path):
    world = World()
    world.sample = CapacitySample((device(A, 9000), device(B, 3000)))
    pool = world.pool(tmp_path)
    assert reserve(pool, allowed=(A, B), count=2, budget=4000)["state"] == "QUEUED"
    assert rows(tmp_path)[0]["grant_json"] == "[]"
    world.sample = CapacitySample((device(A, 9000), device(B, 9000)))
    assert action(pool, "status")["gpu_uuids"] == [A, B]
    action(pool, "release")
    assert reserve(pool, run="tie", allowed=(B, A))["gpu_uuids"] == [A]


def test_selects_device_with_more_allocatable_memory_not_utilization(tmp_path):
    world = World()
    world.sample = CapacitySample((device(A, 6000), device(B, 10000)))
    assert reserve(world.pool(tmp_path), allowed=(A, B))["gpu_uuids"] == [B]


def test_arm_rechecks_capacity_without_discarding_its_commitment(tmp_path):
    world = World()
    pool = world.pool(tmp_path)
    reserve(pool, budget=8000)
    world.sample = CapacitySample((device(A, 8500), device(B)))
    waiting = action(pool, "arm")
    assert waiting["state"] == "RESERVED"
    assert waiting["reason"] == "capacity_unavailable"
    assert waiting["gpu_uuids"] == [A]
    world.sample = CapacitySample((device(A, 9000), device(B)))
    assert action(pool, "arm")["state"] == "LAUNCHING"
    assert json.loads(rows(tmp_path)[0]["decision_json"])["phase"] == "arm"


def test_launch_gap_never_releases_on_restart_or_unknown_process(tmp_path):
    world = World()
    pool = world.pool(tmp_path)
    reserve(pool)
    action(pool, "arm")
    world.present = False
    restarted = world.pool(tmp_path)
    assert action(restarted, "release")["state"] == "LAUNCHING"
    assert action(restarted, "release")["reason"] == "recovery_required"
    assert action(restarted, "bind_process", process_group_id=99999)["state"] == "BOUND"
    world.present = None
    assert action(restarted, "release")["state"] == "BOUND"
    world.present = True
    assert action(restarted, "release")["reason"] == "process_present"
    world.present = False
    assert action(restarted, "release")["state"] == "RELEASED"


def test_binding_requires_armed_intent_and_idempotent_exact_pgid(tmp_path):
    pool = World().pool(tmp_path)
    reserve(pool)
    with pytest.raises(PoolError) as error:
        action(pool, "bind_process", process_group_id=99)
    assert error.value.code == "invalid_state"
    action(pool, "arm")
    bound = action(pool, "bind_process", process_group_id=99)
    assert action(pool, "bind_process", process_group_id=99) == bound
    with pytest.raises(PoolError) as error:
        action(pool, "bind_process", process_group_id=100)
    assert error.value.code == "process_conflict"


def test_only_current_bound_attribution_receives_resident_credit(tmp_path):
    world = World()
    pool = world.pool(tmp_path)
    reserve(pool, budget=8000)
    owned = RequestKey(UID, **key())
    world.sample = CapacitySample((device(A, 7000), device(B)), {(owned, A): 6000})
    assert reserve(pool, run="other", budget=4000)["state"] == "QUEUED"
    world.sample = CapacitySample((device(), device(B)))
    action(pool, "arm")
    action(pool, "bind_process", process_group_id=99999)
    world.sample = CapacitySample((device(A, 7000), device(B)), {(owned, A): 6000})
    assert action(pool, "status", run="other")["state"] == "RESERVED"
    decision = json.loads(rows(tmp_path)[1]["decision_json"])
    assert decision["devices"][0]["outstanding_memory_mib"] == 2000
    assert decision["devices"][0]["commitments"][0]["resident_memory_mib"] == 6000


def test_unknown_attribution_is_reported_unknown_and_receives_zero_credit(tmp_path):
    world = World()
    pool = world.pool(tmp_path)
    reserve(pool, budget=4000)
    action(pool, "arm")
    action(pool, "bind_process", process_group_id=99999)
    assert reserve(pool, run="other", budget=4000)["state"] == "RESERVED"
    own = json.loads(rows(tmp_path)[1]["decision_json"])["devices"][0]["commitments"][0]
    assert own["resident_memory_mib"] is None
    assert own["workload_memory_mib"] == 4000


def test_bound_credit_is_capped_by_budget_and_sum_must_fit_device_used(tmp_path):
    world = World()
    pool = world.pool(tmp_path)
    reserve(pool, budget=2000)
    action(pool, "arm")
    action(pool, "bind_process", process_group_id=99999)
    own = RequestKey(UID, **key())
    world.sample = CapacitySample((device(A, 9000), device(B)), {(own, A): 6000})
    assert reserve(pool, run="other", budget=8000)["state"] == "RESERVED"
    d = json.loads(rows(tmp_path)[1]["decision_json"])["devices"][0]
    assert d["outstanding_memory_mib"] == 0
    world.sample = CapacitySample((device(A, 9000), device(B)), {(own, A): 8000})
    assert reserve(pool, run="third", budget=1)["reason"] == "invalid_sample"


def test_fairness_survives_restart_and_small_heads_cannot_backfill(tmp_path):
    world = World()
    pool = world.pool(tmp_path)
    reserve(pool, run="served", budget=1000)
    action(pool, "release", run="served")
    world.sample = CapacitySample((device(A, 1000), device(B)))
    reserve(pool, run="served", attempt="second", budget=1000)
    reserve(pool, run="new", budget=8000)
    reserve(pool, run="third", allowed=(B,), budget=3000)
    world.sample = CapacitySample((device(A, 6000), device(B)))
    restarted = world.pool(tmp_path)
    assert action(restarted, "status", run="served", attempt="second")["state"] == "QUEUED"
    assert action(restarted, "status", run="third")["state"] == "RESERVED"
    world.sample = CapacitySample((device(A, 9000), device(B)))
    assert action(restarted, "status", run="new")["state"] == "RESERVED"
    assert action(restarted, "status", run="served", attempt="second")["state"] == "QUEUED"
    action(restarted, "release", run="new")
    assert action(restarted, "status", run="served", attempt="second")["state"] == "RESERVED"


def test_run_fifo_and_one_head_per_run_per_scheduling_pass(tmp_path):
    world = World()
    world.sample = CapacitySample((device(A, 0), device(B)))
    pool = world.pool(tmp_path)
    reserve(pool, run="a", attempt="one", budget=4000)
    reserve(pool, run="a", attempt="two", budget=4000)
    reserve(pool, run="b", attempt="one", budget=4000)
    world.sample = CapacitySample((device(A, 9000), device(B)))
    assert action(pool, "status", run="a", attempt="one")["state"] == "RESERVED"
    result = rows(tmp_path)
    assert [row["state"] for row in result] == ["RESERVED", "QUEUED", "RESERVED"]


def test_snapshot_discloses_only_authorized_aggregate_capacity(tmp_path):
    world = World()
    pool = world.pool(tmp_path)
    reserve(pool, run="private-run", budget=3000)
    snapshot = pool.handle(2000, "snapshot", {})
    assert [item["gpu_uuid"] for item in snapshot["devices"]] == [A]
    assert snapshot["devices"][0]["outstanding_memory_mib"] == 3000
    assert "private-run" not in json.dumps(snapshot)
    assert "execution-1" not in json.dumps(snapshot)
    with pytest.raises(PoolError) as error:
        pool.handle(2000, "status", {"key": key("private-run")})
    assert error.value.code == "not_found"
    with pytest.raises(PoolError) as error:
        pool.handle(3000, "snapshot", {})
    assert error.value.code == "unauthorized"


@pytest.mark.parametrize("change", [
    {"gpu_count": True}, {"gpu_count": 0}, {"gpu_count": 2},
    {"workload_memory_mib": 0}, {"workload_memory_mib": 1.5},
    {"reserve_memory_mib": -1}, {"allowed_gpu_uuids": [A, A]},
    {"allowed_gpu_uuids": ["GPU-invalid"]}, {"uid": 2000},
    {"resident_memory_mib": 1000}, {"workload_memory_mib": 2**100},
    {"key": {**key(), "uid": UID}}, {"key": key(namespace="/private/path")},
])
def test_malformed_intent_is_rejected_without_persistence(tmp_path, change):
    pool = World().pool(tmp_path)
    with pytest.raises(PoolError):
        pool.handle(UID, "reserve", {**request(), **change})
    assert rows(tmp_path) == []


def test_membership_limits_each_exact_uuid_and_payload_cannot_set_owner(tmp_path):
    pool = World().pool(tmp_path)
    with pytest.raises(PoolError) as error:
        pool.handle(2000, "reserve", request(allowed=(B,)))
    assert error.value.code == "unauthorized"
    with pytest.raises(PoolError):
        pool.handle(True, "reserve", request())
    assert rows(tmp_path) == []


@pytest.mark.parametrize("bad_sample", [
    CapacitySample(()), CapacitySample((device(), device())),
    CapacitySample((device(),), {(RequestKey(UID, **key()), A): -1}),
    CapacitySample((device(),), {(RequestKey(UID, **key()), A): True}),
    CapacitySample((device(total=2**53),)),
    RuntimeError("NVML unavailable"),
])
def test_invalid_or_missing_samples_fail_closed_and_queued_withdrawal_still_works(
    tmp_path, bad_sample,
):
    world = World()
    world.sample = bad_sample
    pool = world.pool(tmp_path)
    result = reserve(pool)
    assert result["state"] == "QUEUED"
    assert result["reason"] in {"invalid_sample", "missing_inventory", "telemetry_unavailable"}
    assert action(pool, "release")["state"] == "RELEASED"


def test_sampler_runs_inside_transaction_before_any_capacity_promise(tmp_path):
    observed = []

    def sample():
        with sqlite3.connect(tmp_path / "pool.sqlite3", timeout=0) as rival:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                rival.execute("BEGIN IMMEDIATE")
        observed.append(True)
        return CapacitySample((device(),))

    pool = GpuPool(tmp_path, members={UID: (A,)}, reserve_memory_mib=1000, sampler=sample)
    assert reserve(pool)["state"] == "RESERVED"
    assert observed


@pytest.mark.parametrize("pgid", [0, 1, True, 2**53])
def test_bind_rejects_init_group_and_non_json_safe_process_identity(tmp_path, pgid):
    pool = World().pool(tmp_path)
    reserve(pool)
    action(pool, "arm")
    with pytest.raises(PoolError) as error:
        action(pool, "bind_process", process_group_id=pgid)
    assert error.value.code == "invalid_argument"
    assert action(pool, "status")["state"] == "LAUNCHING"


@pytest.mark.parametrize("field", ["workload_memory_mib", "reserve_memory_mib"])
def test_intent_rejects_integer_values_not_exactly_representable_in_json_clients(tmp_path, field):
    pool = World().pool(tmp_path)
    with pytest.raises(PoolError):
        pool.handle(UID, "reserve", {**request(), field: 2**53})
    assert rows(tmp_path) == []


@pytest.mark.parametrize("target", ["root", "database", "root_symlink", "database_symlink"])
def test_public_or_symlinked_pool_storage_is_refused_without_modifying_it(tmp_path, target):
    root = tmp_path / "pool"
    root.mkdir(mode=0o700)
    database = root / "pool.sqlite3"
    if target == "root":
        root.chmod(0o755)
    elif target == "database":
        database.write_bytes(b"not opened or rewritten")
        database.chmod(0o644)
    elif target == "root_symlink":
        link = tmp_path / "link"
        link.symlink_to(root, target_is_directory=True)
        root = link
    else:
        other = tmp_path / "other.sqlite3"
        other.write_bytes(b"not opened or rewritten")
        other.chmod(0o600)
        database.symlink_to(other)
    before = root.lstat() if target.startswith("root") else database.lstat()
    with pytest.raises(PoolError) as error:
        World().pool(root)
    assert error.value.code == "invalid_storage"
    after = root.lstat() if target.startswith("root") else database.lstat()
    assert (before.st_mode, before.st_ino) == (after.st_mode, after.st_ino)
    if target.startswith("root"):
        assert not (root / "pool.sqlite3").exists()
    else:
        assert database.read_bytes() == b"not opened or rewritten"


def test_new_pool_storage_is_owned_private_and_regular(tmp_path):
    pool = World().pool(tmp_path / "pool")
    for path in (pool.root, pool.database_path):
        assert path.stat().st_uid == os.getuid()
        assert path.stat().st_mode & 0o077 == 0


def test_snapshot_dates_live_observation_and_declares_unknown_attribution(tmp_path, monkeypatch):
    world = World()
    pool = world.pool(tmp_path)
    reserve(pool, budget=4000)
    action(pool, "arm")
    action(pool, "bind_process", process_group_id=99)
    reserve(pool, run="other", budget=4000)
    times = iter([1700000000.0, 1700000000.25])
    monkeypatch.setattr(gpu_pool, "time", SimpleNamespace(time=lambda: next(times)), raising=False)
    snapshot = pool.handle(UID, "snapshot", {})
    assert snapshot["sampling_started_at"] == 1700000000.0
    assert snapshot["sampled_at"] == 1700000000.25
    assert snapshot["devices"][0]["unknown_attribution_commitments"] == 2
    world.sample = CapacitySample((device(), device(B)), {(RequestKey(UID, **key()), A): 2000})
    times = iter([1700000001.0, 1700000001.25])
    measured = pool.handle(UID, "snapshot", {})
    assert measured["devices"][0]["unknown_attribution_commitments"] == 1
    assert measured["devices"][0]["outstanding_memory_mib"] == 6000
