"""Mechanical raw/local/common parser planning and artifact collection.

The Host supplies accepted declarations and source bytes. This adapter never
selects a protocol, compares observations or writes research facts.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from .execution_workspace import CollectedExecution, PreparedExecution, _stage_candidate
from .measurement_driver import RAW_EXECUTION_FAILED, sequence_request
from .measurement_execution import ParserDeclaration, PreparedParser, materialize_parser, plan_parser, read_parser_result


@dataclass(frozen=True, slots=True)
class PreparedEvaluation:
    execution: PreparedExecution
    local_parser: PreparedParser | None
    goal_parser: PreparedParser | None


def plan_evaluation(prepared: PreparedExecution, *, local_measurement: ParserDeclaration | None,
        local_files: Mapping[str, bytes], goal_acceptance: ParserDeclaration | None,
        goal_files: Mapping[str, bytes], execution_id: str, timeout_seconds: float,
        python_executable: str = sys.executable, runtime_prefix: str = sys.prefix) -> PreparedEvaluation:
    if local_measurement is None and goal_acceptance is None:
        return PreparedEvaluation(prepared, None, None)
    raw = replace(prepared.process_request, stdout_path="raw-stdout.log", stderr_path="raw-stderr.log")
    arguments = dict(raw_dir=prepared.output_dir, execution_id=execution_id,
        timeout_seconds=min(30.0, float(raw.timeout_seconds)), python_executable=python_executable,
        runtime_prefix=runtime_prefix, raw_log_paths={name + ".log": prepared.log_dir / ("raw-" + name + ".log")
                                                   for name in ("stdout", "stderr")})
    local = None if local_measurement is None else plan_parser(local_measurement, local_files,
        work_dir=prepared.attempt_root / "measurement-parser", attempt_id=prepared.attempt_id + "--parser", **arguments)
    goal = None if goal_acceptance is None else plan_parser(goal_acceptance, goal_files,
        work_dir=prepared.attempt_root / "goal-parser", attempt_id=prepared.attempt_id + "--goal-parser", **arguments)
    outer = sequence_request(raw, None if local is None else local.process_request,
        goal_parser_request=None if goal is None else goal.process_request, attempt_id=prepared.attempt_id,
        attempt_ordinal=raw.attempt_ordinal, execution_id=execution_id, cwd=prepared.log_dir,
        timeout_seconds=timeout_seconds)
    return PreparedEvaluation(replace(prepared, process_request=outer), local, goal)


def materialize_evaluation(prepared: PreparedEvaluation) -> None:
    """Called after the ordinary Process intent, alongside raw materialization."""
    for parser in (prepared.local_parser, prepared.goal_parser):
        if parser is not None:
            materialize_parser(parser)


def _completion_code(observation: Mapping[str, Any], *, has_goal: bool) -> int | None:
    code, reason = observation.get("returncode"), observation.get("terminal_reason")
    expected = "SUCCEEDED" if code == 0 else "FAILED"
    completed = (type(code) is int and code in ({0, 70, 71, 72, 73} if has_goal else {0, 70, 71})
                 and reason == expected and not observation.get("synthetic_before_spawn")
                 and not observation.get("integrity_error"))
    return code if completed else None


def raw_execution_failed(observation: Mapping[str, Any]) -> bool:
    """Only the completed driver's raw-failure exit establishes this phase."""
    return _completion_code(observation, has_goal=True) == RAW_EXECUTION_FAILED


def evaluation_phase_success(observation: Mapping[str, Any], *, has_local: bool,
        has_goal: bool) -> tuple[bool, bool | None, bool | None]:
    """Decode actual process completion only, never scientific eligibility."""
    code = _completion_code(observation, has_goal=has_goal)
    raw = code is not None and code != RAW_EXECUTION_FAILED
    return raw, (raw and code in {0, 72}) if has_local else None, (raw and code in {0, 71}) if has_goal else None


def read_evaluation_results(prepared: PreparedEvaluation, observation: Mapping[str, Any]
        ) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
    """Never return a result written by a parser that subsequently failed."""
    _, local_ok, goal_ok = evaluation_phase_success(observation,
        has_local=prepared.local_parser is not None, has_goal=prepared.goal_parser is not None)
    return tuple(read_parser_result(parser) if parser is not None and ok else None
                 for parser, ok in ((prepared.local_parser, local_ok), (prepared.goal_parser, goal_ok)))


def collect_evaluation(prepared: PreparedEvaluation, collected: CollectedExecution,
        observation: Mapping[str, Any]) -> CollectedExecution:
    """Preserve both successful and failed outputs as ordinary publication bytes.

    Availability is determined separately from the actual process observation;
    mere presence of goal/result.json never establishes parser success.
    """
    if prepared.local_parser is None and prepared.goal_parser is None:
        return collected
    execution = prepared.execution
    files = {"raw/" + path: (collected.staging_dir / path).read_bytes() for path in collected.relative_paths}
    for name in ("stdout", "stderr"):
        path = execution.log_dir / ("raw-" + name + ".log")
        if path.is_file() and not path.is_symlink():
            files["raw/" + name + ".log"] = path.read_bytes()
    for prefix, parser in (("", prepared.local_parser), ("goal/", prepared.goal_parser)):
        if parser is None:
            continue
        files.update({prefix + "parser/" + path: data for path, data in parser.source_files})
        result = parser.output_dir / "result.json"
        if result.is_file() and not result.is_symlink() and result.stat().st_size <= parser.max_output_bytes:
            files[prefix + "result.json"] = result.read_bytes()
        for name in (() if not prefix else ("stdout", "stderr")):
            path = Path(parser.process_request.cwd) / (name + ".log")
            if path.is_file() and not path.is_symlink():
                files[prefix + name + ".log"] = path.read_bytes()
    if "result.json" not in files:
        files["result.json"] = json.dumps({"terminal_reason": observation["terminal_reason"],
            "parser_result_unavailable": True}, sort_keys=True, separators=(",", ":")).encode()
    stage = execution.staging_root / "measurement"
    paths = _stage_candidate(stage, files)
    return replace(collected, staging_dir=stage, relative_paths=paths)
