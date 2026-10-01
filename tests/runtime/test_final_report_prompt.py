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
    assert "raw_diagnostic_only" in prompt["instruction"]
    assert "gpu_device_monitored" in prompt["instruction"]
    assert "owned_memory" in prompt["instruction"]
    assert "do not prove GPU computation" in prompt["instruction"]
    assert "reported_tokens" in prompt["instruction"]
    assert "reserved_tokens" in prompt["instruction"]
    assert "Reservations are not measured usage" in prompt["instruction"]
    assert "Read/Write/Edit/Glob/Grep/Bash/Skill" in prompt["instruction"]
    assert "NO_ACCEPTED_VERDICT" in prompt["instruction"]
    assert "advertise their exact read-only /inputs paths" in prompt["instruction"]
    assert "and complete content" not in prompt["instruction"]
    background_rendered, context_rendered = prompt["rendered_inputs"]
    assert background_rendered == {
        "ref": background.ref,
        "media_type": background.media_type,
        "content_utf8": "Actual background",
    }
    context = request.input_artifacts[1]
    assert context_rendered == {
        "ref": context.ref,
        "media_type": context.media_type,
        "path": "/inputs/0001.txt",
        "size_bytes": len(context.content_bytes()),
    }
    assert request.to_dict() == before


def test_report_without_a_mounted_workspace_keeps_every_input_inline():
    prompt = json.loads(_prompt(report_request(), tool_workspace=False))
    assert all("path" not in item for item in prompt["rendered_inputs"])
    assert all("content_utf8" in item for item in prompt["rendered_inputs"])
