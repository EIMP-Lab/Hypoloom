"""Portable regressions for preparation, retry scope and chart diagnostics."""
import json
import shutil
import subprocess
import time
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

from hypoloom.diagnostics import recovery_summary
from hypoloom.execution_workspace import GitTargetBinding
from hypoloom.git_authoring_workspace import GitAuthoringWorkspace
from hypoloom.notification_plot import BriefingPlotError, chart_capability
from hypoloom.observer_trends import TrendCaptureCache
from hypoloom.workflow_host import WorkflowHost


def git(root, *args, data=None):
    return subprocess.run(["git", "-C", str(root), *args], input=data,
        capture_output=True, check=True, timeout=10).stdout


def test_workspace_fetches_only_pinned_history_and_preserves_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q")
    (source / "code.py").write_text("print('base')\n")
    git(source, "add", ".")
    git(source, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-qm", "base")
    base = git(source, "rev-parse", "HEAD").decode().strip()
    # Both unreachable content and an unrelated branch would be copied by a
    # normal local clone. The pinned source needs neither.
    dangling = git(source, "hash-object", "-w", "--stdin", data=b"unreachable\n" * 100000).strip()
    git(source, "checkout", "--orphan", "unrelated")
    (source / "other.txt").write_text("unrelated branch\n")
    git(source, "add", ".")
    git(source, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-qm", "other")
    unrelated = git(source, "rev-parse", "HEAD").strip()
    (source / "code.py").write_text("uncommitted user edit\n")
    before = (git(source, "status", "--porcelain"), git(source, "worktree", "list", "--porcelain"))
    state = tmp_path / "state"
    state.mkdir()
    prepared = GitAuthoringWorkspace(state).prepare(GitTargetBinding(checkout=source, commit=base), "a")
    assert git(prepared.worktree_root, "rev-parse", "HEAD").decode().strip() == base
    assert git(prepared.repository_root, "rev-parse", "HEAD").decode().strip() == base
    assert (prepared.worktree_root / "code.py").read_text() == "print('base')\n"
    for absent in (dangling, unrelated):
        result = subprocess.run(["git", "-C", str(prepared.repository_root), "cat-file", "-e", absent.decode()], capture_output=True)
        assert result.returncode != 0
    assert not (prepared.repository_root / "objects/info/alternates").exists()
    assert git(prepared.repository_root, "remote") == b""
    assert before == (git(source, "status", "--porcelain"), git(source, "worktree", "list", "--porcelain"))
    # Removing the source still leaves a self-contained, usable private copy.
    shutil.rmtree(source)
    assert git(prepared.repository_root, "cat-file", "-t", base) == b"commit\n"
    prepared.cleanup()
    assert not prepared.attempt_root.exists()


def capture(root, number, **fields):
    path = root / "diagnostics/agents/run/attempt/provider" / f"{number:06d}" / "exchange.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"upstream_started_at": 1000 + number, "ended_at": 1100, **fields}))
    return path


def scan(cache, root, *, limit=100, closed=True):
    row = {"attempt_id": "attempt", "process_group_id": 123, "state": "CLOSED" if closed else "OPEN",
           "ended_at": 1100 if closed else None}
    budget = {"deadline": time.monotonic() + 60, "limit": limit, "inspected": 0, "cached": 0}
    times, reasons = cache.scan(root, "run", row, budget)
    return times, reasons, budget


def test_bounded_history_reads_continue_and_reuse_completed_capture(tmp_path):
    for i in range(1, 6): capture(tmp_path, i)
    cache = TrendCaptureCache()
    counts = []
    for _ in range(6):
        times, reasons, budget = scan(cache, tmp_path, limit=1)
        counts.append(len(times))
        assert "capture_unavailable" not in reasons
        assert budget["inspected"] <= 1
    assert counts[:5] == [1, 2, 3, 4, 5]
    assert reasons == set()
    times, reasons, budget = scan(cache, tmp_path, limit=0)
    assert sorted(times) == [1001, 1002, 1003, 1004, 1005] and not reasons
    assert budget["inspected"] == 0 and budget["cached"] == 5


def test_active_capture_updates_appends_and_closes_without_double_counting(tmp_path):
    path = capture(tmp_path, 1, upstream_started_at=None, ended_at=None)
    cache = TrendCaptureCache()
    assert scan(cache, tmp_path, closed=False)[1] == {"upstream_time_unavailable"}
    path.write_text(json.dumps({"upstream_started_at": 1002, "ended_at": None}))
    assert scan(cache, tmp_path, closed=False)[0] == [1002]
    capture(tmp_path, 2, upstream_started_at=1003)
    assert sorted(scan(cache, tmp_path, closed=False)[0]) == [1002, 1003]
    path.write_text(json.dumps({"upstream_started_at": 1002, "ended_at": 1100}))
    assert sorted(scan(cache, tmp_path)[0]) == [1002, 1003]
    assert scan(cache, tmp_path, limit=0)[1] == set()


def test_missing_and_corrupt_capture_remain_unknown_and_can_recover(tmp_path):
    broken = capture(tmp_path, 1)
    broken.write_text("{")
    capture(tmp_path, 2)
    cache = TrendCaptureCache()
    first = scan(cache, tmp_path, limit=1)
    assert "metadata_unavailable" in first[1]
    second = scan(cache, tmp_path, limit=1)
    assert second[0] == [1002]  # corrupt first file cannot starve the later one
    capture(tmp_path, 1)
    assert sorted(scan(cache, tmp_path)[0]) == [1001, 1002]
    shutil.rmtree(broken.parent.parent)
    assert scan(cache, tmp_path)[:2] == ([], {"capture_unavailable"})


def test_completed_capture_revalidates_changes_after_bounded_cache_lifetime(tmp_path, monkeypatch):
    from hypoloom import observer_trends
    now = [100.0]
    monkeypatch.setattr(observer_trends.time, "monotonic", lambda: now[0])
    path = capture(tmp_path, 1)
    cache = TrendCaptureCache()
    assert scan(cache, tmp_path)[0] == [1001]
    path.write_text("{")
    now[0] += 61
    assert scan(cache, tmp_path)[:2] == ([], {"metadata_unavailable"})


@pytest.mark.parametrize("retry_class", ["MODEL", "INFRASTRUCTURE"])
def test_recovered_task_does_not_exhaust_itself_or_the_next_task(retry_class):
    def attempt(ordinal, reason):
        return {"attempt_id": f"a{ordinal}", "state": "CLOSED", "attempt_ordinal": ordinal,
            "terminal_reason": reason, "observation_json": json.dumps({"retry_class": retry_class})}
    tasks = {"first": [attempt(1, "FAILED"), attempt(2, "FAILED"), attempt(3, "SUCCEEDED")],
             "next": [attempt(1, "FAILED")], "failed": [attempt(i, "FAILED") for i in range(1, 4)]}
    host = SimpleNamespace(
        store=SimpleNamespace(inspect=lambda sql, args: tasks[args[0]]),
        resource_recovery=SimpleNamespace(blocked=lambda run: {}),
        _expert_task_final_failure=lambda *args: None,
        _research_contract=lambda run: SimpleNamespace(document={"budget": {"max_retries": 2, "max_infrastructure_retries": 2}}),
        _agent_attempts_exhausted=WorkflowHost._agent_attempts_exhausted)
    for method in ("_expert_attempts", "_expert_success", "_expert_task_exhausted"):
        setattr(host, method, MethodType(getattr(WorkflowHost, method), host))
    assert not host._expert_task_exhausted("run", SimpleNamespace(command_id="first"))
    assert not host._expert_task_exhausted("run", SimpleNamespace(command_id="next"))
    assert host._expert_task_exhausted("run", SimpleNamespace(command_id="failed"))
    assert len(tasks["first"]) == 3  # history remains available for accounting


def test_terminal_failure_messages_distinguish_policy_from_exhaustion():
    assert recovery_summary({"retry_class": None}) == "Failure is not automatically retryable"
    assert recovery_summary({"retry_class": "INFRASTRUCTURE"}) == "Retry limit reached for this task"
    assert recovery_summary({}) == "Automatic recovery ended for this node."


def test_plot_preflight_identifies_missing_dependencies_without_subprocess(monkeypatch):
    from hypoloom import notification_plot
    monkeypatch.setattr(notification_plot.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(notification_plot.subprocess, "run", lambda *args, **kwargs: pytest.fail("must fail before render"))
    assert chart_capability("zh-CN") == {"state": "unavailable", "reason": "plot_dependencies_missing"}


@pytest.mark.parametrize("error", ["plot_dependencies_missing", "plot_font_missing", "private path /secret"])
def test_child_plot_errors_expose_only_safe_reason_codes(monkeypatch, error):
    from hypoloom import notification_plot
    monkeypatch.setattr(notification_plot.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(notification_plot.subprocess, "run", lambda *a, **k:
        SimpleNamespace(returncode=1, stdout=json.dumps({"error": error}).encode()))
    expected = error if error.startswith("plot_") else "plot_render_failed"
    with pytest.raises(BriefingPlotError, match=expected): notification_plot._plot_process({})


def test_plot_timeout_has_a_specific_safe_reason(monkeypatch):
    from hypoloom import notification_plot
    monkeypatch.setattr(notification_plot.importlib.util, "find_spec", lambda name: object())
    def timeout(*args, **kwargs): raise subprocess.TimeoutExpired("private command", 15)
    monkeypatch.setattr(notification_plot.subprocess, "run", timeout)
    assert chart_capability()["reason"] == "plot_timeout"
