"""Independent evidence checks for engineering Processes, without Host reducers."""
import json


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
                           and row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED")
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
    assert set(inputs) == {"expert-config", "verification-context", "implementation-source"}
    ctx = inputs["verification-context"]
    assert all(ctx[k] == v for k, v in value.items())
    source = commands[value["source_submit_command_id"]]
    assert source["run_id"] == request.run_id and source["workflow_id"] == value["experiment_id"]
    submit = json.loads(source["request_json"])
    snapshot = submit["source_snapshot"]
    assert value["source_output_digest"] == snapshot["output_digest"]
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


def probe_context(attempt, intent, contexts, commands, attempts, observations, transitions, payloads):
    context = next(value for (experiment, _), value in contexts.items() if experiment == attempt["experiment_id"])
    assert attempt["execution_id"] == attempt["experiment_id"] + "--implementation-probe"
    binding = intent["implementation_probe"]
    commission = json.loads(commands[binding["review_command_id"]]["request_json"])["implementation_verification"]
    assert commission["phase"] == "inspect" and commission["experiment_id"] == attempt["experiment_id"]
    assert commission["source_submit_command_id"] == context["contract"]["source_submit_command_id"]
    assert commission["source_output_digest"] == context["contract"]["source_agent_output_digest"]
    source = attempts[binding["report_ref"]]
    assert source["command_id"] == binding["review_command_id"] and source["run_id"] == attempt["run_id"]
    assert source["kind"] == "AGENT" and source["state"] == "CLOSED" and source["terminal_reason"] == "SUCCEEDED"
    assert source["ended_at"] <= attempt["started_at"]
    report = observations[binding["report_ref"]]
    assert report["request"]["command"] == "VERIFY_IMPLEMENTATION"
    assert report["response"]["typed_candidate"]["verdict"] == "probe_required"
    probe = report["response"]["typed_candidate"]["probe"]
    assert 0 < probe["timeout_seconds"] <= 120 and probe["argv"][1] in probe["files"]
    assert not set(probe["files"]) & set(context["workspace"])
    if attempt["state"] == "CLOSED":
        matches = [row for row in transitions if commands[str(row["command_id"])]["command_type"] == "OBSERVE_IMPLEMENTATION"
                   and json.loads(row["payload_json"]).get("attempt_id") == attempt["attempt_id"]]
        assert len(matches) == 1 and matches[0]["state"] == "READY"
        assert matches[0]["workflow_id"] == attempt["workflow_id"] and matches[0]["run_id"] == attempt["run_id"]
    return {**context, "source_path": probe["argv"][1], "execution_argv": tuple(probe["argv"]),
            "contract": {**context["contract"], "argv": probe["argv"],
                         "timeout_seconds": min(probe["timeout_seconds"], context["contract"]["timeout_seconds"])}}
