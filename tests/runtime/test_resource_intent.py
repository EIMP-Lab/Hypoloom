"""Shared resource intent uses captured synthetic inventory; no GPU I/O."""

from dataclasses import replace

import pytest

from hypoloom import domain
from hypoloom.compute_resources import LocalComputeResources, ResourceUnavailable
from hypoloom.domain import ResourceGrant, ResourceKind, ResourcePreference, fact_to_dict
from test_compute_resources import GPU_A, GPU_B, capabilities, gpu_runtime
from test_domain import all_facts, execution_contract


def _intent(**changes):
    return domain.ResourceIntent(**{
        "kind": ResourceKind.GPU,
        "allowed_gpu_uuids": (GPU_A.uuid, GPU_B.uuid),
        "gpu_count": 1,
        "workload_memory_mib": 8192,
        "reserve_memory_mib": 4096,
        **changes,
    })


def _resources(tmp_path, *, inventory=None, allowed=(GPU_A.uuid, GPU_B.uuid)):
    def forbidden():
        raise AssertionError("intent planning must not sample live inventory")

    return LocalComputeResources(
        allowed_gpu_uuids=allowed, gpu_runtime_prefix=gpu_runtime(tmp_path),
        inventory_provider=inventory or forbidden,
    )


def test_intent_roundtrip_has_only_allowed_devices_and_explicit_memory_promise():
    intent = _intent(gpu_count=2)
    expected = {
        "kind": "GPU", "allowed_gpu_uuids": [GPU_A.uuid, GPU_B.uuid],
        "gpu_count": 2, "workload_memory_mib": 8192, "reserve_memory_mib": 4096,
    }
    assert intent.to_dict() == expected
    assert domain.ResourceIntent.from_dict(expected) == intent
    assert not hasattr(intent, "device_uuids")
    expected["allowed_gpu_uuids"].clear()
    assert intent.allowed_gpu_uuids == (GPU_A.uuid, GPU_B.uuid)


@pytest.mark.parametrize("changes", [
    {"kind": "GPU"}, {"kind": ResourceKind.CPU},
    {"allowed_gpu_uuids": []}, {"allowed_gpu_uuids": ()},
    {"allowed_gpu_uuids": (GPU_B.uuid, GPU_A.uuid)},
    {"allowed_gpu_uuids": (GPU_A.uuid, GPU_A.uuid)},
    {"allowed_gpu_uuids": ("GPU-short",)},
    {"allowed_gpu_uuids": ("MIG-GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/1/2",)},
    {"gpu_count": 0}, {"gpu_count": -1}, {"gpu_count": True},
    {"gpu_count": 1.5}, {"gpu_count": 3},
    {"workload_memory_mib": 0}, {"workload_memory_mib": -1},
    {"workload_memory_mib": True}, {"workload_memory_mib": 1.5},
    {"reserve_memory_mib": -1}, {"reserve_memory_mib": True},
    {"reserve_memory_mib": "0"},
])
def test_intent_rejects_invalid_or_ambiguous_resource_promises(changes):
    with pytest.raises(ValueError):
        _intent(**changes)


@pytest.mark.parametrize("changed", [
    lambda raw: {**raw, "gpu_uuid": GPU_A.uuid},
    lambda raw: {key: value for key, value in raw.items() if key != "kind"},
    lambda raw: {key: value for key, value in raw.items() if key != "reserve_memory_mib"},
    lambda raw: {**raw, "allowed_gpu_uuids": (GPU_A.uuid, GPU_B.uuid)},
    lambda raw: {**raw, "allowed_gpu_uuids": GPU_A.uuid},
    lambda raw: None,
])
def test_intent_wire_shape_is_exact(changed):
    with pytest.raises(ValueError):
        domain.ResourceIntent.from_dict(changed(_intent().to_dict()))


def test_execution_contract_requires_exactly_one_typed_grant_or_intent():
    old = execution_contract()
    gpu = replace(old, resource_preference=ResourcePreference.AUTO,
        resource_grant=None, resource_intent=_intent())
    assert gpu.resource_grant is None
    assert gpu.resource_intent == _intent()
    assert replace(gpu, resource_preference=ResourcePreference.GPU_REQUIRED).resource_intent == _intent()
    for changes in (
        {"resource_grant": None}, {"resource_intent": _intent()},
        {"resource_grant": None, "resource_intent": _intent()},
        {"resource_grant": None, "resource_intent": _intent().to_dict()},
        {"resource_grant": "CPU"},
    ):
        with pytest.raises(ValueError):
            replace(old, **changes)


@pytest.mark.parametrize("grant", [
    ResourceGrant(kind=ResourceKind.CPU),
    ResourceGrant(kind=ResourceKind.GPU, gpu_uuid=GPU_A.uuid),
    ResourceGrant(kind=ResourceKind.GPU, gpu_uuids=(GPU_A.uuid, GPU_B.uuid)),
])
def test_legacy_fact_contract_has_no_new_null_field(grant):
    fact = all_facts()[2]
    changed = replace(fact, resource_preference=ResourcePreference.AUTO,
        execution_contract=replace(fact.execution_contract,
            resource_preference=ResourcePreference.AUTO, resource_grant=grant))
    contract = fact_to_dict(changed)["execution_contract"]
    assert set(contract) == {
        "source_agent_attempt_id", "source_agent_output_digest", "argv", "environment",
        "data_splits", "sandbox_profile", "timeout_seconds", "resource_preference",
        "resource_grant", "source_submit_command_id",
    }
    assert contract["resource_grant"] == grant.to_dict()
    assert contract["source_submit_command_id"] is None


def test_intent_fact_omits_grant_without_claiming_any_actual_device():
    fact = all_facts()[2]
    changed = replace(fact, resource_preference=ResourcePreference.AUTO,
        execution_contract=replace(fact.execution_contract,
            resource_preference=ResourcePreference.AUTO, resource_grant=None, resource_intent=_intent()))
    contract = fact_to_dict(changed)["execution_contract"]
    assert "resource_grant" not in contract
    assert contract["resource_intent"] == _intent().to_dict()
    assert contract["source_submit_command_id"] is None


@pytest.mark.parametrize("preference", [ResourcePreference.AUTO, ResourcePreference.GPU_REQUIRED])
def test_intent_uses_full_authorized_inventory_despite_busy_and_low_free_memory(tmp_path, preference):
    resources = _resources(tmp_path)
    snapshot = replace(capabilities(replace(GPU_A, free_memory_mib=0),
        replace(GPU_B, free_memory_mib=1)), busy_gpu_uuids=(GPU_A.uuid, GPU_B.uuid))
    intent = resources.plan_intent(preference, snapshot,
        min_vram_per_experiment=8192, gpu_vram_reserve_mb=4096)
    assert intent == _intent()
    assert resources.plan_intent(preference, capabilities(GPU_A, GPU_B),
        min_vram_per_experiment=8192, gpu_vram_reserve_mb=4096) == intent


def test_intent_filters_authorization_and_records_exact_count_without_selecting(tmp_path):
    resources = _resources(tmp_path, allowed=(GPU_B.uuid,))
    intent = resources.plan_intent(ResourcePreference.AUTO, capabilities(GPU_A, GPU_B),
        min_vram_per_experiment=4096)
    assert intent.allowed_gpu_uuids == (GPU_B.uuid,)
    assert intent.gpu_count == 1
    with pytest.raises(ResourceUnavailable, match="2"):
        resources.plan_intent(ResourcePreference.AUTO, capabilities(GPU_A, GPU_B),
            gpu_count=2, max_gpus_per_experiment=2, min_vram_per_experiment=4096)


def test_plural_intent_keeps_all_authorized_alternatives(tmp_path):
    resources = _resources(tmp_path)
    intent = resources.plan_intent(ResourcePreference.AUTO, capabilities(GPU_A, GPU_B),
        gpu_count=2, max_gpus_per_experiment=2, min_vram_per_experiment=8192)
    assert intent == _intent(gpu_count=2, reserve_memory_mib=0)


@pytest.mark.parametrize("count,cap,preference", [
    (True, 2, ResourcePreference.AUTO), (-1, 2, ResourcePreference.AUTO),
    (1.5, 2, ResourcePreference.AUTO), ("2", 2, ResourcePreference.AUTO),
    (2, 1, ResourcePreference.GPU_REQUIRED), (None, 0, ResourcePreference.AUTO),
    (None, True, ResourcePreference.AUTO), (None, 1.5, ResourcePreference.AUTO),
    (0, 2, ResourcePreference.GPU_REQUIRED), (2, 2, ResourcePreference.CPU),
])
def test_intent_count_conflicts_use_existing_selection_rules(tmp_path, count, cap, preference):
    with pytest.raises(ValueError):
        _resources(tmp_path).plan_intent(preference, capabilities(GPU_A, GPU_B),
            gpu_count=count, max_gpus_per_experiment=cap, min_vram_per_experiment=1)


@pytest.mark.parametrize("minimum,reserve", [(0, 0), (-1, 1), (True, 0), (1.5, 0), (1, -1), (1, True)])
def test_gpu_intent_requires_an_explicit_positive_workload_floor(tmp_path, minimum, reserve):
    with pytest.raises(ValueError):
        _resources(tmp_path).plan_intent(ResourcePreference.AUTO, capabilities(GPU_A),
            min_vram_per_experiment=minimum, gpu_vram_reserve_mb=reserve)


def test_cpu_and_no_inventory_auto_need_no_workload_floor_or_gpu_query():
    def forbidden():
        raise AssertionError("CPU planning must not query GPU inventory")

    resources = LocalComputeResources(inventory_provider=forbidden)
    cpu = ResourceGrant(kind=ResourceKind.CPU)
    empty = capabilities()
    assert resources.plan_intent(ResourcePreference.CPU, empty) == cpu
    assert resources.plan_intent(ResourcePreference.AUTO, empty) == cpu
    assert resources.plan_intent(ResourcePreference.AUTO, empty, gpu_count=0) == cpu
    with pytest.raises(ResourceUnavailable):
        resources.plan_intent(ResourcePreference.GPU_REQUIRED, empty, min_vram_per_experiment=1)
    resources.validate_identity(cpu, empty)


@pytest.mark.parametrize("fault", [None, "missing", "index", "driver", "total", "unauthorized"])
def test_grant_identity_rechecks_every_device_but_leaves_capacity_to_pool(tmp_path, fault):
    actual = [replace(GPU_A, free_memory_mib=0), replace(GPU_B, free_memory_mib=0)]
    calls = []

    def inventory():
        calls.append(True)
        return capabilities(*actual)

    resources = _resources(tmp_path, inventory=inventory)
    snapshot = replace(capabilities(GPU_A, GPU_B), busy_gpu_uuids=(GPU_A.uuid, GPU_B.uuid))
    grant = ResourceGrant(kind=ResourceKind.GPU, gpu_uuids=(GPU_A.uuid, GPU_B.uuid))
    if fault == "missing": actual.pop()
    elif fault == "index": actual[1] = replace(actual[1], index=1)
    elif fault == "driver": actual[1] = replace(actual[1], driver_version="changed")
    elif fault == "total": actual[1] = replace(actual[1], total_memory_mib=50000)
    elif fault == "unauthorized": resources.allowed_gpu_uuids = (GPU_A.uuid,)
    if fault is None:
        resources.validate_identity(grant, snapshot)
    else:
        with pytest.raises(ResourceUnavailable):
            resources.validate_identity(grant, snapshot)
    assert len(calls) == (0 if fault == "unauthorized" else 1)
