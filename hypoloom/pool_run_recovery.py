"""Reviewed cancellation of stopped pool dependants through their frozen Host.

CREATED, CANCEL and pool request rows are authoritative. This module never
writes a run or pool database; only the ordinary Host owns those mutations.
"""
from __future__ import annotations

from contextlib import ExitStack, closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from .control_protocol import ControlRequest
from .gpu_pool import _identifier
from .gpu_pool_lifecycle import _hold_controller, _read_database
from .runner import process_group_exists

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


def selections(values=()) -> dict[str, int]:
    if values == ["none"]:
        return {}
    result = {}
    for value in values or ():
        run, separator, revision = value.rpartition(":")
        if not separator or not revision.isdecimal() or int(revision) < 1:
            raise ValueError("pool cancellation requires RUN_ID:REVISION")
        _identifier(run, "run_id")
        if run in result and result[run] != int(revision):
            raise ValueError("conflicting pool cancellation revisions")
        result[run] = int(revision)
    return result


def cancel_request(run: str, revision: int) -> ControlRequest:
    return ControlRequest(1, f"init-pool-cancel-{revision}", revision, "CANCEL",
                          {"run_id": run, "cause": "reviewed_init_pool_dependency_cleanup"})


def launch_binding(arguments: list[str]) -> dict | None:
    """Preserve non-secret original CLI inputs in the existing CREATED binding."""
    from .runtime_diagnostics import sanitize_text
    if not arguments or arguments[0] != "serve" or "--cancel-run-id" in arguments:
        return None
    secrets = tuple(os.environ[k] for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN") if os.environ.get(k))
    if any(sanitize_text(value, secrets) != value for value in arguments):
        return None  # Never store redacted, non-replayable input or a secret.
    return {"python": sys.executable, "arguments": arguments, "working_directory": str(Path.cwd())}


def read_target(root: Path, run: str, revision: int | None = None, *, allow_pending: bool = False, retire: bool = False) -> dict:
    """Bounded read-only validation; callers separately hold controller ownership."""
    if root.is_symlink() or not root.is_dir() or root.stat().st_uid != os.geteuid():
        raise ValueError("run_root_unavailable")
    with closing(_read_database(root / "state.sqlite")) as db:
        runs = db.execute("SELECT run_id FROM workflow_runs").fetchall()
        if [r["run_id"] for r in runs] != [run]:
            raise ValueError("recovery_requires_single_run_root")
        head = db.execute("SELECT state,revision FROM workflow_transitions WHERE workflow_id=? ORDER BY revision DESC LIMIT 1", (run,)).fetchone()
        first = db.execute("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND revision=1 AND state='CREATED'", (run,)).fetchone()
        if head is None or first is None:
            raise ValueError("run_binding_unavailable")
        accepted = False
        if revision is not None:
            if retire:
                from .legacy_run_closure import retirement_request
                request = retirement_request(run, revision)
            else:
                request = cancel_request(run, revision)
            prior = db.execute("SELECT request_json,response_status FROM workflow_commands WHERE command_id=?", (request.request_id,)).fetchone()
            accepted = prior is not None and prior["response_status"] == "accepted" and json.loads(prior["request_json"]) == request.to_mapping()
            if not accepted and head["revision"] != revision:
                raise ValueError("cancellation_review_changed")
        process_present = False
        for attempt in db.execute("SELECT process_group_id FROM execution_attempts WHERE run_id=?", (run,)):
            pgid = attempt["process_group_id"]
            if pgid is not None and (type(pgid) is not int or pgid < 2):
                raise ValueError("owned_process_present_or_unknown")
            process_present = process_present or bool(pgid and process_group_exists(pgid))
        if process_present and not (allow_pending and accepted):
            raise ValueError("owned_process_present_or_unknown")
        config = json.loads(first["payload_json"])
    binding = config.get("gpu_pool")
    if binding is not None:
        with closing(_read_database(Path(binding["socket_path"]).parent / "pool.sqlite3")) as db:
            requests = db.execute("SELECT state,process_group_id FROM requests WHERE uid=? AND namespace=? AND run_id=?",
                (os.geteuid(), binding["namespace"], run)).fetchall()
            if any(r["state"] != "RELEASED" or (r["process_group_id"] is not None and
                (type(r["process_group_id"]) is not int or r["process_group_id"] < 2 or process_group_exists(r["process_group_id"]))) for r in requests):
                raise ValueError("pool_requests_not_released")
    return {"run_id": run, "state_root": str(root), "state": head["state"], "revision": head["revision"],
            "cancel_accepted": accepted, "process_present": process_present,
            "launch": config.get("host_launch"), "gpu_pool": binding}


def recovery_launch(target: dict) -> tuple[list[str], str, str]:
    root = Path(target["state_root"])
    launch = target["launch"]
    if not isinstance(launch, dict) or not (root / "bundle/hypoloom/pool_run_recovery.py").is_file():
        raise ValueError("original_recovery_launch_unavailable")
    values = launch.get("arguments")
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values) or values[:1] != ["serve"]:
        raise ValueError("original_recovery_launch_invalid")
    # The bundled CLI validates every option again; no shell or new invocation
    # defaults are substituted. Only settings-based and fake Hosts are replayable.
    from .cli import build_parser
    try:
        args = build_parser().parse_args(values)
    except SystemExit:
        raise ValueError("original_recovery_launch_invalid") from None
    if (Path(args.state_root).resolve() != root.resolve() or args.cancel_run_id is not None
            or not args.exit_when_terminal or args.fault_point is not None):
        raise ValueError("original_recovery_launch_invalid")
    if args.agent_adapter == "claude" and not args.claude_settings:
        raise ValueError("original_environment_auth_unavailable")
    python = Path(launch["python"])
    directory = Path(launch["working_directory"])
    if not python.is_absolute() or not python.is_file() or not os.access(python, os.X_OK) or not directory.is_absolute() or not directory.is_dir():
        raise ValueError("original_recovery_launch_unavailable")
    if args.claude_settings:
        from .host_settings import PRIVATE_SETTINGS_NAME
        index = values.index("--claude-settings") if "--claude-settings" in values else -1
        if index < 0:
            raise ValueError("original_recovery_launch_invalid")
        values = list(values)
        values[index + 1] = str(root / PRIVATE_SETTINGS_NAME)
    return [str(python), "-m", "hypoloom.cli", *values], str(directory), "settings" if args.claude_settings else "none"


def review(dependencies: dict, values=(), *, retire_values=(), timeout: float = 8) -> dict:
    selected = selections(values)
    retirements = selections(retire_values)
    if selected.keys() & retirements.keys():
        raise ValueError("conflicting_pool_closure_selections")
    selected.update(retirements)
    plans = []
    known = set()
    deadline = time.monotonic() + timeout
    timed_out = False
    for binding in dependencies.get("bindings", []):
        if time.monotonic() >= deadline:
            timed_out = True
            break
        run = binding["run_id"]
        known.add(run)
        if binding["state"] in TERMINAL and run not in selected:
            continue
        item = {"run_id": run, "state_root": binding["state_root"], "selected": run in selected, "available": False}
        try:
            target = read_target(Path(binding["state_root"]), run, selected.get(run), allow_pending=True,
                                 retire=run in retirements)
            item["closure"] = "offline" if run in retirements else "original_host"
            item.update(state=target["state"], revision=selected.get(run, target["revision"]), cancel_accepted=target["cancel_accepted"])
            with ExitStack() as locks:
                try:
                    _hold_controller(Path(binding["state_root"]), locks)
                except BlockingIOError:
                    if target["cancel_accepted"]:
                        item.update(available=True, status="cancellation_pending")
                    else:
                        raise ValueError("host_present_or_unknown") from None
                else:
                    if target["process_present"]:
                        raise ValueError("owned_process_present_or_unknown")
                    if target["state"] in TERMINAL:
                        item.update(available=True, status="terminal")
                    else:
                        try:
                            recovery_launch(target)
                        except ValueError as exc:
                            if str(exc) != "original_recovery_launch_unavailable":
                                raise
                            from .legacy_run_closure import inspect_target
                            inspect_target(Path(target["state_root"]), run, target["revision"],
                                           timeout=max(.1, deadline - time.monotonic()))
                            item["closure"] = "offline"
                            if run in selected and run not in retirements:
                                raise ValueError("explicit_legacy_closure_required")
                        else:
                            if run in retirements:
                                raise ValueError("legacy_closure_not_required")
                        item.update(available=True, status="ready")
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            item["reason"] = str(exc) if isinstance(exc, ValueError) else "recovery_state_unavailable"
        plans.append(item)
    unknown = sorted(set(selected) - known)
    covered = {p["run_id"] for p in plans if p["available"] and p["selected"]}
    possible = not timed_out and not unknown and all(b.get("run_id") in covered and b["reason"] in {
        "run_depends_on_pool", "open_attempt", "host_present_or_unknown", "process_present"} for b in dependencies.get("blockers", []))
    return {"runs": plans, "unknown_selections": unknown, "timed_out": timed_out,
            "prepare_after_cleanup": possible and all(p["available"] for p in plans if p["selected"])}


def request_cleanup(repository: Path, plan: dict, *, timeout: float = 15) -> dict:
    if plan["status"] in {"terminal", "cancellation_pending"}:
        return {"run_id": plan["run_id"], "status": plan["status"]}
    command = [sys.executable, "-B", str(repository / ".claude/skills/autoresearch/scripts/manage_host.py"),
        "retire" if plan.get("closure") == "offline" else "recover",
        "--state-root", plan["state_root"], "--run-id", plan["run_id"],
        "--expected-revision", str(plan["revision"]), "--ready-timeout", str(min(timeout, 10))]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 2,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        response = json.loads(result.stdout)
        return {"run_id": plan["run_id"], "status": response["status"]}
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        return {"run_id": plan["run_id"], "status": "cancellation_start_uncertain"}
