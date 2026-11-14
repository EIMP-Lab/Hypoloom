"""Qualification configuration through the actual shipped Profile compiler."""
from pathlib import Path

import yaml

from hypoloom.agent_aci import AgentBudget
from hypoloom.profile_v4 import project_profile_v4, resolve_node_runtime


def verifier_runtime(model, *, effort="low", invocation=None):
    template = Path(__file__).resolve().parents[2] / "profiles/templates/multi-expert-en"
    documents = {p.stem: yaml.safe_load(p.read_text()) for p in template.glob("*.yaml")}
    documents["experts"]["defaults"]["model"] = model
    documents["experts"]["verifier"] = {"model": model, "effort": effort}
    if invocation is not None:
        documents["methodology"]["budget"]["agent_invocation"].update(invocation)
    compiled = project_profile_v4(**documents)
    assert not compiled.issues, compiled.issues
    contract = {"methodology": {"controls": {"product": compiled.settings}}}
    runtime = resolve_node_runtime(contract, None, "verifier")
    budget = AgentBudget.from_dict(compiled.projection.methodology["budget"]["agent_invocation"])
    return runtime, budget
