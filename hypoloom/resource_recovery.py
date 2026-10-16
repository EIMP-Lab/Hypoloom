"""Pre-submit GPU outages use existing workflow transitions and Agent ownership."""
from __future__ import annotations

import json
import time

from .control_protocol import ControlResponse
from .domain import AttemptTerminalReason
from .lifecycle import Command, RunState
from .expert_loop import ResearchNodeTask

POOL_INFRASTRUCTURE_ERRORS = frozenset({"incompatible_pool", "unavailable", "unauthorized",
                                       "unauthorized_server", "invalid_response"})


class ResourceRecovery:
    def __init__(self, host):
        self.host = host

    def blocked(self, run_id):
        return self.host.store.cached_read(("resource-blocked", run_id), lambda: read_resource_blocks(self.host.store, run_id))

    def for_request(self, request):
        return self.blocked(request.run_id).get(request.command_id) if request.schema_version >= 28 else None

    def record(self, request, tool, code):
        host = self.host
        if (request.schema_version < 28 or code not in POOL_INFRASTRUCTURE_ERRORS
                or not isinstance(request.task_context, ResearchNodeTask) or host._owned_tool_experiments(request)):
            return
        if self.for_request(request): return
        block = {"node_command_id": request.command_id, "attempt_id": request.attempt_id,
            "role": request.role, "component_id": request.component_id, "tool": tool,
            "code": code, "observed_at": time.time()}
        with host.store.transaction() as tx:
            head = tx.latest_transition(request.run_id)
            if head is None or head["state"] != RunState.ACTIVE.value: return
            tx.append_transition(host._transition_record(tx, request.run_id, request.run_id, "RUN",
                Command.RESOURCE_BLOCKED, RunState.ACTIVE, next_command=head["next_command"],
                payload={"resource_blocked": block}))

    def close(self, request, *, details=None):
        block = self.for_request(request)
        if not block or self.host.store.latest_transition(request.run_id)["state"] == RunState.STOPPING.value: return False
        self.host._agent_failure(request, AttemptTerminalReason.FAILED, "resource_infrastructure_blocked",
            retryable=True, owner_transition=False,
            details={**(details or {}), "resource_blocked": block})
        return True

    def recheck(self, request):
        host = self.host
        if set(request.payload) != {"run_id", "reason"} or request.payload["reason"] != "operator":
            raise ValueError("RESOURCE_RECHECK requires run_id and the operator reason")
        run_id = request.payload["run_id"]
        if not isinstance(run_id, str): raise ValueError("run_id must be a string")
        def operation(tx):
            from .agent_aci import AgentRequest
            head = tx.latest_transition(run_id)
            if head is None or head["state"] not in {RunState.CREATED.value, RunState.ACTIVE.value}:
                raise ValueError("resource recheck requires a CREATED or ACTIVE run")
            if host.shared_gpu is None: raise ValueError("shared GPU pool is not configured")
            blocks = self.blocked(run_id)
            if not blocks: raise ValueError("there is no blocked GPU node to recheck")
            for block in blocks.values():
                attempt = host.store.get_attempt(block["attempt_id"])
                if attempt["state"] != "CLOSED":
                    raise ValueError("wait for the blocked Agent to finish cleanup before rechecking")
                source = AgentRequest.from_dict(json.loads(attempt["observation_json"])["request"])
                observed = host.shared_gpu.resources(source)
                if observed.get("status") != "ok":
                    raise ValueError("GPU pool is still unavailable: " + str(observed.get("status")))
            row = host._transition_record(tx, run_id, run_id, "RUN", Command.RESOURCE_RECHECK,
                RunState(head["state"]), request.request_id, next_command=head["next_command"],
                payload={"resource_recheck": {"reason": "operator", "checked_at": time.time(),
                    "node_command_ids": sorted(blocks)}})
            tx.append_transition(row)
            return {"status": "accepted", "revision": row["revision"], "result": {"run_id": run_id,
                "run_state": head["state"], "rechecked_nodes": sorted(blocks)}}
        stored = host.store.execute_command({"command_id": request.request_id, "run_id": run_id,
            "workflow_id": run_id, "expected_revision": request.expected_revision,
            "command_type": Command.RESOURCE_RECHECK.value, "request": request.to_mapping()}, operation)
        if stored.status != "accepted":
            error = stored.error or {"code": "rejected", "message": "resource recheck rejected"}
            return ControlResponse.failure(request.request_id, "rejected", error["code"], error["message"], stored.revision)
        return ControlResponse.accepted(request.request_id, stored.revision, stored.result or {})


def read_resource_blocks(store, run_id):
    blocks = {}
    rows = store.inspect("SELECT c.command_type,t.payload_json FROM workflow_transitions t "
        "JOIN workflow_commands c ON c.command_id=t.command_id WHERE t.run_id=? "
        "AND c.command_type IN (?,?) ORDER BY t.rowid",
        (run_id, Command.RESOURCE_BLOCKED.value, Command.RESOURCE_RECHECK.value))
    for row in rows:
        payload = json.loads(row["payload_json"])
        if row["command_type"] == Command.RESOURCE_RECHECK.value:
            for command in payload["resource_recheck"]["node_command_ids"]:
                blocks.pop(command, None)
        else:
            block = payload["resource_blocked"]
            blocks[block["node_command_id"]] = block
    return blocks
