"""Real CC repairs a custom parser, then Host MCP executes shell+CPU parser."""

from __future__ import annotations

import json
import copy
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

import pytest
import yaml

from hypoloom.agent_aci import AgentResponse, WorkspaceArtifact
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.oracle import Oracle
from hypoloom.profile_admission import admit_profile
from hypoloom.profile_v4 import request_node_runtime
from hypoloom.workflow_host import WorkflowHost

from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results
from test_deferred_measurement_host import DeclaredMeasurementAgent
from test_expert_loop_host import _pilot_payload
from test_profile_admission import _materialize_expert_loop_pilot_v3
from test_profile_v4 import documents
from test_expert_memory_g3 import _MemoryScenario
from test_full_cc_host_g3 import (
    _LOCAL_FAILURE,
    _LOCAL_SUCCESS,
    _MODEL,
    _new_host,
    _request_from_provider,
    _start_provider,
)


def _six_yaml_profile_v4_payload(tmp_path: Path, mount: Path, run_id: str) -> dict[str, Any]:
    """Admit the actual six source documents; never edit the admitted science."""
    _, target, data = _materialize_expert_loop_pilot_v3(mount)
    source = documents()
    source["goal"]["stop_conditions"] = {"max_rounds_per_expert": 1, "max_wall_clock_hours": 1}
    source["experts"] = {"defaults": {"model": _MODEL, "effort": "high", "max_forks": 1,
        "max_hypotheses_per_round": 1}, "experts": [{"name": "ml-researcher",
            "role": "Measurement researcher", "persona": "Inspect and test declared measurements.",
            "focus": ["Reproducible custom CPU measurement"],
            "stage_overrides": {"inner_loop.code": {"model": _MODEL, "effort": "high"}}}]}
    source["methodology"] = {"schema_version": 4, "runtime": {"target_repo": str(target)},
        "budget": {"max_active_hypotheses": 1, "max_experiments": 2,
            "max_retries": 2, "max_infrastructure_retries": 1, "max_process_lost_retries": 1,
            "agent_invocation": {"max_turns": 24, "max_tokens": 20000,
                "max_output_bytes": 131072, "wall_seconds": 180, "max_cost": "0.5",
                "token_enforcement_mode": "observe"}}}
    source["language"] = {"prompt_language": "en", "report_language": "en", "code_comment_language": "en"}
    source["notify"] = {"sinks": {}, "events": {}}
    source["safety"] = {"allow_read_paths": [str(data)], "sandbox_os": "linux_bwrap",
        "experiment_timeout_minutes": 1, "per_expert_budget_usd": 10, "total_budget_usd": 20}
    profile = tmp_path / "profile-v4"
    profile.mkdir()
    for name, value in source.items():
        (profile / f"{name}.yaml").write_text(yaml.safe_dump(value), encoding="utf-8")
    assert {path.name for path in profile.iterdir()} == {
        "goal.yaml", "experts.yaml", "methodology.yaml", "language.yaml", "notify.yaml", "safety.yaml"}
    admitted = admit_profile(profile, run_id=run_id, allow_gpu=False)
    assert admitted.admitted, admitted.to_dict()
    assert admitted.create_run_payload is not None
    (tmp_path / "profile-admission.json").write_text(json.dumps(admitted.to_dict(), indent=2))
    payload = admitted.create_run_payload
    contract = payload["research_contract"]
    assert source["goal"]["metric"] is None
    assert contract["components"][0]["result"] == {"kind": "metric", "deferred": True}
    assert contract["roles"][0]["id"] == "ml-researcher"
    assert contract["methodology"]["controls"]["product"]["profile_version"] == 4
    assert not payload["execution"]["allow_gpu"]
    return payload


class _NoPublicWeb:
    """This measurement rehearsal never delegates to a public web transport."""
    def web_search(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("public web retrieval is outside this scripted C5 rehearsal")

    web_fetch = web_search


def test_six_yaml_profile_v4_custom_measurement_admission_to_real_cpu_oracle(tmp_path: Path):
    run_id = "six-yaml-v4-cpu"
    host = WorkflowHost(tmp_path / "state", local_agent=DeclaredMeasurementAgent(), grounding_web=_NoPublicWeb())
    try:
        with tempfile.TemporaryDirectory(prefix="hypoloom-v4-cpu-", dir="/var/tmp") as mount:
            payload = _six_yaml_profile_v4_payload(tmp_path, Path(mount), run_id)
            unchanged = copy.deepcopy(payload)
            host.initialize()
            created = host.handle(ControlRequest(schema_version=1, request_id="create-v4-cpu",
                expected_revision=0, command="CREATE_RUN", payload=payload))
            assert created.status == "accepted", created
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                host.tick()
                if host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
                time.sleep(0.005)
            assert host.store.latest_transition(run_id)["state"] == "COMPLETED", host.store.list_attempts()
            assert payload == unchanged, "admitted configuration was changed after admission"
            outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
            assert len(outcomes) == 1 and outcomes[0]["payload"]["metric_value"] == "-0.1"
            assert outcomes[0]["payload"]["outcome"] == "SUCCESS"
            processes = [row for row in host.store.list_attempts() if row["kind"] == "PROCESS"]
            assert len(processes) == 1 and processes[0]["terminal_reason"] == "SUCCEEDED"
            audit = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
            assert audit.ok, audit.to_dict()
    finally:
        host.close()


class _DeclaredScenario(_MemoryScenario):
    def __init__(self, root: Path, nonce: str):
        super().__init__(root, nonce)
        self.reviser = DeclaredMeasurementAgent()
        self.parser_evidence: dict[str, str] = {}
        self.final_reports: dict[str, Any] = {}

    def record(self, document: Mapping[str, Any]) -> int:
        request = _request_from_provider(document)
        if request.command not in {
            AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
            AgentCommand.EXPERIMENT_EXECUTION,
        }:
            number = super().record(document)
            if request.command is AgentCommand.FINAL_REPORT:
                # Reuse actual report tools plus decoded source comparison:
                # shared-goal stop reasons contain nested command JSON.
                from test_native_shared_goal_stop_g3 import _SharedFinalReport
                if request.attempt_id not in self.final_reports:
                    report = _SharedFinalReport(request)
                    report.tool_prefix = request.attempt_id + "__"
                    self.final_reports[request.attempt_id] = report
                report = self.final_reports[request.attempt_id]
                report.structured_output = self._structured(request)[0][2]
                report.record(document)
                self.errors.extend(report.errors)
            return number
        self.requests.append(document)
        self.current = request
        self.agent_requests[request.attempt_id] = request
        self.steps[request.attempt_id] += 1
        try:
            response = self.reviser.invoke(request)
            assert isinstance(response, AgentResponse)
            self.responses[request.attempt_id] = response
            step = self.steps[request.attempt_id]
            results = _tool_results(document)
            if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
                self.design_candidate = response.typed_candidate
                previous = {
                    2: "write_shell",
                    3: "write_parser",
                    4: "parser_failure",
                    5: "repair_parser",
                    6: "parser_success",
                }.get(step)
                if previous:
                    result = results[previous]
                    rendered = _result_text(result)
                    if previous == "parser_failure":
                        assert (
                            result.get("is_error") is True
                            and _LOCAL_FAILURE in rendered
                        ), result
                    else:
                        assert result.get("is_error") is not True, result
                    if previous == "parser_success":
                        assert _LOCAL_SUCCESS in rendered, result
                    self.parser_evidence[previous] = rendered
            elif step == 2:
                result = results["memory_submit"]
                assert result.get("is_error") is not True, result
                self.experiment_id = json.loads(_result_text(result))["exp_id"]
            elif step == 3:
                result = results["memory_wait"]
                assert result.get("is_error") is not True, result
                waited = json.loads(_result_text(result))
                assert (
                    waited["terminal"] and waited["outcome"]["outcome"] == "SUCCESS"
                ), waited
                assert waited["outcome"]["metric_value"] == "-0.1", waited
                self.parser_evidence["cpu_outcome"] = _result_text(result)
        except Exception as exc:
            self.errors.append(
                f"provider {len(self.requests)}: {type(exc).__name__}: {exc}"
            )
        return len(self.requests)

    def tool_calls(self, number: int):
        request = self.current
        if request is None or self.errors:
            return ()
        if request.command is AgentCommand.FINAL_REPORT:
            report = self.final_reports[request.attempt_id]
            return tuple((report.tool_prefix + identity, name, arguments)
                         for identity, name, arguments in report.tool_calls(number))
        if request.command is not AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            return super().tool_calls(number)
        step = self.steps[request.attempt_id]
        files = {
            item.path: item.content_bytes().decode()
            for item in self.responses[request.attempt_id].proposed_workspace_artifacts
        }
        if step in {1, 2}:
            path = "experiment.sh" if step == 1 else "parse_metrics.py"
            text = files[path]
            if step == 2:
                text = f"assert False, '{_LOCAL_FAILURE}'\n" + text
            return (
                (
                    "write_shell" if step == 1 else "write_parser",
                    "Write",
                    {"file_path": "/workspace/" + path, "content": text},
                ),
            )
        if step in {3, 5}:
            # Local CC tests use scratch paths; the captured parser retains
            # production /raw and /parsed names, exercised by Host afterwards.
            script = (
                "set -e\nmkdir -p /tmp/raw /tmp/parsed /workspace/smoke\n"
                "(cd /tmp/raw && /bin/sh /workspace/experiment.sh)\n"
                "cp /tmp/raw/timings.csv /workspace/smoke/timings.csv\n"
                "python3 - <<'PY'\nimport json\nfrom pathlib import Path\n"
                "source = Path('/workspace/parse_metrics.py').read_text()\n"
                "source = source.replace('/raw/', '/tmp/raw/').replace('/parsed/', '/tmp/parsed/')\n"
                "exec(compile(source, '/workspace/parse_metrics.py', 'exec'))\n"
                "result = json.loads(Path('/tmp/parsed/result.json').read_text())\n"
                "assert result == {'metric': 'median_latency_delta', 'value': '9.0', 'baseline': '10.0'}, result\n"
                f"print({_LOCAL_SUCCESS!r})\nPY"
            )
            return (
                (
                    "parser_failure" if step == 3 else "parser_success",
                    "Bash",
                    {"command": script, "timeout": 10000},
                ),
            )
        if step == 4:
            return (
                (
                    "repair_parser",
                    "Edit",
                    {
                        "file_path": "/workspace/parse_metrics.py",
                        "old_string": f"assert False, '{_LOCAL_FAILURE}'\n",
                        "new_string": "",
                    },
                ),
            )
        return self._structured(request)


def _assert_cycle_report_scope(payload, scenario, observations):
    product = payload["research_contract"]["methodology"]["controls"].get("product", {})
    shared = product.get("goal", {}).get("completion_mode") == "shared_goal"
    count = 0 if shared else 1
    assert sum(item["request"]["command"] == "CYCLE_REPORT" for item in observations) == count
    assert len(scenario.report_attempts) == count and len(scenario.report_evidence) == count * 5
    if shared:
        assert not {"ADJUDICATE", "HYPOTHESIS_UPDATE", "SELF_EVOLVE"}.intersection(
            item["request"]["command"] for item in observations)


def test_declared_final_report_fixture_uses_native_skill_read_write_and_boundaries(tmp_path, monkeypatch):
    from dataclasses import replace
    from hypoloom.agent_aci import EmbeddedInput
    from test_final_report import report_request
    request = report_request()
    item = request.input_artifacts[0]
    context = json.loads(item.content_bytes())
    context["stop_reason"] = 'Actual stop controls: [{"stop_disposition":"COMPLETED"}]'
    request = replace(request, input_artifacts=(EmbeddedInput.from_bytes(item.ref, item.media_type,
        json.dumps(context).encode()),))
    scenario = _DeclaredScenario(tmp_path, "offline-native-report")
    monkeypatch.setattr(__import__(__name__), "_request_from_provider", lambda _: request)
    def record_base(self, document):
        self.requests.append(document)
        self.current = request
        self.steps[request.attempt_id] += 1
        self.responses[request.attempt_id] = self.reviser.invoke(request)
        return len(self.requests)
    monkeypatch.setattr(_MemoryScenario, "record", record_base)
    document = {"messages": []}
    expected = [("Skill", "The report is a narrative artifact derived from accepted records"),
        ("Read", "1\t" + json.dumps(context)), ("Write", "File created successfully"),
        ("Bash", "FINAL_REPORT_FILE_AND_BOUNDARIES")]
    for name, result in expected:
        number = scenario.record(document)
        identity, actual, arguments = scenario.tool_calls(number)[0]
        assert actual == name
        if name == "Write":
            assert arguments["file_path"] == "/report/FINAL_REPORT.md"
            assert "verdict-fact: GOAL_MET" in arguments["content"] and "outcome-accepted" in arguments["content"]
        document = {"messages": [{"role": "user", "content": [{"type": "tool_result",
            "tool_use_id": identity, "content": result}]}]}
    number = scenario.record(document)
    assert scenario.tool_calls(number)[0][1] == "StructuredOutput"
    assert not scenario.errors and len(scenario.final_reports[request.attempt_id].evidence) == 4
    assert not scenario.report_attempts and not scenario.report_evidence


@pytest.mark.parametrize("mode", [None, "explicit_signals", "shared_goal"])
def test_declared_cycle_report_fixture_follows_frozen_completion_mode(tmp_path, mode):
    scenario = _DeclaredScenario(tmp_path, "offline-cycle")
    controls = {} if mode is None else {"product": {"goal": {"completion_mode": mode}}}
    payload = {"research_contract": {"methodology": {"controls": controls}}}
    observations = []
    if mode != "shared_goal":
        scenario.report_attempts.append("actual-cycle-report")
        scenario.report_evidence.update({(1, str(index)): "actual tool result" for index in range(5)})
        observations.append({"request": {"command": "CYCLE_REPORT"}})
    _assert_cycle_report_scope(payload, scenario, observations)
    observations.append({"request": {"command": "CYCLE_REPORT"}})
    with pytest.raises(AssertionError):
        _assert_cycle_report_scope(payload, scenario, observations)


@pytest.mark.parametrize("profile_kind", ("direct", "six-yaml-v4"))
def test_real_claude_shell_parser_repair_mcp_execution_and_independent_oracle(
    tmp_path: Path, profile_kind: str,
):
    _run_real_claude_measurement_g3(tmp_path, profile_kind)


def _run_real_claude_measurement_g3(tmp_path: Path, profile_kind: str, *,
    scenario_factory: Any = _DeclaredScenario, grounding_web: Any = None) -> _DeclaredScenario:
    executable = _installed_claude()
    nonce = "declared-parser-report-" + secrets.token_hex(12)
    scenario = scenario_factory(tmp_path, nonce)
    host_key = "host-only-declared-g3-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, host_key)
    (tmp_path / "settings.json").write_text("{}\n")
    (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(
        tmp_path, executable, f"http://127.0.0.1:{provider.server_port}", host_key
    )
    host.grounding_web = _NoPublicWeb() if grounding_web is None else grounding_web
    run_id = "real-cc-declared-measurement-g3"
    try:
        with tempfile.TemporaryDirectory(
            prefix="hypoloom-c5-cli-g3-", dir="/var/tmp"
        ) as mount:
            if profile_kind == "six-yaml-v4":
                payload = _six_yaml_profile_v4_payload(tmp_path, Path(mount), run_id)
            else:
                payload = _pilot_payload(tmp_path, Path(mount), run_id)
                contract = payload["research_contract"]
                contract["schema_version"] = 2
                component = contract["components"][0]
                component.update(result={"kind": "metric", "deferred": True},
                    seeds=[], protocol=None, required_artifacts=[])
                contract["goal"]["summary_metric"] = {
                    "name": "required_component_completion", "direction": "MAXIMIZE",
                    "target": "1", "display_only": True}
            unchanged = copy.deepcopy(payload)
            host.initialize()
            created = host.handle(
                ControlRequest(
                    schema_version=1,
                    request_id="create-declared-g3",
                    expected_revision=0,
                    command="CREATE_RUN",
                    payload=payload,
                )
            )
            assert created.status == "accepted", created
            deadline = time.monotonic() + 100
            while time.monotonic() < deadline:
                host.tick()
                assert not scenario.errors, scenario.errors
                if host.store.latest_transition(run_id)["state"] in {
                    "COMPLETED",
                    "FAILED",
                    "CANCELLED",
                }:
                    break
                time.sleep(0.01)
            assert host.store.latest_transition(run_id)["state"] == "COMPLETED", (
                host.store.list_attempts()
            )
            assert payload == unchanged, "admitted configuration was changed after admission"
            if profile_kind == "six-yaml-v4":
                for document in scenario.requests:
                    selected = _request_from_provider(document)
                    node_runtime = request_node_runtime(selected.input_artifacts)
                    assert selected.required_model_identity == node_runtime["model"] == _MODEL
                    assert document["output_config"]["effort"] == node_runtime["effort"] == "high"
            assert len(scenario.parser_evidence) == 6
            outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
            assert (
                len(outcomes) == 1 and outcomes[0]["payload"]["metric_value"] == "-0.1"
            )
            attempts = host.store.list_attempts()
            assert len([row for row in attempts if row["kind"] == "PROCESS"]) == 1
            assert all(
                row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED"
                for row in attempts
            )
            observations = [
                json.loads(row["observation_json"])
                for row in attempts
                if row["kind"] == "AGENT"
            ]
            _assert_cycle_report_scope(payload, scenario, observations)
            finals = [item for item in observations if item["request"]["command"] == "FINAL_REPORT"]
            assert len(finals) == len(scenario.final_reports) == 1
            final = finals[0]
            native_report = scenario.final_reports[final["request"]["attempt_id"]]
            assert len(native_report.requests) == 5 and len(native_report.evidence) == 4
            files = final["response"]["proposed_workspace_artifacts"]
            assert len(files) == 1 and files[0]["path"] == "FINAL_REPORT.md"
            assert WorkspaceArtifact.from_dict(files[0]).content_bytes() == native_report.report.encode()
            archive = host.publisher.root / final["session"]["transcript"]["path"]
            assert (archive / "FINAL_REPORT.md").read_bytes() == native_report.report.encode()
            design = next(
                item
                for item in observations
                if item["request"]["command"] == "EXPERIMENT_DESIGN_IMPLEMENT"
            )
            assert {
                item["path"]
                for item in design["response"]["proposed_workspace_artifacts"]
            } == {"experiment.sh", "parse_metrics.py", "implementation.patch"}
            for observation in observations:
                session = observation["session"]
                assert session["transcript_complete"] is True
                transcript = (
                    host.publisher.root
                    / session["transcript"]["path"]
                    / "transcript.jsonl"
                )
                assert host_key.encode() not in transcript.read_bytes()
            assert not host.running and not host.running_agents
            assert all(
                not process_group_exists(row["process_group_id"])
                for row in attempts
                if row["process_group_id"] is not None
            )
            report = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
            (tmp_path / "oracle.json").write_text(json.dumps(report.to_dict(), indent=2))
            assert report.ok, report.to_dict()
            assert report.canonical_outcome_count == 1 and report.artifact_counts[
                "TRANSCRIPT"
            ] == len(observations)
            assert report.open_attempt_count == report.pending_effect_count == 0
            return scenario
    finally:
        host.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
        (tmp_path / "parser-native-evidence.json").write_text(json.dumps(scenario.parser_evidence, indent=2))
        (tmp_path / "final-native-evidence.json").write_text(json.dumps(
            {identity: report.evidence for identity, report in scenario.final_reports.items()}, indent=2))
        (tmp_path / "provider-errors.json").write_text(json.dumps(scenario.errors, indent=2))
