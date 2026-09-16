import json
from pathlib import Path

import pytest

from hypoloom.execution_workspace import WorkspaceIntegrityError
from hypoloom.runner import LocalRunner
from tests.runtime.test_execution_workspace import candidate_pair, make_workspace


def test_non_python_requires_explicit_generic_mode_and_real_bwrap(tmp_path: Path):
    workspace = make_workspace(tmp_path)
    request, response = candidate_pair()
    arguments = dict(agent_request=request, agent_response=response,
                     attempt_id="generic-1", attempt_ordinal=1, execution_id="execution-1",
                     target_env={}, timeout_seconds=5,
                     execution_argv=("/bin/sh", "-c", "printf '{\"raw\":1}' > result.json"),
                     read_only_bindings=(("/usr/bin", "/bin"),))
    with pytest.raises(ValueError, match="Python"):
        workspace.plan(**arguments)
    prepared = workspace.plan(**arguments, allow_generic_argv=True)
    workspace.materialize(prepared, agent_request=request, agent_response=response)
    observed = LocalRunner().run(prepared.process_request)
    assert observed.returncode == 0
    assert json.loads((prepared.output_dir / "result.json").read_text()) == {"raw": 1}
    recovery = {key: value for key, value in arguments.items()
                if key not in {"attempt_id", "attempt_ordinal", "execution_id"}}
    assert workspace.recover(prepared.process_request, **recovery, allow_generic_argv=True) == prepared
    with pytest.raises(WorkspaceIntegrityError):
        workspace.recover(prepared.process_request, **{**recovery, "execution_argv": ("/bin/false",)},
                          allow_generic_argv=True)
