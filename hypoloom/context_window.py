"""Resolve non-secret CC window controls into the existing node runtime input.

The request owns the resolved values. Provider metadata is an optional bounded
observation, never a model catalogue or a replacement for operator limits.
"""
from __future__ import annotations

import copy
import http.client
import json
import re
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path


CONTEXT_ENVIRONMENT_NAMES = frozenset({
    "CLAUDE_CODE_MAX_CONTEXT_TOKENS", "CLAUDE_CODE_DISABLE_1M_CONTEXT",
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT",
    "DISABLE_COMPACT",
})
_BOOLEAN_NAMES = CONTEXT_ENVIRONMENT_NAMES - {
    "CLAUDE_CODE_MAX_CONTEXT_TOKENS", "CLAUDE_CODE_AUTO_COMPACT_WINDOW"}
_SOURCE_NAMES = frozenset({"cc_config", "cc_model_suffix", "cc_builtin", "cc_default",
    "profile_config", "provider_metadata", "framework_fallback"})
_LIMIT = 1024 * 1024


def wire_model(model: str) -> str:
    return model[:-4] if model.lower().endswith("[1m]") else model


def _positive(value):
    return value if type(value) is int and value > 0 else None


def context_environment(values: Mapping) -> dict[str, str]:
    """Allowlist and validate; never copy arbitrary settings/env to an Agent."""
    result = {}
    for name in CONTEXT_ENVIRONMENT_NAMES.intersection(values):
        value = values[name]
        if not isinstance(value, str):
            raise ValueError("CC context environment values must be strings")
        if name in _BOOLEAN_NAMES:
            if value.lower() not in {"", "0", "1", "false", "true"}:
                raise ValueError("CC context switches must be boolean strings")
            result[name] = "1" if value.lower() in {"1", "true"} else "0"
        else:
            if not re.fullmatch(r"[0-9]{1,9}", value) or int(value) <= 0:
                raise ValueError("CC context sizes must be positive decimal integers")
            if name == "CLAUDE_CODE_AUTO_COMPACT_WINDOW" and not 100000 <= int(value) <= 1000000:
                raise ValueError("CC auto-compact window must be between 100000 and 1000000")
            result[name] = str(int(value))
    return result


def _native_window(model: str) -> int | None:
    # CC's documented model families, not third-party marketing names. Unknown
    # future Claude versions stay unconfirmed instead of inheriting a larger cap.
    match = re.search(r"claude-(opus|sonnet|haiku|fable)-(\d+)(?:-(\d{1,2})(?=\D|$))?(?=\D|$)", model.lower())
    if match:
        family, major, minor = match[1], int(match[2]), int(match[3] or 0)
        if family == "opus" and (major == 4 and 7 <= minor <= 8 or major == 5 and minor <= 5): return 1000000
        if family == "sonnet" and major == 5 and minor <= 5: return 1000000
        if family == "haiku" and major == 5 and minor == 5: return 1000000
        if family == "fable" and major == 5 and minor <= 1: return 1000000
        if family in {"opus", "sonnet", "haiku"} and major in {3, 4} and minor <= 6: return 200000
    if re.search(r"claude-3-(opus|sonnet|haiku)(?:-|$)", model.lower()): return 200000
    return None


def _cc_window(model: str, environment: Mapping[str, str]):
    native = _native_window(model)
    disabled = environment.get("CLAUDE_CODE_DISABLE_1M_CONTEXT") == "1"
    tagged = model.lower().endswith("[1m]") and not disabled
    # CC 2.1.296 still reports 200K for an unrecognized bare claude-* ID
    # including claude-private@YYYYMMDD. Keep the native regression explicit.
    custom = native is None and not model.lower().startswith("claude-")
    maximum = int(environment["CLAUDE_CODE_MAX_CONTEXT_TOKENS"]) if "CLAUDE_CODE_MAX_CONTEXT_TOKENS" in environment else None
    if maximum and (custom and not tagged or environment.get("DISABLE_COMPACT") == "1"):
        return maximum, "cc_config", custom
    if tagged: return 1000000, "cc_model_suffix", custom
    if native is not None: return min(native, 200000) if disabled else native, "cc_builtin", custom
    return 200000, "cc_default", custom


def resolve_window(model: str, settings: Mapping, environment: Mapping, memory: Mapping,
                   metadata=None) -> dict:
    selected = context_environment({**settings.get("env", {}), **environment})
    tokens, source, custom = _cc_window(model, selected)
    warnings = []
    if "CLAUDE_CODE_MAX_CONTEXT_TOKENS" in selected and source != "cc_config":
        warnings.append("cc_context_override_ignored")
    result = {"model": model, "cc_context_tokens": tokens, "cc_window_source": source,
        "provider_context_tokens": None, "provider_input_tokens": None,
        "cc_1m_requested": model.lower().endswith("[1m]") and selected.get("CLAUDE_CODE_DISABLE_1M_CONTEXT") != "1",
        "metadata_status": "local_configuration", "cc_environment": selected, "window_warnings": warnings}
    declared = memory.get("context_window_tokens")
    if declared is not None and _positive(declared) is None:
        raise ValueError("configured context window must be a positive integer")
    enabled = memory.get("context_metadata", True)
    if type(enabled) is not bool: raise ValueError("context_metadata must be boolean")
    if source in {"cc_default", "cc_builtin"} and declared is None:
        if enabled and metadata is not None:
            result.update(metadata(model))
        else:
            result["metadata_status"] = "disabled" if not enabled else "unavailable"
    # A custom ID can adopt an explicit deployment window without disabling
    # native compaction. Do not apply this override to a recognized Claude ID.
    adopted = declared or result["provider_context_tokens"]
    if custom and source == "cc_default" and adopted is not None:
        selected["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(adopted)
        result.update(cc_context_tokens=adopted,
            cc_window_source="profile_config" if declared else "provider_metadata")
    per_model = settings.get("modelSettings", {})
    per_model = per_model.get(model, per_model.get(wire_model(model), {})) if isinstance(per_model, Mapping) else {}
    compact = selected.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW",
        per_model.get("autoCompactWindow", settings.get("autoCompactWindow")) if isinstance(per_model, Mapping) else settings.get("autoCompactWindow"))
    if compact is not None and compact != "auto":
        if isinstance(compact, str) and compact.isdecimal(): compact = int(compact)
        if _positive(compact) is None or not 100000 <= compact <= 1000000:
            raise ValueError("CC auto-compact window must be between 100000 and 1000000")
        selected["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(compact)
        result["auto_compact_tokens"] = min(compact, result["cc_context_tokens"])
    else:
        result["auto_compact_tokens"] = None
    if selected.get("DISABLE_COMPACT") == "1" or custom and selected.get("CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT") == "1":
        warnings.append("cc_proactive_compaction_disabled")
    provider = result["provider_context_tokens"]
    if provider is not None and provider != result["cc_context_tokens"]:
        warnings.append("provider_cc_window_difference")
    return result


def validate_window(value, model: str | None = None) -> dict:
    if not isinstance(value, dict) or (model is not None and value.get("model") != model):
        raise ValueError("resolved context window must match the request model")
    if value.get("cc_window_source") not in _SOURCE_NAMES:
        raise ValueError("resolved context window source is invalid")
    for key in ("cc_context_tokens", "provider_context_tokens", "provider_input_tokens", "auto_compact_tokens"):
        if value.get(key) is not None and _positive(value[key]) is None:
            raise ValueError("resolved context limits must be positive integers")
    if context_environment(value.get("cc_environment", {})) != value.get("cc_environment"):
        raise ValueError("resolved context environment is not normalized")
    if type(value.get("cc_1m_requested")) is not bool:
        raise ValueError("resolved 1M selection must be boolean")
    return value


def window_policy(memory: Mapping, window: Mapping) -> dict:
    from .context_management import DEFAULT_WORKING_TOKENS
    working, declared, runtime = (memory.get(key) for key in
        ("working_context_tokens", "context_window_tokens", "runtime_context_tokens"))
    ceilings = [(working, "working_limit_only"), (declared, "configured"), (runtime, "configured_runtime"),
        (window.get("provider_context_tokens"), "provider_metadata"),
        (window.get("cc_context_tokens"), window["cc_window_source"])]
    known = [(tokens, source) for tokens, source in ceilings if tokens is not None]
    if any(_positive(tokens) is None for tokens, _ in known):
        raise ValueError("context limits must be positive integers")
    effective, source = min(known, key=lambda item: item[0]) if known else (DEFAULT_WORKING_TOKENS, "framework_fallback")
    return {**{key: value for key, value in window.items() if key not in {"model", "cc_environment"}},
        "working_context_tokens": working, "declared_context_tokens": declared, "runtime_context_tokens": runtime,
        "effective_context_tokens": effective, "window_source": source,
        "working_context_mode": "automatic" if working is None else "explicit"}


def metadata_limits(document, model: str) -> dict:
    """Accept exact IDs and explicit units; max_tokens is NOT a window."""
    if not isinstance(document, dict): return {}
    entries = document.get("data", [document])
    if not isinstance(entries, list): return {}
    entries = [item for item in entries if isinstance(item, dict) and item.get("id") == wire_model(model)]
    if len(entries) != 1: return {}
    item = entries[0]
    # context_length is the total-window field used by model-list APIs.
    # Anthropic max_input_tokens constrains input separately from total capacity.
    total, inputs = _positive(item.get("context_length")), _positive(item.get("max_input_tokens"))
    return {"provider_context_tokens": total, "provider_input_tokens": inputs} if total or inputs else {}


def _query(environment, model):
    from .provider_relay import validate_provider_connection
    endpoint, credentials = validate_provider_connection(environment.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"), environment)
    connection_type = http.client.HTTPSConnection if endpoint.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(endpoint.hostname, endpoint.port, timeout=2)
    headers = {"anthropic-version": "2023-06-01", "Accept": "application/json"}
    if credentials.get("ANTHROPIC_API_KEY"): headers["x-api-key"] = credentials["ANTHROPIC_API_KEY"]
    if credentials.get("ANTHROPIC_AUTH_TOKEN"): headers["Authorization"] = "Bearer " + credentials["ANTHROPIC_AUTH_TOKEN"]
    prefix = endpoint.path.rstrip("/")
    path = prefix + ("/models" if prefix.endswith("/v1") else "/v1/models")
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        if response.status != 200: return {}  # No redirects or auth forwarding.
        body = response.read(_LIMIT + 1)
        return metadata_limits(json.loads(body), model) if len(body) <= _LIMIT else {}
    finally:
        connection.close()


class WindowResolver:
    """Disposable Host/preview cache; five seconds total, two per query.

    A subprocess bounds DNS and slow-drip reads too. Its stdin carries the
    selected credentials; argv/output never contain them. It cannot generate.
    """
    def __init__(self):
        self.remaining_seconds = 5.0
        self.queries_remaining = 3
        self.cache = {}

    def metadata(self, environment, model):
        connection = {key: environment[key] for key in ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN") if key in environment}
        key = (tuple(sorted(connection.items())), wire_model(model))
        if key in self.cache: return copy.deepcopy(self.cache[key])
        result = {"metadata_status": "budget_exhausted"}
        if self.remaining_seconds > 0.05 and self.queries_remaining > 0:
            self.queries_remaining -= 1
            started = time.monotonic()
            result = {"metadata_status": "unavailable"}
            try:
                completed = subprocess.run([sys.executable, "-c",
                    "import runpy,sys;sys.path.insert(0,sys.argv[1]);runpy.run_module('hypoloom.context_window',run_name='__main__')",
                    str(Path(__file__).resolve().parent.parent)],
                    input=json.dumps({"environment": connection, "model": model}), text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=min(2.0, self.remaining_seconds), check=False)
                value = json.loads(completed.stdout) if completed.returncode == 0 else {}
                if value and set(value) <= {"provider_context_tokens", "provider_input_tokens"} and all(v is None or _positive(v) for v in value.values()):
                    result = {**value, "metadata_status": "available"}
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
            finally:
                self.remaining_seconds = max(0.0, self.remaining_seconds - (time.monotonic() - started))
        self.cache[key] = result
        return copy.deepcopy(result)

    def resolve(self, model, settings, environment, memory):
        return resolve_window(model, settings, environment, memory,
            metadata=lambda selected: self.metadata({**settings.get("env", {}), **environment}, selected))


def bind_inputs(adapter, inputs, default_model, provider_environment):
    """Called before the existing request transaction, never on frozen retries."""
    from .agent_aci import EmbeddedInput
    from .profile_v4 import request_node_runtime
    runtime = request_node_runtime(inputs)
    if not any(item.ref.endswith(("--expert-config", "--grounding-config")) for item in inputs): return inputs
    model = runtime.get("model", default_model)
    window = adapter.resolve_context_window(model, runtime.get("memory", {}), provider_environment)
    result = []
    for item in inputs:
        if item.ref.endswith(("--expert-config", "--grounding-config")):
            document = json.loads(item.content_bytes())
            document.setdefault("node_runtime", {})["context_window"] = window
            item = EmbeddedInput.from_bytes(item.ref, item.media_type, json.dumps(document, ensure_ascii=False).encode())
        result.append(item)
    return tuple(result)


if __name__ == "__main__":
    try:
        arguments = json.load(sys.stdin)
        print(json.dumps(_query(arguments["environment"], arguments["model"])))
    except Exception:
        print("{}")  # Provider failures never echo private URLs/credentials.
