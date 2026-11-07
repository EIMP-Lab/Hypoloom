"""Durable, per-run GPU selection; launched Processes keep their original grants."""
from __future__ import annotations

from dataclasses import replace
import json
import time

from .compute_resources import ComputeCapabilities, GpuCapability, ResourceUnavailable, validate_gpu_uuid
from .control_protocol import ControlResponse
from .gpu_pool import PoolError
from .gpu_pool_client import validate_pool_snapshot
from .lifecycle import Command, RunState
from .profile_v4 import restrict_profile_capabilities


def selection_history(store, run_id):
    return tuple({"revision": row["revision"], **json.loads(row["payload_json"])["resource_selection"]}
        for row in store.inspect("SELECT t.revision,t.payload_json FROM workflow_transitions t "
            "JOIN workflow_commands c ON c.command_id=t.command_id WHERE t.workflow_id=? "
            "AND c.command_type='RESOURCE_SELECT' ORDER BY t.revision", (run_id,)))


def selection_status(store, run_id, configuration):
    history = selection_history(store, run_id)
    selected = history[-1]["gpu_uuids"] if history else configuration.get("gpu_selection")
    if selected is None:
        return None  # A retained runtime does not acquire this control retroactively.
    active = []
    for row in store.inspect("SELECT a.attempt_id,t.payload_json FROM execution_attempts a "
            "JOIN workflow_transitions t ON t.workflow_id=a.workflow_id "
            "AND json_extract(t.payload_json,'$.attempt_id')=a.attempt_id "
            "WHERE a.run_id=? AND a.kind='PROCESS' AND a.state='OPEN' "
            "AND json_type(t.payload_json,'$.resource_grant')='object'", (run_id,)):
        grant = json.loads(row["payload_json"])["resource_grant"]
        devices = grant.get("gpu_uuids") or ([grant["gpu_uuid"]] if grant.get("gpu_uuid") else [])
        active.append({"attempt_id": row["attempt_id"], "gpu_uuids": devices})
    draining = sorted({uuid for item in active for uuid in item["gpu_uuids"] if uuid not in selected})
    return {"gpu_uuids": selected, "revision": history[-1]["revision"] if history else 0,
        "mode": "drain", "draining_gpu_uuids": draining, "active_allocations": active,
        "changed_at": history[-1]["changed_at"] if history else None}


class ResourceControls:
    def __init__(self, host):
        self.host = host
        self.pending = {}  # Observable cleanup failure; durable history drives retries.
        self._reconciled = set()

    def history(self, run_id):
        return self.host.store.cached_read(("resource-selection", run_id),
            lambda: selection_history(self.host.store, run_id))

    def current(self, run_id):
        history = self.history(run_id)
        return history[-1] if history else None

    def pauses_planning(self, run_id, component_id, command):
        if command not in {"DRAFT_EXPERIMENT_SPEC", "EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION"}:
            return False
        selection = self.current(run_id)
        if selection is None or selection["gpu_uuids"]:
            return False
        from .component_contract import component_execution_plan
        from .domain import ComponentResultKind
        contract = self.host._research_contract(run_id)
        component = contract.component(component_id)
        cpu_only = (component is not None and component.result_kind is ComponentResultKind.ARTIFACT
            and component_execution_plan(contract.document, component_id).get("production") is False)
        return not cpu_only

    def compute(self, run_id):
        """Historical authority for validation/telemetry, never admission."""
        allowed = set(self.host.compute_resources.allowed_gpu_uuids)
        for selection in self.history(run_id):
            allowed.update(selection["gpu_uuids"])
        return self.host.compute_resources.for_devices(sorted(allowed))

    def capabilities(self, run_id):
        if not self.host._run_config(run_id)["execution"]["allow_gpu"]:
            return ComputeCapabilities(gpus=())
        selection = self.current(run_id)
        if selection is None:
            return restrict_profile_capabilities(self.host._research_contract(run_id).document,
                self.host.compute_resources.snapshot())
        return self.host.compute_resources.for_devices(selection["gpu_uuids"]).snapshot()

    def effective(self, run_id, intent, capabilities=None, *, revision=None):
        """Rebind only device choices; count, workload and reserve stay frozen."""
        if revision is None:
            selection = self.current(run_id)
        elif type(revision) is not int or revision < 0:
            raise ValueError("invalid resource selection revision")
        else:
            selection = next((item for item in self.history(run_id) if item["revision"] == revision), None)
            if revision and selection is None:
                raise ValueError("resource selection revision is absent")
        if selection is None:
            return intent, capabilities, 0
        if len(selection["gpu_uuids"]) < intent.gpu_count:
            raise ResourceUnavailable("operator_gpu_selection")
        return (replace(intent, allowed_gpu_uuids=tuple(selection["gpu_uuids"])),
            ComputeCapabilities.from_dict(selection["capabilities"]), selection["revision"])

    def options(self, run_id):
        host = self.host
        head = host.store.latest_transition(run_id)
        if head is None:
            raise ValueError("unknown run")
        if host.shared_gpu is None or not host._run_config(run_id)["execution"]["allow_gpu"]:
            raise ValueError("live GPU selection requires a GPU-enabled shared-pool run")
        snapshot = host.shared_gpu.snapshot()
        members = validate_pool_snapshot(snapshot, aci_version=27)
        if "resource_selection/1" not in snapshot.get("capabilities", []):
            raise ValueError("bound GPU pool lacks resource_selection/1; live selection is unavailable")
        capabilities = host.compute_resources.for_devices(members).snapshot()
        return {"run_id": run_id, "run_revision": head["revision"],
            "devices": [gpu.to_dict() for gpu in capabilities.gpus],
            "selection": self.status(run_id), "gpu_pool": host.shared_gpu.configuration}

    def status(self, run_id):
        result = selection_status(self.host.store, run_id, self.host._run_config(run_id))
        if result is not None:
            result["pending_cleanup"] = self.pending.get(run_id)
        return result

    def select(self, request):
        host = self.host
        if set(request.payload) != {"run_id", "mode", "gpu_uuids"}:
            raise ValueError("RESOURCE_SELECT requires run_id, mode and gpu_uuids")
        run_id, mode, values = (request.payload[key] for key in ("run_id", "mode", "gpu_uuids"))
        if not isinstance(run_id, str) or mode not in {"set", "add", "remove", "all", "none"}:
            raise ValueError("invalid run or selection mode")
        if not isinstance(values, list) or len(values) != len(set(values)):
            raise ValueError("gpu_uuids must be a unique list")
        for value in values:
            validate_gpu_uuid(value)
        if (mode in {"all", "none"}) != (not values):
            raise ValueError("all/none take no UUIDs; set/add/remove require UUIDs")

        def operation(tx):
            head = tx.latest_transition(run_id)
            if head is None or head["state"] not in {"CREATED", "ACTIVE", "WAITING_HUMAN"}:
                raise ValueError("GPU selection requires a live run")
            options = self.options(run_id)
            previous = options["selection"]
            if previous is None:
                raise ValueError("run runtime does not support live GPU selection")
            available = {gpu["uuid"] for gpu in options["devices"]}
            selected = set(previous["gpu_uuids"])
            if mode == "set": selected = set(values)
            elif mode == "add": selected.update(values)
            elif mode == "remove":
                if not set(values) <= selected:
                    raise ValueError("cannot remove a GPU outside the current selection")
                selected.difference_update(values)
            elif mode == "all": selected = available
            else: selected = set()
            if not selected <= available:
                raise ValueError("selected GPUs must be visible local members of the bound pool")
            capabilities = ComputeCapabilities(gpus=tuple(GpuCapability.from_dict(gpu)
                for gpu in options["devices"] if gpu["uuid"] in selected))
            selection = {"gpu_uuids": sorted(selected), "capabilities": capabilities.to_dict(),
                "pool_members": sorted(available), "previous_revision": previous["revision"],
                "changed_at": time.time(), "mode": "drain"}
            row = host._transition_record(tx, run_id, run_id, "RUN", Command.RESOURCE_SELECT,
                RunState(head["state"]), request.request_id, next_command=head["next_command"],
                payload={"resource_selection": selection})
            tx.append_transition(row)
            return {"status": "accepted", "revision": row["revision"], "result": {
                "run_id": run_id, "gpu_uuids": selection["gpu_uuids"], "mode": "drain",
                "resource_selection_revision": row["revision"]}}

        stored = host.store.execute_command({"command_id": request.request_id, "run_id": run_id,
            "workflow_id": run_id, "expected_revision": request.expected_revision,
            "command_type": Command.RESOURCE_SELECT.value, "request": request.to_mapping()}, operation)
        if stored.status != "accepted":
            error = stored.error or {"code": "rejected", "message": "GPU selection rejected"}
            return ControlResponse.failure(request.request_id, "rejected", error["code"], error["message"], stored.revision)
        # Commit before pool I/O. A timeout/crash is reconciled from the same
        # durable selection, including when the client replays its request ID.
        self.reconcile(run_id)
        return ControlResponse.accepted(request.request_id, stored.revision, stored.result or {})

    def reconcile(self, run_id):
        host = self.host
        selection = self.current(run_id)
        if selection is None or host.shared_gpu is None:
            return True
        identity = (run_id, selection["revision"])
        if identity in self._reconciled:
            return True
        try:
            for spec in host.store.list_facts(run_id, "ExperimentPreregistered"):
                if spec["payload"].get("execution_contract", {}).get("resource_intent") is None:
                    continue
                attempts = host.store.list_attempts(execution_id=spec["execution_id"])
                if any(row["state"] == "OPEN" for row in attempts):
                    continue
                head = host.store.latest_transition(spec["experiment_id"])
                if head["state"] not in {"READY", "PREREGISTERED", "EXECUTING"}:
                    continue
                attempt_id = f"{spec['experiment_id']}--process-{len(attempts) + 1}"
                key = host.shared_gpu.key(run_id, spec["execution_id"], attempt_id)
                try:
                    host.shared_gpu.reselect(key, selection["gpu_uuids"])
                except PoolError as exc:
                    if exc.code != "not_found": raise
        except PoolError as exc:
            self.pending[run_id] = {"code": exc.code, "revision": selection["revision"]}
            return False
        self.pending.pop(run_id, None)
        self._reconciled.add(identity)
        return True
