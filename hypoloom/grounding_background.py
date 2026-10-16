"""Structured Grounding advice and its derived Markdown, not scientific facts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Collection, Mapping, Sequence

from .literature import LiteratureError, _uri_parts


class GroundingBackgroundError(ValueError):
    pass


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise GroundingBackgroundError(f"{name} must be nonempty NUL-free text")
    return value


def _texts(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise GroundingBackgroundError(f"{name} must be an array of strings")
    return tuple(_text(item, name) for item in value)


@dataclass(frozen=True, slots=True)
class CodebaseEntry:
    path: str
    purpose: str

    def __post_init__(self) -> None:
        _text(self.path, "codebase path")
        _text(self.purpose, "codebase purpose")


@dataclass(frozen=True, slots=True)
class GroundingBackground:
    project_summary: str
    codebase_map: tuple[CodebaseEntry, ...]
    entrypoints: tuple[str, ...]
    how_to_run_experiment: str
    metrics_available: tuple[str, ...]
    risks: tuple[str, ...]
    open_questions: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.project_summary, "project_summary")
        _text(self.how_to_run_experiment, "how_to_run_experiment")
        if not isinstance(self.codebase_map, tuple) or not all(isinstance(item, CodebaseEntry) for item in self.codebase_map):
            raise GroundingBackgroundError("codebase_map must contain typed path/purpose entries")
        for name in ("entrypoints", "metrics_available", "risks", "open_questions"):
            if not isinstance(getattr(self, name), tuple):
                raise GroundingBackgroundError(f"{name} must be a tuple")
            _texts(getattr(self, name), name)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, max_bytes: int = 1024 * 1024) -> GroundingBackground:
        fields = set(cls.__dataclass_fields__)
        if not isinstance(value, Mapping) or set(value) != fields:
            raise GroundingBackgroundError("Grounding background requires exactly its seven fields")
        try:
            size = len(json.dumps(dict(value), ensure_ascii=False, allow_nan=False).encode())
            if type(max_bytes) is not int or max_bytes < 1 or size > max_bytes:
                raise GroundingBackgroundError("complete Grounding background exceeds its byte budget")
            entries = value["codebase_map"]
            if not isinstance(entries, (list, tuple)) or any(not isinstance(item, Mapping) or set(item) != {"path", "purpose"} for item in entries):
                raise GroundingBackgroundError("codebase_map requires path/purpose objects")
            return cls(project_summary=value["project_summary"],
                codebase_map=tuple(CodebaseEntry(**item) for item in entries),
                how_to_run_experiment=value["how_to_run_experiment"],
                **{name: _texts(value[name], name) for name in fields - {
                    "project_summary", "codebase_map", "how_to_run_experiment"}})
        except (TypeError, ValueError) as exc:
            if isinstance(exc, GroundingBackgroundError):
                raise
            raise GroundingBackgroundError(f"invalid Grounding background: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {"project_summary": self.project_summary,
                "codebase_map": [{"path": item.path, "purpose": item.purpose} for item in self.codebase_map],
                "entrypoints": list(self.entrypoints), "how_to_run_experiment": self.how_to_run_experiment,
                "metrics_available": list(self.metrics_available), "risks": list(self.risks),
                "open_questions": list(self.open_questions)}


def background_json_schema() -> dict[str, Any]:
    """The same candidate fields for an adapter's structured completion schema."""
    strings = {"type": "array", "items": {"type": "string", "minLength": 1}}
    properties = {"project_summary": {"type": "string", "minLength": 1},
                  "codebase_map": {"type": "array", "items": {"type": "object",
                      "properties": {"path": {"type": "string", "minLength": 1},
                                     "purpose": {"type": "string", "minLength": 1}},
                      "required": ["path", "purpose"], "additionalProperties": False}},
                  "entrypoints": strings, "how_to_run_experiment": {"type": "string", "minLength": 1},
                  "metrics_available": strings, "risks": strings, "open_questions": strings}
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def validate_web_sources(sources: Sequence[str], retrieved_urls: Collection[str]) -> tuple[str, ...]:
    """Bind dynamic citations to actual tool observations, not immutable refs."""
    values = _texts(sources, "web_sources")
    if len(set(values)) != len(values) or not set(values) <= set(retrieved_urls):
        raise GroundingBackgroundError("web_sources must be unique actually retrieved URLs")
    try:
        for value in values:
            _uri_parts(value, "Grounding web source")
    except LiteratureError as exc:
        raise GroundingBackgroundError(str(exc)) from exc
    return values


def web_result_metadata(result: Mapping[str, Any]) -> dict[str, Any]:
    """Keep ordinary tool observations compact; full content stays in CC history."""
    metadata = {name: value for name, value in result.items() if name not in {"text", "links"}}
    if isinstance(metadata.get("results"), list):
        metadata["results"] = [{name: value for name, value in item.items() if name != "snippet"}
                               for item in metadata["results"] if isinstance(item, Mapping)]
    return metadata


def retrieved_web_urls(records: Sequence[Mapping[str, Any]]) -> set[str]:
    urls: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping) or record.get("status") != "success": continue
        result = record.get("result")
        if not isinstance(result, Mapping): continue
        if record.get("tool") == "web_fetch":
            urls.update(value for name in ("requested_url", "url") if isinstance(value := result.get(name), str))
            documents = result.get("documents", ())
            if isinstance(documents, (list, tuple)):
                for document in documents:
                    if isinstance(document, Mapping) and document.get("status") == "available":
                        urls.update(value for name in ("requested_url", "url")
                                    if isinstance(value := document.get(name), str))
        elif record.get("tool") == "web_search":
            entries = result.get("results", ())
            if isinstance(entries, (list, tuple)):
                urls.update(item["url"] for item in entries if isinstance(item, Mapping) and isinstance(item.get("url"), str))
            if isinstance(result.get("source_url"), str): urls.add(result["source_url"])
    return urls


def transcript_web_results(content: bytes, *, session_id: str) -> tuple[dict[str, Any], ...]:
    """Read paired native CC MCP messages, including a LOST invocation's prefix.

    The caller establishes archive ownership/integrity through its existing
    publication. Agent prose, unmatched tool IDs and unfinished/error results
    are not observations. No new receipt or URL authority is created.
    """
    pending: dict[str, tuple[str, Mapping[str, Any]]] = {}
    records: list[dict[str, Any]] = []
    for line in content.splitlines():
        try: raw = json.loads(line)
        except (ValueError, UnicodeError): continue
        if not isinstance(raw, Mapping) or raw.get("sessionId") != session_id: continue
        message = raw.get("message")
        if not isinstance(message, Mapping) or not isinstance(message.get("content"), list): continue
        for block in message["content"]:
            if not isinstance(block, Mapping): continue
            name, identity = block.get("name"), block.get("id")
            if (raw.get("type") == message.get("role") == "assistant" and block.get("type") == "tool_use"
                    and name in {"mcp__host-tools__web_search", "mcp__host-tools__web_fetch"}
                    and isinstance(identity, str) and isinstance(block.get("input"), Mapping)):
                pending[identity] = (name.removeprefix("mcp__host-tools__"), block["input"])
            elif raw.get("type") == message.get("role") == "user" and block.get("type") == "tool_result":
                identity = block.get("tool_use_id")
                if not isinstance(identity, str): continue
                invocation = pending.pop(identity, None)
                if invocation is None or block.get("is_error"): continue
                body = block.get("content")
                if not isinstance(body, list): continue
                for part in body:
                    if not isinstance(part, Mapping) or part.get("type") != "text": continue
                    try: result = json.loads(part.get("text", ""))
                    except (ValueError, TypeError): continue
                    if isinstance(result, Mapping):
                        records.append({"tool": invocation[0], "arguments": dict(invocation[1]),
                            "status": "success", "result": web_result_metadata(result)})
    return tuple(records)


def delivered_web_metadata(records: Sequence[Mapping[str, Any]],
                           native: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Retain retrieval diagnostics, but only paired native results are success."""
    remaining = list(native)
    result = []
    waiting = []
    for record in records:
        item = dict(record)
        if item.get("status") == "success":
            delivered = {key: item[key] for key in ("tool", "arguments", "status", "result") if key in item}
            if delivered in remaining:
                remaining.remove(delivered)
                prior = [pending for pending in waiting if pending["arguments"] == item["arguments"]]
                traces = [trace for pending in prior for trace in pending.get("literature_download_trace", [])]
                traces.extend(item.get("literature_download_trace", []))
                if traces:
                    item["literature_download_trace"] = traces
                waiting = [pending for pending in waiting if pending not in prior]
            elif (item.get("tool") == "web_fetch"
                    and any(document.get("status") == "pending" for document in
                        item.get("result", {}).get("documents", [item.get("result", {})]))):
                # MCP waits on the existing acquisition, returning only its last
                # result to the model. Internal polls are not separate tool calls;
                # carry their diagnostics into the eventual delivered response.
                waiting.append(item)
                continue
            else:
                item.update(status="error", error={"code": "response_not_observed",
                    "message": "Host retrieval completed, but no matching successful native tool result was observed"})
        result.append(item)
    return result


def render_background_markdown(background: GroundingBackground, *, web_sources: Sequence[str] = ()) -> str:
    """Render only provided observations; empty sections remain explicitly unknown."""
    sections = ["# Research background\n\n", background.project_summary + "\n\n", "## Codebase map\n\n"]
    sections.extend(f"- {item.path}: {item.purpose}\n" for item in background.codebase_map)
    if not background.codebase_map:
        sections.append("Not established in this observation.\n")
    for title, entries in (("Entrypoints", background.entrypoints),
                           ("Metrics available", background.metrics_available),
                           ("Risks", background.risks), ("Open questions", background.open_questions)):
        sections.append(f"\n## {title}\n\n")
        sections.extend(f"- {item}\n" for item in entries)
        if not entries:
            sections.append("None reported; this is not proof of absence.\n")
    sections.extend(("\n## How to run an experiment\n\n", background.how_to_run_experiment + "\n"))
    if web_sources:
        sections.append("\n## Retrieved web sources\n\n")
        sections.extend(f"- {url}\n" for url in web_sources)
    return "".join(sections)


__all__ = ["CodebaseEntry", "GroundingBackground", "GroundingBackgroundError",
           "background_json_schema", "validate_web_sources", "render_background_markdown",
           "web_result_metadata", "retrieved_web_urls", "transcript_web_results", "delivered_web_metadata"]
