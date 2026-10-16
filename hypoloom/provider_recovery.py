"""Operator API recovery through the existing transactional control journal."""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

from .control_protocol import ControlRequest, ControlResponse
from .host_settings import HostConfigurationError, provider_recovery_reused, read_provider_connection
from .lifecycle import Command, RunState


def scheduled_retry(host, run_id):
    head = host.store.latest_transition(run_id)
    if head is None or head["state"] not in {"CREATED", "ACTIVE"}:
        return None
    rows = host.store.inspect("SELECT command_id,payload_json,created_at FROM workflow_transitions "
        "WHERE run_id=? AND workflow_kind='RUN' AND "
        "(json_type(payload_json,'$.provider_retry_schedule')='object' OR "
        "json_type(payload_json,'$.provider_retry_clear')='object') ORDER BY revision DESC LIMIT 1", (run_id,))
    if not rows:
        return None
    row = rows[0]
    value = json.loads(row["payload_json"]).get("provider_retry_schedule", {})
    if value.get("retry_at") is None:
        return None
    return {"retry_at": value["retry_at"], "request_id": row["command_id"], "requested_at": row["created_at"]}


def retry_control(host, request, *, scheduled=False):
    payload = request.payload
    allowed = ({"run_id", "reason"}, {"run_id", "reason", "settings_path"}, {"run_id", "reason", "retry_at"})
    if set(payload) not in allowed:
        raise ValueError("PROVIDER_RETRY_CLEAR requires run_id, reason and either settings_path or retry_at")
    run_id = payload["run_id"]
    if not isinstance(run_id, str) or payload["reason"] != "operator":
        raise ValueError("provider retry requires the operator reason")

    def operation(tx):
        head = tx.latest_transition(run_id)
        if head is None or head["workflow_kind"] != "RUN": raise ValueError(f"unknown run {run_id!r}")
        if head["state"] not in {"CREATED", "ACTIVE"}:
            raise ValueError("provider retry requires a CREATED or ACTIVE run")
        if "retry_at" in payload:
            at = payload["retry_at"]
            if at is not None:
                if type(at) not in (int, float) or not math.isfinite(at) or not time.time() < at <= 253402300799:
                    raise ValueError("retry_at must be a finite future Unix timestamp")
                status = host._provider_retry_status(run_id)
                if status["state"] not in {"cooldown", "waiting_operator", "recovering"}:
                    raise ValueError("schedule a retry only while the API is waiting")
            effect = {"provider_retry_schedule": {"reason": "operator", "retry_at": at}}
        else:
            if "settings_path" in payload:
                if not isinstance(payload["settings_path"], str): raise ValueError("settings_path must be an absolute file path")
                if host.claude_agent_host is None: raise ValueError("API switching requires the provider Agent adapter")
                host._check_agent_runtime()
                try: read_provider_connection(Path(payload["settings_path"]))
                except HostConfigurationError as exc: raise ValueError(str(exc)) from None
            elif not scheduled and provider_recovery_reused(host, run_id):
                return {"status": "accepted", "revision": head["revision"], "result": {
                    "run_id": run_id, "run_state": head["state"], "recovery_already_requested": True}}
            # A due appointment is consumed even when its connection file is now
            # unavailable. The normal connection validator still blocks dispatch.
            effect = {"provider_retry_clear": {"reason": "operator", "cleared_at": time.time(),
                **({"settings_path": payload["settings_path"]} if "settings_path" in payload else {})}}
        row = host._transition_record(tx, run_id, run_id, "RUN", Command.PROVIDER_RETRY_CLEAR,
            RunState(head["state"]), request.request_id, payload=effect)
        tx.append_transition(row)
        return {"status": "accepted", "revision": row["revision"], "result": {"run_id": run_id, "run_state": head["state"]}}

    stored = host.store.execute_command({"command_id": request.request_id, "run_id": run_id, "workflow_id": run_id,
        "expected_revision": request.expected_revision, "command_type": Command.PROVIDER_RETRY_CLEAR.value,
        "request": request.to_mapping()}, operation)
    if stored.status != "accepted":
        error = stored.error or {"code": "rejected", "message": "provider retry rejected"}
        return ControlResponse.failure(request.request_id, "rejected", str(error.get("code")), str(error.get("message")), stored.revision)
    return ControlResponse.accepted(request.request_id, stored.revision,
        {**dict(stored.result or {}), "provider_retry": host._provider_retry_status(run_id)})


def apply_scheduled_retry(host, run_id):
    schedule = scheduled_retry(host, run_id)
    if schedule is None or schedule["retry_at"] > time.time() or host._run_wallclock_expired(run_id):
        return False
    request = ControlRequest(1, "provider-scheduled-" + schedule["request_id"], None,
        "PROVIDER_RETRY_CLEAR", {"run_id": run_id, "reason": "operator"})
    retry_control(host, request, scheduled=True)
    return True


def parse_retry_time(value, *, now=None):
    """User time in the TUI's local zone; DST folds/gaps need an explicit offset."""
    import re
    from datetime import datetime
    now = time.time() if now is None else now
    value = value.strip()
    if len(value) > 64:
        raise ValueError("Retry time is outside the supported range.")
    relative = re.fullmatch(r"([1-9][0-9]*)\s*(m|min|minutes?|分钟后|h|hours?|小时后)", value, re.I)
    if relative:
        amount, unit = relative.groups()
        result = now + int(amount) * (3600 if unit.lower() in {"h", "hour", "hours", "小时后"} else 60)
    else:
        if re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", value):
            value = datetime.fromtimestamp(now).date().isoformat() + " " + value
        if not re.match(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}", value):
            raise ValueError("Use YYYY-MM-DD HH:MM, today's HH:MM, or 30m / 2h.")
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is not None:
                result = parsed.timestamp()
            else:
                # libc knows the local zone's rules at the requested date.
                choices = set()
                for dst in (-1, 0, 1):
                    try: stamp = time.mktime((*parsed.timetuple()[:8], dst)) + parsed.microsecond / 1e6
                    except (OverflowError, OSError): continue
                    if datetime.fromtimestamp(stamp) == parsed:
                        choices.add(stamp)
                if len(choices) != 1:
                    raise ValueError("Local time is missing or ambiguous; include a UTC offset such as +08:00.")
                result = choices.pop()
        except (OverflowError, OSError):
            raise ValueError("Retry time is outside the supported range.") from None
    if not math.isfinite(result) or not now < result <= 253402300799:
        raise ValueError("Retry time must be in the future.")
    return result
