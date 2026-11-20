"""Real CPU GOAL, independent probe, publication, export and Profile admission."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import tempfile

import pytest
import yaml

from hypoloom.agent_aci import WorkspaceArtifact, with_output_digest
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.evaluator_examples import CASES, export_seed, fixture_path, load_seed
from hypoloom.execution_workspace import evaluation_only_roots
from hypoloom.profile_admission import admit_profile
from hypoloom.profile_v4 import project_profile_v4
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_profile_admission import _materialize_expert_loop_pilot_v3
from tests.runtime.test_profile_v4 import documents
from tests.runtime.test_shared_acceptance_host import (
    NUMERIC_PARSER, RUN_ID, SharedAcceptanceAgent, _until, assert_oracle, shared_payload,
)


class ScopedAgent(SharedAcceptanceAgent):
    @property
    def acceptance(self):
        return {**super().acceptance, "diagnostics": {"cache_matches": "BOOLEAN"}}

    def invoke(self, request):
        response = super().invoke(request)
        if request.command is AgentCommand.GROUNDING:
            source = NUMERIC_PARSER.replace(b"'checks':", b"'diagnostics': {'cache_matches': raw['cache']}, 'checks':")
            return with_output_digest(replace(response, proposed_workspace_artifacts=(
                WorkspaceArtifact.from_bytes("parse_goal.py", source),)))
        if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            config = next(json.loads(item.content_bytes()) for item in request.input_artifacts if item.ref.endswith("--expert-config"))
            assert "evaluation_only_roots" not in config["execution"]
            assert config["execution"]["formal_goal_inputs"] == ["/hypoloom-evaluation/1"]
            files = {f.path: f.content_bytes() for f in response.proposed_workspace_artifacts}
            files["experiment.sh"] += (b'cat /hypoloom-evaluation/1 > raw.json || exit 14\n'
                if self.scope == "GOAL" else b'test ! -e /hypoloom-evaluation/1 || exit 15\n')
            return with_output_digest(replace(response, proposed_workspace_artifacts=tuple(
                WorkspaceArtifact.from_bytes(k, v) for k, v in sorted(files.items()))))
        if request.command is AgentCommand.VERIFY_IMPLEMENTATION and response.typed_candidate["probe"]:
            report = dict(response.typed_candidate)
            probe = report["probe"]
            probe["files"]["verification/probe.py"] = ("from hypoloom_probe import check\n"
                "from pathlib import Path\nimport os\nwith check('fixture') as c:\n"
                " c.true(not Path('/hypoloom-evaluation/1').exists())\n"
                " c.true('hypoloom-evaluation' not in os.environ['HYPOLOOM_READ_ROOTS'])\n")
            return with_output_digest(replace(response, typed_candidate=report))
        return response


@pytest.mark.parametrize("scope", ["GOAL", "LOCAL"])
def test_prior_to_and_after_preregistration_with_actual_processes(tmp_path, scope):
    holdout = tmp_path / "held.json"
    positive = {"alpha": "0.9", "beta": "0.8", "causal": True, "cache": False}
    holdout.write_text(json.dumps(positive))
    with tempfile.TemporaryDirectory(prefix="hypoloom-scoped-", dir="/var/tmp") as directory:
        agent = ScopedAgent(scope=scope)
        host = WorkflowHost(tmp_path / "state", local_agent=agent)
        try:
            host.initialize()
            payload = shared_payload(tmp_path, Path(directory))
            settings = documents()
            settings["goal"].update(evaluation="grounding", stop_conditions={})
            settings["safety"]["evaluation_only_read_paths"] = [str(holdout)]
            settings["experts"]["verifier"] = {"model": "fake:model-v1"}
            settings["experts"]["defaults"].update(max_forks=1, max_hypotheses_per_round=1)
            settings["methodology"]["inner_loop"] = {"max_iterations_per_hypothesis": 1, "repair_attempts": 0}
            settings["methodology"]["graph_evolution"]["enabled"] = False
            settings["notify"] = {"sinks": {}, "events": {}}
            product = project_profile_v4(**settings)
            assert not product.issues
            contract = payload["research_contract"]
            contract["methodology"]["controls"]["product"] = product.settings
            contract["safety"]["evaluation_only_roots"] = payload["execution"]["evaluation_only_roots"] = [str(holdout)]
            mismatch = {**payload, "execution": {**payload["execution"], "evaluation_only_roots": []}}
            assert host.handle(ControlRequest(1, "wrong-bind", 0, "CREATE_RUN", mismatch)).status != "accepted"
            created = host.handle(ControlRequest(1, "bind", 0, "CREATE_RUN", payload))
            assert created.status == "accepted", created
            assert holdout not in host._agent_runtime_arguments(RUN_ID)["read_roots"]
            _until(host, lambda: host.store.latest_transition(RUN_ID)["state"] == "COMPLETED")
            rows = [row for row in host.store.list_attempts() if row["kind"] == "PROCESS"]
            assert len(rows) == 2 and all(row["terminal_reason"] == "SUCCEEDED" for row in rows), rows
            assert len([row for row in rows if row["execution_id"].endswith("--implementation-probe")]) == 1
            outcome = host.store.list_facts(RUN_ID, "CanonicalOutcomeAccepted")[0]["payload"]
            verdict = host.store.list_facts(RUN_ID, "GoalVerdictRecorded")[0]["payload"]["verdict"]
            if scope == "GOAL":
                assert verdict == "GOAL_MET" and outcome["goal_evaluation"]["diagnostics"] == {"cache_matches": False}
            else:
                assert verdict != "GOAL_MET" and outcome.get("goal_evaluation") is None
            assert_oracle(host)
        finally:
            host.close()
    if scope == "GOAL":
        fixtures = tmp_path / "toy"
        cases = {}
        for name, cache in (("baseline", True), ("diagnostic_only", False)):
            path = fixtures / fixture_path(name, "raw.json")
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({**positive, "cache": cache}))
            cases[name] = {"goal_met": True, "observation": {"measurements": {"alpha": "0.9", "beta": "0.8"},
                "checks": {"causal": True}, "diagnostics": {"cache_matches": cache}}}
        (fixtures / CASES).write_text(json.dumps(cases))
        output = export_seed(tmp_path / "state", RUN_ID, fixtures, "paired-v1", tmp_path / "portable")
        assert json.loads((output / "acceptance.json").read_bytes())["acceptance"] == agent.acceptance
        assert (output / "parse_goal.py").read_bytes() != NUMERIC_PARSER
        assert not (output / "held.json").exists()


def test_profile_admission_checks_new_scope_and_pinned_seed(tmp_path):
    from tests.test_evaluation_protocol import package
    from hypoloom.evaluator_examples import prepare_seed
    profile, target, data = _materialize_expert_loop_pilot_v3(tmp_path)
    held = tmp_path / "validation-only"
    held.mkdir()
    prepare_seed(target / "reference", package())
    subprocess.run(["git", "-C", str(target), "add", "reference"], check=True)
    subprocess.run(["git", "-C", str(target), "-c", "user.name=Fixture", "-c",
                    "user.email=test@example.invalid", "commit", "-qm", "examples"], check=True)
    settings = documents()
    spec = {"path": "reference", "protocol": "complete-v1"}
    settings["goal"].update(evaluation="grounding", evaluator_seed=spec)
    settings["methodology"]["runtime"]["target_repo"] = str(target)
    settings["safety"].update(allow_read_paths=[str(data)], evaluation_only_read_paths=[str(held)])
    def admit():
        for name, value in settings.items():
            (profile / (name + ".yaml")).write_text(yaml.safe_dump(value))
        return admit_profile(profile, run_id="scoped")
    result = admit()
    assert result.admitted, result.to_dict()
    payload = result.create_run_payload
    assert payload["execution"]["read_roots"] == [str(data)]
    assert result.to_dict()["resource_visibility"]["formal_goal_only"] == [{"host": str(held), "sandbox": "/hypoloom-evaluation/1"}]
    assert load_seed(spec, **payload["execution"]["target"], max_bytes=65536) == package()
    from hypoloom.agent_aci import EmbeddedInput
    from hypoloom.execution_workspace import bind_clean_git_target
    from tests.runtime.test_agent_sessions import _manager
    from tests.runtime.test_grounding_acceptance_aci import acceptance_exchange
    request, _ = acceptance_exchange()
    inputs = tuple(EmbeddedInput.from_bytes(item.ref, item.media_type, json.dumps({
        "goal": {"evaluation": {"mode": "grounding"}}, "evaluator_seed": spec}).encode())
        if item.ref.endswith("--grounding-config") else item for item in request.input_artifacts)
    request = replace(request, schema_version=43, input_artifacts=inputs)
    workspace = _manager(tmp_path).prepare(request, bind_clean_git_target(target))
    try:
        assert workspace.evaluator_seed_files == package()
        assert (workspace.acceptance_root / "score.py").read_bytes() == package()["score.py"]
        captured = workspace.capture_workspace({"acceptance": json.loads(package()["acceptance.json"])["acceptance"]})
        assert [item["path"] for item in captured] == ["score.py"]
    finally:
        workspace.cleanup()
    settings["goal"]["evaluator_seed"] = {**spec, "protocol": "incompatible-v2"}
    assert any(b.code == "evaluator_seed_binding_failed" for b in admit().blockers)
    settings["goal"].pop("evaluator_seed")
    alias = tmp_path / "linked"
    alias.symlink_to(held)
    for unavailable in (data, data.parent, target, alias, held / "missing"):
        settings["safety"]["evaluation_only_read_paths"] = [str(unavailable)]
        assert any(b.code == "evaluation_only_root_binding_failed" for b in admit().blockers)
    settings["safety"]["evaluation_only_read_paths"] = [str(held)]
    settings["goal"].pop("evaluation")
    assert not admit().admitted
    settings["goal"]["evaluation"] = "grounding"
    settings["safety"]["evaluation_only_read_paths"] = 5
    assert not admit().admitted


def test_holdout_cannot_share_a_dataset_binding_or_profile_secret(tmp_path):
    held = tmp_path / "holdout"
    held.mkdir()
    document = {"goal": {"evaluation": {"mode": "grounding"}}, "protocols": {
        "data": {"requirements": {"argv_bindings": [{"scope": "absolute", "path": str(held / "field")}]}}}}
    with pytest.raises(ValueError, match="overlaps"):
        evaluation_only_roots([str(held)], {"read_roots": []}, document)
    document.pop("protocols")
    with pytest.raises(ValueError, match="overlaps"):
        evaluation_only_roots([str(held)], {"read_roots": []}, document, protected=[held / "secret"])



@pytest.mark.parametrize("version", [42, 43])
def test_grounding_schema_and_postflight_agree_on_optional_report_fields(version):
    from hypoloom.agent_aci import request_digest, validate_postflight
    from hypoloom.claude_agent_host import _output_schema
    from tests.runtime.test_grounding_acceptance_aci import acceptance_exchange
    request, response = acceptance_exchange()
    request = replace(request, schema_version=version)
    candidate = dict(response.typed_candidate)
    candidate["acceptance"] = {**candidate["acceptance"], "diagnostics": {"reported": "BOOLEAN"}}
    response = with_output_digest(replace(response, schema_version=version, typed_candidate=candidate,
                                          input_digest=request_digest(request)))
    schema = _output_schema(request)["properties"]["typed_candidate"]["properties"]["acceptance"]["anyOf"][1]
    assert ("diagnostics" in schema["properties"]) is (version == 43)
    assert "diagnostics" not in schema["required"]
    if version == 43:
        assert dict(validate_postflight(request, response).candidate.acceptance.diagnostics) == {"reported": "BOOLEAN"}
    else:
        with pytest.raises(ValueError, match="ACI 43"):
            validate_postflight(request, response)
