"""Bounded exact-DOI OA acquisition through existing public document validators."""
from time import monotonic

from .grounding_web import GroundingWeb, GroundingWebError
from .institutional_access import validate_document
from .literature import _download_event


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


def open_copy(io, web, url, identity, title, rejected, events, deadline, http_calls):
    if io is None or not identity.startswith("doi:") or monotonic() >= deadline:
        return None
    from .paper_sources import _source_rejected
    candidates = io.locate_doi(identity, events, timeout_seconds=max(.001, deadline - monotonic()))
    for target in candidates:
        if target == url or _source_rejected(target, rejected): continue
        if http_calls[0] <= 0 or monotonic() >= deadline: break
        try:
            document = read_document(web, target, deadline, http_calls)
            if document["media_type"] != "application/pdf":
                raise ValueError("identity_unconfirmed")
            verified = validate_document(document, identity=identity, title=title)
        except (GroundingWebError, ValueError):
            _download_event(events, "fallback", "VALIDATION_FAILURE", uri=target)
            continue
        _download_event(events, "fallback", "RETRIEVED", uri=target)
        return {**verified, "requested_url": url, "title": title or verified["title"],
                "version_note": "Open-access copy at the recorded source URL; publisher version of record is not established."}
    return None
