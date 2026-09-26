"""Real CC preserves a rejected evaluator archive through retry and CPU closure.

Only the existing local provider's first Grounding web citation is changed.
Native and Host validation, archive capture, bounded same-task retry, CPU and Oracle stay real.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from hypoloom.agent_aci import WorkspaceArtifact
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.domain import AgentCommand
from hypoloom.oracle import Oracle, _raw_artifact_digest

import test_claude_shared_final_retry_g3 as shared_retry
from test_claude_shared_acceptance_g3 import _DECLARATION, _RUN, _common_source
from test_full_cc_host_g3 import _rows


_UNRETRIEVED_URL = "https://example.org/not-retrieved-grounding-source"


def _archive_bytes(directory: Path):
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in directory.rglob("*") if path.is_file()}


class _RejectedGroundingScenario(shared_retry._FinalRetryScenario):
    def __init__(self, root: Path, nonce: str):
        super().__init__(root, nonce)
        self.evidence_root = root
        self.rejected_grounding = None
        self.before_retry = None
        self.tool_identities = []

    def record(self, document):
        number = super().record(document)
        request = self.current
        if (request is not None and request.command is AgentCommand.GROUNDING
                and self.rejected_grounding is not None
                and request.attempt_id != self.rejected_grounding
                and self.before_retry is None):
            directory = (self.evidence_root / "state/artifacts" / _RUN / "agent"
                         / self.rejected_grounding / "transcript")
            self.before_retry = _archive_bytes(directory)
        return number

    def tool_calls(self, number):
        calls = copy.deepcopy(super().tool_calls(number))
        request = self.current
        if request is None:
            return calls
        if (request.command is AgentCommand.GROUNDING and self.steps[request.attempt_id] == 1
                and self.rejected_grounding is not None and request.attempt_id != self.rejected_grounding):
            # The retried task must read retained source before editing it.
            calls = (*calls, ("ground_existing", "Read", {"file_path": "/acceptance/evaluate.py"}))
        for identity, tool, arguments in calls:
            if request.command is AgentCommand.GROUNDING and tool == "StructuredOutput":
                if self.rejected_grounding is None:
                    self.rejected_grounding = request.attempt_id
                if request.attempt_id == self.rejected_grounding:
                    # Valid schema, complete evaluator and real source capture;
                    # only Host's actually-retrieved-URL authority rejects it.
                    arguments["typed_candidate"]["web_sources"] = [_UNRETRIEVED_URL]
            if request.command in {AgentCommand.GROUNDING, AgentCommand.EXPERIMENT_EXECUTION}:
                self.tool_identities.append({"attempt_id": request.attempt_id,
                    "tool_use_id": identity, "tool": tool})
        return calls


def test_native_rejected_grounding_archive_survives_same_session_retry_and_cpu(tmp_path: Path, monkeypatch):
    scenarios = []

    def scenario_factory(root, nonce):
        scenario = _RejectedGroundingScenario(root, nonce)
        scenarios.append(scenario)
        return scenario

    monkeypatch.setattr(shared_retry, "_FinalRetryScenario", scenario_factory)
    inherited_error = None
    try:
        shared_retry.test_native_shared_final_report_retries_with_one_common_input_and_same_session(tmp_path)
    except AssertionError as error:
        # Preserve scoped evidence even when the unchanged old Oracle rejects
        # the archived evaluator. Never suppress an inherited acceptance check.
        inherited_error = error
    scenario = scenarios[0]
    database = tmp_path / "state/state.sqlite"
    artifacts = tmp_path / "state/artifacts"
    attempts = _rows(database, "SELECT * FROM execution_attempts ORDER BY started_at")
    agents = [(row, json.loads(row["observation_json"])) for row in attempts if row["kind"] == "AGENT"]
    groundings = [(row, observed) for row, observed in agents
                 if observed["request"]["command"] == "GROUNDING"]
    assert not scenario.errors, scenario.errors
    assert len(groundings) == 2
    failed, retry = groundings
    assert failed[0]["attempt_id"] == scenario.rejected_grounding
    assert [row["terminal_reason"] for row, _ in groundings] == ["FAILED", "SUCCEEDED"]
    assert [row["attempt_ordinal"] for row, _ in groundings] == [1, 2]
    assert failed[0]["command_id"] == retry[0]["command_id"]
    assert failed[0]["ended_at"] <= retry[0]["started_at"]
    assert failed[1]["failure_code"] == "invalid_provider_envelope"
    assert failed[1]["retry_class"] == "MODEL" and failed[1].get("response") is None
    trace = failed[1]["diagnostics"]["structured_output_correction"]
    assert trace["status"] == "unresolved" and trace["rejected_count"] > 0
    assert failed[1]["diagnostics"]["validation_error"] == {"code": "invalid_candidate",
        "message": "web_sources must be unique actually retrieved URLs"}
    failed_stdout = (tmp_path / "state/diagnostics/agents" / _RUN / failed[0]["attempt_id"] / "stdout.log")
    failed_result = next(row for row in reversed([json.loads(line) for line in failed_stdout.read_text().splitlines()])
                         if row.get("type") == "result")
    rejected = json.loads(failed_result["result"])
    accepted = retry[1]["response"]
    assert rejected["typed_candidate"]["web_sources"] == [_UNRETRIEVED_URL]
    assert accepted["typed_candidate"]["web_sources"] == []
    assert rejected["typed_candidate"]["acceptance"] == accepted["typed_candidate"]["acceptance"] == _DECLARATION
    session_id = failed[1]["session"]["session_id"]
    assert retry[1]["session"]["session_id"] == session_id
    assert retry[1]["session"]["branch"] == failed[1]["session"]["branch"]
    assert retry[1]["session"]["launch_arguments"] == ["--resume", session_id]
    correction_ref = failed[0]["attempt_id"] + "--agent-retry-diagnostic"
    corrections = [item for item in retry[1]["request"]["input_artifacts"] if item["ref"] == correction_ref]
    assert len(corrections) == 1
    assert correction_ref not in retry[1]["request"]["immutable_context_refs"]
    archives = []
    transcripts = []
    for (row, observed), response in zip(groundings, (rejected, accepted)):
        publication = observed["session"]["transcript"]
        relative = f"{_RUN}/agent/{row['attempt_id']}/transcript"
        assert publication["path"] == relative
        assert publication["artifact_id"] == relative.replace("/", ":")
        assert publication["key"] == {"run_id": _RUN, "attempt_id": row["attempt_id"], "artifact_kind": "transcript"}
        directory = artifacts / relative
        content = _archive_bytes(directory)
        assert set(content) == set(publication["relative_paths"]) == {"transcript.jsonl", "evaluator/evaluate.py"}
        assert publication["digest"] == _raw_artifact_digest(directory)
        assert content["evaluator/evaluate.py"] == _common_source(scenario.nonce).encode()
        if row["terminal_reason"] == "SUCCEEDED":
            captured = {item["path"]: WorkspaceArtifact.from_dict(item).content_bytes()
                        for item in response["proposed_workspace_artifacts"]}
            assert captured == {"evaluate.py": content["evaluator/evaluate.py"]}
        assert content["transcript.jsonl"].endswith(b"\n")
        records = [json.loads(line) for line in content["transcript.jsonl"].splitlines()]
        assert all(record.get("sessionId") in {None, observed["session"]["session_id"]} for record in records)
        transcripts.append(content["transcript.jsonl"])
        archives.append({"attempt_id": row["attempt_id"], "path": str(directory),
            "relative_paths": sorted(content), "digest_matches": True, "evaluator_bytes_match": True,
            "transcript_records": len(records)})
        if row["attempt_id"] == scenario.rejected_grounding:
            assert scenario.before_retry == content
        stdout = tmp_path / "state/diagnostics/agents" / _RUN / row["attempt_id"] / "stdout.log"
        result = next(item for item in reversed([json.loads(line) for line in stdout.read_text().splitlines()])
                      if item.get("type") == "result")
        assert result["subtype"] == "success"  # A CLI success label alone cannot bypass native semantic rejection.
    assert transcripts[1].startswith(transcripts[0])
    assert len(agents) == 13 and len(attempts) == 14
    assert sum(row["kind"] == "PROCESS" for row in attempts) == 1
    assert all(row["state"] == "CLOSED" for row in attempts)
    assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"])
    submits = [item for item in scenario.tool_identities if item["tool"] == "mcp__host-tools__submit_experiment"]
    assert len(submits) == 1 and submits[0]["tool_use_id"] == "shared_submit"
    assert any(item["tool"] == "mcp__host-tools__wait_experiment" for item in scenario.tool_identities)
    audit = Oracle(database, artifacts).audit(_RUN)
    closure = json.loads((tmp_path / "shared-final-retry-evidence.json").read_text())
    assert closure["head"]["state"] == "COMPLETED"
    assert audit.open_attempt_count == audit.pending_effect_count == 0
    (tmp_path / "rejected-grounding-archive-evidence.json").write_text(json.dumps({
        "paid_model": False, "gpu": False, "agent_count": len(agents), "process_count": 1,
        "grounding_attempts": [row["attempt_id"] for row, _ in groundings], "session_id": session_id,
        "native_rejection": failed[1]["diagnostics"]["validation_error"], "correction_ref": correction_ref,
        "first_archive_unchanged_across_retry": True, "retry_transcript_extends_first": True,
        "archives": archives, "tool_identities": scenario.tool_identities,
        "oracle": audit.to_dict(), "head": closure["head"]}, indent=2))
    assert audit.ok, audit.to_dict()
    if inherited_error is not None:
        raise inherited_error
    assert closure["agent_count"] == 13 and closure["process_count"] == 1
    assert closure["oracle"]["ok"]
