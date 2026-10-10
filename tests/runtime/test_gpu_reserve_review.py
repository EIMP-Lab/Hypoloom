import pytest

from hypoloom.bootstrap_review import gpu_reserve_summary
from hypoloom.compute_resources import gpu_memory_reserve_mib


@pytest.mark.parametrize("language", ["en", "zh-CN"])
@pytest.mark.parametrize("budget,reserve", [
    ({"gpu_vram_reserve_mb": 4096}, 4096),
    ({"gpu_vram_reserve_mb": 4096, "gpu_vram_reserve_percent": 5}, 4096),
    ({"gpu_vram_reserve_mb": 512, "gpu_vram_reserve_percent": 5}, 640),
])
@pytest.mark.parametrize("pool", [None, {"reserve_mib": 4096}, {"reserve_mib": None},
    {"device_reserves": [{"pool_reserve_memory_mib": 512}, {"pool_reserve_memory_mib": 4096}]}])
def test_gpu_reserve_explanation(language, budget, reserve, pool):
    memory = {"initial_workload_mib": 12288, "initial_reserve_mib": gpu_memory_reserve_mib(budget, 12288),
        "reserve_floor_mib": gpu_memory_reserve_mib(budget, 0), "reserve_percent": budget.get("gpu_vram_reserve_percent", 0)}
    gpu = {"memory_budget": memory, **({"pool": pool} if pool is not None else {})}
    before = repr(gpu)
    text = "\n".join(gpu_reserve_summary(gpu, language))
    assert f"{reserve} MiB" in text and "12288 MiB" in text
    if not memory["reserve_percent"]:
        assert ("未启用百分比" if language == "zh-CN" else "percentage mode is disabled") in text
    if pool == {"reserve_mib": None}:
        assert ("尚未完整确认" if language == "zh-CN" else "not fully known") in text
        assert ("初始准入保留量" if language == "zh-CN" else "initial admission reserve") not in text
    if pool and pool != {"reserve_mib": None}:
        assert "16384 MiB" in text
        assert ("至少" if language == "zh-CN" else "at least") in text
        assert ("在途请求可能要求更高保留量" if language == "zh-CN" else "Active requests may require a larger reserve") in text
        if reserve < 4096:
            assert ("池须空闲" if language == "zh-CN" else "when the pool is idle") in text
    assert repr(gpu) == before


def test_cpu_review_does_not_invent_gpu_policy():
    assert gpu_reserve_summary(None) == gpu_reserve_summary({}) == []
