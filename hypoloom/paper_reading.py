"""Host-owned auxiliary paper reading using ordinary commands and Agent Attempts.

There is no reader daemon, separate queue database, budget ledger or scientific
state machine. Pending work is derived from accepted commands, their parent
Attempt, and existing terminal observations on every servicing tick.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from .agent_aci import AgentACIError, AgentRequest, EmbeddedInput
from .agent_budget import fold_agent_attempts, remaining_agent_budget
from .domain import AgentCommand, AttemptTerminalReason
from .lifecycle import Command, RunState
from .expert_loop import AuxiliaryRequestKind, AuxiliaryTask, task_context_from_dict
from .profile_v4 import request_literature_tools, request_node_runtime
from .paper_sources import admitted_source


def reading_state(attempts, *, cancelled, run_active, parent_active, exhausted):
    """Shared read projection; unknown retry coverage must not imply zero work."""
    if any(a["state"] == "OPEN" for a in attempts): return "running"
    if cancelled: return "cancelled"
    if attempts and attempts[-1]["terminal_reason"] == "SUCCEEDED": return "completed"
    if not run_active or not parent_active: return "cancelled"
    if not attempts: return "queued"
    if callable(exhausted): exhausted = exhausted()
    if exhausted is None: return "unknown"
    return "failed" if exhausted else "queued"


class PaperReadingHost:
    def __init__(self, host):
        self.host = host
        self.completions: dict[str, tuple[str, tuple[Any, ...], dict[str, Any]]] = {}

    def rows(self, parent: str | None = None) -> list[dict[str, Any]]:
        rows = self.host.store.inspect("SELECT * FROM workflow_commands WHERE json_type(request_json,'$.paper_reading')='object' ORDER BY rowid")
        result = []
        for row in rows:
            value = json.loads(row["request_json"])
            if parent is None or value["paper_reading"]["parent_attempt_id"] == parent:
                result.append({**row, "request": value})
        return result

    def _cancelled(self) -> set[str]:
        return {json.loads(row["request_json"])["cancel_paper_reading"] for row in self.host.store.inspect(
            "SELECT request_json FROM workflow_commands WHERE json_type(request_json,'$.cancel_paper_reading')='text'")}

    def _record(self, tx, request, command_id, value, payload):
        head = tx.latest_transition(request.run_id)
        tx.record_command({"command_id": command_id, "run_id": request.run_id, "workflow_id": request.run_id,
            "command_type": Command.REQUEST_AUXILIARY.value, "expected_revision": head["revision"],
            "request": value, "status": "accepted", "revision": head["revision"] + 1, "result": {"admitted": True}})
        tx.append_transition(self.host._transition_record(tx, request.run_id, request.run_id, "RUN",
            Command.REQUEST_AUXILIARY, RunState(head["state"]), command_id=command_id, payload=payload))

    def handle(self, request: AgentRequest, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if "read_papers" not in request_literature_tools(request):
            raise AgentACIError("tool_caller_scope", "read_papers is enabled only for the owning Literature MAIN")
        if (not isinstance(arguments, Mapping) or set(arguments) - {"action", "readings", "reading_ids", "max_wait_seconds"}
                or arguments.get("action") not in {"submit", "status", "wait", "cancel"}):
            raise AgentACIError("tool_arguments", "invalid read_papers action/fields")
        action = arguments["action"]
        wait = arguments.get("max_wait_seconds", 0)
        if type(wait) not in {int, float} or not math.isfinite(wait) or not 0 <= wait <= 600:
            raise AgentACIError("tool_arguments", "reading wait must be within 0..600 seconds")
        if "max_wait_seconds" in arguments and action != "wait":
            raise AgentACIError("tool_arguments", "max_wait_seconds belongs to wait only")
        if action == "submit":
            if self.host.store.latest_transition(request.run_id)["state"] != "ACTIVE":
                raise AgentACIError("tool_caller_unavailable", "new paper reading requires an ACTIVE run")
            if set(arguments) != {"action", "readings"}: raise AgentACIError("tool_arguments", "submit requires only readings")
            readings = arguments["readings"]
            if not isinstance(readings, list) or not 1 <= len(readings) <= 32:
                raise AgentACIError("tool_arguments", "submit requires 1..32 selected source/questions")
            workspace = getattr(self.host.running_agents.get(request.attempt_id), "tool_workspace", None)
            sources = getattr(getattr(workspace, "papers", None), "sources", [])
            seen = set(); existing = {row["request"]["paper_reading"]["reading_id"]: row for row in self.rows(request.attempt_id)}
            if len(existing.keys() | {str(item.get("reading_id")) for item in readings if isinstance(item, Mapping)}) > 32:
                raise AgentACIError("tool_arguments", "at most 32 selected reading questions per parent Attempt")
            admitted = []
            for index, item in enumerate(readings):
                prefix = f"readings[{index}]: "
                if not isinstance(item, dict) or set(item) != {"reading_id", "paper_ref", "question"}:
                    raise AgentACIError("tool_arguments", prefix + "supply exactly reading_id, paper_ref and question")
                if not isinstance(item["reading_id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", item["reading_id"]):
                    raise AgentACIError("tool_arguments", prefix + "reading_id must be 1..64 letters/digits/_.-, starting with a letter or digit")
                if item["reading_id"] in seen:
                    raise AgentACIError("tool_arguments", prefix + "reading_id duplicates another item in this submission; use a unique ID for each question")
                if not isinstance(item["question"], str) or not 1 <= len(item["question"].strip()) <= 4000 or "\x00" in item["question"]:
                    raise AgentACIError("tool_arguments", prefix + "question must contain 1..4000 nonempty characters without NUL")
                source = admitted_source(sources, item["paper_ref"])
                seen.add(item["reading_id"])
                value = {**item, "parent_attempt_id": request.attempt_id}
                if item["reading_id"] in existing:
                    if existing[item["reading_id"]]["request"]["paper_reading"] != value:
                        raise AgentACIError("reading_id_conflict", "reading_id already names a different source/question")
                else: admitted.append((value, source))
            with self.host.store.transaction() as tx:
                ordinal = len(self.rows()) + 1
                for value, source in admitted:
                    task = AuxiliaryTask(kind=AuxiliaryRequestKind.PAPER_READ, run_id=request.run_id,
                        component_id=request.component_id, expert_slot=request.role, cycle=request.task_context.key.cycle, ordinal=ordinal)
                    ordinal += 1
                    payload = {"task_context": task.to_dict(), "role": request.role, "component_id": request.component_id}
                    self._record(tx, request, task.command_id, {"task_context": task.to_dict(), "paper_reading": value,
                        "source": {**source, "parent_attempt_id": request.attempt_id}}, payload)
        else:
            if "readings" in arguments: raise AgentACIError("tool_arguments", "readings belong to submit only")
            ids = arguments.get("reading_ids")
            if ids is not None and (not isinstance(ids, list) or not ids or any(not isinstance(item, str) for item in ids)
                    or len(ids) != len(set(ids))):
                raise AgentACIError("tool_arguments", "reading_ids must be a nonempty unique string list")
            rows = self.rows(request.attempt_id)
            if ids is not None and set(ids) - {r["request"]["paper_reading"]["reading_id"] for r in rows}:
                raise AgentACIError("tool_caller_scope", "reading_ids do not belong to this parent Attempt")
            if action == "cancel": self.cancel(request, ids)
        # Dispatch on the ordinary Host tick, after primary expert work. Never
        # block the writer thread or lend the parent's frozen reservation.
        return self.status(request, arguments.get("reading_ids"))

    def _state(self, row, cancelled):
        task = task_context_from_dict(row["request"]["task_context"])
        attempts = self.host._expert_attempts(task)
        parent = self.host.store.get_attempt(row["request"]["paper_reading"]["parent_attempt_id"])
        return reading_state(attempts, cancelled=row["command_id"] in cancelled,
            run_active=self.host.store.latest_transition(row["run_id"])["state"] == "ACTIVE",
            parent_active=parent is not None and parent["state"] == "OPEN" and parent["host_generation"] == self.host.generation,
            exhausted=lambda: self.host._agent_attempts_exhausted(self.host._research_contract(row["run_id"]), attempts)), attempts

    def reason(self, request):
        if datetime.fromisoformat(request.deadline.replace("Z", "+00:00")) <= datetime.now(timezone.utc): return "parent_deadline"
        if self.host._run_wallclock_expired(request.run_id): return "run_deadline"
        if self.host._provider_retry_pending(request.run_id): return "provider_backoff"
        if self.host._at_capacity(request.run_id): return "agent_capacity"
        charges = fold_agent_attempts(a for a in self.host.store.list_attempts() if a["run_id"] == request.run_id)
        remaining = remaining_agent_budget(self.host._research_contract(request.run_id).document, request.role, charges, self.host.agent_budget)
        return "budget_capacity" if remaining is None else None

    def remaining_wall(self, task):
        row = next(row for row in self.rows() if row["command_id"] == task.command_id)
        parent = self.host.store.get_attempt(row["request"]["paper_reading"]["parent_attempt_id"])
        deadline = json.loads(parent["observation_json"])["request"]["deadline"]
        return (datetime.fromisoformat(deadline.replace("Z", "+00:00")) - datetime.now(timezone.utc)).total_seconds()

    def status(self, request, ids=None):
        result, cancelled = [], self._cancelled()
        for row in self.rows(request.attempt_id):
            job = row["request"]["paper_reading"]
            if ids is not None and job["reading_id"] not in ids: continue
            state, attempts = self._state(row, cancelled)
            item = {"reading_id": job["reading_id"], "paper_ref": job["paper_ref"], "status": state,
                    "attempt_ids": [a["attempt_id"] for a in attempts]}
            if attempts:
                observed = json.loads(attempts[-1]["observation_json"])
                if state == "completed":
                    item["answer"] = observed["response"]["typed_candidate"]
                    item["source_archive_attempt_id"] = attempts[-1]["attempt_id"]
                elif state == "failed": item["error"] = observed.get("failure_code", attempts[-1]["terminal_reason"])
            result.append(item)
        active = any(item["status"] == "running" for item in result)
        queued = any(item["status"] == "queued" for item in result)
        reason = self.reason(request) if queued else None
        blocked = queued and not active and reason is not None
        return {"readings": result, "terminal": not active and not queued,
            "status": "blocked" if blocked else "running" if active or queued else "completed",
            "wait_reason": reason, "next_action": "Cancel unstarted questions and read sources serially if no capacity can be freed." if blocked else None}

    def cancel(self, request, ids=None):
        cancelled = self._cancelled()
        for row in self.rows(request.attempt_id):
            job = row["request"]["paper_reading"]
            if ids is not None and job["reading_id"] not in ids: continue
            state, attempts = self._state(row, cancelled)
            if state in {"completed", "failed", "cancelled"}: continue
            if row["command_id"] not in cancelled and self.host.store.latest_transition(request.run_id)["state"] == "ACTIVE":
                with self.host.store.transaction() as tx:
                    self._record(tx, request, request.attempt_id + "--cancel-reading-" + job["reading_id"],
                        {"cancel_paper_reading": row["command_id"], "parent_attempt_id": request.attempt_id},
                        {"cancel_paper_reading": row["command_id"], "parent_attempt_id": request.attempt_id})
            for attempt in attempts:
                handle = self.host.running_agents.get(attempt["attempt_id"])
                if handle is not None: self.host.claude_agent_host.cancel(handle)

    def inputs(self, task):
        row = next(row for row in self.rows() if row["command_id"] == task.command_id)
        value = row["request"]
        parent = AgentRequest.from_dict(json.loads(self.host.store.get_attempt(value["paper_reading"]["parent_attempt_id"])["observation_json"])["request"])
        runtime = request_node_runtime(parent.input_artifacts)
        # Carry capacity controls, but never the parent's scientific memory.
        # Retained requests without imported windows preserve their old policy.
        memory = {k: v for k, v in runtime.get("memory", {}).items() if k in {
            "working_context_tokens", "context_window_tokens", "runtime_context_tokens",
            "max_inline_input_bytes", "input_usage_semantics", "context_metadata"}} if "context_window" in runtime else {}
        # The source and question are the whole reader context: no evaluator,
        # peer memory or target repository. The Host resolves the child's window.
        runtime = {k: v for k, v in runtime.items() if k in {"model", "effort", "summary_language", "output_language"}}
        if memory: runtime["memory"] = memory
        runtime["model"] = parent.required_model_identity
        return (EmbeddedInput.from_bytes(task.command_id + "--expert-config", "application/json", json.dumps({"node_runtime": runtime}).encode()),
            EmbeddedInput.from_bytes(task.command_id + "--paper-question", "application/json", json.dumps(value["paper_reading"]).encode()),
            EmbeddedInput.from_bytes(task.command_id + "--paper-context", "application/json", json.dumps({"sources": [value["source"]]}).encode()))

    def _defer(self, mode, request, args, kwargs):
        if request.command is AgentCommand.PAPER_READ: return False
        if not self.rows(request.attempt_id): return False
        self.cancel(request)
        if not any(a["state"] == "OPEN" for row in self.rows(request.attempt_id)
                   for a in self.host._expert_attempts(task_context_from_dict(row["request"]["task_context"]))): return False
        self.completions[request.attempt_id] = (mode, (request, *args), kwargs)
        return True

    def defer_failure(self, request, reason, code, **kwargs):
        return self._defer("failure", request, (reason, code), kwargs)

    def defer_response(self, request, response, **kwargs):
        if request.command is AgentCommand.PAPER_READ and request.command_id in self._cancelled():
            self.host._agent_failure(request, AttemptTerminalReason.CANCELLED, "paper_reading_cancelled", retryable=False,
                details={"discarded_response": response.to_dict(), **({"diagnostics": kwargs["diagnostics"]} if kwargs.get("diagnostics") else {})}, owner_transition=False)
            return True
        return self._defer("response", request, (response,), kwargs)

    def tick(self):
        progressed = False
        for parent_id, (mode, args, kwargs) in tuple(self.completions.items()):
            if any(a["state"] == "OPEN" for row in self.rows(parent_id)
                   for a in self.host._expert_attempts(task_context_from_dict(row["request"]["task_context"]))): continue
            self.completions.pop(parent_id)
            request = args[0]
            if mode == "response" and self.host.store.latest_transition(request.run_id)["state"] == "STOPPING":
                self.host._agent_failure(request, AttemptTerminalReason.CANCELLED, "run_stopping", retryable=False,
                    details={"discarded_response": args[1].to_dict(), **({"diagnostics": kwargs["diagnostics"]} if kwargs.get("diagnostics") else {})}, owner_transition=False)
            elif mode == "failure": self.host._agent_failure(*args, **kwargs)
            else: self.host._accept_agent_response(*args, **kwargs)
            self.host.agent_session_metadata.pop(parent_id, None); progressed = True
        cancelled = self._cancelled(); pending = []; active = {}
        for row in self.rows():
            task = task_context_from_dict(row["request"]["task_context"])
            state, attempts = self._state(row, cancelled)
            if state == "running": active[(row["run_id"], task.expert_slot)] = active.get((row["run_id"], task.expert_slot), 0) + 1
            if state != "queued": continue
            parent = self.host.store.get_attempt(row["request"]["paper_reading"]["parent_attempt_id"])
            if parent["attempt_id"] in self.completions or parent["attempt_id"] not in self.host.running_agents: continue
            request = AgentRequest.from_dict(json.loads(parent["observation_json"])["request"])
            if row["run_id"] in self.host.dispatch_errors or self.host.store.latest_transition(row["run_id"])["state"] != "ACTIVE": continue
            if self.reason(request) is None: pending.append((row, task, request))
        while pending:
            index = min(range(len(pending)), key=lambda i: (active.get((pending[i][0]["run_id"], pending[i][1].expert_slot), 0), i))
            row, task, parent = pending.pop(index)
            if self.reason(parent) is not None: continue
            before = len(self.host._expert_attempts(task))
            self.host._expert_agent_step(task.run_id, task.run_id, task, task.expert_slot, task.component_id, parent.method_family_id)
            if len(self.host._expert_attempts(task)) > before:
                key = (task.run_id, task.expert_slot); active[key] = active.get(key, 0) + 1; progressed = True
        return progressed

    def close(self):
        """Drain owned readers/parents through the usual poll/publication/accounting path."""
        if self.host.claude_agent_host is None: return
        rows = self.rows()
        parents = {row["request"]["paper_reading"]["parent_attempt_id"] for row in rows}
        commands = {row["command_id"] for row in rows}
        owned = [handle for handle in self.host.running_agents.values()
                 if handle.request.attempt_id in parents or handle.request.command_id in commands]
        if not owned: return
        for handle in owned: self.host.claude_agent_host.cancel(handle)
        self.host._poll_agents()
        self.tick()
        if any(self.host.store.get_attempt(handle.request.attempt_id)["state"] == "OPEN" for handle in owned):
            raise RuntimeError("paper reading shutdown did not drain all owned Attempts")
