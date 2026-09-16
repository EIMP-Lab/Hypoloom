from __future__ import annotations

import json
import sqlite3

import pytest

from hypoloom import oracle


GPU = "GPU-11111111-1111-1111-1111-111111111111"


def _raw_pool(tmp_path):
    """Raw records, deliberately independent of the pool writer/reducer."""
    path = tmp_path / "pool.sqlite3"
    intent = {"allowed_gpu_uuids": [GPU], "gpu_count": 1,
              "workload_memory_mib": 8000, "reserve_memory_mib": 1000}
    decision = {"phase": "reserve", "devices": [{
        "gpu_uuid": GPU, "total_memory_mib": 16000, "free_memory_mib": 10000,
        "used_memory_mib": 6000, "workload_memory_mib": 8000,
        "reserve_memory_mib": 1000, "outstanding_memory_mib": 0, "commitments": [],
    }]}
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE requests(request_seq INTEGER PRIMARY KEY, uid INTEGER,
                namespace TEXT, run_id TEXT, execution_id TEXT, attempt_id TEXT,
                state TEXT, intent_json TEXT, grant_json TEXT, decision_json TEXT,
                process_group_id INTEGER, reason TEXT);
            CREATE TABLE runs(uid INTEGER, namespace TEXT, run_id TEXT, last_grant INTEGER);
        """)
        db.execute("INSERT INTO requests VALUES(1,1000,'host-a','run','exec','attempt',"
                   "'RESERVED',?,?,?,NULL,NULL)",
                   (json.dumps(intent), json.dumps([GPU]), json.dumps(decision)))
        db.execute("INSERT INTO runs VALUES(1000,'host-a','run',1)")
    return path


def test_pool_audit_reads_raw_rows_without_mutating_them(tmp_path):
    path = _raw_pool(tmp_path)
    before = path.read_bytes()
    report = oracle.audit_gpu_pool(tmp_path)
    assert report["ok"] is True
    assert report["issues"] == []
    assert report["counts"]["RESERVED"] == 1
    assert path.read_bytes() == before


@pytest.mark.parametrize("mutation, code", [
    ("capacity", "gpu_pool_capacity_exceeded"),
    ("grant", "gpu_pool_grant_invalid"),
    ("intent", "gpu_pool_intent_invalid"),
    ("outstanding", "gpu_pool_accounting_mismatch"),
    ("missing_decision", "gpu_pool_decision_invalid"),
    ("unbound", "gpu_pool_process_binding_invalid"),
    ("duplicate_key", "gpu_pool_duplicate_identity"),
    ("missing_run", "gpu_pool_run_missing"),
    ("malformed_json", "gpu_pool_intent_invalid"),
])
def test_pool_audit_independently_rejects_corrupted_records(tmp_path, mutation, code):
    path = _raw_pool(tmp_path)
    with sqlite3.connect(path) as db:
        if mutation == "capacity":
            decision = json.loads(db.execute("SELECT decision_json FROM requests").fetchone()[0])
            decision["devices"][0].update(free_memory_mib=8000, used_memory_mib=8000)
            db.execute("UPDATE requests SET decision_json=?", (json.dumps(decision),))
        elif mutation == "grant":
            db.execute("UPDATE requests SET grant_json='[]'")
        elif mutation == "intent":
            intent = json.loads(db.execute("SELECT intent_json FROM requests").fetchone()[0])
            intent["workload_memory_mib"] = True
            db.execute("UPDATE requests SET intent_json=?", (json.dumps(intent),))
        elif mutation == "outstanding":
            decision = json.loads(db.execute("SELECT decision_json FROM requests").fetchone()[0])
            decision["devices"][0]["outstanding_memory_mib"] = 1
            db.execute("UPDATE requests SET decision_json=?", (json.dumps(decision),))
        elif mutation == "missing_decision":
            db.execute("UPDATE requests SET decision_json=NULL")
        elif mutation == "unbound":
            db.execute("UPDATE requests SET state='BOUND'")
        elif mutation == "duplicate_key":
            db.execute("INSERT INTO requests SELECT 2,uid,namespace,run_id,execution_id,attempt_id,"
                       "state,intent_json,grant_json,decision_json,process_group_id,reason FROM requests")
        elif mutation == "missing_run":
            db.execute("DELETE FROM runs")
        elif mutation == "malformed_json":
            db.execute("UPDATE requests SET intent_json='{' ")
    result = oracle.audit_gpu_pool(tmp_path)
    assert result["ok"] is False
    assert code in {issue["code"] for issue in result["issues"]}


def test_pool_audit_does_not_create_missing_database(tmp_path):
    root = tmp_path / "absent"
    report = oracle.audit_gpu_pool(root)
    assert report["ok"] is False
    assert not root.exists()


def test_pool_audit_accepts_writer_bound_credit_and_persistent_release(tmp_path):
    from hypoloom.compute_resources import GpuRuntimeObservation
    from hypoloom.gpu_pool import CapacitySample, GpuPool, RequestKey

    key = {"namespace": "root", "run_id": "run", "execution_id": "exec", "attempt_id": "one"}
    request = {"key": key, "allowed_gpu_uuids": [GPU], "gpu_count": 1,
               "workload_memory_mib": 8000, "reserve_memory_mib": 1000}
    sample = CapacitySample((GpuRuntimeObservation(uuid=GPU, total_memory_mib=16000,
        free_memory_mib=16000, used_memory_mib=0, utilization_percent=0),))
    pool = GpuPool(tmp_path, members={1000: [GPU]}, reserve_memory_mib=1000,
        sampler=lambda: sample, process_present=lambda group: False)
    assert pool.handle(1000, "reserve", request)["state"] == "RESERVED"
    assert pool.handle(1000, "arm", {"key": key})["state"] == "LAUNCHING"
    pool.handle(1000, "bind_process", {"key": key, "process_group_id": 20000})
    sample = CapacitySample((GpuRuntimeObservation(uuid=GPU, total_memory_mib=16000,
        free_memory_mib=10000, used_memory_mib=6000, utilization_percent=80),),
        {(RequestKey(uid=1000, **key), GPU): 6000})
    second = {**key, "attempt_id": "two"}
    assert pool.handle(1000, "reserve", {**request, "key": second, "workload_memory_mib": 7000})["state"] == "RESERVED"
    assert oracle.audit_gpu_pool(tmp_path)["ok"] is True
    pool.handle(1000, "release", {"key": key})
    pool.handle(1000, "release", {"key": second})
    report = oracle.audit_gpu_pool(tmp_path)
    assert report["ok"] is True
    assert report["counts"]["RELEASED"] == 2


def test_pool_audit_rejects_fabricated_credit_that_hides_excess_capacity(tmp_path):
    path = _raw_pool(tmp_path)
    with sqlite3.connect(path) as db:
        first = db.execute("SELECT * FROM requests").fetchone()
        # The second decision omits 8,000 MiB still promised by the first.
        second_decision = json.loads(first[9])
        second_decision["devices"][0]["commitments"] = [{
            "request_seq": 1, "state": "BOUND", "workload_memory_mib": 8000,
            "resident_memory_mib": 8000,
        }]
        db.execute("INSERT INTO requests VALUES(2,1000,'host-b','run','exec','attempt',"
                   "'RESERVED',?,?,?,NULL,NULL)", (first[7], first[8], json.dumps(second_decision)))
        db.execute("INSERT INTO runs VALUES(1000,'host-b','run',2)")
    report = oracle.audit_gpu_pool(tmp_path)
    assert report["ok"] is False
    assert "gpu_pool_decision_invalid" in {issue["code"] for issue in report["issues"]}
