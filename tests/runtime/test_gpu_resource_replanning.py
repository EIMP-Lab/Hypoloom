"""Admission policy regressions; all devices and memory observations are synthetic."""
from dataclasses import replace

import pytest

from hypoloom.domain import ResourceEstimate
from hypoloom.gpu_pool import CapacitySample, assess_capacity
from hypoloom.oracle import audit_gpu_pool
from hypoloom.resource_experience import explain_workload_memory, summarize_resource_measurements
from test_resource_estimate import attempt, estimate, MIB
from test_gpu_pool import A, B, UID, World, action, device, request, rows


def plan(*records, initial=8000):
    measured = summarize_resource_measurements(records)
    return explain_workload_memory(1000, estimate(peak_memory_mib=1000,
        measurement_attempt_ids=[r["attempt_id"] for r in measured]), measured,
        initial_memory_mib=initial, adaptive=True)


def test_margins_follow_completed_evidence_and_oom_growth_stays_independent():
    failed = attempt("failed", peak_bytes=16198*MIB, terminal_status="FAILED", ended_at=1)
    unknown = plan(failed)
    assert unknown["resolved_workload_memory_mib"] == 20288
    assert unknown["headroom_percent"] == 25 and unknown["sharing"] == "exclusive"
    success = attempt("success", peak_bytes=16198*MIB, ended_at=2)
    measured = plan(failed, success)
    assert measured["resolved_workload_memory_mib"] == 18688
    assert measured["headroom_percent"] == 15 and measured["sharing"] == "shared"
    stable = plan(failed, success, attempt("s2", peak_bytes=16000*MIB, ended_at=3),
                  attempt("s3", peak_bytes=16200*MIB, ended_at=4))
    assert stable["headroom_percent"] == 10
    assert stable["resolved_workload_memory_mib"] == 17856
    # Reducing the successful measurement margin must not reduce an OOM bound.
    oom = {**failed, "oom": True, "admitted_workload_memory_mib": 20000}
    assert plan(oom, success)["resolved_workload_memory_mib"] == 25024


def test_late_failure_unknown_coverage_and_incomparable_history_do_not_calibrate():
    success = attempt("ok", peak_bytes=1000*MIB, ended_at=1)
    failed = attempt("late", peak_bytes=1100*MIB, terminal_status="FAILED", ended_at=2)
    assert plan(success, failed)["sharing"] == "exclusive"
    assert plan(success, failed)["resolved_workload_memory_mib"] == 8000
    incomplete = attempt("partial", ended_at=3)
    incomplete["resource_usage"][0]["owned_memory"]["unknown_sample_count"] = 1
    assert plan(success, incomplete)["headroom_percent"] == 25
    assert plan(success, {**failed, "ended_at": None})["headroom_percent"] == 25
    assert plan(attempt("large-failed", peak_bytes=12000*MIB, terminal_status="FAILED", ended_at=1),
                attempt("small-ok", peak_bytes=1000*MIB, ended_at=2))["sharing"] == "exclusive"
    small = plan(success)
    assert small["resolved_workload_memory_mib"] == 1536  # 512 MiB minimum, aligned.
    varied = plan(success, attempt("larger", peak_bytes=2000*MIB, ended_at=2),
                  attempt("third", peak_bytes=1800*MIB, ended_at=3))
    assert varied["headroom_percent"] == 15


def _reserve(pool, *, sharing="exclusive", **kw):
    return pool.handle(UID, "reserve", {**request(**kw), "sharing": sharing})


def test_idle_card_impossible_head_does_not_block_feasible_work(tmp_path):
    world = World()
    world.sample = CapacitySample((replace(device(A, free=24120, total=24576),
        system_reserved_memory_mib=441), device(B)))
    pool = world.pool(tmp_path)
    first = _reserve(pool, budget=20288, reserve=4096)
    assert first["state"] == "QUEUED" and first["reason"] == "capacity_requires_revision"
    assert _reserve(pool, run="later", budget=18688, reserve=4096)["state"] == "RESERVED"
    assert action(pool, "release")["state"] == "RELEASED"
    assert _reserve(pool, budget=20288, reserve=4096)["state"] == "RELEASED"
    assert audit_gpu_pool(pool.root)["ok"]


def test_exclusive_is_atomic_and_shared_requests_respect_both_directions(tmp_path):
    world = World(); pool = world.pool(tmp_path)
    assert _reserve(pool, budget=2000)["state"] == "RESERVED"
    other = _reserve(pool, sharing="shared", run="other", budget=2000)
    assert other["state"] == "QUEUED" and other["reason"] == "exclusive_wait"
    action(pool, "release")
    assert action(pool, "status", run="other")["state"] == "RESERVED"
    assert _reserve(pool, run="third", budget=2000)["state"] == "QUEUED"
    # Shared work may still overlap shared work when budget fits; another UUID
    # avoids the fairly waiting exclusive request's device set.
    action(pool, "release", run="third")
    assert _reserve(pool, sharing="shared", run="fourth", budget=2000)["state"] == "RESERVED"
    assert audit_gpu_pool(pool.root)["ok"]


def test_busy_or_unknown_capacity_is_not_structural_failure_and_multigpu_is_counted(tmp_path):
    world = World(); pool = world.pool(tmp_path)
    world.sample = CapacitySample((device(A, free=1000), device(B, free=9000)))
    response = _reserve(pool, allowed=(A, B), count=2, budget=4000)
    assert response["state"] == "QUEUED" and response["reason"] == "capacity_unknown"
    world.sample = CapacitySample((device(A, free=9000), device(B, free=9000)))
    assert action(pool, "status")["gpu_uuids"] == [A, B]
    assert audit_gpu_pool(pool.root)["ok"]


def test_shared_requests_prefer_empty_devices(tmp_path):
    world = World(); pool = world.pool(tmp_path)
    assert _reserve(pool, sharing="shared", budget=1000)["gpu_uuids"] == [A]
    assert _reserve(pool, sharing="shared", run="other", budget=1000, allowed=(A,B))["gpu_uuids"] == [B]


def test_other_requests_reserve_is_temporary_not_an_impossible_capacity(tmp_path):
    pool = World().pool(tmp_path)
    assert _reserve(pool, sharing="shared", budget=1000, reserve=8000)["state"] == "RESERVED"
    waiting = _reserve(pool, sharing="shared", run="other", budget=7000, reserve=1000)
    assert waiting["state"] == "QUEUED" and waiting["reason"] == "capacity_unknown"
    action(pool, "release")
    assert action(pool, "status", run="other")["state"] == "RESERVED"


def test_independent_pool_connections_cannot_both_admit_exclusive_work(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    world = World()
    pools = [world.pool(tmp_path), world.pool(tmp_path)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda i: _reserve(pools[i], run=f"run{i}", budget=1000), range(2)))
    assert sorted(r["state"] for r in results) == ["QUEUED", "RESERVED"]
    assert audit_gpu_pool(pools[0].root)["ok"]


@pytest.mark.parametrize("extended", [True, False])
def test_nvml_reports_driver_reserve_only_when_backend_provides_it(monkeypatch, extended):
    from hypoloom import compute_resources as cr
    monkeypatch.setattr(cr.ctypes, "CDLL", lambda _: object())
    def memory(handle, pointer):
        m = pointer._obj
        m.total, m.free, m.used = 24576*MIB, 24120*MIB, 456*MIB
        return 0
    def memory_v2(handle, pointer):
        m = pointer._obj
        assert m.version == cr.ctypes.sizeof(cr._NvmlMemoryV2) | (2 << 24)
        memory(handle, pointer)
        m.reserved = 441*MIB
        return 0
    def lookup(library, names, args, restype):
        name = names[0]
        if name == "nvmlDeviceGetMemoryInfo": return memory
        if name == "nvmlDeviceGetMemoryInfo_v2" and extended: return memory_v2
        if name in {"nvmlDeviceGetMemoryInfo_v2", "nvmlDeviceGetUtilizationRates"}:
            raise cr.ResourceUnavailable("unsupported")
        return lambda *args: 0
    monkeypatch.setattr(cr, "_nvml_function", lookup)
    result = cr._nvml_runtime_observation(A)
    assert result.system_reserved_memory_mib == (441 if extended else None)
    assert result.total_memory_mib == result.free_memory_mib + result.used_memory_mib
