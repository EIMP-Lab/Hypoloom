"""Kernel messages must match the owned process, device and execution window."""
import copy
import errno

import pytest

from hypoloom import gpu_diagnostics as diagnostics


GPU = "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OTHER = "GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
PCI = "0000:3b:00.0"


def failed_process():
    return {"terminal_reason": "FAILED", "returncode": 1, "started_at": 100.0, "ended_at": 200.0,
        "resource_usage": {"gpu_uuid": GPU, "owned_memory": {
            "sample_count": 3, "unknown_sample_count": 1,
            "latest": {"complete": True, "observed_at": 1160.0,
                "processes": [{"pid": 42, "ancestry": [{"pid": 42, "start_time_ticks": 5000}]}]}, "peak": None}}}


def event(**changes):
    return {"pci_bus": PCI, "xid": 38, "pid": 42, "timestamp_monotonic": 150.0, **changes}


@pytest.fixture
def kernel(monkeypatch):
    sample = {"status": "complete", "reason": None, "first_timestamp": 0.0, "events": [event()]}
    monkeypatch.setattr(diagnostics, "gpu_pci_identities", lambda: {PCI: GPU, "0000:b1:00.0": OTHER})
    monkeypatch.setattr(diagnostics, "read_kernel_xids", lambda: copy.deepcopy(sample))
    monkeypatch.setattr(diagnostics, "_clock_offset", lambda: 1000.0)
    return sample


def test_owned_xid_38_is_preserved_but_unrelated_messages_are_not(kernel):
    kernel["events"] += [event(pid=99), event(pci_bus="0000:b1:00.0"),
        event(timestamp_monotonic=99), event(timestamp_monotonic=201)]
    payload = failed_process()
    payload["gpu_diagnostic"] = diagnostics.diagnose_gpu_failure(payload)
    assert payload["gpu_diagnostic"]["events"] == [{**event(), "gpu_uuid": GPU}]
    assert diagnostics.confirmed_gpu_fault(payload)
    diagnostics.validate_gpu_diagnostic(payload, (GPU,))


@pytest.mark.parametrize("xid", [13, 31, 43, 79])
def test_other_xids_do_not_receive_the_xid_38_recovery_policy(kernel, xid):
    kernel["events"] = [event(xid=xid)]
    payload = failed_process()
    payload["gpu_diagnostic"] = diagnostics.diagnose_gpu_failure(payload)
    assert len(payload["gpu_diagnostic"]["events"]) == 1
    assert not diagnostics.confirmed_gpu_fault(payload)


@pytest.mark.parametrize("changes", [{"terminal_reason": "SUCCEEDED"}, {"synthetic_before_spawn": True},
    {"resource_pressure": {"reason": "pressure"}}, {"resource_failure": "gpu_out_of_memory"}])
def test_device_recovery_cannot_relabel_success_synthetic_oom_or_pressure(kernel, changes):
    assert diagnostics.diagnose_gpu_failure({**failed_process(), **changes}) is None


def test_missing_ownership_stays_unknown_and_does_not_match_another_process(kernel):
    payload = failed_process()
    payload["resource_usage"]["owned_memory"]["latest"] = None
    payload["gpu_diagnostic"] = diagnostics.diagnose_gpu_failure(payload)
    assert payload["gpu_diagnostic"]["status"] == "partial"
    assert payload["gpu_diagnostic"]["reason"] == "ownership_unavailable"
    assert payload["gpu_diagnostic"]["events"] == []
    assert not diagnostics.confirmed_gpu_fault(payload)


@pytest.mark.parametrize("change", ["born_after_event", "last_seen_before_event"])
def test_a_reused_or_no_longer_observed_pid_cannot_earn_device_recovery(kernel, change):
    payload = failed_process()
    sample = payload["resource_usage"]["owned_memory"]["latest"]
    if change == "born_after_event":
        sample["processes"][0]["ancestry"][0]["start_time_ticks"] = 15500
    else:
        sample["observed_at"] = 1140.0
    payload["gpu_diagnostic"] = diagnostics.diagnose_gpu_failure(payload)
    assert payload["gpu_diagnostic"]["events"] == []
    assert not diagnostics.confirmed_gpu_fault(payload)


def test_kernel_rotation_does_not_turn_absence_into_a_clean_bill_of_health(kernel):
    kernel.update(first_timestamp=160, events=[])
    result = diagnostics.diagnose_gpu_failure(failed_process())
    assert result["status"] == "partial" and result["reason"] == "window_not_covered"


@pytest.mark.parametrize("changes", [{"pid": 99}, {"gpu_uuid": OTHER}, {"timestamp_monotonic": 99},
    {"timestamp_monotonic": float("nan")}, {"xid": True}, {"pci_bus": "invalid"}])
def test_diagnostic_validator_rejects_unbound_or_malformed_events(kernel, changes):
    payload = failed_process()
    payload["gpu_diagnostic"] = diagnostics.diagnose_gpu_failure(payload)
    payload["gpu_diagnostic"]["events"][0].update(changes)
    with pytest.raises(ValueError):
        diagnostics.validate_gpu_diagnostic(payload, (GPU,))
    assert not diagnostics.confirmed_gpu_fault(payload)


def test_kernel_reader_is_nonblocking_bounded_and_drops_raw_messages(monkeypatch):
    records = iter([b"6,0,0,-;boot\n", b"3,1,150000000,-;NVRM: Xid (PCI:0000:3b:00): 38, pid=42, name=python private text\n",
        b"6,3,151000000,-;another device message\n"])
    def opened(path, flags):
        assert path == "/dev/kmsg" and flags & diagnostics.os.O_NONBLOCK
        return 123
    def read(fd, size):
        assert fd == 123 and size == 65536
        try:
            return next(records)
        except StopIteration:
            raise BlockingIOError()
    closed = []
    monkeypatch.setattr(diagnostics.os, "open", opened)
    monkeypatch.setattr(diagnostics.os, "read", read)
    monkeypatch.setattr(diagnostics.os, "close", closed.append)
    sample = diagnostics.read_kernel_xids()
    assert sample["events"] == [event()]
    assert sample["status"] == "partial" and sample["reason"] == "sequence_gap"
    assert closed == [123] and "private text" not in str(sample)


def test_unreadable_kernel_log_is_explicitly_unavailable(monkeypatch):
    def denied(*args):
        raise PermissionError(errno.EACCES, "denied")
    monkeypatch.setattr(diagnostics.os, "open", denied)
    result = diagnostics.read_kernel_xids()
    assert result["status"] == "unavailable" and result["reason"] == "permission_denied"
    assert result["events"] == []


def test_pci_mapping_uses_physical_uuid_and_normalizes_domain(tmp_path):
    directory = tmp_path / "00000000:3B:00.0"
    directory.mkdir()
    (directory / "information").write_text("Model: test GPU\nGPU UUID: " + GPU + "\n")
    assert diagnostics.gpu_pci_identities(tmp_path) == {PCI: GPU}
