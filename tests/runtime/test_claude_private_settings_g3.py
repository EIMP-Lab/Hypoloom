"""One native-CC/local-provider CPU closure after its settings source vanishes."""
from __future__ import annotations

import fcntl
import json
from contextlib import ExitStack
from pathlib import Path

from hypoloom.host_settings import prepare_host_settings
from hypoloom.workflow_host import WorkflowHost

import test_full_cc_host_g3 as host_fixture
from test_claude_measurement_g3 import _DeclaredScenario, _run_real_claude_measurement_g3


def test_native_successors_use_private_settings_after_original_source_is_deleted(tmp_path, monkeypatch):
    """Reuse the existing native scenario; no external endpoint or credentials.

    Its original Host factory supplies only a loopback provider and synthetic
    key. Bind those through settings instead of provider_environment, before
    the real WorkflowHost computes its immutable version, under controller.lock.
    All original measurement/Oracle/process-cleanup assertions remain in force.
    """
    original_source = tmp_path / "settings.json"
    captured = {}

    class DeleteSourceScenario(_DeclaredScenario):
        removed_after_attempt: str | None = None

        def record(self, document):
            number = super().record(document)
            if self.removed_after_attempt is None:
                assert captured["host"].private_settings_path == captured["private"]
                assert not captured["host"]._provider_environment
                assert original_source.is_file()
                original_source.unlink()
                self.removed_after_attempt = self.current.attempt_id
            return number

    with ExitStack() as locks:
        def private_host(state_root: Path, **options):
            state_root.mkdir(mode=0o700)
            lock = locks.enter_context((state_root / "controller.lock").open("a+"))
            (state_root / "controller.lock").chmod(0o600)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locks.callback(fcntl.flock, lock, fcntl.LOCK_UN)
            adapter = options["claude_agent_host"]
            assert adapter.settings_path == original_source
            environment = options["provider_environment"]
            assert environment["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1:")
            assert environment["ANTHROPIC_API_KEY"].startswith("host-only-declared-g3-")
            original_source.write_text(json.dumps({"env": environment}))
            original_source.chmod(0o600)
            private = prepare_host_settings(state_root, original_source)
            assert private == state_root / "host/claude-settings.json"
            captured["private_bytes"] = private.read_bytes()
            captured["private"] = private
            adapter.settings_path = private
            options["provider_environment"] = {}
            options["private_settings_path"] = private
            host = WorkflowHost(state_root, **options)
            captured["host"] = host
            return host

        # Only intercept this existing test fixture's constructor call, not
        # production serve, fingerprinting, relay, Agent or Process execution.
        monkeypatch.setattr(host_fixture, "WorkflowHost", private_host)
        scenario = _run_real_claude_measurement_g3(tmp_path, "six-yaml-v4",
            scenario_factory=DeleteSourceScenario)

    assert not original_source.exists()
    assert captured["private"].read_bytes() == captured["private_bytes"]
    assert scenario.removed_after_attempt is not None
    successor_attempts = set(scenario.agent_requests) - {scenario.removed_after_attempt}
    assert len(successor_attempts) >= 2
    assert "cpu_outcome" in scenario.parser_evidence
    assert not scenario.errors
    report = json.loads((tmp_path / "oracle.json").read_text())
    assert report["ok"] and report["open_attempt_count"] == report["pending_effect_count"] == 0
    (tmp_path / "private-settings-native-evidence.json").write_text(json.dumps({
        "source_removed_after_native_attempt": scenario.removed_after_attempt,
        "native_successor_attempt_ids": sorted(successor_attempts),
        "private_bytes_unchanged": True,
        "provider_environment_empty": True,
        "oracle_ok": report["ok"],
        "cpu_outcome_observed": True,
    }, indent=2))
