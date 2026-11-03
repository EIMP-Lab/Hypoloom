"""Cold workloads share capacity; measurement confidence only changes budgets."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from hypoloom.domain import ResourceEstimate
from hypoloom.gpu_pool import CapacitySample
from hypoloom.oracle import audit_gpu_pool
from hypoloom.shared_gpu_host import SharedGpuHost
from test_gpu_pool import A, B, UID, World, device, request
from test_resource_estimate import attempt, MIB
from hypoloom.resource_experience import summarize_resource_measurements


def budget(version=30, records=()):
    host = SimpleNamespace(_research_contract=lambda run: SimpleNamespace(document={
        "budget": {"initial_vram_per_experiment": 18432}}))
    owner = SimpleNamespace(host=host, measurements=lambda *args: list(records))
    source = SimpleNamespace(run_id="r", role="expert", component_id="c",
        method_family_id="m", schema_version=version)
    estimate = ResourceEstimate(workload={"program": "test"}, peak_memory_mib=18432,
        basis="conservative workload estimate", measurement_attempt_ids=tuple(
            row["attempt_id"] for row in records))
    return SharedGpuHost.budget_plan(owner, source, {"resource_estimate": estimate.to_dict()}, 18432)


@pytest.mark.parametrize("partial", [False, True])
def test_new_workloads_can_share_an_80_gib_card_without_complete_history(tmp_path, partial):
    records = []
    if partial:
        source = attempt("partial", peak_bytes=4000*MIB)
        source["workload"] = {"program": "test"}
        source["resource_usage"][0]["owned_memory"]["unknown_sample_count"] = 1
        records = summarize_resource_measurements([source])
    plan = budget(records=records)
    assert plan["sharing"] == "shared"
    assert plan["resolved_workload_memory_mib"] == 18432
    assert plan["headroom_percent"] == 25
    world = World()
    world.sample = CapacitySample(tuple(replace(device(uuid), total_memory_mib=81920,
        free_memory_mib=80000, used_memory_mib=1920) for uuid in (A, B)))
    pool = world.pool(tmp_path)
    for index in range(4):
        result = pool.handle(UID, "reserve", {**request(run=f"run{index}", allowed=(A,),
            budget=plan["resolved_workload_memory_mib"], reserve=960), "sharing": plan["sharing"]})
        assert result["state"] == "RESERVED"
    waiting = pool.handle(UID, "reserve", {**request(run="fifth", allowed=(A,),
        budget=18432, reserve=960), "sharing": plan["sharing"]})
    assert waiting["state"] == "QUEUED"
    assert waiting["reason"] != "exclusive_wait"
    assert audit_gpu_pool(pool.root)["ok"]


@pytest.mark.parametrize("version", [27, 28, 29])
def test_retained_admission_still_uses_its_original_policy(version):
    assert budget(version)["sharing"] == "exclusive"


def test_partial_peak_can_raise_a_shared_budget_but_never_calibrate_it():
    source = attempt("over-budget", peak_bytes=24000*MIB)
    source["workload"] = {"program": "test"}
    source["resource_usage"][0]["owned_memory"]["unknown_sample_count"] = 1
    records = summarize_resource_measurements([source])
    current = budget(records=records)
    assert current["sharing"] == "shared" and current["headroom_percent"] == 25
    assert current["calibrated_measurements"] == 0
    assert current["resolved_workload_memory_mib"] >= 30000
    assert budget(29, records)["resolved_workload_memory_mib"] == 18432
