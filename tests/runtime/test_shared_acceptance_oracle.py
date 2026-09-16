from copy import deepcopy

import pytest

from hypoloom.shared_acceptance_oracle import interpret_shared_acceptance


def acceptance(rule="ALL"):
    return {
        "summary": "Two exact metrics with mandatory validity",
        "rule": rule,
        "criteria": [
            {"name": "left", "operator": "GT", "target": "0.6674"},
            {"name": "right", "operator": "GT", "target": "0.7305"},
        ],
        "validity_checks": ["valid"],
        "parser_argv": ["python", "evaluate.py"],
        "parser_files": ["evaluate.py"],
        "raw_artifacts": ["trials.json"],
    }


def test_canonical_grounding_mode_stops_for_common_success_with_local_refutation():
    from tests.runtime.test_shared_goal_finish_oracle import stopped_batch, verdict_issues
    from hypoloom.batch_oracle import shared_goal_stop
    from hypoloom.oracle import _ComponentResult

    data = stopped_batch()
    goal = data.fact_payloads[data.facts[0]["fact_id"]]["research_contract"]
    goal["schema_version"] = 3
    goal["goal"]["evaluation"] = {"mode": "grounding"}
    goal["methodology"]["controls"]["product"].pop("goal")
    for row in data.facts:
        value = data.fact_payloads[row["fact_id"]]
        if row["fact_type"] == "ExperimentPreregistered":
            value["goal_evaluation"] = {
                "scope": "GOAL",
                "source_attempt_id": "grounding",
                "acceptance": acceptance(),
            }
        if row["fact_type"] == "CanonicalOutcomeAccepted":
            value["goal_evaluation"] = {
                "source_attempt_id": "grounding",
                "measurements": {"left": ".8", "right": ".8"},
                "checks": {"valid": True},
            }
    data.fact_payloads["fact-assessment-1"]["assessment"] = "REFUTED"
    assert (
        shared_goal_stop(
            data.facts, data.fact_payloads, data.transitions, "hypoloom-lifecycle-v9"
        )
        is not None
    )
    assert (
        verdict_issues(
            data,
            winner_result=_ComponentResult(
                "CLEAN_REFUTATION", False, ".1", None, None, True, True
            ),
        )
        == []
    )


@pytest.mark.parametrize(
    "left,right,valid,expected",
    [
        (".6674", ".8", True, False),
        (".8", ".7305", True, False),
        (".6674000000000001", ".7305000000000001", True, True),
        (".8", ".8", False, False),
    ],
)
def test_exact_strict_conjunction(left, right, valid, expected):
    result = interpret_shared_acceptance(
        acceptance(),
        {"measurements": {"left": left, "right": right}, "checks": {"valid": valid}},
    )
    assert result.goal_met is expected


def test_any_never_weakens_mandatory_validity():
    raw = {"measurements": {"left": ".8", "right": ".1"}, "checks": {"valid": True}}
    assert interpret_shared_acceptance(acceptance("ANY"), raw).goal_met
    raw["checks"]["valid"] = False
    assert not interpret_shared_acceptance(acceptance("ANY"), raw).goal_met


def test_artifact_goal_does_not_require_numeric_completion_score():
    declaration = acceptance()
    declaration["criteria"] = [{"name": "artifact_is_loadable", "operator": "IS_TRUE"}]
    raw = {"measurements": {}, "checks": {"artifact_is_loadable": True, "valid": True}}
    assert interpret_shared_acceptance(declaration, raw).goal_met


@pytest.mark.parametrize(
    "damage",
    ["missing", "extra", "verdict", "number-bool", "check-number", "nan", "overlap"],
)
def test_raw_common_observations_are_not_agent_verdicts(damage):
    declaration = acceptance()
    raw = {"measurements": {"left": ".8", "right": ".8"}, "checks": {"valid": True}}
    if damage == "missing":
        del raw["measurements"]["right"]
    if damage == "extra":
        raw["measurements"]["unregistered"] = 1
    if damage == "verdict":
        raw["goal_met"] = True
    if damage == "number-bool":
        raw["measurements"]["left"] = True
    if damage == "check-number":
        raw["checks"]["valid"] = 1
    if damage == "nan":
        raw["measurements"]["left"] = "NaN"
    if damage == "overlap":
        declaration["validity_checks"] = ["left"]
    with pytest.raises(ValueError):
        interpret_shared_acceptance(declaration, raw)


def test_oracle_does_not_call_production_qualification(monkeypatch):
    import hypoloom.shared_acceptance as production

    monkeypatch.setattr(
        production,
        "evaluate_shared_acceptance",
        lambda *_: pytest.fail("production comparison was called"),
    )
    declaration = deepcopy(acceptance())
    assert interpret_shared_acceptance(
        declaration,
        {"measurements": {"left": ".8", "right": ".8"}, "checks": {"valid": True}},
    ).goal_met


@pytest.mark.parametrize("damage", [None, "source", "raw", "parsed", "target"])
def test_shared_parser_replay_uses_real_raw_publication(tmp_path, damage):
    import json
    import sys
    from hypoloom.shared_acceptance_oracle import audit_shared_result

    declaration = acceptance()
    source = b"""import json
from pathlib import Path
rows = json.loads(Path('/raw/trials.json').read_text())
Path('/parsed/result.json').write_text(json.dumps({'measurements':rows,'checks':{'valid':True}}))
"""
    publication = tmp_path / "publication"
    (publication / "raw").mkdir(parents=True)
    (publication / "goal/parser").mkdir(parents=True)
    (publication / "raw/trials.json").write_text(
        json.dumps({"left": ".8", "right": ".8"})
    )
    (publication / "goal/parser/evaluate.py").write_bytes(source)
    (publication / "goal/result.json").write_text(
        json.dumps(
            {"measurements": {"left": ".8", "right": ".8"}, "checks": {"valid": True}}
        )
    )
    if damage == "source":
        (publication / "goal/parser/evaluate.py").write_bytes(source + b"# changed\n")
    if damage == "raw":
        (publication / "raw/trials.json").write_text(
            json.dumps({"left": ".7", "right": ".8"})
        )
    if damage == "parsed":
        (publication / "goal/result.json").write_text(
            json.dumps(
                {
                    "measurements": {"left": ".9", "right": ".8"},
                    "checks": {"valid": True},
                }
            )
        )
    if damage == "target":
        declaration["criteria"][0]["target"] = ".8"
    if damage in {"source", "raw", "parsed"}:
        with pytest.raises(ValueError):
            audit_shared_result(
                declaration,
                {"evaluate.py": source},
                publication,
                runtime_prefix=sys.prefix,
                python_executable=sys.executable,
            )
    else:
        result = audit_shared_result(
            declaration,
            {"evaluate.py": source},
            publication,
            runtime_prefix=sys.prefix,
            python_executable=sys.executable,
        )
        assert result.goal_met is (damage is None)


@pytest.mark.parametrize(
    "damage",
    [None, "question", "source", "binding", "archive", "duplicate", "expert-bytes"],
)
def test_common_source_is_one_actual_final_grounding(tmp_path, damage):
    import base64
    import json
    from hypoloom.shared_acceptance_oracle import audit_shared_bindings

    source = b"print('accepted source')\n"
    encoded = {
        "path": "evaluate.py",
        "content_base64": base64.b64encode(source).decode(),
    }
    row = {
        "attempt_id": "grounding-1",
        "kind": "AGENT",
        "state": "CLOSED",
        "terminal_reason": "SUCCEEDED",
        "run_id": "run-1",
        "workflow_id": "run-1",
        "command_id": "grounding-command",
        "ended_at": 1.0,
    }
    request = {
        "command": "GROUNDING",
        "run_id": "run-1",
        "attempt_id": "grounding-1",
        "command_id": "grounding-command",
        "schema_version": 16,
    }
    response = {
        "typed_candidate": {
            "acceptance": acceptance(),
            "background": {},
            "clarification": None,
        },
        "proposed_workspace_artifacts": [encoded],
    }
    archive = tmp_path / "run-1/agent/grounding-1/transcript/evaluator"
    archive.mkdir(parents=True)
    (archive / "evaluate.py").write_bytes(source)
    observations = {
        "grounding-1": {
            "request": request,
            "response": response,
            "session": {
                "transcript": {"artifact_id": "run-1:agent:grounding-1:transcript"}
            },
        }
    }
    binding = {
        "scope": "GOAL",
        "source_attempt_id": "grounding-1",
        "acceptance": acceptance(),
    }
    facts = [
        {"fact_type": "GoalDefined", "fact_id": "goal"},
        {
            "fact_type": "ExperimentPreregistered",
            "fact_id": "spec",
            "execution_id": "execution-1",
        },
    ]
    payloads = {
        "goal": {
            "research_contract": {
                "schema_version": 3,
                "goal": {"evaluation": {"mode": "grounding"}},
            }
        },
        "spec": {"goal_evaluation": binding},
    }
    attempts = [row]
    if damage == "question":
        response["typed_candidate"]["clarification"] = {"question": "not final"}
    if damage == "source":
        binding["source_attempt_id"] = "foreign"
    if damage == "binding":
        binding["acceptance"]["criteria"][0]["operator"] = "GE"
    if damage == "archive":
        (archive / "evaluate.py").write_bytes(b"changed")
    if damage == "duplicate":
        attempts.append({**row, "attempt_id": "grounding-2"})
        observations["grounding-2"] = deepcopy(observations["grounding-1"])
    if damage == "expert-bytes":
        changed = {
            "source_attempt_id": "grounding-1",
            "acceptance": acceptance(),
            "files": [
                {
                    **encoded,
                    "content_base64": base64.b64encode(b"expert replacement").decode(),
                }
            ],
        }
        observations["expert"] = {
            "request": {
                "input_artifacts": [
                    {
                        "ref": "run-1--shared-acceptance",
                        "media_type": "application/json",
                        "content_base64": base64.b64encode(
                            json.dumps(changed).encode()
                        ).decode(),
                    }
                ],
                "immutable_context_refs": ["run-1--shared-acceptance"],
            }
        }
        attempts.append({"attempt_id": "expert"})
    issues = audit_shared_bindings(
        tmp_path, "run-1", facts, payloads, attempts, observations
    )
    assert bool(issues) is (damage is not None), issues


@pytest.mark.parametrize(
    "outcome",
    ["INTEGRITY_REJECTED", "CANCELLED", "EXECUTION_FAILURE", "CLEAN_REFUTATION"],
)
@pytest.mark.parametrize("observations_present", [True, False])
def test_rejected_or_cancelled_canonical_evidence_cannot_qualify(
    tmp_path, outcome, observations_present
):
    from hypoloom.shared_acceptance_oracle import audit_shared_bindings

    payload = {
        "outcome": outcome,
        "goal_evaluation": {"measurements": {}, "checks": {}}
        if observations_present
        else None,
    }
    issues = audit_shared_bindings(
        tmp_path,
        "run",
        [{"fact_type": "CanonicalOutcomeAccepted", "fact_id": "outcome"}],
        {"outcome": payload},
        [],
        {},
    )
    assert bool(issues) is (
        observations_present and outcome in {"INTEGRITY_REJECTED", "CANCELLED"}
    )
    if issues:
        assert issues[0][0] == "shared_acceptance_disposition_invalid"


def test_common_bindings_accept_review_request_without_task_context(tmp_path):
    from hypoloom.shared_acceptance_oracle import audit_shared_bindings

    facts = [{"fact_type": "GoalDefined", "fact_id": "goal"}]
    payloads = {
        "goal": {
            "research_contract": {
                "schema_version": 3,
                "goal": {"evaluation": {"mode": "grounding"}},
            }
        }
    }
    assert (
        audit_shared_bindings(
            tmp_path,
            "run",
            facts,
            payloads,
            [{"attempt_id": "review"}],
            {
                "review": {
                    "request": {"command": "ANSWER_RENDEZVOUS", "task_context": None}
                }
            },
        )
        == []
    )


@pytest.mark.parametrize(
    "case",
    [
        "review-rejected",
        "local-failed",
        "goal-failed",
        "changed-vector",
        "invented-diagnostic",
        "missing-diagnostic",
        "goal-only-invalid",
        "goal-only-invalid-refutation",
        "goal-only-valid-refutation",
    ],
)
def test_shared_component_keeps_local_results_separate_from_public_diagnostics(
    tmp_path, monkeypatch, case
):
    import json
    import sys
    from types import SimpleNamespace
    import hypoloom.measurement_oracle as measurement
    import hypoloom.oracle as oracle
    import hypoloom.shared_acceptance_oracle as shared

    declaration = acceptance()
    raw = {"measurements": {"left": "0.8", "right": "0.8"}, "checks": {"valid": True}}
    goal_only = case.startswith("goal-only-")
    invalid = case.startswith("goal-only-invalid")
    if goal_only:
        declaration["criteria"] = [{"name": "artifact_ok", "operator": "IS_TRUE"}]
        raw = {"measurements": {}, "checks": {"artifact_ok": invalid, "valid": not invalid}}
    public = {"source_attempt_id": "grounding", **raw}
    files = {"evaluate.py": b"accepted evaluator"}
    (tmp_path / "goal/parser").mkdir(parents=True)
    (tmp_path / "goal/parser/evaluate.py").write_bytes(files["evaluate.py"])
    (tmp_path / "goal/result.json").write_text(json.dumps(raw))
    monkeypatch.setattr(
        measurement,
        "audit_measurement_result",
        lambda *_args, **_kwargs: SimpleNamespace(metric_value="1", criterion_met=True),
    )
    monkeypatch.setattr(
        shared, "grounding_source", lambda *_args: (declaration, files, {})
    )
    monkeypatch.setattr(
        shared,
        "audit_shared_result",
        lambda *_args, **_kwargs: interpret_shared_acceptance(declaration, raw),
    )
    monkeypatch.setattr(oracle, "_raw_submit_source", lambda *_args: ({}, {}))
    monkeypatch.setattr(oracle, "_decode_workspace", lambda *_args: {})
    code = (
        71
        if case == "local-failed"
        else 72
        if case in {"goal-failed", "missing-diagnostic"}
        else 0
    )
    rejected = case == "review-rejected"
    actual = (
        "INTEGRITY_REJECTED"
        if rejected
        else "EXECUTION_FAILURE"
        if code == 71
        else "SUCCESS"
    )
    if goal_only:
        actual = "EXECUTION_FAILURE" if case == "goal-only-invalid" else "CLEAN_REFUTATION"
    metric = None if rejected or code == 71 or goal_only else "1"
    vector = None if rejected or code == 72 else public
    candidate = {
        "outcome": actual,
        "evaluator_version": "component-result/9",
        "metric_value": metric,
        "criterion_met": None if metric is None else True,
        "validation_error": "analysis_declined" if rejected else None,
        "execution_integrity_error": None,
        "goal_evaluation": vector,
        "parser_validation_error": "local parser did not complete successfully"
        if code == 71
        else "goal parser did not complete successfully"
        if code == 72
        else None,
    }
    if case == "changed-vector":
        candidate["goal_evaluation"] = {**public, "checks": {"valid": False}}
    if case == "invented-diagnostic":
        candidate["parser_validation_error"] = "goal parser failed"
    if case == "missing-diagnostic":
        candidate["parser_validation_error"] = None
    if case == "goal-only-invalid":
        candidate.update(validation_error="goal validity checks failed: valid",
            parser_validation_error="goal validity checks failed: valid")
    if goal_only and case != "goal-only-invalid":
        candidate["criterion_met"] = False
    payload = {
        "outcome": actual,
        "metric_value": metric,
        "logical_artifacts": [],
        "goal_evaluation": vector,
        "evaluator_version": "component-result/9",
    }
    spec = {
        "goal_evaluation": {
            "scope": "GOAL",
            "source_attempt_id": "grounding",
            "acceptance": declaration,
        },
        "measurement": None if goal_only else {},
        "execution_contract": {
            "source_submit_command_id": "submit",
            "timeout_seconds": 5,
        },
        "evaluator_version": "component-result/9",
    }
    process = {
        "terminal_reason": "SUCCEEDED" if code == 0 else "FAILED",
        "returncode": code,
        "process_request": {
            "argv": [
                "python",
                "-m",
                "driver",
                json.dumps(
                    {
                        "raw_request": {
                            "argv": ["bwrap", "--ro-bind", sys.prefix, "/runtime"]
                        }
                    }
                ),
            ]
        },
    }
    issues = []
    result = oracle._audit_shared_component_result(
        None,
        {"run_id": "run"},
        spec,
        {"fact_id": "outcome"},
        payload,
        {"attempt_id": "process"},
        tmp_path,
        candidate,
        [{"command_id": "submit"}],
        [],
        {"process": process},
        issues,
        tmp_path,
    )
    if case == "goal-only-invalid-refutation":
        assert any(issue.code == "shared_local_measurement_mismatch" for issue in issues)
        assert result.outcome == "EXECUTION_FAILURE"
        assert result.criterion_met is None
    elif case in {"changed-vector", "invented-diagnostic", "missing-diagnostic"}:
        assert any(
            issue.code == "accept_outcome_common_diagnostic_mismatch"
            for issue in issues
        )
    else:
        assert issues == []
        assert result.goal_met is (None if rejected or code == 72 else not goal_only)
        assert result.outcome == actual
        if goal_only:
            assert result.goal_valid is not invalid
