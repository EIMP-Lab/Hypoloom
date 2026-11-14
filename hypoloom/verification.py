"""Host implementation verification over existing commands and Attempts.

No reviewer daemon, issue database, scientific fact or second budget ledger.
Accepted submission bytes and ordinary Process observations remain authoritative.
"""
from __future__ import annotations

import json
import base64
import os
import stat
from pathlib import Path

from .agent_aci import EmbeddedInput
from .domain import AttemptTerminalReason
from .expert_loop import AuxiliaryRequestKind, AuxiliaryTask
from .lifecycle import Command, ExperimentState, RunState
from .profile_v4 import resolve_node_runtime
from .verification_contract import MAX_PROBE_SECONDS


def review_rows(store, run_id, *, experiment_id=None, subject=None, component=None, phase=None, limit=None):
    clauses, arguments = [], [run_id, Command.REQUEST_AUXILIARY.value]
    for key, value in (("experiment_id", experiment_id), ("subject_expert", subject), ("component_id", component), ("phase", phase)):
        if value is not None:
            clauses.append(f" AND json_extract(request_json,'$.implementation_verification.{key}')=?")
            arguments.append(value)
    tail = " ORDER BY rowid"
    if limit is not None:
        tail += " DESC LIMIT ?"
        arguments.append(limit)
    rows = [{**row, "request": json.loads(row["request_json"])} for row in store.inspect(
        "SELECT * FROM workflow_commands WHERE run_id=? AND command_type=? "
        "AND json_type(request_json,'$.implementation_verification')='object'" + "".join(clauses) + tail, arguments)]
    return list(reversed(rows)) if limit is not None else rows


def accepted_review(store, row):
    attempts = store.list_attempts(command_id=row["command_id"])
    for attempt in reversed(attempts):
        observation = json.loads(attempt["observation_json"] or "{}")
        if attempt["state"] == "CLOSED" and attempt["terminal_reason"] == "SUCCEEDED" and "response" in observation:
            return {"report_ref": attempt["attempt_id"], "report": observation["response"]["typed_candidate"],
                    "ended_at": attempt["ended_at"]}
    return None


def is_probe(attempt):
    return attempt.get("kind") == "PROCESS" and str(attempt.get("execution_id", "")).endswith("--implementation-probe")


class ImplementationVerification:
    def __init__(self, host):
        self.host = host

    def enabled(self, run_id):
        product = self.host._research_contract(run_id).document.get("methodology", {}).get("controls", {}).get("product", {})
        return bool(product.get("experts", {}).get("verifier", {}).get("model"))

    def source(self, run_id, experiment_id):
        spec = self.host.store.find_fact(run_id, "ExperimentPreregistered", experiment_id)
        if spec is None: return None
        execution = spec["payload"]["execution_contract"]
        source_id = execution.get("source_submit_command_id")
        if source_id is None: return None  # Retained, non-submission contracts are not upgraded.
        command, request, snapshot, validated = self.host._submission_source(source_id)
        return spec, execution, request, snapshot, validated

    def _task(self, run_id, experiment_id, phase, source):
        store = self.host.store
        rows = review_rows(store, run_id, experiment_id=experiment_id, phase=phase)
        found = next((row for row in rows if row["request"]["implementation_verification"]["experiment_id"] == experiment_id
                      and row["request"]["implementation_verification"]["phase"] == phase), None)
        if found is not None: return found
        spec, execution, request, snapshot, validated = source
        count = store.inspect("SELECT count(*) AS n FROM workflow_commands WHERE run_id=? AND command_type=? "
            "AND json_type(request_json,'$.implementation_verification')='object'", (run_id, Command.REQUEST_AUXILIARY.value))[0]["n"]
        task = AuxiliaryTask(kind=AuxiliaryRequestKind.VERIFY_IMPLEMENTATION, run_id=run_id, ordinal=count + 1)
        value = {"experiment_id": experiment_id, "phase": phase, "subject_expert": request.role,
                 "component_id": request.component_id, "delivery_id": request.command_id,
                 "source_submit_command_id": execution["source_submit_command_id"],
                 "source_output_digest": validated.output_digest}
        if phase == "inspect":
            recent = review_rows(store, run_id, subject=request.role, component=request.component_id, limit=32)
            for old in reversed(recent):
                previous = old["request"]["implementation_verification"]
                if previous["phase"] != "review" or previous["subject_expert"] != request.role or previous["component_id"] != request.component_id: continue
                report = accepted_review(store, old)
                if report is None or report["report"]["verdict"] != "passed": continue
                prior = self.source(run_id, previous["experiment_id"])
                # Exact same-run capture equality only. Seed/config/scale changes
                # stay relevant; no invented equivalence or cross-run pass cache.
                keys = lambda args: {k: v for k, v in args.items() if k not in {"replaces_experiment_id", "description"}}
                if prior and keys(prior[3]["arguments"]) == keys(snapshot["arguments"]) and all(prior[1].get(k) == execution.get(k) for k in ("argv", "environment", "resource_intent")):
                    value["reused_from"] = report["report_ref"]
                    break
        with store.transaction() as tx:
            head = tx.latest_transition(run_id)
            tx.record_command({"command_id": task.command_id, "run_id": run_id, "workflow_id": run_id,
                "command_type": Command.REQUEST_AUXILIARY.value, "expected_revision": head["revision"],
                "request": {"task_context": task.to_dict(), "implementation_verification": value},
                "status": "accepted", "revision": head["revision"] + 1, "result": {"admitted": True}})
            tx.append_transition(self.host._transition_record(tx, run_id, run_id, "RUN", Command.REQUEST_AUXILIARY,
                RunState(head["state"]), command_id=task.command_id,
                payload={"task_context": task.to_dict(), "role": "verifier", "component_id": "verifier",
                         "implementation_verification": value}))
        row = store.inspect("SELECT * FROM workflow_commands WHERE command_id=?", (task.command_id,))[0]
        return {**row, "request": json.loads(row["request_json"])}

    def _run_review(self, row):
        from .expert_loop import task_context_from_dict
        task = task_context_from_dict(row["request"]["task_context"])
        report = accepted_review(self.host.store, row)
        if report is None:
            self.host._expert_agent_step(task.run_id, task.run_id, task, "verifier", "verifier", "implementation-verification")
            report = accepted_review(self.host.store, row)
        return report

    def _prior(self, run_id, subject, component, exclude=None):
        result = []
        selected = review_rows(self.host.store, run_id, subject=subject, component=component, limit=16)
        for row in selected:
            value = row["request"]["implementation_verification"]
            if value["subject_expert"] == subject and value["component_id"] == component and row["command_id"] != exclude:
                if report := accepted_review(self.host.store, row): result.append({**value, **report})
        return result[-16:]

    def inputs(self, task):
        row = self.host.store.inspect("SELECT * FROM workflow_commands WHERE command_id=?", (task.command_id,))[0]
        row = {**row, "request": json.loads(row["request_json"])}
        value = row["request"]["implementation_verification"]
        source = self.source(task.run_id, value["experiment_id"])
        spec, execution, author, snapshot, validated = source
        if validated.output_digest != value["source_output_digest"]:
            raise ValueError("verification source differs from captured submission")
        prior = self._prior(task.run_id, value["subject_expert"], value["component_id"], task.command_id)
        probes = self.host.store.list_attempts(execution_id=value["experiment_id"] + "--implementation-probe")
        evidence = None
        inspection = next((accepted_review(self.host.store, r) for r in review_rows(self.host.store, task.run_id,
            experiment_id=value["experiment_id"], phase="inspect")), None)
        plan = (inspection or {}).get("report", {}).get("probe")
        if value["phase"] == "review":
            last = probes[-1]
            observation = json.loads(last["observation_json"] or "{}")
            evidence = {"attempt_id": last["attempt_id"], "terminal_reason": last["terminal_reason"],
                "usable": last["state"] == "CLOSED" and last["terminal_reason"] == "SUCCEEDED"
                    and "implementation_probe" in observation and bool(observation.get("stdout", "").strip())
                    and not observation.get("synthetic_before_spawn"),
                "observation": observation}
            if plan and "checks" in plan:
                from .verification_probe import validate_result
                try:
                    assessment = validate_result(observation.get("check_results"), plan["checks"],
                        attempt_id=last["attempt_id"], report_ref=inspection["report_ref"])
                    evidence["usable"] = (last["state"] == "CLOSED" and last["terminal_reason"] == "SUCCEEDED"
                        and observation.get("implementation_probe") is True and not observation.get("synthetic_before_spawn")
                        and assessment["passed"] and not observation.get("check_results_error"))
                except (ValueError, TypeError):
                    evidence["usable"] = False
        config = self.host._run_config(task.run_id)["execution"]
        ctx = {**value, "workspace_paths": sorted(validated.workspace_files), "argv": execution["argv"],
               "implementation_description": snapshot["arguments"].get("description"),
               "environment": execution["environment"], "resource_intent": execution.get("resource_intent"),
               "max_probe_seconds": MAX_PROBE_SECONDS, "probe_evidence": evidence,
               "target": config.get("target"), "bindings": config.get("read_only_bindings", []),
               "prior_reports": prior, "finding_refs": [r["report_ref"] + "#" + f["id"] for r in prior for f in r["report"]["findings"]]}
        if plan and "checks" in plan:
            ctx.update(probe_plan=plan, probe_report_ref=inspection["report_ref"])
        resolved = {ref for r in prior if r["report"]["verdict"] == "passed" for ref in r["report"]["resolved_refs"]}
        ctx["open_blocker_refs"] = [r["report_ref"] + "#" + f["id"] for r in prior for f in r["report"]["findings"]
            if r["delivery_id"] == value["delivery_id"] and f["severity"] == "blocker" and r["report_ref"] + "#" + f["id"] not in resolved]
        def embedded(suffix, document):
            return EmbeddedInput.from_bytes(task.command_id + suffix, "application/json",
                json.dumps(document, ensure_ascii=False, sort_keys=True).encode())
        runtime = resolve_node_runtime(self.host._research_contract(task.run_id).to_dict(), None, "verifier")
        # No Goal, hypothesis, private expert memory, skills or scientific success criteria.
        files = {}
        for path, data in validated.workspace_files.items():
            try: files[path] = data.decode("utf-8")
            except UnicodeDecodeError: files[path] = {"content_base64": base64.b64encode(data).decode("ascii")}
        return (embedded("--expert-config", {"node_runtime": runtime}),
                embedded("--verification-context", ctx),
                embedded("--implementation-source", files))

    def feedback(self, run_id, role, component_id, task_id):
        if not self.enabled(run_id): return ()
        reports = self._prior(run_id, role, component_id)
        if not reports: return ()
        return (EmbeddedInput.from_bytes(task_id + "--implementation-reviews", "application/json",
            json.dumps({"reports": reports, "scope": "engineering only; repair findings and submit captured changes for independent re-review"},
                       ensure_ascii=False, sort_keys=True).encode()),)

    def status(self, run_id, experiment_id):
        reports = []
        for row in review_rows(self.host.store, run_id, experiment_id=experiment_id):
            value = row["request"]["implementation_verification"]
            if value["experiment_id"] == experiment_id:
                if report := accepted_review(self.host.store, row): reports.append({**value, **report})
        return {"reports": reports, "scope": "implementation only; no scientific verdict",
                "handoff": "While verification is pending, return waiting_for_experiment_id to release Agent capacity. Repair engineering blockers from these reports."}

    def gate(self, run_id, experiment_id):
        """Return (allowed, probe plan). Only the Host can authorize formal execution."""
        if not self.enabled(run_id): return True, None
        source = self.source(run_id, experiment_id)
        if source is None: return True, None
        row = self._task(run_id, experiment_id, "inspect", source)
        if row["request"]["implementation_verification"].get("reused_from"): return True, None
        inspection = self._run_review(row)
        if inspection is None:
            self._exhausted(run_id, experiment_id, row)
            return False, None
        verdict = inspection["report"]["verdict"]
        if verdict != "probe_required":
            self._reject(run_id, experiment_id, inspection)
            return False, None
        probes = self.host.store.list_attempts(execution_id=experiment_id + "--implementation-probe")
        if any(a["state"] == "OPEN" for a in probes): return False, None
        if not probes or probes[-1]["terminal_reason"] == "LOST" and len(probes) < self.host._effect_attempt_cap(self.host._research_contract(run_id), "process"):
            return True, {**inspection["report"]["probe"], "review_command_id": row["command_id"], "report_ref": inspection["report_ref"]}
        row = self._task(run_id, experiment_id, "review", source)
        final = self._run_review(row)
        if final is None:
            self._exhausted(run_id, experiment_id, row)
            return False, None
        if final["report"]["verdict"] == "passed": return True, None
        self._reject(run_id, experiment_id, final)
        return False, None

    def _exhausted(self, run_id, experiment_id, row):
        attempts = self.host.store.list_attempts(command_id=row["command_id"])
        if attempts and not any(a["state"] == "OPEN" for a in attempts) and self.host._agent_attempts_exhausted(self.host._research_contract(run_id), attempts):
            self._reject(run_id, experiment_id, {"report_ref": None, "report": {"verdict": "inconclusive", "summary": "Verifier attempts/budget exhausted; implementation was not judged."}})

    def _reject(self, run_id, experiment_id, report):
        self.host._synthetic_terminal(run_id, experiment_id, AttemptTerminalReason.FAILED,
            diagnostic={"implementation_verification": report, "failure_class": "implementation_not_verified"})

    def observe(self, tx, attempt, payload):
        """Close the probe without a scientific result or formal execution retry."""
        tx.append_transition(self.host._transition_record(tx, attempt["run_id"], attempt["workflow_id"], "EXPERIMENT",
            Command.OBSERVE_IMPLEMENTATION, ExperimentState.READY, next_command=Command.EXECUTE.value,
            payload={"attempt_id": attempt["attempt_id"], "implementation_probe": True}))

    def logs(self, attempt, payload):
        result = {**payload, "implementation_probe": True}
        request = payload.get("process_request", {})
        root = Path(request["cwd"])
        for name in ("stdout", "stderr"):
            path = root / request[name + "_path"]
            try:
                with path.open("rb") as stream:
                    data = stream.read(64 * 1024 + 1)
                result[name] = data[:64 * 1024].decode("utf-8", "replace")
                result[name + "_truncated"] = len(data) > 64 * 1024
            except OSError: result[name] = None
        from .verification_probe import MAX_RESULT_BYTES, RESULT_PATH, decode_result
        path = root.parent / "output" / RESULT_PATH
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode): raise ValueError("probe result must be a regular file")
                result["check_results"] = decode_result(stream.read(MAX_RESULT_BYTES + 1))
        except (OSError, ValueError, UnicodeError) as exc:
            result["check_results_error"] = "missing_probe_result" if isinstance(exc, OSError) else "invalid_probe_result"
        return result

    def recover_probe(self, attempt):
        if not is_probe(attempt): return False
        with self.host.store.transaction() as tx:
            head = tx.latest_transition(attempt["workflow_id"])
            if head["state"] == ExperimentState.EXECUTING.value: self.observe(tx, attempt, {})
        return True
