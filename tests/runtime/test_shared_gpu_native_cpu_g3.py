"""Pool-configured native CLI/MCP/CPU closure; no GPU/CUDA qualification.

The existing native scenario supplies a local scripted model provider and real
bubblewrap CPU execution. The pool has one synthetic member and receives no
workload reservation; it is an actual authenticated Unix socket/SQLite service.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from hypoloom import compute_resources
from hypoloom.compute_resources import GpuRuntimeObservation
from hypoloom.gpu_pool import CapacitySample, GpuPool
from hypoloom.gpu_pool_client import PoolClient
from hypoloom.gpu_pool_server import PoolServer
from hypoloom.oracle import audit_gpu_pool

import test_full_cc_host_g3 as native


def test_pool_configured_host_closes_native_cli_mcp_cpu_and_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    synthetic_uuid = "GPU-00000000-0000-0000-0000-000000000001"
    sample = CapacitySample(devices=(GpuRuntimeObservation(
        uuid=synthetic_uuid, total_memory_mib=16000, free_memory_mib=16000,
        used_memory_mib=0, utilization_percent=0,
    ),))
    pool = GpuPool(tmp_path / "pool", members={os.getuid(): (synthetic_uuid,)},
        reserve_memory_mib=0, sampler=lambda: sample)
    socket_path = tmp_path / "p.sock"
    configuration = {"socket_path": str(socket_path), "namespace": "native-cpu-root",
                     "server_uid": os.getuid()}
    original_host = native.WorkflowHost
    hosts = []

    def configured_host(*args, **kwargs):
        host = original_host(*args, **kwargs, gpu_pool_socket=socket_path,
            gpu_pool_namespace=configuration["namespace"], gpu_pool_server_uid=os.getuid())
        hosts.append(host)
        assert host.version.research_contract_version == "hypoloom-research-v12"
        assert host.configuration["gpu_pool"] == configuration
        assert host.compute_resources.allowed_gpu_uuids == ()
        return host

    def forbidden_gpu_query(*args, **kwargs):
        raise AssertionError("the shared-pool CPU rehearsal must not query a physical GPU")

    monkeypatch.setattr(native, "WorkflowHost", configured_host)
    monkeypatch.setattr(compute_resources, "_nvml_inventory", forbidden_gpu_query)
    monkeypatch.setattr(compute_resources, "_nvml_runtime_observation", forbidden_gpu_query)
    trial = tmp_path / "trial"
    trial.mkdir()
    with PoolServer(pool, socket_path) as server:
        client = PoolClient(socket_path, expected_server_uid=os.getuid())
        assert client.request("snapshot", {})["devices"][0]["active_commitments"] == 0
        native._exercise_full_cc_host(trial)
        assert len(hosts) == 1
        database = hosts[0].store.database_path
        runs = native._rows(database, "SELECT research_contract_version FROM workflow_runs")
        assert runs == [{"research_contract_version": "hypoloom-research-v12"}]
        created = native._rows(database,
            "SELECT payload_json FROM workflow_transitions WHERE workflow_kind='RUN' AND revision=1")
        assert json.loads(created[0]["payload_json"])["gpu_pool"] == configuration
        preregistered = native._rows(database,
            "SELECT payload_json FROM research_facts WHERE fact_type='ExperimentPreregistered'")
        assert len(preregistered) == 2
        for row in preregistered:
            contract = json.loads(row["payload_json"])["execution_contract"]
            assert contract["resource_grant"] == {"kind": "CPU", "gpu_uuid": None}
            assert "resource_intent" not in contract
        dispatches = native._rows(database,
            "SELECT payload_json FROM workflow_transitions WHERE json_type(payload_json,'$.process_request')='object'")
        assert len(dispatches) == 2
        for row in dispatches:
            dispatch = json.loads(row["payload_json"])
            request = dispatch["process_request"]
            assert any(Path(arg).name == "bwrap" for arg in request["argv"])
            assert not any("/dev/nvidia" in arg for arg in request["argv"])
            assert "gpu_pool_key" not in dispatch
        assert native._rows(pool.database_path, "SELECT * FROM requests") == []
        assert client.request("snapshot", {})["devices"][0]["active_commitments"] == 0
        assert audit_gpu_pool(pool.root)["ok"]
    assert not socket_path.exists()
    assert server.thread is not None and not server.thread.is_alive()
