"""Bounded live observations; OPEN is durable ownership, never a heartbeat."""
import json
import time

from .runner import RunningProcess


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
