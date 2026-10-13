import copy

import pytest

from hypoloom.contract_normalization import EXPERT_LOOP_RESEARCH_LOOP
from hypoloom.observer_revisions import activity_warning, revision_activities


def document(limit=9):
    return {"methodology": {"controls": {"research_loop": EXPERT_LOOP_RESEARCH_LOOP,
        "product": {"methodology": {"inner_loop": {"repair_attempts": limit}}}}}}


def creation(eid, ordinal, *, chain="a", iteration=1, cycle=1, repair=None, preflight=None):
    payload = {"component_id": "primary.a", "role": "expert", "expert_cycle": cycle,
        "chain": chain, "chain_iteration": iteration, "experiment_ordinal": ordinal,
        "execution_node_command_id": f"execute-{cycle}-{chain}-{iteration}"}
    if iteration > 1:
        payload["iteration_of_experiment_id"] = f"previous-{chain}-{iteration - 1}"
    if repair:
        payload["revision_of_outcome_id"] = repair
    if preflight:
        payload["preflight_repair_of_experiment_id"] = preflight
    return {"workflow_kind": "EXPERIMENT", "revision": 1, "workflow_id": eid, "payload": payload}


def project(rows, *, outcomes=(), version=29, state="ACTIVE", limit=9):
    return revision_activities(rows, outcomes, document(limit), agent_aci_version=version,
        run_state=state)["primary.a"]


def outcome(eid, value):
    return {"experiment_id": eid, "payload": {"outcome": value}}


def test_parallel_science_above_legacy_threshold_is_not_execution_repair():
    rows = [creation(f"{chain}-{iteration}", n * 6 + iteration, chain=chain, iteration=iteration)
        for n, chain in enumerate("abcd") for iteration in range(1, 7)]
    before = copy.deepcopy(rows)
    activity = project(rows)
    assert activity["initial_experiments"] == 4
    assert activity["scientific_iterations"] == 20
    assert activity["operational_repairs"] == 0
    assert activity_warning(activity) is None
    assert all(s["repairs"] == 0 and s["repair_limit"] == 9 for s in activity["scopes"])
    assert rows == before


def test_repairs_inherit_scientific_predecessor_without_double_counting():
    rows = [creation("a1", 1), creation("a2", 2, iteration=2),
        creation("a2fix", 3, iteration=2, repair="out-a2"),
        creation("a2fix2", 4, iteration=2, preflight="a2fix"), creation("b1", 5, chain="b")]
    activity = project(rows + [rows[2]])
    assert (activity["initial_experiments"], activity["scientific_iterations"], activity["operational_repairs"]) == (2, 1, 2)
    assert [s["repairs"] for s in activity["scopes"]] == [0, 2, 0]
    assert project(list(reversed(rows))) == activity
    assert activity_warning(activity) is None


def test_latest_cycle_and_each_execution_node_have_separate_allowances():
    rows = [creation("old", 1), creation("old-fix", 2, repair="old-out")]
    for n, chain in enumerate("ab"):
        rows += [creation(chain, n * 2 + 3, cycle=2, chain=chain),
            creation(chain + "fix", n * 2 + 4, cycle=2, chain=chain, repair="out-" + chain)]
    activity = project(rows, limit=2, outcomes=[outcome("afix", "EXECUTION_FAILURE")])
    assert activity["cycle"] == 2 and activity["operational_repairs"] == 2
    assert activity_warning(activity) is None  # Two nodes used one each, not two on one.


@pytest.mark.parametrize("state", ["ACTIVE", "STOPPING", "CANCELLED", "COMPLETED"])
def test_allowance_used_is_not_failure_and_recovery_clears_warning(state):
    rows = [creation("first", 1), creation("repair", 2, repair="out-first")]
    pending = project(rows, limit=1, state=state)
    assert activity_warning(pending) is None
    failed = project(rows, limit=1, state=state, outcomes=[outcome("repair", "EXECUTION_FAILURE")])
    assert activity_warning(failed) == "allowance_used"
    assert failed["scopes"][0]["historical"] == (state != "ACTIVE")
    for accepted in ("SUCCESS", "CLEAN_REFUTATION"):
        recovered = project(rows, limit=1, state=state, outcomes=[outcome("repair", accepted)])
        assert recovered["operational_repairs"] == 1
        assert activity_warning(recovered) is None
        assert recovered["scopes"][0]["recovered"]


def test_previous_iteration_warning_is_historical():
    rows = [creation("a", 1), creation("fix", 2, repair="a-out"), creation("next", 3, iteration=2)]
    activity = project(rows, limit=1, outcomes=[outcome("fix", "EXECUTION_FAILURE")])
    assert activity["scopes"][0]["historical"]
    assert not activity["scopes"][1]["historical"]


@pytest.mark.parametrize("missing", ["expert_cycle", "chain", "chain_iteration", "execution_node_command_id", "role", "experiment_ordinal"])
def test_incomplete_batch_identity_is_unknown_not_legacy_or_zero(missing):
    row = creation("a", 1)
    del row["payload"][missing]
    activity = project([row])
    assert not activity["complete"]
    assert activity["operational_repairs"] is None
    assert activity_warning(activity) is None


@pytest.mark.parametrize(("count", "warning"), [(10, None), (11, "general"), (20, "general"),
    (21, "severe"), (30, "severe"), (31, "tripped")])
def test_legacy_thresholds_are_preserved_without_batch_identity(count, warning):
    rows = [creation(str(n), n + 1) for n in range(count + 1)]
    for row in rows:
        row["payload"] = {"component_id": "primary.a", "experiment_ordinal": row["payload"]["experiment_ordinal"]}
    activity = project(rows, version=13)
    assert activity["mode"] == "legacy"
    assert activity["cycle"] == 1 and activity["operational_repairs"] == count
    assert activity_warning(activity) == warning


def test_repairable_integrity_limit_does_not_mislabel_scientific_rejection():
    activity = project([creation("a", 1)], limit=0, outcomes=[outcome("a", "INTEGRITY_REJECTED")])
    assert activity_warning(activity) is None


def test_missing_scientific_predecessor_is_not_another_initial_experiment():
    row = creation("iteration-2", 2, iteration=2)
    del row["payload"]["iteration_of_experiment_id"]
    activity = project([row])
    assert not activity["complete"] and activity["initial_experiments"] is None


def test_invalid_cycle_does_not_turn_into_current_cycle_one():
    row = creation("invalid", 1)
    row["payload"]["expert_cycle"] = True
    activity = project([row])
    assert activity["cycle"] is None and activity["operational_repairs"] is None


def test_unknown_budget_does_not_erase_observed_repair_count():
    rows = [creation("a", 1), creation("repair", 2, repair="out-a")]
    activity = project(rows, limit="unknown", outcomes=[outcome("repair", "EXECUTION_FAILURE")])
    assert activity["operational_repairs"] == 1
    assert activity["scopes"][0]["repair_limit"] is None
    assert activity_warning(activity) is None


def test_new_accepted_batch_does_not_inherit_old_cycle_warning_before_first_experiment():
    rows = [creation("a", 1), creation("repair", 2, repair="out-a"),
        {"workflow_kind": "RUN", "revision": 9, "command_type": "ACCEPT_HYPOTHESIS",
            "payload": {"component_id": "primary.a", "hypothesis_batch": {"cycle": 2}}}]
    activity = project(rows, limit=1, outcomes=[outcome("repair", "EXECUTION_FAILURE")])
    assert activity["cycle"] == 2
    assert activity["operational_repairs"] == 0 and not activity["scopes"]
    assert activity_warning(activity) is None
