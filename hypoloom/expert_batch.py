"""Read expert batches and schedule proposal tasks through their existing Host.

This module does not choose a scientific outcome. WorkflowHost owns transactions; the ordinary Policy still decides each experiment action.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from .expert_loop import (AuxiliaryRequestKind, AuxiliaryTask, DerivedWorkState, ExpertLineage,
    ExpertLineageInput, ResearchNodeKey, ResearchNodeKind, ResearchNodeSubkind, ResearchNodeTask,
    ScientificNodeRefs, reduce_expert_lineage)
from .agent_aci import PARALLEL_PROPOSALS_ACI_SCHEMA_VERSION


def proposal_siblings(task, other, version):
    """The sole new overlap: two different wings of the exact same proposal."""
    if version < PARALLEL_PROPOSALS_ACI_SCHEMA_VERSION or not isinstance(task, ResearchNodeTask): return False
    key = task.key.to_dict()
    return (key["node_kind"] == "HYPOTHESIS_PROPOSAL"
        and {key["subkind"], other.get("subkind")} == {"FORWARD", "BACKWARD"}
        and {**key, "subkind": other.get("subkind")} == other)


def sibling_proposal_failed(host, request):
    task = request.task_context
    if (request.schema_version < PARALLEL_PROPOSALS_ACI_SCHEMA_VERSION or not isinstance(task, ResearchNodeTask)
            or task.key.node_kind is not ResearchNodeKind.HYPOTHESIS_PROPOSAL
            or task.key.subkind not in {ResearchNodeSubkind.FORWARD, ResearchNodeSubkind.BACKWARD}): return False
    other = ResearchNodeSubkind.BACKWARD if task.key.subkind is ResearchNodeSubkind.FORWARD else ResearchNodeSubkind.FORWARD
    return host._expert_task_final_failure(request.run_id, ResearchNodeTask(key=replace(task.key, subkind=other))) is not None


def next_proposal_task(host, keys):
    """Select from existing task records; requests, not an extra queue, own work."""
    tasks = [ResearchNodeTask(key=key) for key in keys]
    pending = [task for task in tasks if host._expert_success(task) is None]
    if not pending: return None
    first = pending[0]
    version = int(host.store.get_run(first.key.run_id)["agent_aci_version"])
    if version < PARALLEL_PROPOSALS_ACI_SCHEMA_VERSION: return first
    wings = [task for task in pending if task.key.subkind in {ResearchNodeSubkind.FORWARD, ResearchNodeSubkind.BACKWARD}]
    if first.key.node_kind is ResearchNodeKind.LITERATURE_REVIEW or not wings: return first
    # Do not hide a failed wing by repeatedly selecting the still-running one.
    for task in wings:
        if host._expert_task_exhausted(task.key.run_id, task): return task
    for task in wings:
        if not any(row["state"] == "OPEN" for row in host._expert_attempts(task)): return task
    return None


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
        plans = ([event["leader"]] if event.get("action") == "leader_round_started" and "task_command_id" not in event
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


def read_batch_science_failed(host, run_id: str, component_id: str, role: str, cycle: int) -> bool:
    from .agent_aci import AgentRequest
    from .expert_loop import ResearchNodeTask, ResearchNodeKind
    for failure in host._expert_final_failures(run_id):
        attempt = host.store.get_attempt(failure["attempt_id"])
        observation = json.loads(attempt["observation_json"])
        request = AgentRequest.from_dict(observation["request"])
        task = request.task_context
        if not isinstance(task, ResearchNodeTask) or (task.key.component_id, task.key.expert_slot) != (component_id, role): continue
        if request.schema_version >= 24 and task.key.node_kind is ResearchNodeKind.RESULT_ANALYSIS:
            from .agent_context import operational_agent_failure
            # Selection, update, report and budget requirements still apply.
            if operational_agent_failure(observation): continue
        if (task.key.cycle == cycle and task.key.node_kind in {ResearchNodeKind.RESULT_ANALYSIS, ResearchNodeKind.ADJUDICATE, ResearchNodeKind.HYPOTHESIS_UPDATE, ResearchNodeKind.CYCLE_REPORT}
                or task.key.cycle == cycle + 1 and task.key.node_kind in {ResearchNodeKind.LITERATURE_REVIEW, ResearchNodeKind.HYPOTHESIS_PROPOSAL}): return True
    return False


def batch_chain_failed(host, run_id: str, component_id: str, role: str, cycle: int, chain: str) -> bool:
    from .agent_aci import AgentRequest
    from .lifecycle import ExperimentState
    from .agent_context import operational_agent_failure
    for failure in host._expert_final_failures(run_id):
        attempt = host.store.get_attempt(failure["attempt_id"])
        observed = json.loads(attempt["observation_json"])
        request = observed["request"]
        key = request.get("task_context", {}).get("key", {})
        if (key.get("component_id"), key.get("expert_slot"), key.get("cycle"), key.get("chain")) == (component_id, role, cycle, chain):
            # A lost controller cannot invalidate completed scientific work.
            # Accepted analysis and the usual budgets still govern its successor.
            if request.get("schema_version", 1) >= 24 and request["command"] == "EXPERIMENT_EXECUTION" and operational_agent_failure(observed):
                owned = host._owned_tool_experiments(AgentRequest.from_dict(request))
                if owned and all(host.store.latest_transition(item["exp_id"])["state"] == ExperimentState.TERMINAL.value for item in owned):
                    identities = {item["exp_id"] for item in owned}
                    if any(row["experiment_id"] in identities and row["payload"]["outcome"] in {"SUCCESS", "CLEAN_REFUTATION"}
                            for row in host.store.list_facts(run_id, "CanonicalOutcomeAccepted")): continue
            return True
    return False


def drive_expert_proposal(
    host, run_id: str, component_id: str, role: str, *, cycle: int = 1,
    parent_hypothesis_id: str | None = None, restart_from_hypothesis: bool = False,
) -> None:
    from .workflow_host import OperationalHalt
    contract = host._research_contract(run_id)
    batch_proposal = host._batch_enabled(run_id) and int(host.store.get_run(run_id)["agent_aci_version"]) >= 17
    keys = (
        ResearchNodeKey(run_id=run_id, component_id=component_id, expert_slot=role, cycle=cycle,
                        node_kind=ResearchNodeKind.LITERATURE_REVIEW, subkind=ResearchNodeSubkind.QUERY_PLAN),
        ResearchNodeKey(run_id=run_id, component_id=component_id, expert_slot=role, cycle=cycle,
                        node_kind=ResearchNodeKind.LITERATURE_REVIEW),
        ResearchNodeKey(run_id=run_id, component_id=component_id, expert_slot=role, cycle=cycle,
                        node_kind=ResearchNodeKind.HYPOTHESIS_PROPOSAL, subkind=ResearchNodeSubkind.FORWARD),
        ResearchNodeKey(run_id=run_id, component_id=component_id, expert_slot=role, cycle=cycle,
                        node_kind=ResearchNodeKind.HYPOTHESIS_PROPOSAL, subkind=ResearchNodeSubkind.BACKWARD),
        ResearchNodeKey(run_id=run_id, component_id=component_id, expert_slot=role, cycle=cycle,
                        node_kind=ResearchNodeKind.HYPOTHESIS_PROPOSAL, subkind=ResearchNodeSubkind.SYNTHESIS),
    )
    if batch_proposal:
        history = host._batch_history(run_id, component_id, role)
        if len(history.batches) >= cycle: return  # An accepted batch is the SYNTHESIS proof, even without a selected tip.
        if len(history.batches) != cycle - 1: raise OperationalHalt("expert batch proposal is not the next cycle")
        if cycle == 1:
            if parent_hypothesis_id is not None: raise OperationalHalt("first batch cannot have a parent")
        elif (directed := host.interaction.directed_round(run_id, role, component_id, cycle)) is not None:
            if directed["parent_hypothesis_id"] != parent_hypothesis_id:
                raise OperationalHalt("directed successor differs from its accepted parent")
        elif history.selected_tip is not None:
            if history.selected_tip["node_id"] != parent_hypothesis_id: raise OperationalHalt("expert successor cycle has the wrong selected parent")
        elif parent_hypothesis_id is not None or not host._unselected_batch_revision_ready(run_id, component_id, role):
            raise OperationalHalt("unselected batch successor lacks closed explicit revision")
    else:
        lineage = ExpertLineage(run_id=run_id, component_id=component_id, expert_slot=role,
            cycle=cycle, parent_hypothesis_id=parent_hypothesis_id)
        chain = host._expert_hypothesis_chain(run_id, component_id, role)
        if len(chain) > cycle: raise OperationalHalt("expert hypothesis lineage is ahead of its cycle")
        if cycle > 1 and (len(chain) < cycle - 1 or str(chain[cycle - 2]["hypothesis_id"]) != parent_hypothesis_id):
            raise OperationalHalt("expert successor cycle has the wrong parent")
        hypothesis = chain[cycle - 1] if len(chain) == cycle else None
    if host._expert_success(grounding_plan := AuxiliaryTask(kind=AuxiliaryRequestKind.GROUNDING_QUERY_PLAN, run_id=run_id)) is None:
        if host._expert_task_exhausted(run_id, grounding_plan):
            host._record_expert_final_failure(run_id, component_id, role, grounding_plan, "grounding_query_plan_exhausted"); return
        host._expert_agent_step(run_id, run_id, grounding_plan, contract.role_ids[0], str(contract.components[0].component_id)); return
    if restart_from_hypothesis:
        plan = host.interaction.debate.designated_round(run_id, component_id)
        if (plan is None or not plan.get("restart_from_hypothesis") or (plan["role"], plan["cycle"], plan["parent_hypothesis_id"]) != (role, cycle, parent_hypothesis_id)):
            raise OperationalHalt("hypothesis-stage restart lacks its actual Debate authorization")
        # The explicit control resumes at HP. Do not invent CLOSED LR views
        # for this cycle; FORWARD reads the actual prior LR Attempt instead.
        task = next_proposal_task(host, keys[2:])
        if task is None or any(row["state"] == "OPEN" for row in host._expert_attempts(task)): return
    elif batch_proposal:
        grounding = host._expert_grounding_view(run_id)
        if grounding is None or grounding.state is not DerivedWorkState.CLOSED or not grounding.artifact_ids:
            task = host._grounding_task(run_id)
        else:
            task = next_proposal_task(host, keys)
            if task is None:
                if host._expert_success(ResearchNodeTask(key=keys[-1])) is not None:
                    raise OperationalHalt("successful SYNTHESIS lacks its atomically accepted batch")
                return
        if any(row["state"] == "OPEN" for row in host._expert_attempts(task)): return
    else:
        decision = reduce_expert_lineage(
            ExpertLineageInput(
                lineage=lineage,
                scientific_refs=ScientificNodeRefs(
                    hypothesis_id=None if hypothesis is None else str(hypothesis["hypothesis_id"]),
                ),
            ),
            tuple(host._expert_node_view(key) for key in keys),
            host._expert_grounding_view(run_id),
        )
        task = decision.next_task
        if task is None or decision.task_state is DerivedWorkState.OPEN: return
    if (isinstance(task, ResearchNodeTask)
            and task.key.node_kind is ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT):
        return
    if host._expert_task_exhausted(run_id, task):
        code = "grounding_exhausted" if isinstance(task, AuxiliaryTask) else "expert_node_exhausted"
        host._record_expert_final_failure(run_id, component_id, role, task, code)
        return
    dispatch_role = contract.role_ids[0] if isinstance(task, AuxiliaryTask) else role
    dispatch_component = (str(contract.components[0].component_id)
                          if isinstance(task, AuxiliaryTask) else component_id)
    host._expert_agent_step(
        run_id, run_id, task, dispatch_role, dispatch_component,
    )
