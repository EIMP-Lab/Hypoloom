from dataclasses import replace
from types import SimpleNamespace

import pytest

from hypoloom.compute_resources import GpuPressureTracker, GpuRuntimeObservation, ResourceUnavailable
from hypoloom.monitor import _gpu_usage
from hypoloom.oracle import _audit_process_resource_observation
from hypoloom.process_observation import sample_gpu_pressure


GPU = "GPU-00000000-0000-0000-0000-000000000001"


def tracker(ticks, **metrics):
    observation = GpuRuntimeObservation(uuid=GPU, total_memory_mib=128, used_memory_mib=32,
                                       free_memory_mib=96, **metrics)
    return GpuPressureTracker(initial_observation=observation, reserve_memory_mib=16, grace_seconds=2,
                              now=iter(ticks).__next__), observation


def audit(usage, devices=(GPU,)):
    issues = []
    _audit_process_resource_observation({"resource_usage": usage},
        {"expected_kind": "GPU", "gpu_uuid": devices[0], "gpu_uuids": devices if len(devices) > 1 else ()},
        "attempt", issues)
    return issues


def test_irregular_polls_and_single_sample_legacy_display():
    device, initial = tracker([10, 11, 15, 15], utilization_percent=0, power_draw_mw=10000)
    first = device.summary.to_dict()
    assert first["gpu_activity"]["mean_power_mw"] is None and not audit(first)
    legacy = {key: val for key, val in first.items() if key != "gpu_activity"}
    assert not audit(legacy) and "gpu_activity" not in _gpu_usage(legacy)[0]
    device.sample(replace(initial, utilization_percent=100, power_draw_mw=30000))
    device.sample(replace(initial, utilization_percent=50, power_draw_mw=None))
    usage = device.sample(replace(initial, utilization_percent=10, power_draw_mw=0)).to_dict()
    assert usage["gpu_activity"] == {"scope": "whole_device", "sample_span_seconds": 5,
        "max_poll_gap_seconds": 4, "unavailable_samples": 0, "utilization_covered_seconds": 5,
        "mean_utilization_percent": 70, "power_covered_seconds": 1, "mean_power_mw": 20000}
    other = {**usage, "gpu_uuid": GPU[:-1] + "2"}
    assert not audit([usage, other], (GPU, other["gpu_uuid"]))
    assert _gpu_usage([other, usage])[0]["gpu_activity"] == usage["gpu_activity"]


def test_unavailable_probe_breaks_both_intervals_and_preserves_measured_zero():
    device, initial = tracker([0, 2, 4, 9, 11], utilization_percent=0, power_draw_mw=0)
    device.sample(initial)
    def unavailable(uuid):
        raise ResourceUnavailable("NVML unavailable")
    host = SimpleNamespace(resource_controls=SimpleNamespace(compute=lambda run: SimpleNamespace(observe_runtime=unavailable)),
                           shared_gpu=None)
    sample_gpu_pressure(host, {"run_id": "r"}, "a", None, ((device,), 16, {}))
    device.sample(replace(initial, power_draw_mw=4000, utilization_percent=None))
    usage = device.sample(replace(initial, power_draw_mw=4000)).to_dict()
    activity = usage["gpu_activity"]
    assert usage["sample_count"] == 4 and activity["unavailable_samples"] == 1 and not audit(usage)
    assert activity["power_covered_seconds"] == 4 and activity["mean_power_mw"] == 2000
    assert activity["utilization_covered_seconds"] == 2 and activity["mean_utilization_percent"] == 0
    assert activity["sample_span_seconds"] == 11 and activity["max_poll_gap_seconds"] == 5


@pytest.mark.parametrize("invalid", [{"scope":"process"}, {"mean_power_mw":float("nan")},
    {"mean_utilization_percent":101}, {"power_covered_seconds":0}, {"power_covered_seconds":5},
    {"max_poll_gap_seconds":float("inf")}, {"unavailable_samples":True}])
def test_oracle_rejects_invalid_activity_and_monitor_omits_it(invalid):
    device, initial = tracker([0, 2], utilization_percent=0, power_draw_mw=0)
    usage = device.sample(initial).to_dict()
    usage["gpu_activity"].update(invalid)
    assert audit(usage)
    assert "gpu_activity" not in _gpu_usage(usage)[0]
