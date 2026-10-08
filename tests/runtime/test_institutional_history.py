"""Best-effort log evidence stays separate from caches, PDF archives and reads."""
import json
import os

from hypoloom.observer_summary import institutional_history


def test_existing_log_projection_is_bounded_scoped_and_does_not_write(tmp_path):
    log = tmp_path / 'events.jsonl'
    event = {'run_id': 'r', 'type': 'literature_access', 'payload': {
        'provider': 'IEEE', 'to': 'available', 'reason_code': 'document_verified'}}
    records = [event, {**event, 'run_id': 'other'}, {**event, 'payload': {
        'provider': 'Springer Nature', 'to': 'sample_verified', 'reason_code': 'sample_verified'}},
        {**event, 'type': 'something_else'}, None, event]
    raw = b'bad json\n' + b'\n'.join(json.dumps(e).encode() for e in records) + b'\n{"partial":'
    log.write_bytes(raw)
    before = log.stat()
    assert institutional_history(str(log), 'r') == {
        'state': 'observed', 'tail_only': False, 'document_verified': {'IEEE': 2}}
    tail = institutional_history(str(log), 'r', max_bytes=len(json.dumps(event)) + 40)
    assert tail == {'state': 'observed', 'tail_only': True, 'document_verified': {'IEEE': 1}}
    assert log.read_bytes() == raw and log.stat().st_mtime_ns == before.st_mtime_ns


def test_missing_log_is_unknown_and_nonregular_files_are_not_read(tmp_path):
    missing = tmp_path / 'missing'
    fifo = tmp_path / 'fifo'; os.mkfifo(fifo)
    link = tmp_path / 'link'; link.symlink_to(fifo)
    for path in (None, str(missing), str(fifo), str(link), str(tmp_path), 'relative'):
        assert institutional_history(path, 'r') == {'state': 'unknown', 'document_verified': {}}
