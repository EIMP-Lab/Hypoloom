from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

import hypoloom.execution_workspace as execution_workspace_module
from hypoloom.agent_aci import (
    AgentCommand,
    AgentResponse,
    WorkspaceArtifact,
    with_output_digest,
)
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.candidate_validation import assert_candidate_patch_compiles
from hypoloom.runner import LocalRunner, ProcessRequest
from hypoloom.execution_workspace import (
    EXECUTION_WORKSPACE_VERSION,
    ExecutionWorkspace,
    GitTargetBinding,
    SandboxUnavailable,
    TargetPreparationError,
    WorkspaceIntegrityError,
    bind_clean_git_target,
)


def candidate_pair(source: bytes | None = None) -> tuple[dict[str, object], dict[str, object]]:
    request = default_request(AgentCommand.DRAFT_EXPERIMENT_SPEC)
    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    if source is not None:
        response = with_output_digest(
            replace(
                response,
                proposed_workspace_artifacts=(
                    WorkspaceArtifact.from_bytes("experiment.py", source),
                ),
            )
        )
    return request.to_dict(), response.to_dict()


def make_workspace(tmp_path: Path) -> ExecutionWorkspace:
    state_root = tmp_path / "state"
    artifact_root = state_root / "artifacts"
    artifact_root.mkdir(mode=0o700, parents=True)
    return ExecutionWorkspace(artifact_root, state_root=state_root)


def git(repo: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ("git", "-C", str(repo), *arguments),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        shell=False,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    return completed.stdout


def make_git_target(tmp_path: Path) -> Path:
    target = tmp_path / "target-repository"
    target.mkdir()
    git(target, "init", "-q")
    git(target, "config", "user.email", "hypoloom@example.invalid")
    git(target, "config", "user.name", "Hypoloom test")
    (target / ".gitignore").write_text("ignored.bin\n", encoding="utf-8")
    (target / "src").mkdir()
    (target / "src" / "value.txt").write_text("base\n", encoding="utf-8")
    executable = target / "run.sh"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    git(target, "add", ".gitignore", "src/value.txt", "run.sh")
    git(target, "commit", "-q", "-m", "target base")
    return target


def value_patch(replacement: str = "patched") -> bytes:
    return (
        "diff --git a/src/value.txt b/src/value.txt\n"
        "--- a/src/value.txt\n"
        "+++ b/src/value.txt\n"
        "@@ -1 +1 @@\n"
        "-base\n"
        f"+{replacement}\n"
    ).encode("utf-8")


def make_attempt_root(workspace: ExecutionWorkspace) -> Path:
    work_root = workspace.root / "work"
    work_root.mkdir(mode=0o700)
    attempt_root = workspace.paths("process-attempt-1").attempt_root
    attempt_root.mkdir(mode=0o700)
    return attempt_root


def test_agent_workspace_reuses_pinned_target_and_captures_one_file(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    target = make_git_target(tmp_path)
    binding = GitTargetBinding(
        checkout=target.resolve(), commit=git(target, "rev-parse", "HEAD").decode().strip()
    )
    original = (target / "src" / "value.txt").read_bytes()

    prepared = workspace.prepare_agent_workspace(
        attempt_id="agent-draft-1",
        target_binding=binding,
        target_patch=value_patch(),
    )
    (prepared.writable_root / "experiment.py").write_text(
        "print('candidate')\n", encoding="utf-8"
    )

    assert EXECUTION_WORKSPACE_VERSION == "bwrap-workspace/3"
    assert prepared.target_root is not None
    assert (prepared.target_root / "src" / "value.txt").read_text() == "patched\n"
    assert (target / "src" / "value.txt").read_bytes() == original
    assert workspace.capture_agent_source(prepared, max_bytes=1024) == b"print('candidate')\n"

    prepared.cleanup()
    prepared.cleanup()
    assert not prepared.attempt_root.exists()


@pytest.mark.parametrize(
    "failure",
    ("missing", "extra", "symlink", "hardlink", "directory", "empty", "binary", "oversize"),
)
def test_agent_workspace_rejects_noncanonical_source_inventory(
    tmp_path: Path, failure: str
) -> None:
    workspace = make_workspace(tmp_path)
    prepared = workspace.prepare_agent_workspace(attempt_id=f"agent-{failure}")
    source = prepared.writable_root / "experiment.py"
    if failure == "extra":
        source.write_text("print(1)\n", encoding="utf-8")
        (prepared.writable_root / "notes.txt").write_text("extra", encoding="utf-8")
    elif failure == "symlink":
        target = prepared.attempt_root / "outside.py"
        target.write_text("print(1)\n", encoding="utf-8")
        source.symlink_to(target)
    elif failure == "hardlink":
        target = prepared.attempt_root / "outside.py"
        target.write_text("print(1)\n", encoding="utf-8")
        source.hardlink_to(target)
    elif failure == "directory":
        source.mkdir()
    elif failure == "empty":
        source.touch()
    elif failure == "binary":
        source.write_bytes(b"\xff")
    elif failure == "oversize":
        source.write_bytes(b"x" * 1025)

    with pytest.raises(WorkspaceIntegrityError):
        workspace.capture_agent_source(prepared, max_bytes=1024)
    prepared.cleanup()


def test_agent_workspace_rolls_back_when_draft_directory_creation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = make_workspace(tmp_path)
    original_mkdir = Path.mkdir

    def fail_draft(path: Path, *args, **kwargs):
        if path.name == "draft":
            raise OSError("synthetic draft mkdir failure")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_draft)

    with pytest.raises(OSError, match="synthetic draft mkdir failure"):
        workspace.prepare_agent_workspace(attempt_id="agent-mkdir-failure")
    assert not (workspace.root / "work" / "agent-mkdir-failure").exists()


def plan(
    workspace: ExecutionWorkspace,
    request: dict[str, object],
    response: dict[str, object],
):
    return workspace.plan(
        agent_request=request,
        agent_response=response,
        attempt_id="process-attempt-1",
        attempt_ordinal=1,
        execution_id="execution-1",
        target_env={"HYPOLOOM_PHASE2_SCORE": "1.25"},
        timeout_seconds=5,
    )


def plan_and_materialize(
    workspace: ExecutionWorkspace,
    request: dict[str, object],
    response: dict[str, object],
):
    prepared = plan(workspace, request, response)
    workspace.materialize(
        prepared,
        agent_request=request,
        agent_response=response,
    )
    return prepared


def mount_pairs(argv: tuple[str, ...], option: str) -> list[tuple[str, str]]:
    return [
        (argv[index + 1], argv[index + 2])
        for index, value in enumerate(argv)
        if value == option
    ]


def test_plan_is_pure_then_materialize_replays_aci_and_writes_inputs(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    agent_request, agent_response = candidate_pair()

    prepared = plan(workspace, agent_request, agent_response)

    assert not prepared.attempt_root.exists()
    workspace.materialize(
        prepared,
        agent_request=agent_request,
        agent_response=agent_response,
    )

    expected_source = AgentResponse.from_dict(agent_response).proposed_workspace_artifacts[0].content_bytes()
    assert (prepared.input_dir / "experiment.py").read_bytes() == expected_source
    assert list(prepared.output_dir.iterdir()) == []
    assert prepared.process_request.cwd == str(prepared.log_dir)
    assert prepared.process_request.env == {}
    assert ProcessRequest.from_dict(prepared.process_request.to_dict()) == prepared.process_request

    argv = prepared.process_request.argv
    for option in ("--unshare-all", "--die-with-parent", "--new-session"):
        assert option in argv
    assert "--share-net" not in argv
    assert (str(prepared.input_dir), "/input") in mount_pairs(argv, "--ro-bind")
    assert (str(prepared.output_dir), "/output") in mount_pairs(argv, "--bind")
    assert "--chdir" in argv and argv[argv.index("--chdir") + 1] == "/output"
    assert "/input/experiment.py" in argv

    project_root = Path(__file__).resolve().parents[2]
    forbidden_mounts = {str(workspace.state_root), str(project_root), str(project_root / "hypoloom")}
    all_mounts = mount_pairs(argv, "--bind") + mount_pairs(argv, "--ro-bind")
    assert not forbidden_mounts.intersection(source for source, _ in all_mounts)
    assert not forbidden_mounts.intersection(destination for _, destination in all_mounts)


def test_invalid_committed_response_and_duplicate_attempt_fail_before_overwrite(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    agent_request, agent_response = candidate_pair()
    changed = dict(agent_response)
    changed["output_digest"] = "0" * 64

    with pytest.raises(Exception, match="output_digest"):
        plan(workspace, agent_request, changed)
    assert not workspace.paths("process-attempt-1").attempt_root.exists()

    prepared = plan_and_materialize(workspace, agent_request, agent_response)
    source_before = (prepared.input_dir / "experiment.py").read_bytes()
    with pytest.raises(WorkspaceIntegrityError, match="already exists"):
        workspace.materialize(
            prepared,
            agent_request=agent_request,
            agent_response=agent_response,
        )
    assert (prepared.input_dir / "experiment.py").read_bytes() == source_before


def test_prestart_requires_an_exactly_empty_output_directory(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    agent_request, agent_response = candidate_pair()
    prepared = plan_and_materialize(workspace, agent_request, agent_response)
    workspace.assert_prestart_clean(
        prepared,
        agent_request=agent_request,
        agent_response=agent_response,
    )

    (prepared.output_dir / "result.json").write_text('{"score":999}', encoding="utf-8")
    with pytest.raises(WorkspaceIntegrityError, match="before process start"):
        workspace.assert_prestart_clean(
            prepared,
            agent_request=agent_request,
            agent_response=agent_response,
        )


def test_prestart_replays_committed_aci_and_rejects_input_drift(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    agent_request, agent_response = candidate_pair()
    prepared = plan_and_materialize(workspace, agent_request, agent_response)
    (prepared.input_dir / "experiment.py").write_bytes(b"changed before spawn")

    with pytest.raises(WorkspaceIntegrityError, match="durable Agent response"):
        workspace.assert_prestart_clean(
            prepared,
            agent_request=agent_request,
            agent_response=agent_response,
        )


def test_agent_cannot_collide_with_host_trusted_workspace_path(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    request, response_value = candidate_pair()
    response = AgentResponse.from_dict(response_value)
    response = with_output_digest(
        replace(
            response,
            proposed_workspace_artifacts=(
                WorkspaceArtifact.from_bytes(
                    ".hypoloom/trial_driver.py", b"agent-controlled"
                ),
            ),
        )
    )

    with pytest.raises(
        WorkspaceIntegrityError, match="Host and Agent workspace paths overlap"
    ):
        workspace.plan(
            agent_request=request,
            agent_response=response.to_dict(),
            attempt_id="process-attempt-1",
            attempt_ordinal=1,
            execution_id="execution-1",
            target_env={},
            timeout_seconds=5,
            source_relative_path=".hypoloom/trial_driver.py",
            execution_argv=("python", ".hypoloom/trial_driver.py", "{}"),
            trusted_workspace_files={
                ".hypoloom/trial_driver.py": b"host-controlled"
            },
        )


def test_prestart_rejects_tampered_host_trusted_workspace_file(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    request, response = candidate_pair()
    trusted = {".hypoloom/trial_driver.py": b"host-controlled"}
    prepared = workspace.plan(
        agent_request=request,
        agent_response=response,
        attempt_id="process-attempt-1",
        attempt_ordinal=1,
        execution_id="execution-1",
        target_env={},
        timeout_seconds=5,
        source_relative_path=".hypoloom/trial_driver.py",
        execution_argv=("python", ".hypoloom/trial_driver.py", "{}"),
        trusted_workspace_files=trusted,
    )
    workspace.materialize(
        prepared,
        agent_request=request,
        agent_response=response,
    )
    (prepared.input_dir / ".hypoloom" / "trial_driver.py").write_bytes(
        b"agent-tampered"
    )

    with pytest.raises(WorkspaceIntegrityError, match="materialized input .* differs"):
        workspace.assert_prestart_clean(
            prepared,
            agent_request=request,
            agent_response=response,
        )


def test_real_bwrap_isolates_source_state_and_evaluator_then_collects(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    state_probe = workspace.state_root / "target-wrote-state"
    evaluator = Path(__file__).resolve().parents[2] / "hypoloom" / "outcome_adapter.py"
    source = f'''import json, sys
from pathlib import Path

def writable(path):
    try:
        path.write_text("modified", encoding="utf-8")
    except OSError:
        return False
    return True

result = {{
    "source_writable": writable(Path("/input/experiment.py")),
    "state_writable": writable(Path({str(state_probe)!r})),
    "evaluator_visible": Path({str(evaluator)!r}).exists(),
}}
print("captured stdout")
print("captured stderr", file=sys.stderr)
Path("result.json").write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    '''.encode()
    agent_request, agent_response = candidate_pair(source)
    prepared = plan_and_materialize(workspace, agent_request, agent_response)
    workspace.assert_prestart_clean(
        prepared,
        agent_request=agent_request,
        agent_response=agent_response,
    )

    observation = LocalRunner().run(prepared.process_request)
    assert observation.returncode == 0
    result = json.loads((prepared.output_dir / "result.json").read_text(encoding="utf-8"))
    assert result == {
        "evaluator_visible": False,
        "source_writable": False,
        "state_writable": False,
    }
    assert not state_probe.exists()

    collected = workspace.collect(
        prepared,
        observation,
        agent_request=agent_request,
        agent_response=agent_response,
    )
    assert collected.relative_paths == ("result.json",)
    assert json.loads((collected.staging_dir / "result.json").read_text()) == result
    assert collected.stdout_path.read_text() == "captured stdout\n"
    assert collected.stderr_path.read_text() == "captured stderr\n"
    assert collected.stdout_path.parent != collected.staging_dir
    assert collected.stderr_path.parent != collected.staging_dir


def test_collect_stages_actual_inventory_when_a_successful_process_omits_result(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    source = (
        "from pathlib import Path\n"
        "Path('artifact.txt').write_text('payload', encoding='utf-8')\n"
    ).encode()
    agent_request, agent_response = candidate_pair(source)
    prepared = plan_and_materialize(workspace, agent_request, agent_response)
    observation = LocalRunner().run(prepared.process_request)
    assert observation.returncode == 0
    assert not (prepared.output_dir / "result.json").exists()

    collected = workspace.collect(
        prepared,
        observation,
        agent_request=agent_request,
        agent_response=agent_response,
    )
    assert collected.relative_paths == ("artifact.txt",)
    assert not (collected.staging_dir / "result.json").exists()
    assert (collected.staging_dir / "artifact.txt").read_text() == "payload"


def test_collect_stages_an_empty_inventory_when_a_successful_process_writes_nothing(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    agent_request, agent_response = candidate_pair(b"pass\n")
    prepared = plan_and_materialize(workspace, agent_request, agent_response)
    observation = LocalRunner().run(prepared.process_request)
    assert observation.returncode == 0

    collected = workspace.collect(
        prepared,
        observation,
        agent_request=agent_request,
        agent_response=agent_response,
    )
    assert collected.relative_paths == ()
    assert not (collected.staging_dir / "result.json").exists()
    assert sorted(collected.staging_dir.iterdir()) == []


def test_collect_rejects_source_drift_and_diagnostic_uses_durable_response(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    agent_request, agent_response = candidate_pair()
    prepared = plan_and_materialize(workspace, agent_request, agent_response)
    (prepared.input_dir / "experiment.py").write_bytes(b"changed after preregistration")

    with pytest.raises(WorkspaceIntegrityError, match="durable Agent response"):
        workspace.collect(
            prepared,
            {
                "attempt_id": "process-attempt-1",
                "attempt_ordinal": 1,
                "execution_id": "execution-1",
                "terminal_reason": "SUCCEEDED",
            },
            agent_request=agent_request,
            agent_response=agent_response,
        )

    diagnostic = workspace.integrity_diagnostic(
        prepared,
        "frozen_source_mismatch",
        agent_request=agent_request,
        agent_response=agent_response,
    )
    assert diagnostic.relative_paths == ("result.json",)
    assert json.loads((diagnostic.staging_dir / "result.json").read_text()) == {
        "integrity_rejected": "frozen_source_mismatch"
    }
    assert sorted(path.name for path in diagnostic.staging_dir.iterdir()) == ["result.json"]


def test_recover_uses_persisted_process_request_and_cleanup_is_exact(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    agent_request, agent_response = candidate_pair()
    prepared = plan_and_materialize(workspace, agent_request, agent_response)
    restored = workspace.recover(
        prepared.process_request.to_dict(),
        agent_request=agent_request,
        agent_response=agent_response,
        target_env={"HYPOLOOM_PHASE2_SCORE": "1.25"},
        timeout_seconds=5,
    )
    assert restored == prepared

    prepared.log_dir.joinpath("stdout.log").write_text("keep", encoding="utf-8")
    staged_logs = prepared.staging_root / "logs"
    staged_logs.mkdir(parents=True)
    staged_logs.joinpath("stdout.log").write_text("keep", encoding="utf-8")
    workspace.cleanup_attempt(prepared.attempt_id, preserve_logs=True)
    assert not prepared.attempt_root.exists()
    assert staged_logs.joinpath("stdout.log").read_text() == "keep"
    assert not (prepared.staging_root / "artifact").exists()
    assert not (prepared.staging_root / "integrity").exists()

    with pytest.raises(ValueError, match="attempt_id"):
        workspace.cleanup_attempt("../escape")


@pytest.mark.parametrize(
    ("source_path", "result_path"),
    [
        ("experiment.py", "experiment.py"),
        ("tree", "tree/result.json"),
        ("tree/experiment.py", "tree"),
    ],
)
def test_source_and_result_paths_must_be_disjoint(
    tmp_path: Path, source_path: str, result_path: str
) -> None:
    workspace = make_workspace(tmp_path)
    agent_request, agent_response = candidate_pair()
    with pytest.raises(ValueError, match="must be disjoint"):
        workspace.plan(
            agent_request=agent_request,
            agent_response=agent_response,
            attempt_id="process-attempt-1",
            attempt_ordinal=1,
            execution_id="execution-1",
            target_env={},
            timeout_seconds=5,
            source_relative_path=source_path,
            result_relative_path=result_path,
        )


def test_partial_materialization_can_be_cleaned_and_cleanup_replays_exactly(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    agent_request, agent_response = candidate_pair()
    prepared = plan(workspace, agent_request, agent_response)
    prepared.attempt_root.mkdir(mode=0o700, parents=True)
    prepared.attempt_root.joinpath("partial").write_text("not authority", encoding="utf-8")

    with pytest.raises(WorkspaceIntegrityError, match="already exists"):
        workspace.materialize(
            prepared,
            agent_request=agent_request,
            agent_response=agent_response,
        )
    workspace.cleanup_attempt(prepared.attempt_id)
    workspace.cleanup_attempt(prepared.attempt_id)
    workspace.materialize(
        prepared,
        agent_request=agent_request,
        agent_response=agent_response,
    )
    assert (prepared.input_dir / "experiment.py").is_file()


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("timeout_seconds", 6),
        ("terminate_grace_seconds", 0.75),
        ("env", {"UNCOMMITTED": "1"}),
        ("argv", ("/bin/false",)),
    ],
)
def test_recover_requires_exact_canonical_process_request(
    tmp_path: Path, field: str, changed: object
) -> None:
    workspace = make_workspace(tmp_path)
    agent_request, agent_response = candidate_pair()
    prepared = plan(workspace, agent_request, agent_response)
    persisted = prepared.process_request.to_dict()
    persisted[field] = list(changed) if field == "argv" else changed

    with pytest.raises(WorkspaceIntegrityError, match="canonical execution plan"):
        workspace.recover(
            persisted,
            agent_request=agent_request,
            agent_response=agent_response,
            target_env={"HYPOLOOM_PHASE2_SCORE": "1.25"},
            timeout_seconds=5,
        )


def test_missing_sandbox_fails_closed_at_materialization_without_creating_files(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    artifact_root = state_root / "artifacts"
    artifact_root.mkdir(mode=0o700, parents=True)
    workspace = ExecutionWorkspace(
        artifact_root,
        state_root=state_root,
        bwrap_path=tmp_path / "missing-bwrap",
    )

    request, response = candidate_pair()
    prepared = plan(workspace, request, response)
    with pytest.raises(SandboxUnavailable, match="bubblewrap"):
        workspace.materialize(prepared, agent_request=request, agent_response=response)
    assert not workspace.paths("process-attempt-1").attempt_root.exists()


def test_adapter_adds_no_parallel_digest_or_manifest_authority() -> None:
    source = (Path(__file__).resolve().parents[2] / "hypoloom" / "execution_workspace.py").read_text()
    assert "hashlib" not in source
    assert "manifest" not in source.lower()


def test_clean_git_target_binds_commit_without_mutating_checkout(tmp_path: Path) -> None:
    target = make_git_target(tmp_path)
    (target / "ignored.bin").write_bytes(b"checkout-only data")
    expected_commit = git(target, "rev-parse", "HEAD").decode().strip()
    index_before = (target / ".git" / "index").stat()

    binding = bind_clean_git_target(target)

    index_after = (target / ".git" / "index").stat()
    assert binding.checkout == target.resolve()
    assert binding.commit == expected_commit
    assert (index_after.st_mtime_ns, index_after.st_size) == (
        index_before.st_mtime_ns,
        index_before.st_size,
    )
    assert git(target, "status", "--porcelain=v1", "--untracked-files=all") == b""
    assert (target / "ignored.bin").read_bytes() == b"checkout-only data"


@pytest.mark.parametrize("dirty_kind", ["tracked", "staged", "untracked"])
def test_git_target_binding_rejects_dirty_checkout(
    tmp_path: Path, dirty_kind: str
) -> None:
    target = make_git_target(tmp_path)
    if dirty_kind == "tracked":
        (target / "src" / "value.txt").write_text("dirty\n", encoding="utf-8")
    elif dirty_kind == "staged":
        (target / "src" / "value.txt").write_text("staged\n", encoding="utf-8")
        git(target, "add", "src/value.txt")
    else:
        (target / "untracked.txt").write_text("untracked\n", encoding="utf-8")

    with pytest.raises(TargetPreparationError, match="dirty"):
        bind_clean_git_target(target)


def test_git_target_binding_rejects_unresolvable_or_non_root_target(
    tmp_path: Path,
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(TargetPreparationError):
        bind_clean_git_target(plain)
    with pytest.raises(TargetPreparationError):
        bind_clean_git_target(tmp_path / "missing")

    target = make_git_target(tmp_path)
    with pytest.raises(TargetPreparationError, match="repository root"):
        bind_clean_git_target(target / "src")


def test_target_overlay_uses_only_commit_files_and_patches_only_copy(
    tmp_path: Path,
) -> None:
    target = make_git_target(tmp_path)
    (target / "ignored.bin").write_bytes(b"must not be copied")
    os.symlink("src/value.txt", target / "value-link")
    (target / ".gitattributes").write_text(
        "format.txt export-subst\nhidden.txt export-ignore\n", encoding="utf-8"
    )
    (target / "format.txt").write_text("$Format:%H$\n", encoding="utf-8")
    (target / "hidden.txt").write_text("tracked despite archive rules\n", encoding="utf-8")
    git(target, "add", "value-link", ".gitattributes", "format.txt", "hidden.txt")
    git(target, "commit", "-q", "-m", "safe link")
    binding = bind_clean_git_target(target)
    source_index_before = (target / ".git" / "index").stat()
    workspace = make_workspace(tmp_path)
    attempt_root = make_attempt_root(workspace)

    target_copy = workspace.materialize_target_overlay(
        binding,
        attempt_id="process-attempt-1",
        patch=value_patch(),
    )

    assert target_copy == attempt_root / "target"
    assert (target_copy / "src" / "value.txt").read_text() == "patched\n"
    assert (target_copy / "value-link").is_symlink()
    assert (target_copy / "value-link").read_text() == "patched\n"
    assert (target_copy / "format.txt").read_text() == "$Format:%H$\n"
    assert (target_copy / "hidden.txt").read_text() == "tracked despite archive rules\n"
    assert os.access(target_copy / "run.sh", os.X_OK)
    assert not (target_copy / ".git").exists()
    assert not (target_copy / "ignored.bin").exists()
    assert (target / "src" / "value.txt").read_text() == "base\n"
    assert (target / "ignored.bin").read_bytes() == b"must not be copied"
    source_index_after = (target / ".git" / "index").stat()
    assert (source_index_after.st_mtime_ns, source_index_after.st_size) == (
        source_index_before.st_mtime_ns,
        source_index_before.st_size,
    )
    assert git(target, "status", "--porcelain=v1", "--untracked-files=all") == b""


def test_target_overlay_reads_bound_commit_after_checkout_changes(tmp_path: Path) -> None:
    target = make_git_target(tmp_path)
    binding = bind_clean_git_target(target)
    (target / "src" / "value.txt").write_text(
        "changed after binding\n", encoding="utf-8"
    )
    workspace = make_workspace(tmp_path)
    make_attempt_root(workspace)

    target_copy = workspace.materialize_target_overlay(
        binding, attempt_id="process-attempt-1", patch=value_patch()
    )

    assert (target_copy / "src" / "value.txt").read_text() == "patched\n"
    assert (target / "src" / "value.txt").read_text() == "changed after binding\n"


def test_target_overlay_ignores_replace_ref_drift_after_binding(tmp_path: Path) -> None:
    target = make_git_target(tmp_path)
    binding = bind_clean_git_target(target)
    (target / "src" / "value.txt").write_text("replacement\n", encoding="utf-8")
    git(target, "commit", "-qam", "replacement commit")
    replacement_commit = git(target, "rev-parse", "HEAD").decode().strip()
    git(target, "replace", binding.commit, replacement_commit)
    workspace = make_workspace(tmp_path)
    make_attempt_root(workspace)

    target_copy = workspace.materialize_target_overlay(
        binding, attempt_id="process-attempt-1", patch=None
    )

    assert (target_copy / "src" / "value.txt").read_text() == "base\n"
    assert (target / "src" / "value.txt").read_text() == "replacement\n"


def test_target_overlay_rejects_repo_local_smudge_drift_after_binding(tmp_path: Path) -> None:
    target = make_git_target(tmp_path)
    (target / ".gitattributes").write_text("src/value.txt filter=drift\n", encoding="utf-8")
    git(target, "config", "filter.drift.clean", "cat")
    git(target, "config", "filter.drift.smudge", "cat")
    git(target, "add", ".gitattributes")
    git(target, "commit", "-qm", "declare conversion filter")
    binding = bind_clean_git_target(target)
    git(target, "config", "filter.drift.smudge", "sed s/base/drift/")
    workspace = make_workspace(tmp_path)
    make_attempt_root(workspace)

    with pytest.raises(TargetPreparationError, match="differs from bound Git blob"):
        workspace.materialize_target_overlay(
            binding, attempt_id="process-attempt-1", patch=None
        )

    assert git(target, "show", f"{binding.commit}:src/value.txt") == b"base\n"
    assert (target / "src" / "value.txt").read_bytes() == b"base\n"
    assert git(target, "status", "--porcelain=v1", "--untracked-files=all") == b""


def test_target_overlay_checks_before_apply_with_bounded_argv_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = make_git_target(tmp_path)
    binding = bind_clean_git_target(target)
    workspace = make_workspace(tmp_path)
    attempt_root = make_attempt_root(workspace)
    real_run = subprocess.run
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def recording_run(argv, **kwargs):
        calls.append((tuple(argv), dict(kwargs)))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(execution_workspace_module.subprocess, "run", recording_run)
    workspace.materialize_target_overlay(
        binding,
        attempt_id="process-attempt-1",
        patch=value_patch(),
        timeout_seconds=2,
    )

    apply_calls = [call for call in calls if "apply" in call[0]]
    read_tree_calls = [call for call in calls if "read-tree" in call[0]]
    checkout_calls = [call for call in calls if "checkout-index" in call[0]]
    assert len(read_tree_calls) == len(checkout_calls) == 1
    index_paths = {
        kwargs["env"]["GIT_INDEX_FILE"]
        for _, kwargs in (*read_tree_calls, *checkout_calls)
    }
    assert len(index_paths) == 1
    assert Path(index_paths.pop()).is_relative_to(attempt_root)
    assert len([call for call in calls if "ls-tree" in call[0]]) == 1
    assert any("hash-object" in argv and "--no-filters" in argv for argv, _ in calls)
    assert not any(command in argv for argv, _ in calls for command in ("cat-file", "archive"))
    assert len(apply_calls) == 2
    assert all("--no-index" in argv for argv, _ in apply_calls)
    assert "--check" in apply_calls[0][0]
    assert "--check" not in apply_calls[1][0]
    assert all(
        Path(argv[argv.index("-C") + 1]).is_relative_to(attempt_root)
        for argv, _ in apply_calls
    )
    assert all(kwargs["shell"] is False for _, kwargs in calls)
    assert all(kwargs["timeout"] == 2.0 for _, kwargs in calls)


def test_rejected_or_escaping_patch_leaves_no_partial_target(
    tmp_path: Path,
) -> None:
    target = make_git_target(tmp_path)
    binding = bind_clean_git_target(target)
    workspace = make_workspace(tmp_path)
    attempt_root = make_attempt_root(workspace)
    bad_patch = value_patch().replace(b"-base\n", b"-not-the-base\n")

    with pytest.raises(TargetPreparationError, match="git apply"):
        workspace.materialize_target_overlay(
            binding, attempt_id="process-attempt-1", patch=bad_patch
        )
    escaping_patch = b"""diff --git a/../../escaped.txt b/../../escaped.txt
new file mode 100644
--- /dev/null
+++ b/../../escaped.txt
@@ -0,0 +1 @@
+escaped
"""
    with pytest.raises(TargetPreparationError, match="git apply"):
        workspace.materialize_target_overlay(
            binding, attempt_id="process-attempt-1", patch=escaping_patch
        )
    symlink_patch = b"""diff --git a/escape-link b/escape-link
new file mode 120000
--- /dev/null
+++ b/escape-link
@@ -0,0 +1 @@
+../../outside
\\ No newline at end of file
"""
    with pytest.raises(TargetPreparationError, match="symlink escapes"):
        workspace.materialize_target_overlay(
            binding, attempt_id="process-attempt-1", patch=symlink_patch
        )
    with pytest.raises(ValueError, match="target_directory"):
        workspace.materialize_target_overlay(
            binding,
            attempt_id="process-attempt-1",
            patch=value_patch(),
            target_directory="../escaped",
        )

    assert not (tmp_path / "escaped.txt").exists()
    assert not (attempt_root / "target").exists()
    assert not list(attempt_root.glob(".target-overlay-*"))
    assert (target / "src" / "value.txt").read_text() == "base\n"


def test_target_overlay_rejects_symlink_escape_from_commit_or_attempt_root(
    tmp_path: Path,
) -> None:
    target = make_git_target(tmp_path)
    os.symlink("../../outside", target / "escape-link")
    git(target, "add", "escape-link")
    git(target, "commit", "-q", "-m", "escaping link")
    binding = bind_clean_git_target(target)
    workspace = make_workspace(tmp_path)
    attempt_root = make_attempt_root(workspace)

    with pytest.raises(TargetPreparationError, match="unsafe|outside|escape"):
        workspace.materialize_target_overlay(
            binding, attempt_id="process-attempt-1", patch=None
        )
    outside = tmp_path / "outside-target"
    outside.mkdir()
    os.symlink(outside, attempt_root / "target")
    with pytest.raises(TargetPreparationError, match="already exists"):
        workspace.materialize_target_overlay(
            binding, attempt_id="process-attempt-1", patch=None
        )
    assert list(outside.iterdir()) == []


def test_target_overlay_rejects_state_root_inside_source_checkout(tmp_path: Path) -> None:
    target = make_git_target(tmp_path)
    (target / ".gitignore").write_text("ignored.bin\nstate/\n", encoding="utf-8")
    git(target, "add", ".gitignore")
    git(target, "commit", "-q", "-m", "ignore state")
    binding = bind_clean_git_target(target)
    state_root = target / "state"
    artifact_root = state_root / "artifacts"
    artifact_root.mkdir(mode=0o700, parents=True)
    workspace = ExecutionWorkspace(artifact_root, state_root=state_root)
    make_attempt_root(workspace)

    with pytest.raises(TargetPreparationError, match="disjoint"):
        workspace.materialize_target_overlay(
            binding, attempt_id="process-attempt-1", patch=value_patch()
        )
    assert (target / "src" / "value.txt").read_text() == "base\n"


def test_cpu_plan_explicitly_hides_all_cuda_devices(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    request, response = candidate_pair()

    argv = plan(workspace, request, response).process_request.argv

    assert argv[argv.index("--unsetenv") + 1] == "CUDA_VISIBLE_DEVICES"
    assert "--dev-bind" not in argv


def test_exact_gpu_device_bindings_follow_private_dev_and_freeze_environment(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    artifact_root = state_root / "artifacts"
    artifact_root.mkdir(mode=0o700, parents=True)
    device_bindings = (
        (Path("/dev/nvidia2"), "/dev/nvidia2"),
        (Path("/dev/nvidiactl"), "/dev/nvidiactl"),
        (Path("/dev/nvidia-uvm"), "/dev/nvidia-uvm"),
    )
    workspace = ExecutionWorkspace(
        artifact_root,
        state_root=state_root,
        device_bindings=device_bindings,
        fixed_environment={
            "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
            "CUDA_VISIBLE_DEVICES": "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        },
    )
    request, response = candidate_pair()

    prepared = plan(workspace, request, response)
    argv = prepared.process_request.argv
    private_dev = argv.index("--dev")
    binding_positions = [index for index, value in enumerate(argv) if value == "--dev-bind"]

    assert binding_positions and min(binding_positions) > private_dev
    assert mount_pairs(argv, "--dev-bind") == [
        (str(source), target) for source, target in device_bindings
    ]
    assert all(
        source.startswith("/dev/nvidia") and target.startswith("/dev/nvidia")
        for source, target in mount_pairs(argv, "--dev-bind")
    )
    environment = {
        argv[index + 1]: argv[index + 2]
        for index, value in enumerate(argv)
        if value == "--setenv"
    }
    assert environment["CUDA_DEVICE_ORDER"] == "PCI_BUS_ID"
    assert environment["CUDA_VISIBLE_DEVICES"] == (
        "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    )


@pytest.mark.parametrize(
    "bindings",
    [
        (("dev/nvidia0", "/dev/nvidia0"),),
        (("/dev/nvidia0", "dev/nvidia0"),),
        (("/dev/null", "/dev/nvidia0"),),
        (("/dev/nvidia0", "/dev/null"),),
        (("/dev/nvidia0", "/dev/nvidia0"), ("/dev/nvidia0", "/dev/nvidia1")),
    ],
)
def test_device_binding_contract_rejects_ambiguous_or_non_nvidia_paths(
    tmp_path: Path, bindings: tuple[tuple[str, str], ...]
) -> None:
    state_root = tmp_path / "state"
    artifact_root = state_root / "artifacts"
    artifact_root.mkdir(mode=0o700, parents=True)

    with pytest.raises(ValueError, match="device_bindings"):
        ExecutionWorkspace(
            artifact_root,
            state_root=state_root,
            device_bindings=bindings,
        )


def test_materialize_requires_every_bound_source_to_be_a_character_device(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    artifact_root = state_root / "artifacts"
    artifact_root.mkdir(mode=0o700, parents=True)
    workspace = ExecutionWorkspace(
        artifact_root,
        state_root=state_root,
        device_bindings=((Path("/dev/nvidia999"), "/dev/nvidia999"),),
        fixed_environment={
            "CUDA_VISIBLE_DEVICES": "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        },
    )
    request, response = candidate_pair()
    prepared = plan(workspace, request, response)

    with pytest.raises(SandboxUnavailable, match="character device"):
        workspace.materialize(prepared, agent_request=request, agent_response=response)
    assert not prepared.attempt_root.exists()


def test_materialize_accepts_only_sources_verified_as_character_devices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "state"
    artifact_root = state_root / "artifacts"
    artifact_root.mkdir(mode=0o700, parents=True)
    workspace = ExecutionWorkspace(
        artifact_root,
        state_root=state_root,
        device_bindings=(
            (Path("/dev/nvidia2"), "/dev/nvidia2"),
            (Path("/dev/nvidiactl"), "/dev/nvidiactl"),
            (Path("/dev/nvidia-uvm"), "/dev/nvidia-uvm"),
        ),
        fixed_environment={
            "CUDA_VISIBLE_DEVICES": "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        },
    )
    monkeypatch.setattr(execution_workspace_module, "_is_character_device", lambda _: True)
    request, response = candidate_pair()
    prepared = plan(workspace, request, response)

    workspace.materialize(prepared, agent_request=request, agent_response=response)

    assert (prepared.input_dir / "experiment.py").is_file()


def test_fixed_environment_cannot_be_overridden_by_experiment_target_env(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    artifact_root = state_root / "artifacts"
    artifact_root.mkdir(mode=0o700, parents=True)
    workspace = ExecutionWorkspace(
        artifact_root,
        state_root=state_root,
        fixed_environment={"CUDA_VISIBLE_DEVICES": ""},
    )
    request, response = candidate_pair()

    with pytest.raises(ValueError, match="fixed environment"):
        workspace.plan(
            agent_request=request,
            agent_response=response,
            attempt_id="process-attempt-1",
            attempt_ordinal=1,
            execution_id="execution-1",
            target_env={"CUDA_VISIBLE_DEVICES": "GPU-uncommitted"},
            timeout_seconds=5,
        )


def test_gpu_recovery_reconstructs_the_exact_frozen_device_binding(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    artifact_root = state_root / "artifacts"
    artifact_root.mkdir(mode=0o700, parents=True)
    fixed_environment = {
        "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
        "CUDA_VISIBLE_DEVICES": "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    }
    original = ExecutionWorkspace(
        artifact_root,
        state_root=state_root,
        device_bindings=(
            (Path("/dev/nvidia2"), "/dev/nvidia2"),
            (Path("/dev/nvidiactl"), "/dev/nvidiactl"),
            (Path("/dev/nvidia-uvm"), "/dev/nvidia-uvm"),
        ),
        fixed_environment=fixed_environment,
    )
    request, response = candidate_pair()
    prepared = plan(original, request, response)

    assert original.recover(
        prepared.process_request,
        agent_request=request,
        agent_response=response,
        target_env={"HYPOLOOM_PHASE2_SCORE": "1.25"},
        timeout_seconds=5,
    ) == prepared

    drifted = ExecutionWorkspace(
        artifact_root,
        state_root=state_root,
        device_bindings=(
            (Path("/dev/nvidia3"), "/dev/nvidia3"),
            (Path("/dev/nvidiactl"), "/dev/nvidiactl"),
            (Path("/dev/nvidia-uvm"), "/dev/nvidia-uvm"),
        ),
        fixed_environment=fixed_environment,
    )
    with pytest.raises(WorkspaceIntegrityError, match="canonical execution plan"):
        drifted.recover(
            prepared.process_request,
            agent_request=request,
            agent_response=response,
            target_env={"HYPOLOOM_PHASE2_SCORE": "1.25"},
            timeout_seconds=5,
        )


def test_execution_environment_contract_mirrors_sandbox_profile() -> None:
    cpu = execution_workspace_module.execution_environment_contract("bwrap-cpu-no-network/2")
    gpu = execution_workspace_module.execution_environment_contract("bwrap-gpu-single-no-network/1")
    assert cpu["contract_version"] == execution_workspace_module.EXECUTION_ENVIRONMENT_CONTRACT_VERSION
    assert cpu["sandbox_profile"] == "bwrap-cpu-no-network/2"
    assert cpu["network"] == "none"
    assert cpu["working_directory"] == "/output"
    assert cpu["writable_roots"] == ["/output", "/tmp"]
    assert cpu["gpu"] is None
    assert "git" in cpu["available_executables"]
    assert cpu["environment_variables"]["empty_valued_entries_are_unset"] is True
    assert gpu["sandbox_profile"] == "bwrap-gpu-single-no-network/1"
    assert gpu["gpu"] is not None


@pytest.mark.parametrize("profile", ["bwrap-cpu-no-network/2", "bwrap-gpu-single-no-network/1"])
def test_execution_advisory_distinguishes_generic_system_commands_from_preset_drivers(profile):
    contract = execution_workspace_module.execution_environment_contract(profile)
    available = contract["available_executables"]
    for description in ("Generic C5/GOAL", "/usr/bin", "/bin", "read-only", "shells", "coreutils",
                        "preset drivers", "pinned Python", "Do not install", "modify runtime/system"):
        assert description in available
    assert contract["writable_roots"] == ["/output", "/tmp"]
    assert contract["network"] == "none"


def test_preflight_literal_diagnostic_names_the_scanned_entrypoint(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    target = tmp_path / "target"
    (target / "src" / "app").mkdir(parents=True)
    entrypoint = target / "src" / "app" / "train.py"
    entrypoint.write_text("print('train')\n", encoding="utf-8")
    plan = {
        "production": True,
        "requirements": {
            "required_paths": [
                {"scope": "target", "path": "src/app/train.py", "kind": "file"}
            ],
            "required_source_literals": ["iemocap6_split_manifest.json"],
        },
        "capabilities": {},
        "argv": ["python", "src/app/train.py"],
        "entrypoint_path": "src/app/train.py",
        "target_root": str(target),
        "data_bindings": (),
    }
    with pytest.raises(
        WorkspaceIntegrityError,
        match=r"entrypoint src/app/train\.py lacks required source literals",
    ):
        workspace.assert_component_preflight(plan=plan, target_root=target)


def test_preflight_capability_diagnostic_names_capability_and_missing_literals(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    target = tmp_path / "target"
    (target / "src" / "app").mkdir(parents=True)
    entrypoint = target / "src" / "app" / "train.py"
    entrypoint.write_text("MODEL = 'candidate'\n", encoding="utf-8")
    plan = {
        "production": True,
        "requirements": {},
        "capabilities": {
            "output": {
                "role": "output",
                "path": "src/app/train.py",
                "required_literals": ["causal_audit", "max_future_perturbation_logit_diff"],
            },
        },
        "argv": ["python", "src/app/train.py"],
        "entrypoint_path": "src/app/train.py",
        "target_root": str(target),
        "data_bindings": (),
    }
    with pytest.raises(WorkspaceIntegrityError) as caught:
        workspace.assert_component_preflight(plan=plan, target_root=target)
    message = str(caught.value)
    assert "capability source contract failed: src/app/train.py" in message
    assert "capability 'output'" in message
    assert "['causal_audit', 'max_future_perturbation_logit_diff']" in message


def test_preflight_capability_diagnostic_names_capability_for_missing_file(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    target = tmp_path / "target"
    (target / "src" / "app").mkdir(parents=True)
    (target / "src" / "app" / "train.py").write_text("print('train')\n", encoding="utf-8")
    plan = {
        "production": True,
        "requirements": {},
        "capabilities": {
            "intervention_point": {
                "role": "intervention_point",
                "path": "src/app/candidate.py",
                "required_literals": ["candidate"],
            },
        },
        "argv": ["python", "src/app/train.py"],
        "entrypoint_path": "src/app/train.py",
        "target_root": str(target),
        "data_bindings": (),
    }
    with pytest.raises(
        WorkspaceIntegrityError,
        match=r"capability source contract failed: src/app/candidate\.py "
              r"\(capability 'intervention_point': required file is missing\)",
    ):
        workspace.assert_component_preflight(plan=plan, target_root=target)


def test_verified_target_tree_is_verified_patched_and_repeatable(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    attempt_root = workspace.root / "work" / "attempt-verify"
    attempt_root.mkdir(mode=0o700, parents=True)
    target = tmp_path / "repo"
    (target / "src").mkdir(parents=True)
    (target / "src" / "train.py").write_text("MARKER = 'base'\n", encoding="utf-8")
    git(target, "init", "-q")
    git(target, "add", ".")
    git(target, "-c", "user.name=Hypoloom Test", "-c", "user.email=hypoloom@example.invalid", "commit", "-qm", "base")
    commit = git(target, "rev-parse", "HEAD").decode().strip()
    patch = (
        b"diff --git a/src/train.py b/src/train.py\n"
        b"--- a/src/train.py\n"
        b"+++ b/src/train.py\n"
        b"@@ -1 +1,2 @@\n"
        b" MARKER = 'base'\n"
        b"+AUDIT = 'causal_audit'\n"
    )
    binding = GitTargetBinding(checkout=target.resolve(), commit=commit)
    for _ in range(2):
        with workspace.verified_target_tree(
            binding, attempt_id="attempt-verify", patch=patch
        ) as tree:
            assert (tree / "src" / "train.py").read_text(encoding="utf-8") == (
                "MARKER = 'base'\nAUDIT = 'causal_audit'\n"
            )
        assert not tree.exists()


def _design_pair(
    patch: bytes,
) -> tuple[dict[str, object], dict[str, object], str]:
    request = default_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT)
    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    artifacts = {
        artifact.path: artifact for artifact in response.proposed_workspace_artifacts
    }
    response = with_output_digest(
        replace(
            response,
            proposed_workspace_artifacts=(
                artifacts["experiment.py"],
                WorkspaceArtifact.from_bytes("implementation.patch", patch),
            ),
        )
    )
    return request.to_dict(), response.to_dict(), request.component_id


def _python_target(tmp_path: Path) -> Path:
    target = tmp_path / "repo"
    (target / "src").mkdir(parents=True)
    (target / "src" / "train.py").write_text("MARKER = 'base'\n", encoding="utf-8")
    git(target, "init", "-q")
    git(target, "add", ".")
    git(target, "-c", "user.name=Hypoloom Test", "-c", "user.email=hypoloom@example.invalid", "commit", "-qm", "base")
    return target


def _production_document(target: Path, component_id: str) -> dict[str, object]:
    return {
        "components": [{"id": component_id, "protocol": "p1"}],
        "delivery_contracts": {"protocols": {"p1": {"production": True}}},
        "runtime": {"target_repo": str(target)},
    }


_BROKEN_SOURCE_PATCH = (
    b"diff --git a/src/train.py b/src/train.py\n"
    b"--- a/src/train.py\n"
    b"+++ b/src/train.py\n"
    b"@@ -1 +1,2 @@\n"
    b" MARKER = 'base'\n"
    b"+  BROKEN = True\n"
)


def test_candidate_patch_compile_precheck_rejects_invalid_patched_python(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    target = _python_target(tmp_path)
    request, response, component_id = _design_pair(_BROKEN_SOURCE_PATCH)

    with pytest.raises(
        ValueError,
        match=r"implementation_patch_compile_failed: src/train\.py: "
              r"unexpected indent \(line 2\)",
    ):
        assert_candidate_patch_compiles(
            workspace,
            _production_document(target, component_id),
            component_id,
            agent_request=request,
            agent_response=response,
        )


def test_candidate_patch_compile_precheck_accepts_valid_patched_python(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    target = _python_target(tmp_path)
    valid_patch = (
        b"diff --git a/src/train.py b/src/train.py\n"
        b"--- a/src/train.py\n"
        b"+++ b/src/train.py\n"
        b"@@ -1 +1,2 @@\n"
        b" MARKER = 'base'\n"
        b"+AUDIT = 'causal_audit'\n"
    )
    request, response, component_id = _design_pair(valid_patch)

    assert_candidate_patch_compiles(
        workspace,
        _production_document(target, component_id),
        component_id,
        agent_request=request,
        agent_response=response,
    )


def test_candidate_patch_compile_precheck_handles_deleted_python_sources(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    target = _python_target(tmp_path)
    delete_patch = (
        b"diff --git a/src/train.py b/src/train.py\n"
        b"deleted file mode 100644\n"
        b"--- a/src/train.py\n"
        b"+++ /dev/null\n"
        b"@@ -1 +0,0 @@\n"
        b"-MARKER = 'base'\n"
    )
    request, response, component_id = _design_pair(delete_patch)

    assert_candidate_patch_compiles(
        workspace,
        _production_document(target, component_id),
        component_id,
        agent_request=request,
        agent_response=response,
    )


def test_candidate_patch_compile_precheck_propagates_patch_application_failure(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    target = _python_target(tmp_path)
    stale_patch = (
        b"diff --git a/src/train.py b/src/train.py\n"
        b"--- a/src/train.py\n"
        b"+++ b/src/train.py\n"
        b"@@ -1 +1 @@\n"
        b"-MARKER = 'never-committed'\n"
        b"+MARKER = 'mutated'\n"
    )
    request, response, component_id = _design_pair(stale_patch)

    with pytest.raises(TargetPreparationError):
        assert_candidate_patch_compiles(
            workspace,
            _production_document(target, component_id),
            component_id,
            agent_request=request,
            agent_response=response,
        )


def test_candidate_patch_compile_precheck_skips_non_production_components(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    target = _python_target(tmp_path)
    request, response, component_id = _design_pair(_BROKEN_SOURCE_PATCH)
    document = _production_document(target, component_id)
    document["delivery_contracts"]["protocols"]["p1"]["production"] = False

    assert_candidate_patch_compiles(
        workspace,
        document,
        component_id,
        agent_request=request,
        agent_response=response,
    )


def test_target_overlay_applies_patch_inside_enclosing_repository(
    tmp_path: Path,
) -> None:
    # Regression: git apply --no-index resolved patch paths against an
    # enclosing repository root and silently skipped every section with
    # exit status zero, leaving production target overlays unpatched.
    outer = tmp_path / "outer"
    outer.mkdir()
    git(outer, "init", "-q")
    (outer / "README.md").write_text("enclosing repository\n", encoding="utf-8")
    git(outer, "add", ".")
    git(
        outer, "-c", "user.name=Hypoloom Test",
        "-c", "user.email=hypoloom@example.invalid", "commit", "-qm", "outer",
    )
    target = outer / "repo"
    (target / "src").mkdir(parents=True)
    (target / "src" / "train.py").write_text("MARKER = 'base'\n", encoding="utf-8")
    git(target, "init", "-q")
    git(target, "add", ".")
    git(
        target, "-c", "user.name=Hypoloom Test",
        "-c", "user.email=hypoloom@example.invalid", "commit", "-qm", "base",
    )
    commit = git(target, "rev-parse", "HEAD").decode().strip()
    state_root = outer / "state"
    artifact_root = state_root / "artifacts"
    attempt_root = artifact_root / "work" / "attempt-nested"
    attempt_root.mkdir(mode=0o700, parents=True)
    workspace = ExecutionWorkspace(artifact_root, state_root=state_root)
    binding = GitTargetBinding(checkout=target.resolve(), commit=commit)
    patch = (
        b"diff --git a/src/train.py b/src/train.py\n"
        b"--- a/src/train.py\n"
        b"+++ b/src/train.py\n"
        b"@@ -1 +1,2 @@\n"
        b" MARKER = 'base'\n"
        b"+AUDIT = 'causal_audit'\n"
    )

    tree = workspace.materialize_target_overlay(
        binding, attempt_id="attempt-nested", patch=patch
    )
    assert (tree / "src" / "train.py").read_text(encoding="utf-8") == (
        "MARKER = 'base'\nAUDIT = 'causal_audit'\n"
    )

    assert workspace.check_bound_target_patch(binding, patch=patch)
    with pytest.raises(TargetPreparationError, match="patch does not apply"):
        workspace.check_target_patch(
            attempt_id="attempt-nested",
            patch=patch,
        )

    assert workspace.check_target_patch(
        attempt_id="attempt-nested",
        patch=(
            b"diff --git a/src/train.py b/src/train.py\n"
            b"--- a/src/train.py\n"
            b"+++ b/src/train.py\n"
            b"@@ -1,2 +1,3 @@\n"
            b" MARKER = 'base'\n"
            b" AUDIT = 'causal_audit'\n"
            b"+SECOND = 'second'\n"
        ),
    )
    with pytest.raises(TargetPreparationError, match="patch does not apply"):
        workspace.check_target_patch(
            attempt_id="attempt-nested",
            patch=(
                b"diff --git a/src/train.py b/src/train.py\n"
                b"--- a/src/train.py\n"
                b"+++ b/src/train.py\n"
                b"@@ -1 +1 @@\n"
                b"-MARKER = 'context-that-never-existed'\n"
                b"+MARKER = 'mutated'\n"
            ),
        )
