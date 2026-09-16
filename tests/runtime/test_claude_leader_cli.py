"""Native designated Leader memory mounts; not Observer/Host dispatch proof."""

import json
import secrets
from dataclasses import replace
from pathlib import Path

from hypoloom.agent_aci import EmbeddedInput, HypothesisSetCandidate, validate_postflight
from hypoloom.claude_agent_host import ClaudeAgentHost
from hypoloom.expert_memory import ExpertMemory

from test_agent_sessions import _manager
from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results
from test_expert_memory import _facts, _report
from test_full_cc_adapter import _invoke_adapter, _request as native_request
from test_full_cc_host_g3 import _start_provider
from test_leader_sessions import _leader_request


class _LeaderScenario:
    def __init__(self, nonce):
        self.nonce = nonce
        self.stage = "leader"
        self.stage_step = 0
        self.requests, self.errors = [], []
        self.structured_output = {}
        self.evidence = {}

    def record(self, document):
        self.requests.append(document)
        self.stage_step += 1
        try:
            step = self.stage_step
            previous = ({2: "read_peer_index", 3: "read_peer_round", 4: "leader_readonly"}
                        if self.stage == "leader" else {2: "ordinary_isolation"}).get(step)
            if previous:
                result = _tool_results(document)[previous]
                assert not result.get("is_error"), result
                self.evidence[previous] = _result_text(result)
                if previous.startswith("read_peer"):
                    assert self.nonce in self.evidence[previous]
            if self.stage == "ordinary":
                assert self.nonce not in json.dumps(document), "private peer-memory bytes leaked to an ordinary synthesis"
        except Exception as exc:
            self.errors.append(f"{self.stage}/{self.stage_step}: {type(exc).__name__}: {exc}")
        return len(self.requests)

    def tool_calls(self, number):
        if self.errors:
            return ()
        step = self.stage_step
        if self.stage == "leader" and step == 1:
            return (("read_peer_index", "Read", {"file_path": "/peer-memory/peer/MEMORY.md"}),)
        if self.stage == "leader" and step == 2:
            return (("read_peer_round", "Read", {"file_path": "/peer-memory/peer/rounds/1.md"}),)
        if self.stage == "leader" and step == 3:
            script = (
                "import errno, os\nfrom pathlib import Path\n"
                "assert os.statvfs('/workspace').f_flag & os.ST_RDONLY\n"
                "assert not Path('/state').exists() and not Path('/peer-memory/other').exists()\n"
                "for path in ['/peer-memory/peer/MEMORY.md', '/peer-memory/peer/rounds/1.md']:\n"
                f"    assert {self.nonce!r} in Path(path).read_text()\n"
                "    try:\n        fd = os.open(path, os.O_WRONLY | os.O_APPEND)\n"
                "    except OSError as error:\n        assert error.errno == errno.EROFS, (path, error)\n"
                "    else:\n        os.close(fd)\n        raise AssertionError('Leader can modify peer memory')\n"
                "print('LEADER_PEER_MEMORY_READONLY')\n"
            )
            return (("leader_readonly", "Bash", {"command": "python3 - <<'PY'\n" + script + "PY", "timeout": 10000}),)
        if self.stage == "ordinary" and step == 1:
            return (("ordinary_isolation", "Bash", {"command": "python3 - <<'PY'\nfrom pathlib import Path\nassert not Path('/peer-memory').exists()\nassert not Path('/state').exists()\nprint('NO_LEADER_PEER_MOUNT')\nPY", "timeout": 10000}),)
        output = self.structured_output
        if self.stage == "leader":
            first = output["typed_candidate"]["hypotheses"][0]
            output = {**output, "typed_candidate": {**output["typed_candidate"], "hypotheses": [
                {**first, "candidate": {**first["candidate"], "claim": first["candidate"]["claim"] + " Peer lesson: " + self.nonce}}]}}
        return ((self.stage + "_finish", "StructuredOutput", output),)


def test_native_leader_synthesis_reads_frozen_peer_reports_but_ordinary_synthesis_cannot(tmp_path: Path):
    executable = _installed_claude()
    manager = _manager(tmp_path)
    request, binding = _leader_request(manager)
    nonce = "native-peer-memory-" + secrets.token_hex(16)
    files = {"MEMORY.md": ("# Peer memory\n\n" + nonce + "\nSee rounds/1.md.\n").encode(),
        "rounds/1.md": _report() + ("\nObserved lesson " + nonce + "\n").encode()}
    memory = ExpertMemory(manager.state_root)
    accepted = memory.commit_report(request.run_id, "peer", binding["peer_memory_commits"]["peer"],
        "peer-report-1", files, cycle=1, facts=_facts(), max_bytes=8192)
    binding["peer_memory_commits"]["peer"] = accepted
    item = request.input_artifacts[0]
    request = replace(request, input_artifacts=(EmbeddedInput.from_bytes(item.ref, item.media_type,
        json.dumps(binding).encode()),))
    scenario = _LeaderScenario(nonce)
    key = "host-only-leader-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    adapter = ClaudeAgentHost(claude_executable=executable)
    environment = {"ANTHROPIC_API_KEY": key, "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{provider.server_port}"}
    metadata = []
    try:
        for stage in ("leader", "ordinary"):
            scenario.stage = stage
            selected = request
            if stage == "ordinary":
                task = replace(request.task_context, key=replace(request.task_context.key, component_id="primary.ordinary"))
                selected = replace(request, component_id=task.key.component_id, task_context=task,
                    command_id=task.command_id, input_artifacts=(), immutable_context_refs=())
            selected = native_request(selected, stage, None)
            workspace = manager.prepare(selected, None)
            assert (workspace.peer_memory_root is not None) is (stage == "leader")
            response, archived = _invoke_adapter(adapter, selected, workspace, scenario, environment, tmp_path / stage)
            candidate = validate_postflight(selected, response).candidate
            assert isinstance(candidate, HypothesisSetCandidate)
            assert (nonce in candidate.hypotheses[0].candidate.claim) is (stage == "leader")
            metadata.append(archived)
        assert metadata[0]["session_id"] != metadata[1]["session_id"]
        assert memory.files(request.run_id, "peer", accepted) == files
        assert len(scenario.requests) == 6 and len(scenario.evidence) == 4
        assert not scenario.errors
        for archived in metadata:
            transcript = manager.publisher.root / archived["transcript"]["path"] / "transcript.jsonl"
            assert key.encode() not in transcript.read_bytes()
        (tmp_path / "native-evidence.json").write_text(json.dumps(scenario.evidence, indent=2))
        (tmp_path / "peer-binding.json").write_text(json.dumps(binding, indent=2))
    finally:
        (tmp_path / "provider-requests.json").write_text(json.dumps(scenario.requests, indent=2))
        (tmp_path / "provider-errors.json").write_text(json.dumps(scenario.errors, indent=2))
        provider.shutdown(); provider.server_close(); thread.join(timeout=5)
