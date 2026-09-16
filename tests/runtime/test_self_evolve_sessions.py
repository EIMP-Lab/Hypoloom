"""Actual plugin worktree acceptance is separate from the inline response."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from hypoloom.agent_aci import EmbeddedInput, validate_postflight
from hypoloom.domain import AgentCommand
from hypoloom.expert_memory import ExpertMemory
from hypoloom.expert_plugin import ExpertPluginStore
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.full_cc_runtime import full_cc_argv
from hypoloom.git_authoring_workspace import GitAuthoringIntegrityError
from test_agent_sessions import _manager
from test_git_authoring_workspace import make_target


def _setup(tmp_path):
    manager = _manager(tmp_path)
    store = ExpertPluginStore(manager.state_root)
    request = default_request(AgentCommand.SELF_EVOLVE)
    plugin = store.ensure(request.run_id, request.role, "Use observed repairs.")
    memory = ExpertMemory(manager.state_root).ensure(request.run_id, request.role)
    inputs = (
        EmbeddedInput.from_bytes("node--expert-config", "application/json", json.dumps({
            "node_runtime": {"memory": {"max_plugin_bytes": 512 * 1024}}}).encode()),
        EmbeddedInput.from_bytes("node--expert-resources", "application/json", json.dumps({
            "plugin": plugin.to_dict(), "memory_commit": memory}).encode()),
    )
    request = replace(request, input_artifacts=inputs, immutable_context_refs=tuple(item.ref for item in inputs))
    _, target = make_target(tmp_path)
    return manager, store, request, plugin, target


def test_self_evolve_has_only_separate_plugin_write_mount_and_keeps_baseline(tmp_path):
    manager, store, request, plugin, target = _setup(tmp_path)
    prepared = manager.prepare(request, target)
    try:
        assert not prepared.writable
        assert prepared.plugin_update.base_commit == plugin.commit
        argv = full_cc_argv(bwrap=Path("/usr/bin/bwrap"), executable=tmp_path / "claude",
            request=request, model="exact-model", workspace=prepared,
            provider_socket=tmp_path / "provider.sock", host_socket=None, output_schema={}, extra_paths=())
        triples = set(zip(argv, argv[1:], argv[2:]))
        assert ("--bind", str(prepared.plugin_update.root), "/plugin-update") in triples
        assert ("--ro-bind", str(prepared.plugin_update.root / ".git"), "/plugin-update/.git") in triples
        for source, destination in ((prepared.worktree_root, "/workspace"),
                (prepared.plugin_root, "/expert-plugin"), (prepared.memory_root, "/memory")):
            assert ("--ro-bind", str(source), destination) in triples
        original = (prepared.plugin_root / "skills/experiment-repair/SKILL.md").read_bytes()
        changed = prepared.plugin_update.root / "skills/experiment-repair/SKILL.md"
        changed.write_bytes(original + b"\nCheck the actual failing test before retrying.\n")
        assert store.ensure(request.run_id, request.role, "ignored").commit == plugin.commit
        accepted = store.accept_request_update(request)
        assert accepted["base_commit"] == plugin.commit and accepted["commit"] != plugin.commit
        assert (prepared.plugin_root / "skills/experiment-repair/SKILL.md").read_bytes() == original
        assert store.accept_request_update(replace(request, attempt_id="retry", attempt_ordinal=2)) == accepted
    finally: prepared.cleanup()


def test_invalid_self_evolve_update_is_repaired_in_same_logical_worktree(tmp_path):
    manager, store, request, plugin, target = _setup(tmp_path)
    first = manager.prepare(request, target)
    path = first.plugin_update.root / "skills/experiment-repair/SKILL.md"
    original = path.read_bytes()
    path.write_text("not valid frontmatter")
    with pytest.raises(ValueError, match="frontmatter"):
        store.accept_request_update(request)
    assert store.ensure(request.run_id, request.role, "ignored").commit == plugin.commit
    first.cleanup()
    retry = manager.prepare(replace(request, attempt_id="retry", attempt_ordinal=2), target)
    try:
        assert retry.binding["session_id"] == first.binding["session_id"]
        assert retry.plugin_update.root == first.plugin_update.root
        assert path.read_text() == "not valid frontmatter"
        path.write_bytes(original + b"\nA real repair lesson.\n")
        assert store.accept_request_update(retry.request)["commit"] != plugin.commit
    finally: retry.cleanup()


def test_plugin_directory_budget_is_not_the_inline_response_budget(tmp_path):
    manager, store, request, plugin, target = _setup(tmp_path)
    prepared = manager.prepare(request, target)
    try:
        reference = prepared.plugin_update.root / "skills/experiment-repair/references/large.txt"
        reference.parent.mkdir()
        reference.write_bytes(b"x" * (request.budget.max_output_bytes + 1))
        response = validate_postflight(request, FakeAgent().invoke(request))
        assert not response.workspace_files
        assert store.accept_request_update(request)["commit"] != plugin.commit
    finally: prepared.cleanup()


def test_self_evolution_without_frozen_resources_fails_before_launch(tmp_path):
    manager = _manager(tmp_path)
    with pytest.raises(GitAuthoringIntegrityError, match="frozen plugin"):
        manager.prepare(default_request(AgentCommand.SELF_EVOLVE), None)
