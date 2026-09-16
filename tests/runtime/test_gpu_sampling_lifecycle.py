"""Runner lifecycle brackets for owned GPU samples; synthetic bytes, no CUDA."""

from types import SimpleNamespace

import pytest

from hypoloom.gpu_attribution import (
    AttributionUnavailable,
    GpuAttributionSampler,
    GpuProcessUsage,
)
from hypoloom.shared_gpu_host import SharedGpuHost
from test_gpu_attribution import GPU_A, MIB, metadata, sampler


@pytest.mark.parametrize("stage", ["initial", "recheck"])
@pytest.mark.parametrize("missing", [False, True])
def test_root_exit_is_distinguished_from_other_sampling_failures(stage, missing):
    records, reads = metadata(), 0

    def read(pid):
        nonlocal reads
        if pid == 100:
            reads += 1
            if reads == (1 if stage == "initial" else 2):
                if missing:
                    raise FileNotFoundError("exited root")
                raise AttributionUnavailable("process_exited")
        return records[pid]

    item = GpuAttributionSampler(
        process_provider=lambda uuid: [GpuProcessUsage(103, MIB)],
        metadata_reader=read,
    ).sample([GPU_A], {"owner": 100})["owner"][GPU_A]
    assert not item["complete"] and item["resident_bytes"] is None
    assert item["reason"] == "root_process_exited"


@pytest.mark.parametrize("pid,exception,reason", [
    (103, AttributionUnavailable("process_exited"), "process_exited"),
    (103, FileNotFoundError("exited child"), "process_exited"),
    (100, PermissionError("denied"), "root_unavailable"),
])
def test_child_exit_and_permission_errors_are_not_root_exit(pid, exception, reason):
    records = metadata()

    def read(current):
        if current == pid:
            raise exception
        return records[current]

    item = GpuAttributionSampler(
        process_provider=lambda uuid: [GpuProcessUsage(103, MIB)],
        metadata_reader=read,
    ).sample([GPU_A], {"owner": 100})["owner"][GPU_A]
    assert not item["complete"] and item["reason"] == reason


def test_provider_file_failure_is_not_reclassified_as_root_exit():
    records = metadata()

    def read(pid):
        if pid not in records:
            raise FileNotFoundError("exited root")
        return records[pid]

    def provider(uuid):
        records.pop(100)
        raise FileNotFoundError("provider unavailable")

    item = GpuAttributionSampler(
        process_provider=provider, metadata_reader=read,
    ).sample([GPU_A], {"owner": 100})["owner"][GPU_A]
    assert not item["complete"] and item["resident_bytes"] is None
    assert item["reason"] == "process_sample_unavailable"


def test_one_root_exit_does_not_label_a_different_pool_root_as_exited():
    records, reads = metadata(), 0

    def read(pid):
        nonlocal reads
        if pid == 200:
            reads += 1
            if reads == 2:
                raise AttributionUnavailable("process_exited")
        return records[pid]

    result = GpuAttributionSampler(
        process_provider=lambda uuid: [GpuProcessUsage(103, MIB), GpuProcessUsage(201, MIB)],
        metadata_reader=read,
    ).sample([GPU_A], {"first": 100, "second": 200})
    assert result["first"][GPU_A]["reason"] == "process_exited"
    assert result["second"][GPU_A]["reason"] == "root_process_exited"
    assert all(not item[GPU_A]["complete"] for item in result.values())


def _subject(tmp_path):
    subject = SharedGpuHost(None, tmp_path / "unused.sock", "sampling-boundary")
    handle = SimpleNamespace(returncode=None, process_group_id=100)
    complete = sampler([GpuProcessUsage(103, 2 * MIB)]).sample(
        [GPU_A], {"owner": 100}
    )["owner"][GPU_A]
    return subject, handle, complete, {GPU_A: SimpleNamespace(used_memory_mib=4)}


def test_already_terminal_runner_is_not_sampled_or_synthesized_as_zero(tmp_path, monkeypatch):
    subject, handle, _, devices = _subject(tmp_path)
    handle.returncode = 0

    def forbidden(*args):
        pytest.fail("terminal Runner must not enter NVML sampling")

    monkeypatch.setattr(GpuAttributionSampler, "sample", forbidden)
    subject.sample_owned("owner", handle, (GPU_A,), devices)
    assert subject.finish_owned("owner") == {}
    assert subject.owned_samples == {} and subject.sampled_at == {}


@pytest.mark.parametrize("reason,terminal,expected_unknown", [
    ("root_process_exited", True, 0),
    ("root_process_exited", False, 1),
    ("process_exited", True, 0),
    ("process_exited", False, 1),
    ("process_set_reduced", True, 0),
    ("process_set_reduced", False, 1),
    ("process_set_changed", True, 1),
    ("process_sample_unavailable", True, 1),
    ("root_unavailable", True, 1),
])
@pytest.mark.parametrize("prior_unknown", [False, True])
def test_only_exit_samples_confirmed_by_runner_end_window_without_erasing_prior_unknowns(
    tmp_path, monkeypatch, reason, terminal, expected_unknown, prior_unknown,
):
    subject, handle, complete, devices = _subject(tmp_path)
    unknown = {**complete, "complete": False, "reason": "memory_unavailable",
               "resident_bytes": None, "processes": []}
    items = [complete] + ([unknown] if prior_unknown else [])
    final = {**unknown, "reason": reason}
    items.append(final)
    calls = 0

    def sample(self, uuids, roots):
        nonlocal calls
        item = items[calls]
        calls += 1
        if item is final and terminal:
            handle.returncode = 0
        return {"owner": {GPU_A: item}}

    monkeypatch.setattr(GpuAttributionSampler, "sample", sample)
    for _ in items:
        subject.sampled_at.clear()
        subject.sample_owned("owner", handle, (GPU_A,), devices)
    summary = subject.finish_owned("owner")[GPU_A]
    assert calls == len(items)
    assert summary["unknown_sample_count"] == int(prior_unknown) + expected_unknown
    assert summary["sample_count"] == 1 + int(prior_unknown) + expected_unknown
    assert summary["peak"] == complete
    assert summary["latest"] == (final if expected_unknown else unknown if prior_unknown else complete)
