"""Opt-in bounded native reviewer samples; no scientific-quality guarantee."""
from dataclasses import replace
from decimal import Decimal
import json
import os
import stat

import pytest

from hypoloom.agent_aci import AgentBudget, EmbeddedInput, validate_postflight
from hypoloom.agent_sessions import AgentSessions
from hypoloom.artifacts import ArtifactPublisher
from hypoloom.claude_agent_host import ClaudeAgentHost, CLAUDE_AGENT_HOST_VERSION, CLAUDE_AGENT_TOOL_PERMISSION_PROFILE
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import default_request
from test_real_claude_qualification import _configured_path, _assert_process_groups_gone


@pytest.mark.skipif(os.environ.get("HYPOLOOM_REAL_CLAUDE") != "1" or os.environ.get("HYPOLOOM_REAL_VERIFIER_G5") != "1",
                   reason="requires explicit paid verifier opt-in, exact model/settings and cumulative/prior cost")
def test_real_reviewer_detects_dispatch_defect_and_accepts_novel_probe(tmp_path):
    executable = _configured_path("HYPOLOOM_CLAUDE_EXECUTABLE")
    settings = _configured_path("HYPOLOOM_CLAUDE_SETTINGS")
    assert stat.S_IMODE(settings.stat().st_mode) == 0o600
    model = os.environ["HYPOLOOM_CLAUDE_MODEL"]
    limit = Decimal(os.environ["HYPOLOOM_G5_CUMULATIVE_LIMIT_USD"])
    charged = Decimal(os.environ["HYPOLOOM_G5_PRIOR_CHARGED_USD"])
    assert 0 < limit <= 4 and 0 <= charged < limit
    adapter = ClaudeAgentHost(claude_executable=executable, settings_path=settings)
    adapter.preflight(model=model)
    source = """import math
class BaseLoss:
    def __call__(self, batch): return self.forward(batch)
    def forward(self, batch): return sum(sum(row) for row in batch)
class NewLoss(BaseLoss):
    def forwards(self, batch):
        return sum(sum(math.log1p(x*x) for x in row) for row in batch)/len(batch)
def train_step(batch): return NewLoss()(batch)
"""
    description = "train_step is required to compute the mean over nonempty rows of sum(log1p(x*x)), for finite floats. This tiny CPU implementation is a novel algorithm; no old algorithm is a reference. Inspect actual dispatch and numerical correctness."
    groups = []
    sessions = AgentSessions(tmp_path / "state", ArtifactPublisher(tmp_path / "state/artifacts"))
    try:
        for index, code in enumerate((source, source.replace("def forwards(", "def forward("))):
            assert charged + 2 <= limit, "insufficient remaining invocation reservation; do not reset the cohort"
            prefix = f"verifier-sample-{index}"
            def embedded(suffix, value):
                return EmbeddedInput.from_bytes(prefix + suffix, "application/json", json.dumps(value).encode())
            inputs = (embedded("--expert-config", {"node_runtime": {"model": model}}),
                embedded("--verification-context", {"phase": "inspect", "workspace_paths": ["candidate.py"],
                    "max_probe_seconds": 120, "finding_refs": [], "open_blocker_refs": [],
                    "argv": ["python", "candidate.py"], "implementation_description": description,
                    "prior_reports": [], "probe_evidence": None}),
                embedded("--implementation-source", {"candidate.py": code}))
            request = replace(default_request(AgentCommand.VERIFY_IMPLEMENTATION),
                attempt_id=prefix, role="verifier", component_id="verifier", owner_workflow_id="run-fake",
                input_artifacts=inputs, immutable_context_refs=tuple(i.ref for i in inputs),
                required_model_identity=model, required_adapter_version=CLAUDE_AGENT_HOST_VERSION,
                tool_permission_profile=CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
                budget=AgentBudget(max_turns=8, max_tokens=100_000, max_output_bytes=131_072,
                    wall_seconds=180, max_cost="2", token_enforcement_mode="observe"))
            workspace = sessions.prepare(request, None)
            assert workspace.binding["parent_session_id"] is None
            handle = adapter.start(request, model=model, provider_environment={}, tool_workspace=workspace,
                bind_process_group=lambda binding: groups.append(binding.process_group_id))
            try: observation = adapter.wait(handle)
            finally:
                adapter.cleanup(handle)
                workspace.cleanup()
            response = observation.response
            cost = Decimal(response.cost) if response is not None else Decimal("2")
            charged += cost
            (tmp_path / (prefix + ".json")).write_text(json.dumps({"model": model, "terminal_reason": observation.terminal_reason.value,
                "failure_code": observation.failure_code, "cost_or_reservation_usd": str(cost), "charged_cumulative_usd": str(charged),
                "process_group_id": observation.process_group_id, "response": None if response is None else response.to_dict()}, indent=2))
            assert response is not None, (observation.terminal_reason, observation.failure_code)
            validate_postflight(request, response)
            report = response.typed_candidate
            if index == 0:
                assert report["verdict"] == "changes_required", report
                assert any(f["severity"] == "blocker" and "forward" in f["message"].lower() for f in report["findings"]), report
            else:
                assert report["verdict"] == "probe_required", report
                assert not any(f["severity"] == "blocker" for f in report["findings"]), report
            assert charged <= limit
    finally:
        _assert_process_groups_gone(groups)
        adapter.close_diagnostics()
