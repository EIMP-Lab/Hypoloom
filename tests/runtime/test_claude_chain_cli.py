"""Native CLI/session-only multi-chain proof; not WorkflowHost multi-chain G3."""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import threading
import time
from dataclasses import replace
from http.server import HTTPServer
from pathlib import Path
from typing import Any, Mapping

from hypoloom.agent_aci import AgentBudget
from hypoloom.agent_sessions import AgentSessions
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeSubkind
from hypoloom.full_cc_runtime import full_cc_argv, full_cc_environment
from hypoloom.provider_relay import ProviderRelay

from test_agent_aci import _pincer_request
from test_agent_sessions import _chain_request, _manager
from test_claude_file_tool_cli import _handler_for, _installed_claude, _result_text, _tool_results
from test_claude_session_cli import _MODEL, _SCHEMA
from test_git_authoring_workspace import make_target


class _Chains:
    def __init__(self):
        self.parent = "shared-synthesis-" + secrets.token_hex(12)
        self.nonces = {chain: "tool-private-" + secrets.token_hex(12) for chain in ("1", "chain-a", "chain-b")}
        self.stage = "seed"
        self.chain: str | None = None
        self.step = 0
        self.requests: list[Mapping[str, Any]] = []
        self.errors: list[str] = []
        self.completed: list[tuple[str, str | None]] = []

    def record(self, document):
        self.requests.append(document)
        self.step += 1
        try:
            messages = json.dumps(document["messages"])
            assert self.parent in messages
            for chain, nonce in self.nonces.items():
                if chain != self.chain or self.stage == "design" and self.step == 1:
                    assert nonce not in messages, (self.stage, self.chain, chain)
                elif self.stage != "seed":
                    assert nonce in messages, (self.stage, self.chain, "missing tool context")
            if self.stage != "seed" and self.step > 1:
                tool = "write_chain" if self.stage == "design" and self.step == 2 else "read_chain"
                result = _tool_results(document)[tool]
                assert not result.get("is_error"), result
                if tool == "read_chain":
                    assert self.nonces[self.chain] in _result_text(result)
            if self.stage in {"resume", "restart"}:
                assert "write_chain" in messages and "read_chain" in messages
        except (AssertionError, KeyError) as exc:
            self.errors.append(f"{self.stage}/{self.chain}/{self.step}: {exc}")
        return len(self.requests)

    def tool_calls(self, _number):
        if self.errors:
            return ()
        if self.stage == "design" and self.step == 1:
            return (("write_chain", "Write", {"file_path": "/workspace/chain.txt", "content": self.nonces[self.chain]}),)
        if self.stage != "seed" and self.step == (2 if self.stage == "design" else 1):
            script = ("python3 - <<'PY'\nfrom pathlib import Path\n"
                "text = Path('/workspace/chain.txt').read_text()\n"
                f"assert text == {self.nonces[self.chain]!r}\n"
                "print(text)\nPY")
            return (("read_chain", "Bash", {"command": script, "timeout": 10000}),)
        self.completed.append((self.stage, self.chain))
        return (("finish", "StructuredOutput", {"stage": self.stage, "ok": True}),)


def _invoke(tmp_path, executable, workspace, scenario, base_url, host_key):
    logs = tmp_path / (scenario.stage + "-" + (scenario.chain or "parent"))
    logs.mkdir()
    scenario.step = 0
    # Keep the socket basename short enough for pytest-xdist's worker directory.
    relay = ProviderRelay(logs / "p.sock", base_url=base_url,
        provider_environment={"ANTHROPIC_API_KEY": host_key}, model=_MODEL,
        max_request_bytes=4*1024*1024, max_response_bytes=1024*1024,
        max_requests=8, deadline_at=time.monotonic()+40)
    argv = full_cc_argv(bwrap=Path("/usr/bin/bwrap"), executable=executable,
        request=workspace.request, model=_MODEL, workspace=workspace,
        provider_socket=relay.socket_path, host_socket=None, output_schema=_SCHEMA, extra_paths=())
    (logs / "argv.json").write_text(json.dumps(argv))
    process = None
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=full_cc_environment(), start_new_session=True)
        prompt = scenario.parent if scenario.stage == "seed" else "Continue this selected chain using only its existing tool context."
        stdout, stderr = process.communicate(prompt.encode(), timeout=40)
        (logs / "stdout.jsonl").write_bytes(stdout)
        (logs / "stderr.txt").write_bytes(stderr)
        assert process.returncode == 0, (stdout.decode(), stderr.decode())
        assert not scenario.errors, scenario.errors
        result = next(json.loads(line) for line in stdout.splitlines() if json.loads(line).get("type") == "result")
        assert result.get("subtype") == "success", result
        assert result["structured_output"] == {"stage": scenario.stage, "ok": True}
        metadata = workspace.finish_node()
        assert metadata["transcript_complete"] is True
        (logs / "node-metadata.json").write_text(json.dumps(metadata, indent=2))
        transcript = workspace.session_root / f"{workspace.binding['session_id']}.jsonl"
        assert host_key.encode() not in transcript.read_bytes()
        return metadata
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=5)
        relay.close()
        workspace.cleanup()


def test_three_native_cli_chains_share_seed_but_never_tool_context_and_resume_after_restart(tmp_path: Path):
    executable = _installed_claude()
    _, target = make_target(tmp_path)
    sessions = _manager(tmp_path)
    scenario = _Chains()
    provider = HTTPServer(("127.0.0.1", 0), _handler_for(scenario))
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{provider.server_port}"
    key = "host-only-chain-key-" + secrets.token_hex(12)
    budget = AgentBudget(max_turns=8, max_tokens=20000, max_output_bytes=131072, wall_seconds=40, max_cost="1")
    try:
        seed = sessions.prepare(replace(_pincer_request(ResearchNodeSubkind.SYNTHESIS),
            attempt_id="native-chain-seed", budget=budget), target)
        _invoke(tmp_path, executable, seed, scenario, base_url, key)
        designs = {}
        for chain in scenario.nonces:
            scenario.stage, scenario.chain = "design", chain
            workspace = sessions.prepare(replace(_chain_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
                "native-design-" + chain, chain), budget=budget), target)
            designs[chain] = workspace
            assert workspace.binding["parent_session_id"] == seed.binding["session_id"]
            assert "--fork-session" in workspace.session_arguments
            _invoke(tmp_path, executable, workspace, scenario, base_url, key)
        assert len({item.binding["session_id"] for item in designs.values()}) == 3
        assert len({item.worktree_root for item in designs.values()}) == 3
        for stage in ("resume", "restart"):
            if stage == "restart":
                sessions = AgentSessions(sessions.state_root, sessions.publisher)
            for chain, design in designs.items():
                scenario.stage, scenario.chain = stage, chain
                request = replace(_chain_request(AgentCommand.RESULT_ANALYSIS,
                    f"native-{stage}-{chain}", chain, experiment=2), budget=budget,
                    attempt_ordinal=2 if stage == "restart" else 1)
                workspace = sessions.prepare(request, target)
                assert workspace.binding == design.binding
                assert workspace.session_arguments == ("--resume", design.binding["session_id"])
                assert workspace.worktree_root == design.worktree_root
                _invoke(tmp_path, executable, workspace, scenario, base_url, key)
                content = (workspace.session_root / f"{workspace.binding['session_id']}.jsonl").read_bytes()
                for other, nonce in scenario.nonces.items():
                    assert (nonce.encode() in content) is (other == chain)
        assert len(scenario.completed) == 10  # seed + three design/resume/restart cycles
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
