from __future__ import annotations

import json
import math
import sqlite3
import subprocess
import tempfile
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Sequence, cast

import pytest

import hypoloom.workflow_host as workflow_host_module
from hypoloom.agent_aci import (
    AgentCommand,
    AgentRequest,
    AgentResponse,
    EmbeddedInput,
    canonical_json,
    request_digest,
    required_output_schema,
)
from hypoloom.control_protocol import ControlRequest
from hypoloom.debug_mode import build_debug_report
from hypoloom.domain import HypothesisProposed, fact_to_dict
from hypoloom.literature import (
    LITERATURE_IO_VERSION,
    LITERATURE_SCHEMA_VERSION,
    LITERATURE_STRATA,
    MAX_CONTEXT_REFS,
    FetchResponse,
    FixedLiteratureIO,
    LiteratureFetchError,
    LiteratureFailureCode,
    LiteratureFilters,
    LiteraturePacket,
    LiteratureQuery,
    PdfExtraction,
    ScholarlySourceClass,
    SearchPaper,
    bounded_context_refs,
)
from hypoloom.profile_admission import PROFILE_FILES, admit_profile
from hypoloom.expert_loop import (
    AuxiliaryRequestKind,
    AuxiliaryTask,
    ResearchNodeKey,
    ResearchNodeKind,
    ResearchNodeSubkind,
    ResearchNodeTask,
)
from hypoloom.fake_agent import FakeAgent, _redigest, default_request
from hypoloom.monitor import build_monitor_snapshot
from hypoloom.oracle import (
    Oracle,
    OracleIssue,
    _audit_literature_query_plan_causation,
)
from hypoloom.workflow_host import WorkflowHost


REPOSITORY = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("stage", ["coverage", "retrieve"])
def test_slow_literature_keeps_host_tools_and_cancel_responsive(tmp_path, mount_root, stage):
    from hypoloom.host_tools_server import AgentToolServer, WebToolOperation, _call_host

    entered, release = threading.Event(), threading.Event()
    # Keep I/O blocked until both a tool reply and cancellation complete. The
    # watchdog only bounds a regressed synchronous implementation; elapsed tick
    # time also includes Git/filesystem work and is not evidence of I/O blocking.
    timer = threading.Timer(10, release.set)
    owner = threading.get_ident()
    read_threads = []

    class HeldLiterature(FixedLiteratureIO):
        def hold(self, current):
            if current == stage and not entered.is_set():
                read_threads.append(threading.get_ident())
                entered.set()
                timer.start()
                assert release.wait(15)

        def retrieve(self, query):
            self.hold("retrieve")
            return super().retrieve(query)

        def admit_query_coverage(self, query):
            self.hold("coverage")
            return super().admit_query_coverage(query)

    adapter = HeldLiterature(_LiteratureFetcher())
    host = WorkflowHost(tmp_path / "state", literature_io=adapter)
    endpoint = AgentToolServer(mount_root / "web.sock", "concurrent-reader",
        lambda *_: WebToolOperation(read=lambda: {"text": "immediate response"},
                                    finish=lambda value, error: value), scope="literature")
    # The real endpoint is serviced on the Host owner thread before dispatch.
    poll_agents = host._poll_agents
    host._poll_agents = lambda: endpoint.service() or poll_agents()
    try:
        host.initialize()
        run_id = f"slow-literature-{stage}"
        assert host.handle(ControlRequest(1, "create", 0, "CREATE_RUN",
            _payload(tmp_path, mount_root, run_id))).status == "accepted"
        deadline = time.monotonic() + 5
        while not entered.is_set() and time.monotonic() < deadline:
            host.tick()
            time.sleep(0.002)
        assert entered.is_set() and read_threads[0] != owner
        assert not release.is_set(), "Host tick waited for literature I/O"
        with ThreadPoolExecutor(max_workers=1) as client:
            future = client.submit(_call_host, str(endpoint.socket_path), "web_fetch",
                {"url": "https://example.org/source"}, deadline_at=time.monotonic() + 0.3)
            while not future.done() and time.monotonic() < deadline:
                host.tick()
                time.sleep(0.002)
            assert future.result()["text"] == "immediate response"
        assert not release.is_set(), "tool reply waited for literature I/O"
        revision = host.store.latest_transition(run_id)["revision"]
        assert host.handle(ControlRequest(1, "cancel", revision, "CANCEL",
            {"run_id": run_id, "cause": "operator"})).status == "accepted"
        assert not release.is_set(), "cancellation waited for literature I/O"
        host.tick()
        release.set()
        _drive(host, run_id)
        assert host.store.latest_transition(run_id)["state"] == "CANCELLED"
        attempts = host.store.list_attempts()
        assert all(row["state"] == "CLOSED" for row in attempts)
        assert not any(_request(row).command is AgentCommand.GROUNDING for row in attempts)
        if stage == "coverage":
            assert any(row["terminal_reason"] == "CANCELLED" for row in attempts)
            from hypoloom.agent_budget import fold_agent_attempts
            cancelled = [row for row in attempts if row["terminal_reason"] == "CANCELLED"]
            assert fold_agent_attempts(cancelled)[0].source == "discarded_response"
        assert Oracle(tmp_path / "state/state.sqlite", tmp_path / "state/artifacts").audit(run_id).ok
    finally:
        release.set()
        timer.cancel()
        host.close()
        endpoint.close()
    assert not any(t.name.startswith("hypoloom-literature") for t in threading.enumerate())


def test_shutdown_cancels_owned_agents_before_waiting_for_literature(tmp_path, mount_root):
    from types import SimpleNamespace
    from test_literature import _query
    entered, release = threading.Event(), threading.Event()
    timer = threading.Timer(0.6, release.set)
    events = []
    class HeldLiterature(FixedLiteratureIO):
        def admit_query_coverage(self, query):
            entered.set()
            timer.start()
            assert release.wait(2)
            events.append("read-finished")
    host = WorkflowHost(tmp_path / "state", literature_io=HeldLiterature(_LiteratureFetcher()))
    try:
        host.initialize()
        assert host.handle(ControlRequest(1, "create", 0, "CREATE_RUN", _payload(tmp_path, mount_root, "probe"))).status == "accepted"
        host._read_literature("probe", "coverage", "held-read", _query())
        assert entered.wait(1)
        def cancel(_handle):
            events.append("agent-cancelled")
            release.set()
        host.claude_agent_host = SimpleNamespace(cancel=cancel, cleanup=lambda _handle: None)
        host.running_agents["owned-agent"] = SimpleNamespace(
            request=SimpleNamespace(attempt_id="owned-agent", command_id="ordinary-agent"))
        started = time.monotonic()
        host.close()
        assert time.monotonic() - started < 0.25
        assert events.index("agent-cancelled") < events.index("read-finished")
    finally:
        release.set(); timer.cancel(); host.close()


class _LiteratureFetcher:
    def __init__(self, *, fail: bool = False, fail_calls: tuple[int, ...] = (),
                 failure_code: LiteratureFailureCode = LiteratureFailureCode.OPENALEX_TIMEOUT,
                 http_status: int | None = None,
                 attempt_count: int | None = None) -> None:
        self.fail = fail
        self.fail_calls = fail_calls
        self.failure_code = failure_code
        self.http_status = http_status
        self.attempt_count = attempt_count
        self.preflight_calls = 0
        self.queries: list[LiteratureQuery] = []

    @property
    def configuration(self) -> dict[str, object]:
        return {
            "adapter": "deterministic-test",
            "host": "none",
            "timeout_seconds": 1.0,
            "max_response_bytes": 4096,
        }

    def preflight(self) -> dict[str, object]:
        self.preflight_calls += 1
        return {"adapter": "deterministic-test", "network_probe_performed": False}

    def search(self, query: LiteratureQuery) -> Iterable[SearchPaper]:
        self.queries.append(query)
        if self.fail or len(self.queries) in self.fail_calls:
            raise LiteratureFetchError(
                "bounded catalog failure",
                code=self.failure_code,
                http_status=self.http_status,
                attempt_count=self.attempt_count,
            )
        return (
            SearchPaper(
                bibliographic_identity=f"openalex:W{index + 1}",
                title=f"Bounded paper {index + 1}",
                authors=("Test Researcher",),
                year=year,
                language="en",
                landing_uri=f"https://catalog.example.org/works/{index + 1}",
                source_uri=f"https://catalog.example.org/records/{index + 1}",
                source_class=ScholarlySourceClass.JOURNAL,
                stratum=stratum,
                pdf_uri=None,
            )
            for index, (stratum, year) in enumerate(
                zip(LITERATURE_STRATA, (2000, 2015, 2026), strict=True)
            )
        )

    def resolve(self, hostname: str) -> Sequence[str]:
        assert hostname == "catalog.example.org"
        return ("8.8.8.8",)

    def fetch(
        self,
        uri: str,
        *,
        approved_addresses: tuple[str, ...],
        max_bytes: int,
    ) -> FetchResponse:
        raise AssertionError("fixture papers intentionally have no PDF")

    def extract_pdf(self, content: bytes, *, max_pages: int) -> PdfExtraction:
        raise AssertionError("fixture papers intentionally have no PDF")


class _QualifiedClaudeStub:
    claude_executable = Path("/bin/true")
    bwrap_path = None
    settings_path = None

    def preflight(self, **_options: object) -> dict[str, object]:
        return {"qualified": True}


class _RetrievalClock:
    def __init__(self) -> None:
        self.wall = time.time()
        self.steady = time.monotonic()

    def time(self) -> float:
        return self.wall

    def monotonic(self) -> float:
        return self.steady

    def advance(self, seconds: float) -> None:
        self.wall += seconds
        self.steady += seconds

    def __getattr__(self, name: str) -> object:
        return getattr(time, name)


@pytest.fixture
def mount_root() -> Path:
    with tempfile.TemporaryDirectory(prefix="hypoloom-literature-host-", dir="/var/tmp") as raw:
        yield Path(raw)


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", *arguments), cwd=repository, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def _backup_database(source: Path, destination: Path) -> None:
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as target_db:
        source_db.backup(target_db)


def _payload(tmp_path: Path, mount_root: Path, run_id: str) -> dict[str, object]:
    target = mount_root / "target"
    target.mkdir()
    (target / "README.md").write_text("bounded literature host target\n", encoding="utf-8")
    _git(target, "init", "-q")
    _git(target, "add", ".")
    _git(target, "-c", "user.name=Hypoloom Test", "-c",
         "user.email=hypoloom@example.invalid", "commit", "-qm", "base")
    data = mount_root / "data"
    data.mkdir()
    (data / "README.txt").write_text("bounded fixture metadata\n", encoding="utf-8")
    source = REPOSITORY / "tests" / "fixtures" / "profiles" / "expert-loop-pilot"
    profile = tmp_path / "profile"
    profile.mkdir()
    for name in PROFILE_FILES:
        text = (source / name).read_text(encoding="utf-8")
        text = text.replace("/REPLACE_ME/expert-loop-pilot-target", str(target.resolve()))
        text = text.replace("/REPLACE_ME/expert-loop-pilot-data", str(data.resolve()))
        if name == "safety.yaml":
            text = "allow_read_paths: []\nsandbox_os: linux\n"
        (profile / name).write_text(text, encoding="utf-8")
    report = admit_profile(profile, run_id=run_id)
    assert report.admitted and report.create_run_payload is not None, report.to_dict()
    return report.create_run_payload


def _drive(host: WorkflowHost, run_id: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        host.tick()
        if host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return
        time.sleep(0.005)
    raise AssertionError(f"literature host run did not terminate: {host.store.latest_transition(run_id)}")


def _request(row: dict[str, object]) -> AgentRequest:
    return AgentRequest.from_dict(json.loads(str(row["observation_json"]))["request"])


def _has_request(row: dict[str, object]) -> bool:
    return "request" in json.loads(str(row["observation_json"]))


def _is_research_node_attempt(
    row: dict[str, object], subkind: ResearchNodeSubkind,
) -> bool:
    if not _has_request(row):
        return False
    request = _request(row)
    return (
        request.command is AgentCommand.LITERATURE_REVIEW
        and isinstance(request.task_context, ResearchNodeTask)
        and request.task_context.key.subkind is subkind
    )


def _is_literature_retrieval_attempt(row: dict[str, object]) -> bool:
    if not _has_request(row):
        return False
    request = _request(row)
    return request.command is AgentCommand.GROUNDING or (
        request.command is AgentCommand.LITERATURE_REVIEW
        and isinstance(request.task_context, ResearchNodeTask)
        and request.task_context.key.subkind is ResearchNodeSubkind.MAIN
    )


def _oracle_query_plan_exchanges(
    *, include_packet: bool = True, change_frozen_field: bool = False,
    change_query: bool = False, change_preferred_venues: bool = False,
    fake_offline: bool = False, schema_version: int = 9,
) -> tuple[list[dict[str, object]], dict[str, dict[str, object]],
           dict[str, tuple[AgentRequest, AgentResponse]]]:
    base = replace(
        default_request(AgentCommand.LITERATURE_REVIEW),
        schema_version=schema_version,
    )
    assert isinstance(base.task_context, ResearchNodeTask)
    plan_task = ResearchNodeTask(key=replace(
        base.task_context.key, subkind=ResearchNodeSubkind.QUERY_PLAN,
    ))
    skill = EmbeddedInput.from_bytes(
        f"{plan_task.command_id}--openalex-query-planning-skill",
        "text/markdown", b"bounded query planning\n",
    )
    template_query = LiteratureQuery(
        schema_version=1 if schema_version == 8 else LITERATURE_SCHEMA_VERSION,
        query='"multimodal affect" AND causal',
        filters=LiteratureFilters(
            year_from=1997, year_to=2026, languages=("en",),
        ),
        max_items=3,
        allowed_source_classes=(ScholarlySourceClass.JOURNAL,),
        strata=LITERATURE_STRATA,
        latest_as_of="2026-08-29",
        immutable_context_refs=(skill.ref,),
    )
    template = EmbeddedInput.from_bytes(
        f"{plan_task.command_id}--literature-query-template",
        "application/json", canonical_json(template_query.to_dict()),
    )
    real_adapter = (
        "claude-code-bwrap/18" if schema_version == 8 else "claude-code-bwrap/24"
    )
    plan_request = replace(
        base,
        task_context=plan_task,
        command_id=plan_task.command_id,
        input_artifacts=(skill, template),
        immutable_context_refs=(skill.ref, template.ref),
        required_output_schema=required_output_schema(
            base.command, plan_task, schema_version=schema_version,
        ),
        required_adapter_version=(
            "fake-agent/4" if fake_offline else real_adapter
        ),
        tool_permission_profile="agent-host-mediated-literature/1",
    )
    plan_response = FakeAgent().invoke(plan_request)
    assert isinstance(plan_response, AgentResponse)
    plan_response = _redigest(replace(
        plan_response, adapter_version=plan_request.required_adapter_version,
    ))
    if change_query:
        candidate = dict(plan_response.typed_candidate or {})
        candidate["query"] = '"multimodal affect" AND intervention'
        plan_response = _redigest(replace(plan_response, typed_candidate=candidate))
    if change_preferred_venues:
        candidate = dict(plan_response.typed_candidate or {})
        candidate["preferred_venues"] = [
            {"name": "NeurIPS", "source_class": "CONFERENCE"},
        ]
        plan_response = _redigest(replace(plan_response, typed_candidate=candidate))
    if change_frozen_field:
        candidate = dict(plan_response.typed_candidate or {})
        filters = dict(candidate["filters"])
        filters["year_from"] = int(filters["year_from"]) + 1
        candidate["filters"] = filters
        plan_response = _redigest(replace(plan_response, typed_candidate=candidate))
    planned = dict(plan_response.typed_candidate or {})
    query_input = EmbeddedInput.from_bytes(
        f"{plan_request.attempt_id}--literature-query",
        "application/json", canonical_json(planned),
    )
    inputs = (query_input,)
    if include_packet:
        inputs += (EmbeddedInput.from_bytes(
            f"{base.command_id}--literature-packet", "application/json",
            canonical_json({"query": planned}),
        ),)
    main_request = replace(
        base,
        attempt_id="attempt-main",
        input_artifacts=inputs,
        immutable_context_refs=tuple(item.ref for item in inputs),
        required_adapter_version=(
            "fake-agent/4" if fake_offline else real_adapter
        ),
        tool_permission_profile="agent-host-mediated-literature/1",
    )
    main_response = FakeAgent().invoke(main_request)
    assert isinstance(main_response, AgentResponse)
    main_response = _redigest(replace(
        main_response, adapter_version=main_request.required_adapter_version,
    ))
    successful = {
        plan_request.attempt_id: (plan_request, plan_response),
        main_request.attempt_id: (main_request, main_response),
    }
    attempts = [
        {"attempt_id": plan_request.attempt_id, "started_at": 1.0, "ended_at": 2.0},
        {"attempt_id": main_request.attempt_id, "started_at": 3.0, "ended_at": 4.0},
    ]
    payloads = {
        attempt_id: {"request": request.to_dict(), "response": response.to_dict()}
        for attempt_id, (request, response) in successful.items()
    }
    return attempts, payloads, successful


def test_query_plan_oracle_keeps_aci7_main_read_compatible() -> None:
    request = replace(default_request(AgentCommand.LITERATURE_REVIEW), schema_version=7)
    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    attempts = [{"attempt_id": request.attempt_id, "started_at": 1.0, "ended_at": 2.0}]
    payloads = {request.attempt_id: {
        "request": request.to_dict(), "response": response.to_dict(),
    }}
    issues: list[OracleIssue] = []

    _audit_literature_query_plan_causation(
        attempts, payloads, {request.attempt_id: (request, response)}, issues,
    )  # type: ignore[arg-type]

    assert issues == []


@pytest.mark.parametrize("schema_version", (8, 9))
def test_query_plan_oracle_requires_packet_for_successful_planned_main(
    schema_version: int,
) -> None:
    attempts, payloads, successful = _oracle_query_plan_exchanges(
        include_packet=False, schema_version=schema_version,
    )
    issues: list[OracleIssue] = []

    _audit_literature_query_plan_causation(
        attempts, payloads, successful, issues,
    )  # type: ignore[arg-type]

    assert any("exactly one retrieved packet" in issue.message for issue in issues)


def test_query_plan_oracle_allows_explicit_fake_offline_main_without_packet() -> None:
    attempts, payloads, successful = _oracle_query_plan_exchanges(
        include_packet=False, fake_offline=True,
    )
    issues: list[OracleIssue] = []

    _audit_literature_query_plan_causation(
        attempts, payloads, successful, issues,
    )  # type: ignore[arg-type]

    assert issues == []


@pytest.mark.parametrize("schema_version", (8, 9))
def test_query_plan_oracle_independently_rejects_changed_frozen_template(
    schema_version: int,
) -> None:
    attempts, payloads, successful = _oracle_query_plan_exchanges(
        change_frozen_field=True, schema_version=schema_version,
    )
    issues: list[OracleIssue] = []

    _audit_literature_query_plan_causation(
        attempts, payloads, successful, issues,
    )  # type: ignore[arg-type]

    assert any("changed or lacks its one frozen template" in issue.message for issue in issues)


@pytest.mark.parametrize("schema_version", (8, 9))
def test_query_plan_oracle_allows_query_change_for_both_planned_versions(
    schema_version: int,
) -> None:
    attempts, payloads, successful = _oracle_query_plan_exchanges(
        change_query=True, schema_version=schema_version,
    )
    issues: list[OracleIssue] = []

    _audit_literature_query_plan_causation(
        attempts, payloads, successful, issues,
    )  # type: ignore[arg-type]

    assert issues == []


@pytest.mark.parametrize(("schema_version", "allowed"), ((8, False), (9, True)))
def test_query_plan_oracle_versions_preferred_venue_selection(
    schema_version: int, allowed: bool,
) -> None:
    attempts, payloads, successful = _oracle_query_plan_exchanges(
        change_preferred_venues=True, schema_version=schema_version,
    )
    issues: list[OracleIssue] = []

    _audit_literature_query_plan_causation(
        attempts, payloads, successful, issues,
    )  # type: ignore[arg-type]

    assert (issues == []) is allowed


def test_query_plan_oracle_rejects_main_citation_of_the_query_input() -> None:
    attempts, payloads, successful = _oracle_query_plan_exchanges()
    main_id = "attempt-main"
    request, response = successful[main_id]
    query_ref = next(item.ref for item in request.input_artifacts
        if item.ref.endswith("--literature-query"))
    bad = _redigest(replace(response, typed_candidate={
        **dict(response.typed_candidate or {}), "supporting_refs": [query_ref],
    }))
    successful[main_id] = (request, bad)
    payloads[main_id]["response"] = bad.to_dict()
    issues: list[OracleIssue] = []

    _audit_literature_query_plan_causation(
        attempts, payloads, successful, issues,
    )  # type: ignore[arg-type]

    assert any("outside its retrieved packet" in issue.message for issue in issues)


def test_host_preflights_and_embeds_retrieved_packet_before_agent_request(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    fetcher = _LiteratureFetcher()
    adapter = FixedLiteratureIO(fetcher, max_pdf_pages=20)
    run_id = "literature-packet-host"
    host = WorkflowHost(tmp_path / "state", literature_io=adapter)
    try:
        host.initialize()
        assert fetcher.preflight_calls == 1
        created = host.handle(ControlRequest(
            schema_version=1, request_id=f"create-{run_id}", expected_revision=0,
            command="CREATE_RUN", payload=_payload(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created
        _drive(host, run_id)
        attempt = next(
            row for row in host.store.list_attempts()
            if row["run_id"] == run_id
            and _is_research_node_attempt(row, ResearchNodeSubkind.MAIN)
        )
        request = _request(attempt)
        plan = next(row for row in host.store.list_attempts()
            if row["run_id"] == run_id
            and _is_research_node_attempt(row, ResearchNodeSubkind.QUERY_PLAN))
        plan_observation = json.loads(str(plan["observation_json"]))
        plan_request = _request(plan)
        planned_query = plan_observation["response"]["typed_candidate"]
        query_input = next(item for item in request.input_artifacts
            if item.ref.endswith("--literature-query"))
        assert query_input.ref == f"{plan['attempt_id']}--literature-query"
        assert json.loads(query_input.content_bytes()) == planned_query
        assert len([item for item in plan_request.input_artifacts
            if item.ref.endswith("--openalex-query-planning-skill")]) == 1
        expert_config_input = next(item for item in plan_request.input_artifacts
            if item.ref.endswith("--expert-config"))
        expert_config = json.loads(expert_config_input.content_bytes())
        assert expert_config["expert"] == {"id": "ml-researcher", "focus":
            "Background: machine learning and experimental ML; own the complete "
            "six-node loop for the bounded component."}
        plan_template = LiteratureQuery.from_dict(json.loads(next(
            item.content_bytes() for item in plan_request.input_artifacts
            if item.ref.endswith("--literature-query-template"))))
        assert expert_config_input.ref in plan_template.immutable_context_refs
        assert not any(item.ref.endswith("--openalex-query-planning-skill")
            for item in request.input_artifacts)
        packet_input = next(
            item for item in request.input_artifacts
            if item.ref.endswith("--literature-packet")
        )
        packet = LiteraturePacket.from_bytes(packet_input.content_bytes())
        assert packet.adapter_version == LITERATURE_IO_VERSION
        downloads = json.loads(str(attempt['observation_json']))['preflight_diagnostics']['literature_download_trace']
        assert {item['bibliographic_identity'] for item in downloads} == {item.bibliographic_identity for item in packet.papers}
        assert all(item['events'] for item in downloads)
        assert 'literature_download_trace' not in plan_observation.get('preflight_diagnostics', {})
        assert packet.query.to_dict() == planned_query
        assert packet.query == fetcher.queries[3]
        assert fetcher.queries[2] == fetcher.queries[3]
        assert packet.query.latest_as_of == str(
            host.store.get_run(run_id)["created_at"]
        ).partition("T")[0]
        grounding = next(
            row for row in host.store.list_attempts()
            if row["run_id"] == run_id and _request(row).command is AgentCommand.GROUNDING
        )
        grounding_request = _request(grounding)
        grounding_packet = LiteraturePacket.from_bytes(next(
            item.content_bytes() for item in grounding_request.input_artifacts
            if item.ref.endswith("--literature-packet")
        ))
        grounding_config = json.loads(next(
            item.content_bytes() for item in grounding_request.input_artifacts
            if item.ref.endswith("--grounding-config")
        ))
        grounding_context = json.loads(next(
            item.content_bytes() for item in grounding_request.input_artifacts
            if item.ref.endswith("--grounding-context")
        ))
        assert grounding_packet.query == fetcher.queries[1]
        assert fetcher.queries[0] == fetcher.queries[1]
        assert grounding_config["grounding_scope"] == "role-neutral"
        assert "expert_slot" not in grounding_config
        assert "expert" not in grounding_config
        assert "component" not in grounding_config
        assert grounding_context["target"]["commit"] == grounding_config["execution"]["target"]["commit"]
        assert next(
            item for item in grounding_context["target"]["entries"]
            if item["path"] == "README.md"
        )["sample_text"] == "bounded literature host target\n"
        assert f"{grounding['attempt_id']}--response" in packet.query.immutable_context_refs
        assert packet_input.ref in request.immutable_context_refs
        assert len(fetcher.queries) == 4
        assert not any(item.ref.startswith("literature-pdf-") for item in request.input_artifacts)
    finally:
        host.close()


def test_grounding_output_retry_reuses_exact_packet_without_second_retrieval(tmp_path, mount_root):
    class FailFirstGrounding:
        def invoke(self, request):
            if request.command is AgentCommand.GROUNDING and request.attempt_ordinal == 1:
                response = FakeAgent().invoke(request)
                candidate = dict(response.typed_candidate)
                candidate["supporting_refs"] = ["wrong-long-reference"]
                return _redigest(replace(response, typed_candidate=candidate))
            return FakeAgent().invoke(request)
    fetcher = _LiteratureFetcher()
    run_id = "packet-reuse"
    host = WorkflowHost(tmp_path / "state", literature_io=FixedLiteratureIO(fetcher))
    host.agent = FailFirstGrounding()
    try:
        host.initialize()
        payload = _payload(tmp_path, mount_root, run_id)
        payload["research_contract"]["budget"]["max_retries"] = 1
        assert host.handle(ControlRequest(1, "create-reuse", 0, "CREATE_RUN", payload)).status == "accepted"
        _drive(host, run_id)
        attempts = [row for row in host.store.list_attempts() if _has_request(row)
            and _request(row).command is AgentCommand.GROUNDING]
        assert [row["terminal_reason"] for row in attempts] == ["FAILED", "SUCCEEDED"]
        packets = [next(item for item in _request(row).input_artifacts if item.ref.endswith("--literature-packet")) for row in attempts]
        assert packets[0] == packets[1]
        observed = json.loads(attempts[1]["observation_json"])
        assert observed["preflight_diagnostics"]["literature_reused_from_attempt"] == attempts[0]["attempt_id"]
        assert len(fetcher.queries) == 4  # Grounding/main each have a preflight + retrieval.
        report = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
        assert report.ok, report.to_dict()
    finally:
        host.close()


def test_rejected_paper_keeps_grounding_and_expert_research_advancing(tmp_path):
    class RejectedLanding(_LiteratureFetcher):
        def search(self, query):
            papers = tuple(super().search(query))
            return (replace(papers[0], landing_uri="https://rejected.example.org/paper"), *papers[1:])

        def resolve(self, hostname):
            return ("10.0.0.7",) if hostname == "rejected.example.org" else super().resolve(hostname)

    mount = tmp_path / "mount"
    mount.mkdir()
    run_id = "isolated-literature"
    fetcher = RejectedLanding()
    host = WorkflowHost(tmp_path / "state", literature_io=FixedLiteratureIO(fetcher, pdf_workers=2))
    try:
        host.initialize()
        created = host.handle(ControlRequest(1, "create", 0, "CREATE_RUN", _payload(tmp_path, mount, run_id)))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not host.store.list_facts(run_id, "HypothesisProposed"):
            host.tick()
            time.sleep(0.005)
        assert host.store.list_facts(run_id, "HypothesisProposed"), "literature failure must not stop the proposal"
        # The scripted agent does not submit tool-driven experiments. Close this
        # bounded next-action test through the real operator cancellation path.
        revision = host.store.latest_transition(run_id)["revision"]
        assert host.handle(ControlRequest(1, "cancel", revision, "CANCEL",
            {"run_id": run_id, "cause": "operator"})).status == "accepted"
        _drive(host, run_id)
        assert host.store.latest_transition(run_id)["state"] == "CANCELLED"
        attempts = host.store.list_attempts()
        prepared = [row for row in attempts if row["kind"] == "AGENT"
                    and (_request(row).command is AgentCommand.GROUNDING
                         or _is_research_node_attempt(row, ResearchNodeSubkind.MAIN))]
        assert len(prepared) == 2  # One Grounding and one expert literature task, no outer retries.
        for row in prepared:
            assert row["terminal_reason"] == "SUCCEEDED"
            observation = json.loads(row["observation_json"])
            packet = LiteraturePacket.from_bytes(next(item.content_bytes() for item in _request(row).input_artifacts
                                                      if item.ref.endswith("--literature-packet")))
            assert len(packet.papers) == 3
            assert packet.papers[0].retrieval_disposition.value == "PDF_UNAVAILABLE"
            assert packet.papers[0].pdf_trace is None and packet.papers[0].claim_refs == ()
            event = observation["preflight_diagnostics"]["literature_download_trace"][0]["events"][0]
            assert event["source_host"] == "rejected.example.org" and event["failure_code"] == "VALIDATION_FAILED"
        assert any(row["kind"] == "AGENT" and _request(row).command is AgentCommand.HYPOTHESIS_PROPOSAL
                   and row["terminal_reason"] == "SUCCEEDED" for row in attempts)
        assert all(row["state"] == "CLOSED" for row in attempts)
        assert not any(json.loads(row["observation_json"]).get("failure_stage") == "literature_retrieval" for row in attempts)
        assert Oracle(tmp_path / "state/state.sqlite", tmp_path / "state/artifacts").audit(run_id).ok
    finally:
        host.close()
    assert not any(t.name.startswith("hypoloom-literature") for t in threading.enumerate())


def test_successful_retrieval_is_timed_inside_its_agent_attempt(
    tmp_path: Path,
    mount_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "literature-success-timing"
    adapter = FixedLiteratureIO(_LiteratureFetcher())
    original_retrieve = adapter.retrieve
    clock = _RetrievalClock()
    windows: list[tuple[float, float, float]] = []

    def measured_retrieve(query: LiteratureQuery):
        started = clock.wall
        result = original_retrieve(query)
        duration = float(len(windows) + 2)
        clock.advance(duration)
        windows.append((started, clock.wall, duration))
        return result

    monkeypatch.setattr(workflow_host_module, "time", clock)
    monkeypatch.setattr("hypoloom.literature.time", clock)
    monkeypatch.setattr(adapter, "retrieve", measured_retrieve)
    host = WorkflowHost(tmp_path / "state", literature_io=adapter)
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            1, f"create-{run_id}", 0, "CREATE_RUN",
            _payload(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created
        _drive(host, run_id)

        attempts = sorted(
            (
                row for row in host.store.list_attempts()
                if row["run_id"] == run_id
                and _is_literature_retrieval_attempt(row)
            ),
            key=lambda row: float(row["started_at"]),
        )
        assert len(attempts) == len(windows) == 2
        for row, (retrieval_started, retrieval_ended, duration) in zip(
            attempts, windows, strict=True,
        ):
            assert float(row["started_at"]) <= retrieval_started
            assert float(row["ended_at"]) >= retrieval_ended
            observation = json.loads(str(row["observation_json"]))
            recorded = observation["preflight_diagnostics"][
                "literature_retrieval_seconds"
            ]
            assert math.isfinite(recorded)
            assert recorded == pytest.approx(duration)
    finally:
        host.close()


def test_grounding_plan_oracle_rejects_tampered_binding_packet_and_owner(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    run_id = "grounding-plan-oracle-tamper"
    state_root = tmp_path / "state"
    host = WorkflowHost(
        state_root, literature_io=FixedLiteratureIO(_LiteratureFetcher()),
    )
    host_closed = False
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            1, f"create-{run_id}", 0, "CREATE_RUN",
            _payload(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created
        _drive(host, run_id)

        attempts = [
            row for row in host.store.list_attempts()
            if row["run_id"] == run_id and _has_request(row)
        ]
        plan = next(
            row for row in attempts
            if isinstance((request := _request(row)).task_context, AuxiliaryTask)
            and request.task_context.kind
            is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN
        )
        grounding = next(
            row for row in attempts
            if _request(row).command is AgentCommand.GROUNDING
        )
        original = json.loads(str(grounding["observation_json"]))
        original_request = AgentRequest.from_dict(original["request"])
        original_response = AgentResponse.from_dict(original["response"])

        host.close()
        host_closed = True
        source_database = state_root / "state.sqlite"
        artifact_root = state_root / "artifacts"
        assert Oracle(source_database, artifact_root).audit(run_id).ok

        for suffix in ("--literature-query", "--literature-packet"):
            inputs = list(original_request.input_artifacts)
            matches = [
                (index, item) for index, item in enumerate(inputs)
                if item.ref.endswith(suffix)
            ]
            assert len(matches) == 1
            index, source = matches[0]
            content = json.loads(source.content_bytes())
            if suffix == "--literature-query":
                content["query"] = '"tampered grounding query"'
            else:
                content["query"]["query"] = '"tampered packet query"'
            inputs[index] = EmbeddedInput.from_bytes(
                source.ref, source.media_type, canonical_json(content),
            )
            changed_request = replace(
                original_request, input_artifacts=tuple(inputs),
            )
            changed_response = _redigest(replace(
                original_response,
                input_digest=request_digest(changed_request),
                output_digest="",
            ))
            changed_observation = {
                **original,
                "request": changed_request.to_dict(),
                "response": changed_response.to_dict(),
            }
            tampered_database = tmp_path / f"grounding-plan{suffix}.sqlite"
            _backup_database(source_database, tampered_database)
            with sqlite3.connect(tampered_database) as connection:
                connection.execute("DROP TRIGGER execution_attempt_update_rules")
                updated = connection.execute(
                    "UPDATE execution_attempts SET observation_json=? "
                    "WHERE attempt_id=?",
                    (
                        json.dumps(
                            changed_observation, sort_keys=True,
                            separators=(",", ":"),
                        ),
                        grounding["attempt_id"],
                    ),
                )
                assert updated.rowcount == 1
            report = Oracle(tampered_database, artifact_root).audit(run_id)
            assert "literature_query_plan_causation_mismatch" in {
                issue.code for issue in report.issues
            }, report.to_json()

        owner_database = tmp_path / "grounding-plan-owner.sqlite"
        _backup_database(source_database, owner_database)
        with sqlite3.connect(owner_database) as connection:
            connection.execute("DROP TRIGGER immutable_workflow_commands_update")
            updated = connection.execute(
                "UPDATE workflow_commands SET command_type='REQUEST_RESEARCH_NODE' "
                "WHERE command_id=?",
                (plan["command_id"],),
            )
            assert updated.rowcount == 1
        owner_report = Oracle(owner_database, artifact_root).audit(run_id)
        assert "agent_command_owner_mismatch" in {
            issue.code for issue in owner_report.issues
        }, owner_report.to_json()
    finally:
        if not host_closed:
            host.close()


def test_literature_failure_uses_same_bounded_agent_attempt_semantics(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    # Unknown adapter faults keep ordinary bounded retries. Known service
    # outages have separate availability recovery (test_service_notifications).
    fetcher = _LiteratureFetcher(fail=True, failure_code=LiteratureFailureCode.FETCH_FAILED)
    run_id = "literature-failure-host"
    host = WorkflowHost(tmp_path / "state", literature_io=FixedLiteratureIO(fetcher))
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1, request_id=f"create-{run_id}", expected_revision=0,
            command="CREATE_RUN", payload=_payload(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created
        _drive(host, run_id)
        attempts = [
            row for row in host.store.list_attempts()
            if row["run_id"] == run_id
            and _request(row).command is AgentCommand.GROUNDING
        ]
        assert len(attempts) == 3
        assert len({row["command_id"] for row in attempts}) == 1
        assert all(row["state"] == "CLOSED" for row in attempts)
        observations = [json.loads(str(row["observation_json"])) for row in attempts]
        assert {item["failure_code"] for item in observations} == {
            "literature_fetch_failed"
        }
        assert {item["literature_diagnostic"] for item in observations} == {
            "bounded catalog failure"
        }
        monitor_attempts = {
            item.attempt_id: item
            for item in build_monitor_snapshot(host.state_root, run_id).attempts
        }
        assert {
            monitor_attempts[str(row["attempt_id"])].diagnostic_summary
            for row in attempts
        } == {"bounded catalog failure"}
        assert {
            monitor_attempts[str(row["attempt_id"])].failure_code
            for row in attempts
        } == {"literature_fetch_failed"}
        assert len(fetcher.queries) == 4
        assert host.operational_halt is None
        failures = host.store.inspect(
            "SELECT payload_json FROM workflow_transitions WHERE run_id=? "
            "AND json_extract(payload_json,'$.agent_final_failure')=1",
            (run_id,),
        )
        assert sum(
            json.loads(row["payload_json"]).get("failed_task_command_id")
            == attempts[-1]["command_id"] for row in failures
        ) == 1
    finally:
        host.close()


def test_each_failed_retrieval_retry_has_an_independent_attempt_duration(
    tmp_path: Path,
    mount_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "literature-failure-timing"
    adapter = FixedLiteratureIO(_LiteratureFetcher(fail=True, failure_code=LiteratureFailureCode.FETCH_FAILED))
    original_retrieve = adapter.retrieve
    clock = _RetrievalClock()
    windows: list[tuple[float, float, float]] = []

    def measured_retrieve(query: LiteratureQuery):
        started = clock.wall
        duration = float(len(windows) + 2)
        try:
            return original_retrieve(query)
        finally:
            clock.advance(duration)
            windows.append((started, clock.wall, duration))

    monkeypatch.setattr(workflow_host_module, "time", clock)
    monkeypatch.setattr("hypoloom.literature.time", clock)
    monkeypatch.setattr(adapter, "retrieve", measured_retrieve)
    host = WorkflowHost(tmp_path / "state", literature_io=adapter)
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            1, f"create-{run_id}", 0, "CREATE_RUN",
            _payload(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created
        _drive(host, run_id)

        attempts = sorted(
            (
                row for row in host.store.list_attempts()
                if row["run_id"] == run_id
                and _request(row).command is AgentCommand.GROUNDING
            ),
            key=lambda row: int(row["attempt_ordinal"]),
        )
        assert len(attempts) == len(windows) == 3
        assert [int(row["attempt_ordinal"]) for row in attempts] == [1, 2, 3]
        assert len({str(row["command_id"]) for row in attempts}) == 1

        requests = [_request(row) for row in attempts]
        assert all(
            request.input_artifacts == requests[0].input_artifacts
            and request.immutable_context_refs == requests[0].immutable_context_refs
            for request in requests[1:]
        )
        for row, (retrieval_started, retrieval_ended, duration) in zip(
            attempts, windows, strict=True,
        ):
            assert float(row["started_at"]) <= retrieval_started
            assert float(row["ended_at"]) >= retrieval_ended
            observation = json.loads(str(row["observation_json"]))
            assert observation["retry_class"] == "INFRASTRUCTURE"
            assert observation["failure_stage"] == "literature_retrieval"
            recorded = observation["literature_retrieval_seconds"]
            assert math.isfinite(recorded)
            assert recorded == pytest.approx(duration)
    finally:
        host.close()


def test_literature_http_status_is_preserved_in_existing_attempt_observation(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    class RejectedQueryFetcher(_LiteratureFetcher):
        def search(self, query: LiteratureQuery) -> Iterable[SearchPaper]:
            self.queries.append(query)
            raise LiteratureFetchError(
                "OpenAlex rejected the generated query",
                code=LiteratureFailureCode.QUERY_INVALID,
                http_status=400,
            )

    fetcher = RejectedQueryFetcher()
    run_id = "literature-query-rejected-host"
    host = WorkflowHost(tmp_path / "state", literature_io=FixedLiteratureIO(fetcher))
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1, request_id=f"create-{run_id}", expected_revision=0,
            command="CREATE_RUN", payload=_payload(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created
        _drive(host, run_id)
        attempts = [
            row for row in host.store.list_attempts()
            if row["run_id"] == run_id
            and _request(row).command is AgentCommand.GROUNDING
        ]
        assert len(attempts) == 1
        observations = [json.loads(str(row["observation_json"])) for row in attempts]
        assert {item["failure_code"] for item in observations} == {
            "literature_query_invalid"
        }
        assert {item["literature_http_status"] for item in observations} == {400}
        assert {item["literature_failure_class"] for item in observations} == {
            "LiteratureFetchError"
        }
        assert {item["literature_diagnostic"] for item in observations} == {
            "OpenAlex rejected the generated query"
        }
    finally:
        host.close()


def test_successful_literature_transport_retries_are_durable_debug_evidence(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    class RetriedFetcher(_LiteratureFetcher):
        @property
        def last_retry_trace(self) -> tuple[dict[str, object], ...]:
            return ({
                "retry_ordinal": 1,
                "endpoint": "works",
                "request_attempt_ordinal": 1,
                "failure_code": "OPENALEX_HTTP",
                "http_status": 503,
                "retry_after_seconds": 0.25,
            },)

    fetcher = RetriedFetcher()
    run_id = "literature-successful-retry-trace"
    state_root = tmp_path / "state"
    host = WorkflowHost(state_root, literature_io=FixedLiteratureIO(fetcher))
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            1, f"create-{run_id}", 0, "CREATE_RUN",
            _payload(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created
        _drive(host, run_id)
        grounding = next(
            row for row in host.store.list_attempts()
            if row["run_id"] == run_id
            and _request(row).command is AgentCommand.GROUNDING
        )
        observation = json.loads(str(grounding["observation_json"]))
        preflight = observation["preflight_diagnostics"]
        assert preflight["literature_retry_trace"] == [
            dict(fetcher.last_retry_trace[0])
        ]
        debug_attempt = next(
            item for item in build_debug_report(state_root, run_id)["attempts"]
            if item["attempt_id"] == grounding["attempt_id"]
        )
        assert debug_attempt["literature_retry_count"] == 1
        assert debug_attempt["literature_retry_trace"] == [
            dict(fetcher.last_retry_trace[0])
        ]
        assert Oracle(
            state_root / "state.sqlite", state_root / "artifacts",
        ).audit(run_id).ok
    finally:
        host.close()


@pytest.mark.parametrize(
    ("failure_code", "http_status"),
    (
        (LiteratureFailureCode.OPENALEX_HTTP, 404),
        (LiteratureFailureCode.OPENALEX_RESPONSE_INVALID, None),
        (LiteratureFailureCode.OPENALEX_RESPONSE_LIMIT, None),
    ),
)
def test_deterministic_literature_failure_does_not_open_outer_retry(
    tmp_path: Path,
    mount_root: Path,
    failure_code: LiteratureFailureCode,
    http_status: int | None,
) -> None:
    fetcher = _LiteratureFetcher(
        fail=True,
        failure_code=failure_code,
        http_status=http_status,
        attempt_count=1,
    )
    run_id = f"literature-fail-fast-{failure_code.value.lower()}"
    host = WorkflowHost(tmp_path / "state", literature_io=FixedLiteratureIO(fetcher))
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            1, f"create-{run_id}", 0, "CREATE_RUN",
            _payload(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created
        _drive(host, run_id)
        attempts = [
            row for row in host.store.list_attempts()
            if row["run_id"] == run_id
            and _request(row).command is AgentCommand.GROUNDING
        ]
        assert len(attempts) == 1
        observation = json.loads(str(attempts[0]["observation_json"]))
        assert observation["retry_class"] is None
        assert len(fetcher.queries) == 2
        assert fetcher.queries[0] == fetcher.queries[1]
    finally:
        host.close()


def test_invalid_query_plan_retries_before_probe_and_projects_validation_error(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    class InvalidThenValidPlan:
        def __init__(self) -> None:
            self.calls = 0
            self.delegate = FakeAgent()

        def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
            response = self.delegate.invoke(request)
            assert isinstance(response, AgentResponse)
            task = request.task_context
            if (isinstance(task, ResearchNodeTask)
                    and task.key.subkind is ResearchNodeSubkind.QUERY_PLAN):
                self.calls += 1
                if self.calls == 1:
                    candidate = dict(response.typed_candidate or {})
                    filters = dict(candidate["filters"])
                    filters["year_from"] = int(filters["year_from"]) + 1
                    candidate["filters"] = filters
                    return _redigest(replace(response, typed_candidate=candidate))
            return response

    fetcher = _LiteratureFetcher()
    run_id = "literature-plan-retry-host"
    host = WorkflowHost(tmp_path / "state", literature_io=FixedLiteratureIO(fetcher))
    host.agent = InvalidThenValidPlan()  # type: ignore[assignment]
    try:
        host.initialize()
        created = host.handle(ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN",
            _payload(tmp_path, mount_root, run_id)))
        assert created.status == "accepted", created
        status = host.handle(ControlRequest(1, f"status-{run_id}", None, "STATUS", {"run_id": run_id}))
        assert status.result is not None
        # Core estimate includes FINAL and each batch's ADJ/UPDATE/REPORT/SELF;
        # management interactions are explicitly outside this projection.
        assert status.result["agent_usage"]["remaining_declared_limits"]["attempts"] == (
            8 * (3 + 5 * 2 + 3 * 2 + 4 * 2)
        )
        _drive(host, run_id)
        rows = [row for row in host.store.list_attempts() if row["run_id"] == run_id]
        plans = sorted((row for row in rows
            if _is_research_node_attempt(row, ResearchNodeSubkind.QUERY_PLAN)),
            key=lambda row: int(row["attempt_ordinal"]))
        mains = [row for row in rows
            if _is_research_node_attempt(row, ResearchNodeSubkind.MAIN)]
        assert len(plans) == 2 and len(mains) == 1
        assert json.loads(str(plans[0]["observation_json"]))["failure_code"] == "invalid_candidate"
        retry = _request(plans[1])
        assert any(item.ref.endswith("--candidate-validation-error") for item in retry.input_artifacts)
        assert len(fetcher.queries) == 4
        assert Oracle(host.state_root / "state.sqlite", host.state_root / "artifacts").audit(run_id).ok
    finally:
        host.close()


def test_non_english_grounding_query_plan_broadens_before_grounding(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    topic = "面向 MELD 与 IEMOCAP 的通用多模态因果训练情绪识别"
    narrow_query = (
        '"emotion recognition in conversation" AND multimodal '
        'AND (causal OR temporal)'
    )
    broad_query = (
        '"emotion recognition" OR "affective computing" OR "speech emotion"'
    )

    class SparseThenBroadFetcher(_LiteratureFetcher):
        def __init__(self) -> None:
            super().__init__()
            self.sparse_queries = 0

        def search(self, query: LiteratureQuery) -> Iterable[SearchPaper]:
            papers = tuple(super().search(query))
            if query.query == narrow_query and self.sparse_queries == 0:
                self.sparse_queries += 1
                return tuple(
                    paper
                    for paper in papers
                    if paper.stratum is LITERATURE_STRATA[-1]
                )
            return papers

    class BroadenGroundingPlan:
        def __init__(self) -> None:
            self.delegate = FakeAgent()

        def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
            response = self.delegate.invoke(request)
            assert isinstance(response, AgentResponse)
            task = request.task_context
            if (
                isinstance(task, AuxiliaryTask)
                and task.kind is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN
            ):
                template = next(
                    item
                    for item in request.input_artifacts
                    if item.ref.endswith("--literature-query-template")
                )
                candidate = json.loads(template.content_bytes())
                retry = any(
                    item.ref.endswith("--candidate-validation-error")
                    for item in request.input_artifacts
                )
                candidate["query"] = broad_query if retry else narrow_query
                return _redigest(replace(response, typed_candidate=candidate))
            return response

    fetcher = SparseThenBroadFetcher()
    run_id = "non-english-grounding-replan-host"
    state_root = tmp_path / "state"
    host = WorkflowHost(state_root, literature_io=FixedLiteratureIO(fetcher))
    host.agent = BroadenGroundingPlan()  # type: ignore[assignment]
    try:
        host.initialize()
        payload = _payload(tmp_path, mount_root, run_id)
        contract = cast(dict[str, object], payload["research_contract"])
        goal = cast(dict[str, object], contract["goal"])
        goal["objective"] = (
            f"{topic}\n\n"
            "设计并验证共享核心机制的 causal-training 方法。"
        )
        goal["language"] = "zh-CN"
        created = host.handle(ControlRequest(
            1, f"create-{run_id}", 0, "CREATE_RUN", payload,
        ))
        assert created.status == "accepted", created

        _drive(host, run_id)

        attempts = [
            row
            for row in host.store.list_attempts()
            if row["run_id"] == run_id and _has_request(row)
        ]
        plans = sorted((
            row
            for row in attempts
            if (
                isinstance(_request(row).task_context, AuxiliaryTask)
                and cast(AuxiliaryTask, _request(row).task_context).kind
                is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN
            )
        ), key=lambda row: int(row["attempt_ordinal"]))
        groundings = [
            row
            for row in attempts
            if _request(row).command is AgentCommand.GROUNDING
        ]

        assert len(plans) == 2
        assert len({row["command_id"] for row in plans}) == 1
        assert [row["attempt_ordinal"] for row in plans] == [1, 2]
        assert [row["terminal_reason"] for row in plans] == ["FAILED", "SUCCEEDED"]
        for row in plans:
            request = _request(row)
            assert request.command is AgentCommand.LITERATURE_REVIEW
            assert isinstance(request.task_context, AuxiliaryTask)
            assert request.task_context.component_id is None
            assert request.task_context.expert_slot is None
            assert request.task_context.cycle is None

        first_failure = json.loads(str(plans[0]["observation_json"]))
        assert first_failure["failure_code"] == "invalid_candidate"
        validation = first_failure["diagnostics"]["validation_error"]
        assert validation["code"] == "invalid_candidate"
        assert "missing FOUNDATIONAL, INTERMEDIATE" in validation["message"]
        assert narrow_query in validation["message"]

        retry_request = _request(plans[1])
        retry_diagnostic = next(
            item
            for item in retry_request.input_artifacts
            if item.ref.endswith("--candidate-validation-error")
        )
        retry_payload = json.loads(retry_diagnostic.content_bytes())
        assert retry_diagnostic.ref == (
            f"{plans[0]['attempt_id']}--candidate-validation-error"
        )
        assert retry_payload == {
            "source_attempt_id": plans[0]["attempt_id"],
            "validation_error": validation,
            "rejected_candidate": {"query": narrow_query},
        }

        assert len(groundings) == 1
        assert groundings[0]["terminal_reason"] == "SUCCEEDED"
        grounding_request = _request(groundings[0])
        grounding_query = next(
            item
            for item in grounding_request.input_artifacts
            if item.ref.endswith("--literature-query")
        )
        successful_plan = json.loads(str(plans[1]["observation_json"]))[
            "response"
        ]["typed_candidate"]
        assert grounding_query.ref == f"{plans[1]['attempt_id']}--literature-query"
        assert json.loads(grounding_query.content_bytes()) == successful_plan
        assert successful_plan["query"] == broad_query
        assert successful_plan["filters"]["languages"] == ["en"]
        assert [query.query for query in fetcher.queries[:3]] == [
            narrow_query,
            broad_query,
            broad_query,
        ]

        debug_attempts = {
            item["attempt_id"]: item
            for item in build_debug_report(state_root, run_id)["attempts"]
        }
        assert debug_attempts[str(plans[0]["attempt_id"])][
            "recovered_by_later_attempt"
        ] is True
        second_trace = debug_attempts[str(plans[1]["attempt_id"])]
        assert second_trace["retry_of_attempt_id"] == plans[0]["attempt_id"]
        assert second_trace["retry_trigger"]["failure_code"] == "invalid_candidate"
        assert Oracle(
            state_root / "state.sqlite", state_root / "artifacts",
        ).audit(run_id).ok
    finally:
        host.close()


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("permanent_gap", [False, True])
def test_grounding_retrieval_coverage_gap_replans_query_before_grounding(
    tmp_path: Path,
    mount_root: Path,
    restart: bool,
    permanent_gap: bool,
) -> None:
    narrow_query = '"KV cache" AND compression AND "long context"'
    broad_query = '"KV cache" OR "key-value cache" OR transformer'

    class SparseGroundingRetrievalFetcher(_LiteratureFetcher):
        def search(self, query: LiteratureQuery) -> Iterable[SearchPaper]:
            papers = tuple(super().search(query))
            # The first coverage probe succeeds, while the subsequent
            # Grounding retrieval observes a sparse OpenAlex result set.
            if (
                query.query == narrow_query
                and sum(item.query == narrow_query for item in self.queries) == 2
            ):
                return tuple(
                    paper
                    for paper in papers
                    if paper.stratum is LITERATURE_STRATA[-1]
                )
            return papers

    class BroadenAfterGroundingGap:
        def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
            response = FakeAgent().invoke(request)
            assert isinstance(response, AgentResponse)
            task = request.task_context
            if (
                isinstance(task, AuxiliaryTask)
                and task.kind is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN
            ):
                template = next(
                    item
                    for item in request.input_artifacts
                    if item.ref.endswith("--literature-query-template")
                )
                candidate = json.loads(template.content_bytes())
                retry = any(
                    item.ref.endswith("--grounding-query-coverage-error")
                    for item in request.input_artifacts
                )
                candidate["query"] = broad_query if retry else narrow_query
                return _redigest(replace(response, typed_candidate=candidate,
                    token_usage={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
                    turn_count=1, cost="0.001"))
            return response

    class PersistentGapIO(FixedLiteratureIO):
        def retrieve(self, query):
            from hypoloom.literature import LiteratureCoverageError
            raise LiteratureCoverageError("missing FOUNDATIONAL, INTERMEDIATE", missing_strata=LITERATURE_STRATA[:2])

    fetcher = SparseGroundingRetrievalFetcher()
    adapter_type = PersistentGapIO if permanent_gap else FixedLiteratureIO
    run_id = "grounding-retrieval-coverage-replan"
    state_root = tmp_path / "state"
    host = WorkflowHost(state_root, literature_io=adapter_type(fetcher))
    host.agent = BroadenAfterGroundingGap()  # type: ignore[assignment]
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            1,
            f"create-{run_id}",
            0,
            "CREATE_RUN",
            _payload(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created

        if restart:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                host.tick()
                if any(json.loads(row["observation_json"]).get("literature_failure_class") == "LiteratureCoverageError"
                       for row in host.store.list_attempts()):
                    break
                time.sleep(.005)
            else:
                raise AssertionError("no durable coverage failure before restart")
            host.close()
            host = WorkflowHost(state_root, literature_io=adapter_type(fetcher))
            host.agent = BroadenAfterGroundingGap()
            host.initialize()
        _drive(host, run_id)

        attempts = [
            row
            for row in host.store.list_attempts()
            if row["run_id"] == run_id and _has_request(row)
        ]
        plans = sorted(
            (
                row
                for row in attempts
                if isinstance(_request(row).task_context, AuxiliaryTask)
                and cast(AuxiliaryTask, _request(row).task_context).kind
                is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN
            ),
            key=lambda row: int(row["attempt_ordinal"]),
        )
        groundings = [
            row for row in attempts
            if _request(row).command is AgentCommand.GROUNDING
        ]
        if permanent_gap:
            from hypoloom.agent_budget import fold_agent_attempts
            limit = host._research_contract(run_id).document["budget"]["max_retries"] + 1
            assert len(plans) == len(groundings) == limit
            assert all(row["terminal_reason"] == "SUCCEEDED" for row in plans)
            assert all(row["terminal_reason"] == "FAILED" for row in groundings)
            assert all(charge.tokens == charge.turns == charge.cost == 0 for charge in fold_agent_attempts(groundings))
            assert host._expert_final_failures(run_id)
            assert host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}
            assert all(row["state"] == "CLOSED" for row in host.store.list_attempts())
            assert Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id).ok
            return
        assert [row["terminal_reason"] for row in plans] == [
            "SUCCEEDED",
            "SUCCEEDED",
        ]
        assert [row["terminal_reason"] for row in groundings] == ["FAILED", "SUCCEEDED"]
        failure = json.loads(str(groundings[0]["observation_json"]))
        assert failure["failure_code"] == "invalid_candidate"
        assert "missing FOUNDATIONAL, INTERMEDIATE" in failure["literature_diagnostic"]
        assert "rejected_response" not in failure
        from hypoloom.agent_budget import fold_agent_attempts
        charges = fold_agent_attempts([*plans, groundings[0]])
        assert sum(charge.tokens for charge in charges) == 240
        assert sum(charge.turns for charge in charges) == 2
        assert float(sum(charge.cost for charge in charges)) == .002
        assert charges[-1].source == "pre_model"
        assert charges[-1].tokens == charges[-1].turns == charges[-1].cost == 0
        correction = next(
            item
            for item in _request(plans[1]).input_artifacts
            if item.ref.endswith("--grounding-query-coverage-error")
        )
        correction_payload = json.loads(correction.content_bytes())
        assert correction_payload["source_attempt_id"] == groundings[0]["attempt_id"]
        grounding_query = next(
            item
            for item in _request(groundings[1]).input_artifacts
            if item.ref.endswith("--literature-query")
        )
        assert json.loads(grounding_query.content_bytes())["query"] == broad_query
        assert [query.query for query in fetcher.queries[:4]] == [
            narrow_query,
            narrow_query,
            broad_query,
            broad_query,
        ]
        assert Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(
            run_id
        ).ok
    finally:
        host.close()


def test_exact_rejected_query_fails_before_probe_but_distinct_text_is_probed(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    rejected_query = '"dialogue emotion mechanism" AND multimodal'
    distinct_query = '"Dialogue emotion mechanism" AND multimodal'

    class SparseRejectedQueryFetcher(_LiteratureFetcher):
        def search(self, query: LiteratureQuery) -> Iterable[SearchPaper]:
            papers = tuple(super().search(query))
            if query.query != rejected_query:
                return papers
            return tuple(
                paper
                for paper in papers
                if paper.stratum is LITERATURE_STRATA[-1]
            )

    class RepeatThenChangeCase:
        def __init__(self) -> None:
            self.calls = 0
            self.delegate = FakeAgent()

        def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
            response = self.delegate.invoke(request)
            assert isinstance(response, AgentResponse)
            task = request.task_context
            if (
                isinstance(task, AuxiliaryTask)
                and task.kind is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN
            ):
                self.calls += 1
                template = next(
                    item
                    for item in request.input_artifacts
                    if item.ref.endswith("--literature-query-template")
                )
                candidate = json.loads(template.content_bytes())
                candidate["query"] = (
                    rejected_query if self.calls <= 2 else distinct_query
                )
                return _redigest(replace(response, typed_candidate=candidate))
            return response

    fetcher = SparseRejectedQueryFetcher()
    run_id = "grounding-query-plan-exact-rejection"
    host = WorkflowHost(
        tmp_path / "state",
        literature_io=FixedLiteratureIO(fetcher),
    )
    host.agent = RepeatThenChangeCase()  # type: ignore[assignment]
    try:
        host.initialize()
        payload = _payload(tmp_path, mount_root, run_id)
        contract = cast(dict[str, object], payload["research_contract"])
        budget = cast(dict[str, object], contract["budget"])
        budget["max_retries"] = 2
        created = host.handle(ControlRequest(
            1, f"create-{run_id}", 0, "CREATE_RUN", payload,
        ))
        assert created.status == "accepted", created

        _drive(host, run_id)

        plans = sorted((
            row
            for row in host.store.list_attempts()
            if row["run_id"] == run_id
            and _has_request(row)
            and isinstance(_request(row).task_context, AuxiliaryTask)
            and cast(AuxiliaryTask, _request(row).task_context).kind
            is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN
        ), key=lambda row: int(row["attempt_ordinal"]))
        assert [row["terminal_reason"] for row in plans] == [
            "FAILED",
            "FAILED",
            "SUCCEEDED",
        ]

        repeated_failure = json.loads(str(plans[1]["observation_json"]))
        repeated_validation = repeated_failure["diagnostics"]["validation_error"]
        assert repeated_failure["failure_code"] == "invalid_candidate"
        assert plans[0]["attempt_id"] in repeated_validation["message"]
        assert "exact" in repeated_validation["message"].lower()

        observed = [query.query for query in fetcher.queries]
        assert observed.count(rejected_query) == 1
        assert observed.count(distinct_query) == 2
        assert Oracle(
            host.state_root / "state.sqlite", host.state_root / "artifacts",
        ).audit(run_id).ok
    finally:
        host.close()


def test_non_english_grounding_query_plan_replans_after_host_restart(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    topic = "面向 MELD 与 IEMOCAP 的通用多模态因果训练情绪识别"
    broad_query = '"emotion recognition"'

    class SparseTopicFetcher(_LiteratureFetcher):
        def search(self, query: LiteratureQuery) -> Iterable[SearchPaper]:
            papers = tuple(super().search(query))
            if query.query == topic:
                return tuple(
                    paper
                    for paper in papers
                    if paper.stratum is LITERATURE_STRATA[-1]
                )
            return papers

    class PlanFromDurableDiagnostic:
        def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
            response = FakeAgent().invoke(request)
            assert isinstance(response, AgentResponse)
            task = request.task_context
            if (
                isinstance(task, AuxiliaryTask)
                and task.kind is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN
            ):
                template = next(
                    item
                    for item in request.input_artifacts
                    if item.ref.endswith("--literature-query-template")
                )
                candidate = json.loads(template.content_bytes())
                retry = any(
                    item.ref.endswith("--candidate-validation-error")
                    for item in request.input_artifacts
                )
                candidate["query"] = broad_query if retry else topic
                return _redigest(replace(response, typed_candidate=candidate))
            return response

    state_root = tmp_path / "state"
    run_id = "non-english-grounding-replan-restart"
    first_fetcher = SparseTopicFetcher()
    first = WorkflowHost(
        state_root, literature_io=FixedLiteratureIO(first_fetcher),
    )
    first.agent = PlanFromDurableDiagnostic()  # type: ignore[assignment]
    second: WorkflowHost | None = None
    try:
        first.initialize()
        payload = _payload(tmp_path, mount_root, run_id)
        contract = cast(dict[str, object], payload["research_contract"])
        goal = cast(dict[str, object], contract["goal"])
        goal["objective"] = topic
        goal["language"] = "zh-CN"
        created = first.handle(ControlRequest(
            1, f"create-{run_id}", 0, "CREATE_RUN", payload,
        ))
        assert created.status == "accepted", created

        deadline = time.monotonic() + 30
        failed_plan: dict[str, object] | None = None
        while time.monotonic() < deadline:
            first.tick()
            failed_plan = next((
                row
                for row in first.store.list_attempts()
                if row["run_id"] == run_id
                and _has_request(row)
                and isinstance(_request(row).task_context, AuxiliaryTask)
                and cast(AuxiliaryTask, _request(row).task_context).kind
                is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN
                and row["terminal_reason"] == "FAILED"
            ), None)
            if failed_plan is not None:
                break
        assert failed_plan is not None
        assert failed_plan["attempt_ordinal"] == 1
        assert [query.query for query in first_fetcher.queries] == [topic]
        assert not any(
            row["command_id"] == failed_plan["command_id"]
            and row["attempt_ordinal"] == 2
            for row in first.store.list_attempts()
        )
        assert not any(
            _has_request(row)
            and _request(row).command is AgentCommand.GROUNDING
            for row in first.store.list_attempts()
        )
        first.close()

        second_fetcher = _LiteratureFetcher()
        second = WorkflowHost(
            state_root, literature_io=FixedLiteratureIO(second_fetcher),
        )
        second.agent = PlanFromDurableDiagnostic()  # type: ignore[assignment]
        second.initialize()
        _drive(second, run_id)

        attempts = [
            row
            for row in second.store.list_attempts()
            if row["run_id"] == run_id and _has_request(row)
        ]
        plans = sorted((
            row
            for row in attempts
            if (
                isinstance(_request(row).task_context, AuxiliaryTask)
                and cast(AuxiliaryTask, _request(row).task_context).kind
                is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN
            )
        ), key=lambda row: int(row["attempt_ordinal"]))
        assert len(plans) == 2
        assert [row["terminal_reason"] for row in plans] == ["FAILED", "SUCCEEDED"]
        assert len({row["command_id"] for row in plans}) == 1
        assert plans[0]["attempt_id"] == failed_plan["attempt_id"]

        retry_request = _request(plans[1])
        diagnostic = next(
            item
            for item in retry_request.input_artifacts
            if item.ref.endswith("--candidate-validation-error")
        )
        diagnostic_payload = json.loads(diagnostic.content_bytes())
        assert diagnostic.ref == (
            f"{failed_plan['attempt_id']}--candidate-validation-error"
        )
        assert diagnostic_payload["source_attempt_id"] == failed_plan["attempt_id"]
        successful_plan = json.loads(str(plans[1]["observation_json"]))[
            "response"
        ]["typed_candidate"]
        assert successful_plan["query"] == broad_query

        grounding = next(
            row
            for row in attempts
            if _request(row).command is AgentCommand.GROUNDING
        )
        grounding_query = next(
            item
            for item in _request(grounding).input_artifacts
            if item.ref.endswith("--literature-query")
        )
        assert grounding["terminal_reason"] == "SUCCEEDED"
        assert grounding_query.ref == f"{plans[1]['attempt_id']}--literature-query"
        assert json.loads(grounding_query.content_bytes()) == successful_plan
        assert [query.query for query in second_fetcher.queries[:2]] == [
            broad_query,
            broad_query,
        ]
        assert Oracle(
            state_root / "state.sqlite", state_root / "artifacts",
        ).audit(run_id).ok
    finally:
        if second is not None:
            second.close()
        else:
            first.close()


def test_grounding_query_plan_missing_strata_fails_closed_without_retry_slot(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    topic = "面向 MELD 与 IEMOCAP 的通用多模态因果训练情绪识别"

    class SparseFetcher(_LiteratureFetcher):
        def search(self, query: LiteratureQuery) -> Iterable[SearchPaper]:
            return tuple(
                paper
                for paper in super().search(query)
                if paper.stratum is LITERATURE_STRATA[-1]
            )

    class NarrowGroundingPlan:
        def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
            response = FakeAgent().invoke(request)
            assert isinstance(response, AgentResponse)
            task = request.task_context
            if (
                isinstance(task, AuxiliaryTask)
                and task.kind is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN
            ):
                template = next(
                    item
                    for item in request.input_artifacts
                    if item.ref.endswith("--literature-query-template")
                )
                candidate = json.loads(template.content_bytes())
                candidate["query"] = topic
                return _redigest(replace(response, typed_candidate=candidate))
            return response

    fetcher = SparseFetcher()
    run_id = "grounding-query-plan-missing-strata"
    state_root = tmp_path / "state"
    host = WorkflowHost(state_root, literature_io=FixedLiteratureIO(fetcher))
    host.agent = NarrowGroundingPlan()  # type: ignore[assignment]
    try:
        host.initialize()
        payload = _payload(tmp_path, mount_root, run_id)
        contract = cast(dict[str, object], payload["research_contract"])
        goal = cast(dict[str, object], contract["goal"])
        goal["objective"] = topic
        goal["language"] = "zh-CN"
        budget = cast(dict[str, object], contract["budget"])
        budget["max_retries"] = 0
        created = host.handle(ControlRequest(
            1, f"create-{run_id}", 0, "CREATE_RUN", payload,
        ))
        assert created.status == "accepted", created

        _drive(host, run_id)

        attempts = [
            row
            for row in host.store.list_attempts()
            if row["run_id"] == run_id and _has_request(row)
        ]
        plans = [
            row
            for row in attempts
            if (
                isinstance(_request(row).task_context, AuxiliaryTask)
                and cast(AuxiliaryTask, _request(row).task_context).kind
                is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN
            )
        ]
        assert len(plans) == 1
        assert plans[0]["terminal_reason"] == "FAILED"
        failure = json.loads(str(plans[0]["observation_json"]))
        assert failure["failure_code"] == "invalid_candidate"
        assert failure["retry_class"] == "MODEL"
        assert "missing FOUNDATIONAL, INTERMEDIATE" in failure[
            "diagnostics"
        ]["validation_error"]["message"]
        assert [query.query for query in fetcher.queries] == [topic]
        assert all(
            _request(row).command is not AgentCommand.GROUNDING
            for row in attempts
        )
        assert not any(
            isinstance(_request(row).task_context, ResearchNodeTask)
            for row in attempts
        )
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
        failures = host.store.inspect(
            "SELECT payload_json FROM workflow_transitions WHERE run_id=? "
            "AND json_extract(payload_json,'$.agent_final_failure')=1",
            (run_id,),
        )
        assert len(failures) == 1
        assert json.loads(failures[0]["payload_json"])[
            "failed_task_command_id"
        ] == plans[0]["command_id"]
        final_debug = next(
            item
            for item in build_debug_report(state_root, run_id)["attempts"]
            if item["attempt_id"] == plans[0]["attempt_id"]
        )
        assert final_debug["final_failure_transition"] is True
        assert Oracle(
            state_root / "state.sqlite", state_root / "artifacts",
        ).audit(run_id).ok
    finally:
        host.close()


def test_sparse_query_coverage_replans_before_literature_main(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    class SparseThenBroadFetcher(_LiteratureFetcher):
        def search(self, query: LiteratureQuery) -> Iterable[SearchPaper]:
            papers = tuple(super().search(query))
            if query.query == (
                '"emotion recognition in conversation" AND multimodal '
                'AND (causal OR temporal)'
            ):
                return tuple(
                    paper
                    for paper in papers
                    if paper.stratum is LITERATURE_STRATA[-1]
                )
            return papers

    class NarrowThenBroadPlan:
        def __init__(self) -> None:
            self.calls = 0
            self.delegate = FakeAgent()

        def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
            response = self.delegate.invoke(request)
            assert isinstance(response, AgentResponse)
            task = request.task_context
            if (isinstance(task, ResearchNodeTask)
                    and task.key.subkind is ResearchNodeSubkind.QUERY_PLAN):
                self.calls += 1
                candidate = dict(response.typed_candidate or {})
                candidate["query"] = (
                    '"emotion recognition in conversation" AND multimodal '
                    'AND (causal OR temporal)'
                    if self.calls == 1
                    else (
                        '"emotion recognition" OR "affective computing" '
                        'OR "speech emotion"'
                    )
                )
                return _redigest(replace(response, typed_candidate=candidate))
            return response

    fetcher = SparseThenBroadFetcher()
    run_id = "literature-coverage-replan-host"
    host = WorkflowHost(tmp_path / "state", literature_io=FixedLiteratureIO(fetcher))
    host.agent = NarrowThenBroadPlan()  # type: ignore[assignment]
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            1, f"create-{run_id}", 0, "CREATE_RUN",
            _payload(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created

        _drive(host, run_id)

        rows = [
            row for row in host.store.list_attempts()
            if row["run_id"] == run_id
        ]
        plans = sorted((
            row for row in rows
            if _is_research_node_attempt(row, ResearchNodeSubkind.QUERY_PLAN)
        ), key=lambda row: int(row["attempt_ordinal"]))
        mains = sorted((
            row for row in rows
            if _is_research_node_attempt(row, ResearchNodeSubkind.MAIN)
        ), key=lambda row: int(row["attempt_ordinal"]))
        assert len(plans) == 2
        assert [row["terminal_reason"] for row in plans] == ["FAILED", "SUCCEEDED"]
        assert len(mains) == 1 and mains[0]["terminal_reason"] == "SUCCEEDED"

        first_failure = json.loads(str(plans[0]["observation_json"]))
        assert first_failure["failure_code"] == "invalid_candidate"
        validation = first_failure["diagnostics"]["validation_error"]
        assert validation["code"] == "invalid_candidate"
        assert "every requested stratum" in validation["message"]
        assert "missing FOUNDATIONAL, INTERMEDIATE" in validation["message"]
        assert (
            '"emotion recognition in conversation" AND multimodal '
            'AND (causal OR temporal)'
        ) in validation["message"]

        replan_request = _request(plans[1])
        diagnostic = next(
            item for item in replan_request.input_artifacts
            if item.ref.endswith("--candidate-validation-error")
        )
        diagnostic_payload = json.loads(diagnostic.content_bytes())
        assert diagnostic_payload["source_attempt_id"] == plans[0]["attempt_id"]
        assert diagnostic_payload["validation_error"] == validation

        second_query = next(
            item for item in _request(mains[0]).input_artifacts
            if item.ref.endswith("--literature-query")
        )
        assert second_query.ref == f"{plans[1]['attempt_id']}--literature-query"
        assert json.loads(second_query.content_bytes())["query"] == (
            '"emotion recognition" OR "affective computing" '
            'OR "speech emotion"'
        )
        assert [query.query for query in fetcher.queries] == [
            "Image classification",
            "Image classification",
            '"emotion recognition in conversation" AND multimodal '
            'AND (causal OR temporal)',
            '"emotion recognition" OR "affective computing" '
            'OR "speech emotion"',
            '"emotion recognition" OR "affective computing" '
            'OR "speech emotion"',
        ]
        assert Oracle(
            host.state_root / "state.sqlite", host.state_root / "artifacts",
        ).audit(run_id).ok
    finally:
        host.close()


def test_sparse_query_coverage_fails_cleanly_without_a_replan_slot(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    class SparseFetcher(_LiteratureFetcher):
        def search(self, query: LiteratureQuery) -> Iterable[SearchPaper]:
            papers = tuple(super().search(query))
            if query.query == '"narrow dialogue mechanism"':
                return tuple(
                    paper for paper in papers
                    if paper.stratum is LITERATURE_STRATA[-1]
                )
            return papers

    class NarrowPlan:
        def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
            response = FakeAgent().invoke(request)
            assert isinstance(response, AgentResponse)
            task = request.task_context
            if (isinstance(task, ResearchNodeTask)
                    and task.key.subkind is ResearchNodeSubkind.QUERY_PLAN):
                candidate = dict(response.typed_candidate or {})
                candidate["query"] = '"narrow dialogue mechanism"'
                return _redigest(replace(response, typed_candidate=candidate))
            return response

    fetcher = SparseFetcher()
    run_id = "literature-coverage-no-replan-host"
    host = WorkflowHost(tmp_path / "state", literature_io=FixedLiteratureIO(fetcher))
    host.agent = NarrowPlan()  # type: ignore[assignment]
    try:
        host.initialize()
        payload = _payload(tmp_path, mount_root, run_id)
        contract = cast(dict[str, object], payload["research_contract"])
        budget = cast(dict[str, object], contract["budget"])
        budget["max_retries"] = 0
        created = host.handle(ControlRequest(
            1, f"create-{run_id}", 0, "CREATE_RUN", payload,
        ))
        assert created.status == "accepted", created

        _drive(host, run_id)

        rows = [
            row for row in host.store.list_attempts()
            if row["run_id"] == run_id
        ]
        plans = [
            row for row in rows
            if _is_research_node_attempt(row, ResearchNodeSubkind.QUERY_PLAN)
        ]
        mains = [
            row for row in rows
            if _is_research_node_attempt(row, ResearchNodeSubkind.MAIN)
        ]
        assert len(plans) == 1 and plans[0]["terminal_reason"] == "FAILED"
        assert mains == []
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
        assert [query.query for query in fetcher.queries] == [
            "Image classification", "Image classification",
            '"narrow dialogue mechanism"',
        ]
        failures = host.store.inspect(
            "SELECT payload_json FROM workflow_transitions WHERE run_id=? "
            "AND json_extract(payload_json,'$.agent_final_failure')=1",
            (run_id,),
        )
        assert len(failures) == 1
        assert json.loads(failures[0]["payload_json"])[
            "failed_task_command_id"
        ] == plans[0]["command_id"]
        assert Oracle(
            host.state_root / "state.sqlite", host.state_root / "artifacts",
        ).audit(run_id).ok
    finally:
        host.close()


def test_sparse_query_coverage_replans_after_host_restart(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    class SparseThenBroadFetcher(_LiteratureFetcher):
        def search(self, query: LiteratureQuery) -> Iterable[SearchPaper]:
            papers = tuple(super().search(query))
            if query.query == '"narrow dialogue mechanism"':
                return tuple(
                    paper for paper in papers
                    if paper.stratum is LITERATURE_STRATA[-1]
                )
            return papers

    class PlanByOrdinal:
        def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
            response = FakeAgent().invoke(request)
            assert isinstance(response, AgentResponse)
            task = request.task_context
            if (isinstance(task, ResearchNodeTask)
                    and task.key.subkind is ResearchNodeSubkind.QUERY_PLAN):
                candidate = dict(response.typed_candidate or {})
                candidate["query"] = (
                    '"narrow dialogue mechanism"'
                    if request.attempt_ordinal == 1
                    else '"broad emotion recognition"'
                )
                return _redigest(replace(response, typed_candidate=candidate))
            return response

    fetcher = SparseThenBroadFetcher()
    state_root = tmp_path / "state"
    run_id = "literature-coverage-restart-host"
    first = WorkflowHost(state_root, literature_io=FixedLiteratureIO(fetcher))
    first.agent = PlanByOrdinal()  # type: ignore[assignment]
    second: WorkflowHost | None = None
    try:
        first.initialize()
        created = first.handle(ControlRequest(
            1, f"create-{run_id}", 0, "CREATE_RUN",
            _payload(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 30
        failed_plan: dict[str, object] | None = None
        while time.monotonic() < deadline:
            first.tick()
            failed_plan = next((
                row for row in first.store.list_attempts()
                if row["run_id"] == run_id
                and _is_research_node_attempt(row, ResearchNodeSubkind.QUERY_PLAN)
                and row["terminal_reason"] == "FAILED"
            ), None)
            if failed_plan is not None:
                break
        assert failed_plan is not None
        assert not any(
            row["attempt_ordinal"] == 2
            for row in first.store.list_attempts()
            if row["command_id"] == failed_plan["command_id"]
            and _has_request(row)
        )
        first.close()

        second = WorkflowHost(state_root, literature_io=FixedLiteratureIO(fetcher))
        second.agent = PlanByOrdinal()  # type: ignore[assignment]
        second.initialize()
        _drive(second, run_id)

        plans = sorted((
            row for row in second.store.list_attempts()
            if row["run_id"] == run_id
            and _is_research_node_attempt(row, ResearchNodeSubkind.QUERY_PLAN)
        ), key=lambda row: int(row["attempt_ordinal"]))
        assert len(plans) == 2
        retry = _request(plans[1])
        assert any(
            item.ref == f"{failed_plan['attempt_id']}--candidate-validation-error"
            for item in retry.input_artifacts
        )
        assert Oracle(
            state_root / "state.sqlite", state_root / "artifacts",
        ).audit(run_id).ok
    finally:
        if second is not None:
            second.close()
        else:
            first.close()


def test_query_plan_probe_defers_transport_failure_to_literature_main(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    fetcher = _LiteratureFetcher(
        fail_calls=(3,), failure_code=LiteratureFailureCode.OPENALEX_TIMEOUT,
    )
    run_id = "literature-plan-probe-transport"
    host = WorkflowHost(tmp_path / "state", literature_io=FixedLiteratureIO(fetcher))
    try:
        host.initialize()
        created = host.handle(ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN",
            _payload(tmp_path, mount_root, run_id)))
        assert created.status == "accepted", created
        _drive(host, run_id)
        rows = [row for row in host.store.list_attempts() if row["run_id"] == run_id]
        plans = [row for row in rows
            if _is_research_node_attempt(row, ResearchNodeSubkind.QUERY_PLAN)]
        mains = [row for row in rows
            if _is_research_node_attempt(row, ResearchNodeSubkind.MAIN)]
        assert len(plans) == len(mains) == 1
        assert plans[0]["terminal_reason"] == mains[0]["terminal_reason"] == "SUCCEEDED"
        assert len(fetcher.queries) == 4
        assert Oracle(host.state_root / "state.sqlite", host.state_root / "artifacts").audit(run_id).ok
    finally:
        host.close()


@pytest.mark.parametrize(
    ("code", "http_status", "main_attempts", "fetch_calls"),
    (
        (LiteratureFailureCode.OPENALEX_TIMEOUT, None, 2, 5),
        (LiteratureFailureCode.PDF_TIMEOUT, None, 2, 5),
        (LiteratureFailureCode.QUERY_INVALID, 400, 1, 4),
    ),
)
def test_main_retries_only_transient_failures_with_the_same_closed_plan(
    tmp_path: Path,
    mount_root: Path,
    code: LiteratureFailureCode,
    http_status: int | None,
    main_attempts: int,
    fetch_calls: int,
    monkeypatch,
) -> None:
    fetcher = _LiteratureFetcher(fail_calls=(4,), failure_code=code, http_status=http_status)
    run_id = f"literature-main-{code.value.lower()}"
    host = WorkflowHost(tmp_path / "state", literature_io=FixedLiteratureIO(fetcher))
    try:
        host.initialize()
        created = host.handle(ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN",
            _payload(tmp_path, mount_root, run_id)))
        assert created.status == "accepted", created
        if code is LiteratureFailureCode.OPENALEX_TIMEOUT:
            from hypoloom import literature_recovery
            original_status = literature_recovery.status
            # Observe the actual wait first, then advance only its observation
            # clock, preserving task/run wall limits and original query identity.
            def after_cooldown(store, run_id, **kwargs):
                current = original_status(store, run_id)
                return original_status(store, run_id, now=max(time.time(), current.get("next_retry_at") or 0))
            monkeypatch.setattr(literature_recovery, "status", after_cooldown)
        _drive(host, run_id)
        rows = [row for row in host.store.list_attempts() if row["run_id"] == run_id]
        plan = next(row for row in rows
            if _is_research_node_attempt(row, ResearchNodeSubkind.QUERY_PLAN))
        mains = sorted((row for row in rows
            if _is_research_node_attempt(row, ResearchNodeSubkind.MAIN)),
            key=lambda row: int(row["attempt_ordinal"]))
        planned = json.loads(str(plan["observation_json"]))["response"]["typed_candidate"]
        assert len(mains) == main_attempts and len(fetcher.queries) == fetch_calls
        for row in mains:
            query = next(item for item in _request(row).input_artifacts
                if item.ref.endswith("--literature-query"))
            assert query.ref == f"{plan['attempt_id']}--literature-query"
            assert json.loads(query.content_bytes()) == planned
        failed = json.loads(str(mains[0]["observation_json"]))
        assert failed["failure_code"] == f"literature_{code.value.lower()}"
        if http_status is not None:
            assert failed["literature_http_status"] == http_status
        assert Oracle(host.state_root / "state.sqlite", host.state_root / "artifacts").audit(run_id).ok
    finally:
        host.close()


def test_real_expert_loop_rejects_create_without_real_literature_adapter(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    host = WorkflowHost(
        tmp_path / "state",
        claude_agent_host=cast(object, _QualifiedClaudeStub()),  # type: ignore[arg-type]
        claude_model="exact-model-v1",
    )
    try:
        host.initialize()
        run_id = "real-missing-literature"
        response = host.handle(ControlRequest(
            schema_version=1, request_id=f"create-{run_id}", expected_revision=0,
            command="CREATE_RUN", payload=_payload(tmp_path, mount_root, run_id),
        ))
        assert response.status == "rejected"
        assert response.error is not None
        assert "literature" in response.error["message"]
        assert host.store.get_run(run_id) is None
    finally:
        host.close()


def test_literature_query_refs_pass_through_under_bound() -> None:
    refs = ("goal", "config", "fact-1")
    assert bounded_context_refs(refs, ("fact-1",)) == refs


def test_literature_query_refs_drop_oldest_facts_first() -> None:
    base = ("goal", "config", "skill")
    facts = tuple(f"fact-{index}" for index in range(MAX_CONTEXT_REFS + 10))
    bounded = bounded_context_refs(base + facts, facts)
    assert len(bounded) == MAX_CONTEXT_REFS
    assert bounded[: len(base)] == base
    assert "fact-0" not in bounded
    assert bounded[len(base):] == facts[len(facts) - (MAX_CONTEXT_REFS - len(base)):]


def _append_synthetic_hypotheses(
    host: WorkflowHost, run_id: str, component_id: str, count: int,
) -> None:
    goal = host.store.list_facts(run_id, "GoalDefined")[0]
    component = host._research_contract(run_id).component(component_id)
    assert component is not None
    with host.store.transaction() as tx:
        for index in range(count):
            fact = HypothesisProposed(
                fact_id=f"{run_id}--fact-synthetic-{index}",
                run_id=run_id,
                hypothesis_id=f"{run_id}--synthetic-hypothesis-{index}",
                goal_id=goal["goal_id"],
                component_id=component_id,
                proposer_role=component.owner_role,
                method_family_id="synthetic-family",
                claim=f"Bounded reference fixture hypothesis {index}",
            )
            payload = fact_to_dict(fact)
            assert tx.append_fact({
                **payload,
                "subject_id": fact.hypothesis_id,
                "payload": payload,
            })


def _query_template_refs(inputs: tuple[EmbeddedInput, ...]) -> tuple[str, ...]:
    return LiteratureQuery.from_dict(json.loads(next(
        item.content_bytes() for item in inputs
        if item.ref.endswith("--literature-query-template")))).immutable_context_refs


def test_grounding_query_plan_template_refs_stay_bounded_as_facts_accumulate(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    adapter = FixedLiteratureIO(_LiteratureFetcher(), max_pdf_pages=20)
    run_id = "literature-refs-bounded-grounding"
    host = WorkflowHost(tmp_path / "state", literature_io=adapter)
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1, request_id=f"create-{run_id}", expected_revision=0,
            command="CREATE_RUN", payload=_payload(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created
        component_id = "primary.validation_accuracy"
        _append_synthetic_hypotheses(
            host, run_id, component_id, MAX_CONTEXT_REFS + 10)
        inputs = host._expert_inputs(run_id, AuxiliaryTask(
            kind=AuxiliaryRequestKind.GROUNDING_QUERY_PLAN, run_id=run_id),
            component_id, "ml-researcher", None)
        refs = _query_template_refs(inputs)
        assert len(refs) <= MAX_CONTEXT_REFS
        assert f"{run_id}--fact-goal" in refs
        assert f"{run_id}--fact-synthetic-{MAX_CONTEXT_REFS + 9}" in refs
        assert f"{run_id}--fact-synthetic-0" not in refs
    finally:
        host.close()


def test_expert_query_plan_template_refs_stay_bounded_as_facts_accumulate(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    adapter = FixedLiteratureIO(_LiteratureFetcher(), max_pdf_pages=20)
    run_id = "literature-refs-bounded-expert"
    host = WorkflowHost(tmp_path / "state", literature_io=adapter)
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1, request_id=f"create-{run_id}", expected_revision=0,
            command="CREATE_RUN", payload=_payload(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            host.tick()
            if any(
                row["kind"] == "AGENT" and row["state"] == "CLOSED"
                and _has_request(row)
                and _request(row).command is AgentCommand.GROUNDING
                for row in host.store.list_attempts()
            ):
                break
            time.sleep(0.005)
        else:
            raise AssertionError("grounding did not complete")
        component_id = "primary.validation_accuracy"
        _append_synthetic_hypotheses(
            host, run_id, component_id, MAX_CONTEXT_REFS + 10)
        task = ResearchNodeTask(key=ResearchNodeKey(
            run_id=run_id, component_id=component_id, expert_slot="ml-researcher",
            cycle=1, node_kind=ResearchNodeKind.LITERATURE_REVIEW,
            subkind=ResearchNodeSubkind.QUERY_PLAN))
        inputs = host._expert_inputs(run_id, task, component_id, "ml-researcher", None)
        refs = _query_template_refs(inputs)
        assert len(refs) <= MAX_CONTEXT_REFS
        assert f"{run_id}--fact-goal" in refs
        assert f"{run_id}--fact-synthetic-{MAX_CONTEXT_REFS + 9}" in refs
        assert f"{run_id}--fact-synthetic-0" not in refs
    finally:
        host.close()
