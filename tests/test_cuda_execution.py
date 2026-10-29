"""GPU grants must initialize in containment before any candidate can run."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import uuid

import pytest

from hypoloom import cuda_preflight
from hypoloom.execution_workspace import ExecutionWorkspace, SandboxUnavailable
from hypoloom.domain import AttemptTerminalReason
from hypoloom.measurement_driver import RAW_EXECUTION_FAILED, sequence_request
from hypoloom.oracle import _canonical_bwrap_argv
from hypoloom.runner import LocalRunner, ProcessRequest, process_group_exists


UUIDS = (
    "GPU-11111111-2222-3333-4444-555555555555",
    "GPU-66666666-7777-8888-9999-aaaaaaaaaaaa",
)


class DriverFunction:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


class Driver:
    def __init__(self, identities=UUIDS, failure=None):
        self.identities = identities
        self.failure = failure
        self.calls = []

    def __getattr__(self, name):
        def invoke(*args):
            self.calls.append(name)
            if self.failure and name == self.failure[0]:
                return self.failure[1]
            if name == "cuDeviceGetCount":
                args[0]._obj.value = len(self.identities)
            elif name == "cuDeviceGet":
                args[0]._obj.value = args[1]
            elif name == "cuDeviceGetUuid":
                identity = uuid.UUID(self.identities[args[1].value][4:]).bytes
                ctypes.memmove(args[0], identity, 16)
            elif name == "cuDevicePrimaryCtxRetain":
                args[0]._obj.value = 123
            elif name == "cuMemAlloc_v2":
                args[0]._obj.value = 4096
            return 0
        return DriverFunction(invoke)


@pytest.mark.parametrize("grant", ["", "0", UUIDS[0] + "," + UUIDS[0], "GPU-invalid"])
def test_invalid_grant_never_loads_driver(monkeypatch, grant):
    monkeypatch.setattr(ctypes, "CDLL", lambda _: pytest.fail("invalid grant loaded CUDA"))
    with pytest.raises(cuda_preflight.CudaPreflightError):
        cuda_preflight.check_cuda(grant)


def test_every_granted_device_is_checked_and_released(monkeypatch):
    driver = Driver()
    monkeypatch.setattr(ctypes, "CDLL", lambda _: driver)
    assert cuda_preflight.check_cuda(",".join(UUIDS)) == UUIDS
    for operation in ("cuDeviceGetUuid", "cuDevicePrimaryCtxRetain", "cuMemsetD8_v2",
                      "cuCtxSynchronize", "cuMemFree_v2", "cuDevicePrimaryCtxRelease_v2"):
        assert driver.calls.count(operation) == 2


@pytest.mark.parametrize("failure", [("cuInit", 304), ("cuDevicePrimaryCtxRetain", 999),
                                      ("cuMemAlloc_v2", 2), ("cuCtxSynchronize", 700)])
def test_cuda_failures_are_errors_not_cpu_fallback(monkeypatch, failure):
    driver = Driver((UUIDS[0],), failure)
    monkeypatch.setattr(ctypes, "CDLL", lambda _: driver)
    with pytest.raises(cuda_preflight.CudaPreflightError, match=str(failure[1])):
        cuda_preflight.check_cuda(UUIDS[0])
    if failure[0] in ("cuMemAlloc_v2", "cuCtxSynchronize"):
        assert "cuDevicePrimaryCtxRelease_v2" in driver.calls
    if failure[0] == "cuCtxSynchronize":
        assert "cuMemFree_v2" in driver.calls


@pytest.mark.parametrize("identities", [(), UUIDS, (UUIDS[1],)])
def test_missing_extra_or_wrong_device_is_rejected(monkeypatch, identities):
    driver = Driver(identities)
    monkeypatch.setattr(ctypes, "CDLL", lambda _: driver)
    with pytest.raises(cuda_preflight.CudaPreflightError):
        cuda_preflight.check_cuda(UUIDS[0])
    assert "cuDevicePrimaryCtxRetain" not in driver.calls


def test_failed_preflight_never_executes_candidate(tmp_path):
    marker = tmp_path / "candidate-started"
    candidate = [sys.executable, "-c", "from pathlib import Path; Path(__import__('sys').argv[1]).touch()",
                 str(marker)]
    result = subprocess.run([*cuda_preflight.launch_prefix(sys.executable), *candidate],
                            env={**os.environ, "CUDA_VISIBLE_DEVICES": ""},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == cuda_preflight.CUDA_PREFLIGHT_FAILED
    assert "HYPOLOOM_CUDA_UNAVAILABLE" in result.stderr
    assert not marker.exists()


def test_preflight_watchdog_never_executes_candidate(monkeypatch, capsys):
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 30
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
    monkeypatch.setattr(subprocess, "run", timeout)
    monkeypatch.setattr(os, "execvpe", lambda *args: pytest.fail("timed-out CUDA started workload"))
    assert cuda_preflight.main(["/runtime/bin/python", "experiment.py"]) == 78
    assert "CPU fallback is forbidden" in capsys.readouterr().err


def test_measurement_failure_skips_parser_and_cleans_process_group(tmp_path):
    marker = tmp_path / "unexpected-work"
    command = (sys.executable, "-c", "from pathlib import Path; Path(__import__('sys').argv[1]).touch()",
               str(marker))
    common = dict(attempt_ordinal=1, execution_id="execution", cwd=str(tmp_path), timeout_seconds=10)
    raw = ProcessRequest(attempt_id="raw", argv=(*cuda_preflight.launch_prefix(sys.executable), *command),
                         env={"CUDA_VISIBLE_DEVICES": ""}, stdout_path="raw-stdout.log",
                         stderr_path="raw-stderr.log", **common)
    parser = ProcessRequest(attempt_id="parser", argv=command, env={},
                            stdout_path="parser-stdout.log", stderr_path="parser-stderr.log", **common)
    request = sequence_request(raw, parser, attempt_id="outer", **common)
    observation = LocalRunner().run(request)
    assert observation.terminal_reason is AttemptTerminalReason.FAILED
    assert observation.returncode == RAW_EXECUTION_FAILED
    assert "HYPOLOOM_CUDA_UNAVAILABLE" in (tmp_path / "stderr.log").read_text()
    assert not marker.exists()
    assert not (tmp_path / "parser-stdout.log").exists()
    assert not process_group_exists(observation.process_group_id)


def test_success_preserves_original_workload_argv(monkeypatch):
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(os, "execvpe", lambda *args: called.append(args))
    command = ["/usr/bin/bash", "/input/run.sh", "argument with spaces", "--device", "cuda"]
    assert cuda_preflight.main(command) == 0
    assert called == [(command[0], command, dict(os.environ))]


def workspace(tmp_path, *, gpu=True, count=1, runtime=None):
    root = tmp_path / "state"
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True)
    bindings = (tuple((f"/dev/nvidia{i}", f"/dev/nvidia{i}") for i in range(count))
                + (("/dev/nvidiactl", "/dev/nvidiactl"), ("/dev/nvidia-uvm", "/dev/nvidia-uvm"))
                if gpu else ())
    prefix = Path(runtime or sys.prefix)
    instance = ExecutionWorkspace(artifacts, state_root=root, runtime_prefix=prefix,
        python_executable=prefix / "bin/python", device_bindings=bindings,
        system_runtime_paths=("/lib", "/lib64", "/usr/lib", "/etc/ld.so.cache", "/usr/bin", "/bin"))
    paths = instance.paths("trial")
    for path in (paths.input_dir, paths.output_dir, paths.log_dir):
        path.mkdir(parents=True)
    environment = {"HOME": "/nonexistent", "PATH": "/runtime/bin:/usr/bin:/bin",
                   "CUDA_VISIBLE_DEVICES": ",".join(UUIDS[:count]) if gpu else "",
                   "CUDA_DEVICE_ORDER": "PCI_BUS_ID", "PYTHONDONTWRITEBYTECODE": "1"}
    return instance, paths, bindings, environment


@pytest.mark.parametrize("gpu,count", [(False, 0), (True, 1), (True, 2)])
@pytest.mark.parametrize("command", [("python", "experiment.py", "argument"),
                                    ("/usr/bin/bash", "/input/run.sh")])
def test_workspace_and_oracle_enforce_scoped_gpu_launch(tmp_path, gpu, count, command):
    instance, paths, bindings, environment = workspace(tmp_path, gpu=gpu, count=count)
    argv = list(instance._sandbox_argv(paths, "experiment.py", command, environment, ()))
    kwargs = dict(attempt_id="trial", source_relative_path="experiment.py", execution_argv=command,
                  expected_bindings=bindings, expected_environment=environment, allow_generic=True,
                  cuda_preflight_required=gpu)
    assert _canonical_bwrap_argv(argv, **kwargs)
    if not gpu:
        assert not any(word.startswith("/sys") for word in argv)
        assert "-I" not in argv
        return
    for path in cuda_preflight.NVIDIA_SYSFS_FILES:
        damaged = argv.copy()
        index = damaged.index(path) - 1
        del damaged[index:index + 3]
        assert not _canonical_bwrap_argv(damaged, **kwargs)
    damaged = argv.copy()
    index = damaged.index("--") + 1
    del damaged[index:index + 5]  # The candidate cannot omit the launcher.
    assert not _canonical_bwrap_argv(damaged, **kwargs)
    damaged = argv.copy()
    damaged[index + 4] += "\n# modified trusted check\n"
    assert not _canonical_bwrap_argv(damaged, **kwargs)
    damaged = argv.copy()
    damaged[damaged.index("--tmpfs"):damaged.index("--tmpfs")] = ["--ro-bind", "/sys", "/sys"]
    assert not _canonical_bwrap_argv(damaged, **kwargs)


def test_legacy_gpu_requests_remain_auditable(tmp_path):
    instance, paths, bindings, environment = workspace(tmp_path)
    command = ("python", "experiment.py")
    argv = list(instance._sandbox_argv(paths, "experiment.py", command, environment, ()))
    index = argv.index("--") + 1
    del argv[index:index + 5]
    for path in cuda_preflight.NVIDIA_SYSFS_FILES:
        index = argv.index(path) - 1
        del argv[index:index + 3]
    kwargs = dict(attempt_id="trial", source_relative_path="experiment.py", execution_argv=command,
                  expected_bindings=bindings, expected_environment=environment, allow_generic=True)
    assert _canonical_bwrap_argv(argv, **kwargs)
    assert not _canonical_bwrap_argv(argv, **kwargs, cuda_preflight_required=True)


def test_missing_module_state_fails_before_launch(tmp_path, monkeypatch):
    instance, *_ = workspace(tmp_path)
    monkeypatch.setattr("hypoloom.execution_workspace._is_character_device", lambda _: True)
    original = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda path: False if str(path) in cuda_preflight.NVIDIA_SYSFS_FILES
                        else original(path))
    with pytest.raises(SandboxUnavailable, match="module state"):
        instance._check_sandbox()


@pytest.mark.skipif(not os.environ.get("HYPOLOOM_TEST_CUDA_RUNTIME")
                    or not os.environ.get("HYPOLOOM_TEST_CUDA_UUIDS"),
                    reason="requires an explicitly selected CUDA runtime and GPU UUIDs")
def test_real_cuda_sandboxes(tmp_path):
    runtime = os.environ["HYPOLOOM_TEST_CUDA_RUNTIME"]
    selected = os.environ["HYPOLOOM_TEST_CUDA_UUIDS"].split(",")
    inventory = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=uuid,index", "--format=csv,noheader"], text=True)
    indices = {parts[0].strip(): int(parts[1]) for line in inventory.splitlines()
               if len(parts := line.split(",")) == 2}
    probe = '''import os,torch,json
assert torch.cuda.is_available()
expected=os.environ["CUDA_VISIBLE_DEVICES"].split(",")
assert torch.cuda.device_count() == len(expected)
for index,identity in enumerate(expected):
    device=f"cuda:{index}"
    assert "GPU-"+str(torch.cuda.get_device_properties(index).uuid) == identity
    m=torch.nn.Linear(8,3).to(device); o=torch.optim.Adam(m.parameters())
    x=torch.randn(4,8,device=device); m(x).square().mean().backward(); o.step()
    torch.save(m.state_dict(),"/tmp/model.pt")
    n=torch.nn.Linear(8,3).to(device); n.load_state_dict(torch.load("/tmp/model.pt",weights_only=True))
    torch.cuda.synchronize(index); assert torch.allclose(m(x),n(x))
assert not os.path.exists("/sys/devices")
devices=sorted(name for name in os.listdir("/dev") if name.startswith("nvidia") and name[6:].isdigit())
assert len(devices) == len(expected)
print(json.dumps({"uuids":expected,"device":str(x.device)}))
'''
    groups = [(value,) for value in selected]
    if len(selected) > 1:
        groups.append(tuple(sorted(selected)))
    for ordinal, group in enumerate(groups):
        instance, paths, _, environment = workspace(tmp_path / str(ordinal), runtime=runtime)
        devices = [f"/dev/nvidia{indices[value]}" for value in group]
        instance.device_bindings = tuple((Path(device), device) for device in devices) + (
            (Path("/dev/nvidiactl"), "/dev/nvidiactl"), (Path("/dev/nvidia-uvm"), "/dev/nvidia-uvm"))
        instance._check_sandbox()
        environment["CUDA_VISIBLE_DEVICES"] = ",".join(group)
        argv = instance._sandbox_argv(paths, "experiment.py", ("python", "-c", probe), environment, ())
        result = subprocess.run(argv, env={}, capture_output=True, text=True, timeout=45)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["uuids"] == list(group)
        assert "CUDA preflight: ready" in result.stderr
