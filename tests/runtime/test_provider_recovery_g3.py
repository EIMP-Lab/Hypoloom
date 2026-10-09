"""Native CC/relay/MCP/CPU closure across a real local HTTP quota rejection."""
import json
import secrets
import tempfile
import threading
import time
import pytest
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
                    scheduled = host.handle(ControlRequest(1, "schedule-before-switch", None, "PROVIDER_RETRY_CLEAR",
                        {"run_id": run_id, "reason": "operator", "retry_at": time.time() + 3600}))
                    assert scheduled.status == "accepted", scheduled
                    switched = host.handle(ControlRequest(1, "switch-api", None, "PROVIDER_RETRY_CLEAR",
                        {"run_id": run_id, "reason": "operator", "settings_path": str(selected)}))
                    assert switched.status == "accepted", switched
                    assert switched.result["provider_retry"]["scheduled_retry"] is None
                assert not scenario.errors, scenario.errors
                if host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}: break
                time.sleep(.01)
            assert rejected and switched is not None
            assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
            attempts = host.store.list_attempts()
            failed = [row for row in attempts if row["terminal_reason"] != "SUCCEEDED"]
            assert failed and all(row["kind"] == "AGENT" for row in failed)
            failures = [json.loads(row["observation_json"])["diagnostics"]["provider_failure"] for row in failed]
            assert any(item["code"] == "quota_exhausted" and item["http_status"] == 429 for item in failures)
            # Scheduling can defer an already-open invocation before the switch.
            # That local admission is neither another remote rejection nor quota.
            assert all(item["code"] in {"quota_exhausted", "rate_limited"} or
                item == {"code": "local_request_queue", "http_status": None, "source": "local_admission"}
                for item in failures)
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


@pytest.mark.parametrize('failure_kind', ['remote_429', 'local_queue', 'remote_503', 'window_403', 'scheduled_window_403'])
def test_native_six_transient_429s_recover_without_operator_and_finish_cpu(tmp_path, monkeypatch, failure_kind):
    from hypoloom import provider_observation
    # Accelerate only the shared recovery policy, not Host/Process/model clocks.
    scheduled_mode = failure_kind == 'scheduled_window_403'
    monkeypatch.setattr(provider_observation, 'PROVIDER_RETRY_DELAYS', (18000,) if scheduled_mode else (.02, .04, .08, .1, .2))
    executable = _installed_claude()
    scenario = _DeclaredScenario(tmp_path, 'automatic-rpm-' + secrets.token_hex(12))
    provider, thread = _start_provider(scenario, 'local-rpm-key')
    post, rejected = provider.RequestHandlerClass.do_POST, []
    def overload(handler):
        if failure_kind in {'remote_429', 'remote_503', 'window_403', 'scheduled_window_403'} and len(rejected) < (1 if scheduled_mode else 6):
            handler.rfile.read(int(handler.headers.get('Content-Length', '0')))
            rejected.append(time.time())
            status = 503 if failure_kind == 'remote_503' else 429
            body = json.dumps({'error': {'code': str(status), 'message': 'Temporarily unavailable'}}).encode()
            if failure_kind in {'window_403', 'scheduled_window_403'}:
                from test_provider_observation import WINDOW_ERROR
                status, body = 403, json.dumps(WINDOW_ERROR).encode()
            handler.send_response(status)
            handler.send_header('Content-Type', 'application/json')
            handler.send_header('Content-Length', str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
        else:
            post(handler)
    provider.RequestHandlerClass.do_POST = overload
    if failure_kind == 'local_queue':
        from hypoloom.provider_traffic import ProviderTraffic, ProviderDeferred
        original_acquire = ProviderTraffic.acquire
        def queued(self, **kwargs):
            if len(rejected) < 6:
                rejected.append(time.time())
                raise ProviderDeferred('local_request_queue')
            return original_acquire(self, **kwargs)
        monkeypatch.setattr(ProviderTraffic, 'acquire', queued)
    (tmp_path / 'settings.json').write_text('{}')
    (tmp_path / 'settings.json').chmod(0o600)
    host = None
    run_id = 'native-rpm-recovery'
    try:
        host = _new_host(tmp_path, executable, f'http://127.0.0.1:{provider.server_port}', 'local-rpm-key')
        host.grounding_web = _NoPublicWeb()
        with tempfile.TemporaryDirectory(prefix='hypoloom-rpm-cpu-', dir='/var/tmp') as mount:
            payload = _six_yaml_profile_v4_payload(tmp_path, Path(mount), run_id)
            host.initialize()
            assert host.handle(ControlRequest(1, 'create', 0, 'CREATE_RUN', payload)).status == 'accepted'
            deadline = time.monotonic() + 240
            max_in_flight, max_rpm = 0, 0
            appointment = None
            while time.monotonic() < deadline:
                host.tick()
                status = host.handle(ControlRequest(1, 'observe', None, 'STATUS', {'run_id': run_id})).result
                assert not status['provider_retry']['requires_operator'], status['provider_retry']
                if scheduled_mode and appointment is None and status['provider_retry']['gates']:
                    at = time.time() + .5
                    appointment = host.handle(ControlRequest(1, 'scheduled-retry', None, 'PROVIDER_RETRY_CLEAR',
                        {'run_id': run_id, 'reason': 'operator', 'retry_at': at}))
                    assert appointment.status == 'accepted', appointment
                    host.close()
                    host = _new_host(tmp_path, executable, f'http://127.0.0.1:{provider.server_port}', 'local-rpm-key')
                    host.grounding_web = _NoPublicWeb()
                    host.initialize()
                    assert host._provider_retry_status(run_id)['scheduled_retry']['retry_at'] == at
                if failure_kind == 'local_queue':
                    assert status['provider_retry']['state'] == 'ready'
                traffic = status['provider_traffic']
                max_in_flight = max(max_in_flight, traffic['in_flight'])
                max_rpm = max(max_rpm, traffic['rpm'])
                assert not scenario.errors, scenario.errors
                if host.store.latest_transition(run_id)['state'] in {'COMPLETED', 'FAILED', 'CANCELLED'}:
                    break
                time.sleep(.01)
            assert len(rejected) == (1 if scheduled_mode else 6)
            assert host.store.latest_transition(run_id)['state'] == 'COMPLETED'
            attempts = host.store.list_attempts()
            if failure_kind in {'remote_503', 'window_403'}:
                failures = [json.loads(a['observation_json']) for a in attempts if a['terminal_reason'] == 'FAILED']
                assert len(failures) == 6
                code = 'usage_window_exhausted' if failure_kind == 'window_403' else 'service_unavailable'
                assert all(a['diagnostics']['provider_failure']['code'] == code for a in failures)
            processes = [a for a in attempts if a['kind'] == 'PROCESS']
            assert len(processes) == 1 and processes[0]['terminal_reason'] == 'SUCCEEDED'
            assert len(host.store.list_facts(run_id, 'CanonicalOutcomeAccepted')) == 1
            assert 'cpu_outcome' in scenario.parser_evidence
            assert all(a['state'] == 'CLOSED' for a in attempts)
            assert all(not process_group_exists(a['process_group_id']) for a in attempts if a['process_group_id'] is not None)
            assert not host.running and not host.running_agents
            controls = host.store.inspect("SELECT command_id FROM workflow_commands WHERE command_type='PROVIDER_RETRY_CLEAR'")
            assert len(controls) == (2 if scheduled_mode else 0)
            if scheduled_mode:
                assert {row['command_id'] for row in controls} == {'scheduled-retry', 'provider-scheduled-scheduled-retry'}
                assert host._provider_retry_status(run_id)['scheduled_retry'] is None
            assert max_in_flight <= payload['research_contract']['budget']['provider_max_in_flight'] and 0 < max_rpm <= 60
            assert traffic['in_flight'] == traffic['queued'] == 0
            assert traffic['rpm'] == traffic['grounding']['rpm'] + traffic['other']['rpm'] + sum(v['rpm'] for v in traffic['by_expert'].values())
            assert traffic['rpm'] == sum(v['rpm'] for v in traffic['by_attempt'].values())
            report = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
            assert report.ok, report.to_dict()
            assert report.open_attempt_count == report.pending_effect_count == 0
            (tmp_path / 'automatic-rpm-evidence.json').write_text(json.dumps({
                'failure_kind': failure_kind, 'rejections': len(rejected), 'attempts': len(attempts), 'processes': len(processes),
                'operator_commands': 1 if scheduled_mode else 0, 'host_restarted': scheduled_mode,
                'max_in_flight': max_in_flight, 'max_rpm': max_rpm,
                'oracle_ok': report.ok, 'process_residue': 0}, indent=2))
    finally:
        if host is not None: host.close()
        provider.shutdown(); provider.server_close(); thread.join(timeout=5)
