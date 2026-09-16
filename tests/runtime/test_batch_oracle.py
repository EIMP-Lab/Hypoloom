from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from hypoloom.agent_aci import EmbeddedInput, canonical_json
from hypoloom.batch_oracle import audit_batches, selected_fact_tips
from hypoloom.domain import (
    HypothesisAssessment,
    ScientificOutcome,
    fact_to_dict,
)
from hypoloom.expert_loop import (
    ResearchNodeKey,
    ResearchNodeKind,
    ResearchNodeSubkind,
    ResearchNodeTask,
)
from hypoloom.profile_v4 import project_profile_v4
from test_domain import all_facts
from test_profile_v4 import documents


class Records:
    def __init__(self):
        self.run_id = "run-1"
        self.facts, self.transitions, self.attempts, self.commands = [], [], [], []
        self.fact_payloads, self.attempt_payloads = {}, {}
        self.clock = 0.0

    def transition(
        self, command, payload, *, fact_id=None, workflow="run-1", kind="RUN"
    ):
        ordinal = len(self.transitions) + 1
        self.clock += 1
        row = {
            "raw_order": ordinal,
            "created_at": datetime.fromtimestamp(self.clock, timezone.utc).isoformat(),
            "run_id": self.run_id,
            "workflow_id": workflow,
            "workflow_kind": kind,
            "revision": 1
            + sum(item["workflow_id"] == workflow for item in self.transitions),
            "command_type": command,
            "command_id": f"command-{ordinal}",
            "fact_id": fact_id,
            "payload_json": json.dumps(payload),
        }
        self.transitions.append(row)
        return row

    def fact(self, fact):
        payload = fact_to_dict(fact)
        self.fact_payloads[payload["fact_id"]] = payload
        row = {
            **payload,
            "raw_order": len(self.facts) + 1,
            "payload_json": json.dumps(payload),
        }
        self.facts.append(row)
        return row

    def agent(
        self,
        name,
        node,
        candidate,
        *,
        chain=None,
        iteration=None,
        subkind=ResearchNodeSubkind.MAIN,
        inputs=(),
        state="CLOSED",
        cycle=1,
        component="primary.score",
        role="researcher",
        family="family-1",
    ):
        key = ResearchNodeKey(
            run_id=self.run_id,
            component_id=component,
            expert_slot=role,
            cycle=cycle,
            node_kind=node,
            subkind=subkind,
            experiment_ordinal=iteration,
            chain=chain,
        )
        task = ResearchNodeTask(key=key)
        request = {
            "schema_version": 14,
            "run_id": self.run_id,
            "attempt_id": name,
            "command_id": task.command_id,
            "role": role,
            "component_id": component,
            "method_family_id": family,
            "command": node.value,
            "task_context": task.to_dict(),
            "input_artifacts": [item.to_dict() for item in inputs],
            "immutable_context_refs": [item.ref for item in inputs],
        }
        response = {
            "typed_candidate": copy.deepcopy(candidate),
            "output_digest": f"existing-output-{name}",
        }
        observation = {"request": request, "response": response}
        self.clock += 1
        row = {
            "run_id": self.run_id,
            "attempt_id": name,
            "command_id": task.command_id,
            "kind": "AGENT",
            "state": state,
            "terminal_reason": "SUCCEEDED" if state == "CLOSED" else None,
            "started_at": self.clock,
            "ended_at": self.clock + 0.2 if state == "CLOSED" else None,
            "attempt_ordinal": 1,
        }
        self.clock += 0.2
        self.attempts.append(row)
        self.attempt_payloads[name] = observation
        return row, observation

    def audit(self):
        return audit_batches(
            self.run_id,
            self.attempts,
            self.attempt_payloads,
            self.facts,
            self.fact_payloads,
            self.transitions,
            self.commands,
        )


def _input(ref, payload):
    return EmbeddedInput.from_bytes(ref, "application/json", canonical_json(payload))


def _fixture():
    data = Records()
    goal, hypothesis, spec, outcome, evidence, assessment, _ = all_facts()
    contract = goal.research_contract.to_dict()
    product = documents()
    product["experts"]["experts"][0]["name"] = "researcher"
    product["experts"]["defaults"]["max_hypotheses_per_round"] = 2
    product["methodology"]["inner_loop"]["max_iterations_per_hypothesis"] = 2
    product["goal"]["stop_conditions"]["max_rounds_per_expert"] = 2
    contract["methodology"]["controls"]["product"] = project_profile_v4(
        **product
    ).settings
    goal = replace(goal, research_contract=contract)
    data.fact(goal)
    data.transition("CREATE_RUN", {}, fact_id=goal.fact_id)
    members = {}
    for index in (1, 2):
        fields = {"fact_id": f"fact-hyp-{index}", "hypothesis_id": f"hyp-{index}"}
        members[f"chain-{index}"] = replace(hypothesis, **fields)
    batch_candidate = {
        "schema_version": 1,
        "hypotheses": [
            {
                "chain_id": chain,
                "candidate": {
                    field: fact_to_dict(fact).get(field)
                    for field in (
                        "component_id",
                        "proposer_role",
                        "method_family_id",
                        "claim",
                        "assumptions",
                        "parent_hypothesis_id",
                        "relation",
                    )
                },
            }
            for chain, fact in members.items()
        ],
    }
    source, observation = data.agent(
        "synthesis",
        ResearchNodeKind.HYPOTHESIS_PROPOSAL,
        batch_candidate,
        subkind=ResearchNodeSubkind.SYNTHESIS,
    )
    batch_id = observation["request"]["command_id"]
    for index, (chain, fact) in enumerate(members.items()):
        data.fact(fact)
        payload = {
            "node_command_id": batch_id,
            "agent_attempt_id": "synthesis",
            "agent_output_digest": observation["response"]["output_digest"],
            "role": "researcher",
            "component_id": "primary.score",
            "chain": chain,
            "hypothesis_id": fact.hypothesis_id,
            "method_family_id": fact.method_family_id,
        }
        if index == len(members) - 1:
            payload["hypothesis_batch"] = {
                "cycle": 1,
                "chains": {
                    chain: str(item.hypothesis_id) for chain, item in members.items()
                },
            }
        data.transition("ACCEPT_HYPOTHESIS", payload, fact_id=fact.fact_id)
    tips = {}
    for index, (chain, fact) in enumerate(members.items(), 1):
        exp, execution, output = f"exp-{index}", f"exec-{index}", f"out-{index}"
        source, observation = data.agent(
            f"execution-{index}",
            ResearchNodeKind.EXPERIMENT_EXECUTION,
            None,
            chain=chain,
            iteration=1,
            state="OPEN",
        )
        submit_id = f"submit-{index}"
        creation = {
            "hypothesis_id": fact.hypothesis_id,
            "component_id": fact.component_id,
            "role": fact.proposer_role,
            "method_family_id": fact.method_family_id,
            "expert_cycle": 1,
            "chain": chain,
            "chain_iteration": 1,
            "experiment_ordinal": index,
            "revision_of_outcome_id": None,
            "source_submit_command_id": submit_id,
            "source_agent_attempt_id": source["attempt_id"],
            "execution_node_command_id": observation["request"]["command_id"],
        }
        data.commands.append(
            {
                "run_id": "run-1",
                "command_id": submit_id,
                "workflow_id": exp,
                "command_type": "CREATE_EXPERIMENT",
                "response_status": "accepted",
                "request_json": json.dumps(
                    {
                        "tool": "submit_experiment",
                        "execution_node_command_id": observation["request"][
                            "command_id"
                        ],
                        "source_request": observation["request"],
                    }
                ),
            }
        )
        data.transition("CREATE_EXPERIMENT", creation, workflow=exp, kind="EXPERIMENT")
        current_spec = replace(
            spec,
            fact_id=f"fact-spec-{index}",
            hypothesis_id=fact.hypothesis_id,
            experiment_id=exp,
            execution_id=execution,
        )
        current_outcome = replace(
            outcome,
            fact_id=f"fact-outcome-{index}",
            experiment_id=exp,
            execution_id=execution,
            outcome_id=output,
            artifact_id=f"artifact-{index}",
            outcome=ScientificOutcome.SUCCESS
            if index == 1
            else ScientificOutcome.CLEAN_REFUTATION,
            metric_value="0.8" if index == 1 else "0.7",
        )
        current_evidence = replace(
            evidence,
            fact_id=f"fact-evidence-{index}",
            hypothesis_id=fact.hypothesis_id,
            evidence_id=f"evidence-{index}",
            outcome_id=output,
            artifact_id=f"artifact-{index}",
        )
        current_assessment = replace(
            assessment,
            fact_id=f"fact-assessment-{index}",
            hypothesis_id=fact.hypothesis_id,
            assessment_id=f"assessment-{index}",
            evidence_ids=(f"evidence-{index}",),
            assessment=HypothesisAssessment.SUPPORTED
            if index == 1
            else HypothesisAssessment.REFUTED,
        )
        for item, command in (
            (current_spec, "PREREGISTER"),
            (current_outcome, "ACCEPT_OUTCOME"),
            (current_evidence, "LINK_EVIDENCE"),
            (current_assessment, "ASSESS_HYPOTHESIS"),
        ):
            data.fact(item)
            data.transition(
                command, {}, fact_id=item.fact_id, workflow=exp, kind="EXPERIMENT"
            )
        analysis_key = ResearchNodeKey(
            run_id="run-1",
            component_id="primary.score",
            expert_slot="researcher",
            cycle=1,
            node_kind=ResearchNodeKind.RESULT_ANALYSIS,
            chain=chain,
            experiment_ordinal=1,
        )
        analysis_input = _input(
            analysis_key.command_id + "--current-outcome",
            {
                "fact_id": current_outcome.fact_id,
                "fact_type": "CanonicalOutcomeAccepted",
                "payload": fact_to_dict(current_outcome),
            },
        )
        data.agent(
            f"analysis-{index}",
            ResearchNodeKind.RESULT_ANALYSIS,
            {
                "task_kind": "RESULT_ANALYSIS",
                "summary": "Conclude.",
                "supporting_refs": [analysis_input.ref],
                "iteration_disposition": "COMPLETE",
            },
            chain=chain,
            iteration=1,
            inputs=(analysis_input,),
        )
        tips[chain] = {
            "run_id": "run-1",
            "component_id": "primary.score",
            "expert_slot": "researcher",
            "chain_id": chain,
            "node_id": str(fact.hypothesis_id),
            "experiment_id": exp,
            "outcome_id": output,
            "outcome_fact_id": str(current_outcome.fact_id),
            "artifact_id": f"artifact-{index}",
            "method_family_id": "family-1",
            "outcome": current_outcome.outcome.value,
            "result_kind": "metric",
            "metric_value": current_outcome.metric_value,
            "measurement": {
                "metric": fact_to_dict(current_spec)["metric"],
                "comparison": "VALUE",
            },
        }
    raw = {
        "schema_version": 1,
        "verdicts": [
            {
                "chain_id": chain,
                "node_id": tip["node_id"],
                "status": "verified" if chain == "chain-1" else "refuted",
                "evidence": [tip["outcome_id"]],
            }
            for chain, tip in tips.items()
        ],
        "keep": ["chain-1"],
        "prune": ["chain-2"],
        "best_chain": "chain-1",
        "summary": "Select the accepted successful tip.",
    }
    key = ResearchNodeKey(
        run_id="run-1",
        component_id="primary.score",
        expert_slot="researcher",
        cycle=1,
        node_kind=ResearchNodeKind.ADJUDICATE,
    )
    context = {
        "batch_command_id": batch_id,
        "cycle": 1,
        "chains": {chain: str(fact.hypothesis_id) for chain, fact in members.items()},
        "terminal_tips": tips,
        "selected_tip": None,
    }
    source, observation = data.agent(
        "adjudicate",
        ResearchNodeKind.ADJUDICATE,
        raw,
        inputs=(_input(key.command_id + "--batch-context", context),),
    )
    data.transition(
        "ACCEPT_ADJUDICATION",
        {
            "node_command_id": key.command_id,
            "agent_attempt_id": "adjudicate",
            "agent_output_digest": observation["response"]["output_digest"],
            "role": "researcher",
            "component_id": "primary.score",
            "adjudication": {
                "cycle": 1,
                "batch_command_id": batch_id,
                "candidate": raw,
                "selected_tip": tips["chain-1"],
                "pruned_by": [
                    {
                        "loser_chain": "chain-2",
                        "loser_hypothesis_id": "hyp-2",
                        "winner_chain": "chain-1",
                        "winner_hypothesis_id": "hyp-1",
                        "source_attempt_id": "adjudicate",
                        "debate_id": None,
                        "score_refs": ["out-2"],
                    }
                ],
            },
        },
    )
    return data


def _change_transition(data, key, mutate):
    row = next(
        row for row in data.transitions if key in json.loads(row["payload_json"])
    )
    payload = json.loads(row["payload_json"])
    mutate(payload)
    row["payload_json"] = json.dumps(payload)


def test_raw_batch_adjudication_and_selected_projection_are_independent():
    data = _fixture()
    before = copy.deepcopy(vars(data))
    assert data.audit() == []
    specs, outcomes, evidence = selected_fact_tips(
        data.facts, data.fact_payloads, data.transitions, {}
    )
    assert specs["primary.score"]["experiment_id"] == "exp-1"
    assert outcomes["primary.score"]["outcome_id"] == "out-1"
    assert evidence["primary.score"]["evidence_id"] == "evidence-1"
    assert vars(data) == before


@pytest.mark.parametrize("change", [None, "target", "direction", "parse_rule"])
def test_batch_measurement_uses_typed_decimal_equivalence_but_rejects_scientific_changes(change):
    from test_measurement_contract import declaration

    data = _fixture()
    submitted = declaration().to_dict()
    accepted = copy.deepcopy(submitted)
    submitted["metric"]["target"] = "-0.10"
    accepted["metric"]["target"] = "-0.1"
    if change == "target":
        accepted["metric"]["target"] = "-0.2"
    elif change == "direction":
        accepted["metric"]["direction"] = "MAXIMIZE"
    elif change == "parse_rule":
        accepted["parse_rule"] = "Use a different declared interpretation."
    data.attempt_payloads["synthesis"]["response"]["typed_candidate"]["hypotheses"][0]["candidate"]["measurement"] = submitted
    data.fact_payloads["fact-hyp-1"]["measurement"] = accepted
    before = copy.deepcopy(vars(data))
    issues = data.audit()
    if change is None:
        assert issues == []
    else:
        assert "batch_source_invalid" in {issue[0] for issue in issues}
    assert vars(data) == before


@pytest.mark.parametrize(
    "tamper",
    [
        "missing_marker",
        "missing_member",
        "duplicate_chain",
        "wrong_claim",
        "foreign_owner",
        "bad_source",
        "bad_parent",
        "fake_digest",
    ],
)
def test_batch_source_tampering_is_rejected(tamper):
    data = _fixture()
    if tamper == "missing_marker":
        _change_transition(
            data, "hypothesis_batch", lambda value: value.pop("hypothesis_batch")
        )
    elif tamper == "missing_member":
        _change_transition(
            data,
            "hypothesis_batch",
            lambda value: value["hypothesis_batch"]["chains"].pop("chain-2"),
        )
    elif tamper == "duplicate_chain":
        data.attempt_payloads["synthesis"]["response"]["typed_candidate"]["hypotheses"][
            1
        ]["chain_id"] = "chain-1"
    elif tamper == "wrong_claim":
        data.fact_payloads["fact-hyp-2"]["claim"] = "Unsubmitted scientific proposal."
    elif tamper == "foreign_owner":
        data.fact_payloads["fact-hyp-2"]["proposer_role"] = "foreign"
    elif tamper == "bad_source":
        data.attempts[0]["terminal_reason"] = "LOST"
    elif tamper == "bad_parent":
        data.fact_payloads["fact-hyp-2"]["parent_hypothesis_id"] = "hyp-1"
    else:
        _change_transition(
            data,
            "hypothesis_batch",
            lambda value: value.update(agent_output_digest="different"),
        )
    assert "batch_source_invalid" in {issue[0] for issue in data.audit()}


@pytest.mark.parametrize(
    "tamper",
    [
        "wrong_winner",
        "fake_tip",
        "failure_as_refutation",
        "partition",
        "missing_round",
        "foreign_score_ref",
        "fake_pruning",
        "open_analysis",
        "stale_context",
    ],
)
def test_adjudication_cannot_invent_or_replace_its_terminal_comparison_set(tamper):
    data = _fixture()

    def mutate(value):
        accepted = value["adjudication"]
        if tamper == "wrong_winner":
            accepted["selected_tip"]["chain_id"] = "chain-2"
        elif tamper == "fake_tip":
            accepted["selected_tip"]["outcome_id"] = "not-accepted"
        elif tamper == "partition":
            accepted["candidate"]["keep"].append("chain-2")
        elif tamper == "missing_round":
            accepted["candidate"]["verdicts"].pop()
        elif tamper == "foreign_score_ref":
            accepted["candidate"]["verdicts"][1]["evidence"] = ["foreign"]
        elif tamper == "fake_pruning":
            accepted["pruned_by"][0]["winner_hypothesis_id"] = "hyp-2"

    if tamper == "failure_as_refutation":
        data.fact_payloads["fact-outcome-2"].update(
            outcome="EXECUTION_FAILURE", metric_value=None
        )
    elif tamper == "open_analysis":
        next(row for row in data.attempts if row["attempt_id"] == "analysis-2")[
            "state"
        ] = "OPEN"
    elif tamper == "stale_context":
        data.attempt_payloads["adjudicate"]["request"]["input_artifacts"] = []
    else:
        _change_transition(data, "adjudication", mutate)
    assert "batch_adjudication_invalid" in {issue[0] for issue in data.audit()}


def test_selected_projection_does_not_use_later_completion_and_null_clears_selection():
    data = _fixture()
    data.facts.reverse()
    assert (
        selected_fact_tips(data.facts, data.fact_payloads, data.transitions, {})[1][
            "primary.score"
        ]["outcome_id"]
        == "out-1"
    )
    data.transition(
        "ACCEPT_ADJUDICATION",
        {"component_id": "primary.score", "adjudication": {"selected_tip": None}},
    )
    assert selected_fact_tips(data.facts, data.fact_payloads, data.transitions, {}) == (
        {},
        {},
        {},
    )


def _iteration():
    data = _fixture()
    data.transitions.pop()  # The next scientific iteration is still active, not adjudicated.
    data.attempts = [row for row in data.attempts if row["attempt_id"] != "adjudicate"]
    data.attempt_payloads.pop("adjudicate")
    data.attempt_payloads["analysis-1"]["response"]["typed_candidate"][
        "iteration_disposition"
    ] = "ITERATE"
    origin = {
        "iteration_of_experiment_id": "exp-1",
        "analysis_attempt_id": "analysis-1",
    }
    key = ResearchNodeKey(
        run_id="run-1",
        component_id="primary.score",
        expert_slot="researcher",
        cycle=1,
        node_kind=ResearchNodeKind.EXPERIMENT_EXECUTION,
        chain="chain-1",
        experiment_ordinal=2,
    )
    source, observation = data.agent(
        "execution-next",
        ResearchNodeKind.EXPERIMENT_EXECUTION,
        None,
        chain="chain-1",
        iteration=2,
        state="OPEN",
        inputs=(_input(key.command_id + "--iteration-context", origin),),
    )
    initial = next(
        json.loads(row["payload_json"])
        for row in data.transitions
        if row["workflow_id"] == "exp-1"
    )
    creation = {
        **initial,
        **origin,
        "chain_iteration": 2,
        "experiment_ordinal": 3,
        "source_submit_command_id": "submit-next",
        "source_agent_attempt_id": source["attempt_id"],
        "execution_node_command_id": key.command_id,
    }
    data.commands.append(
        {
            "run_id": "run-1",
            "command_id": "submit-next",
            "workflow_id": "exp-next",
            "command_type": "CREATE_EXPERIMENT",
            "response_status": "accepted",
            "request_json": json.dumps(
                {
                    "tool": "submit_experiment",
                    "source_request": observation["request"],
                    "execution_node_command_id": key.command_id,
                }
            ),
        }
    )
    data.transition(
        "CREATE_EXPERIMENT", creation, workflow="exp-next", kind="EXPERIMENT"
    )
    return data


def test_scientific_iteration_uses_accepted_comparable_analysis_while_execution_stays_open():
    assert _iteration().audit() == []


@pytest.mark.parametrize(
    "tamper",
    [
        "foreign_chain",
        "foreign_hypothesis",
        "missing_submit",
        "wrong_submit_owner",
        "missing_origin",
        "wrong_origin",
        "not_iterate",
        "failure_not_science",
        "open_analysis",
        "future_analysis",
        "stale_frozen_origin",
        "iteration_gap",
    ],
)
def test_iteration_causation_tampering_is_rejected(tamper):
    data = _iteration()
    row = data.transitions[-1]
    payload = json.loads(row["payload_json"])
    if tamper == "foreign_chain":
        payload["chain"] = "chain-2"
    elif tamper == "foreign_hypothesis":
        payload["hypothesis_id"] = "hyp-2"
    elif tamper == "missing_submit":
        data.commands.pop()
    elif tamper == "wrong_submit_owner":
        data.commands[-1]["workflow_id"] = "exp-2"
    elif tamper == "missing_origin":
        payload.pop("analysis_attempt_id")
    elif tamper == "wrong_origin":
        payload["iteration_of_experiment_id"] = "exp-2"
    elif tamper == "not_iterate":
        data.attempt_payloads["analysis-1"]["response"]["typed_candidate"][
            "iteration_disposition"
        ] = "COMPLETE"
    elif tamper == "failure_not_science":
        data.fact_payloads["fact-outcome-1"]["outcome"] = "EXECUTION_FAILURE"
    elif tamper == "open_analysis":
        next(item for item in data.attempts if item["attempt_id"] == "analysis-1")[
            "state"
        ] = "OPEN"
    elif tamper == "future_analysis":
        next(item for item in data.attempts if item["attempt_id"] == "analysis-1")[
            "ended_at"
        ] = data.clock + 1
    elif tamper == "stale_frozen_origin":
        data.attempt_payloads["execution-next"]["request"]["input_artifacts"] = []
    else:
        payload["chain_iteration"] = 3
    row["payload_json"] = json.dumps(payload)
    assert "batch_iteration_invalid" in {issue[0] for issue in data.audit()}


def _closed_cycle():
    data = _fixture()
    for identity, node, prior in (
        ("update", ResearchNodeKind.HYPOTHESIS_UPDATE, "adjudicate"),
        ("report", ResearchNodeKind.CYCLE_REPORT, "update"),
        ("evolve", ResearchNodeKind.SELF_EVOLVE, "report"),
    ):
        candidate = {"summary": "Complete.", "supporting_refs": [prior + "--response"]}
        if identity == "update":
            candidate["update_disposition"] = "TERMINATE_LINEAGE"
        data.agent(
            identity,
            node,
            candidate,
            inputs=(
                _input(prior + "--response", data.attempt_payloads[prior]["response"]),
            ),
        )
    data.transition("COMPLETE_RUN", {})["state"] = "COMPLETED"
    return data


def test_completed_cycle_requires_actual_report_and_self_evolution():
    assert _closed_cycle().audit() == []


@pytest.mark.parametrize(
    "tamper",
    [
        "missing_report",
        "missing_evolve",
        "open_evolve",
        "wrong_report_source",
        "evolve_before_report",
        "update_before_adjudication",
        "revise_with_budget",
        "fake_final_failure",
    ],
)
def test_completed_cycle_cannot_skip_report_or_self_evolution(tamper):
    data = _closed_cycle()
    if tamper.startswith("missing_"):
        identity = tamper.removeprefix("missing_")
        data.attempts = [row for row in data.attempts if row["attempt_id"] != identity]
        data.attempt_payloads.pop(identity)
    elif tamper == "open_evolve":
        next(row for row in data.attempts if row["attempt_id"] == "evolve")["state"] = (
            "OPEN"
        )
    elif tamper == "wrong_report_source":
        data.attempt_payloads["evolve"]["request"]["input_artifacts"] = []
    elif tamper == "evolve_before_report":
        next(row for row in data.attempts if row["attempt_id"] == "evolve")[
            "started_at"
        ] = 1
    elif tamper == "update_before_adjudication":
        next(row for row in data.attempts if row["attempt_id"] == "update")[
            "started_at"
        ] = 1
    elif tamper == "revise_with_budget":
        data.attempt_payloads["update"]["response"]["typed_candidate"][
            "update_disposition"
        ] = "REVISE_NEXT_CYCLE"
    else:
        next(row for row in data.attempts if row["attempt_id"] == "evolve")["state"] = (
            "OPEN"
        )
        data.transition(
            "AGENT_ATTEMPT_FINAL_FAILURE",
            {
                "agent_final_failure": True,
                "attempt_id": "evolve",
                "failed_task_command_id": data.attempt_payloads["evolve"]["request"][
                    "command_id"
                ],
                "role": "researcher",
                "component_id": "primary.score",
            },
        )
        data.transitions[-1]["state"] = "COMPLETED"
    assert "batch_closure_invalid" in {issue[0] for issue in data.audit()}


def _consumer():
    data = _closed_cycle()
    data.transitions.pop()
    goal = next(
        value
        for value in data.fact_payloads.values()
        if value["fact_type"] == "GoalDefined"
    )
    contract = goal["research_contract"]
    requirement = {
        "resource": "model-input",
        "produced_by_component": "primary.score",
        "logical_artifact": "model",
        "min_count": 1,
    }
    contract["components"].append(
        {
            **contract["components"][0],
            "id": "consumer.score",
            "owner": "consumer",
            "dependencies": ["primary.score"],
            "required_inputs": [
                {
                    "resource": requirement["resource"],
                    "produced_by": requirement["produced_by_component"],
                    "artifact": requirement["logical_artifact"],
                    "min_count": requirement["min_count"],
                }
            ],
        }
    )
    contract["roles"].append({**contract["roles"][0], "id": "consumer"})
    _, observation = data.agent(
        "consumer-execution",
        ResearchNodeKind.EXPERIMENT_EXECUTION,
        None,
        chain="consumer-chain",
        iteration=1,
        state="OPEN",
        role="consumer",
        component="consumer.score",
        inputs=(
            _input(
                "adjudicate--response", data.attempt_payloads["adjudicate"]["response"]
            ),
            _input(
                "consumer--expert-config", {"required_method_family_id": "family-1"}
            ),
        ),
    )
    spec = copy.deepcopy(data.fact_payloads["fact-spec-1"])
    spec.update(
        fact_id="consumer-spec",
        experiment_id="consumer-exp",
        execution_id="consumer-exec",
        component_id="consumer.score",
        hypothesis_id="consumer-hyp",
        upstream_outcomes=[
            {**requirement, "outcome_id": "out-1", "artifact_id": "artifact-1"}
        ],
    )
    spec["execution_contract"]["source_agent_attempt_id"] = observation["request"][
        "attempt_id"
    ]
    data.fact_payloads[spec["fact_id"]] = spec
    data.facts.append({**spec, "payload_json": json.dumps(spec)})
    return data


def test_consumer_freezes_the_adjudicated_family_and_exact_producer_outcome():
    data = _consumer()
    assert data.audit() == []


def test_preproposal_consumer_keeps_required_family_in_config_before_hypothesis_exists():
    data = _consumer()
    original = data.attempt_payloads["consumer-execution"]["request"]
    inputs = tuple(
        EmbeddedInput.from_dict(item) for item in original["input_artifacts"]
    )
    data.agent(
        "consumer-literature",
        ResearchNodeKind.LITERATURE_REVIEW,
        {"summary": "Inspect the selected producer."},
        role="consumer",
        component="consumer.score",
        family=None,
        inputs=inputs,
    )
    assert data.audit() == []
    # A same-command infrastructure retry preserves the original inputs.
    original = next(
        row for row in data.attempts if row["attempt_id"] == "consumer-execution"
    )
    retry = {
        **original,
        "attempt_id": "consumer-retry",
        "attempt_ordinal": 2,
        "started_at": data.clock + 2,
    }
    data.attempts.append(retry)
    observation = copy.deepcopy(data.attempt_payloads["consumer-execution"])
    observation["request"]["attempt_id"] = "consumer-retry"
    data.attempt_payloads["consumer-retry"] = observation
    assert data.audit() == []


@pytest.mark.parametrize(
    "tamper",
    [
        "missing_judge",
        "forged_response",
        "wrong_family",
        "wrong_config_family",
        "later_unselected_outcome",
        "wrong_artifact",
        "missing_binding",
        "fake_dependency",
        "before_report",
        "unclosed_new_batch",
    ],
)
def test_all_downstream_consumers_bind_the_accepted_selection(tamper):
    data = _consumer()
    request = data.attempt_payloads["consumer-execution"]["request"]
    spec = data.fact_payloads["consumer-spec"]
    if tamper == "missing_judge":
        request["input_artifacts"] = request["input_artifacts"][1:]
    elif tamper == "forged_response":
        request["input_artifacts"][0] = _input(
            "adjudicate--response", {"typed_candidate": {}}
        ).to_dict()
    elif tamper == "wrong_family":
        request["method_family_id"] = "unselected-family"
    elif tamper == "wrong_config_family":
        request["input_artifacts"][1] = _input(
            "consumer--expert-config", {"required_method_family_id": "wrong"}
        ).to_dict()
    elif tamper == "later_unselected_outcome":
        spec["upstream_outcomes"][0]["outcome_id"] = "out-2"
    elif tamper == "wrong_artifact":
        spec["upstream_outcomes"][0]["artifact_id"] = "artifact-2"
    elif tamper == "missing_binding":
        spec["upstream_outcomes"] = []
    elif tamper == "fake_dependency":
        request["input_artifacts"][0]["ref"] = "analysis-2--response"
    elif tamper == "before_report":
        next(row for row in data.attempts if row["attempt_id"] == "consumer-execution")[
            "started_at"
        ] -= 3
    else:
        row = data.transition(
            "ACCEPT_HYPOTHESIS",
            {
                "role": "researcher",
                "component_id": "primary.score",
                "hypothesis_batch": {"cycle": 2, "chains": {"new": "new-hyp"}},
            },
        )
        row["created_at"] = datetime.fromtimestamp(
            next(
                item
                for item in data.attempts
                if item["attempt_id"] == "consumer-execution"
            )["started_at"]
            - 0.1,
            timezone.utc,
        ).isoformat()
    assert "batch_dependency_binding_invalid" in {issue[0] for issue in data.audit()}
