"""Safe-point pause reasons and operator notes from existing control history."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping


def supports_participant_controls(version: Any) -> bool:
    match = re.fullmatch(r"hypoloom-lifecycle-v([0-9]+)", version) if isinstance(version, str) else None
    return match is not None and int(match[1]) >= 8


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{field} must be nonempty text")
    return value


@dataclass(frozen=True)
class ParticipantScope:
    role: str | None = None
    component_id: str | None = None
    chain: str | None = None

    def __post_init__(self):
        for key in ("role", "component_id"):
            value = getattr(self, key)
            if value is not None: _text(value, key)
        if self.chain is not None and (not isinstance(self.chain, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", self.chain) is None):
            raise ValueError("chain must be a safe nonempty identity")
        if (self.component_id is not None or self.chain is not None) and self.role is None:
            raise ValueError("component/chain control requires its role")
        if self.chain is not None and self.component_id is None:
            raise ValueError("chain control requires its component")

    def contains(self, other: ParticipantScope) -> bool:
        return all(getattr(self, key) is None or getattr(self, key) == getattr(other, key)
                   for key in ("role", "component_id", "chain"))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ParticipantScope:
        if not isinstance(value, Mapping) or set(value) != {"role", "component_id", "chain"}:
            raise ValueError("participant scope fields are not exact")
        return cls(**value)


@dataclass(frozen=True)
class PauseReason:
    scope: ParticipantScope
    reason: str
    command_id: str


@dataclass(frozen=True)
class OperatorNote:
    scope: ParticipantScope
    command_id: str
    text: str


@dataclass(frozen=True)
class ParticipantControls:
    pauses: tuple[PauseReason, ...] = ()
    notes: tuple[OperatorNote, ...] = ()

    def reasons_for(self, participant: ParticipantScope) -> tuple[str, ...]:
        return tuple(sorted({item.reason for item in self.pauses if item.scope.contains(participant)}))

    def blocks(self, participant: ParticipantScope, next_command: str) -> bool:
        return bool(self.reasons_for(participant)) and next_command in {
            "EXPERIMENT_DESIGN_IMPLEMENT", "CREATE_EXPERIMENT", "LITERATURE_REVIEW"}

    def pending_notes(self, participant: ParticipantScope, bound_ids: Iterable[str]) -> tuple[OperatorNote, ...]:
        consumed = set(bound_ids)
        return tuple(item for item in self.notes if item.scope.contains(participant) and item.command_id not in consumed)

    def to_dict(self) -> dict[str, Any]:
        # "pause requested" is a dispatch restriction, not Process liveness.
        return {"pause_requested": [asdict(item) for item in self.pauses],
                "operator_notes": [asdict(item) for item in self.notes]}


def fold_participant_controls(transitions: Iterable[Mapping[str, Any]], roles: Iterable[str]) -> ParticipantControls:
    """Replay durable run transitions; each independent reason clears separately."""
    roster = tuple(roles)
    if not roster or len(set(roster)) != len(roster): raise ValueError("control roster requires unique roles")
    for role in roster: _text(role, "role")
    pauses: dict[tuple[ParticipantScope, str], PauseReason] = {}
    notes = []
    for row in sorted(transitions, key=lambda item: (item["workflow_id"], int(item["revision"]))):
        payload = row.get("payload")
        if payload is None: payload = json.loads(row.get("payload_json") or "{}")
        control = payload.get("participant_control")
        if control is None: continue
        if not isinstance(control, Mapping): raise ValueError("participant control must be an object")
        action = control.get("action")
        if action not in {"pause", "resume", "inject"}: raise ValueError("unknown participant control action")
        expected = {"action", "scope", "reason"} | ({"text"} if action == "inject" else set())
        if set(control) != expected: raise ValueError("participant control fields are not exact")
        scope = ParticipantScope.from_dict(control["scope"])
        if scope.role is not None and scope.role not in roster: raise ValueError("participant control names an unknown role")
        reason = _text(control["reason"], "pause reason")
        command_id = _text(row["command_id"], "control command id")
        if action == "pause":
            targets = (scope,) if scope.role is not None else tuple(ParticipantScope(role) for role in roster)
            for target in targets: pauses[(target, reason)] = PauseReason(target, reason, command_id)
        elif action == "resume":
            pauses = {key: value for key, value in pauses.items()
                      if value.reason != reason or not scope.contains(value.scope)}
        else:
            if reason != "operator": raise ValueError("injected notes must be operator instructions")
            text = _text(control["text"], "operator note")
            notes.append(OperatorNote(scope, command_id, text))
    return ParticipantControls(tuple(pauses.values()), tuple(notes))
