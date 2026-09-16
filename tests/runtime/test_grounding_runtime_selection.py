"""Shared Step-1 model selection without creating per-expert Grounding nodes."""

from __future__ import annotations

import json
import secrets
import tempfile
import time
from pathlib import Path

import pytest
import yaml

from hypoloom.agent_aci import AgentRequest
from hypoloom.claude_agent_host import AgentHostError
from hypoloom.control_protocol import ControlRequest
from hypoloom.expert_loop import (
    AuxiliaryRequestKind,
    AuxiliaryTask,
    ResearchNodeKey,
    ResearchNodeKind,
    ResearchNodeSubkind,
    ResearchNodeTask,
)
from hypoloom.oracle import Oracle
from hypoloom.profile_admission import admit_profile
from hypoloom.profile_language import apply_profile_language
from hypoloom.profile_v4 import request_node_runtime

from test_claude_file_tool_cli import _installed_claude
from test_claude_final_report_g3 import _FinalReportHostScenario
from test_claude_measurement_g3 import _NoPublicWeb
from test_full_cc_host_g3 import _new_host, _request_from_provider, _start_provider
from test_profile_admission import _materialize_expert_loop_pilot_v3
from test_profile_v4 import documents


RUN = "shared-grounding-runtime"
ROLE = "ml-researcher"
BASE_MODEL = "claude-sonnet-4-6"
GROUND_MODEL = "claude-opus-4-7"
PERSONAL_GROUNDING = "claude-haiku-4-5"


def _payload(root: Path, grounding: dict) -> dict:
    mount = Path(tempfile.mkdtemp(prefix="prom-ground-runtime-", dir="/var/tmp"))
    _, target, data = _materialize_expert_loop_pilot_v3(mount)
    source = documents()
    source["goal"]["metric"] = {
        "name": "accuracy",
        "direction": "maximize",
        "target": 0.9,
    }
    source["goal"]["stop_conditions"] = {
        "max_rounds_per_expert": 1,
        "max_wall_clock_hours": 1,
    }
    source["experts"] = {
        "defaults": {
            "model": BASE_MODEL,
            "effort": "high",
            "max_forks": 1,
            "max_hypotheses_per_round": 1,
        },
        "grounding": grounding,
        "experts": [
            {
                "name": ROLE,
                "role": "Measurement researcher",
                "persona": "An explicitly configured expert.",
                "focus": ["Reproducible measured CPU results"],
                "model": BASE_MODEL,
                "effort": "medium",
                "stage_overrides": {
                    "grounding": {"model": PERSONAL_GROUNDING, "effort": "max"}
                },
            }
        ],
    }
    source["methodology"] = {
        "schema_version": 4,
        "runtime": {"target_repo": str(target)},
        "budget": {
            "max_active_agents": 1,
            "max_active_processes": 1,
            "max_active_reviews": 1,
            "max_experiments": 2,
            "max_retries": 1,
            "max_infrastructure_retries": 1,
            "agent_invocation": {
                "max_turns": 24,
                "max_tokens": 100000,
                "max_output_bytes": 131072,
                "wall_seconds": 120,
                "max_cost": "0.5",
                "token_enforcement_mode": "observe",
            },
        },
        "inner_loop": {"max_iterations_per_hypothesis": 1, "repair_attempts": 1},
        "interaction": {
            "leader": {"enabled": False},
            "debate": {"enabled": False, "rounds": 3},
        },
        "graph_evolution": {"enabled": False},
        "literature": {"enabled": False, "tools": []},
    }
    source["language"] = {
        "prompt_language": "en",
        "report_language": "en",
        "code_comment_language": "en",
    }
    source["notify"] = {"sinks": {}, "events": {}}
    source["safety"] = {
        "allow_read_paths": [str(data)],
        "sandbox_os": "linux_bwrap",
        "experiment_timeout_minutes": 1,
        "total_budget_usd": 20,
        "per_expert_budget_usd": 10,
        "bash": {"network_allowed": False, "deny_patterns": []},
        "filesystem": {
            "write_only_within_worktree": True,
            "protected_paths": [".git/"],
        },
    }
    profile = root / "profile-v4"
    profile.mkdir()
    for name, value in source.items():
        (profile / (name + ".yaml")).write_text(yaml.safe_dump(value), encoding="utf-8")
    admitted = admit_profile(profile, run_id=RUN, allow_gpu=False)
    (root / "admission.json").write_text(json.dumps(admitted.to_dict(), indent=2))
    (root / "source-mount.txt").write_text(str(mount) + "\n")
    assert admitted.admitted and admitted.create_run_payload is not None, (
        admitted.to_dict()
    )
    assert any(
        "stage_overrides.grounding" in item.location for item in admitted.warnings
    )
    return admitted.create_run_payload


def _config(request: AgentRequest) -> dict:
    return json.loads(
        next(
            item.content_bytes()
            for item in request.input_artifacts
            if item.ref.endswith(("--grounding-config", "--expert-config"))
        )
    )


@pytest.mark.parametrize(
    "kind", [AuxiliaryRequestKind.GROUNDING_QUERY_PLAN, AuxiliaryRequestKind.GROUNDING]
)
@pytest.mark.parametrize(
    "configured,expected",
    [
        ({"model": GROUND_MODEL, "effort": "low"}, (GROUND_MODEL, "low")),
        ({"effort": "low"}, (BASE_MODEL, "low")),
    ],
)
def test_host_freezes_global_grounding_request_and_reuses_it_on_infrastructure_retry(
    tmp_path, monkeypatch, kind, configured, expected
):
    payload = _payload(tmp_path, configured)
    (tmp_path / "settings.json").write_text("{}\n")
    (tmp_path / "settings.json").chmod(0o600)
    # This unit seam captures the real Host's request before any provider launch.
    host = _new_host(
        tmp_path,
        Path("/usr/bin/true").resolve(),
        "http://127.0.0.1:9",
        "offline-fixture-key",
    )
    host.grounding_web = _NoPublicWeb()
    captured = []

    def no_provider_start(
        request,
        *,
        model,
        provider_environment,
        diagnostics_root,
        bind_process_group,
        tool_workspace=None,
        host_tool_handler=None,
        runtime_prefix=None,
        read_roots=(),
    ):
        assert model == request.required_model_identity
        assert provider_environment["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9"
        assert diagnostics_root == host.state_root
        captured.append(request)
        raise AgentHostError(
            "Offline request-capture fixture; no provider process started"
        )

    monkeypatch.setattr(host.claude_agent_host, "start", no_provider_start)
    try:
        host.initialize()
        created = host.handle(
            ControlRequest(1, "create-grounding-runtime", 0, "CREATE_RUN", payload)
        )
        assert created.status == "accepted", created
        host._activate(RUN)
        component = host._research_contract(RUN).components[0].component_id
        if kind is AuxiliaryRequestKind.GROUNDING:
            # A real Host/FakeAgent accepted predecessor, not a claimed CLI session.
            adapter = host.claude_agent_host
            host.claude_agent_host = None
            try:
                host._expert_agent_step(
                    RUN,
                    RUN,
                    AuxiliaryTask(
                        kind=AuxiliaryRequestKind.GROUNDING_QUERY_PLAN, run_id=RUN
                    ),
                    ROLE,
                    component,
                )
                deadline = time.monotonic() + 5
                while host._literature_responses and time.monotonic() < deadline:
                    host._poll_literature()
                    time.sleep(0.002)
                assert not host._literature_responses
            finally:
                host.claude_agent_host = adapter
        task = AuxiliaryTask(kind=kind, run_id=RUN)
        deadline = time.monotonic() + 5
        while not captured and time.monotonic() < deadline:
            host._expert_agent_step(RUN, RUN, task, ROLE, component)
            time.sleep(0.002)
        assert len(captured) == 1
        first = captured[0]
        runtime = request_node_runtime(first.input_artifacts)
        assert (first.required_model_identity, runtime["effort"]) == expected
        assert runtime["model"] == first.required_model_identity != PERSONAL_GROUNDING
        assert first.task_context.expert_slot is first.task_context.component_id is None
        config = _config(first)
        assert config["language_route"]["route"] == (
            "literature_review.query_plan"
            if kind is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN
            else "grounding"
        )
        assert config["grounding_scope"] == "role-neutral"
        assert not {"expert", "expert_slot", "persona", "memory_commit"} & config.keys()
        assert not any(
            item.ref.endswith(("--expert-resources", "--leader-resources"))
            for item in first.input_artifacts
        )
        row = host.store.get_attempt(first.attempt_id)
        assert json.loads(row["observation_json"])["retry_class"] == "INFRASTRUCTURE"

        def must_not_rebuild(*_args):
            raise AssertionError(
                "An infrastructure retry rebuilt its already frozen inputs"
            )

        monkeypatch.setattr(host, "_expert_inputs", must_not_rebuild)
        host._expert_agent_step(RUN, RUN, task, ROLE, component)
        assert len(captured) == 2
        second = captured[1]
        assert (
            second.attempt_id != first.attempt_id
            and second.command_id == first.command_id
        )
        assert second.input_artifacts == first.input_artifacts
        assert second.immutable_context_refs == first.immutable_context_refs
        assert second.required_model_identity == first.required_model_identity
        (tmp_path / "frozen-request-evidence.json").write_text(
            json.dumps([item.to_dict() for item in captured], indent=2)
        )
    finally:
        host.close()


@pytest.mark.parametrize(
    "kind", ["expert-query", "expert-code", "observer", "manager", "final-report"]
)
def test_global_grounding_does_not_override_other_runtime_consumers(tmp_path, kind):
    contract = _payload(tmp_path, {"model": GROUND_MODEL, "effort": "low"})[
        "research_contract"
    ]
    if kind.startswith("expert-"):
        task = ResearchNodeTask(
            key=ResearchNodeKey(
                run_id=RUN,
                component_id=contract["components"][0]["id"],
                expert_slot=ROLE,
                cycle=1,
                node_kind=ResearchNodeKind.LITERATURE_REVIEW
                if kind == "expert-query"
                else ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT,
                subkind=ResearchNodeSubkind.QUERY_PLAN
                if kind == "expert-query"
                else ResearchNodeSubkind.MAIN,
                **(
                    {"experiment_ordinal": 1, "chain": "chain-a"}
                    if kind == "expert-code"
                    else {}
                ),
            )
        )
    else:
        task = AuxiliaryTask(
            kind={
                "observer": AuxiliaryRequestKind.OBSERVER,
                "manager": AuxiliaryRequestKind.GRAPH_EVOLVE,
                "final-report": AuxiliaryRequestKind.FINAL_REPORT,
            }[kind],
            run_id=RUN,
            **({"cycle": 1} if kind == "manager" else {}),
        )
    config = {}
    apply_profile_language(config, contract, task)
    assert config["node_runtime"]["model"] == BASE_MODEL
    assert config["node_runtime"]["effort"] == (
        "medium" if kind.startswith("expert-") else "high"
    )
    if kind == "expert-query":
        assert config["language_route"]["route"] == "literature_review.query_plan"


class _GroundingSelectionScenario(_FinalReportHostScenario):
    def __init__(self, root):
        super().__init__(root)
        self.observed_routes = {}

    def record(self, document):
        number = super().record(document)
        request = _request_from_provider(document)
        try:
            grounding = isinstance(
                request.task_context, AuxiliaryTask
            ) and request.task_context.kind in {
                AuxiliaryRequestKind.GROUNDING_QUERY_PLAN,
                AuxiliaryRequestKind.GROUNDING,
            }
            runtime = request_node_runtime(request.input_artifacts)
            expected = (
                (GROUND_MODEL, "low")
                if grounding
                else (
                    BASE_MODEL,
                    "high"
                    if isinstance(request.task_context, AuxiliaryTask)
                    else "medium",
                )
            )
            assert (document["model"], document["output_config"]["effort"]) == expected
            assert (
                request.required_model_identity,
                runtime["model"],
                runtime["effort"],
            ) == (expected[0], *expected)
            self.observed_routes[request.attempt_id] = {
                "task": request.task_context.to_dict(),
                "model": document["model"],
                "effort": runtime["effort"],
            }
        except Exception as exc:
            self.errors.append(f"global Grounding route: {type(exc).__name__}: {exc}")
        return number


def test_real_cli_global_grounding_selection_closes_cpu_oracle(tmp_path):
    executable = _installed_claude()
    payload = _payload(tmp_path, {"model": GROUND_MODEL, "effort": "low"})
    scenario = _GroundingSelectionScenario(tmp_path)
    key = "host-only-grounding-routing-" + secrets.token_hex(12)
    provider, thread = _start_provider(scenario, key)
    (tmp_path / "settings.json").write_text("{}\n")
    (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(
        tmp_path, executable, f"http://127.0.0.1:{provider.server_port}", key
    )
    host.grounding_web = _NoPublicWeb()
    try:
        host.initialize()
        created = host.handle(
            ControlRequest(
                1, "create-native-grounding-runtime", 0, "CREATE_RUN", payload
            )
        )
        assert created.status == "accepted", created
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            host.tick()
            assert not scenario.errors, scenario.errors
            if host.store.latest_transition(RUN)["state"] in {
                "COMPLETED",
                "FAILED",
                "CANCELLED",
            }:
                break
            time.sleep(0.01)
        assert host.store.latest_transition(RUN)["state"] == "COMPLETED"
        attempts = host.store.list_attempts()
        assert all(
            row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED"
            for row in attempts
        )
        assert sum(row["kind"] == "PROCESS" for row in attempts) == 1
        assert (
            sum(
                item["model"] == GROUND_MODEL
                for item in scenario.observed_routes.values()
            )
            == 2
        )
        assert len(scenario.local_tests) == len(scenario.execution_results) == 1
        audit = Oracle(host.store.database_path, host.publisher.root).audit(RUN)
        (tmp_path / "oracle.json").write_text(audit.to_json())
        assert audit.ok and audit.canonical_outcome_count == 1, audit.to_dict()
        assert audit.artifact_counts["TRANSCRIPT"] == sum(
            row["kind"] == "AGENT" for row in attempts
        )
        assert (
            audit.open_attempt_count == audit.pending_effect_count == 0
            and not audit.owned_process_residue
        )
    finally:
        (tmp_path / "native-routing-evidence.json").write_text(
            json.dumps(
                {
                    "routes": scenario.observed_routes,
                    "provider_requests": scenario.requests,
                    "errors": scenario.errors,
                },
                indent=2,
            )
        )
        host.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=3)
