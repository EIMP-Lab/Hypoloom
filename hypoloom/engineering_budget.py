"""Stdlib PreToolUse hook: bound local Bash work and leave handoff time.

This never grants permissions or submits experiments. The Host's outer deadline
remains authoritative; existing profile deny hooks still apply to the command.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import sys
import time

MAX_LOCAL_SECONDS = 60
MAX_HOOK_BYTES = 1024 * 1024


def budget_decision(event, *, deadline, wall_seconds, now=None):
    if not isinstance(event, dict) or event.get("tool_name") != "Bash": return {}
    def deny(reason):
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
            "permissionDecision": "deny", "permissionDecisionReason": reason}}
    try:
        end = datetime.strptime(deadline, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() + 1
        if not math.isfinite(wall_seconds) or wall_seconds <= 0: raise ValueError("invalid wall budget")
        reserve = min(15.0, wall_seconds / 10)
        remaining = end - (time.time() if now is None else now) - reserve
        limit = math.floor(min(MAX_LOCAL_SECONDS, remaining) * 1000)
        if limit < 1:
            return deny("Local check budget is exhausted. Preserve the draft and complete the required JSON/handoff; do not start another local command.")
        tool = event["tool_input"]
        if not isinstance(tool, dict) or not isinstance(tool.get("command"), str): raise ValueError("invalid Bash input")
        requested = tool.get("timeout", limit)
        if type(requested) is not int or requested <= 0: raise ValueError("invalid Bash timeout")
        updated = {**tool, "timeout": min(requested, limit), "run_in_background": False}
        if updated == tool: return {}
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": updated,
            "additionalContext": "Local checks are bounded by the remaining invocation budget, reserving time for handoff. "
                "Formal resource work uses Host submission. If a child fails, inspect its exit code instead of waiting on an empty queue."}}
    except (KeyError, TypeError, ValueError, OverflowError):
        return deny("Invalid local check budget/input. Use a positive explicit timeout within the invocation deadline.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deadline", required=True)
    parser.add_argument("--wall-seconds", type=float, required=True)
    args = parser.parse_args()
    raw = sys.stdin.buffer.read(MAX_HOOK_BYTES + 1)
    try:
        if len(raw) > MAX_HOOK_BYTES: raise ValueError("oversized hook input")
        result = budget_decision(json.loads(raw), deadline=args.deadline, wall_seconds=args.wall_seconds)
    except (ValueError, UnicodeError, RecursionError):
        result = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": "Local check input is malformed or exceeds its bounded size."}}
    print(json.dumps(result, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
