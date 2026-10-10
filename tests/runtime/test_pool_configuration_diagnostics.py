"""Configuration-read errors remain distinct from durable pool dependencies."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from hypoloom import managed_gpu_pool as manager
from test_managed_pool_lifecycle import GPU, managed  # noqa: F401


@pytest.mark.parametrize("failure,detail", [
    ("identity", None),
    ("unit_query", "unit_working_directory_query_failed"),
    ("unit_relative", "unit_working_directory_unreadable"),
    ("unit_empty", "unit_working_directory_unreadable"),
    ("unit_directory", "unit_working_directory_mismatch"),
    ("unit_arguments", "process_arguments:PermissionError"),
    ("unit_command", "unexpected_service_command"),
    ("unit_endpoint", "pool_endpoint_mismatch"),
    ("owner", "process_owner_mismatch"),
    ("owner_unreadable", "process_owner:PermissionError"),
    ("gone", "process_working_directory:FileNotFoundError"),
    ("directory", "process_working_directory_mismatch"),
    ("arguments", "process_arguments:PermissionError"),
    ("command", "unexpected_service_command"),
    ("endpoint", "pool_endpoint_mismatch"),
    ("pid", "main_pid:ValueError"),
    ("query", "main_pid_query_failed"),
    (None, None),
])
def test_live_configuration_reports_only_safe_failure_details(tmp_path, monkeypatch, failure, detail):
    root = tmp_path / ".local/gpu-pools/shared"
    plan = manager.PoolPlan(tmp_path, root, root / "control.sock", ((os.geteuid(), (GPU,)),), 512, "unit")
    tools = manager.Tools("escape", "run", "ctl")
    process = Path("/proc") / str(os.getpid())
    resolve, read, stat = Path.resolve, Path.read_bytes, Path.stat
    queries = []
    fallback = failure == "identity" or str(failure).startswith("unit_")

    def command(argv, **kw):
        queries.append(argv)
        if "--property=WorkingDirectory" in argv:
            value = {"unit_relative": "relative", "unit_empty": "",
                     "unit_directory": str(tmp_path.parent)}.get(failure, str(tmp_path))
            return subprocess.CompletedProcess(argv, int(failure == "unit_query"), value + "\n", "private-error")
        assert "--property=MainPID" in argv
        return subprocess.CompletedProcess(argv, int(failure == "query"),
            "private-data" if failure == "pid" else str(os.getpid()), "private-error")

    def owner(path, *a, **kw):
        if path == process:
            if failure == "owner_unreadable": raise PermissionError("private-error")
            if failure == "owner": return SimpleNamespace(st_uid=os.geteuid() + 1)
        return stat(path, *a, **kw)

    def directory(path, *a, **kw):
        if path == process / "cwd":
            if fallback: raise PermissionError("private-error")
            if failure == "gone": raise FileNotFoundError("private-error")
            return tmp_path / "different" if failure == "directory" else tmp_path
        return resolve(path, *a, **kw)

    def arguments(path):
        if path != process / "cmdline": return read(path)
        argument_failure = str(failure).removeprefix("unit_")
        if argument_failure == "arguments": raise PermissionError("private-error")
        pool = root.parent / "different" if argument_failure == "endpoint" else root
        argv = [sys.executable, "-B", "-m", "unrelated" if argument_failure == "command" else "hypoloom.cli",
                "gpu-pool-serve", "--pool-root", str(pool), "--socket-path", str(pool / "control.sock"),
                "--member", f"{os.geteuid()}={GPU}", "--reserve-mib", "4096"]
        return ("\0".join(argv) + "\0").encode()

    monkeypatch.setattr(manager, "_quiet_run", command)
    monkeypatch.setattr(Path, "resolve", directory)
    monkeypatch.setattr(Path, "stat", owner)
    monkeypatch.setattr(Path, "read_bytes", arguments)
    diagnostics = []
    result = manager._configuration(plan, tools, {}, diagnostics)
    assert diagnostics == ([detail] if detail else [])
    if detail:
        assert result is None
    else:
        assert result.reserve_mib == 4096 and result.members == plan.members
    assert "private" not in json.dumps(diagnostics)
    assert all("Environment" not in " ".join(q) for q in queries)
    assert any("--property=WorkingDirectory" in q for q in queries) == fallback


def test_configuration_failure_is_not_reported_as_an_active_dependency(managed, monkeypatch):
    args, state, root = managed
    manager.ensure(args)
    args.reviewed_action = "reconfigure"
    args.reserve_mib = 512

    def unreadable(plan, tools, environment, diagnostics):
        diagnostics.append("unit_working_directory_query_failed")
        return None

    monkeypatch.setattr(manager, "_configuration", unreadable)
    review = manager.review(args)
    assert review["reason"] == "pool_configuration_unknown"
    assert review["detail"] == "unit_working_directory_query_failed"
    assert not review["prepare_available"] and "dependencies" not in review
    assert state["events"] == ["launch"]
    with pytest.raises(manager.ManagedPoolError): manager.ensure(args)
    assert state["events"] == ["launch"]
