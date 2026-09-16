"""Opt-in G5 canary for the ACI-8 expert loop and Git authoring adapter."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

import pytest

from hypoloom.agent_aci import AgentCommand
from hypoloom.claude_agent_host import (
    CLAUDE_AGENT_HOST_VERSION,
    ClaudeAgentHost,
    validate_claude_model_selector,
)
from hypoloom.control_protocol import ControlRequest
from hypoloom.literature import (
    LITERATURE_STRATA,
    FetchResponse,
    FixedLiteratureIO,
    LiteratureQuery,
    PdfExtraction,
    ScholarlySourceClass,
    SearchPaper,
)
from hypoloom.oracle import Oracle
from hypoloom.profile_admission import PROFILE_FILES, admit_profile
from hypoloom.workflow_host import WorkflowHost


_ENABLE = "HYPOLOOM_REAL_EXPERT_LOOP"
_EXECUTABLE = "HYPOLOOM_CLAUDE_EXECUTABLE"
_SETTINGS = "HYPOLOOM_CLAUDE_SETTINGS"
_MODEL = "HYPOLOOM_CLAUDE_MODEL"
_REPOSITORY = Path(__file__).resolve().parents[2]
_REAL_ONLY = pytest.mark.skipif(
    os.environ.get(_ENABLE) != "1",
    reason=f"set {_ENABLE}=1 with an exact Claude executable/settings/model",
)


def _configured_file(name: str, *, executable: bool = False) -> Path:
    raw = os.environ.get(name)
    assert raw, f"{name} is required when {_ENABLE}=1"
    path = Path(raw)
    assert path.is_absolute() and not path.is_symlink() and path.is_file()
    if executable:
        assert os.access(path, os.X_OK)
    return path


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", *arguments), cwd=repository, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


class _DeterministicOpenAlexFixture:
    """Keep G5 about real CC; the live OpenAlex boundary has its own tests."""

    configuration = {
        "adapter": "openalex",
        "host": "api.openalex.org",
        "timeout_seconds": 1.0,
        "max_response_bytes": 4_096,
    }

    def preflight(self) -> dict[str, object]:
        return {"adapter": "openalex", "network_probe_performed": False}

    def search(self, query: LiteratureQuery) -> Iterable[SearchPaper]:
        latest = date.fromisoformat(query.latest_as_of).year
        for index, (stratum, year) in enumerate(zip(
            LITERATURE_STRATA, (latest - 25, latest - 15, latest), strict=True,
        ), 1):
            yield SearchPaper(
                bibliographic_identity=f"openalex:W{index}",
                title=f"Bounded G5 literature fixture {index}",
                authors=("Hypoloom Qualification",),
                year=year,
                language="en",
                landing_uri=f"https://catalog.example.org/works/{index}",
                source_uri=f"https://catalog.example.org/records/{index}",
                source_class=ScholarlySourceClass.JOURNAL,
                stratum=stratum,
                pdf_uri=None,
            )

    def resolve(self, hostname: str) -> Sequence[str]:
        assert hostname == "catalog.example.org"
        return ("8.8.8.8",)

    def fetch(self, uri: str, *, approved_addresses: tuple[str, ...],
              max_bytes: int) -> FetchResponse:
        raise AssertionError("G5 fixture has no PDF")

    def extract_pdf(self, content: bytes, *, max_pages: int) -> PdfExtraction:
        raise AssertionError("G5 fixture has no PDF")


@pytest.fixture
def mount_root() -> Path:
    with tempfile.TemporaryDirectory(
        prefix="hypoloom-expert-real-claude-", dir="/var/tmp",
    ) as raw:
        yield Path(raw)


def _payload(tmp_path: Path, mount_root: Path, run_id: str) -> dict[str, object]:
    target = mount_root / "target"
    target.mkdir()
    (target / "README.md").write_text(
        "Implement one deterministic bounded validation pilot. The generated "
        "experiment must write the exact requested result envelope and artifacts.\n",
        encoding="utf-8",
    )
    _git(target, "init", "-q")
    _git(target, "add", ".")
    _git(target, "-c", "user.name=Hypoloom G5", "-c",
         "user.email=hypoloom@example.invalid", "commit", "-qm", "base")
    data = mount_root / "data"
    data.mkdir()
    (data / "validation.json").write_text(
        json.dumps({"labels": [0, 1, 1, 0], "predictions": [0, 1, 1, 0]}),
        encoding="utf-8",
    )
    profile = tmp_path / "profile"
    profile.mkdir()
    source = _REPOSITORY / "tests" / "fixtures" / "profiles" / "expert-loop-pilot"
    for name in PROFILE_FILES:
        text = (source / name).read_text(encoding="utf-8")
        text = text.replace("/REPLACE_ME/expert-loop-pilot-target", str(target))
        text = text.replace("/REPLACE_ME/expert-loop-pilot-data", str(data))
        (profile / name).write_text(text, encoding="utf-8")
    report = admit_profile(profile, run_id=run_id)
    assert report.admitted and report.create_run_payload is not None, report.to_dict()
    return report.create_run_payload


def _drive(host: WorkflowHost, run_id: str) -> None:
    deadline = time.monotonic() + 1_800
    while time.monotonic() < deadline:
        host.tick()
        head = host.store.latest_transition(run_id)
        if head is not None and head["state"] in {
            "COMPLETED", "FAILED", "CANCELLED",
        }:
            return
        time.sleep(0.02)
    raise AssertionError(f"real expert-loop canary timed out: {host.store.latest_transition(run_id)}")


@_REAL_ONLY
def test_real_claude_closes_one_complete_expert_cycle_in_a_git_worktree(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    executable = _configured_file(_EXECUTABLE, executable=True)
    settings = _configured_file(_SETTINGS)
    assert stat.S_IMODE(settings.stat().st_mode) == 0o600
    configured_model = os.environ.get(_MODEL)
    assert configured_model
    model = validate_claude_model_selector(configured_model)
    adapter = ClaudeAgentHost(
        claude_executable=executable,
        settings_path=settings,
    )
    adapter.preflight(model=model)
    literature = FixedLiteratureIO(_DeterministicOpenAlexFixture())
    run_id = "expert-loop-aci8-real-claude"
    state_root = tmp_path / "state"
    host = WorkflowHost(
        state_root,
        claude_agent_host=adapter,
        claude_model=model,
        literature_io=literature,
    )
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1, request_id=f"create-{run_id}", expected_revision=0,
            command="CREATE_RUN", payload=_payload(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created
        _drive(host, run_id)
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
        requests = [
            json.loads(row["observation_json"])["request"]
            for row in host.store.list_attempts()
            if row["run_id"] == run_id and row["kind"] == "AGENT"
            and row["terminal_reason"] == "SUCCEEDED"
        ]
        commands = [request["command"] for request in requests]
        for required in (
            AgentCommand.GROUNDING,
            AgentCommand.LITERATURE_REVIEW,
            AgentCommand.HYPOTHESIS_PROPOSAL,
            AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
            AgentCommand.RESULT_ANALYSIS,
            AgentCommand.HYPOTHESIS_UPDATE,
        ):
            assert required.value in commands
        assert commands.count(AgentCommand.HYPOTHESIS_PROPOSAL.value) >= 3
        literature_tasks = [
            request["task_context"]["key"]
            for request in requests
            if request["command"] == AgentCommand.LITERATURE_REVIEW.value
            and request["task_context"]["kind"] == "RESEARCH_NODE"
        ]
        literature_cycles = {int(task["cycle"]) for task in literature_tasks}
        assert literature_cycles
        assert {
            (int(task["cycle"]), str(task["subkind"]))
            for task in literature_tasks
        } == {
            (cycle, subkind)
            for cycle in literature_cycles
            for subkind in ("QUERY_PLAN", "MAIN")
        }
        assert len(literature_tasks) == 2 * len(literature_cycles)
        assert host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        assert host.store.list_facts(run_id, "HypothesisAssessed")
        assert not host.running and not host.running_agents
        report = Oracle(
            state_root / "state.sqlite", state_root / "artifacts",
        ).audit(run_id)
        assert report.ok, report.to_json()
        assert report.open_attempt_count == 0
        assert report.pending_effect_count == 0
        assert report.owned_process_residue == ()
        worktree_root = state_root / "git-authoring-worktrees"
        assert not worktree_root.exists() or not tuple(worktree_root.iterdir())
        print(json.dumps({
            "adapter_version": CLAUDE_AGENT_HOST_VERSION,
            "agent_aci_version": 9,
            "commands": commands,
            "model_identity": model,
            "oracle_ok": report.ok,
            "run_state": "COMPLETED",
        }, sort_keys=True, separators=(",", ":")))
    finally:
        host.close()
