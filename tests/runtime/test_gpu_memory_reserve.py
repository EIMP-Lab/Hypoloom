"""Workload-relative device reserves; no physical GPU or research state."""
import pytest

from hypoloom.agent_budget import validate_new_run_budget
from hypoloom.compute_resources import gpu_memory_reserve_mib
from hypoloom.profile_v4 import validate_gpu_pool_budget
from test_agent_budget_accounting import _dollar_budget
from test_component_profile import REPOSITORY, _copy_profile, _mutate_methodology
from hypoloom.profile_admission import admit_profile


@pytest.mark.parametrize("budget,workload,expected", [
    ({}, 19456, 0),
    ({"gpu_vram_reserve_mb": 4096}, 19456, 4096),
    ({"gpu_vram_reserve_mb": 1001, "gpu_vram_reserve_percent": 0}, 19456, 1001),
    ({"gpu_vram_reserve_percent": 5}, 1024, 512),
    ({"gpu_vram_reserve_percent": 5}, 10240, 512),
    ({"gpu_vram_reserve_percent": 5}, 10241, 576),
    ({"gpu_vram_reserve_mb": 512, "gpu_vram_reserve_percent": 5}, 12288, 640),
    ({"gpu_vram_reserve_mb": 512, "gpu_vram_reserve_percent": 5}, 19456, 1024),
    ({"gpu_vram_reserve_mb": 4096, "gpu_vram_reserve_percent": 5}, 19456, 4096),
])
def test_legacy_fixed_and_proportional_reserves(budget, workload, expected):
    assert gpu_memory_reserve_mib(budget, workload) == expected


@pytest.mark.parametrize("percent", [None, True, -1, 101, 5.0, "5", [], {}])
def test_invalid_percent_fails_both_new_run_and_profile_admission(tmp_path, percent):
    budget = {"agent_invocation": _dollar_budget().to_dict(),
        "gpu_vram_reserve_mb": 512, "gpu_vram_reserve_percent": percent}
    with pytest.raises(ValueError, match="gpu_vram_reserve_percent"):
        validate_new_run_budget({"roles": [], "budget": budget}, _dollar_budget())
    profile = _copy_profile(REPOSITORY / "tests/fixtures/profiles/component-demo", tmp_path / "profile")
    _mutate_methodology(profile, lambda doc: doc["budget"].update(gpu_vram_reserve_percent=percent))
    report = admit_profile(profile, run_id="reserve")
    assert not report.admitted
    assert any("gpu_vram_reserve_percent" in item.message for item in report.blockers)


def test_proportional_config_is_preserved_by_profile_admission(tmp_path):
    profile = _copy_profile(REPOSITORY / "tests/fixtures/profiles/component-demo", tmp_path / "profile")
    _mutate_methodology(profile, lambda doc: doc["budget"].update(
        min_vram_per_experiment=8192, gpu_vram_reserve_mb=512, gpu_vram_reserve_percent=5))
    report = admit_profile(profile, run_id="reserve")
    assert report.admitted, report.to_dict()
    budget = report.create_run_payload["research_contract"]["budget"]
    assert budget["gpu_vram_reserve_percent"] == 5 and budget["gpu_vram_reserve_mb"] == 512
    validate_new_run_budget({"roles": [], "budget": {**budget,
        "agent_invocation": _dollar_budget().to_dict()}}, _dollar_budget())


@pytest.mark.parametrize("pooled", [False, True])
def test_gpu_activation_needs_workload_floor_when_using_percentage(pooled):
    with pytest.raises(ValueError, match="positive min_vram_per_experiment"):
        validate_gpu_pool_budget({"budget": {"gpu_vram_reserve_percent": 5}}, pooled=pooled)
    validate_gpu_pool_budget({"budget": {"gpu_vram_reserve_percent": 5,
        "min_vram_per_experiment": 8192}}, pooled=pooled)


def test_oracle_recomputes_reserve_from_learned_workload_and_rejects_wrong_value():
    from test_shared_gpu_learning_oracle import learning_case, audit, BUDGET
    case, context = learning_case()
    budget = {**BUDGET, "gpu_vram_reserve_percent": 50, "gpu_vram_reserve_mb": 512}
    # Prior submitted 1200 -> reserve 640; measured current 2560 -> reserve 1280.
    context["fact_payloads"]["prior-fact"]["execution_contract"]["resource_intent"]["reserve_memory_mib"] = 640
    case[1]["resource_intent"]["reserve_memory_mib"] = 1280
    resource, issues = audit(case, context, budget=budget)
    assert not issues and resource["source_valid"], issues
    assert resource["reserve_memory_mib"] == 1280
    case[1]["resource_intent"]["reserve_memory_mib"] = 640
    _, issues = audit(case, context, budget=budget)
    assert "resource_intent_mismatch" in {issue.code for issue in issues}


def test_proportional_requests_keep_fifo_and_higher_pool_floor(tmp_path):
    from hypoloom.gpu_pool import CapacitySample, GpuPool
    from test_gpu_pool import A, UID, World, action, device, reserve
    world = World()
    world.sample = CapacitySample((device(A, free=20228, total=24576),))
    pool = GpuPool(tmp_path / "fifo", members={UID: (A,)}, reserve_memory_mib=512,
        sampler=world.sample_now)
    policy = {"gpu_vram_reserve_percent": 5, "gpu_vram_reserve_mb": 512}
    large = gpu_memory_reserve_mib(policy, 19456)
    small = gpu_memory_reserve_mib(policy, 12288)
    assert reserve(pool, budget=19456, reserve=large)["state"] == "QUEUED"
    assert reserve(pool, attempt="second", budget=12288, reserve=small)["state"] == "QUEUED"
    world.sample = CapacitySample((device(A, free=20480, total=24576),))
    assert action(pool, "status")["state"] == "RESERVED"
    assert action(pool, "status", attempt="second")["state"] == "QUEUED"
    assert action(pool, "release")["state"] == "RELEASED"
    assert action(pool, "status", attempt="second")["state"] == "RESERVED"
    assert action(pool, "release", attempt="second")["state"] == "RELEASED"
    high_floor = GpuPool(tmp_path / "high", members={UID: (A,)}, reserve_memory_mib=4096,
        sampler=world.sample_now)
    assert reserve(high_floor, budget=19456, reserve=large)["state"] == "QUEUED"
