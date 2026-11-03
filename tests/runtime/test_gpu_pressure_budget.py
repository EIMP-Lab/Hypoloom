"""Under-budget work is not sacrificed merely because freeing it would help."""
import pytest

from hypoloom.compute_resources import gpu_pressure_attribution


@pytest.mark.parametrize("owned,expected", [(4194, "within_budget"), (18432, "within_budget"),
    (18433, "self"), (None, "unknown")])
def test_pressure_decision_uses_the_actual_admitted_budget(owned, expected):
    assert gpu_pressure_attribution(latest_free_memory_mib=47, owned_latest_memory_mib=owned,
        reserve_memory_mib=960, workload_memory_mib=18432) == expected


def test_legacy_counterfactual_and_unknown_budget_are_preserved():
    assert gpu_pressure_attribution(latest_free_memory_mib=47, owned_latest_memory_mib=4194,
        reserve_memory_mib=960) == "self"
    assert gpu_pressure_attribution(latest_free_memory_mib=47, owned_latest_memory_mib=400,
        reserve_memory_mib=960) == "external"
