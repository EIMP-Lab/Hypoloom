import json

from hypoloom.verification_observation import verification_view
from hypoloom.observer_scope import attempt_scopes
from hypoloom.agent_aci import EmbeddedInput
from test_observer_browser_ui import browser  # noqa: F401


def records(index, role="author", phase="inspect", verdict="changes_required", delivery=None, resolved=()):
    ref = f"review-{index}"
    value = {"experiment_id": f"experiment-{index}", "phase": phase, "subject_expert": role,
        "component_id": "code", "delivery_id": delivery or f"delivery-{index}", "source_output_digest": "a" * 64}
    command = {"command_id": ref, "request_json": json.dumps({"implementation_verification": value})}
    report = {"verdict": verdict, "summary": "Engineering evidence", "resolved_refs": list(resolved),
        "findings": [] if verdict != "changes_required" else [{"id": "dispatch", "severity": "blocker",
            "message": "Runtime dispatch selects the inherited implementation", "resolution_condition": "Probe the instantiated class", "followup_of": None, "evidence_refs": ["code"]}]}
    attempt = {"command_id": ref, "attempt_id": ref, "kind": "AGENT", "state": "CLOSED",
        "terminal_reason": "SUCCEEDED", "ended_at": index + 1, "started_at": index,
        "observation": {"request": {"command": "VERIFY_IMPLEMENTATION", "role": "verifier", "input_artifacts": []},
                        "response": {"typed_candidate": report}}}
    return command, attempt


def contract():
    return {"methodology": {"controls": {"product": {"experts": {"verifier": {"model": "selected-review-model", "effort": "high"}}}}}}


def test_two_experts_keep_issues_delivery_and_accounting_ownership_separate():
    pairs = [records(1, "a"), records(2, "b")]
    inputs = EmbeddedInput.from_bytes("task--implementation-reviews", "application/json", json.dumps({"reports": [{"report_ref": "review-1"}]}).encode()).to_dict()
    author = {"attempt_id": "author-repair", "command_id": "author-task", "kind": "AGENT", "state": "CLOSED", "terminal_reason": "SUCCEEDED",
              "observation": {"request": {"command": "EXPERIMENT_EXECUTION", "role": "a", "input_artifacts": [inputs]}}}
    attempts = [p[1] for p in pairs] + [author]
    view = verification_view(contract(), [p[0] for p in pairs], attempts)
    a, b = view["experts"]
    assert a["findings"][0]["delivery_state"] == "responded"
    assert b["findings"][0]["delivery_state"] == "generated"
    assert a["blockers"] == b["blockers"] == 1
    assert a["first_pass"] == {"passed": 0, "completed": 1}
    scopes = attempt_scopes(attempts, include_run_group=True)
    assert scopes["review-1"]["run_group"] == "verifier" and scopes["review-1"]["owner_role"] is None
    assert scopes["author-repair"]["owner_role"] == "a"
    fixed = records(3, "a", phase="review", verdict="passed", delivery="delivery-1", resolved=["review-1#dispatch"])
    view = verification_view(contract(), [p[0] for p in [*pairs, fixed]], attempts + [fixed[1]])
    assert view["experts"][0]["blockers"] == 0
    assert view["experts"][0]["findings"][0]["state"] == "verified_resolved"
    assert view["experts"][1]["blockers"] == 1


def test_large_history_is_bounded_and_inconclusive_does_not_count_as_failure():
    pairs = [records(i, verdict="passed" if i % 2 else "inconclusive") for i in range(2000)]
    view = verification_view(contract(), [p[0] for p in pairs], [p[1] for p in pairs], limit=5, offset=100)
    assert view["experts"][0]["deliveries"] == 2000
    assert view["experts"][0]["first_pass"] == {"passed": 1000, "completed": 1000}
    assert len(view["experts"][0]["recent"]) == 5
    assert len(json.dumps(view)) < 10000
    assert verification_view({}, [], [])["state"] == "unsupported"


def test_verifier_history_has_its_own_rpm_and_export_scope(tmp_path):
    import csv
    import io
    from hypoloom.state_store import StateStore, HostWriterCapability
    from hypoloom.observer_trends import build_run_trends, trends_csv
    from test_observer_trends import _seed, _exchange
    _seed(tmp_path)
    store = StateStore(tmp_path, HostWriterCapability.issue()).initialize()
    try:
        with store.transaction() as tx:
            tx.record_command(dict(command_id="verification", run_id="trends", workflow_id="trends",
                expected_revision=0, command_type="REQUEST_AUXILIARY", status="accepted", result={}, request={}))
            tx.open_attempt(dict(attempt_id="verification", command_id="verification", run_id="trends", workflow_id="trends",
                kind="AGENT", attempt_ordinal=1, host_generation="fixture", started_at=1010,
                payload={"request": {"command": "VERIFY_IMPLEMENTATION", "role": "verifier"}}))
            tx.bind_attempt_process("verification", process_group_id=12345)
            tx.close_attempt("verification", terminal_reason="SUCCEEDED", ended_at=1020, payload={})
    finally: store.close()
    _exchange(tmp_path, "verification", 1, upstream_started_at=1015)
    data = build_run_trends(tmp_path, "trends", now=1180)
    assert data["verifier"]["requests"] == [1, 0, 0]
    assert data["other"]["requests"] == data["grounding"]["requests"] == [0, 0, 0]
    assert all(sum(e["requests"]) == 0 for e in data["experts"])
    exported = list(csv.DictReader(io.StringIO(trends_csv(data).decode())))
    assert next(r for r in exported if r["scope"] == "verifier")["requests"] == "1"


def test_grounding_and_verifier_tiles_survive_same_refresh(browser):
    page = browser
    page.evaluate("""fixture.snapshot.status.run_state='ACTIVE';
      fixture.snapshot.overview.host_observation={availability:'responded'};
      fixture.snapshot.overview.grounding={state:'active',attempt_ids:['ground-review'],activity:[]};
      fixture.snapshot.overview.verifier={state:'active',model:'selected-review-model',effort:'high',attempt_ids:['code-review'],experts:[],probe_seconds:1};
      fixture.snapshot.attempts.push({attempt_id:'ground-review',state:'OPEN',kind:'AGENT',role:'grounding',task_kind:'GROUNDING'},
        {attempt_id:'code-review',state:'OPEN',kind:'AGENT',role:'verifier',task_kind:'VERIFY_IMPLEMENTATION'});""")
    page.wait("document.querySelector('#verifier-agent-card [data-attempt=code-review]') !== null")
    page.evaluate("window.savedVerifierTile=document.querySelector('#verifier-agent-card [data-attempt=code-review]'); fixture.snapshot.overview.verifier.effort='max'; fixture.snapshot.status.run_revision+=1")
    page.click("#refresh-workbench")
    page.wait("document.querySelector('#verifier-agent-card').textContent.includes('max')")
    assert page.evaluate("window.savedVerifierTile === document.querySelector('#verifier-agent-card [data-attempt=code-review]')")
    assert page.evaluate("document.querySelector('#grounding-agent-card [data-attempt=ground-review]') !== null")


def test_verifier_card_shows_probe_coverage_and_failure_attribution(browser):
    page = browser
    page.evaluate("""fixture.snapshot.overview.verifier={state:'idle',model:'mimo-v2.6-pro',effort:'low',experts:[{
      expert:'author-flash',deliveries:2,triggers:2,reuse:0,first_pass:{passed:1,completed:1},blockers:0,advisories:0,
      verification_failures:1,invocation_failures:2,protocol_rejections:3,stage_seconds:{inspect:10,review:4},findings:[],
      model_runtime:{exchanges:[{sent:{model:'mimo-v2.6-pro',effort:'low',max_tokens:8000,thinking_sent:false},served_models:['mimo-v2.6-pro'],stop_reason:'end_turn'}]},
      recent:[{revisions:[{experiment_id:'checked-source',state:'inconclusive',stages:[],probe:{results:{checks:[{id:'actual-gradient',status:'ERROR'}]}},
        reports:[{report_ref:'review-1',verdict:'inconclusive',summary:'Missing CUDA evidence',coverage:{covered:[],uncovered:[{condition:'CUDA gradient',reason:'CPU only',blocking:true}]}}]}]}]
    }]};fixture.snapshot.status.run_revision+=1;""")
    page.click('#refresh-workbench')
    page.wait("document.querySelector('#verifier-agent-card').textContent.includes('actual-gradient')")
    text = page.evaluate("document.querySelector('#verifier-agent-card').textContent")
    assert all(value in text for value in ('author-flash', 'ERROR', 'CPU only', '8000', 'end_turn'))
