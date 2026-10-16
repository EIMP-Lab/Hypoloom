"""Bounded observation of provider labels/usage; never a billing authority."""
from __future__ import annotations

import json
import math
import re
import time
from typing import Any
from email.utils import parsedate_to_datetime


_TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")

INPUT_TOO_LONG_PHRASES = (
    "prompt is too long", "prompt too long", "context length exceeded",
    "maximum context length", "input is too long")

PROVIDER_TRANSIENT = frozenset({"rate_limited", "service_unavailable", "usage_window_exhausted"})
PROVIDER_OPERATOR_REQUIRED = frozenset({"subscription_expired", "access_denied"})
PROVIDER_UNAVAILABLE = PROVIDER_TRANSIENT | PROVIDER_OPERATOR_REQUIRED | {"auth_failed", "quota_exhausted"}
# Local queue expiry retries its task without claiming a remote outage.
PROVIDER_DEFERRED = PROVIDER_UNAVAILABLE | {"local_request_queue"}
PROVIDER_RETRY_DELAYS = (60, 120, 240, 480, 900)


def provider_retry_delay(rounds):
    return PROVIDER_RETRY_DELAYS[min(max(rounds - 1, 0), len(PROVIDER_RETRY_DELAYS) - 1)]


def provider_service(base_url):
    """Select local error rules from the connection, never the model's identity.

    Unknown gateways retain compatible-protocol rules. See the user guide at
    docs/guides/provider-recovery.md for classification and recovery behavior.
    """
    from urllib.parse import urlsplit
    try:
        endpoint = urlsplit(base_url)
        host, path = endpoint.hostname, endpoint.path.rstrip("/")
        if endpoint.scheme not in {"http", "https"}: return "generic"
    except (TypeError, ValueError):
        return "generic"
    if host in {"api.kimi.com", "api.kimi.ai"} and path in {"/coding", "/coding/v1"}:
        return "kimi_code"
    if host == "api.anthropic.com" and path in {"", "/v1"}: return "anthropic"
    if host == "api.xiaomimimo.com" and path == "/anthropic": return "mimo_api"
    if host in {"token-plan-cn.xiaomimimo.com", "token-plan-sgp.xiaomimimo.com",
                "token-plan-ams.xiaomimimo.com"} and path == "/anthropic":
        return "mimo_token_plan"
    if host == "api.deepseek.com" and path == "/anthropic": return "deepseek"
    if path == "/api/anthropic":
        if host == "open.bigmodel.cn": return "glm_cn"
        if host == "api.z.ai": return "glm_global"
    return "generic"


# Official business codes shared by BigModel/Z.ai, scoped to their own endpoints.
# Long-period quotas without a usable reset retain operator recovery; explicitly
# renewable short windows reuse ordinary transient backoff.
_GLM_ERRORS = {
    **dict.fromkeys(("1000", "1001", "1003", "1005"), "auth_failed"),
    **dict.fromkeys(("1113", "1310", "1317", "1319", "1321"), "quota_exhausted"),
    **dict.fromkeys(("1220", "1311", "1313", "1315"), "access_denied"),
    **dict.fromkeys(("1200", "1230", "1234", "1305"), "service_unavailable"),
    **dict.fromkeys(("1309", "1314"), "subscription_expired"),
    **dict.fromkeys(("1308", "1316", "1318", "1320"), "usage_window_exhausted"),
    "1261": "input_too_long", "1302": "rate_limited",
}
_GLM_RESET_CODES = frozenset({"1308", "1310", "1316", "1317", "1318", "1319", "1320", "1321"})


def _glm_code(error, service):
    value = error.get("code")
    return str(value) if service in {"glm_cn", "glm_global"} and type(value) in (str, int) else None


def _provider_reset_at(error, service):
    """Extract only an explicitly zoned reset in a documented quota message.

    The docs name next_flush_time but do not specify its zone/format. A naive
    timestamp, duration or placeholder cannot establish an absolute instant.
    """
    if _glm_code(error, service) not in _GLM_RESET_CODES: return None
    message = error.get("message")
    if not isinstance(message, str): return None
    match = re.search(r"(?:您的限额将在\s*|(?:Your limit will reset at|Resets at)\s+)"
        r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d))"
        r"(?=\s|[.。]|$)", message[:4096], re.IGNORECASE)
    if match is None or match[1].endswith("-00:00"): return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(match[1].upper().replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def provider_error(error, *, service="generic"):
    """The local codebook: normalize only documented/observed error evidence."""
    message = str(error.get("message", ""))[:4096].lower().strip()
    kind = error.get("type")
    code = error.get("code")
    if not isinstance(kind, str): kind = None
    codes = {value for value in (kind, code) if isinstance(value, str)}
    if codes & {"authentication_error", "invalid_api_key"}:
        return "auth_failed"
    if normalized := _GLM_ERRORS.get(_glm_code(error, service)):
        return normalized
    # Preserve the observed compatible-gateway signature; duration is not delay.
    if (kind == "permission_error" or service == "kimi_code" and kind is None) and re.match(
            r"you've reached your ([1-9][0-9]*-hour) usage limit\.\s+"
            r"your quota will reset when the current \1 window ends\.", message):
        return "usage_window_exhausted"
    if codes & {"insufficient_quota", "quota_exceeded", "usage_limit_exceeded"}:
        return "quota_exhausted"
    if service == "kimi_code" and kind in {None, "permission_error"} and (
            message.startswith("you've reached your weekly (7-day) usage limit.") or
            message.startswith("you've reached your monthly usage limit for this billing cycle.")):
        return "quota_exhausted"
    if codes & {"rate_limit_error", "rate_limit_exceeded"}:
        return "rate_limited"
    if codes & {"overloaded_error", "api_error", "timeout_error"}:
        return "service_unavailable"
    if (any(phrase in message for phrase in INPUT_TOO_LONG_PHRASES)
            or code == "context_length_exceeded"
            or service == "kimi_code" and "your request exceeded model token limit:" in message):
        return "input_too_long"
    return None


def provider_status_text(status, *, language="en"):
    from functools import partial
    from .ui_i18n import format_local_time, translate
    tr = partial(translate, language=language)
    if not status:
        return tr("API status unknown: a live Host observation is required.")
    state = status.get("state", "cooldown" if status.get("pending") else "ready")
    if schedule := status.get("scheduled_retry"):
        seconds = max(0, math.ceil(schedule["retry_at"] - time.time()))
        return tr("API retry scheduled for {0} ({1}s remaining). Automatic retries paused; submitted experiments continue.",
            format_local_time(schedule["retry_at"]), seconds)
    if state == "ready": return tr("API dispatch available.")
    if state == "recovering": return tr("Trying API recovery with one pending task; access is not yet confirmed.")
    reason = {"auth_failed": "authentication rejected", "rate_limited": "rate limit (quota cause unconfirmed)",
        "usage_window_exhausted": "usage window quota exhausted",
        "quota_exhausted": "quota exhausted", "service_unavailable": "provider temporarily unavailable",
        "subscription_expired": "subscription expired; renew or switch API",
        "access_denied": "access restricted; check permissions or contact the provider",
        "connection_configuration": "connection configuration unavailable"}.get(status.get("reason"), "provider rejected access")
    until = status.get("next_retry_at")
    if until is None:
        until = max((g["until"] for g in status.get("gates", []) if type(g.get("until")) in (int, float)), default=None)
    when = (format_local_time(until)
        if until is not None and not status.get("requires_operator") else "operator action required")
    return tr("API unavailable: {0}. New Agent dispatch paused. Next attempt: {1}. Submitted experiments continue.", tr(reason), tr(when))


class ProviderObservation:
    def __init__(self, *, service="generic"):
        self.service = service
        self.buffer = b""
        self.dropping_line = False
        self.models = set()
        self.usage = {}
        self.invalid_usage = False
        self.stop_reason = None
        self.message_complete = False
        self.first_content_at = None
        self.json_response = None
        self.provider_error_code = None
        self.provider_retry_at = None

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
        if kind == "error" or kind is None and isinstance(value.get("error"), dict):
            self.message_complete = False
            error = value.get("error")
            if isinstance(error, dict):
                self.provider_error_code = provider_error(error, service=self.service)
                self.provider_retry_at = _provider_reset_at(error, self.service)
        # Some compatible providers put content directly in a start event.
        # Its absence is also used to distinguish an empty terminal SSE.
        block = value.get("content_block")
        start_content = (kind == "content_block_start" and isinstance(block, dict)
            and any(v for k, v in block.items() if k != "type"))
        message_content = kind in {"message_start", "message"} and isinstance(message, dict) and message.get("content")
        if (kind == "content_block_delta" or start_content or message_content) and self.first_content_at is None:
            self.first_content_at = time.time()

    def _stop(self, value):
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
            self.stop_reason = value

    def _usage(self, value):
        if isinstance(value, dict):
            for key in _TOKEN_FIELDS:
                if type(value.get(key)) is int and value[key] >= 0:
                    self.usage[key] = value[key]
                elif key in value:
                    self.invalid_usage = True

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
            "usage_valid": not self.invalid_usage,
            "provider_error_code": self.provider_error_code,
            **({"provider_retry_at": self.provider_retry_at} if self.provider_retry_at is not None else {}),
            "message_complete": self.message_complete, "first_content_at": self.first_content_at}


def empty_generation_response(exchange):
    """Recognize the observed zero-usage terminal SSE, not a model response.

    Keep its raw exchange, but do not use an absent model/content as recovery
    or input measurement. Missing/partial/invalid usage remains unknown.
    """
    usage = exchange.get("usage")
    headers = exchange.get("response_headers")
    return (exchange.get("request_path") == "/v1/messages"
        and isinstance(exchange.get("requested_model"), str) and bool(exchange["requested_model"])
        and exchange.get("upstream_status") == 200
        and isinstance(headers, dict) and headers.get("content-type") == "text/event-stream"
        and exchange.get("response_body_complete") is True
        and exchange.get("message_complete") is True and exchange.get("usage_valid") is True
        and exchange.get("served_models") == [] and exchange.get("stop_reason") == "end_turn"
        and "first_content_at" in exchange and exchange["first_content_at"] is None
        and exchange.get("failure_reason") is None and exchange.get("provider_error_code") is None
        and isinstance(usage, dict)
        and all(type(usage.get(key)) is int and usage[key] == 0 for key in ("input_tokens", "output_tokens"))
        and all(type(usage.get(key, 0)) is int and usage.get(key, 0) == 0 for key in _TOKEN_FIELDS))


def summarize_exchanges(exchanges):
    """Complete observed generation usage, separate from terminal billing."""
    from .context_management import input_usage
    result = dict.fromkeys((*_TOKEN_FIELDS, "total_tokens", "completed_requests", "unmeasured_requests"), 0)
    for exchange in exchanges:
        if exchange.get("request_path") != "/v1/messages":
            continue
        if empty_generation_response(exchange):
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
        tokens = input_usage(usage, (exchange.get("context_capacity") or {}).get(
            "input_usage_semantics", "separate_cache"))
        if tokens is None:
            result["unmeasured_requests"] += 1
            continue
        for key in _TOKEN_FIELDS:
            result[key] += usage.get(key, 0)
        result["total_tokens"] += tokens + usage["output_tokens"]
        result["completed_requests"] += 1
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
    return summary["total_tokens"] - summary["output_tokens"]


def provider_retry_gate(entries, *, cooldown_seconds, cleared_at, now, attempt_limit=3):
    """Reduce durable provider-failure rows to the current dispatch gate."""
    return observe_provider_retry(provider_retry_history(entries, cooldown_seconds=cooldown_seconds,
        cleared_at=cleared_at, attempt_limit=attempt_limit), now=now)


def provider_retry_history(entries, *, cooldown_seconds, cleared_at, attempt_limit=3):
    """Prepare committed history without freezing cooldown or recovery time.

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
        if row.get("code") in PROVIDER_TRANSIENT | {"quota_exhausted"}:
            retry_at = row.get("retry_at")
            if type(retry_at) in (int, float) and math.isfinite(retry_at):
                until = float(retry_at)
            elif row.get("code") in PROVIDER_TRANSIENT:
                until = float(ended_at) + provider_retry_delay(1)
        elif row.get("code") == "auth_failed" and cooldown_seconds >= 0:
            until = float(ended_at) + cooldown_seconds
        if until is None and row.get("code") not in PROVIDER_OPERATOR_REQUIRED | {"quota_exhausted"}:
            continue
        gates.append({"class": row.get("code"), "attempt_id": row.get("attempt_id"),
            "http_status": row.get("http_status"), "attempt_ended_at": float(ended_at),
            "started_at": started(row),
            "until": until})
    gates.sort(key=lambda item: (item["attempt_ended_at"], item["attempt_id"]))
    # Concurrent failures belong to one dispatch wave. Only a subsequent
    # invocation spends another automatic recovery opportunity.
    rounds, last_end = 0, float("-inf")
    for item in gates:
        if item["started_at"] >= last_end:
            rounds += 1
        last_end = max(last_end, item["attempt_ended_at"])
        if item['class'] in PROVIDER_TRANSIENT:
            item['until'] = max(item['until'], item['attempt_ended_at'] + provider_retry_delay(rounds))
    operator = (rounds >= attempt_limit and any(item['class'] not in PROVIDER_TRANSIENT for item in gates)
        or any(item["until"] is None for item in gates))
    first_failure = min((item["attempt_ended_at"] for item in gates), default=float("-inf"))
    boundary = max(first_failure, cleared_at if cleared_at is not None else float("-inf"))
    return {"open_recovery": any(row.get("state") == "OPEN" and started(row) >= boundary for row in eligible),
        "requires_operator": operator,
        "reason": gates[-1]["class"] if gates else None,
        "next_retry_at": None if operator else max((g["until"] for g in gates if g["until"] is not None), default=None),
        "recovery_attempts": rounds, "attempt_limit": attempt_limit, "recovered_at": recovered_at,
        "cooldown_seconds": cooldown_seconds, "cleared_at": cleared_at,
        "gates": gates}


def observe_provider_retry(history, *, now):
    """Evaluate the prepared history at the current request's clock."""
    status = dict(history)
    gates = [{**gate, 'pending': gate['until'] is None or gate['until'] > now} for gate in status['gates']]
    operator = status['requires_operator']
    waiting = operator or any(gate['pending'] for gate in gates)
    recovering = not waiting and (bool(gates) or status['cleared_at'] is not None and status['recovered_at'] is None)
    in_flight = status.pop('open_recovery') and recovering
    state = "waiting_operator" if operator else "cooldown" if waiting else "recovering" if recovering else "ready"
    status.update(pending=waiting or in_flight, state=state, gates=gates, now=now)
    return status


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


def provider_failure(exchanges, *, fresh_session: bool | None = None):
    """Last transport outcome; a subsequent success clears a transient error."""
    if not exchanges:
        return None
    last = next((item for item in reversed(exchanges) if item.get("request_path") != "/v1/messages/count_tokens"), exchanges[-1])
    code = last.get("provider_error_code") or last.get("failure_reason")
    status = last.get("upstream_status")
    if status == 401 or code == "auth_failed":
        return {"code": "auth_failed", "http_status": status}
    service = last.get("provider_service", "generic")
    if code in PROVIDER_OPERATOR_REQUIRED:
        return {"code": code, "http_status": status}
    if status == 402 and service in {"mimo_api", "mimo_token_plan", "deepseek"}:
        code = "quota_exhausted"
    if status == 529 and service == "anthropic":
        code = "service_unavailable"
    if code == "local_request_queue" and status is None:
        return {"code": code, "http_status": None, "source": "local_admission"}
    if code in {"input_too_long", "token_budget_exceeded", "context_capacity"}:
        action = None
        generations = [item for item in exchanges if item.get("request_path") == "/v1/messages"]
        if code == "context_capacity" and generations:
            first = generations[0]
            capacity = first.get("context_capacity") or {}
            minimum = capacity.get("minimum_inline_input_bytes")
            unchanged_retry_input = (fresh_session is True if capacity.get("capacity_measurement_version", 0) >= 25
                else type(minimum) is int and capacity.get("max_inline_input_bytes") == minimum)
            if (first.get("failure_reason") == "context_capacity" and capacity.get("fits") is False
                    and unchanged_retry_input
                    and all(item.get("request_bytes_forwarded") == 0 and item.get("request_body_complete") is True for item in generations)):
                action = "input_reconfiguration_required"
        return {"code": code, "http_status": status,
            **({"recovery_action": action} if action is not None else {}),
            **({"source": "local_context_limit"} if code == "context_capacity" else {})}
    usage = last.get("usage") or {}
    if status == 200 and last.get("stop_reason") == "max_tokens" and usage.get("output_tokens") == 0:
        # An anomaly, not proof of context overflow. A confirmed deployment
        # limit plus near-window input is needed for that inference.
        from .context_management import input_usage
        capacity = last.get("context_capacity") or {}
        tokens = input_usage(usage, capacity.get("input_usage_semantics", "separate_cache"))
        window = capacity.get("declared_context_tokens")
        near_window = type(window) is int and tokens is not None and tokens >= window * .95
        return {"code": "input_too_long" if near_window else "empty_output_limit",
            "http_status": status, "source": "provider_usage_inference" if near_window else "provider_response",
            "stop_reason": "max_tokens", "output_tokens": 0}
    ended_at = last.get("ended_at")
    seconds = None
    if type(ended_at) in (int, float) and math.isfinite(ended_at):
        seconds = retry_after_seconds((last.get("response_headers") or {}).get("retry-after"), now=ended_at)
        reset = last.get("provider_retry_at")
        if (seconds is None and service in {"glm_cn", "glm_global"}
                and code in {"usage_window_exhausted", "quota_exhausted"}
                and type(reset) in (int, float) and math.isfinite(reset) and ended_at < reset <= 253402300799):
            seconds = reset - ended_at
    if code == "quota_exhausted":
        return {"code": code, "http_status": status,
            **({"retry_at": ended_at + seconds, "retry_after_seconds": seconds} if seconds is not None else {})}
    if status == 403 and code != "usage_window_exhausted":
        # An explicit temporary window can explain 403; it never overrides 401.
        # Ordinary authentication failures retain the Host's configured delay.
        return {"code": "auth_failed", "http_status": status}
    if status in (429, 500, 502, 503, 504) or code in PROVIDER_TRANSIENT:
        transient = ("service_unavailable" if status in (500, 502, 503, 504) else
                     code if code in PROVIDER_TRANSIENT else "rate_limited")
        ended_at = last.get("ended_at")
        if type(ended_at) not in (int, float) or not math.isfinite(ended_at):
            return {"code": transient, "http_status": status}
        # No trusted reset time: bounded ordinary retry delay, existing caps apply.
        seconds = float(provider_retry_delay(1)) if seconds is None else seconds
        return {"code": transient, "http_status": status,
            "retry_after_seconds": seconds, "retry_at": ended_at + seconds}
    return None


def read_exchanges(directory, *, latest_generation_only=False, latest_context_only=False,
                   input_usage_semantics="separate_cache"):
    """Derive old and new exchanges from existing diagnostic files, read-only."""
    result = []
    newest_first = latest_generation_only or latest_context_only
    for child in sorted(directory.glob("[0-9][0-9][0-9][0-9][0-9][0-9]"), reverse=newest_first):
        if latest_generation_only and result: break
        try:
            if child.is_symlink():
                continue
            path = child / "exchange.json"
            if path.is_symlink() or path.stat().st_size > 65536:
                continue
            data = json.loads(path.read_bytes())
            if not isinstance(data, dict):
                continue
            if newest_first and data.get("request_path") != "/v1/messages": continue
            if "served_models" not in data:
                source = child / "response.body"
                if not source.is_symlink() and source.is_file():
                    parser = ProviderObservation(service=data.get("provider_service", "generic"))
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
            if latest_context_only:
                from .context_management import input_usage
                if (data.get("upstream_status") == 200 and data.get("message_complete") is True
                        and data.get("response_body_complete") is True
                        and data.get("usage_valid") is not False
                        and not empty_generation_response(data)
                        and input_usage(data.get("usage") or {}, input_usage_semantics) is not None):
                    break
        except (OSError, ValueError, UnicodeError, RecursionError):
            continue
    return list(reversed(result)) if latest_context_only else result


def session_cost_state(content: bytes) -> dict[str, Any] | None:
    """Newest saved session totals in a transcript, or None.

    Since CC 2.1.277 a headless session appends a cumulative "cost-state"
    record at exit, and a --resume/--fork-session launch seeds its reported
    modelUsage/total_cost_usd from it instead of starting at zero. The record
    current at preparation time is exactly the carry the next invocation
    reports back, so the Host deducts it to keep per-Attempt accounting
    invocation-exact. Older CC transcripts have no such record.
    """
    complete = content.endswith(b"\n")
    records = content.splitlines()
    state = None
    for raw in records if complete else records[:-1]:
        try:
            record = json.loads(raw)
        except (ValueError, UnicodeError):
            continue
        if isinstance(record, dict) and record.get("type") == "cost-state":
            state = record
    return state
