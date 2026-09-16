"""Node-local transactional GPU capacity authority, independent of run databases.

The sampler is trusted service code, never a client input. Its resident mapping
must contain only current, complete owned-process observations. Missing entries
remain unknown and earn no credit. The default service supplies no attribution.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .compute_resources import GpuRuntimeObservation, validate_gpu_uuid
from .runner import process_group_exists


class PoolError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= 2**53 - 1:
        raise PoolError("invalid_argument", f"{label} must be an exact JSON integer >= {minimum}")
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", value):
        raise PoolError("invalid_argument", f"{label} must be a non-path identifier")
    return value


def _fields(value: Any, expected: set[str], label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise PoolError("invalid_argument", f"{label} fields must be {sorted(expected)}")


def _uuids(value: Any) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise PoolError("invalid_argument", "allowed_gpu_uuids must be a nonempty sequence")
    try:
        for uuid in value:
            validate_gpu_uuid(uuid)
        if len(set(value)) != len(value):
            raise ValueError("duplicate GPU UUID")
    except (ValueError, TypeError) as exc:
        raise PoolError("invalid_argument", str(exc)) from exc
    return tuple(sorted(value))


@dataclass(frozen=True, slots=True)
class RequestKey:
    uid: int
    namespace: str
    run_id: str
    execution_id: str
    attempt_id: str

    def __post_init__(self) -> None:
        _integer(self.uid, "uid")
        for name in ("namespace", "run_id", "execution_id", "attempt_id"):
            _identifier(getattr(self, name), name)

    def to_dict(self) -> dict[str, Any]:
        return {"uid": self.uid, "namespace": self.namespace, "run_id": self.run_id,
                "execution_id": self.execution_id, "attempt_id": self.attempt_id}


@dataclass(frozen=True, slots=True)
class CapacitySample:
    devices: tuple[GpuRuntimeObservation, ...]
    resident_memory_mib: Mapping[tuple[RequestKey, str], int | None] = field(default_factory=dict)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    request_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    uid INTEGER NOT NULL,
    namespace TEXT NOT NULL,
    run_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('QUEUED','RESERVED','LAUNCHING','BOUND','RELEASED')),
    intent_json TEXT NOT NULL,
    grant_json TEXT NOT NULL DEFAULT '[]',
    decision_json TEXT,
    process_group_id INTEGER,
    reason TEXT,
    UNIQUE(uid,namespace,run_id,execution_id,attempt_id)
);
CREATE TABLE IF NOT EXISTS runs (
    uid INTEGER NOT NULL,
    namespace TEXT NOT NULL,
    run_id TEXT NOT NULL,
    last_grant INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(uid,namespace,run_id)
);
"""
_KEY_FIELDS = {"namespace", "run_id", "execution_id", "attempt_id"}
_INTENT_FIELDS = {"allowed_gpu_uuids", "gpu_count", "workload_memory_mib", "reserve_memory_mib"}
_ACTIVE = ("RESERVED", "LAUNCHING", "BOUND")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _key(row: Mapping[str, Any]) -> RequestKey:
    return RequestKey(**{name: row[name] for name in ("uid", *_KEY_FIELDS)})


def _run(row: Mapping[str, Any]) -> tuple[int, str, str]:
    return row["uid"], row["namespace"], row["run_id"]


def _private_storage(path: Path, *, directory: bool = False) -> None:
    value = path.lstat()
    correct_type = stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode)
    if not correct_type or value.st_uid != os.getuid() or value.st_mode & 0o077:
        raise PoolError("invalid_storage", "pool storage must be owned, private and not a symlink")


class GpuPool:
    """Sole dispatcher for membership, intent, capacity and process retirement.

    Each operation opens an ordinary SQLite transaction. The server's standard
    file lock separately establishes service ownership; independent connection
    transactions also prevent accidental overlapping callers from overpromising.
    """

    def __init__(self, root: Path, *, members: Mapping[int, Sequence[str]],
                 reserve_memory_mib: int, sampler: Callable[[], CapacitySample],
                 process_present: Callable[[int], bool | None] = process_group_exists,
                 attribution_sampler: Callable[[tuple[GpuRuntimeObservation, ...], Mapping[RequestKey, int]], Mapping[tuple[RequestKey, str], int | None]] | None = None):
        if not isinstance(members, Mapping) or not members:
            raise PoolError("invalid_argument", "members must map UIDs to authorized GPU UUIDs")
        self.members = {_integer(uid, "member uid"): _uuids(uuids)
                        for uid, uuids in members.items()}
        self.reserve_memory_mib = _integer(reserve_memory_mib, "pool reserve_memory_mib")
        if not callable(sampler) or not callable(process_present):
            raise PoolError("invalid_argument", "sampler and process_present must be callable")
        self.root = Path(root)
        self.sampler = sampler
        self.attribution_sampler = attribution_sampler
        self.process_present = process_present
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        except FileExistsError:
            pass
        _private_storage(self.root, directory=True)
        self.database_path = self.root / "pool.sqlite3"
        try:
            self.database_path.touch(mode=0o600, exist_ok=False)
        except FileExistsError:
            pass
        _private_storage(self.database_path)
        with closing(self._connect()) as db:
            db.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database_path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def handle(self, uid: int, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        _integer(uid, "authenticated uid")
        if uid not in self.members:
            raise PoolError("unauthorized", "UID is not a pool member")
        if not isinstance(operation, str) or operation not in {
            "snapshot", "reserve", "status", "arm", "activate", "bind_process", "release",
        }:
            raise PoolError("invalid_argument", "unknown pool operation")
        expected = set() if operation == "snapshot" else {"key"}
        if operation == "reserve":
            expected |= _INTENT_FIELDS
        elif operation in {"activate", "bind_process"}:
            expected.add("process_group_id")
        _fields(arguments, expected, "operation arguments")
        key = None
        intent = None
        if operation != "snapshot":
            _fields(arguments["key"], _KEY_FIELDS, "request key")
            key = RequestKey(uid=uid, **arguments["key"])
        if operation == "reserve":
            intent = self._intent(uid, arguments)
        if operation in {"activate", "bind_process"}:
            _integer(arguments["process_group_id"], "process_group_id", 2)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if operation == "snapshot":
                return self._snapshot(db, uid)
            row = self._find(db, key)
            if operation == "reserve":
                if row is None:
                    db.execute("INSERT OR IGNORE INTO runs(uid,namespace,run_id) VALUES(?,?,?)",
                               (uid, key.namespace, key.run_id))
                    db.execute(
                        "INSERT INTO requests(uid,namespace,run_id,execution_id,attempt_id,state,intent_json) "
                        "VALUES(?,?,?,?,?,'QUEUED',?)",
                        (*key.to_dict().values(), _json(intent)),
                    )
                    row = self._find(db, key)
                elif row["intent_json"] != _json(intent):
                    raise PoolError("intent_conflict", "request key already has different resource intent")
            if row is None:
                raise PoolError("not_found", "request key does not exist for this owner")
            if operation in {"reserve", "status", "arm", "activate"} and row["state"] == "QUEUED":
                self._schedule(db)
                row = self._find(db, key)
            if operation == "arm":
                self._arm(db, row)
            elif operation == "activate":
                self._activate(db, row, arguments["process_group_id"])
            elif operation == "bind_process":
                self._bind(db, row, arguments["process_group_id"])
            elif operation == "release":
                self._release(db, row)
                self._schedule(db)
            return self._response(self._find(db, key))

    def _intent(self, uid: int, arguments: Mapping[str, Any]) -> dict[str, Any]:
        allowed = _uuids(arguments["allowed_gpu_uuids"])
        if not set(allowed).issubset(self.members[uid]):
            raise PoolError("unauthorized", "request includes a GPU outside this UID's membership")
        count = _integer(arguments["gpu_count"], "gpu_count", 1)
        if count > len(allowed):
            raise PoolError("invalid_argument", "gpu_count exceeds allowed GPU identities")
        return {"allowed_gpu_uuids": list(allowed), "gpu_count": count,
                "workload_memory_mib": _integer(arguments["workload_memory_mib"], "workload_memory_mib", 1),
                "reserve_memory_mib": _integer(arguments["reserve_memory_mib"], "reserve_memory_mib")}

    @staticmethod
    def _find(db: sqlite3.Connection, key: RequestKey) -> sqlite3.Row | None:
        return db.execute(
            "SELECT * FROM requests WHERE uid=? AND namespace=? AND run_id=? "
            "AND execution_id=? AND attempt_id=?", tuple(key.to_dict().values()),
        ).fetchone()

    @staticmethod
    def _response(row: sqlite3.Row) -> dict[str, Any]:
        return {"key": {name: row[name] for name in sorted(_KEY_FIELDS)},
                "state": row["state"], "gpu_uuids": json.loads(row["grant_json"]),
                "process_group_id": row["process_group_id"], "reason": row["reason"]}

    @staticmethod
    def _active(db: sqlite3.Connection) -> list[sqlite3.Row]:
        return db.execute("SELECT * FROM requests WHERE state IN ('RESERVED','LAUNCHING','BOUND') "
                          "ORDER BY request_seq").fetchall()

    def _sample(self, active: Sequence[sqlite3.Row]) -> CapacitySample:
        try:
            sample = self.sampler()
        except Exception as exc:
            raise PoolError("telemetry_unavailable", "service capacity sampler failed") from exc
        try:
            if not isinstance(sample, CapacitySample) or not isinstance(sample.devices, tuple):
                raise ValueError("sampler must return CapacitySample with tuple devices")
            if self.attribution_sampler is not None:
                roots = {_key(row): row["process_group_id"] for row in active if row["state"] == "BOUND"}
                try:
                    resident = self.attribution_sampler(sample.devices, roots)
                except Exception:
                    resident = {}  # Unknown attribution carries no resident credit.
                sample = CapacitySample(sample.devices, resident)
            devices = {}
            for item in sample.devices:
                if not isinstance(item, GpuRuntimeObservation) or item.uuid in devices:
                    raise ValueError("runtime devices must be typed and unique")
                GpuRuntimeObservation(**item.to_dict())
                for name in ("total_memory_mib", "free_memory_mib", "used_memory_mib"):
                    _integer(getattr(item, name), name)
                devices[item.uuid] = item
            if not isinstance(sample.resident_memory_mib, Mapping):
                raise ValueError("resident samples must be a mapping")
            for identity, resident in sample.resident_memory_mib.items():
                if (not isinstance(identity, tuple) or len(identity) != 2
                        or not isinstance(identity[0], RequestKey)):
                    raise ValueError("resident sample must have request key and GPU identity")
                validate_gpu_uuid(identity[1])
                if resident is not None:
                    _integer(resident, "resident_memory_mib")
            totals = {uuid: 0 for uuid in devices}
            for row in active:
                if row["state"] != "BOUND":
                    continue
                for uuid in json.loads(row["grant_json"]):
                    resident = sample.resident_memory_mib.get((_key(row), uuid))
                    if resident is not None:
                        if uuid not in devices:
                            raise ValueError("resident credit lacks device observation")
                        totals[uuid] += resident
            if any(totals[uuid] > item.used_memory_mib for uuid, item in devices.items()):
                raise ValueError("attributed resident memory exceeds device used memory")
            return sample
        except (ValueError, TypeError, AttributeError) as exc:
            raise PoolError("invalid_sample", "service capacity sample is incoherent") from exc

    def _capacity(self, sample: CapacitySample, active: Sequence[sqlite3.Row],
                  uuid: str, budget: int = 0, reserve: int = 0) -> dict[str, Any]:
        device = next((item for item in sample.devices if item.uuid == uuid), None)
        if device is None:
            raise PoolError("missing_inventory", "an authorized GPU is absent from live inventory")
        commitments = []
        outstanding = 0
        shared_reserve = max(self.reserve_memory_mib, reserve)
        for row in active:
            if uuid not in json.loads(row["grant_json"]):
                continue
            intent = json.loads(row["intent_json"])
            resident = (sample.resident_memory_mib.get((_key(row), uuid))
                        if row["state"] == "BOUND" else None)
            workload = intent["workload_memory_mib"]
            outstanding += max(0, workload - (resident or 0))
            shared_reserve = max(shared_reserve, intent["reserve_memory_mib"])
            commitments.append({"request_seq": row["request_seq"], "state": row["state"],
                                "workload_memory_mib": workload, "resident_memory_mib": resident})
        return {"gpu_uuid": uuid, "total_memory_mib": device.total_memory_mib,
                "free_memory_mib": device.free_memory_mib, "used_memory_mib": device.used_memory_mib,
                "workload_memory_mib": budget, "reserve_memory_mib": shared_reserve,
                "outstanding_memory_mib": outstanding, "commitments": commitments}

    def _schedule(self, db: sqlite3.Connection) -> None:
        queued = db.execute("SELECT * FROM requests WHERE state='QUEUED' ORDER BY request_seq").fetchall()
        if not queued:
            return
        active = self._active(db)
        try:
            sample = self._sample(active)
        except PoolError as exc:
            db.execute("UPDATE requests SET reason=? WHERE state='QUEUED'", (exc.code,))
            return
        while queued:
            heads = {}
            for row in queued:
                heads.setdefault(_run(row), row)
            last = {_run(row): row["last_grant"] for row in db.execute("SELECT * FROM runs")}
            ordered = sorted(heads.values(), key=lambda row: (last[_run(row)], row["request_seq"]))
            blocked: set[str] = set()
            admitted = set()
            for row in ordered:
                intent = json.loads(row["intent_json"])
                allowed = intent["allowed_gpu_uuids"]
                if set(allowed) & blocked:
                    # A multi-device head may still use disjoint eligible devices.
                    eligible = [uuid for uuid in allowed if uuid not in blocked]
                else:
                    eligible = allowed
                try:
                    if not set(allowed).issubset(self.members.get(row["uid"], ())):
                        raise PoolError("unauthorized", "queued request membership was withdrawn")
                    all_capacity = [self._capacity(sample, active, uuid, intent["workload_memory_mib"],
                                                   intent["reserve_memory_mib"]) for uuid in allowed]
                    feasible = [item for item in all_capacity if item["gpu_uuid"] in eligible
                                and item["free_memory_mib"] - item["outstanding_memory_mib"]
                                >= item["workload_memory_mib"] + item["reserve_memory_mib"]]
                    feasible.sort(key=lambda item: (-(item["free_memory_mib"] - item["outstanding_memory_mib"]
                                                       - item["reserve_memory_mib"]), item["gpu_uuid"]))
                    if len(feasible) < intent["gpu_count"]:
                        reason = "fairness_wait" if set(allowed) & blocked else "capacity_unavailable"
                        raise PoolError(reason, "run head cannot fit")
                    chosen = sorted(feasible[:intent["gpu_count"]], key=lambda item: item["gpu_uuid"])
                except PoolError as exc:
                    db.execute("UPDATE requests SET reason=? WHERE request_seq=?", (exc.code, row["request_seq"]))
                    blocked.update(allowed)
                    continue
                db.execute("UPDATE requests SET state='RESERVED',grant_json=?,decision_json=?,reason=NULL "
                           "WHERE request_seq=?", (_json([item["gpu_uuid"] for item in chosen]),
                           _json({"phase": "reserve", "devices": chosen}), row["request_seq"]))
                grant_order = db.execute("SELECT COALESCE(MAX(last_grant),0)+1 FROM runs").fetchone()[0]
                db.execute("UPDATE runs SET last_grant=? WHERE uid=? AND namespace=? AND run_id=?",
                           (grant_order, *_run(row)))
                admitted.add(row["request_seq"])
                active.append(db.execute("SELECT * FROM requests WHERE request_seq=?", (row["request_seq"],)).fetchone())
            if not admitted:
                break
            queued = [row for row in queued if row["request_seq"] not in admitted]

    def _arm(self, db: sqlite3.Connection, row: sqlite3.Row, pgid: int | None = None) -> None:
        if row["state"] != "RESERVED":
            return
        active = self._active(db)
        intent = json.loads(row["intent_json"])
        try:
            if not set(json.loads(row["grant_json"])).issubset(self.members[row["uid"]]):
                raise PoolError("unauthorized", "reserved GPU membership was withdrawn")
            sample = self._sample(active)
            others = [item for item in active if item["request_seq"] != row["request_seq"]]
            capacities = [self._capacity(sample, others, uuid, intent["workload_memory_mib"],
                                         intent["reserve_memory_mib"]) for uuid in json.loads(row["grant_json"])]
            if any(item["free_memory_mib"] - item["outstanding_memory_mib"]
                   < item["workload_memory_mib"] + item["reserve_memory_mib"] for item in capacities):
                raise PoolError("capacity_unavailable", "reserved GPU lost live capacity before launch")
        except PoolError as exc:
            db.execute("UPDATE requests SET reason=? WHERE request_seq=?", (exc.code, row["request_seq"]))
            return
        db.execute("UPDATE requests SET state=?,process_group_id=?,decision_json=?,reason=NULL "
                   "WHERE request_seq=?", ("LAUNCHING" if pgid is None else "BOUND", pgid,
                   _json({"phase": "arm", "devices": capacities}), row["request_seq"]))

    def _activate(self, db: sqlite3.Connection, row: sqlite3.Row, pgid: int) -> None:
        """Bind a Host-registered, held child only after the live launch check."""
        if row["state"] in {"QUEUED", "RESERVED"}:
            self._arm(db, row, pgid)
        elif row["state"] == "BOUND" or (row["state"] == "RELEASED" and row["process_group_id"] is not None):
            self._bind(db, row, pgid)
        else:
            raise PoolError("invalid_state", "activate requires an unarmed reservation or its bound replay")

    @staticmethod
    def _bind(db: sqlite3.Connection, row: sqlite3.Row, pgid: int) -> None:
        if row["state"] == "BOUND" or (row["state"] == "RELEASED" and row["process_group_id"] is not None):
            if row["process_group_id"] != pgid:
                raise PoolError("process_conflict", "request already bound to another process group")
            return
        if row["state"] != "LAUNCHING":
            raise PoolError("invalid_state", "bind_process requires an armed request")
        db.execute("UPDATE requests SET state='BOUND',process_group_id=?,reason=NULL WHERE request_seq=?",
                   (pgid, row["request_seq"]))

    def _release(self, db: sqlite3.Connection, row: sqlite3.Row) -> None:
        if row["state"] == "RELEASED":
            return
        reason = None
        if row["state"] == "LAUNCHING":
            reason = "recovery_required"
        elif row["state"] == "BOUND":
            try:
                present = self.process_present(row["process_group_id"])
            except Exception:
                present = None
            if present is not False:
                reason = "process_present" if present is True else "process_unknown"
        if reason:
            db.execute("UPDATE requests SET reason=? WHERE request_seq=?", (reason, row["request_seq"]))
        else:
            db.execute("UPDATE requests SET state='RELEASED',reason=NULL WHERE request_seq=?", (row["request_seq"],))

    def _snapshot(self, db: sqlite3.Connection, uid: int) -> dict[str, Any]:
        active = self._active(db)
        sampling_started_at = time.time()
        sample = self._sample(active)
        sampled_at = time.time()
        result = []
        for uuid in self.members[uid]:
            capacity = self._capacity(sample, active, uuid)
            commitments = capacity.pop("commitments")
            capacity.pop("workload_memory_mib")
            capacity["active_commitments"] = len(commitments)
            capacity["unknown_attribution_commitments"] = sum(
                item["resident_memory_mib"] is None for item in commitments)
            capacity["allocatable_memory_mib"] = max(0, capacity["free_memory_mib"]
                - capacity["outstanding_memory_mib"] - capacity["reserve_memory_mib"])
            result.append(capacity)
        return {"sampling_started_at": sampling_started_at, "sampled_at": sampled_at,
                "devices": result}


__all__ = ["CapacitySample", "GpuPool", "PoolError", "RequestKey"]
