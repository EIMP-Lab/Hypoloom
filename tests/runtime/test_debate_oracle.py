from __future__ import annotations

import copy

import pytest

from hypoloom.expert_loop import ResearchNodeSubkind
from test_debate_host import setup, debate_rounds, judge
from test_interaction_host import node
from test_interaction_oracle import RawRecords


@pytest.mark.parametrize("damage", [None, "wrong-parent", "duplicate-participant", "premature-finish"])
def test_directed_restart_audit_uses_participant_parent_and_actual_closure(tmp_path, damage):
    host, view, _, _ = setup(tmp_path, agent_version=21, selected=False)
    try:
        view, active, _, completed = debate_rounds(host, view)
        judge(host, view, active, completed)
        view.drive("run-1")
        data = RawRecords(host)
    finally:
        host.store.close()
    assert data.audit() == []
    events = [row for row in data.transitions if row["payload"].get("expert_interaction", {}).get("action") == "debate_restarts_designated"]
    assert len(events) == 2
    if damage == "wrong-parent":
        events[0]["payload"]["expert_interaction"]["plans"][0]["parent_hypothesis_id"] = "hyp-foreign"
    elif damage == "duplicate-participant":
        events[1]["payload"]["expert_interaction"]["plans"] = copy.deepcopy(events[0]["payload"]["expert_interaction"]["plans"])
    elif damage == "premature-finish":
        finished = copy.deepcopy(events[-1])
        finished["revision"] += 1
        event = finished["payload"]["expert_interaction"]
        event.update(action="debate_restarts_finished", plans=[plan for row in events
            for plan in row["payload"]["expert_interaction"]["plans"]])
        data.transitions.append(finished)
    if damage is not None:
        assert data.audit()


def fixture(tmp_path, *, judged=True, restart=True):
    host, view, _, _ = setup(tmp_path)
    view, active, binding, completed = debate_rounds(host, view)
    if judged:
        judge(host, view, active, completed)
        if restart:
            view.drive("run-1")
            task = node("HYPOTHESIS_PROPOSAL", cycle=2, subkind=ResearchNodeSubkind.SYNTHESIS)
            host.record(task, inputs=view.debate.promotion_inputs("run-1", task))
    data = RawRecords(host)
    host.store.close()
    return data


@pytest.mark.parametrize("judged,restart", [(False, False), (True, False), (True, True)])
def test_independent_oracle_reads_actual_six_round_records_and_accepted_promotion(tmp_path, judged, restart):
    data = fixture(tmp_path, judged=judged, restart=restart)
    before = copy.deepcopy(vars(data))
    assert data.audit() == []
    assert vars(data) == before


@pytest.mark.parametrize("field", ["role", "typed_candidate", "attempt_id", "task_context"])
def test_projected_opponent_source_still_matches_the_canonical_attempt(tmp_path, field):
    import json
    from hypoloom.agent_aci import EmbeddedInput
    data = fixture(tmp_path)
    identity = next(identity for identity, value in reversed(data.observations.items())
        if value.get("request", {}).get("command") == "DEBATE")
    request = data.observations[identity]["request"]
    context = json.loads(EmbeddedInput.from_dict(next(item for item in request["input_artifacts"]
        if item["ref"].endswith("--debate-context"))).content_bytes())
    data.edit_input(identity, context["previous_opponent_attempt_id"], lambda value: value.update({field: "changed"}))
    assert data.audit()


@pytest.mark.parametrize("field", ["statement", "source_attempt_id", "session_id", "participant"])
def test_projected_judge_cannot_change_canonical_round_content(tmp_path, field):
    data = fixture(tmp_path)
    identity = data.event("debate_verdict")["payload"]["expert_interaction"]["source_attempt_id"]
    data.edit_input(identity, "--debate-judge-context", lambda value: value["rounds"][0].update({field: "changed"}))
    assert data.audit()


def test_actual_judge_score_reference_order_is_not_lexical_attempt_order(tmp_path):
    host, view, _, _ = setup(tmp_path)
    view, active, _, completed = debate_rounds(host, view)
    original_record = host.record

    def record(task, **kwargs):
        candidate = copy.deepcopy(kwargs["candidate"])
        for score in candidate["scores"]:
            score["round_refs"] = sorted(score["round_refs"], reverse=True)
        return original_record(task, **{**kwargs, "candidate": candidate})

    host.record = record
    judge(host, view, active, completed)
    data = RawRecords(host)
    host.store.close()
    assert data.audit() == []


@pytest.mark.parametrize("tamper", ["missing_round", "failed_round", "duplicate_round", "wrong_previous", "wrong_trigger", "changed_original", "changed_fork"])
def test_debate_judge_cannot_replace_six_actual_causal_participant_outputs(tmp_path, tamper):
    data = fixture(tmp_path)
    identities = [identity for identity, value in data.observations.items() if value.get("request", {}).get("command") == "DEBATE"]
    identity = identities[-1]
    request = data.observations[identity]["request"]
    if tamper == "missing_round":
        data.attempts = [row for row in data.attempts if row["attempt_id"] != identity]
        data.observations.pop(identity)
    elif tamper == "failed_round":
        next(row for row in data.attempts if row["attempt_id"] == identity)["terminal_reason"] = "FAILED"
    elif tamper == "duplicate_round":
        row = copy.deepcopy(next(row for row in data.attempts if row["attempt_id"] == identity))
        row["attempt_id"] += "-duplicate"
        data.attempts.append(row)
        data.observations[row["attempt_id"]] = copy.deepcopy(data.observations[identity])
    elif tamper == "wrong_previous":
        data.edit_input(identity, "--debate-context", lambda value: value.update(previous_opponent_attempt_id=identities[0]))
    elif tamper == "wrong_trigger":
        request["task_context"]["ordinal"] += 3
    elif tamper == "changed_original":
        data.edit_input(identity, "--debate-context", lambda value: value.update(original_attempt_id="invented"))
    else:
        data.edit_input(identity, "--debate-context", lambda value: value.update(session_id="00000000-0000-0000-0000-000000000099"))
    assert data.audit()


@pytest.mark.parametrize("tamper", ["missing_judge", "open_judge", "partial_judge_inputs", "invented_verdict_source", "changed_score_refs", "false_pruning", "wrong_pause", "release_operator"])
def test_debate_verdict_requires_its_actual_judge_scores_and_scoped_control(tmp_path, tamper):
    data = fixture(tmp_path)
    row = data.event("debate_verdict")
    event = row["payload"]["expert_interaction"]
    identity = event["source_attempt_id"]
    if tamper == "missing_judge":
        data.attempts = [row for row in data.attempts if row["attempt_id"] != identity]
        data.observations.pop(identity)
    elif tamper == "open_judge":
        next(row for row in data.attempts if row["attempt_id"] == identity)["state"] = "OPEN"
    elif tamper == "partial_judge_inputs":
        data.edit_input(identity, "--debate-judge-context", lambda value: value["rounds"].pop())
    elif tamper == "invented_verdict_source":
        event["source_attempt_id"] = "invented"
    elif tamper == "changed_score_refs":
        event["scores"][0]["round_refs"] = ["invented"]
        data.observations[identity]["response"]["typed_candidate"]["scores"][0]["round_refs"] = ["invented"]
    elif tamper == "false_pruning":
        event["pruned_by"][0]["winner_hypothesis_id"] = "hyp-1"
    elif tamper == "wrong_pause":
        next(row for row in data.transitions if row["payload"].get("debate_id"))["payload"]["participant_control"]["scope"]["chain"] = "another-chain"
    else:
        row["payload"]["participant_control"]["reason"] = "operator"
    assert data.audit()


@pytest.mark.parametrize("tamper", ["unaccepted_judge", "source_not_third_round", "foreign_owner", "another_cycle", "loser_promoted", "missing_binding", "false_parent", "fake_literature"])
def test_session_promotion_requires_actual_winning_judge_and_budgeted_hp_restart(tmp_path, tamper):
    data = fixture(tmp_path)
    identity = next(identity for identity, value in data.observations.items() if value.get("request", {}).get("command") == "HYPOTHESIS_PROPOSAL")
    request = data.observations[identity]["request"]
    if tamper == "unaccepted_judge":
        data.edit_input(identity, "--session-promotion", lambda value: value.update(source_judge_attempt_id="invented"))
    elif tamper == "source_not_third_round":
        data.edit_input(identity, "--session-promotion", lambda value: value.update(source_attempt_id="invented-round-one"))
    elif tamper == "foreign_owner":
        data.edit_input(identity, "--session-promotion", lambda value: value.update(role="researcher"))
    elif tamper == "another_cycle":
        request["task_context"]["key"]["cycle"] = 3
    elif tamper == "loser_promoted":
        request.update(role="researcher", component_id="primary.score")
        request["task_context"]["key"].update(expert_slot="researcher", component_id="primary.score")
    elif tamper == "missing_binding":
        request["input_artifacts"] = []
    else:
        plans = data.event("debate_restarts_designated")["payload"]["expert_interaction"]["plans"]
        if tamper == "false_parent":
            plans[-1]["parent_hypothesis_id"] = "hyp-1"
        else:
            plans[-1]["source_literature_attempt_id"] = "invented"
    assert data.audit()


@pytest.mark.parametrize("tamper", [None, "missing_verdict", "source", "parent", "cycle", "literature", "missing_plan"])
def test_batch_restart_after_terminate_requires_actual_debate_judge_authorization(tmp_path, tamper):
    from hypoloom.batch_oracle import _Audit
    data = fixture(tmp_path)
    request = next(value["request"] for value in data.observations.values() if value.get("request", {}).get("command") == "HYPOTHESIS_PROPOSAL")
    event = data.event("debate_restarts_designated")["payload"]["expert_interaction"]
    plan = next(item for item in event["plans"] if item["role"] == "critic")
    if tamper == "missing_verdict":
        data.transitions.remove(data.event("debate_verdict"))
    elif tamper == "source":
        event["source_attempt_id"] = "invented"
    elif tamper == "parent":
        plan["parent_hypothesis_id"] = "hyp-1"
    elif tamper == "cycle":
        plan["source_cycle"] = 2
    elif tamper == "literature":
        plan["source_literature_attempt_id"] = "invented"
    elif tamper == "missing_plan":
        data.transitions.remove(data.event("debate_restarts_designated"))
    audit = _Audit("run-1", data.attempts, data.observations, data.facts, data.payloads, data.transitions, ())
    if tamper == "missing_plan":
        assert not audit.debate_reopen(request, "hyp-2", len(data.transitions) + 1)
    elif tamper:
        with pytest.raises((KeyError, ValueError)):
            audit.debate_reopen(request, "hyp-2", len(data.transitions) + 1)
    else:
        assert audit.debate_reopen(request, "hyp-2", len(data.transitions) + 1)


@pytest.mark.parametrize("tamper", [None, "missing_ancestor", "foreign_component", "foreign_source", "changed_cycle", "future_ancestor"])
def test_repeated_debate_literature_requires_exact_accepted_restart_ancestry(tmp_path, tamper):
    from hypoloom.batch_oracle import _debate_literature_source
    data = fixture(tmp_path)
    first = data.event("debate_restarts_designated")
    original = next(item for item in first["payload"]["expert_interaction"]["plans"] if item["role"] == "critic")
    current = {**copy.deepcopy(original), "cycle": 3, "source_cycle": 2}
    effect = {**first, "revision": first["revision"] + 10}
    if tamper == "missing_ancestor": data.transitions.remove(first)
    elif tamper == "foreign_component": original["component_id"] = "primary.score"
    elif tamper == "foreign_source":
        foreign = next(value["request"] for value in data.observations.values()
            if value.get("request", {}).get("command") == "LITERATURE_REVIEW" and value["request"]["role"] == "researcher")
        current["source_literature_attempt_id"] = original["source_literature_attempt_id"] = foreign["attempt_id"]
        current["source_literature_command_id"] = original["source_literature_command_id"] = foreign["command_id"]
    elif tamper == "changed_cycle": original["source_cycle"] = 2
    elif tamper == "future_ancestor": first["revision"] = effect["revision"] + 1
    arguments = (current, effect, [(row, row["payload"]) for row in data.transitions],
        {row["attempt_id"]: row for row in data.attempts}, data.observations, "run-1")
    if tamper:
        with pytest.raises(ValueError): _debate_literature_source(*arguments)
    else:
        _debate_literature_source(*arguments)
