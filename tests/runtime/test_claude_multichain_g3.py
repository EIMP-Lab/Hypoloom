"""Two native CC chain sessions, four scientific CPU experiments and adjudication.

The model and HTTPS content are scripted locally. This exercises WorkflowHost,
not two separately mocked adapter/Process seams. SELF_EVOLVE proves the actual
single-cycle update commit, not a later cycle's selection of that revision.
"""

from __future__ import annotations

import json
import copy
import secrets
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from hypoloom.agent_aci import with_output_digest
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.expert_plugin import ExpertPluginStore
from hypoloom.observation_api import build_product_observation
from hypoloom.oracle import Oracle
from hypoloom.profile_v4 import project_profile_v4

from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results
from test_claude_measurement_g3 import _NoPublicWeb
from test_expert_batch_host import IteratingBatchAgent
from test_expert_loop_host import _pilot_payload
from test_expert_memory_g3 import _MemoryScenario, _resources
from test_full_cc_host_g3 import _MODEL, _new_host, _request_from_provider, _start_provider
from test_profile_v4 import documents


_INNER = {AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
          AgentCommand.EXPERIMENT_EXECUTION, AgentCommand.RESULT_ANALYSIS}
_EVOLVE_LINE = "Use actual cycle reports, accepted experiment results and failure/repair traces to"


class _PruningBatchAgent(IteratingBatchAgent):
    def invoke(self, request):
        response = super().invoke(request)
        if request.command is AgentCommand.ADJUDICATE:
            response = with_output_digest(replace(response, typed_candidate={
                **response.typed_candidate, "keep": ["chain-a"], "prune": ["chain-b"],
                "best_chain": "chain-a", "summary": "Both accepted tips succeeded. Keep the stable first chain and control-prune the duplicate without refuting its evidence."}))
        return response


def _chain_iteration(request):
    key = request.task_context.key
    return key.chain, key.experiment_ordinal


class _BatchScenario(_MemoryScenario):
    def __init__(self, private_root: Path):
        super().__init__(private_root, "batch-report-" + secrets.token_hex(12))
        self.reviser = _PruningBatchAgent()
        self.chain_nonces = {chain: "private-tool-context-" + secrets.token_hex(16)
                             for chain in ("chain-a", "chain-b")}
        self.designs: dict[tuple[str, int], Mapping[str, Any]] = {}
        self.experiments: dict[tuple[str, int], str] = {}
        self.execution_results: dict[tuple[str, int], dict[str, Any]] = {}
        self.evolve_nonce = "observed-local-repair-lesson-" + secrets.token_hex(16)
        self.evolve_evidence: dict[str, str] = {}
        self.local_tests: set[tuple[str, int]] = set()
        self.issued_tool_ids: set[str] = set()

    def _identity(self, request):
        return _chain_iteration(request)

    def _expected_outcome(self, request):
        return "SUCCESS"

    def record(self, document):
        actual_document = document
        # Every real provider tool-use ID is unique across the resumed native
        # session. Normalize only the helper's lookup copy; retain actual wire
        # messages as evidence. CC marks reused IDs as interrupted otherwise.
        document = copy.deepcopy(document)
        for message in document.get("messages", ()):
            for block in message.get("content", ()):
                if not isinstance(block, dict):
                    continue
                key = "tool_use_id" if block.get("type") == "tool_result" else "id"
                if str(block.get(key, "")).startswith("probe_"):
                    block[key] = block[key].split("__", 1)[1]
        request = _request_from_provider(document)
        custom = request.command in {AgentCommand.EXPERIMENT_EXECUTION, AgentCommand.SELF_EVOLVE}
        if custom:
            self.requests.append(document)
            self.current = request
            self.agent_requests[request.attempt_id] = request
            self.steps[request.attempt_id] += 1
            self.responses[request.attempt_id] = self.reviser.invoke(request)
        else:
            super().record(document)
        try:
            step = self.steps[request.attempt_id]
            results = _tool_results(document)
            if request.command in _INNER:
                chain, iteration = _chain_iteration(request)
                identity = self._identity(request)
                text = json.dumps(document["messages"])
                for peer, nonce in self.chain_nonces.items():
                    if peer != chain:
                        assert nonce not in text, "another chain's actual tool context leaked"
                if request.schema_version >= 24 and step == 1:
                    assert not any(block.get("type") == "tool_use"
                        for message in document["messages"] for block in message.get("content", ()) if isinstance(block, dict))
                elif request.schema_version < 24 and (request.command is not AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT or step > 1 or iteration > 1):
                    assert self.chain_nonces[chain] in text, "own previous native tool context was lost"
                if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
                    self.designs[identity] = self.responses[request.attempt_id].typed_candidate
                    if step == 5:
                        self.local_tests.add(identity)
                elif request.command is AgentCommand.EXPERIMENT_EXECUTION:
                    if step == 2:
                        result = results["batch_submit"]
                        assert not result.get("is_error"), result
                        self.experiments[identity] = json.loads(_result_text(result))["exp_id"]
                    if step >= 3:
                        wait_id = f"batch_wait_{step - 1}_call"
                        result = results[wait_id]
                        assert not result.get("is_error"), result
                        waited = json.loads(_result_text(result))
                        assert waited["exp_id"] == self.experiments[identity], waited
                        if waited["terminal"]:
                            assert waited["outcome"]["outcome"] == self._expected_outcome(request), waited
                            self.execution_results[identity] = waited
            if request.command is AgentCommand.SELF_EVOLVE:
                previous = {2: "self_evolve_skill", 3: "read_plugin_update",
                            4: "edit_plugin_update", 5: "plugin_update_protection"}.get(step)
                if previous:
                    result = results[previous]
                    assert not result.get("is_error"), result
                    self.evolve_evidence[previous] = _result_text(result)
                if step == 2:
                    assert "Only a SELF_EVOLVE request grants" in json.dumps(document)
                if step == 5:
                    assert "PLUGIN_UPDATE_ONLY" in self.evolve_evidence["plugin_update_protection"]
        except Exception as exc:
            self.errors.append(f"batch provider {len(self.requests)}: {type(exc).__name__}: {exc}")
        self.requests[-1] = actual_document
        return len(self.requests)

    def tool_calls(self, number):
        calls = self._unscoped_tool_calls(number)
        if self.current is None:
            return calls
        prefix = "probe_" + self.current.attempt_id[-16:] + "__"
        scoped = tuple((identity if identity.startswith("structured_") else prefix + identity,
                        name, arguments) for identity, name, arguments in calls)
        identities = [identity for identity, _, _ in scoped]
        assert len(identities) == len(set(identities)) and not self.issued_tool_ids.intersection(identities)
        self.issued_tool_ids.update(identities)
        return scoped

    def _unscoped_tool_calls(self, number):
        request = self.current
        if request is None or self.errors:
            return ()
        step = self.steps[request.attempt_id]
        if request.command is AgentCommand.EXPERIMENT_EXECUTION:
            identity = self._identity(request)
            if step == 1:
                return (("batch_submit", "mcp__host-tools__submit_experiment",
                         {"candidate": self.designs[identity]}),)
            if identity not in self.execution_results:
                # CC 2.1.268 mispaired batch_wait_3 with the earlier batch_wait.
                # End each fixture ID so no complete ID prefixes another one.
                wait_id = f"batch_wait_{step}_call"
                return ((wait_id, "mcp__host-tools__wait_experiment",
                         {"exp_id": self.experiments[identity], "max_wait_seconds": 10}),)
            return self._structured(request)
        if request.command is AgentCommand.SELF_EVOLVE:
            if step == 1:
                return (("self_evolve_skill", "Skill",
                         {"skill": _resources(request)["plugin"]["name"] + ":self-evolve"}),)
            if step == 2:
                return (("read_plugin_update", "Read",
                         {"file_path": "/plugin-update/skills/self-evolve/SKILL.md"}),)
            if step == 3:
                return (("edit_plugin_update", "Edit", {
                    "file_path": "/plugin-update/skills/self-evolve/SKILL.md",
                    "old_string": _EVOLVE_LINE,
                    "new_string": "Before preregistration, rerun the repaired local test. Lesson: "
                        + self.evolve_nonce + ".\n\n" + _EVOLVE_LINE}),)
            if step == 4:
                script = (
                    "import errno, os\nfrom pathlib import Path\n"
                    "assert os.statvfs('/workspace').f_flag & os.ST_RDONLY\n"
                    f"assert {self.evolve_nonce!r} in Path('/plugin-update/skills/self-evolve/SKILL.md').read_text()\n"
                    f"assert {self.evolve_nonce!r} not in Path('/expert-plugin/skills/self-evolve/SKILL.md').read_text()\n"
                    "for path in ['/plugin-update/.git', '/expert-plugin/skills/self-evolve/SKILL.md']:\n"
                    "    try:\n        fd = os.open(path, os.O_WRONLY | os.O_APPEND)\n"
                    "    except OSError as error:\n        assert error.errno == errno.EROFS, (path, error)\n"
                    "    else:\n        os.close(fd)\n        raise AssertionError('protected plugin path writable')\n"
                    "print('PLUGIN_UPDATE_ONLY')\n"
                )
                return (("plugin_update_protection", "Bash",
                         {"command": "python3 - <<'PY'\n" + script + "PY", "timeout": 10000}),)
            return self._structured(request)
        calls = super().tool_calls(number)
        if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT and step == 1:
            chain, iteration = _chain_iteration(request)
            identity, name, arguments = calls[0]
            calls = ((identity, name, {**arguments, "content": arguments["content"]
                     + f"\n# Scoped tool context: {self.chain_nonces[chain]}; iteration {iteration}\n"}),)
        return calls


def _payload(tmp_path: Path, run_id: str):
    # Runner reserves /tmp for its own scratch; retain this fresh admitted
    # target under /var/tmp, as the other real Process rehearsals do.
    mount = Path(tempfile.mkdtemp(prefix="hypoloom-native-batch-target-", dir="/var/tmp"))
    (tmp_path / "admitted-target.txt").write_text(str(mount) + "\n")
    payload = _pilot_payload(tmp_path, mount, run_id)
    contract = payload["research_contract"]
    settings = project_profile_v4(**documents()).settings
    settings["experts"]["defaults"].update(model=_MODEL, effort="high",
        max_hypotheses_per_round=2, max_forks=4)
    expert = settings["experts"]["experts"][0]
    expert.update(name=contract["roles"][0]["id"], stage_overrides={})
    settings["goal"]["completion_mode"] = "explicit_signals"
    settings["goal"]["stop_conditions"]["max_rounds_per_expert"] = 1
    settings["methodology"]["inner_loop"] = {"max_iterations_per_hypothesis": 2, "repair_attempts": 1}
    settings["methodology"]["interaction"] = {"leader": {"enabled": False}, "debate": {"enabled": False}}
    settings["methodology"]["graph_evolution"] = {"enabled": False}
    settings["notify"] = {"sinks": {}, "events": {}}
    settings["safety"]["filesystem"]["protected_paths"] = [".git/"]
    contract["methodology"]["controls"]["product"] = settings
    contract["budget"]["max_experiments"] = 6
    contract["roles"][0]["budget"]["max_experiments"] = 6
    return payload


def test_real_cli_two_chains_scientific_iteration_adjudication_and_plugin_commit(tmp_path, monkeypatch):
    from test_claude_final_report_g3 import _FinalReportHostScenario

    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 14)
    executable = _installed_claude()
    # Reuse actual report tools, retaining this test's two-chain experiment generator.
    scenario = _FinalReportHostScenario(tmp_path)
    scenario.reviser = _PruningBatchAgent()
    host_key = "host-only-batch-g3-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, host_key)
    settings = tmp_path / "settings.json"
    settings.write_text("{}\n")
    settings.chmod(0o600)
    host = _new_host(tmp_path, executable, f"http://127.0.0.1:{provider.server_port}", host_key)
    host.grounding_web = _NoPublicWeb()
    run_id = "native-multichain-g3"
    try:
        payload = _payload(tmp_path, run_id)
        host.initialize()
        created = host.handle(ControlRequest(schema_version=1, request_id="create-native-batch",
            expected_revision=0, command="CREATE_RUN", payload=payload))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            host.tick()
            assert not scenario.errors, scenario.errors
            if host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(0.01)
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED", host.store.list_attempts()
        expected = {(chain, iteration) for chain in ("chain-a", "chain-b") for iteration in (1, 2)}
        assert scenario.local_tests == set(scenario.execution_results) == expected
        assert len(set(scenario.experiments.values())) == 4
        hypotheses = host.store.list_facts(run_id, "HypothesisProposed")
        outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        assert len(hypotheses) == 2 and len(outcomes) == 4
        assert {row["payload"]["outcome"] for row in outcomes} == {"SUCCESS"}
        creations = [json.loads(row["payload_json"]) for row in host.store.inspect(
            "SELECT payload_json FROM workflow_transitions WHERE run_id=? AND workflow_kind='EXPERIMENT' AND revision=1", (run_id,))]
        assert {(row["chain"], row["chain_iteration"]) for row in creations} == expected
        for row in creations:
            if row["chain_iteration"] == 2:
                assert row["revision_of_outcome_id"] is None and row["analysis_attempt_id"]
                prior = host.store.find_fact(run_id, "ExperimentPreregistered", row["iteration_of_experiment_id"])
                assert prior["hypothesis_id"] == row["hypothesis_id"]
        attempts = host.store.list_attempts()
        assert all(row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED" for row in attempts)
        assert len([row for row in attempts if row["kind"] == "PROCESS"]) == 4
        observations = [json.loads(row["observation_json"]) for row in attempts if row["kind"] == "AGENT"]
        synthesis = [row for row in observations if row["request"]["command"] == "HYPOTHESIS_PROPOSAL"
                     and row["request"]["task_context"]["key"]["subkind"] == "SYNTHESIS"]
        assert len(synthesis) == 1
        chain_sessions = {}
        for chain in ("chain-a", "chain-b"):
            nodes = [row for row in observations if row["request"]["command"] in {item.value for item in _INNER}
                     and row["request"]["task_context"]["key"]["chain"] == chain]
            assert len(nodes) == 6
            assert {row["session"]["parent_session_id"] for row in nodes} == {synthesis[0]["session"]["session_id"]}
            chain_sessions[chain] = nodes[0]["session"]["session_id"]
            assert {row["session"]["session_id"] for row in nodes} == {chain_sessions[chain]}
            assert "--fork-session" in nodes[0]["session"]["launch_arguments"]
            assert all(row["session"]["launch_arguments"] == ["--resume", chain_sessions[chain]] for row in nodes[1:])
        assert len(set(chain_sessions.values())) == 2
        adjudications = [row for row in observations if row["request"]["command"] == "ADJUDICATE"]
        assert len(adjudications) == 1
        history = host._batch_history(run_id, str(hypotheses[0]["payload"]["component_id"]))
        assert len(history.batches) == 1 and history.selected_tip["chain_id"] == "chain-a"
        evolved = [row for row in observations if row["request"]["command"] == "SELF_EVOLVE"]
        assert len(evolved) == 1 and len(scenario.evolve_evidence) == 4
        update = evolved[0]["plugin_update"]
        assert update["base_commit"] != update["commit"]
        assert {row["session"]["expert_resources"]["plugin"]["commit"] for row in observations
                if "expert_resources" in row["session"]} == {update["base_commit"]}
        plugin_store = ExpertPluginStore(host.state_root)
        role = evolved[0]["request"]["role"]
        old = plugin_store.read(run_id, role, commit=update["base_commit"],
            attempt_id="inspect-old", max_bytes=1024 * 1024)
        new = plugin_store.read(run_id, role, commit=update["commit"],
            attempt_id="inspect-new", max_bytes=1024 * 1024)
        assert scenario.evolve_nonce not in (old.root / "skills/self-evolve/SKILL.md").read_text()
        assert scenario.evolve_nonce in (new.root / "skills/self-evolve/SKILL.md").read_text()
        product = build_product_observation(host.state_root, run_id)
        from hypoloom.monitor import build_monitor_snapshot
        observed = build_monitor_snapshot(host.state_root, run_id)
        activity = observed.components[0].revision_activity
        assert (activity["initial_experiments"], activity["scientific_iterations"], activity["operational_repairs"]) == (2, 2, 0)
        assert len(activity["scopes"]) == 4
        assert observed.components[0].revision_warning is None
        (tmp_path / "product-observation.json").write_text(json.dumps(product, indent=2))
        nodes = {item["chain"]: item for item in product["graph"]["nodes"]}
        assert nodes["chain-a"]["selected_tip"] is True
        assert nodes["chain-b"]["state"] == "PRUNED"
        assert nodes["chain-b"]["assessment"] == "SUPPORTED"
        pruning = [edge for edge in product["graph"]["edges"] if edge["relation"] == "pruned_by"]
        assert len(pruning) == 1 and pruning[0]["source"] == nodes["chain-a"]["hypothesis_id"]
        assert pruning[0]["target"] == nodes["chain-b"]["hypothesis_id"]
        assert len([event for event in product["events"] if event["type"] == "expert_evolved"]) == 1
        for observation in observations:
            session = observation["session"]
            assert session["transcript_complete"] is True
            archive = host.publisher.root / session["transcript"]["path"]
            assert host_key.encode() not in (archive / "transcript.jsonl").read_bytes()
            if observation["request"]["command"] == "CYCLE_REPORT":
                assert scenario.nonce in (archive / "memory/MEMORY.md").read_text()
                assert scenario.nonce in (archive / "memory/rounds/1.md").read_text()
        assert not host.running and not host.running_agents
        assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"] is not None)
        audit = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
        (tmp_path / "oracle.json").write_text(json.dumps(audit.to_dict(), indent=2))
        assert audit.ok, audit.to_dict()
        assert audit.canonical_outcome_count == 4 and audit.artifact_counts["TRANSCRIPT"] == len(observations)
        assert audit.open_attempt_count == audit.pending_effect_count == 0
    finally:
        (tmp_path / "provider-requests.json").write_text(json.dumps(scenario.requests, indent=2))
        (tmp_path / "provider-errors.json").write_text(json.dumps(scenario.errors, indent=2))
        (tmp_path / "native-tool-evidence.json").write_text(json.dumps({
            "chain_nonces": scenario.chain_nonces, "self_evolve": scenario.evolve_evidence,
            "completed_experiments": {f"{chain}/{iteration}": value for (chain, iteration), value in scenario.execution_results.items()}}, indent=2))
        host.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
