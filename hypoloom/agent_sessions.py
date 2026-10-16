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
from typing import Any, ClassVar, Mapping, Sequence

from .agent_aci import AgentACIError, AgentRequest, WorkspaceArtifact
from .artifacts import AgentArtifactKey, ArtifactPublisher, ArtifactPublication
from .domain import AgentCommand
from .context_management import continuation_observation
from .provider_observation import session_cost_state as _session_cost_state
from .execution_workspace import GitTargetBinding
from .expert_batch import BatchHistory
from .expert_loop import ResearchNodeSubkind, ResearchNodeTask, chain_session_scope
from .git_authoring_workspace import (
    GitAuthoringDirtyWorktreeError,
    GitAuthoringIntegrityError,
    GitAuthoringWorkspaceError,
    GitAuthoringWorkspace,
    PreparedGitAuthoringWorkspace,
    _audit_changed_paths,
    _deleted_paths,
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
# Judges reconstruct their context on every Attempt, including retries, so a
# retry of these commands is a brand-new session with byte-identical input.
STATELESS_SESSION_COMMANDS = frozenset({AgentCommand.OBSERVER, AgentCommand.DEBATE_JUDGE, AgentCommand.LEADER})
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
        if original.command is not AgentCommand.DEBATE or session["session_id"] != context["session_id"]:
            raise ValueError("promotion requires the actual winning Debate session")
        prior = [item for item in original.input_artifacts if item.ref.endswith("--debate-context")]
        if len(prior) != 1: raise ValueError("promoted Debate lacks its source round")
        prior = json.loads(prior[0].content_bytes())
        if (prior["debate_id"], prior["round"]) != (context["debate_id"], 3): raise ValueError("only a final Debate round can be promoted")
        from .expert_interaction import debate_session_id
        if (session["session_id"] != debate_session_id(prior["session_id"], source_id, original.schema_version)
                or session["parent_session_id"] != (None if original.schema_version >= 29 else context["original_session_id"])
                or prior["original_session_id"] != context["original_session_id"]):
            raise ValueError("promotion changed the actual final round or original expert session")
        # Validate the original expert source as well as the winning round.
        _session_transfer(original)
    return debate, context, original, session


class SessionFinishError(GitAuthoringIntegrityError):
    """Partial closure evidence; never an accepted research output."""

    def __init__(self, message: str, metadata: dict[str, Any]) -> None:
        super().__init__(message)
        self.metadata = metadata


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
    prior_web_urls: frozenset[str] = frozenset()
    continuation_observation: Mapping[str, Any] | None = None

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
            from .final_report import validate_final_report
            validate_final_report({"FINAL_REPORT.md": content}, self.request)
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

    def _commit_node(self) -> str:
        _validate_prepared(self.git_workspace)
        changed = _changed_paths(self.git_workspace)
        _audit_changed_paths(self.worktree_root, changed,
                             deleted_paths=_deleted_paths(self.git_workspace))
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
        return _git(self.git_workspace, "rev-parse", "HEAD").decode().strip()

    def finish_node(self) -> dict[str, Any]:
        """Archive independent evidence even if committing the workspace fails."""
        result = {
            **self.binding,
            "base_commit": self.git_workspace.base_commit,
            "commit": None,
            "launch_arguments": list(self.session_arguments),
            "transcript": None,
        }
        errors = []
        try:
            result["commit"] = self._commit_node()
        except (OSError, ValueError, GitAuthoringWorkspaceError) as exc:
            result["commit_error"] = str(exc)[:1024]
            errors.append(exc)
        try:
            self._archive_node(result)
        except (OSError, ValueError, GitAuthoringWorkspaceError) as exc:
            result["archive_error"] = str(exc)[:1024]
            errors.append(exc)
        if errors:
            raise SessionFinishError(str(errors[0]), result) from errors[0]
        return result

    def _archive_node(self, result: dict[str, Any]) -> None:
        if self.expert_resources is not None:
            result["expert_resources"] = self.expert_resources
        if self.leader_resources is not None:
            result["leader_resources"] = self.leader_resources
        transcript = self.session_root / f"{self.binding['session_id']}.jsonl"
        if not transcript.exists():
            result["transcript_missing"] = True
            return
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


def read_session_archive(publisher: ArtifactPublisher, request: AgentRequest, session: Mapping[str, Any]) -> bytes:
    publication = session.get("transcript")
    if not isinstance(publication, Mapping): return b""
    key = AgentArtifactKey(run_id=request.run_id, attempt_id=request.attempt_id)
    if (publication.get("key") != key.to_dict() or publication.get("artifact_id") != key.artifact_id
            or publication.get("path") != "/".join(key.as_tuple())):
        raise AgentACIError("invalid_candidate", "Agent archive owner mismatch")
    path = publisher.path_for(key)
    publisher.verify(ArtifactPublication(key=key, schema_version=publication["schema_version"],
        digest_algorithm=publication["digest_algorithm"], digest=publication["digest"], path=str(path),
        relative_paths=tuple(publication["relative_paths"]), reused=True))
    transcript = path / "transcript.jsonl"
    if transcript.stat().st_size > node_memory_limit(request, "max_transcript_bytes", 64 * 1024 * 1024):
        raise AgentACIError("invalid_candidate", "Agent transcript exceeds its frozen memory budget")
    return transcript.read_bytes()


def archived_web_results(publisher, request, session):
    from .grounding_background import transcript_web_results
    return transcript_web_results(read_session_archive(publisher, request, session), session_id=session.get("session_id"))


def same_frozen_task(previous: AgentRequest, request: AgentRequest) -> bool:
    def frozen(value):
        data = value.to_dict()
        for name in ("attempt_id", "attempt_ordinal", "budget", "deadline"):
            data.pop(name)
        correction = ("--agent-retry-diagnostic", "--candidate-validation-error")
        data["input_artifacts"] = [item for item in data["input_artifacts"] if not item["ref"].endswith(correction)]
        data["immutable_context_refs"] = [ref for ref in data["immutable_context_refs"] if not ref.endswith(correction)]
        return data
    return frozen(previous) == frozen(request)


def session_resume_allowed(transaction, request: AgentRequest) -> bool:
    """A selected/reloaded connection makes later retry history nonportable.

    Reuse the existing operator transition, including reloads without a path.
    We deliberately keep subsequent retries fresh instead of inventing a
    connection fingerprint or ordering model-connection changes by wall time.
    """
    if request.schema_version < 25: return True
    return transaction.execute("SELECT 1 FROM workflow_transitions WHERE run_id=? "
        "AND workflow_kind='RUN' AND json_extract(payload_json,'$.provider_retry_clear') IS NOT NULL LIMIT 1",
        (request.run_id,)).fetchone() is None


def resumable_task_session(request: AgentRequest, prior: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """Derive bounded retry continuity from the preceding durable Attempt only."""
    if request.schema_version < 25 or request.command in STATELESS_SESSION_COMMANDS | {AgentCommand.DEBATE} or prior is None:
        return None
    if (prior.get("state") != "CLOSED" or prior.get("kind") != "AGENT"
            or prior.get("terminal_reason") not in {"FAILED", "TIMED_OUT"}
            or prior.get("attempt_ordinal") != request.attempt_ordinal - 1): return None
    observation = json.loads(prior["observation_json"])
    if observation.get("retry_class") not in {"MODEL", "INFRASTRUCTURE"}: return None
    from .agent_context import RETRYABLE_AGENT_FAILURES, candidate_failure_retryable
    code = observation.get("failure_code")
    if code not in RETRYABLE_AGENT_FAILURES and not candidate_failure_retryable(code): return None
    diagnostic = observation.get("diagnostics") or {}
    failure = diagnostic.get("provider_failure") or {}
    # Capacity/compaction, empty-output exhaustion, credentials, and quota waits
    # are not evidence that the old conversation is safe to continue.
    if failure.get("code") not in {None, "provider_error", "transport_error", "http_error"}: return None
    previous = AgentRequest.from_dict(observation["request"])
    if (prior.get("attempt_id"), prior.get("run_id"), prior.get("workflow_id"), prior.get("command_id")) != (
            previous.attempt_id, request.run_id, request.owner_workflow_id, request.command_id): return None
    if not same_frozen_task(previous, request): return None
    session, opened = observation.get("session") or {}, observation.get("session_binding") or {}
    if (session.get("transcript_complete") is not True or not isinstance(session.get("transcript"), Mapping)
            or not opened or any(session.get(key) != value for key, value in opened.items())): return None
    context = diagnostic.get("context") or {}
    tokens, limit = context.get("last_observed_input_tokens"), context.get("available_input_tokens")
    exchange = context.get("current_request_exchange")
    if (context.get("current_request_fits") is not True or exchange is None
            or type(tokens) is not int or tokens < 0 or type(limit) is not int): return None
    if exchange != context.get("last_observed_exchange"):
        # A transport may fail after a measured-prefix request was admitted.
        # Keep that conservative projection only when its complete usage anchor
        # is the last observed exchange; a raw byte guess is insufficient.
        if (context.get("current_request_estimate_source") != "provider_usage_plus_delta_estimate"
                or context.get("anchor_exchange") != context.get("last_observed_exchange")): return None
        tokens = context.get("current_request_estimated_input_tokens")
        if type(tokens) is not int or tokens < 0: return None
    from .context_management import context_policy
    # The retry adds its bounded prompt and correction. This eligibility check
    # leaves headroom; the relay still measures every actual outgoing request.
    policy = context_policy(request)
    output = context.get("output_reserve_tokens", 0)
    if type(output) is not int or output < 0 or tokens + policy["max_inline_input_bytes"] + output > limit: return None
    # Complete usage anchors belong to one relay invocation. A new relay
    # cannot reuse the old token measurement to admit its restored transcript.
    # Resume appends a full prompt, not just newly inlined artifacts. Include
    # its observed size and the existing estimate margin for unmeasured reply
    # and continuation text. This is eligibility headroom, not a token count;
    # the relay still checks the actual request. Unknown sizes reconstruct.
    input_bytes = context.get("request_input_bytes")
    prompt_bytes = diagnostic.get("prompt_bytes")
    if (type(input_bytes) is not int or input_bytes < 0
            or type(prompt_bytes) is not int or prompt_bytes < 0
            or input_bytes + prompt_bytes + policy["estimate_margin_tokens"] > limit): return None
    return session


def retrieved_session_urls(publisher, request, records, attempts, *, real):
    from .grounding_background import retrieved_web_urls
    for row in attempts:
        if row["attempt_id"] == request.attempt_id: continue
        observation = json.loads(row["observation_json"])
        previous = observation.get("request", {})
        if (previous.get("command") != request.command.value or previous.get("run_id") != request.run_id
                or previous.get("role") != request.role or row["state"] != "CLOSED"): continue
        session = observation.get("session", {})
        if isinstance(session.get("transcript"), Mapping):
            records.extend(archived_web_results(publisher, AgentRequest.from_dict(previous), session))
        elif not real:
            records.extend(observation.get("web_tool_results", ()))
    return retrieved_web_urls(records)

def observed_session_binding(observation: Mapping[str, Any]) -> tuple[str, str | None]:
    binding, session = observation.get("session_binding", {}), observation.get("session", {})
    values = (binding.get("session_id"), binding.get("parent_session_id"))
    if values != (session.get("session_id"), session.get("parent_session_id")):
        raise ValueError("observed session differs from the admitted session binding")
    for index, value in enumerate(values):
        if index == 1 and value is None:
            continue
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError("interaction source lacks its actual session identity")
    return values


class AgentSessions:
    # Process-local memory: cleanup re-runs only when the accepted batch
    # history moved past the last fully reconciled revision; a retained dirty
    # worktree is a terminal state memorized per revision (a changed history or
    # a Host restart replays once); it warns at most once per Host process.
    _retirement_reconciled: ClassVar[dict[tuple[str, str, str, str], tuple[int, int]]] = {}
    _retirement_retained: ClassVar[dict[tuple[tuple[str, str, str, str], str], tuple[tuple[int, int], str]]] = {}
    _retirement_warned: ClassVar[set[tuple[str, str]]] = set()

    def __init__(self, state_root: Path, publisher: ArtifactPublisher) -> None:
        self.state_root = Path(state_root).resolve(strict=True)
        self.publisher = publisher

    def retire_adjudicated_worktrees(self, history: BatchHistory,
                                    attempts: Sequence[Mapping[str, Any]], *,
                                    creations: Sequence[Mapping[str, Any]] = ()) -> dict[str, str]:
        """Reconcile accepted ADJ cleanup without changing any scientific row."""
        run, role, component = (_component(history.run_id), _component(history.role), _component(history.component_id))
        latest = history.latest
        revision = (0 if latest is None else latest.cycle,
                    sum(1 for batch in history.batches if batch.selection is not None))
        owner = (str(self.state_root), run, role, component)
        if type(self)._retirement_reconciled.get(owner) == revision:
            return {}
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
        pending = False
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
                source = self.state_root / run / "sessions" / role / f"c{batch.cycle}" / component / scope / "source"
                hot = source / "git-authoring-worktrees" / "chain" / "worktree"
                if not hot.exists() and not hot.is_symlink():
                    continue
                if (batch.cycle, chain) in busy or batch.members[chain] in open_hypotheses:
                    pending = True
                    continue
                identity = f"{run}/{role}/{component}/c{batch.cycle}/{scope}"
                memorized = type(self)._retirement_retained.get((owner, identity))
                if memorized is not None and memorized[0] == revision:
                    errors[identity] = memorized[1]
                    continue
                try:
                    if source.resolve() != source:
                        raise GitAuthoringIntegrityError("chain cleanup source is not its canonical private path")
                    GitAuthoringWorkspace(source).retire_worktree("chain", branch=f"prom/{role}/r{batch.cycle}/{scope}")
                except (GitAuthoringWorkspaceError, OSError, ValueError) as exc:
                    errors[identity] = str(exc)
                    if isinstance(exc, GitAuthoringDirtyWorktreeError):
                        type(self)._retirement_retained[(owner, identity)] = (revision, errors[identity])
                    marker = (str(self.state_root), identity)
                    if marker not in type(self)._retirement_warned:
                        type(self)._retirement_warned.add(marker)
                        logging.getLogger(__name__).warning("chain worktree cleanup retained %s: %s", identity, exc)
        if not errors and not pending:
            type(self)._retirement_reconciled[owner] = revision
        return errors

    @staticmethod
    def binding(request: AgentRequest, prior: Mapping[str, Any] | None = None, *, allow_resume: bool = True) -> dict[str, Any]:
        task = request.task_context
        resumed = resumable_task_session(request, prior) if allow_resume else None
        if resumed is not None:
            return {key: resumed[key] for key in ("version", "session_id", "parent_session_id", "scope", "chain", "cycle", "branch")}
        transfer = _session_transfer(request)
        if transfer is not None and transfer[0]:
            context = transfer[1]
            scope = "debate-" + _component(context["debate_id"])
            from .expert_interaction import debate_session_id
            return {"version": SESSION_VERSION,
                "session_id": debate_session_id(context["session_id"], request.attempt_id, request.schema_version),
                "parent_session_id": None if request.schema_version >= 29 else context["original_session_id"], "scope": scope,
                "chain": context["participant"]["scope"]["chain"], "cycle": task.cycle,
                "branch": f"prom/{request.role}/r{task.cycle}/{scope}"}
        if request.command is AgentCommand.GRAPH_EVOLVE:
            from .expert_loop import AuxiliaryTask, AuxiliaryRequestKind
            if (request.schema_version < 14 or not isinstance(task, AuxiliaryTask)
                    or task.kind is not AuxiliaryRequestKind.GRAPH_EVOLVE
                    or request.role != "grounding" or request.component_id != "grounding"):
                raise ValueError("manager session requires its run-scoped Grounding owner")
            return {"version": SESSION_VERSION,
                "session_id": str(uuid.uuid5(_NAMESPACE, request.run_id + "/grounding/manager"
                    + ("/" + request.attempt_id if request.schema_version >= 24 else ""))),
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
        # Legacy chains keep their conversation; current Attempts keep only Git scope.
        stateless = request.command in STATELESS_SESSION_COMMANDS
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
        if request.schema_version >= 24:
            # Source/Git scope stays stable; mutable model history is disposable.
            # Every invocation reconstructs from its admitted task, evidence and
            # existing memory. This also bounds ordinary corrective retries.
            session_id = str(uuid.uuid5(_NAMESPACE, common + "/" + scope + "/" + request.attempt_id))
            parent = None
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
        self, request: AgentRequest, binding: GitTargetBinding | None, *,
        prior: Mapping[str, Any] | None = None, recovering: bool = False,
        admitted_binding: Mapping[str, Any] | None = None,
    ) -> PreparedSessionWorkspace:
        if request.schema_version < 11:
            raise ValueError("persistent sessions require ACI 11")
        descriptor = self.binding(request, prior)
        if admitted_binding is not None:
            # OPEN intent fixes the launch even if a later operator control
            # reloads the connection. Only the normal fresh binding or verified
            # immediate-predecessor retry binding can have been admitted.
            if dict(admitted_binding) not in (descriptor, self.binding(request)):
                raise GitAuthoringIntegrityError("admitted session differs from its task and preceding archive")
            descriptor = dict(admitted_binding)
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
        resumed = resumable_task_session(request, prior)
        if resumed is not None and resumed["session_id"] != descriptor["session_id"]:
            resumed = None
        if resumed is not None:
            original = AgentRequest.from_dict(json.loads(prior["observation_json"])["request"])
            archived = read_session_archive(self.publisher, original, resumed)
            if not archived or not _validate_transcript(archived, descriptor):
                raise GitAuthoringIntegrityError("task retry requires its complete published transcript")
            if own_transcript.exists():
                if not recovering and _regular(own_transcript, transcript_limit) != archived:
                    raise GitAuthoringIntegrityError("task transcript differs from its preceding published archive")
            else:
                _write_new(own_transcript, archived)
        source_content = None
        retained_source = None
        if transfer is not None and (transfer[0] or request.command not in _INNER or request.schema_version >= 24):
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
            if request.schema_version < 24 and not transfer[0] and not own_transcript.exists():
                _write_new(own_transcript, source_content)
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
            from .expert_loop import AuxiliaryTask, AuxiliaryRequestKind
            initial = (request.command is AgentCommand.GROUNDING
                and isinstance(request.task_context, AuxiliaryTask)
                and request.task_context.kind is AuxiliaryRequestKind.GROUNDING
                and request.owner_workflow_id == request.run_id)
            management = (request.command in {AgentCommand.GRAPH_EVOLVE, AgentCommand.OBSERVER, AgentCommand.FINAL_REPORT, AgentCommand.DEBATE_JUDGE}
                and request.role == "grounding" and request.component_id == "grounding")
            if (len(grounding_inputs) != 1 or not (initial or management)
                    or request.schema_version < 14
                    or not isinstance(request.task_context, AuxiliaryTask)
                    or request.task_context.expert_slot is not None or request.task_context.component_id is not None):
                raise GitAuthoringIntegrityError("Grounding resources require initial Grounding or their management or Observer owner")
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
