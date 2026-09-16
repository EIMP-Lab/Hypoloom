"""Fresh-root G3 through real CLI, scoped MCP, CPU process and publication.

Only model choices and literature fixtures are deterministic.  WorkflowHost,
ClaudeAgentHost, bubblewrap, session persistence, provider transport, tool
admission, Runner, collection, evaluation and Oracle are production objects.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from http.server import HTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

import yaml

from hypoloom.agent_aci import AgentRequest, AgentResponse, CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION, EmbeddedInput, WorkspaceArtifact
from hypoloom.claude_agent_host import ClaudeAgentHost, process_group_exists
from hypoloom.compute_resources import (
    ComputeCapabilities,
    LocalComputeResources,
)
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import FakeAgent
from hypoloom.literature import FixedLiteratureIO, OpenAlexLiteratureFetcher
from hypoloom.oracle import Oracle
from hypoloom.profile_admission import admit_profile
from hypoloom.workflow_host import WorkflowHost

from test_claude_file_tool_cli import (
    _handler_for,
    _installed_claude,
    _result_text,
    _tool_results,
)
from test_expert_loop_host import _pilot_payload
from test_literature import RecordingOpenAlexTransport, _openalex_work


_MODEL = "claude-sonnet-4-6"
_LOCAL_FAILURE = "G3_LOCAL_TEST_REQUIRES_REPAIR"
_LOCAL_SUCCESS = "G3_LOCAL_TEST_REPAIRED"
_PROCESS_FAILURE = "G3_PROCESS_REQUIRES_REPAIR"
_MOUNTS_PROTECTED = "G3_PRIVATE_MOUNTS_PROTECTED"
_PROFILE_ENV_SUCCESS = "G3_PROFILE_PYYAML_DATA_READONLY"


def _local_experiment_command(*experiment_args: str) -> str:
    argv_suffix = "".join(f", {argument!r}" for argument in experiment_args)
    return (
        "python3 - <<'PY'\n"
        "import json, os, subprocess, sys, tempfile\nfrom pathlib import Path\n"
        "with tempfile.TemporaryDirectory(prefix='hypoloom-local-output-', dir='/tmp') as output:\n"
        f"    subprocess.run([sys.executable, '-B', '/workspace/experiment.py'{argv_suffix}], cwd=output,\n"
        "        env={**os.environ, 'HYPOLOOM_RESULT_PATH': 'result.json'}, check=True)\n"
        "    print(json.dumps(json.loads((Path(output) / 'result.json').read_text())))\n"
        "PY"
    )


_LOCAL_EXPERIMENT_COMMAND = _local_experiment_command()


class _CatalogTransport(RecordingOpenAlexTransport):
    def get(self, **arguments: Any) -> Any:
        target = arguments["target"]
        if target.startswith("/works?"):
            filters = parse_qs(urlsplit(target).query)["filter"][0]
            start = re.search(r"from_publication_date:(\d{4})", filters)
            assert start is not None
            paper = _openalex_work(1000 + len(self.calls), int(start.group(1)))
            self.body_override = json.dumps({"results": [paper]}).encode()
        try:
            return super().get(**arguments)
        finally:
            self.body_override = None


def _request_from_provider(document: Mapping[str, Any]) -> AgentRequest:
    decoder = json.JSONDecoder()
    for message in reversed(document.get("messages", [])):
        if message.get("role") != "user":
            continue
        content = message.get("content", [])
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        for block in reversed(content):
            text = block.get("text")
            if not isinstance(text, str):
                continue
            start = text.find('{"agent_request_metadata":')
            if start < 0:
                continue
            prompt, _ = decoder.raw_decode(text[start:])
            inputs = []
            for item in prompt["rendered_inputs"]:
                raw = (
                    item["content_utf8"].encode()
                    if "content_utf8" in item
                    else base64.b64decode(item["content_base64"])
                )
                inputs.append(
                    EmbeddedInput.from_bytes(
                        item["ref"], item["media_type"], raw
                    ).to_dict()
                )
            return AgentRequest.from_dict(
                {**prompt["agent_request_metadata"], "input_artifacts": inputs}
            )
    raise AssertionError("real CLI request lacks its frozen AgentRequest prompt")


class _HostScenario:
    def __init__(self, private_root: Path) -> None:
        self.private_root = private_root
        self.environment_probe = ""
        self.requests: list[Mapping[str, Any]] = []
        self.errors: list[str] = []
        self.steps: dict[str, int] = defaultdict(int)
        self.agent_requests: dict[str, AgentRequest] = {}
        self.responses: dict[str, AgentResponse] = {}
        self.design_candidate: Mapping[str, Any] | None = None
        self.experiment_id: str | None = None
        self.repaired_experiment_id: str | None = None
        self.tool_evidence: dict[str, Mapping[str, Any]] = {}
        self.current: AgentRequest | None = None
        self.execution_attempt_ids: list[str] = []

    def record(self, document: Mapping[str, Any]) -> int:
        self.requests.append(document)
        try:
            request = _request_from_provider(document)
            self.current = request
            self.agent_requests[request.attempt_id] = request
            self.steps[request.attempt_id] += 1
            response = FakeAgent().invoke(request)
            assert isinstance(response, AgentResponse)
            self.responses[request.attempt_id] = response
            step = self.steps[request.attempt_id]
            results = _tool_results(document)
            if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
                self.design_candidate = response.typed_candidate
                previous = {
                    2: "write_design",
                    3: "test_failure",
                    4: "repair_design",
                    5: "test_success",
                    6: "protected_mounts",
                }.get(step)
                if previous is not None:
                    result = results[previous]
                    self.tool_evidence[previous] = result
                    if previous == "test_failure":
                        assert result.get("is_error") is True, result
                        assert _LOCAL_FAILURE in _result_text(result), result
                    else:
                        assert result.get("is_error") is not True, result
                        if previous == "test_success":
                            assert _LOCAL_SUCCESS in _result_text(result), result
                            if self.environment_probe:
                                assert _PROFILE_ENV_SUCCESS in _result_text(result), (
                                    result
                                )
                        if previous == "protected_mounts":
                            assert _MOUNTS_PROTECTED in _result_text(result), result
            elif request.command is AgentCommand.EXPERIMENT_EXECUTION:
                if request.attempt_id not in self.execution_attempt_ids:
                    self.execution_attempt_ids.append(request.attempt_id)
                recovered = len(self.execution_attempt_ids) > 1
                previous = {
                    2: "recover_list" if recovered else "inject_process_failure",
                    3: "replay_submit" if recovered else "submit_experiment",
                    4: "wait_failure",
                    5: "read_failure_log",
                    6: "repair_execution",
                    7: "execution_test_success",
                    8: "submit_repair",
                    9: "wait_repair",
                }.get(step)
                if previous is not None:
                    result = results[previous]
                    assert result.get("is_error") is not True, result
                    self.tool_evidence[previous] = result
                    if previous == "recover_list":
                        experiments = json.loads(_result_text(result))["experiments"]
                        assert len(experiments) == 1, experiments
                        self.experiment_id = experiments[0]["exp_id"]
                    elif previous in {
                        "submit_experiment",
                        "replay_submit",
                        "submit_repair",
                    }:
                        submitted = json.loads(_result_text(result))
                        if previous == "submit_experiment":
                            self.experiment_id = submitted["exp_id"]
                        elif previous == "replay_submit":
                            assert submitted["exp_id"] == self.experiment_id
                        else:
                            self.repaired_experiment_id = submitted["exp_id"]
                            assert self.repaired_experiment_id != self.experiment_id
                    elif previous in {"wait_failure", "wait_repair"}:
                        waited = json.loads(_result_text(result))
                        assert waited["terminal"] is True, waited
                        expected = (
                            "EXECUTION_FAILURE"
                            if previous == "wait_failure"
                            else "SUCCESS"
                        )
                        assert waited["outcome"]["outcome"] == expected, waited
                        if self.environment_probe:
                            assert _PROFILE_ENV_SUCCESS in waited["tail_log"], waited
                    elif previous == "read_failure_log":
                        assert _PROCESS_FAILURE in _result_text(result), result
                    elif previous == "execution_test_success":
                        assert _LOCAL_SUCCESS in _result_text(result), result
            elif request.command is AgentCommand.RESULT_ANALYSIS:
                messages = json.dumps(document["messages"])
                assert _LOCAL_FAILURE in messages
                assert _LOCAL_SUCCESS in messages
                assert "mcp__host-tools__submit_experiment" in messages
                assert _PROCESS_FAILURE in messages
                assert "mcp__host-tools__get_experiment_log" in messages
        except Exception as exc:
            self.errors.append(
                f"provider {len(self.requests)}: {type(exc).__name__}: {exc}"
            )
        return len(self.requests)

    def tool_calls(
        self, _number: int
    ) -> tuple[tuple[str, str, Mapping[str, Any]], ...]:
        request = self.current
        if request is None or self.errors:
            return ()
        step = self.steps[request.attempt_id]
        response = self.responses[request.attempt_id]
        if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            if step == 1:
                source = next(
                    item.content_bytes().decode()
                    for item in response.proposed_workspace_artifacts
                    if item.path == "experiment.py"
                )
                source = (
                    self.environment_probe
                    + f"assert 1 == 2, '{_LOCAL_FAILURE}'\n"
                    + source
                    + f"print('{_LOCAL_SUCCESS}')\n"
                )
                return (
                    (
                        "write_design",
                        "Write",
                        {"file_path": "/workspace/experiment.py", "content": source},
                    ),
                )
            if step in {2, 4}:
                return (
                    (
                        "test_failure" if step == 2 else "test_success",
                        "Bash",
                        {
                            "command": _LOCAL_EXPERIMENT_COMMAND,
                            "timeout": 10000,
                        },
                    ),
                )
            if step == 3:
                return (
                    (
                        "repair_design",
                        "Edit",
                        {
                            "file_path": "/workspace/experiment.py",
                            "old_string": "assert 1 == 2",
                            "new_string": "assert 1 == 1",
                        },
                    ),
                )
            if step == 5:
                # Opening for append performs no mutation even if the assertion
                # fails; readonly protection must reject the open itself.
                script = (
                    "import errno, os\nfrom pathlib import Path\n"
                    "inputs = list(Path('/inputs').iterdir())\nassert inputs\n"
                    "for path in [*inputs, Path('/workspace/.git')]:\n"
                    "    before = path.read_bytes()\n"
                    "    try:\n"
                    "        fd = os.open(path, os.O_WRONLY | os.O_APPEND)\n"
                    "    except OSError as error:\n"
                    "        assert error.errno == errno.EROFS, (path, error)\n"
                    "    else:\n"
                    "        os.close(fd)\n"
                    "        raise AssertionError('protected mount writable: ' + str(path))\n"
                    "    assert path.read_bytes() == before\n"
                    f"assert not Path({str(self.private_root)!r}).exists()\n"
                    "assert not Path('/config/settings.json').exists()\n"
                    "assert not Path('/state').exists()\n"
                    "assert {p.name for p in Path('/home/agent/.claude/projects').iterdir()} == {'-workspace'}\n"
                    "gitdir = Path('/workspace/.git').read_text().split('gitdir: ', 1)[1].strip()\n"
                    "assert not Path(gitdir).exists()\n"
                    "assert os.environ.get('ANTHROPIC_API_KEY') == 'hypoloom-local-provider'\n"
                    "assert 'ANTHROPIC_AUTH_TOKEN' not in os.environ\n"
                    f"print({_MOUNTS_PROTECTED!r})\n"
                )
                return (
                    (
                        "protected_mounts",
                        "Bash",
                        {
                            "command": "python3 - <<'PY'\n" + script + "PY",
                            "timeout": 10000,
                        },
                    ),
                )
        elif request.command is AgentCommand.EXPERIMENT_EXECUTION:
            if len(self.execution_attempt_ids) > 1 and step == 1:
                return (("recover_list", "mcp__host-tools__list_my_experiments", {}),)
            if step in {1, 5}:
                return (
                    (
                        "inject_process_failure" if step == 1 else "repair_execution",
                        "Edit",
                        {
                            "file_path": "/workspace/experiment.py",
                            "old_string": f"assert 1 == {1 if step == 1 else 2}, '{_LOCAL_FAILURE if step == 1 else _PROCESS_FAILURE}'",
                            "new_string": f"assert 1 == {2 if step == 1 else 1}, '{_PROCESS_FAILURE}'",
                        },
                    ),
                )
            if step in {2, 7}:
                assert self.design_candidate is not None
                return (
                    (
                        (
                            "replay_submit"
                            if len(self.execution_attempt_ids) > 1
                            else "submit_experiment"
                        )
                        if step == 2
                        else "submit_repair",
                        "mcp__host-tools__submit_experiment",
                        {
                            "candidate": self.design_candidate,
                            **(
                                {"replaces_experiment_id": self.experiment_id}
                                if step == 7
                                else {}
                            ),
                        },
                    ),
                )
            if step in {3, 8}:
                experiment_id = (
                    self.experiment_id if step == 3 else self.repaired_experiment_id
                )
                assert experiment_id is not None
                return (
                    (
                        "wait_failure" if step == 3 else "wait_repair",
                        "mcp__host-tools__wait_experiment",
                        {"exp_id": experiment_id, "max_wait_seconds": 10},
                    ),
                )
            if step == 4:
                return (
                    (
                        "read_failure_log",
                        "mcp__host-tools__get_experiment_log",
                        {"exp_id": self.experiment_id},
                    ),
                )
            if step == 6:
                return (
                    (
                        "execution_test_success",
                        "Bash",
                        {
                            "command": _LOCAL_EXPERIMENT_COMMAND,
                            "timeout": 10000,
                        },
                    ),
                )
        candidate = dict(response.typed_candidate or {})
        if "query" in candidate:
            candidate = {
                key: value
                for key, value in candidate.items()
                if key in {"query", "preferred_venues"}
            }
            structured = {"typed_candidate": candidate}
        else:
            structured = {
                "typed_candidate": candidate,
                "declared_effects": [
                    {"effect": effect.effect} for effect in response.declared_effects
                ],
            }
        return (("structured_" + request.attempt_id, "StructuredOutput", structured),)


def _start_provider(
    scenario: _HostScenario, host_key: str
) -> tuple[HTTPServer, threading.Thread]:
    base_handler = _handler_for(scenario)

    class Handler(base_handler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            if self.headers.get("x-api-key") != host_key:
                scenario.errors.append("provider authentication did not come from Host")
            super().do_POST()

    provider = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    return provider, thread


def _new_host(
    tmp_path: Path,
    executable: Path,
    base_url: str,
    host_key: str,
    *,
    fault_point: str | None = None,
) -> WorkflowHost:
    adapter = ClaudeAgentHost(
        claude_executable=executable, settings_path=tmp_path / "settings.json"
    )
    return WorkflowHost(
        tmp_path / "state",
        fault_point=fault_point,
        claude_agent_host=adapter,
        claude_model=_MODEL,
        provider_environment={
            "ANTHROPIC_API_KEY": host_key,
            "ANTHROPIC_BASE_URL": base_url,
        },
        compute_resources=LocalComputeResources(
            inventory_provider=lambda: ComputeCapabilities(gpus=())
        ),
        literature_io=FixedLiteratureIO(
            OpenAlexLiteratureFetcher(
                api_key="local-literature-fixture-key",
                transport=_CatalogTransport(),
                resolver=lambda _hostname: ("8.8.8.8",),
            )
        ),
    )


def _profile_environment_payload(
    tmp_path: Path, mount: Path, run_id: str
) -> tuple[dict[str, object], str]:
    """Admit a real installed dependency and data using ordinary Profile fields."""
    prefix = Path(sys.prefix).resolve()
    assert Path(sys.executable).resolve().is_relative_to(prefix)
    assert Path(yaml.__file__).resolve().is_relative_to(prefix)
    data_root = mount / "data"
    data_file = data_root / "probe.yaml"
    data_file.write_text("answer: 42\n", encoding="utf-8")
    unadmitted = mount / "unadmitted"
    unadmitted.mkdir()
    hidden_file = unadmitted / "private.txt"
    hidden_file.write_text("not part of any admitted read root\n", encoding="utf-8")
    profile = tmp_path / "profile"
    methodology_path = profile / "methodology.yaml"
    methodology = yaml.safe_load(methodology_path.read_text())
    methodology["runtime"]["conda_env"] = str(prefix)
    methodology_path.write_text(yaml.safe_dump(methodology, sort_keys=False))
    (profile / "safety.yaml").write_text(
        yaml.safe_dump({"allow_read_paths": [str(data_root)], "sandbox_os": "linux"})
    )
    admitted = admit_profile(profile, run_id=run_id)
    assert admitted.admitted, admitted.to_dict()
    assert admitted.create_run_payload is not None
    assert admitted.create_run_payload["execution"]["runtime_prefix"] == str(prefix)
    assert admitted.create_run_payload["execution"]["read_roots"] == [str(data_root)]
    probe = (
        "import errno, json, os, sys\nfrom pathlib import Path\nimport yaml\n"
        f"profile_data = Path({str(data_file)!r})\n"
        "assert yaml.safe_load(profile_data.read_text()) == {'answer': 42}\n"
        f"assert json.loads(os.environ['HYPOLOOM_READ_ROOTS']) == [{str(data_root)!r}]\n"
        "assert Path(yaml.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())\n"
        "assert Path(sys.executable).resolve().is_relative_to(Path(sys.prefix).resolve())\n"
        "assert os.statvfs(sys.prefix).f_flag & os.ST_RDONLY\n"
        f"assert not Path({str(hidden_file)!r}).exists()\n"
        # A running executable may reject open with ETXTBSY before checking
        # readonly mounts; inspect its runtime mount and write-test module/data.
        "for protected in (profile_data, Path(yaml.__file__)):\n"
        "    try:\n"
        "        fd = os.open(protected, os.O_WRONLY | os.O_APPEND)\n"
        "    except OSError as error:\n"
        "        assert error.errno == errno.EROFS, (protected, error)\n"
        "    else:\n"
        "        os.close(fd)\n"
        "        raise AssertionError('admitted asset writable: ' + str(protected))\n"
        f"print({_PROFILE_ENV_SUCCESS!r}, yaml.__version__, sys.prefix)\n"
    )
    return admitted.create_run_payload, probe


def _exercise_full_cc_host(
    tmp_path: Path, *, profile_environment: bool = False, timeout_seconds: float = 180
) -> None:
    # Fifteen native invocations plus tools exceeded the old 90s test-only
    # deadline on a shared server. Agent/run production budgets are unchanged.
    executable = _installed_claude()
    scenario = _current_host_scenario(tmp_path)
    host_key = "host-only-g3-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, host_key)
    settings = tmp_path / "settings.json"
    settings.write_text("{}\n")
    settings.chmod(0o600)
    host = _new_host(
        tmp_path, executable, f"http://127.0.0.1:{provider.server_port}", host_key
    )
    run_id = (
        "full-cc-profile-environment-g3" if profile_environment else "full-cc-host-g3"
    )
    try:
        with tempfile.TemporaryDirectory(
            prefix="hypoloom-full-cc-g3-", dir="/var/tmp"
        ) as mount:
            payload = _pilot_payload(tmp_path, Path(mount), run_id)
            if profile_environment:
                payload, scenario.environment_probe = _profile_environment_payload(
                    tmp_path, Path(mount), run_id
                )
            host.initialize()
            created = host.handle(
                ControlRequest(
                    schema_version=1,
                    request_id="create-g3",
                    expected_revision=0,
                    command="CREATE_RUN",
                    payload=payload,
                )
            )
            assert created.status == "accepted", created
            deadline = time.monotonic() + timeout_seconds
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
            outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
            assert [row["payload"]["outcome"] for row in outcomes] == [
                "EXECUTION_FAILURE",
                "SUCCESS",
            ]
            experiments = host.store.list_workflow_heads(run_id, "EXPERIMENT")
            assert len(experiments) == 2
            attempts = host.store.list_attempts()
            executions = [
                row
                for row in attempts
                if row["kind"] == "AGENT"
                and json.loads(row["observation_json"])["request"]["command"]
                == AgentCommand.EXPERIMENT_EXECUTION.value
            ]
            assert len(executions) == 1
            assert all(row["state"] == "CLOSED" for row in attempts)
            assert len([row for row in attempts if row["kind"] == "PROCESS"]) == 2
            contracts = [
                row["payload"]["execution_contract"]
                for row in host.store.list_facts(run_id, "ExperimentPreregistered")
            ]
            assert {item["source_agent_attempt_id"] for item in contracts} == {
                executions[0]["attempt_id"]
            }
            assert len({item["source_submit_command_id"] for item in contracts}) == 2
            assert not host.running and not host.running_agents
            assert all(
                not process_group_exists(row["process_group_id"])
                for row in attempts
                if row["process_group_id"] is not None
            )
            assert set(scenario.tool_evidence) == {
                "write_design",
                "test_failure",
                "repair_design",
                "test_success",
                "protected_mounts",
                "inject_process_failure",
                "submit_experiment",
                "wait_failure",
                "read_failure_log",
                "repair_execution",
                "execution_test_success",
                "submit_repair",
                "wait_repair",
            }
            observations = [
                json.loads(row["observation_json"])
                for row in attempts
                if row["kind"] == "AGENT"
            ]
            assert all(
                observation.get("session", {}).get("transcript")
                for observation in observations
            )
            assert host_key not in json.dumps(observations)
            for observation in observations:
                session = observation["session"]
                assert session["transcript_complete"] is True
                transcript = (
                    host.publisher.root
                    / session["transcript"]["path"]
                    / "transcript.jsonl"
                )
                assert host_key.encode() not in transcript.read_bytes()
            _assert_current_reports(scenario, attempts, host.publisher.root, tmp_path)
            report = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
            (tmp_path / "oracle.json").write_text(json.dumps(report.to_dict(), indent=2))
            assert report.ok, report.to_dict()
            assert report.open_attempt_count == report.pending_effect_count == 0
            assert report.canonical_outcome_count == 2
            assert report.artifact_counts["TRANSCRIPT"] == len(observations)
    finally:
        (tmp_path / "provider-errors.json").write_text(json.dumps(scenario.errors, indent=2))
        (tmp_path / "tool-evidence.json").write_text(json.dumps(scenario.tool_evidence, indent=2))
        host.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)


def _current_host_scenario(private_root: Path) -> _HostScenario:
    # The shared base remains the original EDI/local-repair, EXEC/repair and
    # committed-submit replay probe. Only mandatory round/final closure uses
    # the existing actual file-tool scenarios; no scientific step is replaced.
    # Import lazily because those helpers themselves reuse this module's base.
    from test_claude_final_report_g3 import _FinalReportHostScenario

    closure = {AgentCommand.CYCLE_REPORT, AgentCommand.SELF_EVOLVE, AgentCommand.FINAL_REPORT}

    class CurrentHostScenario(_FinalReportHostScenario):
        def __init__(self):
            super().__init__(private_root)
            self.reviser = FakeAgent()

        def record(self, document):
            if _request_from_provider(document).command in closure:
                return super().record(document)
            return _HostScenario.record(self, document)

        def tool_calls(self, number):
            if self.current is not None and self.current.command in closure:
                return super().tool_calls(number)
            return _HostScenario.tool_calls(self, number)

    return CurrentHostScenario()


def _assert_current_reports(scenario, attempts, artifacts: Path, evidence_root: Path) -> None:
    observations = [json.loads(row["observation_json"]) for row in attempts if row["kind"] == "AGENT"]
    assert all(row["request"]["schema_version"] == CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION for row in observations)
    for command in ("CYCLE_REPORT", "SELF_EVOLVE", "FINAL_REPORT"):
        matches = [row for row in attempts if row["kind"] == "AGENT"
                   and json.loads(row["observation_json"])["request"]["command"] == command]
        assert len(matches) == 1 and matches[0]["terminal_reason"] == "SUCCEEDED"
    assert {kind for _, kind in scenario.report_evidence} == {
        "report_skill", "report_read", "report_index", "report_round", "report_protection"}
    assert set(scenario.evolve_evidence) == {
        "self_evolve_skill", "read_plugin_update", "edit_plugin_update", "plugin_update_protection"}
    cycle = next(row for row in observations if row["request"]["command"] == "CYCLE_REPORT")
    cycle_archive = artifacts / cycle["session"]["transcript"]["path"] / "memory"
    for raw in cycle["response"]["proposed_workspace_artifacts"]:
        item = WorkspaceArtifact.from_dict(raw)
        assert (cycle_archive / item.path).read_bytes() == item.content_bytes()
    final = next(row for row in observations if row["request"]["command"] == "FINAL_REPORT")
    report = scenario.reports[final["request"]["attempt_id"]]
    assert len(scenario.reports) == 1 and len(report.requests) == 5
    assert set(report.evidence) == {"report_skill", "report_sources", "report_write", "report_boundaries"}
    files = final["response"]["proposed_workspace_artifacts"]
    assert len(files) == 1 and files[0]["path"] == "FINAL_REPORT.md"
    assert WorkspaceArtifact.from_dict(files[0]).content_bytes() == report.report.encode()
    assert (artifacts / final["session"]["transcript"]["path"] / "FINAL_REPORT.md").read_bytes() == report.report.encode()
    (evidence_root / "closure-native-evidence.json").write_text(json.dumps({
        "cycle_report": {f"{cycle}/{kind}": value for (cycle, kind), value in scenario.report_evidence.items()},
        "self_evolve": scenario.evolve_evidence, "final_report": report.evidence}, indent=2))


def test_full_cc_host_closes_real_cli_mcp_cpu_and_oracle(tmp_path: Path) -> None:
    _exercise_full_cc_host(tmp_path)


def test_full_cc_host_uses_profile_dependency_and_read_roots_through_cpu_oracle(
    tmp_path: Path,
) -> None:
    _exercise_full_cc_host(tmp_path, profile_environment=True)


def _subprocess_host_main() -> None:
    """Child-only Host; model server stays in the parent, without forked threads."""
    parameters = json.load(sys.stdin)
    host = _new_host(
        Path(parameters["root"]),
        Path(parameters["executable"]),
        parameters["base_url"],
        parameters["host_key"],
        fault_point=parameters["fault_point"],
    )
    signal.signal(signal.SIGTERM, lambda *_args: sys.exit(143))
    try:
        host.initialize()
        if parameters["payload"] is not None:
            created = host.handle(
                ControlRequest(
                    schema_version=1,
                    request_id="create-g3",
                    expected_revision=0,
                    command="CREATE_RUN",
                    payload=parameters["payload"],
                )
            )
            assert created.status == "accepted", created
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            host.tick()
            if host.store.latest_transition(parameters["run_id"])["state"] in {
                "COMPLETED",
                "FAILED",
                "CANCELLED",
            }:
                break
            time.sleep(0.01)
        assert (
            host.store.latest_transition(parameters["run_id"])["state"] == "COMPLETED"
        ), host.store.list_attempts()
        report = Oracle(host.store.database_path, host.publisher.root).audit(
            parameters["run_id"]
        )
        assert report.ok, report.to_dict()
    finally:
        host.close()


def _run_child(parameters: Mapping[str, Any], log_path: Path) -> int:
    script = (
        "import sys; sys.path.insert(0, " + repr(str(Path(__file__).parent)) + "); "
        "from test_full_cc_host_g3 import _subprocess_host_main; _subprocess_host_main()"
    )
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            (sys.executable, "-c", script),
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            process.communicate(json.dumps(parameters).encode(), timeout=100)
            return process.returncode
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def _rows(database: Path, query: str) -> list[dict[str, Any]]:
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(query)]


def test_full_cc_host_recovers_committed_submit_and_resumes_same_session(
    tmp_path: Path,
) -> None:
    executable = _installed_claude()
    scenario = _current_host_scenario(tmp_path)
    host_key = "host-only-recovery-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, host_key)
    settings = tmp_path / "settings.json"
    settings.write_text("{}\n")
    settings.chmod(0o600)
    run_id = "full-cc-host-recovery-g3"
    database = tmp_path / "state" / "state.sqlite"
    try:
        with tempfile.TemporaryDirectory(
            prefix="hypoloom-full-cc-recovery-", dir="/var/tmp"
        ) as mount:
            parameters = {
                "root": str(tmp_path),
                "executable": str(executable),
                "base_url": f"http://127.0.0.1:{provider.server_port}",
                "host_key": host_key,
                "run_id": run_id,
                "payload": _pilot_payload(tmp_path, Path(mount), run_id),
                "fault_point": "after_submit_commit_before_reply",
            }
            crashed = _run_child(parameters, tmp_path / "crashed-host.log")
            assert crashed == 86, (tmp_path / "crashed-host.log").read_text()
            assert not scenario.errors, scenario.errors
            # The submit transaction committed, but no response reached the CLI.
            creations = _rows(
                database,
                "SELECT * FROM workflow_transitions WHERE workflow_kind='EXPERIMENT' AND revision=1",
            )
            assert len(creations) == 1
            initial_experiment_id = creations[0]["workflow_id"]
            assert "submit_experiment" not in scenario.tool_evidence
            recovered = _run_child(
                {**parameters, "payload": None, "fault_point": None},
                tmp_path / "recovered-host.log",
            )
            assert recovered == 0, (tmp_path / "recovered-host.log").read_text()
            assert not scenario.errors, scenario.errors
            assert scenario.experiment_id == initial_experiment_id
            assert {
                "recover_list",
                "replay_submit",
                "wait_failure",
                "read_failure_log",
                "repair_execution",
                "execution_test_success",
                "submit_repair",
                "wait_repair",
            } <= scenario.tool_evidence.keys()
            attempts = _rows(
                database, "SELECT * FROM execution_attempts ORDER BY started_at"
            )
            assert all(row["state"] == "CLOSED" for row in attempts)
            executions = [
                row
                for row in attempts
                if row["attempt_id"] in scenario.execution_attempt_ids
            ]
            assert [row["terminal_reason"] for row in executions] == [
                "LOST",
                "SUCCEEDED",
            ]
            sessions = [
                json.loads(row["observation_json"])["session"] for row in executions
            ]
            assert sessions[0]["session_id"] == sessions[1]["session_id"]
            assert sessions[0]["archive_after_loss"] is True
            assert sessions[1]["launch_arguments"] == [
                "--resume",
                sessions[0]["session_id"],
            ]
            assert len([row for row in attempts if row["kind"] == "PROCESS"]) == 2
            assert all(
                not process_group_exists(row["process_group_id"])
                for row in attempts
                if row["process_group_id"] is not None
            )
            artifacts = tmp_path / "state" / "artifacts"
            for row in attempts:
                if row["kind"] != "AGENT":
                    continue
                observation = json.loads(row["observation_json"])
                transcript = (
                    artifacts
                    / observation["session"]["transcript"]["path"]
                    / "transcript.jsonl"
                )
                assert host_key.encode() not in transcript.read_bytes()
            report = Oracle(database, artifacts).audit(run_id)
            (tmp_path / "oracle.json").write_text(json.dumps(report.to_dict(), indent=2))
            assert report.ok, report.to_dict()
            assert report.canonical_outcome_count == 2
            assert report.artifact_counts["TRANSCRIPT"] == len([row for row in attempts if row["kind"] == "AGENT"])
            assert report.open_attempt_count == report.pending_effect_count == 0
            _assert_current_reports(scenario, attempts, artifacts, tmp_path)
    finally:
        (tmp_path / "provider-errors.json").write_text(json.dumps(scenario.errors, indent=2))
        (tmp_path / "tool-evidence.json").write_text(json.dumps(scenario.tool_evidence, indent=2))
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
