"""Research cycles are progress, while actual resource budgets stop work."""
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from hypoloom.expert_batch import batch_limits
from hypoloom.control_protocol import ControlRequest
from hypoloom.execution_budget import research_round_budget
from hypoloom.profile_v4 import project_profile_v4, validate_product_settings
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_profile_v4 import documents
from tests.runtime.test_expert_loop_host import _pilot_payload


@pytest.mark.parametrize("value", [1, 4, None, 0])
def test_new_profiles_reject_removed_round_limit(value):
    source = documents()
    source["goal"]["stop_conditions"]["max_rounds_per_expert"] = value
    result = project_profile_v4(**source)
    assert any(issue.location.endswith("max_rounds_per_expert") and "removed" in issue.message
        for issue in result.issues)


def test_experiment_budget_is_not_projected_as_remaining_rounds():
    limits = batch_limits({"budget": {"max_experiments": 2}}, "researcher")
    assert "rounds" not in limits
    task = SimpleNamespace(key=SimpleNamespace(cycle=7, experiment_ordinal=1))
    progress = research_round_budget(task, limits)
    assert progress["current_round"] == 7
    assert "max_rounds_per_expert" not in progress
    assert "remaining_future_rounds" not in progress


def test_old_controls_remain_readable_but_not_admissible_for_new_runs():
    settings = project_profile_v4(**documents()).settings
    settings["goal"]["stop_conditions"]["max_rounds_per_expert"] = 3
    assert validate_product_settings(settings, allow_legacy_round_limit=True) == settings
    with pytest.raises(ValueError, match="removed"):
        validate_product_settings(settings)


def test_default_experiment_count_is_independent_of_rounds():
    source = documents()
    projected = project_profile_v4(**source)
    assert not projected.issues
    assert projected.projection.methodology["budget"]["max_experiments"] == 50


@pytest.mark.parametrize("version,expected", [(30, "accepted"), (31, "rejected")])
def test_direct_run_creation_cannot_reintroduce_removed_round_limit(tmp_path, monkeypatch, version, expected):
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", version)
    with tempfile.TemporaryDirectory(prefix="round-limit-target-", dir="/var/tmp") as directory:
        payload = _pilot_payload(tmp_path, Path(directory), "round-limit")
        contract = payload["research_contract"]
        settings = project_profile_v4(**documents()).settings
        settings["experts"]["experts"][0]["name"] = contract["roles"][0]["id"]
        settings["goal"]["stop_conditions"]["max_rounds_per_expert"] = 1
        contract["methodology"]["controls"]["product"] = settings
        host = WorkflowHost(tmp_path / "state")
        host.initialize()
        try:
            response = host.handle(ControlRequest(1, "create", 0, "CREATE_RUN", payload))
            assert response.status == expected, response
            if expected == "rejected":
                assert "removed" in str(response)
                assert host.store.get_run("round-limit") is None
        finally:
            host.close()


@pytest.mark.parametrize("version,available", [(30, False), (31, True)])
def test_unselected_revision_can_pass_old_fallback_with_resources_remaining(version, available):
    host = object.__new__(WorkflowHost)
    host.store = SimpleNamespace(get_run=lambda _: {"agent_aci_version": str(version)})
    host._batch_history = lambda *args: SimpleNamespace(latest=SimpleNamespace(cycle=7, selection={"selected_tip": None}))
    host._research_contract = lambda _: SimpleNamespace(document={"budget": {"max_experiments": 2}})
    host._batch_creation_available = lambda *args: True
    host._batch_science_failed = lambda *args: False
    host._expert_task_final_failure = lambda *args: None
    host._expert_success = lambda task: {"observation_json": json.dumps({"response": {"typed_candidate": {
        "update_disposition": "REVISE_NEXT_CYCLE"}}})}
    assert host._unselected_batch_revision_ready("run", "component", "role") is available
