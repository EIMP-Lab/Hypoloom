from dataclasses import replace

import pytest

from hypoloom.measurement_contract import MeasurementComparison
from hypoloom.measurement_oracle import interpret_measurement
from tests.runtime.test_measurement_contract import declaration


@pytest.mark.parametrize("comparison,expected", [("VALUE", "9"), ("DELTA_FROM_BASELINE", "-1"),
                                               ("RELATIVE_DELTA_FROM_BASELINE", "-0.1")])
def test_independent_oracle_comparison(comparison, expected):
    measure = replace(declaration(), comparison=MeasurementComparison(comparison))
    result = interpret_measurement(measure.to_dict(), {"metric": measure.metric.name, "value": "9", "baseline": "10"})
    assert result.metric_value == expected
    assert result.criterion_met is (comparison != "VALUE")


@pytest.mark.parametrize("bad", [True, "NaN", "Infinity", [], {}])
def test_independent_oracle_rejects_nonfinite_and_untyped_values(bad):
    with pytest.raises(ValueError):
        interpret_measurement(declaration().to_dict(), {"metric": declaration().metric.name, "value": bad, "baseline": "10"})


def test_independent_oracle_does_not_consult_production_comparison(monkeypatch):
    import hypoloom.measurement_contract as producer
    monkeypatch.setattr(producer, "evaluate_parsed_measurement", lambda *_: pytest.fail("Oracle used producer comparison"))
    result = interpret_measurement(declaration().to_dict(), {"metric": declaration().metric.name, "value": "9", "baseline": "10"})
    assert result.metric_value == "-0.1"


@pytest.mark.parametrize("damage", [False, True])
def test_process_abi_uses_recorded_runtime_source_not_oracle_checkout(tmp_path, damage):
    import json
    from hypoloom.measurement_driver import sequence_request
    from hypoloom.measurement_execution import plan_parser
    from hypoloom.oracle import _measurement_process_requests
    from hypoloom.runner import ProcessRequest
    import sys
    measure = declaration()
    work = tmp_path / "attempt"
    raw = ProcessRequest(attempt_id="process-1", attempt_ordinal=1, execution_id="exec-1",
        argv=("/usr/bin/bwrap", "--ro-bind", sys.prefix, "/runtime"), cwd=str(work / "logs"),
        env={}, timeout_seconds=60, stdout_path="raw-stdout.log", stderr_path="raw-stderr.log")
    parser = plan_parser(measure, {path: b"print(1)" for path in measure.parser_files},
        raw_dir=work / "output", work_dir=work / "measurement-parser",
        attempt_id="process-1--parser", execution_id="exec-1", timeout_seconds=30.0).process_request
    request = sequence_request(raw, parser, attempt_id=raw.attempt_id, attempt_ordinal=1,
        execution_id=raw.execution_id, cwd=raw.cwd, timeout_seconds=60).to_dict()
    source = "/tmp/frozen-source-copy"
    request["env"]["PYTHONPATH"] = source
    plan = json.loads(request["argv"][3])
    argv = plan["parser_request"]["argv"]
    index = argv.index("/measurement-driver.py")
    argv[index - 1] = source + ("/wrong.py" if damage else "/hypoloom/measurement_driver.py")
    request["argv"][3] = json.dumps(plan)
    issues = []
    _measurement_process_requests(request, {"payload": {"measurement": measure.to_dict()}}, "process-1", issues)
    assert [issue.code for issue in issues] == (["measurement_process_binding_mismatch"] if damage else [])
