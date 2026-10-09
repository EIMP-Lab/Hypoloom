"""One seed, real Git versions and stage-scoped native resource loading."""
from dataclasses import replace
import json
from pathlib import Path
import shutil

import pytest

from hypoloom.agent_aci import EmbeddedInput, required_effect, required_output_schema
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import AuxiliaryRequestKind, AuxiliaryTask, ResearchNodeSubkind
from hypoloom.expert_plugin import ExpertPluginStore, SEED_ROOT, WORKFLOW_SKILL_ROOT
from hypoloom.fake_agent import default_request
from hypoloom.full_cc_runtime import full_cc_argv, full_cc_instruction
from hypoloom.git_authoring_workspace import GitAuthoringIntegrityError
from test_agent_sessions import _manager


ENTRY = "skills/research-workflow/SKILL.md"
LIMIT = 1024 * 1024


def _grounding(manager, *, ordinal=1):
    base = default_request(AgentCommand.GROUNDING)
    task = AuxiliaryTask(kind=AuxiliaryRequestKind.GROUNDING, run_id=base.run_id, ordinal=ordinal)
    plugin = ExpertPluginStore(manager.state_root, owner_kind="grounding").ensure(
        base.run_id, "grounding", "Current commission")
    item = EmbeddedInput.from_bytes(task.command_id + "--grounding-resources", "application/json",
        json.dumps({"plugin": plugin.to_dict()}).encode())
    return replace(base, schema_version=28, owner_workflow_id=base.run_id, task_context=task, command_id=task.command_id,
        attempt_id=f"grounding-{ordinal}", input_artifacts=(item,), immutable_context_refs=(item.ref,),
        required_output_schema=required_output_schema(base.command, task, schema_version=28))


def test_common_seed_is_identical_for_both_owners_and_includes_stage_files(tmp_path):
    expected = {p.relative_to(WORKFLOW_SKILL_ROOT).as_posix(): p.read_bytes()
        for p in WORKFLOW_SKILL_ROOT.rglob("*") if p.is_file()}
    assert len(expected) == 9
    assert len(expected["SKILL.md"]) < 2400
    for kind, role in (("expert", "numerical"), ("grounding", "grounding")):
        store = ExpertPluginStore(tmp_path, owner_kind=kind)
        bound = store.ensure("run", role, "Different scientific domain")
        view = store.read("run", role, commit=bound.commit, attempt_id=kind, max_bytes=LIMIT)
        try:
            root = view.root / "skills/research-workflow"
            assert {p.relative_to(root).as_posix(): p.read_bytes()
                for p in root.rglob("*") if p.is_file()} == expected
        finally:
            view.close()


@pytest.mark.parametrize("kind,role", [("expert", "alpha"), ("grounding", "grounding")])
@pytest.mark.parametrize("changed_path", [ENTRY, "skills/research-workflow/references/performance.md"])
def test_workflow_evolves_only_in_owner_git_and_preserves_bound_views(tmp_path, kind, role, changed_path):
    store = ExpertPluginStore(tmp_path, owner_kind=kind)
    base = store.ensure("run", role, "Method evolution")
    peer = ExpertPluginStore(tmp_path).ensure("run", "peer", "Independent")
    view = store.read("run", role, commit=base.commit, attempt_id="before", max_bytes=LIMIT)
    original = (view.root / changed_path).read_bytes()
    update = store.prepare_update("run", role, commit=base.commit, attempt_id="evolve", command_id="evolve")
    (update.root / changed_path).write_bytes(original + b"\nCheck the actual evidence before adopting a report's lesson.\n")
    # Reconstruct after an interrupted authoring call using the same logical command.
    recovered = store.prepare_update("run", role, commit=base.commit, attempt_id="retry", command_id="evolve")
    accepted = store.finish_update(recovered, max_bytes=LIMIT)
    assert accepted.commit != base.commit
    assert store.finish_update(recovered, max_bytes=LIMIT) == accepted
    assert (view.root / changed_path).read_bytes() == original
    assert ExpertPluginStore(tmp_path).ensure("run", "peer", "ignored") == peer
    later = store.read("run", role, commit=accepted.commit, attempt_id="later", max_bytes=LIMIT)
    try:
        assert (later.root / changed_path).read_bytes() != original
    finally:
        later.close()
        view.close()
    broken = store.prepare_update("run", role, commit=accepted.commit, attempt_id="remove")
    (broken.root / ENTRY).unlink()
    with pytest.raises(ValueError, match="preserve the base research-workflow"):
        store.finish_update(broken, max_bytes=LIMIT)
    assert store.ensure("run", role, "ignored") == accepted


def test_retained_plugin_is_not_backfilled_and_common_bytes_count_toward_budget(tmp_path):
    old_seed = tmp_path / "old-seed"
    shutil.copytree(SEED_ROOT, old_seed)
    old = ExpertPluginStore(tmp_path / "state", seed_root=old_seed)
    base = old.ensure("run", "alpha", "Original")
    current = ExpertPluginStore(tmp_path / "state")
    assert current.ensure("run", "alpha", "Ignored") == base
    view = current.read("run", "alpha", commit=base.commit, attempt_id="retained", max_bytes=LIMIT)
    try:
        assert not (view.root / ENTRY).exists()
        size = sum(p.stat().st_size for p in view.root.rglob("*") if p.is_file())
    finally:
        view.close()
    with pytest.raises(ValueError, match="budget"):
        current.ensure("new", "alpha", "Original", max_bytes=size)
    assert not (tmp_path / "state/new/experts/alpha/plugin").exists()


@pytest.mark.parametrize("ordinal", [1, 2])
def test_initial_grounding_loads_readonly_without_changing_execution_identity(tmp_path, ordinal):
    manager = _manager(tmp_path)
    request = _grounding(manager, ordinal=ordinal)
    identity = (request.role, request.component_id, request.required_model_identity, request.budget)
    workspace = manager.prepare(request, None)
    try:
        assert (request.role, request.component_id, request.required_model_identity, request.budget) == identity
        assert request.role != "grounding"
        assert (workspace.plugin_root / ENTRY).is_file()
        assert workspace.plugin_update is None and workspace.memory_root is None
        argv = full_cc_argv(bwrap=Path("/usr/bin/bwrap"), executable=tmp_path / "claude",
            request=request, model="selected-provider-model", workspace=workspace,
            provider_socket=tmp_path / "provider.sock", host_socket=None, output_schema={}, extra_paths=())
        assert ("--ro-bind", str(workspace.plugin_root), "/expert-plugin") in set(zip(argv, argv[1:], argv[2:]))
        assert "/plugin-update" not in argv
        assert argv[argv.index("--model") + 1] == "selected-provider-model"
        assert argv[argv.index("--plugin-dir") + 1] == "/expert-plugin"
        prompt = full_cc_instruction(request)
        assert "`hypoloom-grounding:research-workflow` with args `grounding`" in prompt
        assert "read only its applicable stage reference" in prompt
        assert (workspace.plugin_root / "skills/research-workflow/references/grounding.md").is_file()
    finally:
        workspace.cleanup()


@pytest.mark.parametrize("damage", ["wrong-owner", "mixed-owners", "duplicate", "query-plan", "manager-role"])
def test_grounding_binding_cannot_grant_other_nodes_its_resources(tmp_path, damage):
    manager = _manager(tmp_path)
    request = _grounding(manager)
    item = request.input_artifacts[0]
    if damage == "wrong-owner":
        raw = json.loads(item.content_bytes()); raw["plugin"]["owner_kind"] = "expert"
        request = replace(request, input_artifacts=(EmbeddedInput.from_bytes(item.ref, item.media_type, json.dumps(raw).encode()),))
    elif damage == "mixed-owners":
        mixed = EmbeddedInput.from_bytes("other--expert-resources", "application/json", item.content_bytes())
        request = replace(request, input_artifacts=(item, mixed))
    elif damage == "duplicate":
        other = EmbeddedInput.from_bytes("other--grounding-resources", "application/json", item.content_bytes())
        request = replace(request, input_artifacts=(item, other))
    elif damage == "query-plan":
        task = replace(request.task_context, kind=AuxiliaryRequestKind.GROUNDING_QUERY_PLAN)
        request = replace(request, command=AgentCommand.LITERATURE_REVIEW, task_context=task, command_id=task.command_id,
            effect_requirements=(required_effect(AgentCommand.LITERATURE_REVIEW),),
            required_output_schema=required_output_schema(AgentCommand.LITERATURE_REVIEW, task, schema_version=28))
    else:
        base = default_request(AgentCommand.GRAPH_EVOLVE)
        request = replace(base, schema_version=28, role="researcher", input_artifacts=(item,))
    with pytest.raises((GitAuthoringIntegrityError, ValueError), match="Grounding|another owner"):
        workspace = manager.prepare(request, None)
        workspace.cleanup()


@pytest.mark.parametrize("command,stage", [
    (AgentCommand.LITERATURE_REVIEW, "evidence"), (AgentCommand.HYPOTHESIS_PROPOSAL, "evidence"),
    (AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, "implementation"), (AgentCommand.EXPERIMENT_EXECUTION, "execution"),
    (AgentCommand.RESULT_ANALYSIS, "analysis"), (AgentCommand.SELF_EVOLVE, "evolution"),
])
def test_stage_router_uses_bound_name_and_does_not_inline_all_references(command, stage):
    request = default_request(command)
    item = EmbeddedInput.from_bytes("node--expert-resources", "application/json",
        json.dumps({"plugin": {"name": "hypoloom-numerical-expert"}}).encode())
    prompt = full_cc_instruction(replace(request, input_artifacts=(item,)))
    assert f"`hypoloom-numerical-expert:research-workflow` with args `{stage}`" in prompt
    assert "references/grounding.md" not in prompt
    assert ":research-workflow" not in full_cc_instruction(request)


def test_query_planning_and_restricted_readers_do_not_load_workflow():
    item = EmbeddedInput.from_bytes("node--expert-resources", "application/json",
        json.dumps({"plugin": {"name": "hypoloom-reader"}}).encode())
    request = default_request(AgentCommand.LITERATURE_REVIEW)
    task = replace(request.task_context, key=replace(request.task_context.key, subkind=ResearchNodeSubkind.QUERY_PLAN))
    assert ":research-workflow" not in full_cc_instruction(replace(request, task_context=task,
        command_id=task.command_id, required_output_schema=required_output_schema(request.command, task), input_artifacts=(item,)))
    reader = default_request(AgentCommand.PAPER_READ)
    assert ":research-workflow" not in full_cc_instruction(replace(reader, input_artifacts=(item,)))


def test_existing_runtime_identity_covers_common_entry_and_references(tmp_path, monkeypatch):
    import hypoloom.workflow_host as module
    package = tmp_path / "hypoloom"
    root = package / "skills/research-workflow"
    shutil.copytree(WORKFLOW_SKILL_ROOT, root)
    monkeypatch.setattr(module, "__file__", str(package / "workflow_host.py"))
    before = module.installed_run_version().runtime_environment_id
    path = root / "references/grounding.md"
    path.write_text(path.read_text() + "\nAnother actual interface observation.\n")
    assert module.installed_run_version().runtime_environment_id != before


@pytest.mark.parametrize("domain", ["classification", "optimization", "reproduction", "aggregation"])
def test_task_specific_parsers_keep_distinct_layouts_and_scientific_semantics(tmp_path, domain):
    """Interface compatibility, not a claim about model-authored parser quality."""
    from hypoloom.native_output import parser_smoke
    from hypoloom.shared_acceptance import SharedAcceptance, evaluate_shared_acceptance
    if domain == "classification":
        relative = "heldout/predictions.json"
        criteria = [{"name": "accuracy", "operator": "GT", "target": "0.5"}]
        compute = """truth, predicted = raw['truth'], raw['predicted']
result = {'measurements': {'accuracy': sum(a == b for a, b in zip(truth, predicted)) / len(truth)},
          'checks': {'valid': len(truth) == len(predicted) == 2}}
"""
        cases = [({"truth": [0, 1], "predicted": [0, 1]}, True),
            ({"truth": [0, 1], "predicted": [0, 0]}, False),
            ({"truth": [0, 1], "predicted": [0, 1, 2]}, False)]
    elif domain == "optimization":
        relative = "solution.json"
        criteria = [{"name": "objective", "operator": "LT", "target": "1"}]
        compute = """x = raw['x']
result = {'measurements': {'objective': x * x}, 'checks': {'valid': x >= 0}}
"""
        cases = [({"x": 0}, True), ({"x": 1}, False), ({"x": -0.5}, False)]
    elif domain == "aggregation":
        relative = "measurements/details.json"
        criteria = [{"name": "mean", "operator": "GT", "target": "0.5"}]
        compute = """import math
weights, values = raw['weights'], raw['values']
mean = sum(w*v for w,v in zip(weights, values)) / sum(weights)
result = {'measurements': {'mean': mean}, 'checks': {'valid':
    len(weights) == len(values) == 2 and math.isclose(mean, raw['reported'], abs_tol=1e-9)}}
"""
        cases = [({'weights': [1, 3], 'values': [0.4, 0.8], 'reported': 0.7}, True),
            ({'weights': [1, 3], 'values': [0.1, 0.1], 'reported': 0.7}, False),
            ({'weights': [1, 3], 'values': [0.4, 0.8], 'reported': 0.9}, False),
            ({'weights': [1, 1], 'values': [0.4, 0.8], 'reported': 0.7}, False)]
    else:
        relative = "observations/reproduced.json"
        criteria = [{"name": "reproduced", "operator": "IS_TRUE"}]
        compute = """pairs = raw['pairs']
result = {'measurements': {}, 'checks': {
    'reproduced': all(y == x * x for x, y in pairs),
    'valid': [x for x, _ in pairs] == [0, 1, 2]}}
"""
        cases = [({"pairs": [[0, 0], [1, 1], [2, 4]]}, True),
            ({"pairs": [[0, 0], [1, 1], [2, 5]]}, False),
            ({"pairs": [[0, 0], [1, 1]]}, False)]
    source = ("import json, os\nfrom pathlib import Path\n"
        f"raw = json.loads((Path(os.environ['HYPOLOOM_MEASUREMENT_RAW_ROOT']) / {relative!r}).read_text())\n"
        + compute + "Path(os.environ['HYPOLOOM_MEASUREMENT_RESULT_PATH']).write_text(json.dumps(result))\n")
    declaration = {"summary": domain, "rule": "ALL", "criteria": criteria,
        "validity_checks": ["valid"], "parser_files": ["metrics/evaluate.py"],
        "parser_argv": ["python", "/input/metrics/evaluate.py"], "raw_artifacts": [relative]}
    accepted = SharedAcceptance.from_dict(declaration)
    for index, (raw, expected) in enumerate(cases):
        root = tmp_path / str(index)
        (root / "metrics").mkdir(parents=True)
        (root / "metrics/evaluate.py").write_text(source)
        sample = root / "smoke" / relative
        sample.parent.mkdir(parents=True)
        sample.write_text(json.dumps(raw))
        result = parser_smoke(json.dumps({"typed_candidate": {"acceptance": declaration}}),
            source_root=root, test_root=root / "sandbox", timeout_seconds=5, max_bytes=65536)
        assert result is not None and result["error"] is None, result
        assert evaluate_shared_acceptance(accepted, result["observation"]) is expected
        if domain == "aggregation":
            assert result["observation"]["checks"]["valid"] is expected
