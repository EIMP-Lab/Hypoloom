"""Execute two sandbox commands under an existing outer Process Attempt.

There is no database, outcome authority, or new durable identity here. Internal
children use LocalRunner's existing process groups and parent-death guardian.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import stat
import sys
import threading
from typing import Any, Mapping

RAW_EXECUTION_FAILED = 70
PARSER_EXECUTION_FAILED = 71
GOAL_PARSER_EXECUTION_FAILED = 72
BOTH_PARSERS_EXECUTION_FAILED = 73


def validate_parser_inputs(request: Any) -> None:
    argv = request.argv
    for index, word in enumerate(argv[:-2]):
        if word == "--ro-bind" and argv[index + 2].startswith("/raw/"):
            path = Path(argv[index + 1])
            if path.resolve() != path or not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError("parser raw artifact must be a regular file without symlink aliases")


def sequence_request(raw_request: Any, parser_request: Any, *, attempt_id: str,
                     attempt_ordinal: int, execution_id: str, cwd: str | Path,
                     timeout_seconds: float, python_executable: str = sys.executable,
                     goal_parser_request: Any = None) -> Any:
    from .runner import ProcessRequest
    if parser_request is None and goal_parser_request is None:
        raise ValueError("measurement sequence requires at least one parser")
    if any(request is not None and request.execution_id != execution_id
           for request in (raw_request, parser_request, goal_parser_request)):
        raise ValueError("both internal commands must belong to the outer execution")
    payload = {"raw_request": raw_request.to_dict(),
               "parser_request": None if parser_request is None else parser_request.to_dict()}
    if goal_parser_request is not None:
        payload["goal_parser_request"] = goal_parser_request.to_dict()
    return ProcessRequest(attempt_id=attempt_id, attempt_ordinal=attempt_ordinal,
                          execution_id=execution_id,
                          argv=(python_executable, "-m", "hypoloom.measurement_driver",
                                json.dumps(payload, sort_keys=True, separators=(",", ":"))),
                          cwd=str(cwd), env={"PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                                             "PYTHONDONTWRITEBYTECODE": "1"},
                          timeout_seconds=timeout_seconds, stdout_path="stdout.log", stderr_path="stderr.log")


def _relay(path: Path, stream: Any, stop: threading.Event) -> None:
    offset = 0
    while True:
        if path.is_file():
            with path.open("rb") as reader:
                reader.seek(offset)
                while chunk := reader.read(65536):
                    stream.buffer.write(chunk)
                    stream.buffer.flush()
                    offset += len(chunk)
        if stop.wait(0.05):
            if path.is_file():
                with path.open("rb") as reader:
                    reader.seek(offset)
                    while chunk := reader.read(65536):
                        stream.buffer.write(chunk)
                        stream.buffer.flush()
            return


def _run_phase(phase: str, request: Any) -> bool:
    from .domain import AttemptTerminalReason
    from .runner import LocalRunner
    try:
        if phase != "raw":
            validate_parser_inputs(request)
        stop = threading.Event()
        relays = []
        for name, stream in ((request.stdout_path, sys.stdout), (request.stderr_path, sys.stderr)):
            if name is not None:
                thread = threading.Thread(target=_relay, args=(Path(request.cwd) / name, stream, stop), daemon=True)
                thread.start()
                relays.append(thread)
        try:
            observation = LocalRunner().run(request)
        finally:
            stop.set()
            for thread in relays:
                thread.join(timeout=1)
        if observation.returncode != 0 or observation.terminal_reason is not AttemptTerminalReason.SUCCEEDED:
            print(f"measurement_driver {phase} failed: {observation.terminal_reason.value} exit={observation.returncode}", file=sys.stderr)
            return False
        return True
    except (OSError, ValueError) as exc:
        print(f"measurement_driver {phase} input/execution: {exc}", file=sys.stderr)
        return False


def run_sequence(plan: Mapping[str, Any]) -> int:
    from .runner import ProcessRequest
    old_fields = {"raw_request", "parser_request"}
    if not isinstance(plan, Mapping) or set(plan) not in (old_fields, old_fields | {"goal_parser_request"}):
        raise ValueError("driver requires exact raw/local/optional goal request fields")
    raw = ProcessRequest.from_dict(plan["raw_request"])
    parser = None if plan["parser_request"] is None else ProcessRequest.from_dict(plan["parser_request"])
    goal = None if "goal_parser_request" not in plan else ProcessRequest.from_dict(plan["goal_parser_request"])
    if parser is None and goal is None:
        raise ValueError("measurement sequence requires a local or goal parser")
    if any(item is not None and raw.execution_id != item.execution_id for item in (parser, goal)):
        raise ValueError("internal commands differ in execution ownership")
    if not _run_phase("raw", raw):
        return RAW_EXECUTION_FAILED
    local_ok = parser is None or _run_phase("parser", parser)
    goal_ok = goal is None or _run_phase("goal_parser", goal)
    if not local_ok and not goal_ok:
        return BOTH_PARSERS_EXECUTION_FAILED
    if not goal_ok:
        return GOAL_PARSER_EXECUTION_FAILED
    return 0 if local_ok else PARSER_EXECUTION_FAILED


def _parser_limits(raw: str) -> None:
    # This branch also runs as a stand-alone read-only file inside bwrap; avoid
    # importing the framework or any producer/evaluator code in the sandbox.
    import resource
    limits = json.loads(raw)
    cpu = max(1, math.ceil(float(limits["timeout_seconds"])))
    output = int(limits["max_output_bytes"])
    memory = int(limits["max_memory_bytes"])
    if output <= 0 or memory <= 0:
        raise ValueError("parser limits must be positive")
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_FSIZE, (output, output))
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    command = limits["argv"]
    os.execvpe(command[0], command, dict(os.environ))


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    try:
        if len(arguments) == 2 and arguments[0] == "--parser-limits":
            _parser_limits(arguments[1])
        if len(arguments) != 1:
            raise ValueError("measurement_driver requires one JSON plan argument")
        return run_sequence(json.loads(arguments[0]))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"measurement_driver: {exc}", file=sys.stderr)
        # An invalid new plan cannot claim either internal phase completed.
        return RAW_EXECUTION_FAILED if arguments and '"goal_parser_request"' in arguments[-1] else PARSER_EXECUTION_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
