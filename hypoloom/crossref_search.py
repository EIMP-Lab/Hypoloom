"""Anonymous Crossref candidate search, bounded by the caller's search deadline.

Crossref's bibliographic ranking is not a Boolean search contract. Only records
matching the original expression in their title/abstract are returned; the
ordinary literature validator still decides whether all strata are covered.
PDF links remain unverified hints for the existing acquisition pipeline.
"""
from __future__ import annotations

import calendar
import json
import math
import re
import time
from collections.abc import Mapping
from datetime import date
from html.parser import HTMLParser
from urllib.parse import quote, urlencode

from . import literature
from .literature import (
    LITERATURE_STRATA,
    MAX_AUTHORS,
    LiteratureError,
    LiteratureFetchError,
    LiteratureFailureCode,
    LiteratureQuery,
    LiteratureQueryError,
    LiteratureValidationError,
    OpenAlexResponse,
    ScholarlySourceClass,
    SearchPaper,
    _identity,
    _normalized_venue_name,
    _openalex_boolean_tokens,
    _openalex_year_ranges,
    _stratum_quotas,
    _text,
    _uri_parts,
    _validate_candidate_against_query,
    _validate_openalex_boolean_query,
)

_CROSSREF_TYPES = {
    ScholarlySourceClass.JOURNAL: 'journal-article',
    ScholarlySourceClass.CONFERENCE: 'proceedings-article',
    ScholarlySourceClass.PREPRINT: 'posted-content',
    ScholarlySourceClass.SCHOLARLY_DATASET: 'dataset',
    ScholarlySourceClass.TECHNICAL_REPORT: 'report',
}
_RESPONSE_BYTES = 2 * 1024 * 1024


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _plain(value):
    parser = _Text()
    parser.feed(value)
    parser.close()
    return ' '.join(' '.join(parser.parts).split())


def _expression(query):
    """Compile the admitted safe grammar to postfix without eval or recursion."""
    _validate_openalex_boolean_query(query)
    tokens = _openalex_boolean_tokens(query)
    output, operators, keywords = [], [], []
    precedence = {'OR': 1, 'AND': 2, 'NOT': 3}

    def binary(kind):
        while operators and operators[-1] != '(' and precedence[operators[-1]] >= precedence[kind]:
            output.append((operators.pop(), None))
        operators.append(kind)

    previous = None
    for kind, value in tokens:
        if previous in {'TERM', ')'} and kind in {'TERM', '(', 'NOT'}:
            binary('AND')
        if kind == 'TERM':
            words = value.casefold().split()
            pattern = r'(?<!\w)' + r'\s+'.join(re.escape(word) for word in words) + r'(?!\w)'
            output.append(('TERM', re.compile(pattern)))
            if value not in keywords:
                keywords.append(value)
        elif kind == '(':
            operators.append(kind)
        elif kind == ')':
            while operators and operators[-1] != '(':
                output.append((operators.pop(), None))
            operators.pop()  # The existing query validator has checked balance.
        elif kind == 'NOT':
            operators.append(kind)
        else:
            binary(kind)
        previous = kind
    while operators:
        output.append((operators.pop(), None))
    return tuple(output), ' '.join(keywords)


def _matches(expression, value):
    stack = []
    for kind, pattern in expression:
        if kind == 'TERM':
            stack.append(pattern.search(value) is not None)
        elif kind == 'NOT':
            stack.append(not stack.pop())
        else:
            right, left = stack.pop(), stack.pop()
            stack.append(left and right if kind == 'AND' else left or right)
    return len(stack) == 1 and stack[0]


def _publication_date(work):
    # Use an explicit publication/issued date, never creation/deposit/index time.
    for key in ('published', 'issued', 'published-online', 'published-print'):
        if key not in work:
            continue
        raw = work[key]
        parts = raw.get('date-parts') if isinstance(raw, Mapping) else None
        if not isinstance(parts, list) or len(parts) != 1 or not isinstance(parts[0], list):
            raise LiteratureValidationError('Crossref publication date is invalid')
        values = parts[0]
        if not 1 <= len(values) <= 3 or any(type(item) is not int for item in values):
            raise LiteratureValidationError('Crossref publication date is invalid')
        year = values[0]
        month = values[1] if len(values) >= 2 else 12
        # A partial current-year date must be wholly before the as-of cutoff.
        day = values[2] if len(values) == 3 else calendar.monthrange(year, month)[1]
        return date(year, month, day)
    raise LiteratureValidationError('Crossref record omitted publication date')


def _candidate(work, query, stratum, years, expression):
    try:
        source = next((source for source, kind in _CROSSREF_TYPES.items() if work.get('type') == kind), None)
        if source is None or source not in query.allowed_source_classes:
            return None
        if source is ScholarlySourceClass.PREPRINT and work.get('subtype') != 'preprint':
            return None
        identity = _identity('doi:' + _text(work.get('DOI'), 'Crossref DOI', maximum=512))
        doi = identity.partition(':')[2]
        titles = work.get('title')
        if not isinstance(titles, list) or not titles or not isinstance(titles[0], str):
            return None
        title = _plain(titles[0])
        abstract = work.get('abstract', '')
        if not isinstance(abstract, str):
            return None
        if not _matches(expression, (title + ' ' + _plain(abstract)).casefold()):
            return None
        published = _publication_date(work)
        if not years[0] <= published.year <= years[1] or published > date.fromisoformat(query.latest_as_of):
            return None
        language = work.get('language')
        if language is None and not query.filters.languages:
            language = 'und'
        authors_raw = work.get('author')
        if not isinstance(authors_raw, list) or not 1 <= len(authors_raw) <= MAX_AUTHORS:
            return None
        authors = []
        for author in authors_raw:
            if not isinstance(author, Mapping):
                return None
            if 'name' in author:
                name = _text(author['name'], 'Crossref author', maximum=256)
            else:
                family = _text(author.get('family'), 'Crossref family', maximum=256)
                given = author.get('given')
                name = family if given is None else _text(given, 'Crossref given', maximum=256) + ' ' + family
            if name not in authors:
                authors.append(name)
        pdfs = []
        links = work.get('link', [])
        for link in links if isinstance(links, list) else ():
            if not isinstance(link, Mapping) or link.get('content-type') != 'application/pdf':
                continue
            try:
                uri, _host = _uri_parts(link.get('URL'), 'Crossref PDF hint')
            except LiteratureError:
                continue
            if uri not in pdfs:
                pdfs.append(uri)
            if len(pdfs) == 17:
                break
        paper = SearchPaper(bibliographic_identity=identity, title=title, authors=tuple(authors),
            year=published.year, language=language, landing_uri='https://doi.org/' + quote(doi, safe='/'),
            source_uri='https://api.crossref.org/works/' + quote(doi, safe=''), source_class=source,
            stratum=stratum, pdf_uri=pdfs[0] if pdfs else None, alternate_pdf_uris=tuple(pdfs[1:]), venue=None)
        _validate_candidate_against_query(paper, query)
        return paper
    except (TypeError, ValueError):
        return None


def _preferred(work, paper, query):
    names = work.get('container-title', [])
    if not isinstance(names, list):
        return False
    normalized = {_normalized_venue_name(name) for name in names if isinstance(name, str)}
    return any(preference.source_class is paper.source_class and
               _normalized_venue_name(preference.name) in normalized for preference in query.preferred_venues)


class CrossrefSearch:
    """At most one anonymous request per stratum; no internal HTTP retry/page loop.

    Instances are used serially by the Host's existing literature worker. Spacing
    persists across calls but does not introduce a second scheduling authority.
    """

    def __init__(self, transport=None, proxy_router=None, clock=time.monotonic):
        self._transport = transport
        self._proxy_router = proxy_router
        self._clock = clock
        self._next_request_at = 0.0
        self._retry_at = 0.0
        self.network_routes = []

    def search(self, query, *, deadline, cancel_event=None) -> tuple[SearchPaper, ...]:
        self.network_routes = []
        if not isinstance(query, LiteratureQuery):
            raise LiteratureQueryError('Crossref search requires a LiteratureQuery')
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
            raise LiteratureQueryError('Crossref search requires a finite deadline')
        expression, keywords = _expression(query.query)
        ranges, quotas = _openalex_year_ranges(query), _stratum_quotas(query.max_items)
        papers, seen = [], set()
        for stratum, years, quota in zip(LITERATURE_STRATA, ranges, quotas, strict=True):
            self._remaining(deadline, cancel_event)
            rows = min(100, max(20, quota * 5))
            filters = [f'from-pub-date:{years[0]:04d}-01-01',
                       'until-pub-date:' + min(f'{years[1]:04d}-12-31', query.latest_as_of)]
            filters.extend('type:' + _CROSSREF_TYPES[source] for source in query.allowed_source_classes)
            target = '/works?' + urlencode({'query.bibliographic': keywords, 'filter': ','.join(filters), 'rows': rows})
            works = self._request(target, rows, deadline, cancel_event)
            candidates = []
            for work in works:
                self._remaining(deadline, cancel_event)
                paper = _candidate(work, query, stratum, years, expression)
                if paper is not None and paper.bibliographic_identity not in seen:
                    candidates.append((_preferred(work, paper, query), paper))
            candidates.sort(key=lambda item: not item[0])
            for _preferred_match, paper in candidates:
                if paper.bibliographic_identity in seen:
                    continue
                papers.append(paper)
                seen.add(paper.bibliographic_identity)
                quota -= 1
                if quota == 0:
                    break
        self._remaining(deadline, cancel_event)
        return tuple(papers)

    def _remaining(self, deadline, cancel_event):
        if cancel_event is not None and cancel_event.is_set():
            raise LiteratureFetchError('Crossref search cancelled', code=LiteratureFailureCode.PDF_DEADLINE)
        seconds = deadline - self._clock()
        if seconds <= 0:
            raise LiteratureFetchError('Crossref search deadline expired')
        return seconds

    def _request(self, target, rows, deadline, cancel_event):
        self._remaining(deadline, cancel_event)
        if self._retry_at > self._clock():
            raise LiteratureFetchError('Crossref metadata rate limit cooldown', http_status=429,
                retry_after_seconds=self._retry_at - self._clock())
        while self._next_request_at > self._clock():
            delay = self._next_request_at - self._clock()
            if delay >= self._remaining(deadline, cancel_event):
                raise LiteratureFetchError('Crossref search deadline expired before request spacing')
            if cancel_event is None:
                time.sleep(delay)
            else:
                cancel_event.wait(delay)
        timeout = min(10.0, self._remaining(deadline, cancel_event))
        self._next_request_at = self._clock() + 1.0
        arguments = dict(host='api.crossref.org', target=target,
            headers={'Accept': 'application/json', 'User-Agent': 'Hypoloom-literature/2'},
            timeout_seconds=timeout, max_bytes=_RESPONSE_BYTES)
        try:
            if self._transport is None:
                result = literature._metadata_https_get('crossref', **arguments,
                    cancel_event=cancel_event, proxy_router=self._proxy_router)
            else:
                result = self._transport.get(**arguments)
        except LiteratureError as exc:
            self.network_routes.extend(exc.network_routes[:max(0, 128-len(self.network_routes))])
            raise
        except Exception:
            raise LiteratureFetchError('Crossref metadata transport failed') from None
        self._remaining(deadline, cancel_event)
        if not isinstance(result, OpenAlexResponse):
            raise LiteratureFetchError('Crossref response is invalid')
        self.network_routes.extend(result.network_routes[:max(0, 128-len(self.network_routes))])
        if result.status_code != 200:
            from .provider_observation import retry_after_seconds
            delay = retry_after_seconds(result.retry_after, now=time.time())
            if result.status_code == 429:
                self._retry_at = self._clock() + max(1.0, delay or 0.0)
                self._next_request_at = max(self._next_request_at, self._retry_at)
            raise LiteratureFetchError('Crossref metadata HTTP request failed', http_status=result.status_code,
                retry_after_seconds=delay)
        if len(result.body) > _RESPONSE_BYTES or (result.content_length is not None and result.content_length != len(result.body)):
            raise LiteratureFetchError('Crossref response exceeded or mismatched its byte bound')
        if result.media_type is None or result.media_type.split(';', 1)[0].strip().casefold() != 'application/json':
            raise LiteratureFetchError('Crossref response was not application/json')
        try:
            payload = json.loads(result.body)
        except (UnicodeDecodeError, ValueError):
            raise LiteratureFetchError('Crossref response was not valid JSON') from None
        message = payload.get('message') if isinstance(payload, Mapping) else None
        items = message.get('items') if isinstance(message, Mapping) else None
        if (not isinstance(payload, Mapping) or payload.get('status') != 'ok' or
            payload.get('message-type') != 'work-list' or not isinstance(items, list) or
            len(items) > rows or not all(isinstance(item, Mapping) for item in items)):
            raise LiteratureFetchError('Crossref response omitted bounded work-list items')
        return items
