"""Independent causal checks over Grounding Attempts and HumanDecision rows.

The existing response, decision, command and continuation records are the only
sources. This reader imports no Host, dialogue reducer or scientific evaluator.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


DialogueIssue = tuple[str, str, tuple[str, ...]]
_BACKGROUND = {"project_summary", "codebase_map", "entrypoints", "how_to_run_experiment",
               "metrics_available", "risks", "open_questions"}
_CANDIDATE = {"task_kind", "supporting_refs", "background", "web_sources", "clarification"}
_QUESTION = {"question", "rationale", "options"}
_REQUEST = {"kind", "ordinal", "source_attempt_id", "source_command_id", *_QUESTION}
_TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _version(value: object) -> int:
    return int(value) if type(value) is int or isinstance(value, str) and value.isdigit() else 0


def _object(value: object) -> Mapping[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    _need(isinstance(value, Mapping), "dialogue record is not an object")
    return value


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\x00" not in value


def _time(value: object) -> float:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp()
    _need(type(value) in {int, float}, "dialogue record lacks its actual time")
    return float(value)


def _history_input(request: Mapping[str, Any]) -> Mapping[str, Any]:
    items = [item for item in request.get("input_artifacts", ())
             if str(item.get("ref", "")).endswith("--grounding-dialogue")]
    _need(len(items) == 1 and items[0].get("media_type") == "application/json",
          "Grounding round lacks one exact dialogue input")
    _need(items[0]["ref"] in request.get("immutable_context_refs", ()),
          "Grounding dialogue input is not an admitted context reference")
    return _object(json.loads(base64.b64decode(items[0]["content_base64"], validate=True)))


def _successful(row: Mapping[str, Any]) -> bool:
    return row.get("state") == "CLOSED" and row.get("terminal_reason") == "SUCCEEDED"


def audit_grounding_dialogue(run_id: str, attempts: Sequence[Mapping[str, Any]],
        observations: Mapping[str, Mapping[str, Any]], decisions: Sequence[Mapping[str, Any]],
        transitions: Sequence[Mapping[str, Any]], commands: Sequence[Mapping[str, Any]], *,
        run_state: str | None, aci_version: object) -> list[DialogueIssue]:
    if _version(aci_version) < 15:
        return []
    try:
        _audit(run_id, [dict(row) for row in attempts], observations,
               [dict(row) for row in decisions], [dict(row) for row in transitions],
               [dict(row) for row in commands], run_state)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return [("grounding_dialogue_invalid", str(exc), (run_id,))]
    return []


def _audit(run_id, attempts, observations, decisions, transitions, commands, run_state):
    events = [(row, _object(row.get("payload", row.get("payload_json")))) for row in transitions]
    by_command = {row["command_id"]: row for row in commands}
    rounds = {}
    for row in attempts:
        request = observations.get(row["attempt_id"], {}).get("request", {})
        if row["kind"] != "AGENT" or request.get("command") != "GROUNDING":
            continue
        task = request.get("task_context", {})
        ordinal = task.get("ordinal")
        _need(_version(request.get("schema_version")) >= 15 and row["run_id"] == request["run_id"] == run_id
              and row["workflow_id"] == run_id and row["attempt_id"] == request["attempt_id"]
              and row["command_id"] == request["command_id"]
              and task.get("kind") == "AUXILIARY" and task.get("request_kind") == "GROUNDING"
              and task.get("run_id") == run_id and type(ordinal) is int and ordinal > 0
              and all(task.get(key) is None for key in ("component_id", "expert_slot", "cycle")),
              "Grounding round differs from its actual run-scoped Attempt/command")
        rounds.setdefault(ordinal, []).append((row, request))
    selected_decisions = []
    for decision in decisions:
        value = _object(decision["request_json"])
        if value.get("kind") == "grounding_clarification":
            selected_decisions.append((decision, value))
    _need(not selected_decisions or rounds, "Grounding decision lacks a source conversation")
    decision_ids = {row["decision_id"] for row, _ in selected_decisions}
    _need(all(value.get("decision_id") in decision_ids for row, value in events
              if value.get("kind") == "grounding_clarification"),
          "Grounding human transition has no corresponding decision")
    if not rounds:
        return
    _need(sorted(rounds) == list(range(1, max(rounds) + 1)), "Grounding round ordinals are not contiguous from one")
    owners = {(request.get("role"), request.get("component_id")) for group in rounds.values() for _, request in group}
    _need(len(owners) == 1, "Grounding conversation changed its transport owner")
    questions, finals, successful = {}, [], {}
    for ordinal, group in rounds.items():
        _need(len({row["command_id"] for row, _ in group}) == 1,
              "one Grounding round has multiple command identities")
        accepted = [(row, request) for row, request in group if _successful(row)]
        _need(len(accepted) <= 1, "Grounding round has multiple successful outputs")
        if not accepted:
            continue
        row, request = accepted[0]
        candidate = _object(observations[row["attempt_id"]]["response"]["typed_candidate"])
        fields = _CANDIDATE | ({"acceptance"} if _version(request.get("schema_version")) >= 16 else set())
        _need(set(candidate) == fields and candidate.get("task_kind") == "GROUNDING",
              "Grounding result is not a versioned clarification or final background")
        question, background = candidate["clarification"], candidate["background"]
        if question is not None:
            _need(isinstance(question, Mapping) and set(question) == _QUESTION and background is None
                  and candidate.get("acceptance") is None
                  and _text(question["question"]) and _text(question["rationale"])
                  and isinstance(question["options"], list) and all(_text(item) for item in question["options"]),
                  "Grounding question must have exact text/options and no completed background")
            questions[ordinal] = (row, request, question)
        else:
            _need(isinstance(background, Mapping) and set(background) == _BACKGROUND,
                  "completed Grounding lacks its seven-field background")
            finals.append((row, request))
        successful[ordinal] = row
        _need(all(other["attempt_id"] == row["attempt_id"] or _time(other["started_at"]) < _time(row["ended_at"])
                  for other, _ in group), "Grounding retried an already successful round")
    _need(len(finals) <= 1, "Grounding conversation has multiple final backgrounds")
    if finals:
        _need(finals[0][1]["task_context"]["ordinal"] == max(rounds), "Grounding continued after its final background")
    _need(len(selected_decisions) == len(questions), "Grounding questions and human decisions are not one-to-one")
    answered = {}
    for ordinal, (source, source_request, question) in questions.items():
        matches = [(row, value) for row, value in selected_decisions if value.get("ordinal") == ordinal]
        _need(len(matches) == 1, "Grounding question lacks one human decision")
        decision, value = matches[0]
        expected = {"kind": "grounding_clarification", "ordinal": ordinal,
                    "source_attempt_id": source["attempt_id"], "source_command_id": source["command_id"], **question}
        _need(set(value) == _REQUEST and value == expected
              and decision["run_id"] == decision["workflow_id"] == run_id
              and decision["family"] == "GOAL_SCOPE" and decision["deadline"] is None,
              "human decision differs from its actual Grounding question or owner")
        opened = [(row, payload) for row, payload in events if row.get("command_type") == "REQUEST_HUMAN"
                  and payload.get("decision_id") == decision["decision_id"]]
        _need(len(opened) == 1, "Grounding decision lacks its unique REQUEST_HUMAN transition")
        opening, payload = opened[0]
        expected_event = {"kind": "grounding_clarification", "ordinal": ordinal,
                          "decision_id": decision["decision_id"], "human_decision_command": "OPEN_DECISION"}
        _need(payload == expected_event and opening["command_id"] == decision["command_id"]
              and opening["run_id"] == opening["workflow_id"] == run_id and opening["workflow_kind"] == "RUN"
              and opening["state"] == "WAITING_HUMAN" and opening["previous_state"] == "ACTIVE"
              and opening["revision"] == decision["request_revision"] + 1,
              "Grounding question transition changed its decision, revision or WAITING_HUMAN boundary")
        resolved = [(row, payload) for row, payload in events if row.get("command_type") == "RESOLVE_CONTINUE"
                    and payload.get("decision_id") == decision["decision_id"]]
        end = float("inf")
        if decision["state"] == "OPEN":
            _need(not resolved and all(decision.get(key) is None for key in ("resolution", "resolution_json", "resolved_at"))
                  and run_state not in _TERMINAL and run_state in {"WAITING_HUMAN", "STOPPING"},
                  "open Grounding question has an answer or lacks its waiting/stopping owner")
        else:
            _need(decision["state"] == "RESOLVED", "Grounding decision has an unknown state")
            resolution = _object(decision["resolution_json"])
            end = _time(decision["resolved_at"])
            _need(end >= _time(source["ended_at"]), "Grounding answer predates its question")
            if decision["resolution"] == "CANCELLED":
                _need(not resolved and resolution == {"reason": "run_stopping"}
                      and any(row["workflow_id"] == run_id and row["state"] == "STOPPING"
                              and row["revision"] > opening["revision"] for row, _ in events),
                      "cancelled Grounding question lacks a real stop transition")
            else:
                _need(decision["resolution"] == "EDITED" and set(resolution) == {"answer"} and _text(resolution["answer"])
                      and len(resolved) == 1, "Grounding answer lacks one explicit EDITED resolution and continuation")
                continuation, continued = resolved[0]
                _need(continued == {**expected_event, "human_decision_command": "RESOLVE"}
                      and continuation["run_id"] == continuation["workflow_id"] == run_id
                      and continuation["state"] == "ACTIVE" and continuation["previous_state"] == "WAITING_HUMAN"
                      and continuation["revision"] > opening["revision"],
                      "Grounding answer continuation changed its owner, question or state")
                command = by_command[continuation["command_id"]]
                control = _object(command["request_json"])
                _need(command["run_id"] == command["workflow_id"] == run_id and command["response_status"] == "accepted"
                      and command["command_type"] == "RESOLVE_CONTINUE" and control.get("command") == "ANSWER"
                      and control.get("request_id") == continuation["command_id"]
                      and control.get("payload") == {"run_id": run_id, "decision_id": decision["decision_id"], "answer": resolution["answer"]}
                      and command["accepted_revision"] == continuation["revision"]
                      and type(control.get("expected_revision")) is int
                      and control["expected_revision"] == command["expected_revision"] == continuation["revision"] - 1,
                      "Grounding answer differs from its accepted revision-bound operator command")
                answered[ordinal] = ({"decision_id": decision["decision_id"], "ordinal": ordinal,
                                      **question, "answer": resolution["answer"]}, end)
        start = _time(source["ended_at"])
        _need(not any(row["attempt_id"] != source["attempt_id"] and _time(row["started_at"]) < end
                      and (row["state"] == "OPEN" or _time(row["ended_at"]) > start) for row in attempts),
              "an Agent or Process remained active while Grounding awaited a human answer")
    for ordinal, group in rounds.items():
        _need(all(index in answered for index in range(1, ordinal)),
              "Grounding advanced past a failed, final or unanswered round")
        expected = {"run_id": run_id, "rounds": [answered[index][0] for index in range(1, ordinal)]}
        for row, request in group:
            _need(_history_input(request) == expected, "Grounding input differs from complete persisted prior questions and answers")
            _need(all(answered[index][1] <= _time(row["started_at"]) for index in range(1, ordinal)),
                  "Grounding resumed before its operator answers were recorded")
    question_refs = {row["attempt_id"] + "--response" for row, _, _ in questions.values()}
    final_ref = None if not finals else finals[0][0]["attempt_id"] + "--response"
    for row in attempts:
        request = observations.get(row["attempt_id"], {}).get("request", {})
        command = request.get("command")
        if row["kind"] == "AGENT" and (command in {"GROUNDING", "FINAL_REPORT"}
                or (request.get("task_context") or {}).get("request_kind") == "GROUNDING_QUERY_PLAN"):
            continue
        _need(finals and _time(finals[0][0]["ended_at"]) <= _time(row["started_at"]),
              "expert research or a Process started before final Grounding background")
        _need(not question_refs.intersection(item.get("ref") for item in request.get("input_artifacts", ())),
              "a Grounding question was supplied as completed expert background")
        for item in request.get("input_artifacts", ()):
            if item.get("ref") == final_ref:
                _need(item["ref"] in request.get("immutable_context_refs", ())
                      and _object(json.loads(base64.b64decode(item["content_base64"], validate=True)))
                      == observations[finals[0][0]["attempt_id"]]["response"],
                      "expert background differs from the actual final Grounding response")
    if run_state in _TERMINAL:
        _need(not any(row["state"] == "OPEN" for row in attempts + decisions),
              "terminal dialogue run retains an OPEN Attempt or human decision")
