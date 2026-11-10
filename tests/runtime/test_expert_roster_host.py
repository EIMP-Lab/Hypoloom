"""More experts than slots must progress through real CPU work and audit."""
from __future__ import annotations

import copy
import json
import secrets
import shutil
import time
from collections import Counter

import pytest

from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand, GoalVerdict
from hypoloom.fake_agent import FakeAgent
from hypoloom.oracle import Oracle
from hypoloom.state_store import StateTransaction
from hypoloom.workflow_host import WorkflowHost

from test_claude_file_tool_cli import _installed_claude
from test_claude_final_report_g3 import _FinalReportHostScenario
from test_claude_measurement_g3 import _NoPublicWeb
from test_claude_multichain_g3 import _payload
from test_full_cc_host_g3 import _new_host, _start_provider


@pytest.fixture(autouse=True)
def admitted_capacities(monkeypatch, tmp_path):
    original = StateTransaction.open_attempt
    samples = []

    def observe(transaction, attempt):
        result = original(transaction, attempt)
        counts = Counter()
        for row in transaction.execute("SELECT kind,json_extract(observation_json,'$.request.command') AS command FROM execution_attempts WHERE run_id=? AND state='OPEN'",
                                       (attempt["run_id"],)).fetchall():
            counts["REVIEW" if row["kind"] == "AGENT" and row["command"] == "ANSWER_RENDEZVOUS" else row["kind"]] += 1
        samples.append(dict(counts))
        assert counts["AGENT"] <= 2 and counts["PROCESS"] <= 1 and counts["REVIEW"] <= 1, counts
        return result

    monkeypatch.setattr(StateTransaction, "open_attempt", observe)
    yield samples
    (tmp_path / "admitted-capacity-samples.json").write_text(json.dumps(samples, indent=2) + "\n")
    assert samples


def roster_payload(tmp_path, run_id, count):
    payload = _payload(tmp_path, run_id)
    contract = payload["research_contract"]
    role, component = contract["roles"][0], contract["components"][0]
    settings = contract["methodology"]["controls"]["product"]
    expert = settings["experts"]["experts"][0]
    identities = [role["id"], *(f"researcher-{i + 1}" for i in range(1, count))]
    protocol = "roster-accuracy"
    contract["protocols"][protocol] = {"result_kind": "metric", "requirements": {
        "required_metrics": ["accuracy"], "required_artifacts": component["required_artifacts"]}}
    contract["roles"] = [{**copy.deepcopy(role), "id": name,
        "protocols": {"default": protocol, "allowed": [protocol]}} for name in identities]
    contract["components"] = [{**copy.deepcopy(component), "id": "primary." + name,
        "name": name, "owner": name, "proposers": [name], "protocol": protocol} for name in identities]
    if count > 1:
        contract["goal"]["summary_metric"] = {"name": "required_component_completion",
            "direction": "MAXIMIZE", "target": str(count), "display_only": True}
    settings["experts"]["experts"] = [{**copy.deepcopy(expert), "name": name} for name in identities]
    settings["experts"]["defaults"].update(max_forks=1, max_hypotheses_per_round=1)
    settings["methodology"]["inner_loop"]["max_iterations_per_hypothesis"] = 1
    contract["budget"].update(max_experiments=count * 2, max_active_agents=2,
        max_active_processes=1, max_active_reviews=1, provider_max_in_flight=1)
    return payload, identities


def assert_closed(host, run_id, identities):
    assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
    attempts = host.store.list_attempts()
    assert attempts and all(row["state"] == "CLOSED" for row in attempts)
    processes = [row for row in attempts if row["kind"] == "PROCESS"]
    assert len(processes) == len(identities)
    assert all(row["terminal_reason"] == "SUCCEEDED" for row in processes)
    outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
    assert len(outcomes) == len(identities)
    assert {row["payload"]["component_id"] for row in outcomes} == {"primary." + role for role in identities}
    assert all(row["payload"]["outcome"] == "SUCCESS" for row in outcomes)
    requests = [json.loads(row["observation_json"])["request"] for row in attempts if row["kind"] == "AGENT"]
    for command in (AgentCommand.HYPOTHESIS_PROPOSAL, AgentCommand.RESULT_ANALYSIS, AgentCommand.CYCLE_REPORT):
        assert {row["role"] for row in requests if row["command"] == command.value} == set(identities)
    # Agent.started_at includes pre-admission asynchronous literature retrieval;
    # the fixture observes actual OPEN rows after every admission instead. Process
    # timestamps additionally prove there was no overlap between ticks.
    events = [(row[key], change) for row in processes
              for key, change in (("started_at", 1), ("ended_at", -1))]
    active = peak = 0
    for _, change in sorted(events, key=lambda event: (event[0], event[1])):
        active += change; peak = max(peak, active)
    assert active == 0 and peak == 1
    report = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
    assert report.ok and report.open_attempt_count == report.pending_effect_count == 0, report.to_dict()


@pytest.mark.parametrize("count", [1, 4, 8])
def test_all_experts_complete_cpu_work_with_one_process_slot(tmp_path, count):
    run_id = "roster-cpu"
    payload, identities = roster_payload(tmp_path, run_id, count)
    host = WorkflowHost(tmp_path / "state", local_agent=FakeAgent(), grounding_web=_NoPublicWeb())
    try:
        host.initialize()
        created = host.handle(ControlRequest(1, "create-roster", 0, "CREATE_RUN", payload))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            host.tick()
            if host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}: break
            time.sleep(0.005)
        assert_closed(host, run_id, identities)
    finally:
        host.close()
        shutil.rmtree((tmp_path / "admitted-target.txt").read_text().strip())


def test_four_experts_share_one_goal_and_drain_after_success(tmp_path):
    run_id = "roster-shared-goal"
    payload, identities = roster_payload(tmp_path, run_id, 4)
    payload["research_contract"]["methodology"]["controls"]["product"]["goal"]["completion_mode"] = "shared_goal"
    host = WorkflowHost(tmp_path / "state", local_agent=FakeAgent(), grounding_web=_NoPublicWeb())
    try:
        host.initialize()
        assert host.handle(ControlRequest(1, "create-shared-roster", 0, "CREATE_RUN", payload)).status == "accepted"
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            host.tick()
            if host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}: break
            time.sleep(0.005)
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
        outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        assert any(row["payload"]["outcome"] == "SUCCESS" for row in outcomes)
        assert len(outcomes) <= len(identities)
        verdicts = host.store.list_facts(run_id, "GoalVerdictRecorded")
        assert len(verdicts) == 1 and verdicts[0]["payload"]["verdict"] == GoalVerdict.GOAL_MET.value
        assert all(row["state"] == "CLOSED" for row in host.store.list_attempts())
        report = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
        assert report.ok and report.open_attempt_count == report.pending_effect_count == 0, report.to_dict()
    finally:
        host.close()
        shutil.rmtree((tmp_path / "admitted-target.txt").read_text().strip())


class RosterScenario(_FinalReportHostScenario):
    def _identity(self, request):
        return request.role, request.component_id, *super()._identity(request)


def test_native_four_experts_close_with_bounded_slots(tmp_path):
    executable = _installed_claude()
    scenario = RosterScenario(tmp_path)
    key = "local-roster-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    (tmp_path / "settings.json").write_text("{}\n")
    (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(tmp_path, executable, f"http://127.0.0.1:{provider.server_port}", key)
    host.grounding_web = _NoPublicWeb()
    run_id = "native-roster"
    groups = set()
    try:
        payload, identities = roster_payload(tmp_path, run_id, 4)
        host.initialize()
        created = host.handle(ControlRequest(1, "create-native-roster", 0, "CREATE_RUN", payload))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            host.tick()
            assert not scenario.errors, scenario.errors
            groups.update(row["process_group_id"] for row in host.store.list_attempts() if row["process_group_id"])
            if host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}: break
            time.sleep(0.01)
        assert_closed(host, run_id, identities)
        assert scenario.local_tests, "native design must execute its CPU verification"
        assert len(scenario.reports) == 1
    finally:
        host.close()
        provider.shutdown(); provider.server_close(); thread.join(timeout=5)
        target = tmp_path / "admitted-target.txt"
        if target.exists(): shutil.rmtree(target.read_text().strip())
    assert not any(process_group_exists(group) for group in groups)
