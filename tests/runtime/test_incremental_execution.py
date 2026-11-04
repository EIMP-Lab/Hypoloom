"""Host-owned CPU work fails after one unit, then reuses its publication."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sqlite3

import pytest

from hypoloom.agent_aci import AgentACIError, WorkspaceArtifact, validate_experiment_submission
from hypoloom.execution_reuse import prior_executions
from hypoloom.oracle import Oracle
from test_host_experiment_tools import execution, _until, _terminal
from test_deferred_measurement_host import deferred_host
from hypoloom.expert_loop import ResearchNodeTask, ResearchNodeKind


def _program(arguments):
    arguments = deepcopy(arguments)
    sources = {item.path: item.content_bytes() for raw in arguments["workspace_files"]
               if (item := WorkspaceArtifact.from_dict(raw))}
    helper = Path(__file__).parents[2] / "hypoloom/work_units.py"
    body = '''
import sys
if len(sys.argv) > 1 and sys.argv[1] == '--unit':
    name = sys.argv[2]
    print('ACTUAL_UNIT_EFFECT:' + name, flush=True)
    if name == 'b' and not json.loads(os.environ.get('HYPOLOOM_REUSE_INPUTS', '[]')):
        raise SystemExit(7)
    Path('value.json').write_text(json.dumps({'value': 42, 'unit': name}))
    raise SystemExit(0)
units = WorkUnits('/output')
for prior in json.loads(os.environ.get('HYPOLOOM_REUSE_INPUTS', '[]')):
    try:
        (Path(prior['path']) / 'forbidden-write').write_text('bad')
    except OSError as error:
        assert error.errno in (13, 30), error
        print('REUSE_INPUT_READ_ONLY', flush=True)
    else:
        raise AssertionError('reuse publication was writable')
for name in ('a', 'b'):
    units.run(name, argv=[sys.executable, '/input/experiment.py', '--unit', name],
        context={'implementation': 'fixture:calculation-v1', 'data': 'fixture:input-v1',
                 'configuration': {'steps': 2}, 'repetition': None, 'protocol': 'fixture:validation'},
        outputs=['value.json'], timeout_seconds=5, stage='pilot', purpose='exploration',
        basis='two independent deterministic calculations')
'''
    tail = sources.get("experiment.py", b"Path('timings.csv').write_text('10,8\\n10,9\\n10,100\\n')\n")
    sources["experiment.py"] = helper.read_bytes() + body.encode() + tail
    if arguments["candidate"].get("measurement") is not None:
        arguments["candidate"]["execution_argv"] = ["python", "/input/experiment.py"]
    arguments["workspace_files"] = [WorkspaceArtifact.from_bytes(name, content).to_dict() for name, content in sources.items()]
    return arguments


def test_actual_host_failure_publication_and_partial_replacement(deferred_host, tmp_path):
    host = deferred_host
    _until(host, lambda: bool(host.local_execution_agents))
    request = next(iter(host.local_execution_agents.values()))
    host.local_execution_agents.clear()
    design = ResearchNodeTask(key=replace(request.task_context.key, node_kind=ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT))
    response = json.loads(host._expert_success(design)["observation_json"])["response"]
    arguments = {"candidate": response["typed_candidate"], "workspace_files": response["proposed_workspace_artifacts"]}
    arguments = _program(arguments)
    first = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    _until(host, lambda: _terminal(host, first["exp_id"]) and bool(host.store.list_facts(request.run_id, "EvidenceLinked")))
    view = host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": first["exp_id"]})
    assert view["outcome"]["outcome"] == "EXECUTION_FAILURE"
    previous = prior_executions(host, request.run_id, request.component_id, request.role)
    assert previous[0]["experiment_id"] == first["exp_id"]
    original = host._outcome_publication(request.run_id, view["outcome"]["outcome_id"], view["outcome"]["artifact_id"])[1]
    assert json.loads((original / "raw/work_units/a/record.json").read_text())["status"] == "completed"
    assert json.loads((original / "raw/work_units/b/record.json").read_text())["status"] == "failed"
    replacement = {**arguments, "replaces_experiment_id": first["exp_id"], "reuse_experiments": [first["exp_id"]]}
    second = host.handle_agent_tool(request.attempt_id, "submit_experiment", replacement)
    assert second["source_comparison"][0]["same_submitted_sources"]
    assert second["source_comparison"][0]["reuse_requested"]
    assert host.handle_agent_tool(request.attempt_id, "submit_experiment", replacement) == second
    _until(host, lambda: _terminal(host, second["exp_id"]))
    view = host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": second["exp_id"]})
    assert view["outcome"]["outcome"] == "SUCCESS", view
    root = host._outcome_publication(request.run_id, view["outcome"]["outcome_id"], view["outcome"]["artifact_id"])[1]
    assert json.loads((root / "raw/work_units/a/record.json").read_text())["reused_from"]["experiment_id"] == first["exp_id"]
    assert json.loads((root / "raw/work_units/b/record.json").read_text())["reused_from"] is None
    logs = [host._experiment_tool_log(item["exp_id"]) for item in (first, second)]
    assert logs[0].count("ACTUAL_UNIT_EFFECT:a") == 1 and "ACTUAL_UNIT_EFFECT:a" not in logs[1]
    assert all(log.count("ACTUAL_UNIT_EFFECT:b") == 1 for log in logs)
    assert "REUSE_INPUT_READ_ONLY" in logs[1]
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok, report.to_dict()
    processes = [row for row in host.store.list_attempts() if row["kind"] == "PROCESS"]
    assert len(processes) == 2 and all(row["state"] == "CLOSED" for row in processes)
    from hypoloom.runner import process_group_exists
    assert all(not process_group_exists(row["process_group_id"]) for row in processes)
    # A copied database cannot redirect the frozen reference while preserving
    # scientific acceptance. Do not alter the original run to exercise this.
    copied = tmp_path / "wrong-reuse.sqlite"
    with sqlite3.connect(host.store.database_path) as source, sqlite3.connect(copied) as destination:
        source.backup(destination)
        fact_id, payload = destination.execute("SELECT fact_id,payload_json FROM research_facts WHERE fact_type='ExperimentPreregistered' AND experiment_id=?", (second["exp_id"],)).fetchone()
        for (name,) in destination.execute("SELECT name FROM sqlite_schema WHERE type='trigger' AND tbl_name='research_facts'").fetchall():
            destination.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
        value = json.loads(payload)
        environment = dict(value["execution_contract"]["environment"])
        environment["HYPOLOOM_REUSE_INPUTS"] = "[]"
        value["execution_contract"]["environment"] = sorted(environment.items())
        destination.execute("UPDATE research_facts SET payload_json=? WHERE fact_id=?", (json.dumps(value), fact_id))
    report = Oracle(copied, host.publisher.root).audit(request.run_id)
    assert "execution_reuse_invalid" in {issue.code for issue in report.issues}
    host._finish_fake_agent(request, host.agent.invoke(request))
    _until(host, lambda: host.store.latest_transition(request.run_id)["state"] == "COMPLETED")
    assert Oracle(host.store.database_path, host.publisher.root).audit(request.run_id).ok
    analyses = [json.loads(row["observation_json"])["request"] for row in host.store.list_attempts()
                if row["kind"] == "AGENT" and row["state"] == "CLOSED"
                and json.loads(row["observation_json"])["request"]["command"] == "RESULT_ANALYSIS"]
    from hypoloom.agent_aci import AgentRequest
    assert any(json.loads(item.content_bytes()) == second["source_comparison"]
               for analysis in analyses for item in AgentRequest.from_dict(analysis).input_artifacts
               if item.ref.endswith("--current-source-comparison"))
    from hypoloom.workflow_host import WorkflowHost
    state_root = host.state_root
    host.close()
    reopened = WorkflowHost(state_root)
    try:
        reopened.initialize()
        inputs = reopened._execution_inputs(request.run_id, second["exp_id"])
        mounts, _, _ = reopened._execution_mounts(request.run_id, inputs[0], inputs[6], processes[-1]["attempt_id"])
        assert (original, "/hypoloom-reuse/1") in mounts
        assert Oracle(reopened.store.database_path, reopened.publisher.root).audit(request.run_id).ok
        assert len([row for row in reopened.store.list_attempts() if row["kind"] == "PROCESS"]) == 2
    finally:
        reopened.close()


def test_reuse_ownership_and_existing_publication_integrity(execution):
    from hypoloom.execution_reuse import submission_inputs
    from hypoloom.workflow_host import OperationalHalt
    host, request, arguments = execution
    first = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    _until(host, lambda: _terminal(host, first["exp_id"]))
    selection = {"reuse_experiments": [first["exp_id"]]}
    assert submission_inputs(host, request, selection)
    foreign_task = replace(request.task_context, key=replace(request.task_context.key, expert_slot="foreign"))
    foreign = replace(request, role="foreign", task_context=foreign_task, command_id=foreign_task.command_id)
    with pytest.raises(AgentACIError, match="owner"):
        submission_inputs(host, foreign, selection)
    view = host._experiment_tool_view(request.run_id, first["exp_id"])
    root = host._outcome_publication(request.run_id, view["outcome"]["outcome_id"], view["outcome"]["artifact_id"])[1]
    (root / "result.json").write_text("{}")
    with pytest.raises(OperationalHalt, match="verification"):
        submission_inputs(host, request, selection)


def test_reuse_rejects_unknown_or_nonterminal_source_before_creating_work(execution):
    host, request, arguments = execution
    with pytest.raises(AgentACIError, match="terminal experiment"):
        host.handle_agent_tool(request.attempt_id, "submit_experiment", {**arguments, "reuse_experiments": ["foreign"]})
    assert not host.store.list_workflow_heads(request.run_id, "EXPERIMENT")
    first = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    from hypoloom.execution_reuse import submission_inputs
    with pytest.raises(AgentACIError, match="terminal experiment"):
        submission_inputs(host, request, {"reuse_experiments": [first["exp_id"]]})


def test_completed_negative_cannot_be_resubmitted_as_technical_repair(execution):
    host, request, arguments = execution
    changed = deepcopy(arguments)
    changed["workspace_files"] = [WorkspaceArtifact.from_bytes(item.path,
        item.content_bytes().replace(b'"accuracy": 0.55', b'"accuracy": 0.50')).to_dict()
        for raw in changed["workspace_files"] if (item := WorkspaceArtifact.from_dict(raw))]
    first = host.handle_agent_tool(request.attempt_id, "submit_experiment", changed)
    _until(host, lambda: _terminal(host, first["exp_id"]) and bool(host.store.list_facts(request.run_id, "EvidenceLinked")))
    view = host._experiment_tool_view(request.run_id, first["exp_id"])
    assert view["outcome"]["outcome"] == "CLEAN_REFUTATION"
    with pytest.raises(AgentACIError, match="operational repair"):
        host.handle_agent_tool(request.attempt_id, "submit_experiment", {
            **changed, "replaces_experiment_id": first["exp_id"], "reuse_experiments": [first["exp_id"]]})
    assert len([row for row in host.store.list_attempts() if row["kind"] == "PROCESS"]) == 1


@pytest.mark.parametrize("value", [["a", "a"], [True], "a", [str(i) for i in range(17)]])
def test_reuse_input_validation_and_legacy_rejection(execution, value):
    host, request, arguments = execution
    with pytest.raises(AgentACIError, match="reuse_experiments"):
        validate_experiment_submission(request, {**arguments, "reuse_experiments": value})
    with pytest.raises(AgentACIError):
        validate_experiment_submission(replace(request, schema_version=31), {**arguments, "reuse_experiments": []})
