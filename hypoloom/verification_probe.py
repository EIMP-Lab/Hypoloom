"""Bounded, stdlib-only probe execution and observations, not scientific verdicts.

The same file is mounted read-only as the Process entrypoint. Candidate probes
import ``check`` and ``measure`` from ``hypoloom_probe`` inside that Process.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import runpy
import statistics
import sys
import time
import traceback

MAX_CHECKS = 32
MAX_RESULT_BYTES = 64 * 1024
RUNNER_PATH = "verification/_hypoloom_runner.py"
PLAN_PATH = "verification/_hypoloom_plan.json"
RESULT_PATH = "verification-results.json"
RESERVED_PATHS = frozenset({RUNNER_PATH, PLAN_PATH})
_active = None


def validate_checks(checks):
    if not isinstance(checks, list) or not 1 <= len(checks) <= MAX_CHECKS:
        raise ValueError("probe.checks must contain 1..32 dynamic checks")
    seen = set()
    for item in checks:
        if not isinstance(item, dict) or set(item) != {"id", "purpose", "basis", "kind", "required"}:
            raise ValueError("each check requires id, purpose, basis, kind and required")
        key = item["id"]
        if (not isinstance(key, str) or not key or len(key) > 64 or key in seen
                or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in key)):
            raise ValueError("check ids must be unique short ASCII identifiers")
        seen.add(key)
        if any(not isinstance(item[k], str) or not item[k].strip() or len(item[k]) > 2048 for k in ("purpose", "basis")):
            raise ValueError("each check needs a bounded purpose and expected-value basis")
        if item["kind"] not in {"assertion", "measurement"} or type(item["required"]) is not bool:
            raise ValueError("check kind is assertion or measurement; required is boolean")
    if not any(c["kind"] == "assertion" and c["required"] for c in checks):
        raise ValueError("probe requires at least one required implementation assertion")
    return checks


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError("duplicate probe result member")
        result[key] = value
    return result


def decode_result(data):
    if len(data) > MAX_RESULT_BYTES: raise ValueError("probe result exceeds 64 KiB")
    def nonfinite(_): raise ValueError("non-finite probe result")
    try: return json.loads(data, object_pairs_hook=_unique, parse_constant=nonfinite)
    except RecursionError as exc: raise ValueError("probe result nesting exceeds decoder limits") from exc


def validate_result(result, checks, *, attempt_id, report_ref):
    """Validate actual observations against the admitted plan; never infer meaning."""
    validate_checks(checks)
    if not isinstance(result, dict) or set(result) != {"attempt_id", "report_ref", "checks", "errors"}:
        raise ValueError("probe result requires its exact observation fields")
    if result["attempt_id"] != attempt_id or result["report_ref"] != report_ref:
        raise ValueError("probe result belongs to a different Attempt or report")
    errors = result["errors"]
    if not isinstance(errors, list) or len(errors) > 8 or any(not isinstance(e, str) or len(e) > 2048 for e in errors):
        raise ValueError("invalid probe errors")
    rows = result["checks"]
    if not isinstance(rows, list) or len(rows) != len(checks):
        raise ValueError("probe result omits declared checks")
    expected = {c["id"]: c for c in checks}
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "kind", "status", "assertions", "failures", "details", "seconds", "measurement"}:
            raise ValueError("invalid individual probe result")
        key = row["id"]
        if not isinstance(key, str) or key not in expected or key in seen or row["kind"] != expected[key]["kind"]:
            raise ValueError("probe result has duplicate, unknown or mismatched check ids")
        seen.add(key)
        if row["status"] not in {"PASS", "FAIL", "ERROR", "NOT_RUN", "MEASURED"}:
            raise ValueError("invalid check status")
        for field in ("assertions", "failures"):
            if type(row[field]) is not int or row[field] < 0: raise ValueError("invalid assertion count")
        if row["failures"] > row["assertions"]: raise ValueError("failures exceed assertions")
        if type(row["seconds"]) not in {int, float} or not math.isfinite(row["seconds"]) or row["seconds"] < 0:
            raise ValueError("invalid check duration")
        if not isinstance(row["details"], list) or len(row["details"]) > 8 or any(not isinstance(v, str) or len(v) > 512 for v in row["details"]):
            raise ValueError("invalid check details")
        if row["status"] == "PASS" and (row["kind"] != "assertion" or not row["assertions"] or row["failures"]):
            raise ValueError("PASS requires executed assertions without failures")
        if row["failures"] and row["status"] not in {"FAIL", "ERROR"}:
            raise ValueError("failed assertion cannot be overwritten by success")
        measurement = row["measurement"]
        if row["status"] == "MEASURED":
            if row["kind"] != "measurement" or not isinstance(measurement, dict) or set(measurement) != {"warmup", "repeats", "median_seconds", "min_seconds", "max_seconds", "workload", "synchronized"}:
                raise ValueError("MEASURED requires actual timing observations")
            if (type(measurement["warmup"]) is not int or not 0 <= measurement["warmup"] <= 10000
                    or type(measurement["repeats"]) is not int or not 1 <= measurement["repeats"] <= 10000
                    or type(measurement["synchronized"]) is not bool
                    or not isinstance(measurement["workload"], str) or len(measurement["workload"]) > 1024):
                raise ValueError("invalid measurement conditions")
            times = [measurement[k] for k in ("min_seconds", "median_seconds", "max_seconds")]
            if any(type(t) not in {int, float} or not math.isfinite(t) or t < 0 for t in times) or times != sorted(times):
                raise ValueError("invalid timing results")
        elif measurement is not None:
            raise ValueError("only MEASURED supplies timing results")
    passed = not errors and all(r["status"] in {"PASS", "MEASURED"}
        or r["status"] == "NOT_RUN" and not expected[r["id"]]["required"] for r in rows)
    return {"complete": True, "passed": passed, "checks": rows, "errors": errors}


def execution_files(probe, *, attempt_id, report_ref):
    """Host-owned wrapper uses the existing workspace/Attempt identity."""
    plan = {"checks": probe["checks"], "argv": probe["argv"], "attempt_id": attempt_id, "report_ref": report_ref}
    return {RUNNER_PATH: Path(__file__).read_bytes(), PLAN_PATH: json.dumps(plan, allow_nan=False).encode()}


class _Check:
    def __init__(self, owner, key, kind):
        self.owner, self.key, self.kind = owner, key, kind

    def __enter__(self):
        row = self.owner.rows.get(self.key)
        if row is None or row["kind"] != self.kind or self.key in self.owner.started:
            self.owner.error("duplicate, undeclared or wrong-kind check: " + str(self.key)[:128])
            raise ValueError("invalid check registration")
        self.owner.started.add(self.key)
        self.row, self.start = row, time.monotonic()
        return self

    def _record(self, ok, message):
        ok = bool(ok)
        self.row["assertions"] += 1
        if not ok: self.row["failures"] += 1
        if len(self.row["details"]) < 8:
            self.row["details"].append((('PASS: ' if ok else 'FAIL: ') + str(message))[:512])
        return ok

    def true(self, actual, message="condition"):
        return self._record(actual, message)

    def equal(self, actual, expected, message="equality"):
        return self._record(actual == expected, f"{message}: actual={str(actual)[:120]}, expected={str(expected)[:120]}")

    def close(self, actual, expected, *, rtol=1e-7, atol=0.0, message="approximate equality"):
        if any(type(t) not in {int, float} or not math.isfinite(t) or t < 0 for t in (rtol, atol)):
            raise ValueError("tolerances must be finite nonnegative numbers")
        def near(a, b):
            if isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
                return isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)) and len(a) == len(b) and all(near(x, y) for x, y in zip(a, b))
            return math.isfinite(float(a)) and math.isfinite(float(b)) and abs(float(a) - float(b)) <= atol + rtol * abs(float(b))
        return self._record(near(actual, expected), f"{message}: actual={str(actual)[:100]}, expected={str(expected)[:100]}, rtol={rtol}, atol={atol}")

    def __exit__(self, typ, value, tb):
        self.row["seconds"] = time.monotonic() - self.start
        if typ is not None:
            self.row["status"] = "FAIL" if issubclass(typ, AssertionError) else "ERROR"
            if len(self.row["details"]) < 8: self.row["details"].append((typ.__name__ + ': ' + str(value))[:512])
            traceback.print_exception(typ, value, tb)
        elif self.kind == "assertion":
            self.row["status"] = "FAIL" if self.row["failures"] else "PASS" if self.row["assertions"] else "ERROR"
            if not self.row["assertions"]: self.row["details"].append("no assertions executed")
        return typ is not None and issubclass(typ, Exception)


class _Run:
    def __init__(self, plan):
        validate_checks(plan["checks"])
        self.plan, self.errors, self.started = plan, [], set()
        self.rows = {c["id"]: {"id": c["id"], "kind": c["kind"], "status": "NOT_RUN", "assertions": 0,
            "failures": 0, "details": [], "seconds": 0.0, "measurement": None} for c in plan["checks"]}

    def error(self, message):
        if len(self.errors) < 8: self.errors.append(str(message)[:2048])

    def result(self):
        return {"attempt_id": self.plan["attempt_id"], "report_ref": self.plan["report_ref"],
                "checks": list(self.rows.values()), "errors": self.errors}


def check(key):
    if _active is None: raise RuntimeError("checks must run through the Host probe runner")
    return _Check(_active, key, "assertion")


def measure(key, function, *, workload, warmup=1, repeats=5, synchronize=None):
    if _active is None: raise RuntimeError("measurements must run through the Host probe runner")
    with _Check(_active, key, "measurement") as item:
        if (type(warmup) is not int or not 0 <= warmup <= 10000 or type(repeats) is not int or not 1 <= repeats <= 10000
                or not isinstance(workload, str) or not workload.strip() or len(workload) > 1024):
            raise ValueError("measurement needs a workload and bounded warmup/repeats")
        for _ in range(warmup): function()
        samples = []
        for _ in range(repeats):
            if synchronize is not None: synchronize()
            start = time.monotonic()
            function()
            if synchronize is not None: synchronize()
            samples.append(time.monotonic() - start)
        item.row.update(status="MEASURED", measurement={"warmup": warmup, "repeats": repeats,
            "median_seconds": statistics.median(samples), "min_seconds": min(samples), "max_seconds": max(samples),
            "workload": workload, "synchronized": synchronize is not None})


def main():
    global _active
    plan = json.loads((Path(__file__).parent / Path(PLAN_PATH).name).read_text())
    _active = _Run(plan)
    sys.modules["hypoloom_probe"] = sys.modules[__name__]
    source = Path(__file__).parent.parent / plan["argv"][1]
    sys.argv = [str(source), *plan["argv"][2:]]
    try:
        runpy.run_path(str(source), run_name="__main__")
    except BaseException as exc:
        _active.error(type(exc).__name__ + ": " + str(exc))
        traceback.print_exc()
    result = _active.result()
    assessment = validate_result(result, plan["checks"], attempt_id=plan["attempt_id"], report_ref=plan["report_ref"])
    data = json.dumps(result, allow_nan=False, ensure_ascii=True).encode()
    if len(data) > MAX_RESULT_BYTES: raise ValueError("probe result exceeds bounded output")
    target = Path(RESULT_PATH)
    pending = target.with_suffix(".tmp")
    pending.write_bytes(data)
    pending.replace(target)
    print("Implementation checks: " + ", ".join(r["id"] + "=" + r["status"] for r in result["checks"]))
    return 0 if assessment["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
