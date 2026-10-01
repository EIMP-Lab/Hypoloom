"""EOF-owned user service, reusing systemd ownership instead of PID files."""
from __future__ import annotations

import json
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile


class PipeService:
    def __init__(self, unit, argv, cleanup):
        self.unit = unit
        stop = " ".join(json.dumps(v, ensure_ascii=False).replace("%", "%%") for v in cleanup)
        self.process = subprocess.Popen(["systemd-run", "--user", "--pipe", "--wait", "--quiet", "--collect",
            "--unit=" + unit, "--property=TimeoutStopSec=30s", "--property=ExecStopPost=" + stop,
            "--", *argv], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

    def ready(self, timeout):
        ready, _, _ = select.select([self.process.stdout], [], [], timeout)
        if not ready: raise TimeoutError("connection_start_timeout")
        return json.loads(self.process.stdout.readline())

    def close(self):
        process, self.process = self.process, None
        if process is None: return
        try:
            try: process.stdin.close()
            except (OSError, ValueError): pass
            try: process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                subprocess.run(["systemctl", "--user", "stop", self.unit], capture_output=True, timeout=40, check=True)
                process.wait(timeout=5)
            if process.returncode: raise OSError("connection_service_failed")
        finally: process.stdout.close()


class TransportDirectory:
    def __init__(self, timeout):
        self.path = Path(tempfile.mkdtemp(prefix="hypoloom-cdp-"))
        self.service = None
        command = [sys.executable, "-I", "-B", str(Path(__file__).resolve())]
        try:
            self.service = PipeService(self.path.name + ".service", [*command, "watch", str(self.path)],
                [*command, "cleanup", str(self.path)])
            if self.service.ready(timeout) != {"ready": True}: raise ValueError("connection_start_failed")
        except BaseException:
            self.close()
            raise

    def close(self):
        try:
            if self.service: self.service.close()
        finally:
            # Cleanup the known allocation if the user manager never started it.
            if self.path.exists(): shutil.rmtree(self.path)


if __name__ == "__main__":
    action, path = sys.argv[1:]
    if action == "watch":
        try:
            print(json.dumps({"ready": True}), flush=True)
            sys.stdin.read()
        finally:
            if Path(path).exists(): shutil.rmtree(path)
    elif action == "cleanup":
        if Path(path).exists(): shutil.rmtree(path)
    else: raise ValueError("invalid action")
