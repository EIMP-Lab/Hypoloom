"""Independent raw-row audit of narrative causation and published report bytes."""
from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path
from .batch_oracle import shared_goal_stop, supports_successful_stop


def _time(value):
    return float(value) if isinstance(value, (int, float)) else datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def audit_final_reports(state_root, run_id, attempts, observations, facts, payloads, transitions, *, workflow_version=None):
    issues, accepted = [], []
    actual = {str(row["fact_id"]): payloads[str(row["fact_id"])] for row in facts}
    fact_rows = {str(row["fact_id"]): dict(row) for row in facts}
    completion = [dict(row) for row in transitions if row["workflow_kind"] == "RUN"
        and row["workflow_id"] == run_id and row["state"] in {"COMPLETING", "COMPLETED"}]
    for raw in attempts:
        row = dict(raw); identity = str(row["attempt_id"])
        observed = observations.get(identity, {}); request = observed.get("request", {})
        if request.get("command") != "FINAL_REPORT": continue
        if row["state"] != "CLOSED" or row["terminal_reason"] != "SUCCEEDED": continue
        accepted.append(identity)
        try:
            assert type(request["schema_version"]) is int and request["schema_version"] >= 14
            assert request["attempt_id"] == identity and request["run_id"] == run_id
            assert request["role"] == request["component_id"] == "grounding"
            task = request["task_context"]
            assert task["kind"] == "AUXILIARY" and task["request_kind"] == "FINAL_REPORT"
            assert task["component_id"] is task["expert_slot"] is task["cycle"] is None
            inputs = [item for item in request["input_artifacts"] if item["ref"].endswith("--final-report-context")]
            assert len(inputs) == 1
            context = json.loads(base64.b64decode(inputs[0]["content_base64"], validate=True))
            assert isinstance(context["stop_reason"], str) and context["stop_reason"].strip()
            sources = {}
            for source in context["facts"]:
                value = json.loads(source["payload_json"]) if "payload_json" in source else source.get("payload", source)
                assert all(source.get(key) == value.get(key) for key in ("fact_id", "fact_type", "run_id"))
                assert source["run_id"] == run_id and source["fact_id"] not in sources
                sources[source["fact_id"]] = value
                if "created_at" in fact_rows[source["fact_id"]] and "started_at" in row:
                    assert _time(fact_rows[source["fact_id"]]["created_at"]) <= _time(row["started_at"])
            assert sources == actual
            response = observed["response"]
            files = response["proposed_workspace_artifacts"]
            assert len(files) == 1 and files[0]["path"] == "FINAL_REPORT.md"
            content = base64.b64decode(files[0]["content_base64"], validate=True)
            assert 0 < len(content) <= request["budget"]["max_output_bytes"]
            report = content.decode("utf-8")
            from .final_report import process_consistency_errors
            inconsistencies = process_consistency_errors(context.get("run_outcome", {}), report)
            summary = response.get("typed_candidate", {}).get("summary")
            if isinstance(summary, str):
                inconsistencies.extend(process_consistency_errors(context.get("run_outcome", {}), summary))
            if inconsistencies:
                raise ValueError("final report disagrees with actual Process evidence: " + inconsistencies[0])
            failed_sources = {}
            attempt_rows = {str(item["attempt_id"]): dict(item) for item in attempts}
            for raw_transition in transitions:
                transition = dict(raw_transition)
                if transition.get("command_type") != "AGENT_ATTEMPT_FINAL_FAILURE": continue
                control = json.loads(transition["payload_json"])
                failed_id = control.get("attempt_id")
                failed_request = observations.get(failed_id, {}).get("request", {})
                if failed_request.get("command") != "CYCLE_REPORT": continue
                first_started = min(_time(item["started_at"]) for item in attempts
                    if item["command_id"] == row["command_id"])
                if _time(transition["created_at"]) > first_started: continue
                failed = attempt_rows[failed_id]
                assert control.get("agent_final_failure") is True
                assert failed["run_id"] == run_id and failed["state"] == "CLOSED" and failed["terminal_reason"] != "SUCCEEDED"
                assert control["failed_task_command_id"] == failed["command_id"] == failed_request["command_id"]
                assert _time(failed["ended_at"]) <= _time(transition["created_at"])
                assert failed_id + "--final-failure" not in failed_sources
                failed_sources[failed_id + "--final-failure"] = {"attempt": failed, "control": control}
                assert failed_id in report and failed_id in context["stop_reason"]
            supplied_failures = {item["ref"]: json.loads(base64.b64decode(item["content_base64"], validate=True))
                for item in request["input_artifacts"] if item["ref"].endswith("--final-failure")}
            assert supplied_failures == failed_sources
            verdicts = [value for value in actual.values() if value["fact_type"] == "GoalVerdictRecorded"]
            assert len(verdicts) <= 1
            if verdicts:
                assert verdicts[0]["fact_id"] in report and verdicts[0]["verdict"] in report
            else: assert "NO_ACCEPTED_VERDICT" in report
            assert all(value["outcome_id"] in report for value in actual.values() if value["fact_type"] == "CanonicalOutcomeAccepted")
            if observed.get("session") is not None:
                relative = f"{run_id}/agent/{identity}/transcript"
                assert observed["session"]["transcript"]["path"] == relative
                directory = Path(state_root) / "artifacts" / relative
                path = directory / "FINAL_REPORT.md"
                assert path.resolve(strict=True) == path and not path.is_symlink()
                assert path.read_bytes() == content
            for transition in completion:
                if "created_at" in transition and "ended_at" in row:
                    assert _time(row["ended_at"]) <= _time(transition["created_at"])
        except (AssertionError, KeyError, TypeError, ValueError, OSError) as exc:
            issues.append(("final_report_source_or_publication_invalid", "Final report disagrees with accepted sources, scope or published bytes: " + str(exc), (identity,)))
    if completion and len(accepted) != 1:
        issues.append(("final_report_completion_count", "A completed ACI14 research run requires exactly one accepted final report", tuple(accepted) or (run_id,)))
    try:
        success_stop = shared_goal_stop(facts, payloads, transitions, workflow_version)
    except (KeyError, TypeError, ValueError) as exc:
        issues.append(("final_report_stop_closure_invalid", str(exc), (run_id,)))
        success_stop = None
    issues.extend(_audit_stop_reports(run_id, attempts, observations, transitions, accepted,
                                     success_stop=success_stop, workflow_version=workflow_version))
    return issues


def _audit_stop_reports(run_id, attempts, observations, transitions, accepted, *, success_stop=None, workflow_version=None):
    """Join ordinary stop commands to the first report request, not a live reducer."""
    issues = []
    rows = {str(row["attempt_id"]): dict(row) for row in attempts}
    reports = [row for row in rows.values() if observations.get(row["attempt_id"], {}).get("request", {}).get("command") == "FINAL_REPORT"]
    reports.sort(key=lambda row: (_time(row.get("started_at", 0)), str(row["attempt_id"])))
    run_rows = [dict(row) for row in transitions if row["workflow_kind"] == "RUN" and row["workflow_id"] == run_id]
    controls = [row for row in run_rows if row.get("command_type") in {"CANCEL", "FAIL_INTEGRITY", "RESOLVE_CANCEL", "FINAL_REPORT_FAILED"}]
    if success_stop is not None:
        controls.append(success_stop[0])
        controls.sort(key=lambda row: row["revision"])
    failure_rows = [row for row in controls if row["command_type"] == "FINAL_REPORT_FAILED"]
    stops = [row for row in run_rows if row.get("command_type") == "FINISH_STOP"]
    try:
        if supports_successful_stop(workflow_version):
            for transition in controls:
                if transition["command_type"] != "CANCEL": continue
                earlier = [row for row in controls if row["revision"] < transition["revision"]]
                failed = any(row["command_type"] in {"FAIL_INTEGRITY", "FINAL_REPORT_FAILED"}
                    or json.loads(row["payload_json"]).get("stop_disposition") == "FAILED" for row in earlier)
                successful = success_stop is not None and success_stop[0]["revision"] < transition["revision"]
                expected = "FAILED" if failed else "COMPLETED" if successful else "CANCELLED"
                assert json.loads(transition["payload_json"]).get("stop_disposition") == expected
        if reports:
            first = reports[0]; request = observations[first["attempt_id"]]["request"]
            immutable = {item["ref"]: item for item in request["input_artifacts"] if item["ref"] in request["immutable_context_refs"]}
            for row in reports:
                current = observations[row["attempt_id"]]["request"]
                assert current["command_id"] == request["command_id"]
                assert {item["ref"]: item for item in current["input_artifacts"] if item["ref"] in current["immutable_context_refs"]} == immutable
            context = json.loads(base64.b64decode(next(item for item in immutable.values() if item["ref"].endswith("--final-report-context"))["content_base64"], validate=True))
            before = [row for row in controls if "created_at" in row and "started_at" in first and _time(row["created_at"]) <= _time(first["started_at"])]
            source_controls = context.get("stop_controls", [])
            if before:
                expected = [{"command_id": row["command_id"], "revision": row["revision"], "command_type": row["command_type"], "payload": json.loads(row["payload_json"])} for row in before]
                assert source_controls == expected
                assert all(row["command_id"] in context["stop_reason"] for row in expected)
                # At the first stop-report request every pre-existing effect must
                # already be closed. Later report retries are not foreign work.
                for row in rows.values():
                    if row in reports or row.get("run_id", run_id) != run_id or "started_at" not in row: continue
                    if _time(row["started_at"]) <= _time(first["started_at"]):
                        assert row["state"] == "CLOSED" and row.get("ended_at") is not None
                        assert _time(row["ended_at"]) <= _time(first["started_at"])
            else:
                assert not source_controls
                # A report frozen before cancellation is an as-of record and is
                # reused, never rebound to the later control or opened twice.
                source_facts = [json.loads(row["payload_json"]) if "payload_json" in row else row.get("payload", row) for row in context["facts"]]
                assert any(row["fact_type"] == "GoalVerdictRecorded" for row in source_facts)
        for row in run_rows:
            if row.get("previous_state") != "STOPPING" or row.get("command_type") != "REQUEST_AUXILIARY": continue
            owned = [attempt for attempt in rows.values() if attempt.get("command_id") == row["command_id"]]
            assert owned and all(observations.get(attempt["attempt_id"], {}).get("request", {}).get("command") == "FINAL_REPORT" for attempt in owned)
        if stops or failure_rows:
            assert len(failure_rows) <= 1
            for transition in failure_rows:
                value = json.loads(transition["payload_json"])
                assert value.get("report_missing") is True and not accepted
                source = rows[value["source_attempt_id"]]
                assert source in reports and source["state"] == "CLOSED" and source["terminal_reason"] != "SUCCEEDED"
                failure = value["final_failure"]
                assert failure["attempt_id"] == source["attempt_id"] and failure["failed_task_command_id"] == source["command_id"]
                originals = [json.loads(row["payload_json"]) for row in run_rows if row.get("command_type") == "AGENT_ATTEMPT_FINAL_FAILURE"]
                assert failure in originals
                if "created_at" in transition and "ended_at" in source: assert _time(source["ended_at"]) <= _time(transition["created_at"])
            assert len(accepted) == 1 if not failure_rows else len(accepted) == 0
            for transition in stops:
                earlier = [row for row in controls if row["revision"] < transition["revision"]]
                failed = any(row["command_type"] in {"FAIL_INTEGRITY", "FINAL_REPORT_FAILED"} or json.loads(row["payload_json"]).get("stop_disposition") == "FAILED" for row in earlier)
                successful = success_stop is not None and success_stop[0]["revision"] < transition["revision"]
                expected = "FAILED" if failed else "COMPLETED" if successful else "CANCELLED"
                assert transition["state"] == expected
                assert json.loads(transition["payload_json"])["stop_disposition"] == transition["state"]
                if "created_at" in transition:
                    assert all(_time(rows[identity]["ended_at"]) <= _time(transition["created_at"]) for identity in accepted)
        if success_stop is not None:
            stopped_at = _time(success_stop[0]["created_at"])
            for row in rows.values():
                if _time(row["started_at"]) <= stopped_at: continue
                request = observations.get(row["attempt_id"], {}).get("request", {})
                assert request.get("command") == "FINAL_REPORT" or row["kind"] == "PROCESS" and observations.get(row["attempt_id"], {}).get("synthetic_before_spawn") is True
        elif supports_successful_stop(workflow_version):
            assert not any(row.get("command_type") == "FINISH_STOP" and row.get("state") == "COMPLETED" for row in run_rows)
    except (AssertionError, KeyError, TypeError, ValueError, OSError, StopIteration) as exc:
        issues.append(("final_report_stop_closure_invalid", "Stop reporting disagrees with frozen source, drained effects, actual failure or disposition: " + str(exc), (run_id,)))
    return issues
