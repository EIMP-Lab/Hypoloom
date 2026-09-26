"""Derived working-context limits, not provider capabilities or scientific state.

The relay and prompt renderer use this same calculation. UTF-8 bytes are an
intentionally conservative *estimate*, not a universal tokenizer or a promise
that an unknown deployment accepts the request. No model-name inference.
"""
from __future__ import annotations

import json
from typing import Any, Mapping


CONTEXT_ACI_VERSION = 24
CONTEXT_FAILURES = frozenset({"input_too_long", "context_capacity", "compaction_failed"})
DEFAULT_WORKING_TOKENS = 262144
DEFAULT_INLINE_BYTES = 49152


def context_policy(request) -> dict[str, Any] | None:
    if request.schema_version < CONTEXT_ACI_VERSION:
        return None
    from .profile_v4 import request_node_runtime
    memory = request_node_runtime(request.input_artifacts).get("memory", {})
    working = memory.get("working_context_tokens", DEFAULT_WORKING_TOKENS)
    declared = memory.get("context_window_tokens")
    runtime = memory.get("runtime_context_tokens")
    inline = memory.get("max_inline_input_bytes", DEFAULT_INLINE_BYTES)
    for value in (working, inline, declared, runtime):
        if value is not None and (type(value) is not int or value <= 0):
            raise ValueError("context limits must be positive integers")
    effective = min(value for value in (working, declared, runtime) if value is not None)
    return {"working_context_tokens": working, "declared_context_tokens": declared,
        "runtime_context_tokens": runtime, "effective_context_tokens": effective,
        "window_source": "configured" if declared is not None else "working_limit_only",
        "estimate_source": "utf8_bytes_conservative_estimate",
        "input_usage_semantics": memory.get("input_usage_semantics", "separate_cache"),
        "tool_reserve_tokens": min(8192, effective // 16),
        "estimate_margin_tokens": effective // 16,
        "minimum_inline_input_bytes": min(inline, effective // 4) // 4,
        # Retries carry the same authoritative sources with less inline text.
        "max_inline_input_bytes": min(inline, effective // 4) // min(4, 2 ** min(request.attempt_ordinal - 1, 2))}


def available_input(policy: Mapping[str, Any], output_tokens: int = 0) -> int:
    return max(0, policy["effective_context_tokens"] - output_tokens
        - policy["tool_reserve_tokens"] - policy["estimate_margin_tokens"])


def request_capacity(document: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
    # Include tool schemas/results and reasoning without rewriting signed blocks.
    material = {key: value for key, value in document.items()
        if key not in {"model", "max_tokens", "stream"}}
    estimate = len(json.dumps(material, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    output = document.get("max_tokens", 0)
    if type(output) is not int or output < 0:
        raise ValueError("invalid requested output token limit")
    limit = available_input(policy, output)
    return {**policy, "estimated_input_tokens": estimate, "output_reserve_tokens": output,
        "available_input_tokens": limit, "fits": estimate <= limit}


def input_usage(usage: Mapping[str, Any], semantics: str = "separate_cache") -> int | None:
    value = usage.get("input_tokens")
    if type(value) is not int or value < 0:
        return None
    if semantics == "includes_cache":
        return value
    if semantics != "separate_cache":
        return None
    for key in ("cache_creation_input_tokens", "cache_read_input_tokens"):
        extra = usage.get(key, 0)
        if type(extra) is not int or extra < 0:
            return None
        value += extra
    return value


def latest_context(exchanges, policy=None) -> dict[str, Any]:
    """Latest generation exchange, never accumulated invocation accounting."""
    last = next((item for item in reversed(exchanges) if item.get("request_path") == "/v1/messages"), None)
    if last is None:
        return dict(policy or {})
    policy = {**(last.get("context_capacity") or {}), **(policy or {})}
    usage = last.get("usage") or {}
    tokens = input_usage(usage, policy.get("input_usage_semantics", "separate_cache"))
    capacity = last.get("context_capacity") or {}
    complete = last.get("message_complete") is True and last.get("response_body_complete") is True
    return {**policy, **capacity, "input_tokens": tokens,
        "source": ("provider_usage_final" if complete else "provider_usage_partial") if tokens is not None else "estimate" if capacity else "unknown",
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens")}


def recovery_tool_notes(content: bytes, session_id: str) -> list[dict[str, Any]]:
    """Bounded, unaccepted process observations from the prior own archive."""
    from .diagnostics import bounded_diagnostic_text
    names, recent, last_error = {}, [], None
    for index, line in enumerate(content.splitlines()):
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if not isinstance(row, dict) or row.get("sessionId") != session_id:
            continue
        message = row.get("message")
        blocks = message.get("content") if isinstance(message, dict) else None
        if not isinstance(blocks, list): continue
        for block in blocks:
            if not isinstance(block, dict): continue
            if block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                names[block["id"]] = str(block.get("name", "unknown"))[:128]
            if block.get("type") != "tool_result": continue
            body = block.get("content", "")
            if isinstance(body, list):
                body = "\n".join(item.get("text", "") for item in body
                    if isinstance(item, dict) and isinstance(item.get("text"), str))
            if not isinstance(body, str): continue
            raw = (bounded_diagnostic_text(body) or "").encode()
            item = {"tool_use_id": str(block.get("tool_use_id", ""))[:128],
                "tool": names.get(block.get("tool_use_id"), "unknown"),
                "is_error": block.get("is_error") is True,
                "excerpt": raw[:1024].decode(errors="ignore"),
                "truncated": len(body.encode()) > len(raw[:1024])}
            recent.append((index, item))
            recent = recent[-3:]
            if item["is_error"]: last_error = (index, item)
    if last_error is not None and last_error not in recent:
        recent = sorted([last_error, *recent[-2:]], key=lambda pair: pair[0])
    return [item for _, item in recent]
