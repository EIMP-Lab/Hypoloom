"""Execute generated CUDA-fixture local commands without granting CUDA access."""

from dataclasses import replace
import json
import subprocess

import pytest

from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import default_request
from test_full_cc_host_g3 import _LOCAL_FAILURE, _LOCAL_SUCCESS
from test_native_gpu_g4 import _GpuScenario
from test_shared_gpu_native_g4 import _SharedGpuScenario


_CPU_BOUNDARY = """
import builtins
original_import = builtins.__import__
def forbid_torch(name, *args, **kwargs):
    if name == 'torch' or name.startswith('torch.'):
        raise AssertionError('Local fixture attempted forbidden torch import')
    return original_import(name, *args, **kwargs)
builtins.__import__ = forbid_torch
"""


@pytest.mark.parametrize("shared", (False, True))
@pytest.mark.parametrize("gpu_count", (1, 2))
def test_generated_gpu_local_command_keeps_cpu_boundary_and_actual_writer(
    tmp_path, shared, gpu_count,
):
    uuids = tuple(f"GPU-not-granted-{number}" for number in range(gpu_count))
    scenario = (_SharedGpuScenario(tmp_path, uuids) if shared else
        _GpuScenario(tmp_path, ("chain-a",), uuids))
    request = default_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT)
    task = replace(request.task_context, key=replace(request.task_context.key, chain="chain-a"))
    request = replace(request, schema_version=scenario.reviser.aci_version,
                      task_context=task, command_id=task.command_id)
    scenario.current = request
    scenario.responses[request.attempt_id] = scenario.reviser.invoke(request)
    source_root = tmp_path / "source"
    source_root.mkdir()
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    source = source_root / "experiment.py"

    def call(step, expected_tool):
        scenario.steps[request.attempt_id] = step
        calls = scenario._unscoped_tool_calls(step)
        assert len(calls) == 1 and calls[0][1] == expected_tool
        return calls[0][2]

    # These are the source, seeded failure, repair, and command emitted by the
    # real shared/ordinary fixture, rather than a separately assembled probe.
    source.write_text(_CPU_BOUNDARY + call(1, "Write")["content"] + "\n"
        "assert sys.argv[1:] == ['--local-check']\n"
        "assert os.environ['HYPOLOOM_RESULT_PATH'] == 'result.json'\n"
        "assert Path.cwd() != Path(__file__).parent\n"
        "assert Path('result.json').is_file()\n"
        "assert 'torch' not in sys.modules\n")

    def run(step):
        command = call(step, "Bash")["command"]
        command = command.replace("/workspace/experiment.py", str(source))
        command = command.replace("dir='/tmp'", f"dir={str(outputs)!r}")
        return subprocess.run(("bash", "-c", command), cwd=source_root,
            text=True, capture_output=True, timeout=5)

    failed = run(2)
    assert failed.returncode != 0
    assert _LOCAL_FAILURE in failed.stderr
    assert "forbidden torch import" not in failed.stderr
    assert list(outputs.iterdir()) == []
    edit = call(3, "Edit")
    source.write_text(source.read_text().replace(edit["old_string"], edit["new_string"]))
    succeeded = run(4)
    assert succeeded.returncode == 0, succeeded.stderr
    assert _LOCAL_SUCCESS in succeeded.stdout
    result = json.loads(succeeded.stdout.splitlines()[-1])
    assert result["trials"][0]["metrics"] == {"score": 1}
    assert result["artifacts"] == {}
    assert not (source_root / "result.json").exists()
    assert list(outputs.iterdir()) == []
