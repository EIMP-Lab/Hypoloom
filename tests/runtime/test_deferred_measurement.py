from dataclasses import replace

import pytest

from hypoloom.domain import ResearchContract, fact_to_dict
from tests.runtime.test_domain import research_contract
from tests.runtime.test_measurement_contract import declaration


def deferred_contract():
    raw = research_contract()
    raw["schema_version"] = 2
    raw["components"][0]["result"] = {"kind": "metric", "deferred": True}
    raw["goal"]["summary_metric"] = {"name": "required_component_completion", "direction": "MAXIMIZE",
                                      "target": "1", "display_only": True}
    return raw


def test_deferred_profile_is_explicit_not_an_artifact_placeholder():
    contract = ResearchContract.from_mapping(deferred_contract())
    component = contract.components[0]
    assert component.deferred_measurement is True
    assert component.metric is None
    assert component.result_kind.value == "metric"


def test_old_research_contract_cannot_silently_admit_deferred_semantics():
    raw = deferred_contract()
    raw["schema_version"] = 1
    with pytest.raises(ValueError):
        ResearchContract.from_mapping(raw)


def test_deferred_result_cannot_invent_a_default_target():
    raw = deferred_contract()
    raw["components"][0]["result"]["target"] = "1"
    with pytest.raises(ValueError):
        ResearchContract.from_mapping(raw)


def test_kernel_freezes_measurement_on_first_hypothesis_preregistration():
    from hypoloom.research_kernel import ResearchKernel, ResearchValidationError
    from tests.runtime.test_research_kernel import candidates, MemoryResearchView

    goal, hypothesis, experiment = candidates()[:3]
    measure = declaration()
    goal = replace(goal, research_contract=deferred_contract())
    hypothesis = replace(hypothesis, measurement=measure)
    experiment = replace(experiment, measurement=measure, metric=measure.metric)
    kernel, view = ResearchKernel(), MemoryResearchView()
    for fact in (goal, hypothesis, experiment):
        view.add(kernel.construct_fact(fact, view))
    payload = fact_to_dict(experiment)
    assert payload["measurement"] == measure.to_dict()
    successor = replace(experiment, fact_id="fact-exp-2", experiment_id="exp-2", execution_id="exec-2",
                        measurement=replace(measure, parse_rule="take best rather than median"))
    with pytest.raises(ResearchValidationError, match="measurement"):
        kernel.construct_fact(successor, view)


def test_kernel_does_not_accept_an_undeclared_hypothesis_measurement():
    from hypoloom.research_kernel import ResearchKernel, ResearchValidationError
    from tests.runtime.test_research_kernel import candidates, MemoryResearchView

    goal, hypothesis = candidates()[:2]
    kernel, view = ResearchKernel(), MemoryResearchView()
    view.add(kernel.construct_fact(replace(goal, research_contract=deferred_contract()), view))
    with pytest.raises(ResearchValidationError, match="measurement"):
        kernel.construct_fact(hypothesis, view)


def test_aci_admits_parser_sources_for_deferred_submit_but_not_retained_aci():
    from hypoloom.agent_aci import AgentACIError, WorkspaceArtifact, validate_experiment_submission
    from hypoloom.domain import AgentCommand
    from hypoloom.fake_agent import FakeAgent, default_request

    request = default_request(AgentCommand.EXPERIMENT_EXECUTION)
    response = FakeAgent().invoke(default_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT))
    candidate = {**response.typed_candidate, "measurement": declaration().to_dict(),
                 "execution_argv": ["/bin/sh", "/input/experiment.sh"],
                 "metrics": [declaration().to_dict()["metric"]]}
    request = replace(request, method_family_id=candidate["method_family_id"])
    arguments = {"candidate": candidate, "command": ["/bin/sh", "/input/experiment.sh"],
                 "workspace_files": [WorkspaceArtifact.from_bytes(name, content).to_dict() for name, content in (
                     ("experiment.sh", b"echo raw > timings.csv"),
                     ("parse_metrics.py", b"print('parser')"), ("implementation.patch", b""))]}
    validated, files, _ = validate_experiment_submission(request, arguments)
    assert validated.measurement == declaration()
    assert "parse_metrics.py" in files
    with pytest.raises(AgentACIError):
        validate_experiment_submission(replace(request, schema_version=11), arguments)


@pytest.mark.parametrize("constraint", [{"metric": "other_name"}, {"direction": "MAXIMIZE"}])
def test_partial_operator_metric_cannot_be_overridden_without_a_target(constraint):
    from hypoloom.research_kernel import ResearchKernel, ResearchValidationError
    from tests.runtime.test_research_kernel import candidates, MemoryResearchView
    goal, hypothesis = candidates()[:2]
    raw = deferred_contract()
    raw["components"][0]["result"].update(constraint)
    kernel, view = ResearchKernel(), MemoryResearchView()
    view.add(kernel.construct_fact(replace(goal, research_contract=raw), view))
    with pytest.raises(ResearchValidationError, match="measurement"):
        kernel.construct_fact(replace(hypothesis, measurement=declaration()), view)


def test_partial_operator_metric_allows_expert_target_and_parser():
    from hypoloom.research_kernel import ResearchKernel
    from tests.runtime.test_research_kernel import candidates, MemoryResearchView
    goal, hypothesis = candidates()[:2]
    raw = deferred_contract()
    raw["components"][0]["result"].update(metric=declaration().metric.name, direction="MINIMIZE")
    kernel, view = ResearchKernel(), MemoryResearchView()
    view.add(kernel.construct_fact(replace(goal, research_contract=raw), view))
    assert kernel.construct_fact(replace(hypothesis, measurement=declaration()), view).payload["measurement"] == declaration().to_dict()


def test_new_hypothesis_can_declare_different_measurement():
    from hypoloom.domain import MetricSpec, MetricDirection
    from hypoloom.research_kernel import ResearchKernel
    from tests.runtime.test_research_kernel import candidates, MemoryResearchView
    goal, hypothesis, experiment = candidates()[:3]
    kernel, view = ResearchKernel(), MemoryResearchView()
    for item in (replace(goal, research_contract=deferred_contract()), replace(hypothesis, measurement=declaration()),
                 replace(experiment, metric=declaration().metric, measurement=declaration())):
        view.add(kernel.construct_fact(item, view))
    measure = replace(declaration(), metric=MetricSpec(name="another_measurement", direction=MetricDirection.MAXIMIZE, target="20"))
    revised = replace(hypothesis, fact_id="fact-hyp-new", hypothesis_id="hyp-new", parent_hypothesis_id=hypothesis.hypothesis_id,
                      relation="revises", measurement=measure)
    assert kernel.construct_fact(revised, view).payload["measurement"] == measure.to_dict()


def test_native_seed_bytes_participate_in_existing_runtime_identity(tmp_path, monkeypatch):
    import hypoloom.workflow_host as module
    package = tmp_path / "hypoloom"
    seed = package / "skills" / "expert-seed" / "skills" / "methodology" / "SKILL.md"
    seed.parent.mkdir(parents=True)
    seed.write_text("methodology A")
    monkeypatch.setattr(module, "__file__", str(package / "workflow_host.py"))
    before = module.installed_run_version().runtime_environment_id
    seed.write_text("methodology B")
    assert module.installed_run_version().runtime_environment_id != before
