"""Fresh Host identity promotes the complete runtime without changing old wire bytes."""
from hypoloom.agent_aci import AgentRequest
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import default_request
from hypoloom.workflow_host import installed_run_version


def test_fresh_host_defaults_to_parallel_proposal_scheduling():
    assert installed_run_version().agent_aci_version == "26"


def test_retained_aci13_requests_remain_byte_compatible():
    request = default_request(AgentCommand.HYPOTHESIS_PROPOSAL)
    assert request.schema_version == 13
    assert request.required_output_schema == "ExpertLoopCandidate/v1"
    assert AgentRequest.from_json(request.to_json()).to_json() == request.to_json()


def test_existing_bundle_identity_includes_the_grounding_plugin_seed(tmp_path, monkeypatch):
    import hypoloom.workflow_host as module
    package = tmp_path / "hypoloom"
    seed = package / "skills/grounding-seed/skills/leader-trigger/SKILL.md"
    seed.parent.mkdir(parents=True)
    seed.write_text("original grounding trigger\n")
    monkeypatch.setattr(module, "__file__", str(package / "workflow_host.py"))
    before = module.installed_run_version()
    seed.write_text("changed grounding trigger\n")
    assert module.installed_run_version().runtime_environment_id != before.runtime_environment_id
