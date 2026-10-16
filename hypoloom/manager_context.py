"""Bounded manager proposals over existing request inputs and run transitions."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class GraphEvolution:
    role: str
    effective_max_forks: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def admit_graph_evolution(value: Any, request) -> tuple[GraphEvolution, ...]:
    if request.command.value != "GRAPH_EVOLVE" or request.schema_version < 14:
        raise ValueError("graph evolution requires an ACI14 manager request")
    inputs = [item for item in request.input_artifacts if item.ref.endswith("--manager-context")]
    if len(inputs) != 1: raise ValueError("manager requires one frozen graph context")
    context = json.loads(inputs[0].content_bytes())
    if not isinstance(context, Mapping): raise ValueError("invalid manager context")
    limits = context.get("max_hypotheses_per_round")
    if (not isinstance(limits, Mapping) or not limits
            or any(not isinstance(role, str) or not role or type(limit) is not int or limit < 1
                   for role, limit in limits.items())):
        raise ValueError("invalid admitted manager fork limits")
    enabled = context.get("enabled", True)
    if type(enabled) is not bool: raise ValueError("invalid graph evolution enablement")
    if not isinstance(value, list): raise ValueError("graph evolution must be a list")
    if not enabled and value: raise ValueError("graph evolution is disabled")
    changes = []
    roles = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"role", "effective_max_forks", "reason"}:
            raise ValueError("graph evolution fields are not exact")
        role, count, reason = item["role"], item["effective_max_forks"], item["reason"]
        if not isinstance(role, str) or role not in limits: raise ValueError("graph evolution names an unknown expert")
        if role in roles: raise ValueError("duplicate graph evolution expert")
        if type(count) is not int or not 1 <= count <= limits[role]: raise ValueError("graph evolution exceeds admitted fork limits")
        if not isinstance(reason, str) or not reason.strip() or reason != reason.strip(): raise ValueError("graph evolution requires an explanation")
        changes.append(GraphEvolution(role, count, reason)); roles.add(role)
    return tuple(changes)


def effective_forks(transitions, role: str, *, default: int, maximum: int) -> int:
    """Read only accepted manager decisions; callers freeze the selected batch."""
    selected = default
    for row in transitions:
        if row.get("workflow_kind") != "RUN" or row.get("command_type") != "ACCEPT_GRAPH_EVOLUTION": continue
        payload = row["payload"] if "payload" in row else json.loads(row.get("payload_json") or "{}")
        for item in payload.get("graph_evolution", ()):
            if item.get("role") == role: selected = item["effective_max_forks"]
    if type(selected) is not int or not 1 <= selected <= maximum:
        raise ValueError("accepted graph evolution is outside the declared expert limit")
    return selected
