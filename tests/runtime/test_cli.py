from __future__ import annotations

from pathlib import Path

import pytest

from hypoloom import cli
from hypoloom.claude_agent_host import CLAUDE_AGENT_HOST_VERSION
from hypoloom.workflow_host import installed_run_version


def test_web_fallback_requires_explicit_literature_and_reaches_host(tmp_path, monkeypatch):
    state_root = tmp_path / "state"
    with pytest.raises(SystemExit) as stopped:
        cli.main(("serve", "--state-root", str(state_root), "--web-search-fallback", "openalex"))
    assert stopped.value.code == 2 and not state_root.exists()
    key = tmp_path / "openalex.key"
    key.write_text("fixture-openalex-key")
    key.chmod(0o600)
    observed = {}
    def fake_serve(root, **options):
        observed.update(options)
        return 0
    monkeypatch.setattr("hypoloom.workflow_host.serve", fake_serve)
    assert cli.main(("serve", "--state-root", str(state_root),
        "--literature-adapter", "openalex", "--openalex-api-key-file", str(key),
        "--web-search-fallback", "openalex")) == 0
    web = observed["grounding_web"]
    assert web.search_fallback is observed["literature_io"].fetcher
    assert web.configuration["search_fallback"] == "openalex"
    assert "fixture-openalex-key" not in repr(web.configuration)
    assert not state_root.exists()


@pytest.mark.parametrize("fallback", [False, True])
def test_openalex_native_academic_client_is_independent_of_web_fallback(tmp_path, monkeypatch, fallback):
    from hypoloom.grounding_web import GroundingWebError
    key = tmp_path / "openalex.key"
    key.write_text("fixture-openalex-secret"); key.chmod(0o600)
    observed = {}
    monkeypatch.setattr("hypoloom.workflow_host.serve", lambda root, **options: observed.update(options) or 0)
    assert cli.main(("serve", "--state-root", str(tmp_path / "state"),
        "--literature-adapter", "openalex", "--openalex-api-key-file", str(key),
        *(("--web-search-fallback", "openalex") if fallback else ()))) == 0
    web = observed["grounding_web"]
    fetcher = observed["literature_io"].fetcher
    calls = []
    def academic(query, **options):
        calls.append((query, options))
        return {"provider": "openalex", "query": query, "search_scope": "academic_literature", "results": []}
    monkeypatch.setattr(fetcher, "web_search", academic)
    def unavailable(*args, **kwargs):
        raise GroundingWebError("timed_out", "fixture web timeout")
    monkeypatch.setattr(web, "_search_html", unavailable)
    assert web.web_search("method details", scope="academic")["provider"] == "openalex"
    assert len(calls) == 1
    if fallback:
        assert web.web_search("method details")["primary_error"]["code"] == "timed_out"
        assert len(calls) == 2
    else:
        with pytest.raises(GroundingWebError, match="timed_out"): web.web_search("method details")
        assert len(calls) == 1
    assert web.configuration["academic_provider"] == "openalex"
    assert web.configuration["search_fallback"] == ("openalex" if fallback else None)
    assert "fixture-openalex-secret" not in repr(web.configuration)


@pytest.mark.parametrize(
    "arguments",
    [
        ("--agent-adapter", "claude"),
        ("--claude-model", "exact-model"),
        ("--claude-bwrap", "/custom/bwrap"),
        ("--claude-bwrap", "/usr/bin/bwrap"),
        ("--agent-max-turns", "2"),
        ("--agent-max-turns", "3"),
        ("--agent-max-tokens", "20000"),
    ],
)
def test_serve_rejects_incomplete_or_cross_adapter_options(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    with pytest.raises(SystemExit) as stopped:
        cli.main(("serve", "--state-root", str(tmp_path / "state"), *arguments))
    assert stopped.value.code == 2
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize(
    "selector",
    [
        "default",
        "best",
        "sonnet",
        "opus",
        "haiku",
        "fable",
        "opusplan",
        "OPUS[1M]",
        " exact-model",
        "exact model",
        "[1m]",
        "exact-model[1m][1m]",
        "REPLACE_WITH_PINNED_MODEL",
    ],
)
def test_claude_model_requires_a_pinned_non_alias_selector(
    tmp_path: Path, selector: str
) -> None:
    with pytest.raises(SystemExit) as stopped:
        cli.main(
            (
                "serve",
                "--state-root",
                str(tmp_path / "state"),
                "--agent-adapter",
                "claude",
                "--claude-executable",
                "/bin/true",
                "--claude-settings",
                str(tmp_path / "settings.json"),
                "--claude-model",
                selector,
            )
        )
    assert stopped.value.code == 2
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("max_cost", ["0.02", "null"])
def test_claude_cli_builds_one_explicit_bounded_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, max_cost: str
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-key-must-not-win")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://ambient-gateway-must-not-win.invalid")
    settings = tmp_path / "settings.json"
    settings.write_text("{}\n", encoding="utf-8")
    observed: dict[str, object] = {}

    def fake_serve(state_root: Path, **options: object) -> int:
        observed.update(state_root=state_root, **options)
        return 17

    monkeypatch.setattr("hypoloom.workflow_host.serve", fake_serve)
    result = cli.main(
        (
            "serve",
            "--state-root",
            str(tmp_path / "state"),
            "--agent-adapter",
            "claude",
            "--claude-executable",
            "/bin/true",
            "--claude-settings",
            str(settings),
            "--claude-model",
            "exact-model-v1[1m]",
            "--agent-max-tokens",
            "321",
            "--agent-max-output-bytes",
            "4096",
            "--agent-wall-seconds",
            "4.5",
            "--agent-max-cost-usd",
            max_cost,
            "--exit-when-terminal",
        )
    )
    assert result == 17
    adapter = observed["claude_agent_host"]
    assert adapter is not None
    assert adapter.__class__.__module__ == "hypoloom.claude_agent_host"
    assert adapter.settings_path == settings
    assert CLAUDE_AGENT_HOST_VERSION == "claude-code-bwrap/62"
    assert observed["claude_model"] == "exact-model-v1[1m]"
    assert observed["provider_environment"] == {}
    assert observed["exit_when_terminal"] is True
    assert observed["literature_io"] is None
    assert observed["agent_budget"].to_dict() == {
        "max_turns": 3,
        "max_tokens": 321,
        "max_output_bytes": 4096,
        "wall_seconds": 4.5,
        "max_cost": None if max_cost == "null" else max_cost,
        "token_enforcement_mode": "enforce",
    }


def test_serve_builds_the_same_explicit_openalex_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "serve-openalex-secret"
    key_file = tmp_path / "openalex.key"
    key_file.write_text(f"{secret}\n", encoding="utf-8")
    key_file.chmod(0o600)
    observed: dict[str, object] = {}

    def fake_serve(state_root: Path, **options: object) -> int:
        observed.update(state_root=state_root, **options)
        return 23

    monkeypatch.setattr("hypoloom.workflow_host.serve", fake_serve)
    result = cli.main((
        "serve", "--state-root", str(tmp_path / "state"),
        "--literature-adapter", "openalex",
        "--openalex-api-key-file", str(key_file),
        "--literature-pdfinfo", "/bin/true",
        "--literature-pdftotext", "/bin/true",
        "--literature-max-pdf-pages", "19",
    ))

    assert result == 23
    literature_io = observed["literature_io"]
    assert literature_io.configuration["adapter"] == "openalex"
    assert literature_io.configuration["max_pdf_pages"] == 19
    assert secret not in repr(literature_io.configuration)


def test_current_new_write_identity_tracks_bounded_context() -> None:
    version = installed_run_version()

    assert version.research_contract_version == "hypoloom-research-v10"
    assert version.agent_aci_version == "24"
    assert CLAUDE_AGENT_HOST_VERSION == "claude-code-bwrap/62"


@pytest.mark.parametrize("base_url", [None, "https://approved-gateway.invalid"])
def test_claude_cli_without_settings_snapshots_only_selected_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    base_url,
) -> None:
    secret = "startup-api-key"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "must-not-pass")
    if base_url is None:
        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", base_url)
    observed: dict[str, object] = {}

    def fake_serve(state_root: Path, **options: object) -> int:
        observed.update(state_root=state_root, **options)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "changed-after-snapshot")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://changed-after-snapshot.invalid")
        assert options["provider_environment"] == {"ANTHROPIC_API_KEY": secret,
            **({"ANTHROPIC_BASE_URL": base_url} if base_url else {})}
        return 19

    monkeypatch.setattr("hypoloom.workflow_host.serve", fake_serve)
    result = cli.main(
        (
            "serve",
            "--state-root",
            str(tmp_path / "state"),
            "--agent-adapter",
            "claude",
            "--claude-executable",
            "/bin/true",
            "--claude-model",
            "exact-model-v1",
        )
    )

    assert result == 19
    assert observed["claude_agent_host"].settings_path is None
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err


def test_claude_cli_without_settings_requires_nonempty_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    with pytest.raises(SystemExit) as stopped:
        cli.main(
            (
                "serve",
                "--state-root",
                str(tmp_path / "state"),
                "--agent-adapter",
                "claude",
                "--claude-executable",
                "/bin/true",
                "--claude-model",
                "exact-model-v1",
            )
        )

    assert stopped.value.code == 2
    assert not (tmp_path / "state").exists()

    with pytest.raises(SystemExit) as empty_path_stopped:
        cli.main(
            (
                "serve",
                "--state-root",
                str(tmp_path / "other-state"),
                "--agent-adapter",
                "claude",
                "--claude-executable",
                "/bin/true",
                "--claude-settings",
                "",
                "--claude-model",
                "exact-model-v1",
            )
        )

    assert empty_path_stopped.value.code == 2
    assert not (tmp_path / "other-state").exists()


def test_serve_builds_an_explicit_allowlisted_gpu_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "gpu-runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin" / "python").write_bytes(b"python")
    (runtime / "bin" / "python").chmod(0o755)
    observed: dict[str, object] = {}

    def fake_serve(state_root: Path, **options: object) -> int:
        observed.update(state_root=state_root, **options)
        return 29

    monkeypatch.setattr("hypoloom.workflow_host.serve", fake_serve)
    result = cli.main((
        "serve", "--state-root", str(tmp_path / "state"),
        "--gpu-runtime-prefix", str(runtime),
        "--gpu-device-uuid", "GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "--gpu-device-uuid", "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    ))
    assert result == 29
    resources = observed["compute_resources"]
    assert resources.configuration["gpu_device_uuids"] == [
        "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    ]
    assert resources.configuration["gpu_runtime_prefix"] == str(runtime.resolve())


@pytest.mark.parametrize(
    "arguments",
    [
        ("--gpu-device-uuid", "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        ("--gpu-runtime-prefix", "/tmp/runtime"),
        ("--gpu-runtime-prefix", "/tmp/runtime", "--gpu-device-uuid", "0"),
        ("--gpu-runtime-prefix", "/tmp/runtime", "--gpu-device-uuid", "/dev/nvidia0"),
        ("--gpu-runtime-prefix", "/tmp/runtime", "--gpu-device-uuid", "all"),
    ],
)
def test_serve_rejects_incomplete_or_nonstable_gpu_authority(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    with pytest.raises(SystemExit) as stopped:
        cli.main(("serve", "--state-root", str(tmp_path / "state"), *arguments))
    assert stopped.value.code == 2


def test_only_profile_admission_exposes_fresh_run_creation() -> None:
    parser = cli.build_parser()
    subcommands = next(
        action.choices
        for action in parser._actions
        if isinstance(action, cli.argparse._SubParsersAction)
    )
    assert "start" in subcommands
    assert "create" not in subcommands


def test_backup_maps_exact_socket_payload_without_a_host_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    def fake_submit(args: object, command: str, payload: dict[str, object]) -> int:
        observed.update(args=args, command=command, payload=payload)
        return 23

    monkeypatch.setattr(cli, "_submit", fake_submit)
    result = cli.main((
        "backup", "--state-root", str(tmp_path / "state"),
        "--backup-root", str(tmp_path / "backups"), "--backup-id", "nightly-1",
    ))
    assert result == 23
    assert observed["command"] == "BACKUP"
    assert observed["payload"] == {"backup_root": str(tmp_path / "backups"), "backup_id": "nightly-1"}
    assert observed["args"].expected_revision is None


@pytest.mark.parametrize("verb", ["pause", "resume", "inject"])
def test_participant_controls_only_submit_scoped_operator_commands(tmp_path, monkeypatch, verb):
    observed = {}
    def submit(args, command, payload):
        observed.update(command=command, payload=payload, revision=args.expected_revision)
        return 19
    monkeypatch.setattr(cli, "_submit", submit)
    arguments = [verb, "--state-root", str(tmp_path / "state"), "--run-id", "run-1",
                 "--expert", "ml-researcher", "--component", "model", "--chain", "branch-2",
                 "--expected-revision", "9"]
    if verb == "inject": arguments.append("请先复核数据泄漏风险")
    assert cli.main(arguments) == 19
    assert observed == {"command": verb.upper(), "revision": 9, "payload": {
        "run_id": "run-1", "scope": {"role": "ml-researcher", "component_id": "model", "chain": "branch-2"},
        "reason": "operator", **({"text": "请先复核数据泄漏风险"} if verb == "inject" else {})}}
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("arguments", [["pause", "--chain", "2"], ["resume", "--component", "model"],
                                       ["inject", "text"]])
def test_participant_cli_rejects_ambiguous_scope_before_contacting_host(tmp_path, arguments):
    with pytest.raises(SystemExit) as stopped:
        cli.main([*arguments, "--state-root", str(tmp_path / "state"), "--run-id", "run-1"])
    assert stopped.value.code == 2
    assert not (tmp_path / "state").exists()


def test_managed_notification_file_reaches_services_without_ambient_secret_fallback(tmp_path, monkeypatch, capsys):
    from hypoloom.notification_tailer import notification_remote, notification_preflight
    private = tmp_path / 'notifications.env'
    private.write_text('APP_BOT=123:selected-file-token\nAPP_CHAT=chosen-recipient\nAPP_PROXY=http://127.0.0.1:9911\n')
    private.chmod(0o600)
    monkeypatch.setenv('APP_BOT', '999:ambient-token')
    monkeypatch.setenv('APP_CHAT', 'ambient-recipient')
    observed = {}
    def check(root, **options):
        services = options['services_factory']()
        observed['environment'] = services.environ
        observed['proxy'] = services.proxy_env
        config = {'enabled': True, 'bot_token_env': 'APP_BOT', 'chat_id_env': 'APP_CHAT'}
        url, headers, recipient = notification_remote('telegram', config, services.environ)
        assert url.endswith('123:selected-file-token/sendMessage') and recipient == 'chosen-recipient'
        notification_preflight({'sinks': {'telegram': config}}, state_root=root,
            output_root=services.output_root, environ=services.environ, proxy_env=services.proxy_env)
        return 0
    monkeypatch.setattr('hypoloom.workflow_host.serve', check)
    state = tmp_path / 'state'
    assert cli.main(['serve', '--state-root', str(state), '--notification-credentials-file', str(private),
        '--notification-proxy-env', 'APP_PROXY']) == 0
    assert observed['proxy'] == 'APP_PROXY'
    assert observed['environment']['APP_BOT'] == '123:selected-file-token'
    assert not state.exists()
    assert 'selected-file-token' not in capsys.readouterr().out
