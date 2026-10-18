"""Plural device telemetry stays one Attempt in read-only product consumers."""
import copy
import json
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from hypoloom.monitor import _attempt_views
from hypoloom.resource_experience import summarize_resource_experience
from test_resource_experience import _attempt


def _device(uuid, *, free=12000, used=4000, pressure=False):
    return {"gpu_uuid": uuid, "total_memory_mib": 16000,
        "initial_free_memory_mib": 15000, "minimum_free_memory_mib": free,
        "latest_free_memory_mib": free, "latest_used_memory_mib": used,
        "latest_utilization_percent": 71, "sample_count": 3,
        "pressure_exceeded": pressure}


def _view(usage):
    row = {"attempt_id": "process-1", "kind": "PROCESS", "state": "CLOSED",
        "workflow_id": "experiment-1", "command_type": "EXECUTE",
        "transition_payload_json": json.dumps({"role": "researcher"}),
        "observation_json": json.dumps({"resource_usage": usage}),
        "experiment_id": "experiment-1", "execution_id": "execution-1",
        "attempt_ordinal": 1, "terminal_reason": "CANCELLED", "process_group_id": None,
        "started_at": 1.0, "ended_at": 2.0}
    reader = SimpleNamespace(list_attempts=lambda **_: [row], execute=lambda query, *_args: SimpleNamespace(
        fetchall=lambda: [] if "agent_final_failure" in query else [row]))
    attempts, scientific = _attempt_views(reader, "run-1", ("researcher",),
        {"experiment-1": "component-1"}, {}, frozenset({("experiment-1", "execution-1")}))
    assert scientific == frozenset({"process-1"})
    assert len(attempts) == 1
    return attempts[0]


def _owned(*, unknown=0):
    peak = {"complete": True, "resident_bytes": 7000 * 1024**2 + 1,
        "observed_at": 1.25, "reason": None, "root_process_group_id": 123,
        "processes": [{"pid": 456, "ancestry": [{"pid": 123}], "secret": "private"}]}
    latest = {**peak, "resident_bytes": 3000 * 1024**2, "observed_at": 1.75}
    if unknown: latest.update(complete=False, resident_bytes=None, reason="private diagnostic")
    return {"sample_count": 3, "unknown_sample_count": unknown, "latest": latest, "peak": peak}


@pytest.mark.parametrize("unknown", [0, 1])
def test_monitor_projects_sampled_own_memory_separately_without_raw_process_evidence(unknown):
    device = {**_device("GPU-one"), "owned_memory": _owned(unknown=unknown)}
    before = copy.deepcopy(device)
    view = _view(device)
    assert view.gpu_usage[0]["owned_memory"] == {"sample_count": 3, "unknown_sample_count": unknown,
        "complete_sample_count": 3 - unknown,
        "coverage": "unknown" if unknown else "complete",
        "latest_sample_memory_mib": None if unknown else 3000, "sampled_peak_memory_mib": 7001,
        "latest_observed_at": 1.75, "peak_observed_at": 1.25}
    assert view.gpu_usage[0]["latest_used_memory_mib"] == 4000
    encoded = json.dumps(asdict(view))
    for forbidden in ("ancestry", '"pid"', "root_process_group_id", "private", "resident_bytes"):
        assert forbidden not in encoded
    assert device == before


@pytest.mark.parametrize("owned", [None, {}, {"sample_count": True},
    {"sample_count": 0, "unknown_sample_count": 0, "latest": None, "peak": None},
    {**_owned(), "sample_count": 0}, {**_owned(), "unknown_sample_count": 4}])
def test_monitor_unknown_or_malformed_own_samples_do_not_use_device_pressure_as_memory(owned):
    device = {**_device("GPU-one", used=15000, free=1000), "owned_memory": owned}
    view = _view(device).gpu_usage[0]["owned_memory"]
    assert view["coverage"] == "unknown"
    assert view["latest_sample_memory_mib"] is None and view["sampled_peak_memory_mib"] is None


def test_monitor_does_not_promote_incomplete_or_incoherent_samples():
    owned = _owned()
    owned["latest"].update(complete=False, resident_bytes=15000 * 1024**2)
    owned["peak"]["resident_bytes"] = 17000 * 1024**2
    view = _view({**_device("GPU-one"), "owned_memory": owned}).gpu_usage[0]["owned_memory"]
    assert view["coverage"] == "unknown"
    assert view["latest_sample_memory_mib"] is None and view["sampled_peak_memory_mib"] is None


def test_monitor_preserves_both_devices_and_only_allowlisted_telemetry():
    first = _device("GPU-first")
    second = _device("GPU-second", free=128, used=15872, pressure=True)
    polluted = [dict(first, secret="private-token", pid=999), dict(second, cwd="/private")]
    original = copy.deepcopy(polluted)
    view = _view(polluted)
    assert view.gpu_usage == (first, second)
    assert view.gpu_uuid is None  # plural data must not masquerade as one card
    assert view.minimum_free_memory_mib is None
    assert view.resource_pressure_exceeded is True
    assert polluted == original
    assert "private" not in json.dumps(asdict(view))


def test_monitor_retains_singular_fields_and_adds_one_device_view():
    device = _device("GPU-one", pressure=True)
    view = _view(device)
    assert view.gpu_usage == (device,)
    assert view.gpu_uuid == "GPU-one"
    assert view.minimum_free_memory_mib == 12000
    assert view.latest_free_memory_mib == 12000
    assert view.latest_utilization_percent == 71
    assert view.resource_pressure_exceeded is True


@pytest.mark.parametrize("usage", [None, [], {}, [None, "invalid", {}]])
def test_monitor_absent_or_malformed_gpu_telemetry_is_not_invented(usage):
    view = _view(usage)
    assert view.gpu_usage == ()
    assert view.resource_pressure_exceeded is None


@pytest.mark.parametrize("nested", [False, True])
def test_resource_experience_counts_plural_attempt_once_and_uses_second_device_extremes(nested):
    usage = [_device("GPU-secret-first"), _device("GPU-secret-second", free=128, used=15872, pressure=True)]
    attempt = _attempt("researcher", status="FAILED")
    attempt["resource_grant"] = {"kind": "GPU", "gpu_uuids": [item["gpu_uuid"] for item in usage]}
    if nested:
        attempt["observation"] = {"resource_usage": usage}
    else:
        attempt["resource_usage"] = usage
    original = copy.deepcopy(attempt)
    summary = summarize_resource_experience([attempt])
    group, = summary["groups"]
    assert summary["source_attempt_count"] == group["attempt_count"] == group["gpu_grant_count"] == 1
    assert group["failed_attempt_count"] == group["pressure_termination_count"] == 1
    assert group["minimum_observed_free_memory_mib"] == 128
    assert group["maximum_observed_used_memory_mib"] == 15872
    assert attempt == original
    assert "GPU-secret" not in json.dumps(summary) and "gpu_uuid" not in json.dumps(summary)


def test_plural_success_pressure_is_observation_not_a_termination_and_bad_values_are_ignored():
    attempt = _attempt("researcher", usage={})
    attempt["resource_usage"] = [_device("GPU-a", pressure=True),
        {"minimum_free_memory_mib": -1, "maximum_used_memory_mib": True,
         "pressure_exceeded": "true", "secret": "private"}, None]
    summary = summarize_resource_experience([attempt])
    group, = summary["groups"]
    assert group["minimum_observed_free_memory_mib"] == 12000
    assert group["maximum_observed_used_memory_mib"] == 4000
    assert group["pressure_termination_count"] == 0


def test_tui_renders_each_gpu_once_and_marks_only_the_pressured_device():
    from hypoloom.tui import AttemptsPanel
    view = _view([{**_device("GPU-first"), "owned_memory": _owned(unknown=1)},
        _device("GPU-second", free=128, used=15872, pressure=True)])
    panel = AttemptsPanel()
    panel.update_snapshot(SimpleNamespace(attempts=(view,), observed_at=3.0))
    lines = [line for line in panel._rendered_content.splitlines() if "gpu=" in line]
    assert len(lines) == 2
    assert "GPU-first" in lines[0] and "reserve pressure" not in lines[0]
    assert "GPU-second" in lines[1] and "128/128 MiB" in lines[1] and "reserve pressure" in lines[1]
    assert "own sampled latest/peak=" in panel._rendered_content
    assert "7001 MiB" in panel._rendered_content and "unknown samples=1/3" in panel._rendered_content


def test_http_snapshot_exposes_both_sanitized_devices_without_dropping_legacy_fields(tmp_path):
    from hypoloom.web_observer import create_server
    from test_web_observer import _request, _start, _SyntheticSnapshot, _TOKEN
    view = _view([{**_device("GPU-first"), "owned_memory": _owned(unknown=1)}, _device("GPU-second", pressure=True)])
    snapshot = _SyntheticSnapshot({"attempts": [asdict(view)]})
    server = create_server(tmp_path / "unused", "run-1", port=0, token=_TOKEN,
        snapshot_builder=lambda *_args: snapshot)
    thread = _start(server)
    try:
        status, _, body = _request(server, "GET", "/api/snapshot", token=_TOKEN)
        assert status == 200
        attempt, = json.loads(body)["attempts"]
        assert [item["gpu_uuid"] for item in attempt["gpu_usage"]] == ["GPU-first", "GPU-second"]
        assert attempt["gpu_uuid"] is None and attempt["resource_pressure_exceeded"] is True
        assert attempt["gpu_usage"][0]["owned_memory"]["sampled_peak_memory_mib"] == 7001
        assert attempt["gpu_usage"][0]["owned_memory"]["latest_sample_memory_mib"] is None
        assert b"private" not in body and b"ancestry" not in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize("plural", [True, False])
def test_actual_web_renderer_lists_every_gpu_with_singular_fallback(plural):
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("node is unavailable for the actual web renderer")
    source = (Path(__file__).resolve().parents[2] / "hypoloom/web_observer_assets/app.js").read_text()
    translation = source[source.index("  function t("):source.index("  const byId =")]
    renderer = source[source.index("  function renderAttempts(selection) {"):source.index("  function renderFactCounts(container) {")]
    scope_label = source[source.index("  function taskScopeLabel(record) {"):source.index("  function renderGlobalTasks(snapshot) {")]
    device = {**_device("GPU-first"), "owned_memory": _owned(unknown=1)}
    view = asdict(_view([device, _device("GPU-second", pressure=True)] if plural else device))
    if not plural:
        view.pop("gpu_usage", None)  # retained serialized monitor sample
    harness = """
const entries = [], classes = [];
const uiLanguage = 'en', messages = {};
const element = () => ({append() {}, addEventListener() {}, classList: {add(value) {classes.push(value);}}});
const list = value => Array.isArray(value) ? value : [];
const object = value => value || {};
const byId = () => ({replaceChildren() {}, append() {}});
const setText = () => {}, attemptMatches = () => true;
const formatTimestamp = () => '', formatDuration = () => '';
const appendDefinition = (_grid, name, value) => entries.push([name, value]);
"""
    command = harness + translation + "\nconst state = " + json.dumps({"snapshot": {"attempts": [view]}}) + ";\n" + scope_label + renderer
    command += "\nrenderAttempts(null); process.stdout.write(JSON.stringify({entries, classes}));"
    result = subprocess.run([executable, "-e", command], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    gpu_ids = [value for name, value in output["entries"] if name == "GPU"]
    assert gpu_ids == (["GPU-first", "GPU-second"] if plural else ["GPU-first"])
    assert output["classes"].count("pressure-exceeded") == int(plural)
    own_values = [value for name, value in output["entries"] if name == "own sampled latest / peak"]
    assert own_values == (["? / 7001 MiB"] if plural else [])
