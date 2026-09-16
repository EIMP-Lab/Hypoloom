"""Exact DOI fallback shares the existing bounded PDF retrieval path."""

import json
from dataclasses import replace
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from hypoloom import cli
from hypoloom.literature import (
    FetchResponse, FixedLiteratureIO, LiteratureFetchError, OpenAlexResponse,
    UnpaywallPdfLocator, StdlibUnpaywallTransport, build_openalex_literature_io,
)
from test_literature import FakeFetcher, PDF_FOUNDATIONAL, PUBLIC_A, _papers, _query


EMAIL = "reader@example.org"
DOI = "10.1000/foundation"
PDF_URL = "https://fallback.example.org/paper.pdf"


class Metadata:
    def __init__(self, payload=None, status=200):
        self.payload = payload if payload is not None else {
            "doi": DOI.upper(), "is_oa": True,
            "best_oa_location": {"url_for_pdf": PDF_URL},
            "oa_locations": [{"url_for_pdf": PDF_URL}],
        }
        self.status = status
        self.calls = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        body = json.dumps(self.payload).encode()
        return OpenAlexResponse(status_code=self.status, media_type="application/json",
            content_length=len(body), body=body)


def test_unpaywall_uses_exact_doi_email_and_no_provider_key():
    transport = Metadata()
    locator = UnpaywallPdfLocator(email=EMAIL, transport=transport)
    assert locator.locate(_papers()[-1]) == (PDF_URL,)
    call, = transport.calls
    assert call["host"] == "api.unpaywall.org"
    target = urlsplit(call["target"])
    assert unquote(target.path) == "/v2/" + DOI
    assert parse_qs(target.query) == {"email": [EMAIL]}
    assert set(call["headers"]) == {"Accept", "User-Agent"}
    assert EMAIL not in repr(locator.configuration)
    assert EMAIL not in repr(locator.preflight())
    assert EMAIL not in repr(locator)
    assert locator.locate(_papers()[0]) == ()
    assert len(transport.calls) == 1


@pytest.mark.parametrize("payload", [
    {"doi": "10.1000/another", "is_oa": True, "best_oa_location": {"url_for_pdf": PDF_URL}},
    {"doi": DOI, "is_oa": False, "best_oa_location": {"url_for_pdf": PDF_URL}},
    {"doi": DOI, "is_oa": True, "best_oa_location": None},
    {"doi": DOI, "is_oa": True, "best_oa_location": {"url_for_pdf": "http://example.org/paper.pdf"}},
])
def test_unpaywall_never_substitutes_another_paper_or_claims_unavailable_pdf(payload):
    assert UnpaywallPdfLocator(email=EMAIL, transport=Metadata(payload)).locate(_papers()[-1]) == ()


@pytest.mark.parametrize("status", [404, 429, 500])
def test_unpaywall_status_is_bounded_and_preserves_rate_limit(status):
    transport = Metadata(status=status)
    locator = UnpaywallPdfLocator(email=EMAIL, transport=transport)
    if status == 404:
        assert locator.locate(_papers()[-1]) == ()
    else:
        with pytest.raises(LiteratureFetchError) as failure:
            locator.locate(_papers()[-1])
        assert failure.value.http_status == status
        assert EMAIL not in str(failure.value)
    assert len(transport.calls) == 1


def test_unpaywall_pdf_uses_existing_download_and_extraction_validation():
    papers = [*_papers()[:2], replace(_papers()[-1], pdf_uri=None)]
    fetcher = FakeFetcher(papers=papers, resolutions={"fallback.example.org": (PUBLIC_A,)},
        responses={PDF_URL: FetchResponse(requested_uri=PDF_URL, status_code=200,
            peer_address=PUBLIC_A, media_type="application/pdf", content_length=len(PDF_FOUNDATIONAL),
            location=None, body=PDF_FOUNDATIONAL)})
    metadata = Metadata()
    adapter = FixedLiteratureIO(fetcher, fallback_locator=UnpaywallPdfLocator(email=EMAIL, transport=metadata))
    result = adapter.retrieve(_query())
    foundation = result.packet.papers[0]
    assert foundation.pdf_trace is not None and foundation.pdf_trace.source_uri == PDF_URL
    assert len(metadata.calls) == 1
    assert EMAIL not in result.packet.to_bytes().decode()
    assert EMAIL not in repr(adapter.download_trace)


@pytest.mark.parametrize("email", ["", "no-address", "a@b\nInjected: value", "a@b c.org"])
def test_unpaywall_rejects_invalid_contact_without_network(email):
    with pytest.raises(ValueError, match="email"):
        UnpaywallPdfLocator(email=email, transport=Metadata())


def test_unpaywall_exact_host_transport_rejects_alternate_destinations():
    transport = StdlibUnpaywallTransport()
    for host, target in (("example.org", "/v2/10.1000/paper?email=a%40b.org"),
                         ("api.unpaywall.org", "/v2/search?query=paper&email=a%40b.org")):
        with pytest.raises(LiteratureFetchError):
            transport.get(host=host, target=target, headers={}, timeout_seconds=1, max_bytes=4096)


def test_unpaywall_contact_file_is_read_only_bounded_and_not_a_symlink(tmp_path):
    contact = tmp_path / "unpaywall.mail"
    contact.write_text(EMAIL + "\n")
    contact.chmod(0o664)  # A contact address is not an API-key credential.
    before = contact.stat()
    locator = UnpaywallPdfLocator(email_file=contact, transport=Metadata())
    assert locator.locate(_papers()[-1]) == (PDF_URL,)
    assert contact.stat().st_mtime_ns == before.st_mtime_ns
    assert contact.stat().st_mode == before.st_mode
    link = tmp_path / "link"
    link.symlink_to(contact)
    with pytest.raises(ValueError, match="email file"):
        UnpaywallPdfLocator(email_file=link)
    contact.write_bytes(b"x" * 1025)
    with pytest.raises(ValueError, match="email file"):
        UnpaywallPdfLocator(email_file=contact)


def test_explicit_unpaywall_selection_reaches_host_without_exposing_contact(tmp_path, monkeypatch):
    email_file = tmp_path / "unpaywall.email"
    email_file.write_text(EMAIL + "\n")
    email_file.chmod(0o600)
    key = tmp_path / "openalex.key"
    key.write_text("offline-openalex-secret")
    key.chmod(0o600)
    observed = {}
    monkeypatch.setattr("hypoloom.workflow_host.serve", lambda root, **options: observed.update(options) or 0)
    args = ("serve", "--state-root", str(tmp_path / "state"), "--literature-adapter", "openalex",
        "--openalex-api-key-file", str(key), "--literature-pdf-fallback", "unpaywall")
    with pytest.raises(SystemExit) as rejected:
        cli.main(args)
    assert rejected.value.code == 2 and not (tmp_path / "state").exists()
    assert cli.main((*args, "--unpaywall-email-file", str(email_file))) == 0
    locator = observed["literature_io"].fallback_locator
    assert isinstance(locator, UnpaywallPdfLocator)
    assert locator.configuration["adapter"] == "unpaywall-doi"
    assert EMAIL not in repr(observed["literature_io"].configuration)
    assert "offline-openalex-secret" not in repr(locator.configuration)
    assert not (tmp_path / "state").exists()
    with pytest.raises(ValueError, match="unpaywall"):
        build_openalex_literature_io(api_key="fixture", unpaywall_email_file=email_file)
