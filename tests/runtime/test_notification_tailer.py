from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from hypoloom import notification_tailer as notifier


def _notify(*, webhook=False, telegram=False):
    return {
        "sinks": {
            "log": {"level": "info", "path": "events.jsonl"},
            "webhook": {"enabled": webhook, "url_env": "TEST_WEBHOOK_URL", "token_env": "TEST_WEBHOOK_TOKEN"},
            "telegram": {"enabled": telegram, "bot_token_env": "TEST_BOT", "chat_id_env": "TEST_CHAT"},
        },
        "events": {name: ["stage_started", "stage_finished", "run_started"] for name in ("log", "webhook", "telegram")},
    }


def _event(revision=1, *, source="attempt", identity="a", kind="stage_started"):
    return {"event_id": f"{source}:{identity}:{revision}", "source": source,
            "source_id": identity, "revision": revision, "type": kind,
            "run_id": "notifications", "created_at": str(revision), "payload": {}}


def _mock_run(monkeypatch, config, events):
    monkeypatch.setattr(notifier, "load_notification_config", lambda *_: copy.deepcopy(config))
    monkeypatch.setattr(notifier, "build_product_observation", lambda *_: {"run_id": "notifications", "events": events})


def _tailer(tmp_path, **kwargs):
    return notifier.NotificationTailer(tmp_path / "state", "notifications", tmp_path / "notifications", **kwargs)


def _cursor(tmp_path, sink):
    return tmp_path / "notifications" / "notifications" / "cursors" / f"{sink}.json"


def test_filters_events_and_restarts_without_replay_or_cross_source_collision(tmp_path, monkeypatch):
    config = _notify()
    config["events"]["log"].append("unknown_future_event")
    events = [_event(2, kind="stage_finished"), _event(),
              _event(source="fact", kind="run_started"), _event(identity="b", kind="hypothesis_added")]
    _mock_run(monkeypatch, config, events)
    result = _tailer(tmp_path).poll_once()
    assert result.delivered == {"log": 3}
    assert not result.errors
    assert "unknown_future_event" in " ".join(result.warnings)
    rows = [json.loads(line) for line in (tmp_path / "notifications/notifications/events.jsonl").read_text().splitlines()]
    assert [row["revision"] for row in rows if row["source"] == "attempt"] == [1, 2]
    assert {row["event_id"] for row in rows} == {item["event_id"] for item in events[:3]}
    assert json.loads(_cursor(tmp_path, "log").read_text()) == {"attempt:a": 2, "fact:a": 1}
    assert _tailer(tmp_path).poll_once().delivered == {"log": 0}


def test_missing_secrets_are_visible_and_do_not_block_log_sink(tmp_path, monkeypatch):
    _mock_run(monkeypatch, _notify(webhook=True, telegram=True), [_event()])
    result = _tailer(tmp_path, environ={}).poll_once()
    assert result.delivered == {"log": 1, "telegram": 0, "webhook": 0}
    assert len(result.errors) == 2
    assert "TEST_BOT" in " ".join(result.errors)
    assert "TEST_WEBHOOK_URL" in " ".join(result.errors)
    assert not _cursor(tmp_path, "telegram").exists()
    assert not _cursor(tmp_path, "webhook").exists()


def test_telegram_uses_env_credentials_and_requires_api_success(tmp_path, monkeypatch):
    _mock_run(monkeypatch, _notify(telegram=True), [_event()])
    captured = []

    def transport(url, document, *, headers, timeout):
        captured.append((url, document, headers, timeout))
        return notifier.HTTPResponse(200, b'{"ok":false,"description":"do-not-echo-secret"}' if len(captured) == 1 else b'{"ok":true}')

    environment = {"TEST_BOT": "123:private-token", "TEST_CHAT": "private-chat"}
    first = _tailer(tmp_path, environ=environment, transport=transport).poll_once()
    assert len(first.errors) == 1
    assert "private" not in str(first) and "do-not-echo-secret" not in str(first)
    assert not _cursor(tmp_path, "telegram").exists()
    second = _tailer(tmp_path, environ=environment, transport=transport).poll_once()
    assert not second.errors and second.delivered["telegram"] == 1
    url, document, headers, timeout = captured[-1]
    assert url == "https://api.telegram.org/bot123:private-token/sendMessage"
    assert document["chat_id"] == "private-chat"
    assert "attempt:a:1" in document["text"]
    assert "parse_mode" not in document and "allow_paid_broadcast" not in document
    assert timeout > 0


def _credentials_notify(tmp_path, token_text="123:file-token", chat_text="file-chat"):
    store = tmp_path / "secrets" / "hypoloom.env"
    store.parent.mkdir(mode=0o700)
    store.write_text(f"# test credentials\nTELEGRAM_BOT_TOKEN={token_text}\nTELEGRAM_CHAT_ID={chat_text}\n", encoding="utf-8")
    store.chmod(0o600)
    config = _notify(telegram=True)
    config["sinks"]["telegram"] = {
        "enabled": True,
        "credentials_file": str(store),
    }
    return config


def test_telegram_uses_credentials_file_without_environment(tmp_path, monkeypatch):
    config = _credentials_notify(tmp_path)
    _mock_run(monkeypatch, config, [_event()])
    captured = []

    def transport(url, document, *, headers, timeout):
        captured.append((url, document))
        return notifier.HTTPResponse(200, b'{"ok":true}')

    result = _tailer(tmp_path, environ={}, transport=transport).poll_once()
    assert not result.errors and result.delivered["telegram"] == 1
    url, document = captured[-1]
    assert url == "https://api.telegram.org/bot123:file-token/sendMessage"
    assert document["chat_id"] == "file-chat"


def test_telegram_rejects_ambiguous_secret_source(tmp_path, monkeypatch):
    config = _credentials_notify(tmp_path)
    config["sinks"]["telegram"]["bot_token_env"] = "TEST_BOT"
    _mock_run(monkeypatch, config, [_event()])
    result = _tailer(tmp_path, environ={"TEST_BOT": "123:private-token"}).poll_once()
    assert result.delivered["telegram"] == 0
    assert len(result.errors) == 1
    assert "ambiguous" in result.errors[0]
    assert not _cursor(tmp_path, "telegram").exists()


@pytest.mark.parametrize(
    "prepare,expected",
    (
        ("missing", "unavailable"),
        ("relative", "must be absolute"),
        ("symlink", "unavailable"),
        ("empty", "outside its bound"),
        ("blank", "lacks TELEGRAM_BOT_TOKEN"),
        ("malformed", "is not KEY=VALUE"),
        ("duplicate", "repeats TELEGRAM_BOT_TOKEN"),
        ("empty_value", "empty TELEGRAM_BOT_TOKEN"),
    ),
)
def test_telegram_credentials_file_edge_cases(tmp_path, monkeypatch, prepare, expected):
    config = _credentials_notify(tmp_path)
    store = tmp_path / "secrets" / "hypoloom.env"
    if prepare == "missing":
        store.unlink()
    elif prepare == "relative":
        config["sinks"]["telegram"]["credentials_file"] = "secrets/hypoloom.env"
    elif prepare == "symlink":
        target = tmp_path / "elsewhere"
        target.write_text("TELEGRAM_BOT_TOKEN=123:file-token\n", encoding="utf-8")
        store.unlink()
        store.symlink_to(target)
    elif prepare == "empty":
        store.write_text("", encoding="utf-8")
    elif prepare == "blank":
        store.write_text(" \n", encoding="utf-8")
    elif prepare == "malformed":
        store.write_text("TELEGRAM_BOT_TOKEN 123:file-token\n", encoding="utf-8")
    elif prepare == "duplicate":
        store.write_text("TELEGRAM_BOT_TOKEN=123:file-token\nTELEGRAM_BOT_TOKEN=456:other\nTELEGRAM_CHAT_ID=file-chat\n", encoding="utf-8")
    elif prepare == "empty_value":
        store.write_text("TELEGRAM_BOT_TOKEN=\nTELEGRAM_CHAT_ID=file-chat\n", encoding="utf-8")
    _mock_run(monkeypatch, config, [_event()])
    result = _tailer(tmp_path, environ={}).poll_once()
    assert result.delivered["telegram"] == 0
    assert len(result.errors) == 1
    assert expected in result.errors[0]
    assert "file-token" not in str(result)
    assert not _cursor(tmp_path, "telegram").exists()


def test_failed_sink_does_not_advance_past_failed_revision_or_leak_transport_errors(tmp_path, monkeypatch):
    _mock_run(monkeypatch, _notify(webhook=True), [_event(2, kind="stage_finished"), _event()])
    calls = []

    def transport(url, document, **kwargs):
        calls.append(document)
        raise OSError("SECRET_TOKEN https://private.example/path")

    result = _tailer(tmp_path, environ={"TEST_WEBHOOK_URL": "https://private.example/path", "TEST_WEBHOOK_TOKEN": "SECRET_TOKEN"}, transport=transport).poll_once()
    assert [item["revision"] for item in calls] == [1]
    assert result.delivered["log"] == 2 and result.delivered["webhook"] == 0
    assert "SECRET_TOKEN" not in str(result) and "private.example" not in str(result)


def test_success_then_cursor_failure_replays_at_least_once(tmp_path, monkeypatch):
    _mock_run(monkeypatch, _notify(), [_event()])
    original = notifier._write_cursor

    def fail(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(notifier, "_write_cursor", fail)
    assert _tailer(tmp_path).poll_once().errors
    assert not _cursor(tmp_path, "log").exists()
    monkeypatch.setattr(notifier, "_write_cursor", original)
    assert not _tailer(tmp_path).poll_once().errors
    rows = (tmp_path / "notifications/notifications/events.jsonl").read_text().splitlines()
    assert len(rows) == 2 and rows[0] == rows[1]


def test_corrupt_cursor_is_visible_and_is_not_silently_reset(tmp_path, monkeypatch):
    _mock_run(monkeypatch, _notify(), [_event()])
    _cursor(tmp_path, "log").parent.mkdir(parents=True)
    _cursor(tmp_path, "log").write_text('{"attempt:a":true}')
    result = _tailer(tmp_path).poll_once()
    assert result.errors and result.delivered["log"] == 0
    assert _cursor(tmp_path, "log").read_text() == '{"attempt:a":true}'


@pytest.mark.parametrize("destination", ["state", "state/output", "."])
def test_output_root_cannot_overlap_source(tmp_path, monkeypatch, destination):
    _mock_run(monkeypatch, _notify(), [_event()])
    with pytest.raises(notifier.NotificationError, match="disjoint"):
        notifier.NotificationTailer(tmp_path / "state", "notifications", tmp_path / destination)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("path", ["../outside.log", "/tmp/outside.log", "cursors/log.json", "a/../../outside.log"])
def test_log_path_cannot_escape_output_or_replace_cursor(tmp_path, monkeypatch, path):
    config = _notify()
    config["sinks"]["log"]["path"] = path
    _mock_run(monkeypatch, config, [_event()])
    with pytest.raises(notifier.NotificationError, match="log path"):
        _tailer(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_existing_symlink_log_is_rejected_without_touching_target(tmp_path, monkeypatch):
    _mock_run(monkeypatch, _notify(), [_event()])
    external = tmp_path / "outside"
    external.write_text("keep")
    path = tmp_path / "notifications/notifications/events.jsonl"
    path.parent.mkdir(parents=True)
    path.symlink_to(external)
    result = _tailer(tmp_path).poll_once()
    assert result.errors and external.read_text() == "keep"


def test_no_explicit_remote_selection_keeps_local_log_but_never_sends_webhook(tmp_path, monkeypatch):
    config = _notify(webhook=True)
    config["events"] = {}
    _mock_run(monkeypatch, config, [_event()])
    result = _tailer(tmp_path, environ={}).poll_once()
    assert result.delivered == {"log": 1, "webhook": 0}
    assert len(result.warnings) == 1 and not result.errors
    assert (tmp_path / "notifications/notifications/events.jsonl").is_file()


def test_explicit_empty_log_selection_remains_disabled(tmp_path, monkeypatch):
    config = _notify()
    config["events"]["log"] = []
    _mock_run(monkeypatch, config, [_event()])
    assert _tailer(tmp_path).poll_once().delivered == {"log": 0}
    assert not (tmp_path / "notifications").exists()


def test_missing_source_is_not_created_by_readonly_constructor(tmp_path):
    with pytest.raises(Exception):
        _tailer(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_log_level_filters_lower_severity_events(tmp_path, monkeypatch):
    config = _notify()
    config["sinks"]["log"]["level"] = "error"
    config["events"]["log"].append("experiment_failed")
    _mock_run(monkeypatch, config, [_event(), _event(source="fact", kind="experiment_failed")])
    assert _tailer(tmp_path).poll_once().delivered == {"log": 1}
    rows = (tmp_path / "notifications/notifications/events.jsonl").read_text().splitlines()
    assert len(rows) == 1 and json.loads(rows[0])["type"] == "experiment_failed"


def test_log_without_filter_delivers_all_known_events_and_reports_error_severity(tmp_path, monkeypatch):
    config = _notify()
    config["events"].pop("log")
    config["sinks"]["log"]["level"] = "error"
    _mock_run(monkeypatch, config, [_event(), _event(source="attempt-error", kind="error")])
    assert _tailer(tmp_path).poll_once().delivered == {"log": 1}
    rows = (tmp_path / "notifications/notifications/events.jsonl").read_text().splitlines()
    assert len(rows) == 1 and json.loads(rows[0])["type"] == "error"


def test_telegram_notice_has_safe_summary_and_elapsed_without_note_text(tmp_path, monkeypatch):
    config = _notify(telegram=True)
    config["sinks"]["dashboard"] = {"enabled": True, "host": "127.0.0.1", "port": 8765}
    event = _event()
    event["payload"] = {"role": "optimizer", "node": "RESULT_ANALYSIS", "text": "private operator text"}
    _mock_run(monkeypatch, config, [event])
    documents = []
    def transport(_url, document, **_kwargs):
        documents.append(document)
        return notifier.HTTPResponse(200, b'{"ok":true}')
    result = _tailer(tmp_path, environ={"TEST_BOT": "123:private-token", "TEST_CHAT": "private-chat"}, transport=transport).poll_once()
    assert not result.errors
    text = documents[-1]["text"]
    assert "optimizer" in text and "RESULT_ANALYSIS" in text
    assert text.endswith("⏱ Elapsed: unknown") and "Dashboard" not in text
    assert "private operator text" not in text and "private-token" not in text


@pytest.mark.parametrize("port", [0, None])
def test_standalone_notice_omits_dashboard_address(tmp_path, monkeypatch, port):
    config = _notify(telegram=True)
    config["sinks"]["dashboard"] = {"enabled": True}
    if port is not None:
        config["sinks"]["dashboard"]["port"] = port
    _mock_run(monkeypatch, config, [_event()])
    messages = []

    def transport(_url, document, **_kwargs):
        messages.append(document["text"])
        return notifier.HTTPResponse(200, b'{"ok":true}')

    result = _tailer(tmp_path, environ={"TEST_BOT": "123:test-bot", "TEST_CHAT": "test-chat"}, transport=transport).poll_once()
    assert not result.errors
    assert "Dashboard" not in messages[0] and "Elapsed: unknown" in messages[0]
    assert "http://127.0.0.1:" not in messages[0]


def test_managed_notice_omits_dashboard_even_after_bind_recovery(tmp_path, monkeypatch):
    config = _notify(telegram=True)
    config["sinks"]["dashboard"] = {"enabled": True, "port": 8765}
    events = [_event()]
    _mock_run(monkeypatch, config, events)
    messages = []
    live_port = None

    def transport(_url, document, **_kwargs):
        messages.append(document["text"])
        return notifier.HTTPResponse(200, b'{"ok":true}')

    tailer = _tailer(tmp_path, environ={"TEST_BOT": "123:test-bot", "TEST_CHAT": "test-chat"},
        transport=transport, dashboard_port=lambda: live_port)
    assert not tailer.poll_once().errors
    assert "Dashboard" not in messages[0] and ":8765/" not in messages[0]
    live_port = 43210
    events.append(_event(2))
    assert not tailer.poll_once().errors
    assert "http://127.0.0.1:43210/" not in messages[1]
    assert "Elapsed: unknown" in messages[1]
    assert "#token=" not in messages[1]


def test_grounding_telegram_events_deliver_and_restart_without_replay(tmp_path, monkeypatch):
    from hypoloom.observation_api import product_events

    config = _notify(telegram=True)
    config["events"]["telegram"] = ["grounding_question", "grounding_answered"]
    rows = [{"workflow_id": "notifications", "run_id": "notifications", "workflow_kind": "RUN",
             "revision": revision, "previous_state": before, "state": after,
             "created_at": "2026-09-08T00:00:00Z", "command_id": f"command-{revision}",
             "command_type": command, "payload": {"kind": "grounding_clarification",
                 "ordinal": 1, "decision_id": "question-1", "human_decision_command": action,
                 "question": "private question text", "answer": "private answer text"}}
            for revision, before, after, command, action in [
                (4, "ACTIVE", "WAITING_HUMAN", "REQUEST_HUMAN", "OPEN_DECISION"),
                (5, "WAITING_HUMAN", "ACTIVE", "RESOLVE_CONTINUE", "RESOLVE")]]
    projected = product_events("notifications", [], rows, [])
    events = projected[:1]
    _mock_run(monkeypatch, config, events)
    captured = []

    def transport(_url, document, **_kwargs):
        captured.append(document)
        return notifier.HTTPResponse(200, b'{"ok":true}')

    telegram = config["sinks"]["telegram"]
    options = {"environ": {telegram["bot_token_env"]: "123:private-token",
                           telegram["chat_id_env"]: "private-chat"}, "transport": transport}
    for event in projected:
        if event not in events:
            events.append(event)
        delivered = _tailer(tmp_path, **options).poll_once()
        assert not delivered.errors and not delivered.warnings
        assert delivered.delivered["telegram"] == 1
        assert _tailer(tmp_path, **options).poll_once().delivered["telegram"] == 0
    assert len(captured) == 2
    assert "Human decision requested" in captured[0]["text"]
    assert "TUI: press h" in captured[0]["text"] and "Browser" in captured[0]["text"]
    assert "Telegram replies do not submit an answer" in captured[0]["text"]
    assert "Human answer recorded" in captured[1]["text"]
    for document, event in zip(captured, projected, strict=True):
        assert document["chat_id"] == "private-chat"
        assert event["type"] in document["text"] and event["event_id"] in document["text"]
        assert "Question: 1" in document["text"]
        assert "private question text" not in document["text"]
        assert "private answer text" not in document["text"] and "private-token" not in document["text"]
        assert "#token=" not in document["text"] and "answer_token=" not in document["text"]
    assert json.loads(_cursor(tmp_path, "telegram").read_text()) == {"grounding-dialogue:notifications": 5}


@pytest.mark.parametrize("enabled,subscribed", [(False, True), (True, True), (True, False)])
def test_starter_question_reminder_requires_enable_and_event_subscription(tmp_path, monkeypatch, enabled, subscribed):
    template = Path(__file__).resolve().parents[2] / "profiles/templates/multi-expert-en/notify.yaml"
    config = yaml.safe_load(template.read_text(encoding="utf-8"))
    assert config["sinks"]["telegram"]["enabled"] is False
    assert {"grounding_question", "grounding_answered"} <= set(config["events"]["telegram"])
    config["sinks"]["telegram"]["enabled"] = enabled
    if not subscribed:
        config["events"]["telegram"].remove("grounding_question")
    _mock_run(monkeypatch, config, [_event(kind="grounding_question")])
    captured = []

    def transport(_url, document, **_kwargs):
        captured.append(document)
        return notifier.HTTPResponse(200, b'{"ok":true}')

    sink = config["sinks"]["telegram"]
    if enabled:
        store = tmp_path / "hypoloom.env"
        store.write_text("TELEGRAM_BOT_TOKEN=123:test-token\nTELEGRAM_CHAT_ID=test-chat\n", encoding="utf-8")
        store.chmod(0o600)
        sink["credentials_file"] = str(store)
    result = _tailer(tmp_path, environ={}, transport=transport).poll_once()
    assert not result.errors
    assert len(captured) == int(enabled and subscribed)


def test_webhook_redirect_is_a_failure_not_a_credential_forward(tmp_path, monkeypatch):
    _mock_run(monkeypatch, _notify(webhook=True), [_event()])
    calls = []

    def transport(url, document, **kwargs):
        calls.append(url)
        return notifier.HTTPResponse(307, b"private redirect destination")

    result = _tailer(tmp_path, environ={"TEST_WEBHOOK_URL": "https://private.example/path", "TEST_WEBHOOK_TOKEN": "private"}, transport=transport).poll_once()
    assert len(calls) == 1 and "307" in str(result.errors)
    assert "private" not in str(result.errors)
    assert not _cursor(tmp_path, "webhook").exists()


def test_nonobject_telegram_response_is_visible_and_pending(tmp_path, monkeypatch):
    _mock_run(monkeypatch, _notify(telegram=True), [_event()])
    result = _tailer(tmp_path, environ={"TEST_BOT": "123:private-token", "TEST_CHAT": "private-chat"},
        transport=lambda *_args, **_kwargs: notifier.HTTPResponse(200, b"[]")).poll_once()
    assert result.errors and not _cursor(tmp_path, "telegram").exists()


def test_failed_atomic_cursor_replace_preserves_prior_progress(tmp_path, monkeypatch):
    path = _cursor(tmp_path, "log")
    notifier._write_cursor(path, {"attempt:a": 1})

    def fail(*_args):
        raise OSError("replace failed")

    monkeypatch.setattr(notifier.os, "replace", fail)
    with pytest.raises(OSError):
        notifier._write_cursor(path, {"attempt:a": 2})
    assert json.loads(path.read_text()) == {"attempt:a": 1}
    assert list(path.parent.iterdir()) == [path]


def _create_profile_run(tmp_path, notify):
    from hypoloom.control_protocol import ControlRequest
    from hypoloom.profile_admission import admit_profile
    from hypoloom.workflow_host import WorkflowHost
    from test_profile_admission import _materialize_expert_loop_pilot_v3
    from test_profile_v4 import documents

    profile, target, data = _materialize_expert_loop_pilot_v3(tmp_path)
    source = documents()
    source["methodology"]["runtime"]["target_repo"] = str(target)
    source["safety"]["allow_read_paths"] = [str(data)]
    source["notify"] = notify
    for name, value in source.items():
        (profile / f"{name}.yaml").write_text(yaml.safe_dump(value), encoding="utf-8")
        (profile / f"{name}.yaml").chmod(0o600)
    admitted = admit_profile(profile, run_id="notifications")
    assert admitted.admitted, admitted.to_dict()
    root = tmp_path / "state"
    host = WorkflowHost(root)
    try:
        host.initialize()
        response = host.handle(ControlRequest(schema_version=1, request_id="create-notifications",
            expected_revision=0, command="CREATE_RUN", payload=admitted.create_run_payload))
        assert response.status == "accepted", response
    finally:
        host.close()
    return root


def test_real_cli_loopback_failure_retry_restart_keeps_created_run_readonly(tmp_path):
    from test_web_observer import _row_census

    received = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((json.loads(self.rfile.read(int(self.headers["Content-Length"]))), self.headers.get("Authorization")))
            self.send_response(503 if len(received) == 1 else 204)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        config = _notify(webhook=True)
        root = _create_profile_run(tmp_path, config)
        before_rows = _row_census(root / "state.sqlite")
        before_files = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
        assert notifier.load_notification_config(root, "notifications") == config
        command = [sys.executable, "-m", "hypoloom.notification_tailer", "--state-root", str(root),
                   "--run-id", "notifications", "--output-root", str(tmp_path / "notifications"), "--once"]
        environment = {**os.environ, "TEST_WEBHOOK_URL": f"http://127.0.0.1:{server.server_port}/events", "TEST_WEBHOOK_TOKEN": "local-only-secret"}
        first = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=30)
        assert first.returncode == 1, first.stdout + first.stderr
        assert "503" in first.stdout + first.stderr
        assert not _cursor(tmp_path, "webhook").exists()
        second = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=30)
        assert second.returncode == 0, second.stdout + second.stderr
        third = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=30)
        assert third.returncode == 0, third.stdout + third.stderr
        assert len(received) == 2 and received[0] == received[1]
        event, authorization = received[0]
        assert event["type"] == "run_started" and event["source"] == "fact"
        assert event["event_id"] == f"fact:{event['source_id']}:1"
        assert authorization == "Bearer local-only-secret"
        assert "local-only-secret" not in first.stdout + first.stderr + second.stdout + second.stderr
        assert _row_census(root / "state.sqlite") == before_rows
        assert {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before_files
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
