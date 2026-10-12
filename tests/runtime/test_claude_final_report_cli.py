"""Actual final-report Skill and file capture; not WorkflowHost dispatch."""

import json
import secrets
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.agent_aci import EmbeddedInput, validate_postflight
from hypoloom.claude_agent_host import ClaudeAgentHost, _output_schema

from test_agent_sessions import _manager
from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results
from test_final_report import report_request
from test_full_cc_adapter import _invoke_adapter, _request as native_request
from test_full_cc_host_g3 import _start_provider
from test_grounding_manager import _with_plugin


class _FinalReportScenario:
    def __init__(self, request):
        self.request = request
        self.stage_step = 0
        self.requests, self.errors = [], []
        self.evidence = {}
        self.tool_prefix = ""
        self.structured_output = {}
        index, item = next((index, item) for index, item in enumerate(request.input_artifacts)
                           if item.ref.endswith("--final-report-context"))
        self.input_path = f"/inputs/{index:04d}.txt"
        self.context = json.loads(item.content_bytes())
        facts = [json.loads(row["payload_json"]) if "payload_json" in row else row.get("payload", row)
                 for row in self.context["facts"]]
        verdicts = [row for row in facts if row["fact_type"] == "GoalVerdictRecorded"]
        verdict = f"{verdicts[0]['fact_id']}: {verdicts[0]['verdict']}" if verdicts else "NO_ACCEPTED_VERDICT"
        outcomes = [f"- {row['outcome_id']}: {row['outcome']}" for row in facts if row["fact_type"] == "CanonicalOutcomeAccepted"]
        self.report = ("# Final research report\n\n## Accepted scientific result\n\n" + verdict
            + "\n\n" + "\n".join(outcomes) + "\n\n## Stop reason and limitations\n\n"
            + self.context["stop_reason"] + "\nThis report adds no experiment or scientific verdict. "
            "The compatibility provider was scripted; no paid model or GPU was used.\n")
        # Send literal Unicode through native Write and the Bash byte check;
        # tool argument decoding can otherwise interpret JSON's \u escapes.
        self.report += "\n## Agent attempt history\n\n" + json.dumps(
            self.context.get("run_outcome", {}).get("unsuccessful_agent_attempts", []), ensure_ascii=False)

    def record(self, document):
        self.requests.append(document)
        self.stage_step += 1
        step = self.stage_step
        try:
            previous = {2: "report_skill", 3: "report_sources", 4: "report_write", 5: "report_boundaries"}.get(step)
            if previous:
                result = _tool_results(document)[self.tool_prefix + previous]
                assert not result.get("is_error"), result
                self.evidence[previous] = _result_text(result)
            if step == 2:
                assert "The report is a narrative artifact derived from accepted records" in json.dumps(document)
            if step == 3:
                objects = []
                for line in self.evidence["report_sources"].splitlines():
                    prefix, separator, content = line.partition("\t")
                    if separator and prefix.strip().isdigit(): line = content
                    if line.lstrip().startswith("{"): objects.append(json.loads(line))
                # Read displays JSON escapes and line numbers. Verify the actual
                # decoded complete context, including nested stop-control text.
                assert objects == [self.context]
            if step == 5:
                assert "FINAL_REPORT_FILE_AND_BOUNDARIES" in self.evidence["report_boundaries"]
        except Exception as exc:
            self.errors.append(f"final report {step}: {type(exc).__name__}: {exc}")
        return len(self.requests)

    def tool_calls(self, number):
        if self.errors:
            return ()
        step = self.stage_step
        if step == 1:
            return (("report_skill", "Skill", {"skill": "hypoloom-grounding:final-report"}),)
        if step == 2:
            return (("report_sources", "Read", {"file_path": self.input_path}),)
        if step == 3:
            return (("report_write", "Write", {"file_path": "/report/FINAL_REPORT.md", "content": self.report}),)
        if step == 4:
            script = (
                "import errno, os\nfrom pathlib import Path\n"
                f"assert Path('/report/FINAL_REPORT.md').read_text() == {self.report!r}\n"
                "assert os.statvfs('/workspace').f_flag & os.ST_RDONLY\n"
                "assert not Path('/state').exists() and not Path('/memory').exists()\n"
                "assert not Path('/plugin-update').exists()\n"
                f"for path in [{self.input_path!r}, '/expert-plugin/skills/final-report/SKILL.md']:\n"
                "    try:\n        fd = os.open(path, os.O_WRONLY | os.O_APPEND)\n"
                "    except OSError as error:\n        assert error.errno == errno.EROFS, (path, error)\n"
                "    else:\n        os.close(fd)\n        raise AssertionError('report exceeded its writable scope')\n"
                "print('FINAL_REPORT_FILE_AND_BOUNDARIES')\n"
            )
            return (("report_boundaries", "Bash", {"command":
                "grep -q 'Final research report' /report/FINAL_REPORT.md && python3 - <<'PY'\n"
                + script + "PY", "timeout": 10000}),)
        return (("report_finish", "StructuredOutput", self.structured_output),)


@pytest.mark.parametrize("has_verdict", [True, False], ids=["accepted-verdict", "no-accepted-verdict"])
def test_native_final_report_skill_reads_sources_writes_actual_file_and_archives_exact_bytes(tmp_path: Path, has_verdict):
    executable = _installed_claude()
    manager = _manager(tmp_path)
    request = report_request()
    item = request.input_artifacts[0]
    context = json.loads(item.content_bytes())
    context["stop_reason"] = "Source-bound stop reason " + secrets.token_hex(16)
    if not has_verdict:
        context["facts"] = [row for row in context["facts"] if row["fact_type"] != "GoalVerdictRecorded"]
    request = replace(request, input_artifacts=(EmbeddedInput.from_bytes(item.ref, item.media_type,
        json.dumps(context).encode()),))
    _, _, request = _with_plugin(manager, request)
    request = native_request(request, "final-report", None)
    schema = _output_schema(request, tool_workspace=True)
    assert set(schema["properties"]) == {"typed_candidate", "declared_effects"}
    assert set(schema["properties"]["typed_candidate"]["properties"]) == {"task_kind", "summary", "supporting_refs"}
    scenario = _FinalReportScenario(request)
    key = "host-only-final-report-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    adapter = ClaudeAgentHost(claude_executable=executable)
    environment = {"ANTHROPIC_API_KEY": key, "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{provider.server_port}"}
    workspace = manager.prepare(request, None)
    try:
        response, metadata = _invoke_adapter(adapter, request, workspace, scenario, environment, tmp_path / "native")
        parsed = validate_postflight(request, response)
        assert parsed.workspace_files == {"FINAL_REPORT.md": scenario.report.encode()}
        archive = manager.publisher.root / metadata["transcript"]["path"]
        assert (archive / "FINAL_REPORT.md").read_bytes() == scenario.report.encode()
        assert metadata["transcript_complete"] is True
        assert set(metadata["transcript"]["relative_paths"]) == {"FINAL_REPORT.md", "transcript.jsonl"}
        transcript = (archive / "transcript.jsonl").read_bytes()
        assert key.encode() not in transcript
        assert "NO_ACCEPTED_VERDICT" in scenario.report if not has_verdict else "verdict-fact: GOAL_MET" in scenario.report
        assert len(scenario.requests) == 5 and len(scenario.evidence) == 4
        assert not scenario.errors
        (tmp_path / "native-evidence.json").write_text(json.dumps(scenario.evidence, indent=2))
    finally:
        (tmp_path / "provider-requests.json").write_text(json.dumps(scenario.requests, indent=2))
        (tmp_path / "provider-errors.json").write_text(json.dumps(scenario.errors, indent=2))
        provider.shutdown(); provider.server_close(); thread.join(timeout=5)
