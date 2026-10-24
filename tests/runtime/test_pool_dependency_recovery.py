"""Reviewed cancellation uses original Host ownership, never init SQL repair."""
from contextlib import ExitStack
from dataclasses import replace
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

import pytest

from hypoloom import bootstrap, bootstrap_inspect, bootstrap_questions
from hypoloom import managed_gpu_pool as manager
from hypoloom import pool_run_recovery as recovery
from hypoloom.bootstrap_review import gpu_concurrency_summary, gpu_reserve_summary
from hypoloom.control_protocol import ControlRequest, request_over_socket
from hypoloom.gpu_pool import CapacitySample, GpuPool
from hypoloom.gpu_pool_client import PoolClient
from hypoloom.gpu_pool_server import PoolServer
from hypoloom.oracle import Oracle, audit_gpu_pool
from hypoloom.runner import process_group_exists
from hypoloom.workflow_host import WorkflowHost
from hypoloom.compute_resources import GpuRuntimeObservation
from test_managed_pool_lifecycle import managed as _managed, binding, GPU
from test_gpu_host_integration import _payload
from test_stopped_final_report_host import batch_host as _batch_host, _drive, _reports
from test_bootstrap import checkout as _checkout, arguments

managed, batch_host, checkout = _managed, _batch_host, _checkout

ROOT = Path(__file__).resolve().parents[2]


def recoverable(managed):
    args, state, pool_root = managed
    manager.ensure(args)
    state["active"] = False
    args.reviewed_action = "restore"
    root = binding(pool_root, run_state="ACTIVE", attempt_state="OPEN")
    (root / "bundle/hypoloom").mkdir(parents=True)
    (root / "bundle/hypoloom/pool_run_recovery.py").write_text("# fixture capability\n")
    launch = recovery.launch_binding(["serve", "--state-root", str(root), "--agent-adapter", "fake", "--exit-when-terminal"])
    with sqlite3.connect(root / "state.sqlite") as db:
        first = json.loads(db.execute("SELECT payload_json FROM workflow_transitions WHERE revision=1").fetchone()[0])
        first["host_launch"] = launch
        db.execute("UPDATE workflow_transitions SET payload_json=? WHERE revision=1", (json.dumps(first),))
        db.execute("CREATE TABLE workflow_commands(command_id TEXT,request_json TEXT,response_status TEXT)")
    return args, state, pool_root, root


def accept_cancel(root):
    request = recovery.cancel_request("known", 2)
    with sqlite3.connect(root / "state.sqlite") as db:
        db.execute("INSERT INTO workflow_commands VALUES(?,?,'accepted')", (request.request_id, json.dumps(request.to_mapping())))
        db.execute("INSERT INTO workflow_transitions VALUES('known',3,'RUN','STOPPING','known','{}')")


@pytest.mark.parametrize("language", ["en", "zh-CN"])
def test_inspect_question_carries_exact_cancellation_into_final_launch(managed, language):
    args, state, pool, root = recoverable(managed)
    before = (root / "state.sqlite").read_bytes()
    proposal = manager.review(args)
    assert not proposal["prepare_available"] and not proposal["cancellation"]["prepare_after_cleanup"]
    choices = bootstrap.build_parser().parse_args(["inspect", "--managed-pool", "--allow-gpu", "--ui-language", language])
    result = {"missing": [], "blockers": []}
    review = {"gpu": {"memory_budget": {"reserve_floor_mib": 256}, "pool": proposal}}
    bootstrap_inspect._pool_choices(choices, result, review)
    assert result["missing"] == ["pool_cancel_run"]
    question = bootstrap_questions.questions(choices, result)[0][1]
    assert "known:2" in question["question"]
    bootstrap_questions.apply_answers(choices, result, {question["question"]: question["options"][1]["label"]})
    assert choices.pool_cancel_run == ["known:2"] and choices.pool_action == "restore"
    args.pool_cancel_run = choices.pool_cancel_run
    reviewed = manager.review(args)
    assert not reviewed["prepare_available"] and reviewed["cancellation"]["prepare_after_cleanup"]
    choices.profile, choices.run_id = "new", "new"
    argv = bootstrap_inspect._launch_argv(choices, pool.parents[2])
    assert argv[argv.index("--pool-cancel-run") + 1] == "known:2"
    assert (root / "state.sqlite").read_bytes() == before
    assert state["events"] == ["launch"]
    text = "\n".join(gpu_reserve_summary({"memory_budget": {"initial_workload_mib": 1024,
        "initial_reserve_mib": 256, "reserve_percent": 0, "reserve_floor_mib": 256}, "pool": reviewed}, language))
    assert "known" in text and ("最终报告" if language == "zh-CN" else "final report") in text


@pytest.mark.parametrize("condition", ["legacy", "bundle", "revision", "live_host", "live_process", "unreleased", "unknown", "environment", "other_run"])
def test_unsafe_or_unsupported_recovery_remains_blocked(managed, condition):
    args, state, pool, root = recoverable(managed)
    args.pool_cancel_run = ["known:2"]
    with ExitStack() as stack:
        if condition == "bundle": (root / "bundle/hypoloom/pool_run_recovery.py").unlink()
        if condition == "live_host":
            handle = stack.enter_context((root / "controller.lock").open())
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if condition == "unknown": args.pool_cancel_run = ["another:2"]
        if condition == "live_process":
            process = stack.enter_context(subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True))
            stack.callback(process.terminate)
            with sqlite3.connect(root / "state.sqlite") as db:
                db.execute("UPDATE execution_attempts SET process_group_id=?", (process.pid,))
        with sqlite3.connect(root / "state.sqlite") as db:
            if condition in {"legacy", "environment"}:
                first = json.loads(db.execute("SELECT payload_json FROM workflow_transitions WHERE revision=1").fetchone()[0])
                if condition == "legacy": first.pop("host_launch")
                else: first["host_launch"]["arguments"][4] = "claude"
                db.execute("UPDATE workflow_transitions SET payload_json=? WHERE revision=1", (json.dumps(first),))
            if condition == "revision": db.execute("INSERT INTO workflow_transitions VALUES('known',3,'RUN','ACTIVE','known','{}')")
            if condition == "other_run": db.execute("INSERT INTO workflow_runs VALUES('second')")
        if condition == "unreleased":
            with sqlite3.connect(pool / "pool.sqlite3") as db:
                db.execute("INSERT INTO requests(uid,namespace,run_id,execution_id,attempt_id,state,intent_json) VALUES(?,'namespace','known','e','a','QUEUED','{}')", (os.geteuid(),))
        proposal = manager.review(args)
        assert not proposal["prepare_available"] and not proposal["cancellation"]["prepare_after_cleanup"]
        with pytest.raises(manager.ManagedPoolError, match="cancellation_review_changed"):
            manager.ensure(args)
    assert state["events"] == ["launch"]


def test_pending_cancellation_reuses_accepted_command_without_starting_another_host(managed, monkeypatch):
    args, state, pool, root = recoverable(managed)
    args.pool_cancel_run = ["known:2"]
    accept_cancel(root)
    with (root / "controller.lock").open() as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        proposal = manager.review(args)
        assert proposal["cancellation"]["runs"][0]["status"] == "cancellation_pending"
        monkeypatch.setattr(recovery.subprocess, "run", lambda *a, **k: pytest.fail("duplicate Host launch"))
        with pytest.raises(manager.ManagedPoolError, match="dependency_cleanup_pending") as error:
            manager.ensure(args)
        assert error.value.effects["run_cancellations"] == [{"run_id": "known", "status": "cancellation_pending"}]
    assert state["events"] == ["launch"]


def test_managed_pool_routes_explicit_offline_choice_and_rejects_conflicting_modes(managed, monkeypatch):
    from hypoloom import legacy_run_closure
    args, state, pool, root = recoverable(managed)
    with sqlite3.connect(root / "state.sqlite") as db:
        first = json.loads(db.execute("SELECT payload_json FROM workflow_transitions WHERE revision=1").fetchone()[0])
        first.pop("host_launch")
        db.execute("UPDATE workflow_transitions SET payload_json=? WHERE revision=1", (json.dumps(first),))
    monkeypatch.setattr(legacy_run_closure, "inspect_target", lambda *a, **k: {"status": "ready"})
    args.pool_retire_run = ["known:2"]
    assert manager.review(args)["cancellation"]["prepare_after_cleanup"]
    calls = []
    def invoke(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({"status": "cancellation_pending"}), "")
    monkeypatch.setattr(recovery.subprocess, "run", invoke)
    with pytest.raises(manager.ManagedPoolError, match="dependency_cleanup_pending"):
        manager.ensure(args)
    assert len(calls) == 1 and calls[0][3] == "retire"
    assert calls[0][calls[0].index("--run-id") + 1] == "known"
    assert calls[0][calls[0].index("--expected-revision") + 1] == "2"
    assert state["events"] == ["launch"]
    args.pool_cancel_run = ["known:2"]
    assert manager.review(args)["reason"] == "cancellation_selection_invalid"
    assert len(calls) == 1


@pytest.mark.parametrize("values", [["known"], ["known:0"], ["known:2", "known:3"], ["none", "known:2"], ["../run:2"]])
def test_cancellation_selection_is_exact(values):
    with pytest.raises(ValueError): recovery.selections(values)


def test_recovery_review_budget_exhaustion_cannot_authorize_cleanup(managed, monkeypatch):
    args, state, pool, root = recoverable(managed)
    dependencies = manager.review(args)["dependencies"]
    monkeypatch.setattr(recovery, "read_target", lambda *a, **k: pytest.fail("read after review deadline"))
    review = recovery.review(dependencies, ["known:2"], timeout=0)
    assert review["timed_out"] and not review["prepare_after_cleanup"]
    assert state["events"] == ["launch"]


def test_cancel_cli_does_not_prepare_a_missing_root(tmp_path):
    root = tmp_path / "absent"
    result = subprocess.run([sys.executable, "-B", "-m", "hypoloom.cli", "serve", "--state-root", str(root),
        "--exit-when-terminal", "--cancel-run-id", "missing", "--cancel-expected-revision", "1"],
        cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 2 and "requires an existing run" in result.stderr
    assert not root.exists()


def test_original_launch_does_not_record_secret_values(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-secret-value")
    for value in ["https://user:pass@example.invalid", "https://example.invalid?api_key=secret", "fixture-secret-value"]:
        assert recovery.launch_binding(["serve", "--institutional-browser-url", value]) is None
    binding = recovery.launch_binding(["serve", "--claude-settings", "/private/settings.json"])
    assert binding and "fixture-secret-value" not in json.dumps(binding)


@pytest.mark.parametrize("stale", [False, True])
def test_managed_host_recovery_uses_only_recorded_serve_arguments(managed, monkeypatch, stale):
    _args, _state, _pool, root = recoverable(managed)
    name = "test_pool_recovery_host_helper"
    spec = importlib.util.spec_from_file_location(name, ROOT / ".claude/skills/autoresearch/scripts/manage_host.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        calls = []
        monkeypatch.setattr(module, "_find_tools", lambda: module.Tools("escape", "run", "ctl"))
        monkeypatch.setattr(module, "_derive_unit", lambda c, *a: c)
        monkeypatch.setattr(module, "_unit_is_absent", lambda *a: True)
        monkeypatch.setattr(module, "_launch_service", lambda c, t, directory, argv, env: calls.append((argv, directory, env)))
        monkeypatch.setattr(module, "_wait_until_ready", lambda *a: None)
        args = module.build_parser().parse_args(["recover", "--state-root", str(root), "--run-id", "known",
            "--expected-revision", "3" if stale else "2"])
        if stale:
            with pytest.raises(module.ManagedHostError, match="cancellation_recovery_unavailable"):
                module.recover(args)
            assert not calls
        else:
            assert module.recover(args).state_root == root
            assert len(calls) == 1
            argv, directory, env = calls[0]
            assert argv[:4] == (sys.executable, "-m", "hypoloom.cli", "serve")
            assert argv[-4:] == ("--cancel-run-id", "known", "--cancel-expected-revision", "2")
            assert argv[argv.index("--state-root") + 1] == str(root)
            assert "ANTHROPIC_API_KEY" not in env
    finally:
        sys.modules.pop(name, None)


@pytest.mark.parametrize("selection", ["pool_cancel_run", "pool_retire_run"])
def test_bootstrap_carries_recovery_plan_and_never_starts_new_host_while_pending(checkout, capsys, monkeypatch, selection):
    from hypoloom.compute_resources import ComputeCapabilities, GpuCapability, LocalComputeResources
    repo, overrides = checkout
    assert bootstrap.main([*arguments(repo, [*overrides, "safety.gpus=auto", "methodology.budget.max_active_processes=1",
        "methodology.budget.min_vram_per_experiment=1024"]), "--write"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(bootstrap_inspect, "supervisor_check", lambda: {"status": "ready"})
    monkeypatch.setattr(bootstrap_inspect, "_gpu_inventory", lambda *a: {"status": "observed", "devices": []})
    monkeypatch.setattr(bootstrap_inspect, "_pools", lambda *a: {"state": "stopped"})
    monkeypatch.setattr(LocalComputeResources, "snapshot", lambda self: ComputeCapabilities(gpus=(
        GpuCapability(index=0, uuid=GPU, name="synthetic", total_memory_mib=24000, free_memory_mib=20000, driver_version="synthetic"),)))
    def proposal(args):
        assert getattr(args, selection) == ["old:2"]
        return {"status": "ok", "action": "restore", "prepare_available": False, "start_ready": False,
            "members": {str(os.geteuid()): [GPU]}, "reserve_mib": 512,
            "cancellation": {"prepare_after_cleanup": True, "runs": [{"run_id": "old", "revision": 2,
                "selected": True, "available": True, "status": "ready"}]}}
    monkeypatch.setattr(manager, "review", proposal)
    argv = ["inspect", "topic", "--repository", str(repo), "--allow-gpu", "--managed-pool",
        "--pool-action", "restore", "--" + selection.replace("_", "-"), "old:2", "--gpu-device-uuid", GPU,
        "--auth-transport", "none", "--offline", "--institutional-browser-mode", "off"]
    assert bootstrap.main(argv) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["status"] == "ready_for_review"
    assert inspected["effective_configuration"]["gpu"]["pool"]["start_available"]
    assert bootstrap.main([v for v in inspected["launch_argv"][4:] if v != "--summary"]) == 0
    launched = json.loads(capsys.readouterr().out)
    commands = launched["commands"]
    pool_args = manager.build_parser().parse_args(commands["pool_prepare"][4:])
    assert getattr(pool_args, selection) == ["old:2"]
    assert commands["pool_preflight_available"] and commands["pool_prepare_available"]
    calls = []
    def invoke(argv, *a, **k):
        calls.append(argv)
        if argv == commands["profile_check"]:
            return 0, launched["admission"]
        assert argv == commands["pool_prepare"], "new Host started before old cancellation drained"
        return 2, {"status": "dependency_cleanup_pending", "effects": {"run_cancellations": [{"run_id": "old", "status": "cancellation_pending"}]}}
    monkeypatch.setattr(bootstrap, "_invoke", invoke)
    clock = [0.0]
    monkeypatch.setattr(bootstrap.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _: clock.__setitem__(0, clock[0] + 301))
    result = bootstrap.start_fresh(commands, launched["admission"]["create_run_payload"])
    assert result["status"] == "dependency_cleanup_pending" and len(calls) == 4
    assert result["pool"]["effects"]["run_cancellations"][0]["run_id"] == "old"
    assert not (repo / "state").exists()


@pytest.mark.parametrize("language", ["en", "zh-CN"])
@pytest.mark.parametrize("cap", [1, 4, None])
def test_concurrency_review_explains_global_cap_without_rewriting(language, cap):
    gpu = {"pooled": True, "concurrency": {"authorized_device_count": 4,
        "max_active_processes": cap, "below_device_count": cap == 1}}
    before = json.dumps(gpu)
    text = "\n".join(gpu_concurrency_summary(gpu, language))
    assert "4" in text and "CPU" in text
    assert ("max_active_processes=4" in text or "max_active_processes 调至 4" in text) == (cap == 1)
    assert json.dumps(gpu) == before


def test_stop_only_host_cancels_before_lost_agent_recovery_and_allows_only_report(batch_host, monkeypatch):
    host = batch_host
    monkeypatch.setattr(host, "_finish_fake_agent", lambda *a, **k: None)
    _drive(host, lambda: bool(host.store.list_attempts(state="OPEN")))
    root = host.state_root
    revision = host.store.latest_transition("batch-host")["revision"]
    prior = {a["attempt_id"] for a in host.store.list_attempts()}
    host.close()
    reopened = WorkflowHost(root, local_agent=host.agent, stop_request=recovery.cancel_request("batch-host", revision))
    try:
        reopened.initialize()
        assert reopened.store.latest_transition("batch-host")["state"] == "STOPPING"
        assert all(a["state"] == "CLOSED" and a["terminal_reason"] == "LOST" for a in reopened.store.list_attempts())
        assert reopened.handle(ControlRequest(1, "no-create", 0, "CREATE_RUN", {})).error["code"] == "cancellation_only"
        _drive(reopened, lambda: reopened.store.latest_transition("batch-host")["state"] == "CANCELLED")
        new = [a for a in reopened.store.list_attempts() if a["attempt_id"] not in prior]
        assert new and len(new) == len(_reports(reopened))
        assert all(json.loads(a["observation_json"])["request"]["command"] == "FINAL_REPORT" for a in new)
        assert all(a["state"] == "CLOSED" for a in reopened.store.list_attempts())
        audit = Oracle(reopened.store.database_path, reopened.publisher.root).audit("batch-host")
        assert audit.ok, audit.to_dict()
    finally:
        reopened.close()


def test_stale_stop_only_review_leaves_run_and_attempts_unmodified(batch_host):
    host = batch_host
    root = host.state_root
    head = host.store.latest_transition("batch-host")
    before = host.store.list_attempts()
    host.close()
    reopened = WorkflowHost(root, local_agent=host.agent, stop_request=recovery.cancel_request("batch-host", head["revision"] + 1))
    try:
        with pytest.raises(ValueError, match="cancellation_review_changed"): reopened.initialize()
        assert reopened.store.latest_transition("batch-host") == head
        assert reopened.store.list_attempts() == before
    finally:
        reopened.close()


def test_stop_only_configuration_mismatch_never_records_cancellation(batch_host):
    from hypoloom.workflow_host import PinnedRuntimeError
    host = batch_host
    root = host.state_root
    head = host.store.latest_transition("batch-host")
    host.close()
    reopened = WorkflowHost(root, local_agent=host.agent,
        agent_budget=replace(host.agent_budget, max_tokens=host.agent_budget.max_tokens + 1),
        stop_request=recovery.cancel_request("batch-host", head["revision"]))
    try:
        with pytest.raises(PinnedRuntimeError): reopened.initialize()
        assert reopened.store.latest_transition("batch-host") == head
        assert not reopened.store.inspect("SELECT 1 FROM workflow_commands WHERE command_id LIKE 'init-pool-cancel-%'")
    finally:
        reopened.close()


@pytest.fixture
def short_managed(managed):
    args, state, _ = managed
    with tempfile.TemporaryDirectory(prefix="pdr-") as name:
        repo = Path(name) / "r"
        repo.mkdir()
        pool = repo / ".local/gpu-pools/shared"
        args.repository, args.pool_root, args.socket_path = str(repo), str(pool), str(pool / "control.sock")
        yield args, state, pool


def test_frozen_cli_cpu_loss_closes_then_restores_pool_and_admits_next_run(short_managed, monkeypatch, tmp_path):
    args, state, pool_root = short_managed
    args.ready_timeout = 15
    servers, processes = [], []
    sample = CapacitySample(devices=(GpuRuntimeObservation(uuid=GPU, total_memory_mib=24000,
        free_memory_mib=24000, used_memory_mib=0, utilization_percent=0),))
    def launch(plan, *_):
        pool = GpuPool(plan.pool_root, members=dict(plan.members), reserve_memory_mib=plan.reserve_mib,
                       sampler=lambda: sample)
        server = PoolServer(pool, plan.socket_path).start()
        servers.append(server)
        state.update(pool=pool, active=True, legacy=False)
        state["events"].append("launch")
    monkeypatch.setattr(manager, "_launch_pool", launch)
    run, root = "old", pool_root.parents[2] / "state/old"
    host = new = None
    try:
        manager.ensure(args)
        argv = [sys.executable, "-B", "-m", "hypoloom.cli", "serve", "--state-root", str(root),
            "--agent-adapter", "fake", "--exit-when-terminal", "--gpu-pool-socket", str(pool_root / "control.sock"),
            "--gpu-pool-namespace", run, "--gpu-pool-server-uid", str(os.geteuid())]
        host = subprocess.Popen(argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        processes.append(host)
        deadline = time.monotonic() + 30
        while not (root / "control.sock").exists():
            assert host.poll() is None, host.communicate()
            assert time.monotonic() < deadline
            time.sleep(.02)
        payload = _payload(run, allow_gpu=False)
        payload["execution"]["delay_seconds"] = 25
        response = request_over_socket(root / "control.sock", ControlRequest(1, "create-old", 0, "CREATE_RUN", payload))
        assert response.status == "accepted", response.to_mapping()
        opened = []
        while time.monotonic() < deadline:
            with sqlite3.connect(f"file:{root / 'state.sqlite'}?mode=ro", uri=True) as db:
                opened = db.execute("SELECT attempt_id,process_group_id FROM execution_attempts WHERE kind='PROCESS' AND state='OPEN' AND process_group_id IS NOT NULL").fetchall()
            if opened: break
            assert host.poll() is None, host.communicate()
            time.sleep(.02)
        assert opened, "real CPU Process did not start"
        # Normal Host termination removes its actual child while preserving the
        # unobserved OPEN Attempt, reproducing the durable/liveness mismatch.
        host.terminate()
        host.communicate(timeout=10)
        assert all(not process_group_exists(pgid) for _, pgid in opened)
        target = recovery.read_target(root, run)
        assert target["state"] == "ACTIVE" and target["launch"]
        frozen = root / "bundle/hypoloom/workflow_host.py"
        assert frozen.is_file()
        before_bundle = frozen.read_bytes()
        # Actual released request history survives both cancellation and restore.
        client = PoolClient(pool_root / "control.sock", expected_server_uid=os.geteuid())
        key = {"namespace": run, "run_id": run, "execution_id": "historical", "attempt_id": "historical"}
        assert client.request("reserve", {"key": key, "allowed_gpu_uuids": [GPU], "gpu_count": 1,
            "workload_memory_mib": 1024, "reserve_memory_mib": 256})["state"] == "RESERVED"
        assert client.request("release", {"key": key})["state"] == "RELEASED"
        inode = (pool_root / "pool.sqlite3").stat().st_ino
        servers[-1].close()
        state["active"] = False
        args.reviewed_action, args.pool_cancel_run = "restore", [f"{run}:{target['revision']}"]
        launches = []
        def start_original(_repository, plan, **_):
            old = recovery.read_target(root, run, plan["revision"])
            command, directory, _transport = recovery.recovery_launch(old)
            command += ["--cancel-run-id", run, "--cancel-expected-revision", str(plan["revision"])]
            process = subprocess.Popen(command, cwd=directory, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            processes.append(process); launches.append(process)
            return {"run_id": run, "status": "cancellation_pending"}
        monkeypatch.setattr(recovery, "request_cleanup", start_original)
        with pytest.raises(manager.ManagedPoolError, match="dependency_cleanup_pending"):
            manager.ensure(args)
        assert len(launches) == 1
        output = launches[0].communicate(timeout=30)
        assert launches[0].returncode == 0, output
        monkeypatch.setattr(recovery, "request_cleanup", lambda repository, plan, **k: {
            "run_id": plan["run_id"], "status": plan["status"]})
        assert manager.ensure(args)[1] == "restored"
        assert (pool_root / "pool.sqlite3").stat().st_ino == inode
        assert frozen.read_bytes() == before_bundle
        with sqlite3.connect(f"file:{root / 'state.sqlite'}?mode=ro", uri=True) as db:
            census = db.execute("SELECT kind,state,terminal_reason,process_group_id FROM execution_attempts").fetchall()
            assert db.execute("SELECT count(*) FROM workflow_commands WHERE command_type='CANCEL' AND command_id LIKE 'init-pool-cancel-%'").fetchone()[0] == 1
            assert db.execute("SELECT state FROM workflow_transitions WHERE workflow_id='old' ORDER BY revision DESC LIMIT 1").fetchone()[0] == "CANCELLED"
        assert all(row[1] == "CLOSED" for row in census)
        assert any(row[:3] == ("PROCESS", "CLOSED", "LOST") for row in census)
        assert sum(row[0] == "PROCESS" for row in census) == len(opened)
        assert all(not process_group_exists(row[3]) for row in census if row[3])
        audit = Oracle(root / "state.sqlite", root / "artifacts").audit(run)
        assert audit.ok, audit.to_dict()
        assert audit_gpu_pool(pool_root)["ok"]
        # The restored pool admits a new run through the ordinary Host path.
        new_root = pool_root.parents[2] / "state/new"
        new = WorkflowHost(new_root, gpu_pool_socket=pool_root / "control.sock", gpu_pool_namespace="new", gpu_pool_server_uid=os.geteuid())
        new.initialize()
        assert new.handle(ControlRequest(1, "create-new", 0, "CREATE_RUN", _payload("new", allow_gpu=False))).status == "accepted"
        deadline = time.monotonic() + 30
        while new.store.latest_transition("new")["state"] not in recovery.TERMINAL:
            assert time.monotonic() < deadline
            new.tick(); time.sleep(.005)
        assert new.store.latest_transition("new")["state"] == "COMPLETED"
        new_audit = Oracle(new.store.database_path, new.publisher.root).audit("new")
        assert new_audit.ok, new_audit.to_dict()
        attempts = new.store.list_attempts()
        assert any(a["kind"] == "PROCESS" for a in attempts)
        assert all(a["state"] == "CLOSED" and not (a["process_group_id"] and process_group_exists(a["process_group_id"])) for a in attempts)
        (tmp_path / "recovery-census.json").write_text(json.dumps({"old_attempts": census,
            "new_attempts": [{k: a[k] for k in ("attempt_id", "kind", "state", "terminal_reason", "process_group_id")} for a in attempts],
            "old_oracle": audit.to_dict(), "new_oracle": new_audit.to_dict(), "pool_events": state["events"]}, indent=2))
    finally:
        if new: new.close()
        for process in processes:
            if process.poll() is None:
                process.terminate(); process.communicate(timeout=10)
        for server in servers: server.close()
