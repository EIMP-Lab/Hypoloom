"""Init review preserves pool ownership and makes missing history an explicit choice."""
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from hypoloom import bootstrap, bootstrap_inspect, bootstrap_questions
from hypoloom import managed_gpu_pool as manager
from hypoloom.gpu_pool_lifecycle import inspect_dependencies, retired_owner_keys
from test_managed_pool_lifecycle import managed, binding, GPU, GPU2  # noqa: F401
from test_bootstrap import checkout, arguments  # noqa: F401


def retired_history(root, run="retired", *, uid=None, state="RELEASED", pgid=None):
    with sqlite3.connect(root / "pool.sqlite3") as db:
        db.execute("INSERT INTO runs(uid,namespace,run_id) VALUES(?,'namespace',?)", (os.geteuid() if uid is None else uid, run))
        db.execute("INSERT INTO requests(uid,namespace,run_id,execution_id,attempt_id,state,intent_json,process_group_id) "
                   "VALUES(?,'namespace',?,'exec','attempt',?,'{}',?)", (os.geteuid() if uid is None else uid, run, state, pgid))


def rows(root):
    with sqlite3.connect(root / "pool.sqlite3") as db:
        return {t: db.execute(f"SELECT * FROM {t}").fetchall() for t in ("runs", "requests")}


def maintenance(managed):
    args, state, root = managed
    manager.ensure(args)
    args.reviewed_action = "reconfigure"
    args.reserve_mib = 512
    return args, state, root


def test_missing_released_history_requires_exact_review_and_is_not_deleted(managed):
    args, state, root = maintenance(managed)
    retired_history(root)
    before = rows(root)
    inode = (root / "pool.sqlite3").stat().st_ino
    proposal = manager.review(args)
    assert not proposal["prepare_available"]
    assert proposal["dependencies"]["unresolved_owners"] == [{"uid": os.geteuid(), "namespace": "namespace",
        "run_id": "retired", "released_requests": 1, "unreleased_requests": 0,
        "process_present": False, "can_review_retirement": True}]
    assert state["events"] == ["launch"] and rows(root) == before
    args.pool_retired_owner = ["namespace/retired"]
    proposal = manager.review(args)
    assert proposal["prepare_available"] and proposal["dependencies"]["reviewed_retired_owners"]
    assert manager.ensure(args)[1] == "reconfigured"
    assert state["pool"].reserve_memory_mib == 512 and rows(root) == before
    assert (root / "pool.sqlite3").stat().st_ino == inode
    assert manager.ensure(args)[1] == "reused"
    assert state["events"] == ["launch", "stop", "launch"]
    # A new maintenance operation has no durable exemption from missing state.
    args.reserve_mib = 768
    args.pool_retired_owner = None
    assert not manager.review(args)["prepare_available"]
    assert rows(root) == before


@pytest.mark.parametrize("condition", ["foreign", "empty", "other_owner", "local", "unreadable", "live", "missing_target", "invalid_pgid"])
def test_retirement_does_not_override_other_dependencies(managed, condition):
    args, state, root = maintenance(managed)
    process = None
    try:
        if condition == "live":
            process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"], start_new_session=True)
        retired_history(root, uid=os.geteuid() + 1 if condition == "foreign" else None,
                        pgid=process.pid if process else None)
        if condition == "empty":
            with sqlite3.connect(root / "pool.sqlite3") as db: db.execute("DELETE FROM requests")
        if condition == "other_owner": retired_history(root, "another")
        if condition == "local":
            directory = root.parents[2] / "state/retired"
            directory.mkdir(parents=True)
        if condition == "unreadable":
            directory = root.parents[2] / "state/broken"
            directory.mkdir(parents=True)
            (directory / "state.sqlite").write_text("not a database")
        if condition == "missing_target":
            directory = root.parents[2] / "state"
            directory.mkdir(parents=True)
            (directory / "retired").symlink_to(directory / "missing")
        if condition == "invalid_pgid":
            with sqlite3.connect(root / "pool.sqlite3") as db:
                db.execute("UPDATE requests SET process_group_id=-1")
        args.pool_retired_owner = ["namespace/retired"]
        before = rows(root)
        assert not manager.review(args)["prepare_available"]
        with pytest.raises(manager.ManagedPoolError, match="dependencies_active_or_unknown"):
            manager.ensure(args)
        assert rows(root) == before and state["events"] == ["launch"]
    finally:
        if process:
            process.terminate(); process.wait(timeout=5)


@pytest.mark.parametrize("status", ["QUEUED", "RESERVED", "LAUNCHING", "BOUND"])
def test_retired_owner_with_unreleased_request_cannot_be_confirmed(managed, status):
    args, state, root = maintenance(managed)
    retired_history(root, state=status)
    args.pool_retired_owner = ["namespace/retired"]
    proposal = manager.review(args)
    assert not proposal["prepare_available"]
    assert not proposal["dependencies"]["unresolved_owners"][0]["can_review_retirement"]
    assert {"reason": "unreleased_pool_request"} in proposal["dependencies"]["blockers"]
    assert state["events"] == ["launch"]


@pytest.mark.parametrize("run_state", ["ACTIVE", "CANCELLED"])
def test_retirement_assertion_cannot_hide_a_retained_run(managed, run_state):
    args, state, root = maintenance(managed)
    binding(root, run_state=run_state)
    retired_history(root, "known")
    args.pool_retired_owner = ["namespace/known"]
    proposal = manager.review(args)
    assert not proposal["prepare_available"]
    assert {"reason": "retired_owner_review_changed"} in proposal["dependencies"]["blockers"]


@pytest.mark.parametrize("when", ["before_stop", "after_stop"])
def test_retirement_is_rechecked_during_maintenance(managed, monkeypatch, when):
    args, state, root = maintenance(managed)
    retired_history(root)
    args.pool_retired_owner = ["namespace/retired"]
    assert manager.review(args)["prepare_available"]
    if when == "before_stop":
        original = manager._observe
        calls = 0
        def observe(*a, **kw):
            nonlocal calls
            calls += 1
            if calls == 2: retired_history(root, "new-owner")
            return original(*a, **kw)
        monkeypatch.setattr(manager, "_observe", observe)
    else:
        original = manager._stop_unit
        def stop(*a, **kw):
            result = original(*a, **kw)
            with sqlite3.connect(root / "pool.sqlite3") as db:
                db.execute("UPDATE requests SET state='QUEUED'")
            return result
        monkeypatch.setattr(manager, "_stop_unit", stop)
    with pytest.raises(manager.ManagedPoolError, match="pool_plan_changed|dependencies_active_or_unknown"):
        manager.ensure(args)
    assert state["events"] == (["launch"] if when == "before_stop" else ["launch", "stop"])


@pytest.mark.parametrize("value", [["none", "namespace/run"], ["../run"], ["namespace/../run"], ["run"], ["namespace/run/extra"]])
def test_retired_owner_arguments_use_existing_identifiers(value):
    with pytest.raises(ValueError): retired_owner_keys(value)


@pytest.mark.parametrize("language", ["en", "zh-CN"])
@pytest.mark.parametrize("action", ["reuse", "reconfigure"])
def test_pool_difference_question_preserves_full_pool_and_run_subset(tmp_path, language, action):
    args = bootstrap.build_parser().parse_args(["inspect", "--managed-pool", "--allow-gpu",
        "--gpu-device-uuid", GPU, "--ui-language", language])
    result = {"missing": [], "blockers": []}
    review = {"gpu": {"memory_budget": {"reserve_floor_mib": 512}, "pool": {
        "action": "reuse", "reserve_mib": 4096, "prepare_available": True,
        "members": {str(os.geteuid()): [GPU, GPU2]}}}}
    bootstrap_inspect._pool_choices(args, result, review)
    assert args.pool_action is None and result["missing"] == ["pool_configuration"]
    question = bootstrap_questions.questions(args, result)[0][1]
    assert "4096" in question["question"] and "512" in question["question"] and GPU2 in question["question"]
    bootstrap_questions.apply_answers(args, result, {question["question"]: question["options"][action == "reconfigure"]["label"]})
    assert args.pool_action == action and args.pool_device_uuid == [GPU, GPU2] and args.gpu_device_uuid == [GPU]
    assert args.pool_reserve_mib == (512 if action == "reconfigure" else 4096)
    parsed = bootstrap.build_parser().parse_args(bootstrap_questions.interaction(args, {"missing": []}, tmp_path)["inspect_argv"][4:])
    assert parsed.pool_action == action and parsed.pool_device_uuid == [GPU, GPU2]


def test_equal_reserve_reuses_without_extra_question():
    args = bootstrap.build_parser().parse_args(["inspect", "--managed-pool", "--allow-gpu"])
    result = {"missing": [], "blockers": []}
    review = {"gpu": {"memory_budget": {"reserve_floor_mib": 512}, "pool": {"action": "reuse",
        "reserve_mib": 512, "prepare_available": True, "members": {str(os.geteuid()): [GPU, GPU2]}}}}
    bootstrap_inspect._pool_choices(args, result, review)
    assert not result["missing"] and args.pool_action == "reuse"


def test_completed_maintenance_converges_to_reuse_without_retirement_arguments(tmp_path):
    args = bootstrap.build_parser().parse_args(["inspect", "--managed-pool", "--allow-gpu",
        "--pool-action", "reconfigure", "--pool-reserve-mib", "512", "--pool-retired-owner", "old/retired"])
    review = {"gpu": {"memory_budget": {"reserve_floor_mib": 512}, "pool": {"action": "reuse",
        "reserve_mib": 512, "prepare_available": True, "members": {str(os.geteuid()): [GPU]}}}}
    result = {"missing": [], "blockers": []}
    bootstrap_inspect._pool_choices(args, result, review)
    parsed = bootstrap.build_parser().parse_args(bootstrap_questions.interaction(args, result, tmp_path)["inspect_argv"][4:])
    assert parsed.pool_action == "reuse" and parsed.pool_retired_owner is None
    assert not result["missing"]


@pytest.mark.parametrize("language", ["en", "zh-CN"])
@pytest.mark.parametrize("confirmed", [True, False])
def test_retirement_answers_preserve_exact_keys_and_defer_does_not_loop(tmp_path, language, confirmed):
    args = bootstrap.build_parser().parse_args(["inspect", "--managed-pool", "--allow-gpu", "--ui-language", language])
    owner = {"uid": os.geteuid(), "namespace": "old-namespace", "run_id": "old-run",
             "released_requests": 3, "can_review_retirement": True}
    review = {"gpu": {"memory_budget": {"reserve_floor_mib": 512}, "pool": {
        "action": "reconfigure", "reserve_mib": 512, "prepare_available": False, "members": {str(os.geteuid()): [GPU]},
        "dependencies": {"unresolved_owners": [owner], "blockers": [{"reason": "pool_owner_outside_known_roots"}]}}}}
    result = {"missing": [], "blockers": []}
    bootstrap_inspect._pool_choices(args, result, review)
    question = bootstrap_questions.questions(args, result)[0][1]
    assert "old-namespace" in question["question"] and "old-run" in question["question"]
    bootstrap_questions.apply_answers(args, result, {question["question"]: question["options"][confirmed]["label"]})
    assert args.pool_retired_owner == (["old-namespace/old-run"] if confirmed else ["none"])
    parsed = bootstrap.build_parser().parse_args(bootstrap_questions.interaction(args, {"missing": []}, tmp_path)["inspect_argv"][4:])
    assert parsed.pool_retired_owner == args.pool_retired_owner
    if not confirmed:
        result = {"missing": [], "blockers": []}
        bootstrap_inspect._pool_choices(args, result, review)
        assert not result["missing"]


@pytest.mark.parametrize("choice", ["keep", "confirm", "defer", "unreadable"])
def test_inspect_answers_and_launch_preview_form_one_effect_free_flow(checkout, capsys, monkeypatch, tmp_path, choice):
    from hypoloom.compute_resources import LocalComputeResources, ComputeCapabilities, GpuCapability
    repo, overrides = checkout
    prepare = arguments(repo, [*overrides, "safety.gpus=auto", "methodology.budget.min_vram_per_experiment=1024"])
    prepare[prepare.index("--cpu-only")] = "--allow-gpu"
    assert bootstrap.main([*prepare, "--write"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(bootstrap_inspect, "supervisor_check", lambda: {"status": "ready"})
    monkeypatch.setattr(bootstrap_inspect, "_gpu_inventory", lambda *_: {"status": "observed", "devices": [{"uuid": GPU}, {"uuid": GPU2}]})
    monkeypatch.setattr(bootstrap_inspect, "_pools", lambda *_: {})
    monkeypatch.setattr(LocalComputeResources, "snapshot", lambda *_: ComputeCapabilities(gpus=(
        GpuCapability(index=0, uuid=GPU, name="synthetic", total_memory_mib=16000, free_memory_mib=16000, driver_version="test"),)))
    monkeypatch.setattr(bootstrap, "start_fresh", lambda *_: pytest.fail("preview started a run"))
    monkeypatch.setattr(manager, "ensure", lambda *_: pytest.fail("preview changed a pool"))
    calls = []
    def review(args):
        calls.append(args)
        reconfigure = args.reviewed_action == "reconfigure"
        acknowledged = args.pool_retired_owner == ["old/retired"]
        owner = {"uid": os.geteuid(), "namespace": "old", "run_id": "retired",
            "released_requests": 2, "unreleased_requests": 0, "process_present": False, "can_review_retirement": True}
        result = {"status": "ok", "action": "reconfigure" if reconfigure else "reuse",
            "members": {str(os.geteuid()): [GPU, GPU2]}, "reserve_mib": args.reserve_mib if reconfigure else 4096,
            "prepare_available": not reconfigure or acknowledged, "start_ready": not reconfigure}
        if reconfigure and choice == "unreadable":
            return {**result, "status": "blocked", "action": None, "prepare_available": False,
                "reason": "pool_configuration_unknown", "detail": "unit_working_directory_query_failed"}
        if reconfigure:
            result.update(previous_configuration={"reserve_mib": 4096, "members": {str(os.geteuid()): [GPU, GPU2]}},
                reason=None if acknowledged else "dependencies_active_or_unknown",
                dependencies={"unresolved_owners": [] if acknowledged else [owner],
                    "reviewed_retired_owners": [owner] if acknowledged else [],
                    "blockers": [] if acknowledged else [{"reason": "pool_owner_outside_known_roots"}]})
        return result
    monkeypatch.setattr(manager, "review", review)
    argv = ["inspect", "topic", "--repository", str(repo), "--allow-gpu", "--managed-pool",
        "--gpu-device-uuid", GPU, "--gpu-runtime-prefix", sys.prefix, "--auth-transport", "none",
        "--institutional-browser-mode", "off", "--offline", "--ui-language", "zh-CN"]
    assert bootstrap.main(argv) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["missing"] == ["pool_configuration"] and "launch_argv" not in first
    source = tmp_path / "answers.json"
    def answer(result, index):
        question = result["interaction"]["questions"][0]
        source.write_text(json.dumps({question["question"]: question["options"][index]["label"]}))
        assert bootstrap.main([*result["interaction"]["answer_argv"][4:-1], str(source)]) == 0
        return json.loads(capsys.readouterr().out)
    result = answer(first, 0 if choice == "keep" else 1)
    if choice == "unreadable":
        assert result["status"] == "blocked" and not result["missing"]
        assert result["blockers"] == [{"code": "pool_configuration_unknown", "detail": "unit_working_directory_query_failed"}]
        assert not result["interaction"]["questions"] and "launch_argv" not in result
        parsed = bootstrap.build_parser().parse_args(result["interaction"]["inspect_argv"][4:])
        assert parsed.pool_action == "reconfigure" and parsed.pool_reserve_mib == 512
        assert parsed.gpu_device_uuid == [GPU] and parsed.pool_device_uuid == [GPU, GPU2]
        assert result["identity"] == first["identity"]
        assert not (repo / "state").exists() and not (repo / ".local/gpu-pools").exists()
        return
    if choice != "keep":
        assert result["missing"] == ["pool_retired_owner"]
        assert "launch_argv" not in result and "init_session_argv" not in result
        result = answer(result, 1 if choice == "confirm" else 0)
    assert result["identity"] == first["identity"]
    if choice == "defer":
        assert result["status"] == "blocked" and "launch_argv" not in result
        assert not result["interaction"]["questions"]
    else:
        assert result["status"] == "ready_for_review"
        parsed = bootstrap.build_parser().parse_args(result["launch_argv"][4:])
        assert parsed.gpu_device_uuid == [GPU] and parsed.pool_device_uuid == [GPU, GPU2]
        assert parsed.pool_reserve_mib == (4096 if choice == "keep" else 512)
        assert bootstrap.main(result["launch_argv"][4:] + ["--summary"]) == 0
        final = json.loads(capsys.readouterr().out)
        assert final["effective_configuration"]["gpu"]["pool"]["action"] == ("reuse" if choice == "keep" else "reconfigure")
        if choice == "confirm":
            assert parsed.pool_retired_owner == ["old/retired"]
            raw = bootstrap.launch_arguments(parsed, repo, bootstrap.admit_profile(repo / "profiles/local/topic",
                run_id=parsed.run_id, allow_gpu=True), review={})
            helper = manager.build_parser().parse_args(raw["pool_prepare"][4:])
            assert helper.pool_retired_owner == ["old/retired"] and helper.reserve_mib == 512
            assert "--pool-retired-owner" not in raw["host_start"]
    assert not (repo / "state").exists() and not (repo / ".local/gpu-pools").exists()
