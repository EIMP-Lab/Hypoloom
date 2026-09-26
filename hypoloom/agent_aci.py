"""Versioned, narrow Agent communication interface for Hypoloom.

The Agent is an untrusted candidate producer.  This module owns only the wire
envelopes and deterministic postflight validation; it has no persistence or
workflow authority.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Mapping, TypeAlias

from .collaboration import (
    ExpertSignal,
    RendezvousDisposition,
    RendezvousReply,
    RendezvousRequest,
)
from .domain import (
    AgentCommand,
    HypothesisAssessment,
    MetricDirection,
    MetricSpec,
    ResourceEstimate,
    ResourcePreference,
)
from .expert_loop import (
    AgentTaskContext,
    AuxiliaryRequestKind,
    AuxiliaryTask,
    AdjudicationCandidate,
    HypothesisUpdateDisposition,
    ResearchNodeKind,
    ResearchNodeSubkind,
    ResearchNodeTask,
    task_context_from_dict,
    admit_hypothesis_set,
    admit_adjudication_candidate,
    chain_session_scope,
)
from .literature import LiteratureQuery
from .measurement_contract import ScientificMeasurement, measurement_parser_files
from .grounding_background import GroundingBackground, validate_web_sources
from .expert_interaction import ObserverProposal, DebateStatement, DebateJudgment


# Retained wire constants do not change when the Host promotes its default.
# Fresh Host writes select CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION explicitly.
ACI_SCHEMA_VERSION = 6
_RETAINED_EXPERT_LOOP_ACI_SCHEMA_VERSIONS = frozenset({7, 8, 9, 10, 11, 12})
EXPERT_LOOP_ACI_SCHEMA_VERSION = 13
MULTICHAIN_ACI_SCHEMA_VERSION = 14
GROUNDING_DIALOGUE_ACI_SCHEMA_VERSION = 15
SHARED_ACCEPTANCE_ACI_SCHEMA_VERSION = 16
UNSELECTED_REVISION_ACI_SCHEMA_VERSION = 17
RESOURCE_LEARNING_ACI_SCHEMA_VERSION = 18
REFERENCE_ALIAS_ACI_SCHEMA_VERSION = 19
PAPER_READING_ACI_SCHEMA_VERSION = 20
DIRECTED_RESEARCH_ACI_SCHEMA_VERSION = 21
EXECUTION_HANDOFF_ACI_SCHEMA_VERSION = 22
LEADER_GUIDANCE_ACI_SCHEMA_VERSION = 23
BOUNDED_CONTEXT_ACI_SCHEMA_VERSION = 24
MEASURED_CONTEXT_ACI_SCHEMA_VERSION = 25
CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION = MEASURED_CONTEXT_ACI_SCHEMA_VERSION
EXPERIMENT_SUBMISSION_MIN_ACI_VERSION = 11
GROUNDING_WEB_MIN_ACI_VERSION = 13
LITERATURE_WEB_MIN_ACI_VERSION = 14
_READABLE_ACI_SCHEMA_VERSIONS = frozenset(
    {
        5,
        ACI_SCHEMA_VERSION,
        *_RETAINED_EXPERT_LOOP_ACI_SCHEMA_VERSIONS,
        EXPERT_LOOP_ACI_SCHEMA_VERSION,
        MULTICHAIN_ACI_SCHEMA_VERSION,
        GROUNDING_DIALOGUE_ACI_SCHEMA_VERSION,
        SHARED_ACCEPTANCE_ACI_SCHEMA_VERSION,
        UNSELECTED_REVISION_ACI_SCHEMA_VERSION,
        RESOURCE_LEARNING_ACI_SCHEMA_VERSION,
        REFERENCE_ALIAS_ACI_SCHEMA_VERSION,
        PAPER_READING_ACI_SCHEMA_VERSION,
        DIRECTED_RESEARCH_ACI_SCHEMA_VERSION,
        EXECUTION_HANDOFF_ACI_SCHEMA_VERSION,
        LEADER_GUIDANCE_ACI_SCHEMA_VERSION,
        BOUNDED_CONTEXT_ACI_SCHEMA_VERSION,
        MEASURED_CONTEXT_ACI_SCHEMA_VERSION,
    }
)
LEGACY_AGENT_COMMANDS = (
    AgentCommand.PROPOSE_HYPOTHESIS,
    AgentCommand.DRAFT_EXPERIMENT_SPEC,
    AgentCommand.ANSWER_RENDEZVOUS,
)
EXPERT_LOOP_AGENT_COMMANDS = tuple(command for command in AgentCommand if command not in LEGACY_AGENT_COMMANDS)
AGENT_COMMANDS = (*LEGACY_AGENT_COMMANDS, *EXPERT_LOOP_AGENT_COMMANDS)
_COMMANDS_BY_SCHEMA = {
    5: LEGACY_AGENT_COMMANDS,
    ACI_SCHEMA_VERSION: LEGACY_AGENT_COMMANDS,
    **{
        version: tuple(command for command in AGENT_COMMANDS
                       if (version >= 12 or command is not AgentCommand.CYCLE_REPORT)
                       and command not in {AgentCommand.ADJUDICATE, AgentCommand.SELF_EVOLVE, AgentCommand.GRAPH_EVOLVE, AgentCommand.FINAL_REPORT, AgentCommand.DEBATE_JUDGE})
        for version in {*_RETAINED_EXPERT_LOOP_ACI_SCHEMA_VERSIONS, EXPERT_LOOP_ACI_SCHEMA_VERSION}
    },
    MULTICHAIN_ACI_SCHEMA_VERSION: AGENT_COMMANDS,
    GROUNDING_DIALOGUE_ACI_SCHEMA_VERSION: AGENT_COMMANDS,
    SHARED_ACCEPTANCE_ACI_SCHEMA_VERSION: AGENT_COMMANDS,
    UNSELECTED_REVISION_ACI_SCHEMA_VERSION: AGENT_COMMANDS,
    RESOURCE_LEARNING_ACI_SCHEMA_VERSION: AGENT_COMMANDS,
    REFERENCE_ALIAS_ACI_SCHEMA_VERSION: AGENT_COMMANDS,
    PAPER_READING_ACI_SCHEMA_VERSION: AGENT_COMMANDS,
    DIRECTED_RESEARCH_ACI_SCHEMA_VERSION: AGENT_COMMANDS,
    EXECUTION_HANDOFF_ACI_SCHEMA_VERSION: AGENT_COMMANDS,
    LEADER_GUIDANCE_ACI_SCHEMA_VERSION: AGENT_COMMANDS,
    BOUNDED_CONTEXT_ACI_SCHEMA_VERSION: AGENT_COMMANDS,
    MEASURED_CONTEXT_ACI_SCHEMA_VERSION: AGENT_COMMANDS,
}
_COMMANDS_BY_SCHEMA = {version: tuple(command for command in commands
    if version >= PAPER_READING_ACI_SCHEMA_VERSION or command is not AgentCommand.PAPER_READ)
    for version, commands in _COMMANDS_BY_SCHEMA.items()}


def supports_experiment_submission(schema_version: object) -> bool:
    """Whether an ACI version includes admitted submits, not full schema support.

    Durable run versions are text; typed Agent requests carry integers. Keep
    this semantic threshold independent of the current envelope version.
    """
    if isinstance(schema_version, str):
        try:
            schema_version = int(schema_version)
        except ValueError:
            return False
    return (
        isinstance(schema_version, int)
        and not isinstance(schema_version, bool)
        and schema_version >= EXPERIMENT_SUBMISSION_MIN_ACI_VERSION
    )


def supports_grounding_web(schema_version: object) -> bool:
    if isinstance(schema_version, str):
        try: schema_version = int(schema_version)
        except ValueError: return False
    return isinstance(schema_version, int) and not isinstance(schema_version, bool) and schema_version >= GROUNDING_WEB_MIN_ACI_VERSION


def supports_literature_web(schema_version: object) -> bool:
    if isinstance(schema_version, str):
        try:
            schema_version = int(schema_version)
        except ValueError:
            return False
    return isinstance(schema_version, int) and not isinstance(schema_version, bool) and schema_version >= LITERATURE_WEB_MIN_ACI_VERSION


class AgentStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


class AgentACIError(ValueError):
    """A typed Agent envelope failed schema or postflight validation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentBudget:
    max_turns: int
    max_tokens: int
    max_output_bytes: int
    wall_seconds: float
    max_cost: str | None = "0"
    token_enforcement_mode: str = "enforce"

    def __post_init__(self) -> None:
        for name in ("max_turns", "max_tokens", "max_output_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise AgentACIError("invalid_budget", f"{name} must be a positive integer")
        if isinstance(self.wall_seconds, bool) or not isinstance(self.wall_seconds, (int, float)):
            raise AgentACIError("invalid_budget", "wall_seconds must be numeric")
        if not 0 < float(self.wall_seconds) < float("inf"):
            raise AgentACIError("invalid_budget", "wall_seconds must be finite and positive")
        if self.max_cost is not None and (not isinstance(self.max_cost, str) or not self.max_cost or self.max_cost != self.max_cost.strip()):
            raise AgentACIError("invalid_budget", "max_cost must be decimal text")
        if self.max_cost is not None:
            _canonical_decimal(self.max_cost, "max_cost", nonnegative=True)
        if (
            not isinstance(self.token_enforcement_mode, str)
            or self.token_enforcement_mode not in {"enforce", "observe"}
        ):
            raise AgentACIError(
                "invalid_budget",
                "token_enforcement_mode must be enforce or observe",
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AgentBudget:
        _strict_keys(
            value,
            {
                "max_turns",
                "max_tokens",
                "max_output_bytes",
                "wall_seconds",
                "max_cost",
                "token_enforcement_mode",
            },
            "budget",
            optional={"token_enforcement_mode"},
        )
        try:
            return cls(**dict(value))
        except TypeError as exc:
            raise AgentACIError("invalid_budget", str(exc)) from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class EmbeddedInput:
    """One bounded immutable input whose actual bytes are in the request digest."""

    ref: str
    media_type: str
    content_base64: str

    def __post_init__(self) -> None:
        _text(self.ref, "embedded input ref")
        _text(self.media_type, "embedded input media_type")
        self.content_bytes()

    @classmethod
    def from_bytes(cls, ref: str, media_type: str, content: bytes) -> EmbeddedInput:
        if not isinstance(content, bytes):
            raise AgentACIError("invalid_embedded_input", "content must be bytes")
        return cls(ref=ref, media_type=media_type, content_base64=base64.b64encode(content).decode("ascii"))

    def content_bytes(self) -> bytes:
        try:
            return base64.b64decode(self.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise AgentACIError("invalid_embedded_input", f"{self.ref!r} content is not base64") from exc

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> EmbeddedInput:
        _strict_keys(value, {"ref", "media_type", "content_base64"}, "embedded input")
        try:
            return cls(**dict(value))
        except (KeyError, TypeError) as exc:
            raise AgentACIError("invalid_embedded_input", str(exc)) from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkspaceArtifact:
    """One bounded candidate file carried as base64 in the response package."""

    path: str
    content_base64: str

    def __post_init__(self) -> None:
        _relative_path(self.path)
        self.content_bytes()

    @classmethod
    def from_bytes(cls, path: str, content: bytes) -> WorkspaceArtifact:
        return cls(path=path, content_base64=base64.b64encode(content).decode("ascii"))

    def content_bytes(self) -> bytes:
        try:
            return base64.b64decode(self.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise AgentACIError("invalid_workspace_artifact", f"{self.path!r} is not base64") from exc

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "content_base64": self.content_base64}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> WorkspaceArtifact:
        _strict_keys(value, {"path", "content_base64"}, "workspace artifact")
        try:
            return cls(path=value["path"], content_base64=value["content_base64"])
        except KeyError as exc:
            raise AgentACIError("invalid_workspace_artifact", f"missing {exc.args[0]}") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class DeclaredEffect:
    effect: str
    command_id: str
    attempt_id: str

    def __post_init__(self) -> None:
        _text(self.effect, "effect")
        _text(self.command_id, "command_id")
        _text(self.attempt_id, "attempt_id")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DeclaredEffect:
        _strict_keys(value, {"effect", "command_id", "attempt_id"}, "declared effect")
        try:
            return cls(**dict(value))
        except TypeError as exc:
            raise AgentACIError("invalid_effect", str(exc)) from exc


def required_effect(command: AgentCommand) -> str:
    if command is AgentCommand.PROPOSE_HYPOTHESIS:
        return "HYPOTHESIS_CANDIDATE_PROPOSED"
    if command is AgentCommand.DRAFT_EXPERIMENT_SPEC:
        return "EXPERIMENT_SPEC_CANDIDATE_DRAFTED"
    if command is AgentCommand.ANSWER_RENDEZVOUS:
        return "RENDEZVOUS_REPLY_PRODUCED"
    if command in EXPERT_LOOP_AGENT_COMMANDS:
        return f"{command.value}_CANDIDATE_PRODUCED"
    raise AgentACIError("unknown_command", repr(command))


def required_output_schema(
    command: AgentCommand,
    task_context: AgentTaskContext | None = None,
    *,
    schema_version: int | None = None,
) -> str:
    version = EXPERT_LOOP_ACI_SCHEMA_VERSION if schema_version is None else schema_version
    if command is AgentCommand.ADJUDICATE:
        if version < MULTICHAIN_ACI_SCHEMA_VERSION:
            raise AgentACIError("invalid_schema", "ADJUDICATE requires ACI14")
        return "Adjudication/v1"
    if command is AgentCommand.OBSERVER and version >= 14:
        return "ObserverProposal/v1"
    if command is AgentCommand.DEBATE and version >= 14: return "DebateStatement/v1"
    if command is AgentCommand.DEBATE_JUDGE: return "DebateJudgment/v1"
    if command is AgentCommand.PROPOSE_HYPOTHESIS:
        return "HypothesisCandidate/v2"
    if command is AgentCommand.DRAFT_EXPERIMENT_SPEC:
        return "ExperimentSpecCandidate/v3"
    if command is AgentCommand.ANSWER_RENDEZVOUS:
        return "RendezvousReply/v1"
    if (
        command is AgentCommand.LITERATURE_REVIEW
        and _is_literature_query_plan(task_context)
    ):
        version = (
            EXPERT_LOOP_ACI_SCHEMA_VERSION
            if schema_version is None
            else schema_version
        )
        if version == 8:
            return "LiteratureQuery/v1"
        if version in _READABLE_ACI_SCHEMA_VERSIONS and version >= 9:
            return "LiteratureQuery/v2"
        raise AgentACIError(
            "invalid_schema",
            f"ACI v{version} does not support LITERATURE_REVIEW/QUERY_PLAN",
        )
    if command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
        return "ExperimentDesignCandidate/v1"
    if command is AgentCommand.GROUNDING and supports_grounding_web(EXPERT_LOOP_ACI_SCHEMA_VERSION if schema_version is None else schema_version):
        if version >= SHARED_ACCEPTANCE_ACI_SCHEMA_VERSION: return "GroundingCandidate/v3"
        return "GroundingCandidate/v2" if version >= GROUNDING_DIALOGUE_ACI_SCHEMA_VERSION else "GroundingCandidate/v1"
    if command is AgentCommand.HYPOTHESIS_PROPOSAL:
        subkind = _hypothesis_proposal_subkind(task_context)
        if subkind is ResearchNodeSubkind.SYNTHESIS:
            if version >= MULTICHAIN_ACI_SCHEMA_VERSION:
                return "HypothesisSet/v1"
            return "HypothesisCandidate/v2"
        return "ExpertLoopCandidate/v1"
    if command in EXPERT_LOOP_AGENT_COMMANDS:
        return "ExpertLoopCandidate/v1"
    raise AgentACIError("unknown_command", repr(command))


_NODE_COMMANDS = {
    AgentCommand.LITERATURE_REVIEW: ResearchNodeKind.LITERATURE_REVIEW,
    AgentCommand.HYPOTHESIS_PROPOSAL: ResearchNodeKind.HYPOTHESIS_PROPOSAL,
    AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT: ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT,
    AgentCommand.EXPERIMENT_EXECUTION: ResearchNodeKind.EXPERIMENT_EXECUTION,
    AgentCommand.RESULT_ANALYSIS: ResearchNodeKind.RESULT_ANALYSIS,
    AgentCommand.HYPOTHESIS_UPDATE: ResearchNodeKind.HYPOTHESIS_UPDATE,
    AgentCommand.CYCLE_REPORT: ResearchNodeKind.CYCLE_REPORT,
    AgentCommand.ADJUDICATE: ResearchNodeKind.ADJUDICATE,
    AgentCommand.SELF_EVOLVE: ResearchNodeKind.SELF_EVOLVE,
}
_AUXILIARY_COMMANDS = {
    AgentCommand.PAPER_READ: "PAPER_READ",
    AgentCommand.GROUNDING: "GROUNDING",
    AgentCommand.OBSERVER: "OBSERVER",
    AgentCommand.DEBATE: "DEBATE",
    AgentCommand.LEADER: "LEADER",
    AgentCommand.COMPACT_CONTEXT: "CONTEXT_COMPACTION",
    AgentCommand.GRAPH_EVOLVE: "GRAPH_EVOLVE",
    AgentCommand.FINAL_REPORT: "FINAL_REPORT",
    AgentCommand.DEBATE_JUDGE: "DEBATE_JUDGE",
}


def _validate_task_context(command: AgentCommand, context: AgentTaskContext | None) -> None:
    if command is AgentCommand.LITERATURE_REVIEW and _is_grounding_query_plan(context):
        return
    expected_node = _NODE_COMMANDS.get(command)
    if expected_node is not None:
        if not isinstance(context, ResearchNodeTask) or context.key.node_kind is not expected_node:
            raise AgentACIError("invalid_task_context", f"{command.value} requires its matching research node")
        return
    expected_auxiliary = _AUXILIARY_COMMANDS.get(command)
    if not isinstance(context, AuxiliaryTask) or context.kind.value != expected_auxiliary:
        raise AgentACIError("invalid_task_context", f"{command.value} requires its matching auxiliary task")


def _hypothesis_proposal_subkind(
    context: AgentTaskContext | None,
) -> ResearchNodeSubkind:
    if (
        not isinstance(context, ResearchNodeTask)
        or context.key.node_kind is not ResearchNodeKind.HYPOTHESIS_PROPOSAL
    ):
        raise AgentACIError(
            "invalid_task_context",
            "HYPOTHESIS_PROPOSAL requires its matching research node",
        )
    return context.key.subkind


def _is_literature_query_plan(context: AgentTaskContext | None) -> bool:
    return (
        isinstance(context, ResearchNodeTask)
        and context.key.node_kind is ResearchNodeKind.LITERATURE_REVIEW
        and context.key.subkind is ResearchNodeSubkind.QUERY_PLAN
    ) or _is_grounding_query_plan(context)


def _is_grounding_query_plan(context: AgentTaskContext | None) -> bool:
    return (
        isinstance(context, AuxiliaryTask)
        and context.kind is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN
    )


def _is_literature_main(context: AgentTaskContext | None) -> bool:
    return (
        isinstance(context, ResearchNodeTask)
        and context.key.node_kind is ResearchNodeKind.LITERATURE_REVIEW
        and context.key.subkind is ResearchNodeSubkind.MAIN
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class HypothesisCandidate:
    """Untrusted component-scoped proposal returned by an Agent."""

    component_id: str
    proposer_role: str
    method_family_id: str
    claim: str
    assumptions: tuple[str, ...] = ()
    parent_hypothesis_id: str | None = None
    relation: str | None = None
    measurement: ScientificMeasurement | None = None

    def __post_init__(self) -> None:
        for name in ("component_id", "proposer_role", "method_family_id", "claim"):
            _text(getattr(self, name), name)
        _text_tuple(self.assumptions, "assumptions", allow_empty=True)
        if self.parent_hypothesis_id is not None:
            _text(self.parent_hypothesis_id, "parent_hypothesis_id")
        if (self.parent_hypothesis_id is None) != (self.relation is None):
            raise AgentACIError(
                "invalid_candidate",
                "parent_hypothesis_id and relation must be supplied together",
            )
        if self.relation is not None:
            _text(self.relation, "relation")


@dataclass(frozen=True, slots=True, kw_only=True)
class ExperimentSpecCandidate:
    """Untrusted owner draft bound to one component and method family."""

    component_id: str
    owner_role: str
    method_family_id: str
    method: str
    metrics: tuple[MetricSpec, ...]
    acceptance_criteria: tuple[str, ...]
    evaluator_version: str
    artifact_requirements: tuple[str, ...]
    resource_preference: ResourcePreference
    measurement: ScientificMeasurement | None = None
    execution_argv: tuple[str, ...] | None = None
    evaluation_scope: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "component_id",
            "owner_role",
            "method_family_id",
            "method",
            "evaluator_version",
        ):
            _text(getattr(self, name), name)
        if not isinstance(self.metrics, tuple) or not all(
            isinstance(metric, MetricSpec) for metric in self.metrics
        ):
            raise AgentACIError(
                "invalid_candidate", "metrics must contain only MetricSpec values"
            )
        if len({metric.name for metric in self.metrics}) != len(self.metrics):
            raise AgentACIError("invalid_candidate", "metric names must be unique")
        _text_tuple(
            self.acceptance_criteria, "acceptance_criteria", allow_empty=False
        )
        _text_tuple(
            self.artifact_requirements, "artifact_requirements", allow_empty=False
        )
        if not isinstance(self.resource_preference, ResourcePreference):
            raise AgentACIError(
                "invalid_candidate",
                "resource_preference must be a ResourcePreference",
            )
        if self.measurement is not None:
            if not isinstance(self.measurement, ScientificMeasurement) or self.metrics != (self.measurement.metric,):
                raise AgentACIError("invalid_measurement", "candidate metric must equal its scientific declaration")
            if (not isinstance(self.execution_argv, tuple) or not self.execution_argv
                    or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in self.execution_argv)):
                raise AgentACIError("invalid_command", "declared measurement requires explicit execution_argv")
        elif self.execution_argv is not None and self.evaluation_scope != "GOAL":
            raise AgentACIError("invalid_command", "execution_argv requires a scientific measurement declaration")
        if self.evaluation_scope is not None:
            if self.evaluation_scope not in {"LOCAL", "GOAL"}:
                raise AgentACIError("invalid_candidate", "evaluation_scope must be LOCAL or GOAL")
            if (not isinstance(self.execution_argv, tuple) or not self.execution_argv
                    or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in self.execution_argv)):
                raise AgentACIError("invalid_command", "shared acceptance requires explicit execution_argv")
            if self.measurement is None and (self.evaluation_scope != "GOAL" or self.metrics):
                raise AgentACIError("invalid_candidate", "a goal-only experiment has no fabricated local metric")


@dataclass(frozen=True, slots=True, kw_only=True)
class HypothesisSetMember:
    chain_id: str
    candidate: HypothesisCandidate


@dataclass(frozen=True, slots=True, kw_only=True)
class HypothesisSetCandidate:
    hypotheses: tuple[HypothesisSetMember, ...]

    def to_dict(self) -> dict[str, Any]:
        members = []
        for item in self.hypotheses:
            raw = asdict(item.candidate)
            if item.candidate.measurement is None:
                raw.pop("measurement")
            else:
                raw["measurement"] = item.candidate.measurement.to_dict()
            members.append({"chain_id": item.chain_id, "candidate": _json_value(raw)})
        return {"schema_version": 1, "hypotheses": members}


class IterationDisposition(str, Enum):
    ITERATE = "ITERATE"
    COMPLETE = "COMPLETE"


@dataclass(frozen=True, slots=True, kw_only=True)
class ExpertLoopCandidate:
    """Minimal typed output for an unconnected Stage-1 node or auxiliary task."""

    task_kind: str
    summary: str
    supporting_refs: tuple[str, ...]
    update_disposition: HypothesisUpdateDisposition | None = None
    iteration_disposition: IterationDisposition | None = None
    waiting_for_experiment_id: str | None = None

    def __post_init__(self) -> None:
        if self.waiting_for_experiment_id is not None:
            _text(self.waiting_for_experiment_id, "waiting_for_experiment_id")
        _text(self.task_kind, "task_kind")
        _text(self.summary, "summary")
        _text_tuple(self.supporting_refs, "supporting_refs", allow_empty=True)
        if self.update_disposition is not None and not isinstance(
            self.update_disposition, HypothesisUpdateDisposition
        ):
            raise AgentACIError("invalid_candidate", "update_disposition is invalid")
        if self.iteration_disposition is not None and not isinstance(self.iteration_disposition, IterationDisposition):
            raise AgentACIError("invalid_candidate", "iteration_disposition is invalid")


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphEvolutionCandidate(ExpertLoopCandidate):
    graph_evolution: tuple


@dataclass(frozen=True, slots=True, kw_only=True)
class GroundingCandidate(ExpertLoopCandidate):
    background: GroundingBackground | None
    web_sources: tuple[str, ...]
    clarification: Mapping[str, Any] | None = None
    acceptance: Any = None

    def __post_init__(self) -> None:
        ExpertLoopCandidate.__post_init__(self)
        if self.clarification is None:
            if not isinstance(self.background, GroundingBackground) or self.summary != self.background.project_summary:
                raise AgentACIError("invalid_candidate", "Grounding summary must derive from the typed background")
        else:
            question = self.clarification
            if self.background is not None or not isinstance(question, Mapping) or set(question) != {"question", "rationale", "options"}:
                raise AgentACIError("invalid_candidate", "Grounding must return a question or completed background, never both")
            for name in ("question", "rationale"):
                if not isinstance(question[name], str) or not question[name].strip() or "\x00" in question[name]:
                    raise AgentACIError("invalid_candidate", "Grounding clarification requires nonempty question and rationale")
            options = question["options"]
            if (not isinstance(options, (list, tuple)) or any(not isinstance(item, str) or not item.strip() or "\x00" in item for item in options)
                    or len(set(options)) != len(options) or self.summary != question["question"]):
                raise AgentACIError("invalid_candidate", "Grounding clarification options must be unique text; free-text answers remain allowed")
        try: validate_web_sources(self.web_sources, self.web_sources)
        except ValueError as exc: raise AgentACIError("invalid_candidate", str(exc)) from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class LiteratureSummaryCandidate(ExpertLoopCandidate):
    web_sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        ExpertLoopCandidate.__post_init__(self)
        try:
            validate_web_sources(self.web_sources, self.web_sources)
        except ValueError as exc:
            raise AgentACIError("invalid_candidate", str(exc)) from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentRequest:
    schema_version: int
    run_id: str
    owner_workflow_id: str
    command: AgentCommand
    command_id: str
    attempt_id: str
    attempt_ordinal: int
    role: str
    component_id: str
    method_family_id: str | None
    immutable_context_refs: tuple[str, ...]
    input_artifacts: tuple[EmbeddedInput, ...]
    required_output_schema: str
    effect_requirements: tuple[str, ...]
    tool_permission_profile: str
    budget: AgentBudget
    deadline: str
    max_input_bytes: int
    required_model_identity: str
    required_adapter_version: str
    task_context: AgentTaskContext | None = None

    def __post_init__(self) -> None:
        if self.schema_version not in _READABLE_ACI_SCHEMA_VERSIONS:
            raise AgentACIError(
                "wrong_schema_version",
                f"expected one of {sorted(_READABLE_ACI_SCHEMA_VERSIONS)!r}",
            )
        if not isinstance(self.command, AgentCommand) or self.command not in _COMMANDS_BY_SCHEMA[self.schema_version]:
            raise AgentACIError("unknown_command", repr(self.command))
        if self.schema_version < 7 and self.task_context is not None:
            raise AgentACIError("invalid_task_context", "ACI v5/v6 have no task context")
        if self.command in LEGACY_AGENT_COMMANDS and self.task_context is not None:
            raise AgentACIError("invalid_task_context", "legacy commands have no expert-loop task context")
        if self.command in EXPERT_LOOP_AGENT_COMMANDS:
            _validate_task_context(self.command, self.task_context)
            if (
                self.schema_version < 8
                and _is_literature_query_plan(self.task_context)
            ):
                raise AgentACIError(
                    "invalid_schema",
                    f"ACI v{self.schema_version} does not support LITERATURE_REVIEW/QUERY_PLAN",
                )
            if (
                _is_grounding_query_plan(self.task_context)
                and self.schema_version < 10
            ):
                raise AgentACIError(
                    "invalid_schema",
                    "role-neutral Grounding QUERY_PLAN requires the current expert ACI",
                )
            assert self.task_context is not None
            if self.command_id != self.task_context.command_id:
                raise AgentACIError("invalid_task_context", "command_id must equal the stable task command ID")
            if self.task_context.run_id != self.run_id:
                raise AgentACIError("invalid_task_context", "task run differs from the request")
            if isinstance(self.task_context, ResearchNodeTask) and (
                self.task_context.key.component_id != self.component_id
                or self.task_context.key.expert_slot != self.role
            ):
                raise AgentACIError("invalid_task_context", "node component/expert differs from the request")
            if isinstance(self.task_context, AuxiliaryTask) and (
                self.task_context.component_id not in {None, self.component_id}
                or self.task_context.expert_slot not in {None, self.role}
            ):
                raise AgentACIError("invalid_task_context", "auxiliary scope differs from the request")
        for name in (
            "run_id",
            "owner_workflow_id",
            "command_id",
            "attempt_id",
            "role",
            "component_id",
            "required_output_schema",
            "tool_permission_profile",
            "deadline",
            "required_model_identity",
            "required_adapter_version",
        ):
            _text(getattr(self, name), name)
        if self.method_family_id is not None:
            _text(self.method_family_id, "method_family_id")
        if (
            self.command in {
                AgentCommand.PROPOSE_HYPOTHESIS,
                AgentCommand.HYPOTHESIS_PROPOSAL,
            }
            and self.method_family_id is not None
        ):
            raise AgentACIError(
                "invalid_method_family_context",
                "proposal requests must not preselect a method family",
            )
        if (
            self.command in {
                AgentCommand.DRAFT_EXPERIMENT_SPEC,
                AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
            }
            and self.method_family_id is None
        ):
            raise AgentACIError(
                "invalid_method_family_context",
                "experiment-spec requests require a selected method family",
            )
        expected_schema = required_output_schema(
            self.command,
            self.task_context,
            schema_version=self.schema_version,
        )
        if self.required_output_schema != expected_schema:
            raise AgentACIError(
                "wrong_output_schema",
                f"expected {expected_schema!r} for {self.command.value}",
            )
        if isinstance(self.attempt_ordinal, bool) or not isinstance(self.attempt_ordinal, int):
            raise AgentACIError("invalid_attempt_ordinal", "attempt_ordinal must be an integer")
        if self.attempt_ordinal <= 0:
            raise AgentACIError("invalid_attempt_ordinal", "attempt_ordinal must be positive")
        _text_tuple(self.immutable_context_refs, "immutable_context_refs", allow_empty=True)
        if not isinstance(self.input_artifacts, tuple) or not all(
            isinstance(item, EmbeddedInput) for item in self.input_artifacts
        ):
            raise AgentACIError("invalid_embedded_input", "input_artifacts must be a tuple of EmbeddedInput")
        refs = [item.ref for item in self.input_artifacts]
        if len(set(refs)) != len(refs):
            raise AgentACIError("duplicate_input_artifact", "embedded input refs must be unique")
        if _is_hypothesis_synthesis(self):
            _synthesis_parent_hypothesis_ids(self)
        if isinstance(self.max_input_bytes, bool) or not isinstance(self.max_input_bytes, int) or self.max_input_bytes <= 0:
            raise AgentACIError("invalid_input_limit", "max_input_bytes must be a positive integer")
        input_bytes = sum(len(item.content_bytes()) for item in self.input_artifacts)
        if input_bytes > self.max_input_bytes:
            raise AgentACIError("input_too_large", f"embedded input package is {input_bytes} bytes")
        expected = (required_effect(self.command),)
        if self.effect_requirements != expected:
            raise AgentACIError("invalid_effect_requirements", f"expected exactly {expected!r}")
        if not isinstance(self.budget, AgentBudget):
            raise AgentACIError("invalid_budget", "budget must be AgentBudget")
        if self.budget.max_cost is None and self.schema_version < REFERENCE_ALIAS_ACI_SCHEMA_VERSION:
            raise AgentACIError("invalid_budget", "unbounded invocation cost requires ACI v19")
        if (
            self.schema_version == 5
            and self.budget.token_enforcement_mode != "enforce"
        ):
            raise AgentACIError(
                "invalid_budget",
                "ACI v5 only supports implicit enforce token mode",
            )

    def to_dict(self) -> dict[str, Any]:
        budget = self.budget.to_dict()
        if self.schema_version == 5:
            budget.pop("token_enforcement_mode")
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "owner_workflow_id": self.owner_workflow_id,
            "command": self.command.value,
            **({"task_context": None if self.task_context is None else self.task_context.to_dict()}
               if self.schema_version >= 7 else {}),
            "command_id": self.command_id,
            "attempt_id": self.attempt_id,
            "attempt_ordinal": self.attempt_ordinal,
            "role": self.role,
            "component_id": self.component_id,
            "method_family_id": self.method_family_id,
            "immutable_context_refs": list(self.immutable_context_refs),
            "input_artifacts": [item.to_dict() for item in self.input_artifacts],
            "required_output_schema": self.required_output_schema,
            "effect_requirements": list(self.effect_requirements),
            "tool_permission_profile": self.tool_permission_profile,
            "budget": budget,
            "deadline": self.deadline,
            "max_input_bytes": self.max_input_bytes,
            "required_model_identity": self.required_model_identity,
            "required_adapter_version": self.required_adapter_version,
        }

    def to_json(self) -> bytes:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AgentRequest:
        required = {
            "schema_version",
            "run_id",
            "owner_workflow_id",
            "command",
            "task_context",
            "command_id",
            "attempt_id",
            "attempt_ordinal",
            "role",
            "component_id",
            "method_family_id",
            "immutable_context_refs",
            "input_artifacts",
            "required_output_schema",
            "effect_requirements",
            "tool_permission_profile",
            "budget",
            "deadline",
            "max_input_bytes",
            "required_model_identity",
            "required_adapter_version",
        }
        schema_version = value.get("schema_version")
        _strict_keys(value, required, "Agent request", optional={"task_context"} if schema_version in {5, 6} else None)
        try:
            raw_budget = _mapping(value["budget"], "budget")
            schema_version = value["schema_version"]
            if schema_version in {5, 6} and "task_context" in value:
                raise AgentACIError("invalid_schema", "ACI v5/v6 request must omit task_context")
            if schema_version == 5 and "token_enforcement_mode" in raw_budget:
                raise AgentACIError(
                    "invalid_schema",
                    "ACI v5 budget must omit token_enforcement_mode",
                )
            if (
                schema_version
                in {
                    ACI_SCHEMA_VERSION,
                    *_RETAINED_EXPERT_LOOP_ACI_SCHEMA_VERSIONS,
                    EXPERT_LOOP_ACI_SCHEMA_VERSION,
                    MULTICHAIN_ACI_SCHEMA_VERSION,
                    GROUNDING_DIALOGUE_ACI_SCHEMA_VERSION,
                    SHARED_ACCEPTANCE_ACI_SCHEMA_VERSION,
                    UNSELECTED_REVISION_ACI_SCHEMA_VERSION,
                    RESOURCE_LEARNING_ACI_SCHEMA_VERSION,
                    REFERENCE_ALIAS_ACI_SCHEMA_VERSION,
                }
                and "token_enforcement_mode" not in raw_budget
            ):
                raise AgentACIError(
                    "invalid_schema",
                    "ACI v6 budget requires token_enforcement_mode",
                )
            return cls(
                schema_version=schema_version,
                run_id=value["run_id"],
                owner_workflow_id=value["owner_workflow_id"],
                command=_enum(AgentCommand, value["command"], "command"),
                task_context=(None if value.get("task_context") is None else
                              task_context_from_dict(_mapping(value["task_context"], "task_context"))),
                command_id=value["command_id"],
                attempt_id=value["attempt_id"],
                attempt_ordinal=value["attempt_ordinal"],
                role=value["role"],
                component_id=value["component_id"],
                method_family_id=value["method_family_id"],
                immutable_context_refs=_tuple(value["immutable_context_refs"], "immutable_context_refs"),
                input_artifacts=tuple(
                    EmbeddedInput.from_dict(_mapping(item, "embedded input"))
                    for item in _sequence(value["input_artifacts"], "input_artifacts")
                ),
                required_output_schema=value["required_output_schema"],
                effect_requirements=_tuple(value["effect_requirements"], "effect_requirements"),
                tool_permission_profile=value["tool_permission_profile"],
                budget=AgentBudget.from_dict(raw_budget),
                deadline=value["deadline"],
                max_input_bytes=value["max_input_bytes"],
                required_model_identity=value["required_model_identity"],
                required_adapter_version=value["required_adapter_version"],
            )
        except (KeyError, ValueError) as exc:
            if isinstance(exc, AgentACIError):
                raise
            message = f"missing {exc.args[0]}" if isinstance(exc, KeyError) else str(exc)
            raise AgentACIError("invalid_request", message) from exc

    @classmethod
    def from_json(cls, raw: bytes | str) -> AgentRequest:
        return cls.from_dict(_json_object(raw, "Agent request"))


Candidate: TypeAlias = (
    HypothesisCandidate
    | HypothesisSetCandidate
    | AdjudicationCandidate
    | ExperimentSpecCandidate
    | RendezvousReply
    | ExpertLoopCandidate
    | DebateStatement
    | DebateJudgment
    | LiteratureQuery
    | ObserverProposal
)


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentResponse:
    schema_version: int
    command: AgentCommand
    command_id: str
    attempt_id: str
    attempt_ordinal: int
    status: AgentStatus
    typed_candidate: Mapping[str, Any] | None
    proposed_workspace_artifacts: tuple[WorkspaceArtifact, ...]
    declared_effects: tuple[DeclaredEffect, ...]
    diagnostics: Mapping[str, Any]
    model_identity: str
    adapter_version: str
    token_usage: Mapping[str, int]
    turn_count: int
    cost: str
    cost_currency: str
    input_digest: str
    output_digest: str
    expert_signal: ExpertSignal | None = None

    def __post_init__(self) -> None:
        if self.schema_version not in _READABLE_ACI_SCHEMA_VERSIONS:
            raise AgentACIError(
                "wrong_schema_version",
                f"expected one of {sorted(_READABLE_ACI_SCHEMA_VERSIONS)!r}",
            )
        if not isinstance(self.command, AgentCommand) or self.command not in _COMMANDS_BY_SCHEMA[self.schema_version]:
            raise AgentACIError("unknown_command", repr(self.command))
        if not isinstance(self.status, AgentStatus):
            raise AgentACIError("invalid_status", repr(self.status))
        for name in ("command_id", "attempt_id", "model_identity", "adapter_version", "cost_currency"):
            _text(getattr(self, name), name)
        if isinstance(self.attempt_ordinal, bool) or not isinstance(self.attempt_ordinal, int):
            raise AgentACIError("invalid_attempt_ordinal", "attempt_ordinal must be an integer")
        if self.attempt_ordinal <= 0:
            raise AgentACIError("invalid_attempt_ordinal", "attempt_ordinal must be positive")
        if not isinstance(self.typed_candidate, (Mapping, type(None))):
            raise AgentACIError("invalid_candidate", "typed_candidate must be an object or null")
        if not isinstance(self.proposed_workspace_artifacts, tuple) or not all(
            isinstance(item, WorkspaceArtifact) for item in self.proposed_workspace_artifacts
        ):
            raise AgentACIError("invalid_workspace_artifact", "workspace artifacts must be a tuple")
        paths = [item.path for item in self.proposed_workspace_artifacts]
        if len(set(paths)) != len(paths):
            raise AgentACIError("duplicate_workspace_artifact", "workspace paths must be unique")
        if not isinstance(self.declared_effects, tuple) or not all(
            isinstance(item, DeclaredEffect) for item in self.declared_effects
        ):
            raise AgentACIError("invalid_effect", "declared_effects must be a tuple")
        if not isinstance(self.diagnostics, Mapping):
            raise AgentACIError("invalid_diagnostics", "diagnostics must be an object")
        if not isinstance(self.token_usage, Mapping):
            raise AgentACIError("invalid_token_usage", "token_usage must be an object")
        for name, value in self.token_usage.items():
            _text(name, "token usage key")
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise AgentACIError("invalid_token_usage", f"{name} must be a nonnegative integer")
        required_usage = {"input_tokens", "output_tokens", "total_tokens"}
        if set(self.token_usage) != required_usage:
            raise AgentACIError("invalid_token_usage", "exact input/output/total token counts are required")
        if self.token_usage["total_tokens"] != self.token_usage["input_tokens"] + self.token_usage["output_tokens"]:
            raise AgentACIError("invalid_token_usage", "total_tokens must equal input plus output")
        if isinstance(self.turn_count, bool) or not isinstance(self.turn_count, int) or self.turn_count < 0:
            raise AgentACIError("invalid_turn_count", "turn_count must be a nonnegative integer")
        if self.status is AgentStatus.SUCCEEDED and self.turn_count == 0:
            raise AgentACIError("invalid_turn_count", "successful response must report at least one turn")
        _canonical_decimal(self.cost, "cost", nonnegative=True)
        _digest_or_empty(self.input_digest, "input_digest")
        _digest_or_empty(self.output_digest, "output_digest")
        if self.expert_signal is not None and not isinstance(self.expert_signal, ExpertSignal):
            raise AgentACIError("invalid_expert_signal", "expert_signal must be typed or null")

    def to_dict(self, *, include_output_digest: bool = True) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema_version": self.schema_version,
            "command": self.command.value,
            "command_id": self.command_id,
            "attempt_id": self.attempt_id,
            "attempt_ordinal": self.attempt_ordinal,
            "status": self.status.value,
            "typed_candidate": _json_value(self.typed_candidate),
            "proposed_workspace_artifacts": [item.to_dict() for item in self.proposed_workspace_artifacts],
            "declared_effects": [item.to_dict() for item in self.declared_effects],
            "diagnostics": _json_value(self.diagnostics),
            "model_identity": self.model_identity,
            "adapter_version": self.adapter_version,
            "token_usage": dict(self.token_usage),
            "turn_count": self.turn_count,
            "cost": self.cost,
            "cost_currency": self.cost_currency,
            "input_digest": self.input_digest,
            "expert_signal": None if self.expert_signal is None else self.expert_signal.to_dict(),
        }
        if include_output_digest:
            value["output_digest"] = self.output_digest
        return value

    def to_json(self) -> bytes:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AgentResponse:
        required = {
            "schema_version",
            "command",
            "command_id",
            "attempt_id",
            "attempt_ordinal",
            "status",
            "typed_candidate",
            "proposed_workspace_artifacts",
            "declared_effects",
            "diagnostics",
            "model_identity",
            "adapter_version",
            "token_usage",
            "turn_count",
            "cost",
            "cost_currency",
            "input_digest",
            "output_digest",
            "expert_signal",
        }
        _strict_keys(value, required, "Agent response", optional={"expert_signal"})
        try:
            artifacts = tuple(
                WorkspaceArtifact.from_dict(_mapping(item, "workspace artifact"))
                for item in _sequence(value["proposed_workspace_artifacts"], "proposed_workspace_artifacts")
            )
            effects = tuple(
                DeclaredEffect.from_dict(_mapping(item, "declared effect"))
                for item in _sequence(value["declared_effects"], "declared_effects")
            )
            candidate = value["typed_candidate"]
            if candidate is not None:
                candidate = _mapping(candidate, "typed_candidate")
            return cls(
                schema_version=value["schema_version"],
                command=_enum(AgentCommand, value["command"], "command"),
                command_id=value["command_id"],
                attempt_id=value["attempt_id"],
                attempt_ordinal=value["attempt_ordinal"],
                status=_enum(AgentStatus, value["status"], "status"),
                typed_candidate=candidate,
                proposed_workspace_artifacts=artifacts,
                declared_effects=effects,
                diagnostics=_mapping(value["diagnostics"], "diagnostics"),
                model_identity=value["model_identity"],
                adapter_version=value["adapter_version"],
                token_usage=_mapping(value["token_usage"], "token_usage"),
                turn_count=value["turn_count"],
                cost=value["cost"],
                cost_currency=value["cost_currency"],
                input_digest=value["input_digest"],
                output_digest=value["output_digest"],
                expert_signal=(None if value.get("expert_signal") is None else
                               ExpertSignal.from_dict(_mapping(value["expert_signal"], "expert_signal"))),
            )
        except (KeyError, ValueError) as exc:
            if isinstance(exc, AgentACIError):
                raise
            raise AgentACIError("invalid_response", f"missing {exc.args[0]}") from exc

    @classmethod
    def from_json(cls, raw: bytes | str) -> AgentResponse:
        return cls.from_dict(_json_object(raw, "Agent response"))


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidatedAgentResponse:
    response: AgentResponse
    candidate: Candidate
    workspace_files: Mapping[str, bytes]


def validate_postflight(
    request: AgentRequest,
    response: AgentResponse | Mapping[str, Any] | bytes | str,
) -> ValidatedAgentResponse:
    """Validate exact invocation binding, metadata, effects and typed candidate."""

    parsed = _coerce_response(response)
    input_bytes = sum(len(item.content_bytes()) for item in request.input_artifacts)
    if input_bytes > request.max_input_bytes:
        raise AgentACIError("input_too_large", f"embedded input package is {input_bytes} bytes")
    if parsed.schema_version != request.schema_version:
        raise AgentACIError("wrong_schema_version", "response schema does not match request")
    for name in ("command", "command_id", "attempt_id", "attempt_ordinal"):
        if getattr(parsed, name) != getattr(request, name):
            raise AgentACIError("wrong_invocation_identity", f"response {name} does not match request")
    if parsed.input_digest != request_digest(request):
        raise AgentACIError("input_digest_mismatch", "response does not bind the canonical request")
    if parsed.output_digest != response_digest(parsed):
        raise AgentACIError("output_digest_mismatch", "response digest does not bind returned bytes")
    if len(parsed.to_json()) > request.budget.max_output_bytes:
        raise AgentACIError("output_too_large", f"response envelope is {len(parsed.to_json())} bytes")
    if parsed.model_identity != request.required_model_identity:
        raise AgentACIError("model_identity_mismatch", f"{parsed.model_identity!r} != {request.required_model_identity!r}")
    if parsed.adapter_version != request.required_adapter_version:
        raise AgentACIError("adapter_version_mismatch", f"{parsed.adapter_version!r} != {request.required_adapter_version!r}")
    if parsed.cost_currency != "USD":
        raise AgentACIError("invalid_cost_currency", "Agent cost must use USD")
    if parsed.turn_count > request.budget.max_turns:
        raise AgentACIError("turn_budget_exceeded", f"{parsed.turn_count} > {request.budget.max_turns}")
    if request.budget.max_cost is not None and Decimal(parsed.cost) > Decimal(request.budget.max_cost):
        raise AgentACIError("cost_budget_exceeded", f"{parsed.cost} > {request.budget.max_cost}")
    if (
        request.budget.token_enforcement_mode == "enforce"
        and parsed.token_usage["total_tokens"] > request.budget.max_tokens
    ):
        raise AgentACIError(
            "token_budget_exceeded",
            f"{parsed.token_usage['total_tokens']} > {request.budget.max_tokens}",
        )
    if parsed.status is not AgentStatus.SUCCEEDED:
        raise AgentACIError("agent_attempt_failed", parsed.status.value)
    if parsed.typed_candidate is None:
        raise AgentACIError("missing_candidate", "successful response has no typed candidate")
    expected_effects = set(request.effect_requirements)
    actual_effects: set[str] = set()
    for effect in parsed.declared_effects:
        if effect.command_id != request.command_id or effect.attempt_id != request.attempt_id:
            raise AgentACIError("wrong_effect_identity", effect.effect)
        if effect.effect in actual_effects:
            raise AgentACIError("duplicate_effect", effect.effect)
        actual_effects.add(effect.effect)
    if actual_effects != expected_effects:
        raise AgentACIError(
            "mandatory_effect_mismatch",
            f"expected {sorted(expected_effects)!r}, got {sorted(actual_effects)!r}",
        )
    candidate = _candidate(request, parsed.typed_candidate)
    if isinstance(candidate, ExperimentSpecCandidate): _validate_evaluation_scope(request, candidate)
    if getattr(candidate, "measurement", None) is not None and request.schema_version < 12:
        raise AgentACIError("wrong_schema_version", "expert measurement requires ACI12")
    synthesis = _is_hypothesis_synthesis(request)
    query_plan = _is_literature_query_plan(request.task_context)
    literature_main = (
        request.schema_version >= 8
        and _is_literature_main(request.task_context)
    )
    if request.command is AgentCommand.PROPOSE_HYPOTHESIS or synthesis:
        hypotheses = tuple(item.candidate for item in candidate.hypotheses) if isinstance(candidate, HypothesisSetCandidate) else (candidate,)
        for hypothesis in hypotheses:
            _validate_hypothesis_owner(request, hypothesis, synthesis=synthesis)
    elif request.command in {
        AgentCommand.DRAFT_EXPERIMENT_SPEC,
        AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
    }:
        assert isinstance(candidate, ExperimentSpecCandidate)
        if candidate.component_id != request.component_id:
            raise AgentACIError(
                "component_id_mismatch",
                f"{candidate.component_id!r} != {request.component_id!r}",
            )
        if candidate.owner_role != request.role:
            raise AgentACIError(
                "owner_role_mismatch",
                f"{candidate.owner_role!r} != {request.role!r}",
            )
        if candidate.method_family_id != request.method_family_id:
            raise AgentACIError(
                "method_family_id_mismatch",
                f"{candidate.method_family_id!r} != {request.method_family_id!r}",
            )
    if parsed.expert_signal is not None:
        if request.command is not AgentCommand.PROPOSE_HYPOTHESIS:
            raise AgentACIError("unexpected_expert_signal", request.command.value)
        if not set(parsed.expert_signal.context_refs) <= set(request.immutable_context_refs):
            raise AgentACIError("expert_signal_context_mismatch", "signal cites context outside the request")
    if request.command is AgentCommand.ANSWER_RENDEZVOUS:
        rendezvous = _rendezvous_input(request)
        if (candidate.rendezvous_id != rendezvous.rendezvous_id or candidate.kind is not rendezvous.kind
                or candidate.responder_role != request.role or request.role != rendezvous.target_role
                or candidate.disposition not in {RendezvousDisposition.ANSWERED, RendezvousDisposition.DECLINED}
                or not set(candidate.supporting_refs) <= set(rendezvous.context_refs)):
            raise AgentACIError("rendezvous_reply_mismatch", "reply differs from the Host-built request")
    if query_plan:
        assert isinstance(candidate, LiteratureQuery)
        if not set(candidate.immutable_context_refs) <= set(
            request.immutable_context_refs
        ):
            raise AgentACIError(
                "task_candidate_mismatch",
                "query plan cites context outside the request",
            )
    elif request.command is AgentCommand.ADJUDICATE:
        assert isinstance(candidate, AdjudicationCandidate)
        refs = {ref for verdict in candidate.verdicts for ref in (*verdict["evidence"], verdict["node_id"])}
        if not refs <= adjudication_reference_ids(request):
            raise AgentACIError("invalid_candidate", "adjudication cites outside the admitted input domain")
    elif request.command is AgentCommand.OBSERVER and request.schema_version >= 14:
        assert isinstance(candidate, ObserverProposal)
    elif request.command in {AgentCommand.DEBATE, AgentCommand.DEBATE_JUDGE} and request.schema_version >= 14:
        assert isinstance(candidate, (DebateStatement, DebateJudgment))
    elif (
        request.command in EXPERT_LOOP_AGENT_COMMANDS
        and request.command is not AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT
        and not synthesis
    ):
        assert isinstance(candidate, ExpertLoopCandidate)
        assert request.task_context is not None
        expected_kind = (
            request.task_context.key.node_kind.value
            if isinstance(request.task_context, ResearchNodeTask)
            else request.task_context.kind.value
        )
        if candidate.task_kind != expected_kind:
            raise AgentACIError("task_candidate_mismatch", "candidate task kind differs from the request")
        allowed_refs = (
            {
                item.ref
                for item in request.input_artifacts
                if item.ref.endswith("--literature-packet")
            }
            if literature_main
            else set(request.immutable_context_refs)
        )
        if not set(candidate.supporting_refs) <= allowed_refs:
            raise AgentACIError(
                "invalid_candidate",
                (
                    "literature MAIN cites context outside its validated packet"
                    if literature_main
                    else "candidate cites context outside the request"
                ),
            )
        update = request.command is AgentCommand.HYPOTHESIS_UPDATE
        if update != (candidate.update_disposition is not None):
            raise AgentACIError("task_candidate_mismatch", "only hypothesis update has a disposition")
        iteration = request.command is AgentCommand.RESULT_ANALYSIS and request.schema_version >= MULTICHAIN_ACI_SCHEMA_VERSION
        if iteration != (candidate.iteration_disposition is not None):
            raise AgentACIError("task_candidate_mismatch", "only ACI14 result analysis requires iteration_disposition")
        if candidate.iteration_disposition is IterationDisposition.ITERATE:
            _validate_iteration_input(request, candidate)
        if (
            update
            and not grounding_acceptance_request(request)
            and hypothesis_update_current_assessment(request)
            is HypothesisAssessment.SUPPORTED
            and candidate.update_disposition
            is not HypothesisUpdateDisposition.TERMINATE_LINEAGE
        ):
            raise AgentACIError(
                "invalid_candidate",
                "a SUPPORTED current component tip must TERMINATE_LINEAGE",
            )
    workspace = {item.path: item.content_bytes() for item in parsed.proposed_workspace_artifacts}
    if isinstance(candidate, GroundingCandidate):
        required = grounding_acceptance_request(request) and candidate.clarification is None
        if (candidate.acceptance is not None) != required:
            raise AgentACIError("invalid_candidate", "only completed commissioned Grounding defines common acceptance")
        if required:
            from .shared_acceptance import acceptance_source_files
            try:
                selected = acceptance_source_files(candidate.acceptance, workspace)
                if set(selected) != set(workspace): raise ValueError("Grounding may publish only the declared evaluator source files")
            except ValueError as exc: raise AgentACIError("invalid_workspace", str(exc)) from exc
        elif workspace:
            raise AgentACIError("unexpected_workspace", "a clarification or legacy background cannot publish an evaluator")
    if request.command is AgentCommand.FINAL_REPORT:
        from .final_report import validate_final_report
        try: validate_final_report(workspace, request)
        except ValueError as exc: raise AgentACIError("invalid_workspace", str(exc)) from exc
    if request.command is AgentCommand.CYCLE_REPORT:
        from .expert_memory import validate_report, request_report_component, report_path
        try:
            supplied = json.loads(next(item for item in request.input_artifacts if item.ref.endswith("--cycle-assessments")).content_bytes())
            component = request_report_component(request)
            expected = report_path(request.task_context.key.cycle, component)
            if supplied.get("report_path", expected) != expected: raise ValueError("report path differs from its owning component")
            validate_report(workspace, cycle=request.task_context.key.cycle,
                component_id=component,
                facts=supplied["assessments"], max_bytes=request.budget.max_output_bytes)
        except (ValueError, StopIteration, KeyError) as exc:
            raise AgentACIError("invalid_candidate", str(exc)) from exc
    total = sum(len(content) for content in workspace.values())
    if total > request.budget.max_output_bytes:
        raise AgentACIError("output_too_large", f"workspace package is {total} bytes")
    if request.command is AgentCommand.PROPOSE_HYPOTHESIS and workspace:
        raise AgentACIError("unexpected_workspace", "hypothesis command may not propose workspace files")
    if request.command is AgentCommand.DRAFT_EXPERIMENT_SPEC and not workspace:
        raise AgentACIError("missing_workspace", "experiment spec command requires candidate workspace files")
    if request.command is AgentCommand.ANSWER_RENDEZVOUS and workspace:
        raise AgentACIError("unexpected_workspace", "rendezvous answer may not propose workspace files")
    if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT and (getattr(candidate, "measurement", None) is not None or candidate.evaluation_scope == "GOAL"):
        if "implementation.patch" not in workspace:
            raise AgentACIError("invalid_workspace", "measurement design requires its cumulative implementation.patch")
        try:
            if candidate.measurement is not None: measurement_parser_files(candidate.measurement, workspace)
        except ValueError as exc: raise AgentACIError("invalid_workspace", str(exc)) from exc
    elif request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT and set(workspace) != {
        "experiment.py",
        "implementation.patch",
    }:
        raise AgentACIError(
            "invalid_workspace",
            "design/implementation requires exactly experiment.py and implementation.patch",
        )
    if (
        request.command in EXPERT_LOOP_AGENT_COMMANDS
        and request.command not in {AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, AgentCommand.CYCLE_REPORT, AgentCommand.FINAL_REPORT}
        and not (request.command is AgentCommand.GROUNDING and grounding_acceptance_request(request))
        and workspace
    ):
        raise AgentACIError("unexpected_workspace", "Stage-1 expert-loop commands may not propose workspace files")
    return ValidatedAgentResponse(response=parsed, candidate=candidate, workspace_files=workspace)


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidatedSubmission:
    """Admitted tool source; deliberately not a terminal Agent response."""

    candidate: ExperimentSpecCandidate
    workspace_files: Mapping[str, bytes]
    output_digest: str


def execution_handoff(observation: Mapping[str, Any]) -> str | None:
    """Read a handoff from the existing typed response, never a node-state mirror."""
    request = observation.get("request", {})
    if request.get("schema_version", 0) < EXECUTION_HANDOFF_ACI_SCHEMA_VERSION or request.get("command") != "EXPERIMENT_EXECUTION":
        return None
    return observation.get("response", {}).get("typed_candidate", {}).get("waiting_for_experiment_id")


def validate_execution_completion(latest_state: str | None, *, waiting_for: str | None = None,
                                  latest_experiment_id: str | None = None, can_handoff: bool = False) -> None:
    """Share terminal completion and one-time handoff validation with native Stop."""
    if waiting_for is not None:
        if waiting_for != latest_experiment_id or not can_handoff:
            raise AgentACIError("invalid_candidate", "handoff requires the latest owned experiment without a prior accepted handoff")
        return
    if latest_state != "TERMINAL":
        guidance = ("Submit an experiment first." if latest_state is None else
            f"Latest state is {latest_state}; use wait_experiment until terminal. "
            "A queued, running or blocked experiment is not a completed execution.")
        raise AgentACIError("invalid_candidate",
            "execution completion requires its terminal submitted experiment. " + guidance)


def validate_experiment_submission(
    request: AgentRequest, arguments: Mapping[str, Any]
) -> tuple[ExperimentSpecCandidate, dict[str, bytes], dict[str, Any]]:
    """Validate a submission source; Host separately owns admission/idempotency.

    The existing execution artifact digest now binds this typed source snapshot
    when the creating execution Agent is still OPEN. No Agent status or model
    accounting is invented for this intermediate tool operation.
    """
    if not supports_experiment_submission(request.schema_version) or request.command is not AgentCommand.EXPERIMENT_EXECUTION:
        raise AgentACIError("unauthorized_tool", "submit requires the current execution Agent")
    required = {"candidate", "workspace_files"}
    optional = {"command", "gpus", "timeout_min", "description", "replaces_experiment_id"}
    if request.schema_version >= RESOURCE_LEARNING_ACI_SCHEMA_VERSION:
        optional.add("resource_estimate")
    _strict_keys(arguments, required | optional, "experiment submission", optional=optional)
    resource_estimate = None
    if "resource_estimate" in arguments:
        try:
            resource_estimate = ResourceEstimate.from_dict(arguments["resource_estimate"])
        except (TypeError, ValueError) as exc:
            raise AgentACIError("invalid_resource_estimate", str(exc)) from exc
    candidate = _experiment_candidate(_mapping(arguments["candidate"], "candidate"))
    _validate_evaluation_scope(request, candidate)
    if candidate.measurement is not None and request.schema_version < 12:
        raise AgentACIError("wrong_schema_version", "expert measurement requires ACI12")
    if (candidate.component_id, candidate.owner_role, candidate.method_family_id) != (
        request.component_id, request.role, request.method_family_id
    ):
        raise AgentACIError("submission_owner_mismatch", "candidate differs from invocation ownership")
    artifacts = tuple(
        WorkspaceArtifact.from_dict(_mapping(item, "workspace file"))
        for item in _sequence(arguments["workspace_files"], "workspace_files")
    )
    files = {item.path: item.content_bytes() for item in artifacts}
    if candidate.measurement is not None or candidate.evaluation_scope == "GOAL":
        if len(files) != len(artifacts) or "implementation.patch" not in files:
            raise AgentACIError("invalid_workspace", "measurement submission requires unique sources and implementation.patch")
        try:
            if candidate.measurement is not None: measurement_parser_files(candidate.measurement, files)
        except ValueError as exc: raise AgentACIError("invalid_workspace", str(exc)) from exc
        if "command" in arguments and (not isinstance(arguments["command"], list) or tuple(arguments["command"]) != candidate.execution_argv):
            raise AgentACIError("invalid_command", "measurement submit command must equal its explicit candidate execution_argv")
    elif len(files) != len(artifacts) or set(files) != {"experiment.py", "implementation.patch"}:
        raise AgentACIError("invalid_workspace", "submission requires unique experiment.py and implementation.patch")
    if candidate.measurement is None and candidate.evaluation_scope != "GOAL" and not files["experiment.py"]:
        raise AgentACIError("missing_workspace", "experiment.py must not be empty")
    for name in ("description", "replaces_experiment_id"):
        if name in arguments and arguments[name] is not None:
            _text(arguments[name], name)
    if "gpus" in arguments:
        count = arguments["gpus"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0 or (request.schema_version < 14 and count > 1):
            raise AgentACIError("invalid_resource_request", "gpus must be a nonnegative count; retained ACI permits at most one")
        if count == 0 and candidate.resource_preference is ResourcePreference.GPU_REQUIRED:
            raise AgentACIError("invalid_resource_request", "GPU_REQUIRED conflicts with zero GPUs")
        if count > 0 and candidate.resource_preference is ResourcePreference.CPU:
            raise AgentACIError("invalid_resource_request", "CPU candidate cannot request a GPU")
    if "timeout_min" in arguments:
        value = arguments["timeout_min"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value < float("inf"):
            raise AgentACIError("invalid_budget", "timeout_min must be finite and positive")
    if "command" in arguments:
        value = arguments["command"]
        if not isinstance(value, (str, list)) or not value:
            raise AgentACIError("invalid_command", "command must be text or an argv list")
        if isinstance(value, list) and any(not isinstance(item, str) or not item or "\x00" in item for item in value):
            raise AgentACIError("invalid_command", "command argv contains an invalid item")
        if isinstance(value, str) and "\x00" in value:
            raise AgentACIError("invalid_command", "command contains NUL")
    normalized = json.loads(canonical_json(arguments))
    if resource_estimate is not None:
        normalized["resource_estimate"] = resource_estimate.to_dict()
    normalized["workspace_files"] = [item.to_dict() for item in sorted(artifacts, key=lambda item: item.path)]
    snapshot: dict[str, Any] = {
        "kind": "admitted-submit/1", "command_id": request.command_id,
        "attempt_id": request.attempt_id, "input_digest": request_digest(request),
        "arguments": normalized,
    }
    if sum(map(len, files.values())) > request.budget.max_output_bytes or len(canonical_json(snapshot)) > request.budget.max_output_bytes:
        raise AgentACIError("output_too_large", "submitted execution package exceeds the invocation output budget")
    snapshot["output_digest"] = hashlib.sha256(canonical_json(snapshot)).hexdigest()
    return candidate, files, snapshot


def validate_submission_snapshot(request: AgentRequest, snapshot: Mapping[str, Any]) -> ValidatedSubmission:
    """Revalidate the exact committed tool source at materialization/read time."""
    _strict_keys(snapshot, {"kind", "command_id", "attempt_id", "input_digest", "arguments", "output_digest"}, "submit source")
    candidate, files, expected = validate_experiment_submission(request, _mapping(snapshot["arguments"], "arguments"))
    if canonical_json(snapshot) != canonical_json(expected):
        raise AgentACIError("submission_source_mismatch", "source bytes or invocation binding changed")
    return ValidatedSubmission(candidate=candidate, workspace_files=files, output_digest=expected["output_digest"])


def request_digest(request: AgentRequest) -> str:
    return hashlib.sha256(request.to_json()).hexdigest()


def grounding_acceptance_request(request: AgentRequest) -> bool:
    """Read the explicit commission mode from existing frozen request inputs."""
    if request.schema_version < SHARED_ACCEPTANCE_ACI_SCHEMA_VERSION: return False
    configs = [item for item in request.input_artifacts if item.ref.endswith(("--grounding-config", "--expert-config"))]
    if not configs: return False
    if len(configs) != 1: raise AgentACIError("invalid_schema", "acceptance requires one node configuration")
    value = _json_object(configs[0].content_bytes(), "node configuration")
    return value.get("goal", {}).get("evaluation") == {"mode": "grounding"}


def _validate_evaluation_scope(request: AgentRequest, candidate: ExperimentSpecCandidate) -> None:
    if grounding_acceptance_request(request):
        if candidate.evaluation_scope not in {"LOCAL", "GOAL"}:
            raise AgentACIError("invalid_candidate", "commissioned experiments must select LOCAL or GOAL evaluation")
    elif candidate.evaluation_scope is not None:
        raise AgentACIError("invalid_candidate", "legacy experiments cannot select common evaluation")


def response_digest(response: AgentResponse) -> str:
    return hashlib.sha256(canonical_json(response.to_dict(include_output_digest=False))).hexdigest()


def with_output_digest(response: AgentResponse) -> AgentResponse:
    """Return an otherwise-identical response with its canonical digest filled."""

    values = response.to_dict(include_output_digest=False)
    values["output_digest"] = hashlib.sha256(canonical_json(values)).hexdigest()
    return AgentResponse.from_dict(values)


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            _json_value(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AgentACIError("noncanonical_json", str(exc)) from exc


def _validate_hypothesis_owner(request: AgentRequest, candidate: HypothesisCandidate, *, synthesis: bool) -> None:
    if candidate.component_id != request.component_id:
        raise AgentACIError("component_id_mismatch", f"{candidate.component_id!r} != {request.component_id!r}")
    if candidate.proposer_role != request.role:
        raise AgentACIError("proposer_role_mismatch", f"{candidate.proposer_role!r} != {request.role!r}")
    if candidate.measurement is not None and request.schema_version < 12:
        raise AgentACIError("wrong_schema_version", "expert measurement requires ACI12")
    if synthesis:
        parents = _synthesis_parent_hypothesis_ids(request)
        if candidate.parent_hypothesis_id not in parents or (candidate.relation is None) != (parents == (None,)):
            raise AgentACIError("hypothesis_lineage_mismatch", "synthesis must match its exact bound hypothesis lineage")


def _validate_iteration_input(request: AgentRequest, candidate: ExpertLoopCandidate) -> None:
    """Validate the existing exact analysis inputs, not candidate-owned facts."""
    facts = {}
    for name, kind in (("spec", "ExperimentPreregistered"), ("outcome", "CanonicalOutcomeAccepted")):
        ref = f"{request.command_id}--current-{name}"
        items = [item for item in request.input_artifacts if item.ref == ref and item.media_type == "application/json"]
        if len(items) != 1 or ref not in request.immutable_context_refs:
            raise AgentACIError("invalid_iteration_input", "ITERATE requires its exact current accepted experiment inputs")
        try:
            row = _json_object(items[0].content_bytes(), "current analysis fact")
            payload = _mapping(row.get("payload"), "current analysis payload")
        except AgentACIError as exc:
            raise AgentACIError("invalid_iteration_input", "current analysis fact is malformed") from exc
        if row.get("fact_type") != kind or payload.get("fact_type") != kind or row.get("fact_id") != payload.get("fact_id"):
            raise AgentACIError("invalid_iteration_input", "current analysis fact identity differs")
        for field in ("run_id", "hypothesis_id", "experiment_id", "execution_id", "outcome_id"):
            if field in row and row[field] != payload.get(field):
                raise AgentACIError("invalid_iteration_input", "current analysis fact index differs")
        if payload.get("run_id") != request.run_id or payload.get("component_id") != request.component_id:
            raise AgentACIError("invalid_iteration_input", "current analysis fact belongs to another participant")
        facts[name] = payload
    if any(facts["spec"].get(field) != facts["outcome"].get(field) for field in ("experiment_id", "execution_id", "method_family_id", "result_kind")):
        raise AgentACIError("invalid_iteration_input", "current analysis outcome differs from its preregistration")
    if facts["outcome"].get("outcome") not in {"SUCCESS", "CLEAN_REFUTATION"}:
        raise AgentACIError("invalid_iteration_candidate", "ITERATE requires an accepted scientific comparison, not operational repair")
    if f"{request.command_id}--current-outcome" not in candidate.supporting_refs:
        raise AgentACIError("invalid_iteration_candidate", "ITERATE must cite its current accepted outcome")


def adjudication_reference_ids(request: AgentRequest) -> frozenset[str]:
    """The existing admitted domain shared by postflight and its rendered schema."""
    allowed = set(request.immutable_context_refs)
    for kind, fields in (("HypothesisProposed", ("hypothesis_id",)),
                         ("CanonicalOutcomeAccepted", ("outcome_id", "experiment_id", "artifact_id"))):
        for payload in _embedded_fact_payloads(request, kind):
            if payload.get("component_id") != request.component_id or (kind == "HypothesisProposed" and payload.get("proposer_role") != request.role):
                continue
            allowed.update(payload[field] for field in fields if isinstance(payload.get(field), str))
    return frozenset(allowed)


def debate_reference_ids(request: AgentRequest) -> frozenset[str]:
    from .expert_interaction import InteractionFacts
    sources = [item for item in request.input_artifacts if item.ref.endswith("--interaction-context")]
    contexts = [item for item in request.input_artifacts if item.ref.endswith("--debate-context")]
    if len(sources) != 1 or len(contexts) != 1: raise ValueError("Debate requires exact frozen scientific and round contexts")
    facts = InteractionFacts.from_rows(request.run_id, json.loads(sources[0].content_bytes())["facts"])
    context = json.loads(contexts[0].content_bytes())
    refs = {context["original_attempt_id"]}
    if context["previous_opponent_attempt_id"] is not None: refs.add(context["previous_opponent_attempt_id"])
    if not refs <= set(request.immutable_context_refs).intersection(item.ref for item in request.input_artifacts):
        raise ValueError("Debate must receive the actual original and prior opponent records")
    return facts.reference_ids | refs


def debate_judge_inputs(request: AgentRequest):
    from .expert_interaction import InteractionFacts, DebateBinding, DebateFork, DebateParticipant, read_judge_rounds
    from .participant_controls import ParticipantScope
    inputs = [item for item in request.input_artifacts if item.ref.endswith("--debate-judge-context")]
    if len(inputs) != 1: raise ValueError("Debate judge requires one frozen six-output context")
    context = json.loads(inputs[0].content_bytes())
    facts = InteractionFacts.from_rows(request.run_id, context["facts"], agent_aci_version=request.schema_version)
    participants = []
    for item in context["participants"]:
        participant = DebateParticipant(ParticipantScope.from_dict(item["participant"]["scope"]), item["participant"]["hypothesis_id"])
        facts.hypothesis(participant.hypothesis_id, participant.scope)
        participants.append(DebateFork(participant, item["original_attempt_id"], item["original_session_id"], item["session_id"]))
    binding = DebateBinding(context["debate_id"], request.run_id, tuple(participants))
    return facts, binding, read_judge_rounds(context, binding=binding, facts=facts)


def validate_observer_candidate(request: AgentRequest, raw: Mapping[str, Any]) -> ObserverProposal:
    """Use the frozen Observer context for both native correction and postflight."""
    from .expert_interaction import InteractionFacts, admit_observer_proposal, expand_fact_references
    inputs = [item for item in request.input_artifacts if item.ref.endswith("--interaction-context")]
    try:
        if len(inputs) != 1: raise ValueError("Observer requires one frozen interaction context")
        context = json.loads(inputs[0].content_bytes())
        actions = context["allowed_actions"]
        if (not isinstance(actions, list) or not actions or len(actions) != len(set(actions))
                or not set(actions) <= {"none", "leader", "debate"}): raise ValueError("invalid Observer allowed actions")
        if raw.get("action") not in actions: raise ValueError("Observer action is disabled for this trigger")
        facts = InteractionFacts.from_rows(request.run_id, context["facts"], agent_aci_version=request.schema_version)
        proposal = admit_observer_proposal(raw, facts=facts)
        cited = expand_fact_references(facts.rows, proposal.supporting_refs)
        if "eligible_leader_components" in context:
            eligible = context["eligible_leader_components"]
            if not isinstance(eligible, list) or any(not isinstance(item, str) for item in eligible):
                raise ValueError("invalid eligible Leader components")
            if proposal.leader is not None and any(item.component_id not in eligible
                    for item in proposal.leader.contributions if cited.intersection(item.qualification_refs)):
                raise ValueError("selected Leader component is not eligible at this trigger")
        if request.schema_version >= 21 and proposal.leader is not None:
            if proposal.leader.contributions[0].hypothesis_id not in context["eligible_leader_hypotheses"]:
                raise ValueError("Leader contribution is not eligible at this trigger")
        if "debate_participants" in context:
            participants = context["debate_participants"]
            if not isinstance(participants, list): raise ValueError("invalid available Debate fork sources")
            if proposal.debate is not None and any(asdict(item) not in participants for item in proposal.debate.participants):
                raise ValueError("Debate participant has no exact available fork source")
        return proposal
    except (KeyError, TypeError, ValueError) as exc: raise AgentACIError("invalid_candidate", str(exc)) from exc


def _candidate(request: AgentRequest, raw: Mapping[str, Any]) -> Candidate:
    command = request.command
    synthesis = _is_hypothesis_synthesis(request)
    if synthesis and request.schema_version >= MULTICHAIN_ACI_SCHEMA_VERSION:
        try:
            members = raw.get("hypotheses")
            if not isinstance(members, list):
                raise ValueError("hypotheses must be an array")
            # Host checks this shape against its accepted effective_max_forks.
            batch = admit_hypothesis_set(raw, effective_max_forks=max(1, len(members)), validate_candidate=_hypothesis_candidate)
            for item in batch.hypotheses:
                if not {"parent_hypothesis_id", "relation"} <= set(item["candidate"]):
                    raise ValueError("every synthesis member must explicitly declare its hypothesis lineage")
            return HypothesisSetCandidate(hypotheses=tuple(HypothesisSetMember(chain_id=item["chain_id"],
                candidate=_hypothesis_candidate(item["candidate"])) for item in batch.hypotheses))
        except ValueError as exc:
            raise AgentACIError("invalid_candidate", str(exc)) from exc
    if command is AgentCommand.ADJUDICATE:
        try:
            return admit_adjudication_candidate(raw)
        except ValueError as exc:
            raise AgentACIError("invalid_candidate", str(exc)) from exc
    if command is AgentCommand.DEBATE and request.schema_version >= 14:
        from .expert_interaction import admit_debate_statement
        try: return admit_debate_statement(raw, reference_ids=debate_reference_ids(request))
        except ValueError as exc: raise AgentACIError("invalid_candidate", str(exc)) from exc
    if command is AgentCommand.DEBATE_JUDGE:
        from .expert_interaction import admit_debate_judgment
        try:
            facts, binding, rounds = debate_judge_inputs(request)
            return admit_debate_judgment(raw, binding=binding, rounds=rounds, facts=facts, source_attempt_id=request.attempt_id)
        except (KeyError, TypeError, ValueError) as exc: raise AgentACIError("invalid_candidate", str(exc)) from exc
    if command is AgentCommand.OBSERVER and request.schema_version >= 14:
        return validate_observer_candidate(request, raw)
    if synthesis and not {"parent_hypothesis_id", "relation"} <= set(raw):
        raise AgentACIError(
            "invalid_schema",
            "cycle-one synthesis must explicitly declare null hypothesis lineage",
        )
    if command is AgentCommand.PROPOSE_HYPOTHESIS or synthesis:
        return _hypothesis_candidate(raw)
    if command is AgentCommand.ANSWER_RENDEZVOUS:
        try:
            return RendezvousReply.from_dict(raw)
        except (TypeError, ValueError) as exc:
            raise AgentACIError("invalid_candidate", str(exc)) from exc
    if _is_literature_query_plan(request.task_context):
        try:
            candidate = LiteratureQuery.from_dict(raw)
        except (TypeError, ValueError) as exc:
            raise AgentACIError("invalid_candidate", str(exc)) from exc
        expected_version = (
            1 if request.schema_version == 8
            else 2 if request.schema_version >= 9
            else None
        )
        if candidate.schema_version != expected_version:
            raise AgentACIError(
                "invalid_candidate",
                f"ACI v{request.schema_version} requires LiteratureQuery/v{expected_version}",
            )
        return candidate
    if command is AgentCommand.GROUNDING and supports_grounding_web(request.schema_version):
        dialogue = request.schema_version >= GROUNDING_DIALOGUE_ACI_SCHEMA_VERSION
        _strict_keys(raw, {"task_kind", "supporting_refs", "background", "web_sources"} | ({"clarification"} if dialogue else set())
                     | ({"acceptance"} if request.schema_version >= SHARED_ACCEPTANCE_ACI_SCHEMA_VERSION else set()), "Grounding candidate")
        try:
            from .shared_acceptance import SharedAcceptance
            clarification = raw.get("clarification")
            background = None if dialogue and raw["background"] is None else GroundingBackground.from_dict(raw["background"], max_bytes=request.budget.max_output_bytes)
            return GroundingCandidate(task_kind=raw["task_kind"], summary=background.project_summary if background is not None else clarification["question"],
                supporting_refs=_tuple(raw["supporting_refs"], "supporting_refs"), background=background,
                web_sources=_tuple(raw["web_sources"], "web_sources"), clarification=clarification,
                acceptance=None if raw.get("acceptance") is None else SharedAcceptance.from_dict(raw["acceptance"]))
        except (KeyError, TypeError, ValueError) as exc: raise AgentACIError("invalid_candidate", str(exc)) from exc
    if command is AgentCommand.GRAPH_EVOLVE:
        _strict_keys(raw, {"task_kind", "summary", "supporting_refs", "graph_evolution"}, "Graph evolution candidate")
        from .manager_context import admit_graph_evolution
        try:
            return GraphEvolutionCandidate(task_kind=raw["task_kind"], summary=raw["summary"],
                supporting_refs=_tuple(raw["supporting_refs"], "supporting_refs"),
                graph_evolution=admit_graph_evolution(raw["graph_evolution"], request))
        except (KeyError, TypeError, ValueError) as exc: raise AgentACIError("invalid_candidate", str(exc)) from exc
    if (
        command in EXPERT_LOOP_AGENT_COMMANDS
        and command is not AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT
    ):
        allowed = {"task_kind", "summary", "supporting_refs", "update_disposition"}
        optional = {"update_disposition"}
        iteration = command is AgentCommand.RESULT_ANALYSIS and request.schema_version >= MULTICHAIN_ACI_SCHEMA_VERSION
        web_summary = command is AgentCommand.LITERATURE_REVIEW and _is_literature_main(request.task_context) and supports_literature_web(request.schema_version)
        handoff = command is AgentCommand.EXPERIMENT_EXECUTION and request.schema_version >= EXECUTION_HANDOFF_ACI_SCHEMA_VERSION
        if handoff:
            allowed.add("waiting_for_experiment_id")
            optional.add("waiting_for_experiment_id")
            if "waiting_for_experiment_id" in raw:
                _text(raw["waiting_for_experiment_id"], "waiting_for_experiment_id")
        if iteration:
            allowed.add("iteration_disposition")
        if web_summary:
            allowed.add("web_sources")
            optional.add("web_sources")
        _strict_keys(raw, allowed, "Expert-loop candidate", optional=optional)
        try:
            disposition = raw.get("update_disposition")
            candidate_type = LiteratureSummaryCandidate if web_summary else ExpertLoopCandidate
            return candidate_type(
                task_kind=raw["task_kind"], summary=raw["summary"],
                supporting_refs=_tuple(raw["supporting_refs"], "supporting_refs"),
                update_disposition=(None if disposition is None else
                                    HypothesisUpdateDisposition(disposition)),
                iteration_disposition=IterationDisposition(raw["iteration_disposition"]) if iteration else None,
                waiting_for_experiment_id=raw.get("waiting_for_experiment_id") if handoff else None,
                **({"web_sources": _tuple(raw.get("web_sources", ()), "web_sources")} if web_summary else {}),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AgentACIError("invalid_candidate", str(exc)) from exc
    return _experiment_candidate(raw)


def _experiment_candidate(raw: Mapping[str, Any]) -> ExperimentSpecCandidate:
    allowed = {
        "component_id",
        "owner_role",
        "method_family_id",
        "method",
        "metrics",
        "acceptance_criteria",
        "evaluator_version",
        "artifact_requirements",
        "resource_preference",
        "measurement", "execution_argv", "evaluation_scope",
    }
    _strict_keys(raw, allowed, "ExperimentSpec candidate", optional={"measurement", "execution_argv", "evaluation_scope"})
    try:
        metrics = tuple(
            MetricSpec(
                name=_mapping(metric, "metric")["name"],
                direction=_enum(MetricDirection, _mapping(metric, "metric")["direction"], "direction"),
                target=_mapping(metric, "metric")["target"],
            )
            for metric in _sequence(raw["metrics"], "metrics")
        )
        return ExperimentSpecCandidate(
            component_id=raw["component_id"],
            owner_role=raw["owner_role"],
            method_family_id=raw["method_family_id"],
            method=raw["method"],
            metrics=metrics,
            acceptance_criteria=_tuple(raw["acceptance_criteria"], "acceptance_criteria"),
            evaluator_version=raw["evaluator_version"],
            artifact_requirements=_tuple(raw["artifact_requirements"], "artifact_requirements"),
            resource_preference=_enum(ResourcePreference, raw["resource_preference"], "resource_preference"),
            measurement=None if raw.get("measurement") is None else ScientificMeasurement.from_dict(raw["measurement"]),
            execution_argv=None if raw.get("execution_argv") is None else _tuple(raw["execution_argv"], "execution_argv"),
            evaluation_scope=raw.get("evaluation_scope"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AgentACIError("invalid_candidate", str(exc)) from exc


def _hypothesis_candidate(raw: Mapping[str, Any]) -> HypothesisCandidate:
    allowed = {
        "component_id",
        "proposer_role",
        "method_family_id",
        "claim",
        "assumptions",
        "parent_hypothesis_id",
        "relation",
        "measurement",
    }
    _strict_keys(
        raw,
        allowed,
        "Hypothesis candidate",
        optional={"parent_hypothesis_id", "relation", "measurement"},
    )
    try:
        return HypothesisCandidate(
            component_id=raw["component_id"],
            proposer_role=raw["proposer_role"],
            method_family_id=raw["method_family_id"],
            claim=raw["claim"],
            assumptions=_tuple(raw["assumptions"], "assumptions"),
            parent_hypothesis_id=raw.get("parent_hypothesis_id"),
            relation=raw.get("relation"),
            measurement=None if raw.get("measurement") is None else ScientificMeasurement.from_dict(raw["measurement"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AgentACIError("invalid_candidate", str(exc)) from exc


def _is_hypothesis_synthesis(request: AgentRequest) -> bool:
    return (
        request.command is AgentCommand.HYPOTHESIS_PROPOSAL
        and _hypothesis_proposal_subkind(request.task_context)
        is ResearchNodeSubkind.SYNTHESIS
    )


def _embedded_fact_payloads(
    request: AgentRequest,
    fact_type: str,
) -> tuple[Mapping[str, Any], ...]:
    """Select exact Host-rendered fact payloads from mixed immutable inputs."""

    payloads: list[Mapping[str, Any]] = []
    for item in request.input_artifacts:
        if item.media_type != "application/json":
            continue
        try:
            value = _json_object(item.content_bytes(), "embedded fact")
        except AgentACIError:
            continue
        if (
            set(value) != {"fact_id", "fact_type", "payload"}
            or value.get("fact_id") != item.ref
            or value.get("fact_type") != fact_type
            or not isinstance(value.get("payload"), Mapping)
        ):
            continue
        payload = value["payload"]
        assert isinstance(payload, Mapping)
        if (
            payload.get("fact_id") != item.ref
            or payload.get("fact_type") != fact_type
            or payload.get("run_id") != request.run_id
        ):
            continue
        payloads.append(payload)
    return tuple(payloads)


def hypothesis_update_current_assessment(
    request: AgentRequest,
) -> HypothesisAssessment | None:
    """Derive only the exact current component/role/cycle hypothesis assessment.

    Inputs are a mixed immutable context package.  A dependency assessment or a
    supported historical cycle must not constrain the current local update.
    Multiple assessments for the same tip retain the Host-rendered fact order,
    matching the Host's latest-item selection.  Ambiguous or incomplete
    synthetic context therefore yields no derivation.
    """

    if (
        request.command is not AgentCommand.HYPOTHESIS_UPDATE
        or not isinstance(request.task_context, ResearchNodeTask)
        or request.task_context.key.node_kind
        is not ResearchNodeKind.HYPOTHESIS_UPDATE
        or request.method_family_id is None
    ):
        return None
    cycle = request.task_context.key.cycle
    hypotheses: dict[str, tuple[str | None, str]] = {}
    for payload in _embedded_fact_payloads(request, "HypothesisProposed"):
        if (
            payload.get("component_id") != request.component_id
            or payload.get("proposer_role") != request.role
        ):
            continue
        hypothesis_id = payload.get("hypothesis_id")
        parent_hypothesis_id = payload.get("parent_hypothesis_id")
        method_family_id = payload.get("method_family_id")
        if (
            not isinstance(hypothesis_id, str)
            or not hypothesis_id
            or parent_hypothesis_id is not None
            and (not isinstance(parent_hypothesis_id, str) or not parent_hypothesis_id)
            or not isinstance(method_family_id, str)
            or not method_family_id
            or hypothesis_id in hypotheses
        ):
            return None
        hypotheses[hypothesis_id] = (parent_hypothesis_id, method_family_id)
    if not hypotheses:
        return None
    if request.schema_version >= MULTICHAIN_ACI_SCHEMA_VERSION:
        hypothesis_id = _batch_selected_hypothesis(request)
        if hypothesis_id is None:
            return None
        if hypothesis_id not in hypotheses:
            raise AgentACIError("invalid_batch_context", "selected tip lacks its exact own accepted hypothesis input")
    else:
        roots = tuple(
            hypothesis_id
            for hypothesis_id, (parent, _family) in hypotheses.items()
            if parent is None
        )
        if len(roots) != 1:
            return None
        chain = [roots[0]]
        while True:
            children = tuple(hypothesis_id for hypothesis_id, (parent, _family) in hypotheses.items() if parent == chain[-1])
            if not children:
                break
            if len(children) != 1 or children[0] in chain:
                return None
            chain.append(children[0])
        if len(chain) != len(hypotheses) or len(chain) != cycle:
            return None
        hypothesis_id = chain[-1]
    if hypotheses[hypothesis_id][1] != request.method_family_id:
        return None
    assessments = tuple(
        payload
        for payload in _embedded_fact_payloads(request, "HypothesisAssessed")
        if payload.get("component_id") == request.component_id
        and payload.get("hypothesis_id") == hypothesis_id
        and payload.get("method_family_id") == request.method_family_id
    )
    if not assessments:
        return None
    try:
        return HypothesisAssessment(assessments[-1].get("assessment"))
    except (TypeError, ValueError):
        return None


def _batch_selected_hypothesis(request: AgentRequest) -> str | None:
    ref = request.command_id + "--batch-context"
    items = [item for item in request.input_artifacts if item.ref == ref and item.media_type == "application/json"]
    if len(items) != 1 or ref not in request.immutable_context_refs:
        raise AgentACIError("invalid_batch_context", "ACI14 update requires its exact batch selection input")
    value = _json_object(items[0].content_bytes(), "batch context")
    _strict_keys(value, {"batch_command_id", "cycle", "chains", "terminal_tips", "selected_tip"}, "batch context")
    _text(value["batch_command_id"], "batch command")
    if type(value["cycle"]) is not int or value["cycle"] != request.task_context.key.cycle:
        raise AgentACIError("invalid_batch_context", "batch cycle differs from its node")
    selected = value["selected_tip"]
    if selected is None:
        return None
    selected = _mapping(selected, "selected batch tip")
    chains = _mapping(value["chains"], "batch chains")
    if (selected.get("run_id"), selected.get("component_id"), selected.get("expert_slot"), selected.get("method_family_id")) != (
            request.run_id, request.component_id, request.role, request.method_family_id):
        raise AgentACIError("invalid_batch_context", "selected tip ownership differs from its node")
    hypothesis_id = selected.get("node_id")
    if chains.get(selected.get("chain_id")) != hypothesis_id or selected.get("outcome") not in {"SUCCESS", "CLEAN_REFUTATION"}:
        raise AgentACIError("invalid_batch_context", "selected tip is not a comparable member of this exact batch")
    _text(hypothesis_id, "selected hypothesis")
    return hypothesis_id


def _synthesis_parent_hypothesis_id(request: AgentRequest) -> str | None:
    """Read the fixed parent; ACI17 may instead bind a prior revision batch."""

    assert isinstance(request.task_context, ResearchNodeTask)
    cycle = request.task_context.key.cycle
    lineage_inputs = tuple(
        item
        for item in request.input_artifacts
        if item.ref.endswith("--lineage-context")
    )
    if cycle == 1:
        if lineage_inputs:
            raise AgentACIError(
                "invalid_lineage_context",
                "cycle-one synthesis must not carry a predecessor binding",
            )
        return None
    if len(lineage_inputs) != 1:
        raise AgentACIError(
            "invalid_lineage_context",
            "successor synthesis requires exactly one lineage-context input",
        )
    lineage_input = lineage_inputs[0]
    if lineage_input.media_type != "application/json":
        raise AgentACIError(
            "invalid_lineage_context",
            "lineage-context media_type must be application/json",
        )
    try:
        payload = _json_object(lineage_input.content_bytes(), "lineage context")
        _strict_keys(
            payload,
            {"cycle", "parent_hypothesis_id"},
            "lineage context",
        )
        bound_cycle = payload["cycle"]
        if isinstance(bound_cycle, bool) or not isinstance(bound_cycle, int):
            raise AgentACIError(
                "invalid_schema",
                "lineage context cycle must be an integer",
            )
        parent_hypothesis_id = payload["parent_hypothesis_id"]
        if parent_hypothesis_id is not None or request.schema_version < UNSELECTED_REVISION_ACI_SCHEMA_VERSION:
            _text(parent_hypothesis_id, "lineage context parent_hypothesis_id")
    except (AgentACIError, KeyError) as exc:
        message = (
            exc.message
            if isinstance(exc, AgentACIError)
            else f"lineage context missing {exc.args[0]}"
        )
        raise AgentACIError("invalid_lineage_context", message) from exc
    if bound_cycle != cycle:
        raise AgentACIError(
            "invalid_lineage_context",
            f"lineage context cycle {bound_cycle!r} does not match task cycle {cycle!r}",
        )
    return parent_hypothesis_id


def _synthesis_parent_hypothesis_ids(request: AgentRequest) -> tuple[str | None, ...]:
    """Read admitted revision sources, never select a scientific batch winner."""
    parent = _synthesis_parent_hypothesis_id(request)
    cycle = request.task_context.key.cycle
    if parent is not None or cycle == 1:
        return (parent,)
    lineage = next(item for item in request.input_artifacts if item.ref.endswith("--lineage-context"))
    inputs = [item for item in request.input_artifacts if item.ref.endswith("--batch-context")]
    if (lineage.ref not in request.immutable_context_refs or len(inputs) != 1
            or inputs[0].ref != request.command_id + "--batch-context"
            or inputs[0].ref not in request.immutable_context_refs
            or inputs[0].media_type != "application/json"):
        raise AgentACIError("invalid_lineage_context", "unselected successor requires its exact immutable prior batch context")
    value = _json_object(inputs[0].content_bytes(), "prior batch context")
    _strict_keys(value, {"batch_command_id", "cycle", "chains", "terminal_tips", "selected_tip"}, "prior batch context")
    previous = ResearchNodeTask(key=replace(request.task_context.key, cycle=cycle - 1, chain=None))
    if (type(value["cycle"]) is not int or value["cycle"] != cycle - 1
            or value["batch_command_id"] != previous.command_id or value["selected_tip"] is not None):
        raise AgentACIError("invalid_lineage_context", "revision source is not the immediately preceding unselected SYNTHESIS batch")
    chains = _mapping(value["chains"], "prior batch chains")
    tips = _mapping(value["terminal_tips"], "prior terminal tips")
    if not chains or set(tips) != set(chains):
        raise AgentACIError("invalid_lineage_context", "prior batch requires complete nonempty chain and tip domains")
    parents = []
    facts = _embedded_fact_payloads(request, "HypothesisProposed")
    for chain, hypothesis_id in chains.items():
        try:
            chain_session_scope(chain)
        except ValueError as exc:
            raise AgentACIError("invalid_lineage_context", str(exc)) from exc
        _text(hypothesis_id, "revision parent hypothesis")
        matches = [fact for fact in facts if fact.get("hypothesis_id") == hypothesis_id]
        if (len(matches) != 1 or matches[0].get("fact_id") not in request.immutable_context_refs
                or matches[0].get("component_id") != request.component_id
                or matches[0].get("proposer_role") != request.role):
            raise AgentACIError("invalid_lineage_context", "revision parent lacks its exact immutable own hypothesis fact")
        fact = matches[0]
        if tips[chain] is not None:
            tip = _mapping(tips[chain], "prior terminal tip")
            if tuple(tip.get(field) for field in ("run_id", "component_id", "expert_slot", "chain_id", "node_id", "method_family_id")) != (
                    request.run_id, request.component_id, request.role, chain, hypothesis_id, fact.get("method_family_id")):
                raise AgentACIError("invalid_lineage_context", "prior terminal tip differs from its exact revision parent")
        parents.append(hypothesis_id)
    if len(set(parents)) != len(parents):
        raise AgentACIError("invalid_lineage_context", "prior batch has duplicate canonical hypotheses")
    return tuple(sorted(parents))


def _rendezvous_input(request: AgentRequest) -> RendezvousRequest:
    for item in request.input_artifacts:
        try:
            value = _json_object(item.content_bytes(), "RendezvousRequest")
            if value.get("rendezvous_id") is not None:
                return RendezvousRequest.from_dict(value)
        except (AgentACIError, ValueError):
            continue
    raise AgentACIError("rendezvous_request_missing", "answer request lacks its typed rendezvous input")


def _coerce_response(value: AgentResponse | Mapping[str, Any] | bytes | str) -> AgentResponse:
    if isinstance(value, AgentResponse):
        return value
    if isinstance(value, (bytes, str)):
        return AgentResponse.from_json(value)
    if isinstance(value, Mapping):
        return AgentResponse.from_dict(value)
    raise AgentACIError("invalid_response", f"unsupported response type {type(value).__name__}")


def _json_object(raw: bytes | str, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        raise AgentACIError("malformed_json", f"{label}: {exc}") from exc
    return _mapping(value, label)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise AgentACIError("invalid_schema", f"{label} must be an object with string keys")
    return value


def _sequence(value: Any, label: str) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        raise AgentACIError("invalid_schema", f"{label} must be an array")
    return tuple(value)


def _tuple(value: Any, label: str) -> tuple[Any, ...]:
    return _sequence(value, label)


def _strict_keys(
    value: Mapping[str, Any],
    required: set[str],
    label: str,
    *,
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    keys = set(value)
    missing = required - optional - keys
    unknown = keys - required
    if missing or unknown:
        raise AgentACIError(
            "invalid_schema",
            f"{label} missing={sorted(missing)!r} unknown={sorted(unknown)!r}",
        )


def _enum(kind: type[Enum], value: Any, label: str) -> Any:
    try:
        return kind(value)
    except (TypeError, ValueError) as exc:
        raise AgentACIError("invalid_schema", f"unknown {label} {value!r}") from exc


def _text(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise AgentACIError("invalid_schema", f"{label} must be non-empty trimmed text")


def _text_tuple(value: Any, label: str, *, allow_empty: bool) -> None:
    if not isinstance(value, tuple) or (not allow_empty and not value):
        raise AgentACIError("invalid_schema", f"{label} must be a tuple")
    for item in value:
        _text(item, f"{label} item")


def _relative_path(value: str) -> PurePosixPath:
    _text(value, "workspace path")
    if "\\" in value:
        raise AgentACIError("unsafe_path", f"backslash is forbidden in {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or any(part in {"", ".", ".."} for part in path.parts):
        raise AgentACIError("unsafe_path", repr(value))
    return path


def _canonical_decimal(value: Any, label: str, *, nonnegative: bool) -> str:
    if isinstance(value, bool):
        raise AgentACIError("invalid_schema", f"{label} must be decimal text")
    try:
        number = Decimal(str(value))
    except Exception as exc:
        raise AgentACIError("invalid_schema", f"{label} must be decimal text") from exc
    if not number.is_finite() or (nonnegative and number < 0):
        raise AgentACIError("invalid_schema", f"{label} is out of range")
    return format(number, "f")


def _digest_or_empty(value: Any, label: str) -> None:
    if value == "":
        return
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise AgentACIError("invalid_digest", f"{label} must be lowercase SHA-256")


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise AgentACIError("noncanonical_json", f"unsupported value {type(value).__name__}")


# One reviewed production default for every real-Agent entry point.  These
# ceilings retain bounded calls while allowing the measured component-v4 draft
# and rendezvous payloads to complete on the qualified provider.
DEFAULT_REAL_AGENT_BUDGET = AgentBudget(
    max_turns=3,
    max_tokens=20_000,
    max_output_bytes=65_536,
    wall_seconds=180,
    max_cost="0.25",
)


__all__ = [
    "ACI_SCHEMA_VERSION",
    "AGENT_COMMANDS",
    "EXPERT_LOOP_ACI_SCHEMA_VERSION",
    "MULTICHAIN_ACI_SCHEMA_VERSION",
    "RESOURCE_LEARNING_ACI_SCHEMA_VERSION",
    "REFERENCE_ALIAS_ACI_SCHEMA_VERSION",
    "EXPERIMENT_SUBMISSION_MIN_ACI_VERSION",
    "EXPERT_LOOP_AGENT_COMMANDS",
    "LEGACY_AGENT_COMMANDS",
    "DEFAULT_REAL_AGENT_BUDGET",
    "AgentACIError",
    "AgentBudget",
    "AgentCommand",
    "AgentRequest",
    "AgentResponse",
    "AgentStatus",
    "DeclaredEffect",
    "EmbeddedInput",
    "ExpertLoopCandidate",
    "GroundingCandidate",
    "LiteratureSummaryCandidate",
    "supports_literature_web",
    "LITERATURE_WEB_MIN_ACI_VERSION",
    "supports_grounding_web",
    "GROUNDING_WEB_MIN_ACI_VERSION",
    "ExperimentSpecCandidate",
    "HypothesisCandidate",
    "HypothesisSetCandidate",
    "HypothesisSetMember",
    "AdjudicationCandidate",
    "IterationDisposition",
    "ValidatedAgentResponse",
    "WorkspaceArtifact",
    "canonical_json",
    "adjudication_reference_ids",
    "hypothesis_update_current_assessment",
    "request_digest",
    "supports_experiment_submission",
    "required_effect",
    "required_output_schema",
    "response_digest",
    "validate_postflight",
    "with_output_digest",
]
