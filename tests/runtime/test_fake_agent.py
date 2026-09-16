from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from hypoloom.agent_aci import (
    AgentACIError,
    AgentCommand,
    AgentResponse,
    EmbeddedInput,
    validate_postflight,
)
from hypoloom.component_contract import compile_component_contract
from hypoloom.domain import ResearchContract
from hypoloom.fake_agent import FakeAgent, FakeAgentMode, default_request
from hypoloom.outcome_adapter import (
    OUTCOME_ADAPTER_VERSION,
    validate_component_result,
)


COMPONENT_BATCH_IDS = (
    "primary.dataset_alpha_final_score",
    "primary.dataset_beta_final_score",
    "must.mechanism_search_recorded",
    "must.method_implemented",
    "must.dataset_alpha_audit",
    "must.dataset_beta_audit",
    "must.dataset_alpha_screen_completed",
    "must.dataset_beta_screen_completed",
    "must.final_reported",
    "should.ablation_completed",
)


def _yaml_document(path: Path) -> dict[str, object]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _component_request(
    *,
    component: dict[str, object],
    protocol: dict[str, object],
    required_spec: dict[str, object],
):
    config = EmbeddedInput.from_bytes(
        "run-fake--component-config",
        "application/json",
        json.dumps(
            {
                "component": component,
                "protocol": protocol,
                "goal": {"statement": "exercise the component contract"},
                "required_experiment_spec": required_spec,
            },
            sort_keys=True,
        ).encode(),
    )
    return replace(
        default_request(AgentCommand.DRAFT_EXPERIMENT_SPEC),
        component_id=str(component["id"]),
        immutable_context_refs=(config.ref,),
        input_artifacts=(config,),
    )


@pytest.fixture(scope="module")
def component_batch_contract(
    component_batch_profile: Path,
) -> tuple[dict[str, object], ResearchContract]:
    profile = component_batch_profile
    compilation = compile_component_contract(
        goal_text=(profile / "goal.md").read_text(encoding="utf-8"),
        language_text=(profile / "language.md").read_text(encoding="utf-8"),
        experts=_yaml_document(profile / "experts.yaml"),
        methodology=_yaml_document(profile / "methodology.yaml"),
        notify=_yaml_document(profile / "notify.yaml"),
        safety=_yaml_document(profile / "safety.yaml"),
    )
    assert compilation.blockers == ()
    assert compilation.contract is not None
    document = compilation.contract
    contract = ResearchContract.from_mapping(document)
    assert (
        tuple(str(item.component_id) for item in contract.components)
        == COMPONENT_BATCH_IDS
    )
    assert {item.protocol for item in contract.components} == set(document["protocols"])
    assert (
        sum(item.tier.value in {"primary", "must"} for item in contract.components) == 9
    )
    return document, contract


def _batch_component_request(
    document: dict[str, object],
    contract: ResearchContract,
    component_id: str,
):
    component = next(
        item
        for item in document["components"]
        if item["id"] == component_id  # type: ignore[index]
    )
    typed = contract.component(component_id)
    assert typed is not None and typed.protocol is not None
    protocol = document["protocols"][typed.protocol]  # type: ignore[index]
    trial_plan = contract.trial_plan_for(component_id)
    required_spec = {
        "evaluator_version": OUTCOME_ADAPTER_VERSION,
        "workspace_artifacts": ["experiment.py"],
        "result_envelope": "result.json",
        "artifact_requirements": ["result.json"],
        "required_artifacts": list(typed.required_artifacts),
        "result_kind": typed.result_kind.value,
        "component": component,
        "protocol": protocol,
        "metric": (
            None
            if typed.metric is None
            else {
                "name": typed.metric.name,
                "direction": typed.metric.direction.value,
                "target": typed.metric.target,
            }
        ),
        "trial_plan": {
            "seeds": list(trial_plan.seeds),
            "arms": list(trial_plan.arms),
            "trial_ids": list(trial_plan.trial_ids),
        },
    }
    request = _component_request(
        component=component,
        protocol=protocol,
        required_spec=required_spec,
    )
    return replace(request, role=typed.owner_role), component, protocol


@pytest.mark.parametrize("command", tuple(AgentCommand))
def test_valid_fake_is_byte_deterministic(command: AgentCommand) -> None:
    request = default_request(command)
    # These decisions consume actual source records rather than inventing an
    # empty batch or debate. The bare request helper intentionally has none.
    if command is AgentCommand.ADJUDICATE:
        context = EmbeddedInput.from_bytes("adjudicate--batch-context", "application/json",
            json.dumps({"chains": {"chain-a": "hyp-a"}, "terminal_tips": {
                "chain-a": {"outcome": "SUCCESS", "outcome_id": "out-a"}}}).encode())
        request = replace(request, input_artifacts=(context,),
            immutable_context_refs=(context.ref, "hyp-a", "out-a"))
    elif command is AgentCommand.DEBATE_JUDGE:
        from tests.runtime.test_debate_aci14 import judge_request
        request, _ = judge_request()
    first = FakeAgent().invoke(request)
    second = FakeAgent().invoke(request)
    assert isinstance(first, AgentResponse)
    assert isinstance(second, AgentResponse)
    assert first.to_json() == second.to_json()
    assert FakeAgent().deliveries(request) == (first, first)
    if command in {AgentCommand.ADJUDICATE, AgentCommand.DEBATE_JUDGE}:
        validate_postflight(request, first)


def test_conflicting_duplicate_is_visible_under_the_same_invocation_identity() -> None:
    request = default_request()
    first, conflicting = FakeAgent().deliveries(request, conflict=True)
    assert isinstance(first, AgentResponse)
    assert isinstance(conflicting, AgentResponse)
    assert first.command_id == conflicting.command_id
    assert first.attempt_id == conflicting.attempt_id
    assert first.output_digest != conflicting.output_digest
    assert first.to_json() != conflicting.to_json()


def test_oversized_workspace_package_fails_postflight() -> None:
    request = default_request(AgentCommand.DRAFT_EXPERIMENT_SPEC)
    with pytest.raises(AgentACIError) as error:
        validate_postflight(
            request, FakeAgent(FakeAgentMode.OVERSIZED_OUTPUT).invoke(request)
        )
    assert error.value.code == "output_too_large"


def test_fake_experiment_workspace_is_component_scoped_and_ignores_slot_score_env(
    tmp_path: Path,
) -> None:
    component = {
        "id": "primary.score",
        "result": {
            "kind": "metric",
            "metric": "score",
            "direction": "MAXIMIZE",
            "target": 1,
        },
        "seeds": [],
        "required_artifacts": [],
    }
    protocol = {
        "result_kind": "metric",
        "requirements": {
            "required_metrics": ["score"],
            "required_artifacts": [],
            "artifact_validators": [],
        },
    }
    request = replace(
        _component_request(
            component=component,
            protocol=protocol,
            required_spec={
                "result_kind": "metric",
                "metric": {
                    "name": "score",
                    "direction": "MAXIMIZE",
                    "target": "1",
                },
                "trial_plan": {"seeds": [], "arms": [], "trial_ids": []},
                "required_artifacts": [],
                "artifact_requirements": ["result.json"],
            },
        ),
        owner_workflow_id="workflow-renamed-without-slot-semantics",
    )
    validated = validate_postflight(request, FakeAgent().invoke(request))
    script = tmp_path / "experiment.py"
    script.write_bytes(validated.workspace_files["experiment.py"])
    artifact_dir = tmp_path / "component-output"
    subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env={
            "HYPOLOOM_PHASE2_SCORE": "-999",
            "HYPOLOOM_SCORE": "999",
            "HYPOLOOM_DELAY": "0",
            "HYPOLOOM_RESULT_PATH": str(artifact_dir / "result.json"),
        },
        check=True,
        timeout=5,
    )
    result = json.loads((artifact_dir / "result.json").read_text(encoding="utf-8"))
    assert result == {
        "artifacts": {},
        "identity": {"method_family_id": request.method_family_id},
        "trials": [{"clean": True, "metrics": {"score": 1}, "seed": "only"}],
    }
    outcome = validate_component_result(component, protocol, artifact_dir)
    assert outcome["criterion_met"] is True
    assert outcome["method_family_id"] == request.method_family_id


def test_fake_artifact_component_writes_exact_logical_inventory(
    tmp_path: Path,
) -> None:
    component = {
        "id": "must.report",
        "result": {"kind": "artifact", "evaluation_kind": "artifact_check"},
        "seeds": [],
        "required_artifacts": ["artifact_report"],
    }
    protocol = {
        "result_kind": "artifact",
        "requirements": {
            "required_metrics": [],
            "required_artifacts": ["artifact_report"],
            "artifact_validators": [],
        },
    }
    request = _component_request(
        component=component,
        protocol=protocol,
        required_spec={
            "result_kind": "artifact",
            "metric": None,
            "trial_plan": {"seeds": [], "arms": [], "trial_ids": []},
            "required_artifacts": ["artifact_report"],
            "artifact_requirements": ["result.json"],
        },
    )
    validated = validate_postflight(request, FakeAgent().invoke(request))
    assert validated.candidate.metrics == ()
    assert validated.candidate.artifact_requirements == ("result.json",)

    script = tmp_path / "artifact-experiment.py"
    script.write_bytes(validated.workspace_files["experiment.py"])
    artifact_dir = tmp_path / "artifact-output"
    subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env={
            "HYPOLOOM_DELAY": "0",
            "HYPOLOOM_RESULT_PATH": str(artifact_dir / "result.json"),
        },
        check=True,
        timeout=5,
    )
    outcome = validate_component_result(component, protocol, artifact_dir)
    assert outcome["criterion_met"] is True
    assert outcome["logical_artifacts"] == {
        "artifact_report": ("artifacts/artifact_report-1.txt",)
    }
    assert outcome["method_family_id"] == request.method_family_id


@pytest.mark.parametrize("component_id", COMPONENT_BATCH_IDS)
def test_fake_closes_each_compiled_batch_protocol_through_the_real_validator(
    component_id: str,
    component_batch_contract: tuple[dict[str, object], ResearchContract],
    tmp_path: Path,
) -> None:
    document, contract = component_batch_contract
    request, component, protocol = _batch_component_request(
        document, contract, component_id
    )
    first = FakeAgent().invoke(request)
    second = FakeAgent().invoke(request)
    assert isinstance(first, AgentResponse)
    assert isinstance(second, AgentResponse)
    assert first.to_json() == second.to_json()
    validated = validate_postflight(request, first)

    script = tmp_path / "experiment.py"
    script.write_bytes(validated.workspace_files["experiment.py"])
    artifact_dir = tmp_path / "result"
    subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env={
            "HYPOLOOM_DELAY": "0",
            "HYPOLOOM_RESULT_PATH": str(artifact_dir / "result.json"),
        },
        check=True,
        timeout=5,
    )
    raw_result = json.loads((artifact_dir / "result.json").read_text(encoding="utf-8"))

    def git_apply_check(payload: bytes) -> str | None:
        checked = subprocess.run(
            ["git", "apply", "--check", "-"],
            cwd=tmp_path,
            input=payload,
            capture_output=True,
            check=False,
            timeout=5,
        )
        return None if checked.returncode == 0 else checked.stderr.decode()

    validation_options: dict[str, object] = {"git_apply_check": git_apply_check}
    implementation_ref = raw_result["artifacts"].get("implementation_patch")
    if isinstance(implementation_ref, str):
        validation_options["implementation_bytes"] = (
            artifact_dir / implementation_ref
        ).read_bytes()
    outcome = validate_component_result(
        component,
        protocol,
        artifact_dir,
        **validation_options,
    )

    assert outcome["component_id"] == component_id
    assert outcome["criterion_met"] is True
    assert outcome["method_family_id"] == request.method_family_id
    assert set(outcome["logical_artifacts"]) == set(component["required_artifacts"])
    if component["result"]["kind"] == "metric":
        assert outcome["metric_value"] == component["result"]["target"]
    if "checkpoints" in outcome["logical_artifacts"]:
        assert len(outcome["logical_artifacts"]["checkpoints"]) == 5
    if isinstance(implementation_ref, str):
        runner_ref = raw_result["artifacts"]["runner_config"]
        assert isinstance(runner_ref, str)
        assert (
            outcome["implementation_sha256"]
            == hashlib.sha256(
                (artifact_dir / implementation_ref).read_bytes()
            ).hexdigest()
        )
        assert (
            outcome["core_config_sha256"]
            == hashlib.sha256((artifact_dir / runner_ref).read_bytes()).hexdigest()
        )

    requirements = protocol["requirements"]
    if "required_paths" in requirements or "data_checks" in requirements:
        config_ref = raw_result["artifacts"]["resolved_config"]
        assert isinstance(config_ref, str)
        resolved = json.loads((artifact_dir / config_ref).read_text(encoding="utf-8"))
        assert resolved["required_paths"] == requirements.get("required_paths", [])
        assert resolved["data_checks"] == requirements.get("data_checks", [])

    if component_id in {
        "must.dataset_alpha_screen_completed",
        "must.dataset_beta_screen_completed",
    }:
        assert {(trial["seed"], trial["arm"]) for trial in raw_result["trials"]} == {
            (seed, arm) for seed in range(3) for arm in ("control", "treatment")
        }
        assert len(raw_result["trials"]) == 6
    elif component_id.startswith("primary."):
        assert [trial["seed"] for trial in raw_result["trials"]] == list(range(5))
    elif component_id in {
        "must.dataset_alpha_audit",
        "must.dataset_beta_audit",
    }:
        assert [trial["seed"] for trial in raw_result["trials"]] == list(range(5))
        assert {
            trial["metrics"]["max_integrity_violation"]
            for trial in raw_result["trials"]
        } == {0}


def test_fake_gpu_workspace_records_one_visible_device_after_a_real_tensor() -> None:
    capability = EmbeddedInput.from_bytes(
        "run-fake--compute-capabilities",
        "application/json",
        b'{"gpus":[{"driver_version":"570.133.07","index":0,'
        b'"name":"NVIDIA GeForce RTX 3090","total_memory_mib":24576,'
        b'"uuid":"GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}],"schema_version":1}',
    )
    request = replace(
        default_request(AgentCommand.DRAFT_EXPERIMENT_SPEC),
        immutable_context_refs=(capability.ref,),
        input_artifacts=(capability,),
    )
    validated = validate_postflight(request, FakeAgent().invoke(request))
    source = validated.workspace_files["experiment.py"].decode("utf-8")
    assert "torch.cuda.device_count() == 1" in source
    assert "torch.tensor([1, 1, 1], device='cuda')" in source
    assert source.index("torch.tensor") < source.index("time.sleep")
    assert "CUDA_VISIBLE_DEVICES" not in source
