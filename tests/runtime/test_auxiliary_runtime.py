import pytest

from hypoloom.expert_loop import AuxiliaryTask, AuxiliaryRequestKind, ResearchNodeTask, ResearchNodeKey, ResearchNodeKind
from hypoloom.profile_language import apply_profile_language
from hypoloom.profile_v4 import project_profile_v4
from test_profile_v4 import documents


@pytest.mark.parametrize("kind", [AuxiliaryRequestKind.GRAPH_EVOLVE, AuxiliaryRequestKind.OBSERVER, AuxiliaryRequestKind.FINAL_REPORT])
def test_grounding_auxiliary_nodes_receive_frozen_profile_runtime_and_language(kind):
    source = documents(); settings = project_profile_v4(**source).settings
    contract = {"goal": {"language": "en"}, "methodology": {"controls": {"product": settings,
        "language_routes": {"overrides": {"cycle_report": "zh-CN"}}}}}
    task = AuxiliaryTask(kind=kind, run_id="run", cycle=1 if kind is not AuxiliaryRequestKind.FINAL_REPORT else None)
    config = {}; apply_profile_language(config, contract, task)
    assert config["node_runtime"]["model"] == "configured-model"
    assert config["node_runtime"]["effort"] == "high"
    assert config["node_runtime"]["safety"]["filesystem"] == settings["safety"]["filesystem"]
    assert config["language_route"]["output_language"] == ("zh-CN" if kind is AuxiliaryRequestKind.FINAL_REPORT else "en")


@pytest.mark.parametrize("kind", [ResearchNodeKind.ADJUDICATE, ResearchNodeKind.SELF_EVOLVE])
def test_cycle_tail_nodes_receive_role_runtime_instead_of_losing_all_profile_settings(kind):
    settings = project_profile_v4(**documents()).settings
    settings["experts"]["experts"][0]["model"] = "role-model"
    settings["experts"]["experts"][0]["stage_overrides"]["self_evolve"] = {"model": "evolution-model"}
    contract = {"goal": {"language": "en"}, "methodology": {"controls": {"product": settings}}}
    task = ResearchNodeTask(key=ResearchNodeKey(run_id="run", component_id="component", expert_slot="scientist", cycle=1, node_kind=kind))
    config = {}; apply_profile_language(config, contract, task)
    assert config["node_runtime"]["model"] == ("evolution-model" if kind is ResearchNodeKind.SELF_EVOLVE else "role-model")
