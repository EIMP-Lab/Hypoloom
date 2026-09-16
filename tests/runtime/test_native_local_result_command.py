"""Native rehearsal local checks exercise the actual relative-path writer."""

import json
import subprocess

import pytest

from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import FakeAgent, default_request
from tests.runtime.test_full_cc_host_g3 import _HostScenario


def _local_command(tmp_path, command, step):
    scenario = _HostScenario(tmp_path)
    request = default_request(command)
    scenario.current = request
    scenario.steps[request.attempt_id] = step
    scenario.responses[request.attempt_id] = FakeAgent().invoke(request)
    calls = scenario.tool_calls(1)
    assert len(calls) == 1 and calls[0][1] == "Bash"
    return calls[0][2]["command"]


@pytest.mark.parametrize("command,step", (
    (AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, 2),
    (AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, 4),
    (AgentCommand.EXPERIMENT_EXECUTION, 6),
))
@pytest.mark.parametrize("broken", (False, True))
def test_native_local_command_uses_fresh_relative_output_and_preserves_failures(
    tmp_path, command, step, broken,
):
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "experiment.py"
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    source.write_text(
        "import json, os\nfrom pathlib import Path\n"
        "assert os.environ['HYPOLOOM_RESULT_PATH'] == 'result.json'\n"
        "assert Path.cwd() != Path(__file__).parent\n"
        "assert not Path('result.json').exists()\n"
        "result_path = os.environ['HYPOLOOM_RESULT_PATH']\n"
        "print('COMPUTATION_FINISHED', flush=True)\n"
        + ("os.makedirs(os.path.dirname(result_path), exist_ok=True)\n" if broken else
           "Path(result_path).parent.mkdir(parents=True, exist_ok=True)\n")
        + "Path(result_path).write_text(json.dumps({'computed': sum([1, 2, 3])}))\n"
    )
    native_command = _local_command(tmp_path, command, step)
    native_command = native_command.replace("/workspace/experiment.py", str(source))
    native_command = native_command.replace("dir='/tmp'", f"dir={str(outputs)!r}")
    result = subprocess.run(("bash", "-c", native_command), cwd=source_root,
        capture_output=True, text=True, timeout=5)
    assert "COMPUTATION_FINISHED" in result.stdout, result.stderr
    if broken:
        assert result.returncode != 0
        assert "FileNotFoundError" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout.splitlines()[-1]) == {"computed": 6}
    assert not (source_root / "result.json").exists()
    assert list(outputs.iterdir()) == []
