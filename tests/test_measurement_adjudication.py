"""Portable regressions for threshold equality and same-invocation adjudication."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.domain import AgentCommand, MetricDirection, MetricSpec
from hypoloom.fake_agent import default_request
from hypoloom.measurement_contract import (
    MeasurementComparison, MeasurementContractError, ScientificMeasurement,
    assert_frozen_measurement, evaluate_parsed_measurement,
)
from hypoloom.measurement_oracle import interpret_measurement


def measurement(direction="MAXIMIZE", **changes):
    return ScientificMeasurement(
        metric=MetricSpec(name="paired_gain", direction=MetricDirection(direction), target="0"),
        comparison=changes.pop("comparison", MeasurementComparison.VALUE),
        parse_rule="Compare the measured paired gain to zero using success_operator.",
        parser_argv=("python", "/input/parse.py"), parser_files=("parse.py",),
        raw_artifacts=("scores.json",), **changes)


@pytest.mark.parametrize("direction,operator,accepted", [
    ("MAXIMIZE", "GT", [False, False, True]),
    ("MAXIMIZE", "GE", [False, True, True]),
    ("MINIMIZE", "LT", [True, False, False]),
    ("MINIMIZE", "LE", [True, True, False]),
])
@pytest.mark.parametrize("comparison", list(MeasurementComparison))
def test_explicit_operator_compares_transformed_measurement(direction, operator, accepted, comparison):
    declaration = measurement(direction, success_operator=operator, comparison=comparison)
    assert ScientificMeasurement.from_dict(declaration.to_dict()) == declaration
    for value, expected in zip((-1, 0, 1), accepted, strict=True):
        raw = {"metric": "paired_gain", "value": value}
        if comparison is not MeasurementComparison.VALUE:
            raw.update(value=value + 2, baseline=2)
        result = evaluate_parsed_measurement(declaration, raw)
        audited = interpret_measurement(declaration.to_dict(), raw)
        assert result.criterion_met is audited.criterion_met is expected
        assert result.metric_value == audited.metric_value


@pytest.mark.parametrize("direction", ["MINIMIZE", "MAXIMIZE"])
def test_legacy_declaration_preserves_bytes_and_equality(direction):
    from hypoloom.domain import HypothesisProposed, fact_to_dict
    declaration = measurement(direction)
    raw = declaration.to_dict()
    assert "success_operator" not in raw
    assert ScientificMeasurement.from_dict(raw).to_dict() == raw
    fact = HypothesisProposed(fact_id="fact-hyp", run_id="run", hypothesis_id="hyp",
        goal_id="goal", component_id="component", claim="Compare paired gains",
        proposer_role="expert", method_family_id="family", measurement=declaration)
    assert fact_to_dict(fact)["measurement"] == raw
    observed = {"metric": "paired_gain", "value": 0}
    assert evaluate_parsed_measurement(declaration, observed).criterion_met
    assert interpret_measurement(raw, observed).criterion_met


@pytest.mark.parametrize("direction,operator", [
    ("MAXIMIZE", "LT"), ("MAXIMIZE", "LE"), ("MINIMIZE", "GT"), ("MINIMIZE", "GE"),
    ("MAXIMIZE", "EQ"), ("MAXIMIZE", ">"), ("MAXIMIZE", True), ("MAXIMIZE", []),
])
def test_incompatible_or_invalid_operator_is_rejected(direction, operator):
    raw = measurement(direction).to_dict()
    raw["success_operator"] = operator
    with pytest.raises(MeasurementContractError, match="success_operator"):
        ScientificMeasurement.from_dict(raw)
    with pytest.raises(ValueError, match="success_operator"):
        interpret_measurement(raw, {"metric": "paired_gain", "value": 0})


def test_explicit_null_does_not_silently_restore_inclusive_comparison():
    raw = {**measurement().to_dict(), "success_operator": None}
    with pytest.raises(MeasurementContractError, match="success_operator"):
        ScientificMeasurement.from_dict(raw)
    with pytest.raises(ValueError, match="success_operator"):
        interpret_measurement(raw, {"metric": "paired_gain", "value": 0})


def test_success_operator_is_frozen_with_the_original_measurement():
    original = measurement(success_operator="GT")
    for changed in (replace(original, success_operator="GE"), measurement()):
        with pytest.raises(MeasurementContractError, match="measurement_frozen"):
            assert_frozen_measurement(original, changed, {"parse.py": b"same"}, {"parse.py": b"same"})


def adjudication_request(version=33):
    request = replace(default_request(AgentCommand.ADJUDICATE), schema_version=version)
    tips = {}
    for chain, outcome, value in (("positive", "SUCCESS", "0.01"), ("zero", "CLEAN_REFUTATION", "0")):
        tips[chain] = dict(run_id=request.run_id, component_id=request.component_id,
            expert_slot=request.role, chain_id=chain, node_id="hyp-" + chain,
            experiment_id="exp-" + chain, outcome_id="out-" + chain,
            outcome_fact_id="fact-out-" + chain, artifact_id="artifact-" + chain,
            method_family_id="family", outcome=outcome, result_kind="metric", metric_value=value,
            measurement=measurement(success_operator="GT").to_dict())
    batch = dict(cycle=1, chains={k: v["node_id"] for k, v in tips.items()},
        terminal_tips=tips, selected_tip=None, batch_command_id="accepted-batch")
    artifact = EmbeddedInput.from_bytes(request.command_id + "--batch-context", "application/json",
        json.dumps(batch).encode())
    request = replace(request, input_artifacts=(artifact,), immutable_context_refs=(artifact.ref,))
    candidate = dict(schema_version=1, best_chain="positive", keep=list(tips), prune=[],
        summary="Positive local evidence is not overall goal attainment; zero fails the strict criterion.",
        verdicts=[dict(chain_id=k, node_id=v["node_id"],
            status="verified" if v["outcome"] == "SUCCESS" else "refuted", evidence=[v["outcome_id"]])
            for k, v in tips.items()])
    return request, candidate


def test_frozen_adjudication_uses_host_rules_with_actionable_status_error():
    from hypoloom.agent_aci import validate_adjudication_candidate
    request, candidate = adjudication_request()
    accepted = validate_adjudication_candidate(request, candidate)
    assert accepted.best_chain == "positive"
    bad = deepcopy(candidate)
    bad["verdicts"][1]["status"] = "inconclusive"
    with pytest.raises(ValueError) as failure:
        validate_adjudication_candidate(request, bad)
    assert all(value in str(failure.value) for value in ("zero", "CLEAN_REFUTATION", "refuted", "inconclusive"))


def test_adjudication_schema_and_native_stop_share_terminal_reference_domain(tmp_path):
    from hypoloom.agent_aci import adjudication_reference_ids
    from hypoloom.claude_agent_host import _native_candidate_check, _output_schema
    from hypoloom.native_output import NativeOutputValidator, call_validator
    import time

    request, candidate = adjudication_request()
    historical = EmbeddedInput.from_bytes("fact-out-historical", "application/json", json.dumps({
        "fact_id": "fact-out-historical", "fact_type": "CanonicalOutcomeAccepted",
        "payload": {"run_id": request.run_id, "component_id": request.component_id,
            "outcome_id": "out-historical", "experiment_id": "exp-historical", "artifact_id": "artifact-historical"},
    }).encode())
    request = replace(request, input_artifacts=(*request.input_artifacts, historical),
        immutable_context_refs=(*request.immutable_context_refs, historical.ref))
    expected = {*request.immutable_context_refs}
    for chain in ("positive", "zero"):
        expected.update(prefix + chain for prefix in ("hyp-", "exp-", "out-", "fact-out-", "artifact-"))
    assert adjudication_reference_ids(request) == expected
    assert "fact-out-historical" in expected and "out-historical" not in expected
    schema = _output_schema(request, reference_aliases=False)["properties"]["typed_candidate"]
    assert set(schema["properties"]["verdicts"]["items"]["properties"]["evidence"]["items"]["enum"]) == expected
    # Use the real socket/Stop correction path without model calls or state writes.
    server = NativeOutputValidator(tmp_path / "stop.sock", output_schema={"type": "object"},
            session_id="owned", max_failed_validations=3, max_request_bytes=16384,
            deadline_at=time.monotonic() + 10, candidate_check=_native_candidate_check(request, None))
    try:
        def submit(value):
            return call_validator(str(server.socket_path), session_id="owned",
                last_assistant_message=json.dumps({"typed_candidate": value}),
                max_request_bytes=16384, timeout_seconds=2)
        bad = deepcopy(candidate)
        bad["verdicts"][1]["status"] = "inconclusive"
        reply = submit(bad)
        assert reply["decision"] == "block" and "CLEAN_REFUTATION" in reply["reason"]
        bad = deepcopy(candidate)
        bad["verdicts"][0]["evidence"].append("out-historical")
        reply = submit(bad)
        assert reply["decision"] == "block" and "out-historical" in reply["reason"]
        assert submit(candidate) == {}
        assert server.snapshot()["status"] == "recovered"
        assert json.loads(server.accepted_text())["typed_candidate"] == candidate
    finally:
        server.close()


def test_postflight_rejects_semantic_mismatch_even_without_native_stop():
    from hypoloom.agent_aci import validate_postflight, with_output_digest
    from hypoloom.fake_agent import FakeAgent
    request, candidate = adjudication_request()
    response = with_output_digest(replace(FakeAgent().invoke(request), typed_candidate=candidate))
    validate_postflight(request, response)
    bad = deepcopy(candidate)
    bad["verdicts"][0]["status"] = "refuted"
    with pytest.raises(ValueError, match="expected_status.*verified"):
        validate_postflight(request, with_output_digest(replace(response, typed_candidate=bad)))


def test_observer_does_not_call_equality_a_strict_improvement():
    from hypoloom.observation_api import _metric_target_met
    declared = measurement(success_operator="GT").to_dict()
    spec = {"metric": declared["metric"], "measurement": declared}
    assert _metric_target_met(spec, "0") is False
    assert _metric_target_met(spec, "0.01") is True
    assert _metric_target_met({"metric": declared["metric"]}, "0") is True


def test_new_declarations_require_explicit_operator_and_legacy_requests_reject_it():
    from hypoloom.agent_aci import AgentACIError, _validate_measurement_version
    from hypoloom.claude_agent_host import _measurement_schema
    import jsonschema

    request, _ = adjudication_request()
    strict = measurement(success_operator="GT")
    _validate_measurement_version(request, strict)
    _validate_measurement_version(replace(request, schema_version=32), measurement())
    _validate_measurement_version(request, None)  # GOAL-only remains supported.
    with pytest.raises(AgentACIError, match="success_operator is required"):
        _validate_measurement_version(request, measurement())
    with pytest.raises(AgentACIError, match="requires ACI33"):
        _validate_measurement_version(replace(request, schema_version=32), strict)
    jsonschema.validate(strict.to_dict(), _measurement_schema(33))
    jsonschema.validate(measurement().to_dict(), _measurement_schema(32))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(measurement().to_dict(), _measurement_schema(33))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(strict.to_dict(), _measurement_schema(32))
