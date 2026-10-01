"""Real Host/CLI MODEL correction resumes one task, then closes a CPU experiment.

The loopback provider chooses short tool operations and one invalid citation;
native Stop validation, session archives, accounting and lifecycle stay real.
"""
from __future__ import annotations

import copy
import json
import secrets
import tempfile
import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import yaml

from hypoloom.agent_aci import with_output_digest
from hypoloom.agent_budget import fold_agent_attempts
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.oracle import Oracle
from hypoloom.profile_admission import admit_profile

from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results
from test_claude_measurement_g3 import _NoPublicWeb
from test_claude_shared_acceptance_g3 import _AcceptanceScenario, _DECLARATION, _GOAL, _RUN, _common_source, _payload
from test_full_cc_host_g3 import _new_host, _request_from_provider, _start_provider


class _TaskRetryScenario(_AcceptanceScenario):
    def __init__(self, root, nonce):
        super().__init__(root, nonce)
        self.first_grounding = None
        self.draft_reread = False
        self.first_archive_before_retry = None

    def record(self, document):
        request = _request_from_provider(document)
        if request.command is not AgentCommand.GROUNDING:
            return super().record(document)
        self.requests.append(document)
        self.current = request
        self.agent_requests[request.attempt_id] = request
        self.steps[request.attempt_id] += 1
        if self.first_grounding is None:
            self.first_grounding = request.attempt_id
        self.source_attempt_id = request.attempt_id
        try:
            step = self.steps[request.attempt_id]
            results = _tool_results(document)
            if request.attempt_id == self.first_grounding:
                if step in (2, 3):
                    result = results["retry_parser_write" if step == 2 else "retry_parser_check"]
                    assert not result.get("is_error"), result
                    if step == 3:
                        assert "RETRY_DRAFT_TESTED" in _result_text(result)
            else:
                if step == 1:
                    archive = self.private_root / "state/artifacts" / _RUN / "agent" / self.first_grounding / "transcript/transcript.jsonl"
                    self.first_archive_before_retry = archive.read_bytes()
                if step == 2:
                    result = results["retry_existing_parser"]
                    assert not result.get("is_error") and self.nonce in _result_text(result)
                    self.draft_reread = True
            response = self.reviser.invoke(request)
            self.responses[request.attempt_id] = with_output_digest(replace(response, typed_candidate={
                "task_kind": "GROUNDING", "supporting_refs": list(request.immutable_context_refs),
                "background": {"project_summary": "Common evaluator authored and tested. " + self.nonce,
                    "codebase_map": [], "entrypoints": [],
                    "how_to_run_experiment": "Produce both suites from one candidate and use the tested evaluator.",
                    "metrics_available": [], "risks": [], "open_questions": []},
                # Repeated semantic rejection exhausts the native Stop correction
                # allowance in Attempt one; Attempt two repairs that one field.
                "web_sources": ["https://example.org/unretrieved-retry-source"] if request.attempt_id == self.first_grounding else [],
                "clarification": None, "acceptance": copy.deepcopy(_DECLARATION)}))
        except Exception as exc:
            self.errors.append(f"same-task provider: {type(exc).__name__}: {exc}")
        return len(self.requests)

    def tool_calls(self, number):
        request = self.current
        if request is None or self.errors:
            return ()
        if request.command is not AgentCommand.GROUNDING:
            return super().tool_calls(number)
        step = self.steps[request.attempt_id]
        if request.attempt_id == self.first_grounding:
            if step == 1:
                return (("retry_parser_write", "Write", {"file_path": "/acceptance/evaluate.py", "content": _common_source(self.nonce)}),)
            if step == 2:
                script = """set -e
python3 /acceptance/evaluate.py --self-test
python3 - <<'PY'
import json
from pathlib import Path
sample = {'suites': {name: {'candidate': 'smoke', 'labels': [0, 1, 0, 1],
    'predictions': [0, 0, 0, 0]} for name in ('suite_one', 'suite_two')}}
Path('/acceptance/smoke').mkdir(exist_ok=True)
Path('/acceptance/smoke/predictions.json').write_text(json.dumps(sample))
print('RETRY_DRAFT_TESTED')
PY
"""
                return (("retry_parser_check", "Bash", {"command": script, "timeout": 10000}),)
        elif step == 1:
            return (("retry_existing_parser", "Read", {"file_path": "/acceptance/evaluate.py"}),)
        return self._structured(request)


def test_native_same_task_model_retry_resumes_archive_and_cost_then_starts_fresh_science(tmp_path):
    scenario = _TaskRetryScenario(tmp_path, "same-task-" + secrets.token_hex(8))
    key = "local-same-task-" + secrets.token_hex(12)
    provider, thread = _start_provider(scenario, key)
    (tmp_path / "settings.json").write_text("{}\n")
    (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(tmp_path, _installed_claude(), f"http://127.0.0.1:{provider.server_port}", key)
    host.grounding_web = _NoPublicWeb()
    evidence = {"paid_model": False, "gpu": False}
    try:
        with tempfile.TemporaryDirectory(prefix="hypo-task-retry-", dir="/var/tmp") as mount:
            _payload(tmp_path, Path(mount), roles=(_GOAL,))
            methodology = tmp_path / "profile/methodology.yaml"
            document = yaml.safe_load(methodology.read_text())
            # Keep source prompt material small; the task uses the ordinary
            # working window and receives no test-only resume/capacity bypass.
            document["memory"] = {"max_inline_input_bytes": 8192}
            methodology.write_text(yaml.safe_dump(document))
            admission = admit_profile(tmp_path / "profile", run_id=_RUN, allow_gpu=False)
            assert admission.admitted, admission.to_dict()
            host.initialize()
            result = host.handle(ControlRequest(1, "create-same-task", 0, "CREATE_RUN", admission.create_run_payload))
            assert result.status == "accepted", result
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                host.tick()
                assert not scenario.errors, scenario.errors
                if host.store.latest_transition(_RUN)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
                time.sleep(.01)
            assert host.store.latest_transition(_RUN)["state"] == "COMPLETED"
            attempts = host.store.list_attempts()
            agents = [(row, json.loads(row["observation_json"])) for row in attempts if row["kind"] == "AGENT"]
            grounding = [(row, observation) for row, observation in agents if observation["request"]["command"] == "GROUNDING"]
            assert len(grounding) == 2
            failed, retry = grounding
            assert [row["terminal_reason"] for row, _ in grounding] == ["FAILED", "SUCCEEDED"]
            assert failed[1]["retry_class"] == "MODEL"
            assert failed[1]["diagnostics"]["structured_output_correction"]["rejected_count"] == 2
            session = failed[1]["session"]["session_id"]
            assert retry[1]["session"]["session_id"] == session
            assert retry[1]["session"]["launch_arguments"] == ["--resume", session]
            assert failed[0]["ended_at"] <= retry[0]["started_at"]
            assert scenario.draft_reread
            archives = [host.publisher.root / observation["session"]["transcript"]["path"] for _, observation in grounding]
            transcripts = [(directory / "transcript.jsonl").read_bytes() for directory in archives]
            assert scenario.first_archive_before_retry == transcripts[0]
            assert transcripts[1].startswith(transcripts[0])
            assert all((directory / "evaluator/evaluate.py").read_text() == _common_source(scenario.nonce) for directory in archives)
            carry = retry[1]["diagnostics"]["provider_session_carryover"]
            assert carry["source"] == "session_cost_state" and Decimal(str(carry["total_cost_usd"])) > 0
            charges = {charge.attempt_id: charge for charge in fold_agent_attempts(attempts)}
            ground_charges = [charges[row["attempt_id"]] for row, _ in grounding]
            assert all(charge.tokens > 0 and charge.turns > 0 and charge.cost > 0 for charge in ground_charges)
            assert ground_charges[0].source == "provider" and ground_charges[1].source == "response"
            assert abs(ground_charges[0].cost - Decimal(str(carry["total_cost_usd"]))) < Decimal("0.000000001")
            stdout = tmp_path / "state/diagnostics/agents" / _RUN / retry[0]["attempt_id"] / "stdout.log"
            result = next(item for item in reversed([json.loads(line) for line in stdout.read_text().splitlines()]) if item.get("type") == "result")
            assert abs(sum(charge.cost for charge in ground_charges) - Decimal(str(result["total_cost_usd"]))) < Decimal("0.000000001")
            other_sessions = [observation["session"]["session_id"] for _, observation in agents if observation["request"]["command"] != "GROUNDING"]
            assert session not in other_sessions
            assert len([row for row in attempts if row["kind"] == "PROCESS"]) == 1
            report_row = next(observation for _, observation in agents
                if observation["request"]["command"] == "FINAL_REPORT")
            report_path = host.publisher.root / report_row["session"]["transcript"]["path"] / "FINAL_REPORT.md"
            assert failed[0]["attempt_id"] in report_path.read_text()
            assert failed[1]["failure_code"] in report_path.read_text()
            assert len(host.store.list_facts(_RUN, "CanonicalOutcomeAccepted")) == 1
            assert host.store.list_facts(_RUN, "GoalVerdictRecorded")[0]["payload"]["verdict"] == "GOAL_MET"
            effects = dict((row["command_type"], row["count"]) for row in host.store.inspect(
                "SELECT command_type,COUNT(*) AS count FROM workflow_commands WHERE run_id=? AND command_type IN ('CREATE_EXPERIMENT','ACCEPT_OUTCOME') GROUP BY command_type", (_RUN,)))
            assert effects == {"CREATE_EXPERIMENT": 1, "ACCEPT_OUTCOME": 1}
            audit = Oracle(host.store.database_path, host.publisher.root).audit(_RUN)
            assert audit.ok and audit.open_attempt_count == audit.pending_effect_count == 0, audit.to_dict()
            assert all(row["state"] == "CLOSED" and (row["process_group_id"] is None or not process_group_exists(row["process_group_id"])) for row in attempts)
            evidence.update(agent_count=len(agents), process_count=1, same_task_session=session,
                grounding_attempts=[row["attempt_id"] for row, _ in grounding], carryover=carry,
                grounding_charges=[{"attempt_id": charge.attempt_id, "tokens": charge.tokens, "turns": charge.turns,
                    "cost": str(charge.cost), "source": charge.source} for charge in ground_charges],
                retry_cumulative_cli_cost=result["total_cost_usd"], effects=effects, oracle=audit.to_dict())
    finally:
        evidence["head"] = dict(host.store.latest_transition(_RUN) or {})
        (tmp_path / "same-task-retry-evidence.json").write_text(json.dumps(evidence, indent=2))
        (tmp_path / "provider-errors.json").write_text(json.dumps(scenario.errors, indent=2))
        host.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
