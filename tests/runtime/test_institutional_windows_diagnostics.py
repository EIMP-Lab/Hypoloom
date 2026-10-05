"""The shipped helper keeps bounded safe diagnostics without network authority."""
from pathlib import Path
import os
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("scenario", [
    "bounded_log", "private_logs", "safe_stderr", "status_offline", "status_missing",
    "status_denied", "status_cim", "corrupt_log", "cleanup_all", "cleanup_primary", "stderr_drain",
    "cleanup_discovery", "cleanup_ready", "cleanup_handshake",
])
def test_windows_client_diagnostics(tmp_path, scenario):
    root = Path(__file__).resolve().parents[2]
    executable = os.environ.get("HYPOLOOM_TEST_PWSH") or shutil.which("pwsh")
    if not executable:
        pytest.skip("PowerShell runtime not installed")
    result = subprocess.run([
        executable, "-NoProfile", "-File",
        str(root / "tests/runtime/fixtures/institutional_diagnostic_checks.ps1"),
        "-Connector", str(root / "hypoloom/connection_assets/institutional-connect.ps1"),
        "-Directory", str(tmp_path), "-Scenario", scenario,
    ], capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
