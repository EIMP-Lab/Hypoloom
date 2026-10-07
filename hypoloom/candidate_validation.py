"""Bounded, non-scientific validation adapter for one DRAFT candidate.

The adapter owns no store or lifecycle decision.  It compiles candidate source
inside the existing no-network execution sandbox and returns bounded diagnostic
bytes for the Host to persist and feed into a later Agent attempt.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any, Mapping
from .agent_aci import AgentRequest, validate_postflight, validate_submission_snapshot
from .component_contract import component_is_production
from .compute_resources import ComputeCapabilities, LocalComputeResources
from .domain import ResourceGrant, ResourceKind
from .execution_workspace import ExecutionWorkspace, PreparedExecution, bind_clean_git_target
from .outcome_adapter import OUTCOME_ADAPTER_VERSION, component_result_contract_parts
from .runner import ProcessObservation

DIAGNOSTIC_TAIL_BYTES = 65_536
SPEC_VALIDATION_VERSION = "candidate-compile/3"
_VALIDATE = (
    "import os, sys\n"
    "source = open('/input/experiment.py', 'rb').read()\n"
    "compile(source, '/input/experiment.py', 'exec')\n"
    "if os.environ.get('HYPOLOOM_REQUIRE_RESULT_REFERENCE') == '1'"
    " and b'HYPOLOOM_RESULT_PATH' not in source"
    " and b'result.json' not in source:\n"
    "    print('experiment.py never references HYPOLOOM_RESULT_PATH"
    " or result.json: a successful process would not produce its declared"
    " result', file=sys.stderr)\n"
    "    raise SystemExit(2)\n"
)

def component_result_contract(component: Mapping[str, Any], protocol: Mapping[str, Any]) -> dict[str, Any]:
    """Render advisory schema bytes from the Outcome Adapter's normalized rules."""
    if component.get("result", {}).get("deferred") is True:
        return {"validator_version": OUTCOME_ADAPTER_VERSION, "expert_declared_measurement": True,
                "result_path_env": "HYPOLOOM_MEASUREMENT_RESULT_PATH", "parser_source_root": "/input",
                "raw_artifacts_root": "/raw", "parsed_output_root": "/parsed",
                "required_top_level_keys": ["metric", "value"], "optional_top_level_keys": ["baseline"],
                "numeric_values": "finite decimal numbers; no parser-authored verdict",
                "comparison": "the exact hypothesis declaration, frozen at first preregistration"}
    parts = component_result_contract_parts(component, protocol)
    result: dict[str, Any] = {
        "validator_version": OUTCOME_ADAPTER_VERSION,
        "result_path_env": "HYPOLOOM_RESULT_PATH",
        "result_path_is_exact_file": True,
        "result_json": {
            "type": "object",
            "required_top_level_keys": list(parts["required_top_level_keys"]),
            "optional_top_level_keys": list(parts["optional_top_level_keys"]),
            "additional_top_level_keys": False,
        },
        "required_artifact_map": {
            "type": "object",
            "exact_logical_artifact_ids": list(parts["required_artifacts"]),
            "additional_logical_artifact_ids": False,
            "values": {
                name: {
                    "type": "output_relative_path_string_or_nonempty_path_array",
                    "min_count": parts["artifact_min_counts"][name],
                }
                for name in parts["required_artifacts"]
            },
            "inline_artifact_content": False,
            "paths_must_name_distinct_regular_output_files": True,
            "undeclared_output_files": False,
        },
        "optional_identity": {
            "type": "object",
            "allowed_keys": list(parts["identity_keys"]),
            "additional_keys": False,
            "omit_when_unavailable": True,
            "placeholder_values_allowed": False,
            "fields": parts["identity_fields"],
            "implementation_sha256_required_when": "artifacts.implementation_patch is declared",
            "implementation_sha256_digest_basis": "sha256 hex of the exact implementation.patch file bytes committed by the design Agent attempt",
        },
        "artifact_validators": list(parts["requirements"].get("artifact_validators", [])),
    }
    if parts["requirements"].get("inconclusive_if") is not None:
        result["inconclusive_if"] = parts["requirements"]["inconclusive_if"]
    if parts["kind"] == "metric":
        result["trial_item"] = {
            "type": "object",
            "required_keys": list(parts["trial_required_keys"]),
            "optional_keys": list(parts["trial_optional_keys"]),
            "additional_keys": False,
            "clean": {"const": True},
            "arm": {"enum": ["control", "treatment"]} if parts["metric_mode"] == "paired" else {"const": None},
            "metrics": "nonempty_object_of_finite_numeric_values",
        }
    return result

def prepare_candidate_validation(artifact_root: Path, state_root: Path,
        compute_resources: LocalComputeResources, *,
        agent_request: Mapping[str, Any], agent_response: Mapping[str, Any], attempt_id: str,
        attempt_ordinal: int, execution_id: str, timeout_seconds: float,
        require_result_reference: bool = False) -> tuple[PreparedExecution, ExecutionWorkspace]:
    kwargs = compute_resources.workspace_kwargs(ResourceGrant(kind=ResourceKind.CPU, gpu_uuid=None), ComputeCapabilities(gpus=()))
    workspace = ExecutionWorkspace(artifact_root, state_root=state_root, **kwargs)
    target_env = {"PYTHONDONTWRITEBYTECODE": "1", "HYPOLOOM_RESULT_PATH": "result.json"}
    if require_result_reference:
        target_env["HYPOLOOM_REQUIRE_RESULT_REFERENCE"] = "1"
    prepared = workspace.plan(agent_request=agent_request, agent_response=agent_response,
        attempt_id=attempt_id, attempt_ordinal=attempt_ordinal, execution_id=execution_id,
        target_env=target_env,
        timeout_seconds=timeout_seconds, execution_argv=("python", "-c", _VALIDATE))
    return prepared, workspace

def assert_candidate_patch_compiles(workspace: ExecutionWorkspace,
        document: Mapping[str, Any], component_id: str, *,
        agent_request: Mapping[str, Any], agent_response: Mapping[str, Any]) -> None:
    """Compile the Python sources a production candidate's patch touches.

    The patched target tree is materialized through the same verified checkout
    path as execution, and ``compile`` parses without executing, so this
    host-side pre-check is safe.  A patch that breaks target Python fails the
    candidate validation before any validation or execution process spawns.
    Non-production components and patch-free responses are left to the other
    validators.
    """
    if not component_is_production(document, component_id):
        return
    request = AgentRequest.from_dict(agent_request)
    validated = (validate_submission_snapshot(request, agent_response)
        if agent_response.get("kind") == "admitted-submit/1"
        else validate_postflight(request, agent_response))
    workspace_files = validated.workspace_files
    patch = workspace_files.get("implementation.patch")
    runtime = document.get("runtime")
    target_repo = runtime.get("target_repo") if isinstance(runtime, Mapping) else None
    if patch is None or not isinstance(target_repo, str) or not target_repo:
        return
    binding = bind_clean_git_target(target_repo)
    changed = _patched_python_paths(patch)
    if not changed:
        return
    with workspace.verified_detached_target_tree(binding, patch=patch) as tree:
        for relative in changed:
            source = tree.joinpath(*relative.split("/"))
            if not source.is_file():
                continue
            try:
                compile(source.read_bytes(), relative, "exec")
            except SyntaxError as exc:
                raise ValueError(
                    f"implementation_patch_compile_failed: {relative}: "
                    f"{exc.msg} (line {exc.lineno})"
                ) from exc

def _patched_python_paths(patch: bytes) -> tuple[str, ...]:
    """List the distinct destination ``.py`` paths a unified diff writes."""
    paths: list[str] = []
    for line in patch.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("+++ "):
            continue
        destination = line[4:].strip()
        if destination == "/dev/null":
            continue
        if destination.startswith('"') and destination.endswith('"'):
            destination = destination[1:-1]
        if destination.startswith("b/"):
            destination = destination[2:]
        if destination.endswith(".py") and destination not in paths:
            paths.append(destination)
    return tuple(paths)

def bounded_diagnostics(prepared: PreparedExecution, observation: ProcessObservation | None,
        *, failure: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"validation_version": SPEC_VALIDATION_VERSION,
        "source_agent_attempt_id": prepared.agent_attempt_id,
        "source_agent_output_digest": prepared.agent_output_digest,
        "process_request": prepared.process_request.to_dict()}
    if observation is not None: payload.update(observation.to_dict())
    if failure is not None: payload["validation_error"] = failure[:1024]
    for name in ("stdout", "stderr"):
        path = prepared.log_dir / f"{name}.log"
        payload[f"{name}_tail"] = _tail(path).decode("utf-8", errors="replace")
    return payload

def _tail(path: Path) -> bytes:
    if not path.is_file() or path.is_symlink(): return b""
    size = path.stat().st_size
    with path.open("rb") as stream:
        if size > DIAGNOSTIC_TAIL_BYTES: stream.seek(size - DIAGNOSTIC_TAIL_BYTES)
        return stream.read(DIAGNOSTIC_TAIL_BYTES)

__all__ = ["DIAGNOSTIC_TAIL_BYTES", "SPEC_VALIDATION_VERSION", "assert_candidate_patch_compiles",
           "bounded_diagnostics", "component_result_contract", "prepare_candidate_validation"]
