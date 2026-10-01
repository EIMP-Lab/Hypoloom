from threading import Event

import pytest

from hypoloom.institutional_access import InstitutionalAccess, MAX_REQUESTS
from tests.runtime.test_institutional_access import Backend, SCOPE, URL, SECOND, document, pump


def test_background_auth_finishes_without_operator_and_does_not_reopen_for_new_papers():
    backend = Backend()
    access = InstitutionalAccess(backend)
    try:
        access.request(SCOPE, URL)
        pump(access, lambda: access.peek(SCOPE, URL).get("status") == "unavailable")
        assert access.peek(SCOPE, URL)["reason"] == "login_required"
        assert access.request(SCOPE, SECOND)["status"] == "unavailable"
        access.tick({"run"})
        assert len(backend.calls) == 1
        assert access.status("run")["pending"] == 0
        backend.state = "available"
        access.control("run", "Nature", "retry")
        pump(access, lambda: access.status("run")["available"] == 2)
    finally:
        access.close()


def test_deadline_expires_queued_items_even_while_worker_is_busy_and_discards_late_pdf():
    entered, release, clock = Event(), Event(), [0.]
    class Slow(Backend):
        def fetch(self, url, **kwargs):
            entered.set()
            assert release.wait(5)
            return {"state": "available", "document": document(url)}
    access = InstitutionalAccess(Slow(), clock=lambda: clock[0])
    try:
        for n in range(MAX_REQUESTS):
            access.request(SCOPE, URL + str(n))
        access.tick({"run"})
        assert entered.wait(1)
        clock[0] = 121
        access.tick({"run"})
        assert access.status("run")["pending"] == 0
        assert access.peek(SCOPE, URL + "0")["reason"] == "acquisition_timeout"
        assert access.peek(SCOPE, URL + "1")["reason"] == "queue_timeout"
        assert access.request(SCOPE, SECOND)["status"] == "pending"
        release.set()
        pump(access, lambda: access._flight is None or access.status("run")["available"] > 0)
        assert access.peek(SCOPE, URL + "1")["status"] == "unavailable"
        assert all(p.document is None for p in access._items.values() if p.url != SECOND)
    finally:
        release.set()
        access.close()


def test_new_papers_and_repeated_calls_never_extend_manual_window():
    clock = [0.]
    access = InstitutionalAccess(Backend(), clock=lambda: clock[0])
    try:
        access.request(SCOPE, URL)
        access.control("run", "Nature", "open")
        pump(access, lambda: access.peek(SCOPE, URL)["reason"] == "login_required")
        assert access.peek(SCOPE, URL)["status"] == "pending"
        clock[0] = 550
        access.request(SCOPE, SECOND)
        assert access._items[(*SCOPE, SECOND)].deadline == 600
        access.request(SCOPE, URL)
        clock[0] = 601
        access.tick({"run"})
        assert access.status("run")["pending"] == 0
        assert access.request(SCOPE, URL)["status"] == "unavailable"
        assert access.request(SCOPE, SECOND)["status"] == "unavailable"
    finally:
        access.close()


def test_retry_replaces_inflight_request_instead_of_admitting_old_result():
    entered, release = Event(), Event()
    class Slow(Backend):
        def fetch(self, url, **kwargs):
            if not self.calls:
                self.calls.append(url)
                entered.set()
                assert release.wait(5)
                return {"state": "available", "document": document(url)}
            return {"state": "access_denied"}
    access = InstitutionalAccess(Slow())
    try:
        access.request(SCOPE, URL)
        access.tick({"run"})
        assert entered.wait(1)
        old = access._items[(*SCOPE, URL)]
        access.control("run", "Nature", "retry", url=URL)
        assert access._items[(*SCOPE, URL)] is not old
        release.set()
        pump(access, lambda: access.peek(SCOPE, URL).get("reason") == "access_denied")
        assert access.status("run")["available"] == 0
    finally:
        release.set()
        access.close()


@pytest.mark.parametrize("options", [{"acquisition_seconds": 0}, {"acquisition_seconds": float("inf")},
    {"acquisition_seconds": 601}, {"manual_seconds": float("nan")}, {"manual_seconds": 59}])
def test_deadlines_must_be_finite_and_bounded(options):
    with pytest.raises(ValueError):
        InstitutionalAccess(Backend(), **options)
