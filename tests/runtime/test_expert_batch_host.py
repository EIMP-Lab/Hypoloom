import json
import copy
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.agent_aci import WorkspaceArtifact, with_output_digest
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeKind, ResearchNodeSubkind, ResearchNodeTask
from hypoloom.fake_agent import FakeAgent
from hypoloom.profile_v4 import project_profile_v4
from hypoloom.oracle import Oracle
from hypoloom.workflow_host import WorkflowHost, _leader_round_matches
from tests.runtime.test_expert_loop_host import _pilot_payload
from tests.runtime.test_host_experiment_tools import _until
from tests.runtime.test_profile_v4 import documents


def test_provider_cooldown_survives_host_reopen_without_spending_attempts(batch_host, monkeypatch):
    from hypoloom.domain import AttemptTerminalReason
    host = batch_host
    accept = host._accept_agent_response
    requests = []
    retry_at = time.time() + 60

    def limited(request, response, **kwargs):
        requests.append(request)
        host._agent_failure(request, AttemptTerminalReason.FAILED, "nonzero_exit", retryable=True,
            details={"diagnostics": {"provider_failure": {
                "code": "rate_limited", "http_status": 429, "retry_at": retry_at}}})

    monkeypatch.setattr(host, "_accept_agent_response", limited)
    _until(host, lambda: bool(requests))
    before = len(host.store.list_attempts())
    for _ in range(3): host.tick()
    assert len(host.store.list_attempts()) == before
    assert host._provider_retry_pending("batch-host")
    # The decision is derived from the reopened DB, with no in-memory cooldown.
    reopened = WorkflowHost(host.state_root)
    assert reopened._provider_retry_pending("batch-host")
    reopened.store.close()
    monkeypatch.setattr(host, "_accept_agent_response", accept)
    monkeypatch.setattr("hypoloom.workflow_host.time.time", lambda: retry_at + 1)
    assert not host._provider_retry_pending("batch-host")
    _until(host, lambda: len(host.store.list_attempts()) > before)


def _complete(host, timeout=45):
    # Two scientific rounds include 34 real Host-owned Agent closures and four
    # bwrap Process runs; the single-experiment helper's 20 seconds is too short.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if host.store.latest_transition("batch-host")["state"] == "COMPLETED": return
        host.tick()
        if host.store.latest_transition("batch-host")["state"] == "COMPLETED": return
        time.sleep(0.005)
    raise AssertionError(repr(host._policy_snapshot(host.store.latest_transition("batch-host"))))


class BatchAgent(FakeAgent):
    def invoke(self, request):
        response = super().invoke(request)
        if (request.command is AgentCommand.HYPOTHESIS_PROPOSAL
                and isinstance(request.task_context, ResearchNodeTask)
                and request.task_context.key.subkind is ResearchNodeSubkind.SYNTHESIS):
            candidate = response.typed_candidate["hypotheses"][0]["candidate"]
            response = with_output_digest(replace(response, typed_candidate={"schema_version": 1,
                "hypotheses": [{"chain_id": chain, "candidate": {**candidate, "claim": "Distinct mechanism " + chain}}
                               for chain in ("chain-a", "chain-b")]}))
        return response


class IteratingBatchAgent(BatchAgent):
    def invoke(self, request):
        response = super().invoke(request)
        if request.command is AgentCommand.RESULT_ANALYSIS and request.task_context.key.experiment_ordinal == 1:
            response = with_output_digest(replace(response, typed_candidate={**response.typed_candidate, "iteration_disposition": "ITERATE"}))
        return response


class TwoCycleBatchAgent(BatchAgent):
    def invoke(self, request):
        response = super().invoke(request)
        if isinstance(request.task_context, ResearchNodeTask) and request.task_context.key.cycle == 1:
            if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
                response = with_output_digest(replace(response, proposed_workspace_artifacts=tuple(
                    WorkspaceArtifact.from_bytes(item.path, item.content_bytes().replace(b'"accuracy": 0.55', b'"accuracy": 0.50'))
                    for item in response.proposed_workspace_artifacts)))
            elif request.command is AgentCommand.HYPOTHESIS_UPDATE:
                response = with_output_digest(replace(response, typed_candidate={**response.typed_candidate, "update_disposition": "REVISE_NEXT_CYCLE"}))
        return response


class FailedDesignBatchAgent(BatchAgent):
    def __init__(self, all_chains):
        super().__init__()
        self.all_chains = all_chains

    def invoke(self, request):
        response = super().invoke(request)
        if (request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT
                and (self.all_chains or request.task_context.key.chain == "chain-b")):
            return with_output_digest(replace(response, typed_candidate={**response.typed_candidate, "invalid_field": True}))
        return response


class ManagedBatchAgent(TwoCycleBatchAgent):
    def invoke(self, request):
        response = super().invoke(request)
        if request.command is AgentCommand.GRAPH_EVOLVE:
            return with_output_digest(replace(response, typed_candidate={**response.typed_candidate,
                "graph_evolution": [{"role": "ml-researcher", "effective_max_forks": 1, "reason": "Focus the next round on one mechanism."}]}))
        if (request.command is AgentCommand.HYPOTHESIS_PROPOSAL and isinstance(request.task_context, ResearchNodeTask)
                and request.task_context.key.subkind is ResearchNodeSubkind.SYNTHESIS and request.task_context.key.cycle > 1):
            response = with_output_digest(replace(response, typed_candidate={**response.typed_candidate,
                "hypotheses": response.typed_candidate["hypotheses"][:1]}))
        return response


class SharedGoalAgent(FakeAgent):
    def __init__(self, *, all_refuted):
        super().__init__()
        self.all_refuted = all_refuted

    def invoke(self, request):
        response = super().invoke(request)
        if (request.command is AgentCommand.HYPOTHESIS_PROPOSAL and isinstance(request.task_context, ResearchNodeTask)
                and request.task_context.key.subkind is ResearchNodeSubkind.SYNTHESIS):
            member = response.typed_candidate["hypotheses"][0]
            response = with_output_digest(replace(response, typed_candidate={"schema_version": 1,
                "hypotheses": [{**member, "candidate": {**member["candidate"], "method_family_id": "family-" + request.role}}]}))
        if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT and (self.all_refuted or request.role != "ml-researcher"):
            response = with_output_digest(replace(response, proposed_workspace_artifacts=tuple(
                WorkspaceArtifact.from_bytes(item.path, item.content_bytes().replace(b'"accuracy": 0.55', b'"accuracy": 0.50'))
                for item in response.proposed_workspace_artifacts)))
        return response


class UpDownManagedAgent(BatchAgent):
    def invoke(self, request):
        response = super().invoke(request)
        task = request.task_context
        if request.command is AgentCommand.GRAPH_EVOLVE:
            response = with_output_digest(replace(response, typed_candidate={**response.typed_candidate,
                "graph_evolution": [{"role": "ml-researcher", "effective_max_forks": 1 if task.cycle == 1 else 2,
                    "reason": "Narrow one round, then reopen the admitted two-hypothesis exploration."}]}))
        if isinstance(task, ResearchNodeTask):
            if request.command is AgentCommand.HYPOTHESIS_PROPOSAL and task.key.subkind is ResearchNodeSubkind.SYNTHESIS:
                config = json.loads(next(item for item in request.input_artifacts if item.ref.endswith("--expert-config")).content_bytes())
                response = with_output_digest(replace(response, typed_candidate={**response.typed_candidate,
                    "hypotheses": response.typed_candidate["hypotheses"][:config["effective_max_forks"]]}))
            if task.key.cycle < 3:
                if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
                    response = with_output_digest(replace(response, proposed_workspace_artifacts=tuple(
                        WorkspaceArtifact.from_bytes(item.path, item.content_bytes().replace(b'"accuracy": 0.55', b'"accuracy": 0.50'))
                        for item in response.proposed_workspace_artifacts)))
                if request.command is AgentCommand.HYPOTHESIS_UPDATE:
                    response = with_output_digest(replace(response, typed_candidate={**response.typed_candidate, "update_disposition": "REVISE_NEXT_CYCLE"}))
        return response


class MissingFinalReportAgent(BatchAgent):
    def invoke(self, request):
        response = super().invoke(request)
        if request.command.value == "FINAL_REPORT":
            return with_output_digest(replace(response, proposed_workspace_artifacts=()))
        return response


class LeaderRoundAgent(SharedGoalAgent):
    def __init__(self):
        super().__init__(all_refuted=False)
        self.selected_leader = False

    def invoke(self, request):
        response = super().invoke(request)
        if request.command is AgentCommand.OBSERVER and not self.selected_leader:
            from hypoloom.expert_interaction import InteractionFacts
            context = json.loads(next(item for item in request.input_artifacts if item.ref.endswith("--interaction-context")).content_bytes())
            if "leader" in context["allowed_actions"]:
                facts = InteractionFacts.from_rows(request.run_id, context["facts"])
                qualified = next(item for item in facts.leader_qualifications() if item.role == "ml-researcher")
                self.selected_leader = True
                response = with_output_digest(replace(response, typed_candidate={"action": "leader", "rationale": "Develop the observed qualified alternative with read-only peer experience.",
                    "supporting_refs": list(qualified.qualification_refs), "leader": qualified.role, "debate": None}))
        return response


@pytest.fixture
def batch_host(tmp_path, monkeypatch, request):
    # Retain an explicit ACI13 case while exercising ACI14 batch semantics.
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 13 if getattr(request, "param", None) == "legacy13" else 14)
    with tempfile.TemporaryDirectory(prefix="hypoloom-batch-target-", dir="/var/tmp") as directory:
        payload = _pilot_payload(tmp_path, Path(directory), "batch-host")
        contract = payload["research_contract"]
        settings = project_profile_v4(**documents()).settings
        settings["experts"]["experts"][0]["name"] = contract["roles"][0]["id"]
        settings["experts"]["defaults"]["max_hypotheses_per_round"] = 2
        settings["goal"]["completion_mode"] = "explicit_signals"
        mode = getattr(request, "param", None)
        if mode in {"fifo", "fifo-cross"}: contract["budget"]["max_active_processes"] = 1
        settings["methodology"]["interaction"] = {"leader": {"enabled": mode in {"observer-none", "leader-round"}, "check_after": "cycle_report", "min_rounds_before_leader": 1}, "debate": {"enabled": False, "rounds": 3}}
        settings["methodology"]["graph_evolution"]["enabled"] = mode in {"managed", "managed-up-down"}
        shared = mode in {"shared-success", "shared-refuted", "leader-round"}
        if shared:
            settings["goal"]["completion_mode"] = "shared_goal"
            settings["experts"]["defaults"]["max_hypotheses_per_round"] = 1
            contract["protocols"]["shared-accuracy"] = {"result_kind": "metric", "requirements": {"required_metrics": ["accuracy"]}}
            contract["components"][0]["protocol"] = "shared-accuracy"
            contract["roles"][0]["protocols"] = {"default": "shared-accuracy", "allowed": ["shared-accuracy"]}
            for role in ("scientist-b", "scientist-c"):
                new_role = copy.deepcopy(contract["roles"][0]); new_role["id"] = role
                contract["roles"].append(new_role)
                new_component = copy.deepcopy(contract["components"][0])
                new_component.update(id="primary." + role, name=role, owner=role, proposers=[role])
                contract["components"].append(new_component)
                settings["experts"]["experts"].append({**copy.deepcopy(settings["experts"]["experts"][0]), "name": role})
            contract["goal"]["summary_metric"] = {"name": "required_component_completion", "direction": "MAXIMIZE", "target": "3", "display_only": True}
            if mode == "leader-round":
                # Interaction development continues only while required work remains;
                # shared-goal success now stops before selecting another Leader.
                settings["goal"]["completion_mode"] = "explicit_signals"
        settings["experts"]["defaults"]["max_forks"] = 1 if mode == "max-forks-one" else 4
        if mode in {"parallel", "max-forks-one", "restart"}: payload["execution"]["delay_seconds"] = 0.6
        settings["goal"]["stop_conditions"]["max_rounds_per_expert"] = 3 if mode == "managed-up-down" else 2 if mode in {"two-cycles", "outer-budget-exhausted", "managed", "leader-round"} else 1
        iterate = mode in {"iterate", "iterate-exhausted"}
        settings["methodology"]["inner_loop"] = {"max_iterations_per_hypothesis": 2 if iterate else 1, "repair_attempts": 1}
        contract["methodology"]["controls"]["product"] = settings
        contract["budget"]["max_experiments"] = 6
        if mode == "dependent":
            producer = contract["components"][0]
            producer["required_artifacts"] = ["handoff"]
            producer["protocol"] = "batch-handoff"
            contract["protocols"]["batch-handoff"] = {"result_kind": "metric", "requirements": {
                "required_metrics": ["accuracy"], "required_artifacts": ["handoff"]}}
            contract["roles"][0]["protocols"] = {"default": "batch-handoff", "allowed": ["batch-handoff"]}
            consumer = copy.deepcopy(producer)
            consumer.update(id="primary.dependent", name="dependent", dependencies=[producer["id"]],
                required_inputs=[{"resource": "upstream-handoff", "produced_by": producer["id"], "artifact": "handoff", "min_count": 1}])
            contract["components"].append(consumer)
            contract["goal"]["summary_metric"] = {"name": "required_component_completion", "direction": "MAXIMIZE",
                "target": "2", "display_only": True}
        if mode == "fifo-cross":
            contract["protocols"]["fifo-accuracy"] = {"result_kind": "metric", "requirements": {"required_metrics": ["accuracy"]}}
            contract["components"][0]["protocol"] = "fifo-accuracy"
            contract["roles"][0]["protocols"] = {"default": "fifo-accuracy", "allowed": ["fifo-accuracy"]}
            second = copy.deepcopy(contract["components"][0])
            second.update(id="primary.second", name="second")
            contract["components"].append(second)
            contract["budget"]["max_active_agents"] = 8
            contract["goal"]["summary_metric"] = {"name": "required_component_completion", "direction": "MAXIMIZE",
                "target": "2", "display_only": True}
            payload["execution"]["delay_seconds"] = 1.0
        if mode not in {"iterate-exhausted", "outer-budget-exhausted"}: contract["roles"][0]["budget"]["max_experiments"] = 6
        agent = (LeaderRoundAgent() if mode == "leader-round" else MissingFinalReportAgent() if mode == "final-report-missing" else UpDownManagedAgent() if mode == "managed-up-down" else SharedGoalAgent(all_refuted=mode == "shared-refuted") if shared else ManagedBatchAgent() if mode == "managed" else FailedDesignBatchAgent(mode == "all-designs-fail") if mode in {"one-design-fails", "all-designs-fail"}
            else TwoCycleBatchAgent() if mode in {"two-cycles", "round-exhausted", "outer-budget-exhausted"}
            else IteratingBatchAgent() if iterate else BatchAgent())
        host = WorkflowHost(tmp_path / "state", local_agent=agent)
        host.initialize()
        try:
            response = host.handle(ControlRequest(schema_version=1, request_id="create-batch", expected_revision=0,
                                                  command="CREATE_RUN", payload=payload))
            assert response.status == "accepted", response
            yield host
        finally:
            host.close()


def test_exhausted_adjudication_closes_batch_without_inventing_selection(batch_host, monkeypatch):
    host = batch_host
    invoke = host.agent.invoke

    def fail_adjudication(request):
        response = invoke(request)
        if request.command is AgentCommand.ADJUDICATE:
            return with_output_digest(replace(response, typed_candidate={**response.typed_candidate, "invalid_field": True}))
        return response

    monkeypatch.setattr(host.agent, "invoke", fail_adjudication)
    _until(host, lambda: bool(host._expert_final_failures("batch-host")))
    component = host._research_contract("batch-host").components[0]
    batch = host._batch_history("batch-host", str(component.component_id), component.owner_role).latest
    assert batch.selection is None
    assert host._batch_control_closed("batch-host", str(component.component_id), component.owner_role)
    for _ in range(25):
        host.tick()
        if host.store.latest_transition("batch-host")["state"] == "COMPLETED":
            break
    assert host.store.latest_transition("batch-host")["state"] == "COMPLETED"
    assert len(host._expert_final_failures("batch-host")) == 1
    assert host._batch_history("batch-host", str(component.component_id), component.owner_role).latest.selection is None
    assert Oracle(host.store.database_path, host.publisher.root).audit("batch-host").ok


def test_one_synthesis_drives_two_real_cpu_chains_and_one_adjudication(batch_host):
    host = batch_host
    _complete(host)
    hypotheses = host.store.list_facts("batch-host", "HypothesisProposed")
    assert len(hypotheses) == 2
    outcomes = host.store.list_facts("batch-host", "CanonicalOutcomeAccepted")
    assert len(outcomes) == 2
    assert {row["payload"]["outcome"] for row in outcomes} == {"SUCCESS"}
    assert len([row for row in host.store.list_attempts() if row["kind"] == "PROCESS" and row["terminal_reason"] == "SUCCEEDED"]) == 2
    attempts = [json.loads(row["observation_json"])["request"] for row in host.store.list_attempts() if row["kind"] == "AGENT"]
    assert len([row for row in attempts if row["command"] == "ADJUDICATE"]) == 1
    assert {row["task_context"]["key"]["chain"] for row in attempts if row["command"] == "EXPERIMENT_EXECUTION"} == {"chain-a", "chain-b"}
    history = host._batch_history("batch-host", str(hypotheses[0]["payload"]["component_id"]))
    assert len(history.batches) == 1
    assert history.selected_tip["chain_id"] == "chain-a"
    audit = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert audit.ok, audit.to_dict()


@pytest.mark.parametrize("batch_host", ["iterate"], indirect=True)
def test_scientific_iteration_keeps_hypothesis_and_binds_accepted_analysis(batch_host):
    host = batch_host
    _complete(host)
    outcomes = host.store.list_facts("batch-host", "CanonicalOutcomeAccepted")
    assert len(outcomes) == 4
    assert {row["payload"]["outcome"] for row in outcomes} == {"SUCCESS"}
    assert len(host.store.list_facts("batch-host", "HypothesisProposed")) == 2
    rows = [json.loads(row["payload_json"]) for row in host.store.inspect(
        "SELECT payload_json FROM workflow_transitions WHERE run_id=? AND workflow_kind='EXPERIMENT' AND revision=1", ("batch-host",))]
    assert {(row["chain"], row["chain_iteration"]) for row in rows} == {
        (chain, iteration) for chain in ("chain-a", "chain-b") for iteration in (1, 2)}
    for row in rows:
        if row["chain_iteration"] == 2:
            assert row["revision_of_outcome_id"] is None
            assert row["analysis_attempt_id"]
            prior = host.store.find_fact("batch-host", "ExperimentPreregistered", row["iteration_of_experiment_id"])
            assert prior["hypothesis_id"] == row["hypothesis_id"]
    audit = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert audit.ok, audit.to_dict()


@pytest.mark.parametrize("batch_host", ["iterate-exhausted"], indirect=True)
def test_iteration_request_at_role_budget_closes_actual_tips_without_stalling(batch_host):
    host = batch_host
    _complete(host)
    assert len(host.store.list_facts("batch-host", "CanonicalOutcomeAccepted")) == 2
    assert len(host.store.list_facts("batch-host", "HypothesisAssessed")) == 2


@pytest.mark.parametrize("batch_host", ["two-cycles", "round-exhausted"], indirect=True)
def test_next_cycle_uses_adjudicated_parent_and_respects_round_cap(batch_host, request):
    host = batch_host
    _complete(host)
    cycles = 2 if request.node.callspec.params["batch_host"] == "two-cycles" else 1
    hypotheses = host.store.list_facts("batch-host", "HypothesisProposed")
    component_id = hypotheses[0]["payload"]["component_id"]
    history = host._batch_history("batch-host", component_id)
    assert len(history.batches) == cycles
    assert len(host.store.list_facts("batch-host", "CanonicalOutcomeAccepted")) == 2 * cycles
    if cycles == 2:
        parent = history.batches[0].selection["selected_tip"]["node_id"]
        assert {history.hypotheses[value]["payload"]["parent_hypothesis_id"] for value in history.batches[1].members.values()} == {parent}
    evolves = [row for row in host.store.list_attempts() if json.loads(row["observation_json"]).get("request", {}).get("command") == "SELF_EVOLVE"]
    assert len(evolves) == cycles
    assert all(json.loads(row["observation_json"])["plugin_update"]["commit"] for row in evolves)
    for row in host.store.list_attempts():
        observed = json.loads(row["observation_json"])
        request = observed.get("request", {})
        if request.get("command") not in {"CYCLE_REPORT", "SELF_EVOLVE"}: continue
        from hypoloom.agent_aci import EmbeddedInput
        supplied = next(EmbeddedInput.from_dict(item) for item in request["input_artifacts"]
                        if item["ref"].endswith("--cycle-assessments"))
        context = json.loads(supplied.content_bytes())
        cycle = request["task_context"]["key"]["cycle"]
        assert context["counts"] == {"cycle_experiments": 2, "component_experiments_total": cycle * 2}
        records = context["experiment_records"]
        assert len({record["exp_id"] for record in records}) == 2
        assert all(record["process"]["terminal_reason"] == "SUCCEEDED" for record in records)
        assert all("result.json" in record["result_files"]["available"] for record in records)
        assert all("tail_log" not in record for record in records)


@pytest.mark.parametrize("batch_host", ["parallel", "max-forks-one"], indirect=True)
def test_parallel_chain_processes_obey_the_actual_profile_fork_limit(batch_host, request):
    host = batch_host
    maximum = 0
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and host.store.latest_transition("batch-host")["state"] != "COMPLETED":
        host.tick()
        maximum = max(maximum, len([row for row in host.store.list_attempts() if row["kind"] == "PROCESS" and row["state"] == "OPEN"]))
        time.sleep(0.005)
    assert host.store.latest_transition("batch-host")["state"] == "COMPLETED"
    assert maximum == (1 if request.node.callspec.params["batch_host"] == "max-forks-one" else 2)


@pytest.mark.parametrize("batch_host", ["restart"], indirect=True)
def test_restart_recovers_two_active_chains_without_duplicate_submit_or_selection(batch_host):
    host = batch_host
    _until(host, lambda: len([row for row in host.store.list_attempts() if row["kind"] == "PROCESS" and row["state"] == "OPEN"]) == 2)
    root = host.state_root
    host.close()
    recovered = WorkflowHost(root, local_agent=BatchAgent())
    recovered.initialize()
    try:
        _complete(recovered)
        assert len(recovered.store.list_facts("batch-host", "ExperimentPreregistered")) == 2
        assert len(recovered.store.list_facts("batch-host", "CanonicalOutcomeAccepted")) == 2
        processes = [row for row in recovered.store.list_attempts() if row["kind"] == "PROCESS"]
        assert len(processes) == 4
        assert sorted(row["terminal_reason"] for row in processes) == ["LOST", "LOST", "SUCCEEDED", "SUCCEEDED"]
        assert len(recovered.store.inspect("SELECT 1 FROM workflow_commands WHERE command_type='ACCEPT_ADJUDICATION'")) == 1
    finally:
        recovered.close()


@pytest.mark.parametrize("batch_host", ["one-design-fails", "all-designs-fail"], indirect=True)
def test_failed_design_is_control_terminal_without_fabricated_scientific_outcome(batch_host, request):
    host = batch_host
    _complete(host)
    surviving = 0 if request.node.callspec.params["batch_host"] == "all-designs-fail" else 1
    assert len(host.store.list_facts("batch-host", "CanonicalOutcomeAccepted")) == surviving
    assert len(host.store.list_facts("batch-host", "HypothesisAssessed")) == surviving
    history = host._batch_history("batch-host", "primary.validation_accuracy")
    assert (history.selected_tip is None) is (surviving == 0)
    report = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert report.ok, report.to_dict()


@pytest.mark.parametrize("batch_host", ["two-cycles"], indirect=True)
def test_prestart_failure_in_later_batch_does_not_fall_back_to_unbound_execution(batch_host, monkeypatch):
    from hypoloom.compute_resources import ResourceUnavailable
    from hypoloom.execution_workspace import ExecutionWorkspace

    host = batch_host
    materialize = ExecutionWorkspace.materialize

    def fail_second_cycle(workspace, prepared, **kwargs):
        request = kwargs["agent_request"]
        if request["task_context"]["key"]["cycle"] == 2:
            raise ResourceUnavailable("pinned Agent/compute configuration or runtime drifted")
        return materialize(workspace, prepared, **kwargs)

    monkeypatch.setattr(ExecutionWorkspace, "materialize", fail_second_cycle)
    _complete(host)
    processes = [row for row in host.store.list_attempts() if row["kind"] == "PROCESS"]
    assert sorted(row["terminal_reason"] for row in processes) == ["FAILED", "FAILED", "SUCCEEDED", "SUCCEEDED"]
    failed = [json.loads(row["observation_json"]) for row in processes if row["terminal_reason"] == "FAILED"]
    assert all(item["synthetic_before_spawn"] and "runtime drifted" in item["resource_diagnostic"] for item in failed)
    history = host._batch_history("batch-host", "primary.validation_accuracy")
    assert len(history.batches) == 2 and history.selected_tip is None
    assert len(host.store.list_facts("batch-host", "CanonicalOutcomeAccepted")) == 4
    assert not host.running and all(row["state"] == "CLOSED" for row in host.store.list_attempts())
    report = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert report.ok, report.to_dict()


def _manually_ready_submissions(host, order):
    # Finish proposal admission before holding every controller OPEN. This does
    # not publish fictitious Attempts or change an already-frozen node input.
    _until(host, lambda: host.store.latest_transition("batch-host")["state"] == "ACTIVE")
    for component in host._research_contract("batch-host").components:
        for _ in range(20):
            if host._batch_history("batch-host", str(component.component_id)).latest is not None: break
            host._drive_expert_proposal("batch-host", str(component.component_id), component.owner_role)
    _until(host, lambda: len(host.local_execution_agents) == len(order))
    requests = {(request.component_id, request.task_context.key.chain): request for request in host.local_execution_agents.values()}
    host.local_execution_agents.clear()
    accepted = {}
    for scope in order:
        request = requests[scope]
        design = ResearchNodeTask(key=replace(request.task_context.key,
            node_kind=ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT))
        packet = json.loads(host._expert_success(design)["observation_json"])["response"]
        accepted[scope] = host.handle_agent_tool(request.attempt_id, "submit_experiment", {
            "candidate": packet["typed_candidate"], "workspace_files": packet["proposed_workspace_artifacts"]})["exp_id"]
    for experiment_id in accepted.values():
        for command in ("PREREGISTER", "PREPARE"):
            host._drive_experiment("batch-host", host.store.latest_transition(experiment_id), command)
    assert all(row["state"] == "READY" for row in host.store.list_workflow_heads("batch-host", "EXPERIMENT"))
    assert host._effect_capacity("batch-host", "PROCESS") == 1
    return accepted, requests


@pytest.mark.parametrize("batch_host", ["fifo"], indirect=True)
def test_ready_experiments_start_in_submit_order_not_chain_name_order(batch_host):
    host = batch_host
    order = [("primary.validation_accuracy", chain) for chain in ("chain-b", "chain-a")]
    accepted, _ = _manually_ready_submissions(host, order)
    host.tick()
    processes = [row for row in host.store.list_attempts() if row["kind"] == "PROCESS"]
    assert len(processes) == 1 and processes[0]["experiment_id"] == accepted[order[0]]


@pytest.mark.parametrize("batch_host", ["fifo-cross"], indirect=True)
def test_submit_fifo_interleaves_components_and_completes_real_cpu_work(batch_host):
    host = batch_host
    first, second = "primary.validation_accuracy", "primary.second"
    order = [(first, "chain-a"), (second, "chain-a"), (first, "chain-b"), (second, "chain-b")]
    accepted, requests = _manually_ready_submissions(host, order)
    host.local_execution_agents.update({request.attempt_id: request for request in requests.values()})
    _complete(host)
    processes = host.store.inspect("SELECT * FROM execution_attempts WHERE kind='PROCESS' ORDER BY rowid")
    assert [row["experiment_id"] for row in processes] == [accepted[scope] for scope in order]
    assert all(row["terminal_reason"] == "SUCCEEDED" for row in processes)
    report = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert report.ok, report.to_dict()


@pytest.mark.parametrize("batch_host", ["fifo-cross"], indirect=True)
def test_process_priority_matches_designated_component_not_whole_role(batch_host, monkeypatch):
    host = batch_host
    first, second = "primary.validation_accuracy", "primary.second"
    order = [(first, "chain-a"), (first, "chain-b"), (second, "chain-a"), (second, "chain-b")]
    accepted, _ = _manually_ready_submissions(host, order)
    # This isolates consumption of the already-accepted Leader view; native
    # Leader G3 separately covers Observer admission and the persisted plan.
    monkeypatch.setattr(host.interaction, "leader_round", lambda run, component: {
        "role": "ml-researcher", "component_id": second, "cycle": 1} if component == second else None)
    host.tick()
    processes = [row for row in host.store.list_attempts() if row["kind"] == "PROCESS"]
    assert len(processes) == 1 and processes[0]["experiment_id"] == accepted[(second, "chain-a")]


@pytest.mark.parametrize("role,component,cycle,expected", [
    ("critic", "primary.a", 2, True),
    ("critic", "primary.a", 1, False),
    ("critic", "primary.a", 3, False),
    ("critic", "primary.b", 2, False),
    ("researcher", "primary.a", 2, False),
])
def test_execution_priority_requires_exact_accepted_leader_scope_and_cycle(role, component, cycle, expected):
    assert _leader_round_matches({"role": "critic", "component_id": "primary.a", "cycle": 2}, role, component, cycle) is expected
    assert not _leader_round_matches(None, role, component, cycle)


@pytest.mark.parametrize("batch_host", ["fifo-cross"], indirect=True)
@pytest.mark.parametrize("complete_before_priority", [False, True])
def test_leader_priority_does_not_preempt_an_existing_process(batch_host, monkeypatch, complete_before_priority):
    host = batch_host
    first, second = "primary.validation_accuracy", "primary.second"
    order = [(first, "chain-a"), (first, "chain-b"), (second, "chain-a"), (second, "chain-b")]
    accepted, _ = _manually_ready_submissions(host, order)
    host.tick()
    attempt_id, handle = next(iter(host.running.items()))
    assert host.store.get_attempt(attempt_id)["experiment_id"] == accepted[order[0]]
    cancelled = []
    cancel = host.runner.cancel
    def record_cancel(process):
        cancelled.append(process if isinstance(process, str) else process.request.attempt_id)
        return cancel(process)
    monkeypatch.setattr(host.runner, "cancel", record_cancel)
    monkeypatch.setattr(host.interaction, "leader_round", lambda run, component: {
        "role": "ml-researcher", "component_id": second, "cycle": 1} if component == second else None)
    if complete_before_priority:
        # A scheduling pause may let the real child finish before the next tick.
        assert handle._process.wait(timeout=10) == 0
    host.tick()
    assert attempt_id not in cancelled
    if host.store.get_attempt(attempt_id)["state"] == "OPEN":
        assert tuple(host.running) == (attempt_id,) and host.running[attempt_id] is handle
        assert handle.returncode in (None, 0)
        assert len([row for row in host.store.list_attempts() if row["kind"] == "PROCESS"]) == 1
    _until(host, lambda: host.store.get_attempt(attempt_id)["state"] == "CLOSED")
    completed = host.store.get_attempt(attempt_id)
    assert attempt_id not in cancelled
    assert completed["terminal_reason"] == "SUCCEEDED" and handle.returncode == 0
    assert json.loads(completed["observation_json"])["returncode"] == 0


@pytest.mark.parametrize("batch_host", ["outer-budget-exhausted"], indirect=True)
def test_next_round_request_at_role_experiment_limit_closes_completed_batch(batch_host):
    host = batch_host
    _complete(host)
    assert len(host._batch_history("batch-host", "primary.validation_accuracy").batches) == 1
    outcomes = host.store.list_facts("batch-host", "CanonicalOutcomeAccepted")
    assert len(outcomes) == 2 and all(row["payload"]["outcome"] == "CLEAN_REFUTATION" for row in outcomes)
    assert [row["terminal_reason"] for row in host.store.list_attempts() if row["kind"] == "PROCESS"] == ["SUCCEEDED", "SUCCEEDED"]
    requests = [json.loads(row["observation_json"])["request"] for row in host.store.list_attempts() if row["kind"] == "AGENT"]
    assert {item["task_context"]["key"]["cycle"] for item in requests if item["task_context"].get("key")} == {1}
    assert {"HYPOTHESIS_UPDATE", "CYCLE_REPORT", "SELF_EVOLVE", "FINAL_REPORT"} <= {item["command"] for item in requests}
    assert all(row["state"] == "CLOSED" for row in host.store.list_attempts())
    report = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert report.ok, report.to_dict()


@pytest.mark.parametrize("batch_host", ["dependent"], indirect=True)
def test_downstream_freezes_adjudicated_first_chain_not_later_completed_peer(batch_host):
    host = batch_host
    # The frozen /45 rehearsal reached COMPLETING after 33 successful Agents
    # and four real Processes at 45s, including with only three pytest workers.
    # Allow bounded report closure; scientific/selection assertions stay exact.
    _complete(host, timeout=90)
    selected = host._batch_history("batch-host", "primary.validation_accuracy").selected_tip
    assert selected["chain_id"] == "chain-a"
    specs = [row for row in host.store.list_facts("batch-host", "ExperimentPreregistered")
        if row["payload"]["component_id"] == "primary.dependent"]
    assert len(specs) == 2
    for spec in specs:
        bindings = spec["payload"]["upstream_outcomes"]
        assert len(bindings) == 1
        assert bindings[0]["outcome_id"] == selected["outcome_id"]
        assert bindings[0]["artifact_id"] == selected["artifact_id"]
    report = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert report.ok, report.to_dict()


@pytest.mark.parametrize("batch_host", ["managed"], indirect=True)
def test_manager_acceptance_changes_only_next_frozen_hypothesis_batch(batch_host):
    host = batch_host
    _complete(host)
    history = host._batch_history("batch-host", "primary.validation_accuracy")
    assert [len(batch.members) for batch in history.batches] == [2, 1]
    managers = [json.loads(row["observation_json"]) for row in host.store.list_attempts()
        if json.loads(row["observation_json"]).get("request", {}).get("command") == "GRAPH_EVOLVE"]
    assert len(managers) == 2
    assert all(item["request"]["role"] == item["request"]["component_id"] == "grounding" for item in managers)
    assert len(host.store.inspect("SELECT 1 FROM workflow_commands WHERE command_type='ACCEPT_GRAPH_EVOLUTION'")) == 2
    synthesis = [json.loads(row["observation_json"])["request"] for row in host.store.list_attempts()
        if json.loads(row["observation_json"]).get("request", {}).get("task_context", {}).get("key", {}).get("subkind") == "SYNTHESIS"]
    from hypoloom.agent_aci import AgentRequest
    limits = [json.loads(next(item for item in AgentRequest.from_dict(request).input_artifacts
        if item.ref.endswith("--expert-config")).content_bytes())["effective_max_forks"] for request in synthesis]
    assert limits == [2, 1]
    report = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert report.ok, report.to_dict()


@pytest.mark.parametrize("batch_host", ["shared-success", "shared-refuted"], indirect=True)
def test_three_expert_shared_goal_uses_actual_alternative_cpu_results(batch_host):
    host = batch_host
    # The /45 run reached 38 successful Agents, three CPU results and its goal
    # verdict at the old 60s bound. Include bounded final-report closure.
    _complete(host, timeout=120)
    outcomes = host.store.list_facts("batch-host", "CanonicalOutcomeAccepted")
    assert len(outcomes) == 3
    assert len({item["payload"]["method_family_id"] for item in outcomes}) == 3
    verdict = host.store.list_facts("batch-host", "GoalVerdictRecorded")[0]["payload"]
    if any(item["payload"]["outcome"] == "SUCCESS" for item in outcomes):
        assert verdict["verdict"] == "GOAL_MET"
        assert len(verdict["assessment_ids"]) == 1
        assert verdict["method_family_id"] == "family-ml-researcher"
    else:
        assert {item["payload"]["outcome"] for item in outcomes} == {"CLEAN_REFUTATION"}
        assert verdict["verdict"] == "GOAL_NOT_MET"
        assert len(verdict["assessment_ids"]) == 3
        assert verdict["method_family_id"] is None
    processes = [row for row in host.store.list_attempts() if row["kind"] == "PROCESS"]
    if verdict["verdict"] == "GOAL_MET":
        assert any(row["terminal_reason"] == "SUCCEEDED" for row in processes)
        assert all(row["state"] == "CLOSED" and row["terminal_reason"] in {"SUCCEEDED", "CANCELLED"} for row in processes)
    else:
        assert len([row for row in processes if row["terminal_reason"] == "SUCCEEDED"]) == 3
    report = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert report.ok, report.to_dict()


@pytest.mark.parametrize("batch_host", ["managed-up-down"], indirect=True)
def test_manager_can_reduce_then_raise_the_next_batch_within_the_original_limit(batch_host):
    host = batch_host
    _complete(host, timeout=120)
    history = host._batch_history("batch-host", "primary.validation_accuracy")
    assert [len(batch.members) for batch in history.batches] == [2, 1, 2]
    assert len(host.store.list_facts("batch-host", "CanonicalOutcomeAccepted")) == 5
    assert len([row for row in host.store.list_attempts() if row["kind"] == "PROCESS" and row["terminal_reason"] == "SUCCEEDED"]) == 5
    controls = host.store.inspect("SELECT t.payload_json FROM workflow_transitions t JOIN workflow_commands c USING(command_id) WHERE c.command_type='ACCEPT_GRAPH_EVOLUTION' ORDER BY t.rowid")
    assert [json.loads(row["payload_json"])["graph_evolution"][0]["effective_max_forks"] for row in controls] == [1, 2, 2]
    report = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert report.ok, report.to_dict()


@pytest.mark.parametrize("batch_host", ["observer-none"], indirect=True)
def test_pending_observer_closes_before_verdict_and_begin_completion(batch_host):
    host = batch_host
    _complete(host)
    rows = host.store.list_attempts()
    observers = [row for row in rows if json.loads(row["observation_json"]).get("request", {}).get("command") == "OBSERVER"]
    assert len(observers) == 1
    assert observers[0]["state"] == "CLOSED" and observers[0]["terminal_reason"] == "SUCCEEDED"
    transitions = host.store.inspect("SELECT t.revision,c.command_type,t.payload_json FROM workflow_transitions t JOIN workflow_commands c USING(command_id) WHERE t.workflow_id='batch-host' ORDER BY t.revision")
    control = next(row for row in transitions if row["command_type"] == "ACCEPT_EXPERT_INTERACTION")
    verdict = next(row for row in transitions if row["command_type"] == "RECORD_GOAL_VERDICT")
    completing = next(row for row in transitions if row["command_type"] == "BEGIN_COMPLETION")
    assert control["revision"] < verdict["revision"] < completing["revision"]
    assert json.loads(control["payload_json"])["expert_interaction"]["action"] == "observed"
    report = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert report.ok, report.to_dict()


@pytest.mark.parametrize("batch_host", ["managed"], indirect=True)
def test_open_manager_uses_run_capacity_without_becoming_a_scientific_roster_member(batch_host, monkeypatch):
    host = batch_host
    finish = host._finish_fake_agent
    held = []
    def delayed(request, response):
        if request.command is AgentCommand.GRAPH_EVOLVE and not held:
            held.append((request, response))
        else:
            finish(request, response)
    monkeypatch.setattr(host, "_finish_fake_agent", delayed)
    _until(host, lambda: bool(held))
    snapshot = host._policy_snapshot(host.store.latest_transition("batch-host"))
    assert "grounding" not in snapshot.busy_roles
    assert snapshot.live_effect_count == 1
    assert host.store.get_attempt(held[0][0].attempt_id)["state"] == "OPEN"
    finish(*held[0])
    _complete(host)
    report = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert report.ok, report.to_dict()


def test_final_report_runs_after_accepted_verdict_before_completing(batch_host):
    host = batch_host
    _complete(host)
    reports = [json.loads(row["observation_json"]) for row in host.store.list_attempts()
        if json.loads(row["observation_json"]).get("request", {}).get("command") == "FINAL_REPORT"]
    assert len(reports) == 1
    assert reports[0]["request"]["role"] == reports[0]["request"]["component_id"] == "grounding"
    files = reports[0]["response"]["proposed_workspace_artifacts"]
    assert [item["path"] for item in files] == ["FINAL_REPORT.md"]
    document = WorkspaceArtifact.from_dict(files[0]).content_bytes().decode()
    verdict = host.store.list_facts("batch-host", "GoalVerdictRecorded")[0]
    assert verdict["fact_id"] in document and verdict["payload"]["verdict"] in document
    assert all(item["outcome_id"] in document for item in host.store.list_facts("batch-host", "CanonicalOutcomeAccepted"))
    transitions = host.store.inspect("SELECT t.revision,c.command_type,t.payload_json FROM workflow_transitions t JOIN workflow_commands c USING(command_id) WHERE t.workflow_id='batch-host' ORDER BY t.revision")
    requested = next(row for row in transitions if row["command_type"] == "REQUEST_AUXILIARY" and json.loads(row["payload_json"]).get("task_context", {}).get("request_kind") == "FINAL_REPORT")
    assert next(row["revision"] for row in transitions if row["command_type"] == "RECORD_GOAL_VERDICT") < requested["revision"]
    assert requested["revision"] < next(row["revision"] for row in transitions if row["command_type"] == "BEGIN_COMPLETION")


@pytest.mark.parametrize("batch_host", ["final-report-missing"], indirect=True)
def test_exhausted_final_report_fails_control_without_erasing_the_accepted_verdict(batch_host):
    host = batch_host
    _until(host, lambda: host.store.latest_transition("batch-host")["state"] in {"COMPLETED", "FAILED"})
    assert host.store.latest_transition("batch-host")["state"] == "FAILED"
    assert len(host.store.list_facts("batch-host", "GoalVerdictRecorded")) == 1
    rows = host.store.inspect("SELECT t.payload_json,c.command_type FROM workflow_transitions t JOIN workflow_commands c USING(command_id) WHERE t.workflow_id='batch-host' ORDER BY t.revision")
    assert rows[-1]["command_type"] == "FINAL_REPORT_FAILED"
    assert json.loads(rows[-1]["payload_json"])["report_missing"] is True
    assert not any(row["command_type"] == "FAIL_INTEGRITY" for row in rows)
    report = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert report.ok, report.to_dict()


@pytest.mark.parametrize("batch_host", ["leader-round"], indirect=True)
def test_real_cpu_leader_round_reopens_only_its_designated_component_and_gets_a_fresh_observer(batch_host):
    host = batch_host
    # /45 reached 52 successful Agents/four CPU results at 100s, with Leader
    # finishing/control/report work still pending; no scheduling assertion changes.
    _complete(host, timeout=200)
    requests = [json.loads(row["observation_json"])["request"] for row in host.store.list_attempts() if row["kind"] == "AGENT"]
    observers = [request for request in requests if request["command"] == "OBSERVER"]
    assert len(observers) == 4  # each of three original reports plus the Leader's report
    assert len({request["command_id"] for request in observers}) == 4
    leader_batches = host._batch_history("batch-host", "primary.validation_accuracy")
    assert len(leader_batches.batches) == 2
    assert all(len(host._batch_history("batch-host", "primary." + role).batches) == 1 for role in ("scientist-b", "scientist-c"))
    inputs = [(request, item) for request in requests for item in request["input_artifacts"] if item["ref"].endswith("--leader-resources")]
    assert len(inputs) == 1
    request, item = inputs[0]
    assert request["task_context"]["key"]["cycle"] == 2 and request["task_context"]["key"]["subkind"] == "SYNTHESIS"
    from hypoloom.agent_aci import EmbeddedInput
    resources = json.loads(EmbeddedInput.from_dict(item).content_bytes())
    assert set(resources["peer_memory_commits"]) == {"scientist-b", "scientist-c"}
    assert len(host.store.list_facts("batch-host", "CanonicalOutcomeAccepted")) == 4
    events = [json.loads(row["payload_json"])["expert_interaction"] for row in host.store.inspect(
        "SELECT payload_json FROM workflow_transitions WHERE run_id='batch-host' AND json_type(payload_json,'$.expert_interaction')='object'")]
    assert [item["action"] for item in events if item["action"].startswith("leader_")] == ["leader_selected", "leader_round_started", "leader_round_finished"]
    assert not host._participant_controls("batch-host").pauses
    report = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert report.ok, report.to_dict()
