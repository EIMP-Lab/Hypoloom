"""Hypoloom-only Claude PreToolUse safety adapter.

This hook owns no workflow state. It never discovers an active run, evaluates
a gate, or writes metering data. It delegates generic path checks to Hypoloom.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _deny(reason: str) -> int:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(payload, ensure_ascii=False))
    print(f"guard_paths DENY: {reason}", file=sys.stderr)
    return 2


def check(raw: str) -> int:
    """Evaluate one hook payload without consulting any run authority."""

    if not raw.strip():
        return _deny("empty hook payload")
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        return _deny(f"cannot parse hook payload: {exc}")
    if not isinstance(payload, dict):
        return _deny("hook payload must be a JSON object")

    tool = payload.get("tool_name") or payload.get("tool") or ""
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        if tool in {"Write", "Edit", "NotebookEdit", "Bash", "Monitor"}:
            return _deny(f"{tool} tool_input must be a JSON object")
        return 0

    from hypoloom import hook_safety

    if tool in {"Write", "Edit", "NotebookEdit"}:
        target = tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path") or ""
        base = tool_input.get("cwd") or payload.get("cwd") or REPO_ROOT
        allowed, reason = hook_safety.is_write_allowed(
            target,
            repo_root=REPO_ROOT,
            base_dir=base,
        )
        return 0 if allowed else _deny(reason)

    if tool in {"Bash", "Monitor"}:
        command = tool_input.get("command") or ""
        allowed, reason = hook_safety.is_bash_allowed(command)
        if not allowed:
            return _deny(reason)
        base = tool_input.get("cwd") or payload.get("cwd") or REPO_ROOT
        allowed, reason = hook_safety.is_destructive_command_safe(
            command,
            repo_root=REPO_ROOT,
            base_dir=base,
        )
        if not allowed:
            return _deny(reason)
        try:
            wrapped = hook_safety.research_shell_command(command, repo_root=REPO_ROOT, base_dir=base)
        except (OSError, RuntimeError, ValueError) as exc:
            return _deny(f"cannot protect research source: {exc}")
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
            "updatedInput": {**tool_input, "command": wrapped}}}, ensure_ascii=False))
        return 0

    # Read-only tools and Agent/AskUserQuestion have no direct generic file
    # effect here. Workflow admission and authority belong to the resident Host.
    return 0


def main() -> int:
    try:
        raw = sys.stdin.read() if sys.stdin else ""
    except Exception:
        raw = ""
    return check(raw)


if __name__ == "__main__":
    raise SystemExit(main())
