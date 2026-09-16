from __future__ import annotations

import base64
import copy
import json
from dataclasses import replace

import pytest

from hypoloom import expert_loop as loops
from hypoloom.agent_aci import CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION, _hypothesis_candidate
from hypoloom.domain import ComponentResultKind, MetricDirection, MetricSpec, ScientificOutcome, fact_to_dict
from test_domain import all_facts
from test_expert_loop import completed_prefix, grounding, lineage, node_key, science_for_prefix


def test_absent_chain_keeps_old_wire_and_command_bytes_exactly():
    key = node_key()
    old = {"run_id": "run-1", "component_id": "primary.score", "expert_slot": "scientist",
           "cycle": 1, "node_kind": "LITERATURE_REVIEW", "subkind": "MAIN", "experiment_ordinal": None}
    identity = {name: value for name, value in old.items() if name != "experiment_ordinal"}
    old_command = "research-node-v1." + base64.urlsafe_b64encode(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).decode().rstrip("=")
    assert key.to_dict() == old and key.command_id == old_command
    assert replace(key, chain=None).to_dict() == old
    assert loops.ResearchNodeKey.from_dict(old) == key
    assert lineage().identity == ("run-loop", "primary.score", "scientist", 1)


@pytest.mark.parametrize("chain", ["1", "chain-a", "chain-b"])
def test_explicit_chain_has_distinct_reversible_node_and_session_scope(chain):
    key = replace(node_key(), chain=chain)
    assert key.to_dict()["chain"] == chain
    assert key.command_id != node_key().command_id
    assert loops.task_context_from_dict(loops.ResearchNodeTask(key=key).to_dict()).key == key
    assert loops.chain_session_scope(chain) != loops.chain_session_scope(None)
    assert loops.chain_session_scope("chain-a") != loops.chain_session_scope("chain-b")


@pytest.mark.parametrize("chain", ["", ".", "..", "../escape", "a/b", "a\\b", "bad chain", 1, False])
def test_chain_rejects_unsafe_identity(chain):
    with pytest.raises(ValueError, match="chain"):
        replace(node_key(), chain=chain)
    with pytest.raises(ValueError, match="chain"):
        replace(lineage(), chain=chain)


def test_chain_reduction_ignores_sibling_nodes_and_preserves_successor_identity():
    alpha, beta = replace(lineage(), chain="chain-a"), replace(lineage(), chain="chain-b")
    decision = loops.reduce_expert_lineage(loops.ExpertLineageInput(lineage=beta), completed_prefix(alpha, 9), grounding())
    assert decision.next_task.key == beta.key(loops.ResearchNodeKind.LITERATURE_REVIEW, loops.ResearchNodeSubkind.QUERY_PLAN)
    revised = loops.reduce_expert_lineage(loops.ExpertLineageInput(lineage=alpha,
        scientific_refs=science_for_prefix(9, loops.HypothesisUpdateDisposition.REVISE_NEXT_CYCLE)), completed_prefix(alpha, 9), grounding())
    assert revised.next_lineage.chain == "chain-a" and revised.next_task.key.chain == "chain-a"
    inputs = [loops.ExpertLineageInput(lineage=item) for item in (beta, lineage(), alpha)]
    assert loops.reduce_expert_lineages(inputs, (), (grounding(),)) == loops.reduce_expert_lineages(reversed(inputs), (), (grounding(),))


def test_aci13_projection_keeps_explicit_chains_separate_without_admitting_future_wire():
    alpha, beta = replace(node_key(), chain="a"), replace(node_key(), chain="b")
    commands = [{"command_id": alpha.command_id}]
    attempts = [{"command_id": alpha.command_id, "attempt_id": "attempt-a", "attempt_ordinal": 1,
                 "state": "CLOSED", "terminal_reason": "SUCCEEDED"}]
    views = loops.derive_research_node_views((beta, alpha), commands, attempts, agent_aci_version="13")
    assert [(view.key.chain, view.state.value) for view in views] == [("a", "CLOSED"), ("b", "READY")]
    assert loops.derive_research_node_views((alpha,), commands, attempts, agent_aci_version="14")
    assert loops.derive_research_node_views((alpha,), commands, attempts, agent_aci_version="15")
    assert loops.derive_research_node_views((alpha,), commands, attempts, agent_aci_version="16")
    assert loops.derive_research_node_views((alpha,), commands, attempts, agent_aci_version="17")
    assert loops.derive_research_node_views((alpha,), commands, attempts,
        agent_aci_version=str(CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION))
    assert loops.derive_research_node_views((alpha,), commands, attempts,
        agent_aci_version=str(CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION + 1)) == ()


def _proposal(chain):
    return {"chain_id": chain, "candidate": {"component_id": "primary.score", "proposer_role": "scientist",
        "method_family_id": "family", "claim": "A concrete scientific hypothesis", "assumptions": []}}


def test_hypothesis_set_reuses_existing_candidate_validator_and_stable_order():
    raw = {"schema_version": 1, "hypotheses": [_proposal("chain-b"), _proposal("chain-a")]}
    seen = []

    def validate(candidate):
        seen.append(_hypothesis_candidate(candidate))

    result = loops.admit_hypothesis_set(raw, effective_max_forks=2, validate_candidate=validate)
    assert len(seen) == 2 and result.chains == ("chain-a", "chain-b")
    assert result.to_dict() == {**raw, "hypotheses": list(reversed(raw["hypotheses"]))}
    broken = copy.deepcopy(raw)
    broken["hypotheses"][0]["candidate"]["unknown_science_field"] = True
    with pytest.raises(ValueError):
        loops.admit_hypothesis_set(broken, effective_max_forks=2, validate_candidate=validate)


@pytest.mark.parametrize("hypotheses,limit", [([], 2), ([_proposal("a"), _proposal("b")], 1),
    ([_proposal("a"), _proposal("a")], 2), ([_proposal("../unsafe")], 1), ([_proposal("a")], False)])
def test_hypothesis_set_rejects_empty_overbudget_duplicate_or_unsafe_batch(hypotheses, limit):
    with pytest.raises(ValueError):
        loops.admit_hypothesis_set({"schema_version": 1, "hypotheses": hypotheses}, effective_max_forks=limit,
                                  validate_candidate=_hypothesis_candidate)


def _facts(chain, *, outcome="SUCCESS", value="0.8", metric="score", family="family"):
    common = {"run_id": "run-loop", "component_id": "primary.score", "method_family_id": family}
    hypothesis, preregistration, accepted = all_facts()[1:4]
    hypothesis = replace(hypothesis, **common, fact_id="fh-" + chain, hypothesis_id="h-" + chain,
                         proposer_role="scientist")
    preregistration = replace(preregistration, **common, fact_id="fp-" + chain, hypothesis_id="h-" + chain,
        experiment_id="e-" + chain, execution_id="x-" + chain,
        metric=MetricSpec(name=metric, direction=MetricDirection.MAXIMIZE, target="0.5"))
    accepted = replace(accepted, **common, fact_id="fo-" + chain, experiment_id="e-" + chain,
        execution_id="x-" + chain, outcome_id="o-" + chain, artifact_id="artifact-" + chain,
        outcome=ScientificOutcome(outcome), metric_value=value if outcome in {"SUCCESS", "CLEAN_REFUTATION"} else None)
    return tuple(fact_to_dict(item) for item in (hypothesis, preregistration, accepted))


def _tip(chain, **kwargs):
    hypothesis, preregistration, outcome = _facts(chain, **kwargs)
    return loops.compared_tip(chain, hypothesis=hypothesis, preregistration=preregistration, outcome=outcome)


def _adjudication(*, best="a", outcome_b="CLEAN_REFUTATION"):
    return {"schema_version": 1, "verdicts": [
        {"chain_id": "a", "node_id": "h-a", "status": "verified", "evidence": ["o-a"]},
        {"chain_id": "b", "node_id": "h-b", "status": "refuted" if outcome_b == "CLEAN_REFUTATION" else "inconclusive",
         "evidence": ["o-b"]}], "keep": ["a"], "prune": ["b"], "best_chain": best, "summary": "Select the cited terminal result"}


def _admit(raw=None, **kwargs):
    options = {"lineage": lineage(), "batch": {"a": "h-a", "b": "h-b"}, "terminal_chains": ("a", "b"),
               "terminal_tips": {"a": _tip("a"), "b": _tip("b", outcome="CLEAN_REFUTATION", value="0.2")},
               "source_attempt_id": "adjudication-attempt"}
    options.update(kwargs)
    return loops.admit_adjudication(_adjudication() if raw is None else raw, **options)


def test_adjudication_reports_exact_missing_and_disallowed_evidence():
    raw = _adjudication()
    raw["verdicts"][0]["evidence"] = ["fo-a", "invented-ref"]
    with pytest.raises(ValueError) as failure:
        _admit(raw)
    message = str(failure.value)
    assert 'missing_outcome_ids=["o-a"]' in message
    assert 'inadmissible_reference_ids=["invented-ref"]' in message
    raw["verdicts"][0]["evidence"] = ["fo-a", "o-a"]
    assert _admit(raw).selected_tip.outcome_id == "o-a"


def test_adjudication_binds_exact_tip_and_pruning_is_only_a_control_relation():
    result = _admit(debate_id="debate-actual")
    assert result.selected_tip.outcome_id == "o-a"
    assert result.to_dict() == _adjudication()
    assert result.pruned_by == ({"loser_chain": "b", "loser_hypothesis_id": "h-b", "winner_chain": "a",
        "winner_hypothesis_id": "h-a", "source_attempt_id": "adjudication-attempt", "debate_id": "debate-actual",
        "score_refs": ["o-b"]},)
    assert "assessment" not in str(result.pruned_by).lower()


@pytest.mark.parametrize("mutation", [
    lambda raw: raw["verdicts"].pop(),
    lambda raw: raw["verdicts"].append(copy.deepcopy(raw["verdicts"][0])),
    lambda raw: raw["keep"].append("b"),
    lambda raw: raw["prune"].clear(),
    lambda raw: raw.update(best_chain="b"),
    lambda raw: raw.update(best_chain="outside"),
    lambda raw: raw["verdicts"][0].update(node_id="other-hypothesis"),
    lambda raw: raw["verdicts"][0].update(evidence=["outside-outcome"]),
    lambda raw: raw["verdicts"][0].update(evidence=["o-b"]),
    lambda raw: raw["verdicts"][1].update(status="verified"),
])
def test_adjudication_rejects_incomplete_overlapping_foreign_or_false_claims(mutation):
    raw = _adjudication()
    mutation(raw)
    with pytest.raises(ValueError):
        _admit(raw)


def test_terminal_comparison_set_must_match_batch_exactly():
    for changes in ({"terminal_chains": ("a",)}, {"terminal_chains": ("a", "b", "late")},
                    {"terminal_tips": {"a": _tip("a")}}, {"terminal_tips": {"a": _tip("a"), "b": _tip("other")}}):
        with pytest.raises(ValueError):
            _admit(**changes)


def test_operational_failure_is_inconclusive_not_refutation_or_best_tip():
    tips = {"a": _tip("a"), "b": _tip("b", outcome="EXECUTION_FAILURE")}
    raw = _adjudication(outcome_b="EXECUTION_FAILURE")
    assert _admit(raw, terminal_tips=tips).selected_tip.chain_id == "a"
    with pytest.raises(ValueError):
        _admit(terminal_tips=tips)
    raw.update(best_chain="b", keep=["a", "b"], prune=[])
    with pytest.raises(ValueError, match="comparable"):
        _admit(raw, terminal_tips=tips)


def test_null_best_keeps_failed_or_unmeasured_batch_honestly():
    raw = _adjudication(best=None)
    raw.update(keep=["a", "b"], prune=[])
    for verdict in raw["verdicts"]:
        verdict.update(status="inconclusive", evidence=[])
    result = _admit(raw, terminal_tips={"a": None, "b": None})
    assert result.selected_tip is None and result.pruned_by == ()


@pytest.mark.parametrize("row,field,value", [(0, "run_id", "other"), (1, "hypothesis_id", "other"),
    (2, "experiment_id", "other"), (2, "execution_id", "other"), (2, "method_family_id", "other")])
def test_compared_tip_rejects_mismatched_raw_accepted_fact_ownership(row, field, value):
    facts = list(_facts("a"))
    facts[row][field] = value
    with pytest.raises(ValueError):
        loops.compared_tip("a", hypothesis=facts[0], preregistration=facts[1], outcome=facts[2])


def test_required_input_family_is_checked_before_selection():
    with pytest.raises(ValueError, match="family"):
        _admit(required_method_family_id="family", terminal_tips={"a": _tip("a"), "b": _tip("b", family="foreign", outcome="CLEAN_REFUTATION")})


def test_order_is_stable_not_latest_completion_and_numeric_ranking_is_opt_in():
    a, b = _tip("a", value="0.6"), _tip("b", value="0.9")
    assert loops.order_compared_tips((b, a)) == (a, b)
    assert loops.order_compared_tips((a, b), numeric=True) == (b, a)
    equal = _tip("b", value="0.6")
    assert loops.order_compared_tips((equal, a), numeric=True) == (a, equal)
    different = _tip("b", metric="latency")
    assert loops.order_compared_tips((different, a)) == (a, different)
    with pytest.raises(ValueError, match="declaration"):
        loops.order_compared_tips((a, different), numeric=True)
    selected = _admit()
    late = _tip("b", value="99")
    assert loops.order_compared_tips((late, a), numeric=True)[0] == late
    assert selected.selected_tip.outcome_id == "o-a"  # No mutable latest-tip lookup.


def test_c5_numeric_order_requires_exact_parser_sources():
    tips = []
    for chain, source in (("a", b"print(1)"), ("b", b"print(2)")):
        hypothesis, preregistration, outcome = _facts(chain)
        preregistration["measurement"] = {"metric": preregistration["metric"], "comparison": "VALUE",
            "parse_rule": "Take the parsed score", "parser_argv": ["python", "/input/parse.py"],
            "parser_files": ["parse.py"], "raw_artifacts": ["metrics.csv"]}
        tips.append(loops.compared_tip(chain, hypothesis=hypothesis, preregistration=preregistration, outcome=outcome,
                                      parser_sources={"parse.py": source}))
    with pytest.raises(ValueError, match="parser"):
        loops.order_compared_tips(tips, numeric=True)
    same = replace(tips[1], parser_sources=tips[0].parser_sources)
    assert loops.order_compared_tips((same, tips[0]), numeric=True) == (tips[0], same)


@pytest.mark.parametrize("field", ["keep", "prune", "evidence"])
def test_adjudication_wire_arrays_cannot_be_replaced_with_objects(field):
    raw = _adjudication()
    target = raw["verdicts"][0] if field == "evidence" else raw
    target[field] = {item: True for item in target[field]}
    with pytest.raises(ValueError, match="array"):
        _admit(raw)


def test_compared_tip_accepts_raw_stored_payloads_and_real_artifact_vocabulary():
    facts = _facts("a")
    rows = [{"fact_id": item["fact_id"], "run_id": item["run_id"], "fact_type": item["fact_type"],
             "payload_json": json.dumps(item)} for item in facts]
    tip = loops.compared_tip("a", hypothesis=rows[0], preregistration=rows[1], outcome=rows[2])
    assert tip == _tip("a") and tip.result_kind == ComponentResultKind.METRIC.value
    hypothesis, preregistration, accepted = facts
    preregistration.update(result_kind=ComponentResultKind.ARTIFACT.value, metric=None)
    accepted.update(result_kind=ComponentResultKind.ARTIFACT.value, metric_value=None)
    artifact = loops.compared_tip("a", hypothesis=hypothesis, preregistration=preregistration, outcome=accepted)
    assert artifact.comparable
    with pytest.raises(ValueError, match="declaration"):
        loops.order_compared_tips((artifact,), numeric=True)
    assert not _tip("b", outcome="INCONCLUSIVE").comparable
