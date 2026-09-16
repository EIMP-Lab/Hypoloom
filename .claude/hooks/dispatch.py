"""Hypoloom-only Claude hook dispatcher.

The working tree no longer dispatches legacy budget, metering, event, gate, or
active-run logic. ``pre`` applies the generic interactive safety backstop;
``post`` is a stateless compatibility no-op for machine-local settings that
have not yet been synchronized with ``settings.example.json``.
"""

from __future__ import annotations

import sys
from pathlib import Path


HOOKS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HOOKS_DIR))


def _read_stdin() -> str:
    try:
        return sys.stdin.read() if sys.stdin else ""
    except Exception:
        return ""


def run_pre(raw: str) -> int:
    """Apply the Hypoloom generic guard; malformed input fails closed there."""

    import guard_paths

    return guard_paths.check(raw)


def run_post(_raw: str) -> int:
    """Remain harmless while stale local settings still invoke this entry."""

    return 0


def main() -> int:
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    raw = _read_stdin()
    if event == "pre":
        return run_pre(raw)
    if event == "post":
        return run_post(raw)
    print(
        f"dispatch.py: unknown hook event {event!r} (expected pre|post)",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
