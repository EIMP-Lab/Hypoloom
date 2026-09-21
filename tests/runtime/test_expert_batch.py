import json

import pytest

from hypoloom.expert_batch import BatchHistory, batch_limits, supports_hypothesis_batches


def _fact(hypothesis_id, parent=None):
    return {"fact_type": "HypothesisProposed", "hypothesis_id": hypothesis_id,
            "payload": {"component_id": "c", "proposer_role": "r", "parent_hypothesis_id": parent}}


def _batch(cycle, chains, command=None):
    return {"command": "ACCEPT_HYPOTHESIS", "workflow_kind": "RUN", "workflow_id": "run",
            "run_id": "run", "revision": 2 * cycle - 1,
            "payload_json": json.dumps({"component_id": "c", "role": "r", "node_command_id": command or f"s{cycle}",
                "agent_attempt_id": f"a{cycle}", "hypothesis_batch": {"cycle": cycle, "chains": chains}})}


def _selection(cycle, chain, hypothesis_id, experiment_id="e1", outcome_id="o1"):
    return {"command": "ACCEPT_ADJUDICATION", "workflow_kind": "RUN", "workflow_id": "run",
            "run_id": "run", "revision": 2 * cycle,
            "payload_json": json.dumps({"component_id": "c", "role": "r", "node_command_id": f"j{cycle}",
                "agent_attempt_id": f"ja{cycle}", "adjudication": {"cycle": cycle, "batch_command_id": f"s{cycle}",
                    "candidate": {}, "pruned_by": [], "selected_tip": {"chain_id": chain, "node_id": hypothesis_id,
                        "experiment_id": experiment_id, "outcome_id": outcome_id}}})}


def test_batch_members_are_not_cycles_and_selection_is_not_last_member():
    history = BatchHistory.from_rows("run", "c", "r", [_batch(1, {"b": "hb", "a": "ha"}), _selection(1, "a", "ha")],
                                    [_fact("ha"), _fact("hb")])
    assert len(history.batches) == 1
    assert history.latest.chains == ("a", "b")
    assert history.selected_tip["node_id"] == "ha"
    assert history.hypothesis(1, "b")["hypothesis_id"] == "hb"


def test_unadjudicated_batch_has_no_selected_result():
    history = BatchHistory.from_rows("run", "c", "r", [_batch(1, {"a": "ha", "b": "hb"})], [_fact("ha"), _fact("hb")])
    assert history.selected_tip is None
    assert history.latest.selection is None


def test_new_cycle_does_not_overwrite_prior_selected_binding():
    history = BatchHistory.from_rows("run", "c", "r", [_batch(1, {"a": "ha"}), _selection(1, "a", "ha"),
        _batch(2, {"b": "h2"})], [_fact("ha"), _fact("h2", "ha")])
    assert history.selected_tip["node_id"] == "ha"
    assert history.latest.selection is None
    assert history.latest.cycle == 2


@pytest.mark.parametrize("mutate", ["duplicate", "missing_fact", "wrong_selection", "wrong_parent", "skipped_cycle"])
def test_batch_reader_rejects_incoherent_accepted_relations(mutate):
    rows = [_batch(1, {"a": "ha"})]
    facts = [_fact("ha")]
    if mutate == "duplicate": rows.append(_batch(1, {"b": "hb"}, "different")); facts.append(_fact("hb"))
    elif mutate == "missing_fact": facts = []
    elif mutate == "wrong_selection": rows.append(_selection(1, "b", "ha"))
    elif mutate == "wrong_parent": rows += [_selection(1, "a", "ha"), _batch(2, {"a": "h2"})]; facts.append(_fact("h2", "wrong"))
    else: rows.append(_batch(3, {"a": "h3"})); facts.append(_fact("h3", "ha"))
    with pytest.raises(ValueError): BatchHistory.from_rows("run", "c", "r", rows, facts)


def test_product_inner_loop_and_round_budgets_have_separate_consumers():
    document = {"budget": {"max_experiments": 20, "max_retries": 4}, "methodology": {"controls": {"product": {
        "goal": {"stop_conditions": {"max_rounds_per_expert": 5}},
        "methodology": {"inner_loop": {"max_hypotheses_per_round": 3,
            "max_iterations_per_hypothesis": 2, "repair_attempts": 1}}}}}}
    assert batch_limits(document, "r") == {"forks": 3, "parallel_chains": 3, "rounds": 5, "iterations": 2, "repairs": 1}


@pytest.mark.parametrize(("version", "supported"), [(13, False), (14, True), (15, True), (None, False)])
def test_batch_version_threshold(version, supported):
    assert supports_hypothesis_batches(version) is supported
