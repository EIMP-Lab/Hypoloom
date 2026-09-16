"""Grounding's public schema must expose the existing capture-path contract."""

from copy import deepcopy
import json
import re

import pytest

from hypoloom.claude_agent_host import _output_schema, _prompt
from hypoloom.full_cc_runtime import full_cc_instruction
from hypoloom.shared_acceptance import SharedAcceptance, SharedAcceptanceError
from tests.runtime.test_grounding_acceptance_aci import ACCEPTANCE, acceptance_exchange


def _schema():
    request, _ = acceptance_exchange()
    outer = _output_schema(request, tool_workspace=True)
    acceptance = outer["properties"]["typed_candidate"]["properties"]["acceptance"]["anyOf"][1]
    return request, outer, acceptance


@pytest.mark.parametrize("field", ["parser_files", "raw_artifacts"])
@pytest.mark.parametrize("path", [
    "/acceptance/evaluate.py", "/raw/predictions.json", "../evaluate.py", "./evaluate.py",
    "nested/../evaluate.py", "nested/./evaluate.py", "nested//evaluate.py", "nested/evaluate.py/",
    "nested\\evaluate.py", "evaluate\x00.py", "", " ", "\n", "evaluate.py", "nested/evaluate.py",
    ".evaluate.py", "...", "with space.py", "模型/evaluate.py", "nested/\n", "nested/.\n",
])
def test_public_path_schema_matches_existing_single_path_validation(field, path):
    # Real G5 submitted /acceptance/evaluate.py twice: StructuredOutput accepted
    # it, but existing SharedAcceptance validation rejected it during capture.
    _, _, schema = _schema()
    declaration = deepcopy(ACCEPTANCE)
    declaration[field] = [path]
    try:
        SharedAcceptance.from_dict(declaration)
    except SharedAcceptanceError:
        admitted = False
    else:
        admitted = True
    item = schema["properties"][field]["items"]
    schema_admitted = len(path) >= item.get("minLength", 0) and bool(re.search(item.get("pattern", ".*"), path))
    assert schema_admitted is admitted, (field, repr(path))


def test_grounding_instructions_distinguish_authoring_capture_and_execution_paths():
    request, _, schema = _schema()
    instruction = full_cc_instruction(request)
    assert "parser_files and raw_artifacts are canonical relative paths" in instruction
    assert 'parser_files=["evaluate.py"]' in instruction
    assert 'parser_argv=["python", "/input/evaluate.py"]' in instruction
    assert 'raw_artifacts=["predictions.json"]' in instruction
    assert "/acceptance is not mounted in the parser Process" in instruction
    assert "Do not submit experiments or modify the target checkout" in instruction
    assert "declared_effects is a top-level sibling of typed_candidate" in instruction
    assert instruction in json.loads(_prompt(request, tool_workspace=True))["instruction"]
    for field in ("parser_files", "raw_artifacts"):
        assert "relative" in schema["properties"][field]["description"]
    assert "/input/" in schema["properties"]["parser_argv"]["description"]


def test_grounding_effects_stay_in_existing_top_level_envelope():
    _, outer, _ = _schema()
    candidate = outer["properties"]["typed_candidate"]
    assert "declared_effects" in outer["required"]
    assert "declared_effects" not in candidate["properties"]
    assert candidate["additionalProperties"] is False
    assert "top-level" in outer["properties"]["declared_effects"]["description"]


def test_path_schema_does_not_replace_argv_or_capture_safety():
    _, _, schema = _schema()
    argv = schema["properties"]["parser_argv"]
    assert argv["uniqueItems"] is False
    assert "enum" not in argv["items"] and "const" not in argv["items"]
    for command in (["python", "evaluate.py"], ["python", "/input/evaluate.py"],
                    ["/bin/sh", "/input/evaluate.py"]):
        assert SharedAcceptance.from_dict({**ACCEPTANCE, "parser_argv": command}).parser_argv == tuple(command)
    with pytest.raises(SharedAcceptanceError, match="collision"):
        SharedAcceptance.from_dict({**ACCEPTANCE, "parser_files": ["nested", "nested/evaluate.py"]})


def test_retained_grounding_does_not_acquire_common_path_schema():
    from tests.runtime.test_grounding_dialogue_aci import exchange

    request, _ = exchange(question=False)
    schema = _output_schema(request, tool_workspace=True)
    assert "acceptance" not in schema["properties"]["typed_candidate"]["properties"]
