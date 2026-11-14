"""Automatic init membership must not broaden the initial run or operate a pool."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from hypoloom import bootstrap, bootstrap_gpu, bootstrap_inspect, bootstrap_questions
from hypoloom.bootstrap_review import gpu_selection_summary
from hypoloom.compute_resources import ComputeCapabilities, ResourceUnavailable
from hypoloom.profile_admission import admit_profile
from test_live_gpu_selection import A, B, C, inventory


ROOT = Path(__file__).resolve().parents[1]


def contract(selection="auto"):
    return {"methodology": {"controls": {"product": {"safety": {"gpus": selection}}}}}


def arguments(**updates):
    return SimpleNamespace(**{**dict(allow_gpu=True, managed_pool=False, nonpooled=False,
        gpu_pool_socket=None, gpu_pool_server_uid=None, gpu_device_uuid=None,
        pool_device_uuid=None), **updates})


@pytest.fixture
def devices(monkeypatch):
    monkeypatch.setattr(bootstrap_gpu, "_nvml_inventory", inventory)
    monkeypatch.setattr("hypoloom.compute_resources._nvml_inventory", inventory)
    monkeypatch.setattr(bootstrap_gpu, "_device_access", lambda path: True)


@pytest.mark.parametrize("selection,expected", [("auto", [A, B, C]), ([1], [B]), ([A, C], [A, C])])
def test_default_full_pool_is_independent_of_profile_run_subset(devices, selection, expected):
    args = arguments()
    document = contract(selection)
    original = copy.deepcopy(document)
    bootstrap_gpu.default_scheduling(args)
    result = bootstrap_gpu.resolve(args, document)
    assert args.managed_pool and args.pool_device_uuid == [A, B, C]
    assert args.gpu_device_uuid == expected
    assert result["pool_membership_source"] == "all_usable_devices"
    assert document == original


def test_explicit_run_subset_does_not_shrink_automatic_membership(devices):
    args = arguments()
    args.gpu_device_uuid = [B]
    bootstrap_gpu.default_scheduling(args)
    bootstrap_gpu.resolve(args, contract())
    assert args.gpu_device_uuid == [B] and args.pool_device_uuid == [A, B, C]


def test_permissions_filter_membership_but_busy_memory_does_not(devices, monkeypatch):
    monkeypatch.setattr(bootstrap_gpu, "_device_access", lambda path: path.name != "nvidia1")
    observed = bootstrap_gpu.inventory()
    assert [gpu["uuid"] for gpu in observed["devices"]] == [A, C]
    assert observed["excluded_devices"] == [{"uuid": B, "index": 1, "reason": "gpu_device_access_denied"}]
    from dataclasses import replace
    monkeypatch.setattr(bootstrap_gpu, "_nvml_inventory", lambda: ComputeCapabilities(
        gpus=tuple(replace(gpu, free_memory_mib=0) for gpu in inventory().gpus)))
    args = arguments()
    bootstrap_gpu.default_scheduling(args)
    bootstrap_gpu.resolve(args, contract())
    assert args.pool_device_uuid == [A, C]


def test_device_check_observes_permissions_without_cuda_context(tmp_path, monkeypatch):
    # A regular file cannot masquerade as a GPU device; /dev/null is only an
    # isolated character-device permission probe, not GPU hardware.
    regular = tmp_path / "not-a-device"
    regular.touch()
    assert not bootstrap_gpu._device_access(regular)
    assert not bootstrap_gpu._device_access(tmp_path / "absent")
    assert bootstrap_gpu._device_access(Path("/dev/null"))
    def denied(*args): raise PermissionError("device cgroup denied")
    monkeypatch.setattr(bootstrap_gpu.os, "open", denied)
    assert not bootstrap_gpu._device_access(Path("/dev/null"))


def test_unavailable_inventory_and_cpu_only_profile_do_not_silently_fallback(devices, monkeypatch):
    args = arguments()
    bootstrap_gpu.default_scheduling(args)
    with pytest.raises(ValueError, match="CPU-only"):
        bootstrap_gpu.resolve(args, contract([]))
    assert args.gpu_device_uuid is None and args.pool_device_uuid is None
    def unavailable(): raise ResourceUnavailable("driver unavailable")
    monkeypatch.setattr(bootstrap_gpu, "_nvml_inventory", unavailable)
    assert bootstrap_gpu.inventory()["status"] == "unavailable"
    with pytest.raises(ValueError, match="inventory unavailable"):
        bootstrap_gpu.resolve(args, contract())
    assert args.allow_gpu and args.gpu_device_uuid is None


def test_pinned_review_is_not_expanded_when_another_gpu_appears(devices, monkeypatch):
    monkeypatch.setattr(bootstrap_gpu, "_nvml_inventory", lambda: ComputeCapabilities(gpus=inventory().gpus[:2]))
    args = arguments()
    bootstrap_gpu.default_scheduling(args)
    bootstrap_gpu.resolve(args, contract())
    assert args.pool_device_uuid == [A, B]
    monkeypatch.setattr(bootstrap_gpu, "_nvml_inventory", inventory)
    bootstrap_gpu.resolve(args, contract())
    assert args.pool_device_uuid == [A, B] and args.gpu_device_uuid == [A, B]
    monkeypatch.setattr(bootstrap_gpu, "_device_access", lambda path: path.name != "nvidia1")
    with pytest.raises(ValueError, match="inspect again"):
        bootstrap_gpu.resolve(args, contract())


def test_external_pool_default_is_limited_to_existing_membership(devices, monkeypatch):
    from hypoloom.gpu_pool_client import PoolClient
    calls = []
    def snapshot(self, operation, values):
        calls.append(operation)
        return {"devices": [{"gpu_uuid": B}], "capabilities": ["resource_replanning/1"]}
    monkeypatch.setattr(PoolClient, "request", snapshot)
    args = arguments()
    args.gpu_pool_socket = "/external/pool.sock"
    bootstrap_gpu.default_scheduling(args)
    bootstrap_gpu.resolve(args, contract())
    assert not args.managed_pool and args.pool_device_uuid is None
    assert args.gpu_device_uuid == [B] and calls == ["snapshot"]


def test_cpu_only_and_explicit_nonpooled_modes_remain_explicit():
    args = arguments()
    args.allow_gpu = False
    bootstrap_gpu.default_scheduling(args)
    assert not args.managed_pool
    args.allow_gpu, args.nonpooled = True, True
    bootstrap_gpu.default_scheduling(args)
    assert not args.managed_pool


def test_nonpooled_gpu_mode_uses_profile_without_building_a_pool(devices):
    args = arguments(nonpooled=True)
    bootstrap_gpu.default_scheduling(args)
    bootstrap_gpu.resolve(args, contract([B]))
    assert not args.managed_pool and args.pool_device_uuid is None
    assert args.gpu_device_uuid == [B]


def test_missing_common_device_nodes_block_gpu_init(devices, monkeypatch):
    monkeypatch.setattr(bootstrap_gpu, "_device_access", lambda path: path.name != "nvidia-uvm")
    args = arguments(managed_pool=True)
    with pytest.raises(ValueError, match="no usable GPUs"):
        bootstrap_gpu.resolve(args, contract())
    assert args.gpu_device_uuid is None and args.pool_device_uuid is None


@pytest.fixture
def prepared(tmp_path, devices, monkeypatch):
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".gitignore").write_text("profiles/local/\nstate/\n.local/\n")
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True, capture_output=True)
    target = tmp_path / "target"
    target.mkdir()
    (target / "README.md").write_text("Disposable initialization target.\n")
    for args in (("init", "--quiet"), ("add", "README.md"),
                 ("-c", "user.name=Hypoloom Tests", "-c", "user.email=tests@invalid.example", "commit", "--quiet", "-m", "fixture")):
        subprocess.run(["git", *args], cwd=target, check=True, capture_output=True)
    profile = repository / "profiles/local/topic"
    profile.mkdir(parents=True)
    for path in (ROOT / "profiles/templates/multi-expert-en").glob("*.yaml"):
        document = yaml.safe_load(path.read_text())
        if path.stem == "safety":
            document.update(gpus=[A], allow_read_paths=[str(target)])
        if path.stem == "methodology":
            document["runtime"].update(target_repo=str(target), conda_env=sys.prefix)
            document["budget"]["min_vram_per_experiment"] = 1024
        (profile / path.name).write_text(yaml.safe_dump(document, allow_unicode=True))
    args = bootstrap.build_parser().parse_args(["inspect", "topic", "--repository", str(repository),
        "--allow-gpu", "--auth-transport", "none", "--institutional-browser-mode", "off", "--run-id", "run", "--offline", "--verifier-model", "exact-review-model"])
    monkeypatch.setattr(bootstrap_inspect, "_gpu_inventory", lambda repository: bootstrap_gpu.inventory())
    monkeypatch.setattr(bootstrap_inspect, "_pools", lambda repository: {"status": "absent"})
    monkeypatch.setattr(bootstrap_inspect, "supervisor_check", lambda: {"status": "ready"})
    monkeypatch.setattr("hypoloom.version_check.check_updates", lambda *a, **kw: {"status": "offline"})
    monkeypatch.setattr(bootstrap, "_literature_preflight", lambda args: {"qualified": True})
    reports = []
    def review(plan):
        reports.append(plan)
        uuids = plan.member[0].split("=", 1)[1].split(",")
        return {"action": "create", "prepare_available": True, "members": {str(os.geteuid()): uuids},
            "reserve_mib": plan.reserve_mib, "socket_path": plan.socket_path, "pool_root": plan.pool_root}
    monkeypatch.setattr("hypoloom.managed_gpu_pool.review", review)
    def forbidden(*a, **kw): raise AssertionError("inspection must not operate services")
    monkeypatch.setattr(bootstrap, "start_fresh", forbidden)
    monkeypatch.setattr(bootstrap, "_invoke", forbidden)
    return repository, profile, args, reports


def test_inspect_resolves_full_membership_without_device_questions_or_effects(prepared):
    repository, profile, args, reports = prepared
    before = {path.name: path.read_bytes() for path in profile.iterdir()}
    result = bootstrap_inspect.inspect(args, repository)
    assert result["status"] == "ready_for_review", result
    assert args.managed_pool and args.pool_device_uuid == [A, B, C] and args.gpu_device_uuid == [A]
    assert reports[-1].member == [f"{os.geteuid()}={A},{B},{C}"]
    questions = bootstrap_questions.interaction(args, result, repository)["questions"]
    assert not questions
    argv = result["launch_argv"]
    resolved = bootstrap.build_parser().parse_args(argv[argv.index("launch"):])
    assert resolved.pool_device_uuid == [A, B, C] and resolved.gpu_device_uuid == [A]
    report = admit_profile(profile, run_id="run", allow_gpu=True, verifier_model=resolved.verifier_model)
    commands = bootstrap.launch_arguments(resolved, repository, report)
    assert commands["pool_action_reviewed"]
    assert f"{os.geteuid()}={A},{B},{C}" in commands["pool_prepare"]
    host_argv = commands["host_start"]
    assert host_argv.count("--gpu-device-uuid") == 1 and B not in host_argv and C not in host_argv
    assert {path.name: path.read_bytes() for path in profile.iterdir()} == before
    assert not (repository / "state").exists() and not (repository / ".local").exists()


def test_old_subset_pool_reports_required_maintenance_without_cancellation(prepared, monkeypatch):
    repository, profile, args, _ = prepared
    def blocked(plan):
        return {"action": "reconfigure", "prepare_available": False, "members": {str(os.geteuid()): [A, B, C]},
            "reserve_mib": 512, "previous_configuration": {"members": {str(os.geteuid()): [A]}, "reserve_mib": 512},
            "dependencies": {"quiescent": False, "blockers": [{"reason": "run_depends_on_pool", "run_id": "existing"}]},
            "reason": "dependencies_active_or_unknown"}
    monkeypatch.setattr("hypoloom.managed_gpu_pool.review", blocked)
    result = bootstrap_inspect.inspect(args, repository)
    assert result["status"] == "blocked", result
    gpu = result["effective_configuration"]["gpu"]
    assert gpu["pool"]["membership_change"] == {"added": [B, C], "removed": [], "requires_maintenance": True}
    assert "launch_argv" not in result and args.pool_cancel_run is None
    assert "maintenance required" in " ".join(gpu_selection_summary(gpu))
    assert "旧池需要维护" in " ".join(gpu_selection_summary(gpu, "zh-CN"))


def test_gpu_mode_question_explains_default_without_an_extra_card_choice(prepared):
    repository, _, args, _ = prepared
    args.allow_gpu = None
    result = bootstrap_inspect.inspect(args, repository)
    rows = bootstrap_questions.questions(args, result)
    assert [field for field, _, _ in rows] == ["execution_mode"]
    assert "Manage all usable GPUs" in json.dumps(rows)
    assert "gpu_device_uuid" not in result["missing"] and "gpu_scheduling_mode" not in result["missing"]


def test_cpu_inspect_never_discovers_or_operates_gpu_resources(prepared, monkeypatch):
    repository, _, args, reports = prepared
    args.allow_gpu = False
    def forbidden(*a, **kw): raise AssertionError("CPU-only init must not inspect GPU devices or pools")
    monkeypatch.setattr(bootstrap_inspect, "_gpu_inventory", forbidden)
    monkeypatch.setattr(bootstrap_inspect, "_pools", forbidden)
    monkeypatch.setattr(bootstrap_gpu, "inventory", forbidden)
    result = bootstrap_inspect.inspect(args, repository)
    assert result["status"] == "ready_for_review", result
    assert not reports and not args.managed_pool
    assert "gpu" not in result["effective_configuration"]
    assert "--cpu-only" in result["launch_argv"] and "--managed-pool" not in result["launch_argv"]


def test_direct_launch_preview_gets_same_automatic_defaults(prepared):
    repository, profile, _, reports = prepared
    args = bootstrap.build_parser().parse_args(["launch", "topic", "--repository", str(repository),
        "--allow-gpu", "--auth-transport", "none", "--institutional-browser-mode", "off", "--run-id", "run", "--verifier-model", "exact-review-model"])
    report = admit_profile(profile, run_id="run", allow_gpu=True, verifier_model=args.verifier_model)
    commands = bootstrap.launch_arguments(args, repository, report)
    assert args.pool_device_uuid == [A, B, C] and args.gpu_device_uuid == [A]
    assert reports[-1].member == [f"{os.geteuid()}={A},{B},{C}"]
    # A preview can resolve defaults, but cannot supply Start now or silently
    # authorize the maintenance action that inspection must expose for review.
    assert not commands["pool_action_reviewed"]
    assert not (repository / ".local").exists() and not (repository / "state").exists()


def test_selection_capability_is_required_for_new_init_without_changing_legacy_validation():
    from hypoloom.gpu_pool import PoolError
    from hypoloom.gpu_pool_client import validate_pool_snapshot
    snapshot = {"devices": [{"gpu_uuid": A}], "capabilities": ["resource_replanning/1"]}
    assert validate_pool_snapshot(snapshot, aci_version=27) == (A,)
    with pytest.raises(PoolError, match="resource_selection/1"):
        validate_pool_snapshot(snapshot, aci_version=27, require_resource_selection=True)


@pytest.mark.parametrize("supported,quiescent,action", [
    (True, False, "reuse"), (False, True, "upgrade"), (False, False, "upgrade"),
])
def test_managed_init_requires_selection_capability_before_reusing_a_full_pool(
        tmp_path, monkeypatch, supported, quiescent, action):
    from hypoloom import managed_gpu_pool as managed
    root = tmp_path / ".local/gpu-pools/shared"
    plan = managed.PoolPlan(tmp_path, root, root / "control.sock",
        ((os.geteuid(), (A, B, C)),), 512)
    capabilities = ["resource_replanning/1"] + (["resource_selection/1"] if supported else [])
    snapshot = {"devices": [{"gpu_uuid": uuid, "pool_reserve_memory_mib": 512} for uuid in (A, B, C)],
        "capabilities": capabilities}
    monkeypatch.setattr(managed, "_unit_properties", lambda *a: {"LoadState": "loaded", "ActiveState": "active"})
    monkeypatch.setattr(managed, "_socket_state", lambda *a: "socket")
    monkeypatch.setattr(managed, "_probe_snapshot", lambda *a: ("live", snapshot))
    monkeypatch.setattr(managed, "_configuration", lambda *a: plan)
    monkeypatch.setattr(managed, "_no_conflicts", lambda *a: None)
    monkeypatch.setattr(managed, "inspect_dependencies", lambda *a, **kw: {"quiescent": quiescent})
    def forbidden(*a, **kw): raise AssertionError("pool review must never start or stop a service")
    monkeypatch.setattr(managed, "_launch_pool", forbidden)
    monkeypatch.setattr(managed, "_stop_unit", forbidden)
    result, target = managed._observe(plan, None, {})
    assert target == plan and result["action"] == action
    assert result["start_ready"] is supported
    assert result["prepare_available"] is (supported or quiescent)
    if not supported:
        assert result["reason"] == ("incompatible_pool" if quiescent else "dependencies_active_or_unknown")
    assert not root.exists()


@pytest.mark.parametrize("supported", [False, True])
def test_external_init_reports_selection_compatibility_without_operating_pool(devices, monkeypatch, supported):
    from hypoloom.gpu_pool_client import PoolClient
    calls = []
    def snapshot(self, operation, values):
        calls.append(operation)
        return {"devices": [{"gpu_uuid": A}],
            "capabilities": ["resource_replanning/1"] + (["resource_selection/1"] if supported else [])}
    monkeypatch.setattr(PoolClient, "request", snapshot)
    args = arguments(gpu_pool_socket="/external/pool.sock", gpu_pool_namespace="test")
    result = {"gpu": {}}
    bootstrap._external_pool_review(args, ComputeCapabilities(gpus=inventory().gpus[:1]), result)
    assert calls == ["snapshot"]
    assert result["gpu"]["pool"]["start_ready"] is supported
    if not supported:
        assert result["gpu"]["pool"]["code"] == "incompatible_pool"
        assert "resource_selection/1" in result["gpu"]["pool"]["reason"]
