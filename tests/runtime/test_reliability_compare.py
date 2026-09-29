import json
import sqlite3

from hypoloom.cli import main
from hypoloom.reliability_compare import compare_roots
from tests.runtime.test_observer_summary import _attempt


def _root(path, model, *, cost="0.5", terminal="SUCCEEDED", captured=True):
    path.mkdir()
    row = _attempt("call", reason=terminal)
    observation = json.loads(row["observation_json"])
    observation["request"]["required_model_identity"] = "selector-common"
    observation["request"]["budget"] = {"max_tokens": 1000, "max_turns": 10, "max_cost": "1"}
    observation["diagnostics"] = {"served_models": [model], "provider_subtype": "success",
        "provider_accounting": {"cost": cost, "cost_currency": "USD", "turn_count": 2,
            "token_usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}}}
    if cost is None:
        observation["diagnostics"].pop("provider_accounting")
        observation["request"]["budget"]["max_cost"] = None
    observation.pop("response", None)
    connection = sqlite3.connect(path / "state.sqlite")
    connection.executescript("CREATE TABLE workflow_runs(run_id TEXT); INSERT INTO workflow_runs VALUES('r');"
        "CREATE TABLE execution_attempts(attempt_id TEXT,run_id TEXT,workflow_id TEXT,command_id TEXT,kind TEXT,attempt_ordinal INTEGER,state TEXT,terminal_reason TEXT,started_at REAL,ended_at REAL,observation_json TEXT);"
        "CREATE TABLE workflow_transitions(run_id TEXT,workflow_kind TEXT,revision INTEGER,state TEXT,created_at TEXT,payload_json TEXT);"
        "INSERT INTO workflow_transitions VALUES('r','RUN',1,'COMPLETED','2026-09-10T00:00:10Z','{}');"
        "CREATE TABLE research_facts(run_id TEXT,fact_type TEXT,created_at TEXT,payload_json TEXT);")
    connection.execute("INSERT INTO execution_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("call", "r", "r", "node", "AGENT", 1, "CLOSED", terminal, 0, 10, json.dumps(observation)))
    connection.commit()
    connection.close()
    if captured:
        directory = path / "diagnostics/agents/r/call/provider/000001"
        directory.mkdir(parents=True)
        (directory / "exchange.json").write_text(json.dumps({"served_models": [model], "upstream_status": 200,
            "response_body_complete": True, "message_complete": True, "request_path": "/v1/messages",
            "started_at": 1, "first_content_at": 2, "ended_at": 4}))
    return path


def test_two_served_models_remain_separate_despite_same_selector_and_sources_stay_readonly(tmp_path, capsys):
    roots = [_root(tmp_path / "a", "model-a"), _root(tmp_path / "b", "model-b", terminal="FAILED", cost=None, captured=False)]
    before = [(root / "state.sqlite").read_bytes() for root in roots]
    result = compare_roots([*roots, roots[0]])
    assert len(result["sources"]) == 2
    a, b = result["groups"]
    assert a["served_models"] == ["model-a"] and b["served_models"] == ["model-b"]
    assert a["metrics"]["node_delivery"]["numerator"] == 1
    assert a["exchanges"]["ttft_seconds"]["p50"] == 1
    assert b["unknown_accounting_attempts"] == b["exchanges"]["unknown_attempts"] == 1
    assert b["per_delivered_node"]["cost_usd"] is None
    assert a["cache_usage"]["cache_read_input_tokens"]["unknown_attempts"] == 1
    assert main(["reliability-compare", "--state-root", str(roots[0]), "--state-root", str(roots[1]), "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["groups"]) == 2
    assert main(["reliability-compare", "--state-root", str(roots[0]), "--state-root", str(roots[1])]) == 0
    rendered = capsys.readouterr().out
    assert "Reported tokens" in rendered and "Tokens/delivery" in rendered
    assert "USD" not in rendered
    assert before == [(root / "state.sqlite").read_bytes() for root in roots]


def test_preflight_failure_and_unknown_model_do_not_become_a_known_model_failure(tmp_path):
    root = _root(tmp_path / "run", "known")
    with sqlite3.connect(root / "state.sqlite") as connection:
        raw = connection.execute("SELECT observation_json FROM execution_attempts").fetchone()[0]
        observation = json.loads(raw)
        observation["diagnostics"] = {}
        observation["failure_stage"] = "literature_retrieval"
        observation["failure_code"] = "literature_openalex_timeout"
        connection.execute("INSERT INTO execution_attempts SELECT 'preflight',run_id,workflow_id,command_id,kind,2,state,'FAILED',11,12,? FROM execution_attempts LIMIT 1", (json.dumps(observation),))
    group = compare_roots([root])["groups"][0]
    assert group["served_models"] == ["known"] and group["unknown_model"] is False
    assert group["exchanges"]["preflight_only_attempts"] == 1
    assert group["exchanges"]["unknown_attempts"] == 0
    with sqlite3.connect(root / "state.sqlite") as connection:
        observation.pop("failure_stage")
        observation["failure_code"] = "provider_failed"
        connection.execute("UPDATE execution_attempts SET observation_json=? WHERE attempt_id='preflight'", (json.dumps(observation),))
    group = compare_roots([root])["groups"][0]
    assert group["model_label"] == "known + unknown" and group["unknown_model"] is True
    assert group["exchanges"]["preflight_only_attempts"] == 0
