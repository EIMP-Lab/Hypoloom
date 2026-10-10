import json
from dataclasses import asdict, replace
from functools import partial
from types import SimpleNamespace

import pytest

from hypoloom.context_management import context_policy
from hypoloom.ui_i18n import translate
from test_context_window import resolved_request
from test_streaming_context import _attempt
from test_observer_browser_ui import browser as browser


@pytest.mark.parametrize("language", ["en", "zh-CN"])
def test_imported_window_drives_browser_fill_source_and_cc_disagreement(browser, language):
    page = browser
    attempt, _ = _attempt()
    context = context_policy(resolved_request())
    value = {**asdict(attempt), "attempt_id": "imported-window", "command": "EXPERIMENT_DESIGN_IMPLEMENT",
        "role": "expert-a", "task_scope": "expert", "owner_role": "expert-a", "context": context,
        "context_tokens": 200000, "context_window_tokens": 1000000, "context_window_source": "cc_config",
        "context_observation": {"reported_cc_context_tokens": 200000}}
    if language == "zh-CN": page.click("#language-toggle")
    page.evaluate(f"fixture.snapshot.attempts.push({json.dumps(value)})")
    tile = '.agent-tile[data-attempt=imported-window]'
    page.wait(f"document.querySelector('{tile}') !== null")
    assert page.evaluate(f"document.querySelector('{tile} .agent-tile-fill').style.width") == "20%"
    title = page.evaluate(f"document.querySelector('{tile}').title")
    assert translate("CC explicit configuration", language=language) in title
    assert translate("Working context: {0}", translate("automatic", language=language), language=language) in title
    assert translate("CC reported window differs from launch configuration.", language=language) in title


@pytest.mark.parametrize("language", ["en", "zh-CN"])
def test_tui_has_same_window_and_disagreement_details(monkeypatch, language):
    from hypoloom import tui
    attempt, _ = _attempt()
    context = context_policy(resolved_request())
    attempt = replace(attempt, context=context, context_window_tokens=1000000,
        context_window_source="cc_config", context_observation={"reported_cc_context_tokens": 200000})
    monkeypatch.setattr(tui, "tr", partial(translate, language=language))
    lines = []
    tui.AttemptsPanel.update_snapshot(SimpleNamespace(update=lines.append), SimpleNamespace(attempts=(attempt,), observed_at=10))
    assert "1000000" in lines[0]
    assert translate("CC reported window differs from launch configuration.", language=language) in lines[0]
