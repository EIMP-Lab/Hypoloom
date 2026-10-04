"""Independent acquisition runner: actual transport validation and PDF extraction."""
import json
import os

import pytest

from scripts import probe_literature as probe
from hypoloom.grounding_web import GroundingWeb, GroundingWebError
from tests.runtime.test_grounding_web import Transport, _response
from tests.runtime.test_institutional_browser import _pdf
from tests.runtime.test_institutional_browser import browser  # noqa: F401

DOI = "10.1109/example.2026.001"
METADATA = "https://api.openalex.org/works/https://doi.org/" + DOI
PDF = "https://repository.example.edu/paper.pdf"


class Locator:
    def __init__(self, urls=()):
        self.urls, self.calls = urls, []

    def locate_doi(self, doi, **kwargs):
        self.calls.append(doi)
        return self.urls


def system(*, locations=(PDF,), doi=DOI):
    work = {"doi": "https://doi.org/" + doi, "display_name": "A controlled scholarly article",
            "locations": [{"is_oa": True, "pdf_url": url} for url in locations]}
    transport = Transport({METADATA: _response(METADATA, json.dumps(work).encode(), media_type="application/json")})
    crossref = "https://api.crossref.org/works/" + DOI
    transport.responses[crossref] = _response(crossref, json.dumps({"message": {
        "DOI": doi, "title": ["A controlled scholarly article"], "link": []}}).encode(), media_type="application/json")
    return GroundingWeb(transport), transport, Locator()


def test_automatic_discovery_download_and_real_pdf_identity_save_exact_bytes(tmp_path):
    web, transport, locator = system(locations=(PDF.replace("https:", "http:"),))
    original = _pdf(DOI)
    transport.responses[PDF] = _response(PDF, original, media_type="application/pdf")
    output = tmp_path / "article.pdf"
    row = probe.acquire(DOI, output, web=web, locator=locator)
    assert row["state"] == "pdf_verified" and row["source"] == "metadata"
    assert row["identity_basis"] == "doi" and row["page_count"] == 1
    assert output.read_bytes() == original
    assert not locator.calls and row["public_http_budget_used"] == 2
    assert list(tmp_path.iterdir()) == [output]


def test_wrong_pdf_is_rejected_before_valid_public_alternative_is_saved(tmp_path):
    wrong = "https://repository.example.edu/wrong.pdf"
    web, transport, locator = system(locations=(wrong, PDF))
    original = _pdf(DOI)
    transport.responses[wrong] = _response(wrong, _pdf("10.1109/different.001"), media_type="application/pdf")
    transport.responses[PDF] = _response(PDF, original, media_type="application/pdf")
    output = tmp_path / "article.pdf"
    row = probe.acquire(DOI, output, web=web, locator=locator)
    assert row["state"] == "pdf_verified" and output.read_bytes() == original
    assert len(row["events"]) == 1 and row["events"][0]["url"] == wrong
    assert not locator.calls


def test_missing_openalex_pdf_still_tries_exact_doi_crossref_link(tmp_path):
    web, transport, locator = system(locations=())
    crossref = "https://api.crossref.org/works/" + DOI
    transport.responses[crossref] = _response(crossref, json.dumps({"message": {
        "DOI": DOI, "title": ["A controlled scholarly article"],
        "link": [{"URL": PDF, "content-type": "application/pdf"}]}}).encode(), media_type="application/json")
    transport.responses[PDF] = _response(PDF, _pdf(DOI), media_type="application/pdf")
    row = probe.acquire(DOI, tmp_path / "article.pdf", web=web, locator=locator)
    assert row["state"] == "pdf_verified" and row["source"] == "metadata"
    assert row["public_http_budget_used"] == 3 and not locator.calls


@pytest.mark.parametrize("state", ["verification_required", "login_required", "access_denied"])
def test_browser_intervention_is_reported_once_without_waiting_or_false_pdf(tmp_path, state):
    web, transport, locator = system()
    transport.responses[PDF] = _response(PDF, b"<p>Are you a robot?</p>")
    class Browser:
        calls = []
        def fetch(self, url, **kwargs):
            self.calls.append(url)
            return {"state": state}
    browser = Browser()
    output = tmp_path / "article.pdf"
    row = probe.acquire(DOI, output, web=web, locator=locator, browser=browser)
    assert row["state"] == "unavailable" and not output.exists()
    assert row["events"][-1] == {"stage": "browser", "reason": state}
    assert len(browser.calls) == 1 and locator.calls == [DOI]


def test_html_full_text_is_not_reported_as_pdf(tmp_path):
    web, _, locator = system(locations=())
    class Browser:
        def fetch(self, url, **kwargs):
            return {"state": "available", "document": {"media_type": "text/plain", "content": b"full text"}}
    row = probe.acquire(DOI, tmp_path / "article.pdf", web=web, locator=locator, browser=Browser())
    assert row["state"] == "unavailable"
    assert row["events"][-1]["reason"] == "not_pdf"
    assert not list(tmp_path.iterdir())


def test_existing_doi_locator_is_used_and_query_parameters_are_not_reported(tmp_path):
    candidate = PDF + "?private-token=SECRET"
    web, transport, _ = system(locations=())
    locator = Locator((candidate,))
    transport.responses[candidate] = _response(candidate, _pdf(DOI), media_type="application/pdf")
    row = probe.acquire(DOI, tmp_path / "article.pdf", web=web, locator=locator)
    assert row["state"] == "pdf_verified" and row["source"] == "semantic_scholar"
    assert locator.calls == [DOI] and "SECRET" not in json.dumps(row)


def test_http_budget_stops_candidate_requests_and_does_not_start_fallback(tmp_path):
    candidates = tuple(f"https://repository.example.edu/{i}.pdf" for i in range(16))
    web, transport, locator = system(locations=candidates)
    for url in candidates:
        transport.responses[url] = _response(url, b"", status_code=403)
    row = probe.acquire(DOI, tmp_path / "article.pdf", web=web, locator=locator)
    assert row["state"] == "unavailable" and row["public_http_budget_used"] == 12
    assert len([call for call in transport.calls if call[0] == "fetch"]) == 12
    assert not locator.calls and not list(tmp_path.iterdir())


def test_expired_work_performs_no_network_or_browser_calls(tmp_path):
    web, transport, locator = system()
    row = probe.acquire(DOI, tmp_path / "article.pdf", web=web, locator=locator, deadline=0)
    assert row["reason"] == "deadline" and not transport.calls and not locator.calls
    assert not list(tmp_path.iterdir())


def test_pdf_arriving_after_deadline_is_not_saved(tmp_path, monkeypatch):
    import hypoloom.document_fallback as fallback
    clock = [0.]
    web, transport, locator = system()
    transport.responses[PDF] = _response(PDF, _pdf(DOI), media_type="application/pdf")
    original = transport.fetch
    def fetch(url, **kwargs):
        if url == PDF:
            clock[0] = 2.
        return original(url, **kwargs)
    transport.fetch = fetch
    web.clock = lambda: clock[0]
    monkeypatch.setattr(probe, "monotonic", lambda: clock[0])
    monkeypatch.setattr(fallback, "monotonic", lambda: clock[0])
    row = probe.acquire(DOI, tmp_path / "article.pdf", web=web, locator=locator, seconds=1)
    assert row["state"] == "unavailable" and row["reason"] == "deadline"
    assert not list(tmp_path.iterdir()) and not locator.calls


def test_metadata_mismatch_cannot_supply_a_title_to_admit_another_paper(tmp_path):
    web, transport, locator = system(doi="10.1109/wrong.001")
    crossref = "https://api.crossref.org/works/" + DOI
    transport.responses[crossref] = _response(crossref, b"", status_code=404)
    locator.urls = (PDF,)
    transport.responses[PDF] = _response(PDF, _pdf("10.1109/wrong.001"), media_type="application/pdf")
    row = probe.acquire(DOI, tmp_path / "article.pdf", web=web, locator=locator)
    assert row["state"] == "unavailable" and row["title"] == ""
    assert not list(tmp_path.iterdir())


def test_query_search_deduplicates_and_validates_dois():
    class Web:
        def document(self, url):
            return {"media_type": "application/json", "content": json.dumps({"results": [
                {"doi": "https://doi.org/" + DOI}, {"doi": "https://doi.org/" + DOI}]}).encode()}
    assert probe.search(Web(), "controlled article", 2, probe.monotonic() + 5) == [DOI]


def test_cli_existing_output_fails_before_network(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "GroundingWeb", lambda **kwargs: pytest.fail("network must not start"))
    with pytest.raises(SystemExit) as error:
        probe.main(["--samples", "--output", str(tmp_path)])
    assert error.value.code == 2


@pytest.mark.parametrize("query", ["", "   "])
def test_empty_query_never_defaults_to_publisher_sample_downloads(tmp_path, monkeypatch, query):
    monkeypatch.setattr(probe, "GroundingWeb", lambda **kwargs: pytest.fail("empty query must not start downloads"))
    output = tmp_path / "experiment"
    with pytest.raises(SystemExit) as error:
        probe.main(["--query", query, "--output", str(output)])
    assert error.value.code == 2 and not output.exists()


def test_cli_repeat_checks_again_and_keeps_later_failure_visible(tmp_path, monkeypatch):
    web, transport, locator = system()
    transport.responses[PDF] = _response(PDF, _pdf(DOI), media_type="application/pdf")
    original = probe.acquire
    def acquire(*args, **kwargs):
        row = original(*args, **kwargs)
        transport.responses[PDF] = _response(PDF, b"", status_code=403)
        return row
    monkeypatch.setattr(probe, "GroundingWeb", lambda **kwargs: web)
    monkeypatch.setattr(probe, "SemanticScholarPdfLocator", lambda **kwargs: locator)
    monkeypatch.setattr(probe, "acquire", acquire)
    output = tmp_path / "experiment"
    assert probe.main(["--doi", DOI, "--repeat", "2", "--output", str(output)]) == 2
    report = json.loads((output / "report.json").read_text())
    assert report["pdf_verified"] == 1 and not report["all_selected_verified"]
    assert report["completed"] and not report["research_run_created"]
    assert (output / "round-01-paper-01.pdf").exists()
    assert not (output / "round-02-paper-01.pdf").exists()


def test_empty_search_and_transport_failure_are_not_success(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "search", lambda *args: [])
    output = tmp_path / "empty"
    assert probe.main(["--query", "empty", "--output", str(output)]) == 2
    assert json.loads((output / "report.json").read_text())["errors"] == [{"stage": "search", "reason": "no_results"}]
    def failed(*args):
        raise GroundingWebError("http_error", "DO_NOT_PRINT", http_status=403)
    monkeypatch.setattr(probe, "search", failed)
    output = tmp_path / "failed"
    assert probe.main(["--query", "failed", "--output", str(output)]) == 2
    report = (output / "report.json").read_text()
    assert "DO_NOT_PRINT" not in report and not json.loads(report)["all_selected_verified"]


@pytest.mark.skipif(os.environ.get("HYPOLOOM_INSTITUTIONAL_BROWSER_TEST") != "1", reason="opt-in native CDP")
@pytest.mark.parametrize("challenge", [False, True])
def test_standalone_cli_native_browser_saves_pdf_or_exits_on_challenge(browser, tmp_path, monkeypatch, challenge):  # noqa: F811
    import hypoloom.institutional_browser as browser_module
    adapter, context = browser
    user_page = context.pages[0]
    original = _pdf(DOI)
    landing = "https://ieeexplore.ieee.org/document/11006507"
    pdf = "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?arnumber=11006507"
    def respond(route):
        if route.request.url == pdf:
            route.fulfill(status=200, content_type="application/pdf", body=original)
        elif route.request.url.startswith("https://doi.org/"):
            route.fulfill(status=200, content_type="text/html", body=f'<script>location.href={json.dumps(landing)}</script>')
        else:
            body = "Are you a robot?" if challenge else (
                f'<meta name="citation_doi" content="{DOI}"><meta name="citation_pdf_url" content="{pdf}">')
            route.fulfill(status=200, content_type="text/html", body=body)
    context.route("**/*", respond)
    monkeypatch.setattr("hypoloom.document_fallback.discover", lambda *args: ("Controlled article", []))
    monkeypatch.setattr(probe, "SemanticScholarPdfLocator", lambda **kwargs: Locator())
    monkeypatch.setattr(browser_module, "PlaywrightInstitutionalBrowser", lambda endpoint: adapter)
    output = tmp_path / "experiment"
    status = probe.main(["--doi", DOI, "--output", str(output), "--browser-endpoint", "http://127.0.0.1:9222"])
    report = json.loads((output / "report.json").read_text())
    assert status == (2 if challenge else 0)
    assert report["all_selected_verified"] is not challenge
    if challenge:
        assert not list(output.glob("*.pdf"))
        assert report["rows"][0]["events"][-1]["reason"] == "verification_required"
    else:
        assert (output / report["rows"][0]["file"]).read_bytes() == original
    assert not user_page.is_closed() and not adapter._tabs
