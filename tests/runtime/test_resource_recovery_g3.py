"""Native CLI outage, operator recheck, real CPU acceptance; no CUDA or paid API."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import yaml
import pytest

from hypoloom.compute_resources import ComputeCapabilities, GpuCapability, GpuRuntimeObservation, LocalComputeResources
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.gpu_pool import CapacitySample, GpuPool
from hypoloom.gpu_pool_server import PoolServer
from hypoloom.oracle import Oracle, audit_gpu_pool
from hypoloom.profile_admission import admit_profile
from hypoloom.runner import process_group_exists
from test_claude_file_tool_cli import _installed_claude
from test_claude_measurement_g3 import _NoPublicWeb
from test_claude_shared_acceptance_g3 import _AcceptanceScenario, _GOAL, _RUN, _payload
import test_full_cc_host_g3 as native


@pytest.mark.parametrize("completed_reply_restart", [False, True], ids=["cancel-in-flight", "completed-reply-restart"])
def test_native_pool_fault_closes_rechecks_and_collects_cpu(tmp_path, monkeypatch, completed_reply_restart):
    uuid = "GPU-00000000-0000-0000-0000-000000000001"
    capability = GpuCapability(index=0, uuid=uuid, name="synthetic", total_memory_mib=16000,
        free_memory_mib=16000, driver_version="fixture")
    resources = LocalComputeResources(allowed_gpu_uuids=(uuid,), gpu_runtime_prefix=sys.prefix,
        inventory_provider=lambda: ComputeCapabilities(gpus=(capability,)))
    sample = CapacitySample(devices=(GpuRuntimeObservation(uuid=uuid, total_memory_mib=16000,
        free_memory_mib=16000, used_memory_mib=0, utilization_percent=0),))
    scenario = _AcceptanceScenario(tmp_path, "native-resource-recovery")
    original_tools, original_record = scenario.tool_calls, scenario.record
    fault = {"attempt": None, "active": False, "rechecked": False, "reply_window": completed_reply_restart}
    def record(document):
        request = native._request_from_provider(document)
        if request.attempt_id == fault["attempt"]:
            scenario.requests.append(document)
            scenario.current = request
            scenario.steps[request.attempt_id] += 1
            return len(scenario.requests)
        return original_record(document)
    scenario.record = record
    def tools(number):
        request = scenario.current
        if request.command is AgentCommand.EXPERIMENT_EXECUTION and fault["attempt"] is None:
            fault.update(attempt=request.attempt_id, active=True)
            return (("resource_preflight", "mcp__host-tools__get_resources", {}),)
        if request.attempt_id == fault["attempt"]:
            response = scenario.responses[request.attempt_id]
            return (("resource_exit", "StructuredOutput", {"typed_candidate": response.typed_candidate,
                "declared_effects": [{"effect": item.effect} for item in response.declared_effects]}),)
        return original_tools(number)
    scenario.tool_calls = tools
    provider, thread = native._start_provider(scenario, "local-resource-test")
    (tmp_path / "settings.json").write_text("{}")
    (tmp_path / "settings.json").chmod(0o600)
    with tempfile.TemporaryDirectory(prefix="hypoloom-rr-", dir="/var/tmp") as directory:
        mount = Path(directory)
        pool = GpuPool(mount / "pool", members={os.getuid(): (uuid,)}, reserve_memory_mib=0, sampler=lambda: sample)
        original_pool = pool.handle
        def handle(*args):
            result = original_pool(*args)
            if args[1] == "snapshot" and fault["active"]: result = {**result, "capabilities": []}
            return result
        monkeypatch.setattr(pool, "handle", handle)
        factory = native.WorkflowHost
        def configured(*args, **kwargs):
            kwargs["compute_resources"] = resources
            return factory(*args, **kwargs, gpu_pool_socket=mount / "pool.sock", gpu_pool_namespace="isolated-native-recovery")
        monkeypatch.setattr(native, "WorkflowHost", configured)
        with PoolServer(pool, mount / "pool.sock"):
            host = native._new_host(tmp_path, _installed_claude(), f"http://127.0.0.1:{provider.server_port}", "local-resource-test")
            host.grounding_web = _NoPublicWeb()
            original_cancel = host.claude_agent_host.cancel
            def cancel(handle):
                # Also exercise the late-poll boundary: let the blocked node's
                # terminal tool reply reach CC before Host observes closure.
                # The actual resource block still prevents every submission.
                if fault["reply_window"] and handle.request.attempt_id == fault["attempt"]:
                    return
                return original_cancel(handle)
            monkeypatch.setattr(host.claude_agent_host, "cancel", cancel)
            try:
                _payload(tmp_path, mount, roles=(_GOAL,))
                safety = tmp_path / "profile/safety.yaml"
                document = yaml.safe_load(safety.read_text()); document["gpus"] = [0]; safety.write_text(yaml.safe_dump(document))
                methodology = tmp_path / "profile/methodology.yaml"
                document = yaml.safe_load(methodology.read_text())
                document["runtime"]["conda_env"] = sys.prefix
                document["budget"]["max_infrastructure_retries"] = 1
                document["budget"]["min_vram_per_experiment"] = 1024
                methodology.write_text(yaml.safe_dump(document))
                admission = admit_profile(tmp_path / "profile", run_id=_RUN, allow_gpu=True)
                assert admission.admitted, admission.to_dict()
                host.initialize()
                created = host.handle(ControlRequest(1, "create", 0, "CREATE_RUN", admission.create_run_payload))
                assert created.status == "accepted", created
                deadline = time.monotonic() + 300
                while time.monotonic() < deadline:
                    host.tick()
                    assert not scenario.errors, scenario.errors
                    if fault["attempt"] and not fault["rechecked"]:
                        attempt = host.store.get_attempt(fault["attempt"])
                        if attempt["state"] == "CLOSED":
                            assert attempt["terminal_reason"] == "FAILED"
                            observed = json.loads(attempt["observation_json"])
                            assert observed["failure_code"] == "resource_infrastructure_blocked", observed
                            assert observed["session"]["transcript"]
                            assert not host.store.list_workflow_heads(_RUN, "EXPERIMENT")
                            fault["reply_window"] = False
                            if completed_reply_restart:
                                assert scenario.steps[fault["attempt"]] >= 2
                                host.close()
                                host = native._new_host(tmp_path, _installed_claude(), f"http://127.0.0.1:{provider.server_port}", "local-resource-test")
                                host.grounding_web = _NoPublicWeb()
                                host.initialize()
                            fault["active"] = False
                            response = host.handle(ControlRequest(1, "resource-recheck", None, "RESOURCE_RECHECK", {"run_id": _RUN, "reason": "operator"}))
                            assert response.status == "accepted", response
                            fault["rechecked"] = True
                    if host.store.latest_transition(_RUN)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}: break
                    time.sleep(.01)
                assert fault["rechecked"] and host.store.latest_transition(_RUN)["state"] == "COMPLETED"
                attempts = host.store.list_attempts(run_id=_RUN)
                processes = [row for row in attempts if row["kind"] == "PROCESS"]
                assert len(processes) == 1 and processes[0]["terminal_reason"] == "SUCCEEDED"
                assert not json.loads(processes[0]["observation_json"]).get("synthetic_before_spawn")
                executions = [json.loads(row["observation_json"]) for row in attempts
                    if row["kind"] == "AGENT" and json.loads(row["observation_json"])["request"]["command"] == "EXPERIMENT_EXECUTION"]
                executions.sort(key=lambda item: item["request"]["attempt_ordinal"])
                assert len(executions) == 2
                blocked, recovered = executions
                assert blocked["request"]["command_id"] == recovered["request"]["command_id"]
                assert recovered["session"]["session_id"] != blocked["session"]["session_id"]
                assert recovered["session"]["launch_arguments"] == ["--session-id", recovered["session"]["session_id"]]
                specs = host.store.list_facts(_RUN, "ExperimentPreregistered")
                assert len(specs) == 1 and specs[0]["payload"]["execution_contract"]["resource_grant"]["kind"] == "CPU"
                assert host.store.list_facts(_RUN, "CanonicalOutcomeAccepted") and host.store.list_facts(_RUN, "HypothesisAssessed")
                assert all(row["state"] == "CLOSED" for row in attempts)
                assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"])
                report = Oracle(host.store.database_path, host.publisher.root).audit(_RUN)
                assert report.ok, report.to_dict()
                pool_report = audit_gpu_pool(pool.root); assert pool_report["ok"], pool_report
                (tmp_path / "resource-recovery-evidence.json").write_text(json.dumps({"attempts": len(attempts), "processes": len(processes), "blocked_attempt": fault["attempt"], "oracle": report.to_dict(), "pool": pool_report}, indent=2))
            finally:
                fault["reply_window"] = False
                host.close()
                provider.shutdown(); provider.server_close(); thread.join(timeout=5)
