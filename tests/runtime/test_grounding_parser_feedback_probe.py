"""Fresh native Grounding/session + exact-package CPU feedback; not full G5.

The copied failed parser is advisory test input, never retained-run authority.
Only the local scripted companion runs automatically. Root may explicitly call
run_probe with its own adapter/model/environment/budget and remaining allocation.
"""
from __future__ import annotations

import copy
import json
import secrets
import shlex
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from hypoloom.agent_aci import AgentBudget, EmbeddedInput
from hypoloom.agent_budget import charged_cost, fold_agent_attempts
from hypoloom.claude_agent_host import ClaudeAgentHost, _output_schema, process_group_exists
from hypoloom.measurement_execution import prepare_parser, run_parser
from hypoloom.shared_acceptance import SharedAcceptance, evaluate_shared_acceptance, normalize_shared_observation
from test_agent_sessions import _manager
from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results, native_output_schema
from test_full_cc_adapter import _MODEL, _request as native_request
from test_full_cc_host_g3 import _start_provider
from test_git_authoring_workspace import make_target
from test_grounding_acceptance_aci import acceptance_exchange


PAIRS = {"labels": [0, 1, 1, 0, 1, 0, 1, 0], "predictions": [0, 1, 1, 0, 1, 0, 1, 1]}
BROKEN = Path(__file__).with_name("fixtures").joinpath("grounding_feedback_evaluate.py.txt").read_text()
DECLARATION = {"summary": "Accuracy on all eight original unchanged pairs.", "rule": "ALL",
    "criteria": [{"name": "accuracy", "operator": "GE", "target": "0.875"}],
    "validity_checks": ["complete_pairs", "computed_accuracy_matches"],
    "parser_argv": ["python", "evaluate.py"], "parser_files": ["evaluate.py"], "raw_artifacts": ["predictions.json"]}
MISSING = "FileNotFoundError: [Errno 2] No such file or directory: '/input/validation.json'"
INSTRUCTION = ("Repair your common parser declaration/source using the observed failure below. "
    "The original eight pairs and accuracy GE 0.875 goal cannot change. Raw predictions.json contains "
    "labels, predictions and computed_accuracy. Independently calculate accuracy; never hardcode a score. "
    "Check the original pairs unchanged and computed_accuracy agreement. Write evaluator files in /acceptance. "
    "If static reference data is needed, write it and include it in parser_files. In Bash copy ONLY the "
    "declared parser_files into a fresh package; test original/mutated/missing pairs and wrong computed_accuracy "
    "using separate raw/output directories and the documented parser environment. Do not rely on project/data "
    "mounts, undeclared files, downloads or web. Return complete Grounding JSON with the same goal; this is "
    "a diagnostic component probe, not scientific acceptance or permission to run an experiment.")


def _raw(pairs=PAIRS):
    return {**copy.deepcopy(pairs), "computed_accuracy": sum(a == b for a, b in
        zip(pairs["labels"], pairs["predictions"])) / len(pairs["labels"])}


def _parser(root, label, declaration, files, raw):
    raw_root = root / (label + "-raw"); raw_root.mkdir()
    (raw_root / "predictions.json").write_text(json.dumps(raw))
    prepared = prepare_parser(SharedAcceptance.from_dict(declaration), files, raw_dir=raw_root,
        work_dir=root / label, attempt_id=label, execution_id=label, timeout_seconds=10)
    observed = run_parser(prepared)
    stderr = (Path(prepared.process_request.cwd) / observed.observation.stderr_path).read_text()
    assert not process_group_exists(observed.observation.process_group_id)
    return {"process": observed.observation.to_dict(), "result": observed.raw_result,
            "error": observed.error, "stderr": stderr, "declared_files": list(declaration["parser_files"])}


def _request(run_id, ordinal, model, budget, feedback):
    base, _ = acceptance_exchange()
    task = replace(base.task_context, run_id=run_id)
    config = EmbeddedInput.from_bytes(run_id + "--grounding-config", "application/json",
        json.dumps({"goal": {"evaluation": {"mode": "grounding"}, "description": INSTRUCTION},
                    "node_runtime": {"output_correction": {"max_failed_validations": 3}}}).encode())
    advisory = EmbeddedInput.from_bytes(run_id + f"--parser-feedback-{ordinal}", "application/json",
        json.dumps({"instruction": INSTRUCTION, "original_pairs": PAIRS, "original_declaration": DECLARATION,
                    "copied_broken_source": BROKEN, "actual_parser_feedback": feedback}).encode())
    return replace(native_request(base, "grounding-feedback", None), schema_version=17,
        run_id=run_id, owner_workflow_id=run_id, task_context=task, command_id=task.command_id,
        attempt_id=run_id + f"--agent-{ordinal}", required_model_identity=model, budget=budget,
        input_artifacts=(config, advisory), immutable_context_refs=(config.ref,),
        deadline=(datetime.now(timezone.utc) + timedelta(seconds=budget.wall_seconds + 30)).strftime("%Y-%m-%dT%H:%M:%SZ"))


def _check_budget(budget, allocation, rows=()):
    if (not isinstance(budget, AgentBudget) or Decimal(budget.max_cost) <= 0 or not isinstance(allocation, Decimal)
            or not allocation.is_finite() or allocation <= 0):
        raise ValueError("explicit positive node budget and finite allocation required")
    if charged_cost(fold_agent_attempts(rows)) + Decimal(budget.max_cost) > allocation:
        raise ValueError("reported charges plus next reservation exceed probe allocation")


def run_probe(root: Path, *, adapter, model, provider_environment, budget,
              allocation_remaining_usd: Decimal, max_invocations=2, scenario=None):
    """Explicit caller-owned allocation; no settings lookup, paid default or auto-resume."""
    if (not isinstance(adapter, ClaudeAgentHost) or not isinstance(model, str) or not model.strip()
            or not isinstance(provider_environment, dict)
            or type(max_invocations) is not int or not 1 <= max_invocations <= 2):
        raise ValueError("explicit adapter/model/provider environment and one or two invocations required")
    _check_budget(budget, allocation_remaining_usd)
    if Decimal(budget.max_cost) * max_invocations > allocation_remaining_usd:
        raise ValueError("initial node reservations exceed probe allocation")
    _, target = make_target(root); manager = _manager(root)
    run_id = "grounding-feedback-" + secrets.token_hex(12)
    initial = _parser(root, "copied-broken", DECLARATION, {"evaluate.py": BROKEN.encode()}, _raw())
    assert initial["result"] is None and MISSING in initial["stderr"]
    evidence = {"scope": "native Grounding adapter/session and CPU parser only; no Host science or full G5",
        "run_id": run_id, "model": model, "paid_model": scenario is None, "gpu": False,
        "allocation_remaining_usd": str(allocation_remaining_usd), "initial_cpu_reproduction": initial,
        "attempts": [], "parser_checks": [], "repaired": False}
    rows, feedback = [], initial
    try:
        for ordinal in range(1, max_invocations + 1):
            try: _check_budget(budget, allocation_remaining_usd, rows)
            except ValueError as exc:
                evidence["stopped_reason"] = str(exc); break
            request = _request(run_id, ordinal, model, budget, feedback)
            if scenario is not None: scenario.select(request, ordinal)
            workspace = manager.prepare(request, target)
            row = {"kind": "AGENT", "state": "OPEN", "attempt_id": request.attempt_id,
                   "observation_json": json.dumps({"request": request.to_dict()})}
            rows.append(row); handle = None
            try:
                handle = adapter.start(request, model=model, provider_environment=provider_environment,
                    bind_process_group=lambda _: None, tool_workspace=workspace, diagnostics_root=manager.state_root,
                    host_tool_handler=lambda *_: {"error": "No web operations are in scope for this supplied-input parser probe."})
                observed = adapter.wait(handle, timeout=budget.wall_seconds + 5)
                metadata = workspace.finish_node()
                payload = {"request": request.to_dict(), "session": metadata,
                    "diagnostics": dict(observed.diagnostics), "failure_code": observed.failure_code}
                if observed.response is not None: payload["response"] = observed.response.to_dict()
                row.update(state="CLOSED", observation_json=json.dumps(payload))
                source = manager.state_root / "diagnostics/agents" / run_id / request.attempt_id / "stdout.log"
                records = [json.loads(line) for line in source.read_bytes().splitlines()]
                terminal = records[-1]
                assert terminal["type"] == "result"
                evidence["attempts"].append({"attempt_id": request.attempt_id, "session": metadata,
                    "failure_code": observed.failure_code, "diagnostics": dict(observed.diagnostics),
                    "cli_stdout": str(source), "cli_usage": terminal.get("usage"),
                    "cli_model_usage": terminal.get("modelUsage"), "cli_reported_cost": terminal.get("total_cost_usd")})
            finally:
                if handle is not None:
                    adapter.cleanup(handle)
                    assert not process_group_exists(handle.process_group_id)
            charge = fold_agent_attempts(rows)[-1]
            if charge.source == "declared": break  # No additional spend after unknown accounting.
            if observed.response is None:
                feedback = {"failure_code": observed.failure_code, "diagnostic": observed.diagnostics.get("provider_diagnostic")}
                continue
            declaration = dict(observed.response.typed_candidate["acceptance"])
            assert all(declaration[key] == DECLARATION[key] for key in ("rule", "criteria", "validity_checks", "raw_artifacts"))
            files = {item.path: item.content_bytes() for item in observed.response.proposed_workspace_artifacts}
            assert set(files) == set(declaration["parser_files"])
            feedback = _parser(root, f"returned-{ordinal}", declaration, files, _raw())
            evidence["parser_checks"].append(feedback)
            if feedback["result"] is None: continue
            acceptance = SharedAcceptance.from_dict(declaration)
            normalized = normalize_shared_observation(feedback["result"], acceptance)
            if normalized != {"measurements": {"accuracy": "0.875"},
                    "checks": {"complete_pairs": True, "computed_accuracy_matches": True}}: continue
            for label, raw, failed_check in (
                ("wrong-score", {**_raw(), "computed_accuracy": 0}, "computed_accuracy_matches"),
                ("wrong-pairs", _raw({"labels": PAIRS["labels"], "predictions": [1-x for x in PAIRS["labels"]]}), "complete_pairs"),
                ("missing-pair", _raw({key: value[:-1] for key, value in PAIRS.items()}), "complete_pairs"),
            ):
                check = _parser(root, f"{ordinal}-{label}", declaration, files, raw)
                evidence["parser_checks"].append(check)
                if check["result"] is not None:
                    assert not evaluate_shared_acceptance(acceptance, check["result"])
                    assert check["result"]["checks"][failed_check] is False
                    if label == "wrong-pairs": assert Decimal(str(check["result"]["measurements"]["accuracy"])) == 0
            evidence["repaired"] = True
            break
    finally:
        evidence["charges"] = [{"attempt_id": item.attempt_id, "source": item.source,
            "tokens": item.tokens, "turns": item.turns, "cost": str(item.cost)} for item in fold_agent_attempts(rows)]
        (root / "grounding-parser-feedback-evidence.json").write_text(json.dumps(evidence, indent=2))
    return evidence


class _Scenario:
    def __init__(self): self.requests, self.errors, self.bash_results = [], [], []

    def select(self, request, ordinal):
        self.request, self.ordinal, self.step = request, ordinal, 0
        _, response = acceptance_exchange()
        self.output = {"typed_candidate": copy.deepcopy(response.typed_candidate),
            "declared_effects": [{"effect": effect} for effect in request.effect_requirements]}
        self.output["typed_candidate"].update(acceptance=copy.deepcopy(DECLARATION), web_sources=[])
        if ordinal == 2: self.output["typed_candidate"]["acceptance"]["parser_files"].append("validation.json")

    def record(self, document):
        self.requests.append(document); self.step += 1
        try:
            assert native_output_schema(document) == _output_schema(self.request)
            assert MISSING in json.dumps(document)
            if self.ordinal == 2 and self.step == 4:
                result = _tool_results(document)["declared-package-test"]
                assert not result.get("is_error") and "DECLARED_PACKAGE_OK" in _result_text(result)
                self.bash_results.append(_result_text(result))
        except (AssertionError, KeyError) as exc: self.errors.append(str(exc))
        return len(self.requests)

    def tool_calls(self, _number):
        if self.errors: return ()
        if self.step == 1:
            source = BROKEN if self.ordinal == 1 else BROKEN.replace('INPUT_ROOT = "/input"', 'INPUT_ROOT = os.path.dirname(__file__)')
            return (("write-parser", "Write", {"file_path": "/acceptance/evaluate.py", "content": source}),
                ("write-smoke", "Write", {"file_path": "/acceptance/smoke/predictions.json", "content": json.dumps(_raw())}))
        if self.ordinal == 2 and self.step == 2:
            return (("write-reference", "Write", {"file_path": "/acceptance/validation.json", "content": json.dumps(PAIRS)}),)
        if self.ordinal == 2 and self.step == 3:
            script = "import json,os,shutil,subprocess,tempfile\nfrom pathlib import Path\n" + f"pairs={PAIRS!r}\n"
            script += "with tempfile.TemporaryDirectory() as temp:\n root=Path(temp)\n"
            script += " for name in ('evaluate.py','validation.json'): shutil.copyfile('/acceptance/'+name,root/name)\n"
            script += " raw=root/'raw'; raw.mkdir(); out=root/'out'; out.mkdir()\n"
            script += " score=sum(a==b for a,b in zip(pairs['labels'],pairs['predictions']))/len(pairs['labels'])\n"
            script += " (raw/'predictions.json').write_text(json.dumps(dict(pairs,computed_accuracy=score)))\n"
            script += " subprocess.run(['/usr/bin/python3',str(root/'evaluate.py')],cwd=root,env=dict(os.environ,HYPOLOOM_MEASUREMENT_RAW_ROOT=str(raw),HYPOLOOM_MEASUREMENT_RESULT_PATH=str(out/'result.json')),check=True)\n"
            script += " result=json.loads((out/'result.json').read_text()); assert result['measurements']['accuracy']==score and all(result['checks'].values())\n print('DECLARED_PACKAGE_OK')\n"
            return (("declared-package-test", "Bash", {"command": "python3 -c " + shlex.quote(script), "timeout": 10000}),)
        return (("final-json", "StructuredOutput", copy.deepcopy(self.output)),)


def test_native_grounding_parser_feedback_repairs_declared_package(tmp_path):
    scenario = _Scenario(); key = "local-grounding-feedback-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    try:
        result = run_probe(tmp_path, adapter=ClaudeAgentHost(claude_executable=_installed_claude()), model=_MODEL,
            provider_environment={"ANTHROPIC_API_KEY": key, "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{provider.server_port}"},
            budget=AgentBudget(max_turns=240, max_tokens=1_000_000, max_output_bytes=1_048_576, wall_seconds=1800, max_cost="7.5"),
            allocation_remaining_usd=Decimal("15"), scenario=scenario)
    finally:
        provider.shutdown(); provider.server_close(); thread.join(timeout=5)
    assert not scenario.errors and len(scenario.bash_results) == 1
    assert result["repaired"] and len(result["attempts"]) == 2
    first, second = [item["session"] for item in result["attempts"]]
    assert second["session_id"] == first["session_id"]
    assert second["launch_arguments"] == ["--resume", first["session_id"]]
    # The incomplete package is now rejected before publication by the native
    # parser smoke; only the corrected package reaches the external CPU check.
    correction = result["attempts"][0]["diagnostics"]["structured_output_correction"]
    assert correction["accepted"] is False and correction["rejected_count"] == 3
    assert MISSING in correction["rounds"][0]["validation_error"]
    checks = result["parser_checks"]
    assert len(checks) == 4 and checks[0]["process"]["execution_id"] == "returned-2"
    assert all(check["result"] is not None for check in checks)
    assert [item["source"] for item in result["charges"]] == ["provider", "response"]
    assert all(Decimal(item["cost"]) > 0 and item["tokens"] > 0 for item in result["charges"])
    assert key not in json.dumps(result)


@pytest.mark.parametrize("damage", ["model", "environment", "budget", "allocation", "overshoot"])
def test_probe_rejects_incomplete_inputs_or_unfunded_next_call_before_launch(tmp_path, damage):
    budget = AgentBudget(max_turns=240, max_tokens=1_000_000, max_output_bytes=1_048_576, wall_seconds=1800, max_cost="7.5")
    if damage == "overshoot":
        request = _request("offline-budget-probe", 1, _MODEL, budget, {})
        rows = [{"kind": "AGENT", "state": "CLOSED", "attempt_id": request.attempt_id,
            "observation_json": json.dumps({"request": request.to_dict(), "response": {
                "token_usage": {"total_tokens": 10}, "turn_count": 1, "cost": "7.6"}})}]
        with pytest.raises(ValueError, match="next reservation"):
            _check_budget(budget, Decimal("15"), rows)
    else:
        with pytest.raises(ValueError):
            run_probe(tmp_path, adapter=ClaudeAgentHost(claude_executable="/never-launch"),
                model="" if damage == "model" else _MODEL,
                provider_environment=None if damage == "environment" else {},
                budget=replace(budget, max_cost="0") if damage == "budget" else budget,
                allocation_remaining_usd=Decimal("NaN") if damage == "allocation" else Decimal("15"))
    assert list(tmp_path.iterdir()) == []
