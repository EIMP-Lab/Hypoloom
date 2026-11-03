"""Bounded live observations; OPEN is durable ownership, never a heartbeat."""
import json
import time

from .runner import RunningProcess


def sample_gpu_pressure(host, attempt, attempt_id, handle, pressure):
    """Observe one owned Process without treating another user's occupancy as its budget."""
    from .compute_resources import ResourceUnavailable, gpu_pressure_attribution
    devices = {}
    for tracker in pressure[0]:
        try:
            device = host.compute_resources.observe_runtime(tracker.summary.gpu_uuid)
            devices[device.uuid] = device
            tracker.sample(device)
        except ResourceUnavailable:
            pass
    budget = None
    if host.shared_gpu is not None:
        host.shared_gpu.sample_owned(attempt_id, handle,
            tuple(tracker.summary.gpu_uuid for tracker in pressure[0]), devices)
        budget = host.shared_gpu.pressure_workload_budget(attempt)
    summaries = tuple(tracker.summary for tracker in pressure[0])
    for summary in (item for item in summaries if item.pressure_exceeded):
        owned = (host.shared_gpu.owned_latest_memory_mib(attempt_id, summary.gpu_uuid)
            if host.shared_gpu is not None else None)
        pressure[2][summary.gpu_uuid] = (gpu_pressure_attribution(
            latest_free_memory_mib=summary.gpu_latest_free_memory_mib,
            owned_latest_memory_mib=owned, reserve_memory_mib=pressure[1],
            workload_memory_mib=budget), owned)
    cancel = any(pressure[2][item.gpu_uuid][0] not in {"external", "within_budget"}
        for item in summaries if item.pressure_exceeded)
    return summaries, cancel


def observe_gpu_failure(host, attempt, payload, stderr_path):
    host.shared_gpu.observe_oom(payload, stderr_path)
    if host.shared_gpu.pressure_workload_budget(attempt) is not None:
        from .gpu_diagnostics import diagnose_gpu_failure
        diagnostic = diagnose_gpu_failure(payload)
        if diagnostic is not None:
            payload["gpu_diagnostic"] = diagnostic


def process_activity(host, attempt):
    identity = attempt["attempt_id"]
    handle = host.running.get(identity)
    running = handle.returncode is None if isinstance(handle, RunningProcess) else None
    result = {"attempt_id": identity, "experiment_id": attempt["workflow_id"],
        "durable_state": attempt["state"], "observed_at": time.time(),
        "process_alive": running, "liveness_source": "owned_process" if running is not None else "unavailable",
        "pid": handle.pid if isinstance(handle, RunningProcess) else None,
        "resource_grant": None, "logs": [], "progress": "unknown"}
    spec = host.store.find_fact(attempt["run_id"], "ExperimentPreregistered", attempt["experiment_id"])
    if spec:
        result["resource_grant"] = spec["payload"].get("execution_contract", {}).get("resource_grant")
    bindings = host.store.inspect("SELECT payload_json FROM workflow_transitions WHERE run_id=? "
        "AND json_extract(payload_json,'$.attempt_id')=? AND json_type(payload_json,'$.resource_grant')='object'",
        (attempt["run_id"], identity))
    if len(bindings) == 1:
        result["resource_grant"] = json.loads(bindings[0]["payload_json"])["resource_grant"]
    for name in ("stdout.log", "stderr.log"):
        for category in ("work", "result-staging"):
            path = host.state_root / "artifacts" / category / identity / "logs" / name
            try:
                if path.is_symlink() or not path.is_file(): continue
                info = path.stat()
                result["logs"].append({"name": name, "bytes": info.st_size,
                    "last_modified_at": info.st_mtime,
                    "age_seconds": max(0, result["observed_at"] - info.st_mtime)})
                break
            except OSError: continue
    # Log activity is useful evidence but does not establish scientific progress.
    result["log_activity"] = "observed" if any(row["bytes"] for row in result["logs"]) else "silent_or_unavailable"
    return result
