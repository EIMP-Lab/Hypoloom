"""Manual, non-authoritative research views derived from the offline monitor.

The export reads the source through its existing read-only StateStore boundary.
Only the explicit, new destination directory is written.  It is not a backup,
an Agent input, or a second source of workflow or scientific authority.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .agent_aci import (
    AgentACIError,
    AgentRequest,
    AgentResponse,
    ExpertLoopCandidate,
    validate_postflight,
)
from .artifacts import _SAFE_ID
from .domain import AgentCommand
from .monitor import HypothesisView, MonitorSnapshot, build_monitor_snapshot
from .state_store import StateStore, StateStoreError, StateTransaction


class ResearchExportError(RuntimeError):
    """An offline research view cannot be safely read or written."""


def _frontmatter(fields: Mapping[str, Any]) -> str:
    # JSON values are valid YAML scalars/collections and quote arbitrary text.
    return "---\n" + "".join(
        f"{key}: {json.dumps(value, ensure_ascii=False)}\n"
        for key, value in fields.items()
    ) + "---\n\n"


def _background_markdown(
    snapshot: MonitorSnapshot, attempts: Sequence[Mapping[str, Any]],
) -> str:
    sections = [
        _frontmatter({
            "run_id": snapshot.run_id,
            "observed_at": snapshot.observed_at,
            "run_revision": snapshot.status.get("run_revision"),
        }),
        "# Research background\n\n",
        "Offline polling observation: Grounding and the durable monitor fields "
        "share one SQLite read transaction. The Oracle is a separate read-only "
        "observation. This export is not workflow authority or proof of liveness.\n\n",
    ]
    completed = 0
    for row in attempts:
        if (
            row["run_id"] != snapshot.run_id or row["kind"] != "AGENT"
            or row["state"] != "CLOSED" or row["terminal_reason"] != "SUCCEEDED"
        ):
            continue
        observation = json.loads(row["observation_json"])
        if not isinstance(observation, Mapping):
            raise ResearchExportError(f"Agent Attempt {row['attempt_id']!r} has no observation object")
        raw_request = observation.get("request")
        if not isinstance(raw_request, Mapping) or raw_request.get("command") != "GROUNDING":
            continue
        try:
            request = AgentRequest.from_dict(raw_request)
            response = AgentResponse.from_dict(observation["response"])
            candidate = validate_postflight(request, response).candidate
            if (
                request.command is not AgentCommand.GROUNDING
                or request.run_id != snapshot.run_id
                or request.attempt_id != row["attempt_id"]
                or request.owner_workflow_id != row["workflow_id"]
                or request.command_id != row["command_id"]
                or request.attempt_ordinal != row["attempt_ordinal"]
                or not isinstance(candidate, ExpertLoopCandidate)
            ):
                raise ValueError("Grounding request does not match its stored Attempt")
        except (AgentACIError, KeyError, TypeError, ValueError) as exc:
            raise ResearchExportError(
                f"cannot render successful Grounding Attempt {row['attempt_id']!r}: {exc}"
            ) from exc
        if getattr(candidate, "clarification", None) is not None:
            sections.append("This Grounding Attempt delivered a clarification, not completed research background.\n\n")
            continue
        completed += 1
        from .grounding_background import GroundingBackground, render_background_markdown
        background = getattr(candidate, "background", None)
        rendered = render_background_markdown(background, web_sources=candidate.web_sources) if isinstance(background, GroundingBackground) else candidate.summary
        sections.extend((
            f"## Successful GROUNDING Attempt {completed}\n\n",
            "```json\n" + json.dumps({
                "run_id": request.run_id,
                "attempt_id": request.attempt_id,
                "workflow_id": request.owner_workflow_id,
                "command_id": request.command_id,
                "response_ref": f"{request.attempt_id}--response",
                "supporting_refs": list(candidate.supporting_refs),
            }, ensure_ascii=False, sort_keys=True, indent=2) + "\n```\n\n",
            rendered + "\n\n",
        ))
    if not completed:
        sections.append(
            "Grounding is incomplete or unavailable in this observation: "
            "no successful GROUNDING Attempt is recorded.\n"
        )
    return "".join(sections)


def hypothesis_markdown(hypothesis: HypothesisView, details: Mapping[str, Any] | None = None) -> str:
    fields = {**asdict(hypothesis), **(details or {})}
    claim = fields.pop("claim")
    return (
        _frontmatter(fields)
        + f"# Hypothesis {hypothesis.hypothesis_id}\n\n{claim}\n"
    )


def research_view_files(
    snapshot: MonitorSnapshot, attempts: Sequence[Mapping[str, Any]],
    transitions: Sequence[Mapping[str, Any]] = (),
) -> dict[str, bytes]:
    """Render the shared, non-authoritative human view without filesystem writes."""
    files = {
        "run.json": (snapshot.to_json(indent=2) + "\n").encode(),
        "background.md": _background_markdown(snapshot, attempts).encode(),
    }
    from .final_report import accepted_report_view
    try:
        if report := accepted_report_view(snapshot.run_id, attempts):
            files["FINAL_REPORT.md"] = report["markdown"].encode("utf-8")
    except (KeyError, TypeError, ValueError) as exc:
        raise ResearchExportError(f"cannot render successful final report: {exc}") from exc
    from .observation_api import product_graph
    graph = product_graph([asdict(item) for item in snapshot.hypotheses], transitions)
    details = {item["hypothesis_id"]: item for item in graph["nodes"]}
    for hypothesis in snapshot.hypotheses:
        filename = f"{hypothesis.hypothesis_id}.md"
        if _SAFE_ID.fullmatch(filename) is None:
            raise ResearchExportError(
                f"hypothesis ID is not a safe export filename: {hypothesis.hypothesis_id!r}"
            )
        files[f"graph/nodes/{filename}"] = hypothesis_markdown(hypothesis, details[hypothesis.hypothesis_id]).encode()
    edges = [json.dumps({"parent_hypothesis_id": edge["source"], "hypothesis_id": edge["target"],
        **{name: value for name, value in edge.items() if name not in {"source", "target"}}},
        ensure_ascii=False, sort_keys=True) + "\n" for edge in graph["edges"]]
    files["graph/edges.jsonl"] = "".join(edges).encode()
    return files


def export_research_view(
    state_root: str | Path, run_id: str, destination: str | Path,
) -> tuple[Path, int]:
    """Write one polling observation into a new, disjoint directory.

    Render before creating the destination so source/identity errors leave no
    output. An I/O failure after creation may leave an explicitly reported
    partial directory; it is never reused or overwritten by a subsequent call.
    """

    output: Path | None = None
    created = False
    try:
        root = Path(state_root).expanduser().resolve()
        requested = Path(destination).expanduser()
        output = requested.resolve()
        if output == root or output.is_relative_to(root) or root.is_relative_to(output):
            raise ResearchExportError("export directory and source state root must be disjoint")
        if requested.exists() or requested.is_symlink() or output.exists():
            raise ResearchExportError(f"export directory must be new: {requested}")

        with StateStore(root).readonly_connection() as connection:
            connection.execute("BEGIN")
            snapshot = build_monitor_snapshot(root, run_id, _connection=connection)
            attempts = StateTransaction(connection).list_attempts()
            transitions = [dict(row) for row in connection.execute(
                "SELECT t.*, c.command_type FROM workflow_transitions t JOIN workflow_commands c USING(command_id) "
                "WHERE t.run_id=? ORDER BY t.rowid", (run_id,))]
            files = research_view_files(snapshot, attempts, transitions)

        output.mkdir(mode=0o700)
        created = True
        (output / "graph").mkdir(mode=0o700)
        (output / "graph/nodes").mkdir(mode=0o700)
        for relative, content in files.items():
            with (output / relative).open("x", encoding="utf-8") as stream:
                stream.write(content.decode("utf-8"))
        return output, len(files)
    except ResearchExportError:
        raise
    except (OSError, sqlite3.Error, StateStoreError, TypeError, ValueError) as exc:
        partial = f"; partial export remains at {output}" if created else ""
        raise ResearchExportError(f"cannot export research view: {exc}{partial}") from exc


__all__ = ["ResearchExportError", "export_research_view"]
