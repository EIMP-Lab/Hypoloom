"""Bounded read-only Web details over run-bound Attempts, artifacts and Git.

These are disposable presentations, not acceptance or runtime lifecycle code.
No reader creates a plugin store, materializes a worktree, discovers another
run, or treats a mutable Git HEAD as an accepted version.
"""
from __future__ import annotations

import difflib
import json
import math
import os
import re
import stat
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .diagnostics import credential_shaped_text
from .git_authoring_workspace import _COMMIT_RE, _run_git, GitAuthoringWorkspaceError
from .monitor import MonitorError, _gpu_usage
from .runtime_diagnostics import decode_embedded_content, sanitize_payload
from .state_store import StateStore

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}")
_SKILL = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_MAX_PLUGIN_BYTES = 2 * 1024 * 1024
_MAX_TRACE_BYTES = 4 * 1024 * 1024
_MAX_TEXT = 65536
_SESSION_FIELDS = ("session_id", "parent_session_id", "scope", "chain", "cycle", "branch")
_SELECTORS = {
    "skills": {"role"}, "skill": {"role", "name", "commit", "base_commit"},
    "literature": {"role"}, "sessions": {"role", "component_id", "session_id"},
    "attempt": {"attempt_id"}, "interactions": {"role"},
    "resources": {"role", "component_id", "experiment_id"},
}


def _identity(value: object, name: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f"{name} must be a safe identity")
    return value


def _object(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return {}
    return value if isinstance(value, dict) else {}


def _observation(row):
    return _object(row.get("observation_json", row.get("observation")))


def _payload(row):
    return _object(row.get("payload_json", row.get("payload")))


def _time(value):
    try:
        if isinstance(value, (int, float)):
            return float(value)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _safe(value: Any, depth: int = 0) -> Any:
    """Keep diagnostics useful while bounding text and omitting configuration."""
    if depth > 12:
        return "[detail depth limit]"
    if isinstance(value, dict):
        return {str(key): _safe(item, depth + 1) for key, item in value.items()
                if str(key).lower() not in {"environment", "env", "settings", "provider_settings",
                                           "content_base64", "stderr_excerpt", "launch_arguments"}}
    if isinstance(value, (list, tuple)):
        return [_safe(item, depth + 1) for item in value[:1000]]
    if isinstance(value, str):
        return "[credential-shaped text redacted]" if credential_shaped_text(value) else value[:_MAX_TEXT]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _clean(value):
    return _safe(sanitize_payload(value))


def _path(root: Path, *parts: str) -> Path:
    path = root
    for part in parts:
        path /= part
        if path.is_symlink():
            raise MonitorError("observer evidence must not follow symlinks")
    return path


def _read(root: Path, parts: tuple[str, ...], maximum: int) -> tuple[bytes | None, bool]:
    path = _path(root, *parts)
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise MonitorError("observer evidence must be a regular file")
        with path.open("rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise MonitorError("observer evidence must be a regular file")
            data = handle.read(maximum + 1)
    except FileNotFoundError:
        return None, False
    return data[:maximum], len(data) > maximum


def _page(key, values, selectors, **extra):
    offset, limit = selectors.get("offset", 0), selectors.get("limit", 100)
    selected = values[offset:offset + limit]
    return {**extra, key: selected, "items": selected, "total": len(values),
            "offset": offset, "limit": limit, "has_more": offset + limit < len(values)}


def _request_plugin(row, run_id):
    observation = _observation(row)
    request = _object(observation.get("request"))
    role = request.get("role")
    task = _object(request.get("task_context"))
    initial_grounding = (request.get("command") == "GROUNDING"
        and request.get("owner_workflow_id") == run_id
        and task.get("kind") == "AUXILIARY" and task.get("request_kind") == "GROUNDING"
        and task.get("run_id") == run_id
        and task.get("expert_slot") is None and task.get("component_id") is None)
    bindings = []
    for item in request.get("input_artifacts", ()):
        if not isinstance(item, dict) or not str(item.get("ref", "")).endswith(("--expert-resources", "--grounding-resources")):
            continue
        if len(str(item.get("content_base64", ""))) > _MAX_PLUGIN_BYTES:
            continue
        resources = _object(decode_embedded_content(item))
        bindings.append(_object(resources.get("plugin")))
    resources = _object(_object(observation.get("session")).get("expert_resources"))
    if resources:
        bindings.append(_object(resources.get("plugin")))
    for binding in bindings:
        owner_role = "grounding" if initial_grounding and binding.get("owner_kind") == "grounding" else role
        if (binding.get("run_id"), binding.get("role")) != (run_id, owner_role):
            raise MonitorError("plugin binding belongs to another run or role")
        _identity(role, "role")
        if not isinstance(binding.get("commit"), str) or _COMMIT_RE.fullmatch(binding["commit"]) is None:
            continue
        if binding.get("owner_kind", "expert") not in {"expert", "grounding"}:
            continue
        return binding
    return None


def _binding_view(row):
    observation = _observation(row)
    request, session = _object(observation.get("request")), _object(observation.get("session"))
    key = _object(_object(request.get("task_context")).get("key"))
    return {"attempt_id": row["attempt_id"], "state": row.get("state"),
            "command": request.get("command"), "session_id": session.get("session_id"),
            "component_id": request.get("component_id"), "cycle": key.get("cycle"),
            "chain": key.get("chain"), "started_at": row.get("started_at"),
            "evidence": "request_binding", "native_loaded": None}


def _version_sources(run_id, attempts):
    versions, latest_bound, latest_update = {}, {}, {}
    for row in sorted(attempts, key=lambda item: (_time(item.get("started_at")), item["attempt_id"])):
        if row.get("run_id") != run_id or row.get("kind") != "AGENT":
            continue
        observation = _observation(row)
        request = _object(observation.get("request"))
        binding = _request_plugin(row, run_id)
        if binding:
            role, commit = binding["role"], binding["commit"]
            version = versions.setdefault((role, commit), {"role": role, "commit": commit,
                "owner_kind": binding.get("owner_kind", "expert"), "bindings": [], "updates": [], "current": False})
            view = _binding_view(row)
            if not any(item["attempt_id"] == row["attempt_id"] for item in version["bindings"]):
                version["bindings"].append(view)
            latest_bound[(role, view["component_id"], view["chain"])] = (role, commit)
            if row.get("state") == "OPEN":
                version["current"] = True
        update = _object(observation.get("plugin_update"))
        role = request.get("role")
        if (row.get("state") != "CLOSED" or row.get("terminal_reason") != "SUCCEEDED"
                or request.get("command") not in {"SELF_EVOLVE", "GRAPH_EVOLVE"} or not update):
            continue
        _identity(role, "role")
        if not all(isinstance(update.get(key), str) and _COMMIT_RE.fullmatch(update[key]) for key in ("base_commit", "commit")):
            continue
        owner = "grounding" if request.get("command") == "GRAPH_EVOLVE" else "expert"
        for commit in (update["base_commit"], update["commit"]):
            versions.setdefault((role, commit), {"role": role, "commit": commit,
                "owner_kind": owner, "bindings": [], "updates": [], "current": False})
        target = versions[(role, update["commit"])]
        target["updates"].append({**_binding_view(row), **update, "ended_at": row.get("ended_at")})
        rank = (_time(row.get("ended_at")), row["attempt_id"])
        if role not in latest_update or rank > latest_update[role][0]:
            latest_update[role] = (rank, (role, update["commit"]))
    for identity in (*latest_bound.values(), *(item[1] for item in latest_update.values())):
        versions[identity]["current"] = True
    return list(versions.values())


def _repository(root, run_id, version):
    parts = (run_id, "grounding", "plugin") if version["owner_kind"] == "grounding" else (
        run_id, "experts", version["role"], "plugin")
    path = _path(root, *parts)
    if not _path(path, ".git").is_dir():
        raise FileNotFoundError("accepted plugin repository unavailable")
    return path


@lru_cache(maxsize=32)
def _commit_files(repository: str, commit: str) -> tuple[tuple[str, bytes], ...]:
    """Bounded immutable Git objects; use the existing isolated Git runner."""
    def git(*args, maximum=_MAX_PLUGIN_BYTES):
        return _run_git("/usr/bin/git", ("-C", repository, *args), timeout_seconds=10,
                        max_output_bytes=maximum)
    if git("rev-parse", "--verify", commit + "^{commit}").decode().strip() != commit:
        raise ValueError("plugin commit does not resolve exactly")
    entries = git("ls-tree", "-rz", commit).split(b"\0")
    result, remaining = [], _MAX_PLUGIN_BYTES
    for entry in filter(None, entries):
        header, raw_path = entry.split(b"\t", 1)
        mode, kind, blob = header.split()
        path = raw_path.decode("utf-8")
        parts = PurePosixPath(path).parts
        if (kind != b"blob" or mode not in {b"100644", b"100755"}
                or len(parts) < 3 or parts[0] != "skills" or not _SKILL.fullmatch(parts[1])
                or ".." in parts or any(part.startswith(".") for part in parts)):
            continue
        # Only the accepted native skill subtree, never configuration files.
        if any(part.lower() in {"settings.json", "credentials.json"} for part in parts):
            continue
        content = git("cat-file", "blob", blob.decode(), maximum=max(1, remaining + 1))
        remaining -= len(content)
        if remaining < 0:
            raise ValueError("plugin detail exceeds its byte limit")
        result.append((path, content))
    return tuple(result)


def _files(root, run_id, version):
    try:
        return dict(_commit_files(str(_repository(root, run_id, version)), version["commit"]))
    except (FileNotFoundError, GitAuthoringWorkspaceError, ValueError, UnicodeError):
        return None


def _skill_metadata(files):
    result = []
    for path, content in files.items():
        parts = PurePosixPath(path).parts
        if len(parts) != 3 or parts[-1] != "SKILL.md":
            continue
        try:
            text = content.decode("utf-8")
            frontmatter = text.split("---", 2)
            metadata = yaml.safe_load(frontmatter[1]) if len(frontmatter) == 3 and not frontmatter[0] else None
        except (UnicodeError, yaml.YAMLError):
            continue
        if (not isinstance(metadata, dict) or metadata.get("name") != parts[1]
                or not isinstance(metadata.get("description"), str)):
            continue
        result.append({"name": parts[1], "description": metadata["description"]})
    return result


def skill_catalog(state_root, run_id, attempts):
    """Inventory accepted/bound native entrypoints, retaining concurrent versions."""
    root = Path(state_root).resolve()
    _identity(run_id, "run_id")
    versions, skills = _version_sources(run_id, attempts), []
    for version in versions:
        files = _files(root, run_id, version)
        entries = [] if files is None else _skill_metadata(files)
        version.update(available=files is not None, skill_count=None if files is None else len(entries))
        for item in entries:
            skills.append({**item, "role": version["role"], "commit": version["commit"],
                           "current": version["current"], "bindings": version["bindings"]})
    unknown = sum(not item["available"] for item in versions)
    return _clean({"skills": skills, "versions": versions, "available": bool(versions) and not unknown,
                   "unknown_versions": unknown, "coverage": "unavailable" if not versions else "partial" if unknown else "complete"})


def _skill(root, run_id, attempts, selectors):
    versions = [item for item in _version_sources(run_id, attempts) if item["role"] == selectors["role"]]
    commit = selectors.get("commit")
    if commit is None:
        candidates = [item for item in versions if item["current"]]
        commit = candidates[-1]["commit"] if candidates else None
    version = next((item for item in versions if item["commit"] == commit), None)
    if version is None:
        raise MonitorError("skill commit is not bound to this run and role")
    updates = [item for item in version["updates"] if item["base_commit"] != item["commit"]]
    update = max(updates, key=lambda item: (_time(item.get("ended_at")), item["attempt_id"])) if updates else None
    base = selectors.get("base_commit", None if update is None else update["base_commit"])
    if base is not None and not any(item["base_commit"] == base for item in updates):
        raise MonitorError("skill diff base is not an accepted update source")
    name = selectors["name"]
    files = _files(root, run_id, version)
    path = f"skills/{name}/SKILL.md"
    if files is not None and path not in files:
        raise MonitorError("skill entrypoint is not present at the bound commit")
    body = None if files is None else files[path].decode("utf-8", errors="replace")
    supporting = []
    for file_path, content in (files or {}).items():
        if not file_path.startswith(f"skills/{name}/") or file_path == path:
            continue
        try:
            text = content.decode("utf-8") if b"\0" not in content else None
        except UnicodeError:
            text = None
        supporting.append({"path": file_path, "size": len(content), "text": text,
                           "truncated": text is not None and len(text) > _MAX_TEXT})
    diff = {"base_commit": base, "commit": commit, "available": False, "text": "", "truncated": False}
    if base is not None and files is not None:
        old = _files(root, run_id, {**version, "commit": base})
        if old is not None:
            text = "".join(difflib.unified_diff(old.get(path, b"").decode("utf-8", errors="replace").splitlines(True),
                files[path].decode("utf-8", errors="replace").splitlines(True), fromfile=f"{base}/{path}", tofile=f"{commit}/{path}"))
            diff.update(available=True, text=text, truncated=len(text) > _MAX_TEXT)
    # Publication and binding are separate. No native Skill invocation is inferred.
    adoption = [item for item in version["bindings"] if update is None or
                (item["attempt_id"] != update["attempt_id"] and _time(item.get("started_at")) >= _time(update.get("ended_at")))]
    return {"role": version["role"], "name": name, "commit": commit, "available": files is not None,
            "body": body, "body_truncated": body is not None and len(body) > _MAX_TEXT,
            "supporting_files": supporting[:100], "supporting_files_truncated": len(supporting) > 100,
            "diff": diff, "update_attempt": update, "adoption": adoption,
            "versions": [{"commit": item["commit"], "current": item["current"]} for item in versions]}


def _attempt_summary(row):
    observation = _observation(row)
    request, session = _object(observation.get("request")), _object(observation.get("session"))
    diagnostics = _object(observation.get("diagnostics"))
    normalization = _object(diagnostics.get("provider_turn_usage_normalization"))
    response = _object(observation.get("response"))
    accounting = _object(diagnostics.get("provider_accounting"))
    arguments = session.get("launch_arguments")
    launch_mode = "unknown"
    if isinstance(arguments, (list, tuple)) and all(isinstance(item, str) for item in arguments):
        for flag, mode in (("--fork-session", "fork"), ("--resume", "resume"), ("--session-id", "new")):
            if flag in arguments:
                launch_mode = mode
                break
    return {**{key: row.get(key) for key in ("attempt_id", "kind", "state", "terminal_reason", "started_at", "ended_at", "attempt_ordinal")},
            "role": request.get("role"), "command": request.get("command"),
            "component_id": request.get("component_id"), "task": request.get("task_context"),
            **{key: session.get(key) for key in _SESSION_FIELDS}, "launch_mode": launch_mode,
            "turns": normalization.get("turn_count", response.get("turn_count", accounting.get("turn_count"))),
            "raw_turns": normalization.get("reported_num_turns", diagnostics.get("num_turns")),
            "corrections": diagnostics.get("structured_output_correction")}


def _sessions(attempts, selectors):
    sessions = {}
    for row in attempts:
        if row.get("kind") != "AGENT":
            continue
        view = _attempt_summary(row)
        if any(selectors.get(key) is not None and selectors[key] != view.get(key) for key in ("role", "component_id", "session_id")):
            continue
        identity = view["session_id"] or "unavailable:" + row["attempt_id"]
        session = sessions.setdefault(identity, {"session_id": view["session_id"], "role": view["role"],
            "component_id": view["component_id"], "attempts": [], "launch_modes": [],
            **{key: view[key] for key in _SESSION_FIELDS}, "available": view["session_id"] is not None})
        session.update({key: view[key] for key in _SESSION_FIELDS if view[key] is not None})
        session["launch_mode"] = view["launch_mode"]
        if view["launch_mode"] not in session["launch_modes"]:
            session["launch_modes"].append(view["launch_mode"])
        session["attempts"].append(view)
    return _page("sessions", list(sessions.values()), selectors)


def _trace_events(raw, *, deduplicate=True):
    events, seen = [], set()
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(event, dict):
            continue
        message = _object(event.get("message"))
        content = message.get("content", [])
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            continue
        for index, block in enumerate(content):
            if not isinstance(block, dict) or block.get("type") not in {"text", "tool_use", "tool_result"}:
                continue
            identity = block.get("id", block.get("tool_use_id"))
            key = (event.get("type"), message.get("id", event.get("uuid")), block.get("type"), identity, index)
            if deduplicate and (key[1] is not None or identity is not None) and key in seen:
                continue
            seen.add(key)
            events.append({"type": block["type"], "role": event.get("type"),
                "message_id": message.get("id"), "tool_use_id": identity,
                **{key: block[key] for key in ("name", "input", "content", "is_error", "text") if key in block}})
    # Suppress content of explicit config reads in addition to normal redaction.
    private_calls = {item["tool_use_id"] for item in events if item["type"] == "tool_use"
        and re.search(r"(?:^|[/\s\"'])(?:\.env(?:\.[\w-]+)?|settings(?:\.local)?\.json|credentials\.json)(?:$|[\s\"'])",
                      json.dumps(item.get("input", {})), re.I)}
    for item in events:
        if item["tool_use_id"] in private_calls and item["type"] == "tool_result":
            item["content"] = "[configuration read omitted]"
    return events


def _attempt_detail(root, run_id, attempts, selectors):
    from .observer_evidence import read_attempt_evidence
    identity = selectors["attempt_id"]
    row = next((item for item in attempts if item["attempt_id"] == identity), None)
    if row is None:
        raise MonitorError("Attempt does not belong to this run")
    observation = _observation(row)
    summary = _attempt_summary(row)
    raw, truncated = _read(root, ("diagnostics", "agents", run_id, identity, "stdout.log"), _MAX_TRACE_BYTES)
    source, scope = "runtime_diagnostics", "current_invocation"
    if raw is None:
        archive = _object(_object(observation.get("session")).get("transcript"))
        if archive:
            expected = f"{run_id}/agent/{identity}/transcript"
            if archive.get("path") != expected:
                raise MonitorError("transcript archive belongs to another Attempt or run")
            raw, truncated = _read(root, ("artifacts", run_id, "agent", identity, "transcript", "transcript.jsonl"), _MAX_TRACE_BYTES)
        source, scope = "published_transcript", "session_history"
    events = [] if raw is None else _trace_events(raw)
    result = _page("events", events, selectors, attempt=summary, available=raw is not None,
        source=source if raw is not None else None, scope=scope if raw is not None else "unavailable",
        corrections=summary["corrections"], turns=summary["turns"], raw_turns=summary["raw_turns"], truncated=truncated,
        trace_evidence=read_attempt_evidence(root, run_id, row))
    return result


def _participant_roles(value):
    roles = set()
    if isinstance(value, dict):
        if isinstance(value.get("role"), str):
            roles.add(value["role"])
        for child in value.values():
            if isinstance(child, (dict, list)):
                roles.update(_participant_roles(child))
    elif isinstance(value, list):
        for child in value:
            roles.update(_participant_roles(child))
    return roles


def _interactions(transitions, selectors, attempts=(), facts=()):
    groups = {}
    for row in transitions:
        if row.get("workflow_kind") != "RUN":
            continue
        event = _object(_payload(row).get("expert_interaction"))
        action = event.get("action", "")
        if not isinstance(action, str) or not action.startswith(("leader_", "debate_")):
            continue
        kind = "debate" if action.startswith("debate_") else "leader"
        identity = event.get("debate_id") if kind == "debate" else event.get("source_attempt_id")
        if identity is None:
            continue
        item = groups.setdefault((kind, identity), {"kind": kind, "interaction_id": identity,
            "state": "ACTIVE", "participants": [], "stages": [], "verdict": None,
            "goal_verdict_id": None, "source_attempt_ids": []})
        stage = {**event, **{key: row.get(key) for key in ("command_id", "revision", "created_at")}}
        item["stages"].append(stage)
        roles = _participant_roles(event)
        proposal = _object(event.get("proposal"))
        if isinstance(proposal.get("leader"), str):
            roles.add(proposal["leader"])
        if isinstance(event.get("leader"), str):
            roles.add(event["leader"])
        item["participants"] = sorted(set(item["participants"]) | roles)
        if event.get("source_attempt_id") and event["source_attempt_id"] not in item["source_attempt_ids"]:
            item["source_attempt_ids"].append(event["source_attempt_id"])
        if action.endswith("aborted"):
            item.update(state="ABORTED", goal_verdict_id=event.get("goal_verdict_id"))
        elif action.endswith("failed"):
            item["state"] = "FAILED"
        elif action in {"leader_round_finished", "debate_restarts_finished"}:
            item["state"] = "FAILED" if event.get("terminal_reason") == "AGENT_FINAL_FAILURE" else "FINISHED"
        elif action == "leader_not_started":
            item["state"] = "NOT_STARTED"
        elif action == "debate_verdict":
            item["verdict"] = event
            item["state"] = "RESTARTING"
    research = [(row, _object(_object(row.get("observation", _object(row.get("observation_json")))).get("request"))) for row in attempts if row.get("kind") == "AGENT"]
    for item in groups.values():
        if item["kind"] == "leader":
            observer = next((request for row, request in research if row["attempt_id"] == item["interaction_id"]), {})
            item["mode"] = "experience_sharing" if observer.get("schema_version", 0) >= 23 else "exclusive_round"
            publication = next((stage for stage in item["stages"] if stage.get("terminal_reason") == "GUIDANCE_ACCEPTED"), None)
            item["guidance"] = None
            item["provided_to"] = []
            if publication is not None:
                identity = publication["guidance_attempt_id"]
                item["source_attempt_ids"].append(identity)
                source = next((row for row, _ in research if row["attempt_id"] == identity
                    and row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED"), None)
                if source is not None:
                    observed = _object(source.get("observation", _object(source.get("observation_json"))))
                    item["guidance"] = {"attempt_id": identity, "author": publication["leader"]["role"],
                        "published_at": publication["created_at"],
                        **_object(_object(observed.get("response")).get("typed_candidate"))}
                    item["provided_to"] = [{"attempt_id": row["attempt_id"], "role": request.get("role"),
                        "component_id": request.get("component_id"),
                        **{name: _object(_object(request.get("task_context")).get("key")).get(name)
                            for name in ("cycle", "subkind")}}
                        for row, request in research if request.get("command") == "HYPOTHESIS_PROPOSAL"
                        and any(value.get("ref") == identity + "--leader-guidance" for value in request.get("input_artifacts", ()))]
            continue
        if item["kind"] != "debate": continue
        proposal = next((stage for stage in item["stages"] if stage["action"] == "debate_proposed"), None)
        if proposal is None: continue
        plans = [plan for stage in item["stages"] if stage["action"] == "debate_restarts_designated" for plan in stage["plans"]]
        skipped = [entry for stage in item["stages"] if stage["action"] == "debate_restarts_designated" for entry in stage["not_started"]]
        item["restarts"] = []
        for participant in proposal["proposal"]["debate"]["participants"]:
            scope = participant["scope"]
            role, component = scope["role"], scope["component_id"]
            plan = next((plan for plan in plans if (plan["role"], plan["component_id"]) == (role, component)), None)
            absent = next((entry for entry in skipped if (entry["role"], entry["component_id"]) == (role, component)), None)
            entered = [_object(_payload(row).get("task_context")).get("key", {}) for row in transitions
                if row["workflow_kind"] == "RUN" and row["revision"] < proposal["revision"]]
            source_cycle = max((key["cycle"] for key in entered if (key.get("expert_slot"), key.get("component_id")) == (role, component)), default=0)
            target_cycle = source_cycle + 1 if plan is None else plan["cycle"]
            owned = [row for row, request in research if (request.get("role"), request.get("component_id"),
                _object(_object(request.get("task_context")).get("key")).get("cycle")) == (role, component, target_cycle)]
            experiments = [row["workflow_id"] for row in transitions if row["workflow_kind"] == "EXPERIMENT"
                and row["revision"] == 1 and (lambda value: (value.get("role"), value.get("component_id"), value.get("expert_cycle"))
                    == (role, component, target_cycle))(_payload(row))]
            outcomes = [_payload(fact)["outcome_id"] for fact in facts if fact["fact_type"] == "CanonicalOutcomeAccepted"
                and _payload(fact).get("experiment_id") in experiments]
            state = ("NOT_STARTED" if absent is not None else "OUTCOMES_RECORDED" if outcomes
                else "EXPERIMENT_STARTED" if experiments else "RESEARCH_STARTED" if owned
                else "PLANNED" if plan is not None else "WAITING_FOR_SOURCE_CYCLE" if item["verdict"] else "WAITING_FOR_JUDGMENT")
            item["restarts"].append({"role": role, "component_id": component, "state": state,
                "source_cycle": source_cycle, "target_cycle": target_cycle,
                "parent_hypothesis_id": participant["hypothesis_id"] if plan is None else plan["parent_hypothesis_id"],
                "reason": None if absent is None else absent["reason"],
                "research_attempt_ids": [row["attempt_id"] for row in owned], "experiment_ids": experiments, "outcome_ids": outcomes})
    values = [item for item in groups.values() if not selectors.get("role") or selectors["role"] in item["participants"]]
    return _page("interactions", values, selectors)


def _resources(facts, transitions, attempts, commands, selectors):
    from .resource_experience import summarize_resource_measurements
    commands = {row["command_id"]: row for row in commands}
    experiments = []
    for fact in facts:
        if fact.get("fact_type") != "ExperimentPreregistered":
            continue
        spec = _payload(fact)
        identity = spec.get("experiment_id")
        contract = _object(spec.get("execution_contract"))
        source = commands.get(contract.get("source_submit_command_id"), {})
        submit = _object(source.get("request_json")) if (
            source.get("response_status") == "accepted" and source.get("command_type") == "CREATE_EXPERIMENT") else {}
        request = _object(submit.get("source_request"))
        estimate = _object(_object(submit.get("source_snapshot")).get("arguments")).get("resource_estimate")
        role = request.get("role")
        if role is None:
            role = next((_payload(row).get("role") for row in transitions if row.get("workflow_id") == identity and row.get("revision") == 1), None)
        rows = []
        for attempt in attempts:
            if attempt.get("kind") != "PROCESS" or attempt.get("experiment_id") != identity:
                continue
            observation = _observation(attempt)
            grant = contract.get("resource_grant")
            if grant is None:
                grant = next((_payload(row).get("resource_grant") for row in transitions
                    if row.get("workflow_id") == identity and _payload(row).get("attempt_id") == attempt["attempt_id"]
                    and _payload(row).get("resource_grant")), None)
            admitted = _object(contract.get("resource_intent")).get("workload_memory_mib")
            record = {"attempt_id": attempt["attempt_id"], "kind": "PROCESS", "state": attempt.get("state"),
                "workload": _object(estimate).get("workload"), "resource_usage": observation.get("resource_usage"),
                "admitted_workload_memory_mib": admitted, "oom": observation.get("resource_failure") == "gpu_out_of_memory",
                "terminal_status": attempt.get("terminal_reason"), "ended_at": attempt.get("ended_at")}
            measured = summarize_resource_measurements([record])
            rows.append({"attempt_id": attempt["attempt_id"], "state": attempt.get("state"),
                "terminal_reason": attempt.get("terminal_reason"), "grant": grant,
                "admitted": admitted, "measured": measured[0] if measured else None,
                "usage": list(_gpu_usage(observation.get("resource_usage")))})
        experiments.append({"experiment_id": identity, "preregistration_ref": fact.get("fact_id"),
            "role": role, "component_id": spec.get("component_id"), "method_family_id": spec.get("method_family_id"),
            "estimate": estimate, "resource_intent": contract.get("resource_intent"), "attempts": rows,
            "next_estimate_sources": []})
    for item in experiments:
        ids = {row["attempt_id"] for row in item["attempts"]}
        item["next_estimate_sources"] = [{"experiment_id": other["experiment_id"],
            "measurement_attempt_ids": sorted(ids & set(_object(other["estimate"]).get("measurement_attempt_ids", [])))}
            for other in experiments if ids & set(_object(other["estimate"]).get("measurement_attempt_ids", []))]
    experiments = [item for item in experiments if all(selectors.get(key) is None or
        selectors[key] == item.get(key) for key in ("role", "component_id", "experiment_id"))]
    if selectors.get("experiment_id") and not experiments:
        raise MonitorError("experiment does not belong to this run")
    return _page("experiments", experiments, selectors, shared_pool={"available": False,
        "reason": "Live shared capacity requires explicit Host resource information; no pool discovery is performed."})


def read_observer_pool(socket_path, *, expected_server_uid=None, timeout_seconds=0.25):
    """Query explicitly selected authorized aggregate capacity, never requests.

    The existing pool transport supplies its byte limit and optional peer UID
    check. The Web owner supplies configuration, not an HTTP path selector.
    Snapshot does not dispatch, release, or enumerate private run identities.
    """
    from .compute_resources import validate_gpu_uuid
    from .gpu_pool import PoolError
    from .gpu_pool_client import PoolClient
    if (not isinstance(socket_path, (str, Path)) or not str(socket_path)
            or "\x00" in str(socket_path)):
        raise ValueError("pool observation requires an explicit socket path")
    if (type(timeout_seconds) not in {int, float} or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 1):
        raise ValueError("pool observation timeout must be positive and at most one second")
    client = PoolClient(Path(socket_path), timeout_seconds=timeout_seconds,
                        expected_server_uid=expected_server_uid)
    scope = {"source": "explicit_gpu_pool_snapshot", "scope": "authorized_aggregate_capacity"}
    try:
        snapshot = client.request("snapshot", {})
        if not isinstance(snapshot.get("devices"), list):
            raise PoolError("invalid_response", "pool snapshot lacks devices")
        devices = []
        for raw in snapshot["devices"][:256]:
            if not isinstance(raw, dict):
                raise PoolError("invalid_response", "pool snapshot device is invalid")
            try:
                validate_gpu_uuid(raw.get("gpu_uuid"))
            except (ValueError, TypeError) as exc:
                raise PoolError("invalid_response", "pool snapshot UUID is invalid") from exc
            device = {"gpu_uuid": raw["gpu_uuid"]}
            for name in ("total_memory_mib", "free_memory_mib", "used_memory_mib",
                         "outstanding_memory_mib", "reserve_memory_mib", "allocatable_memory_mib",
                         "active_commitments", "unknown_attribution_commitments"):
                value = raw.get(name)
                device[name] = value if type(value) is int and 0 <= value < 2**53 else None
            devices.append(device)
        times = {}
        for name in ("sampling_started_at", "sampled_at"):
            value = snapshot.get(name)
            times[name] = value if type(value) in {int, float} and math.isfinite(value) and 0 <= value < 2**53 else None
        return {**scope, "available": True, **times, "devices": devices,
                "truncated": len(snapshot["devices"]) > 256}
    except PoolError as exc:
        code = exc.code if exc.code in {"unavailable", "unauthorized", "unauthorized_server",
            "invalid_response", "missing_inventory", "invalid_sample", "internal_error"} else "pool_query_failed"
        return {**scope, "available": False, "code": code, "devices": [],
                "reason": "Explicit shared pool observation is unavailable."}


def read_observer_detail(state_root, run_id, kind, **selectors):
    """Read one bounded detail by existing run identity; never accept a path."""
    _identity(run_id, "run_id")
    if not isinstance(kind, str) or kind not in _SELECTORS or set(selectors) - (_SELECTORS[kind] | {"offset", "limit"}):
        raise ValueError("unsupported observer detail selection")
    for key in ("offset", "limit"):
        if key in selectors and (type(selectors[key]) is not int or selectors[key] < (1 if key == "limit" else 0)
                                 or (key == "limit" and selectors[key] > 200)):
            raise ValueError("invalid observer detail pagination")
    for key, value in selectors.items():
        if key in {"offset", "limit"}:
            continue
        if key in {"commit", "base_commit"}:
            if not isinstance(value, str) or _COMMIT_RE.fullmatch(value) is None:
                raise ValueError("skill detail requires a full known commit")
        else:
            _identity(value, key)
    if kind == "skill" and (not selectors.get("role") or not selectors.get("name") or not _SKILL.fullmatch(selectors["name"])):
        raise ValueError("skill detail requires role and native skill name")
    if kind == "attempt" and not selectors.get("attempt_id"):
        raise ValueError("Attempt detail requires its identity")
    root = Path(state_root).resolve()
    with StateStore(root).readonly_connection() as db:
        db.execute("BEGIN")
        if db.execute("SELECT 1 FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone() is None:
            raise MonitorError("run is unavailable")
        # An explicitly selected Attempt needs no historical run-wide JSON scan.
        # Scope remains the conjunction of the configured run and primary ID.
        if kind == "attempt":
            attempts = [dict(row) for row in db.execute("SELECT * FROM execution_attempts WHERE run_id=? AND attempt_id=?",
                (run_id, selectors["attempt_id"]))]
        else:
            attempts = [dict(row) for row in db.execute("SELECT * FROM execution_attempts WHERE run_id=? ORDER BY started_at,attempt_id", (run_id,))]
        facts = [dict(row) for row in db.execute("SELECT * FROM research_facts WHERE run_id=? ORDER BY rowid", (run_id,))] if kind in {"resources", "interactions"} else []
        transitions = [dict(row) for row in db.execute("SELECT * FROM workflow_transitions WHERE run_id=? ORDER BY rowid", (run_id,))] if kind in {"interactions", "resources"} else []
        commands = [dict(row) for row in db.execute("SELECT * FROM workflow_commands WHERE run_id=?", (run_id,))] if kind == "resources" else []
    if kind == "skills":
        catalog = skill_catalog(root, run_id, attempts)
        values = [item for item in catalog.pop("skills") if not selectors.get("role") or item["role"] == selectors["role"]]
        result = _page("skills", values, selectors, **catalog)
    elif kind == "skill":
        result = _skill(root, run_id, attempts, selectors)
    elif kind == "literature":
        from .observer_summary import literature_catalog
        catalog = literature_catalog(root, run_id, attempts)
        values = [item for item in catalog.pop("papers") if not selectors.get("role") or selectors["role"] in item.get("roles", [])]
        result = _page("papers", values, selectors, **catalog)
    elif kind == "sessions":
        result = _sessions(attempts, selectors)
    elif kind == "attempt":
        result = _attempt_detail(root, run_id, attempts, selectors)
    elif kind == "interactions":
        result = _interactions(transitions, selectors, attempts, facts)
    else:
        result = _resources(facts, transitions, attempts, commands, selectors)
    return _clean({"run_id": run_id, "kind": kind, **result})
