"""Render an applicable method from the request's existing bound plugin view."""
from __future__ import annotations

import json
from pathlib import Path

from .agent_aci import AgentRequest, canonical_json
from .domain import AgentCommand


_PERFORMANCE_STAGES = frozenset({
    AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
    AgentCommand.EXPERIMENT_EXECUTION,
    AgentCommand.RESULT_ANALYSIS,
})
_PERFORMANCE_PATH = "skills/research-workflow/references/performance.md"
MAX_GUIDANCE_BYTES = 16 * 1024


def performance_guidance(request: AgentRequest, plugin_root: Path | None, *,
                         max_bytes: int = MAX_GUIDANCE_BYTES) -> dict | None:
    """Use only the validated, read-only plugin view; never backfill old versions.

    This is a prompt projection, not a Skill-call receipt or performance evidence.
    Large references stay readable at their existing mount, without partial text.
    """
    if plugin_root is None or request.command not in _PERFORMANCE_STAGES:
        return None
    resource = next((item for item in request.input_artifacts
        if item.ref.endswith("--expert-resources")), None)
    if resource is None:
        return None
    plugin = json.loads(resource.content_bytes())["plugin"]
    limit = min(MAX_GUIDANCE_BYTES, max_bytes)
    value = {"path": "/expert-plugin/" + _PERFORMANCE_PATH,
             "plugin_commit": plugin["commit"], "status": "unavailable"}

    if len(canonical_json(value)) > limit:
        return None
    try:
        with (plugin_root / _PERFORMANCE_PATH).open("rb") as handle:
            content = handle.read(limit + 1)
    except FileNotFoundError:
        return value
    value["status"] = "read_from_path"
    if len(content) <= limit:
        inline = {**value, "status": "inline", "content": content.decode("utf-8")}
        if len(canonical_json(inline)) <= limit:
            return inline
    return value if len(canonical_json(value)) <= limit else None
