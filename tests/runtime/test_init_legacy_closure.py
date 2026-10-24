"""Same-init authorization and original-bundle offline closure, without providers."""
from contextlib import ExitStack
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from hypoloom import bootstrap, bootstrap_inspect, bootstrap_questions
from hypoloom import legacy_run_closure as closure, pool_run_recovery as recovery
from hypoloom.bootstrap_review import gpu_reserve_summary
from hypoloom.control_protocol import ControlRequest
from hypoloom.oracle import Oracle
from hypoloom.runtime_bundle import prepare_runtime_bundle
from hypoloom.runner import process_group_exists
from hypoloom.state_store import StateStore
from hypoloom.workflow_host import WorkflowHost
from test_stopped_final_report_host import batch_host as _batch_host, _drive

batch_host = _batch_host

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def stopped(batch_host, tmp_path, monkeypatch):
    payload = json.loads(batch_host.store.inspect(
        "SELECT request_json FROM workflow_commands WHERE command_id='create-batch'")[0]["request_json"])["payload"]
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 29)
    root = tmp_path / "legacy"
    prepare_runtime_bundle(root)
    host = WorkflowHost(root, local_agent=batch_host.agent)
    host.initialize()
    try:
        assert host.handle(ControlRequest(1, "create", 0, "CREATE_RUN", payload)).status == "accepted"
        monkeypatch.setattr(host, "_finish_fake_agent", lambda *a, **k: None)
        _drive(host, lambda: bool(host.store.list_attempts(state="OPEN")))
    finally:
        host.close()
    target = recovery.read_target(root, "batch-host")
    assert target["state"] == "ACTIVE" and target["launch"] is None
    return root, target


def dependencies(root, target):
    return {"bindings": [{"state_root": str(root), "run_id": target["run_id"], "state": target["state"]}],
            "blockers": [{"run_id": target["run_id"], "reason": reason}
                         for reason in ("run_depends_on_pool", "open_attempt")]}


def worker(root, target, *, module=None):
    return subprocess.run([sys.executable, "-I", "-B", str(module or Path(closure.__file__).resolve()), "execute",
        "--state-root", str(root), "--run-id", target["run_id"], "--expected-revision", str(target["revision"])],
        capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("language", ["en", "zh-CN"])
def test_offline_review_is_read_only_and_carries_distinct_authorization(stopped, language):
    root, target = stopped
    before = StateStore(root).latest_transition(target["run_id"])
    proposed = recovery.review(dependencies(root, target))
    assert proposed["runs"][0]["closure"] == "offline" and proposed["runs"][0]["available"]
    assert not proposed["prepare_after_cleanup"]
    args = bootstrap.build_parser().parse_args(["inspect", "--managed-pool", "--allow-gpu", "--ui-language", language])
    pool = {"action": "restore", "reserve_mib": 512, "members": {str(os.geteuid()): ["GPU-fixture"]},
            "dependencies": dependencies(root, target), "cancellation": proposed}
    result = {"missing": []}
    review = {"gpu": {"memory_budget": {"reserve_floor_mib": 256}, "pool": pool}}
    bootstrap_inspect._pool_choices(args, result, review)
    assert result["missing"] == ["pool_retire_run"]
    question = bootstrap_questions.questions(args, result)[0][1]
    assert f"{target['run_id']}:{target['revision']}" in question["question"] and "FAILED" in question["question"]
    bootstrap_questions.apply_answers(args, result, {question["question"]: question["options"][0]["label"]})
    assert args.pool_retire_run == ["none"] and args.pool_cancel_run is None
    deferred = {"missing": []}
    bootstrap_inspect._pool_choices(args, deferred, review)
    assert not deferred["missing"]
    args.pool_retire_run = None
    bootstrap_questions.apply_answers(args, result, {question["question"]: question["options"][1]["label"]})
    assert args.pool_retire_run == [f"{target['run_id']}:{target['revision']}"] and args.pool_cancel_run is None
    args.profile, args.run_id = "next", "next"
    argv = bootstrap_inspect._launch_argv(args, root.parent)
    assert argv[argv.index("--pool-retire-run") + 1] == args.pool_retire_run[0]
    selected = recovery.review(dependencies(root, target), retire_values=args.pool_retire_run)
    assert selected["prepare_after_cleanup"]
    pool["cancellation"] = selected
    text = "\n".join(gpu_reserve_summary({"memory_budget": {"initial_workload_mib": 1024,
        "initial_reserve_mib": 256, "reserve_percent": 0, "reserve_floor_mib": 256}, "pool": pool}, language))
    assert "FAILED" in text and target["run_id"] in text
    ordinary = recovery.review(dependencies(root, target), args.pool_retire_run)
    assert ordinary["runs"][0]["reason"] == "explicit_legacy_closure_required"
    assert not ordinary["prepare_after_cleanup"]
    assert StateStore(root).latest_transition(target["run_id"]) == before


def test_original_bundle_closes_lost_attempt_and_truthfully_fails_unavailable_report(stopped):
    root, target = stopped
    store = StateStore(root)
    version = store.get_run(target["run_id"])
    source = {str(p.relative_to(root / "bundle")): p.read_bytes() for p in (root / "bundle").rglob("*") if p.is_file()}
    attempts = store.list_attempts()
    result = worker(root, target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["oracle_ok"]
    assert store.latest_transition(target["run_id"])["state"] == "FAILED"
    closed = store.list_attempts()
    assert len(closed) == len(attempts) + 1
    assert all(a["state"] == "CLOSED" and not (a["process_group_id"] and process_group_exists(a["process_group_id"])) for a in closed)
    prior = {a["attempt_id"] for a in attempts}
    assert all(a["terminal_reason"] == "LOST" for a in closed if a["attempt_id"] in prior)
    final = [a for a in closed if a["attempt_id"] not in prior]
    assert final[0]["terminal_reason"] == "FAILED"
    assert json.loads(final[0]["observation_json"])["request"]["command"] == "FINAL_REPORT"
    transitions = store.inspect("SELECT c.command_type FROM workflow_transitions t JOIN workflow_commands c "
        "ON c.command_id=t.command_id WHERE t.workflow_id=? ORDER BY t.revision", (target["run_id"],))
    assert sum(t["command_type"] == "FINAL_REPORT_FAILED" for t in transitions) == 1
    assert sum(t["command_type"] == "FINISH_STOP" for t in transitions) == 1
    audit = Oracle(root / "state.sqlite", root / "artifacts").audit(target["run_id"])
    assert audit.ok and not audit.open_attempt_count and not audit.pending_effect_count, audit.to_dict()
    assert store.get_run(target["run_id"]) == version
    assert source == {str(p.relative_to(root / "bundle")): p.read_bytes() for p in (root / "bundle").rglob("*") if p.is_file()}
    head = store.latest_transition(target["run_id"])
    repeated = worker(root, target)
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert store.latest_transition(target["run_id"]) == head and store.list_attempts() == closed


def test_closure_controller_in_tmp_remains_readable_inside_private_tmp(stopped, tmp_path):
    root, target = stopped
    controller = tmp_path / "checkout/hypoloom"
    shutil.copytree(Path(closure.__file__).resolve().parent, controller,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    result = worker(root, target, module=controller / "legacy_run_closure.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["oracle_ok"]
    assert StateStore(root).latest_transition(target["run_id"])["state"] == "FAILED"


@pytest.mark.parametrize("condition", ["revision", "live_host", "schema", "version"])
def test_offline_closure_refuses_changed_or_owned_state_without_writing(stopped, condition):
    root, target = stopped
    with ExitStack() as stack:
        if condition == "revision": target["revision"] += 1
        if condition == "live_host":
            lock = stack.enter_context((root / "controller.lock").open())
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with sqlite3.connect(root / "state.sqlite") as db:
            if condition == "schema": db.execute("PRAGMA user_version=99999")
        if condition == "version":
            module = root / "bundle/hypoloom/lifecycle.py"
            module.write_text(module.read_text().replace('"hypoloom-lifecycle-v11"', '"different"'))
        before = (root / "state.sqlite").read_bytes()
        proposal = recovery.review(dependencies(root, target), retire_values=[f"{target['run_id']}:{target['revision']}"])
        assert not proposal["prepare_after_cleanup"]
        result = worker(root, target)
        assert result.returncode != 0, result.stdout
        assert (root / "state.sqlite").read_bytes() == before


def test_pending_offline_request_keeps_mode_and_never_starts_duplicate(stopped, monkeypatch):
    root, target = stopped
    host = WorkflowHost(root)
    try:
        host.store.initialize()
        assert host._cancel_run(closure.retirement_request(target["run_id"], target["revision"])).status == "accepted"
    finally:
        host.store.close()
        host._projection_executor.shutdown(wait=True)
    with (root / "controller.lock").open() as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        review = recovery.review(dependencies(root, target), retire_values=[f"{target['run_id']}:{target['revision']}"])
        plan = review["runs"][0]
        assert review["prepare_after_cleanup"] and plan["status"] == "cancellation_pending" and plan["closure"] == "offline"
        monkeypatch.setattr(recovery.subprocess, "run", lambda *a, **k: pytest.fail("duplicate worker"))
        assert recovery.request_cleanup(ROOT, plan)["status"] == "cancellation_pending"


@pytest.mark.parametrize("changed", [False, True])
def test_supervised_offline_worker_uses_no_provider_launch_inputs(stopped, monkeypatch, changed):
    root, target = stopped
    name = "test_legacy_managed_host"
    spec = importlib.util.spec_from_file_location(name, ROOT / ".claude/skills/autoresearch/scripts/manage_host.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        calls = []
        monkeypatch.setattr(module, "_find_tools", lambda: module.Tools("escape", "run", "ctl"))
        monkeypatch.setattr(module, "_derive_unit", lambda c, *a: c)
        monkeypatch.setattr(module, "_unit_is_absent", lambda *a: not calls)
        monkeypatch.setattr(module, "_launch_service", lambda c, t, d, a, e: calls.append((a, d, e)))
        read = recovery.read_target
        def accepted(*args, **kwargs):
            value = read(*args, **kwargs)
            if calls: value["cancel_accepted"] = True
            return value
        monkeypatch.setattr(recovery, "read_target", accepted)
        args = module.build_parser().parse_args(["retire", "--state-root", str(root), "--run-id", target["run_id"],
            "--expected-revision", str(target["revision"] + int(changed))])
        before = StateStore(root).latest_transition(target["run_id"])
        if changed:
            with pytest.raises(module.ManagedHostError, match="legacy_closure_unavailable"):
                module.retire(args)
            assert not calls
        else:
            assert module.retire(args).state_root == root
            assert len(calls) == 1
            argv, directory, environment = calls[0]
            assert argv[:3] == [sys.executable, "-I", "-B"] and argv[4] == "execute"
            assert Path(argv[3]) == Path(closure.__file__).resolve()
            assert directory == ROOT and "ANTHROPIC_API_KEY" not in environment
            assert not any("claude" in a for a in argv)
        assert StateStore(root).latest_transition(target["run_id"]) == before
    finally:
        sys.modules.pop(name, None)


@pytest.mark.parametrize("outcome", ["ready", "uncertain", "changed", "profile_changed", "deadline"])
def test_same_start_waits_for_closure_then_creates_exactly_once(monkeypatch, outcome):
    names = ("profile_check", "pool_prepare", "pool_status", "host_start", "start", "status", "host_stop")
    commands = {name: [name] for name in names}
    commands.update(environment={}, working_directory=str(ROOT), pool_action_reviewed=True, pool_prepare_available=True)
    calls, clock, payload = [], [0.0], {"run_id": "reviewed-next"}
    def invoke(argv, *a, **k):
        name = argv[0]; calls.append(name)
        if name == "profile_check":
            return 0, {"admitted": True, "create_run_payload": {} if outcome == "profile_changed" and len(calls) > 1 else payload}
        if name == "pool_prepare":
            if outcome == "uncertain": return 2, {"status": "dependency_cleanup_uncertain"}
            if outcome == "changed": return 2, {"status": "cancellation_review_changed"}
            if calls.count(name) <= 2 or outcome == "deadline": return 2, {"status": "dependency_cleanup_pending"}
            return 0, {"status": "restored"}
        if name == "host_start": return 0, {"status": "ready"}
        return 0, {"status": "accepted"}
    monkeypatch.setattr(bootstrap, "_invoke", invoke)
    monkeypatch.setattr(bootstrap.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _: clock.__setitem__(0, clock[0] + 150))
    result = bootstrap.start_fresh(commands, payload)
    expected = {"ready": "started", "uncertain": "pool_prepare_failed", "changed": "pool_prepare_failed",
                "profile_changed": "profile_changed_before_host", "deadline": "dependency_cleanup_pending"}
    assert result["status"] == expected[outcome]
    assert calls.count("host_start") == calls.count("start") == (1 if outcome == "ready" else 0)
    assert calls.count("pool_prepare") == (3 if outcome in {"ready", "deadline"} else 1)
    assert calls.count("pool_status") == (1 if outcome == "ready" else 0)
