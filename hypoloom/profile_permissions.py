"""Native CC hook adapter for frozen operator Bash regexes (ADR-0023).

Not a shell-language security boundary. The OS sandbox still owns isolation.
This module runs standalone inside bwrap with only the system standard library.
"""
from __future__ import annotations

import json
import re
import sys


def _deny(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
        "permissionDecision": "deny", "permissionDecisionReason": reason}}


def permission_decision(event, patterns):
    try:
        if not isinstance(event, dict) or event.get("tool_name") != "Bash":
            return _deny("Malformed Profile Bash policy input")
        command = event["tool_input"]["command"]
        if not isinstance(command, str) or not isinstance(patterns, list) or any(not isinstance(item, str) for item in patterns):
            return _deny("Malformed Profile Bash policy input")
        if any(re.search(pattern, command, re.MULTILINE) for pattern in patterns):
            return _deny("Command matches a frozen safety.bash.deny_patterns rule; use an allowed operation")
        return {}
    except (KeyError, TypeError, ValueError, re.error):
        return _deny("Malformed Profile Bash policy input")


def main():
    try:
        event = json.loads(sys.stdin.read(1024 * 1024 + 1))
        result = permission_decision(event, json.loads(sys.argv[1]))
    except (ValueError, IndexError):
        result = _deny("Malformed Profile Bash policy input")
    print(json.dumps(result))


if __name__ == "__main__": main()
