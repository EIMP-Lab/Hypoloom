"""Deterministic local CPU/GPU observation and selection adapter.

The adapter observes stable NVIDIA device identity plus live free memory and
translates one frozen resource grant into execution-workspace arguments.  It
owns neither workflow state nor process lifetime.
"""

from __future__ import annotations

import ctypes
import json
import math
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .domain import ResourceGrant, ResourceIntent, ResourceKind, ResourcePreference, validate_physical_gpu_uuid


COMPUTE_RESOURCES_VERSION = "local-compute/8"
COMPUTE_CAPABILITIES_SCHEMA_VERSION = 3
COMPUTE_CAPABILITIES_MEDIA_TYPE = "application/json"
_MIB = 1024 * 1024


def gpu_memory_reserve_mib(budget: Mapping[str, Any], workload_memory_mib: int) -> int:
    """Resolve the per-device reserve from an admitted workload, never live free VRAM.

    Missing/zero percent preserves the historical absolute reserve exactly.
    A positive percent uses at least 512 MiB and rounds upward to 64 MiB.
    The pool may independently require a larger shared device floor.
    """
    floor = budget.get("gpu_vram_reserve_mb", 0)
    percent = budget.get("gpu_vram_reserve_percent", 0)
    if type(floor) is not int or floor < 0:
        raise ValueError("budget.gpu_vram_reserve_mb must be a nonnegative integer")
    if type(percent) is not int or not 0 <= percent <= 100:
        raise ValueError("budget.gpu_vram_reserve_percent must be an integer from 0 to 100")
    if type(workload_memory_mib) is not int or workload_memory_mib < 0:
        raise ValueError("workload_memory_mib must be a nonnegative integer")
    if not percent:
        return floor
    return ((max(512, floor, (workload_memory_mib * percent + 99) // 100) + 63) // 64) * 64


class ComputeResourceError(RuntimeError):
    pass


class ResourceUnavailable(ComputeResourceError):
    pass


class ResourceCapacityUnavailable(ResourceUnavailable):
    """A granted GPU identity exists, but its live free-memory floor is unmet."""

    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class GpuCapability:
    index: int
    uuid: str
    name: str
    total_memory_mib: int
    free_memory_mib: int
    driver_version: str

    def __post_init__(self) -> None:
        if isinstance(self.index, bool) or not isinstance(self.index, int) or self.index < 0:
            raise ValueError("GPU index must be a non-negative integer")
        _gpu_uuid(self.uuid)
        _text(self.name, "GPU name")
        if (
            isinstance(self.total_memory_mib, bool)
            or not isinstance(self.total_memory_mib, int)
            or self.total_memory_mib <= 0
        ):
            raise ValueError("GPU total_memory_mib must be a positive integer")
        if (
            isinstance(self.free_memory_mib, bool)
            or not isinstance(self.free_memory_mib, int)
            or not 0 <= self.free_memory_mib <= self.total_memory_mib
        ):
            raise ValueError(
                "GPU free_memory_mib must be an integer between zero and total memory"
            )
        _text(self.driver_version, "GPU driver_version")

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "uuid": self.uuid,
            "name": self.name,
            "total_memory_mib": self.total_memory_mib,
            "free_memory_mib": self.free_memory_mib,
            "driver_version": self.driver_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> GpuCapability:
        _keys(
            value,
            {
                "index",
                "uuid",
                "name",
                "total_memory_mib",
                "free_memory_mib",
                "driver_version",
            },
            "GPU capability",
        )
        return cls(
            index=value["index"],
            uuid=value["uuid"],
            name=value["name"],
            total_memory_mib=value["total_memory_mib"],
            free_memory_mib=value["free_memory_mib"],
            driver_version=value["driver_version"],
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class GpuRuntimeObservation:
    """One read-only, device-level GPU sample.

    The memory values describe the whole device.  They intentionally make no
    claim about which process owns the used memory.
    """

    uuid: str
    total_memory_mib: int
    free_memory_mib: int
    used_memory_mib: int
    utilization_percent: int | None
    system_reserved_memory_mib: int | None = None
    power_draw_mw: int | None = None
    power_limit_mw: int | None = None
    temperature_c: int | None = None
    sm_clock_mhz: int | None = None

    def __post_init__(self) -> None:
        _gpu_uuid(self.uuid)
        _positive_memory(self.total_memory_mib, "GPU runtime total_memory_mib")
        _bounded_memory(
            self.free_memory_mib,
            self.total_memory_mib,
            "GPU runtime free_memory_mib",
        )
        _bounded_memory(
            self.used_memory_mib,
            self.total_memory_mib,
            "GPU runtime used_memory_mib",
        )
        if self.free_memory_mib + self.used_memory_mib != self.total_memory_mib:
            raise ValueError(
                "GPU runtime free_memory_mib and used_memory_mib must sum to "
                "total_memory_mib"
            )
        if self.system_reserved_memory_mib is not None:
            _bounded_memory(self.system_reserved_memory_mib, self.used_memory_mib,
                            "GPU runtime system_reserved_memory_mib")
        if self.utilization_percent is not None and (
            isinstance(self.utilization_percent, bool)
            or not isinstance(self.utilization_percent, int)
            or not 0 <= self.utilization_percent <= 100
        ):
            raise ValueError("GPU runtime utilization_percent must be null or 0..100")
        for name in ("power_draw_mw", "power_limit_mw", "temperature_c", "sm_clock_mhz"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"GPU runtime {name} must be null or a nonnegative integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "uuid": self.uuid,
            "total_memory_mib": self.total_memory_mib,
            "free_memory_mib": self.free_memory_mib,
            "used_memory_mib": self.used_memory_mib,
            "utilization_percent": self.utilization_percent,
            **({"system_reserved_memory_mib": self.system_reserved_memory_mib}
               if self.system_reserved_memory_mib is not None else {}),
            **{name: getattr(self, name) for name in
               ("power_draw_mw", "power_limit_mw", "temperature_c", "sm_clock_mhz")
               if getattr(self, name) is not None},
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class GpuPressureSummary:
    """Compact device-level usage summary produced by ``GpuPressureTracker``."""

    gpu_uuid: str
    gpu_total_memory_mib: int
    gpu_initial_free_memory_mib: int
    gpu_minimum_free_memory_mib: int
    gpu_latest_free_memory_mib: int
    gpu_latest_used_memory_mib: int
    gpu_latest_utilization_percent: int | None
    sample_count: int
    pressure_exceeded: bool
    gpu_latest_power_draw_mw: int | None = None
    gpu_latest_power_limit_mw: int | None = None
    gpu_latest_temperature_c: int | None = None
    gpu_latest_sm_clock_mhz: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "gpu_uuid": self.gpu_uuid,
            "gpu_total_memory_mib": self.gpu_total_memory_mib,
            "gpu_initial_free_memory_mib": self.gpu_initial_free_memory_mib,
            "gpu_minimum_free_memory_mib": self.gpu_minimum_free_memory_mib,
            "gpu_latest_free_memory_mib": self.gpu_latest_free_memory_mib,
            "gpu_latest_used_memory_mib": self.gpu_latest_used_memory_mib,
            "gpu_latest_utilization_percent": self.gpu_latest_utilization_percent,
            "sample_count": self.sample_count,
            "pressure_exceeded": self.pressure_exceeded,
            **{name: getattr(self, name) for name in
               ("gpu_latest_power_draw_mw", "gpu_latest_power_limit_mw",
                "gpu_latest_temperature_c", "gpu_latest_sm_clock_mhz")
               if getattr(self, name) is not None},
        }


class GpuPressureTracker:
    """Pure in-memory tracker for a sustained device free-memory breach.

    The tracker only interprets samples.  It never signals a process, writes
    workflow state, or attributes device memory to a worker.
    """

    def __init__(
        self,
        *,
        initial_observation: GpuRuntimeObservation,
        reserve_memory_mib: int,
        grace_seconds: float,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(initial_observation, GpuRuntimeObservation):
            raise ValueError("initial_observation must be a GpuRuntimeObservation")
        _non_negative_memory(reserve_memory_mib, "reserve_memory_mib")
        if (
            isinstance(grace_seconds, bool)
            or not isinstance(grace_seconds, (int, float))
            or not math.isfinite(float(grace_seconds))
            or grace_seconds < 0
        ):
            raise ValueError("grace_seconds must be a finite non-negative number")
        if not callable(now):
            raise ValueError("now must be callable")
        observed_at = _clock_value(now)
        self._now = now
        self._gpu_uuid = initial_observation.uuid
        self._total_memory_mib = initial_observation.total_memory_mib
        self._reserve_memory_mib = reserve_memory_mib
        self._grace_seconds = float(grace_seconds)
        self._initial_free_memory_mib = initial_observation.free_memory_mib
        self._minimum_free_memory_mib = initial_observation.free_memory_mib
        self._latest = initial_observation
        self._sample_count = 1
        self._last_sample_at = observed_at
        self._low_free_since = (
            observed_at
            if initial_observation.free_memory_mib < reserve_memory_mib
            else None
        )
        self._pressure_exceeded = (
            self._low_free_since is not None and self._grace_seconds == 0
        )

    @property
    def summary(self) -> GpuPressureSummary:
        return GpuPressureSummary(
            gpu_uuid=self._gpu_uuid,
            gpu_total_memory_mib=self._total_memory_mib,
            gpu_initial_free_memory_mib=self._initial_free_memory_mib,
            gpu_minimum_free_memory_mib=self._minimum_free_memory_mib,
            gpu_latest_free_memory_mib=self._latest.free_memory_mib,
            gpu_latest_used_memory_mib=self._latest.used_memory_mib,
            gpu_latest_utilization_percent=self._latest.utilization_percent,
            sample_count=self._sample_count,
            pressure_exceeded=self._pressure_exceeded,
            gpu_latest_power_draw_mw=self._latest.power_draw_mw,
            gpu_latest_power_limit_mw=self._latest.power_limit_mw,
            gpu_latest_temperature_c=self._latest.temperature_c,
            gpu_latest_sm_clock_mhz=self._latest.sm_clock_mhz,
        )

    def sample(self, observation: GpuRuntimeObservation) -> GpuPressureSummary:
        if not isinstance(observation, GpuRuntimeObservation):
            raise ValueError("observation must be a GpuRuntimeObservation")
        if (
            observation.uuid != self._gpu_uuid
            or observation.total_memory_mib != self._total_memory_mib
        ):
            raise ValueError("GPU runtime observation identity or total memory changed")
        observed_at = _clock_value(self._now)
        if observed_at < self._last_sample_at:
            raise ValueError("GPU pressure tracker clock moved backwards")
        self._last_sample_at = observed_at
        self._latest = observation
        self._sample_count += 1
        self._minimum_free_memory_mib = min(
            self._minimum_free_memory_mib,
            observation.free_memory_mib,
        )
        if observation.free_memory_mib < self._reserve_memory_mib:
            if self._low_free_since is None:
                self._low_free_since = observed_at
            self._pressure_exceeded = (
                observed_at - self._low_free_since >= self._grace_seconds
            )
        else:
            self._low_free_since = None
            self._pressure_exceeded = False
        return self.summary


def gpu_pressure_attribution(
    *,
    latest_free_memory_mib: int,
    owned_latest_memory_mib: int | None,
    reserve_memory_mib: int,
) -> str:
    """Would releasing this workload restore reserve? This is not causal history.

    If reserve would still be breached, other occupancy alone suffices and
    killing this workload cannot restore it. Unknown ownership stays fail-closed.
    """
    if owned_latest_memory_mib is None:
        return "unknown"
    return (
        "self"
        if latest_free_memory_mib + owned_latest_memory_mib >= reserve_memory_mib
        else "external"
    )


def gpu_pressure_payload(
    decisions: Sequence[tuple[str, int | None]],
    *,
    reserve_memory_mib: int,
    terminate_grace_seconds: float,
) -> dict[str, Any]:
    """Cancellation evidence over the pressured devices, without GPU identity."""
    return {
        "reason": "sustained_free_gpu_memory_below_reserve",
        "gpu_reserve_memory_mib": reserve_memory_mib,
        "owned_memory_mib_at_decision": (
            None
            if all(owned is None for _, owned in decisions)
            else sum(owned or 0 for _, owned in decisions)
        ),
        "attribution": (
            "self"
            if any(attribution == "self" for attribution, _ in decisions)
            else "unknown"
        ),
        "terminate_grace_seconds": terminate_grace_seconds,
    }


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputeCapabilities:
    gpus: tuple[GpuCapability, ...]
    busy_gpu_uuids: tuple[str, ...] = ()
    schema_version: int = COMPUTE_CAPABILITIES_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != COMPUTE_CAPABILITIES_SCHEMA_VERSION
        ):
            raise ValueError(
                "compute capability schema_version must be "
                f"{COMPUTE_CAPABILITIES_SCHEMA_VERSION}"
            )
        if not isinstance(self.gpus, tuple) or not all(
            isinstance(gpu, GpuCapability) for gpu in self.gpus
        ):
            raise ValueError("gpus must be a tuple of GpuCapability values")
        uuids = tuple(gpu.uuid for gpu in self.gpus)
        if uuids != tuple(sorted(uuids)):
            raise ValueError("GPU capabilities must be sorted by UUID")
        if len(uuids) != len(set(uuids)):
            raise ValueError("GPU capability UUIDs must be unique")
        indices = tuple(gpu.index for gpu in self.gpus)
        if len(indices) != len(set(indices)):
            raise ValueError("GPU capability indices must be unique")
        if not isinstance(self.busy_gpu_uuids, tuple):
            raise ValueError("busy_gpu_uuids must be a tuple")
        for uuid in self.busy_gpu_uuids:
            _gpu_uuid(uuid)
        if self.busy_gpu_uuids != tuple(sorted(set(self.busy_gpu_uuids))):
            raise ValueError("busy_gpu_uuids must be sorted and unique")
        if not set(self.busy_gpu_uuids) <= set(uuids):
            raise ValueError("busy_gpu_uuids must be a subset of GPU capabilities")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "gpus": [gpu.to_dict() for gpu in self.gpus],
            "busy_gpu_uuids": list(self.busy_gpu_uuids),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ComputeCapabilities:
        _keys(value, {"schema_version", "gpus", "busy_gpu_uuids"}, "compute capabilities")
        raw_gpus = value["gpus"]
        if not isinstance(raw_gpus, list):
            raise ValueError("compute capability gpus must be a list")
        raw_busy = value["busy_gpu_uuids"]
        if not isinstance(raw_busy, list) or not all(isinstance(uuid, str) for uuid in raw_busy):
            raise ValueError("compute capability busy_gpu_uuids must be a list of strings")
        return cls(
            schema_version=value["schema_version"],
            gpus=tuple(GpuCapability.from_dict(_mapping(item)) for item in raw_gpus),
            busy_gpu_uuids=tuple(raw_busy),
        )

    def to_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    def canonical_bytes(self) -> bytes:
        return self.to_bytes()

    @classmethod
    def from_bytes(cls, value: bytes) -> ComputeCapabilities:
        if not isinstance(value, bytes):
            raise ValueError("compute capability payload must be bytes")
        try:
            decoded = value.decode("utf-8")
            raw = json.loads(decoded, object_pairs_hook=_unique_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("compute capability payload must be valid UTF-8 JSON") from exc
        result = cls.from_dict(_mapping(raw))
        if result.to_bytes() != value:
            raise ValueError("compute capability payload must use canonical JSON bytes")
        return result


InventoryProvider = Callable[[], ComputeCapabilities]
RuntimeObservationProvider = Callable[[str], GpuRuntimeObservation]


class LocalComputeResources:
    """Observe explicitly allowed GPUs and make a deterministic local grant."""

    def __init__(
        self,
        *,
        allowed_gpu_uuids: Sequence[str] = (),
        gpu_runtime_prefix: str | Path | None = None,
        inventory_provider: InventoryProvider | None = None,
        observation_provider: RuntimeObservationProvider | None = None,
    ) -> None:
        if isinstance(allowed_gpu_uuids, (str, bytes)):
            raise ValueError("allowed_gpu_uuids must be a sequence of UUIDs")
        supplied = tuple(allowed_gpu_uuids)
        for uuid in supplied:
            _gpu_uuid(uuid)
        if len(supplied) != len(set(supplied)):
            raise ValueError("allowed_gpu_uuids must not contain duplicates")
        if bool(supplied) != (gpu_runtime_prefix is not None):
            raise ValueError("GPU allowlist and gpu_runtime_prefix must be configured together")
        self.allowed_gpu_uuids = tuple(sorted(supplied))
        self.gpu_runtime_prefix: Path | None = None
        if gpu_runtime_prefix is not None:
            raw_prefix = Path(gpu_runtime_prefix)
            if raw_prefix.is_symlink() or not raw_prefix.is_dir():
                raise ValueError("gpu_runtime_prefix must be an existing real directory")
            prefix = raw_prefix.resolve()
            python = prefix / "bin" / "python"
            if not python.is_file():
                raise ValueError("gpu_runtime_prefix must contain bin/python")
            self.gpu_runtime_prefix = prefix
        self._inventory_provider = inventory_provider or _nvml_inventory
        if observation_provider is not None:
            self._observation_provider = observation_provider
        elif inventory_provider is not None:
            self._observation_provider = self._observation_from_inventory
        else:
            self._observation_provider = _nvml_runtime_observation

    @property
    def configuration(self) -> dict[str, Any]:
        return {
            "compute_resources": COMPUTE_RESOURCES_VERSION,
            "gpu_device_uuids": list(self.allowed_gpu_uuids),
            "gpu_runtime_prefix": (
                None if self.gpu_runtime_prefix is None else str(self.gpu_runtime_prefix)
            ),
        }

    @property
    def runtime_files(self) -> tuple[Path, ...]:
        if self.gpu_runtime_prefix is None:
            return ()
        prefix = self.gpu_runtime_prefix
        files = [prefix / "bin" / "python"]
        metadata = prefix / "conda-meta"
        history = metadata / "history"
        if history.is_file():
            files.append(history)
        if metadata.is_dir():
            files.extend(sorted(metadata.glob("*.json")))
        package_records: set[Path] = set()
        for site_packages in sorted(prefix.glob("lib/python*/site-packages")):
            torch_version = site_packages / "torch" / "version.py"
            if torch_version.is_file():
                package_records.add(torch_version)
            for pattern in (
                "torch-*.dist-info",
                "triton-*.dist-info",
                "nvidia_*.dist-info",
            ):
                for distribution in site_packages.glob(pattern):
                    for name in ("METADATA", "RECORD"):
                        candidate = distribution / name
                        if candidate.is_file():
                            package_records.add(candidate)
        files.extend(sorted(package_records, key=lambda path: path.as_posix().encode("utf-8")))
        return tuple(files)

    def snapshot(self) -> ComputeCapabilities:
        if not self.allowed_gpu_uuids:
            return ComputeCapabilities(gpus=())
        observed = self._inventory()
        allowed = set(self.allowed_gpu_uuids)
        return ComputeCapabilities(gpus=tuple(gpu for gpu in observed.gpus if gpu.uuid in allowed))

    def observe_runtime(self, gpu_uuid: str) -> GpuRuntimeObservation:
        """Read one allowed GPU without changing workflow or process state."""

        _gpu_uuid(gpu_uuid)
        if gpu_uuid not in self.allowed_gpu_uuids:
            raise ResourceUnavailable(f"GPU {gpu_uuid} is not authorized")
        try:
            observation = self._observation_provider(gpu_uuid)
        except ResourceUnavailable:
            raise
        except Exception as exc:
            raise ResourceUnavailable(f"GPU runtime observation failed: {exc}") from exc
        if not isinstance(observation, GpuRuntimeObservation):
            raise ResourceUnavailable("GPU runtime observation provider returned an invalid value")
        if observation.uuid != gpu_uuid:
            raise ResourceUnavailable(
                f"GPU runtime observation returned a different GPU: {observation.uuid}"
            )
        return observation

    def select(
        self,
        preference: ResourcePreference,
        snapshot: ComputeCapabilities,
        *,
        busy_gpu_uuids: Collection[str] = (),
        min_vram_per_experiment: int = 0,
        gpu_vram_reserve_mb: int = 0,
        gpu_count: int | None = None,
        max_gpus_per_experiment: int = 1,
    ) -> ResourceGrant:
        preference, count = _requested_gpu_count(preference, gpu_count, max_gpus_per_experiment)
        busy = frozenset(snapshot.busy_gpu_uuids) | _busy_gpu_uuids(busy_gpu_uuids)
        required_free_memory_mib = _required_free_memory_mib(
            min_vram_per_experiment,
            gpu_vram_reserve_mb,
        )
        identities = tuple(
            sorted(
                self._eligible(snapshot),
                key=lambda gpu: (-gpu.free_memory_mib, gpu.uuid),
            )
        )
        available = tuple(gpu for gpu in identities if gpu.uuid not in busy)
        candidates = tuple(
            gpu for gpu in available
            if gpu.free_memory_mib >= required_free_memory_mib
        )
        if preference is ResourcePreference.CPU:
            return ResourceGrant(kind=ResourceKind.CPU, gpu_uuid=None)
        if len(candidates) >= count:
            selected = candidates[:count]
        elif preference is ResourcePreference.AUTO:
            return ResourceGrant(kind=ResourceKind.CPU, gpu_uuid=None)
        else:
            if len(identities) < count:
                raise ResourceUnavailable(f"GPU_REQUIRED needs {count} authorized GPU identities; only {len(identities)} are available")
            ranked = (*candidates, *(gpu for gpu in available if gpu not in candidates),
                *(gpu for gpu in identities if gpu.uuid in busy))
            selected = ranked[:count]
        if count == 1:
            return ResourceGrant(kind=ResourceKind.GPU, gpu_uuid=selected[0].uuid)
        return ResourceGrant(kind=ResourceKind.GPU, gpu_uuids=tuple(sorted(gpu.uuid for gpu in selected)))

    def plan_intent(
        self,
        preference: ResourcePreference,
        snapshot: ComputeCapabilities,
        *,
        gpu_count: int | None = None,
        max_gpus_per_experiment: int = 1,
        min_vram_per_experiment: int = 0,
        gpu_vram_reserve_mb: int = 0,
    ) -> ResourceGrant | ResourceIntent:
        """Capture authorized alternatives; shared capacity is resolved at dispatch."""
        preference, count = _requested_gpu_count(preference, gpu_count, max_gpus_per_experiment)
        _required_free_memory_mib(min_vram_per_experiment, gpu_vram_reserve_mb)
        identities = tuple(gpu.uuid for gpu in self._eligible(snapshot))
        if preference is ResourcePreference.CPU or (preference is ResourcePreference.AUTO and not identities):
            return ResourceGrant(kind=ResourceKind.CPU)
        if len(identities) < count:
            raise ResourceUnavailable(f"GPU_REQUIRED needs {count} authorized GPU identities; only {len(identities)} are available")
        if min_vram_per_experiment <= 0:
            raise ValueError("shared GPU intent requires positive min_vram_per_experiment")
        return ResourceIntent(allowed_gpu_uuids=identities, gpu_count=count,
            workload_memory_mib=min_vram_per_experiment, reserve_memory_mib=gpu_vram_reserve_mb)

    def validate_identity(self, grant: ResourceGrant, snapshot: ComputeCapabilities) -> None:
        """Recheck authorized physical identity, leaving capacity to the shared pool."""
        self.validate_live(grant, snapshot)

    def validate_live(
        self,
        grant: ResourceGrant,
        snapshot: ComputeCapabilities,
        *,
        busy_gpu_uuids: Collection[str] = (),
        min_vram_per_experiment: int = 0,
        gpu_vram_reserve_mb: int = 0,
    ) -> None:
        if not isinstance(grant, ResourceGrant):
            raise ValueError("grant must be a ResourceGrant")
        busy = _busy_gpu_uuids(busy_gpu_uuids)
        required_free_memory_mib = _required_free_memory_mib(
            min_vram_per_experiment,
            gpu_vram_reserve_mb,
        )
        if grant.kind is ResourceKind.CPU:
            return
        expected_gpus = self._granted_gpus(grant, snapshot)
        actual_by_uuid = {gpu.uuid: gpu for gpu in self.snapshot().gpus}
        for expected in expected_gpus:
            actual = actual_by_uuid.get(expected.uuid)
            if actual is None:
                raise ResourceUnavailable(f"granted GPU {expected.uuid} is unavailable")
            if _stable_gpu_identity(actual) != _stable_gpu_identity(expected):
                raise ResourceUnavailable(f"granted GPU {expected.uuid} capability drift detected")
            if actual.uuid in busy:
                raise ResourceUnavailable(f"granted GPU {expected.uuid} is already busy")
            if actual.free_memory_mib < required_free_memory_mib:
                raise ResourceCapacityUnavailable(
                    f"granted GPU {expected.uuid} has {actual.free_memory_mib} MiB free; "
                    f"{required_free_memory_mib} MiB is required"
                )

    def workspace_kwargs(
        self,
        grant: ResourceGrant,
        snapshot: ComputeCapabilities,
    ) -> dict[str, Any]:
        if not isinstance(grant, ResourceGrant):
            raise ValueError("grant must be a ResourceGrant")
        if grant.kind is ResourceKind.CPU:
            return {
                "device_bindings": (),
                "fixed_environment": {"CUDA_VISIBLE_DEVICES": ""},
            }
        gpus = self._granted_gpus(grant, snapshot)
        if self.gpu_runtime_prefix is None:
            raise ResourceUnavailable("GPU runtime is not configured")
        devices = (
            *(Path(f"/dev/nvidia{gpu.index}") for gpu in gpus),
            Path("/dev/nvidiactl"),
            Path("/dev/nvidia-uvm"),
        )
        return {
            "python_executable": self.gpu_runtime_prefix / "bin" / "python",
            "runtime_prefix": self.gpu_runtime_prefix,
            "device_bindings": tuple((device, device.as_posix()) for device in devices),
            "fixed_environment": {
                "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
                "CUDA_VISIBLE_DEVICES": ",".join(gpu.uuid for gpu in gpus),
            },
        }

    def _inventory(self) -> ComputeCapabilities:
        try:
            observed = self._inventory_provider()
        except ResourceUnavailable:
            raise
        except Exception as exc:
            raise ResourceUnavailable(f"GPU inventory failed: {exc}") from exc
        if not isinstance(observed, ComputeCapabilities):
            raise ResourceUnavailable("GPU inventory returned an invalid capability snapshot")
        return observed

    def _observation_from_inventory(self, gpu_uuid: str) -> GpuRuntimeObservation:
        for gpu in self._inventory().gpus:
            if gpu.uuid == gpu_uuid:
                return GpuRuntimeObservation(
                    uuid=gpu.uuid,
                    total_memory_mib=gpu.total_memory_mib,
                    free_memory_mib=gpu.free_memory_mib,
                    used_memory_mib=gpu.total_memory_mib - gpu.free_memory_mib,
                    utilization_percent=None,
                )
        raise ResourceUnavailable(f"GPU {gpu_uuid} is unavailable")

    def _eligible(self, snapshot: ComputeCapabilities) -> tuple[GpuCapability, ...]:
        if not isinstance(snapshot, ComputeCapabilities):
            raise ValueError("snapshot must be ComputeCapabilities")
        allowed = set(self.allowed_gpu_uuids)
        return tuple(gpu for gpu in snapshot.gpus if gpu.uuid in allowed)

    def _granted_gpus(
        self,
        grant: ResourceGrant,
        snapshot: ComputeCapabilities,
    ) -> tuple[GpuCapability, ...]:
        if grant.kind is not ResourceKind.GPU or not grant.device_uuids:
            raise ValueError("GPU resource operation requires a GPU grant")
        eligible = {gpu.uuid: gpu for gpu in self._eligible(snapshot)}
        for uuid in grant.device_uuids:
            if uuid not in eligible:
                raise ResourceUnavailable(f"granted GPU {uuid} is absent from the authorized snapshot")
        return tuple(eligible[uuid] for uuid in grant.device_uuids)


class _NvmlMemory(ctypes.Structure):
    _fields_ = [
        ("total", ctypes.c_ulonglong),
        ("free", ctypes.c_ulonglong),
        ("used", ctypes.c_ulonglong),
    ]


class _NvmlMemoryV2(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint), ("total", ctypes.c_ulonglong),
                ("reserved", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
                ("used", ctypes.c_ulonglong)]


class _NvmlUtilization(ctypes.Structure):
    _fields_ = [
        ("gpu", ctypes.c_uint),
        ("memory", ctypes.c_uint),
    ]


def _nvml_runtime_observation(gpu_uuid: str) -> GpuRuntimeObservation:
    try:
        library = ctypes.CDLL("libnvidia-ml.so.1")
    except OSError as exc:
        raise ResourceUnavailable("NVIDIA management library is unavailable") from exc
    initialize = _nvml_function(library, ("nvmlInit_v2", "nvmlInit"), (), ctypes.c_int)
    shutdown = _nvml_function(library, ("nvmlShutdown",), (), ctypes.c_int)
    handle_by_uuid = _nvml_function(
        library,
        ("nvmlDeviceGetHandleByUUID",),
        (ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)),
        ctypes.c_int,
    )
    get_memory = _nvml_function(
        library,
        ("nvmlDeviceGetMemoryInfo",),
        (ctypes.c_void_p, ctypes.POINTER(_NvmlMemory)),
        ctypes.c_int,
    )
    try:
        get_utilization = _nvml_function(
            library,
            ("nvmlDeviceGetUtilizationRates",),
            (ctypes.c_void_p, ctypes.POINTER(_NvmlUtilization)),
            ctypes.c_int,
        )
    except ResourceUnavailable:
        get_utilization = None
    _nvml_check(initialize(), "initialize")
    try:
        handle = ctypes.c_void_p()
        _nvml_check(
            handle_by_uuid(gpu_uuid.encode("ascii"), ctypes.byref(handle)),
            "device handle",
        )
        memory = _NvmlMemory()
        _nvml_check(get_memory(handle, ctypes.byref(memory)), "device memory")
        system_reserved = None
        try:
            get_memory_v2 = _nvml_function(library, ("nvmlDeviceGetMemoryInfo_v2",),
                (ctypes.c_void_p, ctypes.POINTER(_NvmlMemoryV2)), ctypes.c_int)
        except ResourceUnavailable:
            get_memory_v2 = None
        if get_memory_v2 is not None:
            extended = _NvmlMemoryV2(version=ctypes.sizeof(_NvmlMemoryV2) | (2 << 24))
            if get_memory_v2(handle, ctypes.byref(extended)) == 0:
                memory = extended
                system_reserved = (extended.reserved + _MIB - 1) // _MIB
        utilization_percent: int | None = None
        if get_utilization is not None:
            utilization = _NvmlUtilization()
            if get_utilization(handle, ctypes.byref(utilization)) == 0:
                utilization_percent = int(utilization.gpu)
        total_memory_mib = memory.total // _MIB
        free_memory_mib = memory.free // _MIB
        return GpuRuntimeObservation(
            uuid=gpu_uuid,
            total_memory_mib=total_memory_mib,
            free_memory_mib=free_memory_mib,
            used_memory_mib=total_memory_mib - free_memory_mib,
            utilization_percent=utilization_percent,
            system_reserved_memory_mib=system_reserved,
            power_draw_mw=_nvml_optional_uint(library, "nvmlDeviceGetPowerUsage", handle),
            power_limit_mw=_nvml_optional_uint(library, "nvmlDeviceGetPowerManagementLimit", handle),
            temperature_c=_nvml_optional_uint(library, "nvmlDeviceGetTemperature", handle, 0),
            sm_clock_mhz=_nvml_optional_uint(library, "nvmlDeviceGetClockInfo", handle, 1),
        )
    finally:
        shutdown()


def _nvml_optional_uint(library, name: str, handle, selector: int | None = None) -> int | None:
    """Optional device queries cannot change capacity or make a sample fail."""
    arguments = (handle,) if selector is None else (handle, selector)
    signature = (ctypes.c_void_p,) + (() if selector is None else (ctypes.c_uint,))
    try:
        query = _nvml_function(library, (name,), (*signature, ctypes.POINTER(ctypes.c_uint)), ctypes.c_int)
        value = ctypes.c_uint()
        if query(*arguments, ctypes.byref(value)) == 0 and value.value != 0xFFFFFFFF:
            return int(value.value)
    except (ResourceUnavailable, OSError):
        pass
    return None


def _nvml_inventory() -> ComputeCapabilities:
    try:
        library = ctypes.CDLL("libnvidia-ml.so.1")
    except OSError as exc:
        raise ResourceUnavailable("NVIDIA management library is unavailable") from exc
    initialize = _nvml_function(library, ("nvmlInit_v2", "nvmlInit"), (), ctypes.c_int)
    shutdown = _nvml_function(library, ("nvmlShutdown",), (), ctypes.c_int)
    count_devices = _nvml_function(
        library,
        ("nvmlDeviceGetCount_v2", "nvmlDeviceGetCount"),
        (ctypes.POINTER(ctypes.c_uint),),
        ctypes.c_int,
    )
    handle_by_index = _nvml_function(
        library,
        ("nvmlDeviceGetHandleByIndex_v2", "nvmlDeviceGetHandleByIndex"),
        (ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)),
        ctypes.c_int,
    )
    get_uuid = _nvml_function(
        library,
        ("nvmlDeviceGetUUID",),
        (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint),
        ctypes.c_int,
    )
    get_name = _nvml_function(
        library,
        ("nvmlDeviceGetName",),
        (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint),
        ctypes.c_int,
    )
    get_memory = _nvml_function(
        library,
        ("nvmlDeviceGetMemoryInfo",),
        (ctypes.c_void_p, ctypes.POINTER(_NvmlMemory)),
        ctypes.c_int,
    )
    get_driver = _nvml_function(
        library,
        ("nvmlSystemGetDriverVersion",),
        (ctypes.c_char_p, ctypes.c_uint),
        ctypes.c_int,
    )
    _nvml_check(initialize(), "initialize")
    try:
        driver = _nvml_text(get_driver, "driver version")
        count = ctypes.c_uint()
        _nvml_check(count_devices(ctypes.byref(count)), "device count")
        gpus: list[GpuCapability] = []
        for index in range(count.value):
            handle = ctypes.c_void_p()
            _nvml_check(handle_by_index(index, ctypes.byref(handle)), "device handle")
            uuid = _nvml_text(get_uuid, "device UUID", handle)
            name = _nvml_text(get_name, "device name", handle)
            memory = _NvmlMemory()
            _nvml_check(get_memory(handle, ctypes.byref(memory)), "device memory")
            gpus.append(
                GpuCapability(
                    index=index,
                    uuid=uuid,
                    name=name,
                    total_memory_mib=memory.total // _MIB,
                    free_memory_mib=memory.free // _MIB,
                    driver_version=driver,
                )
            )
        return ComputeCapabilities(gpus=tuple(sorted(gpus, key=lambda gpu: gpu.uuid)))
    finally:
        shutdown()


def _nvml_function(
    library: ctypes.CDLL,
    names: tuple[str, ...],
    argument_types: tuple[Any, ...],
    result_type: Any,
) -> Any:
    for name in names:
        try:
            function = getattr(library, name)
        except AttributeError:
            continue
        function.argtypes = list(argument_types)
        function.restype = result_type
        return function
    raise ResourceUnavailable(f"NVIDIA management symbol is unavailable: {names[0]}")


def _nvml_text(function: Any, label: str, handle: ctypes.c_void_p | None = None) -> str:
    buffer = ctypes.create_string_buffer(256)
    arguments = (buffer, len(buffer)) if handle is None else (handle, buffer, len(buffer))
    _nvml_check(function(*arguments), label)
    try:
        return buffer.value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ResourceUnavailable(f"NVIDIA {label} is not UTF-8") from exc


def _nvml_check(result: int, operation: str) -> None:
    if result != 0:
        raise ResourceUnavailable(f"NVIDIA inventory {operation} failed with code {result}")


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("compute capability object must be a mapping")
    return value


def _keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    actual = set(value)
    if actual != expected:
        unexpected = sorted(actual - expected)
        missing = sorted(expected - actual)
        raise ValueError(f"{label} has unexpected {unexpected!r} or missing {missing!r} keys")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _text(value: Any, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
    ):
        raise ValueError(f"{label} must be non-empty trimmed text")


def _gpu_uuid(value: Any) -> None:
    validate_physical_gpu_uuid(value)


def validate_gpu_uuid(value: Any) -> None:
    """Share the existing physical-device identity grammar with profile authors."""
    _gpu_uuid(value)


def observe_authorized_gpu_memory(
    allowed_gpu_uuids: Sequence[str],
) -> tuple[GpuRuntimeObservation, ...]:
    """Read explicit devices for a Host pool without requiring an execution runtime.

    These are whole-device observations, not owned-process attribution or grants.
    An empty allowlist performs no NVML calls; invalid input is rejected first.
    """
    if isinstance(allowed_gpu_uuids, (str, bytes)) or not isinstance(allowed_gpu_uuids, Sequence):
        raise ValueError("allowed_gpu_uuids must be a sequence of physical UUIDs")
    devices = tuple(allowed_gpu_uuids)
    for uuid in devices:
        _gpu_uuid(uuid)
    if len(devices) != len(set(devices)):
        raise ValueError("allowed_gpu_uuids must not contain duplicates")
    return tuple(_nvml_runtime_observation(uuid) for uuid in sorted(devices))


def _positive_memory(value: Any, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer MiB value")


def _non_negative_memory(value: Any, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer MiB value")


def _bounded_memory(value: Any, total: int, label: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= total
    ):
        raise ValueError(f"{label} must be an integer between zero and total memory")


def _clock_value(now: Callable[[], float]) -> float:
    value = now()
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise ValueError("GPU pressure tracker clock must return a finite number")
    return float(value)


def _busy_gpu_uuids(value: Collection[str]) -> frozenset[str]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Collection):
        raise ValueError("busy_gpu_uuids must be a collection of GPU UUIDs")
    supplied = tuple(value)
    for uuid in supplied:
        _gpu_uuid(uuid)
    if len(supplied) != len(set(supplied)):
        raise ValueError("busy_gpu_uuids must not contain duplicates")
    return frozenset(supplied)


def _requested_gpu_count(
    preference: ResourcePreference, gpu_count: int | None, max_gpus_per_experiment: int,
) -> tuple[ResourcePreference, int]:
    if not isinstance(preference, ResourcePreference):
        raise ValueError("preference must be a ResourcePreference")
    if type(max_gpus_per_experiment) is not int or max_gpus_per_experiment < 1:
        raise ValueError("max_gpus_per_experiment must be a positive integer")
    if gpu_count is not None:
        if type(gpu_count) is not int or gpu_count < 0:
            raise ValueError("gpu_count must be a nonnegative integer")
        if (gpu_count == 0 and preference is ResourcePreference.GPU_REQUIRED
                or gpu_count > 0 and preference is ResourcePreference.CPU):
            raise ValueError("gpu_count conflicts with the resource preference")
        preference = ResourcePreference.CPU if gpu_count == 0 else ResourcePreference.GPU_REQUIRED
    count = (0 if preference is ResourcePreference.CPU else 1) if gpu_count is None else gpu_count
    if count > max_gpus_per_experiment:
        raise ValueError("gpu_count exceeds max_gpus_per_experiment")
    return preference, count


def _required_free_memory_mib(
    min_vram_per_experiment: int,
    gpu_vram_reserve_mb: int,
) -> int:
    for value, label in (
        (min_vram_per_experiment, "min_vram_per_experiment"),
        (gpu_vram_reserve_mb, "gpu_vram_reserve_mb"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{label} must be a non-negative integer MiB value")
    return min_vram_per_experiment + gpu_vram_reserve_mb


def _stable_gpu_identity(gpu: GpuCapability) -> tuple[str, int, str, int]:
    return (
        gpu.uuid,
        gpu.index,
        gpu.driver_version,
        gpu.total_memory_mib,
    )


__all__ = [
    "COMPUTE_CAPABILITIES_MEDIA_TYPE",
    "COMPUTE_CAPABILITIES_SCHEMA_VERSION",
    "COMPUTE_RESOURCES_VERSION",
    "ComputeCapabilities",
    "ComputeResourceError",
    "GpuCapability",
    "GpuPressureSummary",
    "GpuPressureTracker",
    "GpuRuntimeObservation",
    "LocalComputeResources",
    "gpu_memory_reserve_mib",
    "gpu_pressure_attribution",
    "gpu_pressure_payload",
    "observe_authorized_gpu_memory",
    "ResourceUnavailable",
]
