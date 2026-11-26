"""Reproduce implementation coverage, local scoring and verifier fixture failures."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest
import yaml

from hypoloom.agent_aci import AgentACIError, EmbeddedInput
from hypoloom.candidate_validation import check_submitted_measurement
from hypoloom.domain import AgentCommand, MetricDirection, MetricSpec
from hypoloom.evaluator_examples import MEASUREMENT_CASES, fixture_path, measurement_examples
from hypoloom.execution_budget import experiment_execution_feedback
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.measurement_contract import MeasurementComparison, ScientificMeasurement, StageEvidence
from hypoloom.native_output import parser_smoke
from hypoloom.profile_v4 import project_profile_v4, validate_product_settings
from hypoloom.verification_contract import can_repair_probe, context, probe_actions, validate_review
from hypoloom.verification_probe import FixtureError, paired_inputs


def measurement_package():
    spec = ScientificMeasurement(metric=MetricSpec(name="score", direction=MetricDirection.MAXIMIZE, target="0"),
        comparison=MeasurementComparison.DELTA_FROM_BASELINE, parse_rule="Read the two paired scores",
        evidence=StageEvidence("SCREENING", ("a", "b"), "MEAN"), success_operator="GT",
        parser_argv=("python", "parse.py"), parser_files=("parse.py",), raw_artifacts=("scores.json",))
    parser = b"""import json
from pathlib import Path
rows = json.loads(Path('/raw/scores.json').read_bytes())
obs = {unit: {'value': value, 'baseline': baseline} for unit, (value, baseline) in rows.items()}
Path('/parsed/result.json').write_text(json.dumps({'metric': 'score', 'observations': obs}))
"""
    files, examples = {"parse.py": parser}, {}
    for name, rows, gain in (("baseline", {"a": [0.75, 0.5], "b": [0.5, 0.25]}, "0.25"),
                             ("mixed", {"a": [0.25, 0.75], "b": [0.75, 0.5]}, "-0.125")):
        obs = {unit: {"value": x, "baseline": y} for unit, (x, y) in rows.items()}
        files[fixture_path(name, "scores.json")] = json.dumps(rows).encode()
        examples[name] = {"observation": {"metric": "score", "observations": obs}, "metric_value": gain}
    files[MEASUREMENT_CASES] = json.dumps(examples).encode()
    return spec, files


def test_draft_and_submitted_bytes_run_through_production_parser_and_scoring(tmp_path):
    spec, files = measurement_package()
    for name, data in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    packet = json.dumps({"typed_candidate": {"measurement": spec.to_dict()}})
    args = dict(source_root=tmp_path, test_root=tmp_path, declaration_key="measurement",
                measurement_examples_required=True, timeout_seconds=15, max_bytes=100_000)
    assert parser_smoke(packet, **args)["error"] is None
    check_submitted_measurement(spec, files, max_bytes=100_000)
    for before, after, message in [(b"'value': value", b"'value': value - baseline", "expected metric_value"),
                                   (b"'metric': 'score'", b"'value': 0, 'metric': 'score'", "observations object")]:
        with pytest.raises(AgentACIError, match=message):
            check_submitted_measurement(spec, {**files, "parse.py": files["parse.py"].replace(before, after)}, max_bytes=100_000)
    del files[fixture_path("mixed", "scores.json")]
    with pytest.raises(AgentACIError, match="missing captured smoke input"):
        check_submitted_measurement(spec, files, max_bytes=100_000)
    (tmp_path / MEASUREMENT_CASES).unlink()
    assert "missing regular smoke input" in parser_smoke(packet, **args)["error"]
    assert parser_smoke(packet, **{**args, "measurement_examples_required": False})["error"] is None


def test_synthesis_corrects_the_manifest_before_design_and_runs_the_same_parser():
    from hypoloom.claude_agent_host import _native_candidate_check
    spec, files = measurement_package()
    files["scripts/score.py"] = files.pop("parse.py")
    files["config.json"] = b"{}"
    spec = replace(spec, parser_argv=("python", "./scripts/score.py"), parser_files=("config.json",))
    request = replace(default_request(AgentCommand.HYPOTHESIS_PROPOSAL), schema_version=46)
    check = _native_candidate_check(request, None)
    def batch(measurement):
        return {"typed_candidate": {"hypotheses": [{"chain_id": "a", "candidate": {"measurement": measurement.to_dict()}}]}}
    with pytest.raises(ValueError, match="scripts/score.py.*parser_files"):
        check(batch(spec))
    spec = replace(spec, parser_files=("config.json", "scripts/score.py"))
    check(batch(spec))
    check_submitted_measurement(spec, files, max_bytes=100_000)


def test_case_labels_cannot_change_the_host_rule_or_escape_the_fixture_root():
    spec, files = measurement_package()
    cases = json.loads(files[MEASUREMENT_CASES])
    cases["mixed"]["metric_value"] = 100
    with pytest.raises(ValueError, match="disagrees with Host scoring"):
        measurement_examples(json.dumps(cases), spec)
    cases["../escape"] = cases.pop("mixed")
    with pytest.raises(ValueError, match="named cases"):
        measurement_examples(json.dumps(cases), spec)


def test_project_requirements_reach_authors_and_require_independent_observed_checks():
    root = Path(__file__).resolve().parents[1] / "profiles/templates/multi-expert-en"
    docs = {p.stem: yaml.safe_load(p.read_text()) for p in root.glob("*.yaml")}
    requirements = {"reload_output": "Load the artifact through the project's reader."}
    docs["goal"]["implementation_requirements"] = requirements
    profile = project_profile_v4(**docs)
    assert not profile.issues
    assert validate_product_settings(profile.settings)["goal"]["implementation_requirements"] == requirements
    assert requirements["reload_output"] in profile.projection.goal_text
    original = default_request(AgentCommand.VERIFY_IMPLEMENTATION)
    req = replace(original, input_artifacts=(EmbeddedInput.from_bytes(original.input_artifacts[0].ref,
        "application/json", json.dumps({**context(original), "implementation_requirements": requirements}).encode()),))
    report = deepcopy(FakeAgent().invoke(original).typed_candidate)
    with pytest.raises(ValueError, match="reload_output"):
        validate_review(req, report)
    check = {**report["probe"]["checks"][0], "id": "reload_output"}
    report["probe"]["checks"].append(check)
    validate_review(req, report)
    check["required"] = False
    with pytest.raises(ValueError, match="required assertions"):
        validate_review(req, report)
    docs["goal"]["implementation_requirements"] = {"formal_entrypoint": "Conflicting ID"}
    assert project_profile_v4(**docs).issues


def test_failed_sample_and_repeated_fixture_have_different_owners():
    req = default_request(AgentCommand.VERIFY_IMPLEMENTATION)
    checks = [{"id": key, "required": True} for key in ("formal_entrypoint", "prefix")]
    with pytest.raises(FixtureError) as err:
        paired_inputs(lambda: [1, 2], lambda sample: None, control=lambda s: s[0], target=lambda s: s[1])
    rows = [{"id": "prefix", "status": "ERROR", "details": ["FixtureError: " + str(err.value)]}]
    def route(rows, previous, author_smoke=True):
        return probe_actions(rows, checks, previous=previous, author_smoke=author_smoke)
    def repair(actions):
        return can_repair_probe(req, {"phase": "review", "probe_repairs_remaining": 2, "probe_actions": actions})
    first = route(rows, {})
    assert first["fixture_only"] and repair(first)
    repeated = route(rows, first)
    assert repeated["repeated_fixtures"] == ["prefix"] and not repair(repeated)
    feedback = experiment_execution_feedback(None, None, {"implementation_verification":
        {"report": {"verdict": "inconclusive"}, "probe_actions": repeated}})
    assert feedback["failure_category"] == "verifier_fixture_inconclusive"
    mixed = [*rows, {"id": "formal_entrypoint", "status": "FAIL", "details": ["exit=1"]}]
    assert not repair(route(mixed, {}))
    assert repair(route(mixed, {}, author_smoke=False))
    assert not route([{**rows[0], "failures": 1}], {}).get("fixture_only")
