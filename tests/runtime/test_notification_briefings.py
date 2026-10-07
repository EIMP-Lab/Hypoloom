"""Local-only delivery exercises; no Telegram credentials or real messages."""
import io
import json
import subprocess
from types import SimpleNamespace

import pytest

from hypoloom import notification_tailer as notifier
from hypoloom.notification_templates import briefing_text, due_briefing, event_text
from hypoloom.observation_api import PRODUCT_EVENT_TYPES, build_product_observation
from test_notification_tailer import _notify, _tailer, _cursor, _event, _create_profile_run
from test_web_observer import _row_census
from test_shared_acceptance_host import shared_host as shared_host, completed, RUN_ID


ENV = {"TEST_BOT": "123:private-token", "TEST_CHAT": "private-chat"}
PNG = b"\x89PNG\r\n\x1a\nlocal-fixture"


def observation(elapsed=43200, language="zh-CN"):
    return {"run_id": "notifications", "observed_at": 1791374400.0,
        "run": {"run_state": "ACTIVE", "report_language": language, "goal_verdict": None,
                "required_component_count": 3, "required_assessment_count": 1},
        "graph": {"nodes": [{"state": state, "text": "private scientific prose"} for state in
                    ("SUPPORTED", "REFUTED", "PRUNED", "PROPOSED")], "edges": [{}, {}, {}]},
        "summary": {"elapsed_seconds": elapsed,
            "totals": {"experiments": {"total": 9, "states": {"EXECUTING": 2, "SUCCEEDED": 7}}},
            "accounting": {"reported_tokens": 123456, "failed_reported_tokens": 1000,
                "observed_unsettled_tokens": 2000, "reserved_tokens": 9000,
                "unknown_closed_attempts": 2, "unavailable_token_attempts": 3}}, "events": []}


def setup(monkeypatch, view, *, subscribed=True, enabled=True):
    config = _notify(telegram=enabled)
    config["sinks"].pop("log")
    config["events"]["telegram"] = ["run_briefing"] if subscribed else ["error"]
    monkeypatch.setattr(notifier, "load_notification_config", lambda *_: config)
    monkeypatch.setattr(notifier, "build_product_observation", lambda *_: view)
    monkeypatch.setattr(notifier, "briefing_photo", lambda *a, **k: PNG)
    calls = []
    def transport(url, document, **options):
        calls.append((url, document, options))
        return notifier.HTTPResponse(200, b'{"ok":true}')
    return config, calls, transport


@pytest.mark.parametrize("language", ["zh-CN", "en"])
def test_all_event_templates_have_spacing_safe_fields_and_elapsed(language):
    view = observation(elapsed=90061, language=language)
    for kind in PRODUCT_EVENT_TYPES | {"notification_backlog"}:
        event = _event(kind=kind)
        event["payload"] = {"role": "scientist", "text": "private operator text", "question": "private question",
            "ordinal": 2, "counts": {"stage_finished": 5}, "http_status": 403, "next_retry_at": 1791331200}
        text = event_text(event, view)
        assert kind in text and event["event_id"] in text
        assert len(text.split("\n")) == len(text.split("\n\n")) * 2 - 1
        assert text.endswith("1 天 01:01:01" if language == "zh-CN" else "1d 01:01:01")
        assert ("运行：" if language == "zh-CN" else "Run: ") in text
        assert "Dashboard" not in text and "http" not in text and "private" not in text
        assert len(text.encode("utf-16-le")) // 2 <= 4096


def test_report_language_comes_from_admitted_contract_and_source_stays_readonly(tmp_path):
    root = _create_profile_run(tmp_path, _notify())
    # The helper's accepted contract is legacy; then exercise the v4 projection
    # with an in-memory monitor snapshot, without editing admitted facts.
    from hypoloom.monitor import build_monitor_snapshot
    before = _row_census(root / "state.sqlite")
    snapshot = build_monitor_snapshot(root, "notifications").to_dict()
    contract = snapshot["goal"]["research_contract"]
    contract["goal"]["language"] = "zh-CN"
    for policy, expected in [({"prompt_language": "zh-CN", "report_language": "en"}, "en"),
                             ({"prompt_language": "en", "report_language": "zh-CN"}, "zh-CN"), ({}, "zh-CN")]:
        contract.setdefault("methodology", {}).setdefault("controls", {}).setdefault("product", {})["language"] = policy
        actual = build_product_observation(root, "notifications", _snapshot=snapshot)
        assert actual["run"]["report_language"] == expected
        assert actual["run"]["run_state"] == snapshot["status"]["run_state"]
    assert _row_census(root / "state.sqlite") == before


def test_elapsed_boundaries_restart_and_offline_coalescing(tmp_path, monkeypatch):
    view = observation(elapsed=43199)
    _, calls, transport = setup(monkeypatch, view)
    tailer = _tailer(tmp_path, environ=ENV, transport=transport)
    assert tailer.poll_once().delivered["telegram"] == 0
    view["summary"]["elapsed_seconds"] = 43200
    assert tailer.poll_once().delivered["telegram"] == 1
    assert len(calls) == 1 and calls[0][0].endswith("/sendPhoto")
    assert calls[0][2]["photo"] == PNG and "text" not in calls[0][1]
    assert json.loads(_cursor(tmp_path, "telegram").read_text()) == {"run-briefing:notifications": 1}
    restarted = _tailer(tmp_path, environ=ENV, transport=transport)
    view["summary"]["elapsed_seconds"] = 86399
    assert restarted.poll_once().delivered["telegram"] == 0
    view["summary"]["elapsed_seconds"] = 86400
    assert restarted.poll_once().delivered["telegram"] == 1
    view["summary"]["elapsed_seconds"] = 43200 * 20 + 100
    assert _tailer(tmp_path, environ=ENV, transport=transport).poll_once().delivered["telegram"] == 1
    assert len(calls) == 3
    assert json.loads(_cursor(tmp_path, "telegram").read_text()) == {"run-briefing:notifications": 20}


@pytest.mark.parametrize("state", ["COMPLETED", "FAILED", "CANCELLED"])
def test_terminal_run_never_schedules_or_replays_briefing(tmp_path, monkeypatch, state):
    view = observation(elapsed=43200 * 9)
    view["run"]["run_state"] = state
    _, calls, transport = setup(monkeypatch, view)
    assert _tailer(tmp_path, environ=ENV, transport=transport).poll_once().delivered["telegram"] == 0
    assert not calls
    event = _event(kind="run_finished")
    assert event_text(event, view).endswith("4 天 12:00:00")


@pytest.mark.parametrize("enabled,subscribed", [(False, True), (True, False)])
def test_no_images_or_delivery_without_enabled_subscription(tmp_path, monkeypatch, enabled, subscribed):
    _, calls, transport = setup(monkeypatch, observation(), enabled=enabled, subscribed=subscribed)
    def unexpected(*a, **k): raise AssertionError("unsubscribed image requested")
    monkeypatch.setattr(notifier, "briefing_photo", unexpected)
    assert not _tailer(tmp_path, environ=ENV, transport=transport).poll_once().errors
    assert not calls


@pytest.mark.parametrize("error", [ImportError("matplotlib"), ValueError("font unavailable"), subprocess.TimeoutExpired("plot", 15)])
def test_plot_failure_delivers_one_text_summary_and_advances_cursor(tmp_path, monkeypatch, error):
    _, calls, transport = setup(monkeypatch, observation())
    def failed(*a, **k): raise error
    monkeypatch.setattr(notifier, "briefing_photo", failed)
    t = _tailer(tmp_path, environ=ENV, transport=transport)
    assert t.poll_once().delivered["telegram"] == 1
    assert len(calls) == 1 and calls[0][0].endswith("/sendMessage")
    assert "趋势图不可用" in calls[0][1]["text"] and "photo" not in calls[0][2]
    assert t.poll_once().delivered["telegram"] == 0


def test_image_transport_failure_waits_and_never_sends_second_text_message(tmp_path, monkeypatch):
    _, _, _ = setup(monkeypatch, observation())
    calls = []
    now = [0]
    def transport(url, document, **options):
        calls.append(url)
        return notifier.HTTPResponse(429, b'{"ok":false,"parameters":{"retry_after":120}}') if len(calls) == 1 else notifier.HTTPResponse(200, b'{"ok":true}')
    t = _tailer(tmp_path, environ=ENV, transport=transport, clock=lambda: now[0])
    assert t.poll_once().health["telegram"]["next_retry_at"] == 120
    assert len(calls) == 1 and not _cursor(tmp_path, "telegram").exists()
    now[0] = 119
    assert t.poll_once().delivered["telegram"] == 0 and len(calls) == 1
    now[0] = 120
    assert t.poll_once().delivered["telegram"] == 1 and len(calls) == 2
    assert all(url.endswith("/sendPhoto") for url in calls)


@pytest.mark.parametrize("language", ["zh-CN", "en"])
def test_briefing_caption_accounts_separately_and_fits_photo_limit(language):
    view = observation(language=language)
    view["run_id"] = "run-" + "x" * 150
    event = due_briefing(view)
    text = briefing_text(event, chart_available=True)
    assert "123,456" in text and "2,000" in text and "9,000" in text
    assert "1,000" not in text and "125,456" not in text
    assert "1/3" in text
    for label in (("支持 1", "已剪枝 1", "执行中 2") if language == "zh-CN" else ("SUPPORTED 1", "PRUNED 1", "EXECUTING 2")):
        assert label in text
    assert ("尚无判定" if language == "zh-CN" else "not assessed") in text
    assert "private" not in text and "Dashboard" not in text
    assert len(text.encode("utf-16-le")) // 2 <= 1024


def test_five_message_limit_retains_due_briefing_behind_urgent_events(tmp_path, monkeypatch):
    view = observation()
    view["events"] = [_event(identity=f"urgent-{i}", kind="error") for i in range(6)]
    config, calls, transport = setup(monkeypatch, view)
    config["events"]["telegram"].append("error")
    t = _tailer(tmp_path, environ=ENV, transport=transport)
    assert t.poll_once().delivered["telegram"] == 5
    assert len(calls) == 5 and all(url.endswith("/sendMessage") for url, _, _ in calls)
    assert t.poll_once().delivered["telegram"] == 2
    assert calls[-1][0].endswith("/sendPhoto")


def test_managed_service_forwards_media_and_respects_shutdown_timeout(tmp_path, monkeypatch):
    from hypoloom.observer_services import ObserverServices
    calls = []
    def transport(*args, **kwargs):
        calls.append(kwargs)
        return notifier.HTTPResponse(200, b'{"ok":true}')
    services = ObserverServices(tmp_path / "state", transport=transport)
    monkeypatch.setattr("hypoloom.observer_services.time.monotonic", lambda: 100)
    services._shutdown_deadline = 101
    services._post("https://example.org/sendPhoto", {}, headers={}, timeout=10, photo=PNG, proxy_url="http://localhost:1234")
    assert calls[0]["photo"] == PNG and calls[0]["timeout"] == 1 and calls[0]["proxy_url"].endswith(":1234")


@pytest.mark.parametrize("language", ["zh-CN", "en"])
def test_real_png_renderer_reads_existing_trends_without_state_writes(tmp_path, language):
    pytest.importorskip("matplotlib")
    from hypoloom.notification_plot import briefing_photo
    from PIL import Image
    from test_observer_trends import _seed, _exchange
    _seed(tmp_path)
    _exchange(tmp_path, "a", 1, upstream_started_at=1010)
    before = _row_census(tmp_path / "state.sqlite")
    photo = briefing_photo(tmp_path, "trends", observed_at=1180, language=language)
    img = Image.open(io.BytesIO(photo))
    assert img.format == "PNG" and img.size == (1440, 960)
    assert 10000 < len(photo) < 4 * 1024 * 1024
    assert _row_census(tmp_path / "state.sqlite") == before


def test_rendered_axes_include_rates_and_concurrency_without_filling_unknown_gaps(tmp_path, monkeypatch):
    pytest.importorskip("matplotlib")
    import math
    from matplotlib.figure import Figure
    from hypoloom.notification_plot import render
    from hypoloom.observer_trends import build_run_trends
    from test_observer_trends import _seed, _exchange
    _seed(tmp_path)
    for number in range(20): _exchange(tmp_path, "a", number + 1, upstream_started_at=1010 + number)
    data = build_run_trends(tmp_path, "trends", now=1180)
    original = Figure.savefig
    def inspect(figure, *args, **kwargs):
        rpm, occupancy = figure.axes
        assert rpm.get_ylim()[1] > 20
        assert occupancy.get_ylim()[1] > 3
        total = rpm.lines[0]
        # Reader/second expert captures are missing only in the first two
        # buckets; the final bucket is complete and has no upstream requests.
        assert all(math.isnan(value) for value in total.get_ydata()[:2])
        assert total.get_ydata()[2] == 0
        assert any(max(line.get_ydata()) == 20 for line in rpm.lines[1:])
        return original(figure, *args, **kwargs)
    monkeypatch.setattr(Figure, "savefig", inspect)
    assert render(data, "en").startswith(b"\x89PNG")


def test_real_host_cpu_closure_uses_observed_time_and_stops_briefings(shared_host, tmp_path, monkeypatch):
    """Future observation clock exercises 12 hours without aging research rows."""
    from hypoloom import monitor
    from hypoloom.oracle import Oracle
    import os
    host, _ = shared_host
    root = host.state_root
    config = _notify(telegram=True)
    config["sinks"].pop("log")
    config["events"]["telegram"] = ["run_briefing", "run_finished"]
    monkeypatch.setattr(notifier, "load_notification_config", lambda *_: config)
    monkeypatch.setattr(notifier, "briefing_photo", lambda *a, **k: PNG)
    observed = monitor.time.time() + 43201
    def reader():
        with monkeypatch.context() as clock:
            clock.setattr(monitor, "time", SimpleNamespace(time=lambda: observed))
            return build_product_observation(root, RUN_ID)
    calls = []
    def transport(url, document, **options):
        calls.append((url, document))
        return notifier.HTTPResponse(200, b'{"ok":true}')
    tailer = notifier.NotificationTailer(root, RUN_ID, tmp_path / "delivery", environ=ENV,
        transport=transport, observation_reader=reader)
    before = _row_census(root / "state.sqlite")
    assert tailer.poll_once().delivered["telegram"] == 1
    assert calls[0][0].endswith("/sendPhoto")
    assert _row_census(root / "state.sqlite") == before
    completed(host)
    before = _row_census(root / "state.sqlite")
    final = reader()
    assert final["run"]["run_state"] == "COMPLETED"
    assert final["run"]["goal_verdict"] == "GOAL_MET"
    assert final["summary"]["elapsed_seconds"] < 43200
    assert final["summary"]["totals"]["experiments"]["total"] == 1
    assert tailer.poll_once().delivered["telegram"] == 1
    observed += 43200 * 5
    assert tailer.poll_once().delivered["telegram"] == 0
    assert _row_census(root / "state.sqlite") == before
    assert calls[-1][0].endswith("/sendMessage") and "run_finished" in calls[-1][1]["text"]
    assert Oracle(root / "state.sqlite", root / "artifacts").audit(RUN_ID).ok
    attempts = host.store.list_attempts(run_id=RUN_ID)
    assert all(a["state"] == "CLOSED" for a in attempts)
    assert not host.running and not host.running_agents
    for attempt in attempts:
        if attempt.get("process_group_id"):
            with pytest.raises(ProcessLookupError): os.killpg(attempt["process_group_id"], 0)
    (tmp_path / "briefing-closure.json").write_text(json.dumps({"agents": sum(a["kind"] == "AGENT" for a in attempts),
        "processes": sum(a["kind"] == "PROCESS" for a in attempts), "outcomes": 1, "open_attempts": 0,
        "live_owned_groups": 0, "local_deliveries": len(calls), "source_unchanged_by_notifications": True}))
