"""Real Host/CLI/CPU closure must not wait for the provider copy's final file."""
import threading
import time

from hypoloom.claude_agent_host import ClaudeAgentHost
from hypoloom.runtime_diagnostics import DeferredProviderCapture
from test_successful_agent_diagnostics_g3 import (
    test_successful_host_design_retains_native_correction_trace_and_closes_cpu as _close_cpu,
)


def test_live_usage_closes_before_blocked_provider_files(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    write, observe = DeferredProviderCapture._write, ClaudeAgentHost._observe
    checked = []
    def blocked(self, kind, payload):
        if not release.is_set():
            entered.set()
            assert release.wait(30), 'Agent closure waited for provider diagnostic I/O'
        return write(self, kind, payload)
    def final(self, handle, *args, **kwargs):
        first = not release.is_set()
        try:
            started = time.monotonic()
            result = observe(self, handle, *args, **kwargs)
            if first:
                assert entered.is_set()
                assert time.monotonic() - started < 5
                usage = result.diagnostics['provider_observed_usage']
                assert usage['completed_requests'] > 0 and usage['total_tokens'] > 0
                assert not (handle._runtime_diagnostics.root / 'provider').exists()
                checked.append(handle.request.attempt_id)
            return result
        finally:
            release.set()
    monkeypatch.setattr(DeferredProviderCapture, '_write', blocked)
    monkeypatch.setattr(ClaudeAgentHost, '_observe', final)
    try:
        _close_cpu(tmp_path, monkeypatch)
        assert len(checked) == 1
        assert not any(thread.name == 'hypoloom-provider-diagnostics' and thread.is_alive()
                       for thread in threading.enumerate())
    finally:
        release.set()
