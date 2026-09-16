"""Persistent management is distinct from fresh, stateless Observer sessions."""
from dataclasses import replace
import json

import pytest

from hypoloom.agent_aci import EmbeddedInput, validate_postflight, with_output_digest
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import AuxiliaryTask, AuxiliaryRequestKind
from hypoloom.expert_plugin import ExpertPluginStore
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.manager_context import admit_graph_evolution
from test_agent_sessions import _manager


def _request(cycle=1):
    task = AuxiliaryTask(kind=AuxiliaryRequestKind.GRAPH_EVOLVE, run_id="run-fake", cycle=cycle)
    context = EmbeddedInput.from_bytes("graph--manager-context", "application/json", json.dumps({
        "max_hypotheses_per_round": {"scientist": 3, "peer": 2},
        "effective_max_forks": {"scientist": 1, "peer": 1},
        "source_report_refs": ["actual-report--response"], "enabled": True,
    }).encode())
    return replace(default_request(AgentCommand.GRAPH_EVOLVE), command_id=task.command_id,
        task_context=task, input_artifacts=(context,), immutable_context_refs=(context.ref,))


@pytest.mark.parametrize("changes", [[], [{"role": "scientist", "effective_max_forks": 3, "reason": "Explore a new family."}]])
def test_manager_graph_changes_are_admitted_against_exact_frozen_role_limits(changes):
    request = _request()
    assert [item.to_dict() for item in admit_graph_evolution(changes, request)] == changes
    response = FakeAgent().invoke(request)
    raw = {**response.typed_candidate, "graph_evolution": changes}
    parsed = validate_postflight(request, with_output_digest(replace(response, typed_candidate=raw)))
    assert [item.to_dict() for item in parsed.candidate.graph_evolution] == changes


@pytest.mark.parametrize("role,limit", [("not-admitted", 1), ("scientist", 4), ("peer", 3),
    ("scientist", 0), ("scientist", -1), ("scientist", True), ("scientist", 1.5)])
def test_manager_rejects_invalid_fork_changes(role, limit):
    with pytest.raises(ValueError):
        admit_graph_evolution([{"role": role, "effective_max_forks": limit, "reason": "Invalid."}], _request())


def test_manager_rejects_duplicate_expert_and_disabled_changes():
    change = {"role": "scientist", "effective_max_forks": 2, "reason": "Explore."}
    with pytest.raises(ValueError, match="duplicate"):
        admit_graph_evolution([change, change], _request())
    request = _request()
    raw = json.loads(request.input_artifacts[0].content_bytes()); raw["enabled"] = False
    request = replace(request, input_artifacts=(EmbeddedInput.from_bytes("graph--manager-context", "application/json", json.dumps(raw).encode()),))
    with pytest.raises(ValueError, match="disabled"): admit_graph_evolution([change], request)
    assert admit_graph_evolution([], request) == ()


def _with_plugin(manager, request):
    store = ExpertPluginStore(manager.state_root, owner_kind="grounding")
    plugin = store.ensure(request.run_id, "grounding", "Observe actual shared-goal progress.")
    item = EmbeddedInput.from_bytes("graph--grounding-resources", "application/json",
        json.dumps({"plugin": plugin.to_dict()}).encode())
    request = replace(request, input_artifacts=(*request.input_artifacts, item),
        immutable_context_refs=(*request.immutable_context_refs, item.ref))
    return store, plugin, request


def test_manager_resumes_across_cycle_and_host_reconstruction_with_grounding_plugin_only(tmp_path):
    manager = _manager(tmp_path)
    store, plugin, first_request = _with_plugin(manager, _request())
    first = manager.prepare(first_request, None)
    assert first.binding["scope"] == "grounding-manager"
    assert first.scope_root == manager.state_root / "run-fake/grounding/sessions/grounding-manager"
    assert not first.writable and first.memory_root is None
    assert first.plugin_update.owner_kind == "grounding"
    from pathlib import Path
    from hypoloom.full_cc_runtime import full_cc_argv
    argv = full_cc_argv(bwrap=Path("/usr/bin/bwrap"), executable=tmp_path / "claude", request=first_request,
        model="exact-model", workspace=first, provider_socket=tmp_path / "provider.sock", host_socket=None,
        output_schema={}, extra_paths=())
    assert ("--bind", str(first.plugin_update.root), "/plugin-update") in set(zip(argv, argv[1:], argv[2:]))
    path = first.session_root / f"{first.binding['session_id']}.jsonl"
    path.write_text(json.dumps({"type": "user", "sessionId": first.binding["session_id"], "message": "Previous graph observation."}) + "\n")
    updated = first.plugin_update.root / "skills/leader-trigger/SKILL.md"
    updated.write_bytes(updated.read_bytes() + b"\nOnly cite actual accepted progress.\n")
    accepted = store.accept_request_update(first_request)
    assert accepted["commit"] != plugin.commit
    first.cleanup()
    restarted = type(manager)(manager.state_root, manager.publisher)
    _, _, second_request = _with_plugin(restarted, replace(_request(2), attempt_id="manager-second"))
    second = restarted.prepare(second_request, None)
    try:
        assert second.binding["session_id"] == first.binding["session_id"]
        assert second.session_arguments == ("--resume", first.binding["session_id"])
        assert b"Only cite actual accepted progress." in (second.plugin_root / "skills/leader-trigger/SKILL.md").read_bytes()
        ordinary = ExpertPluginStore(manager.state_root).ensure("run-fake", "grounding", "An expert, not the manager.")
        assert ordinary.owner_kind == "expert" and ordinary.commit != accepted["commit"]
    finally: second.cleanup()


def test_observer_is_fresh_each_trigger_and_cannot_resume_manager_session():
    from hypoloom.agent_sessions import AgentSessions
    first = default_request(AgentCommand.OBSERVER)
    task = replace(first.task_context, ordinal=2)
    second = replace(first, task_context=task, command_id=task.command_id)
    assert AgentSessions.binding(first)["session_id"] != AgentSessions.binding(second)["session_id"]
    assert AgentSessions.binding(_request())["session_id"] not in {
        AgentSessions.binding(first)["session_id"], AgentSessions.binding(second)["session_id"]}
