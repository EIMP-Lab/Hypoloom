"""Typed profile-language routing over the existing research contract."""

from __future__ import annotations

import re
from typing import Any, Mapping

from .expert_loop import (
    AuxiliaryRequestKind,
    AuxiliaryTask,
    ResearchNodeKind,
    ResearchNodeTask,
)


PROFILE_LANGUAGE_ROUTES = (
    "grounding",
    "literature_review.query_plan",
    "literature_review.main",
    "hypothesis_proposal.forward",
    "hypothesis_proposal.backward",
    "hypothesis_proposal.synthesis",
    "experiment_design_implement",
    "experiment_execution",
    "result_analysis",
    "hypothesis_update",
    "cycle_report",
)
_LANGUAGE_TAG = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*\Z")


def valid_language_tag(value: object) -> bool:
    return isinstance(value, str) and _LANGUAGE_TAG.fullmatch(value) is not None


def validate_profile_language_policy(
    default_language: object,
    policy: object,
) -> dict[str, str]:
    """Validate and normalize the one durable v3 language control."""

    if not valid_language_tag(default_language):
        raise ValueError("profile default language is invalid")
    if not isinstance(policy, Mapping) or set(policy) != {"overrides"}:
        raise ValueError("profile language policy has an invalid shape")
    overrides = policy["overrides"]
    if not isinstance(overrides, Mapping):
        raise ValueError("profile language policy has invalid values")
    if any(key not in PROFILE_LANGUAGE_ROUTES for key in overrides):
        raise ValueError("profile language policy has an unknown route")
    if any(not valid_language_tag(value) for value in overrides.values()):
        raise ValueError("profile language override has an invalid language")
    return {
        route: str(overrides[route])
        for route in PROFILE_LANGUAGE_ROUTES
        if route in overrides
    }


def resolve_profile_language(
    default_language: object,
    policy: object,
    route: str,
) -> dict[str, object] | None:
    """Resolve one Agent route only when the typed v3 control is present."""

    if policy is None:
        return None
    if route not in PROFILE_LANGUAGE_ROUTES:
        raise ValueError(f"unknown profile language route {route!r}")
    overrides = validate_profile_language_policy(default_language, policy)
    return {
        "route": route,
        "output_language": overrides.get(route, default_language),
    }


def apply_profile_language(
    config: dict[str, Any],
    contract: Mapping[str, Any],
    task: ResearchNodeTask | AuxiliaryTask,
    *,
    designated_leader: bool = False,
    observer_source_role: str | None = None,
) -> None:
    """Project the current typed route into one Agent task configuration."""

    if isinstance(task, AuxiliaryTask):
        route = (
            "grounding"
            if task.kind is AuxiliaryRequestKind.GROUNDING
            else "literature_review.query_plan"
            if task.kind is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN
            else {AuxiliaryRequestKind.GRAPH_EVOLVE: "graph_evolve", AuxiliaryRequestKind.OBSERVER: "observer",
                  AuxiliaryRequestKind.DEBATE: "debate", AuxiliaryRequestKind.LEADER: "leader",
                  AuxiliaryRequestKind.DEBATE_JUDGE: "debate_judge",
                  AuxiliaryRequestKind.FINAL_REPORT: "final_report"}.get(task.kind)
        )
    elif task.key.node_kind is ResearchNodeKind.LITERATURE_REVIEW:
        route = f"literature_review.{task.key.subkind.value.lower()}"
    elif task.key.node_kind is ResearchNodeKind.HYPOTHESIS_PROPOSAL:
        route = f"hypothesis_proposal.{task.key.subkind.value.lower()}"
    else:
        route = {
            ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT: "experiment_design_implement",
            ResearchNodeKind.EXPERIMENT_EXECUTION: "experiment_execution",
            ResearchNodeKind.RESULT_ANALYSIS: "result_analysis",
            ResearchNodeKind.HYPOTHESIS_UPDATE: "hypothesis_update",
            ResearchNodeKind.CYCLE_REPORT: "cycle_report",
            ResearchNodeKind.ADJUDICATE: "adjudicate",
            ResearchNodeKind.SELF_EVOLVE: "self_evolve",
        }.get(task.key.node_kind)
    if route is None:
        return
    from .profile_v4 import resolve_node_runtime
    # Shared Step-1 planning retains its legacy language route, while model/
    # effort belong to Grounding rather than an expert's literature stage.
    runtime_route = "grounding" if (isinstance(task, AuxiliaryTask)
        and task.kind is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN) else route
    runtime = resolve_node_runtime(contract, task.key.expert_slot if isinstance(task, ResearchNodeTask) else task.expert_slot,
        runtime_route, designated_leader=designated_leader, observer_source_role=observer_source_role)
    if runtime: config["node_runtime"] = runtime
    if route not in PROFILE_LANGUAGE_ROUTES:
        product = contract["methodology"].get("controls", {}).get("product")
        if isinstance(product, Mapping):
            field = "report_language" if route == "final_report" else "prompt_language"
            config["language_route"] = {"route": route, "output_language": product["language"].get(field, contract["goal"].get("language", "en"))}
        elif contract["methodology"].get("controls", {}).get("language_routes") is not None:
            validate_profile_language_policy(contract["goal"].get("language"), contract["methodology"]["controls"]["language_routes"])
            config["language_route"] = {"route": route, "output_language": contract["goal"]["language"]}
        return
    resolved = resolve_profile_language(
        contract["goal"].get("language"),
        contract["methodology"].get("controls", {}).get("language_routes"),
        route,
    )
    if resolved is not None:
        config["language_route"] = resolved


__all__ = [
    "PROFILE_LANGUAGE_ROUTES",
    "apply_profile_language",
    "resolve_profile_language",
    "validate_profile_language_policy",
    "valid_language_tag",
]
