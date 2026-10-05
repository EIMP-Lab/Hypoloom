"""Host-side shared GPU dispatch and reconciliation using existing effect rows."""
from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .compute_resources import gpu_memory_reserve_mib, validate_gpu_uuid
from .domain import ResourceEstimate, ResourceGrant, ResourceIntent, ResourceKind
from .gpu_pool import PoolError, RequestKey, assess_capacity
from .gpu_pool_client import PoolClient, validate_pool_snapshot
from .resource_experience import explain_workload_memory, resolve_workload_memory_mib, summarize_resource_measurements


class SharedGpuHost:
    def __init__(self, host: Any, socket_path: Path, namespace: str,
                 server_uid: int | None = None):
        self.host = host
        self.socket_path = Path(socket_path).absolute()
        if "\x00" in str(self.socket_path):
            raise ValueError("GPU pool socket path contains NUL")
        RequestKey(os.getuid(), namespace, "run", "execution", "attempt")
        self.namespace = namespace
        self.server_uid = server_uid
        self.client = PoolClient(self.socket_path, expected_server_uid=server_uid)
        self.owned_samples: dict[str, dict[str, Any]] = {}
        self.sampled_at: dict[str, float] = {}

    @property
    def configuration(self) -> dict[str, Any]:
        return {"socket_path": str(self.socket_path), "namespace": self.namespace,
                "server_uid": self.server_uid}

    def key(self, run_id: str, execution_id: str, attempt_id: str) -> dict[str, str]:
        value = RequestKey(os.getuid(), self.namespace, run_id, execution_id, attempt_id).to_dict()
        return {name: item for name, item in value.items() if name != "uid"}

    def measurements(self, run_id: str, role: str, component_id: str, family: str,
                     ids: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        """Project this run's closed workload records, never another owner's work."""
        records = []
        for attempt in self.host.store.inspect("SELECT * FROM execution_attempts WHERE run_id=? AND kind='PROCESS' AND state='CLOSED' ORDER BY ended_at DESC,attempt_id", (run_id,)):
            if ids is not None and attempt["attempt_id"] not in ids: continue
            spec = self.host.store.find_fact(run_id, "ExperimentPreregistered", attempt["experiment_id"])
            if spec is None or spec["execution_id"] != attempt["execution_id"]: continue
            payload = spec["payload"]; contract = payload.get("execution_contract", {})
            if payload.get("component_id") != component_id or payload.get("method_family_id") != family: continue
            command = self.host._tool_command(contract.get("source_submit_command_id"))
            if command is None or command["run_id"] != run_id: continue
            data = json.loads(command["request_json"]); request = data.get("source_request", {})
            if request.get("role") != role or request.get("component_id") != component_id or request.get("method_family_id") != family: continue
            estimate = data.get("source_snapshot", {}).get("arguments", {}).get("resource_estimate")
            if estimate is None: continue
            estimate = ResourceEstimate.from_dict(estimate)
            observation = json.loads(attempt["observation_json"])
            if observation.get("synthetic_before_spawn") or observation.get("resource_wait_before_exec"):
                continue
            records.append({"attempt_id": attempt["attempt_id"], "kind": "PROCESS", "state": "CLOSED",
                "workload": dict(estimate.workload), "resource_usage": observation.get("resource_usage"),
                "admitted_workload_memory_mib": contract.get("resource_intent", {}).get("workload_memory_mib"),
                "oom": observation.get("resource_failure") == "gpu_out_of_memory",
                "terminal_status": attempt["terminal_reason"], "ended_at": attempt["ended_at"]})
        return summarize_resource_measurements(records)

    def resolve_arguments(self, request: Any, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Capture existing evidence IDs on first submit, preserving raw caller bytes."""
        result = dict(arguments)
        if "resource_estimate" not in result: return result
        estimate = ResourceEstimate.from_dict(result["resource_estimate"])
        records = self.measurements(request.run_id, request.role, request.component_id, request.method_family_id)
        matching = [record for record in records if record["workload"] == dict(estimate.workload)
                    and (request.schema_version >= 27 or record["measurement_status"] == "complete" or record["oom"])]
        # Validate explicit references too; they cannot be replaced by automatic matches.
        if estimate.measurement_attempt_ids:
            requested = self.measurements(request.run_id, request.role, request.component_id,
                                          request.method_family_id, estimate.measurement_attempt_ids)
            resolve_workload_memory_mib(1, estimate, requested)
        selected = list(estimate.measurement_attempt_ids)
        selected.extend(record["attempt_id"] for record in matching if record["attempt_id"] not in selected)
        result["resource_estimate"] = {**estimate.to_dict(), "measurement_attempt_ids": sorted(selected[:32])}
        return result

    def workload_budget(self, request: Any, arguments: Mapping[str, Any], floor: int) -> int:
        if not arguments.get("resource_estimate") and floor == 0:
            return self.host._research_contract(request.run_id).document["budget"].get("initial_vram_per_experiment", 0)
        return self.budget_plan(request, arguments, floor)["resolved_workload_memory_mib"]

    def budget_plan(self, request: Any, arguments: Mapping[str, Any], floor: int) -> dict[str, Any]:
        budget = self.host._research_contract(request.run_id).document["budget"]
        initial = budget.get("initial_vram_per_experiment")
        estimate = ResourceEstimate.from_dict(arguments["resource_estimate"]) if "resource_estimate" in arguments else None
        records = [] if estimate is None else self.measurements(request.run_id, request.role, request.component_id,
                                   request.method_family_id, estimate.measurement_attempt_ids)
        return explain_workload_memory(floor, estimate, records, initial_memory_mib=initial,
                                      adaptive=request.schema_version >= 27)

    def plan_intent(self, request: Any, arguments: Mapping[str, Any], intent: ResourceIntent) -> ResourceIntent:
        if request.schema_version < 27: return intent
        from dataclasses import replace
        floor = self.host._research_contract(request.run_id).document["budget"]["min_vram_per_experiment"]
        return replace(intent, sharing=self.budget_plan(request, arguments, floor)["sharing"])

    def snapshot(self):
        # A single bounded transport recheck is read-only and spends no Agent
        # invocation. Capability/auth failures do not benefit from repetition.
        try: return self.client.request("snapshot", {})
        except PoolError as exc:
            if exc.code != "unavailable": raise
        return self.client.request("snapshot", {})

    def assess_intent(self, intent: ResourceIntent) -> dict[str, Any]:
        snapshot = self.snapshot()
        validate_pool_snapshot(snapshot, aci_version=27, allowed_gpu_uuids=intent.allowed_gpu_uuids)
        return {**assess_capacity(snapshot["devices"], intent.to_dict()), "observed_at": snapshot["sampled_at"]}

    def resources(self, request: Any, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        from .profile_v4 import restrict_profile_capabilities
        document = self.host._research_contract(request.run_id).document
        execution = self.host._run_config(request.run_id)["execution"]
        history = self.measurements(request.run_id, request.role, request.component_id, request.method_family_id)
        response = {"mode": "shared-pool", "observed_at": time.time(), "devices": [], "measurements": history,
                    "estimate_policy": "max_profile_submitted_measured_headroom", "measurement_scope": "current_run_role_component_family",
                    "profile_workload_floor_mib": document["budget"].get("min_vram_per_experiment", 0),
                    "profile_reserve_memory_mib": document["budget"].get("gpu_vram_reserve_mb", 0),
                    "omitted_estimate_behavior": "unestimated_profile_floor_only"}
        if "initial_vram_per_experiment" in document["budget"]:
            response.update(profile_initial_workload_mib=document["budget"]["initial_vram_per_experiment"],
                estimate_policy="initial_until_measured_success_then_max_profile_submitted_measured_headroom",
                omitted_estimate_behavior="unestimated_profile_initial_only")
        if not execution["allow_gpu"]: return {**response, "status": "cpu_only"}
        if request.schema_version >= 27:
            arguments = self.resolve_arguments(request, arguments or {})
            response["budget_preview"] = self.budget_plan(request, arguments, document["budget"]["min_vram_per_experiment"])
            response["estimate_policy"] = "evidence_adaptive_25_15_10_percent"
            response["profile_reserve_percent"] = document["budget"].get("gpu_vram_reserve_percent", 0)
            response["budget_preview"]["reserve_memory_mib"] = gpu_memory_reserve_mib(
                document["budget"], response["budget_preview"]["resolved_workload_memory_mib"])
        allowed = {gpu.uuid for gpu in restrict_profile_capabilities(document, self.host.compute_resources.snapshot()).gpus}
        try: snapshot = self.snapshot()
        except PoolError as error: return {**response, "status": "unavailable", "reason": error.code}
        fields = {"gpu_uuid", "total_memory_mib", "free_memory_mib", "used_memory_mib", "reserve_memory_mib",
                  "outstanding_memory_mib", "active_commitments", "unknown_attribution_commitments", "allocatable_memory_mib"}
        devices = snapshot.get("devices")
        if not isinstance(devices, list) or not all(isinstance(item, Mapping) and fields <= set(item) for item in devices):
            raise PoolError("invalid_response", "pool snapshot lacks capacity fields")
        if request.schema_version >= 27:
            try: validate_pool_snapshot(snapshot, aci_version=request.schema_version, allowed_gpu_uuids=allowed)
            except PoolError as error:
                return {**response, "status": error.code, "reason": str(error)}
            fields |= {"pool_reserve_memory_mib", "system_reserved_memory_mib", "exclusive_commitments"}
            allowed = sorted(allowed)
            if allowed:
                intent = ResourceIntent(allowed_gpu_uuids=tuple(allowed), gpu_count=(arguments or {}).get("gpus", 1),
                    workload_memory_mib=response["budget_preview"]["resolved_workload_memory_mib"],
                    reserve_memory_mib=response["budget_preview"]["reserve_memory_mib"],
                    sharing=response["budget_preview"]["sharing"])
                response["capacity_preview"] = assess_capacity(devices, intent.to_dict())
        return {**response, "status": "ok", "sampling_started_at": snapshot["sampling_started_at"],
            "observed_at": snapshot["sampled_at"], "devices": [{name: item[name] for name in fields}
                for item in devices if item["gpu_uuid"] in allowed]}

    def experiment_view(self, run_id: str, experiment_id: str) -> dict[str, Any]:
        rows = self.host.store.inspect("SELECT payload_json FROM workflow_transitions WHERE run_id=? AND workflow_id=? AND revision=1", (run_id, experiment_id))
        creation = {} if not rows else json.loads(rows[0]["payload_json"])
        command = self.host._tool_command(creation.get("source_submit_command_id"))
        source = {} if command is None else json.loads(command["request_json"])
        estimate = source.get("source_snapshot", {}).get("arguments", {}).get("resource_estimate")
        spec = self.host.store.find_fact(run_id, "ExperimentPreregistered", experiment_id)
        intent = None if spec is None else spec["payload"].get("execution_contract", {}).get("resource_intent")
        result = {"estimate_status": "unestimated" if estimate is None else "submitted",
                "resource_estimate": estimate, "resource_intent": intent}
        if intent is not None and intent.get("sharing") is not None and source.get("source_request") is not None:
            from .agent_aci import AgentRequest
            request = AgentRequest.from_dict(source["source_request"])
            result["budget"] = self.budget_plan(request, source["source_snapshot"]["arguments"],
                self.host._research_contract(run_id).document["budget"]["min_vram_per_experiment"])
        return result

    def sample_owned(self, attempt_id: str, handle: Any, uuids: tuple[str, ...], devices: Mapping[str, Any]) -> None:
        from .gpu_attribution import GpuAttributionSampler, OwnedMemoryAccumulator
        # Device observation may have outlived the caller's cached live status.
        if handle.returncode is not None: return
        now = time.monotonic()
        if now - self.sampled_at.get(attempt_id, float("-inf")) < 0.5: return
        self.sampled_at[attempt_id] = now
        evidence = GpuAttributionSampler().sample(uuids, {attempt_id: handle.process_group_id})[attempt_id]
        terminal = handle.returncode is not None
        accumulators = self.owned_samples.setdefault(attempt_id, {})
        for uuid, item in evidence.items():
            # The existing Runner handle bounds the active sampling window.
            # Child teardown can precede bwrap's root exit within this capture;
            # unrelated errors and earlier unknown samples retain their status.
            if terminal and item["reason"] in {"root_process_exited", "process_exited", "process_set_reduced"}: continue
            device = devices.get(uuid)
            if item["complete"] and (device is None or item["resident_bytes"] // (1024 * 1024) > device.used_memory_mib):
                item = {**item, "complete": False, "reason": "device_sample_incoherent", "resident_bytes": None, "processes": []}
            accumulators.setdefault(uuid, OwnedMemoryAccumulator()).sample(item)

    def finish_owned(self, attempt_id: str) -> dict[str, Any]:
        self.sampled_at.pop(attempt_id, None)
        return {uuid: accumulator.summary for uuid, accumulator in self.owned_samples.pop(attempt_id, {}).items()}

    def owned_latest_memory_mib(self, attempt_id: str, gpu_uuid: str) -> int | None:
        """Latest complete owned sample for a live decision; None when unattributed."""
        accumulator = self.owned_samples.get(attempt_id, {}).get(gpu_uuid)
        latest = None if accumulator is None else accumulator.summary.get("latest")
        if not isinstance(latest, Mapping) or latest.get("complete") is not True:
            return None
        return latest["resident_bytes"] // (1024 * 1024)

    @staticmethod
    def observe_oom(payload: dict[str, Any], stderr_path: Path) -> None:
        """Recognized workload-reported diagnosis; never infer OOM from pressure."""
        if (payload.get("terminal_reason") != "FAILED" or type(payload.get("returncode")) is not int
                or payload["returncode"] == 0 or payload.get("resource_pressure") or payload.get("synthetic_before_spawn")):
            return
        try:
            if stderr_path.is_symlink() or not stderr_path.is_file(): return
            with stderr_path.open("rb") as stream:
                stream.seek(max(0, stderr_path.stat().st_size - 16384))
                text = stream.read(16384).decode("utf-8", errors="replace")
        except OSError:
            return
        lines = [line for line in text.splitlines() if re.match(
            r"^(?:torch(?:\.cuda)?\.OutOfMemoryError: CUDA out of memory|cupy\.cuda\.memory\.OutOfMemoryError: Out of memory)", line)]
        if lines:
            payload.update(resource_failure="gpu_out_of_memory", resource_failure_excerpt=lines[-1][:512])

    @staticmethod
    def _experiment(key: Mapping[str, Any]) -> str:
        return str(key["attempt_id"]).rsplit("--process-", 1)[0]

    def _wait(self, key: Mapping[str, Any], reason: str) -> None:
        self.host.resource_waits[self._experiment(key)] = (key["run_id"], reason[:512])

    def _request(self, operation: str, key: Mapping[str, Any], **arguments: Any) -> dict[str, Any]:
        if (not isinstance(key, Mapping) or set(key) != {"namespace", "run_id", "execution_id", "attempt_id"}
                or key != self.key(key["run_id"], key["execution_id"], key["attempt_id"])):
            raise PoolError("owner_conflict", "GPU pool key differs from this Host's owner namespace")
        result = self.client.request(operation, {"key": dict(key), **arguments})
        try:
            if set(result) != {"key", "state", "gpu_uuids", "process_group_id", "reason"} or result["key"] != key:
                raise ValueError("pool response changed its request identity")
            state, uuids, pgid = result["state"], result["gpu_uuids"], result["process_group_id"]
            if state not in {"QUEUED", "RESERVED", "LAUNCHING", "BOUND", "RELEASED"}:
                raise ValueError("unknown pool state")
            if not isinstance(uuids, list) or uuids != sorted(set(uuids)):
                raise ValueError("pool grant is not a sorted unique list")
            for uuid in uuids:
                validate_gpu_uuid(uuid)
            if pgid is not None and (type(pgid) is not int or not 2 <= pgid < 2**53):
                raise ValueError("invalid pool process group")
            if state == "BOUND" and pgid is None or state in {"QUEUED", "RESERVED", "LAUNCHING"} and pgid is not None:
                raise ValueError("pool process group differs from its state")
            if state == "QUEUED" and uuids or state in {"RESERVED", "LAUNCHING", "BOUND"} and not uuids:
                raise ValueError("pool GPU identities differ from its state")
            if result["reason"] is not None and not isinstance(result["reason"], str):
                raise ValueError("invalid pool reason")
        except (ValueError, TypeError, KeyError) as error:
            raise PoolError("invalid_response", str(error)) from error
        return result

    def reserve(self, intent: ResourceIntent, key: Mapping[str, Any]) -> ResourceGrant | None:
        if not isinstance(intent, ResourceIntent):
            raise ValueError("shared GPU reservation requires ResourceIntent")
        arguments = {name: value for name, value in intent.to_dict().items() if name != "kind"}
        try:
            result = self._request("reserve", key, **arguments)
        except PoolError as error:
            if intent.sharing is not None and error.code == "invalid_argument":
                self._wait(key, "incompatible_pool")
                raise PoolError("incompatible_pool", "GPU pool rejected resource planning fields; configure a compatible pool for the new run") from error
            self._wait(key, error.code)
            if error.code == "unavailable":
                return None
            raise
        if result["state"] == "QUEUED":
            self._wait(key, result["reason"] or "capacity_unavailable")
            if result["reason"] == "capacity_requires_revision" and intent.sharing is not None:
                raise PoolError("capacity_requires_revision", "GPU request exceeds authorized device capacity; revise actual memory demand")
            return None
        if result["state"] != "RESERVED":
            raise PoolError("invalid_state", "future Process identity already launched or was released")
        uuids = tuple(result["gpu_uuids"])
        if len(uuids) != intent.gpu_count or not set(uuids).issubset(intent.allowed_gpu_uuids):
            raise PoolError("invalid_response", "pool grant differs from the accepted resource intent")
        self.host.resource_waits.pop(self._experiment(key), None)
        return (ResourceGrant(kind=ResourceKind.GPU, gpu_uuid=uuids[0]) if len(uuids) == 1
                else ResourceGrant(kind=ResourceKind.GPU, gpu_uuids=uuids))

    def activate(self, key: Mapping[str, Any], pgid: int) -> None:
        try:
            result = self._request("activate", key, process_group_id=pgid)
            if result["state"] in {"QUEUED", "RESERVED"}:
                raise PoolError("resource_wait", result["reason"] or "capacity_unavailable")
            if result["state"] != "BOUND" or result["process_group_id"] != pgid:
                raise PoolError("invalid_state", "pool did not bind this held Process group")
        except PoolError as error:
            self._wait(key, f"{error.code}: {error}")
            raise

    def release(self, key: Mapping[str, Any]) -> bool:
        try:
            result = self._request("release", key)
        except PoolError as error:
            if error.code != "not_found":
                self._wait(key, f"{error.code}: {error}")
                raise
            # A predicted future key may never have reached the service.
            self.host.resource_waits.pop(self._experiment(key), None)
            return True
        if result["state"] != "RELEASED":
            self._wait(key, result["reason"] or "pool release pending")
            return False
        self.host.resource_waits.pop(self._experiment(key), None)
        return True

    def reconcile(self) -> bool:
        """Retire closed effects; preserve nonterminal requests not yet opened.

        Failure leaves ordinary Host rows available for the next retry and only
        blocks subsequent GPU admission. CPU dispatch and publication can proceed.
        """
        pending: dict[str, tuple[str, str]] = {}
        for run in self.host.store.inspect("SELECT run_id FROM workflow_runs ORDER BY run_id"):
            run_id = run["run_id"]
            if self.host._run_config(run_id).get("gpu_pool") != self.configuration:
                continue
            run_head = self.host.store.latest_transition(run_id)
            stopping = run_head is not None and run_head["state"] in {
                "COMPLETING", "STOPPING", "COMPLETED", "FAILED", "CANCELLED"}
            for spec in self.host.store.list_facts(run_id, "ExperimentPreregistered"):
                if spec["payload"].get("execution_contract", {}).get("resource_intent") is None:
                    continue
                experiment_id, execution_id = spec["experiment_id"], spec["execution_id"]
                attempts = tuple(row for row in self.host.store.list_attempts(execution_id=execution_id)
                                 if row["run_id"] == run_id and row["workflow_id"] == experiment_id
                                 and row["kind"] == "PROCESS")
                keys = []
                for attempt in attempts:
                    if attempt["kind"] != "PROCESS" or attempt["state"] != "CLOSED":
                        continue
                    expected = self.key(run_id, execution_id, attempt["attempt_id"])
                    dispatches = self.host.store.inspect(
                        "SELECT payload_json FROM workflow_transitions WHERE workflow_id=? "
                        "AND json_extract(payload_json,'$.attempt_id')=? "
                        "AND json_extract(payload_json,'$.gpu_pool_key') IS NOT NULL",
                        (experiment_id, attempt["attempt_id"]))
                    synthetic = json.loads(attempt["observation_json"]).get("synthetic_before_spawn") is True
                    if not dispatches and synthetic:
                        keys.append(expected)
                    elif len(dispatches) == 1 and json.loads(dispatches[0]["payload_json"])["gpu_pool_key"] == expected:
                        keys.append(expected)
                    else:
                        pending[experiment_id] = (run_id, "pool owner key missing or inconsistent with closed Process")
                head = self.host.store.latest_transition(experiment_id)
                cancelled = self.host.store.inspect("SELECT command_id FROM workflow_commands WHERE command_id=?",
                                                     (f"{experiment_id}--agent-cancel",))
                if stopping or head is not None and (head["state"] == "TERMINAL" or head["next_command"] == "CANCEL") or cancelled:
                    ordinal = len(attempts) + 1
                    keys.append(self.key(run_id, execution_id, f"{experiment_id}--process-{ordinal}"))
                for key in keys:
                    try:
                        if self.release(key):
                            continue
                    except PoolError as error:
                        self._wait(key, f"{error.code}: {error}")
                    pending[experiment_id] = self.host.resource_waits[experiment_id]
        self.host.resource_waits.update(pending)
        return not pending
