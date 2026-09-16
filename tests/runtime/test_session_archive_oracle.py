"""Moving terminal working context preserves the existing canonical audit."""
import json
from dataclasses import replace

from hypoloom.agent_sessions import AgentSessions
from hypoloom.artifacts import ArtifactPublisher
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeSubkind
from hypoloom.oracle import _raw_artifact_digest
from hypoloom.session_archive import archive_run_sessions
from hypoloom.session_oracle import audit_sessions
from test_agent_aci import _pincer_request
from test_agent_sessions import _request, _transcript
from test_git_authoring_workspace import make_target
from test_session_archive import fixture


def test_actual_archive_keeps_canonical_session_audit_and_bytes_unchanged(tmp_path):
    root = fixture(tmp_path)
    target_root = tmp_path / "audit-target"
    target_root.mkdir()
    _, target = make_target(target_root)
    manager = AgentSessions(root, ArtifactPublisher(root / "artifacts"))
    rows, payloads = [], {}
    requests = (_pincer_request(ResearchNodeSubkind.SYNTHESIS),
                _request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, "design"))
    for index, request in enumerate(requests):
        task = replace(request.task_context, key=replace(request.task_context.key, run_id="archive-run"))
        request = replace(request, run_id="archive-run", command_id=task.command_id,
            task_context=task, required_adapter_version="claude-code-bwrap/36")
        prepared = manager.prepare(request, target)
        if index:
            (prepared.worktree_root / "experiment.py").write_text("print('actual Git history')\n")
        _transcript(prepared, "source node " + str(index))
        rows.append({"attempt_id": request.attempt_id, "kind": "AGENT", "state": "CLOSED",
            "terminal_reason": "SUCCEEDED", "started_at": index * 2, "ended_at": index * 2 + 1,
            "process_group_id": 12345})
        payloads[request.attempt_id] = {"request": request.to_dict(), "session_binding": prepared.binding,
            "session": prepared.finish_node()}
    canonical = {root / "artifacts" / observation["session"]["transcript"]["path"] / "transcript.jsonl":
        observation["session"]["transcript"]["digest"] for observation in payloads.values()}
    original_bytes = {path: path.read_bytes() for path in canonical}
    original_payloads = json.dumps(payloads, sort_keys=True)
    before = audit_sessions(root / "artifacts", rows, payloads, _raw_artifact_digest)
    assert before == ([], 2)
    assert (root / "archive-run/sessions").is_dir()

    archived = archive_run_sessions(root, "archive-run")

    assert archived == root / "archive-run/archives/sessions.tar.gz"
    assert archived.is_file() and not (root / "archive-run/sessions").exists()
    assert audit_sessions(root / "artifacts", rows, payloads, _raw_artifact_digest) == before
    assert json.dumps(payloads, sort_keys=True) == original_payloads
    for path, digest in canonical.items():
        assert path.read_bytes() == original_bytes[path]
        assert _raw_artifact_digest(path.parent) == digest
    # No restoration or new tar-based verifier was needed to audit provenance.
    assert not (root / "archive-run/sessions").exists()
