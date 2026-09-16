from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from hypoloom.agent_aci import EmbeddedInput, canonical_json
from hypoloom.expert_loop import (
    AuxiliaryRequestKind,
    AuxiliaryTask,
    ResearchNodeKind,
    ResearchNodeSubkind,
)
from hypoloom.expert_plugin import ExpertPluginStore
from hypoloom.manager_oracle import audit_manager
from test_batch_oracle import Records, _closed_cycle


def _input(ref, value):
    return EmbeddedInput.from_bytes(
        ref, "application/json", canonical_json(value)
    ).to_dict()


def _replace_input(request, suffix, value):
    item = next(
        item for item in request["input_artifacts"] if item["ref"].endswith(suffix)
    )
    item.update(_input(item["ref"], value))


def _fixture(tmp_path: Path, *, edit=True):
    data: Records = _closed_cycle()
    data.transitions.pop()
    state_root = tmp_path / "state"
    state_root.mkdir()
    store = ExpertPluginStore(state_root, owner_kind="grounding")
    binding = store.ensure(
        data.run_id, "grounding", "Manage observed research results."
    )
    report_request = data.attempt_payloads["report"]["request"]
    intent = data.transition(
        "REQUEST_RESEARCH_NODE",
        {
            "task_context": report_request["task_context"],
            "role": report_request["role"],
            "component_id": report_request["component_id"],
        },
    )
    intent["command_id"] = report_request["command_id"]
    task = AuxiliaryTask(
        kind=AuxiliaryRequestKind.GRAPH_EVOLVE,
        run_id=data.run_id,
        cycle=1,
        ordinal=intent["revision"],
    )
    context = {
        "max_hypotheses_per_round": {"researcher": 2},
        "effective_max_forks": {"researcher": 2},
        "source_report_refs": ["report--response"],
        "enabled": True,
    }
    resources = {"plugin": binding.to_dict()}
    request = {
        "schema_version": 14,
        "run_id": data.run_id,
        "attempt_id": "manager",
        "command_id": task.command_id,
        "command": "GRAPH_EVOLVE",
        "role": "grounding",
        "component_id": "grounding",
        "task_context": task.to_dict(),
        "input_artifacts": [
            _input(task.command_id + "--manager-context", context),
            _input("report--response", data.attempt_payloads["report"]["response"]),
            _input(task.command_id + "--grounding-resources", resources),
        ],
    }
    request["immutable_context_refs"] = [
        item["ref"] for item in request["input_artifacts"]
    ]
    candidate = {
        "task_kind": "GRAPH_EVOLVE",
        "summary": "Focus the next batch.",
        "supporting_refs": ["report--response"],
        "graph_evolution": [
            {
                "role": "researcher",
                "effective_max_forks": 1,
                "reason": "One promising reported chain.",
            }
        ],
    }
    response = {
        "typed_candidate": candidate,
        "output_digest": "existing-manager-output",
    }
    prepared = store.prepare_update(
        data.run_id,
        "grounding",
        commit=binding.commit,
        attempt_id="manager",
        command_id=task.command_id,
    )
    if edit:
        skill = prepared.root / "skills/graph-evolve/SKILL.md"
        skill.write_bytes(
            skill.read_bytes()
            + b"\nUse the cited accepted report before changing fork counts.\n"
        )
    accepted = store.finish_update(prepared, max_bytes=131072)
    update = {"base_commit": binding.commit, "commit": accepted.commit}
    data.clock += 1
    data.attempts.append(
        {
            "run_id": data.run_id,
            "attempt_id": "manager",
            "kind": "AGENT",
            "state": "CLOSED",
            "terminal_reason": "SUCCEEDED",
            "command_id": task.command_id,
            "started_at": data.clock,
            "ended_at": data.clock + 0.2,
            "attempt_ordinal": 1,
        }
    )
    data.attempt_payloads["manager"] = {
        "request": request,
        "response": response,
        "plugin_update": update,
    }
    data.clock += 0.2
    data.transition(
        "ACCEPT_GRAPH_EVOLUTION",
        {
            "agent_attempt_id": "manager",
            "node_command_id": task.command_id,
            "agent_output_digest": response["output_digest"],
            "graph_evolution": copy.deepcopy(candidate["graph_evolution"]),
            "source_report_refs": context["source_report_refs"],
            "plugin_update": copy.deepcopy(update),
        },
    )
    evolve = next(row for row in data.attempts if row["attempt_id"] == "evolve")
    evolve.update(started_at=data.clock + 1, ended_at=data.clock + 1.2)
    data.clock += 1.2
    return state_root, data


def _audit(root, data):
    return audit_manager(
        root,
        data.attempts,
        data.attempt_payloads,
        data.facts,
        data.fact_payloads,
        data.transitions,
    )


@pytest.mark.parametrize("edit", [False, True])
def test_actual_grounding_git_update_and_report_source_are_read_only(tmp_path, edit):
    root, data = _fixture(tmp_path, edit=edit)
    before = copy.deepcopy(vars(data))
    files = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    assert _audit(root, data) == []
    assert vars(data) == before
    assert {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    } == files


@pytest.mark.parametrize(
    "tamper",
    [
        "missing_accept",
        "duplicate_accept",
        "open_source",
        "wrong_owner",
        "wrong_output",
        "changed_proposal",
        "foreign_role",
        "too_many_forks",
        "bool_forks",
        "duplicate_role",
        "false_limit",
        "disabled",
        "missing_report",
        "open_report",
        "wrong_report_kind",
        "future_report",
        "forged_report",
        "unlisted_report",
        "wrong_report_refs",
        "wrong_plugin_owner",
        "missing_plugin_commit",
        "wrong_base",
        "wrong_update_commit",
        "wrong_trigger_revision",
    ],
)
def test_manager_proposal_cannot_change_its_sources_limits_or_grounding_owner(
    tmp_path, tamper
):
    root, data = _fixture(tmp_path)
    observation = data.attempt_payloads["manager"]
    request = observation["request"]
    transition = data.transitions[-1]
    accepted = json.loads(transition["payload_json"])
    if tamper == "missing_accept":
        data.transitions.pop()
    elif tamper == "duplicate_accept":
        data.transition("ACCEPT_GRAPH_EVOLUTION", accepted)
    elif tamper == "open_source":
        data.attempts[-1]["state"] = "OPEN"
    elif tamper == "wrong_owner":
        request["role"] = "researcher"
    elif tamper == "wrong_output":
        accepted["agent_output_digest"] = "another-output"
    elif tamper == "changed_proposal":
        accepted["graph_evolution"][0]["reason"] = "Not in the source response."
    elif tamper in {"foreign_role", "too_many_forks", "bool_forks", "duplicate_role"}:
        changes = observation["response"]["typed_candidate"]["graph_evolution"]
        if tamper == "foreign_role":
            changes[0]["role"] = "foreign"
        elif tamper == "duplicate_role":
            changes.append(copy.deepcopy(changes[0]))
        else:
            changes[0]["effective_max_forks"] = True if tamper == "bool_forks" else 3
        accepted["graph_evolution"] = copy.deepcopy(changes)
    elif tamper in {"false_limit", "disabled", "unlisted_report"}:
        context = {
            "max_hypotheses_per_round": {
                "researcher": 3 if tamper == "false_limit" else 2
            },
            "effective_max_forks": {"researcher": 2},
            "source_report_refs": []
            if tamper == "unlisted_report"
            else ["report--response"],
            "enabled": tamper != "disabled",
        }
        _replace_input(request, "--manager-context", context)
    elif tamper == "missing_report":
        request["input_artifacts"] = [
            item
            for item in request["input_artifacts"]
            if item["ref"] != "report--response"
        ]
    elif tamper == "open_report":
        next(row for row in data.attempts if row["attempt_id"] == "report")["state"] = (
            "OPEN"
        )
    elif tamper == "wrong_report_kind":
        data.attempt_payloads["report"]["request"]["command"] = "HYPOTHESIS_UPDATE"
    elif tamper == "future_report":
        next(row for row in data.attempts if row["attempt_id"] == "report")[
            "ended_at"
        ] = data.clock + 1
    elif tamper == "forged_report":
        _replace_input(
            request, "report--response", {"typed_candidate": {"summary": "Forged."}}
        )
    elif tamper == "wrong_report_refs":
        accepted["source_report_refs"] = ["update--response"]
    elif tamper in {"wrong_plugin_owner", "missing_plugin_commit"}:
        from base64 import b64decode

        resources = json.loads(
            b64decode(
                next(
                    item
                    for item in request["input_artifacts"]
                    if item["ref"].endswith("--grounding-resources")
                )["content_base64"]
            )
        )
        resources["plugin"][
            "owner_kind" if tamper == "wrong_plugin_owner" else "commit"
        ] = "expert" if tamper == "wrong_plugin_owner" else "0" * 40
        _replace_input(request, "--grounding-resources", resources)
    elif tamper == "wrong_trigger_revision":
        request["task_context"]["ordinal"] += 1
    elif tamper == "wrong_base":
        observation["plugin_update"]["base_commit"] = observation["plugin_update"][
            "commit"
        ]
        accepted["plugin_update"] = copy.deepcopy(observation["plugin_update"])
    else:
        accepted["plugin_update"]["commit"] = "0" * 40
    transition["payload_json"] = json.dumps(accepted)
    assert _audit(root, data)


def test_future_synthesis_consumes_accepted_count_but_infrastructure_retry_keeps_prior_view(
    tmp_path,
):
    root, data = _fixture(tmp_path)
    data.agent(
        "next-synthesis",
        ResearchNodeKind.HYPOTHESIS_PROPOSAL,
        {"schema_version": 1, "hypotheses": [{"chain_id": "new", "candidate": {}}]},
        cycle=2,
        subkind=ResearchNodeSubkind.SYNTHESIS,
        inputs=(
            EmbeddedInput.from_bytes(
                "next--expert-config",
                "application/json",
                canonical_json({"effective_max_forks": 1}),
            ),
        ),
    )
    assert _audit(root, data) == []
    request = data.attempt_payloads["next-synthesis"]["request"]
    _replace_input(request, "--expert-config", {"effective_max_forks": 2})
    assert any(
        issue[0] == "manager_batch_binding_invalid" for issue in _audit(root, data)
    )


def test_disabled_no_change_manager_still_has_one_actual_acceptance(tmp_path):
    root, data = _fixture(tmp_path, edit=False)
    goal = next(
        value
        for value in data.fact_payloads.values()
        if value["fact_type"] == "GoalDefined"
    )
    goal["research_contract"]["methodology"]["controls"]["product"]["methodology"][
        "graph_evolution"
    ]["enabled"] = False
    observation = data.attempt_payloads["manager"]
    observation["response"]["typed_candidate"]["graph_evolution"] = []
    context = {
        "max_hypotheses_per_round": {"researcher": 2},
        "effective_max_forks": {"researcher": 2},
        "source_report_refs": ["report--response"],
        "enabled": False,
    }
    _replace_input(observation["request"], "--manager-context", context)
    value = json.loads(data.transitions[-1]["payload_json"])
    value["graph_evolution"] = []
    data.transitions[-1]["payload_json"] = json.dumps(value)
    assert _audit(root, data) == []


def test_infrastructure_retry_preserves_the_original_grounding_plugin_selection(
    tmp_path,
):
    root, data = _fixture(tmp_path)
    current = data.attempts[-1]
    lost = {
        **current,
        "attempt_id": "manager-lost",
        "started_at": current["started_at"] - 0.5,
        "ended_at": current["started_at"] - 0.2,
        "terminal_reason": "LOST",
    }
    current["attempt_ordinal"] = 2
    data.attempts.append(lost)
    request = copy.deepcopy(data.attempt_payloads["manager"]["request"])
    request["attempt_id"] = "manager-lost"
    data.attempt_payloads["manager-lost"] = {"request": request}
    assert _audit(root, data) == []
    from base64 import b64decode

    resources = json.loads(
        b64decode(
            next(
                item
                for item in request["input_artifacts"]
                if item["ref"].endswith("--grounding-resources")
            )["content_base64"]
        )
    )
    resources["plugin"]["commit"] = data.attempt_payloads["manager"]["plugin_update"][
        "commit"
    ]
    _replace_input(request, "--grounding-resources", resources)
    assert _audit(root, data)


@pytest.mark.parametrize("tamper", ["missing_all_manager", "open_manager", "self_before_manager", "fake_final_failure"])
def test_required_manager_cannot_be_omitted_between_report_and_self_evolution(tmp_path, tamper):
    root, data = _fixture(tmp_path)
    if tamper == "missing_all_manager":
        data.attempts = [row for row in data.attempts if row["attempt_id"] != "manager"]
        data.attempt_payloads.pop("manager")
        data.transitions = [row for row in data.transitions if row["command_type"] != "ACCEPT_GRAPH_EVOLUTION"]
    elif tamper == "self_before_manager":
        next(row for row in data.attempts if row["attempt_id"] == "evolve")["started_at"] = 1.0
    else:
        manager = next(row for row in data.attempts if row["attempt_id"] == "manager")
        manager.update(state="OPEN", terminal_reason=None, ended_at=None)
        data.transitions = [row for row in data.transitions if row["command_type"] != "ACCEPT_GRAPH_EVOLUTION"]
        data.attempt_payloads["manager"].pop("plugin_update")
        if tamper == "fake_final_failure":
            data.transition("AGENT_ATTEMPT_FINAL_FAILURE", {"agent_final_failure": True,
                "attempt_id": "manager", "failed_task_command_id": manager["command_id"]})
    assert any(issue[0] == "manager_closure_invalid" for issue in _audit(root, data))


def test_active_report_can_wait_for_manager_without_being_a_failure(tmp_path):
    root, data = _fixture(tmp_path)
    data.attempts = [row for row in data.attempts if row["attempt_id"] not in {"manager", "evolve"}]
    data.attempt_payloads.pop("manager")
    data.attempt_payloads.pop("evolve")
    data.transitions = [row for row in data.transitions if row["command_type"] != "ACCEPT_GRAPH_EVOLUTION"]
    assert _audit(root, data) == []


def test_manager_closure_allows_an_unobserved_open_process(tmp_path):
    root, data = _fixture(tmp_path)
    data.attempts.append({**data.attempts[0], "kind": "PROCESS", "attempt_id": "still-running-process",
                         "state": "OPEN", "terminal_reason": None, "ended_at": None})
    assert "still-running-process" not in data.attempt_payloads
    assert _audit(root, data) == []


def test_actual_exhausted_manager_allows_self_evolution_without_fake_proposal(tmp_path):
    root, data = _fixture(tmp_path)
    manager = next(row for row in data.attempts if row["attempt_id"] == "manager")
    manager["terminal_reason"] = "FAILED"
    data.attempt_payloads["manager"].pop("plugin_update")
    data.transitions = [row for row in data.transitions if row["command_type"] != "ACCEPT_GRAPH_EVOLUTION"]
    data.transition("AGENT_ATTEMPT_FINAL_FAILURE", {"agent_final_failure": True,
        "attempt_id": "manager", "failed_task_command_id": manager["command_id"]})
    next(row for row in data.attempts if row["attempt_id"] == "evolve").update(started_at=data.clock + 1, ended_at=data.clock + 1.2)
    assert _audit(root, data) == []
