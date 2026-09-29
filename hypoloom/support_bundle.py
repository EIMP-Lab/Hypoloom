"""Private, read-only support copies of existing run evidence (ADR-0029).

The report inventories observable files, including omissions. It is neither a
backup nor workflow authority, and never reconstructs missing historical logs.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import stat
import time
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .artifacts import _SAFE_ID
from .debug_mode import DebugModeError, _is_credential_key, build_debug_report, attempt_time_key
from .support_evidence import select_evidence, mark_export_fidelity, trim_payloads
from .runtime_diagnostics import (
    OMITTED_EMBEDDED_CONTENT, REDACTED, decode_embedded_content, sanitize_text, text_credential_values,
)
from .support_presentation import PublicSupportView, support_environment, support_profile_summary, support_readme, support_failure_groups

DEFAULT_MAX_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_FILE_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_FILES = 500


class SupportBundleError(DebugModeError):
    """A support copy cannot safely be produced."""


_ENVIRONMENT_KEYS = {"env", "environment", "environ", "host_environment"}
_ROOT_KEYS = {"target_repo", "target_root", "repository_root", "project_root",
              "runtime_prefix", "runtime_paths", "read_roots", "input_root"}
_SAFE_METADATA_KEYS = {
    "kind", "event", "timestamp", "observed_at", "started_at", "ended_at",
    "run_id", "attempt_id", "command_id", "session_id", "workflow_id",
    "attempt_ordinal", "terminal_reason", "failure_code", "returncode",
    "forced_reason", "stdout_bytes", "stderr_bytes", "exception_type",
    "exchange", "exchange_number", "path", "status", "upstream_status",
    "relay_status", "failure_stage", "reason", "request_bytes", "response_bytes",
    "request_complete", "response_complete", "request_body_complete", "response_body_complete",
    "response_headers", "capture", "capture_errors", "redacted", "redaction_count",
    "partial_reason", "response_partial_reason", "complete", "method", "headers",
    "received_bytes", "forwarded_bytes", "captured_bytes", "error_type", "stage",
    "request_bytes_received", "request_bytes_forwarded", "response_bytes_received",
    "response_bytes_forwarded", "response_started_at", "response_capture",
    "failure_reason", "response_capture_exception_type", "operation", "errors",
    "pid", "process_group_id", "written_bytes", "phase", "tool",
    "parser_diagnostics", "diagnostics", "context", "context_capacity", "context_policy",
    "provider_failure", "code", "http_status", "provider_observed_usage",
    "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
    "total_tokens", "completed_requests", "unmeasured_requests", "working_context_tokens",
    "declared_context_tokens", "runtime_context_tokens", "effective_context_tokens", "window_source",
    "estimate_source", "input_usage_semantics", "estimated_input_tokens", "output_reserve_tokens",
    "capacity_measurement_version", "usage_valid", "request_input_bytes", "estimate_reason", "anchor_exchange",
    "anchor_input_tokens", "added_input_bytes", "last_observed_exchange", "last_observed_input_tokens",
    "current_request_exchange", "current_request_estimated_input_tokens", "current_request_estimate_source",
    "current_request_estimate_reason", "current_request_fits",
    "available_input_tokens", "tool_reserve_tokens", "estimate_margin_tokens", "max_inline_input_bytes",
    "inline_input_bytes", "fits", "context_tokens", "context_source", "context_window_tokens",
    "context_window_source", "cumulative_tokens", "operational_reason", "operational_status",
    "minimum_inline_input_bytes", "recovery_action", "context_observation", "input_material",
    "source_bytes", "unique_source_bytes", "duplicate_source_bytes", "source_count", "offloaded_count",
    "coverage", "read_calls", "failed_read_calls", "immutable_read_calls", "exact_reread_calls",
    "cli_unknown_model_pricing", "cli_unknown_pricing_attempts", "cli_pricing_unavailable_attempts",
    "process_activity", "durable_state", "process_alive", "liveness_source", "resource_grant",
    "logs", "bytes", "last_modified_at", "age_seconds", "progress", "log_activity",
    "resource_blocked", "node_command_id", "checked_at", "node_command_ids",
    "native_compaction_started_count", "native_compaction_completion_unobserved", "native_compaction_start_coverage",
    "native_compaction_completion_unobserved_attempts", "local_context_refusal_count", "provider_context_failure_count",
    "incomplete_forwarded_request_count", "compaction_failure_attribution",
    "native_compaction_count", "native_compaction_duration_ms", "native_compaction_duration_unknown",
    "last_native_compaction", "pre_tokens", "post_tokens", "duration_ms",
    "context_recovery", "context_failure_attempts", "recovery_attempts",
    "initial_failure_reported_cost_usd", "initial_failure_unknown_cost_attempts", "initial_failure_reported_tokens", "initial_failure_wall_seconds",
    "recovery_reported_cost_usd", "recovery_unknown_cost_attempts", "recovery_reported_tokens", "recovery_wall_seconds",
    "initial_failure_unknown_token_attempts", "recovery_unknown_token_attempts",
}
_SAFE_HEADERS = {"request-id", "x-request-id", "anthropic-request-id", "cf-ray",
                 "content-type", "content-encoding", "content-length", "retry-after"}
_AGENT_FILES = ("prompt.txt", "launch.json", "request.json", "stdout.log",
                "stderr.log", "events.jsonl", "result.json")


def _lexical(path: str | Path) -> Path:
    value = Path(path).expanduser()
    if ".." in value.parts:
        raise SupportBundleError("support paths must not contain parent traversal")
    return Path(os.path.abspath(value))


def _no_links(path: Path) -> None:
    for candidate in (*reversed(path.parents), path):
        if candidate.is_symlink():
            raise SupportBundleError("support paths must not traverse symlinks")


def _read_file(path: Path, allowed: Path, *, max_bytes: int = DEFAULT_MAX_FILE_BYTES) -> bytes:
    if not path.is_relative_to(allowed):
        raise SupportBundleError("source_outside_selected_scope")
    _no_links(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise SupportBundleError("source_not_regular_file")
        if os.fstat(stream.fileno()).st_size > max_bytes:
            raise SupportBundleError("file_size_limit")
        data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise SupportBundleError("file_size_limit")
        return data


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _metadata(value: object, field: str = "") -> Any:
    if isinstance(value, Mapping):
        allowed = _SAFE_HEADERS if field in {"response_headers", "headers"} else _SAFE_METADATA_KEYS
        return {key: _metadata(item, key) for key, item in value.items() if key in allowed}
    if isinstance(value, (list, tuple)):
        return [_metadata(item, field) for item in value]
    return value


def _embedded(value: Mapping[str, Any]) -> object | None:
    raw = decode_embedded_content(value)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _walk(value: object):
    """Inspect existing typed inputs, including their documented JSON encoding."""
    yield value
    if isinstance(value, Mapping):
        for child in value.values():
            yield from _walk(child)
        if (decoded := _embedded(value)) is not None:
            yield from _walk(decoded)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk(child)
    elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            decoded = json.loads(value)
        except ValueError:
            return
        yield from _walk(decoded)


def _sanitize(value: object, reasons: set[str], secrets: tuple[str, ...]) -> Any:
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if str(key).lower() in _ENVIRONMENT_KEYS:
                result[key] = "[OMITTED: environment]"
                reasons.add("environment_omitted")
            elif _is_credential_key(str(key)) and item not in (None, "", False):
                result[key] = REDACTED
                reasons.add("credential_redacted")
            elif key == "content_base64":
                decoded = _embedded(value)
                if decoded is None:
                    result[key] = OMITTED_EMBEDDED_CONTENT
                    reasons.add("opaque_embedded_content_omitted")
                else:
                    clean = _sanitize(decoded, reasons, secrets)
                    if clean == decoded:
                        result[key] = item
                    else:
                        content = clean.encode() if isinstance(clean, str) else _json_bytes(clean)
                        result[key] = base64.b64encode(content).decode("ascii")
            else:
                result[key] = _sanitize(item, reasons, secrets)
        return result
    if isinstance(value, (list, tuple)):
        clean, position = [], 0
        while position < len(value):
            item = value[position]
            if item == "--setenv" and position + 2 < len(value):
                clean.append("[OMITTED: environment arguments]")
                reasons.add("environment_omitted")
                position += 3
                continue
            clean.append(_sanitize(item, reasons, secrets))
            position += 1
        return clean
    if isinstance(value, str):
        if value.lstrip().startswith(("{", "[")):
            try:
                decoded = json.loads(value)
            except ValueError:
                pass
            else:
                cleaned = _sanitize(decoded, reasons, secrets)
                if cleaned != decoded:
                    return json.dumps(cleaned, ensure_ascii=False, sort_keys=True)
        clean = sanitize_text(value, secrets=secrets)
        if clean != value:
            reasons.add("credential_redacted")
        return clean
    return value


def _clean_bytes(raw: bytes, reasons: set[str], secrets: tuple[str, ...]) -> bytes:
    text = raw.decode("utf-8")  # Binary attachments cannot be safely credential-redacted.
    try:
        value = json.loads(text)
    except ValueError:
        lines = []
        for line in text.splitlines(keepends=True):
            payload = line[5:].lstrip() if line.startswith("data:") else line
            lines.append((line[:len(line) - len(payload)], payload))
        # A transcript/SSE attachment may reveal a named credential on one line
        # and repeat it in a later tool result. Redact throughout the same file.
        known = (*secrets, *(secret for _, line in lines for secret in _known_secrets(line)))
        cleaned = []
        for prefix, line in lines:
            clean = _sanitize(line, reasons, known)
            cleaned.append(prefix + clean + ("\n" if line.endswith("\n") and not clean.endswith("\n") else ""))
        return "".join(cleaned).encode()
    clean = _sanitize(value, reasons, (*secrets, *_known_secrets(value)))
    return raw if clean == value else _json_bytes(clean)


def _known_secrets(value: object) -> tuple[str, ...]:
    secrets = set()
    for node in _walk(value):
        if isinstance(node, Mapping):
            secrets.update(item for key, item in node.items() if _is_credential_key(str(key))
                and isinstance(item, str) and item and item != REDACTED)
        elif isinstance(node, str):
            secrets.update(text_credential_values(node))
    return tuple(secrets)


def _protected_inputs(report: Mapping[str, Any]) -> tuple[Path, ...]:
    roots = []
    for value in _walk(report):
        if not isinstance(value, Mapping):
            continue
        for key, item in value.items():
            if key not in _ROOT_KEYS:
                continue
            for path in item if isinstance(item, (list, tuple)) else (item,):
                if isinstance(path, str) and Path(path).is_absolute():
                    roots.append(Path(path).resolve())
    return tuple(roots)


def _selected_projection(report: dict[str, Any], private: bool) -> dict[str, Any]:
    report = dict(report)
    if not private:
        for group in ("attempts", "supporting_attempts"):
            report[group] = [{key: value for key, value in item.items()
                if key not in {"observation", "command"}} for item in report.get(group, [])]
        report.pop("related_records", None)
    return report


def build_support_bundle(
    state_root: str | Path, run_id: str | None, *,
    attempt_id: str | None = None, include_private_data: bool = False,
    recent_failures: bool = False, max_bytes: int = DEFAULT_MAX_BYTES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES, max_files: int = DEFAULT_MAX_FILES,
    output_path: str | Path | None = None,
    issue_description: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Build the same bounded copy for directory, preview and browser ZIP output.

Live files may be observed at different instants. Missing completion records,
unavailable older data and sanitized copies remain explicit in the report.
"""
    root = _lexical(state_root)
    issue_description = {} if issue_description is None else issue_description
    if (not isinstance(issue_description, Mapping)
            or set(issue_description) - {"actual", "expected", "steps", "occurred_at"}
            or any(not isinstance(value, str) or len(value) > 2000 for value in issue_description.values())):
        raise SupportBundleError("issue description accepts actual/expected/steps/occurred_at text, up to 2000 characters each")
    if type(max_bytes) is not int or not 65536 <= max_bytes <= 256 * 1024 * 1024:
        raise SupportBundleError("total byte limit must be between 64 KiB and 256 MiB")
    if type(max_file_bytes) is not int or not 256 <= max_file_bytes <= 32 * 1024 * 1024:
        raise SupportBundleError("file byte limit must be between 256 bytes and 32 MiB")
    if type(max_files) is not int or not 4 <= max_files <= 5000:
        raise SupportBundleError("file limit must be between 4 and 5000")
    if attempt_id is not None and run_id is None:
        raise SupportBundleError("Attempt selection requires a run ID")
    for name in (run_id, attempt_id):
        if name is not None and (not isinstance(name, str) or _SAFE_ID.fullmatch(name) is None):
            raise SupportBundleError("run and Attempt IDs must be safe path components")
    _no_links(root)
    for suffix in ("", "-wal", "-shm"):
        _no_links(root / ("state.sqlite" + suffix))
    diagnosis = {"available": False, "reason": "startup_scope"}
    projection = {"non_authoritative": True, "mode": "READ_ONLY_DIAGNOSIS", "observed_at": time.time(),
        "run_id": run_id, "selected_attempt_id": attempt_id, "run_versions": {}, "summary": {},
        "attempts": [], "evaluator_retries": [], "related_records": {"artifacts": []},
        "oracle": {"available": False, "error": "diagnosis_unavailable"}}
    if run_id is not None:
        try:
            projection = build_debug_report(root, run_id, include_payloads=True)
            diagnosis = {"available": True, "reason": "recorded_run"}
        except DebugModeError as exc:
            # An exact selector typo must not quietly become a successful export.
            if attempt_id is not None or str(exc).startswith("attempt not found"):
                raise SupportBundleError("attempt not found or run diagnosis unavailable for this exact selection") from exc
            diagnosis = {"available": False, "reason": "run_state_unavailable",
                "exception_type": type(exc.__cause__ or exc).__name__}
    if output_path is not None:
        output = _lexical(output_path)
        _validate_destination(root, output)
        for source in _protected_inputs(projection):
            if output.is_relative_to(source) or source.is_relative_to(output):
                raise SupportBundleError("support output and recorded input roots must be disjoint")
    secrets = (*_known_secrets(projection), *_known_secrets(issue_description))
    profile_summary = support_profile_summary(projection)
    public = PublicSupportView()
    if run_id is not None:
        public.identity(run_id, "run")
    for attempt in projection["attempts"]:
        public.identity(attempt["attempt_id"])
    selected_ids = {item["attempt_id"] for item in projection["attempts"]}
    diagnostic_only = []
    if not diagnosis["available"] and run_id is not None:
        captures = root / "diagnostics/agents" / run_id
        try:
            _no_links(captures)
            diagnostic_only = [path.name for path in sorted(captures.iterdir())
                if not path.is_symlink() and path.is_dir() and _SAFE_ID.fullmatch(path.name)][:max_files]
        except OSError:
            pass
    try:
        primary_ids, supporting_ids, run_attempt_count = select_evidence(projection, attempt_id, recent_failures)
    except ValueError as exc:
        raise SupportBundleError("attempt not found in run") from exc
    selected_ids = primary_ids | supporting_ids
    selected_ids.update(diagnostic_only)
    attachments: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "non_authoritative": True, "mode": "READ_ONLY_SUPPORT_COPY",
        "run_id": run_id if include_private_data else public.identity(run_id, "run"),
        "selected_attempt_id": attempt_id if include_private_data else public.identity(attempt_id),
        "include_private_data": include_private_data, "diagnosis": diagnosis,
        "environment": support_environment(), "profile_summary": profile_summary,
        "selection": {"scope": "attempt" if attempt_id else "recent_failures" if recent_failures else "run" if run_id else "startup",
            "attachment_attempt_count": len(selected_ids) - len(diagnostic_only),
            "diagnostic_only_capture_count": len(diagnostic_only), "run_attempt_count": run_attempt_count,
            "selected_detail_count": len(primary_ids), "supporting_detail_count": len(supporting_ids),
            "order": "ended_at_or_started_at_then_attempt_id"},
        "limits": {"max_bytes": max_bytes, "max_file_bytes": max_file_bytes, "max_files": max_files},
        "limited": False,
        "source_read_only": True, "restorable_backup": False,
        "completeness": "observed_files_only",
        "limitations": ["No historical data is reconstructed.",
            "Live files are separate observations, not an atomic backup.",
            "Missing end records mean interrupted or unknown capture.",
            "Known credentials and raw environment variables are excluded even from private copies.",
            "Public copies omit free-text errors and use local aliases for record identities.",
            "Private copies require manual review and may contain research content and local paths."],
        "attachments": attachments,
        "agent_captures": [],
    }
    report["limitations"].append("SQLite read-only connections may create standard WAL/SHM reader sidecars; no authority records are written.")
    report["observation"] = {"observed_at": projection.get("observed_at"),
        "run_state": projection.get("run_state", {}).get("state"),
        "revision": projection.get("run_state", {}).get("revision")}
    report["issue_description"] = _sanitize(dict(issue_description), set(), secrets)
    groups = _sanitize(support_failure_groups(projection.get("run_attempts", projection["attempts"])), set(), secrets)
    report["failure_groups"] = groups if include_private_data else public.clean(groups)
    report["attempt_environments"] = [{"attempt_id": item["attempt_id"] if include_private_data else public.identity(item["attempt_id"]),
        "scope": "recorded_attempt", "adapter_version": item.get("adapter_version"),
        "requested_model_identity": item.get("requested_model_identity"), "claude_cli_versions": [],
        "cli_version_source": "unavailable"}
        for item in projection["attempts"] + projection.get("supporting_attempts", []) if item["kind"] == "AGENT"]
    report["attempt_environments"] = _sanitize(report["attempt_environments"], set(), secrets)
    if not include_private_data:
        for environment in report["attempt_environments"]:
            for key in ("adapter_version", "requested_model_identity"):
                environment[key] = public.clean(environment[key])
    files: dict[str, bytes] = {}
    written_bytes, inspected = 0, 0
    # Leave space for the existing attachment report and readable summary.
    metadata_reserve = min(1024 * 1024, max_bytes // 2)
    deadline = time.monotonic() + 15
    copied: set[Path] = set()

    def write(relative: str, raw: bytes, *, metadata: bool = False) -> None:
        nonlocal written_bytes
        ceiling = max_bytes if metadata else max_bytes - metadata_reserve
        if len(files) >= max_files - (0 if metadata else 3) or written_bytes + len(raw) > ceiling:
            report["limited"] = True
            raise SupportBundleError("export_size_or_file_limit")
        files[relative] = raw
        written_bytes += len(raw)

    def entry(source: Path, sensitivity: str, status: str, reason: str, **extra: Any) -> None:
        name = source.name if source.name in {*_AGENT_FILES, "exchange.json", "host.jsonl"} else "private-attachment"
        source_label = sanitize_text(str(source.relative_to(root)), secrets=secrets) if include_private_data else name
        owner = next((part for part in source.relative_to(root).parts if part in selected_ids), None)
        attachments.append({"source": source_label,
            "attempt_id": owner if include_private_data else public.identity(owner),
            "sensitivity": sensitivity, "status": status, "reason": reason, **extra})

    def copy(source: Path, allowed: Path, *, private: bool = True) -> None:
        nonlocal inspected
        if source in copied:
            return
        copied.add(source)
        inspected += 1
        sensitivity = "private_research_data" if private or include_private_data else "operational_metadata"
        try:
            if inspected > max_files * 2 or time.monotonic() > deadline:
                report["limited"] = True
                raise SupportBundleError("inspection_limit")
            _no_links(source)
            if not source.is_relative_to(allowed):
                raise SupportBundleError("source_outside_selected_scope")
            metadata = source.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                raise SupportBundleError("source_not_regular_file")
            if private and not include_private_data:
                entry(source, sensitivity, "omitted", "requires_include_private_data", source_bytes=metadata.st_size)
                return
            if source.name in {"state.sqlite", ".env", "credentials.json", "settings.json"} or source.suffix in {".sqlite", ".db"}:
                entry(source, sensitivity, "omitted", "database_or_credentials_file")
                return
            raw = _read_file(source, allowed, max_bytes=max_file_bytes)
            if source.name == "transcript.jsonl":
                versions = set()
                for line in raw.splitlines():
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    version = record.get("version") if isinstance(record, Mapping) else None
                    if isinstance(version, str) and re.fullmatch(r"\d+\.\d+\.\d+(?:[-+.][\w.]+)?", version):
                        versions.add(version)
                owner = next((part for part in source.parts if part in selected_ids), None)
                for environment in report["attempt_environments"]:
                    if environment["attempt_id"] == (owner if include_private_data else public.identity(owner)) and versions:
                        environment.update(claude_cli_versions=sorted(versions), cli_version_source="recorded_transcript")
            reasons: set[str] = set()
            file_secrets = secrets
            if not private and not include_private_data:
                values = []
                for line in raw.decode("utf-8").splitlines() if source.suffix == ".jsonl" else [raw]:
                    try:
                        values.append(json.loads(line))
                    except ValueError:
                        reasons.add("malformed_metadata_record_omitted")
                filtered = [public.clean(_metadata(item)) for item in values if isinstance(item, Mapping)]
                file_secrets = (*secrets, *_known_secrets(values))
                if values != filtered:
                    reasons.add("non_metadata_fields_omitted")
                raw = ("".join(json.dumps(item) + "\n" for item in filtered).encode()
                    if source.suffix == ".jsonl" else _json_bytes(filtered[0] if filtered else {}))
            clean = _clean_bytes(raw, reasons, file_secrets)
            relative = ("files/" + sanitize_text(source.relative_to(root).as_posix(), secrets=secrets)
                if include_private_data else f"files/{len(attachments) + 1:06d}-{source.name}")
            write(relative, clean)
            entry(source, sensitivity, "included", "sanitized_copy" if reasons else "observed_copy",
                path=relative, source_bytes=metadata.st_size, exported_bytes=len(clean), redactions=sorted(reasons))
        except FileNotFoundError:
            entry(source, sensitivity, "unavailable", "not_recorded_or_no_longer_retained")
        except UnicodeDecodeError:
            entry(source, sensitivity, "omitted", "binary_content_cannot_be_safely_redacted")
        except (OSError, ValueError, SupportBundleError) as exc:
            reason = str(exc) if isinstance(exc, SupportBundleError) else type(exc).__name__
            if "limit" in reason:
                report["limited"] = True
            entry(source, sensitivity, "omitted" if "limit" in reason else "unavailable",
                sanitize_text(reason, secrets=secrets))

    def tree(path: Path, *, diagnostics: bool = False) -> None:
        try:
            _no_links(path)
            if not path.is_dir():
                raise FileNotFoundError
            for base, directories, files in os.walk(path, followlinks=False):
                if inspected > max_files * 2 or time.monotonic() > deadline:
                    report["limited"] = True
                    return
                directories.sort(key=lambda name: (name == "provider", name))
                for name in list(directories):
                    child = Path(base) / name
                    if child.is_symlink() or name == ".git":
                        entry(child, "private_research_data", "omitted", "symlink_or_repository_internals")
                        directories.remove(name)
                for name in sorted(files, key=lambda name: (name not in {"result.json", "events.jsonl", "exchange.json", "stderr.log"}, name)):
                    if inspected > max_files * 2 or time.monotonic() > deadline:
                        report["limited"] = True
                        return
                    child = Path(base) / name
                    safe = diagnostics and name in {"events.jsonl", "exchange.json"}
                    copy(child, path, private=not safe)
        except (OSError, SupportBundleError) as exc:
            entry(path, "private_research_data", "unavailable", type(exc).__name__)

    def agent_capture(path: Path, selected_id: str) -> None:
        missing, events, result, errors = [], [], {}, []
        for name in _AGENT_FILES:
            source = path / name
            try:
                _no_links(source)
                if not source.is_file():
                    raise FileNotFoundError
                if name not in {"events.jsonl", "result.json"}:
                    continue
                raw = _read_file(source, path, max_bytes=max_file_bytes)
                if name == "result.json":
                    value = json.loads(raw)
                    if not isinstance(value, Mapping):
                        raise ValueError("result record is not an object")
                    result = value
                else:
                    for line in raw.decode("utf-8").splitlines():
                        try:
                            event = json.loads(line)
                        except ValueError:
                            errors.append({"file": name, "reason": "partial_or_invalid_event"})
                            continue
                        if isinstance(event, Mapping):
                            events.append(event)
            except (OSError, ValueError, SupportBundleError) as exc:
                missing.append(name)
                entry(source, "operational_metadata", "unavailable", "not_recorded_or_unreadable")
                if not isinstance(exc, FileNotFoundError):
                    errors.append({"file": name, "exception_type": type(exc).__name__})
        started = any(item.get("kind") == "started" for item in events)
        ended = any(item.get("kind") == "ended" for item in events)
        capture = result.get("capture", {})
        capture = capture if isinstance(capture, Mapping) else {}
        clean_capture = _sanitize(_metadata(capture), set(), (*secrets, *_known_secrets(result)))
        if not include_private_data:
            clean_capture = public.clean(clean_capture)
        report["agent_captures"].append({"attempt_id": selected_id if include_private_data else public.identity(selected_id),
            "status": "unavailable" if len(missing) == len(_AGENT_FILES) else
                "end_observed" if ended and result else "interrupted_or_unknown",
            "started_recorded": started, "ended_recorded": ended,
            "result_recorded": bool(result), "missing_files": missing,
            "capture_errors": clean_capture.get("capture_errors", []),
            "redacted": clean_capture.get("redacted", False),
            "redaction_count": clean_capture.get("redaction_count", 0),
            "inspection_errors": errors})

    try:
        artifacts = projection["related_records"]["artifacts"]
        for selected_id in diagnostic_only:
            if time.monotonic() > deadline or inspected > max_files * 2:
                report["limited"] = True
                break
            diagnostic_root = root / "diagnostics/agents" / run_id / selected_id
            agent_capture(diagnostic_root, selected_id)
            report["agent_captures"][-1]["identity_source"] = "diagnostic_directory_only"
            tree(diagnostic_root, diagnostics=True)
        evidence_attempts = projection["attempts"] + projection.get("supporting_attempts", [])
        evidence_attempts.sort(key=lambda item: (not item.get("final_failure_transition", False),
            item["attempt_id"] != projection["summary"].get("first_unrecovered_failure_attempt_id"),
            -attempt_time_key(item)[0]))
        # Preserve small causal archives before provider wire and full transcripts.
        for attempt in evidence_attempts:
            if attempt["kind"] == "AGENT":
                evaluator = root / "artifacts" / run_id / "agent" / attempt["attempt_id"] / "transcript/evaluator"
                if evaluator.exists():
                    tree(evaluator)
        for attempt in evidence_attempts:
            selected_id = attempt["attempt_id"]
            if selected_id not in selected_ids:
                continue
            if inspected > max_files * 2 or time.monotonic() > deadline:
                report["limited"] = True
                break
            if _SAFE_ID.fullmatch(selected_id) is None:
                raise SupportBundleError("recorded Attempt ID is not a safe path component")
            if attempt["kind"] == "AGENT":
                diagnostic_root = root / "diagnostics/agents" / run_id / selected_id
                agent_capture(diagnostic_root, selected_id)
                tree(diagnostic_root, diagnostics=True)
                tree(root / "artifacts" / run_id / "agent" / selected_id / "transcript")
            else:
                observation = attempt.get("observation", {})
                if observation.get("synthetic_before_spawn") is True and observation.get("process_group_id") is None:
                    entry(root / "artifacts/work" / selected_id / "logs", "operational_metadata", "not_applicable", "process_not_spawned")
                else:
                    for category in ("work", "result-staging"):
                        tree(root / "artifacts" / category / selected_id / "logs")
                for artifact in artifacts:
                    if artifact["attempt_id"] != selected_id:
                        continue
                    relative = Path(artifact["path"])
                    expected = Path(run_id) / str(artifact["experiment_id"]) / str(artifact["execution_id"]) / selected_id / str(artifact["artifact_kind"])
                    if relative != expected or ".." in relative.parts:
                        entry(root / "artifacts", "private_research_data", "omitted", "artifact_scope_mismatch")
                    else:
                        tree(root / "artifacts" / relative)
        host = root / "diagnostics/host.jsonl"
        try:
            raw = _read_file(host, root / "diagnostics", max_bytes=max_file_bytes)
            records, omitted, reasons = [], 0, set()
            for line in raw.decode("utf-8").splitlines():
                try:
                    item = json.loads(line)
                except ValueError:
                    omitted += 1
                    continue
                if not isinstance(item, Mapping) or item.get("run_id") != run_id:
                    continue
                if attempt_id is not None and item.get("attempt_id") not in {None, attempt_id}:
                    continue
                item = _sanitize(item, reasons, (*secrets, *_known_secrets(item)))
                if not include_private_data:
                    item = public.clean(_metadata(item))
                records.append(item)
            clean = _sanitize(records, reasons, secrets)
            relative = "files/diagnostics/host.jsonl"
            write(relative, "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in clean).encode())
            entry(host, "private_research_data" if include_private_data else "operational_metadata", "included", "selected_run_records",
                path=relative, record_count=len(records), malformed_lines_omitted=omitted, redactions=sorted(reasons),
                omitted_fields=[] if include_private_data else ["traceback", "message", "non_metadata_fields"])
        except (OSError, ValueError, SupportBundleError) as exc:
            if isinstance(exc, SupportBundleError) and "limit" in str(exc):
                report["limited"] = True
            entry(host, "operational_metadata", "unavailable", str(exc) if isinstance(exc, SupportBundleError) else type(exc).__name__)
        reasons = set()
        clean_projection = _sanitize(_selected_projection(projection, include_private_data), reasons, secrets)
        mark_export_fidelity(projection, clean_projection, "export_sanitized_or_private_fields_omitted")
        if not include_private_data:
            clean_projection = public.clean(clean_projection)
            reasons.add("private_text_paths_and_identifiers_omitted")
        omitted_fields = [] if include_private_data else ["observation", "command", "related_records"]
        if include_private_data:
            omissions = trim_payloads(clean_projection, metadata_reserve // 2, _json_bytes)
            if omissions:
                report["limited"] = True
                reasons.add("private_debug_payloads_exceed_size_limit")
                omitted_fields = sorted({item["field"] for item in omissions})
                report["detail_omissions"] = omissions
                mark_export_fidelity(projection, clean_projection, "export_size_limit")
        debug_bytes = _json_bytes(clean_projection)
        if len(debug_bytes) > metadata_reserve // 2:
            report["limited"] = True
            reasons.add("debug_projection_exceeds_size_limit")
            clean_projection = {"non_authoritative": True, "available": False,
                "reason": "debug_projection_exceeds_size_limit", "attempt_count": len(projection["attempts"])}
            debug_bytes = _json_bytes(clean_projection)
        write("debug.json", debug_bytes, metadata=True)
        attachments.insert(0, {"source": "existing_debug_projection", "path": "debug.json", "status": "included",
            "sensitivity": "private_research_data" if include_private_data else "operational_metadata",
            "reason": "sanitized_projection", "redactions": sorted(reasons),
            "omitted_fields": omitted_fields})
        write("README.md", support_readme(report, clean_projection), metadata=True)
        report["exported_file_count"] = len(files) + 1
        report["exported_payload_bytes"] = written_bytes
        report_bytes = _json_bytes(report)
        if written_bytes + len(report_bytes) > max_bytes:
            report["limited"] = True
            report["attachments_omitted_from_inventory"] = len(attachments)
            report["attachments"] = attachments[:1]
            report["agent_captures"] = []
            report_bytes = _json_bytes(report)
        write("report.json", report_bytes, metadata=True)
        return report, files
    except (OSError, ValueError, RuntimeError) as exc:
        raise SupportBundleError(f"support copy unavailable: {type(exc).__name__}") from exc


def _validate_destination(root: Path, output: Path) -> None:
    _no_links(output)
    if output.exists():
        raise SupportBundleError("support output must be new")
    if not output.parent.is_dir():
        raise SupportBundleError("support output parent directory must exist")
    if output.is_relative_to(root) or root.is_relative_to(output):
        raise SupportBundleError("support output and source state root must be disjoint")


def support_zip(files: Mapping[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, raw)
    return output.getvalue()


def export_support_bundle(state_root: str | Path, run_id: str | None, output_dir: str | Path, *,
        attempt_id: str | None = None, include_private_data: bool = False, archive: bool = False,
        recent_failures: bool = False, max_bytes: int = DEFAULT_MAX_BYTES,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES, max_files: int = DEFAULT_MAX_FILES,
        issue_description: Mapping[str, str] | None = None) -> dict[str, Any]:
    output = _lexical(output_dir)
    _validate_destination(_lexical(state_root), output)
    report, files = build_support_bundle(state_root, run_id, attempt_id=attempt_id,
        include_private_data=include_private_data, recent_failures=recent_failures,
        max_bytes=max_bytes, max_file_bytes=max_file_bytes, max_files=max_files, output_path=output,
        issue_description=issue_description)
    try:
        if archive:
            with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(support_zip(files))
        else:
            output.mkdir(mode=0o700)
            output.chmod(0o700)
            for relative, raw in files.items():
                path = output / relative
                parent = output
                for component in path.parent.relative_to(output).parts:
                    parent = parent / component
                    parent.mkdir(mode=0o700, exist_ok=True)
                    parent.chmod(0o700)
                with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(raw)
    except OSError as exc:
        raise SupportBundleError(f"support export failed; partial output remains at {output}: {type(exc).__name__}") from exc
    return report


__all__ = ["SupportBundleError", "build_support_bundle", "export_support_bundle", "support_zip"]
