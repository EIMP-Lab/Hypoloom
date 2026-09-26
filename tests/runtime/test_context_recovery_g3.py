"""Native CLI + actual Host/CPU: a capacity refusal after committed submit."""
import json

from hypoloom.agent_aci import AgentRequest
from hypoloom.domain import AgentCommand
from hypoloom.provider_observation import read_exchanges
import hypoloom.context_management as context
import test_full_cc_host_g3 as native


def test_native_capacity_rebuild_preserves_committed_experiment_and_closes_cpu_science(tmp_path, monkeypatch):
    scenario = native._current_host_scenario(tmp_path)
    monkeypatch.setattr(native, "_current_host_scenario", lambda _: scenario)
    original_capacity = context.request_capacity
    injected = []

    def refuse_after_submit(document, policy):
        capacity = original_capacity(document, policy)
        request = native._request_from_provider(document)
        results = native._tool_results(document)
        if not injected and request.command is AgentCommand.EXPERIMENT_EXECUTION and "submit_experiment" in results:
            # Fault at the real wire boundary after the real submit transaction,
            # before another provider request. The next Attempt must reconcile.
            injected.append(request.attempt_id)
            result = results["submit_experiment"]
            scenario.tool_evidence["submit_experiment"] = result
            scenario.experiment_id = json.loads(native._result_text(result))["exp_id"]
            capacity = {**capacity, "fits": False}
        return capacity

    monkeypatch.setattr(context, "request_capacity", refuse_after_submit)
    native._exercise_full_cc_host(tmp_path, execution_retries=1, timeout_seconds=240)
    assert len(injected) == 1
    rows = native._rows(tmp_path / "state/state.sqlite", "SELECT * FROM execution_attempts ORDER BY started_at")
    agents = [(row, json.loads(row["observation_json"])) for row in rows if row["kind"] == "AGENT"]
    failed, = [(row, value) for row, value in agents if row["attempt_id"] == injected[0]]
    assert failed[0]["terminal_reason"] == "FAILED"
    assert failed[1]["diagnostics"]["provider_failure"]["code"] == "context_capacity"
    assert failed[1]["retry_class"] == "INFRASTRUCTURE"
    assert "recovery_action" not in failed[1]["diagnostics"]["provider_failure"]
    assert failed[1]["diagnostics"]["context_observation"]["coverage"] == "complete"
    assert failed[1]["diagnostics"]["context_observation"]["native_compaction_count"] == 0
    retried, = [(row, value) for row, value in agents
        if row["command_id"] == failed[0]["command_id"] and row["attempt_id"] != injected[0]]
    assert retried[0]["terminal_reason"] == "SUCCEEDED"
    assert retried[1]["session"]["session_id"] != failed[1]["session"]["session_id"]
    assert retried[1]["session"]["branch"] == failed[1]["session"]["branch"]
    assert AgentRequest.from_dict(retried[1]["request"]).input_artifacts == AgentRequest.from_dict(failed[1]["request"]).input_artifacts
    assert retried[1]["diagnostics"]["inline_input_bytes"] <= failed[1]["diagnostics"]["inline_input_bytes"]
    prompt = json.loads((tmp_path / "state/diagnostics/agents/full-cc-host-g3"
        / retried[0]["attempt_id"] / "prompt.txt").read_text())
    notes = prompt["continuation_observation"]
    assert notes["source_attempt_id"] == injected[0]
    assert any(item["tool"] == "mcp__host-tools__submit_experiment" for item in notes["previous_tools"])
    assert notes["owned_experiments"][0]["exp_id"] == scenario.experiment_id
    exchanges = read_exchanges(tmp_path / "state/diagnostics/agents/full-cc-host-g3" / injected[0] / "provider")
    rejected = [item for item in exchanges if item.get("failure_reason") == "context_capacity"]
    assert rejected and all(item.get("request_bytes_forwarded", 0) == 0 for item in rejected)
    # No paid API or native summary is needed for this continuation.
    assert all(b'"compact_boundary"' not in (tmp_path / "state/artifacts" / value["session"]["transcript"]["path"] / "transcript.jsonl").read_bytes()
        for _, value in agents)


def test_native_initial_refusal_stops_at_minimum_without_spending_nine_retries(tmp_path):
    import tempfile
    import time
    from collections import Counter
    from pathlib import Path
    import yaml
    from hypoloom.control_protocol import ControlRequest
    from hypoloom.oracle import Oracle
    from hypoloom.profile_admission import admit_profile
    from test_claude_file_tool_cli import _installed_claude
    from test_claude_shared_acceptance_g3 import _AcceptanceScenario, _RUN, _payload
    from test_claude_measurement_g3 import _NoPublicWeb

    scenario = _AcceptanceScenario(tmp_path, "no-upstream-generation")
    server, thread = native._start_provider(scenario, "local-futile-retry")
    (tmp_path / "settings.json").write_text("{}")
    (tmp_path / "settings.json").chmod(0o600)
    host = native._new_host(tmp_path, _installed_claude(), f"http://127.0.0.1:{server.server_port}", "local-futile-retry")
    host.grounding_web = _NoPublicWeb()
    try:
        with tempfile.TemporaryDirectory(prefix="context-refusal-", dir="/var/tmp") as mount:
            _payload(tmp_path, Path(mount), roles=("never-started",))
            path = tmp_path / "profile/methodology.yaml"
            document = yaml.safe_load(path.read_text())
            document["memory"] = {"working_context_tokens": 1000}
            document["budget"]["max_infrastructure_retries"] = 9
            path.write_text(yaml.safe_dump(document))
            admission = admit_profile(tmp_path / "profile", run_id=_RUN, allow_gpu=False)
            assert admission.admitted, admission.to_dict()
            host.initialize()
            assert host.handle(ControlRequest(1, "create-refusal", 0, "CREATE_RUN", admission.create_run_payload)).status == "accepted"
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline and host.store.latest_transition(_RUN)["state"] not in {"COMPLETED", "FAILED", "CANCELLED"}:
                host.tick()
                time.sleep(.01)
            assert host.store.latest_transition(_RUN)["state"] == "FAILED"
            rows = host.store.list_attempts()
            assert rows and all(row["kind"] == "AGENT" and row["state"] == "CLOSED" for row in rows)
            counts = Counter(row["command_id"] for row in rows)
            assert max(counts.values()) == 3 and all(count <= 3 for count in counts.values())
            assert not scenario.requests  # Actual native CLI; zero upstream calls.
            for row in rows:
                observation = json.loads(row["observation_json"])
                failure = observation["diagnostics"]["provider_failure"]
                assert failure["code"] == "context_capacity"
                if row["attempt_ordinal"] == 3:
                    assert failure["recovery_action"] == "input_reconfiguration_required"
                    assert observation["retry_class"] is None
            audit = Oracle(host.store.database_path, host.publisher.root).audit(_RUN)
            assert audit.ok, audit.to_dict()
            assert audit.open_attempt_count == audit.pending_effect_count == 0
            assert not audit.owned_process_residue
    finally:
        host.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
