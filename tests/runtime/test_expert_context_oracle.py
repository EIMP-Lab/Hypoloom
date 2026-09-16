from __future__ import annotations

import base64
import copy
import json
import subprocess
from pathlib import Path

import pytest

from hypoloom.expert_context_oracle import audit_expert_context
from hypoloom.expert_memory import ExpertMemory
from hypoloom.expert_plugin import ExpertPluginStore
from test_expert_memory import _facts, _report


def _encode(value) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    return base64.b64encode(raw).decode()


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(("git", "-C", str(repository), *arguments), check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.decode().strip()


def _commit(repository: Path) -> str:
    _git(repository, "add", "--all")
    _git(repository, "-c", "user.name=Oracle fixture", "-c", "user.email=oracle@example.invalid",
         "commit", "-qm", "fixture change")
    return _git(repository, "rev-parse", "HEAD")


@pytest.fixture
def context(tmp_path: Path) -> dict:
    run_id, role, attempt_id = "run-1", "expert-a", "attempt-1"
    plugin = ExpertPluginStore(tmp_path).ensure(run_id, role, "bounded fixture")
    memory = ExpertMemory(tmp_path)
    base = memory.ensure(run_id, role)
    report = {"MEMORY.md": b"# Memory\n\nSee rounds/1.md.\n", "rounds/1.md": _report()}
    commit = memory.commit_report(run_id, role, base, "node-1", report,
                                  cycle=1, facts=_facts(), max_bytes=8192)
    owner = tmp_path / run_id / "experts" / role
    view = owner / "views/node-1"
    for name, content in {"background.md": b"Grounding observation", "graph/edges.jsonl": b"",
                          f"experts/{role}/state.json": json.dumps({"role": role}).encode()}.items():
        path = view / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    resources = {"plugin": plugin.to_dict(), "memory_commit": base, "research_view_id": "node-1"}
    request = {
        "schema_version": 12, "run_id": run_id, "role": role, "attempt_id": attempt_id,
        "command_id": "node-1", "component_id": "component-1", "command": "CYCLE_REPORT",
        "required_adapter_version": "claude-code-bwrap/34",
        "task_context": {"kind": "RESEARCH_NODE", "key": {"run_id": run_id,
            "expert_slot": role, "component_id": "component-1", "cycle": 1}},
        "input_artifacts": [{"ref": "node-1--expert-resources", "media_type": "application/json",
                             "content_base64": _encode(resources)}],
    }
    response = {"proposed_workspace_artifacts": [
        {"path": name, "content_base64": _encode(content)} for name, content in report.items()]}
    observation = {"request": request, "response": response,
                   "session": {"expert_resources": resources},
                   "memory": {"base_commit": base, "commit": commit}}
    archive = tmp_path / "artifacts" / run_id / "agent" / attempt_id / "transcript"
    for name, content in report.items():
        path = archive / "memory" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    facts = [{"fact_id": "proposal-1", "fact_type": "HypothesisProposed", "hypothesis_id": "hyp-1"},
             {"fact_id": "assessment-1", "fact_type": "HypothesisAssessed", "hypothesis_id": "hyp-1"}]
    fact_payloads = {"proposal-1": {"proposer_role": role, "component_id": "component-1",
                                     "parent_hypothesis_id": None},
                     "assessment-1": {"assessment": "SUPPORTED"}}
    row = {"attempt_id": attempt_id, "kind": "AGENT", "state": "CLOSED",
           "terminal_reason": "SUCCEEDED", "started_at": 1.0, "attempt_ordinal": 1}
    return {"root": tmp_path, "owner": owner, "archive": archive, "attempts": [row],
            "payloads": {attempt_id: observation}, "facts": facts, "fact_payloads": fact_payloads}


def _audit(context: dict):
    return audit_expert_context(context["root"], context["attempts"], context["payloads"],
                                context["facts"], context["fact_payloads"])


def _resources(context: dict) -> dict:
    request = context["payloads"]["attempt-1"]["request"]
    return json.loads(base64.b64decode(request["input_artifacts"][0]["content_base64"]))


def _set_resources(context: dict, resources: dict) -> None:
    observation = context["payloads"]["attempt-1"]
    observation["request"]["input_artifacts"][0]["content_base64"] = _encode(resources)
    observation["session"]["expert_resources"] = copy.deepcopy(resources)


def test_independent_oracle_accepts_real_git_report_response_and_archive(context: dict):
    assert _audit(context) == []


def _evolve(context, *, changed=True):
    observation = context["payloads"]["attempt-1"]
    request = observation["request"]
    request.update(schema_version=14, command="SELF_EVOLVE")
    resources = _resources(context)
    store = ExpertPluginStore(context["root"])
    prepared = store.prepare_update(request["run_id"], request["role"],
        commit=resources["plugin"]["commit"], attempt_id=request["attempt_id"], command_id=request["command_id"])
    if changed:
        skill = prepared.root / "skills/experiment-repair/SKILL.md"
        skill.write_text(skill.read_text() + "\nUse the observed error trace.\n")
    accepted = store.finish_update(prepared, max_bytes=1024 * 1024)
    observation["plugin_update"] = {"base_commit": resources["plugin"]["commit"], "commit": accepted.commit}
    observation["response"] = {"proposed_workspace_artifacts": []}
    return observation


@pytest.mark.parametrize("changed", [False, True])
def test_independent_oracle_accepts_plugin_noop_or_one_command_owned_git_commit(context, changed):
    _evolve(context, changed=changed)
    assert _audit(context) == []


@pytest.mark.parametrize("mutation", ["base", "missing", "foreign-command", "failed-node"])
def test_independent_oracle_rejects_plugin_update_without_its_accepted_cause(context, mutation):
    observation = _evolve(context)
    if mutation == "base": observation["plugin_update"]["base_commit"] = "0" * 40
    elif mutation == "missing": observation.pop("plugin_update")
    elif mutation == "failed-node": context["attempts"][0]["terminal_reason"] = "FAILED"
    else: observation["request"]["command_id"] = "different-logical-node"
    assert any(issue[0] == "expert_plugin_update_invalid" for issue in _audit(context))


def test_native_executable_script_is_a_regular_plugin_file(context: dict):
    script = context["owner"] / "plugin/skills/experiment-report/scripts/report.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\nprintf 'report helper\\n'\n")
    script.chmod(0o755)
    resources = _resources(context)
    resources["plugin"]["commit"] = _commit(context["owner"] / "plugin")
    _set_resources(context, resources)
    assert _audit(context) == []


def test_memory_markdown_does_not_inherit_plugin_executable_permission(context: dict):
    (context["owner"] / "memory/MEMORY.md").chmod(0o755)
    resources = _resources(context)
    resources["memory_commit"] = _commit(context["owner"] / "memory")
    _set_resources(context, resources)
    assert "non-regular" in _audit(context)[0][1]


@pytest.mark.parametrize("damage", ["unknown-fact", "fake-verdict", "other-role", "stale-cycle",
                                     "archive-bytes", "git-bytes", "wrong-base"])
def test_accepted_report_raw_evidence_mismatch_is_rejected(context: dict, damage: str):
    observation = context["payloads"]["attempt-1"]
    if damage in {"unknown-fact", "fake-verdict"}:
        content = _report("nonexistent") if damage == "unknown-fact" else _report(assessment="REFUTED")
        observation["response"]["proposed_workspace_artifacts"][1]["content_base64"] = _encode(content)
    elif damage == "other-role":
        context["fact_payloads"]["proposal-1"]["proposer_role"] = "expert-b"
    elif damage == "stale-cycle":
        observation["request"]["task_context"]["key"]["cycle"] = 2
        observation["response"]["proposed_workspace_artifacts"][1]["path"] = "rounds/2.md"
    elif damage == "archive-bytes":
        (context["archive"] / "memory/MEMORY.md").write_text("changed archive")
    elif damage == "git-bytes":
        (context["owner"] / "memory/MEMORY.md").write_text("changed Git")
        observation["memory"]["commit"] = _commit(context["owner"] / "memory")
    else:
        observation["memory"]["base_commit"] = observation["memory"]["commit"]
    issues = _audit(context)
    assert len(issues) == 1 and issues[0][0] == "expert_report_invalid", issues


@pytest.mark.parametrize("damage", ["missing-manifest", "unknown-commit", "other-role",
                                     "missing-view", "peer-view", "session-descriptor"])
def test_native_resources_require_raw_git_owner_and_exact_request_binding(context: dict, damage: str):
    resources = _resources(context)
    if damage == "missing-manifest":
        (context["owner"] / "plugin/.claude-plugin/plugin.json").unlink()
        resources["plugin"]["commit"] = _commit(context["owner"] / "plugin")
    elif damage == "unknown-commit":
        resources["plugin"]["commit"] = "0" * 40
    elif damage == "other-role":
        resources["plugin"]["role"] = "expert-b"
    elif damage == "missing-view":
        resources["research_view_id"] = "absent"
    elif damage == "peer-view":
        (context["owner"] / "views/node-1/run.json").write_text("whole run")
    _set_resources(context, resources)
    if damage == "session-descriptor":
        context["payloads"]["attempt-1"]["session"]["expert_resources"]["memory_commit"] = "f" * 40
    issues = _audit(context)
    assert len(issues) == 1 and issues[0][0] == "expert_resource_invalid", issues


@pytest.mark.parametrize("retry_class,expected", [("INFRASTRUCTURE", True), ("MODEL", False)])
def test_only_infrastructure_retry_requires_unchanged_complete_descriptor(
    context: dict, retry_class: str, expected: bool,
):
    first = context["payloads"]["attempt-1"]
    first["request"]["command"] = "RESULT_ANALYSIS"
    first["retry_class"] = retry_class
    context["attempts"][0]["terminal_reason"] = "FAILED"
    second = copy.deepcopy(first)
    second["request"]["attempt_id"] = "attempt-2"
    resources = _resources(context)
    resources["memory_commit"] = first["memory"]["commit"]
    second["request"]["input_artifacts"][0]["content_base64"] = _encode(resources)
    second["session"]["expert_resources"] = resources
    context["payloads"]["attempt-2"] = second
    context["attempts"].append({**context["attempts"][0], "attempt_id": "attempt-2",
                                "started_at": 2.0, "attempt_ordinal": 2})
    issues = _audit(context)
    assert bool(issues) is expected
    if expected:
        assert "infrastructure retry" in issues[0][1]


def test_plugin_revision_is_fixed_within_cycle_but_selectable_next_cycle(context: dict):
    first = context["payloads"]["attempt-1"]
    first["request"]["command"] = "RESULT_ANALYSIS"
    second = copy.deepcopy(first)
    second["request"]["attempt_id"] = "attempt-2"
    second["request"]["command_id"] = "node-2"
    plugin_file = context["owner"] / "plugin/skills/experiment-report/SKILL.md"
    plugin_file.write_bytes(plugin_file.read_bytes() + b"\nA later skill improvement.\n")
    resources = _resources(context)
    resources["plugin"]["commit"] = _commit(context["owner"] / "plugin")
    second["request"]["input_artifacts"][0]["content_base64"] = _encode(resources)
    second["session"]["expert_resources"] = resources
    context["payloads"]["attempt-2"] = second
    context["attempts"].append({**context["attempts"][0], "attempt_id": "attempt-2",
                                "started_at": 2.0, "attempt_ordinal": 1})
    assert "same cycle" in _audit(context)[0][1]
    second["request"]["task_context"]["key"]["cycle"] = 2
    assert _audit(context) == []


def test_legacy_aci_does_not_require_new_expert_resource_semantics(context: dict):
    request = context["payloads"]["attempt-1"]["request"]
    request["schema_version"] = 11
    request["input_artifacts"] = []
    assert _audit(context) == []
