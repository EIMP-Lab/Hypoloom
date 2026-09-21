"""Actual Host manager 2→1 next-cycle capacity, plugins, restart and CPU closure."""

import json
import secrets
import time
from pathlib import Path

from hypoloom.agent_aci import WorkspaceArtifact
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.oracle import Oracle

from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results
from test_claude_final_report_g3 import _FinalReportHostScenario
from test_claude_measurement_g3 import _NoPublicWeb
from test_claude_multichain_g3 import _payload
from test_expert_batch_host import ManagedBatchAgent
from test_expert_memory_g3 import _cycle, _resources
from test_full_cc_host_g3 import _new_host, _request_from_provider, _start_provider


_LEADER_LINE = "Use the request's eligibility and ACI version. Before ACI 21, a candidate needs"


class _ManagedScenario(_FinalReportHostScenario):
    def __init__(self, private_root):
        super().__init__(private_root)
        self.reviser = ManagedBatchAgent()
        self.manager_nonce = "manager-next-trigger-" + secrets.token_hex(16)
        self.manager_evidence = {}
        self.plugin_consumers = set()
        self.fresh_messages = {}

    def _identity(self, request):
        return _cycle(request), *super()._identity(request)

    def _expected_outcome(self, request):
        return "CLEAN_REFUTATION" if _cycle(request) == 1 else "SUCCESS"

    def record(self, document):
        number = super().record(document)
        request = _request_from_provider(document)
        step = self.steps[request.attempt_id]
        try:
            if request.command is AgentCommand.GRAPH_EVOLVE:
                cycle = request.task_context.cycle
                previous = ({2: "manager_skill", 3: "manager_read", 4: "manager_edit", 5: "manager_protection"}
                    if cycle == 1 else {2: "manager_skill", 3: "manager_protection"}).get(step)
                if step == 1:
                    self.fresh_messages[request.attempt_id] = len(document["messages"])
                    if cycle == 2:
                        assert self.manager_nonce in json.dumps(document["messages"]), "manager history was not resumed"
                if previous:
                    identity = "probe_" + request.attempt_id[-16:] + "__" + previous
                    result = _tool_results(document)[identity]
                    assert not result.get("is_error"), result
                    self.manager_evidence[f"{cycle}/{previous}"] = _result_text(result)
                if step == 2 and cycle == 2:
                    fresh = json.dumps(document["messages"][self.fresh_messages[request.attempt_id]:])
                    assert self.manager_nonce in fresh, "new native trigger Skill body was not loaded"
                    self.manager_evidence["second_native_trigger_body"] = fresh
            elif request.command is AgentCommand.LITERATURE_REVIEW and _cycle(request) == 2:
                if step == 4:
                    self.fresh_messages[request.attempt_id] = len(document["messages"])
                if step == 5:
                    fresh = json.dumps(document["messages"][self.fresh_messages[request.attempt_id]:])
                    assert self.evolve_nonce in fresh, "next-cycle expert did not natively load its accepted SELF_EVOLVE revision"
                    self.plugin_consumers.add(request.attempt_id)
        except Exception as exc:
            self.errors.append(f"manager Host {len(self.requests)}: {type(exc).__name__}: {exc}")
        return number

    def _unscoped_tool_calls(self, number):
        request = self.current
        if request is None or self.errors:
            return ()
        step = self.steps[request.attempt_id]
        if request.command is AgentCommand.GRAPH_EVOLVE:
            cycle = request.task_context.cycle
            if step == 1:
                return (("manager_skill", "Skill", {"skill": "hypoloom-grounding:"
                    + ("graph-evolve" if cycle == 1 else "leader-trigger")}),)
            if cycle == 1 and step == 2:
                return (("manager_read", "Read", {"file_path": "/plugin-update/skills/leader-trigger/SKILL.md"}),)
            if cycle == 1 and step == 3:
                return (("manager_edit", "Edit", {"file_path": "/plugin-update/skills/leader-trigger/SKILL.md",
                    "old_string": _LEADER_LINE, "new_string": "Observed next-trigger lesson: " + self.manager_nonce + ".\n\n" + _LEADER_LINE}),)
            if (cycle == 1 and step == 4) or (cycle == 2 and step == 2):
                script = (
                    "import errno, os\nfrom pathlib import Path\n"
                    "assert os.statvfs('/workspace').f_flag & os.ST_RDONLY\n"
                    f"assert {self.manager_nonce!r} in Path('/plugin-update/skills/leader-trigger/SKILL.md').read_text()\n"
                    f"assert ({self.manager_nonce!r} in Path('/expert-plugin/skills/leader-trigger/SKILL.md').read_text()) is {cycle == 2}\n"
                    "assert not Path('/expert-plugin/skills/experiment-report').exists()\n"
                    "assert not Path('/state').exists() and not Path('/memory').exists()\n"
                    "for path in ['/plugin-update/.git', '/expert-plugin/skills/leader-trigger/SKILL.md']:\n"
                    "    try:\n        fd = os.open(path, os.O_WRONLY | os.O_APPEND)\n"
                    "    except OSError as error:\n        assert error.errno == errno.EROFS, (path, error)\n"
                    "    else:\n        os.close(fd)\n        raise AssertionError('manager path writable')\n"
                    "print('MANAGER_REVISION_BOUNDARIES')\n"
                )
                return (("manager_protection", "Bash", {"command": "python3 - <<'PY'\n" + script + "PY", "timeout": 10000}),)
            return self._structured(request)
        if request.command is AgentCommand.SELF_EVOLVE and _cycle(request) == 2 and step >= 3:
            # The next-cycle native Skill and Read confirm the first lesson;
            # no new useful edit is proposed on this second cycle.
            return self._structured(request)
        if request.command is AgentCommand.LITERATURE_REVIEW and _cycle(request) == 2:
            if step == 4:
                return (("read_evolved_expert_skill", "Skill", {"skill": _resources(request)["plugin"]["name"] + ":self-evolve"}),)
            if step == 5:
                return self._structured(request)
        return super()._unscoped_tool_calls(number)


def test_real_host_manager_updates_next_cycle_and_resumes_after_restart(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 14)
    executable = _installed_claude()
    scenario = _ManagedScenario(tmp_path)
    key = "host-only-manager-g3-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    base_url = f"http://127.0.0.1:{provider.server_port}"
    (tmp_path / "settings.json").write_text("{}\n")
    (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(tmp_path, executable, base_url, key)
    host.grounding_web = _NoPublicWeb()
    run_id = "native-manager-host-g3"
    restarted = False
    try:
        payload = _payload(tmp_path, run_id)
        settings = payload["research_contract"]["methodology"]["controls"]["product"]
        settings["goal"]["stop_conditions"]["max_rounds_per_expert"] = 2
        settings["methodology"]["graph_evolution"]["enabled"] = True
        settings["methodology"]["inner_loop"]["max_iterations_per_hypothesis"] = 1
        host.initialize()
        created = host.handle(ControlRequest(schema_version=1, request_id="create-native-manager",
            expected_revision=0, command="CREATE_RUN", payload=payload))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            host.tick()
            assert not scenario.errors, scenario.errors
            if scenario.restart_ready and not restarted:
                assert not host.running
                assert all(handle.request.command is AgentCommand.LITERATURE_REVIEW and _cycle(handle.request) == 2
                           for handle in host.running_agents.values())
                assert len(host.store.inspect("SELECT 1 FROM workflow_commands WHERE command_type='ACCEPT_GRAPH_EVOLUTION'")) == 1
                host.close()
                scenario.restart_completed = True
                host = _new_host(tmp_path, executable, base_url, key)
                host.grounding_web = _NoPublicWeb()
                host.initialize()
                restarted = True
            if host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(0.01)
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED", host.store.list_attempts()
        assert restarted and scenario.plugin_consumers
        assert set(scenario.execution_results) == {(1, "chain-a", 1), (1, "chain-b", 1), (2, "chain-a", 1)}
        history = host._batch_history(run_id, "primary.validation_accuracy")
        assert [len(batch.members) for batch in history.batches] == [2, 1]
        outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        assert sorted(row["payload"]["outcome"] for row in outcomes) == ["CLEAN_REFUTATION", "CLEAN_REFUTATION", "SUCCESS"]
        attempts = host.store.list_attempts()
        assert all(row["state"] == "CLOSED" for row in attempts)
        assert len([row for row in attempts if row["kind"] == "PROCESS"]) == 3
        assert not [row for row in attempts if row["terminal_reason"] not in {"SUCCEEDED", "LOST"}]
        lost = [row for row in attempts if row["terminal_reason"] == "LOST"]
        assert len(lost) == 1
        replay = next(row for row in attempts if row["command_id"] == lost[0]["command_id"] and row["terminal_reason"] == "SUCCEEDED")
        assert "--resume" in json.loads(replay["observation_json"])["session"]["launch_arguments"]
        observations = [json.loads(row["observation_json"]) for row in attempts if row["kind"] == "AGENT"]
        reports = [row for row in observations if row["request"]["command"] == "FINAL_REPORT"]
        assert len(reports) == len(scenario.reports) == 1
        report_observation = reports[0]
        report = scenario.reports[report_observation["request"]["attempt_id"]]
        assert set(report.evidence) == {"report_skill", "report_sources", "report_write", "report_boundaries"}
        assert len(report.requests) == 5
        assert {row["fact_id"]: json.loads(row["payload_json"]) for row in report.context["facts"]} == {
            row["fact_id"]: row["payload"] for row in host.store.list_facts(run_id)}
        report_files = report_observation["response"]["proposed_workspace_artifacts"]
        assert len(report_files) == 1 and report_files[0]["path"] == "FINAL_REPORT.md"
        assert WorkspaceArtifact.from_dict(report_files[0]).content_bytes() == report.report.encode()
        report_archive = host.publisher.root / report_observation["session"]["transcript"]["path"]
        assert (report_archive / "FINAL_REPORT.md").read_bytes() == report.report.encode()
        (tmp_path / "final-report-native-evidence.json").write_text(json.dumps(report.evidence, indent=2))
        managers = sorted((row for row in observations if row["request"]["command"] == "GRAPH_EVOLVE"),
                          key=lambda row: row["request"]["task_context"]["cycle"])
        assert len(managers) == 2 and len(scenario.manager_evidence) == 7
        assert managers[0]["session"]["session_id"] == managers[1]["session"]["session_id"]
        assert managers[1]["session"]["launch_arguments"] == ["--resume", managers[0]["session"]["session_id"]]
        update = managers[0]["plugin_update"]
        assert update["base_commit"] != update["commit"]
        assert managers[1]["plugin_update"] == {"base_commit": update["commit"], "commit": update["commit"]}
        assert len(host.store.inspect("SELECT 1 FROM workflow_commands WHERE command_type='ACCEPT_GRAPH_EVOLUTION'")) == 2
        synthesis = sorted((request for request in scenario.agent_requests.values()
            if request.command is AgentCommand.HYPOTHESIS_PROPOSAL and request.task_context.key.subkind.value == "SYNTHESIS"),
            key=_cycle)
        assert [json.loads(next(item for item in request.input_artifacts if item.ref.endswith("--expert-config")).content_bytes())["effective_max_forks"]
                for request in synthesis] == [2, 1]
        expert_updates = sorted((row for row in observations if row["request"]["command"] == "SELF_EVOLVE"),
                                key=lambda row: row["request"]["task_context"]["key"]["cycle"])
        assert len(expert_updates) == 2
        expert_commit = expert_updates[0]["plugin_update"]["commit"]
        assert expert_commit != expert_updates[0]["plugin_update"]["base_commit"]
        assert {_resources(scenario.agent_requests[identity])["plugin"]["commit"] for identity in scenario.plugin_consumers} == {expert_commit}
        for row in attempts:
            if row["kind"] != "AGENT":
                continue
            observation = json.loads(row["observation_json"])
            if row["terminal_reason"] == "SUCCEEDED":
                assert observation["session"]["transcript_complete"] is True
            archive = host.publisher.root / observation["session"]["transcript"]["path"]
            assert key.encode() not in (archive / "transcript.jsonl").read_bytes()
            if observation["request"]["command"] == "CYCLE_REPORT":
                cycle = observation["request"]["task_context"]["key"]["cycle"]
                assert scenario.nonce in (archive / "memory/MEMORY.md").read_text()
                assert scenario.nonce in (archive / f"memory/rounds/{cycle}.md").read_text()
        assert not host.running and not host.running_agents
        assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"] is not None)
        audit = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
        (tmp_path / "oracle.json").write_text(json.dumps(audit.to_dict(), indent=2))
        assert audit.ok, audit.to_dict()
        assert audit.canonical_outcome_count == 3 and audit.artifact_counts["TRANSCRIPT"] == len(observations)
        assert audit.open_attempt_count == audit.pending_effect_count == 0
    finally:
        (tmp_path / "provider-requests.json").write_text(json.dumps(scenario.requests, indent=2))
        (tmp_path / "provider-errors.json").write_text(json.dumps(scenario.errors, indent=2))
        (tmp_path / "manager-native-evidence.json").write_text(json.dumps(scenario.manager_evidence, indent=2))
        host.close(); provider.shutdown(); provider.server_close(); thread.join(timeout=5)
