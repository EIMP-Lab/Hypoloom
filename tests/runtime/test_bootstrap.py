"""Exercise preparation and launch boundaries, not just the operator prose."""
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time

import pytest

from hypoloom import bootstrap
from hypoloom.cli import build_parser as cli_parser
from hypoloom.profile_admission import admit_profile

ROOT = Path(__file__).resolve().parents[2]


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def checkout(tmp_path):
    repo = tmp_path / "checkout"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / ".gitignore").write_text("/profiles/local/\n/.local/\n/state/\n")
    shutil.copytree(ROOT / "profiles/templates", repo / "profiles/templates")
    shutil.copytree(ROOT / "config", repo / "config")
    scripts = ".claude/skills/autoresearch/scripts"
    shutil.copytree(ROOT / scripts, repo / scripts)
    git(repo, "add", "profiles/templates")
    target = tmp_path / "target"
    target.mkdir()
    git(target, "init", "--quiet")
    (target / "README.md").write_text("test target\n")
    git(target, "add", "README.md")
    git(target, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--quiet", "-m", "fixture")
    data = tmp_path / "data"
    data.mkdir()
    overrides = [f"methodology.runtime.target_repo={target}", f"methodology.runtime.conda_env={sys.prefix}",
                 f"safety.allow_read_paths=[{data}]", "experts.defaults.model=exact-fixture-model"]
    return repo, overrides


def arguments(repo, overrides, action="prepare", template="multi-expert-zh"):
    args = [action, "topic", "--repository", str(repo), "--run-id", "topic-202609221010", "--cpu-only"]
    if template:
        args += ["--template", template]
    for value in overrides:
        args += ["--set", value]
    if action == "launch":
        args += ["--state-root", str(repo / "state/topic-202609221010"), "--auth-transport", "none"]
    if action == "inspect":
        args += ["--institutional-browser-mode", "off"]
    return args


@pytest.mark.parametrize("language", ["en", "zh"])
def test_filled_template_preview_equals_real_admission_without_source_writes(checkout, capsys, language):
    repo, overrides = checkout
    template = "multi-expert-" + language
    source = repo / "profiles/templates" / template
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    argv = arguments(repo, overrides, template=template)
    assert bootstrap.main(argv) == 0
    preview = json.loads(capsys.readouterr().out)
    destination = repo / "profiles/local/topic"
    assert not destination.parent.exists() and not (repo / "state").exists()
    assert bootstrap.main([*argv, "--write"]) == 0
    written = json.loads(capsys.readouterr().out)
    report = admit_profile(destination, run_id="topic-202609221010")
    assert report.admitted
    assert written["profile_written"]
    assert preview["admission"]["create_run_payload"] == report.create_run_payload
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in destination.iterdir())
    assert destination.stat().st_mode & 0o777 == 0o700
    assert before == {p.name: p.read_bytes() for p in source.iterdir()}
    assert not (repo / "state").exists()
    assert bootstrap.main([*argv, "--write"]) == 2
    assert "already exists" in capsys.readouterr().err


def test_unbound_template_is_preparable_but_not_launchable(checkout, capsys):
    repo, _ = checkout
    assert bootstrap.main(arguments(repo, [])) == 2
    assert not json.loads(capsys.readouterr().out)["admission"]["admitted"]
    assert bootstrap.main(arguments(repo, [], "launch")) == 2
    assert "commands" not in json.loads(capsys.readouterr().out)
    assert not (repo / "state").exists()
    assert bootstrap.main([*arguments(repo, []), "--write"]) == 2
    assert json.loads(capsys.readouterr().out)["profile_written"]


def test_existing_profile_is_read_only_and_rejects_overrides(checkout, capsys):
    repo, overrides = checkout
    assert bootstrap.main([*arguments(repo, overrides), "--write"]) == 0
    capsys.readouterr()
    assert bootstrap.main(arguments(repo, [], template=None)) == 0
    assert not json.loads(capsys.readouterr().out)["profile_written"]
    assert bootstrap.main(arguments(repo, ["experts.defaults.model=other"], template=None)) == 2
    assert "read-only" in capsys.readouterr().err


def test_preparation_rejects_unknown_fields_symlinks_and_unignored_destinations(checkout, capsys):
    repo, overrides = checkout
    assert bootstrap.main(arguments(repo, [*overrides, "methodology.unknown=1"])) == 2
    assert json.loads(capsys.readouterr().out)["admission"]["blockers"]
    source = repo / "profiles/templates/multi-expert-zh/goal.yaml"
    source.unlink()
    source.symlink_to(repo / "profiles/templates/multi-expert-en/goal.yaml")
    assert bootstrap.main(arguments(repo, overrides)) == 2
    assert "symlinks" in capsys.readouterr().err
    (repo / ".gitignore").write_text("")
    assert bootstrap.main(arguments(repo, overrides)) == 2
    assert "Git-ignored" in capsys.readouterr().err
    assert not (repo / "profiles/local").exists()


def test_paths_show_declared_private_location_without_values(checkout, tmp_path, capsys):
    repo, overrides = checkout
    private = tmp_path / "my-private.env"
    private.write_text("OPENALEX_API_KEY=never-echo-this\n")
    private.chmod(0o600)
    assert bootstrap.main([*arguments(repo, [*overrides, f"methodology.literature.credentials_file={private}"]), "--write"]) == 0
    capsys.readouterr()
    assert bootstrap.main(["paths", "--repository", str(repo), "--profile", "topic"]) == 0
    output = capsys.readouterr().out
    result = json.loads(output)
    assert not result["credentials"]["exists"]
    assert result["credentials_template"]["path"] == str(repo / "config/hypoloom.env.example")
    assert result["declared_credentials"]["methodology.yaml"]["mode"] == "0600"
    assert "never-echo-this" not in output
    assert not (repo / ".local").exists()


@pytest.mark.parametrize("name,content", [
    ("methodology.yaml", "literature: null\n"),
    ("notify.yaml", "sinks: []\n"),
    ("notify.yaml", "sinks:\n  telegram: null\n"),
])
def test_paths_reject_malformed_credential_sections_without_traceback(checkout, capsys, name, content):
    repo, _ = checkout
    destination = repo / "profiles/local/topic"
    destination.mkdir(parents=True)
    (destination / name).write_text(content)
    assert bootstrap.main(["paths", "--repository", str(repo), "--profile", "topic"]) == 2
    assert "YAML object" in json.loads(capsys.readouterr().err)["error"]


def test_malformed_literature_preview_keeps_admission_blockers(checkout, capsys):
    repo, overrides = checkout
    argv = [*arguments(repo, [*overrides, "methodology.literature=null"]),
            "--literature-adapter", "openalex"]
    assert bootstrap.main(argv) == 2
    result = json.loads(capsys.readouterr().out)
    assert not result["admission"]["admitted"]
    assert not result["literature_preflight"]["qualified"]
    assert not (repo / "profiles/local").exists() and not (repo / "state").exists()


def test_invoke_runs_in_selected_checkout(tmp_path):
    (tmp_path / "selected_checkout_module.py").write_text("value = 'selected checkout'\n")
    code, result = bootstrap._invoke([sys.executable, "-B", "-c",
        "import json, os, selected_checkout_module as m; print(json.dumps({'cwd': os.getcwd(), 'value': m.value}))"],
        {"PYTHONDONTWRITEBYTECODE": "1"}, cwd=tmp_path)
    assert code == 0 and result == {"cwd": str(tmp_path), "value": "selected checkout"}


def test_aggregate_inspect_is_compact_and_returns_executable_arguments(checkout, capsys, monkeypatch):
    from hypoloom import bootstrap_inspect
    repo, overrides = checkout
    assert bootstrap.main([*arguments(repo, overrides), "--write"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(bootstrap_inspect, "supervisor_check", lambda: {"status": "ready", "linger": "no"})
    argv = ["inspect", "topic", "--repository", str(repo), "--cpu-only", "--auth-transport", "none", "--offline",
            "--institutional-browser-mode", "off"]
    assert bootstrap.main(argv) == 0
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["status"] == "ready_for_review" and not result["missing"]
    assert result["profile_written"] is False and result["provider_probe_performed"] is False
    assert "create_run_payload" not in output and "candidate_files" not in output
    assert not (repo / "state").exists()
    assert len(output) < 12000
    prepared = result["launch_argv"]
    parsed = bootstrap.build_parser().parse_args(prepared[4:])
    assert parsed.run_id == result["identity"]["run_id"]
    assert parsed.request_id == parsed.run_id + "-create-1"
    assert parsed.summary and not parsed.start
    assert bootstrap.main(prepared[4:]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["admission"]["admitted"] and "commands" not in summary
    assert summary["effective_configuration"]["budget"] == result["effective_configuration"]["budget"]


def test_inspect_collects_missing_choices_without_default_gpu_authorization(checkout, capsys, monkeypatch):
    from hypoloom import bootstrap_inspect
    repo, _ = checkout
    monkeypatch.setattr(bootstrap_inspect, "supervisor_check", lambda: {"status": "ready"})
    monkeypatch.setattr(bootstrap_inspect, "_gpu_inventory", lambda *a: {"status": "observed", "devices": [{"uuid": "suggestion-only"}]})
    assert bootstrap.main(["inspect", "--repository", str(repo), "--auth-transport", "none", "--offline"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["missing"] == ["profile", "execution_mode", "institutional_browser_mode"]
    assert "launch_argv" not in result and not (repo / "state").exists()
    assert bootstrap.main(["inspect", "--repository", str(repo), "--start"]) == 2
    assert "read-only" in capsys.readouterr().err


@pytest.mark.parametrize("supplied_library", [False, True])
def test_inspect_requires_institutional_choice_and_reuses_identity(checkout, capsys, monkeypatch, supplied_library):
    from hypoloom import bootstrap_inspect
    repo, overrides = checkout
    assert bootstrap.main([*arguments(repo, overrides), "--write"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(bootstrap_inspect, "supervisor_check", lambda: {"status": "ready"})
    argv = ["inspect", "topic", "--repository", str(repo), "--cpu-only", "--auth-transport", "none", "--offline"]
    if supplied_library:
        argv += ["--institutional-library-url", "https://library.example.edu/"]
    assert bootstrap.main(argv) == 0
    pending = json.loads(capsys.readouterr().out)
    assert pending["status"] == "needs_input"
    assert pending["missing"] == ["institutional_browser_mode"]
    assert pending["next_actions"][0] == "complete_choices" and "launch_argv" not in pending
    choices = pending["choices"]["institutional_browser_mode"]
    assert {choice["value"] for choice in choices} == {"off", "local", "client", "server", "existing"}
    assert all(choice["description"] for choice in choices)
    followup = [*argv, "--institutional-browser-mode", "client" if supplied_library else "off"]
    for key, value in pending["identity"].items():
        followup += ["--" + key.replace("_", "-"), value]
    assert bootstrap.main(followup) == 0
    selected = json.loads(capsys.readouterr().out)
    assert selected["identity"] == pending["identity"] and not selected["missing"]
    assert selected["status"] == "ready_for_review" and "choices" not in selected
    if supplied_library:
        assert selected["effective_configuration"]["institutional_connection"]["library_url"] == "https://library.example.edu/"
    assert not (repo / "state").exists()


@pytest.mark.parametrize("mode", ["off", "local", "client", "server", "existing", "legacy"])
def test_inspect_institutional_choice_roundtrips_into_host_without_effects(checkout, capsys, monkeypatch, mode):
    from hypoloom import bootstrap_inspect, institutional_connection, institutional_runtime
    from hypoloom.institutional_options import build
    repo, overrides = checkout
    assert bootstrap.main([*arguments(repo, overrides), "--write"]) == 0
    capsys.readouterr()
    before = {p: p.read_bytes() for p in (repo / "profiles").rglob("*.yaml")}
    monkeypatch.setattr(bootstrap_inspect, "supervisor_check", lambda: {"status": "ready"})
    monkeypatch.setattr(institutional_connection, "_run", lambda *a, **k: pytest.fail("preview managed a process"))
    monkeypatch.setattr(institutional_connection, "probe", lambda *a, **k: pytest.fail("preview contacted browser"))
    monkeypatch.setattr(institutional_runtime.RunBrowser, "__init__", lambda *a, **k: pytest.fail("preview allocated a run browser"))
    choice = (["--institutional-browser-url", "http://127.0.0.1:19222"] if mode == "legacy"
              else ["--institutional-browser-mode", mode])
    argv = ["inspect", "topic", "--repository", str(repo), "--cpu-only", "--auth-transport", "none", "--offline", *choice]
    assert bootstrap.main(argv) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["status"] == "ready_for_review" and not inspected["missing"]
    policy = inspected["effective_configuration"]["institutional_connection"]
    assert inspected["checks"]["institutional_connection"] == policy
    assert policy["mode"] == ("existing" if mode == "legacy" else mode)
    launch = [v for v in inspected["launch_argv"][4:] if v != "--summary"]
    assert bootstrap.main(launch) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["effective_configuration"]["institutional_connection"] == policy
    host = result["commands"]["host_start"]
    serve = cli_parser().parse_args(host[host.index("--") + 4:])
    assert serve.institutional_browser_mode == (None if mode == "legacy" else mode)
    if mode in {"off", "client", "local", "server"}:
        assert serve.institutional_browser_url is None
    access = build(serve)
    if mode == "off":
        assert access is None
        assert policy == {"enabled": False, "mode": "off"}
        assert "institutional_preparation_argv" not in result["effective_configuration"]
    else:
        access.close()
    assert before == {p: p.read_bytes() for p in (repo / "profiles").rglob("*.yaml")}
    assert not (repo / "state").exists()


def test_generated_arguments_preserve_spaces_and_quotes_in_repository(checkout, capsys, monkeypatch):
    from hypoloom import bootstrap_inspect
    old, overrides = checkout
    repo = old.with_name("checkout's space")
    old.rename(repo)
    assert bootstrap.main([*arguments(repo, overrides), "--write"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(bootstrap_inspect, "supervisor_check", lambda: {"status": "ready"})
    assert bootstrap.main(["inspect", "topic", "--repository", str(repo), "--cpu-only", "--auth-transport", "none", "--offline",
        "--institutional-browser-mode", "off"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ready_for_review"
    argv = result["launch_argv"]
    assert argv[argv.index("--repository") + 1] == str(repo)
    assert bootstrap.main(argv[4:]) == 0
    assert json.loads(capsys.readouterr().out)["admission"]["admitted"]


def test_managed_inspect_preserves_full_pool_configuration_and_run_subset(checkout, capsys, monkeypatch):
    from hypoloom import bootstrap_inspect, managed_gpu_pool
    from hypoloom.compute_resources import ComputeCapabilities, GpuCapability, LocalComputeResources
    repo, overrides = checkout
    devices = ["GPU-00000000-0000-0000-0000-000000000001", "GPU-00000000-0000-0000-0000-000000000002"]
    assert bootstrap.main([*arguments(repo, [*overrides, "safety.gpus=auto",
        "methodology.budget.min_vram_per_experiment=1024"]), "--write"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(bootstrap_inspect, "supervisor_check", lambda: {"status": "ready"})
    monkeypatch.setattr(bootstrap_inspect, "_gpu_inventory", lambda *a: {"status": "observed", "devices": []})
    monkeypatch.setattr(bootstrap_inspect, "_pools", lambda *a: {"state": "legacy"})
    monkeypatch.setattr(LocalComputeResources, "snapshot", lambda self: ComputeCapabilities(gpus=(
        GpuCapability(index=1, uuid=devices[1], name="synthetic", total_memory_mib=24000,
                      free_memory_mib=12000, driver_version="synthetic"),)))
    seen = []
    def proposal(args):
        seen.append(args)
        return {"status": "ok", "action": "upgrade", "prepare_available": True,
            "start_ready": False, "members": {str(os.geteuid()): devices}, "reserve_mib": 789,
            "socket_path": args.socket_path}
    monkeypatch.setattr(managed_gpu_pool, "review", proposal)
    assert bootstrap.main(["inspect", "topic", "--repository", str(repo), "--allow-gpu", "--managed-pool",
        "--gpu-device-uuid", devices[1], "--auth-transport", "none", "--offline", "--institutional-browser-mode", "off"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ready_for_review"
    argv = result["launch_argv"]
    parsed = bootstrap.build_parser().parse_args(argv[4:])
    assert parsed.pool_action == "upgrade" and parsed.pool_device_uuid == devices and parsed.pool_reserve_mib == 789
    assert parsed.gpu_device_uuid == [devices[1]]
    assert parsed.gpu_pool_namespace == parsed.run_id and parsed.gpu_pool_server_uid == os.geteuid()
    assert seen[0].member == [f"{os.geteuid()}={devices[1]}"]
    assert bootstrap.main([value for value in argv[4:] if value != "--summary"]) == 0
    commands = json.loads(capsys.readouterr().out)["commands"]
    pool_args = managed_gpu_pool.build_parser().parse_args(commands["pool_prepare"][4:])
    assert pool_args.reviewed_action == "upgrade" and pool_args.reserve_mib == 789
    assert pool_args.member == [f"{os.geteuid()}=" + ",".join(devices)]
    assert commands["pool_action_reviewed"] and commands["pool_preflight_available"]
    host = commands["host_start"]
    assert host[host.index("--gpu-device-uuid") + 1] == devices[1]
    assert host.count("--gpu-device-uuid") == 1
    assert not (repo / "state").exists() and not (repo / ".local/gpu-pools").exists()


def test_inspect_reports_the_actual_control_interpreter(checkout, capsys, monkeypatch):
    from hypoloom import bootstrap_inspect
    repo, _ = checkout
    monkeypatch.setattr(bootstrap_inspect, "supervisor_check", lambda: {"status": "ready"})
    assert bootstrap.main(["inspect", "--repository", str(repo), "--cpu-only", "--auth-transport", "none",
        "--offline", "--control-python", "/other/python"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert {"code": "control_python_mismatch", "message": "Run inspect with the selected control Python"} in result["blockers"]
    assert result["control"]["python"] == str(Path(sys.executable).resolve())


def test_template_inspection_never_replays_overrides_in_generated_argv(checkout, capsys, monkeypatch):
    from hypoloom import bootstrap_inspect
    repo, overrides = checkout
    monkeypatch.setattr(bootstrap_inspect, "supervisor_check", lambda: {"status": "ready"})
    argv = arguments(repo, [*overrides, "methodology.literature.openalex_api_key=private-test-value"], "inspect")
    assert bootstrap.main([*argv, "--auth-transport", "none", "--offline"]) == 2
    rejected = capsys.readouterr()
    assert "fill credentials directly" in rejected.err and "private-test-value" not in rejected.err
    assert not rejected.out
    argv = arguments(repo, overrides, "inspect")
    assert bootstrap.main([*argv, "--auth-transport", "none", "--offline"]) == 0
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["status"] == "ready_for_review" and result["profile_preparation_required"]
    assert "launch_argv" not in result and "private-test-value" not in output
    assert not (repo / "profiles/local/topic").exists()


def test_compact_prepare_never_includes_profile_secret_or_raw_candidate(checkout, capsys):
    import yaml
    repo, overrides = checkout
    assert bootstrap.main([*arguments(repo, overrides), "--write"]) == 0
    capsys.readouterr()
    path = repo / "profiles/local/topic/methodology.yaml"
    value = yaml.safe_load(path.read_text())
    value.setdefault("literature", {})["openalex_api_key"] = "private-key-should-never-be-in-summary"
    path.write_text(yaml.safe_dump(value))
    assert bootstrap.main([*arguments(repo, [], template=None), "--summary"]) == 0
    raw = capsys.readouterr().out
    assert "private-key-should-never-be-in-summary" not in raw and "candidate_files" not in raw
    assert json.loads(raw)["profile_written"] is False


@pytest.mark.parametrize("prepared", [True, False])
def test_managed_bootstrap_prepares_pool_before_any_host_or_run(monkeypatch, prepared):
    names = ("profile_check", "pool_prepare", "pool_status", "host_start", "start", "status", "host_stop")
    commands = {name: [name] for name in names}
    commands.update(environment={}, working_directory=str(ROOT), pool_action_reviewed=True, pool_prepare_available=True)
    calls = []
    payload = {"run_id": "fresh"}
    def invoke(argv, *a, **kw):
        name = argv[0]; calls.append(name)
        if name == "profile_check": return 0, {"admitted": True, "create_run_payload": payload}
        if name == "pool_prepare": return (0, {"status": "upgraded"}) if prepared else (2, {"status": "pool_plan_changed"})
        if name == "host_start": return 0, {"status": "ready"}
        return 0, {"status": "accepted"}
    monkeypatch.setattr(bootstrap, "_invoke", invoke)
    result = bootstrap.start_fresh(commands, payload)
    assert result["status"] == ("started" if prepared else "pool_prepare_failed")
    assert [p["phase"] for p in result["phases"]] == calls
    assert calls[:2] == ["profile_check", "pool_prepare"]
    if prepared:
        assert calls.index("pool_prepare") < calls.index("host_start") < calls.index("start")
    else:
        assert "host_start" not in calls and "start" not in calls
        assert result["next_actions"] == ["recheck", "revise_selection", "prepare_only", "cancel"]


@pytest.mark.parametrize("institutional", [False, True])
def test_launch_preview_generates_cli_compatible_cpu_vectors_without_effects(checkout, capsys, institutional):
    repo, overrides = checkout
    argv = arguments(repo, overrides, "launch")
    if institutional:
        argv += ["--institutional-browser-url", "http://127.0.0.1:9222",
                 "--institutional-browser-ui-url", "http://127.0.0.1:5800",
                 "--institutional-library-url", "https://library.example.edu/"]
    assert bootstrap.main(argv) == 0
    result = json.loads(capsys.readouterr().out)
    commands = result["commands"]
    assert commands["working_directory"] == str(repo)
    parser = cli_parser()
    checks = parser.parse_args(commands["profile_check"][4:])
    start = parser.parse_args(commands["start"][4:])
    serve = parser.parse_args(commands["host_start"][commands["host_start"].index("--") + 4:])
    assert not checks.allow_gpu and not start.allow_gpu
    assert start.request_id == "topic-202609221010-create-1"
    assert checks.literature_adapter == start.literature_adapter == serve.literature_adapter == "offline"
    assert serve.exit_when_terminal and serve.agent_adapter == "fake"
    assert serve.institutional_browser_url == ("http://127.0.0.1:9222" if institutional else None)
    assert serve.institutional_browser_ui_url == ("http://127.0.0.1:5800" if institutional else None)
    assert serve.institutional_library_url == ("https://library.example.edu/" if institutional else None)
    assert not (repo / "state").exists() and not (repo / "profiles/local").exists()


def test_compact_review_preserves_executable_institutional_preparation(checkout, capsys):
    repo, overrides = checkout
    assert bootstrap.main([*arguments(repo, overrides, "launch"), "--summary",
        "--institutional-browser-url", "http://127.0.0.1:9222",
        "--institutional-provider-mode", "IEEE=public_only",
        "--institutional-acquisition-seconds", "90",
        "--institutional-manual-seconds", "300"]) == 0
    review = json.loads(capsys.readouterr().out)["effective_configuration"]
    settings = review["institutional_access"]
    assert settings["provider_modes"]["IEEE"] == "public_only"
    assert settings["acquisition_seconds"] == 90 and settings["manual_seconds"] == 300
    argv = review["institutional_preparation_argv"]
    assert argv[1:5] == ["-m", "hypoloom", "institutional", "prepare"]
    parsed = cli_parser().parse_args([*argv[3:], "--provider", "IEEE"])
    assert parsed.institutional_provider_mode == ["IEEE=public_only"]
    assert not (repo / "state").exists() and not (repo / "profiles/local").exists()


@pytest.mark.parametrize("selection,expected", [
    ([], ["IEEE", "ScienceDirect", "Springer"]),
    (["--institutional-prepare-provider", "Elsevier", "--institutional-prepare-provider", "Wiley"],
     ["ScienceDirect", "Wiley"]),
    (["--institutional-prepare-provider", "none"], []),
    (["--institutional-provider-mode", "ScienceDirect=public_only",
      "--institutional-provider-mode", "ACM=public_only"], ["IEEE", "Springer"]),
])
@pytest.mark.parametrize('failure_mode', ['review', 'public_only'])
def test_init_common_preparation_roundtrips_without_changing_research(checkout, capsys, monkeypatch, selection, expected, failure_mode):
    from hypoloom import bootstrap_inspect, institutional_runtime
    repo, overrides = checkout
    assert bootstrap.main([*arguments(repo, overrides), "--write"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(bootstrap_inspect, "supervisor_check", lambda: {"status": "ready"})
    monkeypatch.setattr(institutional_runtime.RunBrowser, "__init__", lambda *a, **k: pytest.fail("preview opened browser"))
    argv = ["inspect", "topic", "--repository", str(repo), "--control-python", sys.executable,
            "--auth-transport", "none", "--offline", "--cpu-only", "--institutional-browser-mode", "client",
            "--institutional-library-url", "https://library.example.edu/", *selection,
            "--institutional-prepare-failure-mode", failure_mode]
    assert bootstrap.main(argv) == 0
    review = json.loads(capsys.readouterr().out)
    plan = review["effective_configuration"]["institutional_preparation"]
    assert plan["selected"] == expected
    assert plan['failure_mode'] == failure_mode
    assert [p["provider"] for p in plan["catalog"][:4]] == ["IEEE", "ACM", "ScienceDirect", "Springer"]
    assert plan["catalog"][1]["recommended_prepare"] is False
    assert plan["library_url"] == "https://library.example.edu/"
    assert bootstrap.main([part for part in review["launch_argv"][4:] if part != '--summary']) == 0
    launch = json.loads(capsys.readouterr().out)
    assert launch["effective_configuration"]["institutional_preparation"] == plan
    host = launch['commands']['host_start']
    serve = cli_parser().parse_args(host[host.index('--') + 4:])
    assert serve.institutional_provider_mode == ([] if not selection or selection[0] != '--institutional-provider-mode'
                                               else ['ScienceDirect=public_only', 'ACM=public_only'])
    if expected:
        prepare = cli_parser().parse_args(review["effective_configuration"]["institutional_preparation_argv"][3:])
        assert prepare.provider == expected
        assert prepare.institutional_prepare_failure_mode == failure_mode
    else:
        assert "institutional_preparation_argv" not in review["effective_configuration"]
    assert not (repo / "state").exists()


@pytest.mark.parametrize("extra,message", [
    (["--institutional-browser-mode", "off", "--institutional-prepare-provider", "IEEE"], "enabled"),
    (["--institutional-browser-mode", "client", "--institutional-prepare-provider", "none",
      "--institutional-prepare-provider", "IEEE"], "none"),
    (["--institutional-browser-mode", "client",
      *[part for name in ("IEEE", "ACM", "ScienceDirect", "Springer", "Nature", "Wiley", "SAGE", "Oxford", "Taylor & Francis")
        for part in ("--institutional-prepare-provider", name)]], "eight"),
])
def test_preparation_conflicts_fail_before_resources(checkout, capsys, extra, message):
    repo, overrides = checkout
    assert bootstrap.main([*arguments(repo, overrides, "launch"), *extra]) == 2
    assert message in json.loads(capsys.readouterr().err)["error"]
    assert not (repo / "state").exists()


@pytest.mark.parametrize("status", ["started", "host_start_failed", "creation_uncertain"])
def test_only_confirmed_start_exposes_complete_monitor_commands(checkout, capsys, monkeypatch, status):
    import shlex
    repo, overrides = checkout
    assert bootstrap.main([*arguments(repo, overrides), "--write"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(bootstrap, "start_fresh", lambda *a: {"status": status})
    code = bootstrap.main([*arguments(repo, [], "launch", template=None), "--start", "--summary"])
    result = json.loads(capsys.readouterr().out)
    assert code == (0 if status == "started" else 2)
    assert ("monitor" in result) == (status == "started")
    if status == "started":
        command = shlex.split(result["monitor"]["cc_command"])
        assert command[:2] == ["/autoresearch-monitor", result["run_id"]]
        assert command[command.index("--state-root") + 1] == str(repo / "state" / result["run_id"])
        assert " && env " in result["monitor"]["terminal_command"]
    assert not (repo / "state").exists(), "printing monitor commands must not create an observer or state"


def test_server_browser_ui_requires_a_cdp_endpoint(checkout, capsys):
    repo, overrides = checkout
    assert bootstrap.main([*arguments(repo, overrides, "launch"),
        "--institutional-browser-ui-url", "http://127.0.0.1:5800"]) == 2
    assert "browser endpoint" in capsys.readouterr().err
    assert not (repo / "state").exists()


@pytest.mark.parametrize("mode", ["local", "server", "existing", "client"])
def test_connection_preview_roundtrips_without_starting_browser(checkout, capsys, monkeypatch, mode):
    from hypoloom import institutional_connection, bootstrap_inspect
    repo, overrides = checkout
    monkeypatch.setattr(institutional_connection, "_run", lambda *a, **k: pytest.fail("preview started a resource"))
    monkeypatch.setattr(institutional_connection, "probe", lambda *a, **k: pytest.fail("preview contacted browser"))
    argv = [*arguments(repo, overrides, "launch"), "--institutional-browser-mode", mode]
    assert bootstrap.main(argv) == 0
    result = json.loads(capsys.readouterr().out)
    review = result["effective_configuration"]
    assert "institutional_connect" not in result["commands"]
    command = review["institutional_connection_argv"]
    parsed = cli_parser().parse_args(command[4:])
    assert parsed.institutional_action == "connections"
    assert "--agent-session" in review["institutional_preparation_argv"]
    if mode == "client":
        entry = review["institutional_client_entry"]
        assert entry["setup_inputs"]["repository"] == str(repo)
        assert "-Action Setup" in entry["powershell"] and entry["runs_on"] == "Windows client"
        assert "Read-Host" not in entry["powershell"] and Path(entry["script_source"]).is_file()
        assert bootstrap.compact_result(result)["effective_configuration"]["institutional_client_entry"] == entry
    host = result["commands"]["host_start"]
    serve = cli_parser().parse_args(host[host.index("--") + 4:])
    assert serve.institutional_browser_mode == mode
    assert serve.institutional_port == 0
    selected = bootstrap.build_parser().parse_args(argv)
    forwarded = bootstrap_inspect._launch_argv(selected, repo)
    assert bootstrap.build_parser().parse_args(forwarded[4:]).institutional_browser_mode == mode
    assert not (repo / "state").exists() and not (repo / "profiles/local").exists()


@pytest.mark.parametrize("connected", [False, True])
def test_connection_failure_precedes_host_and_success_is_reported(monkeypatch, connected):
    calls = []
    def invoke(argv, *a, **k):
        name = argv[0]; calls.append(name)
        if name == "profile_check": return 0, {"admitted": True, "create_run_payload": {}}
        if name == "institutional_connect": return (0, {"status": "reused"}) if connected else (2, {"status": "unavailable"})
        if name == "host_start": return 0, {"status": "ready"}
        return 0, {"status": "accepted"}
    monkeypatch.setattr(bootstrap, "_invoke", invoke)
    commands = {k: [k] for k in ("profile_check", "institutional_connect", "host_start", "start", "status")}
    result = bootstrap.start_fresh({**commands, "environment": {}, "working_directory": "/tmp"}, {})
    assert result["status"] == ("started" if connected else "institutional_connection_failed")
    assert ("host_start" in calls) == connected
    if not connected: assert calls == ["profile_check", "institutional_connect"]
    assert bootstrap.compact_result({"launch": result})["launch"]["institutional_connection"] == result["institutional_connection"]


def test_relative_browser_path_survives_launch_working_directory_change(checkout, capsys, monkeypatch):
    repo, overrides = checkout
    monkeypatch.chdir(repo.parent)
    executable = repo.parent / "desktop-browser"
    executable.write_text("#!/bin/sh\nexit 0\n"); executable.chmod(0o700)
    assert bootstrap.main([*arguments(repo, overrides, "launch"), "--institutional-browser-mode", "local",
                           "--institutional-browser-executable", "./desktop-browser"]) == 0
    result = json.loads(capsys.readouterr().out)
    review = result["effective_configuration"]
    assert review["institutional_connection"]["executable"] == str(executable)
    assert result["commands"]["working_directory"] != str(Path.cwd())
    for name in ("institutional_preparation_argv",):
        command = review[name]
        assert command[command.index("--institutional-browser-executable") + 1] == str(executable)
    assert not (repo / "state").exists()


def test_real_missing_external_browser_does_not_prevent_host_owned_launch(checkout, capsys):
    import socket
    repo, overrides = checkout
    assert bootstrap.main([*arguments(repo, overrides), "--write"]) == 0
    capsys.readouterr()
    with socket.socket() as occupied:
        # A bound, non-listening fixture reserves a local port without serving CDP.
        occupied.bind(("127.0.0.1", 0))
        port = occupied.getsockname()[1]
        code = bootstrap.main([*arguments(repo, [], "launch", template=None), "--start",
            "--institutional-browser-mode", "existing", "--institutional-browser-url", f"http://127.0.0.1:{port}"])
    result = json.loads(capsys.readouterr().out)
    try:
        assert code == 0 and result["launch"]["status"] == "started", result
        assert "institutional_connect" not in [p["phase"] for p in result["launch"]["phases"]]
    finally:
        subprocess.run(result["commands"]["host_stop"], cwd=repo, capture_output=True, timeout=30)


def test_real_launch_preserves_profile_budgets_and_all_literature_bindings(checkout, tmp_path, capsys, monkeypatch):
    repo, overrides = checkout
    credentials = tmp_path / "private.env"
    credentials.write_text("OPENALEX_API_KEY=synthetic-openalex\nUNPAYWALL_EMAIL=test@example.invalid\n")
    credentials.chmod(0o600)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-model-secret")
    # Test native boundary separately in its existing suite; this case checks
    # the real argument projection, including the actual auth resolver process.
    monkeypatch.setattr("hypoloom.claude_agent_host.ClaudeAgentHost.preflight", lambda *a, **kw: None)
    overrides += [f"methodology.literature.credentials_file={credentials}", "methodology.budget.agent_invocation.max_cost=null"]
    argv = arguments(repo, overrides, "launch")
    argv[argv.index("--auth-transport") + 1] = "environment"
    argv += ["--claude-model", "exact-fixture-model", "--claude-executable", "/usr/bin/true",
             "--literature-adapter", "openalex", "--literature-pdf-fallback", "unpaywall",
             "--literature-max-pdf-pages", "37", "--web-search-fallback", "openalex"]
    assert bootstrap.main(argv) == 0
    output = capsys.readouterr().out
    result = json.loads(output)
    commands = result["commands"]
    parser = cli_parser()
    serve = parser.parse_args(commands["host_start"][commands["host_start"].index("--") + 4:])
    budget = result["admission"]["create_run_payload"]["research_contract"]["budget"]["agent_invocation"]
    assert serve.agent_max_turns == budget["max_turns"]
    assert serve.agent_max_cost_usd == "null" and budget["max_cost"] is None
    for vector in (commands["profile_check"], commands["start"]):
        parsed = parser.parse_args(vector[4:])
        assert parsed.credentials_file == serve.credentials_file == str(credentials)
        assert parsed.literature_pdf_fallback == serve.literature_pdf_fallback == "unpaywall"
        assert parsed.literature_max_pdf_pages == serve.literature_max_pdf_pages == 37
        assert "--web-search-fallback" not in vector
    assert "synthetic-model-secret" not in output and "synthetic-openalex" not in output
    assert result["provider_probe_performed"] is False


@pytest.mark.parametrize("case,expected", [
    ("normal", "started"), ("changed_before", "profile_changed_before_host"),
    ("changed_after", "profile_changed_before_create"), ("lost_reply_exists", "started"),
    ("lost_reply_absent", "create_failed"), ("lost_reply_unknown", "creation_uncertain"),
    ("created_status_unavailable", "started"), ("create_rejected", "create_failed"),
    ("host_failed", "host_start_failed"), ("host_uncertain", "host_start_uncertain"),
    ("pool_unavailable", "pool_unavailable")])
def test_managed_sequence_cleanup_and_create_reconciliation(monkeypatch, case, expected):
    commands = {name: [name] for name in ("profile_check", "host_start", "start", "status", "host_stop")}
    commands["environment"] = {"HYPOLOOM_RUNTIME_FAMILY": "hypoloom"}
    commands["working_directory"] = str(ROOT)
    if case == "pool_unavailable":
        commands["pool_status"] = ["pool_status"]
    calls = []
    payload = {"run_id": "reviewed"}
    def invoke(argv, environment, *, cwd):
        name = argv[0]
        calls.append(name)
        assert environment == commands["environment"]
        assert cwd == commands["working_directory"]
        if name == "profile_check":
            changed = case == "changed_before" or case == "changed_after" and len(calls) > 1
            return 0, {"admitted": True, "create_run_payload": {} if changed else payload}
        if name == "host_start":
            if case == "host_failed": return 2, {"status": "host_ready_timeout"}
            if case == "host_uncertain": return 3, {"status": "transport_error"}
            return 0, {"status": "ready"}
        if name == "host_stop": return 0, {"status": "stopped"}
        if name == "pool_status": return 2, {"status": "unavailable"}
        if name == "start":
            if case == "create_rejected": return 2, {"status": "rejected", "error": {"code": "invalid"}}
            return (3, {}) if case.startswith("lost_reply") else (0, {"status": "accepted"})
        if case in {"lost_reply_absent", "create_rejected"}: return 2, {"status": "rejected", "error": {"code": "not_found"}}
        if case in {"lost_reply_unknown", "created_status_unavailable"}: return 3, {"status": "transport_error"}
        return 0, {"status": "accepted"}
    monkeypatch.setattr(bootstrap, "_invoke", invoke)
    assert bootstrap.start_fresh(commands, payload)["status"] == expected
    assert calls.count("start") <= 1
    assert ("host_stop" in calls) == (case in {"changed_after", "lost_reply_absent", "create_rejected"})
    if case in {"host_failed", "host_uncertain"}:
        assert "start" not in calls
    if case == "normal":
        assert calls == ["profile_check", "host_start", "profile_check", "start", "status"]
    if case in {"changed_before", "pool_unavailable"}:
        assert "host_start" not in calls


@pytest.mark.parametrize("options", [
    ["--gpu-pool-socket", "/tmp/test.sock"],
    ["--gpu-device-uuid", "GPU-not-approved"],
    ["--ready-timeout", "nan"], ["--ready-timeout", "301"],
    ["--request-id", "../bad"], ["--claude-model", "model-with-fake-auth"],
])
def test_invalid_launch_choices_do_not_create_state(checkout, capsys, options):
    repo, overrides = checkout
    assert bootstrap.main([*arguments(repo, overrides, "launch"), *options]) == 2
    assert capsys.readouterr().err
    assert not (repo / "state").exists()


def test_launch_rejects_existing_state_and_runtime_conflict(checkout, capsys, monkeypatch):
    repo, overrides = checkout
    state = repo / "state/topic-202609221010"
    state.mkdir(parents=True)
    original = state.stat().st_mtime_ns
    assert bootstrap.main(arguments(repo, overrides, "launch")) == 2
    assert "absent absolute path" in capsys.readouterr().err
    assert state.stat().st_mtime_ns == original
    monkeypatch.setenv("HYPOLOOM_RUNTIME_FAMILY", "other")
    assert bootstrap.main(arguments(repo, overrides)) == 2
    assert "conflicting runtime" in capsys.readouterr().err


def test_gpu_pool_choices_are_preserved_in_host_only_vectors(checkout, capsys, monkeypatch):
    repo, overrides = checkout
    devices = ["GPU-00000000-0000-0000-0000-000000000001", "GPU-00000000-0000-0000-0000-000000000002"]
    from hypoloom.compute_resources import ComputeCapabilities, GpuCapability, LocalComputeResources
    from hypoloom.gpu_pool_client import PoolClient
    monkeypatch.setattr(PoolClient, "request", lambda self, operation, arguments: {
        "devices": [{"gpu_uuid": device} for device in devices], "capabilities": ["resource_replanning/1"]})
    monkeypatch.setattr(LocalComputeResources, "snapshot", lambda self: ComputeCapabilities(gpus=tuple(
        GpuCapability(index=i, uuid=device, name="synthetic", total_memory_mib=24000,
            free_memory_mib=12000, driver_version="synthetic") for i, device in enumerate(devices))))
    argv = arguments(repo, [*overrides, "safety.gpus=[0,1]", "methodology.budget.min_vram_per_experiment=1024"], "launch")
    argv[argv.index("--cpu-only")] = "--allow-gpu"
    argv += ["--gpu-runtime-prefix", sys.prefix, "--gpu-pool-socket", str(repo / "pool.sock"),
             "--gpu-pool-namespace", "topic", "--gpu-pool-server-uid", str(os.getuid())]
    for device in devices:
        argv += ["--gpu-device-uuid", device]
    assert bootstrap.main(argv) == 0
    commands = json.loads(capsys.readouterr().out)["commands"]
    serve = cli_parser().parse_args(commands["host_start"][commands["host_start"].index("--") + 4:])
    assert serve.gpu_device_uuid == devices and serve.gpu_pool_namespace == "topic"
    assert serve.gpu_pool_socket == str(repo / "pool.sock")
    assert commands["pool_status"][-2:] == ["--expected-server-uid", str(os.getuid())]
    for name in ("profile_check", "start"):
        assert "--allow-gpu" in commands[name]
        assert "--gpu-pool-socket" not in commands[name] and "--gpu-device-uuid" not in commands[name]
    assert not (repo / "state").exists()
    argv[argv.index("--gpu-pool-socket") + 1] = str(repo / ".local/gpu-pools/shared/control.sock")
    external = repo.parent / "external-state"
    argv[argv.index("--state-root") + 1] = str(external)
    assert bootstrap.main(argv) == 2
    assert "canonical managed pool" in capsys.readouterr().err and not external.exists()


def test_bootstrap_runs_actual_cli_host_and_cpu_to_clean_closure(checkout, tmp_path, capsys, monkeypatch):
    """Only the systemd transport is replaced; CLI, socket, Host and CPU are real."""
    from hypoloom.oracle import Oracle
    from hypoloom.runner import process_group_exists
    repo, _ = checkout
    shutil.copytree(ROOT / "tests/fixtures/profiles/component-demo", repo / "profiles/local/topic")
    state = repo / "state/topic-202609221010"
    host = None
    original_invoke = bootstrap._invoke
    log = (tmp_path / "host.log").open("w+")

    def invoke(argv, environment, *, cwd):
        nonlocal host
        assert Path(cwd) == repo
        if "manage_host.py" not in " ".join(argv):
            return original_invoke(argv, environment, cwd=cwd)
        assert argv[3] == "start", "successful creation must not roll back its Host"
        serve = argv[argv.index("--") + 1:]
        host = subprocess.Popen(serve, env={**os.environ, **environment}, cwd=ROOT,
            stdout=log, stderr=log, start_new_session=True)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if (state / "control.sock").is_socket():
                return 0, {"status": "ready", "supervisor": "test-process", "state_root": str(state)}
            assert host.poll() is None, "Host exited before readiness"
            time.sleep(0.02)
        raise AssertionError("Host did not become ready")

    monkeypatch.setattr(bootstrap, "_invoke", invoke)
    try:
        assert bootstrap.main([*arguments(repo, [], "launch", template=None), "--start"]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["launch"]["status"] == "started"
        assert host.wait(timeout=30) == 0
        assert not process_group_exists(host.pid)
        assert not (state / "control.sock").exists()
        report = Oracle(state / "state.sqlite", state / "artifacts").audit("topic-202609221010")
        assert report.ok, report.to_dict()
        assert report.open_attempt_count == report.pending_effect_count == 0
        import sqlite3
        with sqlite3.connect(f"file:{state / 'state.sqlite'}?mode=ro", uri=True) as connection:
            processes = connection.execute("SELECT terminal_reason,process_group_id FROM execution_attempts WHERE kind='PROCESS'").fetchall()
        assert processes and all(reason == "SUCCEEDED" and not process_group_exists(pgid) for reason, pgid in processes)
    finally:
        if host is not None and host.poll() is None:
            os.killpg(host.pid, signal.SIGTERM)
            host.wait(timeout=10)
        log.close()


def real_preview_arguments(checkout, tmp_path, environment):
    repo, overrides = checkout
    settings = tmp_path / 'agent-settings.json'
    settings.write_text(json.dumps({'env': environment}))
    settings.chmod(0o600)
    credentials = tmp_path / 'literature.env'
    credentials.write_text('OPENALEX_API_KEY=synthetic-key\n')
    credentials.chmod(0o600)
    argv = arguments(repo, [*overrides, f'methodology.literature.credentials_file={credentials}'], 'launch')
    argv[argv.index('--auth-transport') + 1] = 'settings'
    return [*argv, '--claude-settings', str(settings), '--claude-model', 'selected-host-model',
            '--claude-executable', '/usr/bin/true', '--literature-adapter', 'openalex']


def test_real_preview_exposes_profile_model_override_without_inheriting_session(checkout, tmp_path, capsys):
    argv = real_preview_arguments(checkout, tmp_path, {'ANTHROPIC_API_KEY': 'synthetic-private-key'})
    assert bootstrap.main(argv) == 0
    output = capsys.readouterr().out
    review = json.loads(output)['effective_configuration']['models']
    assert review['host_default_model'] == 'selected-host-model'
    assert review['profile_models_overriding_host_default'] == ['exact-fixture-model']
    assert all(row['model'] == 'exact-fixture-model' for row in review['routes'])
    assert all(row['sources']['model'] == 'experts.yaml.defaults.model' for row in review['routes'])
    assert 'synthetic-private-key' not in output
    assert not (checkout[0] / 'state').exists()


@pytest.mark.parametrize('extra', [{'ANTHROPIC_BASE_URL': 'not-a-url'}, {'CLAUDE_CODE_USE_VERTEX': '1'}])
def test_invalid_provider_configuration_stops_before_host(checkout, tmp_path, capsys, extra):
    argv = real_preview_arguments(checkout, tmp_path, {'ANTHROPIC_API_KEY': 'synthetic-private-key', **extra})
    assert bootstrap.main(argv) == 2
    error = capsys.readouterr().err
    assert 'not qualified' in error and 'synthetic-private-key' not in error
    assert not (checkout[0] / 'state').exists()


@pytest.mark.parametrize('kind', ['runtime', 'selector', 'no_pool', 'empty'])
def test_gpu_combination_is_rejected_in_preview(checkout, tmp_path, capsys, monkeypatch, kind):
    from hypoloom.compute_resources import ComputeCapabilities, GpuCapability, LocalComputeResources
    repo, overrides = checkout
    device = 'GPU-00000000-0000-0000-0000-000000000001'
    other = 'GPU-00000000-0000-0000-0000-000000000002'
    monkeypatch.setattr(LocalComputeResources, 'snapshot', lambda self: ComputeCapabilities(gpus=(
        GpuCapability(index=0, uuid=device, name='synthetic', total_memory_mib=24000,
                      free_memory_mib=12000, driver_version='synthetic'),)))
    additions = ['safety.gpus=auto']
    prefix = sys.prefix
    if kind == 'runtime':
        prefix = str(tmp_path / 'other-runtime')
        Path(prefix, 'bin').mkdir(parents=True)
        Path(prefix, 'bin/python').symlink_to(sys.executable)
    elif kind == 'selector':
        additions = [f'safety.gpus=[{other}]']
    elif kind == 'no_pool':
        additions += ['methodology.budget.min_vram_per_experiment=1024',
                      'methodology.budget.initial_vram_per_experiment=2048']
    elif kind == 'empty':
        additions = ['safety.gpus=[]']
    argv = arguments(repo, [*overrides, *additions], 'launch')
    argv[argv.index('--cpu-only')] = '--allow-gpu'
    assert bootstrap.main([*argv, '--gpu-device-uuid', device, '--gpu-runtime-prefix', prefix]) == 2
    expected = {'runtime': 'must match', 'selector': 'not in the Host-authorized',
                'no_pool': 'shared GPU pool', 'empty': 'safety.gpus=[]'}
    assert expected[kind] in capsys.readouterr().err
    assert not (repo / 'state').exists()


def test_managed_notifications_use_explicit_file_and_keep_secrets_out_of_commands(checkout, tmp_path, capsys, monkeypatch):
    repo, overrides = checkout
    settings = 'notify.sinks.telegram={enabled: true, bot_token_env: TEST_BOT, chat_id_env: TEST_CHAT}'
    argv = arguments(repo, [*overrides, settings], 'launch')
    monkeypatch.setenv('TEST_BOT', '123:ambient-secret')
    monkeypatch.setenv('TEST_CHAT', 'ambient-chat')
    assert bootstrap.main(argv) == 2
    assert 'does not inherit the interactive environment' in capsys.readouterr().err
    credentials = tmp_path / 'notifications.env'
    credentials.write_text('TEST_BOT=123:file-secret\nTEST_CHAT=private-recipient\nTEST_PROXY=http://127.0.0.1:12345\n')
    credentials.chmod(0o600)
    output = tmp_path / 'deliveries'
    assert bootstrap.main([*argv, '--notification-credentials-file', str(credentials),
        '--notification-proxy-env', 'TEST_PROXY', '--notification-output-root', str(output)]) == 0
    raw = capsys.readouterr().out
    result = json.loads(raw)
    command = result['commands']['host_start']
    for flag, value in (('--notification-credentials-file', str(credentials)),
                        ('--notification-proxy-env', 'TEST_PROXY'), ('--notification-output-root', str(output))):
        assert command[command.index(flag) + 1] == value
        assert flag not in result['commands']['profile_check'] and flag not in result['commands']['start']
    assert 'file-secret' not in raw and 'private-recipient' not in raw and 'ambient-secret' not in raw
    assert result['effective_configuration']['notifications']['enabled_sinks'] == ['log', 'telegram', 'dashboard']
    assert not output.exists() and not (repo / 'state').exists()


@pytest.mark.parametrize('override,message', [
    ('notify.sinks.telegram={enabled: true, credentials_file: /absent-init-fixture.env}', 'credentials file is unavailable'),
    ('notify.sinks.dashboard.host=0.0.0.0', 'host must be 127.0.0.1'),
])
def test_invalid_enabled_services_are_rejected_before_launch(checkout, capsys, override, message):
    repo, overrides = checkout
    assert bootstrap.main(arguments(repo, [*overrides, override], 'launch')) == 2
    assert message in capsys.readouterr().err
    assert not (repo / 'state').exists()
