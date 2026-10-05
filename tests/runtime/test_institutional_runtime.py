"""Observe owned listeners, real Host state and browser lifetime independently."""
from contextlib import contextmanager
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

import pytest

from hypoloom.institutional_relay import CDPRelay, tcp_backend
from hypoloom.institutional_runtime import RunAccess, RunBrowser


def client_spec(**values):
    return {"mode": "client", "port": 0, "library_url": None, "ready_timeout": 3, **values}


def get(endpoint, token=None, path="/json/version"):
    parsed = urlsplit(endpoint)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
    try:
        conn.request("GET", path, headers={"Authorization": "Bearer " + token} if token else {})
        reply = conn.getresponse()
        return reply.status, reply.read()
    finally:
        conn.close()


def assert_free(endpoint):
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", urlsplit(endpoint).port))


def eventually(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(): return
        time.sleep(.05)
    assert predicate()


def test_sigkill_releases_listener_and_private_transport_directory():
    code = """
import json, time
from hypoloom.institutional_runtime import RunAccess
access = RunAccess({'mode': 'client', 'port': 0, 'ready_timeout': 5})
access.sync({'a'})
print(json.dumps(access.connections()), flush=True)
time.sleep(60)
"""
    process = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        entry = json.loads(process.stdout.readline())[0]
        assert get(entry["endpoint"])[0] == 401
        directory = Path(entry["socket_path"]).parent
        assert directory.exists()
        process.kill(); process.wait(timeout=5)
        eventually(lambda: not directory.exists())
        assert_free(entry["endpoint"])
    finally:
        if process.poll() is None: process.kill(); process.wait()
        process.stdout.close()


def test_private_discovery_and_watch_exit_when_operation_ends(tmp_path):
    from hypoloom.institutional_client import PreparationControl, connections
    root = tmp_path / "private"; root.mkdir(mode=0o700)
    access = RunAccess(client_spec())
    access.sync({"preparation"})
    final = tmp_path / "final"
    target = {"state_root": str(final), "run_id": "run", "request_id": "create-run"}
    control = PreparationControl(root, access, target=target)
    try:
        current = connections(final, "run", "create-run", preparation_root=root)["connections"]
        assert len(current) == 1 and current[0]["run_id"] == "preparation"
        old = current[0]
        access.sync(set())
        assert connections(final, "run", "create-run", preparation_root=root)["connections"] == []
        result = subprocess.run([sys.executable, "-m", "hypoloom", "institutional", "watch", "--state-root", str(final),
            "--run-id", "run", "--request-id", "create-run", "--preparation-root", str(root), "--socket-path", old["socket_path"], "--browser-path", "/devtools/browser/fixture"],
            capture_output=True, timeout=5)
        assert result.returncode == 0, result.stderr
    finally:
        control.close(); access.close()


@pytest.mark.skipif(os.environ.get("HYPOLOOM_INSTITUTIONAL_SSH_TEST") != "1", reason="isolated real-SSH opt-in")
@pytest.mark.parametrize("discovery_outage", [False, True])
def test_real_ssh_unix_forward_exits_on_run_end_and_reconnects(tmp_path, discovery_outage):
    """Ephemeral sshd/keys only; never reads or modifies the user's SSH identity."""
    import getpass
    from hypoloom.institutional_client import PreparationControl, prepare_forward
    sshd = shutil.which("sshd")
    if not sshd: pytest.skip("sshd required")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0)); port = reservation.getsockname()[1]
    for name in ("host", "client"):
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp_path / name)], check=True)
    known = tmp_path / "known"
    known.write_text(f"[127.0.0.1]:{port} " + (tmp_path / "host.pub").read_text())
    config = tmp_path / "sshd.conf"
    config.write_text(f'''Port {port}
ListenAddress 127.0.0.1
HostKey {tmp_path / 'host'}
PidFile {tmp_path / 'sshd.pid'}
AuthorizedKeysFile {tmp_path / 'client.pub'}
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM no
StrictModes no
AllowUsers {getpass.getuser()}
AllowStreamLocalForwarding remote
LogLevel ERROR
''')
    server = subprocess.Popen([sshd, "-D", "-e", "-f", str(config)], stderr=subprocess.PIPE, text=True)
    root = tmp_path / "control"; root.mkdir(mode=0o700)
    access = RunAccess(client_spec()); access.sync({"preparation"})
    final = tmp_path / "final"
    target = {"state_root": str(final), "run_id": "run", "request_id": "create-run"}
    control = PreparationControl(root, access, target=target)
    child = None
    try:
        def listening():
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.2): return True
            except OSError: return False
        eventually(listening)
        browser = access.instances["preparation"].backend
        with backend() as endpoint:
            if discovery_outage:
                _check_powershell_discovery_outage(tmp_path, port, known, root, final, endpoint, browser)
                access.sync(set())
                assert_free(browser.endpoint)
                return
            watch = shlex.join([sys.executable, "-m", "hypoloom", "institutional", "watch", "--state-root", str(final),
                "--run-id", "run", "--request-id", "create-run", "--preparation-root", str(root), "--socket-path", str(browser.socket_path), "--browser-path", "/devtools/browser/fixture"])
            remote = "cd " + shlex.quote(str(Path.cwd())) + " && " + watch
            argv = ["ssh", "-F", "/dev/null", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                "-o", "ExitOnForwardFailure=yes", "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + str(known),
                "-p", str(port), "-i", str(tmp_path / "client"), "-R",
                f"{browser.socket_path}:127.0.0.1:{urlsplit(endpoint).port}", getpass.getuser() + "@127.0.0.1", remote]
            for _ in range(2):
                child = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                eventually(lambda: browser.socket_path.exists())
                eventually(lambda: get(browser.endpoint, browser.relay.token)[0] == 200)
                child.terminate(); child.communicate(timeout=5)
                eventually(lambda: prepare_forward(final, "run", str(browser.socket_path), request_id="create-run", preparation_root=root))
                assert not browser.socket_path.exists()
            child = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            eventually(lambda: browser.socket_path.exists())
            access.sync(set())
            _, errors = child.communicate(timeout=8)
            assert child.returncode == 0, errors
            assert not browser.socket_path.exists()
            assert_free(browser.endpoint)
    finally:
        if child and child.poll() is None: child.kill(); child.communicate()
        control.close(); access.close()
        server.terminate(); server.communicate(timeout=5)


def _check_powershell_discovery_outage(tmp_path, port, known, root, final, endpoint, browser):
    from hypoloom.institutional_client import prepare_forward
    pwsh = os.environ.get("HYPOLOOM_TEST_PWSH") or shutil.which("pwsh")
    if not pwsh: pytest.skip("PowerShell required for real discovery loop")
    outage = tmp_path / "discovery-down"
    stop = tmp_path / "stop-fixture"
    wrapper = tmp_path / "ssh-fixture"
    # Fail only list requests. Forward-ready and remote identity/lifetime watch
    # continue through the real isolated SSH service with ephemeral identities.
    options = ["ssh", "-F", "/dev/null", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", "UserKnownHostsFile=" + str(known), "-p", str(port), "-i", str(tmp_path / "client")]
    wrapper.write_text(f'''#!{sys.executable}
import os, sys
from pathlib import Path
if "'connections'" in sys.argv[-1] and Path({str(outage)!r}).exists():
    sys.exit(255)
args = sys.argv[1:]
args[-2] = '127.0.0.1'
os.execvp('ssh', {options!r} + args)
''')
    wrapper.chmod(0o700)
    project = Path(__file__).resolve().parents[2]
    process = subprocess.Popen([pwsh, "-NoProfile", "-File",
        str(project / "tests/runtime/fixtures/institutional_discovery_ssh.ps1"),
        "-Connector", str(project / "hypoloom/connection_assets/institutional-connect.ps1"),
        "-Ssh", str(wrapper), "-Repository", str(project), "-PythonExecutable", sys.executable,
        "-StateRoot", str(final), "-RunId", "run", "-RequestId", "create-run", "-PreparationRoot", str(root), "-LocalPort", str(urlsplit(endpoint).port), "-StopFile", str(stop)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines = []
    # Read incrementally so test observations never block on a long-lived helper.
    def read():
        for line in process.stdout: lines.append(line.strip())
    reader = threading.Thread(target=read, daemon=True); reader.start()
    try:
        eventually(lambda: browser.socket_path.exists())
        eventually(lambda: get(browser.endpoint, browser.relay.token)[0] == 200)
        outage.touch()
        eventually(lambda: any("retry_in_seconds=30" in line for line in lines) or process.poll() is not None)
        assert process.poll() is None, lines
        assert get(browser.endpoint, browser.relay.token)[0] == 200
        outage.unlink()
        eventually(lambda: "run_discovery_recovered" in lines)
        assert sum(line.startswith("run_connection_started;") for line in lines) == 1, lines
        assert get(browser.endpoint, browser.relay.token)[0] == 200
        stop.touch()
        assert process.wait(timeout=10) == 0, lines
        reader.join(timeout=2)
        assert "fixture_stopped" in lines
        eventually(lambda: prepare_forward(final, "run", str(browser.socket_path), request_id="create-run", preparation_root=root))
        assert not browser.socket_path.exists()
    finally:
        if process.poll() is None:
            stop.touch()
            try: process.wait(timeout=20)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
        reader.join(timeout=2)
        process.stdout.close()


@pytest.mark.parametrize("kill", [False, True])
def test_container_guardian_closes_on_eof_or_host_kill_without_publishing_ports(tmp_path, kill):
    """Real user service and process death; Docker is an observable local fixture."""
    events = tmp_path / "events.jsonl"
    item = tmp_path / "container.json"
    docker = tmp_path / "docker"
    docker.write_text(f'''#!{sys.executable}
import json, sys
from pathlib import Path
events, item = Path({str(events)!r}), Path({str(item)!r})
args = sys.argv[1:]
with events.open('a') as out: out.write(json.dumps(args) + '\\n')
if args[0] == 'create':
    labels = dict(args[i+1].split('=', 1) for i, v in enumerate(args) if v == '--label')
    item.write_text(json.dumps({{'Id': 'fixture-id', 'Config': {{'Labels': labels}}}}))
    print('fixture-id')
elif args[0] == 'inspect':
    if not item.exists(): sys.exit(1)
    print('[' + item.read_text() + ']')
elif args[0] == 'rm': item.unlink()
''')
    docker.chmod(0o700)
    spec = client_spec(mode="server", profile=str(tmp_path / "profile"), service={"image": "fixture:1", "environment": {}})
    code = """
import json, sys
from hypoloom.institutional_container import Container
container = Container(json.loads(sys.argv[1]), 'a')
container.docker = sys.argv[2]
print(container.start(), flush=True)
sys.stdin.read()
container.close()
"""
    process = subprocess.Popen([sys.executable, "-c", code, json.dumps(spec), str(docker)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "fixture-id"
        assert item.exists()
        if kill: process.kill()
        else: process.stdin.close()
        process.wait(timeout=10)
        eventually(lambda: not item.exists())
        calls = [json.loads(line) for line in events.read_text().splitlines()]
        create = next(c for c in calls if c[0] == "create")
        assert not any(v in create for v in ("-p", "--publish", "--network=host"))
        assert "WEB_LOCALHOST_ONLY=1" in create
        assert sum(c[0] == "rm" for c in calls) == 1
        assert Path(spec["profile"]).is_dir()
    finally:
        if process.poll() is None: process.kill(); process.wait()
        process.stdin.close(); process.stdout.close()


@contextmanager
def backend():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            body = json.dumps({"webSocketDebuggerUrl": "ws://localhost:1234/devtools/browser/fixture"}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    try: yield f"http://127.0.0.1:{server.server_port}"
    finally: server.shutdown(); server.server_close(); worker.join()


def test_relay_authentication_rewrites_identity_and_never_closes_external_browser():
    with backend() as endpoint:
        relay = CDPRelay(lambda: tcp_backend(endpoint))
        try:
            assert get(relay.endpoint)[0] == 401
            assert get(relay.endpoint, "wrong")[0] == 401
            assert get(relay.endpoint, relay.token, "/json/list")[0] == 404
            status, body = get(relay.endpoint, relay.token)
            assert status == 200
            assert json.loads(body)["webSocketDebuggerUrl"] == relay.endpoint.replace("http:", "ws:") + "/devtools/browser/fixture"
        finally: relay.close()
        assert_free(relay.endpoint)
        assert get(endpoint)[0] == 200


def test_two_runs_release_independently_without_leaking_credentials_or_pinning_ports():
    access = RunAccess(client_spec())
    before = access.configuration
    assert access.instances == {}
    try:
        access.sync({"a", "b"})
        a, b = (access.instances[k].backend for k in ("a", "b"))
        assert a.endpoint != b.endpoint and a.socket_path != b.socket_path
        assert a.directory.stat().st_mode & 0o777 == 0o700
        directory = a.directory
        assert get(a.endpoint, a.relay.token)[0] == 503
        public = json.dumps(access.status("a"))
        assert a.relay.token not in public and str(a.socket_path) not in public
        assert access.configuration == before
        access.sync({"b"})
        assert_free(a.endpoint)
        assert not directory.exists()
        assert get(b.endpoint)[0] == 401
        assert access.status("a")["connection"]["state"] == "inactive"
        access.sync({"b"})
        assert access.instances["b"].backend is b
    finally: access.close()
    assert_free(b.endpoint)


def test_fixed_port_conflict_cannot_adopt_another_runs_endpoint():
    a = RunAccess(client_spec()); b = None
    try:
        a.sync({"a"}); browser = a.instances["a"].backend
        b = RunAccess(client_spec(port=urlsplit(browser.endpoint).port))
        b.sync({"b"})
        assert b.status("b")["connection"]["reason"] == "port_in_use"
        assert b.connections()[0].get("socket_path") is None
        assert get(browser.endpoint)[0] == 401
    finally:
        if b: b.close()
        a.close()


def test_one_run_port_failure_preserves_other_run_and_retries_after_release(monkeypatch):
    from hypoloom.institutional_browser import PlaywrightInstitutionalBrowser
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0)); port = reservation.getsockname()[1]
    access = RunAccess(client_spec(port=port))
    try:
        access.tick({"a"})
        first = access.instances["a"].backend
        access.tick({"a", "b"})
        assert get(first.endpoint)[0] == 401
        assert access.status("b")["connection"]["state"] == "unavailable"
        access.tick({"b"})
        assert_free(first.endpoint)
        # Observe the actual authenticated listener rebuilt on the next browser
        # acquisition; no Windows browser is needed for this transport check.
        monkeypatch.setattr(PlaywrightInstitutionalBrowser, "_connect", lambda browser: get(browser.endpoint, browser.relay.token)[0])
        second = access.instances["b"].backend
        assert second._connect() == 503  # Endpoint is allocated; client is absent.
        assert second.endpoint == first.endpoint
        assert access.status("b")["connection"]["state"] == "waiting_for_client"
    finally: access.close()
    assert_free(second.endpoint)


def test_preparation_does_not_print_private_gui_credentials(tmp_path, monkeypatch):
    from hypoloom.cli import build_parser
    from hypoloom.institutional_prepare import run
    config = tmp_path / "browser.yaml"
    config.write_text("services:\n  browser:\n    image: fixture:1\n")
    # This fixture checks GUI-secret filtering; connectivity has separate real tests.
    monkeypatch.setattr(RunBrowser, "check_connection", lambda *a, **k: {"state": "ready"})
    monkeypatch.setattr("hypoloom.institutional_prepare._observe", lambda *args, **kwargs: {"state": "opened"})
    args = build_parser().parse_args(["institutional", "prepare", "--repository", str(tmp_path),
        "--institutional-browser-mode", "server", "--institutional-compose-file", str(config),
        "--institutional-browser-profile", str(tmp_path / "profile"), "--provider", "IEEE"])
    output = []
    assert run(args, input_fn=lambda *args: "q", output=output.append) == 0
    assert "browser_ui_password" not in json.loads(output[0])["connection"]
    assert not list((tmp_path / "state").iterdir())


def test_host_cancel_and_same_version_recovery_follow_durable_state(tmp_path):
    from hypoloom.control_protocol import ControlRequest
    from hypoloom.workflow_host import WorkflowHost
    from hypoloom.oracle import Oracle
    from tests.runtime.test_expert_loop_host import _pilot_payload
    with tempfile.TemporaryDirectory(prefix="ir-target-", dir="/var/tmp") as target:
        root = tmp_path / "state"
        host = WorkflowHost(root, institutional_access=RunAccess(client_spec()))
        host.initialize()
        try:
            for name in ("a", "b"):
                selected_target = Path(target) / name
                selected_target.mkdir()
                selected_input = tmp_path / name
                selected_input.mkdir()
                reply = host.handle(ControlRequest(1, "create-" + name, None, "CREATE_RUN",
                    _pilot_payload(selected_input, selected_target, name)))
                assert reply.status == "accepted", reply
            host.tick()
            access = host.institutional_access
            a, b = (access.instances[k].backend for k in ("a", "b"))
            pause = host.handle(ControlRequest(1, "pause-b", None, "PAUSE", {"run_id": "b",
                "scope": {"role": None, "component_id": None, "chain": None}, "reason": "operator"}))
            assert pause.status == "accepted", pause
            count = host.store.inspect("SELECT count(*) AS n FROM workflow_commands")[0]["n"]
            reply = host.handle(ControlRequest(1, "connections", None, "INSTITUTIONAL_CONNECTIONS", {}))
            assert reply.status == "rejected" and not reply.result
            reply = host.handle(ControlRequest(1, "connections-a", None, "INSTITUTIONAL_CONNECTIONS",
                {"run_id": "a", "request_id": "create-a"}))
            assert reply.status == "accepted", reply
            assert {e["run_id"] for e in reply.result["connections"]} == {"a"}
            assert host.store.inspect("SELECT count(*) AS n FROM workflow_commands")[0]["n"] == count
            reply = host.handle(ControlRequest(1, "cancel-a", None, "CANCEL", {"run_id": "a", "cause": "test"}))
            assert reply.status == "accepted", reply
            host.tick()
            assert_free(a.endpoint)
            assert get(b.endpoint)[0] == 401
            assert access.instances.keys() == {"b"}
            configuration = access.configuration
        finally: host.close()
        assert_free(b.endpoint)
        recovered = WorkflowHost(root, institutional_access=RunAccess(client_spec()))
        recovered.initialize()
        try:
            recovered.tick()
            assert recovered.institutional_access.configuration == configuration
            assert recovered.institutional_access.instances.keys() == {"b"}
            assert recovered.institutional_access.instances["b"].backend.socket_path != b.socket_path
            recovered.handle(ControlRequest(1, "cancel-b", None, "CANCEL", {"run_id": "b", "cause": "test"}))
            for _ in range(10): recovered.tick()
            assert not recovered.institutional_access.instances
            for name in ("a", "b"):
                assert recovered.store.latest_transition(name)["state"] == "CANCELLED"
                assert Oracle(recovered.store.database_path, recovered.publisher.root).audit(name).ok
        finally: recovered.close()


@pytest.mark.parametrize("mode,terminal", [("VALID", "COMPLETED"), ("MALFORMED_JSON", "FAILED")])
def test_real_host_terminal_tick_releases_connection_before_host_close(tmp_path, monkeypatch, mode, terminal):
    from hypoloom.control_protocol import ControlRequest
    from hypoloom.fake_agent import FakeAgent
    from hypoloom.workflow_host import WorkflowHost
    from hypoloom.oracle import Oracle
    from tests.runtime.test_expert_loop_host import _pilot_payload, _drive
    # Reuse the established deterministic offline pilot at its supported ACI.
    if mode == "VALID": monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 13)
    with tempfile.TemporaryDirectory(prefix="ir-terminal-", dir="/var/tmp") as target:
        access = RunAccess(client_spec())
        host = WorkflowHost(tmp_path / "state", local_agent=FakeAgent(mode), institutional_access=access)
        host.initialize()
        try:
            reply = host.handle(ControlRequest(1, "create", None, "CREATE_RUN", _pilot_payload(tmp_path, Path(target), "a")))
            assert reply.status == "accepted", reply
            host.tick()
            browser = access.instances["a"].backend
            directory = browser.directory
            _drive(host, "a")
            assert host.store.latest_transition("a")["state"] == terminal
            assert not access.instances and not directory.exists()
            assert_free(browser.endpoint)
            assert Oracle(host.store.database_path, host.publisher.root).audit("a").ok
        finally: host.close()


@pytest.mark.skipif(os.environ.get("HYPOLOOM_INSTITUTIONAL_BROWSER_TEST") != "1", reason="real-browser opt-in")
def test_native_pipe_persists_profile_and_rejects_concurrent_owner(tmp_path):
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome: pytest.skip("Chrome required")
    wrapper = tmp_path / "headless fixture"
    wrapper.write_text("#!/bin/sh\nexec " + shlex.quote(chrome) + ' --headless --no-sandbox --password-store=basic "$@"\n')
    wrapper.chmod(0o700)
    spec = client_spec(mode="local", executable=str(wrapper), profile=str(tmp_path / "profile"), desktop={"kind": "headless"})
    first, other = RunBrowser(spec, "a"), RunBrowser(spec, "b")
    try:
        context = first._connect()
        context.add_cookies([{"name": "institutional-test", "value": "synthetic", "url": "https://example.invalid", "expires": time.time() + 3600}])
        with pytest.raises(Exception, match="browser_profile_in_use"): other._connect()
        assert context.browser.is_connected()
    finally:
        first.release(); first.close(); other.release(); other.close()
    restored = RunBrowser(spec, "c")
    try:
        assert any(c["name"] == "institutional-test" for c in restored._connect().cookies())
        assert restored.relay is None
    finally: restored.release(); restored.close()


@pytest.mark.skipif(os.environ.get("HYPOLOOM_INSTITUTIONAL_BROWSER_TEST") != "1", reason="real-browser opt-in")
def test_native_pipe_browser_exits_after_owner_sigkill(tmp_path):
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome: pytest.skip("Chrome required")
    wrapper = tmp_path / "headless"
    wrapper.write_text("#!/bin/sh\nexec " + shlex.quote(chrome) + ' --headless --no-sandbox --password-store=basic "$@"\n')
    wrapper.chmod(0o700)
    spec = client_spec(mode="local", executable=str(wrapper), profile=str(tmp_path / "profile"), desktop={"kind": "headless"})
    code = """
import json, os, sys, time
from pathlib import Path
from hypoloom.institutional_runtime import RunBrowser
spec = json.loads(sys.argv[1]); browser = RunBrowser(spec, 'a')
browser._connect()
print(os.readlink(Path(spec['profile']) / 'SingletonLock').rsplit('-', 1)[1], flush=True)
time.sleep(60)
"""
    process = subprocess.Popen([sys.executable, "-c", code, json.dumps(spec)], stdout=subprocess.PIPE, text=True)
    pid = None
    def browser_live():
        try: return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
        except FileNotFoundError: return False
    try:
        pid = int(process.stdout.readline())
        assert browser_live()
        process.kill(); process.wait(timeout=5)
        eventually(lambda: not browser_live(), timeout=15)
        assert Path(spec["profile"]).is_dir()
    finally:
        if process.poll() is None: process.kill(); process.wait()
        process.stdout.close()
        if pid and browser_live(): os.kill(pid, 15)


@pytest.mark.skipif(os.environ.get("HYPOLOOM_INSTITUTIONAL_BROWSER_TEST") != "1", reason="real-browser opt-in")
def test_real_cdp_websocket_preserves_external_browser(tmp_path):
    from playwright.sync_api import sync_playwright
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome: pytest.skip("Chrome required")
    process = subprocess.Popen([chrome, "--headless", "--no-sandbox", "--remote-debugging-port=0",
        "--remote-debugging-address=127.0.0.1", "--user-data-dir=" + str(tmp_path / "profile"), "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    relay = None
    try:
        port_file = tmp_path / "profile/DevToolsActivePort"
        deadline = time.monotonic() + 15
        while not port_file.exists() and time.monotonic() < deadline: time.sleep(.05)
        port = int(port_file.read_text().splitlines()[0])
        endpoint = f"http://127.0.0.1:{port}"
        relay = CDPRelay(lambda: tcp_backend(endpoint))
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(relay.endpoint,
                headers={"Authorization": "Bearer " + relay.token}, timeout=5000)
            page = browser.contexts[0].new_page()
            assert page.evaluate("2 + 3") == 5
            page.close()
        relay.close()
        assert get(endpoint)[0] == 200
        assert_free(relay.endpoint)
    finally:
        if relay: relay.close()
        process.terminate(); process.wait(timeout=15)
