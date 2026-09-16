from hypoloom.agent_aci import EmbeddedInput
from hypoloom.oracle import _audit_resource_contract
from tests.runtime.test_compute_resources import GPU_A, GPU_B, capabilities

import pytest


@pytest.mark.parametrize("selected,gpus,valid", [
    ("auto", (GPU_A, GPU_B), True),
    ([2], (GPU_A,), True),
    ([GPU_A.uuid], (GPU_A,), True),
    ([], (), True),
    ([], (GPU_A,), False),
    ([2], (GPU_B,), False),
    ([GPU_A.uuid], (GPU_B,), False),
])
def test_oracle_checks_frozen_capability_against_original_profile_without_gpu(selected, gpus, valid):
    request = {"input_artifacts": [EmbeddedInput.from_bytes("node--compute-capabilities", "application/json",
        capabilities(*gpus).canonical_bytes()).to_dict()]}
    contract = {"resource_preference": "CPU", "resource_grant": {"kind": "CPU", "gpu_uuid": None},
        "sandbox_profile": "bwrap-cpu-no-network/2", "environment": [["CUDA_DEVICE_ORDER", "PCI_BUS_ID"], ["CUDA_VISIBLE_DEVICES", ""]]}
    issues = []
    _audit_resource_contract("fact", {"schema_version": 5, "resource_preference": "CPU"}, contract, request,
        {"typed_candidate": {"resource_preference": "CPU"}}, {}, issues,
        document={"methodology": {"controls": {"product": {"safety": {"gpus": selected}}}}})
    assert ("compute_capability_profile_mismatch" not in {item.code for item in issues}) is valid
    assert not any("digest" in item.code for item in issues)
