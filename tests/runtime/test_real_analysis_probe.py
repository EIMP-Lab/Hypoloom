"""Fresh ANALYSIS-only compatibility probe; other native nodes are scripted.

The local companion uses two loopback providers and one real CPU experiment.
The opt-in route purchases only RESULT_ANALYSIS, not an all-model G5 rerun.
Local scientific refutation and independently valid common qualification are
the unchanged shared-acceptance fixture's expected, distinct outcomes.
"""

from __future__ import annotations

import copy
import json
import os
import secrets
import tempfile
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from hypoloom.agent_aci import AgentBudget, EmbeddedInput, WorkspaceArtifact
from hypoloom.agent_budget import charged_cost, fold_agent_attempts
from hypoloom.claude_agent_host import ClaudeAgentHost, process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.oracle import Oracle
from hypoloom.profile_admission import admit_profile
from hypoloom.profile_v4 import resolve_node_runtime

from test_claude_file_tool_cli import _installed_claude
from test_claude_measurement_g3 import _NoPublicWeb
from test_claude_shared_acceptance_g3 import (
    _AcceptanceScenario, _DECLARATION, _GOAL, _common_source,
    _experiment_files, _local_measurement, _payload,
)
from test_full_cc_host_g3 import _MODEL, _new_host, _request_from_provider, _start_provider
from test_real_current_host_g5 import (
    _assert_current_agent_identity, _assert_launch_thresholds, _canary_launch,
    _drive_canary, _native_tools, _positive_allocation,
)
from test_real_design_recovery_probe import _DesignRouter, _write_json


_ANALYSIS = AgentCommand.RESULT_ANALYSIS
_LOCAL_BUDGET = AgentBudget(max_turns=24, max_tokens=30_000, max_output_bytes=262_144,
                            wall_seconds=180, max_cost="0.5", token_enforcement_mode="observe")


def _paid_enabled(environment=None):
    values = os.environ if environment is None else environment
    return (values.get("HYPOLOOM_REAL_ANALYSIS_PROBE") == "1"
            and values.get("HYPOLOOM_REAL_CLAUDE") == "1")


class _AnalysisRouter(_DesignRouter):
    def start(self, request, *, model, provider_environment, **kwargs):
        selected = request.command is _ANALYSIS
        assert model == (self.design_model if selected else _MODEL)
        adapter = self.design if selected else self.local
        environment = self.design_environment if selected else provider_environment
        self.routes.append({"attempt_id": request.attempt_id, "command": request.command.value,
                            "route": "analysis" if selected else "local", "model": model})
        handle = adapter.start(request, model=model, provider_environment=environment, **kwargs)
        self.owners[request.attempt_id] = adapter
        return handle


class _NonAnalysisScenario(_AcceptanceScenario):
    def record(self, document):
        assert _request_from_provider(document).command is not _ANALYSIS
        return super().record(document)


class _LocalAnalysisScenario(_AcceptanceScenario):
    def record(self, document):
        assert _request_from_provider(document).command is _ANALYSIS
        return super().record(document)


def _probe_payload(root, mount, run_id, analysis_model, node_budget, allocation,
                   *, max_retries=2, run_wall_seconds=300):
    allocation = _positive_allocation(allocation)
    assert 0 < Decimal(node_budget.max_cost) <= allocation
    _payload(root, mount, roles=(_GOAL,))
    profile = root / "profile"
    methodology = yaml.safe_load((profile / "methodology.yaml").read_text())
    methodology["output_correction"] = {"max_failed_validations": 3}
    methodology["budget"].update(max_retries=max_retries, agent_invocation=node_budget.to_dict())
    experts = yaml.safe_load((profile / "experts.yaml").read_text())
    experts["experts"][0]["stage_overrides"] = {"inner_loop.analysis": {"model": analysis_model}}
    safety = yaml.safe_load((profile / "safety.yaml").read_text())
    safety["total_budget_usd"] = safety["per_expert_budget_usd"] = float(allocation)
    goal = yaml.safe_load((profile / "goal.yaml").read_text())
    goal["stop_conditions"]["max_wall_clock_hours"] = run_wall_seconds / 3600
    for name, document in (("methodology", methodology), ("experts", experts), ("safety", safety), ("goal", goal)):
        (profile / f"{name}.yaml").write_text(yaml.safe_dump(document))
    admission = admit_profile(profile, run_id=run_id, allow_gpu=False)
    assert admission.admitted, admission.to_dict()
    _write_json(root / "profile-admission.json", admission.to_dict())
    return admission.create_run_payload


def _input(request, ref, *, immutable=True):
    matches = [EmbeddedInput.from_dict(item) for item in request["input_artifacts"] if item["ref"] == ref]
    assert len(matches) == 1 and (not immutable or ref in request["immutable_context_refs"]), ref
    return matches[0].content_bytes()


def _assert_analysis_context(request, expected, source_attempt_id, nonce):
    prefix = request["command_id"] + "--current-"
    for name, value in expected.items():
        assert json.loads(_input(request, prefix + name)) == value, name
    refs = [item["ref"] for item in request["input_artifacts"] if item["ref"].endswith("--shared-acceptance")]
    assert len(refs) == 1
    shared = json.loads(_input(request, refs[0]))
    assert shared["source_attempt_id"] == source_attempt_id
    assert shared["acceptance"] == _DECLARATION
    assert {item["path"]: WorkspaceArtifact.from_dict(item).content_bytes() for item in shared["files"]} == {
        "evaluate.py": _common_source(nonce).encode()}
    for path, content in _experiment_files(_GOAL).items():
        if path.endswith(".py"):
            assert _input(request, prefix + path) == content


def _assert_probe(host, run_id, nonce, routes, analysis_model, node_budget, allocation):
    rows = host.store.list_attempts()
    assert rows and all(row["state"] == "CLOSED" for row in rows)
    assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
    agents = [(row, json.loads(row["observation_json"])) for row in rows if row["kind"] == "AGENT"]
    accepted = [observed for row, observed in agents if row["terminal_reason"] == "SUCCEEDED"]
    counts = Counter(observed["request"]["command"] for observed in accepted)
    for command in ("GROUNDING", "EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION", "RESULT_ANALYSIS", "FINAL_REPORT"):
        assert counts[command] == 1, counts
    ground = next(observed for observed in accepted if observed["request"]["command"] == "GROUNDING")
    source = ground["request"]["attempt_id"]
    specs = host.store.list_facts(run_id, "ExperimentPreregistered")
    outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
    assert len(specs) == len(outcomes) == 1
    spec, outcome = specs[0], outcomes[0]
    assert spec["experiment_id"] == outcome["experiment_id"]
    assert spec["payload"]["measurement"] == _local_measurement().to_dict()
    assert spec["payload"]["goal_evaluation"] == {"scope": "GOAL", "source_attempt_id": source, "acceptance": _DECLARATION}
    assert outcome["payload"]["outcome"] == "CLEAN_REFUTATION" and outcome["payload"]["metric_value"] == "0"
    assert outcome["payload"]["goal_evaluation"] == {"source_attempt_id": source,
        "measurements": {"suite_one_accuracy": "0.75", "suite_two_accuracy": "0.75"},
        "checks": {"complete": True, "single_candidate": True}}
    verdicts = host.store.list_facts(run_id, "GoalVerdictRecorded")
    assert len(verdicts) == 1 and verdicts[0]["payload"]["verdict"] == "GOAL_MET"
    processes = [row for row in rows if row["kind"] == "PROCESS"]
    assert len(processes) == 1 and processes[0]["terminal_reason"] == "SUCCEEDED"
    process = json.loads(processes[0]["observation_json"])
    assert process["returncode"] == 0 and not process.get("synthetic_before_spawn")
    transitions = host.store.inspect("SELECT payload_json FROM workflow_transitions WHERE run_id=? AND fact_id=?", (run_id, outcome["fact_id"]))
    assert len(transitions) == 1
    evaluation = json.loads(transitions[0]["payload_json"])["outcome"]
    creations = host.store.inspect("SELECT payload_json FROM workflow_transitions WHERE run_id=? AND workflow_kind='EXPERIMENT' AND revision=1", (run_id,))
    assert len(creations) == 1 and json.loads(creations[0]["payload_json"])["source_submit_command_id"]
    inner = [(row, observed) for row, observed in agents if observed["request"]["command"] in {
        "EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION", "RESULT_ANALYSIS"}]
    sessions = {observed["session"]["session_id"] for _, observed in inner}
    assert len(sessions) == 1
    session = sessions.pop()
    analyses = [(row, observed) for row, observed in inner if observed["request"]["command"] == _ANALYSIS.value]
    assert len({row["command_id"] for row, _ in analyses}) == 1
    for row, observed in inner:
        if observed["request"]["command"] != "EXPERIMENT_DESIGN_IMPLEMENT":
            assert observed["session"]["launch_arguments"] == ["--resume", session]
    for row, observed in analyses:
        request = observed["request"]
        assert request["required_output_schema"] == "ExpertLoopCandidate/v1"
        _assert_analysis_context(request, {"spec": spec, "outcome": outcome, "evaluation": evaluation,
            "process": process, "result.json": {"metric": "diagnostic_accuracy", "value": "0"}}, source, nonce)
        assert json.loads(_input(request, source + "--response")) == ground["response"]
        if row["terminal_reason"] == "SUCCEEDED":
            assert observed["response"]["typed_candidate"]["task_kind"] == "RESULT_ANALYSIS"
    for previous, current in zip(analyses, analyses[1:]):
        assert previous[0]["terminal_reason"] == "FAILED" and previous[1]["retry_class"] == "MODEL"
        assert current[1]["request"]["attempt_ordinal"] > previous[1]["request"]["attempt_ordinal"]
        _input(current[1]["request"], previous[0]["attempt_id"] + "--agent-retry-diagnostic", immutable=False)
    assert len(routes) == len(agents)
    routed = {item["attempt_id"]: item for item in routes}
    tools = []
    for row, observed in agents:
        selected = observed["request"]["command"] == _ANALYSIS.value
        assert routed[row["attempt_id"]]["route"] == ("analysis" if selected else "local")
        if row["terminal_reason"] == "SUCCEEDED":
            _assert_current_agent_identity(observed, analysis_model if selected else _MODEL)
        else:
            assert selected, "only natural ANALYSIS model retries are in scope"
        assert observed["session"]["transcript_complete"] is True
        archive = host.publisher.root / observed["session"]["transcript"]["path"]
        content = (archive / "transcript.jsonl").read_bytes()
        assert content
        tools.extend(_native_tools(content)[0])
    assert {"mcp__host-tools__submit_experiment", "mcp__host-tools__wait_experiment"} <= {tool["name"] for tool in tools}
    assert any(tool["name"] == "Skill" and tool["input"]["skill"].endswith(":final-report") for tool in tools)
    charges = fold_agent_attempts(sorted(rows, key=lambda row: (row["started_at"], row["attempt_id"])))
    assert all(charge.source != "declared" for charge in charges), "unknown billing is not a passing probe"
    _assert_launch_thresholds(charges, allocation, node_budget)
    audit = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
    assert audit.ok, audit.to_dict()
    assert audit.artifact_counts["TRANSCRIPT"] == len(agents)
    assert audit.open_attempt_count == audit.pending_effect_count == 0 and not audit.owned_process_residue
    assert not host.running and not host.running_agents
    return {"analysis_accepted": True, "local_outcome": "CLEAN_REFUTATION", "goal_verdict": "GOAL_MET",
            "cpu_attempts": 1, "charged_usd": str(charged_cost(charges)), "oracle": audit.to_dict()}


def _run_probe(root, *, analysis_adapter=None, analysis_model=None, fresh_allocation_usd=None,
               node_budget=_LOCAL_BUDGET, max_retries=2, run_wall_seconds=300, progress_callback=None):
    """Explicit runner entrypoint; callers own permission and fresh allocation."""
    paid = analysis_adapter is not None
    if paid:
        analysis_adapter, analysis_model, _, allocation = _canary_launch(analysis_adapter, analysis_model, fresh_allocation_usd)
    else:
        if analysis_model is not None or fresh_allocation_usd is not None:
            raise ValueError("provide actual adapter, model and fresh allocation together")
        analysis_model, allocation = _MODEL, Decimal("20")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    assert not (root / "state").exists(), "probe requires a fresh absent state root"
    run_id, nonce = "analysis-probe-" + secrets.token_hex(8), secrets.token_hex(12)
    mount = Path(tempfile.mkdtemp(prefix="prom-analysis-probe-", dir="/var/tmp"))
    payload = _probe_payload(root, mount, run_id, analysis_model, node_budget, allocation,
                             max_retries=max_retries, run_wall_seconds=run_wall_seconds)
    _write_json(root / "settings.json", {})
    evidence = {"kind": "analysis_only_compatibility", "paid_analysis": paid, "g5": False,
                "run_id": run_id, "fixture_root": str(mount), "analysis_accepted": False,
                "fresh_allocation_usd": str(allocation), "routes": []}
    host, router, providers = None, None, []
    try:
        local = _NonAnalysisScenario(root, nonce)
        local_key = "local-only-" + secrets.token_hex(16)
        server, thread = _start_provider(local, local_key)
        providers.append((server, thread))
        executable = analysis_adapter.claude_executable if paid else _installed_claude()
        host = _new_host(root, executable, f"http://127.0.0.1:{server.server_port}", local_key)
        scenarios, analysis_environment = [local], {}
        if not paid:
            scenario = _LocalAnalysisScenario(root, nonce)
            key = "analysis-local-only-" + secrets.token_hex(16)
            server, thread = _start_provider(scenario, key)
            providers.append((server, thread))
            scenarios.append(scenario)
            analysis_environment = {"ANTHROPIC_API_KEY": key, "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}"}
            analysis_adapter = ClaudeAgentHost(claude_executable=executable, settings_path=root / "settings.json")
        router = _AnalysisRouter(host.claude_agent_host, analysis_adapter, analysis_environment, analysis_model)
        host.claude_agent_host, host.grounding_web = router, _NoPublicWeb()
        host.initialize()
        created = host.handle(ControlRequest(1, run_id + "-create", 0, "CREATE_RUN", payload))
        assert created.status == "accepted", created

        def progress(current_host, current_run):
            assert not any(scenario.errors for scenario in scenarios), "scripted provider fixture error"
            if progress_callback is not None:
                progress_callback(current_host, current_run)

        _drive_canary(host, run_id, run_wall_seconds, progress)
        evidence.update(_assert_probe(host, run_id, nonce, router.routes, analysis_model, node_budget, allocation))
    except BaseException as exc:
        evidence["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        rows = [] if host is None or not host.store.database_path.exists() else host.store.list_attempts()
        evidence["routes"] = [] if router is None else router.routes
        evidence["attempts"] = [{key: row[key] for key in ("attempt_id", "kind", "state", "terminal_reason")} for row in rows]
        evidence["charges"] = [{"attempt_id": charge.attempt_id, "source": charge.source,
            "cost": None if charge.cost is None else str(charge.cost)} for charge in fold_agent_attempts(rows)]
        if host is not None:
            host.close()
        evidence["owned_process_residue_after_close"] = [row["process_group_id"] for row in rows
            if row["process_group_id"] and process_group_exists(row["process_group_id"])]
        _write_json(root / "analysis-probe.json", evidence)
        for server, thread in providers:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    assert not evidence["owned_process_residue_after_close"]
    return evidence


def test_paid_analysis_requires_both_explicit_optins():
    assert not _paid_enabled({})
    assert not _paid_enabled({"HYPOLOOM_REAL_CLAUDE": "1"})
    assert not _paid_enabled({"HYPOLOOM_REAL_ANALYSIS_PROBE": "1"})
    assert _paid_enabled({"HYPOLOOM_REAL_CLAUDE": "1", "HYPOLOOM_REAL_ANALYSIS_PROBE": "1"})


@pytest.mark.parametrize("kwargs", [{"analysis_model": "mimo-v2.5-pro"}, {"fresh_allocation_usd": "100"}])
def test_incomplete_actual_route_is_rejected_before_preparation(tmp_path, kwargs):
    with pytest.raises(ValueError, match="provide actual adapter"):
        _run_probe(tmp_path, **kwargs)
    assert list(tmp_path.iterdir()) == []


def test_analysis_profile_routes_only_analysis_before_create_run(tmp_path):
    mount = tmp_path / "mount"
    mount.mkdir()
    budget = AgentBudget(max_turns=240, max_tokens=100_000, max_output_bytes=1_048_576,
                         wall_seconds=1800, max_cost="7.5", token_enforcement_mode="observe")
    payload = _probe_payload(tmp_path, mount, "prepare-analysis", "mimo-v2.5-pro", budget, "100",
                             max_retries=9, run_wall_seconds=3600)
    contract = payload["research_contract"]
    assert contract["budget"]["agent_invocation"] == budget.to_dict()
    assert contract["budget"]["max_experiments"] == 1 and contract["budget"]["max_retries"] == 9
    for stage in ("grounding", "hypothesis_proposal.synthesis", "experiment_design_implement", "experiment_execution", "result_analysis", "final_report"):
        runtime = resolve_node_runtime(contract, _GOAL, stage)
        assert runtime["model"] == ("mimo-v2.5-pro" if stage == "result_analysis" else _MODEL)
        assert runtime["output_correction"] == {"max_failed_validations": 3}
    assert not payload["execution"]["allow_gpu"] and not (tmp_path / "state").exists()


@pytest.mark.parametrize("mutation", [None, "outcome", "spec", "foreign-source", "missing-ref", "duplicate"])
def test_analysis_context_requires_exact_current_facts_and_common_source(mutation):
    expected = {"spec": {"experiment_id": "exact-experiment"}, "outcome": {"experiment_id": "exact-experiment", "hypothesis_id": None}}
    inputs = [EmbeddedInput.from_bytes("analysis--current-" + name, "application/json", json.dumps(value).encode()).to_dict()
              for name, value in expected.items()]
    shared = {"source_attempt_id": "ground", "acceptance": _DECLARATION,
              "files": [WorkspaceArtifact.from_bytes("evaluate.py", _common_source("nonce").encode()).to_dict()]}
    inputs.append(EmbeddedInput.from_bytes("analysis--shared-acceptance", "application/json", json.dumps(shared).encode()).to_dict())
    inputs.extend(EmbeddedInput.from_bytes("analysis--current-" + path, "text/x-python", content).to_dict()
                  for path, content in _experiment_files(_GOAL).items() if path.endswith(".py"))
    request = {"command_id": "analysis", "input_artifacts": inputs, "immutable_context_refs": [item["ref"] for item in inputs]}
    expected = copy.deepcopy(expected)
    source = "foreign" if mutation == "foreign-source" else "ground"
    if mutation in {"outcome", "spec"}:
        expected[mutation]["experiment_id"] = "foreign"
    elif mutation == "missing-ref":
        request["immutable_context_refs"].pop(0)
    elif mutation == "duplicate":
        inputs.append(copy.deepcopy(inputs[0]))
    if mutation is None:
        _assert_analysis_context(request, expected, source, "nonce")
    else:
        with pytest.raises(AssertionError):
            _assert_analysis_context(request, expected, source, "nonce")


def test_local_analysis_only_route_accepts_refutation_and_common_goal(tmp_path):
    evidence = _run_probe(tmp_path)
    assert evidence["analysis_accepted"] and not evidence["paid_analysis"]


@pytest.mark.skipif(not _paid_enabled(), reason="ANALYSIS-only paid probe requires both explicit opt-ins and approved fresh allocation/auth/model")
def test_real_analysis_only_probe(tmp_path):
    cost = _positive_allocation(os.environ["HYPOLOOM_ANALYSIS_PROBE_MAX_COST"])
    allocation = _positive_allocation(os.environ["HYPOLOOM_ANALYSIS_PROBE_ALLOCATION_USD"])
    assert cost <= allocation
    adapter = ClaudeAgentHost(claude_executable=Path(os.environ["HYPOLOOM_CLAUDE_EXECUTABLE"]),
                              settings_path=Path(os.environ["HYPOLOOM_CLAUDE_SETTINGS"]))
    _run_probe(tmp_path, analysis_adapter=adapter, analysis_model=os.environ["HYPOLOOM_CLAUDE_MODEL"],
               fresh_allocation_usd=allocation, node_budget=AgentBudget(max_turns=240, max_tokens=100_000,
                   max_output_bytes=1_048_576, wall_seconds=1800, max_cost=str(cost), token_enforcement_mode="observe"),
               max_retries=9, run_wall_seconds=3600)
