"""Profile controls reach the existing request inputs, not a second authority."""

import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.domain import ResearchContract
from hypoloom.expert_loop import (
    AuxiliaryRequestKind, AuxiliaryTask, ResearchNodeKey, ResearchNodeKind,
    ResearchNodeSubkind, ResearchNodeTask,
)
from hypoloom.profile_language import apply_profile_language
from hypoloom.profile_v4 import project_profile_v4
from test_plugin_host import ROLE, RUN
from test_plugin_host import host as host
from test_expert_loop_host import mount_root as mount_root
from test_profile_v4 import documents


def _contract():
    settings = project_profile_v4(**documents()).settings
    overrides = settings["experts"]["experts"][0]["stage_overrides"]
    overrides.update({"hypothesis_proposal": {"effort": "medium"},
        "hypothesis_proposal.synthesis": {"model": "synthesis-model"},
        "leader": {"model": "leader-model", "effort": "max"}})
    return {"goal": {"language": "en"}, "methodology": {"controls": {
        "product": settings, "language_routes": {"overrides": {}}}}}


def _synthesis(role="scientist"):
    return ResearchNodeTask(key=ResearchNodeKey(run_id=RUN, component_id="must.primary",
        expert_slot=role, cycle=1, node_kind=ResearchNodeKind.HYPOTHESIS_PROPOSAL,
        subkind=ResearchNodeSubkind.SYNTHESIS))


def test_designated_leader_overlay_preserves_actual_synthesis_language_route():
    contract = _contract()
    before = copy.deepcopy(contract)
    ordinary, leader = {}, {}
    apply_profile_language(ordinary, contract, _synthesis())
    apply_profile_language(leader, contract, _synthesis(), designated_leader=True)
    assert ordinary["node_runtime"]["model"] == "synthesis-model"
    assert ordinary["node_runtime"]["effort"] == "medium"
    assert leader["node_runtime"]["model"] == "leader-model"
    assert leader["node_runtime"]["effort"] == "max"
    assert leader["language_route"] == ordinary["language_route"] == {
        "route": "hypothesis_proposal.synthesis", "output_language": "en"}
    assert contract == before
    with pytest.raises(ValueError, match="SYNTHESIS"):
        apply_profile_language({}, contract, replace(_synthesis(), key=replace(
            _synthesis().key, subkind=ResearchNodeSubkind.FORWARD)), designated_leader=True)


@pytest.mark.parametrize("kind,route", [
    (ResearchNodeKind.ADJUDICATE, "adjudicate"),
    (ResearchNodeKind.SELF_EVOLVE, "self_evolve"),
    (AuxiliaryRequestKind.GRAPH_EVOLVE, "graph_evolve"),
    (AuxiliaryRequestKind.OBSERVER, "observer"),
    (AuxiliaryRequestKind.DEBATE, "debate"),
    (AuxiliaryRequestKind.DEBATE_JUDGE, "debate_judge"),
    (AuxiliaryRequestKind.FINAL_REPORT, "final_report"),
])
def test_v3_default_language_reaches_new_aci14_routes(kind, route):
    contract = {"goal": {"language": "zh-CN"}, "methodology": {
        "controls": {"language_routes": {"overrides": {"hypothesis_update": "en"}}}}}
    if isinstance(kind, ResearchNodeKind):
        task = ResearchNodeTask(key=replace(_synthesis().key, node_kind=kind,
            subkind=ResearchNodeSubkind.MAIN))
    else:
        task = AuxiliaryTask(kind=kind, run_id=RUN,
            **({"cycle": 1} if kind is AuxiliaryRequestKind.GRAPH_EVOLVE else {}))
    config = {}
    apply_profile_language(config, contract, task)
    assert config == {"language_route": {"route": route, "output_language": "zh-CN"}}


def test_existing_v3_route_keeps_its_specific_override():
    contract = {"goal": {"language": "zh-CN"}, "methodology": {
        "controls": {"language_routes": {"overrides": {"hypothesis_update": "en"}}}}}
    task = ResearchNodeTask(key=replace(_synthesis().key,
        node_kind=ResearchNodeKind.HYPOTHESIS_UPDATE, subkind=ResearchNodeSubkind.MAIN))
    config = {}
    apply_profile_language(config, contract, task)
    assert config == {"language_route": {"route": "hypothesis_update", "output_language": "en"}}


@pytest.mark.parametrize("kind,language_route", [
    (AuxiliaryRequestKind.GROUNDING_QUERY_PLAN, "literature_review.query_plan"),
    (AuxiliaryRequestKind.GROUNDING, "grounding"),
])
def test_step_one_runtime_override_does_not_rename_the_existing_language_route(kind, language_route):
    contract = _contract()
    contract["methodology"]["controls"]["product"]["experts"]["grounding"] = {
        "model": "shared-grounding", "effort": "low"}
    contract["methodology"]["controls"]["language_routes"]["overrides"] = {
        "literature_review.query_plan": "zh-CN"}
    config = {}
    apply_profile_language(config, contract, AuxiliaryTask(kind=kind, run_id=RUN))
    assert config["node_runtime"]["model"] == "shared-grounding"
    assert config["node_runtime"]["effort"] == "low"
    assert config["language_route"] == {"route": language_route,
        "output_language": "zh-CN" if kind is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN else "en"}


def test_implemented_product_fields_no_longer_claim_pending_consumers():
    source = documents()
    source["methodology"]["memory"] = {"retention": "archive_on_close"}
    source["experts"]["experts"][0]["stage_overrides"].update({
        stage: {"effort": "high"} for stage in ("self_evolve", "debate", "leader")})
    projected = project_profile_v4(**source)
    assert not projected.issues
    assert not projected.warnings


def test_nondefault_trigger_and_stricter_filesystem_are_precise_visible_warnings():
    source = documents()
    source["methodology"]["interaction"]["leader"]["check_after"] = "result_analysis"
    source["safety"]["filesystem"]["write_only_within_worktree"] = False
    projected = project_profile_v4(**source)
    assert not projected.issues
    warnings = {item.location: item for item in projected.warnings}
    assert set(warnings) == {"methodology.yaml.interaction.leader.check_after",
        "safety.yaml.filesystem.write_only_within_worktree"}
    trigger = warnings["methodology.yaml.interaction.leader.check_after"]
    assert trigger.code == "inactive_feature"
    assert "cycle_report" in trigger.message and "result_analysis" in trigger.message
    assert "not applied" in trigger.message
    filesystem = warnings["safety.yaml.filesystem.write_only_within_worktree"]
    assert "stricter" in filesystem.message and "does not grant" in filesystem.message


def test_observer_override_is_an_implemented_consumer_without_pending_warning():
    source = documents()
    source["experts"]["experts"][0]["stage_overrides"]["observer"] = {"model": "private-observer"}
    projected = project_profile_v4(**source)
    assert not projected.issues
    assert not projected.warnings


@pytest.mark.parametrize("role,expected_model,expected_effort", [
    ("scientist", "observer-a", "max"), ("peer", "observer-b", "high"),
])
def test_observer_uses_only_its_trigger_owner_stage_override(role, expected_model, expected_effort):
    contract = _contract()
    settings = contract["methodology"]["controls"]["product"]
    first = settings["experts"]["experts"][0]
    first.update(model="private-default-a", effort="low", persona="PRIVATE_PERSONA_A")
    first["stage_overrides"]["observer"] = {"model": "observer-a", "effort": "max"}
    settings["experts"]["experts"].append({"name": "peer", "model": "private-default-b",
        "effort": "low", "persona": "PRIVATE_PERSONA_B", "stage_overrides": {"observer": {"model": "observer-b"}}})
    before = copy.deepcopy(contract)
    task = AuxiliaryTask(kind=AuxiliaryRequestKind.OBSERVER, run_id=RUN, cycle=1)
    config = {}
    apply_profile_language(config, contract, task, observer_source_role=role)
    assert config["node_runtime"]["model"] == expected_model
    assert config["node_runtime"]["effort"] == expected_effort
    assert config["language_route"] == {"route": "observer", "output_language": "en"}
    assert "PRIVATE_PERSONA" not in json.dumps(config) and "private-default" not in json.dumps(config)
    assert not {"role", "expert_slot", "expert", "persona", "memory_commit"} & set(config)
    assert task.expert_slot is None and task.component_id is None
    assert contract == before


@pytest.mark.parametrize("source_index,model,effort", [(0, "observer-a", "max"), (1, "observer-b", "high")])
def test_host_observer_binds_override_to_exact_report_owner_without_private_context(host, monkeypatch, source_index, model, effort):
    contract = _profile_host(host, monkeypatch).to_dict()
    contract["roles"].append({**copy.deepcopy(contract["roles"][0]), "id": "peer"})
    contract["components"][1].update(owner="peer", proposers=["peer"])
    settings = contract["methodology"]["controls"]["product"]
    expert = settings["experts"]["experts"][0]
    expert.update(model="PRIVATE_GENERAL_MODEL_A", effort="low", persona="PRIVATE_PERSONA_A")
    expert["stage_overrides"]["observer"] = {"model": "observer-a", "effort": "max"}
    settings["experts"]["experts"].append({**copy.deepcopy(expert), "name": "peer",
        "model": "PRIVATE_GENERAL_MODEL_B", "persona": "PRIVATE_PERSONA_B",
        "stage_overrides": {"observer": {"model": "observer-b"}}})
    settings["methodology"]["interaction"] = {"leader": {"enabled": False}, "debate": {"enabled": False}}
    typed = ResearchContract.from_mapping(contract)
    monkeypatch.setattr(host, "_research_contract", lambda _run: typed)
    reports, rows = [], []
    for index, role in enumerate((ROLE, "peer")):
        report = ResearchNodeTask(key=ResearchNodeKey(run_id=RUN, component_id=contract["components"][index]["id"],
            expert_slot=role, cycle=1, node_kind=ResearchNodeKind.CYCLE_REPORT))
        reports.append(({"attempt_id": "accepted-report-" + role, "ended_at": 100.0 + index}, {"command_id": report.command_id,
            "role": role, "component_id": report.key.component_id, "task_context": report.to_dict()},
            {"response": {"summary": "Accepted scientific report.", "typed_candidate": {"supporting_refs": []}}}))
        rows.append({"command_type": "REQUEST_RESEARCH_NODE", "command_id": report.command_id, "revision": 7 + index})
    monkeypatch.setattr(host.interaction, "_reports", lambda _run: reports)
    monkeypatch.setattr(host.interaction, "_rows", lambda _run: rows)
    monkeypatch.setattr(host.interaction, "_closed", lambda *_args: True)
    monkeypatch.setattr(host, "_expert_success", lambda _task: {"attempt_id": "grounding-source",
        "observation_json": json.dumps({"response": {"summary": "Shared Step-1 background."}})})
    plugin_calls = []

    def plugin_binding(*args, **kwargs):
        plugin_calls.append((args, kwargs))
        return SimpleNamespace(to_dict=lambda: {"owner_kind": "grounding", "role": "grounding", "commit": "a" * 40})

    monkeypatch.setattr(host, "_plugin_binding", plugin_binding)
    task = AuxiliaryTask(kind=AuxiliaryRequestKind.OBSERVER, run_id=RUN, cycle=1, ordinal=7 + source_index)
    inputs = host.interaction.inputs(RUN, task)
    config = json.loads(next(item.content_bytes() for item in inputs if item.ref.endswith("--grounding-config")))
    context = json.loads(next(item.content_bytes() for item in inputs if item.ref.endswith("--interaction-context")))
    assert config["node_runtime"]["model"] == model
    assert config["node_runtime"]["effort"] == effort
    assert context["source_report_ref"] == reports[source_index][0]["attempt_id"] + "--response"
    assert config["grounding_scope"] == "role-neutral"
    assert not {"expert", "expert_slot", "persona", "memory_commit"} & set(config)
    assert not any("--expert-resources" in item.ref or "--leader-resources" in item.ref for item in inputs)
    assert "PRIVATE_" not in "\n".join(item.content_bytes().decode() for item in inputs)
    assert task.expert_slot is None and task.component_id is None
    assert plugin_calls[0][0][2] == "grounding" and plugin_calls[0][1]["owner_kind"] == "grounding"


def _profile_host(host, monkeypatch):
    operational = host._run_config(RUN)
    monkeypatch.setattr(host, "_run_config", lambda _run: operational)
    document = host._research_contract(RUN).to_dict()
    document["goal"]["language"] = "en"
    settings = _contract()["methodology"]["controls"]["product"]
    settings["experts"]["experts"][0]["name"] = ROLE
    document["methodology"]["controls"].update(product=settings,
        language_routes={"overrides": {}})
    contract = ResearchContract.from_mapping(document)
    monkeypatch.setattr(host, "_research_contract", lambda _run: contract)
    monkeypatch.setattr(host, "_expert_response_input", lambda task:
        EmbeddedInput.from_bytes(task.command_id + "--response", "application/json", b"{}"))
    monkeypatch.setattr(host, "_expert_dependency_update_inputs", lambda *_args: ())
    monkeypatch.setattr(host, "_batch_history", lambda *_args: SimpleNamespace(latest=None))
    monkeypatch.setattr(host, "_cycle_reports_enabled", lambda _run: False)
    return contract


@pytest.mark.parametrize("designated", [False, True])
def test_host_freezes_leader_runtime_from_the_same_actual_peer_memory_input(host, monkeypatch, designated):
    _profile_host(host, monkeypatch)
    component = str(host._research_contract(RUN).components[0].component_id)
    task = replace(_synthesis(ROLE), key=replace(_synthesis(ROLE).key, component_id=component))
    item = EmbeddedInput.from_bytes(task.command_id + "--leader-resources", "application/json", b"{}")
    calls = []

    def leader_inputs(run_id, actual):
        calls.append((run_id, actual))
        return (item,) if designated else ()

    monkeypatch.setattr(host.interaction, "leader_inputs", leader_inputs)
    monkeypatch.setattr(host.interaction.debate, "promotion_inputs", lambda *_args: ())
    inputs = host._expert_inputs(RUN, task, component, ROLE, None)
    config = json.loads(next(value.content_bytes() for value in inputs if value.ref.endswith("--expert-config")))
    assert config["node_runtime"]["model"] == ("leader-model" if designated else "synthesis-model")
    assert config["node_runtime"]["effort"] == ("max" if designated else "medium")
    assert calls == [(RUN, task)]
    assert (item in inputs) is designated


def test_manager_receives_actual_stagnation_settings_as_advice_not_a_dispatch_gate(host, monkeypatch):
    contract = _profile_host(host, monkeypatch)
    component = str(contract.components[0].component_id)
    report = ResearchNodeTask(key=ResearchNodeKey(run_id=RUN, component_id=component,
        expert_slot=ROLE, cycle=1, node_kind=ResearchNodeKind.CYCLE_REPORT))
    task = AuxiliaryTask(kind=AuxiliaryRequestKind.GRAPH_EVOLVE, run_id=RUN, cycle=1, ordinal=7)
    monkeypatch.setattr(host.store, "inspect", lambda *_args: [{
        "revision": 7, "payload_json": json.dumps({"task_context": report.to_dict()})}])
    monkeypatch.setattr(host, "_plugin_binding", lambda *_args, **_kwargs:
        SimpleNamespace(to_dict=lambda: {"commit": "a" * 40}))
    inputs = host._manager_inputs(RUN, task)
    context = json.loads(next(value.content_bytes() for value in inputs if value.ref.endswith("--manager-context")))
    assert context["settings"] == {"enabled": True, "stagnation_rounds": 2}
    assert "2" in context["stagnation_guidance"]
    assert "advisory" in context["stagnation_guidance"]
    assert "scientific" in context["stagnation_guidance"]
    assert context["source_report_refs"] == [report.command_id + "--response"]
    dispatched = []
    monkeypatch.setattr(host, "_expert_success", lambda value: {} if value == report else None)
    monkeypatch.setattr(host, "_expert_task_final_failure", lambda *_args: None)
    monkeypatch.setattr(host, "_expert_task_exhausted", lambda *_args: False)
    monkeypatch.setattr(host, "_expert_agent_step", lambda *args: dispatched.append(args))
    assert host._ensure_graph_evolution(report) is False
    assert len(dispatched) == 1 and dispatched[0][2] == task


def test_real_cpu_v3_profile_on_fresh_aci16_keeps_language_through_final_report(tmp_path, mount_root):
    from hypoloom.agent_aci import AgentRequest
    from hypoloom.control_protocol import ControlRequest
    from hypoloom.oracle import Oracle
    from hypoloom.workflow_host import WorkflowHost
    from test_expert_loop_host import _drive, _pilot_payload_v3

    run_id = "v3-current-language"
    payload = _pilot_payload_v3(tmp_path, mount_root, run_id)
    instance = WorkflowHost(tmp_path / "state")
    try:
        instance.initialize()
        created = instance.handle(ControlRequest(schema_version=1, request_id="create-v3-current",
            expected_revision=0, command="CREATE_RUN", payload=payload))
        assert created.status == "accepted", created
        assert str(instance.store.get_run(run_id)["agent_aci_version"]) == "20"
        _drive(instance, run_id)
        assert instance.store.latest_transition(run_id)["state"] == "COMPLETED"
        attempts = instance.store.list_attempts()
        languages = []
        for attempt in attempts:
            assert attempt["state"] == "CLOSED" and attempt["terminal_reason"] == "SUCCEEDED"
            if attempt["kind"] != "AGENT":
                continue
            request = AgentRequest.from_dict(json.loads(attempt["observation_json"])["request"])
            config = json.loads(next(item.content_bytes() for item in request.input_artifacts
                if item.ref.endswith(("--expert-config", "--grounding-config"))))
            language = config["language_route"]
            assert language["output_language"] == ("zh-CN" if request.command.value == "HYPOTHESIS_UPDATE" else "en")
            languages.append({"attempt_id": attempt["attempt_id"], "command": request.command.value, **language})
        assert {"ADJUDICATE", "SELF_EVOLVE", "FINAL_REPORT"} <= {item["command"] for item in languages}
        assert sum(item["kind"] == "PROCESS" for item in attempts) == 1
        report = Oracle(instance.store.database_path, instance.publisher.root).audit(run_id)
        (tmp_path / "language-consumers.json").write_text(json.dumps(languages, indent=2))
        (tmp_path / "oracle.json").write_text(report.to_json())
        assert report.ok, report.to_dict()
    finally:
        instance.close()
