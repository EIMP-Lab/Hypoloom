"""Native CC/relay/MCP/CPU closure across a real local HTTP quota rejection."""
import json
import secrets
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.oracle import Oracle
from test_claude_measurement_g3 import _DeclaredScenario, _NoPublicWeb, _six_yaml_profile_v4_payload
from test_full_cc_host_g3 import _installed_claude, _new_host, _start_provider


def test_native_quota_wait_switches_endpoint_and_finishes_exactly_one_cpu_process(tmp_path):
    executable = _installed_claude()
    scenario = _DeclaredScenario(tmp_path, "provider-recovery-" + secrets.token_hex(12))
    new_key = "local-recovery-key-" + secrets.token_hex(16)
    healthy, healthy_thread = _start_provider(scenario, new_key)
    rejected = []
    class Exhausted(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            rejected.append(self.path)
            body = b'{"type":"error","error":{"type":"insufficient_quota","message":"Local fixture quota exhausted"}}'
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Retry-After", "0")
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args): pass
    exhausted = ThreadingHTTPServer(("127.0.0.1", 0), Exhausted)
    exhausted_thread = threading.Thread(target=exhausted.serve_forever, daemon=True)
    exhausted_thread.start()
    (tmp_path / "settings.json").write_text("{}")
    (tmp_path / "settings.json").chmod(0o600)
    selected = tmp_path / "connection.json"
    selected.write_text(json.dumps({"env": {"ANTHROPIC_API_KEY": new_key,
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{healthy.server_port}"}}))
    selected.chmod(0o600)
    host = None
    run_id = "native-provider-recovery"
    switched = None
    try:
        host = _new_host(tmp_path, executable, f"http://127.0.0.1:{exhausted.server_port}", "local-exhausted-key")
        host.grounding_web = _NoPublicWeb()
        with tempfile.TemporaryDirectory(prefix="hypoloom-provider-cpu-", dir="/var/tmp") as mount:
            payload = _six_yaml_profile_v4_payload(tmp_path, Path(mount), run_id)
            host.initialize()
            response = host.handle(ControlRequest(1, "create", 0, "CREATE_RUN", payload))
            assert response.status == "accepted", response
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                host.tick()
                status = host._provider_retry_status(run_id)
                if switched is None and status["gates"]:
                    assert status["reason"] in {"quota_exhausted", "rate_limited"}
                    assert not host._expert_final_failures(run_id)
                    switched = host.handle(ControlRequest(1, "switch-api", None, "PROVIDER_RETRY_CLEAR",
                        {"run_id": run_id, "reason": "operator", "settings_path": str(selected)}))
                    assert switched.status == "accepted", switched
                assert not scenario.errors, scenario.errors
                if host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}: break
                time.sleep(.01)
            assert rejected and switched is not None
            assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
            attempts = host.store.list_attempts()
            failed = [row for row in attempts if row["terminal_reason"] != "SUCCEEDED"]
            assert failed and all(row["kind"] == "AGENT" and json.loads(row["observation_json"])["diagnostics"]["provider_failure"]["code"] in {"quota_exhausted", "rate_limited"} for row in failed)
            processes = [row for row in attempts if row["kind"] == "PROCESS"]
            assert len(processes) == 1 and processes[0]["terminal_reason"] == "SUCCEEDED"
            assert len(host.store.list_facts(run_id, "CanonicalOutcomeAccepted")) == 1
            assert "cpu_outcome" in scenario.parser_evidence
            assert host._provider_retry_status(run_id)["state"] == "ready"
            assert all(row["state"] == "CLOSED" for row in attempts)
            assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"] is not None)
            assert not host.running and not host.running_agents
            report = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
            (tmp_path / "oracle.json").write_text(json.dumps(report.to_dict(), indent=2))
            assert report.ok, report.to_dict()
            assert report.open_attempt_count == report.pending_effect_count == 0
            (tmp_path / "provider-recovery-evidence.json").write_text(json.dumps({
                "failed_api_attempts": len(failed), "total_attempts": len(attempts), "processes": len(processes),
                "switch_revision": switched.revision, "oracle_ok": True, "process_residue": 0}, indent=2))
    finally:
        if host is not None: host.close()
        for server, thread in ((healthy, healthy_thread), (exhausted, exhausted_thread)):
            server.shutdown(); server.server_close(); thread.join(timeout=5)
