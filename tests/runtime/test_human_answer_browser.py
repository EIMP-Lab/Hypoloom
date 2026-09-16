"""Actual browser decision affordances; socket/Host authority is exercised separately."""
import json
import pytest

from test_observer_browser_ui import browser as browser  # shared actual Chrome fixture


def question(page):
    row = {"decision_id": "question-1", "ordinal": 1, "state": "OPEN", "answer": None,
        "question": "缓存与模型应采用怎样的因果边界？" * 8,
        "rationale": "Evidence remains uncertain. <script>window.humanInjected=true</script>",
        "options": ["Keep the original split. " + "条件与限制需要完整显示。" * 20, "Use a revised extractor."]}
    page.evaluate("Object.assign(fixture.snapshot.status, " + json.dumps({"run_state": "WAITING_HUMAN", "run_revision": 5, "grounding_dialogue": [row]}) + ")")
    page.wait("!document.querySelector('#human-pending').hidden")


def test_browser_read_link_shows_pending_question_across_views_without_answer_access(browser):
    page = browser
    question(page)
    page.click("#tab-map")
    assert not page.evaluate("document.querySelector('#human-pending').hidden")
    page.click("#open-human")
    assert page.evaluate("document.querySelector('#human-dialog').open")
    assert page.evaluate("document.querySelector('#human-answer').readOnly && document.querySelector('#human-answer').value === ''")
    assert "read-only" in page.text("#human-access")
    assert page.evaluate("document.querySelector('#submit-human').hidden")
    assert page.evaluate("window.humanInjected !== true")
    assert page.evaluate("fixtureRequests.every(row => row[0] === 'GET')")


@pytest.mark.parametrize("browser", [True], indirect=True)
def test_browser_operator_reviews_retries_exact_answer_and_retains_draft(browser, tmp_path):
    page = browser
    question(page)
    page.evaluate("""(() => {
      const previous = window.fetch; window.answerCalls = [];
      window.fetch = async (path, options={}) => {
        if (path !== '/api/human-answer') return previous(path, options);
        fixtureRequests.push([options.method, path, options.headers.Authorization]);
        answerCalls.push({body: JSON.parse(options.body), headers: options.headers});
        if (answerCalls.length === 1) throw new Error('connection interrupted');
        const body = JSON.parse(options.body);
        Object.assign(fixture.snapshot.status, {run_state: 'ACTIVE', run_revision: 6});
        Object.assign(fixture.snapshot.status.grounding_dialogue[0], {state:'RESOLVED', resolution:'EDITED', answer:body.answer});
        return new Response(JSON.stringify({status:'accepted', revision:6}), {status:200});
      };
    })()""")
    page.click("#open-human")
    assert not page.evaluate("document.querySelector('#human-answer').readOnly")
    assert page.evaluate("document.querySelector('#human-answer').value === '' && document.querySelector('#review-human').disabled")
    page.click("#human-options button")
    assert page.evaluate("document.querySelector('#human-answer').value.startsWith('Keep the original split.')")
    page.evaluate("document.querySelector('#human-answer').value='保留两套划分。\\nUse five seeds.'; document.querySelector('#human-answer').dispatchEvent(new Event('input'))")
    before = page.evaluate("fixtureRequests.length")
    page.wait(f"fixtureRequests.length > {before}")
    assert page.evaluate("document.querySelector('#human-answer').value === '保留两套划分。\\nUse five seeds.' && answerCalls.length === 0")
    page.click("#review-human")
    assert page.evaluate("answerCalls.length === 0 && document.querySelector('#human-answer').readOnly")
    page.click("#submit-human")
    page.wait("document.querySelector('#submit-human').textContent === 'Retry same answer'")
    page.click("#close-human")
    page.click("#open-human")
    assert page.evaluate("document.querySelector('#human-answer').readOnly")
    page.click("#submit-human")
    page.wait("document.querySelector('#human-pending').hidden")
    assert page.evaluate("answerCalls.length === 2 && JSON.stringify(answerCalls[0].body) === JSON.stringify(answerCalls[1].body)")
    assert page.evaluate("answerCalls.every(item => item.headers['X-Hypoloom-Answer-Token'] === 'fixture-answer-token')")
    assert "Use five seeds" in page.text("#human-recorded")
    assert "EDITED" in page.evaluate("document.querySelector('#human-select').selectedOptions[0].textContent")
    assert page.evaluate("document.querySelector('#submit-human').hidden && document.querySelector('#human-answer').readOnly")
    page.call("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": False})
    assert page.evaluate("document.querySelector('#human-dialog').scrollWidth <= document.querySelector('#human-dialog').clientWidth")
    page.screenshot(tmp_path / "human-decision-mobile.png")
    assert page.evaluate("window.humanInjected !== true")


@pytest.mark.parametrize("browser", [True], indirect=True)
def test_browser_changed_question_keeps_draft_and_conflict_requires_new_review(browser):
    page = browser
    question(page)
    page.click("#open-human")
    page.evaluate("document.querySelector('#human-answer').value='draft'; document.querySelector('#human-answer').dispatchEvent(new Event('input')); fixture.snapshot.status.run_revision=6")
    page.wait("document.querySelector('#human-status').textContent.includes('run changed')")
    assert page.evaluate("document.querySelector('#human-answer').value === 'draft' && document.querySelector('#review-human').hidden")
    page.click("#close-human")
    page.click("#open-human")
    assert page.evaluate("document.querySelector('#human-answer').value === 'draft' && !document.querySelector('#review-human').hidden")
    page.evaluate("""(() => {
      const previous = window.fetch; window.answerCalls=[];
      window.fetch = async (path, options={}) => {
        if (path !== '/api/human-answer') return previous(path, options);
        fixtureRequests.push([options.method, path, options.headers.Authorization]);
        answerCalls.push(JSON.parse(options.body));
        return new Response(JSON.stringify({status:'conflict', error:{message:'Question already answered'}}), {status:409});
      };
    })()""")
    page.click("#review-human")
    page.click("#submit-human")
    page.wait("document.querySelector('#human-status').textContent.includes('not accepted')")
    assert page.evaluate("answerCalls.length === 1 && document.querySelector('#submit-human').hidden")
