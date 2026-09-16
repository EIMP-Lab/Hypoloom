"""Two Grounding questions, durable answers and resumed native CPU research.

Only model choices and literature are local fixtures. The actual Claude CLI,
bubblewrap, Host, session archives, parser, CPU process and Oracle are exercised.
"""

from __future__ import annotations

import copy
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

from hypoloom.agent_aci import CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest, receive_message, socket_address
from hypoloom.domain import AgentCommand
from hypoloom.oracle import Oracle
from hypoloom.workflow_host import _send_control_response

from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results
from test_claude_grounding_g3 import _expert_literature
from test_claude_measurement_g3 import (
    _DeclaredScenario,
    _NoPublicWeb,
    _six_yaml_profile_v4_payload,
)
from test_full_cc_host_g3 import _new_host, _request_from_provider, _start_provider
from test_native_shared_goal_stop_g3 import _read_json


_RUN = "native-grounding-dialogue-g3"
_QUESTIONS = (
    "Which supplied evaluation split should be used?",
    "How should the confirmed repeated measurements be aggregated?",
)
_RATIONALES = (
    "Changing the split would change the scientific comparison.",
    "The aggregation must be consistent across expert experiments.",
)
_OPTIONS = (("Official held-out split", "Documented development split"),
            ("Mean over the confirmed repetitions", "Median over the confirmed repetitions"))


class _DialogueScenario(_DeclaredScenario):
    def __init__(self, root: Path, nonce: str):
        super().__init__(root, nonce)
        self.answers = (
            "Use the official held-out split and its documented exclusions: " + nonce,
            "Use the declared fixture measurement; retain all raw repetitions: " + nonce,
        )
        self.dialogues: dict[int, dict[str, Any]] = {}
        self.decision_ids: list[str] = []
        self.grounding_reads: dict[int, dict[str, Any]] = {}
        self.background_consumers: set[str] = set()

    def record(self, document: Mapping[str, Any]) -> int:
        request = _request_from_provider(document)
        if request.command is not AgentCommand.GROUNDING:
            number = super().record(document)
            if _expert_literature(request):
                try:
                    frozen = [json.loads(item.content_bytes()) for item in request.input_artifacts
                              if item.ref.endswith("--response")]
                    completed = [item["typed_candidate"] for item in frozen
                                 if item.get("typed_candidate", {}).get("task_kind") == "GROUNDING"]
                    assert len(completed) == 1 and completed[0]["clarification"] is None
                    assert all(answer in completed[0]["background"]["project_summary"]
                               for answer in self.answers)
                    if self.steps[request.attempt_id] == 2:
                        result = _tool_results(document)["read_dialogue_background"]
                        assert not result.get("is_error"), result
                        rendered = _result_text(result)
                        assert all(answer in rendered for answer in self.answers)
                        assert all(question not in rendered for question in _QUESTIONS)
                        self.background_consumers.add(request.attempt_id)
                except Exception as exc:
                    self.errors.append(f"background consumer {number}: {type(exc).__name__}: {exc}")
            return number

        self.requests.append(document)
        self.current = request
        self.agent_requests[request.attempt_id] = request
        self.steps[request.attempt_id] += 1
        try:
            assert request.schema_version == 20
            ordinal = request.task_context.ordinal
            assert ordinal in {1, 2, 3}
            inputs = [item for item in request.input_artifacts
                      if item.ref.endswith("--grounding-dialogue")]
            assert len(inputs) == 1
            dialogue = json.loads(inputs[0].content_bytes())
            assert dialogue == {"run_id": _RUN, "rounds": [
                {"decision_id": self.decision_ids[index], "ordinal": index + 1,
                 "question": _QUESTIONS[index], "rationale": _RATIONALES[index],
                 "options": list(_OPTIONS[index]), "answer": self.answers[index]}
                for index in range(ordinal - 1)]}
            previous = self.dialogues.setdefault(ordinal, dialogue)
            assert dialogue == previous, "a Grounding round changed its frozen dialogue inputs"
            results = _tool_results(document)
            step = self.steps[request.attempt_id]
            if step == 2:
                result = results[f"dialogue_{ordinal}_read"]
                assert not result.get("is_error"), result
                assert _read_json(_result_text(result)) == dialogue
                self.grounding_reads[ordinal] = dialogue
            if step == 3:
                result = results[f"dialogue_{ordinal}_target"]
                assert not result.get("is_error"), result
                assert "# profile-v3 target" in _result_text(result)
        except Exception as exc:
            self.errors.append(f"dialogue provider {len(self.requests)}: {type(exc).__name__}: {exc}")
        return len(self.requests)

    def tool_calls(self, number: int):
        request = self.current
        if request is None or self.errors:
            return ()
        step = self.steps[request.attempt_id]
        if request.command is AgentCommand.GROUNDING:
            ordinal = request.task_context.ordinal
            if step == 1:
                index = next(index for index, item in enumerate(request.input_artifacts)
                             if item.ref.endswith("--grounding-dialogue"))
                return ((f"dialogue_{ordinal}_read", "Read", {"file_path": f"/inputs/{index:04d}.txt"}),)
            if step == 2:
                return ((f"dialogue_{ordinal}_target", "Read", {"file_path": "/workspace/train.py"}),)
            background = None if ordinal < 3 else {
                "project_summary": "Confirmed user scope. " + " ".join(self.answers),
                "codebase_map": [{"path": "train.py", "purpose": "Observed example entrypoint"}],
                "entrypoints": ["train.py"],
                "how_to_run_experiment": "Declare and execute the fixture CPU experiment with its parser.",
                "metrics_available": [], "risks": [], "open_questions": [],
            }
            clarification = ({"question": _QUESTIONS[ordinal - 1],
                              "rationale": _RATIONALES[ordinal - 1],
                              "options": list(_OPTIONS[ordinal - 1])} if ordinal < 3 else None)
            candidate = {"task_kind": "GROUNDING", "supporting_refs": list(request.immutable_context_refs),
                         "background": background, "web_sources": [], "clarification": clarification, "acceptance": None}
            return (("structured_" + request.attempt_id, "StructuredOutput", {
                "typed_candidate": candidate,
                "declared_effects": [{"effect": effect} for effect in request.effect_requirements],
            }),)
        if _expert_literature(request):
            if step == 1:
                return (("read_dialogue_background", "Read", {"file_path": "/research/background.md"}),)
            return self._structured(request)
        return super().tool_calls(number)


def _tick_until(host, scenario, state: str) -> None:
    deadline = time.monotonic() + 110
    while time.monotonic() < deadline:
        host.tick()
        assert not scenario.errors, scenario.errors
        current = host.store.latest_transition(_RUN)["state"]
        if current == state:
            return
        assert current not in {"COMPLETED", "FAILED", "CANCELLED"}, host.store.list_attempts()
        time.sleep(0.01)
    raise AssertionError(f"Host did not reach {state}: {host.store.list_attempts()}")


def _waiting(host, scenario, ordinal: int) -> dict[str, Any]:
    assert host.store.latest_transition(_RUN)["state"] == "WAITING_HUMAN"
    attempts = host.store.list_attempts()
    assert attempts and all(row["state"] == "CLOSED" for row in attempts)
    assert all(row["kind"] == "AGENT" for row in attempts)
    assert not host.running_agents and not host.running
    assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"])
    requests = [json.loads(row["observation_json"])["request"] for row in attempts]
    assert all(item["task_context"]["kind"] == "AUXILIARY" for item in requests)
    grounding = [item for item in requests if item["command"] == "GROUNDING"]
    assert [item["task_context"]["ordinal"] for item in grounding] == list(range(1, ordinal + 1))
    decisions = host.store.inspect("SELECT * FROM workflow_human_decisions WHERE run_id=? AND state='OPEN'", (_RUN,))
    assert len(decisions) == 1 and decisions[0]["family"] == "GOAL_SCOPE"
    decision = dict(decisions[0])
    assert json.loads(decision["request_json"]) == {
        "kind": "grounding_clarification", "ordinal": ordinal,
        "source_attempt_id": grounding[-1]["attempt_id"],
        "source_command_id": grounding[-1]["command_id"],
        "question": _QUESTIONS[ordinal - 1], "rationale": _RATIONALES[ordinal - 1],
        "options": list(_OPTIONS[ordinal - 1]),
    }
    before = len(scenario.requests), len(attempts), host.store.latest_transition(_RUN)["revision"]
    for _ in range(4):
        host.tick()
    assert before == (len(scenario.requests), len(host.store.list_attempts()),
                      host.store.latest_transition(_RUN)["revision"])
    report = Oracle(host.store.database_path, host.publisher.root).audit(_RUN)
    assert report.ok, report.to_dict()
    return decision


def _answer_via_cli(host, decision_id: str, answer: str, ordinal: int) -> dict[str, Any]:
    """Use the actual CLI/frame codec and Host handler on one local socket."""
    expected = ControlRequest(schema_version=1, request_id=f"answer-dialogue-{ordinal}",
        expected_revision=host.store.latest_transition(_RUN)["revision"], command="ANSWER",
        payload={"run_id": _RUN, "decision_id": decision_id, "answer": answer})
    path = host.state_root / "control.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        address, directory_fd = socket_address(path)
        try:
            server.bind(address)
        finally:
            if directory_fd is not None:
                os.close(directory_fd)
        path.chmod(0o600)
        server.listen(1)
        server.settimeout(15)

        process = subprocess.Popen([sys.executable, "-m", "hypoloom.cli", "answer",
            "--state-root", str(host.state_root), "--run-id", _RUN,
            "--decision-id", decision_id, "--answer", answer,
            "--request-id", expected.request_id,
            "--expected-revision", str(expected.expected_revision)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            peer, _ = server.accept()
            with peer:
                request = ControlRequest.from_mapping(receive_message(peer))
                assert request == expected
                response = host.handle(request)
                _send_control_response(peer, response)
            stdout, stderr = process.communicate(timeout=15)
            assert process.returncode == 0, (stdout, stderr)
            assert json.loads(stdout) == response.to_mapping()
            return response.to_mapping()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            path.unlink(missing_ok=True)


def test_native_grounding_two_questions_restart_answers_and_cpu_closure(tmp_path: Path):
    executable = _installed_claude()
    scenario = _DialogueScenario(tmp_path, "grounding-dialogue-" + secrets.token_hex(12))
    host_key = "local-dialogue-provider-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, host_key)
    settings = tmp_path / "settings.json"
    settings.write_text("{}\n", encoding="utf-8")
    settings.chmod(0o600)

    def new_host():
        selected = _new_host(tmp_path, executable, f"http://127.0.0.1:{provider.server_port}", host_key)
        selected.grounding_web = _NoPublicWeb()
        selected.initialize()
        return selected

    host = None
    try:
        with tempfile.TemporaryDirectory(prefix="hypoloom-grounding-dialogue-", dir="/var/tmp") as mount:
            payload = _six_yaml_profile_v4_payload(tmp_path, Path(mount), _RUN)
            unchanged = copy.deepcopy(payload)
            host = new_host()
            created = host.handle(ControlRequest(schema_version=1, request_id="create-dialogue-g3",
                expected_revision=0, command="CREATE_RUN", payload=payload))
            assert created.status == "accepted", created
            assert int(host.store.get_run(_RUN)["agent_aci_version"]) == CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION
            decisions = []
            for ordinal, answer in enumerate(scenario.answers, 1):
                _tick_until(host, scenario, "WAITING_HUMAN")
                decision = _waiting(host, scenario, ordinal)
                decisions.append(decision)
                scenario.decision_ids.append(decision["decision_id"])
                if ordinal == 1:
                    host.close()
                    host = new_host()
                    assert _waiting(host, scenario, ordinal) == decision
                response = _answer_via_cli(host, decision["decision_id"], answer, ordinal)
                assert response["status"] == "accepted", response
                assert host.store.latest_transition(_RUN)["state"] == "ACTIVE"
                if ordinal == 2:
                    host.close()
                    host = new_host()
                    assert host.store.latest_transition(_RUN)["state"] == "ACTIVE"
            _tick_until(host, scenario, "COMPLETED")
            assert payload == unchanged
            assert not scenario.errors, scenario.errors
            assert set(scenario.grounding_reads) == {1, 2, 3}
            for ordinal in (1, 2, 3):
                rendered = json.dumps(scenario.dialogues[ordinal], ensure_ascii=False)
                assert all(answer in rendered for answer in scenario.answers[:ordinal - 1])
                assert all(answer not in rendered for answer in scenario.answers[ordinal - 1:])
                assert all(question in rendered for question in _QUESTIONS[:ordinal - 1])
            assert len(scenario.background_consumers) == 2
            attempts = host.store.list_attempts()
            assert all(row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED" for row in attempts)
            processes = [row for row in attempts if row["kind"] == "PROCESS"]
            assert len(processes) == 1
            outcomes = host.store.list_facts(_RUN, "CanonicalOutcomeAccepted")
            assert len(outcomes) == 1 and outcomes[0]["payload"]["outcome"] == "SUCCESS"
            observations = [json.loads(row["observation_json"]) for row in attempts if row["kind"] == "AGENT"]
            grounding = [item for item in observations if item["request"]["command"] == "GROUNDING"]
            assert len(grounding) == 3
            assert [item["request"]["task_context"]["ordinal"] for item in grounding] == [1, 2, 3]
            sessions = [item["session"] for item in grounding]
            assert len({item["session_id"] for item in sessions}) == 1
            assert all("--resume" in item["launch_arguments"] for item in sessions[1:])
            for ordinal, item in enumerate(grounding, 1):
                archive = host.publisher.root / item["session"]["transcript"]["path"] / "transcript.jsonl"
                content = archive.read_text(encoding="utf-8")
                assert item["session"]["session_id"] in content
                assert all(answer in content for answer in scenario.answers[:ordinal - 1])
                assert all(question in content for question in _QUESTIONS[:min(ordinal, 2)])
            resolved = host.store.inspect("SELECT * FROM workflow_human_decisions WHERE run_id=? ORDER BY request_revision", (_RUN,))
            assert len(resolved) == 2 and all(row["state"] == "RESOLVED" for row in resolved)
            assert all(answer in row["resolution_json"] for row, answer in zip(resolved, scenario.answers))
            report = Oracle(host.store.database_path, host.publisher.root).audit(_RUN)
            assert report.ok, report.to_dict()
            assert not host.running_agents and not host.running
            assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"])
            evidence = {"questions": decisions, "answers": scenario.answers,
                        "dialogue_inputs": scenario.dialogues, "sessions": sessions,
                        "background_consumers": sorted(scenario.background_consumers),
                        "cpu_attempt_ids": [row["attempt_id"] for row in processes], "oracle": report.to_dict()}
            (tmp_path / "grounding-dialogue-evidence.json").write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        if host is not None:
            host.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
