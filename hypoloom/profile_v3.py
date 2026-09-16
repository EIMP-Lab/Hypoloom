"""Pure schema-v3 profile projection onto the existing research contract compiler.

Schema v3 changes only the six-file authoring surface.  It does not introduce a
second runtime contract: admission projects these documents into the existing
component compiler and GoalDefined remains the sole durable source of truth.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping

from .profile_language import (
    PROFILE_LANGUAGE_ROUTES,
    valid_language_tag,
)


PROFILE_V3_VERSION = 3
PROFILE_V3_FILES = (
    "goal.yaml",
    "experts.yaml",
    "methodology.yaml",
    "language.yaml",
    "notify.yaml",
    "safety.yaml",
)

_GOAL_FIELDS = {"topic", "objective", "context", "completion"}
_COMPLETION_FIELDS = {
    "rule",
    "signals",
    "evaluation_protocols",
    "delivery_contracts",
}
_SIGNAL_FIELDS = {
    "id",
    "tier",
    "description",
    "protocol",
    "depends_on",
    "result",
    "seeds",
    "evidence",
}
_METRIC_RESULT_FIELDS = {
    "deferred",
    "kind",
    "metric",
    "direction",
    "target",
    "dataset",
    "setting",
    "baseline",
    "target_overlay_policy",
}
_ARTIFACT_RESULT_FIELDS = {"kind", "evaluation_kind"}
_EVIDENCE_FIELDS = {"artifacts", "inputs"}
_INPUT_FIELDS = {"resource", "produced_by", "artifact", "min_count"}
_EXPERT_FIELDS = {"roles"}
_ROLE_FIELDS = {"id", "background", "reasoning_principles"}
_METHODOLOGY_FIELDS = {"schema_version", "mode", "runtime", "budget"}
_MODE_FIELDS = {
    "research_loop",
    "domain_grounding",
    "experiment_isolation",
    "observer",
    "component_ownership",
}
_LANGUAGE_FIELDS = {"default", "overrides"}


@dataclass(frozen=True, slots=True)
class ProfileV3Issue:
    code: str
    location: str
    message: str


@dataclass(frozen=True, slots=True)
class ProfileV3Projection:
    goal_text: str
    language_text: str
    experts: Mapping[str, Any]
    methodology: Mapping[str, Any]
    mapped_fields: tuple[str, ...]
    source_locations: tuple[tuple[str, str], ...]
    issues: tuple[ProfileV3Issue, ...]


class _Projector:
    def __init__(self) -> None:
        self.issues: list[ProfileV3Issue] = []
        self.source_locations: dict[str, str] = {}
        self.component_order: list[str] = []

    def issue(self, code: str, location: str, message: str) -> None:
        self.issues.append(ProfileV3Issue(code, location, message))

    def mapping(self, value: object, location: str) -> Mapping[str, Any] | None:
        if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
            self.issue("profile_v3_invalid_value", location, "value must be an object with text keys")
            return None
        return value

    def unknown(self, value: Mapping[str, Any], allowed: set[str], location: str) -> None:
        for key in sorted(set(value) - allowed):
            self.issue("profile_v3_unknown_field", f"{location}.{key}", "field is not part of profile schema v3")

    def text(self, value: object, location: str, *, single_line: bool = False) -> str | None:
        if not isinstance(value, str) or not value.strip():
            self.issue("profile_v3_invalid_value", location, "value must be non-empty text")
            return None
        normalized = value.strip()
        if single_line and any(character in normalized for character in "\r\n"):
            self.issue("profile_v3_invalid_value", location, "value must be one line")
            return None
        return normalized

    def text_list(self, value: object, location: str, *, nonempty: bool = False) -> list[str]:
        if (
            not isinstance(value, list)
            or (nonempty and not value)
            or any(not isinstance(item, str) or not item.strip() or item != item.strip() for item in value)
            or len(set(value)) != len(value)
        ):
            self.issue("profile_v3_invalid_value", location, "value must be a unique list of trimmed text")
            return []
        return list(value)

    def project(
        self,
        *,
        goal: Mapping[str, Any],
        experts: Mapping[str, Any],
        methodology: Mapping[str, Any],
        language: Mapping[str, Any],
    ) -> ProfileV3Projection:
        goal_text, criteria, owners, proposers, dependencies, protocols, delivery = self._goal(goal)
        legacy_experts, role_ids = self._experts(experts, methodology)
        # Owner assignment depends only on the declared role and signal order.  It
        # is frozen into the ordinary ResearchContract before CREATE_RUN.
        if criteria:
            owners, proposers = self._balanced_owners(role_ids)
        language_text, language_routes = self._language(language)
        legacy_methodology = self._methodology(
            methodology,
            criteria=criteria,
            owners=owners,
            proposers=proposers,
            dependencies=dependencies,
            protocols=protocols,
            delivery=delivery,
            language_routes=language_routes,
        )
        return ProfileV3Projection(
            goal_text=goal_text,
            language_text=language_text,
            experts=legacy_experts,
            methodology=legacy_methodology,
            mapped_fields=(
                "goal.yaml.topic/objective/context -> research_contract.goal.objective",
                "goal.yaml.completion.signals -> research_contract.components",
                "goal.yaml.completion.evaluation_protocols -> research_contract.protocols",
                "goal.yaml.completion.delivery_contracts -> research_contract.delivery_contracts",
                "experts.yaml.roles -> research_contract.roles",
                "methodology.yaml.mode -> research_contract.methodology.controls",
                "methodology.yaml.runtime -> research_contract.runtime",
                "methodology.yaml.budget -> research_contract run/role budgets",
                "language.yaml.default -> research_contract.goal.language",
                "language.yaml.overrides -> research_contract.methodology.controls.language_routes",
            ),
            source_locations=tuple(sorted(self.source_locations.items())),
            issues=tuple(self.issues),
        )

    def _goal(
        self,
        goal: Mapping[str, Any],
    ) -> tuple[
        str,
        dict[str, list[dict[str, object]]],
        dict[str, str],
        dict[str, list[str]],
        dict[str, list[str]],
        Mapping[str, Any],
        Mapping[str, Any],
    ]:
        self.unknown(goal, _GOAL_FIELDS, "goal.yaml")
        topic = self.text(goal.get("topic"), "goal.yaml.topic", single_line=True) or ""
        objective = self.text(goal.get("objective"), "goal.yaml.objective") or ""
        context_value = goal.get("context", "")
        if not isinstance(context_value, str):
            self.issue("profile_v3_invalid_value", "goal.yaml.context", "context must be text")
            context = ""
        else:
            context = context_value.strip()
        sections = [topic, objective]
        if context:
            sections.extend(("Context", context))
        goal_text = "\n\n".join(section for section in sections if section)

        completion = self.mapping(goal.get("completion"), "goal.yaml.completion") or {}
        self.unknown(completion, _COMPLETION_FIELDS, "goal.yaml.completion")
        if completion.get("rule") != "required_conjunction":
            self.issue(
                "profile_v3_invalid_value",
                "goal.yaml.completion.rule",
                "rule must be required_conjunction",
            )
        raw_signals = completion.get("signals")
        if not isinstance(raw_signals, list) or not raw_signals:
            self.issue("profile_v3_invalid_value", "goal.yaml.completion.signals", "signals must be a non-empty list")
            raw_signals = []
        criteria: dict[str, list[dict[str, object]]] = {"primary": [], "must": [], "should": []}
        dependencies: dict[str, list[str]] = {}
        seen_ids: set[str] = set()
        for index, raw_signal in enumerate(raw_signals):
            location = f"goal.yaml.completion.signals[{index}]"
            signal = self.mapping(raw_signal, location)
            if signal is None:
                continue
            self.unknown(signal, _SIGNAL_FIELDS, location)
            name = self.text(signal.get("id"), f"{location}.id", single_line=True)
            tier = signal.get("tier")
            if not isinstance(tier, str) or tier not in criteria:
                self.issue("profile_v3_invalid_value", f"{location}.tier", "tier must be primary, must, or should")
                continue
            component_id = f"{tier}.{name}" if name else ""
            if component_id in seen_ids:
                self.issue("profile_v3_duplicate_signal", f"{location}.id", f"duplicate signal {component_id!r}")
                continue
            if component_id:
                seen_ids.add(component_id)
            protocol = self.text(signal.get("protocol"), f"{location}.protocol", single_line=True)
            result = self.mapping(signal.get("result"), f"{location}.result") or {}
            spec: dict[str, object] = {"name": name, "experiment_kind": protocol}
            criterion_location = (
                f"methodology.yaml.success_criteria.{tier}[{len(criteria[str(tier)])}]"
            )
            self.source_locations[criterion_location] = location
            for generated, source in (
                ("name", "id"),
                ("experiment_kind", "protocol"),
                ("metric", "result.metric"),
                ("direction", "result.direction"),
                ("target", "result.target"),
                ("dataset", "result.dataset"),
                ("setting", "result.setting"),
                ("baseline", "result.baseline"),
                ("target_overlay_policy", "result.target_overlay_policy"),
                ("evaluation_kind", "result.evaluation_kind"),
                ("seeds", "seeds"),
                ("description", "description"),
                ("required_artifacts", "evidence.artifacts"),
                ("required_inputs", "evidence.inputs"),
            ):
                self.source_locations[f"{criterion_location}.{generated}"] = (
                    f"{location}.{source}"
                )
            kind = result.get("kind")
            if kind == "metric":
                self.unknown(result, _METRIC_RESULT_FIELDS, f"{location}.result")
                if "deferred" in result:
                    spec["deferred"] = result["deferred"]
                for source, destination in (
                    ("metric", "metric"),
                    ("direction", "direction"),
                    ("target", "target"),
                    ("dataset", "dataset"),
                    ("setting", "setting"),
                    ("baseline", "baseline"),
                    ("target_overlay_policy", "target_overlay_policy"),
                ):
                    if source in result:
                        spec[destination] = copy.deepcopy(result[source])
            elif kind == "artifact":
                self.unknown(result, _ARTIFACT_RESULT_FIELDS, f"{location}.result")
                if result.get("evaluation_kind", "artifact_check") != "artifact_check":
                    self.issue("profile_v3_invalid_value", f"{location}.result.evaluation_kind", "only artifact_check is supported")
                spec["evaluation_kind"] = "artifact_check"
            else:
                self.issue("profile_v3_invalid_value", f"{location}.result.kind", "kind must be metric or artifact")
            seeds = signal.get("seeds", [])
            spec["seeds"] = copy.deepcopy(seeds)
            description = signal.get("description")
            if description is not None:
                parsed = self.text(description, f"{location}.description")
                if parsed is not None:
                    spec["description"] = parsed
            evidence = self.mapping(signal.get("evidence", {}), f"{location}.evidence") or {}
            self.unknown(evidence, _EVIDENCE_FIELDS, f"{location}.evidence")
            spec["required_artifacts"] = copy.deepcopy(evidence.get("artifacts", []))
            raw_inputs = evidence.get("inputs", [])
            if not isinstance(raw_inputs, list):
                self.issue("profile_v3_invalid_value", f"{location}.evidence.inputs", "inputs must be a list")
                raw_inputs = []
            for input_index, raw_input in enumerate(raw_inputs):
                input_location = f"{location}.evidence.inputs[{input_index}]"
                item = self.mapping(raw_input, input_location)
                if item is not None:
                    self.unknown(item, _INPUT_FIELDS, input_location)
            spec["required_inputs"] = copy.deepcopy(raw_inputs)
            refs = self.text_list(signal.get("depends_on", []), f"{location}.depends_on")
            if component_id:
                dependencies[component_id] = refs
                self.source_locations[
                    f"methodology.yaml.dependencies.{component_id}"
                ] = f"{location}.depends_on"
                criteria[str(tier)].append(spec)
                self.component_order.append(component_id)

        protocols = self.mapping(
            completion.get("evaluation_protocols", {}),
            "goal.yaml.completion.evaluation_protocols",
        ) or {}
        delivery = self.mapping(
            completion.get("delivery_contracts", {}),
            "goal.yaml.completion.delivery_contracts",
        ) or {}
        self.source_locations[
            "methodology.yaml.experiments.protocols"
        ] = "goal.yaml.completion.evaluation_protocols"
        self.source_locations[
            "methodology.yaml.delivery_contracts"
        ] = "goal.yaml.completion.delivery_contracts"
        return goal_text, criteria, {}, {}, dependencies, copy.deepcopy(protocols), copy.deepcopy(delivery)

    def _experts(
        self,
        experts: Mapping[str, Any],
        methodology: Mapping[str, Any],
    ) -> tuple[dict[str, object], list[str]]:
        self.unknown(experts, _EXPERT_FIELDS, "experts.yaml")
        raw_roles = experts.get("roles")
        if not isinstance(raw_roles, list) or not 1 <= len(raw_roles) <= 3:
            self.issue("profile_v3_invalid_roster", "experts.yaml.roles", "roles must contain one to three entries")
            raw_roles = []
        role_ids: list[str] = []
        focus: dict[str, str] = {}
        for index, raw_role in enumerate(raw_roles):
            location = f"experts.yaml.roles[{index}]"
            role = self.mapping(raw_role, location)
            if role is None:
                continue
            self.unknown(role, _ROLE_FIELDS, location)
            role_id = self.text(role.get("id"), f"{location}.id", single_line=True)
            background = self.text(role.get("background"), f"{location}.background")
            principles = self.text_list(
                role.get("reasoning_principles"),
                f"{location}.reasoning_principles",
                nonempty=True,
            )
            if role_id is None:
                continue
            if role_id in role_ids:
                self.issue("profile_v3_duplicate_role", f"{location}.id", f"duplicate role {role_id!r}")
                continue
            role_ids.append(role_id)
            self.source_locations[f"experts.yaml.enabled[{len(role_ids) - 1}]"] = (
                f"{location}.id"
            )
            self.source_locations[f"experts.yaml.focus.{role_id}"] = location
            self.source_locations[f"experts.yaml.autonomy.{role_id}"] = (
                "methodology.yaml.mode"
            )
            self.source_locations[f"experts.yaml.budgets.{role_id}"] = (
                f"methodology.yaml.budget.roles.{role_id}"
            )
            parts = [f"Background:\n{background or ''}", "General reasoning principles:"]
            parts.extend(f"- {principle}" for principle in principles)
            focus[role_id] = "\n".join(parts).strip()

        budget = self.mapping(methodology.get("budget", {}), "methodology.yaml.budget") or {}
        role_budgets = self.mapping(budget.get("roles", {}), "methodology.yaml.budget.roles") or {}
        return {
            "enabled": role_ids,
            "focus": focus,
            "autonomy": {role_id: "evidence_first" for role_id in role_ids},
            "budgets": copy.deepcopy(dict(role_budgets)),
        }, role_ids

    def _balanced_owners(
        self,
        role_ids: list[str],
    ) -> tuple[dict[str, str], dict[str, list[str]]]:
        required = [
            component_id
            for component_id in self.component_order
            if component_id.startswith(("primary.", "must."))
        ]
        optional = [
            component_id
            for component_id in self.component_order
            if component_id.startswith("should.")
        ]
        if role_ids and len(required) < len(role_ids):
            self.issue(
                "profile_v3_insufficient_required_signals",
                "goal.yaml.completion.signals",
                "every expert needs at least one primary/must signal in the current expert loop",
            )
        owners: dict[str, str] = {}
        if role_ids:
            for index, component_id in enumerate((*required, *optional)):
                owners[component_id] = role_ids[index % len(role_ids)]
        return owners, {component_id: [owner] for component_id, owner in owners.items()}

    def _methodology(
        self,
        methodology: Mapping[str, Any],
        *,
        criteria: Mapping[str, Any],
        owners: Mapping[str, str],
        proposers: Mapping[str, list[str]],
        dependencies: Mapping[str, list[str]],
        protocols: Mapping[str, Any],
        delivery: Mapping[str, Any],
        language_routes: Mapping[str, Any] | None,
    ) -> dict[str, object]:
        self.unknown(methodology, _METHODOLOGY_FIELDS, "methodology.yaml")
        raw_version = methodology.get("schema_version")
        if (
            isinstance(raw_version, bool)
            or not isinstance(raw_version, int)
            or raw_version != PROFILE_V3_VERSION
        ):
            self.issue("profile_contract_version_unsupported", "methodology.yaml.schema_version", "schema_version must be 3 for six-YAML profiles")
        mode = self.mapping(methodology.get("mode"), "methodology.yaml.mode") or {}
        self.unknown(mode, _MODE_FIELDS, "methodology.yaml.mode")
        required_mode = {
            "research_loop": "expert-six-node/1",
            "domain_grounding": "required",
            "experiment_isolation": "git_authoring_worktree",
            "observer": "disabled",
            "component_ownership": "balanced",
        }
        for field, expected in required_mode.items():
            if mode.get(field) != expected:
                self.issue("successor_profile_contract_invalid", f"methodology.yaml.mode.{field}", f"v3 requires exact {field}={expected}")
        runtime = self.mapping(methodology.get("runtime", {}), "methodology.yaml.runtime") or {}
        budget = self.mapping(methodology.get("budget"), "methodology.yaml.budget") or {}
        legacy_budget = copy.deepcopy(dict(budget))
        legacy_budget.pop("roles", None)
        hg: dict[str, object] = {
            "research_loop": mode.get("research_loop"),
            "domain_grounding": mode.get("domain_grounding"),
            "experiment_isolation": mode.get("experiment_isolation"),
            "observer": mode.get("observer"),
        }
        if language_routes is not None:
            hg["language_routes"] = copy.deepcopy(dict(language_routes))
        return {
            "type": "hg",
            "success_criteria": copy.deepcopy(dict(criteria)),
            "component_owners": copy.deepcopy(dict(owners)),
            "component_proposers": copy.deepcopy(dict(proposers)),
            "dependencies": copy.deepcopy(dict(dependencies)),
            "experiments": {"protocols": copy.deepcopy(dict(protocols))},
            "delivery_contracts": copy.deepcopy(dict(delivery)),
            "hg": hg,
            "runtime": copy.deepcopy(dict(runtime)),
            "budget": legacy_budget,
        }

    def _language(
        self,
        language: Mapping[str, Any],
    ) -> tuple[str, dict[str, object] | None]:
        self.unknown(language, _LANGUAGE_FIELDS, "language.yaml")
        default = language.get("default")
        if not valid_language_tag(default):
            self.issue("profile_v3_invalid_language", "language.yaml.default", "default must be a BCP-47-like language tag")
            default = ""
        raw_overrides = self.mapping(language.get("overrides", {}), "language.yaml.overrides") or {}
        for route in sorted(set(raw_overrides) - set(PROFILE_LANGUAGE_ROUTES)):
            self.issue("profile_v3_unknown_route", f"language.yaml.overrides.{route}", "route is not an Agent-backed expert-six-node/1 route")
        overrides: dict[str, str] = {}
        for route in PROFILE_LANGUAGE_ROUTES:
            if route not in raw_overrides:
                continue
            value = raw_overrides[route]
            if not valid_language_tag(value):
                self.issue("profile_v3_invalid_language", f"language.yaml.overrides.{route}", "value must be a BCP-47-like language tag")
                continue
            overrides[route] = value
        self.source_locations[
            "methodology.yaml.hg.language_routes"
        ] = "language.yaml.overrides"
        if not valid_language_tag(default):
            return "", None
        return default, {"overrides": overrides}


def project_profile_v3(
    *,
    goal: Mapping[str, Any],
    experts: Mapping[str, Any],
    methodology: Mapping[str, Any],
    language: Mapping[str, Any],
) -> ProfileV3Projection:
    """Validate and project schema-v3 sources without filesystem effects."""

    return _Projector().project(
        goal=goal,
        experts=experts,
        methodology=methodology,
        language=language,
    )


__all__ = [
    "PROFILE_V3_FILES",
    "PROFILE_V3_VERSION",
    "ProfileV3Issue",
    "ProfileV3Projection",
    "project_profile_v3",
]
