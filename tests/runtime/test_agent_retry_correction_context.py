from __future__ import annotations

import json
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from hypoloom.agent_aci import (
    AgentCommand,
    AgentRequest,
    AgentResponse,
    with_output_digest,
)
from hypoloom.claude_agent_host import _prompt
from hypoloom.control_protocol import ControlRequest
from hypoloom.debug_mode import build_debug_report
from hypoloom.diagnostics import MAX_DIAGNOSTIC_TEXT_CHARS
from hypoloom.domain import AttemptTerminalReason
from hypoloom.expert_loop import (
    AuxiliaryRequestKind,
    AuxiliaryTask,
    ResearchNodeSubkind,
    ResearchNodeTask,
)
from hypoloom.fake_agent import FakeAgent
from hypoloom.literature import (
    LITERATURE_STRATA,
    FixedLiteratureIO,
    LiteratureFetchError,
    LiteratureFailureCode,
    LiteratureQuery,
    ScholarlySourceClass,
    SearchPaper,
)
from hypoloom.profile_admission import PROFILE_V3_FILES, admit_profile
from hypoloom.workflow_host import WorkflowHost


_REPOSITORY = Path(__file__).resolve().parents[2]
_CORRECTION_SUFFIX = "--agent-retry-diagnostic"


class _CorrectionLiteratureFetcher:
    def __init__(self) -> None:
        self.queries: list[LiteratureQuery] = []

    @property
    def configuration(self) -> dict[str, object]:
        return {
            "adapter": "retry-correction-test",
            "host": "none",
            "timeout_seconds": 1.0,
            "max_response_bytes": 4096,
        }

    def preflight(self) -> dict[str, object]:
        return {
            "adapter": "retry-correction-test",
            "network_probe_performed": False,
        }

    def search(self, query: LiteratureQuery) -> tuple[SearchPaper, ...]:
        self.queries.append(query)
        return tuple(
            SearchPaper(
                bibliographic_identity=f"openalex:W{index}",
                title=f"Bounded retry paper {index}",
                authors=("Test Researcher",),
                year=year,
                language="en",
                landing_uri=f"https://catalog.example.org/works/{index}",
                source_uri=f"https://catalog.example.org/records/{index}",
                source_class=ScholarlySourceClass.JOURNAL,
                stratum=stratum,
                pdf_uri=None,
            )
            for index, (stratum, year) in enumerate(
                zip(LITERATURE_STRATA, (2000, 2015, 2026), strict=True),
                start=1,
            )
        )

    def resolve(self, hostname: str) -> tuple[str, ...]:
        assert hostname == "catalog.example.org"
        return ("8.8.8.8",)

    def fetch(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("fixture papers intentionally have no PDF")

    def extract_pdf(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("fixture papers intentionally have no PDF")


def _create_run(
    host: WorkflowHost,
    run_id: str,
    payload: dict[str, object],
) -> None:
    response = host.handle(
        ControlRequest(
            schema_version=1,
            request_id=f"create-{run_id}",
            expected_revision=0,
            command="CREATE_RUN",
            payload=payload,
        )
    )
    assert response.status == "accepted", response


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", *arguments),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


def _expert_payload(tmp_path: Path, run_id: str) -> dict[str, object]:
    target = tmp_path / "target"
    target.mkdir()
    (target / "README.md").write_text("retry context target\n", encoding="utf-8")
    _git(target, "init", "-q")
    _git(target, "add", ".")
    _git(
        target,
        "-c",
        "user.name=Hypoloom Test",
        "-c",
        "user.email=hypoloom@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    data = tmp_path / "data"
    data.mkdir()
    (data / "README.txt").write_text("retry context data\n", encoding="utf-8")

    source = _REPOSITORY / "tests" / "fixtures" / "profiles" / "expert-loop-pilot-v3"
    profile = tmp_path / "profile"
    profile.mkdir()
    for name in PROFILE_V3_FILES:
        text = (source / name).read_text(encoding="utf-8")
        text = text.replace(
            "/REPLACE_ME/expert-loop-pilot-target", str(target.resolve())
        )
        text = text.replace(
            "/REPLACE_ME/expert-loop-pilot-data", str(data.resolve())
        )
        if name == "safety.yaml":
            text = "allow_read_paths: []\nsandbox_os: linux\n"
        (profile / name).write_text(text, encoding="utf-8")

    report = admit_profile(profile, run_id=run_id)
    assert report.admitted, report.to_dict()
    assert report.create_run_payload is not None
    payload = report.create_run_payload
    budget = payload["research_contract"]["budget"]
    budget.update(
        {
            "max_retries": 6,
            "max_infrastructure_retries": 6,
            "max_process_lost_retries": 0,
        }
    )
    return payload


def _is_grounding_query_plan(request: AgentRequest) -> bool:
    task = request.task_context
    return (
        request.command is AgentCommand.LITERATURE_REVIEW
        and isinstance(task, AuxiliaryTask)
        and task.kind is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN
    )


def _is_literature_main(request: AgentRequest) -> bool:
    task = request.task_context
    return (
        request.command is AgentCommand.LITERATURE_REVIEW
        and isinstance(task, ResearchNodeTask)
        and task.key.subkind is ResearchNodeSubkind.MAIN
    )


def _failure_details(kind: str, ordinal: int, secret: str) -> dict[str, Any]:
    diagnostics: dict[str, object] = {
        "failure": f"bounded {kind} diagnostic at attempt {ordinal}",
        "stdout_excerpt": f"Authorization: Bearer {secret}",
        "stderr_excerpt": f"api_key={secret}",
    }
    if kind == "provider_structured_output":
        diagnostics["provider_subtype"] = "error_max_structured_output_retries"
        diagnostics["provider_structured_candidate"] = {
            "typed_candidate": {
                "query": f"rejected provider query at attempt {ordinal}",
            },
        }
        diagnostics["provider_diagnostic"] = (
            f"provider rejected structured output at attempt {ordinal}"
        )
    return {
        "returncode": 23,
        "agent_host_diagnostic": f"bounded {kind} diagnostic at attempt {ordinal}",
        "diagnostics": diagnostics,
    }


def _capture_query_plan_retries(
    host: WorkflowHost,
    monkeypatch: pytest.MonkeyPatch,
    failure_kinds: tuple[str, ...],
    *,
    secret: str,
    oversized_structured_candidate: bool = False,
    credential_structured_candidate: bool = False,
) -> list[AgentRequest]:
    requests: list[AgentRequest] = []
    finish = host._finish_fake_agent

    def finish_with_failures(
        request: AgentRequest,
        raw: AgentResponse | bytes,
    ) -> None:
        if not _is_grounding_query_plan(request):
            finish(request, raw)
            return
        requests.append(request)
        index = len(requests) - 1
        if index >= len(failure_kinds):
            finish(request, raw)
            return
        details = _failure_details(failure_kinds[index], index + 1, secret)
        if oversized_structured_candidate:
            diagnostics = details["diagnostics"]
            assert isinstance(diagnostics, dict)
            diagnostics["provider_structured_candidate"] = {
                "typed_candidate": {"query": "x" * (64 * 1024)},
            }
        if credential_structured_candidate:
            diagnostics = details["diagnostics"]
            assert isinstance(diagnostics, dict)
            diagnostics["provider_structured_candidate"] = {
                "typed_candidate": {
                    "query": "ordinary query",
                    "api_key": secret,
                },
            }
        host._agent_failure(
            request,
            AttemptTerminalReason.FAILED,
            "nonzero_exit",
            retryable=True,
            details=details,
        )

    monkeypatch.setattr(host, "_finish_fake_agent", finish_with_failures)
    for _ in range(40):
        host.tick()
        if len(requests) == len(failure_kinds) + 1:
            return requests
    raise AssertionError(
        "Grounding query-plan retry lineage did not reach its recovery request"
    )


def _correction_inputs(request: AgentRequest):
    return tuple(
        item
        for item in request.input_artifacts
        if item.ref.endswith(_CORRECTION_SUFFIX)
    )


def _task_authority(request: AgentRequest) -> tuple[object, ...]:
    return (
        request.owner_workflow_id,
        request.command,
        request.command_id,
        request.role,
        request.component_id,
        request.method_family_id,
        request.task_context,
        request.required_output_schema,
        request.effect_requirements,
    )


def test_provider_structured_output_failure_reaches_same_task_with_safe_correction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure_kind = "provider_structured_output"
    run_id = "agent-correction-provider-structured-output"
    host = WorkflowHost(tmp_path / run_id)
    secret = "must-not-reach-agent-provider-structured-output"
    try:
        host.initialize()
        _create_run(host, run_id, _expert_payload(tmp_path, run_id))
        requests = _capture_query_plan_retries(
            host,
            monkeypatch,
            (failure_kind,),
            secret=secret,
        )

        assert [request.attempt_ordinal for request in requests] == [1, 2]
        assert _task_authority(requests[1]) == _task_authority(requests[0])
        correction, = _correction_inputs(requests[1])
        assert correction.ref == f"{requests[0].attempt_id}{_CORRECTION_SUFFIX}"
        assert correction.ref not in requests[1].immutable_context_refs
        assert json.loads(correction.content_bytes()) == {
            "source_attempt_id": requests[0].attempt_id,
            "source_attempt_ordinal": 1,
            "terminal_reason": "FAILED",
            "failure_code": "nonzero_exit",
            "failure_stage": "provider_structured_output",
            "retry_class": "MODEL",
            "diagnostic_summary": f"bounded {failure_kind} diagnostic at attempt 1",
            "rejected_structured_output": {
                "typed_candidate": {
                    "query": "rejected provider query at attempt 1",
                },
            },
            "allowed_supporting_refs": list(requests[0].immutable_context_refs),
        }
        assert secret.encode() not in correction.content_bytes()
        assert secret not in json.dumps(requests[1].to_dict(), sort_keys=True)
        prompt = _prompt(requests[1]).decode("utf-8")
        lowered_prompt = prompt.lower()
        assert "agent-retry-diagnostic" in lowered_prompt
        assert "correction" in lowered_prompt
        assert "never cite them as scientific evidence" in lowered_prompt
        assert "copy them into supporting_refs" in lowered_prompt
        assert "allowed_supporting_refs" in lowered_prompt
    finally:
        host.close()


def test_plain_nonzero_exit_retries_same_task_without_candidate_correction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "agent-correction-nonzero-exit"
    host = WorkflowHost(tmp_path / run_id)
    secret = "must-not-reach-agent-nonzero-exit"
    try:
        host.initialize()
        _create_run(host, run_id, _expert_payload(tmp_path, run_id))
        requests = _capture_query_plan_retries(
            host,
            monkeypatch,
            ("nonzero_exit",),
            secret=secret,
        )

        assert [request.attempt_ordinal for request in requests] == [1, 2]
        assert _task_authority(requests[1]) == _task_authority(requests[0])
        assert _correction_inputs(requests[1]) == ()
        assert secret not in json.dumps(requests[1].to_dict(), sort_keys=True)

        failed = host.store.get_attempt(requests[0].attempt_id)
        assert failed is not None
        observation = json.loads(failed["observation_json"])
        assert observation["failure_code"] == "nonzero_exit"
        assert observation["failure_stage"] == "provider_process"
        assert observation["retry_class"] == "INFRASTRUCTURE"
        assert observation["returncode"] == 23
    finally:
        host.close()


def test_oversized_persisted_provider_candidate_is_not_projected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "agent-correction-provider-candidate-bound"
    host = WorkflowHost(tmp_path / run_id)
    try:
        host.initialize()
        _create_run(host, run_id, _expert_payload(tmp_path, run_id))
        requests = _capture_query_plan_retries(
            host,
            monkeypatch,
            ("provider_structured_output",),
            secret="must-not-reach-agent-provider-candidate-bound",
            oversized_structured_candidate=True,
        )

        correction, = _correction_inputs(requests[1])
        payload = json.loads(correction.content_bytes())
        assert "rejected_structured_output" not in payload
        assert payload["allowed_supporting_refs"] == list(
            requests[0].immutable_context_refs
        )
        assert len(correction.content_bytes()) <= (
            2 * 1024
            + sum(len(ref.encode("utf-8")) for ref in requests[0].immutable_context_refs)
        )
    finally:
        host.close()


def test_credential_key_in_persisted_provider_candidate_is_not_projected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "agent-correction-provider-candidate-credential"
    secret = "must-not-reach-agent-provider-candidate-credential"
    host = WorkflowHost(tmp_path / run_id)
    try:
        host.initialize()
        _create_run(host, run_id, _expert_payload(tmp_path, run_id))
        requests = _capture_query_plan_retries(
            host,
            monkeypatch,
            ("provider_structured_output",),
            secret=secret,
            credential_structured_candidate=True,
        )

        correction, = _correction_inputs(requests[1])
        payload = json.loads(correction.content_bytes())
        assert "rejected_structured_output" not in payload
        assert secret.encode() not in correction.content_bytes()
    finally:
        host.close()


def test_infrastructure_retry_exactly_replays_the_model_correction_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "agent-correction-model-then-infrastructure"
    host = WorkflowHost(tmp_path / run_id)
    secret = "must-not-leak-across-infrastructure-replay"
    try:
        host.initialize()
        _create_run(host, run_id, _expert_payload(tmp_path, run_id))
        requests = _capture_query_plan_retries(
            host,
            monkeypatch,
            ("provider_structured_output", "nonzero_exit"),
            secret=secret,
        )

        assert [request.attempt_ordinal for request in requests] == [1, 2, 3]
        correction, = _correction_inputs(requests[1])
        assert correction.ref == f"{requests[0].attempt_id}{_CORRECTION_SUFFIX}"
        assert requests[2].input_artifacts == requests[1].input_artifacts
        assert (
            requests[2].immutable_context_refs
            == requests[1].immutable_context_refs
        )
        assert _correction_inputs(requests[2]) == (correction,)
    finally:
        host.close()


def test_host_restart_lost_retry_exactly_replays_the_open_request_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "agent-correction-model-then-host-restart"
    state_root = tmp_path / run_id
    secret = "must-not-leak-across-host-restart"
    requests: list[AgentRequest] = []

    first = WorkflowHost(state_root)
    finish_first = first._finish_fake_agent

    def leave_second_query_plan_open(
        request: AgentRequest,
        raw: AgentResponse | bytes,
    ) -> None:
        if not _is_grounding_query_plan(request):
            finish_first(request, raw)
            return
        requests.append(request)
        if len(requests) == 1:
            first._agent_failure(
                request,
                AttemptTerminalReason.FAILED,
                "nonzero_exit",
                retryable=True,
                details=_failure_details(
                    "provider_structured_output", 1, secret,
                ),
            )

    monkeypatch.setattr(first, "_finish_fake_agent", leave_second_query_plan_open)
    try:
        first.initialize()
        payload = _expert_payload(tmp_path, run_id)
        payload["research_contract"]["budget"][
            "max_process_lost_retries"
        ] = 1
        _create_run(first, run_id, payload)
        for _ in range(40):
            first.tick()
            if len(requests) == 2:
                break
        else:
            raise AssertionError("second query-plan Attempt did not remain OPEN")

        assert [request.attempt_ordinal for request in requests] == [1, 2]
        second = first.store.get_attempt(requests[1].attempt_id)
        assert second is not None and second["state"] == "OPEN"
        correction, = _correction_inputs(requests[1])
        assert correction.ref == f"{requests[0].attempt_id}{_CORRECTION_SUFFIX}"
    finally:
        first.close()

    restarted = WorkflowHost(state_root)
    finish_restarted = restarted._finish_fake_agent

    def capture_restarted_query_plan(
        request: AgentRequest,
        raw: AgentResponse | bytes,
    ) -> None:
        if _is_grounding_query_plan(request):
            requests.append(request)
        finish_restarted(request, raw)

    monkeypatch.setattr(
        restarted, "_finish_fake_agent", capture_restarted_query_plan
    )
    try:
        restarted.initialize()
        lost = restarted.store.get_attempt(requests[1].attempt_id)
        assert lost is not None
        assert lost["terminal_reason"] == AttemptTerminalReason.LOST.value

        for _ in range(40):
            restarted.tick()
            if len(requests) == 3:
                break
        else:
            raise AssertionError("LOST query-plan Attempt did not retry")

        assert requests[2].attempt_ordinal == 3
        assert requests[2].input_artifacts == requests[1].input_artifacts
        assert (
            requests[2].immutable_context_refs
            == requests[1].immutable_context_refs
        )
        assert _correction_inputs(requests[2]) == (correction,)
    finally:
        restarted.close()


@pytest.mark.parametrize("infrastructure_retry", (False, True))
def test_literature_output_retry_preserves_packet_and_model_correction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    infrastructure_retry: bool,
) -> None:
    run_id = "agent-correction-literature-mixed-retry"
    state_root = tmp_path / run_id
    secret = "must-not-leak-across-literature-retrieval"
    fetcher = _CorrectionLiteratureFetcher()
    literature = FixedLiteratureIO(fetcher)
    retrieve = literature.retrieve
    retrieval_events: list[LiteratureQuery] = []

    def scripted_retrieve(query: LiteratureQuery):
        if started_main_requests:
            raise LiteratureFetchError(
                "retrieval became unavailable after the successful packet",
                code=LiteratureFailureCode.OPENALEX_TIMEOUT,
            )
        result = retrieve(query)
        retrieval_events.append(query)
        return result

    monkeypatch.setattr(literature, "retrieve", scripted_retrieve)
    host = WorkflowHost(state_root, literature_io=literature)
    started_main_requests: list[AgentRequest] = []
    finish = host._finish_fake_agent

    def fail_first_literature_main(
        request: AgentRequest,
        raw: AgentResponse | bytes,
    ) -> None:
        if not _is_literature_main(request):
            finish(request, raw)
            return
        started_main_requests.append(request)
        if len(started_main_requests) == 1:
            host._agent_failure(
                request,
                AttemptTerminalReason.FAILED,
                "nonzero_exit",
                retryable=True,
                details=_failure_details(
                    "provider_structured_output", 1, secret,
                ),
            )
            return
        if len(started_main_requests) == 2 and infrastructure_retry:
            host._agent_failure(
                request,
                AttemptTerminalReason.FAILED,
                "nonzero_exit",
                retryable=True,
                details=_failure_details("nonzero_exit", 2, secret),
            )
            return
        finish(request, raw)

    monkeypatch.setattr(host, "_finish_fake_agent", fail_first_literature_main)
    try:
        host.initialize()
        _create_run(host, run_id, _expert_payload(tmp_path, run_id))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            host.tick()
            if len(started_main_requests) == 2 + int(infrastructure_retry):
                break
            time.sleep(.01)
        else:
            raise AssertionError("literature MAIN mixed retry did not recover")

        rows = sorted(
            host.store.list_attempts(
                workflow_id=started_main_requests[0].owner_workflow_id,
                command_id=started_main_requests[0].command_id,
            ),
            key=lambda row: int(row["attempt_ordinal"]),
        )
        assert [int(row["attempt_ordinal"]) for row in rows] == list(
            range(1, 3 + int(infrastructure_retry))
        )
        requests = [
            AgentRequest.from_dict(
                json.loads(row["observation_json"])["request"]
            )
            for row in rows
        ]
        first, model_retry = requests[:2]
        recovered = requests[-1]
        assert [
            request.attempt_id for request in started_main_requests
        ] == [request.attempt_id for request in requests]

        observations = [json.loads(row["observation_json"]) for row in rows]
        assert observations[0]["retry_class"] == "MODEL"
        assert observations[0]["failure_stage"] == "provider_structured_output"
        assert observations[1]["preflight_diagnostics"][
            "literature_reused_from_attempt"
        ] == first.attempt_id
        assert rows[-1]["terminal_reason"] == "SUCCEEDED"
        if infrastructure_retry:
            assert observations[1]["retry_class"] == "INFRASTRUCTURE"
            assert observations[1]["failure_stage"] == "provider_process"
            assert recovered.input_artifacts == model_retry.input_artifacts
            assert recovered.immutable_context_refs == model_retry.immutable_context_refs

        correction, = _correction_inputs(model_retry)
        assert correction.ref == f"{first.attempt_id}{_CORRECTION_SUFFIX}"
        assert _correction_inputs(recovered) == (correction,)
        assert secret not in json.dumps(recovered.to_dict(), sort_keys=True)
        packets = [
            tuple(item for item in request.input_artifacts
                  if item.ref.endswith("--literature-packet"))
            for request in requests
        ]
        assert len(packets[0]) == 1
        assert all(packet == packets[0] for packet in packets[1:])

        query_input = next(
            item
            for item in first.input_artifacts
            if item.ref.endswith("--literature-query")
        )
        main_query = LiteratureQuery.from_dict(
            json.loads(query_input.content_bytes())
        )
        assert retrieval_events.count(main_query) == 1
    finally:
        host.close()


def test_repeated_model_failures_and_debug_project_only_the_direct_predecessor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "agent-correction-direct-predecessor"
    state_root = tmp_path / run_id
    host = WorkflowHost(state_root)
    secret = "must-not-leak-from-provider-excerpts"
    failure_kinds = ("provider_structured_output",) * 3
    try:
        host.initialize()
        _create_run(host, run_id, _expert_payload(tmp_path, run_id))
        requests = _capture_query_plan_retries(
            host,
            monkeypatch,
            failure_kinds,
            secret=secret,
        )

        assert [request.attempt_ordinal for request in requests] == [1, 2, 3, 4]
        assert all(
            _task_authority(request) == _task_authority(requests[0])
            for request in requests[1:]
        )
        for index, request in enumerate(requests[1:], start=1):
            correction, = _correction_inputs(request)
            payload = json.loads(correction.content_bytes())
            predecessor = requests[index - 1]
            assert correction.ref == f"{predecessor.attempt_id}{_CORRECTION_SUFFIX}"
            assert correction.ref not in request.immutable_context_refs
            assert payload["source_attempt_id"] == predecessor.attempt_id
            assert payload["source_attempt_ordinal"] == predecessor.attempt_ordinal
            assert payload["retry_class"] == "MODEL"
            assert payload["rejected_structured_output"] == {
                "typed_candidate": {
                    "query": f"rejected provider query at attempt {index}",
                },
            }
            assert payload["allowed_supporting_refs"] == list(
                predecessor.immutable_context_refs
            )
            assert (
                len(payload["diagnostic_summary"])
                <= MAX_DIAGNOSTIC_TEXT_CHARS
            )
            assert secret.encode() not in correction.content_bytes()
            assert len(correction.content_bytes()) <= (
                2 * 1024
                + sum(len(ref.encode("utf-8")) for ref in predecessor.immutable_context_refs)
            )
        assert secret not in json.dumps(requests[-1].to_dict(), sort_keys=True)

        debug_attempts = {
            item["attempt_id"]: item
            for item in build_debug_report(state_root, run_id)["attempts"]
        }
        assert debug_attempts[requests[0].attempt_id]["correction_inputs"] == {
            "refs": [],
            "count": 0,
            "truncated": False,
        }
        for index, request in enumerate(requests[1:], start=1):
            predecessor = requests[index - 1]
            assert debug_attempts[request.attempt_id]["correction_inputs"] == {
                "refs": [f"{predecessor.attempt_id}{_CORRECTION_SUFFIX}"],
                "count": 1,
                "truncated": False,
            }
    finally:
        host.close()


def test_query_plan_validation_survives_consecutive_provider_output_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    narrow_query = '"dialogue emotion mechanism" AND multimodal'
    broad_query = '"emotion recognition" OR "affective computing"'
    preferred_venues = [
        {
            "name": "Transactions on Affective Computing",
            "source_class": "JOURNAL",
        }
    ]

    class SparseNarrowFetcher(_CorrectionLiteratureFetcher):
        def search(self, query: LiteratureQuery) -> tuple[SearchPaper, ...]:
            papers = super().search(query)
            if query.query != narrow_query:
                return papers
            return tuple(
                paper
                for paper in papers
                if paper.stratum is LITERATURE_STRATA[-1]
            )

    class QueryPlanAgent:
        def __init__(self) -> None:
            self.calls = 0
            self.delegate = FakeAgent()

        def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
            response = self.delegate.invoke(request)
            assert isinstance(response, AgentResponse)
            if not _is_grounding_query_plan(request):
                return response
            self.calls += 1
            template = next(
                item
                for item in request.input_artifacts
                if item.ref.endswith("--literature-query-template")
            )
            candidate = json.loads(template.content_bytes())
            candidate["query"] = narrow_query if self.calls < 4 else broad_query
            candidate["preferred_venues"] = preferred_venues
            return with_output_digest(
                replace(response, typed_candidate=candidate, output_digest="")
            )

    run_id = "query-plan-unresolved-validation-correction"
    host = WorkflowHost(
        tmp_path / run_id,
        literature_io=FixedLiteratureIO(SparseNarrowFetcher()),
    )
    host.agent = QueryPlanAgent()  # type: ignore[assignment]
    requests: list[AgentRequest] = []
    finish = host._finish_fake_agent

    def insert_provider_failures(
        request: AgentRequest,
        raw: AgentResponse | bytes,
    ) -> None:
        if not _is_grounding_query_plan(request):
            finish(request, raw)
            return
        requests.append(request)
        if request.attempt_ordinal in {2, 3}:
            host._agent_failure(
                request,
                AttemptTerminalReason.FAILED,
                "nonzero_exit",
                retryable=True,
                details=_failure_details(
                    "provider_structured_output",
                    request.attempt_ordinal,
                    "must-not-enter-the-correction",
                ),
            )
            return
        finish(request, raw)

    monkeypatch.setattr(host, "_finish_fake_agent", insert_provider_failures)
    try:
        host.initialize()
        _create_run(host, run_id, _expert_payload(tmp_path, run_id))
        for _ in range(60):
            host.tick()
            if len(requests) == 4:
                break
        else:
            raise AssertionError("query-plan correction lineage did not recover")

        rows = sorted(
            host.store.list_attempts(
                workflow_id=run_id,
                command_id=requests[0].command_id,
            ),
            key=lambda row: int(row["attempt_ordinal"]),
        )
        persisted_requests = [
            AgentRequest.from_dict(json.loads(row["observation_json"])["request"])
            for row in rows
        ]
        assert persisted_requests == requests
        assert [request.attempt_ordinal for request in requests] == [1, 2, 3, 4]

        first_observation = json.loads(rows[0]["observation_json"])
        validation_error = first_observation["diagnostics"]["validation_error"]
        assert first_observation["failure_code"] == "invalid_candidate"
        assert narrow_query in validation_error["message"]

        first_correction = tuple(
            item
            for item in requests[1].input_artifacts
            if item.ref.endswith("--candidate-validation-error")
        )
        assert len(first_correction) == 1

        expected_unresolved = {
            "source_attempt_id": requests[0].attempt_id,
            "code": validation_error["code"],
            "message": validation_error["message"],
        }
        expected_rejected = {
            "query": narrow_query,
            "preferred_venues": preferred_venues,
        }
        first_payload = json.loads(first_correction[0].content_bytes())
        assert first_payload == {
            "source_attempt_id": requests[0].attempt_id,
            "validation_error": validation_error,
            "rejected_candidate": expected_rejected,
        }
        assert len(first_payload["validation_error"]["message"]) <= (
            MAX_DIAGNOSTIC_TEXT_CHARS
        )
        for request, predecessor in zip(
            requests[2:], requests[1:3], strict=True,
        ):
            correction, = _correction_inputs(request)
            payload = json.loads(correction.content_bytes())
            assert correction.ref == f"{predecessor.attempt_id}{_CORRECTION_SUFFIX}"
            assert correction.ref not in request.immutable_context_refs
            assert payload["source_attempt_id"] == predecessor.attempt_id
            assert payload["source_attempt_ordinal"] == predecessor.attempt_ordinal
            assert payload["failure_stage"] == "provider_structured_output"
            assert payload["retry_class"] == "MODEL"
            assert payload["rejected_structured_output"] == {
                "typed_candidate": {
                    "query": (
                        "rejected provider query at attempt "
                        f"{predecessor.attempt_ordinal}"
                    ),
                },
            }
            assert payload["unresolved_validation_error"] == expected_unresolved
            assert payload["rejected_candidate"] == expected_rejected
            assert set(payload["rejected_candidate"]) == {
                "query",
                "preferred_venues",
            }
            all_corrections = tuple(
                item
                for item in request.input_artifacts
                if item.ref.endswith(
                    ("--candidate-validation-error", _CORRECTION_SUFFIX)
                )
            )
            assert all_corrections == (correction,)
            assert len(request.input_artifacts) == len(requests[0].input_artifacts) + 1
    finally:
        host.close()


def test_host_and_debug_candidate_validation_keep_exact_correction_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "candidate-validation-correction-regression"
    state_root = tmp_path / run_id
    host = WorkflowHost(state_root)
    requests: list[AgentRequest] = []

    def invoke(request: AgentRequest) -> AgentResponse | bytes:
        requests.append(request)
        response = FakeAgent().invoke(request)
        assert isinstance(response, AgentResponse)
        if (
            request.command is AgentCommand.PROPOSE_HYPOTHESIS
            and request.attempt_ordinal == 1
        ):
            candidate = dict(response.typed_candidate or {})
            candidate.update(
                {
                    "parent_hypothesis_id": None,
                    "relation": "challenge",
                }
            )
            return with_output_digest(
                replace(response, typed_candidate=candidate, output_digest="")
            )
        return response

    monkeypatch.setattr(host.agent, "invoke", invoke)
    try:
        host.initialize()
        report = admit_profile(
            _REPOSITORY / "tests" / "fixtures" / "profiles" / "component-demo",
            run_id=run_id,
        )
        assert report.admitted, report.to_dict()
        assert report.create_run_payload is not None
        report.create_run_payload["research_contract"]["budget"].update(
            {
                "max_retries": 1,
                "max_infrastructure_retries": 0,
                "max_process_lost_retries": 0,
            }
        )
        _create_run(host, run_id, report.create_run_payload)

        for _ in range(20):
            host.tick()
            if host.store.list_facts(run_id, "HypothesisProposed"):
                break
        else:
            raise AssertionError("candidate validation correction did not recover")

        proposal_requests = [
            request
            for request in requests
            if request.command is AgentCommand.PROPOSE_HYPOTHESIS
        ]
        assert [request.attempt_ordinal for request in proposal_requests] == [1, 2]
        retry = proposal_requests[1]
        correction = next(
            item
            for item in retry.input_artifacts
            if item.ref.endswith("--candidate-validation-error")
        )
        assert correction.ref == (
            f"{proposal_requests[0].attempt_id}--candidate-validation-error"
        )
        assert correction.ref not in retry.immutable_context_refs
        assert json.loads(correction.content_bytes()) == {
            "source_attempt_id": proposal_requests[0].attempt_id,
            "validation_error": {
                "code": "invalid_candidate",
                "message": (
                    "parent_hypothesis_id and relation must be supplied together"
                ),
            },
        }
        debug_attempts = {
            item["attempt_id"]: item
            for item in build_debug_report(state_root, run_id)["attempts"]
        }
        assert debug_attempts[retry.attempt_id]["correction_inputs"] == {
            "refs": [correction.ref],
            "count": 1,
            "truncated": False,
        }
    finally:
        host.close()
