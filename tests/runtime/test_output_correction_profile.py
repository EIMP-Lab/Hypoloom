"""The existing frozen node runtime owns bounded CLI output correction."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from hypoloom import full_cc_runtime
from hypoloom.agent_aci import EmbeddedInput
from hypoloom.fake_agent import default_request
from hypoloom.profile_v4 import (
    project_profile_v4,
    request_node_runtime,
    resolve_node_runtime,
    validate_product_settings,
)

from test_profile_v4 import documents


def _request(runtime):
    item = EmbeddedInput.from_bytes(
        "correction--expert-config", "application/json",
        json.dumps({"node_runtime": runtime}).encode(),
    )
    return replace(default_request(), input_artifacts=(item,))


@pytest.mark.parametrize("cap", [1, 3, 10])
def test_profile_correction_is_preserved_without_granting_budget(cap):
    source = documents()
    baseline = project_profile_v4(**source)
    source["methodology"]["output_correction"] = {"max_failed_validations": cap}
    before = copy.deepcopy(source)
    result = project_profile_v4(**source)
    assert not result.issues, result.issues
    assert source == before
    assert result.settings["methodology"]["output_correction"] == {
        "max_failed_validations": cap,
    }
    assert result.projection.methodology["budget"] == baseline.projection.methodology["budget"]
    assert validate_product_settings(result.settings) == result.settings


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, "3", None, [], {}])
def test_profile_rejects_nonpositive_or_untyped_validation_count(value):
    source = documents()
    source["methodology"]["output_correction"] = {"max_failed_validations": value}
    result = project_profile_v4(**source)
    assert any(issue.location == "methodology.yaml.output_correction.max_failed_validations"
               for issue in result.issues)


@pytest.mark.parametrize("value", [None, [], "3", True, {"max_failed_validations": 3, "extra": 1}])
def test_profile_rejects_malformed_correction_settings(value):
    source = documents()
    source["methodology"]["output_correction"] = value
    result = project_profile_v4(**source)
    assert result.issues
    settings = project_profile_v4(**documents()).settings
    settings["methodology"]["output_correction"] = value
    with pytest.raises(ValueError, match="Profile v4"):
        validate_product_settings(settings)


@pytest.mark.parametrize("route", ["grounding", "experiment_design_implement", "result_analysis"])
def test_node_runtime_freezes_correction_for_every_stage(route):
    source = documents()
    source["methodology"]["output_correction"] = {"max_failed_validations": 3}
    product = project_profile_v4(**source).settings
    runtime = resolve_node_runtime(
        {"methodology": {"controls": {"product": product}}}, "scientist", route,
    )
    assert runtime["output_correction"] == {"max_failed_validations": 3}
    request = _request(runtime)
    serialized = request.to_dict()
    product["methodology"]["output_correction"]["max_failed_validations"] = 9
    assert runtime["output_correction"]["max_failed_validations"] == 3
    runtime["output_correction"]["max_failed_validations"] = 8
    assert request_node_runtime(request.input_artifacts)["output_correction"] == {
        "max_failed_validations": 3,
    }
    assert full_cc_runtime.structured_output_validation_cap(request) == 3
    assert request.to_dict() == serialized


@pytest.mark.parametrize("runtime", [{}, {"output_correction": {}}])
def test_omitted_correction_leaf_keeps_one_without_changing_request(runtime):
    request = _request(runtime)
    serialized = request.to_dict()
    assert full_cc_runtime.structured_output_validation_cap(request) == 1
    assert request.to_dict() == serialized


def test_absent_configuration_keeps_legacy_environment_and_serialization():
    request = replace(default_request(), input_artifacts=())
    before = request.to_dict()
    assert full_cc_runtime.structured_output_validation_cap(request) == 1
    assert request.to_dict() == before
    assert full_cc_runtime.full_cc_environment()["MAX_STRUCTURED_OUTPUT_RETRIES"] == "1"
    product = project_profile_v4(**documents()).settings
    runtime = resolve_node_runtime(
        {"methodology": {"controls": {"product": product}}}, "scientist", "grounding",
    )
    assert "output_correction" not in runtime


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, "3", None, [], {}])
def test_frozen_runtime_rejects_invalid_validation_count(value):
    request = _request({"output_correction": {"max_failed_validations": value}})
    with pytest.raises(ValueError, match="max_failed_validations"):
        full_cc_runtime.structured_output_validation_cap(request)


@pytest.mark.parametrize("value", [None, [], "3", True, {"max_failed_validations": 3, "extra": 1}])
def test_frozen_runtime_rejects_malformed_correction_object(value):
    with pytest.raises(ValueError, match="output_correction"):
        full_cc_runtime.structured_output_validation_cap(_request({"output_correction": value}))


@pytest.mark.parametrize("cap", [1, 3, 10])
def test_selected_environment_value_is_not_ambient(cap, monkeypatch):
    monkeypatch.setenv("MAX_STRUCTURED_OUTPUT_RETRIES", "99")
    request = _request({"output_correction": {"max_failed_validations": cap}})
    selected = full_cc_runtime.structured_output_validation_cap(request)
    environment = full_cc_runtime.full_cc_environment(structured_output_failed_validation_cap=selected)
    assert environment["MAX_STRUCTURED_OUTPUT_RETRIES"] == str(cap)
    assert environment["CLAUDE_CODE_MAX_RETRIES"] == "0"
    assert full_cc_runtime.full_cc_environment()["MAX_STRUCTURED_OUTPUT_RETRIES"] == "1"


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, "3", None, [], {}])
def test_environment_rejects_invalid_direct_cap(value):
    with pytest.raises(ValueError, match="max_failed_validations"):
        full_cc_runtime.full_cc_environment(structured_output_failed_validation_cap=value)


def test_current_profile_template_selects_three_internal_validations():
    path = Path(__file__).resolve().parents[2] / "profiles/templates/multi-expert-en/methodology.yaml"
    methodology = yaml.safe_load(path.read_text())
    assert methodology["output_correction"] == {"max_failed_validations": 3}
