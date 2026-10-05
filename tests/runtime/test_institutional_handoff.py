"""One preparation owner survives retry/review, then releases its exact scope."""
import json
from pathlib import Path
import threading
import subprocess
import sys
import tempfile

import pytest

from hypoloom.cli import build_parser
from hypoloom.institutional_prepare import run
from hypoloom.institutional_runtime import RunBrowser
from tests.runtime.test_institutional_init_dialogue import eventually, request


def start(tmp_path, monkeypatch, *, fail_second=False):
    from hypoloom import institutional_handoff
    monkeypatch.setattr(institutional_handoff, "RELEASE_SECONDS", .02)
    scope = {"state_root": str(tmp_path / "state" / "research"), "run_id": "research", "request_id": "research-create-1"}
    observed = {"failed": fail_second, "calls": [], "launched": False, "targets": []}
    def launched(target):
        observed["targets"].append(target)
        return observed["launched"]
    monkeypatch.setattr(institutional_handoff, "_launched", launched)
    monkeypatch.setattr(RunBrowser, "check_connection", lambda *a, **kw: {"state": "ready"})
    def prepare(self, provider, **kw):
        observed["calls"].append(provider)
        return {"state": "failed" if provider == "Springer" and observed["failed"] else "opened"}
    monkeypatch.setattr(RunBrowser, "prepare", prepare)
    args = build_parser().parse_args(["institutional", "prepare", "--repository", str(tmp_path),
        "--agent-session", "--institutional-browser-mode", "client", "--provider", "IEEE", "--provider", "Springer",
        "--institutional-prepare-failure-mode", "review", "--state-root", scope["state_root"],
        "--run-id", scope["run_id"], "--request-id", scope["request_id"]])
    output, codes, errors = [], [], []
    def worker():
        try: codes.append(run(args, output=output.append))
        except BaseException as exc: errors.append(exc)
    thread = threading.Thread(target=worker)
    thread.start()
    root = eventually(lambda: next((Path(json.loads(line)["state_root"]) for line in output
        if line.startswith("{") and "state_root" in json.loads(line)), None))
    return root, scope, observed, output, codes, errors, thread


def wait(root, state, provider=None):
    def check():
        result = request(root).result
        return result if result.get("state") == state and (provider is None or result.get("provider") == provider) else None
    return eventually(check)


def answer(root, action, provider=None):
    payload = {"action": action, **({"provider": provider} if provider else {})}
    result = request(root, "PREPARATION_ANSWER", payload)
    assert result.status == "accepted", result


def cleanup(root, thread):
    if thread.is_alive():
        try: answer(root, "finish")
        except OSError: pass
        thread.join(5)
    assert not thread.is_alive()


def test_retry_preserves_control_browser_scope_and_completed_publishers(tmp_path, monkeypatch):
    from hypoloom.institutional_client import connections
    root, scope, observed, output, codes, errors, thread = start(tmp_path, monkeypatch, fail_second=True)
    try:
        wait(root, "waiting_for_user", "IEEE")
        initial = connections(**{ "root": scope["state_root"], "run_id": scope["run_id"],
            "request_id": scope["request_id"], "preparation_root": root})
        answer(root, "skip", "IEEE")
        first = wait(root, "awaiting_launch")
        assert first["summary"]["remaining_providers"] == ["Springer"]
        assert [r["state"] for r in first["summary"]["preparation_summary"]] == ["skipped", "failed"]
        assert first["remaining_preparation_argv"][-3:] == ["retry", "--state-root", str(root)]
        assert thread.is_alive() and not Path(scope["state_root"]).exists()
        observed["failed"] = False
        parsed = build_parser().parse_args(first["remaining_preparation_argv"][4:])
        assert run(parsed, output=lambda _: None) == 0
        wait(root, "waiting_for_user", "Springer")
        current = connections(scope["state_root"], scope["run_id"], scope["request_id"], preparation_root=root)
        assert current["scope"] == initial["scope"] == scope
        assert current["connections"][0]["socket_path"] == initial["connections"][0]["socket_path"]
        assert observed["calls"] == ["IEEE", "Springer", "Springer"]
        answer(root, "skip", "Springer")
        final = wait(root, "awaiting_launch")
        assert final["remaining_preparation_argv"] == []
        assert [r["provider"] for r in final["summary"]["preparation_summary"]] == ["IEEE", "Springer"]
        assert len(list((tmp_path / "state").iterdir())) == 1
        answer(root, "finish")
        thread.join(4)
        assert not errors and codes == [0] and not root.exists()
        assert not list(tmp_path.rglob("state.sqlite"))
    finally: cleanup(root, thread)


@pytest.mark.parametrize("ending", ["launch", "cancel", "expire"])
def test_handoff_and_abandonment_release_without_creating_research(tmp_path, monkeypatch, ending):
    from hypoloom import institutional_handoff
    if ending == "expire": monkeypatch.setattr(institutional_handoff, "HANDOFF_SECONDS", .2)
    root, scope, observed, output, codes, errors, thread = start(tmp_path, monkeypatch)
    try:
        for provider in ("IEEE", "Springer"):
            wait(root, "waiting_for_user", provider)
            answer(root, "skip", provider)
        view = wait(root, "awaiting_launch")
        assert "finish_argv" in view and view["summary"]["scope"] == scope
        if ending == "launch": observed["launched"] = True
        elif ending == "cancel": answer(root, "finish")
        thread.join(4)
        assert not thread.is_alive() and not errors and codes == [0]
        reason = {"launch": "run_handoff", "cancel": "operator_finished", "expire": "launch_review_expired"}[ending]
        assert json.loads(output[-1]) == {"preparation_lifecycle": {"state": "finished", "reason": reason}}
        assert not root.exists() and not Path(scope["state_root"]).exists()
        assert all(target == scope for target in observed["targets"])
    finally: cleanup(root, thread)


def test_client_preparation_rejects_implicit_target_before_browser_creation(monkeypatch):
    monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda *_: pytest.fail("created unscoped browser"))
    args = build_parser().parse_args(["institutional", "prepare", "--agent-session", "--institutional-browser-mode", "client"])
    with pytest.raises(ValueError, match="exact"):
        run(args)


def test_normal_review_expiry_preserves_failed_sample_and_exit_code(tmp_path, monkeypatch):
    from hypoloom import institutional_handoff
    monkeypatch.setattr(institutional_handoff, "HANDOFF_SECONDS", .2)
    root, scope, observed, output, codes, errors, thread = start(tmp_path, monkeypatch, fail_second=True)
    try:
        wait(root, "waiting_for_user", "IEEE")
        answer(root, "skip", "IEEE")
        view = wait(root, "awaiting_launch")
        assert view["summary"]["remaining_providers"] == ["Springer"]
        thread.join(4)
        assert not thread.is_alive() and not errors and codes == [1]
        events = [json.loads(line) for line in output if line.startswith("{")]
        summaries = [event for event in events if "preparation_summary" in event]
        assert len(summaries) == 1
        assert [row["state"] for row in summaries[0]["preparation_summary"]] == ["skipped", "failed"]
        assert events[-1] == {"preparation_lifecycle": {"state": "finished", "reason": "launch_review_expired"}}
        assert not root.exists() and not Path(scope["state_root"]).exists()
    finally: cleanup(root, thread)


def test_program_generated_windows_entry_quotes_exact_scope():
    from hypoloom.institutional_instructions import client_entry
    target = {"state_root": "/tmp/research's state/run", "run_id": "run", "request_id": "create-1"}
    entry = client_entry("/tmp/research's checkout", "/python", target=target, preparation_root="/tmp/preparation")
    assert "-StateRoot '/tmp/research''s state/run'" in entry["powershell"]
    assert "-RunId 'run' -RequestId 'create-1' -PreparationRoot '/tmp/preparation'" in entry["powershell"]
    assert "Read-Host" not in entry["powershell"]


def test_preparation_hands_off_to_real_host_and_terminal_survives_host_exit(tmp_path, monkeypatch):
    from hypoloom import institutional_handoff
    from hypoloom.institutional_client import connections
    from hypoloom.control_protocol import ControlRequest, request_over_socket
    from hypoloom.state_store import StateStore
    from tests.runtime.test_expert_loop_host import _pilot_payload
    actual_check = institutional_handoff._launched
    root, scope, observed, output, codes, errors, thread = start(tmp_path, monkeypatch)
    monkeypatch.setattr(institutional_handoff, "_launched", actual_check)
    process = None
    try:
        for provider in ("IEEE", "Springer"):
            wait(root, "waiting_for_user", provider)
            answer(root, "skip", provider)
        wait(root, "awaiting_launch")
        final = Path(scope["state_root"])
        process = subprocess.Popen([sys.executable, "-B", "-m", "hypoloom", "serve",
            "--state-root", str(final), "--institutional-browser-mode", "client", "--exit-when-terminal"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        eventually(lambda: (final / "control.sock").exists())
        before = connections(final, scope["run_id"], scope["request_id"], preparation_root=root)
        assert before["lifecycle"] == "awaiting_launch"
        assert before["connections"][0]["state_root"] == str(root)
        assert thread.is_alive(), "a live but empty Host must not claim a created run"
        inputs = tmp_path / "profile-inputs"; inputs.mkdir()
        with tempfile.TemporaryDirectory(prefix="handoff-target-", dir="/var/tmp") as target:
            payload = _pilot_payload(inputs, Path(target), scope["run_id"])
            payload["execution"]["delay_seconds"] = 60
            result = request_over_socket(final / "control.sock",
                ControlRequest(1, scope["request_id"], None, "CREATE_RUN", payload), timeout=10)
            assert result.status == "accepted", result
            thread.join(5)
            assert not thread.is_alive() and not errors and codes == [0] and not root.exists()
            assert json.loads(output[-1])["preparation_lifecycle"]["reason"] == "run_handoff"
            def connected():
                selected = connections(final, scope["run_id"], scope["request_id"])
                return selected if selected["connections"] else None
            current = eventually(connected)
            assert current["lifecycle"] == "active" and current["scope"] == scope
            assert current["connections"][0]["run_id"] == scope["run_id"]
            result = request_over_socket(final / "control.sock", ControlRequest(1, "cancel-fixture", None,
                "CANCEL", {"run_id": scope["run_id"], "cause": "disposable handoff validation"}), timeout=10)
            assert result.status == "accepted", result
            stdout, stderr = process.communicate(timeout=20)
            assert process.returncode == 0, stdout + stderr
            assert not (final / "control.sock").exists()
            ended = connections(final, scope["run_id"], scope["request_id"])
            assert ended["lifecycle"] == "terminal" and ended["connections"] == []
            assert not StateStore(final).inspect("SELECT 1 FROM execution_attempts WHERE state != 'CLOSED'")
            assert not Path(current["connections"][0]["socket_path"]).parent.exists()
    finally:
        cleanup(root, thread)
        if process is not None and process.poll() is None:
            process.terminate(); process.communicate(timeout=15)
