"""Run-owned container, with a user-systemd EOF guardian and private exec I/O."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import threading


def command(argv, timeout=20):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=True)


def cleanup(name, run_id, unit, docker):
    found = subprocess.run([docker, "inspect", name], capture_output=True, text=True, timeout=10)
    if found.returncode:
        # Distinguish an absent owned container from an unreachable daemon.
        command([docker, "info", "--format", "{{.ServerVersion}}"], timeout=10)
        return
    values = json.loads(found.stdout)
    if len(values) != 1: raise ValueError("container_ownership_mismatch")
    item = values[0]
    labels = item["Config"].get("Labels", {})
    expected = {"org.hypoloom.uid": str(os.geteuid()), "org.hypoloom.run": run_id, "org.hypoloom.unit": unit}
    if any(labels.get(k) != v for k, v in expected.items()): raise ValueError("container_ownership_mismatch")
    identifier = item["Id"]
    subprocess.run([docker, "stop", "--time", "10", identifier], capture_output=True, timeout=15)
    command([docker, "rm", "--force", identifier], timeout=10)


def guardian(name, run_id, unit, docker):
    config = json.loads(sys.stdin.readline())
    profile = Path(config["profile"])
    # The image startup removes Chromium's locks. Hold the existing directory
    # flock for its entire lifetime so two instances cannot open the same data.
    descriptor = os.open(profile, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # A crashed guardian releases flock before ExecStopPost removes Docker
        # resources. The existing container metadata closes that handoff gap.
        occupied = command([docker, "ps", "--all", "--filter", "volume=/config", "--format", "{{.ID}}"])
        for identifier in occupied.stdout.split():
            mounts = json.loads(command([docker, "inspect", "--format", "{{json .Mounts}}", identifier]).stdout)
            if any(m.get("Source") == str(profile) for m in mounts): raise ValueError("browser_profile_in_use")
        environment = dict(config["environment"])
        environment.update(USER_ID=str(os.geteuid()), GROUP_ID=str(os.getegid()), UMASK="0077",
            KEEP_APP_RUNNING="0", CHROMIUM_REMOTE_DEBUGGING="0",
            CHROMIUM_CUSTOM_ARGS="--remote-debugging-port=9222 --remote-debugging-address=127.0.0.1",
            WEB_LOCALHOST_ONLY="1", VNC_LISTENING_PORT="-1", WEB_HOST_CLIPBOARD_SYNC="0",
            WEB_FILE_MANAGER="0", WEB_TERMINAL="0", WEB_AUDIO="0")
        argv = [docker, "create", "--pull=never", "--name", name, "--restart=no", "--shm-size=1g",
                "--label", "org.hypoloom.uid=" + str(os.geteuid()), "--label", "org.hypoloom.run=" + run_id,
                "--label", "org.hypoloom.unit=" + unit, "--volume", str(profile) + ":/config:rw"]
        for key, value in environment.items(): argv += ["--env", str(key) + "=" + str(value)]
        created = False
        try:
            identifier = command([*argv, config["image"]]).stdout.strip()
            created = True
            command([docker, "start", identifier])
            command([docker, "exec", identifier, "sh", "-c", "command -v nc >/dev/null"])
            print(json.dumps({"container": identifier}), flush=True)
            sys.stdin.read()  # OS-owned pipe closes even if the Host is SIGKILLed.
        finally:
            if created: cleanup(name, run_id, unit, docker)
    finally:
        os.close(descriptor)


class Container:
    def __init__(self, spec, run_id):
        self.spec, self.run_id = spec, run_id
        self.guard = None
        self.identifier = None
        self.closed = threading.Event()
        self.lock = threading.RLock()
        self.docker = shutil.which("docker")

    def start(self):
        with self.lock:
            if self.closed.is_set(): raise OSError("container_closed")
            if not self.docker: raise OSError("docker_unavailable")
            if self.identifier: return self.identifier
            from .institutional_runtime import private_profile
            profile = private_profile(self.spec["profile"])
            if ":" in str(profile): raise ValueError("container_profile_path_contains_colon")
            service = self.spec["service"]
            image = service["image"]
            if not isinstance(image, str) or not image or image.startswith("-"): raise ValueError("invalid_browser_image")
            if service.get("privileged") or service.get("network_mode"): raise ValueError("unsafe_browser_compose")
            name = f"hypoloom-container-{os.geteuid()}-" + secrets.token_hex(8)
            unit = name + ".service"
            script = str(Path(__file__).resolve())
            clean = [sys.executable, "-I", "-B", script, "cleanup", name, self.run_id, unit, self.docker]
            from .institutional_lifetime import PipeService
            self.guard = PipeService(unit, [sys.executable, "-I", "-B", script, "watch", name, self.run_id, unit, self.docker], clean)
            try:
                self.guard.process.stdin.write(json.dumps({"profile": str(profile), "image": image,
                    "environment": service.get("environment", {})}) + "\n")
                self.guard.process.stdin.flush()
                self.identifier = self.guard.ready(self.spec["ready_timeout"])["container"]
                if self.closed.is_set(): raise OSError("container_closed")
                return self.identifier
            except BaseException:
                self.close()
                raise

    @contextmanager
    def _dial(self, port):
        identifier = self.start()
        parent, child = socket.socketpair()
        process = None
        try:
            process = subprocess.Popen([self.docker, "exec", "--interactive", identifier, "nc", "127.0.0.1", str(port)],
                stdin=child, stdout=child, stderr=subprocess.DEVNULL)
            child.close(); parent.settimeout(5)
            yield parent
        finally:
            parent.close(); child.close()
            if process is not None:
                try: process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try: process.wait(timeout=2)
                    except subprocess.TimeoutExpired: process.kill(); process.wait()

    def cdp(self): return self._dial(9222)
    def gui(self): return self._dial(5800)

    def close(self):
        self.closed.set()
        with self.lock:
            self._close()

    def _close(self):
        if self.guard is not None:
            self.guard.close()
            self.guard = None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("watch", "cleanup"))
    parser.add_argument("name"); parser.add_argument("run_id"); parser.add_argument("unit")
    parser.add_argument("docker")
    values = parser.parse_args()
    try:
        (guardian if values.action == "watch" else cleanup)(values.name, values.run_id, values.unit, values.docker)
    except Exception:
        print("container_operation_failed", file=sys.stderr)
        sys.exit(2)
