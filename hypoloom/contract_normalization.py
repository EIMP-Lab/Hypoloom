"""Compatibility normalization for profile-owned research contracts."""

from __future__ import annotations

from typing import Any

from .profile_language import validate_profile_language_policy


EXPERIMENT_ISOLATION_ATTEMPT_OVERLAY = "attempt_overlay"
EXPERIMENT_ISOLATION_GIT_AUTHORING_WORKTREE = "git_authoring_worktree"
EXPERT_LOOP_PROFILE_VERSION = 2
EXPERT_LOOP_RESEARCH_LOOP = "expert-six-node/1"


def normalize_experiment_isolation(value: object, field_name: str) -> str:
    """Return the one Hypoloom isolation meaning, accepting the legacy spelling."""

    if not isinstance(value, str) or value not in {
        EXPERIMENT_ISOLATION_ATTEMPT_OVERLAY,
        EXPERIMENT_ISOLATION_GIT_AUTHORING_WORKTREE,
        "worktree",
    }:
        raise ValueError(
            f"{field_name} must be {EXPERIMENT_ISOLATION_ATTEMPT_OVERLAY} or "
            f"{EXPERIMENT_ISOLATION_GIT_AUTHORING_WORKTREE} "
            "(legacy worktree is accepted as attempt_overlay compatibility input)"
        )
    return (
        EXPERIMENT_ISOLATION_ATTEMPT_OVERLAY
        if value == "worktree"
        else value
    )


def normalize_research_contract_experiment_isolation(
    document: dict[str, Any],
) -> None:
    """Canonicalize the optional isolation control in a new contract value."""

    methodology = document.get("methodology")
    if not isinstance(methodology, dict):
        return
    controls = methodology.get("controls")
    if not isinstance(controls, dict) or "experiment_isolation" not in controls:
        return
    controls["experiment_isolation"] = normalize_experiment_isolation(
        controls["experiment_isolation"],
        "research_contract.methodology.controls.experiment_isolation",
    )


def normalize_research_contract_language(document: dict[str, Any]) -> None:
    """Validate and canonicalize the optional typed language route control."""

    goal = document.get("goal")
    methodology = document.get("methodology")
    if not isinstance(goal, dict) or not isinstance(methodology, dict):
        return
    controls = methodology.get("controls")
    if not isinstance(controls, dict):
        return
    if "product" in controls:
        from .profile_v4 import validate_product_settings
        controls["product"] = validate_product_settings(controls["product"])
    if "language_routes" not in controls:
        return
    try:
        overrides = validate_profile_language_policy(
            goal.get("language"), controls["language_routes"]
        )
    except ValueError as exc:
        raise ValueError(
            "research_contract.methodology.controls.language_routes is invalid"
        ) from exc
    controls["language_routes"] = {"overrides": overrides}
