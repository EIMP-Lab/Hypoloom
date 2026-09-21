"""Native Grounding evaluator repair, shared read-only source and two CPU outcomes.

Only model choices and literature are fixtures. Actual Claude/bwrap tools author
and test the common parser; production Host/Runner/Policy/Oracle own acceptance.
"""

from __future__ import annotations

import copy
import json
import secrets
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import yaml

from hypoloom.agent_aci import CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION, WorkspaceArtifact, with_output_digest
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand, MetricDirection, MetricSpec
from hypoloom.expert_loop import ResearchNodeSubkind, ResearchNodeTask
from hypoloom.measurement_contract import MeasurementComparison, ScientificMeasurement
from hypoloom.oracle import Oracle
from hypoloom.profile_admission import admit_profile

from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results
from test_claude_measurement_g3 import _DeclaredScenario, _NoPublicWeb
from test_deferred_measurement_host import DeclaredMeasurementAgent, _candidate_bodies
from test_full_cc_host_g3 import _MODEL, _new_host, _request_from_provider, _start_provider
from test_profile_admission import _materialize_expert_loop_pilot_v3
from test_profile_v4 import documents


_RUN = "native-common-acceptance-g3"
_LOCAL = "first-researcher"
_GOAL = "second-researcher"
_BROKEN = "SHARED_EVALUATOR_DENOMINATOR_FAILURE"
_REPAIRED = "SHARED_EVALUATOR_SELF_TEST_PASSED"
_PROTECTED = "ACCEPTED_EVALUATOR_READONLY"
_DECLARATION = {
    "summary": "One candidate must exceed one-half accuracy on each complete suite.",
    "rule": "ALL",
    "criteria": [{"name": name, "operator": "GT", "target": "0.5"}
                 for name in ("suite_one_accuracy", "suite_two_accuracy")],
    "validity_checks": ["complete", "single_candidate"],
    "parser_argv": ["python", "/input/evaluate.py"],
    "parser_files": ["evaluate.py"],
    "raw_artifacts": ["predictions.json"],
}


def _local_measurement():
    return ScientificMeasurement(
        metric=MetricSpec(name="diagnostic_accuracy", direction=MetricDirection.MAXIMIZE, target="0.9"),
        comparison=MeasurementComparison.VALUE,
        parse_rule="Calculate accuracy on the separately declared local diagnostic pairs.",
        parser_argv=("python", "/input/parse_local.py"), parser_files=("parse_local.py",),
        raw_artifacts=("predictions.json",),
    )


def _common_source(nonce: str, *, broken: bool = False) -> str:
    source = f'''import json, os, sys
from decimal import Decimal
from pathlib import Path
# Actual Grounding-authored source: {nonce}

def accuracy(rows):
    assert rows['labels'] and len(rows['labels']) == len(rows['predictions'])
    numerator = sum(left == right for left, right in zip(rows['labels'], rows['predictions']))
    denominator = len(rows['labels'])
    return str(Decimal(numerator) / Decimal(denominator))

def evaluate(raw):
    suites = raw['suites']
    names = ('suite_one', 'suite_two')
    return {{'measurements': {{name + '_accuracy': accuracy(suites[name]) for name in names}},
            'checks': {{'complete': set(suites) == set(names) and all(len(suites[name]['labels']) == 4 for name in names),
                       'single_candidate': len({{suites[name]['candidate'] for name in names}}) == 1}}}}

if '--self-test' in sys.argv:
    assert accuracy({{'labels': [0, 1], 'predictions': [0, 1]}}) == '1', {_BROKEN!r}
    assert accuracy({{'labels': [0, 1], 'predictions': [0, 0]}}) == '0.5'
    print({_REPAIRED!r})
else:
    raw_root = Path(os.environ.get('HYPOLOOM_MEASUREMENT_RAW_ROOT', '/raw'))
    result_path = Path(os.environ.get('HYPOLOOM_MEASUREMENT_RESULT_PATH', '/parsed/result.json'))
    result_path.write_text(json.dumps(evaluate(json.loads((raw_root / 'predictions.json').read_text()))))
'''
    return source.replace("denominator = len(rows['labels'])", "denominator = len(rows['labels']) + 1") if broken else source


def _fixture_pairs():
    labels = [0, 1, 0, 1]
    return {"labels": labels, "candidates": {
        _LOCAL: {"suite_one": labels, "suite_two": [0, 0, 0, 0], "diagnostic": labels},
        _GOAL: {"suite_one": [0, 1, 0, 0], "suite_two": [0, 1, 0, 0], "diagnostic": [1, 0, 1, 0]},
    }}


def _experiment_files(role: str):
    raw = f'''import json, os
from pathlib import Path
data_root = Path(json.loads(os.environ['HYPOLOOM_READ_ROOTS'])[0])
data = json.loads((data_root / 'validation.json').read_text())
candidate = data['candidates'][{role!r}]
rows = {{name: {{'candidate': {role!r}, 'labels': data['labels'], 'predictions': candidate[name]}}
        for name in ('suite_one', 'suite_two')}}
Path('predictions.json').write_text(json.dumps({{'suites': rows,
    'diagnostic': {{'labels': data['labels'], 'predictions': candidate['diagnostic']}}}}))
print('RAW_PREDICTIONS_COMPUTED {role}')
'''
    local = '''import json, os
from decimal import Decimal
from pathlib import Path
raw_root = Path(os.environ.get('HYPOLOOM_MEASUREMENT_RAW_ROOT', '/raw'))
result_path = Path(os.environ.get('HYPOLOOM_MEASUREMENT_RESULT_PATH', '/parsed/result.json'))
rows = json.loads((raw_root / 'predictions.json').read_text())['diagnostic']
assert rows['labels'] and len(rows['labels']) == len(rows['predictions'])
value = Decimal(sum(left == right for left, right in zip(rows['labels'], rows['predictions']))) / Decimal(len(rows['labels']))
result_path.write_text(json.dumps({'metric': 'diagnostic_accuracy', 'value': str(value)}))
'''
    return {"experiment.py": raw.encode(), "parse_local.py": local.encode(), "implementation.patch": b""}


class _AcceptanceChoices(DeclaredMeasurementAgent):
    def invoke(self, request):
        response = super().invoke(request)
        candidate = copy.deepcopy(response.typed_candidate)
        if (request.command is AgentCommand.HYPOTHESIS_PROPOSAL
                and isinstance(request.task_context, ResearchNodeTask)
                and request.task_context.key.subkind is ResearchNodeSubkind.SYNTHESIS):
            for body in _candidate_bodies(request, candidate):
                body["measurement"] = _local_measurement().to_dict()
                body["method_family_id"] = "candidate-" + request.role
        elif request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            declaration = _local_measurement().to_dict()
            candidate.update(method="Measure both suites from one candidate, plus a separate local diagnostic.",
                metrics=[declaration["metric"]], measurement=declaration,
                execution_argv=["python", "/input/experiment.py"], evaluation_scope="GOAL",
                acceptance_criteria=["Local diagnostic accuracy >= 0.9; whole-goal criteria remain independent."],
                resource_preference="CPU")
            response = replace(response, proposed_workspace_artifacts=tuple(
                WorkspaceArtifact.from_bytes(path, content) for path, content in _experiment_files(request.role).items()))
        return with_output_digest(replace(response, typed_candidate=candidate))


class _AcceptanceScenario(_DeclaredScenario):
    def __init__(self, root: Path, nonce: str):
        super().__init__(root, nonce)
        self.reviser = _AcceptanceChoices()
        self.designs = {}
        self.experiments = {}
        self.waited = {}
        self.source_attempt_id = None
        self.shared_inputs = {}
        self.source_reads = set()
        self.source_readonly = set()
        self.grounding_evidence = {}
        self.local_stop_repair = False

    def record(self, document):
        request = _request_from_provider(document)
        if request.command in {AgentCommand.CYCLE_REPORT, AgentCommand.FINAL_REPORT}:
            return super().record(document)
        self.requests.append(document)
        self.current = request
        self.agent_requests[request.attempt_id] = request
        self.steps[request.attempt_id] += 1
        try:
            assert request.schema_version in {16, 17, CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION}
            if request.attempt_id not in self.responses:
                self.responses[request.attempt_id] = self.reviser.invoke(request)
            step = self.steps[request.attempt_id]
            results = _tool_results(document)
            if request.command is AgentCommand.GROUNDING:
                self.source_attempt_id = request.attempt_id
                previous = {2: "ground_target", 3: "ground_write", 4: "ground_failure",
                            5: "ground_repair", 6: "ground_success", 7: "ground_readonly"}.get(step)
                if previous:
                    result = results[previous]
                    text = _result_text(result)
                    self.grounding_evidence[previous] = text
                    if previous == "ground_failure":
                        assert result.get("is_error") and _BROKEN in text, result
                    else:
                        assert not result.get("is_error"), result
                    if previous == "ground_target":
                        assert "# profile-v3 target" in text
                    if previous == "ground_success":
                        assert _REPAIRED in text
                    if previous == "ground_readonly":
                        assert "GROUNDING_TARGET_DATA_READONLY" in text
                response = self.responses[request.attempt_id]
                self.responses[request.attempt_id] = with_output_digest(replace(response, typed_candidate={
                    "task_kind": "GROUNDING", "supporting_refs": list(request.immutable_context_refs),
                    "background": {"project_summary": "Observed actual project and tested common evaluator. " + self.nonce,
                        "codebase_map": [{"path": "train.py", "purpose": "Observed example project source"}],
                        "entrypoints": ["train.py"], "how_to_run_experiment": "Produce both suites from one candidate and run the accepted parser.",
                        "metrics_available": [], "risks": [], "open_questions": []},
                    "web_sources": [], "clarification": None, "acceptance": copy.deepcopy(_DECLARATION)}))
                if step == 7:
                    # The local parser test passed, but the separately authored
                    # declaration has the reproduced metric name mismatch. The native
                    # Stop hook must reject it before experts inherit the source.
                    self.responses[request.attempt_id].typed_candidate["acceptance"]["criteria"][0]["name"] += "_exceeds_target"
            elif request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
                embedded = [item for item in request.input_artifacts if item.ref.endswith("--shared-acceptance")]
                assert len(embedded) == 1
                source = json.loads(embedded[0].content_bytes())
                assert set(source) == {"source_attempt_id", "acceptance", "files"}
                assert source["source_attempt_id"] == self.source_attempt_id
                assert source["acceptance"] == _DECLARATION
                files = {item["path"]: WorkspaceArtifact.from_dict(item).content_bytes() for item in source["files"]}
                assert files == {"evaluate.py": _common_source(self.nonce).encode()}
                self.shared_inputs[request.role] = source
                self.designs[request.role] = self.responses[request.attempt_id].typed_candidate
                previous = {2: "expert_read", 3: "expert_readonly", 4: "expert_raw",
                            5: "expert_parser", 6: "expert_local_check"}.get(step)
                if previous:
                    result = results[previous]
                    assert not result.get("is_error"), result
                    text = _result_text(result)
                    if previous == "expert_read":
                        assert self.nonce in text and "denominator = len(rows['labels'])" in text
                        assert "denominator = len(rows['labels']) + 1" not in text
                        self.source_reads.add(request.role)
                    elif previous == "expert_readonly":
                        assert _PROTECTED in text
                        self.source_readonly.add(request.role)
                    elif previous == "expert_local_check":
                        assert "EXPERT_RAW_AND_BOTH_PARSERS_CHECKED" in text
            elif request.command is AgentCommand.EXPERIMENT_EXECUTION:
                if step == 2:
                    result = results["shared_submit"]
                    assert not result.get("is_error"), result
                    self.experiments[request.role] = json.loads(_result_text(result))["exp_id"]
                elif step > 2:
                    result = results[f"shared_wait_{step - 1}"]
                    assert not result.get("is_error"), result
                    waited = json.loads(_result_text(result))
                    if waited["terminal"]:
                        self.waited[request.role] = waited
        except Exception as exc:
            self.errors.append(f"shared provider {len(self.requests)}: {type(exc).__name__}: {exc}")
        return len(self.requests)

    def tool_calls(self, number):
        request = self.current
        if request is None or self.errors:
            return ()
        if request.command in {AgentCommand.CYCLE_REPORT, AgentCommand.FINAL_REPORT}:
            return super().tool_calls(number)
        step = self.steps[request.attempt_id]
        if request.command is AgentCommand.GROUNDING:
            if step == 1:
                return (("ground_target", "Read", {"file_path": "/workspace/train.py"}),)
            if step == 2:
                return (("ground_write", "Write", {"file_path": "/acceptance/evaluate.py", "content": _common_source(self.nonce, broken=True)}),)
            if step in {3, 5}:
                return (("ground_failure" if step == 3 else "ground_success", "Bash", {
                    "command": "python3 /acceptance/evaluate.py --self-test", "timeout": 10000}),)
            if step == 4:
                return (("ground_repair", "Edit", {"file_path": "/acceptance/evaluate.py",
                    "old_string": "denominator = len(rows['labels']) + 1", "new_string": "denominator = len(rows['labels'])"}),)
            if step == 6:
                script = """import errno, json, os
from pathlib import Path
targets = [Path('/workspace/train.py'), Path(json.loads(os.environ['HYPOLOOM_READ_ROOTS'])[0]) / 'validation.json']
for target in targets:
    before = target.read_bytes()
    try:
        target.write_bytes(b'not allowed')
    except OSError as error:
        assert error.errno == errno.EROFS, error
    else:
        raise AssertionError('Grounding wrote target or data')
    assert target.read_bytes() == before
print('GROUNDING_TARGET_DATA_READONLY')
sample = {'suites': {name: {'candidate': 'smoke', 'labels': [0, 1, 0, 1],
    'predictions': [0, 0, 0, 0]} for name in ('suite_one', 'suite_two')}}
Path('/acceptance/smoke').mkdir(exist_ok=True)
Path('/acceptance/smoke/predictions.json').write_text(json.dumps(sample))
"""
                return (("ground_readonly", "Bash", {"command": "python3 - <<'PY'\n" + script + "PY", "timeout": 10000}),)
        elif request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            if step == 1:
                return (("expert_read", "Read", {"file_path": "/acceptance/evaluate.py"}),)
            if step == 2:
                script = f"""import errno
from pathlib import Path
path = Path('/acceptance/evaluate.py')
before = path.read_bytes()
try:
    path.write_text('raise RuntimeError("expert replacement")')
except OSError as error:
    assert error.errno == errno.EROFS, error
else:
    raise AssertionError('expert replaced the shared evaluator')
assert path.read_bytes() == before and {self.nonce!r}.encode() in before
print({_PROTECTED!r})
"""
                return (("expert_readonly", "Bash", {"command": "python3 - <<'PY'\n" + script + "PY", "timeout": 10000}),)
            if step in {3, 4}:
                name = "experiment.py" if step == 3 else "parse_local.py"
                return (("expert_raw" if step == 3 else "expert_parser", "Write", {
                    "file_path": "/workspace/" + name, "content": _experiment_files(request.role)[name].decode()}),)
            if step == 5:
                script = """set -e
mkdir -p /tmp/shared-local-check /workspace/smoke
(cd /tmp/shared-local-check && python3 /workspace/experiment.py)
cp /tmp/shared-local-check/predictions.json /workspace/smoke/predictions.json
HYPOLOOM_MEASUREMENT_RAW_ROOT=/tmp/shared-local-check HYPOLOOM_MEASUREMENT_RESULT_PATH=/tmp/shared-local-check/local.json python3 /workspace/parse_local.py
HYPOLOOM_MEASUREMENT_RAW_ROOT=/tmp/shared-local-check HYPOLOOM_MEASUREMENT_RESULT_PATH=/tmp/shared-local-check/goal.json python3 /acceptance/evaluate.py
python3 - <<'PY'
import json
from pathlib import Path
local = json.loads(Path('/tmp/shared-local-check/local.json').read_text())
goal = json.loads(Path('/tmp/shared-local-check/goal.json').read_text())
assert set(goal) == {'measurements', 'checks'} and set(local) == {'metric', 'value'}
print('EXPERT_RAW_AND_BOTH_PARSERS_CHECKED')
PY
"""
                return (("expert_local_check", "Bash", {"command": script, "timeout": 10000}),)
            if self.local_stop_repair and step == 6:
                return (("wrong_local_envelope", "Write", {"file_path": "/workspace/parse_local.py",
                    "content": _common_source(self.nonce)}),)
            if self.local_stop_repair and step == 8:
                assert "frozen metric" in json.dumps(self.requests[-1]), "native Stop did not return its actual C5 validation error"
                return (("repair_local_envelope", "Write", {"file_path": "/workspace/parse_local.py",
                    "content": _experiment_files(request.role)["parse_local.py"].decode()}),)
        elif request.command is AgentCommand.EXPERIMENT_EXECUTION:
            if step == 1:
                return (("shared_submit", "mcp__host-tools__submit_experiment", {"candidate": self.designs[request.role], "gpus": 0}),)
            if request.role not in self.waited:
                return ((f"shared_wait_{step}", "mcp__host-tools__wait_experiment", {
                    "exp_id": self.experiments[request.role], "max_wait_seconds": 5}),)
        return self._structured(request)


def _payload(root: Path, mount: Path, *, roles=(_LOCAL, _GOAL), iterations=1, fixture_data=None):
    _, target, data = _materialize_expert_loop_pilot_v3(mount)
    (data / "validation.json").write_text(json.dumps(_fixture_pairs() if fixture_data is None else fixture_data))
    source = documents()
    source["goal"] = {"title": "One candidate across two validation suites",
        "description": "Exceed 0.5 accuracy strictly on both complete suites with a single candidate; preserve complete evidence. Local diagnostics do not establish the whole goal.",
        "evaluation": "grounding", "constraints": ["Read the supplied fixed labels and prediction candidates; never combine the best suite from different candidates."],
        "stop_conditions": {"max_rounds_per_expert": 1, "max_wall_clock_hours": 1}}
    source["experts"] = {"defaults": {"model": _MODEL, "effort": "high", "max_forks": 1, "max_hypotheses_per_round": 1},
        "experts": [{"name": role, "role": "Independent research candidate", "persona": "Measure both suites honestly.", "focus": ["Read and use the common evaluator."]}
                    for role in roles]}
    source["methodology"] = {"schema_version": 4, "runtime": {"target_repo": str(target)},
        "budget": {"max_active_agents": 1, "max_active_processes": 1, "max_active_reviews": 1,
            "max_active_hypotheses": len(roles), "max_experiments": len(roles) * iterations, "max_retries": 1,
            "max_infrastructure_retries": 0, "max_process_lost_retries": 0,
            "agent_invocation": {"max_turns": 24, "max_tokens": 30000, "max_output_bytes": 262144,
                "wall_seconds": 180, "max_cost": "0.5", "token_enforcement_mode": "observe"}},
        "inner_loop": {"max_iterations_per_hypothesis": iterations, "repair_attempts": 1},
        "interaction": {"leader": {"enabled": False}, "debate": {"enabled": False, "rounds": 3}},
        "graph_evolution": {"enabled": False, "stagnation_rounds": 1},
        "output_correction": {"max_failed_validations": 2},
        "literature": {"enabled": False, "tools": []}}
    source["language"] = {"prompt_language": "en", "report_language": "en", "code_comment_language": "en"}
    source["notify"] = {"sinks": {}, "events": {}}
    source["safety"] = {"allow_read_paths": [str(data)], "sandbox_os": "linux_bwrap", "gpus": [],
        "experiment_timeout_minutes": 1, "per_expert_budget_usd": 10, "total_budget_usd": 20,
        "bash": {"network_allowed": False, "deny_patterns": []},
        "filesystem": {"write_only_within_worktree": True, "protected_paths": [".git/"]}}
    profile = root / "profile"
    profile.mkdir()
    for name, document in source.items():
        (profile / (name + ".yaml")).write_text(yaml.safe_dump(document))
    admitted = admit_profile(profile, run_id=_RUN, allow_gpu=False)
    assert admitted.admitted and admitted.create_run_payload is not None, admitted.to_dict()
    (root / "profile-admission.json").write_text(json.dumps(admitted.to_dict(), indent=2))
    return admitted.create_run_payload


def test_common_evaluator_fixture_failure_repair_and_exact_candidate_measurements(tmp_path):
    with tempfile.TemporaryDirectory(prefix="prom-common-profile-", dir="/var/tmp") as mount:
        payload = _payload(tmp_path, Path(mount))
        assert payload["research_contract"]["schema_version"] == 3
        assert payload["research_contract"]["goal"]["evaluation"] == {"mode": "grounding"}
        assert len(payload["research_contract"]["roles"]) == 2
        assert not payload["execution"]["allow_gpu"]
    broken = subprocess.run([sys.executable, "-c", _common_source("offline", broken=True), "--self-test"], capture_output=True, text=True)
    assert broken.returncode != 0 and _BROKEN in broken.stderr
    repaired = subprocess.run([sys.executable, "-c", _common_source("offline"), "--self-test"], capture_output=True, text=True)
    assert repaired.returncode == 0 and _REPAIRED in repaired.stdout
    namespace = {"__name__": "fixture", "__builtins__": __builtins__}
    exec(compile(_common_source("offline").split("if '--self-test'", 1)[0], "evaluate.py", "exec"), namespace)
    data = _fixture_pairs()
    expected = {_LOCAL: {"suite_one_accuracy": "1", "suite_two_accuracy": "0.5"},
                _GOAL: {"suite_one_accuracy": "0.75", "suite_two_accuracy": "0.75"}}
    for role in (_LOCAL, _GOAL):
        suites = {name: {"candidate": role, "labels": data["labels"], "predictions": data["candidates"][role][name]}
                  for name in ("suite_one", "suite_two")}
        result = namespace["evaluate"]({"suites": suites})
        assert result == {"measurements": expected[role], "checks": {"complete": True, "single_candidate": True}}
        suites["suite_two"]["candidate"] = "other-candidate"
        assert namespace["evaluate"]({"suites": suites})["checks"]["single_candidate"] is False


def test_native_grounding_shared_source_local_failure_and_complete_goal_stop(tmp_path, monkeypatch):
    from hypoloom.runner import LocalRunner
    smoke_processes = []
    original_start = LocalRunner.start
    def start(runner, request, **kwargs):
        handle = original_start(runner, request, **kwargs)
        if request.attempt_id == "parser-smoke":
            smoke_processes.append(handle)
        return handle
    monkeypatch.setattr(LocalRunner, "start", start)
    executable = _installed_claude()
    scenario = _AcceptanceScenario(tmp_path, "shared-evaluator-" + secrets.token_hex(12))
    scenario.local_stop_repair = True
    key = "local-shared-provider-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    (tmp_path / "settings.json").write_text("{}\n")
    (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(tmp_path, executable, f"http://127.0.0.1:{provider.server_port}", key)
    host.grounding_web = _NoPublicWeb()
    first_failure = None
    goal_resumed = False
    try:
        with tempfile.TemporaryDirectory(prefix="prom-common-g3-", dir="/var/tmp") as mount:
            payload = _payload(tmp_path, Path(mount))
            original = copy.deepcopy(payload)
            host.initialize()
            response = host.handle(ControlRequest(1, "create-native-common", 0, "CREATE_RUN", payload))
            assert response.status == "accepted", response
            # This scenario requires the locally supported, globally failing
            # candidate first. Express that ordering through ordinary controls;
            # fair component rotation does not promise roster-order dispatch.
            goal_scope = {"role": _GOAL, "component_id": None, "chain": None}
            paused = host.handle(ControlRequest(1, "pause-goal-candidate", None, "PAUSE",
                {"run_id": _RUN, "scope": goal_scope, "reason": "operator"}))
            assert paused.status == "accepted", paused
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                host.tick()
                assert not scenario.errors, scenario.errors
                outcomes = host.store.list_facts(_RUN, "CanonicalOutcomeAccepted")
                head = host.store.latest_transition(_RUN)
                assessments = host.store.list_facts(_RUN, "HypothesisAssessed")
                if len(assessments) == 1:
                    assert assessments[0]["payload"]["assessment"] == "SUPPORTED"
                    assert not host.interaction._closing(_RUN)
                if len(outcomes) == 1 and first_failure is None:
                    first = outcomes[0]["payload"]
                    assert first["outcome"] == "SUCCESS" and first["metric_value"] == "1"
                    assert first["goal_evaluation"]["measurements"] == {"suite_one_accuracy": "1", "suite_two_accuracy": "0.5"}
                    assert head["state"] == "ACTIVE"
                    assert not any(row["payload"]["verdict"] == "GOAL_MET" for row in host.store.list_facts(_RUN, "GoalVerdictRecorded"))
                    first_failure = {"head": dict(head), "outcome": outcomes[0]}
                if first_failure is not None and len(assessments) == 1 and not goal_resumed:
                    resumed = host.handle(ControlRequest(1, "resume-goal-candidate", None, "RESUME",
                        {"run_id": _RUN, "scope": goal_scope, "reason": "operator"}))
                    assert resumed.status == "accepted", resumed
                    goal_resumed = True
                if head["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
                time.sleep(0.01)
            assert host.store.latest_transition(_RUN)["state"] == "COMPLETED", host.store.list_attempts()
            assert payload == original and first_failure is not None and goal_resumed
            assert scenario.source_reads == scenario.source_readonly == {_LOCAL, _GOAL}
            assert scenario.shared_inputs[_LOCAL] == scenario.shared_inputs[_GOAL]
            assert set(scenario.grounding_evidence) == {"ground_target", "ground_write", "ground_failure", "ground_repair", "ground_success", "ground_readonly"}
            grounding_observation = json.loads(host.store.get_attempt(scenario.source_attempt_id)["observation_json"])
            correction = grounding_observation["diagnostics"]["structured_output_correction"]
            assert correction["status"] == "recovered" and correction["rejected_count"] == 1
            assert "missing measurements" in correction["rounds"][0]["validation_error"]
            designs = [json.loads(row["observation_json"]) for row in host.store.list_attempts()
                if row["kind"] == "AGENT" and json.loads(row["observation_json"])["request"]["command"] == "EXPERIMENT_DESIGN_IMPLEMENT"]
            assert len(designs) == 2
            for design in designs:
                check = design["diagnostics"]["structured_output_correction"]
                assert check["status"] == "recovered" and check["rejected_count"] == 1
                assert "frozen metric" in check["rounds"][0]["validation_error"]
            outcomes = host.store.list_facts(_RUN, "CanonicalOutcomeAccepted")
            assert len(outcomes) == 2
            assert outcomes[1]["payload"]["outcome"] == "CLEAN_REFUTATION"
            assert outcomes[1]["payload"]["metric_value"] == "0"
            assert outcomes[1]["payload"]["goal_evaluation"] == {"source_attempt_id": scenario.source_attempt_id,
                "measurements": {"suite_one_accuracy": "0.75", "suite_two_accuracy": "0.75"},
                "checks": {"complete": True, "single_candidate": True}}
            preregistrations = host.store.list_facts(_RUN, "ExperimentPreregistered")
            assert len(preregistrations) == 2
            assert all(row["payload"]["goal_evaluation"] == {"scope": "GOAL", "source_attempt_id": scenario.source_attempt_id,
                "acceptance": _DECLARATION} for row in preregistrations)
            verdicts = host.store.list_facts(_RUN, "GoalVerdictRecorded")
            assert len([row for row in verdicts if row["payload"]["verdict"] == "GOAL_MET"]) == 1
            attempts = host.store.list_attempts()
            assert all(row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED" for row in attempts)
            processes = [row for row in attempts if row["kind"] == "PROCESS"]
            assert len(processes) == 2
            agents = [json.loads(row["observation_json"]) for row in attempts if row["kind"] == "AGENT"]
            grounding = [row for row in agents if row["request"]["command"] == "GROUNDING"]
            assert len(grounding) == 1
            transcript_root = host.publisher.root / grounding[0]["session"]["transcript"]["path"]
            assert (transcript_root / "evaluator/evaluate.py").read_text() == _common_source(scenario.nonce)
            transcript = (transcript_root / "transcript.jsonl").read_text()
            assert all(text in transcript for text in (_BROKEN, _REPAIRED, scenario.nonce))
            assert sum(row["request"]["command"] == "FINAL_REPORT" for row in agents) == 1
            report = Oracle(host.store.database_path, host.publisher.root).audit(_RUN)
            assert report.ok, report.to_dict()
            assert not host.running_agents and not host.running
            assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"])
            # Grounding plus both local DESIGNs each reject once and then recover.
            assert len(smoke_processes) == 6
            assert all(not process_group_exists(handle.process_group_id) and handle._guardian.poll() is not None
                and not Path(handle.request.cwd).exists() for handle in smoke_processes)
            (tmp_path / "shared-acceptance-evidence.json").write_text(json.dumps({
                "grounding_tools": scenario.grounding_evidence, "grounding_transcript": str(transcript_root),
                "shared_inputs": scenario.shared_inputs, "source_reads": sorted(scenario.source_reads),
                "source_readonly": sorted(scenario.source_readonly), "first_nonqualifying_outcome": first_failure,
                "outcomes": outcomes, "preregistrations": preregistrations, "verdicts": verdicts,
                "process_attempt_ids": [row["attempt_id"] for row in processes], "oracle": report.to_dict(),
                "host_parser_smoke_process_groups": [handle.process_group_id for handle in smoke_processes]}, indent=2))
    finally:
        host.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
