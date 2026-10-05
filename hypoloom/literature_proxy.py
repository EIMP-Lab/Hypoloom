"""Explicit exact-domain CONNECT routing for Host-owned literature requests.

The existing private credential source owns the endpoint. This module never
reads proxy environment variables, switches nodes or falls back to direct for a
selected domain. CONNECT names an already-approved public IP; TLS still verifies
the original publisher/API hostname. Proxy routing is not evidence of its exit
node: IP CONNECT may not match a proxy's hostname-based rules.
"""

from __future__ import annotations

import http.client
import ipaddress
import re
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
                 proxy_url_loader: Callable[[], str] | None = None) -> None:
        self.proxy_domains = normalize_proxy_domains(proxy_domains)
        if proxy_url is not None and proxy_url_loader is not None:
            raise LiteratureProxyError("use one literature proxy endpoint source")
        if proxy_url_loader is not None and not callable(proxy_url_loader):
            raise LiteratureProxyError("literature proxy endpoint loader must be callable")
        if bool(self.proxy_domains) != (proxy_url is not None or proxy_url_loader is not None):
            raise LiteratureProxyError("literature proxy requires both domains and an endpoint source")
        if proxy_url is not None:
            proxy_address(proxy_url)
        self._proxy_url, self._proxy_url_loader = proxy_url, proxy_url_loader

    @property
    def configuration(self) -> dict:
        return {"proxy_domains": list(self.proxy_domains), "proxy_policy": "proxy_only"}

    def routes(self, uri: str) -> bool:
        return urlsplit(uri).hostname in self.proxy_domains

    def for_url(self, uri: str) -> str | None:
        if not self.routes(uri):
            return None
        try:
            value = self._proxy_url if self._proxy_url_loader is None else self._proxy_url_loader()
            proxy_address(value)
        except Exception:
            raise LiteratureProxyError("literature proxy endpoint source is unavailable or invalid") from None
        return value


def proxy_router_from_args(args) -> LiteratureProxyRouter | None:
    from .credentials import LITERATURE_PROXY_KEY, optional_credential_loader
    domains = getattr(args, "literature_proxy_domain", None) or ()
    loader = optional_credential_loader(getattr(args, "credentials_file", None), LITERATURE_PROXY_KEY)
    if not domains and loader is None:
        return None
    router = LiteratureProxyRouter(proxy_domains=domains, proxy_url_loader=loader)
    router.for_url("https://" + router.proxy_domains[0])  # Validate the private endpoint without a network probe.
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
    except (OSError, http.client.HTTPException):
        raise LiteratureProxyError("literature proxy tunnel could not be established") from None
    finally:
        tunnel.close()


def validate_response_peer(response, approved_addresses: tuple[str, ...], *, proxy_expected: bool = False) -> None:
    """Keep the observed socket peer distinct from an explicit tunnel target."""
    from .literature import LiteratureValidationError, _public_address
    tunnel = getattr(response, "proxy_tunnel_address", None)
    if tunnel is not None:
        if not proxy_expected:
            raise LiteratureValidationError("an unconfigured proxy response is not admitted")
        if _public_address(tunnel, "proxy tunnel destination") not in approved_addresses:
            raise LiteratureValidationError("proxy tunnel destination differs from approved DNS addresses")
        try:
            ipaddress.ip_address(response.peer_address)
        except ValueError:
            raise LiteratureValidationError("proxy response lacks a valid socket peer") from None
    else:
        if proxy_expected:
            raise LiteratureValidationError("configured proxy response omitted its tunnel destination")
        if _public_address(response.peer_address, "HTTPS peer") not in approved_addresses:
            raise LiteratureValidationError("HTTPS peer differs from approved DNS addresses")
