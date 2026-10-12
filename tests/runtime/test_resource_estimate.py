from __future__ import annotations

import copy
import json
from dataclasses import FrozenInstanceError

import pytest

from hypoloom.domain import ResourceEstimate
from hypoloom.resource_experience import (
    resolve_workload_memory_mib,
    summarize_resource_measurements,
)


MIB = 1024 * 1024
WORKLOAD = {"model": "small-v1", "precision": "bf16", "batch": "8"}


def estimate(**changes):
    return ResourceEstimate.from_dict({"workload": WORKLOAD, "peak_memory_mib": 8000,
        "basis": "Predicted per-device peak including headroom", **changes})


def attempt(attempt_id="process-1", *, peak_bytes=9000 * MIB, **changes):
    return {"attempt_id": attempt_id, "kind": "PROCESS", "state": "CLOSED",
        "workload": dict(WORKLOAD), "admitted_workload_memory_mib": 8000,
        "oom": False, "terminal_status": "SUCCEEDED", "ended_at": 1,
        "resource_usage": [{"gpu_uuid": "private-gpu", "owned_memory": {
            "sample_count": 3, "unknown_sample_count": 0,
            "latest": {"complete": True, "resident_bytes": 2 * MIB},
            "peak": {"complete": True, "resident_bytes": peak_bytes,
                "processes": [{"pid": 123, "ancestry": [{"pid": 123}]}]}}}], **changes}


def test_estimate_is_canonical_immutable_and_omission_defaults_to_no_sources():
    raw = {"workload": dict(WORKLOAD), "peak_memory_mib": 8000, "basis": "prediction"}
    value = ResourceEstimate.from_dict(raw)
    raw["workload"]["model"] = "changed"
    assert value.workload["model"] == "small-v1"
    assert list(value.to_dict()["workload"]) == sorted(WORKLOAD)
    assert value.measurement_attempt_ids == ()
    assert value.to_dict()["measurement_attempt_ids"] == []
    assert ResourceEstimate.from_dict(value.to_dict()) == value
    with pytest.raises(TypeError):
        value.workload["model"] = "changed"
    with pytest.raises(FrozenInstanceError):
        value.peak_memory_mib = 1
    copied = value.to_dict()
    copied["workload"]["model"] = "changed"
    assert value.workload["model"] == "small-v1"
    assert estimate(measurement_attempt_ids=["b", "a"]).measurement_attempt_ids == ("a", "b")


@pytest.mark.parametrize("changes", [
    {"unknown": 1}, {"workload": {}}, {"workload": []},
    {"workload": {1: "x"}}, {"workload": {"model": 3}},
    {"workload": {"model": {"nested": "value"}}},
    {"workload": {" model": "x"}}, {"workload": {"model": "x\nsecret"}},
    {"workload": {"x" * 65: "x"}}, {"workload": {"model": "x" * 257}},
    {"workload": {str(i): "x" for i in range(17)}},
    {"peak_memory_mib": True}, {"peak_memory_mib": 0}, {"peak_memory_mib": -1},
    {"peak_memory_mib": 1.5}, {"peak_memory_mib": 2**53},
    {"basis": ""}, {"basis": "x" * 1025}, {"basis": " x"},
    {"measurement_attempt_ids": "a"}, {"measurement_attempt_ids": ["a", "a"]},
    {"measurement_attempt_ids": ["x" * 257]}, {"measurement_attempt_ids": [True]},
    {"measurement_attempt_ids": [str(i) for i in range(33)]},
])
def test_estimate_rejects_malformed_or_unbounded_fields(changes):
    with pytest.raises(ValueError):
        estimate(**changes)


@pytest.mark.parametrize("missing", ["workload", "peak_memory_mib", "basis"])
def test_estimate_requires_the_three_prediction_fields(missing):
    raw = estimate().to_dict()
    del raw[missing]
    with pytest.raises(ValueError):
        ResourceEstimate.from_dict(raw)


def test_measurements_keep_only_bounded_owned_evidence_and_do_not_mutate_sources():
    raw = attempt()
    raw.update(command="private command", environment={"SECRET": "secret"})
    original = copy.deepcopy(raw)
    projected = summarize_resource_measurements([raw])
    assert raw == original
    assert projected == [{"attempt_id": "process-1", "workload": dict(sorted(WORKLOAD.items())),
        "admitted_workload_memory_mib": 8000, "oom": False,
        "terminal_status": "SUCCEEDED", "ended_at": 1.0,
        "sample_peak_bytes": 9000 * MIB, "observed_peak_lower_bound_bytes": 9000 * MIB,
        "observed_over_budget": True, "pressure_exceeded": False,
        "measurement_status": "complete"}]
    encoded = json.dumps(projected)
    for forbidden in ("private", "SECRET", "processes", "ancestry", "gpu_uuid", '"pid"'):
        assert forbidden not in encoded
    projected[0]["workload"]["model"] = "changed"
    assert raw == original


def test_partial_pressure_history_identifies_underestimation_without_calibrating():
    raw = attempt(terminal_status="FAILED")
    raw['resource_usage'][0]['owned_memory']['unknown_sample_count'] = 1
    raw['resource_usage'][0]['pressure_exceeded'] = True
    history = summarize_resource_measurements([raw])
    assert history[0]['sample_peak_bytes'] is None
    assert history[0]['observed_over_budget'] is True
    assert history[0]['pressure_exceeded'] is True
    assert resolve_workload_memory_mib(8000, estimate(measurement_attempt_ids=['process-1']), history,
        initial_memory_mib=16000, adaptive=True) == 16000
    raw['resource_usage'][0]['owned_memory']['peak']['complete'] = False
    assert summarize_resource_measurements([raw])[0]['observed_over_budget'] is None


def test_peak_is_maximum_per_device_not_sum_or_latest_or_device_pressure():
    raw = attempt(peak_bytes=9000 * MIB + 1)
    raw["resource_usage"].append(copy.deepcopy(attempt(peak_bytes=10000 * MIB)["resource_usage"][0]))
    raw["resource_usage"][0].update(maximum_used_memory_mib=90000, minimum_free_memory_mib=1)
    measurements = summarize_resource_measurements([raw])
    assert measurements[0]["sample_peak_bytes"] == 10000 * MIB
    assert resolve_workload_memory_mib(8000, estimate(measurement_attempt_ids=["process-1"]), measurements) == 12544


@pytest.mark.parametrize("peak_bytes,expected", [(0, 1), (MIB, 64), (MIB + 1, 64),
    (256 * MIB, 320), (256 * MIB + 1, 384), (9000 * MIB + 1, 11264)])
def test_headroom_rounds_bytes_up_to_mib_then_25_percent_then_64mib(peak_bytes, expected):
    measured = summarize_resource_measurements([attempt(peak_bytes=peak_bytes)])
    assert resolve_workload_memory_mib(1, estimate(peak_memory_mib=1,
        measurement_attempt_ids=["process-1"]), measured) == expected


@pytest.mark.parametrize("mutate", [
    lambda owned: owned.update(sample_count=0),
    lambda owned: owned.update(sample_count=True),
    lambda owned: owned.update(unknown_sample_count=1),
    lambda owned: owned.update(unknown_sample_count=True),
    lambda owned: owned.update(peak=None),
    lambda owned: owned["peak"].update(complete=False),
    lambda owned: owned["peak"].update(resident_bytes=None),
    lambda owned: owned["peak"].update(resident_bytes=True),
    lambda owned: owned["peak"].update(resident_bytes=-1),
])
def test_unknown_or_partial_owned_samples_never_invent_a_peak(mutate):
    raw = attempt()
    mutate(raw["resource_usage"][0]["owned_memory"])
    projected = summarize_resource_measurements([raw])
    assert projected[0]["sample_peak_bytes"] is None
    assert projected[0]["measurement_status"] == "unknown"
    assert resolve_workload_memory_mib(8000, estimate(measurement_attempt_ids=["process-1"]), projected) == 8000


def test_one_unknown_device_invalidates_plural_attempt_learning():
    raw = attempt()
    raw["resource_usage"].append({"used_memory_mib": 40000})
    assert summarize_resource_measurements([raw])[0]["sample_peak_bytes"] is None


def test_partial_history_exposes_only_a_lower_bound_without_calibrating_or_releasing_initial_floor():
    raw = attempt(peak_bytes=16000 * MIB)
    raw["resource_usage"][0]["owned_memory"]["unknown_sample_count"] = 1
    raw["resource_usage"].append({"used_memory_mib": 40000})
    measured = summarize_resource_measurements([raw])
    assert measured[0]["observed_peak_lower_bound_bytes"] == 16000 * MIB
    assert measured[0]["sample_peak_bytes"] is None
    assert measured[0]["measurement_status"] == "unknown"
    from hypoloom.resource_experience import explain_workload_memory
    budget = explain_workload_memory(1024, estimate(peak_memory_mib=2200,
        measurement_attempt_ids=["process-1"]), measured, initial_memory_mib=8192, adaptive=True)
    assert budget["resolved_workload_memory_mib"] == 8192
    assert budget["initial_floor_active"] is True
    assert budget["calibrated_measurements"] == 0
    assert budget["sharing"] == "exclusive"


def test_oom_raises_prior_budget_but_pressure_alone_does_not():
    raw = attempt(resource_usage=[{"pressure_exceeded": True, "used_memory_mib": 40000}],
        terminal_status="FAILED")
    value = estimate(measurement_attempt_ids=["process-1"])
    assert resolve_workload_memory_mib(8000, value, summarize_resource_measurements([raw])) == 8000
    raw["oom"] = True
    assert resolve_workload_memory_mib(8000, value, summarize_resource_measurements([raw])) == 10048
    raw["oom"] = "true"
    assert summarize_resource_measurements([raw])[0]["oom"] is False


def test_floor_and_submitted_estimate_never_shrink_and_omission_means_unestimated():
    measurements = summarize_resource_measurements([attempt()])
    assert resolve_workload_memory_mib(8192, None, measurements) == 8192
    assert resolve_workload_memory_mib(8192, estimate(peak_memory_mib=9000), measurements) == 9000
    assert resolve_workload_memory_mib(16000, estimate(measurement_attempt_ids=["process-1"]), measurements) == 16000


@pytest.mark.parametrize("floor", [0, -1, True, 1.5, 2**53])
def test_budget_rejects_invalid_profile_floor(floor):
    with pytest.raises(ValueError):
        resolve_workload_memory_mib(floor, None)


def test_budget_uses_exact_references_and_rejects_missing_duplicate_or_different_workload():
    value = estimate(measurement_attempt_ids=["process-1"])
    measurements = summarize_resource_measurements([attempt()])
    newer = summarize_resource_measurements([attempt("newer", peak_bytes=99000 * MIB)])
    assert resolve_workload_memory_mib(8000, value, measurements + newer) == 11264
    for invalid in ([], measurements * 2, [{**measurements[0], "workload": {"model": "different"}}]):
        with pytest.raises(ValueError):
            resolve_workload_memory_mib(8000, value, invalid)


def test_recent_projection_is_bounded_deterministic_and_ignores_nonprocess_sources():
    records = [attempt(f"p-{i:02d}", ended_at=i) for i in range(40)]
    records.extend([attempt("agent", kind="AGENT"), attempt("open", state="OPEN"),
        attempt("bad", workload={"model": 1}), attempt("")])
    projected = summarize_resource_measurements(records)
    assert len(projected) == 32
    assert projected[0]["attempt_id"] == "p-39"
    assert summarize_resource_measurements(list(reversed(records))) == projected
    assert summarize_resource_measurements(records, limit=1000) == projected
    assert len(summarize_resource_measurements(records, limit=2)) == 2
    for invalid in (0, -1, True):
        with pytest.raises(ValueError):
            summarize_resource_measurements(records, limit=invalid)


@pytest.mark.parametrize("raw", [None, [], "not a record", attempt(terminal_status=[]),
    attempt(terminal_status={}), attempt(terminal_status="UNKNOWN"), attempt(attempt_id=None)])
def test_malformed_measurement_records_are_ignored(raw):
    assert summarize_resource_measurements([raw]) == []


def test_unknown_sample_peak_cannot_be_promoted_and_oom_requires_a_real_prior_budget():
    raw = attempt()
    raw["resource_usage"][0]["owned_memory"].update(unknown_sample_count=1)
    measured = summarize_resource_measurements([raw])
    measured[0]["sample_peak_bytes"] = 99999 * MIB
    value = estimate(measurement_attempt_ids=["process-1"])
    assert resolve_workload_memory_mib(8000, value, measured) == 8000
    for admitted in (None, 0, True, "8000"):
        with pytest.raises(ValueError, match="prior admitted"):
            resolve_workload_memory_mib(8000, value, [{**measured[0], "oom": True,
                "admitted_workload_memory_mib": admitted}])


def test_estimate_direct_constructor_enforces_tuple_sources_and_resolved_budget_stays_json_safe():
    with pytest.raises(ValueError):
        ResourceEstimate(workload=WORKLOAD, peak_memory_mib=8000, basis="prediction",
            measurement_attempt_ids=["a"])
    value = estimate(measurement_attempt_ids=["process-1"])
    measured = summarize_resource_measurements([attempt(oom=True,
        admitted_workload_memory_mib=9_007_199_254_740_991)])
    with pytest.raises(ValueError, match="JSON-safe"):
        resolve_workload_memory_mib(8000, value, measured)


@pytest.mark.parametrize("status,complete,expected", [
    ("SUCCEEDED", True, 2560), ("FAILED", True, 8192),
    ("TIMED_OUT", True, 8192), ("CANCELLED", True, 8192),
    ("SUCCEEDED", False, 8192),
])
def test_initial_memory_can_decrease_only_after_complete_success(status, complete, expected):
    raw = attempt(peak_bytes=2000 * MIB, terminal_status=status)
    if not complete:
        raw["resource_usage"][0]["owned_memory"]["unknown_sample_count"] = 1
    measured = summarize_resource_measurements([raw])
    value = estimate(peak_memory_mib=2200, measurement_attempt_ids=["process-1"])
    assert resolve_workload_memory_mib(1024, value, measured,
        initial_memory_mib=8192) == expected


def test_initial_memory_keeps_unmeasured_predictions_conservative_and_preserves_oom_floor():
    assert resolve_workload_memory_mib(1024, None, initial_memory_mib=8192) == 8192
    assert resolve_workload_memory_mib(1024, estimate(peak_memory_mib=2200),
        initial_memory_mib=8192) == 8192
    assert resolve_workload_memory_mib(1024, estimate(peak_memory_mib=12000),
        initial_memory_mib=8192) == 12000
    records = summarize_resource_measurements([
        attempt("success", peak_bytes=2000 * MIB),
        attempt("oom", peak_bytes=3000 * MIB, terminal_status="FAILED", oom=True,
            admitted_workload_memory_mib=8192),
    ])
    value = estimate(peak_memory_mib=2200, measurement_attempt_ids=["success", "oom"])
    assert resolve_workload_memory_mib(1024, value, records, initial_memory_mib=8192) == 10240
    assert resolve_workload_memory_mib(8192, value, records, initial_memory_mib=8192) == 10240


@pytest.mark.parametrize("initial", [0, -1, True, 1.5, "8192", 1023, 2**53])
def test_initial_memory_must_be_a_json_safe_integer_at_least_the_floor(initial):
    with pytest.raises(ValueError, match="initial_memory_mib"):
        resolve_workload_memory_mib(1024, None, initial_memory_mib=initial)
