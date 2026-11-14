from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.verification_probe import execution_files, RUNNER_PATH, RESULT_PATH, decode_result, validate_result
from hypoloom.verification_contract import context, validate_review, review_schema


def declared(key="values", kind="assertion", required=True):
    return {"id": key, "kind": kind, "required": required, "purpose": "Exercise captured values", "basis": "Independent hand calculation"}


def run_probe(tmp_path, source, checks=None):
    checks = checks or [declared()]
    probe = {"checks": checks, "argv": ["python", "verification/probe.py"]}
    files = execution_files(probe, attempt_id="probe-1", report_ref="inspection-1")
    files["verification/probe.py"] = source.encode()
    for name, data in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    result = subprocess.run([sys.executable, str(tmp_path / RUNNER_PATH)], cwd=tmp_path, capture_output=True, timeout=10)
    document = decode_result((tmp_path / RESULT_PATH).read_bytes())
    return result, document, validate_result(document, checks, attempt_id="probe-1", report_ref="inspection-1")


@pytest.mark.parametrize("body,status", [
    ("c.equal(2, 2)", "PASS"),
    ("c.equal(1, 2); c.equal(2, 2)", "FAIL"),
    ("print('PASS')", "ERROR"),
    ("raise TypeError('bad probe')", "ERROR"),
    ("assert False, 'wrong result'", "FAIL"),
    ("c.true(None is not None, 'missing gradient')", "FAIL"),
])
def test_runner_records_actual_assertions_and_preserves_failure(tmp_path, body, status):
    process, document, assessed = run_probe(tmp_path, "from hypoloom_probe import check\nwith check('values') as c:\n " + body + "\n")
    assert document["checks"][0]["status"] == status
    assert assessed["passed"] is (status == "PASS")
    assert (process.returncode == 0) is assessed["passed"]


@pytest.mark.parametrize("source", ["print('PASS')", "raise SystemExit(0)",
    "from hypoloom_probe import check\nwith check('other') as c: c.true(True)",
    "from hypoloom_probe import check\nwith check('values') as c: c.true(True)\nwith check('values') as c: c.true(True)"])
def test_omitted_early_exit_and_duplicate_checks_do_not_pass(tmp_path, source):
    process, _, assessed = run_probe(tmp_path, source)
    assert process.returncode and not assessed["passed"]


def test_probe_distinguishes_correct_reduction_and_mutant(tmp_path):
    for denominator, expected in ((2, True), (3, False)):
        folder = tmp_path / str(denominator)
        process, _, assessed = run_probe(folder, f"from hypoloom_probe import check\n"
            f"def candidate(xs): return sum(xs)/{denominator}\n"
            "with check('values') as c: c.close(candidate([2,4]),3.0)\n")
        assert assessed["passed"] is expected
        assert (process.returncode == 0) is expected


def test_measurement_executes_warmups_and_repetitions_and_is_not_an_assertion(tmp_path):
    _, document, assessed = run_probe(tmp_path,
        "from hypoloom_probe import check,measure\ncount=[0]\n"
        "def work(): count[0]+=1\n"
        "measure('timing',work,workload='one integer increment',warmup=2,repeats=7)\n"
        "with check('values') as c: c.equal(count[0],9)\n",
        [declared(), declared("timing", "measurement")])
    assert assessed["passed"]
    assert document["checks"][1]["status"] == "MEASURED"
    assert document["checks"][1]["assertions"] == 0
    assert document["checks"][1]["measurement"]["repeats"] == 7


def test_results_cannot_claim_pass_for_empty_assertion_or_wrong_binding(tmp_path):
    _, document, _ = run_probe(tmp_path, "from hypoloom_probe import check\nwith check('values') as c: c.equal(1,1)\n")
    for field, value in (("attempt_id", "old-probe"), ("report_ref", "old-report")):
        altered = {**document, field: value}
        with pytest.raises(ValueError, match="different"):
            validate_result(altered, [declared()], attempt_id="probe-1", report_ref="inspection-1")
    document["checks"][0]["assertions"] = 0
    with pytest.raises(ValueError, match="executed assertions"):
        validate_result(document, [declared()], attempt_id="probe-1", report_ref="inspection-1")
    for data in (b'{"checks":[],"checks":[]}', b'{"a":NaN}', b'{' * 65537):
        with pytest.raises(ValueError): decode_result(data)


def test_coverage_and_structured_evidence_are_required_even_with_successful_process(tmp_path):
    _, document, _ = run_probe(tmp_path, "from hypoloom_probe import check\nwith check('values') as c: c.equal(1,1)\n")
    request = default_request(AgentCommand.VERIFY_IMPLEMENTATION)
    ctx = {**context(request), "phase": "review", "probe_report_ref": "inspection-1",
        "probe_plan": {"checks": [declared()]}, "probe_evidence": {"usable": True, "attempt_id": "probe-1", "observation": {"check_results": document}}}
    request = replace(request, input_artifacts=(EmbeddedInput.from_bytes(request.input_artifacts[0].ref, "application/json", json.dumps(ctx).encode()),))
    report = FakeAgent().invoke(request).typed_candidate
    validate_review(request, report)
    for change in ("coverage", "blocking", "failed_check"):
        altered = deepcopy(report)
        if change == "coverage": altered["coverage"]["covered"] = []
        elif change == "blocking": altered["coverage"]["uncovered"] = [{"condition": "target CUDA", "reason": "not executed", "blocking": True}]
        else:
            ctx["probe_evidence"]["observation"]["check_results"]["checks"][0].update(status="FAIL", failures=1)
            request = replace(request, input_artifacts=(EmbeddedInput.from_bytes(request.input_artifacts[0].ref, "application/json", json.dumps(ctx).encode()),))
        with pytest.raises(ValueError): validate_review(request, altered)


def test_native_correction_uses_real_profile_and_same_semantic_validator(tmp_path):
    from verifier_qualification import verifier_runtime
    from hypoloom.full_cc_runtime import structured_output_validation_cap
    from hypoloom.claude_agent_host import _native_candidate_check, _output_schema, _prompt
    from hypoloom.native_output import NativeOutputValidator, call_validator
    runtime, budget = verifier_runtime("mimo-v2.6-pro")
    request = default_request(AgentCommand.VERIFY_IMPLEMENTATION)
    config = EmbeddedInput.from_bytes("fixture--expert-config", "application/json", json.dumps({"node_runtime": runtime}).encode())
    request = replace(request, input_artifacts=(*request.input_artifacts, config),
        immutable_context_refs=(*request.immutable_context_refs, config.ref), required_model_identity=runtime["model"], budget=budget)
    assert structured_output_validation_cap(request) == 3
    prompt = json.loads(_prompt(request, tool_workspace=True))
    assert "supporting_refs and evidence_refs" in prompt["instruction"]
    response = FakeAgent().invoke(request).to_dict()
    schema = _output_schema(request, tool_workspace=True)
    response = {k: v for k, v in response.items() if k in schema["properties"]}
    response["declared_effects"] = [{"effect": item["effect"]} for item in response["declared_effects"]]
    response["typed_candidate"]["supporting_refs"] = ["R1"]
    candidate = deepcopy(response)
    candidate["typed_candidate"]["probe"]["argv"][0] = "python3"
    server = NativeOutputValidator(tmp_path / "output.sock", output_schema=_output_schema(request, tool_workspace=True),
        session_id="test-session", max_failed_validations=3, max_request_bytes=1024*1024,
        deadline_at=time.monotonic()+5, candidate_check=_native_candidate_check(request, None))
    def call(value):
        return call_validator(str(server.socket_path), session_id="test-session", last_assistant_message=json.dumps(value),
            max_request_bytes=1024*1024, timeout_seconds=2)
    try:
        assert call(candidate)["decision"] == "block"
        assert call(response) == {}
        assert server.snapshot()["accepted"] and server.snapshot()["rejected_count"] == 1
        assert call(candidate)["decision"] == "block"
        assert call(candidate) == {}
        assert not server.snapshot()["accepted"] and server.snapshot()["rejected_count"] == 3
    finally: server.close()


def test_legacy_verifier_contract_remains_readable():
    request = replace(default_request(AgentCommand.VERIFY_IMPLEMENTATION), schema_version=34)
    report = FakeAgent().invoke(request).typed_candidate
    assert "coverage" not in report and "checks" not in report["probe"]
    validate_review(request, report)
    assert "coverage" not in review_schema(request)["properties"]


def test_native_verifier_uses_compact_system_prompt_and_numbered_source(tmp_path):
    from hypoloom.claude_agent_host import _prompt, _output_schema
    from hypoloom.full_cc_runtime import full_cc_argv
    from hypoloom.verification_contract import SYSTEM_INSTRUCTIONS
    from test_full_cc_runtime import _workspace
    request = default_request(AgentCommand.VERIFY_IMPLEMENTATION)
    source = EmbeddedInput.from_bytes("fixture--implementation-source", "application/json", b'{"candidate.py":"def f():\\n    return 2\\n"}')
    request = replace(request, input_artifacts=(*request.input_artifacts, source), immutable_context_refs=(*request.immutable_context_refs, source.ref))
    before = source.content_bytes()
    prompt = json.loads(_prompt(request, tool_workspace=True))
    rendered = prompt["rendered_inputs"][-1]
    assert "1: def f():" in rendered["content_utf8"] and "2:     return 2" in rendered["content_utf8"]
    assert source.content_bytes() == before
    argv = full_cc_argv(bwrap=Path('/usr/bin/bwrap'), executable=tmp_path/'claude', request=request,
        model='mimo-v2.6-pro', workspace=_workspace(tmp_path), provider_socket=tmp_path/'provider.sock',
        host_socket=None, output_socket=tmp_path/'output.sock', output_schema=_output_schema(request, tool_workspace=True),
        output_schema_path=tmp_path/'schema.json', extra_paths=())
    assert argv[argv.index('--system-prompt')+1] == SYSTEM_INSTRUCTIONS
    assert argv[argv.index('--tools')+1] == 'Read,Glob,Grep'
    assert 'Stop' in json.loads(argv[argv.index('--settings')+1])['hooks']
