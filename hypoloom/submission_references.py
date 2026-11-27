"""Expand scoped references before workspace capture and ordinary Host admission."""
from __future__ import annotations

import json
from dataclasses import replace

from .agent_aci import AgentACIError, WorkspaceArtifact
from .expert_loop import ResearchNodeKind, ResearchNodeTask


def accepted_arguments(host, request, experiment_id, owned):
    if not isinstance(experiment_id, str) or experiment_id not in owned:
        raise AgentACIError("tool_chain_mismatch", "Use an accepted exp_id belonging to this execution node.")
    _, source, snapshot, _ = host._submission_source(owned[experiment_id]["source_submit_command_id"])
    if source.run_id != request.run_id or source.command_id != request.command_id or source.role != request.role:
        raise AgentACIError("tool_chain_mismatch", "The submission reference belongs to another execution node.")
    return snapshot["arguments"]


def resolve_estimate(arguments, source):
    if "resource_estimate" in arguments or source.get("resource_estimate") is None:
        raise AgentACIError("resource_estimate_invalid", "Use one estimate or a resource_estimate_ref to a submission with a captured estimate.")
    arguments.pop("resource_estimate_ref")
    arguments["resource_estimate"] = source["resource_estimate"]


def expand_submission(host, request, arguments, owned):
    args = dict(arguments)
    bases = {}

    def source(ref):
        if not isinstance(ref, str):
            raise AgentACIError("tool_arguments", "A submission reference must be a string.")
        if ref not in bases:
            bases[ref] = accepted_arguments(host, request, ref, owned)
        return bases[ref]

    if "resource_estimate_ref" in args:
        resolve_estimate(args, source(args["resource_estimate_ref"]))
    if "candidate_ref" not in args:
        return args, {}
    ref = args.pop("candidate_ref")
    if ref == "design" and not args.get("replaces_experiment_id"):
        task = ResearchNodeTask(key=replace(request.task_context.key, node_kind=ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT))
        accepted = host._expert_success(task)
        if accepted is None:
            raise AgentACIError("candidate_ref_invalid", "This execution node has no accepted design.")
        response = json.loads(accepted["observation_json"])["response"]
        base, artifacts = response["typed_candidate"], response["proposed_workspace_artifacts"]
    elif ref == args.get("replaces_experiment_id"):
        previous = source(ref)
        base, artifacts = previous["candidate"], previous["workspace_files"]
    else:
        raise AgentACIError("candidate_ref_invalid", "Use design for the initial submission or the exact replaces_experiment_id for a repair.")
    delta = args.get("candidate", {})
    mutable = {"method", "acceptance_criteria", "execution_argv", "resource_preference"}
    if not isinstance(delta, dict) or delta.keys() - mutable:
        raise AgentACIError("invalid_candidate", "With candidate_ref, candidate accepts only method, acceptance_criteria, execution_argv and resource_preference. A new scientific declaration requires a new hypothesis.")
    args["candidate"] = {**base, **delta}
    measurement = base.get("measurement")
    if not measurement:
        return args, {}
    from .evaluator_examples import MEASUREMENT_CASES, fixture_path, measurement_examples
    files = {item["path"]: item for item in artifacts}
    paths = set(measurement["parser_files"])
    if MEASUREMENT_CASES in files:
        from .measurement_contract import ScientificMeasurement
        manifest = WorkspaceArtifact.from_dict(files[MEASUREMENT_CASES]).content_bytes()
        cases = measurement_examples(manifest, ScientificMeasurement.from_dict(measurement))
        paths.add(MEASUREMENT_CASES)
        paths.update(fixture_path(case, name) for case in cases for name in measurement["raw_artifacts"])
    return args, {p: WorkspaceArtifact.from_dict(files[p]).content_bytes() for p in paths if p in files}


def inherit_captured_scoring(arguments, frozen):
    """Also enforce reference semantics for already-captured (local adapter) submissions."""
    artifacts = arguments.get("workspace_files")
    if not isinstance(artifacts, list):
        raise AgentACIError("tool_arguments", "A captured workspace is required for a reference submission.")
    supplied = {a.path: a for a in map(WorkspaceArtifact.from_dict, artifacts)}
    from .evaluator_examples import MEASUREMENT_CASES, fixture_path, measurement_examples
    from .measurement_contract import ScientificMeasurement
    measure = ScientificMeasurement.from_dict(arguments["candidate"]["measurement"])
    needed = set(measure.parser_files)
    for path in needed:
        if path not in frozen or path in supplied and supplied[path].content_bytes() != frozen[path]:
            raise AgentACIError("measurement_frozen", f"Frozen parser differs at {path}. Use the accepted parser or a new hypothesis.")
    manifest = supplied.get(MEASUREMENT_CASES)
    if manifest or MEASUREMENT_CASES in frozen:
        cases = measurement_examples(manifest.content_bytes() if manifest else frozen[MEASUREMENT_CASES], measure)
        needed.add(MEASUREMENT_CASES)
        needed.update(fixture_path(case, name) for case in cases for name in measure.raw_artifacts)
    extra = [WorkspaceArtifact.from_bytes(p, frozen[p]).to_dict() for p in sorted(needed - supplied.keys()) if p in frozen]
    arguments["workspace_files"] = [*artifacts, *extra]
