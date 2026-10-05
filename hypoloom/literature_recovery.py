"""OpenAlex availability derived from existing Attempt observations/controls.

No independent service state store. Successful retrieval is observed separately
from the later model outcome. Concurrent failures belong to one recovery wave.
"""
from __future__ import annotations

import json
import math
import time


def service_failure(error):
    from .literature import LiteratureFailureCode as Code
    code, status = error.code, error.http_status
    if code not in {Code.OPENALEX_AUTH, Code.OPENALEX_RATE_LIMIT, Code.OPENALEX_TIMEOUT, Code.OPENALEX_HTTP}:
        return None
    if code is Code.OPENALEX_HTTP and status is not None and status not in {408, 429} and status < 500:
        return None
    return {"service": "openalex", "available": False, "reason": code.value.lower(),
            "http_status": status, "retry_after_seconds": getattr(error, "retry_after_seconds", None)}


def _payload(row):
    return row.get("observation") or json.loads(row.get("observation_json") or "{}")


def deferred_attempt(observation):
    value = observation.get("literature_service") or {}
    return (value.get("service") == "openalex" and value.get("available") is False
            and observation.get("failure_stage") == "literature_retrieval")


def points(attempts, transitions):
    result = []
    for row in attempts:
        observation = _payload(row)
        value = observation.get("literature_service") or observation.get("preflight_diagnostics", {}).get("literature_service")
        if not isinstance(value, dict) or value.get("service") != "openalex": continue
        at = value.get("observed_at", row.get("ended_at"))
        if type(at) not in (int, float) or not math.isfinite(at): continue
        if value.get("available") is False and not value.get("fallback_available") and row.get("state") != "CLOSED": continue
        result.append({**value, "at": at, "started_at": row.get("started_at", at),
            "identity": row["attempt_id"], "kind": "observation", "role": observation.get("request", {}).get("role")})
    for row in transitions:
        payload = row.get("payload") or json.loads(row.get("payload_json") or "{}")
        value = payload.get("literature_retry_clear") or {}
        at = value.get("cleared_at")
        if type(at) in (int, float) and math.isfinite(at):
            result.append({"at": at, "kind": "clear", "identity": row["command_id"]})
    return sorted(result, key=lambda p: (p["at"], p["kind"], p["identity"]))


def reduce_availability(history, *, now, attempt_limit=6):
    boundary = float("-inf"); failures = []; recovered_at = None; cleared_at = None
    active_service = "openalex"
    fallback_primary_failure = None
    for item in history:
        if item["kind"] == "clear":
            failures = []; boundary = item["at"]; cleared_at = item["at"]; recovered_at = None
            active_service = "openalex"; fallback_primary_failure = None
        elif item["started_at"] >= boundary:
            if item.get("available") is True or item.get("fallback_available") is True:
                boundary = item["started_at"]; failures = []; recovered_at = item["at"]
                active_service = "crossref" if item.get("fallback_available") is True else "openalex"
                fallback_primary_failure = item if active_service == "crossref" else None
            else:
                failures.append(item)
                active_service = "openalex"; fallback_primary_failure = None
    rounds = 0; last_end = float("-inf")
    for item in failures:
        if item["started_at"] >= last_end: rounds += 1
        last_end = max(last_end, item["at"])
    last = failures[-1] if failures else None
    operator = bool(last and last["reason"] == "openalex_auth")
    retry_at = None
    if last and not operator:
        # Exhausting the fast recovery rounds slows transient probes; it does
        # not turn an unattended network outage into a permanent operator wait.
        delay = 900.0 if rounds >= attempt_limit else min(30.0 * 2 ** min(max(0, rounds - 1), 4), 300.0)
        if type(last.get("retry_after_seconds")) in (int, float): delay = max(delay, last["retry_after_seconds"])
        retry_at = last["at"] + delay
    state = ("waiting_operator" if operator else "cooldown" if retry_at is not None and retry_at > now
             else "recovering" if last or cleared_at is not None and recovered_at is None else "ready")
    return {"service": "openalex", "state": state, "pending": state in {"cooldown", "waiting_operator"},
        "active_service": active_service,
        "primary_failure": None if fallback_primary_failure is None else fallback_primary_failure.get("reason"),
        "requires_operator": operator, "reason": None if last is None else last["reason"],
        "http_status": None if last is None else last.get("http_status"), "next_retry_at": retry_at,
        "recovery_attempts": rounds, "attempt_limit": attempt_limit, "recovered_at": recovered_at,
        "affected_roles": sorted({p["role"] for p in failures if p.get("role")})}


def status(store, run_id, *, now=None):
    def read():
        attempts = store.inspect("SELECT attempt_id,state,started_at,ended_at,observation_json FROM execution_attempts "
            "WHERE run_id=? AND (json_type(observation_json,'$.literature_service')='object' "
            "OR json_type(observation_json,'$.preflight_diagnostics.literature_service')='object')", (run_id,))
        transitions = store.inspect("SELECT command_id,payload_json FROM workflow_transitions WHERE run_id=? AND json_type(payload_json,'$.literature_retry_clear')='object'", (run_id,))
        return points(attempts, transitions)
    history = store.cached_read(("literature-availability", run_id), read)
    return reduce_availability(history, now=time.time() if now is None else now)


def events(run_id, attempts, transitions):
    from .observation_api import _event
    seen = []; signature = ("ready", None); result = []
    for item in points(attempts, transitions):
        seen.append(item)
        current = reduce_availability(seen, now=item["at"])
        new = (current["state"], current["reason"])
        if item["kind"] == "clear": kind = "literature_retry_requested"
        elif new == signature: continue
        elif current["state"] == "ready" and signature[0] != "ready": kind = "literature_recovered"
        elif current["pending"]: kind = "literature_waiting"
        else: continue
        result.append(_event("literature-service", item["identity"], 1, kind, item["at"], run_id, **current))
        signature = new
    return result
