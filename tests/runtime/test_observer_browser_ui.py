"""Actual browser interactions against the shipped assets and bounded read fixtures.

These tests require a locally installed Chrome and websockets; they never install
a browser, contact model providers, or open a retained research state root.
"""

from __future__ import annotations

import json
import os
import base64
import shutil
import subprocess
import time
import io
import zipfile
from pathlib import Path
from urllib.request import urlopen

import pytest


_ASSETS = Path(__file__).resolve().parents[2] / "hypoloom/web_observer_assets"
_TOKEN = "browser-fixture-token"
_CHROME = os.environ.get("HYPOLOOM_TEST_CHROME") or (
    str(Path("/opt/google/chrome/chrome"))
    if Path("/opt/google/chrome/chrome").is_file()
    else shutil.which("google-chrome") or shutil.which("chromium")
)
_ATTEMPT = {
    "attempt_id": "attempt-a",
    "role": "expert-a",
    "task_scope": "expert",
    "owner_role": "expert-a",
    "state": "CLOSED",
    "kind": "AGENT",
    "command": "SELF_EVOLVE",
    "phase": "SELF_EVOLVE",
    "started_at": 10,
    "ended_at": 20,
    "component_id": "component-a",
    "turns": 2,
    "raw_turns": 5,
    "launch_mode": "fork",
    "parent_session_id": "session-parent",
    "scope": "chain",
    "chain": "main",
    "cycle": 2,
    "branch": "branch-a",
}
_SKILL = {
    "role": "expert-a",
    "name": "resource-planning",
    "commit": "accepted-v2",
    "description": "Plan memory from measured use",
    "current": True,
    "bindings": [{"attempt_id": "attempt-a"}],
}
_PAPER = {
    "bibliographic_identity": "doi:10.1/example",
    "title": "GPU evidence <script>window.injected=true</script>",
    "year": 2026,
    "venue": "Example venue",
    "pdf_available": True,
    "roles": ["expert-a"],
    "source_attempt_ids": ["attempt-a"],
    "url": "javascript:window.injected=true",
    "pdf_url": "https://example.invalid/paper.pdf",
}


def _reliability() -> dict:
    def metric(n, d, *, unknown=0, excluded=0):
        return {"numerator": n, "denominator": d, "rate": n / d if d else None,
                "unknown": unknown, "excluded": excluded}
    metrics = {"output_first_pass": metric(1, 2, unknown=1),
        "correction_recovery": metric(1, 1), "node_retry": metric(1, 2),
        "node_delivery": metric(1, 2), "required_actions": metric(0, 0, unknown=1)}
    counts = {"agent_attempts": 3, "closed_attempts": 3, "open_attempts": 0,
        "timed_out_attempts": 1, "cancelled_attempts": 0, "tool_calls": 8,
        "tool_errors": 2, "trace_unknown_attempts": 1}
    costs = {"retry_attempts": 1, "retry_reported_usd": "0.25", "retry_reserved_usd": "2",
        "retry_reported_tokens": 250, "retry_reserved_tokens": 2000, "retry_unavailable_token_attempts": 1,
        "retry_unknown_closed_attempts": 1, "retry_elapsed_seconds": 32,
        "retry_unknown_nodes": 2, "retry_unavailable_reservations": 1,
        "retry_elapsed_unknown_attempts": 1,
        "correction_cost_usd": None}
    groups, nodes = [], []
    for command, role, node, model in [("command-a", "expert-a", "DESIGN", "model-a"),
                                       ("command-b", "expert-b", "GROUNDING", "model-b")]:
        scope = {"task_scope": "run" if node == "GROUNDING" else "expert",
                 "owner_role": None if node == "GROUNDING" else role}
        groups.append({"role": role, "node": node, "model": model, **scope,
                       "metrics": metrics, "counts": counts, "costs": costs})
        nodes.append({"command_id": command, "workflow_id": "workflow-a", "role": role, **scope,
            "node": node, "model": model, "status": "FAILED", "metrics": metrics, "costs": costs,
            "attempt_ids": ["attempt-a"], "attempts": [{"attempt_id": "attempt-a",
                "terminal_reason": "TIMED_OUT", "failure_code": "timed_out",
                "failure_stage": "provider_response", "retry_class": "INFRA",
                "attribution": {"category": "unverified", "confidence": "unknown",
                                "reason": "Deadline does not identify a model cause"},
                "corrections": {"rejected_count": 2},
                "observations": [{"kind": "output_rejected", "count": 2}],
                "trace": {"coverage": "partial", "tool_errors": 2}}]})
    nodes.append({**nodes[0], "command_id": "command-excluded", "metrics": {
        **metrics, "required_actions": metric(0, 0, excluded=1)}})
    return {"metrics": metrics, "counts": counts, "costs": costs,
            "groups": groups, "nodes": nodes,
            "notices": ["Scientific refutation and expected test failures are not model failures."]}


def _snapshot() -> dict:
    return {
        "run_id": "browser-run",
        "observed_at": time.time(),
        "status": {"run_state": "RUNNING", "run_revision": 1, "open_attempts": 0},
        "overview": {"reliability": _reliability()},
        "oracle": {"ok": True},
        "goal": {"objective": "Observe research evidence"},
        "components": [
            {"component_id": "component-a", "owner_role": "expert-a", "state": "READY"}
        ],
        "experts": [
            {
                "role": "expert-a",
                "slot_ordinal": 1,
                "owned_component_ids": ["component-a"],
            }
        ],
        "attempts": [_ATTEMPT],
        "rendezvous": [],
        "fact_counts": {"HypothesisProposed": 2},
        "hypotheses": [
            {
                "hypothesis_id": "hypothesis-a",
                "component_id": "component-a",
                "proposer_role": "expert-a",
                "claim": "Measure memory",
                "chain": "main",
                "assessment": "SUPPORTED",
            },
            {
                "hypothesis_id": "hypothesis-b",
                "component_id": "component-a",
                "proposer_role": "expert-a",
                "claim": "Use measurements",
                "parent_hypothesis_id": "hypothesis-a",
                "chain": "main",
                "relation": "derived_from",
                "assessment": "UNASSESSED",
            },
        ],
    }


def _skill_effects() -> dict:
    return {"groups": [{"role": "expert-a", "commit": "accepted-v2", "stage": "EXPERIMENT_DESIGN_IMPLEMENT",
        "component_id": "component-a", "model": "model-a", "basis": "observed_call", "name": "resource-planning",
        "failed_attempts": 3, "metrics": {"node_delivery": {"numerator": 0, "denominator": 2, "rate": 0, "unknown": 1, "excluded": 0}},
        "costs": {"retry_reported_usd": "0.5", "retry_reported_tokens": 10},
        "output_rejection_count": 1, "output_rejections": [{"attempt_id": "failed-design", "validation_stage": "json_decode"}],
        "failures": [{"attempt_id": "failed-design", "failure_code": "invalid_candidate", "diagnostic": "<script>window.injected=true</script>",
            "source_proposals": [{"attempt_id": "proposal-source", "hypothesis_id": "h1"}]}]}],
        "followups": [{"role": "expert-a", "name": "resource-planning", "commit": "accepted-v2", "attempt_id": "evolution-source",
            "status": "recurrence_observed", "comparisons": [{"stage": "EXPERIMENT_DESIGN_IMPLEMENT", "component_id": "component-a", "model": "model-a",
                "status": "recurrence_observed", "failure_code": "invalid_candidate", "diagnostic": "frozen measurement",
                "baseline_failed_attempts": 6, "later_nodes": 2, "later_finished_nodes": 2, "later_failed_attempts": 6,
                "baseline_attempt_ids": ["before-update"], "later_attempt_ids": ["after-update"], "later_commits": ["accepted-v3"],
                "versions": [{"commit": "accepted-v3", "status": "recurrence_observed", "later_finished_nodes": 2, "later_failed_attempts": 6}]}]},
            {"role": "expert-a", "name": "resource-planning", "commit": "accepted-v3", "attempt_id": "latest-evolution", "status": "awaiting_samples"}],
        "repairs": [{"role": "expert-a", "experiment_id": "repair-exp", "predecessor_experiment_id": "original-exp",
            "terminal_reason": "SUCCEEDED", "returncode": 0, "predecessor_terminal_reason": "FAILED", "predecessor_returncode": 70,
            "process_attempt_id": "repair-process", "predecessor_process_attempt_id": "failed-process",
            "source_attempt_id": "repair-source", "predecessor_source_attempt_id": "failed-source"}]}


def _activity() -> dict:
    return {
        "run_id": "browser-run",
        "observed_at": time.time(),
        "summary": {
            "observed_at": time.time(),
            "elapsed_seconds": 360,
            "waiting_human_seconds": 60,
            "totals": {
                "experiments": {"total": 123},
                "hypotheses": 2,
                "literature": {"retrieved": 321, "pdf_available": 200},
                "skills": {"count": 2, "coverage": "complete"},
                "evolutions": {"changed": 1, "failed": 1},
                "leader": {"started": 3, "active": 0},
                "debate": {"started": 2, "active": 0},
            },
        },
        "experiments": [{"experiment_id": "experiment-a", "state": "CLOSED"}],
        "events": [],
        "experts": [],
        "final_report": {
            "attempt_id": "report-a",
            "markdown": "Accepted report stays available",
            "verdict": "SUCCESS",
            "stop_reason": "goal met",
        },
    }


def _detail(query: dict[str, list[str]]) -> dict:
    kind = query["kind"][0]
    offset = int(query.get("offset", [0])[0])
    result = {
        "kind": kind,
        "run_id": "browser-run",
        "offset": offset,
        "limit": 50,
        "available": True,
        "total": 1,
    }
    if kind == "skills":
        result.update(
            skills=[dict(_SKILL, name="next-page-skill")] if offset else [_SKILL],
            total=51,
            activity={"coverage": "partial", "observed_calls": 2, "attempts": 2,
                "effects": _skill_effects(),
                "untimed_calls": 0, "bin_seconds": 60,
                "skills": [{"role": "expert-a", "name": "resource-planning", "calls": 2,
                            "last_called_commit": "accepted-v2"}],
                "timeline": [{"started_at": 10, "ended_at": 70, "calls": 2, "failed": 1}],
                "evolutions": [{"attempt_id": "attempt-a", "role": "expert-a", "command": "SELF_EVOLVE",
                    "cycle": 2, "started_at": 10, "ended_at": 20, "base_commit": "accepted-v1",
                    "commit": "accepted-v2", "status": "accepted_change", "changes_available": True,
                    "later_bindings": 1, "observed_later_calls": 1,
                    "skills": [{"name": "resource-planning", "files": [{
                        "path": "skills/resource-planning/references/performance.md", "change": "modified"}]}]}]},
        )
    elif kind == "skill":
        result.update(
            _SKILL,
            effects=_skill_effects(),
            body="# Resource planning\n<script>window.injected=true</script>\n"
            + "Keep measurements separate from estimates.\n" * 80,
            diff={
                "base_commit": "accepted-v1",
                "commit": "accepted-v2",
                "available": True,
                "text": "- 128 MiB\n+ 512 MiB",
            },
            supporting_files=[
                {"path": "scripts/estimate.py", "size": 17, "text": "print('estimate')"}
            ],
            adoption=[
                {
                    "attempt_id": "attempt-a",
                    "evidence": "request_binding",
                    "native_loaded": None,
                }
            ],
            update_attempt={"attempt_id": "attempt-a"},
        )
    elif kind == "literature":
        result.update(papers=[_PAPER], coverage="complete")
    elif kind == "sessions":
        result.update(
            sessions=[
                {
                    "session_id": "session-a",
                    "role": "expert-a",
                    "task_scope": "expert", "owner_role": "expert-a",
                    "component_id": "component-a",
                    "parent_session_id": "session-parent",
                    "scope": "chain",
                    "chain": "main",
                    "cycle": 2,
                    "branch": "branch-a",
                    "launch_modes": ["fork", "resume"],
                    "attempts": [
                        _ATTEMPT,
                        {
                            **_ATTEMPT,
                            "attempt_id": "attempt-resumed",
                            "launch_mode": "resume",
                        },
                    ],
                },
                {
                    "session_id": "session-parent",
                    "role": "grounding",
                    "component_id": None,
                    "parent_session_id": None,
                    "scope": "grounding",
                    "chain": None,
                    "cycle": None,
                    "branch": None,
                    "launch_modes": ["unknown"],
                    "attempts": [
                        {
                            **_ATTEMPT,
                            "attempt_id": "attempt-parent",
                            "command": "GROUNDING",
                            "role": "grounding",
                            "launch_mode": "unknown",
                        }
                    ],
                },
            ],
            total=2,
        )
    elif kind == "attempt":
        result.update(
            attempt=_ATTEMPT,
            scope="current_invocation",
            events=[
                {
                    "type": "tool_use",
                    "name": "Skill",
                    "input": {"skill": "researcher:resource-planning"},
                }
            ]
            if not offset
            else [{"type": "assistant", "content": "Later timeline page"}],
            corrections=[
                {
                    "validation": "failed",
                    "reason": "missing output",
                    "same_invocation": True,
                }
            ],
            total=51,
            turns=2,
            raw_turns=5,
        )
    elif kind == "interactions":
        result.update(
            interactions=[
                {
                    "kind": "DEBATE",
                    "interaction_id": "debate-a",
                    "state": "ABORTED",
                    "participants": ["expert-a", "expert-b"],
                    "stages": [{"action": "GOAL_STOP_ABORT", "revision": 7}],
                    "source_attempt_ids": ["attempt-a"],
                    "verdict": None,
                }
            ]
        )
    elif kind == "resources":
        result.update(
            experiments=[
                {
                    "experiment_id": "experiment-a",
                    "role": "expert-a",
                    "component_id": "component-a",
                    "estimate": {
                        "peak_memory_mib": 512,
                        "workload": {"batch": "8"},
                        "basis": "Previous same-workload observations",
                        "measurement_attempt_ids": [],
                    },
                    "attempts": [
                        {
                            "attempt_id": "attempt-a",
                            "state": "CLOSED",
                            "grant": {"kind": "GPU", "gpu_uuid": "GPU-authorized"},
                            "admitted": 1024,
                            "measured": {
                                "attempt_id": "attempt-a",
                                "sample_peak_bytes": None,
                                "measurement_status": "unknown",
                                "admitted_workload_memory_mib": 1024,
                                "terminal_status": "SUCCEEDED",
                                "oom": False,
                            },
                            "usage": [
                                {
                                    "gpu_uuid": "GPU-authorized",
                                    "owned_memory": {
                                        "sampled_peak_memory_mib": 362,
                                        "sample_count": 12,
                                        "unknown_sample_count": 1,
                                        "coverage": "unknown",
                                    },
                                }
                            ],
                            "terminal_reason": "SUCCEEDED",
                        }
                    ],
                    "next_estimate_sources": [
                        {
                            "experiment_id": "experiment-b",
                            "measurement_attempt_ids": ["attempt-a"],
                        }
                    ],
                },
                {
                    "experiment_id": "experiment-b",
                    "role": "expert-a",
                    "component_id": "component-a",
                    "estimate": {
                        "peak_memory_mib": 768,
                        "measurement_attempt_ids": ["attempt-a"],
                    },
                    "attempts": [],
                    "next_estimate_sources": [],
                },
            ],
            total=2,
            shared_pool={
                "available": False,
                "reason": "No configured pool observation",
            },
        )
    return result


def test_browser_support_preview_private_choice_and_zip_download(browser, tmp_path):
    page = browser
    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, "w") as archive:
        archive.writestr("README.md", "review this support copy")
        archive.writestr("report.json", '{"include_private_data":true}')
    preview = {"readme": "Review this support copy <script>window.supportInjected=true</script>",
        "report": {"include_private_data": False, "diagnosis": {"available": False}, "limited": True,
                   "selection": {"selected_detail_count": 3, "supporting_detail_count": 11, "run_attempt_count": 63},
                   "coverage": {"limiting_reasons": ["inspection_time_limit"],
                       "attachment_status_counts": {"not_inspected": 2},
                       "next_steps": ["Select an individual Attempt to collect uninspected evidence."]},
                   "exported_file_count": 3, "exported_payload_bytes": 2500},
        "diagnostics": {"oracle": {"available": False}, "attempts": []}}
    page.evaluate("""(() => {
      const prior = window.fetch;
      window.supportCalls = [];
      window.supportBodies = [];
      window.supportStatus = 200;
      window.fetch = async (path, options = {}) => {
        if (!path.startsWith('/api/support?')) return prior(path, options);
        window.fixtureRequests.push([options.method, path, options.headers.Authorization]);
        window.supportCalls.push(path);
        window.supportBodies.push(JSON.parse(options.body));
        const query = new URL(path, 'http://observer.invalid').searchParams;
        if (window.supportStatus !== 200) return new Response('{}', {status: window.supportStatus});
        if (query.get('format') === 'zip') return new Response(Uint8Array.from(atob(""" + json.dumps(base64.b64encode(zipped.getvalue()).decode()) + """), c => c.charCodeAt(0)));
        const preview = """ + json.dumps(preview) + """;
        preview.report.include_private_data = query.get('private') === '1';
        return new Response(JSON.stringify(preview));
      };
    })()""")
    assert page.evaluate("supportCalls.length") == 0
    page.click("#open-support")
    page.evaluate("document.querySelector('#support-actual').value = 'Observed a failure'; document.querySelector('#support-actual').dispatchEvent(new Event('input'))")
    assert page.evaluate("document.querySelector('#support-dialog').open")
    assert not page.evaluate("document.querySelector('#support-private').checked")
    assert page.evaluate("document.querySelector('#download-support').disabled")
    page.click("#preview-support")
    page.wait("!document.querySelector('#download-support').disabled")
    assert "3 selected + 11 supporting details / 63 recorded attempts" in page.text("#support-status")
    assert "inspection_time_limit" in page.text("#support-status")
    assert "2 scopes/files not inspected" in page.text("#support-status")
    assert "Select an individual Attempt" in page.text("#support-status")
    assert "diagnosis unavailable" in page.text("#support-status")
    assert page.evaluate("window.supportInjected !== true")
    page.click("#support-private")
    assert page.evaluate("document.querySelector('#download-support').disabled")
    page.evaluate("document.querySelector('#support-scope').value = 'attempt:attempt-a'; document.querySelector('#support-scope').dispatchEvent(new Event('change'))")
    page.click("#preview-support")
    page.wait("!document.querySelector('#download-support').disabled")
    assert page.evaluate("supportCalls.at(-1).includes('private=1') && supportCalls.at(-1).includes('attempt_id=attempt-a')")
    page.call("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(tmp_path)})
    page.click("#download-support")
    page.wait("document.querySelector('#support-status').textContent.includes('Download requested')")
    output = tmp_path / "hypoloom-support-private.zip"
    deadline = time.monotonic() + 10
    while not output.is_file() and time.monotonic() < deadline:
        time.sleep(.05)
    with zipfile.ZipFile(output) as archive:
        assert archive.read("README.md") == b"review this support copy"
    page.call("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": False})
    assert page.evaluate("document.querySelector('#support-dialog').scrollWidth <= document.querySelector('#support-dialog').clientWidth")
    page.screenshot(tmp_path / "support-mobile.png")
    page.evaluate("window.supportStatus = 401")
    page.click("#preview-support")
    page.wait("document.querySelector('#support-status').textContent.includes('authenticated')")
    assert page.evaluate("document.querySelector('#download-support').disabled")
    assert page.evaluate("fixtureRequests.filter(row => row[1].startsWith('/api/support?')).every(row => row[0] === 'POST' && row[2] === 'Bearer browser-fixture-token')")
    assert page.evaluate("supportBodies.every(body => body.actual === 'Observed a failure')")
    page.click("#close-support")
    assert not page.evaluate("document.querySelector('#support-dialog').open")
    assert not page.errors


class _Browser:
    def __init__(self, socket) -> None:
        self.socket = socket
        self.serial = 0
        self.errors: list[dict] = []

    def call(self, method: str, params: dict | None = None) -> dict:
        self.serial += 1
        identity = self.serial
        self.socket.send(
            json.dumps({"id": identity, "method": method, "params": params or {}})
        )
        while True:
            message = json.loads(self.socket.recv(timeout=10))
            if message.get("method") == "Runtime.exceptionThrown":
                self.errors.append(message)
            if message.get("id") == identity:
                assert "error" not in message, message
                return message.get("result", {})

    def evaluate(self, expression: str):
        result = self.call(
            "Runtime.evaluate", {"expression": expression, "returnByValue": True}
        )
        assert "exceptionDetails" not in result, result
        return result.get("result", {}).get("value")

    def wait(self, expression: str) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.evaluate(expression):
                return
            time.sleep(0.05)
        raise AssertionError(
            f"Browser condition did not become true: {expression}; errors={self.errors}"
        )

    def click(self, selector: str) -> None:
        self.wait(f"document.querySelector({json.dumps(selector)}) !== null")
        # Graph fitting runs in requestAnimationFrame after its nodes appear.
        # Let that layout (and scrolling) finish before choosing mouse coordinates.
        result = self.call(
            "Runtime.evaluate", {
                "expression": f"""new Promise(resolve => {{
                  document.querySelector({json.dumps(selector)}).scrollIntoView({{block:'center'}});
                  requestAnimationFrame(() => requestAnimationFrame(() => {{
                    const r=document.querySelector({json.dumps(selector)}).getBoundingClientRect();
                    resolve({{x:r.x+r.width/2,y:r.y+r.height/2}});
                  }}));
                }})""",
                "awaitPromise": True, "returnByValue": True,
            },
        )
        assert "exceptionDetails" not in result, result
        point = result["result"]["value"]
        self.call(
            "Input.dispatchMouseEvent",
            {"type": "mousePressed", "button": "left", "clickCount": 1, **point},
        )
        self.call(
            "Input.dispatchMouseEvent",
            {"type": "mouseReleased", "button": "left", "clickCount": 1, **point},
        )

    def text(self, selector: str) -> str:
        return self.evaluate(
            f"document.querySelector({json.dumps(selector)}).textContent"
        )

    def screenshot(self, path: Path) -> None:
        result = self.call("Page.captureScreenshot", {"format": "png"})
        path.write_bytes(base64.b64decode(result["data"]))


@pytest.fixture
def browser(tmp_path: Path, request):
    if not _CHROME:
        pytest.skip("Browser interaction needs locally installed Chrome/Chromium")
    connect = pytest.importorskip("websockets.sync.client").connect
    # Chrome on some CI hosts cannot route page HTTP to loopback. Keep browser
    # behavior independent of that host transport; actual Python HTTP auth,
    # scoping and immutable state are covered in test_web_observer.py.
    payloads = {
        "messages": json.loads((_ASSETS / "messages.json").read_text()),
        "snapshot": _snapshot(),
        "activity": _activity(),
        "details": {
            f"{kind}:{offset}": _detail({"kind": [kind], "offset": [str(offset)]})
            for kind in (
                "skills",
                "skill",
                "literature",
                "sessions",
                "attempt",
                "interactions",
                "resources",
            )
            for offset in (0, 50)
        },
    }
    payloads["snapshot"]["overview"].update(payloads["activity"]["summary"])
    mock = (
        "window.fixtureRequests = []; const fixture = "
        + json.dumps(payloads)
        + ";"
        + """
    window.fetch = async (path, options = {}) => {
      window.fixtureRequests.push([options.method || 'GET', path, options.headers?.Authorization]);
      const query = new URL(path, 'http://observer.invalid');
      if (query.pathname === '/api/refresh') window.fixtureObservedAt = options.headers?.['X-Hypoloom-Observed-At'];
      if (query.pathname === '/api/refresh' && window.fixtureDelay) await new Promise((resolve, reject) => {
        const abort = () => {clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError'));};
        const timer = setTimeout(() => {options.signal?.removeEventListener('abort', abort); resolve();}, window.fixtureDelay);
        if (options.signal?.aborted) abort();
        else options.signal?.addEventListener('abort', abort, {once:true});
      });
      if (query.pathname === '/api/details' && window.fixtureDetailDelay) await new Promise(resolve => setTimeout(resolve, window.fixtureDetailDelay));
      if (['/api/events','/api/experiments'].includes(query.pathname) && window.fixtureHistoryDelay) await new Promise(resolve => setTimeout(resolve, window.fixtureHistoryDelay));
      if (query.pathname === '/api/refresh' && window.fixtureError) return {ok:false, status:503};
      if (query.pathname === '/api/refresh' && window.fixtureRefresh) return {
        ok:true, status:window.fixtureRefreshStatus || 200, json:async () => structuredClone(window.fixtureRefresh)};
      const data = query.pathname === '/assets/messages.json' ? fixture.messages
        : query.pathname === '/api/refresh' ? {snapshot: fixture.snapshot, activity: fixture.activity}
        : query.pathname === '/api/events' ? {events:[{type:'OLDER_EVENT', event_id:'old-event', payload:{}}], events_page:{offset:100, limit:100,total:101,has_more:false}}
        : query.pathname === '/api/experiments' ? {experiments:[{experiment_id:'old-experiment',state:'CLOSED'}], experiments_page:{offset:50,limit:50,total:51,has_more:false}}
        : query.pathname === '/api/snapshot' ? fixture.snapshot
        : query.pathname === '/api/activity' ? fixture.activity
        : fixture.details[query.searchParams.get('kind') + ':' + (query.searchParams.get('offset') || '0')];
      const response = structuredClone(data);
      if (query.searchParams.get('kind') === 'resources' && query.searchParams.has('experiment_id')) {
        response.experiments = response.experiments.filter(item => item.experiment_id === query.searchParams.get('experiment_id'));
        response.total = response.experiments.length;
      }
      if (query.searchParams.get('kind') === 'sessions') {
        for (const key of ['session_id', 'component_id', 'task_scope']) {
          if (query.searchParams.has(key)) response.sessions = response.sessions.filter(item => item[key] === query.searchParams.get(key));
        }
        if (query.searchParams.has('role')) response.sessions = response.sessions.filter(item => item.task_scope === 'expert' && item.owner_role === query.searchParams.get('role'));
        response.total = response.sessions.length;
      }
      return {ok: true, status: 200, json: async () => response};
    };
    """
    )
    initial_pending = getattr(request, 'param', False) == 'pending'
    operator = False if initial_pending else getattr(request, "param", False)
    if initial_pending:
        mock += "window.fixtureRefreshStatus=202; window.fixtureRefresh={snapshot:null,activity:null,observation:{state:'pending',age_seconds:null,refreshing:true}};"
    # The data-URL fixture lacks secure-context randomUUID; actual product URLs
    # are loopback HTTP. Supply the browser crypto operation for this fixture.
    if operator:
        mock += "if (!crypto.randomUUID) crypto.randomUUID = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), x => x.toString(16).padStart(2, '0')).join('');"
    script = mock + (_ASSETS / "app.js").read_text()
    markup = (
        (_ASSETS / "index.html")
        .read_text()
        .replace(
            "/assets/style.css",
            "data:text/css;base64,"
            + base64.b64encode((_ASSETS / "style.css").read_bytes()).decode(),
        )
        .replace(
            "/assets/app.js",
            "data:text/javascript;base64," + base64.b64encode(script.encode()).decode(),
        )
    )
    profile = tmp_path / "chrome-profile"
    chrome = subprocess.Popen(
        [
            _CHROME,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-extensions",
            "--window-size=1440,1000",
            "--disable-dev-shm-usage",
            "--disable-background-networking",
            "--disable-component-update",
            "--no-first-run",
            "--no-default-browser-check",
            "--no-proxy-server",
            "--remote-debugging-port=0",
            f"--user-data-dir={profile}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    socket = None
    try:
        port_file = profile / "DevToolsActivePort"
        deadline = time.monotonic() + 10
        while not port_file.exists() and time.monotonic() < deadline:
            assert chrome.poll() is None, "Local Chrome exited during startup"
            time.sleep(0.05)
        assert port_file.exists(), "Local Chrome did not publish its debug port"
        port = int(port_file.read_text().splitlines()[0])
        with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as response:
            pages = json.load(response)
        socket = connect(
            next(
                page["webSocketDebuggerUrl"] for page in pages if page["type"] == "page"
            ),
            proxy=None,
            # CDP echoes the fully embedded data URL in navigation events.
            # Size the test transport for that page; keep every UI assertion.
            max_size=max(1024 * 1024, len(markup.encode()) * 4),
        )
        driver = _Browser(socket)
        driver.call("Page.enable")
        driver.call("Runtime.enable")
        driver.call(
            "Page.navigate",
            {
                "url": "data:text/html;base64,"
                + base64.b64encode(markup.encode()).decode()
                + f"#token={_TOKEN}" + ("&provider_token=fixture-provider-token" if operator == "provider"
                    else "&literature_token=fixture-literature-token" if operator == "literature"
                    else "&answer_token=fixture-answer-token" if operator else "")
            },
        )
        driver.wait(
            "document.querySelector('#connection-text').textContent === 'PREPARING SNAPSHOT'" if initial_pending
            else "document.querySelector('#total-skills strong')?.textContent === '2'"
        )
        yield driver
        assert not driver.errors
        requests = driver.evaluate("window.fixtureRequests")
        assert all(method == "GET" or (method == "POST" and path.startswith("/api/support?"))
            or (operator and method == "POST" and path == "/api/human-answer") for method, path, _ in requests)
        assert all(
            token == f"Bearer {_TOKEN}"
            for _, path, token in requests
            if path.startswith("/api/")
        )
        assert all(_TOKEN not in path for _, path, _ in requests)
    finally:
        if socket is not None:
            socket.close()
        chrome.terminate()
        try:
            chrome.wait(timeout=10)
        except subprocess.TimeoutExpired:
            chrome.kill()
            chrome.wait(timeout=5)


def test_browser_host_absence_dispatch_pause_and_terminal_labels(browser):
    page = browser
    page.evaluate("fixture.snapshot.status.run_state='ACTIVE'; fixture.snapshot.overview.host_observation={availability:'absent'}")
    page.wait("document.querySelector('#run-state').textContent.includes('HOST ABSENT (orphaned)')")
    page.evaluate("fixture.snapshot.overview.host_observation.availability='responded'; fixture.snapshot.status.dispatch_error={exception_type:'ValueError'}")
    page.wait("document.querySelector('#run-state').textContent.includes('DISPATCH PAUSED') && !document.querySelector('#run-state').textContent.includes('HOST ABSENT')")
    page.evaluate("fixture.snapshot.status.run_state='CANCELLED'")
    page.wait("document.querySelector('#run-state').textContent === 'CANCELLED'")


def test_browser_final_node_failure_is_separate_from_live_state(browser):
    page = browser
    page.evaluate("""fixture.snapshot.status.run_state='ACTIVE';
      fixture.snapshot.overview.host_observation={availability:'responded'};
      Object.assign(fixture.snapshot.attempts[0], {operational_status:'recovery_exhausted',
        operational_reason:'timed_out', phase:'EXPERIMENT_DESIGN_IMPLEMENT',
        chain:'<script>window.injected=true</script>', ended_at:1791612501});""")
    page.wait("!document.querySelector('#node-failures').hidden")
    assert page.text('#run-state') == 'ACTIVE'
    assert not page.evaluate("document.querySelector('#run-state').classList.contains('blocked')")
    assert page.text('#node-failure-summary') == 'Recorded final node failures: 1'
    page.click('#node-failure-summary')
    assert 'expert-a' in page.text('#node-failure-list')
    assert 'EXPERIMENT_DESIGN_IMPLEMENT' in page.text('#node-failure-list')
    assert 'timed_out' in page.text('#node-failure-list')
    assert not page.evaluate('Boolean(window.injected)')
    page.click('#node-failure-list button')
    page.wait("fixtureRequests.some(r=>r[1].includes('kind=attempt') && r[1].includes('attempt_id=attempt-a'))")
    page.click('#close-drawer')
    page.click('#language-toggle')
    page.wait("document.querySelector('#node-failure-summary').textContent==='已记录的节点最终失败：1'")
    assert '不代表整项运行已停止' in page.text('#node-failures')
    page.evaluate("fixture.snapshot.status.dispatch_error={exception_type:'ValueError'}")
    page.wait("document.querySelector('#run-state').classList.contains('blocked')")
    page.evaluate("fixture.snapshot.status.run_state='COMPLETED'")
    page.wait("document.querySelector('#run-state').textContent==='COMPLETED'")
    assert not page.evaluate("document.querySelector('#node-failures').hidden")
    page.evaluate("fixture.snapshot.attempts=[]")
    page.wait("document.querySelector('#node-failures').hidden")


def test_browser_run_hud_uses_observed_totals_without_reserves_or_filtering(browser, tmp_path):
    page = browser
    assert page.text('#run-tokens') == '—'
    # A compact lower bound must never round upward past known consumption.
    for reported, label in ((1559, '≥1.55K'), (2559000, '≥2.55M'), (999999, '≥999.99K')):
        page.evaluate(f"fixture.snapshot.overview.accounting={{reported_tokens:{reported},observed_unsettled_tokens:0}}")
        page.wait(f"document.querySelector('#run-tokens').textContent === '{label}'")
    page.evaluate("""Object.assign(fixture.snapshot.overview, {
      elapsed_seconds: 90061,
      accounting: {reported_tokens: 2500000, observed_unsettled_tokens: 50000,
        reserved_tokens: 9000000, failed_reported_tokens: 400000, unknown_closed_attempts: 1}
    })""")
    page.wait("document.querySelector('#run-tokens').textContent === '≥2.55M'")
    assert page.text('#run-elapsed') == '1d 1h'
    assert '2500000 reported + 50000' in page.evaluate("document.querySelector('#run-tokens').title")
    page.evaluate("document.querySelector('#filter-search').value='no matching expert'; document.querySelector('#filter-search').dispatchEvent(new Event('input'))")
    for language in ('zh-CN', 'en'):
        page.click('#language-toggle')
        page.wait(f"document.documentElement.lang === '{language}'")
        assert page.text('#run-tokens') == '≥2.55M'
        assert page.text('#run-elapsed') == '1d 1h'
        assert page.evaluate("document.querySelector('#run-elapsed').previousElementSibling.textContent") == ('已运行' if language == 'zh-CN' else 'ELAPSED')
        for width in (1440, 768, 390, 320):
            page.call('Emulation.setDeviceMetricsOverride', {'width': width, 'height': 1000,
                'deviceScaleFactor': 1, 'mobile': False})
            assert page.evaluate("['run-elapsed','run-tokens'].every(id => { const e=document.getElementById(id),r=e.getBoundingClientRect(); return r.left>=0 && r.right<=innerWidth && e.scrollWidth<=e.clientWidth; })")
            if width in (1440, 390):
                page.screenshot(tmp_path / f'hud-{language}-{width}.png')
    # The backend's elapsed observation remains frozen for a terminal run.
    page.evaluate("fixture.snapshot.status.run_state='COMPLETED'; fixture.snapshot.overview.elapsed_seconds=90062; fixture.snapshot.overview.accounting={reported_tokens:2550000,observed_unsettled_tokens:0}")
    page.wait("document.querySelector('#run-state').textContent === 'COMPLETED'")
    assert page.text('#run-tokens') == '≥2.55M'  # Settlement does not double count.
    page.evaluate("window.fixtureError=true")
    page.wait("document.querySelector('#connection').dataset.state === 'stale'")
    assert page.text('#run-elapsed') == '1d 1h'
    assert page.text('#run-tokens') == '≥2.55M'
    page.evaluate("window.fixtureError=false; fixture.snapshot.overview.elapsed_seconds=null; fixture.snapshot.overview.accounting={}")
    page.wait("document.querySelector('#connection').dataset.state === 'live' && document.querySelector('#run-tokens').textContent === '—'")
    assert page.text('#run-elapsed') == '—'
    page.evaluate("fixture.snapshot.overview.elapsed_seconds=0; fixture.snapshot.overview.accounting={reported_tokens:0,observed_unsettled_tokens:0}")
    page.wait("document.querySelector('#run-tokens').textContent === '≥0'")
    assert page.text('#run-elapsed') == '0.0s'
    assert page.evaluate("fixtureRequests.every(item=>item[0]==='GET')")


def test_browser_resource_wait_explains_budget_and_does_not_invent_measurement(browser, tmp_path):
    page = browser
    page.evaluate("fixture.snapshot.status_source='sqlite+resident-host-status'")
    page.evaluate("""fixture.snapshot.status.resource_waits=[{
      experiment_id:'gpu-experiment',reason:'exclusive_wait',
      message:'Waiting for an exclusive GPU; workload sharing is not yet calibrated',
      resource_planning:{resource_intent:{workload_memory_mib:16384,reserve_memory_mib:4096,sharing:'exclusive'},
        budget:{sample_peak_mib:null,headroom_percent:25}}
    }]""")
    page.wait("document.querySelector('#resource-wait-list').textContent.includes('16384 MiB')")
    content = page.text('#resource-wait-list')
    assert 'exclusive GPU' in content and '4096 MiB' in content
    assert 'no complete measurement' in content and 'measured headroom' not in content
    page.evaluate("""Object.assign(fixture.snapshot.status.resource_waits[0],{
      reason:'capacity_unavailable',message:'Waiting for GPU memory to become available',
      resource_planning:{resource_intent:{workload_memory_mib:18688,reserve_memory_mib:4096,sharing:'shared'},
        budget:{sample_peak_mib:16198,headroom_percent:15}}
    })""")
    page.wait("document.querySelector('#resource-wait-list').textContent.includes('measured headroom 15%')")
    assert '18688 MiB' in page.text('#resource-wait-list')
    assert 'no complete measurement' not in page.text('#resource-wait-list')
    page.screenshot(tmp_path / 'resource-budget.png')


def test_browser_execution_reliability_filters_metrics_and_attempt_evidence(browser, tmp_path):
    page = browser
    page.evaluate("fixture.snapshot.overview.stages=[{command_id:'usage-example', accounting:{reported_tokens:1234,reported_cost_usd:'0.25'},context_recovery:{recovery_reported_tokens:1200,cli_unknown_pricing_attempts:3,recovery_reported_cost_usd:'0.2'}}]")
    page.evaluate("fixture.snapshot.status.run_state='ACTIVE'")
    page.wait("document.querySelector('#run-state').textContent === 'ACTIVE'")
    page.click("#command-retry-costs")
    page.wait("document.querySelector('#research-detail-content').textContent.includes('usage-example')")
    usage = page.text("#research-detail-content")
    assert "reported_tokens" in usage and "1234" in usage and "1200" in usage
    assert "cost_usd" not in usage and "pricing" not in usage
    page.click("#close-drawer")
    page.click("#execution-reliability")
    page.wait("document.querySelector('.reliability-metric') !== null")
    summary = page.text("#reliability-summary")
    for expected in ("First output", "1/2", "unknown 1", "Correction recovery",
                     "Nodes needing retry", "Final delivery", "Required actions", "unavailable",
                     "Retry overhead", "250 tokens", "2000 tokens", "Correction-only tokens: unavailable",
                     "unknown retry nodes 2", "unavailable reserves 1", "unknown elapsed 1",
                     "Timeouts 1", "not model failures"):
        assert expected in summary
    assert "$" not in summary and "pricing" not in summary
    assert page.evaluate("document.querySelectorAll('.reliability-node').length") == 3
    page.screenshot(tmp_path / "execution-reliability.png")
    page.click("[data-reliability-metric=required_actions]")
    assert "Required actions" in page.text("#reliability-list-label")
    assert page.evaluate("document.querySelectorAll('.reliability-node').length") == 2
    page.evaluate("document.querySelector('#reliability-model').value='model-b'; document.querySelector('#reliability-model').dispatchEvent(new Event('change'))")
    assert page.evaluate("document.querySelectorAll('.reliability-node').length") == 1
    assert "command-b" in page.text("#reliability-nodes")
    assert "model-a" not in page.text("#reliability-groups")
    page.evaluate("fixture.snapshot.overview.reliability.counts.timed_out_attempts=3")
    page.wait("document.querySelector('#reliability-summary').textContent.includes('Timeouts 3')")
    assert page.evaluate("document.querySelector('#reliability-model').value") == "model-b"
    page.click(".reliability-node button")
    detail = page.text("#research-detail-content")
    assert "timed_out" in detail and "unverified" in detail and "rejected_count" in detail
    assert "Retry overhead" in detail and "250 tokens" in detail
    assert "output rejected · count 2" in page.text(".reliability-observations")
    page.click(".reliability-attempt-link")
    page.wait("document.querySelector('[data-detail-tab=corrections]') !== null")
    page.click("[data-detail-tab=corrections]")
    assert "same_invocation" in page.text("#research-detail-content")
    assert page.evaluate("fixtureRequests.every(row => !row[1].includes('kind=reliability'))")
    page.click("#close-drawer")
    page.evaluate("fixture.snapshot.overview={}")
    page.wait("document.querySelector('#workbench-status').textContent.includes('Reliability unavailable')")
    assert "unavailable/unavailable" in page.text("#reliability-summary")


def test_browser_expert_skill_library_filters_trend_and_evolution(browser, tmp_path):
    page = browser
    page.click('[data-expert-skills="expert-a"]')
    page.wait("document.querySelector('.skill-frequency button') !== null")
    assert page.evaluate("document.querySelector('#filter-expert').value") == "expert-a"
    assert "2 observed calls" in page.text(".skill-coverage")
    assert "lower bounds" in page.text("#skill-library")
    page.click(".skill-trend-bar")
    assert "2 calls / 1 failures" in page.text(".skill-trend-caption")
    page.click(".skill-change summary")
    assert "references/performance.md" in page.text(".skill-change")
    page.evaluate("document.querySelector('#skills-window').value='3600'; document.querySelector('#skills-window').dispatchEvent(new Event('change'))")
    page.wait("fixtureRequests.some(row => row[1].includes('window=3600') && row[1].includes('role=expert-a'))")
    page.evaluate("document.querySelector('#skills-command').value='EXPERIMENT_EXECUTION'; document.querySelector('#skills-command').dispatchEvent(new Event('change'))")
    page.wait("fixtureRequests.some(row => row[1].includes('command=EXPERIMENT_EXECUTION'))")
    page.screenshot(tmp_path / "expert-skill-library.png")
    page.click(".skill-frequency button")
    page.wait("document.querySelector('[data-detail-tab=usage]') !== null")
    page.click("[data-detail-tab=usage]")
    assert "No calls observed" in page.text("#research-detail-content")
    assert page.evaluate("window.injected === undefined")


def test_browser_skill_effects_show_counts_unknowns_followup_and_source_links(browser):
    page = browser
    page.click('[data-expert-skills="expert-a"]')
    page.wait("document.querySelector('.skill-scorecard summary') !== null")
    assert "Awaiting validation" in page.text(".skill-effects")
    assert "Failure recurred" in page.text(".skill-effects")
    page.click(".skill-scorecard summary")
    assert "0/2 · 0.0% · unknown 1" in page.text(".skill-scorecard")
    assert "not causal skill scores" in page.text(".skill-effects")
    assert "<script>" in page.text(".skill-failure")
    assert "Validation stage: json_decode" in page.text(".skill-scorecard")
    assert "owner background observations" in page.text(".skill-effects")
    page.click(".skill-followup summary")
    assert "same-diagnostic failures 6" in page.text(".skill-followup")
    assert "Version accepted-v3: Failure recurred; finished 2, matching failures 6." in page.text(".skill-followup")
    page.click(".skill-repair summary")
    assert "original-exp → repair-exp" in page.text(".skill-repair")
    assert "FAILED / 70 → SUCCEEDED / 0" in page.text(".skill-repair")
    page.evaluate("Array.from(document.querySelectorAll('.skill-failure button')).find(b => b.textContent === 'Inspect source proposal').click()")
    page.wait("fixtureRequests.some(row => row[1].includes('attempt_id=proposal-source'))")
    assert page.evaluate("window.injected === undefined")


def test_browser_execution_repairs_separate_science_budget_history_and_unknown(browser):
    from tests.runtime.test_observer_revisions import creation, project, outcome

    page = browser
    rows = [creation(f"{chain}-{iteration}", n * 6 + iteration, chain=chain, iteration=iteration)
        for n, chain in enumerate("abcd") for iteration in range(1, 7)]
    rows.append(creation("a-fix", 25, iteration=6, repair="a-out"))
    activity = project(rows, limit=1)
    page.evaluate("fixture.snapshot.components[0].revision_activity=" + json.dumps(activity))
    page.wait("document.querySelector('.expert-preview .revision-activity')?.textContent.includes('scientific iterations 20')")
    text = page.text(".expert-preview")
    assert "initial experiments 4" in text and "execution repairs 1" in text
    assert "convergence" not in text and "allowance used" not in text
    page.click('[data-expert-details="expert-a"]')
    page.wait("document.querySelector('#research-detail-content').textContent.includes('execution repairs 1/1')")
    assert "a-fix" in page.text("#research-detail-content")
    activity = project(rows, limit=1, state="CANCELLED", outcomes=[outcome("a-fix", "EXECUTION_FAILURE")])
    page.evaluate("fixture.snapshot.components[0].revision_activity=" + json.dumps(activity))
    page.wait("document.querySelector('#research-detail-content').textContent.includes('Historical observation')")
    assert "Execution repair allowance used" in page.text("#research-detail-content")
    assert not page.evaluate("document.querySelector('#research-detail-content .repair-scope .blocked') !== null")
    page.click("#close-drawer")
    page.click("#language-toggle")
    page.wait("document.querySelector('.expert-preview .revision-activity')?.textContent.includes('科学迭代 20 次')")
    assert "未收敛" not in page.text(".expert-preview")
    page.evaluate("delete fixture.snapshot.components[0].revision_activity; fixture.snapshot.components[0].revision_warning='general'")
    page.wait("document.querySelector('.expert-preview').textContent.includes('执行修复范围无法确定')")
    assert "执行修复 0" not in page.text(".expert-preview")


def test_browser_skill_click_diff_adoption_and_refresh_preserve_context(
    browser, tmp_path: Path
) -> None:
    page = browser
    page.click("#total-skills")
    page.wait(
        "document.querySelector('#workbench-list .record-title')?.textContent === 'resource-planning'"
    )
    assert page.text("#total-experiments strong") == "123"
    assert page.text("#total-literature strong") == "321"
    page.evaluate(
        "document.querySelector('#filter-expert').value='expert-a'; document.querySelector('#filter-expert').dispatchEvent(new Event('change'))"
    )
    page.evaluate(
        "document.querySelector('#filter-search').value='resource'; document.querySelector('#filter-search').dispatchEvent(new Event('input'))"
    )
    page.click("#workbench-list .record-title")
    page.wait("document.querySelector('[data-detail-tab=body]') !== null")
    page.click("[data-detail-tab=body]")
    page.wait("document.querySelector('.skill-body')?.textContent.includes('<script>')")
    assert page.evaluate("window.injected === undefined")
    assert page.evaluate(
        "document.querySelector('#research-detail-content script') === null"
    )
    page.evaluate("document.querySelector('.skill-body').scrollTop=200")
    reads_before = page.evaluate(
        "fixtureRequests.filter(row => row[1].includes('kind=skills')).length"
    )
    # Unchanged snapshots deliberately avoid fetching the same detail list.
    # Trigger a real new revision before checking that refresh preserves context.
    revision = page.evaluate("fixture.snapshot.status.run_revision")
    assert type(revision) is int, "fixture must supply the Host's numeric run revision"
    page.evaluate("fixture.snapshot.status.run_revision += 1")
    page.wait(f"document.querySelector('#run-revision').textContent === '{revision + 1}'")
    page.wait(
        "document.querySelector('#observed-at').textContent !== '' && document.querySelector('[data-detail-tab=body]').getAttribute('aria-pressed') === 'true'"
    )
    page.wait(
        f"fixtureRequests.filter(row => row[1].includes('kind=skills')).length > {reads_before}"
    )
    assert page.evaluate("document.querySelector('.skill-body').scrollTop") == 200
    page.click("#research-detail-tabs button:last-child")
    page.wait(
        "document.querySelector('#research-detail-status').textContent === 'Read-only evidence'"
    )
    assert page.evaluate("document.querySelector('.skill-body').scrollTop") == 200
    assert page.evaluate("document.querySelector('#filter-expert').value") == "expert-a"
    assert page.evaluate("document.querySelector('#filter-search').value") == "resource"
    assert (
        page.evaluate(
            "document.querySelector('#tab-workbench').getAttribute('aria-selected')"
        )
        == "true"
    )
    page.click("[data-detail-tab=diff]")
    assert "+ 512 MiB" in page.text(".skill-diff")
    page.screenshot(tmp_path / "skill-diff.png")
    page.click("[data-detail-tab=adoption]")
    assert "request_binding" in page.text("#research-detail-content")
    assert "unavailable" in page.text("#research-detail-content")
    page.click("[data-detail-tab=files]")
    page.click("#research-detail-content summary")
    assert "print('estimate')" in page.text("#research-detail-content")
    page.click("#close-drawer")
    page.click("#clear-filters")
    page.click("#workbench-next")
    page.wait(
        "document.querySelector('#workbench-list .record-title')?.textContent === 'next-page-skill'"
    )
    assert "51–51 of 51" in page.text("#workbench-page")
    assert page.text("#total-skills strong") == "2"


def test_browser_session_corrections_literature_interactions_and_resource_details(
    browser, tmp_path: Path
) -> None:
    page = browser
    page.click("#expert-list .detail-link")
    page.wait("document.querySelector('#research-detail-content').textContent.includes('Browse expert history')")
    page.evaluate("Array.from(document.querySelectorAll('#research-detail-content button')).find(b=>b.textContent==='Browse expert history').click()")
    page.wait("document.querySelector('.session-attempt') !== null")
    lineage = page.evaluate("document.querySelector('#workbench-list').innerText")
    assert all(
        value in lineage
        for value in ("chain / main", "2 / branch-a", "fork → resume", "session-parent")
    )
    assert page.evaluate(
        "Array.from(document.querySelectorAll('.session-launch')).map(item => item.textContent)"
    ) == ["fork", "resume"]
    page.screenshot(tmp_path / "session-lineage.png")
    page.click(".session-parent")
    page.wait(
        "document.querySelector('#workbench-list h3')?.textContent === 'session-parent'"
    )
    assert page.text(".session-launch") == "unknown"
    assert page.evaluate(
        "fixtureRequests.some(row => row[1].includes('session_id=session-parent') && !row[1].includes('role=') && !row[1].includes('component_id='))"
    )
    assert page.evaluate("document.querySelector('#filter-expert').value") == "expert-a"
    page.click("[data-workbench=sessions]")
    page.wait(
        "document.querySelector('#workbench-list h3')?.textContent === 'session-a'"
    )
    page.click(".session-attempt")
    page.wait("document.querySelector('[data-detail-tab=timeline]') !== null")
    page.click("[data-detail-tab=timeline]")
    assert "researcher:resource-planning" in page.text("#research-detail-content")
    page.click("#research-detail-content .pagination button")
    page.wait(
        "document.querySelector('#research-detail-content').textContent.includes('Later timeline page')"
    )
    page.click("[data-detail-tab=corrections]")
    assert "same_invocation" in page.text("#research-detail-content")
    page.click("#close-drawer")
    page.click("[data-workbench=literature]")
    page.wait(
        "document.querySelector('#workbench-list').textContent.includes('Example venue')"
    )
    assert page.evaluate(
        "document.querySelector('#workbench-list a[href^=\"javascript:\"]') === null"
    )
    assert (
        page.evaluate("document.querySelector('#workbench-list a').rel")
        == "noopener noreferrer"
    )
    page.click("#workbench-list .record-title")
    assert "doi:10.1/example" in page.text("#research-detail-content")
    assert page.evaluate("window.injected === undefined")
    page.click("#close-drawer")
    page.click("#total-debate")
    page.wait(
        "document.querySelector('#workbench-list').textContent.includes('ABORTED')"
    )
    page.click("#workbench-list .record-title")
    page.click("[data-detail-tab=timeline]")
    assert "GOAL_STOP_ABORT" in page.text("#research-detail-content")
    page.click("#close-drawer")
    page.click("#total-experiments")
    page.wait("document.querySelector('#workbench-list').textContent.includes('362')")
    assert "No configured pool observation" in page.text("#workbench-status")
    page.click("#workbench-list .record-title")
    detail = page.evaluate(
        "document.querySelector('#research-detail-content').innerText"
    )
    assert all(
        value in detail for value in ("512", "1,024", "362", "unknown", "attempt-a")
    )
    assert all(
        label in detail
        for label in (
            "Requested estimate",
            "Admitted workload",
            "Qualified measurement",
            "Own sampled peak",
            "Samples / unknown",
            "Coverage",
            "Granted GPU UUID",
            "SUCCEEDED",
            "12 / 1",
        )
    )
    assert page.evaluate("document.querySelector('.resource-raw').open") is False
    assert (
        page.evaluate("document.querySelector('.resource-unknown dd').textContent")
        == "unknown"
    )
    page.screenshot(tmp_path / "resource-detail.png")
    page.click("#research-detail-content .resource-next-link")
    page.wait(
        "document.querySelector('#research-detail-title').textContent === 'experiment-b'"
    )
    assert "768 MiB" in page.text("#research-detail-content")
    assert "This estimate cites: attempt-a" in page.text("#research-detail-content")
    assert page.evaluate(
        "fixtureRequests.some(row => row[1].includes('kind=resources') && row[1].includes('experiment_id=experiment-b'))"
    )
    page.click("#close-drawer")
    page.evaluate(
        "fixture.details['resources:0'].shared_pool = {available:true, sampled_at:100, devices:[{gpu_uuid:'GPU-authorized', free_memory_mib:2048, allocatable_memory_mib:1024, outstanding_memory_mib:512, reserve_memory_mib:512, active_commitments:2, unknown_attribution_commitments:1}]}"
    )
    page.click("#refresh-workbench")
    page.wait(
        "document.querySelector('.pool-capacity')?.textContent.includes('GPU-authorized')"
    )
    assert "2 / 1" in page.text(".pool-capacity")
    page.click("#tab-activity")
    assert "Accepted report stays available" in page.text("#final-report-text")


def test_browser_graph_nodes_edges_and_selected_chain_survive_poll(browser) -> None:
    page = browser
    page.click("#total-hypotheses")
    page.wait("document.querySelectorAll('.graph-node').length === 2")
    page.click(".graph-node[data-key='hypothesis:hypothesis-b']")
    page.wait(
        "document.querySelector('#selection-detail').textContent.includes('hypothesis-b')"
    )
    assert "UNASSESSED" in page.text("#selection-detail")
    page.click("#close-drawer")
    before = page.evaluate(
        "fixtureRequests.filter(row => row[1] === '/api/refresh').length"
    )
    page.wait(
        f"fixtureRequests.filter(row => row[1] === '/api/refresh').length > {before}"
    )
    assert (
        page.evaluate("document.querySelector('.graph-node.selected').dataset.key")
        == "hypothesis:hypothesis-b"
    )
    page.click(".graph-edge")
    page.wait(
        "document.querySelector('#research-detail-content').textContent.includes('derived_from')"
    )
    assert "hypothesis-a" in page.text("#research-detail-content")
    page.click("#close-drawer")
    page.click(".graph-node[data-key='hypothesis:hypothesis-b']")
    page.click("#selection-detail .detail-link")
    page.wait(
        "document.querySelector('#workbench-scope').textContent.includes('component-a')"
    )
    page.wait(
        "fixtureRequests.some(row => row[1].includes('component_id=component-a'))"
    )


def test_browser_hypothesis_relations_keep_revision_evidence_in_details(browser, tmp_path):
    """Recorded ancestry and controls must not be inferred from revision prose."""
    page = browser
    page.evaluate("""(() => {
      const root = {hypothesis_id:'root', component_id:'component-a', proposer_role:'expert-a',
        method_family_id:'attention', claim:'Causal attention improves accuracy.',
        parent_hypothesis_id:null, relation:null, assessment:'INCONCLUSIVE', cycle:1};
      const nodes = [root,
        {...root, hypothesis_id:'repair', parent_hypothesis_id:'root', cycle:2,
          relation:'保留核心科学主张，修正实验执行脚本，确保 predictions.json 输出格式正确。'},
        {...root, hypothesis_id:'experiment', parent_hypothesis_id:'repair', cycle:3,
          relation:'Retains the core scientific hypothesis, fixes the execution pipeline, and increases MELD epochs from 15 to 80. Loss weights adjusted (0.4 -> 0.35).'},
        {...root, hypothesis_id:'science', parent_hypothesis_id:'experiment', cycle:4,
          method_family_id:'graph_attention', claim:'Graph alignment improves causal attention.',
          relation:'保留核心假设，并新增跨模态图对齐机制（Graph Attention Filtering + CrossModalAttention）。修正执行流水线。',
          assessment:'UNASSESSED', state:'PRUNED'},
        {...root, hypothesis_id:'unknown', parent_hypothesis_id:'root', relation:'derived_from'}];
      fixture.activity.graph = {nodes, edges:[
        ...nodes.filter(n => n.parent_hypothesis_id).map(n => ({source:n.parent_hypothesis_id, target:n.hypothesis_id, relation:n.relation})),
        {source:'root', target:'science', relation:'pruned_by'}]};
    })()""")
    page.click("#total-hypotheses")
    page.wait("document.querySelectorAll('.graph-node').length === 5")
    assert page.evaluate("document.querySelectorAll('#graph-edges .graph-edge[data-relation-kind=\"lineage\"]').length") == 4
    assert page.evaluate("[...document.querySelectorAll('#graph-edges .graph-edge[data-relation-kind=\"lineage\"]')].every(n => getComputedStyle(n).strokeDasharray === 'none')")
    assert page.evaluate("getComputedStyle(document.querySelector('#graph-edges .pruning-edge')).strokeDasharray") == "7px, 4px"
    assert "Root hypothesis" in page.text(".graph-node[data-key='hypothesis:root']")
    assert "Derived hypothesis" in page.text(".graph-node[data-key='hypothesis:repair']")
    assert "Execution repair" not in page.text("#graph-nodes")
    assert "Scientific revision" not in page.text("#graph-legend")
    assert "Parent → derived hypothesis" in page.text("#graph-legend")
    assert "Winner → pruned hypothesis" in page.text("#graph-legend")
    assert "Pruning does not change ancestry or scientific assessment" in page.text("#graph-summary")
    assert "parent: root" in page.text("#topology-list")
    page.click(".graph-node[data-key='hypothesis:repair']")
    assert "修正实验执行脚本" in page.text("#selection-detail")
    assert "parent claim" in page.text("#selection-detail")
    assert "INCONCLUSIVE" in page.text("#selection-detail")
    assert "change type" not in page.text("#selection-detail")
    page.click("#close-drawer")
    before = page.evaluate("fixtureRequests.filter(row => row[1] === '/api/refresh').length")
    page.wait(f"fixtureRequests.filter(row => row[1] === '/api/refresh').length > {before}")
    assert page.evaluate("document.querySelector('.graph-node.selected').dataset.key") == "hypothesis:repair"
    page.evaluate("document.querySelector('#graph-edges [data-relation-kind=\"lineage\"][data-target=\"hypothesis:science\"]').dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true}))")
    assert "Graph Attention Filtering" in page.text("#research-detail-content")
    assert "parent claim" in page.text("#research-detail-content")
    assert "child claim" in page.text("#research-detail-content")
    assert "Scientific revision" not in page.text("#research-detail-content")
    page.click("#close-drawer")
    page.evaluate("document.querySelector('#graph-edges .pruning-edge').dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true}))")
    assert "pruned_by" in page.text("#research-detail-content")
    assert "control decision" in page.text("#research-detail-content")
    assert "winner claim" in page.text("#research-detail-content")
    assert "child claim" not in page.text("#research-detail-content")
    page.click("#close-drawer")
    page.click("#fit-graph")
    page.evaluate("window.scrollTo(0, document.querySelector('.graph-panel').getBoundingClientRect().top + window.scrollY - 150)")
    page.screenshot(tmp_path / "hypothesis-relations.png")


def test_browser_hypothesis_revision_text_and_missing_parent_snapshot_fallback(browser):
    page = browser
    page.evaluate("""(() => {
      const root = {hypothesis_id:'root', component_id:'component-a', proposer_role:'expert-a',
        method_family_id:'attention', claim:'Causal attention improves accuracy.', assessment:'INCONCLUSIVE'};
      fixture.snapshot.hypotheses = [root,
        {...root, hypothesis_id:'failure', parent_hypothesis_id:'root', relation:'EXECUTION_FAILURE; INCONCLUSIVE; retry cycle 2'},
        {...root, hypothesis_id:'family-only', parent_hypothesis_id:'root', method_family_id:'renamed', relation:'revision'},
        {...root, hypothesis_id:'missing-parent', parent_hypothesis_id:'absent', relation:'Retains the core scientific hypothesis and fixes the execution script.'},
        {...root, hypothesis_id:'negated', parent_hypothesis_id:'root', relation:'Retains the core scientific hypothesis and fixes the execution script. Does not add a new mechanism.'},
        {...root, hypothesis_id:'format', parent_hypothesis_id:'root', relation:'Retains the core scientific hypothesis. Changes the output format to match the model and fixes the execution script.'},
        {...root, hypothesis_id:'historical', parent_hypothesis_id:'root', relation:'The previous version introduced a new mechanism. Retains the core scientific hypothesis and fixes the execution script.'},
        {...root, hypothesis_id:'same-family-science', parent_hypothesis_id:'root', relation:'Introduces a new graph alignment mechanism.'},
        {...root, hypothesis_id:'protocol', parent_hypothesis_id:'root', relation:'Changes the measurement protocol and replaces the parser.'},
        {...root, hypothesis_id:'unspecified-fix', parent_hypothesis_id:'root', claim:'A different, unspecified explanation.', relation:'fixes the execution pipeline'},
        {...root, hypothesis_id:'literal', parent_hypothesis_id:'root', relation:'<img src=x onerror=window.graphInjected=true>'},
        {...root, hypothesis_id:'screening', parent_hypothesis_id:'root', relation:'新增筛选门→晋升门两段预注册，筛选到晋升冻结核心机制。'},
        {...root, hypothesis_id:'attention', parent_hypothesis_id:'root', relation:'以模态对称因果跨模态注意力替换文本锚定不对称融合。'}];
    })()""")
    page.click("#total-hypotheses")
    page.wait("document.querySelectorAll('.graph-node').length === 13")
    assert page.evaluate("document.querySelectorAll('#graph-edges .graph-edge').length") == 11
    assert page.evaluate("[...document.querySelectorAll('#graph-edges .graph-edge')].every(n => n.dataset.relationKind === 'lineage' && getComputedStyle(n).strokeDasharray === 'none')")
    assert "Scientific revision" not in page.text("#graph-nodes")
    assert "Unclear revision" not in page.text("#graph-nodes")
    for identity in ("screening", "attention", "missing-parent", "literal"):
        page.evaluate(f"document.querySelector('.graph-node[data-key=\"hypothesis:{identity}\"]').dispatchEvent(new KeyboardEvent('keydown', {{key:'Enter', bubbles:true}}))")
        relation = page.evaluate(f"fixture.snapshot.hypotheses.find(n => n.hypothesis_id === '{identity}').relation")
        assert relation in page.text("#selection-detail")
        assert "Derived hypothesis" in page.text("#selection-detail")
        if identity == "missing-parent":
            assert "Parent record unavailable" in page.text("#selection-detail")
        assert page.evaluate("!window.graphInjected && !document.querySelector('#selection-detail img')")
        page.click("#close-drawer")


def test_browser_pruning_overlay_preserves_ancestry_and_visible_legend(browser, tmp_path):
    page = browser
    page.evaluate("""(() => {
      fixture.snapshot.experts.push({role:'expert-b', slot_ordinal:2});
      const root = {component_id:'component-a', proposer_role:'expert-a', claim:'Recorded claim',
        parent_hypothesis_id:null, assessment:'REFUTED', state:'REFUTED'};
      const nodes = [{...root, hypothesis_id:'root-a'},
        {...root, hypothesis_id:'child-a', parent_hypothesis_id:'root-a', assessment:'INCONCLUSIVE', state:'INCONCLUSIVE'},
        {...root, hypothesis_id:'root-b', proposer_role:'expert-b', assessment:'SUPPORTED', state:'SUPPORTED'},
        {...root, hypothesis_id:'child-b', proposer_role:'expert-b', parent_hypothesis_id:'root-b', assessment:null, state:'PROPOSED'}];
      fixture.activity.graph = {nodes, edges:nodes.filter(n=>n.parent_hypothesis_id)
        .map(n=>({source:n.parent_hypothesis_id,target:n.hypothesis_id,relation:'derived_from'}))};
    })()""")
    page.click("#total-hypotheses")
    page.wait("document.querySelectorAll('.graph-node').length === 4")
    positions = "Object.fromEntries([...document.querySelectorAll('.graph-node')].map(n=>[n.dataset.key,n.getAttribute('transform')]))"
    before = page.evaluate(positions)
    assert "Winner → pruned hypothesis" not in page.text("#graph-legend")
    assert page.evaluate("document.querySelector('#toggle-pruning').disabled")
    page.evaluate("""['root-a','child-a'].forEach(target=>{
      fixture.activity.graph.nodes.find(n=>n.hypothesis_id===target).state='PRUNED';
      fixture.activity.graph.edges.push({source:'child-b',target,relation:'pruned_by',source_attempt_id:'accepted-pruning',score_refs:['recorded-score']});
    })""")
    page.wait("document.querySelectorAll('#graph-edges .pruning-edge').length === 2")
    assert page.text("#toggle-pruning") == "Hide pruning links (2)"
    assert page.evaluate(positions) == before
    # An incoming lineage arrow must not make a pruning origin look like a
    # second arrowhead. Check actual rendered endpoints, not routing formulas.
    assert page.evaluate("""(() => {
      const arrows=[...document.querySelectorAll('#graph-edges .graph-edge-arrow')];
      return [...document.querySelectorAll('#graph-edges .pruning-edge')].every(edge=>{
        const start=edge.getPointAtLength(0), end=edge.getPointAtLength(edge.getTotalLength());
        const own=arrows.filter(a=>a.dataset.relationKind==='pruning'
          && a.dataset.source===edge.dataset.source && a.dataset.target===edge.dataset.target);
        if(own.length!==1) return false;
        const tip=own[0].points.getItem(1);
        if(Math.hypot(end.x-tip.x,end.y-tip.y)>10) return false;
        return arrows.every(a=>{
          const b=a.getBBox();
          return start.x<b.x-4 || start.x>b.x+b.width+4 || start.y<b.y-4 || start.y>b.y+b.height+4;
        });
      });
    })()""")
    assert page.evaluate("""(() => {
      const boxes=[...document.querySelectorAll('.graph-node')].map(n=>{
        const b=n.querySelector('.node-body').getBBox(), m=n.transform.baseVal.consolidate().matrix;
        return {x:b.x+m.e,y:b.y+m.f,w:b.width,h:b.height};
      });
      return [...document.querySelectorAll('#graph-edges .pruning-edge')].every(edge=>{
        for(let d=0;d<edge.getTotalLength();d+=2) {
          const p=edge.getPointAtLength(d);
          if(boxes.some(b=>p.x>b.x+1 && p.x<b.x+b.w-1 && p.y>b.y+1 && p.y<b.y+b.h-1)) return false;
        }
        return true;
      });
    })()""")
    assert "Root hypothesis" in page.text(".graph-node[data-key='hypothesis:root-a']")
    assert "Inconclusive" in page.text(".graph-node[data-key='hypothesis:child-a']")
    assert page.evaluate("document.querySelector('.graph-node[data-key=\"hypothesis:root-a\"] .status-cross') !== null")
    assert page.evaluate("document.querySelector('.graph-node[data-key=\"hypothesis:child-a\"] .status-cross') === null")
    assert page.evaluate("getComputedStyle(document.querySelector('.graph-node[data-key=\"hypothesis:child-a\"] .node-body')).strokeDasharray") == "7px, 4px"
    assert "Winner → pruned hypothesis" in page.text("#graph-legend")
    assert "Running" not in page.text("#graph-legend")
    assert "Not assessed" in page.text("#graph-legend")
    page.evaluate("document.querySelector('#graph-edges .pruning-edge').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))")
    assert "accepted-pruning" in page.text("#research-detail-content")
    assert "recorded-score" in page.text("#research-detail-content")
    page.click("#close-drawer")
    page.click('.graph-node[data-key="hypothesis:child-a"]')
    page.click("#close-drawer")
    view = page.evaluate("document.querySelector('#graph-viewport').getAttribute('transform')")
    page.click("#toggle-pruning")
    assert page.text("#toggle-pruning") == "Show pruning links (2)"
    assert page.evaluate("document.querySelectorAll('#graph-edges .graph-edge').length") == 2
    assert page.evaluate(positions) == before
    assert page.evaluate("document.querySelector('#graph-viewport').getAttribute('transform')") == view
    assert "Winner → pruned hypothesis" not in page.text("#graph-legend")
    assert "Pruned (control, not refutation)" in page.text("#graph-legend")
    refresh = page.evaluate("fixtureRequests.filter(row=>row[1]==='/api/refresh').length")
    page.wait(f"fixtureRequests.filter(row=>row[1]==='/api/refresh').length > {refresh}")
    assert page.text("#toggle-pruning") == "Show pruning links (2)"
    assert page.evaluate("document.querySelector('.graph-node.selected').dataset.key") == "hypothesis:child-a"
    page.click("#toggle-pruning")
    assert page.text("#toggle-pruning") == "Hide pruning links (2)"
    assert page.evaluate(positions) == before
    page.evaluate("document.querySelector('#filter-expert').value='expert-a'; document.querySelector('#filter-expert').dispatchEvent(new Event('change'))")
    assert page.evaluate("document.querySelectorAll('.graph-node').length") == 2
    assert page.evaluate("document.querySelectorAll('#graph-edges .graph-edge').length") == 1
    legend = page.text("#graph-legend")
    assert "expert-a" in legend and "expert-b" not in legend
    assert "Supported" not in legend and "Not assessed" not in legend
    assert "Refuted" in legend and "Inconclusive" in legend
    assert "Winner → pruned hypothesis" not in legend
    assert page.evaluate("document.querySelector('#toggle-pruning').disabled")
    # Mobile layout must retain readable grouped legend entries and controls.
    page.call("Emulation.setDeviceMetricsOverride", {"width":390,"height":844,"deviceScaleFactor":1,"mobile":False})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.evaluate("document.querySelector('#graph-legend').scrollIntoView({block:'end'})")
    page.screenshot(tmp_path / "lineage-legend-mobile.png")
    page.evaluate("document.querySelector('#filter-search').value='no-such-hypothesis'; document.querySelector('#filter-search').dispatchEvent(new Event('input'))")
    assert page.evaluate("document.querySelectorAll('.graph-node').length") == 0
    assert page.text("#graph-legend") == ""
    page.evaluate("""fixture.snapshot.goal.research_contract={components:[
      {id:'component-a',owner:'expert-a',dependencies:[]},
      {id:'component-b',owner:'expert-b',dependencies:['component-a']}]};
      fixture.snapshot.components.push({component_id:'component-b',owner_role:'expert-b',state:'BLOCKED'});
      document.querySelector('#filter-expert').value='';
      document.querySelector('#filter-search').value='';
      document.querySelector('#filter-search').dispatchEvent(new Event('input'))""")
    page.click("#show-components")
    page.wait("document.querySelectorAll('.graph-node').length === 2")
    assert page.evaluate("document.querySelector('#toggle-pruning').hidden")
    assert page.evaluate("document.querySelector('#graph-edges .graph-edge').dataset.relationKind") == "dependency"
    assert "Dependency → dependent" in page.text("#graph-legend")
    assert "READY" in page.text("#graph-legend") and "BLOCKED" in page.text("#graph-legend")
    assert "Pruned" not in page.text("#graph-legend")


def test_browser_shared_goal_presentation_and_exact_acceptance_observations(browser, tmp_path):
    page = browser
    objective = "Improve emotion recognition.\n\n" + "Read real paragraphs safely <script>window.injected=true</script>.\n" * 18
    common = {
        "source_attempt_id": "grounding-accepted",
        "acceptance": {
            "summary": "Improve both held-out scores with valid evaluation.",
            "rule": "ALL",
            "criteria": [{"name": "score", "operator": "GT", "target": "0.7305"},
                         {"name": "reproducible", "operator": "IS_TRUE"}],
            "validity_checks": ["held_out"],
            "parser_argv": ["python", "evaluate.py"],
            "parser_files": ["evaluate.py"], "raw_artifacts": ["trials.json"],
        },
        "outcomes": [{"experiment_id": "experiment-common", "scope": "GOAL",
            "component_id": "component-a", "method_family_id": "candidate-a",
            "outcome_id": "outcome-a", "artifact_id": "artifact-a",
            "source_attempt_id": "grounding-accepted", "local_outcome": "SUCCESS",
            "local_metric_value": "1", "goal_evaluation": {
                "measurements": {"score": "0.7305000000000001"},
                "checks": {"held_out": False, "reproducible": True}}}],
    }
    page.evaluate("fixture.snapshot.goal.research_contract=" + json.dumps({
        "goal": {"objective": objective},
        "methodology": {"controls": {"product": {"goal": {"completion_mode": "shared_goal"}}}},
        "components": [{"id": "component-a", "owner": "expert-a"}],
    }) + "; fixture.snapshot.status.shared_acceptance=" + json.dumps(common)
        + "; fixture.snapshot.status.goal_verdict='INCONCLUSIVE'; fixture.snapshot.components[0].phase='DESIGN'")
    page.wait("document.querySelector('#mission-objective') !== null")
    page.wait("document.querySelector('#mission-objective').textContent.includes('Improve emotion')")
    assert len(page.text("#mission-title")) < 100
    assert page.text("#mission-summary") == "Improve emotion recognition."
    assert page.evaluate("document.querySelector('#mission-objective').closest('details').open") is False
    assert page.evaluate("document.querySelectorAll('#mission-objective p').length") >= 2
    assert page.evaluate("document.querySelector('#mission-objective script') === null && !window.injected")
    assert page.evaluate("document.querySelector('#components-panel').hidden")
    assert page.evaluate("document.querySelector('#filter-component').closest('label').hidden")
    assert "DESIGN" in page.text("#expert-list")
    assert "owns:" not in page.text("#expert-list")
    assert "All conditions must hold (ALL)" in page.text("#shared-acceptance-panel")
    assert "score > 0.7305" in page.text("#shared-acceptance-panel")
    assert "held_out" in page.text("#shared-acceptance-panel")
    assert "0.7305000000000001" in page.text("#shared-acceptance-panel")
    assert "false" in page.text("#shared-acceptance-panel")
    assert page.text("#goal-verdict") == "INCONCLUSIVE"
    assert page.evaluate("Array.from(document.querySelectorAll('#shared-acceptance-panel pre')).every(e => e.closest('details') && !e.closest('details').open)")
    page.screenshot(tmp_path / 'shared-goal-operations.png')
    page.evaluate("document.querySelector('#shared-acceptance-panel').scrollIntoView()")
    page.screenshot(tmp_path / 'shared-acceptance.png')
    page.click("#tab-map")
    assert page.evaluate("document.querySelector('#show-components').hidden")
    assert page.text("#graph-heading") == "Hypothesis lineage"
    page.click("#tab-operations")
    page.evaluate("fixture.snapshot.goal.research_contract.methodology.controls.product.goal.completion_mode='explicit_signals'; fixture.snapshot.status.shared_acceptance.acceptance.rule='ANY'")
    page.wait("!document.querySelector('#components-panel').hidden")
    assert not page.evaluate("document.querySelector('#filter-component').closest('label').hidden")
    assert "Any condition may hold (ANY)" in page.text("#shared-acceptance-panel")
    page.click("#tab-map")
    assert not page.evaluate("document.querySelector('#show-components').hidden")


def test_browser_shared_acceptance_metric_trends_per_hypothesis_chain(browser, tmp_path):
    page = browser

    def outcome(experiment_id, hypothesis_id, measurements):
        return {"experiment_id": experiment_id, "scope": "GOAL",
            "component_id": "component-a", "method_family_id": "candidate-a",
            "outcome_id": f"outcome-{experiment_id}", "artifact_id": f"artifact-{experiment_id}",
            "source_attempt_id": "grounding-accepted", "local_outcome": "SUCCESS",
            "local_metric_value": "1", "hypothesis_id": hypothesis_id,
            "goal_evaluation": {"measurements": measurements, "checks": {"reproducible": True}}}

    common = {
        "source_attempt_id": "grounding-accepted",
        "acceptance": {
            "summary": "Improve both held-out scores.",
            "rule": "ALL",
            "criteria": [
                {"name": "iemocap_weighted_f1_mean", "operator": "GT", "target": "0.7305"},
                {"name": "meld_weighted_f1_mean", "operator": "GT", "target": "0.6674"},
                {"name": "reproducible", "operator": "IS_TRUE"},
            ],
            "validity_checks": [],
        },
        "outcomes": [
            outcome("experiment-a1", "hypothesis-a",
                    {"iemocap_weighted_f1_mean": "0.71", "meld_weighted_f1_mean": "0.65"}),
            outcome("experiment-a2", "hypothesis-b",
                    {"iemocap_weighted_f1_mean": "0.74", "meld_weighted_f1_mean": "not-a-number"}),
            outcome("experiment-b1", "hypothesis-c", {"iemocap_weighted_f1_mean": "0.70"}),
            outcome("experiment-b2", "hypothesis-d", {"iemocap_weighted_f1_mean": "0.72"}),
            outcome("experiment-legacy", None, {"iemocap_weighted_f1_mean": "0.69"}),
        ],
    }
    del common["outcomes"][4]["hypothesis_id"]
    page.evaluate("fixture.snapshot.experts.push({role:'expert-b', slot_ordinal:2, owned_component_ids:['component-b']})")
    page.evaluate("fixture.snapshot.components.push({component_id:'component-b', owner_role:'expert-b', state:'READY'})")
    page.evaluate("fixture.snapshot.hypotheses.push("
        "{hypothesis_id:'hypothesis-c', component_id:'component-b', proposer_role:'expert-b', claim:'expert-b root'},"
        "{hypothesis_id:'hypothesis-d', component_id:'component-b', proposer_role:'expert-b', parent_hypothesis_id:'hypothesis-c', claim:'expert-b child'})")
    page.evaluate("fixture.snapshot.status.shared_acceptance=" + json.dumps(common))
    page.wait("document.querySelectorAll('#shared-acceptance-panel .metric-chart').length === 2")
    assert page.evaluate(
        "Array.from(document.querySelectorAll('.metric-chart')).map(e => e.dataset.metric)"
    ) == ["iemocap_weighted_f1_mean", "meld_weighted_f1_mean"]
    assert page.evaluate("document.querySelectorAll('.metric-target-line').length") == 2
    assert page.evaluate(
        "Array.from(document.querySelectorAll('.metric-target-label')).map(e => e.textContent)"
    ) == ["target > 0.7305", "target > 0.6674"]
    iemocap = ".metric-chart[data-metric='iemocap_weighted_f1_mean']"
    meld = ".metric-chart[data-metric='meld_weighted_f1_mean']"

    def count(selector):
        return page.evaluate(f'document.querySelectorAll("{selector}").length')

    assert count(f"{iemocap} .metric-line") == 3
    assert count(f"{iemocap} .metric-point") == 5
    assert count(f"{meld} .metric-line") == 1
    assert count(f"{meld} .metric-point") == 1
    assert count(f"{iemocap} .metric-line.expert-1") == 2
    assert count(f"{iemocap} .metric-line.expert-2") == 1
    stroke_a = page.evaluate(f'getComputedStyle(document.querySelector("{iemocap} .metric-line.expert-1")).stroke')
    stroke_b = page.evaluate(f'getComputedStyle(document.querySelector("{iemocap} .metric-line.expert-2")).stroke')
    assert stroke_a != stroke_b
    legend = page.text(".metric-legend")
    assert "hypothesis-a" in legend
    assert "component-a (no recorded hypothesis)" in legend
    assert "hypothesis-c" in legend
    assert "not-a-number" not in page.text("#shared-acceptance-panel .metric-trends")
    page.click(".metric-legend-chip")
    page.wait(f'document.querySelectorAll("{iemocap} .metric-line").length === 2')
    assert count(f"{meld} .metric-line") == 0
    assert page.evaluate("document.querySelectorAll('.metric-target-line').length") == 2
    page.click(".metric-legend-chip")
    page.wait(f'document.querySelectorAll("{iemocap} .metric-line").length === 3')
    assert "Published experiment records · 5" in page.text("#shared-acceptance-panel")
    assert page.evaluate("document.querySelector('#shared-acceptance-panel .acceptance-records').open") is False
    assert page.evaluate("document.querySelectorAll('.acceptance-records > article').length") == 5
    page.click(".acceptance-records > summary")
    page.wait("document.querySelector('#shared-acceptance-panel .acceptance-records').open")
    assert "experiment-a1" in page.text(".acceptance-records")
    page.evaluate("document.querySelector('.metric-trends').scrollIntoView()")
    page.screenshot(tmp_path / 'metric-trends.png')


def test_browser_local_only_trends_remain_visible_and_separate_measurements(browser, tmp_path):
    page = browser
    common = {"source_attempt_id": "grounding", "acceptance": {
        "criteria": [{"name": "test_f1", "operator": "GT", "target": "0.7"}]}, "outcomes": []}
    page.evaluate("fixture.snapshot.status.shared_acceptance=" + json.dumps(common))
    page.wait("document.querySelector('.metric-empty') !== null")
    assert "Waiting for published GOAL measurements" in page.text(".metric-trends")
    assert page.evaluate("document.querySelectorAll('.metric-goal-empty').length") == 1
    assert "test_f1" in page.text(".metric-goal-empty")
    assert "Target > 0.7" in page.text(".metric-goal-empty")
    measurement = {"metric": {"name": "validation_delta", "direction": "MAXIMIZE", "target": "0"},
        "comparison": "DELTA_FROM_BASELINE", "parse_rule": "Matched seed validation delta"}
    def local(index, value, **changes):
        return {"scope": "LOCAL", "experiment_id": f"experiment-{index}", "outcome_id": f"outcome-{index}",
            "component_id": "component-a", "hypothesis_id": "hypothesis-a",
            "local_outcome": "CLEAN_REFUTATION", "local_metric_value": value,
            "local_measurement": measurement, "goal_evaluation": None, **changes}
    # One observation is a compact result, with full identity and precision
    # available even when the hypothesis label is long.
    long_id = "hypothesis-" + "long-id-" * 20
    common["outcomes"] = [local(1, "-0.021994627971751002", hypothesis_id=long_id)]
    page.evaluate("fixture.snapshot.status.shared_acceptance=" + json.dumps(common))
    page.wait("document.querySelectorAll('.metric-local .metric-single').length === 1")
    assert page.evaluate("document.querySelector('.metric-local svg') === null")
    assert page.text(".metric-single-value") == "-0.02199"
    assert "-0.021994627971751002" in page.evaluate("document.querySelector('.metric-single-value').title")
    assert len(page.text(".metric-local-context")) < len(long_id)
    assert long_id in page.evaluate("document.querySelector('.metric-local-context').title")
    assert "Matched seed validation delta" in page.evaluate("document.querySelector('.metric-local-context').title")
    assert page.text(".metric-single h5") == "validation_delta"
    assert "1 published observation" in page.text(".metric-single")
    page.evaluate("document.querySelector('.metric-local').scrollIntoView({block:'center'})")
    page.screenshot(tmp_path / "local-single-result.png")
    # A second comparable observation replaces the card with a trend.
    common["outcomes"].append(local(2, "0", hypothesis_id=long_id))
    page.evaluate("fixture.snapshot.status.shared_acceptance=" + json.dumps(common))
    page.wait("document.querySelectorAll('.metric-local .metric-point').length === 2")
    assert page.evaluate("document.querySelectorAll('.metric-single').length") == 0
    assert "2 published observations" in page.text(".metric-local")
    assert page.text(".metric-local figcaption") == "validation_delta"
    common["outcomes"] = [local(10, "0.02"), local(2, "-0.01"), local(3, None), local(4, ""),
        local(5, False), local(6, "0.03", hypothesis_id="hypothesis-b"),
        local(7, "0.04", local_measurement={**measurement, "parse_rule": "Different baseline"})]
    page.evaluate("fixture.snapshot.status.shared_acceptance=" + json.dumps(common))
    page.wait("document.querySelectorAll('.metric-local .metric-single').length === 2")
    assert page.evaluate("document.querySelectorAll('.metric-local .metric-chart').length") == 1
    assert page.evaluate("document.querySelectorAll('.metric-local .metric-point').length") == 2
    assert page.evaluate("Array.from(document.querySelectorAll('.metric-single-value')).map(e => e.textContent)") == ["0.03", "0.04"]
    assert page.evaluate("document.querySelectorAll('.metric-target-line').length") == 0
    titles = page.evaluate("Array.from(document.querySelectorAll('.metric-local .metric-chart:first-child circle title')).map(e => e.textContent)")
    assert titles[0].startswith("experiment-2\n") and "-0.01" in titles[0]
    assert titles[1].startswith("experiment-10\n")
    assert "not GOAL scores" in page.text(".metric-local")
    # First published GOAL observation replaces waiting text but keeps local
    # charts separate. Null and boolean measurements must not fabricate zeros.
    common["outcomes"].append({**local(11, None), "scope": "GOAL", "goal_evaluation": {
        "measurements": {"test_f1": "0.69", "absent": None, "check": False, "empty": ""}}})
    page.evaluate("fixture.snapshot.status.shared_acceptance=" + json.dumps(common))
    page.wait("document.querySelectorAll('.metric-chart').length === 2")
    assert page.evaluate("document.querySelectorAll('.metric-empty').length") == 0
    assert page.evaluate("document.querySelectorAll('.metric-target-line').length") == 1
    assert page.evaluate("document.querySelectorAll('.metric-point').length") == 3
    assert page.evaluate("document.querySelectorAll('.metric-single').length") == 2
    page.evaluate("document.querySelector('.metric-local').scrollIntoView({block:'center'})")
    page.screenshot(tmp_path / "local-and-goal-trends.png")
    common["outcomes"][-1]["goal_evaluation"] = {"measurements": {}, "checks": {"valid": True}}
    page.evaluate("fixture.snapshot.status.shared_acceptance=" + json.dumps(common))
    page.wait("document.querySelector('.metric-empty')?.textContent.includes('No numeric GOAL measurements')")
    assert "Waiting for published" not in page.text(".metric-trends")
    assert page.evaluate("document.querySelectorAll('.metric-local .metric-point').length") == 2
    assert page.evaluate("document.querySelectorAll('.metric-local .metric-single').length") == 2


def test_browser_context_rejection_separates_last_measured_usage_from_current_estimate(browser):
    page = browser
    attempt = {"attempt_id": "context-rejected", "kind": "AGENT", "state": "OPEN", "role": "expert-a", "task_scope": "expert", "owner_role": "expert-a",
        "command": "EXPERIMENT_DESIGN_IMPLEMENT", "context_tokens": None, "context_source": "estimate",
        "context_window_tokens": 262144, "context_window_source": "working_limit_only",
        "cumulative_tokens": 3722840, "context": {"last_observed_exchange": 5,
            "last_observed_input_tokens": 59326, "current_request_exchange": 6,
            "current_request_estimated_input_tokens": 211464,
            "current_request_estimate_source": "provider_usage_plus_delta_estimate",
            "current_request_estimate_reason": "unchanged_prefix", "current_request_fits": False}}
    page.evaluate(f"fixture.snapshot.attempts.push({json.dumps(attempt)})")
    tile = '.agent-tile[data-attempt="context-rejected"]'
    page.wait(f"document.querySelector('{tile}') !== null")
    assert page.text(f"{tile} .agent-tile-value") == "59.33K"
    assert page.evaluate(f"document.querySelector('{tile} .agent-tile-fill').style.width") == "22.6%"
    title = page.evaluate(f"document.querySelector('{tile}').title")
    assert "Latest measured context: 59326 tokens" in title
    assert "Effective context limit: 262144 tokens · bar denominator before reserves" in title
    assert "Current request: exchange 6 · provider input usage pending or unavailable" in title
    assert "Last measured input: 59.33K tokens · exchange 5 (earlier request)" in title
    assert "Current request estimate: 211.46K · prior measured usage plus estimated added input" in title
    assert "exceeds input allowance" in title
    assert "conservative UTF-8 byte estimate" not in title

    page.evaluate("Object.assign(fixture.snapshot.attempts.find(a => a.attempt_id === 'context-rejected').context, "
        "{last_observed_exchange:null,last_observed_input_tokens:null,current_request_estimated_input_tokens:300000,"
        "current_request_estimate_source:'utf8_bytes_conservative_estimate',current_request_estimate_reason:'no_usage_anchor'})")
    page.wait(f"document.querySelector('{tile}').title.includes('Last measured input: unavailable')")
    assert page.text(f"{tile} .agent-tile-value") == "—"
    assert page.evaluate(f"document.querySelector('{tile} .agent-tile-fill').style.width") == "0%"
    title = page.evaluate(f"document.querySelector('{tile}').title")
    assert "Current request estimate: 300K · conservative UTF-8 byte estimate" in title

    page.evaluate("Object.assign(fixture.snapshot.attempts.find(a => a.attempt_id === 'context-rejected'), "
        "{context_tokens:65536,context_source:'provider_usage_final',context:{last_observed_exchange:7,"
        "last_observed_input_tokens:65536,current_request_exchange:7,current_request_estimated_input_tokens:65536,"
        "current_request_estimate_source:'provider_count_tokens',current_request_fits:true}})")
    page.wait(f"document.querySelector('{tile}').title.includes('exchange 7')")
    assert page.text(f"{tile} .agent-tile-value") == "65.54K"
    assert page.evaluate(f"document.querySelector('{tile} .agent-tile-fill').style.width") == "25%"
    title = page.evaluate(f"document.querySelector('{tile}').title")
    assert "Current request estimate: 65.54K · provider token count" in title
    assert "earlier request" not in title


def test_browser_expert_card_working_subagents_and_breathing_dot(browser, tmp_path):
    page = browser
    assert page.evaluate("document.querySelector('.expert-preview h3 .breath-dot') === null")
    assert page.evaluate("document.querySelector('.expert-preview .agent-tile-list') === null")
    assert "scientific process attempts" not in page.text(".expert-identity")
    page.evaluate("fixture.snapshot.overview.experts=[{role:'expert-a',accounting:{reported_tokens:1234,reserved_tokens:5000},context_recovery:{context_failure_attempts:1,recovery_attempts:2,recovery_reported_tokens:1200,recovery_unknown_token_attempts:1,cli_unknown_pricing_attempts:3,cli_pricing_unavailable_attempts:1}}]")
    page.wait("document.querySelector('.expert-preview').textContent.includes('reported ≥1.2K tokens')")
    card = page.text(".expert-preview")
    assert "usage unknown 1" in card and "reserve 5K" in card
    assert "pricing" not in card and "$" not in card
    page.evaluate("fixture.snapshot.overview.host_observation = {availability:'responded'}")
    page.evaluate("fixture.snapshot.attempts.push("
        "{attempt_id:'open-run', kind:'AGENT', state:'OPEN', role:'expert-a', task_scope:'expert', owner_role:'expert-a', command:'EXPERIMENT_EXECUTION', context_tokens:45200, context_source:'transcript', budget_max_tokens:1000000, context_window_tokens:200000, context_window_source:'working_limit_only', cumulative_tokens:3722840, context:{cache_read_input_tokens:40000,cache_creation_input_tokens:1200}, session_scope:'chain-a', session_id:'session-a', started_at:fixture.snapshot.observed_at-720, wall_seconds:2700},"
        "{attempt_id:'open-ana', kind:'AGENT', state:'OPEN', role:'expert-a', task_scope:'expert', owner_role:'expert-a', command:'RESULT_ANALYSIS', context_tokens:null, budget_max_tokens:1000000, session_id:'session-b', started_at:fixture.snapshot.observed_at-3000, wall_seconds:2700},"
        "{attempt_id:'open-exe', kind:'PROCESS', state:'OPEN', role:'expert-a', task_scope:'expert', owner_role:'expert-a', command:'EXECUTE', gpu_uuid:'GPU-0', latest_utilization_percent:87, started_at:fixture.snapshot.observed_at-540},"
        "{attempt_id:'open-other', kind:'AGENT', state:'OPEN', role:'expert-b', task_scope:'expert', owner_role:'expert-b', command:'RESULT_ANALYSIS', context_tokens:1200, session_id:'session-c'})")
    page.wait("document.querySelectorAll('.expert-preview .agent-tile').length === 3")
    assert "Working · 3" in page.text(".expert-preview .working-agents")
    tiles = ".expert-preview .agent-tile"
    assert page.evaluate(f"Array.from(document.querySelectorAll('{tiles} .agent-tile-value')).map(e => e.textContent)") == ["—", "45.2K", "9m"]
    assert page.evaluate(f"Array.from(document.querySelectorAll('{tiles} .agent-tile-fill')).map(e => e.style.width)") == ["0%", "22.6%", "0%"]
    assert page.evaluate(f"document.querySelectorAll('{tiles}.overdue').length") == 1
    assert page.evaluate(f"document.querySelectorAll('{tiles} .agent-tile-light').length") == 3
    assert page.evaluate(f"Array.from(document.querySelectorAll('{tiles} .agent-tile-light')).every(e => getComputedStyle(e).animationName === 'none')")
    assert page.evaluate(f"document.querySelectorAll('{tiles}.host-unconfirmed').length") == 0
    title = page.evaluate(f'document.querySelectorAll("{tiles}")[1].title')
    assert "Experiment execution" in title and "not process liveness" in title
    assert "last input 45.2K / 200K working context limit" in title
    assert "transcript" in title
    assert "Provider window unconfirmed" in title
    assert "Cumulative invocation usage" in title
    assert "Cache read: 40K" in title and "Cache creation: 1.2K" in title
    assert "Session scope: chain-a" in title
    assert "elapsed 12m of 45m wall budget" in title
    page.evaluate("Object.assign(fixture.snapshot.attempts.find(a => a.attempt_id === 'open-run'), {operational_status:'compacting', context:{estimated_input_tokens:180000,available_input_tokens:170000,output_reserve_tokens:8000,tool_reserve_tokens:8192,estimate_margin_tokens:12500},context_observation:{coverage:'complete',native_compaction_start_coverage:'stdout_partial',native_compaction_completion_unobserved:null,native_compaction_count:1,native_compaction_duration_ms:1200,native_compaction_duration_unknown:0,last_native_compaction:{pre_tokens:180000,post_tokens:45000,duration_ms:1200},read_calls:4,exact_reread_calls:1}})")
    page.wait("document.querySelector('.agent-tile[data-attempt=\"open-run\"]').title.includes('Estimated input: 180K')")
    updated = page.evaluate("document.querySelector('.agent-tile[data-attempt=\"open-run\"]').title")
    assert "last input 45.2K" in updated and "Admission input allowance: 170K" in updated
    assert "180000 → 45000" in updated and "Native CLI last reported: compacting" in updated
    assert "exact frozen-input rereads 1" in updated
    assert "transcript coverage complete" in updated and "start-event coverage stdout_partial" in updated
    assert "completion unobserved unknown" in updated
    page.evaluate("fixture.snapshot.attempts.find(a => a.attempt_id === 'open-run').context_observation.cli_unknown_model_pricing = true")
    page.evaluate("fixture.snapshot.attempts.find(a => a.attempt_id === 'open-run').cumulative_tokens = 7654321")
    page.wait("document.querySelector('.agent-tile[data-attempt=\"open-run\"]').title.includes('7.65M tokens')")
    assert "pricing" not in page.evaluate("document.querySelector('.agent-tile[data-attempt=\"open-run\"]').title")
    overdue_title = page.evaluate(f'document.querySelectorAll("{tiles}")[0].title')
    assert "no context reading yet" in overdue_title
    assert "elapsed 50m of 45m wall budget · exceeded" in overdue_title
    process_title = page.evaluate(f'document.querySelectorAll("{tiles}")[2].title')
    assert "Experiment process" in process_title
    assert "elapsed 9m" in process_title
    assert "GPU" not in process_title
    page.evaluate("fixture.snapshot.overview.host_observation={availability:'absent'}")
    page.wait(f"document.querySelectorAll('{tiles}.host-unconfirmed').length === 3")
    assert "3 durably open attempts" in page.evaluate("document.querySelector('.expert-preview h3 .breath-dot').title")
    assert page.evaluate("document.querySelector('.expert-preview h3 .breath-dot').classList.contains('host-unconfirmed')")
    assert page.evaluate("getComputedStyle(document.querySelector('.expert-preview h3 .breath-dot')).animationName") == "none"
    page.evaluate("fixture.activity.experts.push({role:'expert-a', pause_requested:[{scope:{role:'expert-a', component_id:'component-a', chain:'chain-x'}, reason:'debate:agent-5e40-1', command_id:'cmd-pause'}]})")
    page.wait("document.querySelector('.expert-preview .pause-notice') !== null")
    notice = ".expert-preview .pause-notice"
    assert page.evaluate(f"document.querySelector('{notice}').classList.contains('in-debate')") is False
    assert "Debate proposed" in page.text(notice)
    assert "component-a / chain-x" in page.text(notice)
    assert "admitted work may finish" in page.text(notice)
    assert "debate:agent-5e40-1" in page.evaluate(f"document.querySelector('{notice}').title")
    page.evaluate("fixture.snapshot.attempts.push("
        "{attempt_id:'open-debate', kind:'AGENT', state:'OPEN', role:'expert-a', task_scope:'expert', owner_role:'expert-a', command:'DEBATE', context_tokens:88000, context_source:'transcript', budget_max_tokens:1000000, session_id:'session-d', started_at:fixture.snapshot.observed_at-120, wall_seconds:2700},"
        "{attempt_id:'open-lead', kind:'AGENT', state:'OPEN', role:'expert-a', task_scope:'expert', owner_role:'expert-a', command:'LEADER', context_tokens:null, budget_max_tokens:1000000, session_id:'session-e', started_at:fixture.snapshot.observed_at-60, wall_seconds:2700})")
    page.wait(f"document.querySelector('{notice}.in-debate') !== null")
    assert "Debate in progress" in page.text(notice)
    assert page.evaluate(f"document.querySelector('{notice} .pause-link') !== null")
    assert page.evaluate(f"document.querySelectorAll('{tiles}').length") == 5
    assert page.evaluate(f"document.querySelectorAll('{tiles}.debate').length") == 1
    assert page.evaluate(f"document.querySelectorAll('{tiles}.leader').length") == 1
    # A standalone legacy LEADER command does not establish accepted tenure.
    assert page.evaluate("document.querySelector('.expert-preview h3 .leader-crown')") is None
    debate_title = page.evaluate(f'document.querySelector("{tiles}.debate").title')
    assert "Debate round" in debate_title
    leader_title = page.evaluate(f'document.querySelector("{tiles}.leader").title')
    assert "Leader advice" in leader_title
    page.evaluate("document.querySelector('.expert-preview').scrollIntoView({block:'start'})")
    page.evaluate("window.scrollBy(0, -110)")
    page.screenshot(tmp_path / 'expert-working-subagents.png')


def test_browser_global_grounding_is_separate_and_sharing_refreshes(browser, tmp_path):
    from test_observer_scope import record
    from test_observer_summary import _summary
    page = browser
    row = record("global-g", "GROUNDING", kind="GROUNDING", role="expert-a", state="OPEN")
    summary = _summary(tmp_path, attempts=[row], experts=[{"role": "expert-a"}])
    attempt = {**_ATTEMPT, "attempt_id": "global-g", "command": "GROUNDING", "state": "OPEN",
        "task_scope": "run", "owner_role": None}
    page.evaluate("fixture.snapshot.overview=" + json.dumps(summary))
    page.evaluate("fixture.snapshot.attempts=" + json.dumps([attempt]))
    page.wait("document.querySelector('#grounding-agent-card').textContent.includes('GROUNDING')")
    assert 'GROUNDING' not in page.text('#global-task-list')
    assert "GROUNDING" not in page.text(".expert-preview")
    assert "Working" not in page.text(".expert-preview")
    page.click("#grounding-agent-card .grounding-history")
    page.wait("document.querySelector('#research-detail-content').textContent.includes('Grounding sharing')")
    assert "No final accepted" in page.text("#research-detail-content")
    completed = record("global-g", "GROUNDING", kind="GROUNDING", role="expert-a", accepted=True)
    peer = record("next", "RESULT_ANALYSIS", role="expert-a", inputs=[{"ref": "global-g--response"}])
    summary = _summary(tmp_path, attempts=[completed, peer], experts=[{"role": "expert-a"}])
    page.evaluate("fixture.snapshot.overview=" + json.dumps(summary))
    page.wait("document.querySelector('#research-detail-content').textContent.includes('provided in task inputs')")
    assert "does not establish reading or understanding" in page.text("#research-detail-content")
    page.evaluate("window.globalDetailsElement=document.querySelector('#research-detail-content button')")
    page.call("Runtime.evaluate", {"expression": "new Promise(r=>setTimeout(r,1800))", "awaitPromise": True})
    assert page.evaluate("window.globalDetailsElement===document.querySelector('#research-detail-content button')")
    from hypoloom.observer_details import _sessions
    page.evaluate("fixture.details['sessions:0']=" + json.dumps(_sessions([completed, peer], {})))
    page.evaluate("Array.from(document.querySelectorAll('#research-detail-content button')).find(b=>b.textContent==='Browse task history').click()")
    page.wait("fixtureRequests.some(r=>r[1].includes('kind=sessions') && r[1].includes('task_scope=run'))")
    assert "Global tasks" in page.text("#workbench-scope")
    page.wait("document.querySelector('#workbench-list').textContent.includes('GROUNDING')")
    assert "RESULT_ANALYSIS" not in page.text("#workbench-list")


def test_browser_exhausted_literature_explains_stop_and_opens_failure_details(browser, tmp_path):
    from test_observer_scope import exhausted_literature_summary, record
    from test_observer_summary import _summary
    page = browser
    summary = exhausted_literature_summary(tmp_path, role="expert-a")
    page.evaluate("fixture.snapshot.overview=" + json.dumps(summary))
    page.evaluate("fixture.snapshot.attempts=[]")
    card = '.expert-preview[data-expert-role="expert-a"]'
    page.wait(f"document.querySelector('{card}').textContent.includes('Automatic recovery ended')")
    assert "Literature preparation" in page.text(card)
    assert "3 attempts" in page.text(card) and "literature_fetch_failed" in page.text(card)
    assert "Last recorded work" not in page.text(card)
    page.evaluate("Object.assign(fixture.details['attempt:0'],{available:false,events:[],attempt:{attempt_id:'failed-3',failure_code:'literature_fetch_failed',failure_stage:'literature_retrieval',diagnostic_summary:'landing_uri is not globally routable'}})")
    page.click('[data-expert-details="expert-a"]')
    page.wait("document.querySelector('#research-detail-content').textContent.includes('API retry does not reopen it')")
    page.evaluate("Array.from(document.querySelectorAll('#research-detail-content button')).find(b=>b.textContent==='Failure details').click()")
    page.wait("fixtureRequests.some(r=>r[1].includes('kind=attempt') && r[1].includes('attempt_id=failed-3'))")
    page.wait("document.querySelector('#research-detail-status').textContent.includes('Recorded failure')")
    assert "landing_uri is not globally routable" in page.text('#research-detail-content')
    assert "before model invocation" in page.text('#research-detail-content')
    # A later cycle can still show current work without carrying the old alert.
    fresh = _summary(tmp_path, attempts=[record("next", "LITERATURE_REVIEW", role="expert-a", cycle=2, state="OPEN")],
                     experts=[{"role": "expert-a"}])
    page.evaluate("fixture.snapshot.overview=" + json.dumps(fresh))
    page.wait(f"document.querySelector('{card}').textContent.includes('Cycle 2')")
    assert "Automatic recovery ended" not in page.text(card)
    assert "1 OPEN" in page.text(card)


@pytest.mark.parametrize("terminal", ["leader_round_finished", "leader_aborted", "leader_failed"])
def test_browser_leader_crown_follows_accepted_round_across_tasks_and_idle(browser, tmp_path, terminal):
    from test_observer_summary import _summary, _transition

    page = browser
    selector = '.expert-preview[data-expert-role="expert-a"] h3 .leader-crown'
    peer = '.expert-preview[data-expert-role="expert-b"] h3 .leader-crown'
    leader = {"role": "expert-a", "component_id": "component-a", "cycle": 4}
    transitions = []

    def publish(action):
        transitions.append(_transition(len(transitions) + 1, payload={"expert_interaction": {
            "action": action, "source_attempt_id": "observer-source", "leader": leader}}))
        summary = _summary(tmp_path, transitions=transitions,
            experts=[{"role": role} for role in ("expert-a", "expert-b")])
        page.evaluate("fixture.snapshot.overview.experts=" + json.dumps(summary["experts"]))

    page.evaluate("fixture.snapshot.experts.push({role:'expert-b', slot_ordinal:2}); fixture.snapshot.attempts=[]")
    publish("leader_selected")
    page.wait("document.querySelectorAll('.expert-preview').length === 2")
    assert page.evaluate(f"document.querySelector('{selector}')") is None
    publish("leader_round_started")
    page.wait(f"document.querySelector('{selector}') !== null")
    assert page.evaluate(f"document.querySelector('{selector}').getAttribute('aria-label')") == "Active Leader"
    assert "advice or retained round semantics" in page.evaluate(f"document.querySelector('{selector}').title")
    assert page.evaluate(f"document.querySelector('{peer}')") is None

    # Current Leader work uses ordinary node commands; no LEADER Attempt exists.
    for command, label in (("HYPOTHESIS_PROPOSAL", "Hypothesis proposal"), ("CYCLE_REPORT", "Cycle report")):
        attempt = {"attempt_id": "leader-work", "kind": "AGENT", "state": "OPEN", "role": "expert-a", "task_scope": "expert", "owner_role": "expert-a",
            "command": command, "started_at": 100, "context_tokens": None}
        page.evaluate("fixture.snapshot.attempts=" + json.dumps([attempt]))
        tile = '.expert-preview[data-expert-role="expert-a"] .agent-tile[data-attempt="leader-work"]'
        page.wait(f"document.querySelector('{tile}')?.title.startsWith('{label}')")
        assert page.evaluate(f"document.querySelector('{selector}').textContent") == "♛"
    page.evaluate("fixture.snapshot.attempts=[]; fixture.snapshot.overview.host_observation={availability:'absent'}")
    page.wait("document.querySelectorAll('.expert-preview .working-agents').length === 0")
    assert page.evaluate(f"document.querySelector('{selector}').textContent") == "♛"

    # History remains, but completion/failure/abort removes active status even
    # with an ordinary task still OPEN. The next expert must not inherit it.
    page.evaluate("fixture.snapshot.attempts=" + json.dumps([attempt]))
    publish(terminal)
    page.wait(f"document.querySelector('{selector}') === null")
    assert page.evaluate("fixture.snapshot.overview.experts[0].leader.started") == 1
    assert page.evaluate(f"document.querySelector('{peer}')") is None
    page.evaluate("fixture.snapshot.overview.experts=[]")
    page.wait("document.querySelectorAll('.leader-crown').length === 0")


def test_browser_parallel_proposals_do_not_appear_as_hypotheses_or_readers(browser, tmp_path):
    from test_observer_summary import _summary, _attempt
    rows = [_attempt(name, role="expert-a", node="HYPOTHESIS_PROPOSAL", subkind=name, state="OPEN", chain=None)
        for name in ("FORWARD", "BACKWARD")]
    summary = _summary(tmp_path, attempts=rows, experts=[{"role": "expert-a"}])
    browser.evaluate("fixture.snapshot.overview.experts=" + json.dumps(summary["experts"]))
    card = '.expert-preview[data-expert-role="expert-a"]'
    browser.wait(f"document.querySelector('{card} .expert-current-work').textContent.includes('FORWARD')")
    compact = browser.text(card)
    for detail in ("Active research chains", "hypotheses:", "FORWARD:", "Reading assistants:", "Reading tool:"):
        assert detail not in compact
    assert "Hypothesis proposal" in compact  # Current work stays visible.
    browser.click('[data-expert-details="expert-a"]')
    selector = '#research-detail-content .expert-parallel-work'
    browser.wait(f"document.querySelector('{selector}') !== null")
    assert "expert-a" in browser.text('#research-detail-title')
    text = browser.text(selector)
    assert "FORWARD: OPEN" in text and "BACKWARD: OPEN" in text
    assert "SYNTHESIS: not dispatched" in text
    assert "hypotheses: 0 accepted" in text and "Reading assistants: 0 tasks" in text
    assert "≥0 rejected" in text and "2 attempt traces incomplete/unavailable" in text
    failed = _attempt("invalid-output", role="expert-a", node="HYPOTHESIS_PROPOSAL",
        subkind="SYNTHESIS", reason="FAILED", chain=None, start=25, end=30)
    closed_wings = [{**row, "state": "CLOSED", "terminal_reason": "SUCCEEDED", "ended_at": 20} for row in rows]
    summary = _summary(tmp_path, attempts=[*closed_wings, failed], experts=[{"role": "expert-a"}])
    browser.evaluate("fixture.snapshot.overview.experts=" + json.dumps(summary["experts"]))
    browser.wait(f"document.querySelector('{selector}').textContent.includes('SYNTHESIS: FAILED')")
    assert "hypotheses: 0 accepted" in browser.text(selector)
    assert "not submitted" not in browser.text(selector)
    assert "SYNTHESIS: FAILED" not in browser.text(card)
    browser.evaluate("window.retainedExpertDetails = document.querySelector('#research-detail-content .expert-parallel-work')")
    polls = browser.evaluate("fixtureRequests.length")
    browser.wait(f"fixtureRequests.length > {polls}")
    assert browser.evaluate("window.retainedExpertDetails === document.querySelector('#research-detail-content .expert-parallel-work')")
    browser.screenshot(tmp_path / 'expert-details.png')
    browser.evaluate("Array.from(document.querySelectorAll('#research-detail-content button')).find(button => button.textContent === 'Browse expert history').click()")
    browser.wait("document.querySelector('#evidence-drawer').hidden")
    assert browser.evaluate("document.querySelector('#filter-expert').value") == "expert-a"
    assert "Sessions & tools" in browser.text('#workbench-title')


def test_browser_refresh_is_sequential_pauses_hidden_and_preserves_unchanged_dom(browser):
    page = browser
    assert page.evaluate("fixtureRequests.some(row => row[1] === '/api/refresh')")
    assert page.evaluate("fixtureRequests.every(row => !['/api/snapshot','/api/activity'].includes(row[1]))")
    page.evaluate("window.hiddenMutations=0; new MutationObserver(rows => window.hiddenMutations+=rows.length).observe(document.querySelector('#view-map'), {subtree:true, childList:true, characterData:true})")
    before = page.evaluate("fixtureRequests.length")
    page.wait(f"fixtureRequests.length > {before}")
    assert page.evaluate("window.hiddenMutations") == 0
    page.click("#total-hypotheses")
    page.wait("document.querySelectorAll('.graph-node').length === 2")
    page.evaluate("window.previousNode=document.querySelector('.graph-node'); window.fixtureDelay=4200")
    before = page.evaluate("fixtureRequests.length")
    page.wait(f"fixtureRequests.length > {before}")
    time.sleep(3.3)
    assert page.evaluate("fixtureRequests.length") == before + 1
    assert page.evaluate("window.previousNode === document.querySelector('.graph-node')")
    page.evaluate("Object.defineProperty(document, 'hidden', {configurable:true, value:true}); document.dispatchEvent(new Event('visibilitychange'))")
    time.sleep(4)
    assert page.evaluate("fixtureRequests.length") == before + 1
    assert "PAUSED" in page.text("#connection-text")
    page.evaluate("window.fixtureDelay=0; Object.defineProperty(document, 'hidden', {configurable:true, value:false}); document.dispatchEvent(new Event('visibilitychange'))")
    page.wait(f"fixtureRequests.length > {before + 1}")
    page.wait("document.querySelector('#connection-text').textContent === 'CONNECTED'")


def test_browser_history_pages_audit_age_and_failed_refresh_keep_evidence(browser):
    page = browser
    page.evaluate("fixture.activity.events_page={offset:0,limit:100,total:101,has_more:true}; fixture.activity.experiments_page={offset:0,limit:50,total:51,has_more:true}; fixture.snapshot.oracle={ok:null,issues:[],observation:{state:'pending',audited_at:null,refreshing:true}}")
    page.wait("document.querySelector('#oracle-state').textContent === 'AUDIT PENDING'")
    page.click("#tab-activity")
    page.evaluate("window.fixtureHistoryDelay=500")
    page.click("#events-older")
    page.click("#tab-evidence")
    time.sleep(0.7)
    page.click("#tab-activity")
    page.wait("document.querySelector('#product-events').textContent.includes('OLDER_EVENT')")
    page.click("#experiments-older")
    page.wait("document.querySelector('#experiment-queue').textContent.includes('old-experiment')")
    page.evaluate("fixture.snapshot.oracle={ok:true,issues:[],observation:{state:'historical',audited_at:100,refreshing:true}}")
    page.wait("document.querySelector('#oracle-state').textContent === 'LAST CLEAN'")
    assert "OLDER_EVENT" in page.text("#product-events")
    assert "old-experiment" in page.text("#experiment-queue")
    page.click("#tab-evidence")
    assert "Historical audit" in page.text("#evidence-page-oracle")
    assert "refresh pending" in page.text("#evidence-page-oracle")
    page.evaluate("window.fixtureError=true")
    page.wait("document.querySelector('#connection-text').textContent === 'RECONNECTING'")
    before = page.evaluate("fixtureRequests.length")
    time.sleep(3.3)
    assert page.evaluate("fixtureRequests.length") == before
    assert "Historical audit" in page.text("#evidence-page-oracle")
    page.evaluate("window.fixtureError=false")
    page.wait("document.querySelector('#connection-text').textContent === 'CONNECTED'")
    page.click("#tab-activity")
    page.click("#events-latest")
    page.wait("!document.querySelector('#product-events').textContent.includes('OLDER_EVENT')")
    page.click("#experiments-latest")
    page.wait("document.querySelector('#experiment-queue').textContent.includes('experiment-a')")


@pytest.mark.parametrize('browser', ['pending'], indirect=True)
def test_browser_cold_poll_and_authentication_states(browser):
    page = browser
    before = page.evaluate('fixtureRequests.length')
    page.wait(f'fixtureRequests.length > {before}')
    assert page.text('#connection-text') == 'PREPARING SNAPSHOT'
    assert page.evaluate("document.querySelector('#connection').dataset.state") == 'connecting'
    page.evaluate('window.fixtureRefresh=null')
    page.wait("document.querySelector('#total-skills strong')?.textContent === '2'")
    page.wait("document.querySelector('#connection-text').textContent === 'CONNECTED'")
    page.evaluate('window.fixtureRefresh={}; window.fixtureRefreshStatus=401')
    page.wait("document.querySelector('#connection-text').textContent === 'TOKEN REJECTED'")
    time.sleep(1.2)
    assert page.evaluate("document.querySelector('#connection').dataset.state") == 'error'
    assert page.text('#connection-text') == 'TOKEN REJECTED'
    page.evaluate('window.fixtureRefresh=null; window.fixtureRefreshStatus=200')
    page.wait("document.querySelector('#connection-text').textContent === 'CONNECTED'")


def test_browser_run_trends_interaction_unknowns_and_responsive_layout(browser, tmp_path):
    page = browser
    points = [dict(at=1800000000+i*60, until=1800000060+i*60,
        experiments=i%3, experiments_peak=3, agents=(i%4)+1, agents_peak=5,
        rpm=8 if i != 58 else None, rpm_lower_bound=8 if i != 58 else 2) for i in range(120)]
    values = [3] * 120
    data = dict(observed_at=1800007200, ended_at=1800007200, interval_seconds=60, points=points,
        coverage=dict(request_count=950, incomplete_attempts=1),
        experts=[dict(role="expert <script>injected=true</script>", rpm=values, rpm_lower_bound=values)],
        grounding=dict(rpm=values, rpm_lower_bound=values), other=dict(rpm=[2]*120, rpm_lower_bound=[2]*120))
    page.evaluate("window.trendFixture=" + json.dumps(data))
    page.evaluate("""window.trendCalls=0; window.baseFetch=window.fetch;
      window.fetch=async (path, options) => {
        if (path !== '/api/trends') return baseFetch(path, options);
        trendCalls++; fixtureRequests.push(['GET',path,options.headers.Authorization]);
        if(window.trendError) return {ok:false,status:503};
        return {ok:true,status:200,json:async()=>structuredClone(trendFixture)};
      };""")
    assert page.evaluate("trendCalls") == 0
    page.click("#open-trends")
    page.wait("document.querySelectorAll('#trends-rpm svg path').length === 4")
    assert "950" in page.text("#trends-coverage")
    assert page.evaluate("window.injected === undefined")
    page.evaluate("document.querySelector('#trends-cursor').value=58; document.querySelector('#trends-cursor').dispatchEvent(new Event('input'))")
    assert "unknown; recorded ≥2 RPM" in page.text("#trends-inspect")
    page.click("#trends-rpm-legend button")
    assert page.evaluate("document.querySelectorAll('#trends-rpm svg path').length") == 3
    assert page.evaluate("document.querySelector('#trends-rpm-legend button').getAttribute('aria-pressed')") == "false"
    page.evaluate("document.querySelector('#trends-range').value='3600'; document.querySelector('#trends-range').dispatchEvent(new Event('change'))")
    assert int(page.evaluate("document.querySelector('#trends-cursor').max")) < 119
    before = page.evaluate("fixtureRequests.filter(r=>r[1]==='/api/refresh').length")
    page.wait(f"fixtureRequests.filter(r=>r[1]==='/api/refresh').length > {before}")
    page.evaluate("window.trendError=true")
    page.click("#refresh-trends")
    page.wait("document.querySelector('#trends-status').textContent.includes('unavailable')")
    assert page.evaluate("document.querySelectorAll('#trends-rpm svg path').length") == 3
    page.screenshot(tmp_path / "trends-desktop.png")
    page.click("#close-trends")
    page.click("#language-toggle")
    page.evaluate("window.trendError=false")
    page.click("#open-trends")
    page.wait("document.querySelector('#trends-status').textContent.includes('观测时间')")
    assert page.text("#trends-title") == "运行趋势"
    page.call("Emulation.setDeviceMetricsOverride", {"width":390, "height":844, "deviceScaleFactor":1, "mobile":False})
    assert page.evaluate("document.querySelector('#trends-dialog').scrollWidth <= document.querySelector('#trends-dialog').clientWidth")
    page.screenshot(tmp_path / "trends-mobile.png")
    page.click("#close-trends")
    assert page.evaluate("fixtureRequests.filter(r=>r[1]==='/api/trends').every(r=>r[2]==='Bearer browser-fixture-token')")
    assert not page.errors


def test_browser_cached_poll_tracks_data_age_and_preserves_evidence(browser):
    page = browser
    page.click('#total-hypotheses')
    page.wait("document.querySelectorAll('.graph-node').length === 2")
    page.evaluate("window.retainedGraph=document.querySelector('.graph-node'); window.fixtureRefresh={unchanged:true, observation:{state:'ready', age_seconds:4, refreshing:true}, oracle:{ok:null,issues:[],observation:{state:'pending'}}}")
    page.wait("document.querySelector('#connection-text').textContent === 'CONNECTED · UPDATING'")
    assert page.evaluate("window.fixtureObservedAt === String(fixture.snapshot.observed_at)")
    assert page.evaluate("window.retainedGraph === document.querySelector('.graph-node')")
    assert 'AUDIT PENDING' in page.text('#oracle-state')
    page.evaluate("window.fixtureRefresh.observation.age_seconds=40")
    page.wait("document.querySelector('#connection-text').textContent.includes('40s OLD')")
    assert page.evaluate("document.querySelector('#connection').dataset.state") == 'stale'
    # A successful HTTP reply containing old data must not make it live again.
    before = page.evaluate('fixtureRequests.length')
    page.wait(f'fixtureRequests.length > {before}')
    assert page.evaluate("document.querySelector('#connection').dataset.state") == 'stale'
    assert page.evaluate("window.retainedGraph === document.querySelector('.graph-node')")
    page.evaluate("window.fixtureRefresh.observation.state='error'")
    page.wait("document.querySelector('#connection-text').textContent === 'REFRESH FAILED · RETRYING'")
    page.evaluate("window.fixtureRefreshStatus=202; window.fixtureRefresh={snapshot:null,activity:null,observation:{state:'pending',age_seconds:null,refreshing:true}}")
    before = page.evaluate('fixtureRequests.length')
    page.wait(f'fixtureRequests.length > {before}')
    assert page.evaluate("document.querySelector('#connection').dataset.state") == 'stale'
    assert page.evaluate("window.retainedGraph === document.querySelector('.graph-node')")
    page.evaluate("window.fixtureRefreshStatus=200; window.fixtureRefresh={snapshot:fixture.snapshot,activity:fixture.activity,observation:{state:'ready',age_seconds:0,refreshing:false}}")
    page.wait("document.querySelector('#connection-text').textContent === 'CONNECTED'")


def test_browser_slow_valid_poll_and_real_timeout_are_distinct(browser):
    page = browser
    page.evaluate('window.fixtureDelay=5500')
    before = page.evaluate('fixtureRequests.length')
    page.wait(f'fixtureRequests.length > {before}')
    time.sleep(4.3)  # More than seven seconds since the preceding good read.
    assert page.evaluate("document.querySelector('#connection').dataset.state") == 'live'
    time.sleep(1.5)
    assert page.text('#connection-text') == 'CONNECTED'
    page.evaluate('window.fixtureDelay=9000')
    before = page.evaluate('fixtureRequests.length')
    page.wait(f'fixtureRequests.length > {before}')
    page.wait("document.querySelector('#connection-text').textContent === 'POLL TIMEOUT'")
    time.sleep(1.2)
    assert page.text('#connection-text') == 'POLL TIMEOUT'
    page.evaluate('window.fixtureDelay=0')
    page.wait("document.querySelector('#connection-text').textContent === 'CONNECTED'")


def test_browser_slow_selected_detail_does_not_block_polling(browser):
    page = browser
    page.evaluate('window.fixtureDetailDelay=9000')
    page.click('#total-skills')
    before = page.evaluate("fixtureRequests.filter(row => row[1] === '/api/refresh').length")
    page.wait(f"fixtureRequests.filter(row => row[1] === '/api/refresh').length > {before}")
    first = time.monotonic()
    page.wait(f"fixtureRequests.filter(row => row[1] === '/api/refresh').length > {before + 1}")
    assert time.monotonic() - first < 5
    assert page.text('#connection-text') == 'CONNECTED'


def test_browser_literature_download_reasons_keep_unknown_and_attempt_context(browser):
    page = browser
    page.click("#total-literature")
    page.wait("document.querySelector('#workbench-list .download-evidence') !== null")
    assert "Download history unavailable" in page.text("#workbench-list")
    page.evaluate("fixture.details['literature:0'].papers[0].download_coverage='recorded'; fixture.details['literature:0'].papers[0].download_attempts=[{attempt_id:'grounding-a',role:'grounding',retrieval_disposition:'METADATA_ONLY',events:[{stage:'download',outcome:'failed',source_host:'publisher.example',failure_code:'http_error',http_status:403},{stage:'fallback',outcome:'available',source_host:'arxiv.org'}]}]")
    page.click("#refresh-workbench")
    page.wait("document.querySelector('#workbench-list .download-evidence').textContent.includes('403')")
    assert not page.evaluate("document.querySelector('#workbench-list .download-evidence').open")
    page.click("#workbench-list .download-evidence summary")
    detail = page.text("#workbench-list .download-evidence")
    assert all(value in detail for value in ("grounding-a", "grounding", "METADATA_ONLY", "publisher.example", "http_error", "403", "arxiv.org"))
    page.evaluate("fixture.details['literature:0'].papers[0].download_attempts[0]={attempt_id:'grounding-a',role:'grounding',retrieval_disposition:'PDF_UNAVAILABLE',events:[{stage:'extraction',outcome:'EXTRACTION_FAILURE',source_host:'repository.example',failure_code:'POPPLER_INVALID'}]}")
    page.click("#refresh-workbench")
    page.wait("document.querySelector('#workbench-list .download-evidence').textContent.includes('PDF downloaded, but evidence extraction failed')")
    assert "PDFs with extracted evidence" in page.text("#total-literature")
    page.evaluate("Object.assign(fixture.details['literature:0'].papers[0],{landing_uri:'https://rejected.example/paper',source_uri:'https://catalog.example/paper',download_attempts:[{attempt_id:'lit-a',role:'expert-a',retrieval_disposition:'PDF_UNAVAILABLE',events:[{stage:'landing_uri',outcome:'VALIDATION_FAILURE',source_host:'rejected.example',failure_code:'VALIDATION_FAILED'}]}]})")
    page.click("#refresh-workbench")
    page.wait("document.querySelector('#workbench-list').textContent.includes('Source link withheld')")
    assert not page.evaluate("Boolean(document.querySelector('#workbench-list a[href^=\"https://rejected.example\"]'))")
    assert page.evaluate("Boolean(document.querySelector('#workbench-list a[href^=\"https://catalog.example\"]'))")
    assert "Metadata retained" in page.text("#workbench-list .download-evidence")


def test_browser_expert_previews_refresh_current_work_and_cumulative_metrics(browser, tmp_path):
    page = browser
    reliability = _reliability()
    expert = {"role": "expert-a", "cycle": 2, "reliability": reliability,
        "accounting": {"reported_tokens": 1234567, "reserved_tokens": 80000,
            "unknown_closed_attempts": 1, "unavailable_token_attempts": 0},
        "activity": [
            {"node": "EXPERIMENT_DESIGN_IMPLEMENT", "state": "OPEN", "chain": "forward", "elapsed_seconds": 70},
            {"node": "EXPERIMENT_EXECUTION", "kind": "PROCESS", "state": "OPEN", "experiment_id": "job-a", "elapsed_seconds": 120},
            {"node": "EXPERIMENT_DESIGN_IMPLEMENT", "state": "OPEN", "chain": "backward <script>window.injected=true</script>"}]}
    # Publish the fixture atomically; an intervening poll could render the new
    # expert activity with the old completion mode and satisfy the wait below.
    page.evaluate("fixture.snapshot.overview.experts=" + json.dumps([expert, {"role": "expert-b", "cycle": 0}])
        + "; fixture.snapshot.experts.push({role:'expert-b',slot_ordinal:2}); fixture.snapshot.overview.host_observation={availability:'responded'}; fixture.snapshot.goal.research_contract={methodology:{controls:{product:{goal:{completion_mode:'shared_goal'}}}}}")
    page.wait("document.querySelector('.expert-work-status').textContent.includes('3 OPEN')")
    assert "Design / implementation" in page.text("#expert-list")
    assert "Experiment execution · process · job-a · OPEN" in page.text("#expert-list")
    assert "1.23M tok · reserve 80K" in page.text(".expert-token-usage")
    assert page.evaluate("document.querySelector('[data-stage=EXPERIMENT_DESIGN_IMPLEMENT]').textContent") == "DES ×2"
    assert page.evaluate("document.querySelector('.expert-current-work script') === null && !window.injected")
    assert page.evaluate("getComputedStyle(document.querySelector('#experts-panel')).gridColumnEnd") == "-1"
    assert page.evaluate("document.querySelector('.expert-work').getBoundingClientRect().left > document.querySelector('.expert-identity').getBoundingClientRect().right")
    global_before = page.text("#run-reliability-preview")
    for abbreviation, name in (("FP", "First output pass"), ("CR", "Correction recovery"),
            ("RT", "Nodes needing retry"), ("DL", "Final delivery"), ("RA", "Required actions")):
        assert abbreviation in global_before and name in global_before
    assert page.evaluate("document.querySelectorAll('#run-reliability-preview .mini-name').length") == 5
    page.evaluate("document.querySelector('#run-reliability-preview').scrollIntoView({block:'center'})")
    page.screenshot(tmp_path / "run-reliability-desktop.png")
    assert "1/2" in global_before and "50%" in global_before and "?1" in global_before
    assert "0/0" in global_before and "—" in global_before
    assert "?/?" in page.text('[data-expert-role="expert-b"] .reliability-preview')
    page.evaluate("document.querySelector('#experts-panel').scrollIntoView({block:'start'})")
    page.screenshot(tmp_path / "expert-previews-desktop.png")
    page.evaluate("window.previewButton=document.querySelector('#run-reliability-preview button'); previewButton.focus()")
    page.evaluate("fixture.snapshot.overview.experts[0].activity[0].state='CLOSED'; fixture.snapshot.overview.experts[0].activity[0].terminal_reason='SUCCEEDED'; fixture.snapshot.overview.experts[0].reliability.metrics.output_first_pass={numerator:2,denominator:3,rate:2/3,unknown:0,excluded:1}")
    page.wait("document.querySelector('.expert-work-status').textContent.includes('2 OPEN')")
    assert "2/3" in page.text('[data-expert-role="expert-a"] .reliability-preview')
    assert "67% · −1" in page.text('[data-expert-role="expert-a"] .reliability-preview')
    assert page.text("#run-reliability-preview") == global_before
    assert page.evaluate("document.activeElement === previewButton")
    page.evaluate("fixture.snapshot.overview.host_observation.availability='absent'")
    page.wait("document.querySelector('.expert-work-status').textContent.includes('Host absent')")
    assert page.evaluate("document.querySelector('[data-stage=EXPERIMENT_EXECUTION]').classList.contains('stage-unconfirmed')")
    page.evaluate("document.querySelector('#filter-expert').value='expert-a'; document.querySelector('#filter-expert').dispatchEvent(new Event('change'))")
    assert page.evaluate("document.querySelectorAll('.expert-preview').length") == 1
    assert page.text("#run-reliability-preview") == global_before
    page.call("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": False})
    assert page.evaluate("document.querySelector('.expert-work').getBoundingClientRect().top >= document.querySelector('.expert-identity').getBoundingClientRect().bottom")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.evaluate("Array.from(document.querySelectorAll('#run-reliability-preview .mini-name')).every(e => e.scrollWidth <= e.clientWidth && e.scrollHeight <= e.clientHeight)")
    page.evaluate("document.querySelector('#run-reliability-preview').scrollIntoView({block:'center'})")
    page.screenshot(tmp_path / "run-reliability-mobile.png")
    page.evaluate("document.querySelector('#experts-panel').scrollIntoView({block:'start'})")
    page.screenshot(tmp_path / "expert-previews-mobile.png")
    page.click('[data-expert-role="expert-a"] [data-preview-metric="node_delivery"]')
    page.wait("document.querySelector('#reliability-owner_role')?.value === 'expert-a'")
    assert page.evaluate("document.querySelector('#reliability-task_scope').value") == "expert"
    assert page.evaluate("document.querySelector('#reliability-role').value") == ""
    assert "Final delivery" in page.text("#reliability-list-label")
    page.click('#run-reliability-preview [data-preview-metric="node_delivery"]')
    page.wait("document.querySelector('#reliability-owner_role')?.value === ''")


def test_browser_graph_text_fits_cards_and_keeps_full_details(browser, tmp_path):
    page = browser
    claim = "多模态因果证据与长上下文推理的联合假设，必须验证每一种干预情景及其可复现性。" * 4 + " FULL CLAIM END"
    identity = "假设节点—" + "WIDE_IDENTIFIER_" * 8
    page.evaluate("fixture.activity.graph=" + json.dumps({"nodes": [
        {"hypothesis_id": identity, "component_id": "component-a", "proposer_role": "expert-a",
         "method_family_id": "W" * 100, "chain": "超长并行假设链条" * 15, "claim": claim,
         "cycle": 3, "assessment": "REFUTED"},
        {"hypothesis_id": "wide-latin", "component_id": "component-a", "proposer_role": "expert-a",
         "claim": "W" * 300, "assessment": "SUPPORTED"},
    ], "edges": []}, ensure_ascii=False))
    page.click("#total-hypotheses")
    page.wait("document.querySelector('.graph-node[data-key=\"hypothesis:wide-latin\"]') !== null")
    long_selector = '.graph-node[data-key=' + json.dumps('hypothesis:' + identity, ensure_ascii=False) + ']'
    # Compare SVG geometry, not character counts or the implementation's fitter.
    overflows = """[...document.querySelectorAll('.graph-node')].flatMap(node => {
      const box=node.querySelector('.node-body').getBBox();
      return [...node.querySelectorAll('text')].filter(text => {
        const b=text.getBBox(); return b.x < box.x+12 || b.x+b.width > box.x+box.width-12
          || b.y < box.y+6 || b.y+b.height > box.y+box.height-6;
      }).map(text => ({node:node.dataset.key, text:text.textContent}));
    })"""
    assert page.evaluate(overflows) == []
    assert page.evaluate("[...document.querySelectorAll('.node-id')].some(n => n.textContent.endsWith('…'))")
    assert page.evaluate("[...document.querySelectorAll('.graph-node .node-detail')].every(n => n.querySelectorAll('tspan').length === 2)")
    assert page.evaluate(f"document.querySelector({json.dumps(long_selector)}).querySelector('title').textContent.includes('FULL CLAIM END')")
    page.click(long_selector)
    assert claim in page.text("#selection-detail")
    assert identity in page.text("#selection-detail")
    page.click("#close-drawer")
    page.call("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": False})
    page.click("#fit-graph")
    assert page.evaluate(overflows) == []
    page.call("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
    page.click("#fit-graph")
    page.evaluate("window.scrollTo(0, document.querySelector('.graph-panel').getBoundingClientRect().top + window.scrollY - 150)")
    page.screenshot(tmp_path / "hypothesis-fitted-text.png")


def test_browser_graph_failure_cross_is_independent_of_pruning_and_parent_failure(browser, tmp_path):
    page = browser
    cases = [("refuted", "REFUTED", "REFUTED"), ("failed", "INCONCLUSIVE", "FAILED"),
        ("pruned-refuted", "REFUTED", "PRUNED"), ("pruned-only", "SUPPORTED", "PRUNED"),
        ("cancelled", "INCONCLUSIVE", "CANCELLED"), ("pending", "UNASSESSED", "PROPOSED"),
        ("supported", "SUPPORTED", "SUPPORTED")]
    nodes = [{"hypothesis_id": identity, "component_id": "component-a", "proposer_role": "expert-a",
        "claim": "Retain the recorded scientific assessment.", "state": state, "assessment": assessment,
        "parent_hypothesis_id": "failed" if identity == "pending" else None}
        for identity, assessment, state in cases]
    page.evaluate("fixture.snapshot.components[0].state='FAILED'; fixture.activity.graph=" + json.dumps({"nodes": nodes,
        "edges": [{"source": "failed", "target": "pending", "relation": "derived_from"}]}))
    page.click("#total-hypotheses")
    page.wait("document.querySelectorAll('.graph-node').length === 7")
    crosses = "[...document.querySelectorAll('.graph-node .status-cross')].map(n => n.closest('.graph-node').dataset.key).sort()"
    assert page.evaluate(crosses) == ["hypothesis:failed", "hypothesis:pruned-refuted", "hypothesis:refuted"]
    assert page.evaluate("[...document.querySelectorAll('.status-cross')].every(n => n.tagName === 'path' && getComputedStyle(n).stroke !== 'none')")
    assert "Refuted" in page.text("#graph-legend")
    assert "Execution failed" in page.text("#graph-legend")
    assert page.evaluate("document.querySelector('[data-key=\"hypothesis:pruned-refuted\"]').classList.contains('is-pruned')")
    assert "Refuted" in page.evaluate("document.querySelector('[data-key=\"hypothesis:pruned-refuted\"]').getAttribute('aria-label')")
    page.click("#fit-graph")
    page.click('[data-key="hypothesis:refuted"]')
    page.wait("document.querySelector('.graph-node.selected')?.dataset.key === 'hypothesis:refuted'")
    page.click("#close-drawer")
    page.evaluate("fixture.activity.graph.nodes[0].state='SUPPORTED'; fixture.activity.graph.nodes[0].assessment='SUPPORTED'")
    page.wait("document.querySelectorAll('.graph-node .status-cross').length === 2")
    assert page.evaluate("document.querySelector('.graph-node.selected').dataset.key") == "hypothesis:refuted"
    page.click("#fit-graph")
    page.evaluate("window.scrollTo(0, document.querySelector('.graph-panel').getBoundingClientRect().top + window.scrollY - 150)")
    page.screenshot(tmp_path / "hypothesis-failure-crosses.png")


def test_browser_leader_advice_shows_sources_and_provided_inputs_without_adoption(browser, tmp_path):
    page = browser
    item = {"kind": "leader", "interaction_id": "observer-advice", "state": "FINISHED",
        "mode": "experience_sharing", "participants": ["expert-a"], "stages": [], "verdict": None,
        "source_attempt_ids": ["observer-advice", "leader-advice"],
        "guidance": {"author": "expert-a", "attempt_id": "leader-advice", "summary": "Test the declared control before generalizing.",
            "supporting_refs": ["accepted-report--response"]},
        "provided_to": [{"attempt_id": "peer-proposal", "role": "expert-b", "cycle": 2, "subkind": "FORWARD"}]}
    page.evaluate('fixture.details["interactions:0"].interactions = ' + json.dumps([item]))
    page.click("#total-leader")
    page.wait("document.querySelector('#workbench-list').textContent.includes('Experience sharing')")
    text = page.text("#workbench-list")
    assert "peers continue independently" in text
    assert item["guidance"]["summary"] in text and "accepted-report--response" in text
    assert "peer-proposal" in text and "does not establish adoption" in text
    page.click("#workbench-list .record-title")
    page.wait("document.querySelector('#research-detail-content').textContent.includes('leader-advice')")
    assert "Source Attempt leader-advice" in page.text("#research-detail-content")
    page.screenshot(tmp_path / "leader-advice.png")
