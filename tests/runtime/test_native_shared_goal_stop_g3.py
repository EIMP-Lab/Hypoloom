"""Native shared-goal stop: two experts, real MCP/Processes, and Host restart.

Only provider choices and literature are local fixtures. No FakeAgent is installed
on the Host. The opt-in GPU variant is separate from the default CPU rehearsal.
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from hypoloom.agent_aci import WorkspaceArtifact, with_output_digest
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.compute_resources import LocalComputeResources
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.oracle import Oracle

from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results
from test_claude_final_report_cli import _FinalReportScenario
from test_claude_measurement_g3 import _NoPublicWeb
from test_expert_batch_host import SharedGoalAgent
from test_full_cc_host_g3 import (
    _HostScenario,
    _MODEL,
    _new_host,
    _request_from_provider,
    _start_provider,
)
from test_native_gpu_g4 import _gpu_host
from test_real_expert_gpu_qualification import _assert_gpu_idle, _configured_gpu
from test_shared_goal_finish_g3 import PEER, RUN, WINNER, _payload as fake_payload


_FAIL = "NATIVE_SHARED_LOCAL_FAILURE"
_PASS = "NATIVE_SHARED_LOCAL_REPAIRED"
_INNER = {
    AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
    AgentCommand.EXPERIMENT_EXECUTION,
    AgentCommand.RESULT_ANALYSIS,
}


def _read_json(text):
    lines = []
    for line in text.splitlines():
        prefix, separator, rest = line.partition("\t")
        if separator and prefix.strip().isdigit():
            line = rest
        if line.lstrip().startswith("{"):
            lines.append(json.loads(line))
    assert len(lines) == 1, "Read must expose exactly one complete source JSON object"
    return lines[0]


class _SharedFinalReport(_FinalReportScenario):
    def record(self, document):
        if self.stage_step != 2:
            return super().record(document)
        # The stop cause contains nested command JSON. Native Read preserves
        # JSON escapes; compare decoded actual bytes, not an unescaped substring.
        self.requests.append(document)
        self.stage_step += 1
        try:
            result = _tool_results(document)[self.tool_prefix + "report_sources"]
            assert not result.get("is_error"), result
            text = _result_text(result)
            self.evidence["report_sources"] = text
            assert _read_json(text) == self.context
        except Exception as exc:
            self.errors.append(f"shared final source: {type(exc).__name__}: {exc}")
        return len(self.requests)


class _SharedScenario(_HostScenario):
    def __init__(self, root: Path, *, gpu: bool):
        super().__init__(root)
        self.gpu = gpu
        self.reviser = SharedGoalAgent(all_refuted=False)
        self.nonces = {role: "native-private-" + secrets.token_hex(12) for role in (WINNER, PEER)}
        self.designs, self.experiments, self.waited, self.reports = {}, {}, {}, {}
        self.local_passes = set()
        self.issued = set()

    def record(self, document):
        self.requests.append(document)
        try:
            request = _request_from_provider(document)
            self.current = request
            self.agent_requests[request.attempt_id] = request
            self.steps[request.attempt_id] += 1
            step = self.steps[request.attempt_id]
            if request.attempt_id not in self.responses:
                response = self.reviser.invoke(request)
                if request.command is AgentCommand.RESULT_ANALYSIS and request.role == WINNER:
                    response = with_output_digest(replace(response, typed_candidate={
                        **response.typed_candidate, "iteration_disposition": "ITERATE"}))
                if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
                    response = with_output_digest(replace(response, typed_candidate={
                        **response.typed_candidate,
                        "resource_preference": "GPU_REQUIRED" if self.gpu else "CPU"}))
                    self.designs[request.role] = response.typed_candidate
                self.responses[request.attempt_id] = response
            if request.command in _INNER:
                text = json.dumps(document["messages"])
                assert self.nonces[PEER if request.role == WINNER else WINNER] not in text
                if request.command is not AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT or step > 1:
                    assert self.nonces[request.role] in text
            prefix = self._prefix(request)
            results = _tool_results(document)
            if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
                previous = {2: "design_write", 3: "local_fail", 4: "design_repair", 5: "local_pass"}.get(step)
                if request.role == PEER and step == 6:
                    previous = "await_winner"
                if previous:
                    result = results[prefix + previous]
                    if previous == "local_fail":
                        assert result.get("is_error") and _FAIL in _result_text(result)
                    else:
                        assert not result.get("is_error"), result
                    if previous == "local_pass":
                        assert _PASS in _result_text(result)
                        self.local_passes.add(request.role)
            elif request.command is AgentCommand.EXPERIMENT_EXECUTION:
                submit_step = 2
                if step == submit_step:
                    result = results[prefix + "submit"]
                    assert not result.get("is_error"), result
                    self.experiments[request.role] = json.loads(_result_text(result))["exp_id"]
                if step > submit_step:
                    result = results[prefix + f"wait_{step - 1}"]
                    assert not result.get("is_error"), result
                    waited = json.loads(_result_text(result))
                    assert waited["exp_id"] == self.experiments[request.role]
                    if waited["terminal"]:
                        assert waited["outcome"]["outcome"] == ("SUCCESS" if request.role == WINNER else "CANCELLED")
                        self.waited[request.role] = waited
            elif request.command is AgentCommand.RESULT_ANALYSIS:
                assert _FAIL in json.dumps(document) and _PASS in json.dumps(document)
                assert "mcp__host-tools__submit_experiment" in json.dumps(document)
            elif request.command is AgentCommand.FINAL_REPORT:
                if request.attempt_id not in self.reports:
                    report = _SharedFinalReport(request)
                    report.tool_prefix = prefix
                    if self.gpu:
                        report.report = report.report.replace(
                            "no paid model or GPU was used", "no paid model was used; authorized CUDA devices were used")
                    self.reports[request.attempt_id] = report
                report = self.reports[request.attempt_id]
                response = self.responses[request.attempt_id]
                report.structured_output = self._structured(response)
                report.record(document)
                assert not report.errors, report.errors
        except Exception as exc:
            self.errors.append(f"native shared provider {len(self.requests)}: {type(exc).__name__}: {exc}")
        return len(self.requests)

    @staticmethod
    def _prefix(request):
        return request.attempt_id + "__"

    @staticmethod
    def _structured(response):
        candidate = dict(response.typed_candidate)
        if "query" in candidate:
            return {"typed_candidate": {key: value for key, value in candidate.items()
                                        if key in {"query", "preferred_venues"}}}
        return {"typed_candidate": candidate,
                "declared_effects": [{"effect": item.effect} for item in response.declared_effects]}

    def _source(self, request):
        original = next(item.content_bytes().decode() for item in
                        self.responses[request.attempt_id].proposed_workspace_artifacts if item.path == "experiment.py")
        original = original.split("\nresult = ", 1)[1]
        original = "result = " + original
        cuda = (
            "    import torch\n    assert torch.cuda.is_available() and torch.cuda.device_count() == 1\n"
            "    value = torch.tensor([1, 2, 3], device='cuda').sum().item()\n"
            "    torch.cuda.synchronize()\n    assert value == 6\n"
            "    print('NATIVE_SHARED_CUDA ' + json.dumps({'uuid': os.environ['CUDA_VISIBLE_DEVICES'], 'sum': value}), flush=True)\n"
        ) if self.gpu else ""
        prefix = (
            "import json, os, sys, time\nfrom pathlib import Path\n"
            f"# Private native tool context: {self.nonces[request.role]}\n"
            f"assert 1 == 2, {_FAIL!r}\n"
            "data_root = Path(json.loads(os.environ['HYPOLOOM_READ_ROOTS'])[0])\n"
            "if '--local-check' not in sys.argv:\n" + cuda +
            "    until = time.monotonic() + 100\n"
            "    while not (data_root / 'release').exists() and time.monotonic() < until: time.sleep(0.01)\n"
            "    assert (data_root / 'release').exists(), 'both actual Processes were never observed'\n"
            + ("    time.sleep(90)\n" if request.role == PEER else "")
        )
        # The ordinary result envelope is unchanged. Its metric is calculated
        # from admitted validation bytes, not a fabricated accepted fact.
        measured = (
            "pairs = json.loads((data_root / 'validation-pairs.json').read_text())\n"
            f"predictions = pairs[{request.role!r}]\n"
            "accuracy = sum(a == b for a, b in zip(pairs['labels'], predictions)) / len(pairs['labels'])\n"
            "for trial in result['trials']: trial['metrics']['accuracy'] = accuracy\n"
        )
        original = original.replace("result_path.write_text(", measured + "result_path.write_text(")
        return prefix + original + f"\nprint({_PASS!r})\n"

    def tool_calls(self, number):
        if self.errors or self.current is None:
            return ()
        request = self.current
        step = self.steps[request.attempt_id]
        command = request.command
        calls = ()
        if command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            if step == 1:
                calls = (("design_write", "Write", {"file_path": "/workspace/experiment.py", "content": self._source(request)}),)
            elif step in {2, 4}:
                calls = (("local_fail" if step == 2 else "local_pass", "Bash", {
                    "command": "HYPOLOOM_RESULT_PATH=/tmp/local-check.json python3 /workspace/experiment.py --local-check",
                    "timeout": 10000}),)
            elif step == 3:
                calls = (("design_repair", "Edit", {"file_path": "/workspace/experiment.py",
                    "old_string": "assert 1 == 2", "new_string": "assert 1 == 1"}),)
            elif request.role == PEER and step == 5:
                # Finish design only after the winner is actually running, so
                # the later EXECUTION request freezes a fresh resource view.
                script = ("import json,os,time\nfrom pathlib import Path\n"
                    "path=Path(json.loads(os.environ['HYPOLOOM_READ_ROOTS'])[0])/'winner-running'\n"
                    "until=time.monotonic()+90\n"
                    "while not path.exists() and time.monotonic()<until: time.sleep(.05)\nassert path.exists()\n")
                calls = (("await_winner", "Bash", {"command": "python3 - <<'PY'\n" + script + "PY", "timeout": 100000}),)
        elif command is AgentCommand.EXPERIMENT_EXECUTION:
            if step == 1:
                calls = (("submit", "mcp__host-tools__submit_experiment", {
                    "candidate": self.designs[request.role], "gpus": 1 if self.gpu else 0}),)
            elif request.role not in self.waited:
                calls = ((f"wait_{step}", "mcp__host-tools__wait_experiment", {
                    "exp_id": self.experiments[request.role], "max_wait_seconds": 5}),)
        elif command is AgentCommand.FINAL_REPORT:
            calls = self.reports[request.attempt_id].tool_calls(number)
        if not calls:
            calls = (("structured", "StructuredOutput", self._structured(self.responses[request.attempt_id])),)
        scoped = tuple((self._prefix(request) + identity, name, arguments) for identity, name, arguments in calls)
        ids = {item[0] for item in scoped}
        assert len(ids) == len(scoped) and not self.issued.intersection(ids)
        self.issued.update(ids)
        return scoped


def _payload(root, *, gpu_uuids=(), runtime=None):
    payload = fake_payload(root)
    contract = payload["research_contract"]
    settings = contract["methodology"]["controls"]["product"]
    settings["experts"]["defaults"].update(model=_MODEL, effort="high")
    for expert in settings["experts"]["experts"]:
        expert["stage_overrides"] = {}
    contract["budget"].update(experiment_timeout=120, max_retries=1,
                              max_infrastructure_retries=1, max_process_lost_retries=0)
    settings["safety"]["filesystem"]["protected_paths"] = [".git/"]
    settings["notify"] = {"sinks": {}, "events": {}}
    if gpu_uuids:
        settings["safety"].update(gpus=list(gpu_uuids), max_gpus_per_experiment=1)
        contract["runtime"]["conda_env"] = str(runtime)
        contract["budget"].update(min_vram_per_experiment=512, gpu_vram_reserve_mb=512)
        payload["execution"].update(allow_gpu=True, runtime_prefix=str(runtime))
    data = Path(payload["execution"]["read_roots"][0])
    (data / "validation-pairs.json").write_text(json.dumps({
        "labels": [1] * 20, WINNER: [1] * 11 + [0] * 9, PEER: [1] * 10 + [0] * 10}))
    (root / "payload.json").write_text(json.dumps(payload))
    return payload


def _child_main():
    parameters = json.loads(sys.stdin.read())
    root = Path(parameters["root"])
    gpu_uuids = tuple(parameters["gpu_uuids"])
    if gpu_uuids:
        resources = LocalComputeResources(allowed_gpu_uuids=gpu_uuids,
                                          gpu_runtime_prefix=Path(parameters["runtime"]))
        host = _gpu_host(root, Path(parameters["executable"]), parameters["base_url"], parameters["key"], resources)
    else:
        host = _new_host(root, Path(parameters["executable"]), parameters["base_url"], parameters["key"])
        host.grounding_web = _NoPublicWeb()
    payload = json.loads((root / "payload.json").read_text())
    data = Path(payload["execution"]["read_roots"][0])
    owners = {component["id"]: component["owner"] for component in payload["research_contract"]["components"]}
    crash = host._crash

    def observe_goal(point):
        if point == "after_goal_verdict_commit":
            live = [row for row in host.store.list_attempts() if row["kind"] == "PROCESS" and row["state"] == "OPEN"]
            assert len(live) == 1 and process_group_exists(live[0]["process_group_id"])
            head = host.store.latest_transition(RUN)
            assert head["state"] == "STOPPING"
            (root / "goal-with-live-peer.json").write_text(json.dumps({"head": head, "peer": live[0]}, indent=2))
        crash(point)

    host._crash = observe_goal
    try:
        host.initialize()
        if parameters["create"]:
            created = host.handle(ControlRequest(1, "create-native-shared", 0, "CREATE_RUN",
                json.loads((root / "payload.json").read_text())))
            assert created.status == "accepted", created
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            host.tick()
            attempts = host.store.list_attempts()
            live = [row for row in attempts if row["kind"] == "PROCESS" and row["state"] == "OPEN"]
            for row in live:
                spec = host.store.find_fact(RUN, "ExperimentPreregistered", row["experiment_id"])
                if owners[spec["payload"]["component_id"]] == WINNER and not (data / "winner-running").exists():
                    assert process_group_exists(row["process_group_id"])
                    if gpu_uuids:
                        records = _cuda_records(row, host.store)
                        if not records:
                            continue
                        assert len(records) == 1 and records[0]["uuid"] in gpu_uuids
                    (data / "winner-running").write_text("the actual winner Process is OPEN\n")
            if len(live) == 2 and not (data / "release").exists():
                assert all(process_group_exists(row["process_group_id"]) for row in live)
                cuda = []
                if gpu_uuids:
                    for row in live:
                        cuda.extend(_cuda_records(row, host.store))
                    if len(cuda) != 2:
                        continue
                    assert {record["uuid"] for record in cuda} == set(gpu_uuids)
                (root / "parallel-processes.json").write_text(json.dumps({"attempts": live, "cuda": cuda}, indent=2))
                (data / "release").write_text("both actual Process groups and requested CUDA contexts observed\n")
            if parameters["crash_final"] and any(handle.request.command is AgentCommand.FINAL_REPORT
                                                   for handle in host.running_agents.values()):
                assert not host.running
                report = next(row for row in attempts if row["kind"] == "AGENT" and row["state"] == "OPEN"
                              and json.loads(row["observation_json"])["request"]["command"] == "FINAL_REPORT")
                (root / "final-open-before-crash.json").write_text(json.dumps(report, indent=2))
                os._exit(86)
            if host.store.latest_transition(RUN)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(0.01)
        assert host.store.latest_transition(RUN)["state"] == "COMPLETED", host.store.list_attempts()
    finally:
        host.close()


def _cuda_records(row, store=None):
    if row["state"] == "OPEN":
        # A live Process has no terminal observation yet. Its exact request
        # was already committed with the execution intent before spawn.
        intents = store.inspect(
            "SELECT payload_json FROM workflow_transitions WHERE workflow_id=? "
            "AND json_extract(payload_json,'$.attempt_id')=? ORDER BY revision DESC LIMIT 2",
            (row["workflow_id"], row["attempt_id"]))
        assert len(intents) == 1, "live Process must have one persisted execution intent"
        request = json.loads(intents[0]["payload_json"])["process_request"]
    else:
        request = json.loads(row["observation_json"])["process_request"]
    log = Path(request["cwd"]) / request["stdout_path"]
    return [json.loads(line.split(" ", 1)[1]) for line in log.read_text().splitlines()
            if line.startswith("NATIVE_SHARED_CUDA ")] if log.exists() else []


def _run_child(parameters, log, scenario):
    script = "import sys; sys.path.insert(0," + repr(str(Path(__file__).parent)) + "); from test_native_shared_goal_stop_g3 import _child_main; _child_main()"
    with log.open("wb") as output:
        child = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE,
                                 stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            child.stdin.write(json.dumps(parameters).encode())
            child.stdin.close()
            deadline = time.monotonic() + 330
            while child.poll() is None and time.monotonic() < deadline:
                if scenario.errors:
                    # A known scripted-provider failure must not consume the
                    # remaining node retry windows. SIGINT unwinds Host.close.
                    child.send_signal(signal.SIGINT)
                    child.wait(timeout=15)
                    break
                time.sleep(0.05)
            assert child.poll() is not None, "bounded native Host child timed out"
            return child.returncode
        finally:
            if child.poll() is None:
                child.send_signal(signal.SIGINT)
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)


def _rows(root, query):
    with sqlite3.connect((root / "state/state.sqlite").as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(query)]


def _transcript_publication(row, observation):
    session = observation["session"]
    publication = session.get("transcript")
    if publication is None:
        assert row["state"] == "CLOSED" and row["terminal_reason"] == "LOST"
        assert session.get("archive_after_loss") is True and session.get("transcript_missing") is True
    return publication


def _assert_closed(root, scenario, *, restart, key, gpu_uuids):
    attempts = _rows(root, "SELECT * FROM execution_attempts ORDER BY started_at")
    (root / "attempts.json").write_text(json.dumps(attempts, indent=2))
    assert all(row["state"] == "CLOSED" for row in attempts)
    processes = [row for row in attempts if row["kind"] == "PROCESS"]
    assert Counter(row["terminal_reason"] for row in processes) == {"SUCCEEDED": 1, "CANCELLED": 1}
    assert all(row["process_group_id"] and not json.loads(row["observation_json"]).get("synthetic_before_spawn") for row in processes)
    assert (root / "goal-with-live-peer.json").is_file() and (root / "parallel-processes.json").is_file()
    agents = [(row, json.loads(row["observation_json"])) for row in attempts if row["kind"] == "AGENT"]
    assert scenario.local_passes == {WINNER, PEER}
    assert set(scenario.experiments) == {WINNER, PEER} and len(set(scenario.experiments.values())) == 2
    forbidden = {"ADJUDICATE", "HYPOTHESIS_UPDATE", "CYCLE_REPORT", "SELF_EVOLVE", "OBSERVER", "DEBATE", "DEBATE_JUDGE", "GRAPH_EVOLVE"}
    assert not forbidden.intersection(o["request"]["command"] for _, o in agents)
    analysis = [(row, o) for row, o in agents if o["request"]["command"] == "RESULT_ANALYSIS"]
    assert len(analysis) == 1 and analysis[0][0]["terminal_reason"] == "SUCCEEDED"
    assert analysis[0][1]["response"]["typed_candidate"]["iteration_disposition"] == "ITERATE"
    sessions = {}
    for role in (WINNER, PEER):
        inner = [(row, o) for row, o in agents if o["request"]["role"] == role
                 and o["request"]["command"] in {command.value for command in _INNER}]
        assert all(o["request"]["task_context"]["key"]["experiment_ordinal"] == 1 for _, o in inner)
        sessions[role] = inner[0][1]["session"]["session_id"]
        assert len({o["session"]["session_id"] for _, o in inner}) == 1
        assert "--fork-session" in inner[0][1]["session"]["launch_arguments"]
        assert all(o["session"]["launch_arguments"] == ["--resume", sessions[role]] for _, o in inner[1:])
    assert len(set(sessions.values())) == 2
    reports = [(row, o) for row, o in agents if o["request"]["command"] == "FINAL_REPORT"]
    assert Counter(row["terminal_reason"] for row, _ in reports) == ({"LOST": 1, "SUCCEEDED": 1} if restart else {"SUCCEEDED": 1})
    if restart:
        assert len({row["command_id"] for row, _ in reports}) == 1
        assert len({observation["session"]["session_id"] for _, observation in reports}) == 1
        assert reports[0][1]["request"]["input_artifacts"] == reports[1][1]["request"]["input_artifacts"]
        assert (root / "final-open-before-crash.json").is_file()
    assert all(row["ended_at"] <= reports[0][0]["started_at"] for row in processes)
    artifacts = root / "state/artifacts"
    publications = 0
    for row, observation in agents:
        publication = _transcript_publication(row, observation)
        if publication is None:
            assert restart and observation["request"]["command"] == "FINAL_REPORT"
            # The Host can die before CC writes its first session byte. Its
            # retry keeps the UUID but legitimately starts with --session-id.
            continue
        publications += 1
        archive = artifacts / publication["path"]
        transcript = (archive / "transcript.jsonl").read_bytes()
        assert key.encode() not in transcript
        if row["terminal_reason"] == "SUCCEEDED":
            assert observation["session"]["transcript_complete"]
        if observation["request"]["command"] == "EXPERIMENT_EXECUTION":
            assert b"mcp__host-tools__submit_experiment" in transcript
            assert b"mcp__host-tools__wait_experiment" in transcript
        if observation["request"]["command"] == "FINAL_REPORT" and row["terminal_reason"] == "SUCCEEDED":
            report = scenario.reports[row["attempt_id"]]
            assert len(report.evidence) == 4
            assert (archive / "FINAL_REPORT.md").read_bytes() == report.report.encode()
            assert WorkspaceArtifact.from_dict(observation["response"]["proposed_workspace_artifacts"][0]).content_bytes() == report.report.encode()
    creations = _rows(root, "SELECT * FROM workflow_transitions WHERE workflow_kind='EXPERIMENT' AND revision=1")
    assert len(creations) == len({json.loads(row["payload_json"])["source_submit_command_id"] for row in creations}) == 2
    audit = Oracle(root / "state/state.sqlite", artifacts).audit(RUN)
    (root / "oracle.json").write_text(audit.to_json())
    assert audit.ok and audit.canonical_outcome_count == 2, audit.to_dict()
    assert audit.artifact_counts["TRANSCRIPT"] == publications
    assert audit.open_attempt_count == audit.pending_effect_count == 0 and not audit.owned_process_residue
    assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"])
    (root / "closure.json").write_text(json.dumps({"agent_terminal_counts": dict(Counter(row["terminal_reason"] for row, _ in agents)),
        "process_terminal_counts": dict(Counter(row["terminal_reason"] for row in processes)),
        "sessions": sessions, "gpu_uuids": gpu_uuids, "restart": restart, "oracle_ok": audit.ok,
        "transcript_count": publications,
        "report_launches": [{"terminal_reason": row["terminal_reason"],
            "session_id": observation["session"]["session_id"],
            "arguments": observation["session"].get("launch_arguments"),
            "transcript_missing": observation["session"].get("transcript_missing", False)}
            for row, observation in reports]}, indent=2))


def _run_native(root, *, restart, gpu_uuids=(), runtime=None):
    executable = _installed_claude()
    _payload(root, gpu_uuids=gpu_uuids, runtime=runtime)
    settings = root / "settings.json"
    settings.write_text("{}\n")
    settings.chmod(0o600)
    scenario = _SharedScenario(root, gpu=bool(gpu_uuids))
    key = "host-only-native-shared-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    parameters = {"root": str(root), "executable": str(executable), "key": key,
        "base_url": f"http://127.0.0.1:{provider.server_port}", "gpu_uuids": gpu_uuids,
        "runtime": str(runtime) if runtime else None, "create": True, "crash_final": restart}
    try:
        code = _run_child(parameters, root / "initial-host.log", scenario)
        assert not scenario.errors, scenario.errors
        assert code == (86 if restart else 0), (root / "initial-host.log").read_text()
        if restart:
            code = _run_child({**parameters, "create": False, "crash_final": False}, root / "recovered-host.log", scenario)
            assert not scenario.errors, scenario.errors
            assert code == 0, (root / "recovered-host.log").read_text()
        assert not scenario.errors, scenario.errors
        _assert_closed(root, scenario, restart=restart, key=key, gpu_uuids=gpu_uuids)
    finally:
        (root / "provider-requests.json").write_text(json.dumps(scenario.requests, indent=2))
        (root / "provider-errors.json").write_text(json.dumps(scenario.errors, indent=2))
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("restart", [False, True], ids=["normal", "report-crash"])
def test_native_shared_goal_cancels_live_peer_and_finishes(tmp_path, restart):
    _run_native(tmp_path, restart=restart)


@pytest.mark.parametrize("gpu", [False, True])
def test_native_shared_source_calculates_each_trial_and_local_check_never_uses_cuda(tmp_path, monkeypatch, gpu):
    scenario = _SharedScenario(tmp_path, gpu=gpu)
    request = SimpleNamespace(role=WINNER, attempt_id="offline-source")
    original = ("import torch\nresult = {'trials': [{'metrics': {'accuracy': 0}}]}\n"
                "result_path = Path(os.environ['HYPOLOOM_RESULT_PATH'])\n"
                "result_path.write_text(json.dumps(result))\n")
    scenario.responses[request.attempt_id] = SimpleNamespace(
        proposed_workspace_artifacts=(WorkspaceArtifact.from_bytes("experiment.py", original.encode()),))
    (tmp_path / "validation-pairs.json").write_text(json.dumps({"labels": [1] * 20, WINNER: [1] * 11 + [0] * 9}))
    monkeypatch.setenv("HYPOLOOM_READ_ROOTS", json.dumps([str(tmp_path)]))
    monkeypatch.setenv("HYPOLOOM_RESULT_PATH", str(tmp_path / "result.json"))
    monkeypatch.setattr(sys, "argv", ["experiment.py", "--local-check"])
    source = scenario._source(request)
    with pytest.raises(AssertionError, match=_FAIL):
        exec(compile(source, "experiment.py", "exec"), {})
    namespace = {}
    exec(compile(source.replace("assert 1 == 2", "assert 1 == 1"), "experiment.py", "exec"), namespace)
    assert json.loads((tmp_path / "result.json").read_text())["trials"][0]["metrics"]["accuracy"] == .55
    assert "torch" not in namespace


@pytest.mark.parametrize("state,log_ready", [("OPEN", False), ("OPEN", True), ("CLOSED", True)])
def test_native_shared_cuda_records_use_exact_persisted_process_request(tmp_path, state, log_ready):
    request = {"cwd": str(tmp_path), "stdout_path": "stdout.log"}
    record = {"uuid": "GPU-offline-fixture", "sum": 4}
    if log_ready:
        (tmp_path / "stdout.log").write_text("ordinary output\nNATIVE_SHARED_CUDA " + json.dumps(record) + "\n")
    row = {"state": state, "workflow_id": "experiment-1", "attempt_id": "process-1",
           "observation_json": None if state == "OPEN" else json.dumps({"process_request": request})}
    # Only the existing intent columns needed by the read query; no Host,
    # scientific run, CUDA context or provider is started in this helper test.
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE workflow_transitions (workflow_id TEXT, revision INTEGER, payload_json TEXT)")
        for workflow, attempt, source in (
            ("experiment-1", "process-1", request),
            ("experiment-1", "another-process", {}),
            ("another-experiment", "process-1", {}),
        ):
            connection.execute("INSERT INTO workflow_transitions VALUES (?, 1, ?)",
                (workflow, json.dumps({"attempt_id": attempt, "process_request": source})))

        def inspect(sql, parameters):
            assert state == "OPEN", "CLOSED Process uses its complete observation"
            return tuple(dict(item) for item in connection.execute(sql, parameters))

        assert _cuda_records(row, SimpleNamespace(inspect=inspect)) == ([record] if log_ready else [])


def test_native_shared_final_sources_decode_nested_stop_control_json():
    context = {"facts": [{"payload_json": '{"verdict":"GOAL_MET"}'}],
               "stop_reason": 'Actual stop controls: [{"stop_disposition":"COMPLETED"}]'}
    text = "1\t" + json.dumps(context) + "\n<system-reminder>metadata</system-reminder>"
    assert _read_json(text) == context
    with pytest.raises(AssertionError, match="exactly one"):
        _read_json(text + "\n" + json.dumps(context))


@pytest.mark.parametrize("changed", [None, "state", "terminal_reason", "archive_after_loss", "transcript_missing"])
def test_native_shared_missing_transcript_requires_actual_archived_loss(changed):
    row = {"state": "CLOSED", "terminal_reason": "LOST"}
    observation = {"session": {"transcript": None, "archive_after_loss": True, "transcript_missing": True}}
    if changed in row:
        row[changed] = "OPEN" if changed == "state" else "SUCCEEDED"
    elif changed:
        observation["session"][changed] = False
    if changed:
        with pytest.raises(AssertionError):
            _transcript_publication(row, observation)
    else:
        assert _transcript_publication(row, observation) is None


@pytest.mark.skipif(os.environ.get("HYPOLOOM_REAL_GPU") != "1"
    or os.environ.get("HYPOLOOM_REAL_NATIVE_GPU") != "1",
    reason="requires explicit native GPU opt-in and two exact authorized UUIDs")
def test_native_shared_goal_two_gpu_stop_and_report_recovery(tmp_path):
    first, runtime = _configured_gpu()
    second = os.environ.get("HYPOLOOM_REAL_GPU_SECOND_UUID")
    assert second and second == second.strip() and second.startswith("GPU-") and second != first
    for uuid in (first, second):
        _assert_gpu_idle(uuid)
    _run_native(tmp_path, restart=True, gpu_uuids=(first, second), runtime=runtime)
    for uuid in (first, second):
        _assert_gpu_idle(uuid)
