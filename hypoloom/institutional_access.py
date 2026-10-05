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
# Presentation/default preparation only; routing requires a selected provider mode.
COMMON_PREPARATION = {"IEEE": True, "ACM": False, "ScienceDirect": True, "Springer": True}
PROVIDER_MODES = {"auto", "public_only", "disabled"}
# Public full-text probes, not subscription/authentication tests. Publisher
# sources and selection rationale: docs/guides/institutional-access.md#fixed-samples
PREPARATION_SAMPLES = {
    "IEEE": "10.1109/ACCESS.2019.2902267",
    "ACM": "10.1145/945445.945450",
    "ScienceDirect": "10.1016/j.softx.2024.101723",
    "Springer": "10.1007/s10664-020-09851-6",
}


def provider_name(value):
    return "ScienceDirect" if value == "Elsevier" else value


def provider_entry_url(provider):
    # Public authentication navigation only; never a supported article source.
    if provider == "Springer":
        return "https://wayf.springernature.com/?redirect_uri=https%3A%2F%2Flink.springer.com%2F"
    entry = next(("https://" + domain + "/" for domain, name in PROVIDERS.items() if name == provider), None)
    if entry is None: raise ValueError("unsupported institutional provider")
    return entry


def provider_catalog():
    return [{"provider": name, "display_name": "Elsevier / ScienceDirect" if name == "ScienceDirect" else name,
             "entry_url": provider_entry_url(name), "common": name in COMMON_PREPARATION,
             "recommended_prepare": COMMON_PREPARATION.get(name, False),
             "default_sample_url": "https://doi.org/" + PREPARATION_SAMPLES[name] if name in PREPARATION_SAMPLES else None,
             "note": "Public full text first; login optional" if name == "ACM" else "Reuse a valid browser session; authenticate if needed"}
            for name in dict.fromkeys([*COMMON_PREPARATION, *PROVIDERS.values()])]


DOI_PROVIDERS = {"10.1038": "Nature", "10.1007": "Springer", "10.1109": "IEEE",
    "10.1016": "ScienceDirect", "10.1145": "ACM", "10.1002": "Wiley", "10.1111": "Wiley",
    "10.1177": "SAGE", "10.1093": "Oxford", "10.1080": "Taylor & Francis"}
MAX_DOCUMENT_BYTES = 24 * 1024 * 1024
MAX_CACHE_BYTES = 64 * 1024 * 1024
MAX_REQUESTS = 32
AUTH_WAIT_STATES = {"login_required", "verification_required", "access_required"}
WAIT_STATES = AUTH_WAIT_STATES | {"browser_unavailable", "fetch_unavailable", "access_required"}
_STATES = WAIT_STATES | {"available", "access_denied", "no_full_text", "identity_unconfirmed", "failed"}
_STATES |= {"opened", "tab_limit"}
TRANSIENT_FETCH_REASONS = {"network_changed", "dns_failed", "connection_failed",
    "navigation_timeout", "network_error", "redirect_unresolved", "http_server_error", "http_rate_limited", "pdf_response_not_pdf"}
_REASONS = TRANSIENT_FETCH_REASONS | {"request_blocked", "http_error", "verification_required",
    "cdp_timeout", "cdp_connection_refused", "cdp_connection_closed", "cdp_authorization_failed",
    "cdp_endpoint_unavailable", "cdp_context_unavailable", "cdp_connection_failed", "browser_driver_unavailable",
    "browser_target_closed", "browser_operation_timeout", "browser_operation_failed",
    "article_identity_unconfirmed", "pdf_target_unconfirmed", "document_validation_failed"}


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
    preparation: str | None = None
    verification: dict | None = None


class InstitutionalAccess:
    def __init__(self, backend, *, clock=monotonic, poll_seconds: float = 30, event_sink=None,
                 acquisition_seconds=120, manual_seconds=600, provider_modes=None):
        for name, value, low, high in (("acquisition_seconds", acquisition_seconds, 30, 600),
                                       ("manual_seconds", manual_seconds, 60, 1800)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} must be finite and between {low} and {high}")
        self.acquisition_seconds, self.manual_seconds = acquisition_seconds, manual_seconds
        self.provider_modes = dict(provider_modes or {})
        if any(provider not in PROVIDERS.values() or mode not in PROVIDER_MODES
               for provider, mode in self.provider_modes.items()):
            raise ValueError("unknown institutional provider or mode")
        self.read_choices = lambda run_id: ({}, {})
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
                "manual_seconds": self.manual_seconds, "provider_modes": dict(self.provider_modes)}

    def check_connection(self, run_id, *, timeout_seconds):
        # Playwright and later acquisition must run on the same existing worker.
        with self._lock:
            if self._closed: raise ValueError("institutional access is closed")
            return self._executor.submit(self.backend.check_connection, timeout_seconds=timeout_seconds)

    def paper_identity(self, run_id, url):
        with self._lock:
            known = next((p.identity for p in self._items.values()
                          if p.scope[0] == run_id and p.url == url and p.identity.startswith("doi:")), "")
            if known: return "doi:" + _doi(known)
            _, skipped = self.read_choices(run_id)
            return next((key for key, saved_url in skipped.items() if saved_url == url),
                        identity_from_url(url).lower() or url)

    def automatic_reason(self, scope, url, identity="", *, institution=False):
        """Durable operator choices are independent of the disposable request cache."""
        modes, skipped = self.read_choices(scope[0])
        key = "doi:" + _doi(identity) if identity.startswith("doi:") else self.paper_identity(scope[0], url)
        if key in skipped or url in skipped.values(): return "skipped"
        mode = modes.get(publisher(url), self.provider_modes.get(publisher(url), "disabled"))
        if institution and mode != "auto":
            return mode
        return None

    def _apply_choices(self, run_id):
        modes, skipped = self.read_choices(run_id)
        for provider in PROVIDERS.values():
            if modes.get(provider, self.provider_modes.get(provider, "disabled")) != "auto":
                self._manual_until.pop((run_id, provider), None)
        for key, item in list(self._items.items()):
            if item.scope[0] != run_id or item.document is not None or item.import_path:
                continue
            identity = "doi:" + _doi(item.identity) if item.identity.startswith("doi:") else item.url
            mode = modes.get(item.provider, self.provider_modes.get(item.provider, "disabled"))
            reason = ("skipped" if not item.preparation and (identity in skipped or item.url in skipped.values()) else
                mode if mode != "auto" else None)
            if reason:
                if item.state != reason: self._transition(item, reason, "operator_" + reason)
            elif item.state in {"skipped", "public_only", "disabled"}:
                # Restore permits a fresh request, not unsolicited replay of an old fetch.
                del self._items[key]

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

    def request(self, scope, url, *, title="", identity="", automatic=True) -> dict:
        try:
            url = article_url(url)
        except ValueError:
            return {"status": "unavailable", "reason": "unsupported_institutional_url"}
        with self._lock:
            self._apply_choices(scope[0])
            self._expire()
            key = (*scope, url)
            if self._closed:
                return {"status": "unavailable", "reason": "institutional_access_closed"}
            if key not in self._items:
                if len(self._items) >= MAX_REQUESTS:
                    finished = [(k, p) for k, p in self._items.items() if not self._pending(p)]
                    # Keep verified bytes ahead of cheap failures/preferences;
                    # all entries still obey the existing count/byte bounds.
                    evict = next((k for k, p in finished if p.document is None),
                                 finished[0][0] if finished else None)
                    if evict is None:
                        return {"status": "unavailable", "reason": "institutional_request_limit"}
                    del self._items[evict]
                provider = publisher(url)
                end = self._manual_until.get((scope[0], provider), 0)
                deadline = self.clock() + self.acquisition_seconds
                self._items[key] = Acquisition(tuple(scope), url, provider, title[:512], identity or identity_from_url(url),
                    deadline=min(deadline, end) if end else deadline)
                state = self.automatic_reason(scope, url, identity, institution=True)
                if state is None:
                    state = (self._auth_needed.get((scope[0], provider), "queued") if not end else "queued") if automatic else "failed"
                reason = "manual_import_available" if not automatic and state == "failed" else "requested" if state == "queued" else state
                self._transition(self._items[key], state, reason, initial=True)
            item = self._items[key]
            self._reuse_available(item)
            return self._reply(item)

    def _reuse_available(self, item):
        if item.state != "queued" or item.preparation or item.import_path or item.force:
            return
        source = next((other for other in self._items.values()
            if other is not item and other.scope[0] == item.scope[0] and other.url == item.url
            and other.document is not None and not other.preparation and not other.import_path), None)
        if source is None:
            return
        # A new expert must explicitly request the URL and pass its own identity
        # check. Only acquisition bytes are reused; source refs/archives stay owned
        # by each requesting Attempt. Nothing crosses runs or manual-import scopes.
        try:
            document = validate_document(source.document, title=item.title, identity=item.identity)
        except ValueError:
            self._transition(item, "identity_unconfirmed", "document_validation_failed")
            return
        if self._cache_size(document) > MAX_CACHE_BYTES:
            self._transition(item, "failed", "cache_limit")
            return
        item.document = document
        item.last_checked_at = source.last_checked_at
        self._transition(item, "available", "document_verified")

    def _cache_size(self, document):
        documents = [p.document for p in self._items.values() if p.document is not None]
        if document is not None:
            documents.append(document)
        return sum(len(d["content"]) + sum(len(t.encode()) for t in d["pages"]) for d in documents)

    def _reply(self, item):
        if item.document is not None:
            return dict(item.document)
        return {"status": "pending" if self._pending(item) else "unavailable",
                "reason": item.state, "reason_code": item.reason_code, "provider": item.provider, "requested_url": item.url,
                "remaining_seconds": max(0, item.deadline - self.clock()) if self._pending(item) else None,
                "next_action": "Document tools wait automatically within this acquisition deadline. "
                    "If unavailable, continue other sources; do not repeatedly poll, request operator action or infer full-text claims."}

    def peek(self, scope, url):
        with self._lock:
            self._apply_choices(scope[0])
            self._expire()
            item = self._items.get((*scope, url))
            return None if item is None else self._reply(item)

    def status(self, run_id):
        with self._lock:
            self._apply_choices(run_id)
            self._expire()
            modes, skipped = self.read_choices(run_id)
            groups = {name: {"provider": name, "entry_url": provider_entry_url(name), "papers": [],
                "mode": modes.get(name, self.provider_modes.get(name, "disabled")),
                "manual_remaining_seconds": max(0, ceil(self._manual_until.get((run_id, name), 0) - self.clock())),
                "preparation": {"state": "unchecked"}}
                for domain, name in PROVIDERS.items()}
            for item in self._items.values():
                if item.scope[0] != run_id:
                    continue
                group = groups[item.provider]
                if item.preparation:
                    group["preparation"] = {"state": item.state, "last_checked_at": item.last_checked_at,
                        "reason_code": item.reason_code,
                        "sample_url": item.url if item.preparation == "verify" else None,
                        **(item.verification or {})}
                    continue
                # Different experts may request the same source. Show it once to the operator.
                if any(p["url"] == item.url for p in group["papers"]):
                    continue
                group["papers"].append({"url": item.url, "title": item.title or item.url, "state": item.state,
                    "identity": item.identity or item.url,
                    "skipped": self.automatic_reason(item.scope, item.url, item.identity) == "skipped",
                    "reason_code": item.reason_code, "last_checked_at": item.last_checked_at,
                    "retry_in_seconds": max(0, ceil(item.next_check - self.clock())) if self._pending(item) else None,
                    "remaining_seconds": max(0, ceil(item.deadline - self.clock())) if self._pending(item) else None})
            # Eviction/restart must not make the operator's restore button disappear.
            for identity, url in skipped.items():
                group = groups.get(publisher(url))
                if group is not None and not any(p["url"] == url for p in group["papers"]):
                    group["papers"].append({"url": url, "title": url, "identity": identity, "state": "skipped",
                        "skipped": True, "reason_code": "operator_skipped", "last_checked_at": None,
                        "retry_in_seconds": None, "remaining_seconds": None})
            config = getattr(self.backend, "configuration", None) or {}
            from .institutional_browser import windows_browser_command
            endpoint_port = urlsplit(config.get("browser_endpoint") or "").port
            return {"enabled": True, "groups": list(groups.values()),
                    "windows_setup_command": windows_browser_command(endpoint_port or "<端口>"),
                    "browser_endpoint": config.get("browser_endpoint"),
                    "browser_ui_url": config.get("browser_ui_url"),
                    "library_url": config.get("library_url"),
                    "pending": sum(p["remaining_seconds"] is not None
                                   for g in groups.values() for p in g["papers"]),
                    "available": sum(p["state"] == "available" for g in groups.values() for p in g["papers"])}

    def control(self, run_id, provider, action, *, url=None, path=None):
        if not isinstance(action, str) or action not in {"open", "retry", "import", "verify"}:
            raise ValueError("unsupported institutional action")
        if provider not in PROVIDERS.values(): raise ValueError("unsupported institutional provider")
        with self._lock:
            self._apply_choices(run_id)
            items = [p for p in self._items.values() if p.scope[0] == run_id
                     and p.provider == provider and not p.preparation and (url is None or p.url == url)]
            modes, _ = self.read_choices(run_id)
            if action != "import" and modes.get(provider, self.provider_modes.get(provider, "disabled")) != "auto":
                raise ValueError("enable institutional access before opening or checking this platform")
            if action == "verify" or not items and action in {"open", "retry"}:
                return self._prepare(run_id, provider, action, url)
            if not items: raise ValueError("no selected institutional request in this run")
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
                if action != "import" and self.automatic_reason(item.scope, item.url, item.identity, institution=True):
                    continue
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

    def _prepare(self, run_id, provider, action, url):
        entry = provider_entry_url(provider)
        if action == "verify":
            if article_url(url) != url or publisher(url) != provider:
                raise ValueError("sample must be a canonical article URL for this platform")
        else: url = entry
        self.discard_preparation(run_id, provider)
        if len(self._items) >= MAX_REQUESTS:
            key = next((key for key, item in self._items.items() if not self._pending(item)), None)
            if key is None: raise ValueError("institutional request limit; retry after active requests finish")
            del self._items[key]
        scope = (run_id, "operator", "preparation:" + provider)
        self._manual_until[(run_id, provider)] = self.clock() + self.manual_seconds
        self._auth_needed.pop((run_id, provider), None)
        self._connection_retry = 0
        item = Acquisition(scope, url, provider, identity=identity_from_url(url),
            deadline=self.clock() + self.acquisition_seconds, focus=action == "open", force=True, preparation=action)
        self._items[(*scope, url)] = item
        self._transition(item, "queued", "operator_" + action, initial=True)
        return self.status(run_id)

    def discard_preparation(self, run_id, provider):
        """Retire this transient sample without changing research requests or tabs."""
        with self._lock:
            for key, item in list(self._items.items()):
                if item.scope[0] == run_id and item.provider == provider and item.preparation:
                    del self._items[key]

    def _acquire(self, item, force, focus, import_path):
        try:
            remaining = max(.001, item.deadline - self.clock())
            result = (self.backend.prepare(item.provider, focus=focus, timeout_seconds=remaining)
                      if item.preparation in {"open", "retry"} else
                      self.backend.import_pdf(import_path, item.url, item.title, timeout_seconds=remaining) if import_path
                      else getattr(self.backend, "fetch_sample", self.backend.fetch)(
                          item.url, force=force, focus=focus, timeout_seconds=remaining) if item.preparation == "verify" and force
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
            public = item.preparation == "verify" and result.get("acquisition_source") == "public_pdf"
            document = validate_document(result["document"], title=result.get("title", "") if public else item.title,
                                         identity=item.identity)
            if item.preparation == "verify":
                document["acquisition_source"] = "public_pdf" if public else "browser"
            return "available", document, "document_verified"
        except ValueError as exc:
            state = "identity_unconfirmed" if str(exc) == "identity_unconfirmed" else "failed"
            return state, None, "document_validation_failed" if state == "identity_unconfirmed" else state
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
            for run_id in active_runs:
                self._apply_choices(run_id)
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
                    if self._cache_size(document) > MAX_CACHE_BYTES:
                        state, document, reason = "failed", None, "cache_limit"
                    if item.preparation and state == "available":
                        from .document_fallback import public_url
                        item.verification = {"media_type": document["media_type"],
                            "source_url": public_url(document["url"]), "page_count": len(document["pages"]),
                            "acquisition_source": document["acquisition_source"]}
                        state, document = "sample_verified", None
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
                self._reuse_available(item)
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
                # Backend remembers the waiting tab. Polling never restarts its login flow.
                break

    def close(self, *, wait=True):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._items.clear()
        self._executor.submit(self.backend.close)
        self._executor.shutdown(wait=wait, cancel_futures=False)
