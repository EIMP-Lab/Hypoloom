"""Standalone acquisition experiment; no init, model calls or research state writes.

Uses Hypoloom's public transport, OA candidate parser, DOI locator, optional CDP
adapter and document validator. Its report is an observation, not run admission.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from time import monotonic
from urllib.parse import urlencode

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hypoloom.document_fallback import acquire_public_pdf, error_event, public_url, read_json
from hypoloom.grounding_web import GroundingWeb, GroundingWebError
from hypoloom.institutional_access import (
    MAX_DOCUMENT_BYTES, PREPARATION_SAMPLES, pdf_identity, publisher, validate_document,
)
from hypoloom.literature import (
    LiteratureError, SemanticScholarPdfLocator, _identity, _uri_parts,
)


def acquire(doi, output, *, web, locator, seconds=45, deadline=None, supplied_url=None, browser=None):
    identity = _identity("doi:" + doi)
    started = monotonic()
    deadline = min(started + seconds, deadline) if deadline is not None else started + seconds
    calls, events = [12], []
    row = {"doi": identity[4:], "provider": publisher("https://doi.org/" + identity[4:]),
           "state": "unavailable", "events": events}
    document, title = acquire_public_pdf(web, identity, deadline=deadline, http_calls=calls,
        events=events, supplied_urls=([supplied_url] if supplied_url else ()),
        locate=lambda remaining: locator.locate_doi(identity[4:], timeout_seconds=remaining))
    row["title"] = title

    def accept(document, source):
        if document.get("media_type") != "application/pdf":
            events.append({"stage": source, "reason": "not_pdf", "media_type": document.get("media_type")})
            return False
        verified = validate_document(document, identity=identity, title=title)
        if monotonic() >= deadline:
            raise GroundingWebError("timed_out", "acquisition window expired")
        output.write_bytes(verified["content"])
        row.update(state="pdf_verified", source=source, url=public_url(verified["url"]),
                   file=output.name, bytes=len(verified["content"]), page_count=len(verified["pages"]),
                   identity_basis="doi" if pdf_identity(verified["pages"]) else "title",
                   media_type="application/pdf",
                   version_note=document.get("version_note",
                       "PDF from the recorded source URL; publisher version of record is not established."))
        return True

    if document is not None:
        source = document["acquisition_source"]
        accept(document, "semantic_scholar" if source == "fallback" else source)
    if row["state"] != "pdf_verified" and browser is not None and monotonic() < deadline:
        result = browser.fetch("https://doi.org/" + identity[4:], timeout_seconds=deadline - monotonic())
        if result.get("state") == "available":
            try:
                accept(result["document"], "browser")
            except (GroundingWebError, LiteratureError, ValueError) as exc:
                events.append(error_event("browser", exc))
        else:
            events.append({"stage": "browser", "reason": result.get("reason") or result.get("state")})
    if row["state"] != "pdf_verified":
        row["reason"] = "deadline" if monotonic() >= deadline else "no_verified_pdf"
    row.update(seconds=round(monotonic() - started, 3), public_http_budget_used=12 - calls[0])
    return row


def search(web, query, count, deadline):
    target = "https://api.openalex.org/works?" + urlencode({"search": query, "per-page": count,
        "select": "doi", "filter": "has_doi:true"})
    payload = read_json(web, target, deadline, [4])
    works = payload.get("results")
    if not isinstance(works, list) or len(works) > count:
        raise ValueError("invalid_search_results")
    if not all(isinstance(work, dict) for work in works):
        raise ValueError("invalid_search_results")
    return list(dict.fromkeys(_identity("doi:" + str(work.get("doi", "")))[4:] for work in works))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--samples", action="store_true", help="use the existing four publisher sample DOIs")
    inputs.add_argument("--doi", action="append", help="repeat for up to ten DOI selections")
    inputs.add_argument("--query", help="public OpenAlex keyword search; no publisher API key")
    parser.add_argument("--url", help="one explicit candidate for a single DOI; reported separately from discovery")
    parser.add_argument("--output", type=Path, required=True, help="new experiment directory; never overwrite an existing one")
    parser.add_argument("--max-items", type=int, default=4, help="1..10 results for --query")
    parser.add_argument("--repeat", type=int, default=1, help="1..3 independent acquisition rounds")
    parser.add_argument("--seconds-per-paper", type=float, default=45)
    parser.add_argument("--total-seconds", type=float, default=300)
    parser.add_argument("--browser-endpoint", help="optional loopback CDP endpoint; opens owned tabs without prompting or solving challenges")
    args = parser.parse_args(argv)
    if args.query is not None and (not args.query.strip() or len(args.query) > 2000 or "\x00" in args.query):
        parser.error("query must contain 1..2000 characters and cannot be blank")
    if not 1 <= args.max_items <= 10 or not 1 <= args.repeat <= 3:
        parser.error("max-items must be 1..10 and repeat 1..3")
    if not math.isfinite(args.seconds_per_paper) or not .1 <= args.seconds_per_paper <= 120:
        parser.error("seconds-per-paper must be .1..120")
    if not math.isfinite(args.total_seconds) or not .1 <= args.total_seconds <= 600:
        parser.error("total-seconds must be .1..600")
    if args.doi and len(args.doi) > 10:
        parser.error("select at most ten DOIs")
    if args.url and (not args.doi or len(args.doi) != 1):
        parser.error("--url requires exactly one --doi")
    try:
        dois = list(dict.fromkeys(_identity("doi:" + doi)[4:] for doi in (args.doi or PREPARATION_SAMPLES.values())))
        if args.url:
            _uri_parts(args.url, "candidate URL")
        args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    except (LiteratureError, OSError) as exc:
        parser.error(type(exc).__name__ + ": invalid input or output directory already exists")
    web = GroundingWeb(max_response_bytes=MAX_DOCUMENT_BYTES, max_seconds=25)
    locator = SemanticScholarPdfLocator(timeout_seconds=5)
    browser = None
    deadline = monotonic() + args.total_seconds
    report = {"scope": "standalone_acquisition_only", "rows": [], "errors": [],
              "research_run_created": False, "manual_challenge_handling": False,
              "completed": False, "selection": "query" if args.query else "samples" if args.samples else "doi"}
    if args.query:
        report["query"] = args.query
    try:
        if args.browser_endpoint:
            from hypoloom.institutional_browser import PlaywrightInstitutionalBrowser
            browser = PlaywrightInstitutionalBrowser(args.browser_endpoint)
        if args.query:
            dois = search(web, args.query, args.max_items, deadline)
        report["selected_dois"] = dois
        for repeat in range(1, args.repeat + 1):
            for number, doi in enumerate(dois, 1):
                output = args.output / f"round-{repeat:02d}-paper-{number:02d}.pdf"
                row = acquire(doi, output, web=web, locator=locator, browser=browser,
                    seconds=args.seconds_per_paper, deadline=deadline, supplied_url=args.url)
                row["round"] = repeat
                report["rows"].append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
        if not dois:
            report["errors"].append({"stage": "search", "reason": "no_results"})
        report["completed"] = True
    except (GroundingWebError, LiteratureError, ValueError) as exc:
        report["errors"].append(error_event("experiment", exc))
    finally:
        if browser is not None:
            browser.close()
        report["pdf_verified"] = sum(row["state"] == "pdf_verified" for row in report["rows"])
        report["all_selected_verified"] = report["completed"] and bool(report["rows"]) and not report["errors"] and all(
            row["state"] == "pdf_verified" for row in report["rows"])
        (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.output / "report.json"),
        "pdf_verified": report["pdf_verified"], "all_selected_verified": report["all_selected_verified"]}), flush=True)
    return 0 if report["all_selected_verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
