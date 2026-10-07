"""Pure compiler for the profile-owned Hypoloom research component contract.

The compiler only validates and normalizes already-loaded profile documents.  It
has no filesystem, process, network, Host, fact-mutation, actionability, or
legacy-runtime authority; the durable core remains the sole runtime authority.
"""

from __future__ import annotations

import copy
import json
import math
import re
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any, Mapping

from .agent_aci import AgentACIError, AgentBudget
from .agent_budget import (
    AGENT_INVOCATION_BUDGET_FIELDS,
    MAX_PROVIDER_AUTH_COOLDOWN_SECONDS,
    PROFILE_BUDGET_FIELDS,
    validate_initial_vram_budget,
    ROLE_BUDGET_FIELDS,
)
from .contract_normalization import normalize_experiment_isolation
from .compute_resources import gpu_memory_reserve_mib
from .domain import MetricDirection, MetricSpec, normalize_metric_value
from .profile_language import validate_profile_language_policy


@dataclass(frozen=True, slots=True)
class ContractBlocker:
    code: str
    location: str
    message: str


@dataclass(frozen=True, slots=True)
class ComponentContractCompilation:
    contract: dict[str, object] | None
    roles: tuple[str, ...] | None
    mapped_fields: tuple[str, ...]
    blockers: tuple[ContractBlocker, ...]


def _plain_contract_value(value: object) -> object:
    """Copy a compiled contract value out of the immutable domain wrapper."""
    if isinstance(value, Mapping):
        return {str(key): _plain_contract_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain_contract_value(item) for item in value]
    return copy.deepcopy(value)


_EXPERT_FIELDS = {
    "enabled",
    "experiment_protocol_bindings",
    "focus",
    "autonomy",
    "budgets",
}
_METHODOLOGY_FIELDS = {
    "type",
    "tier",
    "success_criteria",
    "component_owners",
    "component_proposers",
    "dependencies",
    "experiments",
    "delivery_contracts",
    "hg",
    "runtime",
    "budget",
}
_COMPONENT_FIELDS = {
    "deferred",
    "name",
    "experiment_kind",
    "target_overlay_policy",
    "metric",
    "dataset",
    "setting",
    "baseline",
    "direction",
    "target",
    "seeds",
    "required_artifacts",
    "evaluation_kind",
    "required_inputs",
    "description",
}
_INPUT_FIELDS = {"resource", "produced_by", "artifact", "min_count"}
_ROLE_BUDGET_FIELDS = ROLE_BUDGET_FIELDS
_REQUIREMENT_FIELDS = {
    "required_metrics",
    "required_artifacts",
    "required_paths",
    "data_checks",
    "required_cli_flags",
    "required_source_literals",
    "argv_bindings",
    "artifact_validators",
    "inconclusive_if",
}
_INCONCLUSIVE_IF_FIELDS = {"artifact", "path", "equals"}
_REQUIRED_PATH_FIELDS = {"scope", "path", "kind"}
_DATA_CHECK_FIELDS = {"type", "dataset", "cache", "manifest", "val_cache"}
_ARGV_BINDING_FIELDS = {
    "flag",
    "role",
    "scope",
    "path",
    "kind",
    "absolute",
    "overlay_policy",
    "value",
}
_ARTIFACT_VALIDATOR_FIELDS = {
    "type",
    "artifact",
    "base_scope",
    "apply_check",
    "required_paths",
    "equal_groups",
    "min_chars",
    "required_terms",
    "forbidden_terms",
    "exact_key_sets",
    "allowed_values",
}
_DELIVERY_PROTOCOL_FIELDS = {
    "delivery_mode",
    "production",
    "execution_shape",
    "delivery_goals",
    "capabilities",
    "proof_obligations",
}
_CAPABILITY_FIELDS = {
    "role",
    "goals",
    "path",
    "module",
    "required_literals",
    "selection",
    "path_template",
    "path_flag",
    "required_cli_flags",
    "required_artifacts",
    "allowed_interventions",
    "normalizer",
}
_SELECTION_FIELDS = {
    "argv_flag",
    "identity_input",
    "mapping_symbol",
    "key_source_symbol",
    "expected_values",
    "expected_identity_input",
    "control_value",
}
_NORMALIZER_FIELDS = {
    "metric_paths",
    "per_class_path",
    "class_labels",
    "audit_path",
    "audit_max",
    "audit_required_arms",
    "candidate_manifest_resource",
    "checkpoint_resource",
    "checkpoint_torch_load",
}
_HG_FIELDS = {
    "domain_grounding",
    "capability_probe",
    "roster_size",
    "max_pivot_depth",
    "challenge_quota",
    "max_concurrent_experiments",
    "device_assignment",
    "value_threshold",
    "drift_window",
    "review_cooldown_quanta",
    "mode_min_dwell_quanta",
    "contested_grace_quanta",
    "scheduling_mode",
    "experiment_isolation",
    "experiment_campaigns",
    "auto_resolve",
    "autonomy",
    "models",
    "language_routes",
}
_CAMPAIGN_FIELDS = {
    "enabled",
    "progressive_disclosure",
    "required_roles",
    "require_preflight_before_execute",
    "handoff_integrity",
    "role_owners",
}
_HANDOFF_FIELDS = {
    "enabled",
    "schema_version",
    "required_execute_artifacts",
    "require_argv",
    "require_hypothesis_coverage",
    "required_datasets",
}
_AUTONOMY_FIELDS = {
    "mode",
    "on_unresolved_policy",
    "goal_contract",
    "budget",
    "terminal",
    "migration",
}
_MODEL_FIELDS = {
    "executor",
    "delegate_executor",
    "floors",
    "units",
    "difficulty_routing",
    "escalate_after_dirty",
}
_RUNTIME_FIELDS = {
    "mode",
    "conda_env",
    "target_repo",
    "model_provider",
    "model_identity_probe",
    "model_identity_capabilities",
}
_BUDGET_FIELDS = PROFILE_BUDGET_FIELDS
_AGENT_INVOCATION_BUDGET_FIELDS = AGENT_INVOCATION_BUDGET_FIELDS


class _Compiler:
    def __init__(self) -> None:
        self.blockers: list[ContractBlocker] = []
        self.mapped: list[str] = []

    def block(self, code: str, location: str, message: str) -> None:
        self.blockers.append(ContractBlocker(code, location, message))

    def unknown(self, value: Mapping[str, Any], allowed: set[str], location: str) -> None:
        for key in sorted(set(value) - allowed):
            self.block(
                "contract_unknown_field",
                f"{location}.{key}",
                "field is not part of the component-contract schema",
            )

    def mapping(self, value: object, location: str) -> Mapping[str, Any] | None:
        if not isinstance(value, Mapping) or any(not isinstance(k, str) for k in value):
            self.block("contract_invalid_value", location, "value must be an object with text keys")
            return None
        return value

    def text(self, value: object, location: str) -> str | None:
        if not isinstance(value, str) or not value or value != value.strip():
            self.block("contract_invalid_value", location, "value must be non-empty trimmed text")
            return None
        return value

    def text_list(
        self, value: object, location: str, *, empty: bool = True
    ) -> list[str] | None:
        if (
            not isinstance(value, list)
            or (not empty and not value)
            or any(not isinstance(v, str) or not v or v != v.strip() for v in value)
            or len(set(value)) != len(value)
        ):
            self.block("contract_invalid_value", location, "value must be a unique list of trimmed text")
            return None
        return list(value)

    def positive_int(self, value: object, location: str) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            self.block("contract_invalid_value", location, "value must be a positive integer")
            return None
        return value

    def canonical(self, value: object, location: str) -> object | None:
        if isinstance(value, float) and not math.isfinite(value):
            self.block("contract_invalid_value", location, "numeric value must be finite")
            return None
        if value is None or isinstance(value, (str, int, float, bool)):
            return copy.deepcopy(value)
        if isinstance(value, list):
            result: list[object] = []
            for index, item in enumerate(value):
                canonical = self.canonical(item, f"{location}[{index}]")
                result.append(canonical)
            return result
        if isinstance(value, Mapping) and all(isinstance(k, str) for k in value):
            return {
                key: self.canonical(value[key], f"{location}.{key}")
                for key in sorted(value)
            }
        self.block("contract_invalid_value", location, "value is not a plain YAML value")
        return None

    def compile(
        self,
        *,
        goal_text: str,
        language_text: str,
        experts: Mapping[str, Any],
        methodology: Mapping[str, Any],
        notify: Mapping[str, Any],
        safety: Mapping[str, Any],
    ) -> ComponentContractCompilation:
        self.unknown(experts, _EXPERT_FIELDS, "experts.yaml")
        self.unknown(methodology, _METHODOLOGY_FIELDS, "methodology.yaml")
        roles, role_records, support_budgets, raw_bindings = self._roles(experts)
        components, aliases = self._components(methodology, roles)
        protocols = self._protocols(methodology.get("experiments", {}))
        self._validate_role_protocols(role_records, raw_bindings, protocols)
        self._finish_components(components, aliases, methodology, roles, protocols)
        delivery = self._delivery(methodology.get("delivery_contracts", {}), protocols)
        controls = self._controls(methodology.get("hg", {}), aliases, roles)
        if "language_routes" in controls:
            try:
                overrides = validate_profile_language_policy(
                    language_text.strip(),
                    controls["language_routes"],
                )
            except ValueError as exc:
                self.block(
                    "contract_invalid_value",
                    "methodology.yaml.hg.language_routes",
                    str(exc),
                )
            else:
                controls["language_routes"] = {"overrides": overrides}
        runtime = self._simple_mapping(
            methodology.get("runtime", {}), _RUNTIME_FIELDS, "methodology.yaml.runtime"
        )
        budget = self._budget(methodology.get("budget", {}), role_records, support_budgets)
        observer = self._observer(notify)
        safety_contract = self._safety(safety)

        objective = goal_text.strip()
        if not objective:
            self.block("contract_invalid_value", "goal.md", "goal objective must be non-empty")
        self.mapped.extend(
            (
                "goal.md -> research_contract.goal.objective",
                "language.md -> research_contract.goal.language",
                "experts.yaml -> research_contract.roles",
                "methodology.yaml -> research_contract components/protocols/policy",
                "notify.yaml -> research_contract.observer.notify",
                "safety.yaml -> research_contract.safety",
            )
        )
        if methodology.get("type") != "hg":
            self.block("contract_invalid_value", "methodology.yaml.type", "type must be hg")
        for key in methodology:
            if key in _METHODOLOGY_FIELDS:
                self.mapped.append(f"methodology.yaml:{key} -> research_contract")
        for key in experts:
            if key in _EXPERT_FIELDS:
                self.mapped.append(f"experts.yaml:{key} -> research_contract")
        for key in notify:
            if key in {"channels", "digest", "digest_model"}:
                self.mapped.append(f"notify.yaml:{key} -> research_contract.observer.notify")
        for key in safety:
            if key in {"allow_read_paths", "sandbox_os"}:
                self.mapped.append(f"safety.yaml:{key} -> research_contract.safety")

        required = [component for component in components if component.get("tier") in {"primary", "must"}]
        if (len(required) == 1 and required[0].get("result", {}).get("kind") == "metric"
                and not required[0]["result"].get("deferred")):
            result = required[0]["result"]
            summary_metric = {
                "name": result["metric"],
                "direction": result["direction"],
                "target": result["target"],
                "display_only": True,
            }
        else:
            summary_metric = {
                "name": "required_component_completion",
                "direction": "MAXIMIZE",
                "target": str(len(required)),
                "display_only": True,
            }
        contract: dict[str, object] | None = None
        if not self.blockers and roles:
            contract = {
                "schema_version": 2 if any(item.get("result", {}).get("deferred") for item in components) else 1,
                "goal": {
                    "objective": objective,
                    "language": language_text.strip(),
                    "verdict_rule": "required_conjunction",
                    "summary_metric": summary_metric,
                },
                "roles": role_records,
                "support_role_budgets": support_budgets,
                "components": components,
                "protocols": protocols,
                "delivery_contracts": delivery,
                "methodology": {
                    "type": "hg",
                    "tier": self.canonical(methodology.get("tier"), "methodology.yaml.tier"),
                    "controls": controls,
                },
                "runtime": runtime,
                "budget": budget,
                "safety": safety_contract,
                "observer": observer,
            }
        return ComponentContractCompilation(
            contract=contract,
            roles=tuple(roles) if roles else None,
            mapped_fields=tuple(self.mapped),
            blockers=tuple(self.blockers),
        )

    def _roles(
        self, experts: Mapping[str, Any]
    ) -> tuple[list[str], list[dict[str, object]], dict[str, object], Mapping[str, Any]]:
        enabled = self.text_list(experts.get("enabled"), "experts.yaml.enabled", empty=False)
        roles = enabled or []
        if not 1 <= len(roles) <= 3:
            self.block("contract_invalid_roster", "experts.yaml.enabled", "one to three roles are required")
        focus = self.mapping(experts.get("focus", {}), "experts.yaml.focus") or {}
        autonomy = self.mapping(experts.get("autonomy", {}), "experts.yaml.autonomy") or {}
        bindings = self.mapping(
            experts.get("experiment_protocol_bindings", {}),
            "experts.yaml.experiment_protocol_bindings",
        ) or {}
        budgets = self.mapping(experts.get("budgets", {}), "experts.yaml.budgets") or {}
        for location, mapping in (("focus", focus), ("autonomy", autonomy), ("experiment_protocol_bindings", bindings)):
            for name in mapping:
                if name not in roles:
                    self.block("contract_unknown_role", f"experts.yaml.{location}.{name}", "role is not enabled")
        normalized_budgets: dict[str, dict[str, object]] = {}
        for name, raw in budgets.items():
            if name not in roles and name != "grounding-agent":
                self.block(
                    "contract_unknown_role",
                    f"experts.yaml.budgets.{name}",
                    "budget owner is neither an enabled role nor grounding-agent",
                )
            spec = self.mapping(raw, f"experts.yaml.budgets.{name}")
            if spec is None:
                continue
            self.unknown(spec, _ROLE_BUDGET_FIELDS, f"experts.yaml.budgets.{name}")
            normalized: dict[str, object] = {}
            for key, value in spec.items():
                if key not in _ROLE_BUDGET_FIELDS:
                    continue
                parsed = self._usd(value, f"experts.yaml.budgets.{name}.{key}") if key == "per_expert_budget_usd" else self.positive_int(value, f"experts.yaml.budgets.{name}.{key}")
                if parsed is not None:
                    normalized[key] = parsed
            normalized_budgets[name] = normalized
        records: list[dict[str, object]] = []
        for name in roles:
            focus_value = focus.get(name)
            if focus_value is not None and not isinstance(focus_value, str):
                self.block("contract_invalid_value", f"experts.yaml.focus.{name}", "focus must be text")
            autonomy_value = autonomy.get(name)
            if autonomy_value is not None:
                autonomy_value = self.text(autonomy_value, f"experts.yaml.autonomy.{name}")
            records.append(
                {
                    "id": name,
                    "focus": focus_value.strip() if isinstance(focus_value, str) else "",
                    "autonomy": autonomy_value,
                    "protocols": {"default": None, "allowed": []},
                    "budget": normalized_budgets.get(name, {}),
                }
            )
        support = {
            name: value for name, value in normalized_budgets.items() if name not in roles
        }
        return roles, records, support, bindings

    def _components(
        self, methodology: Mapping[str, Any], roles: list[str]
    ) -> tuple[list[dict[str, object]], dict[str, str]]:
        criteria = self.mapping(
            methodology.get("success_criteria"), "methodology.yaml.success_criteria"
        )
        if criteria is None:
            return [], {}
        self.unknown(criteria, {"primary", "must", "should"}, "methodology.yaml.success_criteria")
        components: list[dict[str, object]] = []
        seen: set[str] = set()
        aliases: dict[str, str] = {}
        for tier in ("primary", "must", "should"):
            raw = criteria.get(tier, [] if tier != "primary" else None)
            if tier == "primary" and isinstance(raw, Mapping):
                entries = [raw]
            elif isinstance(raw, list):
                entries = raw
            else:
                self.block("contract_invalid_value", f"methodology.yaml.success_criteria.{tier}", "criterion tier must be an object or list")
                continue
            for index, raw_component in enumerate(entries):
                location = f"methodology.yaml.success_criteria.{tier}[{index}]"
                spec = self.mapping(raw_component, location)
                if spec is None:
                    continue
                self.unknown(spec, _COMPONENT_FIELDS, location)
                raw_name = spec.get("name", spec.get("metric") if tier == "primary" else None)
                name = self.text(raw_name, f"{location}.name")
                if name is None:
                    continue
                component_id = f"{tier}.{name}"
                if component_id in seen:
                    self.block("contract_duplicate_component", f"{location}.name", f"duplicate stable component id {component_id}")
                    continue
                seen.add(component_id)
                result = self._component_result(spec, location)
                artifacts = self.text_list(
                    spec.get("required_artifacts", []), f"{location}.required_artifacts"
                ) or []
                raw_seeds = spec.get("seeds", [])
                seeds: list[int] = []
                if not isinstance(raw_seeds, list) or any(
                    isinstance(seed, bool) or not isinstance(seed, int) or seed < 0
                    for seed in raw_seeds
                ) or len(set(raw_seeds)) != len(raw_seeds) or raw_seeds != sorted(raw_seeds):
                    self.block("contract_invalid_value", f"{location}.seeds", "seeds must be unique non-negative integers in ascending order")
                else:
                    seeds = list(raw_seeds)
                required_inputs = self._required_inputs(spec.get("required_inputs", []), location)
                description = spec.get("description")
                if description is not None and not isinstance(description, str):
                    self.block("contract_invalid_value", f"{location}.description", "description must be text")
                component: dict[str, object] = {
                    "id": component_id,
                    "tier": tier,
                    "name": name,
                    "owner": roles[0] if len(entries) + len(components) == 1 and roles else None,
                    "proposers": [],
                    "dependencies": [],
                    "required_inputs": required_inputs,
                    "protocol": spec.get("experiment_kind"),
                    "result": result,
                    "seeds": seeds,
                    "required_artifacts": artifacts,
                }
                if description is not None:
                    component["description"] = description.strip()
                components.append(component)
        primary_ids = [c["id"] for c in components if c["tier"] == "primary"]
        if not primary_ids:
            self.block(
                "contract_missing_primary",
                "methodology.yaml.success_criteria.primary",
                "at least one primary component is required",
            )
        if len(primary_ids) == 1:
            aliases["primary"] = str(primary_ids[0])
        aliases.update({str(c["id"]): str(c["id"]) for c in components})
        return components, aliases

    def _component_result(self, spec: Mapping[str, Any], location: str) -> dict[str, object]:
        if spec.get("deferred") is True:
            if any(name in spec for name in ("target", "evaluation_kind", "baseline")):
                self.block("contract_invalid_result", location, "deferred measurement cannot contain a predeclared target")
            result = {"kind": "metric", "deferred": True}
            if "metric" in spec: result["metric"] = self.text(spec["metric"], f"{location}.metric")
            if "direction" in spec:
                try: result["direction"] = MetricDirection(str(spec["direction"]).upper()).value
                except ValueError: self.block("contract_invalid_result", location, "invalid metric direction constraint")
            return result
        has_metric = any(key in spec for key in ("metric", "direction", "target"))
        has_artifact = "evaluation_kind" in spec
        if has_metric == has_artifact:
            self.block("contract_invalid_result", location, "component must define exactly one metric or artifact result")
            return {"kind": "invalid"}
        if has_artifact:
            if spec.get("evaluation_kind") != "artifact_check":
                self.block("contract_invalid_result", f"{location}.evaluation_kind", "only artifact_check is supported")
            return {"kind": "artifact", "evaluation_kind": spec.get("evaluation_kind")}
        try:
            directions = {
                "maximize": MetricDirection.MAXIMIZE,
                "MAXIMIZE": MetricDirection.MAXIMIZE,
                "minimize": MetricDirection.MINIMIZE,
                "MINIMIZE": MetricDirection.MINIMIZE,
            }
            metric = MetricSpec(
                name=spec["metric"],
                direction=directions[spec["direction"]],
                target=spec["target"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            self.block("contract_invalid_result", location, str(exc))
            return {"kind": "invalid"}
        result: dict[str, object] = {
            "kind": "metric",
            "metric": metric.name,
            "direction": metric.direction.value,
            "target": metric.target,
        }
        if "baseline" in spec:
            try:
                result["baseline"] = normalize_metric_value(spec["baseline"])
            except ValueError as exc:
                self.block("contract_invalid_result", f"{location}.baseline", str(exc))
        for key in ("dataset", "setting", "target_overlay_policy"):
            if key in spec:
                value = self.text(spec[key], f"{location}.{key}")
                if value is not None:
                    result[key] = value
        return result

    def _required_inputs(self, raw: object, location: str) -> list[dict[str, object]]:
        if not isinstance(raw, list):
            self.block("contract_invalid_value", f"{location}.required_inputs", "required_inputs must be a list")
            return []
        result: list[dict[str, object]] = []
        resources: set[str] = set()
        for index, item in enumerate(raw):
            item_location = f"{location}.required_inputs[{index}]"
            spec = self.mapping(item, item_location)
            if spec is None:
                continue
            self.unknown(spec, _INPUT_FIELDS, item_location)
            normalized: dict[str, object] = {}
            for key in ("resource", "produced_by", "artifact"):
                value = self.text(spec.get(key), f"{item_location}.{key}")
                if value is not None:
                    normalized[key] = value
            resource = normalized.get("resource")
            if isinstance(resource, str) and resource in resources:
                self.block("contract_duplicate_input", f"{item_location}.resource", "resource is duplicated for this component")
            elif isinstance(resource, str):
                resources.add(resource)
            if "min_count" in spec:
                value = self.positive_int(spec["min_count"], f"{item_location}.min_count")
                if value is not None:
                    normalized["min_count"] = value
            result.append(normalized)
        return result

    def _protocols(self, raw_experiments: object) -> dict[str, object]:
        experiments = self.mapping(raw_experiments, "methodology.yaml.experiments")
        if experiments is None:
            return {}
        self.unknown(experiments, {"protocols"}, "methodology.yaml.experiments")
        raw_protocols = self.mapping(experiments.get("protocols", {}), "methodology.yaml.experiments.protocols")
        if raw_protocols is None:
            return {}
        result: dict[str, object] = {}
        for name, raw in raw_protocols.items():
            if self.text(name, "methodology.yaml.experiments.protocols key") is None:
                continue
            location = f"methodology.yaml.experiments.protocols.{name}"
            spec = self.mapping(raw, location)
            if spec is None:
                continue
            self.unknown(spec, {"result_kind", "requirements"}, location)
            kind = spec.get("result_kind")
            valid_kind = isinstance(kind, str) and kind in {"metric", "artifact"}
            if not valid_kind:
                self.block("contract_invalid_protocol", f"{location}.result_kind", "result_kind must be metric or artifact")
            requirements = self._requirements(spec.get("requirements", {}), f"{location}.requirements")
            result[name] = {
                "result_kind": kind if valid_kind else None,
                "requirements": requirements,
            }
        return result

    def _requirements(self, raw: object, location: str) -> dict[str, object]:
        spec = self.mapping(raw, location)
        if spec is None:
            return {}
        self.unknown(spec, _REQUIREMENT_FIELDS, location)
        normalized = copy.deepcopy(dict(spec))
        for field in ("required_metrics", "required_artifacts", "required_cli_flags", "required_source_literals"):
            if field in spec:
                parsed = self.text_list(spec[field], f"{location}.{field}")
                normalized[field] = parsed or []
        if "inconclusive_if" in spec:
            condition = self.mapping(spec["inconclusive_if"], f"{location}.inconclusive_if")
            if condition is not None:
                self.unknown(condition, _INCONCLUSIVE_IF_FIELDS, f"{location}.inconclusive_if")
                artifact = self.text(condition.get("artifact"), f"{location}.inconclusive_if.artifact")
                path = self.text(condition.get("path"), f"{location}.inconclusive_if.path")
                if isinstance(path, str) and any(not part for part in path.split(".")):
                    self.block("contract_invalid_value", f"{location}.inconclusive_if.path", "path must contain non-empty dotted keys")
                if "equals" not in condition or isinstance(condition.get("equals"), (list, tuple, Mapping)):
                    self.block("contract_invalid_value", f"{location}.inconclusive_if.equals", "value must be a scalar")
                normalized["inconclusive_if"] = {
                    "artifact": artifact,
                    "path": path,
                    "equals": copy.deepcopy(condition.get("equals")),
                }
        list_shapes = {
            "required_paths": _REQUIRED_PATH_FIELDS,
            "data_checks": _DATA_CHECK_FIELDS,
            "argv_bindings": _ARGV_BINDING_FIELDS,
            "artifact_validators": _ARTIFACT_VALIDATOR_FIELDS,
        }
        for field, allowed in list_shapes.items():
            if field not in spec:
                continue
            value = spec[field]
            if not isinstance(value, list):
                self.block("contract_invalid_value", f"{location}.{field}", "value must be a list")
                normalized[field] = []
                continue
            for index, item in enumerate(value):
                item_location = f"{location}.{field}[{index}]"
                mapping = self.mapping(item, item_location)
                if mapping is not None:
                    self.unknown(mapping, allowed, item_location)
        canonical = self.canonical(normalized, location)
        return canonical if isinstance(canonical, dict) else {}

    def _validate_role_protocols(
        self,
        role_records: list[dict[str, object]],
        bindings: Mapping[str, Any],
        protocols: Mapping[str, object],
    ) -> None:
        by_name = {str(role["id"]): role for role in role_records}
        for name, raw in bindings.items():
            spec = self.mapping(raw, f"experts.yaml.experiment_protocol_bindings.{name}")
            if spec is None:
                continue
            self.unknown(spec, {"default_kind", "allowed_kinds"}, f"experts.yaml.experiment_protocol_bindings.{name}")
            default = self.text(spec.get("default_kind"), f"experts.yaml.experiment_protocol_bindings.{name}.default_kind")
            allowed = self.text_list(spec.get("allowed_kinds"), f"experts.yaml.experiment_protocol_bindings.{name}.allowed_kinds", empty=False) or []
            for protocol in ([default] if default else []) + allowed:
                if protocol not in protocols:
                    self.block("contract_unknown_protocol", f"experts.yaml.experiment_protocol_bindings.{name}", f"unknown protocol {protocol}")
            if default is not None and default not in allowed:
                self.block("contract_invalid_protocol_binding", f"experts.yaml.experiment_protocol_bindings.{name}", "default protocol must be allowed")
            if name in by_name:
                by_name[name]["protocols"] = {"default": default, "allowed": allowed}

    def _finish_components(
        self,
        components: list[dict[str, object]],
        aliases: Mapping[str, str],
        methodology: Mapping[str, Any],
        roles: list[str],
        protocols: Mapping[str, object],
    ) -> None:
        by_id = {str(component["id"]): component for component in components}
        owners = self.mapping(methodology.get("component_owners", {}), "methodology.yaml.component_owners") or {}
        proposers = self.mapping(methodology.get("component_proposers", {}), "methodology.yaml.component_proposers") or {}
        dependencies = self.mapping(methodology.get("dependencies", {}), "methodology.yaml.dependencies") or {}

        def resolve(reference: object, location: str, code: str = "contract_unknown_component") -> str | None:
            text = self.text(reference, location)
            if text is None:
                return None
            resolved = aliases.get(text)
            if resolved is None:
                self.block(code, location, f"unknown component {text}")
            return resolved

        resolved_owners: dict[str, str] = {}
        for key, role in owners.items():
            component_id = resolve(key, f"methodology.yaml.component_owners.{key}")
            role_name = self.text(role, f"methodology.yaml.component_owners.{key}")
            if role_name is not None and role_name not in roles:
                self.block("contract_unknown_owner", f"methodology.yaml.component_owners.{key}", f"owner {role_name} is not enabled")
            if component_id and role_name:
                if component_id in resolved_owners:
                    self.block("contract_duplicate_owner", f"methodology.yaml.component_owners.{key}", "component owner is declared twice")
                resolved_owners[component_id] = role_name
        for component in components:
            component_id = str(component["id"])
            if component_id in resolved_owners:
                component["owner"] = resolved_owners[component_id]
            elif len(components) == 1 and roles:
                component["owner"] = roles[0]
            else:
                self.block("contract_missing_owner", f"methodology.yaml.component_owners.{component_id}", "every multi-component contract requires an owner")
        resolved_proposers: dict[str, list[str]] = {}
        for key, raw_roles in proposers.items():
            component_id = resolve(key, f"methodology.yaml.component_proposers.{key}")
            values = self.text_list(raw_roles, f"methodology.yaml.component_proposers.{key}", empty=False) or []
            for role in values:
                if role not in roles:
                    self.block("contract_unknown_proposer", f"methodology.yaml.component_proposers.{key}", f"proposer {role} is not enabled")
            if component_id:
                resolved_proposers[component_id] = values
        for component in components:
            component_id = str(component["id"])
            owner = component.get("owner")
            component["proposers"] = resolved_proposers.get(
                component_id, [owner] if isinstance(owner, str) else []
            )

        declared_dependencies: dict[str, list[str]] = {}
        for key, raw_refs in dependencies.items():
            component_id = resolve(key, f"methodology.yaml.dependencies.{key}")
            if not isinstance(raw_refs, list):
                self.block("contract_invalid_value", f"methodology.yaml.dependencies.{key}", "dependencies must be a list")
                continue
            refs: list[str] = []
            for index, reference in enumerate(raw_refs):
                resolved = resolve(reference, f"methodology.yaml.dependencies.{key}[{index}]")
                if resolved in refs:
                    self.block(
                        "contract_duplicate_dependency",
                        f"methodology.yaml.dependencies.{key}[{index}]",
                        "dependency is duplicated",
                    )
                elif resolved:
                    refs.append(resolved)
            if component_id:
                declared_dependencies[component_id] = refs
        for component in components:
            component_id = str(component["id"])
            refs = list(declared_dependencies.get(component_id, []))
            for index, required_input in enumerate(component["required_inputs"]):
                producer = required_input.get("produced_by")
                resolved = aliases.get(producer) if isinstance(producer, str) else None
                if resolved is None:
                    self.block("contract_unknown_input_producer", f"methodology.yaml.success_criteria.{component['tier']}.{component['name']}.required_inputs[{index}].produced_by", f"unknown producer {producer}")
                    continue
                required_input["produced_by"] = resolved
                producer_artifacts = by_id[resolved].get("required_artifacts", [])
                if required_input.get("artifact") not in producer_artifacts:
                    self.block("contract_input_artifact_missing", f"methodology.yaml.success_criteria.{component['tier']}.{component['name']}.required_inputs[{index}].artifact", "producer does not declare the requested artifact")
                if resolved not in refs:
                    refs.append(resolved)
            component["dependencies"] = refs
            protocol = component.get("protocol")
            result = component.get("result")
            if protocol is None and len(components) > 1:
                self.block(
                    "contract_missing_protocol",
                    f"methodology.yaml.success_criteria.{component['tier']}.{component['name']}.experiment_kind",
                    "every component in a multi-component contract requires a protocol",
                )
            elif protocol is not None:
                if not isinstance(protocol, str) or protocol not in protocols:
                    self.block("contract_unknown_protocol", f"methodology.yaml.success_criteria.{component['tier']}.{component['name']}.experiment_kind", f"unknown protocol {protocol}")
                elif isinstance(result, Mapping):
                    protocol_spec = protocols[protocol]
                    if protocol_spec["result_kind"] != result.get("kind"):
                        self.block("contract_protocol_result_mismatch", f"methodology.yaml.success_criteria.{component['tier']}.{component['name']}.experiment_kind", "component and protocol result kinds differ")
                    requirements = protocol_spec.get("requirements", {})
                    protocol_artifacts = requirements.get("required_artifacts", [])
                    if set(component.get("required_artifacts", [])) != set(protocol_artifacts):
                        self.block(
                            "contract_protocol_artifact_mismatch",
                            f"methodology.yaml.success_criteria.{component['tier']}.{component['name']}.required_artifacts",
                            "component and protocol required artifacts differ",
                        )
                    inconclusive_if = requirements.get("inconclusive_if")
                    if isinstance(inconclusive_if, Mapping) and inconclusive_if.get("artifact") not in protocol_artifacts:
                        self.block(
                            "contract_protocol_mismatch",
                            f"methodology.yaml.success_criteria.{component['tier']}.{component['name']}.experiment_kind",
                            "inconclusive_if artifact must be one of the protocol required artifacts",
                        )
                    if result.get("kind") == "metric" and not result.get("deferred") and result.get("metric") not in requirements.get("required_metrics", []):
                        self.block(
                            "contract_protocol_metric_mismatch",
                            f"methodology.yaml.success_criteria.{component['tier']}.{component['name']}.metric",
                            "component metric is not required by its protocol",
                        )
        self._detect_cycle(components)

    def _detect_cycle(self, components: list[dict[str, object]]) -> None:
        graph = {str(c["id"]): list(c["dependencies"]) for c in components}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str, path: list[str]) -> None:
            if node in visiting:
                start = path.index(node)
                cycle = path[start:] + [node]
                self.block("contract_dependency_cycle", "methodology.yaml.dependencies", "dependency cycle: " + " -> ".join(cycle))
                return
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph.get(node, []):
                visit(dependency, path + [node])
            visiting.remove(node)
            visited.add(node)

        for node in graph:
            visit(node, [])

    def _delivery(self, raw: object, protocols: Mapping[str, object]) -> dict[str, object]:
        value = self.mapping(raw, "methodology.yaml.delivery_contracts")
        if value is None:
            return {"enabled": False, "schema_version": None, "protocols": {}}
        self.unknown(value, {"enabled", "schema_version", "protocols"}, "methodology.yaml.delivery_contracts")
        raw_protocols = self.mapping(value.get("protocols", {}), "methodology.yaml.delivery_contracts.protocols") or {}
        for name, raw_spec in raw_protocols.items():
            location = f"methodology.yaml.delivery_contracts.protocols.{name}"
            if name not in protocols:
                self.block("contract_unknown_protocol", location, "delivery protocol is not in the protocol catalog")
            spec = self.mapping(raw_spec, location)
            if spec is None:
                continue
            self.unknown(spec, _DELIVERY_PROTOCOL_FIELDS, location)
            capabilities = self.mapping(spec.get("capabilities", {}), f"{location}.capabilities") or {}
            for capability, raw_capability in capabilities.items():
                cap_location = f"{location}.capabilities.{capability}"
                cap = self.mapping(raw_capability, cap_location)
                if cap is None:
                    continue
                self.unknown(cap, _CAPABILITY_FIELDS, cap_location)
                if "selection" in cap:
                    selection = self.mapping(cap["selection"], f"{cap_location}.selection")
                    if selection is not None:
                        self.unknown(selection, _SELECTION_FIELDS, f"{cap_location}.selection")
                if "normalizer" in cap:
                    normalizer = self.mapping(cap["normalizer"], f"{cap_location}.normalizer")
                    if normalizer is not None:
                        self.unknown(normalizer, _NORMALIZER_FIELDS, f"{cap_location}.normalizer")
            obligations = spec.get("proof_obligations", [])
            if not isinstance(obligations, list):
                self.block("contract_invalid_value", f"{location}.proof_obligations", "proof_obligations must be a list")
                continue
            seen: set[str] = set()
            for index, raw_obligation in enumerate(obligations):
                obligation_location = f"{location}.proof_obligations[{index}]"
                obligation = self.mapping(raw_obligation, obligation_location)
                if obligation is None:
                    continue
                self.unknown(obligation, {"id", "goal", "capabilities"}, obligation_location)
                obligation_id = self.text(obligation.get("id"), f"{obligation_location}.id")
                if obligation_id in seen:
                    self.block("contract_duplicate_delivery_obligation", f"{obligation_location}.id", "proof obligation id is duplicated")
                elif obligation_id:
                    seen.add(obligation_id)
                refs = self.text_list(obligation.get("capabilities"), f"{obligation_location}.capabilities", empty=False) or []
                for ref in refs:
                    if ref not in capabilities:
                        self.block("contract_unknown_delivery_capability", f"{obligation_location}.capabilities", f"unknown capability {ref}")
        canonical = self.canonical(value, "methodology.yaml.delivery_contracts")
        if isinstance(canonical, dict):
            canonical.setdefault("enabled", False)
            canonical.setdefault("schema_version", None)
            canonical.setdefault("protocols", {})
            return canonical
        return {"enabled": False, "schema_version": None, "protocols": {}}

    def _controls(self, raw: object, aliases: Mapping[str, str], roles: list[str]) -> dict[str, object]:
        value = self.mapping(raw, "methodology.yaml.hg")
        if value is None:
            return {}
        self.unknown(value, _HG_FIELDS, "methodology.yaml.hg")
        experiment_isolation: str | None = None
        if "experiment_isolation" in value:
            try:
                experiment_isolation = normalize_experiment_isolation(
                    value["experiment_isolation"],
                    "methodology.yaml.hg.experiment_isolation",
                )
            except ValueError as exc:
                self.block(
                    "contract_invalid_value",
                    "methodology.yaml.hg.experiment_isolation",
                    str(exc),
                )
        if "experiment_campaigns" in value:
            campaigns = self.mapping(value["experiment_campaigns"], "methodology.yaml.hg.experiment_campaigns")
            if campaigns is not None:
                self.unknown(campaigns, _CAMPAIGN_FIELDS, "methodology.yaml.hg.experiment_campaigns")
                if "handoff_integrity" in campaigns:
                    handoff = self.mapping(campaigns["handoff_integrity"], "methodology.yaml.hg.experiment_campaigns.handoff_integrity")
                    if handoff is not None:
                        self.unknown(handoff, _HANDOFF_FIELDS, "methodology.yaml.hg.experiment_campaigns.handoff_integrity")
                role_owners = self.mapping(campaigns.get("role_owners", {}), "methodology.yaml.hg.experiment_campaigns.role_owners") or {}
                for component, raw_owners in role_owners.items():
                    if component != "*" and component not in aliases:
                        self.block("contract_unknown_component", f"methodology.yaml.hg.experiment_campaigns.role_owners.{component}", "unknown component")
                    owners = self.mapping(raw_owners, f"methodology.yaml.hg.experiment_campaigns.role_owners.{component}")
                    if owners is None:
                        continue
                    self.unknown(owners, {"preflight", "analyze"}, f"methodology.yaml.hg.experiment_campaigns.role_owners.{component}")
                    for phase, role in owners.items():
                        if role not in roles:
                            self.block("contract_unknown_role", f"methodology.yaml.hg.experiment_campaigns.role_owners.{component}.{phase}", "role is not enabled")
        if "autonomy" in value:
            autonomy = self.mapping(value["autonomy"], "methodology.yaml.hg.autonomy")
            if autonomy is not None:
                self.unknown(autonomy, _AUTONOMY_FIELDS, "methodology.yaml.hg.autonomy")
                nested = {
                    "budget": {"hard_cap", "allow_raise", "on_breach"},
                    "terminal": {"allow_external_actions", "require_consistent_snapshot"},
                    "migration": {"allowed_bundle_digests", "allow_arbitrary_source_change"},
                }
                for key, allowed in nested.items():
                    if key in autonomy:
                        mapping = self.mapping(autonomy[key], f"methodology.yaml.hg.autonomy.{key}")
                        if mapping is not None:
                            self.unknown(mapping, allowed, f"methodology.yaml.hg.autonomy.{key}")
        if "models" in value:
            models = self.mapping(value["models"], "methodology.yaml.hg.models")
            if models is not None:
                self.unknown(models, _MODEL_FIELDS, "methodology.yaml.hg.models")
                if "difficulty_routing" in models:
                    routing = self.mapping(models["difficulty_routing"], "methodology.yaml.hg.models.difficulty_routing")
                    if routing is not None:
                        self.unknown(routing, {"enabled", "unknown", "low", "medium", "high"}, "methodology.yaml.hg.models.difficulty_routing")
        canonical = self.canonical(value, "methodology.yaml.hg")
        if isinstance(canonical, dict) and experiment_isolation is not None:
            canonical["experiment_isolation"] = experiment_isolation
        return canonical if isinstance(canonical, dict) else {}

    def _simple_mapping(self, raw: object, allowed: set[str], location: str) -> dict[str, object]:
        value = self.mapping(raw, location)
        if value is None:
            return {}
        self.unknown(value, allowed, location)
        canonical = self.canonical(value, location)
        return canonical if isinstance(canonical, dict) else {}

    def _usd(self, value: object, location: str) -> str | None:
        try:
            parsed = Decimal(str(value))
            if not parsed.is_finite() or parsed <= 0: raise ValueError
        except (InvalidOperation, ValueError):
            self.block("contract_invalid_value", location, "value must be finite positive USD")
            return None
        return format(parsed.normalize(), "f")

    def _budget(self, raw: object, roles: list[dict[str, object]], support: dict[str, object]) -> dict[str, object]:
        location = "methodology.yaml.budget"
        value = self.mapping(raw, location)
        if value is None:
            return {}
        self.unknown(value, _BUDGET_FIELDS, location)
        try:
            gpu_memory_reserve_mib(value, 0)
        except ValueError as exc:
            self.block("contract_invalid_value", location, str(exc))
        try:
            validate_initial_vram_budget(value)
        except ValueError as exc:
            self.block("contract_invalid_value", f"{location}.initial_vram_per_experiment", str(exc))
        canonical = self.canonical(value, location)
        result = canonical if isinstance(canonical, dict) else {}
        positive = {"run_tokens", "per_expert_tokens", "max_experiments", "max_papers",
                    "experiment_timeout", "max_active_hypotheses", "max_turns",
                    "max_active_agents", "max_active_processes", "max_active_reviews"}
        nonnegative = {
            "max_retries",
            "max_infrastructure_retries",
            "max_process_lost_retries",
            "min_vram_per_experiment",
            "gpu_vram_reserve_mb",
        }
        for name in positive:
            if name in value and (parsed := self.positive_int(value[name], f"{location}.{name}")) is not None:
                result[name] = parsed
        for name in ("total_budget_usd", "per_expert_budget_usd"):
            if name in value and (parsed := self._usd(value[name], f"{location}.{name}")) is not None:
                result[name] = parsed
        for name in nonnegative:
            if name not in value:
                continue
            item = value[name]
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                self.block("contract_invalid_value", f"{location}.{name}", "value must be a nonnegative integer")
            else:
                result[name] = item
        if "wallclock_hours" in value:
            hours = value["wallclock_hours"]
            if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 0 < float(hours) < float("inf"):
                self.block("contract_invalid_value", f"{location}.wallclock_hours", "value must be finite and positive")
            else:
                result["wallclock_hours"] = hours
        if "provider_auth_cooldown_seconds" in value:
            cooldown = value["provider_auth_cooldown_seconds"]
            if (isinstance(cooldown, bool) or not isinstance(cooldown, (int, float))
                    or not 0 <= float(cooldown) <= MAX_PROVIDER_AUTH_COOLDOWN_SECONDS):
                self.block("contract_invalid_value", f"{location}.provider_auth_cooldown_seconds",
                    f"value must be a finite number from 0 to {int(MAX_PROVIDER_AUTH_COOLDOWN_SECONDS)}")
            else:
                result["provider_auth_cooldown_seconds"] = cooldown
        invocation_location = f"{location}.agent_invocation"
        if "agent_invocation" not in value:
            self.block(
                "contract_missing_field",
                invocation_location,
                "every source profile must declare its Agent invocation budget",
            )
            return result
        invocation = self.mapping(value["agent_invocation"], invocation_location)
        if invocation is None:
            return result
        self.unknown(
            invocation,
            _AGENT_INVOCATION_BUDGET_FIELDS,
            invocation_location,
        )
        missing = (
            _AGENT_INVOCATION_BUDGET_FIELDS
            - {"token_enforcement_mode"}
            - set(invocation)
        )
        for name in sorted(missing):
            self.block(
                "contract_missing_field",
                f"{invocation_location}.{name}",
                "field is required by the Agent invocation budget",
            )
        if missing or set(invocation) - _AGENT_INVOCATION_BUDGET_FIELDS:
            return result

        normalized: dict[str, object] = {}
        for name in ("max_turns", "max_tokens", "max_output_bytes"):
            parsed = self.positive_int(invocation[name], f"{invocation_location}.{name}")
            if parsed is not None:
                normalized[name] = parsed
        wall_seconds = invocation["wall_seconds"]
        if (
            isinstance(wall_seconds, bool)
            or not isinstance(wall_seconds, (int, float))
            or not 0 < float(wall_seconds) < float("inf")
        ):
            self.block(
                "contract_invalid_value",
                f"{invocation_location}.wall_seconds",
                "value must be finite and positive",
            )
        else:
            normalized["wall_seconds"] = wall_seconds
        max_cost = invocation["max_cost"]
        try:
            canonical_cost = (
                normalize_metric_value(max_cost) if isinstance(max_cost, str) else None
            )
        except ValueError:
            canonical_cost = None
        if canonical_cost != max_cost or str(canonical_cost).startswith("-"):
            self.block(
                "contract_invalid_value",
                f"{invocation_location}.max_cost",
                "value must be nonnegative canonical decimal text",
            )
        else:
            normalized["max_cost"] = max_cost
        token_enforcement_mode = invocation.get(
            "token_enforcement_mode", "enforce"
        )
        if (
            not isinstance(token_enforcement_mode, str)
            or token_enforcement_mode not in {"enforce", "observe"}
        ):
            self.block(
                "contract_invalid_value",
                f"{invocation_location}.token_enforcement_mode",
                "value must be enforce or observe",
            )
        else:
            normalized["token_enforcement_mode"] = token_enforcement_mode
        if len(normalized) != len(_AGENT_INVOCATION_BUDGET_FIELDS):
            return result
        try:
            result["agent_invocation"] = AgentBudget.from_dict(normalized).to_dict()
        except AgentACIError as exc:
            self.block("contract_invalid_value", invocation_location, str(exc))
        invocation_tokens = normalized.get("max_tokens")
        for cap_name in ("run_tokens", "per_expert_tokens"):
            if isinstance(invocation_tokens, int) and isinstance(result.get(cap_name), int) and invocation_tokens > result[cap_name]:
                self.block("contract_invalid_value", invocation_location, f"max_tokens exceeds {cap_name}")
        run_tokens = result.get("run_tokens")
        named_role_budgets = [(str(record["id"]), record.get("budget", {})) for record in roles]
        named_role_budgets.extend((name, item) for name, item in support.items())
        for role_name, role_budget in named_role_budgets:
            if not isinstance(role_budget, Mapping):
                continue
            role_tokens = role_budget.get("per_expert_tokens", result.get("per_expert_tokens"))
            role_turns = role_budget.get("max_turns", result.get("max_turns"))
            if isinstance(role_tokens, int) and isinstance(run_tokens, int) and role_tokens > run_tokens:
                self.block("contract_invalid_value", f"experts.yaml.budgets.{role_name}.per_expert_tokens", "role token cap exceeds run_tokens")
            if isinstance(invocation_tokens, int) and isinstance(role_tokens, int) and invocation_tokens > role_tokens:
                self.block("contract_invalid_value", f"experts.yaml.budgets.{role_name}.per_expert_tokens", "role token cap is below one Agent invocation")
            if isinstance(normalized.get("max_turns"), int) and isinstance(role_turns, int) and normalized["max_turns"] > role_turns:
                self.block("contract_invalid_value", f"experts.yaml.budgets.{role_name}.max_turns", "role turn cap is below one Agent invocation")
        return result

    def _observer(self, notify: Mapping[str, Any]) -> dict[str, object]:
        self.unknown(notify, {"channels", "digest", "digest_model"}, "notify.yaml")
        channels = self.text_list(notify.get("channels", []), "notify.yaml.channels") or []
        digest = notify.get("digest", False)
        if not isinstance(digest, bool):
            self.block("contract_invalid_value", "notify.yaml.digest", "digest must be boolean")
        digest_model = notify.get("digest_model")
        if digest_model is not None:
            digest_model = self.text(digest_model, "notify.yaml.digest_model")
        return {"notify": {"channels": channels, "digest": digest, "digest_model": digest_model}}

    def _safety(self, safety: Mapping[str, Any]) -> dict[str, object]:
        self.unknown(safety, {"allow_read_paths", "sandbox_os"}, "safety.yaml")
        roots = self.text_list(safety.get("allow_read_paths", []), "safety.yaml.allow_read_paths") or []
        for index, root in enumerate(roots):
            if not PurePath(root).is_absolute():
                self.block("contract_invalid_read_root", f"safety.yaml.allow_read_paths[{index}]", "read root must be absolute")
        sandbox = safety.get("sandbox_os")
        if sandbox is not None:
            sandbox = self.text(sandbox, "safety.yaml.sandbox_os")
        return {"read_roots": roots, "sandbox": sandbox}


def compile_component_contract(
    *,
    goal_text: str,
    language_text: str,
    experts: Mapping[str, Any],
    methodology: Mapping[str, Any],
    notify: Mapping[str, Any],
    safety: Mapping[str, Any],
) -> ComponentContractCompilation:
    """Validate and normalize six profile documents without side effects."""

    return _Compiler().compile(
        goal_text=goal_text,
        language_text=language_text,
        experts=experts,
        methodology=methodology,
        notify=notify,
        safety=safety,
    )


_EXECUTION_REQUIREMENTS = {
    "required_paths",
    "data_checks",
    "required_cli_flags",
    "required_source_literals",
    "argv_bindings",
}


def component_execution_plan(
    document: Mapping[str, Any],
    component_id: str,
    *,
    method_family_id: str = "profile-check",
    implementation_sha256: str = "0" * 64,
    device: str = "cpu",
) -> dict[str, object]:
    """Derive the only Host-authorized command for one frozen component."""

    components = document.get("components")
    protocols = document.get("protocols")
    delivery = document.get("delivery_contracts")
    if not isinstance(components, (list, tuple)) or not isinstance(protocols, Mapping):
        raise ValueError("research contract has no component protocol catalog")
    component = next(
        (item for item in components if isinstance(item, Mapping) and item.get("id") == component_id),
        None,
    )
    if component is None:
        raise ValueError(f"unknown component {component_id!r}")
    protocol_name = component.get("protocol")
    protocol = protocols.get(protocol_name) if isinstance(protocol_name, str) else None
    requirements = protocol.get("requirements", {}) if isinstance(protocol, Mapping) else {}
    if not isinstance(requirements, Mapping):
        raise ValueError("component protocol requirements are invalid")
    delivery_protocols = delivery.get("protocols", {}) if isinstance(delivery, Mapping) else {}
    delivery_protocol = (
        delivery_protocols.get(protocol_name)
        if isinstance(delivery_protocols, Mapping) and isinstance(protocol_name, str)
        else None
    )
    production = isinstance(delivery_protocol, Mapping) and delivery_protocol.get("production") is True
    has_execution_requirements = any(requirements.get(name) for name in _EXECUTION_REQUIREMENTS)
    if not production:
        if has_execution_requirements:
            raise ValueError("executable protocol requirements need a production delivery contract")
        return {
            "production": False,
            "argv": ("python", "experiment.py"),
            "environment": {},
            "requirements": dict(requirements),
            "capabilities": {},
            "entrypoint_path": None,
        }
    seeds = component.get("seeds")
    if not isinstance(seeds, (list, tuple)):
        raise ValueError("production trials need a frozen seed array")
    runtime = document.get("runtime")
    target_root = runtime.get("target_repo") if isinstance(runtime, Mapping) else None
    if not isinstance(target_root, str) or not PurePath(target_root).is_absolute():
        raise ValueError("production delivery needs an absolute frozen target_repo")
    capabilities = delivery_protocol.get("capabilities")
    if not isinstance(capabilities, Mapping):
        raise ValueError("production delivery capabilities are absent")
    entrypoints = [
        item for item in capabilities.values()
        if isinstance(item, Mapping) and item.get("role") == "entrypoint"
    ]
    if len(entrypoints) != 1:
        raise ValueError("production delivery needs exactly one frozen entrypoint")
    entrypoint = entrypoints[0]
    entrypoint_path = entrypoint.get("path")
    module = entrypoint.get("module")
    if (
        not isinstance(entrypoint_path, str)
        or not _safe_relative(entrypoint_path)
        or not isinstance(module, str)
        or not module
    ):
        raise ValueError("production entrypoint needs a safe path and module")

    shape = delivery_protocol.get("execution_shape")
    if shape in {"paired_control_treatment", "seeded_trials", "checkpoint_audit"}:
        return _batch_execution_plan(
            document=document,
            component=component,
            requirements=requirements,
            delivery_protocol=delivery_protocol,
            capabilities=capabilities,
            target_root=target_root,
            entrypoint_path=entrypoint_path,
            module=module,
            method_family_id=method_family_id,
            implementation_sha256=implementation_sha256,
            device=device,
        )
    if requirements.get("data_checks"):
        raise ValueError("semantic data checks require a Host-owned batch driver")
    if len(seeds) > 1:
        raise ValueError("multiple trials need a frozen trial driver and result normalizer")

    values: dict[str, str] = {}
    for raw in requirements.get("argv_bindings", []):
        if not isinstance(raw, Mapping) or not isinstance(raw.get("flag"), str):
            raise ValueError("argv binding is invalid")
        scope, path = raw.get("scope"), raw.get("path")
        if scope == "target" and isinstance(path, str) and _safe_relative(path):
            value = str(PurePath(target_root) / path)
        elif scope == "absolute" and isinstance(path, str) and PurePath(path).is_absolute():
            value = path
        else:
            raise ValueError("argv binding has no frozen target or absolute path")
        if raw.get("absolute") is not True:
            raise ValueError("production argv bindings must be absolute")
        values[str(raw["flag"])] = value
    selected_model: str | None = None
    for raw in capabilities.values():
        if not isinstance(raw, Mapping):
            continue
        selection = raw.get("selection")
        if isinstance(selection, Mapping):
            expected = selection.get("expected_values")
            flag = selection.get("argv_flag")
            if not isinstance(expected, (list, tuple)) or len(expected) != 1 or not isinstance(flag, str):
                raise ValueError("production selection must freeze one argv value")
            selected_model = str(expected[0])
            values[flag] = selected_model
    result = component.get("result")
    dataset = result.get("dataset") if isinstance(result, Mapping) else None
    for raw in capabilities.values():
        if not isinstance(raw, Mapping) or not raw.get("path_template"):
            continue
        template, flag = raw.get("path_template"), raw.get("path_flag")
        if not isinstance(template, str) or not isinstance(flag, str):
            raise ValueError("production config path binding is invalid")
        if selected_model is None or not isinstance(dataset, str):
            raise ValueError("config path template needs frozen model and dataset values")
        rendered = template.replace("{model}", selected_model).replace(
            "{dataset_lower}", dataset.lower()
        )
        if not _safe_relative(rendered):
            raise ValueError("rendered config path is unsafe")
        values[flag] = str(PurePath(target_root) / rendered)
    builtins = {
        "--dataset": dataset,
        "--seed": str(seeds[0]) if seeds else None,
        "--out": "/output/result.json",
        "--save-ckpt": "/output/checkpoint.pt",
    }
    flags: list[str] = []
    for source in (
        requirements.get("required_cli_flags", []),
        *(raw.get("required_cli_flags", []) for raw in capabilities.values() if isinstance(raw, Mapping)),
        tuple(values),
    ):
        if not isinstance(source, (list, tuple)):
            raise ValueError("required CLI flags must be an array")
        for flag in source:
            if not isinstance(flag, str) or not flag.startswith("--"):
                raise ValueError("required CLI flag is invalid")
            if flag not in flags:
                flags.append(flag)
    argv = ["python", "-m", module]
    for flag in flags:
        value = values.get(flag, builtins.get(flag))
        if not isinstance(value, str) or not value:
            raise ValueError(f"Host cannot derive a frozen value for {flag}")
        argv.extend((flag, value))
    python_root = str(PurePath(target_root) / "src") if entrypoint_path.startswith("src/") else target_root
    return {
        "production": True,
        "argv": tuple(argv),
        "environment": {"PYTHONPATH": python_root},
        "requirements": dict(requirements),
        "capabilities": dict(capabilities),
        "entrypoint_path": entrypoint_path,
        "target_root": target_root,
    }


def component_is_production(document: Mapping[str, Any], component_id: str) -> bool:
    """Return whether one component executes through the production batch driver."""

    components = document.get("components")
    component = next(
        (item for item in components if isinstance(item, Mapping) and item.get("id") == component_id),
        None,
    ) if isinstance(components, (list, tuple)) else None
    if component is None:
        raise ValueError(f"unknown component {component_id!r}")
    delivery = document.get("delivery_contracts")
    protocols = delivery.get("protocols", {}) if isinstance(delivery, Mapping) else {}
    protocol = protocols.get(component.get("protocol")) if isinstance(protocols, Mapping) else None
    return isinstance(protocol, Mapping) and protocol.get("production") is True


def downstream_source_contract_plans(
    document: Mapping[str, Any],
    component_id: str,
    *,
    family: str,
    implementation_sha256: str,
) -> list[tuple[str, dict[str, object]]]:
    """Production plans of the transitive latest-overlay descendants of one component.

    Their execution targets are shaped only by that component's accepted
    implementation patch, so their source contracts define what the patch must
    deliver.  Non-production descendants, and descendants whose plan does not
    build from the frozen contract, are skipped; those defects surface at the
    consumer's own execution instead.
    """

    components = document.get("components")
    if not isinstance(components, (list, tuple)):
        raise ValueError("research contract has no component catalog")
    by_id = {
        str(item["id"]): item
        for item in components
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    plans: list[tuple[str, dict[str, object]]] = []
    for item in components:
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
            continue
        other = str(item["id"])
        if other == component_id:
            continue
        result = item.get("result")
        if not isinstance(result, Mapping) or result.get("target_overlay_policy") != "latest":
            continue
        ancestors: set[str] = set()
        stack = [str(value) for value in item.get("dependencies", [])]
        while stack:
            dependency = stack.pop()
            if dependency in ancestors:
                continue
            ancestors.add(dependency)
            parent = by_id.get(dependency)
            stack.extend(
                str(value)
                for value in (parent.get("dependencies", []) if isinstance(parent, Mapping) else ())
            )
        if component_id not in ancestors:
            continue
        try:
            plan = component_execution_plan(
                document, other, method_family_id=family,
                implementation_sha256=implementation_sha256, device="cpu",
            )
        except ValueError:
            continue
        if plan.get("production") is True:
            plans.append((other, plan))
    return plans


def _batch_execution_plan(
    *,
    document: Mapping[str, Any],
    component: Mapping[str, Any],
    requirements: Mapping[str, Any],
    delivery_protocol: Mapping[str, Any],
    capabilities: Mapping[str, Any],
    target_root: str,
    entrypoint_path: str,
    module: str,
    method_family_id: str,
    implementation_sha256: str,
    device: str,
) -> dict[str, object]:
    """Compile one Host-owned batch without trusting the Agent workspace."""

    shape = delivery_protocol.get("execution_shape")
    if device not in {"cpu", "cuda"}:
        raise ValueError("production device must be cpu or cuda")
    if not isinstance(method_family_id, str) or not method_family_id:
        raise ValueError("production driver needs a method family")
    if not isinstance(implementation_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", implementation_sha256) is None:
        raise ValueError("production driver needs an implementation sha256")
    selections = [raw.get("selection") for raw in capabilities.values()
                  if isinstance(raw, Mapping) and isinstance(raw.get("selection"), Mapping)]
    if len(selections) != 1:
        raise ValueError("production delivery needs one frozen model selection")
    selection = selections[0]; expected = selection.get("expected_values")
    if not isinstance(expected, (list, tuple)) or len(expected) != 1 or not isinstance(expected[0], str):
        raise ValueError("production selection must freeze one treatment value")
    model_flag, treatment = selection.get("argv_flag"), expected[0]
    if not isinstance(model_flag, str) or not model_flag.startswith("--"):
        raise ValueError("production model selection needs an argv flag")
    control = selection.get("control_value")
    if shape == "paired_control_treatment" and (not isinstance(control, str) or not control):
        raise ValueError("paired delivery needs a frozen control selector")
    outputs = [raw for raw in capabilities.values() if isinstance(raw, Mapping)
               and raw.get("role") == "output" and isinstance(raw.get("normalizer"), Mapping)]
    if len(outputs) != 1:
        raise ValueError("batch delivery needs one frozen output normalizer")
    normalizer = dict(outputs[0]["normalizer"])
    metric_paths = normalizer.get("metric_paths")
    if (not isinstance(metric_paths, Mapping) or not metric_paths
            or any(not isinstance(key, str) or not key or not isinstance(value, str) or not value
                   for key, value in metric_paths.items())):
        raise ValueError("output normalizer needs metric_paths")
    candidate_resource = normalizer.get("candidate_manifest_resource")
    if not isinstance(candidate_resource, str) or not candidate_resource:
        raise ValueError("output normalizer needs candidate_manifest_resource")

    data_bindings: list[dict[str, str]] = []; fixed: dict[str, str] = {}
    for raw in requirements.get("argv_bindings", []):
        if not isinstance(raw, Mapping) or not isinstance(raw.get("flag"), str):
            raise ValueError("argv binding is invalid")
        flag = str(raw["flag"]); literal = raw.get("value")
        if literal is not None:
            if not isinstance(literal, (str, int, float)) or isinstance(literal, bool) or raw.get("path") is not None:
                raise ValueError("literal argv binding is invalid")
            fixed[flag] = str(literal); continue
        role, scope, path = raw.get("role"), raw.get("scope"), raw.get("path")
        if not isinstance(role, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", role) is None:
            raise ValueError("path argv binding needs a stable role")
        if scope == "target" and isinstance(path, str) and _safe_relative(path):
            source = str(PurePath(target_root) / path)
        elif scope == "absolute" and isinstance(path, str) and PurePath(path).is_absolute():
            source = path
        else:
            raise ValueError("argv binding has no frozen target or absolute path")
        if raw.get("absolute") is not True:
            raise ValueError("production argv bindings must be absolute")
        sandbox = f"/hypoloom-data/{role}"
        fixed[flag] = sandbox
        data_bindings.append({"flag": flag, "role": role, "scope": str(scope),
                              "path": str(path), "source": source, "sandbox": sandbox,
                              "kind": str(raw.get("kind", ""))})

    result = component.get("result"); dataset = result.get("dataset") if isinstance(result, Mapping) else None
    if not isinstance(dataset, str) or not dataset:
        raise ValueError("production batch needs a frozen dataset")
    seeds = component.get("seeds")
    if not isinstance(seeds, (list, tuple)):
        raise ValueError("production trials need frozen seeds")
    if shape == "paired_control_treatment" and len(seeds) != 3:
        raise ValueError("paired delivery needs exactly three frozen seeds")
    if shape == "seeded_trials" and len(seeds) != 5:
        raise ValueError("confirmation delivery needs exactly five frozen seeds")
    config_specs = [raw for raw in capabilities.values() if isinstance(raw, Mapping) and raw.get("path_template")]
    if shape == "checkpoint_audit":
        template = config_flag = None
        if config_specs: raise ValueError("checkpoint audit must load its frozen checkpoint configuration")
    else:
        if len(config_specs) != 1: raise ValueError("batch delivery needs one config path template")
        template, config_flag = config_specs[0].get("path_template"), config_specs[0].get("path_flag")
        if not isinstance(template, str) or not isinstance(config_flag, str):
            raise ValueError("production config path binding is invalid")
    required_flags: list[str] = []
    for source in (requirements.get("required_cli_flags", []),
                   *(raw.get("required_cli_flags", []) for raw in capabilities.values() if isinstance(raw, Mapping))):
        if not isinstance(source, (list, tuple)):
            raise ValueError("required CLI flags must be an array")
        for flag in source:
            if not isinstance(flag, str) or not flag.startswith("--"):
                raise ValueError("required CLI flag is invalid")
            if flag not in required_flags: required_flags.append(flag)
    for flag in fixed:
        if flag not in required_flags: required_flags.append(flag)
    for flag in ((config_flag,) if isinstance(config_flag, str) else ()) + ("--device",):
        if flag not in required_flags: required_flags.append(flag)

    input_specs = component.get("required_inputs", [])
    if not isinstance(input_specs, (list, tuple)):
        raise ValueError("production required_inputs are invalid")
    input_positions = {item.get("resource"): (index, int(item.get("min_count", 1)))
                       for index, item in enumerate(input_specs, 1) if isinstance(item, Mapping)}
    if candidate_resource not in input_positions:
        raise ValueError("candidate manifest resource is not a required input")
    _, candidate_count = input_positions[candidate_resource]
    if candidate_count != 1:
        raise ValueError("candidate manifest resource must resolve to one file")

    treatment_root = target_root
    treatment_python = str(PurePath(treatment_root) / "src") if entrypoint_path.startswith("src/") else treatment_root
    control_root = "/hypoloom-control-target"
    control_python = str(PurePath(control_root) / "src") if entrypoint_path.startswith("src/") else control_root
    trials: list[dict[str, object]] = []

    def config_path(model: str, root: str) -> str:
        if not isinstance(template, str): raise ValueError("trial has no config template")
        rendered = template.replace("{model}", model).replace("{dataset_lower}", dataset.lower())
        if not _safe_relative(rendered): raise ValueError("rendered config path is unsafe")
        return str(PurePath(root) / rendered)

    def trial(seed: object, arm: str | None, model: str, python_root: str, root: str,
              checkpoint_input: str | None = None) -> dict[str, object]:
        tag = f"{arm}.seed{seed}" if arm else f"trial.seed{seed}"
        raw_result = f"raw/{tag}.json"
        checkpoint = f"raw-checkpoints/{tag}.pt" if "--save-ckpt" in required_flags else checkpoint_input
        dynamic = {"--dataset": dataset, model_flag: model, "--seed": str(seed),
                   "--out": "{raw_result}",
                   "--save-ckpt": "{checkpoint}" if "--save-ckpt" in required_flags else None,
                   "--checkpoint": "{checkpoint}" if checkpoint_input is not None else None}
        if isinstance(config_flag, str): dynamic[config_flag] = config_path(model, root)
        dynamic["--device"] = device
        argv: list[str] = []
        for flag in required_flags:
            value = fixed.get(flag, dynamic.get(flag))
            if not isinstance(value, str) or not value:
                raise ValueError(f"Host cannot derive a frozen value for {flag}")
            argv.extend((flag, value))
        record: dict[str, object] = {"seed": seed, "model": model, "module": module,
            "pythonpath": python_root, "target_argv": argv, "raw_result": raw_result}
        if arm is not None: record["arm"] = arm
        if checkpoint is not None: record["checkpoint"] = checkpoint
        return record

    if shape == "paired_control_treatment":
        for seed in seeds:
            trials.extend((trial(seed, "control", str(control), control_python, control_root),
                           trial(seed, "treatment", treatment, treatment_python, treatment_root)))
    elif shape == "seeded_trials":
        trials.extend(trial(seed, None, treatment, treatment_python, treatment_root) for seed in seeds)
    elif shape == "checkpoint_audit":
        checkpoint_resource = normalizer.get("checkpoint_resource")
        if not isinstance(checkpoint_resource, str) or checkpoint_resource not in input_positions:
            raise ValueError("checkpoint audit needs a declared checkpoint resource")
        position, count = input_positions[checkpoint_resource]
        if count < 1: raise ValueError("checkpoint audit needs at least one checkpoint")
        trials.extend(trial(seed, None, treatment, treatment_python, treatment_root,
                            f"/hypoloom-inputs/{position}/{seed + 1}") for seed in range(count))
    else:
        raise ValueError("unsupported production execution_shape")
    checks = [_sandbox_data_check(raw, data_bindings) for raw in requirements.get("data_checks", [])]
    driver_plan = {"schema_version": 1, "shape": shape, "dataset": dataset,
        "identity": {"method_family_id": method_family_id, "implementation_sha256": implementation_sha256},
        "metric_paths": dict(metric_paths),
        "candidate_manifest_resource": candidate_resource,
        "data_checks": checks, "trials": trials,
        **{key: normalizer[key] for key in ("per_class_path", "class_labels", "audit_path", "audit_max",
                                             "audit_required_arms", "checkpoint_resource",
                                             "checkpoint_torch_load") if key in normalizer}}
    source = ".hypoloom/trial_driver.py"
    return {"production": True, "batch": True, "argv": ("python", source,
            json.dumps(driver_plan, sort_keys=True, separators=(",", ":"), allow_nan=False)),
        "environment": {}, "requirements": _plain_contract_value(requirements),
        "capabilities": _plain_contract_value(capabilities), "entrypoint_path": entrypoint_path,
        "target_root": target_root, "source_relative_path": source,
        "data_bindings": tuple(data_bindings), "control_target": shape == "paired_control_treatment",
        "driver_plan": driver_plan}


def _sandbox_data_check(raw: object, bindings: list[dict[str, str]]) -> dict[str, object]:
    if not isinstance(raw, Mapping) or raw.get("type") != "hdf5_split_manifest":
        raise ValueError("unsupported production data check")
    result = dict(raw)
    for field in ("cache", "manifest", "val_cache"):
        value = raw.get(field)
        if value is None: continue
        if not isinstance(value, str) or not _safe_relative(value):
            raise ValueError("data check path must be target-relative")
        matches = []
        for binding in bindings:
            path = PurePath(binding["path"])
            candidate = PurePath(value)
            try: suffix = candidate.relative_to(path)
            except ValueError: continue
            matches.append((len(path.parts), str(PurePath(binding["sandbox"]) / suffix)))
        if not matches: raise ValueError(f"data check {field} has no immutable argv binding")
        result[field] = max(matches)[1]
        if field == "val_cache": result["declared_val_cache"] = value.replace("\\", "/")
    return result


def component_execution_blockers(document: Mapping[str, Any]) -> tuple[ContractBlocker, ...]:
    """Report components whose frozen declarations cannot drive production."""

    components = document.get("components")
    if not isinstance(components, (list, tuple)):
        return (ContractBlocker("component_execution_unrepresentable", "research_contract.components", "components are absent"),)
    blockers: list[ContractBlocker] = []
    for index, component in enumerate(components):
        if not isinstance(component, Mapping) or not isinstance(component.get("id"), str):
            continue
        try:
            component_execution_plan(document, str(component["id"]))
        except ValueError as exc:
            blockers.append(ContractBlocker(
                "component_execution_unrepresentable",
                f"research_contract.components[{index}].protocol",
                f"{component['id']}: {exc}",
            ))
    return tuple(blockers)


def _safe_relative(value: str) -> bool:
    path = PurePath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and "." not in path.parts


__all__ = [
    "ComponentContractCompilation",
    "ContractBlocker",
    "component_execution_blockers",
    "component_execution_plan",
    "component_is_production",
    "compile_component_contract",
]
