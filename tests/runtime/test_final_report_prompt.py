"""Report instructions direct the full-capability Agent to actual bound sources."""

import json
from dataclasses import replace

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.claude_agent_host import _prompt
from test_final_report import report_request


def test_final_report_names_native_skill_and_exact_input_files_without_losing_sources():
    request = report_request()
    background = EmbeddedInput.from_bytes("report--background", "text/plain", b"Actual background")
    request = replace(request, input_artifacts=(background, *request.input_artifacts))
    before = request.to_dict()
    prompt = json.loads(_prompt(request, tool_workspace=True))
    assert prompt["instruction"].startswith("Invoke Skill hypoloom-grounding:final-report")
    assert "Inspect the environment and run local compilation/tests" not in prompt["instruction"]
    assert "/report/FINAL_REPORT.md" in prompt["instruction"]
    assert "Read/Write/Edit/Glob/Grep/Bash/Skill" in prompt["instruction"]
    assert "NO_ACCEPTED_VERDICT" in prompt["instruction"]
    for index, (source, rendered) in enumerate(zip(request.input_artifacts, prompt["rendered_inputs"], strict=True)):
        assert rendered["path"] == f"/inputs/{index:04d}.txt"
        assert rendered["ref"] == source.ref
        assert rendered["content_utf8"].encode() == source.content_bytes()
    assert request.to_dict() == before


def test_report_without_a_mounted_workspace_does_not_advertise_input_files():
    prompt = json.loads(_prompt(report_request(), tool_workspace=False))
    assert all("path" not in item for item in prompt["rendered_inputs"])
