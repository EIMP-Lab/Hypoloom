"""Actual CC rejects root.query, then Host retries the corrected envelope.

Reuse the existing native shared-final-report rehearsal for real CPU closure,
publication and Oracle checks. Its independently checked final-report retry
remains present; only one extra query-plan CLI Attempt is added here.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from hypoloom.expert_loop import AuxiliaryRequestKind, AuxiliaryTask

import test_claude_shared_final_retry_g3 as shared_retry
from test_claude_file_tool_cli import native_output_schema
from test_claude_shared_acceptance_g3 import _RUN
from test_full_cc_host_g3 import _rows


_INSTRUCTIONS = (
    "The mutable proposal is the value of typed_candidate.",
    "The final JSON object contains only typed_candidate.",
    "Put query and optional preferred_venues inside typed_candidate; never duplicate them at the root.",
    "JSON-schema keywords type, properties and required describe the schema; do not emit them as output fields.",
)


class _QueryEnvelopeScenario(shared_retry._FinalRetryScenario):
    def __init__(self, root: Path, nonce: str):
        super().__init__(root, nonce)
        self.query_schemas = {}
        self.query_calls = []
        self.rejected_query = None

    def record(self, document):
        number = super().record(document)
        request = self.current
        if (request is not None and isinstance(request.task_context, AuxiliaryTask)
                and request.task_context.kind is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN):
            try:
                rendered = json.dumps(document)
                assert all(text in rendered for text in _INSTRUCTIONS)
                schema = native_output_schema(document)
                assert set(schema["properties"]) == {"typed_candidate"}
                assert schema["required"] == ["typed_candidate"] and schema["additionalProperties"] is False
                assert "query" in schema["properties"]["typed_candidate"]["properties"]
                assert "query" in schema["properties"]["typed_candidate"]["required"]
                skill = next(item for item in request.input_artifacts
                             if item.ref.endswith("--openalex-query-planning-skill"))
                assert "typed_candidate" in skill.content_bytes().decode()
                self.query_schemas[request.attempt_id] = copy.deepcopy(schema)
            except (AssertionError, KeyError, StopIteration) as exc:
                self.errors.append("query envelope instructions/schema: " + repr(exc))
        return number

    def tool_calls(self, number):
        calls = super().tool_calls(number)
        request = self.current
        if request is None or request.attempt_id not in self.query_schemas:
            return calls
        if self.rejected_query is None:
            self.rejected_query = request.attempt_id
        emitted = []
        for identity, tool, arguments in calls:
            arguments = copy.deepcopy(arguments)
            if tool == "StructuredOutput":
                assert set(arguments) == {"typed_candidate"}
                if request.attempt_id == self.rejected_query:
                    # The nested candidate is valid: production must reject the
                    # extra root field rather than silently strip/coerce it.
                    arguments["query"] = copy.deepcopy(arguments["typed_candidate"]["query"])
                self.query_calls.append({"attempt_id": request.attempt_id,
                    "arguments": copy.deepcopy(arguments)})
            emitted.append((identity, tool, arguments))
        return tuple(emitted)


def test_native_query_plan_root_field_rejects_then_same_session_retry_closes_cpu(tmp_path: Path, monkeypatch):
    scenarios = []

    def scenario_factory(root, nonce):
        scenario = _QueryEnvelopeScenario(root, nonce)
        scenarios.append(scenario)
        return scenario

    monkeypatch.setattr(shared_retry, "_FinalRetryScenario", scenario_factory)
    shared_retry._run_final_retry(tmp_path)
    scenario = scenarios[0]
    attempts = _rows(tmp_path / "state/state.sqlite", "SELECT * FROM execution_attempts ORDER BY started_at")
    queries = []
    for row in attempts:
        if row["kind"] != "AGENT":
            continue
        observed = json.loads(row["observation_json"])
        if observed["request"]["task_context"].get("request_kind") == "GROUNDING_QUERY_PLAN":
            queries.append((row, observed))
    assert len(queries) == 2
    failed, retry = queries
    assert failed[0]["attempt_id"] == scenario.rejected_query
    assert [row["terminal_reason"] for row, _ in queries] == ["FAILED", "SUCCEEDED"]
    assert [row["attempt_ordinal"] for row, _ in queries] == [1, 2]
    assert failed[0]["command_id"] == retry[0]["command_id"]
    assert failed[1]["failure_code"] == "invalid_candidate" and failed[1]["retry_class"] == "MODEL"
    assert failed[1].get("response") is None
    diagnostic = failed[1]["diagnostics"]["provider_diagnostic"]
    assert diagnostic == "/: additionalProperties constraint failed"
    trace = failed[1]["diagnostics"]["structured_output_correction"]
    assert trace["scope"] == "native_stop_validation" and trace["status"] == "unresolved"
    assert trace["rounds"] == [{"ordinal": ordinal, "status": "rejected",
                                "validation_error": diagnostic, "validation_stage": "output_schema"} for ordinal in (1, 2)]
    session = failed[1]["session"]["session_id"]
    assert retry[1]["session"]["session_id"] == session
    assert retry[1]["session"]["branch"] == failed[1]["session"]["branch"]
    assert retry[1]["session"]["launch_arguments"] == ["--resume", session]
    assert any(item["ref"] == failed[0]["attempt_id"] + "--candidate-validation-error"
               for item in retry[1]["request"]["input_artifacts"])
    assert scenario.query_schemas[failed[0]["attempt_id"]] == scenario.query_schemas[retry[0]["attempt_id"]]
    assert len(scenario.query_calls) == 3
    invalid, rejected_again, corrected = scenario.query_calls
    assert rejected_again == invalid
    assert set(invalid["arguments"]) == {"typed_candidate", "query"}
    assert set(corrected["arguments"]) == {"typed_candidate"}
    assert invalid["arguments"]["typed_candidate"] == corrected["arguments"]["typed_candidate"]
    assert retry[1]["response"]["typed_candidate"]["query"] == corrected["arguments"]["typed_candidate"]["query"]
    source = tmp_path / "state/diagnostics/agents" / _RUN / failed[0]["attempt_id"] / "stdout.log"
    results = [json.loads(line) for line in source.read_text().splitlines()]
    outer = next(item for item in reversed(results) if item.get("type") == "result")
    assert outer["subtype"] == "success" and outer["is_error"] is False
    assert outer.get("structured_output") is None and json.loads(outer["result"]) == invalid["arguments"]
    assert json.loads(source.with_name("result.json").read_text())["returncode"] == 0
    closure = json.loads((tmp_path / "shared-final-retry-evidence.json").read_text())
    assert closure["agent_count"] == 13 and closure["process_count"] == 1
    assert closure["oracle"]["ok"] and closure["head"]["state"] == "COMPLETED"
    (tmp_path / "query-plan-envelope-evidence.json").write_text(json.dumps({
        "paid_model": False, "gpu": False, "agent_count": closure["agent_count"],
        "process_count": closure["process_count"], "query_session_id": session,
        "query_attempt_ids": [row["attempt_id"] for row, _ in queries],
        "query_calls": scenario.query_calls, "supplied_schemas": scenario.query_schemas,
        "verified_instruction_text": list(_INSTRUCTIONS), "failed_cli_stdout": str(source),
        "reused_final_attempts": closure["final_attempts"], "oracle": closure["oracle"],
        "head": closure["head"]}, indent=2))
