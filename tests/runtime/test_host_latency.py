"""Reuse verified inputs while preserving drift, time and commit boundaries."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from hypoloom import workflow_host
from hypoloom.control_protocol import ControlRequest
from hypoloom.host_settings import HostConfigurationError


def _runtime(tmp_path, monkeypatch):
    package = tmp_path / 'hypoloom'
    package.mkdir()
    source = package / 'worker.py'
    source.write_text('original bytes\n')
    binary = tmp_path / 'native'
    binary.write_bytes(b'x' * (1024 * 1024))
    monkeypatch.setattr(workflow_host, '__file__', str(package / 'workflow_host.py'))
    return source, binary


def test_unchanged_host_identity_reuses_bytes_but_direct_checks_still_read(tmp_path, monkeypatch):
    source, binary = _runtime(tmp_path, monkeypatch)
    original, reads = Path.open, []
    def counted(path, *args, **kwargs):
        if path == binary and args == ('rb',): reads.append(1)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', counted)
    cache = {}
    first = workflow_host.installed_run_version(runtime_files=(binary,), _identity_cache=cache)
    assert workflow_host.installed_run_version(runtime_files=(binary,), _identity_cache=cache) == first
    assert len(reads) == 1
    assert workflow_host.installed_run_version(runtime_files=(binary,)) == first
    assert len(reads) == 2
    changed = workflow_host.installed_run_version({'test': 'changed'}, runtime_files=(binary,), _identity_cache=cache)
    assert changed.runtime_environment_id == first.runtime_environment_id
    assert changed.configuration_id != first.configuration_id


@pytest.mark.parametrize('change', ['same-size-restored-mtime', 'inode', 'add', 'remove'])
def test_runtime_cache_sees_file_edits_and_inventory_changes(tmp_path, monkeypatch, change):
    source, binary = _runtime(tmp_path, monkeypatch)
    cache = {}
    before = workflow_host.installed_run_version(runtime_files=(binary,), _identity_cache=cache)
    metadata = source.stat()
    if change == 'same-size-restored-mtime':
        source.write_bytes(b'changed bytes!\n')
        assert source.stat().st_size == metadata.st_size
        os.utime(source, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    elif change == 'inode':
        replacement = source.with_suffix('.new')
        replacement.write_bytes(b'changed bytes!\n')
        os.utime(replacement, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        replacement.replace(source)
    elif change == 'add': (source.parent / 'extra.py').write_text('new package input\n')
    else: source.unlink()
    after = workflow_host.installed_run_version(runtime_files=(binary,), _identity_cache=cache)
    assert after.runtime_environment_id != before.runtime_environment_id
    assert after == workflow_host.installed_run_version(runtime_files=(binary,))


@pytest.mark.parametrize('change', ['permissions', 'symlink', 'invalid-json'])
def test_runtime_cache_never_skips_private_settings_validation(tmp_path, monkeypatch, change):
    _, binary = _runtime(tmp_path, monkeypatch)
    private = tmp_path / 'settings.json'
    private.write_text('{}'); private.chmod(0o600)
    cache = {}
    options = dict(runtime_files=(binary, private), private_settings_path=private, _identity_cache=cache)
    workflow_host.installed_run_version(**options)
    if change == 'permissions': private.chmod(0o644)
    elif change == 'symlink':
        target = tmp_path / 'moved.json'
        private.rename(target); private.symlink_to(target)
    else: private.write_text('PRIVATE_INVALID_SETTINGS')
    with pytest.raises(HostConfigurationError) as caught:
        workflow_host.installed_run_version(**options)
    assert 'PRIVATE_INVALID_SETTINGS' not in str(caught.value)


def test_runtime_change_during_full_verification_is_not_cached(tmp_path, monkeypatch):
    source, binary = _runtime(tmp_path, monkeypatch)
    original = Path.open
    def changing(path, *args, **kwargs):
        if path == binary and args == ('rb',): source.write_text('changed while reading\n')
        return original(path, *args, **kwargs)
    cache = {}
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', changing)
        with pytest.raises(HostConfigurationError, match='changed'):
            workflow_host.installed_run_version(runtime_files=(binary,), _identity_cache=cache)
    assert workflow_host.installed_run_version(runtime_files=(binary,), _identity_cache=cache) == workflow_host.installed_run_version(runtime_files=(binary,))


def test_runtime_cache_sees_native_binary_changes_and_missing_input(tmp_path, monkeypatch):
    _, binary = _runtime(tmp_path, monkeypatch)
    cache = {}
    before = workflow_host.installed_run_version(runtime_files=(binary,), _identity_cache=cache)
    metadata = binary.stat()
    binary.write_bytes(b'y' * metadata.st_size)
    os.utime(binary, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    after = workflow_host.installed_run_version(runtime_files=(binary,), _identity_cache=cache)
    assert before.runtime_environment_id != after.runtime_environment_id
    assert after == workflow_host.installed_run_version(runtime_files=(binary,))
    binary.unlink()
    with pytest.raises(FileNotFoundError):
        workflow_host.installed_run_version(runtime_files=(binary,), _identity_cache=cache)


def _closed_attempt(host, identity, ordinal, *, failure=None, started=1., ended=2.):
    command = host.store.inspect('SELECT command_id FROM workflow_commands ORDER BY rowid LIMIT 1')[0]['command_id']
    with host.store.transaction() as tx:
        tx.open_attempt({'attempt_id': identity, 'run_id': 'run', 'workflow_id': 'run',
            'command_id': command, 'kind': 'AGENT', 'attempt_ordinal': ordinal,
            'host_generation': host.generation, 'started_at': started, 'observation': {'request': {
                'run_id': 'run', 'attempt_id': identity, 'role': 'researcher', 'command': 'RESULT_ANALYSIS',
                'budget': {'max_tokens': 100, 'max_turns': 3, 'max_cost': '0.5'}}}})
        tx.close_attempt(identity, terminal_reason='FAILED' if failure else 'SUCCEEDED', ended_at=ended,
            payload={'diagnostics': {'provider_failure': failure}} if failure else {
                'response': {'token_usage': {'total_tokens': 32}, 'turn_count': 1, 'cost': '0.1'}})


def test_status_reuses_committed_totals_but_refreshes_traffic_and_sees_writes(tmp_path, monkeypatch):
    from test_web_observer import _create_fresh_run
    from hypoloom.provider_traffic import for_run
    _create_fresh_run(tmp_path, 'run')
    host = workflow_host.WorkflowHost(tmp_path)
    try:
        host.initialize()
        host.claude_agent_host = SimpleNamespace()
        _closed_attempt(host, 'a', 1)
        calls, original = [], workflow_host.build_status_projection
        def counted(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)
        monkeypatch.setattr(workflow_host, 'build_status_projection', counted)
        request = ControlRequest(1, 'status', None, 'STATUS', {'run_id': 'run'})
        first = host.handle(request).result
        first['agent_usage']['observed_lower_bound']['tokens'] = 999
        traffic = for_run(host, 'run')
        from test_provider_traffic import acquire, success
        ticket = acquire(traffic)
        live = host.handle(request).result
        assert live['provider_traffic']['rpm'] == live['provider_traffic']['in_flight'] == 1
        assert live['agent_usage']['observed_lower_bound']['tokens'] == 32
        assert len(calls) == 1
        host._role_view_jobs['pending'] = object()
        assert host.handle(request).result['quiescent_for_backup'] is False
        host._role_view_jobs.clear()
        assert host.handle(request).result['quiescent_for_backup'] is True
        traffic.finish(ticket, success())
        _closed_attempt(host, 'b', 2)
        updated = host.handle(request).result
        assert updated['provider_traffic']['in_flight'] == 0
        assert updated['agent_usage']['observed_lower_bound']['tokens'] == 64
        assert len(calls) == 2
        with pytest.raises(RuntimeError, match='rollback'):
            with host.store.transaction() as tx:
                tx.record_command({'command_id': 'rolled-back', 'run_id': 'run', 'workflow_id': 'run',
                    'command_type': 'STATUS', 'request': {'run_id': 'run'}})
                raise RuntimeError('rollback')
        assert not host.store.inspect("SELECT command_id FROM workflow_commands WHERE command_id='rolled-back'")
        assert host.handle(request).result['agent_usage'] == updated['agent_usage']
    finally:
        host.claude_agent_host = None
        host.close()


def test_retry_history_reused_while_clock_advances_and_commits_replace_it(tmp_path, monkeypatch):
    from test_web_observer import _create_fresh_run
    from hypoloom import host_settings, provider_observation
    _create_fresh_run(tmp_path, 'run')
    host = workflow_host.WorkflowHost(tmp_path)
    clock, scans = [110.], []
    monkeypatch.setattr(host_settings, 'time', SimpleNamespace(time=lambda: clock[0]))
    original = provider_observation.provider_retry_history
    def counted(*args, **kwargs):
        scans.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(provider_observation, 'provider_retry_history', counted)
    request = ControlRequest(1, 'status', None, 'STATUS', {'run_id': 'run'})
    try:
        host.initialize()
        _closed_attempt(host, 'failed', 1, started=90., ended=100.,
            failure={'code': 'rate_limited', 'http_status': 429, 'retry_at': 180.})
        first = host.handle(request).result['provider_retry']
        assert first['state'] == 'cooldown' and first['pending']
        assert first['next_retry_at'] == 180.
        clock[0] = 181.
        current = host.handle(request).result['provider_retry']
        assert current['now'] == 181. and current['state'] == 'recovering' and not current['pending']
        assert first['gates'][0]['pending'] and not current['gates'][0]['pending']
        assert len(scans) == 1
        _closed_attempt(host, 'recovered', 2, started=181., ended=182.)
        recovered = host.handle(request).result['provider_retry']
        assert recovered['state'] == 'ready' and recovered['recovered_at'] == 182.
        assert recovered['gates'] == [] and len(scans) == 2
    finally:
        host.close()


def test_real_host_socket_status_interleaves_with_ticks(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from threading import Event, Thread
    from hypoloom.control_protocol import request_over_socket
    from test_web_observer import _create_fresh_run
    _create_fresh_run(tmp_path, 'run')
    responses, errors, ticks = [], [], []
    tick_done = Event()
    hosts = []
    class ObservedHost(workflow_host.WorkflowHost):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            hosts.append(self)
        def handle(self, request):
            response = super().handle(request)
            if request.request_id == 'status-5': self.stop_requested = True
            return response
        def tick(self):
            assert self._runtime_version() == self.version
            super().tick()
            ticks.append(1)
            tick_done.set()
    def client():
        try:
            for index in range(6):
                tick_done.clear()
                responses.append(request_over_socket(tmp_path / 'control.sock',
                    ControlRequest(1, f'status-{index}', None, 'STATUS', {'run_id': 'run'})))
                if index < 5: assert tick_done.wait(5)
        except BaseException as exc:
            errors.append(exc)
            hosts[0].stop_requested = True
    @contextmanager
    def services():
        thread = Thread(target=client)
        thread.start()
        try: yield
        finally: thread.join(6)
    monkeypatch.setattr(workflow_host, 'WorkflowHost', ObservedHost)
    monkeypatch.setattr(workflow_host.signal, 'signal', lambda *args: None)
    assert workflow_host.serve(tmp_path, services_factory=services) == 0
    assert not errors and len(responses) == 6 and len(ticks) >= 5
    assert all(response.status == 'accepted' and response.result['run_id'] == 'run' for response in responses)
    assert not (tmp_path / 'control.sock').exists()
