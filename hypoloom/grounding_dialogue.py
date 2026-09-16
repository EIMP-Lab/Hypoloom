"""Run-scoped Grounding continuation over the existing HumanDecision records."""
from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, Mapping

from .control_protocol import ControlRequest, ControlResponse
from .expert_loop import AuxiliaryRequestKind, AuxiliaryTask
from .lifecycle import Command, HumanDecisionState, Machine, RunState, validate_transition
from .state_store import IdentityConflict, StateTransaction

if TYPE_CHECKING:
    from .agent_aci import AgentRequest
    from .workflow_host import WorkflowHost

_KIND = "grounding_clarification"
_SELECT = "SELECT * FROM workflow_human_decisions WHERE run_id=? AND json_extract(request_json,'$.kind')=? ORDER BY request_revision,decision_id"


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{field} must be nonempty text")
    return value


def _clarification(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"question", "rationale", "options"}:
        raise ValueError("Grounding clarification fields are not exact")
    options = value["options"]
    if not isinstance(options, list):
        raise ValueError("Grounding clarification options must be a list")
    return {"question": _text(value["question"], "question"),
        "rationale": _text(value["rationale"], "rationale"),
        "options": [_text(option, "option") for option in options]}


def _next_ordinal(rows: list[dict[str, Any]]) -> int:
    ordinal = 1
    for row in rows:
        request = json.loads(row["request_json"])
        if type(request.get("ordinal")) is not int or request["ordinal"] != ordinal:
            raise IdentityConflict("Grounding dialogue rounds are not contiguous")
        if row["state"] == "OPEN" or row["resolution"] == "CANCELLED":
            if row is not rows[-1]:
                raise IdentityConflict("Grounding dialogue continued past an unresolved round")
            return ordinal
        if row["resolution"] != "EDITED":
            raise IdentityConflict("Grounding dialogue has no operator answer")
        ordinal += 1
    return ordinal


class GroundingDialogue:
    """A delegate of the sole Host writer, not a second session or goal ledger."""

    def __init__(self, host: WorkflowHost) -> None:
        self.host = host

    def current_task(self, run_id: str) -> AuxiliaryTask:
        rows = list(self.host.store.inspect(_SELECT, (run_id, _KIND)))
        return AuxiliaryTask(kind=AuxiliaryRequestKind.GROUNDING, run_id=run_id,
            ordinal=_next_ordinal(rows))

    def history(self, run_id: str, before_ordinal: int) -> dict[str, Any]:
        if type(before_ordinal) is not int or before_ordinal < 1:
            raise ValueError("before_ordinal must be a positive integer")
        rows = list(self.host.store.inspect(_SELECT, (run_id, _KIND)))
        _next_ordinal(rows)
        rounds = []
        for row in rows:
            question = json.loads(row["request_json"])
            if question["ordinal"] >= before_ordinal:
                continue
            if row["state"] != "RESOLVED" or row["resolution"] != "EDITED":
                raise IdentityConflict("Grounding history contains an unanswered prior round")
            answer = json.loads(row["resolution_json"])
            rounds.append({"decision_id": row["decision_id"], "ordinal": question["ordinal"],
                **{name: question[name] for name in ("question", "rationale", "options")},
                "answer": _text(answer["answer"], "answer")})
        return {"run_id": run_id, "rounds": rounds}

    def open_question(self, tx: StateTransaction, request: AgentRequest,
        clarification: Mapping[str, Any]) -> str:
        question = _clarification(clarification)
        task = request.task_context
        if (request.schema_version < 15 or not isinstance(task, AuxiliaryTask)
            or task.kind is not AuxiliaryRequestKind.GROUNDING
            or request.owner_workflow_id != request.run_id
            or request.command_id != task.command_id or task.run_id != request.run_id):
            raise ValueError("clarification requires an ACI 15 run-owned Grounding round")
        rows = [dict(row) for row in tx.execute(_SELECT, (request.run_id, _KIND)).fetchall()]
        if task.ordinal != _next_ordinal(rows) or (rows and rows[-1]["state"] == "OPEN"):
            raise IdentityConflict("Grounding question is not the next conversation round")
        head = tx.latest_transition(request.run_id)
        if head is None or head["state"] != RunState.ACTIVE.value:
            raise ValueError("Grounding question requires an ACTIVE run")
        attempt = tx.get_attempt(request.attempt_id)
        if (attempt is None or attempt["state"] != "CLOSED" or attempt["terminal_reason"] != "SUCCEEDED"
            or attempt["kind"] != "AGENT" or attempt["run_id"] != request.run_id
            or attempt["workflow_id"] != request.run_id or attempt["command_id"] != request.command_id):
            raise IdentityConflict("Grounding question requires its successful CLOSED source Attempt")
        decision_id = request.attempt_id + "--clarification"
        payload = {"kind": _KIND, "ordinal": task.ordinal, "decision_id": decision_id,
            "human_decision_command": Command.OPEN_DECISION.value}
        transition = self.host._transition_record(tx, request.run_id, request.run_id, "RUN",
            Command.REQUEST_HUMAN, RunState.WAITING_HUMAN, payload=payload)
        validate_transition(Machine.HUMAN_DECISION, None, Command.OPEN_DECISION, HumanDecisionState.OPEN)
        tx.open_human_decision({"decision_id": decision_id, "run_id": request.run_id,
            "workflow_id": request.run_id, "family": "GOAL_SCOPE", "request_revision": head["revision"],
            "request": {"kind": _KIND, "ordinal": task.ordinal, "source_attempt_id": request.attempt_id,
                "source_command_id": request.command_id, **question}, "deadline": None,
            "command_id": transition["command_id"]})
        tx.append_transition(transition)
        return decision_id

    def answer(self, request: ControlRequest) -> ControlResponse:
        if request.command != "ANSWER" or set(request.payload) != {"run_id", "decision_id", "answer"}:
            raise ValueError("ANSWER payload fields are not exact")
        run_id = _text(request.payload["run_id"], "run_id")
        decision_id = _text(request.payload["decision_id"], "decision_id")
        answer = _text(request.payload["answer"], "answer")
        if type(request.expected_revision) is not int or request.expected_revision < 0:
            raise ValueError("ANSWER requires an exact expected_revision")

        def operation(tx: StateTransaction) -> Mapping[str, Any]:
            decision = tx.get_human_decision(decision_id)
            if decision is None or decision["run_id"] != run_id or decision["workflow_id"] != run_id:
                raise ValueError("Grounding decision does not belong to the requested run")
            question = json.loads(decision["request_json"])
            if decision["family"] != "GOAL_SCOPE" or question.get("kind") != _KIND:
                raise ValueError("decision is not a Grounding clarification")
            if decision["state"] != "OPEN":
                raise IdentityConflict("Grounding decision has already been resolved")
            head = tx.latest_transition(run_id)
            if head is None or head["state"] != RunState.WAITING_HUMAN.value:
                raise ValueError("Grounding answer requires its WAITING_HUMAN run")
            validate_transition(Machine.HUMAN_DECISION, HumanDecisionState.OPEN,
                Command.RESOLVE, HumanDecisionState.RESOLVED)
            tx.resolve_human_decision(decision_id, run_id=run_id, resolution="EDITED",
                resolution_payload={"answer": answer}, resolved_at=time.time())
            transition = self.host._transition_record(tx, run_id, run_id, "RUN",
                Command.RESOLVE_CONTINUE, RunState.ACTIVE, request.request_id,
                payload={"kind": _KIND, "ordinal": question["ordinal"], "decision_id": decision_id,
                    "human_decision_command": Command.RESOLVE.value})
            tx.append_transition(transition)
            return {"status": "accepted", "revision": transition["revision"],
                "result": {"run_id": run_id, "run_state": RunState.ACTIVE.value,
                    "decision_id": decision_id, "ordinal": question["ordinal"]}}

        stored = self.host.store.execute_command({"command_id": request.request_id,
            "run_id": run_id, "workflow_id": run_id, "expected_revision": request.expected_revision,
            "command_type": Command.RESOLVE_CONTINUE.value, "request": request.to_mapping()}, operation)
        return ControlResponse.accepted(request.request_id, stored.revision, stored.result or {})

    def cancel_pending(self, tx: StateTransaction, run_id: str) -> tuple[str, ...]:
        cancelled = []
        for row in tx.execute(_SELECT, (run_id, _KIND)).fetchall():
            if row["state"] != "OPEN":
                continue
            validate_transition(Machine.HUMAN_DECISION, HumanDecisionState.OPEN,
                Command.CANCEL_DECISION, HumanDecisionState.RESOLVED)
            tx.resolve_human_decision(row["decision_id"], run_id=run_id, resolution="CANCELLED",
                resolution_payload={"reason": "run_stopping"}, resolved_at=time.time())
            cancelled.append(row["decision_id"])
        return tuple(cancelled)
