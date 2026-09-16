"""Real bwrap workers and synthetic NVML bytes; never a CUDA workload."""

import json
import os
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from hypoloom.gpu_attribution import (
    AttributionUnavailable,
    GpuAttributionSampler,
    GpuProcessUsage,
    read_process_metadata,
)
from hypoloom.runner import LocalRunner, ProcessRequest, process_group_exists
from hypoloom.shared_gpu_host import SharedGpuHost
from test_gpu_attribution import GPU_A, MIB


def _sandbox_code():
    worker = "import json,os,time; print(json.dumps({'role':ROLE,'inner_pid':os.getpid()}),flush=True); time.sleep(6)"
    thread = worker.replace("ROLE", repr("thread-worker"))
    session = worker.replace("ROLE", repr("session-worker"))
    orphan = (
        "import os; child=os.fork(); os._exit(0) if child else None; "
        + worker.replace("ROLE", repr("orphan-worker"))
    )
    return f"""
import json, os, subprocess, threading, time
from pathlib import Path
assert not list(Path('/dev').glob('nvidia*'))
def create_from_thread():
    subprocess.Popen(['/usr/bin/python3','-B','-c',{thread!r}])
    time.sleep(6)
threading.Thread(target=create_from_thread).start()
subprocess.Popen(['/usr/bin/python3','-B','-c',{session!r}], start_new_session=True)
subprocess.Popen(['/usr/bin/python3','-B','-c',{orphan!r}])
print(json.dumps({{'role':'application','inner_pid':os.getpid()}}),flush=True)
time.sleep(6)
"""


def _owned_tree(root):
    """Independent test inventory, only under this test's known Runner root.

    This walker is deliberately NOT used by the production attribution sampler.
    Tests wait for stationary workers; live traversal cannot certify completeness.
    """
    pending, records = [root], {}
    while pending:
        pid = pending.pop()
        if pid in records:
            continue
        directory = Path("/proc") / str(pid)
        try:
            status = dict(
                line.split(":", 1)
                for line in (directory / "status").read_text().splitlines()
                if ":" in line
            )
            fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
            children = {}
            for thread in (directory / "task").iterdir():
                children[int(thread.name)] = tuple(
                    map(int, (thread / "children").read_text().split())
                )
                pending.extend(children[int(thread.name)])
            records[pid] = {
                "pid": pid,
                "ppid": int(status["PPid"]),
                "pgid": int(fields[2]),
                "nspid": tuple(map(int, status["NSpid"].split())),
                "state": fields[0],
                "children": children,
            }
        except FileNotFoundError:
            continue
    return records


def _ready(handle, stdout):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        tree = _owned_tree(handle.pid)
        messages = [
            json.loads(line)
            for line in stdout.read_text().splitlines()
            if line.endswith("}")
        ]
        roles = {item["role"]: item["inner_pid"] for item in messages}
        mapped = {
            role: next(
                (
                    pid
                    for pid, row in tree.items()
                    if len(row["nspid"]) > 1 and row["nspid"][-1] == inner
                ),
                None,
            )
            for role, inner in roles.items()
        }
        reaper = next(
            (
                pid
                for pid, row in tree.items()
                if len(row["nspid"]) > 1 and row["nspid"][-1] == 1
            ),
            None,
        )
        if (
            len(mapped) == 4
            and all(mapped.values())
            and tree[mapped["orphan-worker"]]["ppid"] == reaper
        ):
            return tree, mapped
        assert handle.returncode is None, "owned CPU sandbox exited before sample"
        time.sleep(0.01)
    raise AssertionError("owned CPU workers did not become stationary")


def test_real_bwrap_ancestry_attributes_threads_sessions_orphans_and_independent_roots(
    tmp_path,
):
    bwrap = shutil.which("bwrap")
    assert bwrap is not None, "attribution acceptance requires actual bubblewrap"
    argv = [
        bwrap,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
    ]
    for source in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(source).exists():
            argv.extend(("--ro-bind", source, source))
    argv.extend(("/usr/bin/python3", "-B", "-c", _sandbox_code()))
    runner, handles, inventories = LocalRunner(), [], []
    try:
        for index in range(2):

            def register(pgid):
                assert pgid != os.getpgrp() and process_group_exists(pgid)

            handle = runner.start(
                ProcessRequest(
                    attempt_id=f"owned-attribution-{index}",
                    attempt_ordinal=1,
                    execution_id=f"cpu-{index}",
                    argv=tuple(argv),
                    cwd=str(tmp_path),
                    env={"PYTHONDONTWRITEBYTECODE": "1"},
                    timeout_seconds=8,
                    terminate_grace_seconds=0.1,
                    kill_grace_seconds=1,
                    stdout_path=f"{index}.stdout",
                    stderr_path=f"{index}.stderr",
                ),
                before_exec=register,
            )
            handles.append(handle)
            inventories.append(_ready(handle, tmp_path / f"{index}.stdout"))
        for handle, (tree, roles) in zip(handles, inventories, strict=True):
            application = tree[roles["application"]]
            assert application["nspid"][-1] == 2
            assert all(
                tree[pid]["pgid"] != handle.process_group_id for pid in roles.values()
            )
            assert tree[roles["session-worker"]]["pgid"] == roles["session-worker"]
            assert any(
                tid != application["pid"] and roles["thread-worker"] in children
                for tid, children in application["children"].items()
            )
        assert inventories[0][1]["application"] != inventories[1][1]["application"]

        # Values are explicitly synthetic; the real process tree supplies only
        # the ownership mapping. The other sandbox's large value must not leak.
        rows = [
            GpuProcessUsage(pid, (index + 1) * MIB)
            for index, pid in enumerate(inventories[0][1].values())
        ]
        rows += [GpuProcessUsage(pid, 100 * MIB) for pid in inventories[1][1].values()]
        sampler = GpuAttributionSampler(process_provider=lambda uuid: rows)
        result = sampler.sample(
            [GPU_A], {"first": handles[0].pid, "second": handles[1].pid}
        )
        assert result["first"][GPU_A]["complete"], result
        assert result["first"][GPU_A]["resident_bytes"] == 10 * MIB
        assert result["second"][GPU_A]["resident_bytes"] == 400 * MIB
        for index, key in enumerate(("first", "second")):
            evidence = result[key][GPU_A]
            assert {row["pid"] for row in evidence["processes"]} == set(
                inventories[index][1].values()
            )
            for process in evidence["processes"]:
                chain = process["ancestry"]
                assert (
                    chain[0]["pid"] == process["pid"]
                    and chain[-1]["pid"] == handles[index].pid
                )
                assert all(
                    child["ppid"] == parent["pid"]
                    for child, parent in zip(chain, chain[1:])
                )
    finally:
        for handle in handles:
            runner.cancel(handle)
            runner.wait(handle)
            assert not process_group_exists(handle.process_group_id)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            live = []
            for tree, _ in inventories:
                for pid in tree:
                    try:
                        state = (
                            (Path("/proc") / str(pid) / "stat")
                            .read_text()
                            .rsplit(")", 1)[1]
                            .split()[0]
                        )
                    except FileNotFoundError:
                        continue
                    if state != "Z":
                        live.append(pid)
            if not live:
                break
            time.sleep(0.01)
        assert not live, f"owned CPU worker residue: {live}"


@pytest.fixture
def exiting_cpu_sandbox(tmp_path):
    """A real held Runner/bwrap child, released only through this fixture's file."""
    bwrap = shutil.which("bwrap")
    assert bwrap is not None, "exit-boundary acceptance requires actual bubblewrap"
    argv = [
        bwrap,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--bind",
        str(tmp_path),
        "/test",
    ]
    for source in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(source).exists():
            argv.extend(("--ro-bind", source, source))
    code = """
import json, os, time
from pathlib import Path
assert not list(Path('/dev').glob('nvidia*'))
print(json.dumps({'inner_pid': os.getpid()}), flush=True)
deadline = time.monotonic() + 6
while not Path('/test/finish').exists() and time.monotonic() < deadline:
    time.sleep(0.01)
raise SystemExit(0 if Path('/test/finish').exists() else 124)
"""
    argv.extend(("/usr/bin/python3", "-B", "-c", code))
    runner = LocalRunner()

    def register(pgid):
        assert pgid != os.getpgrp() and process_group_exists(pgid)

    handle = runner.start(
        ProcessRequest(
            attempt_id="sampling-exit--process-1",
            attempt_ordinal=1,
            execution_id="sampling-exit",
            argv=tuple(argv),
            cwd=str(tmp_path),
            env={"PYTHONDONTWRITEBYTECODE": "1"},
            timeout_seconds=8,
            terminate_grace_seconds=0.1,
            kill_grace_seconds=1,
            stdout_path="exit.stdout",
            stderr_path="exit.stderr",
        ),
        before_exec=register,
    )
    owned_pids = {handle.pid, handle._guardian.pid}
    try:
        deadline = time.monotonic() + 3
        application = None
        while time.monotonic() < deadline:
            tree = _owned_tree(handle.pid)
            owned_pids.update(tree)
            lines = (tmp_path / "exit.stdout").read_text().splitlines()
            if lines:
                inner = json.loads(lines[0])["inner_pid"]
                application = next(
                    (
                        pid
                        for pid, item in tree.items()
                        if len(item["nspid"]) > 1 and item["nspid"][-1] == inner
                    ),
                    None,
                )
            if application is not None:
                break
            assert handle.returncode is None, "CPU sandbox exited before readiness"
            time.sleep(0.01)
        assert application is not None, "CPU sandbox never became ready"

        def finish(*, reap):
            (tmp_path / "finish").touch()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                # Do not poll Popen yet: exercise a real zombie root as well as
                # a reaped root, not invented process metadata or return codes.
                state = (Path("/proc") / str(handle.pid) / "stat").read_text()
                if state.rsplit(")", 1)[1].split()[0] == "Z":
                    break
                time.sleep(0.01)
            else:
                raise AssertionError("owned CPU sandbox did not exit on release")
            if reap:
                assert handle._process.wait(timeout=1) == 0

        yield SimpleNamespace(handle=handle, application=application, finish=finish)
    finally:
        if handle.returncode is None:
            runner.cancel(handle)
        runner.wait(handle)
        assert not process_group_exists(handle.process_group_id)
        assert all(not (Path("/proc") / str(pid)).exists() for pid in owned_pids)


@pytest.mark.parametrize("history", ["empty", "complete", "unknown"])
@pytest.mark.parametrize(
    "boundary",
    [
        "after_device_observation",
        "zombie_before_root_read",
        "reaped_before_root_read",
        "zombie_between_nvml_reads",
        "reaped_between_nvml_reads",
        "zombie_before_child_read",
        "reaped_before_child_read",
        "zombie_during_child_recheck",
        "reaped_during_child_recheck",
    ],
)
def test_real_runner_exit_boundary_does_not_invent_or_erase_owned_measurements(
    tmp_path,
    monkeypatch,
    exiting_cpu_sandbox,
    boundary,
    history,
):
    cpu = exiting_cpu_sandbox
    phase = {"exit": False, "calls": 0, "unknown": False, "triggered": False,
             "child_reads": 0}

    def finish():
        phase["triggered"] = True
        cpu.finish(reap=boundary.startswith("reaped"))

    def metadata(pid):
        if phase["exit"] and boundary.endswith("before_root_read"):
            assert pid == cpu.handle.process_group_id
            finish()
        if phase["exit"] and pid == cpu.application:
            phase["child_reads"] += 1
            if (boundary.endswith("before_child_read")
                    or boundary.endswith("during_child_recheck") and phase["child_reads"] == 2):
                finish()
        return read_process_metadata(pid)

    def nvml(uuid):
        assert uuid == GPU_A
        phase["calls"] += 1
        if phase["unknown"]:
            raise AttributionUnavailable("telemetry_unavailable")
        if (
            phase["exit"]
            and boundary.endswith("between_nvml_reads")
            and phase["calls"] == 2
        ):
            finish()
        # Synthetic bytes only. Actual numeric ancestry still comes from /proc.
        return (GpuProcessUsage(cpu.application, 4 * MIB),)

    sampler = GpuAttributionSampler(process_provider=nvml, metadata_reader=metadata)
    monkeypatch.setattr(
        "hypoloom.gpu_attribution.GpuAttributionSampler",
        lambda: sampler,
    )
    shared = SharedGpuHost(SimpleNamespace(), tmp_path / "unused.sock", "cpu-exit")
    attempt = cpu.handle.request.attempt_id
    devices = {GPU_A: SimpleNamespace(used_memory_mib=8)}
    if history != "empty":
        phase["unknown"] = history == "unknown"
        shared.sample_owned(attempt, cpu.handle, (GPU_A,), devices)
        prior = shared.owned_samples[attempt][GPU_A].summary
        assert prior["sample_count"] == 1
        assert prior["unknown_sample_count"] == (history == "unknown")
        if history == "complete":
            assert prior["peak"]["resident_bytes"] == 4 * MIB
            assert prior["peak"]["processes"][0]["pid"] == cpu.application
        else:
            assert prior["peak"] is None
    else:
        prior = None
    # Bypass only the 0.5-second cadence; the tested sample retains real clocks,
    # process polling, metadata, shutdown and NVML-read ordering.
    shared.sampled_at.pop(attempt, None)
    phase.update(exit=True, calls=0, unknown=False)
    if boundary == "after_device_observation":
        cpu.finish(reap=True)
    shared.sample_owned(attempt, cpu.handle, (GPU_A,), devices)
    assert cpu.handle.returncode == 0
    assert not process_group_exists(cpu.handle.process_group_id)
    if boundary == "after_device_observation":
        assert phase["calls"] == 0, "terminal Runner must not start a new NVML sample"
    else:
        assert phase["triggered"], "the real root must exit inside attribution"
    observed = shared.finish_owned(attempt)
    assert observed == ({} if prior is None else {GPU_A: prior})
