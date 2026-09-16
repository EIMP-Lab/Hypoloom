"""Shared-goal early finish across actual CPU work and durable Host restart.

Only Agent decisions are fake. Experiment processes, bwrap, publication,
scientific acceptance and the final report validator are production consumers.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from hypoloom.agent_aci import (
    AgentRequest,
    WorkspaceArtifact,
    with_output_digest,
)
from hypoloom.agent_sessions import AgentSessions
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.compute_resources import (
    ComputeCapabilities,
    LocalComputeResources,
)
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import _new_file_patch
from hypoloom.oracle import Oracle
from hypoloom.profile_v4 import project_profile_v4
from hypoloom.state_store import StateTransaction
from hypoloom.workflow_host import WorkflowHost

from tests.runtime.test_combined_interaction_g3 import FakeSessionHost
from tests.runtime.test_expert_batch_host import SharedGoalAgent
from tests.runtime.test_expert_loop_host import _pilot_payload
from tests.runtime.test_profile_v4 import documents


RUN = "shared-goal-finish"
WINNER = "ml-researcher"
PEER = "scientist-b"


class _ParallelGoalAgent(SharedGoalAgent):
    def __init__(self):
        super().__init__(all_refuted=False)

    def invoke(self, request):
        response = super().invoke(request)
        if request.command is AgentCommand.RESULT_ANALYSIS and request.role == WINNER:
            response = with_output_digest(
                replace(
                    response,
                    typed_candidate={
                        **response.typed_candidate,
                        "iteration_disposition": "ITERATE",
                    },
                )
            )
        if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            original = next(
                item.content_bytes()
                for item in response.proposed_workspace_artifacts
                if item.path == "experiment.py"
            )
            # Ordinary fixture input coordination, never a production marker.
            # Both actual children must be running before either can publish.
            barrier = (
                "release = Path(json.loads(os.environ['HYPOLOOM_READ_ROOTS'])[0]) / 'release'\n"
                "wait_until = time.monotonic() + 30\n"
                "while not release.exists() and time.monotonic() < wait_until: time.sleep(0.01)\n"
                "assert release.exists(), 'the test never observed both real CPU processes'\n"
                # The peer must stay alive until the Host cancels it; a fixed
                # sleep races winner acceptance on slower runs. Existing Host
                # loop and process deadlines still bound a failed stop.
                + ("while True: time.sleep(1)\n" if request.role == PEER else "")
            ).encode()
            source = original.replace(
                b"from pathlib import Path\n", b"from pathlib import Path\n" + barrier
            )
            assert source != original
            response = with_output_digest(
                replace(
                    response,
                    proposed_workspace_artifacts=(
                        WorkspaceArtifact.from_bytes("experiment.py", source),
                        WorkspaceArtifact.from_bytes(
                            "implementation.patch",
                            _new_file_patch("experiment.py", source),
                        ),
                    ),
                )
            )
        return response


def _payload(root):
    mount = Path(tempfile.mkdtemp(prefix="shared-goal-cpu-", dir="/var/tmp"))
    payload = _pilot_payload(root, mount, RUN)
    contract = payload["research_contract"]
    settings = project_profile_v4(**documents()).settings
    settings["goal"]["completion_mode"] = "shared_goal"
    settings["goal"]["stop_conditions"]["max_rounds_per_expert"] = 2
    settings["experts"]["experts"][0]["name"] = WINNER
    settings["experts"]["defaults"].update(max_forks=1, max_hypotheses_per_round=1)
    settings["methodology"]["inner_loop"] = {
        "max_iterations_per_hypothesis": 2,
        "repair_attempts": 1,
    }
    settings["methodology"]["interaction"] = {
        "leader": {
            "enabled": True,
            "check_after": "cycle_report",
            "min_rounds_before_leader": 1,
        },
        "debate": {"enabled": True, "rounds": 3},
    }
    settings["methodology"]["graph_evolution"]["enabled"] = False
    contract["protocols"]["shared-accuracy"] = {
        "result_kind": "metric",
        "requirements": {"required_metrics": ["accuracy"]},
    }
    contract["components"][0]["protocol"] = "shared-accuracy"
    contract["roles"][0]["protocols"] = {
        "default": "shared-accuracy",
        "allowed": ["shared-accuracy"],
    }
    contract["roles"][0]["budget"]["max_experiments"] = 2
    contract["roles"].append({**copy.deepcopy(contract["roles"][0]), "id": PEER})
    component = copy.deepcopy(contract["components"][0])
    component.update(id="primary." + PEER, name=PEER, owner=PEER, proposers=[PEER])
    contract["components"].append(component)
    settings["experts"]["experts"].append(
        {**copy.deepcopy(settings["experts"]["experts"][0]), "name": PEER}
    )
    contract["methodology"]["controls"]["product"] = settings
    contract["budget"].update(
        max_experiments=4,
        max_active_agents=2,
        max_active_processes=2,
        max_active_reviews=2,
    )
    contract["goal"]["summary_metric"] = {
        "name": "required_component_completion",
        "direction": "MAXIMIZE",
        "target": "2",
        "display_only": True,
    }
    payload["execution"]["read_roots"] = [str(mount / "data")]
    contract["safety"]["read_roots"] = payload["execution"]["read_roots"]
    (root / "fixture-paths.json").write_text(json.dumps({"mount": str(mount)}))
    (root / "payload.json").write_text(json.dumps(payload))
    return payload


def _host(root):
    return WorkflowHost(
        root / "state",
        local_agent=_ParallelGoalAgent(),
        compute_resources=LocalComputeResources(
            inventory_provider=lambda: ComputeCapabilities(gpus=())
        ),
    )


def _run(root, *, crash_report=False, initialize_run=True):
    payload = json.loads((root / "payload.json").read_text())
    release = Path(payload["execution"]["read_roots"][0]) / "release"
    host = _host(root)
    saw_two = saw_winner_with_live_peer = False
    finish = host._finish_fake_agent
    crash = host._crash

    def observe_goal_commit(point):
        nonlocal saw_winner_with_live_peer
        if point == "after_goal_verdict_commit":
            live = [
                row
                for row in host.store.list_attempts()
                if row["kind"] == "PROCESS" and row["state"] == "OPEN"
            ]
            assert len(live) == 1 and process_group_exists(live[0]["process_group_id"])
            head = host.store.latest_transition(RUN)
            assert head["state"] == "STOPPING"
            saw_winner_with_live_peer = True
            (root / "goal-commit-with-live-peer.json").write_text(
                json.dumps(
                    {
                        "head": head,
                        "verdict": host.store.list_facts(RUN, "GoalVerdictRecorded"),
                        "live": live,
                    },
                    indent=2,
                )
            )
            if not crash_report:
                request = ControlRequest(
                    1,
                    "redundant-cancel-after-shared-goal",
                    head["revision"],
                    "CANCEL",
                    {
                        "run_id": RUN,
                        "cause": "redundant stop after accepted shared goal",
                    },
                )
                response = host.handle(request)
                transition = host.store.latest_transition(RUN)
                (root / "redundant-cancel.json").write_text(
                    json.dumps(
                        {
                            "request": request.to_mapping(),
                            "response": response.to_mapping(),
                            "transition": transition,
                        },
                        indent=2,
                    )
                )
                assert response.status == "accepted", response
                assert transition["state"] == "STOPPING"
                assert (
                    json.loads(transition["payload_json"])["stop_disposition"]
                    == "COMPLETED"
                )
        crash(point)

    def finish_or_crash(request, response):
        if crash_report and request.command is AgentCommand.FINAL_REPORT:
            assert host.store.get_attempt(request.attempt_id)["state"] == "OPEN"
            assert not host.running
            os._exit(
                86
            )  # Abrupt Host exit, not orderly close or a fabricated LOST row.
        finish(request, response)

    host._finish_fake_agent = finish_or_crash
    host._crash = observe_goal_commit
    try:
        host.initialize()
        if initialize_run:
            created = host.handle(
                ControlRequest(1, "create-shared-goal-finish", 0, "CREATE_RUN", payload)
            )
            assert created.status == "accepted", created
        deadline = time.monotonic() + 75
        while time.monotonic() < deadline:
            host.tick()
            live = [
                row
                for row in host.store.list_attempts()
                if row["kind"] == "PROCESS" and row["state"] == "OPEN"
            ]
            if len(live) == 2 and not release.exists():
                assert all(
                    process_group_exists(row["process_group_id"]) for row in live
                )
                saw_two = True
                release.write_text("both real CPU processes have started\n")
                (root / "parallel-processes.json").write_text(
                    json.dumps(live, indent=2)
                )
            supported = [
                row
                for row in host.store.list_facts(RUN, "HypothesisAssessed")
                if row["payload"]["assessment"] == "SUPPORTED"
            ]
            if supported and live and not saw_winner_with_live_peer:
                assert all(
                    process_group_exists(row["process_group_id"]) for row in live
                )
                saw_winner_with_live_peer = True
                (root / "winner-with-live-peer.json").write_text(
                    json.dumps({"supported": supported, "live": live}, indent=2)
                )
            if host.store.latest_transition(RUN)["state"] in {
                "COMPLETED",
                "FAILED",
                "CANCELLED",
            }:
                break
            time.sleep(0.005)
        if initialize_run:
            assert saw_two and saw_winner_with_live_peer
        _assert_complete(host, root)
    finally:
        host.close()


def _assert_complete(host, root):
    attempts = host.store.list_attempts()
    (root / "attempts.json").write_text(json.dumps(attempts, indent=2))
    assert host.store.latest_transition(RUN)["state"] == "COMPLETED", attempts
    assert all(row["state"] == "CLOSED" for row in attempts)
    processes = [row for row in attempts if row["kind"] == "PROCESS"]
    assert len(processes) == 2 and all(row["process_group_id"] for row in processes)
    assert sorted(row["terminal_reason"] for row in processes) == [
        "CANCELLED",
        "SUCCEEDED",
    ]
    assert not any(
        json.loads(row["observation_json"]).get("synthetic_before_spawn")
        for row in processes
    )
    outcomes = host.store.list_facts(RUN, "CanonicalOutcomeAccepted")
    assert len(outcomes) == 2
    assert sorted(row["payload"]["outcome"] for row in outcomes) == [
        "CANCELLED",
        "SUCCESS",
    ]
    verdicts = host.store.list_facts(RUN, "GoalVerdictRecorded")
    assert len(verdicts) == 1 and verdicts[0]["payload"]["verdict"] == "GOAL_MET"
    supported = next(
        row
        for row in host.store.list_facts(RUN, "HypothesisAssessed")
        if row["payload"]["assessment"] == "SUPPORTED"
    )
    assert verdicts[0]["payload"]["assessment_ids"] == [supported["subject_id"]]
    observations = [
        (row, json.loads(row["observation_json"]))
        for row in attempts
        if row["kind"] == "AGENT"
    ]
    winner_analyses = [
        observation
        for row, observation in observations
        if row["terminal_reason"] == "SUCCEEDED"
        and observation["request"]["command"] == "RESULT_ANALYSIS"
        and observation["request"]["role"] == WINNER
    ]
    assert len(winner_analyses) == 1
    assert (
        winner_analyses[0]["response"]["typed_candidate"]["iteration_disposition"]
        == "ITERATE"
    )
    # The declared cap permits another iteration; the accepted criterion, not
    # the cap or the model's suggestion, is what prevents any ordinal-2 work.
    assert all(
        AgentRequest.from_dict(
            observation["request"]
        ).task_context.key.experiment_ordinal
        == 1
        for _, observation in observations
        if observation["request"]["command"]
        in {"EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION", "RESULT_ANALYSIS"}
    )
    reports = [
        (row, observation)
        for row, observation in observations
        if observation["request"]["command"] == "FINAL_REPORT"
    ]
    accepted = [
        (row, observation)
        for row, observation in reports
        if row["terminal_reason"] == "SUCCEEDED"
    ]
    assert len(accepted) == 1
    report_row, report = accepted[0]
    assert all(row["ended_at"] <= report_row["started_at"] for row in processes)
    request = AgentRequest.from_dict(report["request"])
    context = json.loads(
        next(
            item.content_bytes()
            for item in request.input_artifacts
            if item.ref.endswith("--final-report-context")
        )
    )
    actual = {row["fact_id"] for row in host.store.list_facts(RUN)}
    assert {row["fact_id"] for row in context["facts"]} == actual
    text = (
        WorkspaceArtifact.from_dict(
            report["response"]["proposed_workspace_artifacts"][0]
        )
        .content_bytes()
        .decode()
    )
    assert verdicts[0]["fact_id"] in text and "GOAL_MET" in text
    assert all(row["payload"]["outcome_id"] in text for row in outcomes)
    forbidden = {
        "ADJUDICATE",
        "HYPOTHESIS_UPDATE",
        "CYCLE_REPORT",
        "SELF_EVOLVE",
        "OBSERVER",
        "DEBATE",
        "DEBATE_JUDGE",
        "GRAPH_EVOLVE",
    }
    assert not [
        o["request"]["command"]
        for _, o in observations
        if o["request"]["command"] in forbidden
    ]
    assert (
        not host.running and not host.running_agents and not host.local_execution_agents
    )
    assert all(not process_group_exists(row["process_group_id"]) for row in processes)
    audit = Oracle(host.store.database_path, host.publisher.root).audit(RUN)
    (root / "oracle.json").write_text(audit.to_json())
    assert audit.ok and audit.canonical_outcome_count == 2, audit.to_dict()
    assert (
        audit.open_attempt_count == audit.pending_effect_count == 0
        and not audit.owned_process_residue
    )


def test_shared_goal_stops_new_research_while_peer_cpu_is_running(tmp_path):
    _payload(tmp_path)
    _run(tmp_path)


def test_shared_goal_restart_after_open_final_report_does_not_repeat_cpu(tmp_path):
    _payload(tmp_path)
    with (tmp_path / "crashed-host.log").open("wb") as output:
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, sys.argv[2]); from pathlib import Path; from tests.runtime.test_shared_goal_finish_g3 import _run; _run(Path(sys.argv[1]), crash_report=True)",
                str(tmp_path),
                str(Path(__file__).resolve().parent),
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=90,
            check=False,
        )
    assert child.returncode == 86, (tmp_path / "crashed-host.log").read_text()
    assert (tmp_path / "goal-commit-with-live-peer.json").is_file()
    _run(tmp_path, initialize_run=False)
    attempts = json.loads((tmp_path / "attempts.json").read_text())
    reports = [
        row
        for row in attempts
        if row["kind"] == "AGENT"
        and json.loads(row["observation_json"])["request"]["command"] == "FINAL_REPORT"
    ]
    assert sorted(row["terminal_reason"] for row in reports) == ["LOST", "SUCCEEDED"]
    assert len({row["command_id"] for row in reports}) == 1
    requests = [json.loads(row["observation_json"])["request"] for row in reports]
    assert requests[0]["input_artifacts"] == requests[1]["input_artifacts"]


class _DebateGoalAgent(SharedGoalAgent):
    def __init__(self):
        super().__init__(all_refuted=True)
        self.selected_debate = False

    def invoke(self, request):
        response = super().invoke(request)
        if (
            request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT
            and request.role == "scientist-c"
        ):
            source = next(
                item.content_bytes()
                for item in response.proposed_workspace_artifacts
                if item.path == "experiment.py"
            )
            source = source.replace(b'"accuracy": 0.50', b'"accuracy": 0.55')
            barrier = (
                "release = Path(json.loads(os.environ['HYPOLOOM_READ_ROOTS'])[0]) / 'debate-open'\n"
                "wait_until = time.monotonic() + 75\n"
                "while not release.exists() and time.monotonic() < wait_until: time.sleep(0.01)\n"
                "assert release.exists(), 'the Host never opened its admitted Debate'\n"
            ).encode()
            source = source.replace(
                b"from pathlib import Path\n", b"from pathlib import Path\n" + barrier
            )
            response = with_output_digest(
                replace(
                    response,
                    proposed_workspace_artifacts=(
                        WorkspaceArtifact.from_bytes("experiment.py", source),
                        WorkspaceArtifact.from_bytes(
                            "implementation.patch",
                            _new_file_patch("experiment.py", source),
                        ),
                    ),
                )
            )
        if request.command is AgentCommand.OBSERVER and not self.selected_debate:
            context = json.loads(
                next(
                    item.content_bytes()
                    for item in request.input_artifacts
                    if item.ref.endswith("--interaction-context")
                )
            )
            reports = [
                json.loads(item.content_bytes())
                for item in request.input_artifacts
                if item.ref.endswith("--response")
            ]
            completed_reports = [
                item
                for item in reports
                if item.get("typed_candidate", {}).get("task_kind") == "CYCLE_REPORT"
            ]
            participants = [
                item
                for item in context["debate_participants"]
                if item["scope"]["role"] in {WINNER, PEER}
            ]
            if (
                "debate" in context["allowed_actions"]
                and len(completed_reports) == 2
                and len(participants) == 2
            ):
                self.selected_debate = True
                response = with_output_digest(
                    replace(
                        response,
                        typed_candidate={
                            "action": "debate",
                            "leader": None,
                            "debate": {
                                "participants": participants,
                                "topic": "Compare the two actual refuted mechanisms.",
                            },
                            "supporting_refs": [
                                item["fact_id"]
                                for item in context["facts"]
                                if item["fact_type"] == "HypothesisAssessed"
                            ],
                            "rationale": "Both experiments and completed reports exist; compare their observed limitations.",
                        },
                    )
                )
        return response


class _DeferredDebateHost(FakeSessionHost):
    """Defer one fake transport reply; OPEN/stop/discard remain ordinary Host paths."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.deferred_debate = None

    def _finish_fake_agent(self, request, raw):
        if request.command is AgentCommand.DEBATE:
            assert self.deferred_debate is None
            self.deferred_debate = (request, raw)
        else:
            super()._finish_fake_agent(request, raw)


def test_shared_goal_aborts_actual_open_debate_after_third_cpu_succeeds(
    tmp_path, monkeypatch
):
    payload = _payload(tmp_path)
    contract = payload["research_contract"]
    settings = contract["methodology"]["controls"]["product"]
    # This independent scenario retains COMPLETE and its original single
    # iteration so the two refutations can reach the actual Debate trigger.
    settings["methodology"]["inner_loop"]["max_iterations_per_hypothesis"] = 1
    contract["roles"].append(
        {**copy.deepcopy(contract["roles"][0]), "id": "scientist-c"}
    )
    component = copy.deepcopy(contract["components"][0])
    component.update(
        id="primary.scientist-c",
        name="scientist-c",
        owner="scientist-c",
        proposers=["scientist-c"],
    )
    contract["components"].append(component)
    contract["goal"]["summary_metric"]["target"] = "3"
    contract["budget"].update(
        max_active_agents=3, max_active_processes=3, max_active_reviews=3
    )
    settings["experts"]["experts"].append(
        {**copy.deepcopy(settings["experts"]["experts"][0]), "name": "scientist-c"}
    )
    (tmp_path / "payload.json").write_text(json.dumps(payload))

    # Exactly the existing fake-session integration seam: bind before OPEN,
    # actual SessionWorkspace/archives for accepted source attempts, no forged
    # closed session identity or injected interaction acceptance.
    original_open = StateTransaction.open_attempt

    def open_attempt(tx, attempt):
        key = "observation" if "observation" in attempt else "payload"
        if attempt.get("kind") == "AGENT" and attempt.get(key, {}).get("request"):
            attempt = copy.deepcopy(attempt)
            attempt[key]["session_binding"] = AgentSessions.binding(
                AgentRequest.from_dict(attempt[key]["request"])
            )
        return original_open(tx, attempt)

    monkeypatch.setattr(StateTransaction, "open_attempt", open_attempt)
    agent = _DebateGoalAgent()
    host = _DeferredDebateHost(
        tmp_path / "state",
        local_agent=agent,
        compute_resources=LocalComputeResources(
            inventory_provider=lambda: ComputeCapabilities(gpus=())
        ),
    )
    observed_open = observed_stop = False
    original_pauses = ()
    try:
        host.initialize()
        created = host.handle(
            ControlRequest(1, "create-shared-goal-debate", 0, "CREATE_RUN", payload)
        )
        assert created.status == "accepted", created
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            host.tick()
            state = host.store.latest_transition(RUN)["state"]
            if host.deferred_debate is not None:
                request, response = host.deferred_debate
                attempt = host.store.get_attempt(request.attempt_id)
                if not observed_open:
                    assert attempt["state"] == "OPEN" and state == "ACTIVE"
                    assert (
                        len(host.store.list_facts(RUN, "CanonicalOutcomeAccepted")) == 2
                    )
                    assert {
                        item["payload"]["outcome"]
                        for item in host.store.list_facts(
                            RUN, "CanonicalOutcomeAccepted"
                        )
                    } == {"CLEAN_REFUTATION"}
                    assert any(
                        row["kind"] == "PROCESS"
                        and row["state"] == "OPEN"
                        and process_group_exists(row["process_group_id"])
                        for row in host.store.list_attempts()
                    )
                    original_pauses = host._participant_controls(RUN).pauses
                    assert len(original_pauses) == 2 and {
                        pause.scope.role for pause in original_pauses
                    } == {WINNER, PEER}
                    (tmp_path / "debate-open.json").write_text(
                        json.dumps(
                            {
                                "attempt": attempt,
                                "pauses": host._participant_controls(RUN).to_dict(),
                            },
                            indent=2,
                        )
                    )
                    (
                        Path(payload["execution"]["read_roots"][0]) / "debate-open"
                    ).write_text("a real admitted Debate attempt is OPEN\n")
                    observed_open = True
                if state == "STOPPING":
                    observed_stop = True
                    if (
                        any(
                            event.get("action") == "debate_aborted"
                            for _, event in host.interaction._events(RUN)
                        )
                        and attempt["state"] == "OPEN"
                    ):
                        # A deferred transport can race cancellation. Its late
                        # valid reply must close CANCELLED, never an accepted round.
                        WorkflowHost._finish_fake_agent(host, request, response)
            if state in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(0.005)
        assert observed_open and observed_stop and agent.selected_debate
        assert host.store.latest_transition(RUN)["state"] == "COMPLETED"
        attempts = host.store.list_attempts()
        (tmp_path / "attempts.json").write_text(json.dumps(attempts, indent=2))
        assert all(row["state"] == "CLOSED" for row in attempts)
        processes = [row for row in attempts if row["kind"] == "PROCESS"]
        assert len(processes) == 3 and all(
            row["terminal_reason"] == "SUCCEEDED" for row in processes
        )
        observations = [
            (row, json.loads(row["observation_json"]))
            for row in attempts
            if row["kind"] == "AGENT"
        ]
        debate = [
            (row, observation)
            for row, observation in observations
            if observation["request"]["command"] == "DEBATE"
        ]
        assert len(debate) == 1 and debate[0][0]["terminal_reason"] == "CANCELLED"
        assert (
            debate[0][1]["response_discarded_after_stop"] is True
            and "response" not in debate[0][1]
        )
        assert not [
            observation
            for _, observation in observations
            if observation["request"]["command"] == "DEBATE_JUDGE"
        ]
        events = host.interaction._events(RUN)
        (tmp_path / "interactions.json").write_text(json.dumps(events, indent=2))
        actions = [event["action"] for _, event in events]
        for action in ("debate_proposed", "debate_started", "debate_aborted"):
            assert actions.count(action) == 1
        assert not {
            "debate_verdict",
            "debate_failed",
            "debate_restarts_designated",
            "debate_restarts_finished",
        } & set(actions)
        assert not host._participant_controls(RUN).pauses
        aborted = next(
            event for _, event in events if event["action"] == "debate_aborted"
        )
        assert {pause.reason for pause in original_pauses} == {
            "debate:" + aborted["debate_id"]
        }
        after_stop = host.store.inspect(
            "SELECT t.* FROM workflow_transitions t JOIN workflow_commands c USING(command_id) WHERE t.run_id=? AND c.command_type='RECORD_GOAL_VERDICT'",
            (RUN,),
        )[0]
        late_nodes = host.store.inspect(
            "SELECT c.request_json FROM workflow_transitions t JOIN workflow_commands c USING(command_id) WHERE t.run_id=? AND t.workflow_kind='RUN' AND t.revision>? AND c.command_type IN('REQUEST_RESEARCH_NODE','REQUEST_AUXILIARY')",
            (RUN, after_stop["revision"]),
        )
        assert late_nodes and all(
            json.loads(row["request_json"])["task_context"].get("request_kind")
            == "FINAL_REPORT"
            for row in late_nodes
        )
        report = next(
            observation
            for row, observation in observations
            if row["terminal_reason"] == "SUCCEEDED"
            and observation["request"]["command"] == "FINAL_REPORT"
        )
        verdicts = host.store.list_facts(RUN, "GoalVerdictRecorded")
        assert len(verdicts) == 1 and verdicts[0]["payload"]["verdict"] == "GOAL_MET"
        report_file = WorkspaceArtifact.from_dict(
            report["response"]["proposed_workspace_artifacts"][0]
        )
        assert verdicts[0]["fact_id"].encode() in report_file.content_bytes()
        assert all(
            row["payload"]["outcome_id"].encode() in report_file.content_bytes()
            for row in host.store.list_facts(RUN, "CanonicalOutcomeAccepted")
        )
        assert (
            not host.running
            and not host.running_agents
            and not host.local_execution_agents
        )
        assert all(
            not process_group_exists(row["process_group_id"]) for row in processes
        )
        audit = Oracle(host.store.database_path, host.publisher.root).audit(RUN)
        (tmp_path / "oracle.json").write_text(audit.to_json())
        assert audit.ok and audit.canonical_outcome_count == 3, audit.to_dict()
        assert (
            audit.open_attempt_count == audit.pending_effect_count == 0
            and not audit.owned_process_residue
        )
    finally:
        host.close()
