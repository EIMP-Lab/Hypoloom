from __future__ import annotations

import re
from pathlib import Path

import yaml


REPOSITORY = Path(__file__).resolve().parents[2]
SKILL_DIR = (
    REPOSITORY / "hypoloom" / "skills" / "openalex-query-planning"
)
SKILL = SKILL_DIR / "SKILL.md"


def _document() -> tuple[dict[str, object], str]:
    text = SKILL.read_text(encoding="utf-8")
    match = re.fullmatch(r"---\n(.*?)\n---\n(.*)", text, flags=re.DOTALL)
    assert match is not None
    frontmatter = yaml.safe_load(match.group(1))
    assert isinstance(frontmatter, dict)
    return frontmatter, match.group(2)


def test_query_planning_skill_is_one_small_packaged_document() -> None:
    frontmatter, body = _document()

    assert frontmatter["name"] == "openalex-query-planning"
    assert "OpenAlex LiteratureQuery/v2" in str(frontmatter["description"])
    assert {path.name for path in SKILL_DIR.iterdir()} == {"SKILL.md"}
    assert len(body.splitlines()) < 40
    assert not (REPOSITORY / ".claude" / "skills" / "openalex-query-planning").exists()


def test_query_planning_skill_freezes_policy_and_separates_main_review() -> None:
    _, body = _document()
    normalized = " ".join(body.split())

    for context in (
        "frozen goal", "grounding result", "executing role",
        "expert background", "focus", "component",
    ):
        assert context in normalized
    assert "Return one mutable proposal containing `query`" in normalized
    assert "optional `preferred_venues`" in normalized
    assert "omission means no venue suggestion" in normalized
    assert "Host combines those fields with the frozen template" in normalized
    assert "do not repeat or change" in normalized
    assert "`--candidate-validation-error`" in normalized
    assert "names missing time strata" in normalized
    assert "materially broader query" in normalized
    assert "do not resubmit the same query" in normalized
    assert "uppercase `AND`, `OR`, and `NOT`" in normalized
    assert "wildcards" in normalized and "fuzzy-search" in normalized
    assert "filesystem or URL paths" in normalized and "Markdown syntax" in normalized
    assert "Do not force a recent dataset" in normalized
    assert "one to three discriminative concept groups" in normalized
    assert "flat bag of loosely related generic terms" in normalized
    assert "field-specific anchor" in normalized
    assert "For an initial plan" in normalized
    assert "stable scholarly terms" in normalized
    assert "recognizably about the scoped goal or component" in normalized
    assert "at most six entries" in normalized
    assert "`JOURNAL` or `CONFERENCE`" in normalized
    assert "omit an uncertain venue instead of guessing" in normalized
    assert "retrieval preferences" in normalized
    assert "not an authoritative venue ranking" in normalized
    assert "broad retrieval lane" in normalized
    assert "For a `MAIN` literature task" in normalized
    assert "Host-supplied validated `LiteraturePacket`" in normalized


def test_query_planning_skill_prioritizes_early_stratum_recovery() -> None:
    _, body = _document()
    normalized = " ".join(body.split()).casefold()

    assert "field-anchor broadening" in normalized
    assert "missing foundational or intermediate" in normalized
    assert "takes precedence" in normalized
    assert "parent or adjacent field terms" in normalized
    assert "need not be exact synonyms" in normalized
    assert "recognizably relevant to the scoped goal or component" in normalized
    assert "remove and-attached modern task, population, modality, and mechanism" in normalized
    assert "add one back only when it is itself stable through that window" in normalized
