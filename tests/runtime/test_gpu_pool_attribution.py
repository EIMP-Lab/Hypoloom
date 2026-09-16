"""Service-owned sampling uses only transactionally BOUND process roots."""
from hypoloom.gpu_pool import GpuPool
from tests.runtime.test_gpu_pool import A, UID, World, action, reserve


def test_sampler_receives_bound_roots_not_client_resident_claims(tmp_path):
    world = World()
    calls = []

    def attribution(devices, roots):
        calls.append((devices, dict(roots)))
        return {(key, A): 3000 for key in roots}

    pool = GpuPool(tmp_path, members={UID: (A,)}, reserve_memory_mib=1000,
        sampler=world.sample_now, process_present=lambda pgid: True,
        attribution_sampler=attribution)
    assert reserve(pool, budget=4000)["state"] == "RESERVED"
    assert calls[-1][1] == {}
    action(pool, "activate", process_group_id=45678)
    result = pool.handle(UID, "snapshot", {})
    assert list(calls[-1][1].values()) == [45678]
    assert result["devices"][0]["outstanding_memory_mib"] == 1000
    assert result["devices"][0]["unknown_attribution_commitments"] == 0


def test_attribution_failure_never_turns_into_capacity_credit(tmp_path):
    world = World()

    def unavailable(devices, roots):
        raise PermissionError("numeric process metadata unavailable")

    pool = GpuPool(tmp_path, members={UID: (A,)}, reserve_memory_mib=1000,
        sampler=world.sample_now, process_present=lambda pgid: True,
        attribution_sampler=unavailable)
    assert reserve(pool, budget=4000)["state"] == "RESERVED"
    action(pool, "activate", process_group_id=45678)
    capacity = pool.handle(UID, "snapshot", {})["devices"][0]
    assert capacity["outstanding_memory_mib"] == 4000
    assert capacity["unknown_attribution_commitments"] == 1
