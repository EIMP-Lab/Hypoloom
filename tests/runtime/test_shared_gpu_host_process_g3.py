"""Real Host death, held child, pool RPC, and recovery; never GPU execution.

The isolated Host crashes after activating its GPU-shaped reservation but before
Runner releases the real child. Inventory and subsequent workload results are
synthetic. No bwrap GPU mount or CUDA program is ever executed.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import signal
import sqlite3
import time
from pathlib import Path

from hypoloom.compute_resources import (
    ComputeCapabilities,
    GpuCapability,
    GpuRuntimeObservation,
    LocalComputeResources,
)
from hypoloom.execution_workspace import ExecutionWorkspace
from hypoloom.oracle import audit_gpu_pool
from hypoloom.runner import LocalRunner, ProcessRequest, process_group_exists
from hypoloom.workflow_host import WorkflowHost
from test_gpu_host_recovery import _DeterministicRunner, _output_binding
from test_shared_gpu_closure import _clean, _finish, _pool_rows
from test_shared_gpu_dispatch import GPU_A, GPU_B, create, specs, until
from test_shared_gpu_dispatch import environment as environment


def _crashing_host(root: Path, runtime: Path, socket: Path) -> None:
    # Spawn does not inherit the parent fixture's sandbox patch or server thread.
    # The only real child remains held before exec, so no synthetic GPU bindings
    # are passed to bubblewrap or a CUDA runtime.
    ExecutionWorkspace._check_sandbox = lambda self: None
    inventory = ComputeCapabilities(
        gpus=tuple(
            GpuCapability(
                index=index,
                uuid=uuid,
                name="synthetic",
                total_memory_mib=24000,
                free_memory_mib=20000 - index * 1000,
                driver_version="test",
            )
            for index, uuid in enumerate((GPU_A, GPU_B))
        )
    )

    def sample(uuid):
        free = {GPU_A: 18000, GPU_B: 16000}[uuid]
        return GpuRuntimeObservation(
            uuid=uuid,
            total_memory_mib=24000,
            free_memory_mib=free,
            used_memory_mib=24000 - free,
            utilization_percent=60,
        )

    resources = LocalComputeResources(
        allowed_gpu_uuids=(GPU_A, GPU_B),
        gpu_runtime_prefix=runtime,
        inventory_provider=lambda: inventory,
        observation_provider=sample,
    )
    host = WorkflowHost(
        root,
        compute_resources=resources,
        gpu_pool_socket=socket,
        gpu_pool_namespace="alpha",
        fault_point="after_pool_activation_before_exec",
    )
    host.runner = _DeterministicRunner(complete=True)
    host.initialize()
    crash = host._crash

    def observe_then_crash(point):
        if point == "after_pool_activation_before_exec":
            attempt = next(
                row
                for row in host.store.list_attempts()
                if row["kind"] == "PROCESS" and row["state"] == "OPEN"
            )
            pgid = attempt["process_group_id"]
            assert isinstance(pgid, int) and process_group_exists(pgid)
            assert pgid != os.getpgrp()
            key = host.shared_gpu.key(
                "shared", attempt["execution_id"], attempt["attempt_id"]
            )
            bound = host.shared_gpu.client.request("status", {"key": key})
            assert bound["state"] == "BOUND" and bound["process_group_id"] == pgid
            assert bound["gpu_uuids"] == [GPU_A]
        crash(point)

    host._crash = observe_then_crash
    try:
        create(host, runtime)
        until(host, lambda: bool(specs(host)))
        assert not host.running
        host.runner = LocalRunner()
        for _ in range(160):
            host.tick()
        raise AssertionError("shared GPU pre-exec Host fault was not reached")
    finally:
        host.close()


def _process_rows(root: Path):
    database = root / "state.sqlite"
    if not database.is_file():
        return ()
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        return tuple(
            dict(row)
            for row in connection.execute(
                "SELECT * FROM execution_attempts WHERE kind='PROCESS'"
            )
        )


def _wait_absent(pgid):
    deadline = time.monotonic() + 5
    while process_group_exists(pgid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not process_group_exists(pgid), f"owned held process group remains: {pgid}"


def test_shared_gpu_host_death_after_activation_recovers_new_grant_and_closes(
    environment,
):
    make, free, pool, runtime = environment
    root = runtime.parent / "alpha"
    child = multiprocessing.get_context("spawn").Process(
        target=_crashing_host,
        args=(root, runtime, runtime.parent / "pool.sock"),
        name="shared-gpu-host-preexec-crash",
    )
    child.start()
    try:
        child.join(timeout=15)
        assert child.exitcode == 86, (
            "Host did not reach its activated pre-exec crash boundary"
        )
        attempts = [row for row in _process_rows(root) if row["state"] == "OPEN"]
        assert len(attempts) == 1
        abandoned = attempts[0]
        assert abandoned["attempt_ordinal"] == 1
        assert abandoned["process_group_id"] is not None
        with sqlite3.connect(
            f"file:{root / 'state.sqlite'}?mode=ro", uri=True
        ) as connection:
            dispatch = json.loads(
                connection.execute(
                    "SELECT payload_json FROM workflow_transitions WHERE "
                    "json_extract(payload_json,'$.attempt_id')=?",
                    (abandoned["attempt_id"],),
                ).fetchone()[0]
            )
            frozen = connection.execute(
                "SELECT payload_json FROM research_facts WHERE fact_type='ExperimentPreregistered'"
            ).fetchone()[0]
            assert (
                connection.execute(
                    "SELECT count(*) FROM execution_artifacts"
                ).fetchone()[0]
                == 0
            )
        assert dispatch["resource_grant"] == {"kind": "GPU", "gpu_uuid": GPU_A}
        request = ProcessRequest.from_dict(dispatch["process_request"])
        for relative in (request.stdout_path, request.stderr_path):
            assert relative is not None
            assert (Path(request.cwd) / relative).read_bytes() == b""
        assert not (_output_binding(request) / "result.json").exists()
        rows = _pool_rows(pool)
        assert len(rows) == 1 and rows[0]["state"] == "BOUND"
        assert rows[0]["process_group_id"] == abandoned["process_group_id"]
        assert json.loads(rows[0]["grant_json"]) == [GPU_A]
        _wait_absent(abandoned["process_group_id"])

        # Only the fresh live sample changes. The restarted Host uses the same
        # versioned configuration and accepted scientific intent as its owner.
        free[GPU_A] = 1000
        host = make("alpha")
        lost = host.store.get_attempt(abandoned["attempt_id"])
        assert lost["state"] == "CLOSED" and lost["terminal_reason"] == "LOST"
        observation = json.loads(lost["observation_json"])
        assert observation["absence_proven"] is True
        assert "resource_wait_before_exec" not in observation
        assert _pool_rows(pool)[0]["state"] == "RELEASED"
        assert json.dumps(specs(host)[0]["payload"], sort_keys=True) == json.dumps(
            json.loads(frozen), sort_keys=True
        )
        _finish(host)
        _clean(host)
        attempts = host.store.list_attempts(execution_id=abandoned["execution_id"])
        assert len(attempts) == 2
        assert [
            (row["attempt_ordinal"], row["terminal_reason"]) for row in attempts
        ] == [(1, "LOST"), (2, "SUCCEEDED")]
        rows = _pool_rows(pool)
        assert len(rows) == 2 and all(row["state"] == "RELEASED" for row in rows)
        assert [json.loads(row["grant_json"]) for row in rows] == [[GPU_A], [GPU_B]]
        publications = host.store.inspect("SELECT attempt_id FROM execution_artifacts")
        assert publications and {row["attempt_id"] for row in publications} == {
            attempts[1]["attempt_id"]
        }
        assert audit_gpu_pool(pool.root)["ok"]
        assert all(
            row["process_group_id"] is None
            or not process_group_exists(row["process_group_id"])
            for row in attempts
        )
    finally:
        if child.is_alive():
            child.kill()
        child.join(timeout=3)
        assert not child.is_alive()
        # Emergency cleanup is restricted to PGIDs recorded by this fresh test
        # Host. Never signal an inferred or foreign group.
        for row in _process_rows(root):
            pgid = row["process_group_id"]
            if pgid is not None and process_group_exists(pgid):
                assert pgid > 1 and pgid != os.getpgrp()
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                _wait_absent(pgid)
