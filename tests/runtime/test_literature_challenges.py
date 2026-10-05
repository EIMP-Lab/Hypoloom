"""Challenge responses end before body reads and preserve public fallback."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from hypoloom.literature import (
    FixedLiteratureIO, LiteratureFailureCode, LiteratureFetchError,
    OpenAlexLiteratureFetcher, RetrievalDisposition, literature_failure_retryable,
)
from test_literature import FakeFetcher, PUBLIC_A, _papers, _query


@pytest.mark.parametrize("status", [200, 403, 503])
def test_batch_https_challenge_closes_without_reading_body(monkeypatch, status):
    closed = []

    class Response:
        def getheader(self, name):
            return {"Cf-Mitigated": "challenge", "Content-Length": "1207846"}.get(name)

        def read(self, size):
            pytest.fail("challenge body must not be downloaded")

    Response.status = status

    class Connection:
        def __init__(self, *args, **kwargs):
            self.sock = SimpleNamespace(getpeername=lambda: (PUBLIC_A, 443))

        def connect(self): pass
        def request(self, *args, **kwargs): pass
        def getresponse(self): return Response()
        def close(self): closed.append(True)

    monkeypatch.setattr("hypoloom.literature.http.client.HTTPSConnection", Connection)
    fetcher = OpenAlexLiteratureFetcher(api_key="fixture-openalex")
    with pytest.raises(LiteratureFetchError) as caught:
        fetcher._fetch_pdf_https("https://dl.acm.org/doi/pdf/10.1145/945445.945450",
                                approved_addresses=(PUBLIC_A,), max_bytes=2 * 1024 * 1024)
    assert caught.value.code is LiteratureFailureCode.PDF_CHALLENGE
    assert caught.value.http_status == status
    assert not literature_failure_retryable(caught.value)
    assert closed == [True]


def test_batch_challenge_is_recorded_while_another_public_copy_succeeds():
    blocked = "https://dl.acm.org/doi/pdf/10.1145/945445.945450"
    original = next(p for p in _papers() if p.bibliographic_identity.startswith("doi:"))
    selected = replace(original, pdf_uri=blocked)

    class Fetcher(FakeFetcher):
        def fetch(self, uri, **kwargs):
            if uri == blocked:
                self.fetch_calls.append((uri, kwargs))
                raise LiteratureFetchError("publisher challenge", code=LiteratureFailureCode.PDF_CHALLENGE,
                                           http_status=403)
            return super().fetch(uri, **kwargs)

    class Locator:
        def locate(self, paper):
            return (original.pdf_uri,)

    papers = [selected if p.bibliographic_identity == original.bibliographic_identity else p for p in _papers()]
    fetcher = Fetcher(papers=papers, resolutions={"dl.acm.org": (PUBLIC_A,)})
    adapter = FixedLiteratureIO(fetcher, fallback_locator=Locator(), pdf_workers=1)
    result = adapter.retrieve(_query())
    paper = next(p for p in result.packet.papers if p.bibliographic_identity == selected.bibliographic_identity)
    assert paper.retrieval_disposition is RetrievalDisposition.RETRIEVED
    entry = next(row for row in adapter.download_trace if row["bibliographic_identity"] == selected.bibliographic_identity)
    assert any(e["outcome"] == "CHALLENGE" and e["failure_code"] == "PDF_CHALLENGE"
               and e["http_status"] == 403 for e in entry["events"])
    assert any(e["outcome"] == "RETRIEVED" for e in entry["events"])
    assert len([call for call in fetcher.fetch_calls if call[0] == blocked]) == 1
