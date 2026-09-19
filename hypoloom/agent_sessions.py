"""Host-owned CC session and chain-worktree lifecycle (ADR-0023).

Mutable CC files are working context. Only Host node closure publishes their
archive; neither transcript contents nor Git HEAD authorize scientific effects.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .agent_aci import AgentRequest, WorkspaceArtifact
from .artifacts import AgentArtifactKey, ArtifactPublisher, ArtifactPublication
from .domain import AgentCommand
from .execution_workspace import GitTargetBinding
from .expert_batch import BatchHistory
from .expert_loop import ResearchNodeSubkind, ResearchNodeTask, chain_session_scope
from .git_authoring_workspace import (
    GitAuthoringIntegrityError,
    GitAuthoringWorkspaceError,
    GitAuthoringWorkspace,
    PreparedGitAuthoringWorkspace,
    _audit_changed_paths,
    _changed_paths,
    _run_git,
    _validate_prepared,
)

SESSION_VERSION = "chain-session/1"
_NAMESPACE = uuid.UUID("744be49c-1932-4e15-8460-5e5d8d036979")
_INNER = frozenset(
    {
        AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
        AgentCommand.EXPERIMENT_EXECUTION,
        AgentCommand.RESULT_ANALYSIS,
    }
)
_WRITABLE = frozenset(
    {AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, AgentCommand.EXPERIMENT_EXECUTION}
)
_MAX_TRANSCRIPT_BYTES = 64 * 1024 * 1024


def declared_measurement_request(request: AgentRequest) -> bool:
    if request.schema_version < 12:
        return False
    configs = [item for item in request.input_artifacts if item.ref.endswith("--expert-config")]
    if len(configs) != 1:
        return False
    config = json.loads(configs[0].content_bytes())
    result = config.get("component", {}).get("result", {})
    return isinstance(result, Mapping) and result.get("deferred") is True


def node_memory_limit(request: AgentRequest, name: str, default: int) -> int:
    from .profile_v4 import request_node_runtime
    memory = request_node_runtime(request.input_artifacts).get("memory", {})
    if not isinstance(memory, Mapping):
        raise ValueError("node memory settings must be an object")
    value = memory.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("node memory byte limit must be a positive integer")
    return value


def _component(value: str) -> str:
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,200}", value)
        or value in {".", ".."}
    ):
        raise ValueError("invalid session path component")
    return value


def _directory(path: Path) -> Path:
    if path.is_symlink():
        raise GitAuthoringIntegrityError("session path must not be a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir() or path.resolve() != path:
        raise GitAuthoringIntegrityError(
            "session directory must be a real canonical path"
        )
    return path


def _regular(path: Path, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > limit
        ):
            raise GitAuthoringIntegrityError(
                "session/candidate file has invalid shape or size"
            )
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise GitAuthoringIntegrityError(
            "session/candidate file exceeds its byte budget"
        )
    return content


def _write_new(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _validate_transcript(content: bytes, binding: dict[str, Any]) -> bool:
    complete = content.endswith(b"\n")
    records = content.splitlines()
    for raw in records if complete else records[:-1]:
        try:
            record = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise GitAuthoringIntegrityError(
                "CC transcript has an invalid complete record"
            ) from exc
        if not isinstance(record, dict):
            raise GitAuthoringIntegrityError("CC transcript record is not an object")
        identity = record.get("sessionId")
        if identity is not None and (
            not isinstance(identity, str)
            or identity not in {binding["session_id"], binding["parent_session_id"]}
        ):
            raise GitAuthoringIntegrityError(
                "CC transcript contains another chain identity"
            )
    return complete


def _session_cost_state(content: bytes) -> dict[str, Any] | None:
    """Newest saved session totals in a transcript, or None.

    Since CC 2.1.277 a headless session appends a cumulative "cost-state"
    record at exit, and a --resume/--fork-session launch seeds its reported
    modelUsage/total_cost_usd from it instead of starting at zero. The record
    current at preparation time is exactly the carry the next invocation
    reports back, so the Host deducts it to keep per-Attempt accounting
    invocation-exact. Older CC transcripts have no such record.
    """
    complete = content.endswith(b"\n")
    records = content.splitlines()
    state = None
    for raw in records if complete else records[:-1]:
        try:
            record = json.loads(raw)
        except (ValueError, UnicodeError):
            continue
        if isinstance(record, dict) and record.get("type") == "cost-state":
            state = record
    return state


def _git(prepared: PreparedGitAuthoringWorkspace, *args: str) -> bytes:
    return _run_git(
        prepared.git_executable,
        ("-C", str(prepared.worktree_root), *args),
        timeout_seconds=prepared.timeout_seconds,
    )


def _session_transfer(request: AgentRequest):
    debate = request.command is AgentCommand.DEBATE and request.schema_version >= 14
    suffix = "--debate-context" if debate else "--session-promotion"
    values = [item for item in request.input_artifacts if item.ref.endswith(suffix)]
    if not values and not debate: return None
    if len(values) != 1 or request.schema_version < 14: raise ValueError("session transfer requires one frozen context")
    context = json.loads(values[0].content_bytes())
    if debate:
        required = {"debate_id", "round", "participant", "original_attempt_id", "original_session_id", "session_id", "previous_opponent_attempt_id"}
        if set(context) != required or type(context["round"]) is not int or context["round"] not in {1, 2, 3}:
            raise ValueError("Debate session requires its exact three-round context")
        scope = context["participant"]["scope"]
        if (scope["role"], scope["component_id"]) != (request.role, request.component_id): raise ValueError("Debate session belongs to another participant")
        source_id = context["original_attempt_id"]
    else:
        task = request.task_context
        if (not isinstance(task, ResearchNodeTask) or not (request.command in _INNER
                or request.command is AgentCommand.HYPOTHESIS_PROPOSAL and task.key.subkind is ResearchNodeSubkind.SYNTHESIS)):
            raise ValueError("session promotion belongs only to the designated proposal and its inner chains")
        if set(context) != {"debate_id", "source_judge_attempt_id", "role", "component_id", "cycle", "original_session_id", "session_id", "source_attempt_id"}:
            raise ValueError("session promotion has an invalid source shape")
        if (context["role"], context["component_id"], context["cycle"]) != (request.role, request.component_id, task.key.cycle):
            raise ValueError("session promotion belongs to another expert cycle")
        source_id = context["source_attempt_id"]; _component(context["source_judge_attempt_id"])
    _component(context["debate_id"]); _component(source_id)
    for name in ("session_id", "original_session_id"):
        if str(uuid.UUID(context[name])) != context[name]: raise ValueError("session transfer requires actual UUID identities")
    sources = [item for item in request.input_artifacts if item.ref == source_id]
    if len(sources) != 1: raise ValueError("session transfer requires its actual source Attempt")
    source = json.loads(sources[0].content_bytes())
    if (source.get("run_id"), source.get("attempt_id"), source.get("kind"), source.get("state"), source.get("terminal_reason")) != (
            request.run_id, source_id, "AGENT", "CLOSED", "SUCCEEDED"):
        raise ValueError("session transfer source is not an accepted own-run Agent Attempt")
    observation = json.loads(source["observation_json"]); original = AgentRequest.from_dict(observation["request"])
    session = observation["session"]
    if (original.run_id, original.role, original.component_id, original.attempt_id) != (request.run_id, request.role, request.component_id, source_id):
        raise ValueError("session source belongs to another owner")
    if any(session.get(name) != value for name, value in observation["session_binding"].items()):
        raise ValueError("source session differs from its original OPEN binding")
    if debate:
        from .expert_interaction import DebateParticipant, validate_original_debate_scope
        from .participant_controls import ParticipantScope
        validate_original_debate_scope(original.to_dict(), DebateParticipant(ParticipantScope.from_dict(context["participant"]["scope"]), context["participant"]["hypothesis_id"]))
        if session["session_id"] != context["original_session_id"]: raise ValueError("Debate source has another original session")
    else:
        if original.command is not AgentCommand.DEBATE or (session["session_id"], session["parent_session_id"]) != (context["session_id"], context["original_session_id"]):
            raise ValueError("promotion requires the actual winning Debate fork")
        prior = [item for item in original.input_artifacts if item.ref.endswith("--debate-context")]
        if len(prior) != 1: raise ValueError("promoted Debate lacks its source round")
        prior = json.loads(prior[0].content_bytes())
        if (prior["debate_id"], prior["round"]) != (context["debate_id"], 3): raise ValueError("only a final Debate round can be promoted")
    return debate, context, original, session


@dataclass(slots=True, kw_only=True)
class PreparedSessionWorkspace:
    request: AgentRequest
    publisher: ArtifactPublisher
    state_root: Path
    scope_root: Path
    session_root: Path
    input_root: Path
    git_workspace: PreparedGitAuthoringWorkspace
    binding: dict[str, Any]
    session_arguments: tuple[str, ...]
    resources: list[Any] = field(default_factory=list)
    plugin_root: Path | None = None
    memory_root: Path | None = None
    research_root: Path | None = None
    expert_resources: dict[str, Any] | None = None
    plugin_update: Any = None
    peer_memory_root: Path | None = None
    leader_resources: dict[str, Any] | None = None
    final_report_root: Path | None = None
    acceptance_root: Path | None = None
    captured_acceptance_files: dict[str, bytes] = field(default_factory=dict)
    papers: Any = None
    session_usage_baseline: Mapping[str, Any] | None = None

    @property
    def attempt_id(self) -> str:
        return self.request.attempt_id

    @property
    def attempt_root(self) -> Path:
        # Persistent ownership is by chain, not by transient invocation ordinal.
        return self.scope_root

    @property
    def worktree_root(self) -> Path:
        return self.git_workspace.worktree_root

    @property
    def writable(self) -> bool:
        return self.request.command in _WRITABLE

    def capture_patch(self, *, max_bytes: int, max_files: int, allow_empty: bool = False) -> bytes:
        return self.git_workspace.capture_patch(
            max_bytes=max_bytes, max_files=max_files, include_commits=True, allow_empty=allow_empty
        )

    def capture_workspace(self, candidate: Mapping[str, Any] | None = None, *, max_bytes: int | None = None) -> list[dict[str, str]]:
        budget = self.request.budget.max_output_bytes
        if max_bytes is not None:
            if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
                raise GitAuthoringIntegrityError("candidate byte budget must be positive")
            budget = min(budget, max_bytes)
        if self.request.command is AgentCommand.GROUNDING:
            if candidate is None: raise GitAuthoringIntegrityError("Grounding capture requires its structured response")
            declaration = candidate.get("acceptance")
            if declaration is None: return []
            from .shared_acceptance import SharedAcceptance
            if self.acceptance_root is None: raise GitAuthoringIntegrityError("Grounding has no evaluator authoring directory")
            self.captured_acceptance_files = {}
            for relative in SharedAcceptance.from_dict(declaration).parser_files:
                path = self.acceptance_root
                for part in PurePosixPath(relative).parts:
                    path /= part
                    if path.is_symlink(): raise GitAuthoringIntegrityError("evaluator source cannot traverse symlinks")
                content = _regular(path, budget)
                budget -= len(content)
                self.captured_acceptance_files[relative] = content
            return [WorkspaceArtifact.from_bytes(path, data).to_dict() for path, data in sorted(self.captured_acceptance_files.items())]
        if self.request.command is AgentCommand.FINAL_REPORT:
            if self.final_report_root is None or {path.name for path in self.final_report_root.iterdir()} != {"FINAL_REPORT.md"}:
                raise GitAuthoringIntegrityError("final report requires exactly its requested report file")
            content = _regular(self.final_report_root / "FINAL_REPORT.md", budget)
            return [WorkspaceArtifact.from_bytes("FINAL_REPORT.md", content).to_dict()]
        if self.request.command is AgentCommand.CYCLE_REPORT:
            from .expert_memory import ExpertMemory, request_report_component
            if self.memory_root is None or self.expert_resources is None:
                raise GitAuthoringIntegrityError("cycle report lacks its bound memory")
            return [WorkspaceArtifact.from_bytes(path, content).to_dict() for path, content in
                ExpertMemory(self.state_root).capture(self.request.run_id, self.request.role,
                    self.expert_resources["memory_commit"], self.memory_root,
                    cycle=self.request.task_context.key.cycle, max_bytes=budget,
                    component_id=request_report_component(self.request),
                    max_memory_bytes=node_memory_limit(self.request, "max_memory_bytes", 64 * 1024 * 1024)).items()]
        if candidate is not None and (candidate.get("measurement") is not None or candidate.get("evaluation_scope") == "GOAL"):
            if not declared_measurement_request(self.request):
                raise GitAuthoringIntegrityError("declared measurement requires an admitted deferred component")
            from .measurement_contract import ScientificMeasurement
            declaration = None if candidate.get("measurement") is None else ScientificMeasurement.from_dict(candidate["measurement"])
            argv = candidate.get("execution_argv")
            if not isinstance(argv, (list, tuple)) or not argv or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv):
                raise GitAuthoringIntegrityError("measurement execution requires explicit argv")
            paths = set(() if declaration is None else declaration.parser_files)
            for arg in argv:
                if arg.startswith("/input/"):
                    paths.add(arg.removeprefix("/input/"))
                elif not arg.startswith(("/", "-")):
                    relative = arg.removeprefix("./")
                    if (self.worktree_root / relative).is_file():
                        paths.add(relative)
            if len(paths) > 4096:
                raise GitAuthoringIntegrityError("measurement source exceeds its file bound")
            captured = []
            for relative in sorted(paths):
                path = PurePosixPath(relative)
                if (path.is_absolute() or path.as_posix() != relative or any(part in {".", ".."} for part in relative.split("/"))
                        or "\\" in relative or ".git" in path.parts or relative == "implementation.patch"):
                    raise GitAuthoringIntegrityError("measurement source requires safe relative paths")
                source_path = self.worktree_root
                try:
                    for part in path.parts:
                        source_path /= part
                        if source_path.is_symlink():
                            raise GitAuthoringIntegrityError(f"measurement source must not traverse symlinks: /input/{relative}")
                    if source_path.is_dir():
                        raise GitAuthoringIntegrityError(
                            f"measurement argv references a directory, not a file: /input/{relative}; "
                            "directories are not captured; use HYPOLOOM_TARGET_ROOT for the submitted source tree")
                    content = _regular(source_path, budget)
                except FileNotFoundError:
                    raise GitAuthoringIntegrityError(f"measurement source is missing: /input/{relative}") from None
                except OSError as exc:
                    raise GitAuthoringIntegrityError(
                        f"measurement source cannot be read: /input/{relative} ({type(exc).__name__})") from None
                if not content:
                    raise GitAuthoringIntegrityError("measurement source must not be empty")
                budget -= len(content)
                captured.append(WorkspaceArtifact.from_bytes(relative, content).to_dict())
            patch = self.capture_patch(max_bytes=budget, max_files=4096, allow_empty=True)
            captured.append(WorkspaceArtifact.from_bytes("implementation.patch", patch).to_dict())
            return captured
        source = _regular(self.worktree_root / "experiment.py", budget)
        source.decode("utf-8")
        patch = self.capture_patch(max_bytes=budget - len(source), max_files=4096)
        return [
            WorkspaceArtifact.from_bytes("experiment.py", source).to_dict(),
            WorkspaceArtifact.from_bytes("implementation.patch", patch).to_dict(),
        ]

    def finish_node(self) -> dict[str, Any]:
        """Called by the Host after the process group exits, before durable close."""
        _validate_prepared(self.git_workspace)
        changed = _changed_paths(self.git_workspace)
        _audit_changed_paths(self.worktree_root, changed)
        message = (
            f"Hypoloom node {self.request.command_id}\n\nAttempt: {self.attempt_id}"
        )
        last_message = (
            _git(self.git_workspace, "log", "-1", "--format=%B").decode().strip()
        )
        if changed or last_message != message:
            _git(self.git_workspace, "add", "--all", "--", ".")
            _git(
                self.git_workspace,
                "-c",
                "user.name=Hypoloom Host",
                "-c",
                "user.email=hypoloom@localhost",
                "commit",
                "--allow-empty",
                "-m",
                message,
            )
        commit = _git(self.git_workspace, "rev-parse", "HEAD").decode().strip()
        result = {
            **self.binding,
            "base_commit": self.git_workspace.base_commit,
            "commit": commit,
            "launch_arguments": list(self.session_arguments),
            "transcript": None,
        }
        if self.expert_resources is not None:
            result["expert_resources"] = self.expert_resources
        if self.leader_resources is not None:
            result["leader_resources"] = self.leader_resources
        transcript = self.session_root / f"{self.binding['session_id']}.jsonl"
        if not transcript.exists():
            result["transcript_missing"] = True
            return result
        content = _regular(transcript, node_memory_limit(self.request, "max_transcript_bytes", _MAX_TRANSCRIPT_BYTES))
        # A partial final CC write is evidence of interruption, not a completed
        # record; preserve those exact bytes in the archive and label it.
        complete = _validate_transcript(content, self.binding)
        key = AgentArtifactKey(run_id=self.request.run_id, attempt_id=self.attempt_id)
        stage_parent = _directory(self.publisher.root / "agent-staging")
        staging = Path(tempfile.mkdtemp(prefix="transcript-", dir=stage_parent))
        try:
            _write_new(staging / "transcript.jsonl", content)
            published_paths = ["transcript.jsonl"]
            if self.papers is not None:
                published_paths.extend(self.papers.archive(staging))
                result["paper_sources"] = list(self.papers.sources)
            for relative, data in sorted(self.captured_acceptance_files.items()):
                archived = staging / "evaluator" / relative
                archived.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                _write_new(archived, data)
                published_paths.append("evaluator/" + relative)
            if self.final_report_root is not None and (self.final_report_root / "FINAL_REPORT.md").exists():
                _write_new(staging / "FINAL_REPORT.md", _regular(self.final_report_root / "FINAL_REPORT.md", self.request.budget.max_output_bytes))
                published_paths.append("FINAL_REPORT.md")
            if self.request.command is AgentCommand.CYCLE_REPORT and self.memory_root is not None:
                from .expert_memory import report_path, request_report_component
                for relative in ("MEMORY.md", report_path(self.request.task_context.key.cycle, request_report_component(self.request))):
                    source = self.memory_root / relative
                    if source.exists():
                        archived = staging / "memory" / relative
                        archived.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                        _write_new(archived, _regular(source, self.request.budget.max_output_bytes))
                        published_paths.append("memory/" + relative)
            publication = self.publisher.publish(key, staging, tuple(published_paths))
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        # Publisher handles absolute filesystem paths internally; durable node
        # metadata uses the existing portable stable artifact identity.
        archive = {**publication.to_dict(), "path": "/".join(key.as_tuple())}
        result.update(transcript=archive, transcript_complete=complete)
        return result

    def cleanup(self) -> None:
        """Reap per-invocation transports; preserve the chain's recovery context."""
        failures: list[Exception] = []
        for resource in reversed(self.resources):
            try:
                resource.close()
            except Exception as exc:
                failures.append(exc)
        self.resources.clear()
        if failures:
            raise GitAuthoringIntegrityError(
                "session transport cleanup failed"
            ) from failures[0]

    def discard_papers(self) -> None:
        """Called after Host publication, or when an unstarted workspace is discarded."""
        if self.papers is not None:
            self.papers.close()
            self.papers = None


class AgentSessions:
    def __init__(self, state_root: Path, publisher: ArtifactPublisher) -> None:
        self.state_root = Path(state_root).resolve(strict=True)
        self.publisher = publisher

    def retire_adjudicated_worktrees(self, history: BatchHistory,
                                    attempts: Sequence[Mapping[str, Any]], *,
                                    creations: Sequence[Mapping[str, Any]] = ()) -> dict[str, str]:
        """Reconcile accepted ADJ cleanup without changing any scientific row."""
        run, role, component = (_component(history.run_id), _component(history.role), _component(history.component_id))
        busy = set()
        experiments = {row["workflow_id"]: json.loads(row["payload_json"]).get("hypothesis_id")
            for row in creations if row.get("run_id") == run}
        open_hypotheses = set()
        for row in attempts:
            if row.get("run_id") != run or row.get("state") != "OPEN":
                continue
            if row.get("kind") == "PROCESS":
                open_hypotheses.add(experiments.get(row.get("experiment_id")))
                continue
            observed = json.loads(row["observation_json"])
            request = observed.get("request", {})
            key = request.get("task_context", {}).get("key", {})
            if (request.get("role"), request.get("component_id")) == (role, component):
                busy.add((key.get("cycle"), key.get("chain")))
        errors = {}
        for batch in history.batches:
            if batch.selection is None:
                continue
            candidate = batch.selection.get("candidate", {})
            chains = set(candidate.get("prune", ())) | {
                item["chain_id"] for item in candidate.get("verdicts", ()) if item.get("status") == "refuted"}
            if not chains <= set(batch.members):
                raise ValueError("worktree cleanup differs from its accepted batch")
            if isinstance(batch.cycle, bool) or not isinstance(batch.cycle, int) or batch.cycle < 1:
                raise ValueError("worktree cleanup requires a valid batch cycle")
            for chain in sorted(chains):
                scope = chain_session_scope(chain)
                if (batch.cycle, chain) in busy or batch.members[chain] in open_hypotheses:
                    continue
                source = self.state_root / run / "sessions" / role / f"c{batch.cycle}" / component / scope / "source"
                hot = source / "git-authoring-worktrees" / "chain" / "worktree"
                if not hot.exists() and not hot.is_symlink():
                    continue
                identity = f"{run}/{role}/{component}/c{batch.cycle}/{scope}"
                try:
                    if source.resolve() != source:
                        raise GitAuthoringIntegrityError("chain cleanup source is not its canonical private path")
                    GitAuthoringWorkspace(source).retire_worktree("chain", branch=f"prom/{role}/r{batch.cycle}/{scope}")
                except (GitAuthoringWorkspaceError, OSError, ValueError) as exc:
                    errors[identity] = str(exc)
                    logging.getLogger(__name__).warning("chain worktree cleanup retained %s: %s", identity, exc)
        return errors

    @staticmethod
    def binding(request: AgentRequest) -> dict[str, Any]:
        task = request.task_context
        transfer = _session_transfer(request)
        if transfer is not None and transfer[0]:
            context = transfer[1]
            scope = "debate-" + _component(context["debate_id"])
            return {"version": SESSION_VERSION, "session_id": context["session_id"],
                "parent_session_id": context["original_session_id"], "scope": scope,
                "chain": context["participant"]["scope"]["chain"], "cycle": task.cycle,
                "branch": f"prom/{request.role}/r{task.cycle}/{scope}"}
        if request.command is AgentCommand.GRAPH_EVOLVE:
            from .expert_loop import AuxiliaryTask, AuxiliaryRequestKind
            if (request.schema_version < 14 or not isinstance(task, AuxiliaryTask)
                    or task.kind is not AuxiliaryRequestKind.GRAPH_EVOLVE
                    or request.role != "grounding" or request.component_id != "grounding"):
                raise ValueError("manager session requires its run-scoped Grounding owner")
            return {"version": SESSION_VERSION,
                "session_id": str(uuid.uuid5(_NAMESPACE, request.run_id + "/grounding/manager")),
                "parent_session_id": None, "scope": "grounding-manager", "chain": None,
                "cycle": 1, "branch": "prom/grounding/r1/grounding-manager"}
        cycle = (
            task.key.cycle
            if isinstance(task, ResearchNodeTask)
            else (getattr(task, "cycle", None) or 1)
        )
        common = f"{request.run_id}/{request.role}/{request.component_id}/r{cycle}"
        synthesis = (
            request.command is AgentCommand.HYPOTHESIS_PROPOSAL
            and isinstance(task, ResearchNodeTask)
            and task.key.subkind is ResearchNodeSubkind.SYNTHESIS
        )
        inner = request.command in _INNER
        chain = task.key.chain if isinstance(task, ResearchNodeTask) else None
        # Judges reconstruct their context on every Attempt, including retries.
        # Expert chains and the graph manager keep their existing conversation.
        stateless = request.command in {AgentCommand.OBSERVER, AgentCommand.DEBATE_JUDGE}
        scope_key = request.command_id + "/" + request.attempt_id if stateless else request.command_id
        scope = (
            chain_session_scope(chain)
            if inner
            else "synthesis"
            if synthesis
            else "grounding-preparation"
            if request.command is AgentCommand.GROUNDING and request.schema_version >= 15
            else str(uuid.uuid5(_NAMESPACE, scope_key))
        )
        session_id = str(uuid.uuid5(_NAMESPACE, common + "/" + scope))
        parent = (
            str(uuid.uuid5(_NAMESPACE, common + "/synthesis"))
            if inner
            else None
        )
        if transfer is not None:
            promotion = transfer[1]
            if inner: parent = promotion["session_id"]
            else: session_id, parent = promotion["session_id"], promotion["original_session_id"]
        return {
            "version": SESSION_VERSION,
            "session_id": session_id,
            "parent_session_id": parent,
            "scope": scope,
            "chain": (chain if chain is not None else "1") if inner else None,
            "cycle": cycle,
            "branch": f"prom/{request.role}/r{cycle}/{scope}",
        }

    def prepare(
        self, request: AgentRequest, binding: GitTargetBinding | None
    ) -> PreparedSessionWorkspace:
        if request.schema_version < 11:
            raise ValueError("persistent sessions require ACI 11")
        descriptor = self.binding(request)
        transfer = _session_transfer(request)
        transcript_limit = node_memory_limit(request, "max_transcript_bytes", _MAX_TRANSCRIPT_BYTES)
        common = (
            self.state_root
            / _component(request.run_id)
            / "sessions"
            / _component(request.role)
            / f"c{descriptor['cycle']}"
            / _component(request.component_id)
        )
        if request.command is AgentCommand.GRAPH_EVOLVE:
            common = self.state_root / _component(request.run_id) / "grounding" / "sessions"
        root = _directory(common / descriptor["scope"])
        session_root = _directory(root / "projects")
        own_transcript = session_root / f"{descriptor['session_id']}.jsonl"
        source_content = None
        retained_source = None
        if transfer is not None and (transfer[0] or request.command not in _INNER):
            _, context, original, session = transfer
            archive = session["transcript"]
            key = AgentArtifactKey(run_id=request.run_id, attempt_id=original.attempt_id)
            source_directory = self.publisher.path_for(key)
            if archive["path"] != "/".join(key.as_tuple()): raise ValueError("session transfer archive has another source")
            publication = ArtifactPublication(key=key, schema_version=archive["schema_version"],
                digest_algorithm=archive["digest_algorithm"], digest=archive["digest"], path=str(source_directory),
                relative_paths=tuple(archive["relative_paths"]), reused=archive["reused"])
            if not self.publisher.verify(publication): raise ValueError("session transfer source archive failed existing publication verification")
            source_content = _regular(source_directory / "transcript.jsonl", transcript_limit)
            _validate_transcript(source_content, session)
            source_root = (self.state_root / _component(request.run_id) / "sessions" / _component(request.role)
                / f"c{session['cycle']}" / _component(request.component_id) / _component(session["scope"]) / "source")
            if source_root.resolve() != source_root:
                raise GitAuthoringIntegrityError("session transfer source is not its canonical private path")
            retained_source = GitAuthoringWorkspace(source_root).retained("chain", base_commit=session["base_commit"])
            if not transfer[0] and not own_transcript.exists(): _write_new(own_transcript, source_content)
        session_usage_baseline = None
        if own_transcript.exists():
            own_content = _regular(own_transcript, transcript_limit)
            _validate_transcript(own_content, descriptor)
            session_arguments = ("--resume", descriptor["session_id"])
            session_usage_baseline = _session_cost_state(own_content)
        elif descriptor["parent_session_id"] is not None:
            parent = (
                common
                / "synthesis"
                / "projects"
                / f"{descriptor['parent_session_id']}.jsonl"
            )
            content = source_content if source_content is not None else _regular(parent, transcript_limit)
            _validate_transcript(
                content,
                transfer[3] if source_content is not None else {
                    "session_id": descriptor["parent_session_id"],
                    "parent_session_id": transfer[1]["original_session_id"] if transfer is not None else None,
                },
            )
            copy = session_root / parent.name
            if copy.exists():
                if _regular(copy, transcript_limit) != content:
                    raise GitAuthoringIntegrityError(
                        "frozen synthesis context changed before fork"
                    )
            else:
                _write_new(copy, content)
            session_arguments = (
                "--resume",
                descriptor["parent_session_id"],
                "--fork-session",
                "--session-id",
                descriptor["session_id"],
            )
            session_usage_baseline = _session_cost_state(content)
        else:
            session_arguments = ("--session-id", descriptor["session_id"])
        # Exact node inputs remain independently named even though CC now also
        # carries chain history. Retry inputs never follow a mutable whole run.
        inputs = _directory(root / "inputs" / _component(request.attempt_id))
        expected_inputs = {
            f"{index:04d}.txt" for index in range(len(request.input_artifacts))
        }
        if {item.name for item in inputs.iterdir()} - expected_inputs:
            raise GitAuthoringIntegrityError(
                "Attempt input inventory differs from its frozen request"
            )
        for index, item in enumerate(request.input_artifacts):
            destination = inputs / f"{index:04d}.txt"
            content = item.content_bytes()
            if destination.exists():
                if _regular(destination, request.max_input_bytes) != content:
                    raise GitAuthoringIntegrityError("Attempt input bytes changed")
            else:
                _write_new(destination, content)
        source_root = _directory(root / "source")
        manager = GitAuthoringWorkspace(source_root)
        repository = manager.root / "chain" / "repository.git"
        if not repository.exists():
            if binding is None and retained_source is None:
                seed = _directory(root / "seed")
                _run_git(
                    manager.git_executable, ("init", str(seed)), timeout_seconds=30
                )
                _run_git(
                    manager.git_executable,
                    (
                        "-C",
                        str(seed),
                        "-c",
                        "user.name=Hypoloom Host",
                        "-c",
                        "user.email=hypoloom@localhost",
                        "commit",
                        "--allow-empty",
                        "-m",
                        "Empty CC workspace",
                    ),
                    timeout_seconds=30,
                )
                commit = (
                    _run_git(
                        manager.git_executable,
                        ("-C", str(seed), "rev-parse", "HEAD"),
                        timeout_seconds=30,
                    )
                    .decode()
                    .strip()
                )
                binding = GitTargetBinding(checkout=seed, commit=commit)
                # The seed is a separate asset relative to this private manager.
            prepared = (manager.prepare(binding, "chain") if retained_source is None else
                manager.prepare_from_retained(retained_source, "chain", commit=transfer[3]["commit"]))
            _git(prepared, "switch", "-c", descriptor["branch"])
        else:
            base = (
                transfer[3]["commit"] if retained_source is not None else binding.commit
                if binding is not None or retained_source is not None
                else _run_git(
                    manager.git_executable,
                    (
                        "--git-dir",
                        str(repository),
                        "rev-list",
                        "--max-parents=0",
                        "HEAD",
                    ),
                    timeout_seconds=30,
                )
                .decode()
                .strip()
            )
            prepared = manager.materialize("chain", base_commit=base, branch=descriptor["branch"])
        workspace = PreparedSessionWorkspace(
            request=request,
            publisher=self.publisher,
            state_root=self.state_root,
            scope_root=root,
            session_root=session_root,
            input_root=inputs,
            git_workspace=prepared,
            binding=descriptor,
            session_arguments=session_arguments,
            session_usage_baseline=session_usage_baseline,
        )
        if request.command is AgentCommand.FINAL_REPORT:
            workspace.final_report_root = _directory(root / "report" / _component(request.attempt_id))
        from .agent_aci import grounding_acceptance_request
        if grounding_acceptance_request(request) and request.command is not AgentCommand.PAPER_READ:
            if request.command is AgentCommand.GROUNDING:
                workspace.acceptance_root = _directory(root / "acceptance-authoring")
            else:
                supplied = [item for item in request.input_artifacts if item.ref.endswith("--shared-acceptance")]
                if not supplied and (request.command is AgentCommand.FINAL_REPORT
                        or getattr(getattr(request.task_context, "kind", None), "value", None) == "GROUNDING_QUERY_PLAN"):
                    supplied = []
                elif len(supplied) != 1: raise GitAuthoringIntegrityError("expert request requires its one accepted evaluator")
            if request.command is not AgentCommand.GROUNDING and supplied:
                value = json.loads(supplied[0].content_bytes())
                from .shared_acceptance import SharedAcceptance, acceptance_source_files
                files = {item.path: item.content_bytes() for raw in value["files"] if (item := WorkspaceArtifact.from_dict(raw))}
                selected = acceptance_source_files(SharedAcceptance.from_dict(value["acceptance"]), files)
                workspace.acceptance_root = _directory(inputs / "shared-acceptance")
                for relative, data in selected.items():
                    destination = workspace.acceptance_root / relative
                    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    if destination.exists():
                        if _regular(destination, len(data)) != data: raise GitAuthoringIntegrityError("frozen shared evaluator source changed")
                    else: _write_new(destination, data)
        leader_inputs = [item for item in request.input_artifacts if item.ref.endswith("--leader-resources")]
        if leader_inputs:
            if (len(leader_inputs) != 1 or request.schema_version < 14
                    or request.command is not AgentCommand.HYPOTHESIS_PROPOSAL
                    or not isinstance(request.task_context, ResearchNodeTask)
                    or request.task_context.key.subkind is not ResearchNodeSubkind.SYNTHESIS):
                raise GitAuthoringIntegrityError("peer memory is available only to the designated Leader SYNTHESIS")
            leader = json.loads(leader_inputs[0].content_bytes())
            if (not isinstance(leader, dict)
                    or set(leader) != {"source_attempt_id", "role", "component_id", "cycle", "peer_memory_commits"}
                    or (leader["role"], leader["component_id"], leader["cycle"]) !=
                    (request.role, request.component_id, request.task_context.key.cycle)):
                raise GitAuthoringIntegrityError("Leader memory belongs to another participant/cycle")
            _component(leader["source_attempt_id"])
            peers = leader["peer_memory_commits"]
            if not isinstance(peers, dict) or request.role in peers:
                raise GitAuthoringIntegrityError("Leader memory requires distinct peer owners")
            from .expert_memory import ExpertMemory
            memory = ExpertMemory(self.state_root)
            parent = _directory(root / "peer-memory" / _component(request.attempt_id))
            remaining = node_memory_limit(request, "max_memory_bytes", 64 * 1024 * 1024)
            for peer, commit in sorted(peers.items()):
                destination = _directory(parent / _component(peer))
                files = memory.files(request.run_id, peer, commit, max_bytes=remaining)
                remaining -= sum(map(len, files.values()))
                if {path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()} - set(files):
                    raise GitAuthoringIntegrityError("frozen peer memory inventory changed")
                for name, content in files.items():
                    path = destination / name; _directory(path.parent)
                    if path.exists():
                        if _regular(path, len(content)) != content: raise GitAuthoringIntegrityError("frozen peer memory bytes changed")
                    else: _write_new(path, content)
            workspace.peer_memory_root = parent; workspace.leader_resources = leader
        grounding_inputs = [item for item in request.input_artifacts if item.ref.endswith("--grounding-resources")]
        if grounding_inputs:
            from .expert_plugin import ExpertPluginStore
            from .expert_loop import AuxiliaryTask
            if (len(grounding_inputs) != 1 or request.command not in {AgentCommand.GRAPH_EVOLVE, AgentCommand.OBSERVER, AgentCommand.FINAL_REPORT, AgentCommand.DEBATE_JUDGE}
                    or request.schema_version < 14 or request.role != "grounding" or request.component_id != "grounding"
                    or not isinstance(request.task_context, AuxiliaryTask)
                    or request.task_context.expert_slot is not None or request.task_context.component_id is not None):
                raise GitAuthoringIntegrityError("Grounding resources require their management or Observer owner")
            resources = json.loads(grounding_inputs[0].content_bytes()); plugin = resources["plugin"]
            if plugin.get("run_id") != request.run_id or plugin.get("role") != "grounding" or plugin.get("owner_kind") != "grounding":
                raise GitAuthoringIntegrityError("Grounding plugin binding belongs to another owner")
            store = ExpertPluginStore(self.state_root, owner_kind="grounding")
            snapshot = store.read(request.run_id, "grounding", commit=plugin["commit"],
                attempt_id=request.attempt_id, max_bytes=node_memory_limit(request, "max_plugin_bytes", 1024 * 1024))
            workspace.resources.append(snapshot); workspace.plugin_root = snapshot.root
            if request.command is AgentCommand.GRAPH_EVOLVE:
                workspace.plugin_update = store.prepare_update(request.run_id, "grounding", commit=plugin["commit"],
                    attempt_id=request.attempt_id, command_id=request.command_id)
        elif request.command is AgentCommand.GRAPH_EVOLVE:
            raise GitAuthoringIntegrityError("manager requires its frozen Grounding plugin resources")
        resource_input = next((item for item in request.input_artifacts if item.ref.endswith("--expert-resources")), None)
        if resource_input is not None and grounding_inputs:
            raise GitAuthoringIntegrityError("Grounding and expert resource owners cannot be mixed")
        if resource_input is not None:
            from .expert_memory import ExpertMemory
            from .expert_plugin import ExpertPluginStore
            resources = json.loads(resource_input.content_bytes())
            plugin = resources["plugin"]
            if (plugin["run_id"] != request.run_id or plugin["role"] != request.role
                    or plugin.get("owner_kind", "expert") != "expert"):
                raise GitAuthoringIntegrityError("plugin binding belongs to another expert")
            snapshot = ExpertPluginStore(self.state_root).read(request.run_id, request.role,
                commit=plugin["commit"], attempt_id=request.attempt_id,
                max_bytes=node_memory_limit(request, "max_plugin_bytes", request.max_input_bytes))
            workspace.resources.append(snapshot); workspace.plugin_root = snapshot.root
            workspace.expert_resources = resources
            if request.command is AgentCommand.SELF_EVOLVE:
                workspace.plugin_update = ExpertPluginStore(self.state_root).prepare_update(
                    request.run_id, request.role, commit=plugin["commit"],
                    attempt_id=request.attempt_id, command_id=request.command_id)
            workspace.memory_root = ExpertMemory(self.state_root).prepare(request.run_id, request.role,
                resources["memory_commit"], root / "memory" / _component(request.attempt_id),
                writable=request.command is AgentCommand.CYCLE_REPORT,
                max_bytes=node_memory_limit(request, "max_memory_bytes", 64 * 1024 * 1024))
            if "research_view_id" in resources:
                from .research_projection import read_role_view
                view_id = _component(resources["research_view_id"])
                read_role_view(self.state_root, request.run_id, request.role, view_id)
                workspace.research_root = self.state_root / _component(request.run_id) / "experts" / _component(request.role) / "views" / view_id
        elif request.command is AgentCommand.SELF_EVOLVE:
            raise GitAuthoringIntegrityError("self-evolution requires the expert's frozen plugin resources")
        return workspace
