"""Derived working-context limits, not provider capabilities or scientific state.

The relay and prompt renderer use this same calculation. Complete same-relay
provider observations can measure unchanged history; new text retains a byte
estimate. Neither estimate is a universal tokenizer or provider capability.
"""
from __future__ import annotations

import json
from pathlib import Path
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
    policy = {"working_context_tokens": working, "declared_context_tokens": declared,
        "runtime_context_tokens": runtime, "effective_context_tokens": effective,
        "window_source": "configured" if declared is not None else "working_limit_only",
        "estimate_source": "utf8_bytes_conservative_estimate",
        "input_usage_semantics": memory.get("input_usage_semantics", "separate_cache"),
        "tool_reserve_tokens": min(8192, effective // 16),
        "estimate_margin_tokens": effective // 16,
        "minimum_inline_input_bytes": min(inline, effective // 4) // 4,
        # Retained ACI-24 retries reduce inline text; ACI-25 keeps it stable.
        "max_inline_input_bytes": min(inline, effective // 4) // min(4, 2 ** min(request.attempt_ordinal - 1, 2))}
    if request.schema_version >= 25:
        policy.update(capacity_measurement_version=28 if request.schema_version >= 28 else 25,
                      max_inline_input_bytes=min(inline, effective // 4))
    return policy


def available_input(policy: Mapping[str, Any], output_tokens: int = 0) -> int:
    return max(0, policy["effective_context_tokens"] - output_tokens
        - policy["tool_reserve_tokens"] - policy["estimate_margin_tokens"])


def counting_material(document: Mapping[str, Any], *, version: int = 25) -> dict[str, Any]:
    """Narrow measurement equivalence; never rewrite provider request bytes.

    CLI moves known cache annotations between text/tool-result blocks and can
    turn a single text block into the same string on its next request. Other
    fields, block order, signed thinking and schemas remain exact. Output
    length/stream transport do not change the input.
    """
    material = {key: value for key, value in document.items()
                if key not in {"max_tokens", "stream"}}
    messages = material.get("messages")
    if isinstance(messages, list):
        normalized = []
        for message in messages:
            if not isinstance(message, dict):
                normalized.append(message)
                continue
            content = message.get("content")
            if isinstance(content, list):
                blocks = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") in (("text", "tool_result", "tool_use") if version >= 28 else ("text", "tool_result")):
                        cache = block.get("cache_control")
                        if (isinstance(cache, dict) and cache.get("type") == "ephemeral"
                                and set(cache) <= {"type", "ttl"}
                                and cache.get("ttl", "5m") in ("5m", "1h")):
                            block = {key: value for key, value in block.items() if key != "cache_control"}
                    blocks.append(block)
                message = {**message, "content": blocks}
            else:
                blocks = []
            if len(blocks) == 1:
                block = blocks[0]
                if (isinstance(block, dict) and block.get("type") == "text"
                        and isinstance(block.get("text"), str)
                        and set(block) <= {"type", "text", "cache_control"}
                        and block.get("cache_control") is None):
                    message = {**message, "content": block["text"]}
            normalized.append(message)
        material["messages"] = normalized
    if version >= 28 and isinstance(material.get("system"), list):
        # System text uses the same narrow representation equivalence. No tool
        # schemas, thinking blocks or provider-specific extensions are removed.
        material["system"] = counting_material({"messages": [{"content": material["system"]}]},
                                                version=version)["messages"][0]["content"]
    return material


def _appended_text_bytes(before: Any, after: Any) -> int | None:
    """Only append text to an existing message; keep its metadata exact."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    if {k: v for k, v in before.items() if k != "content"} != {k: v for k, v in after.items() if k != "content"}:
        return None
    old, new = before.get("content"), after.get("content")
    if isinstance(old, str) and isinstance(new, str):
        return len(json.dumps(new[len(old):], ensure_ascii=False).encode("utf-8")) + 32 if new.startswith(old) else None
    old = [{"type": "text", "text": old}] if isinstance(old, str) else old
    new = [{"type": "text", "text": new}] if isinstance(new, str) else new
    if not isinstance(old, list) or not isinstance(new, list) or new[:len(old)] != old:
        return None
    added = new[len(old):]
    if not added or not all(isinstance(b, dict) and set(b) == {"type", "text"}
                            and b["type"] == "text" and isinstance(b["text"], str) for b in added):
        return None
    return len(json.dumps(added, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 32 * len(added)


def _text_message(message: Any) -> bool:
    """New media or opaque redacted thinking have no reliable JSON token bound."""
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if isinstance(content, str):
        return True
    if not isinstance(content, list):
        return False
    for block in content:
        if not isinstance(block, dict):
            return False
        kind = block.get("type")
        if kind == "tool_result":
            if not _text_message({"content": block.get("content", "")}):
                return False
        elif kind not in ("text", "tool_use", "thinking"):
            return False
    return True


def usage_anchor(document: Mapping[str, Any], usage: Mapping[str, Any],
                 policy: Mapping[str, Any], *, exchange: int | None = None) -> dict[str, Any] | None:
    """A disposable observation from a completed response, not a new authority."""
    tokens = input_usage(usage, policy.get("input_usage_semantics", "separate_cache"))
    if tokens is None or type(usage.get("output_tokens")) is not int or usage["output_tokens"] < 0:
        return None
    return {"document": counting_material(document, version=policy.get("capacity_measurement_version", 25)), "input_tokens": tokens, "exchange": exchange}


def request_capacity(document: Mapping[str, Any], policy: Mapping[str, Any], *,
                     anchor: Mapping[str, Any] | None = None,
                     exact_count: Mapping[str, Any] | None = None) -> dict[str, Any]:
    # Include tool schemas/results and reasoning without rewriting signed blocks.
    material = {key: value for key, value in document.items()
        if key not in {"model", "max_tokens", "stream"}}
    estimate = len(json.dumps(material, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    details = {}
    if policy.get("capacity_measurement_version", 0) >= 25:
        current = counting_material(document, version=policy.get("capacity_measurement_version", 25))
        details = {"request_input_bytes": estimate,
                   "estimate_source": "utf8_bytes_conservative_estimate",
                   "estimate_reason": "no_usage_anchor", "anchor_exchange": None}
        if (exact_count is not None and exact_count.get("document") == current
                and type(exact_count.get("input_tokens")) is int and exact_count["input_tokens"] >= 0):
            estimate = exact_count["input_tokens"]
            details.update(estimate_source="provider_count_tokens", estimate_reason="same_request_provider_count",
                           anchor_exchange=exact_count.get("exchange"))
        elif policy.get("input_usage_semantics", "separate_cache") not in {"separate_cache", "includes_cache"}:
            details["estimate_reason"] = "unknown_usage_semantics"
        elif anchor is not None:
            previous = anchor.get("document") or {}
            before, after = previous.get("messages"), current.get("messages")
            appended = None
            prefix_matches = isinstance(before, list) and isinstance(after, list) and after[:len(before)] == before
            if (not prefix_matches and policy.get("capacity_measurement_version", 0) >= 28
                    and isinstance(before, list) and before and isinstance(after, list) and len(after) >= len(before)):
                changed = [i for i, message in enumerate(before) if after[i] != message]
                if len(changed) == 1:
                    index = changed[0]
                    if (isinstance(before[index], dict) and before[index].get("role") == "user"
                            and all(isinstance(m, dict) and m.get("role") == "system" for m in before[index+1:])):
                        appended = _appended_text_bytes(before[index], after[index])
            if (type(anchor.get("input_tokens")) is int and anchor["input_tokens"] >= 0
                    and isinstance(before, list) and isinstance(after, list)
                    and len(after) >= len(before) and (prefix_matches or appended is not None)
                    and {key: value for key, value in current.items() if key != "messages"}
                        == {key: value for key, value in previous.items() if key != "messages"}):
                added = after[len(before):]
                if all(_text_message(message) for message in added):
                    delta = (len(json.dumps(added, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                             + 32 * len(added)) if added else 0
                    delta += appended or 0
                    estimate = anchor["input_tokens"] + delta
                    details.update(estimate_source="provider_usage_plus_delta_estimate",
                                   estimate_reason="message_text_appended" if appended is not None else "unchanged_prefix", anchor_exchange=anchor.get("exchange"),
                                   anchor_input_tokens=anchor["input_tokens"], added_input_bytes=delta)
                else:
                    details["estimate_reason"] = "unsupported_added_content"
            else:
                details["estimate_reason"] = "history_or_configuration_changed"
    output = document.get("max_tokens", 0)
    if type(output) is not int or output < 0:
        raise ValueError("invalid requested output token limit")
    limit = available_input(policy, output)
    return {**policy, **details, "estimated_input_tokens": estimate, "output_reserve_tokens": output,
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
    from .provider_observation import empty_generation_response
    last = next((item for item in reversed(exchanges) if item.get("request_path") == "/v1/messages"), None)
    if last is None:
        return dict(policy or {})
    policy = {**(last.get("context_capacity") or {}), **(policy or {})}
    usage = last.get("usage") or {}
    tokens = input_usage(usage, policy.get("input_usage_semantics", "separate_cache"))
    capacity = last.get("context_capacity") or {}
    complete = last.get("message_complete") is True and last.get("response_body_complete") is True
    if policy.get("capacity_measurement_version", 0) >= 25:
        if last.get("usage_valid") is False or empty_generation_response(last):
            tokens = None
        observed = next((item for item in reversed(exchanges)
            if item.get("request_path") == "/v1/messages" and item.get("upstream_status") == 200
            and item.get("message_complete") is True and item.get("response_body_complete") is True
            and item.get("usage_valid") is not False
            and not empty_generation_response(item)
            and input_usage(item.get("usage") or {}, policy.get("input_usage_semantics", "separate_cache")) is not None), None)
        observed_usage = (observed or {}).get("usage") or {}
        observed_tokens = input_usage(observed_usage, policy.get("input_usage_semantics", "separate_cache"))
        return {**policy, **capacity, "input_tokens": tokens,
            "source": ("provider_usage_final" if complete else "provider_usage_partial") if tokens is not None else "estimate" if capacity else "unknown",
            "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
            "last_observed_exchange": (observed or {}).get("exchange"),
            "last_observed_input_tokens": observed_tokens,
            "current_request_exchange": last.get("exchange"),
            "current_request_estimated_input_tokens": capacity.get("estimated_input_tokens"),
            "current_request_estimate_source": capacity.get("estimate_source"),
            "current_request_estimate_reason": capacity.get("estimate_reason"),
            "current_request_fits": capacity.get("fits")}
    return {**policy, **capacity, "input_tokens": tokens,
        "source": ("provider_usage_final" if complete else "provider_usage_partial") if tokens is not None else "estimate" if capacity else "unknown",
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens")}


def recovery_tool_notes(content: bytes, session_id: str, *, rich: bool = False) -> list[dict[str, Any]]:
    """Keep observable source locations, checks and errors without inventing a summary."""
    if not rich:
        return _legacy_recovery_tool_notes(content, session_id)
    from .diagnostics import bounded_diagnostic_text
    calls, results = {}, []
    def excerpt(value, limit):
        return (bounded_diagnostic_text(value) or "").encode()[:limit].decode(errors="ignore")
    for line in content.splitlines():
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if not isinstance(row, dict) or row.get("sessionId") != session_id: continue
        message = row.get("message")
        blocks = message.get("content") if isinstance(message, dict) else None
        if not isinstance(blocks, list): continue
        for block in blocks:
            if not isinstance(block, dict): continue
            if block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                arguments = block.get("input")
                # Preserve actionable locations/ranges and command/query inputs;
                # source text already lives in the workspace or frozen inputs.
                allowed = {"file_path", "path", "offset", "limit", "pages", "pattern", "glob",
                    "command", "description", "url", "mode", "paper_ref", "sources", "query", "max_results"}
                selected = {key: value for key, value in arguments.items() if key in allowed} if isinstance(arguments, dict) else {}
                calls[block["id"]] = (excerpt(str(block.get("name", "unknown")), 128),
                    excerpt(json.dumps(selected, ensure_ascii=False), 768))
            elif block.get("type") == "tool_result":
                body = block.get("content", "")
                if isinstance(body, list):
                    body = "\n".join(item["text"] for item in body if isinstance(item, dict) and isinstance(item.get("text"), str))
                if not isinstance(body, str): continue
                name, arguments = calls.get(block.get("tool_use_id"), ("unknown", "{}"))
                text = excerpt(body, 512)
                results.append({"tool_use_id": str(block.get("tool_use_id", ""))[:128], "tool": name,
                    "input_excerpt": arguments, "is_error": block.get("is_error") is True,
                    "excerpt": text, "truncated": text != body})
    # Keep a recent tail, the last observed error, and distinct early source/check
    # operations. Bounded selection is an observation, never a reading claim.
    selected = set(range(max(0, len(results) - 4), len(results)))
    for predicate, count in ((lambda item: item["is_error"], 1),
            (lambda item: item["tool"] in {"Bash", "Write", "Edit", "MultiEdit"}, 3)):
        matches = [i for i, item in enumerate(results) if predicate(item)]
        selected.update(matches[-count:])
    seen = set()
    for index, item in enumerate(results):
        signature = (item["tool"], item["input_excerpt"])
        if len(selected) >= 12: break
        if index not in selected and signature not in seen:
            selected.add(index)
        seen.add(signature)
    return [results[index] for index in sorted(selected)]


def _legacy_recovery_tool_notes(content: bytes, session_id: str) -> list[dict[str, Any]]:
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


def continuation_observation(publisher, prior, workspace=None):
    from .agent_context import bounded_diagnostic_text
    from .agent_aci import AgentRequest
    from .agent_sessions import _INNER, read_session_archive
    from .domain import AgentCommand
    observed = json.loads(prior["observation_json"])
    diagnostic = observed.get("diagnostics") or {}
    session = observed.get("session") or {}
    request = AgentRequest.from_dict(observed["request"])
    content = read_session_archive(publisher, request, session)
    result = {"source_attempt_id": prior["attempt_id"], "terminal_reason": prior["terminal_reason"],
        "failure_code": observed.get("failure_code"), "provider_failure": diagnostic.get("provider_failure"),
        "diagnostic": bounded_diagnostic_text(diagnostic.get("provider_diagnostic")),
        "worktree_commit": session.get("commit"), "previous_tools": recovery_tool_notes(content, session.get("session_id"), rich=request.schema_version >= 25),
        "authority": "operational observation only; frozen inputs and accepted facts still govern"}
    if request.schema_version >= 25 and workspace is not None:
        # These are existing authoring files, not a new acceptance or memory store.
        result["session_action"] = "resume_same_task" if workspace.session_arguments[0] == "--resume" else "reconstruct_task"
        result["available_drafts"] = []
        if workspace.acceptance_root is not None and request.command is AgentCommand.GROUNDING:
            for path in sorted(workspace.acceptance_root.rglob("*")):
                if path.is_symlink() or not path.is_file(): continue
                relative = path.relative_to(workspace.acceptance_root)
                if any((workspace.acceptance_root / Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts))): continue
                result["available_drafts"].append({"path": "/acceptance/" + relative.as_posix(), "size_bytes": path.stat().st_size})
                if len(result["available_drafts"]) == 32: break
        if request.command in _INNER:
            result["source_workspace"] = "/workspace"
            result["source_guidance"] = "Inspect the retained Git changes and existing source before repeating implementation or experiments."
    return result
