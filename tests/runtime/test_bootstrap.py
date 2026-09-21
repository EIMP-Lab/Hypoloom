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


def test_launch_preview_generates_cli_compatible_cpu_vectors_without_effects(checkout, capsys):
    repo, overrides = checkout
    assert bootstrap.main(arguments(repo, overrides, "launch")) == 0
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
    assert not (repo / "state").exists() and not (repo / "profiles/local").exists()


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


def test_gpu_pool_choices_are_preserved_in_host_only_vectors(checkout, capsys):
    repo, overrides = checkout
    devices = ["GPU-00000000-0000-0000-0000-000000000001", "GPU-00000000-0000-0000-0000-000000000002"]
    argv = arguments(repo, [*overrides, "safety.gpus=[0,1]"], "launch")
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
