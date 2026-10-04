"""Bounded exact-DOI OA acquisition through existing public document validators."""
import json
import re
from time import monotonic
from urllib.parse import quote, urlsplit, urlunsplit

from .grounding_web import GroundingWeb, GroundingWebError
from .institutional_access import validate_document
from .literature import LiteratureError, _download_event, _identity, _openalex_pdf_uris, _uri_parts


def read_document(web, url, deadline, http_calls):
    remaining = deadline - monotonic()
    if remaining <= 0: raise GroundingWebError("timed_out", "document work window expired")
    if http_calls[0] <= 0: raise GroundingWebError("request_limit", "document HTTP work budget exhausted")
    if isinstance(web, GroundingWeb):
        result = web.document(url, max_seconds=remaining, http_calls=http_calls)
    else:
        http_calls[0] -= 1
        result = web.document(url)
    if monotonic() >= deadline: raise GroundingWebError("timed_out", "document work window expired")
    return result


def public_url(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def read_json(web, url, deadline, calls):
    document = read_document(web, url, deadline, calls)
    if document["media_type"] != "application/json":
        raise ValueError("metadata_not_json")
    result = json.loads(document["content"])
    if not isinstance(result, dict):
        raise ValueError("invalid_metadata")
    return result


def work_details(work, identity):
    if _identity("doi:" + str(work.get("doi", ""))) != identity:
        raise ValueError("metadata_identity_mismatch")
    title = work.get("display_name")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("metadata_title_missing")
    candidates = list(_openalex_pdf_uris(work))
    return title[:1024], candidates[:16]


def error_event(stage, error, url=None):
    code = getattr(error, "code", None)
    # Exceptions can contain URLs or browser details; never export their text.
    event = {"stage": stage, "reason": getattr(code, "value", code) or type(error).__name__}
    if url:
        event["url"] = public_url(url)
    if getattr(error, "http_status", None) is not None:
        event["http_status"] = error.http_status
    return event


def discover(web, identity, deadline, calls, events):
    doi = identity[4:]
    title = ""
    try:
        work = read_json(web, "https://api.openalex.org/works/https://doi.org/" + quote(doi, safe="/"), deadline, calls)
        title, candidates = work_details(work, identity)
        if candidates:
            return title, candidates
    except (GroundingWebError, LiteratureError, ValueError) as exc:
        events.append(error_event("openalex", exc))
    try:
        payload = read_json(web, "https://api.crossref.org/works/" + quote(doi, safe="/"), deadline, calls)
        work = payload.get("message", {})
        if not isinstance(work, dict) or _identity("doi:" + str(work.get("DOI", ""))) != identity:
            raise ValueError("metadata_identity_mismatch")
        titles = work.get("title")
        if not isinstance(titles, list) or not titles or not isinstance(titles[0], str) or not titles[0].strip():
            raise ValueError("metadata_title_missing")
        candidates = []
        links = work.get("link")
        if links is not None and not isinstance(links, list):
            raise ValueError("invalid_links")
        for link in (links or [])[:16]:
            if not isinstance(link, dict):
                continue
            url = link.get("URL")
            if not isinstance(url, str) or not (link.get("content-type") == "application/pdf"
                    or re.search(r"\.pdf(?:[?#]|$)|/doi/pdf/", url)):
                continue
            try:
                _uri_parts(url, "Crossref PDF URL")
            except LiteratureError:
                continue
            if url not in candidates:
                candidates.append(url)
        return title or titles[0][:1024], candidates
    except (GroundingWebError, LiteratureError, ValueError) as exc:
        events.append(error_event("crossref", exc))
        return title, []


def acquire_public_pdf(web, identity, *, deadline, http_calls, events, title="",
                       supplied_urls=(), locate=None, reject=lambda url: False):
    """One bounded public acquisition path for the probe, Host and init samples."""
    if not identity.startswith("doi:") or monotonic() >= deadline or http_calls[0] <= 0:
        return None, title
    identity = _identity(identity)
    discovered_title, candidates = discover(web, identity, deadline, http_calls, events)
    title = title or discovered_title
    seen = set()

    def attempt(url, source):
        if url in seen or reject(url) or monotonic() >= deadline or http_calls[0] <= 0:
            return None
        seen.add(url)
        try:
            document = read_document(web, url, deadline, http_calls)
            if reject(document["url"]):
                raise ValueError("rejected_source")
            if document.get("media_type") != "application/pdf":
                events.append({"stage": source, "reason": "not_pdf", "media_type": document.get("media_type")})
                return None
            verified = validate_document(document, identity=identity, title=title)
            if monotonic() >= deadline:
                raise GroundingWebError("timed_out", "document work window expired")
            return {**verified, "title": title or verified["title"], "acquisition_source": source}
        except (GroundingWebError, LiteratureError, ValueError) as exc:
            events.append(error_event(source, exc, url))
            return None

    for url in [*supplied_urls, *candidates]:
        document = attempt(url, "supplied" if url in supplied_urls else "metadata")
        if document is not None:
            return document, title
    if locate is not None and monotonic() < deadline and http_calls[0] > 0:
        http_calls[0] -= 1
        try:
            for url in locate(max(.001, deadline - monotonic())):
                document = attempt(url, "fallback")
                if document is not None:
                    return document, title
        except LiteratureError as exc:
            events.append(error_event("fallback_lookup", exc))
    return None, title


def open_copy(io, web, url, identity, title, rejected, events, deadline, http_calls):
    if not identity.startswith("doi:") or monotonic() >= deadline:
        return None
    from .paper_sources import _source_rejected
    observed = []
    locate = (lambda remaining: io.locate_doi(identity, events, timeout_seconds=remaining)) if io is not None else None
    document, _ = acquire_public_pdf(web, identity, title=title, deadline=deadline,
        http_calls=http_calls, events=observed, locate=locate,
        reject=lambda target: target == url or _source_rejected(target, rejected))
    # Reuse the Host's existing attempt trace and repeated-lookup suppression.
    for event in observed:
        _download_event(events, "fallback", "VALIDATION_FAILURE", uri=event.get("url"),
            error=LiteratureError(str(event["reason"]), http_status=event.get("http_status")))
    if not any(event.get("stage") == "fallback_lookup" for event in events):
        _download_event(events, "fallback_lookup", "FALLBACK_FOUND" if document else "FALLBACK_EMPTY")
    if document is None:
        return None
    _download_event(events, "fallback", "RETRIEVED", uri=document["url"])
    return {**document, "requested_url": url,
            "version_note": "Open-access copy at the recorded source URL; publisher version of record is not established."}
