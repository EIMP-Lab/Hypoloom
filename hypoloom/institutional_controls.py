"""Operator choices in the existing run command/transition journal."""
from __future__ import annotations

import json

from .control_protocol import ControlResponse
from .institutional_access import PROVIDERS, PROVIDER_MODES, article_url, publisher
from .lifecycle import Command, RunState, WORKFLOW_DEFINITION_VERSION

DURABLE_ACTIONS = {"skip", "restore", "mode"}


def choices(store, run_id):
    modes, skipped = {}, {}
    # Document workers also consult choices. Do not touch the writer's read cache
    # or SQLite connection from those threads.
    with store.readonly_connection() as db:
        rows = db.execute("SELECT t.payload_json FROM workflow_transitions t JOIN workflow_commands c ON c.command_id=t.command_id WHERE t.run_id=? "
            "AND t.workflow_kind='RUN' AND c.command_type=? ORDER BY t.revision", (run_id, Command.LITERATURE_ACCESS.value)).fetchall()
    for row in rows:
        control = json.loads(row["payload_json"])["literature_access"]
        if control["action"] == "mode":
            modes[control["provider"]] = control["mode"]
        elif control["action"] == "skip":
            skipped[control["identity"]] = control["url"]
        else:
            skipped.pop(control["identity"], None)
    return modes, skipped


def bind(host):
    if host.institutional_access is not None:
        host.institutional_access.read_choices = lambda run_id: choices(host.store, run_id)


def control(host, request):
    payload = request.payload
    action = payload.get("action")
    fields = {"run_id", "provider", "action"}
    if action == "mode": fields.add("mode")
    elif action in {"skip", "restore", "verify"}: fields.add("url")
    elif action == "import": fields.update(("url", "path"))
    elif action in {"open", "retry"}:
        if "url" in payload: fields.add("url")
    else: raise ValueError("unsupported institutional action")
    if set(payload) != fields or not isinstance(payload["run_id"], str) or payload["provider"] not in PROVIDERS.values():
        raise ValueError("invalid institutional control fields or provider")
    run_id = payload["run_id"]
    if "url" in payload:
        if article_url(payload["url"]) != payload["url"] or publisher(payload["url"]) != payload["provider"]:
            raise ValueError("use a canonical article URL belonging to the selected provider")
    if action == "mode" and payload["mode"] not in PROVIDER_MODES:
        raise ValueError("provider mode must be auto, public_only or disabled")
    access = host.institutional_access
    if access is None: raise ValueError("institutional browser access is not configured")
    run = host.store.get_run(run_id)
    if run is None: raise ValueError("unknown run")
    if action in DURABLE_ACTIONS | {"verify"} and run["workflow_definition_version"] != WORKFLOW_DEFINITION_VERSION:
        raise ValueError("this run lifecycle does not support institutional preferences/preparation")
    bind(host)
    if action not in DURABLE_ACTIONS:
        head = host.store.latest_transition(run_id)
        if head is None or head["state"] not in {"CREATED", "ACTIVE"}:
            raise ValueError("institutional acquisition requires a live non-stopping run")
        result = access.control(run_id, payload["provider"], action, url=payload.get("url"), path=payload.get("path"))
        return ControlResponse.accepted(request.request_id, head["revision"], {"literature_access": result})
    if request.expected_revision is None:
        raise ValueError("institutional preferences require expected_revision")

    def operation(tx):
        head = tx.latest_transition(run_id)
        if head is None or head["state"] not in {"CREATED", "ACTIVE"}:
            raise ValueError("institutional preferences require a CREATED or ACTIVE run")
        selected = dict(payload)
        if action in {"skip", "restore"}:
            selected["identity"] = access.paper_identity(run_id, payload["url"])
        row = host._transition_record(tx, run_id, run_id, "RUN", Command.LITERATURE_ACCESS,
            RunState(head["state"]), request.request_id, payload={"literature_access": selected})
        tx.append_transition(row)
        return {"status": "accepted", "revision": row["revision"], "result": {"run_id": run_id}}

    stored = host.store.execute_command({"command_id": request.request_id, "run_id": run_id, "workflow_id": run_id,
        "expected_revision": request.expected_revision, "command_type": Command.LITERATURE_ACCESS.value,
        "request": request.to_mapping()}, operation)
    # Apply only committed choices; a replay cannot overwrite a newer choice.
    return ControlResponse.accepted(request.request_id, stored.revision,
        {**dict(stored.result or {}), "literature_access": access.status(run_id)})
