"""Optional Host-only ScienceDirect Article Retrieval API, using existing document admission.

Secrets are reread from the original private Profile. Only the fixed API origin
receives authentication headers; redirects use the existing public HTTPS rules.
No browser, cookie transfer, automatic authentication retry or durable state.
"""
from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from time import monotonic
from urllib.parse import quote, urlsplit

from .credentials import (CredentialError, ELSEVIER_API_KEY, ELSEVIER_INSTTOKEN,
                          credential, read_credentials_file)
from .grounding_web import GroundingWeb, GroundingWebError, PublicHttpsTransport
from .institutional_access import identity_from_url, validate_document
from .literature import LiteratureError, _identity, _uri_parts

API_ORIGIN = "https://api.elsevier.com"


def article_target(url: str, identity: str = "") -> tuple[str, str] | None:
    """Bind canonical ScienceDirect/DOI selections to a DOI or PII, never arbitrary URLs."""
    try:
        canonical, host = _uri_parts(url, "article URL")
        parts = urlsplit(canonical)
        if parts.query or parts.fragment:
            return None
        doi = identity or identity_from_url(canonical)
        doi = _identity(doi) if doi.startswith("doi:") else ""
        science_direct = host in {"sciencedirect.com", "www.sciencedirect.com", "linkinghub.elsevier.com"}
        if not science_direct and not (host == "doi.org" and doi.startswith("doi:10.1016/")):
            return None
        if doi:
            return API_ORIGIN + "/content/article/doi/" + quote(doi[4:], safe="/") + "?view=FULL", doi
        match = re.fullmatch(r"/(?:science/article|retrieve)/pii/([A-Za-z0-9]{17})(?:/pdfft)?", parts.path)
        if match:
            return API_ORIGIN + "/content/article/pii/" + match[1].upper() + "?view=FULL", ""
    except (ValueError, LiteratureError):
        pass
    return None


def _headers(source: str) -> dict[str, str]:
    try:
        entries = read_credentials_file(source)
        key = credential(entries, ELSEVIER_API_KEY)
        token = entries.get(ELSEVIER_INSTTOKEN, "")
        if any(not isinstance(value, str) or len(value) > 4096
               or any(ord(c) < 33 or ord(c) > 126 for c in value) for value in (key, token)):
            raise CredentialError("invalid Elsevier credential format")
        return {"X-ELS-APIKey": key, **({"X-ELS-Insttoken": token} if token else {})}
    except (CredentialError, OSError):
        raise GroundingWebError("elsevier_credentials", "Elsevier private credentials are unavailable or invalid") from None


class ElsevierTransport(PublicHttpsTransport):
    def __init__(self, source: str, target: str, timeout_seconds: float, *, proxy_router=None):
        super().__init__(timeout_seconds, proxy_router=proxy_router)
        self.source, self.target = source, target
        self.accept = "application/pdf"

    def request_headers(self, uri: str) -> dict[str, str]:
        headers = {"Accept": self.accept, "User-Agent": "Hypoloom-Literature/1"}
        # Never forward credentials to redirects, publisher pages or arbitrary API paths.
        if uri == self.target and uri.startswith(API_ORIGIN + "/content/article/"):
            headers.update(_headers(self.source))
        return headers


def _xml_document(result, target, url, identity, title):
    """Accept an explicit article body and bound DOI/PII, never a META_ABS response."""
    raw = result["content"]
    if result["media_type"] not in {"application/xml", "text/xml"} or b"\x00" in raw or re.search(br"<!DOCTYPE|<!ENTITY", raw, re.I):
        raise GroundingWebError("elsevier_full_text_unconfirmed", "API did not return supported full-text XML")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise GroundingWebError("elsevier_full_text_unconfirmed", "API returned invalid article XML") from None
    ns = "{http://www.elsevier.com/xml/svapi/article/dtd}"
    core = root.find(ns + "coredata")
    original = root.find(ns + "originalText")
    if root.tag != ns + "full-text-retrieval-response" or core is None or original is None:
        raise GroundingWebError("elsevier_full_text_unconfirmed", "API returned metadata without an article body")
    def text(element):
        return "\n".join(part.strip() for part in element.itertext() if part.strip())
    bodies = [node for node in original.iter() if node.tag.rsplit("}", 1)[-1] == "body"]
    if not any(text(node).strip() for node in bodies):
        raise GroundingWebError("elsevier_full_text_unconfirmed", "API returned no supported full-text article body")
    observed = (core.findtext("{http://prismstandard.org/namespaces/basic/2.0/}doi") or "").strip().lower()
    if identity and observed != identity[4:].lower():
        raise GroundingWebError("identity_unconfirmed", "API article DOI differs from the selected paper")
    if not identity:
        pii = re.sub(r"[^A-Z0-9]", "", (core.findtext(ns + "pii") or "").upper())
        if not pii or pii != urlsplit(target).path.rsplit("/", 1)[-1]:
            raise GroundingWebError("identity_unconfirmed", "API article PII differs from the selected paper")
    article = next((node for node in original.iter() if node.tag.rsplit("}", 1)[-1] == "serial-item"), original)
    heading = (core.findtext("{http://purl.org/dc/elements/1.1/}title") or title).strip()
    content = "\n\n".join([heading, "DOI: " + observed, text(article)])
    verified = validate_document({"content": content.encode("utf-8"), "pages": (content,),
        "media_type": "text/plain", "identity": "doi:" + observed, "url": target,
        "requested_url": url, "title": heading}, identity=identity, title=title)
    return {**verified, "version_note": "Text extracted from Elsevier full-text XML; no PDF pagination; figures and formula formatting may be omitted. Access verified for this document only."}


class ElsevierArticleAPI:
    def __init__(self, credentials_file: str, *, max_bytes: int = 16 * 1024 * 1024,
                 timeout_seconds: float = 15, pdf_extractor=None, transport_factory=ElsevierTransport, proxy_router=None):
        self.credentials_file = credentials_file
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds
        self.pdf_extractor = pdf_extractor
        self.transport_factory = transport_factory
        self.proxy_router = proxy_router
        # Configuration errors fail at review, without making a network request.
        _headers(credentials_file)

    @property
    def configuration(self):
        return {"adapter": "elsevier-article", "host": "api.elsevier.com",
                "max_response_bytes": self.max_bytes, "timeout_seconds": self.timeout_seconds}

    def preflight(self):
        _headers(self.credentials_file)
        return {**self.configuration, "credential_source": "private_file",
                "network_probe_performed": False, "full_text_access": "not_checked"}

    def handles(self, url, identity=""):
        return article_target(url, identity) is not None

    def document(self, url, *, identity="", title="", deadline, http_calls):
        selected = article_target(url, identity)
        if selected is None:
            raise GroundingWebError("elsevier_unsupported_article", "Use a canonical ScienceDirect PII or Elsevier DOI URL")
        target, identity = selected
        if deadline <= monotonic():
            raise GroundingWebError("timed_out", "document work window expired")
        transport = self.transport_factory(self.credentials_file, target, self.timeout_seconds,
            **({"proxy_router": self.proxy_router} if self.transport_factory is ElsevierTransport else {}))
        web = GroundingWeb(transport,
            pdf_extractor=self.pdf_extractor, max_response_bytes=self.max_bytes,
            max_seconds=min(120, max(.1, deadline - monotonic())))
        try:
            if not identity:
                # PII-only links need the API's explicit article identity; a PDF DOI
                # without a known DOI cannot bind this selection by itself.
                web.transport.accept = "text/xml"
                result = web.document(target, max_seconds=deadline - monotonic(), http_calls=http_calls)
                result = _xml_document(result, target, url, identity, title)
                if monotonic() >= deadline:
                    raise GroundingWebError("timed_out", "document work window expired")
                return result
            try:
                result = web.document(target, max_seconds=deadline - monotonic(), http_calls=http_calls)
            except GroundingWebError as exc:
                # PDF and text-mining representations may have different entitlements.
                # This is one bounded representation fallback, not an authentication retry.
                if exc.code == "challenge" or exc.http_status not in {403, 406}:
                    raise
                web.transport.accept = "text/xml"
                result = web.document(target, max_seconds=deadline - monotonic(), http_calls=http_calls)
                result = _xml_document(result, target, url, identity, title)
                if monotonic() >= deadline:
                    raise GroundingWebError("timed_out", "document work window expired")
                return result
            if result["media_type"] != "application/pdf":
                raise GroundingWebError("elsevier_full_text_unconfirmed", "API response is not a complete article PDF")
            verified = validate_document(result, identity=identity, title=title)
            if monotonic() >= deadline:
                raise GroundingWebError("timed_out", "document work window expired")
            return {**verified, "requested_url": url, "url": target, "title": title or result["title"],
                    "version_note": "Article PDF retrieved through Elsevier API; access verified for this document only."}
        except GroundingWebError as exc:
            code = ("challenge" if exc.code == "challenge" else
                {401: "elsevier_authentication_failed", 403: "elsevier_access_denied",
                 404: "elsevier_not_found", 429: "elsevier_rate_limited"}.get(exc.http_status, exc.code))
            raise GroundingWebError(code, "Elsevier article retrieval did not yield verified full text",
                                   http_status=exc.http_status) from None
        except (ValueError, LiteratureError):
            raise GroundingWebError("identity_unconfirmed", "Article PDF failed content or identity validation") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only bounded Elsevier full-text access check; never prints credentials or document bytes.")
    parser.add_argument("--credentials-file", required=True, help="absolute private Profile methodology.yaml or legacy dotenv")
    parser.add_argument("--url", required=True, help="canonical DOI or ScienceDirect PII article URL")
    args = parser.parse_args(argv)
    try:
        api = ElsevierArticleAPI(args.credentials_file)
        document = api.document(args.url, deadline=monotonic() + 30, http_calls=[6])
        print(json.dumps({"state": "available", "scope": "sample_only", "url": document["url"],
                          "media_type": document["media_type"],
                          "page_count": len(document["pages"]) if document["media_type"] == "application/pdf" else None}))
        return 0
    except GroundingWebError as exc:
        print(json.dumps({"state": "unavailable", "reason": exc.code, "http_status": exc.http_status}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
