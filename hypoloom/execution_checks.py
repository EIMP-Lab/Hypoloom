"""Task-owned prerequisites, using the existing Process and publication authority."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
from pathlib import Path, PurePosixPath
import re
from types import SimpleNamespace
from typing import Any, Mapping

from .artifacts import ArtifactError, normalize_relative_paths
from .measurement_execution import PreparedParser, materialize_parser, read_parser_result

MAX_RESULT_BYTES = 64 * 1024
MAX_SOURCE_BYTES = 512 * 1024
CHECKS_FAILED = 74


def validate_declaration(value: object) -> dict[str, Any]:
    """One validator for Profile projection and direct GoalDefined admission."""
    if not isinstance(value, Mapping) or set(value) != {"requirements", "argv", "files", "timeout_seconds"}:
        raise ValueError("execution_checks requires requirements, argv, files and timeout_seconds")
    requirements, argv, files, timeout = (value[k] for k in ("requirements", "argv", "files", "timeout_seconds"))
    if (not isinstance(requirements, Mapping) or not 1 <= len(requirements) <= 64
            or any(not isinstance(k, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", k)
                   or not isinstance(v, str) or not v.strip() or "\x00" in v
                   for k, v in requirements.items())):
        raise ValueError("execution_checks requirements needs 1..64 named nonempty descriptions")
    if len(json.dumps(dict(requirements), ensure_ascii=False).encode()) > 16 * 1024:
        raise ValueError("execution_checks requirement descriptions exceed 16 KiB")
    if (not isinstance(files, Mapping) or not 1 <= len(files) <= 64
            or any(not isinstance(k, str) or not isinstance(v, str) or "\x00" in v for k, v in files.items())):
        raise ValueError("execution_checks files needs 1..64 UTF-8 sources")
    try: paths = normalize_relative_paths(files)
    except ArtifactError as exc: raise ValueError(str(exc)) from exc
    if any("\x00" in name or any(parent.as_posix() in files for parent in PurePosixPath(name).parents) for name in paths):
        raise ValueError("execution_checks file paths overlap or contain NUL")
    if any(path == "context.json" or path.startswith("context.json/") for path in paths):
        raise ValueError("execution_checks context.json is reserved for Host inputs")
    if sum(len(v.encode()) for v in files.values()) > MAX_SOURCE_BYTES:
        raise ValueError("execution_checks sources exceed 512 KiB")
    if (not isinstance(argv, (list, tuple)) or len(argv) < 2 or argv[0] != "python"
            or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv)
            or argv[1] not in files or not argv[1].endswith(".py")
            or len(json.dumps(argv).encode()) > 8192):
        raise ValueError("execution_checks argv must start with python and a declared .py source")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("execution_checks timeout_seconds must be finite and positive")
    return {"requirements": dict(requirements), "argv": list(argv), "files": dict(files), "timeout_seconds": timeout}


def contract_checks(contract: Mapping[str, Any]) -> dict[str, Any] | None:
    return contract.get("methodology", {}).get("controls", {}).get("product", {}).get("goal", {}).get("execution_checks")


def publication_paths(value: Mapping[str, Any] | None) -> tuple[str, ...]:
    if value is None: return ()
    return ("raw/stdout.log", "raw/stderr.log", *(
        "raw/execution-checks/" + name for name in
        ("result.json", "stdout.log", "stderr.log", "source/context.json", *("source/" + name for name in value["files"]))))


@dataclass(frozen=True, slots=True)
class PreparedChecks:
    parser: PreparedParser
    requirements: tuple[str, ...]

    def driver_plan(self) -> dict[str, Any]:
        return {"request": self.parser.process_request.to_dict(), "requirements": list(self.requirements),
                "output_dir": str(self.parser.output_dir)}


def plan_checks(value: Mapping[str, Any], prepared, *, workload_argv, python_executable, runtime_prefix) -> PreparedChecks:
    declaration = validate_declaration(value)
    raw = prepared.process_request
    split = raw.argv.index("--")
    sandbox = list(raw.argv[:split])
    root = prepared.attempt_root / "execution-checks"
    source, output, logs = (root / name for name in ("source", "output", "logs"))
    # Copy the exact workload sandbox: patched target, read roots, environment,
    # pinned runtime and granted devices. Only the command and writable output differ.
    matches = [i for i in range(len(sandbox) - 2)
               if sandbox[i:i + 3] == ["--bind", str(prepared.output_dir), "/output"]]
    if len(matches) != 1:
        raise ValueError("execution_checks requires the ordinary workload output binding")
    sandbox[matches[0] + 1] = str(output)
    sandbox.extend(("--ro-bind", str(source), "/hypoloom-checks"))
    interpreter = str(PurePosixPath("/runtime") / Path(python_executable).resolve().relative_to(Path(runtime_prefix).resolve()))
    command = [interpreter, "/hypoloom-checks/" + declaration["argv"][1], *declaration["argv"][2:]]
    if "--dev-bind" in sandbox:
        from .cuda_preflight import launch_prefix
        command = [*launch_prefix(interpreter), *command]
    request = replace(raw, attempt_id=raw.attempt_id + "--checks", argv=tuple([*sandbox, "--", *command]),
        cwd=str(logs), timeout_seconds=min(float(raw.timeout_seconds), declaration["timeout_seconds"]),
        stdout_path="stdout.log", stderr_path="stderr.log")
    context = {"argv": list(workload_argv), "input_dir": "/input", "output_dir": "/output",
        "requirements": declaration["requirements"]}
    files = {name: content.encode("utf-8") for name, content in declaration["files"].items()}
    files["context.json"] = json.dumps(context, sort_keys=True, ensure_ascii=False).encode()
    parser = PreparedParser(process_request=request, source_dir=source, raw_dir=prepared.input_dir,
        output_dir=output, max_output_bytes=MAX_RESULT_BYTES, source_files=tuple(sorted(files.items())))
    return PreparedChecks(parser, tuple(sorted(declaration["requirements"])))


def materialize_checks(prepared: PreparedChecks) -> None:
    materialize_parser(prepared.parser)


def verify_check_sources(prepared: PreparedChecks) -> None:
    for relative, expected in prepared.parser.source_files:
        path = prepared.parser.source_dir / relative
        if path.is_symlink() or not path.is_file() or path.stat().st_size != len(expected) or path.read_bytes() != expected:
            raise ValueError("materialized execution checker differs from admitted source: " + relative)


def read_checks(output_dir: Path, requirements) -> Mapping[str, Any]:
    result = read_parser_result(SimpleNamespace(output_dir=output_dir, max_output_bytes=MAX_RESULT_BYTES))
    if set(result) != {"checks"} or not isinstance(result["checks"], dict) or set(result["checks"]) != set(requirements):
        raise ValueError("execution_checks result must contain exactly the declared checks")
    if any(type(value) is not bool for value in result["checks"].values()):
        raise ValueError("execution_checks results must be Boolean")
    failed = [name for name, value in result["checks"].items() if not value]
    if failed:
        raise ValueError("execution requirements failed: " + ", ".join(sorted(failed)))
    return result


def collect_checks(prepared: PreparedChecks) -> dict[str, bytes]:
    parser = prepared.parser
    files = {"source/" + name: data for name, data in parser.source_files}
    for name, path in [("result.json", parser.output_dir / "result.json"),
            *[(stream + ".log", Path(parser.process_request.cwd) / (stream + ".log")) for stream in ("stdout", "stderr")]]:
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as reader:
                # Diagnostic tails stay bounded even when a checker is broken.
                reader.seek(max(0, path.stat().st_size - MAX_RESULT_BYTES))
                files[name] = reader.read(MAX_RESULT_BYTES)
    return {"raw/execution-checks/" + name: data for name, data in files.items()}


def prompt_checks(request, *, max_bytes: int) -> dict[str, Any] | None:
    """Project the existing admitted input; never infer coverage from free text."""
    for index, item in enumerate(request.input_artifacts):
        if not item.ref.endswith("--fact-goal"):
            continue
        try:
            fact = json.loads(item.content_bytes())
            value = contract_checks(fact["payload"]["research_contract"])
        except (ValueError, KeyError, TypeError):
            continue
        if value is None:
            continue
        projection = {"source_ref": item.ref, "source_path": f"/inputs/{index:04d}.txt",
            "requirements": value["requirements"], "argv": value["argv"],
            "instruction": "Host runs these task-owned prerequisites before every workload. Exercise the actual implementation, including overrides. Failure is a technical repair, not scientific refutation. Inspect the admitted source; candidate declarations cannot replace it."}
        if len(json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()) <= max_bytes:
            return projection
        projection = {"source_ref": item.ref, "source_path": f"/inputs/{index:04d}.txt",
            "instruction": "Read goal.execution_checks in the admitted product controls. Host checks these requirements before workload launch."}
        if len(json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()) <= max_bytes:
            return projection
    return None
