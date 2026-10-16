"""Pure contracts and recovery projection for the staged expert loop.

The values in this module are projections over existing commands and Attempts;
they are not another lifecycle or persistence authority.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, TypeAlias

from .artifacts import _SAFE_ID


class ResearchNodeKind(str, Enum):
    LITERATURE_REVIEW = "LITERATURE_REVIEW"
    HYPOTHESIS_PROPOSAL = "HYPOTHESIS_PROPOSAL"
    EXPERIMENT_DESIGN_IMPLEMENT = "EXPERIMENT_DESIGN_IMPLEMENT"
    EXPERIMENT_EXECUTION = "EXPERIMENT_EXECUTION"
    RESULT_ANALYSIS = "RESULT_ANALYSIS"
    HYPOTHESIS_UPDATE = "HYPOTHESIS_UPDATE"
    CYCLE_REPORT = "CYCLE_REPORT"
    ADJUDICATE = "ADJUDICATE"
    SELF_EVOLVE = "SELF_EVOLVE"


_EXPERIMENT_BOUND_NODE_KINDS = frozenset(
    {
        ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT,
        ResearchNodeKind.EXPERIMENT_EXECUTION,
        ResearchNodeKind.RESULT_ANALYSIS,
    }
)


class ResearchNodeSubkind(str, Enum):
    MAIN = "MAIN"
    QUERY_PLAN = "QUERY_PLAN"
    FORWARD = "FORWARD"
    BACKWARD = "BACKWARD"
    SYNTHESIS = "SYNTHESIS"

class AuxiliaryRequestKind(str, Enum):
    GROUNDING_QUERY_PLAN = "GROUNDING_QUERY_PLAN"
    GROUNDING = "GROUNDING"
    OBSERVER = "OBSERVER"
    DEBATE = "DEBATE"
    LEADER = "LEADER"
    CONTEXT_COMPACTION = "CONTEXT_COMPACTION"
    GRAPH_EVOLVE = "GRAPH_EVOLVE"
    FINAL_REPORT = "FINAL_REPORT"
    DEBATE_JUDGE = "DEBATE_JUDGE"
    PAPER_READ = "PAPER_READ"


class HypothesisUpdateDisposition(str, Enum):
    REVISE_NEXT_CYCLE = "REVISE_NEXT_CYCLE"
    TERMINATE_LINEAGE = "TERMINATE_LINEAGE"


class DerivedWorkState(str, Enum):
    READY = "READY"
    OPEN = "OPEN"
    CLOSED = "CLOSED"


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be non-empty trimmed text")
    return value


def _chain(value: object) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise ValueError("chain must be a safe non-empty string identity")
    return value


def chain_session_scope(chain: str | None) -> str:
    """Session consumers opt in explicitly; None retains the legacy scope."""
    if chain is None:
        return "chain-1"
    encoded = base64.urlsafe_b64encode(_chain(chain).encode()).decode().rstrip("=")
    return "chain-v1." + encoded


@dataclass(frozen=True, slots=True, kw_only=True)
class ResearchNodeKey:
    run_id: str
    component_id: str
    expert_slot: str
    cycle: int
    node_kind: ResearchNodeKind
    subkind: ResearchNodeSubkind = ResearchNodeSubkind.MAIN
    experiment_ordinal: int | None = None
    chain: str | None = None

    def __post_init__(self) -> None:
        for name in ("run_id", "component_id", "expert_slot"):
            _text(getattr(self, name), name)
        if self.chain is not None:
            _chain(self.chain)
        if isinstance(self.cycle, bool) or not isinstance(self.cycle, int) or self.cycle < 1:
            raise ValueError("cycle must be a positive integer")
        if not isinstance(self.node_kind, ResearchNodeKind):
            raise ValueError("node_kind must be a ResearchNodeKind")
        if not isinstance(self.subkind, ResearchNodeSubkind):
            raise ValueError("subkind must be a ResearchNodeSubkind")
        if self.node_kind is ResearchNodeKind.LITERATURE_REVIEW:
            allowed_subkinds = {
                ResearchNodeSubkind.QUERY_PLAN,
                ResearchNodeSubkind.MAIN,
            }
        elif self.node_kind is ResearchNodeKind.HYPOTHESIS_PROPOSAL:
            allowed_subkinds = {
                ResearchNodeSubkind.FORWARD,
                ResearchNodeSubkind.BACKWARD,
                ResearchNodeSubkind.SYNTHESIS,
            }
        else:
            allowed_subkinds = {ResearchNodeSubkind.MAIN}
        if self.subkind not in allowed_subkinds:
            raise ValueError(
                f"{self.node_kind.value} does not use {self.subkind.value}"
            )
        experiment_bound = self.node_kind in _EXPERIMENT_BOUND_NODE_KINDS
        if experiment_bound:
            if (
                isinstance(self.experiment_ordinal, bool)
                or not isinstance(self.experiment_ordinal, int)
                or self.experiment_ordinal < 1
            ):
                raise ValueError(
                    "experiment_ordinal must be a positive integer for experiment-bound nodes"
                )
        elif self.experiment_ordinal is not None:
            raise ValueError(
                "only experiment-bound nodes may set experiment_ordinal"
            )

    @property
    def command_id(self) -> str:
        """Return a reversible stable ID; no Attempt or completion order enters it."""
        identity = self.to_dict()
        if self.experiment_ordinal is None:
            identity.pop("experiment_ordinal")
        raw = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        return f"research-node-v1.{encoded}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "component_id": self.component_id,
            "expert_slot": self.expert_slot,
            "cycle": self.cycle,
            "node_kind": self.node_kind.value,
            "subkind": self.subkind.value,
            "experiment_ordinal": self.experiment_ordinal,
            **({"chain": self.chain} if self.chain is not None else {}),
        }

    @property
    def lineage_identity(self) -> tuple:
        legacy = (self.run_id, self.component_id, self.expert_slot, self.cycle)
        return legacy if self.chain is None else (*legacy, self.chain)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ResearchNodeKey:
        expected = {
            "run_id", "component_id", "expert_slot", "cycle", "node_kind",
            "subkind", "experiment_ordinal",
        }
        if set(value) not in (expected, expected | {"chain"}):
            raise ValueError("research node key fields are not exact")
        return cls(
            run_id=value["run_id"], component_id=value["component_id"],
            expert_slot=value["expert_slot"], cycle=value["cycle"],
            node_kind=ResearchNodeKind(value["node_kind"]),
            subkind=ResearchNodeSubkind(value["subkind"]),
            experiment_ordinal=value["experiment_ordinal"],
            chain=value.get("chain"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ResearchNodeTask:
    key: ResearchNodeKey

    def __post_init__(self) -> None:
        if not isinstance(self.key, ResearchNodeKey):
            raise ValueError("key must be a ResearchNodeKey")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "RESEARCH_NODE", "key": self.key.to_dict()}

    @property
    def command_id(self) -> str:
        return self.key.command_id

    @property
    def run_id(self) -> str:
        return self.key.run_id


@dataclass(frozen=True, slots=True, kw_only=True)
class AuxiliaryTask:
    kind: AuxiliaryRequestKind
    run_id: str
    component_id: str | None = None
    expert_slot: str | None = None
    cycle: int | None = None
    ordinal: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.kind, AuxiliaryRequestKind):
            raise ValueError("kind must be an AuxiliaryRequestKind")
        _text(self.run_id, "run_id")
        for name in ("component_id", "expert_slot"):
            value = getattr(self, name)
            if value is not None:
                _text(value, name)
        for name in ("cycle", "ordinal"):
            value = getattr(self, name)
            if (value is not None and
                    (isinstance(value, bool) or not isinstance(value, int) or value < 1)):
                raise ValueError(f"{name} must be a positive integer or None")
        if self.kind in {AuxiliaryRequestKind.GROUNDING_QUERY_PLAN, AuxiliaryRequestKind.GROUNDING, AuxiliaryRequestKind.FINAL_REPORT} and any(
                value is not None for value in (self.component_id, self.expert_slot, self.cycle)):
            raise ValueError("Grounding planning and Grounding must be role-neutral and run-scoped")
        if self.kind is AuxiliaryRequestKind.GRAPH_EVOLVE and (
                self.component_id is not None or self.expert_slot is not None or self.cycle is None):
            raise ValueError("Grounding manager requires a role-neutral trigger cycle")

    @property
    def command_id(self) -> str:
        raw = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        return f"expert-aux-v1.{encoded}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "AUXILIARY", "request_kind": self.kind.value,
            "run_id": self.run_id, "component_id": self.component_id,
            "expert_slot": self.expert_slot, "cycle": self.cycle,
            "ordinal": self.ordinal,
        }


AgentTaskContext: TypeAlias = ResearchNodeTask | AuxiliaryTask


def task_context_from_dict(value: Mapping[str, Any]) -> AgentTaskContext:
    if value.get("kind") == "RESEARCH_NODE" and set(value) == {"kind", "key"}:
        key = value["key"]
        if not isinstance(key, Mapping):
            raise ValueError("research node task key must be an object")
        return ResearchNodeTask(key=ResearchNodeKey.from_dict(key))
    expected = {"kind", "request_kind", "run_id", "component_id", "expert_slot", "cycle", "ordinal"}
    if value.get("kind") != "AUXILIARY" or set(value) != expected:
        raise ValueError("task context fields are not exact")
    return AuxiliaryTask(
        kind=AuxiliaryRequestKind(value["request_kind"]), run_id=value["run_id"],
        component_id=value["component_id"], expert_slot=value["expert_slot"],
        cycle=value["cycle"], ordinal=value["ordinal"],
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ResearchNodeView:
    key: ResearchNodeKey
    command_id: str
    state: DerivedWorkState
    attempt_ids: tuple[str, ...]
    fact_ids: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class AuxiliaryTaskView:
    """Derived observation of one auxiliary task, never a persisted status."""

    task: AuxiliaryTask
    state: DerivedWorkState
    attempt_ids: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.task, AuxiliaryTask):
            raise ValueError("task must be an AuxiliaryTask")
        if not isinstance(self.state, DerivedWorkState):
            raise ValueError("state must be a DerivedWorkState")
        for name in ("attempt_ids", "artifact_ids"):
            values = getattr(self, name)
            if not isinstance(values, tuple):
                raise ValueError(f"{name} must be a tuple")
            for value in values:
                _text(value, f"{name} item")
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must not contain duplicates")


@dataclass(frozen=True, slots=True, kw_only=True)
class ExpertLineage:
    """Identity of the one currently reducible cycle for an expert lineage."""

    run_id: str
    component_id: str
    expert_slot: str
    cycle: int = 1
    parent_hypothesis_id: str | None = None
    chain: str | None = None

    def __post_init__(self) -> None:
        for name in ("run_id", "component_id", "expert_slot"):
            _text(getattr(self, name), name)
        if self.chain is not None:
            _chain(self.chain)
        if (
            isinstance(self.cycle, bool)
            or not isinstance(self.cycle, int)
            or self.cycle < 1
        ):
            raise ValueError("cycle must be a positive integer")
        if self.parent_hypothesis_id is not None:
            _text(self.parent_hypothesis_id, "parent_hypothesis_id")
        if (self.cycle == 1) != (self.parent_hypothesis_id is None):
            raise ValueError("only cycle one may omit its parent hypothesis")

    @property
    def identity(self) -> tuple:
        legacy = (self.run_id, self.component_id, self.expert_slot, self.cycle)
        return legacy if self.chain is None else (*legacy, self.chain)

    def key(
        self,
        node_kind: ResearchNodeKind,
        subkind: ResearchNodeSubkind = ResearchNodeSubkind.MAIN,
        *,
        experiment_ordinal: int | None = None,
    ) -> ResearchNodeKey:
        if node_kind in _EXPERIMENT_BOUND_NODE_KINDS and experiment_ordinal is None:
            experiment_ordinal = 1
        return ResearchNodeKey(
            run_id=self.run_id,
            component_id=self.component_id,
            expert_slot=self.expert_slot,
            cycle=self.cycle,
            node_kind=node_kind,
            subkind=subkind,
            experiment_ordinal=experiment_ordinal,
            chain=self.chain,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ScientificNodeRefs:
    """Existing Host-admitted science needed to advance semantic nodes."""

    hypothesis_id: str | None = None
    experiment_id: str | None = None
    experiment_ordinal: int | None = None
    outcome_id: str | None = None
    assessment_id: str | None = None
    update_disposition: HypothesisUpdateDisposition | None = None
    execution_node_ordinal: int | None = None

    def __post_init__(self) -> None:
        for name in (
            "hypothesis_id",
            "experiment_id",
            "outcome_id",
            "assessment_id",
        ):
            value = getattr(self, name)
            if value is not None:
                _text(value, name)
        if (
            self.update_disposition is not None
            and not isinstance(
                self.update_disposition, HypothesisUpdateDisposition
            )
        ):
            raise ValueError(
                "update_disposition must be a HypothesisUpdateDisposition"
            )
        if self.experiment_ordinal is not None and (
            isinstance(self.experiment_ordinal, bool)
            or not isinstance(self.experiment_ordinal, int)
            or self.experiment_ordinal < 1
        ):
            raise ValueError("experiment_ordinal must be a positive integer")
        if self.execution_node_ordinal is not None and (
            isinstance(self.execution_node_ordinal, bool) or not isinstance(self.execution_node_ordinal, int)
            or self.execution_node_ordinal < 1):
            raise ValueError("execution_node_ordinal must be a positive integer")
        if (self.experiment_id is None) != (self.experiment_ordinal is None):
            raise ValueError(
                "experiment_id and experiment_ordinal must have the same presence"
            )
        ordered = (
            self.hypothesis_id,
            self.experiment_id,
            self.outcome_id,
            self.assessment_id,
        )
        if any(value is not None for value in ordered[1:]) and ordered[0] is None:
            raise ValueError("scientific refs must include their hypothesis")
        if any(value is not None for value in ordered[2:]) and ordered[1] is None:
            raise ValueError("scientific refs must include their experiment")
        if ordered[3] is not None and ordered[2] is None:
            raise ValueError("assessment ref requires its outcome")
        if self.update_disposition is not None and self.assessment_id is None:
            raise ValueError("hypothesis update requires its assessment")


@dataclass(frozen=True, slots=True, kw_only=True)
class ExpertLineageInput:
    lineage: ExpertLineage
    scientific_refs: ScientificNodeRefs = ScientificNodeRefs()
    tool_execution: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.lineage, ExpertLineage):
            raise ValueError("lineage must be an ExpertLineage")
        if not isinstance(self.scientific_refs, ScientificNodeRefs):
            raise ValueError("scientific_refs must be ScientificNodeRefs")
        if not isinstance(self.tool_execution, bool):
            raise ValueError("tool_execution must be boolean")


@dataclass(frozen=True, slots=True, kw_only=True)
class ExpertLineageDecision:
    """One current task, or a terminal lineage; callers still own dispatch."""

    source_lineage: ExpertLineage
    next_lineage: ExpertLineage | None
    next_task: AgentTaskContext | None
    task_state: DerivedWorkState | None
    terminated: bool

    def __post_init__(self) -> None:
        if not isinstance(self.source_lineage, ExpertLineage):
            raise ValueError("source_lineage must be an ExpertLineage")
        if self.terminated:
            if any(
                value is not None
                for value in (self.next_lineage, self.next_task, self.task_state)
            ):
                raise ValueError("terminated lineage must not retain next work")
            return
        if (
            not isinstance(self.next_lineage, ExpertLineage)
            or not isinstance(self.next_task, (ResearchNodeTask, AuxiliaryTask))
            or not isinstance(self.task_state, DerivedWorkState)
        ):
            raise ValueError(
                "active lineage must contain exactly one typed next task"
            )


_EXPERT_NODE_ORDER = (
    (ResearchNodeKind.LITERATURE_REVIEW, ResearchNodeSubkind.QUERY_PLAN),
    (ResearchNodeKind.LITERATURE_REVIEW, ResearchNodeSubkind.MAIN),
    (ResearchNodeKind.HYPOTHESIS_PROPOSAL, ResearchNodeSubkind.FORWARD),
    (ResearchNodeKind.HYPOTHESIS_PROPOSAL, ResearchNodeSubkind.BACKWARD),
    (ResearchNodeKind.HYPOTHESIS_PROPOSAL, ResearchNodeSubkind.SYNTHESIS),
    (ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT, ResearchNodeSubkind.MAIN),
    (ResearchNodeKind.EXPERIMENT_EXECUTION, ResearchNodeSubkind.MAIN),
    (ResearchNodeKind.RESULT_ANALYSIS, ResearchNodeSubkind.MAIN),
    (ResearchNodeKind.HYPOTHESIS_UPDATE, ResearchNodeSubkind.MAIN),
)


def _next_decision(
    source: ExpertLineage,
    lineage: ExpertLineage,
    task: AgentTaskContext,
    state: DerivedWorkState,
) -> ExpertLineageDecision:
    return ExpertLineageDecision(
        source_lineage=source,
        next_lineage=lineage,
        next_task=task,
        task_state=state,
        terminated=False,
    )


def _effective_state(
    view: ResearchNodeView | None, proof_present: bool
) -> DerivedWorkState:
    if view is None:
        return DerivedWorkState.READY
    if view.state is DerivedWorkState.CLOSED and not proof_present:
        return DerivedWorkState.READY
    return view.state


def reduce_expert_lineage(
    item: ExpertLineageInput,
    node_views: Iterable[ResearchNodeView],
    grounding_view: AuxiliaryTaskView | None,
) -> ExpertLineageDecision:
    """Return the sole current task for one lineage, without reading state."""

    if not isinstance(item, ExpertLineageInput):
        raise TypeError("item must be an ExpertLineageInput")
    lineage = item.lineage
    if grounding_view is None:
        grounding = AuxiliaryTask(
            kind=AuxiliaryRequestKind.GROUNDING,
            run_id=lineage.run_id,
        )
        return _next_decision(lineage, lineage, grounding, DerivedWorkState.READY)
    if (
        not isinstance(grounding_view, AuxiliaryTaskView)
        or grounding_view.task.kind is not AuxiliaryRequestKind.GROUNDING
        or grounding_view.task.run_id != lineage.run_id
    ):
        raise ValueError(
            "grounding view must be the matching run-scoped Grounding task"
        )
    grounding_complete = (
        grounding_view.state is DerivedWorkState.CLOSED
        and bool(grounding_view.artifact_ids)
    )
    if not grounding_complete:
        state = (
            DerivedWorkState.READY
            if grounding_view.state is DerivedWorkState.CLOSED
            else grounding_view.state
        )
        return _next_decision(lineage, lineage, grounding_view.task, state)

    by_key: dict[ResearchNodeKey, ResearchNodeView] = {}
    for view in node_views:
        if not isinstance(view, ResearchNodeView):
            raise TypeError("node_views must contain ResearchNodeView values")
        if view.command_id != view.key.command_id:
            raise ValueError("research node view has a mismatched command identity")
        if view.key.lineage_identity != lineage.identity:
            continue
        if view.key in by_key:
            raise ValueError("lineage has duplicate research node views")
        by_key[view.key] = view

    refs = item.scientific_refs
    proofs = (
        lambda view: view is not None and bool(view.artifact_ids),
        lambda view: view is not None and bool(view.artifact_ids),
        lambda view: view is not None and bool(view.artifact_ids),
        lambda view: view is not None and bool(view.artifact_ids),
        lambda _view: refs.hypothesis_id is not None,
        lambda view: (view is not None and bool(view.artifact_ids)) if item.tool_execution else refs.experiment_id is not None,
        lambda _view: refs.outcome_id is not None,
        lambda _view: refs.assessment_id is not None,
        lambda _view: refs.update_disposition is not None,
    )
    for (node_kind, subkind), proof in zip(
        _EXPERT_NODE_ORDER, proofs, strict=True
    ):
        experiment_ordinal = None
        if node_kind in _EXPERIMENT_BOUND_NODE_KINDS:
            experiment_ordinal = refs.experiment_ordinal or 1
            if item.tool_execution and node_kind in {ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT, ResearchNodeKind.EXPERIMENT_EXECUTION}:
                experiment_ordinal = refs.execution_node_ordinal or experiment_ordinal
        key = lineage.key(
            node_kind,
            subkind,
            experiment_ordinal=experiment_ordinal,
        )
        view = by_key.get(key)
        proof_present = proof(view)
        complete = (
            view is not None
            and view.state is DerivedWorkState.CLOSED
            and proof_present
        )
        if not complete:
            return _next_decision(
                lineage,
                lineage,
                ResearchNodeTask(key=key),
                _effective_state(view, proof_present),
            )

    if refs.update_disposition is HypothesisUpdateDisposition.TERMINATE_LINEAGE:
        return ExpertLineageDecision(
            source_lineage=lineage,
            next_lineage=None,
            next_task=None,
            task_state=None,
            terminated=True,
        )
    if refs.update_disposition is not HypothesisUpdateDisposition.REVISE_NEXT_CYCLE:
        raise ValueError("closed update has an unknown disposition")
    assert refs.hypothesis_id is not None
    successor = ExpertLineage(
        run_id=lineage.run_id,
        component_id=lineage.component_id,
        expert_slot=lineage.expert_slot,
        cycle=lineage.cycle + 1,
        parent_hypothesis_id=refs.hypothesis_id,
        chain=lineage.chain,
    )
    return _next_decision(
        lineage,
        successor,
        ResearchNodeTask(
            key=successor.key(
                ResearchNodeKind.LITERATURE_REVIEW,
                ResearchNodeSubkind.QUERY_PLAN,
            )
        ),
        DerivedWorkState.READY,
    )


def reduce_expert_lineages(
    inputs: Iterable[ExpertLineageInput],
    node_views: Iterable[ResearchNodeView],
    grounding_views: Iterable[AuxiliaryTaskView] = (),
) -> tuple[ExpertLineageDecision, ...]:
    """Reduce independent lineages deterministically, regardless of input order."""

    items = tuple(inputs)
    views = tuple(node_views)
    groundings: dict[str, AuxiliaryTaskView] = {}
    for view in grounding_views:
        if view.task.kind is not AuxiliaryRequestKind.GROUNDING:
            raise ValueError("grounding_views may contain only Grounding")
        if view.task.run_id in groundings:
            raise ValueError("run has duplicate Grounding views")
        groundings[view.task.run_id] = view
    by_identity: dict[tuple, ExpertLineageInput] = {}
    for item in items:
        if not isinstance(item, ExpertLineageInput):
            raise TypeError("inputs must contain ExpertLineageInput values")
        if item.lineage.identity in by_identity:
            raise ValueError("expert lineage input is duplicated")
        by_identity[item.lineage.identity] = item
    return tuple(
        reduce_expert_lineage(item, views, groundings.get(item.lineage.run_id))
        for _, item in sorted(by_identity.items())
    )


def derive_research_node_views(
    keys: Iterable[ResearchNodeKey],
    commands: Iterable[Mapping[str, Any]],
    attempts: Iterable[Mapping[str, Any]],
    transitions: Iterable[Mapping[str, Any]] = (),
    facts: Iterable[Mapping[str, Any]] = (),
    artifacts: Iterable[Mapping[str, Any]] = (),
    *,
    agent_aci_version: str | int = 10,
) -> tuple[ResearchNodeView, ...]:
    """Derive retry-safe work state without persisting a node-status mirror."""
    from .agent_aci import _READABLE_ACI_SCHEMA_VERSIONS, execution_handoff
    import json
    if str(agent_aci_version) not in {str(version) for version in _READABLE_ACI_SCHEMA_VERSIONS if version >= 7}:
        return ()
    command_ids = {str(row["command_id"]) for row in commands}
    by_command: dict[str, list[Mapping[str, Any]]] = {}
    for attempt in attempts:
        by_command.setdefault(str(attempt["command_id"]), []).append(attempt)
    known_facts = {str(fact["fact_id"]) for fact in facts}
    facts_by_command: dict[str, list[str]] = {}
    for transition in transitions:
        payload = transition.get("payload", transition)
        fact_id = transition.get("fact_id")
        if (isinstance(payload, Mapping) and isinstance(payload.get("node_command_id"), str)
                and fact_id is not None and str(fact_id) in known_facts):
            facts_by_command.setdefault(str(payload["node_command_id"]), []).append(str(fact_id))
    artifacts_by_command: dict[str, list[str]] = {}
    for artifact in artifacts:
        attempt_id = str(artifact.get("attempt_id"))
        for command_id, observed in by_command.items():
            if any(str(row["attempt_id"]) == attempt_id for row in observed):
                artifacts_by_command.setdefault(command_id, []).append(str(artifact["artifact_id"]))
    views = []
    for key in sorted(keys, key=lambda item: (
            item.run_id, item.component_id, item.expert_slot, item.cycle,
            item.node_kind.value, item.subkind.value,
            item.experiment_ordinal or 0, item.chain or "")):
        command_id = key.command_id
        observed = sorted(by_command.get(command_id, ()), key=lambda row: int(row["attempt_ordinal"]))
        ordinals = [int(row["attempt_ordinal"]) for row in observed]
        if ordinals != list(range(1, len(ordinals) + 1)):
            raise ValueError(f"attempt ordinals are not contiguous for {command_id!r}")
        if any(str(row["state"]) == "OPEN" for row in observed):
            state = DerivedWorkState.OPEN
        elif any(str(row["state"]) == "CLOSED" and str(row.get("terminal_reason")) == "SUCCEEDED"
                 and not execution_handoff(json.loads(row.get("observation_json", "{}")))
                 for row in observed):
            state = DerivedWorkState.CLOSED
        else:
            state = DerivedWorkState.READY
        if observed and command_id not in command_ids:
            raise ValueError(f"attempts reference an absent command {command_id!r}")
        views.append(ResearchNodeView(
            key=key, command_id=command_id, state=state,
            attempt_ids=tuple(str(row["attempt_id"]) for row in observed),
            fact_ids=tuple(sorted(facts_by_command.get(command_id, ()))),
            artifact_ids=tuple(sorted(artifacts_by_command.get(command_id, ()))),
        ))
    return tuple(views)


def _exact(value: Mapping[str, Any], fields: set[str], label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f"{label} fields must be exact")


def _freeze_json(value: Any) -> Any:
    from .domain import _freeze_json as freeze
    return freeze(value)


def _thaw_json(value: Any) -> Any:
    from .domain import _thaw_json as thaw
    return thaw(value)


def _unique(values: Iterable[str], label: str, *, chains: bool = False) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{label} must be a collection")
    result = tuple(_chain(value) if chains else _text(value, label) for value in values)
    if len(set(result)) != len(result):
        raise ValueError(f"{label} must not contain duplicates")
    return result


@dataclass(frozen=True, slots=True)
class HypothesisSet:
    hypotheses: tuple[Mapping[str, Any], ...]

    @property
    def chains(self) -> tuple[str, ...]:
        return tuple(item["chain_id"] for item in self.hypotheses)

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "hypotheses": _thaw_json(self.hypotheses)}


def admit_hypothesis_set(value: Mapping[str, Any], *, effective_max_forks: int,
                         validate_candidate: Callable[[Mapping[str, Any]], object]) -> HypothesisSet:
    """Group existing candidates; the caller's ACI/postflight raises on failure."""
    _exact(value, {"schema_version", "hypotheses"}, "HypothesisSet/v1")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("HypothesisSet schema_version must be 1")
    if type(effective_max_forks) is not int or effective_max_forks < 1:
        raise ValueError("effective_max_forks must be a positive integer")
    members = value["hypotheses"]
    if not isinstance(members, list) or not 1 <= len(members) <= effective_max_forks:
        raise ValueError("hypothesis batch must contain 1..effective_max_forks members")
    for item in members:
        _exact(item, {"chain_id", "candidate"}, "hypothesis batch member")
        _chain(item["chain_id"])
        if not isinstance(item["candidate"], Mapping):
            raise ValueError("hypothesis candidate must be an object")
    _unique((item["chain_id"] for item in members), "batch chains", chains=True)
    copied = json.loads(json.dumps(members, ensure_ascii=False, allow_nan=False))
    for item in copied:
        validate_candidate(item["candidate"])
    return HypothesisSet(tuple(_freeze_json(item) for item in sorted(copied, key=lambda item: item["chain_id"])))


@dataclass(frozen=True, slots=True, kw_only=True)
class ComparedTip:
    run_id: str
    component_id: str
    expert_slot: str
    chain_id: str
    node_id: str
    experiment_id: str
    outcome_id: str
    outcome_fact_id: str
    artifact_id: str
    method_family_id: str
    outcome: str
    result_kind: str
    metric_value: str | None
    measurement: Mapping[str, Any] | None
    parser_sources: tuple[tuple[str, bytes], ...] | None = None

    @property
    def comparable(self) -> bool:
        return self.outcome in {"SUCCESS", "CLEAN_REFUTATION"}

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return (self.chain_id, self.node_id, self.experiment_id, self.outcome_id)

    def to_dict(self) -> dict[str, Any]:
        # Parser bytes are bound source inputs, never duplicated in a selection.
        return {name: _thaw_json(getattr(self, name)) for name in self.__dataclass_fields__ if name != "parser_sources"}


def _accepted_payload(row: Mapping[str, Any], fact_type: str) -> Mapping[str, Any]:
    payload = json.loads(row["payload_json"]) if "payload_json" in row else row
    if not isinstance(payload, Mapping) or payload.get("fact_type") != fact_type:
        raise ValueError(f"compared tip requires an accepted {fact_type} fact")
    for name in ("fact_id", "run_id"):
        _text(payload.get(name), name)
        if row.get(name) != payload[name]:
            raise ValueError("accepted fact row and payload ownership differ")
    return payload


def compared_tip(chain_id: str, *, hypothesis: Mapping[str, Any], preregistration: Mapping[str, Any],
                 outcome: Mapping[str, Any], parser_sources: Mapping[str, bytes] | None = None) -> ComparedTip:
    """Join explicit accepted identities; never choose a latest/maximum result."""
    from .domain import ComponentResultKind, MetricDirection, MetricSpec, ScientificOutcome, normalize_metric_value
    from .measurement_contract import ScientificMeasurement, measurement_parser_files

    _chain(chain_id)
    hypothesis = _accepted_payload(hypothesis, "HypothesisProposed")
    preregistration = _accepted_payload(preregistration, "ExperimentPreregistered")
    outcome = _accepted_payload(outcome, "CanonicalOutcomeAccepted")
    for name in ("run_id", "component_id", "method_family_id"):
        if len({_text(item.get(name), name) for item in (hypothesis, preregistration, outcome)}) != 1:
            raise ValueError("compared tip fact ownership differs")
    for name, left, right in (("hypothesis_id", hypothesis, preregistration),
                             ("experiment_id", preregistration, outcome), ("execution_id", preregistration, outcome),
                             ("result_kind", preregistration, outcome)):
        if _text(left.get(name), name) != _text(right.get(name), name):
            raise ValueError(f"compared tip has a mismatched {name}")
    scientific = ScientificOutcome(outcome.get("outcome")) in {ScientificOutcome.SUCCESS, ScientificOutcome.CLEAN_REFUTATION}
    kind = ComponentResultKind(outcome["result_kind"])
    metric_value, measurement, sources = None, None, None
    if kind is ComponentResultKind.METRIC:
        raw_metric = preregistration.get("metric")
        _exact(raw_metric, {"name", "direction", "target"}, "preregistered metric")
        metric = MetricSpec(name=raw_metric["name"], direction=MetricDirection(raw_metric["direction"]), target=raw_metric["target"])
        measurement = {"metric": {"name": metric.name, "direction": metric.direction.value, "target": metric.target}, "comparison": "VALUE"}
        if preregistration.get("measurement") is not None:
            declaration = ScientificMeasurement.from_dict(preregistration["measurement"])
            if declaration.metric != metric:
                raise ValueError("declared measurement differs from preregistered metric")
            measurement = declaration.to_dict()
            if parser_sources is not None:
                sources = tuple(sorted(measurement_parser_files(declaration, parser_sources).items()))
        if scientific:
            metric_value = normalize_metric_value(outcome.get("metric_value"))
    if not (scientific and kind is ComponentResultKind.METRIC) and outcome.get("metric_value") is not None:
        raise ValueError("non-comparable/artifact outcome cannot carry a metric")
    return ComparedTip(run_id=hypothesis["run_id"], component_id=hypothesis["component_id"],
        expert_slot=_text(hypothesis.get("proposer_role"), "proposer_role"), chain_id=chain_id,
        node_id=hypothesis["hypothesis_id"], experiment_id=preregistration["experiment_id"],
        outcome_id=_text(outcome.get("outcome_id"), "outcome_id"), outcome_fact_id=outcome["fact_id"],
        artifact_id=_text(outcome.get("artifact_id"), "artifact_id"), method_family_id=hypothesis["method_family_id"],
        outcome=outcome["outcome"], result_kind=kind.value, metric_value=metric_value,
        measurement=_freeze_json(measurement), parser_sources=sources)


def order_compared_tips(tips: Iterable[ComparedTip], *, numeric: bool = False) -> tuple[ComparedTip, ...]:
    """Stable display order by default; numeric comparison is explicitly opted in."""
    ordered = tuple(sorted(tips, key=lambda tip: tip.identity))
    if not numeric or not ordered:
        return ordered
    first = ordered[0]
    if any(not tip.comparable or tip.metric_value is None or tip.measurement is None or tip.measurement != first.measurement for tip in ordered):
        raise ValueError("numeric ordering requires equal comparable metric declarations")
    if "parser_files" in first.measurement and any(tip.parser_sources is None or tip.parser_sources != first.parser_sources for tip in ordered):
        raise ValueError("numeric ordering requires identical declared parser source bytes")
    maximize = first.measurement["metric"]["direction"] == "MAXIMIZE"
    return tuple(sorted(ordered, key=lambda tip: (
        Decimal(tip.metric_value).copy_negate() if maximize else Decimal(tip.metric_value), tip.identity)))


@dataclass(frozen=True, slots=True, kw_only=True)
class AdjudicationCandidate:
    """Untrusted complete-batch shape; accepted tip semantics remain Host-bound."""
    verdicts: tuple[Mapping[str, Any], ...]
    keep: tuple[str, ...]
    prune: tuple[str, ...]
    best_chain: str | None
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "verdicts": _thaw_json(self.verdicts), "keep": list(self.keep),
                "prune": list(self.prune), "best_chain": self.best_chain, "summary": self.summary}


def admit_adjudication_candidate(value: Mapping[str, Any]) -> AdjudicationCandidate:
    _exact(value, {"schema_version", "verdicts", "keep", "prune", "best_chain", "summary"}, "Adjudication/v1")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("Adjudication schema_version must be 1")
    if not isinstance(value["verdicts"], list) or not value["verdicts"]:
        raise ValueError("verdicts must be a nonempty array")
    verdicts = []
    for item in value["verdicts"]:
        _exact(item, {"chain_id", "node_id", "status", "evidence"}, "chain verdict")
        _chain(item["chain_id"])
        _text(item["node_id"], "canonical hypothesis")
        if _text(item["status"], "verdict status") not in {"verified", "refuted", "inconclusive"}:
            raise ValueError("verdict status must be verified/refuted/inconclusive")
        if not isinstance(item["evidence"], list):
            raise ValueError("verdict evidence must be an array")
        _unique(item["evidence"], "verdict evidence")
        verdicts.append(_freeze_json(item))
    chains = set(_unique((item["chain_id"] for item in verdicts), "verdict chains"))
    _unique((item["node_id"] for item in verdicts), "verdict hypotheses")
    if not isinstance(value["keep"], list) or not isinstance(value["prune"], list):
        raise ValueError("keep/prune must be arrays")
    keep, prune = (set(_unique(value[name], name, chains=True)) for name in ("keep", "prune"))
    if keep & prune or keep | prune != chains:
        raise ValueError("keep/prune must be disjoint and cover the complete exact verdict batch")
    best = value["best_chain"]
    if best is not None and _chain(best) not in keep:
        raise ValueError("best_chain must name a kept batch chain")
    if prune and best is None:
        raise ValueError("pruning requires a selected winner")
    return AdjudicationCandidate(verdicts=tuple(verdicts), keep=tuple(sorted(keep)), prune=tuple(sorted(prune)),
        best_chain=best, summary=_text(value["summary"], "adjudication summary"))


@dataclass(frozen=True, slots=True, kw_only=True)
class Adjudication:
    verdicts: tuple[Mapping[str, Any], ...]
    keep: tuple[str, ...]
    prune: tuple[str, ...]
    best_chain: str | None
    summary: str
    selected_tip: ComparedTip | None
    pruned_by: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "verdicts": _thaw_json(self.verdicts), "keep": list(self.keep),
                "prune": list(self.prune), "best_chain": self.best_chain, "summary": self.summary}


def pruning_relation(*, loser_chain: str | None, loser_hypothesis_id: str, winner_chain: str | None,
                     winner_hypothesis_id: str, source_attempt_id: str, score_refs: Iterable[str],
                     debate_id: str | None = None) -> dict[str, Any]:
    """A control relationship, shared by batch and cross-expert adjudication."""
    for chain in (loser_chain, winner_chain):
        if chain is not None:
            _chain(chain)
    for name, value in (("loser hypothesis", loser_hypothesis_id), ("winner hypothesis", winner_hypothesis_id),
                        ("source Attempt", source_attempt_id)):
        _text(value, name)
    if loser_hypothesis_id == winner_hypothesis_id:
        raise ValueError("pruning requires distinct canonical hypotheses")
    if debate_id is not None:
        _text(debate_id, "debate_id")
    return {"loser_chain": loser_chain, "loser_hypothesis_id": loser_hypothesis_id,
            "winner_chain": winner_chain, "winner_hypothesis_id": winner_hypothesis_id,
            "source_attempt_id": source_attempt_id, "debate_id": debate_id,
            "score_refs": list(_unique(score_refs, "pruning score references"))}


def admit_adjudication(value: Mapping[str, Any], *, lineage: ExpertLineage, batch: Mapping[str, str],
                       terminal_chains: Iterable[str], terminal_tips: Mapping[str, ComparedTip | None],
                       source_attempt_id: str, accepted_reference_ids: Iterable[str] = (),
                       required_method_family_id: str | None = None, debate_id: str | None = None) -> Adjudication:
    """Validate one complete terminal batch; Host persists its exact selected tip."""
    value = admit_adjudication_candidate(value).to_dict()
    chains = set(_unique(batch, "batch chains", chains=True))
    if not chains or len(_unique(batch.values(), "batch hypotheses")) != len(chains):
        raise ValueError("accepted batch requires unique hypothesis identities")
    if set(_unique(terminal_chains, "terminal chains", chains=True)) != chains or set(terminal_tips) != chains:
        raise ValueError("terminal comparison set must exactly match the accepted batch")
    _text(source_attempt_id, "source_attempt_id")
    if debate_id is not None:
        _text(debate_id, "debate_id")
    references = set(_unique(accepted_reference_ids, "accepted references")) | set(batch.values())
    for chain, tip in terminal_tips.items():
        if tip is None:
            continue
        if not isinstance(tip, ComparedTip) or tip.chain_id != chain or tip.node_id != batch[chain]:
            raise ValueError("compared tip is outside the exact batch hypothesis binding")
        if (tip.run_id, tip.component_id, tip.expert_slot) != (lineage.run_id, lineage.component_id, lineage.expert_slot):
            raise ValueError("compared tip belongs to another participant")
        if tip.comparable and required_method_family_id is not None and tip.method_family_id != required_method_family_id:
            raise ValueError("required-input producer method family differs")
        references.update((tip.node_id, tip.experiment_id, tip.outcome_id, tip.outcome_fact_id, tip.artifact_id))
    raw_verdicts = value["verdicts"]
    if not isinstance(raw_verdicts, list):
        raise ValueError("verdicts must be an array")
    verdicts = {}
    for verdict in raw_verdicts:
        _exact(verdict, {"chain_id", "node_id", "status", "evidence"}, "chain verdict")
        chain = _chain(verdict["chain_id"])
        if chain not in chains or chain in verdicts or verdict["node_id"] != batch[chain]:
            raise ValueError("verdicts must cite each exact batch hypothesis once")
        tip = terminal_tips[chain]
        expected = {"SUCCESS": "verified", "CLEAN_REFUTATION": "refuted"}.get(None if tip is None else tip.outcome, "inconclusive")
        if verdict["status"] != expected:
            raise ValueError("verdict contradicts accepted outcome; failure is not refutation")
        if not isinstance(verdict["evidence"], list):
            raise ValueError("verdict evidence must be an array")
        evidence = set(_unique(verdict["evidence"], "verdict evidence"))
        missing = [tip.outcome_id] if tip is not None and tip.comparable and tip.outcome_id not in evidence else []
        inadmissible = sorted(evidence - references)
        if missing or inadmissible:
            raise ValueError("verdict evidence must cite its exact outcome and admitted reference domain; "
                f"chain_id={json.dumps(chain)}; missing_outcome_ids={json.dumps(missing)}; "
                f"inadmissible_reference_ids={json.dumps(inadmissible)}")
        verdicts[chain] = _freeze_json({**verdict, "evidence": sorted(evidence)})
    if set(verdicts) != chains:
        raise ValueError("verdicts must cover the complete accepted batch")
    if not isinstance(value["keep"], list) or not isinstance(value["prune"], list):
        raise ValueError("keep/prune must be arrays")
    keep = set(_unique(value["keep"], "keep", chains=True))
    prune = set(_unique(value["prune"], "prune", chains=True))
    if keep & prune or keep | prune != chains:
        raise ValueError("keep/prune must be disjoint and cover the exact batch")
    best = value["best_chain"]
    selected = None
    if best is not None:
        _chain(best)
        if best not in keep:
            raise ValueError("best_chain must name a kept batch chain")
        selected = terminal_tips[best]
        if selected is None or not selected.comparable:
            raise ValueError("best_chain requires an accepted comparable terminal tip")
    if prune and selected is None:
        raise ValueError("pruning requires a selected winner")
    summary = _text(value["summary"], "adjudication summary")
    pruned = tuple(pruning_relation(loser_chain=chain, loser_hypothesis_id=batch[chain], winner_chain=best,
        winner_hypothesis_id=selected.node_id, source_attempt_id=source_attempt_id, debate_id=debate_id,
        score_refs=verdicts[chain]["evidence"]) for chain in sorted(prune))
    return Adjudication(verdicts=tuple(verdicts[chain] for chain in sorted(chains)), keep=tuple(sorted(keep)),
        prune=tuple(sorted(prune)), best_chain=best, summary=summary, selected_tip=selected, pruned_by=pruned)


__all__ = [
    "AgentTaskContext", "AuxiliaryRequestKind", "AuxiliaryTask", "AuxiliaryTaskView",
    "DerivedWorkState", "ExpertLineage", "ExpertLineageDecision",
    "ExpertLineageInput", "HypothesisUpdateDisposition", "ResearchNodeKey",
    "ResearchNodeKind", "ResearchNodeSubkind", "ResearchNodeTask",
    "ResearchNodeView", "ScientificNodeRefs", "derive_research_node_views",
    "reduce_expert_lineage", "reduce_expert_lineages", "task_context_from_dict",
    "HypothesisSet", "ComparedTip", "Adjudication", "AdjudicationCandidate", "admit_adjudication_candidate", "admit_hypothesis_set", "compared_tip",
    "admit_adjudication", "order_compared_tips", "chain_session_scope", "pruning_relation",
]
