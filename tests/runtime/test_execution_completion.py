"""Native completion uses the same durable experiment boundary as the Host."""

from concurrent.futures import ThreadPoolExecutor
import time

import pytest

from hypoloom.agent_aci import AgentACIError, WorkspaceArtifact
from hypoloom.domain import AttemptTerminalReason
from hypoloom.fake_agent import FakeAgent
from hypoloom.host_tools_server import AgentToolServer
from hypoloom.native_output import NativeOutputValidator, call_validator

from test_host_experiment_tools import execution as _execution_fixture, _until
from test_native_output import SCHEMA, VALID

execution = _execution_fixture


def _validate(server, tools):
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(call_validator, str(server.socket_path),
            session_id="execution-session", last_assistant_message=VALID,
            max_request_bytes=4096, timeout_seconds=5)
        deadline = time.monotonic() + 5
        while not future.done() and time.monotonic() < deadline:
            tools.service()
            time.sleep(0.005)
        return future.result(timeout=0.5)


def test_native_stop_rejects_unfinished_work_then_accepts_terminal_experiment(execution, tmp_path):
    host, request, arguments = execution
    tools = AgentToolServer(tmp_path / "tools.sock", request.attempt_id, host.handle_agent_tool)
    server = NativeOutputValidator(tmp_path / "output.sock", output_schema=SCHEMA,
        session_id="execution-session", max_failed_validations=4,
        max_request_bytes=4096, deadline_at=time.monotonic() + 30,
        execution_tool_socket=tools.socket_path)
    try:
        # No submission, accepted queued submission, and running Process all
        # failed only after native Stop had already accepted in the incident.
        for phase in ("absent", "queued", "executing"):
            if phase == "queued":
                submitted = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
            if phase == "executing":
                _until(host, lambda: bool(host.running))
            with pytest.raises(AgentACIError, match="terminal submitted experiment"):
                host._accept_expert_packet(request, FakeAgent().invoke(request))
            reply = _validate(server, tools)
            assert reply.get("decision") == "block", (phase, reply)
            assert "terminal submitted experiment" in reply["reason"]
            assert not server.snapshot()["accepted"]
            assert host.store.get_attempt(request.attempt_id)["state"] == "OPEN"
        _until(host, lambda: host.store.latest_transition(submitted["exp_id"])["state"] == "TERMINAL")
        assert _validate(server, tools) == {}
        assert server.snapshot()["accepted"]
        assert server.snapshot()["rejected_count"] == 3
        host._finish_fake_agent(request, FakeAgent().invoke(request))
        assert host.store.get_attempt(request.attempt_id)["terminal_reason"] == "SUCCEEDED"
    finally:
        server.close()
        tools.close()


@pytest.mark.parametrize("cap", [1, 3])
def test_latest_replacement_and_dispatch_pause_do_not_look_completed(execution, tmp_path, cap):
    host, request, arguments = execution
    failing = [WorkspaceArtifact.from_bytes(item["path"], b"raise RuntimeError('repair-me')\n# result.json\n"
        if item["path"] == "experiment.py" else WorkspaceArtifact.from_dict(item).content_bytes()).to_dict()
        for item in arguments["workspace_files"]]
    first = host.handle_agent_tool(request.attempt_id, "submit_experiment", {**arguments, "workspace_files": failing})
    _until(host, lambda: host.store.latest_transition(first["exp_id"])["state"] == "TERMINAL"
           and bool(host.store.list_facts(request.run_id, "EvidenceLinked")))
    second = host.handle_agent_tool(request.attempt_id, "submit_experiment",
        {**arguments, "replaces_experiment_id": first["exp_id"]})
    host.dispatch_errors[request.run_id] = {"code": "test_dispatch_pause"}
    tools = AgentToolServer(tmp_path / "tools.sock", request.attempt_id, host.handle_agent_tool)
    server = NativeOutputValidator(tmp_path / "output.sock", output_schema=SCHEMA,
        session_id="execution-session", max_failed_validations=cap,
        max_request_bytes=4096, deadline_at=time.monotonic() + 10,
        execution_tool_socket=tools.socket_path)
    try:
        result = host.handle_agent_tool(request.attempt_id, "list_my_experiments", {})
        assert result["experiments"][0]["terminal"]
        assert result["experiments"][1]["exp_id"] == second["exp_id"]
        assert result["experiments"][1]["status"] == "blocked"
        reply = _validate(server, tools)
        assert reply == {} if cap == 1 else reply["decision"] == "block"
        assert server.snapshot()["accepted"] is False
        assert server.snapshot()["rejected_count"] == 1
        assert server.snapshot()["rounds"][0]["validation_stage"] == "execution_completion"
        with pytest.raises(AgentACIError, match="terminal submitted experiment"):
            host._accept_expert_packet(request, FakeAgent().invoke(request))
    finally:
        server.close()
        tools.close()


def test_closed_attempt_cannot_supply_a_native_completion_view(execution, tmp_path):
    host, request, _ = execution
    tools = AgentToolServer(tmp_path / "tools.sock", request.attempt_id, host.handle_agent_tool)
    server = NativeOutputValidator(tmp_path / "output.sock", output_schema=SCHEMA,
        session_id="execution-session", max_failed_validations=3,
        max_request_bytes=4096, deadline_at=time.monotonic() + 10,
        execution_tool_socket=tools.socket_path)
    try:
        host._agent_failure(request, AttemptTerminalReason.CANCELLED, "run_stopping", retryable=False)
        with pytest.raises(ValueError, match="state is unavailable"):
            _validate(server, tools)
        assert server.snapshot()["status"] == "validation_failed"
        assert server.snapshot()["accepted"] is False
        assert server.snapshot()["rejected_count"] == 0 and server.snapshot()["rounds"] == []
    finally:
        server.close()
        tools.close()


@pytest.mark.parametrize("termination", ["cancel", "deadline"])
def test_completion_read_ends_without_waiting_for_host_service(tmp_path, termination):
    tools = AgentToolServer(tmp_path / "tools.sock", "attempt-owned", lambda *_: {"experiments": []})
    server = NativeOutputValidator(tmp_path / "output.sock", output_schema=SCHEMA,
        session_id="execution-session", max_failed_validations=3,
        max_request_bytes=4096, deadline_at=time.monotonic() + (0.25 if termination == "deadline" else 30),
        execution_tool_socket=tools.socket_path)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(call_validator, str(server.socket_path),
                session_id="execution-session", last_assistant_message=VALID,
                max_request_bytes=4096, timeout_seconds=5)
            deadline = time.monotonic() + 3
            while tools._calls.empty() and time.monotonic() < deadline:
                time.sleep(0.005)
            assert not tools._calls.empty()
            started = time.monotonic()
            if termination == "cancel":
                server.close()
            with pytest.raises(ValueError):
                future.result(timeout=2)
            assert time.monotonic() - started < 2
        assert not server.snapshot()["accepted"]
    finally:
        server.close()
        tools.close()
