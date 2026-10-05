"""Native resource Skill/query/SELF_EVOLVE feedback on real CC and bwrap CPU.

The pool is an actual private Unix-socket/SQLite service with synthetic member
inventory. This run authorizes no GPU and fabricates no workload measurement:
it proves native interface/next-cycle closure, not GPU memory learning or CUDA.
"""

from __future__ import annotations

from collections import defaultdict
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import time

import pytest

from hypoloom import compute_resources
from hypoloom.agent_aci import CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION, AgentRequest
from hypoloom.compute_resources import GpuRuntimeObservation
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.expert_plugin import ExpertPluginStore
from hypoloom.gpu_pool import CapacitySample, GpuPool
from hypoloom.gpu_pool_client import PoolClient
from hypoloom.gpu_pool_server import PoolServer
from hypoloom.oracle import Oracle, audit_gpu_pool

from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results
from test_expert_loop_host import _pilot_payload
from test_expert_memory_g3 import _FinalReportMemoryScenario, _cycle, _resources
import test_full_cc_host_g3 as native


_SKILL = "skills/resource-planning/SKILL.md"
_ANCHOR = "for a later cycle; an already bound cycle and current grant never change."
_RESOURCE_COMMANDS = {AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, AgentCommand.EXPERIMENT_EXECUTION}
_HISTORY_COMMANDS = _RESOURCE_COMMANDS | {AgentCommand.CYCLE_REPORT, AgentCommand.SELF_EVOLVE}


def _resource_skill_from_prompt(document, request):
    texts = []
    for message in document["messages"]:
        content = message.get("content", ())
        if isinstance(content, str):
            texts.append(content)
        else:
            texts.extend(block["text"] for block in content if block.get("type") == "text")
    for text in reversed(texts):
        start = text.find('{"agent_request_metadata":')
        if start < 0:
            continue
        prompt, _ = json.JSONDecoder().raw_decode(text[start:])
        names = re.findall(r"Your bound native resource Skill is `([^`]+)`", prompt["instruction"])
        assert len(names) == 1, "actual production prompt must identify one bound resource Skill"
        name = names[0]
        assert name == _resources(request)["plugin"]["name"] + ":resource-planning"
        assert any(line.startswith("- " + name + ": ") for text in texts for line in text.splitlines()), (
            "production prompt names a Skill absent from the native available list")
        guidance = next(item for item in prompt["rendered_inputs"] if item["ref"].endswith("--resource-planning-skill"))
        assert not guidance["content_utf8"].startswith("---\n")
        assert "not a callable Skill" in guidance["content_utf8"]
        return name
    raise AssertionError("actual native provider request lacks its production prompt")


class _ResourceLearningScenario(_FinalReportMemoryScenario):
    def __init__(self, private_root: Path):
        super().__init__(private_root, "cpu-memory-lesson-" + secrets.token_hex(8))
        # This rehearsal covers the next-cycle Git boundary; the base memory G3
        # separately covers restart. Do not inject its sleeping restart branch.
        self.restart_completed = True
        self.resource_nonce = "native-resource-lesson-" + secrets.token_hex(12)
        self.prefix_steps = defaultdict(int)
        self.frozen_requests = {}
        self.history = {}
        self.live_queries = {}
        self.skill_results = {}
        self.skill_names = {}
        self.evolution_results = {}

    def _identity(self, label, request):
        return f"{label}_{request.attempt_id}"

    def _tool_result(self, document, label, request):
        result = _tool_results(document)[self._identity(label, request)]
        assert not result.get("is_error"), result
        return _result_text(result)

    def record(self, document):
        request = native._request_from_provider(document)
        identity = request.attempt_id
        try:
            assert request.schema_version == CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION
            frozen = self.frozen_requests.setdefault(identity, request.to_json())
            assert request.to_json() == frozen, "live tools changed the captured Agent inputs"
            tools = {item["name"] for item in document["tools"]}
            if request.command in _HISTORY_COMMANDS:
                item = next(item for item in request.input_artifacts if item.ref.endswith("--resource-experience"))
                history = json.loads(item.content_bytes())
                assert history["mode"] == "shared-pool" and history["measurements"] == []
                assert self.history.setdefault(identity, history) == history
                for group in history["groups"]:
                    assert group["role"] == request.role and group["component_id"] == request.component_id
                if request.command in {AgentCommand.CYCLE_REPORT, AgentCommand.SELF_EVOLVE}:
                    assert "mcp__host-tools__get_resources" not in tools
                    assert "mcp__host-tools__submit_experiment" not in tools
                    assert history["source_attempt_count"] >= 1
            if request.command in _RESOURCE_COMMANDS:
                assert "mcp__host-tools__get_resources" in tools
                name = _resource_skill_from_prompt(document, request)
                assert self.skill_names.setdefault(identity, name) == name
                if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
                    assert not {"mcp__host-tools__submit_experiment", "mcp__host-tools__cancel_experiment"} & tools
                phase = self.prefix_steps[identity]
                if phase < 3:
                    phase += 1
                    self.prefix_steps[identity] = phase
                    if phase == 2:
                        self._tool_result(document, "resource_skill", request)
                        # Native Skill acknowledges the call as a tool result,
                        # then injects its body as a separate user text block.
                        bodies = [block["text"] for message in document["messages"]
                            for block in message.get("content", ()) if isinstance(block, dict)
                            and block.get("type") == "text" and "Base directory for this skill: /expert-plugin/skills/resource-planning" in block.get("text", "")]
                        assert bodies, "native Skill body was not injected after its acknowledgment"
                        result = bodies[-1]
                        assert "Device used/free memory is" in result
                        assert (self.resource_nonce in result) is (_cycle(request) == 2)
                        self.skill_results[identity] = result
                    if phase == 3:
                        result = json.loads(self._tool_result(document, "resource_query", request))
                        assert result["mode"] == "shared-pool" and result["status"] == "cpu_only"
                        assert result["devices"] == result["measurements"] == []
                        assert result["measurement_scope"] == "current_run_role_component_family"
                        assert result["observed_at"] > 0
                        self.live_queries[identity] = result
                    else:
                        self.requests.append(document)
                        self.current = request
                        self.agent_requests[identity] = request
                        return len(self.requests)
            elif request.command is AgentCommand.SELF_EVOLVE:
                phase = self.prefix_steps[identity]
                if phase < 5:
                    phase += 1
                    self.prefix_steps[identity] = phase
                    previous = {2: "evolve_skill", 3: "evolve_read", 4: "evolve_edit", 5: "evolve_check"}.get(phase)
                    if previous:
                        result = self._tool_result(document, previous, request)
                        self.evolution_results[self._identity(previous, request)] = result
                        if previous == "evolve_check":
                            assert self.resource_nonce in result and "RESOURCE_PLUGIN_BOUNDARY_OK" in result
                    if phase < 5:
                        self.requests.append(document)
                        self.current = request
                        self.agent_requests[identity] = request
                        return len(self.requests)
        except Exception as exc:
            self.errors.append(f"resource interface {identity}: {type(exc).__name__}: {exc}")
        return super().record(document)

    def tool_calls(self, number):
        request = self.current
        if request is None or self.errors:
            return ()
        phase = self.prefix_steps[request.attempt_id]
        if request.command in _RESOURCE_COMMANDS and phase < 3:
            if phase == 1:
                return ((self._identity("resource_skill", request), "Skill",
                         {"skill": self.skill_names[request.attempt_id]}),)
            return ((self._identity("resource_query", request), "mcp__host-tools__get_resources", {}),)
        if request.command is AgentCommand.SELF_EVOLVE and phase < 5:
            if phase == 1:
                return ((self._identity("evolve_skill", request), "Skill",
                         {"skill": _resources(request)["plugin"]["name"] + ":self-evolve"}),)
            if phase == 2:
                return ((self._identity("evolve_read", request), "Read", {"file_path": "/plugin-update/" + _SKILL}),)
            if phase == 3:
                lesson = (f"\nCPU-only cycle {_cycle(request)} provided no attributed GPU peak. Keep GPU memory "
                    f"unknown and query live authorized resources before a later GPU plan. {self.resource_nonce}\n")
                return ((self._identity("evolve_edit", request), "Edit", {"file_path": "/plugin-update/" + _SKILL,
                    "old_string": _ANCHOR, "new_string": _ANCHOR + lesson}),)
            script = (
                "import errno, os\nfrom pathlib import Path\n"
                f"path = Path('/plugin-update/{_SKILL}')\nassert {self.resource_nonce!r} in path.read_text()\n"
                f"bound = Path('/expert-plugin/{_SKILL}')\n"
                f"assert ({self.resource_nonce!r} in bound.read_text()) is {(_cycle(request) == 2)!r}\n"
                "try:\n    fd = os.open(bound, os.O_WRONLY | os.O_APPEND)\n"
                "except OSError as error:\n    assert error.errno == errno.EROFS\n"
                "else:\n    os.close(fd)\n    raise AssertionError('bound plugin became writable')\n"
                f"assert not Path({str(self.private_root)!r}).exists()\n"
                f"assert {{p.name for p in Path('/research/experts').iterdir()}} == {{{request.role!r}}}\n"
                f"print({self.resource_nonce!r}, 'RESOURCE_PLUGIN_BOUNDARY_OK')\n"
            )
            return ((self._identity("evolve_check", request), "Bash",
                     {"command": "python3 - <<'PY'\n" + script + "PY", "timeout": 10000}),)
        return super().tool_calls(number)


def test_native_resource_skill_query_and_self_evolve_close_two_cpu_cycles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    executable = _installed_claude()
    synthetic_uuid = "GPU-00000000-0000-0000-0000-000000000001"
    sample = CapacitySample(devices=(GpuRuntimeObservation(uuid=synthetic_uuid,
        total_memory_mib=16000, free_memory_mib=16000, used_memory_mib=0, utilization_percent=0),))
    pool = GpuPool(tmp_path / "pool", members={os.getuid(): (synthetic_uuid,)},
        reserve_memory_mib=0, sampler=lambda: sample)
    socket_path = tmp_path / "p.sock"
    original_host = native.WorkflowHost

    def configured_host(*args, **kwargs):
        return original_host(*args, **kwargs, gpu_pool_socket=socket_path,
            gpu_pool_namespace="native-learning-root", gpu_pool_server_uid=os.getuid())

    def forbidden_gpu(*args, **kwargs):
        raise AssertionError("native CPU resource rehearsal attempted physical GPU access")

    monkeypatch.setattr(native, "WorkflowHost", configured_host)
    monkeypatch.setattr(compute_resources, "_nvml_inventory", forbidden_gpu)
    monkeypatch.setattr(compute_resources, "_nvml_runtime_observation", forbidden_gpu)
    trial = tmp_path / "trial"
    trial.mkdir()
    scenario = _ResourceLearningScenario(trial)
    host_key = "local-native-learning-" + secrets.token_hex(16)
    provider, thread = native._start_provider(scenario, host_key)
    (trial / "settings.json").write_text("{}\n")
    (trial / "settings.json").chmod(0o600)
    host = None
    run_id = "native-resource-learning-g3"
    evidence = {"gpu_effects": 0, "gpu_measurements": 0, "paid_provider_calls": 0}
    try:
        with PoolServer(pool, socket_path) as server, tempfile.TemporaryDirectory(prefix="p3-native-target-", dir="/var/tmp") as mount:
            client = PoolClient(socket_path, expected_server_uid=os.getuid())
            assert client.request("snapshot", {})["devices"][0]["active_commitments"] == 0
            host = native._new_host(trial, executable, f"http://127.0.0.1:{provider.server_port}", host_key)
            assert host.version.research_contract_version == "hypoloom-research-v12"
            host.initialize()
            payload = _pilot_payload(trial, Path(mount), run_id)
            payload["research_contract"]["budget"].update(min_vram_per_experiment=1024,
                initial_vram_per_experiment=8192, gpu_vram_reserve_mb=512, gpu_vram_reserve_percent=5)
            created = host.handle(ControlRequest(schema_version=1, request_id="create-native-learning", expected_revision=0,
                command="CREATE_RUN", payload=payload))
            assert created.status == "accepted", created
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                host.tick()
                assert not scenario.errors, scenario.errors
                if host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
                time.sleep(0.01)
            assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
            attempts = host.store.list_attempts()
            assert all(row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED" for row in attempts)
            processes = [row for row in attempts if row["kind"] == "PROCESS"]
            agents = [json.loads(row["observation_json"]) for row in attempts if row["kind"] == "AGENT"]
            assert len(processes) == 2
            assert [row["payload"]["outcome"] for row in host.store.list_facts(run_id, "CanonicalOutcomeAccepted")] == ["CLEAN_REFUTATION", "SUCCESS"]
            evolutions = [row for row in agents if row["request"]["command"] == "SELF_EVOLVE"]
            assert len(evolutions) == 2
            first_update = evolutions[0]["plugin_update"]
            assert first_update["commit"] != first_update["base_commit"]
            requests = [AgentRequest.from_dict(row["request"]) for row in agents]
            for request in requests:
                if request.attempt_id in scenario.frozen_requests:
                    assert request.to_json() == scenario.frozen_requests[request.attempt_id]
                if request.command in _HISTORY_COMMANDS:
                    assert request.attempt_id in scenario.history
                    expected = first_update["base_commit"] if _cycle(request) == 1 else first_update["commit"]
                    assert _resources(request)["plugin"]["commit"] == expected
            assert len(scenario.live_queries) == len(scenario.skill_results) == len(scenario.skill_names) == 4
            assert len(scenario.evolution_results) == 8
            assert len(scenario.report_attempts) == 2 and scenario.consumers
            query_times = [item["observed_at"] for item in scenario.live_queries.values()]
            assert query_times == sorted(query_times) and query_times[-1] > query_times[0]
            store = ExpertPluginStore(host.state_root)
            role = evolutions[0]["request"]["role"]
            for commit, contains_lesson in ((first_update["base_commit"], False), (first_update["commit"], True)):
                view = store.read(run_id, role, commit=commit, attempt_id="verify-" + commit, max_bytes=1024 * 1024)
                try:
                    assert (scenario.resource_nonce in (view.root / _SKILL).read_text()) is contains_lesson
                finally:
                    view.close()
            dispatches = host.store.inspect("SELECT payload_json FROM workflow_transitions WHERE json_type(payload_json,'$.process_request')='object'")
            assert len(dispatches) == 2
            for row in dispatches:
                payload = json.loads(row["payload_json"])
                assert any(Path(arg).name == "bwrap" for arg in payload["process_request"]["argv"])
                assert not any("/dev/nvidia" in arg for arg in payload["process_request"]["argv"])
                assert "gpu_pool_key" not in payload
            assert native._rows(pool.database_path, "SELECT * FROM requests") == []
            assert client.request("snapshot", {})["devices"][0]["active_commitments"] == 0
            assert audit_gpu_pool(pool.root)["ok"]
            groups = [row["process_group_id"] for row in attempts if row["process_group_id"] is not None]
            for pgid in groups:
                with pytest.raises(ProcessLookupError):
                    os.killpg(pgid, 0)
            archives = []
            for observation in agents:
                session = observation["session"]
                assert session["transcript_complete"]
                transcript = host.publisher.root / session["transcript"]["path"] / "transcript.jsonl"
                assert transcript.is_file() and host_key.encode() not in transcript.read_bytes()
                archives.append(str(transcript))
            oracle = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
            (trial / "oracle.json").write_text(json.dumps(oracle.to_dict(), indent=2))
            assert oracle.ok, oracle.to_dict()
            assert oracle.canonical_outcome_count == 2
            assert oracle.open_attempt_count == oracle.pending_effect_count == 0
            assert oracle.artifact_counts["TRANSCRIPT"] == len(archives)
            assert not host.running and not host.running_agents and not host.projection_errors
            evidence.update(agent_attempts=len(agents), process_attempts=len(processes), absent_process_groups=len(groups),
                transcript_count=len(archives), transcripts=archives, first_plugin_update=first_update)
            host.close()
            host = None
        assert not socket_path.exists() and not server.thread.is_alive()
    finally:
        if host is not None:
            host.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
        evidence.update(errors=scenario.errors, live_queries=scenario.live_queries, history=scenario.history,
            native_skill_names=scenario.skill_names, native_skill_results=scenario.skill_results,
            evolution_results=scenario.evolution_results)
        (trial / "native-resource-evidence.json").write_text(json.dumps(evidence, indent=2))
