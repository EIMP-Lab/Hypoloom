"""Producer/reader disagreements reproduced by the October 16 qualification."""
from dataclasses import replace
import json
import sys

import pytest

from hypoloom.measurement_driver import sequence_request
from hypoloom.measurement_execution import plan_parser
from hypoloom.oracle import _measurement_process_requests, _raw_reported_gpu_oom
from hypoloom.runner import ProcessRequest
from hypoloom.shared_gpu_host import SharedGpuHost
from tests.runtime.test_measurement_contract import declaration


def test_truncated_workload_oom_excerpt_remains_valid_raw_evidence(tmp_path):
    line = "torch.OutOfMemoryError: CUDA out of memory. "
    line += "x" * (511 - len(line)) + " is unavailable"
    log = tmp_path / "stderr.log"
    log.write_text(line + "\n")
    observation = {"terminal_reason": "FAILED", "returncode": 70}
    SharedGpuHost.observe_oom(observation, log)
    excerpt = observation["resource_failure_excerpt"]
    assert len(excerpt) == 512 and excerpt.endswith(" ")
    assert excerpt == line[:512]
    assert _raw_reported_gpu_oom(observation, {"terminal_reason": "FAILED"})


@pytest.mark.parametrize("excerpt", [None, 42, "", " " * 512,
    " torch.OutOfMemoryError: CUDA out of memory",
    "Advice: torch.OutOfMemoryError: CUDA out of memory",
    "torch.OutOfMemoryError: CUDA out of memory\n",
    "torch.OutOfMemoryError: CUDA out of memory\t",
    "torch.OutOfMemoryError: CUDA out of memory\x7f",
    "torch.OutOfMemoryError: CUDA out of memory" + "x" * 1024])
def test_raw_oom_excerpt_still_requires_a_bounded_exception_line(excerpt):
    observation = {"terminal_reason": "FAILED", "returncode": 70,
                   "resource_failure": "gpu_out_of_memory", "resource_failure_excerpt": excerpt}
    with pytest.raises(ValueError, match="exception excerpt"):
        _raw_reported_gpu_oom(observation, {"terminal_reason": "FAILED"})


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("adapter_version", [82, 83, 84])
@pytest.mark.parametrize("argument,expected", [
    ("runs/data.csv", "/raw/runs/data.csv"),
    ("runs", "/raw/runs"),
    ("--raw=runs/data.csv", "--raw=/raw/runs/data.csv"),
    ("--source=parse_metrics.py", "--source=/input/parse_metrics.py"),
])
def test_parser_audit_matches_declared_input_projection(tmp_path, shared, adapter_version, argument, expected):
    measure = replace(declaration(), raw_artifacts=("runs/data.csv",),
                      parser_argv=("python", "parse_metrics.py", argument))
    work = tmp_path / "attempt"
    raw = ProcessRequest(attempt_id="process-1", attempt_ordinal=1, execution_id="exec-1",
        argv=("/usr/bin/bwrap", "--ro-bind", sys.prefix, "/runtime"), cwd=str(work / "logs"),
        env={}, timeout_seconds=60, stdout_path="raw-stdout.log", stderr_path="raw-stderr.log")
    parser = plan_parser(measure, {"parse_metrics.py": b"print(1)"},
        raw_dir=work / "output", work_dir=work / "measurement-parser",
        attempt_id="process-1--parser", execution_id="exec-1", timeout_seconds=30.0).process_request
    request = sequence_request(raw, parser, attempt_id=raw.attempt_id, attempt_ordinal=1,
        execution_id=raw.execution_id, cwd=raw.cwd, timeout_seconds=60).to_dict()
    plan = json.loads(request["argv"][3])
    argv = plan["parser_request"]["argv"]
    limits = json.loads(argv[-1])
    assert limits["argv"][-1] == expected
    if adapter_version < 83:
        limits["argv"][-1] = argument
        argv[-1] = json.dumps(limits, sort_keys=True, separators=(",", ":"))
        request["argv"][3] = json.dumps(plan)
    payload = {"measurement": measure.to_dict()}
    if shared:
        payload["goal_evaluation"] = {"scope": "LOCAL"}
    context = {"payload": payload, "request": {"required_adapter_version": f"claude-code-bwrap/{adapter_version}"}}
    issues = []
    _measurement_process_requests(request, context, raw.attempt_id, issues)
    assert issues == []
    wrong_version = "claude-code-bwrap/83" if adapter_version < 83 else "claude-code-bwrap/82"
    issues = []
    _measurement_process_requests(request, {**context, "request": {"required_adapter_version": wrong_version}},
                                  raw.attempt_id, issues)
    assert [issue.code for issue in issues] == ["measurement_process_binding_mismatch"]
    limits["argv"][-1] = "/raw/undeclared.csv"
    argv[-1] = json.dumps(limits, sort_keys=True, separators=(",", ":"))
    request["argv"][3] = json.dumps(plan)
    issues = []
    _measurement_process_requests(request, context, raw.attempt_id, issues)
    assert [issue.code for issue in issues] == ["measurement_process_binding_mismatch"]
