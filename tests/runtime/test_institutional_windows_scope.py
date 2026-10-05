"""Run the shipped PowerShell lifecycle with deterministic desktop/SSH edges."""
from pathlib import Path
import os
import shutil
import subprocess
import sys

import pytest


@pytest.mark.parametrize("scenario", [
    "terminal", "outage", "recover", "sleep", "wrong_scope", "pending",
    "handoff", "deadline_wait", "owned_cleanup", "task_ownership", "legacy_task",
    "wrong_entry", "handshake_scope", "handshake_failure", "empty_active",
    "empty_preparing", "awaiting_launch", "retry_preparation", "fast_retry", "review_reconnect", "interrupt", "native_compile",
])
def test_windows_run_scoped_helper(scenario):
    root = Path(__file__).resolve().parents[2]
    executable = os.environ.get("HYPOLOOM_TEST_PWSH") or shutil.which("pwsh")
    if not executable:
        pytest.skip("PowerShell runtime not installed")
    result = subprocess.run([
        executable, "-NoProfile", "-File",
        str(root / "tests/runtime/fixtures/institutional_scope_checks.ps1"),
        "-Connector", str(root / "hypoloom/connection_assets/institutional-connect.ps1"),
        "-Scenario", scenario,
    ], capture_output=True, text=True, timeout=35)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object kernel required")
@pytest.mark.parametrize("forced", [False, True])
def test_windows_job_reaps_owned_child_on_normal_or_forced_helper_exit(forced):
    import ctypes

    root = Path(__file__).resolve().parents[2]
    executable = os.environ.get("HYPOLOOM_TEST_PWSH") or shutil.which("pwsh")
    if not executable:
        pytest.skip("PowerShell runtime not installed")
    process = subprocess.Popen([
        executable, "-NoProfile", "-File",
        str(root / "tests/runtime/fixtures/institutional_scope_checks.ps1"),
        "-Connector", str(root / "hypoloom/connection_assets/institutional-connect.ps1"),
        "-Scenario", "native_job",
    ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    owned = None
    try:
        child_id = int(process.stdout.readline().strip())
        owned = kernel.OpenProcess(0x100001, False, child_id)  # synchronize + terminate only this owned child
        assert owned
        if forced:
            process.kill()
        else:
            process.stdin.write("finish\n")
            process.stdin.flush()
        process.wait(timeout=10)
        assert kernel.WaitForSingleObject(owned, 10000) == 0, "Job retained its owned child"
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        if owned:
            if kernel.WaitForSingleObject(owned, 0) != 0:
                kernel.TerminateProcess(owned, 1)
            kernel.CloseHandle(owned)
        for pipe in (process.stdin, process.stdout, process.stderr):
            pipe.close()
