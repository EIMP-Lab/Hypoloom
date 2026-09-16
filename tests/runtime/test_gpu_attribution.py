"""Owned GPU measurements with fake NVML/process metadata; no GPU access."""

import ctypes
from dataclasses import replace
from types import SimpleNamespace

import pytest

from hypoloom.compute_resources import GpuRuntimeObservation
from hypoloom.gpu_attribution import (
    GpuAttributionSampler,
    GpuProcessUsage,
    OwnedMemoryAccumulator,
    ProcessMetadata,
    _NvmlProcessInfo,
    _process_rows,
    read_gpu_processes,
)

GPU_A = "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
GPU_B = "GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
MIB = 1024**2


def metadata():
    # Inner sessions and PID1 reparenting deliberately do not preserve PGID100.
    return {
        1: ProcessMetadata(1, 0, 1, 1, 1000),
        100: ProcessMetadata(100, 1, 100, 10, 1000),
        101: ProcessMetadata(101, 100, 101, 11, 1000),
        102: ProcessMetadata(102, 101, 101, 12, 1000),
        103: ProcessMetadata(103, 102, 103, 13, 1000),
        104: ProcessMetadata(104, 101, 101, 14, 1000),
        200: ProcessMetadata(200, 1, 200, 20, 1000),
        201: ProcessMetadata(201, 200, 201, 21, 1000),
        900: ProcessMetadata(900, 1, 900, 90, 2000),
    }


def sampler(rows, *, records=None, **kwargs):
    records = metadata() if records is None else records
    return GpuAttributionSampler(
        process_provider=lambda uuid: rows,
        metadata_reader=records.__getitem__,
        clock=lambda: 1000.0,
        monotonic=lambda: 1.0,
        **kwargs,
    )


def test_counts_all_owned_descendants_without_pgid_or_foreign_occupancy_substitution():
    rows = [
        GpuProcessUsage(102, 2 * MIB),
        GpuProcessUsage(103, 3 * MIB),
        GpuProcessUsage(104, MIB),
        GpuProcessUsage(201, 4 * MIB),
        GpuProcessUsage(900, 90 * MIB),
    ]
    result = sampler(rows).sample([GPU_A, GPU_B], {"first": 100, "second": 200})
    own = result["first"][GPU_A]
    assert own["complete"] is True and own["reason"] is None
    assert own["resident_bytes"] == 6 * MIB
    assert own["observed_at"] == 1000 and own["duration_seconds"] == 0
    assert {row["pid"] for row in own["processes"]} == {102, 103, 104}
    assert all(row["ancestry"][-1]["pid"] == 100 for row in own["processes"])
    assert result["second"][GPU_A]["resident_bytes"] == 4 * MIB
    assert result["first"][GPU_B]["resident_bytes"] == 6 * MIB
    assert "900" not in str(own)


def test_known_empty_gpu_process_list_differs_from_unknown_root():
    assert (
        sampler([]).sample([GPU_A], {"owner": 100})["owner"][GPU_A]["resident_bytes"]
        == 0
    )
    unknown = sampler([]).sample([GPU_A], {"owner": 999})["owner"][GPU_A]
    assert unknown["complete"] is False and unknown["resident_bytes"] is None


def test_compute_graphics_duplicate_pid_is_counted_once():
    rows = [
        GpuProcessUsage(102, MIB, mode="compute"),
        GpuProcessUsage(102, MIB, mode="graphics"),
    ]
    result = sampler(rows).sample([GPU_A], {"owner": 100})["owner"][GPU_A]
    assert result["complete"] and result["resident_bytes"] == MIB
    assert len(result["processes"]) == 1


@pytest.mark.parametrize(
    "rows,reason",
    [
        ([GpuProcessUsage(102, None)], "memory_unavailable"),
        ([GpuProcessUsage(102, MIB, mode="mps")], "mps_unsupported"),
        ([GpuProcessUsage(102, MIB, gpu_instance_id=1)], "mig_unsupported"),
        (
            [GpuProcessUsage(102, MIB), GpuProcessUsage(102, 2 * MIB, mode="graphics")],
            "duplicate_memory_mismatch",
        ),
    ],
)
def test_ambiguous_or_missing_memory_is_unknown(rows, reason):
    result = sampler(rows).sample([GPU_A], {"owner": 100})["owner"][GPU_A]
    assert result["complete"] is False and result["resident_bytes"] is None
    assert result["processes"] == [] and result["reason"] == reason


def test_provider_permission_failure_never_leaks_exception_paths():
    def forbidden(uuid):
        raise PermissionError("private foreign process /secret/path")

    result = GpuAttributionSampler(process_provider=forbidden).sample(
        [GPU_A], {"owner": 100}
    )
    assert result["owner"][GPU_A]["resident_bytes"] is None
    assert "secret" not in str(result)


@pytest.mark.parametrize("change", ["reuse", "reparent", "disappear"])
def test_rechecks_process_incarnation_and_ancestry_around_nvml_sample(change):
    records = metadata()
    calls = 0

    def provider(uuid):
        nonlocal calls
        calls += 1
        if calls == 2:
            if change == "reuse":
                records[103] = replace(records[103], start_time_ticks=999)
            elif change == "reparent":
                records[103] = replace(records[103], ppid=200)
            else:
                records.pop(103)
        return [GpuProcessUsage(103, MIB)]

    result = GpuAttributionSampler(
        process_provider=provider, metadata_reader=records.__getitem__
    ).sample([GPU_A], {"owner": 100})
    assert result["owner"][GPU_A]["complete"] is False
    assert result["owner"][GPU_A]["resident_bytes"] is None


def test_changing_process_set_is_unknown_but_current_memory_may_change():
    records = metadata()
    rows = iter(([GpuProcessUsage(102, MIB)], [GpuProcessUsage(103, MIB)]))
    subject = GpuAttributionSampler(
        process_provider=lambda uuid: next(rows), metadata_reader=records.__getitem__
    )
    assert (
        subject.sample([GPU_A], {"owner": 100})["owner"][GPU_A]["reason"]
        == "process_set_changed"
    )
    rows = iter(([GpuProcessUsage(102, MIB)], [GpuProcessUsage(102, 2 * MIB)]))
    current = subject.sample([GPU_A], {"owner": 100})["owner"][GPU_A]
    assert current["complete"] is True and current["resident_bytes"] == 2 * MIB


@pytest.mark.parametrize("before,after,expected", [
    ([], [102], 2 * MIB),
    ([102], [102, 103], 5 * MIB),
    ([201], [201, 102], 2 * MIB),
])
def test_startup_growth_can_finish_with_a_stable_owned_capture(before, after, expected):
    snapshots = iter((before, after, after))
    calls = 0

    def provider(uuid):
        nonlocal calls
        calls += 1
        return [GpuProcessUsage(pid, {102: 2, 103: 3, 201: 50}[pid] * MIB)
                for pid in next(snapshots)]

    result = GpuAttributionSampler(
        process_provider=provider, metadata_reader=metadata().__getitem__,
    ).sample([GPU_A], {"owner": 100})["owner"][GPU_A]
    assert result["complete"] is True and result["reason"] is None
    assert result["resident_bytes"] == expected and calls == 3
    assert all(item["ancestry"][-1]["pid"] == 100 for item in result["processes"])
    assert 201 not in {item["pid"] for item in result["processes"]}


@pytest.mark.parametrize("failure,reason", [
    ("disappear", "process_set_reduced"),
    ("grow_again", "process_set_changed"),
    ("metadata_changed", "process_metadata_changed"),
    ("late", "sample_too_slow"),
    ("missing_bytes", "memory_unavailable"),
])
def test_startup_growth_keeps_churn_metadata_bytes_and_deadline_failures_unknown(failure, reason):
    records, calls, now = metadata(), 0, 0.0

    def provider(uuid):
        nonlocal calls, now
        calls += 1
        if calls == 1:
            return []
        if calls == 3:
            if failure == "disappear":
                return []
            if failure == "grow_again":
                return [GpuProcessUsage(102, MIB), GpuProcessUsage(103, MIB)]
            if failure == "metadata_changed":
                records[102] = replace(records[102], ppid=200)
            if failure == "late":
                now = 2.0
            if failure == "missing_bytes":
                return [GpuProcessUsage(102, None)]
        return [GpuProcessUsage(102, MIB)]

    result = GpuAttributionSampler(
        process_provider=provider, metadata_reader=records.__getitem__,
        monotonic=lambda: now,
    ).sample([GPU_A], {"owner": 100})["owner"][GPU_A]
    assert calls == 3
    assert result["complete"] is False and result["reason"] == reason
    assert result["resident_bytes"] is None and result["processes"] == []


def test_root_reuse_and_duplicate_root_mapping_are_unknown():
    records = metadata()
    calls = 0

    def provider(uuid):
        nonlocal calls
        calls += 1
        if calls == 2:
            records[100] = replace(records[100], start_time_ticks=999)
        return []

    result = GpuAttributionSampler(
        process_provider=provider, metadata_reader=records.__getitem__
    ).sample([GPU_A], {"owner": 100})
    assert result["owner"][GPU_A]["resident_bytes"] is None
    result = sampler([]).sample([GPU_A], {"first": 100, "second": 100})
    assert all(
        device[GPU_A]["reason"] == "ambiguous_root" for device in result.values()
    )


def test_excessive_measurement_window_and_proof_size_are_unknown():
    clock = iter((0.0, 2.0))
    subject = GpuAttributionSampler(
        process_provider=lambda uuid: [],
        metadata_reader=metadata().__getitem__,
        monotonic=lambda: next(clock, 2.0),
        max_duration_seconds=1,
    )
    sample = subject.sample([GPU_A], {"owner": 100})["owner"][GPU_A]
    assert sample["duration_seconds"] == 2 and sample["reason"] == "sample_too_slow"
    limited = sampler([GpuProcessUsage(103, MIB)], max_ancestry=2).sample(
        [GPU_A], {"owner": 100}
    )
    assert limited["owner"][GPU_A]["resident_bytes"] is None


def test_accumulator_retains_complete_peak_and_truthful_unknown_latest():
    accumulator = OwnedMemoryAccumulator()
    for amount in (2000, 20000, 2000):
        accumulator.sample(
            sampler([GpuProcessUsage(103, amount * MIB)]).sample(
                [GPU_A], {"owner": 100}
            )["owner"][GPU_A]
        )
    summary = accumulator.summary
    assert summary["sample_count"] == 3 and summary["unknown_sample_count"] == 0
    assert summary["peak"]["resident_bytes"] == 20000 * MIB
    assert summary["latest"]["resident_bytes"] == 2000 * MIB
    accumulator.sample(
        sampler([GpuProcessUsage(103, None)]).sample([GPU_A], {"owner": 100})["owner"][
            GPU_A
        ]
    )
    assert accumulator.summary["unknown_sample_count"] == 1
    assert accumulator.summary["latest"]["resident_bytes"] is None
    assert accumulator.summary["peak"]["resident_bytes"] == 20000 * MIB
    summary["peak"]["resident_bytes"] = 1
    assert accumulator.summary["peak"]["resident_bytes"] == 20000 * MIB


def test_pool_adapter_preserves_owner_keys_floors_bytes_and_rejects_incoherent_credit():
    device = GpuRuntimeObservation(
        uuid=GPU_A,
        total_memory_mib=100,
        free_memory_mib=80,
        used_memory_mib=20,
        utilization_percent=50,
    )
    key = ("opaque", "existing", "key")
    subject = sampler([GpuProcessUsage(103, 2 * MIB + 900)])
    assert subject.pool_residents((device,), {key: 100}) == {(key, GPU_A): 2}
    too_much = sampler([GpuProcessUsage(103, 21 * MIB)])
    assert too_much.pool_residents((device,), {key: 100}) == {(key, GPU_A): None}


@pytest.mark.parametrize(
    "uuids,roots",
    [
        ("bad", {"owner": 100}),
        ([GPU_A, GPU_A], {"owner": 100}),
        ([GPU_A], {"": 100}),
        ([GPU_A], {"owner": True}),
        ([GPU_A], {"owner": 1}),
    ],
)
def test_invalid_authority_inputs_are_rejected_before_any_sampling(uuids, roots):
    def forbidden(uuid):
        raise AssertionError("must validate inputs before provider calls")

    with pytest.raises((TypeError, ValueError)):
        GpuAttributionSampler(process_provider=forbidden).sample(uuids, roots)


class _Function:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


def _count(pointer):
    return ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint))


def test_nvml_v3_layout_resize_retry_and_missing_byte_sentinel():
    assert ctypes.sizeof(_NvmlProcessInfo) == 24
    calls = []

    def query(handle, count, buffer):
        calls.append(_count(count)[0])
        if buffer is None or len(calls) == 2:
            _count(count)[0] = len(calls)
            return 7
        _count(count)[0] = 2
        for index, memory in enumerate((17, 2**64 - 1)):
            buffer[index].pid = 102 + index
            buffer[index].usedGpuMemory = memory
            buffer[index].gpuInstanceId = 2**32 - 1
            buffer[index].computeInstanceId = 2**32 - 1
        return 0

    library = SimpleNamespace(nvmlDeviceGetComputeRunningProcesses_v3=_Function(query))
    rows = _process_rows(library, ctypes.c_void_p(1), "compute")
    assert len(calls) == 3
    assert rows == (GpuProcessUsage(102, 17), GpuProcessUsage(103, None))


def test_nvml_v2_fallback_never_uses_incompatible_v1_struct():
    def empty(handle, count, buffer):
        _count(count)[0] = 0
        return 0

    library = SimpleNamespace(nvmlDeviceGetComputeRunningProcesses_v2=_Function(empty))
    assert _process_rows(library, ctypes.c_void_p(1), "compute") == ()
    library.nvmlDeviceGetComputeRunningProcesses_v3 = _Function(lambda *args: 13)
    assert _process_rows(library, ctypes.c_void_p(1), "compute") == ()
    with pytest.raises(ValueError, match="unsupported"):
        _process_rows(
            SimpleNamespace(nvmlDeviceGetComputeRunningProcesses=_Function(empty)),
            None,
            "compute",
        )


@pytest.mark.parametrize("failure", [4, 7, 3])
def test_nvml_errors_and_perpetual_resize_are_bounded_unknown(failure):
    calls = []

    def failing(handle, count, buffer):
        calls.append(None)
        _count(count)[0] = 1
        return failure

    library = SimpleNamespace(
        nvmlDeviceGetComputeRunningProcesses_v3=_Function(failing)
    )
    with pytest.raises(ValueError):
        _process_rows(library, ctypes.c_void_p(1), "compute")
    assert len(calls) <= 4


def test_nvml_device_lookup_is_uuid_scoped_and_shutdown_occurs_on_query_error(
    monkeypatch,
):
    import hypoloom.gpu_attribution as module

    seen = []

    def lookup(uuid, handle):
        seen.append(uuid)
        ctypes.cast(handle, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.c_void_p(1)
        return 0

    library = SimpleNamespace(
        nvmlInit_v2=_Function(lambda: 0),
        nvmlShutdown=_Function(lambda: seen.append("shutdown") or 0),
        nvmlDeviceGetHandleByUUID=_Function(lookup),
        nvmlDeviceGetComputeRunningProcesses_v3=_Function(lambda *args: 4),
    )
    monkeypatch.setattr(module.ctypes, "CDLL", lambda name: library)
    with pytest.raises(ValueError):
        read_gpu_processes(GPU_A)
    assert seen == [GPU_A.encode("ascii"), "shutdown"]


def test_sampler_bounds_own_proofs_and_keeps_cpu_empty_call_nvml_free():
    subject = sampler([GpuProcessUsage(103, MIB)], max_proof_records=2)
    assert (
        subject.sample([GPU_A], {"owner": 100})["owner"][GPU_A]["reason"]
        == "proof_limit"
    )

    def forbidden(uuid):
        raise AssertionError("CPU call must not inspect NVML")

    assert GpuAttributionSampler(process_provider=forbidden).sample(
        [], {"owner": 100}
    ) == {"owner": {}}
    assert GpuAttributionSampler(process_provider=forbidden).sample([GPU_A], {}) == {}


def test_sampling_budget_stops_further_provider_and_metadata_reads():
    now, calls = [0.0], []
    records = metadata()

    def provider(uuid):
        calls.append("nvml")
        now[0] = 2.0
        return [GpuProcessUsage(103, MIB)]

    def read(pid):
        calls.append(pid)
        return records[pid]

    subject = GpuAttributionSampler(
        process_provider=provider, metadata_reader=read, monotonic=lambda: now[0]
    )
    result = subject.sample([GPU_A], {"owner": 100})["owner"][GPU_A]
    assert result["reason"] == "sample_too_slow" and result["resident_bytes"] is None
    assert calls == [100, "nvml"]


def test_resident_sum_stays_within_exact_json_integer_range():
    rows = [GpuProcessUsage(103, 2**53 - 1), GpuProcessUsage(104, 2**53 - 1)]
    result = sampler(rows).sample([GPU_A], {"owner": 100})["owner"][GPU_A]
    assert result["resident_bytes"] is None and result["reason"] == "memory_sum_invalid"
