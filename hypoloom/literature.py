"""Fixed, bounded literature retrieval values and I/O validation.

This module is an outward adapter only.  It has no workflow/state authority,
artifact publisher, ambient credential source, or durable counter.  Its fixed
OpenAlex/HTTPS and Poppler implementations validate scholarly records, approve
public DNS addresses for PDF fetches, follow a bounded redirect chain, and
return PDF bytes separately from the JSON-safe literature packet.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import math
import os
import re
import socket
import stat
import subprocess
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from threading import BoundedSemaphore, Event, Lock
from typing import Any, Protocol
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit


LITERATURE_IO_VERSION = "fixed-literature-io/2"
LITERATURE_SCHEMA_VERSION = 2
_LITERATURE_IO_BY_SCHEMA = {
    1: "fixed-literature-io/1",
    LITERATURE_SCHEMA_VERSION: LITERATURE_IO_VERSION,
}
LITERATURE_PACKET_MEDIA_TYPE = "application/json"

MAX_QUERY_CHARS = 1_024
MAX_QUERY_ITEMS = 32
MAX_CONTEXT_REFS = 64
MAX_AUTHORS = 64
MAX_SUMMARY_CHARS = 16_384
MAX_CLAIM_REFS = 64
MAX_PREFERRED_VENUES = 6
MAX_PDF_FALLBACK_LOOKUPS = 8
PDF_FALLBACK_COOLDOWN_SECONDS = 60.0
MAX_PDF_HTTP_CALLS_PER_BATCH = 64
MAX_PDF_DOWNLOAD_EVENTS_PER_PAPER = 32
MAX_PDF_CACHE_ENTRIES = 32
MAX_PDF_CACHE_BYTES = 64 * 1024 * 1024


def bounded_context_refs(refs: tuple[str, ...], fact_refs: tuple[str, ...]) -> tuple[str, ...]:
    """Bound context refs to MAX_CONTEXT_REFS: non-fact refs plus the newest facts.

    Embedded fact inputs accumulate with every research cycle while a query
    template only needs the stable planning context plus the most recent
    facts; drop the oldest fact refs first and never drop non-fact refs.
    """
    if len(refs) <= MAX_CONTEXT_REFS: return refs
    base = tuple(ref for ref in refs if ref not in frozenset(fact_refs)); keep = max(0, MAX_CONTEXT_REFS - len(base))
    return (*base, *fact_refs[len(fact_refs) - keep:]) if keep else base
DEFAULT_OPENALEX_SEARCH_SECONDS = 60.0
DEFAULT_PDF_WORK_WINDOW_SECONDS = 120.0
DEFAULT_MAX_PDF_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_PDF_PAGES = 2_000
DEFAULT_MAX_REDIRECTS = 4
DEFAULT_OPENALEX_TIMEOUT_SECONDS = 15.0
DEFAULT_OPENALEX_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_OPENALEX_MAX_RETRIES = 2
DEFAULT_OPENALEX_RETRY_BACKOFF_SECONDS = 0.25
MAX_OPENALEX_RETRIES = 10
MAX_OPENALEX_RETRY_BACKOFF_SECONDS = 5.0
MAX_OPENALEX_RETRY_TRACE_EVENTS = 64
DEFAULT_POPPLER_TIMEOUT_SECONDS = 15.0
DEFAULT_POPPLER_TEXT_BYTES = 2 * 1024 * 1024

OPENALEX_API_HOST = "api.openalex.org"
SEMANTIC_SCHOLAR_API_HOST = "api.semanticscholar.org"

_LANGUAGE = re.compile(r"[a-z]{2,3}\Z")
_DOI = re.compile(r"10\.\d{4,9}/\S+\Z")
_ARXIV_PATH_ID = re.compile(
    r"(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7})(?:v\d+)?\Z",
    re.IGNORECASE,
)
_PMID = re.compile(r"\d+\Z")
_OPENALEX = re.compile(r"W\d+\Z", re.IGNORECASE)
_OPENALEX_SOURCE = re.compile(r"S\d+\Z", re.IGNORECASE)
_ISSN = re.compile(r"\d{4}-[\dXx]{4}\Z")
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_FORBIDDEN_HOSTS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
    }
)
_FORBIDDEN_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home", ".lan")


class LiteratureFailureCode(str, Enum):
    """Stable outward-boundary classifications; messages remain diagnostic only."""

    QUERY_INVALID = "QUERY_INVALID"
    FETCH_FAILED = "FETCH_FAILED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    OPENALEX_AUTH = "OPENALEX_AUTH"
    OPENALEX_RATE_LIMIT = "OPENALEX_RATE_LIMIT"
    OPENALEX_HTTP = "OPENALEX_HTTP"
    OPENALEX_TIMEOUT = "OPENALEX_TIMEOUT"
    OPENALEX_RESPONSE_LIMIT = "OPENALEX_RESPONSE_LIMIT"
    OPENALEX_RESPONSE_INVALID = "OPENALEX_RESPONSE_INVALID"
    PDF_TIMEOUT = "PDF_TIMEOUT"
    PDF_DEADLINE = "PDF_DEADLINE"
    PDF_REQUEST_BUDGET = "PDF_REQUEST_BUDGET"
    PDF_HTTP = "PDF_HTTP"
    PDF_TRANSPORT = "PDF_TRANSPORT"
    PDF_INVALID = "PDF_INVALID"
    PDF_RESPONSE_LIMIT = "PDF_RESPONSE_LIMIT"
    POPPLER_TIMEOUT = "POPPLER_TIMEOUT"
    POPPLER_FAILED = "POPPLER_FAILED"
    POPPLER_OUTPUT_LIMIT = "POPPLER_OUTPUT_LIMIT"
    POPPLER_INVALID = "POPPLER_INVALID"


class LiteratureError(ValueError):
    """Base class for a typed literature boundary rejection."""

    default_code = LiteratureFailureCode.VALIDATION_FAILED

    def __init__(
        self,
        message: str,
        *,
        code: LiteratureFailureCode | None = None,
        http_status: int | None = None,
        attempt_count: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = self.default_code if code is None else code
        if http_status is not None and (
            isinstance(http_status, bool)
            or not isinstance(http_status, int)
            or not 100 <= http_status <= 599
        ):
            raise ValueError("http_status must be an HTTP status code")
        self.http_status = http_status
        if attempt_count is not None and (
            isinstance(attempt_count, bool)
            or not isinstance(attempt_count, int)
            or attempt_count < 1
        ):
            raise ValueError("attempt_count must be a positive integer")
        self.attempt_count = attempt_count


class LiteratureQueryError(LiteratureError):
    """The caller supplied an invalid or ambiguous literature query."""

    default_code = LiteratureFailureCode.QUERY_INVALID


class LiteratureFetchError(LiteratureError):
    """A fetch, DNS, redirect, media, or PDF transport check failed."""

    default_code = LiteratureFailureCode.FETCH_FAILED


class _LiteratureDnsResolutionError(LiteratureFetchError):
    """One public candidate hostname could not produce a DNS answer."""

    def __init__(self, name: str, hostname: str) -> None:
        self.hostname = hostname
        super().__init__(f"{name} DNS resolution failed for {hostname}")


class LiteratureValidationError(LiteratureError):
    """Fetcher output or a packet/PDF trace was inconsistent."""

    default_code = LiteratureFailureCode.VALIDATION_FAILED


class LiteratureCoverageError(LiteratureValidationError):
    """A valid query did not cover every Host-required time stratum."""

    def __init__(
        self,
        message: str,
        *,
        missing_strata: tuple[LiteratureStratum, ...],
    ) -> None:
        if not missing_strata or any(
            not isinstance(item, LiteratureStratum) for item in missing_strata
        ):
            raise ValueError("missing_strata must contain LiteratureStratum values")
        self.missing_strata = missing_strata
        super().__init__(message)


class LiteratureStratum(str, Enum):
    FOUNDATIONAL = "FOUNDATIONAL"
    INTERMEDIATE = "INTERMEDIATE"
    LATEST_AS_OF_RUN = "LATEST_AS_OF_RUN"


LITERATURE_STRATA = tuple(LiteratureStratum)


class ScholarlySourceClass(str, Enum):
    JOURNAL = "JOURNAL"
    CONFERENCE = "CONFERENCE"
    PREPRINT = "PREPRINT"
    SCHOLARLY_DATASET = "SCHOLARLY_DATASET"
    TECHNICAL_REPORT = "TECHNICAL_REPORT"


class RetrievalDisposition(str, Enum):
    RETRIEVED = "RETRIEVED"
    PDF_UNAVAILABLE = "PDF_UNAVAILABLE"


@dataclass(frozen=True, slots=True, kw_only=True)
class PreferredVenue:
    """One Agent-suggested retrieval preference, never a ranking authority."""

    name: str
    source_class: ScholarlySourceClass

    def __post_init__(self) -> None:
        _text(self.name, "preferred venue name", maximum=256)
        if self.source_class not in {
            ScholarlySourceClass.JOURNAL,
            ScholarlySourceClass.CONFERENCE,
        }:
            raise LiteratureQueryError(
                "preferred venue source_class must be JOURNAL or CONFERENCE"
            )

    @property
    def identity_key(self) -> tuple[str, str]:
        return self.source_class.value, _normalized_venue_name(self.name)

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "source_class": self.source_class.value}

    @classmethod
    def from_dict(cls, value: object) -> PreferredVenue:
        raw = _strict(value, {"name", "source_class"}, "PreferredVenue")
        try:
            return cls(
                name=raw["name"],
                source_class=ScholarlySourceClass(raw["source_class"]),
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, LiteratureError):
                raise
            raise LiteratureQueryError(f"invalid PreferredVenue: {exc}") from exc


def _text(value: object, name: str, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise LiteratureValidationError(
            f"{name} must be non-empty bounded trimmed text without controls"
        )
    return value


def _strict(value: object, keys: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise LiteratureValidationError(f"{name} must be an object with text keys")
    unknown = set(value) - keys
    missing = keys - set(value)
    if unknown or missing:
        raise LiteratureValidationError(
            f"{name} unknown={sorted(unknown)!r} missing={sorted(missing)!r}"
        )
    return value


def _tuple_of_text(
    value: object,
    name: str,
    *,
    maximum_items: int,
    maximum_text: int,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if (
        not isinstance(value, (tuple, list))
        or len(value) > maximum_items
        or (not allow_empty and not value)
    ):
        raise LiteratureValidationError(f"{name} must be a bounded array")
    result = tuple(
        _text(item, f"{name} item", maximum=maximum_text) for item in value
    )
    if len(set(result)) != len(result):
        raise LiteratureValidationError(f"{name} must not contain duplicates")
    return result


def _year(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1000 <= value <= 9999:
        raise LiteratureValidationError(f"{name} must be a four-digit year")
    return value


def _as_of(value: object) -> str:
    raw = _text(value, "latest_as_of", maximum=10)
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise LiteratureQueryError("latest_as_of must be an ISO calendar date") from exc
    if parsed.isoformat() != raw:
        raise LiteratureQueryError("latest_as_of must use canonical YYYY-MM-DD")
    return raw


def _identity(value: object) -> str:
    raw = _text(value, "bibliographic_identity", maximum=512)
    scheme, separator, identifier = raw.partition(":")
    if not separator:
        raise LiteratureValidationError(
            "bibliographic_identity must use a supported scheme prefix"
        )
    scheme = scheme.casefold()
    identifier = identifier.strip()
    if scheme == "doi":
        identifier = identifier.casefold()
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if identifier.startswith(prefix):
                identifier = identifier[len(prefix) :]
                break
        if not _DOI.fullmatch(identifier):
            raise LiteratureValidationError("DOI bibliographic identity is invalid")
    elif scheme == "arxiv":
        identifier = identifier.casefold()
        if not identifier or any(character.isspace() for character in identifier):
            raise LiteratureValidationError("arXiv bibliographic identity is invalid")
    elif scheme == "pmid":
        if not _PMID.fullmatch(identifier):
            raise LiteratureValidationError("PMID bibliographic identity is invalid")
    elif scheme == "openalex":
        identifier = identifier.upper()
        if not _OPENALEX.fullmatch(identifier):
            raise LiteratureValidationError("OpenAlex bibliographic identity is invalid")
    elif scheme == "uri":
        _uri_parts(identifier, "bibliographic identity URI")
    else:
        raise LiteratureValidationError(
            "bibliographic_identity scheme must be doi, arxiv, pmid, openalex, or uri"
        )
    return f"{scheme}:{identifier}"


def _uri_parts(value: object, name: str) -> tuple[str, str]:
    uri = _text(value, name, maximum=4_096)
    try:
        parsed = urlsplit(uri)
        port = parsed.port
    except ValueError as exc:
        raise LiteratureFetchError(f"{name} is not a valid URI") from exc
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in {None, 443}
    ):
        raise LiteratureFetchError(
            f"{name} must be credential-free HTTPS on the default port without a fragment"
        )
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").casefold().rstrip(".")
    except UnicodeError as exc:
        raise LiteratureFetchError(f"{name} hostname is invalid") from exc
    if not hostname:
        raise LiteratureFetchError(f"{name} hostname is empty")
    if hostname in _FORBIDDEN_HOSTS or hostname.endswith(_FORBIDDEN_HOST_SUFFIXES):
        raise LiteratureFetchError(f"{name} hostname is not public")
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise LiteratureFetchError(f"{name} hostname is not globally routable")
    return uri, hostname


@dataclass(frozen=True, slots=True, kw_only=True)
class LiteratureFilters:
    year_from: int | None
    year_to: int | None
    languages: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.year_from is not None:
            _year(self.year_from, "filters.year_from")
        if self.year_to is not None:
            _year(self.year_to, "filters.year_to")
        if (
            self.year_from is not None
            and self.year_to is not None
            and self.year_from > self.year_to
        ):
            raise LiteratureQueryError("filters.year_from must not exceed year_to")
        languages = _tuple_of_text(
            self.languages,
            "filters.languages",
            maximum_items=16,
            maximum_text=3,
            allow_empty=True,
        )
        if any(_LANGUAGE.fullmatch(language) is None for language in languages):
            raise LiteratureQueryError("filter languages must be lowercase ISO-like codes")
        object.__setattr__(self, "languages", languages)

    def to_dict(self) -> dict[str, object]:
        return {
            "year_from": self.year_from,
            "year_to": self.year_to,
            "languages": list(self.languages),
        }

    @classmethod
    def from_dict(cls, value: object) -> LiteratureFilters:
        raw = _strict(value, {"year_from", "year_to", "languages"}, "LiteratureFilters")
        return cls(
            year_from=raw["year_from"],
            year_to=raw["year_to"],
            languages=raw["languages"],
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class LiteratureQuery:
    schema_version: int
    query: str
    filters: LiteratureFilters
    max_items: int
    allowed_source_classes: tuple[ScholarlySourceClass, ...]
    strata: tuple[LiteratureStratum, ...]
    latest_as_of: str
    immutable_context_refs: tuple[str, ...]
    preferred_venues: tuple[PreferredVenue, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version not in _LITERATURE_IO_BY_SCHEMA:
            raise LiteratureQueryError(
                "LiteratureQuery schema_version must be 1 or 2"
            )
        try:
            query = _text(self.query, "query", maximum=MAX_QUERY_CHARS)
        except LiteratureValidationError as exc:
            raise LiteratureQueryError(str(exc)) from exc
        if not isinstance(self.filters, LiteratureFilters):
            raise LiteratureQueryError("filters must be LiteratureFilters")
        if (
            isinstance(self.max_items, bool)
            or not isinstance(self.max_items, int)
            or not 1 <= self.max_items <= MAX_QUERY_ITEMS
        ):
            raise LiteratureQueryError(f"max_items must be 1..{MAX_QUERY_ITEMS}")
        if not isinstance(self.allowed_source_classes, tuple) or not all(
            isinstance(item, ScholarlySourceClass)
            for item in self.allowed_source_classes
        ):
            raise LiteratureQueryError("allowed_source_classes must contain closed enum values")
        if not self.allowed_source_classes or len(set(self.allowed_source_classes)) != len(
            self.allowed_source_classes
        ):
            raise LiteratureQueryError(
                "allowed_source_classes must be non-empty without duplicates"
            )
        if self.strata != LITERATURE_STRATA:
            raise LiteratureQueryError(
                "strata must contain FOUNDATIONAL, INTERMEDIATE, and LATEST_AS_OF_RUN in canonical order"
            )
        if self.max_items < len(self.strata):
            raise LiteratureQueryError("max_items must reserve at least one item per stratum")
        as_of = _as_of(self.latest_as_of)
        try:
            refs = _tuple_of_text(
                self.immutable_context_refs,
                "immutable_context_refs",
                maximum_items=MAX_CONTEXT_REFS,
                maximum_text=512,
                allow_empty=True,
            )
        except LiteratureValidationError as exc:
            raise LiteratureQueryError(str(exc)) from exc
        if not isinstance(self.preferred_venues, tuple) or not all(
            isinstance(item, PreferredVenue) for item in self.preferred_venues
        ):
            raise LiteratureQueryError(
                "preferred_venues must be an array of PreferredVenue values"
            )
        if len(self.preferred_venues) > MAX_PREFERRED_VENUES:
            raise LiteratureQueryError(
                f"preferred_venues must contain at most {MAX_PREFERRED_VENUES} items"
            )
        venue_keys = [item.identity_key for item in self.preferred_venues]
        if len(set(venue_keys)) != len(venue_keys):
            raise LiteratureQueryError("preferred_venues must not contain duplicates")
        if self.schema_version == 1 and self.preferred_venues:
            raise LiteratureQueryError(
                "LiteratureQuery/v1 cannot contain preferred_venues"
            )
        object.__setattr__(self, "query", query)
        object.__setattr__(self, "latest_as_of", as_of)
        object.__setattr__(self, "immutable_context_refs", refs)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": self.schema_version,
            "query": self.query,
            "filters": self.filters.to_dict(),
            "max_items": self.max_items,
            "allowed_source_classes": [item.value for item in self.allowed_source_classes],
            "strata": [item.value for item in self.strata],
            "latest_as_of": self.latest_as_of,
            "immutable_context_refs": list(self.immutable_context_refs),
        }
        if self.schema_version >= 2:
            result["preferred_venues"] = [
                item.to_dict() for item in self.preferred_venues
            ]
        return result

    @classmethod
    def from_dict(cls, value: object) -> LiteratureQuery:
        if not isinstance(value, Mapping):
            raise LiteratureValidationError("LiteratureQuery must be an object")
        version = value.get("schema_version")
        base_keys = {
            "schema_version", "query", "filters", "max_items",
            "allowed_source_classes", "strata", "latest_as_of",
            "immutable_context_refs",
        }
        raw = _strict(
            value,
            base_keys | ({"preferred_venues"} if version == 2 else set()),
            "LiteratureQuery",
        )
        try:
            sources = tuple(
                ScholarlySourceClass(item) for item in raw["allowed_source_classes"]
            )
            strata = tuple(LiteratureStratum(item) for item in raw["strata"])
            venues = tuple(
                PreferredVenue.from_dict(item)
                for item in raw.get("preferred_venues", ())
            )
        except (TypeError, ValueError) as exc:
            raise LiteratureQueryError(f"LiteratureQuery contains an unknown enum: {exc}") from exc
        return cls(
            schema_version=raw["schema_version"],
            query=raw["query"],
            filters=LiteratureFilters.from_dict(raw["filters"]),
            max_items=raw["max_items"],
            allowed_source_classes=sources,
            strata=strata,
            latest_as_of=raw["latest_as_of"],
            immutable_context_refs=raw["immutable_context_refs"],
            preferred_venues=venues,
        )


_QUERY_PLAN_FROZEN_FIELDS = (
    "schema_version",
    "filters",
    "max_items",
    "allowed_source_classes",
    "strata",
    "latest_as_of",
    "immutable_context_refs",
)
_BOOLEAN_OPERATORS = frozenset({"AND", "OR", "NOT"})
_QUERY_PLAN_FORBIDDEN_CHARACTERS = frozenset(
    "*?~:/\\#[]{}<>`!|"
)


def _query_plan_term(token: str, *, phrase: bool = False) -> None:
    words = token.split() if phrase else (token,)
    if not words:
        raise LiteratureQueryError("query plan contains an empty quoted phrase")
    for word in words:
        if (
            not word
            or not word[0].isalnum()
            or not word[-1].isalnum()
            or any(
                not (character.isalnum() or character in {"-", "_", "'"})
                for character in word
            )
        ):
            raise LiteratureQueryError(
                "query plan terms may contain only letters, numbers, internal hyphens, underscores, or apostrophes"
            )


def _openalex_boolean_tokens(query: str) -> tuple[tuple[str, str], ...]:
    if any(character in _QUERY_PLAN_FORBIDDEN_CHARACTERS for character in query):
        raise LiteratureQueryError(
            "query plan must not contain wildcards, URLs, paths, or Markdown"
        )
    tokens: list[tuple[str, str]] = []
    index = 0
    while index < len(query):
        if query[index].isspace():
            index += 1
            continue
        character = query[index]
        if character in "()":
            tokens.append((character, character))
            index += 1
            continue
        if character == '"':
            end = query.find('"', index + 1)
            if end < 0:
                raise LiteratureQueryError("query plan has an unterminated quoted phrase")
            phrase = query[index + 1:end]
            _query_plan_term(phrase, phrase=True)
            tokens.append(("TERM", phrase))
            index = end + 1
            continue
        end = index
        while (
            end < len(query)
            and not query[end].isspace()
            and query[end] not in '()"'
        ):
            end += 1
        token = query[index:end]
        upper = token.upper()
        if upper in _BOOLEAN_OPERATORS:
            if token != upper:
                raise LiteratureQueryError(
                    "query plan Boolean operators must be uppercase AND, OR, or NOT"
                )
            tokens.append((upper, token))
        else:
            _query_plan_term(token)
            tokens.append(("TERM", token))
        index = end
    return tuple(tokens)


def _validate_openalex_boolean_query(query: str) -> None:
    # LiteratureQuery has already enforced trimming, controls, and MAX_QUERY_CHARS.
    tokens = _openalex_boolean_tokens(query)
    if not tokens:
        raise LiteratureQueryError("query plan must contain at least one term")
    depth = 0
    expecting_operand = True
    previous_kind: str | None = None
    for kind, _token in tokens:
        if kind in {"AND", "OR"}:
            if expecting_operand:
                raise LiteratureQueryError("query plan has a misplaced Boolean operator")
            expecting_operand = True
        elif kind == "NOT":
            # An adjacent NOT is the safe OpenAlex form of implicit AND NOT.
            if previous_kind == "NOT":
                raise LiteratureQueryError("query plan must not contain consecutive NOT operators")
            expecting_operand = True
        elif kind == "(":
            depth += 1
            expecting_operand = True
        elif kind == ")":
            if expecting_operand or depth == 0:
                raise LiteratureQueryError("query plan has invalid parentheses")
            depth -= 1
            expecting_operand = False
        else:
            expecting_operand = False
        previous_kind = kind
    if expecting_operand:
        raise LiteratureQueryError("query plan must end with a term or phrase")
    if depth:
        raise LiteratureQueryError("query plan has unbalanced parentheses")


def admit_literature_query_plan(
    candidate: LiteratureQuery,
    *,
    template: LiteratureQuery,
) -> LiteratureQuery:
    """Admit an Agent query without granting it control of Host policy fields."""

    if not isinstance(candidate, LiteratureQuery) or not isinstance(
        template, LiteratureQuery
    ):
        raise LiteratureQueryError("query plan candidate and template must be LiteratureQuery values")
    changed = tuple(
        field
        for field in _QUERY_PLAN_FROZEN_FIELDS
        if getattr(candidate, field) != getattr(template, field)
    )
    if changed:
        raise LiteratureQueryError(
            f"query plan changed frozen template fields: {', '.join(changed)}"
        )
    _validate_openalex_boolean_query(candidate.query)
    return candidate


def _profile_search_text(objective: str) -> str:
    # The first non-empty goal line is the profile's bounded topic, while the
    # remaining Markdown is an execution contract. Sending that whole contract
    # made OpenAlex implicitly AND paths, IDs and formatting operators together.
    line = next((item.strip() for item in objective.splitlines() if item.strip()), "")
    visible = " ".join(re.sub(r"[^\w\s]", " ", line).split())
    return visible[:MAX_QUERY_CHARS].strip()


def expert_literature_query(
    contract: Mapping[str, Any],
    *,
    component_id: str,
    role: str,
    latest_as_of: str,
    immutable_context_refs: tuple[str, ...],
) -> LiteratureQuery:
    """Derive one frozen expert query from the already-accepted contract."""

    goal = contract.get("goal")
    components = contract.get("components")
    roles = contract.get("roles")
    if not isinstance(goal, Mapping) or not isinstance(components, (list, tuple)):
        raise LiteratureQueryError("expert literature contract is invalid")
    component = next(
        (item for item in components if isinstance(item, Mapping) and item.get("id") == component_id),
        None,
    )
    role_record = next(
        (item for item in roles or () if isinstance(item, Mapping) and item.get("id") == role),
        None,
    )
    if component is None or role_record is None:
        raise LiteratureQueryError("expert literature component or role is unknown")
    result = component.get("result", {})
    budget = role_record.get("budget", {})
    if not isinstance(result, Mapping) or not isinstance(budget, Mapping):
        raise LiteratureQueryError("expert literature component or role configuration is invalid")
    max_items = budget.get("max_papers", contract.get("budget", {}).get("max_papers", 6))
    if isinstance(max_items, int) and not isinstance(max_items, bool):
        max_items = min(max_items, MAX_QUERY_ITEMS)
    objective = goal.get("objective")
    if not isinstance(objective, str):
        raise LiteratureQueryError("expert literature goal objective is invalid")
    latest_year = date.fromisoformat(latest_as_of).year
    query = _profile_search_text(objective)
    return LiteratureQuery(
        schema_version=LITERATURE_SCHEMA_VERSION,
        query=query,
        filters=LiteratureFilters(
            year_from=max(1000, latest_year - 29),
            year_to=latest_year,
            languages=("en",),
        ),
        max_items=max_items,
        allowed_source_classes=(
            ScholarlySourceClass.JOURNAL,
            ScholarlySourceClass.CONFERENCE,
            ScholarlySourceClass.PREPRINT,
            ScholarlySourceClass.TECHNICAL_REPORT,
        ),
        strata=LITERATURE_STRATA,
        latest_as_of=latest_as_of,
        immutable_context_refs=immutable_context_refs,
    )


def grounding_literature_query(
    contract: Mapping[str, Any],
    *,
    latest_as_of: str,
    immutable_context_refs: tuple[str, ...],
) -> LiteratureQuery:
    """Derive one role-neutral scholarly query for the Run Grounding prelude."""

    goal = contract.get("goal")
    components = contract.get("components")
    roles = contract.get("roles")
    if (
        not isinstance(goal, Mapping)
        or not isinstance(components, (list, tuple))
        or not isinstance(roles, (list, tuple))
    ):
        raise LiteratureQueryError("grounding literature contract is invalid")
    objective = goal.get("objective")
    if not isinstance(objective, str):
        raise LiteratureQueryError("grounding literature goal objective is invalid")
    for component in components:
        if not isinstance(component, Mapping):
            raise LiteratureQueryError("grounding literature component is invalid")
        result = component.get("result", {})
        if not isinstance(result, Mapping):
            raise LiteratureQueryError("grounding literature result is invalid")
    paper_limits: list[int] = []
    for role in roles:
        if not isinstance(role, Mapping) or not isinstance(role.get("budget"), Mapping):
            raise LiteratureQueryError("grounding literature role budget is invalid")
        value = role["budget"].get("max_papers")
        if isinstance(value, bool) or not isinstance(value, int):
            raise LiteratureQueryError("grounding literature max_papers is invalid")
        paper_limits.append(value)
    if not paper_limits:
        raise LiteratureQueryError("grounding literature requires an expert budget")
    latest_year = date.fromisoformat(latest_as_of).year
    query = _profile_search_text(objective)
    return LiteratureQuery(
        schema_version=LITERATURE_SCHEMA_VERSION,
        query=query,
        filters=LiteratureFilters(
            year_from=max(1000, latest_year - 29),
            year_to=latest_year,
            languages=("en",),
        ),
        max_items=min(min(paper_limits), MAX_QUERY_ITEMS),
        allowed_source_classes=(
            ScholarlySourceClass.JOURNAL,
            ScholarlySourceClass.CONFERENCE,
            ScholarlySourceClass.PREPRINT,
            ScholarlySourceClass.TECHNICAL_REPORT,
        ),
        strata=LITERATURE_STRATA,
        latest_as_of=latest_as_of,
        immutable_context_refs=immutable_context_refs,
    )


def _normalized_venue_name(value: str) -> str:
    return " ".join(re.sub(r"[^0-9a-z]+", " ", value.casefold()).split())


@dataclass(frozen=True, slots=True, kw_only=True)
class LiteratureVenue:
    """Resolved OpenAlex Source metadata carried in the existing packet."""

    source_id: str
    display_name: str
    source_class: ScholarlySourceClass
    issn_l: str | None

    def __post_init__(self) -> None:
        raw = _text(self.source_id, "venue source_id", maximum=64)
        scheme, separator, identifier = raw.partition(":")
        if separator != ":" or scheme.casefold() != "openalex" or not _OPENALEX_SOURCE.fullmatch(identifier):
            raise LiteratureValidationError(
                "venue source_id must be an OpenAlex Source identity"
            )
        object.__setattr__(self, "source_id", f"openalex:{identifier.upper()}")
        _text(self.display_name, "venue display_name", maximum=512)
        if self.source_class not in {
            ScholarlySourceClass.JOURNAL,
            ScholarlySourceClass.CONFERENCE,
        }:
            raise LiteratureValidationError(
                "venue source_class must be JOURNAL or CONFERENCE"
            )
        if self.issn_l is not None and _ISSN.fullmatch(
            _text(self.issn_l, "venue issn_l", maximum=9)
        ) is None:
            raise LiteratureValidationError("venue issn_l is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "display_name": self.display_name,
            "source_class": self.source_class.value,
            "issn_l": self.issn_l,
        }

    @classmethod
    def from_dict(cls, value: object) -> LiteratureVenue:
        raw = _strict(
            value,
            {"source_id", "display_name", "source_class", "issn_l"},
            "LiteratureVenue",
        )
        try:
            return cls(
                source_id=raw["source_id"],
                display_name=raw["display_name"],
                source_class=ScholarlySourceClass(raw["source_class"]),
                issn_l=raw["issn_l"],
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, LiteratureError):
                raise
            raise LiteratureValidationError(f"invalid LiteratureVenue: {exc}") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchPaper:
    """One untrusted normalized search record returned by the fixed fetcher."""

    bibliographic_identity: str
    title: str
    authors: tuple[str, ...]
    year: int
    language: str
    landing_uri: str
    source_uri: str
    source_class: ScholarlySourceClass
    stratum: LiteratureStratum
    pdf_uri: str | None
    alternate_pdf_uris: tuple[str, ...] = ()
    venue: LiteratureVenue | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "bibliographic_identity", _identity(self.bibliographic_identity))
        _text(self.title, "paper title", maximum=1_024)
        authors = _tuple_of_text(
            self.authors,
            "paper authors",
            maximum_items=MAX_AUTHORS,
            maximum_text=256,
        )
        _year(self.year, "paper year")
        language = _text(self.language, "paper language", maximum=3)
        if _LANGUAGE.fullmatch(language) is None:
            raise LiteratureValidationError("paper language must be lowercase ISO-like text")
        _uri_parts(self.landing_uri, "landing_uri")
        _uri_parts(self.source_uri, "source_uri")
        if not isinstance(self.source_class, ScholarlySourceClass):
            raise LiteratureValidationError("paper source_class is unknown")
        if not isinstance(self.stratum, LiteratureStratum):
            raise LiteratureValidationError("paper stratum is unknown")
        if self.pdf_uri is not None:
            _uri_parts(self.pdf_uri, "pdf_uri")
        alternates = _tuple_of_text(
            self.alternate_pdf_uris,
            "alternate_pdf_uris",
            maximum_items=16,
            maximum_text=4_096,
            allow_empty=True,
        )
        for uri in alternates:
            _uri_parts(uri, "alternate_pdf_uri")
        if self.pdf_uri is not None and self.pdf_uri in alternates:
            raise LiteratureValidationError(
                "alternate_pdf_uris must not repeat pdf_uri"
            )
        if self.venue is not None and not isinstance(self.venue, LiteratureVenue):
            raise LiteratureValidationError("paper venue must be LiteratureVenue")
        object.__setattr__(self, "authors", authors)
        object.__setattr__(self, "alternate_pdf_uris", alternates)

    @property
    def pdf_uris(self) -> tuple[str, ...]:
        return (() if self.pdf_uri is None else (self.pdf_uri,)) + self.alternate_pdf_uris


@dataclass(frozen=True, slots=True, kw_only=True)
class FetchResponse:
    requested_uri: str
    status_code: int
    peer_address: str
    media_type: str | None
    content_length: int | None
    location: str | None
    body: bytes

    def __post_init__(self) -> None:
        _uri_parts(self.requested_uri, "fetch response requested_uri")
        if (
            isinstance(self.status_code, bool)
            or not isinstance(self.status_code, int)
            or not 100 <= self.status_code <= 599
        ):
            raise LiteratureValidationError("fetch response status_code is invalid")
        _text(self.peer_address, "fetch response peer_address", maximum=64)
        if self.media_type is not None:
            _text(self.media_type, "fetch response media_type", maximum=256)
        if self.content_length is not None and (
            isinstance(self.content_length, bool)
            or not isinstance(self.content_length, int)
            or self.content_length < 0
        ):
            raise LiteratureValidationError("fetch response content_length is invalid")
        if self.location is not None:
            _text(self.location, "fetch response location", maximum=4_096)
        if not isinstance(self.body, bytes):
            raise LiteratureValidationError("fetch response body must be bytes")


@dataclass(frozen=True, slots=True, kw_only=True)
class ClaimPageRef:
    claim_ref: str
    section: str
    page_start: int
    page_end: int

    def __post_init__(self) -> None:
        _text(self.claim_ref, "claim_ref", maximum=512)
        _text(self.section, "claim section", maximum=512)
        for name, value in (("page_start", self.page_start), ("page_end", self.page_end)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise LiteratureValidationError(f"{name} must be a positive integer")
        if self.page_start > self.page_end:
            raise LiteratureValidationError("claim page_start must not exceed page_end")

    def to_dict(self) -> dict[str, object]:
        return {
            "claim_ref": self.claim_ref,
            "section": self.section,
            "page_start": self.page_start,
            "page_end": self.page_end,
        }

    @classmethod
    def from_dict(cls, value: object) -> ClaimPageRef:
        raw = _strict(
            value,
            {"claim_ref", "section", "page_start", "page_end"},
            "ClaimPageRef",
        )
        return cls(**dict(raw))


@dataclass(frozen=True, slots=True, kw_only=True)
class PdfDocument:
    """Text from exact PDF bytes, indexed by physical page, without section assumptions."""

    page_count: int
    pages: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.page_count) is not int or self.page_count <= 0:
            raise LiteratureValidationError("PDF page_count must be positive")
        if (not isinstance(self.pages, tuple) or len(self.pages) != self.page_count
                or not all(isinstance(page, str) for page in self.pages)):
            raise LiteratureValidationError("PDF pages must match physical page_count")


@dataclass(frozen=True, slots=True, kw_only=True)
class PdfExtraction:
    """Bounded extraction supplied by the reviewed fetcher for exact PDF bytes."""

    page_count: int
    abstract_summary: str
    conclusions: str
    claim_support: tuple[ClaimPageRef, ...]
    pages: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            isinstance(self.page_count, bool)
            or not isinstance(self.page_count, int)
            or self.page_count <= 0
        ):
            raise LiteratureValidationError("PDF page_count must be positive")
        _text(self.abstract_summary, "PDF abstract_summary", maximum=MAX_SUMMARY_CHARS)
        _text(self.conclusions, "PDF conclusions", maximum=MAX_SUMMARY_CHARS)
        if (
            not isinstance(self.claim_support, tuple)
            or not self.claim_support
            or len(self.claim_support) > MAX_CLAIM_REFS
            or not all(isinstance(item, ClaimPageRef) for item in self.claim_support)
        ):
            raise LiteratureValidationError("PDF claim_support must be a bounded non-empty tuple")
        refs = [item.claim_ref for item in self.claim_support]
        if len(set(refs)) != len(refs):
            raise LiteratureValidationError("PDF claim refs must not contain duplicates")
        if any(item.page_end > self.page_count for item in self.claim_support):
            raise LiteratureValidationError("PDF claim page ref exceeds page_count")
        if self.pages:
            PdfDocument(page_count=self.page_count, pages=self.pages)


@dataclass(frozen=True, slots=True, kw_only=True)
class PdfTrace:
    source_uri: str
    final_uri: str
    media_type: str
    artifact_ref: str
    page_count: int
    abstract_summary: str
    conclusions: str
    claim_support: tuple[ClaimPageRef, ...]

    def __post_init__(self) -> None:
        _uri_parts(self.source_uri, "PDF trace source_uri")
        _uri_parts(self.final_uri, "PDF trace final_uri")
        if self.media_type != "application/pdf":
            raise LiteratureValidationError("PDF trace media_type must be application/pdf")
        _text(self.artifact_ref, "PDF artifact_ref", maximum=512)
        extraction = PdfExtraction(
            page_count=self.page_count,
            abstract_summary=self.abstract_summary,
            conclusions=self.conclusions,
            claim_support=self.claim_support,
        )
        object.__setattr__(self, "claim_support", extraction.claim_support)

    def to_dict(self) -> dict[str, object]:
        return {
            "source_uri": self.source_uri,
            "final_uri": self.final_uri,
            "media_type": self.media_type,
            "artifact_ref": self.artifact_ref,
            "page_count": self.page_count,
            "abstract_summary": self.abstract_summary,
            "conclusions": self.conclusions,
            "claim_support": [item.to_dict() for item in self.claim_support],
        }

    @classmethod
    def from_dict(cls, value: object) -> PdfTrace:
        raw = _strict(
            value,
            {
                "source_uri",
                "final_uri",
                "media_type",
                "artifact_ref",
                "page_count",
                "abstract_summary",
                "conclusions",
                "claim_support",
            },
            "PdfTrace",
        )
        try:
            support = tuple(ClaimPageRef.from_dict(item) for item in raw["claim_support"])
        except TypeError as exc:
            raise LiteratureValidationError("PdfTrace claim_support must be an array") from exc
        return cls(**{**dict(raw), "claim_support": support})


@dataclass(frozen=True, slots=True, kw_only=True)
class LiteraturePaper:
    bibliographic_identity: str
    title: str
    authors: tuple[str, ...]
    year: int
    language: str
    landing_uri: str
    source_uri: str
    source_class: ScholarlySourceClass
    stratum: LiteratureStratum
    retrieval_disposition: RetrievalDisposition
    claim_refs: tuple[str, ...]
    pdf_trace: PdfTrace | None
    venue: LiteratureVenue | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "bibliographic_identity", _identity(self.bibliographic_identity))
        _text(self.title, "paper title", maximum=1_024)
        object.__setattr__(
            self,
            "authors",
            _tuple_of_text(
                self.authors,
                "paper authors",
                maximum_items=MAX_AUTHORS,
                maximum_text=256,
            ),
        )
        _year(self.year, "paper year")
        language = _text(self.language, "paper language", maximum=3)
        if _LANGUAGE.fullmatch(language) is None:
            raise LiteratureValidationError(
                "paper language must be lowercase ISO-like text"
            )
        _uri_parts(self.landing_uri, "landing_uri")
        _uri_parts(self.source_uri, "source_uri")
        if not isinstance(self.source_class, ScholarlySourceClass):
            raise LiteratureValidationError("paper source_class is unknown")
        if not isinstance(self.stratum, LiteratureStratum):
            raise LiteratureValidationError("paper stratum is unknown")
        if self.venue is not None and not isinstance(self.venue, LiteratureVenue):
            raise LiteratureValidationError("paper venue must be LiteratureVenue")
        if not isinstance(self.retrieval_disposition, RetrievalDisposition):
            raise LiteratureValidationError("paper retrieval_disposition is unknown")
        claims = _tuple_of_text(
            self.claim_refs,
            "paper claim_refs",
            maximum_items=MAX_CLAIM_REFS,
            maximum_text=512,
            allow_empty=True,
        )
        if self.retrieval_disposition is RetrievalDisposition.PDF_UNAVAILABLE:
            if self.pdf_trace is not None or claims:
                raise LiteratureValidationError(
                    "PDF_UNAVAILABLE paper cannot carry a PDF trace or support a claim"
                )
        else:
            if not isinstance(self.pdf_trace, PdfTrace) or not claims:
                raise LiteratureValidationError(
                    "RETRIEVED paper requires a PDF trace and claim refs"
                )
            trace_refs = tuple(item.claim_ref for item in self.pdf_trace.claim_support)
            if claims != trace_refs:
                raise LiteratureValidationError(
                    "paper claim_refs must exactly match its PDF claim-support refs"
                )
        object.__setattr__(self, "claim_refs", claims)

    @property
    def order_key(self) -> tuple[int, str]:
        return LITERATURE_STRATA.index(self.stratum), self.bibliographic_identity

    def to_dict(self, *, schema_version: int = LITERATURE_SCHEMA_VERSION) -> dict[str, object]:
        result: dict[str, object] = {
            "bibliographic_identity": self.bibliographic_identity,
            "title": self.title,
            "authors": list(self.authors),
            "year": self.year,
            "language": self.language,
            "landing_uri": self.landing_uri,
            "source_uri": self.source_uri,
            "source_class": self.source_class.value,
            "stratum": self.stratum.value,
            "retrieval_disposition": self.retrieval_disposition.value,
            "claim_refs": list(self.claim_refs),
            "pdf_trace": None if self.pdf_trace is None else self.pdf_trace.to_dict(),
        }
        if schema_version >= 2:
            result["venue"] = None if self.venue is None else self.venue.to_dict()
        return result

    @classmethod
    def from_dict(
        cls, value: object, *, schema_version: int = LITERATURE_SCHEMA_VERSION
    ) -> LiteraturePaper:
        raw = _strict(
            value,
            {
                "bibliographic_identity",
                "title",
                "authors",
                "year",
                "language",
                "landing_uri",
                "source_uri",
                "source_class",
                "stratum",
                "retrieval_disposition",
                "claim_refs",
                "pdf_trace",
            } | ({"venue"} if schema_version >= 2 else set()),
            "LiteraturePaper",
        )
        try:
            return cls(
                bibliographic_identity=raw["bibliographic_identity"],
                title=raw["title"],
                authors=raw["authors"],
                year=raw["year"],
                language=raw["language"],
                landing_uri=raw["landing_uri"],
                source_uri=raw["source_uri"],
                source_class=ScholarlySourceClass(raw["source_class"]),
                stratum=LiteratureStratum(raw["stratum"]),
                retrieval_disposition=RetrievalDisposition(
                    raw["retrieval_disposition"]
                ),
                claim_refs=raw["claim_refs"],
                pdf_trace=(
                    None
                    if raw["pdf_trace"] is None
                    else PdfTrace.from_dict(raw["pdf_trace"])
                ),
                venue=(
                    None
                    if raw.get("venue") is None
                    else LiteratureVenue.from_dict(raw["venue"])
                ),
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, LiteratureError):
                raise
            raise LiteratureValidationError(f"invalid LiteraturePaper: {exc}") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class LiteraturePacket:
    schema_version: int
    adapter_version: str
    query: LiteratureQuery
    papers: tuple[LiteraturePaper, ...]

    def __post_init__(self) -> None:
        if self.schema_version not in _LITERATURE_IO_BY_SCHEMA:
            raise LiteratureValidationError(
                "LiteraturePacket schema_version must be 1 or 2"
            )
        if self.adapter_version != _LITERATURE_IO_BY_SCHEMA[self.schema_version]:
            raise LiteratureValidationError(
                "LiteraturePacket adapter_version does not match its schema_version"
            )
        if not isinstance(self.query, LiteratureQuery):
            raise LiteratureValidationError("packet query must be LiteratureQuery")
        if self.query.schema_version != self.schema_version:
            raise LiteratureValidationError(
                "packet query schema_version must match the packet"
            )
        if (
            not isinstance(self.papers, tuple)
            or not self.papers
            or len(self.papers) > self.query.max_items
            or not all(isinstance(item, LiteraturePaper) for item in self.papers)
        ):
            raise LiteratureValidationError("packet papers must be a bounded non-empty tuple")
        identities = [item.bibliographic_identity for item in self.papers]
        if len(set(identities)) != len(identities):
            raise LiteratureValidationError("packet bibliographic identities must be unique")
        if self.papers != tuple(sorted(self.papers, key=lambda item: item.order_key)):
            raise LiteratureValidationError("packet papers are not in canonical order")
        represented = {item.stratum for item in self.papers}
        if represented != set(self.query.strata):
            raise LiteratureValidationError("packet must represent every requested stratum")
        for paper in self.papers:
            if self.schema_version == 1 and paper.venue is not None:
                raise LiteratureValidationError(
                    "LiteraturePacket/v1 cannot carry venue metadata"
                )
            _validate_paper_against_query(paper, self.query)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "adapter_version": self.adapter_version,
            "query": self.query.to_dict(),
            "papers": [
                item.to_dict(schema_version=self.schema_version)
                for item in self.papers
            ],
        }

    def to_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @classmethod
    def from_dict(cls, value: object) -> LiteraturePacket:
        raw = _strict(
            value,
            {"schema_version", "adapter_version", "query", "papers"},
            "LiteraturePacket",
        )
        query = LiteratureQuery.from_dict(raw["query"])
        version = raw["schema_version"]
        if version not in _LITERATURE_IO_BY_SCHEMA:
            raise LiteratureValidationError(
                "LiteraturePacket schema_version must be 1 or 2"
            )
        try:
            papers = tuple(
                LiteraturePaper.from_dict(item, schema_version=version)
                for item in raw["papers"]
            )
        except TypeError as exc:
            raise LiteratureValidationError("LiteraturePacket papers must be an array") from exc
        return cls(
            schema_version=raw["schema_version"],
            adapter_version=raw["adapter_version"],
            query=query,
            papers=papers,
        )

    @classmethod
    def from_bytes(cls, value: bytes) -> LiteraturePacket:
        if not isinstance(value, bytes):
            raise LiteratureValidationError("LiteraturePacket bytes must be bytes")
        try:
            raw = json.loads(value)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiteratureValidationError("LiteraturePacket bytes are not JSON") from exc
        return cls.from_dict(raw)


@dataclass(frozen=True, slots=True, kw_only=True)
class RetrievedPdfArtifact:
    artifact_ref: str
    media_type: str
    content: bytes
    pages: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.artifact_ref, "retrieved PDF artifact_ref", maximum=512)
        if self.media_type != "application/pdf":
            raise LiteratureValidationError(
                "retrieved PDF artifact media_type must be application/pdf"
            )
        if not isinstance(self.content, bytes) or not self.content:
            raise LiteratureValidationError("retrieved PDF artifact content must be bytes")


@dataclass(frozen=True, slots=True, kw_only=True)
class LiteratureRetrieval:
    packet: LiteraturePacket
    pdf_artifacts: tuple[RetrievedPdfArtifact, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.packet, LiteraturePacket):
            raise LiteratureValidationError("retrieval packet must be LiteraturePacket")
        if not isinstance(self.pdf_artifacts, tuple) or not all(
            isinstance(item, RetrievedPdfArtifact) for item in self.pdf_artifacts
        ):
            raise LiteratureValidationError("pdf_artifacts must be a tuple")
        artifacts = {item.artifact_ref: item for item in self.pdf_artifacts}
        if len(artifacts) != len(self.pdf_artifacts):
            raise LiteratureValidationError("retrieved PDF artifact refs must be unique")
        packet_refs = {
            paper.pdf_trace.artifact_ref
            for paper in self.packet.papers
            if paper.pdf_trace is not None
        }
        if set(artifacts) != packet_refs:
            raise LiteratureValidationError(
                "retrieved PDF artifacts must exactly match packet artifact refs"
            )


class LiteratureFetcher(Protocol):
    """Reviewed injected I/O implementation; it must not carry ambient auth."""

    def search(self, query: LiteratureQuery) -> Iterable[SearchPaper]: ...

    def resolve(self, hostname: str) -> Sequence[str]: ...

    def fetch(
        self,
        uri: str,
        *,
        approved_addresses: tuple[str, ...],
        max_bytes: int,
    ) -> FetchResponse: ...

    def extract_pdf(self, content: bytes, *, max_pages: int) -> PdfExtraction: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenAlexResponse:
    """One bounded API response returned by an injected HTTPS transport."""

    status_code: int
    media_type: str | None
    content_length: int | None
    body: bytes

    def __post_init__(self) -> None:
        if (
            isinstance(self.status_code, bool)
            or not isinstance(self.status_code, int)
            or not 100 <= self.status_code <= 599
        ):
            raise LiteratureValidationError("OpenAlex status_code is invalid")
        if self.media_type is not None:
            _text(self.media_type, "OpenAlex media_type", maximum=256)
        if self.content_length is not None and (
            isinstance(self.content_length, bool)
            or not isinstance(self.content_length, int)
            or self.content_length < 0
        ):
            raise LiteratureValidationError("OpenAlex content_length is invalid")
        if not isinstance(self.body, bytes):
            raise LiteratureValidationError("OpenAlex body must be bytes")


class OpenAlexTransport(Protocol):
    """Exact-host HTTPS GET boundary used by ``OpenAlexLiteratureFetcher``."""

    def get(
        self,
        *,
        host: str,
        target: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> OpenAlexResponse: ...


class StdlibOpenAlexTransport:
    """Small real HTTPS transport restricted to the one OpenAlex API host."""

    def get(
        self,
        *,
        host: str,
        target: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> OpenAlexResponse:
        if host != OPENALEX_API_HOST:
            raise LiteratureFetchError(
                "OpenAlex transport rejected a non-canonical host",
                code=LiteratureFailureCode.OPENALEX_RESPONSE_INVALID,
            )
        if (
            not isinstance(target, str)
            or not target.startswith(("/works?", "/sources?"))
            or "#" in target
            or "\r" in target
            or "\n" in target
        ):
            raise LiteratureFetchError(
                "OpenAlex transport rejected an invalid request target",
                code=LiteratureFailureCode.OPENALEX_RESPONSE_INVALID,
            )
        connection = http.client.HTTPSConnection(
            OPENALEX_API_HOST,
            443,
            timeout=timeout_seconds,
        )
        try:
            connection.request("GET", target, headers=dict(headers))
            response = connection.getresponse()
            content_length = _http_content_length(
                response.getheader("Content-Length"),
                "OpenAlex",
                LiteratureFailureCode.OPENALEX_RESPONSE_INVALID,
            )
            if content_length is not None and content_length > max_bytes:
                raise LiteratureFetchError(
                    "OpenAlex response exceeded its byte bound",
                    code=LiteratureFailureCode.OPENALEX_RESPONSE_LIMIT,
                )
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise LiteratureFetchError(
                    "OpenAlex response exceeded its byte bound",
                    code=LiteratureFailureCode.OPENALEX_RESPONSE_LIMIT,
                )
            return OpenAlexResponse(
                status_code=response.status,
                media_type=response.getheader("Content-Type"),
                content_length=content_length,
                body=body,
            )
        except (TimeoutError, socket.timeout) as exc:
            raise LiteratureFetchError(
                "OpenAlex request timed out",
                code=LiteratureFailureCode.OPENALEX_TIMEOUT,
            ) from exc
        except LiteratureError:
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise LiteratureFetchError(
                "OpenAlex HTTPS transport failed",
                code=LiteratureFailureCode.OPENALEX_HTTP,
            ) from exc
        finally:
            connection.close()


class StdlibSemanticScholarTransport:
    """Bounded exact-host transport for DOI-only open-PDF metadata lookup."""

    def get(
        self,
        *,
        host: str,
        target: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> OpenAlexResponse:
        if host != SEMANTIC_SCHOLAR_API_HOST:
            raise LiteratureFetchError("Semantic Scholar host is not canonical")
        if (
            not isinstance(target, str)
            or not target.startswith("/graph/v1/paper/DOI%3A")
            or not target.endswith("?fields=externalIds%2CopenAccessPdf")
            or "#" in target
            or "\r" in target
            or "\n" in target
        ):
            raise LiteratureFetchError("Semantic Scholar target is invalid")
        return _pdf_locator_get("Semantic Scholar", host, target, headers, timeout_seconds, max_bytes)


def _pdf_locator_get(provider: str, host: str, target: str, headers: Mapping[str, str],
                     timeout_seconds: float, max_bytes: int) -> OpenAlexResponse:
    """Shared bounded HTTP read after a locator's exact-host/target check."""
    connection = http.client.HTTPSConnection(host, 443, timeout=timeout_seconds)
    try:
        connection.request("GET", target, headers=dict(headers))
        response = connection.getresponse()
        content_length = _http_content_length(
            response.getheader("Content-Length"), provider, LiteratureFailureCode.FETCH_FAILED,
        )
        if content_length is not None and content_length > max_bytes:
            raise LiteratureFetchError(f"{provider} response exceeded its byte bound")
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise LiteratureFetchError(f"{provider} response exceeded its byte bound")
        return OpenAlexResponse(status_code=response.status,
            media_type=response.getheader("Content-Type"), content_length=content_length, body=body)
    except (TimeoutError, socket.timeout) as exc:
        raise LiteratureFetchError(
            f"{provider} request timed out", code=LiteratureFailureCode.PDF_TIMEOUT,
        ) from exc
    except LiteratureError:
        raise
    except (OSError, http.client.HTTPException) as exc:
        raise LiteratureFetchError(
            f"{provider} HTTPS transport failed", code=LiteratureFailureCode.PDF_TRANSPORT,
        ) from exc
    finally:
        connection.close()


class PdfFallbackLocator(Protocol):
    def locate(self, paper: SearchPaper) -> Sequence[str]: ...


class SemanticScholarPdfLocator:
    """Find one legal OA copy by an exact DOI, never by fuzzy title search."""

    def __init__(
        self,
        *,
        transport: OpenAlexTransport | None = None,
        timeout_seconds: float = DEFAULT_OPENALEX_TIMEOUT_SECONDS,
        max_response_bytes: int = 64 * 1024,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0.1 <= float(timeout_seconds) <= 60.0
        ):
            raise ValueError("timeout_seconds must be within 0.1..60")
        if (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or not 1_024 <= max_response_bytes <= 1024 * 1024
        ):
            raise ValueError("max_response_bytes must be within 1024..1048576")
        self._transport = (
            StdlibSemanticScholarTransport() if transport is None else transport
        )
        self.timeout_seconds = float(timeout_seconds)
        self.max_response_bytes = max_response_bytes

    @property
    def configuration(self) -> dict[str, object]:
        return {
            "adapter": "semantic-scholar-doi",
            "host": SEMANTIC_SCHOLAR_API_HOST,
            "timeout_seconds": self.timeout_seconds,
            "max_response_bytes": self.max_response_bytes,
        }

    def preflight(self) -> dict[str, object]:
        return {**self.configuration, "credential_source": "none", "network_probe_performed": False}

    def locate(self, paper: SearchPaper) -> Sequence[str]:
        prefix = "doi:"
        if not paper.bibliographic_identity.startswith(prefix):
            return ()
        doi = paper.bibliographic_identity[len(prefix) :]
        target = (
            "/graph/v1/paper/"
            + quote(f"DOI:{doi}", safe="")
            + "?fields=externalIds%2CopenAccessPdf"
        )
        try:
            response = self._transport.get(
                host=SEMANTIC_SCHOLAR_API_HOST,
                target=target,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Hypoloom-Hypoloom-literature/2",
                },
                timeout_seconds=self.timeout_seconds,
                max_bytes=self.max_response_bytes,
            )
        except LiteratureError:
            raise
        except TimeoutError as exc:
            raise LiteratureFetchError(
                "Semantic Scholar metadata lookup timed out", code=LiteratureFailureCode.PDF_TIMEOUT,
            ) from exc
        except Exception as exc:
            raise LiteratureFetchError(
                "Semantic Scholar metadata lookup failed", code=LiteratureFailureCode.PDF_TRANSPORT,
            ) from exc
        if not isinstance(response, OpenAlexResponse):
            raise LiteratureFetchError("Semantic Scholar response is invalid")
        if response.status_code == 404:
            return ()
        if response.status_code != 200:
            raise LiteratureFetchError(
                f"Semantic Scholar returned HTTP {response.status_code}",
                http_status=response.status_code,
            )
        if response.content_length is not None and response.content_length != len(
            response.body
        ):
            raise LiteratureFetchError(
                "Semantic Scholar Content-Length did not match its body"
            )
        media_type = "" if response.media_type is None else response.media_type.split(";", 1)[0].strip().casefold()
        if media_type != "application/json" or len(response.body) > self.max_response_bytes:
            raise LiteratureFetchError("Semantic Scholar response is invalid")
        try:
            payload = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiteratureFetchError("Semantic Scholar response is not JSON") from exc
        external = payload.get("externalIds") if isinstance(payload, Mapping) else None
        returned_doi = external.get("DOI") if isinstance(external, Mapping) else None
        if not isinstance(returned_doi, str) or returned_doi.casefold() != doi.casefold():
            return ()
        oa = payload.get("openAccessPdf") if isinstance(payload, Mapping) else None
        candidate = oa.get("url") if isinstance(oa, Mapping) else None
        if not isinstance(candidate, str):
            return ()
        try:
            uri, _hostname = _uri_parts(candidate, "Semantic Scholar openAccessPdf")
        except LiteratureError:
            return ()
        return (uri,)


UNPAYWALL_API_HOST = "api.unpaywall.org"


class StdlibUnpaywallTransport:
    """Contact email is sent only to the fixed DOI endpoint, with no redirects."""

    def get(self, *, host: str, target: str, headers: Mapping[str, str],
            timeout_seconds: float, max_bytes: int) -> OpenAlexResponse:
        if host != UNPAYWALL_API_HOST:
            raise LiteratureFetchError("Unpaywall host is not canonical")
        if not isinstance(target, str) or not target.startswith("/v2/10.") or any(c in target for c in "#\r\n"):
            raise LiteratureFetchError("Unpaywall target is invalid")
        parsed = urlsplit(target)
        query = parse_qs(parsed.query)
        try:
            _identity("doi:" + unquote(parsed.path[4:]))
            if set(query) != {"email"} or len(query["email"]) != 1:
                raise ValueError("email required")
            _unpaywall_email(query["email"][0], None)
        except ValueError as exc:
            raise LiteratureFetchError("Unpaywall target is invalid") from exc
        return _pdf_locator_get("Unpaywall", host, target, headers, timeout_seconds, max_bytes)


def _unpaywall_email(email: str | None, email_file: str | Path | None) -> str:
    if (email is None) == (email_file is None):
        raise ValueError("supply exactly one Unpaywall email or email_file")
    if email_file is not None:
        try:
            path = Path(email_file)
            metadata = path.lstat()
            if not path.is_absolute() or not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 1024:
                raise ValueError("invalid contact file")
            with path.open("rb") as stream:
                email = stream.read(1025).decode("utf-8").rstrip("\r\n")
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError("Unpaywall email file must be an existing absolute bounded regular UTF-8 file") from exc
    if not isinstance(email, str) or len(email) > 254 or re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+", email) is None:
        raise ValueError("Unpaywall email must be one bounded plain contact address")
    return email


class UnpaywallPdfLocator:
    """Resolve an exact DOI to at most four OA PDF URLs; no title search."""

    def __init__(self, *, email: str | None = None, email_file: str | Path | None = None,
                 transport: OpenAlexTransport | None = None,
                 timeout_seconds: float = 5.0, max_response_bytes: int = 64 * 1024) -> None:
        self._email = _unpaywall_email(email, email_file)
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0.1 <= timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be within 0.1..60")
        if type(max_response_bytes) is not int or not 1024 <= max_response_bytes <= 1024 * 1024:
            raise ValueError("max_response_bytes must be within 1024..1048576")
        self._transport = StdlibUnpaywallTransport() if transport is None else transport
        self.timeout_seconds = float(timeout_seconds)
        self.max_response_bytes = max_response_bytes
        self._email_source = "file" if email_file is not None else "explicit"

    @property
    def configuration(self) -> dict[str, object]:
        return {"adapter": "unpaywall-doi", "host": UNPAYWALL_API_HOST,
                "timeout_seconds": self.timeout_seconds, "max_response_bytes": self.max_response_bytes}

    def preflight(self) -> dict[str, object]:
        return {**self.configuration, "contact_source": self._email_source,
                "credential_source": "none", "network_probe_performed": False}

    def locate(self, paper: SearchPaper) -> Sequence[str]:
        if not paper.bibliographic_identity.startswith("doi:"):
            return ()
        doi = paper.bibliographic_identity[4:]
        target = "/v2/" + quote(doi, safe="/") + "?" + urlencode({"email": self._email})
        try:
            response = self._transport.get(host=UNPAYWALL_API_HOST, target=target,
                headers={"Accept": "application/json", "User-Agent": "Hypoloom-Literature/2"},
                timeout_seconds=self.timeout_seconds, max_bytes=self.max_response_bytes)
        except LiteratureError:
            raise
        except TimeoutError as exc:
            raise LiteratureFetchError("Unpaywall metadata lookup timed out", code=LiteratureFailureCode.PDF_TIMEOUT) from exc
        except Exception as exc:
            raise LiteratureFetchError("Unpaywall metadata lookup failed", code=LiteratureFailureCode.PDF_TRANSPORT) from exc
        if not isinstance(response, OpenAlexResponse):
            raise LiteratureFetchError("Unpaywall response is invalid")
        if response.status_code == 404:
            return ()
        if response.status_code != 200:
            raise LiteratureFetchError(f"Unpaywall returned HTTP {response.status_code}", http_status=response.status_code)
        if (response.content_length is not None and response.content_length != len(response.body)
                or len(response.body) > self.max_response_bytes
                or (response.media_type or "").split(";", 1)[0].strip().casefold() != "application/json"):
            raise LiteratureFetchError("Unpaywall response is invalid")
        try:
            payload = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiteratureFetchError("Unpaywall response is not JSON") from exc
        returned = payload.get("doi") if isinstance(payload, Mapping) else None
        try:
            if not isinstance(returned, str) or _identity("doi:" + returned) != paper.bibliographic_identity:
                return ()
        except LiteratureError:
            return ()
        if payload.get("is_oa") is not True:
            return ()
        locations = payload.get("oa_locations")
        locations = [payload.get("best_oa_location"), *(locations if isinstance(locations, list) else [])]
        result = []
        for location in locations:
            candidate = location.get("url_for_pdf") if isinstance(location, Mapping) else None
            if not isinstance(candidate, str):
                continue
            try:
                uri, _ = _uri_parts(candidate, "Unpaywall OA PDF")
            except LiteratureError:
                continue
            if uri not in result:
                result.append(uri)
            if len(result) == 4:
                break
        return tuple(result)


class PopplerPdfExtractor:
    """Bounded ``pdfinfo``/``pdftotext`` adapter over exact PDF bytes."""

    def __init__(
        self,
        *,
        pdfinfo_path: str | Path = "/usr/bin/pdfinfo",
        pdftotext_path: str | Path = "/usr/bin/pdftotext",
        runner: Callable[..., Any] = subprocess.run,
        timeout_seconds: float = DEFAULT_POPPLER_TIMEOUT_SECONDS,
        max_pdf_bytes: int = DEFAULT_MAX_PDF_BYTES,
        max_text_bytes: int = DEFAULT_POPPLER_TEXT_BYTES,
        max_summary_chars: int = 4_096,
    ) -> None:
        self.pdfinfo_path = _absolute_tool_path(pdfinfo_path, "pdfinfo_path")
        self.pdftotext_path = _absolute_tool_path(pdftotext_path, "pdftotext_path")
        if not callable(runner):
            raise TypeError("runner must be callable")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0.1 <= float(timeout_seconds) <= 120.0
        ):
            raise ValueError("timeout_seconds must be within 0.1..120")
        for name, value, upper in (
            ("max_pdf_bytes", max_pdf_bytes, 64 * 1024 * 1024),
            ("max_text_bytes", max_text_bytes, 16 * 1024 * 1024),
            ("max_summary_chars", max_summary_chars, MAX_SUMMARY_CHARS),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= upper
            ):
                raise ValueError(f"{name} must be a positive bounded integer")
        self._runner = runner
        self.timeout_seconds = float(timeout_seconds)
        self.max_pdf_bytes = max_pdf_bytes
        self.max_text_bytes = max_text_bytes
        self.max_summary_chars = max_summary_chars

    @property
    def configuration(self) -> dict[str, object]:
        return {
            "timeout_seconds": self.timeout_seconds,
            "max_pdf_bytes": self.max_pdf_bytes,
            "max_text_bytes": self.max_text_bytes,
            "max_summary_chars": self.max_summary_chars,
        }

    def preflight(self) -> dict[str, object]:
        for name, value in (
            ("pdfinfo", self.pdfinfo_path),
            ("pdftotext", self.pdftotext_path),
        ):
            path = Path(value)
            try:
                metadata = path.lstat()
            except OSError as exc:
                raise LiteratureValidationError(
                    f"Poppler {name} executable is unavailable",
                    code=LiteratureFailureCode.POPPLER_FAILED,
                ) from exc
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or not os.access(path, os.X_OK)
            ):
                raise LiteratureValidationError(
                    f"Poppler {name} must be an executable regular file",
                    code=LiteratureFailureCode.POPPLER_FAILED,
                )
        return {
            "pdfinfo": self.pdfinfo_path,
            "pdftotext": self.pdftotext_path,
            "timeout_seconds": self.timeout_seconds,
            "max_pdf_bytes": self.max_pdf_bytes,
            "max_text_bytes": self.max_text_bytes,
        }

    def extract_document(self, content: bytes, *, max_pages: int, timeout_seconds: float | None = None) -> PdfDocument:
        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
        if not isinstance(content, bytes) or not content.startswith(b"%PDF-"):
            raise LiteratureValidationError(
                "Poppler input is not a PDF",
                code=LiteratureFailureCode.POPPLER_INVALID,
            )
        if len(content) > self.max_pdf_bytes:
            raise LiteratureValidationError(
                "Poppler PDF input exceeded its byte bound",
                code=LiteratureFailureCode.POPPLER_OUTPUT_LIMIT,
            )
        if (
            isinstance(max_pages, bool)
            or not isinstance(max_pages, int)
            or not 1 <= max_pages <= 10_000
        ):
            raise LiteratureValidationError(
                "Poppler max_pages is invalid",
                code=LiteratureFailureCode.POPPLER_INVALID,
            )
        info = self._run(
            (self.pdfinfo_path, "-"),
            content,
            max_output_bytes=64 * 1024,
            deadline=deadline,
        )
        page_count = _poppler_page_count(info)
        if page_count > max_pages:
            raise LiteratureValidationError(
                "PDF page_count exceeds configured bound",
                code=LiteratureFailureCode.POPPLER_OUTPUT_LIMIT,
            )
        text_bytes = self._run(
            (
                self.pdftotext_path,
                "-f",
                "1",
                "-l",
                str(page_count),
                "-enc",
                "UTF-8",
                "-",
                "-",
            ),
            content,
            max_output_bytes=self.max_text_bytes,
            deadline=deadline,
        )
        try:
            text = text_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LiteratureValidationError(
                "Poppler text output is not UTF-8",
                code=LiteratureFailureCode.POPPLER_INVALID,
            ) from exc
        pages = tuple(page.replace("\x00", " ") for page in text.split("\f"))
        if pages and not pages[-1].strip():
            pages = pages[:-1]
        if len(pages) > page_count:
            raise LiteratureValidationError(
                "Poppler text contained more pages than pdfinfo",
                code=LiteratureFailureCode.POPPLER_INVALID,
            )
        return PdfDocument(page_count=page_count, pages=pages + ("",) * (page_count - len(pages)))

    def extract_pdf(self, content: bytes, *, max_pages: int) -> PdfExtraction:
        document = self.extract_document(content, max_pages=max_pages)
        pages, page_count = document.pages, document.page_count
        abstract, abstract_page = _extract_named_pdf_section(
            pages,
            section="abstract",
            maximum=self.max_summary_chars,
        )
        conclusions, conclusion_page = _extract_named_pdf_section(
            pages,
            section="conclusions",
            maximum=self.max_summary_chars,
        )
        return PdfExtraction(
            page_count=page_count,
            pages=pages,
            abstract_summary=abstract,
            conclusions=conclusions,
            claim_support=(
                ClaimPageRef(
                    claim_ref="abstract-excerpt",
                    section="Abstract",
                    page_start=abstract_page,
                    page_end=abstract_page,
                ),
                ClaimPageRef(
                    claim_ref="conclusions-excerpt",
                    section="Conclusions",
                    page_start=conclusion_page,
                    page_end=conclusion_page,
                ),
            ),
        )

    def _run(
        self,
        command: tuple[str, ...],
        content: bytes,
        *,
        max_output_bytes: int,
        deadline: float | None = None,
    ) -> bytes:
        timeout = self.timeout_seconds if deadline is None else min(self.timeout_seconds, deadline - time.monotonic())
        if timeout <= 0:
            raise LiteratureValidationError("Poppler document work window expired", code=LiteratureFailureCode.POPPLER_TIMEOUT)
        try:
            completed = self._runner(
                command,
                input=content,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise LiteratureValidationError(
                "Poppler command timed out",
                code=LiteratureFailureCode.POPPLER_TIMEOUT,
            ) from exc
        except OSError as exc:
            raise LiteratureValidationError(
                "Poppler command could not be executed",
                code=LiteratureFailureCode.POPPLER_FAILED,
            ) from exc
        return_code = getattr(completed, "returncode", None)
        output = getattr(completed, "stdout", None)
        error = getattr(completed, "stderr", None)
        if (
            isinstance(return_code, bool)
            or not isinstance(return_code, int)
            or not isinstance(output, bytes)
            or not isinstance(error, bytes)
        ):
            raise LiteratureValidationError(
                "Poppler runner returned an invalid result",
                code=LiteratureFailureCode.POPPLER_INVALID,
            )
        if len(output) > max_output_bytes or len(error) > 64 * 1024:
            raise LiteratureValidationError(
                "Poppler output exceeded its byte bound",
                code=LiteratureFailureCode.POPPLER_OUTPUT_LIMIT,
            )
        if return_code != 0:
            raise LiteratureValidationError(
                "Poppler command failed",
                code=LiteratureFailureCode.POPPLER_FAILED,
            )
        return output


class OpenAlexLiteratureFetcher:
    """Real OpenAlex search plus injected/stdlib PDF and Poppler boundaries.

    Authentication is explicit: the caller supplies exactly one API-key value
    or regular-file path.  The key is used only in a Bearer header and is never
    placed in a URI, representation, result, or diagnostic.
    """

    _SELECT_FIELDS = (
        "id,doi,display_name,publication_year,language,authorships,type,"
        "primary_location,best_oa_location,locations,open_access"
    )
    _SOURCE_SELECT_FIELDS = "id,display_name,type,issn_l,alternate_titles,abbreviated_title"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_key_file: str | Path | None = None,
        transport: OpenAlexTransport | None = None,
        resolver: Callable[[str], Sequence[str]] | None = None,
        pdf_fetcher: Callable[..., FetchResponse] | None = None,
        pdf_extractor: PopplerPdfExtractor | None = None,
        timeout_seconds: float = DEFAULT_OPENALEX_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_OPENALEX_RESPONSE_BYTES,
        max_search_seconds: float = DEFAULT_OPENALEX_SEARCH_SECONDS,
        max_retries: int = DEFAULT_OPENALEX_MAX_RETRIES,
        retry_backoff_seconds: float = DEFAULT_OPENALEX_RETRY_BACKOFF_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key_source = "parameter" if api_key is not None else "file"
        self._api_key = _explicit_api_key(api_key, api_key_file)
        if transport is not None and not callable(getattr(transport, "get", None)):
            raise TypeError("transport must implement get")
        if resolver is not None and not callable(resolver):
            raise TypeError("resolver must be callable")
        if pdf_fetcher is not None and not callable(pdf_fetcher):
            raise TypeError("pdf_fetcher must be callable")
        if pdf_extractor is not None and not callable(
            getattr(pdf_extractor, "extract_pdf", None)
        ):
            raise TypeError("pdf_extractor must implement extract_pdf")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0.1 <= float(timeout_seconds) <= 60.0
        ):
            raise ValueError("timeout_seconds must be within 0.1..60")
        if (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or not 1_024 <= max_response_bytes <= 16 * 1024 * 1024
        ):
            raise ValueError("max_response_bytes must be within 1024..16777216")
        if (
            isinstance(max_search_seconds, bool)
            or not isinstance(max_search_seconds, (int, float))
            or not 1.0 <= float(max_search_seconds) <= 600.0
        ):
            raise ValueError("max_search_seconds must be within 1..600")
        if (
            isinstance(max_retries, bool)
            or not isinstance(max_retries, int)
            or not 0 <= max_retries <= MAX_OPENALEX_RETRIES
        ):
            raise ValueError(
                f"max_retries must be within 0..{MAX_OPENALEX_RETRIES}"
            )
        if (
            isinstance(retry_backoff_seconds, bool)
            or not isinstance(retry_backoff_seconds, (int, float))
            or not 0 <= float(retry_backoff_seconds)
            <= MAX_OPENALEX_RETRY_BACKOFF_SECONDS
        ):
            raise ValueError(
                "retry_backoff_seconds must be within "
                f"0..{MAX_OPENALEX_RETRY_BACKOFF_SECONDS:g}"
            )
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not callable(sleeper):
            raise TypeError("sleeper must be callable")
        self._transport = StdlibOpenAlexTransport() if transport is None else transport
        self._resolver = self._resolve_host if resolver is None else resolver
        self._pdf_fetcher = self._fetch_pdf_https if pdf_fetcher is None else pdf_fetcher
        self._pdf_extractor = (
            PopplerPdfExtractor(timeout_seconds=timeout_seconds)
            if pdf_extractor is None
            else pdf_extractor
        )
        self.timeout_seconds = float(timeout_seconds)
        self.max_response_bytes = max_response_bytes
        self.max_search_seconds = float(max_search_seconds)
        self.max_retries = max_retries
        self.retry_backoff_seconds = float(retry_backoff_seconds)
        self._clock = clock
        self._sleeper = sleeper
        self._source_cache: dict[tuple[str, str], tuple[LiteratureVenue, ...]] = {}
        self._last_retry_trace: tuple[dict[str, object], ...] = ()
        self._cancel_event: Event | None = None

    @property
    def last_retry_trace(self) -> tuple[dict[str, object], ...]:
        """Return safe retry events from the most recent synchronous search."""

        return tuple(dict(item) for item in self._last_retry_trace)

    def web_search(self, query: str, *, max_results: int, timeout_seconds: float) -> dict[str, Any]:
        """One academic search, without altering literature-batch traces/caches.

        Reuse the exact-host authenticated transport and response validator.
        No retries or PDF downloads; this is metadata, not retrieved full text.
        """
        if not isinstance(query, str) or not query.strip() or len(query) > 2000 or "\x00" in query:
            raise LiteratureQueryError("search query must contain 1..2000 characters")
        if type(max_results) is not int or not 1 <= max_results <= 10:
            raise LiteratureQueryError("max_results must be within 1..10")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 30:
            raise LiteratureQueryError("search timeout must be within 0..30 seconds")
        target = "/works?" + urlencode({"search": query, "per-page": max_results,
            "select": "id,display_name,publication_year,doi,primary_location,best_oa_location"})
        response = self._transport.get(host=OPENALEX_API_HOST, target=target,
            headers={"Accept": "application/json", "Authorization": f"Bearer {self._api_key}",
                     "User-Agent": "Hypoloom-Grounding/1"},
            timeout_seconds=min(self.timeout_seconds, timeout_seconds), max_bytes=self.max_response_bytes)
        works = self._response_works(response, max_results)
        results = []
        for work in works:
            title = work.get("display_name")
            if not isinstance(title, str) or not title.strip():
                raise LiteratureValidationError("OpenAlex search result lacks a title")
            locations = [work.get("best_oa_location"), work.get("primary_location")]
            links = [location.get("landing_page_url") for location in locations if isinstance(location, Mapping)]
            links.extend([work.get("doi"), work.get("id")])
            url = None
            for link in links:
                if not isinstance(link, str): continue
                try: url, _ = _uri_parts(link, "OpenAlex result URL")
                except LiteratureError: continue
                break
            if url is None: raise LiteratureValidationError("OpenAlex search result lacks a public HTTPS link")
            year = work.get("publication_year")
            snippet = f"OpenAlex bibliographic metadata; publication year: {year if type(year) is int else 'unknown'}. Full text has not been fetched."
            results.append({"url": url, "title": title[:512], "snippet": snippet,
                            "truncated": len(title) > 512})
        return {"provider": "openalex", "search_scope": "academic_literature",
                "query": query, "source_url": f"https://{OPENALEX_API_HOST}{target}",
                "results": results, "truncated": len(results) == max_results,
                "source_is_untrusted": True}

    @property
    def configuration(self) -> dict[str, object]:
        return {
            "adapter": "openalex",
            "host": OPENALEX_API_HOST,
            "timeout_seconds": self.timeout_seconds,
            "search_wall_seconds": self.max_search_seconds,
            "max_response_bytes": self.max_response_bytes,
            "max_retries": self.max_retries,
            "retry_backoff_seconds": self.retry_backoff_seconds,
            **{
                f"poppler_{name}": value
                for name, value in self._pdf_extractor.configuration.items()
            },
        }

    def preflight(self) -> dict[str, object]:
        return {
            "adapter": "openalex",
            "host": OPENALEX_API_HOST,
            "credential_source": self._api_key_source,
            "timeout_seconds": self.timeout_seconds,
            "search_wall_seconds": self.max_search_seconds,
            "max_response_bytes": self.max_response_bytes,
            "max_retries": self.max_retries,
            "retry_backoff_seconds": self.retry_backoff_seconds,
            "poppler": self._pdf_extractor.preflight(),
            "network_probe_performed": False,
        }

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(host={OPENALEX_API_HOST!r}, "
            f"timeout_seconds={self.timeout_seconds!r}, "
            f"max_response_bytes={self.max_response_bytes!r}, "
            f"max_retries={self.max_retries!r}, "
            f"retry_backoff_seconds={self.retry_backoff_seconds!r}, "
            "api_key=<redacted>)"
        )

    def search(self, query: LiteratureQuery) -> Iterable[SearchPaper]:
        if not isinstance(query, LiteratureQuery):
            raise LiteratureQueryError("query must be LiteratureQuery")
        self._last_retry_trace = ()
        ranges = _openalex_year_ranges(query)
        quotas = _stratum_quotas(query.max_items)
        deadline = self._clock() + self.max_search_seconds
        strata_plan = tuple(zip(LITERATURE_STRATA, ranges, quotas, strict=True))
        broad_by_stratum: list[tuple[SearchPaper, ...]] = []
        for stratum, years, quota in strata_plan:
            broad = self._request_results(
                self._request_target(query, stratum, years, quota),
                quota,
                deadline=deadline,
            )
            broad_by_stratum.append(self._normalize_works(broad, stratum, query))
        preferred_by_stratum: list[tuple[SearchPaper, ...]] = [
            () for _ in strata_plan
        ]
        preferred_source_ids = self._preferred_source_ids(
            query.preferred_venues, deadline=deadline
        )
        if preferred_source_ids:
            for index, (stratum, years, quota) in enumerate(strata_plan):
                try:
                    preferred = self._request_results(
                        self._request_target(
                            query,
                            stratum,
                            years,
                            quota,
                            source_ids=preferred_source_ids,
                        ),
                        quota,
                        deadline=deadline,
                    )
                    preferred_by_stratum[index] = self._normalize_works(
                        preferred,
                        stratum,
                        query,
                        allowed_source_ids=frozenset(preferred_source_ids),
                    )
                except LiteratureError:
                    break
        papers: list[SearchPaper] = []
        for (_stratum, _years, quota), broad_papers, preferred_papers in zip(
            strata_plan,
            broad_by_stratum,
            preferred_by_stratum,
            strict=True,
        ):
            preferred_limit = (quota + 1) // 2
            chosen = list(preferred_papers[:preferred_limit])
            seen = {item.bibliographic_identity for item in chosen}
            for paper in (*broad_papers, *preferred_papers[preferred_limit:]):
                if len(chosen) >= quota:
                    break
                if paper.bibliographic_identity not in seen:
                    chosen.append(paper)
                    seen.add(paper.bibliographic_identity)
                if len(chosen) >= quota:
                    break
            papers.extend(chosen)
        if len(papers) > query.max_items:
            raise LiteratureValidationError(
                "OpenAlex normalized result count exceeded query max_items",
                code=LiteratureFailureCode.OPENALEX_RESPONSE_LIMIT,
            )
        return tuple(papers)

    def _normalize_works(
        self,
        works: Sequence[Mapping[str, Any]],
        stratum: LiteratureStratum,
        query: LiteratureQuery,
        *,
        allowed_source_ids: frozenset[str] | None = None,
    ) -> tuple[SearchPaper, ...]:
        papers: list[SearchPaper] = []
        for work in works:
            try:
                paper = _openalex_work_to_paper(work, stratum, query)
            except LiteratureFetchError as exc:
                if exc.code is not LiteratureFailureCode.OPENALEX_RESPONSE_INVALID:
                    raise
                continue
            if paper is None:
                continue
            if allowed_source_ids is not None and (
                paper.venue is None
                or paper.venue.source_id.split(":", 1)[1] not in allowed_source_ids
            ):
                raise LiteratureFetchError(
                    "OpenAlex preferred result came from an unrequested Source",
                    code=LiteratureFailureCode.OPENALEX_RESPONSE_INVALID,
                )
            papers.append(paper)
        return tuple(papers)

    def _preferred_source_ids(
        self,
        preferences: tuple[PreferredVenue, ...],
        *,
        deadline: float,
    ) -> tuple[str, ...]:
        source_ids: list[str] = []
        for preference in preferences:
            key = preference.identity_key
            if key not in self._source_cache:
                try:
                    resolved = self._resolve_preferred_venue(
                        preference, deadline=deadline
                    )
                except LiteratureError:
                    continue
                self._source_cache[key] = resolved
            for venue in self._source_cache[key]:
                identifier = venue.source_id.split(":", 1)[1]
                if identifier not in source_ids:
                    source_ids.append(identifier)
        return tuple(source_ids[: 4 * MAX_PREFERRED_VENUES])

    def _resolve_preferred_venue(
        self,
        preference: PreferredVenue,
        *,
        deadline: float,
    ) -> tuple[LiteratureVenue, ...]:
        source_type = (
            "journal"
            if preference.source_class is ScholarlySourceClass.JOURNAL
            else "conference"
        )
        target = "/sources?" + urlencode(
            {
                "search": preference.name,
                "filter": f"type:{source_type}",
                "per-page": "4",
                "select": self._SOURCE_SELECT_FIELDS,
            }
        )
        sources = self._request_results(target, 4, deadline=deadline)
        resolved: list[LiteratureVenue] = []
        wanted = preference.identity_key[1]
        for source in sources:
            aliases = _openalex_source_aliases(source)
            if not any(_venue_alias_matches(wanted, alias) for alias in aliases):
                continue
            venue = _openalex_source_to_venue(source)
            if venue.source_class is preference.source_class:
                resolved.append(venue)
        return tuple(resolved)

    def resolve(self, hostname: str) -> Sequence[str]:
        try:
            return self._resolver(hostname)
        except LiteratureError:
            raise
        except OSError as exc:
            raise _LiteratureDnsResolutionError("PDF URI", hostname) from exc
        except Exception as exc:
            raise LiteratureFetchError("PDF DNS resolution failed") from exc

    def fetch(
        self,
        uri: str,
        *,
        approved_addresses: tuple[str, ...],
        max_bytes: int,
    ) -> FetchResponse:
        try:
            return self._pdf_fetcher(
                uri,
                approved_addresses=approved_addresses,
                max_bytes=max_bytes,
            )
        except LiteratureError:
            raise
        except (TimeoutError, socket.timeout) as exc:
            raise LiteratureFetchError(
                "PDF request timed out",
                code=LiteratureFailureCode.PDF_TIMEOUT,
            ) from exc
        except Exception as exc:
            raise LiteratureFetchError("PDF HTTPS transport failed", code=LiteratureFailureCode.PDF_TRANSPORT) from exc

    def extract_pdf(self, content: bytes, *, max_pages: int) -> PdfExtraction:
        return self._pdf_extractor.extract_pdf(content, max_pages=max_pages)

    def _request_results(
        self,
        target: str,
        quota: int,
        *,
        deadline: float,
    ) -> tuple[Mapping[str, Any], ...]:
        total_attempts = self.max_retries + 1
        last_failure: LiteratureFetchError | None = None
        for attempt_count in range(1, total_attempts + 1):
            if self._cancel_event is not None and self._cancel_event.is_set():
                raise LiteratureFetchError("OpenAlex read cancelled", code=LiteratureFailureCode.OPENALEX_TIMEOUT)
            remaining = deadline - self._clock()
            if remaining <= 0:
                if last_failure is not None:
                    raise _openalex_attempt_failure(
                        last_failure,
                        attempt_count=attempt_count - 1,
                    ) from last_failure
                raise LiteratureFetchError(
                    "OpenAlex search wall bound expired",
                    code=LiteratureFailureCode.OPENALEX_TIMEOUT,
                )
            try:
                response = self._transport.get(
                    host=OPENALEX_API_HOST,
                    target=target,
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {self._api_key}",
                        "User-Agent": "Hypoloom-Hypoloom-literature/2",
                    },
                    timeout_seconds=min(self.timeout_seconds, remaining),
                    max_bytes=self.max_response_bytes,
                )
                return self._response_works(response, quota)
            except (TimeoutError, socket.timeout):
                failure = LiteratureFetchError(
                    "OpenAlex request timed out",
                    code=LiteratureFailureCode.OPENALEX_TIMEOUT,
                )
            except LiteratureFetchError as exc:
                failure = exc
            except LiteratureError:
                raise
            except Exception:
                failure = LiteratureFetchError(
                    "OpenAlex transport failed",
                    code=LiteratureFailureCode.OPENALEX_HTTP,
                )
            last_failure = failure
            if (
                not _openalex_failure_is_retryable(failure)
                or attempt_count >= total_attempts
            ):
                raise _openalex_attempt_failure(
                    failure,
                    attempt_count=attempt_count,
                ) from failure
            delay = min(
                self.retry_backoff_seconds * (2 ** (attempt_count - 1)),
                MAX_OPENALEX_RETRY_BACKOFF_SECONDS,
            )
            if delay > 0:
                remaining = deadline - self._clock()
                if remaining <= delay:
                    raise _openalex_attempt_failure(
                        failure,
                        attempt_count=attempt_count,
                    ) from failure
            if len(self._last_retry_trace) < MAX_OPENALEX_RETRY_TRACE_EVENTS:
                endpoint = "sources" if target.startswith("/sources?") else "works"
                self._last_retry_trace = (*self._last_retry_trace, {
                    "retry_ordinal": len(self._last_retry_trace) + 1,
                    "endpoint": endpoint,
                    "request_attempt_ordinal": attempt_count,
                    "failure_code": failure.code.value,
                    "http_status": failure.http_status,
                    "retry_after_seconds": delay,
                })
            if delay > 0:
                if self._cancel_event is None: self._sleeper(delay)
                else: self._cancel_event.wait(delay)
        raise AssertionError("bounded OpenAlex retry loop exhausted unexpectedly")

    def _request_target(
        self,
        query: LiteratureQuery,
        stratum: LiteratureStratum,
        years: tuple[int, int],
        quota: int,
        *,
        source_ids: tuple[str, ...] = (),
    ) -> str:
        start_year, end_year = years
        filters = [
            f"from_publication_date:{start_year:04d}-01-01",
            (
                f"to_publication_date:{query.latest_as_of}"
                if stratum is LiteratureStratum.LATEST_AS_OF_RUN
                else f"to_publication_date:{end_year:04d}-12-31"
            ),
            f"type:{_openalex_type_filter(query.allowed_source_classes)}",
        ]
        if query.filters.languages:
            filters.append(f"language:{'|'.join(query.filters.languages)}")
        if source_ids:
            filters.append(
                f"primary_location.source.id:{'|'.join(source_ids)}"
            )
        sort = "relevance_score:desc"
        return "/works?" + urlencode(
            {
                "search": query.query,
                "filter": ",".join(filters),
                "sort": sort,
                "per-page": str(quota),
                "select": self._SELECT_FIELDS,
            }
        )

    def _response_works(
        self,
        response: object,
        quota: int,
    ) -> tuple[Mapping[str, Any], ...]:
        if not isinstance(response, OpenAlexResponse):
            raise LiteratureFetchError(
                "OpenAlex transport returned an invalid response",
                code=LiteratureFailureCode.OPENALEX_RESPONSE_INVALID,
            )
        if response.status_code == 400:
            raise LiteratureFetchError(
                "OpenAlex rejected the generated query",
                code=LiteratureFailureCode.QUERY_INVALID,
                http_status=response.status_code,
            )
        if response.status_code in {401, 403}:
            raise LiteratureFetchError(
                "OpenAlex authentication was rejected",
                code=LiteratureFailureCode.OPENALEX_AUTH,
                http_status=response.status_code,
            )
        if response.status_code == 429:
            raise LiteratureFetchError(
                "OpenAlex rate limit was reached",
                code=LiteratureFailureCode.OPENALEX_RATE_LIMIT,
                http_status=response.status_code,
            )
        if response.status_code != 200:
            raise LiteratureFetchError(
                f"OpenAlex returned HTTP {response.status_code}",
                code=LiteratureFailureCode.OPENALEX_HTTP,
                http_status=response.status_code,
            )
        if len(response.body) > self.max_response_bytes:
            raise LiteratureFetchError(
                "OpenAlex response exceeded its byte bound",
                code=LiteratureFailureCode.OPENALEX_RESPONSE_LIMIT,
            )
        if response.content_length is not None:
            if response.content_length > self.max_response_bytes:
                raise LiteratureFetchError(
                    "OpenAlex Content-Length exceeded its byte bound",
                    code=LiteratureFailureCode.OPENALEX_RESPONSE_LIMIT,
                )
            if response.content_length != len(response.body):
                raise LiteratureFetchError(
                    "OpenAlex Content-Length did not match its body",
                    code=LiteratureFailureCode.OPENALEX_RESPONSE_INVALID,
                )
        media_type = (
            ""
            if response.media_type is None
            else response.media_type.split(";", 1)[0].strip().casefold()
        )
        if media_type != "application/json":
            raise LiteratureFetchError(
                "OpenAlex response was not application/json",
                code=LiteratureFailureCode.OPENALEX_RESPONSE_INVALID,
            )
        try:
            payload = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiteratureFetchError(
                "OpenAlex response was not valid JSON",
                code=LiteratureFailureCode.OPENALEX_RESPONSE_INVALID,
            ) from exc
        if not isinstance(payload, Mapping) or not isinstance(
            payload.get("results"), list
        ):
            raise LiteratureFetchError(
                "OpenAlex response omitted its results array",
                code=LiteratureFailureCode.OPENALEX_RESPONSE_INVALID,
            )
        results = payload["results"]
        if len(results) > quota:
            raise LiteratureFetchError(
                "OpenAlex returned more works than requested",
                code=LiteratureFailureCode.OPENALEX_RESPONSE_LIMIT,
            )
        if not all(isinstance(item, Mapping) for item in results):
            raise LiteratureFetchError(
                "OpenAlex results contained a non-object",
                code=LiteratureFailureCode.OPENALEX_RESPONSE_INVALID,
            )
        return tuple(results)

    @staticmethod
    def _resolve_host(hostname: str) -> Sequence[str]:
        try:
            records = socket.getaddrinfo(
                hostname,
                443,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise _LiteratureDnsResolutionError("PDF URI", hostname) from exc
        return tuple(sorted({record[4][0] for record in records}))

    def _fetch_pdf_https(
        self,
        uri: str,
        *,
        approved_addresses: tuple[str, ...],
        max_bytes: int,
    ) -> FetchResponse:
        canonical, hostname = _uri_parts(uri, "PDF URI")
        parsed = urlsplit(canonical)
        target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        connection = http.client.HTTPSConnection(
            hostname,
            443,
            timeout=self.timeout_seconds,
        )
        try:
            connection.connect()
            if connection.sock is None:
                raise LiteratureFetchError("PDF HTTPS connection has no socket")
            peer_address = str(connection.sock.getpeername()[0])
            if peer_address not in approved_addresses:
                raise LiteratureFetchError(
                    "PDF HTTPS peer was not pre-approved by DNS"
                )
            connection.request(
                "GET",
                target,
                headers={
                    "Accept": "application/pdf",
                    "User-Agent": "Hypoloom-Hypoloom-literature/1",
                },
            )
            response = connection.getresponse()
            content_length = _http_content_length(
                response.getheader("Content-Length"),
                "PDF",
                LiteratureFailureCode.FETCH_FAILED,
            )
            if content_length is not None and content_length > max_bytes:
                raise LiteratureFetchError(
                    "PDF response exceeded its byte bound",
                    code=LiteratureFailureCode.PDF_RESPONSE_LIMIT,
                )
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise LiteratureFetchError(
                    "PDF response exceeded its byte bound",
                    code=LiteratureFailureCode.PDF_RESPONSE_LIMIT,
                )
            return FetchResponse(
                requested_uri=canonical,
                status_code=response.status,
                peer_address=peer_address,
                media_type=response.getheader("Content-Type"),
                content_length=content_length,
                location=response.getheader("Location"),
                body=body,
            )
        except (TimeoutError, socket.timeout) as exc:
            raise LiteratureFetchError(
                "PDF request timed out",
                code=LiteratureFailureCode.PDF_TIMEOUT,
            ) from exc
        except LiteratureError:
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise LiteratureFetchError("PDF HTTPS transport failed") from exc
        finally:
            connection.close()


def _http_content_length(
    value: str | None,
    boundary: str,
    code: LiteratureFailureCode,
) -> int | None:
    if value is None:
        return None
    if not value.isascii() or not value.isdigit():
        raise LiteratureFetchError(
            f"{boundary} Content-Length is invalid",
            code=code,
        )
    return int(value)


def _absolute_tool_path(value: str | Path, name: str) -> str:
    try:
        path = Path(value)
    except TypeError as exc:
        raise ValueError(f"{name} must be an absolute path") from exc
    if not path.is_absolute() or not path.name:
        raise ValueError(f"{name} must be an absolute path")
    return str(path)


def _poppler_page_count(value: bytes) -> int:
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LiteratureValidationError(
            "pdfinfo output is not UTF-8",
            code=LiteratureFailureCode.POPPLER_INVALID,
        ) from exc
    matches = re.findall(r"(?m)^Pages:\s*([0-9]+)\s*$", text)
    if len(matches) != 1:
        raise LiteratureValidationError(
            "pdfinfo output omitted one canonical Pages field",
            code=LiteratureFailureCode.POPPLER_INVALID,
        )
    pages = int(matches[0])
    if not 1 <= pages <= 10_000:
        raise LiteratureValidationError(
            "pdfinfo page count is invalid",
            code=LiteratureFailureCode.POPPLER_INVALID,
        )
    return pages


_PDF_SECTION_PREFIX = (
    r"(?:(?:[0-9]+(?:\.[0-9]+)*|(?-i:[IVXLCDM]+))\.?\s+)?"
)
_PDF_CONCLUSION_WORD = (
    r"c[ \t]*o[ \t]*n[ \t]*c[ \t]*l[ \t]*u[ \t]*s[ \t]*i[ \t]*o[ \t]*n[ \t]*s?"
)
_PDF_REFERENCES_WORD = (
    r"r[ \t]*e[ \t]*f[ \t]*e[ \t]*r[ \t]*e[ \t]*n[ \t]*c[ \t]*e[ \t]*s?"
)
_PDF_SECTION_PATTERNS = {
    # Older IEEE PDFs can decode the em dash as an eth (AbstractÐText).
    "abstract": re.compile(r"(?im)^\s*abstract(?:\u00d0|\b\s*[:.\-\u2014]?)\s*"),
    "conclusions": re.compile(
        rf"(?im)^\s*{_PDF_SECTION_PREFIX}"
        rf"(?:(?:discussions?|summary)\s+and\s+)?{_PDF_CONCLUSION_WORD}\b"
        r"\s*[:.\-\u2014]?\s*"
    ),
}
_PDF_NEXT_HEADING = re.compile(
    rf"(?im)^\s*{_PDF_SECTION_PREFIX}(?:keywords?|index[ \t]+terms|introduction|"
    r"background|related\s+work|methods?|methodology|experiments?|results?|"
    rf"discussion|{_PDF_CONCLUSION_WORD}|acknowledg(?:e)?ments?|"
    rf"{_PDF_REFERENCES_WORD}|appendix)(?:\b|(?=\u00d0))"
)


def _extract_named_pdf_section(
    pages: tuple[str, ...],
    *,
    section: str,
    maximum: int,
) -> tuple[str, int]:
    pattern = _PDF_SECTION_PATTERNS[section]
    for page_index, page in enumerate(pages):
        match = pattern.search(page)
        if match is None:
            continue
        tail = page[match.end() :]
        next_heading = _PDF_NEXT_HEADING.search(tail)
        if next_heading is not None:
            tail = tail[: next_heading.start()]
        if len(re.sub(r"\s+", " ", tail).strip()) < 32 and page_index + 1 < len(
            pages
        ):
            following = pages[page_index + 1]
            following_heading = _PDF_NEXT_HEADING.search(following)
            tail += "\n" + (
                following
                if following_heading is None
                else following[: following_heading.start()]
            )
        # Poppler can preserve stray C0/DEL glyph controls from otherwise valid
        # PDF text.  They have no scholarly meaning, so normalize them at the
        # extractor boundary before the strict packet text validator runs.
        excerpt = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]+", " ", tail)).strip()
        if len(excerpt) > maximum:
            excerpt = excerpt[:maximum].rstrip()
        if excerpt:
            try:
                return _text(
                    excerpt,
                    f"PDF {section} excerpt",
                    maximum=maximum,
                ), page_index + 1
            except LiteratureValidationError as exc:
                raise LiteratureValidationError(
                    f"PDF {section} excerpt is invalid",
                    code=LiteratureFailureCode.POPPLER_INVALID,
                ) from exc
    raise LiteratureValidationError(
        f"PDF omitted a bounded {section} section",
        code=LiteratureFailureCode.POPPLER_INVALID,
    )


def _explicit_api_key(
    api_key: str | None,
    api_key_file: str | Path | None,
) -> str:
    if (api_key is None) == (api_key_file is None):
        raise ValueError("supply exactly one of api_key or api_key_file")
    if api_key is not None:
        return _clean_api_key(api_key, allow_file_newline=False)
    try:
        path = Path(api_key_file)  # type: ignore[arg-type]
        metadata = path.lstat()
        if (not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600):
            raise OSError("not a regular mode-0600 file")
        if metadata.st_size > 4_098:
            raise OSError("file too large")
        with path.open("rb") as stream:
            raw = stream.read(4_099)
    except (OSError, TypeError, ValueError) as exc:
        raise LiteratureFetchError(
            "OpenAlex API key file could not be read",
            code=LiteratureFailureCode.OPENALEX_AUTH,
        ) from exc
    if len(raw) > 4_098:
        raise LiteratureFetchError(
            "OpenAlex API key file exceeded its byte bound",
            code=LiteratureFailureCode.OPENALEX_AUTH,
        )
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LiteratureFetchError(
            "OpenAlex API key file was not UTF-8",
            code=LiteratureFailureCode.OPENALEX_AUTH,
        ) from exc
    return _clean_api_key(decoded, allow_file_newline=True)


def _openalex_failure_is_retryable(failure: LiteratureFetchError) -> bool:
    """Classify only transient transport/status failures for bounded retry."""

    status = failure.http_status
    if status is not None:
        return status in {408, 429} or 500 <= status <= 599
    return failure.code in {
        LiteratureFailureCode.OPENALEX_HTTP,
        LiteratureFailureCode.OPENALEX_TIMEOUT,
    }


def literature_failure_retryable(failure: LiteratureError) -> bool:
    """Classify a failure for a new outer Host Attempt.

    OpenAlex already consumes its small same-request transport budget. The
    outer retry remains useful for transient service, DNS, PDF, or Poppler
    failures, while deterministic auth/query/shape/size failures fail fast.
    """

    if not isinstance(failure, LiteratureError):
        raise TypeError("failure must be a LiteratureError")
    if failure.code in {
        LiteratureFailureCode.OPENALEX_TIMEOUT,
        LiteratureFailureCode.OPENALEX_RATE_LIMIT,
        LiteratureFailureCode.PDF_TIMEOUT,
        LiteratureFailureCode.POPPLER_TIMEOUT,
        LiteratureFailureCode.FETCH_FAILED,
    }:
        return True
    if failure.code is LiteratureFailureCode.OPENALEX_HTTP:
        return (
            failure.http_status is None
            or failure.http_status in {408, 429}
            or 500 <= failure.http_status <= 599
        )
    return False


def _openalex_attempt_failure(
    failure: LiteratureFetchError,
    *,
    attempt_count: int,
) -> LiteratureFetchError:
    """Return bounded, credential-free terminal evidence for one API request."""

    return LiteratureFetchError(
        f"OpenAlex request failed after {attempt_count} attempt"
        f"{'s' if attempt_count != 1 else ''}",
        code=failure.code,
        http_status=failure.http_status,
        attempt_count=attempt_count,
    )


def _clean_api_key(value: object, *, allow_file_newline: bool) -> str:
    if not isinstance(value, str):
        raise LiteratureFetchError(
            "OpenAlex API key is invalid",
            code=LiteratureFailureCode.OPENALEX_AUTH,
        )
    candidate = value.rstrip("\r\n") if allow_file_newline else value
    if (
        not candidate
        or candidate != candidate.strip()
        or len(candidate) > 4_096
        or any(character.isspace() or ord(character) < 33 for character in candidate)
    ):
        raise LiteratureFetchError(
            "OpenAlex API key is invalid",
            code=LiteratureFailureCode.OPENALEX_AUTH,
        )
    if allow_file_newline and value not in {candidate, candidate + "\n", candidate + "\r\n"}:
        raise LiteratureFetchError(
            "OpenAlex API key file must contain one line",
            code=LiteratureFailureCode.OPENALEX_AUTH,
        )
    return candidate


def _stratum_quotas(max_items: int) -> tuple[int, int, int]:
    base, remainder = divmod(max_items, len(LITERATURE_STRATA))
    quotas = [base] * len(LITERATURE_STRATA)
    for index in range(len(LITERATURE_STRATA) - remainder, len(LITERATURE_STRATA)):
        quotas[index] += 1
    return tuple(quotas)  # type: ignore[return-value]


def _openalex_year_ranges(
    query: LiteratureQuery,
) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    latest_year = date.fromisoformat(query.latest_as_of).year
    lower = query.filters.year_from
    if lower is None:
        lower = max(1000, latest_year - 99)
    upper = min(query.filters.year_to or latest_year, latest_year)
    span = upper - lower + 1
    if span < len(LITERATURE_STRATA):
        raise LiteratureQueryError(
            "OpenAlex stratified retrieval requires at least three publication years"
        )
    base, remainder = divmod(span, len(LITERATURE_STRATA))
    sizes = [base] * len(LITERATURE_STRATA)
    for index in range(len(LITERATURE_STRATA) - remainder, len(LITERATURE_STRATA)):
        sizes[index] += 1
    ranges: list[tuple[int, int]] = []
    start = lower
    for size in sizes:
        end = start + size - 1
        ranges.append((start, end))
        start = end + 1
    return tuple(ranges)  # type: ignore[return-value]


def _openalex_type_filter(
    source_classes: tuple[ScholarlySourceClass, ...],
) -> str:
    values: set[str] = set()
    for source_class in source_classes:
        values.update(
            {
                ScholarlySourceClass.JOURNAL: {"article"},
                ScholarlySourceClass.CONFERENCE: {
                    "article",
                    "proceedings-article",
                },
                ScholarlySourceClass.PREPRINT: {"preprint"},
                ScholarlySourceClass.SCHOLARLY_DATASET: {"dataset"},
                ScholarlySourceClass.TECHNICAL_REPORT: {"report"},
            }[source_class]
        )
    return "|".join(sorted(values))


def _openalex_source_identity(value: object) -> str:
    uri = _text(value, "OpenAlex Source id", maximum=512)
    parsed = urlsplit(uri)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "openalex.org"
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or re.fullmatch(r"/S[0-9]+", parsed.path) is None
    ):
        raise LiteratureValidationError("OpenAlex Source id is invalid")
    return f"openalex:{parsed.path[1:]}"


def _openalex_source_class_from_value(value: object) -> ScholarlySourceClass:
    if value == "journal":
        return ScholarlySourceClass.JOURNAL
    if value == "conference":
        return ScholarlySourceClass.CONFERENCE
    raise LiteratureValidationError("OpenAlex Source type is unsupported")


def _openalex_source_to_venue(source: Mapping[str, Any]) -> LiteratureVenue:
    return LiteratureVenue(
        source_id=_openalex_source_identity(source.get("id")),
        display_name=_text(
            source.get("display_name"), "OpenAlex Source display_name", maximum=512
        ),
        source_class=_openalex_source_class_from_value(source.get("type")),
        issn_l=(
            source.get("issn_l") if isinstance(source.get("issn_l"), str) else None
        ),
    )


def _openalex_source_aliases(source: Mapping[str, Any]) -> frozenset[str]:
    values: list[str] = []
    for field in ("display_name", "abbreviated_title"):
        value = source.get(field)
        if isinstance(value, str) and value.strip():
            values.append(value)
    alternates = source.get("alternate_titles")
    if isinstance(alternates, list):
        values.extend(
            value for value in alternates if isinstance(value, str) and value.strip()
        )
    return frozenset(_normalized_venue_name(value) for value in values)


def _venue_alias_matches(wanted: str, alias: str) -> bool:
    if wanted == alias:
        return True
    wanted_tokens = wanted.split()
    alias_tokens = alias.split()
    if len(wanted_tokens) < 2 or len(wanted_tokens) > len(alias_tokens):
        return False
    width = len(wanted_tokens)
    return any(
        alias_tokens[index : index + width] == wanted_tokens
        for index in range(len(alias_tokens) - width + 1)
    )


def _openalex_primary_venue(work: Mapping[str, Any]) -> LiteratureVenue | None:
    primary = work.get("primary_location")
    source = primary.get("source") if isinstance(primary, Mapping) else None
    if not isinstance(source, Mapping) or source.get("type") not in {
        "journal",
        "conference",
    }:
        return None
    try:
        return _openalex_source_to_venue(source)
    except LiteratureError:
        return None


def _openalex_pdf_uris(work: Mapping[str, Any]) -> tuple[str, ...]:
    candidates: list[str] = []
    best = work.get("best_oa_location")
    primary = work.get("primary_location")
    locations = work.get("locations")
    for location in (best, primary, *(locations if isinstance(locations, list) else ())):
        if not isinstance(location, Mapping) or location.get("is_oa") is not True:
            continue
        uri = _location_https(location, "pdf_url")
        if uri is not None and uri not in candidates:
            candidates.append(uri)
        if len(candidates) == 16:
            break
        arxiv_uri = _arxiv_pdf_uri_from_location(location)
        if arxiv_uri is not None and arxiv_uri not in candidates:
            candidates.append(arxiv_uri)
        if len(candidates) == 16:
            break
    return tuple(candidates)


def _arxiv_pdf_uri_from_location(location: object) -> str | None:
    """Derive the official PDF URL from an exact OpenAlex arXiv landing URL.

    HG's literature reconnaissance used the PDF URL returned by arXiv itself.
    OpenAlex locations sometimes preserve only that exact ``/abs/<id>`` landing
    identity.  Converting only this fixed public-host shape recovers the same
    deterministic path without a fuzzy title search or a second citation
    authority; the resulting URI still crosses the normal Hypoloom DNS, redirect,
    byte, PDF and Poppler checks.
    """

    landing_uri = _location_https(location, "landing_page_url")
    if landing_uri is None:
        return None
    parsed = urlsplit(landing_uri)
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if (
        hostname not in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
        or parsed.port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/abs/")
    ):
        return None
    identifier = parsed.path[len("/abs/") :]
    if _ARXIV_PATH_ID.fullmatch(identifier) is None:
        return None
    return f"https://arxiv.org/pdf/{identifier}"


def _openalex_work_to_paper(
    work: Mapping[str, Any],
    stratum: LiteratureStratum,
    query: LiteratureQuery,
) -> SearchPaper | None:
    try:
        work_uri, work_identity = _openalex_work_identity(work.get("id"))
        source_class = _openalex_source_class(work)
        if source_class not in query.allowed_source_classes:
            return None
        title = _text(work.get("display_name"), "OpenAlex title", maximum=1_024)
        year = _year(work.get("publication_year"), "OpenAlex publication_year")
        language = _text(work.get("language"), "OpenAlex language", maximum=3)
        authorships = work.get("authorships")
        if not isinstance(authorships, list) or len(authorships) > MAX_AUTHORS:
            raise LiteratureValidationError("OpenAlex authorships is invalid")
        authors: list[str] = []
        for authorship in authorships:
            if not isinstance(authorship, Mapping) or not isinstance(
                authorship.get("author"), Mapping
            ):
                raise LiteratureValidationError("OpenAlex authorship is invalid")
            name = _text(
                authorship["author"].get("display_name"),
                "OpenAlex author",
                maximum=256,
            )
            if name not in authors:
                authors.append(name)
        if not authors:
            raise LiteratureValidationError("OpenAlex work omitted authors")
        identity = work_identity
        doi = work.get("doi")
        if isinstance(doi, str):
            try:
                identity = _identity(f"doi:{doi}")
            except LiteratureError:
                identity = work_identity
        primary = work.get("primary_location")
        landing_uri = _location_https(primary, "landing_page_url") or work_uri
        pdf_uris = _openalex_pdf_uris(work)
        venue = _openalex_primary_venue(work) if query.schema_version >= 2 else None
        return SearchPaper(
            bibliographic_identity=identity,
            title=title,
            authors=tuple(authors),
            year=year,
            language=language,
            landing_uri=landing_uri,
            source_uri=work_uri,
            source_class=source_class,
            stratum=stratum,
            pdf_uri=pdf_uris[0] if pdf_uris else None,
            alternate_pdf_uris=pdf_uris[1:],
            venue=venue,
        )
    except (TypeError, ValueError) as exc:
        raise LiteratureFetchError(
            "OpenAlex work could not be normalized",
            code=LiteratureFailureCode.OPENALEX_RESPONSE_INVALID,
        ) from exc


def _openalex_work_identity(value: object) -> tuple[str, str]:
    uri = _text(value, "OpenAlex work id", maximum=512)
    parsed = urlsplit(uri)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "openalex.org"
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or re.fullmatch(r"/W[0-9]+", parsed.path) is None
    ):
        raise LiteratureValidationError("OpenAlex work id is invalid")
    identifier = parsed.path[1:]
    return uri, f"openalex:{identifier}"


def _openalex_source_class(work: Mapping[str, Any]) -> ScholarlySourceClass:
    work_type = work.get("type")
    if work_type == "preprint":
        return ScholarlySourceClass.PREPRINT
    if work_type == "dataset":
        return ScholarlySourceClass.SCHOLARLY_DATASET
    if work_type == "report":
        return ScholarlySourceClass.TECHNICAL_REPORT
    if work_type == "proceedings-article":
        return ScholarlySourceClass.CONFERENCE
    if work_type == "article":
        primary = work.get("primary_location")
        if isinstance(primary, Mapping) and isinstance(primary.get("source"), Mapping):
            if primary["source"].get("type") == "conference":
                return ScholarlySourceClass.CONFERENCE
        return ScholarlySourceClass.JOURNAL
    raise LiteratureValidationError("OpenAlex work type is unsupported")


def _location_https(value: object, field: str) -> str | None:
    if not isinstance(value, Mapping):
        return None
    candidate = value.get(field)
    if not isinstance(candidate, str):
        return None
    try:
        uri, _ = _uri_parts(candidate, f"OpenAlex {field}")
    except LiteratureError:
        return None
    return uri


def _download_event(
    events: list[dict[str, object]], stage: str, outcome: str,
    *, uri: str | None = None, error: LiteratureError | None = None,
) -> None:
    if len(events) >= MAX_PDF_DOWNLOAD_EVENTS_PER_PAPER:
        return
    try:
        source_host = None if uri is None else _uri_parts(uri, "diagnostic PDF URI")[1]
    except LiteratureError:
        source_host = None
    if error is not None:
        if isinstance(error, _LiteratureDnsResolutionError):
            outcome = "TRANSPORT_FAILURE"
        elif stage == "extraction":
            outcome = "EXTRACTION_FAILURE"
        elif error.http_status is not None:
            outcome = "HTTP_FAILURE"
        else:
            outcome = {
                LiteratureFailureCode.PDF_TIMEOUT: "TIMEOUT",
                LiteratureFailureCode.PDF_DEADLINE: "DEADLINE",
                LiteratureFailureCode.PDF_REQUEST_BUDGET: "REQUEST_BUDGET",
                LiteratureFailureCode.PDF_TRANSPORT: "TRANSPORT_FAILURE",
                LiteratureFailureCode.PDF_INVALID: "INVALID_PDF",
                LiteratureFailureCode.PDF_RESPONSE_LIMIT: "INVALID_PDF",
            }.get(error.code, "VALIDATION_FAILURE")
    events.append({
        "stage": stage, "outcome": outcome,
        "source_host": source_host,
        "failure_code": None if error is None else error.code.value,
        "http_status": None if error is None else error.http_status,
    })


@dataclass
class LiteratureRead:
    """Disposable worker read; the Host alone interprets its opaque run identity."""

    run_id: str
    query: LiteratureQuery
    cancelled: Event
    future: Future[dict[str, Any]]


def literature_read_diagnostics(job: LiteratureRead | None) -> dict[str, object]:
    if job is None or not job.future.done() or job.future.cancelled():
        return {}
    return dict(job.future.result()["diagnostics"])


class FixedLiteratureIO:
    """Validate one bounded query through a fixed injected fetcher."""

    def __init__(
        self,
        fetcher: LiteratureFetcher,
        *,
        fallback_locator: PdfFallbackLocator | None = None,
        max_pdf_bytes: int = DEFAULT_MAX_PDF_BYTES,
        max_pdf_pages: int = DEFAULT_MAX_PDF_PAGES,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        pdf_work_window_seconds: float = DEFAULT_PDF_WORK_WINDOW_SECONDS,
        pdf_workers: int = 1,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        for name, value, upper in (
            ("max_pdf_bytes", max_pdf_bytes, 64 * 1024 * 1024),
            ("max_pdf_pages", max_pdf_pages, 10_000),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
                raise ValueError(f"{name} must be a positive bounded integer")
        if (
            isinstance(max_redirects, bool)
            or not isinstance(max_redirects, int)
            or not 0 <= max_redirects <= 10
        ):
            raise ValueError("max_redirects must be 0..10")
        if (
            isinstance(pdf_work_window_seconds, bool)
            or not isinstance(pdf_work_window_seconds, (int, float))
            or not 1.0 <= float(pdf_work_window_seconds) <= 600.0
        ):
            raise ValueError("pdf_work_window_seconds must be within 1..600")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if isinstance(pdf_workers, bool) or not isinstance(pdf_workers, int) or not 1 <= pdf_workers <= 2:
            raise ValueError("pdf_workers must be 1..2")
        for method in ("search", "resolve", "fetch", "extract_pdf"):
            if not callable(getattr(fetcher, method, None)):
                raise TypeError(f"fetcher must implement {method}")
        if fallback_locator is not None and not callable(
            getattr(fallback_locator, "locate", None)
        ):
            raise TypeError("fallback_locator must implement locate")
        self.fetcher = fetcher
        self.fallback_locator = fallback_locator
        self.max_pdf_bytes = max_pdf_bytes
        self.max_pdf_pages = max_pdf_pages
        self.max_redirects = max_redirects
        self.pdf_work_window_seconds = float(pdf_work_window_seconds)
        self.pdf_workers = pdf_workers
        self.pdf_slots = BoundedSemaphore(pdf_workers)
        self._clock = clock
        self._pdf_lock = Lock()
        self._pdf_cache: OrderedDict[tuple[str, str], tuple[str, str, str, bytes, PdfExtraction]] = OrderedDict()
        self._pdf_cache_bytes = 0
        self._download_trace: list[dict[str, Any]] = []
        self._cancel_event: Event | None = None
        self._fallback_retry_at = 0.0

    def submit_read(self, executor: ThreadPoolExecutor, run_id: str,
                    kind: str, query: LiteratureQuery) -> LiteratureRead:
        """One Host worker serializes mutable batch traces and PDF cache reads.

        The result contains no state mutation. Existing Host task and Attempt
        identities control completion, cancellation and publication.
        """
        if kind not in {"retrieve", "coverage"}:
            raise ValueError("unknown literature read kind")
        cancelled = Event()
        started_at, started = time.time(), time.monotonic()
        def read() -> dict[str, Any]:
            value = None
            error = None
            with self.cancellation(cancelled):
                try:
                    self.check_cancelled()
                    value = self.retrieve(query) if kind == "retrieve" else self.admit_query_coverage(query)
                except Exception as exc:
                    error = exc
                trace = self.retry_trace
                downloads = self.download_trace if kind == "retrieve" else ()
                diagnostics = {
                    **({"literature_retry_trace": [dict(item) for item in trace]} if trace else {}),
                    **({"literature_download_trace": [dict(item) for item in downloads]} if downloads else {}),
                }
            return {"value": value, "error": error, "diagnostics": diagnostics,
                    "started_at": started_at, "seconds": max(0.0, time.monotonic() - started)}
        return LiteratureRead(run_id, query, cancelled, executor.submit(read))

    @contextmanager
    def cancellation(self, event: Event):
        """A single Host worker owns the adapter for the entire read and trace."""
        self._cancel_event = event
        if isinstance(self.fetcher, OpenAlexLiteratureFetcher): self.fetcher._cancel_event = event
        try:
            yield
        finally:
            self._cancel_event = None
            if isinstance(self.fetcher, OpenAlexLiteratureFetcher): self.fetcher._cancel_event = None

    def check_cancelled(self) -> None:
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise LiteratureFetchError("literature read cancelled", code=LiteratureFailureCode.PDF_DEADLINE)

    @property
    def download_trace(self) -> tuple[dict[str, object], ...]:
        """Disposable per-paper observations; no URL paths, bodies or error messages."""
        return tuple({**item, "events": [dict(event) for event in item["events"]]}
                     for item in self._download_trace)

    @property
    def max_pdf_http_calls_per_paper(self) -> int:
        """Bound one direct and one DOI-fallback redirect chain."""

        return 2 * (self.max_redirects + 1)

    @property
    def retry_trace(self) -> tuple[dict[str, object], ...]:
        """Expose only the fetcher's bounded, credential-free retry telemetry."""

        raw = getattr(self.fetcher, "last_retry_trace", ())
        if not isinstance(raw, tuple) or len(raw) > MAX_OPENALEX_RETRY_TRACE_EVENTS:
            raise LiteratureValidationError("literature retry trace is invalid")
        allowed = {
            "retry_ordinal", "endpoint", "request_attempt_ordinal",
            "failure_code", "http_status", "retry_after_seconds",
        }
        result: list[dict[str, object]] = []
        for index, item in enumerate(raw, start=1):
            if not isinstance(item, Mapping) or set(item) != allowed:
                raise LiteratureValidationError("literature retry trace is invalid")
            attempt_ordinal = item.get("request_attempt_ordinal")
            status = item.get("http_status")
            delay = item.get("retry_after_seconds")
            if (
                item.get("retry_ordinal") != index
                or item.get("endpoint") not in {"works", "sources"}
                or isinstance(attempt_ordinal, bool)
                or not isinstance(attempt_ordinal, int)
                or attempt_ordinal < 1
                or item.get("failure_code") not in {
                    value.value for value in LiteratureFailureCode
                }
                or status is not None and (
                    isinstance(status, bool)
                    or not isinstance(status, int)
                    or not 100 <= status <= 599
                )
                or isinstance(delay, bool)
                or not isinstance(delay, (int, float))
                or not math.isfinite(float(delay))
                or not 0 <= float(delay) <= MAX_OPENALEX_RETRY_BACKOFF_SECONDS
            ):
                raise LiteratureValidationError("literature retry trace is not contiguous")
            result.append(dict(item))
        return tuple(result)

    @property
    def configuration(self) -> dict[str, object]:
        raw = getattr(self.fetcher, "configuration", None)
        if not isinstance(raw, Mapping):
            raise LiteratureValidationError(
                "literature fetcher does not expose immutable configuration"
            )
        allowed = {
            "adapter", "host", "timeout_seconds", "search_wall_seconds",
            "max_response_bytes", "max_retries", "retry_backoff_seconds",
            "poppler_timeout_seconds", "poppler_max_pdf_bytes",
            "poppler_max_text_bytes", "poppler_max_summary_chars",
        }
        if set(raw) - allowed:
            raise LiteratureValidationError(
                "literature configuration may contain only adapter identity and bounds"
            )
        result = {
            "adapter_version": LITERATURE_IO_VERSION,
            **dict(raw),
            "max_pdf_bytes": self.max_pdf_bytes,
            "max_pdf_pages": self.max_pdf_pages,
            "max_redirects": self.max_redirects,
            "pdf_work_window_seconds": self.pdf_work_window_seconds,
            "pdf_workers": self.pdf_workers,
            "max_pdf_http_calls_per_paper": self.max_pdf_http_calls_per_paper,
            "max_pdf_http_calls_per_batch": MAX_PDF_HTTP_CALLS_PER_BATCH,
        }
        if self.fallback_locator is not None:
            fallback = getattr(self.fallback_locator, "configuration", None)
            if not isinstance(fallback, Mapping):
                raise LiteratureValidationError(
                    "PDF fallback locator does not expose immutable configuration"
                )
            result["pdf_fallback"] = dict(fallback)
            result["pdf_fallback_cooldown_seconds"] = PDF_FALLBACK_COOLDOWN_SECONDS
        return result

    def preflight(self) -> dict[str, object]:
        operation = getattr(self.fetcher, "preflight", None)
        if not callable(operation):
            raise LiteratureValidationError(
                "literature fetcher does not expose a read-only preflight"
            )
        result = operation()
        if not isinstance(result, Mapping):
            raise LiteratureValidationError(
                "literature fetcher preflight returned an invalid result"
            )
        checked = dict(result)
        if self.fallback_locator is not None:
            fallback = getattr(self.fallback_locator, "preflight", None)
            if not callable(fallback):
                raise LiteratureValidationError(
                    "PDF fallback locator does not expose a read-only preflight"
                )
            fallback_result = fallback()
            if not isinstance(fallback_result, Mapping):
                raise LiteratureValidationError(
                    "PDF fallback preflight returned an invalid result"
                )
            checked["pdf_fallback"] = dict(fallback_result)
        return checked

    def _search_candidates(
        self, query: LiteratureQuery,
    ) -> tuple[SearchPaper, ...]:
        self.check_cancelled()
        if not isinstance(query, LiteratureQuery):
            raise LiteratureQueryError("query must be LiteratureQuery")
        try:
            raw_results = self.fetcher.search(query)
            self.check_cancelled()
        except LiteratureError:
            raise
        except Exception as exc:
            raise LiteratureFetchError(
                f"literature search failed with {type(exc).__name__}"
            ) from exc

        candidates: list[SearchPaper] = []
        try:
            for index, candidate in enumerate(raw_results):
                self.check_cancelled()
                if index >= query.max_items:
                    raise LiteratureValidationError(
                        "literature search exceeded query max_items"
                    )
                if not isinstance(candidate, SearchPaper):
                    raise LiteratureValidationError(
                        "literature search returned a non-SearchPaper item"
                    )
                _validate_candidate_against_query(candidate, query)
                candidates.append(candidate)
        except LiteratureError:
            raise
        except Exception as exc:
            raise LiteratureFetchError(
                f"literature search iteration failed with {type(exc).__name__}"
            ) from exc
        if not candidates:
            raise LiteratureCoverageError(
                f"planned query returned no papers: {query.query}",
                missing_strata=query.strata,
            )
        identities = [item.bibliographic_identity for item in candidates]
        if len(set(identities)) != len(identities):
            raise LiteratureValidationError(
                "literature search returned duplicate bibliographic identities"
            )
        represented = {item.stratum for item in candidates}
        if represented != set(query.strata):
            missing = tuple(
                stratum for stratum in query.strata if stratum not in represented
            )
            raise LiteratureCoverageError(
                "literature search must represent every requested stratum for "
                f"planned query; missing {', '.join(item.value for item in missing)}: "
                f"{query.query}",
                missing_strata=missing,
            )

        return tuple(sorted(
            candidates,
            key=lambda item: (
                LITERATURE_STRATA.index(item.stratum),
                item.bibliographic_identity,
            ),
        ))

    def admit_query_coverage(self, query: LiteratureQuery) -> None:
        """Reject proven coverage gaps; defer other adapter failures to MAIN."""
        try:
            self._search_candidates(query)
        except LiteratureCoverageError:
            raise
        except LiteratureError:
            pass

    def retrieve(self, query: LiteratureQuery) -> LiteratureRetrieval:
        self._download_trace = []
        canonical_candidates = self._search_candidates(query)
        groups = [
            [
                item
                for item in canonical_candidates
                if item.stratum is stratum
            ]
            for stratum in LITERATURE_STRATA
        ]
        retrieval_order = tuple(
            group[index]
            for index in range(max(len(group) for group in groups))
            for group in groups
            if index < len(group)
        )
        papers: list[LiteraturePaper] = []
        artifacts: list[RetrievedPdfArtifact] = []
        pdf_deadline = self._clock() + self.pdf_work_window_seconds
        self._download_trace = [{
            "bibliographic_identity": item.bibliographic_identity,
            "stratum": item.stratum.value, "retrieval_disposition": None, "events": [],
        } for item in retrieval_order]
        for ordinal, (candidate, retrieved) in enumerate(
            zip(retrieval_order, self._retrieve_pdfs(retrieval_order, pdf_deadline), strict=True), start=1,
        ):
            self._download_trace[ordinal - 1]["retrieval_disposition"] = (
                RetrievalDisposition.PDF_UNAVAILABLE.value if retrieved is None
                else RetrievalDisposition.RETRIEVED.value
            )
            if retrieved is None:
                papers.append(_unavailable_paper(candidate))
                continue
            source_pdf_uri, final_uri, media_type, body, extraction = retrieved
            artifact_ref = f"literature-pdf-{ordinal:03d}"
            trace = PdfTrace(
                source_uri=source_pdf_uri,
                final_uri=final_uri,
                media_type=media_type,
                artifact_ref=artifact_ref,
                page_count=extraction.page_count,
                abstract_summary=extraction.abstract_summary,
                conclusions=extraction.conclusions,
                claim_support=extraction.claim_support,
            )
            papers.append(
                LiteraturePaper(
                    bibliographic_identity=candidate.bibliographic_identity,
                    title=candidate.title,
                    authors=candidate.authors,
                    year=candidate.year,
                    language=candidate.language,
                    landing_uri=candidate.landing_uri,
                    source_uri=candidate.source_uri,
                    source_class=candidate.source_class,
                    stratum=candidate.stratum,
                    retrieval_disposition=RetrievalDisposition.RETRIEVED,
                    claim_refs=tuple(
                        item.claim_ref for item in extraction.claim_support
                    ),
                    pdf_trace=trace,
                    venue=candidate.venue,
                )
            )
            artifacts.append(
                RetrievedPdfArtifact(
                    artifact_ref=artifact_ref,
                    media_type=media_type,
                    content=body,
                    pages=extraction.pages,
                )
            )
        packet = LiteraturePacket(
            schema_version=query.schema_version,
            adapter_version=_LITERATURE_IO_BY_SCHEMA[query.schema_version],
            query=query,
            papers=tuple(sorted(papers, key=lambda item: item.order_key)),
        )
        return LiteratureRetrieval(packet=packet, pdf_artifacts=tuple(artifacts))

    def _retrieve_pdfs(self, candidates: tuple[SearchPaper, ...], deadline: float):
        # Only this coordinator owns the batch credits. Reserve before dispatch;
        # workers spend local credits and unused credits return on collection.
        # Finish direct work across all strata before any slow DOI lookups.
        available = min(MAX_PDF_HTTP_CALLS_PER_BATCH,
                        self.max_pdf_http_calls_per_paper * len(candidates))
        fallback_lookups = [0]
        fallback_rate_limited = [False]
        remaining = [self.max_pdf_http_calls_per_paper] * len(candidates)
        results = [None] * len(candidates)
        context = ThreadPoolExecutor(max_workers=self.pdf_workers) if self.pdf_workers > 1 else nullcontext()
        with context as executor:
            for fallback in (False, True):
                indices = [index for index, candidate in enumerate(candidates)
                           if not fallback or results[index] is None
                           and self.fallback_locator is not None
                           and candidate.bibliographic_identity.startswith("doi:")]
                pending = deque()
                next_index = 0
                while next_index < len(indices) or pending:
                    while next_index < len(indices) and len(pending) < self.pdf_workers:
                        index = indices[next_index]
                        allocation = min(remaining[index],
                                         max(0, available - (len(indices) - next_index - 1)))
                        available -= allocation
                        calls = [allocation]
                        args = (candidates[index], calls, fallback_lookups, deadline,
                                self._download_trace[index]["events"], fallback,
                                fallback_rate_limited)
                        operation = (executor.submit(self._retrieve_pdf_candidate, *args)
                                     if executor else self._retrieve_pdf_candidate(*args))
                        pending.append((index, allocation, calls, operation))
                        next_index += 1
                    index, allocation, calls, operation = pending.popleft()
                    results[index] = operation.result() if executor else operation
                    available += calls[0]
                    remaining[index] -= allocation - calls[0]
        yield from results

    def _retrieve_pdf_candidate(
        self, candidate: SearchPaper, batch_http_calls: list[int],
        fallback_lookups: list[int], deadline: float, events: list[dict[str, object]],
        fallback: bool, fallback_rate_limited: list[bool],
    ) -> tuple[str, str, str, bytes, PdfExtraction] | None:
        if not self.pdf_slots.acquire(timeout=max(0.0, deadline - self._clock())):
            _download_event(events, "direct", "DEADLINE")
            return None
        try:
            return self._retrieve_pdf_candidate_owned(candidate, batch_http_calls, fallback_lookups,
                deadline, events, fallback, fallback_rate_limited)
        finally:
            self.pdf_slots.release()

    def _retrieve_pdf_candidate_owned(
        self, candidate: SearchPaper, batch_http_calls: list[int],
        fallback_lookups: list[int], deadline: float, events: list[dict[str, object]],
        fallback: bool, fallback_rate_limited: list[bool],
    ) -> tuple[str, str, str, bytes, PdfExtraction] | None:
        stage = "fallback_lookup" if fallback else "direct"
        if self._cancel_event is not None and self._cancel_event.is_set():
            _download_event(events, stage, "CANCELLED")
            return None
        if self._clock() >= deadline:
            _download_event(events, stage, "DEADLINE")
            return None
        if batch_http_calls[0] <= 0:
            with self._pdf_lock:
                reusable = any((candidate.bibliographic_identity, uri) in self._pdf_cache
                               for uri in candidate.pdf_uris)
            if not reusable:
                _download_event(events, stage, "REQUEST_BUDGET")
                return None
        approved_hosts: dict[str, tuple[str, ...]] = {}
        for name, uri in (("landing_uri", candidate.landing_uri), ("source_uri", candidate.source_uri)):
            if self._clock() >= deadline:
                _download_event(events, name, "DEADLINE", uri=uri)
                return None
            try:
                self._approve_uri(uri, name, approved_hosts)
            except _LiteratureDnsResolutionError as exc:
                _download_event(events, name, "TRANSPORT_FAILURE", uri=uri, error=exc)
        direct_uris = candidate.pdf_uris
        allocation = batch_http_calls[0]
        fallback_eligible = (not fallback and self.fallback_locator is not None
                             and candidate.bibliographic_identity.startswith("doi:"))
        fallback_reserve = (min(self.max_redirects + 1, allocation // 2)
                            if fallback_eligible and allocation > 1 else 0)
        paper_http_calls = [allocation - fallback_reserve]
        if not fallback:
            if not direct_uris:
                _download_event(events, "direct", "NO_OA_URL")
            retrieved = self._first_valid_pdf(
                direct_uris, approved_hosts, batch_http_calls=batch_http_calls,
                paper_http_calls=paper_http_calls, deadline=deadline,
                identity=candidate.bibliographic_identity, events=events, stage="direct",
            )
            if retrieved is None and paper_http_calls[0] <= 0:
                _download_event(events, "direct", "REQUEST_BUDGET")
            return retrieved
        with self._pdf_lock:
            if fallback_rate_limited[0] or self._clock() < self._fallback_retry_at:
                _download_event(events, "fallback_lookup", "RATE_LIMITED")
                return None
            if fallback_lookups[0] >= MAX_PDF_FALLBACK_LOOKUPS:
                _download_event(events, "fallback_lookup", "FALLBACK_LIMIT")
                return None
            if paper_http_calls[0] + fallback_reserve <= 0:
                _download_event(events, "fallback_lookup", "REQUEST_BUDGET")
                return None
            fallback_lookups[0] += 1
        try:
            located = self.fallback_locator.locate(candidate)
        except LiteratureError as exc:
            if exc.http_status == 429:
                with self._pdf_lock:
                    fallback_rate_limited[0] = True
                    self._fallback_retry_at = max(self._fallback_retry_at,
                        self._clock() + PDF_FALLBACK_COOLDOWN_SECONDS)
            _download_event(events, "fallback_lookup", "VALIDATION_FAILURE", error=exc)
            return None
        except Exception as exc:
            raise LiteratureValidationError(
                f"PDF fallback locator failed with {type(exc).__name__}"
            ) from exc
        if isinstance(located, (str, bytes)):
            raise LiteratureValidationError("PDF fallback locator must return a URI array")
        fallback_uris: list[str] = []
        try:
            for index, uri in enumerate(located):
                if index >= 4 or not isinstance(uri, str):
                    raise LiteratureValidationError("PDF fallback locator returned an invalid bounded URI array")
                fallback_uris.append(uri)
        except TypeError as exc:
            raise LiteratureValidationError("PDF fallback locator returned an invalid value") from exc
        _download_event(events, "fallback_lookup", "FALLBACK_FOUND" if fallback_uris else "FALLBACK_EMPTY")
        if self._clock() >= deadline:
            _download_event(events, "fallback_lookup", "DEADLINE")
            return None
        paper_http_calls[0] += fallback_reserve
        return self._first_valid_pdf(
            tuple(uri for uri in fallback_uris if uri not in direct_uris), approved_hosts,
            batch_http_calls=batch_http_calls, paper_http_calls=paper_http_calls,
            deadline=deadline, identity=candidate.bibliographic_identity,
            events=events, stage="fallback",
        )

    def _first_valid_pdf(
        self,
        uris: tuple[str, ...],
        approved_hosts: dict[str, tuple[str, ...]],
        *,
        batch_http_calls: list[int],
        paper_http_calls: list[int],
        deadline: float,
        identity: str,
        events: list[dict[str, object]],
        stage: str,
    ) -> tuple[str, str, str, bytes, PdfExtraction] | None:
        for source_uri in uris:
            if self._cancel_event is not None and self._cancel_event.is_set():
                _download_event(events, stage, "CANCELLED", uri=source_uri)
                break
            if self._clock() >= deadline:
                _download_event(events, stage, "DEADLINE", uri=source_uri)
                break
            with self._pdf_lock:
                cached = self._pdf_cache.get((identity, source_uri))
                if cached is not None:
                    self._pdf_cache.move_to_end((identity, source_uri))
                    _download_event(events, stage, "CACHE_HIT", uri=source_uri)
                    return cached
            if (
                batch_http_calls[0] <= 0
                or paper_http_calls[0] <= 0
            ):
                _download_event(events, stage, "REQUEST_BUDGET", uri=source_uri)
                break
            try:
                final_uri, media_type, body = self._fetch_pdf(
                    source_uri,
                    approved_hosts,
                    batch_http_calls=batch_http_calls,
                    paper_http_calls=paper_http_calls,
                    deadline=deadline,
                )
                self.check_cancelled()
                if self._clock() >= deadline:
                    _download_event(events, stage, "DEADLINE", uri=source_uri)
                    continue
            except LiteratureError as exc:
                _download_event(events, stage, "VALIDATION_FAILURE", uri=source_uri, error=exc)
                continue
            try:
                extraction = self.fetcher.extract_pdf(
                    body, max_pages=self.max_pdf_pages
                )
            except LiteratureError as exc:
                _download_event(events, "extraction", "EXTRACTION_FAILURE", uri=source_uri, error=exc)
                continue
            except Exception as exc:
                error = LiteratureValidationError(
                    f"PDF extraction failed with {type(exc).__name__}"
                )
                _download_event(events, "extraction", "EXTRACTION_FAILURE", uri=source_uri, error=error)
                raise error from exc
            if not isinstance(extraction, PdfExtraction):
                error = LiteratureValidationError(
                    "PDF extractor returned a non-PdfExtraction value"
                )
                _download_event(events, "extraction", "EXTRACTION_FAILURE", uri=source_uri, error=error)
                raise error
            if self._clock() >= deadline:
                _download_event(events, "extraction", "DEADLINE", uri=source_uri)
                continue
            if extraction.page_count > self.max_pdf_pages:
                error = LiteratureValidationError(
                    "PDF page_count exceeds configured bound"
                )
                _download_event(events, "extraction", "EXTRACTION_FAILURE", uri=source_uri, error=error)
                raise error
            if self._cancel_event is not None and self._cancel_event.is_set():
                _download_event(events, "extraction", "CANCELLED", uri=source_uri)
                continue
            result = source_uri, final_uri, media_type, body, extraction
            with self._pdf_lock:
                size = len(body)
                if size <= MAX_PDF_CACHE_BYTES:
                    previous = self._pdf_cache.pop((identity, source_uri), None)
                    if previous is not None:
                        self._pdf_cache_bytes -= len(previous[3])
                    while self._pdf_cache and (
                        len(self._pdf_cache) >= MAX_PDF_CACHE_ENTRIES
                        or self._pdf_cache_bytes + size > MAX_PDF_CACHE_BYTES
                    ):
                        _, removed = self._pdf_cache.popitem(last=False)
                        self._pdf_cache_bytes -= len(removed[3])
                    self._pdf_cache[(identity, source_uri)] = result
                    self._pdf_cache_bytes += size
            _download_event(events, stage, "RETRIEVED", uri=source_uri)
            return result
        return None

    def _approve_uri(
        self,
        value: str,
        name: str,
        cache: dict[str, tuple[str, ...]],
        *,
        refresh: bool = False,
    ) -> tuple[str, tuple[str, ...]]:
        uri, hostname = _uri_parts(value, name)
        if (
            hostname in _FORBIDDEN_HOSTS
            or hostname.endswith(_FORBIDDEN_HOST_SUFFIXES)
        ):
            raise LiteratureFetchError(f"{name} hostname is not public")
        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            literal = None
        if literal is not None:
            addresses = (_public_address(str(literal), name),)
        elif hostname in cache and not refresh:
            addresses = cache[hostname]
        else:
            try:
                resolved = self.fetcher.resolve(hostname)
            except _LiteratureDnsResolutionError as exc:
                raise _LiteratureDnsResolutionError(name, exc.hostname) from exc
            except LiteratureError:
                raise
            except OSError as exc:
                raise _LiteratureDnsResolutionError(name, hostname) from exc
            except Exception as exc:
                raise LiteratureFetchError(
                    f"{name} DNS resolution failed with {type(exc).__name__}"
                ) from exc
            if isinstance(resolved, (str, bytes)):
                raise LiteratureFetchError(f"{name} DNS result must be an address array")
            try:
                addresses = tuple(
                    sorted({_public_address(item, name) for item in resolved})
                )
            except TypeError as exc:
                raise LiteratureFetchError(f"{name} DNS result is invalid") from exc
            if not addresses:
                raise _LiteratureDnsResolutionError(name, hostname)
            if len(addresses) > 8:
                raise LiteratureFetchError(
                    f"{name} DNS result must contain 1..8 public addresses"
                )
            cache[hostname] = addresses
        return uri, addresses

    def _fetch_pdf(
        self,
        source_uri: str,
        cache: dict[str, tuple[str, ...]],
        *,
        batch_http_calls: list[int],
        paper_http_calls: list[int],
        deadline: float,
    ) -> tuple[str, str, bytes]:
        current = source_uri
        visited: set[str] = set()
        for redirect_count in range(self.max_redirects + 1):
            self.check_cancelled()
            if self._clock() >= deadline:
                raise LiteratureFetchError(
                    "PDF wall bound expired", code=LiteratureFailureCode.PDF_DEADLINE,
                )
            if batch_http_calls[0] <= 0 or paper_http_calls[0] <= 0:
                raise LiteratureFetchError(
                    "PDF request bound expired", code=LiteratureFailureCode.PDF_REQUEST_BUDGET,
                )
            uri, addresses = self._approve_uri(
                current, "PDF URI", cache, refresh=True
            )
            if self._clock() >= deadline:
                raise LiteratureFetchError(
                    "PDF work window expired after DNS resolution",
                    code=LiteratureFailureCode.PDF_DEADLINE,
                )
            if uri in visited:
                raise LiteratureFetchError("PDF redirect cycle detected")
            visited.add(uri)
            batch_http_calls[0] -= 1
            paper_http_calls[0] -= 1
            try:
                response = self.fetcher.fetch(
                    uri,
                    approved_addresses=addresses,
                    max_bytes=self.max_pdf_bytes + 1,
                )
            except LiteratureError:
                raise
            except TimeoutError as exc:
                raise LiteratureFetchError(
                    "PDF fetch timed out", code=LiteratureFailureCode.PDF_TIMEOUT,
                ) from exc
            except Exception as exc:
                raise LiteratureFetchError(
                    f"PDF fetch failed with {type(exc).__name__}", code=LiteratureFailureCode.PDF_TRANSPORT,
                ) from exc
            if not isinstance(response, FetchResponse):
                raise LiteratureFetchError("PDF fetch returned a non-FetchResponse value")
            if response.requested_uri != uri:
                raise LiteratureFetchError("PDF fetch response changed requested_uri")
            peer = _public_address(response.peer_address, "PDF peer_address")
            if peer not in addresses:
                raise LiteratureFetchError(
                    "PDF fetch peer_address was not one of the approved DNS addresses"
                )
            if response.status_code in _REDIRECT_STATUSES:
                if response.location is None:
                    raise LiteratureFetchError("PDF redirect omitted Location")
                if redirect_count >= self.max_redirects:
                    raise LiteratureFetchError("PDF redirect bound exceeded")
                current = urljoin(uri, response.location)
                continue
            if response.status_code != 200:
                raise LiteratureFetchError(
                    f"PDF fetch returned HTTP {response.status_code}",
                    code=LiteratureFailureCode.PDF_HTTP, http_status=response.status_code,
                )
            if response.location is not None:
                raise LiteratureFetchError("non-redirect PDF response included Location")
            media_type = _media_type(response.media_type)
            body = response.body
            if len(body) > self.max_pdf_bytes:
                raise LiteratureFetchError("PDF byte bound exceeded", code=LiteratureFailureCode.PDF_RESPONSE_LIMIT)
            if response.content_length is not None:
                if response.content_length > self.max_pdf_bytes:
                    raise LiteratureFetchError("PDF Content-Length exceeds byte bound", code=LiteratureFailureCode.PDF_RESPONSE_LIMIT)
                if response.content_length != len(body):
                    raise LiteratureFetchError(
                        "PDF Content-Length differs from received bytes", code=LiteratureFailureCode.PDF_INVALID,
                    )
            if not body.startswith(b"%PDF-"):
                raise LiteratureFetchError("PDF body lacks the PDF header", code=LiteratureFailureCode.PDF_INVALID)
            if not body.rstrip().endswith(b"%%EOF"):
                raise LiteratureFetchError("PDF body is truncated or lacks final EOF", code=LiteratureFailureCode.PDF_INVALID)
            return uri, media_type, body
        raise AssertionError("redirect loop exceeded its explicit bound")


def build_openalex_literature_io(
    *,
    api_key: str | None = None,
    api_key_file: str | Path | None = None,
    pdfinfo_path: str | Path = "/usr/bin/pdfinfo",
    pdftotext_path: str | Path = "/usr/bin/pdftotext",
    timeout_seconds: float = DEFAULT_OPENALEX_TIMEOUT_SECONDS,
    max_response_bytes: int = DEFAULT_OPENALEX_RESPONSE_BYTES,
    max_retries: int = DEFAULT_OPENALEX_MAX_RETRIES,
    retry_backoff_seconds: float = DEFAULT_OPENALEX_RETRY_BACKOFF_SECONDS,
    max_pdf_bytes: int = DEFAULT_MAX_PDF_BYTES,
    max_pdf_pages: int = DEFAULT_MAX_PDF_PAGES,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    pdf_work_window_seconds: float = DEFAULT_PDF_WORK_WINDOW_SECONDS,
    pdf_workers: int = 2,
    transport: OpenAlexTransport | None = None,
    semantic_scholar_transport: OpenAlexTransport | None = None,
    pdf_fallback: str = "semantic-scholar",
    unpaywall_email: str | None = None,
    unpaywall_email_file: str | Path | None = None,
    unpaywall_transport: OpenAlexTransport | None = None,
    runner: Callable[..., Any] = subprocess.run,
) -> FixedLiteratureIO:
    """Construct one explicit OpenAlex/Poppler adapter without probing network."""

    if pdf_fallback not in {"semantic-scholar", "unpaywall"}:
        raise ValueError("unknown PDF fallback")
    if pdf_fallback != "unpaywall" and (unpaywall_email is not None or unpaywall_email_file is not None
                                        or unpaywall_transport is not None):
        raise ValueError("Unpaywall options require pdf_fallback='unpaywall'")
    fallback = (UnpaywallPdfLocator(email=unpaywall_email, email_file=unpaywall_email_file, transport=unpaywall_transport,
                    timeout_seconds=min(float(timeout_seconds), 5.0)) if pdf_fallback == "unpaywall" else
                SemanticScholarPdfLocator(transport=semantic_scholar_transport,
                    timeout_seconds=min(float(timeout_seconds), 5.0)))
    extractor = PopplerPdfExtractor(
        pdfinfo_path=pdfinfo_path,
        pdftotext_path=pdftotext_path,
        runner=runner,
        timeout_seconds=timeout_seconds,
        max_pdf_bytes=max_pdf_bytes,
    )
    fetcher = OpenAlexLiteratureFetcher(
        api_key=api_key,
        api_key_file=api_key_file,
        transport=transport,
        pdf_extractor=extractor,
        timeout_seconds=timeout_seconds,
        max_response_bytes=max_response_bytes,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
    )
    return FixedLiteratureIO(
        fetcher,
        fallback_locator=fallback,
        max_pdf_bytes=max_pdf_bytes,
        max_pdf_pages=max_pdf_pages,
        max_redirects=max_redirects,
        pdf_work_window_seconds=pdf_work_window_seconds,
        pdf_workers=pdf_workers,
    )


def _media_type(value: str | None) -> str:
    if value is None:
        raise LiteratureFetchError("PDF response omitted Content-Type", code=LiteratureFailureCode.PDF_INVALID)
    media_type = value.split(";", 1)[0].strip().casefold()
    if media_type != "application/pdf":
        raise LiteratureFetchError("PDF response media type is not application/pdf", code=LiteratureFailureCode.PDF_INVALID)
    return media_type


def _public_address(value: object, name: str) -> str:
    raw = _text(value, name, maximum=64)
    try:
        address = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise LiteratureFetchError(f"{name} is not an IP address") from exc
    if not address.is_global:
        raise LiteratureFetchError(f"{name} is not globally routable")
    return str(address)


def _validate_candidate_against_query(
    paper: SearchPaper, query: LiteratureQuery
) -> None:
    if paper.source_class not in query.allowed_source_classes:
        raise LiteratureValidationError("paper source_class is not allowed by query")
    if paper.stratum not in query.strata:
        raise LiteratureValidationError("paper stratum is not requested")
    filters = query.filters
    if filters.year_from is not None and paper.year < filters.year_from:
        raise LiteratureValidationError("paper year is below query filter")
    if filters.year_to is not None and paper.year > filters.year_to:
        raise LiteratureValidationError("paper year exceeds query filter")
    if filters.languages and paper.language not in filters.languages:
        raise LiteratureValidationError("paper language is not allowed by query")
    if paper.year > date.fromisoformat(query.latest_as_of).year:
        raise LiteratureValidationError("paper year is after latest_as_of")


def _validate_paper_against_query(
    paper: LiteraturePaper, query: LiteratureQuery
) -> None:
    if paper.source_class not in query.allowed_source_classes:
        raise LiteratureValidationError("packet paper source_class is not allowed")
    if paper.stratum not in query.strata:
        raise LiteratureValidationError("packet paper stratum is not requested")
    filters = query.filters
    if filters.year_from is not None and paper.year < filters.year_from:
        raise LiteratureValidationError("packet paper year is below filter")
    if filters.year_to is not None and paper.year > filters.year_to:
        raise LiteratureValidationError("packet paper year exceeds filter")
    if filters.languages and paper.language not in filters.languages:
        raise LiteratureValidationError("packet paper language is not allowed")
    if paper.year > date.fromisoformat(query.latest_as_of).year:
        raise LiteratureValidationError("packet paper year is after latest_as_of")


def _unavailable_paper(candidate: SearchPaper) -> LiteraturePaper:
    return LiteraturePaper(
        bibliographic_identity=candidate.bibliographic_identity,
        title=candidate.title,
        authors=candidate.authors,
        year=candidate.year,
        language=candidate.language,
        landing_uri=candidate.landing_uri,
        source_uri=candidate.source_uri,
        source_class=candidate.source_class,
        stratum=candidate.stratum,
        retrieval_disposition=RetrievalDisposition.PDF_UNAVAILABLE,
        claim_refs=(),
        pdf_trace=None,
        venue=candidate.venue,
    )


__all__ = [
    "DEFAULT_MAX_PDF_BYTES",
    "DEFAULT_MAX_PDF_PAGES",
    "DEFAULT_MAX_REDIRECTS",
    "DEFAULT_OPENALEX_RESPONSE_BYTES",
    "DEFAULT_OPENALEX_TIMEOUT_SECONDS",
    "DEFAULT_POPPLER_TEXT_BYTES",
    "DEFAULT_POPPLER_TIMEOUT_SECONDS",
    "FetchResponse",
    "FixedLiteratureIO",
    "LITERATURE_IO_VERSION",
    "LITERATURE_PACKET_MEDIA_TYPE",
    "LITERATURE_SCHEMA_VERSION",
    "LITERATURE_STRATA",
    "MAX_PREFERRED_VENUES",
    "MAX_PDF_FALLBACK_LOOKUPS",
    "MAX_PDF_HTTP_CALLS_PER_BATCH",
    "DEFAULT_PDF_WORK_WINDOW_SECONDS",
    "DEFAULT_OPENALEX_SEARCH_SECONDS",
    "LiteratureCoverageError",
    "LiteratureError",
    "LiteratureFailureCode",
    "LiteratureFetchError",
    "LiteratureFetcher",
    "LiteratureFilters",
    "LiteraturePacket",
    "LiteraturePaper",
    "LiteratureQuery",
    "LiteratureQueryError",
    "LiteratureRetrieval",
    "LiteratureStratum",
    "LiteratureVenue",
    "LiteratureValidationError",
    "OPENALEX_API_HOST",
    "OpenAlexLiteratureFetcher",
    "OpenAlexResponse",
    "OpenAlexTransport",
    "PdfExtraction",
    "PdfFallbackLocator",
    "PdfTrace",
    "PopplerPdfExtractor",
    "PreferredVenue",
    "ClaimPageRef",
    "RetrievalDisposition",
    "RetrievedPdfArtifact",
    "ScholarlySourceClass",
    "SearchPaper",
    "SEMANTIC_SCHOLAR_API_HOST",
    "SemanticScholarPdfLocator",
    "StdlibOpenAlexTransport",
    "StdlibSemanticScholarTransport",
    "StdlibUnpaywallTransport",
    "UnpaywallPdfLocator",
    "admit_literature_query_plan",
    "build_openalex_literature_io",
    "expert_literature_query",
    "grounding_literature_query",
    "literature_failure_retryable",
]
