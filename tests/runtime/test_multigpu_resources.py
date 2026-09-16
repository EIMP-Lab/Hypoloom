"""Exact-count GPU grants use synthetic inventories only; no NVML or GPU I/O."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.compute_resources import LocalComputeResources, ResourceCapacityUnavailable, ResourceUnavailable
from hypoloom.domain import ResourceGrant, ResourceKind, ResourcePreference, fact_to_dict
from hypoloom.execution_workspace import ExecutionWorkspace, execution_environment_contract
from test_compute_resources import GPU_A, GPU_B, capabilities, gpu_runtime
from test_domain import all_facts
from test_execution_workspace import candidate_pair, mount_pairs, plan


GPU_C = replace(GPU_A, index=3, uuid="GPU-cccccccc-cccc-cccc-cccc-cccccccccccc", free_memory_mib=20000)


def _resources(tmp_path, inventory=None, *, allowed=(GPU_A.uuid, GPU_B.uuid, GPU_C.uuid)):
    return LocalComputeResources(allowed_gpu_uuids=allowed, gpu_runtime_prefix=gpu_runtime(tmp_path),
        inventory_provider=inventory or (lambda: capabilities(GPU_A, GPU_B, GPU_C)))


def test_plural_grant_roundtrip_and_legacy_fact_bytes_keep_one_representation():
    grants = (
        (ResourceGrant(kind=ResourceKind.CPU), {"kind": "CPU", "gpu_uuid": None}),
        (ResourceGrant(kind=ResourceKind.GPU, gpu_uuid=GPU_A.uuid), {"kind": "GPU", "gpu_uuid": GPU_A.uuid}),
        (ResourceGrant(kind=ResourceKind.GPU, gpu_uuids=(GPU_A.uuid, GPU_B.uuid)),
            {"kind": "GPU", "gpu_uuids": [GPU_A.uuid, GPU_B.uuid]}),
    )
    for grant, expected in grants:
        assert grant.to_dict() == expected
        assert ResourceGrant.from_dict(expected) == grant
        assert grant.device_uuids == tuple(expected.get("gpu_uuids", [expected["gpu_uuid"]] if expected.get("gpu_uuid") else []))
        fact = all_facts()[2]
        changed = replace(fact, resource_preference=ResourcePreference.AUTO, execution_contract=replace(fact.execution_contract,
            resource_preference=ResourcePreference.AUTO, resource_grant=grant))
        assert fact_to_dict(changed)["execution_contract"]["resource_grant"] == expected
        if not grant.gpu_uuids:
            assert "gpu_uuids" not in json.dumps(fact_to_dict(changed))


@pytest.mark.parametrize("raw", [
    {"kind": "GPU", "gpu_uuid": GPU_A.uuid, "gpu_uuids": [GPU_A.uuid, GPU_B.uuid]},
    {"kind": "CPU", "gpu_uuids": [GPU_A.uuid, GPU_B.uuid]},
    {"kind": "GPU", "gpu_uuids": []},
    {"kind": "GPU", "gpu_uuids": [GPU_A.uuid]},
    {"kind": "GPU", "gpu_uuids": [GPU_B.uuid, GPU_A.uuid]},
    {"kind": "GPU", "gpu_uuids": [GPU_A.uuid, GPU_A.uuid]},
    {"kind": "GPU", "gpu_uuids": [GPU_A.uuid, "not-a-uuid"]},
    {"kind": "GPU", "gpu_uuids": GPU_A.uuid},
    {"kind": "GPU", "gpu_uuid": GPU_A.uuid, "unexpected": 1},
])
def test_grant_rejects_duplicate_ambiguous_or_noncanonical_shapes(raw):
    with pytest.raises(ValueError):
        ResourceGrant.from_dict(raw)


def test_exact_count_ranks_then_freezes_canonical_uuid_set(tmp_path):
    resources = _resources(tmp_path)
    grant = resources.select(ResourcePreference.GPU_REQUIRED, resources.snapshot(),
        gpu_count=2, max_gpus_per_experiment=2)
    assert grant.device_uuids == (GPU_B.uuid, GPU_C.uuid)
    assert grant.gpu_uuid is None
    assert resources.configuration["compute_resources"] == "local-compute/5"
    # Equal free memory uses UUID rather than arbitrary inventory order.
    tied = capabilities(GPU_A, replace(GPU_B, free_memory_mib=GPU_A.free_memory_mib),
        replace(GPU_C, free_memory_mib=GPU_A.free_memory_mib))
    assert resources.select(ResourcePreference.GPU_REQUIRED, tied,
        gpu_count=2, max_gpus_per_experiment=3).device_uuids == (GPU_A.uuid, GPU_B.uuid)


def test_required_plural_preserves_count_when_capacity_or_occupancy_requires_wait(tmp_path):
    resources = _resources(tmp_path)
    snapshot = replace(resources.snapshot(), busy_gpu_uuids=(GPU_B.uuid, GPU_C.uuid))
    grant = resources.select(ResourcePreference.AUTO, snapshot, gpu_count=2,
        max_gpus_per_experiment=2, min_vram_per_experiment=24000)
    # An explicit count is required: never CPU/one, and non-busy A is preferred
    # even when below its per-device floor. No identity is changed while waiting.
    assert grant.device_uuids == (GPU_A.uuid, GPU_B.uuid)
    with pytest.raises(ResourceCapacityUnavailable, match=GPU_A.uuid):
        resources.validate_live(grant, snapshot, min_vram_per_experiment=24000)
    with pytest.raises(ResourceUnavailable, match="busy"):
        resources.validate_live(grant, snapshot, busy_gpu_uuids=(GPU_B.uuid,))
    with pytest.raises(ResourceUnavailable, match="2"):
        resources.select(ResourcePreference.GPU_REQUIRED, capabilities(GPU_A),
            gpu_count=2, max_gpus_per_experiment=2)


@pytest.mark.parametrize("count,cap,preference", [
    (True, 2, ResourcePreference.AUTO), (-1, 2, ResourcePreference.AUTO),
    (1.5, 2, ResourcePreference.AUTO), ("2", 2, ResourcePreference.AUTO),
    (2, 1, ResourcePreference.GPU_REQUIRED), (None, 0, ResourcePreference.AUTO),
    (None, True, ResourcePreference.AUTO), (None, 1.5, ResourcePreference.AUTO),
    (0, 2, ResourcePreference.GPU_REQUIRED), (2, 2, ResourcePreference.CPU),
])
def test_invalid_or_conflicting_counts_do_not_select_partial_grant(tmp_path, count, cap, preference):
    resources = _resources(tmp_path)
    with pytest.raises(ValueError):
        resources.select(preference, resources.snapshot(), gpu_count=count, max_gpus_per_experiment=cap)


def test_omitted_count_retains_auto_fallback_and_zero_never_observes_inventory():
    def forbidden():
        raise AssertionError("CPU-only path must not inspect physical GPUs")
    resources = LocalComputeResources(inventory_provider=forbidden)
    snapshot = resources.snapshot()
    assert resources.select(ResourcePreference.AUTO, snapshot).device_uuids == ()
    assert resources.select(ResourcePreference.AUTO, snapshot, gpu_count=0).kind is ResourceKind.CPU
    with pytest.raises(ResourceUnavailable):
        resources.select(ResourcePreference.AUTO, snapshot, gpu_count=2, max_gpus_per_experiment=2)


@pytest.mark.parametrize("fault", ["missing", "index", "driver", "total", "second-low", "second-busy", "unauthorized"])
def test_live_validation_checks_every_uuid_from_one_fresh_inventory(tmp_path, fault):
    live = [GPU_A, GPU_B, GPU_C]
    calls = []
    def inventory():
        calls.append(True)
        return capabilities(*live)
    resources = _resources(tmp_path, inventory)
    snapshot = resources.snapshot()
    grant = ResourceGrant(kind=ResourceKind.GPU, gpu_uuids=(GPU_A.uuid, GPU_B.uuid))
    calls.clear()
    busy = ()
    if fault == "missing": live.pop(1)
    elif fault == "index": live[1] = replace(GPU_B, index=1)
    elif fault == "driver": live[1] = replace(GPU_B, driver_version="changed")
    elif fault == "total": live[1] = replace(GPU_B, total_memory_mib=50000)
    elif fault == "second-low": live[1] = replace(GPU_B, free_memory_mib=4999)
    elif fault == "second-busy": busy = (GPU_B.uuid,)
    elif fault == "unauthorized": resources.allowed_gpu_uuids = (GPU_A.uuid, GPU_C.uuid)
    with pytest.raises(ResourceUnavailable):
        resources.validate_live(grant, snapshot, busy_gpu_uuids=busy,
            min_vram_per_experiment=4000, gpu_vram_reserve_mb=1000)
    assert len(calls) == (0 if fault == "unauthorized" else 1)


def test_workspace_mounts_exact_two_authorized_devices_without_gpu_io(tmp_path):
    resources = _resources(tmp_path)
    snapshot = resources.snapshot()
    grant = ResourceGrant(kind=ResourceKind.GPU, gpu_uuids=(GPU_A.uuid, GPU_B.uuid))
    kwargs = resources.workspace_kwargs(grant, snapshot)
    assert kwargs["fixed_environment"] == {"CUDA_DEVICE_ORDER": "PCI_BUS_ID",
        "CUDA_VISIBLE_DEVICES": GPU_A.uuid + "," + GPU_B.uuid}
    assert kwargs["device_bindings"] == (
        (Path("/dev/nvidia2"), "/dev/nvidia2"), (Path("/dev/nvidia0"), "/dev/nvidia0"),
        (Path("/dev/nvidiactl"), "/dev/nvidiactl"), (Path("/dev/nvidia-uvm"), "/dev/nvidia-uvm"))
    state = tmp_path / "state"; artifacts = state / "artifacts"; artifacts.mkdir(parents=True)
    workspace = ExecutionWorkspace(artifacts, state_root=state, **kwargs)
    request, response = candidate_pair()
    prepared = plan(workspace, request, response)
    argv = prepared.process_request.argv
    assert mount_pairs(argv, "--dev-bind") == [(str(source), target) for source, target in kwargs["device_bindings"]]
    assert all(index > argv.index("--dev") for index, value in enumerate(argv) if value == "--dev-bind")
    assert "/dev/nvidia3" not in argv
    with pytest.raises(ValueError, match="fixed environment"):
        workspace.plan(agent_request=request, agent_response=response, attempt_id="foreign-env",
            attempt_ordinal=1, execution_id="execution-2", timeout_seconds=5,
            target_env={"CUDA_VISIBLE_DEVICES": GPU_C.uuid})
    contract = execution_environment_contract("bwrap-gpu-no-network/1")
    assert "exactly one" not in contract["gpu"]
    assert "each" in contract["gpu"]
    assert execution_environment_contract("bwrap-cpu-no-network/1")["gpu"] is None
