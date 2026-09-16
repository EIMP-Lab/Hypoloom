from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hypoloom.profile_admission import PROFILE_FILES


_REPOSITORY = Path(__file__).resolve().parents[2]
COMPONENT_BATCH_TEMPLATE = (
    _REPOSITORY / "tests" / "fixtures" / "profiles" / "component-batch"
)


def _run_git(target: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


def materialize_component_batch_profile(root: Path) -> Path:
    """Bind the tracked placeholder template only to disposable test paths."""

    target = root / "target-repository"
    target.mkdir(parents=True)
    (target / "README.md").write_text("test target\n", encoding="utf-8")
    _run_git(target, "init", "--quiet")
    _run_git(target, "add", "README.md")
    _run_git(
        target,
        "-c",
        "user.name=Hypoloom Tests",
        "-c",
        "user.email=tests@invalid.example",
        "commit",
        "--quiet",
        "-m",
        "fixture",
    )

    runtime = root / "runtime-prefix"
    (runtime / "bin").mkdir(parents=True)
    python = runtime / "bin" / "python"
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o755)

    alpha = root / "dataset-alpha-read-root"
    beta = root / "dataset-beta-read-root"
    alpha.mkdir()
    beta.mkdir()

    replacements = {
        "/REPLACE_ME/target-repository": str(target.resolve()),
        "/REPLACE_ME/runtime-prefix": str(runtime.resolve()),
        "/REPLACE_ME/dataset-alpha-read-root": str(alpha.resolve()),
        "/REPLACE_ME/dataset-beta-read-root": str(beta.resolve()),
    }
    profile = root / "component-batch-profile"
    profile.mkdir()
    for name in PROFILE_FILES:
        text = (COMPONENT_BATCH_TEMPLATE / name).read_text(encoding="utf-8")
        for placeholder, value in replacements.items():
            text = text.replace(placeholder, value)
        assert not any(placeholder in text for placeholder in replacements)
        (profile / name).write_text(text, encoding="utf-8")
    return profile


@pytest.fixture(scope="module")
def component_batch_profile(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return materialize_component_batch_profile(
        tmp_path_factory.mktemp("component-batch")
    )
