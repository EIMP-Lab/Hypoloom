"""Real native CLI, broker, sandbox Process, publication and independent audit."""
import json
import sqlite3

from hypoloom.verification import ImplementationVerification
import test_full_cc_host_g3 as native


def test_native_verifier_has_independent_sessions_and_releases_resources(tmp_path, monkeypatch):
    monkeypatch.setattr(ImplementationVerification, "enabled", lambda self, run: True)
    native._exercise_full_cc_host(tmp_path, timeout_seconds=240, implementation_probes=2)
    with sqlite3.connect(f"file:{tmp_path / 'state/state.sqlite'}?mode=ro", uri=True) as db:
        rows = db.execute("SELECT observation_json FROM execution_attempts WHERE kind='AGENT' "
            "AND json_extract(observation_json,'$.request.command')='VERIFY_IMPLEMENTATION'").fetchall()
        probes = db.execute("SELECT state,terminal_reason FROM execution_attempts WHERE execution_id LIKE '%--implementation-probe'").fetchall()
    observations = [json.loads(r[0]) for r in rows]
    assert len(observations) == 4
    assert len({o['session']['session_id'] for o in observations}) == 4
    assert all(o['session']['parent_session_id'] is None for o in observations)
    assert all(not any(i['ref'].endswith(('--expert-resources', '--goal', '--hypothesis')) for i in o['request']['input_artifacts']) for o in observations)
    assert probes == [('CLOSED', 'SUCCEEDED')] * 2
