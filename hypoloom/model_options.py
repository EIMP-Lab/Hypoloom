"""Inspect one selected Claude auth source without exposing credentials.

This helper is intentionally read-only and provider-free.  It emits only the
logical model choices that `/autoresearch init` may show to the operator.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Mapping


from .claude_agent_host import validate_claude_model_selector

_REPOSITORY = Path(__file__).resolve().parent.parent

_MODEL_MAPPINGS = (
    ("sonnet", "ANTHROPIC_DEFAULT_SONNET_MODEL"),
    ("opus", "ANTHROPIC_DEFAULT_OPUS_MODEL"),
    ("haiku", "ANTHROPIC_DEFAULT_HAIKU_MODEL"),
    ("fable", "ANTHROPIC_DEFAULT_FABLE_MODEL"),
)
_MODEL_INPUT_NAMES = frozenset(
    {"ANTHROPIC_MODEL", *(name for _, name in _MODEL_MAPPINGS)}
)
_FORBIDDEN_SETTINGS = frozenset(
    {
        _REPOSITORY / ".claude" / name
        for name in (
            "settings.json",
            "settings.local.json",
            "settings.example.json",
        )
    }
)


class ResolverError(ValueError):
    """A fixed, non-sensitive inspection failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _model_options(
    environment: Mapping[str, object],
) -> tuple[list[dict[str, str]], list[str]]:
    options: list[dict[str, str]] = []
    invalid: list[str] = []
    for logical_selector, variable in _MODEL_MAPPINGS:
        if variable not in environment:
            continue
        claude_model = environment[variable]
        if not isinstance(claude_model, str):
            invalid.append(logical_selector)
            continue
        try:
            claude_model = validate_claude_model_selector(claude_model)
        except ValueError:
            invalid.append(logical_selector)
            continue
        options.append(
            {
                "logical_selector": logical_selector,
                "claude_model": claude_model,
            }
        )
    return options, invalid


def _configured_default(
    selected: object, options: list[dict[str, str]]
) -> tuple[dict[str, str | None] | None, str | None]:
    if not isinstance(selected, str) or not selected:
        return None, None
    by_logical = {
        option["logical_selector"]: option for option in options
    }
    logical = selected.casefold()
    if logical in by_logical:
        return dict(by_logical[logical]), None
    if logical in {logical_selector for logical_selector, _ in _MODEL_MAPPINGS}:
        return None, "unresolved_logical_selector"
    try:
        selected = validate_claude_model_selector(selected)
    except ValueError:
        return None, "invalid_model_selector"
    return {
        "logical_selector": None,
        "claude_model": selected,
    }, None


def _result(
    *,
    auth_source: str,
    auth_kind: str,
    environment: Mapping[str, object],
    settings_model: object = None,
) -> dict[str, object]:
    from hypoloom.provider_relay import validate_provider_connection
    try:
        validate_provider_connection(environment.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"), environment)
    except ValueError as exc:
        raise ResolverError(str(exc)) from None
    model_environment = {
        name: environment[name]
        for name in _MODEL_INPUT_NAMES
        if name in environment
    }
    options, invalid = _model_options(model_environment)
    selected = model_environment.get("ANTHROPIC_MODEL")
    if not _nonempty(selected):
        selected = settings_model
    configured_default, configured_default_error = _configured_default(
        selected, options
    )
    return {
        "auth_source": auth_source,
        "auth_kind": auth_kind,
        "configured_default": configured_default,
        "configured_default_error": configured_default_error,
        "model_options": options,
        "invalid_logical_selectors": invalid,
    }


def inspect_environment() -> dict[str, object]:
    """Inspect only the inherited environment selected for environment auth."""

    if not _nonempty(os.environ.get("ANTHROPIC_API_KEY")):
        raise ResolverError("environment_auth_unavailable")
    environment = {
        name: os.environ[name]
        for name in (*_MODEL_INPUT_NAMES, "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
                     "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
        if name in os.environ
    }
    return _result(
        auth_source="environment",
        auth_kind="api_key",
        environment=environment,
    )


def _read_settings(path: Path, *, repository: Path | None = None) -> dict[str, object]:
    if not path.is_absolute():
        raise ResolverError("settings_path_not_absolute")
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ResolverError("settings_not_regular_nonsymlink_0600") from exc
    forbidden = _FORBIDDEN_SETTINGS | (frozenset(repository / ".claude" / name
        for name in ("settings.json", "settings.local.json", "settings.example.json")) if repository else frozenset())
    if resolved in forbidden:
        raise ResolverError("settings_repository_interactive_forbidden")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ResolverError("settings_not_regular_nonsymlink_0600") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ResolverError("settings_not_regular_nonsymlink_0600")
        try:
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                descriptor = -1
                value = json.load(stream)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResolverError("settings_invalid_json_object") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(value, dict):
        raise ResolverError("settings_invalid_json_object")
    return value


def inspect_settings(path: Path, *, repository: Path | None = None) -> dict[str, object]:
    """Inspect only one explicitly selected existing-settings auth file."""

    settings = _read_settings(path, repository=repository)
    environment = settings.get("env")
    if not isinstance(environment, dict):
        raise ResolverError("settings_auth_unavailable")
    if _nonempty(environment.get("ANTHROPIC_API_KEY")):
        auth_kind = "api_key"
    elif _nonempty(environment.get("ANTHROPIC_AUTH_TOKEN")) and _nonempty(
        environment.get("ANTHROPIC_BASE_URL")
    ):
        auth_kind = "gateway"
    else:
        raise ResolverError("settings_auth_unavailable")
    return _result(
        auth_source="settings",
        auth_kind=auth_kind,
        environment=environment,
        settings_model=settings.get("model"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--environment", action="store_true")
    source.add_argument("--settings", type=Path)
    return parser


def main(argv: list[str] | None = None, *, repository: Path | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = (
            inspect_environment()
            if args.environment
            else inspect_settings(args.settings, repository=repository)
        )
    except ResolverError as exc:
        print(json.dumps({"error": exc.code}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
