"""CPU-only executable measurement parser adapter, with no fact authority."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import sys
import threading
from typing import Any, Mapping, Protocol

from .domain import AttemptTerminalReason
from .measurement_contract import measurement_parser_files
from .measurement_driver import validate_parser_inputs
from .runner import LocalRunner, ProcessObservation, ProcessRequest


class ParserDeclaration(Protocol):
    """Only executable input mechanics; no metric or acceptance authority."""
    parser_argv: tuple[str, ...]
    parser_files: tuple[str, ...]
    raw_artifacts: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class PreparedParser:
    process_request: ProcessRequest
    source_dir: Path
    raw_dir: Path
    output_dir: Path
    max_output_bytes: int
    source_files: tuple[tuple[str, bytes], ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ParserExecutionResult:
    observation: ProcessObservation
    raw_result: Mapping[str, Any] | None
    error: str | None = None


def plan_parser(declaration: ParserDeclaration, workspace_files: Mapping[str, bytes], *,
                   raw_dir: str | Path, work_dir: str | Path, attempt_id: str, execution_id: str,
                   timeout_seconds: float = 30, max_output_bytes: int = 1024 * 1024,
                   max_memory_bytes: int = 2 * 1024**3, python_executable: str = sys.executable,
                   runtime_prefix: str = sys.prefix, bwrap_path: str = "/usr/bin/bwrap",
                   raw_log_paths: Mapping[str, str | Path] | None = None) -> PreparedParser:
    """Build the CPU request without writing; raw/source directories may not exist."""
    files = measurement_parser_files(declaration, workspace_files)
    if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("parser timeout must be finite and positive")
    for value in (max_output_bytes, max_memory_bytes):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("parser byte limits must be positive integers")
    root, raw = Path(work_dir).absolute(), Path(raw_dir).absolute()
    if raw.is_symlink() or (raw.exists() and not raw.is_dir()) or raw.resolve() != raw:
        raise ValueError("parser raw root must be a canonical directory")
    prefix = Path(runtime_prefix).resolve()
    executable = Path(python_executable).resolve()
    interpreter = "/runtime/" + executable.relative_to(prefix).as_posix()
    if any(prefix == item or prefix in item.parents for item in (root, Path(__file__).resolve())):
        raise ValueError("parser runtime mount is too broad")
    source, output, logs = root / "input", root / "parsed", root / "logs"
    argv = [bwrap_path, "--unshare-all", "--die-with-parent", "--new-session",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/runtime",
            "--ro-bind", str(prefix), "/runtime"]
    for path in ("/lib", "/lib64", "/usr/lib", "/usr/bin", "/etc/ld.so.cache"):
        if Path(path).exists():
            argv.extend(("--ro-bind", path, path))
    argv.extend(("--ro-bind", "/usr/bin", "/bin", "--ro-bind", str(source), "/input",
                 "--bind", str(output), "/parsed", "--dir", "/raw"))
    directories = {"/raw"}
    for relative in declaration.raw_artifacts:
        destination = Path("/raw") / relative
        for parent in reversed(destination.parents):
            if str(parent) not in {"/", *directories}:
                argv.extend(("--dir", str(parent)))
                directories.add(str(parent))
        raw_path = Path((raw_log_paths or {}).get(relative, raw / relative)).absolute()
        argv.extend(("--ro-bind", str(raw_path), str(destination)))
    driver = Path(__file__).with_name("measurement_driver.py").resolve()
    argv.extend(("--ro-bind", str(driver), "/measurement-driver.py"))
    environment = {"HOME": "/nonexistent", "PATH": "/runtime/bin:/usr/bin:/bin",
                   "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "TMPDIR": "/tmp",
                   "CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                   "HYPOLOOM_MEASUREMENT_RAW_ROOT": "/raw",
                   "HYPOLOOM_MEASUREMENT_RESULT_PATH": "/parsed/result.json"}
    for name, value in sorted(environment.items()):
        argv.extend(("--setenv", name, value) if value else ("--unsetenv", name))
    command = list(declaration.parser_argv)
    if command[0] == "python":
        command[0] = interpreter
    command = ["/input/" + item if item in files else item for item in command]
    limits = {"argv": command, "timeout_seconds": timeout_seconds, "max_output_bytes": max_output_bytes,
              "max_memory_bytes": max_memory_bytes}
    argv.extend(("--chdir", "/parsed", "--", interpreter, "/measurement-driver.py", "--parser-limits",
                 json.dumps(limits, sort_keys=True, separators=(",", ":"))))
    request = ProcessRequest(attempt_id=attempt_id, attempt_ordinal=1, execution_id=execution_id,
                             argv=tuple(argv), cwd=str(logs), env={}, timeout_seconds=timeout_seconds,
                             stdout_path="stdout.log", stderr_path="stderr.log")
    return PreparedParser(process_request=request, source_dir=source, raw_dir=raw,
                          output_dir=output, max_output_bytes=max_output_bytes,
                          source_files=tuple(sorted(files.items())))


def materialize_parser(prepared: PreparedParser) -> None:
    """Create exact fresh parser inputs only after the owner records its OPEN intent."""
    root = prepared.source_dir.parent
    if root.exists() or root.is_symlink():
        raise ValueError("parser workspace must be fresh")
    root.mkdir(mode=0o700, parents=True)
    for path in (prepared.source_dir, prepared.output_dir, Path(prepared.process_request.cwd)):
        path.mkdir(mode=0o700)
    for relative, data in prepared.source_files:
        path = prepared.source_dir / relative
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(data)


def prepare_parser(declaration: ParserDeclaration, workspace_files: Mapping[str, bytes],
                   **kwargs: Any) -> PreparedParser:
    """Fresh-temp convenience for parser testing/Oracle, not Host intent planning."""
    prepared = plan_parser(declaration, workspace_files, **kwargs)
    materialize_parser(prepared)
    return prepared


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("parsed result contains duplicate keys")
        result[key] = value
    return result


def read_parser_result(prepared: PreparedParser) -> Mapping[str, Any]:
    path = prepared.output_dir / "result.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > prepared.max_output_bytes:
        raise ValueError("parser result is absent, nonregular, or too large")
    if tuple(sorted(item.name for item in prepared.output_dir.iterdir())) != ("result.json",):
        raise ValueError("parser produced undeclared files")
    def reject_constant(value: str) -> Any:
        raise ValueError(f"nonfinite parsed JSON constant {value}")
    raw = json.loads(path.read_bytes(), object_pairs_hook=_unique_object, parse_constant=reject_constant)
    if not isinstance(raw, Mapping):
        raise ValueError("parser result must be an object")
    return raw


def run_parser(prepared: PreparedParser, *, cancel_event: threading.Event | None = None) -> ParserExecutionResult:
    validate_parser_inputs(prepared.process_request)
    observation = LocalRunner().run(prepared.process_request, cancel_event=cancel_event)
    if observation.terminal_reason is not AttemptTerminalReason.SUCCEEDED:
        return ParserExecutionResult(observation=observation, raw_result=None,
                                     error=f"parser {observation.terminal_reason.value}")
    try:
        return ParserExecutionResult(observation=observation, raw_result=read_parser_result(prepared))
    except (OSError, ValueError, UnicodeError) as exc:
        return ParserExecutionResult(observation=observation, raw_result=None, error=str(exc))
