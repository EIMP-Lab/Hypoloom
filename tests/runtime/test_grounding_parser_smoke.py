"""Execute a generated common parser before accepting its native completion."""
import json
from contextlib import closing
from pathlib import Path
import time

import pytest

from hypoloom.shared_acceptance import SharedAcceptance, normalize_shared_observation
from tests.runtime.test_shared_acceptance import acceptance_dict


def test_mismatched_shared_names_identify_the_actual_missing_and_extra_fields():
    acceptance = SharedAcceptance.from_dict(acceptance_dict())
    with pytest.raises(ValueError) as caught:
        normalize_shared_observation({"measurements": {"alpha_mean": 0.4, "beta": 0.6},
                                      "checks": {"causal": True}}, acceptance)
    message = str(caught.value)
    assert 'missing measurements=["alpha"]' in message
    assert 'unexpected measurements=["alpha_mean"]' in message


def _case(tmp_path: Path, *, key="alpha", value=0.1):
    root = tmp_path / "acceptance"
    (root / "smoke").mkdir(parents=True)
    (root / "smoke/raw.json").write_text(json.dumps({"value": value}))
    source = '''import json, os
from pathlib import Path
value = json.loads((Path(os.environ['HYPOLOOM_MEASUREMENT_RAW_ROOT']) / 'raw.json').read_text())['value']
Path(os.environ['HYPOLOOM_MEASUREMENT_RESULT_PATH']).write_text(json.dumps({
    'measurements': {KEY: value, 'beta': value}, 'checks': {'causal': True}}))
'''.replace("KEY", repr(key))
    (root / "evaluate.py").write_text(source)
    declaration = {**acceptance_dict(), "parser_argv": ["python", "/input/evaluate.py"],
                   "parser_files": ["evaluate.py"], "raw_artifacts": ["raw.json"]}
    output = {"typed_candidate": {"acceptance": declaration, "clarification": None}}
    return root, output


@pytest.mark.parametrize("argument", ["records/value.json", "records", "records/", "/raw/records/value.json"])
@pytest.mark.parametrize("joined", [False, True])
def test_relative_declared_raw_arguments_match_actual_parser_sandbox(tmp_path, argument, joined):
    from dataclasses import replace
    from hypoloom.measurement_execution import prepare_parser, run_parser
    from hypoloom.native_output import parser_smoke
    from test_measurement_contract import declaration

    root = tmp_path / "authoring"
    (root / "smoke/records").mkdir(parents=True)
    (root / "smoke/records/value.json").write_text('{"value":-1.0}')
    # This unrelated file must remain unavailable even when argv names its parent.
    (root / "smoke/records/undeclared.json").write_text('{"value":99}')
    source = b'''import argparse, json, os
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--data')
path = Path(p.parse_args().data)
assert Path.cwd() == Path('/parsed')
assert not Path('/raw/records/undeclared.json').exists()
if path.is_dir(): path = path / 'value.json'
assert os.statvfs(path).f_flag & os.ST_RDONLY
value = json.loads(path.read_text())['value']
Path(os.environ['HYPOLOOM_MEASUREMENT_RESULT_PATH']).write_text(json.dumps({
    'metric': 'median_latency_delta', 'value': value, 'baseline': 0}))
'''
    (root / "evaluate.py").write_bytes(source)
    args = ("--data=" + argument,) if joined else ("--data", argument)
    contract = replace(declaration(), parser_argv=("python", "evaluate.py", *args),
        parser_files=("evaluate.py",), raw_artifacts=("records/value.json",))
    prepared = prepare_parser(contract, {"evaluate.py": source}, raw_dir=root / "smoke",
        work_dir=tmp_path / "production", attempt_id="parser", execution_id="parser", timeout_seconds=5)
    actual = run_parser(prepared)
    assert actual.error is None
    assert actual.raw_result == {"metric": "median_latency_delta", "value": -1, "baseline": 0}
    smoke = parser_smoke(json.dumps({"typed_candidate": {"measurement": contract.to_dict()}}),
        source_root=root, test_root=tmp_path / "smoke", declaration_key="measurement",
        timeout_seconds=5, max_bytes=65536)
    assert smoke == {"observation": actual.raw_result, "error": None}


def test_parser_argument_projection_preserves_outputs_and_source_precedence():
    from hypoloom.measurement_execution import _parser_input_argument
    files = {"evaluate.py": b"source", "reference.json": b"reference"}
    raw = ("records/value.json", "reference.json")
    assert _parser_input_argument("reference.json", files, raw) == "/input/reference.json"
    for value in ("result.json", "--output=result.json", ".", "..", "/tmp/value.json", "record", "--flag"):
        assert _parser_input_argument(value, files, raw) == value


def test_native_stop_rejects_actual_parser_output_then_accepts_its_repair(tmp_path):
    from hypoloom.native_output import NativeOutputValidator, call_validator
    from hypoloom.runtime_diagnostics import RuntimeDiagnostics
    root, output = _case(tmp_path, key="alpha_mean")
    with closing(NativeOutputValidator(tmp_path / "output.sock", output_schema={"type": "object"},
            session_id="session", max_failed_validations=3, max_request_bytes=65536,
            deadline_at=time.monotonic() + 20, acceptance_smoke=True, parser_source_root=root,
            diagnostics=RuntimeDiagnostics(tmp_path / "diagnostics"))) as validator:
        def check():
            text = json.dumps(output)
            return call_validator(str(validator.socket_path), session_id="session", last_assistant_message=text,
                timeout_seconds=5, max_request_bytes=65536)
        rejected = check()
        assert rejected["decision"] == "block"
        assert 'unexpected measurements=["alpha_mean"]' in rejected["reason"]
        path = root / "evaluate.py"
        path.write_text(path.read_text().replace("'alpha_mean'", "'alpha'"))
        assert check() == {}
        assert validator.snapshot()["status"] == "recovered"
        assert validator.snapshot()["accepted"] is True
        # This smoke is below the target; interface validity is not goal success.
        trace = json.loads((tmp_path / "diagnostics/native-output/000002.json").read_text())
        assert trace["parser_smoke"]["observation"]["measurements"]["alpha"] == 0.1
        assert not list(tmp_path.glob("parser-smoke-*"))


@pytest.mark.parametrize("damage", ["missing-fixture", "no-result", "timeout", "oversize", "symlink", "extra-result"])
def test_smoke_requires_actual_bounded_execution_not_a_stale_result(tmp_path, damage):
    from hypoloom.native_output import parser_smoke
    root, output = _case(tmp_path)
    test_root = tmp_path / "test"
    (test_root / "parsed").mkdir(parents=True)
    (test_root / "parsed/result.json").write_text('{"measurements":{},"checks":{}}')
    if damage == "missing-fixture": (root / "smoke/raw.json").unlink()
    elif damage == "no-result": (root / "evaluate.py").write_text("pass\n")
    elif damage == "timeout": (root / "evaluate.py").write_text("import time; time.sleep(10)\n")
    elif damage == "oversize": (root / "smoke/raw.json").write_bytes(b"x" * 65537)
    elif damage == "symlink":
        (root / "smoke/raw.json").unlink()
        (root / "smoke/raw.json").symlink_to(root / "evaluate.py")
    else:
        source = root / "evaluate.py"
        source.write_text(source.read_text() + "\nPath('extra.txt').write_text('undeclared')\n")
    result = parser_smoke(json.dumps(output), source_root=root, test_root=test_root,
                         timeout_seconds=0.2 if damage == "timeout" else 5, max_bytes=65536)
    assert result["error"]
    assert result.get("observation") is None


def test_grounding_clarification_does_not_require_a_parser(tmp_path):
    from hypoloom.native_output import parser_smoke
    assert parser_smoke(json.dumps({"typed_candidate": {"acceptance": None}}),
        source_root=tmp_path, test_root=tmp_path / "test", timeout_seconds=1,
        max_bytes=65536) is None


def test_local_parser_rejects_common_envelope_and_repairs_before_preregistration(tmp_path):
    from hypoloom.native_output import NativeOutputValidator, call_validator
    from test_measurement_contract import declaration
    root, output = _case(tmp_path)
    output["typed_candidate"] = {"measurement": {**declaration().to_dict(),
        "parser_argv": ["python", "/input/evaluate.py"],
        "parser_files": ["evaluate.py"], "raw_artifacts": ["raw.json"]}}
    with closing(NativeOutputValidator(tmp_path / "output.sock", output_schema={"type": "object"},
            session_id="session", max_failed_validations=3, max_request_bytes=65536,
            deadline_at=time.monotonic() + 20, measurement_smoke=True, parser_source_root=root)) as validator:
        def check():
            text = json.dumps(output)
            return call_validator(str(validator.socket_path), session_id="session", last_assistant_message=text,
                timeout_seconds=5, max_request_bytes=65536)
        rejected = check()
        assert rejected["decision"] == "block"
        assert "frozen metric" in rejected["reason"] and "/workspace" in rejected["reason"]
        path = root / "evaluate.py"
        path.write_text(path.read_text().replace(
            "'measurements': {'alpha': value, 'beta': value}, 'checks': {'causal': True}",
            "'metric': 'median_latency_delta', 'value': value, 'baseline': 0.1"))
        assert check() == {}
        assert validator.snapshot()["status"] == "recovered"
        # An unchanged baseline is valid parser output, not scientific success.
        assert validator.snapshot()["accepted"] is True


def test_goal_only_design_does_not_require_a_local_parser(tmp_path):
    from hypoloom.native_output import NativeOutputValidator, parser_smoke
    text = json.dumps({"typed_candidate": {"measurement": None}})
    smoke = parser_smoke(text, source_root=tmp_path, test_root=tmp_path / "test",
        declaration_key="measurement", timeout_seconds=1, max_bytes=65536)
    assert smoke is None
    with closing(NativeOutputValidator(tmp_path / "output.sock", output_schema={"type": "object"},
            session_id="session", max_failed_validations=3, max_request_bytes=65536,
            deadline_at=time.monotonic() + 5, measurement_smoke=True, parser_source_root=tmp_path)) as validator:
        assert validator._validate({"session_id": "session", "last_assistant_message": text}) == {}


@pytest.mark.parametrize("dependency", ["profile-data", "authoring-tree", "agent-module"])
def test_smoke_matches_production_when_agent_only_inputs_are_available(tmp_path, monkeypatch, dependency):
    from hypoloom.measurement_execution import prepare_parser, run_parser
    from hypoloom.native_output import parser_smoke
    root, output = _case(tmp_path)
    data = tmp_path / "profile-data"
    data.mkdir()
    (data / "validation.json").write_text('{"labels":[0,1]}')
    (data / "agent_only_dependency.py").write_text("AVAILABLE = True\n")
    monkeypatch.setenv("HYPOLOOM_READ_ROOTS", json.dumps([str(data)]))
    monkeypatch.setenv("PYTHONPATH", str(data))
    condition = {
        "profile-data": "any((Path(p) / 'validation.json').is_file() for p in json.loads(os.environ.get('HYPOLOOM_READ_ROOTS', '[]')))",
        "authoring-tree": f"Path({str(root / 'smoke/raw.json')!r}).is_file()",
        "agent-module": "__import__('importlib.util', fromlist=['find_spec']).find_spec('agent_only_dependency') is not None",
    }[dependency]
    source = root / "evaluate.py"
    source.write_text(source.read_text().replace("'causal': True", "'causal': " + condition))
    output["typed_candidate"]["acceptance"]["parser_argv"] = ["python", "/input/evaluate.py"]
    declaration = SharedAcceptance.from_dict(output["typed_candidate"]["acceptance"])
    prepared = prepare_parser(declaration, {"evaluate.py": source.read_bytes()},
        raw_dir=root / "smoke", work_dir=tmp_path / "production", attempt_id="production",
        execution_id="production", timeout_seconds=5, max_output_bytes=65536)
    production = run_parser(prepared)
    assert production.error is None
    assert production.raw_result["checks"]["causal"] is False
    smoke = parser_smoke(json.dumps(output), source_root=root, test_root=tmp_path / "smoke",
        timeout_seconds=5, max_bytes=65536)
    assert smoke["error"] is None
    assert smoke["observation"] == production.raw_result


@pytest.mark.parametrize("declaration_key", ["acceptance", "measurement"])
def test_host_preflight_returns_environment_error_and_accepts_declared_reference_repair(tmp_path, monkeypatch, declaration_key):
    from hypoloom.native_output import NativeOutputValidator, call_validator
    from test_measurement_contract import declaration
    root, output = _case(tmp_path)
    reference = tmp_path / "profile-data"
    reference.mkdir()
    (reference / "validation.json").write_text('{"value":0.1}')
    monkeypatch.setenv("HYPOLOOM_READ_ROOTS", json.dumps([str(reference)]))
    contract = output["typed_candidate"]["acceptance"]
    if declaration_key == "measurement":
        contract = {**declaration().to_dict(), **{key: contract[key]
            for key in ("parser_files", "parser_argv", "raw_artifacts")}}
        output["typed_candidate"] = {"measurement": contract}
    path = root / "evaluate.py"
    source = path.read_text()
    if declaration_key == "measurement":
        source = source.replace("'measurements': {'alpha': value, 'beta': value}, 'checks': {'causal': True}",
            "'metric': 'median_latency_delta', 'value': value, 'baseline': 0.1")
    dependency = "reference = Path(json.loads(os.environ['HYPOLOOM_READ_ROOTS'])[0]) / 'validation.json'"
    source = source.replace("value = ", dependency + "\nassert json.loads(reference.read_text())['value'] == 0.1\nvalue = ", 1)
    path.write_text(source)
    with closing(NativeOutputValidator(tmp_path / "output.sock", output_schema={"type": "object"},
            session_id="session", max_failed_validations=3, max_request_bytes=65536,
            deadline_at=time.monotonic() + 20, acceptance_smoke=declaration_key == "acceptance",
            measurement_smoke=declaration_key == "measurement", parser_source_root=root,
            parser_smoke_max_bytes=65536)) as validator:
        def check():
            return call_validator(str(validator.socket_path), session_id="session",
                last_assistant_message=json.dumps(output), timeout_seconds=5, max_request_bytes=65536)
        rejected = check()
        assert rejected["decision"] == "block"
        assert "HYPOLOOM_READ_ROOTS" in rejected["reason"]
        # Preserve the reference check by declaring its actual input, not removing it.
        (root / "validation.json").write_bytes((reference / "validation.json").read_bytes())
        contract["parser_files"].append("validation.json")
        path.write_text(source.replace(dependency, "reference = Path('/input/validation.json')"))
        assert check() == {}
        assert validator.snapshot()["status"] == "recovered"
        assert not list(tmp_path.glob("parser-smoke-*"))


@pytest.mark.parametrize("stop", ["close", "agent-cancel", "agent-exit", "agent-timeout"])
def test_close_cancels_running_host_preflight_and_its_process_group(tmp_path, monkeypatch, stop):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from hypoloom.native_output import NativeOutputValidator, call_validator
    from hypoloom.runner import LocalRunner, process_group_exists
    root, output = _case(tmp_path)
    (root / "evaluate.py").write_text("""import os, time
from pathlib import Path
if os.fork() == 0:
    time.sleep(60)
else:
    Path('/parsed/result.json').write_text('READY')
    time.sleep(60)
""")
    handles = []
    started = threading.Event()
    original_start = LocalRunner.start
    def start(runner, request, **kwargs):
        handle = original_start(runner, request, **kwargs)
        handles.append(handle)
        started.set()
        return handle
    monkeypatch.setattr(LocalRunner, "start", start)
    with closing(NativeOutputValidator(tmp_path / "output.sock", output_schema={"type": "object"},
            session_id="session", max_failed_validations=3, max_request_bytes=65536,
            deadline_at=time.monotonic() + 30, acceptance_smoke=True, parser_source_root=root)) as validator:
        def check():
            with pytest.raises((OSError, ValueError)):
                call_validator(str(validator.socket_path), session_id="session", last_assistant_message=json.dumps(output),
                    timeout_seconds=20, max_request_bytes=65536)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(check)
            assert started.wait(5)
            handle = handles[0]
            result_path = Path(handle.request.cwd).parent / "parsed/result.json"
            deadline = time.monotonic() + 5
            while not result_path.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert result_path.read_text() == "READY"
            before = time.monotonic()
            if stop == "close":
                validator.close()
            else:
                from test_claude_agent_host import _host, _start
                from hypoloom.claude_agent_host import AgentHostTerminalReason
                host = _host(tmp_path)
                agent = _start(host, mode="hang")
                agent._output_validator = validator
                try:
                    if stop == "agent-cancel":
                        assert host.cancel(agent)
                    elif stop == "agent-timeout":
                        agent.deadline_at = time.monotonic() - 1
                    else:
                        agent._process.terminate()
                        agent._process.wait(timeout=2)
                    observed = host.wait(agent)
                    if stop == "agent-cancel":
                        assert observed.terminal_reason is AgentHostTerminalReason.CANCELLED
                    elif stop == "agent-timeout":
                        assert observed.terminal_reason is AgentHostTerminalReason.TIMED_OUT
                    assert not process_group_exists(agent.process_group_id)
                finally:
                    host.cleanup(agent)
            assert time.monotonic() - before < 5
            pending.result(timeout=2)
        assert validator.snapshot()["accepted"] is False
        assert validator.snapshot()["call_count"] == 0
        assert not process_group_exists(handle.process_group_id)
        assert handle._guardian.poll() is not None
        assert not list(tmp_path.glob("parser-smoke-*"))
        assert not validator.socket_path.exists()
