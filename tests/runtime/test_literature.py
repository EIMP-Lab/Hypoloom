from __future__ import annotations

import inspect
import json
import re
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import parse_qs, urlsplit

import pytest

import hypoloom.literature as literature_module
from hypoloom.literature import (
    FetchResponse,
    FixedLiteratureIO,
    LITERATURE_IO_VERSION,
    LITERATURE_SCHEMA_VERSION,
    LITERATURE_STRATA,
    MAX_PDF_HTTP_CALLS_PER_BATCH,
    ClaimPageRef,
    LiteratureCoverageError,
    LiteratureFetchError,
    LiteratureFailureCode,
    LiteratureFilters,
    LiteraturePacket,
    LiteratureQuery,
    LiteratureQueryError,
    LiteratureRetrieval,
    LiteratureStratum,
    LiteratureValidationError,
    OPENALEX_API_HOST,
    OpenAlexLiteratureFetcher,
    OpenAlexResponse,
    PdfExtraction,
    PopplerPdfExtractor,
    PreferredVenue,
    RetrievalDisposition,
    RetrievedPdfArtifact,
    ScholarlySourceClass,
    SearchPaper,
    SemanticScholarPdfLocator,
    admit_literature_query_plan,
    build_openalex_literature_io,
    expert_literature_query,
    grounding_literature_query,
    literature_failure_retryable,
)


PUBLIC_A = "8.8.8.8"
PUBLIC_B = "1.1.1.1"
PDF_FOUNDATIONAL = b"%PDF-1.7\nFOUNDATIONAL-PDF-BYTES\n%%EOF\n"
PDF_LATEST = b"%PDF-1.7\nLATEST-PDF-BYTES\n%%EOF\n"


@pytest.mark.parametrize(
    ("code", "status", "expected"),
    (
        (LiteratureFailureCode.OPENALEX_TIMEOUT, None, True),
        (LiteratureFailureCode.OPENALEX_RATE_LIMIT, 429, True),
        (LiteratureFailureCode.OPENALEX_HTTP, 503, True),
        (LiteratureFailureCode.OPENALEX_HTTP, 404, False),
        (LiteratureFailureCode.OPENALEX_AUTH, 401, False),
        (LiteratureFailureCode.OPENALEX_RESPONSE_INVALID, None, False),
        (LiteratureFailureCode.OPENALEX_RESPONSE_LIMIT, None, False),
        (LiteratureFailureCode.QUERY_INVALID, 400, False),
        (LiteratureFailureCode.PDF_TIMEOUT, None, True),
        (LiteratureFailureCode.POPPLER_TIMEOUT, None, True),
    ),
)
def test_outer_literature_retry_classification_is_transient_only(
    code: LiteratureFailureCode,
    status: int | None,
    expected: bool,
) -> None:
    failure = LiteratureFetchError("safe diagnostic", code=code, http_status=status)
    assert literature_failure_retryable(failure) is expected


def _query(**changes: object) -> LiteratureQuery:
    values: dict[str, object] = {
        "schema_version": LITERATURE_SCHEMA_VERSION,
        "query": "causal representation learning for multimodal affect",
        "filters": LiteratureFilters(
            year_from=1990,
            year_to=2026,
            languages=("en",),
        ),
        "max_items": 6,
        "allowed_source_classes": (
            ScholarlySourceClass.JOURNAL,
            ScholarlySourceClass.PREPRINT,
            ScholarlySourceClass.TECHNICAL_REPORT,
        ),
        "strata": LITERATURE_STRATA,
        "latest_as_of": "2026-08-29",
        "immutable_context_refs": ("goal-1", "dataset-schema-1"),
    }
    values.update(changes)
    return LiteratureQuery(**values)  # type: ignore[arg-type]


def _malformed_work_query() -> LiteratureQuery:
    return LiteratureQuery(
        schema_version=2,
        query=(
            "causal representation learning dialogue emotion recognition "
            "speaker-aware temporal modeling multimodal conversation"
        ),
        filters=LiteratureFilters(
            year_from=1997,
            year_to=2026,
            languages=("en",),
        ),
        max_items=32,
        allowed_source_classes=(
            ScholarlySourceClass.JOURNAL,
            ScholarlySourceClass.CONFERENCE,
            ScholarlySourceClass.PREPRINT,
            ScholarlySourceClass.TECHNICAL_REPORT,
        ),
        strata=LITERATURE_STRATA,
        latest_as_of="2026-08-30",
        immutable_context_refs=("test-goal", "test-query-plan"),
        preferred_venues=(
            PreferredVenue(
                name="ACL",
                source_class=ScholarlySourceClass.CONFERENCE,
            ),
            PreferredVenue(
                name="EMNLP",
                source_class=ScholarlySourceClass.CONFERENCE,
            ),
            PreferredVenue(
                name="ACM Transactions on Internet Technology",
                source_class=ScholarlySourceClass.JOURNAL,
            ),
            PreferredVenue(
                name="IEEE Transactions on Affective Computing",
                source_class=ScholarlySourceClass.JOURNAL,
            ),
        ),
    )


def _paper(
    stratum: LiteratureStratum,
    identity: str,
    *,
    year: int,
    source_class: ScholarlySourceClass = ScholarlySourceClass.JOURNAL,
    pdf_uri: str | None = None,
    host: str = "catalog.example.org",
    language: str = "en",
) -> SearchPaper:
    suffix = identity.replace(":", "-").replace("/", "-")
    return SearchPaper(
        bibliographic_identity=identity,
        title=f"Paper {suffix}",
        authors=("Ada Researcher", "Bo Scientist"),
        year=year,
        language=language,
        landing_uri=f"https://{host}/works/{suffix}",
        source_uri=f"https://{host}/records/{suffix}",
        source_class=source_class,
        stratum=stratum,
        pdf_uri=pdf_uri,
    )


def _papers() -> list[SearchPaper]:
    # Deliberately non-canonical input order.
    return [
        _paper(
            LiteratureStratum.LATEST_AS_OF_RUN,
            "openalex:w12345",
            year=2026,
            source_class=ScholarlySourceClass.PREPRINT,
            pdf_uri="https://latest.example.org/paper.pdf",
        ),
        _paper(
            LiteratureStratum.INTERMEDIATE,
            "arxiv:2101.12345v2",
            year=2021,
            source_class=ScholarlySourceClass.TECHNICAL_REPORT,
            pdf_uri=None,
        ),
        _paper(
            LiteratureStratum.FOUNDATIONAL,
            "DOI:10.1000/FOUNDATION",
            year=2004,
            pdf_uri="https://papers.example.org/foundation.pdf",
        ),
    ]


def _extraction(label: str, *, page_count: int = 8) -> PdfExtraction:
    return PdfExtraction(
        page_count=page_count,
        abstract_summary=f"Bounded abstract summary for {label}.",
        conclusions=f"Bounded conclusion summary for {label}.",
        claim_support=(
            ClaimPageRef(
                claim_ref=f"claim-{label}",
                section="Results",
                page_start=2,
                page_end=3,
            ),
        ),
    )


class FakeFetcher:
    def __init__(
        self,
        *,
        papers: Iterable[SearchPaper] | None = None,
        resolutions: dict[str, Sequence[str]] | None = None,
        responses: dict[str, FetchResponse] | None = None,
        extractions: dict[bytes, PdfExtraction] | None = None,
    ) -> None:
        self.papers = list(_papers() if papers is None else papers)
        self.resolutions = {
            "catalog.example.org": (PUBLIC_A,),
            "papers.example.org": (PUBLIC_A,),
            "cdn.example.org": (PUBLIC_B,),
            "latest.example.org": (PUBLIC_A,),
            **(resolutions or {}),
        }
        self.responses = {
            "https://papers.example.org/foundation.pdf": FetchResponse(
                requested_uri="https://papers.example.org/foundation.pdf",
                status_code=302,
                peer_address=PUBLIC_A,
                media_type=None,
                content_length=0,
                location="https://cdn.example.org/foundation.pdf",
                body=b"",
            ),
            "https://cdn.example.org/foundation.pdf": FetchResponse(
                requested_uri="https://cdn.example.org/foundation.pdf",
                status_code=200,
                peer_address=PUBLIC_B,
                media_type="application/pdf; charset=binary",
                content_length=len(PDF_FOUNDATIONAL),
                location=None,
                body=PDF_FOUNDATIONAL,
            ),
            "https://latest.example.org/paper.pdf": FetchResponse(
                requested_uri="https://latest.example.org/paper.pdf",
                status_code=200,
                peer_address=PUBLIC_A,
                media_type="application/pdf",
                content_length=len(PDF_LATEST),
                location=None,
                body=PDF_LATEST,
            ),
            **(responses or {}),
        }
        self.extractions = {
            PDF_FOUNDATIONAL: _extraction("foundational"),
            PDF_LATEST: _extraction("latest"),
            **(extractions or {}),
        }
        self.search_calls: list[LiteratureQuery] = []
        self.resolve_calls: list[str] = []
        self.fetch_calls: list[tuple[str, tuple[str, ...], int]] = []
        self.extract_calls: list[tuple[bytes, int]] = []

    def search(self, query: LiteratureQuery) -> Iterable[SearchPaper]:
        self.search_calls.append(query)
        return iter(self.papers)

    def resolve(self, hostname: str) -> Sequence[str]:
        self.resolve_calls.append(hostname)
        return self.resolutions[hostname]

    def fetch(
        self,
        uri: str,
        *,
        approved_addresses: tuple[str, ...],
        max_bytes: int,
    ) -> FetchResponse:
        self.fetch_calls.append((uri, approved_addresses, max_bytes))
        return self.responses[uri]

    def extract_pdf(self, content: bytes, *, max_pages: int) -> PdfExtraction:
        self.extract_calls.append((content, max_pages))
        return self.extractions[content]


def test_fixed_adapter_returns_canonical_packet_and_separate_pdf_bytes() -> None:
    fetcher = FakeFetcher()
    query = _query()

    result = FixedLiteratureIO(
        fetcher,
        max_pdf_bytes=1_024,
        max_pdf_pages=20,
        max_redirects=2,
    ).retrieve(query)

    assert fetcher.search_calls == [query]
    assert [paper.stratum for paper in result.packet.papers] == list(
        LITERATURE_STRATA
    )
    assert [paper.bibliographic_identity for paper in result.packet.papers] == [
        "doi:10.1000/foundation",
        "arxiv:2101.12345v2",
        "openalex:W12345",
    ]
    assert result.packet.papers[1].retrieval_disposition is (
        RetrievalDisposition.PDF_UNAVAILABLE
    )
    assert result.packet.papers[1].claim_refs == ()
    assert result.packet.papers[1].pdf_trace is None
    assert [item.artifact_ref for item in result.pdf_artifacts] == [
        "literature-pdf-001",
        "literature-pdf-003",
    ]
    assert [item.content for item in result.pdf_artifacts] == [
        PDF_FOUNDATIONAL,
        PDF_LATEST,
    ]
    assert result.packet.papers[0].pdf_trace is not None
    assert result.packet.papers[0].pdf_trace.final_uri == (
        "https://cdn.example.org/foundation.pdf"
    )
    assert result.packet.papers[0].claim_refs == ("claim-foundational",)
    encoded = result.packet.to_bytes()
    assert PDF_FOUNDATIONAL not in encoded
    assert PDF_LATEST not in encoded
    assert LiteraturePacket.from_bytes(encoded) == result.packet
    assert LiteratureQuery.from_dict(query.to_dict()) == query
    assert LITERATURE_IO_VERSION.encode() in encoded


def test_pdf_candidates_and_doi_fallback_are_isolated_per_paper() -> None:
    bad_uri = "https://latest.example.org/bad.pdf"
    alternate_uri = "https://alternate.example.org/good.pdf"
    latest = replace(
        _papers()[0],
        pdf_uri=bad_uri,
        alternate_pdf_uris=(alternate_uri,),
    )
    responses = {
        bad_uri: FetchResponse(
            requested_uri=bad_uri,
            status_code=403,
            peer_address=PUBLIC_A,
            media_type="text/html",
            content_length=0,
            location=None,
            body=b"",
        ),
        alternate_uri: FetchResponse(
            requested_uri=alternate_uri,
            status_code=200,
            peer_address=PUBLIC_A,
            media_type="application/pdf",
            content_length=len(PDF_LATEST),
            location=None,
            body=PDF_LATEST,
        ),
    }
    fetcher = FakeFetcher(
        papers=[latest, *_papers()[1:]],
        resolutions={"alternate.example.org": (PUBLIC_A,)},
        responses=responses,
    )
    result = FixedLiteratureIO(fetcher).retrieve(_query())
    trace = result.packet.papers[-1].pdf_trace
    assert trace is not None and trace.source_uri == alternate_uri

    fallback_uri = "https://fallback.example.org/paper.pdf"

    class Locator:
        def __init__(self) -> None:
            self.calls: list[SearchPaper] = []

        def locate(self, paper: SearchPaper) -> Sequence[str]:
            self.calls.append(paper)
            return (fallback_uri,) if paper.bibliographic_identity.startswith("doi:") else ()

    bad_direct_uris = tuple(
        f"https://latest.example.org/bad-{index}.pdf" for index in range(4)
    )
    foundation = replace(
        _papers()[-1],
        pdf_uri=bad_direct_uris[0],
        alternate_pdf_uris=bad_direct_uris[1:],
    )
    locator = Locator()
    fallback_fetcher = FakeFetcher(
        papers=[*_papers()[:2], foundation],
        resolutions={"fallback.example.org": (PUBLIC_A,)},
        responses={
            **{
                uri: replace(responses[bad_uri], requested_uri=uri)
                for uri in bad_direct_uris
            },
            fallback_uri: FetchResponse(
                requested_uri=fallback_uri,
                status_code=200,
                peer_address=PUBLIC_A,
                media_type="application/pdf",
                content_length=len(PDF_FOUNDATIONAL),
                location=None,
                body=PDF_FOUNDATIONAL,
            ),
        },
    )
    fallback_result = FixedLiteratureIO(
        fallback_fetcher, fallback_locator=locator
    ).retrieve(_query())
    foundation_trace = fallback_result.packet.papers[0].pdf_trace
    assert foundation_trace is not None and foundation_trace.source_uri == fallback_uri
    assert {*bad_direct_uris, fallback_uri} <= {
        call[0] for call in fallback_fetcher.fetch_calls
    }
    assert [paper.bibliographic_identity for paper in locator.calls] == [
        "doi:10.1000/foundation",
    ]


def test_non_doi_records_do_not_exhaust_exact_doi_fallback_lookups() -> None:
    papers = [
        _paper(
            LiteratureStratum.FOUNDATIONAL,
            f"openalex:W{index}",
            year=2000 + index,
        )
        for index in range(1, 9)
    ]
    papers.append(
        _paper(
            LiteratureStratum.INTERMEDIATE,
            "doi:10.1000/fallback-after-nondoi",
            year=2020,
        )
    )
    papers.append(
        _paper(
            LiteratureStratum.LATEST_AS_OF_RUN,
            "openalex:W99",
            year=2026,
        )
    )
    fallback_uri = "https://fallback.example.org/after-nondoi.pdf"

    class Locator:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def locate(self, paper: SearchPaper) -> Sequence[str]:
            self.calls.append(paper.bibliographic_identity)
            return (fallback_uri,)

    locator = Locator()
    fetcher = FakeFetcher(
        papers=papers,
        resolutions={"fallback.example.org": (PUBLIC_A,)},
        responses={
            fallback_uri: FetchResponse(
                requested_uri=fallback_uri,
                status_code=200,
                peer_address=PUBLIC_A,
                media_type="application/pdf",
                content_length=len(PDF_FOUNDATIONAL),
                location=None,
                body=PDF_FOUNDATIONAL,
            )
        },
    )
    query = replace(_query(max_items=10), filters=LiteratureFilters(
        year_from=1990, year_to=2026, languages=("en",)
    ))

    result = FixedLiteratureIO(
        fetcher, fallback_locator=locator
    ).retrieve(query)

    assert locator.calls == ["doi:10.1000/fallback-after-nondoi"]
    doi_paper = next(
        paper
        for paper in result.packet.papers
        if paper.bibliographic_identity == "doi:10.1000/fallback-after-nondoi"
    )
    assert doi_paper.retrieval_disposition is RetrievalDisposition.RETRIEVED


def test_pdf_http_requests_have_one_batch_wide_bound() -> None:
    papers: list[SearchPaper] = []
    responses: dict[str, FetchResponse] = {}
    primary_uris: set[str] = set()
    for index in range(1, 21):
        stratum = LITERATURE_STRATA[(index - 1) % len(LITERATURE_STRATA)]
        uris = tuple(
            f"https://papers.example.org/{index}-{candidate}.pdf"
            for candidate in range(10)
        )
        primary_uris.add(uris[0])
        papers.append(
            replace(
                _paper(stratum, f"openalex:W{index}", year=2000 + index),
                pdf_uri=uris[0],
                alternate_pdf_uris=uris[1:],
            )
        )
        for uri in uris:
            responses[uri] = FetchResponse(
                requested_uri=uri,
                status_code=403,
                peer_address=PUBLIC_A,
                media_type="text/html",
                content_length=0,
                location=None,
                body=b"",
            )
    fetcher = FakeFetcher(papers=papers, responses=responses)

    result = FixedLiteratureIO(fetcher).retrieve(_query(max_items=20))

    assert len(fetcher.fetch_calls) == MAX_PDF_HTTP_CALLS_PER_BATCH
    assert primary_uris <= {call[0] for call in fetcher.fetch_calls}
    assert all(
        paper.retrieval_disposition is RetrievalDisposition.PDF_UNAVAILABLE
        for paper in result.packet.papers
    )


def test_pdf_batch_budget_is_distributed_across_all_time_strata() -> None:
    papers: list[SearchPaper] = []
    responses: dict[str, FetchResponse] = {}
    for index in range(1, 21):
        stratum = LITERATURE_STRATA[(index - 1) % len(LITERATURE_STRATA)]
        uris = tuple(
            f"https://papers.example.org/fair-{index}-{candidate}.pdf"
            for candidate in range(5)
        )
        papers.append(
            replace(
                _paper(stratum, f"openalex:W{index}", year=2000 + index),
                pdf_uri=uris[0],
                alternate_pdf_uris=uris[1:],
            )
        )
        for uri in uris[:-1]:
            responses[uri] = FetchResponse(
                requested_uri=uri,
                status_code=403,
                peer_address=PUBLIC_A,
                media_type="text/html",
                content_length=0,
                location=None,
                body=b"",
            )
        responses[uris[-1]] = FetchResponse(
            requested_uri=uris[-1],
            status_code=200,
            peer_address=PUBLIC_A,
            media_type="application/pdf",
            content_length=len(PDF_FOUNDATIONAL),
            location=None,
            body=PDF_FOUNDATIONAL,
        )
    fetcher = FakeFetcher(papers=papers, responses=responses)

    result = FixedLiteratureIO(fetcher).retrieve(_query(max_items=20))

    retrieved_by_stratum = {
        stratum: sum(
            paper.stratum is stratum
            and paper.retrieval_disposition is RetrievalDisposition.RETRIEVED
            for paper in result.packet.papers
        )
        for stratum in LITERATURE_STRATA
    }
    assert len(fetcher.fetch_calls) == MAX_PDF_HTTP_CALLS_PER_BATCH
    assert min(retrieved_by_stratum.values()) > 0
    assert max(retrieved_by_stratum.values()) - min(retrieved_by_stratum.values()) <= 1


def test_pdf_work_window_stops_later_candidate_calls() -> None:
    class Clock:
        def __init__(self) -> None:
            self.value = 0.0

        def __call__(self) -> float:
            return self.value

    clock = Clock()
    papers = []
    responses: dict[str, FetchResponse] = {}
    for index, stratum in enumerate(LITERATURE_STRATA, start=1):
        uris = tuple(
            f"https://papers.example.org/wall-{index}-{candidate}.pdf"
            for candidate in range(4)
        )
        papers.append(
            replace(
                _paper(stratum, f"openalex:W{index}", year=2000 + index),
                pdf_uri=uris[0],
                alternate_pdf_uris=uris[1:],
            )
        )
        for uri in uris:
            responses[uri] = FetchResponse(
                requested_uri=uri,
                status_code=403,
                peer_address=PUBLIC_A,
                media_type="text/html",
                content_length=0,
                location=None,
                body=b"",
            )

    class TimedFetcher(FakeFetcher):
        def fetch(
            self,
            uri: str,
            *,
            approved_addresses: tuple[str, ...],
            max_bytes: int,
        ) -> FetchResponse:
            response = super().fetch(
                uri,
                approved_addresses=approved_addresses,
                max_bytes=max_bytes,
            )
            clock.value += 50.0
            return response

    fetcher = TimedFetcher(papers=papers, responses=responses)

    result = FixedLiteratureIO(
        fetcher, pdf_work_window_seconds=100.0, clock=clock
    ).retrieve(_query(max_items=3))

    assert len(fetcher.fetch_calls) == 2
    assert all(
        paper.retrieval_disposition is RetrievalDisposition.PDF_UNAVAILABLE
        for paper in result.packet.papers
    )


def test_pdf_work_completed_after_window_is_not_accepted() -> None:
    class Clock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

    clock = Clock()

    class SlowExtractor(FakeFetcher):
        def extract_pdf(self, content: bytes, *, max_pages: int) -> PdfExtraction:
            result = super().extract_pdf(content, max_pages=max_pages)
            clock.value += 2.0
            return result

    result = FixedLiteratureIO(
        SlowExtractor(), pdf_work_window_seconds=1.0, clock=clock
    ).retrieve(_query())

    assert result.pdf_artifacts == ()
    assert all(
        paper.retrieval_disposition is RetrievalDisposition.PDF_UNAVAILABLE
        for paper in result.packet.papers
    )


def test_fallback_uri_iterable_is_materialized_to_only_its_bound() -> None:
    yielded: list[int] = []

    class Locator:
        def locate(self, paper: SearchPaper) -> Iterable[str]:
            def values() -> Iterable[str]:
                for index in range(1_000_000):
                    yielded.append(index)
                    yield f"https://fallback.example.org/{index}.pdf"

            return values()

    papers = [replace(_papers()[-1], pdf_uri=None), *_papers()[:2]]

    with pytest.raises(LiteratureValidationError, match="bounded URI array"):
        FixedLiteratureIO(
            FakeFetcher(papers=papers), fallback_locator=Locator()
        ).retrieve(_query())

    assert yielded == [0, 1, 2, 3, 4]


def test_runtime_configuration_freezes_identity_and_bounds_without_credentials(
    tmp_path: Path,
) -> None:
    secret = "openalex-secret-canary"
    key_file = tmp_path / "openalex.key"
    key_file.write_text(f"{secret}\n", encoding="utf-8")
    key_file.chmod(0o600)
    adapter = build_openalex_literature_io(
        api_key_file=key_file,
        pdfinfo_path="/bin/true",
        pdftotext_path="/bin/true",
        timeout_seconds=3.5,
        max_response_bytes=8_192,
        max_pdf_bytes=16_384,
        max_pdf_pages=17,
    )

    configuration = adapter.configuration

    assert configuration["adapter"] == "openalex"
    assert configuration["adapter_version"] == LITERATURE_IO_VERSION
    assert configuration["max_retries"] == 2
    assert configuration["retry_backoff_seconds"] == 0.25
    assert configuration["max_pdf_pages"] == 17
    encoded = json.dumps(configuration, sort_keys=True)
    assert secret not in encoded
    assert str(key_file) not in encoded
    assert "/bin/true" not in encoded


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"schema_version": 3}, "schema_version"),
        ({"query": ""}, "query"),
        ({"max_items": 2}, "one item per stratum"),
        ({"max_items": 33}, "1..32"),
        (
            {
                "strata": (
                    LiteratureStratum.FOUNDATIONAL,
                    LiteratureStratum.LATEST_AS_OF_RUN,
                    LiteratureStratum.INTERMEDIATE,
                )
            },
            "canonical order",
        ),
        ({"latest_as_of": "2026-8-29"}, "ISO calendar date"),
        (
            {
                "allowed_source_classes": (
                    ScholarlySourceClass.JOURNAL,
                    ScholarlySourceClass.JOURNAL,
                )
            },
            "duplicates",
        ),
    ],
)
def test_query_contract_is_closed_and_bounded(
    changes: dict[str, object], match: str
) -> None:
    with pytest.raises(LiteratureQueryError, match=match):
        _query(**changes)

    raw = _query().to_dict()
    raw["unknown"] = True
    with pytest.raises(LiteratureValidationError, match="unknown"):
        LiteratureQuery.from_dict(raw)


def test_query_filters_are_closed_and_canonical() -> None:
    with pytest.raises(LiteratureQueryError, match="must not exceed"):
        LiteratureFilters(year_from=2020, year_to=2019, languages=("en",))
    with pytest.raises(LiteratureQueryError, match="lowercase"):
        LiteratureFilters(year_from=None, year_to=None, languages=("EN",))
    raw = LiteratureFilters(year_from=None, year_to=None, languages=())
    assert raw.to_dict() == {"year_from": None, "year_to": None, "languages": []}
    with pytest.raises(LiteratureValidationError, match="unknown"):
        LiteratureFilters.from_dict(
            {"year_from": None, "year_to": None, "languages": [], "x": 1}
        )


def test_query_plan_admission_freezes_host_policy_and_does_not_rewrite() -> None:
    template = _query()
    candidate = replace(
        template,
        query='"multimodal affect" AND (causal OR robust) NOT survey',
    )

    admitted = admit_literature_query_plan(candidate, template=template)

    assert admitted is candidate
    assert admitted.query == candidate.query
    frozen_changes = (
        {"max_items": template.max_items - 1},
        {
            "filters": LiteratureFilters(
                year_from=2000,
                year_to=template.filters.year_to,
                languages=template.filters.languages,
            )
        },
        {"immutable_context_refs": ("agent-chosen-ref",)},
    )
    for changes in frozen_changes:
        with pytest.raises(LiteratureQueryError, match="frozen template"):
            admit_literature_query_plan(
                replace(candidate, **changes),
                template=template,
            )


def test_query_v2_admits_bounded_venue_preferences_and_v1_remains_exact() -> None:
    template = _query()
    candidate = replace(
        template,
        preferred_venues=(
            PreferredVenue(
                name="IEEE Transactions on Pattern Analysis and Machine Intelligence",
                source_class=ScholarlySourceClass.JOURNAL,
            ),
            PreferredVenue(
                name="Neural Information Processing Systems",
                source_class=ScholarlySourceClass.CONFERENCE,
            ),
        ),
    )
    assert admit_literature_query_plan(candidate, template=template) is candidate
    assert LiteratureQuery.from_dict(candidate.to_dict()) == candidate

    with pytest.raises(LiteratureQueryError, match="duplicates"):
        replace(
            template,
            preferred_venues=(
                PreferredVenue(
                    name="NeurIPS",
                    source_class=ScholarlySourceClass.CONFERENCE,
                ),
                PreferredVenue(
                    name="neurips",
                    source_class=ScholarlySourceClass.CONFERENCE,
                ),
            ),
        )
    with pytest.raises(LiteratureQueryError, match="JOURNAL or CONFERENCE"):
        PreferredVenue(
            name="arXiv",
            source_class=ScholarlySourceClass.PREPRINT,
        )

    legacy = template.to_dict()
    legacy["schema_version"] = 1
    legacy.pop("preferred_venues")
    parsed = LiteratureQuery.from_dict(legacy)
    assert parsed.schema_version == 1
    assert "preferred_venues" not in parsed.to_dict()
    legacy_packet = FixedLiteratureIO(FakeFetcher()).retrieve(parsed).packet
    assert legacy_packet.adapter_version == "fixed-literature-io/1"
    assert "venue" not in legacy_packet.to_dict()["papers"][0]
    assert LiteraturePacket.from_bytes(legacy_packet.to_bytes()) == legacy_packet


@pytest.mark.parametrize(
    "query",
    (
        "causal*",
        "causal?",
        "causal~2",
        "https://example.org/paper",
        "/tmp/query",
        "[causal](paper)",
        "`causal`",
        "causal and robust",
        "causal NOT NOT robust",
        "NOT NOT causal",
        "causal AND (robust",
        "causal OR",
        '""',
    ),
)
def test_query_plan_admission_rejects_unsafe_openalex_syntax(query: str) -> None:
    template = _query()
    with pytest.raises(LiteratureQueryError):
        admit_literature_query_plan(
            replace(template, query=query),
            template=template,
        )


@pytest.mark.parametrize(
    "missing_strata",
    (
        (LiteratureStratum.FOUNDATIONAL,),
        (LiteratureStratum.INTERMEDIATE,),
        (
            LiteratureStratum.FOUNDATIONAL,
            LiteratureStratum.INTERMEDIATE,
        ),
    ),
)
def test_coverage_error_names_missing_early_strata_for_query_broadening(
    missing_strata: tuple[LiteratureStratum, ...],
) -> None:
    papers = [
        paper for paper in _papers() if paper.stratum not in missing_strata
    ]

    with pytest.raises(
        LiteratureCoverageError,
        match="every requested stratum",
    ) as sparse:
        FixedLiteratureIO(FakeFetcher(papers=papers)).retrieve(_query())

    assert sparse.value.missing_strata == missing_strata
    missing_names = ", ".join(stratum.value for stratum in missing_strata)
    assert f"missing {missing_names}:" in str(sparse.value)


def test_search_count_identity_strata_and_filters_fail_closed() -> None:
    query = _query(max_items=3)
    extra = _paper(
        LiteratureStratum.LATEST_AS_OF_RUN,
        "pmid:12345",
        year=2025,
        pdf_uri=None,
    )
    with pytest.raises(LiteratureValidationError, match="max_items"):
        FixedLiteratureIO(FakeFetcher(papers=[*_papers(), extra])).retrieve(query)

    with pytest.raises(LiteratureCoverageError, match="no papers") as empty:
        FixedLiteratureIO(FakeFetcher(papers=[])).retrieve(_query())
    assert empty.value.missing_strata == LITERATURE_STRATA

    duplicate = replace(_papers()[1], bibliographic_identity="ARXIV:2101.12345V2")
    with pytest.raises(LiteratureValidationError, match="duplicate bibliographic"):
        FixedLiteratureIO(FakeFetcher(papers=[*_papers(), duplicate])).retrieve(
            _query()
        )

    disallowed = replace(
        _papers()[0], source_class=ScholarlySourceClass.SCHOLARLY_DATASET
    )
    with pytest.raises(LiteratureValidationError, match="source_class"):
        FixedLiteratureIO(
            FakeFetcher(papers=[disallowed, *_papers()[1:]])
        ).retrieve(_query())

    too_new = replace(_papers()[0], year=2027)
    with pytest.raises(LiteratureValidationError, match="year"):
        FixedLiteratureIO(FakeFetcher(papers=[too_new, *_papers()[1:]])).retrieve(
            _query()
        )

    wrong_language = replace(_papers()[0], language="fr")
    with pytest.raises(LiteratureValidationError, match="language"):
        FixedLiteratureIO(
            FakeFetcher(papers=[wrong_language, *_papers()[1:]])
        ).retrieve(_query())


@pytest.mark.parametrize(
    "uri",
    [
        "http://papers.example.org/paper.pdf",
        "https://user:secret@papers.example.org/paper.pdf",
        "https://papers.example.org:8443/paper.pdf",
        "https://papers.example.org/paper.pdf#fragment",
        "https://localhost/paper.pdf",
        "https://metadata.google.internal/computeMetadata/v1",
        "https://127.0.0.1/paper.pdf",
        "https://10.0.0.1/paper.pdf",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/paper.pdf",
    ],
)
def test_uri_shape_rejects_credential_downgrade_and_nonpublic_literals(
    uri: str,
) -> None:
    with pytest.raises(LiteratureFetchError):
        _paper(
            LiteratureStratum.FOUNDATIONAL,
            "doi:10.1000/unsafe",
            year=2001,
            pdf_uri=uri,
        )


def test_dns_results_and_connected_peer_must_be_exact_public_addresses() -> None:
    private_dns = FakeFetcher(
        resolutions={"catalog.example.org": ("10.0.0.7",)}
    )
    with pytest.raises(LiteratureFetchError, match="globally routable"):
        FixedLiteratureIO(private_dns).retrieve(_query())
    assert private_dns.fetch_calls == []

    bad_peer = FakeFetcher(
        responses={
            "https://latest.example.org/paper.pdf": FetchResponse(
                requested_uri="https://latest.example.org/paper.pdf",
                status_code=200,
                peer_address=PUBLIC_B,
                media_type="application/pdf",
                content_length=len(PDF_LATEST),
                location=None,
                body=PDF_LATEST,
            )
        }
    )
    result = FixedLiteratureIO(bad_peer).retrieve(_query())
    assert result.packet.papers[-1].retrieval_disposition is (
        RetrievalDisposition.PDF_UNAVAILABLE
    )


@pytest.mark.parametrize("field", ("landing_uri", "source_uri"))
def test_public_candidate_uri_without_dns_answer_keeps_unavailable_paper(
    field: str,
) -> None:
    unavailable_host = "researchbank.acu.edu.au"
    foundational, intermediate, latest = sorted(
        _papers(),
        key=lambda paper: LITERATURE_STRATA.index(paper.stratum),
    )
    unavailable = replace(
        foundational,
        pdf_uri="https://missing-pdf.example.org/unavailable.pdf",
        **{field: f"https://{unavailable_host}/unavailable"},
    )
    fetcher = FakeFetcher(
        papers=[unavailable, intermediate, latest],
        resolutions={
            unavailable_host: (),
            "missing-pdf.example.org": (),
        },
    )

    retrieval = FixedLiteratureIO(fetcher).retrieve(_query())
    packet = LiteraturePacket.from_bytes(retrieval.packet.to_bytes())

    retained = next(
        paper
        for paper in packet.papers
        if paper.bibliographic_identity == unavailable.bibliographic_identity
    )
    assert retained.retrieval_disposition is RetrievalDisposition.PDF_UNAVAILABLE
    assert retained.pdf_trace is None
    assert [paper.stratum for paper in packet.papers] == list(LITERATURE_STRATA)


def test_every_redirect_re_resolves_and_rejects_rebinding_or_metadata() -> None:
    class RebindingFetcher(FakeFetcher):
        def __init__(self) -> None:
            super().__init__(
                papers=[
                    replace(
                        paper,
                        landing_uri=paper.landing_uri.replace(
                            "catalog.example.org", "stable.example.org"
                        ),
                        source_uri=paper.source_uri.replace(
                            "catalog.example.org", "stable.example.org"
                        ),
                    )
                    for paper in _papers()
                ],
                resolutions={"stable.example.org": (PUBLIC_A,)},
                responses={
                    "https://papers.example.org/foundation.pdf": FetchResponse(
                        requested_uri="https://papers.example.org/foundation.pdf",
                        status_code=302,
                        peer_address=PUBLIC_A,
                        media_type=None,
                        content_length=0,
                        location="/second-hop.pdf",
                        body=b"",
                    )
                },
            )
            self.paper_resolutions = 0

        def resolve(self, hostname: str) -> Sequence[str]:
            if hostname == "papers.example.org":
                self.resolve_calls.append(hostname)
                self.paper_resolutions += 1
                return (PUBLIC_A,) if self.paper_resolutions == 1 else ("127.0.0.1",)
            return super().resolve(hostname)

    fetcher = RebindingFetcher()
    result = FixedLiteratureIO(fetcher).retrieve(_query())
    assert result.packet.papers[0].retrieval_disposition is (
        RetrievalDisposition.PDF_UNAVAILABLE
    )
    assert fetcher.paper_resolutions == 2

    metadata_redirect = FakeFetcher(
        responses={
            "https://papers.example.org/foundation.pdf": FetchResponse(
                requested_uri="https://papers.example.org/foundation.pdf",
                status_code=302,
                peer_address=PUBLIC_A,
                media_type=None,
                content_length=0,
                location="https://169.254.169.254/latest/meta-data",
                body=b"",
            )
        }
    )
    result = FixedLiteratureIO(metadata_redirect).retrieve(_query())
    assert result.packet.papers[0].retrieval_disposition is (
        RetrievalDisposition.PDF_UNAVAILABLE
    )


@pytest.mark.parametrize(
    ("response", "max_bytes", "match"),
    [
        (
            FetchResponse(
                requested_uri="https://latest.example.org/paper.pdf",
                status_code=200,
                peer_address=PUBLIC_A,
                media_type="text/html",
                content_length=13,
                location=None,
                body=b"<html></html>",
            ),
            1_024,
            "media type",
        ),
        (
            FetchResponse(
                requested_uri="https://latest.example.org/paper.pdf",
                status_code=200,
                peer_address=PUBLIC_A,
                media_type="application/pdf",
                content_length=len(PDF_LATEST) + 1,
                location=None,
                body=PDF_LATEST,
            ),
            1_024,
            "differs",
        ),
        (
            FetchResponse(
                requested_uri="https://latest.example.org/paper.pdf",
                status_code=200,
                peer_address=PUBLIC_A,
                media_type="application/pdf",
                content_length=len(PDF_LATEST),
                location=None,
                body=b"NOT-A-PDF" + b"x" * (len(PDF_LATEST) - 9),
            ),
            1_024,
            "header",
        ),
        (
            FetchResponse(
                requested_uri="https://latest.example.org/paper.pdf",
                status_code=200,
                peer_address=PUBLIC_A,
                media_type="application/pdf",
                content_length=len(b"%PDF-1.7\ntruncated"),
                location=None,
                body=b"%PDF-1.7\ntruncated",
            ),
            1_024,
            "truncated",
        ),
        (
            FetchResponse(
                requested_uri="https://latest.example.org/paper.pdf",
                status_code=503,
                peer_address=PUBLIC_A,
                media_type=None,
                content_length=0,
                location=None,
                body=b"",
            ),
            1_024,
            "HTTP 503",
        ),
        (
            FetchResponse(
                requested_uri="https://latest.example.org/paper.pdf",
                status_code=200,
                peer_address=PUBLIC_A,
                media_type="application/pdf",
                content_length=len(PDF_LATEST),
                location=None,
                body=PDF_LATEST,
            ),
            len(PDF_LATEST) - 1,
            "byte bound",
        ),
    ],
)
def test_invalid_pdf_candidate_is_rejected_without_aborting_the_packet(
    response: FetchResponse, max_bytes: int, match: str
) -> None:
    fetcher = FakeFetcher(
        responses={"https://latest.example.org/paper.pdf": response}
    )
    result = FixedLiteratureIO(fetcher, max_pdf_bytes=max_bytes).retrieve(_query())
    assert result.packet.papers[-1].retrieval_disposition is (
        RetrievalDisposition.PDF_UNAVAILABLE
    )
    assert result.packet.papers[-1].pdf_trace is None


def test_bad_pdf_redirect_isolated_to_its_paper() -> None:
    cycle = FakeFetcher(
        responses={
            "https://papers.example.org/foundation.pdf": FetchResponse(
                requested_uri="https://papers.example.org/foundation.pdf",
                status_code=302,
                peer_address=PUBLIC_A,
                media_type=None,
                content_length=0,
                location="/foundation.pdf",
                body=b"",
            )
        }
    )
    result = FixedLiteratureIO(cycle, max_redirects=2).retrieve(_query())
    assert result.packet.papers[0].retrieval_disposition is RetrievalDisposition.PDF_UNAVAILABLE

    missing = FakeFetcher(
        responses={
            "https://papers.example.org/foundation.pdf": FetchResponse(
                requested_uri="https://papers.example.org/foundation.pdf",
                status_code=302,
                peer_address=PUBLIC_A,
                media_type=None,
                content_length=0,
                location=None,
                body=b"",
            )
        }
    )
    result = FixedLiteratureIO(missing).retrieve(_query())
    assert result.packet.papers[0].retrieval_disposition is RetrievalDisposition.PDF_UNAVAILABLE

    result = FixedLiteratureIO(FakeFetcher(), max_redirects=0).retrieve(_query())
    assert result.packet.papers[0].retrieval_disposition is RetrievalDisposition.PDF_UNAVAILABLE


def test_default_redirect_bound_can_reach_its_final_pdf_hop() -> None:
    chain = tuple(
        f"https://redirect.example.org/hop-{index}.pdf" for index in range(5)
    )
    responses = {
        uri: FetchResponse(
            requested_uri=uri,
            status_code=302,
            peer_address=PUBLIC_A,
            media_type=None,
            content_length=0,
            location=chain[index + 1],
            body=b"",
        )
        for index, uri in enumerate(chain[:-1])
    }
    responses[chain[-1]] = FetchResponse(
        requested_uri=chain[-1],
        status_code=200,
        peer_address=PUBLIC_A,
        media_type="application/pdf",
        content_length=len(PDF_FOUNDATIONAL),
        location=None,
        body=PDF_FOUNDATIONAL,
    )
    foundation = replace(_papers()[-1], pdf_uri=chain[0])
    fetcher = FakeFetcher(
        papers=[*_papers()[:2], foundation],
        resolutions={"redirect.example.org": (PUBLIC_A,)},
        responses=responses,
    )

    result = FixedLiteratureIO(fetcher).retrieve(_query())

    trace = result.packet.papers[0].pdf_trace
    assert trace is not None and trace.final_uri == chain[-1]
    assert [call[0] for call in fetcher.fetch_calls[:5]] == list(chain)


def test_pdf_trace_requires_bounded_pages_abstract_conclusion_and_claims() -> None:
    with pytest.raises(LiteratureValidationError, match="page ref exceeds"):
        PdfExtraction(
            page_count=2,
            abstract_summary="abstract",
            conclusions="conclusion",
            claim_support=(
                ClaimPageRef(
                    claim_ref="claim-outside",
                    section="Results",
                    page_start=2,
                    page_end=3,
                ),
            ),
        )
    with pytest.raises(LiteratureValidationError, match="abstract_summary"):
        PdfExtraction(
            page_count=2,
            abstract_summary="",
            conclusions="conclusion",
            claim_support=(
                ClaimPageRef(
                    claim_ref="claim-1",
                    section="Results",
                    page_start=1,
                    page_end=1,
                ),
            ),
        )
    with pytest.raises(LiteratureValidationError, match="claim_support"):
        PdfExtraction(
            page_count=2,
            abstract_summary="abstract",
            conclusions="conclusion",
            claim_support=(),
        )
    with pytest.raises(LiteratureValidationError, match="page_start"):
        ClaimPageRef(
            claim_ref="claim-1", section="Results", page_start=0, page_end=1
        )


def test_unavailable_pdf_cannot_support_claim_and_artifact_refs_are_exact() -> None:
    result = FixedLiteratureIO(FakeFetcher()).retrieve(_query())
    unavailable = result.packet.papers[1]
    with pytest.raises(LiteratureValidationError, match="cannot carry"):
        replace(unavailable, claim_refs=("fabricated-claim",))

    with pytest.raises(LiteratureValidationError, match="exactly match"):
        LiteratureRetrieval(
            packet=result.packet,
            pdf_artifacts=result.pdf_artifacts
            + (
                RetrievedPdfArtifact(
                    artifact_ref="unreferenced-pdf",
                    media_type="application/pdf",
                    content=PDF_LATEST,
                ),
            ),
        )
    with pytest.raises(LiteratureValidationError, match="exactly match"):
        LiteratureRetrieval(
            packet=result.packet,
            pdf_artifacts=result.pdf_artifacts[:-1],
        )


def test_packet_roundtrip_rejects_unknown_fields_and_tampered_page_refs() -> None:
    result = FixedLiteratureIO(FakeFetcher()).retrieve(_query())
    raw = result.packet.to_dict()
    raw["unexpected"] = True
    with pytest.raises(LiteratureValidationError, match="unknown"):
        LiteraturePacket.from_dict(raw)

    raw = result.packet.to_dict()
    trace = raw["papers"][0]["pdf_trace"]  # type: ignore[index]
    trace["claim_support"][0]["page_end"] = 999  # type: ignore[index]
    with pytest.raises(LiteratureValidationError, match="page ref exceeds"):
        LiteraturePacket.from_dict(raw)


def _openalex_work(
    identifier: int,
    year: int,
    *,
    doi: str | None = None,
    pdf_url: str | None = None,
    is_oa: bool = False,
) -> dict[str, object]:
    return {
        "id": f"https://openalex.org/W{identifier}",
        "doi": doi,
        "display_name": f"OpenAlex work {identifier}",
        "publication_year": year,
        "language": "en",
        "authorships": [
            {"author": {"display_name": f"Researcher {identifier}"}}
        ],
        "type": "article",
        "primary_location": {
            "landing_page_url": f"https://catalog.example.org/W{identifier}",
            "pdf_url": None,
            "is_oa": False,
            "source": {"type": "journal"},
        },
        "best_oa_location": (
            None
            if pdf_url is None
            else {
                "landing_page_url": f"https://catalog.example.org/W{identifier}",
                "pdf_url": pdf_url,
                "is_oa": is_oa,
            }
        ),
        "open_access": {"is_oa": is_oa},
    }


def test_openalex_uses_exact_arxiv_landing_as_a_pdf_candidate() -> None:
    work = _openalex_work(104, 2022)
    work["locations"] = [
        {
            "landing_page_url": "https://arxiv.org/abs/2201.01234v2",
            "pdf_url": None,
            "is_oa": True,
        }
    ]

    assert literature_module._openalex_pdf_uris(work) == (
        "https://arxiv.org/pdf/2201.01234v2",
    )

    work["locations"] = [
        {
            "landing_page_url": "https://example.org/abs/2201.01234v2",
            "pdf_url": None,
            "is_oa": True,
        },
        {
            "landing_page_url": "https://arxiv.org/abs/not-an-arxiv-id",
            "pdf_url": None,
            "is_oa": True,
        },
        {
            "landing_page_url": "https://arxiv.org/abs/2201.01234v2",
            "pdf_url": None,
            "is_oa": False,
        },
    ]
    assert literature_module._openalex_pdf_uris(work) == ()


class RecordingOpenAlexTransport:
    def __init__(
        self,
        *,
        works_by_sort: dict[str, list[dict[str, object]]] | None = None,
        works_by_request: tuple[tuple[dict[str, object], ...], ...] | None = None,
        status_code: int = 200,
        media_type: str = "application/json",
        body_override: bytes | None = None,
    ) -> None:
        self.works_by_sort = works_by_sort or {
            "cited_by_count:desc": [_openalex_work(101, 1995)],
            "relevance_score:desc": [_openalex_work(102, 2008)],
            "publication_date:desc": [_openalex_work(103, 2024)],
        }
        self.works_by_request = works_by_request
        self.status_code = status_code
        self.media_type = media_type
        self.body_override = body_override
        self.calls: list[dict[str, object]] = []

    def get(
        self,
        *,
        host: str,
        target: str,
        headers: dict[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> OpenAlexResponse:
        self.calls.append(
            {
                "host": host,
                "target": target,
                "headers": dict(headers),
                "timeout_seconds": timeout_seconds,
                "max_bytes": max_bytes,
            }
        )
        parameters = parse_qs(urlsplit("https://fixed.example" + target).query)
        body = self.body_override
        if body is None:
            if target.startswith("/sources?"):
                results = ()
            else:
                sort = parameters["sort"][0]
                request_index = len(
                    [call for call in self.calls if str(call["target"]).startswith("/works?")]
                ) - 1
                results = (
                    self.works_by_request[request_index]
                    if self.works_by_request is not None
                    else self.works_by_sort.get(sort, [])
                )
            body = json.dumps({"results": results}).encode()
        return OpenAlexResponse(
            status_code=self.status_code,
            media_type=self.media_type,
            content_length=len(body),
            body=body,
        )


def test_openalex_skips_one_malformed_work_and_returns_transferable_packet() -> None:
    malformed = _openalex_work(100, 1998)
    malformed["authorships"] = []
    transport = RecordingOpenAlexTransport(
        works_by_request=(
            (malformed, _openalex_work(101, 1999)),
            (_openalex_work(102, 2010),),
            (_openalex_work(103, 2024),),
        )
    )
    query = _malformed_work_query()
    retrieval = FixedLiteratureIO(
        OpenAlexLiteratureFetcher(
            api_key="secret",
            transport=transport,
            resolver=lambda hostname: (PUBLIC_A,),
        )
    ).retrieve(query)

    packet = LiteraturePacket.from_bytes(retrieval.packet.to_bytes())
    assert packet == retrieval.packet
    assert [paper.bibliographic_identity for paper in packet.papers] == [
        "openalex:W101",
        "openalex:W102",
        "openalex:W103",
    ]
    assert [paper.stratum for paper in packet.papers] == list(LITERATURE_STRATA)
    assert packet.query == query
    assert len(
        [call for call in transport.calls if str(call["target"]).startswith("/works?")]
    ) == len(LITERATURE_STRATA)


def test_openalex_unresolved_landing_can_retrieve_pdf_from_another_host() -> None:
    unavailable_host = "researchbank.acu.edu.au"
    pdf_uri = "https://pdfs.example.org/W100.pdf"
    unavailable = _openalex_work(
        100,
        1998,
        pdf_url=pdf_uri,
        is_oa=True,
    )
    unavailable["primary_location"] = {
        "landing_page_url": f"https://{unavailable_host}/W100",
        "pdf_url": None,
        "is_oa": False,
        "source": {"type": "journal"},
    }
    transport = RecordingOpenAlexTransport(
        works_by_request=(
            (unavailable, _openalex_work(101, 1999)),
            (_openalex_work(102, 2010),),
            (_openalex_work(103, 2024),),
        )
    )
    resolve_calls: list[str] = []

    def resolve(hostname: str) -> Sequence[str]:
        resolve_calls.append(hostname)
        return () if hostname == unavailable_host else (PUBLIC_A,)

    def fetch_pdf(
        uri: str,
        *,
        approved_addresses: tuple[str, ...],
        max_bytes: int,
    ) -> FetchResponse:
        assert uri == pdf_uri
        assert approved_addresses == (PUBLIC_A,)
        assert max_bytes > len(PDF_FOUNDATIONAL)
        return FetchResponse(
            requested_uri=uri,
            status_code=200,
            peer_address=PUBLIC_A,
            media_type="application/pdf",
            content_length=len(PDF_FOUNDATIONAL),
            location=None,
            body=PDF_FOUNDATIONAL,
        )

    class Extractor:
        def extract_pdf(self, content: bytes, *, max_pages: int) -> PdfExtraction:
            assert content == PDF_FOUNDATIONAL
            assert max_pages > 0
            return _extraction("unresolved-landing")

    query = _malformed_work_query()
    retrieval = FixedLiteratureIO(
        OpenAlexLiteratureFetcher(
            api_key="must-not-be-diagnostic",
            transport=transport,
            resolver=resolve,
            pdf_fetcher=fetch_pdf,
            pdf_extractor=Extractor(),  # type: ignore[arg-type]
        )
    ).retrieve(query)

    packet = LiteraturePacket.from_bytes(retrieval.packet.to_bytes())
    assert [paper.bibliographic_identity for paper in packet.papers] == [
        "openalex:W100",
        "openalex:W101",
        "openalex:W102",
        "openalex:W103",
    ]
    retrieved = packet.papers[0]
    assert retrieved.retrieval_disposition is RetrievalDisposition.RETRIEVED
    assert retrieved.pdf_trace is not None
    assert retrieved.pdf_trace.source_uri == pdf_uri
    assert {paper.stratum for paper in packet.papers} == set(LITERATURE_STRATA)
    assert unavailable_host in resolve_calls


def test_public_candidate_dns_failure_has_safe_locating_diagnostic() -> None:
    unavailable_host = "researchbank.acu.edu.au"
    secret = "must-not-be-diagnostic"

    def resolve(hostname: str) -> Sequence[str]:
        if hostname == unavailable_host:
            raise OSError(secret)
        return (PUBLIC_A,)

    adapter = FixedLiteratureIO(
        OpenAlexLiteratureFetcher(
            api_key=secret,
            transport=RecordingOpenAlexTransport(),
            resolver=resolve,
        )
    )
    with pytest.raises(
        LiteratureFetchError,
        match=rf"landing_uri DNS resolution failed for {unavailable_host}",
    ) as rejected:
        adapter._approve_uri(  # noqa: SLF001
            "https://ResearchBank.ACU.EDU.AU./W100",
            "landing_uri",
            {},
        )

    assert rejected.value.code is LiteratureFailureCode.FETCH_FAILED
    assert secret not in str(rejected.value)


def test_metadata_resolver_safety_rejection_is_not_isolated() -> None:
    rejected_host = "rejected.example.org"
    rejected = _openalex_work(100, 1998)
    rejected["primary_location"] = {
        "landing_page_url": f"https://{rejected_host}/W100",
        "pdf_url": None,
        "is_oa": False,
        "source": {"type": "journal"},
    }
    transport = RecordingOpenAlexTransport(
        works_by_request=(
            (rejected, _openalex_work(101, 1999)),
            (_openalex_work(102, 2010),),
            (_openalex_work(103, 2024),),
        )
    )

    def resolve(hostname: str) -> Sequence[str]:
        if hostname == rejected_host:
            raise LiteratureFetchError("resolver rejected unsafe target")
        return (PUBLIC_A,)

    with pytest.raises(
        LiteratureFetchError,
        match="resolver rejected unsafe target",
    ):
        FixedLiteratureIO(
            OpenAlexLiteratureFetcher(
                api_key="secret",
                transport=transport,
                resolver=resolve,
            )
        ).retrieve(_malformed_work_query())


def test_metadata_resolver_programming_failure_is_not_isolated() -> None:
    rejected_host = "broken-resolver.example.org"
    rejected = _openalex_work(100, 1998)
    rejected["primary_location"] = {
        "landing_page_url": f"https://{rejected_host}/W100",
        "pdf_url": None,
        "is_oa": False,
        "source": {"type": "journal"},
    }
    transport = RecordingOpenAlexTransport(
        works_by_request=(
            (rejected, _openalex_work(101, 1999)),
            (_openalex_work(102, 2010),),
            (_openalex_work(103, 2024),),
        )
    )

    def resolve(hostname: str) -> Sequence[str]:
        if hostname == rejected_host:
            raise RuntimeError("resolver implementation bug")
        return (PUBLIC_A,)

    with pytest.raises(
        LiteratureFetchError,
        match="PDF DNS resolution failed",
    ):
        FixedLiteratureIO(
            OpenAlexLiteratureFetcher(
                api_key="secret",
                transport=transport,
                resolver=resolve,
            )
        ).retrieve(_malformed_work_query())


def test_openalex_all_malformed_works_fail_closed_on_packet_coverage() -> None:
    malformed_works: list[dict[str, object]] = []
    for identifier, year in ((101, 1999), (102, 2010), (103, 2024)):
        work = _openalex_work(identifier, year)
        work["authorships"] = []
        malformed_works.append(work)
    query = _malformed_work_query()
    fetcher = OpenAlexLiteratureFetcher(
        api_key="secret",
        transport=RecordingOpenAlexTransport(
            works_by_request=tuple((work,) for work in malformed_works)
        ),
    )

    with pytest.raises(LiteratureCoverageError) as rejected:
        FixedLiteratureIO(fetcher).retrieve(query)

    assert rejected.value.missing_strata == LITERATURE_STRATA


def test_semantic_scholar_fallback_requires_exact_returned_doi() -> None:
    class Transport:
        def __init__(self, returned_doi: str) -> None:
            self.returned_doi = returned_doi
            self.calls: list[dict[str, object]] = []

        def get(self, **kwargs: object) -> OpenAlexResponse:
            self.calls.append(dict(kwargs))
            body = json.dumps(
                {
                    "externalIds": {"DOI": self.returned_doi},
                    "openAccessPdf": {
                        "url": "https://pdfs.semanticscholar.org/ab/paper.pdf"
                    },
                }
            ).encode()
            return OpenAlexResponse(
                status_code=200,
                media_type="application/json",
                content_length=len(body),
                body=body,
            )

    paper = _paper(
        LiteratureStratum.FOUNDATIONAL,
        "doi:10.1000/foundation",
        year=2004,
    )
    exact = Transport("10.1000/FOUNDATION")
    locator = SemanticScholarPdfLocator(transport=exact)
    assert locator.locate(paper) == (
        "https://pdfs.semanticscholar.org/ab/paper.pdf",
    )
    assert str(exact.calls[0]["target"]).startswith(
        "/graph/v1/paper/DOI%3A10.1000%2Ffoundation?"
    )
    assert exact.calls[0]["headers"] == {
        "Accept": "application/json",
        "User-Agent": "Hypoloom-Hypoloom-literature/2",
    }

    mismatch = Transport("10.1000/different")
    assert SemanticScholarPdfLocator(transport=mismatch).locate(paper) == ()


def test_openalex_exact_host_bearer_bounds_and_existing_bundle_conversion() -> None:
    api_secret = "test-openalex-secret"
    intermediate_work = _openalex_work(
        102,
        2008,
        pdf_url="http://papers.example.org/not-https.pdf",
        is_oa=True,
    )
    intermediate_work["locations"] = [
        {
            "landing_page_url": "https://arxiv.org/abs/0801.01234v3",
            "pdf_url": None,
            "is_oa": True,
        }
    ]
    transport = RecordingOpenAlexTransport(
        works_by_request=(
            (
                _openalex_work(
                    101,
                    1995,
                    doi="https://doi.org/10.1000/FOUNDATION",
                    pdf_url="https://papers.example.org/foundation.pdf",
                    is_oa=True,
                ),
            ),
            (intermediate_work,),
            (_openalex_work(103, 2024),),
        )
    )

    class Extractor:
        def extract_pdf(self, content: bytes, *, max_pages: int) -> PdfExtraction:
            assert content == PDF_FOUNDATIONAL
            assert max_pages == 20
            return _extraction("openalex", page_count=4)

    pdf_calls: list[tuple[str, tuple[str, ...], int]] = []

    def fetch_pdf(
        uri: str,
        *,
        approved_addresses: tuple[str, ...],
        max_bytes: int,
    ) -> FetchResponse:
        pdf_calls.append((uri, approved_addresses, max_bytes))
        return FetchResponse(
            requested_uri=uri,
            status_code=200,
            peer_address=PUBLIC_A,
            media_type="application/pdf",
            content_length=len(PDF_FOUNDATIONAL),
            location=None,
            body=PDF_FOUNDATIONAL,
        )

    fetcher = OpenAlexLiteratureFetcher(
        api_key=api_secret,
        transport=transport,
        resolver=lambda hostname: (PUBLIC_A,),
        pdf_fetcher=fetch_pdf,
        pdf_extractor=Extractor(),  # type: ignore[arg-type]
        timeout_seconds=7.5,
        max_response_bytes=4_096,
    )
    result = FixedLiteratureIO(
        fetcher,
        max_pdf_bytes=1_024,
        max_pdf_pages=20,
    ).retrieve(_query())

    assert [paper.stratum for paper in result.packet.papers] == list(
        LITERATURE_STRATA
    )
    assert [paper.bibliographic_identity for paper in result.packet.papers] == [
        "doi:10.1000/foundation",
        "openalex:W102",
        "openalex:W103",
    ]
    assert result.packet.papers[0].retrieval_disposition is RetrievalDisposition.RETRIEVED
    assert result.packet.papers[1].retrieval_disposition is (
        RetrievalDisposition.RETRIEVED
    )
    assert len(result.pdf_artifacts) == 2
    assert pdf_calls == [
        (
            "https://papers.example.org/foundation.pdf",
            (PUBLIC_A,),
            1_025,
        ),
        (
            "https://arxiv.org/pdf/0801.01234v3",
            (PUBLIC_A,),
            1_025,
        ),
    ]
    assert len(transport.calls) == 3
    for call in transport.calls:
        assert call["host"] == OPENALEX_API_HOST
        assert call["timeout_seconds"] == 7.5
        assert call["max_bytes"] == 4_096
        assert call["headers"] == {
            "Accept": "application/json",
            "Authorization": f"Bearer {api_secret}",
            "User-Agent": "Hypoloom-Hypoloom-literature/2",
        }
        target = str(call["target"])
        assert target.startswith("/works?")
        assert api_secret not in target
        parameters = parse_qs(urlsplit("https://fixed.example" + target).query)
        assert int(parameters["per-page"][0]) <= 2
        assert parameters["filter"][0].count("from_publication_date:") == 1
    assert api_secret not in repr(fetcher)
    assert api_secret not in result.packet.to_bytes().decode()


def test_openalex_venue_preferences_share_each_stratum_with_broad_recall() -> None:
    preferred_source = {
        "id": "https://openalex.org/S111",
        "display_name": "Journal of Machine Learning Research",
        "type": "journal",
        "issn_l": "1532-4435",
        "alternate_titles": ["JMLR"],
        "abbreviated_title": "J Mach Learn Res",
    }

    def work(identifier: int, year: int, source: dict[str, object]) -> dict[str, object]:
        value = _openalex_work(identifier, year)
        value["locations"] = []
        value["primary_location"] = {
            "landing_page_url": f"https://catalog.example.org/W{identifier}",
            "pdf_url": None,
            "is_oa": False,
            "source": source,
        }
        return value

    broad_source = {
        "id": "https://openalex.org/S222",
        "display_name": "Broad Interdisciplinary Journal",
        "type": "journal",
        "issn_l": "1234-567X",
    }

    class VenueTransport:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get(self, *, target: str, **kwargs: object) -> OpenAlexResponse:
            self.calls.append(target)
            parameters = parse_qs(urlsplit("https://fixed.example" + target).query)
            if target.startswith("/sources?"):
                results = [preferred_source]
            else:
                filters = parameters["filter"][0]
                match = re.search(r"from_publication_date:(\d{4})", filters)
                assert match is not None
                year = int(match.group(1))
                first = work(year * 10 + 1, year, preferred_source)
                results = (
                    [first]
                    if "primary_location.source.id:S111" in filters
                    else [work(year * 10 + 2, year, broad_source), first]
                )
            body = json.dumps({"results": results}).encode()
            return OpenAlexResponse(
                status_code=200,
                media_type="application/json",
                content_length=len(body),
                body=body,
            )

    transport = VenueTransport()
    query = replace(
        _query(),
        preferred_venues=(
            PreferredVenue(
                name="Journal of Machine Learning Research",
                source_class=ScholarlySourceClass.JOURNAL,
            ),
        ),
    )
    fetcher = OpenAlexLiteratureFetcher(
        api_key="venue-test-key",
        transport=transport,
        resolver=lambda hostname: (PUBLIC_A,),
        pdf_extractor=PopplerPdfExtractor(
            pdfinfo_path="/bin/true", pdftotext_path="/bin/true"
        ),
    )
    packet = FixedLiteratureIO(fetcher).retrieve(query).packet
    assert len(packet.papers) == 6
    source_ids = [paper.venue.source_id for paper in packet.papers if paper.venue]
    assert source_ids.count("openalex:S111") == 3
    assert source_ids.count("openalex:S222") == 3
    assert len([target for target in transport.calls if target.startswith("/sources?")]) == 1
    assert len([target for target in transport.calls if target.startswith("/works?")]) == 6
    assert packet.query.preferred_venues == query.preferred_venues


def test_openalex_full_preferred_quota_does_not_append_broad_results() -> None:
    preferred_source = {
        "id": "https://openalex.org/S111",
        "display_name": "Journal of Machine Learning Research",
        "type": "journal",
        "issn_l": "1532-4435",
    }
    broad_source = {
        "id": "https://openalex.org/S222",
        "display_name": "Broad Interdisciplinary Journal",
        "type": "journal",
        "issn_l": "1234-567X",
    }

    def work(identifier: int, year: int, source: dict[str, object]) -> dict[str, object]:
        value = _openalex_work(identifier, year)
        value["locations"] = []
        value["primary_location"] = {
            "landing_page_url": f"https://catalog.example.org/W{identifier}",
            "pdf_url": None,
            "is_oa": False,
            "source": source,
        }
        return value

    class VenueTransport:
        def get(self, *, target: str, **kwargs: object) -> OpenAlexResponse:
            parameters = parse_qs(urlsplit("https://fixed.example" + target).query)
            if target.startswith("/sources?"):
                results = [preferred_source]
            else:
                filters = parameters["filter"][0]
                match = re.search(r"from_publication_date:(\d{4})", filters)
                assert match is not None
                year = int(match.group(1))
                results = (
                    [work(year * 10 + 1, year, preferred_source)]
                    if "primary_location.source.id:S111" in filters
                    else [work(year * 10 + 2, year, broad_source)]
                )
            body = json.dumps({"results": results}).encode()
            return OpenAlexResponse(
                status_code=200,
                media_type="application/json",
                content_length=len(body),
                body=body,
            )

    query = replace(
        _query(max_items=3),
        preferred_venues=(
            PreferredVenue(
                name="Journal of Machine Learning Research",
                source_class=ScholarlySourceClass.JOURNAL,
            ),
        ),
    )
    fetcher = OpenAlexLiteratureFetcher(
        api_key="venue-test-key",
        transport=VenueTransport(),
        resolver=lambda hostname: (PUBLIC_A,),
        pdf_extractor=PopplerPdfExtractor(
            pdfinfo_path="/bin/true", pdftotext_path="/bin/true"
        ),
    )

    papers = tuple(fetcher.search(query))

    assert len(papers) == query.max_items
    assert [paper.venue.source_id for paper in papers if paper.venue] == [
        "openalex:S111",
        "openalex:S111",
        "openalex:S111",
    ]


def test_openalex_preferred_lane_failure_keeps_completed_broad_lane() -> None:
    preferred_source = {
        "id": "https://openalex.org/S111",
        "display_name": "Journal of Machine Learning Research",
        "type": "journal",
        "issn_l": "1532-4435",
    }

    class PreferredFailureTransport:
        def __init__(self) -> None:
            self.targets: list[str] = []

        def get(self, *, target: str, **kwargs: object) -> OpenAlexResponse:
            self.targets.append(target)
            if target.startswith("/sources?"):
                results = [preferred_source]
                status = 200
            else:
                parameters = parse_qs(
                    urlsplit("https://fixed.example" + target).query
                )
                filters = parameters["filter"][0]
                if "primary_location.source.id:S111" in filters:
                    results = []
                    status = 503
                else:
                    match = re.search(r"from_publication_date:(\d{4})", filters)
                    assert match is not None
                    results = [_openalex_work(int(match.group(1)), int(match.group(1)))]
                    status = 200
            body = json.dumps({"results": results}).encode()
            return OpenAlexResponse(
                status_code=status,
                media_type="application/json",
                content_length=len(body),
                body=body,
            )

    transport = PreferredFailureTransport()
    query = replace(
        _query(max_items=3),
        preferred_venues=(
            PreferredVenue(
                name="Journal of Machine Learning Research",
                source_class=ScholarlySourceClass.JOURNAL,
            ),
        ),
    )

    papers = tuple(
        OpenAlexLiteratureFetcher(
            api_key="venue-test-key",
            transport=transport,
            sleeper=lambda _delay: None,
        ).search(query)
    )

    assert len(papers) == query.max_items
    preferred_targets = [
        target
        for target in transport.targets
        if "primary_location.source.id%3AS111" in target
    ]
    assert len(preferred_targets) == 3


def test_openalex_short_venue_hint_does_not_match_inside_source_word() -> None:
    source = {
        "id": "https://openalex.org/S333",
        "display_name": "Brain",
        "type": "journal",
        "issn_l": "0006-8993",
    }

    class BoundaryTransport:
        def __init__(self) -> None:
            self.targets: list[str] = []

        def get(self, *, target: str, **kwargs: object) -> OpenAlexResponse:
            self.targets.append(target)
            if target.startswith("/sources?"):
                results = [source]
            else:
                filters = parse_qs(
                    urlsplit("https://fixed.example" + target).query
                )["filter"][0]
                match = re.search(r"from_publication_date:(\d{4})", filters)
                assert match is not None
                year = int(match.group(1))
                results = [_openalex_work(year, year)]
            body = json.dumps({"results": results}).encode()
            return OpenAlexResponse(
                status_code=200,
                media_type="application/json",
                content_length=len(body),
                body=body,
            )

    transport = BoundaryTransport()
    query = replace(
        _query(max_items=3),
        preferred_venues=(
            PreferredVenue(
                name="AI", source_class=ScholarlySourceClass.JOURNAL
            ),
        ),
    )

    papers = tuple(
        OpenAlexLiteratureFetcher(
            api_key="venue-test-key", transport=transport
        ).search(query)
    )

    assert len(papers) == 3
    assert len([target for target in transport.targets if target.startswith("/works?")]) == 3
    assert all("primary_location.source.id" not in target for target in transport.targets)


def test_openalex_broad_lanes_finish_before_optional_venue_deadline() -> None:
    source = {
        "id": "https://openalex.org/S111",
        "display_name": "Journal of Machine Learning Research",
        "type": "journal",
        "issn_l": "1532-4435",
    }

    class Clock:
        def __init__(self) -> None:
            self.value = 0.0

        def __call__(self) -> float:
            return self.value

    clock = Clock()

    class TimedTransport:
        def get(self, *, target: str, **kwargs: object) -> OpenAlexResponse:
            clock.value += float(kwargs["timeout_seconds"])
            if target.startswith("/sources?"):
                results = [source]
            else:
                filters = parse_qs(
                    urlsplit("https://fixed.example" + target).query
                )["filter"][0]
                match = re.search(r"from_publication_date:(\d{4})", filters)
                assert match is not None
                year = int(match.group(1))
                results = [_openalex_work(year, year)]
            body = json.dumps({"results": results}).encode()
            return OpenAlexResponse(
                status_code=200,
                media_type="application/json",
                content_length=len(body),
                body=body,
            )

    query = replace(
        _query(max_items=3),
        preferred_venues=(
            PreferredVenue(
                name="Journal of Machine Learning Research",
                source_class=ScholarlySourceClass.JOURNAL,
            ),
        ),
    )

    papers = tuple(
        OpenAlexLiteratureFetcher(
            api_key="venue-test-key",
            transport=TimedTransport(),
            timeout_seconds=15.0,
            max_search_seconds=60.0,
            clock=clock,
        ).search(query)
    )

    assert len(papers) == 3
    assert {paper.stratum for paper in papers} == set(LITERATURE_STRATA)
    assert clock.value == 60.0


def test_profile_derived_openalex_queries_use_bounded_goal_topic() -> None:
    contract = {
        "goal": {
            "objective": (
                "# Multimodal emotion recognition\n\n"
                "Implement **`multiemo_causal_candidate`** from a very long "
                "Markdown goal without sending its paths or machine terms."
            )
        },
        "components": [
            {
                "id": "primary.weighted_f1",
                "name": "**weighted-F1**",
                "result": {
                    "dataset": "MELD",
                    "setting": "causal_train",
                    "metric": "weighted_f1",
                },
            }
        ],
        "roles": [
            {
                "id": "ml-expert",
                "focus": "Compare **causal** candidates.",
                "autonomy": "evidence_first",
                "budget": {"max_papers": 3},
            }
        ],
        "budget": {"max_papers": 3},
    }
    grounding = grounding_literature_query(
        contract,
        latest_as_of="2026-08-29",
        immutable_context_refs=("goal-1",),
    )
    expert = expert_literature_query(
        contract,
        component_id="primary.weighted_f1",
        role="ml-expert",
        latest_as_of="2026-08-29",
        immutable_context_refs=("grounding-1",),
    )

    assert grounding.query == "Multimodal emotion recognition"
    assert expert.query == grounding.query
    assert grounding.filters.year_from == 1997
    assert expert.filters.year_from == 1997
    assert grounding.filters.year_to == 2026
    assert expert.filters.year_to == 2026
    assert "multiemo_causal_candidate" not in grounding.query

    transport = RecordingOpenAlexTransport()
    fetcher = OpenAlexLiteratureFetcher(api_key="secret", transport=transport)
    tuple(fetcher.search(grounding))
    tuple(fetcher.search(expert))
    assert len(transport.calls) == 6
    expected_ranges = (("1997-01-01", "2006-12-31"),
                       ("2007-01-01", "2016-12-31"),
                       ("2017-01-01", "2026-08-29"))
    for index, call in enumerate(transport.calls):
        parameters = parse_qs(
            urlsplit("https://fixed.example" + str(call["target"])).query
        )
        assert parameters["search"] == [grounding.query]
        assert parameters["sort"] == ["relevance_score:desc"]
        date_filter = parameters["filter"][0]
        start, end = expected_ranges[index % 3]
        assert f"from_publication_date:{start}" in date_filter
        assert f"to_publication_date:{end}" in date_filter


def test_openalex_key_file_is_explicit_regular_and_never_ambient(
    tmp_path: Path,
) -> None:
    secret = "file-openalex-secret"
    key_path = tmp_path / "openalex.key"
    key_path.write_text(secret + "\n", encoding="utf-8")
    key_path.chmod(0o600)
    transport = RecordingOpenAlexTransport()
    fetcher = OpenAlexLiteratureFetcher(
        api_key_file=key_path,
        transport=transport,
        resolver=lambda hostname: (PUBLIC_A,),
    )
    tuple(fetcher.search(_query()))
    assert transport.calls[0]["headers"]["Authorization"] == f"Bearer {secret}"  # type: ignore[index]
    assert secret not in repr(fetcher)

    with pytest.raises(ValueError, match="exactly one"):
        OpenAlexLiteratureFetcher()
    with pytest.raises(ValueError, match="exactly one"):
        OpenAlexLiteratureFetcher(api_key=secret, api_key_file=key_path)
    link_path = tmp_path / "linked.key"
    link_path.symlink_to(key_path)
    with pytest.raises(LiteratureFetchError) as rejected:
        OpenAlexLiteratureFetcher(api_key_file=link_path)
    assert rejected.value.code is LiteratureFailureCode.OPENALEX_AUTH
    assert secret not in str(rejected.value)


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o660, 0o400, 0o700])
def test_openalex_key_file_requires_documented_0600_mode(tmp_path, mode):
    path = tmp_path / "openalex.key"
    path.write_text("fixture-secret\n")
    path.chmod(mode)
    transport = RecordingOpenAlexTransport()
    with pytest.raises(LiteratureFetchError) as rejected:
        OpenAlexLiteratureFetcher(api_key_file=path, transport=transport)
    assert rejected.value.code is LiteratureFailureCode.OPENALEX_AUTH
    assert "fixture-secret" not in str(rejected.value)
    assert transport.calls == []


def test_openalex_factory_preflight_checks_key_and_poppler_without_network(
    tmp_path: Path,
) -> None:
    key_path = tmp_path / "openalex.key"
    key_path.write_text("factory-secret\n", encoding="utf-8")
    key_path.chmod(0o600)
    pdfinfo = tmp_path / "pdfinfo"
    pdftotext = tmp_path / "pdftotext"
    for tool in (pdfinfo, pdftotext):
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tool.chmod(0o700)

    class NoNetworkTransport:
        def get(self, **kwargs: object) -> OpenAlexResponse:
            raise AssertionError("preflight must not make an OpenAlex request")

    def no_process(*args: object, **kwargs: object) -> object:
        raise AssertionError("preflight must not launch Poppler")

    adapter = build_openalex_literature_io(
        api_key_file=key_path,
        pdfinfo_path=pdfinfo,
        pdftotext_path=pdftotext,
        transport=NoNetworkTransport(),
        runner=no_process,
        timeout_seconds=4.0,
        max_response_bytes=4_096,
        max_pdf_bytes=8_192,
        max_pdf_pages=12,
    )

    assert adapter.preflight() == {
        "adapter": "openalex",
        "host": OPENALEX_API_HOST,
        "credential_source": "file",
        "timeout_seconds": 4.0,
        "search_wall_seconds": 60.0,
        "max_response_bytes": 4_096,
        "max_retries": 2,
        "retry_backoff_seconds": 0.25,
        "poppler": {
            "pdfinfo": str(pdfinfo),
            "pdftotext": str(pdftotext),
            "timeout_seconds": 4.0,
            "max_pdf_bytes": 8_192,
            "max_text_bytes": 2 * 1024 * 1024,
        },
        "network_probe_performed": False,
        "pdf_fallback": {
            "adapter": "semantic-scholar-doi",
            "host": "api.semanticscholar.org",
            "timeout_seconds": 4.0,
            "max_response_bytes": 64 * 1024,
            "credential_source": "none",
            "network_probe_performed": False,
        },
    }

    pdftotext.chmod(0o600)
    with pytest.raises(LiteratureValidationError) as rejected:
        adapter.preflight()
    assert rejected.value.code is LiteratureFailureCode.POPPLER_FAILED


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (400, LiteratureFailureCode.QUERY_INVALID),
        (401, LiteratureFailureCode.OPENALEX_AUTH),
        (403, LiteratureFailureCode.OPENALEX_AUTH),
        (404, LiteratureFailureCode.OPENALEX_HTTP),
        (408, LiteratureFailureCode.OPENALEX_HTTP),
        (429, LiteratureFailureCode.OPENALEX_RATE_LIMIT),
        (503, LiteratureFailureCode.OPENALEX_HTTP),
    ],
)
def test_openalex_http_failures_have_stable_secret_free_classification(
    status_code: int,
    expected: LiteratureFailureCode,
) -> None:
    secret = "never-echo-this-secret"
    sleeps: list[float] = []
    transport = RecordingOpenAlexTransport(
        status_code=status_code,
        body_override=(secret + " in upstream response").encode(),
    )
    fetcher = OpenAlexLiteratureFetcher(
        api_key=secret,
        transport=transport,
        sleeper=sleeps.append,
    )
    with pytest.raises(LiteratureFetchError) as rejected:
        tuple(fetcher.search(_query()))
    assert rejected.value.code is expected
    assert rejected.value.http_status == status_code
    expected_calls = 3 if status_code in {408, 429, 503} else 1
    assert rejected.value.attempt_count == expected_calls
    assert len(transport.calls) == expected_calls
    assert sleeps == ([0.25, 0.5] if expected_calls == 3 else [])
    assert secret not in str(rejected.value)


def test_openalex_transient_statuses_retry_then_continue_search() -> None:
    sleeps: list[float] = []
    successful = RecordingOpenAlexTransport()

    class TransientTransport:
        def __init__(self) -> None:
            self.statuses = [503, 429]
            self.calls = 0

        def get(self, **kwargs: object) -> OpenAlexResponse:
            self.calls += 1
            if self.statuses:
                status = self.statuses.pop(0)
                return OpenAlexResponse(
                    status_code=status,
                    media_type="application/json",
                    content_length=0,
                    body=b"",
                )
            return successful.get(**kwargs)  # type: ignore[arg-type]

    transport = TransientTransport()
    fetcher = OpenAlexLiteratureFetcher(
        api_key="secret",
        transport=transport,
        sleeper=sleeps.append,
    )
    papers = tuple(fetcher.search(_query()))

    assert len(papers) == 3
    assert transport.calls == 5
    assert len(successful.calls) == 3
    assert sleeps == [0.25, 0.5]
    assert fetcher.last_retry_trace == (
        {
            "retry_ordinal": 1,
            "endpoint": "works",
            "request_attempt_ordinal": 1,
            "failure_code": "OPENALEX_HTTP",
            "http_status": 503,
            "retry_after_seconds": 0.25,
        },
        {
            "retry_ordinal": 2,
            "endpoint": "works",
            "request_attempt_ordinal": 2,
            "failure_code": "OPENALEX_RATE_LIMIT",
            "http_status": 429,
            "retry_after_seconds": 0.5,
        },
    )
    assert FixedLiteratureIO(fetcher).retry_trace == fetcher.last_retry_trace


def test_openalex_transient_retry_then_invalid_response_stops_with_trace() -> None:
    calls = 0

    class TransientThenMalformed:
        def get(self, **kwargs: object) -> OpenAlexResponse:
            nonlocal calls
            calls += 1
            if calls == 1:
                return OpenAlexResponse(
                    status_code=503, media_type="application/json",
                    content_length=0, body=b"",
                )
            return OpenAlexResponse(
                status_code=200, media_type="application/json",
                content_length=1, body=b"{",
            )

    sleeps: list[float] = []
    fetcher = OpenAlexLiteratureFetcher(
        api_key="secret",
        transport=TransientThenMalformed(),
        sleeper=sleeps.append,
    )
    with pytest.raises(LiteratureFetchError) as rejected:
        tuple(fetcher.search(_query()))

    assert rejected.value.code is LiteratureFailureCode.OPENALEX_RESPONSE_INVALID
    assert rejected.value.attempt_count == 2
    assert calls == 2
    assert sleeps == [0.25]
    assert [item["failure_code"] for item in fetcher.last_retry_trace] == [
        "OPENALEX_HTTP",
    ]


@pytest.mark.parametrize("failure", [TimeoutError(), OSError("connection reset")])
def test_openalex_transient_transport_failure_retries_then_succeeds(
    failure: Exception,
) -> None:
    sleeps: list[float] = []
    successful = RecordingOpenAlexTransport()

    class TransientTransport:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, **kwargs: object) -> OpenAlexResponse:
            self.calls += 1
            if self.calls == 1:
                raise failure
            return successful.get(**kwargs)  # type: ignore[arg-type]

    transport = TransientTransport()
    papers = tuple(
        OpenAlexLiteratureFetcher(
            api_key="secret",
            transport=transport,
            sleeper=sleeps.append,
        ).search(_query())
    )

    assert len(papers) == 3
    assert transport.calls == 4
    assert sleeps == [0.25]


def test_openalex_retry_exhaustion_preserves_safe_terminal_evidence() -> None:
    secret = "must-not-be-diagnostic"
    sleeps: list[float] = []
    transport = RecordingOpenAlexTransport(
        status_code=503,
        body_override=(secret + " upstream body").encode(),
    )
    fetcher = OpenAlexLiteratureFetcher(
        api_key=secret,
        transport=transport,
        sleeper=sleeps.append,
    )

    with pytest.raises(LiteratureFetchError) as rejected:
        tuple(fetcher.search(_query()))

    assert rejected.value.code is LiteratureFailureCode.OPENALEX_HTTP
    assert rejected.value.http_status == 503
    assert rejected.value.attempt_count == 3
    assert len(transport.calls) == 3
    assert sleeps == [0.25, 0.5]
    assert secret not in str(rejected.value)


def test_openalex_wall_bound_stops_retry_and_preserves_attempt_count() -> None:
    class Clock:
        def __init__(self) -> None:
            self.value = 0.0

        def __call__(self) -> float:
            return self.value

    clock = Clock()
    calls: list[float] = []

    class TimeoutTransport:
        def get(self, **kwargs: object) -> OpenAlexResponse:
            timeout = float(kwargs["timeout_seconds"])
            calls.append(timeout)
            clock.value += timeout
            raise TimeoutError

    fetcher = OpenAlexLiteratureFetcher(
        api_key="secret",
        transport=TimeoutTransport(),
        timeout_seconds=1.0,
        max_search_seconds=1.0,
        retry_backoff_seconds=0,
        clock=clock,
        sleeper=lambda _delay: pytest.fail("zero backoff must not sleep"),
    )

    with pytest.raises(LiteratureFetchError) as rejected:
        tuple(fetcher.search(_query()))

    assert rejected.value.code is LiteratureFailureCode.OPENALEX_TIMEOUT
    assert rejected.value.attempt_count == 1
    assert calls == [1.0]


def test_openalex_retry_timeout_never_exceeds_remaining_search_window() -> None:
    class Clock:
        def __init__(self) -> None:
            self.value = 0.0

        def __call__(self) -> float:
            return self.value

    clock = Clock()
    timeouts: list[float] = []

    class NearDeadlineTransport:
        def get(self, **kwargs: object) -> OpenAlexResponse:
            timeout = float(kwargs["timeout_seconds"])
            timeouts.append(timeout)
            if len(timeouts) == 1:
                clock.value += 0.95
            else:
                clock.value += timeout
            raise TimeoutError

    fetcher = OpenAlexLiteratureFetcher(
        api_key="secret",
        transport=NearDeadlineTransport(),
        timeout_seconds=1.0,
        max_search_seconds=1.0,
        retry_backoff_seconds=0,
        clock=clock,
        sleeper=lambda _delay: None,
    )

    with pytest.raises(LiteratureFetchError):
        tuple(fetcher.search(_query()))

    assert timeouts[0] == 1.0
    assert timeouts[1] == pytest.approx(0.05)


def test_openalex_response_bytes_json_and_result_count_are_bounded() -> None:
    oversized_transport = RecordingOpenAlexTransport(body_override=b"x" * 1_025)
    oversized = OpenAlexLiteratureFetcher(
        api_key="secret",
        transport=oversized_transport,
        max_response_bytes=1_024,
    )
    with pytest.raises(LiteratureFetchError) as rejected:
        tuple(oversized.search(_query()))
    assert rejected.value.code is LiteratureFailureCode.OPENALEX_RESPONSE_LIMIT
    assert rejected.value.attempt_count == 1
    assert len(oversized_transport.calls) == 1

    invalid_transport = RecordingOpenAlexTransport(body_override=b"not-json")
    invalid = OpenAlexLiteratureFetcher(
        api_key="secret",
        transport=invalid_transport,
    )
    with pytest.raises(LiteratureFetchError) as rejected:
        tuple(invalid.search(_query()))
    assert rejected.value.code is LiteratureFailureCode.OPENALEX_RESPONSE_INVALID
    assert rejected.value.attempt_count == 1
    assert len(invalid_transport.calls) == 1

    too_many = RecordingOpenAlexTransport(
        works_by_sort={
            "relevance_score:desc": [
                _openalex_work(201, 1995),
                _openalex_work(202, 1996),
            ]
        }
    )
    fetcher = OpenAlexLiteratureFetcher(api_key="secret", transport=too_many)
    with pytest.raises(LiteratureFetchError) as rejected:
        tuple(fetcher.search(_query(max_items=3)))
    assert rejected.value.code is LiteratureFailureCode.OPENALEX_RESPONSE_LIMIT


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"max_retries": True}, "max_retries"),
        ({"max_retries": -1}, "max_retries"),
        ({"max_retries": 11}, "max_retries"),
        ({"retry_backoff_seconds": -0.1}, "retry_backoff_seconds"),
        ({"retry_backoff_seconds": 5.1}, "retry_backoff_seconds"),
        ({"sleeper": None}, "sleeper"),
    ],
)
def test_openalex_retry_configuration_is_bounded(
    changes: dict[str, object],
    match: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=match):
        OpenAlexLiteratureFetcher(
            api_key="secret",
            transport=RecordingOpenAlexTransport(),
            **changes,
        )


def test_openalex_transport_timeout_is_stably_classified() -> None:
    class TimeoutTransport:
        def get(self, **kwargs: object) -> OpenAlexResponse:
            raise TimeoutError

    fetcher = OpenAlexLiteratureFetcher(
        api_key="secret",
        transport=TimeoutTransport(),
    )
    with pytest.raises(LiteratureFetchError) as rejected:
        tuple(fetcher.search(_query()))
    assert rejected.value.code is LiteratureFailureCode.OPENALEX_TIMEOUT


def test_poppler_uses_injected_tools_timeout_and_bounded_section_pages() -> None:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    text = (
        "Abstract\n"
        "This abstract excerpt explains the bounded scientific contribution.\n"
        "Introduction\nOther text.\f"
        "Methods\nIntermediate body.\f"
        "7 Conclusion\n"
        "This conclusion excerpt reports the bounded experimental outcome.\n"
        "References\nCitation.\f"
    ).encode()

    def runner(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((command, kwargs))
        output = b"Pages: 3\n" if command[0] == "/tools/pdfinfo" else text
        return subprocess.CompletedProcess(command, 0, output, b"")

    extractor = PopplerPdfExtractor(
        pdfinfo_path="/tools/pdfinfo",
        pdftotext_path="/tools/pdftotext",
        runner=runner,
        timeout_seconds=3.5,
        max_text_bytes=2_048,
    )
    result = extractor.extract_pdf(PDF_FOUNDATIONAL, max_pages=4)

    assert result.page_count == 3
    assert result.abstract_summary.startswith("This abstract excerpt")
    assert result.conclusions.startswith("This conclusion excerpt")
    assert [(item.section, item.page_start) for item in result.claim_support] == [
        ("Abstract", 1),
        ("Conclusions", 3),
    ]
    assert [call[0][0] for call in calls] == [
        "/tools/pdfinfo",
        "/tools/pdftotext",
    ]
    assert all(call[1]["input"] == PDF_FOUNDATIONAL for call in calls)
    assert all(call[1]["timeout"] == 3.5 for call in calls)
    assert "-l" in calls[1][0]
    assert calls[1][0][calls[1][0].index("-l") + 1] == "3"


def test_poppler_reads_spaced_small_caps_conclusion_heading() -> None:
    text = (
        "Abstract\n"
        "This abstract describes multimodal emotion recognition evidence.\n"
        "Introduction\nOther text.\f"
        "VI. C ONCLUSION\n"
        "This conclusion reports the bounded experimental result.\n"
        "R EFERENCES\nCitation that must not enter the conclusion.\f"
    ).encode()
    calls = 0

    def runner(
        command: tuple[str, ...], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal calls
        calls += 1
        output = b"Pages: 2\n" if calls == 1 else text
        return subprocess.CompletedProcess(command, 0, output, b"")

    result = PopplerPdfExtractor(runner=runner).extract_pdf(
        PDF_FOUNDATIONAL,
        max_pages=2,
    )
    assert result.conclusions == (
        "This conclusion reports the bounded experimental result."
    )


@pytest.mark.parametrize(("abstract_heading", "conclusion_heading"), [
    ("AbstractÐ", "6\n\nCONCLUSION"),
    ("Abstract ", "9 Discussions and conclusions"),
])
def test_poppler_recognizes_observed_ieee_separator_and_plural_heading(
    abstract_heading, conclusion_heading,
) -> None:
    text = (abstract_heading + "A bounded synthetic abstract describes the study.\n"
            "Index TermsÐSynthetic keywords.\n"
            "1 Introduction\nUnrelated introduction.\f" + conclusion_heading + "\n"
            "A bounded synthetic conclusion reports the result.\nReferences\nCitation.\f")

    def runner(command, **kwargs):
        output = b"Pages: 2\n" if "pdfinfo" in command[0] else text.encode()
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr=b"")

    extraction = PopplerPdfExtractor(runner=runner).extract_pdf(PDF_LATEST, max_pages=2)
    assert extraction.abstract_summary == "A bounded synthetic abstract describes the study."
    assert extraction.conclusions == "A bounded synthetic conclusion reports the result."
    assert [(item.page_start, item.page_end) for item in extraction.claim_support] == [(1, 1), (2, 2)]


def test_poppler_normalizes_extracted_pdf_control_characters() -> None:
    text = (
        "Abstract\n"
        "This abstract describes bounded evidence with an embedded\x01 marker.\n"
        "Introduction\nOther text.\f"
        "Conclusion\n"
        "This conclusion reports a bounded result with a stray\x7f marker.\n"
        "References\nCitation.\f"
    ).encode()
    calls = 0

    def runner(
        command: tuple[str, ...], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal calls
        calls += 1
        output = b"Pages: 2\n" if calls == 1 else text
        return subprocess.CompletedProcess(command, 0, output, b"")

    result = PopplerPdfExtractor(runner=runner).extract_pdf(
        PDF_FOUNDATIONAL,
        max_pages=2,
    )

    assert result.abstract_summary == (
        "This abstract describes bounded evidence with an embedded marker."
    )
    assert result.conclusions == (
        "This conclusion reports a bounded result with a stray marker."
    )


def test_poppler_does_not_treat_title_case_prose_as_roman_section_prefix() -> None:
    text = (
        "Abstract\n"
        "This abstract describes the bounded evidence used by the paper.\n"
        "Introduction\n"
        "Civil conclusions about the survey are tentative and appear here "
        "as ordinary prose, not a heading.\n"
        "References\nCitation.\f"
    ).encode()
    calls = 0

    def runner(
        command: tuple[str, ...], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal calls
        calls += 1
        output = b"Pages: 1\n" if calls == 1 else text
        return subprocess.CompletedProcess(command, 0, output, b"")

    with pytest.raises(LiteratureValidationError) as rejected:
        PopplerPdfExtractor(runner=runner).extract_pdf(
            PDF_FOUNDATIONAL,
            max_pages=1,
        )
    assert rejected.value.code is LiteratureFailureCode.POPPLER_INVALID


def test_poppler_timeout_page_and_output_failures_are_stably_classified() -> None:
    def timeout_runner(
        command: tuple[str, ...], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(command, 1)

    with pytest.raises(LiteratureValidationError) as rejected:
        PopplerPdfExtractor(runner=timeout_runner).extract_pdf(
            PDF_FOUNDATIONAL,
            max_pages=4,
        )
    assert rejected.value.code is LiteratureFailureCode.POPPLER_TIMEOUT

    def too_many_pages(
        command: tuple[str, ...], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 0, b"Pages: 9\n", b"")

    with pytest.raises(LiteratureValidationError) as rejected:
        PopplerPdfExtractor(runner=too_many_pages).extract_pdf(
            PDF_FOUNDATIONAL,
            max_pages=4,
        )
    assert rejected.value.code is LiteratureFailureCode.POPPLER_OUTPUT_LIMIT

    calls = 0

    def oversized_text(
        command: tuple[str, ...], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal calls
        calls += 1
        output = b"Pages: 1\n" if calls == 1 else b"x" * 33
        return subprocess.CompletedProcess(command, 0, output, b"")

    with pytest.raises(LiteratureValidationError) as rejected:
        PopplerPdfExtractor(
            runner=oversized_text,
            max_text_bytes=32,
        ).extract_pdf(PDF_FOUNDATIONAL, max_pages=4)
    assert rejected.value.code is LiteratureFailureCode.POPPLER_OUTPUT_LIMIT


def test_adapter_has_fixed_outward_io_but_no_persistence_or_ambient_auth() -> None:
    source = inspect.getsource(literature_module)
    for forbidden in (
        "urllib.request",
        "requests",
        "os.environ",
        "getenv(",
        ".netrc",
        "Cookie",
        "state.sqlite",
        "StateStore",
        "ArtifactPublisher",
        "hashlib",
    ):
        assert forbidden not in source
    assert f'OPENALEX_API_HOST = "{OPENALEX_API_HOST}"' in source
    assert not hasattr(literature_module, "main")
    assert Path(literature_module.__file__).name == "literature.py"
