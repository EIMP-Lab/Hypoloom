"""Scientific boundaries exercised through the parser sandbox and the probe runner."""
from decimal import localcontext
import json
from pathlib import Path
import subprocess
import sys

import pytest

from hypoloom.domain import normalize_metric_value
from hypoloom.evaluator_examples import CASES, fixture_path, load_seed, prepare_seed
from hypoloom.native_output import parser_smoke
from hypoloom.shared_acceptance import (SharedAcceptance, evaluate_shared_acceptance,
    normalize_goal_evaluation_observation, normalize_shared_observation)
from hypoloom.shared_acceptance_oracle import interpret_shared_acceptance
from hypoloom.verification_probe import RUNNER_PATH, RESULT_PATH, execution_files


DECLARATION = {"summary": "Positive score on complete samples", "rule": "ALL",
    "criteria": [{"name": "score", "operator": "GT", "target": "0"}], "validity_checks": ["complete"],
    "diagnostics": {"cache_matches": "BOOLEAN", "offset": "DECIMAL"}, "parser_argv": ["python", "score.py"],
    "parser_files": ["score.py"], "raw_artifacts": ["samples.json"]}

PARSER = b"""import json
from pathlib import Path
r = json.loads(Path('/raw/samples.json').read_bytes())
result = {'measurements': {'score': r['score']}, 'checks': {'complete': len(r['ids']) == 2},
          'diagnostics': {'cache_matches': r['cache'] == r['ids'], 'offset': r['offset']}}
Path('/parsed/result.json').write_text(json.dumps(result))
"""

INPUTS = {"baseline": ([1, 2], [1, 2], 1), "cache_only": ([1, 2], [2, 1], 1),
          "missing": ([1], [1], 1), "boundary": ([1, 2], [1, 2], 0)}


def package():
    cases = {}
    files = {"score.py": PARSER, "acceptance.json": json.dumps(
        {"protocol": "complete-v1", "acceptance": DECLARATION}).encode()}
    for name, (ids, cache, score) in INPUTS.items():
        observation = {"measurements": {"score": score}, "checks": {"complete": len(ids) == 2},
                       "diagnostics": {"cache_matches": ids == cache, "offset": "0.00"}}
        cases[name] = {"goal_met": len(ids) == 2 and score > 0, "observation": observation}
        files[fixture_path(name, "samples.json")] = json.dumps(
            dict(ids=ids, cache=cache, score=score, offset="0.00")).encode()
    files[CASES] = json.dumps(cases).encode()
    return files


def test_report_only_diagnostics_survive_publication_and_independent_decision():
    accepted = SharedAcceptance.from_dict(DECLARATION)
    for case in json.loads(package()[CASES]).values():
        result = normalize_goal_evaluation_observation({"source_attempt_id": "grounding", **case["observation"]})
        observation = {k: v for k, v in result.items() if k != "source_attempt_id"}
        independent = interpret_shared_acceptance(DECLARATION, observation)
        assert independent.observations == observation
        assert observation["diagnostics"]["offset"] == "0"
        assert evaluate_shared_acceptance(accepted, observation) is independent.goal_met is case["goal_met"]
    legacy = {k: v for k, v in DECLARATION.items() if k != "diagnostics"}
    assert SharedAcceptance.from_dict(legacy).to_dict() == legacy


@pytest.mark.parametrize("diagnostics", [None, {}, {"cache_matches": 1, "offset": 0},
    {"cache_matches": False, "offset": "NaN"}, {"cache_matches": False, "offset": 0, "extra": True}])
def test_only_declared_diagnostic_names_and_types_are_accepted(diagnostics):
    observation = {"measurements": {"score": 1}, "checks": {"complete": True}}
    if diagnostics is not None:
        observation["diagnostics"] = diagnostics
    with pytest.raises(ValueError):
        normalize_shared_observation(observation, SharedAcceptance.from_dict(DECLARATION))
    with pytest.raises(ValueError):
        interpret_shared_acceptance(DECLARATION, observation)


@pytest.mark.parametrize("precision", [3, 28, 60])
def test_strict_threshold_and_canonical_output_are_context_independent(precision):
    base = 10**28
    with localcontext() as context:
        context.prec = precision
        declaration = {**DECLARATION, "criteria": [{"name": "score", "operator": "GT", "target": base}]}
        spec = SharedAcceptance.from_dict(declaration)
        for score in (base - 1, base, base + 1):
            obs = {"measurements": {"score": score}, "checks": {"complete": True},
                   "diagnostics": {"cache_matches": False, "offset": "1.2000e-10000"}}
            assert normalize_shared_observation(obs, spec)["measurements"]["score"] == str(score)
            replay = interpret_shared_acceptance(declaration, obs)
            assert replay.goal_met is evaluate_shared_acceptance(spec, obs) is (score > base)
            assert replay.observations["diagnostics"]["offset"] == "1.2E-10000"
        assert normalize_metric_value("12e9998") == "1.2E+9999"
        assert normalize_metric_value("-0.000") == "0"


def smoke(root, work, *, seed=None):
    return parser_smoke(json.dumps({"typed_candidate": {"acceptance": DECLARATION}}),
        source_root=root, test_root=work, seed_files=seed, timeout_seconds=15, max_bytes=65536)


def test_paired_smoke_and_pinned_examples_catch_a_false_gate(tmp_path):
    root = tmp_path / "acceptance"
    files = package()
    prepare_seed(root, files)
    assert smoke(root, tmp_path)["error"] is None
    wrong = PARSER.replace(b"len(r['ids']) == 2", b"len(r['ids']) == 2 and r['cache'] == r['ids']")
    (root / "score.py").write_bytes(wrong)
    assert "case cache_only" in smoke(root, tmp_path)["error"]
    # Reopening a Grounding session keeps the edited draft. Its author cannot
    # erase the originally selected scientific examples by deleting the manifest.
    prepare_seed(root, files)
    (root / CASES).unlink()
    assert "seed/cache_only" in smoke(root, tmp_path, seed=files)["error"]


def test_seed_reads_only_reviewed_git_blobs_with_exact_protocol_and_bounded_size(tmp_path):
    root = tmp_path / "target"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    files = package()
    prepare_seed(root / "protocol", files)
    (root / "protocol/unrelated").write_text("unreviewed note")
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
    git("add", "protocol")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "protocol")
    commit = git("rev-parse", "HEAD")
    spec = {"path": "protocol", "protocol": "complete-v1"}
    (root / "protocol/score.py").write_text("modified draft")
    assert load_seed(spec, root, commit, 65536) == files
    for bad in ({**spec, "path": "../protocol"}, {**spec, "protocol": "other"}, {**spec, "path": "absent"}):
        with pytest.raises(ValueError):
            load_seed(bad, root, commit, 65536)
    with pytest.raises(ValueError, match="budget"):
        load_seed(spec, root, commit, 5)
    (root / "protocol/score.py").unlink()
    (root / "protocol/score.py").symlink_to("unrelated")
    git("add", "protocol")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "linked source")
    with pytest.raises(ValueError, match="regular Git blob"):
        load_seed(spec, root, git("rev-parse", "HEAD"), 65536)


@pytest.mark.parametrize("mode,status", [("valid", "PASS"), ("no_change", "ERROR"),
    ("spill", "ERROR"), ("wrong_output", "FAIL"), ("no_assertion", "ERROR")])
def test_paired_probe_distinguishes_invalid_fixture_from_implementation_failure(tmp_path, mode, status):
    probe = {"argv": ["python", "verification/p.py"], "checks": [
        {"id": "pair", "kind": "assertion", "required": True, "purpose": "Locality", "basis": "fixed prefix"}]}
    files = execution_files(probe, attempt_id="a", report_ref="r")
    files["verification/p.py"] = ("""from hypoloom_probe import check, paired_inputs
draws = []
def draw():
    draws.append(1)
    return {'prefix': [len(draws)], 'suffix': [0]}
def change(batch):
    if mode != 'no_change': batch['suffix'][0] = 1
    if mode == 'spill': batch['prefix'].append(2)
with check('pair') as c:
    a,b = paired_inputs(draw, change, control=lambda x:x['prefix'], target=lambda x:x['suffix'])
    if mode != 'no_assertion':
        c.equal(len(draws),1)
        model = (lambda x: sum(x['suffix'])) if mode == 'wrong_output' else (lambda x: sum(x['prefix']))
        c.equal(model(a),model(b))
""").encode()
    files["verification/p.py"] = ("mode = " + repr(mode) + "\n").encode() + files["verification/p.py"]
    for name, data in files.items():
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_bytes(data)
    process = subprocess.run([sys.executable, str(tmp_path / RUNNER_PATH)], cwd=tmp_path, capture_output=True, timeout=10)
    assert json.loads((tmp_path / RESULT_PATH).read_text())["checks"][0]["status"] == status
    assert (process.returncode == 0) is (status == "PASS")



def test_seed_rejects_silent_threshold_drift_even_when_examples_still_pass(tmp_path):
    root = tmp_path / "draft"
    files = package()
    prepare_seed(root, files)
    changed = {**DECLARATION, "criteria": [{"name": "score", "operator": "GT", "target": "0.5"}]}
    smoke = parser_smoke(json.dumps({"typed_candidate": {"acceptance": changed}}), source_root=root,
        test_root=tmp_path, timeout_seconds=10, max_bytes=65536, seed_files=files)
    assert "seed contract differs" in smoke["error"]


def test_wire_fraction_preserves_digits_through_isolated_parser_replay(tmp_path):
    from hypoloom.measurement_oracle import _read_result
    root = tmp_path / "draft"
    (root / "smoke").mkdir(parents=True)
    result = b'{"measurements":{"score":1.00000000000000000000000000001},"checks":{} }'
    (root / "smoke/row").write_bytes(result)
    (root / "p.py").write_text("from pathlib import Path\nPath('/parsed/result.json').write_bytes(Path('/raw/row').read_bytes())")
    spec = {"summary": "Strict improvement", "criteria": [{"name": "score", "operator": "GT", "target": "1"}],
            "rule": "ALL", "validity_checks": [], "parser_argv": ["python", "p.py"], "parser_files": ["p.py"], "raw_artifacts": ["row"]}
    parsed = parser_smoke(json.dumps({"typed_candidate": {"acceptance": spec}}),
        source_root=root, test_root=tmp_path, max_bytes=4096, timeout_seconds=10)
    assert parsed["error"] is None
    independent = _read_result(root / "smoke/row")
    assert parsed["observation"] == independent
    assert interpret_shared_acceptance(spec, independent).goal_met
    assert evaluate_shared_acceptance(SharedAcceptance.from_dict(spec), parsed["observation"])
