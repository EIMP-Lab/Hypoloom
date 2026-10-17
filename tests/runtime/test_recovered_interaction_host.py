"""A real failed CPU experiment must not poison interaction after its repair."""
import json
import tempfile
import time
from pathlib import Path

from hypoloom.agent_aci import WorkspaceArtifact
from hypoloom.control_protocol import ControlRequest
from hypoloom.expert_interaction import InteractionFacts
from hypoloom.oracle import Oracle
from hypoloom.profile_v4 import project_profile_v4
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_expert_batch_host import BatchAgent
from tests.runtime.test_expert_loop_host import _pilot_payload
from tests.runtime.test_profile_v4 import documents


def _until(host, predicate):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if predicate():
            return
        host.tick()
        assert not host.dispatch_errors, host.dispatch_errors
        time.sleep(0.005)
    raise AssertionError("recovered interaction did not reach its next action")


def test_failed_cpu_experiment_repairs_then_observer_and_publication_close(tmp_path):
    with tempfile.TemporaryDirectory(prefix="hypoloom-recovered-target-", dir="/var/tmp") as directory:
        payload = _pilot_payload(tmp_path, Path(directory), "recovered-interaction")
        contract = payload["research_contract"]
        settings = project_profile_v4(**documents()).settings
        settings["experts"]["experts"][0]["name"] = contract["roles"][0]["id"]
        settings["experts"]["defaults"].update(max_hypotheses_per_round=2, max_forks=2)
        settings["goal"]["completion_mode"] = "explicit_signals"
        settings["goal"]["stop_conditions"]["max_rounds_per_expert"] = 1
        settings["methodology"]["inner_loop"] = {"max_iterations_per_hypothesis": 1, "repair_attempts": 1}
        settings["methodology"]["graph_evolution"]["enabled"] = False
        settings["methodology"]["interaction"] = {"leader": {
            "enabled": True, "check_after": "cycle_report", "min_rounds_before_leader": 1},
            "debate": {"enabled": False}}
        contract["methodology"]["controls"]["product"] = settings
        contract["budget"]["max_experiments"] = 6
        contract["roles"][0]["budget"]["max_experiments"] = 6
        host = WorkflowHost(tmp_path / "state", local_agent=BatchAgent())
        host.initialize()
        run_id = payload["run_id"]
        try:
            created = host.handle(ControlRequest(1, "create-recovered", 0, "CREATE_RUN", payload))
            assert created.status == "accepted", created
            _until(host, lambda: any(request.task_context.key.chain == "chain-a"
                for request in host.local_execution_agents.values()))
            request = next(request for request in host.local_execution_agents.values()
                if request.task_context.key.chain == "chain-a")
            # Supply the local Agent's normal submit/wait/repair calls through Host.
            del host.local_execution_agents[request.attempt_id]
            design = next(json.loads(row["observation_json"])["response"]
                for row in host.store.list_attempts() if row["kind"] == "AGENT"
                and (observation := json.loads(row["observation_json"]))["request"]["command"] == "EXPERIMENT_DESIGN_IMPLEMENT"
                and observation["request"]["task_context"]["key"].get("chain") == "chain-a")
            arguments = {"candidate": design["typed_candidate"], "workspace_files": design["proposed_workspace_artifacts"]}
            failing = [WorkspaceArtifact.from_bytes(item["path"], b"raise RuntimeError('bounded repair fixture')\n# result.json\n"
                if item["path"] == "experiment.py" else WorkspaceArtifact.from_dict(item).content_bytes()).to_dict()
                for item in arguments["workspace_files"]]
            first = host.handle_agent_tool(request.attempt_id, "submit_experiment", {**arguments, "workspace_files": failing})
            hypothesis_id = host._batch_history(run_id, request.component_id, request.role).latest.members["chain-a"]
            _until(host, lambda: (head := host.store.latest_transition(first["exp_id"])) is not None and head["state"] == "TERMINAL"
                and any(row["hypothesis_id"] == hypothesis_id
                    for row in host.store.list_facts(run_id, "EvidenceLinked")))
            assert host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": first["exp_id"]})["outcome"]["outcome"] == "EXECUTION_FAILURE"
            second = host.handle_agent_tool(request.attempt_id, "submit_experiment", {**arguments, "replaces_experiment_id": first["exp_id"]})
            _until(host, lambda: (head := host.store.latest_transition(second["exp_id"])) is not None and head["state"] == "TERMINAL")
            assert host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": second["exp_id"]})["outcome"]["outcome"] == "SUCCESS"
            host._finish_fake_agent(request, host.agent.invoke(request))
            _until(host, lambda: host.store.latest_transition(run_id)["state"] == "COMPLETED")
            outcomes = {row["outcome_id"]: row["payload"]
                for row in host.store.list_facts(run_id, "CanonicalOutcomeAccepted")}
            assert sorted(row["outcome"] for row in outcomes.values()) == ["EXECUTION_FAILURE", "SUCCESS", "SUCCESS"]
            evidence = {row["subject_id"]: row["payload"] for row in host.store.list_facts(run_id, "EvidenceLinked")}
            assessments = host.store.list_facts(run_id, "HypothesisAssessed")
            recovered = [row["payload"] for row in assessments
                if row["payload"]["assessment"] == "SUPPORTED" and len(row["payload"]["evidence_ids"]) == 2]
            assert len(recovered) == 1
            assert {outcomes[evidence[identity]["outcome_id"]]["outcome"]
                for identity in recovered[0]["evidence_ids"]} == {"EXECUTION_FAILURE", "SUCCESS"}
            version = int(host.store.get_run(run_id)["agent_aci_version"])
            qualified = InteractionFacts.from_rows(run_id, host.store.list_facts(run_id), agent_aci_version=version).leader_qualifications()
            assert recovered[0]["hypothesis_id"] in {item.hypothesis_id for item in qualified}
            attempts = host.store.list_attempts()
            assert all(row["state"] == "CLOSED" for row in attempts)
            processes = [row for row in attempts if row["kind"] == "PROCESS"]
            assert len(processes) == 3
            assert sorted(row["terminal_reason"] for row in processes) == ["FAILED", "SUCCEEDED", "SUCCEEDED"]
            requests = [json.loads(row["observation_json"]).get("request", {})
                for row in attempts if row["kind"] == "AGENT" and row["terminal_reason"] == "SUCCEEDED"]
            assert sum(row.get("command") == "OBSERVER" for row in requests) == 1
            assert sum(row.get("command") == "FINAL_REPORT" for row in requests) == 1
            assert all(row["state"] == "TERMINAL" for row in host.store.list_workflow_heads(run_id, "EXPERIMENT"))
            audit = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
            assert audit.ok, audit.to_dict()
        finally:
            host.close()
