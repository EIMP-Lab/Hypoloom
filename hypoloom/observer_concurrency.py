"""Disposable capacity and execution-handoff observations from existing rows."""
from __future__ import annotations

from collections import Counter
import json

from .agent_aci import execution_handoff, supports_experiment_submission
from .agent_budget import profile_effect_capacity
from .contract_normalization import EXPERT_LOOP_RESEARCH_LOOP


def concurrency_projection(contract, attempts, experiments, observed_at, version):
    separate = (supports_experiment_submission(version)
        and contract.get("methodology", {}).get("controls", {}).get("research_loop") == EXPERT_LOOP_RESEARCH_LOOP)
    counts = Counter()
    open_commands, handoffs = set(), set()
    for row in attempts:
        observation = row.get("observation") or json.loads(row.get("observation_json") or "{}")
        request = observation.get("request", {})
        if row.get("state") == "OPEN":
            kind = row.get("kind")
            if kind == "AGENT" and request.get("command") == "ANSWER_RENDEZVOUS": kind = "REVIEW"
            counts[kind] += 1
            if kind == "AGENT" and request.get("command") == "EXPERIMENT_EXECUTION":
                open_commands.add(row.get("command_id") or request.get("command_id"))
        elif row.get("state") == "CLOSED" and row.get("terminal_reason") == "SUCCEEDED":
            if identity := execution_handoff(observation): handoffs.add(identity)
    slots = {}
    for kind in ("AGENT", "PROCESS", "REVIEW"):
        try: limit = profile_effect_capacity(contract, kind) if separate else None
        except (TypeError, ValueError, KeyError): limit = None
        slots[kind] = {"used": counts[kind], "limit": limit}
    roles = {}
    for identity, experiment in experiments.items():
        role = experiment.get("role")
        if role is None or experiment.get("state") == "TERMINAL": continue
        own = roles.setdefault(role, {"pending_experiments": 0, "experiment_ids": [], "handed_off": 0,
            "open_controllers": 0, "oldest_controller_seconds": None})
        own["pending_experiments"] += 1
        own["experiment_ids"].append(identity)
        if experiment.get("execution_node_command_id") in open_commands:
            own["open_controllers"] += 1
            start = experiment.get("submitted_at")
            if observed_at is not None and start is not None:
                own["oldest_controller_seconds"] = max(own["oldest_controller_seconds"] or 0, observed_at - start, 0)
        elif identity in handoffs:
            own["handed_off"] += 1
    return {"slots": slots, "separate_limits": separate, "experts": roles}
