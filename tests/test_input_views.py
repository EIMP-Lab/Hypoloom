from dataclasses import replace
import json

import pytest

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.claude_agent_host import _prompt
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import default_request
from hypoloom.input_views import PAGE_BYTES, input_views


def request_for(suffix, value, version=38):
    item = EmbeddedInput.from_bytes("job" + suffix, "application/json",
        json.dumps(value, ensure_ascii=False).encode())
    base = default_request(AgentCommand.VERIFY_IMPLEMENTATION)
    inputs = (item,) + tuple(i for i in base.input_artifacts if not i.ref.endswith(suffix))
    return replace(base, schema_version=version, max_input_bytes=1024 * 1024,
        input_artifacts=inputs, immutable_context_refs=tuple(i.ref for i in inputs))


def read_pages(files, descriptor):
    return b"".join(files[path.removeprefix("/inputs/")] for path in descriptor["reading_pages"])


def overview(files, ad):
    index = json.loads(files[ad["index_path"].removeprefix("/inputs/")])
    return json.loads(read_pages(files, index["overview"])), index


@pytest.mark.parametrize("source", ["", "def f():\n    return 42\n" * 6000,
    "一😀行" * 19000, "a\r\nb\n" * 15000])
def test_source_pages_reconstruct_exact_text_with_safe_names(source):
    request = request_for("--implementation-source", {"../../outside.py": source})
    before = request.to_dict()
    files, ads = input_views(request)
    view, index = overview(files, ads[0])
    assert read_pages(files, view["../../outside.py"]) == source.encode()
    assert all("/" not in path and ".." not in path for path in files)
    assert all(len(data) <= PAGE_BYTES for data in files.values())
    assert all(data.decode() is not None for data in files.values())
    assert index["original_input"] == "/inputs/0000.txt"
    assert index["evidence_ref"] == request.input_artifacts[0].ref
    assert request.to_dict() == before and input_views(request) == (files, ads)


def test_nested_probe_logs_and_plan_code_are_readable_without_escaped_json():
    source = "assert 2 + 2 == 4\n" * 4000
    logs = "PASS step\n" * 8000
    request = request_for("--verification-context", {"phase": "review",
        "probe_plan": {"files": {"probe.py": source}},
        "probe_evidence": {"observation": {"stdout": logs, "returncode": 0}},
        "prior_reports": [{"report_ref": "old", "report": {"verdict": "probe_required", "summary": source}}]})
    files, ads = input_views(request)
    view, _ = overview(files, ads[0])
    plan = json.loads(read_pages(files, view["probe_plan"]))
    evidence = json.loads(read_pages(files, view["probe_evidence"]))
    assert read_pages(files, plan["files"]["probe.py"]) == source.encode()
    assert read_pages(files, evidence["observation"]["stdout"]) == logs.encode()
    assert evidence["observation"]["returncode"] == 0
    assert all(len(data) <= PAGE_BYTES for data in files.values())


def test_large_input_prompt_advertises_views_without_embedding_original():
    request = request_for("--implementation-source", {"candidate.py": "x = 1\n" * 30000})
    before = request.to_dict()
    prompt = json.loads(_prompt(request, tool_workspace=True))
    rendered = prompt["rendered_inputs"][0]
    assert rendered["path"] == "/inputs/0000.txt"
    assert rendered["reading_view"]["index_path"] == "/inputs/0000.view.txt"
    assert "content_utf8" not in rendered and len(json.dumps(rendered)) < 1000
    assert rendered["ref"] == request.input_artifacts[0].ref
    assert request.to_dict() == before


def test_large_memory_is_offloaded_but_reconstructs_all_evidence():
    entries = [{"covered": ["measured condition " * 500], "checks": [{"status": "MEASURED"}]}] * 4
    request = request_for("--verified-engineering-memory", {"entries": entries, "omitted_entries": 0})
    prompt = json.loads(_prompt(request, tool_workspace=True))
    assert prompt["rendered_inputs"][0]["path"] == "/inputs/0000.txt"
    files, ads = input_views(request)
    view, _ = overview(files, ads[0])
    for descriptor, original in zip(view["entries"], entries):
        entry = json.loads(read_pages(files, descriptor))
        assert read_pages(files, entry["covered"][0]).decode() == original["covered"][0]
        assert entry["checks"] == original["checks"]


def test_retained_aci_has_no_new_files_or_prompt_paths():
    request = request_for("--implementation-source", {"candidate.py": "x=1\n"}, version=37)
    assert input_views(request) == ({}, {})
    assert "reading_view" not in json.loads(_prompt(request, tool_workspace=True))["rendered_inputs"][0]
    unrelated = request_for("--unrelated", {"value": "x" * 100000})
    assert 0 not in input_views(unrelated)[1]
