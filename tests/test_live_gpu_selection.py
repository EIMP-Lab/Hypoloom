"""Live controls against real Host/pool stores, with deterministic GPU observations."""
import copy
import json
import os
import time
from types import SimpleNamespace

import pytest

from hypoloom.compute_resources import ComputeCapabilities, GpuCapability, GpuRuntimeObservation, LocalComputeResources, ResourceUnavailable
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import ResourceIntent
from hypoloom.gpu_pool import CapacitySample, GpuPool, PoolError
from hypoloom.lifecycle import Command, RunState
from hypoloom.oracle import _audit_shared_process_resource, _raw_resource_selections, audit_gpu_pool
from hypoloom.resource_controls import ResourceControls
from hypoloom.workflow_host import WorkflowHost


A, B, C = (f"GPU-00000000-0000-0000-0000-{index:012d}" for index in range(1, 4))


def inventory():
    return ComputeCapabilities(gpus=tuple(GpuCapability(index=i, uuid=uuid, name="Test GPU",
        total_memory_mib=32000, free_memory_mib=32000, driver_version="test-driver")
        for i, uuid in enumerate((A, B, C))))


def capacity():
    return CapacitySample(devices=tuple(GpuRuntimeObservation(uuid=gpu.uuid,
        total_memory_mib=gpu.total_memory_mib, free_memory_mib=gpu.free_memory_mib,
        used_memory_mib=0, utilization_percent=0) for gpu in inventory().gpus))


def intent(allowed=(A,), count=1):
    return ResourceIntent(allowed_gpu_uuids=allowed, gpu_count=count, workload_memory_mib=1024,
        reserve_memory_mib=512, sharing="exclusive")


def arguments(resource):
    return {key: value for key, value in resource.to_dict().items() if key != "kind"}


@pytest.fixture
def live(tmp_path):
    prefix = tmp_path / "runtime"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/python").touch()
    pool = GpuPool(tmp_path / "pool", members={os.getuid(): (A, B, C)}, reserve_memory_mib=512,
        sampler=capacity, process_present=lambda _: True)
    compute = LocalComputeResources(allowed_gpu_uuids=(A,), gpu_runtime_prefix=prefix, inventory_provider=inventory)
    host = WorkflowHost(tmp_path / "host", compute_resources=compute,
        gpu_pool_socket=tmp_path / "pool.sock", gpu_pool_namespace="test")
    host.shared_gpu.client = SimpleNamespace(socket_path=tmp_path / "pool.sock",
        request=lambda operation, values: pool.handle(os.getuid(), operation, values))
    host.initialize()
    document = {"methodology": {"controls": {"product": {"safety": {"gpus": [A]}}}}}
    host._research_contract = lambda _: SimpleNamespace(document=document)
    with host.store.transaction() as tx:
        tx.create_run("run", host.version)
        tx.append_transition(host._transition_record(tx, "run", "run", "RUN", Command.CREATE_RUN, RunState.CREATED,
            payload={"execution": {"allow_gpu": True}, "gpu_pool": host.shared_gpu.configuration, "gpu_selection": [A]}))
        tx.append_transition(host._transition_record(tx, "run", "run", "RUN", Command.ACTIVATE, RunState.ACTIVE))
    yield host, pool
    host.close()


def select(host, mode, values=(), *, request_id=None, revision=None):
    request = ControlRequest(1, request_id or f"select-{time.time_ns()}", revision, "RESOURCE_SELECT",
        {"run_id": "run", "mode": mode, "gpu_uuids": list(values)})
    return request, host.handle(request)


def add_experiment(host, experiment="e", *, running=False):
    """Persist the minimal scheduling records; no scientific/Process effects run."""
    execution = experiment + "--execution"
    with host.store.transaction() as tx:
        command = experiment + "--fixture"
        tx.record_command({"command_id": command, "run_id": "run", "workflow_id": experiment,
            "command_type": "EXECUTE" if running else "PREPARE", "request": {}, "status": "accepted", "result": {}, "revision": 1})
        payload = {}
        if running:
            attempt_id = experiment + "--process-1"
            payload = {"attempt_id": attempt_id, "resource_grant": {"kind": "GPU", "gpu_uuid": A},
                "gpu_pool_key": host.shared_gpu.key("run", execution, attempt_id)}
            tx.open_attempt({"attempt_id": attempt_id, "run_id": "run", "workflow_id": experiment,
                "command_id": command, "kind": "PROCESS", "experiment_id": experiment, "execution_id": execution,
                "attempt_ordinal": 1, "host_generation": host.generation, "started_at": time.time()})
        tx.append_transition({"workflow_id": experiment, "revision": 1, "run_id": "run", "workflow_kind": "EXPERIMENT",
            "state": "EXECUTING" if running else "READY", "command_id": command, "payload": payload})
        tx.append_fact({"fact_id": experiment + "--spec", "run_id": "run", "fact_type": "ExperimentPreregistered",
            "schema_version": 5, "subject_id": experiment, "hypothesis_id": "hypothesis", "experiment_id": experiment, "execution_id": execution,
            "payload": {"execution_contract": {"resource_intent": intent().to_dict()}}})
    return host.shared_gpu.key("run", execution, experiment + "--process-1")


def raw_context(host):
    return {name: list(host.store.inspect("SELECT * FROM " + table + " WHERE run_id='run'"))
        for name, table in (("commands", "workflow_commands"), ("transitions", "workflow_transitions"))}


def test_add_replace_remove_all_pause_and_persistent_authority(live):
    host, _ = live
    original_identity = host._runtime_version()
    assert select(host, "add", (B,))[1].status == "accepted"
    assert [gpu.uuid for gpu in host.resource_controls.capabilities("run").gpus] == [A, B]
    assert select(host, "set", (B, C))[1].status == "accepted"
    assert select(host, "remove", (B,))[1].status == "accepted"
    assert host.resource_controls.status("run")["gpu_uuids"] == [C]
    assert select(host, "all")[1].status == "accepted"
    assert host.resource_controls.status("run")["gpu_uuids"] == [A, B, C]
    assert select(host, "none")[1].status == "accepted"
    assert host.resource_controls.capabilities("run").gpus == ()
    with pytest.raises(ResourceUnavailable, match="operator_gpu_selection"):
        host.resource_controls.effective("run", intent())
    restored = ResourceControls(host)
    assert restored.status("run")["gpu_uuids"] == []
    assert restored.compute("run").allowed_gpu_uuids == (A, B, C)
    assert host.compute_resources.allowed_gpu_uuids == (A,)
    assert host._run_config("run")["gpu_selection"] == [A]
    assert host._runtime_version() == original_identity
    assert len(_raw_resource_selections(raw_context(host), "run")) == 5


def test_request_replay_and_revision_conflict_do_not_reapply_selection(live):
    host, _ = live
    request, first = select(host, "set", (B,), request_id="once")
    assert first.status == "accepted"
    select(host, "set", (C,))
    replay = host.handle(request)
    assert replay == first
    assert host.resource_controls.status("run")["gpu_uuids"] == [C]
    assert select(host, "set", (A,), revision=first.revision)[1].status == "conflict"
    assert select(host, "set", (A,), request_id="once")[1].status == "conflict"
    assert len(host.resource_controls.history("run")) == 2


@pytest.mark.parametrize("mode,values", [("set", [A, A]), ("add", ["GPU-unknown"]), ("set", []), ("all", [A]), ("remove", [B])])
def test_invalid_selections_leave_authority_unchanged(live, mode, values):
    host, _ = live
    assert select(host, mode, values)[1].status == "rejected"
    assert not host.resource_controls.history("run")


def test_unauthorized_gpu_and_old_pool_are_rejected(live):
    host, pool = live
    pool.members[os.getuid()] = (A,)
    assert select(host, "add", (B,))[1].status == "rejected"
    original = host.shared_gpu.snapshot
    host.shared_gpu.snapshot = lambda: {**original(), "capabilities": ["resource_replanning/1"]}
    assert select(host, "none")[1].status == "rejected"
    assert not host.resource_controls.history("run")


def test_pending_reservation_moves_and_running_job_drains(live):
    host, pool = live
    running = add_experiment(host, "running", running=True)
    pending = add_experiment(host, "pending")
    host.shared_gpu.reserve(intent(), running)
    host.shared_gpu.activate(running, 123456)
    assert host.shared_gpu.reserve(intent(), pending) is None
    _, response = select(host, "set", (B,))
    assert response.status == "accepted"
    current, _, revision = host.resource_controls.effective("run", intent())
    grant = host.shared_gpu.reserve(current, pending)
    assert grant.device_uuids == (B,)
    assert revision == response.revision
    assert pool.handle(os.getuid(), "status", {"key": running})["gpu_uuids"] == [A]
    assert host.resource_controls.status("run")["draining_gpu_uuids"] == [A]
    assert audit_gpu_pool(pool.root)["ok"]


def test_reserved_memory_is_returned_on_pause_and_request_can_resume(live):
    host, pool = live
    pending = add_experiment(host)
    assert host.shared_gpu.reserve(intent(), pending).device_uuids == (A,)
    select(host, "none")
    suspended = pool.handle(os.getuid(), "status", {"key": pending})
    assert suspended["state"] == "QUEUED" and suspended["gpu_uuids"] == []
    assert suspended["reason"] == "operator_gpu_selection"
    other = {**pending, "run_id": "other"}
    assert host.shared_gpu.reserve(intent(), other).device_uuids == (A,)
    assert audit_gpu_pool(pool.root)["ok"]
    select(host, "set", (B,))
    effective, _, _ = host.resource_controls.effective("run", intent())
    assert host.shared_gpu.reserve(effective, pending).device_uuids == (B,)
    assert audit_gpu_pool(pool.root)["ok"]


def test_unconfirmed_pool_update_recovers_after_host_controller_restart(live):
    host, pool = live
    pending = add_experiment(host)
    host.shared_gpu.reserve(intent(), pending)
    reselect = host.shared_gpu.reselect
    def unavailable(*args):
        raise PoolError("unavailable", "test pool outage")
    host.shared_gpu.reselect = unavailable
    _, response = select(host, "set", (B,))
    assert response.status == "accepted"
    assert host.resource_controls.status("run")["pending_cleanup"]["code"] == "unavailable"
    host.shared_gpu.reselect = reselect
    host.resource_controls = ResourceControls(host)
    assert host.resource_controls.reconcile("run")
    effective, _, _ = host.resource_controls.effective("run", intent())
    assert host.shared_gpu.reserve(effective, pending).device_uuids == (B,)


@pytest.mark.parametrize("operation,extra", [("arm", {}), ("activate", {"process_group_id": 123456})])
def test_pool_never_reselects_launching_or_bound_process(live, operation, extra):
    host, pool = live
    key = host.shared_gpu.key("run", "execution", "attempt")
    host.shared_gpu.reserve(intent(), key)
    pool.handle(os.getuid(), operation, {"key": key, **extra})
    with pytest.raises(PoolError, match="unlaunched"):
        host.shared_gpu.reselect(key, [B])
    assert pool.handle(os.getuid(), "status", {"key": key})["gpu_uuids"] == [A]


def test_multi_gpu_wait_keeps_count_and_budgets_and_does_not_block_smaller_jobs(live):
    host, pool = live
    large = host.shared_gpu.key("run", "large", "attempt")
    host.shared_gpu.reserve(intent((A, B), 2), large)
    host.shared_gpu.reselect(large, [B])
    small = host.shared_gpu.key("run", "small", "attempt")
    assert host.shared_gpu.reserve(intent((B,)), small).device_uuids == (B,)
    assert pool.handle(os.getuid(), "status", {"key": large})["state"] == "QUEUED"
    select(host, "set", (B,))
    with pytest.raises(ResourceUnavailable): host.resource_controls.effective("run", intent((A, B), 2))
    select(host, "set", (B, C))
    effective, caps, _ = host.resource_controls.effective("run", intent((A, B), 2))
    assert effective.gpu_count == 2 and effective.workload_memory_mib == 1024 and effective.reserve_memory_mib == 512
    assert effective.sharing == "exclusive" and effective.allowed_gpu_uuids == (B, C)
    assert [gpu.uuid for gpu in caps.gpus] == [B, C]
    assert audit_gpu_pool(pool.root)["ok"]


def test_oracle_binds_dispatch_to_its_selection_and_preserves_old_attempt(live):
    host, _ = live
    old_start = time.time()
    _, response = select(host, "set", (B,))
    selections = _raw_resource_selections(raw_context(host), "run")
    resource = {"source_valid": True, "intent": intent().to_dict(), "pool_binding": host.shared_gpu.configuration,
        "gpu_by_uuid": {gpu.uuid: gpu.to_dict() for gpu in inventory().gpus if gpu.uuid == A}, "resource_selections": selections}
    attempt = {"attempt_id": "p", "run_id": "run", "execution_id": "x", "started_at": time.time()}
    dispatch = {"resource_grant": {"kind": "GPU", "gpu_uuid": B}, "gpu_uuid": B,
        "gpu_pool_key": host.shared_gpu.key("run", "x", "p"), "resource_selection_revision": response.revision}
    issues = []
    result = _audit_shared_process_resource(resource, dispatch, attempt, {}, issues)
    assert not issues and result["gpu_indices"] == (1,)
    tampered = {**dispatch, "resource_selection_revision": 0}
    _audit_shared_process_resource(resource, tampered, attempt, {}, issues)
    assert issues[-1].code == "shared_gpu_grant_mismatch"
    old = {**tampered, "resource_grant": {"kind": "GPU", "gpu_uuid": A}, "gpu_uuid": A}
    issues.clear()
    _audit_shared_process_resource(resource, old, {**attempt, "started_at": old_start}, {}, issues)
    assert not issues
    context = copy.deepcopy(raw_context(host))
    selected = next(row for row in context["transitions"] if row["revision"] == response.revision)
    payload = json.loads(selected["payload_json"])
    payload["resource_selection"]["gpu_uuids"] = [C]
    selected["payload_json"] = json.dumps(payload)
    with pytest.raises(AssertionError): _raw_resource_selections(context, "run")


@pytest.mark.parametrize("changed", [False, True])
def test_selection_audit_reads_sqlite_rows_with_or_without_operator_history(live, changed):
    import sqlite3
    host, _ = live
    if changed:
        _, response = select(host, "set", (B,))
    expected = _raw_resource_selections(raw_context(host), "run")
    with sqlite3.connect(host.store.database_path) as connection:
        connection.row_factory = sqlite3.Row
        context = {"commands": connection.execute("SELECT * FROM workflow_commands").fetchall(),
            "transitions": connection.execute("SELECT * FROM workflow_transitions").fetchall()}
    assert _raw_resource_selections(context, "run") == expected
    assert (expected[response.revision]["gpu_uuids"] == [B]) if changed else not expected


def test_cli_exposes_set_add_remove_pause_and_options():
    from hypoloom.cli import build_parser
    parser = build_parser()
    for flags in (["--gpu", A, "--gpu", B], ["--add-gpu", B], ["--remove-gpu", A], ["--all-gpus"], ["--no-gpus"]):
        assert parser.parse_args(["resource-select", "--run-id", "run", *flags]).run_id == "run"
    assert parser.parse_args(["resource-options", "--run-id", "run"]).expected_revision is None


def test_empty_selection_holds_new_gpu_planning_without_spending_attempts(live):
    from hypoloom.domain import ComponentResultKind
    host, _ = live
    select(host, "none")
    host._research_contract = lambda _: SimpleNamespace(document={},
        component=lambda _: SimpleNamespace(result_kind=ComponentResultKind.METRIC))
    assert host.resource_controls.pauses_planning("run", "component", "EXPERIMENT_DESIGN_IMPLEMENT")
    assert host.resource_controls.pauses_planning("run", "component", "EXPERIMENT_EXECUTION")
    assert not host.resource_controls.pauses_planning("run", "component", "LITERATURE_REVIEW")
    assert not host.store.list_attempts(run_id="run")


def test_reopened_host_replays_selection_without_changing_launch_configuration(live):
    host, pool = live
    select(host, "set", (B, C))
    configuration, version = host.configuration.copy(), host.version
    host.close()
    restored = WorkflowHost(host.state_root, compute_resources=host.compute_resources,
        gpu_pool_socket=host.shared_gpu.socket_path, gpu_pool_namespace=host.shared_gpu.namespace)
    restored.shared_gpu.client = SimpleNamespace(socket_path=host.shared_gpu.socket_path,
        request=lambda operation, values: pool.handle(os.getuid(), operation, values))
    restored._research_contract = host._research_contract
    try:
        restored.initialize()
        assert restored.version == version and restored.configuration == configuration
        assert [gpu.uuid for gpu in restored.resource_controls.capabilities("run").gpus] == [B, C]
        effective, _, _ = restored.resource_controls.effective("run", intent())
        key = restored.shared_gpu.key("run", "x", "e--process-1")
        assert restored.shared_gpu.reserve(effective, key).device_uuids[0] in {B, C}
    finally:
        restored.close()


def test_host_dispatch_uses_new_devices_and_persists_selection_revision(live, monkeypatch):
    host, pool = live
    key = add_experiment(host)
    # The original source and scientific contract remain fixed on A.
    host.shared_gpu.reserve(intent(), key)
    _, response = select(host, "set", (B,))
    captured = {}
    host._at_capacity = lambda *args: False
    host._participant_busy = lambda *args, **kwargs: False
    host._execution_mounts = lambda *args: ((), None, None)
    host._implementation_patch = lambda *args: None
    document = {"budget": {"min_vram_per_experiment": 1024}}
    host._research_contract = lambda _: SimpleNamespace(document=document)
    monkeypatch.setattr("hypoloom.workflow_host.wallclock_expired", lambda *args: False)
    monkeypatch.setattr("hypoloom.workflow_host._component_plan", lambda *args: {})
    original_inspect = host.store.inspect
    def inspect(sql, values=()):
        rows = original_inspect(sql, values)
        if sql.startswith("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND revision=1") and values == ("e",):
            return ({"payload_json": json.dumps({"role": "expert", "component_id": "component"})},)
        return rows
    monkeypatch.setattr(host.store, "inspect", inspect)

    process_request = SimpleNamespace(attempt_id="e--process-1", to_dict=lambda: {"attempt_id": "e--process-1"})
    prepared = SimpleNamespace(process_request=process_request, agent_attempt_id="source", agent_output_digest="digest")
    workspace = SimpleNamespace(plan=lambda **kwargs: prepared, materialize=lambda *args, **kwargs: None,
        assert_prestart_clean=lambda *args, **kwargs: None)
    def execution_inputs(run_id, experiment_id, *, actual_grant, resource_revision):
        captured.update(grant=actual_grant, revision=resource_revision)
        spec = host.store.find_fact(run_id, "ExperimentPreregistered", experiment_id)
        spec["payload"].update(component_id="component", method_family_id="family")
        effective, caps, _ = host.resource_controls.effective(run_id, intent(), revision=resource_revision)
        contract = {"resource_intent": intent().to_dict(), "environment": {}, "timeout_seconds": 30, "argv": ["python", "experiment.py"]}
        assert actual_grant.device_uuids == effective.allowed_gpu_uuids
        return spec, contract, {}, {}, "experiment.py", "result.json", workspace, caps, actual_grant
    host._execution_inputs = execution_inputs
    def start(request, *, before_exec):
        before_exec(123456)
        return SimpleNamespace(returncode=None, pid=123456)
    host.runner.start = start
    host.runner.cancel = host.runner.cleanup = lambda *args, **kwargs: None
    host._start_process("run", "e", retry=False)
    assert captured["grant"].device_uuids == (B,)
    assert captured["revision"] == response.revision
    dispatch = json.loads(host.store.latest_transition("e")["payload_json"])
    assert dispatch["resource_grant"]["gpu_uuid"] == B
    assert dispatch["resource_selection_revision"] == response.revision
    assert pool.handle(os.getuid(), "status", {"key": key})["state"] == "BOUND"
    assert host.store.find_fact("run", "ExperimentPreregistered", "e")["payload"]["execution_contract"]["resource_intent"] == intent().to_dict()


def test_web_selection_requires_operator_scope_and_same_origin(tmp_path, monkeypatch):
    import http.client
    import threading
    from hypoloom.control_protocol import ControlResponse
    from hypoloom.web_observer import create_server
    received = []
    def send(path, request, **kwargs):
        received.append(request)
        return ControlResponse.accepted(request.request_id, 3, {"gpu_uuids": [B]})
    monkeypatch.setattr("hypoloom.control_protocol.request_over_socket", send)
    server = create_server(tmp_path, "run", port=0)
    server.log_requests = False
    server._provider_token = "test-operator-token"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = f"127.0.0.1:{server.server_address[1]}"
    def post(path, body, *, operator=True, origin=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        headers = {"Authorization": "Bearer " + server.access_token, "Content-Type": "application/json",
            "Origin": origin or "http://" + address}
        if operator: headers["X-Hypoloom-Provider-Token"] = server._provider_token
        connection.request("POST", path, body=json.dumps(body), headers=headers)
        response = connection.getresponse()
        status, result = response.status, json.loads(response.read())
        connection.close()
        return status, result
    try:
        body = {"request_id": "web-choice", "mode": "set", "gpu_uuids": [B]}
        assert post("/api/resource-select", body, operator=False)[0] == 403
        assert post("/api/resource-select", body, origin="http://untrusted.example")[0] == 403
        assert not received
        assert post("/api/resource-select", {**body, "run_id": "different"})[0] == 400
        assert post("/api/resource-select", body)[0] == 200
        assert received[-1].command == "RESOURCE_SELECT" and received[-1].payload["run_id"] == "run"
        assert post("/api/resource-options", {"request_id": "options"})[0] == 200
        assert received[-1].command == "RESOURCE_OPTIONS"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_tui_selects_devices_and_retries_same_request_after_lost_reply(live):
    import asyncio
    pytest.importorskip("textual")
    from textual.app import App
    from textual.widgets import SelectionList
    from hypoloom.resource_selection_tui import ResourceSelectionScreen
    host, _ = live
    draft, requests = {}, []
    screen = ResourceSelectionScreen(draft)
    async def send(request):
        response = host.handle(request)
        if request.command == "RESOURCE_SELECT":
            requests.append(request)
            if len(requests) == 1: raise OSError("reply lost after commit")
        return response
    screen.send = send
    class TestApp(App):
        run_id = "run"
        state_root = host.state_root
        def on_mount(self): self.push_screen(screen)
        def action_refresh(self): pass
    async def exercise():
        async with TestApp().run_test(size=(150, 50)) as pilot:
            await pilot.pause()
            choices = screen.query_one("#gpu-choices", SelectionList)
            choices.deselect_all()
            choices.select(B)
            await pilot.click("#gpu-apply")
            await pilot.pause()
            assert draft["pending"] == requests[0] and choices.disabled
            assert host.resource_controls.status("run")["gpu_uuids"] == [B]
            await pilot.pause(0.6)  # Let Textual's button activation animation finish.
            await pilot.click("#gpu-apply")
            await pilot.pause()
            assert requests[0] == requests[1] and not draft and not choices.disabled
            assert len(host.resource_controls.history("run")) == 1
    asyncio.run(exercise())
