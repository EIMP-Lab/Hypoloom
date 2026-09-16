"""ACI count semantics; the Host enforces the admitted Profile capacity."""
from dataclasses import replace

import pytest

from hypoloom.agent_aci import AgentACIError, validate_experiment_submission
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import FakeAgent, default_request


def arguments(version=14, preference="GPU_REQUIRED", count=2):
    design = default_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT)
    response = FakeAgent().invoke(design)
    request = replace(default_request(AgentCommand.EXPERIMENT_EXECUTION), schema_version=version,
        method_family_id=design.method_family_id)
    return request, {"candidate": {**response.typed_candidate, "resource_preference": preference},
        "workspace_files": [item.to_dict() for item in response.proposed_workspace_artifacts], "gpus": count}


@pytest.mark.parametrize("count", [2, 3])
def test_aci14_preserves_exact_plural_gpu_count_for_host_admission(count):
    request, candidate = arguments(count=count)
    _, _, snapshot = validate_experiment_submission(request, candidate)
    assert snapshot["arguments"]["gpus"] == count


@pytest.mark.parametrize("version,preference,count", [(13, "GPU_REQUIRED", 2), (14, "CPU", 2),
    (14, "GPU_REQUIRED", 0), (14, "AUTO", True), (14, "AUTO", -1), (14, "AUTO", ["GPU-id"])])
def test_retained_protocol_and_conflicting_or_noncount_gpu_requests_still_reject(version, preference, count):
    request, candidate = arguments(version, preference, count)
    with pytest.raises(AgentACIError, match="resource_request"):
        validate_experiment_submission(request, candidate)
