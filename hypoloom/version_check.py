"""Advisory Git update checks; no fetch, persistent cache or run-state writes."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.metadata
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import time


DEFAULT_TIMEOUT = 5.0
_OID = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})\Z")


def _git_read(repository: Path, *args: str, deadline: float):
    """Bound the complete child group, including an SSH/credential helper."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    environment = dict(os.environ)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        environment.pop(key, None)
    environment.update(GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never",
                       GIT_ASKPASS="/bin/false", SSH_ASKPASS="/bin/false")
    # Use normal SSH config/agent authentication without an interactive prompt.
    environment.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes -oStrictHostKeyChecking=yes -oConnectTimeout=3")
    try:
        process = subprocess.Popen(["git", "-C", str(repository), *args], env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", start_new_session=True)
    except OSError:
        return None
    try:
        output, _ = process.communicate(timeout=remaining)
        return process.returncode, output.strip()
    except BaseException as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        if isinstance(exc, subprocess.TimeoutExpired):
            return None
        raise


def source_version(repository=None, *, deadline=None) -> dict:
    """Current checkout/package identity, also reused by support exports; local only."""
    root = Path(repository).expanduser().resolve() if repository is not None else Path(__file__).resolve().parent.parent
    deadline = time.monotonic() + DEFAULT_TIMEOUT if deadline is None else deadline
    try:
        package_version = importlib.metadata.version("hypoloom")
    except importlib.metadata.PackageNotFoundError:
        package_version = None
    result = {"package_version": package_version, "source_kind": "package_or_archive",
              "local_revision": None, "branch": None, "dirty": None, "partial_clone": None}
    found = _git_read(root, "rev-parse", "--show-toplevel", deadline=deadline)
    if found is None or found[0] or Path(found[1]).resolve() != root:
        return result
    result["source_kind"] = "git_checkout"
    head = _git_read(root, "rev-parse", "--verify", "HEAD", deadline=deadline)
    if head is None or head[0] or not _OID.fullmatch(head[1]):
        return result
    result["local_revision"] = head[1]
    branch = _git_read(root, "symbolic-ref", "--quiet", "--short", "HEAD", deadline=deadline)
    result["branch"] = branch[1] if branch is not None and branch[0] == 0 else None
    partial = _git_read(root, "config", "--get", "extensions.partialClone", deadline=deadline)
    promisor = _git_read(root, "config", "--bool", "--get-regexp", r"^remote\..*\.promisor$", deadline=deadline)
    if partial is not None and promisor is not None and partial[0] in (0, 1) and promisor[0] in (0, 1):
        result["partial_clone"] = partial[0] == 0 or any(line.endswith(" true") for line in promisor[1].splitlines())
    # Older Git versions ignore GIT_NO_LAZY_FETCH. Do not ask them to inspect
    # objects/worktrees in a promisor repository: that can trigger a hidden fetch.
    if result["partial_clone"] is False:
        modified = _git_read(root, "status", "--porcelain", "--untracked-files=normal", deadline=deadline)
        result["dirty"] = bool(modified[1]) if modified is not None and modified[0] == 0 else None
    return result


def check_updates(repository=None, *, offline=False, remote=None, branch=None,
                  timeout=DEFAULT_TIMEOUT) -> dict:
    """Compare the selected checkout with its actual upstream, never a stale ref."""
    if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError("version-check timeout must be greater than 0 and at most 30 seconds")
    root = Path(repository).expanduser().resolve() if repository is not None else Path(__file__).resolve().parent.parent
    deadline = time.monotonic() + timeout
    result = {**source_version(root, deadline=deadline), "remote": None, "remote_branch": None,
        "remote_revision": None, "status": "unavailable", "reason": None, "update_available": None,
        "checked_at": datetime.now(timezone.utc).isoformat(), "remote_query_performed": False,
        "scope": "selected_checkout" if repository is not None else "current_installation"}

    def finish(status, reason=None):
        result.update(status=status, reason=reason)
        result["update_available"] = True if status == "behind" else False if status in {"up_to_date", "ahead"} else None
        result["message"] = format_status(result)
        return result

    if offline:
        return finish("offline", "remote_check_disabled")
    if result["source_kind"] != "git_checkout":
        return finish("unavailable", "no_git_checkout")
    if result["local_revision"] is None:
        return finish("unavailable", "local_revision_unavailable")
    local_branch = result["branch"]
    tracking = None if local_branch is None else _git_read(root, "for-each-ref",
        "--format=%(upstream:remotename)%09%(upstream:remoteref)", "refs/heads/" + local_branch, deadline=deadline)
    upstream = tracking[1].split("\t") if tracking is not None and tracking[0] == 0 else []
    upstream_remote, upstream_ref = upstream if len(upstream) == 2 else (None, None)
    selected_remote = remote or upstream_remote or "origin"
    selected_branch = branch or (upstream_ref.removeprefix("refs/heads/")
        if upstream_ref and (remote is None or remote == upstream_remote) else local_branch)
    if not selected_branch:
        return finish("unavailable", "detached_head_choose_branch")
    result.update(remote=selected_remote, remote_branch=selected_branch)
    remotes = _git_read(root, "remote", deadline=deadline)
    if remotes is None or remotes[0] or selected_remote not in remotes[1].splitlines():
        return finish("unavailable", "remote_not_configured")
    ref = "refs/heads/" + selected_branch
    valid = _git_read(root, "check-ref-format", ref, deadline=deadline)
    if valid is None or valid[0]:
        return finish("unavailable", "invalid_remote_branch")
    result["remote_query_performed"] = True
    observed = _git_read(root, "ls-remote", "--quiet", "--exit-code", "--refs", "--",
                         selected_remote, ref, deadline=deadline)
    if observed is None:
        return finish("unavailable", "timeout_or_git_unavailable")
    if observed[0] != 0:
        return finish("unavailable", "remote_branch_missing" if observed[0] == 2 else "remote_query_failed")
    revisions = [parts[0] for line in observed[1].splitlines()
        if len(parts := line.split()) == 2 and parts[1] == ref and _OID.fullmatch(parts[0])]
    if len(revisions) != 1:
        return finish("unavailable", "invalid_remote_response")
    result["remote_revision"] = revisions[0]
    local, latest = result["local_revision"], revisions[0]
    if local == latest:
        return finish("up_to_date")
    if result["partial_clone"] is not False:
        return finish("remote_differs", "partial_history_not_compared")
    # ls-remote intentionally does not download the commit graph. Only classify
    # ordering when Git can prove it using already available local objects.
    known = _git_read(root, "cat-file", "-e", latest + "^{commit}", deadline=deadline)
    if known is None or known[0]:
        return finish("remote_differs", "remote_history_not_local")
    for first, second, status in ((local, latest, "behind"), (latest, local, "ahead")):
        ancestor = _git_read(root, "merge-base", "--is-ancestor", first, second, deadline=deadline)
        if ancestor is None or ancestor[0] not in (0, 1):
            return finish("remote_differs", "ancestry_unavailable")
        if ancestor[0] == 0:
            return finish(status)
    shallow = _git_read(root, "rev-parse", "--is-shallow-repository", deadline=deadline)
    return finish("diverged" if shallow == (0, "false") else "remote_differs", "history_differs")


def format_status(result: dict) -> str:
    target = f"{result.get('remote')}/{result.get('remote_branch')}"
    messages = {
        "up_to_date": f"Local HEAD matches {target} at the time of this check.",
        "behind": f"Updates are available on {target}; review before updating your checkout.",
        "ahead": f"Local HEAD is ahead of {target}; there are local commits not on that branch.",
        "diverged": f"Local HEAD and {target} have diverged; review both histories before updating.",
        "remote_differs": f"{target} has a different commit; review updates. Commit ordering is not verified without fetching history.",
        "offline": "Remote version check skipped (offline); latest version is unknown.",
        "unavailable": f"Remote version could not be checked ({result.get('reason')}); latest version is unknown.",
    }
    message = messages[result["status"]]
    if result.get("dirty"):
        message += " Uncommitted changes are present; HEAD does not describe the whole working tree."
    elif result.get("dirty") is None:
        message += " Working-tree changes could not be determined."
    return message + " This is advisory; running research keeps its recorded runtime."
