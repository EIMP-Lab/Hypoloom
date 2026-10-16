"""Read-only cross-run comparison derived from Attempts and provider captures."""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
import json
import math
from pathlib import Path
import sqlite3

from .agent_budget import fold_agent_attempts
from .observer_details import _path
from .observer_reliability import build_reliability, summarize_reliability, _time
from .provider_observation import read_exchanges


def _trace(*args):
    return {"coverage": "unavailable", "required_action": {"status": "unknown"}}


def _distribution(values):
    values = sorted(value for value in values if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0)
    def percentile(p):
        if not values:
            return None
        index = (len(values) - 1) * p
        lower = int(index)
        return values[lower] + (values[min(lower + 1, len(values) - 1)] - values[lower]) * (index - lower)
    return {"observed": len(values), "p50": percentile(.5), "p95": percentile(.95)}


def _runs(root):
    database = root / "state.sqlite"
    if not database.is_file():
        raise ValueError(f"Hypoloom state database is absent: {root}")
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        runs = [dict(row) for row in connection.execute("SELECT run_id FROM workflow_runs ORDER BY run_id")]
        for run in runs:
            run_id = run["run_id"]
            run["attempts"] = [dict(row) for row in connection.execute(
                "SELECT attempt_id,run_id,workflow_id,command_id,kind,attempt_ordinal,state,terminal_reason,started_at,ended_at,"
                "json_remove(observation_json,'$.request.input_artifacts','$.response.proposed_workspace_artifacts',"
                "'$.diagnostics.provider_structured_candidate') AS observation_json "
                "FROM execution_attempts WHERE run_id=? AND kind='AGENT' ORDER BY started_at,attempt_id", (run_id,))]
            transitions = [dict(row) for row in connection.execute(
                "SELECT state,created_at,payload_json FROM workflow_transitions WHERE run_id=? AND workflow_kind='RUN' ORDER BY revision", (run_id,))]
            run["terminal"] = bool(transitions) and transitions[-1]["state"] in {"COMPLETED", "CANCELLED", "FAILED"}
            stops = [_time(row["created_at"]) for row in transitions if row["state"] == "CANCELLED" or (
                row["state"] == "STOPPING" and json.loads(row["payload_json"]).get("stop_disposition") == "CANCELLED")]
            stops.extend(_time(row[0]) for row in connection.execute(
                "SELECT created_at FROM research_facts WHERE run_id=? AND fact_type='GoalVerdictRecorded' "
                "AND json_extract(payload_json,'$.verdict')='GOAL_MET'", (run_id,)))
            run["interrupted_at"] = min((value for value in stops if value is not None), default=None)
        return runs
    finally:
        connection.close()


def compare_roots(state_roots):
    groups = defaultdict(list)
    sources = []
    notices = []
    for root in dict.fromkeys(Path(value).expanduser().resolve() for value in state_roots):
        for run in _runs(root):
            run_id, rows = run["run_id"], run["attempts"]
            sources.append({"state_root": str(root), "run_id": run_id})
            reliability = build_reliability(root, run_id, rows, run_terminal=run["terminal"],
                interrupted_at=run["interrupted_at"], evidence_reader=_trace)
            notices = reliability["notices"]
            by_id = {row["attempt_id"]: row for row in rows}
            for node in reliability["nodes"]:
                records, exchanges, models = [], [], set()
                missing = no_provider = 0
                unknown_model = False
                for attempt in node["attempts"]:
                    row = by_id[attempt["attempt_id"]]
                    records.append(row)
                    captured = read_exchanges(_path(root, "diagnostics", "agents", run_id, row["attempt_id"], "provider"))
                    exchanges.extend(captured)
                    observation = json.loads(row["observation_json"])
                    diagnostics = observation.get("diagnostics", {})
                    preflight_only = not captured and observation.get("failure_stage", diagnostics.get("failure_stage")) == "literature_retrieval"
                    served = {model for exchange in captured for model in exchange.get("served_models", []) if isinstance(model, str)}
                    if not served:
                        served = {model for model in diagnostics.get("served_models", []) if isinstance(model, str)}
                    models.update(served)
                    unknown_model |= not served and not preflight_only
                    missing += not bool(captured) and not preflight_only
                    no_provider += preflight_only
                identity = tuple(sorted(models))
                groups[(identity, unknown_model, node["node"])].append((node, records, exchanges, missing, no_provider))
    result = []
    for (models, unknown_model, kind), members in sorted(groups.items()):
        nodes = [item[0] for item in members]
        rows = [row for item in members for row in item[1]]
        exchanges = [exchange for item in members for exchange in item[2]]
        charges = fold_agent_attempts(rows)
        reported = [charge for charge in charges if charge.source != "declared"]
        summary = summarize_reliability(nodes, include_nodes=False, include_groups=False)
        delivered = summary["metrics"]["node_delivery"]["numerator"]
        costs = sum((charge.cost for charge in reported if charge.cost is not None), Decimal(0))
        tokens = sum(charge.tokens or 0 for charge in reported)
        turns = sum(charge.turns or 0 for charge in reported)
        failures, subtypes, validation = Counter(), Counter(), Counter()
        cache_values = {key: [] for key in ("uncached_input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")}
        for row in rows:
            observation = json.loads(row["observation_json"])
            diagnostics = observation.get("diagnostics", {})
            if observation.get("failure_code"):
                failures[observation["failure_code"]] += 1
            if diagnostics.get("provider_subtype"):
                subtypes[diagnostics["provider_subtype"]] += 1
            for correction in diagnostics.get("structured_output_correction", {}).get("rounds", []):
                error = correction.get("validation_error")
                if isinstance(error, str):
                    # Diagnostic categories, never raw candidate values or bodies.
                    label = "enum" if "enum constraint" in error else "json" if "invalid JSON" in error else "other"
                    validation[label] += 1
            for key, values in cache_values.items():
                value = diagnostics.get(key)
                if type(value) is int and value >= 0:
                    values.append(value)
        durations, ttfts = [], []
        for exchange in exchanges:
            start, end, first = (_time(exchange.get(key)) for key in ("started_at", "ended_at", "first_content_at"))
            if start is not None and end is not None:
                durations.append(end - start)
            if start is not None and first is not None:
                ttfts.append(first - start)
        label = " + ".join((*models, *(('unknown',) if unknown_model else ()))) or "no provider invocation"
        result.append({"served_models": list(models), "model_label": label, "unknown_model": unknown_model,
            "node": kind, "selectors": sorted({node["model"] for node in nodes}), "logical_nodes": len(nodes),
            **summary, "reported_cost_usd": str(costs), "reported_tokens": tokens, "reported_turns": turns,
            "unknown_accounting_attempts": sum(charge.source == "declared" for charge in charges),
            "per_delivered_node": {"cost_usd": str(costs / delivered) if delivered else None,
                "tokens": tokens / delivered if delivered else None, "turns": turns / delivered if delivered else None,
                "exchanges": len(exchanges) / delivered if delivered else None},
            "cache_usage": {key: {"observed_total": sum(values), "known_attempts": len(values),
                "unknown_attempts": len(rows) - len(values)} for key, values in cache_values.items()},
            "failure_codes": dict(failures), "provider_subtypes": dict(subtypes), "validation_failures": dict(validation),
            "exchanges": {"observed": len(exchanges), "unknown_attempts": sum(item[3] for item in members),
                "preflight_only_attempts": sum(item[4] for item in members),
                "status_counts": dict(Counter(str(item.get("upstream_status", "unknown")) for item in exchanges)),
                "non_200": sum(type(item.get("upstream_status")) is int and item["upstream_status"] != 200 for item in exchanges),
                "incomplete_bodies": sum(item.get("response_body_complete") is False for item in exchanges),
                "incomplete_messages": sum(item.get("request_path") == "/v1/messages" and item.get("message_complete") is not True for item in exchanges),
                "duration_seconds": _distribution(durations), "ttft_seconds": _distribution(ttfts)}})
    return {"sources": sources, "groups": result, "notices": [*notices,
        "Served models are upstream-reported labels, not independently attested model identity; mixed-model nodes remain mixed.",
        "Per-delivery reported tokens include failed work. Unknown usage is not zero; reservations are not measured consumption, and these observations do not establish savings.",
        "Old captures cannot reconstruct TTFT. Missing cache usage and exchanges remain unknown. Tool evidence is not read by this comparison."]}


def as_markdown(comparison):
    lines = ["Provider reliability comparison", "", "| Served model | Node | Delivery | First output | Retry | Reported tokens | Tokens/delivery |", "|---|---|---:|---:|---:|---:|---:|"]
    def rate(metric):
        return f"{metric['numerator']}/{metric['denominator']} (unknown {metric['unknown']})"
    for group in comparison["groups"]:
        metrics = group["metrics"]
        lines.append("| " + " | ".join(str(value).replace("|", "\\|").replace("\n", " ") for value in (
            group["model_label"], group["node"], rate(metrics["node_delivery"]), rate(metrics["output_first_pass"]),
            rate(metrics["node_retry"]), group["reported_tokens"],
            group["per_delivered_node"]["tokens"] if group["per_delivered_node"]["tokens"] is not None else "unknown")) + " |")
    lines.extend(["", *[f"- {notice}" for notice in comparison["notices"]]])
    return "\n".join(lines) + "\n"
