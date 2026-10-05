from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.compute_resources import (
    COMPUTE_CAPABILITIES_MEDIA_TYPE,
    COMPUTE_CAPABILITIES_SCHEMA_VERSION,
    ComputeCapabilities,
    GpuCapability,
    GpuPressureTracker,
    GpuRuntimeObservation,
    LocalComputeResources,
    ResourceCapacityUnavailable,
    ResourceUnavailable,
    gpu_pressure_attribution,
    gpu_pressure_payload,
)
from hypoloom.domain import ResourceGrant, ResourceKind, ResourcePreference


GPU_A = GpuCapability(
    index=2,
    uuid="GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    name="NVIDIA Test A",
    total_memory_mib=24_576,
    free_memory_mib=12_288,
    driver_version="570.133.07",
)
GPU_B = GpuCapability(
    index=0,
    uuid="GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    name="NVIDIA Test B",
    total_memory_mib=49_152,
    free_memory_mib=32_768,
    driver_version="570.133.07",
)


def capabilities(*gpus: GpuCapability) -> ComputeCapabilities:
    return ComputeCapabilities(gpus=tuple(sorted(gpus, key=lambda item: item.uuid)))


def gpu_runtime(tmp_path: Path) -> Path:
    prefix = tmp_path / "gpu-runtime"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / "python").write_bytes(b"python")
    (prefix / "conda-meta").mkdir()
    (prefix / "conda-meta" / "history").write_text("torch install\n", encoding="utf-8")
    (prefix / "conda-meta" / "torch.json").write_text("{}\n", encoding="utf-8")
    site_packages = prefix / "lib" / "python3.12" / "site-packages"
    (site_packages / "torch").mkdir(parents=True)
    (site_packages / "torch" / "version.py").write_text(
        "__version__ = '2.8.0+cu128'\n", encoding="utf-8"
    )
    for distribution in (
        "nvidia_cuda_runtime_cu12-12.8.dist-info",
        "torch-2.8.0+cu128.dist-info",
        "triton-3.4.0.dist-info",
    ):
        root = site_packages / distribution
        root.mkdir()
        root.joinpath("METADATA").write_text(f"Name: {distribution}\n", encoding="utf-8")
        root.joinpath("RECORD").write_text("package.so,sha256=value,1\n", encoding="utf-8")
    return prefix


def test_capability_contract_is_strict_sorted_and_canonical() -> None:
    snapshot = capabilities(GPU_B, GPU_A)

    assert ComputeCapabilities.from_dict(snapshot.to_dict()) == snapshot
    assert COMPUTE_CAPABILITIES_MEDIA_TYPE == "application/json"
    assert COMPUTE_CAPABILITIES_SCHEMA_VERSION == 3
    assert snapshot.canonical_bytes() == snapshot.to_bytes() == (
        b'{"busy_gpu_uuids":[],"gpus":[{"driver_version":"570.133.07","free_memory_mib":12288,'
        b'"index":2,'
        b'"name":"NVIDIA Test A","total_memory_mib":24576,'
        b'"uuid":"GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"},'
        b'{"driver_version":"570.133.07","free_memory_mib":32768,'
        b'"index":0,'
        b'"name":"NVIDIA Test B","total_memory_mib":49152,'
        b'"uuid":"GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}],'
        b'"schema_version":3}'
    )
    assert ComputeCapabilities.from_bytes(snapshot.to_bytes()) == snapshot
    assert set(snapshot.to_dict()) == {"schema_version", "gpus", "busy_gpu_uuids"}
    assert all(
        set(gpu)
        == {
            "index",
            "uuid",
            "name",
            "total_memory_mib",
            "free_memory_mib",
            "driver_version",
        }
        for gpu in snapshot.to_dict()["gpus"]
    )

    with pytest.raises(ValueError, match="sorted"):
        ComputeCapabilities(gpus=(GPU_B, GPU_A))
    with pytest.raises(ValueError, match="unexpected"):
        ComputeCapabilities.from_dict({**snapshot.to_dict(), "free_memory_mib": 1})
    with pytest.raises(ValueError, match="schema_version must be 3"):
        ComputeCapabilities(gpus=(), schema_version=1)
    with pytest.raises(ValueError, match="schema_version must be 3"):
        ComputeCapabilities(gpus=(), schema_version=2.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="sorted and unique"):
        ComputeCapabilities(gpus=(GPU_A, GPU_B), busy_gpu_uuids=(GPU_B.uuid, GPU_A.uuid))
    with pytest.raises(ValueError, match="sorted and unique"):
        ComputeCapabilities(gpus=(GPU_A,), busy_gpu_uuids=(GPU_A.uuid, GPU_A.uuid))
    with pytest.raises(ValueError, match="subset"):
        ComputeCapabilities(gpus=(GPU_A,), busy_gpu_uuids=(GPU_B.uuid,))
    malformed = snapshot.to_dict()
    malformed["gpus"][0]["utilization"] = 0
    with pytest.raises(ValueError, match="unexpected"):
        ComputeCapabilities.from_dict(malformed)
    with pytest.raises(ValueError, match="canonical"):
        ComputeCapabilities.from_bytes(b'{"busy_gpu_uuids": [], "gpus": [], "schema_version": 3}')
    with pytest.raises(ValueError, match="duplicate"):
        ComputeCapabilities.from_bytes(
            b'{"busy_gpu_uuids":[],"gpus":[],"schema_version":3,"schema_version":3}'
        )


def test_capability_free_memory_is_required_and_bounded() -> None:
    raw = GPU_A.to_dict()
    del raw["free_memory_mib"]
    with pytest.raises(ValueError, match="missing"):
        GpuCapability.from_dict(raw)
    with pytest.raises(ValueError, match="between zero and total"):
        replace(GPU_A, free_memory_mib=-1)
    with pytest.raises(ValueError, match="between zero and total"):
        replace(GPU_A, free_memory_mib=GPU_A.total_memory_mib + 1)


def test_cpu_only_never_calls_inventory_and_auto_falls_back_to_cpu() -> None:
    calls = 0

    def forbidden_inventory() -> ComputeCapabilities:
        nonlocal calls
        calls += 1
        raise AssertionError("CPU-only configuration must not inspect GPUs")

    resources = LocalComputeResources(inventory_provider=forbidden_inventory)
    snapshot = resources.snapshot()

    assert snapshot == ComputeCapabilities(gpus=())
    assert resources.select(ResourcePreference.CPU, snapshot) == ResourceGrant(
        kind=ResourceKind.CPU,
        gpu_uuid=None,
    )
    assert resources.select(ResourcePreference.AUTO, snapshot) == ResourceGrant(
        kind=ResourceKind.CPU,
        gpu_uuid=None,
    )
    resources.validate_live(ResourceGrant(kind=ResourceKind.CPU, gpu_uuid=None), snapshot)
    assert resources.workspace_kwargs(
        ResourceGrant(kind=ResourceKind.CPU, gpu_uuid=None), snapshot
    ) == {
        "device_bindings": (),
        "fixed_environment": {"CUDA_VISIBLE_DEVICES": ""},
    }
    assert resources.configuration == {
        "compute_resources": "local-compute/8",
        "gpu_device_uuids": [],
        "gpu_runtime_prefix": None,
    }
    assert resources.runtime_files == ()
    assert calls == 0


def test_configured_snapshot_filters_allowlist_and_selects_stable_uuid(tmp_path: Path) -> None:
    runtime = gpu_runtime(tmp_path)
    calls = 0

    def inventory() -> ComputeCapabilities:
        nonlocal calls
        calls += 1
        return capabilities(GPU_A, GPU_B)

    resources = LocalComputeResources(
        allowed_gpu_uuids=(GPU_B.uuid, GPU_A.uuid),
        gpu_runtime_prefix=runtime,
        inventory_provider=inventory,
    )
    snapshot = resources.snapshot()

    assert snapshot.gpus == (GPU_A, GPU_B)
    assert resources.select(ResourcePreference.AUTO, snapshot) == ResourceGrant(
        kind=ResourceKind.GPU,
        gpu_uuid=GPU_B.uuid,
    )
    assert resources.select(ResourcePreference.GPU_REQUIRED, snapshot) == ResourceGrant(
        kind=ResourceKind.GPU,
        gpu_uuid=GPU_B.uuid,
    )
    assert calls == 1

    missing = LocalComputeResources(
        allowed_gpu_uuids=("GPU-cccccccc-cccc-cccc-cccc-cccccccccccc",),
        gpu_runtime_prefix=runtime,
        inventory_provider=inventory,
    )
    empty = missing.snapshot()
    assert missing.select(ResourcePreference.AUTO, empty).kind is ResourceKind.CPU
    with pytest.raises(ResourceUnavailable, match="GPU_REQUIRED"):
        missing.select(ResourcePreference.GPU_REQUIRED, empty)


def test_select_filters_busy_and_insufficient_free_memory(tmp_path: Path) -> None:
    resources = LocalComputeResources(
        allowed_gpu_uuids=(GPU_B.uuid, GPU_A.uuid),
        gpu_runtime_prefix=gpu_runtime(tmp_path),
        inventory_provider=lambda: capabilities(GPU_A, GPU_B),
    )
    snapshot = capabilities(GPU_A, GPU_B)

    assert resources.select(
        ResourcePreference.GPU_REQUIRED,
        snapshot,
        min_vram_per_experiment=10_240,
        gpu_vram_reserve_mb=2_048,
    ).gpu_uuid == GPU_B.uuid
    assert resources.select(
        ResourcePreference.GPU_REQUIRED,
        snapshot,
        busy_gpu_uuids={GPU_A.uuid},
    ).gpu_uuid == GPU_B.uuid
    assert resources.select(
        ResourcePreference.GPU_REQUIRED,
        snapshot,
        min_vram_per_experiment=16_384,
        gpu_vram_reserve_mb=2_048,
    ).gpu_uuid == GPU_B.uuid

    unavailable = {
        "busy_gpu_uuids": {GPU_B.uuid},
        "min_vram_per_experiment": 16_384,
        "gpu_vram_reserve_mb": 2_048,
    }
    assert resources.select(
        ResourcePreference.GPU_REQUIRED,
        snapshot,
        **unavailable,
    ).gpu_uuid == GPU_A.uuid
    assert resources.select(ResourcePreference.AUTO, snapshot, **unavailable).kind is (
        ResourceKind.CPU
    )

    all_busy = {GPU_A.uuid, GPU_B.uuid}
    assert resources.select(
        ResourcePreference.GPU_REQUIRED,
        snapshot,
        busy_gpu_uuids=all_busy,
    ).gpu_uuid == GPU_B.uuid
    assert resources.select(
        ResourcePreference.AUTO,
        snapshot,
        busy_gpu_uuids=all_busy,
    ).kind is ResourceKind.CPU


def test_request_bound_busy_devices_drive_preference_specific_selection(
    tmp_path: Path,
) -> None:
    resources = LocalComputeResources(
        allowed_gpu_uuids=(GPU_B.uuid, GPU_A.uuid),
        gpu_runtime_prefix=gpu_runtime(tmp_path),
        inventory_provider=lambda: capabilities(GPU_A, GPU_B),
    )
    one_busy = ComputeCapabilities(
        gpus=(GPU_A, GPU_B),
        busy_gpu_uuids=(GPU_B.uuid,),
    )
    all_busy = ComputeCapabilities(
        gpus=(GPU_A, GPU_B),
        busy_gpu_uuids=(GPU_A.uuid, GPU_B.uuid),
    )

    assert resources.select(ResourcePreference.GPU_REQUIRED, one_busy).gpu_uuid == GPU_A.uuid
    assert resources.select(ResourcePreference.AUTO, one_busy).gpu_uuid == GPU_A.uuid
    assert resources.select(ResourcePreference.GPU_REQUIRED, all_busy).gpu_uuid == GPU_B.uuid
    assert resources.select(ResourcePreference.AUTO, all_busy).kind is ResourceKind.CPU


def test_selection_uses_uuid_only_as_equal_free_memory_tie_break(tmp_path: Path) -> None:
    equal_free_b = replace(GPU_B, free_memory_mib=GPU_A.free_memory_mib)
    resources = LocalComputeResources(
        allowed_gpu_uuids=(GPU_B.uuid, GPU_A.uuid),
        gpu_runtime_prefix=gpu_runtime(tmp_path),
        inventory_provider=lambda: capabilities(GPU_A, equal_free_b),
    )

    assert resources.select(
        ResourcePreference.GPU_REQUIRED,
        capabilities(GPU_A, equal_free_b),
    ).gpu_uuid == GPU_A.uuid


def test_gpu_required_freezes_greatest_free_identity_while_capacity_is_low(
    tmp_path: Path,
) -> None:
    resources = LocalComputeResources(
        allowed_gpu_uuids=(GPU_B.uuid, GPU_A.uuid),
        gpu_runtime_prefix=gpu_runtime(tmp_path),
        inventory_provider=lambda: capabilities(GPU_A, GPU_B),
    )
    constraints = {
        "min_vram_per_experiment": 40_000,
        "gpu_vram_reserve_mb": 2_048,
    }

    assert resources.select(
        ResourcePreference.GPU_REQUIRED,
        capabilities(GPU_A, GPU_B),
        **constraints,
    ).gpu_uuid == GPU_B.uuid
    assert resources.select(
        ResourcePreference.GPU_REQUIRED,
        capabilities(GPU_A, GPU_B),
        busy_gpu_uuids={GPU_B.uuid},
        **constraints,
    ).gpu_uuid == GPU_A.uuid
    assert resources.select(
        ResourcePreference.AUTO,
        capabilities(GPU_A, GPU_B),
        **constraints,
    ).kind is ResourceKind.CPU


def test_runtime_observation_is_typed_read_only_and_injectable(tmp_path: Path) -> None:
    expected = GpuRuntimeObservation(
        uuid=GPU_A.uuid,
        total_memory_mib=GPU_A.total_memory_mib,
        free_memory_mib=10_240,
        used_memory_mib=14_336,
        utilization_percent=37,
    )
    requested: list[str] = []

    def observe(uuid: str) -> GpuRuntimeObservation:
        requested.append(uuid)
        return expected

    resources = LocalComputeResources(
        allowed_gpu_uuids=(GPU_A.uuid,),
        gpu_runtime_prefix=gpu_runtime(tmp_path),
        inventory_provider=lambda: capabilities(GPU_A),
        observation_provider=observe,
    )

    assert resources.observe_runtime(GPU_A.uuid) == expected
    assert expected.to_dict() == {
        "uuid": GPU_A.uuid,
        "total_memory_mib": GPU_A.total_memory_mib,
        "free_memory_mib": 10_240,
        "used_memory_mib": 14_336,
        "utilization_percent": 37,
    }
    assert requested == [GPU_A.uuid]

    with pytest.raises(ResourceUnavailable, match="not authorized"):
        resources.observe_runtime(GPU_B.uuid)
    with pytest.raises(AttributeError):
        expected.free_memory_mib = 1  # type: ignore[misc]


def test_runtime_observation_rejects_invalid_or_mismatched_provider_values(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="free.*used"):
        GpuRuntimeObservation(
            uuid=GPU_A.uuid,
            total_memory_mib=GPU_A.total_memory_mib,
            free_memory_mib=12_000,
            used_memory_mib=12_000,
            utilization_percent=0,
        )
    with pytest.raises(ValueError, match="utilization_percent"):
        GpuRuntimeObservation(
            uuid=GPU_A.uuid,
            total_memory_mib=GPU_A.total_memory_mib,
            free_memory_mib=12_288,
            used_memory_mib=12_288,
            utilization_percent=101,
        )

    resources = LocalComputeResources(
        allowed_gpu_uuids=(GPU_A.uuid,),
        gpu_runtime_prefix=gpu_runtime(tmp_path),
        inventory_provider=lambda: capabilities(GPU_A),
        observation_provider=lambda uuid: GpuRuntimeObservation(
            uuid=GPU_B.uuid,
            total_memory_mib=GPU_B.total_memory_mib,
            free_memory_mib=GPU_B.free_memory_mib,
            used_memory_mib=GPU_B.total_memory_mib - GPU_B.free_memory_mib,
            utilization_percent=None,
        ),
    )
    with pytest.raises(ResourceUnavailable, match="different GPU"):
        resources.observe_runtime(GPU_A.uuid)


def test_pressure_tracker_recovers_from_transient_low_free_memory() -> None:
    now = [100.0]
    initial = GpuRuntimeObservation(
        uuid=GPU_A.uuid,
        total_memory_mib=GPU_A.total_memory_mib,
        free_memory_mib=8_192,
        used_memory_mib=16_384,
        utilization_percent=20,
    )
    tracker = GpuPressureTracker(
        initial_observation=initial,
        reserve_memory_mib=4_096,
        grace_seconds=3.0,
        now=lambda: now[0],
    )

    now[0] = 101.0
    low = tracker.sample(replace(
        initial,
        free_memory_mib=3_000,
        used_memory_mib=GPU_A.total_memory_mib - 3_000,
        utilization_percent=91,
    ))
    assert low.pressure_exceeded is False

    now[0] = 103.0
    recovered = tracker.sample(replace(
        initial,
        free_memory_mib=4_096,
        used_memory_mib=GPU_A.total_memory_mib - 4_096,
        utilization_percent=42,
    ))
    assert recovered.pressure_exceeded is False
    assert recovered.gpu_minimum_free_memory_mib == 3_000
    assert recovered.gpu_latest_free_memory_mib == 4_096
    assert recovered.gpu_latest_utilization_percent == 42
    assert recovered.sample_count == 3


def test_pressure_tracker_reports_only_a_sustained_breach() -> None:
    now = [10.0]
    initial = GpuRuntimeObservation(
        uuid=GPU_A.uuid,
        total_memory_mib=GPU_A.total_memory_mib,
        free_memory_mib=3_000,
        used_memory_mib=GPU_A.total_memory_mib - 3_000,
        utilization_percent=None,
    )
    tracker = GpuPressureTracker(
        initial_observation=initial,
        reserve_memory_mib=4_096,
        grace_seconds=2.5,
        now=lambda: now[0],
    )

    assert tracker.summary.pressure_exceeded is False
    now[0] = 12.4
    assert tracker.sample(initial).pressure_exceeded is False
    now[0] = 12.5
    summary = tracker.sample(replace(
        initial,
        free_memory_mib=2_500,
        used_memory_mib=GPU_A.total_memory_mib - 2_500,
        utilization_percent=88,
    ))

    assert summary.pressure_exceeded is True
    assert summary.to_dict() == {
        "gpu_uuid": GPU_A.uuid,
        "gpu_total_memory_mib": GPU_A.total_memory_mib,
        "gpu_initial_free_memory_mib": 3_000,
        "gpu_minimum_free_memory_mib": 2_500,
        "gpu_latest_free_memory_mib": 2_500,
        "gpu_latest_used_memory_mib": GPU_A.total_memory_mib - 2_500,
        "gpu_latest_utilization_percent": 88,
        "sample_count": 3,
        "pressure_exceeded": True,
    }


@pytest.mark.parametrize(
    "latest_free,owned,expected",
    [
        (3_901, 552, "self"),
        (4_096, 0, "self"),
        (3_901, 100, "external"),
        (3_000, 0, "external"),
        (1_911, 770, "external"),
        (3_901, None, "unknown"),
        (1, None, "unknown"),
    ],
)
def test_pressure_attribution_only_spares_externally_consumed_devices(
    latest_free: int, owned: int | None, expected: str
) -> None:
    assert (
        gpu_pressure_attribution(
            latest_free_memory_mib=latest_free,
            owned_latest_memory_mib=owned,
            reserve_memory_mib=4_096,
        )
        == expected
    )


def test_pressure_payload_records_cancel_decision_evidence() -> None:
    assert gpu_pressure_payload(
        (("self", 552),), reserve_memory_mib=4_096, terminate_grace_seconds=30.0
    ) == {
        "reason": "sustained_free_gpu_memory_below_reserve",
        "gpu_reserve_memory_mib": 4_096,
        "owned_memory_mib_at_decision": 552,
        "attribution": "self",
        "terminate_grace_seconds": 30.0,
    }
    assert gpu_pressure_payload(
        (("unknown", None),), reserve_memory_mib=4_096, terminate_grace_seconds=30.0
    ) == {
        "reason": "sustained_free_gpu_memory_below_reserve",
        "gpu_reserve_memory_mib": 4_096,
        "owned_memory_mib_at_decision": None,
        "attribution": "unknown",
        "terminate_grace_seconds": 30.0,
    }
    assert gpu_pressure_payload(
        (("external", 552), ("unknown", None)),
        reserve_memory_mib=4_096,
        terminate_grace_seconds=30.0,
    ) == {
        "reason": "sustained_free_gpu_memory_below_reserve",
        "gpu_reserve_memory_mib": 4_096,
        "owned_memory_mib_at_decision": 552,
        "attribution": "unknown",
        "terminate_grace_seconds": 30.0,
    }
    assert gpu_pressure_payload(
        (("self", 552), ("self", 100)),
        reserve_memory_mib=4_096,
        terminate_grace_seconds=30.0,
    )["owned_memory_mib_at_decision"] == 652


def test_workspace_kwargs_freeze_uuid_index_runtime_and_exact_devices(tmp_path: Path) -> None:
    runtime = gpu_runtime(tmp_path)
    resources = LocalComputeResources(
        allowed_gpu_uuids=(GPU_A.uuid,),
        gpu_runtime_prefix=runtime,
        inventory_provider=lambda: capabilities(GPU_A),
    )
    snapshot = resources.snapshot()
    grant = resources.select(ResourcePreference.GPU_REQUIRED, snapshot)

    assert resources.workspace_kwargs(grant, snapshot) == {
        "python_executable": runtime.resolve() / "bin" / "python",
        "runtime_prefix": runtime.resolve(),
        "device_bindings": (
            (Path("/dev/nvidia2"), "/dev/nvidia2"),
            (Path("/dev/nvidiactl"), "/dev/nvidiactl"),
            (Path("/dev/nvidia-uvm"), "/dev/nvidia-uvm"),
        ),
        "fixed_environment": {
            "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
            "CUDA_VISIBLE_DEVICES": GPU_A.uuid,
        },
    }
    assert resources.configuration == {
        "compute_resources": "local-compute/8",
        "gpu_device_uuids": [GPU_A.uuid],
        "gpu_runtime_prefix": str(runtime.resolve()),
    }
    assert resources.runtime_files == (
        runtime.resolve() / "bin" / "python",
        runtime.resolve() / "conda-meta" / "history",
        runtime.resolve() / "conda-meta" / "torch.json",
        runtime.resolve()
        / "lib/python3.12/site-packages/nvidia_cuda_runtime_cu12-12.8.dist-info/METADATA",
        runtime.resolve()
        / "lib/python3.12/site-packages/nvidia_cuda_runtime_cu12-12.8.dist-info/RECORD",
        runtime.resolve()
        / "lib/python3.12/site-packages/torch-2.8.0+cu128.dist-info/METADATA",
        runtime.resolve()
        / "lib/python3.12/site-packages/torch-2.8.0+cu128.dist-info/RECORD",
        runtime.resolve() / "lib/python3.12/site-packages/torch/version.py",
        runtime.resolve() / "lib/python3.12/site-packages/triton-3.4.0.dist-info/METADATA",
        runtime.resolve() / "lib/python3.12/site-packages/triton-3.4.0.dist-info/RECORD",
    )


@pytest.mark.parametrize(
    "drifted",
    [
        GpuCapability(
            index=3,
            uuid=GPU_A.uuid,
            name=GPU_A.name,
            total_memory_mib=GPU_A.total_memory_mib,
            free_memory_mib=GPU_A.free_memory_mib,
            driver_version=GPU_A.driver_version,
        ),
        GpuCapability(
            index=GPU_A.index,
            uuid=GPU_A.uuid,
            name=GPU_A.name,
            total_memory_mib=12_288,
            free_memory_mib=8_192,
            driver_version=GPU_A.driver_version,
        ),
        GpuCapability(
            index=GPU_A.index,
            uuid=GPU_A.uuid,
            name=GPU_A.name,
            total_memory_mib=GPU_A.total_memory_mib,
            free_memory_mib=GPU_A.free_memory_mib,
            driver_version="571.0",
        ),
    ],
)
def test_live_validation_fails_closed_on_stable_capability_drift(
    tmp_path: Path, drifted: GpuCapability
) -> None:
    runtime = gpu_runtime(tmp_path)
    resources = LocalComputeResources(
        allowed_gpu_uuids=(GPU_A.uuid,),
        gpu_runtime_prefix=runtime,
        inventory_provider=lambda: capabilities(drifted),
    )
    snapshot = capabilities(GPU_A)
    grant = ResourceGrant(kind=ResourceKind.GPU, gpu_uuid=GPU_A.uuid)

    with pytest.raises(ResourceUnavailable, match="drift"):
        resources.validate_live(grant, snapshot)


def test_live_validation_reprobes_exact_gpu_and_missing_gpu_fails(tmp_path: Path) -> None:
    runtime = gpu_runtime(tmp_path)
    observed = capabilities(GPU_A)
    calls = 0

    def inventory() -> ComputeCapabilities:
        nonlocal calls
        calls += 1
        return observed

    resources = LocalComputeResources(
        allowed_gpu_uuids=(GPU_A.uuid,),
        gpu_runtime_prefix=runtime,
        inventory_provider=inventory,
    )
    grant = ResourceGrant(kind=ResourceKind.GPU, gpu_uuid=GPU_A.uuid)
    resources.validate_live(grant, capabilities(GPU_A))
    assert calls == 1

    observed = capabilities()
    with pytest.raises(ResourceUnavailable, match="unavailable"):
        resources.validate_live(grant, capabilities(GPU_A))
    assert calls == 2


def test_live_validation_rechecks_dynamic_threshold_for_first_and_retry(
    tmp_path: Path,
) -> None:
    runtime = gpu_runtime(tmp_path)
    observations = iter(
        (
            replace(GPU_A, free_memory_mib=12_288),
            replace(GPU_A, free_memory_mib=12_287),
            replace(GPU_A, free_memory_mib=20_000),
            replace(GPU_A, free_memory_mib=20_000),
        )
    )
    calls = 0

    def inventory() -> ComputeCapabilities:
        nonlocal calls
        calls += 1
        return capabilities(next(observations))

    resources = LocalComputeResources(
        allowed_gpu_uuids=(GPU_A.uuid,),
        gpu_runtime_prefix=runtime,
        inventory_provider=inventory,
    )
    snapshot = capabilities(replace(GPU_A, free_memory_mib=24_000))
    grant = ResourceGrant(kind=ResourceKind.GPU, gpu_uuid=GPU_A.uuid)
    threshold = {
        "min_vram_per_experiment": 10_240,
        "gpu_vram_reserve_mb": 2_048,
    }

    resources.validate_live(grant, snapshot, **threshold)
    with pytest.raises(ResourceCapacityUnavailable, match="12288 MiB is required"):
        resources.validate_live(grant, snapshot, **threshold)
    resources.validate_live(grant, snapshot, **threshold)
    with pytest.raises(ResourceUnavailable, match="already busy"):
        resources.validate_live(
            grant,
            snapshot,
            busy_gpu_uuids={GPU_A.uuid},
            **threshold,
        )
    assert calls == 4


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"min_vram_per_experiment": -1}, "min_vram_per_experiment"),
        ({"gpu_vram_reserve_mb": True}, "gpu_vram_reserve_mb"),
        ({"busy_gpu_uuids": GPU_A.uuid}, "collection"),
        ({"busy_gpu_uuids": [GPU_A.uuid, GPU_A.uuid]}, "duplicates"),
    ],
)
def test_selection_inputs_are_strict(
    tmp_path: Path,
    kwargs: dict[str, object],
    message: str,
) -> None:
    resources = LocalComputeResources(
        allowed_gpu_uuids=(GPU_A.uuid,),
        gpu_runtime_prefix=gpu_runtime(tmp_path),
        inventory_provider=lambda: capabilities(GPU_A),
    )

    with pytest.raises(ValueError, match=message):
        resources.select(ResourcePreference.AUTO, capabilities(GPU_A), **kwargs)


def test_configuration_requires_allowlist_and_runtime_together(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="physical NVIDIA"):
        LocalComputeResources(
            allowed_gpu_uuids=("GPU-not-a-stable-physical-uuid",),
            gpu_runtime_prefix=tmp_path,
        )
    with pytest.raises(ValueError, match="together"):
        LocalComputeResources(allowed_gpu_uuids=(GPU_A.uuid,))
    with pytest.raises(ValueError, match="together"):
        LocalComputeResources(gpu_runtime_prefix=tmp_path)
    with pytest.raises(ValueError, match="duplicate"):
        LocalComputeResources(
            allowed_gpu_uuids=(GPU_A.uuid, GPU_A.uuid),
            gpu_runtime_prefix=tmp_path,
        )


def test_adapter_owns_no_subprocess_or_parallel_assurance_chain() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "hypoloom" / "compute_resources.py"
    ).read_text(encoding="utf-8")
    assert "subprocess" not in source
    assert "hashlib" not in source
    assert "lease" not in source.lower()
    assert "receipt" not in source.lower()
