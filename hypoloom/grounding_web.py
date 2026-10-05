"""Agent-selected, Host-mediated public web reads for Grounding.

DuckDuckGo HTML is a public interface, not an API SLA. Errors and challenges
are explicit. The injected transport uses the existing literature FetchResponse
and URL/DNS/peer rules; no state, credentials, receipts or authority are added.
Host callers must schedule this bounded blocking I/O outside writer transactions.
"""

from __future__ import annotations

import http.client
import base64
import ipaddress
import re
import socket
import time
from contextlib import nullcontext
from html.parser import HTMLParser
from typing import Any, Callable, Protocol, Sequence
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from .literature import (MAX_ADDRESSES_PER_HOST, FetchResponse, LiteratureError, PdfDocument,
                         PopplerPdfExtractor, _public_address, _uri_parts)
from .network_io import NetworkOperationError, report_network_progress, run_network_operation
from .literature_proxy import LiteratureProxyRouter, connect_tunnel, validate_response_peer


class GroundingWebError(ValueError):
    def __init__(self, code: str, message: str, *, http_status: int | None = None,
                 transport_phase: str | None = None, received_bytes: int = 0) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.http_status = http_status
        self.transport_phase = transport_phase
        self.received_bytes = received_bytes


class WebTransport(Protocol):
    def resolve(self, hostname: str) -> Sequence[str]: ...

    def fetch(self, uri: str, *, approved_addresses: tuple[str, ...], max_bytes: int) -> FetchResponse: ...


class PublicHttpsTransport:
    """Credential-free HTTPS GET with the literature transport's peer check."""

    def __init__(self, timeout_seconds: float = 15.0, *, proxy_router: LiteratureProxyRouter | None = None) -> None:
        if isinstance(timeout_seconds, bool) or not 0.1 <= timeout_seconds <= 60:
            raise ValueError("web timeout must be within 0.1..60 seconds")
        self.timeout_seconds = timeout_seconds
        self.proxy_router = proxy_router

    def resolve(self, hostname: str, *, timeout_seconds: float | None = None,
                cancel_event: Any = None) -> tuple[str, ...]:
        timeout = self.timeout_seconds if timeout_seconds is None else min(self.timeout_seconds, timeout_seconds)
        try:
            value = run_network_operation("hypoloom.grounding_web:_resolve_in_process",
                {"hostname": hostname}, deadline=time.monotonic() + max(0, timeout),
                max_output_bytes=65536, cancel_event=cancel_event)
        except NetworkOperationError as error:
            raise _network_error(error, phase="dns") from None
        return tuple(value["addresses"])

    def request_headers(self, uri: str) -> dict[str, str]:
        return {"Accept": "text/html, text/plain, application/pdf;q=0.9, application/json;q=0.8, application/atom+xml;q=0.8",
                "User-Agent": "Hypoloom-Grounding/1"}

    def fetch(self, uri: str, *, approved_addresses: tuple[str, ...], max_bytes: int,
              timeout_seconds: float | None = None, cancel_event: Any = None) -> FetchResponse:
        canonical, _ = _uri_parts(uri, "web URL")
        timeout = self.timeout_seconds if timeout_seconds is None else min(self.timeout_seconds, timeout_seconds)
        deadline = time.monotonic() + max(0, timeout)
        try:
            result = run_network_operation("hypoloom.grounding_web:_fetch_in_process",
                {"uri": canonical, "approved_addresses": list(approved_addresses), "max_bytes": max_bytes,
                 "headers": self.request_headers(canonical), "deadline": deadline,
                 "proxy_url": None if self.proxy_router is None else self.proxy_router.for_url(canonical)},
                deadline=deadline, max_output_bytes=(max_bytes * 4 // 3) + 65536, cancel_event=cancel_event)
        except NetworkOperationError as error:
            raise _network_error(error) from None
        return _fetch_response(result)

    def _fetch_in_process(self, uri: str, *, approved_addresses: tuple[str, ...], max_bytes: int,
                          timeout_seconds: float | None = None) -> FetchResponse:
        """Socket implementation seam for injected HTTP tests; production uses fetch."""
        timeout = self.timeout_seconds if timeout_seconds is None else min(self.timeout_seconds, timeout_seconds)
        return _fetch_response(self._request_in_process(uri, approved_addresses=approved_addresses,
            max_bytes=max_bytes, deadline=time.monotonic() + max(0, timeout), headers=self.request_headers(uri),
            proxy_url=None if self.proxy_router is None else self.proxy_router.for_url(uri)))

    def _request_in_process(self, uri: str, *, approved_addresses: tuple[str, ...], max_bytes: int,
                            deadline: float, headers: dict[str, str], proxy_url: str | None = None) -> dict[str, Any]:
        canonical, host = _uri_parts(uri, "web URL")
        parsed = urlsplit(canonical)
        timeout = deadline - time.monotonic()
        if timeout <= 0: raise GroundingWebError("timed_out", "web request wall bound expired")
        connection = http.client.HTTPSConnection(host, 443, timeout=timeout)
        phase, size, status = "tcp_connect", 0, None
        tunnel_address, proxy_peer = None, None
        def progress(value: str) -> None:
            nonlocal phase
            phase = value
            report_network_progress(phase, received_bytes=size, http_status=status)
        def timed_out():
            return GroundingWebError("timed_out", "web transport timed out",
                transport_phase=phase, received_bytes=size,
                http_status=status if status != 200 else None)
        def remaining() -> float:
            value = deadline - time.monotonic()
            if value <= 0: raise timed_out()
            return value
        def connect_approved(_address, _timeout, source_address=None):
            nonlocal phase, tunnel_address, proxy_peer
            if proxy_url is not None:
                if not approved_addresses:
                    raise GroundingWebError("unsafe_url", "no approved public web address")
                tunnel_address = _public_address(approved_addresses[0], "proxy tunnel destination")
                progress("tcp_connect")
                stream, proxy_peer = connect_tunnel(proxy_url, tunnel_address, remaining=remaining)
                progress("tls_handshake")
                return stream
            # Do not repeat DNS or grant each address a fresh full timeout.
            last_error = None
            for address in approved_addresses:
                progress("tcp_connect")
                address = _public_address(address, "web DNS address")
                sock = socket.socket(socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM)
                try:
                    sock.settimeout(remaining())
                    if source_address is not None: sock.bind(source_address)
                    sock.connect((address, 443))
                    sock.settimeout(remaining())
                    progress("tls_handshake")
                    return sock
                except OSError as exc:
                    last_error = exc; sock.close()
                except BaseException:
                    sock.close(); raise
            if last_error is not None: raise last_error
            raise GroundingWebError("unsafe_url", "no approved public web address")
        connection._create_connection = connect_approved
        try:
            progress("tcp_connect")
            connection.connect()
            if connection.sock is None:
                raise GroundingWebError("transport_failed", "HTTPS connection has no socket")
            peer = str(connection.sock.getpeername()[0])
            if proxy_url is not None:
                if peer != proxy_peer:
                    raise GroundingWebError("unsafe_url", "HTTPS proxy peer changed after CONNECT")
            elif _public_address(peer, "web peer") not in approved_addresses:
                raise GroundingWebError("unsafe_url", "HTTPS peer differs from approved DNS addresses")
            progress("request_send")
            connection.sock.settimeout(remaining())
            connection.request("GET", urlunsplit(("", "", parsed.path or "/", parsed.query, "")),
                headers=headers)
            connection.sock.settimeout(remaining())
            progress("response_headers")
            response = connection.getresponse()
            status = response.status
            if (response.getheader("Cf-Mitigated") or "").strip().lower() == "challenge":
                raise GroundingWebError("challenge", "web provider requires browser verification",
                    http_status=status)
            raw_length = response.getheader("Content-Length")
            if raw_length is not None and (not raw_length.isascii() or not raw_length.isdigit()):
                raise GroundingWebError("transport_failed", "invalid Content-Length")
            length = None if raw_length is None else int(raw_length)
            if length is not None and length > max_bytes:
                raise GroundingWebError("response_limit", "web response exceeds its byte bound")
            chunks = []
            progress("response_body")
            while True:
                seconds_left = remaining()
                if connection.sock is not None:
                    connection.sock.settimeout(seconds_left)
                chunk = response.read1(min(65536, max_bytes + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                progress("response_body")
                if size > max_bytes:
                    raise GroundingWebError("response_limit", "web response exceeds its byte bound")
            return {"requested_uri": canonical, "status_code": response.status,
                "peer_address": peer, "media_type": response.getheader("Content-Type"),
                "content_length": length, "location": response.getheader("Location"),
                "body_b64": base64.b64encode(b"".join(chunks)).decode("ascii"),
                "retry_after": response.getheader("Retry-After"), "proxy_tunnel_address": tunnel_address}
        except (TimeoutError, socket.timeout) as exc:
            raise timed_out() from exc
        finally:
            connection.close()


def _network_error(error: NetworkOperationError, *, phase: str | None = None) -> GroundingWebError:
    return GroundingWebError(error.code, "web network operation ended",
        http_status=error.http_status, transport_phase=error.transport_phase or phase,
        received_bytes=error.received_bytes)


def _fetch_response(value: dict[str, Any]) -> FetchResponse:
    return FetchResponse(requested_uri=value["requested_uri"], status_code=value["status_code"],
        peer_address=value["peer_address"], media_type=value["media_type"], content_length=value["content_length"],
        location=value["location"], body=base64.b64decode(value["body_b64"], validate=True),
        proxy_tunnel_address=value.get("proxy_tunnel_address"))


def _resolve_in_process(payload: dict[str, Any]) -> dict[str, Any]:
    report_network_progress("dns")
    return {"addresses": sorted({item[4][0] for item in socket.getaddrinfo(
        payload["hostname"], 443, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)})}


def _fetch_in_process(payload: dict[str, Any]) -> dict[str, Any]:
    """One child-owned DNS/HTTPS request, also shared by fixed literature APIs."""
    uri, host = _uri_parts(payload["uri"], "web URL")
    addresses = payload.get("approved_addresses")
    if addresses is None:
        addresses = _resolve_in_process({"hostname": host})["addresses"]
    addresses = tuple(sorted({_public_address(value, "web DNS address") for value in addresses}))
    if not 1 <= len(addresses) <= MAX_ADDRESSES_PER_HOST:
        raise GroundingWebError("unsafe_url", "DNS returned an invalid public address count")
    return PublicHttpsTransport()._request_in_process(uri, approved_addresses=addresses,
        max_bytes=payload["max_bytes"], deadline=payload["deadline"], headers=payload["headers"],
        proxy_url=payload.get("proxy_url"))


def _link(base: str, href: str) -> str | None:
    absolute = urljoin(base, href)
    parsed = urlsplit(absolute)
    if parsed.hostname in {"duckduckgo.com", "html.duckduckgo.com"} and parsed.path == "/l/":
        absolute = parse_qs(parsed.query).get("uddg", [absolute])[0]
        parsed = urlsplit(absolute)
    absolute = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
    try:
        return _uri_parts(absolute, "web link")[0]
    except LiteratureError:
        return None


class _SearchHTML(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base, self.results = base, []
        self.capture: str | None = None
        self.capture_tag: str | None = None
        self.parts: list[str] = []
        self.empty = False
        self.challenge = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        classes = (data.get("class") or "").split()
        self.empty |= any(name in {"no-results", "no-results__title"} for name in classes)
        self.challenge |= "challenge-form" == data.get("id") or "anomaly" in (data.get("action") or "")
        if tag == "a" and "result__a" in classes:
            self.results.append({"url": _link(self.base, data.get("href") or ""), "title": "", "snippet": ""})
            self.capture, self.capture_tag, self.parts = "title", tag, []
        elif "result__snippet" in classes and self.results:
            self.capture, self.capture_tag, self.parts = "snippet", tag, []

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.parts.append(data)
        if "unfortunately, bots use duckduckgo too" in data.casefold():
            self.challenge = True

    def handle_endtag(self, tag: str) -> None:
        if self.capture and tag == self.capture_tag:
            self.results[-1][self.capture] = " ".join("".join(self.parts).split())
            self.capture, self.capture_tag, self.parts = None, None, []


class _PageHTML(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.links: list[str] = []
        self.hidden = 0
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "template", "svg"}:
            self.hidden += 1
        if tag == "title":
            self.in_title = True
        if self.hidden:
            return
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "pre", "tr"}:
            self.parts.append("\n")
        if tag == "a" and len(self.links) < 100:
            link = _link(self.base, dict(attrs).get("href") or "")
            if link and link not in self.links:
                self.links.append(link)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "template", "svg"} and self.hidden:
            self.hidden -= 1
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)
            if self.in_title:
                self.title_parts.append(data)


class GroundingWeb:
    """Stateless bounded reads; the Host owns per-Attempt budgets and trace."""

    def __init__(self, transport: WebTransport | None = None, *,
                 pdf_extractor: Any = None, max_response_bytes: int = 8 * 1024 * 1024,
                 max_redirects: int = 5, max_seconds: float = 25.0,
                 search_fallback: Any = None, academic_search: Any = None,
                 proxy_router: LiteratureProxyRouter | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if type(max_response_bytes) is not int or not 1 <= max_response_bytes <= 64 * 1024 * 1024:
            raise ValueError("web response byte bound must be within 1..64 MiB")
        if type(max_redirects) is not int or not 0 <= max_redirects <= 10:
            raise ValueError("web redirect bound must be within 0..10")
        if isinstance(max_seconds, bool) or not 0.1 <= max_seconds <= 120:
            raise ValueError("web work window must be within 0.1..120 seconds")
        if transport is not None and proxy_router is not None:
            raise ValueError("supply the transport or its proxy router, not both")
        self.transport = transport or PublicHttpsTransport(min(15, max_seconds), proxy_router=proxy_router)
        self.pdf_extractor = pdf_extractor or PopplerPdfExtractor(max_pdf_bytes=max_response_bytes)
        self.max_response_bytes, self.max_redirects = max_response_bytes, max_redirects
        self.max_seconds, self.clock = max_seconds, clock
        self.search_fallback = search_fallback
        self.academic_search = search_fallback if academic_search is None else academic_search

    @property
    def configuration(self) -> dict[str, Any]:
        router = getattr(self.transport, "proxy_router", None)
        return {"search_provider": "duckduckgo-html", "max_seconds": self.max_seconds,
                "max_response_bytes": self.max_response_bytes, "max_redirects": self.max_redirects,
                "academic_provider": None if self.academic_search is None else "openalex",
                "search_fallback": None if self.search_fallback is None else "openalex",
                **({} if router is None else router.configuration)}

    def _read(self, url: str, *, max_seconds: float | None = None, http_calls: list[int] | None = None) -> tuple[str, FetchResponse]:
        visited = set()
        phase = "dns"
        deadline = self.clock() + (self.max_seconds if max_seconds is None else min(max_seconds, self.max_seconds))
        try:
            for _ in range(self.max_redirects + 1):
                uri, host = _uri_parts(url, "web URL")
                if uri in visited:
                    raise GroundingWebError("redirect_error", "web redirect cycle")
                visited.add(uri)
                try:
                    literal = ipaddress.ip_address(host)
                except ValueError:
                    literal = None
                phase = "dns"
                resolve_bounds = ({"timeout_seconds": deadline - self.clock()}
                    if getattr(self.transport.resolve, "__func__", None) is PublicHttpsTransport.resolve else {})
                resolved = (str(literal),) if literal is not None else self.transport.resolve(host, **resolve_bounds)
                if isinstance(resolved, (str, bytes)):
                    raise GroundingWebError("unsafe_url", "DNS must return an address array")
                addresses = tuple(sorted({_public_address(value, "web DNS address") for value in resolved}))
                if not 1 <= len(addresses) <= MAX_ADDRESSES_PER_HOST:
                    raise GroundingWebError("unsafe_url",
                        f"DNS must resolve 1..{MAX_ADDRESSES_PER_HOST} public addresses")
                if self.clock() >= deadline:
                    raise GroundingWebError("timed_out", "web work window expired", transport_phase=phase)
                if http_calls is not None and http_calls[0] <= 0:
                    raise GroundingWebError("request_limit", "document HTTP work budget exhausted")
                admission = nullcontext()
                if host in {"export.arxiv.org", "arxiv.org"} and urlsplit(uri).path == "/api/query":
                    from .arxiv_fallback import request_slot
                    admission = request_slot(time.monotonic() + max(0, deadline - self.clock()))
                with admission:
                    phase = "request"
                    bounds = {"timeout_seconds": deadline - self.clock()} if isinstance(self.transport, PublicHttpsTransport) else {}
                    if http_calls is not None:
                        http_calls[0] -= 1
                    response = self.transport.fetch(uri, approved_addresses=addresses, max_bytes=self.max_response_bytes, **bounds)
                if not isinstance(response, FetchResponse) or response.requested_uri != uri:
                    raise GroundingWebError("transport_failed", "web response does not bind its requested URL")
                router = getattr(self.transport, "proxy_router", None)
                validate_response_peer(response, addresses,
                    proxy_expected=router is not None and router.routes(uri))
                if self.clock() >= deadline:
                    raise GroundingWebError("timed_out", "web work window expired")
                if len(response.body) > self.max_response_bytes:
                    raise GroundingWebError("response_limit", "web response exceeds its byte bound")
                if response.content_length is not None and response.content_length != len(response.body):
                    raise GroundingWebError("transport_failed", "web Content-Length differs from received bytes")
                if response.status_code in {301, 302, 303, 307, 308}:
                    if not response.location:
                        raise GroundingWebError("redirect_error", "redirect omitted Location")
                    url = urljoin(uri, response.location)
                    continue
                if response.status_code == 429:
                    raise GroundingWebError("rate_limited", "web provider returned HTTP 429", http_status=429)
                if response.status_code != 200:
                    raise GroundingWebError("http_error", f"web provider returned HTTP {response.status_code}", http_status=response.status_code)
                if response.location is not None:
                    raise GroundingWebError("redirect_error", "non-redirect response included Location")
                return uri, response
            raise GroundingWebError("redirect_error", "web redirect bound exceeded")
        except GroundingWebError:
            raise
        except LiteratureError as exc:
            raise GroundingWebError("unsafe_url", str(exc)) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise GroundingWebError("timed_out", "web transport timed out", transport_phase=phase) from exc
        except (OSError, http.client.HTTPException, TypeError, ValueError) as exc:
            raise GroundingWebError("transport_failed", f"web transport failed ({type(exc).__name__})") from exc

    @staticmethod
    def _text(response: FetchResponse) -> str:
        match = re.search(r"charset\s*=\s*['\"]?([\w-]+)", response.media_type or "", re.I)
        encoding = "utf-8" if match is None else match[1]
        try:
            return response.body.decode(encoding)
        except (LookupError, UnicodeError) as exc:
            raise GroundingWebError("unsupported_encoding", "web text encoding is unavailable or invalid") from exc

    def web_search(self, query: str, *, max_results: int = 5, scope: str = "web") -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip() or len(query) > 2000 or "\x00" in query:
            raise GroundingWebError("invalid_request", "search query must contain 1..2000 characters")
        if type(max_results) is not int or not 1 <= max_results <= 10:
            raise GroundingWebError("invalid_request", "max_results must be within 1..10")
        if scope not in {"web", "academic"}:
            raise GroundingWebError("invalid_request", "search scope must be web or academic")
        if scope == "academic":
            if self.academic_search is None:
                raise GroundingWebError("academic_unavailable", "Host has no configured OpenAlex search client")
            try:
                return self.academic_search.web_search(query, max_results=max_results,
                    timeout_seconds=min(10.0, self.max_seconds))
            except (LiteratureError, OSError, http.client.HTTPException) as exc:
                raise GroundingWebError("academic_failed", "configured academic search failed") from exc
        started = self.clock()
        try:
            return self._search_html(query, max_results=max_results)
        except GroundingWebError as exc:
            if self.search_fallback is None or exc.code not in {
                    "transport_failed", "timed_out", "rate_limited", "http_error",
                    "challenge", "invalid_provider_response"}:
                raise
            remaining = self.max_seconds - (self.clock() - started)
            if remaining <= 0: raise
            try:
                result = self.search_fallback.web_search(query, max_results=max_results,
                    timeout_seconds=min(10.0, remaining))
            except (LiteratureError, OSError, http.client.HTTPException) as fallback_error:
                raise GroundingWebError("search_backends_failed",
                    f"DuckDuckGo failed ({exc.code}); OpenAlex academic fallback failed ({type(fallback_error).__name__})") from fallback_error
            if self.clock() - started >= self.max_seconds:
                raise GroundingWebError("timed_out", "search work window expired")
            return {**result, "primary_error": {"provider": "duckduckgo-html", "code": exc.code},
                    "fallback_notice": "Academic literature metadata only; general web search failed."}

    def _search_html(self, query: str, *, max_results: int) -> dict[str, Any]:
        uri, response = self._read("https://html.duckduckgo.com/html/?" + urlencode({"q": query}),
            max_seconds=min(15.0, self.max_seconds * 0.6) if self.search_fallback is not None else None)
        if (response.media_type or "").split(";", 1)[0].lower() not in {"text/html", "application/xhtml+xml"}:
            raise GroundingWebError("invalid_provider_response", "search provider did not return HTML")
        parser = _SearchHTML(uri)
        parser.feed(self._text(response))
        parser.close()
        if parser.challenge:
            raise GroundingWebError("challenge", "search provider requires CAPTCHA or bot verification")
        results, seen = [], set()
        for item in parser.results:
            if item["url"] and item["title"] and item["url"] not in seen:
                seen.add(item["url"])
                results.append({"url": item["url"], "title": item["title"][:512], "snippet": item["snippet"][:3000],
                                "truncated": len(item["title"]) > 512 or len(item["snippet"]) > 3000})
        if not results and not parser.empty:
            raise GroundingWebError("invalid_provider_response", "unrecognized search results; not an empty-result claim")
        return {"provider": "duckduckgo-html", "query": query, "source_url": uri,
                "results": results[:max_results], "truncated": len(results) > max_results,
                "source_is_untrusted": True}

    def web_fetch(self, url: str, *, max_chars: int = 20000) -> dict[str, Any]:
        if type(max_chars) is not int or not 1 <= max_chars <= 100000:
            raise GroundingWebError("invalid_request", "max_chars must be within 1..100000")
        uri, response = self._read(url)
        media = (response.media_type or "").split(";", 1)[0].strip().lower()
        title, links, scope = "", [], "document_text"
        if media in {"text/html", "application/xhtml+xml"}:
            parser = _PageHTML(uri)
            parser.feed(self._text(response))
            parser.close()
            text = "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())
            title, links = " ".join("".join(parser.title_parts).split()), parser.links
        elif media.startswith("text/") or media in {"application/json", "application/xml", "application/atom+xml"}:
            text = self._text(response)
        elif media == "application/pdf":
            if not response.body.startswith(b"%PDF-") or not response.body.rstrip().endswith(b"%%EOF"):
                raise GroundingWebError("invalid_pdf", "PDF header or final EOF is missing")
            try:
                extracted = self.pdf_extractor.extract_document(response.body, max_pages=100)
                if not isinstance(extracted, PdfDocument) or extracted.page_count > 100:
                    raise ValueError("invalid PDF extraction")
                text = "\n\n".join(f"--- Physical page {number} ---\n{page}" for number, page in enumerate(extracted.pages, 1))
                scope = "pdf_physical_pages"
            except (LiteratureError, ValueError, OSError) as exc:
                raise GroundingWebError("pdf_extraction_failed", "bounded PDF extraction failed") from exc
        else:
            raise GroundingWebError("unsupported_media", f"unsupported web media type: {media or 'missing'}")
        return {"requested_url": url, "url": uri, "title": title[:512], "media_type": media,
                "text": text[:max_chars], "text_scope": scope, "links": links,
                "truncated": len(text) > max_chars, "source_is_untrusted": True}

    def document(self, url: str, *, max_seconds: float | None = None, http_calls: list[int] | None = None) -> dict[str, Any]:
        """Prepare exact bytes off the Host thread; raw fields never enter a tool reply."""
        budget = self.max_seconds if max_seconds is None else min(self.max_seconds, max_seconds)
        if budget <= 0: raise GroundingWebError("timed_out", "document work window expired")
        deadline = self.clock() + budget
        uri, response = self._read(url, max_seconds=budget, http_calls=http_calls)
        media = (response.media_type or "").split(";", 1)[0].strip().lower()
        title = ""
        if media == "application/pdf":
            if not response.body.startswith(b"%PDF-") or not response.body.rstrip().endswith(b"%%EOF"):
                raise GroundingWebError("invalid_pdf", "PDF header or final EOF is missing")
            try:
                pages = self.pdf_extractor.extract_document(response.body, max_pages=100,
                    timeout_seconds=deadline - self.clock()).pages
            except (LiteratureError, OSError) as exc:
                raise GroundingWebError("pdf_extraction_failed", "bounded PDF document extraction failed") from exc
        elif media in {"text/html", "application/xhtml+xml"}:
            parser = _PageHTML(uri)
            parser.feed(self._text(response)); parser.close()
            title = " ".join("".join(parser.title_parts).split())[:512]
            pages = ("\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip()),)
        elif media.startswith("text/") or media in {"application/json", "application/xml", "application/atom+xml"}:
            pages = (self._text(response),)
        else:
            raise GroundingWebError("unsupported_media", f"unsupported document type: {media or 'missing'}")
        return {"requested_url": url, "url": uri, "title": title, "media_type": media,
                "content": response.body, "pages": pages}


__all__ = ["GroundingWeb", "GroundingWebError", "PublicHttpsTransport", "WebTransport"]
