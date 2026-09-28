"""Independent pin/revert source and future-cycle checks use raw rows/Git."""
import copy
import json

import pytest

from hypoloom.expert_context_oracle import audit_expert_context
from hypoloom.expert_plugin import ExpertPluginStore
from test_expert_context_oracle import context as context, _commit, _encode, _resources, _set_resources


@pytest.fixture
def selection(context):
    original = context["payloads"]["attempt-1"]
    original["request"].update(schema_version=14, command="RESULT_ANALYSIS")
    old = copy.deepcopy(original)
    old["request"].update(attempt_id="before-control", command_id="before-control")
    context["payloads"]["before-control"] = old
    context["attempts"].append({**context["attempts"][0], "attempt_id": "before-control"})
    context["attempts"][0]["started_at"] = 10.0
    original["request"]["task_context"]["key"]["cycle"] = 2
    base = _resources(context)["plugin"]["commit"]
    path = context["owner"] / "plugin/skills/experiment-report/SKILL.md"
    path.write_bytes(path.read_bytes() + b"\nAnother valid native revision.\n")
    later = _commit(context["owner"] / "plugin")
    goal = {"run_id": "run-1", "research_contract": {"roles": [{"id": "expert-a"}],
        "components": [{"id": "component-1", "owner": "expert-a"}]}}
    context["facts"].append({"fact_id": "goal", "fact_type": "GoalDefined"})
    context["fact_payloads"]["goal"] = goal
    control = {"action": "pin", "role": "expert-a", "owner_kind": "expert", "requested_commit": base,
        "commit": base, "effective_cycles": {"component-1": 2}}
    effect = {"run_id": "run-1", "workflow_id": "run-1", "workflow_kind": "RUN", "state": "ACTIVE",
        "revision": 7, "command_id": "pin-1", "command_type": "SELECT_PLUGIN", "created_at": 5.0,
        "payload_json": json.dumps({"plugin_selection": control})}
    command = {"run_id": "run-1", "workflow_id": "run-1", "command_id": "pin-1", "command_type": "SELECT_PLUGIN",
        "response_status": "accepted", "request_json": json.dumps({"schema_version": 1, "request_id": "pin-1",
            "expected_revision": 6, "command": "PIN_PLUGIN", "payload": {"run_id": "run-1", "role": "expert-a",
                "owner_kind": "expert", "commit": base}})}
    return context, control, effect, command, base, later


def audit(selection, *, commands=True):
    data, control, effect, command, _, _ = selection
    effect["payload_json"] = json.dumps({"plugin_selection": control})
    kwargs = {"commands": [command]} if commands else {}
    return audit_expert_context(data["root"], data["attempts"], data["payloads"], data["facts"],
        data["fact_payloads"], batch_transitions=[effect], **kwargs)


@pytest.mark.parametrize("tamper", ["consumer", "commit", "owner", "effective-cycle"])
def test_pin_cannot_change_source_owner_target_or_frozen_cycle(selection, tamper):
    data, control, _, _, _, later = selection
    if tamper == "consumer":
        resources = _resources(data)
        resources["plugin"]["commit"] = later
        _set_resources(data, resources)
    elif tamper == "commit": control["commit"] = later
    elif tamper == "owner": control["role"] = "foreign-owner"
    else: control["effective_cycles"]["component-1"] = 1
    issues = audit(selection)
    assert any(item[0] == ("plugin_selection_invalid" if tamper == "consumer" else "plugin_control_invalid") for item in issues)


def test_pin_uses_accepted_command_and_preserves_older_cycle(selection):
    assert audit(selection) == []
    assert audit(selection, commands=False)[0][0] == "plugin_control_invalid"


@pytest.mark.parametrize("cycle,early", [(1, False), (2, False), (1, True)])
def test_midcycle_pin_changes_only_the_next_cycle(selection, cycle, early):
    data, control, _, command, _, later = selection
    control.update(requested_commit=later, commit=later)
    public = json.loads(command["request_json"])
    public["payload"]["commit"] = later
    command["request_json"] = json.dumps(public)
    data["payloads"]["attempt-1"]["request"]["task_context"]["key"]["cycle"] = cycle
    if cycle == 2 or early:
        resources = _resources(data)
        resources["plugin"]["commit"] = later
        _set_resources(data, resources)
    issues = audit(selection)
    assert bool(issues) is early
    if early:
        assert "same cycle" in issues[0][1]  # The existing invariant already covered this case.


@pytest.mark.parametrize("tamper", [None, "different-command", "different-tree"])
def test_revert_reads_actual_target_tree_and_git_command_cause(selection, tamper):
    data, control, effect, command, base, later = selection
    binding = ExpertPluginStore(data["root"]).revert_to("run-1", "expert-a", target_commit=base,
        command_id="pin-1", max_bytes=1024 * 1024)
    control.update(action="revert", commit=binding.commit)
    public = json.loads(command["request_json"])
    public["command"] = "REVERT_PLUGIN"
    if tamper == "different-command":
        effect["command_id"] = command["command_id"] = public["request_id"] = "another-control"
    elif tamper == "different-tree":
        control["commit"] = later
    command["request_json"] = json.dumps(public)
    resources = _resources(data)
    resources["plugin"]["commit"] = control["commit"]
    _set_resources(data, resources)
    issues = audit(selection)
    if tamper:
        assert any(item[0] == "plugin_control_invalid" for item in issues)
    else:
        assert issues == []


@pytest.mark.parametrize("tamper", [None, "failed-source", "late-source", "other-role", "substitution"])
def test_unpin_without_explicit_target_selects_actual_successful_candidate(selection, tamper):
    data, control, _, command, base, later = selection
    store = ExpertPluginStore(data["root"])
    prepared = store.prepare_update("run-1", "expert-a", commit=base, attempt_id="update-source", command_id="update-source")
    skill = prepared.root / "skills/self-evolve/SKILL.md"
    skill.write_bytes(skill.read_bytes() + b"\nRetained actual candidate under an older pin.\n")
    candidate = store.finish_update(prepared, max_bytes=1024 * 1024, advance=False)
    observation = copy.deepcopy(data["payloads"]["before-control"])
    observation["request"].update(attempt_id="update-source", command_id="update-source", command="SELF_EVOLVE")
    observation["plugin_update"] = {"base_commit": base, "commit": candidate.commit}
    data["payloads"]["update-source"] = observation
    row = {**data["attempts"][0], "attempt_id": "update-source", "started_at": 4.0, "ended_at": 4.5}
    data["attempts"].append(row)
    if tamper == "failed-source": row["terminal_reason"] = "FAILED"
    elif tamper == "late-source": row["ended_at"] = 6.0
    elif tamper == "other-role": observation["request"]["role"] = "another-role"
    control.update(action="unpin", requested_commit=None, commit=later if tamper == "substitution" else candidate.commit)
    public = json.loads(command["request_json"])
    public.update(command="UNPIN_PLUGIN")
    public["payload"]["commit"] = None
    command["request_json"] = json.dumps(public)
    resources = _resources(data)
    resources["plugin"]["commit"] = control["commit"]
    _set_resources(data, resources)
    issues = audit(selection)
    if tamper:
        assert any(item[0] == "plugin_control_invalid" for item in issues)
    else:
        assert issues == []


@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.parametrize("consumer,role,component", [("OBSERVER", "grounding", "grounding"),
    ("GROUNDING", "expert-a", "component-1")])
def test_grounding_control_applies_to_next_unfrozen_auxiliary_request(selection, changed, consumer, role, component):
    data, control, _, command, _, _ = selection
    store = ExpertPluginStore(data["root"], owner_kind="grounding")
    base = store.ensure("run-1", "grounding", "manager")
    prepared = store.prepare_update("run-1", "grounding", commit=base.commit, attempt_id="candidate", command_id="candidate")
    skill = prepared.root / "skills/graph-evolve/SKILL.md"
    skill.write_bytes(skill.read_bytes() + b"\nLater candidate.\n")
    candidate = store.finish_update(prepared, max_bytes=1024 * 1024, advance=False)
    control.update(owner_kind="grounding", role="grounding", requested_commit=base.commit, commit=base.commit, effective_cycles={})
    public = json.loads(command["request_json"])
    public["payload"].update(owner_kind="grounding", role="grounding", commit=base.commit)
    command["request_json"] = json.dumps(public)
    request = data["payloads"]["attempt-1"]["request"]
    request.update(command=consumer, role=role, component_id=component, task_context={"kind": "AUXILIARY"})
    request["input_artifacts"] = [{"ref": "node-1--grounding-resources", "content_base64":
        _encode({"plugin": (candidate if changed else base).to_dict()})}]
    issues = audit(selection)
    assert bool(issues) is changed
    if changed:
        assert issues[0][0] == "plugin_selection_invalid"
