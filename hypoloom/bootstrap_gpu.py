"""Read-only GPU discovery and defaults for new init plans.

Pool membership and the run's initial selection are independent. Concrete UUIDs
are carried forward in the reviewed argv; launch never expands a pinned plan.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat

from .compute_resources import ComputeCapabilities, ComputeResourceError, GpuCapability, _nvml_inventory, validate_gpu_uuid
from .profile_v4 import restrict_profile_capabilities


def _device_access(path: Path) -> bool:
    """Check device permissions, including device cgroups, without a CUDA context."""
    try:
        if not stat.S_ISCHR(path.stat().st_mode):
            return False
        descriptor = os.open(path, os.O_RDWR | os.O_CLOEXEC | os.O_NONBLOCK)
        os.close(descriptor)
        return True
    except OSError:
        return False


def inventory() -> dict:
    try:
        observed = _nvml_inventory()
    except ComputeResourceError as exc:
        return {"status": "unavailable", "devices": [], "excluded_devices": [], "reason": str(exc),
            "cuda_execution_verified": False}
    common = all(_device_access(Path(path)) for path in ("/dev/nvidiactl", "/dev/nvidia-uvm"))
    devices, excluded = [], []
    for gpu in observed.gpus:
        if common and _device_access(Path(f"/dev/nvidia{gpu.index}")):
            devices.append(gpu.to_dict())
        else:
            excluded.append({"uuid": gpu.uuid, "index": gpu.index,
                "reason": "gpu_device_access_denied" if common else "shared_device_nodes_unavailable"})
    return {"status": "observed", "devices": devices, "excluded_devices": excluded,
        "checks": ["nvml_inventory", "device_node_access"], "cuda_execution_verified": False}


def default_scheduling(args) -> None:
    if args.allow_gpu and not (args.managed_pool or args.nonpooled or args.gpu_pool_socket):
        args.managed_pool = True


def _uuids(values, label):
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError(f"{label} must contain explicit GPU UUIDs")
    for value in values:
        validate_gpu_uuid(value)
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must not repeat a GPU UUID")
    return sorted(values)


def resolve(args, contract: dict) -> dict:
    """Resolve defaults once, leaving Profile and pool services untouched."""
    observed = inventory()
    if observed["status"] != "observed":
        raise ValueError("GPU inventory unavailable; repair discovery or select CPU-only")
    capabilities = ComputeCapabilities(gpus=tuple(GpuCapability.from_dict(gpu) for gpu in observed["devices"]))
    available = {gpu.uuid for gpu in capabilities.gpus}
    if not available:
        raise ValueError("no usable GPUs passed NVML and device-node checks; repair GPU access or select CPU-only")
    external = bool(args.gpu_pool_socket and not args.managed_pool)
    candidates = set(available)
    if external and not args.gpu_device_uuid:
        from .agent_aci import CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION
        from .gpu_pool_client import PoolClient, validate_pool_snapshot
        from .gpu_pool import PoolError
        try:
            snapshot = PoolClient(Path(args.gpu_pool_socket), expected_server_uid=args.gpu_pool_server_uid).request("snapshot", {})
            candidates.intersection_update(validate_pool_snapshot(snapshot, aci_version=CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION))
        except PoolError as exc:
            raise ValueError(f"cannot resolve authorized GPUs from the external pool: {exc.code}") from exc
    run_source = "explicit" if args.gpu_device_uuid is not None else "profile"
    if args.gpu_device_uuid is not None:
        selected = _uuids(args.gpu_device_uuid, "run GPU selection")
        if not set(selected) <= available:
            raise ValueError("reviewed run GPUs are unavailable or inaccessible; inspect again before launch")
    else:
        candidate_capabilities = ComputeCapabilities(gpus=tuple(gpu for gpu in capabilities.gpus if gpu.uuid in candidates))
        effective = restrict_profile_capabilities(contract, candidate_capabilities)
        selected = [gpu.uuid for gpu in effective.gpus]
        if not selected:
            raise ValueError("GPU launch has no devices permitted by the Profile and pool; retain CPU-only for safety.gpus=[]")
    pool_source = None
    pool_devices = None
    if args.managed_pool:
        pool_source = "reviewed" if args.pool_device_uuid is not None else "all_usable_devices"
        pool_devices = (_uuids(args.pool_device_uuid, "reviewed pool membership")
            if args.pool_device_uuid is not None else sorted(available))
        if not set(pool_devices) <= available:
            raise ValueError("reviewed pool GPUs are unavailable or inaccessible; inspect again before pool maintenance")
        if not set(selected) <= set(pool_devices):
            raise ValueError("initial run selection must be contained in the reviewed pool membership")
    # Do not mutate partial choices when discovery/permission checks fail.
    args.gpu_device_uuid = selected
    if pool_devices is not None:
        args.pool_device_uuid = pool_devices
    return {"policy": "all_usable_devices" if args.managed_pool else "external_pool" if external else "nonpooled",
        "eligible_gpu_uuids": sorted(available), "excluded_devices": observed.get("excluded_devices", []),
        "pool_membership_source": pool_source, "run_selection_source": run_source,
        "cuda_execution_verified": False}
