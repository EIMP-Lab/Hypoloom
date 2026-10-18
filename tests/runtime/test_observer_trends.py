"""Historical charts reuse recorded intervals and relay starts, not UI samples."""
import csv
import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from hypoloom.observer_trends import build_run_trends, trends_csv
from hypoloom.provider_traffic import ProviderTraffic
from hypoloom.runtime_diagnostics import RuntimeDiagnostics
from hypoloom.state_store import HostWriterCapability, StateStore
from hypoloom.support_bundle import build_support_bundle
from test_debug_mode import _version, _seed_agent_retry, _attempt_census
from test_provider_relay import upstream as _upstream_fixture, _relay, _request, _RESPONSE
from test_provider_traffic import until

upstream = _upstream_fixture


def _seed(root):
    store = StateStore(root, HostWriterCapability.issue()).initialize()
    store.create_run("trends", _version())
    with store.transaction() as tx:
        for name, kind, start, end, role, task, parent, bound in [
            ("a", "AGENT", 0, 180, "private-expert", "EXPERIMENT_DESIGN_IMPLEMENT", None, True),
            ("b", "AGENT", 60, 120, "other", "EXPERIMENT_DESIGN_IMPLEMENT", None, True),
            ("r", "AGENT", 30, 100, "reader", "PAPER_READ", "a", True),
            ("g", "AGENT", 0, 15, "grounding", "GROUNDING", None, True),
            ("p1", "PROCESS", 10, 100, None, None, None, True),
            ("p2", "PROCESS", 90, 150, None, None, None, True),
            ("prelaunch", "PROCESS", 10, 11, None, None, None, False),
        ]:
            tx.record_command(dict(command_id=name, run_id="trends", workflow_id="trends",
                expected_revision=0, command_type="REQUEST_AUXILIARY", status="accepted", result={},
                request={"paper_reading": {"parent_attempt_id": parent}} if parent else {}))
            tx.open_attempt(dict(attempt_id=name, run_id="trends", workflow_id="trends", command_id=name,
                kind=kind, experiment_id=name if kind == "PROCESS" else None,
                execution_id=name if kind == "PROCESS" else None, attempt_ordinal=1,
                host_generation="fixture", started_at=1000 + start,
                payload={"request": {"role": role, "command": task}} if kind == "AGENT" else None))
            if bound:
                tx.bind_attempt_process(name, process_group_id=12345)
            tx.close_attempt(name, terminal_reason="SUCCEEDED", ended_at=1000 + end, payload={})
    store.close()


def _exchange(root, attempt, number, **data):
    path = root / "diagnostics/agents/trends" / attempt / "provider" / f"{number:06d}" / "exchange.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ended_at": 1180, **data}))
    return path


def test_rates_ownership_peaks_and_unknown_are_independent_of_browser(tmp_path):
    _seed(tmp_path)
    _exchange(tmp_path, "a", 1, upstream_started_at=1010)
    _exchange(tmp_path, "a", 2, upstream_started_at=1059, upstream_status=429)
    _exchange(tmp_path, "a", 3, failure_stage="provider_queue", request_bytes_forwarded=0)
    _exchange(tmp_path, "r", 1, upstream_started_at=1060)
    _exchange(tmp_path, "g", 1, upstream_started_at=1001)
    _exchange(tmp_path, "b", 1, upstream_started_at=1065)
    _exchange(tmp_path, "b", 2, upstream_status=200)  # old log without exact start
    before = _attempt_census(tmp_path)
    data = build_run_trends(tmp_path, "trends", now=1180)
    assert [p["experiments"] for p in data["points"]] == [1, 1, 0]
    assert [p["experiments_peak"] for p in data["points"]] == [1, 2, 1]
    assert [p["agents"] for p in data["points"]] == [3, 1, 0]
    assert [p["requests"] for p in data["points"]] == [3, 2, 0]
    assert [p["rpm"] for p in data["points"]] == [3, None, 0]
    experts = {e["role"]: e for e in data["experts"]}
    assert experts["private-expert"]["rpm"] == [2, 1, 0]  # reader belongs to parent
    assert experts["other"]["rpm"] == [0, None, 0]  # no global-group collision
    assert experts["other"]["rpm_lower_bound"] == [0, 1, 0]
    assert data["other"]["rpm"] == [0, 0, 0]
    assert data["grounding"]["rpm"] == [1, 0, 0]
    assert data["coverage"]["request_count"] == 5
    assert data["coverage"]["availability"] == "partial"
    assert _attempt_census(tmp_path) == before
    assert build_run_trends(tmp_path, "trends", now=1180) == data
    rows = list(csv.DictReader(io.StringIO(trends_csv(data).decode())))
    unknown = next(row for row in rows if row["scope"] == "run" and float(row["at"]) == 1060)
    assert unknown["rpm"] == "" and unknown["rpm_lower_bound"] == "2.0"


@pytest.mark.parametrize("failure", ["missing", "corrupt", "symlink", "limit"])
def test_missing_or_bounded_capture_never_becomes_zero(tmp_path, failure):
    _seed(tmp_path)
    path = _exchange(tmp_path, "a", 1, upstream_started_at=1010)
    if failure == "missing":
        path.unlink()
    elif failure == "corrupt":
        path.write_text("{")
    elif failure == "symlink":
        path.unlink()
        target = tmp_path / "outside.json"
        target.write_text('{"upstream_started_at":1010}')
        path.symlink_to(target)
    data = build_run_trends(tmp_path, "trends", now=1180, max_files=0 if failure == "limit" else 100)
    assert data["coverage"]["availability"] == "unavailable"
    assert data["coverage"]["request_count"] == 0
    assert all(p["rpm"] is None for p in data["points"])
    assert [p["experiments_peak"] for p in data["points"]] == [1, 2, 1]


def test_long_run_coarsens_without_dropping_short_experiments(tmp_path):
    _seed(tmp_path)
    data = build_run_trends(tmp_path, "trends", now=1180, max_points=1)
    assert data["interval_seconds"] == 180
    assert data["points"][0]["experiments_peak"] == 2
    assert data["points"][0]["experiments"] == 0


def test_open_attempt_and_partial_last_minute_remain_visible(tmp_path):
    _seed(tmp_path)
    store = StateStore(tmp_path, HostWriterCapability.issue()).initialize()
    try:
        with store.transaction() as tx:
            tx.open_attempt(dict(attempt_id="open", run_id="trends", workflow_id="trends", command_id="a",
                kind="AGENT", attempt_ordinal=2, host_generation="fixture", started_at=1181,
                payload={"request": {"role": "private-expert", "command": "EXPERIMENT_DESIGN_IMPLEMENT"}}))
            tx.bind_attempt_process("open", 12346)
        _exchange(tmp_path, "open", 1, upstream_started_at=1185, ended_at=None)
        data = build_run_trends(tmp_path, "trends", now=1190)
        last = data["points"][-1]
        assert last["agents"] == 1 and last["experiments"] == 0
        assert last["rpm"] == 6 and last["requests"] == 1
        assert store.get_attempt("open")["state"] == "OPEN"
    finally:
        store.close()


def test_default_support_export_includes_run_wide_anonymous_trends(tmp_path):
    run, first, second = _seed_agent_retry(tmp_path)
    before = _attempt_census(tmp_path)
    for attempt, at in [(first, 1.5), (second, 3.5)]:
        path = tmp_path / "diagnostics/agents" / run / attempt / "provider/000001/exchange.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"upstream_started_at": at, "ended_at": at + .1}))
    report, files = build_support_bundle(tmp_path, run, attempt_id=first)
    assert report["trends"]["files"] == ["runtime-trends.json", "runtime-trends.csv"]
    data = json.loads(files["runtime-trends.json"])
    assert data["coverage"]["request_count"] == 2  # includes unselected successful retry
    assert data["run_id"] == report["run_id"] != run
    for raw in (files["runtime-trends.json"], files["runtime-trends.csv"]):
        assert run.encode() not in raw and first.encode() not in raw and second.encode() not in raw
    assert _attempt_census(tmp_path) == before
    report, files = build_support_bundle(tmp_path, run, max_file_bytes=256, max_bytes=65536)
    assert not report["trends"]["files"] and report["limited"]
    assert any(item.get("path") == "runtime-trends.json" and item["status"] == "omitted" for item in report["attachments"])


def test_relay_records_transport_start_before_response_or_browser(tmp_path, upstream):
    release = threading.Event()
    def blocked(handler):
        assert release.wait(3)
        handler.send_response(200)
        handler.send_header("Content-Length", str(len(_RESPONSE)))
        handler.send_header("Content-Type", "application/json")
        handler.end_headers()
        handler.wfile.write(_RESPONSE)
    upstream.script = blocked
    diagnostics = RuntimeDiagnostics(tmp_path / "capture")
    relay = _relay(tmp_path, upstream, diagnostics=diagnostics,
        traffic_binding=(ProviderTraffic(), ("expert", "a"), None))
    try:
        with ThreadPoolExecutor() as pool:
            request = pool.submit(_request, relay)
            try:
                until(lambda: len(upstream.calls) == 1)
                exchange = diagnostics.root / "provider/000001/exchange.json"
                # Socket receipt can precede the relay's atomic metadata write.
                # Keep the response blocked until its start is observable.
                until(lambda: exchange.is_file() and
                    json.loads(exchange.read_bytes()).get("upstream_started_at") is not None)
                data = json.loads(exchange.read_bytes())
                assert data["upstream_started_at"] > 0 and data["ended_at"] is None
                assert not request.done()
            finally:
                release.set()
            assert request.result(timeout=3)[0] == 200
    finally:
        release.set()
        relay.close()
