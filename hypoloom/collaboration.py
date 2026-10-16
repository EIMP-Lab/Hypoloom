"""Bounded typed expert rendezvous values with no workflow authority."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping


class ExpertSignalKind(str, Enum):
    HELP = "HELP"
    DEBATE = "DEBATE"


class RendezvousDisposition(str, Enum):
    ANSWERED = "ANSWERED"
    DECLINED = "DECLINED"
    NO_TARGET = "NO_TARGET"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    INTEGRITY_REJECTED = "INTEGRITY_REJECTED"


def _text(value: object, name: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be non-empty bounded trimmed text")
    return value


def _refs(value: object, name: str, *, empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or (not empty and not value) or len(value) > 16:
        raise ValueError(f"{name} must be a bounded array")
    result = tuple(_text(item, f"{name} item", maximum=512) for item in value)
    if len(set(result)) != len(result):
        raise ValueError(f"{name} must not contain duplicates")
    return result


def _strict(value: Mapping[str, Any], keys: set[str], name: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    unknown, missing = set(value) - keys, keys - set(value)
    if unknown or missing:
        raise ValueError(f"{name} unknown={sorted(unknown)!r} missing={sorted(missing)!r}")


@dataclass(frozen=True, slots=True, kw_only=True)
class ExpertSignal:
    schema_version: int
    kind: ExpertSignalKind
    target_role_hint: str | None
    question: str
    context_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("ExpertSignal schema_version must be 1")
        if not isinstance(self.kind, ExpertSignalKind):
            raise ValueError("ExpertSignal kind is unknown")
        if self.target_role_hint is not None:
            _text(self.target_role_hint, "target_role_hint", maximum=128)
        _text(self.question, "question")
        object.__setattr__(self, "context_refs", _refs(self.context_refs, "context_refs"))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "kind": self.kind.value,
                "target_role_hint": self.target_role_hint, "question": self.question,
                "context_refs": list(self.context_refs)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ExpertSignal:
        keys = {"schema_version", "kind", "target_role_hint", "question", "context_refs"}
        _strict(value, keys, "ExpertSignal")
        try:
            return cls(schema_version=value["schema_version"], kind=ExpertSignalKind(value["kind"]),
                       target_role_hint=value["target_role_hint"], question=value["question"],
                       context_refs=_refs(value["context_refs"], "context_refs"))
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and "unknown=" in str(exc):
                raise
            raise ValueError(f"invalid ExpertSignal: {exc}") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class RendezvousRequest:
    schema_version: int
    rendezvous_id: str
    run_id: str
    requester_role: str
    requester_slot: int
    target_role: str
    source_attempt_id: str
    kind: ExpertSignalKind
    question: str
    context_refs: tuple[str, ...]
    round: int
    max_rounds: int

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("RendezvousRequest schema_version must be 1")
        for name in ("rendezvous_id", "run_id", "requester_role", "target_role", "source_attempt_id"):
            _text(getattr(self, name), name, maximum=256)
        if self.target_role == self.requester_role:
            raise ValueError("target_role must be a peer role")
        if isinstance(self.requester_slot, bool) or not isinstance(self.requester_slot, int) or not 1 <= self.requester_slot <= 3:
            raise ValueError("requester_slot must be 1..3")
        if not isinstance(self.kind, ExpertSignalKind):
            raise ValueError("RendezvousRequest kind is unknown")
        _text(self.question, "question")
        object.__setattr__(self, "context_refs", _refs(self.context_refs, "context_refs"))
        if self.round != 1 or self.max_rounds != 1:
            raise ValueError("rendezvous is limited to one round")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "rendezvous_id": self.rendezvous_id,
                "run_id": self.run_id, "requester_role": self.requester_role,
                "requester_slot": self.requester_slot, "target_role": self.target_role,
                "source_attempt_id": self.source_attempt_id, "kind": self.kind.value,
                "question": self.question, "context_refs": list(self.context_refs),
                "round": self.round, "max_rounds": self.max_rounds}

    def terminal_reply(self, disposition: RendezvousDisposition, responder_role: str, summary: str) -> RendezvousReply:
        return RendezvousReply(schema_version=1, rendezvous_id=self.rendezvous_id, kind=self.kind,
                               disposition=disposition, responder_role=responder_role,
                               summary=summary, supporting_refs=())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RendezvousRequest:
        keys = {"schema_version", "rendezvous_id", "run_id", "requester_role",
                "requester_slot", "target_role", "source_attempt_id", "kind", "question",
                "context_refs", "round", "max_rounds"}
        _strict(value, keys, "RendezvousRequest")
        try:
            return cls(**{**dict(value), "kind": ExpertSignalKind(value["kind"]),
                          "context_refs": _refs(value["context_refs"], "context_refs")})
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and "unknown=" in str(exc):
                raise
            raise ValueError(f"invalid RendezvousRequest: {exc}") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class RendezvousReply:
    schema_version: int
    rendezvous_id: str
    kind: ExpertSignalKind
    disposition: RendezvousDisposition
    responder_role: str
    summary: str
    supporting_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("RendezvousReply schema_version must be 1")
        _text(self.rendezvous_id, "rendezvous_id", maximum=256)
        if not isinstance(self.kind, ExpertSignalKind) or not isinstance(self.disposition, RendezvousDisposition):
            raise ValueError("RendezvousReply enum is unknown")
        _text(self.responder_role, "responder_role", maximum=128)
        _text(self.summary, "summary")
        object.__setattr__(self, "supporting_refs", _refs(self.supporting_refs, "supporting_refs", empty=True))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "rendezvous_id": self.rendezvous_id,
                "kind": self.kind.value, "disposition": self.disposition.value,
                "responder_role": self.responder_role, "summary": self.summary,
                "supporting_refs": list(self.supporting_refs)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RendezvousReply:
        keys = {"schema_version", "rendezvous_id", "kind", "disposition",
                "responder_role", "summary", "supporting_refs"}
        _strict(value, keys, "RendezvousReply")
        try:
            return cls(schema_version=value["schema_version"], rendezvous_id=value["rendezvous_id"],
                       kind=ExpertSignalKind(value["kind"]),
                       disposition=RendezvousDisposition(value["disposition"]),
                       responder_role=value["responder_role"], summary=value["summary"],
                       supporting_refs=_refs(value["supporting_refs"], "supporting_refs", empty=True))
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and "unknown=" in str(exc):
                raise
            raise ValueError(f"invalid RendezvousReply: {exc}") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class RendezvousOrigin:
    rendezvous_id: str
    requester_role: str
    requester_slot: int
    source_attempt_id: str
    target_role: str | None
    signal: ExpertSignal
    component_id: str | None = None
    phase: str = "optional"

    def __post_init__(self) -> None:
        for name in ("rendezvous_id", "requester_role", "source_attempt_id"):
            _text(getattr(self, name), name, maximum=256)
        if isinstance(self.requester_slot, bool) or not isinstance(self.requester_slot, int) or not 1 <= self.requester_slot <= 3:
            raise ValueError("requester_slot must be 1..3")
        if self.target_role is not None:
            _text(self.target_role, "target_role", maximum=128)
        if not isinstance(self.signal, ExpertSignal):
            raise ValueError("signal must be an ExpertSignal")
        if self.component_id is not None:
            _text(self.component_id, "component_id", maximum=256)
        if self.phase not in {"optional", "preflight", "analyze"}:
            raise ValueError("rendezvous phase is unknown")

    def request(self, run_id: str) -> RendezvousRequest:
        target_role = self.target_role
        if target_role is None:
            raise ValueError("a no-target origin cannot dispatch a request")
        return RendezvousRequest(schema_version=1, rendezvous_id=self.rendezvous_id, run_id=run_id,
                                 requester_role=self.requester_role, requester_slot=self.requester_slot,
                                 target_role=target_role, source_attempt_id=self.source_attempt_id,
                                 kind=self.signal.kind, question=self.signal.question,
                                 context_refs=self.signal.context_refs, round=1, max_rounds=1)

    def terminal_reply(self, disposition: RendezvousDisposition, summary: str, responder_role: str = "WorkflowHost") -> RendezvousReply:
        return RendezvousReply(schema_version=1, rendezvous_id=self.rendezvous_id, kind=self.signal.kind,
                               disposition=disposition, responder_role=responder_role,
                               summary=summary, supporting_refs=())


def derive_rendezvous_records(
    rows: Iterable[tuple[str, Mapping[str, Any]]],
) -> tuple[tuple[RendezvousOrigin, ...], dict[str, RendezvousRequest], dict[str, RendezvousReply]]:
    """Project the canonical Run transition stream into ephemeral rendezvous views."""

    origins: dict[str, RendezvousOrigin] = {}
    requests: dict[str, RendezvousRequest] = {}
    replies: dict[str, RendezvousReply] = {}
    for command, payload in rows:
        if command == "ACCEPT_HYPOTHESIS" and payload.get("expert_signal") is not None:
            origin = RendezvousOrigin(
                rendezvous_id=payload["rendezvous_id"],
                requester_role=payload["role"],
                requester_slot=payload["slot_ordinal"],
                source_attempt_id=payload["agent_attempt_id"],
                target_role=payload["target_role"],
                signal=ExpertSignal.from_dict(payload["expert_signal"]),
                component_id=payload.get("component_id"),
            )
            if origin.rendezvous_id in origins:
                raise ValueError("duplicate rendezvous origin")
            origins[origin.rendezvous_id] = origin
        elif command == "REQUEST_RENDEZVOUS":
            raw_origin = payload.get("rendezvous_origin")
            if raw_origin is not None:
                _strict(raw_origin, {"rendezvous_id", "requester_role", "requester_slot", "source_attempt_id",
                                     "target_role", "expert_signal", "component_id", "phase"}, "rendezvous_origin")
                origin = RendezvousOrigin(rendezvous_id=raw_origin["rendezvous_id"], requester_role=raw_origin["requester_role"],
                    requester_slot=raw_origin["requester_slot"], source_attempt_id=raw_origin["source_attempt_id"],
                    target_role=raw_origin["target_role"], signal=ExpertSignal.from_dict(raw_origin["expert_signal"]),
                    component_id=raw_origin["component_id"], phase=raw_origin["phase"])
                if origin.rendezvous_id in origins: raise ValueError("duplicate rendezvous origin")
                origins[origin.rendezvous_id] = origin
            request = RendezvousRequest.from_dict(payload["rendezvous_request"])
            if request.rendezvous_id in requests:
                raise ValueError("duplicate rendezvous request")
            requests[request.rendezvous_id] = request
        elif command == "RESOLVE_RENDEZVOUS":
            reply = RendezvousReply.from_dict(payload["rendezvous_reply"])
            if reply.rendezvous_id in replies:
                raise ValueError("duplicate rendezvous resolution")
            replies[reply.rendezvous_id] = reply
    if not set(requests) <= set(origins) or not set(replies) <= set(origins):
        raise ValueError("rendezvous request or resolution lacks an accepted origin")
    return tuple(origins.values()), requests, replies


__all__ = ["ExpertSignal", "ExpertSignalKind", "RendezvousDisposition",
           "RendezvousOrigin", "RendezvousReply", "RendezvousRequest",
           "derive_rendezvous_records"]
