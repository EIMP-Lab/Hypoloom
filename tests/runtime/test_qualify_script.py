"""Qualification scheduling preserves explicit pytest choices and live opt-ins."""
import importlib.util
from types import SimpleNamespace

import pytest

from scripts import qualify


@pytest.fixture
def calls(monkeypatch):
    for key in tuple(qualify.os.environ):
        if key == "PYTEST_ADDOPTS" or key.startswith("HYPOLOOM_REAL_"):
            monkeypatch.delenv(key)
    result = []

    def run(command, **kwargs):
        result.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(qualify.subprocess, "run", run)
    original = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object() if name == "xdist" else original(name))
    return result


@pytest.mark.parametrize("cpus,workers", [(None, 1), (1, 1), (3, 3), (256, 8)])
def test_g1_defaults_to_bounded_worksteal_after_static_checks(monkeypatch, calls, cpus, workers):
    monkeypatch.setattr(qualify.os, "cpu_count", lambda: cpus)
    assert qualify.main(["g1"]) == 0
    assert len(calls) == 4
    assert calls[0][0][1:4] == ["-m", "compileall", "-q"]
    assert calls[1][0][1:4] == ["-m", "ruff", "check"]
    assert calls[2][0] == ["git", "diff", "--check"]
    command, options = calls[-1]
    assert command == [qualify.sys.executable, "-B", "-m", "pytest", "-q", "-ra", "-n", str(workers), "--dist=worksteal"]
    assert options["cwd"] == qualify.ROOT
    assert options["env"]["PYTHONDONTWRITEBYTECODE"] == "1"


def test_g1_without_xdist_runs_serially(monkeypatch, calls):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert qualify.main(["g1"]) == 0
    assert calls[-1][0] == [qualify.sys.executable, "-B", "-m", "pytest", "-q", "-ra"]


@pytest.mark.parametrize("addopts", ["-n 2 --dist=loadscope", "-n 0", "--maxfail=1"])
def test_explicit_pytest_environment_is_preserved_without_injected_defaults(monkeypatch, calls, addopts):
    monkeypatch.setenv("PYTEST_ADDOPTS", addopts)
    assert qualify.main(["g1"]) == 0
    assert calls[-1][0] == [qualify.sys.executable, "-B", "-m", "pytest", "-q", "-ra"]
    assert calls[-1][1]["env"]["PYTEST_ADDOPTS"] == addopts


def test_explicit_pytest_arguments_override_environment_as_in_pytest(monkeypatch, calls, tmp_path):
    monkeypatch.setenv("PYTEST_ADDOPTS", "-n 2 --dist=loadscope")
    report = tmp_path / "g1.xml"
    extra = ["-n", "4", "--dist=worksteal", "--basetemp=/tmp/explicit-qualification"]
    assert qualify.main(["g1", "--junitxml", str(report), "--", *extra]) == 0
    assert calls[-1][0] == [qualify.sys.executable, "-B", "-m", "pytest", "-q", "-ra", "--junitxml", str(report), *extra]
    assert calls[-1][1]["env"]["PYTEST_ADDOPTS"] == "-n 2 --dist=loadscope"


def test_explicit_serial_cli_is_not_overridden(calls):
    assert qualify.main(["g1", "--", "-n", "0"]) == 0
    assert calls[-1][0][-2:] == ["-n", "0"]
    assert "--dist=worksteal" not in calls[-1][0]


@pytest.mark.parametrize("extra", [["-n", "4"], ["--dist=worksteal"], ["--junitxm", "misspelled.xml"]])
def test_script_options_before_separator_are_not_silently_forwarded(calls, extra):
    with pytest.raises(SystemExit) as error:
        qualify.main(["g1", *extra])
    assert error.value.code == 2 and not calls


@pytest.mark.parametrize("argv", [["g1", "--collect-only"], ["g2"], ["g3"]])
def test_collection_and_other_stages_do_not_gain_parallel_defaults(calls, argv):
    assert qualify.main(argv) == 0
    assert len(calls) == 1
    command = calls[-1][0]
    assert "-n" not in command and "--dist=worksteal" not in command
    if "--collect-only" in argv: assert command[-1] == "--collect-only"
    else: assert command[6:] == qualify.SELECTIONS[argv[0]]


@pytest.mark.parametrize("stage", ["g1", "g2", "g3"])
@pytest.mark.parametrize("flag", ["HYPOLOOM_REAL_CLAUDE", "HYPOLOOM_REAL_GPU"])
def test_live_optins_still_stop_offline_execution_before_any_effect(monkeypatch, calls, stage, flag):
    monkeypatch.setenv(flag, "1")
    with pytest.raises(SystemExit) as error:
        qualify.main([stage])
    assert error.value.code == 2
    assert not calls


def test_static_failure_does_not_start_pytest(monkeypatch, calls):
    def fail(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=7)
    monkeypatch.setattr(qualify.subprocess, "run", fail)
    assert qualify.main(["g1"]) == 7
    assert len(calls) == 1 and "compileall" in calls[0][0]
