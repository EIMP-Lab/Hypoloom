from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from hypoloom.profile_v4 import project_profile_v4
from hypoloom.profile_admission import admit_profile
from hypoloom.domain import ResearchContract
from test_profile_admission import _materialize_expert_loop_pilot_v3


@pytest.mark.parametrize("port", [0, 8766, 65535])
def test_dashboard_accepts_dynamic_or_fixed_port(port):
    source = documents()
    source["notify"]["sinks"]["dashboard"]["port"] = port
    result = project_profile_v4(**source)
    assert not result.issues
    assert result.settings["notify"]["sinks"]["dashboard"]["port"] == port


@pytest.mark.parametrize("port", [-1, 65536, True, 1.5, "0"])
def test_dashboard_rejects_invalid_port(port):
    source = documents()
    source["notify"]["sinks"]["dashboard"]["port"] = port
    result = project_profile_v4(**source)
    assert any(issue.location == "notify.yaml.sinks.dashboard.port" for issue in result.issues)


@pytest.mark.parametrize("section,key,value", [
    ("debate", "min_interval_seconds", -1), ("debate", "min_interval_seconds", True),
    ("debate", "min_new_outcomes", 1.5), ("debate", "min_new_outcomes", -1),
    ("observer", "coalesce_reports", "true"),
])
def test_interaction_cadence_rejects_invalid_controls(section, key, value):
    source = documents()
    source["methodology"]["interaction"].setdefault(section, {})[key] = value
    result = project_profile_v4(**source)
    assert any(key in item.location for item in result.issues)


def test_interaction_cadence_is_preserved_in_admitted_profile():
    source = documents()
    controls = source["methodology"]["interaction"]
    controls["observer"] = {"coalesce_reports": True}
    controls["debate"].update(min_interval_seconds=1800, min_new_outcomes=2)
    result = project_profile_v4(**source)
    assert not result.issues
    assert result.settings["methodology"]["interaction"] == controls


def test_literature_credentials_file_requires_absolute_path():
    source = documents()
    source["methodology"]["literature"]["credentials_file"] = "/secrets/hypoloom.env"
    assert not project_profile_v4(**source).issues
    for bad in ("secrets/hypoloom.env", "literal-secret-value"):
        source = documents()
        source["methodology"]["literature"]["credentials_file"] = bad
        result = project_profile_v4(**source)
        assert any(item.location == "methodology.yaml.literature.credentials_file" for item in result.issues)


def test_telegram_credentials_file_and_env_exclusion():
    source = documents()
    del source["notify"]["sinks"]["telegram"]["bot_token_env"]
    del source["notify"]["sinks"]["telegram"]["chat_id_env"]
    source["notify"]["sinks"]["telegram"]["credentials_file"] = "/secrets/hypoloom.env"
    assert not project_profile_v4(**source).issues
    source["notify"]["sinks"]["telegram"]["bot_token_env"] = "TEST_BOT_TOKEN"
    result = project_profile_v4(**source)
    assert any("exactly one" in item.message and "credentials_file" in item.location for item in result.issues)
    source = documents()
    source["notify"]["sinks"]["telegram"]["credentials_file"] = "relative/hypoloom.env"
    result = project_profile_v4(**source)
    assert any(item.location == "notify.yaml.sinks.telegram.credentials_file" for item in result.issues)


def documents():
    return {
        "goal": {"title": "Improve a measured system", "description": "Experts choose the experiment and parser.",
                 "metric": None, "constraints": ["Keep source checkout read-only"],
                 "stop_conditions": {"max_rounds_per_expert": 4, "max_wall_clock_hours": 2}},
        "experts": {"defaults": {"model": "configured-model", "effort": "high", "max_forks": 4,
                                 "max_hypotheses_per_round": 3}, "experts": [
            {"name": "scientist", "role": "System researcher", "persona": "Question assumptions.",
             "focus": ["Measurements", "Reproducibility"], "stage_overrides": {
                 "inner_loop.code": {"model": "coding-model", "effort": "high"}}}]},
        "methodology": {"schema_version": 4, "runtime": {"target_repo": "/target-repo"},
                        "literature": {"enabled": True, "tools": ["web_search", "web_fetch"], "max_sources": 8},
                        "inner_loop": {"max_iterations_per_hypothesis": 5, "repair_attempts": 3},
                        "interaction": {"leader": {"enabled": True, "check_after": "cycle_report", "min_rounds_before_leader": 2},
                                        "debate": {"enabled": True, "rounds": 3}},
                        "graph_evolution": {"enabled": True, "stagnation_rounds": 2}},
        "language": {"prompt_language": "en", "report_language": "zh-CN", "code_comment_language": "en"},
        "notify": {"sinks": {"log": {"level": "info", "path": "events.jsonl"},
                             "telegram": {"enabled": True, "bot_token_env": "TEST_BOT_TOKEN", "chat_id_env": "TEST_CHAT_ID"},
                             "dashboard": {"enabled": True, "host": "127.0.0.1", "port": 7860}},
                   "events": {"telegram": ["experiment_complete", "leader_selected"]}},
        "safety": {"allow_read_paths": ["/dataset"], "gpus": "auto", "max_gpus_per_experiment": 1,
                   "experiment_timeout_minutes": 5, "per_expert_budget_usd": 10, "total_budget_usd": 20,
                   "bash": {"deny_patterns": ["sudo"], "network_allowed": False},
                   "filesystem": {"write_only_within_worktree": True, "protected_paths": ["/target-repo"]}},
    }


def test_node_runtime_selects_defaults_role_and_specific_stage_in_order():
    from hypoloom.profile_v4 import resolve_node_runtime
    settings = project_profile_v4(**documents()).settings
    settings["experts"]["experts"][0]["model"] = "role-model"
    settings["experts"]["experts"][0]["stage_overrides"]["hypothesis_proposal"] = {"effort": "medium"}
    settings["experts"]["experts"][0]["stage_overrides"]["hypothesis_proposal.synthesis"] = {"model": "synthesis-model"}
    contract = {"methodology": {"controls": {"product": settings}}}
    code = resolve_node_runtime(contract, "scientist", "experiment_design_implement")
    assert code["model"] == "coding-model" and code["effort"] == "high"
    assert code["code_comment_language"] == "en"
    assert code["safety"]["filesystem"] == settings["safety"]["filesystem"]
    assert code["literature"] == settings["methodology"]["literature"]
    synthesis = resolve_node_runtime(contract, "scientist", "hypothesis_proposal.synthesis")
    assert synthesis["model"] == "synthesis-model" and synthesis["effort"] == "medium"
    grounding = resolve_node_runtime(contract, "grounding-agent", "grounding")
    assert grounding["model"] == "configured-model"


def test_resolved_node_settings_are_actual_frozen_config_not_live_profile():
    import json
    from hypoloom.agent_aci import EmbeddedInput
    from hypoloom.profile_v4 import request_node_runtime
    item = EmbeddedInput.from_bytes("run--component--expert-config", "application/json", json.dumps({
        "node_runtime": {"model": "frozen-model", "effort": "high", "memory": {"max_transcript_bytes": 100000000}}}).encode())
    assert request_node_runtime((item,))["model"] == "frozen-model"
    assert request_node_runtime(()) == {}


def test_design_vocabulary_is_preserved_and_null_metric_is_explicitly_deferred():
    source = documents()
    before = copy.deepcopy(source)
    projected = project_profile_v4(**source)
    assert not projected.issues
    assert source == before
    criteria = projected.projection.methodology["success_criteria"]["primary"]
    assert criteria[0]["deferred"] is True
    assert projected.settings["experts"] == source["experts"]
    assert projected.settings["methodology"]["interaction"]["debate"]["rounds"] == 3
    assert projected.settings["notify"]["sinks"]["telegram"]["bot_token_env"] == "TEST_BOT_TOKEN"
    assert not projected.warnings
    assert projected.projection.methodology["budget"]["experiment_timeout"] == 300
    assert projected.projection.methodology["budget"]["total_budget_usd"] == 20
    assert projected.projection.methodology["budget"]["max_active_agents"] == 4
    assert not any("budget_usd" in item.location for item in projected.warnings)
    assert projected.safety["allow_read_paths"] == ["/dataset"]


def test_default_effect_capacity_matches_sum_of_per_expert_fork_limits():
    source = documents()
    source["experts"]["experts"].append({
        **copy.deepcopy(source["experts"]["experts"][0]), "name": "peer", "max_forks": 2})
    projection = project_profile_v4(**source)
    assert not projection.issues
    budget = projection.projection.methodology["budget"]
    assert {budget[name] for name in ("max_active_agents", "max_active_processes", "max_active_reviews")} == {6}
    source["methodology"]["budget"] = {"max_active_agents": 2, "max_active_processes": 1, "max_active_reviews": 3}
    budget = project_profile_v4(**source).projection.methodology["budget"]
    assert tuple(budget[name] for name in ("max_active_agents", "max_active_processes", "max_active_reviews")) == (2, 1, 3)
    source = documents()
    source["experts"]["defaults"].pop("max_forks")
    assert project_profile_v4(**source).projection.methodology["budget"]["max_active_agents"] == 3


def test_partial_metric_never_invents_an_operator_threshold():
    source = documents()
    source["goal"]["metric"] = {"name": "latency", "direction": "minimize"}
    result = project_profile_v4(**source)
    assert not result.issues
    criterion = result.projection.methodology["success_criteria"]["primary"][0]
    assert criterion["deferred"] is True
    assert criterion["metric"] == "latency"
    assert "target" not in criterion


@pytest.mark.parametrize("document,key,value", [
    ("safety", "total_budget_usd", -1), ("safety", "gpus", [0, 0]),
    ("methodology", "unknown_control", True), ("language", "report_language", "bad language"),
])
def test_profile_v4_rejects_unknown_or_invalid_values(document, key, value):
    source = documents()
    source[document][key] = value
    assert project_profile_v4(**source).issues


def test_notification_secrets_are_environment_names_not_literal_tokens():
    source = documents()
    source["notify"]["sinks"]["telegram"]["bot_token"] = "literal-secret"
    result = project_profile_v4(**source)
    assert any("bot_token" in issue.location for issue in result.issues)


@pytest.mark.parametrize("grounding,telegram,events,warn", [
    (True, True, None, True),
    (True, True, [], True),
    (True, True, ["grounding_answered"], True),
    (True, True, ["grounding_question"], False),
    (True, False, [], False),
    (True, None, [], False),
    (False, True, [], False),
])
def test_grounding_warns_when_enabled_telegram_does_not_subscribe_to_questions(
    grounding, telegram, events, warn,
):
    source = documents()
    if grounding:
        source["goal"]["evaluation"] = "grounding"
    if telegram is None:
        source["notify"]["sinks"].pop("telegram")
    else:
        source["notify"]["sinks"]["telegram"]["enabled"] = telegram
    if events is None:
        source["notify"].pop("events")
    else:
        source["notify"]["events"]["telegram"] = events
    result = project_profile_v4(**source)
    assert not result.issues
    assert result.settings["notify"] == source["notify"]
    warnings = [item for item in result.warnings if item.location == "notify.yaml.events.telegram"]
    assert bool(warnings) is warn
    if warn:
        assert len(warnings) == 1
        assert "grounding_question" in warnings[0].message
        assert "answer" in warnings[0].message


def test_six_yaml_admission_keeps_product_settings_and_visible_inactive_warnings(tmp_path: Path):
    profile, target, data = _materialize_expert_loop_pilot_v3(tmp_path)
    source = documents()
    source["methodology"]["interaction"]["leader"]["check_after"] = "result_analysis"
    source["methodology"]["runtime"]["target_repo"] = str(target)
    source["safety"]["allow_read_paths"] = [str(data)]
    for name, value in source.items():
        (profile / f"{name}.yaml").write_text(yaml.safe_dump(value), encoding="utf-8")
    report = admit_profile(profile, run_id="profile-v4-actual")
    assert report.admitted, report.to_dict()
    assert report.to_dict()["warnings"]
    contract = ResearchContract.from_mapping(report.create_run_payload["research_contract"])
    assert contract.components[0].deferred_measurement
    assert contract.document["budget"]["total_budget_usd"] == "20"
    assert contract.document["methodology"]["controls"]["product"]["profile_version"] == 4
    changed = contract.to_dict()
    changed["methodology"]["controls"]["product"]["safety"]["total_budget_usd"] = -3
    with pytest.raises(ValueError): ResearchContract.from_mapping(changed)


@pytest.mark.parametrize("model", ["scripted-exact-model", "REPLACE_WITH_PINNED_MODEL"])
def test_tracked_three_expert_v4_template_admits_without_starting_work(tmp_path, model):
    import sys
    template = Path(__file__).resolve().parents[2] / "profiles/templates/multi-expert-en"
    expected = {name + ".yaml" for name in ("goal", "experts", "methodology", "language", "notify", "safety")}
    assert {item.name for item in template.iterdir()} == expected
    profile, target, data = _materialize_expert_loop_pilot_v3(tmp_path)
    source = {path.stem: yaml.safe_load(path.read_text()) for path in template.iterdir()}
    source["methodology"]["runtime"].update(target_repo=str(target), conda_env=sys.prefix)
    source["safety"]["allow_read_paths"] = [str(data)]
    source["experts"]["defaults"]["model"] = model
    for name, value in source.items(): (profile / (name + ".yaml")).write_text(yaml.safe_dump(value))
    report = admit_profile(profile, run_id="three-expert-v4-starter")
    if model == "REPLACE_WITH_PINNED_MODEL":
        assert not report.admitted and report.create_run_payload is None
        assert any(item.location == "experts.yaml.defaults.model" for item in report.blockers)
        assert not list(tmp_path.rglob("state.sqlite"))
        return
    assert report.admitted, report.blockers
    contract = ResearchContract.from_mapping(report.create_run_payload["research_contract"])
    settings = contract.document["methodology"]["controls"]["product"]
    assert len(settings["experts"]["experts"]) == 3
    assert settings["experts"]["defaults"]["max_forks"] == 2
    assert settings["methodology"]["interaction"]["debate"]["rounds"] == 3
    assert tuple(settings["safety"]["gpus"]) == ()
    assert not list(tmp_path.rglob("state.sqlite"))


@pytest.mark.parametrize("scope", ["defaults", "expert", "stage", "grounding"])
def test_model_validation_covers_each_profile_override(scope):
    source = documents()
    expert = source["experts"]["experts"][0]
    selected = source["experts"]["defaults"] if scope == "defaults" else (
        expert if scope == "expert" else expert["stage_overrides"]["inner_loop.code"])
    selected["model"] = "REPLACE_WITH_PINNED_MODEL"
    if scope == "grounding":
        source["experts"]["grounding"] = {"model": "REPLACE_WITH_PINNED_GROUNDING_MODEL"}
        del selected["model"]
    result = project_profile_v4(**source)
    assert any(item.location.endswith(".model") for item in result.issues)


@pytest.mark.parametrize("selectors,expected", [
    ("auto", ("a", "b")), ([], ()), ([0], ("b",)), ([2], ("a",)),
    (["GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"], ("b",)),
    ([2, "GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"], ("a", "b")),
])
def test_profile_gpu_selection_only_narrows_host_authorized_snapshot(selectors, expected):
    from hypoloom.compute_resources import ComputeCapabilities
    from hypoloom.profile_v4 import restrict_profile_capabilities
    from test_compute_resources import GPU_A, GPU_B
    source = documents()
    source["safety"]["gpus"] = selectors
    settings = project_profile_v4(**source).settings
    contract = {"methodology": {"controls": {"product": settings}}}
    before = copy.deepcopy(contract)
    captured = ComputeCapabilities(gpus=(GPU_A, GPU_B), busy_gpu_uuids=(GPU_B.uuid,))
    result = restrict_profile_capabilities(contract, captured)
    assert tuple(gpu.uuid[4] for gpu in result.gpus) == expected
    assert result.busy_gpu_uuids == ((GPU_B.uuid,) if "b" in expected else ())
    assert contract == before and len(captured.gpus) == 2
    assert ComputeCapabilities.from_bytes(result.to_bytes()) == result
    assert restrict_profile_capabilities({}, captured) is captured


@pytest.mark.parametrize("selectors,match", [
    ([1], "not in the Host-authorized"),
    (["GPU-cccccccc-cccc-cccc-cccc-cccccccccccc"], "not in the Host-authorized"),
    ([2, "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"], "same GPU"),
])
def test_profile_gpu_selection_never_discovers_or_authorizes_another_device(selectors, match):
    from hypoloom.compute_resources import ComputeCapabilities
    from hypoloom.profile_v4 import restrict_profile_capabilities
    from test_compute_resources import GPU_A, GPU_B
    contract = {"methodology": {"controls": {"product": {"safety": {"gpus": selectors}}}}}
    with pytest.raises(ValueError, match=match):
        restrict_profile_capabilities(contract, ComputeCapabilities(gpus=(GPU_A, GPU_B)))


@pytest.mark.parametrize("selectors", [[True], [-1], [""], ["0"], ["GPU-"], ["not-a-uuid"]])
def test_profile_gpu_identifiers_are_indices_or_stable_physical_uuids(selectors):
    source = documents()
    source["safety"]["gpus"] = selectors
    assert any(issue.location == "safety.yaml.gpus" for issue in project_profile_v4(**source).issues)


@pytest.mark.parametrize("version,command,settings,expected", [
    (13, "LITERATURE_REVIEW", {}, ()), (14, "RESULT_ANALYSIS", {}, ()),
    (14, "LITERATURE_REVIEW", {}, ("web_search", "web_fetch")),
    (14, "LITERATURE_REVIEW", {"enabled": False}, ()),
    (14, "LITERATURE_REVIEW", {"tools": []}, ()),
    (14, "LITERATURE_REVIEW", {"tools": ["web_fetch"]}, ("web_fetch",)),
])
def test_literature_web_access_is_bound_to_the_frozen_node_settings(version, command, settings, expected):
    import json
    from types import SimpleNamespace
    from hypoloom.agent_aci import AgentCommand, EmbeddedInput
    from hypoloom.profile_v4 import request_literature_tools
    from hypoloom.fake_agent import default_request
    item = EmbeddedInput.from_bytes("node--expert-config", "application/json",
        json.dumps({"node_runtime": {"literature": settings}}).encode())
    request = SimpleNamespace(command=AgentCommand(command), schema_version=version, input_artifacts=(item,),
        task_context=default_request(AgentCommand.LITERATURE_REVIEW).task_context)
    assert request_literature_tools(request) == expected


def test_grounding_query_plan_does_not_acquire_the_experts_literature_mcp():
    from types import SimpleNamespace
    from hypoloom.agent_aci import AgentCommand
    from hypoloom.expert_loop import AuxiliaryTask, AuxiliaryRequestKind
    from hypoloom.profile_v4 import request_literature_tools
    request = SimpleNamespace(command=AgentCommand.LITERATURE_REVIEW, schema_version=14,
        input_artifacts=(), task_context=AuxiliaryTask(kind=AuxiliaryRequestKind.GROUNDING_QUERY_PLAN, run_id="run-fake"))
    assert request_literature_tools(request) == ()
