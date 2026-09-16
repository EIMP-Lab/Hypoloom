"""Failed results stay failed while a budgeted, explicit revision can recover."""
import json
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from hypoloom.agent_aci import WorkspaceArtifact, with_output_digest
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeKind, ResearchNodeSubkind, ResearchNodeTask
from hypoloom.oracle import Oracle
from hypoloom.profile_v4 import project_profile_v4
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_expert_batch_host import BatchAgent, _complete
from tests.runtime.test_expert_loop_host import _pilot_payload
from tests.runtime.test_profile_v4 import documents


@pytest.mark.parametrize("block", [None, "legacy", "terminate", "rounds", "creation", "science", "update", "report", "evolve"])
def test_unselected_revision_requires_explicit_closed_controls_and_budget(block):
    host = object.__new__(WorkflowHost)
    report = host._cycle_report_task("run", "component", "role", 1)
    host.store = SimpleNamespace(get_run=lambda _: {"agent_aci_version": "16" if block == "legacy" else "17"})
    batch = SimpleNamespace(cycle=1, selection={"selected_tip": None})
    host._batch_history = lambda *args: SimpleNamespace(latest=batch)
    host._research_contract = lambda _: SimpleNamespace(document={"budget": {"max_experiments": 1 if block == "rounds" else 2}})
    host._batch_creation_available = lambda *args: block != "creation"
    host._batch_science_failed = lambda *args: block == "science"
    host._expert_task_final_failure = lambda *args: None
    names = {ResearchNodeKind.HYPOTHESIS_UPDATE: "update", ResearchNodeKind.CYCLE_REPORT: "report", ResearchNodeKind.SELF_EVOLVE: "evolve"}
    def success(task):
        if names[task.key.node_kind] == block: return None
        return {"observation_json": json.dumps({"response": {"typed_candidate": {
            "update_disposition": "TERMINATE_LINEAGE" if block == "terminate" else "REVISE_NEXT_CYCLE"}}})}
    host._expert_success = success
    assert host._unselected_batch_revision_ready("run", "component", "role") is (block is None)
    assert report.key.cycle == 1


class RevisionAgent(BatchAgent):
    def __init__(self, failed_cycle, *, terminate=False):
        super().__init__()
        self.failed_cycle = failed_cycle
        self.terminate = terminate

    def invoke(self, request):
        response = super().invoke(request)
        task = request.task_context
        if not isinstance(task, ResearchNodeTask): return response
        if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            artifacts = []
            for item in response.proposed_workspace_artifacts:
                data = item.content_bytes()
                if item.path == "experiment.py" and task.key.cycle == self.failed_cycle:
                    data += b'\nraise RuntimeError("deliberate operational failure")\n'
                elif task.key.cycle < self.failed_cycle:
                    data = data.replace(b'"accuracy": 0.55', b'"accuracy": 0.50')
                artifacts.append(WorkspaceArtifact.from_bytes(item.path, data))
            response = with_output_digest(replace(response, proposed_workspace_artifacts=tuple(artifacts)))
        if request.command is AgentCommand.HYPOTHESIS_UPDATE and not self.terminate:
            response = with_output_digest(replace(response, typed_candidate={**response.typed_candidate, "update_disposition": "REVISE_NEXT_CYCLE"}))
        if (request.command is AgentCommand.HYPOTHESIS_PROPOSAL and task.key.subkind is ResearchNodeSubkind.SYNTHESIS
                and task.key.cycle == self.failed_cycle + 1):
            context = json.loads(next(item for item in request.input_artifacts if item.ref == request.command_id + "--batch-context").content_bytes())
            parents = sorted(context["chains"].values())
            assert context["cycle"] == self.failed_cycle and context["selected_tip"] is None
            members = [{**member, "candidate": {**member["candidate"], "parent_hypothesis_id": parent, "relation": "revises"}}
                       for member, parent in zip(response.typed_candidate["hypotheses"], parents, strict=True)]
            response = with_output_digest(replace(response, typed_candidate={"schema_version": 1, "hypotheses": members}))
        return response


@pytest.mark.parametrize(("failed_cycle", "version", "terminate", "creation_limit", "expected_cycles"), [
    (1, 17, False, 6, 2), (2, 17, False, 6, 3),
    (1, 16, False, 6, 1), (1, 17, True, 6, 1), (1, 17, False, 2, 1),
])
def test_failed_batch_revision_keeps_real_ancestry_without_inventing_a_winner(
        tmp_path, monkeypatch, failed_cycle, version, terminate, creation_limit, expected_cycles):
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", version)
    with tempfile.TemporaryDirectory(prefix="hypoloom-revision-target-", dir="/var/tmp") as directory:
        payload = _pilot_payload(tmp_path, Path(directory), "batch-host")
        contract = payload["research_contract"]
        settings = project_profile_v4(**documents()).settings
        settings["experts"]["experts"][0]["name"] = contract["roles"][0]["id"]
        settings["experts"]["defaults"].update(max_hypotheses_per_round=2, max_forks=2)
        settings["goal"]["completion_mode"] = "explicit_signals"
        settings["goal"]["stop_conditions"]["max_rounds_per_expert"] = failed_cycle + 1
        settings["methodology"]["inner_loop"] = {"max_iterations_per_hypothesis": 1, "repair_attempts": 0}
        settings["methodology"]["graph_evolution"]["enabled"] = False
        settings["methodology"]["interaction"] = {"leader": {"enabled": False}, "debate": {"enabled": False}}
        contract["methodology"]["controls"]["product"] = settings
        contract["budget"]["max_experiments"] = creation_limit
        contract["roles"][0]["budget"]["max_experiments"] = creation_limit
        host = WorkflowHost(tmp_path / "state", local_agent=RevisionAgent(failed_cycle, terminate=terminate))
        host.initialize()
        try:
            created = host.handle(ControlRequest(schema_version=1, request_id="create-revision", expected_revision=0, command="CREATE_RUN", payload=payload))
            assert created.status == "accepted", created
            # Three rounds include real Process and control closure under full-suite contention.
            _complete(host, timeout=120)
            history = host._batch_history("batch-host", "primary.validation_accuracy")
            assert len(history.batches) == expected_cycles
            failed = history.batches[failed_cycle - 1]
            assert failed.selection["selected_tip"] is None
            assert failed.selection["pruned_by"] == []
            if expected_cycles == 1 and version >= 17:
                snapshot = host._policy_snapshot(host.store.latest_transition("batch-host")).components[0]
                assert snapshot.proposal_terminal and snapshot.selected_hypothesis_id is None
                assert snapshot.experiment_id is None and snapshot.assessment is None
                synthesis = [row for row in host.store.list_attempts() if json.loads(row["observation_json"]).get("request", {}).get("task_context", {}).get("key", {}).get("subkind") == "SYNTHESIS"]
                assert len(synthesis) == 1 and synthesis[0]["terminal_reason"] == "SUCCEEDED"
            if expected_cycles > failed_cycle:
                revised = history.batches[failed_cycle]
                assert {history.hypotheses[h]["payload"]["parent_hypothesis_id"] for h in revised.members.values()} == set(failed.members.values())
                assert revised.selection["selected_tip"]["outcome"] == "SUCCESS"
                for row in host.store.list_attempts():
                    request = json.loads(row["observation_json"]).get("request", {})
                    key = request.get("task_context", {}).get("key", {})
                    if key.get("cycle") != failed_cycle + 1 or key.get("chain") is None: continue
                    from hypoloom.agent_aci import AgentRequest
                    parsed = AgentRequest.from_dict(request)
                    lineage = json.loads(next(item for item in parsed.input_artifacts if item.ref.endswith("--lineage-context")).content_bytes())
                    assert lineage["parent_hypothesis_id"] == history.hypothesis(key["cycle"], key["chain"])["payload"]["parent_hypothesis_id"]
            outcomes = host.store.list_facts("batch-host", "CanonicalOutcomeAccepted")
            assert sum(row["payload"]["outcome"] == "EXECUTION_FAILURE" for row in outcomes) == 2
            assert len(outcomes) == 2 * expected_cycles
            audit = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
            assert audit.ok, audit.to_dict()
        finally:
            host.close()
