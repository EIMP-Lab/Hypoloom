"""Bounded observation of provider labels/usage; never a billing authority."""
from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


_TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")

INPUT_TOO_LONG_PHRASES = (
    "prompt is too long", "prompt too long", "context length exceeded",
    "maximum context length", "input is too long")

PROVIDER_UNAVAILABLE = frozenset({"auth_failed", "rate_limited", "quota_exhausted"})


def provider_status_text(status):
    if not status:
        return "API status unknown: a live Host observation is required."
    state = status.get("state", "cooldown" if status.get("pending") else "ready")
    if state == "ready": return "API dispatch available."
    if state == "recovering": return "Trying API recovery with one pending task; access is not yet confirmed."
    reason = {"auth_failed": "authentication rejected", "rate_limited": "rate limit (quota cause unconfirmed)",
        "quota_exhausted": "quota exhausted", "connection_configuration": "connection configuration unavailable"}.get(status.get("reason"), "provider rejected access")
    until = status.get("next_retry_at")
    if until is None:
        until = max((g["until"] for g in status.get("gates", []) if type(g.get("until")) in (int, float)), default=None)
    when = (datetime.fromtimestamp(until, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if until is not None and not status.get("requires_operator") else "operator action required")
    return f"API unavailable: {reason}. New Agent dispatch paused. Next attempt: {when}. Submitted experiments continue."


class ProviderObservation:
    def __init__(self):
        self.buffer = b""
        self.dropping_line = False
        self.models = set()
        self.usage = {}
        self.stop_reason = None
        self.message_complete = False
        self.first_content_at = None
        self.json_response = None
        self.provider_error_code = None

    def _event(self, value):
        if not isinstance(value, dict):
            return
        kind = value.get("type")
        message = value.get("message") if kind == "message_start" else value
        if kind in {"message_start", "message"} and isinstance(message, dict):
            model = message.get("model")
            if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/\[\]-]{0,255}", model):
                self.models.add(model)
            self._usage(message.get("usage"))
            if kind == "message":
                self._stop(message.get("stop_reason"))
                self.message_complete = self.stop_reason is not None
        if kind == "message_delta":
            self._usage(value.get("usage"))
            delta = value.get("delta")
            if isinstance(delta, dict):
                self._stop(delta.get("stop_reason"))
        if kind == "message_stop":
            self.message_complete = True
        if kind == "error":
            self.message_complete = False
            error = value.get("error")
            if isinstance(error, dict):
                message = str(error.get("message", ""))[:4096].lower()
                if error.get("type") in {"insufficient_quota", "quota_exceeded", "usage_limit_exceeded"}:
                    self.provider_error_code = "quota_exhausted"
                elif error.get("type") == "rate_limit_error":
                    self.provider_error_code = "rate_limited"
                elif any(phrase in message for phrase in INPUT_TOO_LONG_PHRASES):
                    self.provider_error_code = "input_too_long"
        if kind == "content_block_delta" and self.first_content_at is None:
            self.first_content_at = time.time()

    def _stop(self, value):
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
            self.stop_reason = value

    def _usage(self, value):
        if isinstance(value, dict):
            for key in _TOKEN_FIELDS:
                if type(value.get(key)) is int and value[key] >= 0:
                    self.usage[key] = value[key]

    def feed(self, chunk):
        if self.json_response is None:
            chunk = chunk.lstrip()
            if not chunk:
                return
            self.json_response = chunk.startswith(b"{")
        if self.json_response:
            if len(self.buffer) + len(chunk) <= 1024 * 1024:
                self.buffer += chunk
            else:
                self.dropping_line = True
            return
        self.buffer += chunk
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            if self.dropping_line:
                self.dropping_line = False
                continue
            if not line.startswith(b"data:"):
                continue
            try:
                self._event(json.loads(line[5:]))
            except (ValueError, UnicodeError, RecursionError):
                pass
        if len(self.buffer) > 1024 * 1024:
            self.buffer = b""
            self.dropping_line = True

    def finish(self):
        if self.json_response and not self.dropping_line:
            try:
                self._event(json.loads(self.buffer))
            except (ValueError, UnicodeError, RecursionError):
                pass
        return {"served_models": sorted(self.models),
            "served_model": next(iter(self.models)) if len(self.models) == 1 else None,
            "stop_reason": self.stop_reason, "usage": dict(self.usage),
            "provider_error_code": self.provider_error_code,
            "message_complete": self.message_complete, "first_content_at": self.first_content_at}


def summarize_exchanges(exchanges):
    """Complete observed generation usage, separate from terminal billing."""
    result = dict.fromkeys((*_TOKEN_FIELDS, "total_tokens", "completed_requests", "unmeasured_requests"), 0)
    for exchange in exchanges:
        if exchange.get("request_path") != "/v1/messages":
            continue
        usage = exchange.get("usage")
        if (exchange.get("upstream_status") != 200
                or exchange.get("message_complete") is not True
                or exchange.get("response_body_complete") is not True
                or not isinstance(usage, dict)
                or any(type(usage.get(key)) is not int or usage[key] < 0
                       for key in ("input_tokens", "output_tokens"))
                or any(type(usage.get(key, 0)) is not int or usage.get(key, 0) < 0 for key in _TOKEN_FIELDS)):
            result["unmeasured_requests"] += 1
            continue
        for key in _TOKEN_FIELDS:
            result[key] += usage.get(key, 0)
        result["completed_requests"] += 1
    result["total_tokens"] = sum(result[key] for key in _TOKEN_FIELDS)
    return result


def observed_input_floor(exchanges):
    """Wire-final input floor from complete single-model generation exchanges.

    The transcript's per-message usage can carry the provider's stream-start
    snapshot, which may exceed the final per-request usage the bill actually
    covers; the wire final usage is the same quantity as the ledger.
    """
    summary = summarize_exchanges(exchanges)
    if summary["unmeasured_requests"] or not summary["completed_requests"]:
        return None
    models = {model for exchange in exchanges
        if exchange.get("request_path") == "/v1/messages"
        for model in exchange.get("served_models", ())}
    if len(models) != 1:
        return None
    return sum(summary[key] for key in (
        "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


def provider_retry_gate(entries, *, cooldown_seconds, cleared_at, now, attempt_limit=3):
    """Reduce durable provider-failure rows to the current dispatch gate.

    A clear admits subsequent invocations; failures of those new invocations
    re-engage the gate. Earlier in-flight work is not a recovery invocation.
    """
    entries = list(entries)
    def started(row):
        value = row.get("started_at", row.get("ended_at"))
        return float(value) if type(value) in (int, float) and math.isfinite(value) else 0.0
    # A clear applies to subsequent invocations, including after a connection
    # switch. Late responses from an older invocation cannot undo it.
    eligible = [row for row in entries if cleared_at is None or started(row) >= cleared_at]
    successes = [row for row in eligible if type(row.get("ended_at")) in (int, float)
        and math.isfinite(row["ended_at"]) and row.get("provider_success")]
    recovery = max(successes, key=lambda row: (started(row), row["ended_at"]), default=None)
    recovered_at = None if recovery is None else recovery["ended_at"]
    gates = []
    for row in eligible:
        ended_at = row.get("ended_at")
        if type(ended_at) not in (int, float) or not math.isfinite(ended_at):
            continue
        if recovery is not None and (started(row) < started(recovery) or row.get("attempt_id") == recovery.get("attempt_id")):
            continue
        until = None
        if row.get("code") in {"rate_limited", "quota_exhausted"}:
            retry_at = row.get("retry_at")
            if type(retry_at) in (int, float) and math.isfinite(retry_at):
                until = float(retry_at)
            elif row.get("code") == "rate_limited":
                until = float(ended_at) + 60.0
        elif row.get("code") == "auth_failed" and cooldown_seconds >= 0:
            until = float(ended_at) + cooldown_seconds
        if until is None and row.get("code") != "quota_exhausted":
            continue
        gates.append({"class": row.get("code"), "attempt_id": row.get("attempt_id"),
            "http_status": row.get("http_status"), "attempt_ended_at": float(ended_at),
            "started_at": started(row),
            "until": until, "pending": until is None or until > now})
    gates.sort(key=lambda item: (item["attempt_ended_at"], item["attempt_id"]))
    # Concurrent failures belong to one dispatch wave. Only a subsequent
    # invocation spends another automatic recovery opportunity.
    rounds, last_end = 0, float("-inf")
    for item in gates:
        if item["started_at"] >= last_end:
            rounds += 1
        last_end = max(last_end, item["attempt_ended_at"])
    operator = rounds >= attempt_limit or any(item["until"] is None for item in gates)
    waiting = operator or any(item["pending"] for item in gates)
    recovering = not waiting and (bool(gates) or cleared_at is not None and recovered_at is None)
    first_failure = min((item["attempt_ended_at"] for item in gates), default=float("-inf"))
    boundary = max(first_failure, cleared_at if cleared_at is not None else float("-inf"))
    in_flight = recovering and any(row.get("state") == "OPEN" and started(row) >= boundary for row in eligible)
    state = "waiting_operator" if operator else "cooldown" if waiting else "recovering" if recovering else "ready"
    return {"pending": waiting or in_flight, "state": state, "requires_operator": operator,
        "reason": gates[-1]["class"] if gates else None,
        "next_retry_at": None if operator else max((g["until"] for g in gates if g["until"] is not None), default=None),
        "recovery_attempts": rounds, "attempt_limit": attempt_limit, "recovered_at": recovered_at,
        "cooldown_seconds": cooldown_seconds, "cleared_at": cleared_at,
        "gates": gates, "now": now}


def retry_after_seconds(value, *, now):
    """Parse the standard Retry-After header, never a timezone-free message."""
    if not isinstance(value, str):
        return None
    try:
        if value.isascii() and value.isdigit():
            seconds = float(value)
        else:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                return None
            seconds = parsed.timestamp() - now
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except (ValueError, TypeError, OverflowError):
        return None


def provider_failure(exchanges):
    """Last transport outcome; a subsequent success clears a transient error."""
    if not exchanges:
        return None
    last = exchanges[-1]
    code = last.get("provider_error_code") or last.get("failure_reason")
    status = last.get("upstream_status")
    if code in {"input_too_long", "token_budget_exceeded"}:
        return {"code": code, "http_status": status}
    if code == "quota_exhausted":
        ended_at = last.get("ended_at")
        seconds = retry_after_seconds((last.get("response_headers") or {}).get("retry-after"), now=ended_at) if type(ended_at) in (int, float) else None
        return {"code": code, "http_status": status,
            **({"retry_at": ended_at + seconds, "retry_after_seconds": seconds} if seconds is not None else {})}
    if status in (401, 403):
        # Credential rejected (rolling quota window or rotation). The Host owns
        # the cooldown length; the exchange only proves the rejection class.
        return {"code": "auth_failed", "http_status": status}
    if status == 429 or code == "rate_limited":
        ended_at = last.get("ended_at")
        if type(ended_at) not in (int, float) or not math.isfinite(ended_at):
            return {"code": "rate_limited", "http_status": status}
        headers = last.get("response_headers") or {}
        seconds = retry_after_seconds(headers.get("retry-after"), now=ended_at)
        # No trusted reset time: bounded ordinary retry delay, existing caps apply.
        seconds = 60.0 if seconds is None else seconds
        return {"code": "rate_limited", "http_status": status,
            "retry_after_seconds": seconds, "retry_at": ended_at + seconds}
    return None


def read_exchanges(directory):
    """Derive old and new exchanges from existing diagnostic files, read-only."""
    result = []
    for child in sorted(directory.glob("[0-9][0-9][0-9][0-9][0-9][0-9]")):
        try:
            if child.is_symlink():
                continue
            path = child / "exchange.json"
            if path.is_symlink() or path.stat().st_size > 65536:
                continue
            data = json.loads(path.read_bytes())
            if not isinstance(data, dict):
                continue
            if "served_models" not in data:
                source = child / "response.body"
                if source.is_symlink() or not source.is_file():
                    result.append(data)
                    continue
                parser = ProviderObservation()
                with source.open("rb") as stream:
                    for _ in range(128):
                        chunk = stream.read(65536)
                        if not chunk:
                            break
                        parser.feed(chunk)
                observed = parser.finish()
                # Reading an old file now cannot recover token arrival time.
                observed["first_content_at"] = None
                data.update(observed)
            result.append(data)
        except (OSError, ValueError, UnicodeError, RecursionError):
            continue
    return result
