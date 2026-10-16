"""Bounded, read-only owned GPU measurements; never a process authority.

NVML supplies physical-device process bytes. Numeric host PPid ancestry joins
those PIDs to already-owned Runner roots across bwrap PID/session boundaries.
Metadata is checked on both sides of the sample and is never used for signals,
launch authorization, persisted process identity, or private-file inspection.
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .compute_resources import GpuRuntimeObservation, validate_gpu_uuid


_MIB = 1024**2
_UINT_MAX = 2**32 - 1
_ULL_MAX = 2**64 - 1
_MAX_PROCESSES = 4096
_MAX_JSON_INT = 2**53 - 1


class AttributionUnavailable(ValueError):
    """A fixed, non-private reason why this sample cannot receive credit."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _integer(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value <= _MAX_JSON_INT


@dataclass(frozen=True, slots=True)
class ProcessMetadata:
    pid: int
    ppid: int
    process_group_id: int
    start_time_ticks: int
    uid: int

    def __post_init__(self) -> None:
        if not (
            _integer(self.pid, 1)
            and all(
                _integer(value)
                for value in (
                    self.ppid,
                    self.process_group_id,
                    self.start_time_ticks,
                    self.uid,
                )
            )
        ):
            raise ValueError("process metadata must contain bounded numeric fields")

    def proof(self) -> dict[str, int]:
        return {
            "pid": self.pid,
            "ppid": self.ppid,
            "start_time_ticks": self.start_time_ticks,
        }


def read_process_metadata(pid: int) -> ProcessMetadata:
    """Read only stat numbers and directory UID; never cmdline/environ/files."""
    if not _integer(pid, 1):
        raise ValueError("invalid process ID")
    directory = Path("/proc") / str(pid)
    # An open proc directory FD stays tied to that incarnation if the PID exits.
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        uid = os.fstat(fd).st_uid
        stat_fd = os.open("stat", os.O_RDONLY | os.O_CLOEXEC, dir_fd=fd)
        with os.fdopen(stat_fd, "r", encoding="utf-8") as stream:
            raw = stream.read(16384)
    finally:
        os.close(fd)
    # comm may contain spaces or ')'; only the final ')' ends that field.
    prefix, separator, suffix = raw.rpartition(")")
    fields = suffix.split()
    if not separator or int(prefix.split("(", 1)[0]) != pid or len(fields) < 20:
        raise AttributionUnavailable("process_metadata_invalid")
    if fields[0] in {"Z", "X", "x"}:
        raise AttributionUnavailable("process_exited")
    return ProcessMetadata(pid, int(fields[1]), int(fields[2]), int(fields[19]), uid)


@dataclass(frozen=True, slots=True)
class GpuProcessUsage:
    pid: int
    resident_bytes: int | None
    mode: str = "compute"
    gpu_instance_id: int | None = None
    compute_instance_id: int | None = None

    def __post_init__(self) -> None:
        if not _integer(self.pid, 1) or self.mode not in {"compute", "graphics", "mps"}:
            raise ValueError("invalid GPU process identity or mode")
        if self.resident_bytes is not None and not _integer(self.resident_bytes):
            raise ValueError("invalid GPU resident byte count")
        if any(
            value is not None and not _integer(value)
            for value in (self.gpu_instance_id, self.compute_instance_id)
        ):
            raise ValueError("invalid GPU instance identity")


class _NvmlProcessInfo(ctypes.Structure):
    # Both exported v2 and v3 functions use this 24-byte layout; v1 does not.
    _fields_ = [
        ("pid", ctypes.c_uint),
        ("usedGpuMemory", ctypes.c_ulonglong),
        ("gpuInstanceId", ctypes.c_uint),
        ("computeInstanceId", ctypes.c_uint),
    ]


def _function(library: Any, name: str, arguments: tuple[Any, ...]) -> Any:
    try:
        function = getattr(library, name)
    except AttributeError as exc:
        raise AttributionUnavailable("nvml_api_unsupported") from exc
    function.argtypes = list(arguments)
    function.restype = ctypes.c_int
    return function


def _process_rows(library: Any, handle: Any, mode: str) -> tuple[GpuProcessUsage, ...]:
    title = {"compute": "Compute", "graphics": "Graphics", "mps": "MPSCompute"}[mode]
    function = None
    for version in (3, 2):
        try:
            function = _function(
                library,
                f"nvmlDeviceGet{title}RunningProcesses_v{version}",
                (
                    ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_uint),
                    ctypes.POINTER(_NvmlProcessInfo),
                ),
            )
        except AttributionUnavailable:
            continue
        count = ctypes.c_uint(0)
        result = function(handle, ctypes.byref(count), None)
        if result == 13:  # Library/driver version skew: try the older same-layout API.
            continue
        break
    else:
        raise AttributionUnavailable("nvml_api_unsupported")
    if result == 0:
        return ()
    if result != 7:
        raise AttributionUnavailable("nvml_process_query_failed")
    for _ in range(3):
        capacity = min(_MAX_PROCESSES, int(count.value) * 2 + 8)
        if count.value > _MAX_PROCESSES:
            raise AttributionUnavailable("process_limit")
        array = (_NvmlProcessInfo * capacity)()
        count.value = capacity
        result = function(handle, ctypes.byref(count), array)
        if result == 7:
            continue
        if result != 0 or count.value > capacity:
            raise AttributionUnavailable("nvml_process_query_failed")
        return tuple(
            GpuProcessUsage(
                pid=int(item.pid),
                resident_bytes=None
                if item.usedGpuMemory == _ULL_MAX
                else int(item.usedGpuMemory),
                mode=mode,
                gpu_instance_id=None
                if item.gpuInstanceId == _UINT_MAX
                else int(item.gpuInstanceId),
                compute_instance_id=None
                if item.computeInstanceId == _UINT_MAX
                else int(item.computeInstanceId),
            )
            for item in array[: count.value]
        )
    raise AttributionUnavailable("process_set_changed")


def read_gpu_processes(uuid: str) -> tuple[GpuProcessUsage, ...]:
    """Read explicit physical UUID only, without loading or executing CUDA."""
    validate_gpu_uuid(uuid)
    try:
        library = ctypes.CDLL("libnvidia-ml.so.1")
        try:
            initialize = _function(library, "nvmlInit_v2", ())
        except AttributionUnavailable:
            initialize = _function(library, "nvmlInit", ())
        shutdown = _function(library, "nvmlShutdown", ())
        by_uuid = _function(
            library,
            "nvmlDeviceGetHandleByUUID",
            (ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)),
        )
        if initialize() != 0:
            raise AttributionUnavailable("nvml_initialize_failed")
        try:
            handle = ctypes.c_void_p()
            if by_uuid(uuid.encode("ascii"), ctypes.byref(handle)) != 0:
                raise AttributionUnavailable("nvml_device_unavailable")
            return tuple(
                row
                for mode in ("compute", "graphics", "mps")
                for row in _process_rows(library, handle, mode)
            )
        finally:
            shutdown()
    except OSError as exc:
        raise AttributionUnavailable("nvml_unavailable") from exc


def _normalize(rows: Sequence[GpuProcessUsage]) -> dict[int, int]:
    if not isinstance(rows, Sequence) or len(rows) > _MAX_PROCESSES:
        raise AttributionUnavailable("process_limit")
    result: dict[int, int] = {}
    for row in rows:
        if not isinstance(row, GpuProcessUsage):
            raise AttributionUnavailable("nvml_process_data_invalid")
        if row.mode == "mps":
            raise AttributionUnavailable("mps_unsupported")
        if row.gpu_instance_id is not None or row.compute_instance_id is not None:
            raise AttributionUnavailable("mig_unsupported")
        if row.resident_bytes is None:
            raise AttributionUnavailable("memory_unavailable")
        if row.pid in result and result[row.pid] != row.resident_bytes:
            raise AttributionUnavailable("duplicate_memory_mismatch")
        result[row.pid] = row.resident_bytes
    return result


class GpuAttributionSampler:
    """One bounded, fresh sample across the trusted active Runner roots.

    The process provider and metadata reader are service code, not RPC inputs.
    Sampling cannot promise an instantaneous peak or survive arbitrary process
    churn: an observed incomplete/raced sample is unknown, not a partial sum.
    """

    def __init__(
        self,
        *,
        process_provider: Callable[
            [str], Sequence[GpuProcessUsage]
        ] = read_gpu_processes,
        metadata_reader: Callable[[int], ProcessMetadata] = read_process_metadata,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        max_duration_seconds: float = 1.0,
        max_ancestry: int = 64,
        max_proof_records: int = 512,
    ):
        if (
            isinstance(max_duration_seconds, bool)
            or not isinstance(max_duration_seconds, (int, float))
            or not math.isfinite(max_duration_seconds)
            or max_duration_seconds <= 0
            or not _integer(max_ancestry, 1)
            or max_ancestry > 256
            or not _integer(max_proof_records, 1)
            or max_proof_records > 4096
        ):
            raise ValueError("invalid attribution sample bounds")
        self.process_provider, self.metadata_reader = process_provider, metadata_reader
        self.clock, self.monotonic = clock, monotonic
        self.max_duration_seconds = max_duration_seconds
        self.max_ancestry, self.max_proof_records = max_ancestry, max_proof_records

    def sample(
        self, authorized_uuids: Sequence[str], roots: Mapping[str, int]
    ) -> dict[str, dict[str, dict[str, Any]]]:
        if isinstance(authorized_uuids, (str, bytes)) or not isinstance(
            authorized_uuids, Sequence
        ):
            raise ValueError("authorized GPUs must be a UUID sequence")
        uuids = tuple(authorized_uuids)
        for uuid in uuids:
            validate_gpu_uuid(uuid)
        if len(set(uuids)) != len(uuids):
            raise ValueError("authorized GPUs must be unique")
        if (
            not isinstance(roots, Mapping)
            or len(roots) > 128
            or any(
                not isinstance(key, str)
                or not key
                or len(key) > 4096
                or "\x00" in key
                or not _integer(pgid, 2)
                for key, pgid in roots.items()
            )
        ):
            raise ValueError("roots must map bounded stable strings to Runner PGIDs")
        result: dict[str, dict[str, dict[str, Any]]] = {key: {} for key in roots}
        if not roots:
            return result
        for uuid in sorted(uuids):
            for key, observation in self._device(uuid, roots).items():
                result[key][uuid] = observation
        return result

    def _check_budget(self, started: float) -> None:
        if self.monotonic() - started > self.max_duration_seconds:
            raise AttributionUnavailable("sample_too_slow")

    def _read(self, pid: int, started: float) -> ProcessMetadata:
        self._check_budget(started)
        try:
            value = self.metadata_reader(pid)
        except FileNotFoundError as exc:
            raise AttributionUnavailable("process_exited") from exc
        if not isinstance(value, ProcessMetadata) or value.pid != pid:
            raise AttributionUnavailable("process_metadata_invalid")
        return value

    def _trace(
        self,
        pid: int,
        root_pids: set[int],
        metadata: dict[int, ProcessMetadata],
        started: float,
    ) -> tuple[ProcessMetadata, ...]:
        ancestry: list[ProcessMetadata] = []
        seen: set[int] = set()
        while pid:
            if pid in seen or len(ancestry) >= self.max_ancestry:
                raise AttributionUnavailable("ancestry_limit")
            seen.add(pid)
            if pid not in metadata:
                metadata[pid] = self._read(pid, started)
            item = metadata[pid]
            if ancestry and item.start_time_ticks > ancestry[-1].start_time_ticks:
                raise AttributionUnavailable("process_metadata_changed")
            ancestry.append(item)
            if pid in root_pids:
                if any(part.uid != item.uid for part in ancestry):
                    raise AttributionUnavailable("process_uid_changed")
                return tuple(ancestry)
            pid = item.ppid
        return ()  # Proven external ancestry; never include it in returned proof.

    def _device(self, uuid: str, roots: Mapping[str, int]) -> dict[str, dict[str, Any]]:
        started = self.monotonic()
        metadata: dict[int, ProcessMetadata] = {}
        reasons: dict[str, str | None] = {}
        duplicates = Counter(roots.values())
        for key, pgid in roots.items():
            try:
                if duplicates[pgid] > 1:
                    raise AttributionUnavailable("ambiguous_root")
                metadata[pgid] = self._read(pgid, started)
                if metadata[pgid].process_group_id != pgid:
                    raise AttributionUnavailable("root_not_group_leader")
                reasons[key] = None
            except Exception as exc:
                reasons[key] = (
                    exc.reason
                    if isinstance(exc, AttributionUnavailable)
                    else "root_unavailable"
                )
                if isinstance(exc, FileNotFoundError) or reasons[key] == "process_exited":
                    reasons[key] = "root_process_exited"
        active = {pgid for key, pgid in roots.items() if reasons[key] is None}
        processes: dict[int, list[dict[str, Any]]] = {pgid: [] for pgid in active}
        try:
            if active:
                self._check_budget(started)
                before = _normalize(self.process_provider(uuid))
                self._check_budget(started)
                paths = {
                    pid: self._trace(pid, active, metadata, started) for pid in before
                }
                self._check_budget(started)
                after = _normalize(self.process_provider(uuid))
                # CUDA startup may add a process during capture. Include its
                # ancestry and require one stable pair within the same budget;
                # disappearing processes or further churn still remain unknown.
                if before.keys() < after.keys():
                    before = after
                    paths.update({
                        pid: self._trace(pid, active, metadata, started)
                        for pid in before if pid not in paths
                    })
                    self._check_budget(started)
                    after = _normalize(self.process_provider(uuid))
                if before.keys() != after.keys():
                    raise AttributionUnavailable(
                        "process_set_reduced" if after.keys() < before.keys() else "process_set_changed"
                    )
                for pid, previous in metadata.items():
                    try:
                        current = self._read(pid, started)
                    except (FileNotFoundError, AttributionUnavailable) as exc:
                        if isinstance(exc, FileNotFoundError) or exc.reason == "process_exited":
                            for key, root_pid in roots.items():
                                if root_pid == pid and reasons[key] is None:
                                    reasons[key] = "root_process_exited"
                        raise
                    if current != previous:
                        raise AttributionUnavailable("process_metadata_changed")
                sizes = Counter()
                totals = Counter()
                for pid, ancestry in sorted(paths.items()):
                    if not ancestry:
                        continue
                    root = ancestry[-1].pid
                    sizes[root] += len(ancestry)
                    if sizes[root] > self.max_proof_records:
                        raise AttributionUnavailable("proof_limit")
                    totals[root] += after[pid]
                    if not _integer(totals[root]):
                        raise AttributionUnavailable("memory_sum_invalid")
                    processes[root].append(
                        {
                            "pid": pid,
                            "resident_bytes": after[pid],
                            "ancestry": [item.proof() for item in ancestry],
                        }
                    )
        except Exception as exc:
            reason = (
                exc.reason
                if isinstance(exc, AttributionUnavailable)
                else "process_sample_unavailable"
            )
            for key in reasons:
                if reasons[key] is None:
                    reasons[key] = reason
        duration = self.monotonic() - started
        observed_at = self.clock()
        if (
            not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or duration < 0
            or not isinstance(observed_at, (int, float))
            or not math.isfinite(observed_at)
        ):
            raise ValueError("attribution clocks must be finite and monotonic")
        if duration > self.max_duration_seconds:
            reasons = {key: "sample_too_slow" for key in roots}
        result = {}
        for key, pgid in roots.items():
            complete = reasons[key] is None
            own = processes.get(pgid, []) if complete else []
            root = metadata.get(pgid)
            result[key] = {
                "gpu_uuid": uuid,
                "root_process_group_id": pgid,
                "root_start_time_ticks": None
                if root is None
                else root.start_time_ticks,
                "complete": complete,
                "reason": reasons[key],
                "observed_at": float(observed_at),
                "duration_seconds": float(duration),
                "resident_bytes": sum(item["resident_bytes"] for item in own)
                if complete
                else None,
                "processes": own,
            }
        return result

    def pool_residents(
        self, devices: tuple[GpuRuntimeObservation, ...], roots: Mapping[Any, int]
    ) -> dict[tuple[Any, str], int | None]:
        """Adapt the same sampler to existing pool keys without importing pool code."""
        keys = tuple(roots)
        observations = self.sample(
            tuple(device.uuid for device in devices),
            {str(index): roots[key] for index, key in enumerate(keys)},
        )
        result = {}
        for device in devices:
            rows = [observations[str(index)][device.uuid] for index in range(len(keys))]
            coherent = (
                sum(row["resident_bytes"] or 0 for row in rows)
                <= device.used_memory_mib * _MIB
            )
            for key, row in zip(keys, rows, strict=True):
                result[(key, device.uuid)] = (
                    row["resident_bytes"] // _MIB
                    if coherent
                    and row["complete"]
                    and row["resident_bytes"] is not None
                    else None
                )
        return result


class OwnedMemoryAccumulator:
    """Keep two bounded evidence samples, not an ever-growing telemetry ledger.

    A peak is a historically complete sample. Learning additionally requires
    sample_count > 0 and unknown_sample_count == 0 within the Runner sampling
    window. This does not promise continuous coverage or a hardware lifetime max.
    Current capacity never uses this historical accumulator.
    """

    def __init__(self) -> None:
        self._count = self._unknown = 0
        self._latest: dict[str, Any] | None = None
        self._peak: dict[str, Any] | None = None

    def sample(self, evidence: Mapping[str, Any]) -> None:
        value = json.loads(json.dumps(dict(evidence), allow_nan=False))
        if (
            type(value.get("complete")) is not bool
            or value["complete"]
            and not _integer(value.get("resident_bytes"))
            or not value["complete"]
            and value.get("resident_bytes") is not None
        ):
            raise ValueError("owned memory sample has inconsistent coverage")
        if self._latest is not None and any(
            value.get(name) != self._latest.get(name)
            for name in ("gpu_uuid", "root_process_group_id")
        ):
            raise ValueError("owned memory accumulator cannot mix devices or Attempts")
        self._count += 1
        self._latest = value
        if value["complete"]:
            if (
                self._peak is None
                or value["resident_bytes"] > self._peak["resident_bytes"]
            ):
                self._peak = value
        else:
            self._unknown += 1

    @property
    def summary(self) -> dict[str, Any]:
        return json.loads(
            json.dumps(
                {
                    "sample_count": self._count,
                    "unknown_sample_count": self._unknown,
                    "latest": self._latest,
                    "peak": self._peak,
                }
            )
        )
