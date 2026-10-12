"""Real CC preserves the SYNTHESIS C5 choice through design and CPU publication.

Only model choices use a local scripted provider. Reuse the existing evaluator,
source tests and Process fixture, without replacing schema/Host/Kernel checks.
"""

from __future__ import annotations

import copy
import json
import secrets
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.agent_aci import with_output_digest
from hypoloom.claude_agent_host import _output_schema, process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeSubkind
from hypoloom.oracle import Oracle

from test_claude_file_tool_cli import _installed_claude, native_output_schema
from test_claude_measurement_g3 import _NoPublicWeb
from test_claude_shared_acceptance_g3 import (
    _AcceptanceChoices, _AcceptanceScenario, _DECLARATION, _GOAL, _RUN,
    _common_source, _local_measurement, _payload,
)
from test_deferred_measurement_host import _candidate_bodies
from test_full_cc_host_g3 import _new_host, _request_from_provider, _start_provider


class _ScopeChoices(_AcceptanceChoices):
    def __init__(self, goal_only):
        super().__init__()
        self.goal_only = goal_only

    def invoke(self, request):
        response = super().invoke(request)
        if not self.goal_only:
            return response
        candidate = copy.deepcopy(response.typed_candidate)
        if request.command is AgentCommand.HYPOTHESIS_PROPOSAL:
            for body in _candidate_bodies(request, candidate):
                if "measurement" in body:
                    body["measurement"] = None
        elif request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            candidate.update(measurement=None, metrics=[])
            response = replace(response, proposed_workspace_artifacts=tuple(
                item for item in response.proposed_workspace_artifacts if item.path != "parse_local.py"))
        return with_output_digest(replace(response, typed_candidate=candidate))


class _ScopeScenario(_AcceptanceScenario):
    def __init__(self, root, nonce, goal_only):
        super().__init__(root, nonce)
        self.reviser = _ScopeChoices(goal_only)
        self.goal_only = goal_only
        self.rejected_design = None
        self.rejected_output = None
        self.design_schemas = {}

    def record(self, document):
        request = _request_from_provider(document)
        # Each retry has distinct actual tool IDs. The inherited fixture only
        # needs its local names when inspecting results in this in-memory copy.
        observed = copy.deepcopy(document)
        prefix = request.attempt_id + "__"
        if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            schema = native_output_schema(document)
            assert schema == _output_schema(request)
            self.design_schemas[request.attempt_id] = copy.deepcopy(schema)
            for message in observed.get("messages", []):
                for block in message.get("content", []) if isinstance(message.get("content"), list) else []:
                    if block.get("type") == "tool_result" and block.get("tool_use_id", "").startswith(prefix):
                        block["tool_use_id"] = block["tool_use_id"][len(prefix):]
        number = super().record(observed)
        # The declaration is chosen by SYNTHESIS and preserved by DESIGN;
        # directional argument nodes must not receive design-only instructions.
        if (request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT
                or request.command is AgentCommand.HYPOTHESIS_PROPOSAL
                and request.task_context.key.subkind is ResearchNodeSubkind.SYNTHESIS):
            if "Once SYNTHESIS is accepted" not in json.dumps(document):
                self.errors.append("Actual CC request omitted the stage-specific measurement instruction")
        return number

    def tool_calls(self, number):
        calls = super().tool_calls(number)
        request = self.current
        if request is None or request.command is not AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            return calls
        if not self.goal_only and any(tool == "StructuredOutput" for _, tool, _ in calls):
            if self.rejected_design is None:
                self.rejected_design = request.attempt_id
            if request.attempt_id == self.rejected_design:
                # Exhaust both native correction opportunities so this case
                # still exercises Host retry with frozen measurement inputs.
                calls = copy.deepcopy(calls)
                for _, tool, arguments in calls:
                    if tool == "StructuredOutput":
                        arguments["typed_candidate"].update(measurement=None, metrics=[], supporting_refs=[])
                        self.rejected_output = copy.deepcopy(arguments)
        return tuple((request.attempt_id + "__" + identity, tool, arguments)
                     for identity, tool, arguments in calls)


@pytest.mark.parametrize("goal_only", [False, True], ids=["preserve-local-after-rejection", "goal-only-from-synthesis"])
def test_native_shared_scope_preserves_the_accepted_measurement_choice(tmp_path: Path, goal_only):
    scenario = _ScopeScenario(tmp_path, "shared-scope-" + secrets.token_hex(12), goal_only)
    key = "local-scope-provider-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    (tmp_path / "settings.json").write_text("{}\n")
    (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(tmp_path, _installed_claude(), f"http://127.0.0.1:{provider.server_port}", key)
    host.grounding_web = _NoPublicWeb()
    try:
        with tempfile.TemporaryDirectory(prefix="prom-shared-scope-", dir="/var/tmp") as mount:
            payload = _payload(tmp_path, Path(mount), roles=(_GOAL,))
            host.initialize()
            assert host.handle(ControlRequest(1, "create-scope", 0, "CREATE_RUN", payload)).status == "accepted"
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                host.tick()
                assert not scenario.errors, scenario.errors
                if host.store.latest_transition(_RUN)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
                time.sleep(0.01)
            assert host.store.latest_transition(_RUN)["state"] == "COMPLETED", host.store.list_attempts()
            hypotheses = host.store.list_facts(_RUN, "HypothesisProposed")
            specs = host.store.list_facts(_RUN, "ExperimentPreregistered")
            outcomes = host.store.list_facts(_RUN, "CanonicalOutcomeAccepted")
            assert len(hypotheses) == len(specs) == len(outcomes) == 1
            selected = None if goal_only else _local_measurement().to_dict()
            assert hypotheses[0]["payload"].get("measurement") == specs[0]["payload"].get("measurement") == selected
            assert specs[0]["payload"]["goal_evaluation"] == {"scope": "GOAL",
                "source_attempt_id": scenario.source_attempt_id, "acceptance": _DECLARATION}
            result = outcomes[0]["payload"]
            assert result["goal_evaluation"] == {"source_attempt_id": scenario.source_attempt_id,
                "measurements": {"suite_one_accuracy": "0.75", "suite_two_accuracy": "0.75"},
                "checks": {"complete": True, "single_candidate": True}}
            assert result["outcome"] == ("SUCCESS" if goal_only else "CLEAN_REFUTATION")
            assert result["metric_value"] == (None if goal_only else "0")
            assert specs[0]["payload"]["result_kind"] == ("artifact" if goal_only else "metric")
            attempts = host.store.list_attempts()
            agents = [(row, json.loads(row["observation_json"])) for row in attempts if row["kind"] == "AGENT"]
            design = [(row, observed) for row, observed in agents
                      if observed["request"]["command"] == "EXPERIMENT_DESIGN_IMPLEMENT"]
            assert len(design) == (1 if goal_only else 2)
            assert len({row["command_id"] for row, _ in design}) == 1
            inner = [observed for _, observed in agents if observed["request"]["command"] in {
                "EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION", "RESULT_ANALYSIS"}]
            # This large design retry reconstructs; execution and analysis also
            # start new conversations over the same retained Git branch.
            assert len({observed["session"]["session_id"] for observed in inner}) == len(inner)
            assert len({observed["session"]["branch"] for observed in inner}) == 1
            if not goal_only:
                rejected, retry = design
                assert rejected[0]["terminal_reason"] == "FAILED" and retry[0]["terminal_reason"] == "SUCCEEDED"
                assert rejected[1]["failure_code"] == "invalid_candidate" and rejected[1]["retry_class"] == "MODEL"
                assert rejected[1].get("response") is None and rejected[1]["returncode"] == 0
                diagnostic = rejected[1]["diagnostics"]["provider_diagnostic"]
                assert diagnostic == "/typed_candidate: additionalProperties constraint failed"
                trace = rejected[1]["diagnostics"]["structured_output_correction"]
                assert trace["scope"] == "native_stop_validation" and trace["status"] == "unresolved"
                assert trace["accepted"] is False and trace["call_count"] == trace["rejected_count"] == 2
                assert trace["rounds"] == [{"ordinal": ordinal, "status": "rejected", "validation_error": diagnostic}
                                           for ordinal in (1, 2)]
                # The standard validator reports its first error, not Ajv's
                # former multi-error list. The actual full schema still freezes
                # both local fields, independently of the extra-field error.
                schema = scenario.design_schemas[rejected[0]["attempt_id"]]
                assert schema == scenario.design_schemas[retry[0]["attempt_id"]]
                properties = schema["properties"]["typed_candidate"]["properties"]
                assert properties["measurement"] == {"const": selected}
                assert properties["metrics"] == {"const": [selected["metric"]]}
                source = tmp_path / "state/diagnostics/agents" / _RUN / rejected[0]["attempt_id"] / "stdout.log"
                records = [json.loads(line) for line in source.read_text().splitlines()]
                outer = next(record for record in reversed(records) if record.get("type") == "result")
                assert outer["subtype"] == "success" and outer["is_error"] is False
                assert outer.get("structured_output") is None
                assert json.loads(outer["result"]) == scenario.rejected_output
                invalid = scenario.rejected_output["typed_candidate"]
                assert invalid["measurement"] is None and invalid["metrics"] == [] and invalid["supporting_refs"] == []
                assert any(block.get("type") == "text" and block.get("text") == outer["result"]
                           for record in records if record.get("type") == "assistant"
                           for block in record["message"]["content"])
                assert retry[1]["session"]["session_id"] != rejected[1]["session"]["session_id"]
                assert retry[1]["session"]["launch_arguments"] == ["--session-id", retry[1]["session"]["session_id"]]
                assert any(item["ref"] == rejected[0]["attempt_id"] + "--candidate-validation-error"
                           for item in retry[1]["request"]["input_artifacts"])
            accepted = design[-1][1]["response"]["typed_candidate"]
            assert accepted["measurement"] == selected and accepted["evaluation_scope"] == "GOAL"
            assert accepted["method"] and "supporting_refs" not in accepted
            assert accepted["metrics"] == ([] if goal_only else [selected["metric"]])
            assert len([row for row in attempts if row["kind"] == "PROCESS"]) == 1
            assert all(row["state"] == "CLOSED" for row in attempts)
            verdicts = host.store.list_facts(_RUN, "GoalVerdictRecorded")
            assert len(verdicts) == 1 and verdicts[0]["payload"]["verdict"] == "GOAL_MET"
            ground = next(observed for _, observed in agents if observed["request"]["command"] == "GROUNDING")
            source = host.publisher.root / ground["session"]["transcript"]["path"] / "evaluator/evaluate.py"
            assert source.read_text() == _common_source(scenario.nonce)
            assert scenario.source_reads == scenario.source_readonly == {_GOAL}
            assert sum(observed["request"]["command"] == "FINAL_REPORT" for _, observed in agents) == 1
            audit = Oracle(host.store.database_path, host.publisher.root).audit(_RUN)
            assert audit.ok, audit.to_dict()
            assert not host.running and not host.running_agents
            assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"])
            (tmp_path / "shared-scope-evidence.json").write_text(json.dumps({
                "goal_only": goal_only, "hypotheses": hypotheses, "specs": specs, "outcomes": outcomes,
                "design_attempt_ids": [row["attempt_id"] for row, _ in design],
                "inner_session": inner[0]["session"]["session_id"], "oracle": audit.to_dict()}, indent=2))
    finally:
        host.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
