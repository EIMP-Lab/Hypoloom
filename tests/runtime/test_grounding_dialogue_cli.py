import json

import pytest

from hypoloom import cli
from hypoloom.control_protocol import ControlResponse


def arguments(tmp_path):
    return ["answer", "--state-root", str(tmp_path / "no-database"), "--run-id", "run",
        "--decision-id", "exact-decision", "--answer", "  Keep both.\nFive seeds.  ",
        "--expected-revision", "7", "--request-id", "same-answer"]


def test_answer_cli_uses_socket_and_preserves_free_text(tmp_path, monkeypatch, capsys):
    calls = []
    def send(path, request, *, timeout):
        calls.append((path, request, timeout))
        return ControlResponse.accepted(request.request_id, 8, {"run_state": "ACTIVE"})
    monkeypatch.setattr(cli, "request_over_socket", send)
    assert cli.main(arguments(tmp_path)) == 0
    path, request, timeout = calls[0]
    assert path == tmp_path / "no-database" / "control.sock"
    assert request.command == "ANSWER" and request.expected_revision == 7
    assert request.payload == {"run_id": "run", "decision_id": "exact-decision", "answer": "  Keep both.\nFive seeds.  "}
    assert request.request_id == "same-answer"
    assert json.loads(capsys.readouterr().out)["revision"] == 8
    assert not (tmp_path / "no-database").exists()


@pytest.mark.parametrize("fault", ["missing-revision", "empty-answer"])
def test_answer_cli_rejects_missing_revision_or_empty_answer_before_socket(tmp_path, monkeypatch, fault):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid answer reached control socket")
    monkeypatch.setattr(cli, "request_over_socket", forbidden)
    args = arguments(tmp_path)
    if fault == "missing-revision":
        index = args.index("--expected-revision")
        del args[index:index + 2]
    else:
        args[args.index("--answer") + 1] = "  "
    with pytest.raises(SystemExit) as stopped:
        cli.main(args)
    assert stopped.value.code == 2
