"""Configuration evidence, not a successful process alone, supports selection."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from hypoloom.configuration_study import StudyError, audit_study, main


@pytest.fixture
def plan():
    config = {"architecture": {"layers": 2, "options": [True]},
              "training": {"lr": 0.001, "batch": 16}, "adaptation": {"classes": 4},
              "runtime": {"num_workers": 0}}
    candidates = []
    for identity, lr in (("reference", 0.001), ("alternative", 0.003)):
        configs = {dataset: deepcopy(config) for dataset in ("small", "large")}
        for settings in configs.values():
            settings["training"]["lr"] = lr
        configs["large"]["adaptation"]["classes"] = 6
        candidates.append({"id": identity, "configs": configs, "rationale": "A bounded sensitivity comparison."})
    return {"schema_version": 1, "datasets": ["small", "large"], "seeds": [0, 1],
            "metric": "accuracy", "direction": "maximize", "dataset_weights": {"small": 1, "large": 1},
            "max_trials": 10, "shared_architecture": True, "shared_training": True,
            "context": {"implementation_ref": "git:implementation", "data_refs": {"small": "split:s", "large": "split:l"}},
            "candidates": candidates, "evaluation": {"split": "test", "seeds": [2, 3, 4]}}


def observations(plan, *, final=False):
    candidates = plan["candidates"][1:] if final else plan["candidates"]
    seeds = plan["evaluation"]["seeds"] if final else plan["seeds"]
    return [{"candidate_id": candidate["id"], "dataset": dataset, "seed": seed,
             "attempt": 1, "status": "completed", "evidence_ref": f"output:{candidate['id']}:{dataset}:{seed}",
             "resolved_config": deepcopy(candidate["configs"][dataset]),
             "implementation_ref": plan["context"]["implementation_ref"],
             "data_ref": plan["context"]["data_refs"][dataset], "metric": plan["metric"],
             "split": "test" if final else "validation",
             "value": {"reference": {"small": 0.8, "large": 0.4}, "alternative": {"small": 0.7, "large": 0.9}}[candidate["id"]][dataset]}
            for candidate in candidates for dataset in plan["datasets"] for seed in seeds]


def selected(plan):
    return {"candidate_id": "alternative", "configs": deepcopy(plan["candidates"][1]["configs"])}


def test_progressive_single_seed_then_confirmation_keeps_recipe_and_test_separate(plan):
    from hypoloom.configuration_study import audit_progression
    pilot = deepcopy(plan); pilot["seeds"] = [0]
    confirm = deepcopy(plan); confirm["candidates"] = [confirm["candidates"][1]]
    stages = [dict(name="pilot", basis="cheap paired screening", plan=pilot, trials=observations(pilot)),
              dict(name="confirm", basis="confirm the validation-selected alternative", plan=confirm,
                   trials=observations(confirm), selection=selected(plan))]
    report = audit_progression(stages)
    assert report["stages"][0]["planned_trials"] == 4
    assert report["stages"][1]["planned_trials"] == 4
    assert report["scientific_acceptance"] == "task_owned"
    changed = deepcopy(stages)
    for settings in changed[1]["plan"]["candidates"][0]["configs"].values():
        settings["training"]["lr"] = 0.9
    with pytest.raises(StudyError, match="configuration changed"):
        audit_progression(changed)
    changed = deepcopy(stages); changed[0]["trials"] = changed[0]["trials"][:2]
    with pytest.raises(StudyError, match="incomplete or unmeasured"):
        audit_progression(changed)
    changed = deepcopy(stages); changed[0]["selection"] = selected(plan); changed[0]["evaluation"] = []
    with pytest.raises(StudyError, match="final observations"):
        audit_progression(changed)


def test_complete_study_keeps_per_dataset_regression_visible_and_final_separate(plan):
    trials, selection, final = observations(plan), selected(plan), observations(plan, final=True)
    before = deepcopy((plan, trials, selection, final))
    report = audit_study(plan, trials, selection=selection, evaluation=final)
    assert (plan, trials, selection, final) == before
    assert report["search_complete"] and report["recommendation_scope"] == "complete_plan"
    assert report["ranking"] == ["alternative", "reference"]
    assert report["candidates"]["alternative"]["score"] == "0.8"
    assert report["candidates"]["alternative"]["per_dataset"]["small"]["mean"] == "0.7"
    assert report["candidates"]["reference"]["per_dataset"]["small"]["mean"] == "0.8"
    assert report["selection"]["status"] == "frozen"
    assert report["evaluation"]["complete"] and not report["evaluation"]["used_for_selection"]
    for record in final:
        record["value"] = -1000
    changed = audit_study(plan, trials, selection=selection, evaluation=final)
    assert changed["ranking"] == report["ranking"] and changed["candidates"] == report["candidates"]


def test_partial_search_does_not_turn_unrun_or_partial_candidates_into_losers(plan):
    trials = observations(plan)[:5]
    selection = {"candidate_id": "reference", "configs": deepcopy(plan["candidates"][0]["configs"])}
    report = audit_study(plan, trials, selection=selection)
    assert not report["search_complete"] and len(report["missing"]) == 3
    assert report["ranking"] == ["reference"]
    assert report["selection"]["status"] == "provisional"
    assert not report["candidates"]["alternative"]["eligible"]
    assert report["candidates"]["alternative"]["score"] is None
    empty = audit_study(plan, trials[:1])
    assert empty["recommended_candidate"] is None and empty["recommendation_scope"] == "no_complete_candidate"
    with pytest.raises(StudyError, match="validation ranking"):
        audit_study(plan, trials[:1], selection=selection)


@pytest.mark.parametrize("status", ["failed", "interrupted"])
def test_retry_preserves_failure_budget_and_missing_cells(plan, status):
    trials = observations(plan)
    failed = {key: trials[0][key] for key in ("candidate_id", "dataset", "seed", "attempt", "evidence_ref")}
    failed.update(status=status, reason="process ended before measurement")
    partial = audit_study(plan, [failed, *trials[1:]])
    assert partial["counts"][status] == 1 and partial["missing"] == [{"candidate_id": "reference", "dataset": "small", "seed": 0}]
    trials[0]["attempt"] = 2
    report = audit_study(plan, [*trials, failed])
    assert report["search_complete"] and report["attempted_trials"] == 9
    assert report["counts"][status] == 1 and report["counts"]["completed"] == 8
    plan["max_trials"] = 8
    with pytest.raises(StudyError, match="retries count"):
        audit_study(plan, [failed, *trials])


@pytest.mark.parametrize("change,match", [
    (lambda p, t: t.append(deepcopy(t[0])), "duplicate completed"),
    (lambda p, t: t[0].update(attempt=2), "missing attempt"),
    (lambda p, t: t[0].update(seed=True), "expected integer"),
    (lambda p, t: t[0].update(seed=8), "unplanned"),
    (lambda p, t: t[0].update(split="test"), "split differs"),
    (lambda p, t: t[0].update(metric="test_accuracy"), "metric differs"),
    (lambda p, t: t[0].update(value=float("nan")), "finite JSON"),
    (lambda p, t: t[0].update(value=True), "finite JSON"),
    (lambda p, t: t[0].update(implementation_ref="git:another"), "implementation_ref differs"),
    (lambda p, t: t[0].update(data_ref="split:test-leakage"), "data_ref differs"),
    (lambda p, t: t[0]["resolved_config"]["training"].update(batch=64), "resolved_config differs"),
    (lambda p, t: t[0]["resolved_config"]["architecture"].update(options=[1]), "resolved_config differs"),
    (lambda p, t: t[0]["resolved_config"]["runtime"].update(num_workers=2), "resolved_config differs"),
    (lambda p, t: t[0].update(duration_seconds=-1), "nonnegative"),
    (lambda p, t: t[0].update(unknown=1), "unknown"),
    (lambda p, t: p.update(max_trials=7), "smaller than"),
    (lambda p, t: p.update(shared_architecture="yes"), "expected boolean"),
    (lambda p, t: p.update(seeds=[0, 0]), "duplicate seed"),
    (lambda p, t: p["candidates"].append(deepcopy(p["candidates"][0])), "duplicate candidate"),
    (lambda p, t: p["dataset_weights"].update(small=0), "positive"),
    (lambda p, t: p["dataset_weights"].update(small=float("inf")), "finite JSON"),
    (lambda p, t: p["candidates"][0]["configs"]["large"]["architecture"].update(layers=3), "architecture differs"),
    (lambda p, t: p["candidates"][0]["configs"]["large"]["training"].update(batch=32), "training differs"),
])
def test_invalid_evidence_never_produces_a_selection(plan, change, match):
    trials = observations(plan)
    change(plan, trials)
    with pytest.raises(StudyError, match=match):
        audit_study(plan, trials)


def test_sharing_is_task_specific_and_adaptation_can_differ(plan):
    plan["shared_architecture"] = plan["shared_training"] = False
    plan["candidates"][0]["configs"]["large"]["architecture"]["layers"] = 3
    plan["candidates"][0]["configs"]["large"]["training"]["batch"] = 32
    assert audit_study(plan, observations(plan))["search_complete"]


def test_direction_weights_and_exact_ties_follow_declared_rule(plan):
    plan["direction"] = "minimize"
    assert audit_study(plan, observations(plan))["recommended_candidate"] == "reference"
    plan["direction"] = "maximize"
    plan["dataset_weights"]["small"] = 10
    assert audit_study(plan, observations(plan))["recommended_candidate"] == "reference"
    trials = observations(plan)
    for record in trials:
        record["value"] = 0.5
    assert audit_study(plan, list(reversed(trials)))["ranking"] == ["reference", "alternative"]


def test_selection_and_final_evaluation_cannot_drift(plan):
    trials, selection, final = observations(plan), selected(plan), observations(plan, final=True)
    report = audit_study(plan, trials, selection=selection, evaluation=final[:-1])
    assert not report["evaluation"]["complete"] and len(report["evaluation"]["missing"]) == 1
    assert report["evaluation"]["verified_completed_trials"] == len(final) - 1
    empty = audit_study(plan, trials, selection=selection, evaluation=[])
    assert not empty["evaluation"]["complete"] and empty["evaluation"]["verified_completed_trials"] == 0
    bad = deepcopy(selection)
    bad["configs"]["small"]["training"]["lr"] = 0.01
    with pytest.raises(StudyError, match="configurations differ"):
        audit_study(plan, trials, selection=bad)
    final[0]["resolved_config"]["architecture"]["layers"] = 4
    with pytest.raises(StudyError, match="resolved_config differs"):
        audit_study(plan, trials, selection=selection, evaluation=final)
    with pytest.raises(StudyError, match="requires a selection"):
        audit_study(plan, trials, evaluation=[])


def test_no_retry_after_success_or_hidden_failed_attempt(plan):
    trials = observations(plan)
    failed = {key: trials[0][key] for key in ("candidate_id", "dataset", "seed", "evidence_ref")}
    failed.update(attempt=2, status="failed", reason="later run")
    with pytest.raises(StudyError, match="retry after a completed"):
        audit_study(plan, [*trials, failed])


def test_cli_and_standalone_script_are_read_only_and_reject_bad_json(plan, tmp_path, capsys):
    paths = {"plan": plan, "trials": observations(plan), "selection": selected(plan),
             "evaluation": observations(plan, final=True)}
    argv = []
    for name, value in paths.items():
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(value))
        argv.extend(("--" + name, str(path)))
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    assert main(argv) == 0
    expected = json.loads(capsys.readouterr().out)
    import hypoloom.configuration_study as module
    script = tmp_path / "configuration_study.py"
    script.write_bytes(Path(module.__file__).read_bytes())
    command = subprocess.run([sys.executable, "-I", str(script), *argv], cwd=tmp_path,
                             capture_output=True, text=True, timeout=10)
    assert command.returncode == 0, command.stderr
    assert json.loads(command.stdout) == expected
    assert all((tmp_path / name).read_bytes() == content for name, content in before.items())
    (tmp_path / "trials.json").write_text('[{"seed": 0, "seed": 1}]')
    assert main(argv) == 2 and "duplicate JSON key" in capsys.readouterr().err
    (tmp_path / "trials.json").write_text('[NaN]')
    assert main(argv) == 2 and "nonfinite JSON" in capsys.readouterr().err
