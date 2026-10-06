"""Fixed init review over the real private preparation control and one launch."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading

import pytest

from hypoloom import bootstrap, bootstrap_inspect, bootstrap_session, institutional_handoff
from hypoloom.institutional_runtime import RunBrowser
from tests.runtime.test_bootstrap import ROOT, checkout, arguments
from tests.runtime.test_institutional_handoff import wait, answer, cleanup
from tests.runtime.test_institutional_init_dialogue import eventually, request


@pytest.fixture
def prepared(checkout, capsys, monkeypatch):
    repo, overrides = checkout
    assert bootstrap.main([*arguments(repo, overrides), "--write"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(bootstrap_inspect, "supervisor_check", lambda: {"status": "ready", "linger": "yes"})
    assert bootstrap.main([*arguments(repo, [], "inspect", template=None), "--auth-transport", "none", "--offline",
        "--institutional-browser-mode", "client", "--institutional-prepare-provider", "IEEE",
        "--institutional-prepare-provider", "ACM"]) == 0
    inspected = json.loads(capsys.readouterr().out)
    argv = inspected["init_session_argv"][4:]
    assert argv[0] == "session" and "--start" not in argv
    args = bootstrap.build_parser().parse_args(argv)
    assert bootstrap.main([part for part in inspected["launch_argv"][4:] if part != "--summary"]) == 0
    result = json.loads(capsys.readouterr().out)
    report = bootstrap.admit_profile(repo / "profiles/local/topic", run_id=args.run_id, allow_gpu=False,
                                     delay_seconds=args.process_delay)
    monkeypatch.setattr(institutional_handoff, "RELEASE_SECONDS", .02)
    monkeypatch.setattr(RunBrowser, "check_connection", lambda *a, **kw: {"state": "ready"})
    monkeypatch.setattr(RunBrowser, "prepare", lambda *a, **kw: {"state": "opened"})
    return repo, args, report, result


def start(prepared):
    repo, args, report, result = prepared
    output, codes, errors = [], [], []
    def worker():
        try:
            codes.append(bootstrap_session.run(args, repo, report, result, output=output.append))
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=worker)
    thread.start()
    def root_or_error():
        assert not errors, errors
        return next((Path(json.loads(line)["state_root"]) for line in output
            if line.startswith("{") and "state_root" in json.loads(line)), None)
    root = eventually(root_or_error)
    assert json.loads(output[0])["state_root"] == str(root)
    return root, thread, output, codes, errors


def finish_platforms(root):
    wait(root, "waiting_for_user", "IEEE")
    answer(root, "public_only", "IEEE")
    wait(root, "waiting_for_user", "ACM")
    answer(root, "skip", "ACM")
    return wait(root, "awaiting_launch")


def reply(root, text):
    return request(root, "PREPARATION_ANSWER", {"reply": text})


@pytest.mark.parametrize("choice", ["Start now", "Prepare only", "expired"])
def test_program_finishes_review_and_accepts_only_live_explicit_launch(prepared, monkeypatch, choice):
    from hypoloom import institutional_dialogue
    from hypoloom.institutional_instructions import render
    skew = [0]
    original_clock = institutional_dialogue.monotonic
    monkeypatch.setattr(institutional_dialogue, "monotonic", lambda: original_clock() + skew[0])
    starts = []
    def launch(commands, payload):
        starts.append((commands, payload))
        return {"status": "started"}
    monkeypatch.setattr(bootstrap, "start_fresh", launch)
    root, thread, output, codes, errors = start(prepared)
    try:
        # A stale/early prompt cannot authorize launch while a sample is pending.
        wait(root, "waiting_for_user", "IEEE")
        assert reply(root, "Start now").status == "rejected"
        view = finish_platforms(root)
        text = render(view)
        assert "Launch review" in text and "无需合并策略" in text
        assert view["launch_review"]["status"] == "ready_for_review"
        assert "public_only" in text and "ACM" in text
        assert "Delivery" not in text or "unverified" in text
        assert not starts and not Path(prepared[1].state_root).exists()
        if choice == "expired":
            skew[0] = 658  # the observed model delay, without actually waiting eleven minutes
            assert reply(root, "Start now").status == "rejected"
        else:
            assert reply(root, choice).status == "accepted"
        thread.join(5)
        assert not thread.is_alive() and not errors and codes == [0]
        assert len(starts) == (choice == "Start now")
        if starts:
            host = starts[0][0]["host_start"]
            assert "IEEE=public_only" in host and "ACM=auto" in host
            assert starts[0][1]["run_id"] == prepared[1].run_id
            documents = [json.loads(line) for line in output if line.startswith("{")]
            result = next(d["bootstrap_result"] for d in documents if "bootstrap_result" in d)
            assert result["launch"]["status"] == "started" and result["monitor"]["cc_command"]
        reason = {"Start now": "run_handoff", "Prepare only": "operator_finished", "expired": "launch_review_expired"}[choice]
        assert json.loads(output[-1])["preparation_lifecycle"]["reason"] == reason
        assert not root.exists()
    finally:
        cleanup(root, thread)


@pytest.mark.parametrize("status,ending", [("profile_changed_before_host", "launch_failed"),
                                           ("creation_uncertain", "launch_uncertain")])
def test_review_processing_precedes_clock_and_no_second_launch_during_start(prepared, monkeypatch, status, ending):
    from hypoloom import institutional_dialogue, bootstrap_review
    clock = institutional_dialogue.monotonic
    skew = [0]
    monkeypatch.setattr(institutional_dialogue, "monotonic", lambda: clock() + skew[0])
    original_render = bootstrap_review.render
    def render(*args):
        skew[0] += 658
        return original_render(*args)
    monkeypatch.setattr(bootstrap_review, "render", render)
    entered, release = threading.Event(), threading.Event()
    starts = []
    def launch(*args):
        starts.append(args)
        entered.set()
        assert release.wait(5)
        return {"status": status}
    monkeypatch.setattr(bootstrap, "start_fresh", launch)
    root, thread, output, codes, errors = start(prepared)
    try:
        view = finish_platforms(root)
        assert 590 <= view["remaining_seconds"] <= 600
        assert reply(root, "Start now").status == "accepted"
        assert entered.wait(3)
        assert request(root).result["state"] == "launching"
        assert reply(root, "Start now").status == "rejected"
        assert reply(root, "Prepare only").status == "rejected"
        release.set()
        thread.join(5)
        assert not errors and codes == [2] and len(starts) == 1
        assert not root.exists() and not Path(prepared[1].state_root).exists()
        assert json.loads(output[-1])["preparation_lifecycle"]["reason"] == ending
    finally:
        release.set()
        cleanup(root, thread)


def test_profile_change_before_final_review_disables_launch(prepared, monkeypatch):
    monkeypatch.setattr(bootstrap, "start_fresh", lambda *a: pytest.fail("unreviewed launch"))
    root, thread, _, codes, errors = start(prepared)
    try:
        wait(root, "waiting_for_user", "IEEE")
        profile = prepared[0] / "profiles/local/topic/experts.yaml"
        profile.write_text(profile.read_text().replace("exact-fixture-model", "changed-model"))
        view = finish_platforms(root)
        assert view["launch_review"]["status"] == "blocked"
        assert "Profile changed" in view["launch_review"]["text"]
        assert reply(root, "Start now").status == "rejected"
        assert reply(root, "Prepare only").status == "accepted"
        thread.join(5)
        assert not errors and codes == [0]
    finally:
        cleanup(root, thread)


def test_retry_keeps_completed_publisher_and_updates_fixed_review(prepared, monkeypatch):
    calls = []
    def prepare(self, provider, **kw):
        calls.append(provider)
        return {"state": "failed" if provider == "ACM" and calls.count("ACM") == 1 else "opened"}
    monkeypatch.setattr(RunBrowser, "prepare", prepare)
    monkeypatch.setattr(bootstrap, "start_fresh", lambda *a: pytest.fail("retry launched research"))
    root, thread, _, codes, errors = start(prepared)
    try:
        wait(root, "waiting_for_user", "IEEE")
        answer(root, "public_only", "IEEE")
        first = wait(root, "awaiting_launch")
        assert first["summary"]["remaining_providers"] == ["ACM"]
        assert reply(root, "重新准备").status == "accepted"
        wait(root, "waiting_for_user", "ACM")
        answer(root, "skip", "ACM")
        second = wait(root, "awaiting_launch")
        assert calls == ["IEEE", "ACM", "ACM"]
        assert second["summary"]["scope"] == first["summary"]["scope"]
        assert second["summary"]["remaining_providers"] == []
        assert [r["state"] for r in second["summary"]["preparation_summary"]] == ["public_only", "skipped"]
        assert len(list((prepared[0] / "state").iterdir())) == 1
        assert reply(root, "Prepare only").status == "accepted"
        thread.join(5)
        assert not errors and codes == [0] and not root.exists()
    finally:
        cleanup(root, thread)


def test_notification_review_is_complete_without_private_values(tmp_path):
    from hypoloom.notification_tailer import notification_preflight
    secrets = tmp_path / "notify.env"
    secrets.write_text("TELEGRAM_BOT_TOKEN=123:private-token\nTELEGRAM_CHAT_ID=private-recipient\n")
    secrets.chmod(0o600)
    result = notification_preflight({"sinks": {"log": {}, "telegram": {"enabled": True,
        "credentials_file": str(secrets)}, "dashboard": {"enabled": True, "host": "127.0.0.1", "port": 8765}},
        "events": {"telegram": ["grounding_question", "run_finished"]}}, state_root=tmp_path / "run", environ={})
    assert result["sinks"]["telegram"]["events"] == ["grounding_question", "run_finished"]
    assert result["sinks"]["log"]["events"]
    assert result["proxy"] == "direct" and result["delivery_status"] == "unverified"
    assert "private-token" not in json.dumps(result) and "private-recipient" not in json.dumps(result)


def test_session_cli_rejects_start_and_template_before_effects(prepared, capsys):
    repo, args, _, _ = prepared
    argv = bootstrap_inspect._launch_argv(args, repo, action="session")[4:]
    for extra in (["--start"], ["--template", "multi-expert-zh"], ["--set", "experts.defaults.model=other"]):
        assert bootstrap.main([*argv, *extra]) == 2
        assert "Start now is a separate live reply" in capsys.readouterr().err
    assert not Path(args.state_root).exists()


def test_session_explicit_reply_reaches_real_host_cpu_and_clean_terminal(checkout, tmp_path, monkeypatch, capsys, record_property):
    """Real sockets/CLI/CREATE/Host/Process/Oracle; fake research adapter, no paid calls.

    Only systemd transport and publisher browser operations are replaced.
    """
    from hypoloom.oracle import Oracle
    from hypoloom.runner import process_group_exists
    from hypoloom.institutional_client import connections
    repo, _ = checkout
    shutil.copytree(ROOT / "tests/fixtures/profiles/component-demo", repo / "profiles/local/topic")
    monkeypatch.setattr(bootstrap_inspect, "supervisor_check", lambda: {"status": "ready", "linger": "yes"})
    monkeypatch.setattr(RunBrowser, "check_connection", lambda *a, **kw: {"state": "ready"})
    monkeypatch.setattr(RunBrowser, "prepare", lambda *a, **kw: {"state": "opened"})
    monkeypatch.setattr(institutional_handoff, "RELEASE_SECONDS", .02)
    argv = [*arguments(repo, [], "launch", template=None), "--institutional-browser-mode", "client",
            "--institutional-prepare-provider", "IEEE", "--institutional-prepare-provider", "ACM"]
    assert bootstrap.main(argv) == 0
    capsys.readouterr()
    args = bootstrap.build_parser().parse_args(["session", *argv[1:]])
    state = Path(args.state_root)
    host = None
    original_invoke = bootstrap._invoke
    log = (tmp_path / "host.log").open("w+")
    calls = []
    def invoke(argv, environment, *, cwd):
        nonlocal host
        calls.append(argv)
        if "manage_host.py" not in " ".join(argv):
            return original_invoke(argv, environment, cwd=cwd)
        assert argv[3] == "start"
        host = subprocess.Popen(argv[argv.index("--") + 1:], env={**os.environ, **environment}, cwd=ROOT,
                                stdout=log, stderr=log, start_new_session=True)
        eventually(lambda: (state / "control.sock").is_socket())
        return 0, {"status": "ready", "state_root": str(state)}
    monkeypatch.setattr(bootstrap, "_invoke", invoke)
    codes, errors = [], []
    def worker():
        try:
            codes.append(bootstrap.main(bootstrap_inspect._launch_argv(args, repo, action="session")[4:]))
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=worker)
    thread.start()
    root = eventually(lambda: next((path for path in (repo / "state").glob("institutional-prepare-*")
                                    if (path / "control.sock").exists()), None))
    try:
        finish_platforms(root)
        assert calls == [] and not state.exists()
        assert reply(root, "Start now").status == "accepted"
        thread.join(20)
        assert not thread.is_alive() and not errors and codes == [0]
        output = capsys.readouterr().out.splitlines()
        assert json.loads(output[0])["state_root"] == str(root)
        assert sum("manage_host.py" in " ".join(cmd) for cmd in calls) == 1
        assert sum("start" in cmd and "--request-id" in cmd for cmd in calls) == 1
        assert json.loads(output[-1])["preparation_lifecycle"]["reason"] == "run_handoff"
        assert host.wait(timeout=30) == 0 and not process_group_exists(host.pid)
        assert not root.exists() and not (state / "control.sock").exists()
        observed = connections(state, args.run_id, args.run_id + "-create-1")
        assert observed["lifecycle"] == "terminal" and observed["connections"] == []
        audit = Oracle(state / "state.sqlite", state / "artifacts").audit(args.run_id)
        assert audit.ok, audit.to_dict()
        assert audit.open_attempt_count == audit.pending_effect_count == 0
        import sqlite3
        with sqlite3.connect(f"file:{state / 'state.sqlite'}?mode=ro", uri=True) as db:
            processes = db.execute("SELECT terminal_reason,process_group_id FROM execution_attempts WHERE kind='PROCESS'").fetchall()
            creates = db.execute("SELECT COUNT(*) FROM workflow_commands WHERE command_type='CREATE_RUN'").fetchone()[0]
            attempts = db.execute("SELECT kind,terminal_reason,COUNT(*) FROM execution_attempts GROUP BY kind,terminal_reason").fetchall()
        assert creates == 1 and processes
        assert all(reason == "SUCCEEDED" and not process_group_exists(pgid) for reason, pgid in processes)
        record_property("native_census", json.dumps({"creates": creates, "attempts": attempts,
            "open_attempts": audit.open_attempt_count, "pending_effects": audit.pending_effect_count,
            "preparation_removed": not root.exists(), "host_socket_removed": not (state / "control.sock").exists()}))
    finally:
        cleanup(root, thread)
        if host is not None and host.poll() is None:
            os.killpg(host.pid, signal.SIGTERM)
            host.wait(timeout=10)
        log.close()
