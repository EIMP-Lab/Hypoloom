"""Run-scoped disposable detail projections over ordinary retained evidence."""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess

import pytest

from hypoloom.monitor import MonitorError
from hypoloom.observer_details import read_observer_detail, read_observer_pool, skill_catalog


@pytest.mark.parametrize("damage", [None, "owner", "command", "task", "foreign-run", "expert-slot"])
def test_initial_grounding_catalog_resolves_plugin_owner_without_changing_executor(tmp_path, damage):
    from dataclasses import replace
    from hypoloom.agent_aci import EmbeddedInput
    from hypoloom.expert_plugin import ExpertPluginStore
    from hypoloom.fake_agent import default_request
    from hypoloom.domain import AgentCommand
    store = ExpertPluginStore(tmp_path, owner_kind="grounding")
    base = default_request(AgentCommand.GROUNDING)
    plugin = store.ensure(base.run_id, "grounding", "Current commission")
    item = EmbeddedInput.from_bytes("grounding--grounding-resources", "application/json",
        json.dumps({"plugin": plugin.to_dict()}).encode())
    request = replace(base, schema_version=28, owner_workflow_id=base.run_id,
        required_output_schema="GroundingCandidate/v3", input_artifacts=(item,)).to_dict()
    if damage == "owner": request["owner_workflow_id"] = "different-workflow"
    elif damage == "command": request["command"] = "RESULT_ANALYSIS"
    elif damage == "task": request["task_context"]["request_kind"] = "GROUNDING_QUERY_PLAN"
    elif damage == "foreign-run": request["task_context"]["run_id"] = "another-run"
    elif damage == "expert-slot": request["task_context"]["expert_slot"] = request["role"]
    row = _attempt(base.attempt_id, run_id=base.run_id, role=base.role,
        command="GROUNDING", observation={"request": request})
    if damage:
        with pytest.raises(MonitorError, match="another run or role"):
            skill_catalog(tmp_path, base.run_id, [row])
    else:
        catalog = skill_catalog(tmp_path, base.run_id, [row])
        assert any(skill["role"] == "grounding" and skill["name"] == "research-workflow" for skill in catalog["skills"])
        assert request["role"] != "grounding"


def _attempt(identity, *, run_id="run", role="researcher", command="SELF_EVOLVE",
             state="CLOSED", observation=None, started_at=1, experiment_id=None):
    request = {"run_id": run_id, "role": role, "command": command,
               "component_id": "component", "task_context": {"key": {"cycle": 1}}}
    return {"attempt_id": identity, "run_id": run_id, "kind": "AGENT",
            "state": state, "terminal_reason": "SUCCEEDED" if state == "CLOSED" else None,
            "started_at": started_at, "ended_at": started_at + 1 if state == "CLOSED" else None,
            "experiment_id": experiment_id, "attempt_ordinal": 1,
            "observation_json": json.dumps({"request": request, **(observation or {})})}


def test_pre_model_failure_details_remain_available_without_transcript(tmp_path):
    trace = [{"bibliographic_identity": "openalex:W1", "events": [{"stage": "landing_uri",
              "outcome": "VALIDATION_FAILURE", "source_host": "rejected.example.org",
              "failure_code": "VALIDATION_FAILED", "http_status": None}]}]
    row = _attempt("failure", command="LITERATURE_REVIEW", observation={
        "failure_code": "literature_fetch_failed", "failure_stage": "literature_retrieval",
        "retry_class": "INFRASTRUCTURE", "literature_diagnostic": "landing_uri is not globally routable",
        "preflight_diagnostics": {"literature_download_trace": trace}})
    row["terminal_reason"] = "FAILED"
    _database(tmp_path, [row])
    result = read_observer_detail(tmp_path, "run", "attempt", attempt_id="failure")
    assert not result["available"] and result["events"] == []
    assert result["attempt"]["failure_stage"] == "literature_retrieval"
    assert result["attempt"]["diagnostic_summary"] == "landing_uri is not globally routable"
    assert result["literature_download_trace"] == trace


def _database(root, attempts=(), *, facts=(), transitions=(), commands=()):
    root.mkdir(exist_ok=True)
    with sqlite3.connect(root / "state.sqlite") as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE workflow_runs(run_id TEXT)")
        db.executemany("INSERT INTO workflow_runs VALUES (?)", [("run",), ("other",)])
        db.execute("CREATE TABLE execution_attempts(attempt_id TEXT,run_id TEXT,kind TEXT,state TEXT,terminal_reason TEXT,started_at REAL,ended_at REAL,experiment_id TEXT,attempt_ordinal INTEGER,observation_json TEXT)")
        for row in attempts:
            db.execute("INSERT INTO execution_attempts VALUES (?,?,?,?,?,?,?,?,?,?)", tuple(row[k] for k in ("attempt_id", "run_id", "kind", "state", "terminal_reason", "started_at", "ended_at", "experiment_id", "attempt_ordinal", "observation_json")))
        db.execute("CREATE TABLE research_facts(run_id TEXT,fact_id TEXT,fact_type TEXT,payload_json TEXT)")
        db.executemany("INSERT INTO research_facts VALUES (?,?,?,?)", facts)
        db.execute("CREATE TABLE workflow_transitions(run_id TEXT,workflow_id TEXT,workflow_kind TEXT,revision INTEGER,command_id TEXT,created_at REAL,payload_json TEXT)")
        db.executemany("INSERT INTO workflow_transitions VALUES (?,?,?,?,?,?,?)", transitions)
        db.execute("CREATE TABLE workflow_commands(run_id TEXT,command_id TEXT,command_type TEXT,response_status TEXT,request_json TEXT)")
        db.executemany("INSERT INTO workflow_commands VALUES (?,?,?,?,?)", commands)
    # sqlite's connection context commits, but does not close the writer.
    # Finish its checkpoint before readers and their immutability assertions.
    db.close()


def _git(repo, *args):
    return subprocess.check_output(("git", "-C", str(repo), *args), stderr=subprocess.DEVNULL).decode().strip()


def _plugin(root):
    repo = root / "run/experts/researcher/plugin"
    skill = repo / "skills/resource-planning/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: resource-planning\ndescription: Plan compute\n---\nFirst plan.\n")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=test", "-c", "user.email=test@local", "commit", "-qm", "seed")
    base = _git(repo, "rev-parse", "HEAD")
    skill.write_text(skill.read_text().replace("First plan.", "Use own measured peak."))
    (skill.parent / "reference.md").write_text("Only own measurement evidence.\n")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=test", "-c", "user.email=test@local", "commit", "-qm", "learn")
    return repo, base, _git(repo, "rev-parse", "HEAD")


def _binding(commit):
    return {"plugin": {"run_id": "run", "role": "researcher", "name": "hypoloom-researcher", "commit": commit}}


def test_skill_reads_persisted_commit_diff_and_actual_adoption_not_worktree(tmp_path):
    repo, base, commit = _plugin(tmp_path)
    update = _attempt("update", observation={"session": {"expert_resources": _binding(base)}, "plugin_update": {"base_commit": base, "commit": commit}})
    adopted = _attempt("next", command="EXPERIMENT_DESIGN_IMPLEMENT", started_at=5,
                       observation={"session": {"session_id": "chain", "expert_resources": _binding(commit)}})
    _database(tmp_path, [update, adopted])
    (repo / "skills/resource-planning/SKILL.md").write_text("Unaccepted dirty worktree")
    before = (tmp_path / "state.sqlite").read_bytes()
    detail = read_observer_detail(tmp_path, "run", "skill", role="researcher", name="resource-planning", commit=commit)
    assert "Use own measured peak." in detail["body"]
    assert "Unaccepted" not in json.dumps(detail)
    assert "-First plan." in detail["diff"]["text"]
    assert "+Use own measured peak." in detail["diff"]["text"]
    assert detail["update_attempt"]["attempt_id"] == "update"
    assert [row["attempt_id"] for row in detail["adoption"]] == ["next"]
    assert detail["supporting_files"][0]["text"] == "Only own measurement evidence.\n"
    assert (tmp_path / "state.sqlite").read_bytes() == before


def test_missing_git_preserves_unknown_version_and_rejects_unbound_commit(tmp_path):
    commit = "a" * 40
    row = _attempt("old", observation={"session": {"expert_resources": _binding(commit)}})
    _database(tmp_path, [row])
    catalog = skill_catalog(tmp_path, "run", [row])
    assert catalog["unknown_versions"] == 1
    assert catalog["versions"][0]["skill_count"] is None
    assert not read_observer_detail(tmp_path, "run", "skill", role="researcher", name="resource-planning", commit=commit)["available"]
    with pytest.raises(MonitorError):
        read_observer_detail(tmp_path, "run", "skill", role="researcher", name="resource-planning", commit="b" * 40)


@pytest.mark.parametrize("selectors", [{"role": "../other", "name": "x"}, {"role": "researcher", "name": "../.env"}, {"role": "researcher", "name": "x", "commit": "HEAD"}, {"path": "/etc/passwd"}, {"limit": 201}, {"offset": -1}])
def test_detail_rejects_paths_and_invalid_bounds_before_io(tmp_path, selectors):
    with pytest.raises(ValueError):
        read_observer_detail(tmp_path, "run", "skill", **selectors)


def test_attempt_scope_is_current_invocation_and_redacts_tool_credentials(tmp_path):
    own = _attempt("ours", observation={"session": {"session_id": "chain"}, "diagnostics": {
        "provider_turn_usage_normalization": {"reported_num_turns": 5, "turn_count": 2},
        "structured_output_correction": {"scope": "native_stop_validation", "rejected_count": 1}}})
    foreign = _attempt("theirs", run_id="other")
    _database(tmp_path, [own, foreign])
    target = tmp_path / "diagnostics/agents/run/ours/stdout.log"
    target.parent.mkdir(parents=True)
    events = [{"type": "assistant", "message": {"id": "m", "content": [{"type": "tool_use", "id": "t", "name": "Read", "input": {"file_path": "report.md", "api_key": "secret-value"}}]}},
              {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "content": "report body", "is_error": False}]}}]
    target.write_text("\n".join(json.dumps(event) for event in events) + "\n")
    detail = read_observer_detail(tmp_path, "run", "attempt", attempt_id="ours")
    assert detail["scope"] == "current_invocation"
    assert (detail["turns"], detail["raw_turns"]) == (2, 5)
    assert detail["corrections"]["rejected_count"] == 1
    assert {row["type"] for row in detail["events"]} >= {"tool_use", "tool_result"}
    assert "secret-value" not in json.dumps(detail)
    with pytest.raises(MonitorError):
        read_observer_detail(tmp_path, "run", "attempt", attempt_id="theirs")


def test_attempt_refuses_symlink_and_cross_run_archive(tmp_path):
    _database(tmp_path, [_attempt("ours", observation={"session": {"transcript": {"path": "other/agent/theirs/transcript"}}})])
    with pytest.raises(MonitorError):
        read_observer_detail(tmp_path, "run", "attempt", attempt_id="ours")
    target = tmp_path / "diagnostics/agents/run/ours"
    target.mkdir(parents=True)
    secret = tmp_path / "private.txt"
    secret.write_text("secret content")
    (target / "stdout.log").symlink_to(secret)
    with pytest.raises(MonitorError):
        read_observer_detail(tmp_path, "run", "attempt", attempt_id="ours")


def test_interaction_stage_retries_and_goal_abort_are_one_occurrence(tmp_path):
    events = [{"action": "leader_selected", "source_attempt_id": "observer", "leader": {"role": "researcher"}},
              {"action": "leader_round_started", "source_attempt_id": "observer", "leader": {"role": "researcher"}},
              {"action": "leader_aborted", "source_attempt_id": "observer", "goal_verdict_id": "goal-ok"}]
    transitions = [("run", "run", "RUN", n, f"c{n}", n, json.dumps({"expert_interaction": event})) for n, event in enumerate(events, 1)]
    _database(tmp_path, transitions=transitions)
    detail = read_observer_detail(tmp_path, "run", "interactions")
    assert detail["total"] == 1
    item = detail["interactions"][0]
    assert item["state"] == "ABORTED"
    assert item["verdict"] is None
    assert item["goal_verdict_id"] == "goal-ok"
    assert len(item["stages"]) == 3


@pytest.mark.parametrize("started", [False, True])
def test_debate_restart_detail_distinguishes_empty_finish_from_actual_experiment(tmp_path, started):
    participant = {"scope": {"role": "researcher", "component_id": "component", "chain": "a"}, "hypothesis_id": "hyp"}
    plan = {"role": "researcher", "component_id": "component", "cycle": 2, "parent_hypothesis_id": "hyp"}
    events = [
        {"action": "debate_proposed", "proposal": {"debate": {"participants": [participant]}}},
        {"action": "debate_verdict"},
        {"action": "debate_restarts_designated", "plans": [plan] if started else [],
         "not_started": [] if started else [{"role": "researcher", "component_id": "component", "reason": "round_budget_exhausted"}]},
        {"action": "debate_restarts_finished", "plans": [plan] if started else []},
    ]
    transitions = [("run", "run", "RUN", 1, "prior", 1, json.dumps({"task_context": {"key": {
        "expert_slot": "researcher", "component_id": "component", "cycle": 1}}}))]
    transitions += [("run", "run", "RUN", n, f"c{n}", n, json.dumps({"expert_interaction": {
        **event, "debate_id": "debate", "source_attempt_id": "source"}})) for n, event in enumerate(events, 2)]
    facts = []
    if started:
        transitions.append(("run", "experiment", "EXPERIMENT", 1, "create", 4,
            json.dumps({"role": "researcher", "component_id": "component", "expert_cycle": 2})))
        facts.append(("run", "fact", "CanonicalOutcomeAccepted", json.dumps({"experiment_id": "experiment", "outcome_id": "outcome"})))
    _database(tmp_path, transitions=transitions, facts=facts)
    item = read_observer_detail(tmp_path, "run", "interactions")["interactions"][0]
    assert item["state"] == "FINISHED"
    restart = item["restarts"][0]
    assert (restart["source_cycle"], restart["target_cycle"]) == (1, 2)
    assert restart["state"] == ("OUTCOMES_RECORDED" if started else "NOT_STARTED")
    assert restart["outcome_ids"] == (["outcome"] if started else [])
    assert restart["reason"] == (None if started else "round_budget_exhausted")


def test_embedded_bound_plugin_and_concurrent_old_version_remain_visible(tmp_path):
    _, base, commit = _plugin(tmp_path)
    old = _attempt("concurrent", state="OPEN", observation={"session": {"expert_resources": _binding(base)}})
    new = _attempt("new", started_at=5, observation={"session": {"expert_resources": _binding(commit)}})
    payload = json.loads(new["observation_json"])
    payload.pop("session")
    payload["request"]["input_artifacts"] = [{"ref": "new--expert-resources", "media_type": "application/json", "content_base64": base64.b64encode(json.dumps(_binding(commit)).encode()).decode()}]
    new["observation_json"] = json.dumps(payload)
    current = skill_catalog(tmp_path, "run", [old, new])
    assert {item["commit"] for item in current["skills"] if item["current"]} == {base, commit}


def test_session_retries_are_attempts_and_trace_deduplicates_tool_snapshots(tmp_path):
    first = _attempt("first", observation={"session": {"session_id": "chain"}})
    first["terminal_reason"] = "FAILED"
    second = _attempt("second", started_at=4, observation={"session": {"session_id": "chain"}})
    _database(tmp_path, [first, second])
    sessions = read_observer_detail(tmp_path, "run", "sessions", session_id="chain")
    assert sessions["total"] == 1
    assert len(sessions["sessions"][0]["attempts"]) == 2
    target = tmp_path / "diagnostics/agents/run/second/stdout.log"
    target.parent.mkdir(parents=True)
    call = {"type": "assistant", "message": {"id": "m", "content": [{"type": "tool_use", "id": "t", "name": "Read", "input": {"file_path": ".env"}}]}}
    result = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "content": "PRIVATE_UNLABELLED_VALUE"}]}}
    target.write_text("\n".join(json.dumps(item) for item in [call, call, result]) + "\n")
    detail = read_observer_detail(tmp_path, "run", "attempt", attempt_id="second", limit=1)
    assert detail["total"] == 2 and detail["has_more"]
    detail = read_observer_detail(tmp_path, "run", "attempt", attempt_id="second", offset=1)
    assert "PRIVATE_UNLABELLED_VALUE" not in json.dumps(detail)


def test_resource_detail_uses_owned_measurement_and_preserves_future_source_link(tmp_path):
    specs, commands, attempts = [], [], []
    for index in (1, 2):
        identity = f"experiment-{index}"
        estimate = {"workload": {"batch": "8"}, "peak_memory_mib": 128 if index == 1 else 512,
                    "basis": "Own previous workload", "measurement_attempt_ids": [] if index == 1 else ["process-1"]}
        contract = {"source_submit_command_id": f"submit-{index}", "resource_intent": {"kind": "GPU", "workload_memory_mib": 256 if index == 1 else 512}}
        spec = {"experiment_id": identity, "component_id": "component", "method_family_id": "family", "execution_contract": contract}
        specs.append(("run", f"prereg-{index}", "ExperimentPreregistered", json.dumps(spec)))
        commands.append(("run", f"submit-{index}", "CREATE_EXPERIMENT", "accepted", json.dumps({"source_request": {"role": "researcher"}, "source_snapshot": {"arguments": {"resource_estimate": estimate}}})))
        row = _attempt(f"process-{index}", experiment_id=identity, observation={"resource_usage": [{"owned_memory": {
            "sample_count": 2, "unknown_sample_count": 0,
            "peak": {"complete": True, "reason": None, "resident_bytes": 362 * 1048576}}}]})
        row["kind"] = "PROCESS"
        attempts.append(row)
    _database(tmp_path, attempts, facts=specs, commands=commands)
    result = read_observer_detail(tmp_path, "run", "resources", experiment_id="experiment-1")
    item = result["experiments"][0]
    assert item["estimate"]["peak_memory_mib"] == 128
    assert item["attempts"][0]["admitted"] == 256
    assert item["attempts"][0]["measured"]["sample_peak_bytes"] == 362 * 1048576
    assert item["next_estimate_sources"] == [{"experiment_id": "experiment-2", "measurement_attempt_ids": ["process-1"]}]
    assert not result["shared_pool"]["available"]


def test_git_environment_cannot_redirect_the_accepted_repository(tmp_path, monkeypatch):
    _, base, _ = _plugin(tmp_path)
    row = _attempt("bound", observation={"session": {"expert_resources": _binding(base)}})
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "another-private-repository"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.worktree")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "/not-the-bound-worktree")
    assert skill_catalog(tmp_path, "run", [row])["versions"][0]["available"]


def test_skill_latest_binding_is_independent_of_database_command_order(tmp_path):
    _, base, commit = _plugin(tmp_path)
    old = _attempt("z-old", command="RESULT_ANALYSIS", observation={"session": {"expert_resources": _binding(base)}})
    recent = _attempt("a-new", command="EXPERIMENT_DESIGN_IMPLEMENT", started_at=10,
                      observation={"session": {"expert_resources": _binding(commit)}})
    catalog = skill_catalog(tmp_path, "run", [recent, old])
    assert [item["commit"] for item in catalog["versions"] if item["current"]] == [commit]


def test_explicit_pool_reader_uses_only_aggregate_snapshot_and_drops_private_fields(tmp_path, monkeypatch):
    from hypoloom import gpu_pool_client
    calls = []
    class Client:
        def __init__(self, path, **options):
            calls.append((path, options))
        def request(self, operation, arguments):
            calls.append((operation, arguments))
            return {"sampling_started_at": 10, "sampled_at": 11, "namespace": "private-root",
                "devices": [{"gpu_uuid": "GPU-11111111-1111-1111-1111-111111111111", "total_memory_mib": 16000,
                    "free_memory_mib": 9000, "used_memory_mib": 7000, "outstanding_memory_mib": 1000,
                    "reserve_memory_mib": 512, "allocatable_memory_mib": 7488, "active_commitments": 2,
                    "unknown_attribution_commitments": 1, "run_id": "private-run", "attempt_id": "private-attempt",
                    "commitments": [{"namespace": "private-root", "workload_memory_mib": 4000}]}]}
    monkeypatch.setattr(gpu_pool_client, "PoolClient", Client)
    path = tmp_path / "pool.sock"
    result = read_observer_pool(path, expected_server_uid=123)
    assert calls == [(path, {"timeout_seconds": 0.25, "expected_server_uid": 123}), ("snapshot", {})]
    assert result["available"] and result["devices"][0]["active_commitments"] == 2
    assert result["devices"][0]["allocatable_memory_mib"] == 7488
    assert "private-" not in json.dumps(result)
    assert "workload_memory_mib" not in json.dumps(result)
    assert str(path) not in json.dumps(result)
    assert list(tmp_path.iterdir()) == []


def test_explicit_pool_reader_failure_never_reports_private_error_text(tmp_path, monkeypatch):
    from hypoloom import gpu_pool_client
    from hypoloom.gpu_pool import PoolError
    class Client:
        def __init__(self, *args, **kwargs):
            pass
        def request(self, operation, arguments):
            raise PoolError("unavailable", "/private/socket: private-auth-value")
    monkeypatch.setattr(gpu_pool_client, "PoolClient", Client)
    result = read_observer_pool(tmp_path / "absent.sock")
    assert not result["available"] and result["code"] == "unavailable"
    assert "private" not in json.dumps(result)
    assert not (tmp_path / "absent.sock").exists()


def test_explicit_pool_reader_real_cpu_transport_keeps_request_rows_unchanged(tmp_path):
    from hypoloom.compute_resources import GpuRuntimeObservation
    from hypoloom.gpu_pool import CapacitySample, GpuPool
    from hypoloom.gpu_pool_server import PoolServer
    uuid = "GPU-11111111-1111-1111-1111-111111111111"
    pool = GpuPool(tmp_path / "pool", members={os.getuid(): [uuid]}, reserve_memory_mib=1000,
        sampler=lambda: CapacitySample((GpuRuntimeObservation(uuid=uuid, total_memory_mib=16000,
            free_memory_mib=12000, used_memory_mib=4000, utilization_percent=75),)))
    # Only a fresh temporary reservation; no Runner or GPU computation is started.
    pool.handle(os.getuid(), "reserve", {"key": {"namespace": "private-root", "run_id": "private-run",
        "execution_id": "private-effect", "attempt_id": "private-attempt"}, "allowed_gpu_uuids": [uuid],
        "gpu_count": 1, "workload_memory_mib": 6000, "reserve_memory_mib": 1000})
    before = pool.database_path.read_bytes()
    calls = []
    original = pool.handle
    def handle(uid, operation, arguments):
        calls.append((operation, arguments))
        return original(uid, operation, arguments)
    pool.handle = handle
    socket_path = tmp_path / "pool.sock"
    with PoolServer(pool, socket_path):
        result = read_observer_pool(socket_path, expected_server_uid=os.getuid())
    assert calls == [("snapshot", {})]
    assert result["available"]
    assert result["devices"][0]["active_commitments"] == 1
    assert result["devices"][0]["allocatable_memory_mib"] == 5000
    assert "private-" not in json.dumps(result)
    assert pool.database_path.read_bytes() == before
    assert not socket_path.exists()


def test_session_lineage_uses_recorded_fork_resume_and_unknown_launch_arguments(tmp_path):
    lineage = {"session_id": "child", "parent_session_id": "parent", "scope": "chain-a",
               "chain": "a", "cycle": 2, "branch": "prom/researcher/r2/chain-a"}
    fork = _attempt("fork", started_at=1, observation={"session": {**lineage,
        "launch_arguments": ["--resume", "parent", "--fork-session", "--session-id", "child",
                             "--private-option", "/private/path-never-displayed"]}})
    resume = _attempt("resume", started_at=4, observation={"session": {**lineage,
        "launch_arguments": ["--resume", "child"]}})
    lost = _attempt("lost", started_at=7, observation={"session": dict(lineage)})
    lost["terminal_reason"] = "LOST"
    new = _attempt("new", started_at=10, observation={"session": {
        "session_id": "parent", "parent_session_id": None, "launch_arguments": ["--session-id", "parent"]}})
    _database(tmp_path, [fork, resume, lost, new])
    detail = read_observer_detail(tmp_path, "run", "sessions", session_id="child")
    session = detail["sessions"][0]
    assert {key: session[key] for key in lineage} == lineage
    assert session["launch_mode"] == "unknown"
    assert session["launch_modes"] == ["fork", "resume", "unknown"]
    assert [item["launch_mode"] for item in session["attempts"]] == ["fork", "resume", "unknown"]
    for item in session["attempts"]:
        assert {key: item[key] for key in lineage} == lineage
    assert "launch_arguments" not in json.dumps(detail)
    assert "/private/path-never-displayed" not in json.dumps(detail)
    assert read_observer_detail(tmp_path, "run", "attempt", attempt_id="fork")["attempt"]["launch_mode"] == "fork"
    assert read_observer_detail(tmp_path, "run", "attempt", attempt_id="new")["attempt"]["launch_mode"] == "new"
