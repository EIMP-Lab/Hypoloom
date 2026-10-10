"""Delayed provider usage must not look like an Agent with zero context."""
import copy
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from functools import partial
from types import SimpleNamespace

import pytest

from hypoloom.context_management import latest_context, request_capacity
from hypoloom.monitor import AttemptView, _with_live_attempt_context
from hypoloom.provider_observation import ProviderObservation, read_exchanges
from hypoloom.runtime_diagnostics import RuntimeDiagnostics
from hypoloom.ui_i18n import translate
from test_context_capacity_projection import append, document, policy25, response
from test_context_management import request24
from test_observer_browser_ui import browser
from test_provider_relay import _relay, _request, upstream


def _event(value):
    return b"data: " + json.dumps(value).encode() + b"\n\n"


_START = _event({"type": "message_start", "message": {"model": "stub-model",
    "usage": {"input_tokens": 0, "output_tokens": 0}}})
_END = (_event({"type": "message_delta", "delta": {"stop_reason": "end_turn"},
    "usage": {"input_tokens": 10442, "cache_read_input_tokens": 58944, "output_tokens": 201}})
    + _event({"type": "message_stop"}))


def _measured():
    return {"exchange": 19, "request_path": "/v1/messages", "upstream_status": 200,
        "message_complete": True, "response_body_complete": True, "served_models": ["stub-model"],
        "usage": {"input_tokens": 10442, "cache_read_input_tokens": 58944, "output_tokens": 201}}


def _pending(**overrides):
    parser = ProviderObservation()
    parser.feed(_START)
    return {**_measured(), **parser.finish(), "exchange": 20, "response_body_complete": False,
        "context_capacity": {**policy25(), "estimated_input_tokens": 74806, "fits": True,
            "estimate_source": "provider_usage_plus_delta_estimate"}, **overrides}


@pytest.mark.parametrize("message_complete,body_complete", [(False, False), (True, False), (False, True)])
@pytest.mark.parametrize("prior", [False, True])
def test_incomplete_zero_input_is_unknown_and_preserves_separate_history(message_complete, body_complete, prior):
    rows = ([_measured()] if prior else []) + [_pending(
        message_complete=message_complete, response_body_complete=body_complete)]
    original = copy.deepcopy(rows)
    value = latest_context(rows)
    assert value["input_tokens"] is None and value["source"] == "estimate"
    assert value["current_request_exchange"] == 20
    assert value["current_request_estimated_input_tokens"] == 74806 and value["current_request_fits"]
    assert value["last_observed_input_tokens"] == (69386 if prior else None)
    assert value["last_observed_exchange"] == (19 if prior else None)
    assert rows == original  # Observation cannot rewrite provider accounting.


@pytest.mark.parametrize("usage,semantics,expected", [
    ({"input_tokens": 21497, "cache_read_input_tokens": 2048}, "separate_cache", 23545),
    ({"input_tokens": 0, "cache_read_input_tokens": 2048}, "separate_cache", 2048),
    ({"input_tokens": 0, "cache_creation_input_tokens": 100}, "separate_cache", 100),
    ({"input_tokens": 100, "cache_read_input_tokens": 80}, "includes_cache", 100),
    ({"input_tokens": 0, "cache_read_input_tokens": 80}, "includes_cache", None),
    ({"input_tokens": 100}, "unknown", None),
])
def test_partial_nonzero_input_and_cache_semantics_remain_valid(usage, semantics, expected):
    row = _pending(usage=usage)
    value = latest_context([row], {"input_usage_semantics": semantics})
    assert value["input_tokens"] == expected
    assert value["source"] == ("provider_usage_partial" if expected is not None else "estimate")


@pytest.mark.parametrize("tokens", [0, 69386])
def test_complete_valid_input_including_real_zero_remains_measured(tokens):
    row = _pending(message_complete=True, response_body_complete=True,
        usage={"input_tokens": tokens, "output_tokens": 201})
    value = latest_context([_measured(), row])
    assert value["input_tokens"] == value["last_observed_input_tokens"] == tokens
    assert value["source"] == "provider_usage_final" and value["last_observed_exchange"] == 20


def _attempt():
    request = replace(request24(), schema_version=25)
    attempt = AttemptView(attempt_id=request.attempt_id, kind="AGENT", state="OPEN",
        workflow_id=request.owner_workflow_id, command=request.command.value, component_id=request.component_id,
        phase="design", role=request.role, slot_ordinal=1, attempt_ordinal=1, terminal_reason=None,
        process_group_id=None, started_at=1, ended_at=None, gpu_uuid=None, minimum_free_memory_mib=None,
        latest_free_memory_mib=None, latest_utilization_percent=None, resource_pressure_exceeded=None)
    return attempt, {request.attempt_id: {"request": request.to_dict()}}


@pytest.mark.parametrize("language", ["en", "zh-CN"])
def test_real_streaming_relay_projects_pending_then_final_usage_to_tui(tmp_path, upstream, monkeypatch, language):
    from hypoloom import tui
    attempt, observation = _attempt()
    capture = tmp_path / "diagnostics/agents/run" / attempt.attempt_id
    relay = _relay(tmp_path, upstream, context_policy=policy25(),
        diagnostics=RuntimeDiagnostics(capture), max_request_bytes=1000000)
    release = threading.Event()
    # A following block lets the normal credential lookbehind release the
    # complete start event, as happens during real streamed generation.
    prefix = _START + _event({"type": "content_block_start", "index": 0,
        "content_block": {"type": "text", "text": "working"}})
    suffix = _event({"type": "content_block_stop", "index": 0}) + _END
    def stream(handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Content-Length", str(len(prefix + suffix)))
        handler.end_headers()
        handler.wfile.write(prefix)
        handler.wfile.flush()
        if release.wait(5):
            handler.wfile.write(suffix)
            handler.wfile.flush()
    monkeypatch.setattr(tui, "tr", partial(translate, language=language))
    old = document("existing research input")
    try:
        upstream.body = response(69386)
        assert _request(relay, body=json.dumps(old).encode())[0] == 200
        upstream.script = stream
        with ThreadPoolExecutor() as executor:
            future = executor.submit(_request, relay, body=json.dumps(append(old, "new result")).encode())
            try:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    rows = read_exchanges(capture / "provider", latest_context_only=True)
                    if rows and rows[-1].get("usage") == {"input_tokens": 0, "output_tokens": 0}:
                        break
                    time.sleep(.01)
                else:
                    pytest.fail("relay did not expose the unfinished zero-usage start")
                observed, = _with_live_attempt_context(tmp_path, "run", (attempt,), observation)
                assert observed.context_tokens is None
                assert observed.context["last_observed_input_tokens"] == 69386
                assert observed.context["current_request_estimated_input_tokens"] > 69386
                lines = []
                tui.AttemptsPanel.update_snapshot(SimpleNamespace(update=lines.append),
                    SimpleNamespace(attempts=(observed,), observed_at=10))
                assert translate("pending or unavailable", language=language) in lines[0]
                assert "69386" in lines[0]
            finally:
                release.set()
            assert future.result(timeout=3)[0] == 200
        final, = _with_live_attempt_context(tmp_path, "run", (attempt,), observation)
        assert final.context_tokens == 69386 and final.context_source == "provider_usage_final"
        assert final.context["last_observed_exchange"] == final.context["current_request_exchange"] == 2
        assert len(upstream.calls) == 2
    finally:
        release.set()
        relay.close()


@pytest.mark.parametrize("language", ["en", "zh-CN"])
def test_browser_delayed_usage_keeps_latest_measurement_and_compaction_slot(browser, language, tmp_path):
    page = browser
    attempt, _ = _attempt()
    context = latest_context([_measured(), _pending()])
    value = {**asdict(attempt), "attempt_id": "delayed-usage", "command": "GROUNDING",
        "task_scope": "run", "run_group": "grounding", "owner_role": None, "context": context,
        "context_tokens": context["input_tokens"], "context_source": context["source"],
        "context_window_tokens": 262144}
    if language == "zh-CN":
        page.click("#language-toggle")
    page.evaluate(f"fixture.snapshot.attempts.push({json.dumps(value)})")
    page.evaluate("fixture.snapshot.overview.grounding={state:'active',attempt_ids:['delayed-usage'],accounting:{}}")
    tile = '#grounding-agent-card .agent-tile[data-attempt="delayed-usage"]'
    page.wait(f"document.querySelector('{tile}') !== null")
    assert page.text(f"{tile} .agent-tile-value") == "69.39K"
    assert page.evaluate(f"document.querySelector('{tile} .agent-tile-fill').style.width") == "26.5%"
    title = page.evaluate(f"document.querySelector('{tile}').title")
    assert translate("provider input usage pending or unavailable", language=language) in title
    assert "74.81K" in title
    assert translate("Latest measured context: {0} tokens", "69386", language=language) in title
    # DOM text alone missed a real regression: the prefix occupied line one,
    # while the numeric value wrapped below the capsule's clipped height.
    for width in (1440, 768, 390, 320):
        page.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": 1000,
            "deviceScaleFactor": 1, "mobile": False})
        page.evaluate(f"document.querySelector('{tile}').scrollIntoView({{block:'center'}})")
        assert page.evaluate(f"""(() => {{
            const value=document.querySelector('{tile} .agent-tile-value');
            const bar=value.parentElement.getBoundingClientRect();
            const range=document.createRange(); range.selectNodeContents(value);
            const rects=Array.from(range.getClientRects());
            return rects.length===1 && rects.every(r=>r.left>=bar.left && r.right<=bar.right
                && r.top>=bar.top && r.bottom<=bar.bottom);
        }})()"""), f"{language} context digits clipped at {width}px"
    # Compaction is independent of the reading. A reserved slot must remain
    # present and leave both the capsule and the numeric text in place.
    bounds = f"""(() => {{
        const tile=document.querySelector('{tile}');
        return [tile, tile.querySelector('.agent-tile-status'), tile.querySelector('.agent-tile-value')]
            .map(e=>{{const r=e.getBoundingClientRect(); return [r.x,r.width];}});
    }})()"""
    # Measure the settled capsule, after the entrance scale animation.
    page.wait(f"document.querySelector('{tile}').getAnimations().every(a=>a.playState !== 'running')")
    before = page.evaluate(bounds)
    assert before[1][1] > 0
    for status in ("compacting", None):
        page.evaluate("fixture.snapshot.attempts.find(a=>a.attempt_id==='delayed-usage').operational_status="
            + json.dumps(status))
        expected = "↻" if status else ""
        page.wait(f"document.querySelector('{tile} .agent-tile-status').textContent === {json.dumps(expected)}")
        assert page.evaluate(bounds) == before
        assert page.text(f"{tile} .agent-tile-value") == "69.39K"
        assert page.evaluate(f"document.querySelector('{tile} .agent-tile-fill').style.width") == "26.5%"
        if status:
            assert translate("Native CLI last reported: compacting", language=language) in page.evaluate(
                f"document.querySelector('{tile}').getAttribute('aria-label')")
            page.screenshot(tmp_path / f"capsule-compacting-{language}.png")
    context = latest_context([_measured(), _pending(message_complete=True, response_body_complete=True,
        usage={"input_tokens": 10442, "cache_read_input_tokens": 58944, "output_tokens": 201})])
    value.update(context=context, context_tokens=context["input_tokens"], context_source=context["source"])
    page.evaluate(f"Object.assign(fixture.snapshot.attempts.find(a=>a.attempt_id==='delayed-usage'), {json.dumps(value)})")
    page.wait(f"!document.querySelector('{tile}').title.includes({json.dumps(translate('provider input usage pending or unavailable', language=language))})")
    assert page.text(f"{tile} .agent-tile-value") == "69.39K"
    assert translate("provider input usage pending or unavailable", language=language) not in page.evaluate(
        f"document.querySelector('{tile}').title")
    assert page.evaluate(bounds) == before
    assert page.evaluate(f"document.querySelector('{tile} .agent-tile-fill').style.width") == "26.5%"


@pytest.mark.parametrize("language", ["en", "zh-CN"])
def test_browser_context_fill_uses_effective_window_and_keeps_input_allowance_separate(browser, language):
    page = browser
    # Each ceiling can be the effective one. The provider's advertised window,
    # cumulative invocation budget and request-specific reserves are distinct.
    cases = [({}, 262144, 205568, 65536, "25%"),
        ({"context_window_tokens": 128000}, 128000, 80000, 64000, "50%"),
        ({"context_window_tokens": 1000000, "runtime_context_tokens": 64000}, 64000, 24000, 32000, "50%"),
        ({"context_window_tokens": 1000000, "working_context_tokens": 80000}, 80000, 38000, 80000, "100%"),
        ({"working_context_tokens": 2000000}, 2000000, 1834808, 999990, "50%")]
    attempts = []
    for i, (memory, effective, allowance, tokens, _) in enumerate(cases):
        capacity = request_capacity(document("input"), policy25(**memory))
        assert capacity["effective_context_tokens"] == effective
        assert capacity["available_input_tokens"] == allowance
        context = latest_context([{**_measured(), "usage": {"input_tokens": tokens, "output_tokens": 201}},
            _pending(context_capacity=capacity)], policy25(**memory))
        attempts.append({"attempt_id": f"limit-{i}", "kind": "AGENT", "state": "OPEN",
            "role": "expert-a", "task_scope": "expert", "owner_role": "expert-a",
            "command": "EXPERIMENT_DESIGN_IMPLEMENT", "context_tokens": None,
            "context_source": "estimate", "context": context, "budget_max_tokens": 9000000,
            "context_window_tokens": effective, "context_window_source": capacity["window_source"]})
    if language == "zh-CN":
        page.click("#language-toggle")
    page.evaluate(f"fixture.snapshot.attempts.push(...{json.dumps(attempts)})")
    page.wait("document.querySelector('[data-attempt=limit-4]') !== null")
    for i, (_, effective, _, tokens, fill) in enumerate(cases):
        tile = f".agent-tile[data-attempt=limit-{i}]"
        assert page.evaluate(f"document.querySelector('{tile} .agent-tile-fill').style.width") == fill
        page.wait(f"""(() => {{
            const tile=document.querySelector('{tile}');
            return Math.abs(tile.querySelector('.agent-tile-fill').getBoundingClientRect().width
                / tile.querySelector('.agent-tile-bar').clientWidth - {float(fill[:-1]) / 100}) < .002;
        }})()""")
        title = page.evaluate(f"document.querySelector('{tile}').title")
        assert translate("Effective context limit: {0} tokens · bar denominator before reserves", effective, language=language) in title
        assert translate("Latest measured context: {0} tokens", tokens, language=language) in title
        if i in (0, 4):
            assert translate("Provider window unconfirmed; limit is the framework working estimate.", language=language) in title
        else:
            assert "Configured provider window" in title or "已配置提供商窗口" in title
    # The longest supported compact reading fits with the compaction glyph.
    page.evaluate("fixture.snapshot.attempts.find(a=>a.attempt_id==='limit-4').operational_status='compacting'")
    page.wait("document.querySelector('[data-attempt=limit-4] .agent-tile-status').textContent === '↻'")
    assert page.text("[data-attempt=limit-4] .agent-tile-value") == "999.99K"
    for width in (1440, 320):
        page.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": 1000,
            "deviceScaleFactor": 1, "mobile": False})
        assert page.evaluate("""(() => {
            const tile=document.querySelector('[data-attempt=limit-4]');
            const bar=tile.querySelector('.agent-tile-bar').getBoundingClientRect();
            const rects=['.agent-tile-status','.agent-tile-value'].map(selector=>{
                const range=document.createRange(); range.selectNodeContents(tile.querySelector(selector));
                return range.getBoundingClientRect();});
            return rects[0].right <= rects[1].left && rects.every(r=>r.left>=bar.left && r.right<=bar.right
                && r.top>=bar.top && r.bottom<=bar.bottom);
        })()""")
    # Missing window must not fall back to the invocation budget, and neither
    # estimates nor a process duration are measurements. A real zero stays zero.
    page.evaluate("Object.assign(fixture.snapshot.attempts.find(a=>a.attempt_id==='limit-0'), {context_window_tokens:null});"
        "Object.assign(fixture.snapshot.attempts.find(a=>a.attempt_id==='limit-1'), {context_tokens:0});"
        "Object.assign(fixture.snapshot.attempts.find(a=>a.attempt_id==='limit-2').context, {last_observed_input_tokens:null});"
        "Object.assign(fixture.snapshot.attempts.find(a=>a.attempt_id==='limit-3'), {kind:'PROCESS'});")
    page.wait("document.querySelector('[data-attempt=limit-1] .agent-tile-value').textContent === '0'")
    assert page.text("[data-attempt=limit-0] .agent-tile-value") == "65.54K"
    assert page.text("[data-attempt=limit-2] .agent-tile-value") == "—"
    for i in range(4):
        assert page.evaluate(f"document.querySelector('[data-attempt=limit-{i}] .agent-tile-fill').style.width") == "0%"
