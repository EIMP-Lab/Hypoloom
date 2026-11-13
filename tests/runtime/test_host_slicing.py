"""Control mutations must invalidate run heads captured by a prior slice."""
from hypoloom.control_protocol import ControlRequest
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_monitor import _v4_payload


def test_cancel_between_run_dispatches_cannot_dispatch_a_stale_active_head(tmp_path, monkeypatch):
    host = WorkflowHost(tmp_path / 'state')
    host.initialize()
    monkeypatch.setattr(host, '_cycle_reports_enabled', lambda run: False)
    clock, dispatched, stopping = [100.0], [], []
    monkeypatch.setattr('hypoloom.workflow_host.time.monotonic', lambda: clock[0])
    try:
        for run in ('alpha', 'beta'):
            result = host.handle(ControlRequest(1, 'create-' + run, 0, 'CREATE_RUN', _v4_payload(run)))
            assert result.status == 'accepted'
        host.tick()
        assert all(host.store.latest_transition(run)['state'] == 'ACTIVE' for run in ('alpha', 'beta'))
        def drive(run):
            dispatched.append(run['run_id'])
            clock[0] += 0.3
        monkeypatch.setattr(host, '_drive_active', drive)
        monkeypatch.setattr(host, '_drive_stopping', stopping.append)
        assert host.tick()
        assert dispatched == ['alpha']
        assert not host.store._db.in_transaction
        head = host.store.latest_transition('beta')
        cancelled = host.handle(ControlRequest(1, 'cancel-beta', head['revision'], 'CANCEL',
            {'run_id': 'beta', 'cause': 'slice regression'}))
        assert cancelled.status == 'accepted'
        host.tick()
        assert dispatched == ['alpha'] and stopping == ['beta']
    finally:
        host.close()
