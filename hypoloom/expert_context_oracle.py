"""Independent raw-record/Git observations for the existing outcome Oracle.

No Host, plugin store, memory writer, projection builder or report validator is
imported. Git and accepted scientific facts are the only persistent sources.
"""

from __future__ import annotations

import base64
import json
import re
import stat
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from .batch_oracle import _time

ContextIssue = tuple[str, str, tuple[str, ...]]


def _component(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}", value):
        raise ValueError("invalid expert context path identity")
    return value


def _git(repository: Path, *arguments: str) -> bytes:
    if repository.resolve() != repository or not (repository / ".git").is_dir():
        raise ValueError("expert context Git repository is unavailable or indirect")
    completed = subprocess.run(("git", "--no-optional-locks", "-C", str(repository), *arguments),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=False)
    if completed.returncode:
        raise ValueError("expert context Git object is unavailable")
    return completed.stdout


def _commit_files(repository: Path, commit: Any, *, allow_executable: bool = False) -> dict[str, bytes]:
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise ValueError("expert context requires a full Git commit")
    if _git(repository, "rev-parse", "--verify", commit + "^{commit}").decode().strip() != commit:
        raise ValueError("expert context commit differs from its Git object")
    files = {}
    for record in _git(repository, "ls-tree", "-rz", commit).split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        path = raw_path.decode()
        modes = (b"100644 blob ", b"100755 blob ") if allow_executable else (b"100644 blob ",)
        if not metadata.startswith(modes):
            raise ValueError("expert context Git tree contains a non-regular file")
        files[path] = _git(repository, "show", f"{commit}:{path}")
    return files


def _input(request: Mapping[str, Any], suffix: str) -> Mapping[str, Any]:
    values = [item for item in request.get("input_artifacts", ()) if item["ref"].endswith(suffix)]
    if len(values) != 1:
        raise ValueError("expert resource request binding is missing or ambiguous")
    value = json.loads(base64.b64decode(values[0]["content_base64"], validate=True))
    if not isinstance(value, dict):
        raise ValueError("expert resource request binding is not an object")
    return value


def _view(directory: Path, role: str) -> None:
    if directory.resolve() != directory or not directory.is_dir():
        raise ValueError("bound research view is unavailable or indirect")
    files = set()
    for path in directory.rglob("*"):
        relative = path.relative_to(directory).as_posix()
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("bound research view contains a non-regular file")
        if not (relative in {"background.md", "graph/edges.jsonl"}
                or relative.startswith("graph/nodes/") or relative.startswith(f"experts/{role}/")):
            raise ValueError("bound research view contains whole-run or peer files")
        files.add(relative)
    if not {"background.md", "graph/edges.jsonl", f"experts/{role}/state.json"} <= files:
        raise ValueError("bound research view is incomplete")
    if json.loads((directory / f"experts/{role}/state.json").read_bytes()).get("role") != role:
        raise ValueError("bound research view role differs")


def _report_assessments(
    content: bytes, request: Mapping[str, Any], facts: Sequence[Mapping[str, Any]],
    payloads: Mapping[str, Mapping[str, Any]],
    *, batch_transitions: Sequence[Mapping[str, Any]] = (),
) -> None:
    key = request["task_context"]["key"]
    proposals = {str(row["hypothesis_id"]): payloads[str(row["fact_id"])]
                 for row in facts if row["fact_type"] == "HypothesisProposed"}

    def depth(hypothesis: str, seen: frozenset[str] = frozenset()) -> int:
        if hypothesis in seen or hypothesis not in proposals:
            raise ValueError("report hypothesis has an invalid accepted parent chain")
        parent = proposals[hypothesis].get("parent_hypothesis_id")
        return 1 if parent is None else 1 + depth(parent, seen | {hypothesis})

    batched = type(request.get("schema_version")) is int and request["schema_version"] >= 14
    if batched:
        accepted = []
        for raw in batch_transitions:
            row = dict(raw)
            if (row.get("run_id"), row.get("workflow_kind"), row.get("workflow_id"), row.get("command_type")) != (
                    request["run_id"], "RUN", request["run_id"], "ACCEPT_HYPOTHESIS"):
                continue
            payload = row.get("payload")
            if payload is None:
                payload = json.loads(row["payload_json"])
            batch = payload.get("hypothesis_batch")
            if (isinstance(batch, Mapping) and batch.get("cycle") == key["cycle"]
                    and payload.get("role") == request["role"] and payload.get("component_id") == request["component_id"]):
                accepted.append((payload, batch))
        if len(accepted) != 1:
            raise ValueError("report requires exactly one accepted batch for its actual role/component/cycle")
        payload, batch = accepted[0]
        context = _input(request, "--batch-context")
        members = batch.get("chains")
        if (not isinstance(members, Mapping) or not members or not all(isinstance(item, str) for item in members.values())
                or len(set(members.values())) != len(members) or context.get("cycle") != key["cycle"]
                or context.get("chains") != members or context.get("batch_command_id") != payload.get("node_command_id")):
            raise ValueError("report input differs from its accepted batch membership")
        owned = set(members.values())
        if any(hypothesis not in proposals or (proposals[hypothesis].get("run_id"), proposals[hypothesis].get("proposer_role"),
                proposals[hypothesis].get("component_id")) != (request["run_id"], request["role"], request["component_id"]) for hypothesis in owned):
            raise ValueError("report batch contains missing or foreign accepted hypotheses")
    else:
        owned = {hypothesis for hypothesis, payload in proposals.items()
                 if payload.get("proposer_role") == request["role"]
                 and payload.get("component_id") == request["component_id"]
                 and depth(hypothesis) == key["cycle"]}
    expected = [{"fact_id": row["fact_id"], "hypothesis_id": row["hypothesis_id"],
                 "assessment": payloads[str(row["fact_id"])]["assessment"]}
                for row in facts if row["fact_type"] == "HypothesisAssessed"
                and row["hypothesis_id"] in owned]
    text = content.decode("utf-8")
    header, body = text.split("\n---\n", 1)
    if not text.startswith("---\n") or not body.strip():
        raise ValueError("accepted report lacks Markdown assessment frontmatter")
    document = yaml.safe_load(header[4:])
    # ACI14 can close an entirely operationally failed batch with zero scientific
    # assessments. batch_oracle independently checks those terminal failures.
    if ((not batched and (not expected or len(owned) != 1)) or not isinstance(document, dict)
            or set(document) != {"assessments"} or not isinstance(document["assessments"], list)
            or sorted(document["assessments"], key=lambda item: item["fact_id"])
            != sorted(expected, key=lambda item: item["fact_id"])):
        raise ValueError("report references do not equal this cycle's accepted own-hypothesis assessments")


def _audit_plugin_selections(root, attempts, observations, facts, payloads, transitions, commands):
    effects = [(dict(row), json.loads(row["payload_json"]) if "payload_json" in row.keys() else row["payload"])
        for row in transitions if dict(row).get("command_type") == "SELECT_PLUGIN"]
    if not effects:
        return []
    issues, accepted = [], []
    sources = {row["command_id"]: dict(row) for row in commands}
    goals = {value["run_id"]: value["research_contract"] for row in facts if row["fact_type"] == "GoalDefined"
        for value in [payloads[row["fact_id"]]]}

    def updates(run, role, kind, before, control=None, control_time=None):
        result = []
        for row in attempts:
            value = observations.get(row["attempt_id"], {})
            request = value.get("request", {})
            if (row["kind"] != "AGENT" or row["state"] != "CLOSED" or row["terminal_reason"] != "SUCCEEDED"
                    or request.get("run_id") != run or request.get("role") != role
                    or request.get("command") != ("SELF_EVOLVE" if kind == "expert" else "GRAPH_EVOLVE")
                    or "plugin_update" not in value or _time(row["ended_at"]) > before):
                continue
            if control is not None:
                if kind == "expert":
                    threshold = control["effective_cycles"].get(request.get("component_id"))
                    if threshold is None or request["task_context"]["key"]["cycle"] < threshold:
                        continue
                elif _time(row["started_at"]) < control_time:
                    continue
            result.append((_time(row["ended_at"]), row["attempt_id"], value["plugin_update"]["commit"]))
        return max(result)[2] if result else None

    for effect, value in effects:
        identity = str(effect["command_id"])
        try:
            control = value["plugin_selection"]
            if set(control) != {"action", "owner_kind", "role", "requested_commit", "commit", "effective_cycles"}:
                raise ValueError("plugin control fields differ")
            run, kind, role = effect["run_id"], control["owner_kind"], control["role"]
            action = control["action"]
            source = sources[identity]
            public = json.loads(source["request_json"])
            if (effect["workflow_id"] != run or effect["workflow_kind"] != "RUN" or effect["state"] not in {"CREATED", "ACTIVE"}
                    or sum(row["command_id"] == identity for row, _ in effects) != 1
                    or source["run_id"] != run or source["workflow_id"] != run or source["command_type"] != "SELECT_PLUGIN"
                    or source.get("response_status", source.get("status")) != "accepted"
                    or public["request_id"] != identity or public["command"] != action.upper() + "_PLUGIN"
                    or public["payload"] != {"run_id": run, "role": role, "owner_kind": kind, "commit": control["requested_commit"]}
                    or action not in {"pin", "revert", "unpin"}):
                raise ValueError("plugin control differs from its actual accepted public command")
            contract = goals[run]
            if kind == "grounding" and role == "grounding":
                expected_cycles = {}
                repository = root / _component(run) / "grounding/plugin"
                skills = ("leader-trigger", "debate-trigger", "debate-judge", "graph-evolve", "final-report", "self-evolve")
            elif kind == "expert" and role in {item["id"] for item in contract["roles"]}:
                expected_cycles = {item["id"]: 1 for item in contract["components"] if item["owner"] == role}
                for row in attempts:
                    request = observations.get(row["attempt_id"], {}).get("request", {})
                    task = request.get("task_context") or {}
                    component = request.get("component_id")
                    if (row["kind"] == "AGENT" and _time(row["started_at"]) <= _time(effect["created_at"])
                            and request.get("run_id") == run and request.get("role") == role
                            and task.get("kind") == "RESEARCH_NODE" and component in expected_cycles):
                        expected_cycles[component] = max(expected_cycles[component], task["key"]["cycle"] + 1)
                repository = root / _component(run) / "experts" / _component(role) / "plugin"
                skills = ("experiment-report", "literature-summary", "experiment-repair", "self-evolve")
            else:
                raise ValueError("plugin control has no admitted owner namespace")
            if (not isinstance(control["effective_cycles"], dict)
                    or any(type(cycle) is not int or cycle < 1 for cycle in control["effective_cycles"].values())
                    or control["effective_cycles"] != expected_cycles):
                raise ValueError("plugin control changed an already frozen cycle or omitted its owner component")
            committed = _commit_files(repository, control["commit"], allow_executable=True)
            name = "hypoloom-" + re.sub(r"[^a-z0-9-]", "-", role.lower())
            if json.loads(committed[".claude-plugin/plugin.json"])["name"] != name or not all(committed[f"skills/{skill}/SKILL.md"].strip() for skill in skills):
                raise ValueError("plugin control Git tree has another native owner or missing skill")
            requested = control["requested_commit"]
            if action == "revert":
                target = _commit_files(repository, requested, allow_executable=True)
                if _git(repository, "rev-parse", control["commit"] + "^{tree}") != _git(repository, "rev-parse", requested + "^{tree}") or committed != target:
                    raise ValueError("reverted plugin does not contain the requested Git tree")
                parents = _git(repository, "rev-list", "--parents", "-n", "1", control["commit"]).decode().split()
                if len(parents) > 1 and control["commit"] != requested:
                    expected = f"Hypoloom plugin revert\n\nCommand: {identity}\nBase: {parents[1]}\nTarget: {requested}"
                    if len(parents) != 2 or _git(repository, "log", "-1", "--format=%B", control["commit"]).decode().strip() != expected:
                        raise ValueError("plugin revert Git parent or command cause differs")
            elif requested is not None:
                if control["commit"] != requested:
                    raise ValueError("plugin selection substituted another requested commit")
            elif action != "unpin":
                raise ValueError("pin requires its explicit requested commit")
            else:
                latest = updates(run, role, kind, _time(effect["created_at"]))
                if latest is None:
                    roots = _git(repository, "rev-list", "--max-parents=0", "--all").decode().split()
                    if len(roots) != 1:
                        raise ValueError("plugin seed Git source is ambiguous")
                    latest = roots[0]
                if control["commit"] != latest:
                    raise ValueError("unpin did not select its latest successful actual evolution or seed")
            accepted.append((effect, control))
        except (KeyError, TypeError, ValueError, AttributeError, OSError, subprocess.SubprocessError) as exc:
            issues.append(("plugin_control_invalid", str(exc), (identity,)))

    first = {}
    for row in sorted(attempts, key=lambda item: (_time(item["started_at"]), item["attempt_id"])):
        request = observations.get(row["attempt_id"], {}).get("request", {})
        if row["kind"] != "AGENT" or request.get("schema_version", 0) < 14:
            continue
        try:
            task = request.get("task_context") or {}
            kind = "expert" if task.get("kind") == "RESEARCH_NODE" else "grounding"
            suffix = "--expert-resources" if kind == "expert" else "--grounding-resources"
            if not any(item["ref"].endswith(suffix) for item in request.get("input_artifacts", ())):
                continue  # Debate reuses original expert resources, not a fresh Grounding selection.
            cycle = task["key"]["cycle"] if kind == "expert" else None
            run, role = request["run_id"], request["role"]
            key = (run, role, kind, request["component_id"], cycle) if kind == "expert" else (run, kind, request["command_id"])
            started = first.setdefault(key, _time(row["started_at"]))
            applicable = [(effect, control) for effect, control in accepted if effect["run_id"] == run
                and (control["owner_kind"], control["role"]) == (kind, role) and _time(effect["created_at"]) <= started
                and (kind == "grounding" or cycle >= control["effective_cycles"].get(request["component_id"], cycle + 1))]
            if not applicable:
                continue
            effect, control = max(applicable, key=lambda item: item[0]["revision"])
            expected = control["commit"]
            if control["action"] == "unpin":
                expected = updates(run, role, kind, started, control, _time(effect["created_at"])) or expected
            if _input(request, suffix)["plugin"]["commit"] != expected:
                raise ValueError("frozen plugin differs from its effective control and accepted evolution source")
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            issues.append(("plugin_selection_invalid", str(exc), (str(row["attempt_id"]),)))
    return issues


def audit_expert_context(
    state_root: Path, attempts: Sequence[Mapping[str, Any]],
    payloads: Mapping[str, Mapping[str, Any]], facts: Sequence[Mapping[str, Any]],
    fact_payloads: Mapping[str, Mapping[str, Any]],
    *, batch_transitions: Sequence[Mapping[str, Any]] = (),
    commands: Sequence[Mapping[str, Any]] = (),
) -> list[ContextIssue]:
    """Audit ACI12+ research-node bindings and accepted report publications."""
    issues: list[ContextIssue] = _audit_plugin_selections(state_root, attempts, payloads, facts,
        fact_payloads, batch_transitions, commands)
    cycle_plugins: dict[tuple[str, str, int], Mapping[str, Any]] = {}
    prior: dict[tuple[str, str], tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]] = {}
    git_files: dict[tuple[Path, str, bool], dict[str, bytes]] = {}

    def files(repository: Path, commit: str, *, allow_executable: bool = False) -> dict[str, bytes]:
        identity = (repository, commit, allow_executable)
        if identity not in git_files:
            git_files[identity] = _commit_files(repository, commit, allow_executable=allow_executable)
        return git_files[identity]

    selected = sorted(attempts, key=lambda row: (row["started_at"], row["attempt_ordinal"]))
    for row in selected:
        attempt_id = str(row["attempt_id"])
        observation = payloads.get(attempt_id, {})
        request = observation.get("request", {})
        version = request.get("schema_version")
        task = request.get("task_context") or {}
        if (row["kind"] != "AGENT" or type(version) is not int or version < 12
                or task.get("kind") != "RESEARCH_NODE"):
            continue
        stage = "expert_resource_invalid"
        try:
            run_id, role = _component(request["run_id"]), _component(request["role"])
            key = task["key"]
            if key["expert_slot"] != role or key["run_id"] != run_id:
                raise ValueError("expert resource ownership differs from its node")
            resources = _input(request, "--expert-resources")
            resource_fields = {"plugin", "memory_commit", "research_view_id"}
            report_component = resources.get("report_component_id")
            if report_component is not None:
                if version < 14 or report_component != key["component_id"] or report_component != request["component_id"]:
                    raise ValueError("report namespace differs from its owning component")
                _component(report_component); resource_fields.add("report_component_id")
            if set(resources) != resource_fields:
                raise ValueError("expert resource descriptor fields differ")
            plugin = resources["plugin"]
            if (set(plugin) != {"run_id", "role", "name", "commit"}
                    or plugin["run_id"] != run_id or plugin["role"] != role):
                raise ValueError("native plugin belongs to a different expert")
            owner = state_root / run_id / "experts" / role
            native = files(owner / "plugin", plugin["commit"], allow_executable=True)
            manifest = json.loads(native[".claude-plugin/plugin.json"])
            if manifest.get("name") != plugin["name"] or not isinstance(plugin["name"], str):
                raise ValueError("native plugin manifest differs from its request binding")
            for skill in ("experiment-report", "literature-summary", "experiment-repair", "self-evolve"):
                if not native[f"skills/{skill}/SKILL.md"].strip():
                    raise ValueError("native plugin is missing a wired skill")
            memory = files(owner / "memory", resources["memory_commit"])
            if "MEMORY.md" not in memory or any(name != "MEMORY.md" and not re.fullmatch(r"rounds/(?:[A-Za-z0-9][A-Za-z0-9_.-]*/)?[1-9][0-9]*\.md", name) for name in memory):
                raise ValueError("selected memory commit has an invalid tree")
            _view(owner / "views" / _component(resources["research_view_id"]), role)
            cycle = (run_id, role, key["component_id"], key["cycle"])
            if cycle in cycle_plugins and cycle_plugins[cycle] != plugin:
                raise ValueError("expert plugin binding changed within the same cycle")
            cycle_plugins[cycle] = plugin
            logical = (run_id, request["command_id"])
            previous = prior.get(logical)
            if previous is not None:
                previous_row, previous_observation, previous_resources = previous
                if (previous_row["terminal_reason"] == "LOST"
                        or previous_observation.get("retry_class") == "INFRASTRUCTURE"):
                    if previous_resources != resources:
                        raise ValueError("infrastructure retry changed its expert resource binding")
            prior[logical] = (row, observation, resources)
            session = observation.get("session")
            if session is not None and session.get("expert_resources") != resources:
                raise ValueError("session expert resources differ from the admitted request")
            if request["command"] == "SELF_EVOLVE" or "plugin_update" in observation:
                stage = "expert_plugin_update_invalid"
                accepted = row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED"
                if request["command"] != "SELF_EVOLVE" or version < 14 or not accepted:
                    if "plugin_update" in observation:
                        raise ValueError("plugin update has no successful owning SELF_EVOLVE node")
                    continue
                metadata = observation["plugin_update"]
                if (set(metadata) != {"base_commit", "commit"}
                        or metadata["base_commit"] != plugin["commit"]):
                    raise ValueError("plugin update changed its request's selected base")
                committed = files(owner / "plugin", metadata["commit"], allow_executable=True)
                if json.loads(committed[".claude-plugin/plugin.json"]).get("name") != plugin["name"]:
                    raise ValueError("accepted plugin update changed its owner identity")
                if metadata["commit"] != metadata["base_commit"]:
                    parents = _git(owner / "plugin", "rev-list", "--parents", "-n", "1", metadata["commit"]).decode().split()
                    expected = ("Hypoloom plugin update\n\nCommand: " + request["command_id"]
                        + "\nBase: " + metadata["base_commit"])
                    message = _git(owner / "plugin", "log", "-1", "--format=%B", metadata["commit"]).decode().strip()
                    if parents != [metadata["commit"], metadata["base_commit"]] or message != expected:
                        raise ValueError("plugin Git parent or command cause differs from its accepted node")
                continue
            if request["command"] != "CYCLE_REPORT" or row["state"] != "CLOSED" or row["terminal_reason"] != "SUCCEEDED":
                continue
            stage = "expert_report_invalid"
            response = observation["response"]
            artifacts = response["proposed_workspace_artifacts"]
            report_files = {item["path"]: base64.b64decode(item["content_base64"], validate=True) for item in artifacts}
            report_path = f"rounds/{key['cycle']}.md" if report_component is None else f"rounds/{report_component}/{key['cycle']}.md"
            if len(report_files) != len(artifacts) or set(report_files) != {"MEMORY.md", report_path}:
                raise ValueError("accepted report response does not contain its exact two files")
            _report_assessments(report_files[report_path], request, facts, fact_payloads,
                batch_transitions=batch_transitions)
            metadata = observation["memory"]
            if metadata.get("base_commit") != resources["memory_commit"]:
                raise ValueError("accepted report changed its selected memory base")
            committed = files(owner / "memory", metadata["commit"])
            parents = _git(owner / "memory", "rev-list", "--parents", "-n", "1", metadata["commit"]).decode().split()
            if parents != [metadata["commit"], metadata["base_commit"]] or committed != {**memory, **report_files} or report_path in memory:
                raise ValueError("accepted report Git lineage or file bytes differ from its response")
            # Real CC reports share the existing Agent transcript publication.
            # Fake Agents have explicit adapter identity and no CC archive.
            if re.fullmatch(r"claude-code-bwrap/[0-9]+", str(request.get("required_adapter_version"))):
                archive = state_root / "artifacts" / run_id / "agent" / _component(attempt_id) / "transcript"
                for name, content in report_files.items():
                    path = archive / "memory" / name
                    if path.resolve() != path or not stat.S_ISREG(path.lstat().st_mode) or path.read_bytes() != content:
                        raise ValueError("accepted report archive bytes differ from Git and response")
        except (KeyError, ValueError, TypeError, AttributeError, OSError,
                subprocess.SubprocessError, yaml.YAMLError) as exc:
            issues.append((stage, str(exc), (attempt_id,)))
    return issues


__all__ = ["audit_expert_context"]
