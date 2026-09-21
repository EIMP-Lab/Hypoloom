"""Read expert batches and selections from existing accepted relations.

This module does not write state or choose a scientific outcome. WorkflowHost
owns transactions; the ordinary Policy still decides each experiment action.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence


def supports_hypothesis_batches(version: object) -> bool:
    return isinstance(version, int) and not isinstance(version, bool) and version >= 14


def directed_research_plan(transitions, run_id, role, component_id, cycle, *, before_revision=None):
    """Read one already accepted control; ordinary lineage remains unchanged."""
    matches = []
    for row in transitions:
        if row.get("workflow_kind") != "RUN" or row.get("workflow_id") != run_id or row.get("run_id") != run_id:
            continue
        if before_revision is not None and row["revision"] >= before_revision: continue
        event = _payload(row).get("expert_interaction", {})
        plans = ([event["leader"]] if event.get("action") == "leader_round_started"
            else event.get("plans", ()) if event.get("action") == "debate_restarts_designated" else ())
        for plan in plans:
            if (plan["role"], plan["component_id"], plan["cycle"]) == (role, component_id, cycle):
                if row.get("command_type", row.get("command")) != "ACCEPT_EXPERT_INTERACTION":
                    raise ValueError("directed research lacks an accepted interaction control")
                matches.append(plan)
    if len(matches) > 1:
        raise ValueError("multiple directed controls target the same research cycle")
    return matches[0] if matches else None


def batch_limits(document: Mapping[str, Any], role: str) -> dict[str, int]:
    product = document.get("methodology", {}).get("controls", {}).get("product", {})
    inner = product.get("methodology", {}).get("inner_loop", {})
    stop = product.get("goal", {}).get("stop_conditions", {})
    budget = document.get("budget", {})
    experts = product.get("experts", {})
    settings = dict(experts.get("defaults", {}))
    declared = experts.get("experts", [])
    if isinstance(declared, list):
        settings.update(next((item for item in declared if item.get("name", item.get("id")) == role), {}))
    forks = settings.get("max_hypotheses_per_round", inner.get("max_hypotheses_per_round", 1))
    values = {"forks": forks, "parallel_chains": settings.get("max_forks", forks), "rounds": stop.get("max_rounds_per_expert", budget.get("max_experiments", 1)),
              "iterations": inner.get("max_iterations_per_hypothesis", 1),
              "repairs": inner.get("repair_attempts", budget.get("repair_attempts", budget.get("max_retries", 3)))}
    if any(isinstance(value, bool) or not isinstance(value, int) or value < (0 if key == "repairs" else 1)
           for key, value in values.items()):
        raise ValueError("expert batch budgets must be finite admitted integer bounds")
    return values


def _payload(row: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = row.get("payload")
    if payload is None:
        payload = json.loads(row["payload_json"])
    if not isinstance(payload, Mapping):
        raise ValueError("accepted batch row lacks an object payload")
    return payload


@dataclass(frozen=True, slots=True)
class AcceptedBatch:
    cycle: int
    command_id: str
    source_attempt_id: str
    members: Mapping[str, str]
    selection: Mapping[str, Any] | None = None

    @property
    def chains(self) -> tuple[str, ...]:
        return tuple(self.members)


@dataclass(frozen=True, slots=True)
class BatchHistory:
    run_id: str
    component_id: str
    role: str
    batches: tuple[AcceptedBatch, ...]
    hypotheses: Mapping[str, Mapping[str, Any]]

    @property
    def latest(self) -> AcceptedBatch | None:
        return self.batches[-1] if self.batches else None

    @property
    def selected_tip(self) -> Mapping[str, Any] | None:
        # A newer unfinished batch cannot silently replace an accepted binding.
        for batch in reversed(self.batches):
            if batch.selection is not None:
                return batch.selection.get("selected_tip")
        return None

    def hypothesis(self, cycle: int, chain: str) -> Mapping[str, Any]:
        try:
            return self.hypotheses[self.batches[cycle - 1].members[chain]]
        except (KeyError, IndexError) as exc:
            raise ValueError("task does not name an accepted batch member") from exc

    @classmethod
    def from_rows(cls, run_id: str, component_id: str, role: str,
                  transitions: Sequence[Mapping[str, Any]], facts: Sequence[Mapping[str, Any]],
                  *, agent_aci_version: int = 16) -> BatchHistory:
        hypotheses = {str(row["hypothesis_id"]): row for row in facts
                      if row["fact_type"] == "HypothesisProposed"
                      and _payload(row).get("component_id") == component_id
                      and _payload(row).get("proposer_role") == role}
        batches: dict[int, AcceptedBatch] = {}
        batch_revisions = {}
        selections: dict[int, Mapping[str, Any]] = {}
        for row in transitions:
            if row.get("workflow_kind") != "RUN" or row.get("workflow_id") != run_id:
                continue
            payload = _payload(row)
            if payload.get("component_id") != component_id or payload.get("role") != role:
                continue
            raw = payload.get("hypothesis_batch")
            if raw is not None:
                if row.get("command_type", row.get("command")) != "ACCEPT_HYPOTHESIS" or not isinstance(raw, Mapping):
                    raise ValueError("batch must belong to an accepted SYNTHESIS relation")
                cycle, members = raw.get("cycle"), raw.get("chains")
                if (isinstance(cycle, bool) or not isinstance(cycle, int) or cycle < 1
                        or not isinstance(members, Mapping) or not members or cycle in batches
                        or len(set(members.values())) != len(members)):
                    raise ValueError("accepted batch has duplicate or invalid membership")
                from .expert_loop import chain_session_scope
                for chain, hypothesis_id in members.items():
                    chain_session_scope(chain)
                    if chain is None or hypothesis_id not in hypotheses:
                        raise ValueError("accepted batch member lacks its exact hypothesis")
                batch_revisions[cycle] = row["revision"]
                batches[cycle] = AcceptedBatch(cycle, payload["node_command_id"], payload["agent_attempt_id"],
                    MappingProxyType(dict(sorted(members.items()))))
            selection = payload.get("adjudication")
            if selection is not None:
                if row.get("command_type", row.get("command")) != "ACCEPT_ADJUDICATION" or not isinstance(selection, Mapping):
                    raise ValueError("selection must belong to an accepted adjudication")
                cycle = selection.get("cycle")
                if cycle in selections:
                    raise ValueError("batch has more than one accepted adjudication")
                selections[cycle] = selection
        if sorted(batches) != list(range(1, len(batches) + 1)) or set(selections) - set(batches):
            raise ValueError("accepted batch cycles are not contiguous")
        result: list[AcceptedBatch] = []
        for cycle, batch in sorted(batches.items()):
            prior = None if cycle == 1 else selections.get(cycle - 1)
            parent = None if prior is None or prior.get("selected_tip") is None else prior["selected_tip"]["node_id"]
            directed = directed_research_plan(transitions, run_id, role, component_id, cycle, before_revision=batch_revisions[cycle]) if agent_aci_version >= 21 else None
            if directed is not None:
                parent = directed["parent_hypothesis_id"]
                if cycle <= 1 or parent not in hypotheses or not any(parent in previous.members.values()
                        for previous in result):
                    raise ValueError("directed parent is not an earlier own accepted hypothesis")
            unselected_revision = (directed is None and agent_aci_version >= 17 and cycle > 1
                and prior is not None and prior.get("selected_tip") is None)
            if cycle > 1 and parent is None and not unselected_revision:
                raise ValueError("new batch lacks the prior adjudicated hypothesis")
            parents = set(batches[cycle - 1].members.values()) if unselected_revision else {parent}
            for hypothesis_id in batch.members.values():
                payload = _payload(hypotheses[hypothesis_id])
                if payload.get("parent_hypothesis_id") not in parents:
                    raise ValueError("batch member does not descend from its accepted prior source")
                if unselected_revision and (not isinstance(payload.get("relation"), str)
                        or not payload["relation"].strip()):
                    raise ValueError("unselected batch revision requires a nonempty relation")
            selection = selections.get(cycle)
            if selection is not None:
                tip = selection.get("selected_tip")
                if selection.get("batch_command_id") != batch.command_id or (
                        tip is not None and (not isinstance(tip, Mapping)
                            or batch.members.get(tip.get("chain_id")) != tip.get("node_id"))):
                    raise ValueError("adjudication selection does not belong to its exact batch")
            result.append(AcceptedBatch(cycle, batch.command_id, batch.source_attempt_id, batch.members, selection))
        return cls(run_id, component_id, role, tuple(result), MappingProxyType(hypotheses))
