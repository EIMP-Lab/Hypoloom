"""Independent evidence checks for the optional prerequisite Process phase."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath


def _need(condition, message):
    if not condition:
        raise ValueError(message)


def audit_request(request, declaration):
    """Remove only a verified prerequisite before existing raw/parser audits."""
    outer = request["argv"]
    _need(len(outer) == 4 and outer[1:3] == ["-m", "hypoloom.measurement_driver"], "missing execution checks driver")
    plan = json.loads(outer[3])
    if declaration is None:
        _need("execution_checks" not in plan, "unadmitted execution checks")
        return request
    checks = plan.pop("execution_checks")
    raw = plan["raw_request"]
    _need(set(checks) == {"request", "requirements", "output_dir"}, "invalid execution checks plan")
    root = PurePosixPath(request["cwd"]).parent / "execution-checks"
    _need(checks["requirements"] == sorted(declaration["requirements"])
        and checks["output_dir"] == str(root / "output"), "execution check requirements/output differ")
    check = checks["request"]
    raw_argv = raw["argv"]
    split = raw_argv.index("--")
    expected_argv = list(raw_argv[:split])
    matches = [i for i in range(len(expected_argv) - 2)
        if expected_argv[i:i + 3] == ["--bind", str(PurePosixPath(request["cwd"]).parent / "output"), "/output"]]
    _need(len(matches) == 1, "ambiguous workload output")
    expected_argv[matches[0] + 1] = str(root / "output")
    expected_argv.extend(("--ro-bind", str(root / "source"), "/hypoloom-checks", "--"))
    command = check["argv"][check["argv"].index("--") + 1:]
    if "--dev-bind" in expected_argv:
        expected_argv.extend(raw_argv[split + 1:split + 6])
        command = command[5:]
    _need(command and command[0].startswith("/runtime/bin/python") and "/" not in command[0][13:], "checker runtime differs")
    expected_argv.extend((command[0], "/hypoloom-checks/" + declaration["argv"][1], *declaration["argv"][2:]))
    expected = {**raw, "attempt_id": raw["attempt_id"] + "--checks", "argv": expected_argv,
        "cwd": str(root / "logs"), "timeout_seconds": min(raw["timeout_seconds"], declaration["timeout_seconds"]),
        "stdout_path": "stdout.log", "stderr_path": "stderr.log"}
    _need(check == expected, "checker identity, sandbox, command or timeout differs from admitted task")
    _need(raw["attempt_id"] == request["attempt_id"] and raw["attempt_ordinal"] == request["attempt_ordinal"]
        and raw["execution_id"] == request["execution_id"] and raw["cwd"] == request["cwd"]
        and raw["timeout_seconds"] == request["timeout_seconds"] and raw["env"] == {}
        and raw["stdout_path"] == "raw-stdout.log" and raw["stderr_path"] == "raw-stderr.log"
        and request["stdout_path"] == "stdout.log" and request["stderr_path"] == "stderr.log", "driver ownership differs")
    runtime = request["env"].get("PYTHONPATH")
    _need(isinstance(runtime, str) and PurePosixPath(runtime).is_absolute() and ":" not in runtime
        and ".." not in PurePosixPath(runtime).parts and str(PurePosixPath(runtime)) == runtime
        and request["env"] == {"PYTHONPATH": runtime, "PYTHONDONTWRITEBYTECODE": "1"}
        and Path(outer[0]).is_absolute() and Path(outer[0]).name.startswith("python"), "invalid driver runtime")
    return {**request, "argv": [*outer[:3], json.dumps(plan, sort_keys=True, separators=(",", ":"))]}


def audit_publication(root, declaration, workload_argv, observation):
    source = root / "raw" / "execution-checks" / "source"
    expected = {name: content.encode("utf-8") for name, content in declaration["files"].items()}
    expected["context.json"] = json.dumps({"argv": list(workload_argv), "input_dir": "/input",
        "output_dir": "/output", "requirements": declaration["requirements"]}, sort_keys=True, ensure_ascii=False).encode()
    for name, data in expected.items():
        path = source / name
        _need(path.is_file() and not path.is_symlink() and path.read_bytes() == data, "published checker source/context differs from admitted task")
    if observation.get("returncode") in {0, 70, 71, 72, 73} and not observation.get("synthetic_before_spawn") and not observation.get("integrity_error"):
        from .measurement_oracle import _read_result
        path = source.parent / "result.json"
        _need(path.stat().st_size <= 64 * 1024, "oversized prerequisite result")
        result = _read_result(path)
        _need(set(result) == {"checks"} and isinstance(result["checks"], dict)
            and set(result["checks"]) == set(declaration["requirements"])
            and all(value is True for value in result["checks"].values()), "workload ran without all required checks passing")
