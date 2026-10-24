"""Explicit offline closure of a stopped run through its original Host methods.

This is an administrative operation, never a research resume. The current
controller validates ownership; an isolated child loads only the retained
bundle for storage, recovery, collection and terminal transitions.
"""
from __future__ import annotations

from contextlib import ExitStack
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time


def retirement_request(run: str, revision: int):
    from hypoloom.control_protocol import ControlRequest
    return ControlRequest(1, f"init-pool-retire-{revision}", revision, "CANCEL",
                          {"run_id": run, "cause": "reviewed_init_offline_legacy_closure"})


def frozen_argv(root: Path, run: str, revision: int, *, controller_fd: int | None = None) -> list[str]:
    loader = "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_path(sys.argv.pop(1),run_name='__main__')"
    return [sys.executable, "-I", "-B", "-c", loader, str(root / "bundle"), str(Path(__file__).resolve()),
            "_frozen_execute" if controller_fd is not None else "_frozen_inspect", "--state-root", str(root),
            "--run-id", run, "--expected-revision", str(revision),
            *(["--controller-fd", str(controller_fd)] if controller_fd is not None else [])]


def inspect_target(root: Path, run: str, revision: int, *, timeout: float = 5) -> dict:
    if not shutil.which("bwrap"):
        raise ValueError("legacy_closure_sandbox_unavailable")
    try:
        result = subprocess.run(frozen_argv(root, run, revision), capture_output=True, text=True,
                                timeout=timeout, check=False)
        value = json.loads(result.stdout)
        if result.returncode or value.get("status") != "ready":
            raise ValueError(value.get("reason", "legacy_closure_unsupported"))
        return value
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise ValueError("legacy_closure_inspection_unavailable") from exc


def _compatible(root: Path, run: str) -> dict:
    """Use the bundled types/schema; no initialization, migration or runtime substitution."""
    import hypoloom
    from hypoloom.state_store import StateStore, SCHEMA_VERSION
    from hypoloom.workflow_host import WorkflowHost, WORKFLOW_DEFINITION_VERSION
    from hypoloom.agent_aci import CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION
    from hypoloom.outcome_adapter import OUTCOME_ADAPTER_VERSION
    from hypoloom.runner import RUNNER_VERSION
    if Path(hypoloom.__file__).resolve().parent != (root / "bundle/hypoloom").resolve():
        raise ValueError("legacy_closure_requires_original_bundle")
    store = StateStore(root)
    row = store.get_run(run)
    expected = {"workflow_definition_version": WORKFLOW_DEFINITION_VERSION,
                "agent_aci_version": str(CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION),
                "evaluator_version": OUTCOME_ADAPTER_VERSION, "runner_version": RUNNER_VERSION,
                "artifact_result_schema_version": "2", "sqlite_runtime_version": sqlite3.sqlite_version}
    if row is None or any(row.get(k) != v for k, v in expected.items()):
        raise ValueError("legacy_closure_bundle_incompatible")
    # The supported administrative interfaces are present in the affected ACI 29
    # bundles. Ordinary execution's environment/configuration pins are untouched.
    if row["agent_aci_version"] != "29" or row["workflow_definition_version"] != "hypoloom-lifecycle-v11":
        raise ValueError("legacy_closure_bundle_unsupported")
    required = ("_cancel_run", "_recover_abandoned_attempts", "_reconcile_terminal_attempt_residue",
                "_drive_stopping", "_agent_failure", "_finish_fake_agent")
    if not all(callable(getattr(WorkflowHost, name, None)) for name in required):
        raise ValueError("legacy_closure_host_unsupported")
    with store.readonly_connection() as db:
        if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise ValueError("legacy_closure_schema_incompatible")
    config = json.loads(store.inspect("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND revision=1", (run,))[0]["payload_json"])
    if row["research_contract_version"] != ("hypoloom-research-v12" if config.get("gpu_pool") else "hypoloom-research-v10"):
        raise ValueError("legacy_closure_contract_incompatible")
    return {"status": "ready", "run_id": run, "closure": "offline", "provider_calls": False,
            "missing_final_report_state": "FAILED"}


def _archive_interrupted(host, request, binding):
    """Archive failed work independently of reopening its execution inputs.

    The original preparation rejects auxiliary input directories it created
    itself. Preserve that error and use the original Git/transcript validators;
    no input is removed or reclassified as valid execution input.
    """
    from hypoloom.agent_sessions import AgentSessions, PreparedSessionWorkspace, SessionFinishError, _session_transfer
    from hypoloom.git_authoring_workspace import GitAuthoringWorkspace, _run_git
    from hypoloom.domain import AgentCommand
    from hypoloom.paper_sources import restore_sources
    prior = [r for r in host.store.list_attempts(workflow_id=request.owner_workflow_id,
             command_id=request.command_id) if r["kind"] == "AGENT" and r["attempt_ordinal"] < request.attempt_ordinal]
    sessions = AgentSessions(host.state_root, host.publisher)
    if binding not in (sessions.binding(request), sessions.binding(request, prior[-1] if prior else None)):
        raise ValueError("legacy_session_binding_changed")
    common = host.state_root / request.run_id / "sessions" / request.role / f"c{binding['cycle']}" / request.component_id
    if request.command is AgentCommand.GRAPH_EVOLVE:
        common = host.state_root / request.run_id / "grounding/sessions"
    scope = common / binding["scope"]
    if scope.resolve() != scope or not scope.is_relative_to(host.state_root):
        raise ValueError("legacy_session_path_invalid")
    manager = GitAuthoringWorkspace(scope / "source")
    target = host._run_config(request.run_id)["execution"].get("target")
    transfer = _session_transfer(request)
    base = (transfer[3]["commit"] if transfer is not None else target["commit"] if target else
            _run_git(manager.git_executable, ("--git-dir", str(manager.root / "chain/repository.git"),
                     "rev-list", "--max-parents=0", "HEAD"), timeout_seconds=30).decode().strip())
    workspace = PreparedSessionWorkspace(request=request, publisher=host.publisher, state_root=host.state_root,
        scope_root=scope, session_root=scope / "projects", input_root=scope / "inputs" / request.attempt_id,
        git_workspace=manager.materialize("chain", base_commit=base, branch=binding["branch"]),
        binding=binding, session_arguments=())
    try:
        for item in request.input_artifacts:
            if item.ref.endswith("--expert-resources"):
                workspace.expert_resources = json.loads(item.content_bytes())
            if item.ref.endswith("--leader-resources"):
                workspace.leader_resources = json.loads(item.content_bytes())
        restore_sources(host, workspace)
        try:
            metadata = workspace.finish_node()
        except SessionFinishError as exc:
            metadata = exc.metadata
        return {**metadata, "launch_arguments": None, "archive_after_loss": True}
    finally:
        workspace.cleanup()
        workspace.discard_papers()


def _close_with_frozen_host(root: Path, run: str, revision: int) -> dict:
    from hypoloom.workflow_host import WorkflowHost
    from hypoloom.agent_aci import AgentRequest
    from hypoloom.artifacts import ArtifactPublisher
    from hypoloom.compute_resources import ComputeCapabilities, LocalComputeResources
    from hypoloom.domain import AgentCommand, AttemptTerminalReason
    from hypoloom.state_store import StateStore
    from hypoloom.oracle import Oracle
    from hypoloom.runner import process_group_exists

    class UnavailableProvider:
        model_identity = "unavailable-original-provider"
        adapter_version = "offline-cancellation"
        permission_profile = "no-provider-or-process-execution"

        def invoke(self, request):
            if request.command is not AgentCommand.FINAL_REPORT:
                raise ValueError("legacy_closure_scientific_dispatch_forbidden")
            return None

    class ClosureHost(WorkflowHost):
        def _finish_fake_agent(self, request, raw):
            if request.command is not AgentCommand.FINAL_REPORT:
                raise ValueError("legacy_closure_scientific_dispatch_forbidden")
            self._agent_failure(request, AttemptTerminalReason.FAILED,
                "original_provider_unavailable_during_reviewed_retirement", retryable=False)

        def _agent_failure(self, request, reason, code, *, retryable, details=None, owner_transition=True):
            if reason is AttemptTerminalReason.LOST and (details or {}).get("session_recovery_error"):
                observation = json.loads(self.store.get_attempt(request.attempt_id)["observation_json"])
                binding = observation.get("session_binding")
                if binding is not None:
                    self.agent_session_metadata[request.attempt_id] = _archive_interrupted(self, request, binding)
            return super()._agent_failure(request, reason, code, retryable=retryable,
                                         details=details, owner_transition=owner_transition)

    store = StateStore(root)
    created = json.loads(store.inspect("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND revision=1", (run,))[0]["payload_json"])
    devices = set()
    for row in store.list_attempts(run_id=run):
        if row["kind"] != "AGENT":
            continue
        request = AgentRequest.from_dict(json.loads(row["observation_json"])["request"])
        for item in request.input_artifacts:
            if item.ref.endswith("--compute-capabilities"):
                devices.update(g.uuid for g in ComputeCapabilities.from_bytes(item.content_bytes()).gpus)
    pool = created.get("gpu_pool") or {}
    host = ClosureHost(root, local_agent=UnavailableProvider(),
        compute_resources=LocalComputeResources(allowed_gpu_uuids=tuple(sorted(devices)),
            gpu_runtime_prefix=created["execution"]["runtime_prefix"] if devices else None),
        gpu_pool_socket=Path(pool["socket_path"]) if pool else None,
        gpu_pool_namespace=pool.get("namespace"), gpu_pool_server_uid=pool.get("server_uid"))
    try:
        host.store.initialize()  # Exact bundled schema was checked before writing.
        host.publisher = ArtifactPublisher(root / "artifacts")
        head = host.store.latest_transition(run)
        if head["state"] not in {"COMPLETED", "FAILED", "CANCELLED"}:
            response = host._cancel_run(retirement_request(run, revision))
            if response.status != "accepted":
                raise ValueError("legacy_closure_cancellation_rejected")
            host._recover_abandoned_attempts()
            host._reconcile_terminal_attempt_residue()
            deadline = time.monotonic() + 240
            while host.store.latest_transition(run)["state"] == "STOPPING":
                if time.monotonic() >= deadline:
                    raise ValueError("legacy_closure_deadline")
                host._drive_stopping(run)
                time.sleep(.02)
        audit = Oracle(host.store.database_path, root / "artifacts").audit(run)
        attempts = host.store.list_attempts(run_id=run)
        if (not audit.ok or audit.open_attempt_count or audit.pending_effect_count
                or any(a["process_group_id"] and process_group_exists(a["process_group_id"]) for a in attempts)):
            raise ValueError("legacy_closure_verification_failed")
        return {"status": "terminal", "run_id": run, "state": host.store.latest_transition(run)["state"],
                "attempts_closed": len(attempts), "oracle_ok": audit.ok}
    finally:
        # No full Host startup/close: these include research/pool reconciliation.
        host.store.close()
        host._projection_executor.shutdown(wait=True)


def _execute_locked(args) -> dict:
    from hypoloom.gpu_pool_lifecycle import _hold_controller
    from hypoloom.pool_run_recovery import read_target, recovery_launch
    root = Path(args.state_root)
    with ExitStack() as locks:
        descriptor = _hold_controller(root, locks)
        target = read_target(root, args.run_id, args.expected_revision, retire=True)
        if target["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return {"status": "terminal", "run_id": args.run_id, "state": target["state"]}
        try:
            recovery_launch(target)
        except ValueError as exc:
            if str(exc) != "original_recovery_launch_unavailable":
                raise
        else:
            raise ValueError("legacy_closure_not_required")
        inspect_target(root, args.run_id, args.expected_revision)
        result = subprocess.run(frozen_argv(root, args.run_id, args.expected_revision, controller_fd=descriptor),
                                pass_fds=(descriptor,), capture_output=True, text=True, timeout=300)
        value = json.loads(result.stdout)
        if result.returncode:
            raise ValueError(value.get("reason", "legacy_closure_failed"))
        return value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("execute", "_execute_locked", "_frozen_inspect", "_frozen_execute"))
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-revision", required=True, type=int)
    parser.add_argument("--controller-fd", type=int)
    args = parser.parse_args(argv)
    root = Path(args.state_root)
    try:
        if args.action.startswith("_frozen_"):
            result = _compatible(root, args.run_id)
            if args.action == "_frozen_execute":
                if args.controller_fd is None or args.controller_fd < 3:
                    raise ValueError("legacy_closure_controller_required")
                held, expected = os.fstat(args.controller_fd), (root / "controller.lock").stat()
                if (held.st_dev, held.st_ino) != (expected.st_dev, expected.st_ino):
                    raise ValueError("legacy_closure_controller_mismatch")
                fcntl.flock(args.controller_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = _close_with_frozen_host(root, args.run_id, args.expected_revision)
        elif args.action == "execute":
            sandbox = shutil.which("bwrap")
            if not sandbox:
                raise ValueError("legacy_closure_sandbox_unavailable")
            command = [sandbox, "--die-with-parent", "--unshare-net", "--ro-bind", "/", "/",
                       "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev",
                       "--bind", str(root), str(root), "--ro-bind", str(root / "bundle"), str(root / "bundle"),
                       sys.executable, "-I", "-B", str(Path(__file__).resolve()), "_execute_locked",
                       "--state-root", str(root), "--run-id", args.run_id,
                       "--expected-revision", str(args.expected_revision)]
            os.execv(sandbox, command)
            raise AssertionError("exec returned")
        else:
            result = _execute_locked(args)
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("legacy_") else "legacy_closure_failed"
        print(json.dumps({"status": "blocked", "reason": reason}))
        return 2


if __name__ == "__main__":
    if not any(value.startswith("_frozen_") for value in sys.argv[1:2]):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    raise SystemExit(main())
