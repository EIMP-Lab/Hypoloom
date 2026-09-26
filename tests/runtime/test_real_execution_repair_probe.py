"""Fresh native EXECUTION-only compatibility probe with actual failed CPU repair.

The default companion uses local scripted providers. Only the separately opted
in EXECUTION node uses the configured model; Host, MCP, capture, CPU execution,
scientific declarations, collection, publication and Oracle are unmodified.
"""

from __future__ import annotations

import copy
import json
import os
import secrets
import stat
import tempfile
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pytest
import yaml

from hypoloom.agent_aci import WorkspaceArtifact
from hypoloom.agent_budget import fold_agent_attempts
from hypoloom.claude_agent_host import (
    ClaudeAgentHost, process_group_exists, validate_claude_model_selector,
)
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.oracle import Oracle
from hypoloom.profile_admission import admit_profile

from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results
from test_claude_measurement_g3 import _NoPublicWeb
from test_claude_shared_acceptance_g3 import (
    _AcceptanceScenario, _DECLARATION, _GOAL, _common_source, _experiment_files,
    _fixture_pairs, _local_measurement, _payload,
)
from test_full_cc_host_g3 import _MODEL, _new_host, _request_from_provider, _start_provider
from test_real_design_recovery_probe import _DESIGN, _DESIGN_FIELDS, _DesignRouter, _write_json


_EXECUTION = AgentCommand.EXPERIMENT_EXECUTION
_GOOD_ROOT = "data_root = Path(json.loads(os.environ['HYPOLOOM_READ_ROOTS'])[0])"
_BAD_ROOT = "data_root = Path(os.environ['HYPOLOOM_READ_ROOTS'].split(':')[0])"
_REPAIR_TASK = (
    " This is a bounded CPU execution-repair compatibility exercise. At EXECUTION, "
    "first submit the accepted DESIGN and captured implementation unchanged, then wait for its "
    "terminal outcome. If it fails, call get_experiment_log for that experiment and use its "
    "actual failure to repair the raw experiment source. Test the repair using the admitted "
    "environment unchanged, then submit with replaces_experiment_id naming the failed experiment "
    "and wait for the replacement's terminal outcome before the final execution response. "
    "Preserve the accepted local measurement, local parser and common evaluator. Read validation.json "
    "from the admitted data root and preserve the supplied labels and candidate prediction arrays. "
    "Use the candidate keyed by your exact role; no downloads or installations are needed."
)


def _paid_enabled(environment=None):
    values = os.environ if environment is None else environment
    return (values.get("HYPOLOOM_REAL_EXECUTION_REPAIR") == "1"
            and values.get("HYPOLOOM_REAL_CLAUDE") == "1")


class _ExecutionRouter(_DesignRouter):
    """Reuse adapter-handle ownership; select only EXECUTION for the second adapter."""

    def start(self, request, *, model, provider_environment, **kwargs):
        selected = request.command is _EXECUTION
        assert model == (self.design_model if selected else _MODEL)
        adapter = self.design if selected else self.local
        environment = self.design_environment if selected else provider_environment
        self.routes.append({"attempt_id": request.attempt_id, "command": request.command.value,
                            "route": "execution" if selected else "local", "model": model})
        handle = adapter.start(request, model=model, provider_environment=environment, **kwargs)
        self.owners[request.attempt_id] = adapter
        return handle


class _NonExecutionScenario(_AcceptanceScenario):
    """Install the observed raw-source bug through a native DESIGN edit before capture."""

    def record(self, document):
        request = _request_from_provider(document)
        assert request.command is not _EXECUTION, "EXECUTION reached the local prerequisite provider"
        number = super().record(document)
        if request.command is _DESIGN and self.steps[request.attempt_id] == 7:
            result = _tool_results(document)["baseline_runtime_bug"]
            if result.get("is_error"):
                self.errors.append("native DESIGN fixture did not install the raw-source bug")
        return number

    def tool_calls(self, number):
        request = self.current
        if request is not None and request.command is _DESIGN and self.steps[request.attempt_id] == 6:
            return (("baseline_runtime_bug", "Edit", {"file_path": "/workspace/experiment.py",
                "old_string": _GOOD_ROOT, "new_string": _BAD_ROOT}),)
        return super().tool_calls(number)


class _LocalExecutionScenario(_AcceptanceScenario):
    """Make tool choices only; observe real submit/wait/log/repair results in one invocation."""

    def __init__(self, root, nonce):
        super().__init__(root, nonce)
        self.phase = "submit"
        self.pending = None
        self.failed_experiment = None
        self.repaired_experiment = None
        self.tool_evidence = []

    def record(self, document):
        request = _request_from_provider(document)
        assert request.command is _EXECUTION, "non-EXECUTION node reached the execution provider"
        self.requests.append(document)
        self.current = request
        self.agent_requests[request.attempt_id] = request
        self.steps[request.attempt_id] += 1
        try:
            if request.attempt_id not in self.responses:
                assert len(self.agent_requests) == 1, "repair must stay within one execution Attempt"
                self.responses[request.attempt_id] = self.reviser.invoke(request)
                designs = [json.loads(item.content_bytes()) for item in request.input_artifacts
                    if item.ref.endswith("--response") and item.media_type == "application/json"
                    and json.loads(item.content_bytes()).get("command") == _DESIGN.value]
                assert len(designs) == 1 and designs[0]["status"] == "SUCCEEDED"
                self.designs[request.role] = copy.deepcopy(designs[0]["typed_candidate"])
            if self.pending is not None:
                identity, tool, arguments = self.pending
                result = _tool_results(document)[identity]
                assert not result.get("is_error"), result
                rendered = _result_text(result)
                self.tool_evidence.append({"attempt_id": request.attempt_id, "tool_use_id": identity,
                    "tool": tool, "arguments": arguments, "result": rendered})
                if self.phase == "submit":
                    self.failed_experiment = json.loads(rendered)["exp_id"]
                    self.phase = "wait_failure"
                elif self.phase == "wait_failure":
                    waited = json.loads(rendered)
                    if waited["terminal"]:
                        assert waited["outcome"]["outcome"] == "EXECUTION_FAILURE", waited
                        self.phase = "log"
                elif self.phase == "log":
                    assert "FileNotFoundError" in rendered and "validation.json" in rendered
                    self.phase = "read"
                elif self.phase == "read":
                    assert _BAD_ROOT in rendered
                    self.phase = "repair"
                elif self.phase == "repair":
                    self.phase = "self_test"
                elif self.phase == "self_test":
                    assert "EXPERT_RAW_AND_BOTH_PARSERS_CHECKED" in rendered
                    self.phase = "resubmit"
                elif self.phase == "resubmit":
                    self.repaired_experiment = json.loads(rendered)["exp_id"]
                    assert self.repaired_experiment != self.failed_experiment
                    self.phase = "wait_success"
                elif self.phase == "wait_success":
                    waited = json.loads(rendered)
                    if waited["terminal"]:
                        assert waited["outcome"]["outcome"] == "SUCCESS", waited
                        self.phase = "done"
                self.pending = None
        except Exception as exc:
            self.errors.append(f"execution fixture: {type(exc).__name__}: {exc}")
        return len(self.requests)

    def tool_calls(self, number):
        if self.current is None or self.errors:
            return ()
        request = self.current
        phase = self.phase
        if phase == "done":
            return self._structured(request)
        identity = f"execution_{phase}_{self.steps[request.attempt_id]}"
        if phase in {"submit", "resubmit"}:
            tool = "mcp__host-tools__submit_experiment"
            arguments = {"candidate": self.designs[request.role], "gpus": 0}
            if phase == "resubmit":
                arguments["replaces_experiment_id"] = self.failed_experiment
        elif phase in {"wait_failure", "wait_success"}:
            tool = "mcp__host-tools__wait_experiment"
            arguments = {"exp_id": self.failed_experiment if phase == "wait_failure" else self.repaired_experiment,
                         "max_wait_seconds": 5}
        elif phase == "log":
            tool, arguments = "mcp__host-tools__get_experiment_log", {"exp_id": self.failed_experiment}
        elif phase == "read":
            tool, arguments = "Read", {"file_path": "/workspace/experiment.py"}
        elif phase == "repair":
            tool, arguments = "Edit", {"file_path": "/workspace/experiment.py",
                                       "old_string": _BAD_ROOT, "new_string": _GOOD_ROOT}
        else:
            assert phase == "self_test"
            # Execute the accepted raw + both parsers with JSON READ_ROOTS
            # untouched; only parser output paths use private scratch.
            script = """set -e
mkdir -p /tmp/execution-repair-check
(cd /tmp/execution-repair-check && python3 /workspace/experiment.py)
HYPOLOOM_MEASUREMENT_RAW_ROOT=/tmp/execution-repair-check HYPOLOOM_MEASUREMENT_RESULT_PATH=/tmp/execution-repair-check/local.json python3 /workspace/parse_local.py
HYPOLOOM_MEASUREMENT_RAW_ROOT=/tmp/execution-repair-check HYPOLOOM_MEASUREMENT_RESULT_PATH=/tmp/execution-repair-check/goal.json python3 /acceptance/evaluate.py
python3 - <<'PY'
import json, os
from pathlib import Path
assert ':' in json.loads(os.environ['HYPOLOOM_READ_ROOTS'])[0]
local = json.loads(Path('/tmp/execution-repair-check/local.json').read_text())
goal = json.loads(Path('/tmp/execution-repair-check/goal.json').read_text())
assert local == {'metric': 'diagnostic_accuracy', 'value': '1'}
assert goal == {'measurements': {'suite_one_accuracy': '0.75', 'suite_two_accuracy': '0.75'}, 'checks': {'complete': True, 'single_candidate': True}}
print('EXPERT_RAW_AND_BOTH_PARSERS_CHECKED')
PY
"""
            tool, arguments = "Bash", {"command": script, "timeout": 10000}
        self.pending = (identity, tool, copy.deepcopy(arguments))
        return ((identity, tool, arguments),)


def _probe_payload(root, mount, run_id, execution_model, node_cost, *, paid):
    data = _fixture_pairs()
    # This fresh fixture's one candidate succeeds at both independent goals.
    # The metric, parser and common evaluator are the original C5 declarations.
    data["candidates"][_GOAL]["diagnostic"] = list(data["labels"])
    _payload(root, mount, roles=(_GOAL,), fixture_data=data)
    profile = root / "profile"
    methodology = yaml.safe_load((profile / "methodology.yaml").read_text())
    methodology["output_correction"] = {"max_failed_validations": 3}
    methodology["budget"].update(max_retries=2, max_experiments=2)
    if paid:
        methodology["budget"]["agent_invocation"].update(max_turns=64, max_tokens=150_000,
            max_output_bytes=1_048_576, wall_seconds=420, max_cost=str(node_cost))
    experts = yaml.safe_load((profile / "experts.yaml").read_text())
    experts["experts"][0]["stage_overrides"] = {"inner_loop.run": {"model": execution_model}}
    goal = yaml.safe_load((profile / "goal.yaml").read_text())
    goal["description"] += _REPAIR_TASK
    safety = yaml.safe_load((profile / "safety.yaml").read_text())
    if paid:
        safety["per_expert_budget_usd"] = safety["total_budget_usd"] = float(node_cost * 3 + 5)
    for name, document in (("methodology", methodology), ("experts", experts), ("goal", goal), ("safety", safety)):
        (profile / f"{name}.yaml").write_text(yaml.safe_dump(document))
    admitted = admit_profile(profile, run_id=run_id, allow_gpu=False)
    assert admitted.admitted, admitted.to_dict()
    _write_json(root / "profile-admission.json", admitted.to_dict())
    return admitted.create_run_payload


def _paid_adapter():
    # Even direct programmatic use cannot inspect settings without both flags.
    if not _paid_enabled():
        raise ValueError("EXECUTION repair requires both explicit paid opt-ins")
    try:
        cost = Decimal(os.environ["HYPOLOOM_EXECUTION_REPAIR_MAX_COST"])
    except (KeyError, InvalidOperation) as exc:
        raise ValueError("set an explicitly approved positive EXECUTION_REPAIR_MAX_COST") from exc
    if not cost.is_finite() or cost <= 0:
        raise ValueError("EXECUTION_REPAIR_MAX_COST must be finite and positive")
    executable = Path(os.environ["HYPOLOOM_CLAUDE_EXECUTABLE"])
    settings = Path(os.environ["HYPOLOOM_CLAUDE_SETTINGS"])
    if (not executable.is_absolute() or executable.is_symlink() or not executable.is_file()
            or not os.access(executable, os.X_OK)):
        raise ValueError("use the absolute resolved Claude executable")
    if (not settings.is_absolute() or settings.is_symlink() or not settings.is_file()
            or stat.S_IMODE(settings.stat().st_mode) != 0o600):
        raise ValueError("use the approved mode-0600 Claude settings file")
    model = validate_claude_model_selector(os.environ["HYPOLOOM_CLAUDE_MODEL"])
    adapter = ClaudeAgentHost(claude_executable=executable, settings_path=settings)
    adapter.preflight(model=model, provider_environment={})
    return adapter, model, cost


def _raw_tools(path):
    calls, results = [], {}
    for line in path.read_text().splitlines():
        record = json.loads(line)
        message = record.get("message", {})
        for block in message.get("content", ()):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                calls.append({"tool_use_id": block["id"], "tool": block["name"], "arguments": block["input"]})
            elif block.get("type") == "tool_result":
                results[block["tool_use_id"]] = block
    return [{**call, "result": results.get(call["tool_use_id"])} for call in calls]


def _final_output_index(calls, stdout_path):
    """Locate actual legacy-tool or ordinary-JSON completion after tool feedback."""
    legacy = [index for index, call in enumerate(calls) if call["tool"] == "StructuredOutput"]
    if legacy:
        return legacy[0]
    records = [json.loads(line) for line in stdout_path.read_text().splitlines()]
    terminal = records[-1]
    assert terminal.get("type") == "result" and terminal.get("subtype") == "success"
    assert terminal.get("is_error") is False
    text = terminal.get("result")
    assert isinstance(text, str) and isinstance(json.loads(text), dict)
    # Host acceptance above already checks the full node schema. Here the
    # assistant text, not a fabricated tool call, must precede that CLI result.
    completed = [index for index, record in enumerate(records[:-1])
        if record.get("type") == "assistant" and "\n".join(
            block["text"] for block in record.get("message", {}).get("content", ())
            if block.get("type") == "text") == text]
    assert completed, "successful CLI result lacks its actual assistant final JSON"
    boundary = completed[-1]
    for index, record in enumerate(records):
        for block in record.get("message", {}).get("content", ()):
            if block.get("type") in {"tool_use", "tool_result"}:
                assert index < boundary, "final JSON preceded execution tool completion"
    return len(calls)


def _accepted_submit_indices(calls, submissions):
    """Match successful tool feedback to existing admitted experiment identities."""
    expected = [item["experiment_id"] for item in submissions]
    assert len(expected) == len(set(expected)) == 2
    accepted, returned = [], []
    for index, call in enumerate(calls):
        if call["tool"] != "mcp__host-tools__submit_experiment":
            continue
        result = call.get("result")
        assert isinstance(result, dict) and isinstance(result.get("is_error", False), bool), "submit feedback missing or unknown"
        content = _result_text(result)
        if result.get("is_error", False):
            assert content.strip(), "rejected submit lacks actual feedback"
            continue
        try:
            response = json.loads(content)
        except (TypeError, ValueError) as exc:
            raise AssertionError("successful submit lacks a readable experiment identity") from exc
        assert isinstance(response, dict) and response.get("exp_id") in expected, "submit identity differs from durable admission"
        accepted.append(index)
        returned.append(response["exp_id"])
    assert returned == expected, "accepted submit results must match exactly two durable experiments"
    for index, submitted in zip(accepted, submissions):
        assert calls[index]["arguments"]["candidate"] == submitted["candidate"], "submitted candidate differs from durable capture"
    return accepted


def _run_probe(root, *, execution_adapter=None, execution_model=_MODEL, node_cost=Decimal("0.5")):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    assert not (root / "state").exists(), "probe requires a fresh absent state root"
    paid = execution_adapter is not None
    run_id = "execution-repair-" + secrets.token_hex(8)
    nonce = "execution-repair-fixture-" + secrets.token_hex(12)
    local = _NonExecutionScenario(root, nonce)
    local_key = "local-only-" + secrets.token_hex(16)
    local_server, local_thread = _start_provider(local, local_key)
    execution_scenario = execution_server = execution_thread = host = None
    evidence = {"kind": "mixed_provider_execution_repair_probe", "paid_execution": paid,
        "g5": False, "scientific_success": False, "gpu": False, "run_id": run_id,
        "execution_node_max_cost": str(node_cost), "natural_repair_closure": False}
    try:
        settings = root / "settings.json"
        settings.write_text("{}\n")
        settings.chmod(0o600)
        executable = execution_adapter.claude_executable if paid else _installed_claude()
        host = _new_host(root, executable, f"http://127.0.0.1:{local_server.server_port}", local_key)
        execution_environment = {}
        if not paid:
            execution_scenario = _LocalExecutionScenario(root, nonce)
            execution_key = "execution-local-only-" + secrets.token_hex(16)
            execution_server, execution_thread = _start_provider(execution_scenario, execution_key)
            execution_environment = {"ANTHROPIC_API_KEY": execution_key,
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{execution_server.server_port}"}
            execution_adapter = ClaudeAgentHost(claude_executable=executable, settings_path=settings)
        router = _ExecutionRouter(host.claude_agent_host, execution_adapter, execution_environment, execution_model)
        host.claude_agent_host = router
        host.grounding_web = _NoPublicWeb()
        mount = Path(tempfile.mkdtemp(prefix="prom-execution:probe-", dir="/var/tmp"))
        evidence["fixture_root"] = str(mount)
        payload = _probe_payload(root, mount, run_id, execution_model, node_cost, paid=paid)
        unchanged = copy.deepcopy(payload)
        host.initialize()
        created = host.handle(ControlRequest(1, run_id + "-create", 0, "CREATE_RUN", payload))
        assert created.status == "accepted", created
        deadline = time.monotonic() + (1380 if paid else 240)
        while time.monotonic() < deadline:
            host.tick()
            errors = local.errors + ([] if execution_scenario is None else execution_scenario.errors)
            if errors:
                evidence["fixture_errors"] = errors
                break
            if host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(0.01)
        assert payload == unchanged, "admitted configuration changed during execution repair"
        attempts = host.store.list_attempts()
        agents = [(row, json.loads(row["observation_json"])) for row in attempts if row["kind"] == "AGENT"]
        evidence["routes"] = router.routes
        evidence["agent_count"] = len(agents)
        evidence["head"] = dict(host.store.latest_transition(run_id))
        evidence["open_attempt_ids"] = [row["attempt_id"] for row in attempts if row["state"] == "OPEN"]
        evidence["process_attempts"] = [{key: row[key] for key in
            ("attempt_id", "experiment_id", "state", "terminal_reason", "process_group_id")}
            for row in attempts if row["kind"] == "PROCESS"]
        evidence["diagnostics_root"] = str(root / "state/diagnostics/agents" / run_id)
        evidence["execution_attempts"] = []
        for row, observed in agents:
            if observed["request"]["command"] != _EXECUTION.value:
                continue
            private = Path(evidence["diagnostics_root"]) / row["attempt_id"]
            evidence["execution_attempts"].append({"attempt_id": row["attempt_id"],
                "terminal_reason": row["terminal_reason"], "session": observed.get("session"),
                "diagnostics": observed.get("diagnostics"),
                "raw_tools": _raw_tools(private / "stdout.log") if (private / "stdout.log").exists() else []})
        evidence["design_attempts"] = [{"attempt_id": row["attempt_id"], "terminal_reason": row["terminal_reason"],
            "session": observed.get("session"), "candidate": observed.get("response", {}).get("typed_candidate")}
            for row, observed in agents if observed["request"]["command"] == _DESIGN.value]
        evidence["specs"] = host.store.list_facts(run_id, "ExperimentPreregistered")
        evidence["outcomes"] = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        evidence["verdicts"] = host.store.list_facts(run_id, "GoalVerdictRecorded")
        evidence["submissions"] = []
        for spec in evidence["specs"]:
            command_id = spec["payload"]["execution_contract"]["source_submit_command_id"]
            rows = host.store.inspect("SELECT request_json FROM workflow_commands WHERE command_id=?", (command_id,))
            submitted = json.loads(rows[0]["request_json"])
            source = submitted["source_snapshot"]["arguments"]
            evidence["submissions"].append({"command_id": command_id, "experiment_id": spec["experiment_id"],
                "candidate": source["candidate"], "workspace": {item["path"]:
                    WorkspaceArtifact.from_dict(item).content_bytes().decode() for item in source["workspace_files"]}})
        evidence["charges"] = [{"attempt_id": charge.attempt_id, "source": charge.source,
            "cost": None if charge.cost is None else str(charge.cost)} for charge in fold_agent_attempts(attempts)]
        evidence["oracle"] = Oracle(host.store.database_path, host.publisher.root).audit(run_id).to_dict()
        ground = next((observed for _, observed in agents if observed["request"]["command"] == "GROUNDING"
                       and observed.get("response", {}).get("status") == "SUCCEEDED"), None)
        evidence["common_source_unchanged"] = bool(ground and (
            host.publisher.root / ground["session"]["transcript"]["path"] / "evaluator/evaluate.py"
        ).read_text() == _common_source(nonce))
        evidence["natural_repair_closure"] = (evidence["head"]["state"] == "COMPLETED"
            and not evidence["open_attempt_ids"] and evidence["oracle"]["ok"]
            and [item["payload"]["outcome"] for item in evidence["outcomes"]] == ["EXECUTION_FAILURE", "SUCCESS"])
        evidence["local_request_commands"] = sorted({r.command.value for r in local.agent_requests.values()})
    finally:
        groups = [] if host is None or not host.store.database_path.exists() else [row["process_group_id"]
            for row in host.store.list_attempts() if row["process_group_id"] is not None]
        if host is not None:
            host.close()
        evidence["owned_process_residue_after_close"] = [pid for pid in groups if process_group_exists(pid)]
        _write_json(root / "execution-repair-probe.json", evidence)
        local_server.shutdown()
        local_server.server_close()
        local_thread.join(timeout=5)
        if execution_server is not None:
            execution_server.shutdown()
            execution_server.server_close()
            execution_thread.join(timeout=5)
    return evidence


def _assert_repair(evidence):
    assert not evidence.get("fixture_errors"), evidence.get("fixture_errors")
    assert evidence["natural_repair_closure"], "inspect execution-repair-probe.json and raw diagnostics"
    assert not evidence["owned_process_residue_after_close"]
    assert _EXECUTION.value not in evidence["local_request_commands"]
    assert len(evidence["execution_attempts"]) == len(evidence["design_attempts"]) == 1
    execution, design = evidence["execution_attempts"][0], evidence["design_attempts"][0]
    assert execution["terminal_reason"] == design["terminal_reason"] == "SUCCEEDED"
    assert execution["session"]["session_id"] != design["session"]["session_id"]
    assert execution["session"]["branch"] == design["session"]["branch"]
    assert execution["session"]["launch_arguments"] == ["--session-id", execution["session"]["session_id"]]
    assert set(design["candidate"]) == _DESIGN_FIELDS
    first, repaired = evidence["submissions"]
    assert first["candidate"] == design["candidate"]
    assert {key: value for key, value in first["candidate"].items() if key != "method"} == {
        key: value for key, value in repaired["candidate"].items() if key != "method"}
    assert first["candidate"]["measurement"] == _local_measurement().to_dict()
    assert first["workspace"]["experiment.py"] == _experiment_files(_GOAL)["experiment.py"].decode().replace(_GOOD_ROOT, _BAD_ROOT)
    assert repaired["workspace"]["experiment.py"] != first["workspace"]["experiment.py"]
    assert first["workspace"]["parse_local.py"] == repaired["workspace"]["parse_local.py"] == _experiment_files(_GOAL)["parse_local.py"].decode()
    assert evidence["common_source_unchanged"]
    specs = evidence["specs"]
    assert len(specs) == len(evidence["process_attempts"]) == 2
    assert {item["experiment_id"] for item in evidence["submissions"]} == {
        item["experiment_id"] for item in specs} == {
        item["experiment_id"] for item in evidence["process_attempts"]}
    assert [item["command_id"] for item in evidence["submissions"]] == [
        item["payload"]["execution_contract"]["source_submit_command_id"] for item in specs]
    assert [item["terminal_reason"] for item in evidence["process_attempts"]] == ["FAILED", "SUCCEEDED"]
    assert {spec["payload"]["execution_contract"]["source_agent_attempt_id"] for spec in specs} == {execution["attempt_id"]}
    assert len({spec["payload"]["execution_contract"]["source_submit_command_id"] for spec in specs}) == 2
    assert all(spec["payload"]["goal_evaluation"]["acceptance"] == _DECLARATION for spec in specs)
    assert evidence["outcomes"][1]["payload"]["goal_evaluation"]["measurements"] == {
        "suite_one_accuracy": "0.75", "suite_two_accuracy": "0.75"}
    assert evidence["outcomes"][1]["payload"]["metric_value"] == "1"
    assert any(item["payload"]["verdict"] == "GOAL_MET" for item in evidence["verdicts"])
    calls = execution["raw_tools"]
    submits = _accepted_submit_indices(calls, evidence["submissions"])
    logs = [index for index, call in enumerate(calls) if call["tool"] == "mcp__host-tools__get_experiment_log"]
    assert len(submits) == 2 and logs and submits[0] < logs[0] < submits[1]
    assert calls[submits[1]]["arguments"]["replaces_experiment_id"] == first["experiment_id"]
    assert calls[logs[0]]["arguments"]["exp_id"] == first["experiment_id"]
    assert "FileNotFoundError" in json.dumps(calls[logs[0]]["result"])
    final_output = _final_output_index(calls,
        Path(evidence["diagnostics_root"]) / execution["attempt_id"] / "stdout.log")
    assert submits[1] < final_output
    for experiment, start, end, outcome in (
        (first, submits[0], logs[0], "EXECUTION_FAILURE"),
        (repaired, submits[1], final_output, "SUCCESS"),
    ):
        waits = [call for call in calls[start + 1:end]
            if call["tool"] == "mcp__host-tools__wait_experiment"
            and call["arguments"]["exp_id"] == experiment["experiment_id"]]
        terminal = [json.loads(_result_text(call["result"])) for call in waits
                    if call["result"] is not None and not call["result"].get("is_error")]
        assert any(item.get("terminal") is True and item.get("outcome", {}).get("outcome") == outcome
                   for item in terminal), (experiment["experiment_id"], outcome, waits)
    oracle = evidence["oracle"]
    assert oracle["canonical_outcome_count"] == 2
    assert oracle["workflow_state_counts"].get("TERMINAL") == 2
    assert oracle["nonterminal_experiment_count"] == 0
    assert oracle["open_attempt_count"] == oracle["pending_effect_count"] == oracle["duplicate_effect_count"] == 0
    assert all(item["route"] == ("execution" if item["command"] == _EXECUTION.value else "local")
               for item in evidence["routes"])


def test_execution_paid_adapter_requires_both_optins_before_any_settings_access(monkeypatch):
    for name in ("HYPOLOOM_REAL_EXECUTION_REPAIR", "HYPOLOOM_REAL_CLAUDE"):
        monkeypatch.delenv(name, raising=False)
    assert not _paid_enabled({})
    for flag in ("HYPOLOOM_REAL_EXECUTION_REPAIR", "HYPOLOOM_REAL_CLAUDE"):
        assert not _paid_enabled({flag: "1"})
        monkeypatch.setenv(flag, "1")
        with pytest.raises(ValueError, match="both explicit paid opt-ins"):
            _paid_adapter()
        monkeypatch.delenv(flag)
    assert _paid_enabled({"HYPOLOOM_REAL_EXECUTION_REPAIR": "1", "HYPOLOOM_REAL_CLAUDE": "1"})


def _completion_records():
    text = json.dumps({"typed_candidate": {}, "declared_effects": []})
    return [
        {"type": "assistant", "message": {"content": [{"type": "tool_use",
            "id": "wait", "name": "mcp__host-tools__wait_experiment", "input": {}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result",
            "tool_use_id": "wait", "content": "terminal SUCCESS"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": text},
    ]


def test_execution_completion_observes_actual_normal_json_after_tool_feedback(tmp_path):
    stdout = tmp_path / "stdout.log"
    stdout.write_text("\n".join(map(json.dumps, _completion_records())))
    calls = _raw_tools(stdout)
    assert [call["tool"] for call in calls] == ["mcp__host-tools__wait_experiment"]
    assert _final_output_index(calls, stdout) == 1


@pytest.mark.parametrize("failure", ["missing-text", "different-text", "premature-text",
    "late-tool", "failed-result", "error-result", "non-json"])
def test_execution_completion_rejects_missing_or_misordered_normal_output(tmp_path, failure):
    records = _completion_records()
    if failure == "missing-text":
        records.pop(2)
    elif failure == "different-text":
        records[2]["message"]["content"][0]["text"] = "{}"
    elif failure == "premature-text":
        records[1], records[2] = records[2], records[1]
    elif failure == "late-tool":
        records.insert(-1, copy.deepcopy(records[0]))
    elif failure == "failed-result":
        records[-1]["subtype"] = "error_max_structured_output_retries"
    elif failure == "error-result":
        records[-1]["is_error"] = True
    else:
        records[2]["message"]["content"][0]["text"] = records[-1]["result"] = "not JSON"
    stdout = tmp_path / "stdout.log"
    stdout.write_text("\n".join(map(json.dumps, records)))
    with pytest.raises((AssertionError, ValueError)):
        _final_output_index(_raw_tools(stdout), stdout)


def test_execution_completion_preserves_retained_legacy_tool_boundary(tmp_path):
    calls = [{"tool": "mcp__host-tools__wait_experiment"}, {"tool": "StructuredOutput"}]
    assert _final_output_index(calls, tmp_path / "unused-normal-output.log") == 1


def test_execution_paid_profile_preparation_requires_no_credentials(tmp_path):
    root, mount = tmp_path / "probe", tmp_path / "fixture:colon"
    root.mkdir()
    mount.mkdir()
    payload = _probe_payload(root, mount, "execution-paid-preparation", "mimo-v2.5-pro", Decimal("2"), paid=True)
    contract = payload["research_contract"]
    product = contract["methodology"]["controls"]["product"]
    assert product["experts"]["experts"][0]["stage_overrides"] == {"inner_loop.run": {"model": "mimo-v2.5-pro"}}
    assert product["experts"]["defaults"]["model"] == _MODEL
    assert contract["budget"]["max_experiments"] == 2
    assert contract["budget"]["agent_invocation"]["max_turns"] == 64
    assert contract["budget"]["agent_invocation"]["wall_seconds"] == 420
    assert contract["budget"]["agent_invocation"]["max_cost"] == "2"
    assert product["methodology"]["output_correction"] == {"max_failed_validations": 3}
    assert ":" in payload["execution"]["read_roots"][0]
    assert not payload["execution"]["allow_gpu"] and not (root / "state").exists()


def _submit_feedback_fixture(rejections=0):
    submissions = [{"experiment_id": name, "candidate": {"method": "same accepted method"}}
                   for name in ("initial", "replacement")]
    calls = [{"tool": "mcp__host-tools__submit_experiment", "arguments": {"candidate": {}},
              "result": {"is_error": True, "content": "invalid_schema: missing candidate fields"}}
             for _ in range(rejections)]
    calls.extend({"tool": "mcp__host-tools__submit_experiment", "arguments": {"candidate": copy.deepcopy(item["candidate"])},
                  "result": {"content": json.dumps({"exp_id": item["experiment_id"], "queue_position": 0})}}
                 for item in submissions)
    return calls, submissions


@pytest.mark.parametrize("rejections", [0, 2])
def test_rejected_submit_feedback_does_not_count_as_an_admitted_experiment(rejections):
    calls, submissions = _submit_feedback_fixture(rejections)
    assert _accepted_submit_indices(calls, submissions) == [rejections, rejections + 1]


@pytest.mark.parametrize("failure", ["missing", "unknown", "misbound", "duplicate", "candidate"])
def test_submit_feedback_requires_complete_distinct_durable_admissions(failure):
    calls, submissions = _submit_feedback_fixture(2)
    if failure == "missing":
        calls[0]["result"] = None
    elif failure == "unknown":
        calls[2]["result"] = {"content": "unclassified response"}
    elif failure == "misbound":
        calls[2]["result"] = {"content": json.dumps({"exp_id": "unrelated"})}
    elif failure == "duplicate":
        calls.append(copy.deepcopy(calls[2]))
    else:
        calls[2]["arguments"]["candidate"]["method"] = "different from durable capture"
    with pytest.raises(AssertionError):
        _accepted_submit_indices(calls, submissions)


def test_local_execution_repairs_real_cpu_failure_in_one_native_attempt(tmp_path):
    evidence = _run_probe(tmp_path)
    assert not evidence["paid_execution"]
    _assert_repair(evidence)


@pytest.mark.skipif(not _paid_enabled(), reason="EXECUTION-only paid probe requires both explicit opt-ins and approved auth/model/cost")
def test_real_execution_repairs_from_actual_cpu_log(tmp_path):
    adapter, model, cost = _paid_adapter()
    _assert_repair(_run_probe(tmp_path, execution_adapter=adapter, execution_model=model, node_cost=cost))
