"""Native same-Attempt correction remains visible through authenticated HTTP.

The existing local-provider fixture owns real CC, bwrap, Host, MCP and CPU
execution. Only its DESIGN outputs are deliberately rejected twice. Web reads
the resulting ordinary durable state after that fixture has closed its Host.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
from urllib.parse import urlencode

from hypoloom.claude_agent_host import process_group_exists
from hypoloom.oracle import Oracle
from hypoloom.web_observer import create_server

import test_real_design_recovery_probe as probe
from test_full_cc_host_g3 import _rows
from test_module_rehearsal import _artifact_snapshot, _authority_snapshot
from test_successful_agent_diagnostics_g3 import _SameAttemptDesign
from test_web_observer import _request, _start, _stop


def test_native_same_attempt_corrections_survive_authenticated_read_only_web(tmp_path, monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("HYPOLOOM_REAL_"):
            monkeypatch.delenv(name)
    assert not probe._paid_enabled()
    import test_full_cc_host_g3 as native_fixture
    from hypoloom.literature import FetchResponse, LiteratureFetchError, LiteratureStratum
    from test_literature import PDF_LATEST, _extraction
    original_literature = native_fixture.FixedLiteratureIO

    def literature_io(fetcher, **kwargs):
        original_search = fetcher.search
        operations = []

        def search(query):
            operations.clear()
            return tuple(replace(paper,
                bibliographic_identity="doi:10.1000/" + paper.bibliographic_identity.split(":")[-1],
                pdf_uri=("https://papers.example.org/" + paper.bibliographic_identity.split(":")[-1] + ".pdf"
                         if paper.stratum is LiteratureStratum.LATEST_AS_OF_RUN else None))
                for paper in original_search(query))

        def fetch_pdf(uri, **kwargs):
            operations.append("download")
            return FetchResponse(requested_uri=uri, status_code=200, peer_address="8.8.8.8",
                media_type="application/pdf", content_length=len(PDF_LATEST), location=None, body=PDF_LATEST)

        def extract_pdf(content, *, max_pages):
            assert content == PDF_LATEST
            return _extraction("native literature evidence")

        class Locator:
            configuration = {"adapter": "local-scripted-doi"}

            def preflight(self):
                return {"network_probe_performed": False}

            def locate(self, paper):
                assert "download" in operations, "fallback starved the direct PDF"
                raise LiteratureFetchError("scripted rate limit", http_status=429)

        fetcher.search = search
        fetcher._pdf_fetcher = fetch_pdf
        fetcher._pdf_extractor.extract_pdf = extract_pdf
        return original_literature(fetcher, fallback_locator=Locator(), pdf_workers=2, **kwargs)

    monkeypatch.setattr(native_fixture, "FixedLiteratureIO", literature_io)
    monkeypatch.setattr(probe, "_LocalDesignScenario", _SameAttemptDesign)
    native = tmp_path / "native"
    closure = probe._run_probe(native)
    assert closure["paid_design"] is closure["gpu"] is False
    assert not closure.get("fixture_errors")
    assert closure["design_accepted"] and closure["natural_cpu_closure"]
    assert closure["oracle"]["ok"] and not closure["owned_process_residue_after_close"]
    assert len(closure["design_attempts"]) == 1
    identity = closure["design_attempts"][0]["attempt_id"]
    root, run_id = native / "state", closure["run_id"]
    rows = _rows(root / "state.sqlite", "SELECT * FROM execution_attempts ORDER BY started_at")
    design = next(row for row in rows if row["attempt_id"] == identity)
    observation = json.loads(design["observation_json"])
    assert design["state"] == "CLOSED" and design["terminal_reason"] == "SUCCEEDED"
    assert design["attempt_ordinal"] == 1
    assert all(row["state"] == "CLOSED" for row in rows)
    correction = observation["diagnostics"]["structured_output_correction"]
    assert (correction["call_count"], correction["rejected_count"]) == (3, 2)
    assert correction["accepted"] is True
    assert [row["status"] for row in correction["rounds"]] == ["rejected", "rejected", "accepted"]
    assert len(closure["design_outputs"]) == 3
    assert {row["attempt_id"] for row in closure["design_outputs"]} == {identity}
    assert closure["accepted_designs_used_by_execution"] == {
        identity: observation["response"]["typed_candidate"]}

    before_rows = _authority_snapshot(root)
    before_artifacts = _artifact_snapshot(root / "artifacts")
    before_diagnostics = _artifact_snapshot(root / "diagnostics")
    server = create_server(root, run_id, port=0)
    server.log_requests = False
    thread = _start(server)
    try:
        detail_path = "/api/details?" + urlencode({"kind": "attempt", "attempt_id": identity})
        for path in ("/api/snapshot", "/api/refresh", detail_path):
            status, _, body = _request(server, "GET", path)
            assert status == 401 and identity.encode() not in body
        status, _, body = _request(server, "GET", "/api/snapshot", token=server.access_token)
        assert status == 200, body
        snapshot = json.loads(body)
        (tmp_path / "web-snapshot.json").write_bytes(body)
        status, _, refresh_body = _request(server, "GET", "/api/refresh", token=server.access_token)
        assert status == 200
        refresh = json.loads(refresh_body)
        (tmp_path / "web-refresh.json").write_bytes(refresh_body)
        assert refresh["snapshot"]["observed_at"] == refresh["activity"]["observed_at"]
        assert refresh["snapshot"]["overview"]["totals"] == snapshot["overview"]["totals"]
        assert 'summary' not in refresh['activity']
        assert refresh['snapshot']['oracle']['observation']['state'] in {'pending', 'historical'}
        assert refresh['activity']['events_page']['total'] >= len(refresh['activity']['events'])
        code, _, literature_body = _request(server, 'GET', '/api/details?kind=literature', token=server.access_token)
        assert code == 200
        literature = json.loads(literature_body)
        (tmp_path / 'web-literature.json').write_bytes(literature_body)
        assert literature['papers']
        assert all(paper['download_coverage'] == 'recorded' and paper['download_attempts'] for paper in literature['papers'])
        assert any(paper['retrieval_disposition'] == 'RETRIEVED' for paper in literature['papers'])
        assert any(event.get('http_status') == 429 for paper in literature['papers']
            for attempt in paper['download_attempts'] for event in attempt['events'])
        reliability = snapshot["overview"]["reliability"]
        assert set(reliability["metrics"]) == {
            "output_first_pass", "correction_recovery", "node_retry", "node_delivery", "required_actions"}
        assert reliability["counts"]["agent_attempts"] == sum(row["kind"] == "AGENT" for row in rows)
        node = next(item for item in reliability["nodes"] if identity in item["attempt_ids"])
        assert node["attempt_ids"] == [identity] and node["status"] == "delivered"
        assert len(node["attempts"]) == 1 and node["costs"]["retry_attempts"] == 0
        assert node["costs"]["correction_cost_usd"] is None
        for name, numerator in (("output_first_pass", 0), ("correction_recovery", 1),
                                ("node_retry", 0), ("node_delivery", 1)):
            assert node["metrics"][name] == {"numerator": numerator, "denominator": 1,
                "rate": float(numerator), "unknown": 0, "excluded": 0}, (name, node)
        attempt = node["attempts"][0]
        assert attempt["terminal_reason"] == "SUCCEEDED"
        assert {"kind": "output_rejected", "count": 2} in attempt["observations"]
        assert attempt["attribution"]["category"] == "model_output"
        assert attempt["attribution"]["confidence"] == "suspected"
        assert attempt["corrections"]["rejected_count"] == 2
        assert attempt["trace"]["coverage"] == "complete"
        assert attempt["trace"]["tool_calls"] > 0

        status, _, body = _request(server, "GET", detail_path, token=server.access_token)
        assert status == 200, body
        detail = json.loads(body)
        (tmp_path / "web-attempt.json").write_bytes(body)
        assert detail["attempt"]["attempt_id"] == identity
        assert detail["attempt"]["terminal_reason"] == "SUCCEEDED"
        assert detail["available"] and detail["scope"] == "current_invocation"
        assert detail["corrections"]["call_count"] == 3
        assert detail["corrections"]["rejected_count"] == 2
        rounds = detail["corrections"]["rounds"]
        assert [row["status"] for row in rounds] == ["rejected", "rejected", "accepted"]
        assert all(row["validation_error"] for row in rounds[:2])
        assert detail["trace_evidence"] == attempt["trace"]
        assert detail["trace_evidence"]["counts_scope"] == "observed_current_invocation"
        assert any(event["type"] == "tool_use" and event.get("name") == "Bash"
                   for event in detail["events"])
    finally:
        _stop(server, thread)
        server.refresh_reader.wait_for_audit(timeout=5)

    assert not thread.is_alive() and server.fileno() == -1
    assert not server.refresh_reader.audit_running
    assert all(not process_group_exists(row["process_group_id"])
               for row in rows if row["process_group_id"] is not None)
    audit = Oracle(root / "state.sqlite", root / "artifacts").audit(run_id)
    assert audit.ok and not audit.owned_process_residue, audit.to_dict()
    assert audit.open_attempt_count == audit.pending_effect_count == audit.duplicate_effect_count == 0
    assert _authority_snapshot(root) == before_rows
    assert _artifact_snapshot(root / "artifacts") == before_artifacts
    assert _artifact_snapshot(root / "diagnostics") == before_diagnostics
