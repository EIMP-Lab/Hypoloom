"""Fresh scoped queries over one owned SSH stdio session, without a status cache."""
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hypoloom import institutional_client as client
from hypoloom.cli import build_parser


def arguments():
    return build_parser().parse_args([
        "institutional", "connections", "--stdio", "--state-root", str(Path('/state/final').resolve()),
        "--run-id", "final", "--request-id", "create-final", "--preparation-root", "/state/preparation",
    ])


def test_stdio_queries_are_fresh_and_keep_scope_and_credential_projection(monkeypatch):
    calls, responses = [], []
    def current(root, run_id, request_id, *, preparation_root):
        calls.append((root, run_id, request_id, preparation_root))
        return {"lifecycle": "active" if len(calls) == 1 else "terminal", "connections": [
            {"browser_ui_password": "must-not-leave", "mode": "client"}] if len(calls) == 1 else []}
    monkeypatch.setattr(client, "connections", current)
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n\r\n"))
    assert client.run(arguments(), output=responses.append) == 0
    assert calls == [(str(Path('/state/final').resolve()), "final", "create-final", "/state/preparation")] * 2
    assert [json.loads(row)["lifecycle"] for row in responses] == ["active", "terminal"]
    assert "must-not-leave" not in str(responses)


@pytest.mark.parametrize("input_text", ["", "change-scope\n", " ", "\ninvalid\n", "\r", "\rX"])
def test_stdio_eof_and_invalid_input_never_issue_extra_queries(monkeypatch, input_text):
    calls = []
    def current(*a, **k):
        calls.append(True)
        return {"lifecycle": "terminal", "connections": []}
    monkeypatch.setattr(client, "connections", current)
    monkeypatch.setattr(sys, "stdin", io.StringIO(input_text))
    if input_text:
        with pytest.raises(ValueError, match="connection_query_empty_line_required"):
            client.run(arguments(), output=lambda _: None)
    else:
        assert client.run(arguments(), output=lambda _: None) == 0
    assert len(calls) == int(input_text.startswith("\n"))


def test_stdio_does_not_repeat_cached_result_after_query_failure(monkeypatch):
    calls, responses = [], []
    def current(*a, **k):
        calls.append(True)
        if len(calls) == 2: raise ConnectionError("connection_target_unavailable")
        return {"lifecycle": "preparing", "connections": []}
    monkeypatch.setattr(client, "connections", current)
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n\n\n"))
    with pytest.raises(ConnectionError, match="connection_target_unavailable"):
        client.run(arguments(), output=responses.append)
    assert len(calls) == 2 and len(responses) == 1


@pytest.mark.parametrize("scenario", ["reuse", "timeout", "scope", "ssh_exit", "stdin_eof", "watch_cleanup", "after_attach"])
@pytest.mark.skipif(sys.platform == "win32", reason="Linux remote CLI fixture; physical Windows SSH acceptance is separate")
def test_shipped_powershell_query_channel_with_real_process_pipes(tmp_path, scenario):
    executable = os.environ.get("HYPOLOOM_TEST_PWSH") or shutil.which("pwsh")
    if not executable: pytest.skip("PowerShell runtime not installed")
    root = Path(__file__).resolve().parents[2]
    # Replace only the SSH/remote-control boundary. The shipped Python CLI,
    # stdio loop, PowerShell process ownership, query and validators are real.
    peer = tmp_path / "peer.py"
    peer.write_text(f'''import json, os, shlex, sys, time
from pathlib import Path
sys.path.insert(0, {str(root)!r})
from hypoloom import institutional_client as client
from hypoloom.cli import main
with open(sys.argv[2], 'a') as record: record.write(str(os.getpid()) + '\\n')
def current(*args, **kwargs):
    value = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
    if value.get('exit'): os._exit(value['exit'])
    time.sleep(value.pop('delay', 0))
    return value
client.connections = current
command = shlex.split(sys.argv[3])
raise SystemExit(main(command[command.index('institutional'):]))
''')
    result = subprocess.run([
        executable, "-NoProfile", "-File", str(root / "tests/runtime/fixtures/institutional_query_checks.ps1"),
        "-Connector", str(root / "hypoloom/connection_assets/institutional-connect.ps1"),
        "-Python", sys.executable, "-Peer", str(peer), "-Directory", str(tmp_path), "-Scenario", scenario,
    ], capture_output=True, text=True, timeout=35, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"query_{scenario} passed" in result.stdout
    # Independently observe every actual child registered by the peer fixture.
    if sys.platform != "win32":
        for value in (tmp_path / "pids").read_text().splitlines():
            with pytest.raises(ProcessLookupError): os.kill(int(value), 0)
