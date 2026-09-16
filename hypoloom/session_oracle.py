"""Read-only session/archive checks, called by the single outcome Oracle.

No session producer, Host reducer, Git writer or publisher is imported here.
The existing Oracle's independent artifact digest is passed as a reader.
"""

from __future__ import annotations

import base64
import json
import re
import stat
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

from .agent_aci import supports_experiment_submission

SessionIssue = tuple[str, str, tuple[str, ...]]
_INNER = {"EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION", "RESULT_ANALYSIS"}
_BINDING_KEYS = {"version", "session_id", "parent_session_id", "scope", "chain", "cycle", "branch"}


def _transcript_limit(request: Mapping[str, Any]) -> int:
    """Read the frozen request directly, independently of the session producer."""
    inputs = request.get("input_artifacts", [])
    if not isinstance(inputs, list) or any(not isinstance(item, dict) for item in inputs):
        raise ValueError("invalid session inputs")
    configs = [item for item in inputs if str(item.get("ref", "")).endswith(
        ("--expert-config", "--grounding-config"))]
    if not configs:
        return 64 * 1024 * 1024
    if len(configs) != 1:
        raise ValueError("ambiguous session configuration")
    config = json.loads(base64.b64decode(configs[0].get("content_base64", ""), validate=True))
    if not isinstance(config, dict):
        raise ValueError("invalid session configuration")
    runtime = config.get("node_runtime", {})
    if not isinstance(runtime, dict) or not isinstance(runtime.get("memory", {}), dict):
        raise ValueError("invalid session memory configuration")
    value = runtime.get("memory", {}).get("max_transcript_bytes", 64 * 1024 * 1024)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("invalid transcript byte limit")
    return value


def _transfer(request, rows, payloads):
    debate = request.get("command") == "DEBATE" and request.get("schema_version", 0) >= 14
    suffix = "--debate-context" if debate else "--session-promotion"
    inputs = [item for item in request.get("input_artifacts", ()) if item["ref"].endswith(suffix)]
    if not inputs and not debate: return None
    assert len(inputs) == 1 and request["schema_version"] >= 14
    context = json.loads(base64.b64decode(inputs[0]["content_base64"], validate=True))
    source_id = context["original_attempt_id"] if debate else context["source_attempt_id"]
    originals = [item for item in request["input_artifacts"] if item["ref"] == source_id]
    assert len(originals) == 1 and source_id in rows
    copied = json.loads(base64.b64decode(originals[0]["content_base64"], validate=True))
    source = rows[source_id]; observed = payloads[source_id]; original = observed["request"]
    assert source["kind"] == "AGENT" and source["state"] == "CLOSED" and source["terminal_reason"] == "SUCCEEDED"
    assert copied["attempt_id"] == source_id and copied["run_id"] == request["run_id"]
    assert copied["kind"] == source["kind"] and copied["state"] == source["state"] and copied["terminal_reason"] == source["terminal_reason"]
    assert json.loads(copied["observation_json"]) == observed
    assert (original["run_id"], original["role"], original["component_id"]) == (request["run_id"], request["role"], request["component_id"])
    session = observed["session_binding"]
    if debate:
        scope = context["participant"]["scope"]
        assert (scope["role"], scope["component_id"], scope["chain"]) == (original["role"], original["component_id"], original["task_context"]["key"]["chain"])
        assert context["original_session_id"] == session["session_id"]
        assert type(context["round"]) is int and context["round"] in {1, 2, 3}
    else:
        key = request["task_context"]["key"]
        assert request["command"] in _INNER or request["command"] == "HYPOTHESIS_PROPOSAL" and key["subkind"] == "SYNTHESIS"
        assert (context["role"], context["component_id"], context["cycle"]) == (request["role"], request["component_id"], key["cycle"])
        assert original["command"] == "DEBATE"
        assert (context["session_id"], context["original_session_id"]) == (session["session_id"], session["parent_session_id"])
        sources = [item for item in original["input_artifacts"] if item["ref"].endswith("--debate-context")]
        assert len(sources) == 1
        prior = json.loads(base64.b64decode(sources[0]["content_base64"], validate=True))
        assert prior["debate_id"] == context["debate_id"] and prior["round"] == 3
    return debate, context, source, original, session


def audit_sessions(artifact_root: Path, attempts: Sequence[Mapping[str, Any]],
                   payloads: Mapping[str, Mapping[str, Any]],
                   digest: Callable[[Path], str]) -> tuple[list[SessionIssue], int]:
    issues: list[SessionIssue] = []
    archives = 0
    sessions: dict[str, tuple[str, str, str, int, str]] = {}
    groups: dict[tuple[str, str, str, int, str], str] = {}
    seeds: dict[tuple[str, str, str, int], tuple[str, float]] = {}
    selected: list[tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]] = []
    rows = {str(row["attempt_id"]): row for row in attempts}
    transfers = {}

    def issue(code: str, message: str, identity: str) -> None:
        issues.append((code, message, (identity,)))

    for row in attempts:
        identity = str(row["attempt_id"])
        observation = payloads.get(identity, {})
        request = observation.get("request")
        if (row["kind"] != "AGENT" or not isinstance(request, Mapping)
                or not supports_experiment_submission(request.get("schema_version"))):
            continue
        # Deterministic fake Agents have no CC transcript. This is an explicit
        # adapter identity distinction, not permission to omit real CC sessions.
        if not re.fullmatch(r"claude-code-bwrap/[0-9]+", str(request.get("required_adapter_version"))):
            continue
        binding = observation.get("session_binding")
        if not isinstance(binding, Mapping) or set(binding) != _BINDING_KEYS:
            issue("session_binding_invalid", "real CC Attempt lacks its exact OPEN session binding", identity)
            continue
        task = request.get("task_context", {})
        if not isinstance(task, Mapping):
            issue("session_binding_invalid", "session request lacks its typed task context", identity)
            continue
        key = task.get("key", {})
        if not isinstance(key, Mapping):
            issue("session_binding_invalid", "session request has an invalid node key", identity)
            continue
        manager = (request.get("command") == "GRAPH_EVOLVE" and type(request.get("schema_version")) is int
            and request["schema_version"] >= 14)
        cycle = 1 if manager else key.get("cycle", task.get("cycle") or 1)
        inner = request.get("command") in _INNER
        synthesis = request.get("command") == "HYPOTHESIS_PROPOSAL" and key.get("subkind") == "SYNTHESIS"
        try:
            transfer = _transfer(request, rows, payloads)
            if transfer is not None:
                assert float(transfer[2]["ended_at"]) <= float(row["started_at"])
                transfers[identity] = transfer
        except (AssertionError, KeyError, ValueError, TypeError):
            issue("session_transfer_source_invalid", "session transfer differs from its actual accepted same-owner source", identity)
            continue
        debate = transfer is not None and transfer[0]
        promotion = transfer is not None and not transfer[0]
        try:
            session_id = str(uuid.UUID(binding["session_id"]))
            parent_id = binding["parent_session_id"]
            if parent_id is not None:
                assert str(uuid.UUID(parent_id)) == parent_id
            assert binding["version"] == "chain-session/1"
            assert binding["session_id"] == session_id
            assert binding["cycle"] == cycle and isinstance(cycle, int) and not isinstance(cycle, bool) and cycle > 0
            chain = key.get("chain")
            assert chain is None or isinstance(chain, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", chain)
            assert binding["chain"] == (transfer[1]["participant"]["scope"]["chain"] if debate else (chain if chain is not None else "1") if inner else None)
            assert (parent_id is not None) == (inner or debate or promotion and synthesis)
            scope = binding["scope"]
            assert isinstance(scope, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", scope)
            if debate:
                assert scope == "debate-" + transfer[1]["debate_id"]
                assert (session_id, parent_id) == (transfer[1]["session_id"], transfer[1]["original_session_id"])
            elif manager:
                assert request.get("role") == request.get("component_id") == "grounding"
                assert task.get("kind") == "AUXILIARY" and task.get("request_kind") == "GRAPH_EVOLVE"
                assert task.get("expert_slot") is None and task.get("component_id") is None
                assert type(task.get("cycle")) is int and task["cycle"] > 0
                assert scope == "grounding-manager"
            elif inner:
                expected_scope = "chain-1" if chain is None else "chain-v1." + base64.urlsafe_b64encode(chain.encode()).decode().rstrip("=")
                assert scope == expected_scope and parent_id != session_id
            elif synthesis:
                assert scope == "synthesis"
                if promotion: assert (session_id, parent_id) == (transfer[1]["session_id"], transfer[1]["original_session_id"])
            elif request.get("command") == "GROUNDING" and request["schema_version"] >= 15:
                assert scope == "grounding-preparation" and cycle == 1
                assert task.get("kind") == "AUXILIARY" and task.get("request_kind") == "GROUNDING"
                assert task.get("expert_slot") is None and task.get("component_id") is None and task.get("cycle") is None
            assert binding["branch"] == f"prom/{request['role']}/r{cycle}/{scope}"
        except (AssertionError, KeyError, ValueError, TypeError, AttributeError):
            issue("session_binding_invalid", "session identity, parent, scope or branch differs from its node", identity)
            continue
        common = (str(request["run_id"]), str(request["role"]), str(request["component_id"]), cycle)
        group = (*common, str(scope))
        promoted_group = promotion and synthesis and sessions.get(session_id) == (
            str(request["run_id"]), str(request["role"]), str(request["component_id"]), transfer[4]["cycle"], transfer[4]["scope"])
        if session_id in sessions and sessions[session_id] != group and not promoted_group:
            issue("session_cross_owner", "one CC session is bound to different chain owners", identity)
        if group in groups and groups[group] != session_id:
            issue("session_restart_identity", "the same chain changed CC session identity", identity)
        sessions[session_id], groups[group] = group, session_id
        if synthesis and row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED":
            seeds[common] = (session_id, float(row["ended_at"]))
        selected.append((row, observation, request, binding))

    for row, observation, request, binding in selected:
        identity = str(row["attempt_id"])
        parent = binding["parent_session_id"]
        transfer = transfers.get(identity)
        if parent is not None and not (transfer is not None and (transfer[0] or request["command"] not in _INNER)):
            seed = seeds.get((str(request["run_id"]), str(request["role"]),
                str(request["component_id"]), binding["cycle"]))
            if seed is None or seed[0] != parent or seed[1] > float(row["started_at"]):
                issue("session_parent_mismatch", "chain does not descend from a prior accepted same-owner synthesis", identity)
        if row["state"] != "CLOSED":
            continue
        closed = observation.get("session")
        success = row["terminal_reason"] == "SUCCEEDED"
        if not isinstance(closed, Mapping):
            if success or row["process_group_id"] is not None:
                issue("session_archive_missing", "closed launched CC Attempt lacks session closure metadata", identity)
            continue
        if any(closed.get(name) != value for name, value in binding.items()):
            issue("session_close_binding_mismatch", "node closure changed its OPEN session binding", identity)
        arguments = closed.get("launch_arguments")
        valid_arguments = [["--resume", binding["session_id"]]]
        valid_arguments.append(["--resume", parent, "--fork-session", "--session-id", binding["session_id"]]
            if parent is not None else ["--session-id", binding["session_id"]])
        if transfer is not None and not transfer[0] and request["command"] not in _INNER:
            valid_arguments = [["--resume", binding["session_id"]]]
        recovered_unknown = row["terminal_reason"] == "LOST" and closed.get("archive_after_loss") is True and arguments is None
        if arguments not in valid_arguments and not recovered_unknown:
            issue("session_launch_mismatch", "node did not use its bound seed/fork/resume arguments", identity)
        if any(not isinstance(closed.get(name), str) or not re.fullmatch(r"[0-9a-f]{40,64}", closed[name])
               for name in ("base_commit", "commit")):
            issue("session_commit_missing", "closed node lacks its Host Git commit/base identity", identity)
        publication = closed.get("transcript")
        if not isinstance(publication, Mapping):
            if success or not closed.get("transcript_missing"):
                issue("session_archive_missing", "successful CC node has no canonical transcript", identity)
            continue
        relative = f"{request['run_id']}/agent/{identity}/transcript"
        directory = artifact_root / relative
        try:
            assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", str(request["run_id"]))
            assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", identity)
            assert publication.get("path") == relative
            assert publication.get("artifact_id") == relative.replace("/", ":")
            assert publication.get("key") == {"run_id": request["run_id"], "attempt_id": identity, "artifact_kind": "transcript"}
            assert publication.get("digest_algorithm") == "sha256"
            listed = publication.get("relative_paths")
            assert isinstance(listed, list) and len(listed) == len(set(listed))
            expected_paths = {"transcript.jsonl"}
            if closed.get("paper_sources"):
                from .paper_sources import source_paths, MAX_SOURCE_BYTES, MAX_SOURCES
                sources = closed["paper_sources"]
                assert request.get("schema_version", 0) >= 20 and len(sources) <= MAX_SOURCES
                assert len({source["source_ref"] for source in sources}) == len(sources)
                for source in sources:
                    if source.get("archive_attempt_id"):
                        origin = payloads.get(source["archive_attempt_id"])
                        assert isinstance(origin, Mapping)
                        assert all(origin["request"][key] == request[key] for key in ("run_id", "role", "component_id"))
                        original = next((s for s in origin["session"]["paper_sources"] if s["source_ref"] == source["source_ref"]), None)
                        assert original is not None and not original.get("archive_attempt_id") and original["directory"] == source.get("archive_directory")
                        assert set(source_paths(original)) <= set(origin["session"]["transcript"]["relative_paths"])
                    else: expected_paths.update(source_paths(source))
                assert sum((directory / path).stat().st_size for path in expected_paths - {"transcript.jsonl"}) <= MAX_SOURCE_BYTES
            unaccepted_capture = False
            if request.get("command") == "GROUNDING" and request.get("schema_version", 0) >= 16:
                # Capture precedes Host candidate validation. Rejection retains
                # those files and the response as evidence, not accepted science.
                response = observation.get("response", {}) if success else observation.get(
                    "rejected_response", observation.get("response", {}))
                candidate = response.get("typed_candidate") or {}
                acceptance = candidate.get("acceptance")
                if acceptance is not None:
                    assert candidate.get("clarification") is None
                    expected_paths |= {"evaluator/" + path for path in acceptance["parser_files"]}
                elif (not success and observation.get("failure_code") == "invalid_provider_envelope"
                      and observation.get("failure_stage") == "provider_process"
                      and "response" not in observation and "rejected_response" not in observation):
                    # Native validation can capture evaluator bytes before the
                    # provider envelope fails. The existing publication proves
                    # their identity, not an accepted evaluator declaration.
                    unaccepted_capture = True
                    for name in listed:
                        path = PurePosixPath(name)
                        assert str(path) == name and not path.is_absolute() and ".." not in path.parts
                        if len(path.parts) > 1 and path.parts[0] == "evaluator":
                            expected_paths.add(name)
            if request.get("command") == "FINAL_REPORT": expected_paths.add("FINAL_REPORT.md")
            if request.get("command") == "CYCLE_REPORT":
                cycle = request["task_context"]["key"]["cycle"]
                resources = [item for item in request.get("input_artifacts", []) if str(item.get("ref", "")).endswith("--expert-resources")]
                assert len(resources) <= 1
                resource = json.loads(base64.b64decode(resources[0]["content_base64"], validate=True)) if resources else {}
                component = resource.get("report_component_id")
                if component is not None:
                    assert request.get("schema_version", 0) >= 14 and component == request["component_id"]
                    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", component)
                relative = f"rounds/{cycle}.md" if component is None else f"rounds/{component}/{cycle}.md"
                expected_paths |= {"memory/MEMORY.md", "memory/" + relative}
            assert set(listed) == expected_paths if success else "transcript.jsonl" in listed and set(listed) <= expected_paths
            assert directory.resolve(strict=True) == directory
            actual_paths = set()
            evaluator_bytes = 0
            for path in directory.rglob("*"):
                assert not path.is_symlink()
                if path.is_dir():
                    assert any(name.startswith(path.relative_to(directory).as_posix() + "/") for name in listed)
                else:
                    info = path.stat()
                    assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                    actual_paths.add(path.relative_to(directory).as_posix())
                    if path.relative_to(directory).parts[0] == "evaluator":
                        evaluator_bytes += info.st_size
            assert actual_paths == set(listed)
            if unaccepted_capture:
                assert evaluator_bytes <= request["budget"]["max_output_bytes"]
            source = directory / "transcript.jsonl"
            metadata = source.lstat()
            assert stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and metadata.st_size <= _transcript_limit(request)
            assert digest(directory) == publication.get("digest")
            content = source.read_bytes()
            complete = content.endswith(b"\n")
            assert closed.get("transcript_complete") is complete
            assert complete or not success
            records = content.splitlines()
            for raw in records if complete else records[:-1]:
                record = json.loads(raw)
                assert isinstance(record, dict)
                assert record.get("sessionId") in {None, binding["session_id"], parent}
        except (AssertionError, OSError, ValueError, TypeError):
            issue("session_archive_invalid", "transcript identity, tree, bytes or record lineage is invalid", identity)
        else:
            archives += 1
    return issues, archives
