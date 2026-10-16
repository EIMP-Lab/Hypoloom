"""Bounded resource experience and pure learning from closed process attempts.

The caller owns extraction and de-identification of closed ``PROCESS`` attempt
records.  This module only projects a small allowlist of scalar fields and
device-level telemetry. Explicit estimate resolution additionally consumes exact
caller-validated measurement identities; it does not read workflow state,
persist data, select devices, or authorize a grant.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .domain import ResourceEstimate, normalize_resource_workload

RESOURCE_EXPERIENCE_SCHEMA_VERSION = 1
RESOURCE_EXPERIENCE_VERSION = "resource-experience/1"
MAX_RESOURCE_EXPERIENCE_ATTEMPTS = 32

_MAX_LABEL_LENGTH = 128
_MAX_JSON_SAFE_INTEGER = 9_007_199_254_740_991
_PREFERENCES = frozenset({"CPU", "AUTO", "GPU_REQUIRED"})
_GRANT_KINDS = frozenset({"CPU", "GPU"})
_TERMINAL_STATUSES = frozenset(
    {"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED", "LOST"}
)
_MINIMUM_FREE_KEYS = (
    "gpu_minimum_free_memory_mib",
    "minimum_free_memory_mib",
    "minimum_observed_free_memory_mib",
    "min_free_memory_mib",
    "free_memory_mib",
)
_MAXIMUM_USED_KEYS = (
    "gpu_latest_used_memory_mib",
    "maximum_used_memory_mib",
    "maximum_observed_used_memory_mib",
    "max_used_memory_mib",
    "used_memory_mib",
    "latest_used_memory_mib",
)

_GroupKey = tuple[str, str, str, str]


@dataclass(frozen=True, slots=True)
class _Attempt:
    input_index: int
    ended_at: float | None
    group_key: _GroupKey
    terminal_status: str
    grant_kind: str | None
    minimum_free_memory_mib: int | None
    maximum_used_memory_mib: int | None
    pressure_terminated: bool

    @property
    def stable_key(self) -> tuple[object, ...]:
        """Tie-break equal timestamps without consulting unapproved fields."""

        return (
            *self.group_key,
            self.terminal_status,
            self.grant_kind or "",
            -1
            if self.minimum_free_memory_mib is None
            else self.minimum_free_memory_mib,
            -1
            if self.maximum_used_memory_mib is None
            else self.maximum_used_memory_mib,
            self.pressure_terminated,
        )


@dataclass(slots=True)
class _Aggregate:
    attempt_count: int = 0
    successful_attempt_count: int = 0
    failed_attempt_count: int = 0
    cpu_grant_count: int = 0
    gpu_grant_count: int = 0
    minimum_observed_free_memory_mib: int | None = None
    maximum_observed_used_memory_mib: int | None = None
    pressure_termination_count: int = 0

    def add(self, attempt: _Attempt) -> None:
        self.attempt_count += 1
        if attempt.terminal_status == "SUCCEEDED":
            self.successful_attempt_count += 1
        else:
            self.failed_attempt_count += 1
        if attempt.grant_kind == "CPU":
            self.cpu_grant_count += 1
        elif attempt.grant_kind == "GPU":
            self.gpu_grant_count += 1
        if attempt.minimum_free_memory_mib is not None:
            current = self.minimum_observed_free_memory_mib
            self.minimum_observed_free_memory_mib = (
                attempt.minimum_free_memory_mib
                if current is None
                else min(current, attempt.minimum_free_memory_mib)
            )
        if attempt.maximum_used_memory_mib is not None:
            current = self.maximum_observed_used_memory_mib
            self.maximum_observed_used_memory_mib = (
                attempt.maximum_used_memory_mib
                if current is None
                else max(current, attempt.maximum_used_memory_mib)
            )
        if attempt.pressure_terminated:
            self.pressure_termination_count += 1

    def to_dict(self, key: _GroupKey) -> dict[str, object]:
        role, component_id, method_family, preference = key
        return {
            "role": role,
            "component_id": component_id,
            "method_family": method_family,
            "resource_preference": preference,
            "attempt_count": self.attempt_count,
            "successful_attempt_count": self.successful_attempt_count,
            "failed_attempt_count": self.failed_attempt_count,
            "cpu_grant_count": self.cpu_grant_count,
            "gpu_grant_count": self.gpu_grant_count,
            "minimum_observed_free_memory_mib": (
                self.minimum_observed_free_memory_mib
            ),
            "maximum_observed_used_memory_mib": (
                self.maximum_observed_used_memory_mib
            ),
            "pressure_termination_count": self.pressure_termination_count,
        }


def summarize_resource_experience(
    attempts: Sequence[Mapping[str, object]],
    *,
    limit: int = MAX_RESOURCE_EXPERIENCE_ATTEMPTS,
) -> dict[str, object]:
    """Return deterministic advisory aggregates for recent process attempts.

    Malformed or out-of-scope records are ignored.  Extra fields are never
    copied.  If every accepted record has a finite numeric ``ended_at``, the
    most recent records are selected by that value with an allowlisted stable
    tie-break.  Otherwise input order is the recency source and later entries
    are newer.  ``limit`` may request a smaller view but cannot raise the
    global bound of 32 source attempts (and therefore 32 groups).

    ``source_attempt_count`` is the number of accepted records before that
    recency bound.  Counts and observations are historical advice only: the
    result contains no requested grant, device identity, or scheduling action.
    """

    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    bounded_limit = min(limit, MAX_RESOURCE_EXPERIENCE_ATTEMPTS)
    accepted = [
        item
        for index, raw in enumerate(attempts)
        if (item := _sanitize_attempt(raw, index)) is not None
    ]

    if accepted and all(item.ended_at is not None for item in accepted):
        recent = sorted(
            accepted,
            key=lambda item: (-_required_end_time(item), item.stable_key),
        )[:bounded_limit]
    else:
        recent = list(reversed(accepted))[:bounded_limit]

    aggregates: dict[_GroupKey, _Aggregate] = {}
    for attempt in recent:
        aggregate = aggregates.setdefault(attempt.group_key, _Aggregate())
        aggregate.add(attempt)
    groups = [aggregates[key].to_dict(key) for key in sorted(aggregates)]
    return {
        "schema_version": RESOURCE_EXPERIENCE_SCHEMA_VERSION,
        "resource_experience_version": RESOURCE_EXPERIENCE_VERSION,
        "groups": groups,
        "source_attempt_count": len(accepted),
        "truncated": len(accepted) > bounded_limit,
    }


def _sanitize_attempt(raw: object, input_index: int) -> _Attempt | None:
    if not isinstance(raw, Mapping):
        return None
    if raw.get("kind", "PROCESS") != "PROCESS":
        return None
    if raw.get("state", "CLOSED") != "CLOSED":
        return None

    labels = tuple(
        _safe_label(raw.get(name))
        for name in ("role", "component_id", "method_family")
    )
    if any(label is None for label in labels):
        return None
    role, component_id, method_family = labels
    assert role is not None and component_id is not None and method_family is not None

    preference = raw.get("resource_preference")
    terminal_status = raw.get("terminal_status")
    if preference not in _PREFERENCES or terminal_status not in _TERMINAL_STATUSES:
        return None
    assert isinstance(preference, str) and isinstance(terminal_status, str)

    usage = _resource_usage(raw)
    free = [_memory_extreme(item, _MINIMUM_FREE_KEYS, minimum=True) for item in usage]
    used = [_device_peak_memory_mib(item) for item in usage]
    minimum_free = min((value for value in free if value is not None), default=None)
    maximum_used = max((value for value in used if value is not None), default=None)
    pressure_observed = any(
        item.get("pressure_terminated") is True or item.get("pressure_exceeded") is True
        for item in usage
    )
    return _Attempt(
        input_index=input_index,
        ended_at=_finite_number(raw.get("ended_at")),
        group_key=(role, component_id, method_family, preference),
        terminal_status=terminal_status,
        grant_kind=_grant_kind(raw.get("resource_grant")),
        minimum_free_memory_mib=minimum_free,
        maximum_used_memory_mib=maximum_used,
        pressure_terminated=terminal_status != "SUCCEEDED" and pressure_observed,
    )


def _safe_label(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_LABEL_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return None
    return value


def _grant_kind(value: object) -> str | None:
    if isinstance(value, Mapping):
        value = value.get("kind")
    return value if isinstance(value, str) and value in _GRANT_KINDS else None


def _resource_usage(raw: Mapping[object, object]) -> tuple[Mapping[object, object], ...]:
    direct = raw.get("resource_usage")
    if isinstance(direct, Mapping):
        return (direct,)
    if isinstance(direct, list):
        return tuple(item for item in direct if isinstance(item, Mapping))
    observation = raw.get("observation")
    if isinstance(observation, Mapping):
        nested = observation.get("resource_usage")
        if isinstance(nested, Mapping):
            return (nested,)
        if isinstance(nested, list):
            return tuple(item for item in nested if isinstance(item, Mapping))
    return ()


def _memory_extreme(
    usage: Mapping[object, object],
    keys: tuple[str, ...],
    *,
    minimum: bool,
) -> int | None:
    values = [
        value
        for key in keys
        if _valid_memory_mib(value := usage.get(key))
    ]
    if not values:
        return None
    return min(values) if minimum else max(values)


def _valid_memory_mib(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= _MAX_JSON_SAFE_INTEGER
    )


def _device_peak_memory_mib(usage: Mapping[object, object]) -> int | None:
    """Device pressure history, deliberately not attributable workload usage."""
    latest_or_peak = _memory_extreme(usage, _MAXIMUM_USED_KEYS, minimum=False)
    total = usage.get("gpu_total_memory_mib", usage.get("total_memory_mib"))
    free = _memory_extreme(usage, _MINIMUM_FREE_KEYS, minimum=True)
    if _valid_memory_mib(total) and free is not None and free <= total:
        return max(total - free, latest_or_peak or 0)
    return latest_or_peak


def _owned_peak_bytes(raw: Mapping[str, object], *, complete_coverage: bool = True) -> int | None:
    usage = raw.get("resource_usage")
    devices = [usage] if isinstance(usage, Mapping) else usage
    if not isinstance(devices, list) or not devices:
        return None
    peaks = []
    for device in devices:
        owned = device.get("owned_memory") if isinstance(device, Mapping) else None
        if not isinstance(owned, Mapping):
            if complete_coverage: return None
            continue
        count, unknown = owned.get("sample_count"), owned.get("unknown_sample_count")
        peak = owned.get("peak")
        if (not _valid_memory_mib(count) or count == 0 or type(unknown) is not int or unknown < 0
                or (complete_coverage and unknown != 0)
                or not isinstance(peak, Mapping) or peak.get("complete") is not True
                or peak.get("reason") is not None or not _valid_memory_mib(peak.get("resident_bytes"))):
            if complete_coverage: return None
            continue
        peaks.append(peak["resident_bytes"])
    return max(peaks, default=None)


def summarize_resource_measurements(
    attempts: Sequence[Mapping[str, object]], *, limit: int = MAX_RESOURCE_EXPERIENCE_ATTEMPTS,
) -> list[dict[str, object]]:
    """Project scoped CLOSED sources without leaking raw GPU/PID/process data.

    Host supplies the allowed run/role/component/family scope. The workload map
    and existing Attempt ID remain to select immutable sources on later replay.
    Unknown coverage supplies only a diagnostic lower bound, never calibration.
    """
    if type(limit) is not int or limit <= 0:
        raise ValueError("limit must be a positive integer")
    accepted = []
    for raw in attempts:
        if not isinstance(raw, Mapping) or raw.get("kind") != "PROCESS" or raw.get("state") != "CLOSED":
            continue
        attempt_id = raw.get("attempt_id")
        if (not isinstance(attempt_id, str) or not attempt_id or attempt_id != attempt_id.strip()
                or len(attempt_id) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in attempt_id)
                or not isinstance(raw.get("terminal_status"), str)
                or raw["terminal_status"] not in _TERMINAL_STATUSES):
            continue
        try:
            workload = normalize_resource_workload(raw.get("workload"))
        except ValueError:
            continue
        peak = _owned_peak_bytes(raw)
        lower_bound = _owned_peak_bytes(raw, complete_coverage=False)
        admitted = raw.get("admitted_workload_memory_mib")
        usage = raw.get("resource_usage")
        devices = [usage] if isinstance(usage, Mapping) else usage if isinstance(usage, list) else []
        accepted.append({"attempt_id": attempt_id, "workload": workload,
            "admitted_workload_memory_mib": admitted if _valid_memory_mib(admitted) and admitted > 0 else None,
            "oom": raw.get("oom") is True, "terminal_status": raw["terminal_status"],
            "ended_at": _finite_number(raw.get("ended_at")), "sample_peak_bytes": peak,
            "observed_peak_lower_bound_bytes": lower_bound,
            "observed_over_budget": (lower_bound > admitted * 1024**2
                if lower_bound is not None and _valid_memory_mib(admitted) and admitted > 0 else None),
            "pressure_exceeded": any(isinstance(device, Mapping) and device.get("pressure_exceeded") is True
                for device in devices),
            "measurement_status": "complete" if peak is not None else "unknown"})
    if accepted and all(item["ended_at"] is not None for item in accepted):
        accepted.sort(key=lambda item: (-item["ended_at"], item["attempt_id"]))
    else:
        accepted.reverse()
    return accepted[:min(limit, MAX_RESOURCE_EXPERIENCE_ATTEMPTS)]


def _headroom_memory_mib(peak_mib: int) -> int:
    # ceil(peak_mib * 1.25 / 64) * 64, using integers throughout.
    return ((peak_mib * 5 + 255) // 256) * 64


def resolve_workload_memory_mib(
    profile_floor_mib: int, estimate: ResourceEstimate | None,
    measurements: Sequence[Mapping[str, object]] = (),
    *, initial_memory_mib: int | None = None, adaptive: bool = False,
) -> int:
    """Resolve a budget from exact, already scoped immutable source projections.

    ``measurements`` is the output of ``summarize_resource_measurements``.
    Missing/ambiguous references are errors; a latest-history query is never a
    replay substitute. Omitting the estimate explicitly retains the Profile
    floor without learning, or the optional conservative initial budget. Only a
    complete successful comparable trial releases that initial budget. Sampled
    peaks are lower bounds, not OOM guarantees.
    """
    return explain_workload_memory(profile_floor_mib, estimate, measurements,
        initial_memory_mib=initial_memory_mib, adaptive=adaptive)["resolved_workload_memory_mib"]


def explain_workload_memory(
    profile_floor_mib: int, estimate: ResourceEstimate | None,
    measurements: Sequence[Mapping[str, object]] = (), *,
    initial_memory_mib: int | None = None, adaptive: bool = False,
) -> dict[str, object]:
    """One budget calculation for admission, previews and validated audit sources.

    Adaptive margins require completed comparable runs after the last failure,
    covering all earlier observed peaks. This is evidence, not a peak guarantee.
    OOM escalation deliberately retains its independent 25 percent rule.
    """
    if estimate is not None and not isinstance(estimate, ResourceEstimate):
        raise ValueError("estimate must be a ResourceEstimate or None")
    sources = []
    for attempt_id in (() if estimate is None else estimate.measurement_attempt_ids):
        matches = [item for item in measurements if isinstance(item, Mapping) and item.get("attempt_id") == attempt_id]
        if len(matches) != 1 or matches[0].get("workload") != estimate.workload:
            raise ValueError("measurement Attempt must have one exact matching workload source")
        sources.append(matches[0])
    return explain_measured_memory(profile_floor_mib,
        None if estimate is None else estimate.peak_memory_mib, sources,
        initial_memory_mib=initial_memory_mib, adaptive=adaptive)


def explain_measured_memory(
    profile_floor_mib: int, submitted_memory_mib: int | None,
    sources: Sequence[Mapping[str, object]], *,
    initial_memory_mib: int | None = None, adaptive: bool = False,
) -> dict[str, object]:
    """Pure numeric policy after callers verify identities and workload scope.

    Admission uses typed estimates; the Oracle independently validates raw
    evidence. Neither path needs the other's source validator to share arithmetic.
    """
    if not _valid_memory_mib(profile_floor_mib) or profile_floor_mib == 0:
        raise ValueError("profile_floor_mib must be a positive JSON-safe integer")
    initial = profile_floor_mib if initial_memory_mib is None else initial_memory_mib
    if not _valid_memory_mib(initial) or initial < profile_floor_mib:
        raise ValueError("initial_memory_mib must be a JSON-safe integer at least profile_floor_mib")
    if submitted_memory_mib is not None and (not _valid_memory_mib(submitted_memory_mib) or submitted_memory_mib == 0):
        raise ValueError("submitted_memory_mib must be a positive JSON-safe integer or None")
    peaks, successes = [], []
    oom_floor = 0
    for source in sources:
        peak = source.get("sample_peak_bytes")
        if source.get("measurement_status") == "complete" and _valid_memory_mib(peak):
            mib = (peak + 1_048_575) // 1_048_576
            peaks.append(mib)
            if source.get("terminal_status") == "SUCCEEDED" and source.get("oom") is not True:
                successes.append((source, mib))
        admitted = source.get("admitted_workload_memory_mib")
        if source.get("oom") is True:
            if not _valid_memory_mib(admitted) or admitted == 0:
                raise ValueError("observed OOM source requires its prior admitted workload budget")
            oom_floor = max(oom_floor, _headroom_memory_mib(admitted))
    peak_mib = max(peaks, default=0)
    calibrated = []
    if adaptive and sources and all(_finite_number(s.get("ended_at")) is not None for s in sources):
        success_ids = {s["attempt_id"] for s, _ in successes}
        last_failure = max((s["ended_at"] for s in sources if s["attempt_id"] not in success_ids), default=float("-inf"))
        calibrated = [p for s, p in successes if s["ended_at"] > last_failure]
        if not calibrated or max(calibrated) < peak_mib:
            calibrated = []
    percent = 25
    if calibrated:
        percent = 10 if len(calibrated) >= 3 and max(calibrated) * 100 <= min(calibrated) * 105 else 15
    absolute_margin = 512 if calibrated else 0
    measured_budget = ((peak_mib + max((peak_mib * percent + 99) // 100, absolute_margin) + 63) // 64) * 64 if peaks else 0
    resolved = max(profile_floor_mib, submitted_memory_mib or 0, measured_budget, oom_floor)
    initial_active = not (calibrated if adaptive else successes)
    if initial_active or submitted_memory_mib is None:
        resolved = max(resolved, initial)
    if resolved > _MAX_JSON_SAFE_INTEGER:
        raise ValueError("resolved workload memory exceeds a JSON-safe integer")
    return {"resolved_workload_memory_mib": resolved, "profile_floor_mib": profile_floor_mib,
        "initial_memory_mib": initial, "initial_floor_active": initial_active,
        "submitted_memory_mib": submitted_memory_mib,
        "sample_peak_mib": peak_mib if peaks else None, "headroom_percent": percent,
        "minimum_headroom_mib": absolute_margin, "measured_budget_mib": measured_budget,
        "oom_budget_floor_mib": oom_floor, "successful_measurements": len(successes),
        "calibrated_measurements": len(calibrated),
        "sharing": "shared" if calibrated else "exclusive",
        "measurement_attempt_ids": [s["attempt_id"] for s in sources]}


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        numeric = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _required_end_time(attempt: _Attempt) -> float:
    assert attempt.ended_at is not None
    return attempt.ended_at


__all__ = [
    "MAX_RESOURCE_EXPERIENCE_ATTEMPTS",
    "RESOURCE_EXPERIENCE_SCHEMA_VERSION",
    "RESOURCE_EXPERIENCE_VERSION",
    "resolve_workload_memory_mib",
    "summarize_resource_experience",
    "summarize_resource_measurements",
]
