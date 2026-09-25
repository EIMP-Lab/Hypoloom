"""Disposable numerical read projections over the existing run evidence.

No counter is persisted. Attempts identify retries, facts identify scientific
objects, and ordinary accepted plugin Git supplies native skill entrypoints.
"""
from __future__ import annotations

import base64
import binascii
import json
import math
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

_TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


def _payload(row):
    return row["payload"] if "payload" in row else json.loads(row.get("payload_json") or "{}")


def _time(value):
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            result = float(value)
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            result = (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()
        return result if math.isfinite(result) else None
    except (ValueError, OverflowError):
        return None


def _observation(row):
    observation = row.get("observation")
    return json.loads(row.get("observation_json") or "{}") if observation is None else observation


def _agents(attempts):
    for row in attempts:
        if row.get("kind") != "AGENT":
            continue
        observation = _observation(row)
        yield row, observation, observation.get("request", {})


@lru_cache(maxsize=128)
def _packet_papers(encoded):
    """Parse immutable packet bytes once; do not retain entire session archives."""
    packet = json.loads(base64.b64decode(encoded, validate=True))
    papers = packet["papers"]
    if not isinstance(papers, list) or any(not isinstance(paper, dict)
            or not isinstance(paper.get("bibliographic_identity"), str)
            or not paper["bibliographic_identity"]
            or paper.get("retrieval_disposition") not in {"RETRIEVED", "PDF_UNAVAILABLE"}
            for paper in papers):
        raise ValueError("literature packet lacks recorded bibliographic evidence")
    return papers


def literature_catalog(state_root: str | Path, run_id: str, attempts) -> dict[str, Any]:
    """Exact bibliography identities and PDF availability, never paper reads.

    The root is intentionally unused: packet bytes already belong to the frozen
    request. Missing old packet evidence remains distinguishable from no work.
    """
    papers, unavailable = {}, []
    for row, observation, request in _agents(attempts):
        if row.get("run_id", run_id) != run_id:
            continue
        role = request.get("role")
        preflight = observation.get("preflight_diagnostics")
        recorded_downloads = preflight.get("literature_download_trace", ()) if isinstance(preflight, dict) else ()
        downloads = {entry.get("bibliographic_identity"): entry for entry in recorded_downloads[:128]
            if isinstance(entry, dict) and isinstance(entry.get("bibliographic_identity"), str)} if isinstance(recorded_downloads, (list, tuple)) else {}
        inputs = [item for item in request.get("input_artifacts", ())
            if str(item.get("ref", "")).endswith("--literature-packet")]
        task = request.get("task_context") or {}
        key = task.get("key") or {}
        expects_packet = request.get("command") == "GROUNDING" or (
            request.get("command") == "LITERATURE_REVIEW" and key.get("subkind", "MAIN") == "MAIN"
            and task.get("request_kind") != "GROUNDING_QUERY_PLAN")
        if expects_packet and not inputs and observation.get("failure_stage") != "literature_retrieval":
            unavailable.append({"attempt_id": row["attempt_id"], "role": role})
        for item in inputs:
            try:
                found = _packet_papers(item["content_base64"])
            except (KeyError, TypeError, ValueError, binascii.Error):
                unavailable.append({"attempt_id": row["attempt_id"], "role": role})
                continue
            for paper in found:
                identity = paper["bibliographic_identity"]
                if identity not in papers:
                    papers[identity] = {**deepcopy(paper), "roles": set(), "pdf_available_roles": set(),
                        "source_attempt_ids": set(), "download_attempts": []}
                current = papers[identity]
                current["roles"].add(role)
                current["source_attempt_ids"].add(row["attempt_id"])
                download = downloads.get(identity)
                if download is not None and isinstance(download.get("events"), list):
                    events = [{name: value for name, value in event.items()
                        if name in {"stage", "outcome", "source_host", "failure_code", "http_status"}
                        and (value is None or type(value) is int or isinstance(value, str) and len(value) <= 256)}
                        for event in download["events"][:64] if isinstance(event, dict)]
                    current["download_attempts"].append({"attempt_id": row["attempt_id"], "role": role,
                        "retrieval_disposition": paper["retrieval_disposition"], "events": events})
                if paper["retrieval_disposition"] == "RETRIEVED":
                    current["pdf_available_roles"].add(role)
                    # Preserve available extracted evidence if an earlier packet
                    # had metadata only for this exact bibliographic identity.
                    current.update(deepcopy(paper))
    values = []
    for identity in sorted(papers):
        paper = papers[identity]
        values.append({**paper, "download_coverage": "recorded" if paper["download_attempts"] else "unknown",
            **{name: sorted(paper[name], key=lambda item: str(item))
            for name in ("roles", "pdf_available_roles", "source_attempt_ids")}})
    return {"papers": values, "coverage": "partial" if unavailable and values else "unavailable" if unavailable else "complete",
        "unavailable_attempt_ids": sorted({item["attempt_id"] for item in unavailable}), "unavailable": unavailable}


def _literature_counts(catalog, role=None):
    papers = [item for item in catalog["papers"] if role is None or role in item["roles"]]
    missing = any(role is None or item["role"] == role for item in catalog["unavailable"])
    coverage = "partial" if missing and papers else "unavailable" if missing else "complete"
    return {"retrieved": None if coverage == "unavailable" else len(papers),
        "pdf_available": None if coverage == "unavailable" else sum(
            bool(item["pdf_available_roles"]) if role is None else role in item["pdf_available_roles"] for item in papers),
        "coverage": coverage}


def _skill_counts(catalog, role=None):
    # Versions are intentionally visible in detail: parallel bound commits can
    # differ. A skill's role/name is counted once across those current versions.
    skills = [item for item in catalog["skills"] if item.get("current", True)
        and (role is None or item["role"] == role)]
    versions = [item for item in catalog["versions"] if item.get("current", True)
        and (role is None or item["role"] == role)]
    unavailable = not versions or any(not item.get("available", False) for item in versions)
    coverage = "partial" if unavailable and skills else "unavailable" if unavailable else "complete"
    return {"count": None if coverage == "unavailable" else len({(item["role"], item["name"]) for item in skills}),
        "coverage": coverage}


def _logical_stages(agents, observed_at, state_root, run_id):
    from .agent_budget import fold_agent_attempts
    from .provider_observation import read_exchanges, summarize_exchanges

    grouped = defaultdict(list)
    for row, observation, request in agents:
        identity = request.get("command_id") or row.get("command_id") or row["attempt_id"]
        grouped[(request.get("role"), identity)].append((row, observation, request))
    stages = []
    for (role, command_id), records in grouped.items():
        row, observation, request = max(records, key=lambda item: (_time(item[0].get("started_at")) or 0, item[0]["attempt_id"]))
        task = request.get("task_context") or {}
        key = task.get("key") or {}
        start = _time(row.get("started_at"))
        finish = observed_at if row.get("state") == "OPEN" else _time(row.get("ended_at"))
        charges = fold_agent_attempts({**record, "observation_json": record.get("observation_json") or json.dumps(value)}
            for record, value, _ in records)
        failed = {record["attempt_id"] for record, _, _ in records
            if record.get("state") == "CLOSED" and record.get("terminal_reason") in {"FAILED", "LOST", "TIMED_OUT"}}
        reported = [charge for charge in charges if charge.source != "declared" and charge.cost is not None]
        reserved = [charge for charge in charges if charge.source == "declared"]
        unknown_ids = {charge.attempt_id for charge in reserved}
        observed_unsettled = 0
        for record, value, _ in records:
            if record["attempt_id"] not in unknown_ids:
                continue
            diagnostics = value.get("diagnostics")
            diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
            observed = diagnostics.get("provider_observed_usage")
            if not isinstance(observed, dict):
                # Old retained calls have no aggregate. Read their existing
                # capture only; no settlement or rewriting of run state.
                observed = summarize_exchanges(read_exchanges(
                    state_root / "diagnostics" / "agents" / run_id / record["attempt_id"] / "provider"))
            tokens = observed.get("total_tokens")
            if type(tokens) is int and tokens >= 0:
                observed_unsettled += tokens
        accounting = {"reported_cost_usd": str(sum((item.cost for item in reported), Decimal("0"))),
            "failed_reported_cost_usd": str(sum((item.cost for item in reported if item.attempt_id in failed), Decimal("0"))),
            "reserved_cost_usd": str(sum((item.cost for item in reserved if item.cost is not None), Decimal("0"))),
            "unknown_closed_attempts": sum(item.state == "CLOSED" for item in reserved),
            "open_attempts": sum(item.state == "OPEN" for item in charges),
            "reported_tokens": sum(item.tokens for item in charges if item.source != "declared" and item.tokens is not None),
            "observed_unsettled_tokens": observed_unsettled,
            "reserved_tokens": sum(item.tokens for item in reserved if item.tokens is not None),
            "unavailable_token_attempts": sum(item.tokens is None for item in charges)}
        stages.append({"role": role, "node": task.get("request_kind") or request.get("command"), "subkind": key.get("subkind"),
            "chain": key.get("chain"), "component_id": key.get("component_id", request.get("component_id")),
            "cycle": key.get("cycle", task.get("cycle")), "experiment_ordinal": key.get("experiment_ordinal"),
            "state": row.get("state"), "terminal_reason": row.get("terminal_reason"),
            "attempt_id": row["attempt_id"], "command_id": command_id, "attempts": len(records),
            "failed_attempts": len(failed), "accounting": accounting,
            "started_at": start, "ended_at": _time(row.get("ended_at")),
            "elapsed_seconds": None if start is None or finish is None else max(0.0, finish - start)})
    return sorted(stages, key=lambda item: (item["started_at"] or 0, item["attempt_id"]))


def _token_accounting(stages):
    # All logical commands, including retries and earlier cycles; never just the
    # currently visible stages. The existing admission fold supplies the values.
    return {key: sum(stage["accounting"][key] for stage in stages) for key in (
        "reported_tokens", "observed_unsettled_tokens", "reserved_tokens", "unknown_closed_attempts", "open_attempts",
        "unavailable_token_attempts")}


def _current_activity(stages, processes, observed_at):
    opened = [stage for stage in stages if stage["state"] == "OPEN"]
    for row in processes:
        if row.get("state") != "OPEN":
            continue
        start = _time(row.get("started_at"))
        opened.append({"node": "EXPERIMENT_EXECUTION", "kind": "PROCESS", "state": "OPEN",
            "attempt_id": row["attempt_id"], "experiment_id": row.get("experiment_id") or row.get("workflow_id"),
            "started_at": start, "elapsed_seconds": None if start is None or observed_at is None
                else max(0.0, observed_at - start)})
    # An executing job is current work even between Agent calls. With no OPEN
    # work show only the latest recorded cycle, not a stale stage from every cycle.
    if opened:
        return opened
    cycle = max((stage["cycle"] or 0 for stage in stages), default=0)
    return _visible_stages([stage for stage in stages if (stage["cycle"] or 0) == cycle])


def _host_observation(state_root):
    """Historical diagnostics are not evidence that a Host is alive or dead."""
    from .diagnostics import bounded_diagnostic_text

    latest = None
    try:
        with (Path(state_root) / "diagnostics/host.jsonl").open("rb") as source:
            source.seek(0, 2)
            source.seek(max(0, source.tell() - 65536))
            lines = source.read(65536).splitlines()
        for line in reversed(lines):
            try:
                record = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(record, dict) or record.get("kind") != "host_exception":
                continue
            latest = {key: bounded_diagnostic_text(record.get(key))
                for key in ("timestamp", "stage", "exception_type")}
            latest["stage"] = bounded_diagnostic_text(record.get("phase", record.get("stage")))
            latest["evidence"] = "diagnostics/host.jsonl"
            break
    except OSError:
        pass
    import fcntl
    import os
    import stat

    root = Path(state_root)
    lock_state = "unknown"
    try:
        descriptor = os.open(root / "controller.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if stat.S_ISREG(os.fstat(descriptor).st_mode):
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError:
                    lock_state = "held"
                else:
                    lock_state = "unheld"
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
    except FileNotFoundError:
        lock_state = "missing"
    except OSError:
        pass
    try:
        socket_state = "present" if stat.S_ISSOCK((root / "control.sock").lstat().st_mode) else "invalid"
    except FileNotFoundError:
        socket_state = "missing"
    except OSError:
        socket_state = "unknown"
    absent = lock_state in {"missing", "unheld"} and socket_state == "missing"
    return {"availability": "absent" if absent else "unconfirmed", "last_recorded_exception": latest,
        "lock": lock_state, "control_socket": socket_state}


def _evolutions(agents, role=None):
    records = [(row, observation, request) for row, observation, request in agents
        if request.get("command") == "SELF_EVOLVE" and (role is None or request.get("role") == role)]
    completed, changed = set(), set()
    failed, unknown_update = 0, False
    for row, observation, request in records:
        if row.get("state") != "CLOSED":
            continue
        identity = request.get("command_id") or row.get("command_id") or row["attempt_id"]
        if row.get("terminal_reason") == "SUCCEEDED":
            completed.add(identity)
            update = observation.get("plugin_update") or {}
            if not update.get("commit") or not update.get("base_commit"):
                unknown_update = True
            elif update["commit"] != update["base_commit"]:
                changed.add(identity)
        elif row.get("terminal_reason") in {"FAILED", "LOST", "TIMED_OUT"}:
            failed += 1
    return {"completed": len(completed), "changed": None if unknown_update else len(changed), "failed": failed}


def _interactions(transitions):
    leaders, debates = {}, {}
    for row in transitions:
        if row.get("workflow_kind") != "RUN":
            continue
        event = _payload(row).get("expert_interaction") or {}
        action = event.get("action", "")
        if action.startswith("leader_"):
            identity, target = event.get("source_attempt_id"), leaders
        elif action.startswith("debate_"):
            identity, target = event.get("debate_id"), debates
        else:
            continue
        if not identity:
            continue
        current = target.setdefault(identity, {"roles": set(), "started": False, "state": "selected"})
        if leader := event.get("leader"):
            if leader.get("role"):
                current["roles"].add(leader["role"])
        debate = event.get("proposal", {}).get("debate")
        participants = event.get("participants") or (debate.get("participants", []) if debate is not None else [])
        for participant in participants:
            scope = participant.get("participant", participant).get("scope", {})
            if scope.get("role"):
                current["roles"].add(scope["role"])
        if action in {"leader_round_started", "debate_started"}:
            current.update(started=True, state="active")
        elif action in {"leader_round_finished", "debate_restarts_finished"}:
            current["state"] = "failed" if event.get("terminal_reason") == "AGENT_FINAL_FAILURE" else "completed"
        elif action in {"leader_aborted", "debate_aborted"}:
            current["state"] = "aborted"
        elif action in {"leader_failed", "debate_failed"}:
            current["state"] = "failed"
        elif action == "leader_not_started":
            current["state"] = "not_started"
        # A debate verdict can still require designated restart work. Keep its
        # observed active status until restarts-finished or a recorded abort.
    return leaders, debates


def _interaction_counts(records, role=None):
    values = [item for item in records.values() if role is None or role in item["roles"]]
    return {"started": sum(item["started"] for item in values),
        **{name: sum(item["state"] == name for item in values) for name in ("active", "completed", "failed", "aborted")}}


def _experiment_records(facts, transitions, agents, experts):
    owners, hypotheses, experiments = {}, {}, {}
    for expert in experts:
        for component in expert.get("owned_component_ids", ()):
            owners[component] = expert["role"]
    for row in facts:
        payload = _payload(row)
        if row["fact_type"] == "HypothesisProposed":
            hypotheses[payload["hypothesis_id"]] = payload.get("proposer_role")
    for row in transitions:
        if row.get("workflow_kind") == "EXPERIMENT":
            payload = _payload(row)
            record = experiments.setdefault(row["workflow_id"], {})
            record["state"] = row["state"]
            role = payload.get("role") or payload.get("proposer_role") or owners.get(payload.get("component_id"))
            if role:
                record["role"] = role
    for row in facts:
        payload = _payload(row)
        if row["fact_type"] not in {"ExperimentPreregistered", "CanonicalOutcomeAccepted"}:
            continue
        record = experiments.setdefault(payload["experiment_id"], {})
        if row["fact_type"] == "ExperimentPreregistered":
            record["role"] = hypotheses.get(payload.get("hypothesis_id")) or owners.get(payload.get("component_id"))
        else:
            record["outcome"] = payload.get("outcome")
    for row, _, request in agents:
        if row.get("experiment_id") in experiments:
            record = experiments[row["experiment_id"]]
            if not record.get("role"):
                record["role"] = request.get("role")
    return experiments


def _experiment_counts(records, role=None):
    values = [item for item in records.values() if role is None or item.get("role") == role]
    return {"total": len(values), "states": dict(Counter(item.get("state") or "UNKNOWN" for item in values)),
        "outcomes": dict(Counter(item["outcome"] for item in values if item.get("outcome")))}


def _chains(nodes, stages):
    grouped = {}
    for node in nodes:
        identity = (node.get("component_id"), node.get("cycle"), node.get("chain"))
        # Legacy accepted hypotheses have no recorded chain identity. Do not
        # manufacture one from a display position or a hypothesis ID.
        if identity[2] is None:
            continue
        group = grouped.setdefault(identity, {"component_id": identity[0], "cycle": identity[1],
            "chain": identity[2], "nodes": [], "stages": []})
        group["nodes"].append({"hypothesis_id": node["hypothesis_id"], "assessment": node.get("assessment"),
            "pruned": node.get("state") == "PRUNED" or bool(node.get("pruned_by"))})
    for stage in stages:
        identity = (stage["component_id"], stage["cycle"], stage["chain"])
        if identity[2] is None:
            continue
        group = grouped.setdefault(identity, {"component_id": identity[0], "cycle": identity[1],
            "chain": identity[2], "nodes": [], "stages": []})
        group["stages"].append(stage)
    return sorted(grouped.values(), key=lambda item: (str(item["component_id"]), item["cycle"] or 0, str(item["chain"])))


def _visible_stages(stages):
    opened = [item for item in stages if item["state"] == "OPEN"]
    if opened:
        return opened
    latest = {}
    for stage in stages:
        latest[(stage["component_id"], stage["chain"])] = stage
    return list(latest.values())


def _chain_coverage(nodes, stages, chains):
    missing = any(node.get("chain") is None for node in nodes) or any(
        stage["chain"] is None and stage["node"] in {
            "EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION", "RESULT_ANALYSIS"}
        for stage in stages)
    return "partial" if missing and chains else "unavailable" if missing else "complete"


def build_run_summary(state_root: str | Path, run_id: str, *, facts, transitions,
                      attempts, experts, graph, observed_at, created_at=None) -> dict[str, Any]:
    """Build all-run counts from complete transaction rows, never event pages."""
    from .observer_details import skill_catalog
    from .observer_reliability import build_reliability, summarize_reliability

    # Decode transaction rows once for all projections and expert subsets. These
    # are disposable local copies; no parsed value is retained across refreshes.
    facts = [{**row, "payload": _payload(row)} for row in facts]
    transitions = [{**row, "payload": _payload(row)} for row in transitions]
    attempts = [{**row, "observation": _observation(row)} if row.get("kind") == "AGENT" else row
        for row in attempts]
    observed = _time(observed_at)
    agents = list(_agents(attempts))
    stages = _logical_stages(agents, observed, state_root, run_id)
    literature = literature_catalog(state_root, run_id, attempts)
    skills = skill_catalog(state_root, run_id, attempts)
    leaders, debates = _interactions(transitions)
    experiments = _experiment_records(facts, transitions, agents, experts)
    run_rows = [row for row in transitions if row.get("workflow_kind") == "RUN"]
    terminal = next((row for row in run_rows if row.get("state") in _TERMINAL), None)
    interruptions = [_time(row.get("created_at")) for row in facts
        if row["fact_type"] == "GoalVerdictRecorded" and _payload(row).get("verdict") == "GOAL_MET"]
    interruptions.extend(_time(row.get("created_at")) for row in run_rows
        if row.get("state") == "STOPPING" and _payload(row).get("stop_disposition") == "CANCELLED")
    if terminal is not None and terminal.get("state") == "CANCELLED":
        interruptions.append(_time(terminal.get("created_at")))
    interrupted_at = min((value for value in interruptions if value is not None), default=None)
    reliability = build_reliability(state_root, run_id, attempts, run_terminal=terminal is not None,
        interrupted_at=interrupted_at)
    finish = _time(terminal["created_at"]) if terminal else observed
    created = _time(created_at)
    notices = []
    if created is None:
        first = next((row for row in run_rows if row.get("state") == "CREATED"), None)
        created = _time(first["created_at"]) if first else None
        notices.append("Run creation timestamp unavailable; elapsed time uses the CREATED transition when present.")
    elapsed = None if created is None or finish is None else max(0.0, finish - created)
    waiting = 0.0
    for index, row in enumerate(run_rows):
        if row.get("state") != "WAITING_HUMAN":
            continue
        start = _time(row.get("created_at"))
        end = _time(run_rows[index + 1].get("created_at")) if index + 1 < len(run_rows) else finish
        if start is not None and end is not None:
            waiting += max(0.0, min(end, finish) - start)
    goal = next((_payload(row) for row in facts if row["fact_type"] == "GoalDefined"), {})
    hours = goal.get("research_contract", {}).get("budget", {}).get("wallclock_hours")
    remaining = max(0.0, float(hours) * 3600 - elapsed) if isinstance(hours, (int, float)) and elapsed is not None else None
    activity = [_time(row.get("created_at")) for row in (*facts, *transitions)]
    activity.extend(_time(row.get("ended_at") or row.get("started_at")) for row in attempts)
    roles = list(dict.fromkeys([item["role"] for item in experts] + [request.get("role") for _, _, request in agents]
        + [node.get("proposer_role") for node in graph["nodes"]]))
    roles = [role for role in roles if role is not None]
    stages_by_role, nodes_by_role, reliability_by_role = defaultdict(list), defaultdict(list), defaultdict(list)
    for stage in stages:
        stages_by_role[stage["role"]].append(stage)
    for node in graph["nodes"]:
        nodes_by_role[node.get("proposer_role")].append(node)
    for node in reliability["nodes"]:
        reliability_by_role[node["role"]].append(node)
    expert_values = []
    for role in roles:
        own_stages = stages_by_role[role]
        nodes = nodes_by_role[role]
        chains = _chains(nodes, own_stages)
        own_activity = [item["ended_at"] or item["started_at"] for item in own_stages]
        hypothesis_ids = {node["hypothesis_id"] for node in nodes}
        experiment_ids = {identity for identity, item in experiments.items() if item.get("role") == role}
        for row in (*facts, *transitions):
            payload = _payload(row)
            if (payload.get("proposer_role") == role or payload.get("role") == role
                    or payload.get("hypothesis_id") in hypothesis_ids
                    or payload.get("experiment_id") in experiment_ids
                    or row.get("workflow_id") in experiment_ids):
                own_activity.append(_time(row.get("created_at")))
        own_activity.extend(_time(row.get("ended_at") or row.get("started_at"))
            for row in attempts if row.get("experiment_id") in experiment_ids)
        expert_values.append({"role": role, "cycle": max((item["cycle"] or 0 for item in own_stages), default=0),
            "accounting": _token_accounting(own_stages),
            "activity": _current_activity(own_stages, [row for row in attempts if row.get("kind") == "PROCESS"
                and (row.get("experiment_id") or row.get("workflow_id")) in experiment_ids], observed),
            "reliability": summarize_reliability(reliability_by_role[role],
                include_nodes=False, include_groups=True),
            "stages": _visible_stages(own_stages), "hypotheses": len({item["hypothesis_id"] for item in nodes}),
            "chains": chains, "chain_coverage": _chain_coverage(nodes, own_stages, chains),
            "active_chains": sum(any(stage["state"] == "OPEN" for stage in chain["stages"]) for chain in chains),
            "experiments": _experiment_counts(experiments, role), "literature": _literature_counts(literature, role),
            "skills": _skill_counts(skills, role), "evolutions": _evolutions(agents, role),
            "leader": _interaction_counts(leaders, role), "debate": _interaction_counts(debates, role),
            "last_activity_at": max((value for value in own_activity if value is not None), default=None)})
    hypotheses = {_payload(row)["hypothesis_id"] for row in facts if row["fact_type"] == "HypothesisProposed"}
    chain_count = sum(len(item["chains"]) for item in expert_values)
    chain_missing = any(item["chain_coverage"] != "complete" for item in expert_values)
    chain_coverage = "partial" if chain_missing and chain_count else "unavailable" if chain_missing else "complete"
    totals = {"experiments": _experiment_counts(experiments), "hypotheses": len(hypotheses),
        "chains": None if chain_coverage == "unavailable" else chain_count, "chain_coverage": chain_coverage,
        "active_chains": sum(item["active_chains"] for item in expert_values),
        "literature": _literature_counts(literature), "skills": _skill_counts(skills), "evolutions": _evolutions(agents),
        "leader": _interaction_counts(leaders), "debate": _interaction_counts(debates)}
    if any(item["skills"]["coverage"] != "complete" for item in expert_values):
        totals["skills"]["coverage"] = "partial" if totals["skills"]["count"] is not None else "unavailable"
    if any(row.get("state") == "OPEN" for row in attempts):
        notices.append("OPEN is durable Attempt state, not a process heartbeat; live subphases may be unknown.")
    notices.append("Reported command costs are observed lower bounds; reservations and unknown closed accounting are not actual spend.")
    unsettled = sum(stage["accounting"]["observed_unsettled_tokens"] for stage in stages)
    if unsettled:
        notices.append(f"An additional {unsettled:,} tokens were observed in unsettled calls; they are not included in reported usage or confirmed USD cost.")
    retry_times = []
    for _, value, _ in agents:
        diagnostics = value.get("diagnostics")
        failure = diagnostics.get("provider_failure") if isinstance(diagnostics, dict) else None
        retry_at = _time(failure.get("retry_at")) if isinstance(failure, dict) else None
        if isinstance(failure, dict) and failure.get("code") == "rate_limited" and retry_at is not None:
            retry_times.append(retry_at)
    retry_at = max(retry_times, default=None)
    if terminal is None and retry_at is not None and observed is not None and retry_at > observed:
        notices.append(f"Provider rate limit: new Agent calls are waiting; retry in {math.ceil(retry_at - observed)} seconds.")
    if literature["coverage"] != "complete":
        notices.append("Literature packet evidence is unavailable or incomplete; counts are observed coverage, not papers read.")
    if totals["skills"]["coverage"] != "complete":
        notices.append("Accepted or bound plugin evidence is unavailable or incomplete.")
    if totals["evolutions"]["changed"] is None:
        notices.append("Successful self-evolution lacks retained plugin-update evidence; effective change count is unknown.")
    host_observation = _host_observation(state_root)
    host_observation["orphaned"] = terminal is None and host_observation["availability"] == "absent"
    return {"run_id": run_id, "observed_at": observed_at, "elapsed_seconds": elapsed,
        "reliability": reliability, "accounting": _token_accounting(stages),
        "host_observation": host_observation, "stages": stages,
        "waiting_human_seconds": waiting, "remaining_seconds": remaining,
        "last_activity_at": max((value for value in activity if value is not None), default=created),
        "totals": totals, "experts": expert_values, "notices": notices}
