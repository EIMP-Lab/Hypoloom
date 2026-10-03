"""Synthetic credentials only; real PDF extraction and existing source admission."""
import json
from pathlib import Path
from time import monotonic

import pytest

from hypoloom.elsevier import ElsevierArticleAPI, ElsevierTransport, article_target
from hypoloom.grounding_web import GroundingWebError
from hypoloom.literature import FetchResponse
from tests.runtime.test_institutional_browser import _pdf
from tests.runtime.test_document_fallback import system as system
from tests.runtime.test_paper_reading import reading_host as reading_host

DOI = "10.1016/j.fixture.2026.123456"
URL = "https://doi.org/" + DOI
TARGET = "https://api.elsevier.com/content/article/doi/" + DOI + "?view=FULL"
KEY = "synthetic-elsevier-key"
TOKEN = "synthetic-institution-token"


@pytest.fixture
def api(tmp_path):
    source = tmp_path / "private.env"
    source.write_text(f"ELSEVIER_API_KEY={KEY}\nELSEVIER_INSTTOKEN={TOKEN}\n")
    source.chmod(0o600)
    calls = []
    settings = {"status": 200, "body": _pdf(DOI), "media": "application/pdf", "redirect": None}
    class Transport(ElsevierTransport):
        def resolve(self, host): return (settings.get("address", "8.8.8.8"),)
        def fetch(self, uri, **kwargs):
            calls.append((uri, self.request_headers(uri), kwargs))
            status, location = settings["status"], None
            if self.accept == "text/xml" and "xml" in settings:
                body = settings["xml"]
                return FetchResponse(requested_uri=uri, status_code=200, body=body,
                    content_length=len(body), peer_address="8.8.8.8", media_type="text/xml", location=None)
            if settings["redirect"] and uri == self.target:
                status, location = 303, settings["redirect"]
            body = b"" if location else settings["body"]
            return FetchResponse(requested_uri=uri, status_code=status, body=body,
                content_length=len(body), peer_address="8.8.8.8", media_type=settings["media"], location=location)
    client = ElsevierArticleAPI(str(source), transport_factory=Transport)
    return client, settings, calls, source


def read(api, url=URL, **kwargs):
    return api.document(url, deadline=monotonic() + 10, http_calls=[6], **kwargs)


def test_real_poppler_identity_and_secrets_remain_outside_result(api):
    client, settings, calls, source = api
    result = read(client)
    assert result["content"] == settings["body"]
    assert DOI in result["pages"][0]
    assert result["requested_url"] == URL and result["url"] == TARGET
    assert calls[0][0] == TARGET
    assert calls[0][1]["X-ELS-APIKey"] == KEY and calls[0][1]["X-ELS-Insttoken"] == TOKEN
    assert calls[0][1]["Accept"] == "application/pdf"
    assert KEY not in repr(result) + repr(client.configuration) + repr(client.preflight())
    assert TOKEN not in repr(result)
    source.write_text("ELSEVIER_API_KEY=rotated-synthetic\n")
    read(client)
    assert calls[-1][1]["X-ELS-APIKey"] == "rotated-synthetic"
    assert "X-ELS-Insttoken" not in calls[-1][1]


@pytest.mark.parametrize("target", ["https://cdn.example.org/paper.pdf", "https://api.elsevier.com/other.pdf"])
def test_redirect_never_receives_credentials_or_leaks_signed_url(api, target):
    client, settings, calls, _ = api
    settings["redirect"] = target + "?signature=private-download-token"
    result = read(client)
    assert len(calls) == 2
    assert "X-ELS-APIKey" not in calls[1][1] and "X-ELS-Insttoken" not in calls[1][1]
    assert result["url"] == TARGET and "private-download-token" not in repr(result)


@pytest.mark.parametrize("status,code", [(401, "elsevier_authentication_failed"),
    (403, "elsevier_access_denied"), (404, "elsevier_not_found"), (429, "elsevier_rate_limited"),
    (503, "http_error")])
def test_denials_are_bounded_and_never_echo_provider_body(api, status, code):
    client, settings, calls, _ = api
    settings.update(status=status, body=(KEY + TOKEN).encode(), media="application/json")
    with pytest.raises(GroundingWebError) as failure:
        read(client)
    assert failure.value.code == code and failure.value.http_status == status
    assert KEY not in str(failure.value) and TOKEN not in str(failure.value)
    assert failure.value.__suppress_context__ and len(calls) == (2 if status == 403 else 1)


@pytest.mark.parametrize("kind", ["metadata", "captcha", "wrong_doi", "truncated_pdf", "private_dns", "too_large"])
def test_non_fulltext_wrong_identity_and_unsafe_transport_are_never_admitted(api, kind):
    client, settings, calls, _ = api
    if kind == "metadata": settings.update(media="application/json", body=b'{"abstract":"Metadata only"}')
    if kind == "captcha": settings.update(media="text/html", body=b"<p>Are you a robot?</p>")
    if kind == "wrong_doi": settings["body"] = _pdf("10.1016/wrong")
    if kind == "truncated_pdf": settings["body"] = settings["body"][:-10]
    if kind == "private_dns": settings["address"] = "127.0.0.1"
    if kind == "too_large": client.max_bytes = 10
    with pytest.raises(GroundingWebError): read(client)
    assert len(calls) <= 1
    if kind == "private_dns": assert calls == []


def test_request_and_deadline_bounds_prevent_api_work(api):
    client, _, calls, _ = api
    with pytest.raises(GroundingWebError, match="request_limit"):
        client.document(URL, deadline=monotonic() + 10, http_calls=[0])
    with pytest.raises(GroundingWebError, match="timed_out"):
        client.document(URL, deadline=monotonic() - 1, http_calls=[6])
    assert not calls


@pytest.mark.parametrize("url", ["https://attacker.example/10.1016/test", "http://doi.org/" + DOI,
    "https://user:pass@doi.org/" + DOI, "https://doi.org/10.1109/test",
    URL + "?apiKey=synthetic", "https://sciencedirect.com.evil.example/science/article/pii/S0167527308011704"])
def test_unrelated_and_credential_bearing_selections_do_not_reach_api(url):
    assert article_target(url) is None


def test_pii_requires_identity_in_downloaded_bytes(api):
    client, _, calls, _ = api
    pii_url = "https://www.sciencedirect.com/science/article/pii/S0167527308011704"
    assert article_target(pii_url)[0].endswith("/pii/S0167527308011704?view=FULL")
    with pytest.raises(GroundingWebError, match="elsevier_full_text_unconfirmed"): read(client, pii_url)
    assert len(calls) == 1


def test_host_document_api_success_archive_and_reuse_need_no_browser(system, api, tmp_path):
    from hypoloom.paper_sources import web_tool
    host, request, metadata, public, backend, papers = system
    client, settings, calls, _ = api
    host.literature_io.article_api = client
    args = {"mode": "document", "url": URL}
    result = web_tool(host, request, "web_fetch", args)
    assert result["status"] == "available" and result["url"] == TARGET
    assert len(calls) == 1 and not public.calls and not metadata.calls and not backend.calls
    assert web_tool(host, request, "web_fetch", args) == result and len(calls) == 1
    archive = tmp_path / "archive"; archive.mkdir()
    papers.archive(archive)
    assert (archive / result["original_path"].lstrip("/")).read_bytes() == settings["body"]
    assert KEY not in json.dumps(host.web_tool_results) and TOKEN not in json.dumps(host.web_tool_results)


def test_host_api_denial_never_starts_captcha_loop(system, api):
    from hypoloom.paper_sources import web_tool
    host, request, metadata, public, backend, papers = system
    client, settings, calls, _ = api
    host.literature_io.article_api = client
    host.literature_io.fallback_locator = None
    settings["status"] = 403
    result = web_tool(host, request, "web_fetch", {"mode": "document", "url": URL})
    assert result["status"] == "unavailable" and result["error"]["code"] == "elsevier_access_denied"
    assert len(calls) == 2 and not public.calls and not backend.calls and not papers.sources
    assert host.institutional_access.status(request.run_id)["pending"] == 0
    group = next(g for g in host.institutional_access.status(request.run_id)["groups"] if g["provider"] == "ScienceDirect")
    assert group["papers"][0]["url"] == URL and group["papers"][0]["reason_code"] == "manual_import_available"


def test_api_denial_retains_operator_import_and_consumes_import_without_api_retry(system, api, tmp_path):
    from hypoloom.paper_sources import web_tool
    from tests.runtime.test_institutional_access import pump
    host, request, _, _, backend, papers = system
    client, settings, calls, _ = api
    host.literature_io.article_api = client
    host.literature_io.fallback_locator = None
    settings["status"] = 403
    arguments = {"mode": "document", "url": URL}
    assert web_tool(host, request, "web_fetch", arguments)["status"] == "unavailable"
    path = tmp_path / "manual.pdf"; path.write_bytes(_pdf(DOI))
    def import_pdf(path, url, title, **kwargs):
        return {"state": "available", "document": {"content": Path(path).read_bytes(),
            "pages": ("Method coefficient 0.83. DOI " + DOI,), "media_type": "application/pdf",
            "url": url, "requested_url": url, "title": title}}
    backend.import_pdf = import_pdf
    access = host.institutional_access
    access.control(request.run_id, "ScienceDirect", "import", url=URL, path=str(path))
    pump(access, lambda: access.status(request.run_id)["available"] == 1, runs=(request.run_id,))
    result = web_tool(host, request, "web_fetch", arguments)
    assert result["status"] == "available" and len(calls) == 2 and not backend.calls


def test_api_denial_can_recover_through_exact_doi_oa(system, api):
    from hypoloom.paper_sources import web_tool
    from hypoloom.literature import OpenAlexResponse
    from tests.runtime.test_document_fallback import OA
    host, request, metadata, public, backend, papers = system
    client, settings, calls, _ = api
    host.literature_io.article_api = client
    settings["status"] = 403
    def locate(**kwargs):
        metadata.calls.append(kwargs)
        body = json.dumps({"doi": DOI, "is_oa": True, "best_oa_location": {"url_for_pdf": OA}}).encode()
        return OpenAlexResponse(status_code=200, media_type="application/json", content_length=len(body), body=body)
    metadata.get = locate
    def document(url):
        public.calls.append(url)
        assert url == OA
        return {"url": url, "requested_url": url, "title": "Example", "content": _pdf(DOI),
                "pages": ("Method coefficient 0.83. DOI " + DOI,), "media_type": "application/pdf"}
    public.document = document
    result = web_tool(host, request, "web_fetch", {"mode": "document", "url": URL})
    assert result["status"] == "available" and result["url"] == OA and result["requested_url"] == URL
    assert "Open-access copy" in result["version_note"]
    assert len(calls) == 2 and public.calls == [OA] and len(metadata.calls) == 1 and not backend.calls


def test_public_only_choice_also_disables_authenticated_api(system, api):
    from hypoloom.paper_sources import web_tool
    host, request, metadata, public, backend, papers = system
    client, settings, calls, _ = api
    host.literature_io.article_api = client
    host.literature_io.fallback_locator = None
    host.institutional_access.provider_modes["ScienceDirect"] = "public_only"
    result = web_tool(host, request, "web_fetch", {"mode": "document", "url": URL})
    assert result["status"] == "unavailable" and result["reason"] == "public_only"
    assert public.calls == [URL]
    assert not calls and not backend.calls and not papers.sources


def test_probe_does_not_print_key_even_on_failure(api, monkeypatch, capsys):
    from hypoloom import elsevier
    client, settings, _, source = api
    settings["status"] = 403
    monkeypatch.setattr(elsevier, "ElsevierArticleAPI", lambda path: client)
    assert elsevier.main(["--credentials-file", str(source), "--url", URL]) == 2
    output = capsys.readouterr()
    assert json.loads(output.out)["reason"] == "elsevier_access_denied"
    assert KEY not in output.out + output.err and TOKEN not in output.out + output.err


def article_xml(doi=DOI, body="<body><section><para>Methods: coefficient 0.83.</para></section></body>"):
    return (f'<full-text-retrieval-response xmlns="http://www.elsevier.com/xml/svapi/article/dtd" '
        'xmlns:p="http://prismstandard.org/namespaces/basic/2.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<coredata><p:doi>{doi}</p:doi><pii>S0167527308011704</pii><dc:title>Example paper title</dc:title></coredata>'
        f'<originalText><doc><serial-item><article>{body}</article></serial-item></doc></originalText>'
        '</full-text-retrieval-response>').encode()


def test_pdf_denied_but_fulltext_xml_entitled_is_usable(api):
    client, settings, calls, _ = api
    settings.update(status=403, xml=article_xml())
    result = read(client)
    assert result["media_type"] == "text/plain" and "coefficient 0.83" in result["pages"][0]
    assert result["content"] == result["pages"][0].encode()
    assert "XML" in result["version_note"] and "no PDF pagination" in result["version_note"]
    assert [call[1]["Accept"] for call in calls] == ["application/pdf", "text/xml"]


@pytest.mark.parametrize("xml", [article_xml(doi="10.1016/wrong"), article_xml(body="<abstract>Only abstract</abstract>"),
    b'<html><body>Are you a robot?</body></html>', b'<!DOCTYPE x [<!ENTITY data "test">]><x>&data;</x>'])
def test_xml_metadata_challenge_wrong_identity_and_entities_rejected(api, xml):
    client, settings, calls, _ = api
    settings.update(status=403, xml=xml)
    with pytest.raises(GroundingWebError): read(client)
    assert len(calls) == 2


def test_xml_pii_and_request_budget_are_checked(api):
    client, settings, calls, _ = api
    settings.update(status=403, xml=article_xml())
    pii_url = "https://www.sciencedirect.com/science/article/pii/S0167527308011704"
    assert read(client, pii_url)["media_type"] == "text/plain"
    with pytest.raises(GroundingWebError, match="identity_unconfirmed"):
        read(client, pii_url[:-1] + "5")
    before = len(calls)
    with pytest.raises(GroundingWebError, match="request_limit"):
        client.document(URL, deadline=monotonic() + 10, http_calls=[1])
    assert len(calls) == before + 1


def test_real_host_tool_broker_accepts_api_document_and_reader_reuses_it(reading_host, api):
    from hypoloom.literature import FixedLiteratureIO
    from test_literature import FakeFetcher
    host, parent, _, held, finish = reading_host
    client, settings, calls, _ = api
    host.literature_io = FixedLiteratureIO(FakeFetcher())
    host.literature_io.article_api = client
    result = host.handle_agent_tool(parent.attempt_id, "web_fetch", {"mode": "document", "url": URL})
    assert result["status"] == "available" and len(calls) == 1
    reused = host.handle_agent_tool(parent.attempt_id, "web_fetch", {"mode": "document", "paper_ref": result["source_ref"]})
    assert reused == result and len(calls) == 1
    host.handle_agent_tool(parent.attempt_id, "read_papers", {"action": "submit", "readings": [
        {"reading_id": "api-method", "paper_ref": result["source_ref"], "question": "What is the method coefficient?"}]})
    host.paper_reading.tick()
    from hypoloom.domain import AgentCommand
    readers = [pair for pair in held.values() if pair[0].command is AgentCommand.PAPER_READ]
    assert len(readers) == 1
    finish(*readers[0])
    status = host.paper_reading.status(parent)
    assert status["terminal"] and status["readings"][0]["status"] == "completed"
    assert KEY not in json.dumps(host.web_tool_results) and TOKEN not in json.dumps(host.web_tool_results)
