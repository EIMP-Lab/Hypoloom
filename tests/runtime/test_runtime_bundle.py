import fcntl
from pathlib import Path
import subprocess
import sys
import os
import shutil
import time

import pytest

from hypoloom.host_settings import HostConfigurationError
from hypoloom.runtime_bundle import prepare_runtime_bundle
from hypoloom.workflow_host import installed_run_version


def test_new_bundle_keeps_existing_runtime_identity_and_is_reused(tmp_path):
    root = tmp_path / "new"
    bundle = prepare_runtime_bundle(root)
    assert bundle == root / "bundle"
    assert bundle.stat().st_mode & 0o777 == 0o700
    source = Path(__file__).resolve().parents[2]
    for path in (source / "hypoloom").glob("*.py"):
        assert (bundle / "hypoloom" / path.name).read_bytes() == path.read_bytes()
    code = "import sys;sys.path.insert(0,sys.argv[1]);from hypoloom.workflow_host import installed_run_version;print(installed_run_version().runtime_environment_id)"
    run = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(bundle)],
        cwd=tmp_path, capture_output=True, text=True, timeout=20, check=True)
    assert run.stdout.strip() == installed_run_version().runtime_environment_id
    pinned = bundle / "hypoloom/cli.py"
    pinned.write_text("preserved exact old package\n")
    assert prepare_runtime_bundle(root) == bundle
    assert pinned.read_text() == "preserved exact old package\n"


def test_retained_root_is_not_backfilled_and_owner_lock_is_respected(tmp_path):
    root = tmp_path / "old"
    root.mkdir()
    (root / "state.sqlite").write_bytes(b"retained source")
    before = sorted(root.iterdir())
    assert prepare_runtime_bundle(root) is None
    assert sorted(root.iterdir()) == before
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    with (fresh / "controller.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(HostConfigurationError, match="another Host"):
            prepare_runtime_bundle(fresh)
        assert not (fresh / "bundle").exists()


def test_external_host_restart_uses_preserved_source_after_checkout_changes(tmp_path):
    from hypoloom.control_protocol import ControlRequest, request_over_socket
    from hypoloom.oracle import Oracle
    from hypoloom.profile_admission import admit_profile

    source = Path(__file__).resolve().parents[2]
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    shutil.copytree(source / "hypoloom", checkout / "hypoloom",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copyfile(source / "environment.yml", checkout / "environment.yml")
    root = tmp_path / "state"
    run_id = "bundle-restart"
    admitted = admit_profile(source / "tests/fixtures/profiles/component-demo", run_id=run_id, delay_seconds=0.0)
    assert admitted.admitted, admitted.to_dict()
    pinned_source = None
    for generation in range(2):
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "PYTHONSAFEPATH": "1",
            "PYTHONPATH": str(checkout) if generation == 0 else os.pathsep.join((str(root / "bundle"), str(checkout)))}
        process = subprocess.Popen([sys.executable, "-B", "-m", "hypoloom.cli", "serve", "--state-root", str(root)],
            cwd=checkout, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 15
            while not (root / "control.sock").is_socket():
                assert process.poll() is None, process.communicate()
                assert time.monotonic() < deadline, "Host readiness timeout"
                time.sleep(.02)
            if generation == 0:
                created = request_over_socket(root / "control.sock", ControlRequest(1, "create-bundle", 0, "CREATE_RUN", admitted.create_run_payload))
                assert created.status == "accepted", created
            while True:
                status = request_over_socket(root / "control.sock", ControlRequest(1, f"status-{generation}", None, "STATUS", {"run_id": run_id}))
                assert status.status == "accepted", status
                if status.result["run_state"] == "COMPLETED":
                    break
                assert time.monotonic() < deadline, status
                time.sleep(.02)
            bundle_source = (root / "bundle/hypoloom/policy.py").read_bytes()
            if pinned_source is None:
                pinned_source = bundle_source
            assert bundle_source == pinned_source
        finally:
            if process.poll() is None:
                process.terminate()
            stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, (stdout, stderr)
        assert not (root / "control.sock").exists()
        if generation == 0:
            with (checkout / "hypoloom/policy.py").open("a") as stream:
                stream.write("\n# Simulated subsequent development checkout change.\n")
            assert (checkout / "hypoloom/policy.py").read_bytes() != pinned_source
    report = Oracle(root / "state.sqlite", root / "artifacts").audit(run_id)
    assert report.ok, report.to_dict()
