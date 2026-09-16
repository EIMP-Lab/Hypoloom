"""Actual two-party, three-round CC forks and promotion, not Host dispatch."""

import json
import re
import secrets
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4

from hypoloom.agent_aci import EmbeddedInput, required_output_schema, validate_postflight
from hypoloom.agent_sessions import AgentSessions
from hypoloom.claude_agent_host import ClaudeAgentHost
from hypoloom.domain import AgentCommand, _thaw_json
from hypoloom.expert_interaction import DebateBinding, DebateFork, read_debate_rounds
from hypoloom.expert_loop import AuxiliaryRequestKind, AuxiliaryTask, ResearchNodeSubkind
from hypoloom.fake_agent import default_request
from hypoloom.git_authoring_workspace import GitAuthoringWorkspace

from test_agent_aci import _pincer_request
from test_agent_sessions import _manager
from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results
from test_debate_sessions import _source_row
from test_expert_interaction import _debate
from test_full_cc_adapter import _invoke_adapter, _request as native_request
from test_full_cc_host_g3 import _start_provider
from test_git_authoring_workspace import make_target


def _json_input(ref, value):
    return EmbeddedInput.from_bytes(ref, "application/json", json.dumps(value).encode())


def _research_request(participant, stage, *, synthesis=False, cycle=1, inputs=()):
    scope = participant.scope
    base = _pincer_request(ResearchNodeSubkind.SYNTHESIS) if synthesis else default_request(AgentCommand.RESULT_ANALYSIS)
    task = replace(base.task_context, key=replace(base.task_context.key, run_id="run-1",
        expert_slot=scope.role, component_id=scope.component_id, cycle=cycle,
        chain=None if synthesis else scope.chain))
    if not synthesis:
        inputs = (*inputs, _json_input(stage + "--batch-context", {
            "chains": {scope.chain: participant.hypothesis_id}}))
    request = replace(base, schema_version=14, run_id="run-1", role=scope.role, max_input_bytes=4 * 1024 * 1024,
        component_id=scope.component_id, task_context=task, command_id=task.command_id,
        input_artifacts=tuple(inputs), immutable_context_refs=tuple(item.ref for item in inputs),
        required_output_schema=required_output_schema(base.command, task, schema_version=14))
    return native_request(request, stage, None)


class _DebateScenario:
    def __init__(self):
        self.requests, self.errors = [], []
        self.nonces, self.evidence = {}, {}
        self.stage_step = 0
        self.structured_output = {}

    def select(self, request, stage, *, previous=None, require_history=False):
        self.request, self.stage, self.previous = request, stage, previous
        self.require_history = require_history
        self.role = request.role

    def record(self, document):
        self.requests.append(document)
        self.stage_step += 1
        try:
            text = json.dumps(document["messages"])
            if self.stage_step == 1:
                if self.require_history:
                    assert self.nonces[self.role], "the test has no actual prior tool observation"
                    assert all(nonce in text for nonce in self.nonces[self.role]), "own actual tool history was not resumed"
                for role, nonces in self.nonces.items():
                    if role != self.role:
                        assert all(nonce not in text for nonce in nonces), "peer's private tool transcript leaked"
            if self.stage_step == 2:
                result = _tool_results(document)[self.stage + "__observe"]
                assert not result.get("is_error"), result
                value = _result_text(result)
                nonce = re.search(r"NATIVE_DEBATE_OBSERVATION_[a-f0-9]{32}", value).group()
                self.nonces.setdefault(self.role, []).append(nonce)
                self.evidence[self.stage] = {"observed_nonce": nonce, "tool_result": value}
            if self.stage_step == 3 and self.previous is not None:
                result = _tool_results(document)[self.stage + "__opponent"]
                assert not result.get("is_error"), result
                value = _result_text(result)
                assert self.previous["attempt_id"] in value
                assert "Scientific round position" in value
                self.evidence[self.stage]["opponent_result"] = value
        except Exception as exc:
            self.errors.append(f"{self.stage}/{self.stage_step}: {type(exc).__name__}: {exc}")
        return len(self.requests)

    def tool_calls(self, number):
        if self.errors:
            return ()
        if self.stage_step == 1:
            script = (
                "import errno, os, secrets\nfrom pathlib import Path\n"
                "assert not Path('/state').exists() and not Path('/peer-memory').exists()\n"
                "assert os.statvfs('/workspace').f_flag & os.ST_RDONLY\n"
                "try:\n    fd = os.open('/workspace/README.md', os.O_WRONLY | os.O_APPEND)\n"
                "except OSError as error:\n    assert error.errno == errno.EROFS\n"
                "else:\n    os.close(fd)\n    raise AssertionError('Debate exceeded read-only workspace')\n"
                "print('NATIVE_DEBATE_OBSERVATION_' + secrets.token_hex(16))\n"
            )
            return ((self.stage + "__observe", "Bash", {"command": "python3 - <<'PY'\n" + script + "PY", "timeout": 10000}),)
        if self.stage_step == 2 and self.previous is not None:
            index = next(index for index, item in enumerate(self.request.input_artifacts)
                if item.ref == self.previous["attempt_id"])
            return ((self.stage + "__opponent", "Read", {"file_path": f"/inputs/{index:04d}.txt"}),)
        output = self.structured_output
        if self.request.command is AgentCommand.DEBATE:
            candidate = {**output["typed_candidate"], "position": "Scientific round position " + self.stage,
                "evidence": ["out-1", "out-2"]}
            output = {**output, "typed_candidate": candidate}
        return ((self.stage + "__finish", "StructuredOutput", output),)


def test_native_debate_three_rounds_resume_forks_and_promote_winner_into_next_synthesis_and_inner(tmp_path: Path):
    executable = _installed_claude()
    _, target = make_target(tmp_path)
    manager = _manager(tmp_path)
    facts, template, _, _ = _debate()
    scenario = _DebateScenario()
    key = "host-only-debate-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    environment = {"ANTHROPIC_API_KEY": key, "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{provider.server_port}"}
    adapter = ClaudeAgentHost(claude_executable=executable)
    rows, metadata, originals, original_bytes, retired_worktrees = [], [], {}, {}, []

    def no_public_web(_attempt, tool, _arguments):
        raise AssertionError(f"native session-only probe must not perform web retrieval: {tool}")

    def invoke(request, stage, *, previous=None, history=False):
        # A new Host-side session manager each time must reconstruct from Git
        # and canonical actual CLI archives, never fabricated transcript rows.
        restarted = AgentSessions(manager.state_root, manager.publisher)
        workspace = restarted.prepare(request, target)
        scenario.select(request, stage, previous=previous, require_history=history)
        response, archived = _invoke_adapter(adapter, request, workspace, scenario, environment, tmp_path / stage,
            host_tool_handler=no_public_web)
        parsed = validate_postflight(request, response)
        row = _source_row(workspace, archived)
        observed = json.loads(row["observation_json"])
        observed["response"] = response.to_dict()
        row["observation_json"] = json.dumps(observed)
        rows.append(row); metadata.append(archived)
        return workspace, row, parsed

    try:
        forks = []
        for item in template.participants:
            participant = item.participant
            role = participant.scope.role
            seed = _research_request(participant, "seed-" + role, synthesis=True)
            invoke(seed, "seed-" + role)
            request = _research_request(participant, "original-" + role)
            workspace, row, _ = invoke(request, "original-" + role, history=True)
            originals[role] = row
            archived = metadata[-1]
            path = manager.publisher.root / archived["transcript"]["path"] / "transcript.jsonl"
            original_bytes[role] = (path, path.read_bytes())
            forks.append(DebateFork(participant, request.attempt_id, workspace.binding["session_id"], str(uuid4())))
            # Exercise the real retained-source consumer, without pretending
            # this session-only fixture supplies Host ADJ acceptance authority.
            repository = GitAuthoringWorkspace(workspace.scope_root / "source")
            assert repository.retire_worktree("chain", branch=workspace.binding["branch"])
            assert workspace.git_workspace.repository_root.is_dir()
            retired_worktrees.append(workspace.worktree_root)
        binding = DebateBinding("native-debate", "run-1", tuple(forks))
        rounds = {}
        for number in (1, 2, 3):
            for fork in binding.participants:
                role = fork.participant.scope.role
                peer = next(item.participant.scope.role for item in binding.participants if item is not fork)
                previous = None if number == 1 else rounds[(number - 1, peer)]
                context = binding.round_context(role, number, None if previous is None else previous["attempt_id"])
                inputs = (_json_input(binding.input_ref, context),
                    _json_input(fork.original_attempt_id, originals[role]),
                    _json_input("native--interaction-context", {"facts": _thaw_json(facts.rows)}))
                if previous is not None:
                    inputs += (_json_input(previous["attempt_id"], previous),)
                task = AuxiliaryTask(kind=AuxiliaryRequestKind.DEBATE, run_id="run-1", expert_slot=role,
                    component_id=fork.participant.scope.component_id, cycle=1, ordinal=51 + number)
                stage = f"round-{number}-{role}"
                base = default_request(AgentCommand.DEBATE)
                request = native_request(replace(base, schema_version=14, run_id="run-1", role=role, max_input_bytes=4 * 1024 * 1024,
                    component_id=task.component_id, task_context=task, command_id=task.command_id,
                    input_artifacts=inputs, immutable_context_refs=tuple(item.ref for item in inputs),
                    required_output_schema=required_output_schema(base.command, task, schema_version=14)), stage, None)
                workspace, row, parsed = invoke(request, stage, previous=previous, history=True)
                assert set(parsed.candidate.evidence) == {"out-1", "out-2"}
                expected = (["--resume", fork.original_session_id, "--fork-session", "--session-id", fork.session_id]
                    if number == 1 else ["--resume", fork.session_id])
                assert metadata[-1]["launch_arguments"] == expected
                assert workspace.binding["session_id"] == fork.session_id
                assert all(not path.exists() for path in retired_worktrees)
                rounds[(number, role)] = row
        assert len(read_debate_rounds(rounds.values(), binding=binding, facts=facts)) == 6

        judge_task = AuxiliaryTask(kind=AuxiliaryRequestKind.DEBATE_JUDGE, run_id="run-1", ordinal=17)
        context = _json_input("native--debate-judge-context", {"debate_id": binding.debate_id,
            "participants": [asdict(item) for item in binding.participants], "facts": _thaw_json(facts.rows),
            "round_attempts": list(rounds.values())})
        # Match the existing production Host request bound, including all six
        # actual source rows; default_request's small unit-fixture cap is not it.
        judge = native_request(replace(default_request(AgentCommand.DEBATE_JUDGE), run_id="run-1", max_input_bytes=4 * 1024 * 1024,
            task_context=judge_task, command_id=judge_task.command_id, input_artifacts=(context,),
            immutable_context_refs=(context.ref, *(row["attempt_id"] for row in rounds.values()))), "judge", None)
        _, judge_row, judgment = invoke(judge, "judge")
        winner = next(item for item in binding.participants if item.participant.scope.role == judgment.candidate.winner)
        source = rounds[(3, winner.participant.scope.role)]
        promotion = {"debate_id": binding.debate_id, "source_judge_attempt_id": judge_row["attempt_id"],
            "role": winner.participant.scope.role, "component_id": winner.participant.scope.component_id,
            "cycle": 2, "original_session_id": winner.original_session_id, "session_id": winner.session_id,
            "source_attempt_id": source["attempt_id"]}
        inputs = (_json_input("winner--session-promotion", promotion), _json_input(source["attempt_id"], source),
            _json_input("winner--lineage-context", {"cycle": 2, "parent_hypothesis_id": winner.participant.hypothesis_id}))
        request = _research_request(winner.participant, "winning-synthesis", synthesis=True, cycle=2, inputs=inputs)
        synthesis, _, _ = invoke(request, "winning-synthesis", history=True)
        assert metadata[-1]["launch_arguments"] == ["--resume", winner.session_id]
        assert synthesis.binding["session_id"] == winner.session_id
        request = _research_request(winner.participant, "winning-inner", cycle=2, inputs=inputs)
        inner, _, _ = invoke(request, "winning-inner", history=True)
        assert metadata[-1]["launch_arguments"] == ["--resume", winner.session_id, "--fork-session", "--session-id", inner.binding["session_id"]]
        assert inner.binding["session_id"] != winner.session_id
        assert all(not path.exists() for path in retired_worktrees)
        assert len(rows) == len(metadata) == 13
        assert not scenario.errors
        for path, content in original_bytes.values():
            assert path.read_bytes() == content
        for archived in metadata:
            path = manager.publisher.root / archived["transcript"]["path"] / "transcript.jsonl"
            assert key.encode() not in path.read_bytes()
            assert archived["transcript_complete"] is True
        (tmp_path / "native-evidence.json").write_text(json.dumps(scenario.evidence, indent=2))
        (tmp_path / "actual-attempt-rows.json").write_text(json.dumps(rows, indent=2))
        (tmp_path / "binding-and-promotion.json").write_text(json.dumps({"binding": asdict(binding), "promotion": promotion}, indent=2))
        (tmp_path / "retired-worktrees.json").write_text(json.dumps([str(path) for path in retired_worktrees], indent=2))
    finally:
        (tmp_path / "native-evidence.json").write_text(json.dumps(scenario.evidence, indent=2))
        (tmp_path / "actual-attempt-rows.json").write_text(json.dumps(rows, indent=2))
        (tmp_path / "provider-requests.json").write_text(json.dumps(scenario.requests, indent=2))
        (tmp_path / "provider-errors.json").write_text(json.dumps(scenario.errors, indent=2))
        provider.shutdown(); provider.server_close(); thread.join(timeout=5)
