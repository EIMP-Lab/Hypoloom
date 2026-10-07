"""Read-only time series from Attempt intervals and existing relay metadata.

No sampler, persistent counter, or browser lifetime participates in collection.
These are recorded occupancy and diagnostic request counts, never liveness or
provider-account usage. Missing capture remains unknown, not zero traffic.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import stat
import time
from datetime import datetime
from pathlib import Path

from .observer_scope import attempt_scopes
from .state_store import StateStore


def _time(value):
    if type(value) in (float, int) and math.isfinite(value):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return None


def _safe_directory(root, *parts):
    path = root
    for part in parts:
        if not isinstance(part, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", part) or part in {".", ".."}:
            raise ValueError("invalid diagnostic identity")
        path = path / part
        if path.is_symlink():
            raise ValueError("linked diagnostics unavailable")
    return path


def build_run_trends(state_root, run_id, *, now=None, max_points=1440,
                     max_files=100000, inspection_seconds=5.0):
    """Bounded run-wide projection, independent of selected support Attempts."""
    if type(max_points) is not int or not 1 <= max_points <= 1440:
        raise ValueError("invalid trend point limit")
    root = Path(state_root).resolve()
    observed = time.time() if now is None else now
    with StateStore(root).readonly_connection() as db:
        db.execute("BEGIN")
        run = db.execute("SELECT created_at FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            raise ValueError("run unavailable")
        transitions = [dict(row) for row in db.execute("SELECT state, created_at FROM workflow_transitions "
            "WHERE run_id=? AND workflow_kind='RUN' ORDER BY revision", (run_id,))]
        # Only ownership fields are needed; avoid decoding prompts, workspaces,
        # transcripts, and scientific results on a chart request.
        attempts = [dict(row) for row in db.execute("""SELECT attempt_id, run_id, command_id, kind,
            state, started_at, ended_at, process_group_id,
            json_object('request', json_object(
                'command', json_extract(observation_json,'$.request.command'),
                'role', json_extract(observation_json,'$.request.role'),
                'task_context', json_extract(observation_json,'$.request.task_context'))) AS observation_json
            FROM execution_attempts WHERE run_id=? ORDER BY started_at, attempt_id""", (run_id,))]
        commands = [dict(row) for row in db.execute("""SELECT command_id,
            json_object('paper_reading', json_extract(request_json,'$.paper_reading')) AS request_json
            FROM workflow_commands WHERE run_id=? AND json_type(request_json,'$.paper_reading')='object'""", (run_id,))]
    started = min([_time(run["created_at"]) or observed, *[row["started_at"] for row in attempts]])
    terminal = next((row for row in transitions if row["state"] in {"COMPLETED", "FAILED", "CANCELLED"}), None)
    ended = min(observed, _time(terminal["created_at"]) or observed) if terminal else observed
    ended = max(started, ended)
    # Minute buckets match RPM; long runs coarsen to an explicit average rate.
    step = max(60, math.ceil(max(0, ended - started) / max_points / 60) * 60)
    count = max(1, math.ceil((ended - started) / step))
    points = [dict(at=started + i * step, until=min(ended, started + (i + 1) * step),
        experiments=0, experiments_peak=0, agents=0, agents_peak=0,
        requests=0, rpm=0, rpm_lower_bound=0, incomplete_attempts=0) for i in range(count)]
    scopes = attempt_scopes(attempts, commands, include_run_group=True)
    roles = sorted({s["owner_role"] for s in scopes.values() if s["task_scope"] == "expert"})
    series = [{"role": role, "requests": [0] * count, "rpm": [0] * count,
               "rpm_lower_bound": [0] * count, "incomplete_attempts": [0] * count} for role in roles]
    groups = {item["role"]: item for item in series}
    run_groups = {}
    for key in ("grounding", "other"):
        run_groups[key] = {"requests": [0] * count, "rpm": [0] * count,
                       "rpm_lower_bound": [0] * count, "incomplete_attempts": [0] * count}
    def indices(start, finish):
        lo = max(0, min(count - 1, int((start - started) // step)))
        hi = max(lo + 1, min(count, math.ceil((finish - started) / step)))
        return range(lo, hi)

    # Sweep recorded intervals. No OPEN row is asserted to be a live OS process.
    for kind, key in (("PROCESS", "experiments"), ("AGENT", "agents")):
        events = []
        for row in attempts:
            if row["kind"] != kind or row["process_group_id"] is None:
                continue  # Admission failures and pre-spawn synthetic closures.
            start, finish = row["started_at"], row["ended_at"]
            if finish is not None and finish <= start:
                continue
            events.append((start, 1))
            if finish is not None:
                events.append((finish, -1))
        events.sort()  # Ends before starts at the same instant.
        cursor, active = 0, 0
        for point in points:
            while cursor < len(events) and events[cursor][0] <= point["at"]:
                active += events[cursor][1]
                cursor += 1
            peak = active
            while cursor < len(events) and events[cursor][0] <= point["until"]:
                active += events[cursor][1]
                peak = max(peak, active)
                cursor += 1
            point[key], point[key + "_peak"] = active, peak

    deadline = time.monotonic() + inspection_seconds
    coverage = dict(availability="recorded", inspected_files=0, request_count=0,
                    incomplete_attempts=0, reasons={})
    for row in attempts:
        if row["kind"] != "AGENT":
            continue
        scope = scopes[row["attempt_id"]]
        group = (groups[scope["owner_role"]] if scope["task_scope"] == "expert" else
                 run_groups["grounding"] if scope["run_group"] == "grounding" else run_groups["other"])
        reasons = set()
        try:
            directory = _safe_directory(root, "diagnostics", "agents", run_id, row["attempt_id"], "provider")
            if not directory.is_dir() and row["process_group_id"] is None:
                continue  # No recorded model launch.
            found = False
            with os.scandir(directory) as children:
                for child in children:
                    if time.monotonic() >= deadline or coverage["inspected_files"] >= max_files:
                        reasons.add("inspection_limit")
                        break
                    if not re.fullmatch(r"[0-9]{6}", child.name):
                        continue
                    found = True
                    coverage["inspected_files"] += 1
                    try:
                        path = Path(child.path) / "exchange.json"
                        if child.is_symlink() or path.is_symlink() or not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size > 65536:
                            raise ValueError("unavailable metadata")
                        with path.open("rb") as stream:
                            exchange = json.loads(stream.read(65537))
                        if not isinstance(exchange, dict):
                            raise ValueError("invalid metadata")
                        when = _time(exchange.get("upstream_started_at"))
                        if when is None:
                            # Completed local rejections did not reach upstream.
                            if (exchange.get("ended_at") is None or exchange.get("upstream_status") is not None
                                    or exchange.get("request_bytes_forwarded", 0) > 0
                                    or str(exchange.get("failure_stage", "")).startswith("upstream")):
                                reasons.add("upstream_time_unavailable")
                            continue
                        if not started <= when <= ended:
                            continue
                        index = min(count - 1, int((when - started) // step))
                        points[index]["requests"] += 1
                        group["requests"][index] += 1
                        coverage["request_count"] += 1
                    except (OSError, ValueError, TypeError, RecursionError):
                        reasons.add("metadata_unavailable")
            if not found:
                reasons.add("capture_unavailable")
        except (OSError, ValueError):
            reasons.add("capture_unavailable")
        if reasons:
            coverage["incomplete_attempts"] += 1
            for reason in reasons:
                coverage["reasons"][reason] = coverage["reasons"].get(reason, 0) + 1
            for index in indices(row["started_at"], row["ended_at"] or ended):
                points[index]["incomplete_attempts"] += 1
                group["incomplete_attempts"][index] += 1
    for i, point in enumerate(points):
        duration = max(1, point["until"] - point["at"])
        point["rpm_lower_bound"] = round(point["requests"] * 60 / duration, 3)
        point["rpm"] = None if point["incomplete_attempts"] else point["rpm_lower_bound"]
        for group in [*groups.values(), *run_groups.values()]:
            group["rpm_lower_bound"][i] = round(group["requests"][i] * 60 / duration, 3)
            group["rpm"][i] = None if group["incomplete_attempts"][i] else group["rpm_lower_bound"][i]
    if coverage["incomplete_attempts"]:
        coverage["availability"] = "partial" if coverage["request_count"] else "unavailable"
    return dict(run_id=run_id, observed_at=observed, started_at=started, ended_at=ended,
                terminal=terminal is not None, interval_seconds=step, points=points,
                experts=series, grounding=run_groups["grounding"], other=run_groups["other"],
                coverage=coverage, non_authoritative=True,
                occupancy_semantics="recorded_bound_attempts_at_bucket_end_and_peak_not_host_liveness",
                rpm_semantics="recorded_outbound_requests_per_bucket_minute_including_retries_not_account_usage")


def trends_csv(trends):
    """Long-form export keeps disposable public role aliases out of field names."""
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["at", "until", "scope", "role", "experiments", "experiments_peak",
                     "agents", "agents_peak", "requests", "rpm", "rpm_lower_bound", "incomplete_attempts"])
    for i, point in enumerate(trends.get("points", [])):
        writer.writerow([point["at"], point["until"], "run", "", *[point[key] for key in
            ("experiments", "experiments_peak", "agents", "agents_peak", "requests", "rpm", "rpm_lower_bound", "incomplete_attempts")]])
        for scope, group in [("expert", g) for g in trends["experts"]] + [(key, trends[key]) for key in ("grounding", "other")]:
            role = group.get("role", "")
            # CSV readers must not interpret private role labels as formulae.
            if role.startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
                role = "'" + role
            writer.writerow([point["at"], point["until"], scope, role, "", "", "", "",
                             *[group[key][i] for key in ("requests", "rpm", "rpm_lower_bound", "incomplete_attempts")]])
    return stream.getvalue().encode("utf-8")
