"""Bounded kernel evidence for failed owned GPU Processes, never device control."""
from __future__ import annotations

import errno
import math
import os
import re
import time
from collections.abc import Mapping
from pathlib import Path


_XID = re.compile(r"NVRM: Xid \((?:PCI:)?([0-9a-fA-F:.]+)\): (\d+), pid=(\d+)(?:,|\s|$)")
_PCI = re.compile(r"(?:0000)?([0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2})(?:\.([0-7]))?", re.I)
_UUID = re.compile(r"GPU UUID:\s*(GPU-[0-9a-f-]{36})", re.I)
_MAX_RECORDS = 16384
_MAX_EVENTS = 16


def _pci(value):
    match = _PCI.fullmatch(value)
    return None if match is None else match[1].lower() + "." + (match[2] or "0")


def gpu_pci_identities(root=Path("/proc/driver/nvidia/gpus")):
    identities = {}
    try:
        for entry in sorted(root.iterdir())[:128]:
            pci = _pci(entry.name)
            if pci is None:
                continue
            with (entry / "information").open() as stream:
                match = _UUID.search(stream.read(8192))
            if match:
                identities[pci] = match[1]
    except OSError:
        pass
    return identities


def read_kernel_xids():
    """Opening kmsg has an independent cursor; reading never clears the kernel log."""
    events, first_timestamp, previous = [], None, None
    status, reason = "complete", None
    try:
        fd = os.open("/dev/kmsg", os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as exc:
        return {"status": "unavailable", "reason": "permission_denied" if exc.errno in
            {errno.EACCES, errno.EPERM} else "kernel_log_unavailable", "events": [], "first_timestamp": None}
    started = time.monotonic()
    try:
        for _ in range(_MAX_RECORDS):
            if time.monotonic() - started > 0.2:
                status, reason = "partial", "read_limit"
                break
            try:
                raw = os.read(fd, 65536)
            except BlockingIOError:
                break
            except OSError as exc:
                status, reason = "partial", "kernel_log_overrun" if exc.errno == errno.EPIPE else "read_failed"
                if exc.errno == errno.EPIPE:
                    continue
                break
            if not raw:
                break
            header, separator, message = raw.decode("utf-8", errors="replace").partition(";")
            try:
                fields = header.split(",")
                sequence, timestamp = int(fields[1]), int(fields[2]) / 1_000_000
                if not separator or sequence < 0 or not math.isfinite(timestamp) or timestamp < 0:
                    raise ValueError("invalid kernel record")
            except (ValueError, IndexError):
                status, reason = "partial", "invalid_record"
                continue
            if first_timestamp is None:
                first_timestamp = timestamp
            if previous is not None and sequence != previous + 1:
                status, reason = "partial", "sequence_gap"
            previous = sequence
            match = _XID.search(message)
            if match and (pci := _pci(match[1])) is not None:
                events.append({"pci_bus": pci, "xid": int(match[2]), "pid": int(match[3]),
                    "timestamp_monotonic": timestamp})
                # This is diagnostic evidence, not an unbounded host log archive.
                if len(events) > 1024:
                    events.pop(0)
                    status, reason = "partial", "event_limit"
        else:
            status, reason = "partial", "read_limit"
    finally:
        os.close(fd)
    return {"status": status, "reason": reason, "events": events, "first_timestamp": first_timestamp}


def owned_gpu_pids(observation):
    """Use only the two existing complete attribution samples, never guessed ancestry."""
    usage = observation.get("resource_usage", [])
    usage = [usage] if isinstance(usage, Mapping) else usage
    result = {}
    for item in usage if isinstance(usage, list) else []:
        if not isinstance(item, Mapping) or not isinstance(item.get("gpu_uuid"), str):
            continue
        pids = result.setdefault(item["gpu_uuid"], set())
        owned = item.get("owned_memory", {})
        for key in ("latest", "peak"):
            sample = owned.get(key) if isinstance(owned, Mapping) else None
            if not isinstance(sample, Mapping) or sample.get("complete") is not True:
                continue
            for process in sample.get("processes", []):
                if isinstance(process, Mapping) and type(process.get("pid")) is int and process["pid"] > 0:
                    pids.add(process["pid"])
    return result


def _clock_offset():
    return time.time() - time.monotonic()


def _same_incarnation(observation, event, offset, ticks):
    usage = observation.get("resource_usage", [])
    for item in [usage] if isinstance(usage, Mapping) else usage:
        if item.get("gpu_uuid") != event["gpu_uuid"]:
            continue
        for key in ("latest", "peak"):
            sample = item.get("owned_memory", {}).get(key)
            if not isinstance(sample, Mapping) or sample.get("complete") is not True:
                continue
            observed = sample.get("observed_at")
            if type(observed) not in {int, float} or not event["timestamp_monotonic"] <= observed - offset:
                continue
            for process in sample.get("processes", []):
                ancestry = process.get("ancestry", [])
                if process.get("pid") == event["pid"] and ancestry:
                    birth = ancestry[0].get("start_time_ticks")
                    if type(birth) is int and 0 <= birth / ticks <= event["timestamp_monotonic"]:
                        return True
    return False


def diagnose_gpu_failure(observation):
    if (observation.get("terminal_reason") != "FAILED" or observation.get("synthetic_before_spawn")
            or observation.get("resource_pressure") or observation.get("resource_failure") == "gpu_out_of_memory"):
        return None
    start, end = observation.get("started_at"), observation.get("ended_at")
    if not all(type(value) in {int, float} and math.isfinite(value) for value in (start, end)) or end < start:
        return None
    pids = owned_gpu_pids(observation)
    if not pids:
        return None
    identities, kernel = gpu_pci_identities(), read_kernel_xids()
    status, reason = kernel["status"], kernel["reason"]
    if status == "complete" and (kernel["first_timestamp"] is None or kernel["first_timestamp"] > start):
        status, reason = "partial", "window_not_covered"
    if not set(pids) <= set(identities.values()) and status != "unavailable":
        status, reason = "partial", "gpu_identity_unavailable"
    elif any(not values for values in pids.values()) and status == "complete":
        status, reason = "partial", "ownership_unavailable"
    offset, ticks = _clock_offset(), os.sysconf("SC_CLK_TCK")
    events = [{**event, "gpu_uuid": identities[event["pci_bus"]]} for event in kernel["events"]
        if start <= event["timestamp_monotonic"] <= end
        and event["pid"] in pids.get(identities.get(event["pci_bus"]), set())]
    events = [event for event in events if _same_incarnation(observation, event, offset, ticks)]
    if len(events) > _MAX_EVENTS:
        status, reason = "partial", "event_limit"
    return {"source": "linux_kmsg", "status": status, "reason": reason, "events": events[-_MAX_EVENTS:],
        "wall_monotonic_offset_seconds": offset, "clock_ticks_per_second": ticks}


def validate_gpu_diagnostic(observation, devices):
    diagnostic = observation.get("gpu_diagnostic")
    if diagnostic is None:
        return
    if (not isinstance(diagnostic, Mapping) or set(diagnostic) != {"source", "status", "reason", "events",
            "wall_monotonic_offset_seconds", "clock_ticks_per_second"}
            or diagnostic["source"] != "linux_kmsg" or diagnostic["status"] not in {"complete", "partial", "unavailable"}
            or diagnostic["reason"] not in {None, "permission_denied", "kernel_log_unavailable", "read_limit",
                "kernel_log_overrun", "read_failed", "invalid_record", "sequence_gap", "event_limit",
                "window_not_covered", "gpu_identity_unavailable", "ownership_unavailable"}
            or (diagnostic["status"] == "complete") != (diagnostic["reason"] is None)
            or not isinstance(diagnostic["events"], list) or len(diagnostic["events"]) > _MAX_EVENTS
            or diagnostic["status"] == "unavailable" and diagnostic["events"]
            or type(diagnostic["clock_ticks_per_second"]) is not int or diagnostic["clock_ticks_per_second"] <= 0
            or type(diagnostic["wall_monotonic_offset_seconds"]) not in {int, float}
            or not math.isfinite(diagnostic["wall_monotonic_offset_seconds"])
            or observation.get("terminal_reason") != "FAILED" or observation.get("synthetic_before_spawn")
            or observation.get("resource_pressure") or observation.get("resource_failure") == "gpu_out_of_memory"):
        raise ValueError("GPU diagnostic lacks bounded failed-Process evidence")
    pids = owned_gpu_pids(observation)
    for event in diagnostic["events"]:
        if (not isinstance(event, Mapping) or set(event) != {"gpu_uuid", "pci_bus", "xid", "pid", "timestamp_monotonic"}
                or event["gpu_uuid"] not in devices or not isinstance(event["pci_bus"], str)
                or _pci(event["pci_bus"]) != event["pci_bus"] or type(event["xid"]) is not int or not 0 < event["xid"] < 2**31
                or type(event["pid"]) is not int or event["pid"] not in pids.get(event["gpu_uuid"], set())
                or type(event["timestamp_monotonic"]) not in {int, float}
                or not observation["started_at"] <= event["timestamp_monotonic"] <= observation["ended_at"]
                or not _same_incarnation(observation, event, diagnostic["wall_monotonic_offset_seconds"],
                    diagnostic["clock_ticks_per_second"])):
            raise ValueError("GPU Xid differs from the owned GPU, PID or execution window")


def confirmed_gpu_fault(observation):
    if not isinstance(observation, Mapping) or observation.get("gpu_diagnostic") is None:
        return False
    try:
        validate_gpu_diagnostic(observation, tuple(owned_gpu_pids(observation)))
    except (ValueError, TypeError, KeyError):
        return False
    return any(event["xid"] == 38 for event in observation["gpu_diagnostic"]["events"])


def record_device_fault(host, tx, attempt, observation):
    """The existing run transition and Process closure share the same transaction."""
    if not confirmed_gpu_fault(observation):
        return
    spec = host.store.find_fact(attempt["run_id"], "ExperimentPreregistered", attempt["workflow_id"])
    source = host._tool_command(spec["payload"]["execution_contract"].get("source_submit_command_id")) if spec else None
    if source is None:
        return
    import json
    request = json.loads(source["request_json"])["source_request"]
    if request["schema_version"] < 30 or request["command_id"] in host.resource_recovery.blocked(attempt["run_id"]):
        return
    head = tx.latest_transition(attempt["run_id"])
    if head["state"] != "ACTIVE":
        return
    from .lifecycle import Command, RunState
    block = {"node_command_id": request["command_id"], "attempt_id": request["attempt_id"],
        "role": request["role"], "component_id": request["component_id"], "tool": "process",
        "code": "gpu_device_fault", "observed_at": time.time(), "process_attempt_id": attempt["attempt_id"]}
    tx.append_transition(host._transition_record(tx, attempt["run_id"], attempt["run_id"], "RUN",
        Command.RESOURCE_BLOCKED, RunState.ACTIVE, next_command=head["next_command"],
        payload={"resource_blocked": block}))
