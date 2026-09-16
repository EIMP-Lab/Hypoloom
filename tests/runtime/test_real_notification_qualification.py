"""Opt-in, one-event Telegram qualification; never loads .env or starts models.

The operator must inject only the selected Telegram credentials and explicitly
set HYPOLOOM_REAL_TELEGRAM=1. A failed first poll is never retried here.
Offline tests replace post_json; the opt-in case delegates to production HTTP.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

from hypoloom import notification_tailer as notifications
from hypoloom.agent_aci import CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import FakeAgent
from hypoloom.observation_api import build_product_observation
from hypoloom.oracle import Oracle
from hypoloom.workflow_host import WorkflowHost
from test_profile_admission import _materialize_expert_loop_pilot_v3
from test_profile_v4 import documents
from test_web_observer import _row_census


_ENABLE = "HYPOLOOM_REAL_TELEGRAM"
_BOT = "HYPOLOOM_TELEGRAM_BOT_TOKEN"
_CHAT = "HYPOLOOM_TELEGRAM_CHAT_ID"
_PROXY_ENV = "HYPOLOOM_NOTIFICATION_PROXY_ENV"
_RUN_ID = "notification-qualification-TEST-only"


class _ReportOnlyAgent(FakeAgent):
    def invoke(self, request):
        if request.command is not AgentCommand.FINAL_REPORT:
            raise AssertionError("notification fixture must not start scientific Agents")
        return super().invoke(request)


def _no_process(*_args, **_kwargs):
    raise AssertionError("notification fixture must not start a Process")


def _create_cancelled_fixture(root: Path) -> Path:
    """Normal current-ACI CREATE/CANCEL, with only a deterministic stop report."""
    from hypoloom.profile_admission import admit_profile

    root.chmod(0o700)
    profile, target, data = _materialize_expert_loop_pilot_v3(root)
    source = documents()
    source["goal"]["title"] = "TEST ONLY — notification delivery qualification"
    source["methodology"]["runtime"]["target_repo"] = str(target)
    source["safety"]["allow_read_paths"] = [str(data)]
    source["safety"]["gpus"] = []
    source["notify"] = {
        "sinks": {"telegram": {
            "enabled": True, "bot_token_env": _BOT, "chat_id_env": _CHAT,
        }},
        "events": {"telegram": ["run_started"]},
    }
    for name, value in source.items():
        (profile / f"{name}.yaml").write_text(yaml.safe_dump(value), encoding="utf-8")
    admitted = admit_profile(profile, run_id=_RUN_ID)
    assert admitted.admitted, admitted.to_dict()
    state_root = root / "state"
    host = WorkflowHost(state_root, local_agent=_ReportOnlyAgent())
    host.runner.start = _no_process
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1, request_id="create-" + _RUN_ID,
            expected_revision=0, command="CREATE_RUN", payload=admitted.create_run_payload,
        ))
        assert created.status == "accepted", created
        assert not host.store.list_attempts()
        cancelled = host.handle(ControlRequest(
            schema_version=1, request_id="cancel-" + _RUN_ID,
            expected_revision=created.revision, command="CANCEL",
            payload={"run_id": _RUN_ID, "cause": "TEST ONLY; no research or model invocation requested"},
        ))
        assert cancelled.status == "accepted", cancelled
        for _ in range(4):
            host.tick()
            if host.store.latest_transition(_RUN_ID)["state"] == "CANCELLED":
                break
        assert host.store.latest_transition(_RUN_ID)["state"] == "CANCELLED"
        attempts = host.store.list_attempts()
        assert len(attempts) == 1
        attempt = attempts[0]
        assert (attempt["kind"], attempt["state"], attempt["terminal_reason"]) == (
            "AGENT", "CLOSED", "SUCCEEDED",
        )
        request = json.loads(attempt["observation_json"])["request"]
        assert request["command"] == "FINAL_REPORT"
        assert request["schema_version"] == CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION
        assert [row["fact_type"] for row in host.store.list_facts(_RUN_ID)] == ["GoalDefined"]
        assert not host.running and not host.running_agents
    finally:
        host.close()
    return state_root


def _source_snapshot(state_root: Path):
    # Ordinary before/after rows and bytes, not a new digest or delivery authority.
    return _row_census(state_root / "state.sqlite"), {
        str(path.relative_to(state_root)): path.read_bytes()
        for path in state_root.rglob("*") if path.is_file()
    }


class _ObservedPost:
    """Delegate real HTTP, retaining no URL, recipient, response body or token."""
    def __init__(self):
        self.attempts = 0
        self.calls = 0
        self.responses = []

    def __call__(self, url, document, *, headers, timeout, proxy_url=None):
        __tracebackhide__ = True
        self.attempts += 1
        if self.attempts > 1:
            raise notifications.NotificationError("unexpected second POST blocked; only one TEST send authorized")
        self.calls += 1
        try:
            options = {} if proxy_url is None else {"proxy_url": proxy_url}
            response = notifications.post_json(url, document, headers=headers, timeout=timeout, **options)
        except Exception as error:
            self.responses.append({"http_status": None, "telegram_ok": None,
                                   "error_type": type(error).__name__})
            raise notifications.NotificationError("qualification HTTP failed; no automatic retry") from None
        try:
            body = json.loads(response.body)
            ok = isinstance(body, dict) and body.get("ok") is True
        except (ValueError, TypeError):
            ok = False
        self.responses.append({"http_status": response.status, "telegram_ok": ok})
        return response


def _qualify(root: Path, *, environ, proxy_env=None) -> dict:
    __tracebackhide__ = True
    state_root = _create_cancelled_fixture(root)
    output_root = root / "delivery"
    before = _source_snapshot(state_root)
    observation = build_product_observation(state_root, _RUN_ID)
    selected = [event for event in observation["events"] if event["type"] == "run_started"]
    assert len(selected) == 1 and selected[0]["source"] == "fact"
    assert "TEST" in selected[0]["run_id"]
    oracle = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(_RUN_ID)
    assert oracle.ok, oracle.to_json()
    assert oracle.canonical_outcome_count == oracle.open_attempt_count == oracle.pending_effect_count == 0
    assert oracle.owned_process_residue == ()
    post = _ObservedPost()
    first = notifications.NotificationTailer(
        state_root, _RUN_ID, output_root, environ=environ, transport=post, proxy_env=proxy_env,
    ).poll_once()
    second = None
    if not first.errors and first.delivered == {"telegram": 1}:
        # Same source and cursor, but a new tailer instance. Never retry failure.
        second = notifications.NotificationTailer(
            state_root, _RUN_ID, output_root, environ=environ, transport=post, proxy_env=proxy_env,
        ).poll_once()
    after_oracle = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(_RUN_ID)
    source_unchanged = _source_snapshot(state_root) == before
    evidence = {
        "run_id": _RUN_ID, "run_state": "CANCELLED", "selected_event_id": selected[0]["event_id"],
        "selected_event_type": "run_started", "first_poll": asdict(first),
        "second_poll": None if second is None else asdict(second),
        "post_count": post.calls, "post_attempt_count": post.attempts,
        "http": post.responses, "source_unchanged": source_unchanged,
        "oracle_ok": after_oracle.ok, "real_model_calls": 0, "process_attempts": 0,
        "fake_final_report_attempts": 1,
        "proxy_enabled": proxy_env is not None,
    }
    (root / "notification-qualification.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    assert source_unchanged
    assert after_oracle.ok, after_oracle.to_json()
    assert post.calls == post.attempts == 1
    if first.errors:
        assert second is None
        assert not (output_root / _RUN_ID / "cursors" / "telegram.json").exists()
    else:
        assert first.delivered == {"telegram": 1}
        assert second is not None and second.delivered == {"telegram": 0} and not second.errors
        cursor = json.loads((output_root / _RUN_ID / "cursors" / "telegram.json").read_text())
        assert cursor == {f"{selected[0]['source']}:{selected[0]['source_id']}": selected[0]["revision"]}
    return evidence


def test_qualification_wrapper_never_sends_a_second_POST(monkeypatch):
    calls = []

    def offline_post(*_args, **_kwargs):
        calls.append(True)
        return notifications.HTTPResponse(200, b'{"ok":true}')

    monkeypatch.setattr(notifications, "post_json", offline_post)
    post = _ObservedPost()
    post("https://telegram.invalid/fixture", {}, headers={}, timeout=10.0)
    with pytest.raises(notifications.NotificationError, match="second POST blocked"):
        post("https://telegram.invalid/fixture", {}, headers={}, timeout=10.0)
    assert len(calls) == post.calls == 1 and post.attempts == 2


@pytest.mark.parametrize("response", ["accepted", "rejected", "http_failure", "transport_failure"])
def test_offline_telegram_qualification_and_failure_never_retries(tmp_path, monkeypatch, response):
    def offline_post(_url, document, *, headers, timeout):
        assert set(document) == {"chat_id", "text"}
        assert "TEST" in document["text"] and "run_started" in document["text"]
        assert headers == {} and timeout == 10.0
        if response == "transport_failure":
            raise OSError("local fixture response; never sent")
        return notifications.HTTPResponse(
            503 if response == "http_failure" else 200,
            json.dumps({"ok": response == "accepted"}).encode(),
        )

    monkeypatch.setattr(notifications, "post_json", offline_post)
    evidence = _qualify(tmp_path, environ={_BOT: "123:local-fixture-token", _CHAT: "local-fixture-chat"})
    assert bool(evidence["first_poll"]["errors"]) is (response != "accepted")
    assert evidence["post_count"] == 1 and evidence["source_unchanged"] and evidence["oracle_ok"]
    assert (evidence["second_poll"] is not None) is (response == "accepted")
    saved = (tmp_path / "notification-qualification.json").read_text()
    assert "local-fixture-token" not in saved and "local-fixture-chat" not in saved


@pytest.mark.skipif(os.environ.get(_ENABLE) != "1", reason=f"explicitly set {_ENABLE}=1 for one real TEST notification")
def test_one_real_telegram_TEST_event_with_restart_deduplication(tmp_path):
    __tracebackhide__ = True
    # The operator supplies these two values securely; no .env or ambient config scan.
    environ = {name: os.environ.get(name, "") for name in (_BOT, _CHAT)}
    if not all(environ.values()):
        pytest.fail("selected Telegram environment variables must both be nonempty", pytrace=False)
    proxy_env = os.environ.get(_PROXY_ENV) or None
    if proxy_env is not None:
        environ[proxy_env] = os.environ.get(proxy_env, "")
    evidence = _qualify(tmp_path, environ=environ, proxy_env=proxy_env)
    assert evidence["first_poll"]["errors"] == [], evidence
    assert evidence["http"] == [{"http_status": 200, "telegram_ok": True}], evidence
    assert evidence["post_count"] == 1


def test_qualification_explicit_proxy_preserves_single_send_and_restart(tmp_path, monkeypatch):
    def offline_post(_url, _document, *, headers, timeout, proxy_url):
        assert proxy_url == "http://127.0.0.1:7891"
        assert headers == {} and timeout == 10.0
        return notifications.HTTPResponse(200, b'{"ok":true}')

    monkeypatch.setattr(notifications, "post_json", offline_post)
    evidence = _qualify(tmp_path, proxy_env="TEST_PROXY", environ={
        _BOT: "123:local-fixture-token", _CHAT: "local-fixture-chat",
        "TEST_PROXY": "http://127.0.0.1:7891",
    })
    assert evidence["proxy_enabled"] and evidence["post_count"] == 1
    assert evidence["first_poll"]["delivered"] == {"telegram": 1}
    assert evidence["second_poll"]["delivered"] == {"telegram": 0}
