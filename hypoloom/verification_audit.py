"""Independent evidence checks for engineering Processes, without Host reducers."""
import json


def engineering_memory_binding(request, consumer, commands, attempts, observations):
    """Reconcile each recalled claim with the historical capture and Process."""
    import hashlib
    from .agent_aci import ENGINEERING_MEMORY_ACI_SCHEMA_VERSION, READABLE_ENGINEERING_INPUTS_ACI_SCHEMA_VERSION, canonical_json
    from .engineering_memory import MAX_MEMORY_BYTES, LEGACY_MAX_MEMORY_BYTES, MAX_MEMORY_ENTRIES, SCOPE, SUFFIX
    from .verification_probe import validate_result
    inputs = [i for i in request.input_artifacts if i.ref.endswith(SUFFIX)]
    if not inputs: return
    assert request.schema_version >= ENGINEERING_MEMORY_ACI_SCHEMA_VERSION and len(inputs) == 1
    assert request.command.value in {"EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION", "RESULT_ANALYSIS"}
    item = inputs[0]
    assert item.ref == request.command_id + SUFFIX and item.ref in request.immutable_context_refs
    readable = request.schema_version >= READABLE_ENGINEERING_INPUTS_ACI_SCHEMA_VERSION
    assert len(item.content_bytes()) <= (MAX_MEMORY_BYTES if readable else LEGACY_MAX_MEMORY_BYTES)
    memory = json.loads(item.content_bytes())
    assert set(memory) == {"schema_version", "run_id", "expert", "component_id", "scope", "entries", "omitted_entries", "version_id"}
    assert memory["schema_version"] == 1 and memory["scope"] == SCOPE
    assert (memory["run_id"], memory["expert"], memory["component_id"]) == (request.run_id, request.role, request.component_id)
    assert type(memory["omitted_entries"]) is int and memory["omitted_entries"] >= 0
    assert memory["version_id"] == hashlib.sha256(canonical_json({k: v for k, v in memory.items() if k != "version_id"})).hexdigest()
    assert (0 if readable and memory["omitted_entries"] else 1) <= len(memory["entries"]) <= MAX_MEMORY_ENTRIES
    seen = set()
    for entry in memory["entries"]:
        assert set(entry) == {"experiment_id", "source_submit_command_id", "source_output_digest", "review_attempt_id",
            "probe_attempt_id", "probe_report_ref", "covered", "uncovered", "checks"}
        identity = (entry["source_output_digest"], entry["probe_attempt_id"])
        assert identity not in seen
        seen.add(identity)
        review, plan, probe = (attempts[entry[k]] for k in ("review_attempt_id", "probe_report_ref", "probe_attempt_id"))
        for row, kind in ((review, "AGENT"), (plan, "AGENT"), (probe, "PROCESS")):
            assert row["run_id"] == request.run_id and row["kind"] == kind
            assert row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED"
        assert plan["ended_at"] <= probe["started_at"] <= probe["ended_at"] <= review["started_at"] <= review["ended_at"] <= consumer["started_at"]
        assert probe["experiment_id"] == entry["experiment_id"]
        assert probe["execution_id"] == entry["experiment_id"] + "--implementation-probe"
        commissioning = [json.loads(commands[r["command_id"]]["request_json"])["implementation_verification"] for r in (review, plan)]
        for job in commissioning:
            assert job["subject_expert"] == request.role and job["component_id"] == request.component_id
            assert all(job[k] == entry[k] for k in ("experiment_id", "source_submit_command_id", "source_output_digest"))
        assert commissioning[0]["phase"] == "review"
        assert commissioning[0]["probe_attempt_id"] == probe["attempt_id"] and commissioning[0]["probe_report_ref"] == plan["attempt_id"]
        submit = commands[entry["source_submit_command_id"]]
        assert submit["run_id"] == request.run_id and submit["workflow_id"] == entry["experiment_id"]
        assert json.loads(submit["request_json"])["source_snapshot"]["output_digest"] == entry["source_output_digest"]
        reviewed, planned, actual = (observations[r["attempt_id"]] for r in (review, plan, probe))
        assert all(o["request"]["command"] == "VERIFY_IMPLEMENTATION" and o["request"]["role"] == "verifier" for o in (reviewed, planned))
        report, plan_report = (o["response"]["typed_candidate"] for o in (reviewed, planned))
        assert report["verdict"] == "passed" and plan_report["verdict"] == "probe_required"
        assert actual.get("implementation_probe") is True and actual.get("returncode") == 0
        assert not actual.get("synthetic_before_spawn") and not actual.get("check_results_error")
        checks = plan_report["probe"]["checks"]
        assessed = validate_result(actual["check_results"], checks, attempt_id=probe["attempt_id"], report_ref=plan["attempt_id"])
        assert assessed["passed"]
        assert entry["covered"] == report["coverage"]["covered"] and entry["uncovered"] == report["coverage"]["uncovered"]
        covered = {i for c in entry["covered"] for i in c["check_ids"]}
        assert {c["id"] for c in checks if c["required"]} <= covered <= {c["id"] for c in assessed["checks"] if c["status"] in {"PASS", "MEASURED"}}
        assert not any(c["blocking"] for c in entry["uncovered"]) and not any(f["severity"] == "blocker" for f in report["findings"])
        declared = {c["id"]: c for c in checks}
        assert entry["checks"] == [{"id": c["id"], "kind": c["kind"], "purpose": declared[c["id"]]["purpose"],
            "basis": declared[c["id"]]["basis"], "status": c["status"], "measurement": c["measurement"]}
            for c in assessed["checks"] if c["id"] in covered]


def formal_gate(attempt, context, commands, attempts, observations):
    if context is None or not context["contract"].get("source_submit_command_id"): return
    matches = []
    for command in commands.values():
        value = json.loads(command["request_json"]).get("implementation_verification", {})
        if value.get("experiment_id") != attempt["experiment_id"]: continue
        assert value["source_output_digest"] == context["contract"]["source_agent_output_digest"]
        if ref := value.get("reused_from"):
            matches.append(ref)
            prior_request = observations[ref]["request"]
            prior = json.loads(commands[prior_request["command_id"]]["request_json"])["implementation_verification"]
            original = json.loads(commands[prior["source_submit_command_id"]]["request_json"])["source_snapshot"]["arguments"]
            current = json.loads(commands[value["source_submit_command_id"]]["request_json"])["source_snapshot"]["arguments"]
            selected = lambda args: {k: v for k, v in args.items() if k not in {"replaces_experiment_id", "description"}}
            assert selected(original) == selected(current)
        elif value["phase"] == "review":
            matches.extend(ref for ref, row in attempts.items() if row["command_id"] == command["command_id"]
                           and row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED"
                           and observations[ref].get("response", {}).get("typed_candidate", {}).get("verdict") == "passed")
    assert len(matches) == 1
    ref = matches[0]
    assert attempts[ref]["run_id"] == attempt["run_id"] and attempts[ref]["ended_at"] <= attempt["started_at"]
    assert observations[ref]["response"]["typed_candidate"]["verdict"] == "passed"


def request_binding(request, command, commands, attempts, observations):
    from .agent_aci import WorkspaceArtifact
    value = json.loads(command["request_json"])["implementation_verification"]
    assert request.owner_workflow_id == request.run_id and request.role == request.component_id == "verifier"
    assert request.task_context.expert_slot is None and request.task_context.component_id is None
    inputs = {i.ref.rsplit("--", 1)[-1]: json.loads(i.content_bytes()) for i in request.input_artifacts}
    assert len(inputs) == len(request.input_artifacts)
    core = {"expert-config", "verification-context", "implementation-source"}
    extra = set(inputs) - core
    assert core <= set(inputs) and extra <= {"candidate-validation-error", "agent-retry-diagnostic"} and len(extra) <= 1
    for name in extra:
        diagnostic = inputs[name]
        source_id = diagnostic["source_attempt_id"]
        source_attempt = attempts[source_id]
        assert source_attempt["run_id"] == request.run_id and source_attempt["workflow_id"] == request.owner_workflow_id
        assert source_attempt["command_id"] == request.command_id and source_attempt["kind"] == "AGENT"
        assert source_attempt["state"] == "CLOSED" and source_attempt["attempt_ordinal"] < request.attempt_ordinal
        assert next(i for i in request.input_artifacts if i.ref.endswith("--" + name)).ref == source_id + "--" + name
        source_observation = observations[source_id]
        if name == "candidate-validation-error":
            assert set(diagnostic) == {"source_attempt_id", "validation_error"}
            assert source_attempt["terminal_reason"] == "FAILED" and source_observation["failure_code"] == "invalid_candidate"
            assert diagnostic["validation_error"] == source_observation["diagnostics"]["validation_error"]
        else:
            assert set(diagnostic) <= {"source_attempt_id", "source_attempt_ordinal", "terminal_reason", "retry_class",
                "failure_code", "failure_stage", "diagnostic_summary", "rejected_structured_output", "allowed_supporting_refs"}
            assert source_attempt["terminal_reason"] in {"FAILED", "TIMED_OUT", "LOST"}
            assert diagnostic["source_attempt_ordinal"] == source_attempt["attempt_ordinal"]
            assert diagnostic["terminal_reason"] == source_attempt["terminal_reason"]
            assert diagnostic["retry_class"] == source_observation["retry_class"] == "MODEL"
            from .diagnostics import bounded_diagnostic_text, diagnostic_summary
            for key in ("failure_code", "failure_stage"):
                assert diagnostic[key] == bounded_diagnostic_text(source_observation[key])
            if "diagnostic_summary" in diagnostic:
                assert diagnostic["diagnostic_summary"] == diagnostic_summary(source_observation)
            if "rejected_structured_output" in diagnostic:
                assert diagnostic["rejected_structured_output"] == source_observation["diagnostics"]["provider_structured_candidate"]
            if "allowed_supporting_refs" in diagnostic:
                assert diagnostic["failure_stage"] in {"provider_structured_output", "provider_output"}
                assert diagnostic["allowed_supporting_refs"] == source_observation["request"]["immutable_context_refs"]
    ctx = inputs["verification-context"]
    assert all(ctx[k] == v for k, v in value.items())
    source = commands[value["source_submit_command_id"]]
    assert source["run_id"] == request.run_id and source["workflow_id"] == value["experiment_id"]
    submit = json.loads(source["request_json"])
    snapshot = submit["source_snapshot"]
    assert value["source_output_digest"] == snapshot["output_digest"]
    assert ctx.get("entrypoint_smoke") == snapshot["arguments"].get("entrypoint_smoke")
    import base64
    captured = {a.path: a.content_bytes() for a in map(WorkspaceArtifact.from_dict, snapshot["arguments"]["workspace_files"])}
    actual = {path: value.encode("utf-8") if isinstance(value, str) else base64.b64decode(value["content_base64"], validate=True)
              for path, value in inputs["implementation-source"].items()}
    assert actual == captured
    if ctx["phase"] == "review":
        evidence = ctx["probe_evidence"]
        probe = attempts[evidence["attempt_id"]]
        assert probe["run_id"] == request.run_id and probe["experiment_id"] == value["experiment_id"]
        assert probe["execution_id"] == value["experiment_id"] + "--implementation-probe"
        assert probe["state"] == "CLOSED" and evidence["terminal_reason"] == probe["terminal_reason"]
        assert evidence["observation"] == observations[probe["attempt_id"]]
        if request.schema_version >= 35:
            from .verification_probe import validate_result
            inspection = observations[ctx["probe_report_ref"]]
            assert inspection["request"]["command"] == "VERIFY_IMPLEMENTATION"
            commissioned = json.loads(commands[inspection["request"]["command_id"]]["request_json"])["implementation_verification"]
            assert commissioned["experiment_id"] == value["experiment_id"]
            if request.schema_version >= 36:
                from .verification_contract import MAX_PROBE_REPAIRS
                assert value["probe_attempt_id"] == probe["attempt_id"]
                assert value["probe_report_ref"] == ctx["probe_report_ref"]
                assert 0 <= value["probe_revision"] <= value["probe_repair_limit"] <= MAX_PROBE_REPAIRS
                assert ctx["probe_repairs_remaining"] == value["probe_repair_limit"] - value["probe_revision"]
                assert value["probe_repair_limit"] == commissioned["probe_repair_limit"]
                assert value["source_output_digest"] == commissioned["source_output_digest"]
                assert value["source_submit_command_id"] == commissioned["source_submit_command_id"]
                prior = attempts[ctx["probe_report_ref"]]
                assert prior["state"] == "CLOSED" and prior["terminal_reason"] == "SUCCEEDED"
                assert prior["ended_at"] <= probe["started_at"]
                assert inspection["response"]["typed_candidate"]["verdict"] == "probe_required"
                assert value["probe_revision"] == commissioned["probe_revision"] + (commissioned["phase"] == "review")
                assert commissioned["phase"] in {"inspect", "review"}
            else:
                assert commissioned["phase"] == "inspect"
            assert ctx["probe_plan"] == inspection["response"]["typed_candidate"]["probe"]
            actual = evidence["observation"]
            try:
                checks = validate_result(actual.get("check_results"), ctx["probe_plan"]["checks"],
                    attempt_id=probe["attempt_id"], report_ref=ctx["probe_report_ref"])
                usable = (probe["terminal_reason"] == "SUCCEEDED" and actual.get("implementation_probe") is True
                    and not actual.get("synthetic_before_spawn") and not actual.get("check_results_error") and checks["passed"])
            except (ValueError, TypeError): usable = False
            assert evidence["usable"] is usable


def probe_context(attempt, intent, contexts, commands, attempts, observations, transitions, payloads):
    context = next(value for (experiment, _), value in contexts.items() if experiment == attempt["experiment_id"])
    assert attempt["execution_id"] == attempt["experiment_id"] + "--implementation-probe"
    binding = intent["implementation_probe"]
    commission = json.loads(commands[binding["review_command_id"]]["request_json"])["implementation_verification"]
    assert commission["experiment_id"] == attempt["experiment_id"]
    assert commission["source_submit_command_id"] == context["contract"]["source_submit_command_id"]
    assert commission["source_output_digest"] == context["contract"]["source_agent_output_digest"]
    source = attempts[binding["report_ref"]]
    assert source["command_id"] == binding["review_command_id"] and source["run_id"] == attempt["run_id"]
    assert source["kind"] == "AGENT" and source["state"] == "CLOSED" and source["terminal_reason"] == "SUCCEEDED"
    assert source["ended_at"] <= attempt["started_at"]
    report = observations[binding["report_ref"]]
    assert commission["phase"] == "inspect" or (report["request"]["schema_version"] >= 36 and commission["phase"] == "review")
    assert report["request"]["command"] == "VERIFY_IMPLEMENTATION"
    assert report["response"]["typed_candidate"]["verdict"] == "probe_required"
    probe = report["response"]["typed_candidate"]["probe"]
    submission = json.loads(commands[commission["source_submit_command_id"]]["request_json"])
    if "entrypoint_smoke" in submission["source_snapshot"]["arguments"]:
        assert any(c["id"] == "formal_entrypoint" and c["kind"] == "assertion" and c["required"] is True
                   for c in probe["checks"])
    assert 0 < probe["timeout_seconds"] <= 120 and probe["argv"][1] in probe["files"]
    assert not set(probe["files"]) & set(context["workspace"])
    if attempt["state"] == "CLOSED":
        matches = [row for row in transitions if commands[str(row["command_id"])]["command_type"] == "OBSERVE_IMPLEMENTATION"
                   and json.loads(row["payload_json"]).get("attempt_id") == attempt["attempt_id"]]
        assert len(matches) == 1 and matches[0]["state"] == "READY"
        assert matches[0]["workflow_id"] == attempt["workflow_id"] and matches[0]["run_id"] == attempt["run_id"]
    argv = probe["argv"]
    if report["request"]["schema_version"] >= 35:
        from .verification_probe import RUNNER_PATH
        argv = ["python", RUNNER_PATH]
    mounts = context.get("component_mounts")
    if mounts and mounts.formal_goal_inputs:
        from dataclasses import replace
        withheld = set(mounts.formal_goal_inputs)
        public = replace(mounts, bindings=tuple(b for b in mounts.bindings if b.destination not in withheld),
                         read_roots=tuple(root for root in mounts.read_roots if root not in withheld), formal_goal_inputs=())
        environment = {**context["resource"]["environment"], "HYPOLOOM_READ_ROOTS": json.dumps(public.read_roots, separators=(",", ":"))}
        context = {**context, "component_mounts": public, "resource": {**context["resource"], "environment": environment}}
    return {**context, "source_path": argv[1], "execution_argv": tuple(argv),
            "contract": {**context["contract"], "argv": argv,
                         "timeout_seconds": min(probe["timeout_seconds"], context["contract"]["timeout_seconds"])}}
