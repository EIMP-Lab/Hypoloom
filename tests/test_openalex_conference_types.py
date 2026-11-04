"""Provider taxonomy compatibility through stratified OpenAlex retrieval."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from hypoloom.literature import (
    LITERATURE_STRATA,
    LiteratureFilters,
    LiteratureQuery,
    OpenAlexLiteratureFetcher,
    OpenAlexResponse,
    ScholarlySourceClass,
)


class ConferenceTransport:
    def __init__(self, work_type):
        self.work_type = work_type
        self.calls = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        parameters = parse_qs(urlsplit(kwargs["target"]).query)
        filters = dict(part.split(":", 1) for part in parameters["filter"][0].split(","))
        # Reproduce the provider's rejection of the Crossref-only request label.
        if "proceedings-article" in filters["type"].split("|"):
            status = 400
            payload = {"error": "Invalid query parameters error.",
                       "message": "Use type:conference-paper."}
        else:
            status = 200
            year = int(filters["from_publication_date"][:4])
            payload = {"results": [{
                "id": f"https://openalex.org/W{year}",
                "doi": None,
                "display_name": f"Conference paper {year}",
                "publication_year": year,
                "language": "en",
                "authorships": [{"author": {"display_name": "Ada Researcher"}}],
                "type": self.work_type,
                "primary_location": {
                    "landing_page_url": f"https://example.org/paper/{year}",
                    "pdf_url": None,
                    "is_oa": False,
                    "source": {"type": "conference"},
                },
                "best_oa_location": None,
                "locations": [],
                "open_access": {"is_oa": False},
            }]}
        body = json.dumps(payload).encode()
        return OpenAlexResponse(status_code=status, media_type="application/json",
                                content_length=len(body), body=body)


@pytest.mark.parametrize("work_type", ["conference-paper", "proceedings-article", "article"])
@pytest.mark.parametrize("source_classes", [
    (ScholarlySourceClass.CONFERENCE,),
    tuple(ScholarlySourceClass),
], ids=["conference-only", "mixed-sources"])
def test_conference_search_uses_openalex_types_and_keeps_all_strata(work_type, source_classes):
    query = LiteratureQuery(
        schema_version=2,
        query="causal learning",
        filters=LiteratureFilters(year_from=1997, year_to=2026, languages=("en",)),
        max_items=3,
        allowed_source_classes=source_classes,
        strata=LITERATURE_STRATA,
        latest_as_of="2026-11-04",
        immutable_context_refs=(),
    )
    transport = ConferenceTransport(work_type)
    fetcher = OpenAlexLiteratureFetcher(api_key="test-openalex-key", transport=transport)

    papers = tuple(fetcher.search(query))

    assert [paper.stratum for paper in papers] == list(LITERATURE_STRATA)
    assert [paper.year for paper in papers] == [1997, 2007, 2017]
    assert all(paper.source_class is ScholarlySourceClass.CONFERENCE for paper in papers)
    assert len({paper.bibliographic_identity for paper in papers}) == 3
    assert len(transport.calls) == 3
    for call in transport.calls:
        assert call["host"] == "api.openalex.org"
        filters = parse_qs(urlsplit(call["target"]).query)["filter"][0].split(",")
        types = next(value.removeprefix("type:") for value in filters if value.startswith("type:"))
        assert "conference-paper" in types.split("|")
        assert "proceedings-article" not in types.split("|")
