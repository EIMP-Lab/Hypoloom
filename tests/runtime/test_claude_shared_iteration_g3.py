"""Mixed local/common phase results continue through an actual native chain."""

from __future__ import annotations

import copy
import json
import secrets
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from hypoloom.agent_aci import WorkspaceArtifact, with_output_digest
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.oracle import Oracle

from test_claude_file_tool_cli import _installed_claude
from test_claude_measurement_g3 import _NoPublicWeb
from test_claude_shared_acceptance_g3 import (
    _AcceptanceChoices, _AcceptanceScenario, _DECLARATION, _GOAL, _RUN,
    _common_source, _experiment_files, _fixture_pairs, _payload,
)
from test_full_cc_host_g3 import _new_host, _request_from_provider, _start_provider


_RAW_OMISSION = "del rows['suite_two']['predictions']\n"


def _iteration_files(iteration):
    files = _experiment_files(_GOAL)
    if iteration == 1:
        files["experiment.py"] = files["experiment.py"].replace(
            b"Path('predictions.json')", _RAW_OMISSION.encode() + b"Path('predictions.json')")
    return files


class _IterationChoices(_AcceptanceChoices):
    def invoke(self, request):
        response = super().invoke(request)
        if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            response = replace(response, proposed_workspace_artifacts=tuple(
                WorkspaceArtifact.from_bytes(name, data)
                for name, data in _iteration_files(request.task_context.key.experiment_ordinal).items()))
        elif request.command is AgentCommand.RESULT_ANALYSIS:
            candidate = copy.deepcopy(response.typed_candidate)
            candidate["iteration_disposition"] = "ITERATE" if request.task_context.key.experiment_ordinal == 1 else "COMPLETE"
            response = replace(response, typed_candidate=candidate)
        return with_output_digest(response)


class _IterationScenario(_AcceptanceScenario):
    def __init__(self, root, nonce):
        super().__init__(root, nonce)
        self.reviser = _IterationChoices()
        self.waited_iterations = {}
        self.analysis_inputs = {}

    def record(self, document):
        request = _request_from_provider(document)
        if request.command is AgentCommand.EXPERIMENT_EXECUTION and request.attempt_id not in self.agent_requests:
            self.waited.pop(request.role, None)
        # The actual session contains prior iterations. Keep the fixture's tool
        # IDs invocation-scoped, while its parent reads the same payloads by name.
        observed = copy.deepcopy(document)
        prefix = request.attempt_id + "__"
        for message in observed.get("messages", []) if request.command is AgentCommand.EXPERIMENT_EXECUTION else []:
            for block in message.get("content", []) if isinstance(message.get("content"), list) else []:
                if block.get("type") == "tool_result" and block.get("tool_use_id", "").startswith(prefix):
                    block["tool_use_id"] = block["tool_use_id"][len(prefix):]
        number = super().record(observed)
        try:
            if request.command is AgentCommand.EXPERIMENT_EXECUTION and request.role in self.waited:
                self.waited_iterations[request.task_context.key.experiment_ordinal] = copy.deepcopy(self.waited[request.role])
            if request.command is AgentCommand.RESULT_ANALYSIS:
                iteration = request.task_context.key.experiment_ordinal
                values = {name: json.loads(next(item.content_bytes() for item in request.input_artifacts
                    if item.ref == request.command_id + "--current-" + name)) for name in ("outcome", "evaluation", "process")}
                files = json.loads(next(item.content_bytes() for item in request.input_artifacts
                    if item.ref == request.command_id + "--current-result-files"))
                assert "raw/predictions.json" in files["available"]
                common = [item for item in request.input_artifacts if item.ref.endswith("--current-goal-result.json")]
                if iteration == 1:
                    assert values["outcome"]["payload"]["outcome"] == "SUCCESS"
                    assert values["outcome"]["payload"].get("goal_evaluation") is None
                    assert "goal parser did not complete" in values["evaluation"]["parser_validation_error"]
                    assert values["process"]["returncode"] == 72
                    assert "goal/result.json" in files["missing"] and not common
                else:
                    assert "goal/result.json" in files["available"] and len(common) == 1
                    assert set(json.loads(common[0].content_bytes())) == {"measurements", "checks"}
                self.analysis_inputs[iteration] = values
        except Exception as exc:
            self.errors.append(f"iteration provider: {type(exc).__name__}: {exc}")
        return number

    def tool_calls(self, number):
        request = self.current
        if request is not None and not self.errors and request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            iteration = request.task_context.key.experiment_ordinal
            step = self.steps[request.attempt_id]
            if step == 3:
                if iteration == 1:
                    return (("expert_raw", "Write", {"file_path": "/workspace/experiment.py",
                        "content": _iteration_files(iteration)["experiment.py"].decode()}),)
                return (("expert_raw", "Edit", {"file_path": "/workspace/experiment.py",
                    "old_string": _RAW_OMISSION, "new_string": ""}),)
            if step == 5 and iteration == 1:
                script = """set -e
mkdir -p /tmp/shared-local-check /workspace/smoke
(cd /tmp/shared-local-check && python3 /workspace/experiment.py)
cp /tmp/shared-local-check/predictions.json /workspace/smoke/predictions.json
HYPOLOOM_MEASUREMENT_RAW_ROOT=/tmp/shared-local-check HYPOLOOM_MEASUREMENT_RESULT_PATH=/tmp/shared-local-check/local.json python3 /workspace/parse_local.py
python3 - <<'PY'
import json, os, subprocess
from pathlib import Path
assert json.loads(Path('/tmp/shared-local-check/local.json').read_text())['value'] == '1'
parsed = subprocess.run(['python3', '/acceptance/evaluate.py'], env={**os.environ,
    'HYPOLOOM_MEASUREMENT_RAW_ROOT': '/tmp/shared-local-check',
    'HYPOLOOM_MEASUREMENT_RESULT_PATH': '/tmp/shared-local-check/goal.json'}, capture_output=True, text=True)
assert parsed.returncode != 0 and 'KeyError' in parsed.stderr and 'predictions' in parsed.stderr
assert not Path('/tmp/shared-local-check/goal.json').exists()
print('EXPERT_RAW_AND_BOTH_PARSERS_CHECKED: local passed; common raw input is incomplete')
PY
"""
                return (("expert_local_check", "Bash", {"command": script, "timeout": 10000}),)
        calls = super().tool_calls(number)
        if request is not None and request.command is AgentCommand.EXPERIMENT_EXECUTION:
            return tuple((request.attempt_id + "__" + identity, tool, arguments) for identity, tool, arguments in calls)
        return calls


def test_native_local_success_common_raw_failure_iterates_in_same_chain_session(tmp_path):
    scenario = _IterationScenario(tmp_path, "shared-iteration-" + secrets.token_hex(12))
    key = "local-iteration-provider-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    (tmp_path / "settings.json").write_text("{}\n")
    (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(tmp_path, _installed_claude(), f"http://127.0.0.1:{provider.server_port}", key)
    host.grounding_web = _NoPublicWeb()
    first_record = None
    try:
        with tempfile.TemporaryDirectory(prefix="prom-common-iteration-data-", dir="/var/tmp") as mount:
            data = _fixture_pairs()
            data["candidates"][_GOAL]["diagnostic"] = data["labels"]
            payload = _payload(tmp_path, Path(mount), roles=(_GOAL,), iterations=2, fixture_data=data)
            original = copy.deepcopy(payload)
            host.initialize()
            created = host.handle(ControlRequest(1, "create-native-iteration", 0, "CREATE_RUN", payload))
            assert created.status == "accepted", created
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                host.tick()
                assert not scenario.errors, scenario.errors
                outcomes = host.store.list_facts(_RUN, "CanonicalOutcomeAccepted")
                head = host.store.latest_transition(_RUN)
                if len(outcomes) == 1 and first_record is None:
                    first_record = copy.deepcopy(outcomes[0])
                    assert first_record["payload"]["outcome"] == "SUCCESS"
                    assert first_record["payload"]["metric_value"] == "1"
                    assert first_record["payload"].get("goal_evaluation") is None
                    assert head["state"] == "ACTIVE"
                if head["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
                time.sleep(0.01)
            assert host.store.latest_transition(_RUN)["state"] == "COMPLETED", host.store.list_attempts()
            assert payload == original and first_record is not None
            outcomes = host.store.list_facts(_RUN, "CanonicalOutcomeAccepted")
            assert len(outcomes) == 2 and outcomes[0] == first_record
            assert outcomes[1]["payload"]["outcome"] == "SUCCESS"
            assert outcomes[1]["payload"]["goal_evaluation"] == {"source_attempt_id": scenario.source_attempt_id,
                "measurements": {"suite_one_accuracy": "0.75", "suite_two_accuracy": "0.75"},
                "checks": {"complete": True, "single_candidate": True}}
            specs = host.store.list_facts(_RUN, "ExperimentPreregistered")
            assert len(specs) == 2 and len({row["hypothesis_id"] for row in specs}) == 1
            assert all(row["payload"]["goal_evaluation"] == {"scope": "GOAL", "source_attempt_id": scenario.source_attempt_id,
                "acceptance": _DECLARATION} for row in specs)
            transitions = [json.loads(row["payload_json"]) for row in host.store.inspect(
                "SELECT payload_json FROM workflow_transitions WHERE run_id=? AND workflow_kind='EXPERIMENT' AND revision=1 ORDER BY rowid", (_RUN,))]
            assert [row["chain_iteration"] for row in transitions] == [1, 2]
            assert all(row["revision_of_outcome_id"] is None for row in transitions)
            assert transitions[1]["iteration_of_experiment_id"] == specs[0]["experiment_id"]
            attempts = host.store.list_attempts()
            assert all(row["state"] == "CLOSED" for row in attempts)
            processes = sorted((row for row in attempts if row["kind"] == "PROCESS"), key=lambda row: row["started_at"])
            assert len(processes) == 2
            assert [json.loads(row["observation_json"])["returncode"] for row in processes] == [72, 0]
            agents = [json.loads(row["observation_json"]) for row in attempts if row["kind"] == "AGENT"]
            inner = [row for row in agents if row["request"]["command"] in {
                "EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION", "RESULT_ANALYSIS"}]
            assert len(inner) == 6 and len({row["request"]["attempt_id"] for row in inner}) == 6
            assert len({row["session"]["session_id"] for row in inner}) == len(inner)
            assert len({row["session"]["branch"] for row in inner}) == 1
            assert len({row["request"]["task_context"]["key"]["chain"] for row in inner}) == 1
            analysis = next(row for row in inner if row["request"]["command"] == "RESULT_ANALYSIS"
                and row["request"]["task_context"]["key"]["experiment_ordinal"] == 1)
            assert analysis["response"]["typed_candidate"]["iteration_disposition"] == "ITERATE"
            assert transitions[1]["analysis_attempt_id"] == analysis["request"]["attempt_id"]
            assert set(scenario.waited_iterations) == set(scenario.analysis_inputs) == {1, 2}
            assert scenario.waited_iterations[1]["outcome"]["outcome"] == "SUCCESS"
            assert scenario.waited_iterations[1]["outcome"].get("goal_evaluation") is None
            ground = next(row for row in agents if row["request"]["command"] == "GROUNDING")
            source = host.publisher.root / ground["session"]["transcript"]["path"] / "evaluator/evaluate.py"
            assert source.read_text() == _common_source(scenario.nonce)
            verdicts = host.store.list_facts(_RUN, "GoalVerdictRecorded")
            assert len(verdicts) == 1 and verdicts[0]["payload"]["verdict"] == "GOAL_MET"
            assert len(host.store.list_facts(_RUN, "EvidenceLinked")) == 2
            report = Oracle(host.store.database_path, host.publisher.root).audit(_RUN)
            assert report.ok, report.to_dict()
            assert not host.running and not host.running_agents
            assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"])
            (tmp_path / "shared-iteration-evidence.json").write_text(json.dumps({
                "outcomes": outcomes, "preregistrations": specs, "creation_transitions": transitions,
                "session_id": inner[0]["session"]["session_id"], "inner_attempt_ids": [row["request"]["attempt_id"] for row in inner],
                "waited_iterations": scenario.waited_iterations, "analysis_inputs": scenario.analysis_inputs,
                "process_attempt_ids": [row["attempt_id"] for row in processes], "oracle": report.to_dict()}, indent=2))
    finally:
        host.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
