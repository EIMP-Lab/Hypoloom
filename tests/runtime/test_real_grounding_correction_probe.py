"""Explicit single native Grounding invocation; not WorkflowHost/Oracle/G5.

Only the localhost scripted companion runs under pytest. A paid caller must
explicitly supply its approved adapter, model, environment, runtime and remaining
allocation. There is no outer retry, experiment, GPU, settings discovery or resume.
"""

from __future__ import annotations

import copy
import json
import secrets
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from hypoloom.agent_aci import (
    CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION,
    AgentACIError, AgentBudget, EmbeddedInput, request_digest, validate_postflight, with_output_digest,
)
from hypoloom.agent_budget import charged_cost, fold_agent_attempts
from hypoloom.claude_agent_host import (
    CLAUDE_AGENT_HOST_VERSION, ClaudeAgentHost, _output_schema, process_group_exists,
)
from hypoloom.full_cc_runtime import full_cc_environment, structured_output_validation_cap
from hypoloom.native_output import decode_output
from hypoloom.reference_aliases import canonical_output
from test_agent_sessions import _manager
from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results, native_output_schema, native_prompt
from test_full_cc_adapter import _MODEL, _request as native_request
from test_full_cc_host_g3 import _request_from_provider, _start_provider
from test_git_authoring_workspace import git, make_target
from test_grounding_acceptance_aci import ACCEPTANCE, acceptance_exchange
from test_real_current_host_g5 import _native_tools


NODE_BUDGET = AgentBudget(max_turns=32, max_tokens=100_000,
    max_output_bytes=128 * 1024, wall_seconds=300, max_cost="2", token_enforcement_mode="observe")
INSTRUCTION = (
    "This is a single ordinary GROUNDING background investigation of a fresh disposable target, "
    "not scientific qualification or permission to execute an experiment. Read README.md and validation.json. "
    "The later resource canary has a fixed checksum >= 6 metric for the supplied values [1, 2, 3]; "
    "do not alter the metric or data. Do not run CUDA, training, experiments, installs or downloads. "
    "The metric is already declared; common acceptance was not commissioned. Return acceptance: null "
    "and clarification: null with the observed seven-field background. Do not author an evaluator. "
    "No public web retrieval is in scope; return web_sources: [] and describe missing evidence honestly. "
    "Use tools as needed, then return one complete JSON object matching the supplied schema, with no "
    "prose preamble, Markdown fences or schema keywords. The shared_goal completion mode does not "
    "change the common-acceptance commission boundary."
)


def test_request_preserves_current_schema_scope_budget_and_correction_cap():
    request = _request("fresh-grounding-offline", _MODEL, NODE_BUDGET)
    assert request.schema_version == CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION
    assert request.required_adapter_version == CLAUDE_AGENT_HOST_VERSION
    assert request.run_id == request.owner_workflow_id == request.task_context.run_id
    assert request.command_id == request.task_context.command_id
    assert request.attempt_id.startswith(request.run_id)
    assert request.attempt_ordinal == 1 and request.budget == NODE_BUDGET
    assert request.required_output_schema == "GroundingCandidate/v3"
    assert structured_output_validation_cap(request) == 3
    environment = full_cc_environment(structured_output_failed_validation_cap=3)
    assert environment["CLAUDE_CODE_STOP_HOOK_BLOCK_CAP"] == "3"
    assert environment["MAX_STRUCTURED_OUTPUT_RETRIES"] == "3"
    config = json.loads(request.input_artifacts[0].content_bytes())
    assert "evaluation" not in config["goal"]
    assert config["goal"]["metric"] == {"name": "checksum", "direction": "maximize", "target": 6}
    assert config["node_runtime"]["output_correction"] == {"max_failed_validations": 3}
    assert config["node_runtime"]["safety"]["bash"]["network_allowed"] is False
    assert "acceptance: null" in config["goal"]["description"]


def test_current_grounding_schema_and_postflight_remain_strict():
    request = _request("fresh-grounding-postflight", _MODEL, NODE_BUDGET)
    _, template = acceptance_exchange(shared=False)
    candidate = {**template.typed_candidate, "web_sources": []}
    response = with_output_digest(replace(template, schema_version=request.schema_version,
        command_id=request.command_id, attempt_id=request.attempt_id,
        declared_effects=tuple(replace(item, command_id=request.command_id,
            attempt_id=request.attempt_id) for item in template.declared_effects),
        input_digest=request_digest(request), adapter_version=request.required_adapter_version,
        model_identity=request.required_model_identity, typed_candidate=candidate))
    assert validate_postflight(request, response).candidate.acceptance is None
    invalid = with_output_digest(replace(response,
        typed_candidate={**candidate, "acceptance": ACCEPTANCE}))
    with pytest.raises(AgentACIError, match="only completed commissioned Grounding"):
        validate_postflight(request, invalid)
    for malformed in ('{"typed_candidate":{}}', 'prefix {"typed_candidate":{}}'):
        with pytest.raises(ValueError):
            decode_output(malformed, _output_schema(request))


@pytest.mark.parametrize("allocation", [Decimal("1.999999"), Decimal("0"), Decimal("NaN")])
def test_unfunded_probe_never_launches_or_creates_state(tmp_path, allocation, monkeypatch):
    adapter = ClaudeAgentHost(claude_executable="/never-launch")
    monkeypatch.setattr(adapter, "start", lambda *_a, **_k: pytest.fail("unfunded launch"))
    with pytest.raises(ValueError, match="allocation"):
        run_probe(tmp_path, adapter=adapter, model=_MODEL, provider_environment={},
            runtime=Path(sys.prefix), allocation_remaining_usd=allocation)
    assert list(tmp_path.iterdir()) == []


class _CorrectionScenario:
    def __init__(self, corrected=True):
        self.corrected = corrected
        self.requests, self.errors, self.tool_results, self.outputs, self.feedback = [], [], {}, [], []

    def select(self, request):
        self.request = request
        _, template = acceptance_exchange(shared=False)
        candidate = copy.deepcopy(template.typed_candidate)
        candidate.update(web_sources=[], supporting_refs=list(request.immutable_context_refs))
        candidate["background"] = {
            "project_summary": "Disposable checksum resource-canary background only.",
            "codebase_map": [{"path": "README.md", "purpose": "bounded task and restrictions"},
                {"path": "validation.json", "purpose": "fixed checksum inputs"}],
            "entrypoints": [], "how_to_run_experiment": "No execution is authorized in this probe.",
            "metrics_available": ["checksum"], "risks": ["No GPU or external evidence was collected."],
            "open_questions": [],
        }
        self.output = {"typed_candidate": candidate,
            "declared_effects": [{"effect": effect} for effect in request.effect_requirements]}

    def record(self, document):
        self.requests.append(document)
        try:
            assert _request_from_provider(document).to_dict() == self.request.to_dict()
            assert native_output_schema(document) == _output_schema(self.request)
            if len(self.requests) > 1:
                self.tool_results.update(_tool_results(document))
            if len(self.requests) in {4, 5}:
                feedback = next(block["text"] for message in reversed(document["messages"])
                    if message.get("role") == "user" for block in message.get("content", [])
                    if block.get("type") == "text" and block.get("text", "").startswith("Stop hook feedback:"))
                assert "/typed_candidate" in feedback
                assert "return one complete JSON object matching the full output schema" in feedback
                self.feedback.append(feedback)
        except (AssertionError, KeyError, ValueError, StopIteration) as exc:
            self.errors.append(str(exc))
        return len(self.requests)

    def tool_calls(self, step):
        if self.errors:
            return ()
        if step == 1:
            return (("read-background", "Read", {"file_path": "/workspace/README.md"}),)
        if step == 2:
            script = (
                "import errno,json,os\nfrom pathlib import Path\n"
                "assert json.loads(Path('/workspace/validation.json').read_text())['values']==[1,2,3]\n"
                "assert os.environ['CLAUDE_CODE_STOP_HOOK_BLOCK_CAP']=='3'\n"
                "assert os.environ['MAX_STRUCTURED_OUTPUT_RETRIES']=='3'\n"
                "try:\n fd=os.open('/workspace/README.md',os.O_WRONLY|os.O_APPEND)\n"
                "except OSError as exc:\n assert exc.errno==errno.EROFS\n"
                "else:\n os.close(fd); raise AssertionError('target writable')\n"
                "print('CAP3_READONLY_NO_EXECUTION')\n"
            )
            return (("check-boundary", "Bash", {"command": "python3 - <<'PY'\n" + script + "PY",
                "timeout": 10000}),)
        output = copy.deepcopy(self.output)
        if step == 3:
            output["typed_candidate"] = "not an object"
        elif step == 4 or step == 5 and not self.corrected:
            del output["typed_candidate"]["background"]
        elif step != 5:
            return ()
        self.outputs.append(copy.deepcopy(output))
        return ((f"grounding-final-{step}", "StructuredOutput", output),)


@pytest.mark.parametrize("corrected", [True, False], ids=["third-output-valid", "three-output-rejections"])
def test_local_native_grounding_corrects_twice_in_one_attempt_and_archives_all_usage(tmp_path, corrected):
    scenario = _CorrectionScenario(corrected)
    key = "local-grounding-correction-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    progress = []
    try:
        result = run_probe(tmp_path, adapter=ClaudeAgentHost(claude_executable=_installed_claude()),
            model=_MODEL, provider_environment={"ANTHROPIC_API_KEY": key,
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{provider.server_port}"},
            runtime=Path(sys.prefix), allocation_remaining_usd=Decimal("6.652944"),
            scenario=scenario, progress_callback=progress.append)
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
    assert not scenario.errors and len(scenario.requests) == 5 and len(scenario.feedback) == 2
    assert result["observation"]["terminal_reason"] == ("SUCCEEDED" if corrected else "INVALID_OUTPUT")
    assert result["observation"]["failure_code"] == (None if corrected else "invalid_structured_output")
    if corrected:
        assert result["observation"]["response"]["typed_candidate"]["acceptance"] is None
        assert result["observation"]["response"]["typed_candidate"] == scenario.outputs[-1]["typed_candidate"]
        assert validate_postflight(scenario.request, result["observation"]["response"]).candidate.acceptance is None
    else:
        assert "response" not in result["observation"]
    correction = result["observation"]["diagnostics"]["structured_output_correction"]
    assert correction["validation_source"] == "host_stop_hook"
    assert correction["status"] == ("recovered" if corrected else "unresolved")
    assert correction["call_count"] == 3 and correction["rejected_count"] == (2 if corrected else 3)
    assert [item["status"] for item in correction["rounds"]] == ["rejected", "rejected",
        "accepted" if corrected else "rejected"]
    session = result["session"]
    assert session["launch_arguments"] == ["--session-id", session["session_id"]]
    assert session["transcript_complete"] is True
    archive = tmp_path / "state/artifacts" / session["transcript"]["path"] / "transcript.jsonl"
    content = archive.read_bytes()
    assert b"not an object" in content and b"grounding-final" not in content
    records = [json.loads(line) for line in content.splitlines()]
    outputs = [json.loads(block["text"]) for record in records if record.get("type") == "assistant"
        for block in record["message"]["content"] if block.get("type") == "text"]
    aliases = native_prompt(scenario.requests[0])["ref_aliases"]
    assert aliases == {"R1": scenario.request.immutable_context_refs[0]}
    assert [canonical_output(output, aliases) for output in outputs] == scenario.outputs
    assert b"CAP3_READONLY_NO_EXECUTION" in content
    diagnostic_root = Path(result["cli_stdout"]).parent
    validations = [json.loads(path.read_text()) for path in sorted((diagnostic_root / "native-output").glob("*.json"))]
    assert [json.loads(item["last_assistant_message"]) for item in validations] == outputs
    assert {item["session_id"] for item in validations} == {session["session_id"]}
    assert json.loads((diagnostic_root / "request.json").read_text()) == result["request"]
    assert len(list((diagnostic_root / "provider").glob("*/request.body"))) == 5
    assert result["process_group_exists_after_cleanup"] is False
    charge, = result["charges"]
    assert charge["source"] == ("response" if corrected else "provider") and Decimal(charge["cost"]) > 0
    assert Decimal(charge["cost"]) == Decimal(str(result["cli_result"]["total_cost_usd"]))
    assert charge["turns"] == result["cli_result"]["num_turns"]
    assert charge["tokens"] == 10
    assert result["cli_result"]["usage"]["input_tokens"] == 5
    assert result["cli_result"]["usage"]["output_tokens"] == 5
    assert result["unknown_reservation_usd"] == "0"
    assert result["invocations"] == 1 and result["paid_model"] is False and result["gpu"] is False
    assert "CAP3_READONLY_NO_EXECUTION" in _result_text(scenario.tool_results["check-boundary"])
    assert progress and any(item["tool_calls"] >= 2 for item in progress)
    assert all(set(item) <= {"attempt_id", "state", "elapsed_seconds", "tool_calls", "tool_results",
        "last_tool", "last_progress_seconds", "charged_usd", "unknown_reservation_usd"} for item in progress)
    assert key not in json.dumps(result) and key not in json.dumps(progress)


def _request(run_id, model, budget):
    base, _ = acceptance_exchange(shared=False)
    task = replace(base.task_context, run_id=run_id)
    config = EmbeddedInput.from_bytes(run_id + "--grounding-config", "application/json",
        json.dumps({"goal": {"description": INSTRUCTION,
            "metric": {"name": "checksum", "direction": "maximize", "target": 6}},
            "node_runtime": {"output_correction": {"max_failed_validations": 3},
                "safety": {"bash": {"network_allowed": False}}}}).encode())
    return replace(native_request(base, "grounding-correction", None),
        schema_version=CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION,
        run_id=run_id, owner_workflow_id=run_id, task_context=task, command_id=task.command_id,
        attempt_id=run_id + "--agent-1", attempt_ordinal=1,
        required_model_identity=model, budget=budget, input_artifacts=(config,),
        immutable_context_refs=(config.ref,),
        deadline=(datetime.now(timezone.utc) + timedelta(seconds=budget.wall_seconds + 30)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"))


def _charges(rows):
    charges = fold_agent_attempts(rows)
    return {"charges": [{"attempt_id": item.attempt_id, "state": item.state,
        "source": item.source, "tokens": item.tokens, "turns": item.turns,
        "cost": None if item.cost is None else str(item.cost)} for item in charges],
        "charged_usd": str(charged_cost(charges)),
        "unknown_reservation_usd": str(sum((item.cost for item in charges
            if item.source == "declared" and item.cost is not None), Decimal("0")))}


def _progress(path, request, started, previous, changed_at, rows):
    content = path.read_bytes() if path.exists() else b""
    uses, results = _native_tools(content)
    signature = (len(content), len(uses), len(results))
    now = time.monotonic()
    if signature != previous:
        changed_at = now
    last_tool = uses[-1].get("name") if uses else None
    if last_tool not in {None, "Read", "Write", "Edit", "Glob", "Grep", "Bash", "Skill",
                         "mcp__host-tools__web_search", "mcp__host-tools__web_fetch"}:
        last_tool = "other"
    accounting = _charges(rows)
    return ({"attempt_id": request.attempt_id, "state": rows[0]["state"],
        "elapsed_seconds": round(now - started, 3), "tool_calls": len(uses),
        "tool_results": len(results), "last_tool": last_tool,
        "last_progress_seconds": round(now - changed_at, 3),
        "charged_usd": accounting["charged_usd"],
        "unknown_reservation_usd": accounting["unknown_reservation_usd"]}, signature, changed_at)


def run_probe(root: Path, *, adapter, model, provider_environment, runtime,
              allocation_remaining_usd: Decimal, scenario=None, progress_callback=None):
    """One explicit native invocation; ordinary diagnostic JSON is not a ledger.

    The optional scenario is solely the localhost test fixture. Supplying no
    scenario never supplies a response: the approved adapter contacts its actual
    provider. Unknown terminal accounting retains the original $2 reservation.
    """
    if (not isinstance(allocation_remaining_usd, Decimal)
            or not allocation_remaining_usd.is_finite()
            or allocation_remaining_usd < Decimal(NODE_BUDGET.max_cost)):
        raise ValueError("remaining allocation must cover the complete $2 node reservation")
    if (not isinstance(adapter, ClaudeAgentHost) or not isinstance(model, str) or not model.strip()
            or not isinstance(provider_environment, dict)):
        raise ValueError("explicit native adapter, model and provider environment required")
    runtime = Path(runtime).resolve(strict=True)
    if not runtime.is_dir() or not (runtime / "bin/python").is_file():
        raise ValueError("explicit Python runtime prefix required")
    root = Path(root).absolute()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValueError("probe root must be fresh and empty")
    adapter.preflight(model=model, provider_environment=provider_environment)
    root.mkdir(parents=True, exist_ok=True)
    target_path, target = make_target(root)
    (target_path / "README.md").write_text(INSTRUCTION + "\n")
    (target_path / "validation.json").write_text(json.dumps({"values": [1, 2, 3], "checksum": 6}))
    git(target_path, "add", ".")
    git(target_path, "-c", "user.name=Hypoloom test", "-c", "user.email=hypoloom@example.invalid",
        "commit", "-qm", "Disposable ordinary Grounding input")
    target = replace(target, commit=git(target_path, "rev-parse", "HEAD").decode().strip())
    manager = _manager(root)
    run_id = "gc-" + secrets.token_hex(8)
    request = _request(run_id, model, NODE_BUDGET)
    if scenario is not None:
        scenario.select(request)
    workspace = manager.prepare(request, target)
    assert not workspace.writable
    evidence = {"scope": "single native Grounding adapter/session; no WorkflowHost, SQLite, Oracle or G5 qualification",
        "run_id": run_id, "request": request.to_dict(), "model": model,
        "paid_model": scenario is None, "gpu": False, "invocations": 0,
        "allocation_remaining_usd": str(allocation_remaining_usd), "session": None,
        "observation": None, "process_group_exists_after_cleanup": None}
    row = {"kind": "AGENT", "state": "OPEN", "attempt_id": request.attempt_id,
        "observation_json": json.dumps({"request": request.to_dict()})}
    rows = [row]
    handle, observed = None, None
    started = time.monotonic()
    source = manager.state_root / "diagnostics/agents" / run_id / request.attempt_id / "stdout.log"
    previous, changed_at, next_progress = None, started, started
    try:
        evidence["invocations"] = 1
        handle = adapter.start(request, model=model, provider_environment=provider_environment,
            bind_process_group=lambda _: None, tool_workspace=workspace, runtime_prefix=runtime,
            diagnostics_root=manager.state_root,
            host_tool_handler=lambda *_: {"error": "No public web is in scope for this supplied-input Grounding probe."})
        while observed is None:
            adapter.service_tools()
            observed = adapter.poll(handle)
            if progress_callback is not None and time.monotonic() >= next_progress:
                progress, previous, changed_at = _progress(source, request, started, previous, changed_at, rows)
                progress_callback(progress)
                next_progress = time.monotonic() + 1
            if observed is None:
                time.sleep(0.05)
    finally:
        try:
            if handle is not None:
                adapter.cleanup(handle)
                observed = adapter.poll(handle)
                evidence["process_group_exists_after_cleanup"] = process_group_exists(handle.process_group_id)
            if observed is not None:
                observation = {"attempt_id": observed.attempt_id,
                    "terminal_reason": observed.terminal_reason.value, "returncode": observed.returncode,
                    "process_group_id": observed.process_group_id,
                    "started_at": observed.started_at, "ended_at": observed.ended_at,
                    "failure_code": observed.failure_code, "diagnostics": dict(observed.diagnostics)}
                if observed.response is not None:
                    observation["response"] = observed.response.to_dict()
                evidence["observation"] = observation
                row.update(state="CLOSED", observation_json=json.dumps({"request": request.to_dict(), **observation}))
            evidence["session"] = workspace.finish_node()
        finally:
            workspace.cleanup()
            evidence.update(_charges(rows))
            evidence["cli_stdout"] = str(source)
            terminal = None
            if source.exists():
                for line in source.read_bytes().splitlines():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and event.get("type") == "result":
                        terminal = {key: event[key] for key in ("type", "subtype", "is_error", "num_turns",
                            "total_cost_usd", "usage", "modelUsage") if key in event}
            evidence["cli_result"] = terminal
            (root / "probe-result.json").write_text(json.dumps(evidence, indent=2) + "\n")
            if progress_callback is not None:
                progress, _, _ = _progress(source, request, started, previous, changed_at, rows)
                progress_callback(progress)
    return evidence
