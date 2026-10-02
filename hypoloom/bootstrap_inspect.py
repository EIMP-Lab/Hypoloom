"""One read-only init inspection using the existing admission and launch validators."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import importlib.util
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

from . import bootstrap
from .model_options import inspect_environment, inspect_settings, ResolverError


def _gpu_inventory(repository: Path) -> dict:
    try:
        completed = subprocess.run([sys.executable, "-B", "-m", "hypoloom.bootstrap", "inventory",
            "--repository", str(repository)], capture_output=True, text=True, timeout=5,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "HYPOLOOM_RUNTIME_FAMILY": "hypoloom"})
        if completed.returncode == 0:
            return {"status": "observed", **json.loads(completed.stdout)}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return {"status": "unavailable", "devices": [], "note": "Observation unavailable; no GPU authorization inferred"}


def supervisor_check() -> dict:
    required = ("systemctl", "systemd-run", "systemd-escape")
    if any(shutil.which(name) is None for name in required):
        return {"status": "unavailable", "reason": "user_systemd_tools_unavailable"}
    try:
        result = subprocess.run(["systemctl", "--user", "show", "--property=Version", "--value"],
            capture_output=True, text=True, timeout=3)
        if result.returncode:
            return {"status": "unavailable", "reason": "user_manager_unreachable"}
        linger = subprocess.run(["loginctl", "show-user", str(os.geteuid()), "--property=Linger", "--value"],
            capture_output=True, text=True, timeout=2)
        value = linger.stdout.strip() if linger.returncode == 0 else "unknown"
        return {"status": "ready", "linger": value if value in {"yes", "no"} else "unknown"}
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "unavailable", "reason": "user_manager_check_failed"}


def _auth(args, repository: Path) -> dict:
    if args.auth_transport == "none":
        return {"status": "fake", "auth_source": "none"}
    source = args.auth_transport or ("settings" if args.claude_settings else
        "environment" if os.environ.get("ANTHROPIC_API_KEY") else "settings")
    try:
        if source == "environment":
            if args.claude_settings: raise ResolverError("mixed_auth_sources")
            result = inspect_environment()
        else:
            configured = os.environ.get("CLAUDE_CONFIG_DIR", "")
            root = Path(configured) if configured and Path(configured).is_absolute() else Path.home() / ".claude"
            selected = Path(args.claude_settings) if args.claude_settings else root / "settings.json"
            result = inspect_settings(selected, repository=repository)
            result["settings_path"] = str(selected)
        return {"status": "qualified", **result}
    except (ValueError, OSError) as exc:
        return {"status": "unavailable", "auth_source": source,
                "reason": exc.code if isinstance(exc, ResolverError) else "auth_source_unavailable"}


def _pools(repository: Path) -> dict:
    from . import managed_gpu_pool as manager
    # Endpoint discovery only. Historical run census is deferred to maintenance,
    # where it can affect the decision, instead of every ordinary CPU init.
    root = repository / ".local/gpu-pools/shared"
    return manager._endpoint_entry(root / "control.sock", root)


def _launch_argv(args, repository: Path) -> list[str]:
    """Exact validated choices, not shell text, and no secret literals."""
    values = [args.control_python, "-B", "-m", "hypoloom.bootstrap", "launch", args.profile,
        "--repository", str(repository), "--run-id", args.run_id,
        "--allow-gpu" if args.allow_gpu else "--cpu-only", "--summary"]
    for name in ("state_root", "request_id", "control_python", "ready_timeout", "process_delay", "auth_transport",
        "claude_executable", "claude_model", "claude_settings", "claude_bwrap", "gpu_runtime_prefix",
        "gpu_pool_socket", "gpu_pool_namespace", "gpu_pool_server_uid", "web_search_fallback",
        "notification_output_root", "notification_proxy_env", "notification_credentials_file",
        "pool_action", "pool_reserve_mib"):
        value = getattr(args, name)
        if value is not None: values += ["--" + name.replace("_", "-"), str(value)]
    for name in ("gpu_device_uuid", "pool_device_uuid"):
        flag = "--" + name.replace("_", "-")
        for value in getattr(args, name) or (): values += [flag, value]
    if args.managed_pool: values += ["--managed-pool"]
    if args.nonpooled: values += ["--nonpooled"]
    from .institutional_options import launch_arguments as institutional_arguments
    return [*values, *bootstrap._literature_arguments(args), *institutional_arguments(args, include_connection=True)]


def inspect(args, repository: Path) -> dict:
    from .version_check import check_updates
    before = time.monotonic()
    phases = []
    def timed(name, function):
        start = time.monotonic()
        value = function()
        return value, {"phase": name, "seconds": round(time.monotonic() - start, 3)}
    tasks = {
        "auth": lambda: _auth(args, repository),
        "supervisor": supervisor_check,
        "version": lambda: check_updates(repository, offline=args.offline, timeout=args.version_timeout),
    }
    if args.allow_gpu is not False:
        tasks["gpu_inventory"] = lambda: _gpu_inventory(repository)
        tasks["project_pool"] = lambda: _pools(repository)
    if args.institutional_browser_mode or args.institutional_browser_url:
        from .institutional_options import preview as institutional_preview
        tasks["institutional_connection"] = lambda: institutional_preview(args, repository)["institutional_connection"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {name: pool.submit(timed, name, function) for name, function in tasks.items()}
        checks = {}
        for name, future in futures.items():
            checks[name], phase = future.result()
            phases.append(phase)
    result = {"status": "needs_input", "paths": bootstrap.locations(repository), "checks": checks,
        "missing": [], "blockers": [], "profile_written": False, "provider_probe_performed": False}
    missing, blockers = result["missing"], result["blockers"]
    if sqlite3.sqlite_version_info < (3, 51, 3): blockers.append({"code": "control_sqlite_too_old"})
    if importlib.util.find_spec("filelock") is None: blockers.append({"code": "control_filelock_unavailable"})
    if Path(args.control_python).resolve() != Path(sys.executable).resolve():
        blockers.append({"code": "control_python_mismatch", "message": "Run inspect with the selected control Python"})
    result["project_settings"] = bootstrap._path_info(repository / ".claude/settings.json")
    result["control"] = {"python": str(Path(sys.executable).resolve()), "sqlite": sqlite3.sqlite_version}
    if not args.profile:
        missing.append("profile")
    if args.allow_gpu is None:
        missing.append("execution_mode")
    if args.institutional_browser_mode is None and not args.institutional_browser_url:
        from .institutional_connection import BROWSER_MODES
        missing.append("institutional_browser_mode")
        result["choices"] = {"institutional_browser_mode": [
            {"value": value, "description": description} for value, description in BROWSER_MODES.items()]}
    auth = checks["auth"]
    if auth["status"] == "unavailable":
        missing.append("model_auth_source")
    else:
        args.auth_transport = auth["auth_source"]
        args.claude_settings = auth.get("settings_path")
        if args.auth_transport != "none":
            args.claude_executable = args.claude_executable or shutil.which("claude")
            if args.claude_executable: args.claude_executable = str(Path(args.claude_executable).resolve())
            if not args.claude_executable: missing.append("claude_executable")
            default = auth.get("configured_default") or {}
            args.claude_model = args.claude_model or default.get("claude_model")
            if not args.claude_model: missing.append("claude_model")
            args.literature_adapter = "openalex"
    if args.allow_gpu and not args.gpu_device_uuid: missing.append("gpu_device_uuid")
    if args.allow_gpu and not (args.managed_pool or args.gpu_pool_socket):
        # No implicit authorization to drop the pool. Explicit nonpooled choice
        # is represented by --nonpooled; budget admission still validates it.
        if not args.nonpooled: missing.append("gpu_scheduling_mode")
    if args.nonpooled and args.gpu_pool_socket: blockers.append({"code": "conflicting_gpu_modes"})
    if checks["supervisor"]["status"] != "ready": blockers.append({"code": checks["supervisor"]["reason"]})
    if args.profile:
        args.run_id = args.run_id or f"{args.profile}-{datetime.now().strftime('%Y%m%d%H%M')}"
        args.state_root = args.state_root or str(repository / "state" / args.run_id)
        args.request_id = args.request_id or args.run_id + "-create-1"
        result["identity"] = {"run_id": args.run_id, "state_root": args.state_root, "request_id": args.request_id}
        start = time.monotonic()
        destination, texts = bootstrap.candidate(repository, args.profile, args.template, args.overrides)
        report = bootstrap.admit_profile(destination, run_id=args.run_id, allow_gpu=bool(args.allow_gpu),
            delay_seconds=args.process_delay, source_texts=texts)
        raw = {"admission": report.to_dict()}
        result.update(bootstrap.compact_result(raw))
        if report.admitted:
            try:
                from .cli import _profile_literature_source
                _profile_literature_source(args, report)
                result["literature_preflight"] = bootstrap._literature_preflight(args)
                if not missing:
                    review = {}
                    bootstrap.launch_arguments(args, repository, report, review=review)
                    result.update(bootstrap.compact_result({**raw, "effective_configuration": review}))
                    pool = review.get("gpu", {}).get("pool", {})
                    if args.managed_pool and pool.get("prepare_available"):
                        args.pool_action = pool["action"]
                        args.pool_device_uuid = pool["members"][str(os.geteuid())]
                        args.pool_reserve_mib = pool["reserve_mib"]
                    elif pool and not pool.get("start_ready"):
                        blockers.append({"code": pool.get("reason") or pool.get("code") or "pool_unavailable"})
                    if args.template:
                        result["profile_preparation_required"] = True
                    else:
                        result["launch_argv"] = _launch_argv(args, repository)
            except (ValueError, OSError) as exc:
                blockers.append({"code": "launch_preflight_failed", "message": str(exc)})
        else:
            blockers.extend(report.to_dict()["blockers"])
        phases.append({"phase": "profile_and_launch_checks", "seconds": round(time.monotonic() - start, 3)})
    result["status"] = "needs_input" if missing else "blocked" if blockers else "ready_for_review"
    result["phases"] = phases
    result["elapsed_seconds"] = round(time.monotonic() - before, 3)
    result["next_actions"] = ["complete_choices" if missing else "review_start" if not blockers else "recheck_or_revise", "prepare_only", "cancel"]
    return result
