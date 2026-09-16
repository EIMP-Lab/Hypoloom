"""Host-owned expert report Git history; never scientific acceptance authority."""

from __future__ import annotations

import os
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from .git_authoring_workspace import GitAuthoringPreparationError, _run_git


class MemoryError(ValueError):
    pass


def _safe(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,200}", value):
        raise MemoryError("invalid memory owner identity")
    return value


def _atomic_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.resolve() != path.parent:
        raise MemoryError("memory file must not traverse a symlink")
    descriptor, temporary = tempfile.mkstemp(prefix=".memory-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def report_path(cycle: int, component_id: str | None = None) -> str:
    if type(cycle) is not int or cycle < 1: raise MemoryError("report cycle must be positive")
    prefix = "rounds/" if component_id is None else "rounds/" + _safe(component_id) + "/"
    return prefix + str(cycle) + ".md"


def request_report_component(request) -> str | None:
    inputs = [item for item in request.input_artifacts if item.ref.endswith("--expert-resources")]
    if len(inputs) > 1: raise MemoryError("ambiguous expert report resources")
    resources = json.loads(inputs[0].content_bytes()) if inputs else {}
    component = resources.get("report_component_id")
    if component is not None and (request.schema_version < 14 or component != request.component_id):
        raise MemoryError("report component differs from its owning request")
    return None if component is None else _safe(component)


def validate_report(files: Mapping[str, bytes], *, cycle: int,
                    component_id: str | None = None,
                    facts: Sequence[Mapping[str, Any]], max_bytes: int) -> None:
    """Validate the actual two edited files and their explicit assessment cites."""
    if isinstance(cycle, bool) or not isinstance(cycle, int) or cycle < 1:
        raise MemoryError("report cycle must be positive")
    relative = report_path(cycle, component_id)
    if set(files) != {"MEMORY.md", relative}:
        raise MemoryError("report may write only MEMORY.md and its exact round")
    if any(not isinstance(content, bytes) for content in files.values()) or sum(map(len, files.values())) > max_bytes:
        raise MemoryError("report exceeds its admitted byte budget")
    try:
        texts = {path: content.decode("utf-8") for path, content in files.items()}
        if any(not text.strip() or "\x00" in text for text in texts.values()):
            raise MemoryError("report must contain nonempty UTF-8 Markdown")
        report = texts[relative]
        parts = report.split("\n---\n", 1)
        if not report.startswith("---\n") or len(parts) != 2 or not parts[1].strip():
            raise MemoryError("round report requires assessment frontmatter and Markdown body")
        header = yaml.safe_load(parts[0][4:])
    except (UnicodeError, yaml.YAMLError) as exc:
        raise MemoryError("invalid report Markdown/frontmatter") from exc
    if not isinstance(header, dict) or set(header) != {"assessments"} or not isinstance(header["assessments"], list):
        raise MemoryError("round frontmatter must contain an assessments list")
    accepted = {str(row["fact_id"]): row for row in facts if row.get("fact_type") == "HypothesisAssessed"}
    cited: set[str] = set()
    for item in header["assessments"]:
        if not isinstance(item, dict) or set(item) != {"fact_id", "hypothesis_id", "assessment"}:
            raise MemoryError("invalid assessment reference")
        if not all(isinstance(value, str) for value in item.values()):
            raise MemoryError("assessment references must be strings")
        row = accepted.get(item["fact_id"])
        if row is None or item["fact_id"] in cited or str(row.get("hypothesis_id")) != item["hypothesis_id"] or row["payload"].get("assessment") != item["assessment"]:
            raise MemoryError("report assessment must match one accepted scientific fact")
        cited.add(item["fact_id"])
    if cited != set(accepted):
        raise MemoryError("report must cite every assessment supplied for its cycle")


class ExpertMemory:
    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root).resolve(strict=True)
        self.git = shutil.which("git")
        if self.git is None:
            raise MemoryError("expert memory requires Git")

    def _repository(self, run_id: str, role: str) -> Path:
        path = self.state_root / _safe(run_id) / "experts" / _safe(role) / "memory"
        if path.resolve() != path or path.is_symlink():
            raise MemoryError("memory repository must be a private real directory")
        return path

    def _git(self, path: Path, *args: str) -> bytes:
        try:
            return _run_git(self.git, ("-C", str(path), *args), timeout_seconds=30)
        except GitAuthoringPreparationError as exc:
            raise MemoryError(str(exc)) from exc

    def ensure(self, run_id: str, role: str) -> str:
        path = self._repository(run_id, role)
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not (path / ".git").exists():
            self._git(path, "init")
            self._git(path, "symbolic-ref", "HEAD", "refs/heads/memory")
            _atomic_file(path / "MEMORY.md", b"# Expert memory\n\nNo completed cycles yet.\n")
            self._git(path, "add", "--", "MEMORY.md")
            self._commit(path, f"Initialize memory for {run_id}/{role}")
        return self._git(path, "rev-parse", "HEAD").decode().strip()

    def _commit(self, path: Path, message: str) -> None:
        self._git(path, "-c", "user.name=Hypoloom Host", "-c",
                  "user.email=hypoloom@localhost", "commit", "--allow-empty", "-m", message)

    def files(self, run_id: str, role: str, commit: str, *, max_bytes: int = 64 * 1024 * 1024) -> dict[str, bytes]:
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise MemoryError("memory requires an explicit Git commit")
        path = self._repository(run_id, role)
        self._git(path, "merge-base", "--is-ancestor", commit, "HEAD")
        result: dict[str, bytes] = {}
        for record in self._git(path, "ls-tree", "-rz", commit).split(b"\x00"):
            if not record:
                continue
            metadata, raw_path = record.split(b"\t", 1)
            name = raw_path.decode("utf-8")
            if not metadata.startswith(b"100644 blob ") or not (name == "MEMORY.md" or re.fullmatch(r"rounds/(?:[A-Za-z0-9][A-Za-z0-9_.-]{0,200}/)?[1-9][0-9]*\.md", name)):
                raise MemoryError("memory contains an unsupported path or file type")
            result[name] = self._git(path, "show", f"{commit}:{name}")
            if sum(map(len, result.values())) > max_bytes:
                raise MemoryError("complete memory snapshot exceeds its byte budget")
        return result

    def commit_report(self, run_id: str, role: str, base_commit: str, command_id: str,
                      files: Mapping[str, bytes], *, cycle: int,
                      component_id: str | None = None,
                      facts: Sequence[Mapping[str, Any]], max_bytes: int,
                      max_memory_bytes: int = 64 * 1024 * 1024) -> str:
        if isinstance(max_memory_bytes, bool) or not isinstance(max_memory_bytes, int) or max_memory_bytes <= 0:
            raise MemoryError("memory byte limit must be a positive integer")
        validate_report(files, cycle=cycle, component_id=component_id, facts=facts, max_bytes=max_bytes)
        path = self._repository(run_id, role)
        original = self.files(run_id, role, base_commit, max_bytes=max_memory_bytes)
        combined = {**original, **files}
        if sum(map(len, combined.values())) > max_memory_bytes:
            raise MemoryError("complete memory snapshot exceeds its byte budget")
        head = self.ensure(run_id, role)
        message = f"Hypoloom CYCLE_REPORT {command_id}"
        if head != base_commit:
            if self._git(path, "log", "-1", "--format=%B").decode().strip() == message and self.files(run_id, role, head, max_bytes=max_memory_bytes) == combined:
                return head
            raise MemoryError("report base differs from current expert memory")
        relative = report_path(cycle, component_id)
        if relative in original:
            raise MemoryError("a completed round report cannot be overwritten")
        for name, content in files.items():
            _atomic_file(path / name, content)
        self._git(path, "add", "--", "MEMORY.md", relative)
        self._commit(path, message)
        return self._git(path, "rev-parse", "HEAD").decode().strip()

    def prepare(self, run_id: str, role: str, commit: str, destination: Path, *, writable: bool, max_bytes: int = 64 * 1024 * 1024) -> Path:
        original = self.files(run_id, role, commit, max_bytes=max_bytes)
        if not destination.is_relative_to(self.state_root / _safe(run_id) / "sessions" / _safe(role)) or destination.resolve() != destination:
            raise MemoryError("memory view must be inside the owning expert session")
        if writable:
            if not destination.exists():
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                self._git(self._repository(run_id, role), "worktree", "add", "--detach", str(destination), commit)
            if self._git(destination, "rev-parse", "HEAD").decode().strip() != commit:
                raise MemoryError("report worktree differs from its selected memory commit")
        else:
            destination.mkdir(mode=0o700, parents=True, exist_ok=True)
            for name, content in original.items():
                path = destination / name
                if path.exists():
                    if path.is_symlink() or path.read_bytes() != content:
                        raise MemoryError("read-only memory view changed")
                else:
                    _atomic_file(path, content)
        return destination

    def capture(self, run_id: str, role: str, commit: str, directory: Path, *, cycle: int, max_bytes: int, max_memory_bytes: int = 64 * 1024 * 1024, component_id: str | None = None) -> dict[str, bytes]:
        original = self.files(run_id, role, commit, max_bytes=max_memory_bytes)
        writable = {"MEMORY.md", report_path(cycle, component_id)}
        found: dict[str, bytes] = {}
        for path in directory.rglob("*"):
            name = path.relative_to(directory).as_posix()
            if name == ".git": continue
            if path.is_symlink(): raise MemoryError("report contains a symlink")
            if path.is_dir(): continue
            info = path.stat()
            if info.st_nlink != 1 or info.st_size > max_bytes:
                raise MemoryError("report contains an invalid file")
            found[name] = path.read_bytes()
        expected = set(original) | writable
        missing, extra = expected - set(found), set(found) - expected
        modified = {name for name, content in original.items()
                    if name not in writable and name in found and found[name] != content}
        problems = []
        for names, message in ((missing, "report missing required files"),
                (extra, "report contains files outside the allowed set"),
                (modified, "report modified read-only memory files")):
            if names: problems.append(message + ": " + ", ".join("/memory/" + name for name in sorted(names)))
        if problems:
            raise MemoryError("; ".join(problems) + "; expected writable paths: "
                              + ", ".join("/memory/" + name for name in sorted(writable)))
        result = {name: found[name] for name in writable}
        if sum(map(len, result.values())) > max_bytes: raise MemoryError("report exceeds its byte budget")
        return result
