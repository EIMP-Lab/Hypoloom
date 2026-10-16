"""Host-private settings input; never an artifact, export or new fact source."""
from __future__ import annotations

import json
import hashlib
import math
import time
import os
import sqlite3
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

from .agent_aci import AgentACIError
from .agent_budget import DEFAULT_PROVIDER_AUTH_COOLDOWN_SECONDS

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


def runtime_environment_id(package, runtime_files, *, private_settings_path=None, cache=None):
    """Reuse a Host's last byte verification while all local file inputs agree.

    Direct callers omit the disposable cache and always verify complete bytes.
    Private settings validation is never cached, including its permission check.
    """
    if private_settings_path is not None:
        if private_settings_path not in runtime_files:
            raise ValueError("private settings must be included in runtime_files")
        validate_private_settings(private_settings_path)
    platform = (sys.version, str(Path(sys.executable).resolve()), sqlite3.sqlite_version)
    def inventory():
        files = sorted(package.glob("*.py")) + sorted((package / "migrations").glob("*.sql"))
        files += sorted(path for seed in ("expert-seed", "grounding-seed", "research-workflow")
            for path in (package / "skills" / seed).rglob("*") if path.is_file())
        environment = package.parent / "environment.yml"
        if environment.is_file(): files.append(environment)
        result = []
        for path in (*files, *runtime_files):
            if path == private_settings_path: name = PRIVATE_SETTINGS_NAME.encode()
            else:
                try: name = path.relative_to(package.parent).as_posix().encode()
                except ValueError: name = path.resolve().as_posix().encode()
            metadata = path.stat()
            result.append((path, name, metadata.st_dev, metadata.st_ino, metadata.st_size,
                metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_mode))
        return tuple(result)
    files = inventory()
    key = (platform, files)
    if cache is not None and cache.get('key') == key:
        return cache['identity']
    digest = hashlib.sha256()
    for part in platform:
        raw = part.encode(); digest.update(len(raw).to_bytes(8, 'big') + raw)
    for path, name, _, _, size, *_ in files:
        digest.update(len(name).to_bytes(8, 'big') + name)
        digest.update(size.to_bytes(8, 'big'))
        with path.open('rb') as stream:
            for content in iter(lambda: stream.read(1024 * 1024), b''): digest.update(content)
    if files != inventory():
        raise HostConfigurationError('runtime files changed during verification')
    identity = 'sha256:' + digest.hexdigest()
    if cache is not None:
        cache.clear(); cache.update(key=key, identity=identity)
    return identity


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


def provider_retry_status(host, run_id: str) -> dict[str, Any]:
    # Durable gate view: closed Attempts carry the failure, an operator
    # clear transition ends it early. No in-memory authority, so a
    # reopened Host computes the identical gate.
    from .provider_observation import PROVIDER_TRANSIENT, provider_retry_history, observe_provider_retry
    cleared = host.store.inspect("SELECT MAX(json_extract(payload_json,'$.provider_retry_clear.cleared_at')) AS v "
        "FROM workflow_transitions WHERE run_id=? AND workflow_kind='RUN'", (run_id,))[0]["v"]
    try: cooldown = host._research_contract(run_id).document.get("budget", {}).get("provider_auth_cooldown_seconds")
    except (AgentACIError, KeyError, TypeError, ValueError): cooldown = None
    if type(cooldown) not in (int, float) or not math.isfinite(float(cooldown)) or float(cooldown) < 0:
        cooldown = DEFAULT_PROVIDER_AUTH_COOLDOWN_SECONDS
    limit = host._agent_attempt_limits(host._research_contract(run_id))[1]
    cleared_at = float(cleared) if type(cleared) in (int, float) and math.isfinite(cleared) else None
    def history():
        rows = host.store.inspect("SELECT attempt_id, started_at, ended_at, state, "
            "json_extract(observation_json,'$.diagnostics.provider_failure.code') AS code, "
            "json_extract(observation_json,'$.diagnostics.provider_failure.retry_at') AS retry_at, "
            "json_extract(observation_json,'$.diagnostics.provider_failure.http_status') AS http_status, "
            "(terminal_reason='SUCCEEDED' OR (json_extract(observation_json,'$.diagnostics.provider_observed_usage.completed_requests')>0 "
            "AND json_extract(observation_json,'$.diagnostics.provider_failure.code') IS NULL)) AS provider_success "
            "FROM execution_attempts WHERE run_id=? AND kind='AGENT'", (run_id,))
        return provider_retry_history(rows, cooldown_seconds=float(cooldown), cleared_at=cleared_at, attempt_limit=limit)
    status = observe_provider_retry(host.store.cached_read(("provider-retry-history", run_id, cooldown, cleared_at, limit), history), now=time.time())
    from .provider_traffic import for_run
    traffic = for_run(host, run_id)
    traffic.select(cleared)
    boundary = max([cleared or 0, *(gate['attempt_ended_at'] for gate in status['gates'])])
    recovered_at = traffic.recovered_after(boundary)
    if (all(gate['class'] in PROVIDER_TRANSIENT for gate in status['gates'])
            and recovered_at is not None):
        status.update(pending=False, state='ready', requires_operator=False,
            reason=None, next_retry_at=None, recovered_at=recovered_at,
            recovery_attempts=0, gates=[])
    _, error = host._provider_connection(run_id)
    if error:
        status.update(pending=True, state="waiting_operator", requires_operator=True,
            reason="connection_configuration", connection_error=error, next_retry_at=None)
    from .provider_recovery import scheduled_retry
    schedule = scheduled_retry(host, run_id)
    status.update(scheduling_supported=True, scheduled_retry=schedule)
    traffic.schedule(None if schedule is None else schedule["retry_at"])
    if schedule is not None:
        status.update(pending=True, state="cooldown", requires_operator=False, next_retry_at=schedule["retry_at"])
    return status

def provider_recovery_reused(host, run_id: str) -> bool:
    """Reload mutable credentials while coalescing an unchanged recovery."""
    previous, _ = host._provider_connection(run_id)
    host._provider_connections.pop(run_id, None)
    environment, error = host._provider_connection(run_id)
    if error:
        raise ValueError(error)
    status = host._provider_retry_status(run_id)
    # The first explicit reload must retain its existing session boundary.
    return (environment == previous and status['cleared_at'] is not None
        and status['state'] in {'ready', 'recovering'})


def provider_connection(host, run_id: str) -> tuple[dict[str, str], str | None]:
    controls = host.store.inspect("SELECT command_id, payload_json FROM workflow_transitions WHERE run_id=? "
        "AND workflow_kind='RUN' AND json_extract(payload_json,'$.provider_retry_clear') IS NOT NULL ORDER BY revision DESC", (run_id,))
    source = next((json.loads(row["payload_json"])["provider_retry_clear"].get("settings_path") for row in controls
        if json.loads(row["payload_json"])["provider_retry_clear"].get("settings_path")), None)
    if source is None: return dict(host._provider_environment), None
    identity = controls[0]["command_id"]
    cached = host._provider_connections.get(run_id)
    if cached is None or cached[0] != identity:
        try: cached = (identity, read_provider_connection(Path(source)), None)
        except HostConfigurationError as exc: cached = (identity, None, str(exc))
        host._provider_connections[run_id] = cached
    return {**host._provider_environment, **(cached[1] or {})}, cached[2]
