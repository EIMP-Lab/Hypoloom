"""Scripted final-output sentinels follow the actual advertised native surface."""

import json
import threading
import urllib.request
from http.server import HTTPServer

import pytest

from test_claude_file_tool_cli import _handler_for, native_output_schema


@pytest.mark.parametrize("advertised,tools,expected", [
    (True, ("StructuredOutput",), "tool_use"),
    (False, ("StructuredOutput",), "text"),
    (False, ("Bash",), "tool_use"),
    (False, ("Bash", "StructuredOutput"), "tool_use"),
])
def test_local_provider_preserves_tools_and_uses_final_text_only_when_unadvertised(advertised, tools, expected):
    arguments = {"typed_candidate": {"value": 3}, "declared_effects": []}

    class Scenario:
        def __init__(self):
            self.errors, self.requests = [], []

        def record(self, document):
            self.requests.append(document)
            return len(self.requests)

        def tool_calls(self, number):
            return tuple((f"tool-{index}", tool, arguments) for index, tool in enumerate(tools))

    scenario = Scenario()
    server = HTTPServer(("127.0.0.1", 0), _handler_for(scenario))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    document = {"model": "local-model", "stream": True, "messages": [],
                "tools": [{"name": "StructuredOutput", "input_schema": {"type": "object"}}] if advertised else []}
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/v1/messages",
            data=json.dumps(document).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            raw = response.read().decode()
        events = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: ")]
        starts = [item["content_block"] for item in events if item["type"] == "content_block_start"]
        deltas = [item["delta"] for item in events if item["type"] == "content_block_delta"]
        assert [item["type"] for item in starts] == [expected] * len(tools)
        if expected == "text":
            assert json.loads(deltas[0]["text"]) == arguments
            assert deltas[0]["type"] == "text_delta"
        else:
            assert [item["name"] for item in starts] == list(tools)
            assert all(json.loads(item["partial_json"]) == arguments for item in deltas)
        final = next(item for item in events if item["type"] == "message_delta")
        assert final["delta"]["stop_reason"] == ("end_turn" if expected == "text" else "tool_use")
        assert final["usage"] == {"output_tokens": len(tools)}
        assert events[0]["message"]["usage"] == {"input_tokens": 1, "output_tokens": 0}
        assert events[-1]["type"] == "message_stop"
        assert scenario.requests == [document] and not scenario.errors
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("advertised,string_content", [(True, False), (False, False), (False, True)])
def test_native_output_schema_reads_real_tool_or_exact_prompt_section(advertised, string_content):
    schema = {"type": "object", "properties": {"typed_candidate": {"type": "object"}},
              "required": ["typed_candidate"], "additionalProperties": False}
    prompt = {"agent_request_metadata": {}, "instruction": "Complete the node.\nHYPOLOOM_OUTPUT_SCHEMA_JSON\n"
              + json.dumps(schema) + "\nEND_HYPOLOOM_OUTPUT_SCHEMA_JSON\n"}
    text = json.dumps(prompt)
    document = {"tools": [{"name": "StructuredOutput", "input_schema": schema}] if advertised else [],
                "messages": [{"role": "user", "content": text if string_content else [{"type": "text", "text": text}]}]}
    assert native_output_schema(document) == schema


@pytest.mark.parametrize("instruction", [
    "No schema supplied.",
    "\nHYPOLOOM_OUTPUT_SCHEMA_JSON\n{}",
    "\nHYPOLOOM_OUTPUT_SCHEMA_JSON\n{} {}\nEND_HYPOLOOM_OUTPUT_SCHEMA_JSON\n",
    "\nHYPOLOOM_OUTPUT_SCHEMA_JSON\n[]\nEND_HYPOLOOM_OUTPUT_SCHEMA_JSON\n",
])
def test_native_output_schema_does_not_invent_missing_or_malformed_schema(instruction):
    document = {"tools": [], "messages": [{"role": "user", "content": json.dumps({
        "agent_request_metadata": {}, "instruction": instruction})}]}
    with pytest.raises((AssertionError, ValueError)):
        native_output_schema(document)
