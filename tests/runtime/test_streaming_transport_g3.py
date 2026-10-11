"""Real CC/Host/CPU closure with GLM-shaped, fragmented thinking streams."""
import json
import secrets
import tempfile
import time
from pathlib import Path

import pytest

from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.oracle import Oracle
import test_claude_file_tool_cli as cli_fixture
from test_claude_measurement_g3 import _DeclaredScenario, _NoPublicWeb, _six_yaml_profile_v4_payload
from test_full_cc_host_g3 import _new_host, _start_provider


@pytest.mark.parametrize("limited", [False, True], ids=["complete", "local-cap"])
def test_native_fragmented_thinking_transport(tmp_path, monkeypatch, limited):
    executable = cli_fixture._installed_claude()
    scenario = _DeclaredScenario(tmp_path, "streaming-transport")
    send = cli_fixture._send_event
    fragments = 17000
    injected = []

    def thinking_event(handler, event, data):
        if event == "message_start":
            request = scenario.current
            handler.thinking_transport = (request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT
                and scenario.steps[request.attempt_id] in {1, 2})
            send(handler, event, data)
            if handler.thinking_transport:
                injected.append(request.attempt_id)
                send(handler, "content_block_start", {"type": "content_block_start", "index": 0,
                    "content_block": {"type": "thinking", "thinking": ""}})
                frame = (b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
                    b'"delta":{"type":"thinking_delta","thinking":"x "}}\n\n')
                # Frame boundaries are deliberately unrelated to network chunks.
                wire = frame * fragments
                for offset in range(0, len(wire), 65521):
                    try:
                        handler.wfile.write(wire[offset:offset + 65521])
                        handler.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        if limited:
                            return
                        raise
                send(handler, "content_block_delta", {"type": "content_block_delta", "index": 0,
                    "delta": {"type": "signature_delta", "signature": "scripted-provider-signature"}})
                send(handler, "content_block_stop", {"type": "content_block_stop", "index": 0})
            return
        if getattr(handler, "thinking_transport", False):
            if "index" in data:
                data = {**data, "index": data["index"] + 1}
            if event == "message_delta":
                data = {**data, "usage": {"output_tokens": fragments // 2 + data["usage"]["output_tokens"]}}
        try:
            send(handler, event, data)
        except (BrokenPipeError, ConnectionResetError):
            if not limited:
                raise

    monkeypatch.setattr(cli_fixture, "_send_event", thinking_event)
    if limited:
        # Exercise the same production boundary at its former 4 MiB value.
        from hypoloom.provider_relay import ProviderRelay
        initialize = ProviderRelay.__init__

        def bounded_relay(self, *args, **kwargs):
            assert kwargs["max_response_bytes"] == 64 * 1024 * 1024
            kwargs["max_response_bytes"] = 4 * 1024 * 1024
            initialize(self, *args, **kwargs)

        monkeypatch.setattr(ProviderRelay, "__init__", bounded_relay)
    key = "transport-test-" + secrets.token_hex(12)
    provider, thread = _start_provider(scenario, key)
    (tmp_path / "settings.json").write_text("{}\n")
    (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(tmp_path, executable, f"http://127.0.0.1:{provider.server_port}", key)
    host.grounding_web = _NoPublicWeb()
    run = "transport-test"
    try:
        with tempfile.TemporaryDirectory(prefix="hypoloom-stream-", dir="/var/tmp") as mount:
            payload = _six_yaml_profile_v4_payload(tmp_path, Path(mount), run)
            host.initialize()
            assert host.handle(ControlRequest(1, "create", 0, "CREATE_RUN", payload)).status == "accepted"
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                host.tick()
                assert not scenario.errors, scenario.errors
                if host.store.latest_transition(run)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
                time.sleep(.01)
            attempts = host.store.list_attempts()
            agents = [json.loads(a["observation_json"]) for a in attempts if a["kind"] == "AGENT"]
            designs = [a for a in agents if a["request"]["command"] == "EXPERIMENT_DESIGN_IMPLEMENT"]
            assert len(designs) == 1, designs
            design = designs[0]
            assert len(injected) == 2
            transport = design["diagnostics"]["provider_response_transport"]
            assert transport["received_bytes"] > 4 * 1024 * 1024
            processes = [a for a in attempts if a["kind"] == "PROCESS"]
            outcomes = host.store.list_facts(run, "CanonicalOutcomeAccepted")
            if limited:
                assert design["failure_code"] == "provider_transport_limit"
                assert design["retry_class"] is None
                assert transport["limit_reached"]
                assert len(processes) == len(outcomes) == 0
                assert scenario.steps[design["request"]["attempt_id"]] == 2
                assert "provider_failure" not in design["diagnostics"]
            else:
                assert host.store.latest_transition(run)["state"] == "COMPLETED"
                assert design["response"]["status"] == "SUCCEEDED"
                stdout = design["diagnostics"]["stdout_transport"]
                assert stdout["raw_bytes"] > 4 * 1024 * 1024
                assert stdout["progress_events"] == fragments * 2
                assert stdout["content_bytes"] < 4 * 1024 * 1024
                assert not transport["limit_reached"] and transport["limit_bytes"] == 64 * 1024 * 1024
                assert design["diagnostics"]["structured_output_correction"]["accepted"]
                assert "provider_accounting" in design["diagnostics"]
                assert len(processes) == 1 and processes[0]["terminal_reason"] == "SUCCEEDED"
                assert len(outcomes) == 1 and outcomes[0]["payload"]["metric_value"] == "-0.1"
            assert all(a["state"] == "CLOSED" for a in attempts)
            assert all(not process_group_exists(a["process_group_id"]) for a in attempts if a["process_group_id"])
            audit = Oracle(host.store.database_path, host.publisher.root).audit(run)
            assert audit.ok and audit.open_attempt_count == audit.pending_effect_count == 0, audit.to_dict()
            (tmp_path / "census.json").write_text(json.dumps({"state": host.store.latest_transition(run)["state"],
                "agents": len(agents), "processes": len(processes), "outcomes": len(outcomes),
                "design_transport": transport, "stdout_transport": design["diagnostics"]["stdout_transport"],
                "oracle": audit.to_dict()}, indent=2))
    finally:
        host.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
