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
        raise ValueError(f"invalid JSON at line {exc.lineno}, column {exc.colno}") from exc
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
        path = "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1")
                               for part in error.absolute_path)
        # jsonschema's human message often embeds the candidate value. Keep only
        # its precise location and constraint in the redacted diagnostic projection.
        hint = ""
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
            raise ValueError("native output transport ended before a complete JSON line")
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
                 parser_source_root: Path | None = None, parser_smoke_max_bytes: int | None = None,
                 execution_tool_socket: Path | None = None,
                 candidate_check: Callable[[Mapping[str, Any]], Any] | None = None,
                 final_report_check: Callable[[], Any] | None = None,
                 context_notice: Callable[[], str | None] | None = None) -> None:
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
        self._deadline_notice_window = (min(300.0, max(0.0, deadline_at - time.monotonic()) * 0.2)
            if acceptance_smoke else 0.0)
        self.diagnostics = diagnostics
        self.acceptance_smoke = acceptance_smoke
        self.measurement_smoke = measurement_smoke
        self.execution_tool_socket = execution_tool_socket
        self.candidate_check = candidate_check
        self.final_report_check = final_report_check
        self.context_notice = context_notice
        self.parser_source_root = parser_source_root
        self.parser_smoke_max_bytes = parser_smoke_max_bytes or max(1, max_request_bytes // 2)
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._connections: set[socket.socket] = set()
        self._status = "not_observed"
        self._accepted = False
        self._accepted_message: str | None = None
        self._error: str | None = None
        self._rounds: list[dict[str, Any]] = []
        self._rejected = 0
        endpoint = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                try:
                    with endpoint._lock:
                        if endpoint._closed.is_set():
                            raise ValueError("native output validator is closed")
                        endpoint._connections.add(self.request)
                    raw = _read_line(self.request, endpoint.max_request_bytes, endpoint.deadline_at)
                    response = {"ok": True, "result": endpoint._dispatch(_decode(raw))}
                    payload = _encode(response)
                    if len(payload) > endpoint.max_request_bytes:
                        raise ValueError("native output response exceeds size limit")
                except (ValueError, OSError) as exc:
                    message = str(exc) if isinstance(exc, ValueError) else (
                        "native output validation deadline exceeded" if time.monotonic() >= endpoint.deadline_at
                        else "native output transport failed")
                    endpoint._fail(message)
                    payload = _encode({"ok": False, "error": message})
                except Exception:
                    endpoint._fail("native output validation failed")
                    payload = _encode({"ok": False, "error": "native output validation failed"})
                try:
                    self.request.sendall(payload)
                except OSError:
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
            remaining = self.deadline_at - time.monotonic()
            if 0 < remaining <= self._deadline_notice_window:
                notices.append(
                    f"Grounding has about {math.ceil(remaining)} seconds left before its existing deadline. "
                    "Preserve reusable tests and the draft final JSON under /acceptance, update work notes, "
                    "and submit the complete final JSON as soon as scientific checks and smoke inputs are ready. "
                    "Leave time for native Stop corrections; do not weaken criteria or treat drafts as accepted.")
            if isinstance(notice, str) and notice:
                notices.append(notice)
            if not notices:
                return {}
            notice = "\n".join(notices)
            notice = notice.encode("utf-8")[:1536].decode("utf-8", errors="ignore")
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": notice}}
        return self._validate(document)

    def _validate(self, document: Any) -> dict[str, Any]:
        with self._lock:
            _remaining(self.deadline_at)
            if self._closed.is_set() or self._error is not None:
                raise ValueError("native output validator is unavailable")
            required = {"session_id", "last_assistant_message"}
            if not isinstance(document, dict) or set(document) != required:
                raise ValueError("native output request requires session_id and last_assistant_message")
            if document["session_id"] != self.session_id:
                raise ValueError("native output session mismatch")
            if not isinstance(document["last_assistant_message"], str):
                raise ValueError("native output last_assistant_message must be text")
            repeated = self._accepted
            if repeated and document["last_assistant_message"] != self._accepted_message:
                raise ValueError("native output changed after acceptance")
            if self._rejected >= self.max_failed_validations:
                raise ValueError("native output validation already completed")
            error = None
            hint = ""
            smoke = None
            try:
                output = _decode_output(document["last_assistant_message"], self._validator)
                if self.candidate_check is not None:
                    self.candidate_check(output)
                if self.final_report_check is not None:
                    from .git_authoring_workspace import GitAuthoringIntegrityError
                    try:
                        self.final_report_check()
                    except (OSError, GitAuthoringIntegrityError) as exc:
                        raise ValueError(f"final report file is unavailable: {exc}") from exc
                if self.execution_tool_socket is not None:
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
                        smoke = parser_smoke(document["last_assistant_message"],
                            source_root=self.parser_source_root, test_root=self.socket_path.parent,
                            declaration_key=key, timeout_seconds=_remaining(self.deadline_at),
                            max_bytes=self.parser_smoke_max_bytes, cancel_event=self._closed)
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
            _remaining(self.deadline_at)
            if self._closed.is_set():
                raise ValueError("native output validator is closed")
            # CC can repeat Stop after a background-task notification. Reuse
            # the same validator, including current files and Host state: prior
            # acceptance alone must not bless changed source or report content.
            if repeated and error is not None:
                raise ValueError(error)
            ordinal = len(self._rounds) + 1
            self._rounds.append({"ordinal": ordinal, "status": "accepted" if error is None else "rejected",
                                 "validation_error": error})
            self._accepted = error is None
            if error is None:
                self._accepted_message = document["last_assistant_message"]
                self._status = "recovered" if self._rejected else "first_pass"
            else:
                self._rejected += 1
                self._status = "unresolved"
            if self.diagnostics is not None:
                self.diagnostics.write_json(f"native-output/{ordinal:06d}.json", {
                    **document, **({"parser_smoke": smoke} if self.acceptance_smoke or self.measurement_smoke else {}),
                    "ordinal": ordinal, "error": error})
            _remaining(self.deadline_at)
            if error is not None and self._rejected < self.max_failed_validations:
                return {"decision": "block", "reason": error + hint + (
                    ". Correct the output and return one complete JSON object matching the full output schema. "
                    "Do not return a partial object, Markdown fences, or prose.") + (
                    " Repair the actual /acceptance parser and smoke fixtures when its execution or output fields fail."
                    if self.acceptance_smoke else
                    " Repair the actual /workspace local C5 parser and /workspace/smoke fixtures; its output requires metric, value and optional baseline."
                    if self.measurement_smoke else "")}
            return {}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"scope": "native_stop_validation", "status": self._status, "accepted": self._accepted,
                    "call_count": len(self._rounds), "rejected_count": self._rejected,
                    "rounds": [dict(round_) for round_ in self._rounds],
                    "validation_source": "host_stop_hook",
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
                 max_bytes: int, declaration_key: str = "acceptance",
                 cancel_event: threading.Event | None = None) -> dict[str, Any] | None:
    """Snapshot declared fixtures and use the production parser environment.

    This runs only on the Host. Each correction gets fresh inputs and output;
    Agent data roots, authoring trees and environment never reach the parser.
    """
    from .measurement_contract import ScientificMeasurement
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
            raw_root = work / "raw"
            raw_root.mkdir()
            files = {}
            remaining = max_bytes
            for key, origin, names in (("parser_files", source_root, contract.parser_files),
                    ("raw_artifacts", source_root / "smoke", contract.raw_artifacts)):
                for name in names:
                    path = origin / name
                    path.resolve().relative_to(origin.resolve())
                    if path.is_symlink() or not path.is_file():
                        raise ValueError(f"missing regular smoke input: {key}/{name}")
                    with path.open("rb") as stream: content = stream.read(remaining + 1)
                    remaining -= len(content)
                    if remaining < 0: raise ValueError("parser source and smoke fixtures exceed the output byte budget")
                    if key == "parser_files":
                        files[name] = content
                    else:
                        destination = raw_root / name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(content)
            prepared = prepare_parser(contract, files, raw_dir=raw_root, work_dir=work / "parser",
                attempt_id="parser-smoke", execution_id="parser-smoke",
                timeout_seconds=_remaining(deadline), max_output_bytes=max_bytes)
            result = run_parser(prepared, cancel_event=cancel_event)
            if result.error:
                with (Path(prepared.process_request.cwd) / "stderr.log").open("rb") as log:
                    log.seek(0, os.SEEK_END); log.seek(max(0, log.tell() - 1536))
                    detail = log.read().decode(errors="replace")
                raise ValueError(result.error + (": " + detail if detail else ""))
            return {"observation": result.raw_result, "error": None}
    except (ValueError, OSError, TypeError, AttributeError) as exc:
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--max-bytes", type=int, required=True)
    parser.add_argument("--timeout-seconds", type=float, required=True)
    parser.add_argument("--context-notice", action="store_true")
    arguments = parser.parse_args()
    try:
        if arguments.max_bytes < 1 or not math.isfinite(arguments.timeout_seconds) or arguments.timeout_seconds <= 0:
            raise ValueError("native Stop hook requires positive size and timeout limits")
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
        result = {} if arguments.context_notice else {
            "continue": False, "stopReason": "Host output validation unavailable; this candidate must not be accepted."}
    sys.stdout.buffer.write(_encode(result))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
