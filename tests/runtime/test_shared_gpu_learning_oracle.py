"""Raw independent P3 source and attribution audit; synthetic data only."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

import pytest

import hypoloom.oracle as oracle
from tests.runtime.test_shared_gpu_oracle import BUDGET, GPU_A, POOL, shared_case
from tests.runtime.test_multigpu_oracle import usage


MIB = 1024**2
WORKLOAD = {"model": "small", "batch": "8", "precision": "bf16"}


def estimate(ids=()):
    return {"workload": dict(WORKLOAD), "peak_memory_mib": 1200,
        "basis": "Prediction with headroom", "measurement_attempt_ids": list(ids)}


def submit(name="current", *, stamp=100, ids=(), original_ids=()):
    request = {"schema_version": 18, "attempt_id": name + "-agent", "attempt_ordinal": 1,
        "run_id": "run", "owner_workflow_id": "run", "command_id": name + "-node",
        "command": "EXPERIMENT_EXECUTION", "component_id": "component", "role": "expert",
        "method_family_id": "family", "task_context": {"kind": "RESEARCH_NODE", "key": {
            "node_kind": "EXPERIMENT_EXECUTION", "run_id": "run", "component_id": "component", "expert_slot": "expert"}}}
    arguments = {"candidate": {"resource_preference": "AUTO"}, "gpus": 1,
        "workspace_files": [{"path": "experiment.py", "content_base64": "cGFzcw=="}],
        "resource_estimate": estimate(original_ids)}
    snapshot = {"kind": "admitted-submit/1", "command_id": request["command_id"],
        "attempt_id": request["attempt_id"], "input_digest": oracle._canonical_json_digest(request),
        "arguments": {**copy.deepcopy(arguments), "resource_estimate": estimate(ids)}}
    snapshot["output_digest"] = oracle._canonical_json_digest(snapshot)
    data = {"source_request": request, "source_snapshot": snapshot, "arguments": arguments,
        "predecessor_experiment_id": None, "tool": "submit_experiment", "execution_node_command_id": request["command_id"]}
    command = {"command_id": request["command_id"] + "--submit--initial", "run_id": "run",
        "command_type": "CREATE_EXPERIMENT", "response_status": "accepted", "request_json": json.dumps(data),
        "created_at": datetime.fromtimestamp(stamp, timezone.utc).isoformat()}
    attempt = {"attempt_id": request["attempt_id"], "attempt_ordinal": 1, "run_id": "run",
        "workflow_id": "run", "command_id": request["command_id"], "kind": "AGENT",
        "started_at": stamp - 1, "ended_at": stamp + 1}
    return command, attempt, {attempt["attempt_id"]: {"request": request}}


def rewrite(command, change):
    data = json.loads(command["request_json"])
    change(data)
    snapshot = data["source_snapshot"]
    snapshot["input_digest"] = oracle._canonical_json_digest(data["source_request"])
    snapshot["output_digest"] = oracle._canonical_json_digest({k: v for k, v in snapshot.items() if k != "output_digest"})
    command["request_json"] = json.dumps(data)
    return data


def proof(*, resident_bytes=2000 * MIB, stamp=15, complete=True):
    return {"gpu_uuid": GPU_A, "root_process_group_id": 100, "root_start_time_ticks": 1000,
        "complete": complete, "reason": None if complete else "process_set_changed",
        "observed_at": stamp, "duration_seconds": 0.01,
        "resident_bytes": resident_bytes if complete else None,
        "processes": [{"pid": 102, "resident_bytes": resident_bytes, "ancestry": [
            {"pid": 102, "ppid": 101, "start_time_ticks": 1002},
            {"pid": 101, "ppid": 100, "start_time_ticks": 1001},
            {"pid": 100, "ppid": 1, "start_time_ticks": 1000}]}] if complete else []}


def learning_case():
    current, current_agent, current_obs = submit(ids=["prior-process"])
    prior, prior_agent, prior_obs = submit("prior", stamp=5)
    request, response = oracle._raw_submit_source(prior, [prior_agent], prior_obs)
    source_contract = {"source_submit_command_id": prior["command_id"],
        "source_agent_attempt_id": prior_agent["attempt_id"], "source_agent_output_digest": response["output_digest"],
        "resource_intent": {"kind": "GPU", "allowed_gpu_uuids": [GPU_A], "gpu_count": 1,
            "workload_memory_mib": 1200, "reserve_memory_mib": 1000}}
    source_fact = {"fact_id": "prior-fact", "fact_type": "ExperimentPreregistered", "run_id": "run",
        "experiment_id": "prior-experiment", "execution_id": "prior-execution"}
    source_payload = {"component_id": "component", "method_family_id": "family", "execution_contract": source_contract}
    process = {"attempt_id": "prior-process", "kind": "PROCESS", "state": "CLOSED", "run_id": "run",
        "workflow_id": "prior-experiment", "experiment_id": "prior-experiment", "execution_id": "prior-execution",
        "process_group_id": 100, "started_at": 10, "ended_at": 20, "terminal_reason": "SUCCEEDED"}
    sample = proof()
    observation = {"terminal_reason": "SUCCEEDED", "returncode": 0, "resource_usage": {**usage(GPU_A),
        "owned_memory": {"sample_count": 1, "unknown_sample_count": 0, "latest": sample, "peak": copy.deepcopy(sample)}}}
    dispatch = {"attempt_id": "prior-process", "resource_grant": {"kind": "GPU", "gpu_uuid": GPU_A},
        "gpu_pool_key": {"namespace": POOL["namespace"], "run_id": "run", "execution_id": "prior-execution", "attempt_id": "prior-process"}}
    transition = {"row_id": "dispatch", "run_id": "run", "workflow_id": "prior-experiment", "payload_json": json.dumps(dispatch)}
    context = {"facts": [source_fact], "fact_payloads": {"prior-fact": source_payload},
        "attempts": [current_agent, prior_agent, process], "observations": {**current_obs, **prior_obs, "prior-process": observation},
        "commands": [current, prior], "transitions": [transition]}
    case = shared_case()
    request, response = oracle._raw_submit_source(current, context["attempts"], context["observations"])
    case[2].update(request)
    case[3].clear(); case[3].update(response)
    case[1]["source_submit_command_id"] = current["command_id"]
    case[1]["resource_intent"]["workload_memory_mib"] = 2560
    case[0].update(component_id="component", method_family_id="family")
    return case, context


def audit(case, context=None, *, budget=BUDGET):
    issues = []
    resource = oracle._audit_resource_contract("current-fact", *case[:4], budget, issues,
        document=case[4], research_contract_version="hypoloom-research-v12", pool_binding=POOL,
        learning_context=context)
    return resource, issues


def test_host_added_existing_sources_preserve_agent_prediction_and_raw_idempotency():
    command, attempt, observations = submit(ids=["a", "b"], original_ids=["a"])
    assert oracle._raw_submit_source(command, [attempt], observations) is not None
    data = json.loads(command["request_json"])
    assert data["arguments"]["resource_estimate"]["measurement_attempt_ids"] == ["a"]


@pytest.mark.parametrize("change", ["model", "peak", "basis", "drop_explicit", "duplicates", "extra",
    "too_many", "nested", "missing_original", "old_aci", "other_argument"])
def test_recomputed_digest_cannot_hide_illegal_source_enrichment(change):
    command, attempt, observations = submit(ids=["a", "b"], original_ids=["a"])
    def alter(data):
        value = data["source_snapshot"]["arguments"]["resource_estimate"]
        if change == "model": value["workload"]["model"] = "other"
        elif change == "peak": value["peak_memory_mib"] = 999
        elif change == "basis": value["basis"] = "other"
        elif change == "drop_explicit": value["measurement_attempt_ids"] = ["b"]
        elif change == "duplicates": value["measurement_attempt_ids"] = ["a", "a"]
        elif change == "extra": value["extra"] = 1
        elif change == "too_many": value["measurement_attempt_ids"] = [str(i) for i in range(33)]
        elif change == "nested": value["workload"]["nested"] = {"x": "y"}
        elif change == "missing_original": del data["arguments"]["resource_estimate"]
        elif change == "old_aci": data["source_request"]["schema_version"] = 17
        else: data["source_snapshot"]["arguments"]["gpus"] = 2
    changed = rewrite(command, alter)
    observations[attempt["attempt_id"]]["request"] = changed["source_request"]
    assert oracle._raw_submit_source(command, [attempt], observations) is None


def test_v12_budget_is_independently_resolved_from_exact_closed_owned_samples():
    case, context = learning_case()
    resource, issues = audit(case, context)
    assert issues == [] and resource["source_valid"] is True


@pytest.mark.parametrize("success,admitted", [(True, 2560), (False, 8192)])
def test_initial_budget_reduction_requires_successful_raw_measurement_source(success, admitted):
    case, context = learning_case()
    budget = {**BUDGET, "initial_vram_per_experiment": 8192}
    prior = context["fact_payloads"]["prior-fact"]["execution_contract"]
    prior["resource_intent"]["workload_memory_mib"] = 8192
    if not success:
        context["attempts"][-1]["terminal_reason"] = "FAILED"
        context["observations"]["prior-process"].update(terminal_reason="FAILED", returncode=1)
    case[1]["resource_intent"]["workload_memory_mib"] = admitted
    resource, issues = audit(case, context, budget=budget)
    assert issues == [] and resource["source_valid"] is True
    case[1]["resource_intent"]["workload_memory_mib"] = 2560 if not success else 8192
    _, issues = audit(case, context, budget=budget)
    assert "resource_intent_mismatch" in {issue.code for issue in issues}


@pytest.mark.parametrize("change", ["budget", "foreign_run", "wrong_component", "wrong_role", "wrong_family", "wrong_workload",
    "open", "future", "missing", "agent", "source_digest", "prior_budget", "dispatch_owner", "root", "starttime", "sum",
    "ancestry", "foreign_process", "duplicate_process", "sample_time", "duration", "uuid", "unknown_credit", "unknown_bytes"])
def test_scoped_source_and_attribution_corruption_is_not_a_valid_learned_budget(change):
    case, context = learning_case()
    process = context["attempts"][-1]
    source_payload = context["fact_payloads"]["prior-fact"]
    owned = context["observations"]["prior-process"]["resource_usage"]["owned_memory"]
    if change == "budget": case[1]["resource_intent"]["workload_memory_mib"] = 1200
    elif change == "foreign_run": process["run_id"] = "foreign"
    elif change == "wrong_component": source_payload["component_id"] = "foreign"
    elif change == "wrong_family": source_payload["method_family_id"] = "foreign"
    elif change == "wrong_role":
        source = context["commands"][-1]
        data = rewrite(source, lambda data: data["source_request"].update(role="foreign"))
        context["observations"]["prior-agent"]["request"] = data["source_request"]
    elif change == "wrong_workload":
        rewrite(context["commands"][-1], lambda data: data["source_snapshot"]["arguments"]["resource_estimate"]["workload"].update(batch="16"))
    elif change == "open": process["state"] = "OPEN"
    elif change == "future": process["ended_at"] = 101
    elif change == "missing": context["attempts"].pop()
    elif change == "agent": process["kind"] = "AGENT"
    elif change == "source_digest": source_payload["execution_contract"]["source_agent_output_digest"] = "0" * 64
    elif change == "prior_budget": source_payload["execution_contract"]["resource_intent"]["workload_memory_mib"] = 9999
    elif change == "dispatch_owner":
        dispatch = json.loads(context["transitions"][0]["payload_json"])
        dispatch["gpu_pool_key"]["attempt_id"] = "foreign"
        context["transitions"][0]["payload_json"] = json.dumps(dispatch)
    elif change == "unknown_credit": owned["unknown_sample_count"] = 1
    elif change == "unknown_bytes": owned["latest"].update(complete=False, reason="process_set_changed"); owned["unknown_sample_count"] = 1
    else:
        for sample in (owned["latest"], owned["peak"]):
            if change == "root": sample["root_process_group_id"] = 999
            elif change == "starttime": sample["root_start_time_ticks"] = 999
            elif change == "sum": sample["resident_bytes"] += 1
            elif change == "ancestry": sample["processes"][0]["ancestry"][0]["ppid"] = 999
            elif change == "foreign_process": sample["processes"][0]["ancestry"][-1]["pid"] = 999
            elif change == "duplicate_process": sample["processes"].append(copy.deepcopy(sample["processes"][0]))
            elif change == "sample_time": sample["observed_at"] = 25
            elif change == "duration": sample["duration_seconds"] = 5
            elif change == "uuid": sample["gpu_uuid"] = "foreign"
    _, issues = audit(case, context)
    assert issues


def test_unknown_coverage_does_not_erase_its_diagnostic_peak_or_invent_learning():
    case, context = learning_case()
    owned = context["observations"]["prior-process"]["resource_usage"]["owned_memory"]
    owned.update(sample_count=2, unknown_sample_count=1, latest=proof(stamp=16, complete=False))
    case[1]["resource_intent"]["workload_memory_mib"] = 1200
    assert audit(case, context)[1] == []


def test_workload_reported_oom_uses_prior_budget_with_headroom_not_device_pressure():
    case, context = learning_case()
    process = context["attempts"][-1]
    process["terminal_reason"] = "FAILED"
    observation = context["observations"]["prior-process"]
    observation.update(terminal_reason="FAILED", returncode=1, resource_failure="gpu_out_of_memory",
        resource_failure_excerpt="torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 1.00 GiB")
    observation["resource_usage"]["owned_memory"] = {"sample_count": 0, "unknown_sample_count": 0, "latest": None, "peak": None}
    case[1]["resource_intent"]["workload_memory_mib"] = 1536
    assert audit(case, context)[1] == []
    observation["resource_failure_excerpt"] = "A busy device has memory pressure"
    assert audit(case, context)[1]


def test_omitted_estimate_retains_floor_and_v11_does_not_acquire_learning_semantics():
    case = shared_case()
    case[2]["schema_version"] = 18
    assert audit(case)[1] == []
    case[3]["arguments"]["resource_estimate"] = estimate()
    case[1]["resource_intent"]["workload_memory_mib"] = 1200
    assert audit(case)[1] == []
    issues = []
    oracle._audit_resource_contract("fact", *case[:4], BUDGET, issues, document=case[4],
        research_contract_version="hypoloom-research-v11", pool_binding=POOL)
    assert issues


@pytest.mark.parametrize("change", [None, "sum", "missing_owned", "empty_count", "unknown_numeric"])
@pytest.mark.parametrize("performance", [False, True])
def test_terminal_observation_uses_the_same_independent_ownership_check(change, performance):
    case, context = learning_case()
    resource, issues = audit(case, context)
    process = context["attempts"][-1]
    dispatch = json.loads(context["transitions"][0]["payload_json"])
    dispatch["gpu_uuid"] = GPU_A
    observation = context["observations"]["prior-process"]
    if performance:
        # This fixture defaults to the retained, unprefixed memory shape. Device
        # performance samples are emitted with the current prefixed shape.
        usage = observation["resource_usage"]
        for name in ("total_memory_mib", "initial_free_memory_mib", "minimum_free_memory_mib",
                     "latest_free_memory_mib", "latest_used_memory_mib", "latest_utilization_percent"):
            usage[f"gpu_{name}"] = usage.pop(name)
        usage.update(gpu_latest_power_draw_mw=210000,
            gpu_latest_power_limit_mw=350000, gpu_latest_temperature_c=65,
            gpu_latest_sm_clock_mhz=1695)
    bound = oracle._audit_shared_process_resource(resource, dispatch, process, observation, issues)
    assert issues == []
    owned = observation["resource_usage"]["owned_memory"]
    if change == "sum": owned["peak"]["resident_bytes"] += 1
    elif change == "missing_owned": del observation["resource_usage"]["owned_memory"]
    elif change == "empty_count": owned["sample_count"] = 0
    elif change == "unknown_numeric":
        owned["latest"].update(complete=False, reason="process_set_changed")
        owned.update(sample_count=2, unknown_sample_count=1)
    oracle._audit_process_resource_observation(observation, bound, process["attempt_id"], issues)
    assert bool(issues) is (change is not None)


def test_independent_raw_budget_does_not_call_production_learning_or_domain_validator(monkeypatch):
    import hypoloom.domain as domain
    import hypoloom.resource_experience as production
    def forbidden(*_args, **_kwargs): raise AssertionError("production resource validator was called")
    monkeypatch.setattr(domain.ResourceEstimate, "from_dict", forbidden)
    monkeypatch.setattr(production, "resolve_workload_memory_mib", forbidden)
    case, context = learning_case()
    assert audit(case, context)[1] == []
