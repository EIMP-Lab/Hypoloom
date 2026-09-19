"""Native adapter/session correction, not WorkflowHost scientific acceptance.

Each case has one real seed and one DESIGN CLI. Only model choices are local
fixtures; Profile selection, sandbox, capture, diagnostics and accounting are
production paths. No experiment or paid provider participates.
"""

from __future__ import annotations

import copy
import json
import secrets
import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from hypoloom.agent_aci import EmbeddedInput, required_output_schema
from hypoloom.agent_budget import fold_agent_attempts
from hypoloom.agent_sessions import _session_cost_state
from hypoloom.claude_agent_host import ClaudeAgentHost, _output_schema, _session_usage_view, process_group_exists
from hypoloom.expert_loop import ResearchNodeSubkind
from hypoloom.fake_agent import FakeAgent
from hypoloom.oracle import _raw_artifact_digest
from hypoloom.profile_v4 import project_profile_v4, resolve_node_runtime
from hypoloom.session_oracle import audit_sessions

from test_agent_sessions import _manager
from test_claude_agent_host import _claude_expert_design_request, _claude_pincer_request
from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results, native_output_schema
from test_full_cc_adapter import _MODEL, _request as native_request
from test_full_cc_host_g3 import _start_provider
from test_git_authoring_workspace import make_target
from test_profile_v4 import documents


class _CorrectionScenario:
    def __init__(self, corrected, cap=3):
        self.corrected = corrected
        self.cap = cap
        self.requests, self.errors, self.calls, self.feedback = [], [], [], {}
        self.stage, self.step = "seed", 0

    def select(self, request, stage):
        self.stage, self.step = stage, 0
        self.schema = _output_schema(request)
        response = FakeAgent().invoke(request)
        self.complete = {"typed_candidate": copy.deepcopy(response.typed_candidate),
            "declared_effects": [{"effect": item.effect} for item in response.declared_effects]}

    def record(self, document):
        self.requests.append(document)
        self.step += 1
        try:
            actual = native_output_schema(document)
            assert actual == self.schema
            if self.stage == "design" and self.step == 2:
                result = _tool_results(document)["author_source"]
                assert not result.get("is_error")
                self.feedback["author_source"] = _result_text(result)
            elif self.stage == "design" and self.step > 2:
                feedback = next(block["text"] for message in reversed(document["messages"])
                    if message.get("role") == "user" for block in message.get("content", [])
                    if block.get("type") == "text" and block.get("text", "").startswith("Stop hook feedback:"))
                assert "/typed_candidate: required constraint failed" in feedback
                assert "return one complete JSON object matching the full output schema" in feedback
                self.feedback[f"round-{self.step - 2}"] = feedback
            assert self.step <= self.cap + 1
        except (AssertionError, KeyError, StopIteration) as error:
            self.errors.append(type(error).__name__ + ": " + str(error))
        return len(self.requests)

    def tool_calls(self, _number):
        if self.errors:
            return ()
        if self.stage == "seed":
            return (("seed-output", "StructuredOutput", copy.deepcopy(self.complete)),)
        if self.step == 1:
            return (("author_source", "Write", {"file_path": "/workspace/experiment.py",
                                               "content": "print('adapter correction fixture only')\n"}),)
        number = self.step - 1
        output = copy.deepcopy(self.complete)
        missing = ("owner_role", "method_family_id")
        if number == 1 or not self.corrected or 2 < number < self.cap:
            for name in missing:
                output["typed_candidate"].pop(name)
        elif number == 2:
            output["typed_candidate"] = {name: output["typed_candidate"][name] for name in missing}
        identity = f"schema-round-{number}"
        self.calls.append(copy.deepcopy(output))
        return ((identity, "StructuredOutput", output),)


def _design_request(cap=3):
    source = documents()
    source["methodology"]["output_correction"] = {"max_failed_validations": cap}
    profile = project_profile_v4(**source)
    assert not profile.issues, profile.issues
    runtime = resolve_node_runtime({"methodology": {"controls": {"product": profile.settings}}},
                                   "scientist", "experiment_design_implement")
    assert runtime["output_correction"] == {"max_failed_validations": cap}
    request = native_request(_claude_expert_design_request(), "design", None)
    inputs = []
    for item in request.input_artifacts:
        if item.ref.endswith("--expert-config"):
            content = json.loads(item.content_bytes())
            content["node_runtime"] = runtime
            item = EmbeddedInput.from_bytes(item.ref, item.media_type, json.dumps(content).encode())
        inputs.append(item)
    return replace(request, schema_version=16, input_artifacts=tuple(inputs),
                   budget=replace(request.budget, max_turns=max(request.budget.max_turns, cap + 2)),
                   required_output_schema=required_output_schema(request.command, request.task_context, schema_version=16))


@pytest.mark.parametrize("corrected,cap", [(True, 3), (False, 3), (True, 11)],
    ids=("corrected-third-round", "exhausted-three-rounds", "corrected-eleventh-round"))
def test_native_profile_correction_in_one_design_invocation(tmp_path: Path, corrected, cap):
    _, target = make_target(tmp_path)
    manager = _manager(tmp_path)
    scenario = _CorrectionScenario(corrected, cap)
    key = "local-correction-provider-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    environment = {"ANTHROPIC_API_KEY": key, "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{provider.server_port}"}
    adapter = ClaudeAgentHost(claude_executable=_installed_claude())
    rows, payloads, metadata, observations, requests = [], {}, {}, {}, {}
    try:
        for stage, request in (
            ("seed", native_request(_claude_pincer_request(ResearchNodeSubkind.SYNTHESIS), "seed", None)),
            ("design", _design_request(cap)),
        ):
            requests[stage] = request
            frozen_request = request.to_dict()
            scenario.select(request, stage)
            workspace = manager.prepare(request, target)
            bindings = []
            started = time.time()
            handle = adapter.start(request, model=_MODEL, provider_environment=environment,
                bind_process_group=bindings.append, tool_workspace=workspace, diagnostics_root=manager.state_root)
            try:
                observation = adapter.wait(handle, timeout=70)
                observations[stage] = observation
                metadata[stage] = workspace.finish_node()
                assert request.to_dict() == frozen_request
                assert len(bindings) == 1 and bindings[0].attempt_id == request.attempt_id
                observed = {"request": frozen_request, "session_binding": workspace.binding,
                    "session": metadata[stage], "diagnostics": dict(observation.diagnostics),
                    "failure_code": observation.failure_code}
                if observation.response is not None:
                    observed["response"] = observation.response.to_dict()
                payloads[request.attempt_id] = observed
                rows.append({"attempt_id": request.attempt_id, "kind": "AGENT", "state": "CLOSED",
                    "terminal_reason": observation.terminal_reason.value, "started_at": started, "ended_at": time.time(),
                    "process_group_id": handle.process_group_id, "observation_json": json.dumps(observed)})
            finally:
                adapter.cleanup(handle)
            assert not process_group_exists(handle.process_group_id)
            assert workspace.resources == []
            if stage == "seed":
                assert observation.response is not None
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
    request = requests["design"]
    observation = observations["design"]
    diagnostic_root = manager.state_root / "diagnostics/agents" / request.run_id / request.attempt_id
    records = [json.loads(line) for line in (diagnostic_root / "stdout.log").read_text().splitlines()]
    result = next(item for item in reversed(records) if item.get("type") == "result")
    raw_outputs, inputs = [], {}
    for record in records:
        if record.get("type") == "assistant":
            message = record["message"]
            inputs[message["id"]] = max(inputs.get(message["id"], 0), message["usage"]["input_tokens"])
            assert not any(block.get("type") == "tool_use" and block.get("name") == "StructuredOutput"
                           for block in message["content"])
            raw_outputs.extend(json.loads(block["text"]) for block in message["content"] if block.get("type") == "text")
    issues, archive_count = audit_sessions(manager.publisher.root, rows, payloads, _raw_artifact_digest)
    charges = fold_agent_attempts(rows)
    charge = next(item for item in charges if item.attempt_id == request.attempt_id)
    evidence = {"paid_model": False, "gpu": False, "scope": "adapter/session only; no Host science or experiment",
        "request": request.to_dict(), "corrected": corrected, "validation_cap": cap, "raw_outputs": raw_outputs,
        "feedback": scenario.feedback, "diagnostics": dict(observation.diagnostics),
        "session_metadata": metadata, "observed_input_by_message_id": inputs,
        "cli_result": result, "charge_projection": {"source": charge.source, "tokens": charge.tokens,
            "turns": charge.turns, "cost": str(charge.cost)},
        "session_oracle_issues": issues, "archive_count": archive_count,
        "owned_process_residue": [row["process_group_id"] for row in rows if process_group_exists(row["process_group_id"])]}
    (tmp_path / "structured-output-recovery-evidence.json").write_text(json.dumps(evidence, indent=2))
    assert not scenario.errors, scenario.errors
    assert observation.diagnostics["adapter_retry_policy"]["structured_output_failed_validation_cap"] == cap
    assert len(scenario.calls) == len(raw_outputs) == cap
    assert raw_outputs == scenario.calls  # CC neither merges nor rewrites partial candidates.
    assert set(raw_outputs[0]["typed_candidate"]) == set(scenario.complete["typed_candidate"]) - {"owner_role", "method_family_id"}
    if corrected:
        assert set(raw_outputs[1]["typed_candidate"]) == {"owner_role", "method_family_id"}
        assert raw_outputs[-1] == scenario.complete
        assert all(item == raw_outputs[0] for item in raw_outputs[2:-1])
    else:
        assert raw_outputs[0] == raw_outputs[1] == raw_outputs[2]
    assert sum(inputs.values()) == len(inputs) == cap + 1
    assert len(list((diagnostic_root / "provider").glob("*/request.body"))) == cap + 1
    assert json.loads((diagnostic_root / "request.json").read_text()) == request.to_dict()
    assert result["session_id"] == metadata["design"]["session_id"]
    assert metadata["design"]["parent_session_id"] == metadata["seed"]["session_id"]
    assert metadata["design"]["launch_arguments"] == ["--resume", metadata["seed"]["session_id"],
        "--fork-session", "--session-id", metadata["design"]["session_id"]]
    assert issues == [] and archive_count == 2
    assert evidence["owned_process_residue"] == []
    trace = observation.diagnostics["structured_output_correction"]
    assert trace["scope"] == "native_stop_validation" and trace["call_count"] == cap
    assert trace["validation_source"] == "host_stop_hook"
    assert trace["rejected_count"] == (cap - 1 if corrected else cap)
    assert trace["accepted"] is corrected
    assert trace["status"] == ("recovered" if corrected else "unresolved")
    assert [item["ordinal"] for item in trace["rounds"]] == list(range(1, cap + 1))
    assert [item["status"] for item in trace["rounds"]] == ["rejected"] * (cap - 1) + ["accepted" if corrected else "rejected"]
    assert all(set(item) == {"ordinal", "status", "validation_error"} for item in trace["rounds"])
    validations = [json.loads(path.read_text()) for path in sorted((diagnostic_root / "native-output").glob("*.json"))]
    assert [json.loads(item["last_assistant_message"]) for item in validations] == raw_outputs
    assert [item["error"] for item in validations] == [item["validation_error"] for item in trace["rounds"]]
    assert set(item["session_id"] for item in validations) == {metadata["design"]["session_id"]}
    assert key not in json.dumps(evidence)
    assert result["subtype"] == "success" and result["is_error"] is False and result.get("structured_output") is None
    assert json.loads(result["result"]) == raw_outputs[-1]
    assert json.loads((diagnostic_root / "result.json").read_text())["returncode"] == 0
    assert observation.diagnostics["provider_accounting"]["token_usage"] == {
        "input_tokens": cap + 1, "output_tokens": cap + 1, "total_tokens": (cap + 1) * 2}
    assert charge.tokens == (cap + 1) * 2 and charge.turns == result["num_turns"] <= request.budget.max_turns
    # Since CC 2.1.277 the forked design session's result ledger accumulates
    # over the seed's saved cost-state; deduct that exact carried baseline.
    carried = _session_cost_state(
        (manager.publisher.root / metadata["seed"]["transcript"]["path"] / "transcript.jsonl").read_bytes())
    view = _session_usage_view(result, carried)
    assert result["usage"]["input_tokens"] == view["modelUsage"][_MODEL]["inputTokens"] == cap + 1
    assert result["usage"]["output_tokens"] == view["modelUsage"][_MODEL]["outputTokens"] == cap + 1
    assert charge.cost == Decimal(str(view["total_cost_usd"])) > 0
    assert "provider_reported_lower_bound" not in observation.diagnostics
    if corrected:
        assert observation.response is not None and observation.failure_code is None
        assert observation.response.token_usage["input_tokens"] == observation.response.token_usage["output_tokens"] == cap + 1
        assert charge.source == "response"
    else:
        assert observation.response is None and observation.failure_code == "invalid_structured_output"
        assert observation.diagnostics["provider_observed_input_tokens_lower_bound"] == cap + 1
        assert charge.source == "provider" and charge.cost < Decimal(request.budget.max_cost)
