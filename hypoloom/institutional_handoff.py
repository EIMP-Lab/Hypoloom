"""Bounded preparation-to-run handoff using the existing temporary control owner."""
from __future__ import annotations

import json
import sys
from time import sleep


HANDOFF_SECONDS = 600
RELEASE_SECONDS = 6


def preparation_target(args):
    values = [args.state_root, args.run_id, args.request_id]
    if not any(values) and args.institutional_browser_mode != "client":
        return None
    if not all(values) or not args.agent_session or args.institutional_browser_mode != "client":
        raise ValueError("client preparation requires --agent-session and exact --state-root/--run-id/--request-id from init")
    from .institutional_client import validate_target
    return validate_target(*values)


def _launched(target):
    from .institutional_client import connections
    try:
        result = connections(target["state_root"], target["run_id"], target["request_id"])
    except (OSError, ValueError):
        return False
    return result["lifecycle"] in {"active", "terminal"}


def batches(args, access, dialogue, selected, modes, output, input_fn, root, target):
    from .institutional_prepare import _batch
    from .institutional_instructions import render_results
    command = [sys.executable, "-B", "-m", "hypoloom", "institutional", "preparation"]
    results = {}
    pending = list(selected)

    def progress(line):
        # Publish one combined summary, retaining completed publishers on retries.
        if line.startswith("准备结果（"): return
        if line.startswith("{") and "preparation_summary" in json.loads(line): return
        output(line)

    while True:
        summary, code = _batch(args, access, dialogue, pending, modes, progress, input_fn)
        results.update((row["provider"], row) for row in summary["preparation_summary"])
        summary["preparation_summary"] = [results[name] for name in selected]
        pending = list(summary["remaining_providers"])
        summary["remaining_preparation_argv"] = ([*command, "retry", "--state-root", root] if pending else [])
        summary["finish_argv"] = [*command, "finish", "--state-root", root]
        summary["scope"] = target
        output(render_results(summary["preparation_summary"], modes))
        output(json.dumps(summary, ensure_ascii=False))
        outcome = ("operator_finished" if dialogue.cancelled() else
                   dialogue.await_launch(summary, lambda: _launched(target), seconds=HANDOFF_SECONDS))
        if outcome != "retry":
            dialogue.update(state="finishing", actions=[])
            # Leave the existing endpoint available for one ordinary client poll
            # to observe revocation. Offline clients retain their local 300s bound.
            if outcome != "run_handoff": sleep(RELEASE_SECONDS)
            # Controller lifetime is independent of the publisher sample result
            # and its exit code. Emit only the bounded, fixed ending reason.
            output(json.dumps({"preparation_lifecycle": {"state": "finished", "reason": outcome}}))
            return code
        # Discard only the retried publishers' temporary acquisitions. The same
        # browser owner, control root and accepted results survive this batch.
        for provider in pending:
            access.discard_preparation("preparation", provider)
