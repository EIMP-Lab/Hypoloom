"""Independent shared-GPU source/dispatch checks with synthetic GPU data only."""

from __future__ import annotations

import copy
import json
import sqlite3

import pytest

import hypoloom.oracle as oracle
from hypoloom.agent_aci import EmbeddedInput
from tests.runtime.test_multigpu_oracle import GPU_A, GPU_B, GPU_C, contract_case, usage


POOL = {
    "socket_path": "/tmp/configured-pool.sock",
    "namespace": "private-root",
    "server_uid": 1000,
}
BUDGET = {"min_vram_per_experiment": 1000, "gpu_vram_reserve_mb": 1000}


def shared_case(*, count=1):
    payload, contract, request, response, document = contract_case(count=count)
    contract.pop("resource_grant")
    contract["resource_intent"] = {
        "kind": "GPU",
        "allowed_gpu_uuids": [GPU_A, GPU_B, GPU_C],
        "gpu_count": count,
        "workload_memory_mib": 1000,
        "reserve_memory_mib": 1000,
    }
    contract["environment"][-1][-1] = ""
    contract["sandbox_profile"] = (
        "bwrap-gpu-single-no-network/1"
        if count == 1
        else "bwrap-gpu-multi-no-network/1"
    )
    return payload, contract, request, response, document


def audit_source(case, *, version="hypoloom-research-v11", pool=POOL):
    payload, contract, request, response, document = case
    issues = []
    resource = oracle._audit_resource_contract(
        "fact",
        payload,
        contract,
        request,
        response,
        BUDGET,
        issues,
        document=document,
        research_contract_version=version,
        pool_binding=pool,
    )
    return resource, issues


def process_case(uuid=GPU_C, *, ordinal=1):
    attempt = {
        "attempt_id": f"attempt-{ordinal}",
        "execution_id": "execution",
        "run_id": "run",
        "process_group_id": 2345 + ordinal,
        "state": "CLOSED",
        "terminal_reason": "SUCCEEDED",
    }
    intent = {
        "resource_grant": {"kind": "GPU", "gpu_uuid": uuid},
        "gpu_uuid": uuid,
        "gpu_pool_key": {
            "namespace": POOL["namespace"],
            "run_id": "run",
            "execution_id": "execution",
            "attempt_id": attempt["attempt_id"],
        },
    }
    return (
        intent,
        attempt,
        {"terminal_reason": "SUCCEEDED", "resource_usage": usage(uuid)},
    )


def test_shared_source_does_not_repeat_snapshot_free_memory_or_busy_selection():
    case = shared_case()
    request = case[2]
    snapshot = {
        "schema_version": 3,
        "busy_gpu_uuids": [GPU_A, GPU_B, GPU_C],
        "gpus": [
            {
                "index": index,
                "uuid": uuid,
                "name": uuid,
                "total_memory_mib": 24576,
                "free_memory_mib": 0,
                "driver_version": "synthetic",
            }
            for index, uuid in ((2, GPU_A), (0, GPU_B), (3, GPU_C))
        ],
    }
    request["input_artifacts"] = [
        EmbeddedInput.from_bytes(
            "execution--compute-capabilities",
            "application/json",
            json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode(),
        ).to_dict()
    ]
    resource, issues = audit_source(case)
    assert issues == []
    assert resource["shared"] is True


def test_configured_pool_may_use_the_existing_optional_server_uid():
    _, issues = audit_source(shared_case(), pool={**POOL, "server_uid": None})
    assert issues == []


@pytest.mark.parametrize(
    "change",
    [
        "old_version",
        "no_pool",
        "pool_namespace",
        "pool_uid",
        "pool_relative_socket",
        "both_forms",
        "unknown_uuid",
        "missing_authorized_uuid",
        "duplicate_uuid",
        "unsorted_uuid",
        "count",
        "count_bool",
        "workload_zero",
        "workload_bool",
        "workload_floor",
        "reserve_floor",
        "extra_intent_field",
        "frozen_cuda",
        "wrong_sandbox",
        "cpu_preference",
        "profile_limit",
    ],
)
def test_shared_intent_cannot_replace_source_profile_or_version_authority(change):
    case = shared_case()
    raw = case[1]["resource_intent"]
    pool = copy.deepcopy(POOL)
    version = "hypoloom-research-v11"
    if change == "old_version":
        version = "hypoloom-research-v10"
    elif change == "no_pool":
        pool = None
    elif change == "pool_namespace":
        pool["namespace"] = ""
    elif change == "pool_uid":
        pool["server_uid"] = True
    elif change == "pool_relative_socket":
        pool["socket_path"] = "relative.sock"
    elif change == "both_forms":
        case[1]["resource_grant"] = {"kind": "GPU", "gpu_uuid": GPU_C}
    elif change == "unknown_uuid":
        raw["allowed_gpu_uuids"].append("GPU-dddddddd-dddd-dddd-dddd-dddddddddddd")
    elif change == "missing_authorized_uuid":
        raw["allowed_gpu_uuids"].pop()
    elif change == "duplicate_uuid":
        raw["allowed_gpu_uuids"].append(GPU_C)
    elif change == "unsorted_uuid":
        raw["allowed_gpu_uuids"].reverse()
    elif change == "count":
        raw["gpu_count"] = 2
    elif change == "count_bool":
        raw["gpu_count"] = True
    elif change == "workload_zero":
        raw["workload_memory_mib"] = 0
    elif change == "workload_bool":
        raw["workload_memory_mib"] = True
    elif change == "workload_floor":
        raw["workload_memory_mib"] = 999
    elif change == "reserve_floor":
        raw["reserve_memory_mib"] = 999
    elif change == "extra_intent_field":
        raw["resident_memory_mib"] = 1000
    elif change == "frozen_cuda":
        case[1]["environment"][-1][-1] = GPU_A
    elif change == "wrong_sandbox":
        case[1]["sandbox_profile"] = "bwrap-cpu-no-network/2"
    elif change == "cpu_preference":
        case[3]["arguments"]["candidate"]["resource_preference"] = "CPU"
    else:
        case[4]["methodology"]["controls"]["product"]["safety"]["gpus"] = [GPU_A]
    _, issues = audit_source(case, version=version, pool=pool)
    assert issues


def test_retry_grants_and_telemetry_are_validated_per_attempt_without_mutating_source():
    resource, issues = audit_source(shared_case())
    assert issues == []
    original = copy.deepcopy(resource)
    for ordinal, uuid in enumerate((GPU_A, GPU_C), 1):
        intent, attempt, observation = process_case(uuid, ordinal=ordinal)
        bound = oracle._audit_shared_process_resource(
            resource, intent, attempt, observation, issues
        )
        assert bound["gpu_uuids"] == (uuid,)
        assert bound["environment"]["CUDA_VISIBLE_DEVICES"] == uuid
        oracle._audit_process_resource_observation(
            observation, bound, attempt["attempt_id"], issues
        )
    assert issues == []
    assert resource == original


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "cpu",
        "foreign_uuid",
        "extra_gpu",
        "identity",
        "namespace",
        "run",
        "execution",
        "attempt",
        "key_extra",
    ],
)
def test_dispatch_grant_and_pool_key_bind_the_exact_attempt(change):
    resource, issues = audit_source(shared_case())
    intent, attempt, observation = process_case()
    if change == "missing":
        intent.pop("resource_grant")
    elif change == "cpu":
        intent["resource_grant"] = {"kind": "CPU", "gpu_uuid": None}
    elif change == "foreign_uuid":
        intent["resource_grant"]["gpu_uuid"] = (
            "GPU-dddddddd-dddd-dddd-dddd-dddddddddddd"
        )
    elif change == "extra_gpu":
        intent["resource_grant"] = {"kind": "GPU", "gpu_uuids": [GPU_A, GPU_C]}
    elif change == "identity":
        intent["gpu_uuid"] = GPU_A
    elif change == "key_extra":
        intent["gpu_pool_key"]["uid"] = 1000
    else:
        field = {
            "namespace": "namespace",
            "run": "run_id",
            "execution": "execution_id",
            "attempt": "attempt_id",
        }[change]
        intent["gpu_pool_key"][field] = "other"
    oracle._audit_shared_process_resource(
        resource, intent, attempt, observation, issues
    )
    assert issues


def test_unspawned_diagnostic_requires_no_actual_grant_or_pool_key():
    resource, issues = audit_source(shared_case())
    _, attempt, _ = process_case()
    attempt.update(process_group_id=None, terminal_reason="CANCELLED")
    observation = {"synthetic_before_spawn": True, "terminal_reason": "CANCELLED"}
    diagnostic = oracle._audit_shared_process_resource(
        resource, {}, attempt, observation, issues
    )
    assert issues == []
    assert diagnostic["expected_kind"] == "CPU" and diagnostic["gpu_uuids"] == ()
    assert diagnostic["environment"]["CUDA_VISIBLE_DEVICES"] == ""
    for invalid in (
        {"gpu_pool_key": process_case()[0]["gpu_pool_key"]},
        {"gpu_uuid": GPU_A},
    ):
        failures = []
        oracle._audit_shared_process_resource(
            resource, invalid, attempt, observation, failures
        )
        assert failures


def test_legacy_snapshot_ranking_still_rejects_an_authorized_but_wrong_grant():
    case = contract_case(count=1, selected=(GPU_C,))
    _, issues = audit_source(case, version="hypoloom-research-v10", pool=None)
    assert "resource_resolution_mismatch" in {issue.code for issue in issues}


def test_shared_attempt_bwrap_mounts_visibility_and_usage_follow_actual_grant(tmp_path):
    from hypoloom.domain import ResourceGrant, ResourceKind
    from hypoloom.execution_workspace import ExecutionWorkspace
    from test_execution_workspace import candidate_pair, plan
    from test_multigpu_resources import _resources

    resources = _resources(tmp_path)
    case = shared_case()
    case[1]["environment"].append(["HYPOLOOM_PHASE2_SCORE", "1.25"])
    resource, issues = audit_source(case)
    intent, attempt, observation = process_case(GPU_C)
    grant = ResourceGrant(kind=ResourceKind.GPU, gpu_uuid=GPU_C)
    state = tmp_path / "state"
    artifacts = state / "artifacts"
    artifacts.mkdir(parents=True)
    workspace = ExecutionWorkspace(
        artifacts,
        state_root=state,
        **resources.workspace_kwargs(grant, resources.snapshot()),
    )
    prepared = plan(workspace, *candidate_pair())
    bound = oracle._audit_shared_process_resource(
        resource, intent, attempt, observation, issues
    )
    process = prepared.process_request.to_dict()
    oracle._audit_process_resource(
        process,
        bound,
        prepared.process_request.attempt_id,
        "experiment.py",
        ("python", "experiment.py"),
        None,
        issues,
    )
    assert issues == []
    for change in ("wrong_mount", "wrong_visibility", "missing_mount"):
        altered = copy.deepcopy(process)
        index = altered["argv"].index("/dev/nvidia3") - 1
        if change == "wrong_mount":
            altered["argv"][index + 1 : index + 3] = ["/dev/nvidia2", "/dev/nvidia2"]
        elif change == "missing_mount":
            del altered["argv"][index : index + 3]
        else:
            altered["argv"][altered["argv"].index("CUDA_VISIBLE_DEVICES") + 1] = GPU_A
        errors = []
        oracle._audit_process_resource(
            altered,
            bound,
            prepared.process_request.attempt_id,
            "experiment.py",
            ("python", "experiment.py"),
            None,
            errors,
        )
        assert {issue.code for issue in errors} == {"process_resource_binding_mismatch"}
    errors = []
    oracle._audit_process_resource_observation(
        {**observation, "resource_usage": usage(GPU_A)},
        bound,
        attempt["attempt_id"],
        errors,
    )
    assert {issue.code for issue in errors} == {"process_resource_observation_mismatch"}


def _overlap_database():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
CREATE TABLE workflow_runs(run_id, research_contract_version);
CREATE TABLE research_facts(run_id, fact_type, execution_id, payload_json);
CREATE TABLE workflow_transitions(run_id, workflow_id, workflow_kind, revision, command_id, payload_json);
CREATE TABLE workflow_commands(command_id, command_type);
CREATE TABLE execution_attempts(attempt_id, run_id, started_at, ended_at, execution_id, kind,
    state, terminal_reason, process_group_id, observation_json, experiment_id);
""")
    for ordinal, run in enumerate(("alpha", "beta"), 1):
        payload, contract, request, response, document = shared_case()
        response = {"typed_candidate": {"resource_preference": "AUTO"}}
        document["budget"] = BUDGET
        source_id = run + "--source"
        attempt_id = run + "--process"
        contract["source_agent_attempt_id"] = source_id
        payload["execution_contract"] = contract
        dispatch = {
            "attempt_id": attempt_id,
            "resource_grant": {"kind": "GPU", "gpu_uuid": GPU_C},
            "gpu_uuid": GPU_C,
            "gpu_pool_key": {
                "namespace": POOL["namespace"],
                "run_id": run,
                "execution_id": run + "--execution",
                "attempt_id": attempt_id,
            },
        }
        connection.execute(
            "INSERT INTO workflow_runs VALUES(?,?)", (run, "hypoloom-research-v11")
        )
        connection.execute(
            "INSERT INTO research_facts VALUES(?,?,?,?)",
            (run, "GoalDefined", None, json.dumps({"research_contract": document})),
        )
        connection.execute(
            "INSERT INTO research_facts VALUES(?,?,?,?)",
            (run, "ExperimentPreregistered", run + "--execution", json.dumps(payload)),
        )
        connection.execute(
            "INSERT INTO workflow_transitions VALUES(?,?,?,?,?,?)",
            (run, run, "RUN", 1, run + "--create", json.dumps({"gpu_pool": POOL})),
        )
        connection.execute(
            "INSERT INTO workflow_transitions VALUES(?,?,?,?,?,?)",
            (
                run,
                run + "--experiment",
                "EXPERIMENT",
                2,
                run + "--execute",
                json.dumps(dispatch),
            ),
        )
        connection.execute(
            "INSERT INTO workflow_commands VALUES(?,?)", (run + "--execute", "EXECUTE")
        )
        connection.execute(
            "INSERT INTO execution_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                source_id,
                run,
                0,
                1,
                None,
                "AGENT",
                "CLOSED",
                "SUCCEEDED",
                None,
                json.dumps({"request": request, "response": response}),
                run + "--experiment",
            ),
        )
        connection.execute(
            "INSERT INTO execution_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                attempt_id,
                run,
                ordinal + 1,
                ordinal + 5,
                run + "--execution",
                "PROCESS",
                "CLOSED",
                "SUCCEEDED",
                1000 + ordinal,
                "{}",
                run + "--experiment",
            ),
        )
    return connection


def test_two_valid_shared_grants_may_overlap_across_runs():
    with _overlap_database() as connection:
        issues = []
        oracle._audit_gpu_attempt_overlaps(connection, "alpha", issues)
        assert issues == []


@pytest.mark.parametrize(
    "change", ["version", "pool_key", "profile_floor", "legacy_peer"]
)
def test_overlap_is_not_exempt_when_shared_authority_is_invalid_or_peer_is_legacy(
    change,
):
    with _overlap_database() as connection:
        if change == "version":
            connection.execute(
                "UPDATE workflow_runs SET research_contract_version='hypoloom-research-v10' WHERE run_id='beta'"
            )
        elif change == "pool_key":
            connection.execute(
                "UPDATE workflow_transitions SET payload_json=json_set(payload_json,'$.gpu_pool_key.namespace','other') WHERE command_id='beta--execute'"
            )
        elif change == "profile_floor":
            connection.execute(
                "UPDATE research_facts SET payload_json=json_set(payload_json,'$.research_contract.budget.min_vram_per_experiment',2000) WHERE run_id='beta' AND fact_type='GoalDefined'"
            )
        else:
            connection.execute(
                "UPDATE research_facts SET payload_json=json_set(json_remove(payload_json,'$.execution_contract.resource_intent'),'$.execution_contract.resource_grant',json(?)) WHERE run_id='beta' AND fact_type='ExperimentPreregistered'",
                (json.dumps({"kind": "GPU", "gpu_uuid": GPU_C}),),
            )
            connection.execute(
                "UPDATE workflow_runs SET research_contract_version='hypoloom-research-v10' WHERE run_id='beta'"
            )
        issues = []
        oracle._audit_gpu_attempt_overlaps(connection, "alpha", issues)
        assert len(issues) == 1 and issues[0].code == "same_gpu_attempt_overlap"
