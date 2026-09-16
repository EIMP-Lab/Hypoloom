"""New-root executable preservation using the existing lock and run version."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import shutil
import sys
import tempfile

from .host_settings import HostConfigurationError


def prepare_runtime_bundle(state_root: Path) -> Path | None:
    source = Path(__file__).resolve().parent.parent
    bundle = state_root / "bundle"
    if bundle.is_symlink():
        raise HostConfigurationError("runtime bundle must be a private directory")
    if bundle.exists():
        if not (bundle / "hypoloom/cli.py").is_file():
            raise HostConfigurationError("runtime bundle is incomplete")
        return bundle
    if (state_root / "state.sqlite").exists():
        return None  # A retained run never acquires a fabricated source bundle.
    state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state_root / "controller.lock").open("a+") as lock:
        os.chmod(lock.name, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HostConfigurationError("another Host owns this runtime root") from exc
        if bundle.exists():
            return bundle
        if (state_root / "state.sqlite").exists():
            return None
        from .workflow_host import installed_run_version
        before = installed_run_version().runtime_environment_id
        staging = Path(tempfile.mkdtemp(prefix=".bundle-", dir=state_root))
        try:
            package = source / "hypoloom"
            if any(path.is_symlink() for path in package.rglob("*")):
                raise HostConfigurationError("runtime package contains a symlink")
            shutil.copytree(package, staging / "hypoloom",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            environment = source / "environment.yml"
            if environment.is_file():
                shutil.copyfile(environment, staging / environment.name)
            for copied in staging.rglob("*"):
                if copied.is_file() and copied.read_bytes() != (source / copied.relative_to(staging)).read_bytes():
                    raise HostConfigurationError("runtime source changed while copying")
            if before != installed_run_version().runtime_environment_id:
                raise HostConfigurationError("runtime source changed while copying")
            staging.rename(bundle)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return bundle


def enter_runtime_bundle(state_root: Path, arguments: list[str]) -> None:
    bundle = prepare_runtime_bundle(state_root)
    if bundle is None or Path(__file__).resolve().parent.parent == bundle.resolve():
        return
    # A new interpreter removes already-imported working-tree modules. -I also
    # removes cwd/user PYTHONPATH from imports, while retaining this environment.
    code = "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module('hypoloom.cli',run_name='__main__')"
    os.execv(sys.executable, [sys.executable, "-I", "-B", "-c", code, str(bundle), *arguments])
