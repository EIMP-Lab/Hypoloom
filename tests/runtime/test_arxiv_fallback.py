"""Same-title fallback through real transport/PDF validation and ordinary archives."""
import json
import multiprocessing
import time
from urllib.parse import parse_qs, urlsplit
from xml.sax.saxutils import escape

import pytest

from hypoloom import arxiv_fallback as arxiv
from hypoloom.document_fallback import acquire_public_pdf
from hypoloom.grounding_web import GroundingWeb, GroundingWebError
from hypoloom.paper_sources import web_tool
from tests.runtime.test_document_fallback import system  # noqa: F401
from tests.runtime.test_grounding_web import Transport, _response
from tests.runtime.test_institutional_browser import _pdf
from tests.runtime.test_literature_probe import DOI, METADATA, Locator

TITLE = "Masked Graph Learning With Recurrent Alignment"
AUTHORS = ["Alice Smith", "Bob Jones"]
PDF = "https://arxiv.org/pdf/2401.01234v2"


def atom(*, title=TITLE, authors=AUTHORS, doi=DOI, identifier="2401.01234v2", pdf=None):
    return ('<entry><id>http://arxiv.org/abs/' + identifier + '</id><title>' + escape(title)
        + '</title>' + ''.join('<author><name>' + escape(name) + '</name></author>' for name in authors)
        + '<arxiv:doi>' + escape(doi) + '</arxiv:doi><link title="pdf" type="application/pdf" href="'
        + escape(pdf or ('https://arxiv.org/pdf/' + identifier)) + '"/></entry>')


def feed(entries):
    return ('<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'
            + entries + '</feed>').encode()


@pytest.fixture(autouse=True)
def isolated_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(arxiv, "API_LOCK", tmp_path / "api.lock")
    monkeypatch.setattr(arxiv, "API_INTERVAL", 0)


def setup(*, entries=None, authors=AUTHORS, original=None):
    work = {"doi": DOI, "display_name": TITLE, "authorships": [
        {"author": {"display_name": name}} for name in authors], "locations": []}
    target = arxiv.title_query(TITLE)
    original = _pdf(TITLE + " arXiv:2401.01234v2 Alice Smith Bob Jones") if original is None else original
    crossref = "https://api.crossref.org/works/" + DOI
    transport = Transport({
        METADATA: _response(METADATA, json.dumps(work).encode(), media_type="application/json"),
        crossref: _response(crossref, b"missing", status_code=404),
        target: _response(target, feed(atom() if entries is None else entries), media_type="application/atom+xml"),
        PDF: _response(PDF, original, media_type="application/pdf"),
    })
    return GroundingWeb(transport), transport, original


def acquire(web, *, calls=12, seconds=10, reject=lambda _: False):
    events, budget = [], [calls]
    result, title = acquire_public_pdf(web, 'doi:' + DOI, deadline=time.monotonic() + seconds,
        http_calls=budget, events=events, locate=lambda _: (), reject=reject)
    return result, events, budget[0]


def test_title_author_match_downloads_preprint_and_preserves_version():
    web, transport, original = setup()
    result, events, remaining = acquire(web)
    assert result['content'] == original and result['media_type'] == 'application/pdf'
    assert result['acquisition_source'] == 'arxiv' and result['url'] == PDF
    assert 'arXiv 2401.01234v2' in result['version_note']
    assert 'publisher version of record is not established' in result['version_note']
    assert remaining == 7  # Two metadata calls, existing locator, Atom, PDF.
    assert [call[1] for call in transport.calls if call[0] == 'fetch'][-2:] == [arxiv.title_query(TITLE), PDF]


def test_title_phrase_preserves_stopwords_without_requiring_them_as_individual_terms():
    query = parse_qs(urlsplit(arxiv.title_query('Deep Residual Learning for Image Recognition')).query)
    assert query['search_query'] == ['ti:"deep residual learning for image recognition"']


@pytest.mark.parametrize('entry', [
    atom(title='Masked Graph Learning Without Recurrent Alignment'),
    atom(authors=['Carol Smith', 'Bob Jones']),
    atom(authors=['Alice Smith', 'Charlie Brown']),
    atom(doi='10.1109/another.001'),
    atom(identifier='2401.01234'),
    atom(pdf='https://unrelated.example/paper.pdf'),
    atom() + atom(identifier='2402.09999v1'),
    '<!DOCTYPE feed [<!ENTITY paper "bad">]>' + atom(),
])
def test_ambiguous_or_conflicting_metadata_never_downloads(entry):
    web, transport, _ = setup(entries=entry)
    result, events, _ = acquire(web)
    assert result is None
    assert not any(call[:2] == ('fetch', PDF) for call in transport.calls)
    assert events[-1]['stage'] == 'arxiv_lookup'


@pytest.mark.parametrize('status', [403, 429, 503])
def test_api_failures_are_bounded_gaps_without_retries(status):
    web, transport, _ = setup()
    target = arxiv.title_query(TITLE)
    transport.responses[target] = _response(target, b'no', status_code=status)
    result, events, _ = acquire(web)
    assert result is None and events[-1]['http_status'] == status
    assert sum(call[:2] == ('fetch', target) for call in transport.calls) == 1


@pytest.mark.parametrize('mode', ['wrong_doi', 'wrong_title', 'challenge', 'rejected', 'redirect'])
def test_metadata_match_does_not_override_download_validation(mode):
    web, transport, _ = setup(original=_pdf('10.1109/wrong.001') if mode == 'wrong_doi'
                              else _pdf('Entirely different paper text') if mode == 'wrong_title' else None)
    if mode == 'challenge':
        transport.responses[PDF] = _response(PDF, b'<p>Are you a robot?</p>')
    if mode == 'redirect':
        other = PDF.replace('v2', 'v3')
        transport.responses[PDF] = _response(PDF, b'', status_code=302, location=other)
        transport.responses[other] = _response(other, _pdf(DOI), media_type='application/pdf')
    result, _, _ = acquire(web, reject=lambda url: mode == 'rejected' and url == PDF)
    assert result is None
    if mode == 'rejected':
        assert not any(call[:2] == ('fetch', PDF) for call in transport.calls)


@pytest.mark.parametrize('mode', ['no_authors', 'budget', 'deadline'])
def test_insufficient_identity_or_budget_does_not_query(mode):
    web, transport, _ = setup(authors=[] if mode == 'no_authors' else AUTHORS)
    result, _, _ = acquire(web, calls=4 if mode == 'budget' else 12,
                          seconds=-1 if mode == 'deadline' else 10)
    assert result is None
    assert not any(call[:2] == ('fetch', arxiv.title_query(TITLE)) for call in transport.calls)


def test_matching_preprint_without_publisher_doi_and_with_author_initials():
    web, _, _ = setup(entries=atom(doi='', authors=['A. Smith', 'B. Jones'], title=TITLE.upper()))
    assert acquire(web)[0]['url'] == PDF


def test_host_delivers_and_archives_arxiv_without_browser_and_reuses_source(system, tmp_path):
    host, request, _, _, backend, papers = system
    web, transport, original = setup()
    url = 'https://doi.org/' + DOI
    transport.responses[url] = _response(url, b'denied', status_code=403)
    host.grounding_web, host.literature_io = web, None
    result = web_tool(host, request, 'web_fetch', {'mode': 'document', 'url': url})
    assert result['status'] == 'available' and result['url'] == PDF and result['requested_url'] == url
    assert 'arXiv 2401.01234v2' in result['version_note'] and not backend.calls
    archive = tmp_path / 'archive'; archive.mkdir()
    papers.archive(archive)
    assert (archive / result['original_path'].lstrip('/')).read_bytes() == original
    before = list(transport.calls)
    assert web_tool(host, request, 'web_fetch', {'mode': 'document', 'url': url}) == result
    assert transport.calls == before and len(papers.sources) == 1


def test_lock_deadline_does_not_send_or_consume_http_budget(monkeypatch):
    monkeypatch.setattr(arxiv, 'API_INTERVAL', 3)
    web, transport, _ = setup()
    budget = [1]
    with pytest.raises(GroundingWebError, match='timed_out'):
        web.document(arxiv.title_query(TITLE), max_seconds=.1, http_calls=budget)
    assert budget == [1]
    assert not any(call[0] == 'fetch' for call in transport.calls)


def _hold_slot(path, connection):
    arxiv.API_LOCK, arxiv.API_INTERVAL = path, .1
    with arxiv.request_slot(time.monotonic() + 5):
        connection.send(time.monotonic())
        connection.recv()


def test_independent_process_exclusion_crash_release_and_spacing(monkeypatch):
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe()
    process = context.Process(target=_hold_slot, args=(arxiv.API_LOCK, child))
    process.start()
    try:
        assert parent.poll(10)
        parent.recv()
        with pytest.raises(GroundingWebError, match='timed_out'):
            with arxiv.request_slot(time.monotonic() + .1):
                pytest.fail('another process still owns the request')
        process.terminate(); process.join(5)
        monkeypatch.setattr(arxiv, 'API_INTERVAL', .1)
        started = time.monotonic()
        with arxiv.request_slot(time.monotonic() + 5):
            assert time.monotonic() - started >= .1
            assert arxiv.API_LOCK.stat().st_mode & 0o777 == 0o444
        assert arxiv.API_LOCK.exists()  # Never unlink a shared lock inode.
    finally:
        if process.is_alive(): process.terminate(); process.join(5)
        parent.close(); child.close()
