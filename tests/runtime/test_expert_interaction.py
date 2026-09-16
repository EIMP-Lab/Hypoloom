from __future__ import annotations

import copy
import json
from dataclasses import replace
from uuid import UUID

import pytest

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.domain import (
    GoalDefined,
    MetricSpec,
    ScientificOutcome,
    fact_to_dict,
)
from hypoloom.expert_loop import (
    AuxiliaryRequestKind,
    AuxiliaryTask,
    ResearchNodeKey,
    ResearchNodeKind,
    ResearchNodeTask,
    task_context_from_dict,
)
from hypoloom.expert_interaction import (
    InteractionFacts,
    admit_debate_judgment,
    admit_observer_proposal,
    bind_debate,
    debate_judgment_from_attempt,
    observer_from_attempt,
    read_debate_rounds,
)
from hypoloom.research_kernel import ResearchKernel
from test_domain import all_facts, research_contract
from test_research_kernel import MemoryResearchView


def test_context_selection_preserves_exact_current_and_cited_historical_evidence():
    from hypoloom.expert_interaction import select_interaction_facts
    rows = _facts()
    before = copy.deepcopy(rows)
    selected = select_interaction_facts(rows, hypothesis_ids=["hyp-1"])
    facts = InteractionFacts.from_rows("run-1", selected)
    assert facts.hypothesis("hyp-1")
    assert "out-1" in facts.reference_ids and "out-2" not in facts.reference_ids
    cited = select_interaction_facts(rows, hypothesis_ids=["hyp-1"], reference_ids=["out-2"])
    assert "out-2" in InteractionFacts.from_rows("run-1", cited).reference_ids
    assert all(row in rows for row in cited) and rows == before


def _facts(*, accepted_rows=False):
    originals = all_facts()
    contract = research_contract()
    contract["roles"].append({**copy.deepcopy(contract["roles"][0]), "id": "critic"})
    other = copy.deepcopy(contract["components"][0])
    other.update(
        id="must.robustness",
        tier="must",
        name="robustness",
        owner="critic",
        proposers=["critic"],
    )
    other["result"].update(metric="robustness", target="0.9")
    contract["components"].append(other)
    contract["protocols"] = {"measured": {"result_kind": "metric", "requirements": {}}}
    for component in contract["components"]:
        component["protocol"] = "measured"
    contract["goal"]["summary_metric"].update(
        name="required_component_completion", target="2"
    )
    goal = replace(originals[0], research_contract=contract)
    assert isinstance(goal, GoalDefined)
    peers = []
    substitutions = {
        "hypothesis_id": "hyp-2",
        "experiment_id": "exp-2",
        "execution_id": "exec-2",
        "outcome_id": "out-2",
        "artifact_id": "artifact-2",
        "evidence_id": "evidence-2",
        "assessment_id": "assessment-2",
        "component_id": "must.robustness",
    }
    for fact in originals[1:6]:
        fields = {
            key: value for key, value in substitutions.items() if hasattr(fact, key)
        }
        fields["fact_id"] = str(fact.fact_id) + "-2"
        if hasattr(fact, "proposer_role"):
            fields["proposer_role"] = "critic"
        if hasattr(fact, "metric"):
            fields["metric"] = MetricSpec(
                name="robustness", direction=fact.metric.direction, target="0.9"
            )
        if hasattr(fact, "metric_value"):
            fields["metric_value"] = "0.95"
        if hasattr(fact, "evidence_ids"):
            fields["evidence_ids"] = ("evidence-2",)
        peers.append(replace(fact, **fields))
    candidates = (goal, *originals[1:6], *peers)
    if accepted_rows:
        view = MemoryResearchView()
        kernel = ResearchKernel()
        for candidate in candidates:
            view.add(kernel.construct_fact(candidate, view))
        return view.rows
    return [fact_to_dict(item) for item in candidates]


@pytest.mark.parametrize("database_rows", [False, True])
def test_existing_research_kernel_rows_are_the_only_qualification_source(database_rows):
    rows = _facts(accepted_rows=True)
    if database_rows:
        rows = [{**row, "payload_json": json.dumps(row["payload"])} for row in rows]
        for row in rows:
            row.pop("payload")
    before = copy.deepcopy(rows)
    facts = InteractionFacts.from_rows("run-1", rows)
    selection = admit_observer_proposal(_observer(), facts=facts)
    assert selection.leader.role == "critic"
    assert rows == before


@pytest.mark.parametrize("action", [None, [], {}, 1, "graph_evolution"])
def test_observer_action_is_typed_without_implementing_manager_controls(action):
    raw = _observer()
    raw["action"] = action
    with pytest.raises(ValueError):
        admit_observer_proposal(
            raw, facts=InteractionFacts.from_rows("run-1", _facts())
        )


def _observer(action="leader"):
    return {
        "action": action,
        "rationale": "The critic advances the shared goal's required robustness component.",
        "supporting_refs": ["out-2"],
        "leader": "critic" if action == "leader" else None,
        "debate": {
            "participants": [
                {
                    "scope": {
                        "role": "researcher",
                        "component_id": "primary.score",
                        "chain": "chain-a",
                    },
                    "hypothesis_id": "hyp-1",
                },
                {
                    "scope": {
                        "role": "critic",
                        "component_id": "must.robustness",
                        "chain": "chain-b",
                    },
                    "hypothesis_id": "hyp-2",
                },
            ],
            "topic": "Which approach transfers to the shared objective?",
        }
        if action == "debate"
        else None,
    }


def test_aci14_observer_scientific_refs_come_from_frozen_fact_payload_not_input_alias():
    task = AuxiliaryTask(
        kind=AuxiliaryRequestKind.OBSERVER, run_id="run-1", cycle=1, ordinal=19
    )
    context = EmbeddedInput.from_bytes(
        task.command_id + "--interaction-context",
        "application/json",
        json.dumps(
            {
                "facts": _facts(),
                "source_report_ref": "report--response",
                "allowed_actions": ["none", "leader"],
            }
        ).encode(),
    )
    row = _attempt(
        "observer",
        task,
        _observer(),
        session_id="9a4fc500-b7dd-4d91-aa91-9cb33e497f53",
        inputs=(context.to_dict(),),
        refs=(context.ref,),
    )
    observation = json.loads(row["observation_json"])
    observation["request"]["schema_version"] = 14
    row["observation_json"] = json.dumps(observation)
    assert (
        observer_from_attempt(
            row, facts=InteractionFacts.from_rows("run-1", _facts())
        ).leader.role
        == "critic"
    )
    observation["request"]["input_artifacts"] = []
    row["observation_json"] = json.dumps(observation)
    with pytest.raises(ValueError):
        observer_from_attempt(row, facts=InteractionFacts.from_rows("run-1", _facts()))


def test_leader_must_cite_one_exact_qualified_component_not_role_first():
    rows = _facts()
    contract = rows[0]["research_contract"]
    contract["components"][1].update(owner="researcher", proposers=["researcher"])
    for row in rows:
        if row.get("component_id") == "must.robustness" and "proposer_role" in row:
            row["proposer_role"] = "researcher"
    facts = InteractionFacts.from_rows("run-1", rows)
    candidate = {**_observer(), "leader": "researcher"}
    assert admit_observer_proposal(candidate, facts=facts).leader.role == "researcher"
    candidate["supporting_refs"] = ["out-1", "out-2"]
    with pytest.raises(ValueError, match="exactly one qualified component"):
        admit_observer_proposal(candidate, facts=facts)


def test_debate_statement_optional_web_sources_preserve_actual_urls_not_scientific_ids():
    from hypoloom.expert_interaction import admit_debate_statement
    candidate = {"position": "Read the original method.", "arguments": ["Its assumptions differ."],
        "evidence": ["out-1"], "concessions": []}
    assert admit_debate_statement(candidate, reference_ids=["out-1"]).to_dict() == candidate
    candidate["web_sources"] = ["https://example.org/paper"]
    assert admit_debate_statement(candidate, reference_ids=["out-1"]).to_dict() == candidate
    candidate["web_sources"] *= 2
    with pytest.raises(ValueError):
        admit_debate_statement(candidate, reference_ids=["out-1"])


def test_aci14_debate_round_numbers_are_context_not_global_auxiliary_ordinals():
    facts, binding, _, rows = _debate()
    current = []
    for row in rows:
        observation = json.loads(row["observation_json"])
        request = observation["request"]
        task = task_context_from_dict(request["task_context"])
        task = replace(task, ordinal=19 * 3 + task.ordinal)
        request.update(schema_version=14, task_context=task.to_dict(), command_id=task.command_id)
        request["immutable_context_refs"] = [ref for ref in request["immutable_context_refs"] if not ref.startswith(("out-", "hyp-"))]
        context = EmbeddedInput.from_bytes(task.command_id + "--interaction-context", "application/json", json.dumps({"facts": _facts()}).encode())
        request["input_artifacts"].append(context.to_dict())
        request["immutable_context_refs"].append(context.ref)
        current.append({**row, "observation_json": json.dumps(observation)})
    assert len(read_debate_rounds(current, binding=binding, facts=facts)) == 6
    last = json.loads(current[-1]["observation_json"])
    task = task_context_from_dict(last["request"]["task_context"])
    task = replace(task, ordinal=task.ordinal + 3)
    last["request"].update(command_id=task.command_id, task_context=task.to_dict())
    current[-1]["observation_json"] = json.dumps(last)
    with pytest.raises(ValueError):
        read_debate_rounds(current, binding=binding, facts=facts)


def test_aci14_debate_original_uses_exact_frozen_batch_member_not_hypothesis_alias():
    facts, binding, originals, _ = _debate()
    current = []
    for source, fork in zip(originals, binding.participants):
        observation = json.loads(source["observation_json"])
        request = observation["request"]
        scope = fork.participant.scope
        item = EmbeddedInput.from_bytes(request["command_id"] + "--batch-context", "application/json",
            json.dumps({"chains": {scope.chain: fork.participant.hypothesis_id}}).encode())
        request.update(schema_version=14, immutable_context_refs=[item.ref], input_artifacts=[item.to_dict()])
        current.append({**source, "observation_json": json.dumps(observation)})
    proposal = admit_observer_proposal(_observer("debate"), facts=facts)
    assert bind_debate(binding.debate_id, proposal.debate, facts=facts, original_attempts=current,
        fork_session_ids={fork.participant.scope.role: fork.session_id for fork in binding.participants}) == binding


def _attempt(
    attempt_id,
    task,
    candidate=None,
    *,
    session_id=None,
    parent=None,
    inputs=(),
    refs=(),
):
    auxiliary = isinstance(task, AuxiliaryTask)
    role = task.expert_slot if auxiliary else task.key.expert_slot
    component = task.component_id if auxiliary else task.key.component_id
    command = task.kind.value if auxiliary else task.key.node_kind.value
    request = {
        "run_id": "run-1",
        "attempt_id": attempt_id,
        "command_id": task.command_id,
        "command": command,
        "role": role or "observer",
        "component_id": component or "primary.score",
        "task_context": task.to_dict(),
        "immutable_context_refs": list(refs),
        "input_artifacts": list(inputs),
    }
    observation = {"request": request, "response": {"typed_candidate": candidate}}
    if session_id:
        session = {"session_id": session_id, "parent_session_id": parent}
        observation["session_binding"] = session.copy()
        observation["session"] = {
            **session,
            "launch_arguments": [
                "--resume",
                parent,
                "--fork-session",
                "--session-id",
                session_id,
            ]
            if parent
            else ["--session-id", session_id],
        }
    return {
        "attempt_id": attempt_id,
        "run_id": "run-1",
        "kind": "AGENT",
        "state": "CLOSED",
        "terminal_reason": "SUCCEEDED",
        "observation_json": json.dumps(observation),
    }


def _edit(row, mutation):
    result = copy.deepcopy(row)
    observation = json.loads(result["observation_json"])
    mutation(observation)
    result["observation_json"] = json.dumps(observation)
    return result


def _debate():
    facts = InteractionFacts.from_rows("run-1", _facts())
    proposal = admit_observer_proposal(_observer("debate"), facts=facts)
    originals = []
    for ordinal, participant in enumerate(proposal.debate.participants, 1):
        scope = participant.scope
        key = ResearchNodeKey(
            run_id="run-1",
            component_id=scope.component_id,
            expert_slot=scope.role,
            cycle=1,
            node_kind=ResearchNodeKind.RESULT_ANALYSIS,
            experiment_ordinal=1,
            chain=scope.chain,
        )
        originals.append(
            _attempt(
                f"original-{scope.role}",
                ResearchNodeTask(key=key),
                session_id=str(UUID(int=ordinal)),
                refs=[participant.hypothesis_id],
            )
        )
    fork_ids = {"researcher": str(UUID(int=3)), "critic": str(UUID(int=4))}
    binding = bind_debate(
        "debate-1",
        proposal.debate,
        facts=facts,
        original_attempts=originals,
        fork_session_ids=fork_ids,
    )
    rows = []
    for number in range(1, 4):
        for participant in binding.participants:
            role = participant.participant.scope.role
            peer = next(
                item for item in binding.participants if item is not participant
            )
            previous = (
                None
                if number == 1
                else f"round-{number - 1}-{peer.participant.scope.role}"
            )
            context = binding.round_context(role, number, previous)
            refs = [
                participant.original_attempt_id,
                participant.participant.hypothesis_id,
                "out-1",
                "out-2",
            ]
            if previous:
                refs.append(previous)
            candidate = {
                "position": "The recorded result is relevant.",
                "arguments": ["Compare the declared objectives."],
                "evidence": ["out-1", "out-2"],
                "concessions": [],
            }
            task = AuxiliaryTask(
                kind=AuxiliaryRequestKind.DEBATE,
                run_id="run-1",
                component_id=participant.participant.scope.component_id,
                expert_slot=role,
                cycle=1,
                ordinal=number,
            )
            row = _attempt(
                f"round-{number}-{role}",
                task,
                candidate,
                session_id=participant.session_id,
                parent=participant.original_session_id,
                inputs=[
                    EmbeddedInput.from_bytes(
                        binding.input_ref,
                        "application/json",
                        json.dumps(context).encode(),
                    ).to_dict()
                ],
                refs=refs,
            )
            if number > 1:
                row = _edit(
                    row,
                    lambda observation: observation["session"].update(
                        launch_arguments=["--resume", participant.session_id]
                    ),
                )
            rows.append(row)
    return facts, binding, originals, rows


def _judgment(rows):
    return {
        "winner": "critic",
        "scores": [
            {
                "role": role,
                "score": score,
                "round_refs": [f"round-{number}-{role}" for number in range(1, 4)],
            }
            for role, score in (("researcher", "7"), ("critic", "8"))
        ],
        "keep": ["critic"],
        "prune": ["researcher"],
        "rationale": "The critique resolves the stated disagreement.",
        "supporting_refs": [row["attempt_id"] for row in rows] + ["out-2"],
    }


def test_unique_component_owners_both_qualify_without_cross_metric_ranking():
    facts = InteractionFacts.from_rows("run-1", _facts())
    qualified = facts.leader_qualifications()
    assert {item.role for item in qualified} == {"researcher", "critic"}
    assert {item.tier for item in qualified} == {"primary", "must"}
    chosen = admit_observer_proposal(_observer(), facts=facts)
    assert chosen.leader.role == "critic"
    assert chosen.leader.contributions[0].component_id == "must.robustness"
    assert chosen.to_dict() == _observer()


def test_supported_requires_real_same_hypothesis_evidence_and_comparable_outcome():
    rows = _facts()
    outcome = next(
        item
        for item in rows
        if item.get("outcome_id") == "out-2"
        and item["fact_type"] == "CanonicalOutcomeAccepted"
    )
    outcome["outcome"] = ScientificOutcome.CLEAN_REFUTATION.value
    facts = InteractionFacts.from_rows("run-1", rows)
    proposal = _observer()
    proposal["supporting_refs"] = ["assessment-2"]
    assert admit_observer_proposal(proposal, facts=facts).leader.role == "critic"
    # The accepted assessment is the qualification, not an invented numerical verdict.
    outcome["outcome"], outcome["metric_value"] = "EXECUTION_FAILURE", None
    with pytest.raises(ValueError, match="comparable"):
        InteractionFacts.from_rows("run-1", rows).leader_qualifications()


@pytest.mark.parametrize(
    "tamper",
    ["foreign_run", "wrong_goal", "wrong_owner", "fake_evidence", "foreign_evidence"],
)
def test_leader_rejects_false_accepted_fact_ownership(tamper):
    rows = _facts()
    hypothesis = next(
        item
        for item in rows
        if item.get("hypothesis_id") == "hyp-2"
        and item["fact_type"] == "HypothesisProposed"
    )
    assessment = next(
        item for item in rows if item.get("assessment_id") == "assessment-2"
    )
    if tamper == "foreign_run":
        hypothesis["run_id"] = "run-foreign"
    elif tamper == "wrong_goal":
        hypothesis["goal_id"] = "goal-foreign"
    elif tamper == "wrong_owner":
        hypothesis["proposer_role"] = "researcher"
    else:
        assessment["evidence_ids"] = [
            "missing" if tamper == "fake_evidence" else "evidence-1"
        ]
    with pytest.raises(ValueError):
        InteractionFacts.from_rows("run-1", rows).leader_qualifications()


@pytest.mark.parametrize(
    "tamper",
    [
        "unknown_role",
        "uncited_leader",
        "unknown_ref",
        "extra_action",
        "duplicate_debater",
        "foreign_hypothesis",
    ],
)
def test_observer_candidate_domain_is_exact(tamper):
    raw = _observer(
        "debate" if tamper in {"duplicate_debater", "foreign_hypothesis"} else "leader"
    )
    if tamper == "unknown_role":
        raw["leader"] = "missing"
    elif tamper == "uncited_leader":
        raw["supporting_refs"] = ["out-1"]
    elif tamper == "unknown_ref":
        raw["supporting_refs"].append("not-accepted")
    elif tamper == "extra_action":
        raw["action"] = "graph_evolution"
    elif tamper == "duplicate_debater":
        raw["debate"]["participants"][1] = raw["debate"]["participants"][0]
    else:
        raw["debate"]["participants"][0]["hypothesis_id"] = "hyp-2"
    with pytest.raises(ValueError):
        admit_observer_proposal(
            raw, facts=InteractionFacts.from_rows("run-1", _facts())
        )


@pytest.mark.parametrize("tamper", [None, "resumed", "open", "wrong_command"])
def test_observer_raw_source_is_a_fresh_closed_observer(tamper):
    task = AuxiliaryTask(kind=AuxiliaryRequestKind.OBSERVER, run_id="run-1")
    row = _attempt(
        "observer-1", task, _observer(), session_id=str(UUID(int=9)), refs=["out-2"]
    )
    if tamper == "resumed":
        row = _edit(
            row,
            lambda item: item["session"].update(
                launch_arguments=["--resume", str(UUID(int=9))]
            ),
        )
    if tamper == "open":
        row["state"] = "OPEN"
    if tamper == "wrong_command":
        row = _edit(row, lambda item: item["request"].update(command="LEADER"))
    if tamper is None:
        selection = observer_from_attempt(
            row, facts=InteractionFacts.from_rows("run-1", _facts())
        )
        control = selection.control_payload("observer-1")
        assert control["expert_interaction"]["source_attempt_id"] == "observer-1"
        assert control["expert_interaction"]["action"] == "leader_selected"
    else:
        with pytest.raises(ValueError):
            observer_from_attempt(
                row, facts=InteractionFacts.from_rows("run-1", _facts())
            )


def test_three_rounds_bind_exact_real_attempts_original_sessions_and_winner_promotion():
    facts, binding, originals, rows = _debate()
    frozen = copy.deepcopy(rows)
    rounds = read_debate_rounds(list(reversed(rows)), binding=binding, facts=facts)
    assert [(item.number, item.participant.scope.role) for item in rounds] == [
        (number, role) for number in range(1, 4) for role in ("critic", "researcher")
    ]
    judged = admit_debate_judgment(
        _judgment(rows),
        binding=binding,
        rounds=rounds,
        facts=facts,
        source_attempt_id="judge-1",
    )
    control = judged.control_payload()["expert_interaction"]
    assert control["promote_session"] == {
        "role": "critic",
        "original_session_id": str(UUID(int=2)),
        "session_id": str(UUID(int=4)),
    }
    assert control["pruned_by"][0]["loser_hypothesis_id"] == "hyp-1"
    assert control["pruned_by"][0]["winner_hypothesis_id"] == "hyp-2"
    assert control["pruned_by"][0]["debate_id"] == "debate-1"
    assert control["pruned_by"][0]["score_refs"] == [
        f"round-{number}-researcher" for number in range(1, 4)
    ]
    assert "assessment" not in json.dumps(control)
    assert rows == frozen and len(originals) == 2


@pytest.mark.parametrize(
    "tamper",
    [
        "missing",
        "duplicate",
        "open",
        "failed",
        "wrong_parent",
        "wrong_fork",
        "wrong_role",
        "wrong_round",
        "missing_context",
        "wrong_previous",
        "unknown_evidence",
        "not_in_input",
        "restart_from_original",
    ],
)
def test_debate_rounds_reject_broken_process_and_causal_bindings(tamper):
    facts, binding, _, rows = _debate()
    if tamper == "missing":
        rows.pop()
    elif tamper == "duplicate":
        rows[-1] = rows[0]
    elif tamper == "open":
        rows[0]["state"] = "OPEN"
    elif tamper == "failed":
        rows[0]["terminal_reason"] = "LOST"
    else:

        def mutate(item):
            if tamper == "wrong_parent":
                item["session_binding"]["parent_session_id"] = str(UUID(int=99))
            elif tamper == "wrong_fork":
                item["session"]["session_id"] = str(UUID(int=99))
            elif tamper == "wrong_role":
                item["request"]["role"] = "critic"
            elif tamper == "wrong_round":
                item["request"]["task_context"]["ordinal"] = 1
            elif tamper == "missing_context":
                item["request"]["input_artifacts"] = []
            elif tamper == "wrong_previous":
                item["request"]["immutable_context_refs"].remove("round-1-critic")
            elif tamper == "unknown_evidence":
                item["response"]["typed_candidate"]["evidence"] = ["fake-ref"]
            elif tamper == "not_in_input":
                item["request"]["immutable_context_refs"].remove("out-2")
            else:
                item["session"]["launch_arguments"] = [
                    "--resume",
                    str(UUID(int=1)),
                    "--fork-session",
                    "--session-id",
                    str(UUID(int=3)),
                ]

        index = (
            2
            if tamper in {"wrong_round", "wrong_previous", "restart_from_original"}
            else 0
        )
        rows[index] = _edit(rows[index], mutate)
    with pytest.raises(ValueError):
        read_debate_rounds(rows, binding=binding, facts=facts)


@pytest.mark.parametrize(
    "tamper",
    [
        "foreign_original_role",
        "wrong_chain",
        "unknown_hypothesis",
        "reuse_original",
        "same_fork",
    ],
)
def test_debate_binding_does_not_fabricate_original_sessions(tamper):
    facts, binding, originals, _ = _debate()
    proposal = admit_observer_proposal(_observer("debate"), facts=facts).debate
    ids = {
        item.participant.scope.role: item.session_id for item in binding.participants
    }
    if tamper == "reuse_original":
        ids["researcher"] = str(UUID(int=1))
    elif tamper == "same_fork":
        ids["researcher"] = ids["critic"]
    else:

        def mutate(item):
            if tamper == "foreign_original_role":
                item["request"]["role"] = "critic"
            elif tamper == "wrong_chain":
                item["request"]["task_context"]["key"]["chain"] = "other"
            else:
                item["request"]["immutable_context_refs"] = ["hyp-foreign"]

        originals[0] = _edit(originals[0], mutate)
    with pytest.raises(ValueError):
        bind_debate(
            "debate-1",
            proposal,
            facts=facts,
            original_attempts=originals,
            fork_session_ids=ids,
        )


@pytest.mark.parametrize(
    "tamper",
    [
        "missing_round_ref",
        "foreign_round_ref",
        "fake_fact_ref",
        "foreign_winner",
        "overlap",
        "missing_partition",
        "loser_wins",
        "nonfinite",
        "numeric",
        "fake_verdict",
    ],
)
def test_judgment_only_controls_exact_participants_and_accepted_references(tamper):
    facts, binding, _, rows = _debate()
    rounds = read_debate_rounds(rows, binding=binding, facts=facts)
    raw = _judgment(rows)
    if tamper == "missing_round_ref":
        raw["supporting_refs"].pop(0)
    elif tamper == "foreign_round_ref":
        raw["scores"][0]["round_refs"][0] = "round-1-critic"
    elif tamper == "fake_fact_ref":
        raw["supporting_refs"].append("made-up-assessment")
    elif tamper == "foreign_winner":
        raw["winner"] = "outsider"
    elif tamper == "overlap":
        raw["keep"].append("researcher")
    elif tamper == "missing_partition":
        raw["prune"] = []
    elif tamper == "loser_wins":
        raw["scores"][0]["score"] = "9"
    elif tamper == "nonfinite":
        raw["scores"][0]["score"] = "NaN"
    elif tamper == "numeric":
        raw["scores"][0]["score"] = 7
    else:
        raw["assessment"] = "REFUTED"
    with pytest.raises(ValueError):
        admit_debate_judgment(
            raw,
            binding=binding,
            rounds=rounds,
            facts=facts,
            source_attempt_id="judge-1",
        )


def test_judgment_keep_both_and_raw_judge_provenance():
    facts, binding, _, rows = _debate()
    raw = _judgment(rows)
    raw.update(winner=None, keep=["researcher", "critic"], prune=[])
    task = AuxiliaryTask(kind=AuxiliaryRequestKind.OBSERVER, run_id="run-1", ordinal=2)
    source = _attempt(
        "judge-1", task, raw, session_id=str(UUID(int=99)), refs=raw["supporting_refs"]
    )
    judged = debate_judgment_from_attempt(
        source, binding=binding, round_attempts=rows, facts=facts
    )
    assert judged.control_payload()["expert_interaction"]["promote_session"] is None
    assert not judged.pruned_by
    source = _edit(
        source,
        lambda item: item["request"]["immutable_context_refs"].remove("round-3-critic"),
    )
    with pytest.raises(ValueError):
        debate_judgment_from_attempt(
            source, binding=binding, round_attempts=rows, facts=facts
        )
