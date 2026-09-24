"""Host-private settings input; never an artifact, export or new fact source."""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import tempfile
from pathlib import Path

from .artifacts import renameat2_noreplace

PRIVATE_SETTINGS_NAME = "host/claude-settings.json"


class HostConfigurationError(RuntimeError):
    """An explicit configuration refusal, not a transient execution failure."""


def _settings_bytes(path: Path) -> bytes:
    if not path.is_absolute():
        raise HostConfigurationError("private settings input must be an absolute real file")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            os.close(fd)
            raise HostConfigurationError("private settings input must be a regular file with mode 0600")
        with os.fdopen(fd, "rb") as stream:
            content = stream.read()
        value = json.loads(content)
        if not isinstance(value, dict) or not isinstance(value.get("env", {}), dict):
            raise HostConfigurationError("private settings input must contain a JSON object with object env")
        return content
    except (OSError, UnicodeError, ValueError):
        # JSON/OSError messages may contain credential bytes or private paths.
        raise HostConfigurationError("private settings input is unavailable or invalid JSON") from None


def validate_private_settings(path: Path) -> None:
    _settings_bytes(path)


def read_provider_connection(path: Path) -> dict[str, str]:
    """Read the operator-selected, mutable connection input (never model config).

    The control journal owns selection; this 0600 file owns credentials. No
    secret is copied to a fact, backup or additional private snapshot.
    """
    from urllib.parse import urlsplit
    value = json.loads(_settings_bytes(path))
    allowed = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"}
    env = value.get("env", {})
    if set(value) != {"env"} or set(env) - allowed:
        raise HostConfigurationError("connection settings allow only env with API key, auth token and base URL; model/runtime changes are not allowed")
    if any(not isinstance(v, str) or "\x00" in v for v in env.values()):
        raise HostConfigurationError("connection values must be strings without NUL")
    if sum(bool(env.get(key)) for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")) != 1:
        raise HostConfigurationError("connection requires exactly one nonempty API key or auth token")
    endpoint = env.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    try:
        parsed = urlsplit(endpoint)
        valid = (parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username
            and not parsed.password and not parsed.query and not parsed.fragment
            and (parsed.port is None or 0 < parsed.port < 65536))
    except ValueError:
        valid = False
    if not valid:
        raise HostConfigurationError("connection base URL is invalid; credentials must be separate")
    # Explicit empty counterpart replaces, rather than inherits, the old key.
    return {"ANTHROPIC_API_KEY": env.get("ANTHROPIC_API_KEY", ""),
        "ANTHROPIC_AUTH_TOKEN": env.get("ANTHROPIC_AUTH_TOKEN", ""), "ANTHROPIC_BASE_URL": endpoint}


def _has_created_run(state_root: Path) -> bool:
    database = state_root / "state.sqlite"
    if not os.path.lexists(database):
        return False
    if database.is_symlink() or not database.is_file():
        raise HostConfigurationError("cannot establish private settings lifecycle from the existing database")
    try:
        connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            return connection.execute("SELECT 1 FROM workflow_runs LIMIT 1").fetchone() is not None
        finally:
            connection.close()
    except sqlite3.Error:
        raise HostConfigurationError("cannot establish private settings lifecycle from the existing database") from None


def _private_directory(state_root: Path) -> Path:
    directory = state_root / "host"
    if os.path.lexists(directory):
        if directory.is_symlink() or not directory.is_dir() or stat.S_IMODE(directory.stat().st_mode) != 0o700:
            raise HostConfigurationError("private settings directory must be a real directory with mode 0700")
    else:
        directory.mkdir(mode=0o700)
    return directory


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _copy_settings(state_root: Path, source: Path) -> Path:
    content = _settings_bytes(source)
    directory = _private_directory(state_root)
    destination = state_root / PRIVATE_SETTINGS_NAME
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".settings-", dir=directory)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        renameat2_noreplace(temporary, destination)
        _fsync_directory(directory)
        _fsync_directory(state_root)
    except OSError:
        raise HostConfigurationError("could not publish private settings input") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def prepare_host_settings(state_root: Path, source: str | os.PathLike[str] | None) -> Path | None:
    """Called under the existing Host lock, before constructing its version.

    A missing companion in a created run is not permission to bind new secrets.
    An existing companion wins without opening or validating the source path.
    Environment authentication deliberately retains its existing semantics.
    """
    if source is None:
        return None
    destination = state_root / PRIVATE_SETTINGS_NAME
    if os.path.lexists(destination):
        _private_directory(state_root)
        validate_private_settings(destination)
        return destination
    if _has_created_run(state_root):
        raise HostConfigurationError("missing Host private settings for an existing run; explicit fresh-root restore is required")
    return _copy_settings(state_root, Path(source))


def restore_host_settings(fresh_restore_root: Path, source: str | os.PathLike[str]) -> Path:
    """Only restore_backup's unpublished fresh staging root may call this.

    The ordinary complete run-version check still rejects incorrect supplied
    bytes. This does not alter backups, old roots, or their immutable versions.
    """
    if os.path.lexists(fresh_restore_root / PRIVATE_SETTINGS_NAME):
        raise HostConfigurationError("fresh restore already contains private settings")
    return _copy_settings(fresh_restore_root, Path(source))
