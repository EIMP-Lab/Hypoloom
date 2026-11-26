"""A Profile-bound GOAL entrypoint and its bounded development-data run."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess

REPORT = "preflight.json"
VALIDATED_REPORT = "preflight-validated.json"
MAX_REPORT_BYTES = 64 * 1024


def checked_spec(value):
    if not hasattr(value, "keys") or set(value) != {"path", "sha256", "timeout_seconds"}:
        raise ValueError("preflight requires path, sha256 and timeout_seconds")
    path = value["path"]
    if (not isinstance(path, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+\.py", path)
            or any(part in {"", ".", "..", ".git"} for part in path.split("/"))):
        raise ValueError("preflight.path must be a relative Python file in the bound Git target")
    if not isinstance(value["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"]):
        raise ValueError("preflight.sha256 must be the lowercase SHA-256 of that file")
    if type(value["timeout_seconds"]) is not int or not 1 <= value["timeout_seconds"] <= 300:
        raise ValueError("preflight.timeout_seconds must be an integer from 1 to 300")
    return dict(value)


def check_target(spec, target):
    spec = checked_spec(spec)
    if not target:
        raise ValueError("GOAL preflight requires a bound Git target")
    path = spec["path"]
    git = ["git", "-C", str(target["checkout"])]
    try:
        entry = subprocess.check_output([*git, "ls-tree", "-lz", target["commit"], "--", ":(literal)" + path],
                                        stderr=subprocess.DEVNULL, timeout=10)
        fields = entry.partition(b"\t")[0].split()
        if (len(fields) != 4 or fields[0] not in {b"100644", b"100755"} or fields[1] != b"blob"
                or not 0 < int(fields[3]) <= 1024 * 1024):
            raise ValueError("preflight must name a regular Git blob of at most 1 MiB")
        data = subprocess.check_output([*git, "cat-file", "blob", fields[2].decode("ascii")], timeout=10)
    except subprocess.SubprocessError as exc:
        raise ValueError("cannot read preflight from the bound Git commit") from exc
    if hashlib.sha256(data).hexdigest() != spec["sha256"]:
        raise ValueError("preflight.sha256 differs from the file in the bound Git commit")


def check_submission(spec, argv, files=None):
    if tuple((argv or ())[:2]) != ("python", "/input/" + spec["path"]) or "--preflight" in argv:
        raise ValueError(f"GOAL execution_argv must begin with python /input/{spec['path']}; Host appends --preflight only for the sample")
    if files is not None and hashlib.sha256(files.get(spec["path"], b"")).hexdigest() != spec["sha256"]:
        raise ValueError("submitted GOAL entrypoint differs from the Profile-bound template")


def sample_request(raw, spec):
    """Use the formal capture, interpreter and device grant with a private output and only public inputs."""
    sample_dir = Path(raw.cwd).parent / "goal-preflight"
    args = iter(raw.argv)
    command = [next(args)]
    single = {"--proc", "--dev", "--dir", "--tmpfs", "--unsetenv"}
    pairs = {"--bind", "--ro-bind", "--dev-bind", "--setenv"}
    for flag in args:
        if flag == "--chdir":
            break
        if flag in {"--unshare-all", "--die-with-parent", "--new-session"}:
            command.append(flag)
        elif flag in single:
            arg = next(args)
            if flag != "--dir" or not _held_out(arg):
                command.extend((flag, arg))
        elif flag in pairs:
            a, b = next(args), next(args)
            if flag == "--ro-bind" and _held_out(b):
                continue
            if flag == "--bind" and b == "/output":
                a = str(sample_dir)
            if flag == "--setenv" and a == "HYPOLOOM_READ_ROOTS":
                b = json.dumps([p for p in json.loads(b) if not _held_out(p)], separators=(",", ":"))
            command.extend((flag, a, b))
        else:
            raise ValueError(f"unexpected GOAL sandbox option {flag}")
    if (next(args, None), next(args, None)) != ("/output", "--"):
        raise ValueError("GOAL sample requires the formal output working directory")
    command.extend(("--chdir", "/output", "--", *args, "--preflight"))
    return replace(raw, attempt_id=raw.attempt_id + "--preflight", argv=tuple(command),
                   timeout_seconds=min(raw.timeout_seconds, spec["timeout_seconds"]),
                   stdout_path="preflight-stdout.log", stderr_path="preflight-stderr.log")


def _held_out(path):
    return PurePosixPath(path).is_relative_to("/hypoloom-evaluation")


def check_sample_outputs(directory, parser):
    """Verify the sample reached the same project evidence filenames as the GOAL parser."""
    for i, word in enumerate(parser.argv[:-2]):
        if word == "--chdir":
            break
        if word != "--ro-bind" or not parser.argv[i + 2].startswith("/raw/"):
            continue
        name = parser.argv[i + 2].removeprefix("/raw/")
        if name in {"stdout.log", "stderr.log"}:  # Runtime-owned streams are captured separately.
            continue
        path = directory / name
        if (path.resolve() != path or not path.is_file() or not path.stat().st_size):
            raise ValueError(f"GOAL preflight did not write its declared raw evidence: {name}")


def read_report(directory, name=REPORT):
    path = Path(directory) / name
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= MAX_REPORT_BYTES:
        raise ValueError("preflight.json must be a fresh, bounded regular file")
    data = path.read_bytes()
    value = json.loads(data)
    if not isinstance(value, dict) or value.get("passed") is not True:
        raise ValueError("preflight.json must report passed=true")
    return data
