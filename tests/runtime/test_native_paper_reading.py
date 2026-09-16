"""Real CC/Host readers and CPU closure; the provider is an isolated local script."""
import json
import secrets
import shutil
import time
from dataclasses import replace
from pathlib import Path
import pytest

from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeTask
from hypoloom.grounding_web import GroundingWeb
from hypoloom.oracle import Oracle
from test_claude_file_tool_cli import _installed_claude, _tool_results, _result_text
from test_claude_final_report_g3 import _FinalReportHostScenario
from test_claude_multichain_g3 import _payload
from test_full_cc_host_g3 import _new_host, _start_provider
from test_grounding_web import DOC, Transport, _response


def _main(request):
    return (request.command is AgentCommand.LITERATURE_REVIEW and isinstance(request.task_context, ResearchNodeTask)
            and request.task_context.key.subkind.value == "MAIN")


class ReadingScenario(_FinalReportHostScenario):
    def __init__(self, root, *, batch_documents=False):
        super().__init__(root)
        self.batch_documents = batch_documents
        self.nonce = "method-page-" + secrets.token_hex(12)
        self.sources = {}; self.results = {}; self.readers = set()

    def record(self, document):
        number = super().record(document)
        request = self.current
        try:
            step = self.steps[request.attempt_id]
            results = _tool_results(document)
            if _main(request):
                if step == 2:
                    result = next(value for key, value in results.items() if key.endswith("__prepare_source"))
                    assert not result.get("is_error"), result
                    source = json.loads(_result_text(result))
                    self.sources[request.attempt_id] = source["documents"] if self.batch_documents else [source]
                if step >= 4:
                    result = next(value for key, value in results.items() if key.endswith(f"__reading_wait_{step - 1}_call"))
                    assert not result.get("is_error"), result
                    self.results[request.attempt_id] = json.loads(_result_text(result))
                    if self.results[request.attempt_id]["terminal"]:
                        assert len(self.results[request.attempt_id]["readings"]) == 3
                        assert all(item["status"] == "completed" and self.nonce in item["answer"]["summary"]
                            for item in self.results[request.attempt_id]["readings"])
                        response = self.responses[request.attempt_id]
                        self.responses[request.attempt_id] = replace(response, typed_candidate={**response.typed_candidate,
                            "summary": "Selected original methods were checked by three independent question readers: " + self.nonce,
                            "web_sources": [source["url"] for source in self.sources[request.attempt_id]]})
            elif request.command is AgentCommand.PAPER_READ and step >= 3:
                result = next(value for key, value in results.items() if key.endswith("__read_method"))
                assert not result.get("is_error"), result
                assert self.nonce in _result_text(result)
                self.readers.add(request.attempt_id)
                response = self.responses[request.attempt_id]
                source = json.loads(next(item for item in request.input_artifacts if item.ref.endswith("--paper-context")).content_bytes())["sources"][0]
                self.responses[request.attempt_id] = replace(response, typed_candidate={**response.typed_candidate,
                    "summary": f"Source {source['source_ref']}, physical page 1: exact observed method detail {self.nonce}. Interpretation is limited to the supplied independent question."})
        except Exception as exc: self.errors.append(f"paper provider step {number}: {type(exc).__name__}: {exc}")
        return number

    def _unscoped_tool_calls(self, number):
        request = self.current
        if request is None or self.errors: return ()
        step = self.steps[request.attempt_id]
        if request.command is AgentCommand.PAPER_READ:
            if step == 1: return (("read_index", "Read", {"file_path": "/papers/INDEX.md"}),)
            if step == 2: return (("read_method", "Read", {"file_path": "/papers/source-0001/pages.txt"}),)
            return self._structured(request)
        if _main(request):
            if step == 1:
                arguments = {"mode": "document", "sources": [{"url": DOC}, {"url": DOC + "/second"}]} if self.batch_documents else {"mode": "document", "url": DOC}
                return (("prepare_source", "mcp__host-tools__web_fetch", arguments),)
            if step == 2:
                return (("submit_readers", "mcp__host-tools__read_papers", {"action": "submit", "readings": [
                    {"reading_id": f"question-{i}", "paper_ref": self.sources[request.attempt_id][i % len(self.sources[request.attempt_id])]["source_ref"],
                     "question": f"Independent method question {i}: quote the exact coefficient label and state the limitation."} for i in range(3)]}),)
            if self.results.get(request.attempt_id, {}).get("terminal"): return self._structured(request)
            return ((f"reading_wait_{step}_call", "mcp__host-tools__read_papers", {"action": "wait", "max_wait_seconds": 10}),)
        return super()._unscoped_tool_calls(number)


@pytest.mark.parametrize("end_run,batch_documents", [(False, False), (True, False), (False, True)],
                         ids=["cpu-completion", "end-run", "batch-documents"])
def test_native_three_readers_original_sources_parent_collects_and_cpu_closes(tmp_path, end_run, batch_documents):
    scenario = ReadingScenario(tmp_path, batch_documents=batch_documents)
    secret = "isolated-script-only-" + secrets.token_hex(12)
    provider, thread = _start_provider(scenario, secret)
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{}\n"); settings_file.chmod(0o600)
    host = _new_host(tmp_path, _installed_claude(), f"http://127.0.0.1:{provider.server_port}", secret)
    host.grounding_web = GroundingWeb(Transport({url: _response(url,
        ("<title>Selected method</title><p>Coefficient label " + scenario.nonce + "</p>").encode())
        for url in (DOC, DOC + "/second")}))
    run_id = "native-paper-reading"
    max_readers = 0; stopped = False
    try:
        payload = _payload(tmp_path, run_id)
        contract = payload["research_contract"]
        product = contract["methodology"]["controls"]["product"]
        product["methodology"]["literature"]["tools"] = ["web_search", "web_fetch", "read_papers"]
        product["experts"]["defaults"]["max_hypotheses_per_round"] = 1
        product["methodology"]["inner_loop"]["max_iterations_per_hypothesis"] = 1
        contract["budget"]["max_active_agents"] = 4
        host.initialize()
        created = host.handle(ControlRequest(schema_version=1, request_id="create-reading", expected_revision=0,
            command="CREATE_RUN", payload=payload))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            host.tick()
            max_readers = max(max_readers, sum(handle.request.command is AgentCommand.PAPER_READ for handle in host.running_agents.values()))
            if end_run and not stopped and max_readers == 3:
                reply = host.handle(ControlRequest(schema_version=1, request_id="end-paper-test", expected_revision=None,
                    command="CANCEL", payload={"run_id": run_id, "cause": "bounded paper-reader End run test"}))
                assert reply.status == "accepted", reply
                stopped = True
            assert not scenario.errors, scenario.errors
            assert not host.dispatch_errors, host.dispatch_errors
            if host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}: break
            time.sleep(0.01)
        assert host.store.latest_transition(run_id)["state"] == ("CANCELLED" if end_run else "COMPLETED")
        assert max_readers == 3
        if not end_run: assert len(scenario.readers) == 3
        attempts = host.store.list_attempts()
        assert all(row["state"] == "CLOSED" for row in attempts)
        if not end_run: assert all(row["terminal_reason"] == "SUCCEEDED" for row in attempts)
        assert len([row for row in attempts if row["kind"] == "PROCESS"]) == (0 if end_run else 1)
        for row in attempts:
            observed = json.loads(row["observation_json"])
            if row["attempt_id"] in scenario.readers and not end_run:
                source = observed["session"]["paper_sources"][0]
                directory = host.publisher.root / observed["session"]["transcript"]["path"]
                assert scenario.nonce in (directory / "papers" / source["directory"] / "pages.txt").read_text()
        report = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
        (tmp_path / "oracle.json").write_text(json.dumps(report.to_dict(), indent=2))
        assert report.ok, report.to_dict()
        assert not host.running_agents and not host.running
        assert not list((host.state_root / "paper-staging").iterdir())
        assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"] is not None)
    finally:
        (tmp_path / "provider-errors.json").write_text(json.dumps(scenario.errors))
        (tmp_path / "reader-evidence.json").write_text(json.dumps({"max_readers": max_readers, "observed_readers": sorted(scenario.readers), "results": scenario.results}, indent=2))
        host.close()
        provider.shutdown(); provider.server_close(); thread.join(timeout=5)
        if (tmp_path / "admitted-target.txt").exists(): shutil.rmtree(Path((tmp_path / "admitted-target.txt").read_text().strip()))


@pytest.mark.parametrize("stage", ["execution", "readers"])
def test_native_quarantine_ends_calls_preserves_work_and_allows_explicit_cleanup(tmp_path, monkeypatch, stage):
    from test_claude_measurement_g3 import _NoPublicWeb

    class SlowProcessScenario(_FinalReportHostScenario):
        def _unscoped_tool_calls(self, number):
            calls = super()._unscoped_tool_calls(number)
            if self.current.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT and self.steps[self.current.attempt_id] == 1:
                identity, name, arguments = calls[0]
                return ((identity, name, {**arguments, "content": arguments["content"] + "\nimport time\ntime.sleep(3)\n"}),)
            return calls

    scenario = ReadingScenario(tmp_path) if stage == "readers" else SlowProcessScenario(tmp_path)
    secret = "isolated-quarantine-" + secrets.token_hex(12)
    provider, thread = _start_provider(scenario, secret)
    settings = tmp_path / "settings.json"; settings.write_text("{}\n"); settings.chmod(0o600)
    host = _new_host(tmp_path, _installed_claude(), f"http://127.0.0.1:{provider.server_port}", secret)
    host.grounding_web = (GroundingWeb(Transport({DOC: _response(DOC,
        ("<p>Method label " + scenario.nonce + "</p>").encode())})) if stage == "readers" else _NoPublicWeb())
    run_id = "native-quarantine-" + stage
    affected = {}; paused = False
    try:
        payload = _payload(tmp_path, run_id)
        contract = payload["research_contract"]
        product = contract["methodology"]["controls"]["product"]
        product["experts"]["defaults"]["max_hypotheses_per_round"] = 1
        product["methodology"]["inner_loop"]["max_iterations_per_hypothesis"] = 1
        if stage == "readers": product["methodology"]["literature"]["tools"] = ["web_search", "web_fetch", "read_papers"]
        contract["budget"]["max_active_agents"] = 4
        host.initialize()
        assert host.handle(ControlRequest(1, "create-quarantine", 0, "CREATE_RUN", payload)).status == "accepted"
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            host.tick()
            assert not scenario.errors, scenario.errors
            if stage == "readers":
                ready = sum(h.request.command is AgentCommand.PAPER_READ for h in host.running_agents.values()) == 3
            else:
                ready = bool(host.running) and any(h.request.command is AgentCommand.EXPERIMENT_EXECUTION
                    and scenario.steps[h.request.attempt_id] >= 2 for h in host.running_agents.values())
            if ready and not paused:
                def fail_dispatch(run):
                    raise ValueError("isolated native dispatch fault")
                monkeypatch.setattr(host, "_drive_active", fail_dispatch)
                host.tick()
                assert run_id in host.dispatch_errors
                # Polling precedes dispatch: readers accepted earlier in this tick
                # remain successful. Only calls live at the fault are interrupted.
                affected = {aid: h.process_group_id for aid, h in host.running_agents.items()}
                paused = True
            if paused and not host.running_agents and not host.running and not host.paper_reading.completions: break
            time.sleep(0.01)
        assert paused and affected
        assert not host.running_agents and not host.running and not host.paper_reading.completions
        closed = [host.store.get_attempt(aid) for aid in affected]
        assert all(row["state"] == "CLOSED" and row["terminal_reason"] == "FAILED" for row in closed)
        assert all(json.loads(row["observation_json"])["failure_code"] == "dispatch_paused" for row in closed)
        assert all(not process_group_exists(pgid) for pgid in affected.values())
        assert host.store.latest_transition(run_id)["state"] == "ACTIVE"
        assert not host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        if stage == "execution":
            processes = [row for row in host.store.list_attempts() if row["kind"] == "PROCESS"]
            assert len(processes) == 1 and processes[0]["terminal_reason"] == "SUCCEEDED"
            view = host._experiment_tool_view(run_id, processes[0]["workflow_id"], include_log=False)
            assert view["state"] == "EVALUATING" and view["status"] == "blocked" and not view["terminal"]
            assert not scenario.execution_results
        provider_count = len(scenario.requests)
        attempts_count = len(host.store.list_attempts())
        for _ in range(5): host.tick()
        assert len(scenario.requests) == provider_count and len(host.store.list_attempts()) == attempts_count
        assert host.handle(ControlRequest(1, "status-paused", None, "STATUS", {"run_id": run_id})).status == "accepted"
        assert host.handle(ControlRequest(1, "cancel-quarantine", None, "CANCEL",
            {"run_id": run_id, "cause": "explicit isolated test cleanup"})).status == "accepted"
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            host.tick()
            if host.store.latest_transition(run_id)["state"] == "CANCELLED": break
            time.sleep(0.01)
        assert host.store.latest_transition(run_id)["state"] == "CANCELLED"
        attempts = host.store.list_attempts()
        assert all(row["state"] == "CLOSED" for row in attempts)
        assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"] is not None)
        report = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
        (tmp_path / "oracle.json").write_text(json.dumps(report.to_dict(), indent=2))
        assert report.ok, report.to_dict()
        assert not host.running_agents and not host.running
        assert not list((host.state_root / "paper-staging").glob("*"))
    finally:
        (tmp_path / "quarantine-evidence.json").write_text(json.dumps({"stage": stage,
            "paused": paused, "affected": affected, "provider_errors": scenario.errors,
            "provider_steps": dict(scenario.steps)}, indent=2))
        host.close()
        provider.shutdown(); provider.server_close(); thread.join(timeout=5)
        if (tmp_path / "admitted-target.txt").exists(): shutil.rmtree(Path((tmp_path / "admitted-target.txt").read_text().strip()))
