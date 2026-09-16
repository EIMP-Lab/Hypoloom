from __future__ import annotations

import copy
import json

import pytest

from hypoloom.resource_experience import (
    MAX_RESOURCE_EXPERIENCE_ATTEMPTS,
    RESOURCE_EXPERIENCE_SCHEMA_VERSION,
    RESOURCE_EXPERIENCE_VERSION,
    summarize_resource_experience,
)


def _attempt(
    role: str,
    *,
    component_id: str = "component-a",
    method_family: str = "transformer",
    preference: str = "AUTO",
    grant: str = "GPU",
    status: str = "SUCCEEDED",
    ended_at: float | None = 1.0,
    usage: dict[str, object] | None = None,
) -> dict[str, object]:
    attempt: dict[str, object] = {
        "kind": "PROCESS",
        "state": "CLOSED",
        "role": role,
        "component_id": component_id,
        "method_family": method_family,
        "resource_preference": preference,
        "resource_grant": {
            "kind": grant,
            "gpu_uuid": "GPU-secret-device-identity",
            "device_nodes": ["/dev/nvidia0"],
        },
        "terminal_status": status,
    }
    if ended_at is not None:
        attempt["ended_at"] = ended_at
    if usage is not None:
        attempt["resource_usage"] = usage
    return attempt


def test_summary_aggregates_only_advisory_device_level_experience() -> None:
    attempts = [
        {
            **_attempt(
                "critic",
                ended_at=10.0,
                usage={
                    "minimum_free_memory_mib": 8_000,
                    "maximum_used_memory_mib": 16_000,
                    "pressure_exceeded": False,
                    "pid": 1234,
                    "user": "private-user",
                    "secret": "top-secret-token",
                },
            ),
            "pid": 9999,
            "cwd": "/private/worktree",
            "api_key": "top-secret-key",
        },
        {
            **_attempt("critic", status="FAILED", ended_at=11.0),
            "observation": {
                "resource_usage": {
                    "minimum_free_memory_mib": 2_000,
                    "maximum_used_memory_mib": 22_000,
                    "pressure_exceeded": True,
                    "process_owner": "private-user",
                    "runtime_path": "/private/runtime",
                },
                "stderr": "top-secret-stderr",
            },
        },
        _attempt(
            "leader",
            component_id="component-b",
            method_family="linear",
            preference="CPU",
            grant="CPU",
            ended_at=12.0,
            usage={"used_memory_mib": 512, "free_memory_mib": 1_024},
        ),
        {**_attempt("ignored-agent"), "kind": "AGENT"},
        {**_attempt("ignored-open"), "state": "OPEN"},
    ]
    original = copy.deepcopy(attempts)

    summary = summarize_resource_experience(attempts)

    assert attempts == original
    assert summary == {
        "schema_version": RESOURCE_EXPERIENCE_SCHEMA_VERSION,
        "resource_experience_version": RESOURCE_EXPERIENCE_VERSION,
        "groups": [
            {
                "role": "critic",
                "component_id": "component-a",
                "method_family": "transformer",
                "resource_preference": "AUTO",
                "attempt_count": 2,
                "successful_attempt_count": 1,
                "failed_attempt_count": 1,
                "cpu_grant_count": 0,
                "gpu_grant_count": 2,
                "minimum_observed_free_memory_mib": 2_000,
                "maximum_observed_used_memory_mib": 22_000,
                "pressure_termination_count": 1,
            },
            {
                "role": "leader",
                "component_id": "component-b",
                "method_family": "linear",
                "resource_preference": "CPU",
                "attempt_count": 1,
                "successful_attempt_count": 1,
                "failed_attempt_count": 0,
                "cpu_grant_count": 1,
                "gpu_grant_count": 0,
                "minimum_observed_free_memory_mib": 1_024,
                "maximum_observed_used_memory_mib": 512,
                "pressure_termination_count": 0,
            },
        ],
        "source_attempt_count": 3,
        "truncated": False,
    }
    encoded = json.dumps(summary, allow_nan=False, sort_keys=True)
    for forbidden in (
        "pid",
        "private-user",
        "top-secret",
        "/private",
        "gpu_uuid",
        "GPU-secret-device-identity",
        "device_nodes",
        "resource_grant",
    ):
        assert forbidden not in encoded


def test_recent_selection_is_bounded_then_groups_are_sorted_deterministically() -> None:
    attempts = [
        _attempt("delta", ended_at=1.0),
        _attempt("alpha", ended_at=4.0),
        _attempt("charlie", ended_at=3.0),
        _attempt("bravo", ended_at=2.0),
    ]

    summary = summarize_resource_experience(attempts, limit=2)
    reversed_summary = summarize_resource_experience(list(reversed(attempts)), limit=2)

    assert summary == reversed_summary
    assert [group["role"] for group in summary["groups"]] == ["alpha", "charlie"]
    assert summary["source_attempt_count"] == 4
    assert summary["truncated"] is True


def test_missing_end_time_uses_input_order_as_recency_fallback() -> None:
    attempts = [
        _attempt("oldest", ended_at=None),
        _attempt("middle", ended_at=None),
        _attempt("newest", ended_at=None),
    ]

    summary = summarize_resource_experience(attempts, limit=2)

    assert [group["role"] for group in summary["groups"]] == ["middle", "newest"]


def test_default_and_caller_limits_cannot_exceed_the_global_bound() -> None:
    attempts = [
        _attempt(f"role-{index:02d}", ended_at=float(index))
        for index in range(MAX_RESOURCE_EXPERIENCE_ATTEMPTS + 5)
    ]

    default = summarize_resource_experience(attempts)
    oversized = summarize_resource_experience(attempts, limit=10_000)

    assert len(default["groups"]) == MAX_RESOURCE_EXPERIENCE_ATTEMPTS
    assert oversized == default
    assert default["source_attempt_count"] == MAX_RESOURCE_EXPERIENCE_ATTEMPTS + 5
    assert default["truncated"] is True
    with pytest.raises(ValueError, match="positive integer"):
        summarize_resource_experience(attempts, limit=0)
    with pytest.raises(ValueError, match="positive integer"):
        summarize_resource_experience(attempts, limit=True)


def test_invalid_records_and_telemetry_are_ignored_not_forwarded() -> None:
    valid_with_bad_telemetry = _attempt(
        "valid",
        usage={
            "minimum_free_memory_mib": -1,
            "free_memory_mib": True,
            "maximum_used_memory_mib": float("inf"),
            "used_memory_mib": "512",
            "pressure_exceeded": "true",
        },
    )
    attempts: list[object] = [
        valid_with_bad_telemetry,
        "not-a-mapping",
        {**_attempt("bad-preference"), "resource_preference": "GPU_UUID"},
        {**_attempt("bad-status"), "terminal_status": "UNKNOWN"},
        {**_attempt("bad-role"), "role": ""},
        {**_attempt("bad-time"), "ended_at": 10**10_000},
    ]

    summary = summarize_resource_experience(attempts)  # type: ignore[arg-type]

    assert summary["source_attempt_count"] == 2
    assert summary["truncated"] is False
    assert summary["groups"] == [
        {
            "role": "bad-time",
            "component_id": "component-a",
            "method_family": "transformer",
            "resource_preference": "AUTO",
            "attempt_count": 1,
            "successful_attempt_count": 1,
            "failed_attempt_count": 0,
            "cpu_grant_count": 0,
            "gpu_grant_count": 1,
            "minimum_observed_free_memory_mib": None,
            "maximum_observed_used_memory_mib": None,
            "pressure_termination_count": 0,
        },
        {
            "role": "valid",
            "component_id": "component-a",
            "method_family": "transformer",
            "resource_preference": "AUTO",
            "attempt_count": 1,
            "successful_attempt_count": 1,
            "failed_attempt_count": 0,
            "cpu_grant_count": 0,
            "gpu_grant_count": 1,
            "minimum_observed_free_memory_mib": None,
            "maximum_observed_used_memory_mib": None,
            "pressure_termination_count": 0,
        }
    ]


def test_empty_summary_has_the_fixed_json_schema() -> None:
    summary = summarize_resource_experience([])

    assert set(summary) == {
        "schema_version",
        "resource_experience_version",
        "groups",
        "source_attempt_count",
        "truncated",
    }
    assert summary == {
        "schema_version": 1,
        "resource_experience_version": "resource-experience/1",
        "groups": [],
        "source_attempt_count": 0,
        "truncated": False,
    }


def test_device_peak_uses_total_minus_minimum_free_not_only_latest_used() -> None:
    summary = summarize_resource_experience([_attempt("critic", usage={
        "total_memory_mib": 24000, "minimum_free_memory_mib": 4000,
        "latest_used_memory_mib": 2000,
    })])
    assert summary["groups"][0]["maximum_observed_used_memory_mib"] == 20000


@pytest.mark.parametrize("total,free", [(1000, 2000), (True, 0), (24000, -1)])
def test_incoherent_device_totals_do_not_invent_a_peak(total, free) -> None:
    summary = summarize_resource_experience([_attempt("critic", usage={
        "total_memory_mib": total, "minimum_free_memory_mib": free,
        "latest_used_memory_mib": 2000,
    })])
    assert summary["groups"][0]["maximum_observed_used_memory_mib"] == 2000
