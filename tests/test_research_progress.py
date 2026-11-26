import json
from types import SimpleNamespace

from hypoloom.artifacts import ArtifactKey
from hypoloom.observer_progress import research_progress, result_producers, stage_coverage


def test_formal_failures_and_comparable_evidence_use_only_the_canonical_process():
    attempts, facts = [], []
    measurement = {"evidence": {"stage": "CONFIRMATION", "units": ["fold"], "replicates": ["a", "b"]}}
    for number, (verdict, outcome) in enumerate((("changes_required", "EXECUTION_FAILURE"),
            ("inconclusive", "EXECUTION_FAILURE"), (None, "EXECUTION_FAILURE"), (None, "CLEAN_REFUTATION"))):
        experiment = f"e{number}"
        spec = {"component_id": "primary", "measurement": measurement, "goal_evaluation": {"scope": "GOAL"}}
        facts.append({"fact_type": "ExperimentPreregistered", "experiment_id": experiment, "payload": spec})
        key = ArtifactKey(run_id="run", experiment_id=experiment, execution_id=experiment,
            attempt_id=f"{experiment}-formal", artifact_kind="result")
        observation = ({"synthetic_before_spawn": True, "failure_class": "implementation_not_verified",
            "implementation_verification": {"report": {"verdict": verdict}}} if verdict else {"returncode": 70 if number == 2 else 0})
        formal = {"kind": "PROCESS", "run_id": "run", "experiment_id": experiment, "execution_id": experiment,
            "attempt_id": key.attempt_id, "observation_json": json.dumps(observation)}
        attempts.extend((formal, {**formal, "execution_id": experiment + "-probe", "attempt_id": experiment + "-probe",
            "observation_json": '{"returncode": 0}'}))
        facts.append({"fact_type": "CanonicalOutcomeAccepted", "experiment_id": experiment, "created_at": str(number),
            "payload": {"component_id": "primary", "outcome_id": f"o{number}", "artifact_id": key.artifact_id,
                "outcome": outcome, "metric_value": "0" if number == 3 else None}})
        progress = research_progress(list(reversed(facts)), result_producers("run", attempts), {"primary": "author"})["primary"]
        if number == 0:
            assert progress["last_failure"]["owner"] == "author"
        if number == 1:
            assert progress["last_failure"]["owner"] == "verifier"
    assert progress["failure_counts"] == {"GOAL": {"implementation_verification": 2, "formal_execution": 1}}
    assert progress["last_failure"]["experiment_id"] == "e2" and progress["last_failure"]["owner"] == "author"
    assert progress["last_evidence"]["experiment_id"] == "e3"
    assert progress["last_evidence"]["coverage"] == {**measurement["evidence"], "validated": True}
    assert not stage_coverage(measurement, None)["validated"]
    assert stage_coverage({"evidence": {"stage": "SCREENING", "units": ["fold"]}}, {})["replicates"] is None


def test_synthesis_receives_only_its_unresolved_parser_failures(tmp_path):
    from hypoloom.expert_batch import prior_parser_failures
    from hypoloom.workflow_host import WorkflowHost
    host = WorkflowHost(tmp_path / "state")
    host.store.initialize()
    try:
        with host.store.transaction() as tx:
            tx.create_run("run", host.version)
            for i, (cycle, role, stage, recovered) in enumerate(((1, "author", "parser_smoke", False),
                    (2, "author", "parser_smoke", False), (2, "author", "parser_smoke", True),
                    (2, "peer", "parser_smoke", False), (2, "author", "output_schema", False))):
                command = f"design-{i}"
                context = {"key": {"cycle": cycle, "chain": command, "expert_slot": role,
                    "node_kind": "EXPERIMENT_DESIGN_IMPLEMENT", "component_id": "primary"}}
                tx.record_command({"run_id": "run", "workflow_id": "run", "command_id": command,
                    "command_type": "REQUEST_RESEARCH_NODE", "request": {"task_context": context}})
                for ordinal in range(1, 3 if recovered else 2):
                    attempt = f"{command}-{ordinal}"
                    tx.open_attempt({"attempt_id": attempt, "run_id": "run", "workflow_id": "run", "command_id": command,
                        "kind": "AGENT", "host_generation": "test", "attempt_ordinal": ordinal, "started_at": float(i + ordinal),
                        "observation": {"request": {}}})
                    tx.close_attempt(attempt, terminal_reason="SUCCEEDED" if ordinal == 2 else "FAILED",
                        ended_at=float(i + ordinal + 1), payload={"diagnostics": {"structured_output_correction":
                            {"rounds": [{"validation_stage": stage, "validation_error": "script unavailable"}]}}})
        history = SimpleNamespace(hypothesis=lambda cycle, chain: {"payload": {"measurement": {
            "parser_argv": ["python", "bin/score.py"], "parser_files": ["cfg.json"]}}})
        task = SimpleNamespace(key=SimpleNamespace(cycle=3, component_id="primary", expert_slot="author", run_id="run"))
        issue, = prior_parser_failures(host.store, task, history)
        assert issue["cycles"] == [2, 1] and issue["attempts"] == 2
        assert issue["repair_node"] == "HYPOTHESIS_PROPOSAL"
        assert "bin/score.py" in issue["detail"]
    finally:
        host.close()
