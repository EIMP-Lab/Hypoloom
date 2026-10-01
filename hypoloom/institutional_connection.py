"""Operator-owned browser startup; existing CDP, systemd and Compose are authoritative."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit


class ConnectionFailure(RuntimeError):
    """Fixed diagnostics, never raw browser or SSH errors containing credentials."""


def add_arguments(parser):
    parser.add_argument("--institutional-browser-mode", choices=("existing", "local", "server", "client"),
                        help="explicit connection placement; local starts a Linux desktop browser, server uses Compose")
    parser.add_argument("--institutional-browser-executable", help="local Chrome/Chromium executable")
    parser.add_argument("--institutional-browser-profile", help="absolute dedicated local browser data directory")
    parser.add_argument("--institutional-connect-timeout", type=float, default=30)
    parser.add_argument("--institutional-port", type=int, default=0, help="run-owned CDP listener; 0 allocates atomically")
    parser.add_argument("--institutional-compose-file", help="server browser Compose source")


def arguments(args):
    result = []
    for key in ("browser_mode", "browser_executable", "browser_profile", "connect_timeout", "port", "compose_file"):
        value = getattr(args, "institutional_" + key, None)
        if value is not None and (key != "connect_timeout" or value != 30) and (key != "port" or value != 0):
            result += ["--institutional-" + key.replace("_", "-"), str(value)]
    return result


def configure(args):
    mode = getattr(args, "institutional_browser_mode", None)
    timeout = getattr(args, "institutional_connect_timeout", 30)
    port = getattr(args, "institutional_port", 0)
    if isinstance(port, bool) or not 0 <= port <= 65535:
        raise ValueError("institutional port must be between 0 and 65535")
    if port and mode not in {"existing", "client", "server"}:
        raise ValueError("institutional port requires existing, client or server mode")
    if not math.isfinite(timeout) or not 3 <= timeout <= 120:
        raise ValueError("institutional connection timeout must be finite and between 3 and 120 seconds")
    local = any(getattr(args, "institutional_" + key, None) for key in ("browser_executable", "browser_profile"))
    if local and mode not in {"local", "server"}:
        raise ValueError("local browser executable/profile requires --institutional-browser-mode local")
    selected = getattr(args, "institutional_browser_executable", None)
    if selected and mode != "local": raise ValueError("browser executable requires local mode")
    if mode == "local" and selected and os.sep in selected:
        args.institutional_browser_executable = os.path.abspath(selected)
    if mode == "existing" and not args.institutional_browser_url:
        args.institutional_browser_url = "http://127.0.0.1:9222"
    compose = getattr(args, "institutional_compose_file", None)
    if compose:
        if mode != "server": raise ValueError("Compose source requires server mode")
        args.institutional_compose_file = str(Path(compose).resolve())
    if not mode and timeout != 30:
        raise ValueError("connection timeout requires an explicit institutional browser mode")


def runtime_plan(args):
    """Stable launch policy; no listener, directory, browser or Docker operation."""
    configure(args)
    mode = args.institutional_browser_mode
    if mode != "existing" and (args.institutional_browser_url or args.institutional_browser_ui_url):
        raise ValueError("managed run connections discover their addresses; use --institutional-port for a fixed listener")
    from .institutional_browser import PlaywrightInstitutionalBrowser
    PlaywrightInstitutionalBrowser(args.institutional_browser_url or "http://127.0.0.1:1",
        library_url=args.institutional_library_url, ui_url=args.institutional_browser_ui_url)
    value = {"mode": mode, "port": getattr(args, "institutional_port", 0),
             "library_url": args.institutional_library_url, "ready_timeout": args.institutional_connect_timeout}
    if mode == "existing":
        value.update(endpoint=args.institutional_browser_url, browser_ui_url=args.institutional_browser_ui_url)
    elif mode == "local":
        selected = args.institutional_browser_executable
        executable = shutil.which(selected) if selected else next((p for n in (
            "google-chrome", "google-chrome-stable", "chromium", "chromium-browser") if (p := shutil.which(n))), None)
        value.update(executable=executable, profile=str(_profile(args.institutional_browser_profile, executable)), desktop=desktop())
        args.institutional_browser_executable = executable
        args.institutional_browser_profile = value["profile"]
    elif mode == "server":
        source = args.institutional_compose_file or str(Path(getattr(args, "repository", None) or Path.cwd()) / "config/institutional-browser.compose.yaml")
        value.update(compose_file=str(Path(source).resolve()),
            profile=str(_profile(args.institutional_browser_profile, "server-chromium")))
        import yaml
        try:
            service = yaml.safe_load(Path(value["compose_file"]).read_text())["services"]["browser"]
            if not isinstance(service, dict) or not isinstance(service.get("environment", {}), dict): raise ValueError
            if not isinstance(service.get("image"), str) or not service["image"] or service["image"].startswith("-"): raise ValueError
        except (OSError, yaml.YAMLError, ValueError, KeyError, TypeError):
            raise ValueError("invalid browser Compose source") from None
        if service.get("privileged") or service.get("network_mode"):
            raise ValueError("unsafe browser Compose configuration")
        allowed = {"UMASK", "KEEP_APP_RUNNING", "CHROMIUM_REMOTE_DEBUGGING", "CHROMIUM_REMOTE_DEBUGGING_PORT",
            "ENABLE_CJK_FONT", "VNC_LISTENING_PORT", "WEB_HOST_CLIPBOARD_SYNC", "WEB_FILE_MANAGER", "WEB_TERMINAL", "WEB_AUDIO"}
        if set(service.get("environment", {})) - allowed: raise ValueError("unsupported browser Compose environment")
        value["service"] = {"image": service["image"], "environment": dict(service.get("environment", {}))}
        args.institutional_compose_file = value["compose_file"]
        args.institutional_browser_profile = value["profile"]
    return value


def _run(argv, *, timeout=5):
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=max(.1, timeout), check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ConnectionFailure("connection_command_unavailable") from None


def desktop():
    if not sys.platform.startswith("linux") or os.geteuid() == 0:
        return {"kind": None, "reason": "current_linux_user_desktop_required"}
    wayland = os.environ.get("WAYLAND_DISPLAY")
    if wayland:
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR", "/nonexistent"))
        path = Path(wayland) if Path(wayland).is_absolute() else runtime / wayland
        try:
            info = path.stat()
            if stat.S_ISSOCK(info.st_mode) and info.st_uid == os.geteuid():
                return {"kind": "wayland", "window_visibility": "requires_operator_check"}
        except OSError:
            pass
    if os.environ.get("DISPLAY"):
        return {"kind": "x11", "window_visibility": "requires_operator_check"}
    return {"kind": None, "reason": "graphical_session_unavailable"}


def _profile(value, executable):
    base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    path = Path(value) if value else base / "hypoloom" / ("Institutional-" + Path(executable or "chromium").name)
    if not path.is_absolute():
        raise ValueError("institutional browser profile must be an absolute dedicated directory")
    path = path.resolve()
    config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))).resolve()
    defaults = [config / name for name in ("google-chrome", "google-chrome-beta", "google-chrome-unstable", "chromium", "microsoft-edge")]
    if path == Path.home().resolve() or any(path == p or path in p.parents or p in path.parents for p in defaults):
        raise ValueError("use a dedicated institutional profile, never the default personal browser directory")
    return path


def plan(args, repository=None):
    configure(args)
    # Explicit manual connect commands retain their operator-owned semantics.
    # Run-owned init/serve/prepare use runtime_plan instead.
    mode = getattr(args, "institutional_browser_mode", None)
    if mode == "client": raise ValueError("client connections are created by a run or institutional prepare")
    if mode and not args.institutional_browser_url:
        args.institutional_browser_url = "http://127.0.0.1:9222"
    if mode == "server":
        if args.institutional_browser_url.rstrip("/") != "http://127.0.0.1:9222":
            raise ValueError("the manual Compose service requires http://127.0.0.1:9222")
        if args.institutional_browser_ui_url not in (None, "http://127.0.0.1:5800"):
            raise ValueError("the manual Compose service requires browser UI http://127.0.0.1:5800")
        args.institutional_browser_ui_url = "http://127.0.0.1:5800"
    if not args.institutional_browser_url:
        return {"enabled": False}
    # Reuse the actual endpoint/library validator; construction never connects.
    from .institutional_browser import PlaywrightInstitutionalBrowser
    PlaywrightInstitutionalBrowser(args.institutional_browser_url)
    mode = getattr(args, "institutional_browser_mode", None) or "existing"
    result = {"enabled": True, "mode": mode, "endpoint": args.institutional_browser_url,
              "ready_timeout": getattr(args, "institutional_connect_timeout", 30)}
    if mode == "local":
        endpoint = urlsplit(result["endpoint"])
        if endpoint.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("managed local browser requires an IPv4 loopback endpoint")
        selected = getattr(args, "institutional_browser_executable", None)
        executable = shutil.which(selected) if selected else next((p for name in (
            "google-chrome", "google-chrome-stable", "chromium", "chromium-browser") if (p := shutil.which(name))), None)
        profile = _profile(getattr(args, "institutional_browser_profile", None), executable)
        result.update(executable=executable, profile=str(profile), desktop=desktop())
    elif mode == "server":
        result["compose_file"] = str(Path(repository or Path.cwd()).resolve() / "config/institutional-browser.compose.yaml")
    else:
        result["client_script"] = str(Path(__file__).with_name("connection_assets") / "institutional-connect.ps1")
    return result


def _occupied(endpoint):
    parts = urlsplit(endpoint)
    try:
        with socket.create_connection((parts.hostname, parts.port), timeout=.3):
            return True
    except OSError:
        return False


def probe(endpoint, timeout=3):
    """Actual CDP handshake; no pages, cookies, storage or authentication fields."""
    from .institutional_browser import PlaywrightInstitutionalBrowser
    browser = PlaywrightInstitutionalBrowser(endpoint)
    browser._deadline = time.monotonic() + timeout
    try:
        browser._connect()
        return {"connected": True, "browser": browser._browser.version}
    except ImportError:
        return {"connected": False, "reason": "playwright_not_installed"}
    except Exception:
        return {"connected": False, "reason": "cdp_unavailable"}
    finally:
        browser.close()


def _unit(spec):
    if not sys.platform.startswith("linux"):
        raise ConnectionFailure("linux_user_systemd_required")
    result = _run(["systemd-escape", "--path", "--template=hypoloom-browser@.service", spec["profile"]])
    name = result.stdout.strip()
    if result.returncode or not re.fullmatch(r"hypoloom-browser@[A-Za-z0-9_.:\\-]+\.service", name):
        raise ConnectionFailure("browser_unit_unavailable")
    return name


def _unit_state(unit):
    result = _run(["systemctl", "--user", "show", unit, "--property=ActiveState,MainPID,LoadState"])
    if result.returncode and not result.stdout:
        raise ConnectionFailure("user_systemd_unavailable")
    return {"unit": unit, **dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)}


def _owned(spec, properties):
    try:
        pid = int(properties.get("MainPID", 0))
        if not pid: return False
        root = Path("/proc") / str(pid)
        if root.stat().st_uid != os.geteuid(): return False
        # Chrome rewrites /proc/cmdline after startup. systemd retains the actual
        # argv, including spaces, and is already the lifecycle authority.
        bus = ["busctl", "--user", "--json=short"]
        unit = _run([*bus, "call", "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
                     "org.freedesktop.systemd1.Manager", "GetUnit", "s", properties["unit"]])
        path = json.loads(unit.stdout)["data"][0]
        command = _run([*bus, "get-property", "org.freedesktop.systemd1", path,
                        "org.freedesktop.systemd1.Service", "ExecStart"])
        starts = json.loads(command.stdout)["data"]
        if unit.returncode or command.returncode or len(starts) != 1: return False
        executable, argv = starts[0][:2]
        port = urlsplit(spec["endpoint"]).port
        return (executable == spec["executable"] and f"--user-data-dir={spec['profile']}" in argv
                and f"--remote-debugging-port={port}" in argv and "--remote-debugging-address=127.0.0.1" in argv)
    except (OSError, ValueError, KeyError, IndexError, TypeError, ConnectionFailure):
        return False


def _profile_busy(profile):
    lock = profile / "SingletonLock"
    if not os.path.lexists(lock): return False
    try:
        host, _, pid = os.readlink(lock).rpartition("-")
        # Chrome owns stale-lock recovery. Only let Chrome retry a lock whose
        # local process has exited; never remove it ourselves.
        return host != socket.gethostname() or not pid.isdigit() or Path("/proc", pid).exists()
    except OSError:
        return True


def _close_browser(endpoint):
    """Explicit managed stop: Chromium must flush cookies before systemd exits."""
    from .institutional_browser import PlaywrightInstitutionalBrowser
    browser = PlaywrightInstitutionalBrowser(endpoint)
    browser._deadline = time.monotonic() + 5
    try:
        browser._connect()
        session = browser._browser.new_browser_cdp_session()
        session.send("Browser.close")
    except Exception:
        # Browser.close may close its transport before the reply. The caller
        # checks actual unit termination instead of interpreting the exception.
        pass
    finally:
        browser.close()


def _reuse_local(spec, unit, deadline):
    """Refresh OS ownership after preflight; another caller may have started it."""
    while time.monotonic() < deadline:
        properties = _unit_state(unit)
        if properties.get("ActiveState") not in {"active", "activating", "reloading"}:
            return None
        if _owned(spec, properties):
            observed = probe(spec["endpoint"], min(2, max(.1, deadline - time.monotonic())))
            if observed["connected"]:
                return {"status": "reused", "unit": unit, **observed}
        elif properties.get("ActiveState") != "activating" or properties.get("MainPID") != "0":
            raise ConnectionFailure("browser_unit_ownership_mismatch")
        # Type=exec can be activating before MainPID is available. Never adopt
        # its endpoint until the existing ownership validator succeeds.
        time.sleep(.1)
    raise ConnectionFailure("managed_browser_not_ready")


def _local(spec, action):
    deadline = time.monotonic() + spec["ready_timeout"]
    unit = _unit(spec)
    properties = _unit_state(unit)
    live = properties.get("ActiveState") in {"active", "activating", "reloading"}
    owned = _owned(spec, properties)
    observed = probe(spec["endpoint"], min(3, spec["ready_timeout"]))
    if action == "status":
        return {**spec, **observed, "unit": unit, "managed": owned,
                "status": "ready" if owned and observed["connected"] else "unavailable"}
    if action == "stop":
        if not live: return {"status": "not_running", "unit": unit}
        if not owned: raise ConnectionFailure("browser_unit_ownership_mismatch")
        _close_browser(spec["endpoint"])
        until = time.monotonic() + 10
        while _unit_state(unit).get("ActiveState") in {"active", "activating", "reloading", "deactivating"}:
            if time.monotonic() >= until: raise ConnectionFailure("browser_close_not_confirmed")
            time.sleep(.1)
        return {"status": "stopped", "unit": unit, "profile_preserved": True}
    if reused := _reuse_local(spec, unit, deadline): return reused
    if _occupied(spec["endpoint"]):
        if reused := _reuse_local(spec, unit, deadline): return reused
        raise ConnectionFailure("browser_endpoint_occupied")
    if observed.get("reason") == "playwright_not_installed": raise ConnectionFailure(observed["reason"])
    if not spec["executable"]: raise ConnectionFailure("browser_executable_missing")
    if not spec["desktop"]["kind"]: raise ConnectionFailure(spec["desktop"]["reason"])
    profile = Path(spec["profile"])
    if _profile_busy(profile):
        if reused := _reuse_local(spec, unit, deadline): return reused
        raise ConnectionFailure("browser_profile_in_use")
    profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = profile.stat()
    if not profile.is_dir() or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ConnectionFailure("browser_profile_must_be_private")
    argv = ["systemd-run", "--user", "--quiet", "--collect", "--unit=" + unit,
            "--property=Type=exec", "--property=KillMode=control-group"]
    for name in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS", "XDG_SESSION_TYPE"):
        if os.environ.get(name): argv += ["--setenv=" + name + "=" + os.environ[name]]
    argv += ["--", spec["executable"], "--no-first-run", "--no-default-browser-check",
             "--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={urlsplit(spec['endpoint']).port}",
             "--user-data-dir=" + str(profile), "--ozone-platform=" + spec["desktop"]["kind"], "about:blank"]
    launched = _run(argv, timeout=min(10, max(.1, deadline - time.monotonic())))
    while time.monotonic() < deadline:
        properties = _unit_state(unit)
        if _owned(spec, properties):
            observed = probe(spec["endpoint"], min(2, max(.1, deadline - time.monotonic())))
            if observed["connected"]:
                return {"status": "started" if launched.returncode == 0 else "reused", "unit": unit, **observed}
        if properties.get("ActiveState") in {"failed", "inactive"}: break
        time.sleep(.1)
    # A failed concurrent start never stops the winning caller's browser.
    if launched.returncode == 0 and _owned(spec, _unit_state(unit)):
        _run(["systemctl", "--user", "stop", unit], timeout=15)
    raise ConnectionFailure("browser_start_failed")


def _compose(spec, action):
    deadline = time.monotonic() + spec["ready_timeout"]
    path = Path(spec["compose_file"])
    if not path.is_file(): raise ConnectionFailure("compose_checkout_required")
    command = ["docker", "compose", "--project-name", "hypoloom-institutional-browser", "--file", str(path)]
    listed = _run([*command, "ps", "--all", "--quiet", "browser"])
    if listed.returncode: raise ConnectionFailure("docker_compose_unavailable")
    ids = listed.stdout.split()
    owned = False
    running = False
    if ids:
        checked = _run(["docker", "inspect", *ids])
        try:
            containers = json.loads(checked.stdout)
            owned = len(containers) == 1 and all(c["Config"]["Labels"].get("com.docker.compose.project") == "hypoloom-institutional-browser"
                and c["Config"]["Labels"].get("com.docker.compose.service") == "browser"
                and c["HostConfig"]["PortBindings"] == {port + "/tcp": [{"HostIp": "127.0.0.1", "HostPort": port}]
                                                        for port in ("5800", "9222")}
                for c in containers)
            running = owned and containers[0].get("State", {}).get("Running") is True
        except (ValueError, KeyError, TypeError):
            pass
        if not owned: raise ConnectionFailure("browser_container_ownership_mismatch")
    observed = probe(spec["endpoint"], min(3, spec["ready_timeout"]))
    if action == "status":
        return {**spec, **observed, "managed": owned, "status": "ready" if running and observed["connected"] else "unavailable"}
    if action == "stop":
        if not ids: return {"status": "not_running"}
        stopped = _run([*command, "stop", "browser"], timeout=40)
        if stopped.returncode: raise ConnectionFailure("browser_stop_failed")
        return {"status": "stopped", "profile_preserved": True}
    if running and observed["connected"]: return {"status": "reused", **observed}
    if not running and _occupied(spec["endpoint"]): raise ConnectionFailure("browser_endpoint_occupied")
    if observed.get("reason") == "playwright_not_installed": raise ConnectionFailure(observed["reason"])
    # Validate the actual Compose expansion before creating any container. The
    # checked-in file remains the only configuration, including volume ownership.
    expanded = _run([*command, "config", "--format", "json"])
    try:
        service = json.loads(expanded.stdout)["services"]["browser"]
        ports = service["ports"]
        valid = (expanded.returncode == 0 and len(ports) == 2 and not service.get("network_mode")
                 and not service.get("privileged") and {str(p["target"]) for p in ports} == {"5800", "9222"}
                 and all(p["host_ip"] == "127.0.0.1" and str(p["published"]) == str(p["target"])
                         and p.get("protocol", "tcp") == "tcp" for p in ports))
    except (ValueError, KeyError, TypeError):
        valid = False
    if not valid: raise ConnectionFailure("compose_loopback_configuration_required")
    started = _run([*command, "up", "--detach", "--no-build", "--pull", "never", "--no-deps", "browser"],
                   timeout=max(.1, deadline - time.monotonic()))
    if started.returncode: raise ConnectionFailure("browser_container_start_failed_check_local_image")
    while time.monotonic() < deadline:
        observed = probe(spec["endpoint"], min(2, max(.1, deadline - time.monotonic())))
        if observed["connected"]: return {"status": "started", **observed}
        time.sleep(.2)
    return {"status": "unavailable", "reason": "container_cdp_not_ready", "service_may_be_running": True}


def operate(args, action, repository=None):
    spec = plan(args, repository)
    if not spec["enabled"]: raise ValueError("select an institutional browser mode or endpoint")
    try:
        if spec["mode"] == "local": result = _local(spec, action)
        elif spec["mode"] == "server": result = _compose(spec, action)
        elif action == "stop": result = {"status": "unmanaged", "reason": "external_browser_not_stopped"}
        else:
            observed = probe(spec["endpoint"], min(5, spec["ready_timeout"]))
            result = {**observed, "status": "ready" if observed["connected"] else "unavailable"}
        return {**spec, **result}
    except ConnectionFailure as exc:
        return {**spec, "status": "unavailable", "reason": str(exc)}
    except OSError:
        return {**spec, "status": "unavailable", "reason": "connection_io_error"}


def run(args, *, output=print):
    if not args.institutional_browser_url and not args.institutional_browser_mode:
        args.institutional_browser_mode = "existing"
    result = operate(args, args.connection_action, getattr(args, "repository", None))
    output(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in {"ready", "reused", "started", "stopped", "not_running"} else 2


def main(argv=None):
    from .institutional_options import add_arguments as options
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("connection_action", choices=("status", "start", "stop"))
    parser.add_argument("--repository")
    options(parser, connection=True)
    try: return run(parser.parse_args(argv))
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "unavailable", "reason": str(exc) if isinstance(exc, ValueError) else "connection_io_error"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
