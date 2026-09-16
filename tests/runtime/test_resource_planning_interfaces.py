"""Native resource advice stays separate from Host authority and retained ABI."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from hypoloom import host_tools_server
from hypoloom.agent_aci import (
    AgentACIError, AgentRequest, validate_experiment_submission, validate_submission_snapshot,
)
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import derive_research_node_views
from hypoloom.expert_plugin import ExpertPluginStore, SEED_ROOT
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.full_cc_runtime import HOST_TOOLS, host_tool_configuration
from hypoloom.host_tools_server import AgentToolServer, call_tool, scoped_tools
from test_host_tools_server import _finish


_RESOURCE_SKILL = "skills/resource-planning/SKILL.md"
_LIMIT = 131072
_ESTIMATE = {"workload": {"precision": "fp32", "batch": "8"},
             "peak_memory_mib": 2048, "basis": "Comparable measured workload plus headroom."}


def _submission(version=18):
    design = default_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT)
    response = FakeAgent().invoke(design)
    request = replace(default_request(AgentCommand.EXPERIMENT_EXECUTION), schema_version=version,
                      method_family_id=response.typed_candidate["method_family_id"])
    return request, {"candidate": dict(response.typed_candidate),
                     "workspace_files": [item.to_dict() for item in response.proposed_workspace_artifacts]}


@pytest.mark.parametrize("version", (17, 18))
@pytest.mark.parametrize("command", (AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, AgentCommand.EXPERIMENT_EXECUTION))
def test_resource_mcp_exposure_is_versioned_and_design_is_read_only(version, command):
    request = replace(default_request(command), schema_version=version)
    scope, tools = host_tool_configuration(request)
    if command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
        assert (scope, tools) == (("resources", ("get_resources",)) if version == 18 else (None, ()))
    else:
        assert scope == "execution"
        assert set(tools) == set(HOST_TOOLS) | ({"get_resources"} if version == 18 else set())
    assert AgentRequest.from_dict(request.to_dict()) == request
    if scope == "execution":
        submit = next(item for item in scoped_tools(scope, tools) if item["name"] == "submit_experiment")
        assert ("resource_estimate" in submit["inputSchema"]["properties"]) == (version == 18)


def test_design_resources_socket_never_captures_workspace_or_permits_mutation(tmp_path):
    calls = []
    def capture(_):
        raise AssertionError("read-only query attempted workspace capture")
    server = AgentToolServer(tmp_path / "resources.sock", "design-attempt",
        lambda attempt, tool, arguments: calls.append((attempt, tool, arguments)) or {"observed_at": "now"},
        capture, scope="resources")
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert _finish(server, pool.submit(call_tool, str(server.socket_path), "get_resources", {})) == {"observed_at": "now"}
        for tool, arguments in (("submit_experiment", {"candidate": {}}),
                                ("cancel_experiment", {"exp_id": "anything"}),
                                ("list_my_experiments", {})):
            with pytest.raises(ValueError, match="invocation scope"):
                call_tool(str(server.socket_path), tool, arguments)
        with pytest.raises(ValueError, match="unknown tool arguments"):
            call_tool(str(server.socket_path), "get_resources", {"role": "another"})
        assert calls == [("design-attempt", "get_resources", {})]
    finally:
        server.close()
    assert not server.socket_path.exists()


def test_design_resource_scope_cannot_be_expanded():
    with pytest.raises(ValueError, match="expands"):
        scoped_tools("resources", ("get_resources", "submit_experiment"))


def test_standalone_bridge_advertises_only_resource_query():
    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    result = subprocess.run((sys.executable, "-B", str(Path(host_tools_server.__file__)),
        "--scope", "resources", "--socket", "/unused-resource-query.sock"),
        input=json.dumps(message) + "\n", text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert [tool["name"] for tool in json.loads(result.stdout)["result"]["tools"]] == ["get_resources"]


def test_estimate_normalization_reuses_existing_source_and_replay():
    request, arguments = _submission()
    candidate, files, snapshot = validate_experiment_submission(request,
        {**arguments, "resource_estimate": _ESTIMATE})
    assert files and candidate.owner_role == request.role
    expected = {**_ESTIMATE, "measurement_attempt_ids": []}
    assert snapshot["arguments"]["resource_estimate"] == expected
    assert list(snapshot["arguments"]["resource_estimate"]["workload"]) == ["batch", "precision"]
    assert validate_submission_snapshot(request, snapshot).output_digest == snapshot["output_digest"]
    explicit = validate_experiment_submission(request,
        {**arguments, "resource_estimate": expected})[2]
    assert explicit == snapshot
    assert "resource_estimate" not in validate_experiment_submission(request, arguments)[2]["arguments"]


@pytest.mark.parametrize("estimate", (None, {}, {**_ESTIMATE, "peak_memory_mib": True},
    {**_ESTIMATE, "workload": {"batch": 8}}, {**_ESTIMATE, "device_uuids": ["GPU-not-authority"]},
    {**_ESTIMATE, "measurement_attempt_ids": "attempt"}))
def test_resource_estimate_is_exact_and_typed(estimate):
    request, arguments = _submission()
    with pytest.raises(AgentACIError, match="resource_estimate"):
        validate_experiment_submission(request, {**arguments, "resource_estimate": estimate})


def test_estimate_schema_matches_admitted_shape_and_crosses_the_stdlib_socket(tmp_path):
    submit = next(item for item in scoped_tools("execution") if item["name"] == "submit_experiment")
    schema = submit["inputSchema"]["properties"]["resource_estimate"]
    assert set(schema["properties"]) == {"workload", "peak_memory_mib", "basis", "measurement_attempt_ids"}
    assert set(schema["required"]) == {"workload", "peak_memory_mib", "basis"}
    assert schema["additionalProperties"] is False
    calls = []
    server = AgentToolServer(tmp_path / "submit.sock", "execution-attempt",
        lambda attempt, tool, arguments: calls.append(arguments) or {"accepted": True})
    try:
        arguments = {"candidate": {}, "resource_estimate": _ESTIMATE}
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert _finish(server, pool.submit(call_tool, str(server.socket_path), "submit_experiment", arguments)) == {"accepted": True}
        assert calls == [arguments]
    finally:
        server.close()


def test_retained_submission_rejects_resource_estimate():
    request, arguments = _submission(17)
    with pytest.raises(AgentACIError, match="unknown.*resource_estimate|resource_estimate.*ACI18"):
        validate_experiment_submission(request, {**arguments, "resource_estimate": _ESTIMATE})


def test_current_aci_node_projection_is_not_silently_empty():
    request = replace(default_request(AgentCommand.EXPERIMENT_EXECUTION), schema_version=18)
    views = derive_research_node_views((request.task_context.key,), (), (), agent_aci_version=18)
    assert len(views) == 1


def test_seed_has_native_resource_skill_and_update_cannot_remove_it(tmp_path):
    store = ExpertPluginStore(tmp_path / "store")
    binding = store.ensure("run", "alpha", "Scientific resource learning")
    view = store.read("run", "alpha", commit=binding.commit, attempt_id="read", max_bytes=_LIMIT)
    try:
        assert (view.root / _RESOURCE_SKILL).is_file()
        update = store.prepare_update("run", "alpha", commit=binding.commit, attempt_id="update")
        (update.root / _RESOURCE_SKILL).unlink()
        with pytest.raises(ValueError, match="resource-planning"):
            store.finish_update(update, max_bytes=_LIMIT)
        assert store.ensure("run", "alpha", "ignored") == binding
    finally:
        view.close()


def test_retained_four_entrypoint_plugin_can_update_and_opt_in_without_forced_migration(tmp_path):
    seed = tmp_path / "retained-seed"
    shutil.copytree(SEED_ROOT, seed)
    if (seed / _RESOURCE_SKILL).exists():
        (seed / _RESOURCE_SKILL).unlink()
    store = ExpertPluginStore(tmp_path / "store", seed_root=seed)
    old = store.ensure("run", "alpha", "Retained plugin")
    update = store.prepare_update("run", "alpha", commit=old.commit, attempt_id="old-update")
    report = update.root / "skills/experiment-report/SKILL.md"
    report.write_text(report.read_text() + "\nOne retained lesson.\n")
    accepted = store.finish_update(update, max_bytes=_LIMIT)
    assert accepted.commit != old.commit
    resource = update.root / _RESOURCE_SKILL
    assert not resource.exists()
    opt_in = store.prepare_update("run", "alpha", commit=accepted.commit, attempt_id="opt-in")
    resource = opt_in.root / _RESOURCE_SKILL
    resource.parent.mkdir(parents=True, exist_ok=True)
    resource.write_text("---\nname: resource-planning\ndescription: Scope resource estimates to measured workloads.\n---\nUse measured history.\n")
    upgraded = store.finish_update(opt_in, max_bytes=_LIMIT)
    removal = store.prepare_update("run", "alpha", commit=upgraded.commit, attempt_id="remove")
    (removal.root / _RESOURCE_SKILL).unlink()
    with pytest.raises(ValueError, match="resource-planning"):
        store.finish_update(removal, max_bytes=_LIMIT)
