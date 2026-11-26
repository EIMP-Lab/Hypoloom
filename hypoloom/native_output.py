"""Per-Attempt native Stop validation and a standalone standard-library client.

The Host validates the admitted schema; this socket grants no scientific effects.
The Host runs parser fixtures through the production CPU parser sandbox; the
standalone client only forwards the native completion.
"""

from __future__ import annotations

import argparse
import difflib
import json
import math
import os
from pathlib import Path
import select
import socket
import socketserver
import sys
import tempfile
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Mapping

if TYPE_CHECKING:
    from .runtime_diagnostics import RuntimeDiagnostics


class _ValidationUnavailable(ValueError):
    pass


class _IncompleteRequest(ValueError):
    pass


class _SchemaMismatch(ValueError):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.correction_hint = hint


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite JSON number")
    return result


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object member")
        result[key] = value
    return result


def _decode(raw: str | bytes) -> Any:
    try:
        return json.loads(raw, parse_constant=_reject_constant, parse_float=_finite_float,
                          object_pairs_hook=_unique_object)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("invalid JSON encoding or nesting") from exc


def _validator(schema: Mapping[str, Any]):
    # Imports stay Host-only: the same file runs with stdlib Python inside bwrap.
    from jsonschema import Draft7Validator
    from jsonschema.exceptions import SchemaError
    from referencing import Registry

    if not isinstance(schema, Mapping):
        raise ValueError("output schema must be an object")
    try:
        Draft7Validator.check_schema(schema)
    except (SchemaError, RecursionError) as exc:
        raise ValueError("invalid output schema") from exc
    # An unresolved external reference fails locally; validation never fetches URLs.
    return Draft7Validator(schema, registry=Registry())


def check_output_schema(schema: Mapping[str, Any]) -> None:
    """Reject an invalid admitted schema before opening a native invocation."""
    _validator(schema)


def _decode_output(text: str, validator) -> dict[str, Any]:
    if not isinstance(text, str):
        raise ValueError("native output must be JSON text")
    output = _decode(text)
    if not isinstance(output, dict):
        raise ValueError("native output must be one complete JSON object")
    try:
        error = next(validator.iter_errors(output), None)
    except Exception as exc:
        raise _ValidationUnavailable("output schema could not be evaluated") from exc
    if error is not None:
        # Nullable object branches otherwise report only '/probe: anyOf',
        # concealing the field the model must fix. Reuse schema type selection;
        # ambiguous unions retain their original error rather than guessing.
        while error.validator in {"anyOf", "oneOf"} and error.context:
            matching = [index for index, branch in enumerate(error.validator_value)
                if isinstance(branch, dict) and isinstance(branch.get("type"), str)
                and validator.is_type(error.instance, branch["type"])]
            nested = [child for child in error.context
                if len(matching) == 1 and child.schema_path[0] == matching[0]]
            if not nested:
                break
            error = nested[0]
        path = "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1")
                               for part in error.absolute_path)
        # jsonschema's human message often embeds the candidate value. Keep only
        # its precise location and constraint in the redacted diagnostic projection.
        hint = ""
        if error.validator == "required" and isinstance(error.instance, dict):
            missing = [key for key in error.validator_value if key not in error.instance]
            hint = " Missing required fields: " + json.dumps(missing) + "."
        elif error.validator in {"maxLength", "minLength", "minItems", "maxItems", "maxProperties"}:
            hint = f" Limit: {error.validator_value}."
        elif error.validator == "uniqueItems" and isinstance(error.instance, list):
            first = {}
            for index, item in enumerate(error.instance):
                if not isinstance(item, str): continue
                if item in first:
                    hint = f" Duplicate item at index {index} matches index {first[item]} (zero-based)."
                    break
                first[item] = index
        if error.validator == "type":
            hint = f" Expected {error.validator_value}, received {type(error.instance).__name__}."
        if error.validator == "enum" and "supporting_refs" not in error.absolute_path:
            if len(error.validator_value) <= 12:
                hint = (" Allowed values: " + json.dumps(error.validator_value, ensure_ascii=True)[:384]
                        + f". Received {type(error.instance).__name__}.")
        if error.validator == "enum" and "supporting_refs" in error.absolute_path:
            allowed = error.validator_value
            if isinstance(allowed, list) and all(isinstance(value, str) for value in allowed):
                hint = " Allowed supporting_refs: " + json.dumps(allowed, ensure_ascii=True) + "."
                if isinstance(error.instance, str):
                    closest = difflib.get_close_matches(error.instance, allowed, n=1)
                    if closest:
                        hint += " Closest allowed reference: " + json.dumps(closest[0]) + "."
        raise _SchemaMismatch(f"{path}: {error.validator} constraint failed", hint)
    return output


def decode_output(text: str, schema: Mapping[str, Any]) -> dict[str, Any]:
    """Decode one unmodified finite JSON object and validate the full schema."""
    return _decode_output(text, _validator(schema))


def _prepare_review_probe(report: dict[str, Any]) -> None:
    """Restore the canonical probe field at the native tool boundary only."""
    verdict = report.get("verdict")
    probe = report.get("probe")
    if verdict in ("passed", "changes_required", "inconclusive"):
        if probe is not None and probe != "null":
            raise ValueError("/probe: omit this field for " + verdict + "; only probe_required takes a probe")
        report["probe"] = None
    elif verdict == "probe_required":
        # Some compatible tool transports stringify a nested argument. Unwrap
        # one complete JSON object; its files and checks still get full validation.
        if isinstance(probe, str):
            try:
                probe = _decode(probe)
            except ValueError:
                probe = None
        if not isinstance(probe, dict):
            raise ValueError("/probe: probe_required needs a JSON object with files, argv, timeout_seconds and checks")
        report["probe"] = probe


def _encode(document: Any) -> bytes:
    return json.dumps(document, ensure_ascii=True, allow_nan=False,
                      separators=(",", ":")).encode() + b"\n"


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("native output validation deadline exceeded")
    return remaining


def _read_line(connection: socket.socket, maximum: int, deadline: float) -> bytes:
    raw = bytearray()
    while b"\n" not in raw:
        connection.settimeout(_remaining(deadline))
        chunk = connection.recv(min(65536, maximum + 1 - len(raw)))
        if not chunk:
            raise _IncompleteRequest("native output transport ended before a complete JSON line")
        raw.extend(chunk)
        if len(raw) > maximum:
            raise ValueError("native output transport exceeds size limit")
    if raw.find(b"\n") != len(raw) - 1:
        raise ValueError("native output transport requires one JSON line")
    return bytes(raw)


class _UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class NativeOutputValidator:
    """Validate normal native completions, without accepting scientific output."""

    def __init__(self, socket_path: Path, *, output_schema: Mapping[str, Any],
                 session_id: str, max_failed_validations: int, max_request_bytes: int,
                 deadline_at: float, diagnostics: RuntimeDiagnostics | None = None,
                 acceptance_smoke: bool = False, measurement_smoke: bool = False,
                 semantic_examples: bool = False, seed_files: Mapping[str, bytes] | None = None,
                 measurement_examples_required: bool = False,
                 design_implementation: bool = False,
                 parser_source_root: Path | None = None, parser_smoke_max_bytes: int | None = None,
                 execution_tool_socket: Path | None = None,
                 candidate_check: Callable[[Mapping[str, Any]], Any] | None = None,
                 final_report_check: Callable[[], Any] | None = None,
                 context_notice: Callable[[], str | None] | None = None,
                 structured_submission: bool = False, compact_review: bool = False,
                 verdict_scoped_review: bool = False, authoring_progress: bool = False,
                 draft_check: bool = False, soft_wall_seconds: float | None = None,
                 invocation_wall_seconds: float | None = None) -> None:
        if compact_review and not structured_submission:
            raise ValueError("compact review requires structured submission")
        if acceptance_smoke and measurement_smoke:
            raise ValueError("a node authors either the common or the local parser")
        if (acceptance_smoke or measurement_smoke) and parser_source_root is None:
            raise ValueError("parser smoke requires the Host-owned authoring root")
        if parser_smoke_max_bytes is not None and (type(parser_smoke_max_bytes) is not int or parser_smoke_max_bytes < 1):
            raise ValueError("parser smoke byte limit must be a positive integer")
        if not isinstance(session_id, str) or not session_id or "\x00" in session_id:
            raise ValueError("native output session identity is required")
        if type(max_failed_validations) is not int or max_failed_validations < 1:
            raise ValueError("max_failed_validations must be a positive integer")
        if type(max_request_bytes) is not int or max_request_bytes < 1:
            raise ValueError("max_request_bytes must be a positive integer")
        if isinstance(deadline_at, bool) or not isinstance(deadline_at, (int, float)) or not math.isfinite(deadline_at):
            raise ValueError("native output deadline must be finite")
        self._validator = _validator(output_schema)
        self.socket_path = Path(socket_path)
        if self.socket_path.exists() or self.socket_path.is_symlink():
            raise FileExistsError(self.socket_path)
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id
        self.max_failed_validations = max_failed_validations
        self.max_request_bytes = max_request_bytes
        self.deadline_at = deadline_at
        self.authoring_progress = authoring_progress
        self.draft_check = draft_check
        self._soft_seconds = soft_wall_seconds
        self._soft_deadline = deadline_at
        if soft_wall_seconds is not None:
            if (not authoring_progress or invocation_wall_seconds is None
                    or not 0 < soft_wall_seconds <= invocation_wall_seconds):
                raise ValueError("invalid authoring progress window")
            self._soft_deadline = deadline_at - invocation_wall_seconds + soft_wall_seconds
        self._progress_rank = 0
        self._last_verified = None
        self._latest_error = None
        self._latest_validation_stage = None
        self._repeated_errors = 0
        self._deadline_notice_window = (min(600.0 if design_implementation else 300.0,
            max(0.0, self._current_deadline() - time.monotonic()) * 0.2)
            if acceptance_smoke or design_implementation or structured_submission else 0.0)
        self.design_implementation = design_implementation
        self.diagnostics = diagnostics
        self.semantic_examples = semantic_examples
        self.measurement_examples_required = measurement_examples_required
        self.seed_files = seed_files
        self.acceptance_smoke = acceptance_smoke
        self.measurement_smoke = measurement_smoke
        self.execution_tool_socket = execution_tool_socket
        self.candidate_check = candidate_check
        self.final_report_check = final_report_check
        self.context_notice = context_notice
        self.structured_submission = structured_submission
        self.compact_review = compact_review
        self.verdict_scoped_review = verdict_scoped_review
        self._prepared = False
        self.parser_source_root = parser_source_root
        self.parser_smoke_max_bytes = parser_smoke_max_bytes or max(1, max_request_bytes // 2)
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._connections: set[socket.socket] = set()
        self._status = "not_observed"
        self._accepted = False
        self._completion_text: str | None = None
        # The invocation-owned result is handed to the adapter without asking
        # the model to reproduce it. Sanitized diagnostics are never transport.
        self.output_path = self.socket_path.with_suffix(".json")
        self._error: str | None = None
        self._rounds: list[dict[str, Any]] = []
        self._rejected = 0
        endpoint = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                incomplete = False
                advisory = False
                try:
                    with endpoint._lock:
                        if endpoint._closed.is_set():
                            raise ValueError("native output validator is closed")
                        endpoint._connections.add(self.request)
                    raw = _read_line(self.request, endpoint.max_request_bytes, endpoint.deadline_at)
                    document = _decode(raw)
                    advisory = isinstance(document, dict) and document.get("operation") == "check_draft"
                    response = {"ok": True, "result": endpoint._dispatch(document)}
                    payload = _encode(response)
                    if len(payload) > endpoint.max_request_bytes:
                        raise ValueError("native output response exceeds size limit")
                except _IncompleteRequest as exc:
                    # No complete candidate was submitted. A disconnected probe
                    # must not poison later native Stop requests in this invocation.
                    incomplete = True
                    if endpoint.diagnostics is not None:
                        endpoint.diagnostics.event("native_output_incomplete_request", message=str(exc))
                    payload = _encode({"ok": False, "error": str(exc)})
                except (ValueError, OSError) as exc:
                    message = str(exc) if isinstance(exc, ValueError) else (
                        "native output validation deadline exceeded" if time.monotonic() >= endpoint.deadline_at
                        else "native output transport failed")
                    if not advisory:
                        endpoint._fail(message)
                    payload = _encode({"ok": False, "error": message})
                except Exception:
                    if not advisory:
                        endpoint._fail("native output validation failed")
                    payload = _encode({"ok": False, "error": "native output validation failed"})
                try:
                    self.request.sendall(payload)
                except OSError:
                    if not incomplete and not advisory:
                        endpoint._fail("native output response transport failed")
                finally:
                    with endpoint._lock:
                        endpoint._connections.discard(self.request)

        self.server = _UnixServer(str(self.socket_path), Handler)
        self.socket_path.chmod(0o600)
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.05), daemon=True)
        self.thread.start()

    def _fail(self, message: str) -> None:
        with self._lock:
            self._error = self._error or message
            self._status, self._accepted = "validation_failed", False
        if self.diagnostics is not None:
            self.diagnostics.event("native_output_validation_error", message=message)

    def _dispatch(self, document: Any) -> dict[str, Any]:
        if isinstance(document, dict) and document.get("operation") == "check_draft":
            if (not self.draft_check or set(document) != {"session_id", "operation", "text"}
                    or document.get("session_id") != self.session_id or not isinstance(document.get("text"), str)):
                return {"valid": False, "error": "draft check is unavailable for this session"}
            return self._validate({"session_id": self.session_id, "last_assistant_message": document["text"]},
                                  preview=True)
        if isinstance(document, dict) and document.get("operation") == "prepare_output":
            if not self.structured_submission or set(document) != {"session_id", "operation", "output"}:
                raise ValueError("structured output submission is unavailable")
            return self._validate({"session_id": document["session_id"],
                "last_assistant_message": _encode(document["output"]).decode().rstrip("\n")}, prepare=True)
        # Reuse the existing Attempt/session boundary. An advisory cannot validate
        # a candidate, change tool permissions or consume a correction round.
        if isinstance(document, dict) and document.get("operation") == "context_notice":
            with self._lock:
                _remaining(self.deadline_at)
                if self._closed.is_set() or self._error is not None:
                    raise ValueError("native output validator is unavailable")
                if set(document) != {"session_id", "operation"} or document["session_id"] != self.session_id:
                    raise ValueError("native context notice session mismatch")
            try:
                notice = self.context_notice() if self.context_notice is not None else None
            except Exception:
                # Missing telemetry does not weaken Stop validation or block tools.
                notice = None
            notices = []
            remaining = self._current_deadline() - time.monotonic()
            if 0 < remaining <= self._deadline_notice_window:
                notices.append((
                    f"DESIGN has about {math.ceil(remaining)} seconds left before its existing deadline. "
                    "Finish the smallest runnable change and bounded CPU checks; do not start or keep polling slow training. Preserve draft JSON, "
                    "reusable tests and remaining GPU checks in the retained /workspace. Put device checks at "
                    "the start of the Host-submitted experiment for EXECUTION; DESIGN has no GPU grant. "
                    "Submit complete output with time for native Stop corrections, without claiming unrun checks."
                ) if self.design_implementation else (
                    f"Verifier has about {math.ceil(remaining)} seconds left before its existing deadline. "
                    "Submit the smallest justified review with submit_review now, leaving time for corrections. "
                    "Use existing coverage to state untested conditions; do not claim unrun checks or start full training."
                ) if self.structured_submission else (
                    f"Grounding has about {math.ceil(remaining)} seconds left before its existing deadline. "
                    "Preserve reusable tests and the draft final JSON under /acceptance, update work notes, "
                    "and submit the complete final JSON as soon as scientific checks and smoke inputs are ready. "
                    "Leave time for native Stop corrections; do not weaken criteria or treat drafts as accepted."))
            if isinstance(notice, str) and notice:
                notices.append(notice)
            if self._soft_seconds is not None and 0 < remaining <= self._deadline_notice_window:
                notices.insert(0, "The authoring progress window is ending. Run the Host draft check or submit "
                    "the current result. Only a newly passed check can extend work within the hard deadline.")
            if not notices:
                return {}
            notice = "\n".join(notices)
            notice = notice.encode("utf-8")[:1536].decode("utf-8", errors="ignore")
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": notice}}
        return self._validate(document)

    def _current_deadline(self):
        return self.deadline_at if self._soft_seconds is None else min(self.deadline_at, self._soft_deadline)

    def soft_deadline_expired(self) -> bool:
        return self._soft_seconds is not None and time.monotonic() >= self._current_deadline()

    def _verified(self, rank: int, check: str) -> None:
        if not self.authoring_progress or rank <= self._progress_rank or self._closed.is_set() or self.soft_deadline_expired():
            return
        now = time.monotonic()
        self._progress_rank = rank
        self._last_verified = {"check": check, "at": time.time()}
        if self._soft_seconds is not None:
            self._soft_deadline = min(self.deadline_at, max(self._soft_deadline, now + self._soft_seconds))
        self._record_authoring()

    def _authoring_status(self):
        status = {"last_verified": self._last_verified, "validation_error": self._latest_error,
                  "repeated_errors": self._repeated_errors}
        if self._latest_validation_stage:
            status["validation_stage"] = self._latest_validation_stage
        if self._soft_seconds is not None:
            status["progress_due_at"] = time.time() + self._soft_deadline - time.monotonic()
        return status

    def _record_authoring(self):
        if self.diagnostics is not None and self.authoring_progress:
            self.diagnostics.write_json("authoring-status.json", self._authoring_status())

    def _validate(self, document: Any, *, prepare: bool = False, preview: bool = False) -> dict[str, Any]:
        with self._lock:
            _remaining(self._current_deadline())
            if self._closed.is_set() or self._error is not None:
                raise ValueError("native output validator is unavailable")
            required = {"session_id", "last_assistant_message"}
            if not isinstance(document, dict) or set(document) != required:
                raise ValueError("native output request requires session_id and last_assistant_message")
            if document["session_id"] != self.session_id:
                raise ValueError("native output session mismatch")
            if not isinstance(document["last_assistant_message"], str):
                raise ValueError("native output last_assistant_message must be text")
            if self._rejected >= self.max_failed_validations:
                raise ValueError("native output validation already completed")
            error = None
            hint = ""
            smoke = None
            validation_stage = "json_decode"
            prepared = self._prepared
            if not preview:
                self._prepared = self._accepted = False
                self._completion_text = None
            text = document["last_assistant_message"]
            selected_prepared = False
            try:
                if prepare and self.compact_review:
                    validation_stage = "output_schema"
                    report = _decode(text)
                    if not isinstance(report, dict) or {"task_kind", "typed_candidate", "declared_effects"} & report.keys():
                        raise ValueError("submit_review takes report fields directly; omit task_kind, typed_candidate and declared_effects")
                    if self.verdict_scoped_review:
                        _prepare_review_probe(report)
                    fields = self._validator.schema["properties"]
                    task_kind = fields["typed_candidate"]["properties"]["task_kind"]["const"]
                    effect = fields["declared_effects"]["items"]["properties"]["effect"]["const"]
                    text = _encode({"typed_candidate": {"task_kind": task_kind, **report},
                                    "declared_effects": [{"effect": effect}]}).decode().rstrip("\n")
                selector = _decode(text) if not prepare and self.structured_submission else None
                if isinstance(selector, dict) and set(selector) == {"review_submitted"} and selector["review_submitted"] is True:
                    # A file alone is not a completed invocation. Only this
                    # session's latest successful preparation can be selected.
                    if not prepared:
                        raise ValueError("no prepared review; call submit_review with the complete output")
                    text = self._read_output()
                    selected_prepared = True
                output = _decode_output(text, self._validator)
                self._verified(1, "output_schema")
                if self.candidate_check is not None:
                    validation_stage = "candidate_validation"
                    self.candidate_check(output)
                    self._verified(2, "candidate_validation")
                if self.final_report_check is not None:
                    validation_stage = "final_report"
                    from .git_authoring_workspace import GitAuthoringIntegrityError
                    try:
                        self.final_report_check()
                    except (OSError, GitAuthoringIntegrityError) as exc:
                        raise ValueError(f"final report file is unavailable: {exc}") from exc
                if self.execution_tool_socket is not None:
                    validation_stage = "execution_completion"
                    from .agent_aci import validate_execution_completion
                    from .host_tools_server import _call_host
                    # The existing Attempt-scoped queue reads on the Host thread.
                    # Never use this socket worker to read/write the state store.
                    try:
                        resources = _call_host(str(self.execution_tool_socket), "list_my_experiments", {},
                            max_request_bytes=self.max_request_bytes, deadline_at=self.deadline_at,
                            cancel_event=self._closed)
                        submitted = resources["experiments"]
                    except (ValueError, OSError, KeyError) as exc:
                        raise _ValidationUnavailable("execution completion state is unavailable") from exc
                    latest = {} if not submitted else submitted[-1]
                    # Only Host-observed pre-submit outages permit technical exit.
                    # The Host closes this Attempt as FAILED, never scientific success.
                    if not (not submitted and resources.get("resource_blocked")):
                        validate_execution_completion(latest.get("state"),
                            waiting_for=output.get("typed_candidate", {}).get("waiting_for_experiment_id"),
                            latest_experiment_id=latest.get("exp_id"), can_handoff=latest.get("can_handoff", False))
                if self.acceptance_smoke or self.measurement_smoke:
                    key = "acceptance" if self.acceptance_smoke else "measurement"
                    label = "common" if self.acceptance_smoke else "local C5"
                    declaration = output.get("typed_candidate", {}).get(key)
                    if declaration is not None:
                        validation_stage = "parser_smoke"
                        smoke = parser_smoke(text,
                            source_root=self.parser_source_root, test_root=self.socket_path.parent,
                            semantic_examples=self.semantic_examples, seed_files=self.seed_files,
                            measurement_examples_required=self.measurement_examples_required,
                            declaration_key=key, timeout_seconds=_remaining(self.deadline_at),
                            max_bytes=self.parser_smoke_max_bytes, cancel_event=self._closed,
                            **({"case_verified": lambda count: self._verified(2 + count, f"parser_cases/{count}")}
                               if self.authoring_progress else {}))
                        if smoke.get("error"):
                            raise ValueError(f"{label} parser smoke test: " + str(smoke["error"])[:2048])
                        if self.acceptance_smoke:
                            from .shared_acceptance import SharedAcceptance, normalize_shared_observation
                            normalize_shared_observation(smoke.get("observation"), SharedAcceptance.from_dict(declaration))
                        else:
                            from .measurement_contract import ScientificMeasurement, evaluate_parsed_measurement
                            measurement = evaluate_parsed_measurement(ScientificMeasurement.from_dict(declaration), smoke.get("observation"))
                            smoke["measurement"] = {"parser_value": measurement.value,
                                "baseline": measurement.baseline, "comparison": declaration["comparison"],
                                "metric_value": measurement.metric_value, "criterion_met": measurement.criterion_met}
            except _ValidationUnavailable:
                raise
            except ValueError as exc:
                error = str(exc)
                hint = getattr(exc, "correction_hint", "")
                if validation_stage == "json_decode" and isinstance(exc, _SchemaMismatch):
                    validation_stage = "output_schema"
            _remaining(self._current_deadline())
            if self._closed.is_set():
                raise ValueError("native output validator is closed")
            if self.authoring_progress:
                from .diagnostics import bounded_diagnostic_text
                detail = bounded_diagnostic_text(error + hint) if error else None
                self._latest_validation_stage = validation_stage if detail else None
                if detail and detail == self._latest_error:
                    self._repeated_errors += 1
                    self._record_authoring()
                    hint += " This check has failed again. Repair the indicated part of the saved draft before retrying."
                elif detail != self._latest_error:
                    self._latest_error = detail
                    self._record_authoring()
                if error is None:
                    self._verified(32, "complete_draft")
            if preview:
                return {"valid": error is None, **({"error": error + hint} if error is not None else {})}
            # Repeated Stop rechecks current files and Host state. Changed invalid
            # output revokes acceptance and uses the same remaining correction cap.
            ordinal = len(self._rounds) + 1
            if error is None:
                self._write_output(text)
                self._status = "prepared" if prepare else "recovered" if self._rejected else "first_pass"
                self._prepared = prepare or selected_prepared
            else:
                self._rejected += 1
                self._status = "unresolved"
            self._accepted = error is None and not prepare
            if self._accepted:
                self._completion_text = document["last_assistant_message"]
            # Optional observation of the boundary that actually rejected this
            # completion. Never reconstruct it later from diagnostic prose.
            failure_source = {"validation_stage": validation_stage} if error is not None else {}
            recorded_error = self._latest_error if self.authoring_progress else error
            self._rounds.append({"ordinal": ordinal, "status": ("prepared" if prepare else "accepted") if error is None else "rejected",
                                 "validation_error": recorded_error, **failure_source})
            if self.diagnostics is not None:
                self.diagnostics.write_json(f"native-output/{ordinal:06d}.json", {
                    **document, **({"parser_smoke": smoke} if self.acceptance_smoke or self.measurement_smoke else {}),
                    "ordinal": ordinal, "error": recorded_error, **failure_source})
            _remaining(self.deadline_at)
            if prepare:
                return {"prepared": error is None, **({"error": error + hint,
                    "remaining_corrections": self.max_failed_validations - self._rejected} if error is not None else {
                    "next": 'Finish with {"review_submitted":true}; Stop will revalidate the prepared review. This is not scientific acceptance.'})}
            if error is not None and self._rejected < self.max_failed_validations:
                return {"decision": "block", "reason": error + hint + (
                    ". Correct the complete output using submit_review, then finish with {\"review_submitted\":true}."
                    if self.structured_submission else
                    ". Correct the output and return one complete JSON object matching the full output schema. "
                    "Do not return a partial object, Markdown fences, or prose.") + (
                    " Repair the actual /acceptance parser and smoke fixtures when its execution or output fields fail."
                    if self.acceptance_smoke else
                    " Repair the actual /workspace local C5 parser and /workspace/smoke fixtures to match the frozen measurement."
                    if self.measurement_smoke else "")}
            return {}

    def _write_output(self, text: str) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=".output-", dir=self.output_path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(text.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.output_path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def accepted_text(self, *, final_message: str | None = None) -> str:
        """Read the exact current result after Stop, including after close().

        Invocation state remains authoritative for acceptance. A file alone can
        never recover a cancelled, failed or unvalidated invocation.
        """
        with self._lock:
            if not self._accepted or self._error is not None:
                raise ValueError("native output was not accepted")
            if final_message is not None and final_message != self._completion_text:
                raise ValueError("provider result differs from validated native completion")
            text = self._read_output()
            _decode_output(text, self._validator)
            return text

    def _read_output(self) -> str:
        with self.output_path.open("rb") as stream:
            raw = stream.read(self.max_request_bytes + 1)
        if len(raw) > self.max_request_bytes:
            raise ValueError("native output exceeds size limit")
        return raw.decode("utf-8")

    def terminal_review_failure(self) -> str | None:
        """Report an irreversible validation failure to the owning process adapter."""
        with self._lock:
            if self._rejected >= self.max_failed_validations:
                return "invalid_candidate"
            if self._closed.is_set() or self._error is not None:
                return "invalid_provider_envelope"
            return None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"scope": "native_stop_validation", "status": self._status, "accepted": self._accepted,
                    "call_count": len(self._rounds), "rejected_count": self._rejected,
                    "rounds": [dict(round_) for round_ in self._rounds],
                    "validation_source": "host_stop_hook",
                    **({"authoring": self._authoring_status()} if self.authoring_progress else {}),
                    **({"error": self._error} if self._error is not None else {})}

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        with self._lock:
            connections = tuple(self._connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.socket_path.unlink(missing_ok=True)


def parser_smoke(text: str, *, source_root: Path, test_root: Path, timeout_seconds: float,
                 max_bytes: int, declaration_key: str = "acceptance", semantic_examples: bool = True,
                 seed_files: Mapping[str, bytes] | None = None,
                 measurement_examples_required: bool = False, captured_files: Mapping[str, bytes] | None = None,
                 cancel_event: threading.Event | None = None,
                 case_verified: Callable[[int], None] | None = None) -> dict[str, Any] | None:
    """Snapshot declared fixtures and use the production parser environment.

    This runs only on the Host. Each correction gets fresh inputs and output;
    Agent data roots, authoring trees and environment never reach the parser.
    """
    from .measurement_contract import ScientificMeasurement, measurement_answer
    from .measurement_execution import prepare_parser, run_parser
    from .shared_acceptance import SharedAcceptance

    try:
        output = _decode(text)
        if declaration_key not in {"acceptance", "measurement"}: raise ValueError("unknown parser declaration")
        declaration = output.get("typed_candidate", {}).get(declaration_key) if isinstance(output, dict) else None
        if declaration is None: return None
        if not isinstance(declaration, dict): raise ValueError(f"{declaration_key} must be an object")
        contract = (SharedAcceptance if declaration_key == "acceptance" else ScientificMeasurement).from_dict(declaration)
        deadline = time.monotonic() + min(15.0, timeout_seconds)
        test_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="parser-smoke-", dir=test_root) as temporary:
            work = Path(temporary)
            from .evaluator_examples import (CASES, MEASUREMENT_CASES, check_seed_contract, examples,
                                             fixture_path, measurement_examples)
            from .shared_acceptance import normalize_shared_observation
            if seed_files is not None:
                check_seed_contract(seed_files, contract)
            remaining = max_bytes
            def read(name):
                nonlocal remaining
                if captured_files is None:
                    path = source_root / name
                    if path.resolve() != source_root.resolve() / name or not path.is_file() or path.is_symlink():
                        raise ValueError(f"missing regular smoke input: {name}")
                    with path.open("rb") as stream:
                        content = stream.read(remaining + 1)
                else:
                    if name not in captured_files:
                        raise ValueError(f"missing captured smoke input: {name}")
                    content = captured_files[name]
                remaining -= len(content)
                if remaining < 0:
                    raise ValueError("parser source and smoke fixtures exceed the output byte budget")
                return content

            files = {name: read(name) for name in contract.parser_files}
            def run_case(case, raw, expected, ordinal):
                root = work / str(ordinal)
                for name, content in raw.items():
                    destination = root / "raw" / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(content)
                prepared = prepare_parser(contract, files, raw_dir=root / "raw", work_dir=root / "parser",
                    attempt_id="parser-smoke", execution_id="parser-smoke",
                    timeout_seconds=_remaining(deadline), max_output_bytes=max_bytes)
                result = run_parser(prepared, cancel_event=cancel_event)
                if result.error:
                    with (Path(prepared.process_request.cwd) / "stderr.log").open("rb") as log:
                        log.seek(0, os.SEEK_END); log.seek(max(0, log.tell() - 1536))
                        detail = log.read().decode(errors="replace")
                    raise ValueError(result.error + (": " + detail if detail else ""))
                if expected:
                    if declaration_key == "measurement":
                        try:
                            readings, score = measurement_answer(contract, result.raw_result)
                        except ValueError as exc:
                            raise ValueError(f"case {case}: {exc}") from exc
                        if (readings, score) != expected:
                            mismatched = [unit for unit in readings if readings[unit] != expected[0][unit]]
                            raise ValueError(f"case {case}: expected metric_value {expected[1]}, observed {score}; "
                                             f"check absolute value/baseline in {', '.join(mismatched)}")
                    elif normalize_shared_observation(result.raw_result, contract) != expected[0]:
                        raise ValueError(f"case {case}: actual parser observation differs from the expected observation")
                return result.raw_result

            cases = {}
            if declaration_key == "measurement" and measurement_examples_required:
                cases = measurement_examples(read(MEASUREMENT_CASES), contract)
            if declaration_key == "acceptance" and semantic_examples and (source_root / CASES).exists():
                cases = examples(read(CASES), contract)
            # Keep exact bytes for comparison so unchanged seed cases run only once.
            completed = {}
            baseline = None
            for case in cases or ("baseline",):
                raw = {name: read(fixture_path(case, name)) for name in contract.raw_artifacts}
                actual = run_case(case, raw, cases.get(case), len(completed))
                completed[case] = (raw, actual)
                if case_verified is not None:
                    case_verified(len(completed))
                if case == "baseline":
                    baseline = actual
            if seed_files is not None:
                for i, (case, expected) in enumerate(examples(seed_files[CASES], contract).items(), len(completed)):
                    raw = {name: seed_files[fixture_path(case, name)] for name in contract.raw_artifacts}
                    if case in completed and completed[case][0] == raw:
                        if normalize_shared_observation(completed[case][1], contract) != expected[0]:
                            raise ValueError(f"seed case {case}: actual parser observation differs")
                    else:
                        run_case("seed/" + case, raw, expected, i)
                    if case_verified is not None:
                        case_verified(i + 1)
            return {"observation": baseline, "error": None}
    except (ValueError, OSError, TypeError, AttributeError, KeyError) as exc:
        return {"observation": None, "error": str(exc)[:2048]}


def call_validator(socket_path: str, *, session_id: str, last_assistant_message: str,
                   max_request_bytes: int, timeout_seconds: float) -> dict[str, Any]:
    """Forward native final text; the Host owns schema and parser validation."""
    if not isinstance(session_id, str) or not session_id or not isinstance(last_assistant_message, str):
        raise ValueError("native Stop hook requires session_id and last_assistant_message")
    result = _call_endpoint(socket_path,
        {"session_id": session_id, "last_assistant_message": last_assistant_message},
        max_request_bytes=max_request_bytes, timeout_seconds=timeout_seconds)
    if not (result == {} or (
        set(result) == {"decision", "reason"} and result["decision"] == "block"
        and isinstance(result["reason"], str) and result["reason"])):
        raise ValueError("invalid native Stop validation result")
    return result


def call_context_notice(socket_path: str, *, session_id: str,
                        max_request_bytes: int, timeout_seconds: float) -> dict[str, Any]:
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("native context notice requires session_id")
    result = _call_endpoint(socket_path, {"session_id": session_id, "operation": "context_notice"},
        max_request_bytes=max_request_bytes, timeout_seconds=timeout_seconds)
    if result == {}:
        return result
    output = result.get("hookSpecificOutput")
    if (set(result) != {"hookSpecificOutput"} or not isinstance(output, dict)
            or set(output) != {"hookEventName", "additionalContext"}
            or output["hookEventName"] != "PreToolUse"
            or not isinstance(output["additionalContext"], str)):
        raise ValueError("invalid native context notice result")
    return result


def _call_endpoint(socket_path: str, document: dict[str, Any], *,
                   max_request_bytes: int, timeout_seconds: float) -> dict[str, Any]:
    if type(max_request_bytes) is not int or max_request_bytes < 1:
        raise ValueError("native Stop hook size limit must be positive")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("native Stop hook timeout must be positive and finite")
    deadline = time.monotonic() + timeout_seconds
    payload = _encode(document)
    if len(payload) > max_request_bytes:
        raise ValueError("native Stop hook request exceeds size limit")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(_remaining(deadline))
        connection.connect(socket_path)
        connection.sendall(payload)
        response = _decode(_read_line(connection, max_request_bytes, deadline))
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise ValueError(response.get("error", "native output validator rejected the request")
                         if isinstance(response, dict) else "invalid native output validation response")
    result = response.get("result")
    if not isinstance(result, dict):
        raise ValueError("invalid native output endpoint result")
    return result


def _read_stdin(maximum: int, deadline: float) -> bytes:
    raw = bytearray()
    while True:
        if not select.select([sys.stdin.fileno()], [], [], _remaining(deadline))[0]:
            raise ValueError("native Stop hook input deadline exceeded")
        chunk = os.read(sys.stdin.fileno(), min(65536, maximum + 1 - len(raw)))
        if not chunk:
            return bytes(raw)
        raw.extend(chunk)
        if len(raw) > maximum:
            raise ValueError("native Stop hook input exceeds size limit")


def serve_review_tool(socket_path: str, *, schema_path: str, session_id: str,
                      max_request_bytes: int, timeout_seconds: float, compact_review: bool = False,
                      verdict_scoped_review: bool = False) -> int:
    # Both modules are mounted read-only for the standalone stdlib bridge.
    if __package__:
        from .host_tools_server import serve_mcp
    else:
        from host_tools_server import serve_mcp
    with Path(schema_path).open("rb") as stream:
        raw = stream.read(max_request_bytes + 1)
    if len(raw) > max_request_bytes:
        raise ValueError("output schema exceeds size limit")
    schema = _decode(raw)
    if compact_review:
        report = schema["properties"]["typed_candidate"]
        schema = {**report, "properties": {key: spec for key, spec in report["properties"].items() if key != "task_kind"},
                  "required": [key for key in report["required"] if key != "task_kind"]}
        if verdict_scoped_review:
            # Preserve the admitted object's nested constraints and explicit
            # JSON null compatibility. The Host enforces the verdict dependency.
            probe = next(branch for branch in schema["properties"]["probe"]["anyOf"] if branch["type"] == "object")
            schema["properties"]["probe"] = {**probe, "type": ["object", "null"],
                "description": "Supply a JSON object for probe_required. Omit this field for all other verdicts."}
            schema["required"].remove("probe")
    tool = {"name": "submit_review", "description": (
        ("Prepare the Verifier report fields directly. " if compact_review else
         "Prepare the complete Verifier AgentResponse using the supplied schema. ") +
        "The Host checks fields, references and probe source within the existing correction budget. "
        'After success finish with {"review_submitted":true}; preparation does not approve research.'),
        "inputSchema": schema}
    def invoke(name, arguments):
        result = _call_endpoint(socket_path,
            {"operation": "prepare_output", "session_id": session_id, "output": arguments},
            max_request_bytes=max_request_bytes, timeout_seconds=timeout_seconds)
        if result.get("prepared") is not True:
            raise ValueError(str(result.get("error", "review preparation failed"))
                + f" Remaining failed validations: {result.get('remaining_corrections', 0)}.")
        return result
    return serve_mcp((tool,), invoke, max_request_bytes=max_request_bytes,
        server_name="hypoloom-verifier-output")


def main() -> int:
    if sys.argv[1:2] == ["--check-draft"]:
        parser = argparse.ArgumentParser(description="Check a draft through this attempt's Host validator")
        parser.add_argument("--check-draft", required=True, type=Path)
        path = parser.parse_args().check_draft
        try:
            limit = int(os.environ["HYPOLOOM_DRAFT_MAX_BYTES"])
            if not path.is_file() or not 0 < path.stat().st_size <= limit:
                raise ValueError("expected a nonempty regular draft file within the output budget")
            with path.open("rb") as stream:
                raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("draft exceeds the output budget")
            result = _call_endpoint("/output-validation.sock",
                {"operation": "check_draft", "session_id": os.environ["HYPOLOOM_DRAFT_SESSION"],
                 "text": raw.decode("utf-8")}, max_request_bytes=max(4 * 1024 * 1024, limit * 2),
                timeout_seconds=30)
        except (KeyError, OSError, ValueError) as exc:
            result = {"valid": False, "error": str(exc)}
        sys.stdout.buffer.write(_encode(result))
        return 0 if result.get("valid") is True else 1
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--max-bytes", type=int, required=True)
    parser.add_argument("--timeout-seconds", type=float, required=True)
    parser.add_argument("--context-notice", action="store_true")
    parser.add_argument("--review-tool", action="store_true")
    parser.add_argument("--compact-review", action="store_true")
    parser.add_argument("--verdict-scoped-review", action="store_true")
    parser.add_argument("--output-schema")
    parser.add_argument("--session-id")
    arguments = parser.parse_args()
    try:
        if arguments.max_bytes < 1 or not math.isfinite(arguments.timeout_seconds) or arguments.timeout_seconds <= 0:
            raise ValueError("native Stop hook requires positive size and timeout limits")
        if arguments.review_tool:
            if not arguments.output_schema or not arguments.session_id:
                raise ValueError("review tool requires its schema and session")
            return serve_review_tool(arguments.socket, schema_path=arguments.output_schema,
                session_id=arguments.session_id, max_request_bytes=arguments.max_bytes,
                timeout_seconds=arguments.timeout_seconds, compact_review=arguments.compact_review,
                verdict_scoped_review=arguments.verdict_scoped_review)
        deadline = time.monotonic() + arguments.timeout_seconds
        hook = _decode(_read_stdin(arguments.max_bytes, deadline))
        if not isinstance(hook, dict):
            raise ValueError("native Stop hook input must be an object")
        if arguments.context_notice:
            result = call_context_notice(arguments.socket, session_id=hook.get("session_id"),
                                max_request_bytes=arguments.max_bytes,
                                timeout_seconds=_remaining(deadline))
        else:
            result = call_validator(arguments.socket, session_id=hook.get("session_id"),
                                last_assistant_message=hook.get("last_assistant_message"),
                                max_request_bytes=arguments.max_bytes,
                                timeout_seconds=_remaining(deadline))
    except (ValueError, OSError, TypeError):
        if arguments.review_tool:
            return 1
        result = {} if arguments.context_notice else {
            "continue": False, "stopReason": "Host output validation unavailable; this candidate must not be accepted."}
    sys.stdout.buffer.write(_encode(result))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
