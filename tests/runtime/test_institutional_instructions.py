"""Executable instructions and exact replies for an existing init conversation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading

import pytest

from hypoloom.cli import build_parser
from hypoloom.institutional_dialogue import PreparationDialogue
from hypoloom.institutional_instructions import client_entry, render
from hypoloom.institutional_prepare import run
from tests.runtime.test_institutional_init_dialogue import arguments, eventually, ready_access, request


def test_live_connection_instructions_and_exact_reply_share_the_real_deadline(tmp_path, monkeypatch):
    from hypoloom import institutional_dialogue
    now = [100.]
    monkeypatch.setattr(institutional_dialogue, "monotonic", lambda: now[0])
    dialogue = PreparationDialogue(600)
    replies = []
    thread = threading.Thread(target=lambda: replies.append(dialogue.wait(connection={"state": "waiting_for_client"})))
    thread.start()
    from hypoloom.control_protocol import ControlRequest
    def status(): return dialogue.handle(ControlRequest(1, "read", None, "PREPARATION_STATUS", {})).result
    def answer(text): return dialogue.handle(ControlRequest(1, "answer", None, "PREPARATION_ANSWER", {"reply": text}))
    try:
        eventually(lambda: status()["state"] == "waiting_for_connection")
        assert status()["remaining_seconds"] == 600
        now[0] += 37
        assert status()["remaining_seconds"] == 563
        assert "剩余 563 秒" in render(status()) and "继续连接 / 跳过准备" in render(status())
        with pytest.raises(ValueError, match="exact reply"):
            answer("Background command completed")
        with pytest.raises(ValueError, match="exact reply"):
            answer("完成")
        assert answer("继续连接").status == "accepted"
        thread.join(2)
        assert replies == ["retry"] and not thread.is_alive()
    finally:
        if thread.is_alive():
            dialogue.handle(ControlRequest(1, "stop", None, "PREPARATION_ANSWER", {"action": "finish"}))
            thread.join(2)


def test_cli_instructions_replies_and_expired_batch_do_not_create_research(tmp_path, monkeypatch):
    _, backend, _ = ready_access(monkeypatch)
    output, codes, errors = [], [], []
    def worker():
        try: codes.append(run(arguments("--agent-session", "--repository", str(tmp_path), "--provider", "IEEE"), output=output.append))
        except BaseException as exc: errors.append(exc)
    thread = threading.Thread(target=worker); thread.start()
    root = eventually(lambda: next((Path(json.loads(v)["state_root"]) for v in output
        if v.startswith('{') and "state_root" in json.loads(v)), None))
    emitted = []
    def cli(action, *extra):
        return run(build_parser().parse_args(["institutional", "preparation", action,
            "--state-root", str(root), *extra]), output=emitted.append)
    try:
        eventually(lambda: request(root).result.get("state") == "waiting_for_user")
        assert cli("instructions") == 0
        assert "IEEE 页面已打开" in emitted[-1] and "仅公开" in emitted[-1]
        view = request(root).result
        assert view["reply_argv"][-2:] == ["--provider", "IEEE"]
        assert cli("reply", "--text", "完成", "--provider", "Springer Nature") == 1
        assert cli("reply", "--text", "继续连接", "--provider", "IEEE") == 1
        assert cli("reply", "--text", "仅公开", "--provider", "IEEE") == 0
        thread.join(5)
        assert not thread.is_alive() and not errors and codes == [0]
        assert json.loads(output[-1])["provider_modes"] == {"IEEE": "public_only"}
        assert backend.closed and not root.exists()
        assert cli("instructions") == 1
        assert "重新准备" in emitted[-1] and "本次等待剩余" not in emitted[-1]
        assert not root.exists() and not list(tmp_path.rglob("state.sqlite"))
    finally:
        if thread.is_alive():
            request(root, "PREPARATION_ANSWER", {"action": "finish"}); thread.join(5)


@pytest.mark.parametrize("bad", ["/tmp/line\nbreak", "/tmp/carriage\rreturn", "/tmp/null\x00byte"])
def test_generated_setup_rejects_unrepresentable_paths(bad):
    with pytest.raises(ValueError): client_entry(bad, "/python")


@pytest.mark.parametrize('reply,expected', [('跳过此平台', 'skipped'), ('完成', 'sample_verified')])
def test_challenge_reason_reaches_fixed_instructions_and_stays_with_its_platform(tmp_path, monkeypatch, reply, expected):
    _, backend, _ = ready_access(monkeypatch)
    def prepare(provider, **kwargs):
        backend.calls.append((provider, kwargs))
        return {'state': 'opened', **({'reason': 'verification_required'} if provider == 'ScienceDirect' else {})}
    backend.prepare = prepare
    output, codes, errors = [], [], []
    def worker():
        try:
            codes.append(run(arguments('--agent-session', '--repository', str(tmp_path),
                '--provider', 'ScienceDirect', '--provider', 'Springer Nature'), output=output.append))
        except BaseException as exc: errors.append(exc)
    thread = threading.Thread(target=worker); thread.start()
    root = eventually(lambda: next((Path(json.loads(v)['state_root']) for v in output
        if v.startswith('{') and 'state_root' in json.loads(v)), None))
    try:
        eventually(lambda: request(root).result.get('state') == 'waiting_for_user')
        view = request(root).result
        assert view['reason_code'] == 'verification_required'
        assert '打开时检测到人机验证' in render(view)
        assert '文章页和 PDF 下载页可能分别出现人机验证' in render(view)
        assert '跳过此平台' in render(view) and '不代表登录或订阅已验证' in render(view)
        assert [call[0] for call in backend.calls] == ['ScienceDirect']
        assert request(root, 'PREPARATION_ANSWER', {'provider': 'ScienceDirect', 'reply': reply}).status == 'accepted'
        eventually(lambda: request(root).result.get('state') == 'waiting_for_user'
            and request(root).result.get('provider') == 'Springer Nature')
        assert 'verification_required' != request(root).result.get('reason_code')
        assert '打开时检测到人机验证' not in render(request(root).result)
        from hypoloom.institutional_access import provider_entry_url
        assert provider_entry_url('Springer Nature') in render(request(root).result)
        assert 'Find your institution' in render(request(root).result)
        assert request(root, 'PREPARATION_ANSWER', {'provider': 'Springer Nature', 'reply': '完成'}).status == 'accepted'
        thread.join(5)
        assert not thread.is_alive() and not errors and codes == [0]
        assert [r['state'] for r in json.loads(output[-1])['preparation_summary']] == [expected, 'sample_verified']
        assert backend.closed and not root.exists() and not list(tmp_path.rglob('state.sqlite'))
    finally:
        if thread.is_alive():
            request(root, 'PREPARATION_ANSWER', {'action': 'finish'}); thread.join(5)


def test_timeout_generates_exact_remaining_command_with_updated_modes(tmp_path, monkeypatch):
    ready_access(monkeypatch)
    monkeypatch.setattr("hypoloom.institutional_prepare.PreparationDialogue", lambda _: PreparationDialogue(.3))
    args = arguments("--agent-session", "--repository", str(tmp_path), "--provider", "IEEE",
        "--provider", "Springer Nature", "--institutional-manual-seconds", "70",
        "--institutional-connect-timeout", "7", "--institutional-provider-mode", "ACM=public_only")
    output, codes = [], []
    thread = threading.Thread(target=lambda: codes.append(run(args, output=output.append)))
    thread.start()
    root = eventually(lambda: next((Path(json.loads(v)["state_root"]) for v in output
        if v.startswith('{') and "state_root" in json.loads(v)), None))
    try:
        eventually(lambda: request(root).result.get("state") == "waiting_for_user")
        assert request(root, "PREPARATION_ANSWER", {"reply": "仅公开", "provider": "IEEE"}).status == "accepted"
        thread.join(5)
        assert not thread.is_alive() and codes == [1]
        summary = json.loads(output[-1])
        assert summary["preparation_state"] == "input_timeout"
        remaining = build_parser().parse_args(summary["remaining_preparation_argv"][4:])
        assert remaining.provider == ["Springer Nature"] and remaining.agent_session
        assert remaining.institutional_provider_mode == ["ACM=public_only", "IEEE=public_only", "Springer Nature=auto"]
        assert remaining.institutional_manual_seconds == 70 and remaining.institutional_connect_timeout == 7
        assert remaining.repository == str(tmp_path) and remaining.institutional_browser_url == args.institutional_browser_url
        assert not root.exists() and not list(tmp_path.rglob("state.sqlite"))
    finally:
        if thread.is_alive():
            request(root, "PREPARATION_ANSWER", {"action": "finish"}); thread.join(5)


def test_watch_waits_for_program_progress_and_never_submits_a_reply(tmp_path, monkeypatch):
    from hypoloom import institutional_dialogue, institutional_prepare
    from concurrent.futures import Future
    monkeypatch.setattr(institutional_dialogue, "CONNECTION_RETRY_SECONDS", .05)
    # A client can attach after the short foreground observation has elapsed.
    # The generated background watch must still deliver the platform prompt.
    monkeypatch.setattr(institutional_prepare, "_WATCH_SECONDS", .05)
    access, backend, _ = ready_access(monkeypatch)
    connected = threading.Event()
    def check(*_, **kwargs):
        result = Future(); result.set_result({"state": "ready" if connected.is_set() else "waiting_for_client"})
        return result
    access.check_connection = check
    output, codes = [], []
    worker = threading.Thread(target=lambda: codes.append(run(
        arguments("--agent-session", "--repository", str(tmp_path), "--provider", "IEEE"), output=output.append)))
    worker.start()
    first = eventually(lambda: next((json.loads(v) for v in output if v.startswith('{') and "state_root" in json.loads(v)), None))
    root = Path(first["state_root"])
    watched, watch_codes = [], []
    watcher = threading.Thread(target=lambda: watch_codes.append(run(
        build_parser().parse_args(first["watch_argv"][4:]), output=watched.append)))
    try:
        eventually(lambda: request(root).result.get("state") == "waiting_for_connection")
        watcher.start()
        watcher.join(.2)
        assert watcher.is_alive(), "generated watch returned before the delayed client attached"
        connected.set()
        watcher.join(3)
        assert not watcher.is_alive() and watch_codes == [0]
        assert "IEEE 页面已打开" in watched[-1]
        assert '无需重新登录，直接回复：完成' in watched[-1]
        assert worker.is_alive() and codes == [], "watch must never complete manual preparation"
        assert request(root, "PREPARATION_ANSWER", {"provider": "IEEE", "reply": "完成"}).status == "accepted"
        worker.join(3)
        assert codes == [0] and backend.closed and not root.exists()
        assert run(build_parser().parse_args(first["watch_argv"][4:]), output=watched.append) == 1
        assert "准备进程已结束" in watched[-1]
    finally:
        if worker.is_alive():
            request(root, "PREPARATION_ANSWER", {"action": "finish"}); worker.join(3)
        if watcher.is_alive(): watcher.join(3)


@pytest.mark.parametrize('explicit', [False, True])
def test_watch_returns_latest_instructions_at_its_wallclock_limit(tmp_path, monkeypatch, explicit):
    from hypoloom import institutional_prepare
    from hypoloom.institutional_client import PreparationControl
    from time import monotonic
    access, _, _ = ready_access(monkeypatch)
    root = tmp_path / "preparation"; root.mkdir(mode=0o700)
    dialogue = PreparationDialogue(600)
    dialogue.update(state="waiting_for_connection", automatic_connection_checks=True,
        connection={"state": "browser_unavailable", "reason_code": "cdp_timeout"}, actions=["retry_connection", "finish"])
    monkeypatch.setattr(institutional_prepare, "_WATCH_SECONDS", .15)
    control = PreparationControl(root, access, dialogue.handle)
    output = []
    start = monotonic()
    try:
        assert run(build_parser().parse_args(["institutional", "preparation", "watch", "--state-root", str(root),
            *(['--wait-seconds', '.15'] if explicit else [])]), output=output.append) == 0
        assert .1 <= monotonic() - start < 1
        assert "自动重检连接" in output[-1] and "cdp_timeout" in output[-1]
        assert dialogue.answer is None and not dialogue.finished
    finally:
        control.close(); access.close()


@pytest.mark.parametrize('action,seconds', [('watch', 'nan'), ('watch', 'inf'), ('watch', '0'),
    ('watch', '-1'), ('watch', '3601'), ('status', '60'), ('finish', '60')])
def test_invalid_watch_budget_is_rejected_before_control_access(tmp_path, monkeypatch, action, seconds):
    monkeypatch.setattr('hypoloom.control_protocol.request_over_socket',
                        lambda *a, **k: pytest.fail('invalid observation accessed control socket'))
    args = build_parser().parse_args(['institutional', 'preparation', action,
        '--state-root', str(tmp_path / 'absent'), '--wait-seconds', seconds])
    with pytest.raises(ValueError, match='wait-seconds'):
        run(args)


def test_background_watch_exits_on_explicit_finish_without_answering(tmp_path, monkeypatch):
    from concurrent.futures import Future
    access, _, _ = ready_access(monkeypatch)
    def check(*_, **kwargs):
        future = Future(); future.set_result({'state': 'waiting_for_client'}); return future
    access.check_connection = check
    output, codes, watched, watch_codes = [], [], [], []
    worker = threading.Thread(target=lambda: codes.append(run(
        arguments('--agent-session', '--repository', str(tmp_path), '--provider', 'IEEE'), output=output.append)))
    worker.start()
    first = eventually(lambda: next((json.loads(v) for v in output if v.startswith('{') and 'state_root' in json.loads(v)), None))
    root = Path(first['state_root'])
    watcher = threading.Thread(target=lambda: watch_codes.append(run(
        build_parser().parse_args(first['watch_argv'][4:]), output=watched.append)))
    try:
        eventually(lambda: request(root).result.get('state') == 'waiting_for_connection')
        watcher.start(); watcher.join(.2)
        assert watcher.is_alive()
        view = request(root).result
        assert '终止本批次' in render(view) and '不会自动重新准备' in render(view)
        assert request(root, 'PREPARATION_ANSWER', {'action': 'finish'}).status == 'accepted'
        worker.join(3); watcher.join(3)
        assert not worker.is_alive() and not watcher.is_alive()
        assert codes == [0] and watch_codes[0] in (0, 1)
        assert '结束' in watched[-1]
        assert json.loads(output[-1])['preparation_state'] == 'finished'
        assert not root.exists()
    finally:
        if worker.is_alive(): request(root, 'PREPARATION_ANSWER', {'action': 'finish'}); worker.join(3)
        if watcher.is_alive(): watcher.join(3)


def test_client_entry_delivers_a_file_launch_and_values_without_installation_source(tmp_path):
    entry = client_entry(tmp_path, "/python with spaces")
    assert not list(tmp_path.iterdir()), "inspection must not write an installer or state"
    assert Path(entry["script_source"]).is_file()
    assert entry["setup_inputs"] == {"repository": str(tmp_path),
        "python_executable": "/python with spaces", "runs_root": str(tmp_path / "state")}
    view = render({"state": "waiting_for_connection", "client_entry": entry,
        "automatic_connection_checks": True, "actions": ["retry_connection", "finish"]})
    command = view.split("```powershell\n", 1)[1].split("```", 1)[0].strip()
    assert command == r"pwsh -NoProfile -File .\institutional-connect.ps1 -Action Setup"
    assert "Get-Command" not in view and "WriteAllBytes" not in view and "FromBase64String" not in view
    assert "SFTP/SCP" in view and "/python with spaces" in view


def test_old_live_preparation_does_not_reintroduce_inline_installation_code():
    view = render({"state": "waiting_for_connection", "client_entry": {
        "script_source": "/installed/institutional-connect.ps1",
        "powershell": "Get-Command pwsh -ErrorAction -SilentlyContinue"}})
    assert "-Action Setup" in view and "此旧批次未单独提供路径值" in view
    assert "Get-Command" not in view and "SilentlyContinue" not in view


@pytest.mark.parametrize("scenario", ["defaults", "custom", "ssh_failure", "invalid_target",
    "invalid_repository", "write_failure", "connection_failure", "installed", "supplied"])
def test_file_setup_installs_original_bytes_and_reuses_connect(tmp_path, scenario):
    executable = os.environ.get("HYPOLOOM_TEST_PWSH") or shutil.which("pwsh")
    if not executable: pytest.skip("PowerShell runtime not installed")
    source = Path(__file__).resolve().parents[2] / "hypoloom/connection_assets/institutional-connect.ps1"
    desktop = tmp_path / "desktop"
    installed = desktop / "Hypoloom/institutional-connect.ps1"
    installed.parent.mkdir(parents=True)
    original = source.read_bytes() if scenario == "installed" else b"# existing entry\n"
    installed.write_bytes(original)
    downloaded = tmp_path / "download ' $literal ` file.ps1"
    downloaded.write_bytes(source.read_bytes())
    fixture = Path(__file__).with_name("fixtures") / "institutional_setup_checks.ps1"
    result = subprocess.run([executable, "-NoProfile", "-File", str(fixture),
        "-Connector", str(installed if scenario == "installed" else downloaded),
        "-Desktop", str(desktop), "-Scenario", scenario], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads((desktop / "observed.json").read_text())
    errors = {"ssh_failure": "init_ssh_connection_unavailable_check_key_and_known_host",
        "invalid_target": "invalid_ssh_target", "invalid_repository": "absolute_repository_path_required",
        "write_failure": "fixture_write_failure", "connection_failure": "client_setup_connection_failed"}
    assert observed["error"] == errors.get(scenario, "")
    executes = scenario not in {"ssh_failure", "invalid_target", "invalid_repository", "write_failure"}
    if executes:
        assert installed.read_bytes() == source.read_bytes()
        args = observed["args"]
        assert args[args.index("-File") + 1] == str(installed)
        assert args[args.index("-Action") + 1] == "Connect", "setup must not open another CC init"
        assert args[args.index("-Server") + 1] == "research@fixture"
        repository = "/server/repo ' $literal ` $(throw INJECTED)"
        assert args[args.index("-Repository") + 1] == repository
        expected_python = "/custom/python space" if scenario in {"custom", "supplied"} else repository + "/.conda/envs/hypoloom/bin/python"
        assert args[args.index("-PythonExecutable") + 1] == expected_python
        prefix = "/custom" if scenario in {"custom", "supplied"} else "/state"
        assert args[args.index("-StateRoot") + 1] == prefix + "/final"
        assert args[args.index("-RunId") + 1] == "final"
        assert args[args.index("-RequestId") + 1] == "create-final"
        assert args[args.index("-PreparationRoot") + 1] == prefix + "/preparation"
        assert "-RunsRoot" not in args
        assert observed["checks"] == 1
    else:
        assert observed["args"] == []
        assert installed.read_bytes() == original
    assert list(installed.parent.iterdir()) == [installed], "setup left a partial installation"
    if scenario == "supplied": assert observed["prompts"] == 0
