"""Real CC/Host readers and CPU closure; the provider is an isolated local script."""
import json
import secrets
import shutil
import threading
import time
from dataclasses import replace
from pathlib import Path
import pytest

from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeTask
from hypoloom.grounding_web import GroundingWeb
from hypoloom.oracle import Oracle
from test_claude_file_tool_cli import _installed_claude, _tool_results, _result_text
from test_claude_final_report_g3 import _FinalReportHostScenario
from test_claude_multichain_g3 import _payload
from test_full_cc_host_g3 import _new_host, _start_provider
from test_grounding_web import DOC, Transport, _response


from tests.runtime.test_institutional_access import ALL_PROVIDERS_AUTO


def _main(request):
    return (request.command is AgentCommand.LITERATURE_REVIEW and isinstance(request.task_context, ResearchNodeTask)
            and request.task_context.key.subkind.value == "MAIN")


class ReadingScenario(_FinalReportHostScenario):
    def __init__(self, root, *, batch_documents=False, correct_errors=False):
        super().__init__(root)
        self.batch_documents = batch_documents
        self.correct_errors = correct_errors
        self.nonce = "method-page-" + secrets.token_hex(12)
        self.sources = {}; self.results = {}; self.readers = set()
        self.source_retries = set()
        # The local provider can finish a reader before bounded Host dispatch
        # starts the third one. Hold its first response until the test observes
        # all three real reader processes; cancellation still uses the Host.
        self.readers_ready = threading.Event()

    def record(self, document):
        number = super().record(document)
        request = self.current
        try:
            step = self.steps[request.attempt_id]
            if request.command is AgentCommand.PAPER_READ and step == 1:
                assert self.readers_ready.wait(30), "three native reader processes were not admitted"
            results = _tool_results(document)
            if _main(request):
                if step == 2:
                    result = next(value for key, value in results.items() if key.endswith("__prepare_source"))
                    assert not result.get("is_error"), result
                    source = json.loads(_result_text(result))
                    self.sources[request.attempt_id] = source["documents"] if self.batch_documents else [source]
                    if self.batch_documents and any("source_ref" not in item for item in source["documents"]):
                        assert all("source_ref" in item or item.get("error", {}).get("code") == "paper_io_busy"
                            for item in source["documents"]), source
                        self.source_retries.add(request.attempt_id)
                if step == 3 and request.attempt_id in self.source_retries:
                    result = next(value for key, value in results.items() if key.endswith("__prepare_retry"))
                    assert not result.get("is_error"), result
                    source = json.loads(_result_text(result))
                    assert len(source["documents"]) == 2 and all("source_ref" in item for item in source["documents"]), source
                    self.sources[request.attempt_id] = source["documents"]
                logical_step = step - int(request.attempt_id in self.source_retries and step >= 3)
                if self.correct_errors and logical_step in {3, 4}:
                    suffix = "__bad_fields" if logical_step == 3 else "__bad_reference"
                    result = next(value for key, value in results.items() if key.endswith(suffix))
                    assert result.get("is_error"), result
                    assert ("wait_for_completion" if logical_step == 3 else "web_fetch") in _result_text(result)
                if logical_step >= (6 if self.correct_errors else 4):
                    result = next(value for key, value in results.items() if key.endswith(f"__reading_wait_{step - 1}_call"))
                    assert not result.get("is_error"), result
                    self.results[request.attempt_id] = json.loads(_result_text(result))
                    if self.results[request.attempt_id]["terminal"]:
                        assert len(self.results[request.attempt_id]["readings"]) == 3
                        assert all(item["status"] == "completed" and self.nonce in item["answer"]["summary"]
                            for item in self.results[request.attempt_id]["readings"])
                        response = self.responses[request.attempt_id]
                        self.responses[request.attempt_id] = replace(response, typed_candidate={**response.typed_candidate,
                            "summary": "Selected original methods were checked by three independent question readers: " + self.nonce,
                            "web_sources": [source["url"] for source in self.sources[request.attempt_id]]})
            elif request.command is AgentCommand.PAPER_READ and step >= 3:
                result = next(value for key, value in results.items() if key.endswith("__read_method"))
                assert not result.get("is_error"), result
                assert self.nonce in _result_text(result)
                self.readers.add(request.attempt_id)
                response = self.responses[request.attempt_id]
                source = json.loads(next(item for item in request.input_artifacts if item.ref.endswith("--paper-context")).content_bytes())["sources"][0]
                self.responses[request.attempt_id] = replace(response, typed_candidate={**response.typed_candidate,
                    "summary": f"Source {source['source_ref']}, physical page 1: exact observed method detail {self.nonce}. Interpretation is limited to the supplied independent question."})
        except Exception as exc: self.errors.append(f"paper provider step {number}: {type(exc).__name__}: {exc}")
        return number

    def _unscoped_tool_calls(self, number):
        request = self.current
        if request is None or self.errors: return ()
        step = self.steps[request.attempt_id]
        if request.command is AgentCommand.PAPER_READ:
            if step == 1: return (("read_index", "Read", {"file_path": "/papers/INDEX.md"}),)
            if step == 2: return (("read_method", "Read", {"file_path": "/papers/source-0001/pages.txt"}),)
            return self._structured(request)
        if _main(request):
            if step == 1:
                arguments = {"mode": "document", "sources": [{"url": DOC}, {"url": DOC + "/second"}]} if self.batch_documents else {"mode": "document", "url": DOC}
                return (("prepare_source", "mcp__host-tools__web_fetch", arguments),)
            if step == 2 and request.attempt_id in self.source_retries:
                # Keep the real one-worker policy. Reuse the first accepted
                # source and retry the bounded batch once after transient busy.
                return (("prepare_retry", "mcp__host-tools__web_fetch",
                    {"mode": "document", "sources": [{"url": DOC}, {"url": DOC + "/second"}]}),)
            logical_step = step - int(request.attempt_id in self.source_retries and step >= 3)
            if logical_step == 2:
                if self.correct_errors:
                    return (("bad_fields", "mcp__host-tools__read_papers", {"action": "submit", "readings": [], "wait_for_completion": True}),)
            if logical_step == 3 and self.correct_errors:
                return (("bad_reference", "mcp__host-tools__read_papers", {"action": "submit", "readings": [
                    {"reading_id": "url-mistake", "paper_ref": DOC, "question": "Check the method."}]}),)
            if logical_step == (4 if self.correct_errors else 2):
                return (("submit_readers", "mcp__host-tools__read_papers", {"action": "submit", "readings": [
                    {"reading_id": f"question-{i}", "paper_ref": self.sources[request.attempt_id][i % len(self.sources[request.attempt_id])]["source_ref"],
                     "question": f"Independent method question {i}: quote the exact coefficient label and state the limitation."} for i in range(3)]}),)
            if self.results.get(request.attempt_id, {}).get("terminal"): return self._structured(request)
            return ((f"reading_wait_{step}_call", "mcp__host-tools__read_papers", {"action": "wait", "max_wait_seconds": 10}),)
        return super()._unscoped_tool_calls(number)


class CrossrefReadingScenario(ReadingScenario):
    """Select the actual backup packet's first PDF for the ordinary readers."""
    def __init__(self, root, **kwargs):
        super().__init__(root, **kwargs)
        self.catalog_packets = {}

    def _unscoped_tool_calls(self, number):
        if self.current is not None and _main(self.current) and self.steps[self.current.attempt_id] == 1:
            from hypoloom.literature import LiteraturePacket
            packet = LiteraturePacket.from_bytes(next(item.content_bytes() for item in self.current.input_artifacts
                if item.ref.endswith('--literature-packet')))
            self.catalog_packets[self.current.attempt_id] = packet
            assert all(paper.pdf_trace is not None for paper in packet.papers)
            return (('prepare_source', 'mcp__host-tools__web_fetch',
                {'mode': 'document', 'url': packet.papers[0].pdf_trace.final_uri}),)
        return super()._unscoped_tool_calls(number)


@pytest.mark.parametrize("end_run,batch_documents,correct_errors,institutional_oa", [
    (False, False, False, False), (True, False, False, False), (False, True, False, False),
    (False, False, True, False), (False, False, False, True), (False, False, False, "metadata"),
    (False, False, False, "preparation-failure"), (False, False, False, "browser"),
    (False, False, False, "arxiv"), (False, False, False, "arxiv-timeout"),
    (False, False, False, "arxiv-challenge")],
    ids=["cpu-completion", "end-run", "batch-documents", "correct-errors", "institutional-oa", "institutional-metadata", "preparation-failure", "institutional-browser", "arxiv-fallback", "arxiv-timeout", "challenge-and-quota"])
def test_native_three_readers_original_sources_parent_collects_and_cpu_closes(tmp_path, monkeypatch, end_run, batch_documents, correct_errors, institutional_oa, service_recovery=False, catalog_fallback=False):
    arxiv_oa = institutional_oa in {'arxiv', 'arxiv-timeout', 'arxiv-challenge'}
    if institutional_oa:
        import sys
        monkeypatch.setattr(sys.modules[__name__], "DOC", "https://www.nature.com/articles/s41586-026-00001-1")
    scenario_type = CrossrefReadingScenario if catalog_fallback else ReadingScenario
    scenario = scenario_type(tmp_path, batch_documents=batch_documents, correct_errors=correct_errors)
    secret = "isolated-script-only-" + secrets.token_hex(12)
    provider, thread = _start_provider(scenario, secret)
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{}\n"); settings_file.chmod(0o600)
    host = _new_host(tmp_path, _installed_claude(), f"http://127.0.0.1:{provider.server_port}", secret)
    if catalog_fallback:
        from urllib.parse import parse_qs, urlsplit
        from hypoloom.crossref_search import CrossrefSearch
        from hypoloom.literature import FetchResponse, OpenAlexResponse
        from test_institutional_browser import _pdf
        catalog_requests, primary_requests, pdf_bytes, pdf_requests = [], [], {}, []

        class CrossrefCatalog:
            def get(self, **arguments):
                catalog_requests.append(arguments)
                assert arguments['host'] == 'api.crossref.org'
                assert set(arguments['headers']) == {'Accept', 'User-Agent'}
                params = parse_qs(urlsplit(arguments['target']).query)
                year = int(params['filter'][0].split('from-pub-date:')[1][:4])
                doi = f'10.1234/crossref-native.{year}'
                uri = f'https://papers.example.org/crossref-native-{year}.pdf'
                pdf_bytes.setdefault(uri, _pdf(doi + ' ' + scenario.nonce))
                host.grounding_web.transport.responses[uri] = _response(uri, pdf_bytes[uri], media_type='application/pdf')
                work = {'DOI': doi, 'type': 'journal-article', 'title': [params['query.bibliographic'][0]],
                    'author': [{'given': 'Ada', 'family': 'Lovelace'}], 'language': 'en',
                    'published': {'date-parts': [[year, 1, 1]]},
                    'link': [{'URL': uri, 'content-type': 'application/pdf'}]}
                body = json.dumps({'status': 'ok', 'message-type': 'work-list', 'message': {'items': [work]}}).encode()
                return OpenAlexResponse(status_code=200, media_type='application/json', content_length=len(body), body=body)

        def unavailable_primary(**arguments):
            primary_requests.append(arguments)
            return OpenAlexResponse(status_code=503, media_type='application/json',
                content_length=2, body=b'{}', retry_after='900')

        def public_pdf(uri, *, approved_addresses, max_bytes):
            assert approved_addresses == ('8.8.8.8',)
            pdf_requests.append(uri)
            body = pdf_bytes[uri]
            assert len(body) <= max_bytes
            return FetchResponse(requested_uri=uri, status_code=200, peer_address='8.8.8.8',
                media_type='application/pdf', content_length=len(body), location=None, body=body)

        host.literature_io.fetcher._transport.get = unavailable_primary
        host.literature_io.fetcher.max_retries = 0
        host.literature_io.fetcher._search_fallback = CrossrefSearch(transport=CrossrefCatalog())
        host.literature_io.fetcher._pdf_fetcher = public_pdf
    if service_recovery:
        from hypoloom.literature import OpenAlexResponse
        from hypoloom.literature_recovery import status as service_status
        from test_literature_recovery import OffsetClock
        recovery_clock = OffsetClock()
        monkeypatch.setattr("hypoloom.literature_recovery.time", recovery_clock)
        # Fault only the catalog transport. The original OpenAlex parser,
        # accepted query, packet admission and real CC topology stay in use.
        fetcher = host.literature_io.fetcher
        fetcher.max_retries = 0
        catalog_get = fetcher._transport.get
        admit_coverage = host.literature_io.admit_query_coverage
        coverage_admitted = False
        def admit_then_outage(query):
            nonlocal coverage_admitted
            result = admit_coverage(query)
            coverage_admitted = True
            return result
        host.literature_io.admit_query_coverage = admit_then_outage
        outage_requests, recovery_waves = [], []
        def outage_then_catalog(**kwargs):
            if coverage_admitted and len(outage_requests) < 6:
                outage_requests.append(kwargs["target"])
                return OpenAlexResponse(status_code=503, media_type="application/json",
                    content_length=2, body=b"{}")
            return catalog_get(**kwargs)
        fetcher._transport.get = outage_then_catalog
    host.grounding_web = GroundingWeb(Transport({url: _response(url,
        ("<title>Selected method</title><p>Coefficient label " + scenario.nonce + "</p>").encode())
        for url in (DOC, DOC + "/second")}))
    if institutional_oa:
        from hypoloom.institutional_access import InstitutionalAccess
        from hypoloom.literature import UnpaywallPdfLocator
        from test_document_fallback import Metadata, OA, DOI
        from test_institutional_access import Backend
        from test_institutional_browser import _pdf
        backend, metadata = Backend(), Metadata()
        if institutional_oa == "browser":
            def fetch(url, **kwargs):
                backend.calls.append(url)
                return {"state": "available", "document": {"content": _pdf(DOI + " " + scenario.nonce),
                    "pages": (DOI + " " + scenario.nonce,), "media_type": "application/pdf",
                    "url": DOC + ".pdf", "requested_url": url, "identity": "doi:" + DOI,
                    "title": "Original method for independent reading"}}
            backend.fetch = fetch
        modes = {"Nature": "public_only"} if arxiv_oa else ({"Nature": "auto"} if institutional_oa == "browser" else {})
        if institutional_oa == 'preparation-failure':
            from hypoloom.cli import build_parser
            from hypoloom import institutional_prepare
            from test_institutional_preparation import PreparationBackend
            failed = PreparationBackend(); failed.state = 'verification_required'
            output = []
            with monkeypatch.context() as preparation_patch:
                preparation_patch.setattr(institutional_prepare, 'build', lambda _: InstitutionalAccess(failed, provider_modes=ALL_PROVIDERS_AUTO))
                args = build_parser().parse_args(['institutional', 'prepare', '--sample', DOC, '--non-interactive',
                    '--institutional-prepare-failure-mode', 'public_only'])
                assert institutional_prepare.run(args, input_fn=lambda _: pytest.fail('manual recovery requested'),
                    output=output.append) == 1
            summary = json.loads(output[-1])
            (tmp_path / 'preparation-summary.json').write_text(json.dumps(summary, indent=2))
            assert summary['preparation_summary'][0]['state'] == 'verification_required'
            assert summary['remaining_preparation_argv'] == [] and failed.closed
            modes = summary['provider_modes']
            assert modes == {'Nature': 'public_only'}
        host.institutional_access = InstitutionalAccess(backend, provider_modes=modes)
        host.configuration["institutional_access"] = host.institutional_access.configuration
        if institutional_oa == 'arxiv-challenge':
            from hypoloom.literature import SemanticScholarPdfLocator, OpenAlexResponse
            def limited(**kwargs):
                metadata.calls.append(kwargs)
                body = b'{"message":"Too Many Requests","code":"429"}'
                return OpenAlexResponse(status_code=429, media_type='application/json',
                    content_length=len(body), body=body, retry_after='120')
            metadata.get = limited
            host.literature_io.fallback_locator = SemanticScholarPdfLocator(transport=metadata)
        else:
            host.literature_io.fallback_locator = UnpaywallPdfLocator(email="reader@example.org", transport=metadata)
        host.configuration["literature"] = host.literature_io.configuration
        # Finish isolated launch configuration before the first run/version is persisted.
        host.version = host._runtime_version()
        lookup = "https://api.openalex.org/works/https://doi.org/" + DOI
        crossref = "https://api.crossref.org/works/" + DOI
        work = {"doi": DOI, "display_name": "Original method for independent reading",
                "locations": [{"is_oa": True, "pdf_url": OA.replace("https:", "http:")}]}
        if arxiv_oa:
            from hypoloom import arxiv_fallback
            from test_arxiv_fallback import atom, feed, AUTHORS, PDF as ARXIV_PDF
            monkeypatch.setattr(arxiv_fallback, 'API_LOCK', tmp_path / 'arxiv-api.lock')
            work.update(locations=[], authorships=[{'author': {'display_name': author}} for author in AUTHORS])
            if institutional_oa == 'arxiv-challenge':
                work['locations'] = [{'is_oa': True, 'pdf_url': OA}]
        host.grounding_web = GroundingWeb(Transport({DOC: _response(DOC, b"Denied", status_code=403),
            lookup: _response(lookup, json.dumps(work).encode(), media_type="application/json",
                              status_code=200 if arxiv_oa or institutional_oa in {"metadata", "preparation-failure"} else 404),
            crossref: _response(crossref, b"Not found", status_code=404),
            OA: _response(OA, _pdf(DOI + " " + scenario.nonce), media_type="application/pdf",
                          status_code=403 if arxiv_oa or institutional_oa == "browser" else 200)}))
        if arxiv_oa:
            target = arxiv_fallback.title_query(work['display_name'])
            host.grounding_web.transport.responses.update({
                target: _response(target, feed(atom(title=work['display_name'], doi=DOI)), media_type='application/atom+xml'),
                ARXIV_PDF: _response(ARXIV_PDF, _pdf(DOI + ' ' + scenario.nonce), media_type='application/pdf')})
            if institutional_oa == 'arxiv-challenge':
                from hypoloom.grounding_web import GroundingWebError
                fetch = host.grounding_web.transport.fetch
                challenged = []
                def publisher_challenge(url, **kwargs):
                    if url in {DOC, OA}:
                        challenged.append(url)
                        raise GroundingWebError('challenge', 'web provider requires human verification', http_status=403)
                    return fetch(url, **kwargs)
                host.grounding_web.transport.fetch = publisher_challenge
            if institutional_oa == 'arxiv-timeout':
                from hypoloom.grounding_web import GroundingWebError
                fetch = host.grounding_web.transport.fetch
                pdf_requests = []
                def interrupted_once(url, **kwargs):
                    if url == ARXIV_PDF:
                        pdf_requests.append(url)
                        if len(pdf_requests) == 1:
                            raise GroundingWebError('timed_out', 'controlled partial transfer timeout',
                                transport_phase='response_body', received_bytes=1378)
                    return fetch(url, **kwargs)
                host.grounding_web.transport.fetch = interrupted_once
    if service_recovery or catalog_fallback:
        host.configuration["literature"] = host.literature_io.configuration
        host.version = host._runtime_version()
    run_id = "native-paper-reading"
    max_readers = 0; stopped = False
    try:
        payload = _payload(tmp_path, run_id)
        contract = payload["research_contract"]
        product = contract["methodology"]["controls"]["product"]
        product["methodology"]["literature"]["tools"] = ["web_search", "web_fetch", "read_papers"]
        product["experts"]["defaults"]["max_hypotheses_per_round"] = 1
        product["methodology"]["inner_loop"]["max_iterations_per_hypothesis"] = 1
        contract["budget"]["max_active_agents"] = 4
        host.initialize()
        created = host.handle(ControlRequest(schema_version=1, request_id="create-reading", expected_revision=0,
            command="CREATE_RUN", payload=payload))
        assert created.status == "accepted", created
        if institutional_oa and not arxiv_oa and institutional_oa not in {'preparation-failure', 'browser'}:
            choice = host.handle(ControlRequest(1, "native-public-only", created.revision, "LITERATURE_ACCESS",
                {"run_id": run_id, "provider": "Nature", "action": "mode", "mode": "public_only"}))
            assert choice.status == "accepted", choice
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            host.tick()
            if service_recovery:
                waiting = service_status(host.store, run_id)
                if waiting["pending"] and waiting["recovery_attempts"] > len(recovery_waves):
                    wave = len(recovery_waves) + 1
                    assert waiting["state"] == "cooldown" and not waiting["requires_operator"]
                    assert waiting["recovery_attempts"] == wave and len(outage_requests) == wave
                    failed_reads = [row for row in host.store.list_attempts()
                        if json.loads(row["observation_json"]).get("failure_stage") == "literature_retrieval"]
                    assert len(failed_reads) == wave
                    failure_observed_at = max(json.loads(row["observation_json"])["literature_service"]["observed_at"]
                        for row in failed_reads)
                    assert waiting["next_retry_at"] - failure_observed_at == pytest.approx(
                        (30, 60, 120, 240, 300, 900)[wave - 1], abs=.1)
                    for _ in range(3): host.tick()
                    assert len(outage_requests) == wave, "cooldown must not launch another catalog request"
                    recovery_waves.append(waiting)
                    # Only advance the existing recovery decision clock. Real
                    # Host/Attempt/CC wall times and original facts are intact.
                    recovery_clock.advance_to(waiting["next_retry_at"])
            max_readers = max(max_readers, sum(handle.request.command is AgentCommand.PAPER_READ for handle in host.running_agents.values()))
            if max_readers == 3:
                scenario.readers_ready.set()
            if end_run and not stopped and max_readers == 3:
                reply = host.handle(ControlRequest(schema_version=1, request_id="end-paper-test", expected_revision=None,
                    command="CANCEL", payload={"run_id": run_id, "cause": "bounded paper-reader End run test"}))
                assert reply.status == "accepted", reply
                stopped = True
            assert not scenario.errors, scenario.errors
            assert not host.dispatch_errors, host.dispatch_errors
            if host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}: break
            time.sleep(0.01)
        assert host.store.latest_transition(run_id)["state"] == ("CANCELLED" if end_run else "COMPLETED")
        assert max_readers == 3
        if not end_run: assert len(scenario.readers) == 3
        if institutional_oa:
            assert backend.calls == ([DOC] if institutional_oa == "browser" else [])
            assert len(metadata.calls) == (0 if institutional_oa in {"metadata", "preparation-failure"} else 1)
            assert all(source["url"] == (DOC + ".pdf" if institutional_oa == "browser" else ARXIV_PDF if arxiv_oa else OA)
                       for sources in scenario.sources.values() for source in sources)
            if arxiv_oa:
                assert all('arXiv 2401.01234v2' in source['version_note']
                           for sources in scenario.sources.values() for source in sources)
            if institutional_oa == 'arxiv-timeout':
                assert len(pdf_requests) == 2
            if institutional_oa == 'arxiv-challenge':
                assert challenged.count(DOC) == challenged.count(OA) == 1
                trace = [event for attempt in host.store.list_attempts()
                    for call in json.loads(attempt['observation_json']).get('web_tool_results', [])
                    for row in call.get('literature_download_trace', []) for event in row.get('events', [])]
                assert any(e['failure_code'] == 'PDF_CHALLENGE' and e['http_status'] == 403 for e in trace)
                assert any(e['http_status'] == 429 for e in trace)
                assert host.literature_io.locate_doi('doi:' + DOI, [], timeout_seconds=1) == ()
                assert len(metadata.calls) == 1, 'quota failure must not cause another remote lookup'
                (tmp_path / 'challenge-quota-evidence.json').write_text(json.dumps({
                    'challenged_urls': challenged, 's2_requests': len(metadata.calls), 'trace': trace}, indent=2))
            if institutional_oa != "browser":
                assert all("version_note" in source for sources in scenario.sources.values() for source in sources)
            assert len(host.store.inspect("SELECT * FROM workflow_commands WHERE command_type='LITERATURE_ACCESS'")) == (0 if arxiv_oa or institutional_oa in {'preparation-failure', 'browser'} else 1)
        attempts = host.store.list_attempts()
        assert all(row["state"] == "CLOSED" for row in attempts)
        service_failures = [row for row in attempts
            if json.loads(row["observation_json"]).get("failure_stage") == "literature_retrieval"] if service_recovery else []
        if not end_run:
            assert all(row["terminal_reason"] == "SUCCEEDED" for row in attempts if row not in service_failures)
        if service_recovery:
            from hypoloom.agent_budget import fold_agent_attempts
            from hypoloom.observation_api import build_product_observation
            assert len(service_failures) == len(recovery_waves) == 6
            assert all(row["terminal_reason"] == "FAILED" for row in service_failures)
            assert all(json.loads(row["observation_json"])["literature_service"]["reason"] == "openalex_http"
                and json.loads(row["observation_json"])["literature_service"]["http_status"] == 503
                for row in service_failures)
            assert len({row["command_id"] for row in service_failures}) == 1
            requests = [json.loads(row["observation_json"])["request"] for row in service_failures]
            assert all(request["input_artifacts"] == requests[0]["input_artifacts"] for request in requests)
            assert len(set(outage_requests)) == 1
            continuation = [row for row in attempts if row["command_id"] == service_failures[0]["command_id"]]
            assert len(continuation) == 7 and continuation[-1]["terminal_reason"] == "SUCCEEDED"
            assert all(charge.source == "pre_model" and charge.tokens == charge.turns == charge.cost == 0
                for charge in fold_agent_attempts(service_failures))
            plans = [row for row in attempts if json.loads(row["observation_json"]).get("request", {}).get(
                "task_context", {}).get("request_kind") == "GROUNDING_QUERY_PLAN"]
            assert len(plans) == 1
            assert service_status(host.store, run_id)["state"] == "ready"
            assert not host.store.inspect("SELECT command_id FROM workflow_transitions WHERE json_type(payload_json,'$.literature_retry_clear')='object'")
            events = [event["type"] for event in build_product_observation(host.state_root, run_id)["events"]]
            assert events.count("literature_waiting") == events.count("literature_recovered") == 1
            (tmp_path / "automatic-recovery-evidence.json").write_text(json.dumps({
                "paid_model": False, "gpu": False, "recovery_clock_only_advanced": True,
                "waves": recovery_waves, "failed_request_targets": outage_requests,
                "failed_attempts": [row["attempt_id"] for row in service_failures],
                "continuation": [row["attempt_id"] for row in continuation], "grounding_query_plans": len(plans),
                "literature_waiting_events": events.count("literature_waiting"),
                "literature_recovered_events": events.count("literature_recovered")}, indent=2))
        if catalog_fallback:
            from hypoloom.literature_recovery import status as service_status
            assert len(primary_requests) == 1, 'primary Retry-After must prevent repeated outage probes'
            assert len(catalog_requests) >= 6 and len(catalog_requests) % 3 == 0
            assert pdf_requests and scenario.catalog_packets
            for packet in scenario.catalog_packets.values():
                assert len(packet.papers) == 3
                assert all(paper.source_uri.startswith('https://api.crossref.org/works/')
                    and paper.venue is None and paper.pdf_trace is not None for paper in packet.papers)
            status = service_status(host.store, run_id)
            assert status['state'] == 'ready' and status['active_service'] == 'crossref'
            assert status['primary_failure'] == 'openalex_http'
            service = [json.loads(row['observation_json']).get('preflight_diagnostics', {}).get('literature_service')
                for row in attempts]
            service = [value for value in service if value is not None]
            assert service and all(value['available'] is False and value['fallback_available'] is True
                and value['http_status'] == 503 for value in service)
            assert not host.store.inspect("SELECT command_id FROM workflow_transitions WHERE json_type(payload_json,'$.literature_retry_clear')='object'")
            assert not host.store.inspect("SELECT command_id FROM workflow_commands WHERE command_type='LITERATURE_ACCESS'")
            original_count = 0
            reader_originals = []
            for row in attempts:
                session = json.loads(row['observation_json']).get('session', {})
                for source in session.get('paper_sources', []):
                    assert source['url'] in pdf_bytes
                    from hypoloom.paper_sources import published_source, source_paths
                    original_source, archive_root, _owner = published_source(host, row, source['source_ref'])
                    original = archive_root / source_paths(original_source)[0]
                    assert original.read_bytes() == pdf_bytes[source['url']]
                    original_count += 1
                    if row['attempt_id'] in scenario.readers:
                        reader_originals.append(original.read_bytes())
            assert original_count >= 6 and len(reader_originals) == 3
            assert len(set(reader_originals)) == 1
            (tmp_path / 'crossref-fallback-evidence.json').write_text(json.dumps({
                'paid_model': False, 'gpu': False, 'primary_requests': len(primary_requests),
                'crossref_requests': len(catalog_requests), 'pdf_requests': len(pdf_requests),
                'catalog_packet_count': len(scenario.catalog_packets), 'archived_originals': original_count,
                'matching_reader_originals': len(reader_originals), 'literature_service': status}, indent=2))
        assert len([row for row in attempts if row["kind"] == "PROCESS"]) == (0 if end_run else 1)
        for row in attempts:
            observed = json.loads(row["observation_json"])
            if row["attempt_id"] in scenario.readers and not end_run:
                source = observed["session"]["paper_sources"][0]
                directory = host.publisher.root / observed["session"]["transcript"]["path"]
                assert scenario.nonce in (directory / "papers" / source["directory"] / "pages.txt").read_text()
        report = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
        (tmp_path / "oracle.json").write_text(json.dumps(report.to_dict(), indent=2))
        assert report.ok, report.to_dict()
        if correct_errors:
            from hypoloom.monitor import build_monitor_snapshot
            summary = build_monitor_snapshot(host.state_root, run_id).overview["experts"][0]["parallel_activity"]["readers"]
            assert summary["tasks"] == summary["attempts"] == summary["states"]["completed"] == 3
            assert summary["calls"]["rejected"] == 2
        assert not host.running_agents and not host.running
        assert not list((host.state_root / "paper-staging").iterdir())
        assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"] is not None)
    finally:
        scenario.readers_ready.set()
        (tmp_path / "provider-errors.json").write_text(json.dumps(scenario.errors))
        (tmp_path / "reader-evidence.json").write_text(json.dumps({"max_readers": max_readers, "observed_readers": sorted(scenario.readers), "results": scenario.results}, indent=2))
        host.close()
        provider.shutdown(); provider.server_close(); thread.join(timeout=5)
        if (tmp_path / "admitted-target.txt").exists(): shutil.rmtree(Path((tmp_path / "admitted-target.txt").read_text().strip()))


def test_native_six_catalog_failures_recover_without_operator_then_readers_and_cpu_close(tmp_path, monkeypatch):
    test_native_three_readers_original_sources_parent_collects_and_cpu_closes(tmp_path, monkeypatch,
        False, False, False, "arxiv", service_recovery=True)


def test_native_crossref_outage_fallback_delivers_packet_pdfs_to_readers_and_cpu(tmp_path, monkeypatch):
    test_native_three_readers_original_sources_parent_collects_and_cpu_closes(tmp_path, monkeypatch,
        False, False, False, False, catalog_fallback=True)


@pytest.mark.parametrize("stage", ["execution", "readers"])
def test_native_quarantine_ends_calls_preserves_work_and_allows_explicit_cleanup(tmp_path, monkeypatch, stage):
    from test_claude_measurement_g3 import _NoPublicWeb

    class SlowProcessScenario(_FinalReportHostScenario):
        def _unscoped_tool_calls(self, number):
            calls = super()._unscoped_tool_calls(number)
            if self.current.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT and self.steps[self.current.attempt_id] == 1:
                identity, name, arguments = calls[0]
                return ((identity, name, {**arguments, "content": arguments["content"] + "\nimport time\ntime.sleep(3)\n"}),)
            return calls

    scenario = ReadingScenario(tmp_path) if stage == "readers" else SlowProcessScenario(tmp_path)
    secret = "isolated-quarantine-" + secrets.token_hex(12)
    provider, thread = _start_provider(scenario, secret)
    settings = tmp_path / "settings.json"; settings.write_text("{}\n"); settings.chmod(0o600)
    host = _new_host(tmp_path, _installed_claude(), f"http://127.0.0.1:{provider.server_port}", secret)
    host.grounding_web = (GroundingWeb(Transport({DOC: _response(DOC,
        ("<p>Method label " + scenario.nonce + "</p>").encode())})) if stage == "readers" else _NoPublicWeb())
    run_id = "native-quarantine-" + stage
    affected = {}; paused = False
    try:
        payload = _payload(tmp_path, run_id)
        contract = payload["research_contract"]
        product = contract["methodology"]["controls"]["product"]
        product["experts"]["defaults"]["max_hypotheses_per_round"] = 1
        product["methodology"]["inner_loop"]["max_iterations_per_hypothesis"] = 1
        if stage == "readers": product["methodology"]["literature"]["tools"] = ["web_search", "web_fetch", "read_papers"]
        contract["budget"]["max_active_agents"] = 4
        host.initialize()
        assert host.handle(ControlRequest(1, "create-quarantine", 0, "CREATE_RUN", payload)).status == "accepted"
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            host.tick()
            assert not scenario.errors, scenario.errors
            if stage == "readers":
                ready = sum(h.request.command is AgentCommand.PAPER_READ for h in host.running_agents.values()) == 3
            else:
                ready = bool(host.running) and any(h.request.command is AgentCommand.EXPERIMENT_EXECUTION
                    and scenario.steps[h.request.attempt_id] >= 2 for h in host.running_agents.values())
            if ready and not paused:
                if stage == "readers":
                    scenario.readers_ready.set()
                def fail_dispatch(run):
                    raise ValueError("isolated native dispatch fault")
                monkeypatch.setattr(host, "_drive_active", fail_dispatch)
                host.tick()
                assert run_id in host.dispatch_errors
                # Polling precedes dispatch: readers accepted earlier in this tick
                # remain successful. Only calls live at the fault are interrupted.
                affected = {aid: h.process_group_id for aid, h in host.running_agents.items()}
                paused = True
            if paused and not host.running_agents and not host.running and not host.paper_reading.completions: break
            time.sleep(0.01)
        assert paused and affected
        assert not host.running_agents and not host.running and not host.paper_reading.completions
        closed = [host.store.get_attempt(aid) for aid in affected]
        assert all(row["state"] == "CLOSED" and row["terminal_reason"] == "FAILED" for row in closed)
        assert all(json.loads(row["observation_json"])["failure_code"] == "dispatch_paused" for row in closed)
        assert all(not process_group_exists(pgid) for pgid in affected.values())
        assert host.store.latest_transition(run_id)["state"] == "ACTIVE"
        assert not host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        if stage == "execution":
            processes = [row for row in host.store.list_attempts() if row["kind"] == "PROCESS"]
            assert len(processes) == 1 and processes[0]["terminal_reason"] == "SUCCEEDED"
            view = host._experiment_tool_view(run_id, processes[0]["workflow_id"], include_log=False)
            assert view["state"] == "EVALUATING" and view["status"] == "blocked" and not view["terminal"]
            assert not scenario.execution_results
        provider_count = len(scenario.requests)
        attempts_count = len(host.store.list_attempts())
        for _ in range(5): host.tick()
        assert len(scenario.requests) == provider_count and len(host.store.list_attempts()) == attempts_count
        assert host.handle(ControlRequest(1, "status-paused", None, "STATUS", {"run_id": run_id})).status == "accepted"
        assert host.handle(ControlRequest(1, "cancel-quarantine", None, "CANCEL",
            {"run_id": run_id, "cause": "explicit isolated test cleanup"})).status == "accepted"
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            host.tick()
            if host.store.latest_transition(run_id)["state"] == "CANCELLED": break
            time.sleep(0.01)
        assert host.store.latest_transition(run_id)["state"] == "CANCELLED"
        attempts = host.store.list_attempts()
        assert all(row["state"] == "CLOSED" for row in attempts)
        assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"] is not None)
        report = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
        (tmp_path / "oracle.json").write_text(json.dumps(report.to_dict(), indent=2))
        assert report.ok, report.to_dict()
        assert not host.running_agents and not host.running
        assert not list((host.state_root / "paper-staging").glob("*"))
    finally:
        if stage == "readers":
            scenario.readers_ready.set()
        (tmp_path / "quarantine-evidence.json").write_text(json.dumps({"stage": stage,
            "paused": paused, "affected": affected, "provider_errors": scenario.errors,
            "provider_steps": dict(scenario.steps)}, indent=2))
        host.close()
        provider.shutdown(); provider.server_close(); thread.join(timeout=5)
        if (tmp_path / "admitted-target.txt").exists(): shutil.rmtree(Path((tmp_path / "admitted-target.txt").read_text().strip()))
