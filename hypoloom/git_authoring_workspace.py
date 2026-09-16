"""Attempt-local Git worktrees for Host-managed source authoring.

This module owns only disposable Git preparation, patch capture, and cleanup.
It does not launch an Agent, mutate workflow state, or use the operator's
checkout as a Git worktree manager.
"""

from __future__ import annotations

import math
import os
import re
import selectors
import signal
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Sequence

from .execution_workspace import GitTargetBinding


_ATTEMPT_ROOT_NAME = "git-authoring-worktrees"
_PRIVATE_REPOSITORY_NAME = "repository.git"
_WORKTREE_NAME = "worktree"
_MAX_PATCH_BYTES = 64 * 1024 * 1024
_MAX_GIT_OUTPUT_BYTES = _MAX_PATCH_BYTES + 1
_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
GIT_AUTHORING_WORKSPACE_VERSION = "git-authoring-worktree/1"


class GitAuthoringWorkspaceError(RuntimeError):
    """Base error for preparation, capture, and cleanup failures."""


class GitAuthoringPreparationError(GitAuthoringWorkspaceError):
    """The pinned repository could not be prepared safely."""


class GitAuthoringIntegrityError(GitAuthoringWorkspaceError):
    """The disposable worktree no longer has an acceptable shape."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PreparedGitAuthoringWorkspace:
    """One disposable, detached worktree rooted below a state directory."""

    attempt_id: str
    workspace_root: Path
    attempt_root: Path
    repository_root: Path
    worktree_root: Path
    base_commit: str
    git_executable: str
    timeout_seconds: float
    repository_config_snapshot: bytes

    def capture_patch(self, *, max_bytes: int, max_files: int, include_commits: bool = False, allow_empty: bool = False) -> bytes:
        """Capture the cumulative worktree state as a Git binary patch."""

        _positive_limit(max_bytes, "max_bytes", maximum=_MAX_PATCH_BYTES)
        _positive_limit(max_files, "max_files")
        _validate_prepared(self)
        head = _git_text(
            _run_git(
                self.git_executable,
                (
                    "-C",
                    str(self.worktree_root),
                    "rev-parse",
                    "--verify",
                    "HEAD^{commit}",
                ),
                timeout_seconds=self.timeout_seconds,
            ),
            "worktree HEAD",
        )
        if head != self.base_commit and not include_commits:
            raise GitAuthoringIntegrityError(
                "authoring worktree HEAD differs from its pinned commit"
            )

        if include_commits:
            _run_git(self.git_executable, ("-C", str(self.worktree_root), "merge-base", "--is-ancestor", self.base_commit, head), timeout_seconds=self.timeout_seconds)
        base_ref = self.base_commit if include_commits else "HEAD"
        tracked_paths = _tracked_paths(self, base_ref=base_ref)
        untracked_paths = _untracked_paths(self)
        changed_paths = _merge_paths(tracked_paths, untracked_paths)
        if not changed_paths:
            if allow_empty:
                return b""
            raise GitAuthoringIntegrityError(
                "authoring worktree has no changes to capture"
            )
        if len(changed_paths) > max_files:
            raise GitAuthoringIntegrityError(
                "authoring patch exceeds its changed-file bound"
            )
        _audit_changed_paths(self.worktree_root, changed_paths)
        patch = _capture_readonly_patch(
            self,
            tracked_paths=tracked_paths,
            untracked_paths=untracked_paths,
            max_bytes=max_bytes,
            base_ref=base_ref,
        )
        if not patch:
            raise GitAuthoringIntegrityError(
                "authoring worktree produced an empty patch"
            )
        if len(patch) > max_bytes:
            raise GitAuthoringIntegrityError("authoring patch exceeds its byte bound")
        if _merge_paths(_tracked_paths(self, base_ref=base_ref), _untracked_paths(self)) != changed_paths:
            raise GitAuthoringIntegrityError(
                "authoring worktree changed while it was captured"
            )
        return patch

    def cleanup(self) -> None:
        """Remove only this exact Attempt root; repeated cleanup is harmless."""

        _remove_attempt_root(
            attempt_id=self.attempt_id,
            attempt_root=self.attempt_root,
            workspace_root=self.workspace_root,
        )


class GitAuthoringWorkspace:
    """Prepare isolated private-clone worktrees below one existing state root."""

    def __init__(
        self,
        state_root: str | os.PathLike[str],
        *,
        git_executable: str | os.PathLike[str] = "git",
        timeout_seconds: float = 30.0,
    ) -> None:
        raw_state_root = Path(state_root)
        _real_directory(raw_state_root, "state root", error=ValueError)
        self.state_root = raw_state_root.resolve()
        self.git_executable = _resolve_executable(git_executable)
        self.timeout_seconds = _positive_timeout(timeout_seconds)
        self.root = self.state_root / _ATTEMPT_ROOT_NAME
        if self.root.exists() or self.root.is_symlink():
            _real_directory(self.root, "Git authoring root", error=ValueError)
        if self.root.parent != self.state_root:
            raise ValueError("Git authoring root must be a direct child of state_root")

    def cleanup_attempt(self, attempt_id: str) -> None:
        """Remove one exact Attempt root; absent residue is already clean."""

        _safe_component(attempt_id, "attempt_id")
        _remove_attempt_root(
            attempt_id=attempt_id,
            attempt_root=self.root / attempt_id,
            workspace_root=self.root,
        )

    def retained(self, attempt_id: str, *, base_commit: str) -> PreparedGitAuthoringWorkspace:
        """Open an existing private repository, including a retired checkout."""
        _safe_component(attempt_id, "attempt_id")
        if _COMMIT_RE.fullmatch(base_commit) is None:
            raise GitAuthoringIntegrityError("retained base must be one exact commit")
        root = self.root / attempt_id
        prepared = PreparedGitAuthoringWorkspace(
            attempt_id=attempt_id, workspace_root=self.root, attempt_root=root,
            repository_root=root / _PRIVATE_REPOSITORY_NAME,
            worktree_root=root / _WORKTREE_NAME, base_commit=base_commit,
            git_executable=self.git_executable, timeout_seconds=self.timeout_seconds,
            repository_config_snapshot=_read_small_regular(root / _PRIVATE_REPOSITORY_NAME / "config", "private Git configuration"),
        )
        _validate_repository(prepared)
        self._repository_git(prepared, "cat-file", "-e", base_commit + "^{commit}")
        return prepared

    def _repository_git(self, prepared: PreparedGitAuthoringWorkspace, *arguments: str) -> bytes:
        return _run_git(self.git_executable, ("--git-dir", str(prepared.repository_root), *arguments),
                        timeout_seconds=self.timeout_seconds)

    def materialize(self, attempt_id: str, *, base_commit: str, branch: str) -> PreparedGitAuthoringWorkspace:
        """Recreate a missing hot checkout from its retained ordinary branch."""
        prepared = self.retained(attempt_id, base_commit=base_commit)
        self._repository_git(prepared, "check-ref-format", "refs/heads/" + branch)
        self._repository_git(prepared, "rev-parse", "--verify", "refs/heads/" + branch + "^{commit}")
        if not _lexists(prepared.worktree_root):
            # A crash after removing files may leave Git's own worktree admin.
            self._repository_git(prepared, "worktree", "prune", "--expire", "now")
            self._repository_git(prepared, "worktree", "add", str(prepared.worktree_root), branch)
        _validate_prepared(prepared)
        actual = _run_git(self.git_executable, ("-C", str(prepared.worktree_root), "branch", "--show-current"),
                          timeout_seconds=self.timeout_seconds).decode().strip()
        if actual != branch:
            raise GitAuthoringIntegrityError("persistent chain branch differs from its owner")
        return prepared

    def retire_worktree(self, attempt_id: str, *, branch: str) -> bool:
        """Remove only a clean hot checkout; its private Git history remains."""
        _safe_component(attempt_id, "attempt_id")
        root = self.root / attempt_id
        if not _lexists(root / _WORKTREE_NAME):
            return False
        commit = _run_git(self.git_executable, ("--git-dir", str(root / _PRIVATE_REPOSITORY_NAME),
            "rev-parse", "--verify", "refs/heads/" + branch + "^{commit}"), timeout_seconds=self.timeout_seconds).decode().strip()
        prepared = self.materialize(attempt_id, base_commit=commit, branch=branch)
        dirty = _run_git(self.git_executable, ("-C", str(prepared.worktree_root), "status", "--porcelain",
            "--untracked-files=all", "--ignored"), timeout_seconds=self.timeout_seconds)
        if dirty:
            raise GitAuthoringIntegrityError("dirty chain worktree is retained")
        self._repository_git(prepared, "worktree", "remove", str(prepared.worktree_root))
        return True

    def prepare_from_retained(self, source: PreparedGitAuthoringWorkspace, attempt_id: str, *, commit: str) -> PreparedGitAuthoringWorkspace:
        """Internal session transfer; external GitTargetBinding stays checkout-only."""
        _validate_repository(source)
        if _COMMIT_RE.fullmatch(commit) is None:
            raise GitAuthoringIntegrityError("session transfer requires one exact commit")
        return self._prepare(source.repository_root, commit, attempt_id, None, bare_source=True)

    def prepare(
        self,
        binding: GitTargetBinding,
        attempt_id: str,
        patch: bytes | None = None,
    ) -> PreparedGitAuthoringWorkspace:
        """Clone the binding privately and detach a worktree at its exact commit."""

        checkout, commit = _validate_binding(binding)
        return self._prepare(checkout, commit, attempt_id, patch)

    def _prepare(self, checkout: Path, commit: str, attempt_id: str, patch: bytes | None,
                 *, bare_source: bool = False) -> PreparedGitAuthoringWorkspace:
        _safe_component(attempt_id, "attempt_id")
        patch_bytes = _validate_patch(patch)
        try:
            self.root.mkdir(mode=0o700)
        except FileExistsError:
            pass
        _real_directory(self.root, "Git authoring root")
        if _contains(checkout, self.state_root) or _contains(self.state_root, checkout):
            raise GitAuthoringPreparationError(
                "binding checkout and state_root must be disjoint"
            )
        attempt_root = self.root / attempt_id
        try:
            attempt_root.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise GitAuthoringPreparationError(
                f"Git authoring Attempt already exists: {attempt_id}"
            ) from exc

        repository_root = attempt_root / _PRIVATE_REPOSITORY_NAME
        worktree_root = attempt_root / _WORKTREE_NAME
        try:
            if bare_source:
                if _run_git(self.git_executable, ("--git-dir", str(checkout), "rev-parse", "--is-bare-repository"),
                            timeout_seconds=self.timeout_seconds).strip() != b"true":
                    raise GitAuthoringPreparationError("session source is not its private bare repository")
            else:
                _verify_checkout_root(self.git_executable, checkout, self.timeout_seconds)
            _run_git(
                self.git_executable,
                (
                    "clone",
                    "--bare",
                    "--no-hardlinks",
                    "--",
                    str(checkout),
                    str(repository_root),
                ),
                timeout_seconds=self.timeout_seconds,
            )
            _real_directory(repository_root, "private bare repository")
            alternates = repository_root / "objects" / "info" / "alternates"
            if alternates.exists() or alternates.is_symlink():
                raise GitAuthoringPreparationError(
                    "private clone uses an alternate object store"
                )
            _run_git(
                self.git_executable,
                ("-C", str(repository_root), "remote", "remove", "origin"),
                timeout_seconds=self.timeout_seconds,
            )
            if _run_git(
                self.git_executable,
                ("-C", str(repository_root), "remote"),
                timeout_seconds=self.timeout_seconds,
            ):
                raise GitAuthoringPreparationError(
                    "private bare repository retained a remote"
                )
            resolved = _git_text(
                _run_git(
                    self.git_executable,
                    (
                        "-C",
                        str(repository_root),
                        "rev-parse",
                        "--verify",
                        f"{commit}^{{commit}}",
                    ),
                    timeout_seconds=self.timeout_seconds,
                ),
                "bound commit",
            )
            if resolved != commit:
                raise GitAuthoringPreparationError(
                    "bound commit did not resolve exactly"
                )
            _run_git(
                self.git_executable,
                (
                    "--git-dir",
                    str(repository_root),
                    "worktree",
                    "add",
                    "--detach",
                    str(worktree_root),
                    commit,
                ),
                timeout_seconds=self.timeout_seconds,
            )
            prepared = PreparedGitAuthoringWorkspace(
                attempt_id=attempt_id,
                workspace_root=self.root,
                attempt_root=attempt_root,
                repository_root=repository_root,
                worktree_root=worktree_root,
                base_commit=commit,
                git_executable=self.git_executable,
                timeout_seconds=self.timeout_seconds,
                repository_config_snapshot=_read_small_regular(
                    repository_root / "config", "private Git configuration"
                ),
            )
            _validate_prepared(prepared)
            if patch_bytes is not None:
                apply_arguments = (
                    "-C",
                    str(worktree_root),
                    "apply",
                    "--index",
                    "--binary",
                    "--whitespace=nowarn",
                )
                _run_git(
                    self.git_executable,
                    (*apply_arguments, "--check", "-"),
                    timeout_seconds=self.timeout_seconds,
                    input_bytes=patch_bytes,
                )
                _run_git(
                    self.git_executable,
                    (*apply_arguments, "-"),
                    timeout_seconds=self.timeout_seconds,
                    input_bytes=patch_bytes,
                )
                _audit_changed_paths(worktree_root, _changed_paths(prepared))
        except BaseException:
            _remove_attempt_root(
                attempt_id=attempt_id,
                attempt_root=attempt_root,
                workspace_root=self.root,
            )
            raise
        return prepared


def _git_environment() -> dict[str, str]:
    return {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.defpath,
    }


def _run_git(
    git: str,
    arguments: Sequence[str],
    *,
    timeout_seconds: float,
    input_bytes: bytes | None = None,
    allowed_return_codes: Sequence[int] = (0,),
    max_output_bytes: int = _MAX_GIT_OUTPUT_BYTES,
) -> bytes:
    if not arguments or any(
        not isinstance(argument, str) or not argument or "\x00" in argument
        for argument in arguments
    ):
        raise ValueError("Git arguments must be non-empty NUL-free text")
    environment = _git_environment()
    argv = (
        git,
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "filter.lfs.process=",
        "-c",
        "filter.lfs.smudge=",
        "-c",
        "filter.lfs.required=false",
        "-c",
        "submodule.recurse=false",
        *arguments,
    )
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            start_new_session=True,
        )
    except OSError as exc:
        raise GitAuthoringPreparationError(f"git could not run: {exc}") from exc
    try:
        stdout, stderr = _bounded_communicate(
            process,
            input_bytes=input_bytes,
            timeout_seconds=timeout_seconds,
            stdout_limit=max_output_bytes,
            stderr_limit=_MAX_GIT_OUTPUT_BYTES,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitAuthoringPreparationError(
            f"git {arguments[0]} exceeded its execution timeout"
        ) from exc
    else:
        _kill_process_group(process)
    if process.returncode not in allowed_return_codes:
        detail = stderr[:2048].decode("utf-8", errors="replace").strip()
        suffix = f": {detail}" if detail else ""
        raise GitAuthoringPreparationError(
            f"git command failed ({arguments[0]}){suffix}"
        )
    return stdout


def _bounded_communicate(
    process: subprocess.Popen[bytes],
    *,
    input_bytes: bytes | None,
    timeout_seconds: float,
    stdout_limit: int,
    stderr_limit: int,
) -> tuple[bytes, bytes]:
    if process.stdout is None or process.stderr is None or process.stdin is None:
        _kill_process_group(process)
        raise GitAuthoringIntegrityError("Git subprocess pipes are unavailable")
    deadline = time.monotonic() + timeout_seconds
    streams = selectors.DefaultSelector()
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": stdout_limit, "stderr": stderr_limit}
    streams.register(process.stdout, selectors.EVENT_READ, "stdout")
    streams.register(process.stderr, selectors.EVENT_READ, "stderr")
    pending_input = memoryview(input_bytes or b"")
    if pending_input:
        streams.register(process.stdin, selectors.EVENT_WRITE, "stdin")
    else:
        process.stdin.close()
    try:
        while streams.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout_seconds)
            events = streams.select(remaining)
            if not events:
                raise subprocess.TimeoutExpired(process.args, timeout_seconds)
            for key, _mask in events:
                if key.data == "stdin":
                    try:
                        written = os.write(key.fileobj.fileno(), pending_input[:65536])
                    except BrokenPipeError:
                        written = len(pending_input)
                    pending_input = pending_input[written:]
                    if not pending_input:
                        streams.unregister(key.fileobj)
                        key.fileobj.close()
                    continue
                chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                if not chunk:
                    streams.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                output = outputs[key.data]
                if len(output) + len(chunk) > limits[key.data]:
                    label = "Git output" if key.data == "stdout" else "Git error output"
                    raise GitAuthoringIntegrityError(f"{label} exceeded its byte bound")
                output.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout_seconds)
        process.wait(timeout=remaining)
        return bytes(outputs["stdout"]), bytes(outputs["stderr"])
    except BaseException:
        _kill_process_group(process)
        raise
    finally:
        streams.close()
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None and not pipe.closed:
                pipe.close()


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired as exc:
        raise GitAuthoringIntegrityError("Git subprocess did not terminate") from exc


def _validate_binding(binding: GitTargetBinding) -> tuple[Path, str]:
    if not isinstance(binding, GitTargetBinding):
        raise TypeError("binding must be an execution_workspace.GitTargetBinding")
    if not isinstance(binding.checkout, Path):
        raise GitAuthoringPreparationError("binding checkout must be a Path")
    _real_directory(
        binding.checkout, "binding checkout", error=GitAuthoringPreparationError
    )
    checkout = binding.checkout.resolve()
    if binding.checkout != checkout:
        raise GitAuthoringPreparationError("binding checkout must name its real path")
    if (
        not isinstance(binding.commit, str)
        or _COMMIT_RE.fullmatch(binding.commit) is None
    ):
        raise GitAuthoringPreparationError("binding commit must be one exact object ID")
    return checkout, binding.commit


def _verify_checkout_root(git: str, checkout: Path, timeout_seconds: float) -> None:
    root = Path(
        _git_text(
            _run_git(
                git,
                ("-C", str(checkout), "rev-parse", "--show-toplevel"),
                timeout_seconds=timeout_seconds,
            ),
            "checkout root",
        )
    ).resolve()
    if root != checkout:
        raise GitAuthoringPreparationError(
            "binding checkout must name a repository root"
        )


def _validate_patch(patch: bytes | None) -> bytes | None:
    if patch is None:
        return None
    if not isinstance(patch, bytes) or not patch:
        raise ValueError("patch must be non-empty bytes or None")
    if len(patch) > _MAX_PATCH_BYTES:
        raise ValueError("patch exceeds its preparation size bound")
    return patch


def _validate_repository(prepared: PreparedGitAuthoringWorkspace) -> None:
    _safe_component(prepared.attempt_id, "attempt_id")
    _real_directory(prepared.workspace_root, "Git authoring root")
    expected_attempt = prepared.workspace_root / prepared.attempt_id
    if prepared.attempt_root != expected_attempt:
        raise GitAuthoringIntegrityError("Attempt root is not the exact prepared path")
    _real_directory(prepared.attempt_root, "Git authoring Attempt root")
    if prepared.attempt_root.resolve().parent != prepared.workspace_root.resolve():
        raise GitAuthoringIntegrityError("Attempt root escaped the Git authoring root")
    if prepared.repository_root != prepared.attempt_root / _PRIVATE_REPOSITORY_NAME:
        raise GitAuthoringIntegrityError("private repository path is not canonical")
    if prepared.worktree_root != prepared.attempt_root / _WORKTREE_NAME:
        raise GitAuthoringIntegrityError("worktree path is not canonical")
    _real_directory(prepared.repository_root, "private bare repository")
    if prepared.repository_root.resolve().parent != prepared.attempt_root.resolve():
        raise GitAuthoringIntegrityError("private repository escaped its Attempt root")
    if (
        _read_small_regular(
            prepared.repository_root / "config", "private Git configuration"
        )
        != prepared.repository_config_snapshot
    ):
        raise GitAuthoringIntegrityError("private Git configuration changed")
    if _lexists(prepared.repository_root / "config.worktree"):
        raise GitAuthoringIntegrityError("private worktree configuration is forbidden")


def _validate_prepared(prepared: PreparedGitAuthoringWorkspace) -> None:
    _validate_repository(prepared)
    _real_directory(prepared.worktree_root, "authoring worktree")
    if prepared.worktree_root.resolve().parent != prepared.attempt_root.resolve():
        raise GitAuthoringIntegrityError("worktree escaped its Attempt root")
    dot_git = prepared.worktree_root / ".git"
    content = _read_small_regular(dot_git, "worktree Git link")
    if not content.startswith(b"gitdir: ") or not content.endswith(b"\n"):
        raise GitAuthoringIntegrityError("worktree Git link has an invalid shape")
    raw_admin = os.fsdecode(content[len(b"gitdir: ") : -1])
    if not Path(raw_admin).is_absolute():
        raise GitAuthoringIntegrityError("worktree Git link must be absolute")
    admin_root = Path(raw_admin).resolve(strict=False)
    expected_admin = prepared.repository_root / "worktrees" / _WORKTREE_NAME
    if admin_root != expected_admin:
        raise GitAuthoringIntegrityError(
            "worktree Git link escaped its private repository"
        )
    _real_directory(admin_root, "worktree Git admin directory")
    raw_common = os.fsdecode(
        _read_small_regular(
            admin_root / "commondir", "Git common-directory link"
        ).strip()
    )
    common_path = Path(raw_common)
    common_root = (
        common_path if common_path.is_absolute() else admin_root / common_path
    ).resolve(strict=False)
    if common_root != prepared.repository_root:
        raise GitAuthoringIntegrityError(
            "worktree common directory escaped its private repository"
        )
    if _lexists(admin_root / "config.worktree"):
        raise GitAuthoringIntegrityError("private worktree configuration is forbidden")
    raw_back = os.fsdecode(
        _read_small_regular(admin_root / "gitdir", "Git admin link").strip()
    )
    if not Path(raw_back).is_absolute():
        raise GitAuthoringIntegrityError("Git admin link must be absolute")
    if Path(raw_back).resolve(strict=False) != dot_git.resolve(strict=False):
        raise GitAuthoringIntegrityError("Git admin link escaped its worktree")


def _tracked_paths(prepared: PreparedGitAuthoringWorkspace, *, base_ref: str = "HEAD") -> tuple[str, ...]:
    payload = _run_git(
        prepared.git_executable,
        (
            "-C",
            str(prepared.worktree_root),
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            base_ref,
            "--",
        ),
        timeout_seconds=prepared.timeout_seconds,
    )
    return tuple(_decode_git_path(raw) for raw in _nul_records(payload))


def _untracked_paths(prepared: PreparedGitAuthoringWorkspace) -> tuple[str, ...]:
    payload = _run_git(
        prepared.git_executable,
        (
            "-C",
            str(prepared.worktree_root),
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
        ),
        timeout_seconds=prepared.timeout_seconds,
    )
    return tuple(_decode_git_path(raw) for raw in _nul_records(payload))


def _changed_paths(prepared: PreparedGitAuthoringWorkspace) -> tuple[str, ...]:
    return _merge_paths(_tracked_paths(prepared), _untracked_paths(prepared))


def _merge_paths(*groups: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({path for group in groups for path in group}, key=os.fsencode))


def _capture_readonly_patch(
    prepared: PreparedGitAuthoringWorkspace,
    *,
    tracked_paths: Sequence[str],
    untracked_paths: Sequence[str],
    max_bytes: int,
    base_ref: str = "HEAD",
) -> bytes:
    common = (
        "--binary",
        "--full-index",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
    )
    parts: list[bytes] = []
    if tracked_paths:
        parts.append(
            _run_git(
                prepared.git_executable,
                (
                    "-C",
                    str(prepared.worktree_root),
                    "diff",
                    *common,
                    base_ref,
                    "--",
                ),
                timeout_seconds=prepared.timeout_seconds,
                max_output_bytes=max_bytes + 1,
            )
        )
    for relative in untracked_paths:
        remaining = max_bytes + 1 - sum(len(part) for part in parts)
        if remaining <= 0:
            raise GitAuthoringIntegrityError("authoring patch exceeds its byte bound")
        parts.append(
            _run_git(
                prepared.git_executable,
                (
                    "-C",
                    str(prepared.worktree_root),
                    "diff",
                    "--no-index",
                    *common,
                    "--",
                    "/dev/null",
                    relative,
                ),
                timeout_seconds=prepared.timeout_seconds,
                allowed_return_codes=(1,),
                max_output_bytes=remaining,
            )
        )
    return b"".join(parts)


def _audit_changed_paths(root: Path, paths: Sequence[str]) -> None:
    _real_directory(root, "authoring worktree")
    resolved_root = root.resolve()
    for relative in paths:
        parts = PurePosixPath(_safe_relative(relative)).parts
        current = root
        for part in parts[:-1]:
            current /= part
            try:
                metadata = current.lstat()
            except OSError as exc:
                raise GitAuthoringIntegrityError(
                    f"changed path has an unavailable parent: {relative!r}"
                ) from exc
            if not stat.S_ISDIR(metadata.st_mode) or current.is_symlink():
                raise GitAuthoringIntegrityError(
                    f"changed path crosses a non-directory parent: {relative!r}"
                )
            try:
                current.resolve().relative_to(resolved_root)
            except (OSError, RuntimeError, ValueError) as exc:
                raise GitAuthoringIntegrityError(
                    f"changed path escaped the authoring worktree: {relative!r}"
                ) from exc
        path = root.joinpath(*parts)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise GitAuthoringIntegrityError(
                f"changed path is unavailable: {relative!r}"
            ) from exc
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise GitAuthoringIntegrityError(
                f"changed path must be one regular single-link file: {relative!r}"
            )


def _remove_attempt_root(
    *, attempt_id: str, attempt_root: Path, workspace_root: Path
) -> None:
    _safe_component(attempt_id, "attempt_id")
    if not workspace_root.exists() and not workspace_root.is_symlink():
        return
    _real_directory(workspace_root, "Git authoring root")
    expected = workspace_root / attempt_id
    if attempt_root != expected:
        raise GitAuthoringIntegrityError("cleanup target is not the exact Attempt root")
    if not attempt_root.exists() and not attempt_root.is_symlink():
        return
    if attempt_root.is_symlink() or not attempt_root.is_dir():
        raise GitAuthoringIntegrityError(
            "cleanup target is not a real Attempt directory"
        )
    if attempt_root.resolve().parent != workspace_root.resolve():
        raise GitAuthoringIntegrityError(
            "cleanup target escaped the Git authoring root"
        )
    shutil.rmtree(attempt_root)


def _real_directory(
    path: Path, label: str, *, error: type[Exception] = GitAuthoringIntegrityError
) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise error(f"{label} must be an existing real directory") from exc
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        raise error(f"{label} must be an existing real directory")


def _read_small_regular(path: Path, label: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise GitAuthoringIntegrityError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > 4096
    ):
        raise GitAuthoringIntegrityError(f"{label} must be one small regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        content = os.read(descriptor, metadata.st_size + 1)
    finally:
        os.close(descriptor)
    if len(content) != metadata.st_size:
        raise GitAuthoringIntegrityError(f"{label} changed while it was read")
    return content


def _nul_records(payload: bytes) -> tuple[bytes, ...]:
    if not payload:
        return ()
    if not payload.endswith(b"\0"):
        raise GitAuthoringIntegrityError("Git returned an invalid path inventory")
    records = tuple(payload[:-1].split(b"\0"))
    if any(not record for record in records):
        raise GitAuthoringIntegrityError("Git returned an invalid path inventory")
    return records


def _decode_git_path(raw: bytes) -> str:
    value = os.fsdecode(raw)
    if os.fsencode(value) != raw:
        raise GitAuthoringIntegrityError("Git returned a non-round-tripping path")
    return _safe_relative(value)


def _safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise GitAuthoringIntegrityError("changed path is not a safe relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(
            part in {"", ".", ".."} or part.casefold() == ".git" for part in path.parts
        )
    ):
        raise GitAuthoringIntegrityError("changed path is not a safe relative path")
    return value


def _safe_component(value: str, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise ValueError(f"{label} must be one safe path component")


def _git_text(payload: bytes, label: str) -> str:
    if not payload.endswith(b"\n") or b"\x00" in payload:
        raise GitAuthoringIntegrityError(f"Git returned an invalid {label}")
    try:
        text = payload[:-1].decode("ascii")
    except UnicodeDecodeError as exc:
        raise GitAuthoringIntegrityError(f"Git returned an invalid {label}") from exc
    if not text or "\n" in text or "\r" in text:
        raise GitAuthoringIntegrityError(f"Git returned an invalid {label}")
    return text


def _resolve_executable(value: str | os.PathLike[str]) -> str:
    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise ValueError("git_executable must be non-empty NUL-free text")
    resolved = shutil.which(raw)
    if resolved is None or not os.access(resolved, os.X_OK):
        raise ValueError(f"Git executable is unavailable: {raw}")
    return str(Path(resolved).resolve())


def _positive_timeout(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError("timeout_seconds must be a finite positive number")
    return float(value)


def _positive_limit(value: int, label: str, *, maximum: int | None = None) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} exceeds the supported bound")


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
    except ValueError:
        return False
    return True


def _lexists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


__all__ = [
    "GIT_AUTHORING_WORKSPACE_VERSION",
    "GitAuthoringIntegrityError",
    "GitAuthoringPreparationError",
    "GitAuthoringWorkspace",
    "GitAuthoringWorkspaceError",
    "PreparedGitAuthoringWorkspace",
]
