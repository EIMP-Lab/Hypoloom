"""Disposable task ownership for observers; execution identities stay unchanged."""
from __future__ import annotations

import base64
import json

from .expert_loop import ResearchNodeKind

_RUN_TASKS = {"GROUNDING_QUERY_PLAN", "GROUNDING", "OBSERVER", "GRAPH_EVOLVE",
              "FINAL_REPORT", "DEBATE_JUDGE"}
_EXPERT_TASKS = {item.value for item in ResearchNodeKind} | {"LEADER", "DEBATE",
    "PROPOSE_HYPOTHESIS", "DRAFT_EXPERIMENT_SPEC", "ANSWER_RENDEZVOUS"}


def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def request_of(row):
    return _object(_object(row.get("observation", row.get("observation_json"))).get("request"))


def reading_parent(request):
    for item in request.get("input_artifacts", ()):
        if str(item.get("ref", "")).endswith("--paper-question"):
            try:
                return _object(json.loads(base64.b64decode(item["content_base64"], validate=True))).get("parent_attempt_id")
            except (ValueError, KeyError, TypeError):
                pass
    return None


def attempt_scopes(attempts, commands=()):
    """Follow existing reader commissions, never infer ownership from a role name."""
    rows = {row["attempt_id"]: row for row in attempts}
    parents = {}
    for row in commands:
        job = _object(_object(row.get("request", row.get("request_json"))).get("paper_reading"))
        if job.get("parent_attempt_id"):
            parents[row["command_id"]] = job["parent_attempt_id"]
    scopes = {}

    def resolve(identity, visiting):
        if identity in scopes:
            return scopes[identity]
        row = rows.get(identity, {})
        request = request_of(row)
        task = _object(request.get("task_context"))
        key = _object(task.get("key"))
        kind = task.get("request_kind") or key.get("node_kind") or request.get("command")
        scope, owner = "unknown", None
        if identity not in visiting and row:
            if kind == "PAPER_READ":
                parent = parents.get(request.get("command_id", row.get("command_id"))) or reading_parent(request)
                if parent in rows and rows[parent].get("run_id") == row.get("run_id"):
                    inherited = resolve(parent, visiting | {identity})
                    scope, owner = inherited["task_scope"], inherited["owner_role"]
            elif kind in _RUN_TASKS:
                scope = "run"
            elif kind in _EXPERT_TASKS:
                owner = key.get("expert_slot") or task.get("expert_slot") or request.get("role")
                if isinstance(owner, str) and owner:
                    scope = "expert"
                else:
                    owner = None
        value = {"task_scope": scope, "owner_role": owner, "task_kind": kind}
        scopes[identity] = value
        return value

    for identity in rows:
        resolve(identity, set())
    return scopes


def grounding_sharing(attempts, roles, run_state):
    """Evidence of supplied inputs is not evidence of reading or understanding."""
    groundings = [row for row in attempts if request_of(row).get("command") == "GROUNDING"]
    final = []
    for row in groundings:
        candidate = _object(_object(_object(row.get("observation", row.get("observation_json"))).get("response")).get("typed_candidate"))
        if (row.get("state") == "CLOSED" and row.get("terminal_reason") == "SUCCEEDED"
                and candidate.get("clarification") is None and isinstance(candidate.get("background"), dict)):
            final.append(row)
    source = final[0]["attempt_id"] if len(final) == 1 else None
    state = ("accepted" if source else "waiting_human" if run_state == "WAITING_HUMAN"
             else "preparing" if any(row.get("state") == "OPEN" for row in groundings) else "unavailable")
    supplied = []
    for role in roles:
        matches = [row["attempt_id"] for row in attempts
            if source and request_of(row).get("role") == role
            and _object(request_of(row).get("task_context")).get("kind") == "RESEARCH_NODE"
            and any(item.get("ref") == source + "--response" for item in request_of(row).get("input_artifacts", ()))]
        supplied.append({"role": role, "provided": bool(matches), "attempt_ids": matches})
    return {"state": state, "source_attempt_id": source, "experts": supplied}
