"""Opt-in DESIGN-only model probe; all other native CC nodes are scripted.

This is a fresh mixed-provider compatibility fixture, not source-run replay,
scientific goal completion, or G5. The default companion has two loopback providers and makes
no paid call. Production Host owns admission, retry, sessions and all effects.
"""

from __future__ import annotations

import copy
import json
import os
import secrets
import stat
import tempfile
import time
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pytest
import yaml

from hypoloom.agent_budget import fold_agent_attempts
from hypoloom.claude_agent_host import (
    ClaudeAgentHost,
    process_group_exists,
    validate_claude_model_selector,
)
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.oracle import Oracle
from hypoloom.profile_admission import admit_profile

from test_claude_file_tool_cli import _installed_claude
from test_claude_measurement_g3 import _NoPublicWeb
from test_claude_shared_acceptance_g3 import (
    _AcceptanceScenario,
    _GOAL,
    _local_measurement,
    _payload,
)
from test_full_cc_host_g3 import _MODEL, _new_host, _request_from_provider, _start_provider


_DESIGN = AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT
_CORRECTION_FIELDS = {"owner_role", "method_family_id", "acceptance_criteria", "evaluator_version"}
_DESIGN_FIELDS = {
    "component_id", "owner_role", "method_family_id", "method", "metrics",
    "acceptance_criteria", "evaluator_version", "artifact_requirements",
    "resource_preference", "measurement", "execution_argv", "evaluation_scope",
}


def _paid_enabled(environment=None):
    values = os.environ if environment is None else environment
    return (values.get("HYPOLOOM_REAL_DESIGN_RECOVERY") == "1"
            and values.get("HYPOLOOM_REAL_CLAUDE") == "1")


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")
    path.chmod(0o600)


class _DesignRouter:
    """Test-only dispatch; each real adapter owns its handles and credentials."""

    def __init__(self, local, design, design_environment, design_model):
        self.local = local
        self.design = design
        self.design_environment = dict(design_environment)
        self.design_model = design_model
        self.routes = []
        self.owners = {}

    def __getattr__(self, name):
        return getattr(self.local, name)

    def start(self, request, *, model, provider_environment, **kwargs):
        selected = request.command is _DESIGN
        assert model == (self.design_model if selected else _MODEL)
        adapter = self.design if selected else self.local
        environment = self.design_environment if selected else provider_environment
        self.routes.append({"attempt_id": request.attempt_id, "command": request.command.value,
                            "route": "design" if selected else "local", "model": model})
        handle = adapter.start(request, model=model, provider_environment=environment, **kwargs)
        self.owners[request.attempt_id] = adapter
        return handle

    def _owner(self, handle):
        identity = handle if isinstance(handle, str) else handle.request.attempt_id
        return self.owners[identity]

    def poll(self, handle):
        return self._owner(handle).poll(handle)

    def cancel(self, handle):
        return self._owner(handle).cancel(handle)

    def cleanup(self, handle):
        return self._owner(handle).cleanup(handle)

    def service_tools(self):
        local = self.local.service_tools()
        return self.design.service_tools() or local


class _NonDesignScenario(_AcceptanceScenario):
    """Submit the Host-supplied accepted DESIGN, never our predicted design."""

    def __init__(self, root, nonce):
        super().__init__(root, nonce)
        self.accepted_designs = {}

    def record(self, document):
        request = _request_from_provider(document)
        assert request.command is not _DESIGN, "DESIGN reached the non-DESIGN provider"
        if request.command is AgentCommand.EXPERIMENT_EXECUTION:
            designs = []
            for item in request.input_artifacts:
                if not item.ref.endswith("--response") or item.media_type != "application/json":
                    continue
                value = json.loads(item.content_bytes())
                if value.get("command") == _DESIGN.value and value.get("status") == "SUCCEEDED":
                    designs.append(value)
            assert len(designs) == 1, "execution must receive exactly one accepted DESIGN"
            selected = designs[0]
            assert selected["typed_candidate"]["owner_role"] == request.role
            assert set(selected["typed_candidate"]) == _DESIGN_FIELDS
            self.designs[request.role] = copy.deepcopy(selected["typed_candidate"])
            self.accepted_designs[selected["attempt_id"]] = copy.deepcopy(selected["typed_candidate"])
        return super().record(document)


class _LocalDesignScenario(_AcceptanceScenario):
    """Reproduce full→partial schema failure, then let Host resume and recover."""

    def __init__(self, root, nonce):
        super().__init__(root, nonce)
        self.failed_attempt = None
        self.output_calls = []
        self.output_counts = Counter()

    def record(self, document):
        request = _request_from_provider(document)
        assert request.command is _DESIGN, "a non-DESIGN node reached the DESIGN provider"
        source = next(item for item in request.input_artifacts if item.ref.endswith("--shared-acceptance"))
        self.source_attempt_id = json.loads(source.content_bytes())["source_attempt_id"]
        if self.failed_attempt is None:
            self.failed_attempt = request.attempt_id
        return super().record(document)

    def tool_calls(self, number):
        result = []
        for identity, tool, arguments in super().tool_calls(number):
            arguments = copy.deepcopy(arguments)
            if tool == "StructuredOutput":
                request = self.current
                self.output_counts[request.attempt_id] += 1
                ordinal = self.output_counts[request.attempt_id]
                identity = f"{identity}-{ordinal}"
                candidate = arguments["typed_candidate"]
                if request.attempt_id == self.failed_attempt:
                    if ordinal == 1:
                        for key in _CORRECTION_FIELDS:
                            candidate.pop(key)
                        candidate.update(measurement=None, metrics=[])
                    else:
                        arguments["typed_candidate"] = {
                            key: candidate[key] for key in _CORRECTION_FIELDS}
                else:
                    # Distinguishes the accepted actual output from the other
                    # provider's default fixture proposal during MCP submission.
                    candidate["method"] += " Accepted only after full-candidate reconstruction."
                self.output_calls.append({"attempt_id": request.attempt_id,
                                          "arguments": copy.deepcopy(arguments)})
            result.append((identity, tool, arguments))
        return tuple(result)


def _probe_payload(root, mount, run_id, design_model, node_cost, *, paid):
    # Reuse tiny source/data preparation, then admit the final six documents
    # before CREATE_RUN. No source-run contracts or history are imported.
    _payload(root, mount, roles=(_GOAL,))
    profile = root / "profile"
    methodology = yaml.safe_load((profile / "methodology.yaml").read_text())
    methodology["output_correction"] = {"max_failed_validations": 3}
    methodology["budget"]["max_retries"] = 2
    if paid:
        # First actual DESIGN repaired its files and produced a schema-valid
        # candidate, but CLI reported 27 turns against the small 24-turn probe.
        # The user authorizes broader monitored testing: widen only this fresh
        # test allocation; never override Host budget validation or old results.
        methodology["budget"]["agent_invocation"].update(
            max_turns=64, max_tokens=100_000, max_output_bytes=1_048_576,
            wall_seconds=300, max_cost=str(node_cost))
    experts = yaml.safe_load((profile / "experts.yaml").read_text())
    experts["experts"][0]["stage_overrides"] = {"inner_loop.code": {"model": design_model}}
    goal = yaml.safe_load((profile / "goal.yaml").read_text())
    goal["description"] += (
        " This is a bounded CPU compatibility fixture, not original research. "
        "Read validation.json from the admitted data root; use the candidate keyed by your exact role. "
        "Write predictions.json containing suites.suite_one and suites.suite_two, each with candidate, "
        "labels and predictions, plus diagnostic with labels and predictions. Preserve the supplied "
        "arrays. The accepted common evaluator and non-null local measurement remain independent. "
        "Author and test the actual experiment and local parser; no downloads or installations are needed.")
    safety = yaml.safe_load((profile / "safety.yaml").read_text())
    if paid:
        # Explicit finite allocation includes three declared DESIGN reservations
        # and room for the zero-cost scripted prerequisite/closure Attempts.
        safety["per_expert_budget_usd"] = safety["total_budget_usd"] = float(node_cost * 3 + 5)
    for name, document in (("methodology", methodology), ("experts", experts), ("goal", goal), ("safety", safety)):
        (profile / f"{name}.yaml").write_text(yaml.safe_dump(document))
    admission = admit_profile(profile, run_id=run_id, allow_gpu=False)
    assert admission.admitted, admission.to_dict()
    _write_json(root / "profile-admission.json", admission.to_dict())
    return admission.create_run_payload


def _paid_adapter():
    # Called only after both explicit opt-ins; no settings/credentials are read
    # during collection or the entirely local companion.
    try:
        cost = Decimal(os.environ["HYPOLOOM_DESIGN_RECOVERY_MAX_COST"])
    except (KeyError, InvalidOperation) as exc:
        raise ValueError("set an explicitly approved positive DESIGN_RECOVERY_MAX_COST") from exc
    if not cost.is_finite() or cost <= 0:
        raise ValueError("DESIGN_RECOVERY_MAX_COST must be finite and positive")
    executable = Path(os.environ["HYPOLOOM_CLAUDE_EXECUTABLE"])
    settings = Path(os.environ["HYPOLOOM_CLAUDE_SETTINGS"])
    if (not executable.is_absolute() or executable.is_symlink() or not executable.is_file()
            or not os.access(executable, os.X_OK)):
        raise ValueError("use the absolute resolved Claude executable")
    if (not settings.is_absolute() or settings.is_symlink() or not settings.is_file()
            or stat.S_IMODE(settings.stat().st_mode) != 0o600):
        raise ValueError("use the approved mode-0600 Claude settings file")
    model = validate_claude_model_selector(os.environ["HYPOLOOM_CLAUDE_MODEL"])
    adapter = ClaudeAgentHost(claude_executable=executable, settings_path=settings)
    adapter.preflight(model=model, provider_environment={})
    return adapter, model, cost


def _run_probe(root, *, design_adapter=None, design_model=_MODEL, node_cost=Decimal("0.5")):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    assert not (root / "state").exists(), "probe requires an absent fresh state root"
    paid = design_adapter is not None
    run_id = "design-recovery-" + secrets.token_hex(8)
    nonce = "design-recovery-fixture-" + secrets.token_hex(12)
    local = _NonDesignScenario(root, nonce)
    local_key = "local-only-" + secrets.token_hex(16)
    local_server, local_thread = _start_provider(local, local_key)
    design_scenario = None
    design_server = design_thread = None
    host = None
    evidence = {"kind": "mixed_provider_design_compatibility_probe", "paid_design": paid,
                "g5": False, "scientific_success": False, "gpu": False, "run_id": run_id,
                "internal_failed_validation_cap": 3, "host_model_retries": 2,
                "design_node_max_cost": str(node_cost), "design_accepted": False,
                "natural_cpu_closure": False}
    try:
        settings = root / "settings.json"
        settings.write_text("{}\n")
        settings.chmod(0o600)
        executable = design_adapter.claude_executable if paid else _installed_claude()
        host = _new_host(root, executable, f"http://127.0.0.1:{local_server.server_port}", local_key)
        design_environment = {}
        if not paid:
            design_scenario = _LocalDesignScenario(root, nonce)
            design_key = "design-local-only-" + secrets.token_hex(16)
            design_server, design_thread = _start_provider(design_scenario, design_key)
            design_environment = {"ANTHROPIC_API_KEY": design_key,
                                  "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{design_server.server_port}"}
            design_adapter = ClaudeAgentHost(claude_executable=executable, settings_path=settings)
        router = _DesignRouter(host.claude_agent_host, design_adapter, design_environment, design_model)
        host.claude_agent_host = router
        host.grounding_web = _NoPublicWeb()
        # Sources live outside /tmp, which the Process reserves for scratch.
        mount = Path(tempfile.mkdtemp(prefix="prom-design-probe-", dir="/var/tmp"))
        evidence["fixture_root"] = str(mount)
        payload = _probe_payload(root, mount, run_id, design_model, node_cost, paid=paid)
        host.initialize()
        created = host.handle(ControlRequest(1, run_id + "-create", 0, "CREATE_RUN", payload))
        assert created.status == "accepted", created
        deadline = time.monotonic() + (1020 if paid else 240)
        while time.monotonic() < deadline:
            host.tick()
            errors = local.errors + ([] if design_scenario is None else design_scenario.errors)
            if errors:
                evidence["fixture_errors"] = errors
                break
            if host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(0.01)
        attempts = host.store.list_attempts()
        agents = [(row, json.loads(row["observation_json"])) for row in attempts if row["kind"] == "AGENT"]
        designs = [(row, observed) for row, observed in agents if observed["request"]["command"] == _DESIGN.value]
        accepted = [(row, observed) for row, observed in designs if row["terminal_reason"] == "SUCCEEDED"]
        assert len(accepted) <= 1
        evidence["design_accepted"] = bool(accepted)
        evidence["routes"] = router.routes
        evidence["design_attempts"] = [{"attempt_id": row["attempt_id"], "state": row["state"],
            "terminal_reason": row["terminal_reason"], "retry_class": observed.get("retry_class"),
            "session": observed.get("session"), "diagnostics": observed.get("diagnostics")}
            for row, observed in designs]
        evidence["design_outputs"] = [] if design_scenario is None else design_scenario.output_calls
        evidence["accepted_designs_used_by_execution"] = local.accepted_designs
        if accepted:
            row, observed = accepted[0]
            candidate = observed["response"]["typed_candidate"]
            assert set(candidate) == _DESIGN_FIELDS
            assert candidate["measurement"] == _local_measurement().to_dict()
            assert candidate["metrics"] == [candidate["measurement"]["metric"]]
            if local.accepted_designs:
                assert local.accepted_designs == {row["attempt_id"]: candidate}
        charges = fold_agent_attempts(attempts)
        evidence["charges"] = [{"attempt_id": charge.attempt_id, "source": charge.source,
                                "cost": None if charge.cost is None else str(charge.cost)} for charge in charges]
        head = dict(host.store.latest_transition(run_id))
        evidence["head"] = head
        evidence["open_attempt_ids"] = [row["attempt_id"] for row in attempts if row["state"] == "OPEN"]
        evidence["process_attempts"] = [{key: row[key] for key in
            ("attempt_id", "state", "terminal_reason", "process_group_id")} for row in attempts if row["kind"] == "PROCESS"]
        audit = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
        evidence["oracle"] = audit.to_dict()
        evidence["natural_cpu_closure"] = (bool(accepted) and head["state"] == "COMPLETED"
            and not evidence["open_attempt_ids"] and audit.ok and len(evidence["process_attempts"]) == 1
            and evidence["process_attempts"][0]["terminal_reason"] == "SUCCEEDED")
        evidence["diagnostics_root"] = str(root / "state/diagnostics/agents" / run_id)
        evidence["local_request_commands"] = sorted({r.command.value for r in local.agent_requests.values()})
        evidence["design_request_commands"] = ([] if design_scenario is None else
            sorted({r.command.value for r in design_scenario.agent_requests.values()}))
    finally:
        groups = [] if host is None or not host.store.database_path.exists() else [
            row["process_group_id"] for row in host.store.list_attempts()
            if row["process_group_id"] is not None]
        if host is not None:
            host.close()
        evidence["owned_process_residue_after_close"] = [pid for pid in groups if process_group_exists(pid)]
        _write_json(root / "design-recovery-probe.json", evidence)
        local_server.shutdown()
        local_server.server_close()
        local_thread.join(timeout=5)
        if design_server is not None:
            design_server.shutdown()
            design_server.server_close()
            design_thread.join(timeout=5)
    return evidence


def test_probe_requires_both_explicit_paid_optins():
    assert not _paid_enabled({})
    assert not _paid_enabled({"HYPOLOOM_REAL_CLAUDE": "1"})
    assert not _paid_enabled({"HYPOLOOM_REAL_DESIGN_RECOVERY": "1"})
    assert _paid_enabled({"HYPOLOOM_REAL_CLAUDE": "1", "HYPOLOOM_REAL_DESIGN_RECOVERY": "1"})


def test_preparation_failure_keeps_original_error_without_querying_absent_database(tmp_path, monkeypatch):
    from types import SimpleNamespace

    events = []
    database = tmp_path / "state/state.sqlite"

    def unexpected_query():
        raise AssertionError("uninitialized database must not be queried")

    def preparation_failure(*args, **kwargs):
        raise FileNotFoundError("missing-profile-template")

    host = SimpleNamespace(
        claude_agent_host=SimpleNamespace(),
        store=SimpleNamespace(database_path=database, list_attempts=unexpected_query),
        close=lambda: events.append("host-close"),
    )
    server = SimpleNamespace(server_port=1, shutdown=lambda: events.append("shutdown"),
                             server_close=lambda: events.append("server-close"))
    thread = SimpleNamespace(join=lambda timeout: events.append("join"))
    monkeypatch.setattr(f"{__name__}._start_provider", lambda *args: (server, thread))
    monkeypatch.setattr(f"{__name__}._new_host", lambda *args: host)
    monkeypatch.setattr(f"{__name__}._installed_claude", lambda: tmp_path / "unused-claude")
    monkeypatch.setattr(f"{__name__}._probe_payload", preparation_failure)
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **kwargs: str(tmp_path / "unused-mount"))
    with pytest.raises(FileNotFoundError, match="missing-profile-template"):
        _run_probe(tmp_path)
    assert not database.exists()
    assert events.count("host-close") == 1
    assert events.count("shutdown") == events.count("server-close") == events.count("join") == 2
    evidence = json.loads((tmp_path / "design-recovery-probe.json").read_text())
    assert evidence["owned_process_residue_after_close"] == []
    assert not evidence["design_accepted"]


def test_paid_profile_preparation_needs_no_provider_or_credentials(tmp_path):
    root = tmp_path / "probe"
    root.mkdir()
    mount = tmp_path / "mount"
    mount.mkdir()
    payload = _probe_payload(root, mount, "paid-profile-preparation",
                             "mimo-v2.5-pro", Decimal("2"), paid=True)
    contract = payload["research_contract"]
    product = contract["methodology"]["controls"]["product"]
    assert product["methodology"]["output_correction"] == {"max_failed_validations": 3}
    assert contract["budget"]["max_retries"] == 2
    assert contract["budget"]["agent_invocation"]["max_cost"] == "2"
    assert contract["budget"]["agent_invocation"]["max_turns"] == 64
    assert contract["budget"]["agent_invocation"]["max_tokens"] == 100_000
    assert contract["budget"]["agent_invocation"]["wall_seconds"] == 300
    assert product["experts"]["defaults"]["model"] == _MODEL
    assert product["experts"]["experts"][0]["stage_overrides"] == {
        "inner_loop.code": {"model": "mimo-v2.5-pro"}}
    assert not payload["execution"]["allow_gpu"]
    assert not (root / "state").exists()


def test_local_design_only_route_recovers_same_session_then_closes_cpu(tmp_path):
    evidence = _run_probe(tmp_path)
    assert not evidence.get("fixture_errors"), evidence.get("fixture_errors")
    assert evidence["design_accepted"] and evidence["natural_cpu_closure"], evidence
    assert not evidence["owned_process_residue_after_close"]
    assert _DESIGN.value not in evidence["local_request_commands"]
    assert evidence["design_request_commands"] == [_DESIGN.value]
    failed, recovered = evidence["design_attempts"]
    assert [failed["terminal_reason"], recovered["terminal_reason"]] == ["FAILED", "SUCCEEDED"]
    assert failed["retry_class"] == "MODEL"
    session = failed["session"]["session_id"]
    assert recovered["session"]["session_id"] != session
    assert recovered["session"]["branch"] == failed["session"]["branch"]
    assert recovered["session"]["launch_arguments"] == ["--session-id", recovered["session"]["session_id"]]
    assert Counter(item["attempt_id"] for item in evidence["design_outputs"]) == {
        failed["attempt_id"]: 3, recovered["attempt_id"]: 1}
    assert all(item["route"] == ("design" if item["command"] == _DESIGN.value else "local")
               for item in evidence["routes"])


@pytest.mark.skipif(not _paid_enabled(), reason="DESIGN-only paid probe requires both explicit opt-ins and approved auth/model/cost")
def test_real_design_only_recovery_probe(tmp_path):
    adapter, model, cost = _paid_adapter()
    evidence = _run_probe(tmp_path, design_adapter=adapter, design_model=model, node_cost=cost)
    assert not evidence["owned_process_residue_after_close"]
    assert evidence["design_accepted"], "DESIGN did not recover; inspect fresh design-recovery-probe.json and raw diagnostics"
