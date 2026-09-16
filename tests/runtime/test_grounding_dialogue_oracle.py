"""Independent raw-record mutations for Grounding's operator conversation."""
from __future__ import annotations

import copy
import json

import pytest

from hypoloom.agent_aci import EmbeddedInput, canonical_json
from hypoloom.grounding_dialogue_oracle import audit_grounding_dialogue


def _input(ref, value):
    return EmbeddedInput.from_bytes(ref, "application/json", canonical_json(value)).to_dict()


def _background():
    return {"project_summary": "Use the existing held-out evaluation.", "codebase_map": [],
            "entrypoints": ["evaluate.py"], "how_to_run_experiment": "python evaluate.py",
            "metrics_available": ["accuracy"], "risks": [], "open_questions": []}


class Records:
    def __init__(self):
        self.attempts = []
        self.observations = {}
        self.decisions = []
        self.transitions = []
        self.commands = []
        self.history = []
        self.run_state = "ACTIVE"
        self.revision = 1
        self.clock = 10

    def transition(self, command, payload, *, command_id, state, previous_state):
        self.revision += 1
        row = {"run_id": "run", "workflow_id": "run", "workflow_kind": "RUN",
               "revision": self.revision, "command_type": command, "command_id": command_id,
               "state": state, "previous_state": previous_state, "payload_json": json.dumps(payload),
               "created_at": self.clock}
        self.transitions.append(row)
        self.run_state = state
        return row

    def round(self, ordinal, *, question=None):
        identity = f"grounding-{ordinal}"
        command_id = f"round-{ordinal}"
        self.clock += 10
        request = {"schema_version": 15, "run_id": "run", "attempt_id": identity,
                   "command_id": command_id, "command": "GROUNDING", "role": "researcher",
                   "component_id": "primary.researcher", "task_context": {"kind": "AUXILIARY",
                       "request_kind": "GROUNDING", "run_id": "run", "component_id": None,
                       "expert_slot": None, "cycle": None, "ordinal": ordinal},
                   "input_artifacts": [_input(command_id + "--grounding-dialogue",
                       {"run_id": "run", "rounds": copy.deepcopy(self.history)})]}
        request["immutable_context_refs"] = [item["ref"] for item in request["input_artifacts"]]
        candidate = {"task_kind": "GROUNDING", "supporting_refs": [], "web_sources": [],
                     "background": None if question else _background(), "clarification": question}
        row = {"attempt_id": identity, "run_id": "run", "workflow_id": "run", "command_id": command_id,
               "kind": "AGENT", "state": "CLOSED", "terminal_reason": "SUCCEEDED", "attempt_ordinal": 1,
               "started_at": self.clock, "ended_at": self.clock + 1}
        self.attempts.append(row)
        self.observations[identity] = {"request": request, "response": {"typed_candidate": candidate}}
        self.clock += 2
        if question is None:
            return row
        decision_id = f"decision-{ordinal}"
        request_value = {"kind": "grounding_clarification", "ordinal": ordinal,
                         "source_attempt_id": identity, "source_command_id": command_id, **question}
        decision = {"decision_id": decision_id, "run_id": "run", "workflow_id": "run", "family": "GOAL_SCOPE",
                    "request_revision": self.revision, "command_id": f"ask-{ordinal}", "deadline": None,
                    "request_json": json.dumps(request_value), "state": "OPEN", "resolution": None,
                    "resolution_json": None, "resolved_at": None}
        self.decisions.append(decision)
        self.transition("REQUEST_HUMAN", {"kind": "grounding_clarification", "ordinal": ordinal,
                        "decision_id": decision_id, "human_decision_command": "OPEN_DECISION"},
                        command_id=decision["command_id"], state="WAITING_HUMAN", previous_state="ACTIVE")
        return row

    def answer(self, answer):
        decision = self.decisions[-1]
        request = json.loads(decision["request_json"])
        self.clock += 3
        decision.update(state="RESOLVED", resolution="EDITED", resolution_json=json.dumps({"answer": answer}),
                        resolved_at=self.clock)
        command_id = "answer-" + str(request["ordinal"])
        self.commands.append({"command_id": command_id, "run_id": "run", "workflow_id": "run",
                              "command_type": "RESOLVE_CONTINUE", "response_status": "accepted",
                              "expected_revision": self.revision, "accepted_revision": self.revision + 1,
                              "request_json": json.dumps({"schema_version": 1, "request_id": command_id,
                                  "expected_revision": self.revision, "command": "ANSWER", "payload": {
                                      "run_id": "run", "decision_id": decision["decision_id"], "answer": answer}})})
        self.transition("RESOLVE_CONTINUE", {"kind": "grounding_clarification", "ordinal": request["ordinal"],
                        "decision_id": decision["decision_id"], "human_decision_command": "RESOLVE"},
                        command_id=command_id, state="ACTIVE", previous_state="WAITING_HUMAN")
        self.history.append({"decision_id": decision["decision_id"], "ordinal": request["ordinal"],
                             **{key: request[key] for key in ("question", "rationale", "options")}, "answer": answer})

    def expert(self):
        final = self.attempts[-1]
        identity = "expert"
        self.clock += 10
        request = {"schema_version": 15, "run_id": "run", "attempt_id": identity,
                   "command_id": "expert-command", "command": "HYPOTHESIS_PROPOSAL",
                   "task_context": {"kind": "RESEARCH_NODE"}, "input_artifacts": [
                       _input(final["attempt_id"] + "--response", self.observations[final["attempt_id"]]["response"])]}
        request["immutable_context_refs"] = [item["ref"] for item in request["input_artifacts"]]
        row = {"attempt_id": identity, "run_id": "run", "workflow_id": "run", "command_id": "expert-command",
               "kind": "AGENT", "state": "CLOSED", "terminal_reason": "SUCCEEDED", "started_at": self.clock,
               "ended_at": self.clock + 1, "attempt_ordinal": 1}
        self.attempts.append(row)
        self.observations[identity] = {"request": request}

    def audit(self, version=15):
        return audit_grounding_dialogue("run", self.attempts, self.observations, self.decisions,
            self.transitions, self.commands, run_state=self.run_state, aci_version=version)


def _question(text="Which held-out split?"):
    return {"question": text, "rationale": "The choice changes scientific comparison.", "options": ["Existing split"]}


def complete():
    records = Records()
    records.round(1, question=_question())
    records.answer("Keep the existing split, including its fixed validation set.")
    records.round(2, question=_question("Which final metric?"))
    records.answer("Use weighted-F1; keep the originally specified threshold.")
    records.round(3)
    records.expert()
    return records


@pytest.mark.parametrize("version", [15, "15", 16])
def test_two_questions_then_final_background_has_exact_causal_history(version):
    assert complete().audit(version) == []


def test_pending_question_is_valid_without_a_final_background():
    records = Records()
    records.round(1, question=_question())
    assert records.audit() == []


def test_old_versions_do_not_acquire_dialogue_semantics():
    records = complete()
    records.decisions.clear()
    assert records.audit(14) == []


def test_review_after_final_background_has_no_research_task_context():
    records = complete()
    request = records.observations["expert"]["request"]
    request.update(command="ANSWER_RENDEZVOUS", task_context=None)
    assert records.audit() == []


@pytest.mark.parametrize("damage", ["question", "source", "answer-command", "answer-history", "missing-history",
    "round-gap", "duplicate-final", "question-as-background", "early-expert", "missing-decision",
    "wrong-scope", "request-revision", "missing-resolution", "final-has-question", "late-answer",
    "changed-transport", "extra-grounding-after-final", "forged-background", "orphan-human-transition"])
def test_grounding_dialogue_rejects_changed_ownership_causation_and_result_sources(damage):
    records = complete()
    if damage in {"question", "source"}:
        value = json.loads(records.decisions[0]["request_json"])
        value["question" if damage == "question" else "source_attempt_id"] = "changed"
        records.decisions[0]["request_json"] = json.dumps(value)
    elif damage == "answer-command":
        value = json.loads(records.commands[0]["request_json"])
        value["payload"]["answer"] = "A different answer"
        records.commands[0]["request_json"] = json.dumps(value)
    elif damage == "answer-history":
        item = records.observations["grounding-2"]["request"]["input_artifacts"][0]
        history = {"run_id": "run", "rounds": copy.deepcopy(records.history[:1])}
        history["rounds"][0]["answer"] = "Choose the easiest split"
        item.update(_input(item["ref"], history))
    elif damage == "missing-history":
        records.observations["grounding-2"]["request"]["input_artifacts"] = []
    elif damage == "round-gap":
        records.observations["grounding-2"]["request"]["task_context"]["ordinal"] = 4
    elif damage == "duplicate-final":
        records.attempts.append({**records.attempts[2], "attempt_id": "another-final", "attempt_ordinal": 2})
        records.observations["another-final"] = copy.deepcopy(records.observations["grounding-3"])
        records.observations["another-final"]["request"]["attempt_id"] = "another-final"
    elif damage == "question-as-background":
        request = records.observations["expert"]["request"]
        request["input_artifacts"] = [_input("grounding-1--response", records.observations["grounding-1"]["response"])]
        request["immutable_context_refs"] = ["grounding-1--response"]
    elif damage == "early-expert":
        records.attempts[-1].update(started_at=records.attempts[2]["started_at"] - 2, ended_at=records.attempts[2]["started_at"] - 1)
    elif damage == "missing-decision":
        records.decisions.pop(0)
    elif damage == "wrong-scope":
        records.decisions[0]["workflow_id"] = "other"
    elif damage == "request-revision":
        records.decisions[0]["request_revision"] += 1
    elif damage == "missing-resolution":
        records.transitions.pop(1)
    elif damage == "final-has-question":
        records.observations["grounding-3"]["response"]["typed_candidate"]["clarification"] = _question()
    elif damage == "late-answer":
        records.decisions[0]["resolved_at"] = records.attempts[1]["started_at"] + 1
    elif damage == "changed-transport":
        records.observations["grounding-2"]["request"]["role"] = "other"
    elif damage == "extra-grounding-after-final":
        records.round(4)
    elif damage == "forged-background":
        item = records.observations["expert"]["request"]["input_artifacts"][0]
        item.update(_input(item["ref"], records.observations["grounding-1"]["response"]))
    elif damage == "orphan-human-transition":
        row = copy.deepcopy(records.transitions[0])
        value = json.loads(row["payload_json"])
        value["decision_id"] = "missing-decision"
        row["payload_json"] = json.dumps(value)
        records.transitions.append(row)
    assert records.audit(), damage


def test_no_effects_may_run_while_waiting_and_terminal_keeps_no_open_question():
    records = Records()
    records.round(1, question=_question())
    records.attempts.append({"attempt_id": "unwanted-process", "kind": "PROCESS", "run_id": "run",
                            "state": "OPEN", "started_at": records.clock + 1, "ended_at": None})
    assert records.audit()
    records.attempts.pop()
    records.run_state = "CANCELLED"
    assert records.audit()


def test_cancelled_question_requires_a_real_stop_and_cannot_supply_a_next_answer():
    records = Records()
    records.round(1, question=_question())
    decision = records.decisions[0]
    records.clock += 2
    decision.update(state="RESOLVED", resolution="CANCELLED", resolution_json=json.dumps({"reason": "run_stopping"}),
                    resolved_at=records.clock)
    assert records.audit()
    records.transition("CANCEL", {"stop_disposition": "CANCELLED", "cause": "operator"},
                       command_id="cancel", state="STOPPING", previous_state="WAITING_HUMAN")
    assert records.audit() == []


def test_retries_in_one_round_reuse_the_exact_history_without_another_question():
    records = complete()
    failed = {**records.attempts[1], "attempt_id": "round-two-failed", "terminal_reason": "FAILED",
              "started_at": records.attempts[1]["started_at"] - 2, "ended_at": records.attempts[1]["started_at"] - 1}
    records.attempts[1]["attempt_ordinal"] = 2
    records.attempts.append(failed)
    value = copy.deepcopy(records.observations["grounding-2"])
    value.pop("response")
    value["request"]["attempt_id"] = failed["attempt_id"]
    records.observations[failed["attempt_id"]] = value
    assert records.audit() == []
