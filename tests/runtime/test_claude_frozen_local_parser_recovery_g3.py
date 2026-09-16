"""Native parser-boundary failure must recover through a new scientific hypothesis.

After DESIGN's sample check, the execution session introduces a parser edit
that wrongly emits the accepted common parser's envelope.
The real Host rejects an in-hypothesis parser rewrite. Ordinary analysis/update
and the next cycle must establish a new hypothesis before corrected publication.
All model choices are local scripted-provider fixtures; no paid model or GPU.
"""

from __future__ import annotations

import copy
import json
import secrets
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import yaml

from hypoloom.agent_aci import WorkspaceArtifact, with_output_digest
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.oracle import Oracle
from hypoloom.profile_admission import admit_profile

from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results
from test_claude_measurement_g3 import _NoPublicWeb
from test_claude_multichain_g3 import _BatchScenario
from test_claude_shared_acceptance_g3 import (
    _AcceptanceChoices, _AcceptanceScenario, _DECLARATION, _GOAL, _RUN,
    _common_source, _experiment_files, _fixture_pairs, _local_measurement, _payload,
)
from test_full_cc_host_g3 import _new_host, _request_from_provider, _start_provider
from test_real_design_recovery_probe import _write_json
from test_real_execution_repair_probe import _raw_tools


_DESIGN = AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT
_EXECUTION = AgentCommand.EXPERIMENT_EXECUTION


class _RevisionChoices(_AcceptanceChoices):
    def invoke(self, request):
        response = super().invoke(request)
        if request.command is AgentCommand.HYPOTHESIS_PROPOSAL and "hypotheses" in response.typed_candidate:
            candidate = copy.deepcopy(response.typed_candidate)
            parent = None
            if request.task_context.key.cycle > 1:
                prior = [json.loads(item.content_bytes()) for item in request.input_artifacts
                    if item.ref == request.command_id + "--batch-context"]
                assert len(prior) == 1 and prior[0]["cycle"] == request.task_context.key.cycle - 1
                assert prior[0]["selected_tip"] is None
                parent = sorted(prior[0]["chains"].values())[0]
            for item in candidate["hypotheses"]:
                if parent is not None:
                    item["candidate"].update(parent_hypothesis_id=parent, relation="revises")
                item["candidate"]["claim"] = (
                    "The original constant predictions will exceed the common suite accuracy thresholds."
                    if request.task_context.key.cycle == 1 else
                    "The revised candidate corrects one additional label per suite and will exceed the unchanged common thresholds.")
            return with_output_digest(replace(response, typed_candidate=candidate))
        if request.command is _DESIGN and request.task_context.key.cycle == 2:
            return with_output_digest(replace(response, proposed_workspace_artifacts=tuple(
                WorkspaceArtifact.from_bytes(path, content)
                for path, content in _experiment_files(request.role + "-revised").items())))
        if request.command not in {_EXECUTION, AgentCommand.RESULT_ANALYSIS, AgentCommand.HYPOTHESIS_UPDATE}:
            return response
        if request.task_context.key.cycle != 1:
            return response
        candidate = copy.deepcopy(response.typed_candidate)
        candidate["summary"] = (
            "The raw computation and common evaluator completed, but the local parser emitted the wrong "
            "output envelope. Its frozen bytes cannot be repaired within this hypothesis; the rejected "
            "replacement created no experiment. A revised hypothesis needs a distinct correct local parser.")
        if request.command is AgentCommand.HYPOTHESIS_UPDATE:
            candidate["update_disposition"] = "REVISE_NEXT_CYCLE"
        return with_output_digest(replace(response, typed_candidate=candidate))


class _FrozenLocalParserScenario(_AcceptanceScenario):
    def __init__(self, root, nonce):
        super().__init__(root, nonce)
        self.reviser = _RevisionChoices()
        self.phases = {}
        self.pending = {}
        self.experiment_by_cycle = {}
        self.execution_evidence = []
        self.rejected_replacements = []
        self.evolve_nonce = "preserve-failed-parser-and-revise-science"
        self.evolve_evidence = {}

    def record(self, document):
        request = _request_from_provider(document)
        observed = copy.deepcopy(document)
        prefix = request.attempt_id + "__"
        for message in observed.get("messages", []):
            for block in message.get("content", []) if isinstance(message.get("content"), list) else []:
                if block.get("type") == "tool_result" and block.get("tool_use_id", "").startswith(prefix):
                    block["tool_use_id"] = block["tool_use_id"][len(prefix):]
        if request.command is AgentCommand.SELF_EVOLVE:
            number = _BatchScenario.record(self, observed)
        elif request.command is not _EXECUTION:
            number = super().record(observed)
        else:
            self.requests.append(observed)
            self.current = request
            self.agent_requests[request.attempt_id] = request
            self.steps[request.attempt_id] += 1
            self.responses[request.attempt_id] = self.reviser.invoke(request)
            self.phases.setdefault(request.attempt_id, "seed" if request.task_context.key.cycle == 1 else "submit")
            try:
                pending = self.pending.pop(request.attempt_id, None)
                if pending is not None:
                    identity, tool, arguments = pending
                    result = _tool_results(observed)[identity]
                    text = _result_text(result)
                    cycle = request.task_context.key.cycle
                    phase = self.phases[request.attempt_id]
                    self.execution_evidence.append({"attempt_id": request.attempt_id, "cycle": cycle,
                        "tool": tool, "arguments": arguments, "result": copy.deepcopy(result)})
                    if phase == "resubmit":
                        assert result.get("is_error") and "measurement_frozen" in text, result
                        self.rejected_replacements.append({"attempt_id": request.attempt_id,
                            "experiment_id": self.experiment_by_cycle[cycle], "result": text})
                        self.phases[request.attempt_id] = "done"
                    else:
                        assert not result.get("is_error"), result
                        if phase == "seed":
                            self.phases[request.attempt_id] = "submit"
                        elif phase == "submit":
                            self.experiment_by_cycle[cycle] = json.loads(text)["exp_id"]
                            self.phases[request.attempt_id] = "wait"
                        elif phase == "wait":
                            waited = json.loads(text)
                            if waited["terminal"]:
                                assert waited["outcome"]["outcome"] == ("EXECUTION_FAILURE" if cycle == 1 else "SUCCESS"), waited
                                self.phases[request.attempt_id] = "log" if cycle == 1 else "done"
                        elif phase == "log":
                            self.phases[request.attempt_id] = "read"
                        elif phase == "read":
                            assert self.nonce in text and "measurements" in text
                            self.phases[request.attempt_id] = "rewrite"
                        elif phase == "rewrite":
                            self.phases[request.attempt_id] = "resubmit"
            except Exception as exc:
                self.errors.append(f"frozen parser provider: {type(exc).__name__}: {exc}")
            number = len(self.requests)
        self.requests[-1] = document
        return number

    def tool_calls(self, number):
        request = self.current
        if request is None or self.errors:
            return ()
        if request.command is AgentCommand.SELF_EVOLVE:
            calls = _BatchScenario._unscoped_tool_calls(self, number)
        elif request.command is not _EXECUTION:
            calls = super().tool_calls(number)
            if request.command is _DESIGN and request.task_context.key.cycle == 2 and self.steps[request.attempt_id] == 3:
                identity, tool, arguments = calls[0]
                calls = ((identity, tool, {**arguments,
                    "content": _experiment_files(request.role + "-revised")["experiment.py"].decode()}),)
        else:
            phase = self.phases[request.attempt_id]
            cycle = request.task_context.key.cycle
            identity = f"parser_{phase}_{self.steps[request.attempt_id]}"
            if phase == "done":
                calls = self._structured(request)
            else:
                if phase == "seed":
                    tool, arguments = "Write", {"file_path": "/workspace/parse_local.py",
                        "content": _common_source(self.nonce)}
                elif phase in {"submit", "resubmit"}:
                    tool = "mcp__host-tools__submit_experiment"
                    arguments = {"candidate": self.designs[request.role], "gpus": 0}
                    if phase == "resubmit":
                        arguments["replaces_experiment_id"] = self.experiment_by_cycle[cycle]
                elif phase == "wait":
                    tool, arguments = "mcp__host-tools__wait_experiment", {
                        "exp_id": self.experiment_by_cycle[cycle], "max_wait_seconds": 5}
                elif phase == "log":
                    tool, arguments = "mcp__host-tools__get_experiment_log", {"exp_id": self.experiment_by_cycle[cycle]}
                elif phase == "read":
                    tool, arguments = "Read", {"file_path": "/workspace/parse_local.py"}
                else:
                    assert phase == "rewrite"
                    tool, arguments = "Write", {"file_path": "/workspace/parse_local.py",
                        "content": _experiment_files(request.role)["parse_local.py"].decode()}
                self.pending[request.attempt_id] = (identity, tool, copy.deepcopy(arguments))
                calls = ((identity, tool, arguments),)
        return tuple((request.attempt_id + "__" + identity, tool, arguments) for identity, tool, arguments in calls)


def _recovery_payload(root, mount):
    data = _fixture_pairs()
    data["candidates"][_GOAL]["diagnostic"] = list(data["labels"])
    data["candidates"][_GOAL + "-revised"] = copy.deepcopy(data["candidates"][_GOAL])
    for suite in ("suite_one", "suite_two"):
        data["candidates"][_GOAL][suite] = [0, 0, 0, 0]
    _payload(root, mount, roles=(_GOAL,), fixture_data=data)
    profile = root / "profile"
    goal = yaml.safe_load((profile / "goal.yaml").read_text())
    goal["stop_conditions"]["max_rounds_per_expert"] = 2
    goal["description"] += (
        " Preserve each hypothesis's accepted measurement/parser bytes. If the first hypothesis cannot "
        "produce its declared local result envelope, record the factual failure and revise in a new "
        "scientific cycle using the same common acceptance.")
    methodology = yaml.safe_load((profile / "methodology.yaml").read_text())
    methodology["budget"]["max_experiments"] = 2
    for name, document in (("goal", goal), ("methodology", methodology)):
        (profile / f"{name}.yaml").write_text(yaml.safe_dump(document))
    admitted = admit_profile(profile, run_id=_RUN, allow_gpu=False)
    assert admitted.admitted, admitted.to_dict()
    _write_json(root / "profile-admission.json", admitted.to_dict())
    return admitted.create_run_payload


def test_native_frozen_local_parser_failure_revises_new_hypothesis_then_meets_goal(tmp_path):
    nonce = "frozen-local-parser-" + secrets.token_hex(12)
    scenario = _FrozenLocalParserScenario(tmp_path, nonce)
    key = "local-parser-recovery-only-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    (tmp_path / "settings.json").write_text("{}\n")
    (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(tmp_path, _installed_claude(), f"http://127.0.0.1:{provider.server_port}", key)
    host.grounding_web = _NoPublicWeb()
    evidence = {"paid_model": False, "gpu": False, "run_id": _RUN}
    try:
        mount = Path(tempfile.mkdtemp(prefix="prom-frozen-parser-", dir="/var/tmp"))
        evidence["fixture_root"] = str(mount)
        payload = _recovery_payload(tmp_path, mount)
        original = copy.deepcopy(payload)
        host.initialize()
        created = host.handle(ControlRequest(1, "create-frozen-parser-recovery", 0, "CREATE_RUN", payload))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 360
        while time.monotonic() < deadline:
            host.tick()
            if scenario.errors or host.store.latest_transition(_RUN)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(0.01)
        assert payload == original
        attempts = host.store.list_attempts()
        agents = [json.loads(row["observation_json"]) for row in attempts if row["kind"] == "AGENT"]
        evidence.update(head=dict(host.store.latest_transition(_RUN)), fixture_errors=scenario.errors,
            hypotheses=host.store.list_facts(_RUN, "HypothesisProposed"),
            preregistrations=host.store.list_facts(_RUN, "ExperimentPreregistered"),
            outcomes=host.store.list_facts(_RUN, "CanonicalOutcomeAccepted"),
            assessments=host.store.list_facts(_RUN, "HypothesisAssessed"),
            verdicts=host.store.list_facts(_RUN, "GoalVerdictRecorded"),
            rejected_replacements=scenario.rejected_replacements, execution_tools=scenario.execution_evidence,
            process_attempts=[dict(row) for row in attempts if row["kind"] == "PROCESS"],
            agent_commands=[{"attempt_id": row["request"]["attempt_id"], "command": row["request"]["command"],
                "cycle": row["request"].get("task_context", {}).get("key", {}).get("cycle"),
                "session": row.get("session"), "candidate": row.get("response", {}).get("typed_candidate")}
                for row in agents])
        evidence["attempt_terminals"] = [{"attempt_id": row["attempt_id"], "kind": row["kind"],
            "state": row["state"], "terminal_reason": row["terminal_reason"],
            "validation_error": json.loads(row["observation_json"]).get("diagnostics", {}).get("validation_error")}
            for row in attempts]
        evidence["creations"] = [dict(row) for row in host.store.inspect(
            "SELECT workflow_id,command_id,payload_json FROM workflow_transitions WHERE run_id=? AND workflow_kind='EXPERIMENT' AND revision=1 ORDER BY rowid", (_RUN,))]
        evidence["submitted_local_parsers"] = []
        evidence["submitted_raw_sources"] = []
        for spec in evidence["preregistrations"]:
            command_id = spec["payload"]["execution_contract"]["source_submit_command_id"]
            command = host.store.inspect("SELECT request_json FROM workflow_commands WHERE command_id=?", (command_id,))[0]
            captured = json.loads(command["request_json"])["source_snapshot"]["arguments"]["workspace_files"]
            parser = next(WorkspaceArtifact.from_dict(item).content_bytes().decode() for item in captured if item["path"] == "parse_local.py")
            evidence["submitted_local_parsers"].append(parser)
            raw = next(WorkspaceArtifact.from_dict(item).content_bytes().decode() for item in captured if item["path"] == "experiment.py")
            evidence["submitted_raw_sources"].append(raw)
        ground = next(row for row in agents if row["request"]["command"] == "GROUNDING")
        common_path = host.publisher.root / ground["session"]["transcript"]["path"] / "evaluator/evaluate.py"
        evidence["common_source_unchanged"] = common_path.read_text() == _common_source(nonce)
        evidence["oracle"] = Oracle(host.store.database_path, host.publisher.root).audit(_RUN).to_dict()
        for row in evidence["agent_commands"]:
            if row["command"] == _EXECUTION.value:
                path = tmp_path / "state/diagnostics/agents" / _RUN / row["attempt_id"] / "stdout.log"
                row["raw_tools"] = _raw_tools(path)
    finally:
        groups = [] if not host.store.database_path.exists() else [row["process_group_id"] for row in host.store.list_attempts() if row["process_group_id"]]
        host.close()
        evidence["owned_process_residue_after_close"] = [group for group in groups if process_group_exists(group)]
        _write_json(tmp_path / "frozen-local-parser-recovery.json", evidence)
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)

    assert not evidence["fixture_errors"], evidence["fixture_errors"]
    assert len(evidence["rejected_replacements"]) == 1
    assert len(evidence["hypotheses"]) == 2, "Host must admit a new hypothesis after the requested scientific revision"
    assert [row["payload"]["outcome"] for row in evidence["outcomes"]] == ["EXECUTION_FAILURE", "SUCCESS"]
    assert len(evidence["creations"]) == len(evidence["preregistrations"]) == len(evidence["process_attempts"]) == 2
    hypotheses = evidence["hypotheses"]
    assert hypotheses[1]["payload"]["parent_hypothesis_id"] == hypotheses[0]["hypothesis_id"]
    assert hypotheses[1]["payload"]["relation"] == "revises"
    assert hypotheses[0]["payload"]["claim"] != hypotheses[1]["payload"]["claim"]
    assert len({row["hypothesis_id"] for row in evidence["preregistrations"]}) == 2
    assert evidence["submitted_local_parsers"] == [_common_source(nonce), _experiment_files(_GOAL)["parse_local.py"].decode()]
    assert evidence["submitted_raw_sources"] == [_experiment_files(role)["experiment.py"].decode() for role in (_GOAL, _GOAL + "-revised")]
    assert all(row["payload"]["measurement"] == _local_measurement().to_dict() for row in evidence["preregistrations"])
    assert all(row["payload"]["goal_evaluation"]["acceptance"] == _DECLARATION for row in evidence["preregistrations"])
    assert evidence["common_source_unchanged"]
    assert all(row["terminal_reason"] == "SUCCEEDED" for row in evidence["process_attempts"])
    assert evidence["assessments"][0]["payload"]["assessment"] == "INCONCLUSIVE"
    assert [row["payload"]["goal_evaluation"]["measurements"] for row in evidence["outcomes"]] == [
        {"suite_one_accuracy": value, "suite_two_accuracy": value} for value in ("0.5", "0.75")]
    assert evidence["verdicts"][-1]["payload"]["verdict"] == "GOAL_MET"
    commands = evidence["agent_commands"]
    adjudication = next(row for row in commands if row["command"] == "ADJUDICATE" and row["cycle"] == 1)["candidate"]
    assert adjudication["best_chain"] is None and adjudication["prune"] == []
    assert {row["status"] for row in adjudication["verdicts"]} == {"inconclusive"}
    update = next(row for row in commands if row["command"] == "HYPOTHESIS_UPDATE" and row["cycle"] == 1)
    assert update["candidate"]["update_disposition"] == "REVISE_NEXT_CYCLE"
    assert {row["cycle"] for row in commands if row["command"] == _DESIGN.value} == {1, 2}
    assert {row["cycle"] for row in commands if row["command"] == _EXECUTION.value} == {1, 2}
    assert sum(row["command"] == "FINAL_REPORT" for row in commands) == 1
    assert all(row["state"] == "CLOSED" for row in evidence["attempt_terminals"])
    assert evidence["head"]["state"] == "COMPLETED" and evidence["oracle"]["ok"]
    assert evidence["oracle"]["open_attempt_count"] == evidence["oracle"]["pending_effect_count"] == evidence["oracle"]["duplicate_effect_count"] == 0
    assert not evidence["owned_process_residue_after_close"]
