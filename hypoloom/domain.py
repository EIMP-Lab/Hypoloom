"""Immutable domain values for the Hypoloom finite core."""
from __future__ import annotations
from dataclasses import asdict, astuple, dataclass, field, fields, is_dataclass
from decimal import Decimal, DecimalException, InvalidOperation
from enum import Enum
import math
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Mapping, NewType, TypeAlias; from .contract_normalization import normalize_research_contract_experiment_isolation, normalize_research_contract_language
if TYPE_CHECKING:
    from .measurement_contract import ScientificMeasurement
RunId = NewType("RunId", str); WorkflowId = NewType("WorkflowId", str); GoalId = NewType("GoalId", str)
HypothesisId = NewType("HypothesisId", str); ExperimentId = NewType("ExperimentId", str)
ExecutionId = NewType("ExecutionId", str); FactId = NewType("FactId", str); OutcomeId = NewType("OutcomeId", str)
EvidenceId = NewType("EvidenceId", str); AssessmentId = NewType("AssessmentId", str)
VerdictId = NewType("VerdictId", str); CommandId = NewType("CommandId", str); AttemptId = NewType("AttemptId", str)
DecisionId = NewType("DecisionId", str); ArtifactId = NewType("ArtifactId", str)
ComponentId = NewType("ComponentId", str); MethodFamilyId = NewType("MethodFamilyId", str)
FACT_SCHEMA_VERSION = 5
class ResearchFactType(str, Enum):
    GOAL_DEFINED = "GoalDefined"; HYPOTHESIS_PROPOSED = "HypothesisProposed"
    EXPERIMENT_PREREGISTERED = "ExperimentPreregistered"
    CANONICAL_OUTCOME_ACCEPTED = "CanonicalOutcomeAccepted"; EVIDENCE_LINKED = "EvidenceLinked"
    HYPOTHESIS_ASSESSED = "HypothesisAssessed"; GOAL_VERDICT_RECORDED = "GoalVerdictRecorded"
class ScientificOutcome(str, Enum):
    SUCCESS = "SUCCESS"; CLEAN_REFUTATION = "CLEAN_REFUTATION"; INCONCLUSIVE = "INCONCLUSIVE"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"; INTEGRITY_REJECTED = "INTEGRITY_REJECTED"; CANCELLED = "CANCELLED"
REVISIONABLE_INTEGRITY_VALIDATION_CODES = frozenset({"apply_check_failed", "invalid_unified_diff", "missing_implementation_digest", "downstream_target_contract_failed"})
class HypothesisAssessment(str, Enum):
    SUPPORTED = "SUPPORTED"; REFUTED = "REFUTED"; INCONCLUSIVE = "INCONCLUSIVE"
class GoalVerdict(str, Enum):
    GOAL_MET = "GOAL_MET"; GOAL_NOT_MET = "GOAL_NOT_MET"; INCONCLUSIVE = "INCONCLUSIVE"
class ArtifactValidation(str, Enum):
    NOT_VALIDATED = "NOT_VALIDATED"; VALID = "VALID"; REJECTED = "REJECTED"
class EffectKind(str, Enum):
    AGENT = "AGENT"; PROCESS = "PROCESS"
class AttemptTerminalReason(str, Enum):
    SUCCEEDED = "SUCCEEDED"; FAILED = "FAILED"; TIMED_OUT = "TIMED_OUT"; CANCELLED = "CANCELLED"; LOST = "LOST"
class HumanResolution(str, Enum):
    APPROVED = "APPROVED"; REJECTED = "REJECTED"; EDITED = "EDITED"; TIMED_OUT = "TIMED_OUT"; CANCELLED = "CANCELLED"
class StopDisposition(str, Enum):
    FAILED = "FAILED"; CANCELLED = "CANCELLED"
class AgentCommand(str, Enum):
    PROPOSE_HYPOTHESIS = "PROPOSE_HYPOTHESIS"; DRAFT_EXPERIMENT_SPEC = "DRAFT_EXPERIMENT_SPEC"; ANSWER_RENDEZVOUS = "ANSWER_RENDEZVOUS"; GROUNDING = "GROUNDING"; LITERATURE_REVIEW = "LITERATURE_REVIEW"; HYPOTHESIS_PROPOSAL = "HYPOTHESIS_PROPOSAL"; EXPERIMENT_DESIGN_IMPLEMENT = "EXPERIMENT_DESIGN_IMPLEMENT"; EXPERIMENT_EXECUTION = "EXPERIMENT_EXECUTION"; RESULT_ANALYSIS = "RESULT_ANALYSIS"; HYPOTHESIS_UPDATE = "HYPOTHESIS_UPDATE"; OBSERVER = "OBSERVER"; DEBATE = "DEBATE"; LEADER = "LEADER"; COMPACT_CONTEXT = "COMPACT_CONTEXT"
    CYCLE_REPORT = "CYCLE_REPORT"
    ADJUDICATE = "ADJUDICATE"
    SELF_EVOLVE = "SELF_EVOLVE"
    GRAPH_EVOLVE = "GRAPH_EVOLVE"
    FINAL_REPORT = "FINAL_REPORT"
    DEBATE_JUDGE = "DEBATE_JUDGE"
    PAPER_READ = "PAPER_READ"
class MetricDirection(str, Enum):
    MAXIMIZE = "MAXIMIZE"; MINIMIZE = "MINIMIZE"
class ComponentResultKind(str, Enum): METRIC = "metric"; ARTIFACT = "artifact"
class ComponentTier(str, Enum): PRIMARY = "primary"; MUST = "must"; SHOULD = "should"
class ResourcePreference(str, Enum): CPU = "CPU"; AUTO = "AUTO"; GPU_REQUIRED = "GPU_REQUIRED"
class ResourceKind(str, Enum): CPU = "CPU"; GPU = "GPU"
def _require_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be a non-empty, trimmed string")
def _require_id(value: object, field: str) -> None: _require_text(value, field)
def _require_text_tuple(value: object, field: str, *, nonempty: bool = True) -> None:
    if not isinstance(value, tuple) or (nonempty and not value):
        qualifier = "a non-empty" if nonempty else "a"
        raise ValueError(f"{field} must be {qualifier} tuple")
    for item in value: _require_text(item, f"{field} item")
    if len(set(value)) != len(value): raise ValueError(f"{field} must not contain duplicates")
def validate_physical_gpu_uuid(value: Any) -> None:
    _require_text(value, "GPU UUID")
    parts = value.removeprefix("GPU-").split("-") if value.startswith("GPU-") else []
    if [len(part) for part in parts] != [8, 4, 4, 4, 12] or any(
        character not in "0123456789abcdefABCDEF" for part in parts for character in part
    ):
        raise ValueError("GPU UUID must be a physical NVIDIA GPU UUID")
def _require_digest(value: object, field: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
def normalize_metric_value(value: Decimal | int | float | str) -> str:
    """Return a canonical finite decimal representation."""
    if isinstance(value, bool): raise ValueError("metric value must be numeric, not bool")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("metric value must be numeric") from exc
    if not number.is_finite(): raise ValueError("metric value must be finite")
    if number == 0: return "0"
    try:
        normalized = number.normalize()
    except DecimalException as exc:
        raise ValueError("metric magnitude is unsupported") from exc
    return str(normalized) if abs(normalized.adjusted()) > 1000 else format(normalized, "f")
@dataclass(frozen=True, slots=True, kw_only=True)
class RunVersion:
    workflow_definition_version: str; research_contract_version: str; evaluator_version: str
    agent_aci_version: str; runner_version: str; artifact_result_schema_version: str
    runtime_environment_id: str; configuration_id: str; sqlite_runtime_version: str
    def __post_init__(self) -> None:
        for field_name, value in zip(self.__dataclass_fields__, astuple(self), strict=True): _require_text(value, field_name)
    def as_tuple(self) -> tuple[str, str, str, str, str, str, str, str, str]: return astuple(self)
@dataclass(frozen=True, slots=True, kw_only=True)
class BudgetFrame:
    max_agent_attempts: int; max_process_attempts: int; max_wall_seconds: int; max_hypotheses: int; max_experiments: int
    def __post_init__(self) -> None:
        for field_name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")
        if self.max_hypotheses > 3: raise ValueError("max_hypotheses must be <= 3")
        if self.max_experiments > self.max_hypotheses: raise ValueError("max_experiments must be <= max_hypotheses")
@dataclass(frozen=True, slots=True, kw_only=True)
class MetricSpec:
    name: str; direction: MetricDirection; target: str
    def __post_init__(self) -> None:
        _require_text(self.name, "name")
        if not isinstance(self.direction, MetricDirection): raise ValueError("direction must be a MetricDirection")
        object.__setattr__(self, "target", normalize_metric_value(self.target))
    def accepts(self, value: Decimal | int | float | str) -> bool:
        actual = Decimal(normalize_metric_value(value)); target = Decimal(self.target)
        if self.direction is MetricDirection.MAXIMIZE: return actual >= target
        return actual <= target
def _canonical_json(value: object, field_name: str) -> object:
    """Copy a plain JSON value while rejecting ambiguous/non-finite values."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} contains a non-finite number")
        return value
    if isinstance(value, (list, tuple)):
        return [
            _canonical_json(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError(f"{field_name} object keys must be strings")
        return {
            key: _canonical_json(value[key], f"{field_name}.{key}")
            for key in sorted(value)
        }
    raise ValueError(f"{field_name} must contain only plain JSON values")
def _freeze_json(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value
def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw_json(item) for item in value]
    return value
def _contract_mapping(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    return value
def _contract_list(value: object, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return value
def _require_exact_fields(
    value: Mapping[str, object], expected: set[str], field_name: str
) -> None:
    if set(value) != expected:
        unknown = sorted(set(value) - expected)
        missing = sorted(expected - set(value))
        raise ValueError(
            f"{field_name} has unknown fields {unknown!r} or missing fields {missing!r}"
        )
@dataclass(frozen=True, slots=True, kw_only=True)
class ComponentInputRequirement:
    resource: str
    produced_by_component: ComponentId
    logical_artifact: str
    min_count: int = 1
    def __post_init__(self) -> None:
        _require_text(self.resource, "resource")
        _require_id(self.produced_by_component, "produced_by_component")
        _require_text(self.logical_artifact, "logical_artifact")
        if isinstance(self.min_count, bool) or not isinstance(self.min_count, int) or self.min_count <= 0:
            raise ValueError("min_count must be a positive integer")
@dataclass(frozen=True, slots=True, kw_only=True)
class ResearchComponent:
    component_id: ComponentId
    tier: ComponentTier
    name: str
    owner_role: str
    proposer_roles: tuple[str, ...]
    dependencies: tuple[ComponentId, ...]
    required_inputs: tuple[ComponentInputRequirement, ...]
    protocol: str | None
    result_kind: ComponentResultKind
    metric: MetricSpec | None
    seeds: tuple[int, ...]
    required_artifacts: tuple[str, ...]
    deferred_measurement: bool = False
_CONTRACT_TOP_FIELDS = {
    "schema_version", "goal", "roles", "support_role_budgets", "components",
    "protocols", "delivery_contracts", "methodology", "runtime", "budget",
    "safety", "observer",
}
_CONTRACT_COMPONENT_FIELDS = {
    "id", "tier", "name", "owner", "proposers", "dependencies",
    "required_inputs", "protocol", "result", "seeds", "required_artifacts",
}
_CONTRACT_METRIC_RESULT_FIELDS = {
    "kind", "metric", "direction", "target", "baseline", "dataset", "setting",
    "target_overlay_policy",
}
def _text_list(value: object, field_name: str, *, nonempty: bool = False) -> tuple[str, ...]:
    items = _contract_list(value, field_name)
    result = tuple(items)
    _require_text_tuple(result, field_name, nonempty=nonempty)
    return result
def _validate_research_contract(document: dict[str, Any]) -> tuple[tuple[str, ...], tuple[ResearchComponent, ...]]:
    _require_exact_fields(document, _CONTRACT_TOP_FIELDS, "research_contract")
    if isinstance(document["schema_version"], bool) or document["schema_version"] not in {1, 2, 3}:
        raise ValueError("research_contract.schema_version must be 1, 2 or 3")
    roles = _contract_list(document["roles"], "research_contract.roles")
    role_ids: list[str] = []
    for index, raw_role in enumerate(roles):
        role = _contract_mapping(raw_role, f"research_contract.roles[{index}]")
        role_id = role.get("id")
        _require_text(role_id, f"research_contract.roles[{index}].id")
        role_ids.append(role_id)
    if not 1 <= len(role_ids) <= 3 or len(set(role_ids)) != len(role_ids):
        raise ValueError("research_contract.roles must contain 1..3 unique role ids")
    raw_components = _contract_list(document["components"], "research_contract.components")
    if not raw_components:
        raise ValueError("research_contract.components must not be empty")
    components: list[ResearchComponent] = []
    seen_components: set[str] = set()
    for index, raw_component in enumerate(raw_components):
        location = f"research_contract.components[{index}]"
        component = _contract_mapping(raw_component, location)
        allowed = _CONTRACT_COMPONENT_FIELDS | ({"description"} if "description" in component else set())
        _require_exact_fields(component, allowed, location)
        component_id = component["id"]
        name = component["name"]
        owner = component["owner"]
        for value, label in ((component_id, "id"), (name, "name"), (owner, "owner")):
            _require_text(value, f"{location}.{label}")
        try:
            tier = ComponentTier(component["tier"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{location}.tier must be primary, must, or should") from exc
        if component_id != f"{tier.value}.{name}":
            raise ValueError(f"{location}.id must equal tier.name")
        if component_id in seen_components:
            raise ValueError("research_contract component ids must be unique")
        seen_components.add(component_id)
        if owner not in role_ids:
            raise ValueError(f"{location}.owner is not a declared role")
        proposers = _text_list(component["proposers"], f"{location}.proposers", nonempty=True)
        if any(role not in role_ids for role in proposers):
            raise ValueError(f"{location}.proposers contains an undeclared role")
        dependencies = _text_list(component["dependencies"], f"{location}.dependencies")
        artifacts = _text_list(component["required_artifacts"], f"{location}.required_artifacts")
        raw_seeds = _contract_list(component["seeds"], f"{location}.seeds")
        if (
            any(isinstance(seed, bool) or not isinstance(seed, int) or seed < 0 for seed in raw_seeds)
            or raw_seeds != sorted(raw_seeds)
            or len(set(raw_seeds)) != len(raw_seeds)
        ):
            raise ValueError(f"{location}.seeds must be unique sorted non-negative integers")
        protocol = component["protocol"]
        if protocol is not None:
            _require_text(protocol, f"{location}.protocol")
        result = _contract_mapping(component["result"], f"{location}.result")
        try:
            result_kind = ComponentResultKind(result.get("kind"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{location}.result.kind must be metric or artifact") from exc
        metric: MetricSpec | None = None
        deferred = result.get("deferred") is True
        if deferred:
            if (document["schema_version"] not in {2, 3} or result_kind is not ComponentResultKind.METRIC
                    or set(result) - {"kind", "deferred", "metric", "direction"}):
                raise ValueError("deferred measurement requires research contract v2 and kind/deferred/optional metric constraints")
            if "metric" in result: _require_text(result["metric"], "deferred metric name constraint")
            if "direction" in result: result["direction"] = MetricDirection(str(result["direction"]).upper()).value
            if raw_seeds:
                raise ValueError("deferred measurement cannot imply a predeclared trial batch")
        elif result_kind is ComponentResultKind.METRIC:
            unknown = set(result) - _CONTRACT_METRIC_RESULT_FIELDS
            missing = {"kind", "metric", "direction", "target"} - set(result)
            if unknown or missing:
                raise ValueError(f"{location}.result has unknown fields {sorted(unknown)!r} or missing fields {sorted(missing)!r}")
            try:
                metric = MetricSpec(
                    name=result["metric"],
                    direction=MetricDirection(result["direction"]),
                    target=result["target"],
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{location}.result has an invalid metric") from exc
            result["target"] = metric.target
            if "baseline" in result:
                result["baseline"] = normalize_metric_value(result["baseline"])
            for key in ("dataset", "setting", "target_overlay_policy"):
                if key in result:
                    _require_text(result[key], f"{location}.result.{key}")
        else:
            _require_exact_fields(result, {"kind", "evaluation_kind"}, f"{location}.result")
            if result["evaluation_kind"] != "artifact_check":
                raise ValueError(f"{location}.result.evaluation_kind must be artifact_check")
            if raw_seeds:
                raise ValueError(f"{location}.seeds must be empty for an artifact component")
        raw_inputs = _contract_list(component["required_inputs"], f"{location}.required_inputs")
        inputs: list[ComponentInputRequirement] = []
        resources: set[str] = set()
        for input_index, raw_input in enumerate(raw_inputs):
            input_location = f"{location}.required_inputs[{input_index}]"
            item = _contract_mapping(raw_input, input_location)
            required = {"resource", "produced_by", "artifact"}
            allowed_input = required | ({"min_count"} if "min_count" in item else set())
            _require_exact_fields(item, allowed_input, input_location)
            requirement = ComponentInputRequirement(
                resource=item["resource"],
                produced_by_component=ComponentId(item["produced_by"]),
                logical_artifact=item["artifact"],
                min_count=item.get("min_count", 1),
            )
            if requirement.resource in resources:
                raise ValueError(f"{location}.required_inputs resources must be unique")
            resources.add(requirement.resource)
            inputs.append(requirement)
        if "description" in component and not isinstance(component["description"], str):
            raise ValueError(f"{location}.description must be text")
        components.append(
            ResearchComponent(
                component_id=ComponentId(component_id),
                tier=tier,
                name=name,
                owner_role=owner,
                proposer_roles=proposers,
                dependencies=tuple(ComponentId(value) for value in dependencies),
                required_inputs=tuple(inputs),
                protocol=protocol,
                result_kind=result_kind,
                metric=metric,
                seeds=tuple(raw_seeds),
                required_artifacts=artifacts,
                deferred_measurement=deferred,
            )
        )
    by_id = {str(component.component_id): component for component in components}
    protocols = _contract_mapping(document["protocols"], "research_contract.protocols")
    for protocol_name, raw_protocol in protocols.items():
        _require_text(protocol_name, "research_contract.protocols key")
        protocol = _contract_mapping(raw_protocol, f"research_contract.protocols.{protocol_name}")
        _require_exact_fields(protocol, {"result_kind", "requirements"}, f"research_contract.protocols.{protocol_name}")
        try:
            ComponentResultKind(protocol["result_kind"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"research_contract.protocols.{protocol_name}.result_kind is invalid") from exc
        _contract_mapping(protocol["requirements"], f"research_contract.protocols.{protocol_name}.requirements")
    for component in components:
        for dependency in component.dependencies:
            if dependency not in by_id or dependency == component.component_id:
                raise ValueError(f"component {component.component_id} has an unknown or self dependency")
        for requirement in component.required_inputs:
            producer = by_id.get(str(requirement.produced_by_component))
            if producer is None:
                raise ValueError(f"component {component.component_id} has an unknown input producer")
            if requirement.logical_artifact not in producer.required_artifacts:
                raise ValueError(f"component {component.component_id} requests an undeclared producer artifact")
            if requirement.produced_by_component not in component.dependencies:
                raise ValueError(f"component {component.component_id} input producer must be a dependency")
        if component.protocol is None:
            if len(components) != 1:
                raise ValueError(f"component {component.component_id} must name a protocol")
        else:
            protocol = protocols.get(component.protocol)
            if not isinstance(protocol, dict):
                raise ValueError(f"component {component.component_id} names an unknown protocol")
            if protocol["result_kind"] != component.result_kind.value:
                raise ValueError(f"component {component.component_id} protocol result kind differs")
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(component_id: str) -> None:
        if component_id in visiting:
            raise ValueError("research_contract component dependencies must be acyclic")
        if component_id in visited:
            return
        visiting.add(component_id)
        for dependency in by_id[component_id].dependencies:
            visit(str(dependency))
        visiting.remove(component_id)
        visited.add(component_id)
    for component_id in by_id:
        visit(component_id)
    goal = _contract_mapping(document["goal"], "research_contract.goal")
    _require_exact_fields(goal, {"objective", "language", "verdict_rule", "summary_metric"}
        | ({"evaluation"} if document["schema_version"] == 3 else set()), "research_contract.goal")
    if document["schema_version"] == 3 and goal["evaluation"] != {"mode": "grounding"}:
        raise ValueError("research contract v3 requires Grounding evaluation")
    product_goal = document.get("methodology", {}).get("controls", {}).get("product", {}).get("goal", {})
    if product_goal.get("evaluation") is not None and (document["schema_version"] != 3 or product_goal["evaluation"] != "grounding"):
        raise ValueError("product Grounding evaluation differs from research contract version")
    _require_text(goal["objective"], "research_contract.goal.objective")
    if not isinstance(goal["language"], str): raise ValueError("research_contract.goal.language must be text")
    normalize_research_contract_language(document)
    if goal["verdict_rule"] != "required_conjunction":
        raise ValueError("research_contract.goal.verdict_rule must be required_conjunction")
    summary = _contract_mapping(goal["summary_metric"], "research_contract.goal.summary_metric")
    _require_exact_fields(summary, {"name", "direction", "target", "display_only"}, "research_contract.goal.summary_metric")
    try:
        summary_metric = MetricSpec(
            name=summary["name"],
            direction=MetricDirection(summary["direction"]),
            target=summary["target"],
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("research_contract.goal.summary_metric is invalid") from exc
    summary["target"] = summary_metric.target
    required_components = [
        component for component in components
        if component.tier in {ComponentTier.PRIMARY, ComponentTier.MUST}
    ]
    if len(required_components) == 1 and required_components[0].metric is not None:
        source = required_components[0].metric
        expected_summary = {
            "name": source.name,
            "direction": source.direction.value,
            "target": source.target,
            "display_only": True,
        }
    else:
        expected_summary = {
            "name": "required_component_completion",
            "direction": MetricDirection.MAXIMIZE.value,
            "target": str(len(required_components)),
            "display_only": True,
        }
    if summary != expected_summary:
        raise ValueError("research_contract.goal.summary_metric must be display-only and derived from required components")
    for key in _CONTRACT_TOP_FIELDS - {"schema_version", "goal", "roles", "components", "protocols"}:
        _contract_mapping(document[key], f"research_contract.{key}")
    return tuple(role_ids), tuple(components)
@dataclass(frozen=True, slots=True)
class ResearchContract:
    """Deeply immutable canonical component contract persisted by GoalDefined."""
    document: Mapping[str, Any]
    role_ids: tuple[str, ...] = field(init=False, repr=False, compare=False)
    components: tuple[ResearchComponent, ...] = field(init=False, repr=False, compare=False)
    def __post_init__(self) -> None:
        canonical = _canonical_json(self.document, "research_contract")
        if not isinstance(canonical, dict):
            raise ValueError("research_contract must be an object")
        normalize_research_contract_experiment_isolation(canonical); role_ids, components = _validate_research_contract(canonical)
        object.__setattr__(self, "document", _freeze_json(canonical))
        object.__setattr__(self, "role_ids", role_ids)
        object.__setattr__(self, "components", components)
    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ResearchContract:
        return cls(value)
    def __deepcopy__(self, memo):
        # Construction already freezes every nested JSON value and component.
        return self
    def to_dict(self) -> dict[str, Any]:
        value = _thaw_json(self.document)
        assert isinstance(value, dict)
        return value
    def component(self, component_id: str) -> ResearchComponent | None:
        return next(
            (component for component in self.components if component.component_id == component_id),
            None,
        )
    def trial_plan_for(self, component_id: str) -> TrialPlan:
        component = self.component(component_id)
        if component is None:
            raise ValueError(f"unknown component {component_id!r}")
        arms: tuple[str, ...] = ()
        delivery = self.document.get("delivery_contracts")
        if isinstance(delivery, Mapping):
            protocols = delivery.get("protocols")
            if isinstance(protocols, Mapping) and component.protocol is not None:
                protocol = protocols.get(component.protocol)
                if (
                    isinstance(protocol, Mapping)
                    and protocol.get("execution_shape") == "paired_control_treatment"
                ):
                    arms = ("control", "treatment")
        return TrialPlan(seeds=component.seeds, arms=arms)
@dataclass(frozen=True, slots=True, kw_only=True)
class TrialPlan:
    seeds: tuple[int, ...] = ()
    arms: tuple[str, ...] = ()
    trial_ids: tuple[str, ...] = ()
    def __post_init__(self) -> None:
        if (
            not isinstance(self.seeds, tuple)
            or any(isinstance(seed, bool) or not isinstance(seed, int) or seed < 0 for seed in self.seeds)
            or tuple(sorted(self.seeds)) != self.seeds
            or len(set(self.seeds)) != len(self.seeds)
        ):
            raise ValueError("seeds must be unique sorted non-negative integers")
        _require_text_tuple(self.arms, "arms", nonempty=False)
        _require_text_tuple(self.trial_ids, "trial_ids", nonempty=False)
        if self.arms and not self.seeds:
            raise ValueError("arms require an explicit seed plan")
        expected = (
            tuple(f"{arm}:{seed}" for arm in self.arms for seed in self.seeds)
            if self.arms
            else tuple(str(seed) for seed in self.seeds)
        )
        if expected and self.trial_ids and self.trial_ids != expected:
            raise ValueError("trial_ids must exactly match the declared arms and seeds")
        if expected and not self.trial_ids:
            object.__setattr__(self, "trial_ids", expected)
@dataclass(frozen=True, slots=True, kw_only=True)
class ProducerOutcomeBinding:
    resource: str
    produced_by_component: ComponentId
    logical_artifact: str
    min_count: int
    outcome_id: OutcomeId
    artifact_id: ArtifactId
    def __post_init__(self) -> None:
        ComponentInputRequirement(
            resource=self.resource,
            produced_by_component=self.produced_by_component,
            logical_artifact=self.logical_artifact,
            min_count=self.min_count,
        )
        _require_id(self.outcome_id, "outcome_id")
        _require_id(self.artifact_id, "artifact_id")
@dataclass(frozen=True, slots=True, kw_only=True)
class LogicalArtifact:
    logical_artifact: str
    paths: tuple[str, ...]
    def __post_init__(self) -> None:
        _require_text(self.logical_artifact, "logical_artifact")
        _require_text_tuple(self.paths, "paths")
@dataclass(frozen=True, slots=True, kw_only=True)
class ResourceGrant:
    kind: ResourceKind; gpu_uuid: str | None = None
    gpu_uuids: tuple[str, ...] = ()
    def __post_init__(self) -> None:
        if not isinstance(self.kind, ResourceKind): raise ValueError("kind must be a ResourceKind")
        _require_text_tuple(self.gpu_uuids, "gpu_uuids", nonempty=False)
        if self.gpu_uuids and (len(self.gpu_uuids) < 2 or self.gpu_uuids != tuple(sorted(self.gpu_uuids)) or self.gpu_uuid is not None):
            raise ValueError("plural GPU grant must use only sorted unique gpu_uuids with at least two entries")
        if self.kind is ResourceKind.CPU and (self.gpu_uuid is not None or self.gpu_uuids): raise ValueError("CPU grant must not include GPU UUIDs")
        if self.kind is ResourceKind.GPU and not self.device_uuids: raise ValueError("GPU grant requires GPU UUIDs")
        for value in self.device_uuids:
            _require_text(value, "gpu_uuid")
            if value in {"GPU-", "MIG-"} or not value.startswith(("GPU-", "MIG-")): raise ValueError("gpu_uuid must be a GPU or MIG UUID")
    @property
    def device_uuids(self) -> tuple[str, ...]:
        return self.gpu_uuids or (() if self.gpu_uuid is None else (self.gpu_uuid,))
    def to_dict(self) -> dict[str, Any]:
        if self.gpu_uuids: return {"kind": self.kind.value, "gpu_uuids": list(self.gpu_uuids)}
        return {"kind": self.kind.value, "gpu_uuid": self.gpu_uuid}
    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ResourceGrant:
        if not isinstance(value, Mapping): raise ValueError("resource_grant must be an object")
        if set(value) == {"kind", "gpu_uuid"}: return cls(kind=ResourceKind(value["kind"]), gpu_uuid=value["gpu_uuid"])
        if set(value) == {"kind", "gpu_uuids"} and isinstance(value["gpu_uuids"], list) and len(value["gpu_uuids"]) >= 2:
            return cls(kind=ResourceKind(value["kind"]), gpu_uuids=tuple(value["gpu_uuids"]))
        raise ValueError("resource_grant must contain exactly kind and singular or plural GPU UUIDs")
def _resource_estimate_text(value: object, field_name: str, limit: int) -> None:
    _require_text(value, field_name)
    if len(value) > limit or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field_name} must have at most {limit} characters and no control characters")
def normalize_resource_workload(value: object) -> dict[str, str]:
    """Canonical workload labels; these describe scope, not measured resources."""
    if not isinstance(value, Mapping) or not 1 <= len(value) <= 16:
        raise ValueError("workload must be an object with 1..16 string labels")
    for key, item in value.items():
        _resource_estimate_text(key, "workload key", 64)
        _resource_estimate_text(item, "workload value", 256)
    return dict(sorted(value.items()))
@dataclass(frozen=True, slots=True, kw_only=True)
class ResourceEstimate:
    """A per-device prediction and existing Attempt references, not a grant."""
    workload: Mapping[str, str]
    peak_memory_mib: int
    basis: str
    measurement_attempt_ids: tuple[str, ...] = ()
    def __post_init__(self) -> None:
        object.__setattr__(self, "workload", MappingProxyType(normalize_resource_workload(self.workload)))
        if type(self.peak_memory_mib) is not int or not 0 < self.peak_memory_mib <= 9_007_199_254_740_991:
            raise ValueError("peak_memory_mib must be a positive JSON-safe integer")
        _resource_estimate_text(self.basis, "basis", 1024)
        _require_text_tuple(self.measurement_attempt_ids, "measurement_attempt_ids", nonempty=False)
        if len(self.measurement_attempt_ids) > 32:
            raise ValueError("measurement_attempt_ids must contain at most 32 existing Attempt IDs")
        for item in self.measurement_attempt_ids: _resource_estimate_text(item, "measurement_attempt_id", 256)
        object.__setattr__(self, "measurement_attempt_ids", tuple(sorted(self.measurement_attempt_ids)))
    def to_dict(self) -> dict[str, Any]:
        return {"workload": dict(self.workload), "peak_memory_mib": self.peak_memory_mib,
                "basis": self.basis, "measurement_attempt_ids": list(self.measurement_attempt_ids)}
    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ResourceEstimate:
        required = {"workload", "peak_memory_mib", "basis"}
        if (not isinstance(value, Mapping) or not required <= set(value) or set(value) - required - {"measurement_attempt_ids"}
                or not isinstance(value.get("measurement_attempt_ids", []), list)):
            raise ValueError("resource_estimate requires workload, peak_memory_mib, basis and optional measurement_attempt_ids array")
        return cls(workload=value["workload"], peak_memory_mib=value["peak_memory_mib"], basis=value["basis"],
                   measurement_attempt_ids=tuple(value.get("measurement_attempt_ids", [])))
@dataclass(frozen=True, slots=True, kw_only=True)
class ResourceIntent:
    """A GPU capacity request whose allowlist is not an assigned device set."""
    kind: ResourceKind = ResourceKind.GPU
    allowed_gpu_uuids: tuple[str, ...]
    gpu_count: int
    workload_memory_mib: int
    reserve_memory_mib: int
    def __post_init__(self) -> None:
        if self.kind is not ResourceKind.GPU: raise ValueError("resource_intent kind must be ResourceKind.GPU")
        _require_text_tuple(self.allowed_gpu_uuids, "allowed_gpu_uuids")
        if self.allowed_gpu_uuids != tuple(sorted(self.allowed_gpu_uuids)):
            raise ValueError("allowed_gpu_uuids must be sorted")
        for uuid in self.allowed_gpu_uuids: validate_physical_gpu_uuid(uuid)
        if type(self.gpu_count) is not int or not 1 <= self.gpu_count <= len(self.allowed_gpu_uuids):
            raise ValueError("gpu_count must be a positive integer within the authorized allowlist")
        if type(self.workload_memory_mib) is not int or self.workload_memory_mib <= 0:
            raise ValueError("workload_memory_mib must be a positive integer")
        if type(self.reserve_memory_mib) is not int or self.reserve_memory_mib < 0:
            raise ValueError("reserve_memory_mib must be a nonnegative integer")
    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "allowed_gpu_uuids": list(self.allowed_gpu_uuids),
                "gpu_count": self.gpu_count, "workload_memory_mib": self.workload_memory_mib,
                "reserve_memory_mib": self.reserve_memory_mib}
    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ResourceIntent:
        expected = {"kind", "allowed_gpu_uuids", "gpu_count", "workload_memory_mib", "reserve_memory_mib"}
        if not isinstance(value, Mapping) or set(value) != expected or not isinstance(value["allowed_gpu_uuids"], list):
            raise ValueError("resource_intent must contain exactly kind, allowed_gpu_uuids array, gpu_count, workload_memory_mib and reserve_memory_mib")
        return cls(**{**value, "kind": ResourceKind(value["kind"]), "allowed_gpu_uuids": tuple(value["allowed_gpu_uuids"])})
@dataclass(frozen=True, slots=True, kw_only=True)
class HypothesisCandidate:
    claim: str; owner_role: str; assumptions: tuple[str, ...] = ()
    parent_hypothesis_id: HypothesisId | None = None; relation: str | None = None
    def __post_init__(self) -> None:
        _require_text(self.claim, "claim"); _require_text(self.owner_role, "owner_role")
        _require_text_tuple(self.assumptions, "assumptions", nonempty=False)
        if self.parent_hypothesis_id is not None: _require_id(self.parent_hypothesis_id, "parent_hypothesis_id")
        if (self.parent_hypothesis_id is None) != (self.relation is None):
            raise ValueError("parent_hypothesis_id and relation must be supplied together")
        if self.relation is not None: _require_text(self.relation, "relation")
@dataclass(frozen=True, slots=True, kw_only=True)
class ExperimentSpecCandidate:
    method: str; metrics: tuple[MetricSpec, ...]; acceptance_criteria: tuple[str, ...]
    evaluator_version: str; artifact_requirements: tuple[str, ...]; resource_preference: ResourcePreference
    def __post_init__(self) -> None:
        _require_text(self.method, "method")
        if not isinstance(self.metrics, tuple) or not self.metrics: raise ValueError("metrics must be a non-empty tuple")
        if not all(isinstance(metric, MetricSpec) for metric in self.metrics):
            raise ValueError("metrics must contain only MetricSpec values")
        if len({metric.name for metric in self.metrics}) != len(self.metrics): raise ValueError("metric names must be unique")
        _require_text_tuple(self.acceptance_criteria, "acceptance_criteria")
        _require_text(self.evaluator_version, "evaluator_version")
        _require_text_tuple(self.artifact_requirements, "artifact_requirements")
        if not isinstance(self.resource_preference, ResourcePreference): raise ValueError("resource_preference must be a ResourcePreference")
@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionContract:
    source_agent_attempt_id: AttemptId; source_agent_output_digest: str; argv: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]; data_splits: tuple[str, ...]; sandbox_profile: str
    timeout_seconds: float; resource_preference: ResourcePreference; resource_grant: ResourceGrant | None = None
    source_submit_command_id: str | None = None
    resource_intent: ResourceIntent | None = None
    def __post_init__(self) -> None:
        _require_id(self.source_agent_attempt_id, "source_agent_attempt_id")
        _require_digest(self.source_agent_output_digest, "source_agent_output_digest")
        if self.source_submit_command_id is not None:
            _require_id(self.source_submit_command_id, "source_submit_command_id")
        # argv is an ordered sequence: repeated flags and values are meaningful.
        if not isinstance(self.argv, tuple) or not self.argv:
            raise ValueError("argv must be a non-empty tuple")
        for item in self.argv: _require_text(item, "argv item")
        if not isinstance(self.environment, tuple) or any(not isinstance(item, tuple) or len(item) != 2 for item in self.environment):
            raise ValueError("environment must be a tuple of name/value pairs")
        names: list[str] = []
        for name, value in self.environment:
            _require_text(name, "environment name")
            if "=" in name or "\x00" in name or not isinstance(value, str) or "\x00" in value:
                raise ValueError("environment entries must be safe strings")
            names.append(name)
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("environment names must be unique and sorted")
        _require_text_tuple(self.data_splits, "data_splits"); _require_text(self.sandbox_profile, "sandbox_profile")
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float)) or not 0 < float(self.timeout_seconds) < float("inf"):
            raise ValueError("timeout_seconds must be finite and positive")
        if not isinstance(self.resource_preference, ResourcePreference): raise ValueError("resource_preference must be a ResourcePreference")
        if (self.resource_grant is None) == (self.resource_intent is None): raise ValueError("exactly one resource_grant or resource_intent is required")
        if self.resource_grant is not None and not isinstance(self.resource_grant, ResourceGrant): raise ValueError("resource_grant must be a ResourceGrant")
        if self.resource_intent is not None and not isinstance(self.resource_intent, ResourceIntent): raise ValueError("resource_intent must be a ResourceIntent")
        resource = self.resource_intent if self.resource_intent is not None else self.resource_grant
        if self.resource_preference is ResourcePreference.CPU and resource.kind is not ResourceKind.CPU: raise ValueError("CPU resource preference requires a CPU grant")
        if self.resource_preference is ResourcePreference.GPU_REQUIRED and resource.kind is not ResourceKind.GPU: raise ValueError("GPU_REQUIRED resource preference requires GPU resources")
@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionIdentity:
    run_id: RunId; experiment_id: ExperimentId; execution_id: ExecutionId
    def __post_init__(self) -> None:
        _require_id(self.run_id, "run_id"); _require_id(self.experiment_id, "experiment_id"); _require_id(self.execution_id, "execution_id")
@dataclass(frozen=True, slots=True, kw_only=True)
class ArtifactRef:
    artifact_id: ArtifactId; artifact_set_digest: str; digest_algorithm: str = "sha256"
    def __post_init__(self) -> None:
        _require_id(self.artifact_id, "artifact_id")
        if self.digest_algorithm != "sha256": raise ValueError("digest_algorithm must be sha256")
        _require_digest(self.artifact_set_digest, "artifact_set_digest")
@dataclass(frozen=True, slots=True, kw_only=True)
class AttemptObservation:
    attempt_id: AttemptId; reason: AttemptTerminalReason; artifact: ArtifactRef | None = None; exit_code: int | None = None
    def __post_init__(self) -> None:
        _require_id(self.attempt_id, "attempt_id")
        if not isinstance(self.reason, AttemptTerminalReason): raise ValueError("reason must be an AttemptTerminalReason")
        if self.exit_code is not None and (isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int)):
            raise ValueError("exit_code must be an integer or None")
        if self.reason is AttemptTerminalReason.LOST and self.artifact is not None:
            raise ValueError("a LOST attempt cannot expose an artifact")
@dataclass(frozen=True, slots=True, kw_only=True)
class _FactBase:
    fact_id: FactId; run_id: RunId; schema_version: int = FACT_SCHEMA_VERSION
    fact_type: ClassVar[ResearchFactType]
    def __post_init__(self) -> None:
        _require_id(self.fact_id, "fact_id"); _require_id(self.run_id, "run_id")
        if isinstance(self.schema_version, bool) or self.schema_version != FACT_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {FACT_SCHEMA_VERSION}")
        _validate_fact(self)
@dataclass(frozen=True, slots=True, kw_only=True)
class GoalDefined(_FactBase):
    fact_type: ClassVar[ResearchFactType] = ResearchFactType.GOAL_DEFINED
    goal_id: GoalId; research_contract: ResearchContract
@dataclass(frozen=True, slots=True, kw_only=True)
class HypothesisProposed(_FactBase):
    fact_type: ClassVar[ResearchFactType] = ResearchFactType.HYPOTHESIS_PROPOSED
    hypothesis_id: HypothesisId; goal_id: GoalId; component_id: ComponentId
    proposer_role: str; method_family_id: MethodFamilyId; claim: str; assumptions: tuple[str, ...] = ()
    parent_hypothesis_id: HypothesisId | None = None; relation: str | None = None
    measurement: ScientificMeasurement | None = None
@dataclass(frozen=True, slots=True, kw_only=True)
class ExperimentPreregistered(_FactBase):
    fact_type: ClassVar[ResearchFactType] = ResearchFactType.EXPERIMENT_PREREGISTERED
    experiment_id: ExperimentId; hypothesis_id: HypothesisId; execution_id: ExecutionId
    component_id: ComponentId; method_family_id: MethodFamilyId; method: str
    result_kind: ComponentResultKind; metric: MetricSpec | None; trial_plan: TrialPlan
    acceptance_criteria: tuple[str, ...]; evaluator_version: str
    required_artifacts: tuple[str, ...]; upstream_outcomes: tuple[ProducerOutcomeBinding, ...]
    resource_preference: ResourcePreference; execution_contract: ExecutionContract; revision_of_outcome_id: OutcomeId | None = None
    measurement: ScientificMeasurement | None = None
    goal_evaluation: Mapping[str, Any] | None = None
@dataclass(frozen=True, slots=True, kw_only=True)
class CanonicalOutcomeAccepted(_FactBase):
    fact_type: ClassVar[ResearchFactType] = ResearchFactType.CANONICAL_OUTCOME_ACCEPTED
    outcome_id: OutcomeId; experiment_id: ExperimentId; execution_id: ExecutionId; artifact_id: ArtifactId
    component_id: ComponentId; method_family_id: MethodFamilyId; result_kind: ComponentResultKind
    outcome: ScientificOutcome; artifact_set_digest: str; evaluator_version: str; metric_value: str | None
    logical_artifacts: tuple[LogicalArtifact, ...]; validation_code: str | None = None
    goal_evaluation: Mapping[str, Any] | None = None
@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceLinked(_FactBase):
    fact_type: ClassVar[ResearchFactType] = ResearchFactType.EVIDENCE_LINKED
    evidence_id: EvidenceId; hypothesis_id: HypothesisId; outcome_id: OutcomeId; artifact_id: ArtifactId
    component_id: ComponentId; method_family_id: MethodFamilyId; evidence_kind: str
@dataclass(frozen=True, slots=True, kw_only=True)
class HypothesisAssessed(_FactBase):
    fact_type: ClassVar[ResearchFactType] = ResearchFactType.HYPOTHESIS_ASSESSED
    assessment_id: AssessmentId; hypothesis_id: HypothesisId; component_id: ComponentId
    method_family_id: MethodFamilyId; evidence_ids: tuple[EvidenceId, ...]
    assessment: HypothesisAssessment
@dataclass(frozen=True, slots=True, kw_only=True)
class GoalVerdictRecorded(_FactBase):
    fact_type: ClassVar[ResearchFactType] = ResearchFactType.GOAL_VERDICT_RECORDED
    verdict_id: VerdictId; goal_id: GoalId; method_family_id: MethodFamilyId | None
    assessment_ids: tuple[AssessmentId, ...]; verdict: GoalVerdict
def _validate_fact(fact: _FactBase) -> None:
    if isinstance(fact, GoalDefined):
        _require_id(fact.goal_id, "goal_id")
        if isinstance(fact.research_contract, Mapping) and not isinstance(fact.research_contract, ResearchContract):
            object.__setattr__(fact, "research_contract", ResearchContract.from_mapping(fact.research_contract))
        if not isinstance(fact.research_contract, ResearchContract):
            raise ValueError("research_contract must be a ResearchContract or mapping")
    elif isinstance(fact, HypothesisProposed):
        if fact.measurement is not None:
            from .measurement_contract import ScientificMeasurement
            if not isinstance(fact.measurement, ScientificMeasurement): raise ValueError("measurement must be a ScientificMeasurement")
        _require_id(fact.hypothesis_id, "hypothesis_id"); _require_id(fact.goal_id, "goal_id")
        _require_id(fact.component_id, "component_id")
        _require_text(fact.method_family_id, "method_family_id")
        HypothesisCandidate(
            claim=fact.claim, owner_role=fact.proposer_role, assumptions=fact.assumptions,
            parent_hypothesis_id=fact.parent_hypothesis_id, relation=fact.relation,
        )
    elif isinstance(fact, ExperimentPreregistered):
        if fact.goal_evaluation is not None:
            from .shared_acceptance import normalize_goal_evaluation_binding
            object.__setattr__(fact, "goal_evaluation", _freeze_json(normalize_goal_evaluation_binding(fact.goal_evaluation)))
        if fact.measurement is not None:
            from .measurement_contract import ScientificMeasurement
            if not isinstance(fact.measurement, ScientificMeasurement) or fact.measurement.metric != fact.metric:
                raise ValueError("measurement must contain the preregistered metric")
        _require_id(fact.experiment_id, "experiment_id"); _require_id(fact.hypothesis_id, "hypothesis_id")
        _require_id(fact.execution_id, "execution_id"); _require_id(fact.component_id, "component_id")
        _require_text(fact.method_family_id, "method_family_id"); _require_text(fact.method, "method")
        if not isinstance(fact.result_kind, ComponentResultKind):
            raise ValueError("result_kind must be a ComponentResultKind")
        if fact.result_kind is ComponentResultKind.METRIC:
            if not isinstance(fact.metric, MetricSpec):
                raise ValueError("a metric result requires one MetricSpec")
        elif fact.metric is not None:
            raise ValueError("an artifact result must not declare a metric")
        if not isinstance(fact.trial_plan, TrialPlan): raise ValueError("trial_plan must be a TrialPlan")
        _require_text_tuple(fact.acceptance_criteria, "acceptance_criteria")
        _require_text(fact.evaluator_version, "evaluator_version")
        _require_text_tuple(fact.required_artifacts, "required_artifacts", nonempty=False)
        if not isinstance(fact.upstream_outcomes, tuple) or any(
            not isinstance(binding, ProducerOutcomeBinding) for binding in fact.upstream_outcomes
        ):
            raise ValueError("upstream_outcomes must contain only ProducerOutcomeBinding values")
        resources = tuple(binding.resource for binding in fact.upstream_outcomes)
        if len(resources) != len(set(resources)):
            raise ValueError("upstream_outcomes resources must be unique")
        if not isinstance(fact.resource_preference, ResourcePreference):
            raise ValueError("resource_preference must be a ResourcePreference")
        if not isinstance(fact.execution_contract, ExecutionContract): raise ValueError("execution_contract must be an ExecutionContract")
        if fact.resource_preference is not fact.execution_contract.resource_preference: raise ValueError("fact and execution resource preferences must match")
        if fact.revision_of_outcome_id is not None: _require_id(fact.revision_of_outcome_id, "revision_of_outcome_id")
    elif isinstance(fact, CanonicalOutcomeAccepted):
        if fact.goal_evaluation is not None:
            from .shared_acceptance import normalize_goal_evaluation_observation
            object.__setattr__(fact, "goal_evaluation", _freeze_json(normalize_goal_evaluation_observation(fact.goal_evaluation)))
        for name in ("outcome_id", "experiment_id", "execution_id"): _require_id(getattr(fact, name), name)
        _require_id(fact.component_id, "component_id"); _require_text(fact.method_family_id, "method_family_id")
        if not isinstance(fact.result_kind, ComponentResultKind): raise ValueError("result_kind must be a ComponentResultKind")
        if not isinstance(fact.outcome, ScientificOutcome): raise ValueError("outcome must be a ScientificOutcome")
        ArtifactRef(artifact_id=fact.artifact_id, artifact_set_digest=fact.artifact_set_digest)
        _require_text(fact.evaluator_version, "evaluator_version")
        if fact.validation_code is not None: _require_text(fact.validation_code, "validation_code")
        if not isinstance(fact.logical_artifacts, tuple) or any(
            not isinstance(artifact, LogicalArtifact) for artifact in fact.logical_artifacts
        ):
            raise ValueError("logical_artifacts must contain only LogicalArtifact values")
        logical_ids = tuple(artifact.logical_artifact for artifact in fact.logical_artifacts)
        if len(logical_ids) != len(set(logical_ids)):
            raise ValueError("logical_artifacts must have unique logical ids")
        scientific = {ScientificOutcome.SUCCESS, ScientificOutcome.CLEAN_REFUTATION}
        if fact.result_kind is ComponentResultKind.METRIC and fact.outcome in scientific:
            if fact.metric_value is None: raise ValueError("metric_value is required for scientific comparable outcomes")
            object.__setattr__(fact, "metric_value", normalize_metric_value(fact.metric_value))
        elif fact.metric_value is not None:
            raise ValueError("metric_value must be None for artifact or non-comparable outcomes")
    elif isinstance(fact, EvidenceLinked):
        for name in ("evidence_id", "hypothesis_id", "outcome_id", "artifact_id"): _require_id(getattr(fact, name), name)
        _require_id(fact.component_id, "component_id"); _require_text(fact.method_family_id, "method_family_id")
        _require_text(fact.evidence_kind, "evidence_kind")
    elif isinstance(fact, HypothesisAssessed):
        _require_id(fact.assessment_id, "assessment_id"); _require_id(fact.hypothesis_id, "hypothesis_id")
        _require_id(fact.component_id, "component_id"); _require_text(fact.method_family_id, "method_family_id")
        _require_text_tuple(fact.evidence_ids, "evidence_ids")
        if not isinstance(fact.assessment, HypothesisAssessment): raise ValueError("assessment must be a HypothesisAssessment")
    elif isinstance(fact, GoalVerdictRecorded):
        _require_id(fact.verdict_id, "verdict_id"); _require_id(fact.goal_id, "goal_id")
        if not isinstance(fact.verdict, GoalVerdict): raise ValueError("verdict must be a GoalVerdict")
        _require_text_tuple(fact.assessment_ids, "assessment_ids", nonempty=fact.verdict is not GoalVerdict.INCONCLUSIVE)
        if fact.method_family_id is None:
            # Cross-family GOAL_NOT_MET is meaningful only for an accepted
            # shared-goal contract. The kernel checks that contract boundary;
            # this context-free shape validator cannot grant that authority.
            if fact.verdict is GoalVerdict.GOAL_MET:
                raise ValueError("method_family_id is required for a conclusive verdict")
        else:
            _require_text(fact.method_family_id, "method_family_id")
    else:
        raise TypeError("only the seven declared Research fact dataclasses are valid")
FactCandidate: TypeAlias = GoalDefined | HypothesisProposed | ExperimentPreregistered | CanonicalOutcomeAccepted | EvidenceLinked | HypothesisAssessed | GoalVerdictRecorded
def fact_to_dict(fact: FactCandidate) -> dict[str, Any]:
    """Return the canonical JSON-compatible representation of a fact candidate."""
    if not isinstance(fact, _FactBase): raise TypeError("fact must be one of the seven Research fact dataclasses")
    def encode(value: Any) -> Any:
        if isinstance(value, Enum): return value.value
        if isinstance(value, ResearchContract): return value.to_dict()
        if isinstance(value, (ResourceGrant, ResourceIntent)): return value.to_dict()
        if isinstance(value, ExecutionContract): return {item.name: encode(getattr(value, item.name)) for item in fields(value)
            if item.name not in {"resource_grant", "resource_intent"} or getattr(value, item.name) is not None}
        if isinstance(value, Mapping): return {key: encode(item) for key, item in value.items()}
        if is_dataclass(value): return {item.name: encode(getattr(value, item.name)) for item in fields(value)
                                       if item.name not in {"measurement", "goal_evaluation"} or getattr(value, item.name) is not None}
        if isinstance(value, (tuple, list)): return [encode(item) for item in value]
        return value
    payload = encode(fact)
    return {"fact_type": fact.fact_type.value, **payload}
