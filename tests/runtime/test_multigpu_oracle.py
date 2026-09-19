"""Independent raw resource audit: synthetic inventories/argv, no GPU I/O."""
import copy
import json
import sqlite3

import pytest

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.oracle import _audit_gpu_attempt_overlaps, _audit_process_resource, _audit_process_resource_observation, _audit_resource_contract


GPU_A = "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
GPU_B = "GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
GPU_C = "GPU-cccccccc-cccc-cccc-cccc-cccccccccccc"


def contract_case(*, count=2, cap=2, version=14, preference="AUTO", selected=(GPU_A, GPU_B)):
    capabilities = {"schema_version": 3, "busy_gpu_uuids": [], "gpus": [
        {"index": index, "uuid": uuid, "name": uuid, "total_memory_mib": 24576,
            "free_memory_mib": free, "driver_version": "synthetic"}
        for index, uuid, free in ((2, GPU_A, 10000), (0, GPU_B, 12000), (3, GPU_C, 5000))]}
    request = {"schema_version": version, "input_artifacts": [EmbeddedInput.from_bytes(
        "execution--compute-capabilities", "application/json", json.dumps(capabilities, sort_keys=True, separators=(",", ":")).encode()).to_dict()]}
    grant = {"kind": "GPU", "gpu_uuids": list(selected)} if len(selected) > 1 else {"kind": "GPU", "gpu_uuid": selected[0]}
    contract = {"resource_preference": preference, "resource_grant": grant,
        "sandbox_profile": "bwrap-gpu-multi-no-network/1" if len(selected) > 1 else "bwrap-gpu-single-no-network/1",
        "environment": [["CUDA_DEVICE_ORDER", "PCI_BUS_ID"], ["CUDA_VISIBLE_DEVICES", ",".join(selected)]]}
    snapshot = {"kind": "admitted-submit/1", "arguments": {"candidate": {"resource_preference": preference}, "gpus": count}}
    document = {"methodology": {"controls": {"product": {"safety": {"gpus": "auto", "max_gpus_per_experiment": cap}}}}}
    return {"schema_version": 5, "resource_preference": preference}, contract, request, snapshot, document


def audit_contract(case):
    payload, contract, request, snapshot, document = case
    issues = []
    resource = _audit_resource_contract("fact-resource", payload, contract, request, snapshot,
        {"min_vram_per_experiment": 1000, "gpu_vram_reserve_mb": 1000}, issues, document=document)
    return resource, issues


@pytest.mark.parametrize("version", [14, 15])
def test_oracle_resolves_exact_source_submit_count_and_each_authorized_device(version):
    resource, issues = audit_contract(contract_case(version=version))
    assert issues == []
    assert resource["gpu_uuids"] == (GPU_A, GPU_B)
    assert resource["gpu_indices"] == (2, 0)


def test_pre14_single_gpu_retains_its_original_grant_and_telemetry_shape():
    resource, issues = audit_contract(contract_case(count=1, cap=1, version=13, selected=(GPU_B,)))
    assert issues == [] and resource["gpu_uuid"] == GPU_B and resource["gpu_index"] == 0
    _audit_process_resource_observation({"terminal_reason": "SUCCEEDED", "resource_usage": usage(GPU_B)}, resource, "old-single", issues)
    assert issues == []


@pytest.mark.parametrize("change", ["count", "cap", "retained", "duplicate", "unsorted", "ambiguous", "unauthorized", "rank", "environment", "cpu_conflict"])
def test_plural_grant_cannot_change_source_count_cap_authority_or_canonical_shape(change):
    case = contract_case(); payload, contract, request, snapshot, document = case
    if change == "count": snapshot["arguments"]["gpus"] = 1
    elif change == "cap": document["methodology"]["controls"]["product"]["safety"]["max_gpus_per_experiment"] = 1
    elif change == "retained": request["schema_version"] = 13
    elif change == "duplicate": contract["resource_grant"]["gpu_uuids"] = [GPU_A, GPU_A]
    elif change == "unsorted": contract["resource_grant"]["gpu_uuids"].reverse()
    elif change == "ambiguous": contract["resource_grant"]["gpu_uuid"] = GPU_A
    elif change == "unauthorized": contract["resource_grant"]["gpu_uuids"][-1] = "GPU-dddddddd-dddd-dddd-dddd-dddddddddddd"
    elif change == "rank": contract["resource_grant"]["gpu_uuids"][-1] = GPU_C
    elif change == "environment": contract["environment"][-1][-1] = GPU_A
    else:
        payload["resource_preference"] = contract["resource_preference"] = snapshot["arguments"]["candidate"]["resource_preference"] = "CPU"
    _, issues = audit_contract(case)
    assert "resource_resolution_mismatch" in {issue.code for issue in issues}
    if change == "unauthorized": assert "unauthorized_device_binding" in {issue.code for issue in issues}


def usage(uuid, *, pressure=False):
    return {"gpu_uuid": uuid, "total_memory_mib": 24576, "initial_free_memory_mib": 8000,
        "minimum_free_memory_mib": 500 if pressure else 6000, "latest_free_memory_mib": 500 if pressure else 7000,
        "latest_used_memory_mib": 24076 if pressure else 17576, "latest_utilization_percent": 99,
        "sample_count": 3, "pressure_exceeded": pressure}


def renamed_usage(uuid, *, pressure=False, attribution=None):
    result = {"gpu_uuid": uuid, "gpu_total_memory_mib": 24576, "gpu_initial_free_memory_mib": 8000,
        "gpu_minimum_free_memory_mib": 500 if pressure else 6000, "gpu_latest_free_memory_mib": 500 if pressure else 7000,
        "gpu_latest_used_memory_mib": 24076 if pressure else 17576, "gpu_latest_utilization_percent": 99,
        "sample_count": 3, "pressure_exceeded": pressure}
    if pressure: result["pressure_attribution"] = attribution
    return result


@pytest.mark.parametrize("attribution,owned", [("self", 552), ("unknown", None)])
def test_multi_gpu_pressure_cancel_records_the_attributed_decision(attribution, owned):
    resource = {"expected_kind": "GPU", "gpu_uuids": (GPU_A, GPU_B), "gpu_uuid": None,
        "reserve_memory_mib": 1000, "telemetry_required": True}
    observation = {"terminal_reason": "FAILED",
        "resource_usage": [renamed_usage(GPU_A), renamed_usage(GPU_B, pressure=True, attribution=attribution)],
        "resource_pressure": {"reason": "sustained_free_gpu_memory_below_reserve", "gpu_reserve_memory_mib": 1000,
            "owned_memory_mib_at_decision": owned, "attribution": attribution,
            "terminate_grace_seconds": 30.0, "gpu_uuids": [GPU_B]}}
    issues = []
    _audit_process_resource_observation(observation, resource, "process", issues)
    assert issues == []


def test_multi_gpu_external_pressure_survives_without_a_cancel_payload():
    resource = {"expected_kind": "GPU", "gpu_uuids": (GPU_A, GPU_B), "gpu_uuid": None,
        "reserve_memory_mib": 1000, "telemetry_required": True}
    observation = {"terminal_reason": "SUCCEEDED",
        "resource_usage": [renamed_usage(GPU_A), renamed_usage(GPU_B, pressure=True, attribution="external")]}
    issues = []
    _audit_process_resource_observation(observation, resource, "process", issues)
    assert issues == []


@pytest.mark.parametrize("change", ["legacy_reason", "missing_attribution", "wrong_aggregate",
    "missing_owned_field", "missing_grace", "invalid_grace", "usage_without_attribution", "spared_device_blamed"])
def test_multi_gpu_pressure_attribution_cannot_be_rewritten_or_dropped(change):
    resource = {"expected_kind": "GPU", "gpu_uuids": (GPU_A, GPU_B), "gpu_uuid": None,
        "reserve_memory_mib": 1000, "telemetry_required": True}
    observation = {"terminal_reason": "FAILED",
        "resource_usage": [renamed_usage(GPU_A), renamed_usage(GPU_B, pressure=True, attribution="self")],
        "resource_pressure": {"reason": "sustained_free_gpu_memory_below_reserve", "gpu_reserve_memory_mib": 1000,
            "owned_memory_mib_at_decision": 552, "attribution": "self",
            "terminate_grace_seconds": 30.0, "gpu_uuids": [GPU_B]}}
    if change == "legacy_reason": observation["resource_pressure"]["reason"] = "sustained_free_memory_below_reserve"
    elif change == "missing_attribution": observation["resource_pressure"].pop("attribution")
    elif change == "wrong_aggregate": observation["resource_pressure"]["attribution"] = "unknown"
    elif change == "missing_owned_field": observation["resource_pressure"].pop("owned_memory_mib_at_decision")
    elif change == "missing_grace": observation["resource_pressure"].pop("terminate_grace_seconds")
    elif change == "invalid_grace": observation["resource_pressure"]["terminate_grace_seconds"] = 0
    elif change == "usage_without_attribution": observation["resource_usage"][1].pop("pressure_attribution")
    else: observation["resource_usage"][1]["pressure_attribution"] = "external"
    issues = []
    _audit_process_resource_observation(observation, resource, "process", issues)
    assert {issue.code for issue in issues} == {"process_resource_observation_mismatch"}


@pytest.mark.parametrize("pressured", [False, True])
def test_multi_gpu_usage_and_pressure_account_for_every_device(pressured):
    resource = {"expected_kind": "GPU", "gpu_uuids": (GPU_A, GPU_B), "gpu_uuid": None,
        "reserve_memory_mib": 1000, "telemetry_required": True}
    observation = {"terminal_reason": "FAILED" if pressured else "SUCCEEDED",
        "resource_usage": [usage(GPU_A), usage(GPU_B, pressure=pressured)]}
    if pressured: observation["resource_pressure"] = {"reason": "sustained_free_memory_below_reserve", "reserve_memory_mib": 1000, "gpu_uuids": [GPU_B]}
    issues = []
    _audit_process_resource_observation(observation, resource, "process", issues)
    assert issues == []


@pytest.mark.parametrize("change", ["missing_second", "duplicate", "foreign", "undeclared_pressure", "wrong_pressure_card", "missing_usage"])
def test_second_device_telemetry_cannot_disappear_or_be_attributed_to_first(change):
    resource = {"expected_kind": "GPU", "gpu_uuids": (GPU_A, GPU_B), "gpu_uuid": None,
        "reserve_memory_mib": 1000, "telemetry_required": True}
    observation = {"terminal_reason": "FAILED", "resource_usage": [usage(GPU_A), usage(GPU_B, pressure=True)],
        "resource_pressure": {"reason": "sustained_free_memory_below_reserve", "reserve_memory_mib": 1000, "gpu_uuids": [GPU_B]}}
    if change == "missing_second": observation["resource_usage"].pop()
    elif change == "duplicate": observation["resource_usage"][-1] = copy.deepcopy(observation["resource_usage"][0])
    elif change == "foreign": observation["resource_usage"][-1]["gpu_uuid"] = GPU_C
    elif change == "undeclared_pressure": observation.pop("resource_pressure")
    elif change == "wrong_pressure_card": observation["resource_pressure"]["gpu_uuids"] = [GPU_A]
    else: observation.pop("resource_usage")
    issues = []
    _audit_process_resource_observation(observation, resource, "process", issues)
    assert {issue.code for issue in issues} == {"process_resource_observation_mismatch"}


def test_overlap_detects_shared_second_device_across_runs_and_legacy_single_grant():
    connection = sqlite3.connect(":memory:"); connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE execution_attempts(attempt_id,run_id,started_at,ended_at,execution_id,kind)")
    connection.execute("CREATE TABLE research_facts(run_id,fact_type,execution_id,payload_json)")
    for run, attempt, start, end, grant in (("run-a", "plural", 1, 5, {"kind": "GPU", "gpu_uuids": [GPU_A, GPU_B]}),
            ("run-b", "single", 2, 6, {"kind": "GPU", "gpu_uuid": GPU_B})):
        connection.execute("INSERT INTO execution_attempts VALUES(?,?,?,?,?,?)", (attempt, run, start, end, attempt, "PROCESS"))
        connection.execute("INSERT INTO research_facts VALUES(?,?,?,?)", (run, "ExperimentPreregistered", attempt,
            json.dumps({"execution_contract": {"resource_grant": grant}})))
    issues = []
    _audit_gpu_attempt_overlaps(connection, "run-a", issues)
    connection.close()
    assert len(issues) == 1 and issues[0].code == "same_gpu_attempt_overlap"
    assert issues[0].identifiers == (GPU_B, "plural", "single")


def test_oracle_process_mounts_match_both_uuid_bound_indices_without_gpu_io(tmp_path):
    from hypoloom.domain import ResourceGrant, ResourceKind
    from hypoloom.execution_workspace import ExecutionWorkspace
    from test_execution_workspace import candidate_pair, plan
    from test_multigpu_resources import _resources
    resources = _resources(tmp_path)
    grant = ResourceGrant(kind=ResourceKind.GPU, gpu_uuids=(GPU_A, GPU_B))
    state = tmp_path / "state"; artifacts = state / "artifacts"; artifacts.mkdir(parents=True)
    workspace = ExecutionWorkspace(artifacts, state_root=state, **resources.workspace_kwargs(grant, resources.snapshot()))
    prepared = plan(workspace, *candidate_pair())
    process = prepared.process_request.to_dict()
    resource = {"expected_kind": "GPU", "gpu_uuids": (GPU_A, GPU_B), "gpu_indices": (2, 0), "gpu_uuid": None,
        "environment": {"CUDA_DEVICE_ORDER": "PCI_BUS_ID", "CUDA_VISIBLE_DEVICES": GPU_A + "," + GPU_B, "HYPOLOOM_PHASE2_SCORE": "1.25"}}
    issues = []
    _audit_process_resource(process, resource, prepared.process_request.attempt_id, "experiment.py", ("python", "experiment.py"), None, issues)
    assert issues == []
    for mutate in ("missing", "foreign"):
        altered = copy.deepcopy(process)
        index = altered["argv"].index("/dev/nvidia0") - 1
        if mutate == "missing": del altered["argv"][index:index + 3]
        else: altered["argv"][index + 1:index + 3] = ["/dev/nvidia3", "/dev/nvidia3"]
        issues = []
        _audit_process_resource(altered, resource, prepared.process_request.attempt_id, "experiment.py", ("python", "experiment.py"), None, issues)
        assert {issue.code for issue in issues} == {"process_resource_binding_mismatch"}
