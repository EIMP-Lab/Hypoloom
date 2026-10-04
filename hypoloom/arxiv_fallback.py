"""Conservative same-paper discovery; public bytes use the existing validators."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import stat
import time
import unicodedata
from urllib.parse import urlencode, urlsplit
from xml.etree import ElementTree

from .grounding_web import GroundingWebError
from .literature import _arxiv_pdf_uri_from_location, _identity

API = "https://export.arxiv.org/api/query"
API_LOCK = Path("/tmp/hypoloom-arxiv-api.lock")
API_INTERVAL = 3.0
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"


@contextmanager
def request_slot(deadline):
    """Serialize same-host API calls, including separate users and crashed Hosts."""
    descriptor = None
    try:
        try:
            descriptor = os.open(API_LOCK, os.O_CREAT | os.O_EXCL | os.O_RDONLY | os.O_NOFOLLOW, 0o444)
            os.fchmod(descriptor, 0o444)
        except FileExistsError:
            descriptor = os.open(API_LOCK, os.O_RDONLY | os.O_NOFOLLOW)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise GroundingWebError("rate_limited", "arXiv request coordination unavailable")
        while True:
            if time.monotonic() + API_INTERVAL >= deadline:
                raise GroundingWebError("timed_out", "arXiv request wait exceeds document deadline")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(.05, max(0, deadline - time.monotonic())))
        # Waiting before the request also covers a previous holder's abrupt exit.
        time.sleep(API_INTERVAL)
        if time.monotonic() >= deadline:
            raise GroundingWebError("timed_out", "arXiv request wait exceeds document deadline")
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)


def words(value):
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold())


def title_query(title):
    tokens = words(title)
    if not 4 <= len(tokens) <= 100:
        return None
    # Quoting each token separately makes stop words such as "for" required
    # terms and loses known papers. Use the API's complete title phrase instead.
    return API + "?" + urlencode({"search_query": 'ti:"' + " ".join(tokens) + '"',
                                  "start": 0, "max_results": 5})


def _same_author(left, right):
    a, b = words(left), words(right)
    return (len(a) >= 2 and len(b) >= 2 and a[-1] == b[-1]
            and all(x == y or (len(x) == 1 or len(y) == 1) and x[0] == y[0]
                    for x, y in zip(a[:-1], b[:-1])) and len(a) == len(b))


def _same_authors(expected, observed):
    if not expected or not observed or not _same_author(expected[0], observed[0]):
        return False
    if len(expected) == 1 or len(observed) == 1:
        return len(expected) == len(observed) == 1
    return any(_same_author(a, b) for a in expected[1:] for b in observed[1:])


def candidates(document, *, identity, title, authors):
    """Return at most one versioned PDF; conflicting/ambiguous results stay gaps."""
    if document.get("media_type") not in {"application/atom+xml", "application/xml", "text/xml"}:
        raise ValueError("arxiv_metadata_not_atom")
    final = urlsplit(document["url"])
    if final.hostname not in {"export.arxiv.org", "arxiv.org"} or final.path != "/api/query":
        raise ValueError("arxiv_metadata_origin_mismatch")
    raw = document["content"]
    if len(raw) > 1024 * 1024:
        raise ValueError("arxiv_metadata_too_large")
    xml = raw.decode("utf-8")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", xml, re.I):
        raise ValueError("arxiv_metadata_invalid_xml")
    try:
        feed = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ValueError("arxiv_metadata_invalid_xml") from exc
    entries = feed.findall(ATOM + "entry")
    total = feed.findtext("{http://a9.com/-/spec/opensearch/1.1/}totalResults")
    if feed.tag != ATOM + "feed" or len(entries) > 5 or total is not None and (not total.isdigit() or int(total) > 5):
        raise ValueError("arxiv_results_incomplete")
    selected = {}
    for entry in entries:
        if words(entry.findtext(ATOM + "title", "")) != words(title):
            continue
        observed = [author.findtext(ATOM + "name", "") for author in entry.findall(ATOM + "author")]
        if not _same_authors(authors, observed):
            continue
        doi = entry.findtext(ARXIV + "doi", "").strip()
        if doi and _identity("doi:" + doi) != identity:
            continue
        landing = entry.findtext(ATOM + "id", "").replace("http://", "https://", 1)
        pdf = _arxiv_pdf_uri_from_location({"landing_page_url": landing})
        if not pdf or not re.search(r"v[1-9][0-9]*$", pdf):
            continue
        if not any(link.get("title") == "pdf" and link.get("type") == "application/pdf"
                   and link.get("href", "").replace("http://", "https://", 1) == pdf
                   for link in entry.findall(ATOM + "link")):
            continue
        selected[pdf] = landing.split("/abs/", 1)[1]
    if len(selected) > 1:
        raise ValueError("arxiv_identity_ambiguous")
    return tuple(selected.items())
