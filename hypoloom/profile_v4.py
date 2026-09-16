"""Fidelity-first six-YAML product authoring, projected into GoalDefined once."""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping

from .profile_language import valid_language_tag
from .profile_v3 import ProfileV3Issue, ProfileV3Projection, project_profile_v3

PROFILE_V4_VERSION = 4


@dataclass(frozen=True, slots=True)
class ProfileV4Projection:
    projection: ProfileV3Projection
    notify: Mapping[str, Any]
    safety: Mapping[str, Any]
    settings: Mapping[str, Any]
    warnings: tuple[ProfileV3Issue, ...]
    issues: tuple[ProfileV3Issue, ...]


class _Validator:
    def __init__(self):
        self.issues: list[ProfileV3Issue] = []

    def error(self, location: str, message: str):
        self.issues.append(ProfileV3Issue("profile_v4_invalid_value", location, message))

    def object(self, value: object, fields: set[str], location: str) -> dict[str, Any]:
        if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
            self.error(location, "expected an object with text keys"); return {}
        for key in sorted(set(value) - fields): self.error(f"{location}.{key}", "unknown Profile v4 field")
        return copy.deepcopy(dict(value))

    def text(self, value: object, location: str) -> str:
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            self.error(location, "expected nonempty NUL-free text"); return "invalid"
        return value

    def number(self, value: object, location: str, *, integer: bool = False, zero: bool = False):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or value == 0 and not zero or integer and not isinstance(value, int):
            self.error(location, "expected a finite " + ("nonnegative" if zero else "positive") + (" integer" if integer else " number"))

    def boolean(self, value: object, location: str):
        if not isinstance(value, bool): self.error(location, "expected boolean")

    def strings(self, value: object, location: str, *, nonempty: bool = False) -> list[str]:
        if not isinstance(value, list) or nonempty and not value:
            self.error(location, "expected a list of text values"); return []
        for index, item in enumerate(value): self.text(item, f"{location}[{index}]")
        if any(not isinstance(item, str) for item in value): return []
        if len(set(value)) != len(value): self.error(location, "duplicate values are not permitted")
        return value


_GOAL = {"title", "description", "metric", "constraints", "stop_conditions", "completion", "evaluation"}
_EXPERT = {"name", "role", "persona", "focus", "model", "effort", "max_forks", "max_hypotheses_per_round", "stage_overrides"}
_DEFAULTS = {"model", "effort", "max_forks", "max_hypotheses_per_round"}
_METHOD = {"schema_version", "runtime", "budget", "literature", "inner_loop", "interaction", "graph_evolution", "memory", "output_correction"}
_SAFETY = {"allow_read_paths", "sandbox_os", "gpus", "max_gpus_per_experiment", "experiment_timeout_minutes", "per_expert_budget_usd", "total_budget_usd", "bash", "filesystem"}
_STAGES = {"grounding", "literature_review", "hypothesis_proposal", "hypothesis_proposal.forward",
    "hypothesis_proposal.backward", "hypothesis_proposal.synthesis", "inner_loop.code", "inner_loop.run",
    "inner_loop.analysis", "hypothesis_update", "cycle_report", "self_evolve", "observer", "debate", "leader"}


def _model(v: _Validator, value: Mapping[str, Any], location: str) -> None:
    if "model" in value:
        from .claude_agent_host import validate_claude_model_selector
        try:
            validate_claude_model_selector(value["model"])
        except (TypeError, ValueError) as exc:
            v.error(location + ".model", str(exc))
    if "effort" in value and (not isinstance(value["effort"], str) or value["effort"] not in {"low", "medium", "high", "xhigh", "max"}):
        v.error(location + ".effort", "effort must be low, medium, high, xhigh or max")
    for field in ("max_forks", "max_hypotheses_per_round"):
        if field in value: v.number(value[field], location + "." + field, integer=True)


def _methodology(v: _Validator, value: Mapping[str, Any]) -> None:
    definitions = {
        "literature": {"enabled", "tools", "max_sources", "credentials_file"},
        "inner_loop": {"max_iterations_per_hypothesis", "repair_attempts"},
        "graph_evolution": {"enabled", "stagnation_rounds"},
        "memory": {"max_transcript_bytes", "max_plugin_bytes", "max_memory_bytes", "retention"},
        "output_correction": {"max_failed_validations"},
    }
    for section, fields in definitions.items():
        if section not in value: continue
        selected = v.object(value[section], fields, "methodology.yaml." + section)
        for key, item in selected.items():
            location = f"methodology.yaml.{section}.{key}"
            if key == "enabled": v.boolean(item, location)
            elif key == "tools":
                if any(tool not in {"web_search", "web_fetch", "read_papers"} for tool in v.strings(item, location)):
                    v.error(location, "literature tools must be web_search, web_fetch or read_papers")
            elif key == "retention":
                if item not in {"retain", "archive_on_close"}: v.error(location, "retention must be retain or archive_on_close")
            elif key == "credentials_file":
                text = v.text(item, location)
                if not PurePosixPath(text).is_absolute() or "\x00" in text:
                    v.error(location, "expected an absolute credentials file path, never a secret literal")
            else: v.number(item, location, integer=True, zero=key == "repair_attempts")
    interaction = v.object(value.get("interaction", {}), {"leader", "debate", "observer"}, "methodology.yaml.interaction")
    for section, fields in {"leader": {"enabled", "check_after", "min_rounds_before_leader"},
            "debate": {"enabled", "rounds", "min_interval_seconds", "min_new_outcomes"},
            "observer": {"coalesce_reports"}}.items():
        if section not in interaction: continue
        selected = v.object(interaction[section], fields, "methodology.yaml.interaction." + section)
        for key, item in selected.items():
            location = f"methodology.yaml.interaction.{section}.{key}"
            if key in {"enabled", "coalesce_reports"}: v.boolean(item, location)
            elif key == "check_after": v.text(item, location)
            elif key == "rounds":
                if type(item) is not int or item != 3:
                    v.error(location, "Debate requires exactly three rounds")
            else: v.number(item, location, integer=True, zero=key in {"min_interval_seconds", "min_new_outcomes"})


def _safety(v: _Validator, value: Mapping[str, Any]) -> None:
    for key in ("max_gpus_per_experiment", "experiment_timeout_minutes", "per_expert_budget_usd", "total_budget_usd"):
        if key in value: v.number(value[key], "safety.yaml." + key, integer=key == "max_gpus_per_experiment")
    gpus = value.get("gpus", "auto")
    if gpus != "auto":
        if not isinstance(gpus, list) or any(not _gpu_selector(item) for item in gpus):
            v.error("safety.yaml.gpus", "expected auto or a GPU UUID/index list")
        elif len(set(gpus)) != len(gpus): v.error("safety.yaml.gpus", "duplicate GPU identifiers")
    v.strings(value.get("allow_read_paths", []), "safety.yaml.allow_read_paths")
    bash = v.object(value.get("bash", {}), {"deny_patterns", "network_allowed"}, "safety.yaml.bash")
    v.strings(bash.get("deny_patterns", []), "safety.yaml.bash.deny_patterns")
    for pattern in bash.get("deny_patterns", []) if isinstance(bash.get("deny_patterns", []), list) else []:
        if isinstance(pattern, str):
            try: re.compile(pattern)
            except re.error: v.error("safety.yaml.bash.deny_patterns", "expected valid regular expressions")
    if "network_allowed" in bash: v.boolean(bash["network_allowed"], "safety.yaml.bash.network_allowed")
    filesystem = v.object(value.get("filesystem", {}), {"write_only_within_worktree", "protected_paths"}, "safety.yaml.filesystem")
    v.strings(filesystem.get("protected_paths", []), "safety.yaml.filesystem.protected_paths")
    if "write_only_within_worktree" in filesystem: v.boolean(filesystem["write_only_within_worktree"], "safety.yaml.filesystem.write_only_within_worktree")


def _gpu_selector(value: object) -> bool:
    if type(value) is int: return value >= 0
    from .compute_resources import validate_gpu_uuid
    try: validate_gpu_uuid(value)
    except ValueError: return False
    return True


def restrict_profile_capabilities(contract: Mapping[str, Any], capabilities):
    """Narrow an already authorized inventory; never discover or grant devices.

    The existing request capability input captures index-to-UUID observations.
    A retry reuses that input and a preregistration keeps its UUID grant, rather
    than interpreting a device index again at execution or recovery time.
    """
    from .compute_resources import ComputeCapabilities
    product = contract.get("methodology", {}).get("controls", {}).get("product", {})
    selectors = product.get("safety", {}).get("gpus", "auto")
    if selectors == "auto": return capabilities
    if not isinstance(selectors, (list, tuple)) or any(not _gpu_selector(item) for item in selectors):
        raise ValueError("safety.yaml.gpus requires GPU UUIDs or nonnegative indices")
    selected = set()
    for selector in selectors:
        found = next((gpu for gpu in capabilities.gpus if
            (gpu.index == selector if type(selector) is int else gpu.uuid == selector)), None)
        if found is None: raise ValueError(f"safety.yaml.gpus: {selector!r} is not in the Host-authorized GPU inventory")
        if found.uuid in selected: raise ValueError("safety.yaml.gpus identifiers resolve to the same GPU")
        selected.add(found.uuid)
    return ComputeCapabilities(gpus=tuple(gpu for gpu in capabilities.gpus if gpu.uuid in selected),
        busy_gpu_uuids=tuple(uuid for uuid in capabilities.busy_gpu_uuids if uuid in selected))


def _notify(v: _Validator, value: Mapping[str, Any]) -> None:
    sinks = v.object(value.get("sinks", {}), {"log", "telegram", "dashboard", "webhook"}, "notify.yaml.sinks")
    fields = {"log": {"level", "path"}, "telegram": {"enabled", "bot_token_env", "chat_id_env", "credentials_file"},
        "dashboard": {"enabled", "host", "port"}, "webhook": {"enabled", "url_env", "token_env"}}
    for name, raw in sinks.items():
        if name not in fields: continue
        selected = v.object(raw, fields[name], f"notify.yaml.sinks.{name}")
        for field, item in selected.items():
            location = f"notify.yaml.sinks.{name}.{field}"
            if field == "enabled": v.boolean(item, location)
            elif field == "port":
                v.number(item, location, integer=True)
                if isinstance(item, int) and item > 65535: v.error(location, "port exceeds 65535")
            else:
                text = v.text(item, location)
                if field.endswith("_env") and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text): v.error(location, "expected an environment variable name, never a secret literal")
                if field == "credentials_file" and (not PurePosixPath(text).is_absolute() or "\x00" in text): v.error(location, "expected an absolute credentials file path, never a secret literal")
        if name == "telegram" and selected.get("credentials_file") is not None and (
                selected.get("bot_token_env") is not None or selected.get("chat_id_env") is not None):
            v.error(f"notify.yaml.sinks.{name}.credentials_file", "use exactly one of credentials_file or bot_token_env/chat_id_env")
    events = v.object(value.get("events", {}), {"log", "telegram", "dashboard", "webhook"}, "notify.yaml.events")
    for sink, names in events.items(): v.strings(names, "notify.yaml.events." + sink)


def project_profile_v4(*, goal: Mapping[str, Any], experts: Mapping[str, Any], methodology: Mapping[str, Any],
                       language: Mapping[str, Any], notify: Mapping[str, Any], safety: Mapping[str, Any]) -> ProfileV4Projection:
    v = _Validator()
    goal = v.object(goal, _GOAL, "goal.yaml")
    experts = v.object(experts, {"defaults", "grounding", "experts"}, "experts.yaml")
    methodology = v.object(methodology, _METHOD, "methodology.yaml")
    language = v.object(language, {"prompt_language", "report_language", "code_comment_language"}, "language.yaml")
    notify = v.object(notify, {"sinks", "events"}, "notify.yaml")
    safety = v.object(safety, _SAFETY, "safety.yaml")
    if methodology.get("schema_version") != 4: v.error("methodology.yaml.schema_version", "expected 4")
    title = v.text(goal.get("title"), "goal.yaml.title")
    description = v.text(goal.get("description"), "goal.yaml.description")
    constraints = v.strings(goal.get("constraints", []), "goal.yaml.constraints")
    stops = v.object(goal.get("stop_conditions", {}), {"max_rounds_per_expert", "max_wall_clock_hours", "target_metric"}, "goal.yaml.stop_conditions")
    for key, value in stops.items():
        if key != "target_metric": v.number(value, "goal.yaml.stop_conditions." + key, integer=key == "max_rounds_per_expert")
        elif value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            v.error("goal.yaml.stop_conditions.target_metric", "target must be finite or null")
    defaults = v.object(experts.get("defaults", {}), _DEFAULTS, "experts.yaml.defaults"); _model(v, defaults, "experts.yaml.defaults")
    if "grounding" in experts:
        _model(v, v.object(experts["grounding"], {"model", "effort"}, "experts.yaml.grounding"), "experts.yaml.grounding")
    raw_experts = experts.get("experts")
    if not isinstance(raw_experts, list) or not raw_experts:
        v.error("experts.yaml.experts", "expected one or more experts"); raw_experts = []
    roles = []
    for index, raw in enumerate(raw_experts):
        location = f"experts.yaml.experts[{index}]"
        expert = v.object(raw, _EXPERT, location)
        name = v.text(expert.get("name"), location + ".name")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name): v.error(location + ".name", "expert name must be a safe stable ID")
        role = v.text(expert.get("role", name), location + ".role")
        persona = v.text(expert.get("persona", role), location + ".persona")
        focus = v.strings(expert.get("focus", []), location + ".focus", nonempty=True)
        _model(v, expert, location)
        overrides = v.object(expert.get("stage_overrides", {}), _STAGES, location + ".stage_overrides")
        for stage, settings in overrides.items():
            _model(v, v.object(settings, {"model", "effort"}, location + ".stage_overrides." + stage), location + ".stage_overrides." + stage)
        roles.append({"id": name, "background": role + "\n\n" + persona, "reasoning_principles": focus})
    _methodology(v, methodology); _safety(v, safety); _notify(v, notify)
    for key, value in language.items():
        if not valid_language_tag(value): v.error("language.yaml." + key, "expected a language tag")
    prompt_language = language.get("prompt_language", "en")
    report_language = language.get("report_language", prompt_language)
    metric = goal.get("metric")
    if "evaluation" in goal and goal["evaluation"] != "grounding":
        v.error("goal.yaml.evaluation", "expected grounding")
    if goal.get("evaluation") == "grounding" and (metric is not None or "completion" in goal or stops.get("target_metric") is not None):
        v.error("goal.yaml.evaluation", "Grounding commission cannot also declare metric/completion authority")
    if metric is not None:
        metric = v.object(metric, {"name", "direction", "target"}, "goal.yaml.metric")
        v.text(metric.get("name"), "goal.yaml.metric.name")
        if metric.get("direction") not in {"maximize", "minimize", "MAXIMIZE", "MINIMIZE"}: v.error("goal.yaml.metric.direction", "expected maximize or minimize")
    metric_target = None if metric is None else metric.get("target")
    stop_target = stops.get("target_metric")
    if stop_target is not None and metric is None:
        v.error("goal.yaml.stop_conditions.target_metric", "a target requires goal.metric.name and goal.metric.direction")
    if metric_target is not None and stop_target is not None and metric_target != stop_target:
        v.error("goal.yaml.stop_conditions.target_metric", "target conflicts with goal.metric.target")
    result: dict[str, Any] = {"kind": "metric", "deferred": True}
    target = metric_target if metric_target is not None else stop_target
    if metric is not None:
        result.update(metric=metric.get("name"), direction=metric.get("direction"))
        if target is not None:
            if isinstance(target, bool) or not isinstance(target, (int, float)) or not math.isfinite(target): v.error("goal.yaml.metric.target", "target must be finite")
            result.pop("deferred"); result["target"] = target
    completion = goal.get("completion")
    if completion is not None and metric is not None: v.error("goal.yaml", "choose explicit completion signals or one shared metric, not two scientific declarations")
    if completion is None:
        completion = {"rule": "required_conjunction", "signals": [{"id": role["id"] + "_goal", "tier": "primary",
            "description": description, "protocol": "expert-measurement", "result": copy.deepcopy(result), "seeds": [], "evidence": {"artifacts": [], "inputs": []}}
            for role in roles], "evaluation_protocols": {"expert-measurement": {"result_kind": "metric", "requirements": {
                "required_metrics": [] if result.get("deferred") else [result["metric"]], "required_artifacts": [], "artifact_validators": []}}}, "delivery_contracts": {}}
    from .agent_aci import DEFAULT_REAL_AGENT_BUDGET
    budget = copy.deepcopy(methodology.get("budget", {}))
    if not isinstance(budget, dict): v.error("methodology.yaml.budget", "expected budget object"); budget = {}
    budget.setdefault("agent_invocation", DEFAULT_REAL_AGENT_BUDGET.to_dict())
    budget.setdefault("max_experiments", max(1, len(roles)) * int(stops.get("max_rounds_per_expert", 10) if isinstance(stops.get("max_rounds_per_expert", 10), int) else 10) * 5)
    budget.setdefault("max_active_hypotheses", max(1, len(roles)))
    fork_settings = [{**defaults, **item} for item in raw_experts if isinstance(item, Mapping)]
    fork_limits = [item.get("max_forks", item.get("max_hypotheses_per_round", 1)) for item in fork_settings]
    default_effect_capacity = max(1, sum(value if type(value) is int and value > 0 else 1 for value in fork_limits))
    for field in ("max_active_agents", "max_active_processes", "max_active_reviews"):
        budget.setdefault(field, default_effect_capacity)
    for field in ("total_budget_usd", "per_expert_budget_usd"):
        if field in safety: budget[field] = safety[field]
    if "max_wall_clock_hours" in stops: budget["wallclock_hours"] = stops["max_wall_clock_hours"]
    if "experiment_timeout_minutes" in safety and isinstance(safety["experiment_timeout_minutes"], (int, float)):
        budget["experiment_timeout"] = int(safety["experiment_timeout_minutes"] * 60)
    role_budgets = budget.setdefault("roles", {})
    max_sources = methodology.get("literature", {}).get("max_sources", 8) if isinstance(methodology.get("literature", {}), dict) else 8
    if isinstance(role_budgets, dict):
        for role in roles: role_budgets.setdefault(role["id"], {"max_papers": max_sources})
    legacy = project_profile_v3(goal={"topic": title, "objective": description, "context": "\n".join(constraints), "completion": completion},
        experts={"roles": roles}, methodology={"schema_version": 3, "mode": {"research_loop": "expert-six-node/1", "domain_grounding": "required",
            "experiment_isolation": "git_authoring_worktree", "observer": "disabled", "component_ownership": "balanced"},
            "runtime": copy.deepcopy(methodology.get("runtime", {})), "budget": budget},
        language={"default": prompt_language, "overrides": {"cycle_report": report_language}})
    settings = {"profile_version": 4, "goal": {"constraints": constraints, "stop_conditions": {key: value for key, value in stops.items() if key != "target_metric"},
        "completion_mode": "explicit_signals" if "completion" in goal else "shared_goal",
        **({"evaluation": "grounding"} if goal.get("evaluation") == "grounding" else {})}, "experts": experts,
        "methodology": {key: value for key, value in methodology.items() if key not in {"schema_version", "runtime", "budget"}},
        "language": language, "notify": notify, "safety": {key: value for key, value in safety.items() if key not in {"allow_read_paths", "sandbox_os", "experiment_timeout_minutes"}}}
    warnings = _inactive_warnings(settings) if not v.issues else ()
    legacy_notify = {"channels": [], "digest": False}
    legacy_safety = {"allow_read_paths": safety.get("allow_read_paths", []), "sandbox_os": safety.get("sandbox_os", "linux_bwrap")}
    return ProfileV4Projection(legacy, legacy_notify, legacy_safety, settings, warnings, tuple(v.issues) + legacy.issues)


def _inactive_warnings(settings: Mapping[str, Any]) -> tuple[ProfileV3Issue, ...]:
    """Name only requested semantics that the current consumer does not apply."""
    warnings = []
    notify = settings["notify"]
    if (settings["goal"].get("evaluation") == "grounding"
            and notify.get("sinks", {}).get("telegram", {}).get("enabled", False)
            and "grounding_question" not in notify.get("events", {}).get("telegram", [])):
        warnings.append(ProfileV3Issue("inactive_feature", "notify.yaml.events.telegram",
            "Grounding may wait for a user answer, but Telegram is enabled without grounding_question; add it to notify.yaml.events.telegram to receive pending-question notifications."))
    for index, expert in enumerate(settings["experts"].get("experts", ())):
        if "grounding" in expert.get("stage_overrides", {}):
            warnings.append(ProfileV3Issue("inactive_feature", f"experts.yaml.experts[{index}].stage_overrides.grounding",
                "Grounding is role-neutral and precedes expert rounds; this personal override is retained but not applied. Move shared model/effort to experts.yaml.grounding; omitted fields inherit product defaults."))
    trigger = settings["methodology"].get("interaction", {}).get("leader", {}).get("check_after", "expert_round")
    if trigger not in {"expert_round", "cycle_report"}:
        warnings.append(ProfileV3Issue("inactive_feature", "methodology.yaml.interaction.leader.check_after",
            f"Requested {trigger!r} is retained but not applied; Leader checks follow expert_round closure (cycle_report alias), after successful CYCLE_REPORT and successful or formally exhausted SELF_EVOLVE."))
    if settings["safety"].get("filesystem", {}).get("write_only_within_worktree") is False:
        warnings.append(ProfileV3Issue("stricter_sandbox", "safety.yaml.filesystem.write_only_within_worktree",
            "False is retained but does not grant additional writable paths; the stricter per-node sandbox remains active."))
    return tuple(warnings)


def resolve_node_runtime(contract: Mapping[str, Any], role: str | None, route: str, *, designated_leader: bool = False,
                         observer_source_role: str | None = None) -> dict[str, Any]:
    """Materialize the admitted per-node controls once into its existing input."""
    if designated_leader and route != "hypothesis_proposal.synthesis":
        raise ValueError("Leader stage overrides require the designated SYNTHESIS task")
    if observer_source_role is not None and (route != "observer" or role is not None):
        raise ValueError("Observer execution overrides require a role-neutral Observer task")
    product = contract.get("methodology", {}).get("controls", {}).get("product")
    if not isinstance(product, Mapping): return {}
    roster = product["experts"]
    expert = next((item for item in roster.get("experts", ()) if item["name"] == role and route != "grounding"), {})
    selected = {key: value for key, value in roster.get("defaults", {}).items() if key in {"model", "effort"}}
    selected.update({key: value for key, value in expert.items() if key in {"model", "effort"}})
    stage = {"experiment_design_implement": "inner_loop.code", "experiment_execution": "inner_loop.run",
             "result_analysis": "inner_loop.analysis", "literature_review.query_plan": "literature_review",
             "literature_review.main": "literature_review"}.get(route, route)
    overrides = expert.get("stage_overrides", {})
    for key in (stage.split(".")[0], stage): selected.update(overrides.get(key, {}))
    if route == "grounding": selected.update(roster.get("grounding", {}))
    if designated_leader: selected.update(overrides.get("leader", {}))
    if observer_source_role is not None:
        source = next((item for item in roster.get("experts", ()) if item["name"] == observer_source_role), {})
        selected.update({key: value for key, value in source.get("stage_overrides", {}).get("observer", {}).items()
            if key in {"model", "effort"}})
    comments = product["language"].get("code_comment_language")
    if comments is not None: selected["code_comment_language"] = comments
    if "memory" in product["methodology"]: selected["memory"] = copy.deepcopy(product["methodology"]["memory"])
    if "literature" in product["methodology"]: selected["literature"] = copy.deepcopy(product["methodology"]["literature"])
    if "output_correction" in product["methodology"]: selected["output_correction"] = copy.deepcopy(product["methodology"]["output_correction"])
    if any(key in product["safety"] for key in ("filesystem", "bash")):
        selected["safety"] = {key: copy.deepcopy(product["safety"][key]) for key in ("filesystem", "bash") if key in product["safety"]}
    return selected


def request_node_runtime(inputs) -> dict[str, Any]:
    """Read materialized controls from the replayed request, never a live profile."""
    configs = [item for item in inputs if item.ref.endswith(("--expert-config", "--grounding-config"))]
    if not configs: return {}
    if len(configs) != 1: raise ValueError("node runtime requires one configuration input")
    value = json.loads(configs[0].content_bytes()).get("node_runtime", {})
    if not isinstance(value, dict): raise ValueError("node runtime must be an object")
    return value


def request_literature_tools(request) -> tuple[str, ...]:
    """Resolve the frozen brokered web subset for literature or Debate."""
    from .agent_aci import supports_literature_web
    from .expert_loop import AuxiliaryRequestKind, AuxiliaryTask, ResearchNodeTask, ResearchNodeKind
    if not supports_literature_web(request.schema_version): return ()
    task = request.task_context
    literature = (request.command.value == "LITERATURE_REVIEW" and isinstance(task, ResearchNodeTask)
        and task.key.node_kind is ResearchNodeKind.LITERATURE_REVIEW)
    debate = (request.command.value == "DEBATE" and isinstance(task, AuxiliaryTask)
        and task.kind is AuxiliaryRequestKind.DEBATE)
    if not (literature or debate): return ()
    settings = request_node_runtime(request.input_artifacts).get("literature", {})
    if not isinstance(settings, Mapping): raise ValueError("frozen literature settings must be an object")
    if type(settings.get("enabled", True)) is not bool: raise ValueError("frozen literature enabled must be boolean")
    if not settings.get("enabled", True): return ()
    selected = settings.get("tools", ["web_search", "web_fetch"])
    if (not isinstance(selected, (list, tuple)) or any(not isinstance(name, str) or name not in {"web_search", "web_fetch", "read_papers"} for name in selected)
            or len(selected) != len(set(selected))): raise ValueError("frozen literature tools are invalid")
    available = ("web_search", "web_fetch")
    if literature and request.schema_version >= 20 and task.key.subkind.value == "MAIN": available += ("read_papers",)
    return tuple(name for name in available if name in selected)


def validate_product_settings(value: object) -> dict[str, Any]:
    """Validate the admitted product controls even on a direct CREATE_RUN."""
    if not isinstance(value, dict) or set(value) != {"profile_version", "goal", "experts", "methodology", "language", "notify", "safety"} or value["profile_version"] != 4:
        raise ValueError("invalid Profile v4 product settings")
    goal = value["goal"]
    if not isinstance(goal, dict) or set(goal) != {"constraints", "stop_conditions", "completion_mode"} | ({"evaluation"} if "evaluation" in goal else set()) or goal["completion_mode"] not in {"shared_goal", "explicit_signals"}:
        raise ValueError("invalid Profile v4 goal controls")
    if "evaluation" in goal and (goal["evaluation"] != "grounding" or goal["completion_mode"] != "shared_goal"):
        raise ValueError("Grounding evaluation requires shared_goal controls")
    if not isinstance(value["methodology"], dict) or set(value["methodology"]) - (_METHOD - {"schema_version", "runtime", "budget"}):
        raise ValueError("invalid Profile v4 methodology controls")
    projected = project_profile_v4(goal={"title": "Admitted product controls", "description": "Validate controls without redefining scientific components.",
        "constraints": goal["constraints"], "stop_conditions": goal["stop_conditions"],
        **({"evaluation": goal["evaluation"]} if "evaluation" in goal else {})}, experts=value["experts"],
        methodology={"schema_version": 4, **value["methodology"]}, language=value["language"], notify=value["notify"], safety=value["safety"])
    if projected.issues: raise ValueError("invalid Profile v4 controls: " + "; ".join(issue.location + ": " + issue.message for issue in projected.issues))
    return copy.deepcopy(value)
