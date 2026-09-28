"""Pool maintenance preserves actual SQLite commitments; no physical GPU needed."""
import argparse
from contextlib import ExitStack
import fcntl
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from hypoloom import managed_gpu_pool as manager
from hypoloom.gpu_pool import GpuPool, CapacitySample, PoolError
from hypoloom.gpu_pool_lifecycle import admission_lock, directory_lock, inspect_dependencies

GPU = "GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
GPU2 = "GPU-ffffffff-0000-1111-2222-333333333333"


@pytest.fixture
def managed(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    root = repo / ".local/gpu-pools/shared"
    args = argparse.Namespace(repository=str(repo), pool_root=str(root), socket_path=str(root / "control.sock"),
        member=[f"{os.geteuid()}={GPU}"], reserve_mib=256, ready_timeout=2, reviewed_action="create")
    state = {"active": False, "legacy": False, "events": [], "pool": None}
    tools = manager.Tools("escape", "run", "ctl")
    monkeypatch.setattr(manager, "_find_tools", lambda: tools)
    monkeypatch.setattr(manager, "_derive_unit", lambda p, *a: manager.replace(p, unit="hypoloom-gpu-pool@test.service"))
    monkeypatch.setattr(manager, "_unit_properties", lambda *a: {"LoadState": "loaded" if state["pool"] else "not-found",
        "ActiveState": "active" if state["active"] else "inactive"})
    monkeypatch.setattr(manager, "_socket_state", lambda p: "socket" if state["active"] else "absent")
    def snapshot(*unused):
        if not state["active"]: return "absent", None
        return "live", {"devices": [{"gpu_uuid": g} for g in state["pool"].members[os.geteuid()]],
            "capabilities": [] if state["legacy"] else ["resource_replanning/1"]}
    monkeypatch.setattr(manager, "_probe_snapshot", snapshot)
    def config(p, *a):
        pool = state["pool"]
        return None if pool is None else manager.replace(p, members=tuple((uid, tuple(devices)) for uid, devices in pool.members.items()),
            reserve_mib=pool.reserve_memory_mib)
    monkeypatch.setattr(manager, "_configuration", config)
    def launch(p, *a):
        state["events"].append("launch")
        assert not state["active"]
        state["pool"] = GpuPool(p.pool_root, members=dict(p.members), reserve_memory_mib=p.reserve_mib,
            sampler=lambda: CapacitySample(devices=()))
        state["active"], state["legacy"] = True, False
    def stop(*unused):
        state["events"].append("stop")
        state["active"] = False
        return True
    monkeypatch.setattr(manager, "_launch_pool", launch)
    monkeypatch.setattr(manager, "_stop_unit", stop)
    return args, state, root


def binding(root, *, run_state="CANCELLED", attempt_state="CLOSED", pgid=None):
    state_root = root.parents[2] / "state" / "known"
    state_root.mkdir(parents=True, mode=0o700)
    (state_root / "controller.lock").touch(mode=0o600)
    with sqlite3.connect(state_root / "state.sqlite") as db:
        db.executescript("CREATE TABLE workflow_runs(run_id TEXT PRIMARY KEY);"
            "CREATE TABLE workflow_transitions(workflow_id TEXT,revision INTEGER,workflow_kind TEXT,state TEXT,run_id TEXT,payload_json TEXT);"
            "CREATE UNIQUE INDEX transition_revision ON workflow_transitions(workflow_id,revision);"
            "CREATE TABLE execution_attempts(run_id TEXT,state TEXT,process_group_id INTEGER);")
        db.execute("INSERT INTO workflow_runs VALUES('known')")
        db.execute("INSERT INTO workflow_transitions VALUES('known',1,'RUN','CREATED','known',?)", (json.dumps({"gpu_pool": {
            "socket_path": str(root / "control.sock"), "namespace": "namespace", "server_uid": os.geteuid()}}),))
        db.execute("INSERT INTO workflow_transitions VALUES('known',2,'RUN',?,'known','{}')", (run_state,))
        db.execute("INSERT INTO execution_attempts VALUES('known',?,?)", (attempt_state, pgid))
    return state_root


def test_review_is_effect_free_then_create_and_reuse(managed):
    args, state, root = managed
    assert manager.review(args)["action"] == "create"
    assert not root.exists()
    assert manager.ensure(args)[1] == "ready"
    assert manager.ensure(args)[1] == "reused"
    assert state["events"] == ["launch"]


def test_supervisor_discovery_failure_is_a_structured_read_only_block(managed, monkeypatch):
    args, state, root = managed
    def unavailable():
        raise manager.ManagedPoolError("user_systemd_tools_unavailable")
    monkeypatch.setattr(manager, "_find_tools", unavailable)
    result = manager.review(args)
    assert result["reason"] == "user_systemd_tools_unavailable"
    assert not result["prepare_available"] and not root.exists() and not state["events"]


def test_lost_launch_reply_cleans_up_its_service_and_reports_effects(managed, monkeypatch):
    args, state, root = managed
    launch = manager._launch_pool
    def lost_reply(*a):
        launch(*a)
        raise manager.ManagedPoolError("supervisor_command_failed")
    monkeypatch.setattr(manager, "_launch_pool", lost_reply)
    with pytest.raises(manager.ManagedPoolError) as error:
        manager.ensure(args)
    assert state["events"] == ["launch", "stop"] and not state["active"]
    assert error.value.effects == {"pool_directory_created": True,
        "new_service_start_requested": True, "failed_service_stop_acknowledged": True}
    assert (root / "pool.sqlite3").exists()


def test_empty_legacy_host_database_still_holds_ownership(managed):
    args, state, root = managed
    manager.ensure(args)
    run = binding(root)
    with sqlite3.connect(run / "state.sqlite") as db:
        db.execute("DELETE FROM workflow_transitions")
        db.execute("DELETE FROM workflow_runs")
    with (run / "controller.lock").open() as controller:
        fcntl.flock(controller, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state["legacy"] = True
        assert not manager.review(args)["prepare_available"]
    assert state["events"] == ["launch"]


def test_multiple_historical_bindings_share_one_controller_lock(managed):
    args, state, root = managed
    manager.ensure(args)
    run = binding(root)
    with sqlite3.connect(run / "state.sqlite") as db:
        db.execute("INSERT INTO workflow_runs VALUES ('another')")
        db.execute("INSERT INTO workflow_transitions SELECT 'another',revision,workflow_kind,state,'another',payload_json "
                   "FROM workflow_transitions WHERE workflow_id='known'")
    state["legacy"] = True
    args.reviewed_action = "upgrade"
    assert manager.ensure(args)[1] == "upgraded"


def test_real_host_admission_is_serialized_with_maintenance(tmp_path, monkeypatch):
    from hypoloom.control_protocol import ControlRequest
    from hypoloom.workflow_host import WorkflowHost
    from test_gpu_host_integration import _resources, _capabilities, _payload
    repo = tmp_path / "repository"
    root = repo / ".local/gpu-pools/shared"
    root.mkdir(parents=True, mode=0o700)
    resources = _resources(tmp_path, _capabilities)
    host = WorkflowHost(repo / "state/fresh", compute_resources=resources,
        gpu_pool_socket=root / "control.sock", gpu_pool_namespace="fresh")
    observed = []
    def snapshot(operation, arguments):
        assert operation == "snapshot"
        with pytest.raises(PoolError, match="in progress"):
            with directory_lock(root, exclusive=True, timeout=0):
                pytest.fail("maintenance overlapped the Host preflight")
        observed.append(operation)
        return {"capabilities": ["resource_replanning/1"],
            "devices": [{"gpu_uuid": gpu.uuid} for gpu in _capabilities().gpus]}
    monkeypatch.setattr(host.shared_gpu.client, "request", snapshot)
    try:
        host.initialize()
        payload = _payload("fresh", runtime=resources.gpu_runtime_prefix)
        payload["research_contract"]["budget"]["min_vram_per_experiment"] = 1024
        with directory_lock(root, exclusive=True):
            blocked = host.handle(ControlRequest(1, "blocked", 0, "CREATE_RUN", payload))
            assert blocked.status != "accepted" and "pool_maintenance_busy" in str(blocked.error)
        assert not host.store.inspect("SELECT * FROM workflow_runs")
        created = host.handle(ControlRequest(1, "created", 0, "CREATE_RUN", payload))
        assert created.status == "accepted", created
        assert observed == ["snapshot"]
        assert len(host.store.inspect("SELECT * FROM workflow_runs")) == 1
        assert not host.store.inspect("SELECT * FROM execution_attempts")
        with directory_lock(root, exclusive=True, timeout=0):
            dependencies = inspect_dependencies(repo, root / "control.sock")
        assert dependencies["bindings"][0]["run_id"] == "fresh"
        assert any(b["reason"] == "run_depends_on_pool" for b in dependencies["blockers"])
    finally:
        host.close()


def test_legacy_upgrade_keeps_database_and_historical_requests(managed):
    args, state, root = managed
    manager.ensure(args)
    binding(root)
    with sqlite3.connect(root / "pool.sqlite3") as db:
        db.execute("INSERT INTO runs(uid,namespace,run_id) VALUES(?,'namespace','known')", (os.geteuid(),))
        db.execute("INSERT INTO requests(uid,namespace,run_id,execution_id,attempt_id,state,intent_json) "
            "VALUES(?,'namespace','known','experiment','attempt','RELEASED','{}')", (os.geteuid(),))
    inode = (root / "pool.sqlite3").stat().st_ino
    state["legacy"] = True
    review = manager.review(args)
    assert review["action"] == "upgrade" and review["prepare_available"] and not review["start_ready"]
    args.reviewed_action = "upgrade"
    assert manager.ensure(args)[1] == "upgraded"
    assert state["events"] == ["launch", "stop", "launch"]
    assert manager.review(args)["action"] == "reuse"
    assert (root / "pool.sqlite3").stat().st_ino == inode
    with sqlite3.connect(root / "pool.sqlite3") as db:
        assert db.execute("SELECT state FROM requests").fetchall() == [("RELEASED",)]


@pytest.mark.parametrize("state_name,attempt", [("ACTIVE", "CLOSED"), ("PAUSED", "CLOSED"), ("CANCELLED", "OPEN")])
def test_dependency_without_gpu_request_prevents_upgrade(managed, state_name, attempt):
    args, state, root = managed
    manager.ensure(args)
    binding(root, run_state=state_name, attempt_state=attempt)
    state["legacy"] = True
    assert not manager.review(args)["prepare_available"]
    args.reviewed_action = "upgrade"
    with pytest.raises(manager.ManagedPoolError, match="dependencies_active_or_unknown"):
        manager.ensure(args)
    assert state["events"] == ["launch"]


def test_live_host_or_unknown_owner_prevents_upgrade(managed):
    args, state, root = managed
    manager.ensure(args)
    run = binding(root)
    state["legacy"] = True
    with (run / "controller.lock").open() as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not manager.review(args)["prepare_available"]
    with sqlite3.connect(root / "pool.sqlite3") as db:
        db.execute("INSERT INTO runs(uid,namespace,run_id) VALUES(?,'outside','unseen')", (os.geteuid(),))
    assert manager.review(args)["dependencies"]["blockers"] == [{"reason": "pool_owner_outside_known_roots"}]


def test_upgrade_requires_reviewed_full_membership_and_reserve(managed):
    args, state, root = managed
    args.member = [f"{os.geteuid()}={GPU},{GPU2}"]
    manager.ensure(args)
    args.member = [f"{os.geteuid()}={GPU}"]
    assert manager.ensure(args)[1] == "reused"
    state["legacy"] = True
    args.reviewed_action = "upgrade"
    review = manager.review(args)
    assert review["members"][str(os.geteuid())] == [GPU, GPU2]
    with pytest.raises(manager.ManagedPoolError, match="pool_configuration_changed"):
        manager.ensure(args)
    assert state["events"] == ["launch"]


def test_stopped_pool_restore_and_explicit_reconfiguration(managed):
    args, state, root = managed
    manager.ensure(args)
    state["active"] = False
    assert manager.review(args)["action"] == "restore"
    args.reviewed_action = "restore"
    assert manager.ensure(args)[1] == "restored"
    args.reviewed_action = "reconfigure"
    args.member = [f"{os.geteuid()}={GPU2}"]
    assert manager.ensure(args)[1] == "reconfigured"
    assert state["pool"].members[os.geteuid()] == (GPU2,)


def test_failed_start_keeps_database_and_no_silent_fallback(managed, monkeypatch):
    args, state, root = managed
    manager.ensure(args)
    state["legacy"] = True
    args.reviewed_action = "upgrade"
    monkeypatch.setattr(manager, "_wait_until_ready", lambda *a: (_ for _ in ()).throw(manager.ManagedPoolError("pool_ready_timeout")))
    with pytest.raises(manager.ManagedPoolError, match="pool_ready_timeout"):
        manager.ensure(args)
    assert not state["active"] and (root / "pool.sqlite3").exists()
    assert state["events"] == ["launch", "stop", "launch", "stop"]


def test_maintenance_and_admission_use_real_process_locks(tmp_path):
    root = tmp_path / ".local/gpu-pools/shared"
    root.mkdir(parents=True, mode=0o700)
    program = "from pathlib import Path; from hypoloom.gpu_pool_lifecycle import directory_lock; import sys\nwith directory_lock(Path(sys.argv[1]), exclusive=False, timeout=3): print('entered', flush=True)\n"
    with directory_lock(root, exclusive=True):
        child = subprocess.Popen([sys.executable, "-B", "-c", program, str(root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with pytest.raises(subprocess.TimeoutExpired): child.communicate(timeout=0.2)
        except BaseException:
            child.kill(); child.communicate(); raise
    out, err = child.communicate(timeout=5)
    assert child.returncode == 0 and out.strip() == "entered" and not err
    # An abandoned maintenance owner releases OS ownership, without a marker.
    with admission_lock(root / "control.sock", tmp_path / "state/fresh"):
        with pytest.raises(PoolError, match="preparation"):
            with directory_lock(root, exclusive=True, timeout=0): pass
    with directory_lock(root, exclusive=True, timeout=0): pass


def test_known_controller_locks_remain_held_through_maintenance(managed):
    args, state, root = managed
    manager.ensure(args)
    run = binding(root)
    with ExitStack() as stack:
        assert inspect_dependencies(root.parents[2], root / "control.sock", locks=stack)["quiescent"]
        with (run / "controller.lock").open() as stream:
            with pytest.raises(BlockingIOError): fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize("request_state", ["QUEUED", "RESERVED", "LAUNCHING", "BOUND"])
def test_any_unreleased_request_blocks_maintenance(managed, request_state):
    args, state, root = managed
    manager.ensure(args)
    binding(root)
    with sqlite3.connect(root / "pool.sqlite3") as db:
        db.execute("INSERT INTO runs(uid,namespace,run_id) VALUES(?,'namespace','known')", (os.geteuid(),))
        db.execute("INSERT INTO requests(uid,namespace,run_id,execution_id,attempt_id,state,intent_json) "
            "VALUES(?,'namespace','known','exec','attempt',?,'{}')", (os.geteuid(), request_state))
    state["legacy"] = True
    proposal = manager.review(args)
    assert not proposal["prepare_available"]
    assert {"reason": "unreleased_pool_request"} in proposal["dependencies"]["blockers"]
    assert state["events"] == ["launch"]


def test_terminal_run_with_real_process_still_blocks(managed):
    args, state, root = managed
    manager.ensure(args)
    process = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(10)"], start_new_session=True)
    try:
        binding(root, pgid=process.pid)
        state["legacy"] = True
        proposal = manager.review(args)
        assert not proposal["prepare_available"]
        assert {"run_id": "known", "reason": "process_present"} in proposal["dependencies"]["blockers"]
    finally:
        process.terminate(); process.wait(timeout=3)


def test_dependency_arriving_after_preview_prevents_service_stop(managed, monkeypatch):
    args, state, root = managed
    manager.ensure(args)
    state["legacy"] = True
    args.reviewed_action = "upgrade"
    observe = manager._observe
    count = 0
    def race(*values, **kw):
        nonlocal count
        count += 1
        if count == 2: binding(root, run_state="ACTIVE")
        return observe(*values, **kw)
    monkeypatch.setattr(manager, "_observe", race)
    with pytest.raises(manager.ManagedPoolError, match="pool_plan_changed"):
        manager.ensure(args)
    assert state["events"] == ["launch"]


def test_repeated_reviewed_reconfiguration_reuses_service(managed):
    args, state, root = managed
    manager.ensure(args)
    args.reviewed_action = "reconfigure"
    args.member = [f"{os.geteuid()}={GPU2}"]
    assert manager.ensure(args)[1] == "reconfigured"
    assert manager.ensure(args)[1] == "reused"
    assert state["events"] == ["launch", "stop", "launch"]


def test_stopped_service_configuration_is_read_without_environment(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    root = repo / ".local/gpu-pools/shared"
    plan = manager.PoolPlan(repo, root, root / "control.sock", ((os.geteuid(), (GPU,)),), 256, "unit")
    tools = manager.Tools("escape", "run", "ctl")
    queries = []
    def command(argv, **kw):
        queries.append(argv)
        text = "0\n" if "--property=MainPID" in argv else (
            f"{{ path={sys.executable} ; argv[]={sys.executable} -B -m hypoloom.cli gpu-pool-serve "
            f"--pool-root {root} --socket-path {root / 'control.sock'} --member {os.geteuid()}={GPU} "
            "--reserve-mib 1024 ; ignore_errors=no ; pid=0 ; code=(null) ; status=0 }\n")
        return subprocess.CompletedProcess(argv, 0, text, "")
    monkeypatch.setattr(manager, "_quiet_run", command)
    prior = manager._configuration(plan, tools, {})
    assert prior is not None and prior.reserve_mib == 1024 and prior.members == plan.members
    assert all("Environment" not in " ".join(q) for q in queries)


def test_managed_admission_rejects_external_root_without_writing(tmp_path):
    root = tmp_path / ".local/gpu-pools/shared"
    with pytest.raises(ValueError, match="external pool"):
        with admission_lock(root / "control.sock", tmp_path / "external/fresh"): pass
    assert not root.exists()
