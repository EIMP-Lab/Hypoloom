"""Short-lived historical readers using the existing bounded worker transport.

Workers open no writer-capable StateStore. Only the existing disposable view
preparers may write files; the resident Host still publishes the human view.
"""
from __future__ import annotations

from pathlib import Path
import time

from .network_io import run_network_operation


def read_in_worker(operation, state_root, run_id, *, cancel_event=None, timeout=600.0, **arguments):
    return run_network_operation("hypoloom.observer_worker:perform", {
        "operation": operation, "state_root": str(Path(state_root).resolve()),
        "run_id": run_id, **arguments,
    }, deadline=time.monotonic() + timeout, max_output_bytes=16 * 1024 * 1024,
        cancel_event=cancel_event)


def perform(payload):
    root, run_id = Path(payload["state_root"]), payload["run_id"]
    operation = payload["operation"]
    if operation == "audit":
        from .oracle import Oracle
        return Oracle(root / "state.sqlite", root / "artifacts").audit(run_id).to_dict()
    from .research_projection import freeze_role_view, prepare_research_projection
    staged = Path(payload["staged"])
    if operation == "projection":
        prepared = prepare_research_projection(root, run_id, _staged=staged)
        return {"output": str(prepared[1]), "count": prepared[2], "replacing": prepared[3]}
    if operation == "role_view":
        path = freeze_role_view(root, run_id, payload["role"], payload["view_id"], _staged=staged)
        return {"path": str(path)}
    raise ValueError("unknown historical reader operation")
