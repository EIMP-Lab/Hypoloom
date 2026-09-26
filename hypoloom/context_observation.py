"""Read-only context telemetry from existing transcripts, captures and charges.

These projections never authorize work or settle billing. Unknown and bounded
coverage remain explicit; repeated reads count only successful exact ranges of
the same frozen input, not arbitrary shell commands or mutable source files.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
import json
from pathlib import Path


def _rows(content):
    for line in content.splitlines():
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError):
            if line.strip():
                yield None
            continue
        if isinstance(row, dict):
            yield row


def native_status(content):
    """Only the CLI's explicit status events establish an ongoing compaction."""
    status = None
    for row in _rows(content):
        if row is None:
            continue
        if row.get("type") == "system" and row.get("subtype") == "status":
            status = "compacting" if row.get("status") == "compacting" else None
        elif row.get("subtype") == "compact_boundary" or row.get("type") in {"assistant", "result"}:
            status = None
    return status


def read_native_status(path):
    try:
        path = Path(path)
        if path.is_symlink():
            return None
        with path.open("rb") as source:
            size = path.stat().st_size
            if size > 1024 * 1024:
                source.seek(size - 1024 * 1024)
                source.readline()
            return native_status(source.read(1024 * 1024))
    except (OSError, ValueError):
        return None


def transcript_metrics(content, session_id, started_at, input_refs=(), *, partial=False):
    tools, seen_events, seen_results = {}, set(), set()
    reads, errors, boundaries, ranges = 0, 0, [], Counter()
    unknown_pricing = None
    for row in _rows(content):
        if row is None:
            partial = True
            continue
        if row.get("sessionId") != session_id:
            continue
        if row.get("type") == "cost-state" and type(row.get("hasUnknownModelCost")) is bool:
            # This CLI flag describes its own session price catalogue, not
            # settled invocation billing or a verified vendor invoice.
            unknown_pricing = row["hasUnknownModelCost"]
        message = row.get("message") or {}
        blocks = message.get("content") if isinstance(message, dict) else None
        boundary = row.get("type") == "system" and row.get("subtype") == "compact_boundary"
        if not boundary and not any(isinstance(block, dict) and block.get("type") in {"tool_use", "tool_result"}
                for block in (blocks if isinstance(blocks, list) else ())):
            continue  # CLI cost/latch records have no event time and are not reads.
        try:
            stamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).timestamp()
        except (KeyError, TypeError, ValueError, AttributeError):
            partial = True
            continue
        if stamp < started_at:
            continue  # Resumed sessions must not recharge previous invocations.
        uid = row.get("uuid")
        if uid and uid in seen_events:
            continue
        if uid:
            seen_events.add(uid)
        if boundary:
            metadata = row.get("compactMetadata") or {}
            boundaries.append({"timestamp": stamp, **{target: metadata.get(source)
                if type(metadata.get(source)) is int and metadata[source] >= 0 else None
                for source, target in (("preTokens", "pre_tokens"), ("postTokens", "post_tokens"), ("durationMs", "duration_ms"))}})
        for block in blocks if isinstance(blocks, list) else ():
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "Read" and isinstance(block.get("id"), str):
                tools[block["id"]] = block.get("input") or {}
            if block.get("type") != "tool_result" or block.get("tool_use_id") not in tools:
                continue
            identity = block["tool_use_id"]
            if identity in seen_results:
                continue
            seen_results.add(identity)
            if block.get("is_error") is True:
                errors += 1
                continue
            reads += 1
            args = tools[identity]
            if not isinstance(args, dict):
                continue
            path = args.get("file_path")
            for index, ref in enumerate(input_refs):
                if path != f"/inputs/{index:04d}.txt":
                    continue
                offset, limit = args.get("offset", 1), args.get("limit")
                pages = args.get("pages")
                if type(offset) is int and offset >= 1 and (limit is None or type(limit) is int and limit > 0) and pages is None:
                    ranges[(ref, offset, limit)] += 1
                break
    entries = [{"ref": ref, "offset": offset, "limit": limit, "count": count}
        for (ref, offset, limit), count in ranges.items()]
    return {"coverage": "partial" if partial or len(entries) > 256 else "complete",
        "cli_unknown_model_pricing": unknown_pricing,
        "read_calls": reads, "failed_read_calls": errors,
        "immutable_read_calls": sum(ranges.values()),
        "exact_reread_calls": sum(max(0, count - 1) for count in ranges.values()),
        "immutable_read_ranges": entries[:256],
        "native_compaction_count": len(boundaries),
        "native_compaction_duration_ms": sum(item["duration_ms"] or 0 for item in boundaries),
        "native_compaction_duration_unknown": sum(item["duration_ms"] is None for item in boundaries),
        "last_native_compaction": boundaries[-1] if boundaries else None}


@lru_cache(maxsize=64)
def _read_metrics(path, size, mtime_ns, session_id, started_at, refs, limit):
    # File metadata only invalidates this ephemeral reader cache, never authority.
    with Path(path).open("rb") as source:
        if size > limit:
            source.seek(size - limit)
            source.readline()
        content = source.read(limit)
    return transcript_metrics(content, session_id, started_at, refs, partial=size > limit)


def read_transcript_metrics(path, session_id, started_at, input_refs=(), *, limit=64 * 1024 * 1024):
    try:
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            return {"coverage": "unavailable"}
        info = path.stat()
        return _read_metrics(str(path), info.st_size, info.st_mtime_ns, session_id,
            started_at, tuple(input_refs), limit)
    except (OSError, ValueError, TypeError, RecursionError):
        return {"coverage": "unavailable"}


def attempt_order(row, request):
    return (row.get("attempt_ordinal", request.get("attempt_ordinal", 0)),
        row.get("started_at") or 0, row["attempt_id"])


def recovery_metrics(records, charges):
    """Disjoint failed-context and subsequent recovery costs; no savings claim."""
    from .context_management import CONTEXT_FAILURES
    ordered = sorted(records, key=lambda item: attempt_order(item[0], item[2]))
    failed_ids, recovery_ids, ranges = set(), set(), Counter()
    coverage, reads, compactions, duration, unknown_duration = [], 0, 0, 0, 0
    unknown_pricing, unavailable_pricing = 0, 0
    material = Counter()
    wire = Counter()
    for row, observation, request in ordered:
        identity = row["attempt_id"]
        if failed_ids:
            recovery_ids.add(identity)
        diagnostic = observation.get("diagnostics") or {}
        for name in ("source_bytes", "unique_source_bytes", "duplicate_source_bytes", "source_count", "offloaded_count"):
            value = (diagnostic.get("input_material") or {}).get(name)
            if type(value) is int and value >= 0:
                material[name] += value
        for name in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "completed_requests", "unmeasured_requests"):
            value = (diagnostic.get("provider_observed_usage") or {}).get(name)
            if type(value) is int and value >= 0:
                wire[name] += value
        if (diagnostic.get("provider_failure") or {}).get("code") in CONTEXT_FAILURES:
            failed_ids.add(identity)
        metrics = diagnostic.get("context_observation") or {}
        unknown_pricing += metrics.get("cli_unknown_model_pricing") is True
        unavailable_pricing += metrics.get("cli_unknown_model_pricing") is None
        coverage.append(metrics.get("coverage", "unavailable"))
        reads += metrics.get("read_calls", 0)
        compactions += metrics.get("native_compaction_count", 0)
        duration += metrics.get("native_compaction_duration_ms", 0)
        unknown_duration += metrics.get("native_compaction_duration_unknown", 0)
        for entry in metrics.get("immutable_read_ranges", []):
            ranges[(entry["ref"], entry["offset"], entry["limit"])] += entry["count"]
    # The initial context failure is separate. Later failures belong to recovery.
    initial_ids = failed_ids - recovery_ids
    result = {"coverage": "complete" if all(value == "complete" for value in coverage) else "partial" if any(value != "unavailable" for value in coverage) else "unavailable",
        "context_failure_attempts": len(failed_ids), "recovery_attempts": len(recovery_ids),
        "cli_unknown_pricing_attempts": unknown_pricing,
        "cli_pricing_unavailable_attempts": unavailable_pricing,
        "read_calls": reads, "exact_reread_calls": sum(max(0, count - 1) for count in ranges.values()),
        "native_compaction_count": compactions, "native_compaction_duration_ms": duration,
        "native_compaction_duration_unknown": unknown_duration}
    result["input_material"] = dict(material)
    result["provider_observed_usage"] = dict(wire)
    for prefix, identities in (("initial_failure", initial_ids), ("recovery", recovery_ids)):
        selected = [item for item in charges if item.attempt_id in identities]
        result[prefix + "_reported_cost_usd"] = str(sum((item.cost for item in selected
            if item.source != "declared" and item.cost is not None), Decimal("0")))
        result[prefix + "_unknown_cost_attempts"] = sum(item.source == "declared" or item.cost is None for item in selected)
        result[prefix + "_reported_tokens"] = sum(item.tokens for item in selected
            if item.source != "declared" and item.tokens is not None)
        result[prefix + "_wall_seconds"] = sum(max(0, row["ended_at"] - row["started_at"])
            for row, _, _ in ordered if row["attempt_id"] in identities
            and type(row.get("started_at")) in (int, float) and type(row.get("ended_at")) in (int, float))
    return result


def recovery_totals(stages):
    """All admitted logical stages, including earlier cycles, exactly once."""
    rows = [stage["context_recovery"] for stage in stages]
    result = {name: sum(row[name] for row in rows) for name in (
        "context_failure_attempts", "recovery_attempts", "read_calls", "exact_reread_calls",
        "cli_unknown_pricing_attempts", "cli_pricing_unavailable_attempts",
        "native_compaction_count", "native_compaction_duration_ms", "native_compaction_duration_unknown",
        "initial_failure_unknown_cost_attempts", "initial_failure_reported_tokens", "initial_failure_wall_seconds",
        "recovery_unknown_cost_attempts", "recovery_reported_tokens", "recovery_wall_seconds")}
    for name in ("initial_failure_reported_cost_usd", "recovery_reported_cost_usd"):
        result[name] = str(sum((Decimal(row[name]) for row in rows), Decimal("0")))
    result["coverage"] = "complete" if rows and all(row["coverage"] == "complete" for row in rows) else "partial" if any(row["coverage"] != "unavailable" for row in rows) else "unavailable"
    return result
