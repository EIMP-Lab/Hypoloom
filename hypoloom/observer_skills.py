"""Disposable skill usage and evolution views over existing invocation/Git evidence."""
from __future__ import annotations

import difflib
import math
import time
from collections import Counter

from . import observer_details as details
from .observer_evidence import evidence_read_budget, read_attempt_evidence


def skill_diff(old, new, name, base, commit):
    """Include references/scripts and removed files, not just SKILL.md."""
    result = {"base_commit": base, "commit": commit, "available": old is not None and new is not None,
              "text": "", "files": [], "truncated": False}
    if not result["available"]:
        return result
    prefix = f"skills/{name}/"
    chunks, size = [], 0
    for path in sorted(set(old) | set(new)):
        if not path.startswith(prefix) or old.get(path) == new.get(path):
            continue
        change = "added" if path not in old else "deleted" if path not in new else "modified"
        result["files"].append({"path": path, "change": change})
        before, after = old.get(path, b""), new.get(path, b"")
        if b"\0" in before or b"\0" in after:
            text = f"Binary file {change}: {path}\n"
        else:
            text = "".join(difflib.unified_diff(
                before.decode("utf-8", errors="replace").splitlines(True),
                after.decode("utf-8", errors="replace").splitlines(True),
                fromfile=f"{base}/{path}", tofile=f"{commit}/{path}"))
        remaining = max(0, details._MAX_TEXT - size)
        chunks.append(text[:remaining])
        size += len(text)
    result.update(text="".join(chunks), truncated=size > details._MAX_TEXT)
    return result


def _counter(role, name, commit=None):
    return {"role": role, "name": name, **({"commit": commit} if commit else {}),
            "calls": 0, "succeeded": 0, "failed": 0, "unanswered": 0,
            "last_called_at": None}


def _evolutions(root, run_id, attempts, versions, role, since):
    known = {(item["role"], item["commit"]): item for item in versions}
    events = []
    for row in attempts:
        observation = details._observation(row)
        request = details._object(observation.get("request"))
        if request.get("command") not in {"SELF_EVOLVE", "GRAPH_EVOLVE"}:
            continue
        owner = request.get("role")
        if role and role != owner:
            continue
        ended = details._time(row.get("ended_at"))
        if max(ended, details._time(row.get("started_at"))) < since:
            continue
        update = details._object(observation.get("plugin_update"))
        base, commit = update.get("base_commit"), update.get("commit")
        event = {"attempt_id": row["attempt_id"], "role": owner,
                 "command": request["command"], "started_at": row.get("started_at"),
                 "ended_at": row.get("ended_at"), "base_commit": base, "commit": commit,
                 "cycle": details._object(details._object(request.get("task_context")).get("key")).get("cycle"),
                 "status": "running" if row.get("state") == "OPEN" else "failed",
                 "skills": [], "changes_available": False, "later_bindings": 0}
        if row.get("state") == "CLOSED" and row.get("terminal_reason") == "SUCCEEDED":
            event["status"] = "unknown"
            source, target = known.get((owner, base)), known.get((owner, commit))
            if source and target:
                event["status"] = "unchanged" if base == commit else "accepted_change"
                old, new = details._files(root, run_id, source), details._files(root, run_id, target)
                event["changes_available"] = old is not None and new is not None
                if event["changes_available"]:
                    names = sorted({path.split("/")[1] for path in set(old) | set(new)
                                    if old.get(path) != new.get(path)})
                    event["skills"] = [{"name": name,
                        "removed": f"skills/{name}/SKILL.md" not in new,
                        "files": skill_diff(old, new, name, base, commit)["files"]} for name in names]
                event["later_bindings"] = sum(
                    binding["attempt_id"] != row["attempt_id"] and details._time(binding.get("started_at")) >= ended
                    for binding in target["bindings"])
        events.append(event)
    return sorted(events, key=lambda event: (details._time(event.get("started_at")), event["attempt_id"]), reverse=True)


def skill_activity(root, run_id, attempts, selectors, versions, *, transitions=(), facts=()):
    """Bounded current-invocation reads. Partial capture is a lower bound.

    Request binding identifies the version, never proves a Skill call. Reuse
    the invocation parser/cache so resumed session archives are not counted.
    """
    role, command = selectors.get("role"), selectors.get("command")
    rows = [row for row in attempts if row.get("kind") == "AGENT" and row.get("run_id") == run_id]
    until = max((details._time(row.get("ended_at") or row.get("started_at")) for row in rows), default=0)
    if any(row.get("state") == "OPEN" for row in rows):
        until = max(until, time.time())
    since = max(0, until - int(selectors["window"])) if selectors.get("window") else 0
    selected, bindings, evidence_by_attempt = [], {}, {}
    known_versions = {(item["role"], item["commit"]): item for item in versions}
    for row in rows:
        binding = details._request_plugin(row, run_id)
        bindings[row["attempt_id"]] = binding
        if binding is None or role and binding["role"] != role:
            continue
        if selectors.get("commit") and binding["commit"] != selectors["commit"]:
            continue
        request = details._object(details._observation(row).get("request"))
        if command and request.get("command") != command:
            continue
        if details._time(row.get("ended_at") or until) < since:
            continue
        selected.append((row, binding, request))

    by_version, by_skill, calls, version_files = {}, {}, [], {}
    coverage = Counter()
    untimed = unmatched = 0
    deadline = time.monotonic() + 4
    # Newest calls remain useful on a very long run. Missing older capture is
    # explicitly counted; no persisted parallel counter or archive rescan.
    with evidence_read_budget(32):
        for row, binding, request in reversed(selected):
            evidence = read_attempt_evidence(root, run_id, row) if time.monotonic() < deadline else {"coverage": "unavailable"}
            evidence_by_attempt[row["attempt_id"]] = evidence
            coverage[evidence["coverage"]] += 1
            for call in evidence.get("skill_calls", ()):
                qualified = call["name"]
                prefix = binding.get("name", "") + ":"
                if not qualified.startswith(prefix) or not details._SKILL.fullmatch(qualified[len(prefix):]):
                    unmatched += 1
                    continue
                timestamp = details._time(call.get("timestamp")) or None
                if timestamp is not None and not since <= timestamp <= until:
                    continue
                # Untimed calls cannot be assigned to a requested time window.
                if timestamp is None:
                    untimed += 1
                    if selectors.get("window"):
                        continue
                name = qualified[len(prefix):]
                if selectors.get("name") and name != selectors["name"]:
                    continue
                identity = (binding["role"], name, binding["commit"])
                counters = (by_version.setdefault(identity, _counter(*identity)),
                            by_skill.setdefault(identity[:2], _counter(*identity[:2])))
                for counter in counters:
                    counter["calls"] += 1
                    counter[call["status"]] += 1
                    if not counter.get("last_called_commit") or timestamp is not None and timestamp >= (counter["last_called_at"] or 0):
                        counter["last_called_commit"] = binding["commit"]
                        counter["last_attempt_id"] = row["attempt_id"]
                        version_key = (binding["role"], binding["commit"])
                        if version_key not in version_files:
                            version = known_versions.get(version_key)
                            version_files[version_key] = (None if version is None or version.get("available") is False
                                else details._files(root, run_id, version))
                        files = version_files[version_key]
                        counter["entrypoint_available"] = files is not None and f"skills/{name}/SKILL.md" in files
                    if timestamp is not None:
                        counter["last_called_at"] = max(timestamp, counter["last_called_at"] or 0)
                calls.append({"role": binding["role"], "name": name, "commit": binding["commit"],
                    "attempt_id": row["attempt_id"], "tool_use_id": call["tool_use_id"],
                    "command": request.get("command"), "timestamp": timestamp, "status": call["status"]})

    complete = not coverage["partial"] and not coverage["unavailable"] and not unmatched
    if selectors.get("window") and untimed:
        complete = False
    state = ("unavailable" if not selected else "complete" if complete else
             "partial" if coverage["complete"] or coverage["partial"] else "unavailable")
    for counter in (*by_skill.values(), *by_version.values()):
        counter["coverage"] = state
    timed = [call for call in calls if call["timestamp"] is not None]
    start = since or min((details._time(row.get("started_at")) for row, _, _ in selected), default=until)
    width = max(60, math.ceil(max(0, until - start) / 60))
    bin_count = max(1, math.ceil(max(0, until - start) / width))
    bins = {index: {"started_at": start + index * width,
        "ended_at": min(until, start + (index + 1) * width), "calls": 0, "failed": 0}
        for index in range(bin_count)}
    for call in timed:
        index = min(bin_count - 1, max(0, int((call["timestamp"] - start) // width)))
        bucket = bins[index]
        bucket["calls"] += 1
        bucket["failed"] += call["status"] == "failed"
    evolutions = _evolutions(root, run_id, rows, versions, role, since)
    for event in evolutions:
        changed_names = {skill["name"] for skill in event["skills"]}
        event["observed_later_calls"] = sum(call["role"] == event["role"] and call["commit"] == event["commit"]
            and call["name"] in changed_names
            and call["attempt_id"] != event["attempt_id"] and details._time(call["timestamp"]) >= details._time(event["ended_at"])
            for call in calls)
    from .skill_observations import outcome_nodes, process_outcomes, process_repairs, scorecards, followups
    nodes = outcome_nodes(root, run_id, rows, bindings, transitions=transitions, facts=facts, evidence=evidence_by_attempt)
    processes = process_outcomes(root, run_id, attempts, bindings, transitions=transitions, facts=facts)
    effects = scorecards(nodes, calls, selected_ids={row["attempt_id"] for row, _, _ in selected}, name=selectors.get("name"))
    effects.update(process_repairs(processes, selected_ids={row["attempt_id"] for row, _, _ in selected}))
    effects.update(followups([node for node in nodes if not command or node["node"] == command],
        evolutions, name=selectors.get("name"), processes=processes))
    return {"coverage": state, "attempts": len(selected), "capture": dict(coverage),
        "effects": effects,
        "started_at": start, "ended_at": until, "bin_seconds": width, "untimed_calls": untimed,
        "unmatched_calls": unmatched, "observed_calls": len(calls),
        "skills": sorted(by_skill.values(), key=lambda item: (-item["calls"], item["role"], item["name"])),
        "versions": list(by_version.values()), "timeline": [bins[key] for key in sorted(bins)],
        "calls": sorted(calls, key=lambda item: item["timestamp"] or 0, reverse=True)[:200],
        "calls_truncated": len(calls) > 200, "evolutions": evolutions[:200],
        "evolutions_truncated": len(evolutions) > 200}
