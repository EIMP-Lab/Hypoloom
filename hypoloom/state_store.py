"""The single-writer SQLite authority used by the resident Hypoloom Host."""
from __future__ import annotations
import json
import os
import sqlite3
from copy import deepcopy
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
MINIMUM_SQLITE = (3, 51, 3)
_MIGRATIONS = tuple(sorted((Path(__file__).with_name("migrations")).glob("*.sql")))
SCHEMA_VERSION = len(_MIGRATIONS)
_VERSIONS = ("workflow_definition_version", "research_contract_version", "evaluator_version", "agent_aci_version", "runner_version", "artifact_result_schema_version", "runtime_environment_id", "configuration_id", "sqlite_runtime_version")
_FACT_COLUMNS = ("fact_id", "run_id", "fact_type", "schema_version", "subject_id", "goal_id", "hypothesis_id", "experiment_id", "execution_id", "outcome_id", "artifact_id", "payload_json")
class StateStoreError(RuntimeError): pass
class WriterCapabilityError(StateStoreError): pass
class SQLiteRuntimeError(StateStoreError): pass
class MigrationError(StateStoreError): pass
class IdentityConflict(StateStoreError): pass
class RevisionConflict(StateStoreError): pass
class HostWriterCapability:
    """Nominal token that makes writable construction explicit at the Host."""
    __slots__ = ("_marker",)
    _ISSUER = object()
    def __init__(self, marker: object) -> None:
        if marker is not self._ISSUER: raise TypeError("use HostWriterCapability.issue() in Workflow Host")
        self._marker = marker
    @classmethod
    def issue(cls) -> HostWriterCapability: return cls(cls._ISSUER)
@dataclass(frozen=True, slots=True)
class StoredCommandResult:
    status: str
    revision: int | None
    result: Mapping[str, Any] | None
    error: Mapping[str, Any] | None
    replayed: bool = False
def _plain(value: Any) -> Any:
    if isinstance(value, Enum): return value.value
    if isinstance(value, Decimal): return str(value)
    if isinstance(value, Path): return str(value)
    if is_dataclass(value) and not isinstance(value, type): return _plain(asdict(value))
    if isinstance(value, Mapping): return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)): return [_plain(item) for item in value]
    return value
def canonical_json(value: Any) -> str:
    return json.dumps(_plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
def _json(value: Any) -> str:
    if isinstance(value, str): value = json.loads(value)
    return canonical_json(value)
def _record(value: Any) -> dict[str, Any]:
    if hasattr(value, "as_record"): value = value.as_record()
    elif is_dataclass(value) and not isinstance(value, type): value = asdict(value)
    if not isinstance(value, Mapping): raise TypeError("mutation must be a mapping or expose as_record()")
    return {str(key): _plain(item) for key, item in value.items()}
def _text(value: Any, field: str) -> str:
    value = _plain(value)
    if not isinstance(value, str) or not value or "\x00" in value: raise ValueError(f"{field} must be non-empty text")
    return value
def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None: return None if row is None else dict(row)
class StateTransaction:
    """Transaction-scoped reads and primitive mutations; it never commits."""
    def __init__(self, connection: sqlite3.Connection) -> None: self._db = connection
    def execute(self, sql: str, parameters: Sequence[Any] = ()) -> sqlite3.Cursor: return self._db.execute(sql, tuple(parameters))
    def executemany(self, sql: str, parameters: Sequence[Sequence[Any]]) -> sqlite3.Cursor: return self._db.executemany(sql, parameters)
    def _insert(self, table: str, columns: Sequence[str], values: Sequence[Any], key: str, params: Sequence[Any], label: str) -> bool:
        old = self.execute(f"SELECT {','.join(columns)} FROM {table} WHERE {key}", params).fetchone()
        if old is not None:
            if tuple(old) == tuple(values): return False
            raise IdentityConflict(f"{label} already has different content")
        try: self.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", values)
        except sqlite3.IntegrityError as exc: raise IdentityConflict(f"{label} constraint conflict: {exc}") from exc
        return True
    def create_run(self, run_id: str, version: Mapping[str, Any] | object, *, derived_from_run_id: str | None = None) -> bool:
        data = _record(version); missing = [field for field in _VERSIONS if field not in data]
        if missing: raise ValueError(f"run version missing fields: {missing}")
        if str(data["sqlite_runtime_version"]) != sqlite3.sqlite_version: raise SQLiteRuntimeError("run SQLite version differs from active runtime")
        columns = ("run_id", "derived_from_run_id", *_VERSIONS); values = (_text(run_id, "run_id"), derived_from_run_id, *(str(data[field]) for field in _VERSIONS))
        return self._insert("workflow_runs", columns, values, "run_id=?", (run_id,), f"run {run_id!r}")
    def get_run(self, run_id: str) -> dict[str, Any] | None: return _dict(self.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone())
    def get_command(self, command_id: str) -> dict[str, Any] | None: return _dict(self.execute("SELECT * FROM workflow_commands WHERE command_id=?", (command_id,)).fetchone())
    def get_human_decision(self, decision_id: str) -> dict[str, Any] | None:
        return _dict(self.execute("SELECT * FROM workflow_human_decisions WHERE decision_id=?", (decision_id,)).fetchone())
    def open_human_decision(self, decision: Mapping[str, Any] | object) -> bool:
        data = _record(decision); decision_id = _text(data["decision_id"], "decision_id")
        revision = data["request_revision"]
        if type(revision) is not int or revision < 0: raise ValueError("request_revision must be a non-negative integer")
        head = self.latest_transition(data["workflow_id"])
        if head is None or head["run_id"] != data["run_id"] or head["revision"] != revision:
            raise RevisionConflict("human decision request differs from its owner revision")
        if self.execute("SELECT 1 FROM execution_attempts WHERE workflow_id=? AND state='OPEN' LIMIT 1", (data["workflow_id"],)).fetchone():
            raise IdentityConflict("human decision owner still has an OPEN Attempt")
        columns = ("decision_id", "run_id", "workflow_id", "state", "family", "request_revision", "request_json", "deadline", "command_id")
        values = (decision_id, data["run_id"], data["workflow_id"], "OPEN", data["family"], revision,
            _json(data.get("request_json", data.get("request"))), data.get("deadline"), data["command_id"])
        return self._insert("workflow_human_decisions", columns, values, "decision_id=?", (decision_id,), "human decision")
    def resolve_human_decision(self, decision_id: str, *, run_id: str, resolution: str,
        resolution_payload: Mapping[str, Any], resolved_at: float) -> bool:
        current = self.get_human_decision(decision_id)
        if current is None or current["run_id"] != run_id: raise ValueError("human decision does not belong to the requested run")
        if resolution not in {"APPROVED", "REJECTED", "EDITED", "TIMED_OUT", "CANCELLED"}: raise ValueError("invalid human decision resolution")
        payload = _json(resolution_payload); terminal = (resolution, payload, resolved_at)
        if current["state"] == "RESOLVED":
            if tuple(current[name] for name in ("resolution", "resolution_json", "resolved_at")) == terminal: return False
            raise IdentityConflict("human decision already has a different resolution")
        self.execute("UPDATE workflow_human_decisions SET state='RESOLVED',resolution=?,resolution_json=?,resolved_at=? WHERE decision_id=? AND state='OPEN'", (*terminal, decision_id))
        return True
    def record_command(self, command: Mapping[str, Any] | object) -> bool:
        data = _record(command); command_id = _text(data["command_id"], "command_id")
        columns = ("command_id", "run_id", "workflow_id", "expected_revision", "command_type", "request_json", "response_status", "result_json", "error_json", "accepted_revision")
        values = (command_id, data["run_id"], data["workflow_id"], data.get("expected_revision"), data["command_type"], _json(data.get("request_json", data.get("request"))), data.get("status"), None if data.get("result") is None else _json(data["result"]), None if data.get("error") is None else _json(data["error"]), data.get("revision"))
        return self._insert("workflow_commands", columns, values, "command_id=?", (command_id,), f"command {command_id!r}")
    def latest_transition(self, workflow_id: str) -> dict[str, Any] | None:
        return _dict(self.execute("SELECT * FROM workflow_transitions WHERE workflow_id=? ORDER BY revision DESC LIMIT 1", (workflow_id,)).fetchone())
    def append_transition(self, transition: Mapping[str, Any] | object) -> bool:
        data = _record(transition); workflow_id = _text(data["workflow_id"], "workflow_id"); revision = int(data["revision"])
        columns = ("workflow_id", "revision", "run_id", "workflow_kind", "previous_state", "state", "command_id", "next_command", "due_at", "fact_id", "payload_json")
        values = (workflow_id, revision, data["run_id"], _plain(data["workflow_kind"]), data.get("previous_state"), _plain(data["state"]), data["command_id"], data.get("next_command"), data.get("due_at"), data.get("fact_id"), _json(data.get("payload", {})))
        old = self.execute(f"SELECT {','.join(columns)} FROM workflow_transitions WHERE workflow_id=? AND revision=?", (workflow_id, revision)).fetchone()
        if old is not None:
            if tuple(old) == values: return False
            raise IdentityConflict(f"transition {(workflow_id, revision)!r} conflicts")
        head = self.latest_transition(workflow_id)
        if head is None and (revision != 1 or data.get("previous_state") is not None): raise RevisionConflict("initial revision must be 1 with no previous_state")
        if head is not None and (revision != head["revision"] + 1 or data.get("previous_state") != head["state"]): raise RevisionConflict(f"expected revision {head['revision'] + 1} from {head['state']!r}")
        return self._insert("workflow_transitions", columns, values, "workflow_id=? AND revision=?", (workflow_id, revision), "transition")
    def _facts(self, clause: str, params: Sequence[Any]) -> list[dict[str, Any]]:
        rows = [dict(row) for row in self.execute("SELECT * FROM research_facts " + clause, params).fetchall()]
        for row in rows:
            row["payload"] = json.loads(row["payload_json"]); row["refs"] = [dict(ref) for ref in self.execute("SELECT ref_fact_id,role,ordinal FROM research_fact_refs WHERE fact_id=? ORDER BY role,ordinal", (row["fact_id"],)).fetchall()]
        return rows
    def get_fact(self, fact_id: str) -> dict[str, Any] | None:
        rows = self._facts("WHERE fact_id=?", (fact_id,)); return rows[0] if rows else None
    def find_fact(self, run_id: str, fact_type: str | Enum, subject_id: str | None = None) -> dict[str, Any] | None:
        params: list[Any] = [run_id, _plain(fact_type)]; suffix = ""
        if subject_id is not None: suffix = " AND subject_id=?"; params.append(subject_id)
        rows = self._facts("WHERE run_id=? AND fact_type=?" + suffix + " ORDER BY fact_id LIMIT 2", params)
        if len(rows) > 1 and subject_id is None: raise IdentityConflict(f"multiple {_plain(fact_type)} facts require subject_id")
        return rows[0] if rows else None
    def list_facts(self, run_id: str, fact_type: str | Enum | None = None) -> tuple[dict[str, Any], ...]:
        if fact_type is None: return tuple(self._facts("WHERE run_id=? ORDER BY fact_id", (run_id,)))
        return tuple(self._facts("WHERE run_id=? AND fact_type=? ORDER BY fact_id", (run_id, _plain(fact_type))))
    def append_fact(self, mutation: Mapping[str, Any] | object) -> bool:
        data = _record(mutation); payload = _json(data.get("payload_json", data.get("payload")))
        values = tuple(payload if field == "payload_json" else data.get(field) for field in _FACT_COLUMNS)
        if any(values[index] is None for index in (0, 1, 2, 3, 4)): raise ValueError("fact identity fields are required")
        refs = tuple(sorted((_text(ref["ref_fact_id"], "ref_fact_id"), _text(ref["role"], "role"), int(ref.get("ordinal", 0))) for ref in data.get("refs", ())))
        old = self.get_fact(str(values[0]))
        if old is not None:
            old_refs = tuple(sorted((ref["ref_fact_id"], ref["role"], ref["ordinal"]) for ref in old["refs"]))
            if tuple(old[field] for field in _FACT_COLUMNS) == values and old_refs == refs: return False
            raise IdentityConflict(f"fact {values[0]!r} conflicts")
        inserted = self._insert("research_facts", _FACT_COLUMNS, values, "fact_id=?", (values[0],), "research fact")
        try: self.executemany("INSERT INTO research_fact_refs(fact_id,ref_fact_id,role,ordinal) VALUES (?,?,?,?)", [(values[0], *ref) for ref in refs])
        except sqlite3.IntegrityError as exc: raise IdentityConflict(f"research reference conflict: {exc}") from exc
        return inserted
    def get_attempt(self, attempt_id: str) -> dict[str, Any] | None: return _dict(self.execute("SELECT * FROM execution_attempts WHERE attempt_id=?", (attempt_id,)).fetchone())
    def list_attempts(self, *, workflow_id: str | None = None, command_id: str | None = None, execution_id: str | None = None) -> tuple[dict[str, Any], ...]:
        filters = [(name, value) for name, value in (("workflow_id", workflow_id), ("command_id", command_id), ("execution_id", execution_id)) if value is not None]
        where = " WHERE " + " AND ".join(f"{name}=?" for name, _ in filters) if filters else ""
        return tuple(dict(row) for row in self.execute("SELECT * FROM execution_attempts" + where + " ORDER BY workflow_id,command_id,attempt_ordinal", [value for _, value in filters]).fetchall())
    def open_attempt(self, attempt: Mapping[str, Any] | object) -> bool:
        data = _record(attempt); attempt_id = _text(data["attempt_id"], "attempt_id"); kind = _plain(data["kind"])
        intent = data.get("observation", data.get("payload"))
        if kind == "AGENT":
            if not isinstance(intent, Mapping) or not {"request"} <= set(intent) <= {"request", "preflight_diagnostics", "session_binding"} or not isinstance(intent["request"], Mapping):
                raise ValueError("AGENT attempt requires typed request intent")
            if "preflight_diagnostics" in intent and not isinstance(intent["preflight_diagnostics"], Mapping):
                raise ValueError("preflight_diagnostics must be an object")
            if "session_binding" in intent:
                from .agent_aci import AgentRequest
                from .agent_sessions import AgentSessions
                request = AgentRequest.from_dict(intent["request"])
                if request.schema_version < 11 or intent["session_binding"] != AgentSessions.binding(request):
                    raise ValueError("session binding differs from its typed Agent owner")
        if kind == "PROCESS" and intent is not None: raise ValueError("PROCESS OPEN attempt cannot carry observation payload")
        observation = None if intent is None else _json(intent)
        owner_sql = "workflow_id=? AND command_id=? AND kind=?"; owner: list[Any] = [data["workflow_id"], data["command_id"], kind]
        if kind == "PROCESS": owner_sql += " AND execution_id=?"; owner.append(data["execution_id"])
        prior = self.execute("SELECT attempt_ordinal,state FROM execution_attempts WHERE " + owner_sql + " ORDER BY attempt_ordinal", owner).fetchall(); ordinal = int(data["attempt_ordinal"])
        if any(row["state"] != "CLOSED" for row in prior): raise IdentityConflict("prior attempt is OPEN")
        if ordinal != (prior[-1]["attempt_ordinal"] + 1 if prior else 1): raise IdentityConflict("attempt ordinal must be contiguous")
        columns = ("attempt_id", "run_id", "workflow_id", "command_id", "kind", "experiment_id", "execution_id", "attempt_ordinal", "host_generation", "state", "started_at", "process_group_id", "observation_json")
        values = (attempt_id, data["run_id"], data["workflow_id"], data["command_id"], kind, data.get("experiment_id"), data.get("execution_id"), ordinal, data["host_generation"], "OPEN", data["started_at"], data.get("process_group_id"), observation)
        return self._insert("execution_attempts", columns, values, "attempt_id=?", (attempt_id,), f"attempt {attempt_id!r}")
    def bind_attempt_process(self, attempt_id: str, process_group_id: int) -> bool:
        current = self.get_attempt(attempt_id)
        if current is None: raise KeyError(f"unknown attempt {attempt_id!r}")
        if isinstance(process_group_id, bool) or not isinstance(process_group_id, int) or process_group_id <= 0: raise ValueError("process_group_id must be a positive integer")
        if current["state"] != "OPEN": raise IdentityConflict("only an OPEN attempt can bind a process group")
        if current["process_group_id"] is not None:
            if current["process_group_id"] == process_group_id: return False
            raise IdentityConflict(f"attempt {attempt_id!r} already binds another process group")
        self.execute("UPDATE execution_attempts SET process_group_id=? WHERE attempt_id=? AND state='OPEN' AND process_group_id IS NULL", (process_group_id, attempt_id)); return True
    def close_attempt(self, attempt_id: str, observation: Mapping[str, Any] | None = None, **fields: Any) -> bool:
        data = {**(observation or {}), **fields}; current = self.get_attempt(attempt_id)
        if current is None: raise KeyError(f"unknown attempt {attempt_id!r}")
        reason = _plain(data.get("terminal_reason", data.get("reason"))); ended = data["ended_at"]; observation = data.get("observation", data.get("payload", {}))
        if current["kind"] == "AGENT" and current["observation_json"] is not None:
            intent = json.loads(current["observation_json"])
            if not isinstance(observation, Mapping) or any(name in observation and observation[name] != value for name, value in intent.items()): raise IdentityConflict("terminal Agent observation conflicts with OPEN request intent")
            observation = {**intent, **observation}
        payload = _json(observation); pgid = data.get("process_group_id", current["process_group_id"]); terminal = (ended, reason, payload, pgid)
        if current["state"] == "CLOSED":
            if tuple(current[name] for name in ("ended_at", "terminal_reason", "observation_json", "process_group_id")) == terminal: return False
            raise IdentityConflict(f"attempt {attempt_id!r} has conflicting terminal")
        self.execute("UPDATE execution_attempts SET state='CLOSED',ended_at=?,terminal_reason=?,observation_json=?,process_group_id=? WHERE attempt_id=? AND state='OPEN'", (*terminal, attempt_id)); return True
    def record_artifact(self, artifact: Mapping[str, Any] | object) -> bool:
        data = _record(artifact); data["schema_version"] = str(data["schema_version"]); columns = ("artifact_id", "run_id", "experiment_id", "execution_id", "attempt_id", "artifact_kind", "schema_version", "digest_algorithm", "digest", "path"); values = tuple(data.get(column) for column in columns); attempt = self.get_attempt(str(data["attempt_id"]))
        if attempt is None or attempt["state"] != "CLOSED" or attempt["terminal_reason"] == "LOST": raise IdentityConflict("artifact requires a committed non-LOST CLOSED attempt")
        if (attempt["run_id"], attempt["experiment_id"], attempt["execution_id"]) != (data["run_id"], data["experiment_id"], data["execution_id"]): raise IdentityConflict("artifact identity differs from attempt")
        expected_path = "/".join(str(data[name]) for name in ("run_id", "experiment_id", "execution_id", "attempt_id", "artifact_kind"))
        if data["path"] != expected_path: raise IdentityConflict("artifact path must equal its stable relative identity tuple")
        return self._insert("execution_artifacts", columns, values, "artifact_id=?", (data["artifact_id"],), "artifact")
    def list_workflow_heads(self, run_id: str, workflow_kind: str | None = None) -> tuple[dict[str, Any], ...]:
        kind = "" if workflow_kind is None else " AND t.workflow_kind=?"; params = (run_id,) if workflow_kind is None else (run_id, workflow_kind)
        sql = "SELECT t.* FROM workflow_transitions t WHERE t.run_id=?" + kind + " AND NOT EXISTS (SELECT 1 FROM workflow_transitions n WHERE n.workflow_id=t.workflow_id AND n.revision>t.revision) ORDER BY t.workflow_id"
        return tuple(dict(row) for row in self.execute(sql, params).fetchall())
    def actionable_runs(self, now: float) -> tuple[dict[str, Any], ...]:
        sql = "SELECT t.* FROM workflow_transitions t WHERE t.workflow_kind='RUN' AND t.state='ACTIVE' AND t.next_command IS NOT NULL AND (t.due_at IS NULL OR t.due_at<=?) AND NOT EXISTS (SELECT 1 FROM workflow_transitions n WHERE n.workflow_id=t.workflow_id AND n.revision>t.revision) AND NOT EXISTS (SELECT 1 FROM workflow_human_decisions d WHERE d.workflow_id=t.workflow_id AND d.state='OPEN') ORDER BY t.run_id"
        return tuple(dict(row) for row in self.execute(sql, (now,)).fetchall())
class StateStore:
    """One state.sqlite; construction without the Host token is read-only."""
    def __init__(self, state_root: str | os.PathLike[str], writer_capability: HostWriterCapability | None = None, *, busy_timeout_ms: int = 5_000) -> None:
        self.state_root = Path(state_root); self.database_path = self.state_root / "state.sqlite"; self._capability = writer_capability; self._timeout = int(busy_timeout_ms); self._db: sqlite3.Connection | None = None
        self._read_cache: dict[object, Any] = {}
        self._read_cache_active = False
        self._read_cache_changes = None
        if self._timeout <= 0: raise ValueError("busy_timeout_ms must be positive")
    @property
    def writable(self) -> bool: return isinstance(self._capability, HostWriterCapability)
    @property
    def total_changes(self) -> int:
        """SQLite's connection-local write count, only a disposable refresh hint."""
        return 0 if self._db is None else self._db.total_changes
    def _check_runtime(self) -> None:
        if tuple(sqlite3.sqlite_version_info) < MINIMUM_SQLITE: raise SQLiteRuntimeError(f"SQLite >= 3.51.3 required; active runtime is {sqlite3.sqlite_version}")
    def _connect(self, read_only: bool, *, check_same_thread: bool = True) -> sqlite3.Connection:
        self._check_runtime(); target = self.database_path.resolve().as_uri() + "?mode=ro" if read_only else str(self.database_path)
        db = sqlite3.connect(target, uri=read_only, isolation_level=None, timeout=self._timeout / 1_000, check_same_thread=check_same_thread); db.row_factory = sqlite3.Row; db.execute(f"PRAGMA busy_timeout={self._timeout}"); db.execute("PRAGMA foreign_keys=ON")
        if read_only:
            db.execute("PRAGMA query_only=ON"); mode = db.execute("PRAGMA journal_mode").fetchone()[0]; sync = db.execute("PRAGMA synchronous").fetchone()[0]
            if mode.lower() != "wal" or int(sync) != 2: db.close(); raise StateStoreError("read-only database is not WAL/synchronous=FULL")
        else:
            if db.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() != "wal": db.close(); raise StateStoreError("could not enable WAL")
            db.execute("PRAGMA synchronous=FULL"); db.execute("PRAGMA wal_autocheckpoint=1000")
        return db
    def initialize(self) -> StateStore:
        if self._db is not None: return self
        if not self.writable:
            if not self.database_path.is_file(): raise WriterCapabilityError("read-only StateStore cannot create state.sqlite")
            self._db = self._connect(True); return self
        existed = self.state_root.exists(); self.state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not existed: self.state_root.chmod(0o700)
        if self.state_root.stat().st_mode & 0o077: raise WriterCapabilityError("state_root must be mode 0700")
        if self.database_path.is_file():
            with sqlite3.connect(self.database_path.resolve().as_uri() + "?mode=ro", uri=True) as probe:
                version = probe.execute("PRAGMA user_version").fetchone()[0]
                if 0 < version < SCHEMA_VERSION and probe.execute("SELECT EXISTS(SELECT 1 FROM workflow_runs) OR EXISTS(SELECT 1 FROM execution_attempts)").fetchone()[0]: raise MigrationError(f"automatic migration refuses nonempty schema {version}; restore its exact bundle or migrate an explicitly authorized copy")
        db = self._connect(False)
        try:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if not 0 <= version <= SCHEMA_VERSION: raise MigrationError(f"unsupported state schema {version}")
            for migration in _MIGRATIONS[version:]: db.executescript(migration.read_text(encoding="utf-8"))
            applied = [row[0] for row in db.execute("SELECT version FROM schema_migrations ORDER BY version")]
            if applied != list(range(1, SCHEMA_VERSION + 1)) or db.execute("PRAGMA foreign_key_check").fetchone() is not None: raise MigrationError("migration metadata or foreign keys invalid")
            os.chmod(self.database_path, 0o600)
        except BaseException: db.close(); raise
        self._db = db; return self
    def close(self) -> None:
        if self._db is not None: self._db.close(); self._db = None
        self._read_cache.clear()
    def __enter__(self) -> StateStore: return self.initialize()
    def __exit__(self, *_: object) -> None: self.close()
    @contextmanager
    def transaction(self) -> Iterator[StateTransaction]:
        if not self.writable: raise WriterCapabilityError("Host writer capability required")
        if self._db is None: self.initialize()
        assert self._db is not None
        if self._db.in_transaction: raise StateStoreError("nested transaction")
        self._db.execute("BEGIN IMMEDIATE"); tx = StateTransaction(self._db)
        try: yield tx; self._db.commit()
        except BaseException: self._db.rollback(); raise
        finally:
            self._read_cache.clear()
    @contextmanager
    def cache_reads(self) -> Iterator[None]:
        """Reuse committed reads while the sole writer's DB is unchanged.

        Transactions invalidate values even outside this scope; callers receive
        their own copies. No external reader uses the writer's change counter.
        """
        if not self.writable or self._read_cache_active:
            yield
            return
        self._read_cache_active = True
        try: yield
        finally: self._read_cache_active = False
    def cached_read(self, key: object, read: Callable[[], Any]) -> Any:
        if not self._read_cache_active or self._db is None or self._db.in_transaction:
            return read()
        if self._read_cache_changes != self.total_changes:
            self._read_cache.clear()
            self._read_cache_changes = self.total_changes
        if key not in self._read_cache: self._read_cache[key] = read()
        return deepcopy(self._read_cache[key])
    @contextmanager
    def readonly_connection(self, *, check_same_thread: bool = True) -> Iterator[sqlite3.Connection]:
        db = self._connect(True, check_same_thread=check_same_thread)
        try: yield db
        finally: db.close()
    def inspect(self, sql: str, parameters: Sequence[Any] = ()) -> tuple[dict[str, Any], ...]:
        def read():
            with self.readonly_connection() as db: return tuple(dict(row) for row in db.execute(sql, tuple(parameters)))
        return self.cached_read(("sql", sql, tuple(parameters)), read)
    def checkpoint_truncate(self) -> tuple[int, int, int]:
        if not self.writable: raise WriterCapabilityError("Host writer capability required")
        if self._db is None: self.initialize()
        assert self._db is not None
        if self._db.in_transaction: raise StateStoreError("checkpoint cannot run inside a transaction")
        return tuple(int(value) for value in self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())  # type: ignore[return-value]
    def _read(self, method: str, *args: Any, **kwargs: Any) -> Any:
        def read():
            with self.readonly_connection() as db: return getattr(StateTransaction(db), method)(*args, **kwargs)
        return self.cached_read(("read", method, args, tuple(kwargs.items())), read)
    def get_run(self, run_id: str) -> dict[str, Any] | None: return self._read("get_run", run_id)
    def latest_transition(self, workflow_id: str) -> dict[str, Any] | None: return self._read("latest_transition", workflow_id)
    def get_fact(self, fact_id: str) -> dict[str, Any] | None: return self._read("get_fact", fact_id)
    def find_fact(self, run_id: str, fact_type: str | Enum, subject_id: str | None = None) -> dict[str, Any] | None: return self._read("find_fact", run_id, fact_type, subject_id)
    def list_facts(self, run_id: str, fact_type: str | Enum | None = None) -> tuple[dict[str, Any], ...]: return self._read("list_facts", run_id, fact_type)
    def list_workflow_heads(self, run_id: str, workflow_kind: str | None = None) -> tuple[dict[str, Any], ...]: return self._read("list_workflow_heads", run_id, workflow_kind)
    def get_attempt(self, attempt_id: str) -> dict[str, Any] | None: return self._read("get_attempt", attempt_id)
    def list_attempts(self, **filters: Any) -> tuple[dict[str, Any], ...]: return self._read("list_attempts", **filters)
    def actionable_runs(self, now: float) -> tuple[dict[str, Any], ...]: return self._read("actionable_runs", now)
    def execute_command(self, command: Mapping[str, Any], operation: Callable[[StateTransaction], Mapping[str, Any]]) -> StoredCommandResult:
        data = _record(command); command_id = _text(data["command_id"], "command_id"); request_json = _json(data.get("request_json", data.get("request")))
        with self.transaction() as tx:
            old = tx.get_command(command_id)
            if old is not None:
                if old["request_json"] != request_json or old["run_id"] != data["run_id"] or old["workflow_id"] != data["workflow_id"] or old["expected_revision"] != data.get("expected_revision") or old["command_type"] != data["command_type"]: raise IdentityConflict(f"command {command_id!r} request conflict")
                return StoredCommandResult(old["response_status"], old["accepted_revision"], None if old["result_json"] is None else json.loads(old["result_json"]), None if old["error_json"] is None else json.loads(old["error_json"]), True)
            head = tx.latest_transition(data["workflow_id"]); current_revision = 0 if head is None else head["revision"]
            if data.get("expected_revision") is not None and data["expected_revision"] != current_revision: raise RevisionConflict(f"expected revision {current_revision}, got {data['expected_revision']}")
            outcome = _record(operation(tx)); tx.record_command({**data, "request_json": request_json, "status": outcome["status"], "result": outcome.get("result"), "error": outcome.get("error"), "revision": outcome.get("revision")})
            return StoredCommandResult(outcome["status"], outcome.get("revision"), outcome.get("result"), outcome.get("error"))
    def create_run(self, run_id: str, version: Mapping[str, Any] | object, **kwargs: Any) -> bool:
        with self.transaction() as tx: return tx.create_run(run_id, version, **kwargs)
    def append_fact_and_transition(self, fact: Any, transition: Any) -> tuple[bool, bool]:
        with self.transaction() as tx: return tx.append_fact(fact), tx.append_transition(transition)
    def open_attempt_and_transition(self, attempt: Any, transition: Any) -> tuple[bool, bool]:
        with self.transaction() as tx: return tx.open_attempt(attempt), tx.append_transition(transition)
    def close_attempt_and_transition(self, attempt_id: str, observation: Mapping[str, Any], transition: Any) -> tuple[bool, bool]:
        with self.transaction() as tx: return tx.close_attempt(attempt_id, observation), tx.append_transition(transition)
    def accept_outcome(self, artifact: Any, fact: Any, transition: Any) -> tuple[bool, bool, bool]:
        with self.transaction() as tx: return tx.record_artifact(artifact), tx.append_fact(fact), tx.append_transition(transition)
