"""Opt-in real CUDA probe, independent native review, formal iteration and cleanup."""
from dataclasses import replace
import copy

from hypoloom.agent_aci import with_output_digest
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import FakeAgent
from test_native_gpu_g4 import _NATIVE_GPU, _run_native_gpu


@_NATIVE_GPU
def test_native_verifier_probes_real_cuda_before_formal_iteration(tmp_path, monkeypatch):
    invoke = FakeAgent.invoke
    def reviewed(self, request):
        response = invoke(self, request)
        if request.command is AgentCommand.VERIFY_IMPLEMENTATION and response.typed_candidate["probe"]:
            candidate = copy.deepcopy(response.typed_candidate)
            candidate["probe"]["timeout_seconds"] = 30
            candidate["probe"]["files"]["verification/probe.py"] = (
                "import torch,json\nfrom hypoloom_probe import check\nwith check('fixture') as c:\n"
                " c.true(torch.cuda.is_available(),'CUDA available')\n"
                " for i in range(torch.cuda.device_count()):\n"
                "  x=torch.randn(3,4,device=f'cuda:{i}',dtype=torch.float64,requires_grad=True)\n"
                "  f=lambda a: (a.square().sum(dim=1)+1).sqrt().mean()\n"
                "  c.true(torch.autograd.gradcheck(f,(x,)),'actual CUDA gradients')\n"
                "  torch.cuda.synchronize(i)\n"
                "print(json.dumps({'independent_cuda_probe':True,'devices':torch.cuda.device_count()}))\n")
            response = with_output_digest(replace(response, typed_candidate=candidate))
        return response
    monkeypatch.setattr(FakeAgent, "invoke", reviewed)
    _run_native_gpu(tmp_path, plural=True, implementation_probes=2)
