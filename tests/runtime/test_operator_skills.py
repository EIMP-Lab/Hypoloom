from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
SKILLS = REPOSITORY / ".claude" / "skills"
MODEL_RESOLVER = (
    SKILLS / "autoresearch" / "scripts" / "resolve_model_options.py"
)
MONITOR_LAUNCHER = (
    SKILLS / "autoresearch" / "scripts" / "launch_monitor.py"
)


def _skill(name: str) -> str:
    path = SKILLS / name / "SKILL.md"
    assert path.is_file(), path
    return path.read_text(encoding="utf-8")


def _init_contract() -> str:
    text = _skill("autoresearch")
    return text.split("## Bootstrap init", 1)[1].split("## Supported routing", 1)[0]


def _normalized(text: str) -> str:
    return " ".join(text.split())


def test_init_explains_private_settings_and_explicit_restore_without_old_run_rebind():
    text = _normalized(_skill("autoresearch"))
    for expected in ("<state-root>/host/claude-settings.json", "RestartPreventExitStatus=2 78",
                     "Restarts reuse this private input", "recreate a missing copy in an existing run",
                     "Backups and support bundles do not contain", "restore --claude-settings",
                     "rejects incorrect bytes before recovery"):
        assert expected in text


def test_native_init_detects_and_deploys_project_pool_with_reviewed_binding(tmp_path) -> None:
    from hypoloom.cli import build_parser

    text = _init_contract()
    normalized = _normalized(text)
    for option in ("--gpu-pool-socket", "--gpu-pool-namespace", "--gpu-pool-server-uid",
                   "gpu-pool-status", "--expected-server-uid"):
        assert option in text
    assert "**Shared pool.**" in text
    assert "unavailable pool blocks `Start now`" in normalized
    assert "partial own-use samples remain unknown" in normalized
    for marker in (
        "manage_gpu_pool.py",
        "status --repository",
        "ensure --repository",
        ".local/gpu-pools/",
        "hypoloom-gpu-pool@",
        "`ready` or `reused`",
    ):
        assert marker in text
    for statement in (
        "This probe is read-only",
        "never infer a prior run's participation from socket absence",
        "candidate values, not authorization",
        "foreign_pool_root",
        "service_busy",
        "pool_conflict",
        "gpu_vram_reserve_mb",
        "separate operator actions outside init",
        "Different private pools must not coordinate the same shared device set independently",
    ):
        assert statement in normalized
    parser = build_parser()
    socket_path = str(tmp_path / "operator pool" / "capacity.sock")
    status = parser.parse_args(("gpu-pool-status", "--socket-path", socket_path, "--expected-server-uid", "123"))
    serve = parser.parse_args(("serve", "--state-root", str(tmp_path / "prospective-state"),
        "--gpu-pool-socket", socket_path, "--gpu-pool-namespace", "private-root-a", "--gpu-pool-server-uid", "123"))
    assert status.socket_path == serve.gpu_pool_socket == socket_path
    assert status.expected_server_uid == serve.gpu_pool_server_uid == 123
    assert serve.gpu_pool_namespace == "private-root-a"
    assert not tmp_path.joinpath("prospective-state").exists()
    assert not tmp_path.joinpath("operator pool").exists()


def _assert_ordered(text: str, *markers: str) -> None:
    positions = [text.index(marker) for marker in markers]
    assert positions == sorted(positions), tuple(zip(markers, positions, strict=True))


def _run_model_resolver(
    *arguments: str, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (sys.executable, str(MODEL_RESOLVER), *arguments),
        cwd=REPOSITORY,
        env={
            "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": "C.UTF-8",
            **(environment or {}),
        },
        capture_output=True,
        text=True,
        check=False,
    )


def _write_settings(path: Path, value: object, *, mode: int = 0o600) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(mode)


def test_model_resolver_reads_only_selected_settings_and_redacts_secrets(
    tmp_path: Path,
) -> None:
    settings = tmp_path / "settings.json"
    _write_settings(
        settings,
        {
            "model": "haiku",
            "env": {
                "ANTHROPIC_AUTH_TOKEN": "settings-token-must-not-leak",
                "ANTHROPIC_BASE_URL": "https://settings-endpoint-must-not-leak.invalid",
                "ANTHROPIC_MODEL": "opus",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "mimo-v2.5",
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "mimo-v2.5-pro[1m]",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "mimo-v2.5",
                "ANTHROPIC_DEFAULT_FABLE_MODEL": "fable-provider[1m]",
                "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME": "private-friendly-name",
                "UNRELATED_SECRET": "unrelated-value-must-not-leak",
            },
        },
    )

    completed = _run_model_resolver(
        "--settings",
        str(settings),
        environment={
            "ANTHROPIC_API_KEY": "ambient-key-must-not-win",
            "ANTHROPIC_MODEL": "sonnet",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "ambient-model-must-not-win",
        },
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    result = json.loads(completed.stdout)
    assert set(result) == {
        "auth_source",
        "auth_kind",
        "configured_default",
        "configured_default_error",
        "model_options",
        "invalid_logical_selectors",
    }
    assert result == {
        "auth_source": "settings",
        "auth_kind": "gateway",
        "configured_default": {
            "logical_selector": "opus",
            "claude_model": "mimo-v2.5-pro[1m]",
        },
        "configured_default_error": None,
        "model_options": [
            {
                "logical_selector": "sonnet",
                "claude_model": "mimo-v2.5",
            },
            {
                "logical_selector": "opus",
                "claude_model": "mimo-v2.5-pro[1m]",
            },
            {
                "logical_selector": "haiku",
                "claude_model": "mimo-v2.5",
            },
            {
                "logical_selector": "fable",
                "claude_model": "fable-provider[1m]",
            },
        ],
        "invalid_logical_selectors": [],
    }
    rendered = completed.stdout + completed.stderr
    for forbidden in (
        "settings-token-must-not-leak",
        "settings-endpoint-must-not-leak",
        "private-friendly-name",
        "unrelated-value-must-not-leak",
        "ambient-key-must-not-win",
        "ambient-model-must-not-win",
    ):
        assert forbidden not in rendered


def test_model_resolver_environment_mode_never_borrows_user_settings(
    tmp_path: Path,
) -> None:
    config = tmp_path / ".claude"
    config.mkdir()
    settings = config / "settings.json"
    _write_settings(
        settings,
        {
            "env": {
                "ANTHROPIC_AUTH_TOKEN": "settings-token-must-not-leak",
                "ANTHROPIC_BASE_URL": "https://settings-endpoint.invalid",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "settings-model-must-not-win",
            }
        },
    )

    completed = _run_model_resolver(
        "--environment",
        environment={
            "HOME": str(tmp_path),
            "CLAUDE_CONFIG_DIR": str(config),
            "ANTHROPIC_API_KEY": "environment-key-must-not-leak",
            "ANTHROPIC_MODEL": "opus",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "environment-sonnet",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "environment-opus[1m]",
            "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME": "ignored-environment-name",
        },
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "auth_source": "environment",
        "auth_kind": "api_key",
        "configured_default": {
            "logical_selector": "opus",
            "claude_model": "environment-opus[1m]",
        },
        "configured_default_error": None,
        "model_options": [
            {
                "logical_selector": "sonnet",
                "claude_model": "environment-sonnet",
            },
            {
                "logical_selector": "opus",
                "claude_model": "environment-opus[1m]",
            },
        ],
        "invalid_logical_selectors": [],
    }
    rendered = completed.stdout + completed.stderr
    for forbidden in (
        "environment-key-must-not-leak",
        "settings-token-must-not-leak",
        "settings-endpoint.invalid",
        "settings-model-must-not-win",
        "ignored-environment-name",
    ):
        assert forbidden not in rendered

    unavailable = _run_model_resolver(
        "--environment",
        environment={
            "HOME": str(tmp_path),
            "CLAUDE_CONFIG_DIR": str(config),
            "ANTHROPIC_AUTH_TOKEN": "ambient-token-is-not-environment-auth",
            "ANTHROPIC_BASE_URL": "https://ambient-endpoint.invalid",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "must-not-be-reported",
        },
    )
    assert unavailable.returncode == 2
    assert unavailable.stdout == ""
    assert json.loads(unavailable.stderr) == {
        "error": "environment_auth_unavailable"
    }
    assert "must-not-be-reported" not in unavailable.stderr


def test_model_resolver_rejects_unsafe_or_unauthenticated_settings(
    tmp_path: Path,
) -> None:
    valid = tmp_path / "valid.json"
    _write_settings(valid, {"env": {"ANTHROPIC_API_KEY": "secret"}})
    symlink = tmp_path / "symlink.json"
    symlink.symlink_to(valid)
    wrong_mode = tmp_path / "wrong-mode.json"
    _write_settings(
        wrong_mode, {"env": {"ANTHROPIC_API_KEY": "secret"}}, mode=0o640
    )
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    malformed.chmod(0o600)
    non_object = tmp_path / "non-object.json"
    _write_settings(non_object, [])
    unauthenticated = tmp_path / "unauthenticated.json"
    _write_settings(unauthenticated, {"env": {}})

    cases = (
        ("relative.json", "settings_path_not_absolute"),
        (str(symlink), "settings_not_regular_nonsymlink_0600"),
        (str(wrong_mode), "settings_not_regular_nonsymlink_0600"),
        (str(malformed), "settings_invalid_json_object"),
        (str(non_object), "settings_invalid_json_object"),
        (str(unauthenticated), "settings_auth_unavailable"),
    )
    for path, error in cases:
        completed = _run_model_resolver("--settings", path)
        assert completed.returncode == 2
        assert completed.stdout == ""
        assert json.loads(completed.stderr) == {"error": error}
        assert "secret" not in completed.stderr

    for name in (
        "settings.json",
        "settings.local.json",
        "settings.example.json",
    ):
        forbidden = _run_model_resolver(
            "--settings", str(REPOSITORY / ".claude" / name)
        )
        assert forbidden.returncode == 2
        assert forbidden.stdout == ""
        assert json.loads(forbidden.stderr) == {
            "error": "settings_repository_interactive_forbidden"
        }
        assert str(REPOSITORY) not in forbidden.stderr


def test_model_resolver_reports_only_valid_allowlisted_model_mappings(
    tmp_path: Path,
) -> None:
    settings = tmp_path / "settings.json"
    _write_settings(
        settings,
        {
            "model": "sonnet",
            "env": {
                "ANTHROPIC_API_KEY": "secret-must-not-leak",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "sonnet",
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "provider[1m][1m]",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": 7,
                "ANTHROPIC_DEFAULT_FABLE_MODEL": "valid-fable[1m]",
            },
        },
    )

    completed = _run_model_resolver("--settings", str(settings))

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "auth_source": "settings",
        "auth_kind": "api_key",
        "configured_default": None,
        "configured_default_error": "unresolved_logical_selector",
        "model_options": [
            {
                "logical_selector": "fable",
                "claude_model": "valid-fable[1m]",
            }
        ],
        "invalid_logical_selectors": ["sonnet", "opus", "haiku"],
    }
    assert "secret-must-not-leak" not in completed.stdout + completed.stderr


def test_model_resolver_exposes_an_exact_configured_default_without_relabeling_it(
    tmp_path: Path,
) -> None:
    settings = tmp_path / "settings.json"
    _write_settings(
        settings,
        {
            "model": "standalone-provider[1m]",
            "env": {
                "ANTHROPIC_AUTH_TOKEN": "token-must-not-leak",
                "ANTHROPIC_BASE_URL": "https://endpoint-must-not-leak.invalid",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "mapped-sonnet",
            },
        },
    )

    completed = _run_model_resolver("--settings", str(settings))

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["configured_default"] == {
        "logical_selector": None,
        "claude_model": "standalone-provider[1m]",
    }
    assert result["configured_default_error"] is None
    assert result["model_options"] == [
        {
            "logical_selector": "sonnet",
            "claude_model": "mapped-sonnet",
        }
    ]
    assert "token-must-not-leak" not in completed.stdout + completed.stderr
    assert "endpoint-must-not-leak" not in completed.stdout + completed.stderr

    inherited = _run_model_resolver(
        "--environment",
        environment={
            "ANTHROPIC_API_KEY": "environment-key-must-not-leak",
            "ANTHROPIC_MODEL": "environment-standalone[1m]",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "mapped-environment-sonnet",
        },
    )
    assert inherited.returncode == 0, inherited.stderr
    inherited_result = json.loads(inherited.stdout)
    assert inherited_result["configured_default"] == {
        "logical_selector": None,
        "claude_model": "environment-standalone[1m]",
    }
    assert inherited_result["configured_default_error"] is None
    assert "environment-key-must-not-leak" not in inherited.stdout + inherited.stderr


def test_public_skill_uses_the_same_source_model_resolver_contract() -> None:
    text = _init_contract()

    assert "resolve_model_options.py" in text
    assert "--environment" in text
    assert "--settings" in text
    for field in (
        "logical_selector",
        "claude_model",
    ):
        assert field in text
    assert "provider_model_identity" not in text
    assert "[1m]" in text


def test_public_operator_skill_is_hypoloom_and_names_exact_cli_surface() -> None:
    text = _skill("autoresearch")
    normalized = _normalized(text)

    assert text.startswith("---\nname: autoresearch\n")
    assert "HYPOLOOM_RUNTIME_FAMILY=hypoloom" in text
    assert "-m hypoloom.cli" in text
    assert "An absent value is allowed for every operation" in normalized
    assert "A nonempty value other than exactly `hypoloom` stops every operation" in normalized
    assert "environment of every Python helper" in normalized
    assert "same Claude interaction" in normalized
    assert "force a relaunch" in normalized
    for operation in (
        "init",
        "profile-check",
        "start",
        "status",
        "monitor",
        "debug",
        "oracle",
        "cancel",
        "backup",
        "restore",
    ):
        assert f"`{operation}" in text or f" {operation} " in text
    assert "CREATE_RUN" in text
    assert "--expected-revision" in text
    assert "--backup-path" in text
    assert "--allow-gpu" in text


def test_public_skill_fails_closed_on_profile_semantics_and_legacy_fallback() -> None:
    text = _skill("autoresearch")

    assert "Real topics are copied and reviewed\nunder the ignored `profiles/local/`" in text
    assert "exact target/data/model/cost/\nGPU" in text
    assert "create_run_payload: null" in text
    assert "fails admission remains a hard stop" in text
    assert "Never fall back to another runtime" in text
    assert "never spawn an implicit Host" in text
    assert "There is no separate `drive` command" in text


def test_public_skill_init_qualifies_the_expert_loop_profile_and_literature_wire() -> None:
    text = _init_contract()
    normalized = _normalized(text)

    assert "`methodology.yaml.schema_version: 1`" in text
    assert "legacy three-command Agent path" in normalized
    assert "Schema v2 remains a compatible legacy six-file expert-loop source" in normalized
    assert "Product development should use schema v4" in normalized
    assert "profiles/templates/multi-expert-en" in normalized
    assert "profiles/templates/multi-expert-zh" in normalized
    assert "Older parser examples under `tests/fixtures/profiles/` are test inputs only" in normalized
    assert "`mode.research_loop: expert-six-node/1`" in text
    assert "requires the explicit `openalex` literature adapter" in normalized
    assert "absolute regular, non-symlink mode-`0600` dotenv credentials file" in normalized
    assert "`literature.credentials_file`" in text
    assert "Never create, copy, chmod, or edit that credential file" in normalized
    for argument in (
        "--literature-adapter openalex",
        "--credentials-file",
        "--literature-pdfinfo",
        "--literature-pdftotext",
        "--literature-timeout",
        "--literature-max-response-bytes",
        "--literature-max-pdf-bytes",
        "--literature-max-pdf-pages",
    ):
        assert argument in text
    assert "same exact reviewed literature argument vector" in normalized
    assert "`serve`, both `profile-check` calls, and `start`" in normalized
    assert "Do not fall back to `offline`" in normalized


def test_public_skill_creates_fresh_v4_profiles_without_rewriting_existing_sources() -> None:
    text = _normalized(_init_contract())
    for requirement in (
        "new ignored `profiles/local/<new-safe-name>` destination",
        "Never modify the source profile in place",
        "exact topic, objective, completion signals, roles, runtime",
        "admission validator accepts the in-memory candidate",
        "Do not offer automatic format migration",
    ):
        assert requirement in text


def test_public_skill_states_the_qualified_expert_design_node_boundary() -> None:
    text = _init_contract()
    normalized = _normalized(text)

    assert "`agent-full-cc/1`" in text
    assert "`Read/Write/Edit/Glob/Grep/Bash`" in text
    assert "Host-owned chain worktree" in normalized
    assert "resume their persistent chain session" in normalized
    assert "scoped Host MCP endpoint" in normalized
    assert "Host still validates/admits candidates" in normalized
    assert "Provider credentials remain outside the sandbox" in normalized
    assert "General Agent Web" in normalized
    assert "not permitted by the current capability policy" in normalized
    assert "Bash, MCP, nested Agent, plugin, marketplace, and session persistence remain unavailable" not in normalized
    assert "schema-v1 single-file DRAFT canary requires `max_turns >= 4`" in normalized
    assert "schema-v2/v3 multi-file Git-worktree design canary requires `max_turns >= 24`" in normalized


def test_public_skill_keeps_init_effect_free_until_one_final_choice() -> None:
    text = _init_contract()
    before_choice = text.split("Use one final `AskUserQuestion`", 1)[0]
    normalized = _normalized(before_choice)

    assert "`/autoresearch init` has no corresponding `hypoloom.cli init`" in text
    assert "AskUserQuestion" in text
    assert "Before the user's final confirmation, perform **zero writes**" in text
    assert "No Host, Agent, service, scope, or run exists" in normalized
    assert "manage_host.py" not in before_choice
    assert "check-ignore --no-index" in text
    assert "Do not create `.env`, a launcher, manifest, config, marker, receipt, digest" in _normalized(text)
    assert "must never generate a scratch Python or shell launcher" in _normalized(text)
    assert "`.claude/skills/autoresearch/` tree" in text
    assert text.count("Use one final `AskUserQuestion`") == 1
    assert "**Start now (Recommended)**" in text
    assert "**Prepare only**" in text
    assert "Rejection or cancellation stops with zero bootstrap writes" in _normalized(text)


def test_public_skill_init_can_adopt_local_profile_or_copy_tracked_template() -> None:
    text = _init_contract()

    assert "<repo>/profiles/templates/<template-safe-name>" in text
    assert "<repo>/profiles/local/<profile-safe-name>" in text
    assert "<repo>/state/<profile-safe-name>-<YYYYMMDDHHMM>" in text
    assert "adopt" in text.lower()
    assert "existing" in text.lower()
    assert "copy" in text.lower()
    assert "tracked template" in text.lower()
    assert "[A-Za-z0-9][A-Za-z0-9._-]{0,127}" in text
    for filename in (
        "goal.md",
        "experts.yaml",
        "methodology.yaml",
        "language.md",
        "notify.yaml",
        "safety.yaml",
    ):
        assert filename in text
    # Actual exclusivity, modes and preview/admission equality are exercised in
    # test_bootstrap.py, rather than requiring a hand-written shell copy loop.
    assert "bootstrap prepare" in text and "`--write`" in text
    assert "Never use a recursive or wildcard copy" in text
    assert "Public source templates and existing local Profiles stay unchanged" in text


def test_public_skill_init_uses_one_server_minute_launch_identity() -> None:
    text = _init_contract()
    normalized = _normalized(text)

    assert "date '+%Y%m%d%H%M'" in text
    assert "<profile-safe-name>-<YYYYMMDDHHMM>" in text
    assert "my-topic-202608281123" in text
    assert (
        "Use that exact identity for both the state-root leaf and run ID" in normalized
    )
    assert "<profile-safe-name>-<YYYYMMDDHHMM>-create-1" in text
    assert "never recompute it independently between those three values" in normalized
    assert "Do not offer a date-only name, an `-r2`/`-r3` fallback" in normalized
    assert "at most 106 characters" in normalized
    assert "defaults, not forced identities" in normalized
    assert (
        "explicit replacement state-root path, run ID, and request ID" in normalized
    )
    assert "never append a sequence suffix or reuse the existing root" in normalized
    assert "do not recapture or silently replace the timestamp" in normalized
    assert "YYYYMMDD-HHMM" not in text


def test_init_naming_is_consistent_across_current_operator_docs() -> None:
    documents = (
        REPOSITORY / "README.md",
        REPOSITORY / "SETUP.md",
        REPOSITORY / "USAGE.md",
        REPOSITORY / "docs" / "operator-guide.md",
    )

    for path in documents:
        text = path.read_text(encoding="utf-8")
        assert "YYYYMMDDHHMM" in text, path
        assert "Run ID" in text, path
        assert "Request ID" in text, path
        assert "-create-1" in text, path


def test_public_skill_start_now_runs_the_existing_lifecycle_in_order() -> None:
    text = _init_contract()
    automatic = text.split("### Start now — managed in this interaction", 1)[1]
    automatic = automatic.split("### Prepare only — compact fallback", 1)[0]
    normalized = _normalized(automatic)

    assert "fresh run" in normalized
    assert "state-root leaf is still absent" in normalized
    assert "bootstrap launch" in automatic and "`--start`" in automatic
    assert "admitted `create_run_payload`" in normalized
    assert "one `hypoloom.cli start` with the stable request ID" in normalized
    assert "fetch `status` for the exact run" in normalized
    _assert_ordered(automatic, "read-only `profile-check`", "`manage_host.py` start",
                   "repeated admission and payload", "one `hypoloom.cli start`",
                   "Once creation is confirmed, fetch `status`")
    assert "report" in normalized and "inline" in normalized
    assert "Do not print a multi-terminal handoff" in normalized
    assert "TUI" in automatic and "optional" in normalized
    assert "finite 30-second window and burst of three" in normalized
    assert "do not replace that interval with `infinity`" in normalized
    assert "every run in that root is terminal" in normalized
    assert "no in-memory or durable Attempt is unfinished" in normalized
    assert "Exit status 0 does not trigger `Restart=on-failure`" in normalized


def test_public_skill_init_prefers_inherited_environment_auth_without_exposing_key() -> None:
    text = _init_contract()
    normalized = _normalized(text)

    assert "real-Agent readiness" in text
    assert "executable regular file" in normalized
    assert "symlink" in normalized
    assert "non-alias Claude selector" in text
    assert "identity required from `modelUsage`" in text
    assert "does not independently observe or claim that downstream wire identity" in normalized
    assert "inherited `ANTHROPIC_API_KEY` is nonempty" in text
    assert "select **environment auth** by default" in text
    assert "Do not require, create, or configure a dedicated Agent settings file" in normalized
    assert "not print, return, log, persist, summarize, measure, hash" in text
    assert "omit `--claude-settings`" in text
    assert "systemd scope transport" in normalized
    assert "--auth-transport environment" in text
    assert "never in argv, unit properties, JSON, logs, or chat" in normalized
    assert "ordinary interactive Claude `/login` OAuth session is not reusable" in text
    assert "every command runs with an isolated HOME" in text
    assert "ACI 11 keeps provider credentials in the external relay" in normalized
    assert "does not use `--bare` or `--safe-mode`" in normalized
    assert "ClaudeAgentHost.preflight" in text
    assert "`settings_path=None`" in text
    assert "do not pass the key value as an argument or report field" in normalized
    assert "not provider authentication or model availability" in normalized
    budget = text.split("7. **Budgets.**", 1)[1].split(
        "8. **User-systemd readiness.**", 1
    )[0]
    normalized_budget = _normalized(budget)
    assert "create_run_payload.research_contract.budget.agent_invocation" in budget
    assert "Never offer independent Agent-budget defaults" in normalized_budget
    assert "ask the operator to replace these five values or the mode" in normalized_budget
    assert "`max_cost: null` explicitly removes the single-call USD cap" in normalized_budget
    assert "`--agent-max-cost-usd null`" in text
    assert "The one final Start-now decision authorizes" in normalized_budget
    for field in (
        "max_turns",
        "max_tokens",
        "max_output_bytes",
        "wall_seconds",
        "max_cost",
        "token_enforcement_mode",
    ):
        assert f"`{field}`" in budget
    assert "`enforce` makes per-call, run and role token thresholds hard limits" in normalized_budget
    assert "`observe` treats those token thresholds only as recorded references" in normalized_budget
    assert "actual usage remains durable accounting" in normalized_budget
    assert "must never guess that default" in normalized_budget
    for option in (
        "--agent-max-turns",
        "--agent-max-tokens",
        "--agent-max-output-bytes",
        "--agent-wall-seconds",
        "--agent-max-cost-usd",
    ):
        assert option in text
    assert "--agent-token-enforcement-mode" not in text
    assert "There is no token-mode CLI flag" in text
    assert "GPU UUID allowlist" in text
    assert "LocalComputeResources" in text
    assert "--gpu-device-uuid" in text
    assert "--gpu-runtime-prefix" in text
    assert "--allow-gpu" in text
    assert "no automatic" in normalized and "permission" in normalized


def test_public_skill_reuses_qualified_user_settings_before_other_compatibility_paths() -> None:
    text = _init_contract()
    normalized = _normalized(text)

    assert "optional compatibility path" in normalized
    assert "**user-level Claude settings**" in text
    assert "`${CLAUDE_CONFIG_DIR}/settings.json`" in text
    assert "`${HOME}/.claude/settings.json`" in text
    assert "propose its exact absolute path as the default existing-settings auth choice" in normalized
    assert "do not offer an empty JSON file" in text
    assert "Never treat JSON validity alone as authentication readiness" in normalized
    assert "nonempty `env.ANTHROPIC_API_KEY`" in text
    assert "Do not qualify `apiKeyHelper`" in text
    assert "nonempty `env.ANTHROPIC_AUTH_TOKEN`" in text
    assert "`env.ANTHROPIC_BASE_URL`" in text
    assert "absolute regular, non-symlink mode-`0600` file" in text
    assert "Only an eligible existing-settings path adds `--claude-settings`" in normalized
    assert "Never create or edit the file from init" in text
    assert "resolved repository files" in text
    assert "<repo>/.claude/settings.json" in text
    assert "--auth-transport settings" in text
    assert "transient user systemd service" in normalized
    assert "without forwarding an ambient API key" in normalized


def test_current_user_docs_route_real_agent_auth_contract_by_role() -> None:
    for relative in ("SETUP.md", "USAGE.md", "docs/operator-guide.md"):
        text = _normalized((REPOSITORY / relative).read_text())
        for field in ("ANTHROPIC_API_KEY", "environment auth", "--claude-settings",
                      "/login", "--bare", "mode-0600", "apiKeyHelper",
                      "create_run_payload.research_contract.budget.agent_invocation",
                      "token_enforcement_mode"):
            assert field in text, (relative, field)
    skill = _normalized(_skill("autoresearch"))
    assert "docs/qualification.md" in skill
    assert "experiment-resource-planning/5" in skill
    assert "unknown charges retain reservations" in skill


def test_current_user_docs_publish_current_profile_and_research_boundaries() -> None:
    readme = (REPOSITORY / "README.md").read_text()
    for link in ("profiles/README.md", "docs/architecture.md", "docs/qualification.md"):
        assert f"]({link})" in readme
    usage = (REPOSITORY / "USAGE.md").read_text()
    for marker in ("Grounding", "LITERATURE_REVIEW", "HYPOTHESIS_PROPOSAL",
                   "EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION", "RESULT_ANALYSIS",
                   "HYPOTHESIS_UPDATE", "OpenAlex", "GPU", "MCP", "debug rehearse-chain"):
        assert marker in usage
    architecture = (REPOSITORY / "docs/architecture.md").read_text()
    assert "Profile v4 is the public authoring format" in architecture
    assert "read-only" in architecture and "Cross-version run migration" in architecture


def test_public_skill_distinguishes_control_python_from_topic_gpu_runtime() -> None:
    text = _init_contract()
    normalized = _normalized(text)

    assert "Keep the control Python distinct" in normalized
    assert "admitted experiment runtime prefix" in text
    assert "CUDA/topic environment is not automatically the Hypoloom control interpreter" in text


def test_public_skill_init_never_uses_interactive_project_settings_for_agent() -> None:
    text = _init_contract()
    normalized = _normalized(text)

    assert ".claude/settings.example.json" in text
    assert ".claude/settings.json" in text
    assert "exclusive/no-clobber create under `umask 077`" in text
    assert "never alter pre-existing settings" in normalized
    assert "set -o noclobber" in text
    assert "chmod 0600" in text
    assert "interactive project settings" in normalized
    assert "dedicated" in normalized


def test_public_skill_start_now_replaces_the_manual_terminal_handoff() -> None:
    init = _init_contract()
    normalized = _normalized(init)

    assert "Start now (Recommended)" in init
    assert "same Claude interaction" in normalized
    assert "Do not print a multi-terminal handoff" in normalized
    assert "separate-terminal foreground command" not in init
    assert "must be running before start" not in normalized
    assert "not required by `Start now`" in normalized


def test_public_skill_routes_monitor_tui_outside_claude_capture() -> None:
    text = _skill("autoresearch")
    monitor = text.split("## 3. Status, monitor, and oracle", 1)[1].split(
        "## 4. Cancel", 1
    )[0]
    normalized = _normalized(monitor)
    init = _normalized(_init_contract())

    assert MONITOR_LAUNCHER.is_file()
    assert "monitor <run-id> --json" in monitor
    assert "relay its one snapshot unchanged" in normalized
    assert "Do not parse it into a Markdown table" in normalized
    assert "Both monitor routes consume the same existing observer snapshot" in normalized
    assert "launch_monitor.py" in monitor
    assert "nonempty `TMUX`" in monitor
    assert "new tmux window with a real PTY" in normalized
    assert "window closes naturally" in normalized
    assert "--web-port <port>" in monitor
    assert "--no-web" in monitor
    assert "JSON mode never binds a Web port" in normalized
    assert "Web / local access" in monitor
    assert "stops the server when the TUI unmounts" in normalized
    assert "port collision degrades only Web" in normalized
    assert "ssh -N -L <port>:127.0.0.1:<port>" in monitor
    assert "prints exactly one complete, shell-quoted command" in normalized
    assert "do not execute it inside Claude Code" in normalized
    assert "authorizes no run write or lifecycle action" in normalized
    assert (
        "opens a new window when this Claude Code session is already inside tmux"
        in init
    )
    assert "never embeds Textual in Claude Code's captured command output" in init


def test_public_skill_routes_read_only_debug_as_exact_json_passthrough() -> None:
    text = _skill("autoresearch")
    debug = text.split("## Retained Hypoloom module debug", 1)[1].split(
        "## 4. Cancel", 1,
    )[0]
    normalized = _normalized(debug)

    for action in ("debug inspect", "debug export", "debug validate"):
        assert action in debug
    for option in (
        "--state-root",
        "--run-id",
        "--attempt-id",
        "--include-payloads",
        "--output",
        "--fixture",
        "--source-exact",
        "--candidate",
    ):
        assert option in debug
    assert "PYTHONDONTWRITEBYTECODE=1 HYPOLOOM_RUNTIME_FAMILY=hypoloom" in debug
    assert "literal absolute `--state-root`" in normalized
    assert "Do not automatically select" in normalized
    assert "outside the source state root" in normalized
    assert "regular mode-0600 file with exclusive create" in normalized
    assert "two mutually exclusive input forms" in normalized
    assert "Require exactly one" in normalized
    assert "Never retry a failed source-exact validation as candidate" in normalized
    assert "relay stdout as one verbatim JSON block" in normalized
    assert "Do not parse, summarize, translate, reorder, reserialize" in normalized
    assert "stdout JSON and exit 2" in normalized
    assert "truncates output" in normalized
    for forbidden in (
        "manage_host.py",
        "launch_monitor.py",
        "-m hypoloom.cli status",
        "-m hypoloom.cli start",
        "-m hypoloom.cli cancel",
        "-m hypoloom.cli backup",
        "-m hypoloom.cli restore",
    ):
        assert forbidden not in debug


def test_public_skill_routes_only_bounded_effectful_module_rehearsal() -> None:
    text = _skill("autoresearch")
    routing = text.split("## Supported routing", 1)[1].split(
        "There is no separate `drive` command", 1,
    )[0]
    debug = text.split("## Retained Hypoloom module debug", 1)[1].split(
        "## 4. Cancel", 1,
    )[0]
    normalized = _normalized(debug)

    assert "debug rehearse-chain" in routing
    for option in (
        "--source-state-root",
        "--source-run-id",
        "--through-attempt-id",
        "--profiles-root",
        "--process-delay",
        "--wall-seconds",
        "--allow-agent",
        "--allow-process",
        "--allow-gpu",
        "--gpu-device-uuid",
        "--gpu-runtime-prefix",
    ):
        assert option in debug
    for statement in (
        "currently implemented public effectful debug action",
            "does not by itself qualify a schema-v3 or adapter-`/29` candidate",
        "Never choose or infer any of those values",
        "previously absent absolute destination root",
        "ACI schema 10/profile-schema-v2-or-v3 cycle-one",
        "experiment-ordinal-1",
        "current request/response parser and ACI postflight",
        "same component/role",
        "local deterministic Agent plus offline literature",
        "real subprocess/CUDA framework evidence",
        "non_authoritative=true",
        "source_output_rebound_once=true",
        "clean Oracle, no residue",
        "Source authority rows and canonical artifacts remain unchanged",
        "Never retry automatically",
        "substitute arbitrary shell replay",
    ):
        assert statement in normalized
    for forbidden in (
        "--claude-executable",
        "--claude-settings",
        "--claude-model",
        "--openalex-api-key-file",
        "-m hypoloom.cli serve",
    ):
        assert forbidden not in debug


def test_public_skill_prepare_only_has_one_compact_nonsecret_fallback() -> None:
    init = _init_contract()
    prepare = init.split("### Prepare only — compact fallback", 1)[1]
    normalized = _normalized(prepare)

    assert "approved bootstrap writes" in normalized
    assert "do not call `manage_host.py`" in normalized
    assert "Print no old Host/check/start/status/monitor command bundle" in normalized
    assert "at most one compact" in normalized
    for assignment in (
        "HYPOLOOM_RUNTIME_FAMILY=hypoloom",
        "HYPOLOOM_HOOK_PYTHON=",
        "HYPOLOOM_PYTHON=",
        "HYPOLOOM_PROFILES_ROOT=",
        "HYPOLOOM_STATE_ROOT=",
    ):
        assert assignment in prepare
    assert "never an API-key assignment" in normalized
    assert "reload the newly copied project settings" in normalized
    assert "must not include a Host or run command" in normalized


def test_public_skill_checks_user_systemd_read_only_and_never_enables_linger() -> None:
    init = _init_contract()
    before_choice = init.split("Use one final `AskUserQuestion`", 1)[0]
    normalized = _normalized(before_choice)

    assert "systemctl --user show --property=Version --value" in before_choice
    assert 'loginctl show-user "$(id -u)" --property=Linger --value' in before_choice
    assert "Do not create a probe service or scope" in normalized
    assert "unavailable user manager blocks `Start now` but not `Prepare only`" in normalized
    assert "`Linger=no` does not block same-window operation" in normalized
    assert "limitation on unattended continuation" in normalized
    assert "Never enable linger" in normalized
    assert "offer 30 seconds" in normalized
    assert "no more than 300 seconds" in normalized


def test_public_skill_rolls_back_pre_start_failures_and_reconciles_ambiguity() -> None:
    init = _init_contract()
    automatic = init.split("### Start now — managed in this interaction", 1)[1]
    automatic = automatic.split("### Prepare only — compact fallback", 1)[0]
    normalized = _normalized(automatic)

    assert "failed pre-create check stops only the newly created managed Host" in normalized
    assert "No state root or log is deleted" in normalized
    assert "queries STATUS immediately" in normalized
    assert "If the run exists, it does not retry or stop it" in normalized
    assert "Only a conclusive `not_found` allows cleanup" in normalized
    assert "reports `creation_uncertain`, preserves the unit" in normalized
    assert "Never invent another request ID" in normalized
    assert "an existing state root is rejected" in normalized


def test_root_claude_bootstrap_matches_the_canonical_family_boundary() -> None:
    text = (REPOSITORY / "CLAUDE.md").read_text(encoding="utf-8")
    normalized = _normalized(text)

    assert "absent" in normalized and "allowed" in normalized
    assert "nonempty value other than exactly `hypoloom`" in normalized
    assert "HYPOLOOM_RUNTIME_FAMILY=hypoloom" in text
    assert "same" in normalized and "Claude" in normalized and "interaction" in normalized


def test_init_qualifies_dependencies_without_writing_bytecode() -> None:
    text = _skill("autoresearch")

    assert "PYTHONDONTWRITEBYTECODE=1" in text
    for module in ("hypoloom", "yaml", "filelock"):
        assert f"`{module}`" in text


def test_public_skill_checks_ignore_only_for_repository_local_state() -> None:
    text = _skill("autoresearch")

    assert "local profile must pass\nGit's own ignore matcher" in text
    assert 'check-ignore --no-index -q -- "$PROFILE_DESTINATION"' in text
    assert "If the chosen state root is inside the repository" in text
    assert 'check-ignore --no-index -q -- "$STATE_ROOT"' in text
    assert "If the state root is an absolute path outside this repository, do not run the\nrepository's ignore matcher" in text








def test_public_operator_entry_is_the_single_supported_skill() -> None:
    root = REPOSITORY / ".claude/skills"
    assert {path.name for path in root.iterdir() if path.is_dir()} == {"autoresearch"}


def test_autoresearch_is_hypoloom_canonical_and_does_not_route_legacy_hg() -> None:
    text = _skill("autoresearch")

    assert text.startswith("---\nname: autoresearch\n")
    assert "independent `hypoloom` runtime" in text
    assert "Never fall back to another runtime" in text
    assert "No legacy runtime bridge is provided" in text
    for route in ("hg-start.md", "hg-drive.md", "hg-status.md", "hg-gates.md"):
        assert route not in text
