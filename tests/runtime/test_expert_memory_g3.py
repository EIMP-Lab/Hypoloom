"""Native expert Skill/report, Host restart and next-cycle memory on real CC."""

from __future__ import annotations

import json
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

from hypoloom.agent_aci import AgentRequest, AgentResponse
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeTask
from hypoloom.expert_memory import ExpertMemory
from hypoloom.oracle import Oracle

from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results
from test_expert_loop_host import _ReviseOnceAgent, _pilot_payload
from test_full_cc_host_g3 import (
    _HostScenario,
    _LOCAL_FAILURE,
    _LOCAL_SUCCESS,
    _new_host,
    _request_from_provider,
    _start_provider,
)


def _cycle(request: AgentRequest) -> int:
    return (
        request.task_context.key.cycle
        if isinstance(request.task_context, ResearchNodeTask)
        else 0
    )


_HOST_RESTART_WAIT = "until [ ! -d /workspace ]; do sleep 0.1; done"


def _resources(request: AgentRequest) -> dict[str, Any]:
    return json.loads(
        next(
            item
            for item in request.input_artifacts
            if item.ref.endswith("--expert-resources")
        ).content_bytes()
    )


class _MemoryScenario(_HostScenario):
    def __init__(self, private_root: Path, nonce: str) -> None:
        super().__init__(private_root)
        self.nonce = nonce
        self.reviser = _ReviseOnceAgent()
        self.report_attempts: list[str] = []
        self.consumers: set[str] = set()
        self.report_evidence: dict[tuple[int, str], str] = {}
        self.restart_ready = False
        self.restart_completed = False

    def record(self, document: Mapping[str, Any]) -> int:
        request = _request_from_provider(document)
        custom = request.command in {
            AgentCommand.EXPERIMENT_EXECUTION,
            AgentCommand.RESULT_ANALYSIS,
            AgentCommand.CYCLE_REPORT,
        } or (
            request.command is AgentCommand.LITERATURE_REVIEW and _cycle(request) == 2
        )
        if custom:
            self.requests.append(document)
            self.current = request
            self.agent_requests[request.attempt_id] = request
            self.steps[request.attempt_id] += 1
        else:
            super().record(document)
        try:
            response = self.reviser.invoke(request)
            assert isinstance(response, AgentResponse)
            self.responses[request.attempt_id] = response
            if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
                self.design_candidate = response.typed_candidate
            step = self.steps[request.attempt_id]
            results = _tool_results(document)
            if request.command is AgentCommand.EXPERIMENT_EXECUTION:
                if step == 2:
                    result = results["memory_submit"]
                    assert result.get("is_error") is not True, result
                    self.experiment_id = json.loads(_result_text(result))["exp_id"]
                elif step == 3:
                    result = results["memory_wait"]
                    assert result.get("is_error") is not True, result
                    waited = json.loads(_result_text(result))
                    expected = "CLEAN_REFUTATION" if _cycle(request) == 1 else "SUCCESS"
                    assert (
                        waited["terminal"] and waited["outcome"]["outcome"] == expected
                    ), waited
            elif request.command is AgentCommand.RESULT_ANALYSIS:
                messages = json.dumps(document["messages"])
                if request.schema_version < 24:
                    assert _LOCAL_FAILURE in messages and _LOCAL_SUCCESS in messages
                    assert "mcp__host-tools__submit_experiment" in messages
                else:
                    if step == 1:
                        assert not any(message["role"] == "assistant" for message in document["messages"])
                    assert any("spec" in item.ref for item in request.input_artifacts)
                    assert any("outcome" in item.ref for item in request.input_artifacts)
            elif request.command is AgentCommand.CYCLE_REPORT:
                if request.attempt_id not in self.report_attempts:
                    self.report_attempts.append(request.attempt_id)
                previous = {
                    2: "report_skill",
                    3: "report_read",
                    4: "report_index",
                    5: "report_round",
                    6: "report_protection",
                }.get(step)
                if previous:
                    result = results[previous]
                    assert result.get("is_error") is not True, result
                    self.report_evidence[(_cycle(request), previous)] = _result_text(
                        result
                    )
                if step == 2:
                    # Native Skill delivers the body, without frontmatter metadata.
                    delivered = json.dumps(document)
                    assert "Read this request's accepted outcomes, exact assessments, experiment logs" in delivered
                    assert "as a compact index and rounds/<cycle>.md as the detailed report" in delivered
                    # Skill's tool result acknowledges launch; its body is a
                    # separate message in the provider request.
                    assert json.dumps('row["payload"]["assessment"]')[1:-1] in delivered
                    assert "Do not copy whole fact records" in delivered
            elif (
                request.command is AgentCommand.LITERATURE_REVIEW
                and _cycle(request) == 2
            ):
                previous = {
                    2: "read_prior_index",
                    3: "read_prior_round",
                    4: "memory_protection",
                }.get(step)
                if previous:
                    result = results[previous]
                    assert result.get("is_error") is not True, result
                    if step in {2, 3}:
                        assert self.nonce in _result_text(result), result
                    else:
                        assert "MEMORY_AND_PLUGIN_READONLY" in _result_text(result), (
                            result
                        )
                        self.consumers.add(request.attempt_id)
        except Exception as exc:
            self.errors.append(
                f"provider {len(self.requests)}: {type(exc).__name__}: {exc}"
            )
        return len(self.requests)

    def tool_calls(self, number: int) -> tuple[tuple[str, str, Mapping[str, Any]], ...]:
        request = self.current
        if request is None or self.errors:
            return ()
        step = self.steps[request.attempt_id]
        if request.command is AgentCommand.EXPERIMENT_EXECUTION:
            if step == 1:
                assert self.design_candidate is not None
                return (
                    (
                        "memory_submit",
                        "mcp__host-tools__submit_experiment",
                        {"candidate": self.design_candidate},
                    ),
                )
            if step == 2:
                return (
                    (
                        "memory_wait",
                        "mcp__host-tools__wait_experiment",
                        {"exp_id": self.experiment_id, "max_wait_seconds": 10},
                    ),
                )
            return self._structured(request)
        if request.command is AgentCommand.CYCLE_REPORT:
            if step == 1:
                name = _resources(request)["plugin"]["name"]
                return (
                    ("report_skill", "Skill", {"skill": name + ":experiment-report"}),
                )
            if step == 2:
                return (("report_read", "Read", {"file_path": "/memory/MEMORY.md"}),)
            if step in {3, 4}:
                item = self.responses[request.attempt_id].proposed_workspace_artifacts[
                    step - 3
                ]
                body = (
                    item.content_bytes().decode()
                    + f"\nObserved report lesson: {self.nonce}.\n"
                )
                return (
                    (
                        "report_index" if step == 3 else "report_round",
                        "Write",
                        {"file_path": "/memory/" + item.path, "content": body},
                    ),
                )
            if step == 5:
                return self._protection(request, writable_memory=True)
            return self._structured(request)
        if request.command is AgentCommand.LITERATURE_REVIEW and _cycle(request) == 2:
            if step == 2 and not self.restart_completed:
                # The first Read is already in the real CC transcript. The wait
                # must remain pending until the Host restart interrupts this call.
                self.restart_ready = True
                return (
                    (
                        "before_host_restart",
                        "Bash",
                        {"command": _HOST_RESTART_WAIT, "timeout": 35000},
                    ),
                )
            if step in {1, 2}:
                return (
                    (
                        "read_prior_index" if step == 1 else "read_prior_round",
                        "Read",
                        {
                            "file_path": "/memory/"
                            + ("MEMORY.md" if step == 1 else "rounds/1.md")
                        },
                    ),
                )
            if step == 3:
                return self._protection(request, writable_memory=False)
            return self._structured(request)
        return super().tool_calls(number)

    def _structured(
        self, request: AgentRequest
    ) -> tuple[tuple[str, str, Mapping[str, Any]], ...]:
        response = self.responses[request.attempt_id]
        candidate = dict(response.typed_candidate or {})
        if "query" in candidate:
            output = {
                "typed_candidate": {
                    key: value
                    for key, value in candidate.items()
                    if key in {"query", "preferred_venues"}
                }
            }
        else:
            output = {
                "typed_candidate": candidate,
                "declared_effects": [
                    {"effect": item.effect} for item in response.declared_effects
                ],
            }
        return (("structured_" + request.attempt_id, "StructuredOutput", output),)

    def _protection(
        self, request: AgentRequest, *, writable_memory: bool
    ) -> tuple[tuple[str, str, Mapping[str, Any]], ...]:
        targets = [
            "/expert-plugin/.claude-plugin/plugin.json",
            "/research/background.md",
        ]
        if writable_memory:
            targets.append("/memory/.git")
        targets.append("/workspace/.git" if writable_memory else "/memory/MEMORY.md")
        script = (
            "import errno, json, os\nfrom pathlib import Path\n"
            "assert os.statvfs('/workspace').f_flag & os.ST_RDONLY\n"
            f"assert json.loads(Path('/expert-plugin/.claude-plugin/plugin.json').read_text())['name'] == {_resources(request)['plugin']['name']!r}\n"
            f"for target in {targets!r}:\n"
            "    try:\n"
            "        fd = os.open(target, os.O_WRONLY | os.O_APPEND)\n"
            "    except OSError as error:\n"
            "        assert error.errno == errno.EROFS, (target, error)\n"
            "    else:\n"
            "        os.close(fd)\n"
            "        raise AssertionError('protected file writable: ' + target)\n"
            f"assert not Path({str(self.private_root)!r}).exists()\n"
            f"assert {{p.name for p in Path('/research/experts').iterdir()}} == {{{request.role!r}}}\n"
            "assert not Path('/state').exists()\n"
            "assert not Path('/other-expert-plugin').exists()\n"
            "print('MEMORY_AND_PLUGIN_READONLY')\n"
        )
        return (
            (
                "report_protection" if writable_memory else "memory_protection",
                "Bash",
                {"command": "python3 - <<'PY'\n" + script + "PY", "timeout": 10000},
            ),
        )


class _FinalReportMemoryScenario(_MemoryScenario):
    """Complete this standalone memory test, without changing shared base owners."""

    def __init__(self, private_root: Path, nonce: str) -> None:
        super().__init__(private_root, nonce)
        self.final_reports: dict[str, Any] = {}

    def record(self, document):
        number = super().record(document)
        request = self.current
        if request is not None and request.command is AgentCommand.FINAL_REPORT:
            from test_claude_final_report_cli import _FinalReportScenario

            if request.attempt_id not in self.final_reports:
                report = _FinalReportScenario(request)
                report.tool_prefix = "final_" + request.attempt_id + "__"
                self.final_reports[request.attempt_id] = report
            report = self.final_reports[request.attempt_id]
            response = self.responses[request.attempt_id]
            report.structured_output = {"typed_candidate": response.typed_candidate,
                "declared_effects": [{"effect": item.effect} for item in response.declared_effects]}
            report.record(document)
            self.errors.extend(report.errors)
        return number

    def tool_calls(self, number):
        if self.current is not None and self.current.command is AgentCommand.FINAL_REPORT:
            report = self.final_reports[self.current.attempt_id]
            return tuple((report.tool_prefix + identity, name, arguments)
                for identity, name, arguments in report.tool_calls(number))
        return super().tool_calls(number)


def test_memory_fixture_final_report_uses_existing_native_file_tool_scenario(tmp_path, monkeypatch):
    from test_final_report import report_request

    request = report_request()
    monkeypatch.setattr(__name__ + "._request_from_provider", lambda _: request)

    def record(self, document):
        self.requests.append(document)
        self.current = request
        self.agent_requests[request.attempt_id] = request
        self.steps[request.attempt_id] += 1
        return len(self.requests)

    monkeypatch.setattr(_HostScenario, "record", record)
    scenario = _FinalReportMemoryScenario(tmp_path, "offline-memory-nonce")
    scenario.record({"messages": []})
    calls = scenario.tool_calls(1)
    assert len(calls) == 1 and calls[0][1] == "Skill"
    assert calls[0][2] == {"skill": "hypoloom-grounding:final-report"}
    assert len(scenario.final_reports) == 1
    assert scenario.final_reports[request.attempt_id].stage_step == 1
    result = "The report is a narrative artifact derived from accepted records"
    scenario.record({"messages": [{"role": "user", "content": [{"type": "tool_result",
        "tool_use_id": calls[0][0], "content": result}]}]})
    assert not scenario.errors, scenario.errors
    report = scenario.final_reports[request.attempt_id]
    assert len(report.requests) == report.stage_step == 2
    assert report.evidence == {"report_skill": result}


def test_shared_final_report_scenario_has_one_owner_after_memory_inheritance(tmp_path):
    from test_claude_final_report_g3 import _FinalReportHostScenario
    from test_final_report import report_request

    request = report_request()
    metadata = request.to_dict()
    metadata.pop("input_artifacts")
    prompt = {"agent_request_metadata": metadata, "rendered_inputs": [
        {"ref": item.ref, "media_type": item.media_type, "content_utf8": item.content_bytes().decode()}
        for item in request.input_artifacts]}
    document = {"messages": [{"role": "user", "content": [
        {"type": "text", "text": json.dumps(prompt, separators=(",", ":"))}]}]}
    scenario = _FinalReportHostScenario(tmp_path)
    scenario.record(document)
    calls = scenario.tool_calls(1)
    assert len(calls) == 1 and calls[0][1] == "Skill"
    identity, name, arguments = calls[0]
    assert identity == "probe_" + request.attempt_id[-16:] + "__report_skill"
    result = "The report is a narrative artifact derived from accepted records"
    document = {"messages": [*document["messages"],
        {"role": "assistant", "content": [{"type": "tool_use", "id": identity,
            "name": name, "input": arguments}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": identity,
            "content": result}]}]}
    scenario.record(document)
    assert not scenario.errors, scenario.errors
    assert not hasattr(scenario, "final_reports")
    assert len(scenario.reports) == 1
    report = scenario.reports[request.attempt_id]
    assert len(report.requests) == report.stage_step == 2
    assert report.evidence == {"report_skill": result}


def test_native_plugin_report_survives_host_restart_and_next_cycle_consumes_memory(
    tmp_path: Path,
) -> None:
    executable = _installed_claude()
    nonce = "prior-cycle-lesson-" + secrets.token_hex(12)
    scenario = _FinalReportMemoryScenario(tmp_path, nonce)
    host_key = "host-only-memory-g3-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, host_key)
    base_url = f"http://127.0.0.1:{provider.server_port}"
    (tmp_path / "settings.json").write_text("{}\n")
    (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(tmp_path, executable, base_url, host_key)
    run_id = "native-expert-memory-g3"
    restarted = False
    first_memory_commit = None
    try:
        with tempfile.TemporaryDirectory(
            prefix="hypoloom-expert-memory-g3-", dir="/var/tmp"
        ) as mount:
            payload = _pilot_payload(tmp_path, Path(mount), run_id)
            host.initialize()
            created = host.handle(
                ControlRequest(
                    schema_version=1,
                    request_id="create-memory-g3",
                    expected_revision=0,
                    command="CREATE_RUN",
                    payload=payload,
                )
            )
            assert created.status == "accepted", created
            # Include both reports and SELF_EVOLVE closures after native restart.
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                host.tick()
                assert not scenario.errors, scenario.errors
                reports = [
                    row
                    for row in host.store.list_attempts()
                    if row["terminal_reason"] == "SUCCEEDED"
                    and row["kind"] == "AGENT"
                    and json.loads(row["observation_json"])["request"]["command"]
                    == "CYCLE_REPORT"
                ]
                if reports and scenario.restart_ready and not restarted:
                    assert not host.running
                    assert all(
                        handle.request.command is AgentCommand.LITERATURE_REVIEW
                        and _cycle(handle.request) == 2
                        for handle in host.running_agents.values()
                    )
                    first_memory_commit = json.loads(reports[0]["observation_json"])[
                        "memory"
                    ]["commit"]
                    host.close()
                    scenario.restart_completed = True
                    host = _new_host(tmp_path, executable, base_url, host_key)
                    host.initialize()
                    restarted = True
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
            assert restarted and first_memory_commit is not None
            assert len(scenario.report_attempts) == 2 and scenario.consumers
            assert len(scenario.report_evidence) == 10
            assert {
                (
                    _cycle(scenario.agent_requests[item]),
                    _resources(scenario.agent_requests[item])["memory_commit"],
                )
                for item in scenario.consumers
            } == {(2, first_memory_commit)}
            report_requests = [
                scenario.agent_requests[item] for item in scenario.report_attempts
            ]
            assert [_cycle(request) for request in report_requests] == [1, 2]
            assert (
                len(
                    {
                        _resources(request)["plugin"]["commit"]
                        for request in report_requests
                    }
                )
                == 1
            )
            outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
            assert [row["payload"]["outcome"] for row in outcomes] == [
                "CLEAN_REFUTATION",
                "SUCCESS",
            ]
            assessed = host.store.list_facts(run_id, "HypothesisAssessed")
            assert [row["payload"]["assessment"] for row in assessed] == [
                "REFUTED",
                "SUPPORTED",
            ]
            role = report_requests[0].role
            memory = ExpertMemory(host.state_root)
            files = memory.files(
                run_id, role, memory.ensure(run_id, role), max_bytes=131072
            )
            assert set(files) == {"MEMORY.md", "rounds/1.md", "rounds/2.md"}
            assert all(nonce.encode() in content for content in files.values())
            attempts = host.store.list_attempts()
            assert all(row["state"] == "CLOSED" for row in attempts)
            lost = [row for row in attempts if row["terminal_reason"] == "LOST"]
            assert len(lost) == 1
            replayed = next(
                row
                for row in attempts
                if row["command_id"] == lost[0]["command_id"]
                and row["terminal_reason"] == "SUCCEEDED"
            )
            original = json.loads(lost[0]["observation_json"])
            replay = json.loads(replayed["observation_json"])
            assert original["session_binding"]["session_id"] != replay["session_binding"]["session_id"]
            assert original["session_binding"]["branch"] == replay["session_binding"]["branch"]
            assert original["session_binding"]["scope"] == replay["session_binding"]["scope"]
            assert replay["request"]["attempt_ordinal"] == 2
            assert replay["session"]["launch_arguments"] == ["--session-id", replay["session"]["session_id"]]
            assert _resources(
                AgentRequest.from_dict(original["request"])
            ) == _resources(AgentRequest.from_dict(replay["request"]))
            assert not host.running and not host.running_agents
            assert all(
                not process_group_exists(row["process_group_id"])
                for row in attempts
                if row["process_group_id"] is not None
            )
            observations = [
                json.loads(row["observation_json"])
                for row in attempts
                if row["kind"] == "AGENT"
            ]
            finals = [row for row in observations if row["request"]["command"] == "FINAL_REPORT"]
            assert len(finals) == len(scenario.final_reports) == 1
            final = finals[0]
            report_scenario = scenario.final_reports[final["request"]["attempt_id"]]
            assert len(report_scenario.requests) == 5 and len(report_scenario.evidence) == 4
            assert final["response"]["status"] == "SUCCEEDED"
            actual_report = AgentResponse.from_dict(final["response"]).proposed_workspace_artifacts
            assert len(actual_report) == 1 and actual_report[0].path == "FINAL_REPORT.md"
            assert actual_report[0].content_bytes() == report_scenario.report.encode()
            final_archive = host.publisher.root / final["session"]["transcript"]["path"]
            assert (final_archive / "FINAL_REPORT.md").read_bytes() == report_scenario.report.encode()
            assert all(row["payload"]["outcome_id"] in report_scenario.report for row in outcomes)
            (tmp_path / "final-report-evidence.json").write_text(json.dumps(report_scenario.evidence, indent=2))
            archived_count = 0
            for observation in observations:
                session = observation.get("session", {})
                if "response" in observation:
                    assert (
                        session.get("transcript")
                        and session["transcript_complete"] is True
                    )
                if not session.get("transcript"):
                    continue
                archived_count += 1
                transcript = (
                    host.publisher.root
                    / session["transcript"]["path"]
                    / "transcript.jsonl"
                )
                assert host_key.encode() not in transcript.read_bytes()
                if observation["request"]["command"] == "CYCLE_REPORT":
                    cycle = observation["request"]["task_context"]["key"]["cycle"]
                    archive_root = transcript.parent
                    accepted_files = memory.files(
                        run_id, role, observation["memory"]["commit"]
                    )
                    for relative in ("MEMORY.md", f"rounds/{cycle}.md"):
                        assert (
                            archive_root / "memory" / relative
                        ).read_bytes() == accepted_files[relative]
                    assert set(session["transcript"]["relative_paths"]) == {
                        "transcript.jsonl",
                        "memory/MEMORY.md",
                        f"memory/rounds/{cycle}.md",
                    }
            assert not host.projection_errors
            projection = host.state_root / run_id / "projection"
            assert (projection / "events.jsonl").is_file()
            assert (projection / f"experts/{role}/rounds/2/cycle_report.md").is_file()
            report = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
            assert report.ok, report.to_dict()
            assert report.canonical_outcome_count == 2
            assert report.artifact_counts["TRANSCRIPT"] == archived_count
            assert report.open_attempt_count == report.pending_effect_count == 0
    finally:
        host.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
