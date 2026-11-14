import json
import sqlite3

from hypoloom.verification_observation import read_verification_view, verification_view
from test_verification_observation import records, contract


def test_paged_history_preserves_full_counts_without_decoding_source_or_probe(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE workflow_commands(command_id TEXT PRIMARY KEY,run_id TEXT,command_type TEXT,request_json TEXT);
        CREATE TABLE execution_attempts(attempt_id TEXT PRIMARY KEY,command_id TEXT,run_id TEXT,experiment_id TEXT,
            execution_id TEXT,kind TEXT,state TEXT,started_at REAL,ended_at REAL,terminal_reason TEXT,observation_json TEXT);
    """)
    for index in range(2000):
        command, attempt = records(index, phase="review", verdict="passed" if index % 2 else "inconclusive")
        observation = attempt["observation"]
        observation["request"]["unused_source"] = "discarded-large-captured-source" * 80
        observation["response"]["typed_candidate"]["probe"] = {"files": {"verification/probe.py": "discarded-large-probe" * 100}}
        # An old unresolved finding must retain its message even when no recent
        # reports contain findings. Its count must survive paging as well.
        if index == 0:
            observation["response"]["typed_candidate"]["findings"] = [{"id": "old", "severity": "advisory", "message": "Earlier measured hot loop", "resolution_condition": "Profile the loop"}]
        db.execute("INSERT INTO workflow_commands VALUES(?,?,?,?)", (command["command_id"], "r", "REQUEST_AUXILIARY", command["request_json"]))
        db.execute("INSERT INTO execution_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?)", (attempt["attempt_id"], attempt["command_id"], "r",
            f"experiment-{index}", None, "AGENT", "CLOSED", index, index+1, "SUCCEEDED", json.dumps(observation)))
    import hypoloom.verification_observation as module
    original = module._object
    decoded = []
    def traced(value):
        if isinstance(value, str):
            assert "discarded-large" not in value
            decoded.append(len(value))
        return original(value)
    monkeypatch.setattr(module, "_object", traced)
    view = read_verification_view(db, "r", contract(), offset=100, limit=5)
    subject = view["experts"][0]
    assert subject["deliveries"] == 2000
    assert subject["first_pass"] == {"passed": 1000, "completed": 1000}
    assert subject["verification_failures"] == 1000
    assert len(subject["recent"]) == 5 and len(json.dumps(view)) < 20000
    assert subject["findings"][0]["message"] == "Earlier measured hot loop"
    assert subject["advisories"] == 1
    assert view["detail_attempt_count"] <= 11
    assert max(decoded) < 4096
    db.close()


def test_protocol_and_provider_failures_are_not_author_defects():
    command, attempt = records(1, phase="inspect", verdict="probe_required")
    attempt.update(terminal_reason="INVALID_OUTPUT")
    attempt["observation"] = {"request": {"command": "VERIFY_IMPLEMENTATION"}, "failure_code": "invalid_candidate",
        "diagnostics": {"structured_output_correction": {"rejected_count": 3}, "model_runtime": {"status": "unknown"}}}
    final_command, final = records(2, verdict="inconclusive", delivery="delivery-1")
    view = verification_view(contract(), [command, final_command], [attempt, final], now=10)
    subject = view["experts"][0]
    assert subject["first_pass"] == {"passed": 0, "completed": 0}
    assert subject["blockers"] == 0
    assert subject["protocol_rejections"] == 3 and subject["invocation_failures"] == 1
    assert subject["verification_failures"] == 1
    assert subject["stage_seconds"]["inspect"] == 2


def test_runtime_observation_distinguishes_omitted_thinking_from_disabled():
    from hypoloom.fake_agent import default_request
    from hypoloom.domain import AgentCommand
    from hypoloom.provider_observation import model_runtime_observation
    request = default_request(AgentCommand.VERIFY_IMPLEMENTATION)
    unknown = model_runtime_observation(request, None)
    assert unknown["status"] == "unknown" and unknown["exchanges"] == []
    wire = {"model": "mimo-v2.6-pro", "effort": "low", "max_tokens": 8000, "thinking_sent": False, "thinking_type": None}
    value = model_runtime_observation(request, [{"request_path": "/v1/messages", "request_parameters": wire,
        "served_models": ["mimo-v2.6-pro"], "stop_reason": "max_tokens", "started_at": 1.0, "ended_at": 2.5}])
    assert value["exchanges"][0]["sent"] == wire
    assert value["exchanges"][0]["stop_reason"] == "max_tokens"
    assert value["exchanges"][0]["seconds"] == 1.5
    assert "disabled" not in json.dumps(value)
