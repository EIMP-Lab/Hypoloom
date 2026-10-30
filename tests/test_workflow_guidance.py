"""Bound performance guidance reaches the prompt without a model's Read call."""
from dataclasses import replace
import json
import shutil

import pytest

from hypoloom.agent_aci import EmbeddedInput, canonical_json, required_output_schema
from hypoloom.claude_agent_host import _prompt
from hypoloom.domain import AgentCommand
from hypoloom.expert_plugin import ExpertPluginStore, SEED_ROOT
from hypoloom.fake_agent import default_request
from hypoloom.workflow_guidance import MAX_GUIDANCE_BYTES, performance_guidance


REFERENCE = "skills/research-workflow/references/performance.md"


def _request(command, binding, *, budget=None):
    base = default_request(command)
    inputs = [EmbeddedInput.from_bytes("node--expert-resources", "application/json",
        canonical_json({"plugin": binding.to_dict()}))]
    config = {"required_experiment_spec": {"metric": None,
        "evaluator_version": "fixture-v1", "artifact_requirements": ["result.json"]}}
    if budget is not None:
        config["node_runtime"] = {"memory": {"max_inline_input_bytes": budget}}
    inputs.append(EmbeddedInput.from_bytes("node--expert-config", "application/json", canonical_json(config)))
    return replace(base, schema_version=28, input_artifacts=tuple(inputs),
        required_output_schema=required_output_schema(command, base.task_context, schema_version=28))


@pytest.mark.parametrize("command", [AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
    AgentCommand.EXPERIMENT_EXECUTION, AgentCommand.RESULT_ANALYSIS])
def test_actual_bound_reference_is_in_prompt_without_read_or_request_mutation(tmp_path, command):
    store = ExpertPluginStore(tmp_path)
    binding = store.ensure("run", "alpha", "Any scientific domain")
    view = store.read("run", "alpha", commit=binding.commit, attempt_id="one", max_bytes=1024**2)
    try:
        request = _request(command, binding)
        before = request.to_dict()
        manifest = {}
        prompt = json.loads(_prompt(request, tool_workspace=True, plugin_root=view.root, manifest=manifest))
        guidance = prompt["workflow_guidance"]
        assert guidance["status"] == "inline"
        assert guidance["content"] == (view.root / REFERENCE).read_text()
        assert guidance["plugin_commit"] == binding.commit
        assert guidance["path"] == "/expert-plugin/" + REFERENCE
        assert manifest["workflow_guidance_bytes"] == len(canonical_json(guidance))
        assert request.to_dict() == before
    finally:
        view.close()


def test_guidance_tracks_bound_git_version_not_latest_seed_or_peer(tmp_path):
    store = ExpertPluginStore(tmp_path)
    first = store.ensure("run", "alpha", "One owner")
    peer = store.ensure("run", "beta", "Another owner")
    old = store.read("run", "alpha", commit=first.commit, attempt_id="old", max_bytes=1024**2)
    original = (old.root / REFERENCE).read_text()
    update = store.prepare_update("run", "alpha", commit=first.commit, attempt_id="edit")
    (update.root / REFERENCE).write_text("Owner-specific measured workload guidance.\n")
    accepted = store.finish_update(update, max_bytes=1024**2)
    new = store.read("run", "alpha", commit=accepted.commit, attempt_id="new", max_bytes=1024**2)
    other = store.read("run", "beta", commit=peer.commit, attempt_id="peer", max_bytes=1024**2)
    try:
        command = AgentCommand.EXPERIMENT_EXECUTION
        assert performance_guidance(_request(command, first), old.root)["content"] == original
        assert performance_guidance(_request(command, accepted), new.root)["content"] != original
        assert performance_guidance(_request(command, peer), other.root)["content"] == original
    finally:
        old.close(); new.close(); other.close()


@pytest.mark.parametrize("command", [AgentCommand.LITERATURE_REVIEW,
    AgentCommand.HYPOTHESIS_PROPOSAL, AgentCommand.GROUNDING,
    AgentCommand.PAPER_READ, AgentCommand.SELF_EVOLVE])
def test_unrelated_stages_do_not_read_plugin_files(tmp_path, command):
    # A path that would fail if opened: stage selection must happen before I/O.
    store = ExpertPluginStore(tmp_path)
    binding = store.ensure("run", "alpha", "Other stage")
    invalid = tmp_path / "not-a-directory"
    invalid.write_text("no plugin here")
    assert performance_guidance(_request(command, binding), invalid) is None


def test_retained_plugin_without_reference_is_explicit_and_not_backfilled(tmp_path):
    seed = tmp_path / "seed"
    shutil.copytree(SEED_ROOT, seed)
    store = ExpertPluginStore(tmp_path / "state", seed_root=seed)
    binding = store.ensure("run", "alpha", "Old plugin")
    view = store.read("run", "alpha", commit=binding.commit, attempt_id="old", max_bytes=1024**2)
    try:
        result = performance_guidance(_request(AgentCommand.EXPERIMENT_EXECUTION, binding), view.root)
        assert result["status"] == "unavailable" and "content" not in result
        assert not (view.root / REFERENCE).exists()
        assert ExpertPluginStore(tmp_path / "state").ensure("run", "alpha", "ignored") == binding
    finally:
        view.close()


@pytest.mark.parametrize("budget", [1, 512, 12000])
def test_guidance_shares_inline_budget_and_never_sends_partial_text(tmp_path, budget):
    store = ExpertPluginStore(tmp_path)
    binding = store.ensure("run", "alpha", "Bounded context")
    view = store.read("run", "alpha", commit=binding.commit, attempt_id="one", max_bytes=1024**2)
    try:
        request = _request(AgentCommand.EXPERIMENT_EXECUTION, binding, budget=budget)
        before = request.to_dict()
        manifest = {}
        prompt = json.loads(_prompt(request, tool_workspace=True, plugin_root=view.root, manifest=manifest))
        assert manifest["inline_input_bytes"] <= budget
        result = prompt.get("workflow_guidance")
        actual_inline = sum(len(canonical_json(item)) for item in prompt["rendered_inputs"]
                            if "path" not in item)
        assert actual_inline + (len(canonical_json(result)) if result is not None else 0) <= budget
        if result is not None:
            assert len(canonical_json(result)) <= min(budget, MAX_GUIDANCE_BYTES)
            if result["status"] == "inline":
                assert result["content"] == (view.root / REFERENCE).read_text()
            else:
                assert result["status"] == "read_from_path" and "content" not in result
        assert request.to_dict() == before
    finally:
        view.close()


def test_oversized_bound_reference_remains_readable_without_truncation(tmp_path):
    store = ExpertPluginStore(tmp_path)
    binding = store.ensure("run", "alpha", "Long private guidance")
    update = store.prepare_update("run", "alpha", commit=binding.commit, attempt_id="edit")
    content = "Measured workload details.\n" * MAX_GUIDANCE_BYTES
    (update.root / REFERENCE).write_text(content)
    binding = store.finish_update(update, max_bytes=1024**2)
    view = store.read("run", "alpha", commit=binding.commit, attempt_id="read", max_bytes=1024**2)
    try:
        guidance = performance_guidance(_request(AgentCommand.EXPERIMENT_EXECUTION, binding), view.root)
        assert guidance == {"path": "/expert-plugin/" + REFERENCE,
            "plugin_commit": binding.commit, "status": "read_from_path"}
        assert (view.root / REFERENCE).read_text() == content
    finally:
        view.close()


def test_missing_binding_or_native_workspace_does_not_inject_current_seed(tmp_path):
    request = default_request(AgentCommand.EXPERIMENT_EXECUTION)
    assert performance_guidance(request, tmp_path) is None
    assert performance_guidance(request, None) is None
    assert "workflow_guidance" not in json.loads(_prompt(request, plugin_root=tmp_path))
