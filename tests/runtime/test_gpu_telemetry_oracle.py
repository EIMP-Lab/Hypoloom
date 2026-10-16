"""Producer-to-Oracle coverage for optional device samples; no GPU I/O."""

from dataclasses import replace

import pytest

from hypoloom.compute_resources import GpuPressureTracker, GpuRuntimeObservation
from hypoloom.oracle import _audit_process_resource_observation


GPU_A = "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
GPU_B = "GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
PERFORMANCE = {
    "power_draw_mw": 210000,
    "power_limit_mw": 350000,
    "temperature_c": 65,
    "sm_clock_mhz": 1695,
}


def tracker(uuid=GPU_A, **performance):
    sample = GpuRuntimeObservation(
        uuid=uuid, total_memory_mib=24576, free_memory_mib=10000,
        used_memory_mib=14576, utilization_percent=70, **performance,
    )
    return GpuPressureTracker(
        initial_observation=sample, reserve_memory_mib=512, grace_seconds=2,
    ), sample


def audit(usage, **resource_changes):
    issues = []
    _audit_process_resource_observation(
        {"terminal_reason": "SUCCEEDED", "resource_usage": usage},
        {"expected_kind": "GPU", "gpu_uuid": GPU_A,
         "reserve_memory_mib": 512, "telemetry_required": True, **resource_changes},
        "telemetry-attempt", issues,
    )
    return issues


@pytest.mark.parametrize("performance", [
    {}, PERFORMANCE, {name: 0 for name in PERFORMANCE},
    *({name: value} for name, value in PERFORMANCE.items()),
])
def test_oracle_accepts_available_optional_samples_from_actual_tracker(performance):
    subject, _ = tracker(**performance)
    assert audit(subject.summary.to_dict()) == []


def test_later_unavailable_device_samples_remain_absent_and_auditable():
    subject, sample = tracker(**PERFORMANCE)
    assert audit(subject.summary.to_dict()) == []
    subject.sample(replace(sample, **{name: None for name in PERFORMANCE}))
    summary = subject.summary.to_dict()
    assert not any(f"gpu_latest_{name}" in summary for name in PERFORMANCE)
    assert audit(summary) == []


@pytest.mark.parametrize("field", PERFORMANCE)
@pytest.mark.parametrize("value", [-1, True, 1.5, "65", None, float("nan")])
def test_optional_sample_presence_requires_a_nonnegative_integer(field, value):
    subject, _ = tracker(**PERFORMANCE)
    usage = subject.summary.to_dict()
    usage[f"gpu_latest_{field}"] = value
    assert {issue.code for issue in audit(usage)} == {
        "process_resource_observation_mismatch"
    }


@pytest.mark.parametrize("change", [
    "unknown", "missing_memory", "wrong_gpu", "memory_total", "no_samples",
    "pressure_without_cause", "missing_ownership",
])
def test_optional_telemetry_preserves_resource_and_field_checks(change):
    subject, _ = tracker(**PERFORMANCE)
    usage = subject.summary.to_dict()
    resource_changes = {}
    if change == "unknown": usage["gpu_latest_fan_percent"] = 50
    elif change == "missing_memory": del usage["gpu_minimum_free_memory_mib"]
    elif change == "wrong_gpu": usage["gpu_uuid"] = GPU_B
    elif change == "memory_total": usage["gpu_latest_used_memory_mib"] += 1
    elif change == "no_samples": usage["sample_count"] = 0
    elif change == "pressure_without_cause": usage.update(pressure_exceeded=True, pressure_attribution="self")
    else: resource_changes["attributable"] = True
    assert {issue.code for issue in audit(usage, **resource_changes)} == {
        "process_resource_observation_mismatch"
    }


@pytest.mark.parametrize("change", [None, "invalid_second_device", "wrong_order"])
def test_multigpu_optional_samples_are_validated_per_granted_device(change):
    first, _ = tracker(**PERFORMANCE)
    second, _ = tracker(GPU_B, power_draw_mw=0)
    usage = [first.summary.to_dict(), second.summary.to_dict()]
    if change == "invalid_second_device": usage[1]["gpu_latest_temperature_c"] = False
    elif change == "wrong_order": usage.reverse()
    issues = audit(usage, gpu_uuids=(GPU_A, GPU_B))
    if change is None:
        assert issues == []
    else:
        assert {issue.code for issue in issues} == {"process_resource_observation_mismatch"}
