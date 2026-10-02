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
        assert cli("reply", "--text", "完成", "--provider", "Springer") == 1
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


def test_timeout_generates_exact_remaining_command_with_updated_modes(tmp_path, monkeypatch):
    ready_access(monkeypatch)
    monkeypatch.setattr("hypoloom.institutional_prepare.PreparationDialogue", lambda _: PreparationDialogue(.3))
    args = arguments("--agent-session", "--repository", str(tmp_path), "--provider", "IEEE",
        "--provider", "Nature", "--institutional-manual-seconds", "70",
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
        assert remaining.provider == ["Nature"] and remaining.agent_session
        assert remaining.institutional_provider_mode == ["ACM=public_only", "IEEE=public_only"]
        assert remaining.institutional_manual_seconds == 70 and remaining.institutional_connect_timeout == 7
        assert remaining.repository == str(tmp_path) and remaining.institutional_browser_url == args.institutional_browser_url
        assert not root.exists() and not list(tmp_path.rglob("state.sqlite"))
    finally:
        if thread.is_alive():
            request(root, "PREPARATION_ANSWER", {"action": "finish"}); thread.join(5)


@pytest.mark.parametrize("download_ok", [True, False])
def test_generated_powershell_downloads_then_connects_without_reentering_init(tmp_path, monkeypatch, download_ok):
    executable = os.environ.get("HYPOLOOM_TEST_PWSH") or shutil.which("pwsh")
    if not executable: pytest.skip("PowerShell parser/runtime not installed")
    from hypoloom import institutional_instructions
    package = tmp_path / "package $literal ' quote ` $(throw INJECTED)"
    source = package / "connection_assets/institutional-connect.ps1"
    source.parent.mkdir(parents=True)
    source.write_text("# synthetic downloaded connector\n")
    monkeypatch.setattr(institutional_instructions, "__file__", str(package / "institutional_instructions.py"))
    repository = "/repo $literal ' apostrophe ` $(throw INJECTED)"
    entry = client_entry(repository, "/custom/python with spaces")
    expected = tmp_path / "expected.json"
    expected.write_text(json.dumps({"repository": repository, "python": "/custom/python with spaces"}))
    fixture = tmp_path / "fixture.ps1"
    # Execute the actual generated program. Substitute only external Windows/SSH
    # operations; quoting, download writes, failure handling and argv stay real.
    fixture.write_text("""param($Root, $Expected)
$env:LOCALAPPDATA = $Root
$script:Called = $false
function Read-Host { 'research@fixture' }
function ssh {
    if ($args[-2] -ne 'research@fixture' -or -not $args[-1].StartsWith('cat -- ')) { throw 'bad download argv' }
    if (DOWNLOAD_EXIT -ne 0) { $global:LASTEXITCODE = DOWNLOAD_EXIT; return }
    & /bin/bash -c $args[-1]
}
function pwsh {
    $script:Called = $true
    $values = Get-Content -Raw -LiteralPath $Expected | ConvertFrom-Json
    if ($args[$args.IndexOf('-Action') + 1] -ne 'Connect') { throw 'reentered init' }
    if ($args[$args.IndexOf('-Repository') + 1] -cne $values.repository) { throw 'repository altered' }
    if ($args[$args.IndexOf('-PythonExecutable') + 1] -cne $values.python) { throw 'python altered' }
    if ((Get-Content -Raw -LiteralPath $args[$args.IndexOf('-File') + 1]).Trim() -ne '# synthetic downloaded connector') { throw 'download incomplete' }
    $global:LASTEXITCODE = 0
}
try {
GENERATED
} catch {
    if (EXPECTED_SUCCESS) { throw }
    if ($script:Called) { throw 'executed failed download' }
    if (Test-Path -LiteralPath (Join-Path $Root 'Hypoloom/institutional-connect.ps1')) { throw 'published failed download' }
    Write-Output 'download_failed_without_execution'
    exit 0
}
if (-not EXPECTED_SUCCESS -or -not $script:Called) { throw 'expected path was not executed' }
Write-Output 'setup_connected_same_session'
""".replace("DOWNLOAD_EXIT", "0" if download_ok else "1")
        .replace("EXPECTED_SUCCESS", "$true" if download_ok else "$false")
        .replace("GENERATED", entry["powershell"]))
    result = subprocess.run([executable, "-NoProfile", "-File", str(fixture),
        str(tmp_path / "desktop"), str(expected)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert ("setup_connected_same_session" if download_ok else "download_failed_without_execution") in result.stdout
