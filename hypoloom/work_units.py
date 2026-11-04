"""Completed-work reuse inside an admitted Process; no scheduler or verdict.

This file is also seeded into version-bound plugins. Keep it standalone and
standard-library-only. Source/context declarations need project-specific checks;
the Host, not this helper, authenticates prior published inputs.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import time
from typing import Any, Mapping, Sequence


class WorkUnitError(ValueError):
    pass


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise WorkUnitError("work-unit declarations must be finite JSON") from exc


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise WorkUnitError(f"{label} must be nonempty text")
    return value


def _relative(value: Any) -> str:
    _text(value, "output path")
    path = PurePosixPath(value)
    if (not path.parts or path.is_absolute() or path.as_posix() != value or "\\" in value
            or any(part in {".", ".."} for part in path.parts)
            or path.parts[0] == "record.json"):
        raise WorkUnitError("outputs must be safe relative file paths, excluding record.json")
    return value


def _regular(root: Path, relative: str) -> Path:
    path = root
    for part in PurePosixPath(relative).parts:
        path = path / part
        if path.is_symlink():
            raise WorkUnitError("work-unit outputs cannot contain symlinks")
    try:
        metadata = path.stat()
    except FileNotFoundError as exc:
        raise WorkUnitError(f"missing output: {relative}") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise WorkUnitError(f"output is not a regular unlinked file: {relative}")
    return path


def _write(path: Path, value: Mapping) -> None:
    # A killed child cannot leave a partially written completed record.
    fd, temporary = tempfile.mkstemp(prefix=".record-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(_json(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _read(path: Path) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise WorkUnitError("duplicate record field")
            result[key] = value
        return result
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise WorkUnitError("work-unit record must be an object")
    _json(value)
    return value


class WorkUnits:
    """Execute project-defined units serially, reusing compatible completed work.

    ``root`` is a writable output directory of the current Process. Prior roots
    come from HYPOLOOM_REUSE_INPUTS or explicitly supplied read-only inputs in
    local tests. This helper does not grant access or change Process budgets.
    Each child writes its declared files relative to its own working directory.
    The parent still builds the task's actual LOCAL/GOAL result and audits.
    """

    def __init__(self, root: str | Path, *, previous: Sequence[Mapping] | None = None):
        self.root = Path(root)
        if self.root.is_symlink():
            raise WorkUnitError("output root must be a real directory")
        self.root.mkdir(parents=True, exist_ok=True)
        self.units = self.root / "work_units"
        if self.units.is_symlink():
            raise WorkUnitError("work_units must be a real directory")
        self.units.mkdir(exist_ok=True)
        raw = json.loads(os.environ.get("HYPOLOOM_REUSE_INPUTS", "[]")) if previous is None else previous
        if not isinstance(raw, (list, tuple)) or len(raw) > 16:
            raise WorkUnitError("previous publications must be a bounded list")
        self.previous = []
        for item in raw:
            if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
                raise WorkUnitError("previous publication requires its bound path")
            path = Path(item["path"])
            if not path.is_absolute() or path.is_symlink() or not path.is_dir():
                raise WorkUnitError("previous publication must be an existing real absolute directory")
            self.previous.append(dict(item))

    @staticmethod
    def _completed(directory: Path, definition: Mapping) -> dict | None:
        if directory.is_symlink() or not directory.is_dir():
            return None
        try:
            record = _read(_regular(directory, "record.json"))
            if (record.get("schema_version") != 1 or record.get("status") != "completed"
                    or _json(record.get("definition")) != _json(definition)):
                return None
            for relative in definition["outputs"]:
                _regular(directory, relative)
            return record
        except (OSError, ValueError):
            return None

    def run(self, unit_id: str, *, argv: Sequence[str], context: Mapping,
            outputs: Sequence[str], timeout_seconds: float,
            stage: str, purpose: str, basis: str,
            replication_reason: str | None = None) -> dict:
        """Return completed record plus current output paths, or raise on failure.

        Context must declare implementation, data, resolved configuration,
        repetition (null for deterministic work), and protocol. Include effective
        runtime/device conditions too when reusing a performance measurement.
        Stage labels are provenance, never an automatic promotion/acceptance rule.
        """
        if not isinstance(unit_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", unit_id) is None:
            raise WorkUnitError("unit_id must be a safe, bounded identifier")
        if not isinstance(argv, (list, tuple)) or not argv:
            raise WorkUnitError("argv must be a nonempty sequence")
        command = [_text(item, "argv item") for item in argv]
        if not isinstance(context, Mapping) or not {"implementation", "data", "configuration", "repetition", "protocol"} <= context.keys():
            raise WorkUnitError("context requires implementation, data, configuration, repetition and protocol")
        if not isinstance(outputs, (list, tuple)) or not outputs:
            raise WorkUnitError("outputs must be a nonempty sequence")
        paths = [_relative(item) for item in outputs]
        if len(paths) != len(set(paths)):
            raise WorkUnitError("duplicate output path")
        if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise WorkUnitError("timeout_seconds must be finite and positive")
        if purpose not in {"exploration", "confirmation", "evaluation", "performance"}:
            raise WorkUnitError("unknown work purpose")
        if purpose == "performance" and not context.get("runtime"):
            raise WorkUnitError("performance reuse requires declared runtime/workload conditions")
        _text(stage, "stage"); _text(basis, "basis")
        if replication_reason is not None:
            _text(replication_reason, "replication_reason")
        definition = json.loads(_json({"argv": command, "context": dict(context), "outputs": paths}))
        directory = self.units / unit_id
        current = self._completed(directory, definition)
        if directory.exists():
            if current is not None and replication_reason is None:
                return self._result(directory, current)
            raise WorkUnitError("unit_id already used in this Process; use a new ID for replication or changed work")
        directory.mkdir()
        record = {"schema_version": 1, "unit_id": unit_id, "definition": definition,
                  "stage": stage, "purpose": purpose, "basis": basis,
                  "status": "running", "replication_reason": replication_reason,
                  "reused_from": None}
        _write(directory / "record.json", record)
        started = time.monotonic()
        try:
            reused = None
            if replication_reason is None:
                for source in self.previous:
                    for prefix in ("raw/work_units", "work_units"):
                        prior = Path(source["path"]) / prefix / unit_id
                        if self._completed(prior, definition) is not None:
                            reused = (source, prior)
                            break
                    if reused is not None:
                        break
            if reused is not None:
                source, prior = reused
                for relative in paths:
                    destination = directory / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(_regular(prior, relative), destination)
                record["reused_from"] = {**source, "unit_id": unit_id}
                print(f"[work-unit] {stage}/{unit_id} reused", flush=True)
            else:
                print(f"[work-unit] {stage}/{unit_id} started purpose={purpose}", flush=True)
                completed = subprocess.run(command, cwd=directory, timeout=timeout_seconds, check=False)
                if completed.returncode != 0:
                    raise WorkUnitError(f"unit {unit_id} exited with {completed.returncode}")
            for relative in paths:
                _regular(directory, relative)
            record.update(status="completed", elapsed_seconds=time.monotonic() - started)
            _write(directory / "record.json", record)
            print(f"[work-unit] {stage}/{unit_id} completed elapsed={record['elapsed_seconds']:.3f}s", flush=True)
            return self._result(directory, record)
        except BaseException as exc:
            # Keep completed siblings. A failed record is diagnostic only and is
            # never reusable; Host timeout/cancel still owns descendant cleanup.
            record.update(status="failed", elapsed_seconds=time.monotonic() - started,
                          error=f"{type(exc).__name__}: {exc}"[:2048])
            _write(directory / "record.json", record)
            raise

    @staticmethod
    def _result(directory: Path, record: Mapping) -> dict:
        return {**record, "files": {name: str(directory / name)
                for name in record["definition"]["outputs"]}}
