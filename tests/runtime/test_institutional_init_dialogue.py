"""Init owns connection readiness and dialogue without a research run or stdin."""
from concurrent.futures import Future
import json
from pathlib import Path
import threading
import time

import pytest

from hypoloom.cli import build_parser
from hypoloom.control_protocol import ControlRequest, request_over_socket
from hypoloom.institutional_access import InstitutionalAccess
from hypoloom.institutional_prepare import run
from tests.runtime.test_institutional_preparation import PreparationBackend


def arguments(*extra):
    return build_parser().parse_args(["institutional", "prepare",
        "--institutional-browser-url", "http://127.0.0.1:9222", *extra])


def ready_access(monkeypatch, state="ready"):
    backend = PreparationBackend()
    backend.state = "available"
    access = InstitutionalAccess(backend)
    checks = []
    def check(run_id, *, timeout_seconds):
        checks.append(timeout_seconds)
        result = Future(); result.set_result({"state": state})
        return result
    access.check_connection = check
    monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda _: access)
    return access, backend, checks


def test_connection_failure_is_one_batch_result_without_platform_attempts(monkeypatch):
    access, backend, checks = ready_access(monkeypatch, "waiting_for_client")
    monkeypatch.setattr("hypoloom.institutional_prepare._observe", lambda *a: {"state": "acquisition_timeout"})
    output = []
    assert run(arguments(), input_fn=lambda _: pytest.fail("login requested"), output=output.append) == 1
    assert checks == [30] and backend.calls == [] and backend.closed
    summary = json.loads(output[-1])
    assert summary["connection"]["state"] == "waiting_for_client"
    assert {row["state"] for row in summary["preparation_summary"]} == {"not_checked"}


def test_terminal_eof_is_reported_without_claiming_login_or_leaking_browser(monkeypatch):
    _, backend, _ = ready_access(monkeypatch)
    def eof(_): raise EOFError
    output = []
    assert run(arguments("--provider", "IEEE"), input_fn=eof, output=output.append) == 1
    assert json.loads(output[-1])["preparation_summary"][0]["state"] == "input_required"
    assert backend.closed


def eventually(check):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        result = check()
        if result: return result
        time.sleep(.02)
    raise AssertionError("preparation did not reach the expected observation")


def request(root, command="PREPARATION_STATUS", payload=None):
    return request_over_socket(root / "control.sock",
        ControlRequest(1, "fixture", None, command, payload or {}), timeout=2)


@pytest.mark.parametrize("answer,expected", [("complete", "sample_verified"), ("skip", "skipped"), ("public_only", "public_only")])
def test_background_preparation_accepts_dialogue_and_cleans_control(tmp_path, monkeypatch, answer, expected):
    _, backend, _ = ready_access(monkeypatch)
    output, results, failures = [], [], []
    args = arguments("--agent-session", "--repository", str(tmp_path), "--provider", "IEEE")
    def worker():
        try: results.append(run(args, input_fn=lambda _: pytest.fail("background read stdin"), output=output.append))
        except BaseException as exc: failures.append(exc)
    thread = threading.Thread(target=worker); thread.start()
    root = None
    try:
        def discover():
            return next((Path(json.loads(line)["state_root"]) for line in output
                if line.startswith('{') and "state_root" in json.loads(line)), None)
        root = eventually(discover)
        def waiting():
            value = request(root).result
            return value if value.get("state") == "waiting_for_user" else None
        status = eventually(waiting)
        assert status["provider"] == "IEEE"
        assert len(status['completion_samples']) == 1 and [c[0] for c in backend.calls] == ['IEEE']
        rejected = request(root, "PREPARATION_ANSWER", {"provider": "Springer", "action": answer})
        assert rejected.status != "accepted"
        accepted = request(root, "PREPARATION_ANSWER", {"provider": "IEEE", "action": answer})
        assert accepted.status == "accepted"
        thread.join(5)
        assert not thread.is_alive() and not failures and results == [0]
        assert json.loads(output[-1])["preparation_summary"][0]["state"] == expected
        assert len(backend.calls) == (2 if answer == 'complete' else 1)
        assert backend.closed and not root.exists()
        assert not list(tmp_path.rglob("state.sqlite"))
    finally:
        if thread.is_alive() and root is not None:
            request(root, "PREPARATION_ANSWER", {"action": "finish"})
            thread.join(5)


def test_background_mode_rejects_noninteractive_mix_before_connecting(monkeypatch):
    monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda _: pytest.fail("connected"))
    with pytest.raises(ValueError, match="agent-session"):
        run(arguments("--agent-session", "--non-interactive"))


@pytest.mark.parametrize('action', ['complete', 'skip', 'public_only', 'finish', 'expire', 'manual_expire'])
def test_sample_challenge_waits_for_user_without_restarting_acquisition(tmp_path, monkeypatch, action):
    from hypoloom.institutional_instructions import render
    access, backend, _ = ready_access(monkeypatch)
    access.poll_seconds = .02
    if action == 'expire': access.acquisition_seconds = .6
    if action == 'manual_expire':
        from hypoloom.institutional_dialogue import PreparationDialogue
        monkeypatch.setattr('hypoloom.institutional_prepare.PreparationDialogue', lambda _: PreparationDialogue(.8))
    cleared = threading.Event(); calls = []
    fetch = backend.fetch
    def challenged(url, **kwargs):
        calls.append(kwargs)
        return fetch(url, **kwargs) if cleared.is_set() else {'state': 'verification_required'}
    backend.fetch = challenged
    output, results, errors = [], [], []
    def work():
        try: results.append(run(arguments('--agent-session', '--repository', str(tmp_path),
            '--provider', 'ScienceDirect'), output=output.append))
        except BaseException as exc: errors.append(exc)
    worker = threading.Thread(target=work); worker.start()
    root = eventually(lambda: next((Path(json.loads(v)['state_root']) for v in output
        if v.startswith('{') and 'state_root' in json.loads(v)), None))
    try:
        eventually(lambda: request(root).result.get('state') == 'waiting_for_user')
        assert request(root, 'PREPARATION_ANSWER', {'provider': 'ScienceDirect', 'action': 'verify',
            'sample': '10.1016/explicit.manual.sample'}).status == 'accepted'
        def sample_wait():
            if not worker.is_alive(): return None
            view = request(root).result
            return view if view.get('state') == 'waiting_for_user' and view.get('sample_url') else None
        view = eventually(sample_wait)
        assert view['reason_code'] == 'verification_required'
        assert '样本页面' in render(view) and '原获取期限' in render(view)
        assert 'verify' not in view['actions']
        item = next(iter(access._items.values())); deadline = item.deadline
        assert request(root, 'PREPARATION_ANSWER', {'provider': 'ScienceDirect', 'action': 'verify',
            'sample': '10.1016/another'}).status != 'accepted'
        if action not in {'expire', 'manual_expire'}:
            cleared.set()
            assert request(root, 'PREPARATION_ANSWER', {'provider': 'ScienceDirect', 'action': action}).status == 'accepted'
        worker.join(4)
        assert not worker.is_alive() and not errors
        summary = json.loads(output[-1]); row = summary['preparation_summary'][0]
        if action == 'finish':
            assert summary['preparation_state'] == 'finished'
        else:
            assert row['state'] == {'complete': 'sample_verified', 'skip': 'skipped',
                'public_only': 'public_only', 'expire': 'acquisition_timeout', 'manual_expire': 'input_timeout'}[action]
        assert summary['provider_modes'] == ({'ScienceDirect': 'public_only'} if action == 'public_only' else {})
        assert item.deadline == deadline and sum(c['force'] for c in calls) == 1
        assert [c[0] for c in backend.calls].count('ScienceDirect') == 1
        assert backend.closed and not root.exists() and not list(tmp_path.rglob('state.sqlite'))
    finally:
        if worker.is_alive(): request(root, 'PREPARATION_ANSWER', {'action': 'finish'}); worker.join(4)


@pytest.mark.parametrize('action,expected', [('skip', 'skipped'), ('public_only', 'public_only')])
def test_sample_wait_choice_overrides_earlier_sample_failure(tmp_path, monkeypatch, action, expected):
    _, backend, _ = ready_access(monkeypatch)
    first, second = 'https://doi.org/10.1016/first', 'https://doi.org/10.1016/second'
    backend.fetch = lambda url, **kw: {'state': 'identity_unconfirmed' if url == first else 'verification_required'}
    output, results = [], []
    worker = threading.Thread(target=lambda: results.append(run(arguments('--agent-session',
        '--repository', str(tmp_path), '--provider', 'ScienceDirect', '--sample', first, '--sample', second), output=output.append)))
    worker.start()
    root = eventually(lambda: next((Path(json.loads(v)['state_root']) for v in output
        if v.startswith('{') and 'state_root' in json.loads(v)), None))
    try:
        eventually(lambda: request(root).result.get('sample_url') == second)
        assert request(root, 'PREPARATION_ANSWER', {'provider': 'ScienceDirect', 'action': action}).status == 'accepted'
        worker.join(4)
        assert not worker.is_alive() and results == [0]
        summary = json.loads(output[-1]); row = summary['preparation_summary'][0]
        assert row['state'] == expected and row['samples'][0]['state'] == 'identity_unconfirmed'
        assert summary['provider_modes'] == ({'ScienceDirect': 'public_only'} if action == 'public_only' else {})
        assert summary['remaining_preparation_argv'] == [] and backend.closed and not root.exists()
    finally:
        if worker.is_alive(): request(root, 'PREPARATION_ANSWER', {'action': 'finish'}); worker.join(4)


@pytest.mark.parametrize('action', ['skip', 'public_only'])
def test_sample_skip_prevents_later_platform_ticks_from_fetching_it_again(tmp_path, monkeypatch, action):
    from hypoloom.institutional_access import publisher
    access, backend, _ = ready_access(monkeypatch)
    access.poll_seconds = .01
    fetch = backend.fetch; attempts = []
    def challenged(url, **kwargs):
        if publisher(url) == 'ScienceDirect':
            attempts.append(url)
            return {'state': 'verification_required'}
        return fetch(url, **kwargs)
    backend.fetch = challenged
    output, results = [], []
    worker = threading.Thread(target=lambda: results.append(run(arguments('--agent-session',
        '--repository', str(tmp_path), '--provider', 'ScienceDirect', '--provider', 'Springer'), output=output.append)))
    worker.start()
    root = eventually(lambda: next((Path(json.loads(v)['state_root']) for v in output
        if v.startswith('{') and 'state_root' in json.loads(v)), None))
    try:
        eventually(lambda: request(root).result.get('state') == 'waiting_for_user')
        assert request(root, 'PREPARATION_ANSWER', {'provider': 'ScienceDirect', 'action': 'verify',
            'sample': '10.1016/explicit.manual.sample'}).status == 'accepted'
        eventually(lambda: request(root).result.get('sample_url'))
        assert request(root, 'PREPARATION_ANSWER', {'provider': 'ScienceDirect', 'action': action}).status == 'accepted'
        eventually(lambda: request(root).result.get('provider') == 'Springer'
            and request(root).result.get('state') == 'waiting_for_user')
        assert request(root, 'PREPARATION_ANSWER', {'provider': 'Springer', 'action': 'complete'}).status == 'accepted'
        worker.join(4)
        assert not worker.is_alive() and results == [0]
        assert len(attempts) == 1, 'a skipped sample was fetched while preparing another platform'
        assert json.loads(output[-1])['preparation_summary'][1]['state'] == 'sample_verified'
        assert backend.closed and not root.exists()
    finally:
        if worker.is_alive(): request(root, 'PREPARATION_ANSWER', {'action': 'finish'}); worker.join(4)


def test_fixed_sample_challenge_advances_without_manual_download_or_background_retries(tmp_path, monkeypatch):
    from hypoloom.institutional_access import publisher
    access, backend, _ = ready_access(monkeypatch)
    access.poll_seconds = .01
    fetch = backend.fetch; attempts = []
    def challenged(url, **kwargs):
        if publisher(url) == 'ScienceDirect':
            attempts.append(url)
            return {'state': 'verification_required'}
        return fetch(url, **kwargs)
    backend.fetch = challenged
    output, results = [], []
    worker = threading.Thread(target=lambda: results.append(run(arguments('--agent-session',
        '--repository', str(tmp_path), '--provider', 'ScienceDirect', '--provider', 'Springer'), output=output.append)))
    worker.start()
    root = eventually(lambda: next((Path(json.loads(v)['state_root']) for v in output
        if v.startswith('{') and 'state_root' in json.loads(v)), None))
    try:
        eventually(lambda: request(root).result.get('state') == 'waiting_for_user')
        assert request(root, 'PREPARATION_ANSWER', {'provider': 'ScienceDirect', 'action': 'complete'}).status == 'accepted'
        eventually(lambda: request(root).result.get('provider') == 'Springer'
            and request(root).result.get('state') == 'waiting_for_user')
        time.sleep(.1)
        assert request(root, 'PREPARATION_ANSWER', {'provider': 'Springer', 'action': 'complete'}).status == 'accepted'
        worker.join(4)
        assert not worker.is_alive() and results == [1]
        rows = json.loads(output[-1])['preparation_summary']
        assert [r['state'] for r in rows] == ['verification_required', 'sample_verified']
        assert len(attempts) == 1 and backend.closed and not root.exists()
        assert not list(tmp_path.rglob('state.sqlite'))
    finally:
        if worker.is_alive(): request(root, 'PREPARATION_ANSWER', {'action': 'finish'}); worker.join(4)


def test_finish_during_automatic_sample_check_prevents_next_platform(tmp_path, monkeypatch):
    access, backend, _ = ready_access(monkeypatch)
    started, release = threading.Event(), threading.Event()
    fetch = backend.fetch
    def blocked(url, **kwargs):
        started.set()
        assert release.wait(5)
        return fetch(url, **kwargs)
    backend.fetch = blocked
    output, codes = [], []
    worker = threading.Thread(target=lambda: codes.append(run(arguments('--agent-session',
        '--repository', str(tmp_path), '--provider', 'IEEE', '--provider', 'Springer'), output=output.append)))
    worker.start(); root = None
    try:
        root = eventually(lambda: next((Path(json.loads(v)['state_root']) for v in output
            if v.startswith('{') and 'state_root' in json.loads(v)), None))
        eventually(lambda: request(root).result.get('state') == 'waiting_for_user')
        assert request(root, 'PREPARATION_ANSWER', {'provider': 'IEEE', 'reply': '完成'}).status == 'accepted'
        assert started.wait(3)
        assert request(root).result['state'] == 'processing'
        assert request(root, 'PREPARATION_ANSWER', {'reply': '结束准备'}).status == 'accepted'
        release.set(); worker.join(5)
        assert not worker.is_alive() and codes == [0]
        assert json.loads(output[-1])['preparation_state'] == 'finished'
        assert not any(c[0] == 'Springer' for c in backend.calls)
        assert backend.closed and not root.exists() and not list(tmp_path.rglob('state.sqlite'))
        assert all(item.document is None for item in access._items.values())
    finally:
        release.set()
        if worker.is_alive() and root is not None:
            request(root, 'PREPARATION_ANSWER', {'action': 'finish'}); worker.join(5)


def test_dialogue_wait_expires_without_marking_any_platform_complete(tmp_path, monkeypatch):
    from hypoloom.institutional_dialogue import PreparationDialogue
    _, backend, _ = ready_access(monkeypatch)
    monkeypatch.setattr("hypoloom.institutional_prepare.PreparationDialogue", lambda _: PreparationDialogue(.03))
    output = []
    assert run(arguments("--agent-session", "--repository", str(tmp_path)), output=output.append) == 1
    summary = json.loads(output[-1])
    assert summary["preparation_state"] == "input_timeout"
    assert {r["state"] for r in summary["preparation_summary"]} == {"not_checked"}
    assert [c[0] for c in backend.calls] == ["IEEE"] and backend.closed
    assert not list((tmp_path / "state").iterdir())


def test_background_retries_connection_then_verifies_sample_through_cli(tmp_path, monkeypatch, capsys):
    from tests.runtime.test_institutional_access import URL
    access, backend, checks = ready_access(monkeypatch)
    backend.state = "available"
    def check(run_id, *, timeout_seconds):
        checks.append(timeout_seconds)
        future = Future(); future.set_result({"state": "waiting_for_client" if len(checks) == 1 else "ready"})
        return future
    access.check_connection = check
    output, results, errors = [], [], []
    def worker():
        try: results.append(run(arguments("--agent-session", "--repository", str(tmp_path), "--provider", "Nature"), output=output.append))
        except BaseException as exc: errors.append(exc)
    thread = threading.Thread(target=worker); thread.start()
    root = eventually(lambda: next((Path(json.loads(v)["state_root"]) for v in output if v.startswith('{') and "state_root" in json.loads(v)), None))
    try:
        eventually(lambda: request(root).result.get("state") == "waiting_for_connection")
        assert not backend.calls
        parser = build_parser()
        def cli(action, *extra):
            return run(parser.parse_args(["institutional", "preparation", action, "--state-root", str(root), *extra]))
        assert cli("status") == 0
        assert json.loads(capsys.readouterr().out)["result"]["state"] == "waiting_for_connection"
        assert cli("retry_connection") == 0
        eventually(lambda: request(root).result.get("state") == "waiting_for_user")
        assert cli("verify", "--provider", "Nature", "--sample", URL) == 0
        thread.join(5)
        assert not thread.is_alive() and not errors and results == [0]
        assert [c[0] for c in backend.calls] == ["Nature", URL]
        assert len(checks) == 2
        assert json.loads(output[-1])["preparation_summary"][0]["state"] == "sample_verified"
        assert backend.closed and not root.exists()
    finally:
        if thread.is_alive():
            request(root, "PREPARATION_ANSWER", {"action": "finish"}); thread.join(5)


@pytest.mark.parametrize("automatic", [False, True])
def test_finish_during_connection_check_releases_preparation(tmp_path, monkeypatch, automatic):
    from hypoloom import institutional_dialogue
    monkeypatch.setattr(institutional_dialogue, "CONNECTION_RETRY_SECONDS", .01)
    access, backend, _ = ready_access(monkeypatch)
    pending = Future()
    checks = []
    def check(*a, **k):
        checks.append(k)
        if automatic and len(checks) == 1:
            initial = Future(); initial.set_result({"state": "waiting_for_client"}); return initial
        return pending
    access.check_connection = check
    output, results = [], []
    thread = threading.Thread(target=lambda: results.append(run(
        arguments("--agent-session", "--repository", str(tmp_path)), output=output.append)))
    thread.start()
    root = eventually(lambda: next((Path(json.loads(v)["state_root"]) for v in output if v.startswith('{') and "state_root" in json.loads(v)), None))
    try:
        eventually(lambda: len(checks) == (2 if automatic else 1))
        assert request(root, "PREPARATION_ANSWER", {"action": "finish"}).status == "accepted"
        pending.set_result({"state": "ready"})  # Late success must not open a platform after finish.
        thread.join(5)
        assert not thread.is_alive() and results == [0]
        assert backend.closed and not backend.calls and not root.exists()
        assert json.loads(output[-1])["preparation_state"] == "finished"
    finally:
        if thread.is_alive():
            if not pending.done(): pending.set_result({"state": "browser_unavailable"})
            thread.join(5)


def test_late_client_attaches_without_agent_reply_or_polling(tmp_path, monkeypatch):
    from hypoloom import institutional_dialogue
    monkeypatch.setattr(institutional_dialogue, "CONNECTION_RETRY_SECONDS", .05, raising=False)
    access, backend, checks = ready_access(monkeypatch)
    connected = threading.Event()
    def check(run_id, *, timeout_seconds):
        checks.append(timeout_seconds)
        result = Future()
        result.set_result({"state": "ready"} if connected.is_set() else
                          {"state": "browser_unavailable", "reason_code": "cdp_connection_refused"})
        return result
    access.check_connection = check
    output, codes = [], []
    thread = threading.Thread(target=lambda: codes.append(run(
        arguments("--agent-session", "--repository", str(tmp_path), "--provider", "IEEE"), output=output.append)))
    thread.start()
    root = eventually(lambda: next((Path(json.loads(v)["state_root"]) for v in output
        if v.startswith('{') and "state_root" in json.loads(v)), None))
    try:
        eventually(lambda: len(checks) >= 2)
        assert not backend.calls
        connected.set()  # Client becomes available without any dialogue request.
        eventually(lambda: bool(backend.calls))
        eventually(lambda: request(root).result.get("state") == "waiting_for_user")
        status = request(root).result
        assert status["state"] == "waiting_for_user" and status["provider"] == "IEEE"
        assert request(root, "PREPARATION_ANSWER", {"provider": "IEEE", "reply": "完成"}).status == "accepted"
        thread.join(3)
        assert not thread.is_alive() and codes == [0]
        assert backend.closed and not root.exists() and not list(tmp_path.rglob("state.sqlite"))
    finally:
        if thread.is_alive():
            request(root, "PREPARATION_ANSWER", {"action": "finish"}); thread.join(3)


def test_automatic_connection_deadline_is_not_extended_by_replies_or_status(tmp_path, monkeypatch):
    from hypoloom import institutional_dialogue
    monkeypatch.setattr(institutional_dialogue, "CONNECTION_RETRY_SECONDS", .04, raising=False)
    monkeypatch.setattr("hypoloom.institutional_prepare.PreparationDialogue", lambda _: institutional_dialogue.PreparationDialogue(.5))
    access, backend, checks = ready_access(monkeypatch)
    pending = Future()
    def check(run_id, *, timeout_seconds):
        checks.append(timeout_seconds)
        if len(checks) == 1:
            initial = Future(); initial.set_result({"state": "browser_unavailable", "reason_code": "cdp_timeout"})
            return initial
        return pending
    access.check_connection = check
    output, codes = [], []
    started = time.monotonic()
    thread = threading.Thread(target=lambda: codes.append(run(
        arguments("--agent-session", "--repository", str(tmp_path)), output=output.append)))
    thread.start()
    root = eventually(lambda: next((Path(json.loads(v)["state_root"]) for v in output
        if v.startswith('{') and "state_root" in json.loads(v)), None))
    try:
        eventually(lambda: len(checks) == 2)
        for _ in range(3):
            assert request(root).result["state"] == "waiting_for_connection"
            request(root, "PREPARATION_ANSWER", {"reply": "继续连接"})
            time.sleep(.03)
        assert len(checks) == 2, "only one connection check may be in flight"
        thread.join(2)
        assert not thread.is_alive() and time.monotonic() - started < 2 and codes == [1]
        summary = json.loads(output[-1])
        assert summary["preparation_state"] == "input_timeout"
        assert summary["connection"]["reason_code"] == "cdp_timeout"
        assert 0 < checks[1] <= .5
        assert not backend.calls and backend.closed and not root.exists()
    finally:
        pending.set_result({"state": "ready"})
        if thread.is_alive():
            request(root, "PREPARATION_ANSWER", {"action": "finish"}); thread.join(3)
