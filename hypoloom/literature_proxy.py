"""Explicit exact-domain CONNECT routing for Host-owned literature requests.

The existing private credential source owns the endpoint. This module never
reads proxy environment variables, switches nodes or falls back to direct for a
proxy-only domain. Optional auto routing shares the caller's original deadline.
CONNECT names an already-approved public IP; TLS still verifies
the original publisher/API hostname. Proxy routing is not evidence of its exit
node: IP CONNECT may not match a proxy's hostname-based rules.
"""

from __future__ import annotations

import http.client
import errno
import ipaddress
import re
import time
from collections import OrderedDict
from threading import Lock
from typing import Callable, Sequence
from urllib.parse import urlsplit


class LiteratureProxyError(ValueError):
    code = "proxy_unavailable"


def normalize_proxy_domains(values: Sequence[str]) -> tuple[str, ...]:
    from .literature import _uri_parts
    if isinstance(values, (str, bytes)) or not isinstance(values, (tuple, list)):
        raise LiteratureProxyError("literature proxy domains must be a list of exact DNS names")
    result = []
    for value in values:
        if (not isinstance(value, str) or len(value) > 253 or not value.isascii()
                or any(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) is None
                       for label in value.split("."))):
            raise LiteratureProxyError("literature proxy domain is not an exact DNS name")
        try:
            ipaddress.ip_address(value)
        except ValueError:
            pass
        else:
            raise LiteratureProxyError("literature proxy domains must be DNS names")
        normalized = value.lower()
        try:
            _uri_parts("https://" + normalized, "literature proxy domain")
        except ValueError:
            raise LiteratureProxyError("literature proxy domain is not a public DNS name") from None
        if normalized in result:
            raise LiteratureProxyError("literature proxy domains must be unique")
        result.append(normalized)
    return tuple(result)


def proxy_address(value: str) -> tuple[str, int]:
    # Keep one existing origin validator shared with notification CONNECT.
    from .notification_tailer import _proxy_address
    try:
        return _proxy_address(value)
    except ValueError:
        raise LiteratureProxyError("literature proxy requires a credential-free HTTP origin") from None


class LiteratureProxyRouter:
    validate_domains = staticmethod(normalize_proxy_domains)

    def __init__(self, *, proxy_domains: Sequence[str] = (), proxy_url: str | None = None,
                 proxy_url_loader: Callable[[], str] | None = None,
                 default_route: str = "direct", direct_domains: Sequence[str] = ()) -> None:
        self.proxy_domains = normalize_proxy_domains(proxy_domains)
        self.direct_domains = normalize_proxy_domains(direct_domains)
        if default_route not in {"direct", "auto", "proxy_only"}:
            raise LiteratureProxyError("literature default route must be direct, auto or proxy_only")
        if set(self.proxy_domains) & set(self.direct_domains):
            raise LiteratureProxyError("literature direct and proxy domains must not overlap")
        self.default_route = default_route
        if proxy_url is not None and proxy_url_loader is not None:
            raise LiteratureProxyError("use one literature proxy endpoint source")
        if proxy_url_loader is not None and not callable(proxy_url_loader):
            raise LiteratureProxyError("literature proxy endpoint loader must be callable")
        if bool(self.proxy_domains or default_route != "direct") != (proxy_url is not None or proxy_url_loader is not None):
            raise LiteratureProxyError("literature proxy requires both domains and an endpoint source")
        if proxy_url is not None:
            proxy_address(proxy_url)
        self._proxy_url, self._proxy_url_loader = proxy_url, proxy_url_loader
        self._preferred: OrderedDict[str, float] = OrderedDict()
        self._lock = Lock()

    @property
    def configuration(self) -> dict:
        return {"proxy_domains": list(self.proxy_domains),
                **({"default_route": self.default_route, "direct_domains": list(self.direct_domains)}
                   if self.default_route != "direct" or self.direct_domains else {"proxy_policy": "proxy_only"})}

    def policy(self, uri: str) -> str:
        host = urlsplit(uri).hostname
        if host in self.direct_domains: return "direct"
        if host in self.proxy_domains: return "proxy_only"
        return self.default_route

    def routes(self, uri: str) -> bool:
        return self.policy(uri) != "direct"

    def peer_policy(self, uri: str) -> bool | None:
        policy = self.policy(uri)
        return None if policy == "auto" else policy == "proxy_only"

    def for_url(self, uri: str) -> str | None:
        if not self.routes(uri):
            return None
        return self.endpoint()

    def endpoint(self) -> str:
        try:
            value = self._proxy_url if self._proxy_url_loader is None else self._proxy_url_loader()
            proxy_address(value)
        except Exception:
            raise LiteratureProxyError("literature proxy endpoint source is unavailable or invalid") from None
        return value

    def request(self, uri: str, call, *, deadline: float):
        """At most two routes, one deadline; no HTTP-error/node rotation.

        A successful auto fallback is preferred for five minutes (at most 256
        hosts), only in this router's memory. It is not evidence of VPN egress.
        """
        from .network_io import NetworkOperationError
        host = urlsplit(uri).hostname
        policy = self.policy(uri)
        with self._lock:
            preferred = self._preferred.get(host, 0) > time.monotonic()
        routes = (["proxy"] if policy == "proxy_only" else ["direct"] if policy == "direct"
                  else ["proxy", "direct"] if preferred else ["direct", "proxy"])
        observations = []
        for index, route in enumerate(routes):
            started = time.monotonic()
            # Reserve time for the alternative; never multiply the caller's bound.
            route_deadline = (started + (deadline - started) / 2
                              if index == 0 and len(routes) == 2 else deadline)
            entry = {"route": route, "source_host": host}
            try:
                result = call(None if route == "direct" else self.endpoint(), route_deadline)
            except NetworkOperationError as exc:
                entry.update(failure_code=exc.code, http_status=exc.http_status,
                    transport_phase=exc.transport_phase, received_bytes=exc.received_bytes,
                    seconds=max(0, time.monotonic() - started))
                observations.append(entry)
                exc.network_routes = tuple(observations)
                # DNS still must resolve/validate locally on either route. Auth,
                # certificate, challenge, cancellation and HTTP failures never retry.
                eligible = (exc.code in {"timed_out", "connection_failed"}
                    and exc.transport_phase in {"tcp_connect", "tls_handshake", "request_send", "response_headers", "response_body"}
                    and (exc.http_status is None or exc.http_status == 200))
                if not eligible or index + 1 == len(routes) or time.monotonic() >= deadline:
                    raise
                continue
            entry.update(http_status=result["status_code"], seconds=max(0, time.monotonic() - started))
            observations.append(entry)
            if policy == "auto":
                with self._lock:
                    if route == "proxy" and 200 <= result["status_code"] < 400:
                        self._preferred[host] = time.monotonic() + 300
                        self._preferred.move_to_end(host)
                        while len(self._preferred) > 256: self._preferred.popitem(last=False)
                    elif route == "direct": self._preferred.pop(host, None)
            return {**result, "network_routes": tuple(observations)}


def proxy_router_from_args(args) -> LiteratureProxyRouter | None:
    from .credentials import LITERATURE_PROXY_KEY, optional_credential_loader
    domains = getattr(args, "literature_proxy_domain", None) or ()
    direct = getattr(args, "literature_direct_domain", None) or ()
    default = getattr(args, "literature_default_route", None) or "direct"
    loader = optional_credential_loader(getattr(args, "credentials_file", None), LITERATURE_PROXY_KEY)
    if not domains and not direct and loader is None and default == "direct":
        return None
    router = LiteratureProxyRouter(proxy_domains=domains, direct_domains=direct,
        default_route=default, proxy_url_loader=loader)
    if loader is not None: router.endpoint()  # Local validation only, no network probe.
    return router


def connect_tunnel(proxy_url: str, address: str, *, remaining: Callable[[], float]):
    """Open one tunnel; only destination IP/port cross its cleartext CONNECT."""
    from .literature import _public_address
    address = _public_address(address, "proxy tunnel destination")
    host, port = proxy_address(proxy_url)
    tunnel = http.client.HTTPConnection(host, port, timeout=remaining())
    authority = f"[{address}]:443" if ":" in address else f"{address}:443"
    tunnel.set_tunnel(address, 443, headers={"Host": authority})
    try:
        tunnel.connect()
        if tunnel.sock is None:
            raise LiteratureProxyError("literature proxy tunnel has no socket")
        peer = str(ipaddress.ip_address(tunnel.sock.getpeername()[0]))
        tunnel.sock.settimeout(remaining())
        stream, tunnel.sock = tunnel.sock, None
        return stream, peer
    except (TimeoutError, LiteratureProxyError):
        raise
    except OSError as exc:
        # CONNECT HTTP errors (including proxy authentication) have no errno.
        # Only actual socket failures may activate an explicitly allowed route.
        if exc.errno in {errno.ECONNREFUSED, errno.ECONNRESET, errno.ECONNABORTED,
                         errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EPIPE}:
            from .network_io import NetworkOperationError
            raise NetworkOperationError("connection_failed", transport_phase="tcp_connect") from None
        raise LiteratureProxyError("literature proxy tunnel could not be established") from None
    except http.client.HTTPException:
        raise LiteratureProxyError("literature proxy tunnel could not be established") from None
    finally:
        tunnel.close()


def validate_response_peer(response, approved_addresses: tuple[str, ...], *, proxy_expected: bool | None = False) -> None:
    """Keep the observed socket peer distinct from an explicit tunnel target."""
    from .literature import LiteratureValidationError, _public_address
    tunnel = getattr(response, "proxy_tunnel_address", None)
    if tunnel is not None:
        if proxy_expected is False:
            raise LiteratureValidationError("an unconfigured proxy response is not admitted")
        if _public_address(tunnel, "proxy tunnel destination") not in approved_addresses:
            raise LiteratureValidationError("proxy tunnel destination differs from approved DNS addresses")
        try:
            ipaddress.ip_address(response.peer_address)
        except ValueError:
            raise LiteratureValidationError("proxy response lacks a valid socket peer") from None
    else:
        if proxy_expected is True:
            raise LiteratureValidationError("configured proxy response omitted its tunnel destination")
        if _public_address(response.peer_address, "HTTPS peer") not in approved_addresses:
            raise LiteratureValidationError("HTTPS peer differs from approved DNS addresses")
