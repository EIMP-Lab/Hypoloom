"""Offline checks of the separate paid common-acceptance canary opt-in."""

from __future__ import annotations

import copy
import json
from collections import deque
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from hypoloom.agent_aci import CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION, EmbeddedInput, WorkspaceArtifact, required_output_schema
from hypoloom.domain import AgentCommand, ResearchContract, fact_to_dict
from hypoloom.fake_agent import default_request
from test_domain import all_facts
import test_real_current_host_g5 as canary


@pytest.mark.parametrize("old,common,real,expected", [
    (None, None, "1", (False, False)),
    ("1", None, "1", (True, False)),
    (None, "1", "1", (False, True)),
    ("1", "1", "1", (False, False)),
    ("1", None, None, (False, False)),
    (None, "1", None, (False, False)),
])
def test_common_g5_opt_in_never_double_launches_or_inherits_old_permission(old, common, real, expected):
    values = {"HYPOLOOM_REAL_CURRENT_G5": old,
        "HYPOLOOM_REAL_CURRENT_COMMON_G5": common,
        "HYPOLOOM_REAL_CLAUDE": real}
    assert tuple(canary._paid_canary_enabled(mode, values) for mode in (False, True)) == expected


def test_common_g5_commission_uses_same_limits_and_target_repair_without_a_prebuilt_evaluator(tmp_path):
    old_profile, old = canary._profile(tmp_path / "old", "approved-model", Decimal("4.25"))
    profile, payload = canary._profile(tmp_path / "common", "approved-model", Decimal("4.25"), common_acceptance=True)
    original = yaml.safe_load((old_profile / "goal.yaml").read_text())
    goal = yaml.safe_load((profile / "goal.yaml").read_text())
    assert original["metric"] == {"name": "accuracy", "direction": "maximize", "target": 0.875}
    assert "evaluation" not in original
    assert goal["evaluation"] == "grounding" and {"metric", "completion", "success"}.isdisjoint(goal)
    for phrase in ("accuracy >= 0.875", "/acceptance/evaluate.py", "test_evaluate.py", "GOAL",
        "never hardcode", "computed_accuracy", "complete_pairs", "computed_accuracy_matches",
        "python -m unittest -v test_accuracy.py", "HYPOLOOM_TARGET_ROOT"):
        assert phrase in goal["description"]
    contract = ResearchContract.from_mapping(payload["research_contract"])
    assert contract.document["schema_version"] == 3
    assert contract.document["goal"]["evaluation"] == {"mode": "grounding"}
    assert contract.components[0].metric is None
    assert contract.document["methodology"]["controls"]["product"]["methodology"][
        "output_correction"
    ] == {"max_failed_validations": 3}
    template = yaml.safe_load((Path(__file__).resolve().parents[2]
        / "profiles/templates/multi-expert-en/methodology.yaml").read_text())
    local_contract = ResearchContract.from_mapping(old["research_contract"])
    assert local_contract.document["methodology"]["controls"]["product"]["methodology"][
        "output_correction"
    ] == template["output_correction"]
    assert contract.document["budget"] == old["research_contract"]["budget"]
    assert goal["stop_conditions"] == original["stop_conditions"]
    assert payload["run_id"] == f"current-aci{CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION}-paid-canary-common"
    for name in ("experts", "methodology", "safety", "notify", "language"):
        document = yaml.safe_load((profile / f"{name}.yaml").read_text())
        if name == "safety":
            assert document["gpus"] == [] and document["experiment_timeout_minutes"] == 0.5
        if name == "notify":
            assert document == {"sinks": {}, "events": {}}
    target = payload["execution"]["target"]["checkout"]
    assert "return 0.0" in (Path(target) / "accuracy.py").read_text()
    assert not list(Path(target).rglob("evaluate.py"))
    assert not list(tmp_path.rglob("state.sqlite"))


def test_common_g5_commission_requires_reading_the_actual_mounted_source(tmp_path):
    profile, _ = canary._profile(tmp_path, "approved-model", common_acceptance=True)
    description = yaml.safe_load((profile / "goal.yaml").read_text())["description"]
    assert "Read or Bash cat /acceptance/evaluate.py" in description
    assert "actual mounted file, not an embedded source literal" in description


@pytest.mark.parametrize("common", [False, True])
def test_current_g5_commission_requests_native_read_coverage(tmp_path, common):
    profile, _ = canary._profile(tmp_path, "approved-model", common_acceptance=common)
    description = yaml.safe_load((profile / "goal.yaml").read_text())["description"]
    assert "invoke native Read with file_path=/workspace/accuracy.py" in description


@pytest.mark.parametrize("common", [False, True])
def test_current_g5_commission_uses_version_neutral_submitted_target_wording(tmp_path, common):
    profile, _ = canary._profile(tmp_path, "approved-model", common_acceptance=common)
    description = yaml.safe_load((profile / "goal.yaml").read_text())["description"]
    assert "current submitted-target path" in description
    assert "research-v9" not in description


def test_common_g5_wrapper_reuses_the_existing_runner_without_preflight(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(canary, "_run_current_host_canary", lambda root, **kwargs: calls.append((root, kwargs)))
    canary.test_real_current_host_closes_tools_cpu_sessions_and_reports(tmp_path)
    canary.test_real_current_common_host_closes_grounding_source_goal_and_reports(tmp_path)
    assert calls == [(tmp_path, {}), (tmp_path, {"common_acceptance": True})]


def test_common_g5_explicit_allocation_does_not_invent_prior_cohort_billing(monkeypatch):
    calls = []
    adapter = SimpleNamespace(settings_path=Path("/unused-settings"), preflight=lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(canary, "_preflight", lambda: pytest.fail("fresh allocation must not use legacy billing"))
    actual = canary._canary_launch(adapter, "approved-model", Decimal("1000"))
    assert actual == (adapter, "approved-model", None, Decimal("1000"))
    assert calls == [{"model": "approved-model"}]
    accounting = canary._canary_accounting(None, Decimal("1.25"))
    assert accounting == {
        "budget_scope": "fresh_allocation",
        "prior_charged_usd": None,
        "cohort_charged_lower_bound_usd": None,
    }


def test_common_g5_default_launch_still_requires_the_original_prior_preflight(monkeypatch):
    monkeypatch.setenv("HYPOLOOM_G5_CUMULATIVE_LIMIT_USD", "20")
    adapter = object()
    monkeypatch.setattr(canary, "_preflight", lambda: (adapter, "old-model", Decimal("4.25")))
    assert canary._canary_launch(None, None, None) == (adapter, "old-model", Decimal("4.25"), Decimal("15.75"))
    assert canary._canary_accounting(Decimal("4.25"), Decimal("1.25")) == {
        "budget_scope": "cohort_cumulative", "prior_charged_usd": "4.25", "cohort_charged_lower_bound_usd": "5.50"}


@pytest.mark.parametrize("adapter,model,allocation", [
    (None, "approved-model", "1000"), (object(), None, "1000"),
    (object(), "approved-model", None),
    (object(), "approved-model", "0"), (object(), "approved-model", "-1"),
    (object(), "approved-model", "NaN"), (object(), "approved-model", "Infinity"),
])
def test_common_g5_incomplete_or_invalid_explicit_launch_never_preflights(monkeypatch, adapter, model, allocation):
    monkeypatch.setattr(canary, "_preflight", lambda: pytest.fail("must reject before preflight"))
    with pytest.raises(ValueError):
        canary._canary_launch(adapter, model, allocation)


def test_common_g5_profile_accepts_explicit_finite_technical_limits(tmp_path):
    node = replace(canary._NODE_BUDGET, max_turns=512, max_tokens=10_000_000,
        wall_seconds=3600, max_cost="100", token_enforcement_mode="observe")
    profile, payload = canary._profile(tmp_path / "custom", "approved-model", common_acceptance=True,
        fresh_allocation_usd=Decimal("1000"), node_budget=node, run_wall_seconds=86400, run_turns=10000)
    budget = payload["research_contract"]["budget"]
    assert budget["agent_invocation"] == node.to_dict()
    assert budget["max_turns"] == 10000
    assert budget["total_budget_usd"] == budget["per_expert_budget_usd"] == "1000"
    assert yaml.safe_load((profile / "goal.yaml").read_text())["stop_conditions"]["max_wall_clock_hours"] == 24
    safety = yaml.safe_load((profile / "safety.yaml").read_text())
    assert safety["gpus"] == [] and safety["bash"]["network_allowed"] is False
    assert budget["max_active_agents"] == budget["max_active_processes"] == 1


def test_common_g5_revision_budget_is_explicit_and_default_remains_one_cycle(tmp_path):
    default_profile, default = canary._profile(tmp_path / "default", "approved-model", common_acceptance=True)
    profile, payload = canary._profile(tmp_path / "revision", "approved-model", common_acceptance=True,
        max_rounds_per_expert=2, max_experiments=4)
    assert yaml.safe_load((default_profile / "goal.yaml").read_text())["stop_conditions"]["max_rounds_per_expert"] == 1
    assert default["research_contract"]["budget"]["max_experiments"] == 2
    assert yaml.safe_load((profile / "goal.yaml").read_text())["stop_conditions"]["max_rounds_per_expert"] == 2
    assert payload["research_contract"]["budget"]["max_experiments"] == 4
    for key in ("max_active_hypotheses", "max_active_agents", "max_active_processes"):
        assert payload["research_contract"]["budget"][key] == 1
    assert not list(tmp_path.rglob("state.sqlite"))


@pytest.mark.parametrize("changes", [
    {"run_wall_seconds": 0}, {"run_wall_seconds": float("inf")}, {"run_wall_seconds": True},
    {"run_turns": 0}, {"run_turns": 2.5}, {"run_turns": True},
    {"fresh_allocation_usd": "NaN"}, {"fresh_allocation_usd": "0"},
    {"node_budget": replace(canary._NODE_BUDGET, max_cost="0")},
    {"max_rounds_per_expert": 0}, {"max_rounds_per_expert": 1.5}, {"max_rounds_per_expert": True},
    {"max_experiments": 0}, {"max_experiments": 2.5}, {"max_experiments": True},
])
def test_common_g5_invalid_technical_limits_do_not_create_a_fixture(tmp_path, changes):
    root = tmp_path / "invalid"
    with pytest.raises(ValueError):
        canary._profile(root, "approved-model", **changes)
    assert not root.exists()


@pytest.mark.parametrize("states", [["RUNNING", "COMPLETED"], ["WAITING_HUMAN", "COMPLETED"]])
def test_common_g5_progress_callback_observes_ticks_and_human_wait_never_dispatches_again(monkeypatch, states):
    remaining, ticks, observed = deque(states), [], []
    head = {"state": "CREATED"}
    def tick():
        head["state"] = remaining.popleft()
        ticks.append(head["state"])
    host = SimpleNamespace(tick=tick, store=SimpleNamespace(latest_transition=lambda _: head))
    def progress(actual, run_id):
        assert actual is host and run_id == "run"
        observed.append(head["state"])
    monkeypatch.setattr(canary.time, "sleep", lambda _: None)
    if states[0] == "WAITING_HUMAN":
        with pytest.raises(RuntimeError, match="WAITING_HUMAN"):
            canary._drive_canary(host, "run", 60, progress)
        assert ticks == observed == ["WAITING_HUMAN"] and list(remaining) == ["COMPLETED"]
    else:
        canary._drive_canary(host, "run", 60, progress)
        assert ticks == observed == states


@pytest.mark.parametrize("signal", ["operational_halt", "stop_requested"])
@pytest.mark.parametrize("already_set", [False, True])
def test_common_g5_explicit_host_stop_exits_without_another_dispatch(monkeypatch, signal, already_set):
    ticks, observed = [], []
    def set_stop():
        setattr(host, signal, "fixture fatal SQLite error" if signal == "operational_halt" else True)
    def tick():
        ticks.append("tick")
        assert len(ticks) == 1, "a halted Host must not dispatch again"
        set_stop()
    host = SimpleNamespace(tick=tick, operational_halt=None, stop_requested=False,
        _backup_busy_reason=lambda: None,
        store=SimpleNamespace(latest_transition=lambda _: {"state": "ACTIVE"}))
    if already_set:
        set_stop()
    monkeypatch.setattr(canary.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match=rf"ACTIVE.*{signal}"):
        canary._drive_canary(host, "run", 60, lambda *_: observed.append("callback"))
    assert len(ticks) == (0 if already_set else 1) and observed == ["callback"]


@pytest.mark.parametrize("stop_requested,busy_reason", [
    (False, None), (True, "in-memory process or Agent attempt is running"),
    (True, "durable attempt remains OPEN: fixture"),
])
def test_common_g5_identical_active_snapshots_are_not_a_stall_signal(monkeypatch, stop_requested, busy_reason):
    ticks = []
    host = SimpleNamespace(tick=lambda: ticks.append("tick"), operational_halt=None,
        stop_requested=stop_requested, _backup_busy_reason=lambda: busy_reason,
        store=SimpleNamespace(latest_transition=lambda _: {"state": "ACTIVE"}))
    def progress(*_):
        if len(ticks) == 3:
            raise RuntimeError("caller ends this offline check")
    monkeypatch.setattr(canary.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="caller ends"):
        canary._drive_canary(host, "run", 60, progress)
    assert len(ticks) == 3


@pytest.mark.parametrize("reason", ["WAITING_HUMAN", "caller detected no progress", "operational_halt", "stop_requested", "KeyboardInterrupt"])
def test_common_g5_explicit_runner_preserves_pause_evidence_without_another_dispatch(monkeypatch, tmp_path, reason):
    calls = []
    node = replace(canary._NODE_BUDGET, max_turns=512, max_tokens=10_000_000,
        wall_seconds=3600, max_cost="100", token_enforcement_mode="observe")
    adapter = SimpleNamespace(settings_path=Path("/unused-settings"), preflight=lambda **kw: calls.append(("preflight", kw)))
    class PausedHost:
        def __init__(self, root, **kwargs):
            assert kwargs["claude_agent_host"] is adapter and kwargs["claude_model"] == "approved-model"
            self.operational_halt = None
            self.stop_requested = False
            self.publisher = SimpleNamespace(root=root / "artifacts")
            self.store = SimpleNamespace(
                get_run=lambda _: {"agent_aci_version": str(CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION)},
                latest_transition=lambda _: {"state": "WAITING_HUMAN" if reason == "WAITING_HUMAN" else "ACTIVE"},
                list_attempts=lambda: [], database_path=root / "state.sqlite")
        def initialize(self):
            calls.append(("initialize",))
        def handle(self, request):
            assert request.command == "CREATE_RUN"
            assert request.payload["research_contract"]["budget"]["agent_invocation"] == node.to_dict()
            assert request.payload["research_contract"]["budget"]["max_experiments"] == 4
            product = request.payload["research_contract"]["methodology"]["controls"]["product"]
            assert product["goal"]["stop_conditions"]["max_rounds_per_expert"] == 2
            calls.append(("create",))
            return SimpleNamespace(status="accepted")
        def tick(self):
            calls.append(("tick",))
            if reason == "operational_halt":
                self.operational_halt = "fixture fatal SQLite error"
            self.stop_requested = reason in {"operational_halt", "stop_requested"}
        def _backup_busy_reason(self):
            return None
        def close(self):
            calls.append(("close",))
    def progress(host, run_id):
        calls.append(("progress", host.store.latest_transition(run_id)["state"]))
        if reason == "KeyboardInterrupt":
            raise KeyboardInterrupt
        if reason == "caller detected no progress":
            raise RuntimeError(reason)
    monkeypatch.setattr(canary, "WorkflowHost", PausedHost)
    monkeypatch.setattr(canary, "Oracle", lambda *_: SimpleNamespace(
        audit=lambda _: SimpleNamespace(to_dict=lambda: {"ok": False, "fixture": "paused, not qualified"})))
    monkeypatch.setattr(canary, "_preflight", lambda: pytest.fail("must not reset/reuse old allocation"))
    with pytest.raises(KeyboardInterrupt if reason == "KeyboardInterrupt" else RuntimeError,
        match=None if reason == "KeyboardInterrupt" else reason):
        canary._run_current_host_canary(tmp_path, common_acceptance=True, adapter=adapter,
            model="approved-model", fresh_allocation_usd=Decimal("1000"), node_budget=node,
            run_wall_seconds=86400, run_turns=10000, progress_callback=progress,
            max_rounds_per_expert=2, max_experiments=4)
    assert [item[0] for item in calls] == ["preflight", "initialize", "create", "tick", "progress", "close"]
    evidence = json.loads((tmp_path / "current-g5-evidence.json").read_text())
    assert evidence["budget_scope"] == "fresh_allocation" and evidence["cohort_charged_lower_bound_usd"] is None
    assert evidence["prior_charged_usd"] is None
    assert evidence["run_state"] == ("WAITING_HUMAN" if reason == "WAITING_HUMAN" else "ACTIVE")
    if reason == "KeyboardInterrupt":
        assert evidence["canary_failure"] == {"type": "KeyboardInterrupt", "message": ""}
    else:
        assert evidence["canary_failure"]["type"] == "RuntimeError" and reason in evidence["canary_failure"]["message"]
    assert evidence["limits"] == {"cohort_budget_ceiling_usd": None, "prior_charged_usd": None,
        "total_reported_usd": "1000", "per_expert_reported_usd": "1000", "run_turns": 10000,
        "run_wall_seconds": 86400, "node": node.to_dict(),
        "max_rounds_per_expert": 2, "max_experiments": 4}
    assert evidence["oracle"]["ok"] is False and evidence["common_acceptance"] is None


@pytest.mark.parametrize("guard", ["no-settings", "settings-mode", "executable-symlink"])
def test_common_g5_injected_adapter_keeps_existing_file_permission_guards(tmp_path, guard):
    executable, settings = tmp_path / "cc", tmp_path / "settings.json"
    executable.write_text("#!/bin/sh\nexit 99\n")
    executable.chmod(0o700)
    settings.write_text("{}")
    settings.chmod(0o644 if guard == "settings-mode" else 0o600)
    if guard == "executable-symlink":
        link = tmp_path / "cc-link"
        link.symlink_to(executable)
        executable = link
    adapter = canary.ClaudeAgentHost(claude_executable=executable,
        settings_path=None if guard == "no-settings" else settings)
    from hypoloom.claude_agent_host import SandboxUnavailable
    with pytest.raises((ValueError, SandboxUnavailable)):
        canary._canary_launch(adapter, "approved-model", "1000")


@pytest.mark.parametrize("command", [AgentCommand.GROUNDING, AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT])
def test_common_g5_current_schema_exposes_acceptance_and_goal_scope_without_provider(tmp_path, command):
    _, payload = canary._profile(tmp_path / "common", "approved-model", common_acceptance=True)
    document = payload["research_contract"]
    config = EmbeddedInput.from_bytes(
        "canary--grounding-config" if command is AgentCommand.GROUNDING else "canary--expert-config",
        "application/json", json.dumps({"goal": document["goal"], "component": document["components"][0],
            "required_experiment_spec": {"metric": None, "evaluator_version": canary.OUTCOME_ADAPTER_VERSION,
                                         "artifact_requirements": ["predictions.json"]}}).encode())
    prototype = default_request(command)
    request = replace(prototype, schema_version=CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION,
        required_output_schema=required_output_schema(command, prototype.task_context,
            schema_version=CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION), budget=canary._NODE_BUDGET)
    request = replace(request, input_artifacts=(*request.input_artifacts, config))
    candidate = canary._output_schema(request, tool_workspace=True)["properties"]["typed_candidate"]
    prompt = canary._prompt(request, tool_workspace=True, git_authoring=True)
    assert b"/acceptance" in prompt
    if command is AgentCommand.GROUNDING:
        assert "acceptance" in candidate["required"]
        declaration = candidate["properties"]["acceptance"]["anyOf"][1]
        assert set(declaration["required"]) == {"summary", "rule", "criteria", "validity_checks",
            "parser_argv", "parser_files", "raw_artifacts"}
    else:
        assert candidate["properties"]["evaluation_scope"] == {"enum": ["LOCAL", "GOAL"]}
        assert {"type": "null"} in candidate["properties"]["measurement"]["anyOf"]


@pytest.mark.parametrize("local_outcome", ["SUCCESS", "CLEAN_REFUTATION", "EXECUTION_FAILURE"])
@pytest.mark.parametrize("change", [None, "missing", "wrong-source", "wrong-value", "invalid"])
def test_common_g5_requires_exact_goal_vector_and_all_validity_checks(change, local_outcome):
    payload = {"outcome": local_outcome, "metric_value": None, "goal_evaluation": {"source_attempt_id": "ground-final",
        "measurements": {"accuracy": "0.875"},
        "checks": {"complete_pairs": True, "computed_accuracy_matches": True}}}
    changed = copy.deepcopy(payload)
    if change == "missing":
        changed.pop("goal_evaluation")
    elif change == "wrong-source":
        changed["goal_evaluation"]["source_attempt_id"] = "different-ground"
    elif change == "wrong-value":
        changed["goal_evaluation"]["measurements"]["accuracy"] = "1"
    elif change == "invalid":
        changed["goal_evaluation"]["checks"]["complete_pairs"] = False
    if change is None:
        canary._assert_common_goal_outcome(changed, "ground-final")
    else:
        with pytest.raises(AssertionError):
            canary._assert_common_goal_outcome(changed, "ground-final")


@pytest.mark.parametrize("change", [None, "two-valid-designs", "archive", "untested", "unread", "local-scope", "later-unread", "later-source", "later-local-scope",
    "renamed-tests", "zero-tests", "unmatched-test", "tested-before-author", "unrelated-directory", "no-test-summary",
    "module-name-tests", "unrelated-test-module", "cat", "cat-text-blocks", "cat-trailing-lf",
    "cat-wrong-path", "cat-failed", "cat-unmatched", "cat-echo", "cat-base64", "cat-literal",
    "cat-truncated", "cat-wrong-source", "cat-compound", "read-failed", "cat-native-warning",
    "cat-unknown-prefix", "cat-warning-suffix", "cat-repeated-warning", "cat-warning-wrong-source",
    "cat-warning-failed", "cat-warning-truncated", "cat-stderr", "cat-fallback",
    "cat-stderr-fallback", "cat-stderr-fallback-warning",
    "cat-stderr-fallback-message", "cat-stderr-fallback-missing", "cat-stderr-fallback-unmatched",
    "cat-stderr-fallback-failed", "cat-stderr-fallback-truncated", "cat-stderr-fallback-wrong-source",
    "cat-stderr-fallback-extra-output", "cat-stderr-fallback-extra-command"])
def test_common_g5_source_evidence_checks_do_not_accept_missing_boundaries(tmp_path, change):
    # Ordinary helper fixtures only: no model, process or G5 invocation occurs.
    declaration = {"summary": "Fixture", "rule": "ALL",
        "criteria": [{"name": "accuracy", "operator": "GE", "target": "0.875"}],
        "validity_checks": ["complete_pairs", "computed_accuracy_matches"],
        "parser_argv": ["python", "/input/evaluate.py"],
        "parser_files": ["evaluate.py"], "raw_artifacts": ["predictions.json"]}
    files = [WorkspaceArtifact.from_bytes("evaluate.py", b"# Unit fixture, never executed.\n").to_dict()]
    shared = {"source_attempt_id": "ground-final", "acceptance": declaration, "files": files}
    embedded = EmbeddedInput.from_bytes("run--shared-acceptance", "application/json", json.dumps(shared).encode()).to_dict()
    ground = {"request": {"command": "GROUNDING", "attempt_id": "ground-final"},
        "response": {"typed_candidate": {"acceptance": declaration}, "proposed_workspace_artifacts": files},
        "session": {"transcript": {"path": "ground"}}}
    design = {"request": {"command": "EXPERIMENT_DESIGN_IMPLEMENT", "input_artifacts": [embedded]},
        "response": {"typed_candidate": {"evaluation_scope": "GOAL"}},
        "session": {"transcript": {"path": "design"}}}
    (tmp_path / "ground/evaluator").mkdir(parents=True)
    (tmp_path / "design").mkdir()
    (tmp_path / "ground/evaluator/evaluate.py").write_bytes(WorkspaceArtifact.from_dict(files[0]).content_bytes())
    ground_blocks = [{"type": "tool_use", "id": "write", "name": "Write", "input": {"file_path": "/acceptance/evaluate.py"}},
        {"type": "tool_use", "id": "test", "name": "Bash", "input": {"command": "cd /acceptance && python -m unittest -v test_evaluate.py"}},
        {"type": "tool_result", "tool_use_id": "test", "content": "test_common_accuracy ... ok\nRan 8 tests in 0.01s\nOK"}]
    design_blocks = [{"type": "tool_use", "id": "read", "name": "Read", "input": {"file_path": "/acceptance/evaluate.py"}},
        {"type": "tool_result", "tool_use_id": "read", "content": "# Unit fixture, never executed."}]
    if isinstance(change, str) and change.startswith("cat"):
        source_text = WorkspaceArtifact.from_dict(files[0]).content_bytes().decode()
        warning = "/bin/bash: warning: setlocale: LC_ALL: cannot change locale (C.UTF-8)\n"
        design_blocks[0].update(name="Bash", input={"command": "cat /acceptance/evaluate.py"})
        design_blocks[1]["content"] = source_text
        if change == "cat-text-blocks":
            design_blocks[1]["content"] = [{"type": "text", "text": source_text}]
        elif change == "cat-trailing-lf":
            design_blocks[1]["content"] = source_text.rstrip("\n")
        elif change == "cat-wrong-path":
            design_blocks[0]["input"]["command"] = "cat /workspace/evaluate.py"
        elif change == "cat-failed":
            design_blocks[1]["is_error"] = True
        elif change == "cat-unmatched":
            design_blocks[1]["tool_use_id"] = "unrelated"
        elif change == "cat-echo":
            design_blocks[0]["input"]["command"] = "echo cat /acceptance/evaluate.py"
        elif change == "cat-base64":
            design_blocks[0]["input"]["command"] = f"python -c \"import base64; print(base64.b64decode('{files[0]['content_base64']}').decode())\""
        elif change == "cat-literal":
            design_blocks[0]["input"]["command"] = f"printf '%s' '{source_text}'"
        elif change == "cat-truncated":
            design_blocks[1]["content"] = source_text[:10]
        elif change == "cat-wrong-source":
            design_blocks[1]["content"] = "different source\n"
        elif change == "cat-compound":
            design_blocks[0]["input"]["command"] += " || true"
        elif change == "cat-native-warning":
            design_blocks[1]["content"] = warning + source_text.rstrip("\n")
        elif change == "cat-unknown-prefix":
            design_blocks[1]["content"] = "unknown warning\n" + source_text
        elif change == "cat-warning-suffix":
            design_blocks[1]["content"] = warning + source_text + "unexpected suffix"
        elif change == "cat-repeated-warning":
            design_blocks[1]["content"] = warning * 2 + source_text
        elif change == "cat-warning-wrong-source":
            design_blocks[1]["content"] = warning + "different source\n"
        elif change == "cat-warning-failed":
            design_blocks[1].update(content=warning + source_text, is_error=True)
        elif change == "cat-warning-truncated":
            design_blocks[1]["content"] = warning + source_text[:10]
        elif change == "cat-stderr":
            design_blocks[0]["input"]["command"] += " 2>/dev/null"
        elif change == "cat-fallback":
            design_blocks[0]["input"]["command"] += ' || echo "File not found"'
        elif change.startswith("cat-stderr-fallback"):
            design_blocks[0]["input"]["command"] += ' 2>/dev/null || echo "File not found"'
            if change == "cat-stderr-fallback-warning":
                design_blocks[1]["content"] = warning + source_text.rstrip("\n")
            elif change == "cat-stderr-fallback-message":
                design_blocks[1]["content"] = "File not found\n"
            elif change == "cat-stderr-fallback-missing":
                design_blocks.pop()
            elif change == "cat-stderr-fallback-unmatched":
                design_blocks[1]["tool_use_id"] = "unrelated"
            elif change == "cat-stderr-fallback-failed":
                design_blocks[1]["is_error"] = True
            elif change == "cat-stderr-fallback-truncated":
                design_blocks[1]["content"] = source_text[:10]
            elif change == "cat-stderr-fallback-wrong-source":
                design_blocks[1]["content"] = "different source\n"
            elif change == "cat-stderr-fallback-extra-output":
                design_blocks[1]["content"] = source_text + "File not found\n"
            elif change == "cat-stderr-fallback-extra-command":
                design_blocks[0]["input"]["command"] += "; echo extra"
    elif change == "read-failed":
        design_blocks[1]["is_error"] = True
    if change == "archive":
        (tmp_path / "ground/evaluator/evaluate.py").write_bytes(b"different source")
    elif change == "untested":
        ground_blocks[-1]["is_error"] = True
    elif change == "unread":
        design_blocks = []
    elif change == "local-scope":
        design["response"]["typed_candidate"]["evaluation_scope"] = "LOCAL"
    elif change == "renamed-tests":
        ground_blocks[-1]["content"] = "test_mixed ... ok\nRan 8 tests in 0.01s\nOK"
    elif change == "module-name-tests":
        ground_blocks[1]["input"]["command"] = "cd /acceptance && python -m unittest -v test_evaluate"
    elif change == "unrelated-test-module":
        ground_blocks[1]["input"]["command"] = "cd /acceptance && python -m unittest -v test_evaluate_other"
    elif change == "zero-tests":
        ground_blocks[-1]["content"] = "test_common_accuracy\nRan 0 tests in 0.01s\nOK"
    elif change == "unmatched-test":
        ground_blocks[-1]["tool_use_id"] = "unrelated"
    elif change == "tested-before-author":
        ground_blocks[0], ground_blocks[1] = ground_blocks[1], ground_blocks[0]
    elif change == "unrelated-directory":
        ground_blocks[1]["input"]["command"] = "cd /workspace && python -m unittest -v test_accuracy.py"
    elif change == "no-test-summary":
        ground_blocks[-1]["content"] = "test_common_accuracy file exists; OK to test later"
    for directory, blocks in (("ground", ground_blocks), ("design", design_blocks)):
        (tmp_path / directory / "transcript.jsonl").write_text(json.dumps({"message": {"content": blocks}}) + "\n")
    designs = [design]
    if change in {"two-valid-designs", "later-unread", "later-source", "later-local-scope"}:
        later = copy.deepcopy(design)
        later["session"]["transcript"]["path"] = "later-design"
        (tmp_path / "later-design").mkdir()
        (tmp_path / "later-design/transcript.jsonl").write_text(json.dumps({"message": {"content": [] if change == "later-unread" else design_blocks}}) + "\n")
        if change == "later-source":
            foreign = {**shared, "source_attempt_id": "different-ground"}
            later["request"]["input_artifacts"] = [EmbeddedInput.from_bytes("run--shared-acceptance", "application/json", json.dumps(foreign).encode()).to_dict()]
        if change == "later-local-scope":
            later["response"]["typed_candidate"]["evaluation_scope"] = "LOCAL"
        designs.append(later)
    prereg = [{"payload": {"goal_evaluation": {"scope": "GOAL", "source_attempt_id": "ground-final", "acceptance": declaration}}}]
    host = SimpleNamespace(publisher=SimpleNamespace(root=tmp_path), store=SimpleNamespace(list_facts=lambda *_: prereg))
    outcomes = [{"payload": {"goal_evaluation": {"source_attempt_id": "ground-final",
        "measurements": {"accuracy": "0.875"}, "checks": {"complete_pairs": True, "computed_accuracy_matches": True}}}}]
    if change in {None, "two-valid-designs", "renamed-tests", "module-name-tests", "cat", "cat-text-blocks", "cat-trailing-lf", "cat-native-warning",
            "cat-stderr", "cat-fallback", "cat-stderr-fallback", "cat-stderr-fallback-warning"}:
        assert canary._assert_common_canary_evidence(host, "run", [ground, *designs], outcomes)["source_attempt_id"] == "ground-final"
    else:
        with pytest.raises(AssertionError):
            canary._assert_common_canary_evidence(host, "run", [ground, *designs], outcomes)


def _cycle_observations(cycle, *, close=False, schema_version=23):
    observed = []
    for command, subkinds in (("LITERATURE_REVIEW", ("QUERY_PLAN", "MAIN")),
        ("HYPOTHESIS_PROPOSAL", ("FORWARD", "BACKWARD", "SYNTHESIS")),
        ("EXPERIMENT_DESIGN_IMPLEMENT", ("MAIN",)), ("EXPERIMENT_EXECUTION", ("MAIN",)),
        ("RESULT_ANALYSIS", ("MAIN",))):
        for subkind in subkinds:
            inner = command in {"EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION", "RESULT_ANALYSIS"}
            key = {"cycle": cycle, "chain": "1" if inner else None, "subkind": subkind}
            session_id = f"session-{cycle}-{command}-{subkind}" if schema_version >= 24 else f"session-{cycle}"
            observed.append({"request": {"command": command, "schema_version": schema_version,
                "task_context": {"kind": "RESEARCH_NODE", "key": key}},
                "session": {"session_id": session_id, "parent_session_id": None,
                    "launch_arguments": ["--session-id" if schema_version >= 24 else "--resume", session_id]}})
    if close:
        for command in ("ADJUDICATE", "HYPOTHESIS_UPDATE", "CYCLE_REPORT", "SELF_EVOLVE"):
            observed.append({"request": {"command": command, "task_context": {"kind": "RESEARCH_NODE", "key": {"cycle": cycle}}},
                "response": {"typed_candidate": {"update_disposition": "REVISE_NEXT_CYCLE"}}})
    return observed


@pytest.mark.parametrize("cycles", [1, 2])
@pytest.mark.parametrize("schema_version", [23, 24, canary.CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION])
def test_common_g5_checks_each_actual_cycle_and_chain_session(cycles, schema_version):
    query_plan = {"request": {"command": "LITERATURE_REVIEW", "task_context": {
        "kind": "AUXILIARY", "request_kind": "GROUNDING_QUERY_PLAN"}}}
    accepted = [query_plan, *[item for cycle in range(1, cycles + 1)
        for item in _cycle_observations(cycle, close=cycle < cycles, schema_version=schema_version)]]
    batches = [{"cycle": cycle, "chains": {"1": f"hypothesis-{cycle}"}} for cycle in range(1, cycles + 1)]
    canary._assert_canary_research_cycles(accepted, batches, max_rounds_per_expert=cycles)


@pytest.mark.parametrize("mutation", ["same_node_session", "prior_cycle_session", "parent", "resume", "empty", "mixed_version"])
def test_common_g5_current_sessions_reject_reuse_or_continuation(mutation):
    accepted = (_cycle_observations(1, close=True, schema_version=24)
        + _cycle_observations(2, schema_version=24))
    nodes = [item for item in accepted if item["request"]["command"] in
        {"EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION", "RESULT_ANALYSIS"}]
    target = nodes[-1]
    if mutation in {"same_node_session", "prior_cycle_session", "empty"}:
        session_id = {"same_node_session": nodes[-2]["session"]["session_id"],
            "prior_cycle_session": nodes[0]["session"]["session_id"], "empty": ""}[mutation]
        target["session"].update(session_id=session_id, launch_arguments=["--session-id", session_id])
    elif mutation == "parent": target["session"]["parent_session_id"] = "foreign"
    elif mutation == "resume": target["session"]["launch_arguments"][0] = "--resume"
    else: target["request"]["schema_version"] = 23
    batches = [{"cycle": cycle, "chains": {"1": f"hypothesis-{cycle}"}} for cycle in (1, 2)]
    with pytest.raises(AssertionError):
        canary._assert_canary_research_cycles(accepted, batches, max_rounds_per_expert=2)


@pytest.mark.parametrize("mutation", ["wrong_session", "wrong_resume", "foreign_chain", "missing_analysis", "missing_proposal", "missing_close", "terminate", "extra_cycle", "unaccepted_cycle", "final_controls", "wrong_order", "duplicate_proposal"])
def test_common_g5_revision_coverage_rejects_missing_or_foreign_cycle_work(mutation):
    accepted = _cycle_observations(1, close=True) + _cycle_observations(2)
    batches = [{"cycle": cycle, "chains": {"1": f"hypothesis-{cycle}"}} for cycle in (1, 2)]
    if mutation == "wrong_session":
        next(item for item in accepted if item["request"]["command"] == "EXPERIMENT_EXECUTION" and item["request"]["task_context"]["key"]["cycle"] == 2)["session"]["session_id"] = "foreign"
    elif mutation == "wrong_resume":
        accepted[-2]["session"]["launch_arguments"] = ["--resume", "foreign"]
    elif mutation == "foreign_chain":
        accepted[-2]["request"]["task_context"]["key"]["chain"] = "foreign"
    elif mutation in {"missing_analysis", "missing_proposal", "missing_close"}:
        command = {"missing_analysis": "RESULT_ANALYSIS", "missing_proposal": "HYPOTHESIS_PROPOSAL", "missing_close": "CYCLE_REPORT"}[mutation]
        accepted.remove(next(item for item in reversed(accepted) if item["request"]["command"] == command))
    elif mutation == "terminate":
        next(item for item in accepted if item["request"]["command"] == "HYPOTHESIS_UPDATE")["response"]["typed_candidate"]["update_disposition"] = "TERMINATE_LINEAGE"
    elif mutation == "extra_cycle": accepted.extend(_cycle_observations(3))
    elif mutation == "final_controls": accepted.extend(_cycle_observations(2, close=True)[-4:])
    elif mutation == "wrong_order": accepted[-2], accepted[-1] = accepted[-1], accepted[-2]
    elif mutation == "duplicate_proposal": accepted.append(copy.deepcopy(accepted[2]))
    else: batches.pop()
    with pytest.raises(AssertionError):
        canary._assert_canary_research_cycles(accepted, batches, max_rounds_per_expert=2)


def test_common_g5_one_cycle_default_rejects_revision_coverage_without_opt_in():
    accepted = _cycle_observations(1, close=True) + _cycle_observations(2)
    batches = [{"cycle": cycle, "chains": {"1": f"hypothesis-{cycle}"}} for cycle in (1, 2)]
    with pytest.raises(AssertionError):
        canary._assert_canary_research_cycles(accepted, batches)


def test_seeded_repair_is_observed_once_overall_not_required_again_in_later_design():
    uses = [{"id": "failed", "name": "Bash"}, {"id": "edit", "name": "Edit"}, {"id": "passed", "name": "Bash"}]
    results = [{"tool_use_id": "failed", "is_error": True, "content": "test_known_validation_pairs FAILED"},
        {"tool_use_id": "passed", "content": "Ran 1 test OK"}]
    assert canary._has_seeded_repair(uses, results)
    assert not canary._has_seeded_repair(uses[1:], results[1:])
    assert not canary._has_seeded_repair([uses[0], uses[2], uses[1]], results)


@pytest.mark.parametrize("interpreter", ["python", "python3"])
@pytest.mark.parametrize("command,exit_line,expected", [
    ('python -m unittest -v test_accuracy.py; echo "exit=$?"', "exit=1", True),
    ('python -m unittest -v test_accuracy.py 2>&1; echo "exit=$?"', "exit=1", True),
    ('python -m unittest -v test_accuracy.py 2>&1; echo "EXIT=$?"', "EXIT=1", True),
    ('python -m unittest -v test_accuracy.py 2>&1; echo "EXIT=$?"; echo ---; cat validation.json; echo; ls .', "EXIT=1", True),
    ('python -m unittest -v test_accuracy.py 2>&1; echo "EXIT=$?"; cat validation.json', "EXIT=0", False),
    ('python -m unittest -v test_accuracy.py; true; echo "EXIT=$?"', "EXIT=1", False),
    ('python -m unittest -v test_accuracy.py; echo "exit=$?"', "exit=0", False),
    ('python -m unittest -v test_accuracy.py; echo "exit=$?"', "", False),
    ('python -m unittest -v test_accuracy.py || true', "exit=1", False),
    ('echo "test_known_validation_pairs FAILED exit=1"', "exit=1", False),
    ('cat /var/tmp/prom-current-g5-fixture/data/validation.json; echo "---"; cd /workspace && python -m unittest -v test_accuracy.py; echo "exit=$?"', "exit=1", True),
    ('cd /workspace && python -m unittest -v test_accuracy.py; echo "exit=$?"', "exit=1", True),
    ('cat /var/tmp/prom-current-g5-fixture/data/validation.json; echo "---"; cd /workspace && python -m unittest -v other_test.py; echo "exit=$?"', "exit=1", False),
    ('cd /workspace && python -m unittest -v test_accuracy.py; true; echo "exit=$?"', "exit=1", False),
    ('cd /workspace && python -m unittest -v test_accuracy.py; echo "exit=$?"', "exit=0", False),
    ('echo \'cd /workspace && python -m unittest -v test_accuracy.py; echo "exit=$?"\'', "exit=1", False),
    ('rm -f /workspace/accuracy.py; cd /workspace && python -m unittest -v test_accuracy.py; echo "exit=$?"', "exit=1", False),
])
def test_seeded_repair_recognizes_a_printed_test_exit_without_accepting_masked_failure(
    interpreter, command, exit_line, expected,
):
    # Actual CC trace: the unittest failed, then echo printed its exit status;
    # Bash correctly reports the successful echo as the shell's final status.
    command = command.replace("python ", f"{interpreter} ", 1)
    uses = [
        {"id": "red", "name": "Bash", "input": {"command": command}},
        {"id": "edit", "name": "Edit"},
        {"id": "green", "name": "Bash"},
    ]
    results = [
        {"tool_use_id": "red", "is_error": False, "content":
            "test_known_validation_pairs ... FAIL\nAssertionError: 0.0 != 0.875\n"
            "Ran 1 test in 0.000s\nFAILED (failures=1)\n" + exit_line},
        {"tool_use_id": "green", "is_error": False, "content": "Ran 1 test in 0.000s\nOK\nexit=0"},
    ]
    assert canary._has_seeded_repair(uses, results) is expected
    assert not canary._has_seeded_repair([uses[0], uses[2], uses[1]], results)
    assert not canary._has_seeded_repair(uses, [{**results[0], "tool_use_id": "unrelated"}, results[1]])


@pytest.mark.parametrize("command", [
    "python -m unittest -v test_accuracy.py 2>&1 | tail -20",
    "python -m unittest -v test_accuracy.py 2>&1; ls /workspace",
    "python -m unittest -v test_accuracy.py 2>&1; ls .",
    'python -m unittest -v test_accuracy.py 2>&1; echo "EXIT=$?"',
    'python -m unittest -v test_accuracy.py 2>&1; echo "EXIT=$?"; echo ---; cat validation.json; echo; ls .',
    'cat /var/tmp/prom-current-g5-fixture/data/validation.json; echo "---"; cd /workspace && python -m unittest -v test_accuracy.py 2>&1; echo "exit=$?"',
])
def test_seeded_repair_recognizes_actual_unittest_failure_with_successful_shell_exit(tmp_path, command):
    import os
    import subprocess
    import sys

    (tmp_path / "accuracy.py").write_text("def accuracy():\n    return 0.0\n")
    (tmp_path / "test_accuracy.py").write_text(
        "import unittest\nfrom accuracy import accuracy\n"
        "class AccuracyTest(unittest.TestCase):\n"
        "    def test_known_validation_pairs(self):\n"
        "        self.assertEqual(accuracy(), 0.875)\n"
    )
    (tmp_path / "validation.json").write_text('{"accuracy": 0.875}\n')
    environment = {**os.environ, "PATH": str(Path(sys.executable).parent),
                   "PYTHONDONTWRITEBYTECODE": "1"}
    # Preserve real shell semantics: tail, echo and inspection can exit zero
    # after unittest fails, without changing that preceding test's outcome.
    environment["PATH"] += ":/usr/bin:/bin"
    # /workspace is the native sandbox's working directory; use this test's
    # temporary working directory for the equivalent actual shell execution.
    shell_command = command.replace("ls /workspace", "ls .").replace("cd /workspace", "cd .").replace(
        "/var/tmp/prom-current-g5-fixture/data/validation.json", "validation.json")
    red = subprocess.run(shell_command, shell=True, cwd=tmp_path, env=environment,
                         capture_output=True, text=True, timeout=10)
    (tmp_path / "accuracy.py").write_text("def accuracy():\n    return 0.875\n")
    green = subprocess.run(shell_command, shell=True, cwd=tmp_path, env=environment,
                           capture_output=True, text=True, timeout=10)
    assert red.returncode == green.returncode == 0
    assert "FAILED (failures=1)" in red.stdout and "\nOK\n" in green.stdout
    uses = [
        {"id": "red", "name": "Bash", "input": {"command": command}},
        {"id": "edit", "name": "Edit"},
        {"id": "green", "name": "Bash", "input": {"command": command}},
    ]
    results = [
        {"tool_use_id": "red", "is_error": False, "content": red.stdout},
        {"tool_use_id": "green", "is_error": False, "content": green.stdout},
    ]
    assert canary._has_seeded_repair(uses, results)
    assert not canary._has_seeded_repair([uses[0], uses[2], uses[1]], results)
    assert not canary._has_seeded_repair(uses[1:], results[1:])
    for incomplete in [red.stdout.replace("Ran 1 test", "truncated"),
                       red.stdout.replace("FAILED (failures=1)", "truncated"),
                       red.stdout.replace("test_known_validation_pairs", "another_test")]:
        assert not canary._has_seeded_repair(uses, [{**results[0], "content": incomplete}, results[1]])


def test_seeded_repair_recognizes_copied_fixture_summary_before_workspace_repair():
    # Real DESIGN invocation: tail -5 retains the complete one-test failure
    # summary but omits the test method name. The copied test is unchanged.
    command = ("cd /tmp && mkdir -p synth_check && cp /workspace/accuracy.py "
        "/workspace/test_accuracy.py synth_check/ && cd synth_check && "
        "python -m unittest -v test_accuracy.py 2>&1 | tail -5")
    uses = [
        {"id": "red", "name": "Bash", "input": {"command": command}},
        {"id": "edit", "name": "Write", "input": {"file_path": "/workspace/accuracy.py"}},
        {"id": "green", "name": "Bash", "input": {
            "command": "python -m unittest -v test_accuracy.py 2>&1 | tail -4"}},
    ]
    results = [
        {"tool_use_id": "red", "is_error": False,
            "content": "Ran 1 test in 0.000s\n\nFAILED (failures=1)\nShell cwd was reset to /workspace"},
        {"tool_use_id": "green", "is_error": False, "content": "Ran 1 test in 0.000s\n\nOK\n"},
    ]
    assert canary._has_seeded_repair(uses, results)
    assert not canary._has_seeded_repair([uses[0], uses[2], uses[1]], results)
    assert not canary._has_seeded_repair(uses[1:], results[1:])
    for incomplete in ("Ran 1 test in 0.000s", "FAILED (failures=1)"):
        assert not canary._has_seeded_repair(uses, [{**results[0], "content": incomplete}, results[1]])
    for other in (command.replace("/workspace/test_accuracy.py", "/workspace/another_test.py"),
                  command.replace("cd synth_check", "cd another_copy"),
                  "python -m unittest -v test_accuracy.py 2>&1 | tail -5"):
        assert not canary._has_seeded_repair([{**uses[0], "input": {"command": other}}, *uses[1:]], results)


def _outcome_hypothesis_rows():
    _, preregistration, outcome = all_facts()[1:4]
    outcomes, preregistrations = [], []
    for experiment, hypothesis in (("e1", "h1"), ("e1-repair", "h1"), ("e2", "h2")):
        spec = fact_to_dict(replace(preregistration, experiment_id=experiment, hypothesis_id=hypothesis))
        result = fact_to_dict(replace(outcome, experiment_id=experiment))
        assert "hypothesis_id" not in result  # Canonical schema stores this relation on preregistration.
        outcomes.append({"experiment_id": experiment, "hypothesis_id": None, "payload": result})
        preregistrations.append({"experiment_id": experiment, "hypothesis_id": hypothesis, "payload": spec})
    batches = [{"cycle": cycle, "chains": {"1": f"h{cycle}"}} for cycle in (1, 2)]
    return outcomes, preregistrations, batches


def test_common_g5_outcome_hypothesis_correspondence_uses_preregistered_experiment():
    outcomes, preregistrations, batches = _outcome_hypothesis_rows()
    canary._assert_canary_outcome_hypotheses(outcomes, list(reversed(preregistrations)), batches)


@pytest.mark.parametrize("change", ["foreign_hypothesis", "foreign_experiment", "missing_spec", "missing_outcome", "missing_batch", "duplicate_spec", "duplicate_outcome"])
def test_common_g5_outcome_hypothesis_correspondence_rejects_missing_or_foreign_join(change):
    outcomes, preregistrations, batches = _outcome_hypothesis_rows()
    if change == "foreign_hypothesis":
        preregistrations[-1]["hypothesis_id"] = preregistrations[-1]["payload"]["hypothesis_id"] = "foreign"
    elif change == "foreign_experiment":
        outcomes[-1]["experiment_id"] = outcomes[-1]["payload"]["experiment_id"] = "foreign"
    elif change == "missing_spec": preregistrations.pop()
    elif change == "missing_outcome": outcomes.pop()
    elif change == "missing_batch": batches.pop()
    elif change == "duplicate_spec": preregistrations.append(copy.deepcopy(preregistrations[0]))
    elif change == "duplicate_outcome": outcomes.append(copy.deepcopy(outcomes[0]))
    with pytest.raises(AssertionError):
        canary._assert_canary_outcome_hypotheses(outcomes, preregistrations, batches)
