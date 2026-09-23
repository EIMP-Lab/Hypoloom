"""Read-only product APIs derived from existing rows, not a new event store."""
from __future__ import annotations

import json
import os
import stat
from contextlib import nullcontext
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .agent_budget import charged_cost, fold_agent_attempts
from .monitor import MonitorError, build_monitor_snapshot
from .state_store import StateStore

PRODUCT_EVENT_TYPES = frozenset({"run_started", "run_finished", "hypothesis_added", "hypothesis_verified",
    "hypothesis_refuted", "experiment_submitted", "experiment_started", "experiment_finished",
    "experiment_failed", "stage_started", "stage_finished", "expert_round_done", "error", "budget_warning",
    "expert_evolved", "batch_adjudicated", "hypothesis_pruned", "final_report_ready",
    "leader_selected", "leader_started", "leader_finished", "leader_not_started", "graph_evolved",
    "debate_selected", "debate_started", "debate_finished", "debate_restarts_finished", "debate_restarts_designated", "debate_failed",
    "leader_aborted", "debate_aborted",
    "new_best_result", "goal_verdict_recorded",
    "provider_auth_cooldown", "provider_auth_resumed",
    "provider_waiting", "provider_retry_requested", "provider_recovered",
    "participant_pause_requested", "participant_resumed", "operator_note_injected",
    "grounding_question", "grounding_answered"})


def _payload(row):
    return row["payload"] if "payload" in row else json.loads(row.get("payload_json") or "{}")


def _event(source: str, identity: str, revision: int, kind: str, at, run_id: str, **payload):
    return {"event_id": f"{source}:{identity}:{revision}", "source": source,
            "source_id": identity, "revision": revision, "type": kind,
            "created_at": at, "run_id": run_id, "payload": payload}


def _interaction(row):
    if (row["workflow_kind"] == "RUN" and row.get("command_type", row.get("command"))
            in {"ACCEPT_EXPERT_INTERACTION", "RESUME_PARTICIPANT"}):
        return _payload(row).get("expert_interaction", {})
    return {}


def _pruning(row):
    if row["workflow_kind"] != "RUN": return ()
    if row.get("command_type", row.get("command")) == "ACCEPT_ADJUDICATION":
        return _payload(row).get("adjudication", {}).get("pruned_by", ())
    interaction = _interaction(row)
    return interaction.get("pruned_by", ()) if interaction.get("action") == "debate_verdict" else ()


def _measurement_group(spec, commands):
    fields = {name: spec.get(name) for name in ("component_id", "metric", "measurement", "trial_plan", "upstream_outcomes", "evaluator_version")}
    parser = ()
    if declaration := spec.get("measurement"):
        from .agent_aci import WorkspaceArtifact
        try:
            command = commands[spec["execution_contract"]["source_submit_command_id"]]
            source = command.get("request") or json.loads(command["request_json"])
            if command["command_type"] != "CREATE_EXPERIMENT" or command["response_status"] != "accepted" or source["tool"] != "submit_experiment":
                raise ValueError("measurement parser lacks an accepted submit source")
            files = {item["path"]: WorkspaceArtifact.from_dict(item).content_bytes()
                for item in source["source_snapshot"]["arguments"]["workspace_files"]}
            parser = tuple((name, files[name]) for name in declaration["parser_files"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("best-result comparison cannot resolve its frozen parser bytes") from exc
    return json.dumps(fields, sort_keys=True, separators=(",", ":")), parser


def _best_results(run_id, facts, commands):
    specs = {_payload(row)["experiment_id"]: (row["fact_id"], _payload(row)) for row in facts
        if row["fact_type"] == "ExperimentPreregistered"}
    best, events = {}, []
    for row in facts:
        outcome = _payload(row)
        if (row["fact_type"] != "CanonicalOutcomeAccepted" or outcome.get("result_kind") != "metric"
                or outcome.get("outcome") not in {"SUCCESS", "CLEAN_REFUTATION"}): continue
        selected = specs.get(outcome["experiment_id"])
        if selected is None: raise ValueError("best-result comparison lacks an accepted preregistration")
        spec_ref, spec = selected
        if spec["component_id"] != outcome["component_id"]: raise ValueError("best-result component differs from preregistration")
        metric = spec["metric"]
        try: value = Decimal(str(outcome["metric_value"]))
        except (KeyError, InvalidOperation) as exc: raise ValueError("best-result metric is not a finite decimal") from exc
        if not value.is_finite() or metric["direction"] not in {"MINIMIZE", "MAXIMIZE"}: raise ValueError("best-result metric/direction is invalid")
        group = _measurement_group(spec, commands)
        previous = best.get(group)
        if previous is not None and (value <= previous[0] if metric["direction"] == "MAXIMIZE" else value >= previous[0]): continue
        best[group] = (value, outcome)
        events.append(_event("best-result", row["fact_id"], 1, "new_best_result", row["created_at"], run_id,
            component_id=outcome["component_id"], experiment_id=outcome["experiment_id"], outcome_id=outcome["outcome_id"],
            fact_id=row["fact_id"], preregistration_ref=spec_ref, metric=metric, metric_value=outcome["metric_value"],
            comparison_scope="component_and_frozen_evaluation", previous_outcome_id=None if previous is None else previous[1]["outcome_id"]))
    return events


def _provider_auth_events(run_id, facts, transitions, attempts):
    """Provider events use the Host's reducer and existing durable rows."""
    from .provider_observation import PROVIDER_UNAVAILABLE, provider_retry_gate
    from .agent_budget import agent_attempt_limits
    goal = next((row for row in facts if row["fact_type"] == "GoalDefined"), None)
    if goal is None: return []
    document = _payload(goal).get("research_contract", {})
    cooldown = document.get("budget", {}).get("provider_auth_cooldown_seconds", 18000)
    if type(cooldown) not in (int, float) or not 0 <= float(cooldown) <= 7 * 86400:
        cooldown = 18000.0
    limit = agent_attempt_limits(document, model_fallback=3, infrastructure_fallback=3)[1]
    points = []
    for row in attempts:
        if row["kind"] != "AGENT" or row["state"] != "CLOSED" or type(row.get("ended_at")) not in (int, float): continue
        observation = row.get("observation") or json.loads(row.get("observation_json") or "{}")
        diagnostics = observation.get("diagnostics", {})
        failure = diagnostics.get("provider_failure", {})
        success = row.get("terminal_reason") == "SUCCEEDED" or (not failure and diagnostics.get("provider_observed_usage", {}).get("completed_requests", 0) > 0)
        if failure.get("code") not in PROVIDER_UNAVAILABLE and not success: continue
        entry = {**failure, "attempt_id": row["attempt_id"], "started_at": row.get("started_at", row["ended_at"]),
            "ended_at": row["ended_at"], "state": "CLOSED", "provider_success": success}
        points.append((row["ended_at"], 0, row["attempt_id"], entry))
    for row in transitions:
        clear = _payload(row).get("provider_retry_clear", {})
        if row["workflow_kind"] == "RUN" and type(clear.get("cleared_at")) in (int, float):
            points.append((clear["cleared_at"], 1, row["command_id"], row))
    points.sort(key=lambda p: (p[0], p[1], p[2]))
    events, entries, cleared, signature = [], [], None, ("ready", None)
    for at, kind, identity, row in points:
        if kind == 1:
            cleared = at
            events.append(_event("provider-control", identity, row["revision"], "provider_retry_requested", at, run_id,
                connection_changed=bool(_payload(row)["provider_retry_clear"].get("settings_path"))))
            signature = ("recovering", None)
            continue
        entries.append(row)
        status = provider_retry_gate(entries, cooldown_seconds=cooldown, cleared_at=cleared, now=at, attempt_limit=limit)
        current = (status["state"], status["reason"])
        if current == signature: continue
        if status["state"] == "ready" and signature[0] != "ready":
            events.append(_event("provider", identity, 1, "provider_recovered", at, run_id))
        elif status["state"] in {"cooldown", "waiting_operator"}:
            events.append(_event("provider", identity, 1, "provider_waiting", at, run_id,
                reason=status["reason"], requires_operator=status["requires_operator"],
                next_retry_at=status["next_retry_at"], http_status=row.get("http_status")))
        signature = current
    return events


def product_events(run_id: str, facts, transitions, attempts, *, commands=None) -> list[dict[str, Any]]:
    """Name only observed product events; never infer an accepted scientific fact."""
    result = _best_results(run_id, facts, commands or {})
    result.extend(_provider_auth_events(run_id, facts, transitions, attempts))
    for row in facts:
        payload = _payload(row)
        kind = {"GoalDefined": "run_started", "HypothesisProposed": "hypothesis_added",
                "CanonicalOutcomeAccepted": "experiment_finished",
                "GoalVerdictRecorded": "goal_verdict_recorded"}.get(row["fact_type"])
        if row["fact_type"] == "HypothesisAssessed":
            kind = {"SUPPORTED": "hypothesis_verified", "REFUTED": "hypothesis_refuted"}.get(payload.get("assessment"))
        if row["fact_type"] == "CanonicalOutcomeAccepted" and payload.get("outcome") in {"EXECUTION_FAILURE", "INTEGRITY_REJECTED", "CANCELLED"}:
            kind = "experiment_failed"
        if kind:
            values = {key: payload[key] for key in ("hypothesis_id", "experiment_id", "outcome_id", "outcome", "assessment", "verdict", "proposer_role", "component_id") if key in payload}
            result.append(_event("fact", row["fact_id"], 1, kind, row["created_at"], run_id, fact_id=row["fact_id"], **values))
    for row in transitions:
        kind = None
        payload = _payload(row)
        if row["workflow_kind"] == "RUN" and payload.get("kind") == "grounding_clarification":
            action = payload.get("human_decision_command")
            if action in {"OPEN_DECISION", "RESOLVE"}:
                result.append(_event("grounding-dialogue", row["workflow_id"], row["revision"],
                    "grounding_question" if action == "OPEN_DECISION" else "grounding_answered", row["created_at"], run_id,
                    decision_id=payload["decision_id"], ordinal=payload["ordinal"], command_id=row["command_id"]))
        if (row["workflow_kind"] == "RUN" and row.get("state") in {"COMPLETED", "FAILED", "CANCELLED"}
                and row.get("previous_state") != row["state"]):
            verdict = next((item for item in facts if item["fact_type"] == "GoalVerdictRecorded"), None)
            result.append(_event("run-terminal", row["workflow_id"], row["revision"], "run_finished",
                row["created_at"], run_id, state=row["state"], command_id=row["command_id"],
                verdict_ref=None if verdict is None else verdict["fact_id"]))
        control = payload.get("participant_control")
        if control:
            kind = {"pause": "participant_pause_requested", "resume": "participant_resumed", "inject": "operator_note_injected"}[control["action"]]
            result.append(_event("participant-control", row["workflow_id"], row["revision"], kind,
                row["created_at"], run_id, scope=control["scope"], reason=control["reason"], command_id=row["command_id"]))
            kind = None
        if (row["workflow_kind"] == "RUN" and row.get("command_type", row.get("command")) == "ACCEPT_ADJUDICATION"
                and (adjudication := payload.get("adjudication")) is not None):
            result.append(_event("batch-adjudication", row["workflow_id"], row["revision"], "batch_adjudicated",
                row["created_at"], run_id, role=payload.get("role"), component_id=payload.get("component_id"),
                cycle=adjudication["cycle"], selected_tip=adjudication["selected_tip"], source_attempt_id=payload.get("agent_attempt_id")))
        interaction = _interaction(row)
        named = {"leader_selected": "leader_selected", "leader_round_started": "leader_started",
            "leader_round_finished": "leader_finished", "leader_not_started": "leader_not_started",
            "debate_proposed": "debate_selected", "debate_started": "debate_started",
            "debate_verdict": "debate_finished", "debate_restarts_finished": "debate_restarts_finished",
            "debate_restarts_designated": "debate_restarts_designated",
            "debate_failed": "debate_failed", "debate_aborted": "debate_aborted",
            "leader_aborted": "leader_aborted"}.get(interaction.get("action"))
        if named:
            result.append(_event("interaction", row["workflow_id"], row["revision"], named,
                row["created_at"], run_id, command_id=row["command_id"], **interaction))
        if row["workflow_kind"] == "RUN" and row.get("command_type", row.get("command")) == "ACCEPT_GRAPH_EVOLUTION":
            result.append(_event("graph-evolution", row["workflow_id"], row["revision"], "graph_evolved",
                row["created_at"], run_id, source_attempt_id=payload["agent_attempt_id"],
                graph_evolution=payload["graph_evolution"], plugin_update=payload["plugin_update"]))
        for relation in _pruning(row):
            result.append(_event("pruning", relation["loser_hypothesis_id"], row["revision"], "hypothesis_pruned",
                row["created_at"], run_id, hypothesis_id=relation["loser_hypothesis_id"], **relation))
        if row["workflow_kind"] == "EXPERIMENT":
            if row["previous_state"] is None: kind = "experiment_submitted"
            elif row["state"] == "EXECUTING" and row["previous_state"] != "EXECUTING": kind = "experiment_started"
        if kind:
            result.append(_event("workflow", row["workflow_id"], row["revision"], kind,
                row["created_at"], run_id, experiment_id=row["workflow_id"], command_id=row["command_id"]))
    for row in attempts:
        if row["kind"] != "AGENT": continue
        observation = (row["observation"] if "observation" in row else json.loads(row["observation_json"] or "{}"))
        request = observation.get("request", {})
        values = {"attempt_id": row["attempt_id"], "role": request.get("role"),
                  "node": request.get("command"), "task": request.get("task_context")}
        result.append(_event("attempt", row["attempt_id"], 1, "stage_started", row["started_at"], run_id, **values))
        if row["state"] == "CLOSED":
            result.append(_event("attempt", row["attempt_id"], 2, "stage_finished", row["ended_at"], run_id,
                terminal_reason=row["terminal_reason"], **values))
            extra = []
            if request.get("command") == "CYCLE_REPORT" and row["terminal_reason"] == "SUCCEEDED": extra.append("expert_round_done")
            if request.get("command") == "FINAL_REPORT" and row["terminal_reason"] == "SUCCEEDED": extra.append("final_report_ready")
            update = observation.get("plugin_update", {})
            if (request.get("command") == "SELF_EVOLVE" and row["terminal_reason"] == "SUCCEEDED"
                    and update.get("commit") and update.get("commit") != update.get("base_commit")):
                result.append(_event("attempt-expert-evolved", row["attempt_id"], 2, "expert_evolved",
                    row["ended_at"], run_id, plugin_update=update, **values))
            if row["terminal_reason"] in {"FAILED", "LOST"}: extra.append("error")
            if observation.get("failure_code") == "profile_budget_exhausted": extra.append("budget_warning")
            for kind in extra:
                # Multiple views of one immutable source must have distinct
                # notification cursors, without inventing a second event store.
                result.append(_event("attempt-" + kind, row["attempt_id"], 2, kind, row["ended_at"], run_id,
                    terminal_reason=row["terminal_reason"], error_code=observation.get("failure_code"), **values))
    # Per-source progress, not timestamp ordering, is the delivery identity.
    def timestamp(value):
        if isinstance(value, (int, float)): return float(value)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    return sorted(result, key=lambda item: (timestamp(item["created_at"]), item["source"], item["source_id"], item["revision"]))


def product_graph(hypotheses, transitions) -> dict[str, Any]:
    """Overlay accepted control relationships; retain scientific assessments."""
    nodes = [{**item, "state": item.get("assessment") or "PROPOSED"} for item in hypotheses]
    indexed = {item["hypothesis_id"]: item for item in nodes}
    edges = [{"source": item["parent_hypothesis_id"], "target": item["hypothesis_id"], "relation": item["relation"]}
             for item in nodes if item.get("parent_hypothesis_id")]
    selected = {}
    for row in transitions:
        if row["workflow_kind"] != "RUN": continue
        payload = _payload(row)
        command = row.get("command_type", row.get("command"))
        if command == "ACCEPT_HYPOTHESIS" and (batch := payload.get("hypothesis_batch")):
            for chain, identity in batch["chains"].items():
                if identity in indexed: indexed[identity].update(chain=chain, cycle=batch["cycle"])
        if command == "ACCEPT_ADJUDICATION" and (adjudication := payload.get("adjudication")):
            selected[(payload.get("component_id"), payload.get("role"))] = adjudication.get("selected_tip")
        for relation in _pruning(row):
            loser, winner = relation["loser_hypothesis_id"], relation["winner_hypothesis_id"]
            edge = {"source": winner, "target": loser, "relation": "pruned_by", **relation}
            edges.append(edge)
            if loser in indexed:
                indexed[loser]["state"] = "PRUNED"
                indexed[loser].setdefault("pruned_by", []).append(edge)
    for tip in selected.values():
        if tip and tip["node_id"] in indexed: indexed[tip["node_id"]]["selected_tip"] = True
    return {"nodes": nodes, "edges": edges}


def build_product_observation(state_root: str | Path, run_id: str, *,
                              _connection=None, _snapshot=None) -> dict[str, Any]:
    root = Path(state_root).resolve()
    with (StateStore(root).readonly_connection() if _connection is None else nullcontext(_connection)) as db:
        if _connection is None: db.execute("BEGIN")
        snapshot = build_monitor_snapshot(root, run_id, _connection=db).to_dict() if _snapshot is None else _snapshot
        if snapshot.get("run_id") != run_id: raise ValueError("observation run mismatch")
        facts = [dict(row) for row in db.execute("SELECT * FROM research_facts WHERE run_id=? ORDER BY rowid", (run_id,))]
        transitions = [dict(row) for row in db.execute(
            "SELECT t.*, c.command_type FROM workflow_transitions t JOIN workflow_commands c USING(command_id) "
            "WHERE t.run_id=? ORDER BY t.rowid", (run_id,))]
        attempts = [dict(row) for row in db.execute("SELECT * FROM execution_attempts WHERE run_id=? ORDER BY started_at,attempt_id", (run_id,))]
        commands = {row["command_id"]: dict(row) for row in db.execute("SELECT * FROM workflow_commands WHERE run_id=?", (run_id,))}
    charges = fold_agent_attempts(attempts)
    observations = {row["attempt_id"]: (row["observation"] if "observation" in row else json.loads(row["observation_json"] or "{}")) for row in attempts}
    by_role, by_experiment = {}, {}
    for row in attempts:
        by_role.setdefault(observations[row["attempt_id"]].get("request", {}).get("role"), []).append(row)
        by_experiment.setdefault(row["experiment_id"], []).append(row)
    outcomes = {row["experiment_id"]: json.loads(row["payload_json"]) for row in facts
                if row["fact_type"] == "CanonicalOutcomeAccepted"}
    from .participant_controls import fold_participant_controls
    controls = fold_participant_controls(transitions, [item["role"] for item in snapshot["experts"]]).to_dict() if snapshot["experts"] else {"pause_requested": [], "operator_notes": []}
    experts = []
    for expert in snapshot["experts"]:
        role = expert["role"]
        own = by_role.get(role, [])
        session = next((observations[row["attempt_id"]]["session"] for row in reversed(own)
                        if observations[row["attempt_id"]].get("session")), {})
        try: cost = str(charged_cost(charges, role))
        except ValueError: cost = None
        experts.append({**expert, "cost_charged_or_reserved_usd": cost,
                        "pause_requested": [item for item in controls["pause_requested"] if item["scope"]["role"] == role],
                        "plugin_commit": session.get("expert_resources", {}).get("plugin", {}).get("commit"),
                        "session_id": session.get("session_id")})
    heads = {row["workflow_id"]: row for row in transitions if row["workflow_kind"] == "EXPERIMENT"}
    experiments = []
    for identity, head in heads.items():
        own = by_experiment.get(identity, [])
        outcome = outcomes.get(identity)
        experiments.append({"experiment_id": identity, "state": head["state"], "revision": head["revision"],
            "outcome": outcome, "attempts": [{key: row[key] for key in ("attempt_id", "kind", "state", "terminal_reason", "started_at", "ended_at")} for row in own]})
    from .final_report import accepted_report_view
    return {"run_id": run_id, "observed_at": snapshot["observed_at"], "summary": snapshot["overview"],
            "graph": product_graph(snapshot["hypotheses"], transitions), "experts": experts,
            "experiments": experiments, "participant_controls": controls, "final_report": accepted_report_view(run_id, attempts),
            "events": product_events(run_id, facts, transitions, attempts, commands=commands)}


def read_experiment_log(state_root: str | Path, run_id: str, experiment_id: str,
                        *, stream: str = "stdout", limit: int = 65536) -> dict[str, Any]:
    if stream not in {"stdout", "stderr"} or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1024 * 1024:
        raise ValueError("invalid bounded log selection")
    root = Path(state_root).resolve()
    with StateStore(root).readonly_connection() as db:
        if db.execute("SELECT 1 FROM workflow_transitions WHERE run_id=? AND workflow_id=? AND workflow_kind='EXPERIMENT' LIMIT 1", (run_id, experiment_id)).fetchone() is None:
            raise MonitorError("experiment does not belong to this run")
        attempt = db.execute("SELECT attempt_id FROM execution_attempts WHERE run_id=? AND experiment_id=? AND kind='PROCESS' ORDER BY attempt_ordinal DESC,started_at DESC LIMIT 1", (run_id, experiment_id)).fetchone()
    value = {"experiment_id": experiment_id, "stream": stream, "attempt_id": None if attempt is None else attempt["attempt_id"],
             "available": False, "text": "", "offset": 0, "size": 0, "truncated": False}
    if attempt is None: return value
    identity = attempt["attempt_id"]
    if not isinstance(identity, str) or identity in {".", ".."} or "/" in identity or "\\" in identity:
        raise MonitorError("invalid Process log identity")
    path = root
    for part in ("artifacts", "result-staging", identity, "logs", stream + ".log"):
        path = path / part
        if path.is_symlink(): raise MonitorError("Process log path must not follow symlinks")
    try:
        with path.open("rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode): raise MonitorError("Process log is not regular")
            size = metadata.st_size; offset = max(0, size - limit); handle.seek(offset)
            content = handle.read(limit)
    except FileNotFoundError: return value
    return {**value, "available": True, "text": content.decode("utf-8", errors="replace"),
            "offset": offset, "size": size, "truncated": offset > 0}
