"""Rebuildable human research files and complete, private expert input views.

SQLite facts, transitions and published artifacts remain the authority. The
filesystem view is a polling observation: process logs are not part of the
SQLite read transaction. Full Oracle and historical call diagnostics belong to
the independent monitor/export, not this Host input/publication path. Never use
it to accept or recover an effect. Refresh errors are separate from scientific work.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import sqlite3
import stat
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .agent_aci import EmbeddedInput, WorkspaceArtifact
from .artifacts import _SAFE_ID, renameat2_noreplace
from .monitor import MonitorError, MonitorSnapshot, build_monitor_snapshot
from .research_export import ResearchExportError, research_view_files, hypothesis_markdown
from .state_store import StateStore, StateStoreError, StateTransaction


class ResearchProjectionError(RuntimeError):
    """An observation could not be safely rendered, scoped or published."""


@dataclass(frozen=True, slots=True)
class ResearchProjection:
    """In-memory files, not a persisted protocol or recovery checkpoint."""

    state_root: Path
    run_id: str
    files: Mapping[str, bytes]
    role_files: Mapping[str, Mapping[str, bytes]]


def _json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _jsonl(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows).encode()


def _safe_id(value: str) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise ResearchProjectionError(f"unsafe projection identity: {value!r}")
    return value


def _relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or path.as_posix() != value:
        raise ResearchProjectionError(f"unsafe projection path: {value!r}")
    for part in path.parts:
        _safe_id(part)
    return path


def _read_file(root: Path, relative: str) -> bytes | None:
    """Read only a known artifact path; absence is an explicit view state."""
    if root.is_symlink():
        raise ResearchProjectionError(f"projection source root must not be a symlink: {root}")
    path = root
    for part in _relative(relative).parts:
        path = path / part
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(metadata.st_mode):
            raise ResearchProjectionError(f"projection source must not follow symlinks: {path}")
    if not stat.S_ISREG(metadata.st_mode):
        raise ResearchProjectionError(f"projection source is not a regular file: {path}")
    return path.read_bytes()


_ROUND_FILES = {
    "LITERATURE_REVIEW": "literature.md",
    "HYPOTHESIS_PROPOSAL": "hypotheses.json",
    "EXPERIMENT_DESIGN_IMPLEMENT": "design.md",
    "EXPERIMENT_EXECUTION": "execution.md",
    "RESULT_ANALYSIS": "analysis.md",
    "HYPOTHESIS_UPDATE": "hypothesis_update.md",
    "CYCLE_REPORT": "cycle_report.md",
    "SELF_EVOLVE": "self_evolve.md",
    "ADJUDICATE": "adjudication.md",
}


def _render_projection(
    root: Path, snapshot: MonitorSnapshot, attempts: Sequence[Mapping[str, Any]],
    transitions: Sequence[Mapping[str, Any]], facts: Sequence[Mapping[str, Any]],
    commands: Mapping[str, Mapping[str, Any]], artifacts: Sequence[Mapping[str, Any]],
) -> ResearchProjection:
    files = research_view_files(snapshot, attempts, transitions)
    from .observation_api import product_events, product_graph
    graph = product_graph([asdict(item) for item in snapshot.hypotheses], transitions)
    graph_details = {item["hypothesis_id"]: item for item in graph["nodes"]}
    files["events.jsonl"] = _jsonl(product_events(snapshot.run_id, facts, transitions, attempts, commands=commands))
    indexes = {item.attempt_id: asdict(item) for item in snapshot.attempts}
    for row in attempts:
        observation = json.loads(row["observation_json"] or "{}")
        for name in ("session_binding", "session"):
            if name in observation:
                indexes[row["attempt_id"]][name] = observation[name]
    files["attempts.jsonl"] = _jsonl(list(indexes.values()))
    roles = {_safe_id(item.role): item for item in snapshot.experts}
    scoped: dict[str, dict[str, bytes]] = {
        role: {"background.md": files["background.md"]} for role in roles
    }
    for role, expert in roles.items():
        own_ids = {h.hypothesis_id for h in snapshot.hypotheses if h.proposer_role == role}
        for hypothesis in snapshot.hypotheses:
            if hypothesis.hypothesis_id not in own_ids: continue
            details = dict(graph_details[hypothesis.hypothesis_id])
            if "pruned_by" in details:
                relations = details["pruned_by"]
                details["pruned_by"] = [edge for edge in relations if edge["source"] in own_ids]
                if len(details["pruned_by"]) != len(relations): details["pruning_details_redacted"] = True
            scoped[role][f"graph/nodes/{hypothesis.hypothesis_id}.md"] = hypothesis_markdown(hypothesis, details).encode()
        scoped[role]["graph/edges.jsonl"] = _jsonl([
            edge for line in files["graph/edges.jsonl"].splitlines()
            if (edge := json.loads(line))["hypothesis_id"] in own_ids and edge["parent_hypothesis_id"] in own_ids
        ])
        scoped[role][f"experts/{role}/state.json"] = _json(asdict(expert))
        scoped[role][f"experts/{role}/trace/attempts.jsonl"] = _jsonl([
            item for item in indexes.values()
            if item["role"] == role and item["command"] != "GROUNDING"
        ])

    rounds: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    for row in attempts:
        if row["kind"] != "AGENT":
            continue
        observation = json.loads(row["observation_json"] or "{}")
        request = observation.get("request", {})
        role = request.get("role")
        task = request.get("task_context") or {}
        if request.get("command") == "PAPER_READ" and request.get("schema_version", 0) >= 20:
            if task.get("kind") != "AUXILIARY" or task.get("request_kind") != "PAPER_READ" or task.get("expert_slot") != role:
                raise ResearchProjectionError("stored paper reader ownership differs")
            if role not in roles: continue
            cycle = task.get("cycle")
            if type(cycle) is not int or cycle < 1: raise ResearchProjectionError("stored paper reader cycle is invalid")
            entry = {"attempt_id": row["attempt_id"], "command_id": row["command_id"], "state": row["state"],
                "terminal_reason": row["terminal_reason"], "task": task,
                "paper_sources": observation.get("session", {}).get("paper_sources", [])}
            if row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED":
                entry["candidate"] = observation.get("response", {}).get("typed_candidate")
            rounds.setdefault((role, cycle, "literature.md"), []).append(entry)
            continue
        key = task.get("key", {})
        filename = _ROUND_FILES.get(request.get("command"))
        if role not in roles or task.get("kind") != "RESEARCH_NODE" or filename is None:
            continue
        if (request.get("run_id") != snapshot.run_id
                or request.get("attempt_id") != row["attempt_id"]
                or key.get("expert_slot") != role):
            raise ResearchProjectionError("stored research Attempt ownership differs")
        cycle = key.get("cycle")
        if type(cycle) is not int or cycle < 1:
            raise ResearchProjectionError("stored research cycle is invalid")
        entry = {
            "attempt_id": row["attempt_id"], "command_id": row["command_id"],
            "state": row["state"], "terminal_reason": row["terminal_reason"],
            "task": key,
        }
        # Do not embed request inputs or transcripts: they can contain earlier
        # projections, and run-scoped request inputs may include other roles.
        if row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED":
            entry["candidate"] = observation.get("response", {}).get("typed_candidate")
        rounds.setdefault((role, cycle, filename), []).append(entry)
    for (role, cycle, filename), entries in rounds.items():
        relative = f"experts/{role}/rounds/{cycle}/{filename}"
        content = _json(entries)
        if filename.endswith(".md"):
            content = (f"# {filename[:-3]} — cycle {cycle}\n\n"
                       "Stored Attempt observations; only successful candidates are shown.\n\n"
                       "```json\n").encode() + content + b"```\n"
        scoped[role][relative] = content

    for creation in transitions:
        if creation["workflow_kind"] != "EXPERIMENT" or creation["revision"] != 1:
            continue
        experiment = _safe_id(creation["workflow_id"])
        identity = creation["payload"]
        role = identity.get("role")
        prefix = f"experiments/{experiment}"
        spec = next((f for f in facts if f["fact_type"] == "ExperimentPreregistered"
                     and f.get("experiment_id") == experiment), None)
        outcome = next((f for f in facts if f["fact_type"] == "CanonicalOutcomeAccepted"
                        and f.get("experiment_id") == experiment), None)
        command = commands.get(creation["command_id"], {})
        source = command.get("request", {})
        request_view = {
            "experiment_id": experiment, "command_id": creation["command_id"],
            "identity": identity, "preregistration": None if spec is None else spec["payload"],
            "arguments": source.get("arguments"),
        }
        experiment_files = {"request.json": _json(request_view)}
        process_rows = [row for row in attempts if row["kind"] == "PROCESS"
                        and row.get("experiment_id") == experiment]
        experiment_files["attempts.jsonl"] = _jsonl([
            indexes[row["attempt_id"]] for row in process_rows
        ])
        selected = None
        if outcome is not None:
            terminal = [t for t in transitions if t["workflow_id"] == experiment
                        and t.get("fact_id") == outcome["fact_id"]]
            if len(terminal) != 1:
                raise ResearchProjectionError("canonical outcome has no unique transition")
            selected = terminal[0]["payload"].get("attempt_id")
            process = next((p for p in process_rows if p["attempt_id"] == selected), None)
            if process is None or process["execution_id"] != outcome["execution_id"]:
                raise ResearchProjectionError("canonical outcome does not bind its Process Attempt")
            publication = [a for a in artifacts if a["artifact_id"] == outcome["artifact_id"]
                           and a["attempt_id"] == selected and a["experiment_id"] == experiment
                           and a["execution_id"] == outcome["execution_id"]]
            if len(publication) != 1:
                raise ResearchProjectionError("canonical outcome publication is unavailable")
            artifact = publication[0]
            canonical_path = "/".join(_safe_id(artifact[k]) for k in (
                "run_id", "experiment_id", "execution_id", "attempt_id", "artifact_kind"))
            if artifact["path"] != canonical_path:
                raise ResearchProjectionError("canonical publication path differs from identity")
            result = _read_file(root / "artifacts", canonical_path + "/result.json")
            if result is not None:
                experiment_files["result.json"] = result
        logs = {}
        for process in process_rows:
            attempt = _safe_id(process["attempt_id"])
            logs[attempt] = []
            for name in ("stdout.log", "stderr.log"):
                content = _read_file(root / "artifacts", f"result-staging/{attempt}/logs/{name}")
                if content is not None:
                    experiment_files[f"attempts/{attempt}/{name}"] = content
                    logs[attempt].append(name)
                    if attempt == selected:
                        experiment_files[name] = content
        head = next(t for t in reversed(transitions) if t["workflow_id"] == experiment)
        experiment_files["state.json"] = _json({
            "experiment_id": experiment, "state": head["state"], "revision": head["revision"],
            "canonical_outcome": None if outcome is None else outcome["payload"],
            "canonical_process_attempt_id": selected,
            "result_available": "result.json" in experiment_files, "available_logs": logs,
        })
        files.update({f"{prefix}/{name}": content for name, content in experiment_files.items()})
        if role in scoped:
            # Preregistration includes the full admitted execution contract; the
            # private view instead exposes only this experiment's source IDs.
            own_files = dict(experiment_files)
            own_files["request.json"] = _json({
                "experiment_id": experiment, "command_id": creation["command_id"],
                "identity": identity, "arguments": source.get("arguments"),
            })
            scoped[role].update({f"experts/{role}/trace/{prefix}/{name}": content
                                 for name, content in own_files.items()})
    for role_files in scoped.values():
        files.update({name: content for name, content in role_files.items()
                      if name.startswith("experts/")})
    for name in files:
        _relative(name)
    return ResearchProjection(root, snapshot.run_id, files, scoped)


def build_research_projection(state_root: str | Path, run_id: str) -> ResearchProjection:
    """Read research records once, without a full audit or call-trace replay."""
    try:
        root = Path(state_root).expanduser().resolve()
        _safe_id(run_id)
        with StateStore(root).readonly_connection() as connection:
            connection.execute("BEGIN")
            reader = StateTransaction(connection)
            snapshot = build_monitor_snapshot(root, run_id, _connection=connection,
                _oracle={"ok": None, "issues": []}, _include_overview=False)
            attempts = [a for a in reader.list_attempts() if a["run_id"] == run_id]
            transitions = [dict(t) for t in connection.execute(
                "SELECT t.*, c.command_type FROM workflow_transitions t JOIN workflow_commands c USING(command_id) "
                "WHERE t.run_id=? ORDER BY t.rowid", (run_id,))]
            for transition in transitions:
                transition["payload"] = json.loads(transition.pop("payload_json"))
            commands = {c["command_id"]: dict(c) for c in connection.execute(
                "SELECT * FROM workflow_commands WHERE run_id=?", (run_id,))}
            for command in commands.values():
                command["request"] = json.loads(command.pop("request_json"))
            artifacts = [dict(a) for a in connection.execute(
                "SELECT * FROM execution_artifacts WHERE run_id=?", (run_id,))]
            return _render_projection(root, snapshot, attempts, transitions,
                                      reader.list_facts(run_id), commands, artifacts)
    except (OSError, sqlite3.Error, StateStoreError, MonitorError,
            ResearchExportError, KeyError, TypeError, ValueError) as exc:
        raise ResearchProjectionError(f"cannot build research projection: {exc}") from exc


def role_view_files(projection: ResearchProjection, role: str) -> dict[str, bytes]:
    """Complete own-role files for a request-private, read-only directory.

    The caller persists this selection for retries; never mount the live human
    projection. No run snapshot, global events or peer requests are included.
    """
    try:
        return dict(projection.role_files[role])
    except KeyError as exc:
        raise ResearchProjectionError(f"unknown research role: {role!r}") from exc


def snapshot_role_view(
    projection: ResearchProjection, role: str, *, max_bytes: int,
) -> EmbeddedInput:
    """Optional complete inline transport, explicitly failing over budget."""
    content = _json([WorkspaceArtifact.from_bytes(name, data).to_dict()
                     for name, data in sorted(role_view_files(projection, role).items())])
    if type(max_bytes) is not int or max_bytes < 1 or len(content) > max_bytes:
        raise ResearchProjectionError(
            f"complete research role view needs {len(content)} bytes; limit is {max_bytes}; "
            "use complete request-private files, not truncated history")
    return EmbeddedInput.from_bytes(f"{projection.run_id}--{role}--research-view",
                                    "application/json", content)


def _validate_role_files(files: Mapping[str, bytes], role: str) -> None:
    _safe_id(role)
    for name in files:
        _relative(name)
        if not (name in {"background.md", "graph/edges.jsonl"}
                or name.startswith("graph/nodes/")
                or name.startswith(f"experts/{role}/")):
            raise ResearchProjectionError(f"research view contains a foreign-role or whole-run path: {name}")
    state = json.loads(files.get(f"experts/{role}/state.json", b"{}"))
    if state.get("role") != role or "background.md" not in files or "graph/edges.jsonl" not in files:
        raise ResearchProjectionError("private research view is incomplete or has different role ownership")


def _private_view_path(root: Path, run_id: str, role: str, view_id: str) -> Path:
    path = root
    for part in (_safe_id(run_id), "experts", _safe_id(role), "views", _safe_id(view_id)):
        path = path / part
        if path.is_symlink():
            raise ResearchProjectionError("private research view path must not follow symlinks")
    return path


def _read_private_view(path: Path, role: str) -> dict[str, bytes]:
    if not path.is_dir() or path.is_symlink():
        raise ResearchProjectionError("private research view must be a real directory")
    files = {}
    for entry in path.rglob("*"):
        relative = entry.relative_to(path).as_posix()
        _relative(relative)
        mode = entry.lstat().st_mode
        if stat.S_ISREG(mode):
            files[relative] = entry.read_bytes()
        elif not stat.S_ISDIR(mode):
            raise ResearchProjectionError("private research view contains a non-regular entry")
        if (relative.startswith("experts/") and relative.split("/")[1] != role):
            raise ResearchProjectionError("private research view contains a foreign-role directory")
    _validate_role_files(files, role)
    return files


def read_role_view(
    state_root: str | Path, run_id: str, role: str, view_id: str,
) -> dict[str, bytes]:
    """Read an already bound private view, never the live projection."""
    try:
        root = Path(state_root).expanduser().resolve()
        return _read_private_view(_private_view_path(root, run_id, role, view_id), role)
    except (OSError, TypeError, ValueError) as exc:
        raise ResearchProjectionError(f"cannot read private research view: {exc}") from exc


def freeze_role_view(
    state_root: str | Path, run_id: str, role: str, view_id: str, *,
    projection: ResearchProjection | None = None,
) -> Path:
    """Create one logical node's immutable file selection, or reuse it on retry.

    Existing safe files are reused without reading live DB/artifacts. The Host
    owns view_id selection and the request binding; this is not a new authority.
    """
    staged = None
    try:
        root = Path(state_root).expanduser().resolve()
        destination = _private_view_path(root, run_id, role, view_id)
        if destination.exists():
            _read_private_view(destination, role)
            return destination
        projection = projection or build_research_projection(root, run_id)
        if projection.state_root != root or projection.run_id != run_id:
            raise ResearchProjectionError("private view source identity differs")
        files = role_view_files(projection, role)
        _validate_role_files(files, role)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        staged = Path(tempfile.mkdtemp(prefix=".view-", dir=destination.parent))
        _write_files(staged, files)
        try:
            renameat2_noreplace(staged, destination)
        except FileExistsError:
            _read_private_view(destination, role)
        else:
            staged = None
        return destination
    except (OSError, TypeError, ValueError) as exc:
        raise ResearchProjectionError(f"cannot freeze private research view: {exc}") from exc
    finally:
        if staged is not None:
            shutil.rmtree(staged)


def _write_files(directory: Path, files: Mapping[str, bytes]) -> None:
    for name, content in files.items():
        destination = directory.joinpath(*_relative(name).parts)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(content)
        destination.chmod(0o600)


def _exchange_directories(source: Path, destination: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise OSError("libc does not expose Linux renameat2")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 2):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)


def _validate_old_tree(path: Path, projection: ResearchProjection) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ResearchProjectionError("existing projection must be a real directory")
    raw = _read_file(path, "run.json")
    identity = {} if raw is None else json.loads(raw)
    # A copied run can rebuild its derived view at a different absolute root.
    # The displayed source path is not ownership or recovery authority.
    if identity.get("run_id") != projection.run_id:
        raise ResearchProjectionError("existing projection has different source identity")
    # Canonical facts and Attempts are append-only. A replacement can lose
    # unavailable artifact bytes, but those exact Attempt IDs remain indexed.
    allowed = set(projection.files)
    for name, content in projection.files.items():
        if name.endswith("/attempts.jsonl") and "/experiments/" in "/" + name:
            prefix = name.removesuffix("/attempts.jsonl")
            allowed.update(f"{prefix}/{leaf}" for leaf in ("result.json", "stdout.log", "stderr.log"))
            for line in content.splitlines():
                attempt = _safe_id(json.loads(line)["attempt_id"])
                allowed.update(f"{prefix}/attempts/{attempt}/{leaf}" for leaf in ("stdout.log", "stderr.log"))
    directories = {parent.as_posix() for name in allowed
                   for parent in PurePosixPath(name).parents if parent != PurePosixPath(".")}
    for entry in path.rglob("*"):
        relative = entry.relative_to(path).as_posix()
        _relative(relative)
        mode = entry.lstat().st_mode
        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise ResearchProjectionError("existing projection contains a non-regular entry")
        if (stat.S_ISREG(mode) and relative not in allowed
                or stat.S_ISDIR(mode) and relative not in directories):
            raise ResearchProjectionError(f"unrecognized existing projection entry: {relative}")


def update_research_projection(
    state_root: str | Path, run_id: str, *, projection: ResearchProjection | None = None,
) -> tuple[Path, int]:
    """Atomically publish only the fixed derived directory after committed work.

    A pre-publication error preserves the previous complete directory. A
    cleanup error reports that the new view is visible and the old tree remains.
    The Host must surface this error without rejecting an accepted effect.
    """
    staged: Path | None = None
    published = False
    try:
        root = Path(state_root).expanduser().resolve()
        projection = projection or build_research_projection(root, run_id)
        if projection.state_root != root or projection.run_id != _safe_id(run_id):
            raise ResearchProjectionError("projection source identity differs from destination")
        parent = root / run_id
        if parent.is_symlink():
            raise ResearchProjectionError("run projection parent must not be a symlink")
        parent.mkdir(mode=0o700, exist_ok=True)
        output = parent / "projection"
        replacing = output.exists() or output.is_symlink()
        if replacing:
            _validate_old_tree(output, projection)
        staged = Path(tempfile.mkdtemp(prefix=".projection-", dir=parent))
        _write_files(staged, projection.files)
        if replacing:
            _exchange_directories(staged, output)
        else:
            renameat2_noreplace(staged, output)
        published = True
        if replacing:
            shutil.rmtree(staged)
        staged = None
        return output, len(projection.files)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        suffix = f"; new view published, old derived tree remains at {staged}" if published else ""
        raise ResearchProjectionError(f"cannot refresh research projection: {exc}{suffix}") from exc
    finally:
        if staged is not None and not published:
            shutil.rmtree(staged)


__all__ = ["ResearchProjection", "ResearchProjectionError", "build_research_projection",
           "update_research_projection", "role_view_files", "snapshot_role_view",
           "freeze_role_view", "read_role_view"]
