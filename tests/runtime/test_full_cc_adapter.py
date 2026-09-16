"""Production full-CC adapter with real CLI/bwrap and a local scripted provider.

This covers the adapter/session boundary, not WorkflowHost Experiment admission.
All repositories, artifacts and HTTP traffic are disposable and local.
"""

from __future__ import annotations

import json
import secrets
import shlex
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer
from pathlib import Path
from typing import Any, Mapping

import pytest

from hypoloom.agent_aci import (
    AgentBudget,
    AgentCommand,
    AgentRequest,
    AgentResponse,
    EmbeddedInput,
)
from hypoloom.agent_sessions import AgentSessions, PreparedSessionWorkspace
from hypoloom.artifacts import ArtifactPublisher
from hypoloom.claude_agent_host import (
    CLAUDE_AGENT_HOST_VERSION,
    CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
    AgentHostTerminalReason,
    ClaudeAgentHost,
    process_group_exists,
)
from hypoloom.execution_workspace import GitTargetBinding
from hypoloom.expert_loop import ResearchNodeSubkind
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.outcome_adapter import OUTCOME_ADAPTER_VERSION
from hypoloom.provider_bridge import LOCAL_PROVIDER_KEY

from test_claude_agent_host import (
    _claude_expert_design_request,
    _claude_pincer_request,
)
from test_claude_file_tool_cli import (
    _git,
    _handler_for,
    _installed_claude,
    _result_text,
    _tool_results,
)
from test_claude_session_cli import _MODEL, _SessionScenario


class _AdapterScenario(_SessionScenario):
    def __init__(self, hidden_paths: tuple[Path, ...]) -> None:
        super().__init__()
        self.structured_output: Mapping[str, Any] = {}
        self.upstream_authenticated: list[bool] = []
        self.hidden_paths = hidden_paths

    def record(self, document: Mapping[str, Any]) -> int:
        # The shared scenario verifies all four real Write/Bash/Edit outcomes.
        # Our additional design step verifies the production mount/env boundary.
        stage = self.stage
        if stage == "design" and self.stage_step >= 5:
            self.stage = "verification"
        number = super().record(document)
        self.stage = stage
        expected = (
            "isolation_checks"
            if stage == "design" and self.stage_step == 6
            else "readonly_checks"
            if stage == "analysis" and self.stage_step == 2
            else None
        )
        if expected is not None:
            try:
                result = _tool_results(document)[expected]
                assert result.get("is_error") is not True, result
                assert expected.upper() + "_PASSED" in _result_text(result), result
                self.tool_evidence[expected] = result
            except (AssertionError, KeyError) as exc:
                self.errors.append(f"{stage}/{self.stage_step}: {exc}")
        return number

    def _isolation_command(self, *, readonly: bool) -> str:
        source = (
            "import os\nfrom pathlib import Path\n"
            "assert 'ANTHROPIC_AUTH_TOKEN' not in os.environ\n"
            f"assert os.environ.get('ANTHROPIC_API_KEY') == {LOCAL_PROVIDER_KEY!r}\n"
            "assert 'HYPOLOOM_ADAPTER_HOST_ONLY' not in os.environ\n"
            f"assert all(not Path(p).exists() for p in {[str(p) for p in self.hidden_paths]!r})\n"
            "assert not Path('/dev/nvidia0').exists()\n"
            "try:\n    Path('/workspace/.git').write_text('not-a-git-owner')\n"
            "except OSError:\n    pass\n"
            "else:\n    raise AssertionError('Git metadata was writable')\n"
        )
        if readonly:
            source += (
                "assert 'return 42' in Path('/workspace/experiment.py').read_text()\n"
                "try:\n    Path('/workspace/analysis-must-not-write').write_text('no')\n"
                "except OSError:\n    pass\n"
                "else:\n    raise AssertionError('Analysis worktree was writable')\n"
            )
        source += f"print({'READONLY_CHECKS_PASSED' if readonly else 'ISOLATION_CHECKS_PASSED'!r})\n"
        return "python3 -c " + shlex.quote(source)

    def tool_calls(
        self, request_number: int
    ) -> tuple[tuple[str, str, Mapping[str, Any]], ...]:
        if self.stage == "design" and self.stage_step <= 4:
            return super().tool_calls(request_number)
        if self.stage == "design" and self.stage_step == 5:
            return (("isolation_checks", "Bash", {
                "command": self._isolation_command(readonly=False), "timeout": 10000,
            }),)
        if self.stage == "analysis" and self.stage_step == 1:
            return (("readonly_checks", "Bash", {
                "command": self._isolation_command(readonly=True), "timeout": 10000,
            }),)
        return (("structured_" + self.stage, "StructuredOutput", self.structured_output),)


def _request(base: AgentRequest, stage: str, nonce: str | None) -> AgentRequest:
    inputs = base.input_artifacts
    if stage == "design":
        # The shared schema fixture deliberately retains evaluator /7 for its
        # compatibility assertion. This native current-adapter run uses the
        # current FakeAgent/evaluator, so bind that exact version in its input.
        current = []
        for item in inputs:
            if item.ref.endswith("--expert-config"):
                value = json.loads(item.content_bytes())
                value["required_experiment_spec"]["evaluator_version"] = OUTCOME_ADAPTER_VERSION
                item = EmbeddedInput.from_bytes(item.ref, item.media_type, json.dumps(value).encode())
            current.append(item)
        inputs = tuple(current)
    refs = base.immutable_context_refs
    if nonce is not None:
        context = EmbeddedInput.from_bytes(stage + "--context", "text/plain", nonce.encode())
        inputs = (*inputs, context)
        refs = (*refs, context.ref)
    return replace(
        base,
        attempt_id="adapter-" + stage,
        input_artifacts=inputs,
        immutable_context_refs=refs,
        tool_permission_profile=CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
        required_adapter_version=CLAUDE_AGENT_HOST_VERSION,
        required_model_identity=_MODEL,
        deadline=(datetime.now(timezone.utc) + timedelta(seconds=120)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        budget=AgentBudget(
            max_turns=12, max_tokens=100_000, max_output_bytes=65_536,
            wall_seconds=90, max_cost="1",
        ),
    )


def _invoke_adapter(
    adapter: ClaudeAgentHost,
    request: AgentRequest,
    workspace: PreparedSessionWorkspace,
    scenario: _AdapterScenario,
    provider_environment: Mapping[str, str],
    logs: Path,
    *,
    host_tool_handler=None,
) -> tuple[AgentResponse, dict[str, Any]]:
    generated = FakeAgent().invoke(request)
    assert isinstance(generated, AgentResponse)
    scenario.structured_output = {
        "typed_candidate": generated.typed_candidate,
        "declared_effects": [{"effect": item.effect} for item in generated.declared_effects],
    }
    scenario.stage_step = 0
    before_requests = len(scenario.requests)
    bindings = []

    def bind(binding: Any) -> None:
        assert binding.attempt_id == request.attempt_id
        assert process_group_exists(binding.process_group_id)
        assert len(scenario.requests) == before_requests  # Still at the launch gate.
        bindings.append(binding)

    handle = adapter.start(
        request, model=_MODEL, provider_environment=provider_environment,
        bind_process_group=bind, tool_workspace=workspace,
        host_tool_handler=host_tool_handler,
    )
    logs.mkdir(parents=True)
    try:
        # Preserve the adapter's own stream bytes before observation closes them.
        # CLI invocation, launch argv and response parsing remain production code.
        deadline = time.monotonic() + 70
        while handle.returncode is None and time.monotonic() < deadline:
            time.sleep(0.02)
        for name, stream in (
            ("stdout.jsonl", handle._stdout_file), ("stderr.log", handle._stderr_file)
        ):
            stream.seek(0)
            (logs / name).write_bytes(stream.read())
        assert handle.returncode is not None, f"adapter timed out; logs: {logs}"
        observation = adapter.wait(handle, timeout=5)
        assert observation.terminal_reason is AgentHostTerminalReason.SUCCEEDED, (
            observation, logs
        )
        assert observation.response is not None
        assert not scenario.errors, (scenario.errors, logs)
        metadata = workspace.finish_node()
        (logs / "node-metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
        )
        assert metadata["transcript_complete"] is True, (metadata, logs)
        assert len(bindings) == 1
        return observation.response, metadata
    finally:
        adapter.cleanup(handle)
        assert not process_group_exists(handle.process_group_id)
        assert workspace.resources == []


def test_full_cc_adapter_forks_repairs_and_resumes_readonly_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _installed_claude()
    monkeypatch.setenv("HYPOLOOM_ADAPTER_HOST_ONLY", "host-environment-canary")
    target = tmp_path / "target-checkout"
    target.mkdir()
    canary = target / "canary.py"
    original = b"TARGET_UNCHANGED = True\n"
    canary.write_bytes(original)
    _git(target, "init", "-q")
    _git(target, "add", "canary.py")
    _git(target, "-c", "user.name=Hypoloom Test", "-c", "user.email=test@invalid",
         "commit", "-qm", "disposable adapter target")
    base_commit = _git(target, "rev-parse", "HEAD")
    state_root = tmp_path / "state"
    state_root.mkdir()
    host_only = tmp_path / "host-only.txt"
    host_only.write_text("not mounted")
    publisher = ArtifactPublisher(state_root / "artifacts")
    sessions = AgentSessions(state_root, publisher)
    binding = GitTargetBinding(checkout=target, commit=base_commit)
    scenario = _AdapterScenario((target, state_root, host_only))
    host_key = "local-test-host-key-" + secrets.token_hex(16)
    base_handler = _handler_for(scenario)

    class AuthenticatedHandler(base_handler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib API
            authenticated = self.headers.get("Authorization") == "Bearer " + host_key
            scenario.upstream_authenticated.append(authenticated)
            if not authenticated:
                self.send_error(401, "missing local upstream authentication")
                return
            super().do_POST()

    server = HTTPServer(("127.0.0.1", 0), AuthenticatedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    adapter = ClaudeAgentHost(claude_executable=executable)
    provider_environment = {
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
        "ANTHROPIC_AUTH_TOKEN": host_key,
    }
    metadata: dict[str, dict[str, Any]] = {}
    responses: dict[str, AgentResponse] = {}
    workspaces: dict[str, PreparedSessionWorkspace] = {}
    seed_bytes = b""
    try:
        for stage, base, context in (
            ("seed", _claude_pincer_request(ResearchNodeSubkind.SYNTHESIS), scenario.parent_context),
            ("design", _claude_expert_design_request(), scenario.chain_context),
            ("analysis", default_request(AgentCommand.RESULT_ANALYSIS), None),
        ):
            scenario.stage = stage
            request = _request(base, stage, context)
            workspace = sessions.prepare(request, binding)
            workspaces[stage] = workspace
            responses[stage], metadata[stage] = _invoke_adapter(
                adapter, request, workspace, scenario, provider_environment,
                tmp_path / "adapter-logs" / stage,
            )
            if stage == "seed":
                seed_bytes = (workspace.session_root / f"{workspace.binding['session_id']}.jsonl").read_bytes()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    seed, design, analysis = (metadata[stage] for stage in ("seed", "design", "analysis"))
    assert design["parent_session_id"] == seed["session_id"]
    assert design["session_id"] == analysis["session_id"] != seed["session_id"]
    assert design["launch_arguments"] == [
        "--resume", seed["session_id"], "--fork-session", "--session-id", design["session_id"],
    ]
    assert analysis["launch_arguments"] == ["--resume", design["session_id"]]
    assert workspaces["seed"].writable is False
    assert workspaces["design"].writable is True
    assert workspaces["analysis"].writable is False
    assert workspaces["design"].worktree_root == workspaces["analysis"].worktree_root
    assert canary.read_bytes() == original
    assert _git(target, "rev-parse", "HEAD") == base_commit
    assert _git(target, "status", "--porcelain") == ""
    assert not (target / "experiment.py").exists()
    assert not (workspaces["analysis"].worktree_root / "analysis-must-not-write").exists()
    assert (workspaces["seed"].session_root / f"{seed['session_id']}.jsonl").read_bytes() == seed_bytes
    authored = {
        item.path: item.content_bytes()
        for item in responses["design"].proposed_workspace_artifacts
    }
    assert b"return 42" in authored["experiment.py"]
    assert b"experiment.py" in authored["implementation.patch"]
    assert len(scenario.requests) == 9
    assert scenario.upstream_authenticated == [True] * 9
    assert set(scenario.tool_evidence) == {
        "write_broken", "test_failed", "repair_source", "test_passed",
        "isolation_checks", "readonly_checks",
    }
    assert host_key not in json.dumps(scenario.requests)
    for stage in ("seed", "design", "analysis"):
        archive = publisher.root.joinpath(*metadata[stage]["transcript"]["path"].split("/")) / "transcript.jsonl"
        content = archive.read_text()
        assert host_key not in content
        assert host_key not in (tmp_path / "adapter-logs" / stage / "stdout.jsonl").read_text()
        if stage != "seed":
            assert "AssertionError: repair-required" in content
            assert "REPAIRED_TEST_PASSED" in content
    print(f"adapter evidence: {tmp_path / 'adapter-logs'}; archives: {publisher.root}")
