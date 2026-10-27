"""Native Stop validation is diagnostic evidence, not Host acceptance."""

import copy

import pytest

from hypoloom.debug_mode import _structured_output_correction
from hypoloom.diagnostics import diagnostic_summary


def _observation(**correction):
    return {"diagnostics": {"provider_subtype": "success", "structured_output_correction": {
        "scope": "native_stop_validation", "status": "recovered", "rejected_count": 2,
        "rounds": [{"round": 1, "status": "rejected"}, {"round": 2, "status": "accepted"}],
        **correction,
    }}}


@pytest.mark.parametrize("rejected", [1, 2, 9])
def test_native_recovery_summary_names_observed_completions_not_a_tool(rejected):
    observation = _observation(rejected_count=rejected)
    before = copy.deepcopy(observation)
    assert diagnostic_summary(observation) == (
        f"Native CC output recovered after {rejected} rejected completion(s); Host acceptance is separate.")
    assert observation == before


@pytest.mark.parametrize("status", ["first_pass", "unresolved", "not_observed", "validation_failed"])
def test_native_other_statuses_do_not_infer_recovery(status):
    assert diagnostic_summary(_observation(status=status)) is None


@pytest.mark.parametrize("rejected", [None, 0, -1, True, False, 1.5, "2", {}])
def test_native_recovery_requires_an_explicit_positive_integer_count(rejected):
    assert diagnostic_summary(_observation(rejected_count=rejected)) is None


def test_native_rounds_do_not_substitute_for_missing_rejected_count():
    observation = _observation()
    del observation["diagnostics"]["structured_output_correction"]["rejected_count"]
    assert diagnostic_summary(observation) is None


@pytest.mark.parametrize("scope", [None, "", "unobserved_stop_validation"])
def test_other_scopes_do_not_infer_native_recovery(scope):
    assert diagnostic_summary(_observation(scope=scope)) is None


@pytest.mark.parametrize("rejected", [1, 2])
def test_legacy_tool_observation_summary_is_unchanged(rejected):
    assert diagnostic_summary(_observation(scope="cc_tool_observation", rejected_count=rejected)) == (
        f"CC StructuredOutput recovered after {rejected} rejected call(s); Host acceptance is separate.")


@pytest.mark.parametrize("field", [
    "literature_diagnostic", "resource_diagnostic", "workspace_error", "agent_host_diagnostic",
    "provider_diagnostic", "integrity_error", "validation_error",
])
def test_top_level_errors_keep_priority_over_native_recovery(field):
    observation = _observation()
    observation[field] = "primary error"
    assert diagnostic_summary(observation) == "primary error"


@pytest.mark.parametrize("field", [
    "literature_diagnostic", "resource_diagnostic", "workspace_error", "agent_host_diagnostic",
    "integrity_error", "failure", "provider_diagnostic",
])
def test_nested_errors_keep_priority_over_native_recovery(field):
    observation = _observation()
    observation["diagnostics"][field] = "primary error"
    assert diagnostic_summary(observation) == "primary error"


def test_host_validation_error_keeps_priority_over_native_recovery():
    observation = _observation()
    observation["diagnostics"]["validation_error"] = {
        "code": "invalid_candidate", "message": "Host candidate rejected"}
    assert diagnostic_summary(observation) == "invalid_candidate: Host candidate rejected"


@pytest.mark.parametrize("nested", [False, True])
def test_primary_error_is_redacted_before_native_recovery_summary(nested):
    observation = _observation()
    target = observation["diagnostics"] if nested else observation
    target["provider_diagnostic"] = "Authorization: Bearer private-test-token"
    assert diagnostic_summary(observation) == "[credential-shaped diagnostic redacted]"


def test_native_summary_does_not_project_round_candidate_or_error_text():
    observation = _observation(rounds=[{
        "round": 1, "status": "rejected", "candidate": "private candidate body",
        "validation_error": "Authorization: Bearer private-test-token",
    }])
    assert diagnostic_summary(observation) == (
        "Native CC output recovered after 2 rejected completion(s); Host acceptance is separate.")


def test_native_unresolved_keeps_existing_provider_failure_fallback():
    observation = _observation(status="unresolved")
    observation["diagnostics"]["provider_subtype"] = "error_max_turns"
    assert diagnostic_summary(observation) == "error_max_turns"


def _native_trace(**overrides):
    return {
        "scope": "native_stop_validation", "validation_source": "host_stop_hook",
        "status": "recovered", "accepted": True, "call_count": 2, "rejected_count": 1,
        "rounds": [
            {"ordinal": 1, "status": "rejected", "validation_error": "typed_candidate must be an object"},
            {"ordinal": 2, "status": "accepted", "validation_error": None},
        ],
        **overrides,
    }


@pytest.mark.parametrize("status,accepted", [
    ("first_pass", True), ("recovered", True), ("unresolved", False),
    ("not_observed", False), ("validation_failed", False),
])
def test_debug_preserves_explicit_native_snapshot_without_tool_identity(status, accepted):
    trace = _native_trace(status=status, accepted=accepted)
    before = copy.deepcopy(trace)
    assert _structured_output_correction(trace) == trace
    assert trace == before


@pytest.mark.parametrize("changed", [
    {"validation_source": "unknown"}, {"status": "tool_succeeded"}, {"accepted": 1},
    {"call_count": True}, {"rejected_count": -1}, {"rejected_count": "1"}, {"rounds": "private"},
])
def test_debug_does_not_reconstruct_malformed_native_snapshot(changed):
    assert _structured_output_correction(_native_trace(**changed)) is None


def test_debug_native_trace_redacts_errors_and_drops_unadmitted_values():
    trace = _native_trace(status="validation_failed", accepted=False,
        error="Authorization: Bearer private-test-token", candidate="private candidate body")
    trace["rounds"][0].update(validation_error="api_key=private-test-token",
        tool_use_id="invented-tool", message_id="invented-message", candidate="private round body")
    projected = _structured_output_correction(trace)
    assert projected is not None
    assert projected["error"] == "[credential-shaped diagnostic redacted]"
    assert projected["rounds"][0] == {
        "ordinal": 1, "status": "rejected", "validation_error": "[credential-shaped diagnostic redacted]"}
    assert set(projected) == set(_native_trace()) | {"error"}


@pytest.mark.parametrize("stage,expected", [
    ("output_schema", "output_schema"),
    ("Authorization: Bearer private-test-token", "[credential-shaped diagnostic redacted]"),
    (123, None),
])
def test_debug_preserves_optional_validation_stage_with_existing_redaction(stage, expected):
    trace = _native_trace()
    trace["rounds"][0]["validation_stage"] = stage
    projected = _structured_output_correction(trace)
    assert projected["rounds"][0]["validation_stage"] == expected
    assert projected["rounds"][1] == trace["rounds"][1]
    assert trace["rounds"][0]["validation_stage"] == stage


def test_debug_native_rounds_keep_only_observed_status_and_integer_ordinal():
    trace = _native_trace(rounds=[
        {"ordinal": 1, "status": "error", "validation_error": "hook failed"},
        {"ordinal": True, "status": "accepted", "validation_error": None},
        {"ordinal": 3, "status": "tool_succeeded", "candidate": "private body"},
    ])
    projected = _structured_output_correction(trace)
    assert projected is not None
    assert projected["rounds"] == [
        {"ordinal": 1, "status": "error", "validation_error": "hook failed"},
        {"ordinal": None, "status": "accepted", "validation_error": None},
    ]
    assert projected["truncated"] is True


def test_debug_native_errors_and_rounds_reuse_existing_projection_bounds():
    trace = _native_trace(error="x" * 2000, rounds=[
        {"ordinal": number, "status": "rejected", "validation_error": "x" * 2000}
        for number in range(1, 66)
    ])
    projected = _structured_output_correction(trace)
    assert projected is not None
    assert len(projected["rounds"]) == 64 and projected["truncated"] is True
    assert len(projected["error"]) == len(projected["rounds"][0]["validation_error"]) == 1024
