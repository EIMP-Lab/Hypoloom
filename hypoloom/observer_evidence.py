"""Small disposable observations of one native invocation, never a verdict.

Only current stdout and its prompt are read. Session archives may contain prior
nodes and cannot establish that the current invocation performed an action.
The cache retains summaries and ordinary file metadata, not transcript bodies.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import json
from pathlib import Path
import stat
from threading import RLock

from .agent_aci import RESOURCE_LEARNING_ACI_SCHEMA_VERSION

_COMMANDS = {"EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION"}
_QUERY = "mcp__host-tools__get_resources"
_BODY = "Base directory for this skill: /expert-plugin/skills/resource-planning"
_CACHE: OrderedDict = OrderedDict()
_LOCK = RLock()
_CACHE_LIMIT = 128
_READ_BUDGET = ContextVar("observer_evidence_read_budget", default=None)


@contextmanager
def evidence_read_budget(max_reads=16):
    """Bound cold Attempt reads in one refresh; cache hits remain free.

    This also bounds sequential cache churn when a run exceeds cache capacity.
    A direct Attempt detail can read its selected evidence outside this context.
    """
    if type(max_reads) is not int or max_reads < 0:
        raise ValueError("evidence read budget must be a nonnegative integer")
    token = _READ_BUDGET.set(max_reads)
    try:
        yield
    finally:
        _READ_BUDGET.reset(token)


def _summary(coverage="unavailable", source=None, *, status="unknown", reason="current_stdout_unavailable", name=None):
    return {"coverage": coverage, "source": source, "tool_calls": 0, "tool_errors": 0,
        "reading_calls": {"calls": 0, "rejected": 0, "unanswered": 0, "last_wait_reason": None},
        "counts_scope": "observed_current_invocation",
        "required_action": {"status": status, "name": name, "reason": reason}}


def _cache(key, signature, result):
    with _LOCK:
        _CACHE[key] = (signature, deepcopy(result))
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_LIMIT:
            _CACHE.popitem(last=False)
    return result


def _requirement(request, run_id, details):
    if request.get("command") not in _COMMANDS:
        return "not_applicable", "other_node", None
    version = request.get("schema_version")
    if type(version) is not int:
        return "unknown", "request_version_unavailable", None
    if version < RESOURCE_LEARNING_ACI_SCHEMA_VERSION:
        return "not_applicable", "legacy_request", None
    inputs = request.get("input_artifacts")
    if not isinstance(inputs, list):
        return "unknown", "resource_binding_unavailable", None
    resources = [item for item in inputs if isinstance(item, dict)
        and str(item.get("ref", "")).endswith("--expert-resources")]
    if not resources:
        return "not_applicable", "no_resource_binding", None
    if len(resources) != 1 or len(str(resources[0].get("content_base64", ""))) > details._MAX_PLUGIN_BYTES:
        return "unknown", "resource_binding_unavailable", None
    plugin = details._object(details._object(details.decode_embedded_content(resources[0])).get("plugin"))
    if not plugin:
        return "unknown", "resource_binding_unavailable", None
    if (plugin.get("run_id"), plugin.get("role")) != (run_id, request.get("role")):
        return "unknown", "resource_binding_scope_mismatch", None
    name = plugin.get("name")
    if not isinstance(name, str) or details._SKILL.fullmatch(name) is None:
        return "unknown", "resource_binding_unavailable", None
    return "unknown", "current_requirement_unverified", name + ":resource-planning"


def _metadata(root, parts, details):
    try:
        value = details._path(root, *parts).lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(value.st_mode):
        raise OSError("nonregular evidence")
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _invocation(raw, session_id, details):
    selected, init, terminals = [], None, 0
    malformed, after_terminal = False, False
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            malformed = True
            continue
        if not isinstance(event, dict):
            malformed = True
            continue
        if event.get("isReplay") or event.get("is_replay") or event.get("parent_tool_use_id"):
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            if init is not None:
                malformed = True
                continue
            init = event
            if not isinstance(event.get("session_id"), str) or (
                    session_id is not None and event["session_id"] != session_id):
                malformed = True
        if init is None:
            # Resumed history preceding current init has no current authority.
            continue
        if event.get("session_id") not in {None, init.get("session_id")}:
            malformed = True
            continue
        if event.get("type") == "result":
            terminals += 1
            if type(event.get("is_error")) is not bool:
                malformed = True
        elif terminals and event.get("type") in {"assistant", "user"}:
            after_terminal = True
        selected.append(line)
    # The view's deduplicator would hide a changed result under the same ID.
    # Preserve observations here, then count IDs once and mark conflicts partial.
    trace = details._trace_events(b"\n".join(selected), deduplicate=False)
    uses, results = {}, {}
    bodies = []
    for position, event in enumerate(trace):
        kind, identity = event["type"], event.get("tool_use_id")
        if kind == "text" and event["role"] == "user":
            text = event.get("text", "")
            if isinstance(text, str) and text.startswith(_BODY + "\n") and text[len(_BODY):].strip():
                bodies.append(position)
        if kind not in {"tool_use", "tool_result"}:
            continue
        if not isinstance(identity, str) or not identity or len(identity) > 256:
            malformed = True
            continue
        if (kind == "tool_use" and event["role"] != "assistant") or (
                kind == "tool_result" and event["role"] != "user"):
            malformed = True
            continue
        destination = uses if kind == "tool_use" else results
        comparable = {key: event.get(key) for key in ("name", "input", "content", "is_error")}
        if identity in destination:
            if destination[identity][2] != comparable:
                malformed = True
        else:
            destination[identity] = (position, event, comparable)
        if kind == "tool_use" and (not isinstance(event.get("name"), str)
                                   or not isinstance(event.get("input"), dict)):
            malformed = True
        if kind == "tool_result" and type(event.get("is_error", False)) is not bool:
            malformed = True
    paired = uses.keys() == results.keys() and all(
        uses[key][0] < results[key][0] for key in uses)
    complete = init is not None and terminals == 1 and not malformed and not after_terminal and paired
    return init, uses, results, bodies, complete


def _required(result, requirement, prompt, request, init, uses, results, bodies, complete):
    status, reason, name = requirement
    action = result["required_action"]
    action.update(status=status, reason=reason, name=name)
    if status == "not_applicable" or name is None:
        return
    if not complete:
        action["reason"] = "current_invocation_incomplete"
        return
    if prompt is None:
        action["reason"] = "current_prompt_unavailable"
        return
    metadata = prompt.get("agent_request_metadata")
    if not isinstance(metadata, dict) or any(metadata.get(key) != request.get(key)
            or not isinstance(metadata.get(key), str)
            for key in ("run_id", "attempt_id", "command", "role")):
        action["reason"] = "current_prompt_scope_mismatch"
        return
    instruction = prompt.get("instruction")
    requirement_text = (f"If `{name}` is listed, you must invoke it now, "
        "read its loaded instructions, then query mcp__host-tools__get_resources.")
    if not isinstance(instruction, str) or requirement_text not in instruction or (
            f"Your bound native resource Skill is `{name}`" not in instruction):
        action["reason"] = "current_prompt_requirement_unavailable"
        return
    if not isinstance(init.get("skills"), list) or not isinstance(init.get("tools"), list):
        action["reason"] = "native_capabilities_unavailable"
        return
    if name not in init["skills"]:
        action.update(status="not_applicable", reason="native_skill_not_provided")
        return
    if "Skill" not in init["tools"] or _QUERY not in init["tools"]:
        action["reason"] = "native_tools_unavailable"
        return
    action.update(status="missed", reason="required_resource_sequence_missing")
    for identity, (_, call, _) in uses.items():
        if call["name"] != "Skill" or call["input"].get("skill") != name or results[identity][1].get("is_error"):
            continue
        for query_id, (query_position, query, _) in uses.items():
            if query["name"] != _QUERY or results[query_id][1].get("is_error"):
                continue
            if any(results[identity][0] < body < query_position for body in bodies):
                action.update(status="met", reason="current_skill_body_then_resource_query",
                    skill_tool_use_id=identity, resource_tool_use_id=query_id)
                return


def read_attempt_evidence(root, run_id, row):
    """Return observed tool counts and one bounded existing-action observation.

    Counts under partial/unavailable coverage are observed lower bounds, never
    evidence of zero actual tools. The caller owns selecting the primary row.
    """
    # Lazy import reuses established path/read/redaction limits without a cycle
    # through detail -> summary -> this disposable numerical observation.
    from . import observer_details as details

    details._identity(run_id, "run_id")
    identity = details._identity(row.get("attempt_id"), "attempt_id")
    observation = details._observation(row)
    request = details._object(observation.get("request"))
    if row.get("run_id") != run_id or request.get("run_id", run_id) != run_id or (
            request.get("attempt_id", identity) != identity):
        return _summary(reason="attempt_scope_mismatch")
    if row.get("kind") != "AGENT":
        return _summary(status="not_applicable", reason="not_agent")
    requirement = _requirement(request, run_id, details)
    if requirement[1] == "resource_binding_scope_mismatch":
        return _summary(reason=requirement[1])
    session_id = details._object(observation.get("session")).get("session_id")
    capture = details._object(details._object(observation.get("diagnostics")).get("runtime_capture"))
    root = Path(root).resolve()
    base = ("diagnostics", "agents", run_id, identity)
    paths = (base + ("stdout.log",), base + ("prompt.txt",))
    context = (row.get("state"), row.get("terminal_reason"), session_id, requirement,
        tuple(request.get(key) for key in ("run_id", "attempt_id", "command", "role")),
        bool(capture.get("capture_errors")), details._MAX_TRACE_BYTES)
    key = (str(root), run_id, identity)
    try:
        metadata = tuple(_metadata(root, parts, details) for parts in paths)
        signature = (metadata, context)
        with _LOCK:
            cached = _CACHE.get(key)
            if cached is not None and cached[0] == signature:
                _CACHE.move_to_end(key)
                return deepcopy(cached[1])
        if metadata[0] is None:
            # Missing old captures cannot consume the whole refresh budget and
            # starve later Attempts. A subsequently created file changes stat.
            return _cache(key, signature, _summary(status=requirement[0],
                reason="current_stdout_unavailable", name=requirement[2]))
        budget = _READ_BUDGET.get()
        if budget is not None:
            if budget == 0:
                return _summary(status=requirement[0], reason="evidence_read_deferred", name=requirement[2])
            _READ_BUDGET.set(budget - 1)
        raw, truncated = details._read(root, paths[0], details._MAX_TRACE_BYTES)
        if raw is None:
            return _summary(status=requirement[0], reason="current_stdout_unavailable", name=requirement[2])
        prompt_raw, prompt_truncated = details._read(root, paths[1], details._MAX_TRACE_BYTES)
        prompt = None if prompt_raw is None or prompt_truncated else details._object(prompt_raw.decode("utf-8"))
        init, uses, results, bodies, complete = _invocation(raw, session_id, details)
        stable = metadata == tuple(_metadata(root, parts, details) for parts in paths)
        complete = complete and stable and row.get("state") == "CLOSED" and row.get(
            "terminal_reason") not in {"TIMED_OUT", "LOST", "CANCELLED"} and not (
            truncated or prompt_truncated or capture.get("capture_errors"))
        result = _summary("complete" if complete else "partial", "runtime_diagnostics")
        result.update(tool_calls=len(uses), tool_errors=sum(
            result[1].get("is_error") is True for key, result in results.items() if key in uses))
        reading = result["reading_calls"]
        for identity, (position, call, _) in uses.items():
            if call.get("name") != "mcp__host-tools__read_papers": continue
            reading["calls"] += 1
            reply = results.get(identity)
            if reply is None or reply[0] <= position:
                reading["unanswered"] += 1
                continue
            reading["rejected"] += int(reply[1].get("is_error") is True)
            # Only existing bounded reason codes leave the private tool trace.
            content = reply[1].get("content")
            if isinstance(content, str):
                try: content = json.loads(content)
                except (ValueError, RecursionError): content = None
            if isinstance(content, dict) and not reply[1].get("is_error"):
                reason = content.get("wait_reason")
                reading["last_wait_reason"] = reason if reason in {
                    "parent_deadline", "run_deadline", "provider_backoff", "agent_capacity", "budget_capacity"} else None
        _required(result, requirement, prompt, request, init, uses, results, bodies, complete)
        result = details._clean(result)
        if stable:
            return _cache(key, signature, result)
        return result
    except (OSError, ValueError, TypeError, RecursionError, details.MonitorError):
        return _summary(reason="current_evidence_unreadable", name=requirement[2])
