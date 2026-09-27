"""Real Agent MCP Grounding, frozen downstream context, and CPU/Oracle closure."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from hypoloom.agent_aci import CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import AuxiliaryRequestKind
from hypoloom.grounding_web import GroundingWeb

from test_claude_file_tool_cli import _result_text, _tool_results
from test_claude_measurement_g3 import _DeclaredScenario, _run_real_claude_measurement_g3
from test_full_cc_host_g3 import _request_from_provider
from test_grounding_web import DOC, SEARCH, Transport, _response


def _expert_literature(request: Any) -> bool:
    return (request.command is AgentCommand.LITERATURE_REVIEW
        and getattr(request.task_context, "kind", None) is not AuxiliaryRequestKind.GROUNDING_QUERY_PLAN)


class _GroundedMeasurementScenario(_DeclaredScenario):
    def __init__(self, root: Path, nonce: str):
        super().__init__(root, nonce)
        self.grounding_evidence: dict[str, str] = {}
        self.background_consumers: set[str] = set()
        self.transport: Transport | None = None

    def record(self, document: Mapping[str, Any]) -> int:
        number = super().record(document)
        request = _request_from_provider(document)
        try:
            step = self.steps[request.attempt_id]
            results = _tool_results(document)
            if request.command is AgentCommand.GROUNDING:
                names = {tool["name"] for tool in document["tools"]}
                assert {name for name in names if name.startswith("mcp__")} == {
                    "mcp__host-tools__web_search", "mcp__host-tools__web_fetch"}
                previous = {2: "inspect_target", 3: "ground_search", 4: "ground_fetch"}.get(step)
                if previous:
                    assert not results[previous].get("is_error"), results[previous]
                    text = _result_text(results[previous])
                    self.grounding_evidence[previous] = text
                    if previous == "inspect_target":
                        assert "# profile-v3 target" in text
                    elif previous == "ground_search":
                        assert DOC in text
                    else:
                        assert self.nonce in text and "python evaluate.py --fixed-split" in text
                if step == 1:
                    assert self.nonce not in json.dumps(document), "web source body was injected before retrieval"
                response = self.responses[request.attempt_id]
                self.responses[request.attempt_id] = replace(response, typed_candidate={
                    "task_kind": "GROUNDING", "supporting_refs": response.typed_candidate["supporting_refs"],
                    **({"clarification": None} if request.schema_version >= 15 else {}),
                    **({"acceptance": None} if request.schema_version >= 16 else {}),
                    "background": {"project_summary": "Observed train.py placeholder and retrieved guide: " + self.nonce,
                        "codebase_map": [{"path": "train.py", "purpose": "Observed placeholder target file"}],
                        "entrypoints": ["train.py"],
                        "how_to_run_experiment": "Documentation suggests python evaluate.py --fixed-split, but this checkout only contains placeholder train.py.",
                        "metrics_available": [], "risks": ["The retrieved page is untrusted external evidence."],
                        "open_questions": ["The expert must implement and declare an executable measurement."]},
                    "web_sources": [DOC]})
            elif _expert_literature(request):
                frozen = [json.loads(item.content_bytes()) for item in request.input_artifacts
                    if item.ref.endswith("--response") and item.media_type == "application/json"]
                background = next(item["typed_candidate"]["background"] for item in frozen
                    if item.get("typed_candidate", {}).get("task_kind") == "GROUNDING")
                assert self.nonce in background["project_summary"]
                assert background["entrypoints"] == ["train.py"]
                if step == 2:
                    result = results["read_published_background"]
                    assert not result.get("is_error"), result
                    rendered = _result_text(result)
                    assert self.nonce in rendered and "python evaluate.py --fixed-split" in rendered
                    assert DOC in rendered
                    self.background_consumers.add(request.attempt_id)
        except Exception as exc:
            self.errors.append(f"grounding provider {number}: {type(exc).__name__}: {exc}")
        return number

    def tool_calls(self, number: int):
        request = self.current
        if request is None or self.errors:
            return ()
        step = self.steps[request.attempt_id]
        if request.command is AgentCommand.GROUNDING:
            if step == 1:
                return (("inspect_target", "Read", {"file_path": "/workspace/train.py"}),)
            if step == 2:
                assert self.transport is not None
                self.transport.responses[DOC] = _response(DOC,
                    f"<title>Evaluation</title><p>python evaluate.py --fixed-split</p><p>{self.nonce}</p>".encode())
                return (("ground_search", "mcp__host-tools__web_search", {"query": "project evaluation"}),)
            if step == 3:
                return (("ground_fetch", "mcp__host-tools__web_fetch", {"url": DOC}),)
            return self._structured(request)
        if _expert_literature(request):
            if step == 1:
                return (("read_published_background", "Read", {"file_path": "/research/background.md"}),)
            return self._structured(request)
        return super().tool_calls(number)


def test_real_grounding_mcp_sources_reach_next_expert_and_cpu_oracle(tmp_path: Path):
    transport = Transport({
        SEARCH: _response(SEARCH, b'<a class="result__a" href="https://example.org/docs">Evaluation guide</a>')})
    def scenario_factory(root: Path, nonce: str) -> _GroundedMeasurementScenario:
        scenario = _GroundedMeasurementScenario(root, nonce)
        scenario.transport = transport
        return scenario
    scenario = _run_real_claude_measurement_g3(tmp_path, "six-yaml-v4",
        scenario_factory=scenario_factory, grounding_web=GroundingWeb(transport))
    assert set(scenario.grounding_evidence) == {"inspect_target", "ground_search", "ground_fetch"}
    assert len(scenario.background_consumers) == 2  # query-plan and literature MAIN
    assert [call[1] for call in transport.calls if call[0] == "fetch"] == [SEARCH, DOC]
    state = tmp_path / "state"
    with sqlite3.connect(f"file:{state}/state.sqlite?mode=ro", uri=True) as connection:
        observations = [json.loads(raw) for raw, in connection.execute(
            "SELECT observation_json FROM execution_attempts WHERE kind='AGENT'")]
    grounding = next(item for item in observations if item["request"]["command"] == "GROUNDING")
    assert grounding["response"]["typed_candidate"]["web_sources"] == [DOC]
    records = grounding["web_tool_results"]
    assert [(item["tool"], item["status"]) for item in records] == [
        ("web_search", "success"), ("web_fetch", "success")]
    assert "text" not in records[1]["result"]  # full bytes live in the existing transcript archive
    for item in observations:
        if item["request"]["command"] == "GROUNDING" or item["request"]["attempt_id"] in scenario.background_consumers:
            transcript = state / "artifacts" / item["session"]["transcript"]["path"] / "transcript.jsonl"
            assert scenario.nonce.encode() in transcript.read_bytes()
    (tmp_path / "scripted-web-calls.json").write_text(json.dumps(transport.calls, indent=2))


def test_native_source_error_is_corrected_in_same_attempt_before_cpu_closure(tmp_path: Path):
    class CorrectingScenario(_GroundedMeasurementScenario):
        def record(self, document):
            number = super().record(document)
            request = self.current
            if request.command is AgentCommand.GROUNDING:
                step = self.steps[request.attempt_id]
                if step == 4:
                    response = self.responses[request.attempt_id]
                    self.responses[request.attempt_id] = replace(response,
                        typed_candidate={**response.typed_candidate,
                            "web_sources": ["https://example.org/never-retrieved"]})
                elif step == 5:
                    messages = json.dumps(document["messages"])
                    assert "Stop hook feedback:" in messages and "retrieved" in messages
                assert step <= 5
            return number

    transport = Transport({SEARCH: _response(SEARCH,
        b'<a class="result__a" href="https://example.org/docs">Evaluation guide</a>')})

    def scenario_factory(root, nonce):
        scenario = CorrectingScenario(root, nonce)
        scenario.transport = transport
        return scenario

    scenario = _run_real_claude_measurement_g3(tmp_path, "six-yaml-v4",
        scenario_factory=scenario_factory, grounding_web=GroundingWeb(transport), correction_cap=3)
    with sqlite3.connect(f"file:{tmp_path}/state/state.sqlite?mode=ro", uri=True) as connection:
        observations = [json.loads(raw) for raw, in connection.execute(
            "SELECT observation_json FROM execution_attempts WHERE kind='AGENT'")]
    grounding, = [item for item in observations if item["request"]["command"] == "GROUNDING"]
    assert grounding["request"]["schema_version"] == CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION
    assert grounding["response"]["typed_candidate"]["web_sources"] == [DOC]
    trace = grounding["diagnostics"]["structured_output_correction"]
    assert trace["status"] == "recovered" and trace["accepted"]
    assert (trace["call_count"], trace["rejected_count"]) == (2, 1)
    assert scenario.steps[grounding["request"]["attempt_id"]] == 5


def test_native_tool_timeout_drops_late_source_before_cpu_oracle_closure(tmp_path: Path):
    slow_url = "https://example.org/late-undelivered"
    finished = threading.Event()

    class DelayedTransport(Transport):
        def fetch(self, url, **kwargs):
            if url == slow_url:
                # Real native MCP client uses its unchanged 30-second socket
                # timeout; this bounded injected read completes just after it.
                time.sleep(31)
                result = super().fetch(url, **kwargs)
                finished.set()
                return result
            return super().fetch(url, **kwargs)

    class TimeoutScenario(_GroundedMeasurementScenario):
        def __init__(self, root, nonce):
            super().__init__(root, nonce)
            self.timeout_result = None

        def record(self, document):
            number = super().record(document)
            if self.current.command is AgentCommand.GROUNDING and self.steps[self.current.attempt_id] == 5:
                result = _tool_results(document)["late_fetch"]
                self.timeout_result = result
                assert result.get("is_error"), result
                assert "timed out" in _result_text(result).lower(), result
                assert finished.wait(3), "late retrieval did not complete within its fixture bound"
            return number

        def tool_calls(self, number):
            if self.current.command is AgentCommand.GROUNDING and self.steps[self.current.attempt_id] == 4:
                return (("late_fetch", "mcp__host-tools__web_fetch", {"url": slow_url}),)
            return super().tool_calls(number)

    transport = DelayedTransport({
        SEARCH: _response(SEARCH, b'<a class="result__a" href="https://example.org/docs">Evaluation guide</a>'),
        slow_url: _response(slow_url, b"<p>NEVER_DELIVERED_SOURCE</p>"),
    })

    def scenario_factory(root, nonce):
        scenario = TimeoutScenario(root, nonce)
        scenario.transport = transport
        return scenario

    scenario = _run_real_claude_measurement_g3(tmp_path, "six-yaml-v4",
        scenario_factory=scenario_factory, grounding_web=GroundingWeb(transport, max_seconds=40))
    assert scenario.timeout_result is not None
    state = tmp_path / "state"
    with sqlite3.connect(f"file:{state}/state.sqlite?mode=ro", uri=True) as connection:
        observations = [json.loads(raw) for raw, in connection.execute(
            "SELECT observation_json FROM execution_attempts WHERE kind='AGENT'")]
    grounding = next(item for item in observations if item["request"]["command"] == "GROUNDING")
    assert grounding["response"]["typed_candidate"]["web_sources"] == [DOC]
    late = next(item for item in grounding["web_tool_results"] if item["arguments"].get("url") == slow_url)
    assert late["status"] == "error" and late["error"]["code"] == "response_not_observed"
    assert late["result"]["url"] == slow_url
    transcript = state / "artifacts" / grounding["session"]["transcript"]["path"] / "transcript.jsonl"
    assert b"NEVER_DELIVERED_SOURCE" not in transcript.read_bytes()
