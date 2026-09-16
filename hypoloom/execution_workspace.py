"""Filesystem and bubblewrap adapter for one local experiment attempt.

The adapter owns candidate bytes and OS launch construction only.  It has no
store handle and makes no workflow, retry, attempt-selection, or outcome
decision.
"""

from __future__ import annotations

import ast
import contextlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence

from .agent_aci import AgentRequest, validate_postflight, validate_submission_snapshot
from .component_contract import downstream_source_contract_plans
from .domain import AttemptTerminalReason
from .outcome_adapter import ComponentResultValidationError
from .runner import ProcessObservation, ProcessRequest


EXECUTION_WORKSPACE_VERSION = "bwrap-workspace/3"
_GIT_OUTPUT_LIMIT = 8 * 1024 * 1024
_PATCH_SIZE_LIMIT = 64 * 1024 * 1024

EXECUTION_ENVIRONMENT_CONTRACT_VERSION = "execution-environment/1"


def execution_read_root_projection(
    execution: Mapping[str, Any], plan: Mapping[str, Any], *, generic: bool,
) -> tuple[tuple[tuple[Path, str], ...], dict[str, str]]:
    """Mechanically transport admitted roots; grant no additional filesystem access.

    Generic experiments need ignored data from an explicitly authorized checkout
    without mounting it over the submitted code. Production drivers retain their
    separate data_bindings protocol. Validation still belongs to the existing
    admission and execution-mount validators.
    """
    roots = tuple(Path(root) for root in execution["read_roots"])
    environment = {"HYPOLOOM_READ_ROOTS": json.dumps(execution["read_roots"], separators=(",", ":"))}
    target = execution.get("target")
    outer: list[tuple[Path, str]] = []
    if generic and target is not None and not plan.get("production") and not plan.get("data_bindings"):
        checkout = Path(target["checkout"])
        intersecting = [root for root in roots if root == checkout or root in checkout.parents or checkout in root.parents]
        outer = [(root, f"/hypoloom-read-roots/{index}") for index, root in enumerate(roots, 1)
            if root in intersecting and not any(other in root.parents for other in intersecting)]
    if not outer:
        return tuple((root, str(root)) for root in roots), environment
    bindings: list[tuple[Path, str]] = []
    mapping: dict[str, str] = {}
    for root in roots:
        owner = next(((parent, alias) for parent, alias in outer if root == parent or parent in root.parents), None)
        if owner is None:
            bindings.append((root, str(root)))
        else:
            parent, alias = owner
            mapping[str(root)] = str(Path(alias) / root.relative_to(parent))
            if root == parent:
                bindings.append((root, alias))
    environment["HYPOLOOM_READ_ROOTS"] = json.dumps([target for _, target in bindings], separators=(",", ":"))
    environment["HYPOLOOM_READ_ROOT_MAP"] = json.dumps(mapping, sort_keys=True, separators=(",", ":"))
    return tuple(bindings), environment


def execution_environment_contract(sandbox_profile: str) -> dict[str, Any]:
    """Advisory capability contract of one execution sandbox profile.

    These facts mirror _sandbox_argv and _environment; keep them synchronized
    with both.  They exist so design Agents never have to rediscover the
    sandbox environment through failed executions.
    """
    contract: dict[str, Any] = {
        "contract_version": EXECUTION_ENVIRONMENT_CONTRACT_VERSION,
        "sandbox_profile": sandbox_profile,
        "network": "none",
        "working_directory": "/output",
        "writable_roots": ["/output", "/tmp"],
        "read_only_roots": ["/input", "/runtime"],
        "mounted_inputs": {
            "/input": "committed workspace artifacts (experiment.py, implementation.patch); read-only",
            "/runtime": "pinned Python interpreter environment; read-only",
        },
        "data_roots": "HYPOLOOM_READ_ROOTS lists actual read-only sandbox data roots; optional HYPOLOOM_READ_ROOT_MAP maps admitted original paths to data aliases. Import submitted code through HYPOLOOM_TARGET_ROOT, never an original-checkout data alias. Never create or modify files in these read-only mounts",
        "available_executables": "Generic C5/GOAL executions additionally expose the existing /usr/bin and /bin read-only (including available shells and coreutils); preset drivers retain the pinned Python runtime under /runtime without added git, shell, or coreutils. Do not install software or modify runtime/system directories",
        "environment_variables": {
            "fixed": ["HOME=/nonexistent", "PYTHONDONTWRITEBYTECODE=1", "PYTHONNOUSERSITE=1", "TMPDIR=/tmp"],
            "from_execution_contract": True,
            "empty_valued_entries_are_unset": True,
            "result_path": "HYPOLOOM_RESULT_PATH names the result file relative to /output (result.json)",
        },
        "gpu": ("each granted GPU has its exact /dev/nvidia* device bound; CUDA_VISIBLE_DEVICES lists only the frozen granted UUIDs in canonical order"
                if "gpu" in sandbox_profile else None),
    }
    return contract


class ExecutionWorkspaceError(RuntimeError):
    pass


class WorkspaceIntegrityError(ExecutionWorkspaceError):
    pass


class TargetPreparationError(WorkspaceIntegrityError):
    pass


class SandboxUnavailable(ExecutionWorkspaceError):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class GitTargetBinding:
    checkout: Path
    commit: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionPaths:
    attempt_id: str
    attempt_root: Path
    input_dir: Path
    output_dir: Path
    log_dir: Path
    staging_root: Path


@dataclass(frozen=True, slots=True, kw_only=True)
class PreparedExecution:
    attempt_id: str
    agent_attempt_id: str
    agent_output_digest: str
    attempt_root: Path
    input_dir: Path
    output_dir: Path
    log_dir: Path
    staging_root: Path
    source_relative_path: str
    result_relative_path: str
    read_only_bindings: tuple[tuple[Path, str], ...]
    trusted_workspace_files: tuple[tuple[str, bytes], ...]
    process_request: ProcessRequest


@dataclass(frozen=True, slots=True, kw_only=True)
class PreparedAgentWorkspace:
    """Disposable file-tool workspace for one DRAFT Agent Attempt.

    The writable draft directory is deliberately separate from the verified,
    read-only target overlay.  It is transient transport state, not a second
    Agent response or research-state authority.
    """

    attempt_id: str
    artifact_root: Path
    attempt_root: Path
    writable_root: Path
    target_root: Path | None

    def capture_source(self, *, max_bytes: int) -> bytes:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        _real_directory(self.writable_root, "Agent draft workspace")
        entries = tuple(self.writable_root.iterdir())
        if len(entries) != 1 or entries[0].name != "experiment.py":
            raise WorkspaceIntegrityError(
                "Agent draft workspace must contain exactly experiment.py"
            )
        source = entries[0]
        metadata = source.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise WorkspaceIntegrityError(
                "Agent draft experiment.py must be one regular non-linked file"
            )
        if metadata.st_size <= 0:
            raise WorkspaceIntegrityError("Agent draft experiment.py is empty")
        if metadata.st_size > max_bytes:
            raise WorkspaceIntegrityError("Agent draft experiment.py exceeds its size bound")
        content = _read_regular(source)
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceIntegrityError(
                "Agent draft experiment.py is not valid UTF-8"
            ) from exc
        return content

    def cleanup(self) -> None:
        _remove_tree(self.attempt_root, self.artifact_root)


@dataclass(frozen=True, slots=True, kw_only=True)
class CollectedExecution:
    staging_dir: Path
    relative_paths: tuple[str, ...]
    stdout_path: Path | None
    stderr_path: Path | None


def bind_clean_git_target(
    checkout: str | os.PathLike[str],
    *,
    git_executable: str | os.PathLike[str] = "git",
    timeout_seconds: float = 15.0,
) -> GitTargetBinding:
    """Read one clean repository root and return Git's commit identity."""

    timeout = _git_timeout(timeout_seconds)
    git = _git_executable(git_executable)
    raw_checkout = Path(checkout)
    if raw_checkout.is_symlink() or not raw_checkout.is_dir():
        raise TargetPreparationError("target checkout must be an existing real directory")
    checkout_root = raw_checkout.resolve()
    repository_root = Path(
        _git_text(
            _run_git(
                git,
                checkout_root,
                ("rev-parse", "--show-toplevel"),
                timeout_seconds=timeout,
            ),
            "repository root",
        )
    ).resolve()
    if repository_root != checkout_root:
        raise TargetPreparationError("target checkout must name the repository root")
    commit = _resolve_target_commit(git, repository_root, timeout)
    dirty = _run_git(
        git,
        repository_root,
        (
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignore-submodules=none",
        ),
        timeout_seconds=timeout,
    )
    if dirty:
        raise TargetPreparationError("target checkout is dirty")
    if _resolve_target_commit(git, repository_root, timeout) != commit:
        raise TargetPreparationError("target HEAD changed while it was being bound")
    return GitTargetBinding(checkout=repository_root, commit=commit)


class ExecutionWorkspace:
    """Materialize one committed Agent response into an isolated local launch."""

    def __init__(
        self,
        artifact_root: str | os.PathLike[str],
        *,
        state_root: str | os.PathLike[str],
        bwrap_path: str | os.PathLike[str] = "/usr/bin/bwrap",
        python_executable: str | os.PathLike[str] = sys.executable,
        runtime_prefix: str | os.PathLike[str] = sys.prefix,
        system_runtime_paths: Sequence[str | os.PathLike[str]] | None = None,
        device_bindings: Sequence[
            tuple[str | os.PathLike[str], str | os.PathLike[str]]
        ] = (),
        fixed_environment: Mapping[str, str] | None = None,
    ) -> None:
        raw_root = Path(artifact_root)
        if raw_root.is_symlink() or not raw_root.is_dir():
            raise ValueError("artifact_root must be an existing real directory")
        self.root = raw_root.resolve()
        raw_state = Path(state_root)
        if raw_state.is_symlink() or not raw_state.is_dir():
            raise ValueError("state_root must be an existing real directory")
        self.state_root = raw_state.resolve()
        try:
            self.root.relative_to(self.state_root)
        except ValueError as exc:
            raise ValueError("artifact_root must be beneath state_root") from exc
        self.bwrap_path = Path(bwrap_path)
        self.python_executable = Path(python_executable)
        self.runtime_prefix = Path(runtime_prefix)
        defaults = ("/lib", "/lib64", "/usr/lib", "/etc/ld.so.cache")
        self.system_runtime_paths = tuple(Path(path) for path in (system_runtime_paths or defaults))
        self.device_bindings = _device_bindings(device_bindings)
        fixed = dict(fixed_environment or {})
        fixed.setdefault("CUDA_VISIBLE_DEVICES", "")
        self.fixed_environment = _environment_entries(fixed, "fixed_environment")

    def paths(self, attempt_id: str) -> ExecutionPaths:
        _component(attempt_id, "attempt_id")
        attempt_root = self.root / "work" / attempt_id
        return ExecutionPaths(
            attempt_id=attempt_id,
            attempt_root=attempt_root,
            input_dir=attempt_root / "input",
            output_dir=attempt_root / "output",
            log_dir=attempt_root / "logs",
            staging_root=self.root / "result-staging" / attempt_id,
        )

    def prepare_agent_workspace(
        self,
        *,
        attempt_id: str,
        target_binding: GitTargetBinding | None = None,
        target_patch: bytes | None = None,
    ) -> PreparedAgentWorkspace:
        """Create one fresh DRAFT workspace and optional pinned target view."""

        _component(attempt_id, "attempt_id")
        if target_binding is None and target_patch is not None:
            raise ValueError("target_patch requires target_binding")
        paths = self.paths(attempt_id)
        work_root = self.root / "work"
        _ensure_directory(work_root)
        try:
            paths.attempt_root.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise WorkspaceIntegrityError(
                f"attempt workspace already exists: {attempt_id}"
            ) from exc
        writable_root = paths.attempt_root / "draft"
        target_root: Path | None = None
        try:
            writable_root.mkdir(mode=0o700)
            if target_binding is not None:
                target_root = self.materialize_target_overlay(
                    target_binding,
                    attempt_id=attempt_id,
                    patch=target_patch,
                )
            _fsync_directory(writable_root)
            _fsync_directory(paths.attempt_root)
            _fsync_directory(work_root)
        except BaseException:
            _remove_tree(paths.attempt_root, self.root)
            raise
        return PreparedAgentWorkspace(
            attempt_id=attempt_id,
            artifact_root=self.root,
            attempt_root=paths.attempt_root,
            writable_root=writable_root,
            target_root=target_root,
        )

    def capture_agent_source(
        self,
        prepared: PreparedAgentWorkspace,
        *,
        max_bytes: int,
    ) -> bytes:
        """Read the exact single-file DRAFT result after the Agent exits."""

        if not isinstance(prepared, PreparedAgentWorkspace):
            raise TypeError("prepared must be a PreparedAgentWorkspace")
        if prepared.artifact_root != self.root:
            raise WorkspaceIntegrityError("Agent workspace belongs to another root")
        return prepared.capture_source(max_bytes=max_bytes)

    def _validated_overlay_inputs(
        self,
        binding: GitTargetBinding,
        attempt_id: str,
        patch: bytes | None,
        git_executable: str | os.PathLike[str],
        timeout_seconds: float,
    ) -> tuple[Any, Path, bytes | None, float, ExecutionPaths]:
        if not isinstance(binding, GitTargetBinding):
            raise TypeError("binding must be a GitTargetBinding")
        timeout = _git_timeout(timeout_seconds)
        patch_bytes = _validated_patch(patch)
        git = _git_executable(git_executable)
        checkout = _validated_binding(binding)
        if _contains(checkout, self.state_root) or _contains(self.state_root, checkout):
            raise TargetPreparationError(
                "target checkout and Hypoloom state root must be disjoint"
            )
        paths = self.paths(attempt_id)
        _real_directory(self.root / "work", "workspace work root")
        _real_directory(paths.attempt_root, "attempt root")
        return git, checkout, patch_bytes, timeout, paths

    def _checkout_patched_tree(
        self,
        git: Any,
        checkout: Path,
        binding: GitTargetBinding,
        patch_bytes: bytes | None,
        temporary_root: Path,
        timeout: float,
    ) -> Path:
        resolved_commit = _git_text(
            _run_git(
                git,
                checkout,
                ("rev-parse", "--verify", f"{binding.commit}^{{commit}}"),
                timeout_seconds=timeout,
            ),
            "bound target commit",
        )
        if resolved_commit != binding.commit:
            raise TargetPreparationError("bound target commit does not resolve exactly")
        index_path = temporary_root / "index"
        target_copy = temporary_root / "tree"
        target_copy.mkdir(mode=0o700)
        git_index_environment = {"GIT_INDEX_FILE": str(index_path)}
        _run_git(
            git,
            checkout,
            ("read-tree", binding.commit),
            timeout_seconds=timeout,
            extra_environment=git_index_environment,
        )
        _run_git(
            git,
            checkout,
            (
                "checkout-index",
                "--all",
                f"--prefix={target_copy}{os.sep}",
            ),
            timeout_seconds=timeout,
            extra_environment=git_index_environment,
        )
        _validate_target_tree(target_copy)
        _verify_bound_git_tree(git, checkout, binding.commit, target_copy, timeout)
        if patch_bytes is not None:
            _run_git(
                git,
                target_copy,
                (
                    "apply",
                    "--no-index",
                    "--check",
                    "--whitespace=nowarn",
                    "-",
                ),
                timeout_seconds=timeout,
                input_bytes=patch_bytes,
                extra_environment=_standalone_apply_environment(target_copy),
            )
            _run_git(
                git,
                target_copy,
                ("apply", "--no-index", "--whitespace=nowarn", "-"),
                timeout_seconds=timeout,
                input_bytes=patch_bytes,
                extra_environment=_standalone_apply_environment(target_copy),
            )
            _validate_target_tree(target_copy)
        return target_copy

    def materialize_target_overlay(
        self,
        binding: GitTargetBinding,
        *,
        attempt_id: str,
        patch: bytes | None,
        target_directory: str = "target",
        git_executable: str | os.PathLike[str] = "git",
        timeout_seconds: float = 30.0,
    ) -> Path:
        """Build one commit-based, optionally patched Attempt-local target."""

        _component(target_directory, "target_directory")
        git, checkout, patch_bytes, timeout, paths = self._validated_overlay_inputs(
            binding, attempt_id, patch, git_executable, timeout_seconds
        )
        target_root = paths.attempt_root / target_directory
        if target_root.exists() or target_root.is_symlink():
            raise TargetPreparationError("attempt target tree already exists")
        with tempfile.TemporaryDirectory(
            prefix=".target-overlay-", dir=paths.attempt_root
        ) as temporary:
            target_copy = self._checkout_patched_tree(
                git, checkout, binding, patch_bytes, Path(temporary), timeout
            )
            if target_root.exists() or target_root.is_symlink():
                raise TargetPreparationError(
                    "attempt target tree appeared during preparation"
                )
            target_copy.rename(target_root)
            _fsync_directory(paths.attempt_root)
        return target_root

    @contextlib.contextmanager
    def verified_target_tree(
        self,
        binding: GitTargetBinding,
        *,
        attempt_id: str,
        patch: bytes | None,
        git_executable: str | os.PathLike[str] = "git",
        timeout_seconds: float = 30.0,
    ) -> Iterator[Path]:
        """Yield one verified patched target tree for acceptance-time checks.

        The tree is fully verified exactly like materialize_target_overlay but
        lives in a fresh temporary directory that is removed when the block
        exits, so repeat evaluations of the same Attempt never conflict.
        """

        git, checkout, patch_bytes, timeout, paths = self._validated_overlay_inputs(
            binding, attempt_id, patch, git_executable, timeout_seconds
        )
        with tempfile.TemporaryDirectory(
            prefix=".target-verify-", dir=paths.attempt_root
        ) as temporary:
            yield self._checkout_patched_tree(
                git, checkout, binding, patch_bytes, Path(temporary), timeout
            )

    @contextlib.contextmanager
    def verified_detached_target_tree(
        self,
        binding: GitTargetBinding,
        *,
        patch: bytes | None,
        git_executable: str | os.PathLike[str] = "git",
        timeout_seconds: float = 30.0,
    ) -> Iterator[Path]:
        """Yield one verified patched target tree outside any Attempt root.

        Same verified checkout path as verified_target_tree, for host-side
        checks that run before the owning Attempt root exists.  The tree is
        removed when the block exits.
        """

        git = _git_executable(git_executable)
        timeout = _git_timeout(timeout_seconds)
        patch_bytes = _validated_patch(patch)
        checkout = _validated_binding(binding)
        if _contains(checkout, self.state_root) or _contains(self.state_root, checkout):
            raise TargetPreparationError(
                "target checkout and Hypoloom state root must be disjoint"
            )
        _ensure_directory(self.root / "work")
        with tempfile.TemporaryDirectory(
            prefix=".target-verify-", dir=self.root / "work"
        ) as temporary:
            yield self._checkout_patched_tree(
                git, checkout, binding, patch_bytes, Path(temporary), timeout
            )

    def check_target_patch(
        self,
        *,
        attempt_id: str,
        patch: bytes,
        target_directory: str = "target",
        git_executable: str | os.PathLike[str] = "git",
        timeout_seconds: float = 30.0,
    ) -> bool:
        """Check a patch against the already materialized Attempt-local base."""

        _component(target_directory, "target_directory")
        target_root = self.paths(attempt_id).attempt_root / target_directory
        _validate_target_tree(target_root)
        _run_git(
            _git_executable(git_executable), target_root,
            ("apply", "--no-index", "--check", "--whitespace=nowarn", "-"),
            timeout_seconds=_git_timeout(timeout_seconds), input_bytes=_validated_patch(patch),
            extra_environment=_standalone_apply_environment(target_root),
        )
        return True

    def plan(
        self,
        *,
        agent_request: Mapping[str, Any],
        agent_response: Mapping[str, Any],
        attempt_id: str,
        attempt_ordinal: int,
        execution_id: str,
        target_env: Mapping[str, str],
        timeout_seconds: float,
        source_relative_path: str = "experiment.py",
        result_relative_path: str = "result.json",
        execution_argv: Sequence[str] | None = None,
        allow_generic_argv: bool = False,
        trusted_workspace_files: Mapping[str, bytes] | None = None,
        read_only_bindings: Sequence[
            tuple[str | os.PathLike[str], str | os.PathLike[str]]
        ] = (),
    ) -> PreparedExecution:
        source_relative_path = _relative(source_relative_path, "source_relative_path")
        result_relative_path = _relative(result_relative_path, "result_relative_path")
        source_parts = PurePosixPath(source_relative_path).parts
        result_parts = PurePosixPath(result_relative_path).parts
        if (
            source_parts == result_parts[: len(source_parts)]
            or result_parts == source_parts[: len(result_parts)]
        ):
            raise ValueError("source_relative_path and result_relative_path must be disjoint")
        request, response, workspace_files = _committed_workspace(agent_request, agent_response)
        trusted = _trusted_workspace(trusted_workspace_files or {})
        if set(workspace_files) & set(trusted): raise WorkspaceIntegrityError("Host and Agent workspace paths overlap")
        workspace_files = {**workspace_files, **trusted}
        if source_relative_path not in workspace_files:
            raise WorkspaceIntegrityError(f"durable Agent response lacks {source_relative_path}")
        if request.attempt_id == attempt_id:
            raise WorkspaceIntegrityError("Agent and process attempt_id must differ")
        _positive_int(attempt_ordinal, "attempt_ordinal")
        _text(execution_id, "execution_id")
        environment = _environment(target_env, self.fixed_environment)
        if not isinstance(allow_generic_argv, bool):
            raise ValueError("allow_generic_argv must be boolean")
        if allow_generic_argv and isinstance(execution_argv, (str, bytes)):
            raise ValueError("generic execution_argv must be an explicit argv, not a shell string")
        logical_argv = tuple(execution_argv or ("python", source_relative_path))
        if (not logical_argv or (not allow_generic_argv and logical_argv[0] != "python")
                or any(not isinstance(value, str) or not value or "\x00" in value for value in logical_argv)):
            raise ValueError("execution_argv must be a non-empty Python command")
        paths = self.paths(attempt_id)
        bindings = _read_only_bindings(read_only_bindings, paths)
        process_request = ProcessRequest(
            attempt_id=attempt_id,
            attempt_ordinal=attempt_ordinal,
            execution_id=execution_id,
            argv=self._sandbox_argv(paths, source_relative_path, logical_argv, environment, bindings),
            cwd=str(paths.log_dir),
            env={},
            timeout_seconds=timeout_seconds,
            stdout_path="stdout.log",
            stderr_path="stderr.log",
        )
        return _prepared(
            paths,
            source_relative_path,
            result_relative_path,
            process_request,
            agent_attempt_id=request.attempt_id,
            agent_output_digest=response.output_digest,
            read_only_bindings=bindings,
            trusted_workspace_files=tuple(sorted(trusted.items())),
        )

    def materialize(
        self,
        prepared: PreparedExecution,
        *,
        agent_request: Mapping[str, Any],
        agent_response: Mapping[str, Any],
    ) -> None:
        self._check_prepared(prepared)
        self._check_sandbox()
        _, _, workspace_files = self._bound_workspace(prepared, agent_request, agent_response)
        if prepared.source_relative_path not in workspace_files:
            raise WorkspaceIntegrityError(
                f"durable Agent response lacks {prepared.source_relative_path}"
            )
        self._create_attempt(self.paths(prepared.attempt_id), workspace_files)

    def recover(
        self,
        process_request: ProcessRequest | Mapping[str, Any],
        *,
        agent_request: Mapping[str, Any],
        agent_response: Mapping[str, Any],
        target_env: Mapping[str, str],
        timeout_seconds: float,
        source_relative_path: str = "experiment.py",
        result_relative_path: str = "result.json",
        execution_argv: Sequence[str] | None = None,
        allow_generic_argv: bool = False,
        trusted_workspace_files: Mapping[str, bytes] | None = None,
        read_only_bindings: Sequence[
            tuple[str | os.PathLike[str], str | os.PathLike[str]]
        ] = (),
    ) -> PreparedExecution:
        request = (
            process_request
            if isinstance(process_request, ProcessRequest)
            else ProcessRequest.from_dict(process_request)
        )
        expected = self.plan(
            agent_request=agent_request,
            agent_response=agent_response,
            attempt_id=request.attempt_id,
            attempt_ordinal=request.attempt_ordinal,
            execution_id=request.execution_id,
            target_env=target_env,
            timeout_seconds=timeout_seconds,
            source_relative_path=source_relative_path,
            result_relative_path=result_relative_path,
            execution_argv=execution_argv,
            allow_generic_argv=allow_generic_argv,
            trusted_workspace_files=trusted_workspace_files,
            read_only_bindings=read_only_bindings,
        )
        if request != expected.process_request:
            raise WorkspaceIntegrityError(
                "persisted ProcessRequest differs from the canonical execution plan"
            )
        return expected

    def assert_prestart_clean(
        self,
        prepared: PreparedExecution,
        *,
        agent_request: Mapping[str, Any],
        agent_response: Mapping[str, Any],
    ) -> None:
        self._check_prepared(prepared)
        _, _, workspace_files = self._bound_workspace(prepared, agent_request, agent_response)
        self._verify_materialized_inputs(prepared, workspace_files)
        _validate_read_only_sources(prepared.read_only_bindings)
        _real_directory(prepared.output_dir, "output directory")
        with os.scandir(prepared.output_dir) as entries:
            unexpected = sorted(entry.name for entry in entries)
        if unexpected:
            raise WorkspaceIntegrityError(
                f"output exists before process start: {unexpected!r}"
            )

    def assert_component_preflight(
        self, *, plan: Mapping[str, Any], target_root: Path, control_root: Path | None = None
    ) -> None:
        """Fail before spawn unless the Attempt-local target satisfies GoalDefined."""

        if plan.get("production") is not True:
            return
        argv = plan.get("argv")
        target_mount = plan.get("target_root")
        if not isinstance(argv, (list, tuple)) or not isinstance(target_mount, str):
            raise WorkspaceIntegrityError("component execution plan is malformed")
        check_component_source_contract(plan, target_root)
        requirements = plan["requirements"]
        capabilities = plan["capabilities"]
        bound_paths = _component_bound_paths(plan)

        def checked_path(raw: Mapping[str, Any]) -> Path:
            return _checked_component_path(raw, bound_paths, target_root)

        def require_kind(path: Path, kind: object) -> None:
            _require_component_kind(path, kind)

        if plan.get("batch") is True:
            trials = plan.get("driver_plan", {}).get("trials") if isinstance(plan.get("driver_plan"), Mapping) else None
            if not isinstance(trials, list) or not trials:
                raise WorkspaceIntegrityError("batch execution has no frozen trials")
            if plan.get("control_target") is True:
                if control_root is None: raise WorkspaceIntegrityError("paired control target is absent")
                require_kind(_safe_join(control_root, _relative(str(plan["entrypoint_path"]), "control entrypoint")), "file")
            argv_sets = []
            for trial in trials:
                if not isinstance(trial, Mapping) or not isinstance(trial.get("target_argv"), list):
                    raise WorkspaceIntegrityError("batch trial is malformed")
                trial_argv = trial["target_argv"]
                argv_sets.append(trial_argv)
                for capability in capabilities.values():
                    if not isinstance(capability, Mapping) or not isinstance(capability.get("path_flag"), str): continue
                    flag = capability["path_flag"]; positions = [index for index, value in enumerate(trial_argv[:-1]) if value == flag]
                    if len(positions) != 1: raise WorkspaceIntegrityError("trial config binding is absent")
                    value = str(trial_argv[positions[0] + 1]); root, mount = (control_root, "/hypoloom-control-target") if value.startswith("/hypoloom-control-target/") else (target_root, target_mount)
                    if root is None or not value.startswith(str(mount) + "/"): raise WorkspaceIntegrityError("trial config binding escaped target")
                    require_kind(_safe_join(root, _relative(value[len(str(mount)) + 1:], "trial config")), "file")
        else:
            argv_sets = [argv]

        flags = list(requirements.get("required_cli_flags", []))
        flags.extend(
            flag for raw in capabilities.values() if isinstance(raw, Mapping)
            for flag in raw.get("required_cli_flags", [])
        )
        for trial_argv in argv_sets:
            for flag in flags:
                indices = [index for index, value in enumerate(trial_argv[:-1]) if value == flag]
                if len(indices) != 1 or not isinstance(trial_argv[indices[0] + 1], str):
                    raise WorkspaceIntegrityError(f"required CLI binding is absent or ambiguous: {flag}")
        for raw in requirements.get("argv_bindings", []):
            if not isinstance(raw, Mapping):
                raise WorkspaceIntegrityError("argv binding is malformed")
            flag = raw.get("flag"); binding = bound_paths.get((raw.get("scope"), raw.get("path")))
            if raw.get("value") is not None: expected = str(raw["value"])
            elif isinstance(binding, Mapping):
                path = checked_path(raw); require_kind(path, raw.get("kind")); expected = str(binding.get("sandbox"))
            else:
                path = checked_path(raw); require_kind(path, raw.get("kind")); expected = str(path) if raw.get("scope") == "absolute" else str(PurePosixPath(target_mount) / str(raw.get("path")))
            for trial_argv in argv_sets:
                indices = [index for index, value in enumerate(trial_argv[:-1]) if value == flag]
                if len(indices) != 1 or trial_argv[indices[0] + 1] != expected:
                    raise WorkspaceIntegrityError(f"argv binding differs from frozen value: {flag}")

    def collect(
        self,
        prepared: PreparedExecution,
        observation: ProcessObservation | Mapping[str, Any],
        *,
        agent_request: Mapping[str, Any],
        agent_response: Mapping[str, Any],
        preserve_partial_outputs: bool = False,
    ) -> CollectedExecution:
        self._check_prepared(prepared)
        _, _, workspace_files = self._bound_workspace(prepared, agent_request, agent_response)
        self._verify_materialized_inputs(prepared, workspace_files)
        reason = _verify_observation(prepared.process_request, observation)
        output = _inventory(prepared.output_dir)
        result = output.get(prepared.result_relative_path)
        if result is None and reason is not AttemptTerminalReason.SUCCEEDED:
            candidate = ({relative: _read_regular(path) for relative, path in output.items()}
                         if preserve_partial_outputs else {})
            candidate[prepared.result_relative_path] = _json_bytes({"terminal_reason": reason.value})
        else: candidate = {relative: _read_regular(path) for relative, path in output.items()}
        staging_dir = prepared.staging_root / "artifact"
        relative_paths = _stage_candidate(staging_dir, candidate)
        stdout_path, stderr_path = self._stage_logs(prepared, require=True)
        return CollectedExecution(
            staging_dir=staging_dir,
            relative_paths=relative_paths,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )

    def integrity_diagnostic(
        self,
        prepared: PreparedExecution,
        reason: str,
        *,
        agent_request: Mapping[str, Any],
        agent_response: Mapping[str, Any],
    ) -> CollectedExecution:
        self._check_prepared(prepared)
        if not isinstance(reason, str) or not reason or reason != reason.strip() or len(reason) > 1024:
            raise ValueError("reason must be non-empty trimmed text no longer than 1024 characters")
        _, _, workspace_files = self._bound_workspace(prepared, agent_request, agent_response)
        if prepared.source_relative_path not in workspace_files:
            raise WorkspaceIntegrityError("durable Agent response lacks frozen source")
        staging_dir = prepared.staging_root / "integrity"
        relative_paths = _stage_candidate(staging_dir, {
            prepared.result_relative_path: _json_bytes({"integrity_rejected": reason})})
        stdout_path, stderr_path = self._stage_logs(prepared, require=False)
        return CollectedExecution(
            staging_dir=staging_dir,
            relative_paths=relative_paths,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )

    def cleanup_attempt(self, attempt_id: str, *, preserve_logs: bool = False) -> None:
        paths = self.paths(attempt_id)
        _remove_tree(paths.attempt_root, self.root)
        if not paths.staging_root.exists():
            return
        if paths.staging_root.is_symlink() or not paths.staging_root.is_dir():
            raise WorkspaceIntegrityError("attempt staging root is not a real directory")
        if preserve_logs:
            _remove_tree(paths.staging_root / "artifact", self.root)
            _remove_tree(paths.staging_root / "integrity", self.root)
            _remove_tree(paths.staging_root / "measurement", self.root)
            remaining = tuple(paths.staging_root.iterdir())
            if not remaining:
                paths.staging_root.rmdir()
            elif any(path.name != "logs" for path in remaining):
                raise WorkspaceIntegrityError("attempt staging contains unknown residue")
        else:
            _remove_tree(paths.staging_root, self.root)

    def _create_attempt(self, paths: ExecutionPaths, workspace_files: Mapping[str, bytes]) -> None:
        work_root = self.root / "work"
        _ensure_directory(work_root)
        try:
            paths.attempt_root.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise WorkspaceIntegrityError(f"attempt workspace already exists: {paths.attempt_id}") from exc
        for directory in (paths.input_dir, paths.output_dir, paths.log_dir):
            directory.mkdir(mode=0o700)
        for relative, content in workspace_files.items():
            destination = _safe_join(paths.input_dir, relative)
            _ensure_directory(destination.parent)
            _write_new(destination, content)
        _fsync_tree(paths.input_dir)
        for directory in (paths.output_dir, paths.log_dir, paths.attempt_root, work_root):
            _fsync_directory(directory)

    def _sandbox_argv(
        self,
        paths: ExecutionPaths,
        source_relative_path: str,
        execution_argv: tuple[str, ...],
        environment: Mapping[str, str],
        read_only_bindings: tuple[tuple[Path, str], ...],
    ) -> tuple[str, ...]:
        prefix = self.runtime_prefix.resolve()
        executable = self.python_executable.resolve()
        relative_executable = executable.relative_to(prefix)
        sandbox_executable = str(PurePosixPath("/runtime").joinpath(*relative_executable.parts))
        argv = [
            str(self.bwrap_path),
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
        ]
        for source, destination in self.device_bindings:
            argv.extend(("--dev-bind", str(source), destination))
        argv.extend([
            "--tmpfs",
            "/tmp",
            "--dir",
            "/runtime",
            "--ro-bind",
            str(prefix),
            "/runtime",
        ])
        destinations: set[str] = {"/runtime"}
        for source in self.system_runtime_paths:
            if not source.exists():
                continue
            destination = source.as_posix()
            if destination in destinations:
                continue
            for parent in _missing_destination_parents(destination, destinations):
                argv.extend(("--dir", parent))
                destinations.add(parent)
            argv.extend(("--ro-bind", str(source), destination))
            destinations.add(destination)
        argv.extend(
            (
                "--dir",
                "/input",
                "--dir",
                "/output",
                "--ro-bind",
                str(paths.input_dir),
                "/input",
                "--bind",
                str(paths.output_dir),
                "/output",
            )
        )
        for source, destination in read_only_bindings:
            for parent in _missing_destination_parents(destination, destinations):
                argv.extend(("--dir", parent))
                destinations.add(parent)
            argv.extend(("--ro-bind", str(source), destination))
            destinations.add(destination)
        for name, value in sorted(environment.items()):
            if value:
                argv.extend(("--setenv", name, value))
            else:
                argv.extend(("--unsetenv", name))
        command = [sandbox_executable if execution_argv[0] == "python" else execution_argv[0], *execution_argv[1:]]
        if len(command) > 1 and command[1] == source_relative_path:
            command[1] = str(PurePosixPath("/input") / source_relative_path)
        argv.extend(("--chdir", "/output", "--", *command))
        return tuple(argv)

    def _check_sandbox(self) -> None:
        if not self.bwrap_path.is_absolute() or not self.bwrap_path.is_file() or not os.access(self.bwrap_path, os.X_OK):
            raise SandboxUnavailable(f"bubblewrap executable is unavailable: {self.bwrap_path}")
        if not self.runtime_prefix.is_dir():
            raise SandboxUnavailable(f"Python runtime prefix is unavailable: {self.runtime_prefix}")
        if not self.python_executable.is_file() or not os.access(self.python_executable, os.X_OK):
            raise SandboxUnavailable(f"Python executable is unavailable: {self.python_executable}")
        try:
            self.python_executable.resolve().relative_to(self.runtime_prefix.resolve())
        except ValueError as exc:
            raise SandboxUnavailable("Python executable is outside its runtime prefix") from exc
        project_root = Path(__file__).resolve().parents[1]
        for source in (self.runtime_prefix, *self.system_runtime_paths):
            if not source.exists():
                continue
            resolved = source.resolve()
            if _contains(resolved, self.state_root) or _contains(resolved, project_root):
                raise SandboxUnavailable(f"runtime mount is too broad: {source}")
        for source, _ in self.device_bindings:
            if not _is_character_device(source):
                raise SandboxUnavailable(
                    f"bound GPU source is not an available character device: {source}"
                )

    def _check_prepared(self, prepared: PreparedExecution) -> None:
        expected = self.paths(prepared.attempt_id)
        for name in ("attempt_root", "input_dir", "output_dir", "log_dir", "staging_root"):
            if getattr(prepared, name) != getattr(expected, name):
                raise WorkspaceIntegrityError(f"prepared {name} escaped its attempt paths")
        if prepared.process_request.attempt_id != prepared.attempt_id:
            raise WorkspaceIntegrityError("prepared ProcessRequest attempt identity differs")

    def _bound_workspace(
        self,
        prepared: PreparedExecution,
        agent_request: Mapping[str, Any],
        agent_response: Mapping[str, Any],
    ) -> tuple[AgentRequest, Any, Mapping[str, bytes]]:
        request, response, agent_files = _committed_workspace(agent_request, agent_response)
        if (
            request.attempt_id != prepared.agent_attempt_id
            or response.output_digest != prepared.agent_output_digest
        ):
            raise WorkspaceIntegrityError("committed Agent response differs from the execution plan")
        trusted = dict(prepared.trusted_workspace_files)
        if set(agent_files) & set(trusted): raise WorkspaceIntegrityError("Host and Agent workspace paths overlap")
        return request, response, {**agent_files, **trusted}

    def _verify_materialized_inputs(
        self,
        prepared: PreparedExecution,
        workspace_files: Mapping[str, bytes],
    ) -> None:
        actual = _inventory(prepared.input_dir)
        if set(actual) != set(workspace_files):
            raise WorkspaceIntegrityError("materialized input inventory differs from durable Agent response")
        for relative, expected in workspace_files.items():
            if _read_regular(actual[relative]) != expected:
                raise WorkspaceIntegrityError(
                    f"materialized input {relative!r} differs from durable Agent response"
                )

    def _stage_logs(self, prepared: PreparedExecution, *, require: bool) -> tuple[Path | None, Path | None]:
        destination = prepared.staging_root / "logs"
        staged: list[Path | None] = []
        for name in ("stdout.log", "stderr.log"):
            source = prepared.log_dir / name
            if not source.exists():
                if require:
                    raise WorkspaceIntegrityError(f"terminal process log is missing: {name}")
                staged.append(None)
                continue
            content = _read_regular(source)
            _ensure_directory(destination)
            target = destination / name
            _write_exact(target, content)
            staged.append(target)
        if destination.exists():
            _fsync_tree(destination)
            _fsync_directory(prepared.staging_root)
        return staged[0], staged[1]


def _git_executable(value: str | os.PathLike[str]) -> str:
    text = os.fspath(value)
    if not isinstance(text, str) or not text or "\x00" in text:
        raise ValueError("git_executable must be non-empty NUL-free text")
    resolved = shutil.which(text)
    if resolved is None or not os.access(resolved, os.X_OK):
        raise TargetPreparationError(f"Git executable is unavailable: {text}")
    return str(Path(resolved).resolve())


def _git_timeout(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError("timeout_seconds must be a finite positive number")
    return float(value)


def _git_environment() -> dict[str, str]:
    return {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.defpath,
    }


def _standalone_apply_environment(tree: Path) -> dict[str, str]:
    """Keep ``git apply --no-index`` tree-relative inside an enclosing repository.

    Without the ceiling, Git discovers the enclosing repository above the
    Attempt-local tree and resolves patch paths against that foreign root,
    silently skipping every section while exiting zero.
    """
    return {"GIT_CEILING_DIRECTORIES": str(tree.parent)}


def _run_git(
    git: str,
    repository: Path,
    arguments: Sequence[str],
    *,
    timeout_seconds: float,
    input_bytes: bytes | None = None,
    extra_environment: Mapping[str, str] | None = None,
) -> bytes:
    if not arguments or any(
        not isinstance(argument, str) or "\x00" in argument for argument in arguments
    ):
        raise ValueError("Git arguments must be non-empty NUL-free text")
    argv = (
        git,
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "submodule.recurse=false",
        "-C",
        str(repository),
        *arguments,
    )
    environment = _git_environment()
    if extra_environment is not None:
        environment.update(dict(extra_environment))
    try:
        completed = subprocess.run(
            argv,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
            timeout=timeout_seconds,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        raise TargetPreparationError(
            f"git {arguments[0]} exceeded its execution timeout"
        ) from exc
    except OSError as exc:
        raise TargetPreparationError(f"git {arguments[0]} could not run: {exc}") from exc
    stdout = completed.stdout if isinstance(completed.stdout, bytes) else b""
    stderr = completed.stderr if isinstance(completed.stderr, bytes) else b""
    if len(stdout) > _GIT_OUTPUT_LIMIT or len(stderr) > _GIT_OUTPUT_LIMIT:
        raise TargetPreparationError(f"git {arguments[0]} output exceeded its bound")
    if completed.returncode != 0:
        detail = stderr[:2048].decode("utf-8", errors="replace").strip()
        suffix = f": {detail}" if detail else ""
        raise TargetPreparationError(
            f"git {arguments[0]} rejected target preparation{suffix}"
        )
    return stdout


def _git_text(value: bytes, label: str) -> str:
    if not value.endswith(b"\n") or b"\x00" in value:
        raise TargetPreparationError(f"Git returned an invalid {label}")
    try:
        text = value[:-1].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TargetPreparationError(f"Git returned a non-UTF-8 {label}") from exc
    if not text or "\n" in text or "\r" in text:
        raise TargetPreparationError(f"Git returned an invalid {label}")
    return text


def _resolve_target_commit(git: str, repository: Path, timeout_seconds: float) -> str:
    commit = _git_text(
        _run_git(
            git,
            repository,
            ("rev-parse", "--verify", "HEAD^{commit}"),
            timeout_seconds=timeout_seconds,
        ),
        "target commit",
    )
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit) is None:
        raise TargetPreparationError("Git returned an invalid target commit")
    return commit


def _validated_binding(binding: GitTargetBinding) -> Path:
    if (
        not isinstance(binding.checkout, Path)
        or binding.checkout.is_symlink()
        or not binding.checkout.is_dir()
        or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", binding.commit) is None
    ):
        raise TargetPreparationError("target binding is invalid or unavailable")
    return binding.checkout.resolve()


def _safe_target_relative(value: str) -> str:
    try:
        relative = _relative(value, "target path")
    except ValueError as exc:
        raise TargetPreparationError(str(exc)) from exc
    if any(part.casefold() == ".git" for part in PurePosixPath(relative).parts):
        raise TargetPreparationError("target path cannot contain Git metadata")
    return relative


def _verify_bound_git_tree(git: str, repository: Path, commit: str, root: Path,
                           timeout_seconds: float) -> None:
    raw = _run_git(git, repository, ("ls-tree", "-r", "-z", "--full-tree", commit),
                   timeout_seconds=timeout_seconds)
    expected: dict[str, tuple[bytes, bytes]] = {}
    for record in raw.split(b"\0"):
        if not record: continue
        try: header, raw_path = record.split(b"\t", 1); mode, kind, oid = header.split(b" ", 2)
        except ValueError as exc: raise TargetPreparationError("Git returned an invalid bound tree") from exc
        relative = _safe_target_relative(os.fsdecode(raw_path))
        if os.fsencode(relative) != raw_path or kind != b"blob" or mode not in {b"100644", b"100755", b"120000"} or re.fullmatch(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})", oid) is None: raise TargetPreparationError("bound Git tree contains an unsupported entry")
        if relative in expected: raise TargetPreparationError("bound Git tree contains a duplicate path")
        expected[relative] = mode, oid
    actual: set[str] = set(); actual_directories: set[str] = set()
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in tuple(dirnames):
            path = base / name; relative = path.relative_to(root).as_posix()
            if path.is_symlink(): dirnames.remove(name); actual.add(relative)
            else: actual_directories.add(relative)
        actual.update((base / name).relative_to(root).as_posix() for name in filenames)
    expected_directories = {parent.as_posix() for relative in expected for parent in PurePosixPath(relative).parents if parent.as_posix() != "."}
    if actual != set(expected) or actual_directories != expected_directories: raise TargetPreparationError("Attempt-local target inventory differs from bound Git tree")
    regular: list[tuple[str, Path, bytes]] = []
    for relative, (mode, oid) in expected.items():
        path = root.joinpath(*PurePosixPath(relative).parts); metadata = path.lstat()
        if mode == b"120000":
            if not stat.S_ISLNK(metadata.st_mode): raise TargetPreparationError("Attempt-local target mode differs from bound Git tree")
            observed = _git_text(_run_git(git, repository, ("hash-object", "--stdin"), timeout_seconds=timeout_seconds, input_bytes=os.fsencode(os.readlink(path))), "Git blob")
            if observed != oid.decode("ascii"): raise TargetPreparationError(f"Attempt-local target {relative!r} differs from bound Git blob")
        else:
            if not stat.S_ISREG(metadata.st_mode) or bool(metadata.st_mode & 0o111) != (mode == b"100755"): raise TargetPreparationError("Attempt-local target mode differs from bound Git tree")
            regular.append((relative, path, oid))
    for offset in range(0, len(regular), 128):
        batch = regular[offset:offset + 128]; raw_hashes = _run_git(git, repository, ("hash-object", "--no-filters", "--", *(str(path) for _, path, _ in batch)), timeout_seconds=timeout_seconds)
        hashes = raw_hashes.splitlines()
        if len(hashes) != len(batch): raise TargetPreparationError("Git returned an invalid materialized blob inventory")
        for (relative, _, oid), observed in zip(batch, hashes, strict=True):
            if observed != oid: raise TargetPreparationError(f"Attempt-local target {relative!r} differs from bound Git blob")


def _validate_target_tree(root: Path) -> None:
    _real_directory(root, "Attempt-local target tree")
    resolved_root = root.resolve()
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in tuple(dirnames):
            path = base / name
            if path.is_symlink():
                dirnames.remove(name)
                _validate_target_symlink(resolved_root, root, path)
            elif not path.is_dir():
                raise TargetPreparationError("target tree contains a special directory")
            _safe_target_relative(path.relative_to(root).as_posix())
        for name in filenames:
            path = base / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                _validate_target_symlink(resolved_root, root, path)
            elif not stat.S_ISREG(mode):
                raise TargetPreparationError("target tree contains a special file")
            _safe_target_relative(path.relative_to(root).as_posix())


def _validate_target_symlink(resolved_root: Path, root: Path, path: Path) -> None:
    try:
        path.resolve(strict=False).relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError) as exc:
        relative = path.relative_to(root).as_posix()
        raise TargetPreparationError(
            f"target symlink escapes the Attempt-local tree: {relative}"
        ) from exc


def _validated_patch(value: bytes | None) -> bytes | None:
    if value is None:
        return None
    if not isinstance(value, bytes) or not value:
        raise ValueError("patch must be non-empty bytes or None")
    if len(value) > _PATCH_SIZE_LIMIT:
        raise ValueError("patch exceeds its size bound")
    return value


def _prepared(
    paths: ExecutionPaths,
    source_relative_path: str,
    result_relative_path: str,
    request: ProcessRequest,
    *,
    agent_attempt_id: str,
    agent_output_digest: str,
    read_only_bindings: tuple[tuple[Path, str], ...],
    trusted_workspace_files: tuple[tuple[str, bytes], ...],
) -> PreparedExecution:
    return PreparedExecution(
        attempt_id=paths.attempt_id,
        agent_attempt_id=agent_attempt_id,
        agent_output_digest=agent_output_digest,
        attempt_root=paths.attempt_root,
        input_dir=paths.input_dir,
        output_dir=paths.output_dir,
        log_dir=paths.log_dir,
        staging_root=paths.staging_root,
        source_relative_path=source_relative_path,
        result_relative_path=result_relative_path,
        read_only_bindings=read_only_bindings,
        trusted_workspace_files=trusted_workspace_files,
        process_request=request,
    )


def _trusted_workspace(value: Mapping[str, bytes]) -> dict[str, bytes]:
    if not isinstance(value, Mapping): raise ValueError("trusted_workspace_files must be a mapping")
    result: dict[str, bytes] = {}
    for relative, content in value.items():
        path = _relative(relative, "trusted workspace path")
        if not isinstance(content, bytes) or not content: raise ValueError("trusted workspace content must be non-empty bytes")
        result[path] = content
    return result


def _committed_workspace(
    request_value: Mapping[str, Any],
    response_value: Mapping[str, Any],
) -> tuple[AgentRequest, Any, Mapping[str, bytes]]:
    request = AgentRequest.from_dict(request_value)
    if response_value.get("kind") == "admitted-submit/1":
        submitted = validate_submission_snapshot(request, response_value)
        return request, submitted, submitted.workspace_files
    validated = validate_postflight(request, response_value)
    return request, validated.response, validated.workspace_files


def _environment(
    value: Mapping[str, str],
    fixed_environment: tuple[tuple[str, str], ...],
) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("target_env must be a mapping")
    result = dict(value)
    result.setdefault("HOME", "/nonexistent")
    result.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    result.setdefault("PYTHONNOUSERSITE", "1")
    result.setdefault("TMPDIR", "/tmp")
    _environment_entries(result, "target_env")
    for name, item in fixed_environment:
        if name in result and result[name] != item:
            raise ValueError(f"target_env conflicts with fixed environment entry {name}")
        result[name] = item
    return result


def _environment_entries(
    value: Mapping[str, str],
    label: str,
) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    result = dict(value)
    for name, item in result.items():
        if not isinstance(name, str) or not name or "=" in name or "\x00" in name:
            raise ValueError(f"{label} names are invalid")
        if not isinstance(item, str) or "\x00" in item:
            raise ValueError(f"{label} values are invalid")
    return tuple(sorted(result.items()))


def _device_bindings(
    value: Sequence[tuple[str | os.PathLike[str], str | os.PathLike[str]]],
) -> tuple[tuple[Path, str], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("device_bindings must be a sequence of source/target pairs")
    result: list[tuple[Path, str]] = []
    sources: set[str] = set()
    targets: set[str] = set()
    pattern = re.compile(r"/dev/nvidia(?:[0-9]+|ctl|-uvm(?:-tools)?)\Z")
    for item in value:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise ValueError("device_bindings must contain source/target pairs")
        source_text = os.fspath(item[0])
        target_text = os.fspath(item[1])
        if not isinstance(source_text, str) or not isinstance(target_text, str):
            raise ValueError("device_bindings paths must be text")
        source = Path(source_text)
        target = PurePosixPath(target_text)
        if (
            not source.is_absolute()
            or not target.is_absolute()
            or source.as_posix() != source_text
            or target.as_posix() != target_text
            or pattern.fullmatch(source_text) is None
            or pattern.fullmatch(target_text) is None
            or source_text in sources
            or target_text in targets
        ):
            raise ValueError("device_bindings must be unique absolute /dev/nvidia* paths")
        sources.add(source_text)
        targets.add(target_text)
        result.append((source, target_text))
    return tuple(result)


def _read_only_bindings(
    value: Sequence[tuple[str | os.PathLike[str], str | os.PathLike[str]]],
    paths: ExecutionPaths,
) -> tuple[tuple[Path, str], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("read_only_bindings must be a sequence of source/target pairs")
    result: list[tuple[Path, str]] = []
    sources: set[str] = set(); targets: set[str] = set()
    reserved = ("/dev", "/input", "/output", "/proc", "/runtime", "/tmp")
    for item in value:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise ValueError("read_only_bindings must contain source/target pairs")
        source_text, target_text = os.fspath(item[0]), os.fspath(item[1])
        if not isinstance(source_text, str) or not isinstance(target_text, str):
            raise ValueError("read_only_bindings paths must be text")
        source = Path(source_text); target = PurePosixPath(target_text)
        if (not source.is_absolute() or not target.is_absolute() or "\\" in target_text
                or target.as_posix() != target_text or any(part in {".", ".."} for part in target.parts)
                or target_text == "/" or any(target_text == root or target_text.startswith(root + "/") for root in reserved)):
            raise ValueError("read_only_bindings require safe absolute non-reserved paths")
        source = source.resolve(strict=False); source_text = str(source)
        if source_text in sources or target_text in targets:
            raise ValueError("read_only_bindings sources and targets must be unique")
        if any(_contains(existing, source) or _contains(source, existing) for existing, _ in result):
            raise ValueError("read_only_bindings sources must not overlap")
        if any(target_text.startswith(existing + "/") or existing.startswith(target_text + "/") for existing in targets):
            raise ValueError("read_only_bindings targets must not overlap")
        if any(_contains(path, source) or _contains(source, path)
               for path in (paths.input_dir, paths.output_dir, paths.log_dir)):
            raise ValueError("read_only_bindings must be disjoint from mutable Attempt directories")
        sources.add(source_text); targets.add(target_text); result.append((source, target_text))
    return tuple(result)


def _validate_read_only_sources(bindings: tuple[tuple[Path, str], ...]) -> None:
    for source, _ in bindings:
        try: mode = source.lstat().st_mode
        except OSError as exc: raise WorkspaceIntegrityError(f"read-only source is unavailable: {source}") from exc
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise WorkspaceIntegrityError(f"read-only source is not a real file or directory: {source}")


def _is_character_device(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except OSError:
        return False
    return stat.S_ISCHR(mode)


def _verify_observation(
    request: ProcessRequest,
    observation: ProcessObservation | Mapping[str, Any],
) -> AttemptTerminalReason:
    if isinstance(observation, ProcessObservation):
        attempt_id = observation.attempt_id
        attempt_ordinal = observation.attempt_ordinal
        execution_id = observation.execution_id
        raw_reason: Any = observation.terminal_reason
    elif isinstance(observation, Mapping):
        attempt_id = observation.get("attempt_id")
        attempt_ordinal = observation.get("attempt_ordinal")
        execution_id = observation.get("execution_id")
        raw_reason = observation.get("terminal_reason", observation.get("reason"))
    else:
        raise WorkspaceIntegrityError("terminal observation must be typed or a mapping")
    if (attempt_id, attempt_ordinal, execution_id) != (
        request.attempt_id,
        request.attempt_ordinal,
        request.execution_id,
    ):
        raise WorkspaceIntegrityError("terminal observation identity differs from ProcessRequest")
    try:
        return raw_reason if isinstance(raw_reason, AttemptTerminalReason) else AttemptTerminalReason(raw_reason)
    except (TypeError, ValueError) as exc:
        raise WorkspaceIntegrityError("terminal observation reason is invalid") from exc


def _stage_candidate(root: Path, files: Mapping[str, bytes]) -> tuple[str, ...]:
    _ensure_directory(root)
    for relative, content in files.items():
        destination = _safe_join(root, relative)
        _ensure_directory(destination.parent)
        _write_exact(destination, content)
    expected = set(files)
    actual = _inventory(root)
    if set(actual) != expected:
        raise WorkspaceIntegrityError("candidate staging contains unexpected files")
    _fsync_tree(root)
    _fsync_directory(root.parent)
    return tuple(sorted(expected, key=lambda value: value.encode("utf-8")))


def _inventory(root: Path) -> dict[str, Path]:
    _real_directory(root, "workspace directory")
    result: dict[str, Path] = {}
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in dirnames:
            path = base / name
            if path.is_symlink() or not path.is_dir():
                raise WorkspaceIntegrityError(f"workspace contains a linked directory: {path}")
        for name in filenames:
            path = base / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise WorkspaceIntegrityError(f"workspace contains a non-regular file: {path}")
            result[path.relative_to(root).as_posix()] = path
    return result


def _read_regular(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise WorkspaceIntegrityError(f"cannot open regular file {path}: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise WorkspaceIntegrityError(f"not a regular file: {path}")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise WorkspaceIntegrityError(f"file shortened while reading: {path}")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise WorkspaceIntegrityError(f"file grew while reading: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _write_new(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_exact(path: Path, content: bytes) -> None:
    if path.exists() or path.is_symlink():
        if _read_regular(path) != content:
            raise WorkspaceIntegrityError(f"staged file conflicts with existing bytes: {path}")
        return
    _write_new(path, content)


def _ensure_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=True)
    except FileExistsError:
        pass
    _real_directory(path, "workspace directory")


def _real_directory(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise WorkspaceIntegrityError(f"{label} is not a real directory: {path}")


def _fsync_tree(root: Path) -> None:
    directories = [Path(directory) for directory, _, _ in os.walk(root, topdown=False, followlinks=False)]
    for directory in directories:
        _fsync_directory(directory)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_tree(path: Path, root: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise WorkspaceIntegrityError(f"cleanup escaped artifact root: {path}") from exc
    if path.is_symlink() or not path.is_dir():
        raise WorkspaceIntegrityError(f"cleanup target is not a real directory: {path}")
    shutil.rmtree(path)


def _safe_join(root: Path, value: str) -> Path:
    path = PurePosixPath(_relative(value, "relative path"))
    return root.joinpath(*path.parts)


def _component_bound_paths(plan: Mapping[str, Any]) -> dict[tuple[object, object], Mapping[str, Any]]:
    data_bindings = plan.get("data_bindings", ())
    if not isinstance(data_bindings, (list, tuple)):
        raise WorkspaceIntegrityError("component data bindings are malformed")
    return {(item.get("scope"), item.get("path")): item for item in data_bindings if isinstance(item, Mapping)}


def _checked_component_path(raw: Mapping[str, Any], bound_paths: Mapping[tuple[object, object], Mapping[str, Any]], target_root: Path) -> Path:
    scope, value = raw.get("scope"), raw.get("path")
    binding = bound_paths.get((scope, value))
    if isinstance(binding, Mapping): return Path(str(binding.get("source")))
    if scope == "target" and isinstance(value, str):
        try:
            relative = _relative(value, "component target path")
        except ValueError as exc:
            raise WorkspaceIntegrityError(str(exc)) from exc
        return _safe_join(target_root, relative)
    if scope == "absolute" and isinstance(value, str) and Path(value).is_absolute():
        return Path(value)
    raise WorkspaceIntegrityError("component path has an unsupported scope")


def _require_component_kind(path: Path, kind: object) -> None:
    present = path.is_file() if kind == "file" else path.is_dir() if kind == "dir" else path.exists()
    if not present:
        raise WorkspaceIntegrityError(f"required component {kind} is missing: {path}")


def check_component_source_contract(plan: Mapping[str, Any], target_root: Path) -> None:
    """Fail unless one materialized target tree satisfies the GoalDefined source contract.

    This is the target-tree portion of ExecutionWorkspace.assert_component_preflight:
    unbound required paths, entrypoint source literals, and capability files and
    literals.  It runs pre-spawn and when an accepted implementation patch is
    checked against the downstream contracts its materialized tree must satisfy.
    """

    if plan.get("production") is not True:
        return
    if target_root.is_symlink() or not target_root.is_dir():
        raise WorkspaceIntegrityError("production target overlay is absent")
    requirements = plan.get("requirements")
    capabilities = plan.get("capabilities")
    if not isinstance(requirements, Mapping) or not isinstance(capabilities, Mapping):
        raise WorkspaceIntegrityError("component execution plan is malformed")
    bound_paths = _component_bound_paths(plan)
    for raw in requirements.get("required_paths", []):
        if not isinstance(raw, Mapping):
            raise WorkspaceIntegrityError("required component path is malformed")
        _require_component_kind(_checked_component_path(raw, bound_paths, target_root), raw.get("kind"))
    entrypoint = plan.get("entrypoint_path")
    if not isinstance(entrypoint, str):
        raise WorkspaceIntegrityError("production entrypoint path is absent")
    entrypoint_source = _safe_join(target_root, _relative(entrypoint, "entrypoint path"))
    _require_component_kind(entrypoint_source, "file")
    required_literals = requirements.get("required_source_literals", [])
    if not isinstance(required_literals, (list, tuple)):
        raise WorkspaceIntegrityError("required source literals are malformed")
    missing = set(required_literals) - _python_literals(entrypoint_source)
    if missing:
        raise WorkspaceIntegrityError(
            f"entrypoint {entrypoint} lacks required source literals: {sorted(missing)!r}"
        )
    for name, raw in capabilities.items():
        if not isinstance(raw, Mapping):
            raise WorkspaceIntegrityError("delivery capability is malformed")
        path, literals = raw.get("path"), raw.get("required_literals", [])
        if path is None:
            if literals:
                raise WorkspaceIntegrityError("source literals have no capability path")
            continue
        source = _safe_join(target_root, _relative(str(path), "capability path"))
        if not source.is_file():
            raise WorkspaceIntegrityError(
                f"capability source contract failed: {path} (capability {name!r}: required file is missing)"
            )
        if not isinstance(literals, (list, tuple)):
            raise WorkspaceIntegrityError(
                f"capability source contract failed: {path} (capability {name!r}: required literals are malformed)"
            )
        missing = set(literals) - _python_literals(source)
        if missing:
            raise WorkspaceIntegrityError(
                f"capability source contract failed: {path} (capability {name!r}: missing source literals {sorted(missing)!r})"
            )


def verify_downstream_implementation_contracts(
    document: Mapping[str, Any],
    component_id: str,
    family: str,
    validated_result: Mapping[str, Any],
    artifact_root: Path,
    *,
    workspace: ExecutionWorkspace,
    binding: GitTargetBinding,
    attempt_id: str,
) -> None:
    """Reject an accepted implementation patch a latest-overlay descendant cannot execute.

    The execution target of a latest-overlay consumer is shaped only by the
    accepted ancestor implementation patch, so a downstream source contract the
    patch cannot satisfy is unrecoverable once the outcome is accepted.  The
    check runs at acceptance, while the producer hypothesis can still be
    revised, and reuses the production source-contract check verbatim.
    """

    patch_refs = validated_result["logical_artifacts"].get("implementation_patch", ())
    if len(patch_refs) != 1:
        return
    patch_bytes = artifact_root.joinpath(*str(patch_refs[0]).split("/")).read_bytes()
    digest = validated_result.get("implementation_sha256")
    plans = downstream_source_contract_plans(
        document, component_id, family=family,
        implementation_sha256=digest if isinstance(digest, str) else "0" * 64,
    )
    if not plans:
        return
    with workspace.verified_target_tree(binding, attempt_id=attempt_id, patch=patch_bytes) as tree:
        for other, plan in plans:
            try:
                check_component_source_contract(plan, tree)
            except WorkspaceIntegrityError as exc:
                raise ComponentResultValidationError(
                    "downstream_target_contract_failed", "implementation.patch", f"{other}: {exc}"
                ) from exc


def _python_literals(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError) as exc:
        raise WorkspaceIntegrityError(f"cannot inspect frozen source {path}: {exc}") from exc
    literals = {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    literals.update(node.id for node in ast.walk(tree) if isinstance(node, ast.Name))
    literals.update(node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute))
    literals.update(node.name for node in ast.walk(tree) if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)))
    return literals


def _relative(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "\\" in value:
        raise ValueError(f"{label} must be a safe relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{label} must be a safe relative path")
    return value


def _component(value: str, label: str) -> None:
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


def _text(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value:
        raise ValueError(f"{label} must be non-empty trimmed text")


def _positive_int(value: Any, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
    except ValueError:
        return False
    return True


def _missing_destination_parents(destination: str, present: set[str]) -> tuple[str, ...]:
    path = PurePosixPath(destination)
    parents = [str(parent) for parent in reversed(path.parents) if str(parent) != "/"]
    return tuple(parent for parent in parents if parent not in present)


def _json_bytes(value: Mapping[str, str]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


__all__ = [
    "CollectedExecution",
    "EXECUTION_WORKSPACE_VERSION",
    "ExecutionPaths",
    "ExecutionWorkspace",
    "ExecutionWorkspaceError",
    "GitTargetBinding",
    "PreparedAgentWorkspace",
    "PreparedExecution",
    "SandboxUnavailable",
    "TargetPreparationError",
    "WorkspaceIntegrityError",
    "bind_clean_git_target",
]
