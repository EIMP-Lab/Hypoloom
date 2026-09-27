"""Tracked six-YAML starter → three experts/two chains → shared-goal CPU stop.

This is an explicit FakeAgent, not native CC or physical-GPU qualification.
Interaction and graph-evolution features remain enabled, but shared-goal success
stops research before optional further optimization. Automatic dashboard/
notification lifetime has separate serve tests; this test exercises the admitted
template through WorkflowHost without changing its goal or product controls.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import time
from collections import Counter
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import yaml

from hypoloom.agent_aci import CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION, with_output_digest
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeSubkind, ResearchNodeTask
from hypoloom.oracle import Oracle
from hypoloom.profile_admission import admit_profile
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_deferred_measurement_host import DeclaredMeasurementAgent
from tests.runtime.test_profile_admission import _materialize_expert_loop_pilot_v3


_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE = _ROOT / "profiles/templates/multi-expert-en"
_RUN = "tracked-v4-template"
_ROLES = {"optimizer", "architect", "evaluator"}
_CHAINS = {"chain-a", "chain-b"}


class TemplateAgent(DeclaredMeasurementAgent):
    """Use the existing C5 Shell/parser fixture, with two actual batch members."""

    def invoke(self, request):
        response = super().invoke(request)
        task = request.task_context
        if (request.command is AgentCommand.HYPOTHESIS_PROPOSAL
                and isinstance(task, ResearchNodeTask)
                and task.key.subkind is ResearchNodeSubkind.SYNTHESIS):
            member, = response.typed_candidate["hypotheses"]
            response = with_output_digest(replace(response, typed_candidate={
                "schema_version": 1,
                "hypotheses": [{"chain_id": chain, "candidate": {
                    **member["candidate"],
                    "claim": f"{request.role} independently tests {chain} with declared median latency.",
                }} for chain in sorted(_CHAINS)],
            }))
        return response


class NoPublicWeb:
    def web_search(self, *_args, **_kwargs):
        raise AssertionError("This scripted template test never contacts public web providers")

    web_fetch = web_search


def _template_profile(tmp_path: Path, mount: Path):
    _, target, data = _materialize_expert_loop_pilot_v3(mount)
    originals = {path.name: path.read_bytes() for path in _TEMPLATE.glob("*.yaml")}
    assert set(originals) == {name + ".yaml" for name in (
        "goal", "experts", "language", "methodology", "notify", "safety")}
    replacements = {
        b"/REPLACE_ME/research-target": str(target).encode(),
        b"/REPLACE_ME/project-runtime": sys.prefix.encode(),
        b"/REPLACE_ME/research-data": str(data).encode(),
    }
    profile = tmp_path / "tracked-profile"
    profile.mkdir()
    materialized = {}
    for name, original in originals.items():
        content = original
        for marker, replacement in replacements.items():
            content = content.replace(marker, replacement)
        (profile / name).write_bytes(content)
        materialized[name] = content
    source = {name[:-5]: yaml.safe_load(content) for name, content in materialized.items()}
    unchanged = {name[:-5]: yaml.safe_load(content) for name, content in originals.items()}
    expected = copy.deepcopy(unchanged)
    expected["methodology"]["runtime"].update(target_repo=str(target), conda_env=sys.prefix)
    expected["safety"]["allow_read_paths"] = [str(data)]
    assert source == expected, "Only the fixture paths/runtime may differ from the tracked template"
    admitted = admit_profile(profile, run_id=_RUN, allow_gpu=False)
    (tmp_path / "profile-admission.json").write_text(json.dumps(admitted.to_dict(), indent=2))
    assert admitted.admitted and admitted.create_run_payload is not None, admitted.to_dict()
    payload = admitted.create_run_payload
    settings = payload["research_contract"]["methodology"]["controls"]["product"]
    assert settings["experts"] == source["experts"]
    expected_methodology = copy.deepcopy({key: value for key, value in source["methodology"].items()
        if key not in {"schema_version", "runtime", "budget"}})
    expected_notify = copy.deepcopy(source["notify"])
    # Authoring-only credential fields never enter the admitted product, even
    # when blank. Keep the exact comparison for every other template control.
    for key in ("openalex_api_key", "unpaywall_email"):
        assert expected_methodology["literature"].pop(key) == ""
    for key in ("bot_token", "chat_id"):
        assert expected_notify["sinks"]["telegram"].pop(key) == ""
    assert settings["methodology"] == expected_methodology
    assert settings["notify"] == expected_notify
    assert {item["name"] for item in settings["experts"]["experts"]} == _ROLES
    assert settings["experts"]["defaults"]["max_forks"] == 2
    assert settings["experts"]["defaults"]["max_hypotheses_per_round"] == 2
    assert settings["methodology"]["interaction"]["leader"]["enabled"] is True
    assert settings["methodology"]["interaction"]["debate"] == {
        "enabled": True, "rounds": 3, "min_interval_seconds": 1800, "min_new_outcomes": 2}
    assert settings["methodology"]["interaction"]["observer"]["coalesce_reports"] is True
    assert settings["methodology"]["graph_evolution"]["enabled"] is True
    assert settings["methodology"]["literature"]["enabled"] is True
    assert settings["notify"]["sinks"]["dashboard"]["enabled"] is True
    assert settings["notify"]["sinks"]["telegram"]["enabled"] is False
    assert settings["safety"]["gpus"] == [] and payload["execution"]["allow_gpu"] is False
    return profile, materialized, payload


def test_tracked_v4_template_three_experts_two_chains_reach_cpu_final_oracle(tmp_path):
    with tempfile.TemporaryDirectory(prefix="hypoloom-v4-template-target-", dir="/var/tmp") as mount:
        profile, profile_bytes, payload = _template_profile(tmp_path, Path(mount))
        admitted_payload = copy.deepcopy(payload)
        host = WorkflowHost(tmp_path / "state", local_agent=TemplateAgent(), grounding_web=NoPublicWeb())
        try:
            host.initialize()
            created = host.handle(ControlRequest(schema_version=1, request_id="create-tracked-v4",
                expected_revision=0, command="CREATE_RUN", payload=payload))
            assert created.status == "accepted", created
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                host.tick()
                if host.store.latest_transition(_RUN)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
                time.sleep(0.005)
            attempts = host.store.list_attempts()
            facts = host.store.list_facts(_RUN)
            transitions = host.store.inspect("SELECT t.*,c.command_type FROM workflow_transitions t "
                "JOIN workflow_commands c USING(command_id) WHERE t.run_id=? ORDER BY t.rowid", (_RUN,))
            audit = Oracle(host.store.database_path, host.publisher.root).audit(_RUN)
            for filename, value in (("attempts.json", attempts), ("facts.json", facts),
                                    ("transitions.json", transitions), ("oracle.json", audit.to_dict())):
                (tmp_path / filename).write_text(json.dumps(value, indent=2))
            assert host.store.latest_transition(_RUN)["state"] == "COMPLETED", attempts
            assert payload == admitted_payload, "Do not edit product controls after template admission"
            assert {path.name: path.read_bytes() for path in profile.iterdir()} == profile_bytes
            assert host.store.get_run(_RUN)["agent_aci_version"] == str(CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION)
            assert all(row["state"] == "CLOSED" for row in attempts), attempts
            observations = [json.loads(row["observation_json"]) for row in attempts if row["kind"] == "AGENT"]
            processes = [row for row in attempts if row["kind"] == "PROCESS"]
            # View preparation and slot scheduling need not launch every chain
            # before the first qualified success stops this unchanged template.
            # Count actual effects below, including cancellation, against their
            # own preregistrations and canonical outcomes rather than a timing-
            # dependent expectation that all six hypotheses execute.
            assert processes
            spawned_processes = []
            for row in processes:
                observation = json.loads(row["observation_json"])
                if observation.get("synthetic_before_spawn") is True:
                    assert row["terminal_reason"] == "CANCELLED"
                    assert observation.get("pid") is None and observation.get("process_group_id") is None
                else:
                    assert observation["pid"] > 0 and observation["process_group_id"] > 0
                    spawned_processes.append(row)
            assert spawned_processes, "goal success must come from a real CPU execution"
            commands = Counter(item["request"]["command"] for item in observations)
            # The tracked template defaults to shared_goal: its first qualified
            # success ends research, not three complete ADJ/report/SELF cycles.
            for command in (AgentCommand.ADJUDICATE, AgentCommand.HYPOTHESIS_UPDATE,
                            AgentCommand.CYCLE_REPORT, AgentCommand.SELF_EVOLVE,
                            AgentCommand.GRAPH_EVOLVE, AgentCommand.OBSERVER,
                            AgentCommand.DEBATE, AgentCommand.DEBATE_JUDGE):
                assert commands[command.value] == 0, commands
            assert commands[AgentCommand.FINAL_REPORT.value] == 1
            batches = [json.loads(row["payload_json"]) for row in transitions
                if row["command_type"] == "ACCEPT_HYPOTHESIS"
                and "hypothesis_batch" in json.loads(row["payload_json"])]
            assert len(batches) == 3
            assert {batch["role"] for batch in batches} == _ROLES
            for batch in batches:
                assert batch["hypothesis_batch"]["cycle"] == 1
                assert set(batch["hypothesis_batch"]["chains"]) == _CHAINS
            designs = [item["request"] for item in observations
                if item["request"]["command"] == AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT.value]
            assert {(item["role"], item["task_context"]["key"]["chain"]) for item in designs} == {
                (role, chain) for role in _ROLES for chain in _CHAINS}
            hypotheses = [row for row in facts if row["fact_type"] == "HypothesisProposed"]
            outcomes = [row for row in facts if row["fact_type"] == "CanonicalOutcomeAccepted"]
            assert len(hypotheses) == 6
            assert len(outcomes) == len(processes)
            preregistrations = [row for row in facts if row["fact_type"] == "ExperimentPreregistered"]
            assert len(preregistrations) == len(processes)
            proposed_ids = {row["hypothesis_id"] for row in hypotheses}
            executed_ids = {row["hypothesis_id"] for row in preregistrations}
            assert len(executed_ids) == len(processes)
            assert executed_ids <= proposed_ids
            assert all(row["payload"].get("measurement") for row in hypotheses)
            verdict, = [row for row in facts if row["fact_type"] == "GoalVerdictRecorded"]
            assessment, = [row for row in facts if row["fact_type"] == "HypothesisAssessed"]
            assert verdict["payload"]["verdict"] == "GOAL_MET"
            assert verdict["payload"]["assessment_ids"] == [assessment["subject_id"]]
            assert assessment["payload"]["assessment"] == "SUPPORTED"
            evidence, = [row for row in facts if row["fact_type"] == "EvidenceLinked"
                and row["subject_id"] in assessment["payload"]["evidence_ids"]]
            winner, = [row for row in outcomes if row["outcome_id"] == evidence["payload"]["outcome_id"]]
            spec, = [row for row in facts if row["fact_type"] == "ExperimentPreregistered"
                and row["execution_id"] == winner["execution_id"]]
            hypothesis, = [row for row in hypotheses if row["hypothesis_id"] == spec["hypothesis_id"]]
            assert spec["hypothesis_id"] == evidence["hypothesis_id"] == assessment["hypothesis_id"]
            assert len({row["payload"]["method_family_id"] for row in
                        (verdict, assessment, evidence, winner, spec, hypothesis)}) == 1
            assert winner["payload"]["outcome"] == "SUCCESS"
            assert winner["payload"]["metric_value"] == "-0.1"
            measurement = spec["payload"]["measurement"]
            assert measurement == hypothesis["payload"]["measurement"]
            assert measurement["metric"]["direction"] == "MINIMIZE"
            assert Decimal(winner["payload"]["metric_value"]) <= Decimal(measurement["metric"]["target"])
            assert spec["created_at"] <= winner["created_at"] <= evidence["created_at"] <= assessment["created_at"] <= verdict["created_at"]
            stop, = [row for row in transitions if row["command_type"] == "RECORD_GOAL_VERDICT"]
            assert stop["fact_id"] == verdict["fact_id"]
            assert (stop["previous_state"], stop["state"]) == ("ACTIVE", "STOPPING")
            assert json.loads(stop["payload_json"])["stop_disposition"] == "COMPLETED"
            stop_time = datetime.fromisoformat(stop["created_at"]).timestamp()
            tail = transitions[transitions.index(stop) + 1:]
            run_tail = [row for row in tail if row["workflow_kind"] == "RUN"]
            assert [(row["command_type"], row["state"]) for row in run_tail] == [
                ("REQUEST_AUXILIARY", "STOPPING"), ("FINISH_STOP", "COMPLETED")]
            final_task = json.loads(run_tail[0]["payload_json"])["task_context"]
            assert (final_task["kind"], final_task["request_kind"]) == ("AUXILIARY", "FINAL_REPORT")
            assert json.loads(run_tail[1]["payload_json"])["stop_disposition"] == "COMPLETED"
            assert not any(row["command_type"] in {"CREATE_EXPERIMENT", "EXECUTE", "OPEN_RETRY_ATTEMPT",
                "REQUEST_RESEARCH_NODE", "ACCEPT_HYPOTHESIS"} for row in tail)
            final, = [row for row in attempts if row["kind"] == "AGENT"
                and json.loads(row["observation_json"])["request"]["command"] == "FINAL_REPORT"]
            assert final["terminal_reason"] == "SUCCEEDED" and final["started_at"] >= stop_time
            for row in attempts:
                if row is final:
                    continue
                observation = json.loads(row["observation_json"])
                if observation.get("synthetic_before_spawn") is True:
                    assert row["kind"] == "PROCESS" and row["terminal_reason"] == "CANCELLED"
                    assert row["started_at"] >= stop_time and row["process_group_id"] is None
                else:
                    assert row["started_at"] <= stop_time
                assert row["ended_at"] <= final["started_at"]
                if row["terminal_reason"] == "CANCELLED":
                    assert row["ended_at"] >= stop_time
                    if row["kind"] == "AGENT":
                        assert (observation.get("failure_code"), observation.get("failure_stage")) == (
                            "run_stopping", "host_admission") or observation.get("response_discarded_after_stop") is True
                else:
                    assert row["terminal_reason"] == "SUCCEEDED", row
            by_execution = {row["execution_id"]: row for row in outcomes}
            assert len(by_execution) == len(processes) == len({row["execution_id"] for row in processes})
            for process in processes:
                outcome = by_execution[process["execution_id"]]
                assert outcome["experiment_id"] == process["experiment_id"]
                assert outcome["payload"]["outcome"] == {
                    "SUCCEEDED": "SUCCESS", "CANCELLED": "CANCELLED"}[process["terminal_reason"]]
                assert outcome["payload"]["metric_value"] == ("-0.1" if process["terminal_reason"] == "SUCCEEDED" else None)
            assert audit.ok, audit.to_dict()
            assert audit.open_attempt_count == audit.pending_effect_count == 0
            assert audit.duplicate_effect_count == 0
            assert not audit.owned_process_residue
            (tmp_path / "template-closure.json").write_text(json.dumps({
                "source_template": "profiles/templates/multi-expert-en",
                "agent_adapter": TemplateAgent.adapter_version, "native_cc": False,
                "physical_gpu": False, "automatic_services_tested": False,
                "run_id": _RUN, "state": "COMPLETED", "roles": sorted(_ROLES),
                "chains_per_role": 2, "process_attempts": len(processes), "commands": dict(commands),
                "spawned_processes": len(spawned_processes),
                "cancelled_before_spawn": len(processes) - len(spawned_processes),
                "attempt_terminal_counts": dict(Counter(row["kind"] + ":" + row["terminal_reason"] for row in attempts)),
                "outcome_counts": dict(Counter(row["payload"]["outcome"] for row in outcomes)),
                "goal_verdict_id": verdict["subject_id"], "winning_assessment_id": assessment["subject_id"],
                "winning_outcome_id": winner["outcome_id"], "success_stop_revision": stop["revision"],
                "oracle_ok": audit.ok,
            }, indent=2))
        finally:
            host.close()
