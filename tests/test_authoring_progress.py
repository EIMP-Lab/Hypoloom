from dataclasses import replace
import json
import socket
import threading
from types import SimpleNamespace

import pytest

from hypoloom import native_output
from hypoloom.agent_aci import AgentACIError, DEFAULT_REAL_AGENT_BUDGET
from hypoloom.agent_budget import authoring_wall_budget, validate_stage_wall_seconds
from hypoloom.agent_context import agent_retry_class
from hypoloom.claude_agent_host import ClaudeAgentHost, AgentHostTerminalReason
from hypoloom.diagnostics import authoring_status, diagnostic_summary
from hypoloom.domain import AgentCommand
from hypoloom.evaluator_examples import case_format_example, examples
from hypoloom.fake_agent import default_request
from hypoloom.provider_observation import terminal_gateway_timeout
from hypoloom.shared_acceptance import SharedAcceptance


def test_time_budget_tracks_the_logical_stage_across_retries_and_the_run():
    document = {"budget": {}, "support_role_budgets": {"grounding-agent": {"timeout": 36000}}}
    base = replace(DEFAULT_REAL_AGENT_BUDGET, wall_seconds=3000)

    def budget(stage="GROUNDING", elapsed=0, remaining=None):
        return authoring_wall_budget(document, stage, base, started_at=100, now=100 + elapsed,
                                     run_remaining=remaining)

    assert budget().soft_wall_seconds == 3000
    assert budget().wall_seconds == 36000
    assert budget().wall_source == "budget.roles.grounding-agent.timeout"
    assert budget("RESULT_ANALYSIS") == base
    document["budget"]["stage_wall_seconds"] = {"GROUNDING": 5000}
    assert budget(elapsed=1500).wall_seconds == 3500
    assert budget(elapsed=1500).wall_source == "budget.stage_wall_seconds.GROUNDING"
    assert budget(elapsed=4500).soft_wall_seconds == 500
    assert budget(elapsed=4500, remaining=100).wall_source == "budget.wallclock_hours"
    assert budget(elapsed=5000) is None
    assert budget(remaining=0) is None
    paper = authoring_wall_budget(document, "PAPER_READ", base, started_at=100, now=100,
                                  run_remaining=2000, parent_remaining=120)
    assert (paper.wall_seconds, paper.wall_source) == (120, "parent_request.deadline")
    request = default_request(AgentCommand.HYPOTHESIS_PROPOSAL)
    assert "soft_wall_seconds" not in request.to_dict()["budget"]
    with pytest.raises(AgentACIError, match="ACI 44"):
        replace(request, budget=budget())


@pytest.mark.parametrize("limits", [None, {"GROUNDING": True}, {"GROUNDING": 0}, {"MISSPELLED": 90}])
def test_unusable_stage_limits_are_rejected(limits):
    with pytest.raises(ValueError, match="stage_wall_seconds"):
        validate_stage_wall_seconds({"stage_wall_seconds": limits})


def test_documented_case_shape_uses_the_production_validator():
    contract = SharedAcceptance.from_dict({"summary": "positive score", "rule": "ALL",
        "criteria": [{"name": "score", "operator": "GT", "target": "0"}], "validity_checks": [],
        "parser_argv": ["python", "score.py"], "parser_files": ["score.py"], "raw_artifacts": ["score.json"]})
    cases = case_format_example()
    assert [passed for _, passed in examples(json.dumps(cases), contract).values()] == [False, True]
    wrapped = {"baseline": dict(cases["baseline"], name="baseline"), "variants": [cases["higher"]]}
    with pytest.raises(ValueError, match=r"acceptance-cases.json/baseline:.*top-level"):
        examples(json.dumps(wrapped), contract)
    cases["higher"]["goal_met"] = 1
    with pytest.raises(ValueError, match=r"higher/goal_met: expected a JSON boolean"):
        examples(json.dumps(cases), contract)


def test_preview_extends_only_for_new_verified_progress_and_stop_rechecks(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(native_output, "time", SimpleNamespace(monotonic=lambda: now[0], time=lambda: now[0]))
    def check(result):
        if not result.get("ready"):
            raise ValueError("/ready: candidate still incomplete")
    server = native_output.NativeOutputValidator(tmp_path / "check.sock", session_id="author",
        output_schema={"type": "object"}, max_failed_validations=2, max_request_bytes=8192,
        deadline_at=125, invocation_wall_seconds=25, soft_wall_seconds=10, authoring_progress=True,
        draft_check=True, candidate_check=check)
    def preview(text):
        return server._dispatch({"session_id": "author", "operation": "check_draft", "text": text})
    try:
        now[0] = 107
        assert "/ready" in preview("{}")["error"]
        first = server.snapshot()["authoring"]
        assert first["progress_due_at"] == 117 and first["last_verified"]["check"] == "output_schema"
        now[0] = 113
        assert "failed again" in preview("{}")["error"]
        assert server.snapshot()["authoring"] == {**first, "repeated_errors": 1}
        assert not server.output_path.exists() and server.snapshot()["call_count"] == 0
        now[0] = 116
        assert preview('{"ready":true}') == {"valid": True}
        assert server.snapshot()["authoring"]["progress_due_at"] == 125
        assert not server.snapshot()["accepted"] and not server.output_path.exists()
        stop = {"session_id": "author", "last_assistant_message": "{}"}
        assert server._dispatch(stop)["decision"] == "block"
        stop["last_assistant_message"] = '{"ready":true}'
        assert server._dispatch(stop) == {}
        assert server.accepted_text(final_message=stop["last_assistant_message"]) == stop["last_assistant_message"]
        assert server.snapshot()["rejected_count"] == 1
        now[0] = 126
        assert server.soft_deadline_expired()
        with pytest.raises(ValueError, match="deadline"):
            preview('{"ready":true}')
    finally:
        server.close()


def test_confirmed_gateway_failure_keeps_draft_feedback_separate():
    relay = {"request_path": "/v1/messages", "relay_status": 504, "response_body_complete": False}
    assert terminal_gateway_timeout([relay], "Request timed out.")
    assert not terminal_gateway_timeout([relay], "exit 1")
    assert not terminal_gateway_timeout([relay, {"request_path": "/v1/messages", "upstream_status": 200}],
                                        "Request timed out.")
    correction = {"status": "unresolved", "rejected_count": 2,
                  "authoring": {"last_verified": {"check": "output_schema", "at": 100},
                                "validation_error": "/baseline: extra name field"}}
    diagnostic = {"provider_subtype": "success", "structured_output_correction": correction,
                  "provider_diagnostic": "Request timed out.", "provider_failure": {
                      "code": "transport_error", "source": "cli_gateway_timeout", "http_status": 504}}
    observation = {"failure_code": "nonzero_exit", "diagnostics": diagnostic}
    assert agent_retry_class("nonzero_exit", observation, retryable=True, bounded_context=True) == "INFRASTRUCTURE"
    assert diagnostic_summary(observation) == "Request timed out."
    assert authoring_status(diagnostic) == correction["authoring"]
    del diagnostic["provider_failure"]
    diagnostic["wall_stop"] = "no_verified_progress"
    assert agent_retry_class("timed_out", observation, retryable=True, bounded_context=True) == "MODEL"


def test_disconnected_preview_leaves_final_submission_available(tmp_path):
    import time
    entered = threading.Event()
    resume = threading.Event()
    def check(_):
        entered.set()
        assert resume.wait(5)
    server = native_output.NativeOutputValidator(tmp_path / "check.sock", session_id="author",
        output_schema={"type": "object"}, max_failed_validations=2, max_request_bytes=4096,
        deadline_at=time.monotonic() + 10, authoring_progress=True, draft_check=True, candidate_check=check)
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(str(server.socket_path))
            client.sendall(b'{"operation":"check_draft","session_id":"author","text":"{}"}\n')
            assert entered.wait(3)
        resume.set()
        result = native_output.call_validator(str(server.socket_path), session_id="author",
            last_assistant_message="{}", max_request_bytes=4096, timeout_seconds=5)
        assert result == {} and server.accepted_text() == "{}"
        assert server.snapshot()["call_count"] == 1
    finally:
        resume.set()
        server.close()


def test_debug_trace_retains_verified_progress_and_only_admitted_fields():
    from hypoloom.debug_mode import _structured_output_correction
    trace = {"scope": "native_stop_validation", "validation_source": "host_stop_hook",
             "status": "prepared", "accepted": False, "call_count": 1, "rejected_count": 0,
             "rounds": [{"ordinal": 1, "status": "prepared", "validation_error": None}],
             "authoring": {"last_verified": {"check": "complete_draft", "at": 100},
                           "validation_error": None, "repeated_errors": 0}}
    observed = {**trace, "authoring": {**trace["authoring"], "draft_text": "unpublished draft"}}
    assert _structured_output_correction(observed) == trace


@pytest.mark.parametrize("observer", ["poll", "wait"])
def test_process_supervisor_obeys_the_progress_window_and_cancellation(tmp_path, monkeypatch, observer):
    now = [100.0]
    monkeypatch.setattr(native_output, "time", SimpleNamespace(monotonic=lambda: now[0], time=lambda: now[0]))
    validator = native_output.NativeOutputValidator(tmp_path / "check.sock", session_id="author",
        output_schema={"type": "object"}, max_failed_validations=3, max_request_bytes=8192,
        deadline_at=130, invocation_wall_seconds=30, soft_wall_seconds=10,
        authoring_progress=True, draft_check=True)
    adapter = object.__new__(ClaudeAgentHost)
    adapter._clock = lambda: now[0]
    adapter._require_handle = lambda handle: handle
    reaped = []
    adapter._terminate_group = reaped.append
    adapter._observe = lambda handle, reason, **kwargs: reason
    handle = SimpleNamespace(_process=SimpleNamespace(poll=lambda: None), _observation=None,
        _output_validator=validator, _cancel_requested=False, _lock=threading.RLock(), started_at=100, deadline_at=130,
        request=SimpleNamespace(command=AgentCommand.GROUNDING, schema_version=44,
                                budget=SimpleNamespace(soft_wall_seconds=10)))
    try:
        now[0] = 108
        assert validator._dispatch({"operation": "check_draft", "session_id": "author", "text": "{}"})["valid"]
        now[0] = 111
        assert adapter.poll(handle) is None and not reaped
        if observer == "wait":
            assert adapter.wait(handle, timeout=10) == AgentHostTerminalReason.TIMED_OUT
            assert handle._wall_stop == "caller_timeout"
        handle._cancel_requested = True
        assert getattr(adapter, observer)(handle) == AgentHostTerminalReason.CANCELLED
        handle._cancel_requested = False
        now[0] = 119
        assert getattr(adapter, observer)(handle) == AgentHostTerminalReason.TIMED_OUT
        assert handle._wall_stop == "no_verified_progress" and reaped[-1] is handle
    finally:
        validator.close()


def test_first_experiment_counts_only_preregistered_launched_processes():
    from hypoloom.observer_summary import _first_experiment_start
    experiments = {"exp": {"execution_id": "formal"}}
    formal = {"kind": "PROCESS", "experiment_id": "exp", "execution_id": "formal",
              "process_group_id": 32, "started_at": 42}
    others = [dict(formal, execution_id="probe", started_at=7),
              dict(formal, process_group_id=None, started_at=12), dict(formal, kind="AGENT", started_at=15)]
    assert _first_experiment_start(others, experiments) is None
    assert _first_experiment_start([*others, formal], experiments) == 42
