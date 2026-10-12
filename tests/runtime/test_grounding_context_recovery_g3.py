"""Actual native long discovery, retained evaluator repair and CPU acceptance.

The local provider supplies choices, not a scientific-quality claim. Discovery
really reads files before a wire fault; recovery must inspect retained drafts
and pass the ordinary common parser/experiment/Oracle path.
"""
import copy
import json
import subprocess
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import yaml

from hypoloom.agent_aci import with_output_digest
from hypoloom.claude_agent_host import _output_schema
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.oracle import Oracle
from hypoloom.profile_admission import admit_profile
from hypoloom.provider_observation import read_exchanges
import hypoloom.context_management as context

from test_claude_file_tool_cli import _installed_claude, _tool_results, _result_text
from test_claude_measurement_g3 import _NoPublicWeb
from test_claude_shared_acceptance_g3 import (
    _AcceptanceScenario, _DECLARATION, _GOAL, _RUN, _BROKEN, _REPAIRED, _common_source, _payload,
)
from test_full_cc_host_g3 import _new_host, _request_from_provider, _start_provider


class _DiscoveryScenario(_AcceptanceScenario):
    discovery_count = 14

    def __init__(self, root):
        super().__init__(root, "retained-grounding-draft")
        self.grounding_attempts = []
        self.recovered_draft = False

    def record(self, document):
        request = _request_from_provider(document)
        if request.command is not AgentCommand.GROUNDING:
            return super().record(document)
        self.requests.append(document)
        self.current = request
        self.agent_requests[request.attempt_id] = request
        self.steps[request.attempt_id] += 1
        if request.attempt_id not in self.grounding_attempts:
            self.grounding_attempts.append(request.attempt_id)
        self.source_attempt_id = request.attempt_id
        response = self.reviser.invoke(request)
        self.responses[request.attempt_id] = with_output_digest(replace(response, typed_candidate={
            "task_kind": "GROUNDING", "supporting_refs": list(request.immutable_context_refs),
            "background": {"project_summary": "Inspected interfaces and repaired retained evaluator.",
                "codebase_map": [{"path": "discovery", "purpose": "Observed interface descriptions"}],
                "entrypoints": ["train.py"], "how_to_run_experiment": "Measure both suites using the common evaluator.",
                "metrics_available": [], "risks": [], "open_questions": []},
            "web_sources": [], "clarification": None, "acceptance": copy.deepcopy(_DECLARATION)}))
        results = _tool_results(document)
        try:
            if "inspect_retained" in results:
                text = _result_text(results["inspect_retained"])
                assert "DRAFT_RETAINED" in text and "next: repair denominator" in text
                self.recovered_draft = True
            if "repair_check" in results:
                assert _REPAIRED in _result_text(results["repair_check"])
            if "inspect_schema" in results:
                assert not results["inspect_schema"].get("is_error"), results["inspect_schema"]
                assert "SCHEMA_MATCHES_FROZEN_REQUEST" in _result_text(results["inspect_schema"])
        except AssertionError as exc:
            self.errors.append(str(exc))
        return len(self.requests)

    def tool_calls(self, number):
        request = self.current
        if request.command is not AgentCommand.GROUNDING:
            return super().tool_calls(number)
        step = self.steps[request.attempt_id]
        if len(self.grounding_attempts) == 1:
            if step <= self.discovery_count:
                return ((f"inspect_{step}", "Read", {"file_path": f"/workspace/discovery/interface-{step}.txt"}),)
            if step == self.discovery_count + 1:
                return (("draft_parser", "Write", {"file_path": "/acceptance/evaluate.py",
                    "content": _common_source(self.nonce, broken=True)}),)
            if step == self.discovery_count + 2:
                sample = {"suites": {name: {"candidate": "smoke", "labels": [0, 1, 0, 1],
                    "predictions": [0, 0, 0, 0]} for name in ("suite_one", "suite_two")}}
                return (("draft_notes", "Write", {"file_path": "/acceptance/WORK_NOTES.md",
                    "content": "Both suites use four labels. Interface discovery complete; next: repair denominator in evaluate.py; run work/test_parser.py.\n"}),
                    ("draft_smoke", "Write", {"file_path": "/acceptance/smoke/predictions.json", "content": json.dumps(sample)}),
                    ("draft_helper", "Write", {"file_path": "/acceptance/work/test_parser.py",
                        "content": "import runpy, sys\nsys.argv = ['evaluate.py', '--self-test']\nrunpy.run_path('/acceptance/evaluate.py', run_name='__main__')\n"}),
                    ("draft_candidate", "Write", {"file_path": "/acceptance/work/scientific-draft.json",
                        "content": json.dumps(self.responses[request.attempt_id].typed_candidate)}))
            if step == self.discovery_count + 3:
                return (("discovery_failure", "Bash", {"command": "python3 /acceptance/work/test_parser.py", "timeout": 10000}),)
            raise AssertionError("first exploration should close at the injected capacity boundary")
        assert len(self.grounding_attempts) == 2
        if step == 1:
            return (("inspect_retained", "Bash", {"command":
                "cat /acceptance/WORK_NOTES.md && test -f /acceptance/smoke/predictions.json && test -f /acceptance/work/scientific-draft.json && grep 'denominator =' /acceptance/evaluate.py && echo DRAFT_RETAINED",
                "timeout": 10000}),
                ("read_retained", "Read", {"file_path": "/acceptance/evaluate.py"}),
                ("inspect_schema", "Bash", {"command": "python3 - <<'PY'\nimport json\n"
                    "from pathlib import Path\n"
                    f"expected = json.loads({json.dumps(_output_schema(request, tool_workspace=True))!r})\n"
                    "assert json.loads(Path('/runtime/output-schema.json').read_text()) == expected\n"
                    "print('SCHEMA_MATCHES_FROZEN_REQUEST')\nPY", "timeout": 10000}))
        if step == 2:
            return (("repair_retained", "Edit", {"file_path": "/acceptance/evaluate.py",
                "old_string": "denominator = len(rows['labels']) + 1", "new_string": "denominator = len(rows['labels'])"}),)
        if step == 3:
            return (("repair_check", "Bash", {"command": "python3 /acceptance/work/test_parser.py", "timeout": 10000}),)
        return self._structured(request)


def test_native_long_grounding_rebuild_inspects_draft_then_closes_cpu_goal(tmp_path, monkeypatch):
    scenario = _DiscoveryScenario(tmp_path)
    provider, thread = _start_provider(scenario, "local-grounding-context")
    (tmp_path / "settings.json").write_text("{}")
    (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(tmp_path, _installed_claude(), f"http://127.0.0.1:{provider.server_port}", "local-grounding-context")
    host.grounding_web = _NoPublicWeb()
    original = context.request_capacity
    injected = []

    def capacity(document, policy, **kwargs):
        observed = original(document, policy, **kwargs)
        results = _tool_results(document)
        if not injected and "discovery_failure" in results:
            failure = results["discovery_failure"]
            assert failure.get("is_error") and _BROKEN in _result_text(failure)
            injected.append(_request_from_provider(document).attempt_id)
            return {**observed, "fits": False}
        return observed

    monkeypatch.setattr(context, "request_capacity", capacity)
    try:
        with tempfile.TemporaryDirectory(prefix="grounding-context-", dir="/var/tmp") as mount:
            _payload(tmp_path, Path(mount), roles=(_GOAL,))
            path = tmp_path / "profile/methodology.yaml"
            methodology = yaml.safe_load(path.read_text())
            methodology["budget"]["max_infrastructure_retries"] = 1
            target = Path(methodology["runtime"]["target_repo"])
            directory = target / "discovery"
            directory.mkdir()
            for number in range(1, scenario.discovery_count + 1):
                (directory / f"interface-{number}.txt").write_text(
                    f"Interface {number}: four fixed labels; evaluate both suites separately.\n" +
                    ("# Interface documentation: deterministic fixture records, preserve full-suite comparison and candidate identity. " * 2 + "\n") * 70)
            subprocess.run(["git", "add", "discovery"], cwd=target, check=True, capture_output=True)
            subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "commit", "-m", "Fixture interface documentation"], cwd=target, check=True, capture_output=True)
            path.write_text(yaml.safe_dump(methodology))
            admitted = admit_profile(tmp_path / "profile", run_id=_RUN, allow_gpu=False)
            assert admitted.admitted, admitted.to_dict()
            host.initialize()
            assert host.handle(ControlRequest(1, "create-grounding-context", 0, "CREATE_RUN", admitted.create_run_payload)).status == "accepted"
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                host.tick()
                assert not scenario.errors, scenario.errors
                if host.store.latest_transition(_RUN)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
                time.sleep(.01)
            assert host.store.latest_transition(_RUN)["state"] == "COMPLETED"
            assert injected == scenario.grounding_attempts[:1] and scenario.recovered_draft
            attempts = host.store.list_attempts()
            grounding = [json.loads(host.store.get_attempt(identity)["observation_json"]) for identity in scenario.grounding_attempts]
            assert len(grounding) == 2
            assert grounding[0]["session"]["session_id"] != grounding[1]["session"]["session_id"]
            assert host.store.get_attempt(scenario.grounding_attempts[1])["terminal_reason"] == "SUCCEEDED"
            prompt = json.loads((tmp_path / "state/diagnostics/agents" / _RUN / scenario.grounding_attempts[1] / "prompt.txt").read_text())
            continuation = prompt["continuation_observation"]
            assert continuation["source_attempt_id"] == injected[0]
            assert "/acceptance/evaluate.py" in json.dumps(continuation)
            assert _BROKEN in json.dumps(continuation)
            assert "WORK_NOTES.md" in json.dumps(continuation)
            assert "/acceptance/work/test_parser.py" in json.dumps(continuation)
            assert "/acceptance/work/scientific-draft.json" in json.dumps(continuation)
            first_exchanges = read_exchanges(tmp_path / "state/diagnostics/agents" / _RUN / injected[0] / "provider")
            # Reproduces the old byte-as-token false-positive shape. The complete
            # synthetic usage anchor allows a large wire request before injection.
            assert any(item.get("upstream_status") == 200 and
                (item.get("context_capacity") or {}).get("request_input_bytes", 0) > 205568
                for item in first_exchanges)
            outcomes = host.store.list_facts(_RUN, "CanonicalOutcomeAccepted")
            assert len(outcomes) == 1 and outcomes[0]["payload"]["goal_evaluation"]["checks"] == {
                "complete": True, "single_candidate": True}
            assert len([row for row in attempts if row["kind"] == "PROCESS"]) == 1
            assert all(row["state"] == "CLOSED" for row in attempts)
            audit = Oracle(host.store.database_path, host.publisher.root).audit(_RUN)
            assert audit.ok, audit.to_dict()
            assert audit.open_attempt_count == audit.pending_effect_count == 0
            assert not audit.owned_process_residue
    finally:
        host.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
