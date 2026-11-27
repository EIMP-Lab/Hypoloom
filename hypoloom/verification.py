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

from .agent_aci import EFFICIENT_AGENT_ACI_SCHEMA_VERSION, ENGINEERING_CHECKS_ACI_SCHEMA_VERSION, EmbeddedInput
from .domain import AttemptTerminalReason
from .expert_loop import AuxiliaryRequestKind, AuxiliaryTask
from .lifecycle import Command, ExperimentState, RunState
from .profile_v4 import resolve_node_runtime
from .verification_contract import MAX_PROBE_SECONDS, MAX_PROBE_REPAIRS, probe_actions
from .verification_history import history_view


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


def _recorded_actions(observation):
    request = observation.get("request", {})
    if request.get("schema_version", 0) >= ENGINEERING_CHECKS_ACI_SCHEMA_VERSION:
        for item in request.get("input_artifacts", ()):
            if item["ref"].endswith("--verification-context"):
                return json.loads(EmbeddedInput.from_dict(item).content_bytes()).get("probe_actions", {})
    return {}


def accepted_review(store, row):
    attempts = store.list_attempts(command_id=row["command_id"])
    for attempt in reversed(attempts):
        observation = json.loads(attempt["observation_json"] or "{}")
        if attempt["state"] == "CLOSED" and attempt["terminal_reason"] == "SUCCEEDED" and "response" in observation:
            return {"report_ref": attempt["attempt_id"], "report": observation["response"]["typed_candidate"],
                    "ended_at": attempt["ended_at"], **({"probe_actions": actions} if (actions := _recorded_actions(observation)) else {})}
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

    def _repair_limit(self, run_id):
        from .agent_aci import PROBE_RECOVERY_ACI_SCHEMA_VERSION
        run = self.host.store.get_run(run_id)
        if int(run["agent_aci_version"]) < PROBE_RECOVERY_ACI_SCHEMA_VERSION: return None
        model_attempts, _ = self.host._agent_attempt_limits(self.host._research_contract(run_id))
        return min(MAX_PROBE_REPAIRS, max(0, model_attempts - 1))

    def _task(self, run_id, experiment_id, phase, source, *, probe_attempt=None, plan=None, probe_revision=0):
        store = self.host.store
        rows = review_rows(store, run_id, experiment_id=experiment_id, phase=phase)
        found = next((row for row in rows if row["request"]["implementation_verification"]["experiment_id"] == experiment_id
                      and row["request"]["implementation_verification"]["phase"] == phase
                      and row["request"]["implementation_verification"].get("probe_attempt_id") ==
                          (probe_attempt["attempt_id"] if probe_attempt else None)), None)
        if found is not None: return found
        spec, execution, request, snapshot, validated = source
        count = store.inspect("SELECT count(*) AS n FROM workflow_commands WHERE run_id=? AND command_type=? "
            "AND json_type(request_json,'$.implementation_verification')='object'", (run_id, Command.REQUEST_AUXILIARY.value))[0]["n"]
        task = AuxiliaryTask(kind=AuxiliaryRequestKind.VERIFY_IMPLEMENTATION, run_id=run_id, ordinal=count + 1)
        value = {"experiment_id": experiment_id, "phase": phase, "subject_expert": request.role,
                 "component_id": request.component_id, "delivery_id": request.command_id,
                 "source_submit_command_id": execution["source_submit_command_id"],
                 "source_output_digest": validated.output_digest}
        repair_limit = self._repair_limit(run_id)
        if repair_limit is not None:
            value.update(probe_revision=probe_revision, probe_repair_limit=repair_limit)
        if probe_attempt is not None:
            value.update(probe_attempt_id=probe_attempt["attempt_id"], probe_report_ref=plan["report_ref"])
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
                    if (request.schema_version >= EFFICIENT_AGENT_ACI_SCHEMA_VERSION
                            and history_view(store, run_id, value, task.command_id)["open_blocker_refs"]):
                        break
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
        aci_version = int(self.host.store.get_run(task.run_id)["agent_aci_version"])
        if validated.output_digest != value["source_output_digest"]:
            raise ValueError("verification source differs from captured submission")
        compact = aci_version >= EFFICIENT_AGENT_ACI_SCHEMA_VERSION
        prior = [] if compact else self._prior(task.run_id, value["subject_expert"], value["component_id"], task.command_id)
        probes = self.host.store.list_attempts(execution_id=value["experiment_id"] + "--implementation-probe")
        evidence = None
        if value.get("probe_report_ref"):
            row = self.host.store.get_attempt(value["probe_report_ref"])
            observation = json.loads(row["observation_json"])
            inspection = {"report_ref": row["attempt_id"], "report": observation["response"]["typed_candidate"],
                          "probe_actions": _recorded_actions(observation)}
        else:
            inspection = next((accepted_review(self.host.store, r) for r in review_rows(self.host.store, task.run_id,
                experiment_id=value["experiment_id"], phase="inspect")), None)
        plan = (inspection or {}).get("report", {}).get("probe")
        actions = {}
        if value["phase"] == "review":
            last = (next(p for p in probes if p["attempt_id"] == value["probe_attempt_id"])
                    if value.get("probe_attempt_id") else probes[-1])
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
                    observed = observation.get("implementation_probe") is True and not observation.get("synthetic_before_spawn") and not observation.get("check_results_error")
                    if aci_version >= ENGINEERING_CHECKS_ACI_SCHEMA_VERSION and observed:
                        actions = probe_actions(assessment["checks"], plan["checks"],
                            author_smoke=bool(snapshot["arguments"].get("entrypoint_smoke")),
                            previous=inspection.get("probe_actions", {}))
                    evidence["usable"] = last["state"] == "CLOSED" and last["terminal_reason"] == "SUCCEEDED" and observed and assessment["passed"]
                except (ValueError, TypeError):
                    evidence["usable"] = False
        config = self.host._run_config(task.run_id)["execution"]
        ctx = {**value, "workspace_paths": sorted(validated.workspace_files), "argv": execution["argv"],
               "implementation_description": snapshot["arguments"].get("description"),
               "environment": execution["environment"], "resource_intent": execution.get("resource_intent"),
               "max_probe_seconds": MAX_PROBE_SECONDS, "probe_evidence": evidence,
               "target": config.get("target"), "bindings": config.get("read_only_bindings", []),
               "prior_reports": prior, "finding_refs": [r["report_ref"] + "#" + f["id"] for r in prior for f in r["report"]["findings"]]}
        contract = self.host._research_contract(task.run_id).to_dict()
        if preflight := (spec.get("payload", {}).get("goal_evaluation") or {}).get("preflight"):
            ctx["goal_preflight"] = preflight
        if aci_version >= ENGINEERING_CHECKS_ACI_SCHEMA_VERSION:
            goal = contract.get("methodology", {}).get("controls", {}).get("product", {}).get("goal", {})
            if requirements := goal.get("implementation_requirements"):
                ctx["implementation_requirements"] = requirements
            if actions:
                ctx["probe_actions"] = actions
        if "probe_repair_limit" in value:
            ctx["probe_repairs_remaining"] = max(0, value["probe_repair_limit"] - value["probe_revision"])
        if "entrypoint_smoke" in snapshot["arguments"]:
            ctx["entrypoint_smoke"] = snapshot["arguments"]["entrypoint_smoke"]
        if plan and "checks" in plan:
            ctx.update(probe_plan=plan, probe_report_ref=inspection["report_ref"])
            from .agent_aci import VERIFIER_COMPLETION_ACI_SCHEMA_VERSION
            if (value["phase"] == "review"
                    and aci_version >= VERIFIER_COMPLETION_ACI_SCHEMA_VERSION):
                # The report_ref already identifies this exact plan. Keep the
                # historical findings and every other plan in the reading set.
                for entry in prior:
                    if entry["report_ref"] == inspection["report_ref"]:
                        entry["report"] = {k: v for k, v in entry["report"].items() if k != "probe"}
        resolved = {ref for r in prior if r["report"]["verdict"] == "passed" for ref in r["report"]["resolved_refs"]}
        ctx["open_blocker_refs"] = [r["report_ref"] + "#" + f["id"] for r in prior for f in r["report"]["findings"]
            if r["delivery_id"] == value["delivery_id"] and f["severity"] == "blocker" and r["report_ref"] + "#" + f["id"] not in resolved]
        if compact:
            ctx.update(history_view(self.host.store, task.run_id, value, task.command_id))
            if value["phase"] == "inspect":
                predecessor = snapshot["arguments"].get("replaces_experiment_id")
                previous = self.source(task.run_id, predecessor) if predecessor else None
                if previous:
                    before = previous[4].workspace_files
                    after = validated.workspace_files
                    ctx["source_delta"] = {"base_experiment_id": predecessor,
                        "changed_paths": sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p)),
                        "changed_execution_fields": [k for k in ("argv", "environment", "resource_intent")
                            if previous[1].get(k) != execution.get(k)]}
        def embedded(suffix, document):
            return EmbeddedInput.from_bytes(task.command_id + suffix, "application/json",
                json.dumps(document, ensure_ascii=False, sort_keys=True).encode())
        runtime = resolve_node_runtime(contract, None, "verifier")
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
        # Source and probe files remain in the verifier's own immutable inputs.
        # Authors need the verdict and actions, not another copy of every probe.
        reports = [{**row, "report": {k: v for k, v in row["report"].items() if k != "probe"}} for row in reports]
        current = [row for row in reports if row["delivery_id"] == task_id]
        actions = [{"experiment_id": row["experiment_id"], "report_ref": row["report_ref"],
            "verdict": row["report"]["verdict"], "summary": row["report"]["summary"],
            "findings": row["report"]["findings"], **({"probe_actions": row["probe_actions"]} if row.get("probe_actions") else {})}
            for row in current[-2:]]
        return (EmbeddedInput.from_bytes(task_id + "--implementation-reviews", "application/json",
            json.dumps({"current_actions": actions, "reports": reports,
                "scope": "Engineering only. Read current_actions first. implementation_not_verified means formal training never started; "
                    "an empty training log is expected. Use these reports, not searches for inaccessible Host files. "
                    "Repair demonstrated candidate blockers and submit captured changes for independent re-review. "
                    "Probe defects belong to verifier recovery, not author code repairs."},
                       ensure_ascii=False, sort_keys=True).encode()),) + self.memory(run_id, role, component_id, task_id)

    def memory(self, run_id, role, component_id, task_id):
        from .agent_aci import ENGINEERING_MEMORY_ACI_SCHEMA_VERSION, READABLE_ENGINEERING_INPUTS_ACI_SCHEMA_VERSION, canonical_json
        from .engineering_memory import SUFFIX, MAX_MEMORY_BYTES, LEGACY_MAX_MEMORY_BYTES, memory_document, verified_entry
        version = int(self.host.store.get_run(run_id)["agent_aci_version"])
        if version < ENGINEERING_MEMORY_ACI_SCHEMA_VERSION:
            return ()
        entries = []
        for row in reversed(review_rows(self.host.store, run_id, subject=role, component=component_id, phase="review", limit=16)):
            commission = row["request"]["implementation_verification"]
            accepted = accepted_review(self.host.store, row)
            if accepted is None or accepted["report"].get("verdict") != "passed": continue
            probe = self.host.store.get_attempt(commission.get("probe_attempt_id"))
            plan = self.host.store.get_attempt(commission.get("probe_report_ref"))
            if probe is None or plan is None: continue
            plan_rows = self.host.store.inspect("SELECT request_json FROM workflow_commands WHERE run_id=? AND command_id=?",
                (run_id, plan["command_id"]))
            fact = self.host.store.find_fact(run_id, "ExperimentPreregistered", commission["experiment_id"])
            if not plan_rows or fact is None: continue
            entry = verified_entry(run_id=run_id, role=role, component_id=component_id,
                commission=commission, review=self.host.store.get_attempt(accepted["report_ref"]),
                plan_commission=json.loads(plan_rows[0]["request_json"]).get("implementation_verification", {}),
                plan_attempt=plan, probe=probe, execution=fact["payload"]["execution_contract"])
            if entry is not None: entries.append(entry)
        readable = version >= READABLE_ENGINEERING_INPUTS_ACI_SCHEMA_VERSION
        document = memory_document(run_id, role, component_id, entries,
            max_bytes=MAX_MEMORY_BYTES if readable else LEGACY_MAX_MEMORY_BYTES, include_omissions=readable)
        return () if document is None else (EmbeddedInput.from_bytes(task_id + SUFFIX, "application/json", canonical_json(document)),)

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
        if self._repair_limit(run_id) is not None:
            return self._recoverable_gate(run_id, experiment_id, source, row, inspection)
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

    def _recoverable_gate(self, run_id, experiment_id, source, row, report):
        """Replay a bounded sequence of plans, Processes and independent reviews.

        Each review names its exact closed probe and plan. Repeated ticks and
        restarts reuse those commands; no candidate replacement is needed.
        """
        probes = self.host.store.list_attempts(execution_id=experiment_id + "--implementation-probe")
        revision, lost = 0, 0
        for probe in probes:
            if report["report"]["verdict"] != "probe_required": break
            if probe["state"] == "OPEN": return False, None
            if probe["terminal_reason"] == "LOST":
                lost += 1
                if lost < self.host._effect_attempt_cap(self.host._research_contract(run_id), "process"):
                    continue
            row = self._task(run_id, experiment_id, "review", source,
                probe_attempt=probe, plan=report, probe_revision=revision)
            report = self._run_review(row)
            if report is None:
                self._exhausted(run_id, experiment_id, row)
                return False, None
            if report["report"]["verdict"] == "probe_required":
                revision += 1
                lost = 0
        verdict = report["report"]["verdict"]
        if verdict == "passed": return True, None
        if verdict == "probe_required":
            return True, {**report["report"]["probe"], "review_command_id": row["command_id"], "report_ref": report["report_ref"]}
        self._reject(run_id, experiment_id, report)
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
