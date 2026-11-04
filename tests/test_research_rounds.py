"""Public configuration and progress semantics for uncapped research cycles."""
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace

import pytest
import yaml

from hypoloom.expert_batch import batch_limits, research_round_available
from hypoloom.agent_aci import AgentRequest
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import default_request
from hypoloom.full_cc_runtime import full_cc_instruction
from hypoloom.execution_budget import research_round_budget
from hypoloom.profile_v4 import project_profile_v4, validate_product_settings


def source():
    directory = Path(__file__).resolve().parents[1] / "profiles/templates/multi-expert-en"
    return {path.stem: yaml.safe_load(path.read_text()) for path in directory.glob("*.yaml")}


@pytest.mark.parametrize("value", [1, 4, None, 0, -1])
def test_removed_round_option_requires_deletion(value):
    documents = source()
    documents["goal"]["stop_conditions"] = {"max_rounds_per_expert": value}
    result = project_profile_v4(**documents)
    assert any(issue.location.endswith("max_rounds_per_expert") and "removed" in issue.message for issue in result.issues)


def test_progress_has_no_implicit_experiment_count_as_round_limit():
    document = {"budget": {"max_experiments": 2}}
    limits = batch_limits(document, "scientist")
    assert "rounds" not in limits
    task = SimpleNamespace(key=SimpleNamespace(cycle=7, experiment_ordinal=1))
    progress = research_round_budget(task, limits)
    assert progress["current_round"] == 7
    assert "remaining_future_rounds" not in progress
    assert "max_rounds_per_expert" not in progress
    assert research_round_available(document, 8, 31)
    assert not research_round_available(document, 8, 30)


def test_old_controls_are_readable_but_rejected_for_new_admission():
    settings = project_profile_v4(**source()).settings
    settings["goal"]["stop_conditions"]["max_rounds_per_expert"] = 4
    assert validate_product_settings(settings, allow_legacy_round_limit=True) == settings
    with pytest.raises(ValueError, match="removed"):
        validate_product_settings(settings)


def test_current_expert_guidance_has_progress_without_remaining_rounds():
    request = replace(default_request(AgentCommand.EXPERIMENT_EXECUTION), schema_version=31)
    assert AgentRequest.from_json(request.to_json()) == request
    prompt = full_cc_instruction(request)
    assert "no research-round limit" in prompt
    assert "remaining_future_rounds" not in prompt
    assert "finite allowances" in prompt
