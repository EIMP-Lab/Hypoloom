"""Rejected Grounding keeps its captured evaluator as evidence, not acceptance."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from hypoloom.agent_aci import request_digest, with_output_digest
from hypoloom.claude_agent_host import CLAUDE_AGENT_HOST_VERSION
from hypoloom.oracle import _raw_artifact_digest
from hypoloom.session_oracle import audit_sessions
from test_agent_sessions import _manager, _transcript
from test_grounding_acceptance_aci import SOURCE, acceptance_exchange


def _archive(manager, request, response, *, rejected=False):
    workspace = manager.prepare(request, None)
    if response.typed_candidate["acceptance"] is not None:
        (workspace.acceptance_root / "evaluate.py").write_bytes(SOURCE)
    workspace.capture_workspace(response.typed_candidate)
    _transcript(workspace, "Grounding response retained", append=bool(
        (workspace.session_root / f"{workspace.binding['session_id']}.jsonl").exists()))
    row = {"attempt_id": request.attempt_id, "kind": "AGENT", "state": "CLOSED",
           "terminal_reason": "FAILED" if rejected else "SUCCEEDED",
           "started_at": 1, "ended_at": 2, "process_group_id": 12345}
    observation = {"request": request.to_dict(), "session_binding": workspace.binding,
                   "session": workspace.finish_node(),
                   "rejected_response" if rejected else "response": response.to_dict()}
    if rejected:
        observation.update(failure_code="invalid_candidate", failure_stage="host_candidate_validation")
    return row, observation


def _exchange(*, question=False):
    request, response = acceptance_exchange(question=question)
    request = replace(request, required_adapter_version=CLAUDE_AGENT_HOST_VERSION)
    return request, with_output_digest(replace(response, adapter_version=CLAUDE_AGENT_HOST_VERSION,
                                               input_digest=request_digest(request)))


@pytest.mark.parametrize("rejected", [False, True])
@pytest.mark.parametrize("question", [False, True])
def test_oracle_counts_captured_grounding_files_for_success_or_host_rejection(tmp_path, rejected, question):
    manager = _manager(tmp_path)
    request, response = _exchange(question=question)
    row, observation = _archive(manager, request, response, rejected=rejected)
    issues, count = audit_sessions(manager.publisher.root, [row], {request.attempt_id: observation},
                                   _raw_artifact_digest)
    assert issues == []
    assert count == 1
    assert ("response" in observation) is not rejected
    assert (row["terminal_reason"] == "SUCCEEDED") is not rejected


def test_failed_grounding_archive_stays_valid_after_same_session_retry(tmp_path):
    manager = _manager(tmp_path)
    request, response = _exchange()
    first_row, first = _archive(manager, request, response, rejected=True)
    first_path = manager.publisher.root / first["session"]["transcript"]["path"]
    before = {path.relative_to(first_path).as_posix(): path.read_bytes()
              for path in first_path.rglob("*") if path.is_file()}
    retry = replace(request, attempt_id="grounding-retry")
    retry_response = with_output_digest(replace(response, attempt_id=retry.attempt_id,
                                                 input_digest=request_digest(retry)))
    second_row, second = _archive(manager, retry, retry_response)
    second_row.update(started_at=3, ended_at=4)
    assert first["session_binding"] == second["session_binding"]
    assert second["session"]["launch_arguments"] == ["--resume", first["session_binding"]["session_id"]]
    assert before == {path.relative_to(first_path).as_posix(): path.read_bytes()
                      for path in first_path.rglob("*") if path.is_file()}
    issues, count = audit_sessions(manager.publisher.root, [first_row, second_row],
        {request.attempt_id: first, retry.attempt_id: second}, _raw_artifact_digest)
    assert issues == []
    assert count == 2


def test_captured_evaluator_survives_invalid_provider_envelope_without_response(tmp_path):
    manager = _manager(tmp_path)
    request, response = _exchange()
    row, observation = _archive(manager, request, response, rejected=True)
    observation.pop("rejected_response")
    observation.update(failure_code="invalid_provider_envelope", failure_stage="provider_process")
    issues, count = audit_sessions(manager.publisher.root, [row], {request.attempt_id: observation},
                                   _raw_artifact_digest)
    assert issues == []
    assert count == 1


@pytest.mark.parametrize("damage", ["bytes", "outside-evaluator", "oversize", "success"])
def test_invalid_envelope_capture_keeps_publication_and_failure_boundaries(tmp_path, damage):
    manager = _manager(tmp_path)
    request, response = _exchange()
    row, observation = _archive(manager, request, response, rejected=True)
    observation.pop("rejected_response")
    observation.update(failure_code="invalid_provider_envelope", failure_stage="provider_process")
    publication = observation["session"]["transcript"]
    directory = manager.publisher.root / publication["path"]
    if damage == "bytes":
        (directory / "evaluator/evaluate.py").write_bytes(b"changed")
    elif damage == "outside-evaluator":
        (directory / "unexpected.py").write_bytes(b"unexpected")
        publication["relative_paths"].append("unexpected.py")
        publication["digest"] = _raw_artifact_digest(directory)
    elif damage == "oversize":
        observation["request"]["budget"]["max_output_bytes"] = 1
    else:
        row["terminal_reason"] = "SUCCEEDED"
    issues, count = audit_sessions(manager.publisher.root, [row], {request.attempt_id: observation},
                                   _raw_artifact_digest)
    assert {item[0] for item in issues} == {"session_archive_invalid"}
    assert count == 0


@pytest.mark.parametrize("damage", ["missing-rejected", "undeclared-file", "different-parser",
                                   "changed-bytes", "success-with-only-rejected"])
def test_rejected_grounding_does_not_relax_archive_file_or_success_boundaries(tmp_path, damage):
    manager = _manager(tmp_path)
    request, response = _exchange()
    row, observation = _archive(manager, request, response, rejected=True)
    observation = copy.deepcopy(observation)
    publication = observation["session"]["transcript"]
    directory = manager.publisher.root / publication["path"]
    if damage == "missing-rejected":
        observation.pop("rejected_response")
    elif damage == "undeclared-file":
        (directory / "evaluator" / "extra.py").write_bytes(b"undeclared\n")
        publication["relative_paths"].append("evaluator/extra.py")
        publication["digest"] = _raw_artifact_digest(directory)
    elif damage == "different-parser":
        observation["rejected_response"]["typed_candidate"]["acceptance"]["parser_files"] = ["other.py"]
    elif damage == "changed-bytes":
        (directory / "evaluator" / "evaluate.py").write_bytes(b"changed\n")
    else:
        row["terminal_reason"] = "SUCCEEDED"
    issues, count = audit_sessions(manager.publisher.root, [row], {request.attempt_id: observation},
                                   _raw_artifact_digest)
    assert {item[0] for item in issues} == {"session_archive_invalid"}
    assert count == 0
