"""Advisory freshness checks against actual Git refs, without checkout mutations."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from hypoloom import bootstrap, cli, support_presentation, version_check


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def commit(root, text):
    (root / "file.txt").write_text(text)
    git(root, "add", "file.txt")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "--quiet", "-m", text)
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repositories(tmp_path):
    remote, local, peer = (tmp_path / name for name in ("remote.git", "local", "peer"))
    remote.mkdir()
    git(remote, "init", "--bare", "--quiet")
    local.mkdir()
    git(local, "init", "--quiet")
    git(local, "checkout", "-b", "dev")
    commit(local, "initial")
    git(local, "remote", "add", "origin", str(remote))
    git(local, "push", "--quiet", "-u", "origin", "dev")
    git(tmp_path, "clone", "--quiet", "--branch", "dev", str(remote), str(peer))
    return local, peer, remote


def tree(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def advance(peer):
    revision = commit(peer, "remote advance")
    git(peer, "push", "--quiet")
    return revision


def test_equal_and_dirty_are_independent_and_check_writes_nothing(repositories):
    local, _, remote = repositories
    clean = version_check.check_updates(local)
    assert clean["status"] == "up_to_date" and clean["update_available"] is False
    assert clean["dirty"] is False
    (local / "file.txt").write_text("uncommitted")
    (local / "untracked.txt").write_text("untracked")
    before, remote_before = tree(local), tree(remote)
    checked = version_check.check_updates(local)
    assert checked["status"] == "up_to_date" and checked["dirty"] is True
    assert "HEAD does not describe the whole working tree" in checked["message"]
    assert tree(local) == before and tree(remote) == remote_before


def test_stale_tracking_ref_does_not_falsely_report_latest_or_fetch(repositories):
    local, peer, _ = repositories
    latest = advance(peer)
    assert git(local, "rev-parse", "origin/dev") == git(local, "rev-parse", "HEAD")
    before = tree(local)
    result = version_check.check_updates(local)
    assert result["remote_revision"] == latest
    assert result["status"] == "remote_differs" and result["update_available"] is None
    assert result["reason"] == "remote_history_not_local"
    assert tree(local) == before


@pytest.mark.parametrize("status", ["behind", "ahead", "diverged"])
def test_classifies_only_provable_local_ancestry(repositories, status):
    local, peer, _ = repositories
    if status in {"behind", "diverged"}:
        advance(peer)
        git(local, "fetch", "--quiet")  # Fixture preparation, never the checker.
    if status in {"ahead", "diverged"}:
        commit(local, "local advance")
    before = tree(local)
    result = version_check.check_updates(local)
    assert result["status"] == status
    assert result["update_available"] is ({"behind": True, "ahead": False, "diverged": None}[status])
    assert tree(local) == before


def test_shallow_history_does_not_claim_divergence(repositories, tmp_path):
    local, peer, remote = repositories
    advance(peer)
    shallow = tmp_path / "shallow"
    git(tmp_path, "clone", "--quiet", "--depth", "1", "--branch", "dev", remote.as_uri(), str(shallow))
    # Both tips are available, but the edge to the old tip is cut by shallow depth.
    old = git(local, "rev-parse", "HEAD")
    git(shallow, "fetch", "--quiet", str(local), "dev:refs/heads/old")
    git(shallow, "checkout", "--detach", old)
    before = tree(shallow)
    result = version_check.check_updates(shallow, branch="dev")
    assert result["status"] == "remote_differs"
    assert tree(shallow) == before


@pytest.mark.parametrize("key,value", [("remote.origin.promisor", "true"),
                                       ("extensions.partialClone", "origin")])
def test_partial_clone_never_reads_missing_objects_or_lazily_fetches(repositories, monkeypatch, key, value):
    local, peer, _ = repositories
    advance(peer)
    git(local, "config", key, value)
    git(local, "config", "remote.origin.partialclonefilter", "blob:none")
    original = version_check._git_read
    commands = []

    def observed(root, *args, **kwargs):
        commands.append(args[0])
        return original(root, *args, **kwargs)

    monkeypatch.setattr(version_check, "_git_read", observed)
    before = tree(local)
    result = version_check.check_updates(local)
    assert result["status"] == "remote_differs"
    assert result["reason"] == "partial_history_not_compared"
    assert result["partial_clone"] is True and result["dirty"] is None
    assert not {"cat-file", "merge-base", "status", "fetch"}.intersection(commands)
    assert tree(local) == before


def test_explicitly_disabled_promisor_setting_still_checks_normal_history(repositories):
    local, peer, _ = repositories
    advance(peer)
    git(local, "fetch", "--quiet")
    git(local, "config", "remote.origin.promisor", "false")
    result = version_check.check_updates(local)
    assert result["partial_clone"] is False and result["dirty"] is False
    assert result["status"] == "behind"


def test_upstream_remote_and_differently_named_branch_are_respected(repositories):
    local, _, _ = repositories
    git(local, "remote", "rename", "origin", "my-fork")
    git(local, "branch", "-m", "work")
    result = version_check.check_updates(local)
    assert (result["remote"], result["remote_branch"], result["status"]) == ("my-fork", "dev", "up_to_date")


def test_explicit_remote_does_not_inherit_other_remotes_branch(repositories):
    local, _, remote = repositories
    git(local, "remote", "add", "second", str(remote))
    git(local, "branch", "-m", "work")
    assert version_check.check_updates(local, remote="second")["reason"] == "remote_branch_missing"
    result = version_check.check_updates(local, remote="second", branch="dev")
    assert result["status"] == "up_to_date" and result["remote"] == "second"


def test_no_upstream_falls_back_to_origin_and_current_branch(repositories):
    local, _, _ = repositories
    git(local, "branch", "--unset-upstream")
    result = version_check.check_updates(local)
    assert result["status"] == "up_to_date" and result["remote_branch"] == "dev"


def test_detached_head_requires_explicit_target(repositories):
    local, _, _ = repositories
    git(local, "checkout", "--detach")
    result = version_check.check_updates(local)
    assert result["reason"] == "detached_head_choose_branch"
    assert result["remote_query_performed"] is False
    assert version_check.check_updates(local, branch="dev")["status"] == "up_to_date"


@pytest.mark.parametrize("kwargs,reason", [
    ({"remote": "missing"}, "remote_not_configured"),
    ({"branch": "missing"}, "remote_branch_missing"),
    ({"branch": "../invalid"}, "invalid_remote_branch"),
])
def test_missing_or_invalid_target_is_unavailable(repositories, kwargs, reason):
    result = version_check.check_updates(repositories[0], **kwargs)
    assert result["status"] == "unavailable" and result["reason"] == reason
    assert result["update_available"] is None


def test_offline_does_not_query_remote_and_still_describes_checkout(repositories, monkeypatch):
    original = version_check._git_read

    def no_network(root, *args, **kwargs):
        assert args[0] != "ls-remote"
        return original(root, *args, **kwargs)

    monkeypatch.setattr(version_check, "_git_read", no_network)
    result = version_check.check_updates(repositories[0], offline=True)
    assert result["status"] == "offline" and result["local_revision"]
    assert not result["remote_query_performed"] and result["update_available"] is None


def test_archive_inside_an_unrelated_repository_is_not_that_checkout(repositories):
    archive = repositories[0] / "archive"
    archive.mkdir()
    result = version_check.check_updates(archive)
    assert result["source_kind"] == "package_or_archive" and result["local_revision"] is None
    assert result["reason"] == "no_git_checkout" and not result["remote_query_performed"]


def test_missing_git_is_advisory(repositories, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "missing"))
    result = version_check.check_updates(repositories[0])
    assert result["status"] == "unavailable" and result["local_revision"] is None
    assert not result["remote_query_performed"]


def test_default_identity_uses_installed_code_not_current_directory(repositories, monkeypatch):
    local, peer, _ = repositories
    monkeypatch.chdir(peer)
    monkeypatch.setattr(version_check, "__file__", str(local / "hypoloom/version_check.py"))
    commit(peer, "different current directory")
    result = version_check.check_updates(offline=True)
    assert result["local_revision"] == git(local, "rev-parse", "HEAD")
    assert result["local_revision"] != git(peer, "rev-parse", "HEAD")


def test_remote_failures_do_not_expose_url_or_stderr(repositories, tmp_path):
    local, _, _ = repositories
    private_url = str(tmp_path / "private-url-token-SECRET")
    git(local, "remote", "set-url", "origin", private_url)
    result = version_check.check_updates(local)
    assert result["reason"] == "remote_query_failed"
    assert "SECRET" not in json.dumps(result)


@pytest.mark.parametrize("timeout", [0, -1, 31, float("inf"), float("nan"), True])
def test_invalid_deadlines_are_rejected(timeout):
    with pytest.raises(ValueError, match="timeout"):
        version_check.check_updates(timeout=timeout)


def test_one_deadline_covers_all_git_calls(repositories, monkeypatch):
    original = version_check._git_read
    deadlines = []

    def observed(root, *args, **kwargs):
        deadlines.append(kwargs["deadline"])
        return original(root, *args, **kwargs)

    monkeypatch.setattr(version_check, "_git_read", observed)
    assert version_check.check_updates(repositories[0])["status"] == "up_to_date"
    assert len(deadlines) > 5 and len(set(deadlines)) == 1


def test_git_timeout_terminates_owned_helper_and_suppresses_prompts(tmp_path, monkeypatch):
    executable = tmp_path / "git"
    evidence = tmp_path / "processes.json"
    executable.write_text(f"#!{sys.executable}\n" +
        "import json, os, pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        f"pathlib.Path({str(evidence)!r}).write_text(json.dumps({{'parent': os.getpid(), 'child': child.pid, "
        "'env': {key: os.environ.get(key) for key in "
        "['GIT_TERMINAL_PROMPT', 'GIT_OPTIONAL_LOCKS', 'GIT_NO_LAZY_FETCH', 'GIT_DIR', 'GIT_SSH_COMMAND']}}))\n"
        "print('secret stderr', file=sys.stderr)\ntime.sleep(30)\n")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("GIT_DIR", "/unrelated")
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    started = time.monotonic()
    result = version_check._git_read(tmp_path, "ls-remote", deadline=started + 0.5)
    assert result is None and time.monotonic() - started < 2
    data = json.loads(evidence.read_text())
    assert data["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert data["env"]["GIT_OPTIONAL_LOCKS"] == "0"
    assert data["env"]["GIT_NO_LAZY_FETCH"] == "1" and data["env"]["GIT_DIR"] is None
    assert "BatchMode=yes" in data["env"]["GIT_SSH_COMMAND"]
    for pid in (data["parent"], data["child"]):
        path = Path(f"/proc/{pid}/stat")
        try:
            state = path.read_text().split(")", 1)[1].split()[0]
        except FileNotFoundError:
            continue
        if state != "Z":
            os.kill(pid, signal.SIGKILL)
            pytest.fail(f"version check left a live process: {pid}")


@pytest.mark.parametrize("options,expected", [([], "up_to_date"), (["--offline"], "offline"),
                                                   (["--branch", "missing"], "unavailable")])
def test_cli_results_are_advisory(repositories, capsys, options, expected):
    assert cli.main(["version-check", "--repository", str(repositories[0]), "--json", *options]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == expected


def test_interrupt_also_closes_git_helper_group(tmp_path):
    executable, evidence = tmp_path / "git", tmp_path / "child.json"
    executable.write_text(f"#!{sys.executable}\n" +
        "import json, os, pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        f"pathlib.Path({str(evidence)!r}).write_text(json.dumps([os.getpid(), child.pid]))\n"
        "time.sleep(30)\n")
    executable.chmod(0o755)
    script = ("import pathlib, time\nfrom hypoloom.version_check import _git_read\n"
              f"_git_read(pathlib.Path({str(tmp_path)!r}), 'ls-remote', deadline=time.monotonic()+30)\n")
    process = subprocess.Popen([sys.executable, '-B', '-c', script],
        env=dict(os.environ, PATH=str(tmp_path)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    group = None
    try:
        limit = time.monotonic() + 5
        while not evidence.exists() and time.monotonic() < limit:
            time.sleep(0.01)
        group, child = json.loads(evidence.read_text())
        process.send_signal(signal.SIGINT)
        assert process.wait(timeout=5) != 0
        for pid in (group, child):
            path = Path(f"/proc/{pid}/stat")
            try:
                state = path.read_text().split(")", 1)[1].split()[0]
            except FileNotFoundError:
                continue
            assert state == "Z", f"interrupt left a live version-check helper: {pid}"
    finally:
        if group is not None:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_bootstrap_paths_exposes_the_reminder_without_blocking(repositories, capsys):
    local, _, _ = repositories
    git(local, "remote", "remove", "origin")
    assert bootstrap.main(["paths", "--repository", str(local)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["version_check"]["reason"] == "remote_not_configured"
    assert bootstrap.main(["paths", "--repository", str(local), "--offline"]) == 0
    assert json.loads(capsys.readouterr().out)["version_check"]["status"] == "offline"


def test_support_environment_reuses_local_identity_without_remote_requests(monkeypatch):
    monkeypatch.setattr(version_check, "check_updates", lambda *a, **kw: pytest.fail("remote check in support export"))
    monkeypatch.setattr(version_check, "source_version", lambda: {
        "package_version": "0.0.1", "local_revision": "a" * 40, "dirty": True})
    monkeypatch.setattr(support_presentation.shutil, "which", lambda _: None)
    result = support_presentation.support_environment()
    assert result["framework_version"] == "0.0.1" and result["source_revision"] == "a" * 40
    assert result["source_modified"] is True
