import copy
import json

import pytest

from hypoloom.grounding_background import (
    GroundingBackground, GroundingBackgroundError, background_json_schema,
    render_background_markdown, validate_web_sources,
    transcript_web_results, retrieved_web_urls,
)


def background():
    return {"project_summary": "Measured training project with an existing evaluation script.",
            "codebase_map": [{"path": "src/evaluate.py", "purpose": "Compute held-out accuracy"}],
            "entrypoints": ["src/evaluate.py"],
            "how_to_run_experiment": "python src/evaluate.py --data /data/heldout.csv",
            "metrics_available": ["accuracy"], "risks": ["Dataset split must remain fixed"],
            "open_questions": ["No robustness experiment has been run yet"]}


def test_background_round_trip_and_markdown_preserve_observed_entrypoint_and_command():
    raw = background()
    parsed = GroundingBackground.from_dict(raw)
    assert parsed.to_dict() == raw
    content = render_background_markdown(parsed, web_sources=("https://example.org/evaluation",))
    for text in (raw["project_summary"], raw["entrypoints"][0], raw["how_to_run_experiment"],
                 raw["metrics_available"][0], raw["risks"][0], raw["open_questions"][0]):
        assert text in content
    assert "https://example.org/evaluation" in content
    schema = background_json_schema()
    assert set(schema["required"]) == set(raw)
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("damage", ["missing", "extra-verdict", "string-list", "empty-summary", "bad-map", "null-byte", "budget"])
def test_background_rejects_incomplete_or_mistyped_structure(damage: str):
    raw = copy.deepcopy(background())
    if damage == "missing":
        raw.pop("entrypoints")
    elif damage == "extra-verdict":
        raw["verdict"] = "VERIFIED"
    elif damage == "string-list":
        raw["metrics_available"] = "accuracy"
    elif damage == "empty-summary":
        raw["project_summary"] = " "
    elif damage == "bad-map":
        raw["codebase_map"][0]["invented_id"] = "new-authority"
    elif damage == "null-byte":
        raw["entrypoints"] = ["bad\x00path"]
    with pytest.raises(GroundingBackgroundError):
        GroundingBackground.from_dict(raw, max_bytes=2 if damage == "budget" else 10000)


def test_unknown_project_details_remain_unknown_without_invented_sources():
    raw = background()
    raw.update(codebase_map=[], entrypoints=[], metrics_available=[], risks=[],
               how_to_run_experiment="Not established: repository lacks run instructions.")
    content = render_background_markdown(GroundingBackground.from_dict(raw))
    assert "Not established" in content
    assert "not proof of absence" in content
    assert "src/evaluate.py" not in content


@pytest.mark.parametrize("sources", [["https://example.org/invented"],
                                     ["https://example.org/observed"] * 2,
                                     ["http://example.org/observed"]])
def test_dynamic_web_citations_require_actual_retrieved_public_urls(sources):
    with pytest.raises(GroundingBackgroundError):
        validate_web_sources(sources, {"https://example.org/observed", "http://example.org/observed"})
    assert validate_web_sources(["https://example.org/observed"], {"https://example.org/observed"}) == (
        "https://example.org/observed",)


def web_transcript(*, tool="web_fetch", result=None, session="session-1"):
    result = result or {"requested_url": "https://example.org/observed", "url": "https://example.org/final", "text": "untrusted body"}
    return [
        {"type": "assistant", "sessionId": session, "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "web-1", "name": "mcp__host-tools__" + tool, "input": {"url": "https://example.org/observed"}}]}},
        {"type": "user", "sessionId": session, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "web-1", "content": [{"type": "text", "text": json.dumps(result)}]}]}}]


def test_native_transcript_restores_tool_results_but_not_partial_tail_or_prose():
    lines = web_transcript()
    content = b"\n".join(json.dumps(item).encode() for item in lines) + b'\n{"type":'
    records = transcript_web_results(content, session_id="session-1")
    assert retrieved_web_urls(records) == {"https://example.org/observed", "https://example.org/final"}
    assert "text" not in records[0]["result"]


def test_native_batch_documents_keep_available_citations_and_reject_unavailable_members():
    first, second, missing = ("https://example.org/" + name for name in ("method-a", "method-b", "missing"))
    lines = web_transcript(result={"documents": [
        {"status": "available", "requested_url": first, "url": first + "/final"},
        {"status": "available", "requested_url": second, "url": second},
        {"status": "unavailable", "url": missing, "error": {"code": "timed_out"}},
    ]})
    lines[0]["message"]["content"][0]["input"] = {"mode": "document", "sources": [{"url": first}, {"url": second}]}
    content = b"\n".join(json.dumps(item).encode() for item in lines)
    records = transcript_web_results(content, session_id="session-1")
    observed = retrieved_web_urls(records)
    assert observed == {first, first + "/final", second}
    assert validate_web_sources([first, second], observed) == (first, second)
    with pytest.raises(GroundingBackgroundError):
        validate_web_sources([missing], observed)
    assert retrieved_web_urls(transcript_web_results(content, session_id="other-session")) == set()


@pytest.mark.parametrize("damage", ["unknown-tool", "unmatched-id", "error", "wrong-session", "prose", "malformed-result"])
def test_transcript_does_not_turn_unobserved_urls_into_tool_results(damage):
    lines = web_transcript()
    use, result = [line["message"]["content"][0] for line in lines]
    if damage == "unknown-tool": use["name"] = "Bash"
    elif damage == "unmatched-id": result["tool_use_id"] = "other"
    elif damage == "error": result["is_error"] = True
    elif damage == "wrong-session": lines[1]["sessionId"] = "other"
    elif damage == "prose": result["type"] = "text"
    else: result["content"][0]["text"] = "not JSON"
    assert not transcript_web_results(b"\n".join(json.dumps(item).encode() for item in lines), session_id="session-1")
