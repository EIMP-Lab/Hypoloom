"""Independent common-protocol source, execution and numerical reconstruction.

Only the sandbox parser mechanism is shared with production. This module never
imports the common acceptance validator, Host, Policy or research reducer.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path, PurePosixPath
import tempfile
from typing import Any, Mapping


def _need(condition, message):
    if not condition:
        raise ValueError(message)


def _number(value):
    _need(
        type(value) in {str, int, float, Decimal}, "shared observation is not a decimal"
    )
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("shared observation is not a decimal") from exc
    _need(number.is_finite(), "shared observation is nonfinite")
    return number


def _normal(value):
    number = _number(value)
    if not number:
        return "0"
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _names(value, label, *, nonempty=False, paths=False):
    _need(
        isinstance(value, (list, tuple))
        and (bool(value) or not nonempty)
        and all(
            isinstance(item, str) and item.strip() and "\x00" not in item
            for item in value
        )
        and len(set(value)) == len(value),
        "invalid shared " + label,
    )
    if paths:
        _need(
            all(
                not PurePosixPath(item).is_absolute()
                and PurePosixPath(item).as_posix() == item
                and "\\" not in item
                and not {"", ".", ".."}.intersection(item.split("/"))
                for item in value
            )
            and not any(
                PurePosixPath(left) in PurePosixPath(right).parents
                for left in value
                for right in value
                if left != right
            ),
            "unsafe shared " + label,
        )
    return tuple(value)


@dataclass(frozen=True)
class _Parser:
    parser_argv: tuple[str, ...]
    parser_files: tuple[str, ...]
    raw_artifacts: tuple[str, ...]


def _declaration(raw):
    _need(
        isinstance(raw, Mapping)
        and set(raw)
        == {
            "summary",
            "rule",
            "criteria",
            "validity_checks",
            "parser_argv",
            "parser_files",
            "raw_artifacts",
        },
        "shared acceptance fields differ",
    )
    _need(
        isinstance(raw["summary"], str)
        and raw["summary"].strip()
        and raw["rule"] in {"ALL", "ANY"},
        "shared summary/rule is invalid",
    )
    criteria = raw["criteria"]
    _need(isinstance(criteria, list) and criteria, "shared criteria are empty")
    names = []
    for item in criteria:
        _need(isinstance(item, Mapping), "shared criterion is not an object")
        _names([item.get("name")], "criterion name", nonempty=True)
        operator = item.get("operator")
        _need(
            operator in {"GT", "GE", "LT", "LE", "EQ", "IS_TRUE"},
            "shared operator is unknown",
        )
        _need(
            set(item)
            == (
                {"name", "operator"}
                if operator == "IS_TRUE"
                else {"name", "operator", "target"}
            ),
            "shared criterion fields differ",
        )
        if operator != "IS_TRUE":
            _number(item["target"])
        names.append(item["name"])
    validity = _names(raw["validity_checks"], "validity checks")
    _need(
        len(set(names)) == len(names) and not set(names).intersection(validity),
        "shared criterion names overlap",
    )
    argv = raw["parser_argv"]
    _need(
        isinstance(argv, (list, tuple))
        and argv
        and all(
            isinstance(item, str) and item.strip() and "\x00" not in item
            for item in argv
        ),
        "shared parser argv is invalid",
    )
    return _Parser(
        tuple(argv),
        _names(raw["parser_files"], "parser files", nonempty=True, paths=True),
        _names(raw["raw_artifacts"], "raw artifacts", nonempty=True, paths=True),
    )


def normalized_declaration(raw):
    """Compare validated numeric targets exactly, independent of spelling.

    All nonnumeric fields and the separately verified parser bytes stay exact.
    Do not mutate the accepted source or use the production evaluator here.
    """
    _declaration(raw)
    return {**raw, "criteria": [
        dict(item) if item["operator"] == "IS_TRUE"
        else {**item, "target": _normal(item["target"])}
        for item in raw["criteria"]
    ]}


@dataclass(frozen=True)
class SharedOracleResult:
    observations: Mapping[str, Any]
    criteria: tuple[tuple[str, bool], ...]
    validity_met: bool
    goal_met: bool


def interpret_shared_acceptance(
    declaration: Mapping[str, Any], result: Mapping[str, Any]
) -> SharedOracleResult:
    _declaration(declaration)
    _need(
        isinstance(result, Mapping) and set(result) == {"measurements", "checks"},
        "shared parser emitted extra/missing fields",
    )
    measurements, checks = result["measurements"], result["checks"]
    numeric = {
        item["name"]
        for item in declaration["criteria"]
        if item["operator"] != "IS_TRUE"
    }
    boolean = {
        item["name"]
        for item in declaration["criteria"]
        if item["operator"] == "IS_TRUE"
    } | set(declaration["validity_checks"])
    _need(
        isinstance(measurements, Mapping)
        and isinstance(checks, Mapping)
        and set(measurements) == numeric
        and set(checks) == boolean
        and all(type(value) is bool for value in checks.values()),
        "shared observations differ from exact declared names/types",
    )
    normalized = {name: _normal(value) for name, value in measurements.items()}
    criteria = []
    for item in declaration["criteria"]:
        name, op = item["name"], item["operator"]
        if op == "IS_TRUE":
            passed = checks[name]
        else:
            actual, target = _number(normalized[name]), _number(item["target"])
            passed = {
                "GT": actual > target,
                "GE": actual >= target,
                "LT": actual < target,
                "LE": actual <= target,
                "EQ": actual == target,
            }[op]
        criteria.append((name, passed))
    valid = all(checks[name] for name in declaration["validity_checks"])
    values = [value for _, value in criteria]
    return SharedOracleResult(
        {"measurements": normalized, "checks": dict(checks)},
        tuple(criteria),
        valid,
        valid and (all(values) if declaration["rule"] == "ALL" else any(values)),
    )


def _json_file(path):
    from .measurement_oracle import _read_result

    return _read_result(Path(path))


def _workspace(response):
    files = {}
    for item in response.get("proposed_workspace_artifacts", ()):
        name = item["path"]
        _names([name], "source path", nonempty=True, paths=True)
        _need(name not in files, "shared source file repeats")
        files[name] = base64.b64decode(item["content_base64"], validate=True)
    return files


def grounding_source(
    source_attempt_id, run_id, attempts, observations, artifact_root=None
):
    matches = []
    for original in attempts:
        row = dict(original)
        observation = observations.get(row["attempt_id"], {})
        request, response = (
            observation.get("request", {}),
            observation.get("response", {}),
        )
        candidate = response.get("typed_candidate", {})
        if (
            request.get("command") == "GROUNDING"
            and candidate.get("acceptance") is not None
            and row.get("terminal_reason") == "SUCCEEDED"
        ):
            matches.append((row, request, response, candidate, observation))
    _need(
        len(matches) == 1, "common protocol lacks one final accepted Grounding response"
    )
    row, request, response, candidate, observation = matches[0]
    _need(
        row["attempt_id"] == source_attempt_id
        and row["kind"] == "AGENT"
        and row["state"] == "CLOSED"
        and row["run_id"] == row["workflow_id"] == request["run_id"] == run_id
        and request["attempt_id"] == source_attempt_id
        and request["command_id"] == row["command_id"]
        and type(request["schema_version"]) is int
        and request["schema_version"] >= 16
        and candidate.get("clarification") is None
        and isinstance(candidate.get("background"), Mapping),
        "common protocol references another question, run or unsuccessful source",
    )
    acceptance = candidate["acceptance"]
    parser = _declaration(acceptance)
    files = _workspace(response)
    _need(
        set(files) == set(parser.parser_files) and all(files.values()),
        "accepted common source inventory differs",
    )
    archive = observation.get("session", {}).get("transcript")
    if archive is not None and artifact_root is not None:
        _need(
            archive.get("artifact_id")
            == ":".join((run_id, "agent", source_attempt_id, "transcript")),
            "common source archive has another owner",
        )
        root = (
            Path(artifact_root)
            / run_id
            / "agent"
            / source_attempt_id
            / "transcript"
            / "evaluator"
        )
        for name, data in files.items():
            path = root / name
            _need(
                path.is_file() and not path.is_symlink() and path.read_bytes() == data,
                "archived common evaluator differs from accepted Grounding source",
            )
    return acceptance, files, row


def audit_shared_bindings(
    artifact_root, run_id, facts, payloads, attempts, observations
):
    """Check preregistration against raw source even before an outcome exists."""
    issues = []
    goals = [
        payloads[str(row["fact_id"])]["research_contract"]
        for row in facts
        if row["fact_type"] == "GoalDefined"
    ]
    active = (
        len(goals) == 1
        and goals[0].get("schema_version") == 3
        and goals[0].get("goal", {}).get("evaluation") == {"mode": "grounding"}
    )
    for attempt in attempts:
        observed = observations.get(attempt["attempt_id"], {})
        request = observed.get("request", {})
        inputs = [
            item
            for item in request.get("input_artifacts", ())
            if str(item.get("ref", "")).endswith("--shared-acceptance")
        ]
        if not inputs:
            if (
                active
                and (request.get("task_context") or {}).get("kind") == "RESEARCH_NODE"
            ):
                issues.append(
                    (
                        "shared_acceptance_input_invalid",
                        "expert request lacks its accepted common evaluator",
                        (str(attempt["attempt_id"]),),
                    )
                )
            continue
        try:
            _need(
                active
                and len(inputs) == 1
                and inputs[0].get("media_type") == "application/json"
                and inputs[0]["ref"] in request.get("immutable_context_refs", ()),
                "common evaluator input is not uniquely admitted",
            )
            value = json.loads(
                base64.b64decode(inputs[0]["content_base64"], validate=True)
            )
            _need(
                set(value) == {"source_attempt_id", "acceptance", "files"},
                "common evaluator input fields differ",
            )
            declaration, files, _ = grounding_source(
                value["source_attempt_id"],
                run_id,
                attempts,
                observations,
                artifact_root,
            )
            _need(
                normalized_declaration(value["acceptance"]) == normalized_declaration(declaration)
                and _workspace({"proposed_workspace_artifacts": value["files"]})
                == files,
                "expert common evaluator source differs from final Grounding bytes",
            )
        except (KeyError, TypeError, ValueError, OSError) as exc:
            issues.append(
                (
                    "shared_acceptance_input_invalid",
                    str(exc),
                    (str(attempt["attempt_id"]),),
                )
            )
    for row in facts:
        if row["fact_type"] == "CanonicalOutcomeAccepted":
            payload = payloads[str(row["fact_id"])]
            if payload.get("goal_evaluation") is not None and payload.get(
                "outcome"
            ) in {"INTEGRITY_REJECTED", "CANCELLED"}:
                issues.append(
                    (
                        "shared_acceptance_disposition_invalid",
                        "integrity-rejected or cancelled evidence cannot carry accepted common observations",
                        (str(row["fact_id"]),),
                    )
                )
        if row["fact_type"] != "ExperimentPreregistered":
            continue
        payload = payloads[str(row["fact_id"])]
        binding = payload.get("goal_evaluation")
        try:
            _need(
                active == (binding is not None),
                "preregistration common evaluation mode differs from original commission",
            )
            if binding is None:
                continue
            _need(
                isinstance(binding, Mapping)
                and set(binding) == {"scope", "source_attempt_id", "acceptance"}
                and binding["scope"] in {"LOCAL", "GOAL"},
                "common preregistration binding fields differ",
            )
            declaration, _, source = grounding_source(
                binding["source_attempt_id"],
                run_id,
                attempts,
                observations,
                artifact_root,
            )
            _need(
                normalized_declaration(binding["acceptance"]) == normalized_declaration(declaration),
                "preregistration changed the accepted common protocol",
            )
            experiment_attempts = [
                item
                for item in attempts
                if dict(item).get("execution_id") == row["execution_id"]
            ]
            _need(
                all(
                    float(item["started_at"]) >= float(source["ended_at"])
                    for item in experiment_attempts
                ),
                "experiment preceded its completed common protocol",
            )
        except (KeyError, TypeError, ValueError, OSError) as exc:
            issues.append(
                ("shared_acceptance_source_invalid", str(exc), (str(row["fact_id"]),))
            )
    return issues


def phase_success(observation, *, local, goal):
    """Independent interpretation of the pinned three-stage driver's exit ABI."""
    code = observation.get("returncode")
    complete = (
        type(code) is int
        and code in ({0, 70, 71, 72, 73} if goal else {0, 70, 71})
        and observation.get("terminal_reason")
        == ("SUCCEEDED" if code == 0 else "FAILED")
        and not observation.get("synthetic_before_spawn")
        and not observation.get("integrity_error")
    )
    raw = complete and code != 70
    return (
        raw,
        (raw and code in {0, 72}) if local else None,
        (raw and code in {0, 71}) if goal else None,
    )


def audit_shared_result(
    declaration,
    files,
    publication,
    *,
    runtime_prefix,
    python_executable,
    timeout_seconds=30,
):
    from .measurement_execution import prepare_parser, run_parser

    typed = _declaration(declaration)
    root = Path(publication)
    for relative in typed.parser_files:
        path = root / "goal" / "parser" / relative
        _need(
            path.is_file()
            and not path.is_symlink()
            and path.read_bytes() == files.get(relative),
            "published common parser differs from accepted Grounding bytes",
        )
    for relative in typed.raw_artifacts:
        path = root / "raw" / relative
        _need(
            path.is_file() and not path.is_symlink() and path.resolve() == path,
            "common raw input is absent or indirect",
        )
    recorded = _json_file(root / "goal" / "result.json")
    with tempfile.TemporaryDirectory(prefix="hypoloom-shared-oracle-") as directory:
        prepared = prepare_parser(
            typed,
            files,
            raw_dir=root / "raw",
            work_dir=Path(directory) / "parser",
            attempt_id="oracle-common-parser",
            execution_id="oracle-common-evaluation",
            runtime_prefix=runtime_prefix,
            python_executable=python_executable,
            timeout_seconds=timeout_seconds,
        )
        replay = run_parser(prepared)
        _need(
            replay.error is None and replay.raw_result == recorded,
            "shared parser replay differs from actual published output",
        )
    return interpret_shared_acceptance(declaration, recorded)


def audit_parser_process_requests(request, payload):
    """Independently reconstruct the existing raw/local/common driver ABI."""
    outer = request["argv"]
    _need(
        len(outer) == 4 and outer[1:3] == ["-m", "hypoloom.measurement_driver"],
        "shared experiment lacks its supervised parser sequence",
    )
    plan = json.loads(outer[3])
    binding = payload["goal_evaluation"]
    goal = binding["scope"] == "GOAL"
    _need(
        set(plan)
        == (
            {"raw_request", "parser_request", "goal_parser_request"}
            if goal
            else {"raw_request", "parser_request"}
        ),
        "shared process plan differs from preregistered evaluation scope",
    )
    raw = plan["raw_request"]
    _need(
        (payload.get("measurement") is None) == (plan["parser_request"] is None),
        "local parser declaration/request differs",
    )
    source = request["env"].get("PYTHONPATH")
    _need(
        isinstance(source, str)
        and PurePosixPath(source).is_absolute()
        and str(PurePosixPath(source)) == source
        and ".." not in PurePosixPath(source).parts
        and ":" not in source,
        "shared driver runtime source is invalid",
    )
    _need(
        request["env"] == {"PYTHONPATH": source, "PYTHONDONTWRITEBYTECODE": "1"}
        and Path(outer[0]).is_absolute()
        and Path(outer[0]).name.startswith("python")
        and raw["attempt_id"] == request["attempt_id"]
        and raw["attempt_ordinal"] == request["attempt_ordinal"]
        and raw["execution_id"] == request["execution_id"]
        and raw["cwd"] == request["cwd"]
        and raw["timeout_seconds"] == request["timeout_seconds"]
        and raw["stdout_path"] == "raw-stdout.log"
        and raw["stderr_path"] == "raw-stderr.log"
        and request["stdout_path"] == "stdout.log"
        and request["stderr_path"] == "stderr.log",
        "shared raw/outer process identities or logs differ",
    )
    raw_argv = raw["argv"]
    mounts = [
        raw_argv[index + 1]
        for index, value in enumerate(raw_argv[:-2])
        if value == "--ro-bind" and raw_argv[index + 2] == "/runtime"
    ]
    _need(len(mounts) == 1, "shared experiment lacks one raw runtime")
    prefix = mounts[0]
    base = PurePosixPath(request["cwd"]).parent
    for field, suffix, relative, declaration in (
        (
            "parser_request",
            "--parser",
            "measurement-parser",
            payload.get("measurement"),
        ),
        (
            "goal_parser_request",
            "--goal-parser",
            "goal-parser",
            binding["acceptance"] if goal else None,
        ),
    ):
        if declaration is None:
            continue
        parser = plan[field]
        argv = parser["argv"]
        root = base / relative
        timeout = min(30.0, float(request["timeout_seconds"]))
        _need(
            parser["attempt_id"] == request["attempt_id"] + suffix
            and parser["attempt_ordinal"] == 1
            and parser["execution_id"] == request["execution_id"]
            and parser["cwd"] == str(root / "logs")
            and parser["env"] == {}
            and parser["timeout_seconds"] == timeout
            and parser["stdout_path"] == "stdout.log"
            and parser["stderr_path"] == "stderr.log",
            "shared parser phase owner, path or limits differ",
        )
        interpreter = argv[-4]
        _need(
            interpreter.startswith("/runtime/bin/python"),
            "shared parser interpreter is not admitted",
        )
        expected = [
            raw_argv[0],
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/runtime",
            "--ro-bind",
            prefix,
            "/runtime",
        ]
        for path in ("/lib", "/lib64", "/usr/lib", "/usr/bin", "/etc/ld.so.cache"):
            if Path(path).exists():
                expected.extend(("--ro-bind", path, path))
        expected.extend(
            (
                "--ro-bind",
                "/usr/bin",
                "/bin",
                "--ro-bind",
                str(root / "input"),
                "/input",
                "--bind",
                str(root / "parsed"),
                "/parsed",
                "--dir",
                "/raw",
            )
        )
        directories = {"/raw"}
        for relative in declaration["raw_artifacts"]:
            destination = PurePosixPath("/raw") / relative
            for parent in reversed(destination.parents):
                if str(parent) not in {"/", *directories}:
                    expected.extend(("--dir", str(parent)))
                    directories.add(str(parent))
            raw_path = (
                PurePosixPath(request["cwd"]) / ("raw-" + relative)
                if relative in {"stdout.log", "stderr.log"}
                else base / "output" / relative
            )
            expected.extend(("--ro-bind", str(raw_path), str(destination)))
        expected.extend(
            (
                "--ro-bind",
                str(
                    PurePosixPath(source) / "hypoloom" / "measurement_driver.py"
                ),
                "/measurement-driver.py",
            )
        )
        environment = {
            "HOME": "/nonexistent",
            "PATH": "/runtime/bin:/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "TMPDIR": "/tmp",
            "CUDA_VISIBLE_DEVICES": "",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "HYPOLOOM_MEASUREMENT_RAW_ROOT": "/raw",
            "HYPOLOOM_MEASUREMENT_RESULT_PATH": "/parsed/result.json",
        }
        for name, value in sorted(environment.items()):
            expected.extend(
                ("--setenv", name, value) if value else ("--unsetenv", name)
            )
        command = list(declaration["parser_argv"])
        if command[0] == "python":
            command[0] = interpreter
        command = [
            "/input/" + value if value in declaration["parser_files"] else value
            for value in command
        ]
        limits = {
            "argv": command,
            "timeout_seconds": timeout,
            "max_output_bytes": 1024 * 1024,
            "max_memory_bytes": 2 * 1024**3,
        }
        expected.extend(
            (
                "--chdir",
                "/parsed",
                "--",
                interpreter,
                "/measurement-driver.py",
                "--parser-limits",
                json.dumps(limits, sort_keys=True, separators=(",", ":")),
            )
        )
        _need(
            argv == expected,
            "shared parser executable source, RO mounts, output or CPU limits differ",
        )
    return raw
