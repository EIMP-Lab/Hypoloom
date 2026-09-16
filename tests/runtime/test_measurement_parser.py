import json
from pathlib import Path

from hypoloom.domain import AttemptTerminalReason
from hypoloom.measurement_execution import prepare_parser, run_parser
from tests.runtime.test_measurement_contract import declaration


def test_real_parser_reads_csv_computes_median_and_cannot_mutate_raw(tmp_path: Path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "timings.csv").write_text("10,8\n10,9\n10,100\n")
    source = b'''import csv, json, os, statistics
from pathlib import Path
rows = list(csv.reader(Path('/raw/timings.csv').open()))
try:
    Path('/raw/timings.csv').write_text('mutated')
    raise AssertionError('raw output was writable')
except OSError: pass
assert not Path('/dev/nvidia0').exists()
assert not os.environ.get('ANTHROPIC_API_KEY')
assert not Path('/input/experiment.py').exists()
Path(os.environ['HYPOLOOM_MEASUREMENT_RESULT_PATH']).write_text(json.dumps({
    'metric': 'median_latency_delta',
    'value': str(statistics.median(float(row[1]) for row in rows)),
    'baseline': str(statistics.median(float(row[0]) for row in rows))}))
'''
    prepared = prepare_parser(declaration(), {"parse_metrics.py": source, "experiment.py": b"private"},
                              raw_dir=raw, work_dir=tmp_path / "parser",
                              attempt_id="parser-1", execution_id="execution-1", timeout_seconds=5)
    result = run_parser(prepared)
    assert result.observation.terminal_reason is AttemptTerminalReason.SUCCEEDED
    assert result.raw_result == {"metric": "median_latency_delta", "value": "9.0", "baseline": "10.0"}
    assert (raw / "timings.csv").read_text() == "10,8\n10,9\n10,100\n"


def test_parser_planning_is_pure_and_recovery_reconstructs_same_request(tmp_path: Path):
    import pytest
    from hypoloom.measurement_execution import plan_parser, materialize_parser

    arguments = dict(raw_dir=tmp_path / "raw", work_dir=tmp_path / "parser", attempt_id="parser-1",
                     execution_id="execution-1", timeout_seconds=5)
    prepared = plan_parser(declaration(), {"parse_metrics.py": b"pass"}, **arguments)
    assert list(tmp_path.iterdir()) == []
    materialize_parser(prepared)
    assert plan_parser(declaration(), {"parse_metrics.py": b"pass"}, **arguments) == prepared
    with pytest.raises(ValueError, match="fresh"):
        materialize_parser(prepared)


def test_real_parser_timeout_and_finite_output_limit(tmp_path: Path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "timings.csv").write_text("10,8\n")
    for name, source, timeout in (
        ("timeout", b"while True: pass", 0.15),
        ("output", b"from pathlib import Path\nPath('/parsed/result.json').write_bytes(b'x'*65536)", 3),
    ):
        prepared = prepare_parser(declaration(), {"parse_metrics.py": source}, raw_dir=raw,
                                  work_dir=tmp_path / name, attempt_id=name,
                                  execution_id="execution-1", timeout_seconds=timeout, max_output_bytes=1024)
        result = run_parser(prepared)
        assert result.observation.terminal_reason is not AttemptTerminalReason.SUCCEEDED
        if name == "timeout":
            assert result.observation.terminal_reason is AttemptTerminalReason.TIMED_OUT
        assert result.raw_result is None
        path = prepared.output_dir / "result.json"
        assert not path.exists() or path.stat().st_size <= 1024


def test_driver_runs_real_non_python_raw_then_parser_under_one_outer_runner(tmp_path: Path):
    from hypoloom.measurement_driver import sequence_request
    from hypoloom.runner import LocalRunner, ProcessRequest
    from tests.runtime.test_execution_workspace import candidate_pair, make_workspace

    workspace = make_workspace(tmp_path)
    request, response = candidate_pair()
    raw = workspace.plan(agent_request=request, agent_response=response, attempt_id="raw-1",
                         attempt_ordinal=1, execution_id="execution-1", target_env={}, timeout_seconds=5,
                         execution_argv=("/bin/sh", "-c", "printf '10,8\n10,9\n10,100\n' > timings.csv; echo raw-stage"),
                         allow_generic_argv=True, read_only_bindings=(("/usr/bin", "/bin"),))
    workspace.materialize(raw, agent_request=request, agent_response=response)
    parser = prepare_parser(declaration(), {"parse_metrics.py": b'''import json
from pathlib import Path
rows = [list(map(float, row.split(','))) for row in Path('/raw/timings.csv').read_text().splitlines()]
Path('/parsed/result.json').write_text(json.dumps({'metric':'median_latency_delta','value':str(sorted(row[1] for row in rows)[1]),'baseline':'10'}))
print('parser-stage')
'''}, raw_dir=raw.output_dir, work_dir=tmp_path / "parser", attempt_id="parser-1",
        execution_id="execution-1", timeout_seconds=5)
    outer = sequence_request(raw.process_request, parser.process_request, attempt_id="outer-1",
                             attempt_ordinal=1, execution_id="execution-1", cwd=tmp_path,
                             timeout_seconds=12)
    assert isinstance(outer, ProcessRequest)
    observation = LocalRunner().run(outer)
    assert observation.returncode == 0
    assert json.loads((parser.output_dir / "result.json").read_text())["value"] == "9.0"
    assert "raw-stage" in (tmp_path / "stdout.log").read_text()
    assert "parser-stage" in (tmp_path / "stdout.log").read_text()


def test_parser_accepts_non_python_program_and_rejects_raw_symlink(tmp_path: Path):
    from dataclasses import replace
    import pytest

    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "timings.csv").write_text("raw")
    spec = replace(declaration(), parser_argv=("/bin/sh", "/input/parser.sh"), parser_files=("parser.sh",))
    source = b'''printf '{"metric":"median_latency_delta","value":"8.5","baseline":"10"}' > /parsed/result.json'''
    prepared = prepare_parser(spec, {"parser.sh": source}, raw_dir=raw, work_dir=tmp_path / "parser",
                              attempt_id="parser-1", execution_id="execution-1", timeout_seconds=5)
    assert run_parser(prepared).raw_result["value"] == "8.5"
    (raw / "timings.csv").unlink()
    (raw / "timings.csv").symlink_to(tmp_path / "outside")
    with pytest.raises((ValueError, OSError)):
        run_parser(prepared)


def test_failed_raw_phase_never_launches_parser(tmp_path: Path):
    from hypoloom.measurement_driver import RAW_EXECUTION_FAILED, sequence_request
    from hypoloom.runner import LocalRunner, ProcessRequest

    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "timings.csv").write_text("raw")
    parser = prepare_parser(declaration(), {"parse_metrics.py": b"raise AssertionError('parser must not start')"},
                            raw_dir=raw, work_dir=tmp_path / "parser", attempt_id="parser-1",
                            execution_id="execution-1", timeout_seconds=5)
    raw_request = ProcessRequest(attempt_id="raw-1", attempt_ordinal=1, execution_id="execution-1",
                                 argv=("/bin/false",), cwd=str(raw), env={}, timeout_seconds=5)
    outer = sequence_request(raw_request, parser.process_request, attempt_id="outer-1",
                             attempt_ordinal=1, execution_id="execution-1", cwd=tmp_path, timeout_seconds=10)
    assert LocalRunner().run(outer).returncode == RAW_EXECUTION_FAILED
    assert not (Path(parser.process_request.cwd) / "stderr.log").exists()


def test_cancelling_outer_runner_stops_raw_children_before_parser(tmp_path: Path):
    import time
    from hypoloom.measurement_driver import sequence_request
    from hypoloom.runner import LocalRunner
    from tests.runtime.test_execution_workspace import candidate_pair, make_workspace

    workspace = make_workspace(tmp_path)
    request, response = candidate_pair()
    raw = workspace.plan(agent_request=request, agent_response=response, attempt_id="raw-1",
                         attempt_ordinal=1, execution_id="execution-1", target_env={}, timeout_seconds=10,
                         execution_argv=("/bin/sh", "-c", "while true; do echo tick >> timings.csv; /bin/sleep 0.03; done"),
                         allow_generic_argv=True, read_only_bindings=(("/usr/bin", "/bin"),))
    workspace.materialize(raw, agent_request=request, agent_response=response)
    parser = prepare_parser(declaration(), {"parse_metrics.py": b"raise AssertionError('parser must not start')"},
                            raw_dir=raw.output_dir, work_dir=tmp_path / "parser", attempt_id="parser-1",
                            execution_id="execution-1", timeout_seconds=5)
    outer = sequence_request(raw.process_request, parser.process_request, attempt_id="outer-1",
                             attempt_ordinal=1, execution_id="execution-1", cwd=tmp_path, timeout_seconds=15)
    runner = LocalRunner()
    handle = runner.start(outer)
    ticks = raw.output_dir / "timings.csv"
    try:
        deadline = time.monotonic() + 5
        while not ticks.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ticks.exists()
    finally:
        runner.cancel(handle)
        observed = runner.wait(handle)
    assert observed.terminal_reason is AttemptTerminalReason.CANCELLED
    time.sleep(0.15)
    stable = ticks.read_bytes()
    time.sleep(0.15)
    assert ticks.read_bytes() == stable
    assert not (Path(parser.process_request.cwd) / "stderr.log").exists()
