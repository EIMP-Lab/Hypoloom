"""Fail closed before executing a GPU workload inside its actual sandbox.

The Host embeds this standard-library-only module in an isolated interpreter.
It checks the granted CUDA devices without importing the experiment or requiring
a particular ML framework, releases its contexts, then execs the original argv.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import sys
import uuid


CUDA_PREFLIGHT_FAILED = 78
CUDA_PREFLIGHT_TIMEOUT_SECONDS = 30
NVIDIA_SYSFS_FILES = (
    "/sys/module/nvidia/initstate",
    "/sys/module/nvidia_uvm/initstate",
)


class CudaPreflightError(RuntimeError):
    pass


def launch_prefix(interpreter: str) -> tuple[str, ...]:
    """Freeze the trusted check in the Process argv, outside candidate imports."""
    return (interpreter, "-I", "-S", "-c", Path(__file__).read_text(encoding="utf-8"))


def check_cuda(visible_devices: str) -> tuple[str, ...]:
    devices = tuple(visible_devices.split(","))
    try:
        if not visible_devices or len(set(devices)) != len(devices):
            raise ValueError("empty or duplicate GPU grant")
        expected = tuple(
            "GPU-" + str(uuid.UUID(value[4:])) if value.startswith("GPU-") else ""
            for value in devices
        )
        if expected != devices:
            raise ValueError("GPU grant must contain canonical physical UUIDs")
    except ValueError as exc:
        raise CudaPreflightError(f"invalid CUDA_VISIBLE_DEVICES: {exc}") from exc

    driver = ctypes.CDLL("libcuda.so.1")

    def call(name: str, argtypes: tuple, *args) -> None:
        function = getattr(driver, name)
        function.argtypes = argtypes
        function.restype = ctypes.c_int
        code = function(*args)
        if code:
            raise CudaPreflightError(f"{name} returned CUDA error {code}")

    integer = ctypes.c_int
    pointer = ctypes.c_void_p
    device_pointer = ctypes.c_uint64
    call("cuInit", (ctypes.c_uint,), 0)
    count = integer()
    call("cuDeviceGetCount", (ctypes.POINTER(integer),), ctypes.byref(count))
    if count.value != len(expected):
        raise CudaPreflightError(
            f"granted {len(expected)} GPU(s), CUDA exposes {count.value}"
        )

    for ordinal, expected_uuid in enumerate(expected):
        device = integer()
        call("cuDeviceGet", (ctypes.POINTER(integer), integer), ctypes.byref(device), ordinal)
        identity = ctypes.create_string_buffer(16)
        call("cuDeviceGetUuid", (pointer, integer), ctypes.byref(identity), device)
        actual_uuid = "GPU-" + str(uuid.UUID(bytes=identity.raw))
        if actual_uuid != expected_uuid:
            raise CudaPreflightError(f"CUDA device {ordinal} differs from its granted UUID")

        # Enumeration alone can succeed while UVM/context initialization fails.
        context = pointer()
        call("cuDevicePrimaryCtxRetain", (ctypes.POINTER(pointer), integer),
             ctypes.byref(context), device)
        allocation = device_pointer()
        try:
            call("cuCtxSetCurrent", (pointer,), context)
            call("cuMemAlloc_v2", (ctypes.POINTER(device_pointer), ctypes.c_size_t),
                 ctypes.byref(allocation), 4096)
            call("cuMemsetD8_v2", (device_pointer, ctypes.c_ubyte, ctypes.c_size_t),
                 allocation, 0, 4096)
            call("cuCtxSynchronize", ())
        finally:
            try:
                if allocation.value:
                    call("cuMemFree_v2", (device_pointer,), allocation)
            finally:
                try:
                    call("cuCtxSetCurrent", (pointer,), None)
                finally:
                    call("cuDevicePrimaryCtxRelease_v2", (integer,), device)
    return expected


def _check() -> int:
    try:
        devices = check_cuda(os.environ.get("CUDA_VISIBLE_DEVICES", ""))
    except (CudaPreflightError, OSError, AttributeError) as exc:
        print(
            f"HYPOLOOM_CUDA_UNAVAILABLE: {exc}; GPU workload was not started. "
            "CPU fallback is forbidden for this GPU grant.",
            file=sys.stderr, flush=True,
        )
        return CUDA_PREFLIGHT_FAILED
    print(f"hypoloom CUDA preflight: ready ({len(devices)} granted GPU(s))",
          file=sys.stderr, flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    command = sys.argv[1:] if argv is None else argv
    if command == ["--check-cuda"]:
        return _check()
    if not command:
        print("hypoloom CUDA preflight: missing workload argv", file=sys.stderr, flush=True)
        return CUDA_PREFLIGHT_FAILED
    # Keep the watchdog outside CUDA calls: a Python signal cannot reliably
    # interrupt a blocked driver call. The child stays in the owned Process
    # group and imports no workload code. exec starts the workload afresh.
    source = (Path(__file__).read_text(encoding="utf-8") if "__file__" in globals()
              else sys.orig_argv[sys.orig_argv.index("-c") + 1])
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-c", source, "--check-cuda"],
            stdin=subprocess.DEVNULL, timeout=CUDA_PREFLIGHT_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f"HYPOLOOM_CUDA_UNAVAILABLE: CUDA preflight did not complete ({type(exc).__name__}); "
              "GPU workload was not started; CPU fallback is forbidden.",
              file=sys.stderr, flush=True)
        return CUDA_PREFLIGHT_FAILED
    if result.returncode != 0:
        return CUDA_PREFLIGHT_FAILED
    os.execvpe(command[0], command, dict(os.environ))
    return 0  # exec only returns in a test double.


if __name__ == "__main__":
    raise SystemExit(main())
