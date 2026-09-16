"""Minimal read-only MCP facade for explicit Hypoloom state roots and run IDs."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TextIO

from .monitor import build_monitor_snapshot
from .state_store import StateStore


MCP_PROTOCOL_VERSION = "2024-11-05"


def _text_argument(arguments: Mapping[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be non-empty trimmed text")
    return value


def _inspect_run(arguments: Mapping[str, Any]) -> dict[str, Any]:
    root = _text_argument(arguments, "state_root")
    run_id = _text_argument(arguments, "run_id")
    return build_monitor_snapshot(root, run_id).to_dict()


def _list_runs(arguments: Mapping[str, Any]) -> dict[str, Any]:
    root = Path(_text_argument(arguments, "state_root")).expanduser().resolve()
    store = StateStore(root)
    rows = store.inspect(
        "SELECT r.run_id,t.state,t.revision FROM workflow_runs r "
        "LEFT JOIN workflow_transitions t ON t.workflow_id=r.run_id "
        "AND NOT EXISTS(SELECT 1 FROM workflow_transitions n "
        "WHERE n.workflow_id=t.workflow_id AND n.revision>t.revision) "
        "ORDER BY r.run_id"
    )
    return {
        "state_root": str(root),
        "runs": [
            {
                "run_id": str(row["run_id"]),
                "state": None if row["state"] is None else str(row["state"]),
                "revision": (
                    None if row["revision"] is None else int(row["revision"])
                ),
            }
            for row in rows
        ],
    }


_ToolHandler = Callable[[Mapping[str, Any]], dict[str, Any]]
TOOLS: dict[str, tuple[str, dict[str, Any], _ToolHandler]] = {
    "hypoloom_inspect_run": (
        "Read one exact Hypoloom run, including experts, attempts, facts and Oracle integrity.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "state_root": {"type": "string"},
                "run_id": {"type": "string"},
            },
            "required": ["state_root", "run_id"],
        },
        _inspect_run,
    ),
    "hypoloom_list_runs": (
        "List Hypoloom run IDs and current RunWorkflow heads in one exact state root.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"state_root": {"type": "string"}},
            "required": ["state_root"],
        },
        _list_runs,
    ),
}


def handle_request(request: Mapping[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC request without changing repository or run state."""

    request_id = request.get("id")
    method = request.get("method")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "hypoloom", "version": "1"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {"name": name, "description": spec[0], "inputSchema": spec[1]}
                    for name, spec in TOOLS.items()
                ]
            },
        }
    if method != "tools/call":
        return _rpc_error(request_id, -32601, f"method not found: {method}")
    parameters = request.get("params")
    if not isinstance(parameters, Mapping):
        return _rpc_error(request_id, -32602, "params must be an object")
    tool_name = parameters.get("name")
    tool = TOOLS.get(tool_name) if isinstance(tool_name, str) else None
    if tool is None:
        return _rpc_error(request_id, -32601, f"unknown tool: {tool_name}")
    arguments = parameters.get("arguments", {})
    if not isinstance(arguments, Mapping):
        return _rpc_error(request_id, -32602, "arguments must be an object")
    try:
        result = tool[2](arguments)
    except Exception as exc:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"status": "error", "error": str(exc)},
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    }
                ],
                "isError": True,
            },
        }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, ensure_ascii=False, sort_keys=True),
                }
            ]
        },
    }


def _rpc_error(request_id: object, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def main(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    source = stdin or sys.stdin
    destination = stdout or sys.stdout
    for raw in source:
        if not raw.strip():
            continue
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(request, Mapping):
            continue
        response = handle_request(request)
        if response is not None:
            destination.write(json.dumps(response, ensure_ascii=False) + "\n")
            destination.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["MCP_PROTOCOL_VERSION", "TOOLS", "handle_request", "main"]
