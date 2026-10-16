"""Host-owned native Claude plugins, versioned solely by ordinary Git commits.

The Host selects a cycle's commit and records it in its existing Attempt data.
This module never selects a workflow/cycle, changes scientific facts, or grants
tools. Read views are disposable; accepted commits and update worktrees persist.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Mapping

import yaml

from .git_authoring_workspace import _COMMIT_RE, _run_git


SEED_ROOT = Path(__file__).parent / "skills" / "expert-seed"
GROUNDING_SEED_ROOT = Path(__file__).parent / "skills" / "grounding-seed"
WORKFLOW_SKILL_ROOT = Path(__file__).parent / "skills" / "research-workflow"
_MANIFEST = ".claude-plugin/plugin.json"
_REQUIRED = {
    "experiment-report",
    "literature-summary",
    "experiment-repair",
    "self-evolve",
}
_GROUNDING_REQUIRED = {"leader-trigger", "debate-trigger", "debate-judge",
                       "graph-evolve", "final-report", "self-evolve"}


def _component(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value
    ):
        raise ValueError("plugin identity must be one safe path component")
    return value


def _directory(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("plugin directory must not be a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir() or path.resolve() != path:
        raise ValueError("plugin directory must be canonical")
    return path


def _name(role: str) -> str:
    return "hypoloom-" + re.sub(r"[^a-z0-9-]", "-", role.lower())


def _budget(max_bytes: int) -> None:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("plugin byte budget must be a positive integer")


def _full_commit(value):
    if not isinstance(value, str) or _COMMIT_RE.fullmatch(value) is None:
        raise ValueError("plugin selection requires an explicit full Git commit")
    return value


def _time(value):
    return float(value) if isinstance(value, (int, float)) else datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def select_plugin_commit(controls, attempts, *, run_id, role, component_id=None, cycle=None,
                         owner_kind="expert", default_commit):
    """Fold existing accepted controls/Attempts for a not-yet-frozen cycle.

    Callers MUST first reuse any descriptor already frozen for that cycle.
    Actual Git/native-file validation remains ``binding_at``. This helper writes
    no selection file, revision ledger or new source of authority.
    """
    if owner_kind not in {"expert", "grounding"}:
        raise ValueError("unknown plugin owner_kind")
    if owner_kind == "expert" and (not component_id or type(cycle) is not int or cycle < 1):
        raise ValueError("expert plugin selection needs its component and cycle")
    selected = None
    for row in sorted(controls, key=lambda value: value["revision"]):
        if row["run_id"] != run_id:
            continue
        payload = row.get("payload")
        if payload is None:
            payload = json.loads(row["payload_json"])
        control = payload.get("plugin_selection")
        if control is None or (control.get("role"), control.get("owner_kind")) != (role, owner_kind):
            continue
        if (set(control) != {"action", "owner_kind", "role", "requested_commit", "commit", "effective_cycles"}
                or control["action"] not in {"pin", "revert", "unpin"}):
            raise ValueError("invalid accepted plugin selection control")
        _full_commit(control["commit"])
        if control["requested_commit"] is not None:
            _full_commit(control["requested_commit"])
        effective = control["effective_cycles"]
        if not isinstance(effective, dict) or any(type(value) is not int or value < 1 for value in effective.values()):
            raise ValueError("plugin selection effective cycles must be positive integers")
        if owner_kind == "expert" and (component_id not in effective or cycle < effective[component_id]):
            continue
        selected = row, control
    candidates = []
    for row in attempts:
        if (row["run_id"], row["state"], row["terminal_reason"]) != (run_id, "CLOSED", "SUCCEEDED"):
            continue
        observation = json.loads(row["observation_json"])
        request = observation.get("request", {})
        expected = "SELF_EVOLVE" if owner_kind == "expert" else "GRAPH_EVOLVE"
        if request.get("role") != role or request.get("run_id") != run_id or request.get("command") != expected:
            continue
        update = observation.get("plugin_update")
        if update is None:
            continue  # Explicit fake/pre-plugin records do not invent a Git tip.
        if set(update) != {"base_commit", "commit"}:
            raise ValueError("accepted plugin candidate metadata is invalid")
        _full_commit(update["base_commit"])
        _full_commit(update["commit"])
        if selected is not None:
            control_row, control = selected
            if owner_kind == "expert":
                key = request.get("task_context", {}).get("key", {})
                threshold = control["effective_cycles"].get(request.get("component_id"))
                if threshold is None or key.get("cycle", 0) < threshold:
                    continue
            elif _time(row["started_at"]) < _time(control_row["created_at"]):
                continue
        candidates.append((_time(row["ended_at"]), row["attempt_id"], update["commit"]))
    latest = max(candidates)[2] if candidates else None
    if selected is None:
        return {"commit": latest or _full_commit(default_commit), "pinned": False, "source_command_id": None}
    row, control = selected
    pinned = control["action"] in {"pin", "revert"}
    return {"commit": control["commit"] if pinned or latest is None else latest,
        "pinned": pinned, "source_command_id": row["command_id"]}


def _files(root: Path, max_bytes: int, *, worktree: bool = False) -> dict[str, bytes]:
    _budget(max_bytes)
    files: dict[str, bytes] = {}
    remaining = max_bytes
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if worktree and (relative == ".git" or relative.startswith(".git/")):
            continue
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("plugin contents must be regular, non-linked files")
        if info.st_size > remaining:
            raise ValueError("plugin exceeds its admitted byte budget")
        with path.open("rb") as stream:
            content = stream.read(remaining + 1)
        remaining -= len(content)
        if remaining < 0:
            raise ValueError("plugin exceeds its admitted byte budget")
        files[relative] = content
    return files


def _validate(files: Mapping[str, bytes], role: str, owner_kind: str = "expert") -> None:
    if _MANIFEST not in files:
        raise ValueError("native plugin manifest is missing")
    manifest = json.loads(files[_MANIFEST])
    if (
        not isinstance(manifest, dict)
        or set(manifest) - {"name", "version", "description"}
        or manifest.get("name") != _name(role)
        or any(
            not isinstance(value, str) or not value.strip()
            for value in manifest.values()
        )
    ):
        raise ValueError(
            "native plugin manifest changes its role or supported metadata"
        )
    found = set()
    for name, content in files.items():
        path = PurePosixPath(name)
        if name == _MANIFEST:
            continue
        if (
            len(path.parts) < 3
            or path.parts[0] != "skills"
            or ".." in path.parts
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", path.parts[1])
        ):
            raise ValueError("plugin updates are confined to native skill directories")
        if path.name != "SKILL.md":
            continue  # Native skills may include scripts, references and assets.
        if len(path.parts) != 3:
            raise ValueError("SKILL.md must be the skill's immediate entrypoint")
        text = content.decode("utf-8")
        parts = text.split("---", 2)
        if len(parts) != 3 or parts[0] or not parts[2].strip():
            raise ValueError("skill requires YAML frontmatter and a nonempty body")
        metadata = yaml.safe_load(parts[1])
        if (
            not isinstance(metadata, dict)
            or metadata.get("name") != path.parts[1]
            or not isinstance(metadata.get("description"), str)
            or not metadata["description"].strip()
        ):
            raise ValueError("skill frontmatter requires matching name and description")
        found.add(path.parts[1])
    required = _GROUNDING_REQUIRED if owner_kind == "grounding" else _REQUIRED
    if not required <= found:
        raise ValueError(f"plugin must preserve its {owner_kind} wired skill entrypoints")


@dataclass(frozen=True, slots=True)
class PluginBinding:
    run_id: str
    role: str
    name: str
    commit: str
    owner_kind: str = "expert"

    def to_dict(self) -> dict[str, str]:
        result = {
            "run_id": self.run_id,
            "role": self.role,
            "name": self.name,
            "commit": self.commit,
        }
        if self.owner_kind != "expert":
            result["owner_kind"] = self.owner_kind
        return result


@dataclass(frozen=True, slots=True)
class PreparedPluginSnapshot:
    root: Path
    binding: PluginBinding

    def close(self) -> None:
        # Only this invocation's mkdtemp view, never the Git plugin/history.
        if self.root.is_dir() and not self.root.is_symlink():
            shutil.rmtree(self.root)


@dataclass(frozen=True, slots=True)
class PreparedPluginUpdate:
    root: Path
    repository_root: Path
    run_id: str
    role: str
    base_commit: str
    attempt_id: str
    command_id: str | None = None
    owner_kind: str = "expert"
    target_commit: str | None = None


class ExpertPluginStore:
    def __init__(self, state_root: Path, seed_root: Path | None = None, *, owner_kind: str = "expert") -> None:
        if not isinstance(owner_kind, str) or owner_kind not in {"expert", "grounding"}:
            raise ValueError("plugin owner_kind must be expert or grounding")
        self.owner_kind = owner_kind
        self.state_root = _directory(Path(state_root).absolute())
        self.seed_root = (GROUNDING_SEED_ROOT if owner_kind == "grounding" else SEED_ROOT) if seed_root is None else Path(seed_root)
        self.shared_seed = seed_root is None
        self.git = shutil.which("git")
        if self.git is None:
            raise ValueError("native expert plugins require Git")

    def _git(self, root: Path, *args: str, max_bytes: int = 4 * 1024 * 1024) -> bytes:
        return _run_git(
            self.git,
            ("-C", str(root), *args),
            timeout_seconds=15,
            max_output_bytes=max_bytes,
        )

    def _parent(self, run_id: str, role: str) -> Path:
        _component(role)
        if self.owner_kind == "grounding":
            return _directory(self.state_root / _component(run_id) / "grounding")
        return _directory(
            self.state_root / _component(run_id) / "experts" / _component(role)
        )

    def _repository(self, run_id: str, role: str) -> Path:
        root = self._parent(run_id, role) / "plugin"
        if (
            root.is_symlink()
            or not (root / ".git").is_dir()
            or (root / ".git").is_symlink()
        ):
            raise ValueError("expert plugin is not an initialized Host Git repository")
        return root

    def _commit(self, root: Path, commit: str) -> str:
        if not isinstance(commit, str) or _COMMIT_RE.fullmatch(commit) is None:
            raise ValueError("plugin snapshot requires an explicit full Git commit")
        actual = (
            self._git(root, "rev-parse", "--verify", commit + "^{commit}")
            .decode()
            .strip()
        )
        if actual != commit:
            raise ValueError("plugin commit does not resolve exactly")
        return actual

    def ensure(self, run_id: str, role: str, focus: str, *, max_bytes: int = 1024 * 1024) -> PluginBinding:
        if not isinstance(focus, str):
            raise ValueError("expert focus must be text")
        _budget(max_bytes)
        parent = self._parent(run_id, role)
        root = parent / "plugin"
        if not root.exists():
            files = _files(self.seed_root, max_bytes)
            if self.shared_seed:
                # One public source; owner-local copies use the existing Git lifecycle.
                files.update({"skills/research-workflow/" + name: data
                    for name, data in _files(WORKFLOW_SKILL_ROOT, max_bytes).items()})
            files[_MANIFEST] = json.dumps(
                {**json.loads(files[_MANIFEST]), "name": _name(role)}
            ).encode()
            files = {
                name: content.decode()
                .replace("{{ROLE}}", role)
                .replace("{{FOCUS}}", focus)
                .encode()
                for name, content in files.items()
            }
            _validate(files, role, self.owner_kind)
            if sum(map(len, files.values())) > max_bytes:
                raise ValueError("expanded plugin seed exceeds its byte budget")
            temporary = Path(tempfile.mkdtemp(prefix=".plugin-seed-", dir=parent))
            try:
                self._write(temporary, files)
                self._git(temporary, "init", "-q")
                self._git(temporary, "symbolic-ref", "HEAD", "refs/heads/main")
                self._git(temporary, "add", "--all")
                self._git(
                    temporary,
                    "-c",
                    "user.name=Hypoloom Host",
                    "-c",
                    "user.email=hypoloom@localhost",
                    "commit",
                    "-qm",
                    f"Seed plugin for {role}",
                )
                os.rename(temporary, root)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        root = self._repository(run_id, role)
        _validate(_files(root, max_bytes, worktree=True), role, self.owner_kind)
        return PluginBinding(
            run_id,
            role,
            _name(role),
            self._git(root, "rev-parse", "HEAD").decode().strip(),
            self.owner_kind,
        )

    @staticmethod
    def _write(root: Path, files: Mapping[str, bytes]) -> None:
        for name, content in files.items():
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(content)

    def _commit_files(self, root: Path, role: str, commit: str, max_bytes: int):
        _budget(max_bytes)
        self._commit(root, commit)
        records = self._git(root, "ls-tree", "-rz", commit, max_bytes=max_bytes).split(
            b"\0"
        )
        files: dict[str, bytes] = {}
        executable_paths: set[str] = set()
        remaining = max_bytes
        for record in filter(None, records):
            header, raw_name = record.split(b"\t", 1)
            mode, kind, _identity = header.split()
            if kind != b"blob" or mode not in {b"100644", b"100755"}:
                raise ValueError("plugin commit must contain only regular files")
            name = raw_name.decode("utf-8")
            if mode == b"100755":
                executable_paths.add(name)
            content = self._git(
                root, "show", f"{commit}:{name}", max_bytes=max(1, remaining + 1)
            )
            remaining -= len(content)
            if remaining < 0:
                raise ValueError("plugin exceeds its admitted byte budget")
            files[name] = content
        _validate(files, role, self.owner_kind)
        return files, executable_paths

    def binding_at(self, run_id: str, role: str, commit: str, *, max_bytes: int) -> PluginBinding:
        """Validate an explicit native plugin commit without creating a view."""
        self._commit_files(self._repository(run_id, role), role, commit, max_bytes)
        return PluginBinding(run_id, role, _name(role), commit, self.owner_kind)

    def read(
        self, run_id: str, role: str, *, commit: str, attempt_id: str, max_bytes: int
    ) -> PreparedPluginSnapshot:
        root = self._repository(run_id, role)
        files, executable_paths = self._commit_files(root, role, commit, max_bytes)
        parent = _directory(root.parent / "plugin-views")
        view = Path(tempfile.mkdtemp(prefix=_component(attempt_id) + "-", dir=parent))
        try:
            self._write(view, files)
            for name in executable_paths:
                (view / name).chmod(0o755)
        except BaseException:
            shutil.rmtree(view)
            raise
        return PreparedPluginSnapshot(
            view, PluginBinding(run_id, role, _name(role), commit, self.owner_kind)
        )

    def prepare_update(
        self, run_id: str, role: str, *, commit: str, attempt_id: str,
        command_id: str | None = None, target_commit: str | None = None,
    ) -> PreparedPluginUpdate:
        """Resume one logical update across Attempts, using Git as its history.

        Without ``command_id`` the existing Attempt-addressed API is unchanged.
        The UUID is only a bounded directory encoding; the Git commit message
        retains the complete logical command and explicit base commit.
        """
        _component(attempt_id)
        if command_id is not None and (not isinstance(command_id, str) or not command_id.strip()
                or any(character in command_id for character in "\x00\r\n")):
            raise ValueError("plugin update command must be nonempty single-line text")
        repository = self._repository(run_id, role)
        self._commit(repository, commit)
        if target_commit is not None:
            self._commit(repository, target_commit)
            if command_id is None:
                raise ValueError("plugin revert requires its existing control command identity")
        parent = _directory(repository.parent / "plugin-updates")
        root = parent / (attempt_id if command_id is None else "command-" + str(uuid.uuid5(uuid.NAMESPACE_URL, command_id)))
        if not root.exists():
            self._git(repository, "worktree", "add", "--detach", str(root), commit)
        prepared = PreparedPluginUpdate(
            root, repository, run_id, role, commit, attempt_id, command_id, self.owner_kind, target_commit
        )
        self._validate_update(prepared)
        return prepared

    def _validate_update(self, prepared: PreparedPluginUpdate) -> str:
        if (prepared.owner_kind != self.owner_kind
                or prepared.repository_root != self._parent(prepared.run_id, prepared.role) / "plugin"):
            raise ValueError("plugin update belongs to another owner namespace")
        root = prepared.root
        if (
            root.is_symlink()
            or not root.is_dir()
            or not (root / ".git").is_file()
            or (root / ".git").is_symlink()
        ):
            raise ValueError("plugin update must be its Host worktree")
        common = self._git(root, "rev-parse", "--git-common-dir").decode().strip()
        if (root / common).resolve() != prepared.repository_root / ".git":
            raise ValueError("plugin update belongs to another repository")
        head = self._git(root, "rev-parse", "HEAD").decode().strip()
        message = self._git(root, "log", "-1", "--format=%B").decode().strip()
        if prepared.command_id is not None and message.startswith(
            f"Hypoloom plugin update\n\nCommand: {prepared.command_id}\nBase: "
        ) and message != self._message(prepared):
            raise ValueError("plugin update logical command changed its bound base")
        if head != prepared.base_commit and message != self._message(prepared):
            raise ValueError("plugin update moved beyond its bound base/Attempt")
        return head

    @staticmethod
    def _message(prepared: PreparedPluginUpdate) -> str:
        if prepared.target_commit is not None:
            return f"Hypoloom plugin revert\n\nCommand: {prepared.command_id}\nBase: {prepared.base_commit}\nTarget: {prepared.target_commit}"
        if prepared.command_id is not None:
            return f"Hypoloom plugin update\n\nCommand: {prepared.command_id}\nBase: {prepared.base_commit}"
        return f"Hypoloom plugin update\n\nAttempt: {prepared.attempt_id}\nBase: {prepared.base_commit}"

    def finish_update(
        self, prepared: PreparedPluginUpdate, *, max_bytes: int, advance: bool = True,
    ) -> PluginBinding:
        head = self._validate_update(prepared)
        files = _files(prepared.root, max_bytes, worktree=True)
        _validate(files, prepared.role, self.owner_kind)
        base_files, _ = self._commit_files(prepared.repository_root, prepared.role, prepared.base_commit, max_bytes)
        wired = ("research-workflow", "resource-planning") if self.owner_kind == "expert" else ("research-workflow",)
        for name in wired:
            entry = f"skills/{name}/SKILL.md"
            if entry in base_files and entry not in files:
                raise ValueError(f"plugin update must preserve the base {name} entrypoint")
        current = (
            self._git(prepared.repository_root, "rev-parse", "HEAD").decode().strip()
        )
        if advance and current not in {prepared.base_commit, head}:
            raise ValueError("another accepted plugin update superseded this base")
        changed = bool(
            self._git(
                prepared.root, "status", "--porcelain", "--untracked-files=all"
            ).strip()
        )
        if changed:
            if head != prepared.base_commit:
                raise ValueError(
                    "a completed plugin Attempt cannot accept different bytes"
                )
            self._git(prepared.root, "add", "--all")
            self._git(
                prepared.root,
                "-c",
                "user.name=Hypoloom Host",
                "-c",
                "user.email=hypoloom@localhost",
                "commit",
                "-qm",
                self._message(prepared),
            )
            head = self._git(prepared.root, "rev-parse", "HEAD").decode().strip()
        # Accepted proposals are ordinary Git branches, including candidates
        # based on a pinned old revision. Existing Attempt/control records,
        # rather than this branch name, decide which future cycle selects them.
        branch = "refs/heads/proposals/" + str(uuid.uuid5(uuid.NAMESPACE_URL, prepared.command_id or prepared.attempt_id))
        self._git(prepared.repository_root, "update-ref", branch, head)
        if advance:
            self._git(prepared.repository_root, "merge", "--ff-only", head)
        return PluginBinding(prepared.run_id, prepared.role, _name(prepared.role), head, self.owner_kind)

    def revert_to(self, run_id: str, role: str, *, target_commit: str, command_id: str, max_bytes: int) -> PluginBinding:
        """Create a HEAD child with the target tree; do not erase/move history."""
        repository = self._repository(run_id, role)
        files, executable = self._commit_files(repository, role, target_commit, max_bytes)
        base = self._git(repository, "rev-parse", "HEAD").decode().strip()
        prepared = self.prepare_update(run_id, role, commit=base,
            attempt_id=str(uuid.uuid5(uuid.NAMESPACE_URL, command_id)), command_id=command_id, target_commit=target_commit)
        if self._validate_update(prepared) == base:
            for name in _files(prepared.root, max_bytes, worktree=True):
                if name not in files:
                    (prepared.root / name).unlink()
            for name, content in files.items():
                path = prepared.root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                path.chmod(0o755 if name in executable else 0o644)
            self._git(prepared.root, "add", "--all")
            self._git(prepared.root, "-c", "user.name=Hypoloom Host", "-c", "user.email=hypoloom@localhost",
                "commit", "--allow-empty", "-qm", self._message(prepared))
        return self.finish_update(prepared, max_bytes=max_bytes, advance=False)

    def accept_request_update(self, request, *, advance: bool = True) -> dict[str, str]:
        """Accept this Host-invoked node's actual plugin tree, not inline files."""
        expected = "GRAPH_EVOLVE" if self.owner_kind == "grounding" else "SELF_EVOLVE"
        if request.command.value != expected or request.schema_version < 14:
            raise ValueError("plugin update acceptance requires its ACI14 owner node")
        suffix = "--grounding-resources" if self.owner_kind == "grounding" else "--expert-resources"
        supplied = [item for item in request.input_artifacts if item.ref.endswith(suffix)]
        if len(supplied) != 1:
            raise ValueError("plugin update requires one frozen resource binding")
        plugin = json.loads(supplied[0].content_bytes())["plugin"]
        if (plugin["run_id"] != request.run_id or plugin["role"] != request.role
                or plugin.get("owner_kind", "expert") != self.owner_kind):
            raise ValueError("plugin update request belongs to another owner")
        prepared = self.prepare_update(request.run_id, request.role, commit=plugin["commit"],
            attempt_id=request.attempt_id, command_id=request.command_id)
        from .agent_sessions import node_memory_limit
        accepted = self.finish_update(prepared,
            max_bytes=node_memory_limit(request, "max_plugin_bytes", 1024 * 1024), advance=advance)
        return {"base_commit": plugin["commit"], "commit": accepted.commit}
