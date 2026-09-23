"""Observer Stop and postflight share semantics without changing cited output."""

import json
import time

import pytest

from hypoloom.agent_aci import validate_postflight
from hypoloom.claude_agent_host import _observer_output_check, _output_schema
from hypoloom.native_output import NativeOutputValidator, call_validator, decode_output
from hypoloom.reference_aliases import reference_aliases
from test_agent_aci14 import _response
from test_expert_interaction import _observer
from test_observer_aci14 import observer_request


def observer_candidate(*, valid=True):
    return {**_observer(), "supporting_refs": ["fact-hyp-2", "fact-outcome-2" if valid else "fact-outcome"]}


@pytest.mark.parametrize("version", [14, 22])
@pytest.mark.parametrize("corrected", [False, True])
def test_semantic_correction_uses_native_limit_and_preserves_postflight(version, corrected, tmp_path):
    request = observer_request(version=version)
    frozen = request.to_dict()
    schema = _output_schema(request, tool_workspace=True)
    aliases = reference_aliases(request, _output_schema(request, tool_workspace=True, reference_aliases=False))
    inverse = {ref: alias for alias, ref in aliases.items()}
    server = NativeOutputValidator(tmp_path / "output.sock", output_schema=schema,
        session_id="observer-session", max_failed_validations=2, max_request_bytes=65536,
        deadline_at=time.monotonic() + 10, candidate_check=_observer_output_check(request))
    try:
        for ordinal, valid in enumerate((False, corrected), 1):
            candidate = observer_candidate(valid=valid)
            output = {"typed_candidate": {**candidate, "supporting_refs":
                [inverse[ref] for ref in candidate["supporting_refs"]] if version >= 19 else candidate["supporting_refs"]},
                "declared_effects": []}
            text = json.dumps(output)
            assert decode_output(text, schema) == output  # This error is semantic.
            result = call_validator(str(server.socket_path), session_id="observer-session",
                last_assistant_message=text, max_request_bytes=65536, timeout_seconds=2)
            if ordinal == 1:
                assert result["decision"] == "block" and "own verified contribution" in result["reason"]
            else:
                assert result == {}
            if valid:
                assert validate_postflight(request, _response(request, candidate)).candidate.to_dict() == candidate
            else:
                with pytest.raises(ValueError, match="own verified contribution"):
                    validate_postflight(request, _response(request, candidate))
        trace = server.snapshot()
        assert trace["accepted"] is corrected
        assert trace["call_count"] == 2 and trace["rejected_count"] == (1 if corrected else 2)
        assert trace["status"] == ("recovered" if corrected else "unresolved")
        assert request.to_dict() == frozen
    finally:
        server.close()
    assert not server.socket_path.exists()
