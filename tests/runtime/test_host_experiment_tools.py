from __future__ import annotations

import json
import hashlib
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from hypoloom.agent_aci import AgentACIError, AgentRequest, WorkspaceArtifact, with_output_digest
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand, AttemptTerminalReason
from hypoloom.expert_loop import ResearchNodeKind, ResearchNodeTask, ResearchNodeKey
from hypoloom.fake_agent import FakeAgent
from hypoloom.oracle import Oracle
from hypoloom.state_store import IdentityConflict
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_expert_loop_host import _pilot_payload


def _until(host: WorkflowHost, predicate: object) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if predicate(): return
        host.tick()
        time.sleep(0.005)
    raise AssertionError("Host did not reach the expected observation")


@pytest.fixture
def execution(tmp_path: Path, request):
    with tempfile.TemporaryDirectory(prefix="hypoloom-host-tools-", dir="/var/tmp") as directory:
        host = WorkflowHost(tmp_path / "state")
        host.initialize()
        try:
            payload = _pilot_payload(tmp_path, Path(directory), "host-tools")
            if getattr(request, "param", None) == "campaign":
                contract = payload["research_contract"]
                contract["roles"].append({**contract["roles"][0], "id": "reviewer"})
                contract["budget"]["max_active_hypotheses"] = 1
                contract["methodology"]["controls"]["experiment_campaigns"] = {
                    "enabled": True, "required_roles": ["preflight", "analyze"],
                    "role_owners": {"*": {"preflight": "reviewer", "analyze": "reviewer"}}}
            if getattr(request, "param", None) == "evaluation_summary":
                contract = payload["research_contract"]
                component = contract["components"][0]
                component["required_artifacts"].append("evaluation_summary")
                component["protocol"] = "evaluation-summary-test"
                contract["protocols"][component["protocol"]] = {
                    "result_kind": "metric",
                    "requirements": {
                        "required_artifacts": list(component["required_artifacts"]),
                        "artifact_validators": [{
                            "type": "json_contract", "artifact": "evaluation_summary",
                            "required_paths": ["cells"],
                        }],
                    },
                }
            result = host.handle(ControlRequest(schema_version=1, request_id="create-tools", expected_revision=0,
                command="CREATE_RUN", payload=payload))
            assert result.status == "accepted", result
            _until(host, lambda: bool(host.local_execution_agents))
            request = next(iter(host.local_execution_agents.values()))
            # The test supplies this fake Agent's individual tool invocations.
            host.local_execution_agents.clear()
            assert not host.store.list_workflow_heads(request.run_id, "EXPERIMENT")
            assert isinstance(request.task_context, ResearchNodeTask)
            key = request.task_context.key
            design = ResearchNodeTask(key=ResearchNodeKey(**{**key.to_dict(),
                "node_kind": ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT, "subkind": key.subkind}))
            accepted = host._expert_success(design)
            assert accepted is not None
            response = json.loads(accepted["observation_json"])["response"]
            arguments = {"candidate": response["typed_candidate"], "workspace_files": response["proposed_workspace_artifacts"]}
            yield host, request, arguments
        finally:
            host.close()


def _terminal(host: WorkflowHost, experiment_id: str) -> bool:
    head = host.store.latest_transition(experiment_id)
    return head is not None and head["state"] == "TERMINAL"


def test_execution_mcp_advertises_accepted_candidate_and_runs_it(execution, tmp_path):
    from hypoloom import host_tools_server
    from hypoloom.claude_agent_host import _output_schema
    from hypoloom.native_output import decode_output

    host, request, arguments = execution
    schema_path = tmp_path / "output-schema.json"
    schema_path.write_text(json.dumps(_output_schema(request, tool_workspace=True)))
    completed = subprocess.run(
        [sys.executable, host_tools_server.__file__, "--socket", str(tmp_path / "unused.sock"),
         "--scope", "execution", "--output-schema", str(schema_path)],
        input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n",
        text=True, capture_output=True, timeout=5, check=True)
    tools = json.loads(completed.stdout)["result"]["tools"]
    submit = next(tool for tool in tools if tool["name"] == "submit_experiment")
    schema = submit["inputSchema"]["properties"]["candidate"]
    design = next(AgentRequest.from_dict(observation["request"])
        for attempt in host.store.list_attempts() if attempt["kind"] == "AGENT"
        for observation in [json.loads(attempt["observation_json"])]
        if observation["request"]["command"] == "EXPERIMENT_DESIGN_IMPLEMENT")
    assert schema == _output_schema(design)["properties"]["typed_candidate"]
    assert decode_output(json.dumps(arguments["candidate"]), schema) == arguments["candidate"]
    for candidate in (
        {key: value for key, value in arguments["candidate"].items() if key != "owner_role"},
        {**arguments["candidate"], "owner_role": "foreign-role"},
        {**arguments["candidate"], "execution_argv": ["python", "/input/experiment.py"]},
        {"summary": "execution finished", "supporting_refs": []},
    ):
        with pytest.raises(ValueError):
            decode_output(json.dumps(candidate), schema)
    accepted = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    _until(host, lambda: _terminal(host, accepted["exp_id"]))
    result = host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": accepted["exp_id"]})
    assert result["outcome"]["outcome"] == "SUCCESS"
    processes = [row for row in host.store.list_attempts() if row["kind"] == "PROCESS"]
    assert processes and all(row["state"] == "CLOSED" for row in processes)
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok, report.to_dict()


def test_submit_is_creation_and_replay_is_exactly_once(execution) -> None:
    host, request, arguments = execution
    first = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    second = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    assert first == second
    assert len(host.store.list_workflow_heads(request.run_id, "EXPERIMENT")) == 1
    assert host.store.get_attempt(request.attempt_id)["state"] == "OPEN"
    with pytest.raises(IdentityConflict, match="different immutable candidate"):
        host.handle_agent_tool(request.attempt_id, "submit_experiment", {**arguments, "description": "changed"})
    _until(host, lambda: bool(host.running))
    assert host.store.get_attempt(request.attempt_id)["state"] == "OPEN"
    assert any(row["kind"] == "PROCESS" and row["state"] == "OPEN" for row in host.store.list_attempts())
    _until(host, lambda: _terminal(host, first["exp_id"]))
    result = host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": first["exp_id"], "max_wait_seconds": 0})
    assert result["terminal"] is True
    assert result["outcome"]["outcome"] == "SUCCESS"
    source = host.store.find_fact(request.run_id, "ExperimentPreregistered", first["exp_id"])["payload"]["execution_contract"]
    assert source["source_agent_attempt_id"] == request.attempt_id
    assert source["source_submit_command_id"]
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok, report.to_dict()


@pytest.mark.parametrize("execution", ["evaluation_summary"], indirect=True)
def test_final_report_receives_published_accepted_evaluation_summary(execution) -> None:
    host, request, arguments = execution
    accepted = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    _until(host, lambda: _terminal(host, accepted["exp_id"]))
    result = host.handle_agent_tool(request.attempt_id, "wait_experiment", {
        "exp_id": accepted["exp_id"]})
    assert result["outcome"]["outcome"] == "SUCCESS"
    host._finish_fake_agent(request, FakeAgent().invoke(request))
    _until(host, lambda: host.store.latest_transition(request.run_id)["state"] == "COMPLETED")

    report_attempt = next(row for row in host.store.list_attempts()
        if row["kind"] == "AGENT"
        and json.loads(row["observation_json"])["request"]["command"] == "FINAL_REPORT")
    report_request = AgentRequest.from_dict(
        json.loads(report_attempt["observation_json"])["request"])
    summaries = [item for item in report_request.input_artifacts
        if item.ref.endswith("--accepted-evaluation-summary")]
    assert len(summaries) == 1
    assert json.loads(summaries[0].content_bytes())["cells"]
    context = json.loads(next(item.content_bytes()
        for item in report_request.input_artifacts
        if item.ref.endswith("--final-report-context")))
    assert context["accepted_evaluation_summaries"] == {
        "byte_limit": 1024 * 1024,
        "bytes_included": len(summaries[0].content_bytes()),
        "included": [{
            "outcome_id": result["outcome"]["outcome_id"],
            "artifact_id": result["outcome"]["artifact_id"],
            "refs": [summaries[0].ref],
        }],
        "omitted": [],
    }
    report = Oracle(host.store.database_path, host.publisher.root).audit(
        request.run_id)
    assert report.ok, report.to_dict()


@pytest.mark.parametrize("as_shell_string", [False, True])
def test_repeated_argv_values_survive_submit_preregister_and_real_cpu(execution, as_shell_string):
    import shlex
    host, request, arguments = execution
    argv = ["python", "/input/experiment.py", "--seeds", "0", "1", "2", "3", "4",
            "--epochs", "32", "--batch-size", "32", "--audit-batch", "2"]
    files = [WorkspaceArtifact.from_bytes(item["path"], WorkspaceArtifact.from_dict(item).content_bytes()
        + (f"\nimport sys\nassert sys.argv[1:] == {argv[2:]!r}\n".encode()
           if item["path"] == "experiment.py" else b"")).to_dict() for item in arguments["workspace_files"]]
    accepted = host.handle_agent_tool(request.attempt_id, "submit_experiment", {
        **arguments, "workspace_files": files, "command": shlex.join(argv) if as_shell_string else argv})
    host.tick()
    assert not host.dispatch_errors, host.dispatch_errors
    _until(host, lambda: _terminal(host, accepted["exp_id"]))
    spec = host.store.find_fact(request.run_id, "ExperimentPreregistered", accepted["exp_id"])
    assert spec["payload"]["execution_contract"]["argv"] == argv
    processes = [row for row in host.store.list_attempts(workflow_id=accepted["exp_id"]) if row["kind"] == "PROCESS"]
    assert len(processes) == 1 and processes[0]["terminal_reason"] == "SUCCEEDED"
    result = host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": accepted["exp_id"]})
    assert result["terminal"] and result["outcome"]["outcome"] == "SUCCESS"
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok, report.to_dict()


def test_quarantine_closes_model_owner_without_settling_its_successful_process(execution, monkeypatch):
    from hypoloom.claude_agent_host import AgentHostTerminalReason
    from hypoloom.runner import process_group_exists
    host, request, arguments = execution
    accepted = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    _until(host, lambda: bool(host.running))
    cancelled = []
    cleaned = []
    handle = SimpleNamespace(request=request, tool_workspace=None)
    observation = SimpleNamespace(returncode=-15, pid=None, process_group_id=None,
        terminal_reason=AgentHostTerminalReason.CANCELLED, failure_code="cancelled",
        diagnostics={"cost_usd": None}, response=None)
    host.running_agents[request.attempt_id] = handle
    host.claude_agent_host = SimpleNamespace(
        cancel=lambda h: cancelled.append(h), poll=lambda h: observation if h in cancelled else None,
        cleanup=lambda h: cleaned.append(h))
    try:
        def fail_dispatch(run):
            raise ValueError("original dispatch failure")
        monkeypatch.setattr(host, "_drive_active", fail_dispatch)
        host.tick()
        assert request.run_id in host.dispatch_errors
        host.tick()
        assert cancelled == [handle] and cleaned == [handle]
        attempt = host.store.get_attempt(request.attempt_id)
        assert attempt["state"] == "CLOSED" and attempt["terminal_reason"] == "FAILED"
        evidence = json.loads(attempt["observation_json"])
        assert evidence["failure_code"] == "dispatch_paused" and evidence["retry_class"] is None
        assert evidence["diagnostics"]["cost_usd"] is None
        _until(host, lambda: not host.running)
        view = host._experiment_tool_view(request.run_id, accepted["exp_id"], include_log=False)
        assert view["status"] == "blocked" and view["terminal"] is False
        assert view["state"] == "EVALUATING"
        assert not host.store.list_facts(request.run_id, "CanonicalOutcomeAccepted")
        processes = [row for row in host.store.list_attempts() if row["kind"] == "PROCESS"]
        assert processes[-1]["terminal_reason"] == "SUCCEEDED"
        assert all(not process_group_exists(row["process_group_id"]) for row in processes)
        before = len(host.store.list_attempts())
        for _ in range(4): host.tick()
        assert len(host.store.list_attempts()) == before
        assert host.store.latest_transition(request.run_id)["state"] == "ACTIVE"
        status = host.handle(ControlRequest(1, "paused-status", None, "STATUS", {"run_id": request.run_id}))
        assert status.status == "accepted" and status.result["dispatch_error"]
    finally:
        host.claude_agent_host = None
        host.running_agents.clear()
    # Only the test's explicit cancellation may drain retained experiment output.
    reply = host.handle(ControlRequest(1, "cleanup-paused-test", None, "CANCEL",
        {"run_id": request.run_id, "cause": "isolated test cleanup"}))
    assert reply.status == "accepted"
    _until(host, lambda: host.store.latest_transition(request.run_id)["state"] == "CANCELLED")
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok, report.to_dict()


def test_terminal_elapsed_stops_at_the_recorded_process_end(execution, monkeypatch):
    host, request, arguments = execution
    submitted = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    _until(host, lambda: _terminal(host, submitted["exp_id"]))
    attempts = host.store.list_attempts(workflow_id=submitted["exp_id"])
    process = next(row for row in attempts if row["kind"] == "PROCESS")
    expected = process["ended_at"] - process["started_at"]
    for after in (60, 3600):
        with monkeypatch.context() as clock:
            clock.setattr(time, "time", lambda: process["ended_at"] + after)
            result = host._experiment_tool_view(request.run_id, submitted["exp_id"], include_log=False)
        assert result["terminal"] is True
        assert result["elapsed"] == pytest.approx(expected)


def test_queued_experiment_exposes_live_wait_without_fabricating_execution(execution):
    host, request, arguments = execution
    submitted = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    identity = submitted["exp_id"]
    reason = "granted GPU lacks live capacity; waiting for the authorized device"
    host.resource_waits[identity] = (request.run_id, reason)
    result = host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": identity, "max_wait_seconds": 0})
    assert result["status"] == "queued" and result["elapsed"] == 0
    assert result["resource_wait"] == reason
    assert not result["terminal"] and not host.store.list_attempts(workflow_id=identity)
    host.resource_waits.pop(identity)
    assert "resource_wait" not in host._experiment_tool_view(request.run_id, identity, include_log=False)


def test_invalid_candidate_and_cross_chain_tools_have_no_effect(execution) -> None:
    host, request, arguments = execution
    files = [WorkspaceArtifact.from_bytes(item["path"], b"def invalid(\n" if item["path"] == "experiment.py"
        else WorkspaceArtifact.from_dict(item).content_bytes()).to_dict() for item in arguments["workspace_files"]]
    with pytest.raises(AgentACIError, match="compile failed"):
        host.handle_agent_tool(request.attempt_id, "submit_experiment", {**arguments, "workspace_files": files})
    assert not host.store.list_workflow_heads(request.run_id, "EXPERIMENT")
    for tool in ("wait_experiment", "get_experiment_log", "cancel_experiment"):
        with pytest.raises(AgentACIError, match="caller.s chain"):
            host.handle_agent_tool(request.attempt_id, tool, {"exp_id": "another-chain-experiment"})
    with pytest.raises(AgentACIError, match="current OPEN"):
        host.handle_agent_tool("absent-attempt", "list_my_experiments", {})


def test_failed_experiment_repairs_inside_the_same_execution_attempt(execution) -> None:
    host, request, arguments = execution
    failing = [WorkspaceArtifact.from_bytes(item["path"], b"raise RuntimeError('repair-me')\n# result.json\n"
        if item["path"] == "experiment.py" else WorkspaceArtifact.from_dict(item).content_bytes()).to_dict()
        for item in arguments["workspace_files"]]
    first = host.handle_agent_tool(request.attempt_id, "submit_experiment", {**arguments, "workspace_files": failing})
    _until(host, lambda: _terminal(host, first["exp_id"]) and bool(host.store.list_facts(request.run_id, "EvidenceLinked")))
    failed = host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": first["exp_id"], "max_wait_seconds": 0})
    assert failed["outcome"]["outcome"] == "EXECUTION_FAILURE"
    assert "repair-me" in failed["tail_log"]
    assert not host.store.list_facts(request.run_id, "HypothesisAssessed")
    second = host.handle_agent_tool(request.attempt_id, "submit_experiment", {**arguments, "replaces_experiment_id": first["exp_id"]})
    assert second["exp_id"] != first["exp_id"]
    _until(host, lambda: _terminal(host, second["exp_id"]))
    assert host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": second["exp_id"]})["outcome"]["outcome"] == "SUCCESS"
    requests = [row for row in host.store.list_attempts() if row["kind"] == "AGENT"
        and json.loads(row["observation_json"])["request"]["command"] == AgentCommand.EXPERIMENT_EXECUTION.value]
    assert [row["attempt_id"] for row in requests] == [request.attempt_id]
    assert requests[0]["state"] == "OPEN"
    host._finish_fake_agent(request, FakeAgent().invoke(request))
    _until(host, lambda: bool(host.store.list_facts(request.run_id, "HypothesisAssessed")))
    assert host.store.list_facts(request.run_id, "HypothesisAssessed")[-1]["payload"]["assessment"] == "SUPPORTED"
    _until(host, lambda: host.store.latest_transition(request.run_id)["state"] == "COMPLETED")
    from hypoloom.monitor import build_monitor_snapshot
    observed = build_monitor_snapshot(host.state_root, request.run_id)
    assert observed.components[0].cycle_revisions == 1
    assert observed.components[0].revision_activity["scopes"][0]["recovered"]
    assert observed.components[0].revision_warning is None
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok, report.to_dict()


def test_queued_cancel_is_durable_and_wait_does_not_close_the_agent(execution) -> None:
    host, request, arguments = execution
    accepted = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    waiting = host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": accepted["exp_id"], "max_wait_seconds": 0})
    assert waiting["status"] == "queued" and not waiting["terminal"]
    assert host.store.get_attempt(request.attempt_id)["state"] == "OPEN"
    cancelled = host.handle_agent_tool(request.attempt_id, "cancel_experiment", {"exp_id": accepted["exp_id"]})
    assert cancelled["terminal"] and cancelled["outcome"] is None
    assert not host.running
    assert host.handle_agent_tool(request.attempt_id, "cancel_experiment", {"exp_id": accepted["exp_id"]})["terminal"]


def test_restart_reopens_execution_node_and_replays_committed_submit(execution) -> None:
    host, request, arguments = execution
    accepted = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    root = host.state_root
    host.close()
    recovered = WorkflowHost(root)
    try:
        recovered.initialize()
        assert recovered.store.get_attempt(request.attempt_id)["terminal_reason"] == "LOST"
        _until(recovered, lambda: bool(recovered.local_execution_agents))
        resumed = next(iter(recovered.local_execution_agents.values()))
        recovered.local_execution_agents.clear()
        assert resumed.command_id == request.command_id
        assert resumed.attempt_id != request.attempt_id
        assert recovered.handle_agent_tool(resumed.attempt_id, "submit_experiment", arguments) == accepted
        assert len(recovered.store.list_workflow_heads(request.run_id, "EXPERIMENT")) == 1
        with pytest.raises(AgentACIError, match="current OPEN"):
            recovered.handle_agent_tool(request.attempt_id, "list_my_experiments", {})
        _until(recovered, lambda: _terminal(recovered, accepted["exp_id"]))
        assert recovered.handle_agent_tool(resumed.attempt_id, "wait_experiment", {"exp_id": accepted["exp_id"]})["outcome"]["outcome"] == "SUCCESS"
    finally:
        recovered.close()


def test_process_kill_after_submit_commit_replays_without_duplicate_creation(execution) -> None:
    host, request, arguments = execution
    root = host.state_root
    host.close()
    script = """
import sys
from pathlib import Path
from hypoloom.workflow_host import WorkflowHost
host = WorkflowHost(Path(sys.argv[1]), fault_point='after_submit_commit_before_reply')
host.initialize()
for _ in range(100): host.tick()
raise AssertionError('submit fault point did not terminate the Host')
"""
    child = subprocess.run([sys.executable, "-c", script, str(root)], capture_output=True, text=True, timeout=30)
    assert child.returncode == 86, child.stderr
    recovered = WorkflowHost(root)
    try:
        recovered.initialize()
        creations = recovered.store.list_workflow_heads(request.run_id, "EXPERIMENT")
        assert len(creations) == 1
        experiment_id = creations[0]["workflow_id"]
        _until(recovered, lambda: bool(recovered.local_execution_agents))
        resumed = next(iter(recovered.local_execution_agents.values()))
        recovered.local_execution_agents.clear()
        replay = recovered.handle_agent_tool(resumed.attempt_id, "submit_experiment", arguments)
        assert replay["exp_id"] == experiment_id
        assert len(recovered.store.list_workflow_heads(request.run_id, "EXPERIMENT")) == 1
        _until(recovered, lambda: _terminal(recovered, experiment_id))
        assert recovered.handle_agent_tool(resumed.attempt_id, "wait_experiment", {"exp_id": experiment_id})["outcome"]["outcome"] == "SUCCESS"
        report = Oracle(recovered.store.database_path, recovered.publisher.root).audit(request.run_id)
        assert report.ok, report.to_dict()
    finally:
        recovered.close()


@pytest.mark.parametrize("field,value", [("execution_node_command_id", "wrong-node"), ("predecessor_experiment_id", "wrong-predecessor")])
def test_oracle_rejects_tampered_submit_causation_in_a_database_copy(execution, tmp_path, field, value) -> None:
    host, request, arguments = execution
    accepted = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    command = host.store.inspect("SELECT * FROM workflow_commands WHERE command_type='CREATE_EXPERIMENT' AND workflow_id=?", (accepted["exp_id"],))[0]
    data = json.loads(command["request_json"]); data[field] = value
    copied = tmp_path / f"tampered-{field}.sqlite"
    with sqlite3.connect(host.store.database_path) as source, sqlite3.connect(copied) as destination:
        source.backup(destination)
        for (name,) in destination.execute("SELECT name FROM sqlite_schema WHERE type='trigger' AND tbl_name='workflow_commands'").fetchall():
            destination.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
        destination.execute("UPDATE workflow_commands SET request_json=? WHERE command_id=?", (json.dumps(data), command["command_id"]))
    report = Oracle(copied, host.publisher.root).audit(request.run_id)
    assert not report.ok
    assert "experiment_submit_source_invalid" in {issue.code for issue in report.issues}, report.to_dict()


@pytest.mark.parametrize("execution", ["campaign"], indirect=True)
def test_required_preflight_and_analysis_review_preserve_open_execution_owner(execution) -> None:
    host, request, arguments = execution
    accepted = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    origins, _, replies = host._rendezvous_records(request.run_id)
    assert len(origins) == 1 and origins[0].phase == "preflight" and not replies
    assert not host.store.list_facts(request.run_id, "ExperimentPreregistered")
    assert host.store.get_attempt(request.attempt_id)["state"] == "OPEN"
    _until(host, lambda: _terminal(host, accepted["exp_id"]))
    assert host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": accepted["exp_id"]})["outcome"]["outcome"] == "SUCCESS"
    origins, _, replies = host._rendezvous_records(request.run_id)
    assert [item.phase for item in origins] == ["preflight", "analyze"]
    assert all(item.rendezvous_id in replies for item in origins)
    assert host.store.get_attempt(request.attempt_id)["state"] == "OPEN"
    host._finish_fake_agent(request, FakeAgent().invoke(request))
    _until(host, lambda: host.store.latest_transition(request.run_id)["state"] == "COMPLETED")
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok, report.to_dict()


def test_running_cancel_reaps_only_the_owned_process_and_keeps_agent_open(execution) -> None:
    host, request, arguments = execution
    delayed = [WorkspaceArtifact.from_bytes(item["path"], b"import time\ntime.sleep(10)\n# result.json\n"
        if item["path"] == "experiment.py" else WorkspaceArtifact.from_dict(item).content_bytes()).to_dict()
        for item in arguments["workspace_files"]]
    accepted = host.handle_agent_tool(request.attempt_id, "submit_experiment", {**arguments, "workspace_files": delayed})
    _until(host, lambda: bool(host.running))
    assert host.handle_agent_tool(request.attempt_id, "cancel_experiment", {"exp_id": accepted["exp_id"]})["state"] == "EXECUTING"
    _until(host, lambda: _terminal(host, accepted["exp_id"]))
    result = host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": accepted["exp_id"]})
    assert result["outcome"]["outcome"] == "CANCELLED"
    assert host.store.get_attempt(request.attempt_id)["state"] == "OPEN"
    assert not host.running
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok, report.to_dict()


def test_contradictory_resource_request_is_rejected_before_creation(execution) -> None:
    host, request, arguments = execution
    candidate = {**arguments["candidate"], "resource_preference": "GPU_REQUIRED"}
    with pytest.raises(AgentACIError, match="conflicts"):
        host.handle_agent_tool(request.attempt_id, "submit_experiment", {**arguments, "candidate": candidate, "gpus": 0})
    assert not host.store.list_workflow_heads(request.run_id, "EXPERIMENT")


@pytest.mark.parametrize("session", [{"transcript": None}, {"transcript": {}, "transcript_complete": False}])
def test_successful_agent_cannot_close_without_a_complete_archived_session(execution, session) -> None:
    host, request, _ = execution
    response = FakeAgent().invoke(request)
    observation = SimpleNamespace(returncode=0, pid=11, process_group_id=11,
        failure_code=None, diagnostics={}, response=response)
    host.running_agents[request.attempt_id] = SimpleNamespace(request=request,
        tool_workspace=SimpleNamespace(finish_node=lambda: session))
    host.claude_agent_host = SimpleNamespace(poll=lambda _: observation, cleanup=lambda _: None)
    try:
        assert host._poll_agents()
        attempt = host.store.get_attempt(request.attempt_id)
        assert attempt["terminal_reason"] == "FAILED"
        assert json.loads(attempt["observation_json"])["failure_code"] == "session_archive_failed"
    finally:
        host.claude_agent_host = None


@pytest.mark.parametrize("execution", ["campaign"], indirect=True)
def test_declined_preflight_repairs_in_same_execution_without_a_false_scientific_outcome(execution, tmp_path) -> None:
    host, request, arguments = execution

    class DeclineFirstReview(FakeAgent):
        declined = False

        def invoke(self, current):
            response = super().invoke(current)
            if current.command is AgentCommand.ANSWER_RENDEZVOUS and not self.declined:
                self.declined = True
                return with_output_digest(replace(response, typed_candidate={**response.typed_candidate,
                    "disposition": "DECLINED", "summary": "Please clarify the submitted implementation."}))
            return response

    host.agent = DeclineFirstReview()
    first = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    _until(host, lambda: _terminal(host, first["exp_id"]))
    rejected = host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": first["exp_id"]})
    assert rejected["terminal"] and rejected["repairable"]
    assert rejected["preflight"]["status"] == "rejected"
    assert rejected["preflight"]["reply"]["disposition"] == "DECLINED"
    assert rejected["outcome"] is None
    assert not host.store.list_facts(request.run_id, "ExperimentPreregistered")
    assert not host.store.list_facts(request.run_id, "CanonicalOutcomeAccepted")
    assert not [row for row in host.store.list_attempts() if row["kind"] == "PROCESS"]
    assert host.store.get_attempt(request.attempt_id)["state"] == "OPEN"
    repaired = [WorkspaceArtifact.from_bytes(item["path"], b"# Clarified after preflight.\n" + WorkspaceArtifact.from_dict(item).content_bytes()
        if item["path"] == "experiment.py" else WorkspaceArtifact.from_dict(item).content_bytes()).to_dict()
        for item in arguments["workspace_files"]]
    second = host.handle_agent_tool(request.attempt_id, "submit_experiment", {**arguments,
        "workspace_files": repaired, "replaces_experiment_id": first["exp_id"]})
    _until(host, lambda: _terminal(host, second["exp_id"]))
    assert host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": second["exp_id"]})["outcome"]["outcome"] == "SUCCESS"
    host._finish_fake_agent(request, FakeAgent().invoke(request))
    _until(host, lambda: host.store.latest_transition(request.run_id)["state"] == "COMPLETED")
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok, report.to_dict()
    assert len(host.store.list_facts(request.run_id, "CanonicalOutcomeAccepted")) == 1
    copied = tmp_path / "tampered-control.sqlite"
    with sqlite3.connect(host.store.database_path) as source, sqlite3.connect(copied) as destination:
        source.backup(destination)
        for (name,) in destination.execute("SELECT name FROM sqlite_schema WHERE type='trigger' AND tbl_name='workflow_transitions'").fetchall():
            destination.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
        destination.execute("UPDATE workflow_transitions SET payload_json=json_set(payload_json,'$.reason','unsupported_rejection') WHERE workflow_id=? AND state='TERMINAL'", (first["exp_id"],))
    corrupted = Oracle(copied, host.publisher.root).audit(request.run_id)
    assert "preflight_repair_source_invalid" in {issue.code for issue in corrupted.issues}, corrupted.to_dict()


@pytest.mark.parametrize("retryable", [False, True])
def test_controller_failure_does_not_orphan_its_already_started_experiment(execution, retryable) -> None:
    host, request, arguments = execution
    arguments = {**arguments, "workspace_files": [WorkspaceArtifact.from_bytes(item["path"],
        WorkspaceArtifact.from_dict(item).content_bytes() + (b"\nimport time\ntime.sleep(1)\n"
            if item["path"] == "experiment.py" else b"")).to_dict() for item in arguments["workspace_files"]]}
    accepted = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    _until(host, lambda: bool(host.running))
    host._agent_failure(request, AttemptTerminalReason.TIMED_OUT if retryable else AttemptTerminalReason.FAILED,
        "timed_out" if retryable else "session_archive_failed", retryable=retryable)
    if retryable:
        host._expert_agent_step(request.run_id, request.owner_workflow_id, request.task_context,
            request.role, request.component_id, request.method_family_id)
        assert len(host.store.list_attempts(command_id=request.command_id)) == 1, (
            "an Agent retry must wait until its still-running experiment has finished")
    if host._batch_enabled(request.run_id):
        key = request.task_context.key
        _, _, terminal_chains = host._batch_context(request.run_id, request.component_id, request.role, key.cycle)
        assert key.chain not in terminal_chains, "controller failure cannot close an unfinished submitted experiment"
    _until(host, lambda: host.store.latest_transition(request.run_id)["state"] == "COMPLETED")
    assert len(host.store.list_workflow_heads(request.run_id, "EXPERIMENT")) == 1
    outcome = next(row for row in host.store.list_facts(request.run_id, "CanonicalOutcomeAccepted") if row["experiment_id"] == accepted["exp_id"])
    assert outcome["payload"]["outcome"] == "SUCCESS"
    controllers = [row for row in host.store.list_attempts() if row["kind"] == "AGENT"
        and json.loads(row["observation_json"])["request"]["command"] == "EXPERIMENT_EXECUTION"]
    assert len(controllers) == (2 if retryable else 1)
    assert controllers[0]["terminal_reason"] in {"FAILED", "TIMED_OUT"}
    assert all(row["state"] == "TERMINAL" for row in host.store.list_workflow_heads(request.run_id, "EXPERIMENT"))
    assert any(row["terminal_reason"] == "SUCCEEDED" and json.loads(row["observation_json"]).get("request", {}).get("command") == "RESULT_ANALYSIS"
        for row in host.store.list_attempts() if row["kind"] == "AGENT")
    assert not host.running
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok, report.to_dict()


def test_failed_controller_experiment_drains_after_host_restart(execution) -> None:
    host, request, arguments = execution
    accepted = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    _until(host, lambda: bool(host.running))
    host._agent_failure(request, AttemptTerminalReason.FAILED, "session_archive_failed", retryable=False)
    root = host.state_root
    host.close()
    recovered = WorkflowHost(root)
    recovered.initialize()
    try:
        _until(recovered, lambda: recovered.store.latest_transition(request.run_id)["state"] == "COMPLETED")
        assert len(recovered.store.list_workflow_heads(request.run_id, "EXPERIMENT")) == 1
        outcome = recovered.store.list_facts(request.run_id, "CanonicalOutcomeAccepted")
        assert len(outcome) == 1 and outcome[0]["experiment_id"] == accepted["exp_id"]
        assert outcome[0]["payload"]["outcome"] == "SUCCESS"
        processes = [row for row in recovered.store.list_attempts() if row["kind"] == "PROCESS"]
        assert [row["terminal_reason"] for row in processes] == ["LOST", "SUCCEEDED"]
        report_attempt = next(row for row in recovered.store.list_attempts() if row["kind"] == "AGENT"
            and json.loads(row["observation_json"])["request"]["command"] == "FINAL_REPORT")
        report_request = AgentRequest.from_dict(json.loads(report_attempt["observation_json"])["request"])
        report_context = json.loads(next(item.content_bytes() for item in report_request.input_artifacts
            if item.ref.endswith("--final-report-context")))
        details = {row["attempt_id"]: row for row in report_context["run_outcome"]["process_attempt_details"]}
        assert details[processes[0]["attempt_id"]]["evidence_scope"] == "no_completed_process"
        assert "canonical_outcome" not in details[processes[0]["attempt_id"]]
        assert details[processes[1]["attempt_id"]]["evidence_scope"] == "accepted_scientific_evidence"
        artifact = recovered.store.inspect("SELECT attempt_id FROM execution_artifacts WHERE artifact_id=?",
            (outcome[0]["payload"]["artifact_id"],))
        assert artifact == ({"attempt_id": processes[1]["attempt_id"]},)
        controllers = [row for row in recovered.store.list_attempts() if row["kind"] == "AGENT"
            and json.loads(row["observation_json"])["request"]["command"] == "EXPERIMENT_EXECUTION"]
        assert len(controllers) == 1 and controllers[0]["terminal_reason"] == "FAILED"
        assert not recovered.running and all(row["state"] == "CLOSED" for row in recovered.store.list_attempts())
        report = Oracle(recovered.store.database_path, recovered.publisher.root).audit(request.run_id)
        assert report.ok, report.to_dict()
    finally:
        recovered.close()


def _submit_tamper_copy(host, tmp_path, name):
    copied = tmp_path / f"{name}.sqlite"
    with sqlite3.connect(host.store.database_path) as source, sqlite3.connect(copied) as destination:
        source.backup(destination)
        for (trigger,) in destination.execute("SELECT name FROM sqlite_schema WHERE type='trigger' AND tbl_name IN ('workflow_runs','workflow_commands','workflow_transitions','research_facts','execution_attempts')").fetchall():
            destination.execute('DROP TRIGGER "' + trigger.replace('"', '""') + '"')
    return copied


def _test_json_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _redigest_submit_source(data):
    snapshot = data["source_snapshot"]
    snapshot["input_digest"] = _test_json_digest(data["source_request"])
    snapshot["output_digest"] = _test_json_digest({key: value for key, value in snapshot.items() if key != "output_digest"})
    return snapshot["output_digest"]


@pytest.mark.parametrize("code", [
    "experiment_submit_creation_mismatch", "experiment_submit_effect_count",
    "execution_submit_source_invalid", "execution_submit_contract_mismatch",
])
def test_oracle_submit_audit_codes_reject_rehashed_database_tampering(execution, tmp_path, code):
    host, request, arguments = execution
    accepted = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    _until(host, lambda: _terminal(host, accepted["exp_id"]))
    baseline = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert baseline.ok, baseline.to_dict()
    original = host.store.inspect("SELECT * FROM workflow_commands WHERE command_type='CREATE_EXPERIMENT' AND workflow_id=?", (accepted["exp_id"],))[0]
    command = dict(original); data = json.loads(command["request_json"])
    copied = _submit_tamper_copy(host, tmp_path, code)
    with sqlite3.connect(copied) as destination:
        if code == "experiment_submit_creation_mismatch":
            result = json.loads(command["result_json"])
            result["exp_id"] = "wrong-result-owner"
            destination.execute("UPDATE workflow_commands SET result_json=? WHERE command_id=?", (json.dumps(result), command["command_id"]))
        elif code == "experiment_submit_effect_count":
            predecessor = accepted["exp_id"]
            command.update(command_id=f"{request.command_id}--submit--{predecessor}", workflow_id="uncreated-experiment",
                result_json=json.dumps({"exp_id": "uncreated-experiment", "queue_position": 0}))
            data["predecessor_experiment_id"] = predecessor
            data["arguments"]["replaces_experiment_id"] = predecessor
            data["source_snapshot"]["arguments"]["replaces_experiment_id"] = predecessor
        elif code == "execution_submit_source_invalid":
            data["source_request"]["owner_workflow_id"] = "another-owner"
        else:
            data["arguments"]["candidate"]["method"] = "changed method after preregistration"
            data["source_snapshot"]["arguments"]["candidate"]["method"] = data["arguments"]["candidate"]["method"]
        digest = _redigest_submit_source(data)
        command["request_json"] = json.dumps(data)
        if code == "experiment_submit_effect_count":
            columns = tuple(command)
            destination.execute(f"INSERT INTO workflow_commands ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", tuple(command[key] for key in columns))
        else:
            destination.execute("UPDATE workflow_commands SET request_json=? WHERE command_id=?", (command["request_json"], command["command_id"]))
        if code == "execution_submit_contract_mismatch":
            destination.execute("UPDATE research_facts SET payload_json=json_set(payload_json,'$.execution_contract.source_agent_output_digest',?) WHERE fact_type='ExperimentPreregistered' AND experiment_id=?", (digest, accepted["exp_id"]))
            destination.execute("UPDATE workflow_transitions SET payload_json=json_set(payload_json,'$.agent_output_digest',?) WHERE workflow_id=? AND json_type(payload_json,'$.agent_output_digest')='text'", (digest, accepted["exp_id"]))
        persisted = json.loads(destination.execute("SELECT request_json FROM workflow_commands WHERE command_id=?", (command["command_id"],)).fetchone()[0])
        assert persisted["source_snapshot"]["input_digest"] == _test_json_digest(persisted["source_request"])
        assert persisted["source_snapshot"]["output_digest"] == _test_json_digest({key: value for key, value in persisted["source_snapshot"].items() if key != "output_digest"})
    report = Oracle(copied, host.publisher.root).audit(request.run_id)
    codes = {issue.code for issue in report.issues}
    assert code in codes, report.to_dict()
    # Artifact bytes were not touched; the exact causation check, not a broken
    # source transport checksum or publication hash, must identify the fault.
    assert report.artifact_counts == baseline.artifact_counts
    assert not any("digest" in item or "hash" in item for item in codes), report.to_dict()
    if code != "execution_submit_source_invalid":
        assert "experiment_submit_source_invalid" not in codes, report.to_dict()


@pytest.mark.parametrize("version,audited", [(10, False), (11, True), (12, True)])
def test_submit_audit_version_boundary_is_not_tied_to_the_current_aci(execution, tmp_path, version, audited):
    host, request, arguments = execution
    accepted = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    command = host.store.inspect("SELECT * FROM workflow_commands WHERE command_type='CREATE_EXPERIMENT' AND workflow_id=?", (accepted["exp_id"],))[0]
    data = json.loads(command["request_json"])
    data["source_request"]["schema_version"] = version
    _redigest_submit_source(data)
    copied = _submit_tamper_copy(host, tmp_path, f"aci-{version}")
    with sqlite3.connect(copied) as destination:
        destination.execute("UPDATE workflow_runs SET agent_aci_version=? WHERE run_id=?", (str(version), request.run_id))
        destination.execute("UPDATE execution_attempts SET observation_json=json_set(observation_json,'$.request.schema_version',?) WHERE attempt_id=?", (version, request.attempt_id))
        destination.execute("UPDATE workflow_commands SET request_json=?, result_json=json_set(result_json,'$.exp_id','wrong-result-owner') WHERE command_id=?", (json.dumps(data), command["command_id"]))
    report = Oracle(copied, host.publisher.root).audit(request.run_id)
    codes = {issue.code for issue in report.issues}
    assert ("experiment_submit_creation_mismatch" in codes) is audited, report.to_dict()
    assert "experiment_submit_source_invalid" not in codes, report.to_dict()
    # A future envelope is not thereby declared readable; this test only proves
    # that its independent submit causation audit cannot disappear on a bump.


@pytest.mark.parametrize("version,supported", [
    (6, False), (10, False), ("10", False), (11, True), ("11", True),
    (12, True), ("12", True), (True, False), (11.0, False), (None, False), ("v12", False),
])
def test_submit_semantic_version_support_accepts_only_typed_or_durable_versions(version, supported):
    from hypoloom.agent_aci import supports_experiment_submission

    assert supports_experiment_submission(version) is supported


def test_retained_aci10_request_remains_readable_without_admitting_submit(execution):
    from hypoloom.agent_aci import AgentRequest, validate_experiment_submission

    host, request, arguments = execution
    retained = replace(request, schema_version=10)
    assert AgentRequest.from_dict(retained.to_dict()) == retained
    with pytest.raises(AgentACIError, match="submit requires"):
        validate_experiment_submission(retained, arguments)
    assert not host.store.list_workflow_heads(request.run_id, "EXPERIMENT")


def test_oracle_rejects_one_submit_creating_two_experiments(execution, tmp_path):
    host, request, arguments = execution
    accepted = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    _until(host, lambda: _terminal(host, accepted["exp_id"]))
    baseline = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert baseline.ok, baseline.to_dict()
    duplicate = dict(host.store.inspect("SELECT * FROM workflow_transitions WHERE workflow_id=? AND revision=1", (accepted["exp_id"],))[0])
    # This relation's primary key is (workflow_id, revision); keep the same
    # accepted submit command and source while creating a second workflow.
    duplicate["workflow_id"] = f"{accepted['exp_id']}--duplicate"
    copied = _submit_tamper_copy(host, tmp_path, "duplicate-submit-effect")
    with sqlite3.connect(copied) as destination:
        columns = tuple(duplicate)
        destination.execute(f"INSERT INTO workflow_transitions ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", tuple(duplicate[key] for key in columns))
        count = destination.execute("SELECT COUNT(DISTINCT workflow_id) FROM workflow_transitions WHERE command_id=? AND workflow_kind='EXPERIMENT' AND revision=1", (duplicate["command_id"],)).fetchone()[0]
        assert count == 2
    report = Oracle(copied, host.publisher.root).audit(request.run_id)
    codes = {issue.code for issue in report.issues}
    assert "experiment_submit_effect_count" in codes, report.to_dict()
    assert "experiment_submit_source_invalid" not in codes, report.to_dict()
    assert report.artifact_counts == baseline.artifact_counts
    assert not any("digest" in code or "hash" in code for code in codes), report.to_dict()
