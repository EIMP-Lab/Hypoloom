"""Optional, disposable browser acquisitions; source admission stays in paper_sources.

The queue is not a research fact store. Requests can be repeated after restart;
only documents consumed by the ordinary web tool enter the existing archive.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from html.parser import HTMLParser
from math import ceil, isfinite
from threading import RLock
from time import monotonic
from urllib.parse import urlsplit, urlunsplit
import re


PROVIDERS = {
    "nature.com": "Nature", "link.springer.com": "Springer",
    "ieeexplore.ieee.org": "IEEE", "sciencedirect.com": "ScienceDirect",
    "dl.acm.org": "ACM", "onlinelibrary.wiley.com": "Wiley",
    "journals.sagepub.com": "SAGE", "academic.oup.com": "Oxford",
    "tandfonline.com": "Taylor & Francis",
}
DOI_PROVIDERS = {"10.1038": "Nature", "10.1007": "Springer", "10.1109": "IEEE",
    "10.1016": "ScienceDirect", "10.1145": "ACM", "10.1002": "Wiley", "10.1111": "Wiley",
    "10.1177": "SAGE", "10.1093": "Oxford", "10.1080": "Taylor & Francis"}
MAX_DOCUMENT_BYTES = 24 * 1024 * 1024
MAX_CACHE_BYTES = 64 * 1024 * 1024
MAX_REQUESTS = 32
AUTH_WAIT_STATES = {"login_required", "verification_required", "access_required"}
WAIT_STATES = AUTH_WAIT_STATES | {"browser_unavailable", "fetch_unavailable", "access_required"}
_STATES = WAIT_STATES | {"available", "access_denied", "no_full_text", "identity_unconfirmed", "failed"}
TRANSIENT_FETCH_REASONS = {"network_changed", "dns_failed", "connection_failed",
    "navigation_timeout", "network_error", "redirect_unresolved", "http_server_error", "http_rate_limited", "pdf_response_not_pdf"}
_REASONS = TRANSIENT_FETCH_REASONS | {"request_blocked", "http_error"}


def publisher(url: str) -> str | None:
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443):
            return None
        host = (parts.hostname or "").lower()
        if host == "doi.org":
            return DOI_PROVIDERS.get(parts.path.lstrip("/").split("/")[0])
        return next((name for domain, name in PROVIDERS.items()
                     if host == domain or host.endswith("." + domain)), None)
    except (TypeError, ValueError):
        return None


def article_url(url: str) -> str:
    """Reject credential-bearing URLs; publisher article identity lives in the path."""
    if publisher(url) is None:
        raise ValueError("unsupported institutional publisher URL")
    parts = urlsplit(url)
    if not parts.path or parts.path == "/" or parts.query or parts.fragment:
        raise ValueError("use a canonical article/PDF URL without query parameters")
    return urlunsplit(("https", parts.netloc.lower(), parts.path, "", ""))


def scope_for(request) -> tuple[str, str, str]:
    return request.run_id, request.role, request.component_id


def public_full_text(document: dict) -> bool:
    """Positive article structure, not the presence of a login link or a 403."""
    if document.get("media_type") == "application/pdf":
        return True
    class Article(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.headings = []
            self.text = []
            self.doi = False

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "meta" and attrs.get("name", "").lower() == "citation_doi" and attrs.get("content"):
                self.doi = True
            classes = attrs.get("class", "").split()
            selected = (tag == "article" or bool(set(classes) & {"c-article-body", "article-sections", "article-body"})
                        or attrs.get("id") in {"body", "bodymatter"})
            if tag not in {"meta", "link", "br", "hr", "img", "input", "source", "wbr", "area", "base", "embed", "param", "col", "track"}:
                self.stack.append((tag, selected))

        def handle_endtag(self, tag):
            for n in range(len(self.stack) - 1, -1, -1):
                if self.stack[n][0] == tag:
                    del self.stack[n:]
                    break

        def handle_data(self, data):
            if any(selected for _, selected in self.stack) and not any(tag in {"script", "style"} for tag, _ in self.stack):
                self.text.append(data)
                if any(tag in {"h2", "h3"} for tag, _ in self.stack):
                    self.headings.append(data)
    parsed = Article()
    parsed.feed(document["content"].decode("utf-8", errors="replace"))
    headings = " ".join(parsed.headings)
    return bool(parsed.doi and sum(map(len, parsed.text)) > 2500
                and re.search(r"methods?|results?|discussion|introduction", headings, re.I)
                and re.search(r"references|bibliography", headings, re.I))


def _doi(value: str) -> str:
    return value.lower().removeprefix("doi:").removeprefix("https://doi.org/").strip()


def identity_from_url(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path.removesuffix(".pdf")
    if parts.hostname == "doi.org":
        return "doi:" + path.lstrip("/")
    if publisher(url) == "Nature" and path.startswith("/articles/"):
        return "doi:10.1038/" + path.removeprefix("/articles/")
    match = re.search(r"/(10\.\d{4,9}/.+)$", path)
    return "doi:" + match.group(1) if match else ""


def pdf_identity(pages) -> str:
    """Identify downloaded bytes from their first physical page, not a landing page."""
    if not pages:
        return ""
    # Later reference lists cannot identify the downloaded article. The first
    # DOI is commonly in the publication header; missing identity stays unconfirmed.
    first = re.split(r"(?im)^\s*(?:references|bibliography)\s*$", pages[0], maxsplit=1)[0]
    found = re.search(r"10\.\d{4,9}/[-\w.();/:]+", first, re.I)
    value = found.group(0).rstrip(".,;").lower() if found else ""
    while value.endswith(")") and value.count(")") > value.count("("):
        value = value[:-1]
    return value


def validate_document(document: dict, *, title: str = "", identity: str = "") -> dict:
    content, pages = document.get("content"), document.get("pages")
    if (not isinstance(content, bytes) or not content or len(content) > MAX_DOCUMENT_BYTES
            or not isinstance(pages, (tuple, list)) or not 1 <= len(pages) <= 2000
            or not all(isinstance(page, str) for page in pages)
            or sum(len(page.encode()) for page in pages) > MAX_DOCUMENT_BYTES):
        raise ValueError("invalid or oversized institutional document")
    if document.get("media_type") == "application/pdf":
        if not content.startswith(b"%PDF-") or not content.rstrip().endswith(b"%%EOF"):
            raise ValueError("not a complete PDF")
    elif document.get("media_type") != "text/plain":
        raise ValueError("institutional HTML must supply positively identified article text")
    text = " ".join(pages)
    if not text.strip():
        raise ValueError("no readable document text")
    if identity.startswith("doi:"):
        expected = _doi(identity)
        is_pdf = document.get("media_type") == "application/pdf"
        observed = pdf_identity(pages) if is_pdf else _doi(str(document.get("identity", "")))
        if observed and observed != expected:
            raise ValueError("identity_unconfirmed")
        identity_text = pages[0] if is_pdf else text
        if observed != expected:
            words = re.findall(r"\w+", title.lower())
            normalized = " ".join(re.findall(r"\w+", identity_text[:24000].lower()))
            if len(words) < 4 or " ".join(words) not in normalized:
                raise ValueError("identity_unconfirmed")
    result = {key: document[key] for key in ("content", "pages", "media_type", "url", "requested_url", "title")}
    result["pages"] = tuple(pages)
    return result


@dataclass
class Acquisition:
    scope: tuple[str, str, str]
    url: str
    provider: str
    title: str = ""
    identity: str = ""
    state: str = "queued"
    next_check: float = 0
    document: dict | None = None
    force: bool = False
    focus: bool = False
    import_path: str | None = None
    reason_code: str | None = None
    last_checked_at: str | None = None
    transient_failures: int = 0
    deadline: float = 0
    started: bool = False
    manual: bool = False


class InstitutionalAccess:
    def __init__(self, backend, *, clock=monotonic, poll_seconds: float = 30, event_sink=None,
                 acquisition_seconds=120, manual_seconds=600):
        for name, value, low, high in (("acquisition_seconds", acquisition_seconds, 30, 600),
                                       ("manual_seconds", manual_seconds, 60, 1800)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} must be finite and between {low} and {high}")
        self.acquisition_seconds, self.manual_seconds = acquisition_seconds, manual_seconds
        self.backend, self.clock, self.poll_seconds = backend, clock, poll_seconds
        self.event_sink = event_sink
        self._items: dict[tuple, Acquisition] = {}
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hypoloom-institution")
        self._flight = None
        self._closed = False
        self._manual_until = {}
        self._auth_needed = {}
        self._connection_retry = 0

    @property
    def configuration(self):
        return {**self.backend.configuration, "acquisition_seconds": self.acquisition_seconds,
                "manual_seconds": self.manual_seconds}

    def _pending(self, item):
        return item.state in {"queued", "checking", "browser_unavailable", "fetch_unavailable"} or (
            item.state in AUTH_WAIT_STATES and self._manual_until.get((item.scope[0], item.provider), 0) > self.clock())

    def _expire(self):
        now = self.clock()
        for item in self._items.values():
            if item.state in AUTH_WAIT_STATES and (item.scope[0], item.provider) not in self._manual_until:
                continue
            if item.document is None and item.deadline <= now and item.state in WAIT_STATES | {"queued", "checking"}:
                state = "manual_timeout" if item.manual else "acquisition_timeout" if item.started else "queue_timeout"
                self._transition(item, state, state)
        for key, end in list(self._manual_until.items()):
            if end <= now:
                del self._manual_until[key]
                self._auth_needed[key] = "login_required"

    def _transition(self, item, state, reason=None, *, initial=False):
        previous = None if initial else item.state
        item.state, item.reason_code = state, reason
        if self.event_sink is not None and (previous != state or initial):
            # Only canonical request URLs and fixed codes leave this module.
            # Never export the live browser URL, exception, document or import path.
            self.event_sink({"type": "literature_access", "run_id": item.scope[0],
                "timestamp": datetime.now(timezone.utc).isoformat(), "payload": {
                    "role": item.scope[1], "component_id": item.scope[2], "provider": item.provider,
                    "url": item.url, "from": previous, "to": state, "reason_code": reason}})

    def request(self, scope, url, *, title="", identity="") -> dict:
        try:
            url = article_url(url)
        except ValueError:
            return {"status": "unavailable", "reason": "unsupported_institutional_url"}
        with self._lock:
            self._expire()
            key = (*scope, url)
            if self._closed:
                return {"status": "unavailable", "reason": "institutional_access_closed"}
            if key not in self._items:
                if len(self._items) >= MAX_REQUESTS:
                    evict = next((k for k, p in self._items.items() if not self._pending(p)), None)
                    if evict is None:
                        return {"status": "unavailable", "reason": "institutional_request_limit"}
                    del self._items[evict]
                provider = publisher(url)
                end = self._manual_until.get((scope[0], provider), 0)
                deadline = self.clock() + self.acquisition_seconds
                self._items[key] = Acquisition(tuple(scope), url, provider, title[:512], identity or identity_from_url(url),
                    deadline=min(deadline, end) if end else deadline)
                state = self._auth_needed.get((scope[0], provider), "queued") if not end else "queued"
                self._transition(self._items[key], state, "requested" if state == "queued" else state, initial=True)
            item = self._items[key]
            return self._reply(item)

    def _reply(self, item):
        if item.document is not None:
            return dict(item.document)
        return {"status": "pending" if self._pending(item) else "unavailable",
                "reason": item.state, "reason_code": item.reason_code, "provider": item.provider, "requested_url": item.url,
                "next_action": "Continue other papers. The operator can use i in the TUI. "
                    "Later repeat web_fetch with mode=document and this URL; do not repeatedly poll or infer full-text claims."}

    def peek(self, scope, url):
        with self._lock:
            self._expire()
            item = self._items.get((*scope, url))
            return None if item is None else self._reply(item)

    def status(self, run_id):
        with self._lock:
            self._expire()
            groups = {}
            for item in self._items.values():
                if item.scope[0] != run_id:
                    continue
                group = groups.setdefault(item.provider, {"provider": item.provider, "papers": []})
                # Different experts may request the same source. Show it once to the operator.
                if any(p["url"] == item.url for p in group["papers"]):
                    continue
                group["papers"].append({"url": item.url, "title": item.title or item.url, "state": item.state,
                    "reason_code": item.reason_code, "last_checked_at": item.last_checked_at,
                    "retry_in_seconds": max(0, ceil(item.next_check - self.clock())) if self._pending(item) else None,
                    "remaining_seconds": max(0, ceil(item.deadline - self.clock())) if self._pending(item) else None})
            config = getattr(self.backend, "configuration", None) or {}
            return {"enabled": True, "groups": list(groups.values()),
                    "browser_endpoint": config.get("browser_endpoint"),
                    "browser_ui_url": config.get("browser_ui_url"),
                    "library_url": config.get("library_url"),
                    "pending": sum(p["remaining_seconds"] is not None
                                   for g in groups.values() for p in g["papers"]),
                    "available": sum(p["state"] == "available" for g in groups.values() for p in g["papers"])}

    def control(self, run_id, provider, action, *, url=None, path=None):
        if not isinstance(action, str) or action not in {"open", "retry", "import"}:
            raise ValueError("institutional action must be open, retry or import")
        with self._lock:
            items = [p for p in self._items.values() if p.scope[0] == run_id
                     and p.provider == provider and (url is None or p.url == url)]
            if not items:
                raise ValueError("no selected institutional request in this run")
            if action == "import":
                if url is None or not isinstance(path, str) or not Path(path).is_absolute():
                    raise ValueError("import requires selected URL and an absolute Host-side PDF path")
            end = self.clock() + (self.acquisition_seconds if action == "import" else self.manual_seconds)
            if action != "import":
                self._manual_until[(run_id, provider)] = end
                self._auth_needed.pop((run_id, provider), None)
                self._connection_retry = 0
            replacements = []
            for item in items:
                if item.document is None:
                    fresh = replace(item, next_check=0, transient_failures=0, deadline=end, started=False,
                        force=False, focus=False, import_path=path if action == "import" else None, manual=action != "import")
                    self._items[(*item.scope, item.url)] = fresh
                    self._transition(fresh, "queued", "operator_" + action)
                    replacements.append(fresh)
            if replacements:
                replacements[0].force = True
                replacements[0].focus = action == "open"
        return self.status(run_id)

    def _acquire(self, item, force, focus, import_path):
        try:
            remaining = max(.001, item.deadline - self.clock())
            result = (self.backend.import_pdf(import_path, item.url, item.title, timeout_seconds=remaining) if import_path
                      else self.backend.fetch(item.url, force=force, focus=focus, timeout_seconds=remaining))
            if result.get("state") != "available":
                state = result.get("state", "failed")
                state = state if state in _STATES else "failed"
                reason = result.get("reason")
                return state, None, reason if isinstance(reason, str) and reason in _REASONS else state
            if import_path and not item.identity.startswith("doi:"):
                words = re.findall(r"\w+", item.title.lower())
                text = " ".join(re.findall(r"\w+", " ".join(result["document"]["pages"])[:24000].lower()))
                if len(words) < 4 or " ".join(words) not in text:
                    raise ValueError("identity_unconfirmed")
            document = validate_document(result["document"], title=item.title, identity=item.identity)
            return "available", document, "document_verified"
        except ValueError as exc:
            state = "identity_unconfirmed" if str(exc) == "identity_unconfirmed" else "failed"
            return state, None, state
        except Exception:
            # Browser exceptions can contain SSO URLs/tokens. Never publish their text.
            return "browser_unavailable", None, "browser_unavailable"

    def tick(self, active_runs):
        with self._lock:
            if self._closed:
                return
            had_items = bool(self._items)
            for item in self._items.values():
                if item.scope[0] not in active_runs:
                    self._transition(item, "cancelled", "run_inactive")
            self._items = {key: item for key, item in self._items.items() if item.scope[0] in active_runs}
            self._manual_until = {k: v for k, v in self._manual_until.items() if k[0] in active_runs}
            self._auth_needed = {k: v for k, v in self._auth_needed.items() if k[0] in active_runs}
            self._expire()
            if had_items and not self._items:
                # Runs have stopped. Serialize owned-tab cleanup after any in-flight call.
                self._executor.submit(self.backend.close)
            if self._flight:
                key, acquired, future = self._flight
                if not future.done():
                    return
                self._flight = None
                state, document, reason = future.result()
                if self._items.get(key) is acquired and acquired.state == "checking":
                    item = self._items[key]
                    cache_size = sum(len(p.document["content"]) + sum(len(t.encode()) for t in p.document["pages"])
                                     for p in self._items.values() if p.document is not None)
                    size = 0 if document is None else len(document["content"]) + sum(len(t.encode()) for t in document["pages"])
                    if cache_size + size > MAX_CACHE_BYTES:
                        state, document, reason = "failed", None, "cache_limit"
                    item.document = document
                    item.last_checked_at = datetime.now(timezone.utc).isoformat()
                    self._transition(item, state, reason)
                    group = (item.scope[0], item.provider)
                    if state in AUTH_WAIT_STATES:
                        self._auth_needed[group] = state
                        if self._manual_until.get(group, 0) <= self.clock():
                            for other in self._items.values():
                                if (other.scope[0], other.provider) == group and self._pending(other):
                                    self._transition(other, state, state)
                    elif state == "available":
                        self._auth_needed.pop(group, None)
                    elif state == "browser_unavailable":
                        self._connection_retry = self.clock() + self.poll_seconds
                    delay = self.poll_seconds
                    if state == "fetch_unavailable" and reason in TRANSIENT_FETCH_REASONS:
                        item.transient_failures = min(item.transient_failures + 1, 4)
                        delay = min(300, self.poll_seconds * (1, 2, 4, 10)[item.transient_failures - 1])
                    else:
                        item.transient_failures = 0
                    item.next_check = self.clock() + delay
            # One waiting publisher tab at a time; never navigate away from an unfinished login.
            waiting = {p.provider: p for p in self._items.values() if p.state in AUTH_WAIT_STATES and self._pending(p)}
            for key, item in sorted(self._items.items(), key=lambda pair: (pair[1].next_check, pair[1].deadline)):
                if not self._pending(item) or item.state == "checking" or item.next_check > self.clock():
                    continue
                if not item.import_path and self._connection_retry > self.clock():
                    continue
                if item.provider in waiting and waiting[item.provider] is not item and not (item.force or item.import_path):
                    continue
                self._transition(item, "checking", "import" if item.import_path else "browser_check")
                item.started = True
                self._flight = key, item, self._executor.submit(self._acquire, item, item.force, item.focus, item.import_path)
                item.force = item.focus = False
                item.import_path = None
                # Backend remembers the waiting tab. Polling never restarts its login flow.
                break

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._items.clear()
        self._executor.submit(self.backend.close)
        self._executor.shutdown(wait=True, cancel_futures=False)
