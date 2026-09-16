from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from hypoloom.agent_aci import AgentCommand, EmbeddedInput, WorkspaceArtifact, validate_experiment_submission, validate_submission_snapshot
from hypoloom.agent_context import _submitted_workspace_files, preflight_origin, rendezvous_inputs, result_analysis_inputs
from hypoloom.artifacts import ArtifactPublisher
from hypoloom.expert_loop import ResearchNodeKey, ResearchNodeKind, ResearchNodeTask
from hypoloom.fake_agent import FakeAgent, default_request
from test_measurement_contract import declaration


@pytest.fixture
def submission_source():
    design = default_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT)
    request = replace(
        default_request(AgentCommand.EXPERIMENT_EXECUTION),
        method_family_id=design.method_family_id,
    )
    response = FakeAgent().invoke(design)
    arguments = {
        "candidate": response.typed_candidate,
        "workspace_files": [item.to_dict() for item in response.proposed_workspace_artifacts],
    }
    _, files, snapshot = validate_experiment_submission(request, arguments)
    submit_id = f"{request.command_id}--submit--initial"
    spec = {
        "fact_id": "submitted-spec",
        "run_id": request.run_id,
        "experiment_id": "submitted-experiment",
        "payload": {
            "component_id": request.component_id,
            "method_family_id": request.method_family_id,
            "execution_contract": {
                "source_agent_attempt_id": request.attempt_id,
                "source_agent_output_digest": snapshot["output_digest"],
                "source_submit_command_id": submit_id,
            },
        },
    }
    attempt = {
        "attempt_id": request.attempt_id,
        "run_id": request.run_id,
        "workflow_id": request.owner_workflow_id,
        "command_id": request.command_id,
        "attempt_ordinal": request.attempt_ordinal,
        "kind": "AGENT",
        "state": "OPEN",
        "terminal_reason": None,
        "observation_json": json.dumps({"request": request.to_dict()}),
    }
    data = {
        "tool": "submit_experiment",
        "execution_node_command_id": request.command_id,
        "predecessor_experiment_id": None,
        "arguments": arguments,
        "source_request": request.to_dict(),
        "source_snapshot": snapshot,
    }
    command = {
        "command_id": submit_id,
        "run_id": request.run_id,
        "workflow_id": spec["experiment_id"],
        "command_type": "CREATE_EXPERIMENT",
        "response_status": "accepted",
        "request_json": json.dumps(data),
    }
    store = SimpleNamespace(inspect=lambda sql, parameters: [command])
    return store, request, spec, attempt, command, files


@pytest.mark.parametrize("state,reason", [("OPEN", None), ("CLOSED", "LOST"), ("CLOSED", "SUCCEEDED")])
def test_submitted_source_needs_no_terminal_agent_response(submission_source, state, reason) -> None:
    store, request, spec, attempt, _, files = submission_source
    attempt.update(state=state, terminal_reason=reason)

    assert _submitted_workspace_files(store, request.run_id, spec, attempt) == files
    assert "response" not in json.loads(attempt["observation_json"])


@pytest.mark.parametrize("mismatch", [
    "accepted_command", "experiment", "run", "attempt_command", "attempt_ordinal",
    "source_digest", "method_family", "original_request", "snapshot_bytes",
])
def test_submitted_source_rejects_wrong_ownership_or_changed_source(submission_source, mismatch) -> None:
    store, request, spec, attempt, command, _ = submission_source
    if mismatch == "accepted_command":
        command["response_status"] = "rejected"
    elif mismatch == "experiment":
        command["workflow_id"] = "another-experiment"
    elif mismatch == "run":
        command["run_id"] = "another-run"
    elif mismatch == "attempt_command":
        attempt["command_id"] = "another-execution-node"
    elif mismatch == "attempt_ordinal":
        attempt["attempt_ordinal"] += 1
    elif mismatch == "source_digest":
        spec["payload"]["execution_contract"]["source_agent_output_digest"] = "0" * 64
    elif mismatch == "method_family":
        spec["payload"]["method_family_id"] = "another-family"
    elif mismatch == "original_request":
        original = replace(request, deadline="2031-01-01T00:00:00Z")
        attempt["observation_json"] = json.dumps({"request": original.to_dict()})
    else:
        data = json.loads(command["request_json"])
        data["source_snapshot"]["arguments"]["description"] = "changed after admission"
        command["request_json"] = json.dumps(data)

    with pytest.raises(ValueError, match="submitted experiment source"):
        _submitted_workspace_files(store, request.run_id, spec, attempt)


def test_submitted_source_cannot_fall_back_when_accepted_command_is_missing(submission_source) -> None:
    store, request, spec, attempt, _, _ = submission_source
    store.inspect = lambda sql, parameters: []
    attempt.update(state="CLOSED", terminal_reason="SUCCEEDED")

    with pytest.raises(ValueError, match="source command is unavailable"):
        _submitted_workspace_files(store, request.run_id, spec, attempt)


def test_analysis_rendezvous_uses_the_open_agents_admitted_submission(
    submission_source, tmp_path: Path,
) -> None:
    _, request, spec, attempt, command, files = submission_source
    publisher = ArtifactPublisher(tmp_path / "artifacts")
    result_root = publisher.root / "submitted-result"
    result_root.mkdir(parents=True)
    (result_root / "result.json").write_text('{"outcome":"SUCCESS"}')
    process = {
        "kind": "PROCESS", "attempt_id": "submitted-process",
        "workflow_id": spec["experiment_id"], "experiment_id": spec["experiment_id"],
    }

    def inspect(sql, parameters):
        if "workflow_commands" in sql:
            return [command]
        if "execution_artifacts" in sql:
            return [{"artifact_id": "submitted-artifact", "path": "submitted-result"}]
        assert "workflow_transitions" in sql
        return [{"payload_json": json.dumps({
            "rendezvous_origin": {"source_attempt_id": process["attempt_id"]},
            "outcome": {"outcome": "SUCCESS"},
        })}]

    store = SimpleNamespace(
        inspect=inspect,
        find_fact=lambda *args: spec,
        get_attempt=lambda attempt_id: attempt if attempt_id == attempt["attempt_id"] else None,
    )
    inputs = rendezvous_inputs(store, publisher, request.run_id, process, {"returncode": 0})

    assert inputs[f"{request.attempt_id}--experiment.py"].content_bytes() == files["experiment.py"]
    assert attempt["state"] == "OPEN"


def test_preflight_uses_distinct_submit_identities_for_the_same_open_agent(submission_source) -> None:
    _, request, _, attempt, command, _ = submission_source
    contract = SimpleNamespace(role_ids=(request.role, "reviewer"))
    initial, _ = preflight_origin(
        request, contract, None, set(), "reviewer", source_submit_command_id=command["command_id"],
        experiment_id="submitted-experiment",
    )
    repaired, payload = preflight_origin(
        request, contract, None, set(), "reviewer", source_submit_command_id="repair-submit",
        experiment_id="repaired-experiment",
    )

    assert initial.rendezvous_id != repaired.rendezvous_id
    assert repaired.rendezvous_id == "repaired-experiment--preflight-rendezvous"
    assert initial.source_attempt_id == repaired.source_attempt_id == request.attempt_id
    assert repaired.signal.context_refs == (
        "repaired-experiment--submission", "repaired-experiment--experiment.py",
    )
    assert payload["source_attempt_id"] == request.attempt_id
    assert attempt["state"] == "OPEN"
    with pytest.raises(ValueError, match="no validation Attempt"):
        preflight_origin(
            request, contract, "synthetic-validation", set(), "reviewer",
            source_submit_command_id=command["command_id"], experiment_id="submitted-experiment",
        )


@pytest.mark.parametrize("state,reason", [("OPEN", None), ("CLOSED", "LOST")])
def test_preflight_reads_each_owned_submission_without_conflating_repaired_source(
    submission_source, state, reason,
) -> None:
    _, request, _, attempt, initial, initial_files = submission_source
    attempt.update(state=state, terminal_reason=reason)
    data = json.loads(initial["request_json"])
    arguments = data["arguments"]
    repaired_source = b"print('repaired submit source')\n"
    arguments["workspace_files"] = [
        WorkspaceArtifact.from_bytes(item["path"], repaired_source if item["path"] == "experiment.py"
            else WorkspaceArtifact.from_dict(item).content_bytes()).to_dict()
        for item in arguments["workspace_files"]
    ]
    _, _, data["source_snapshot"] = validate_experiment_submission(request, arguments)
    repaired = {**initial, "command_id": "repair-submit", "workflow_id": "repaired-experiment",
                "request_json": json.dumps(data)}

    def inspect(sql, parameters):
        assert "response_status='accepted'" in sql
        assert "json_extract(request_json,'$.source_request.attempt_id')=?" in sql
        assert parameters == (request.run_id, request.attempt_id)
        return [initial, repaired]

    inputs = rendezvous_inputs(
        SimpleNamespace(inspect=inspect), None, request.run_id, attempt,
        json.loads(attempt["observation_json"]),
    )

    assert inputs[f"{initial['workflow_id']}--experiment.py"].content_bytes() == initial_files["experiment.py"]
    assert inputs["repaired-experiment--experiment.py"].content_bytes() == repaired_source
    submission = json.loads(inputs["repaired-experiment--submission"].content_bytes())
    assert submission == {"source_submit_command_id": "repair-submit",
        "experiment_id": "repaired-experiment", "typed_candidate": arguments["candidate"]}
    assert len(inputs) == 4
    assert f"{request.attempt_id}--response" not in inputs


def test_execution_preflight_keeps_goal_and_component_config_projections(submission_source) -> None:
    _, request, _, attempt, _, _ = submission_source
    goal = {"objective": "one component"}
    goal_ref = "source--fact-goal"
    config_ref = "source--expert-config"
    input_payloads = {
        goal_ref: {"fact_id": "goal-fact", "fact_type": "GoalDefined", "payload": {
            "research_contract": {"goal": goal, "components": ["unrelated-component"]}}},
        config_ref: {"component": {"id": request.component_id}, "protocol": {"kind": "metric"},
            "required_experiment_spec": {"method": "fixed"}, "execution": {},
            "goal": "unrelated-goal", "protocol_catalog": {"unrelated": {}}},
    }
    inputs = tuple(EmbeddedInput.from_bytes(ref, "application/json", json.dumps(payload).encode())
                   for ref, payload in input_payloads.items())
    request = replace(request, input_artifacts=inputs, immutable_context_refs=tuple(input_payloads))
    observation = {"request": request.to_dict()}
    attempt["observation_json"] = json.dumps(observation)

    projected = rendezvous_inputs(
        SimpleNamespace(inspect=lambda *_args: []), None, request.run_id, attempt, observation,
    )

    assert json.loads(projected[goal_ref].content_bytes()) == {
        "fact_id": "goal-fact", "fact_type": "GoalDefined", "goal": goal,
    }
    config = json.loads(projected[config_ref].content_bytes())
    assert config["component"] == {"id": request.component_id}
    assert "goal" not in config and "protocol_catalog" not in config


@pytest.fixture
def generic_submission(submission_source):
    store, request, spec, attempt, command, _ = submission_source
    request = replace(request, budget=replace(request.budget, max_output_bytes=2 * 1024 * 1024))
    attempt["observation_json"] = json.dumps({"request": request.to_dict()})
    data = json.loads(command["request_json"])
    data["source_request"] = request.to_dict()
    measurement = replace(declaration(), parser_files=("parse_metrics.py", "parser/helpers.py"))
    data["arguments"]["candidate"].update(measurement=measurement.to_dict(),
        execution_argv=["/bin/sh", "/input/experiment.sh"], metrics=[measurement.to_dict()["metric"]])
    files = {"experiment.sh": b"echo raw > timings.csv\n", "parse_metrics.py": b"print('parser')\n",
             "parser/helpers.py": b"# complete parser helper\n" * 5000, "implementation.patch": b""}
    data["arguments"]["workspace_files"] = [WorkspaceArtifact.from_bytes(name, content).to_dict()
                                                for name, content in files.items()]
    _, _, data["source_snapshot"] = validate_experiment_submission(request, data["arguments"])
    command["request_json"] = json.dumps(data)
    spec["payload"]["execution_contract"]["source_agent_output_digest"] = data["source_snapshot"]["output_digest"]
    validated = validate_submission_snapshot(request, data["source_snapshot"])
    return store, request, spec, attempt, command, files, validated


def test_generic_preflight_refs_match_actual_shell_and_all_parser_sources(generic_submission):
    store, request, _, attempt, command, files, validated = generic_submission
    inputs = rendezvous_inputs(store, None, request.run_id, attempt, json.loads(attempt["observation_json"]))
    origin, _ = preflight_origin(request, SimpleNamespace(role_ids=(request.role, "reviewer")), None,
        set(), "reviewer", source_submit_command_id=command["command_id"],
        experiment_id=command["workflow_id"], source_submission=validated)
    prefix = command["workflow_id"] + "--"
    assert set(origin.signal.context_refs) == set(inputs)
    assert prefix + "experiment.py" not in inputs
    for name in ("experiment.sh", "parse_metrics.py", "parser/helpers.py"):
        assert inputs[prefix + name].content_bytes() == files[name]
    command_view = json.loads(inputs[prefix + "source-command"].content_bytes())
    assert command_view["execution_argv"] == ["/bin/sh", "/input/experiment.sh"]
    assert {item["path"] for item in command_view["sources"]} == set(files) - {"implementation.patch"}


def test_generic_result_analysis_and_review_use_exact_open_submission_sources(
    generic_submission, tmp_path: Path,
):
    _, request, spec, attempt, command, files, _ = generic_submission
    experiment = spec["experiment_id"]
    spec["execution_id"] = "execution-1"
    outcome = {"fact_id": "outcome-1", "experiment_id": experiment, "execution_id": "execution-1",
               "artifact_id": "artifact-1", "payload": {"component_id": request.component_id, "outcome": "SUCCESS"}}
    process = {"attempt_id": "process-1", "kind": "PROCESS", "state": "CLOSED",
               "workflow_id": experiment, "experiment_id": experiment, "execution_id": "execution-1",
               "observation_json": '{"returncode":0}'}
    terminal = {"attempt_id": process["attempt_id"], "outcome": {"outcome": "SUCCESS"},
                "rendezvous_origin": {"source_attempt_id": process["attempt_id"]}}
    artifact = {"artifact_id": "artifact-1", "path": "published-result"}

    def inspect(sql, parameters):
        if "workflow_commands" in sql:
            return [command]
        if "execution_artifacts" in sql:
            return [artifact]
        if "revision=1" in sql:
            return [{"workflow_id": experiment, "payload_json": json.dumps({
                "component_id": request.component_id, "role": request.role,
                "expert_cycle": 1, "experiment_ordinal": 1})}]
        return [{"payload_json": json.dumps(terminal)}]

    store = SimpleNamespace(inspect=inspect, find_fact=lambda *_args: spec,
        list_facts=lambda *_args: [outcome],
        get_attempt=lambda identity: attempt if identity == attempt["attempt_id"] else process)
    publisher = ArtifactPublisher(tmp_path / "artifacts")
    (publisher.root / "published-result").mkdir()
    (publisher.root / "published-result/result.json").write_text('{"value":9,"baseline":10}')
    task = ResearchNodeTask(key=ResearchNodeKey(run_id=request.run_id,
        component_id=request.component_id, expert_slot=request.role, cycle=1,
        node_kind=ResearchNodeKind.RESULT_ANALYSIS, experiment_ordinal=1))
    analysis = {item.ref: item for item in result_analysis_inputs(store, publisher, request.run_id, task)}
    review = rendezvous_inputs(store, publisher, request.run_id, process, {"returncode": 0})
    for name in ("experiment.sh", "parse_metrics.py", "parser/helpers.py"):
        assert analysis[f"{task.command_id}--current-{name}"].content_bytes() == files[name]
        assert review[f"{attempt['attempt_id']}--{name}"].content_bytes() == files[name]
    assert not any(ref.endswith("--experiment.py") or ref.endswith("--current-experiment.py")
                   for ref in (*analysis, *review))
    assert attempt["state"] == "OPEN"
