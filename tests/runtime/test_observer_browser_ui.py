"""Actual browser interactions against the shipped assets and bounded read fixtures.

These tests require a locally installed Chrome and websockets; they never install
a browser, contact model providers, or open a retained research state root.
"""

from __future__ import annotations

import json
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
_CHROME = (
    str(Path("/opt/google/chrome/chrome"))
    if Path("/opt/google/chrome/chrome").is_file()
    else shutil.which("google-chrome") or shutil.which("chromium")
)
_ATTEMPT = {
    "attempt_id": "attempt-a",
    "role": "expert-a",
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
        "retry_unknown_closed_attempts": 1, "retry_elapsed_seconds": 32,
        "retry_unknown_nodes": 2, "retry_unavailable_reservations": 1,
        "retry_elapsed_unknown_attempts": 1,
        "correction_cost_usd": None}
    groups, nodes = [], []
    for command, role, node, model in [("command-a", "expert-a", "DESIGN", "model-a"),
                                       ("command-b", "expert-b", "GROUNDING", "model-b")]:
        groups.append({"role": role, "node": node, "model": model,
                       "metrics": metrics, "counts": counts, "costs": costs})
        nodes.append({"command_id": command, "workflow_id": "workflow-a", "role": role,
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
        "status": {"run_state": "RUNNING", "open_attempts": 0},
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
        )
    elif kind == "skill":
        result.update(
            _SKILL,
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
                   "exported_file_count": 3, "exported_payload_bytes": 2500},
        "diagnostics": {"oracle": {"available": False}, "attempts": []}}
    page.evaluate("""(() => {
      const prior = window.fetch;
      window.supportCalls = [];
      window.supportStatus = 200;
      window.fetch = async (path, options = {}) => {
        if (!path.startsWith('/api/support?')) return prior(path, options);
        window.fixtureRequests.push([options.method, path, options.headers.Authorization]);
        window.supportCalls.push(path);
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
    assert page.evaluate("document.querySelector('#support-dialog').open")
    assert not page.evaluate("document.querySelector('#support-private').checked")
    assert page.evaluate("document.querySelector('#download-support').disabled")
    page.click("#preview-support")
    page.wait("!document.querySelector('#download-support').disabled")
    assert "Some evidence was omitted" in page.text("#support-status")
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
    assert page.evaluate("fixtureRequests.filter(row => row[1].startsWith('/api/support?')).every(row => row[0] === 'GET' && row[2] === 'Bearer browser-fixture-token')")
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
        point = self.evaluate(
            f"(() => {{const e=document.querySelector({json.dumps(selector)}); e.scrollIntoView({{block:'center'}}); const r=e.getBoundingClientRect(); return {{x:r.x+r.width/2,y:r.y+r.height/2}};}})()"
        )
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
      if (query.pathname === '/api/refresh' && window.fixtureDelay) await new Promise(resolve => setTimeout(resolve, window.fixtureDelay));
      if (['/api/events','/api/experiments'].includes(query.pathname) && window.fixtureHistoryDelay) await new Promise(resolve => setTimeout(resolve, window.fixtureHistoryDelay));
      if (query.pathname === '/api/refresh' && window.fixtureError) return {ok:false, status:503};
      const data = query.pathname === '/api/refresh' ? {snapshot: fixture.snapshot, activity: fixture.activity}
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
        for (const key of ['session_id', 'role', 'component_id']) {
          if (query.searchParams.has(key)) response.sessions = response.sessions.filter(item => item[key] === query.searchParams.get(key));
        }
        response.total = response.sessions.length;
      }
      return {ok: true, status: 200, json: async () => response};
    };
    """
    )
    operator = getattr(request, "param", False)
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
        )
        driver = _Browser(socket)
        driver.call("Page.enable")
        driver.call("Runtime.enable")
        driver.call(
            "Page.navigate",
            {
                "url": "data:text/html;base64,"
                + base64.b64encode(markup.encode()).decode()
                + f"#token={_TOKEN}" + ("&answer_token=fixture-answer-token" if operator else "")
            },
        )
        driver.wait(
            "document.querySelector('#total-skills strong')?.textContent === '2'"
        )
        yield driver
        assert not driver.errors
        requests = driver.evaluate("window.fixtureRequests")
        assert all(method == "GET" or (operator and method == "POST" and path == "/api/human-answer") for method, path, _ in requests)
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


def test_browser_execution_reliability_filters_metrics_and_attempt_evidence(browser, tmp_path):
    page = browser
    page.click("#execution-reliability")
    page.wait("document.querySelector('.reliability-metric') !== null")
    summary = page.text("#reliability-summary")
    for expected in ("First output", "1/2", "unknown 1", "Correction recovery",
                     "Nodes needing retry", "Final delivery", "Required actions", "unavailable",
                     "Retry overhead", "$0.25", "$2", "Correction-only cost: unavailable",
                     "unknown retry nodes 2", "unavailable reserves 1", "unknown elapsed 1",
                     "Timeouts 1", "not model failures"):
        assert expected in summary
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
    assert "Retry overhead" in detail and "$0.25" in detail
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


def test_browser_hypothesis_repairs_and_scientific_revisions(browser, tmp_path):
    """Failed-cycle repairs must not look like new scientific mechanisms."""
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
    assert page.evaluate("Object.fromEntries([...document.querySelectorAll('.graph-node')].map(n => [n.dataset.key, n.dataset.changeKind]))") == {
        "hypothesis:root": "initial",
        "hypothesis:repair": "repair",
        "hypothesis:experiment": "experiment",
        "hypothesis:science": "scientific",
        "hypothesis:unknown": "unknown",
    }
    assert "Execution repair" in page.text(".graph-node[data-key='hypothesis:repair']")
    assert "Experiment revision + repair" in page.text(".graph-node[data-key='hypothesis:experiment']")
    assert "Scientific revision + repair" in page.text(".graph-node[data-key='hypothesis:science']")
    assert "Unclear revision" in page.text(".graph-node[data-key='hypothesis:unknown']")
    assert "INCONCLUSIVE" in page.text(".graph-node[data-key='hypothesis:repair']")
    assert "not validated progress" in page.text("#graph-summary")
    assert "Execution repair" in page.text("#topology-list")
    assert "Scientific revision" in page.text("#graph-legend")
    assert page.evaluate("document.querySelector('.graph-node[data-key=\"hypothesis:repair\"]').getAttribute('aria-label').includes('Execution repair')")
    assert page.evaluate("getComputedStyle(document.querySelector('.graph-edge.change-repair')).strokeDasharray") != "none"
    assert page.evaluate("getComputedStyle(document.querySelector('.graph-edge.change-scientific')).strokeDasharray") == "none"
    assert page.evaluate("document.querySelector('.pruning-edge').dataset.changeKind") == "pruning"
    assert page.evaluate("!document.querySelector('.pruning-edge').classList.contains('change-scientific')")
    page.click(".graph-node[data-key='hypothesis:repair']")
    assert "Execution repair" in page.text("#selection-detail")
    assert "修正实验执行脚本" in page.text("#selection-detail")
    assert "Interpretation of proposal text" in page.text("#selection-detail")
    assert "parent claim" in page.text("#selection-detail")
    page.click("#close-drawer")
    before = page.evaluate("fixtureRequests.filter(row => row[1] === '/api/refresh').length")
    page.wait(f"fixtureRequests.filter(row => row[1] === '/api/refresh').length > {before}")
    assert page.evaluate("document.querySelector('.graph-node.selected').dataset.key") == "hypothesis:repair"
    page.evaluate("document.querySelector('.graph-edge.change-scientific').dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true}))")
    assert "Scientific revision + repair" in page.text("#research-detail-content")
    assert "Graph Attention Filtering" in page.text("#research-detail-content")
    page.click("#close-drawer")
    page.click(".pruning-edge")
    assert "pruned_by" in page.text("#research-detail-content")
    assert "control decision" in page.text("#research-detail-content")
    page.click("#close-drawer")
    page.click("#fit-graph")
    page.evaluate("window.scrollTo(0, document.querySelector('.graph-panel').getBoundingClientRect().top + window.scrollY - 150)")
    page.screenshot(tmp_path / "hypothesis-revision-types.png")


def test_browser_hypothesis_revision_uncertainty_and_snapshot_fallback(browser):
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
        {...root, hypothesis_id:'literal', parent_hypothesis_id:'root', relation:'<img src=x onerror=window.graphInjected=true>'}];
    })()""")
    page.click("#total-hypotheses")
    page.wait("document.querySelectorAll('.graph-node').length === 11")
    assert page.evaluate("Object.fromEntries([...document.querySelectorAll('.graph-node')].map(n => [n.dataset.key, n.dataset.changeKind]))") == {
        "hypothesis:root": "initial",
        "hypothesis:failure": "unknown",
        "hypothesis:family-only": "unknown",
        "hypothesis:missing-parent": "unknown",
        "hypothesis:negated": "repair",
        "hypothesis:format": "repair",
        "hypothesis:historical": "repair",
        "hypothesis:same-family-science": "scientific",
        "hypothesis:protocol": "experiment",
        "hypothesis:unspecified-fix": "unknown",
        "hypothesis:literal": "unknown",
    }
    page.click("#fit-graph")
    page.click(".graph-node[data-key='hypothesis:literal']")
    assert "<img src=x" in page.text("#selection-detail")
    assert page.evaluate("!window.graphInjected && !document.querySelector('#selection-detail img')")


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
    }))
    page.evaluate("fixture.snapshot.status.shared_acceptance=" + json.dumps(common))
    page.evaluate("fixture.snapshot.status.goal_verdict='INCONCLUSIVE'; fixture.snapshot.components[0].phase='DESIGN'")
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


def test_browser_expert_card_working_subagents_and_breathing_dot(browser, tmp_path):
    page = browser
    assert page.evaluate("document.querySelector('.expert-preview h3 .breath-dot') === null")
    assert page.evaluate("document.querySelector('.expert-preview .agent-tile-list') === null")
    assert "scientific process attempts" not in page.text(".expert-identity")
    page.evaluate("fixture.snapshot.overview.host_observation = {availability:'responded'}")
    page.evaluate("fixture.snapshot.attempts.push("
        "{attempt_id:'open-run', kind:'AGENT', state:'OPEN', role:'expert-a', command:'EXPERIMENT_EXECUTION', context_tokens:45200, context_source:'transcript', budget_max_tokens:1000000, session_id:'session-a', started_at:fixture.snapshot.observed_at-720, wall_seconds:2700},"
        "{attempt_id:'open-ana', kind:'AGENT', state:'OPEN', role:'expert-a', command:'RESULT_ANALYSIS', context_tokens:null, budget_max_tokens:1000000, session_id:'session-b', started_at:fixture.snapshot.observed_at-3000, wall_seconds:2700},"
        "{attempt_id:'open-exe', kind:'PROCESS', state:'OPEN', role:'expert-a', command:'EXECUTE', gpu_uuid:'GPU-0', latest_utilization_percent:87, started_at:fixture.snapshot.observed_at-540},"
        "{attempt_id:'open-other', kind:'AGENT', state:'OPEN', role:'expert-b', command:'RESULT_ANALYSIS', context_tokens:1200, session_id:'session-c'})")
    page.wait("document.querySelectorAll('.expert-preview .agent-tile').length === 3")
    assert "Working · 3" in page.text(".expert-preview .working-agents")
    tiles = ".expert-preview .agent-tile"
    assert page.evaluate(f"Array.from(document.querySelectorAll('{tiles} .agent-tile-value')).map(e => e.textContent)") == ["—", "45.2K", "9m"]
    assert page.evaluate(f"Array.from(document.querySelectorAll('{tiles} .agent-tile-fill')).map(e => e.style.width)") == ["0%", "4.5%", "0%"]
    assert page.evaluate(f"document.querySelectorAll('{tiles}.overdue').length") == 1
    assert page.evaluate(f"document.querySelectorAll('{tiles} .agent-tile-light').length") == 3
    assert page.evaluate(f"new Set(Array.from(document.querySelectorAll('{tiles} .agent-tile-light')).map(e => e.style.animationDelay)).size") == 3
    assert page.evaluate(f"document.querySelectorAll('{tiles}.host-unconfirmed').length") == 0
    title = page.evaluate(f'document.querySelectorAll("{tiles}")[1].title')
    assert "Experiment execution" in title and "not process liveness" in title
    assert "context ≥45.2K of 1M attempt token budget" in title
    assert "live context · last model turn read from the session transcript" in title
    assert "elapsed 12m of 45m wall budget" in title
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
    page.evaluate("fixture.activity.experts.push({role:'expert-a', pause_requested:[{scope:{role:'expert-a', component_id:'component-a', chain:'chain-x'}, reason:'debate:agent-5e40-1', command_id:'cmd-pause'}]})")
    page.wait("document.querySelector('.expert-preview .pause-notice') !== null")
    notice = ".expert-preview .pause-notice"
    assert page.evaluate(f"document.querySelector('{notice}').classList.contains('in-debate')") is False
    assert "Debate proposed" in page.text(notice)
    assert "component-a / chain-x" in page.text(notice)
    assert "admitted work may finish" in page.text(notice)
    assert "debate:agent-5e40-1" in page.evaluate(f"document.querySelector('{notice}').title")
    page.evaluate("fixture.snapshot.attempts.push("
        "{attempt_id:'open-debate', kind:'AGENT', state:'OPEN', role:'expert-a', command:'DEBATE', context_tokens:88000, context_source:'transcript', budget_max_tokens:1000000, session_id:'session-d', started_at:fixture.snapshot.observed_at-120, wall_seconds:2700},"
        "{attempt_id:'open-lead', kind:'AGENT', state:'OPEN', role:'expert-a', command:'LEADER', context_tokens:null, budget_max_tokens:1000000, session_id:'session-e', started_at:fixture.snapshot.observed_at-60, wall_seconds:2700})")
    page.wait(f"document.querySelector('{notice}.in-debate') !== null")
    assert "Debate in progress" in page.text(notice)
    assert page.evaluate(f"document.querySelector('{notice} .pause-link') !== null")
    assert page.evaluate(f"document.querySelectorAll('{tiles}').length") == 5
    assert page.evaluate(f"document.querySelectorAll('{tiles}.debate').length") == 1
    assert page.evaluate(f"document.querySelectorAll('{tiles}.leader').length") == 1
    assert page.evaluate("document.querySelector('.expert-preview h3 .leader-crown')?.textContent") == "♛"
    debate_title = page.evaluate(f'document.querySelector("{tiles}.debate").title')
    assert "Debate round" in debate_title
    leader_title = page.evaluate(f'document.querySelector("{tiles}.leader").title')
    assert "Leader round" in leader_title
    page.evaluate("document.querySelector('.expert-preview').scrollIntoView({block:'start'})")
    page.evaluate("window.scrollBy(0, -110)")
    page.screenshot(tmp_path / 'expert-working-subagents.png')


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
    page.evaluate("fixture.snapshot.overview.experts=" + json.dumps([expert, {"role": "expert-b", "cycle": 0}]))
    page.evaluate("fixture.snapshot.experts.push({role:'expert-b',slot_ordinal:2}); fixture.snapshot.overview.host_observation={availability:'responded'}; fixture.snapshot.goal.research_contract={methodology:{controls:{product:{goal:{completion_mode:'shared_goal'}}}}}")
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
    page.wait("document.querySelector('#reliability-role')?.value === 'expert-a'")
    assert "Final delivery" in page.text("#reliability-list-label")
    page.click('#run-reliability-preview [data-preview-metric="node_delivery"]')
    page.wait("document.querySelector('#reliability-role')?.value === ''")


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
    assert "Refuted / failed" in page.text("#graph-legend")
    assert page.evaluate("document.querySelector('[data-key=\"hypothesis:pruned-refuted\"]').classList.contains('state-pruned')")
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
