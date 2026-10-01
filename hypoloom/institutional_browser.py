"""Playwright adapter for an operator-authenticated Chromium session.

Only this adapter's worker uses Playwright. It neither reads cookie/storage APIs
nor types credentials. It disconnects without closing the researcher's browser.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from .institutional_access import MAX_DOCUMENT_BYTES, TRANSIENT_FETCH_REASONS, publisher
from .literature import PopplerPdfExtractor


_INSPECT = r"""() => {
  const meta = name => document.querySelector(`meta[name="${name}"]`)?.content || '';
  const body = (document.body?.innerText || '').slice(0, 120000);
  const network_error = document.querySelector('#main-frame-error .error-code')?.textContent?.trim() || '';
  const main = document.querySelector('.c-article-body, .article-sections, .article-body, #body, #bodymatter, article');
  const mainText = main?.innerText || '';
  const headings = [...(main?.querySelectorAll('h2,h3') || [])].map(x => x.innerText).join(' ');
  const pdf = meta('citation_pdf_url') ||
    [...document.querySelectorAll('a[href], iframe[src]')].map(x => ({
      url: x.href || x.src, text: x.innerText || '',
    })).find(x => /\.pdf(?:[?#]|$)|\/doi\/pdf\/|\/pdfft(?:[?#]|$)|\/stamp\/stamp\.jsp/i.test(x.url))?.url || '';
  const login = [...document.querySelectorAll('input[type="password"], input[autocomplete="one-time-code"]')]
      .some(x => x.getClientRects().length > 0) ||
      [...document.querySelectorAll('form')].some(x => x.getClientRects().length > 0 &&
        /sign in via your (?:institution|organization)|institutional login|统一身份认证|选择.*学校|find your (?:institution|organization)/i.test(x.innerText));
  const verification = /verify (?:that )?you are human|checking your browser|are you a robot|验证码|人机验证/i.test(body)
      || !!document.querySelector('iframe[src*="captcha"], iframe[src*="challenges.cloudflare"]');
  const denied = /your (?:institution|organization) (?:does not|doesn't) (?:have|provide) access|not included in your (?:institution|subscription)|机构.*(?:未订购|无权限)/i.test(body);
  const paywall = /purchase (?:this )?(?:article|pdf)|rent (?:this )?article|access through your institution|check access|subscribe to access/i.test(body);
  const full = mainText.length > 2500 && /methods?|results?|discussion|introduction/i.test(headings)
      && /references|bibliography/i.test(headings);
  const redirecting = /^\s*redirecting[.\s…]*$/i.test(document.title)
      || /^\s*redirecting[.\s…]*$/i.test(body);
  return {title: meta('citation_title') || document.title, doi: meta('citation_doi') || meta('dc.Identifier'),
    pdf, login, verification, denied, paywall, full, network_error,
    loading: document.readyState === 'loading', content_type: document.contentType,
    redirecting, text: full ? mainText : ''};
}"""

_FETCH = r"""async ({url, limit}) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const r = await fetch(url, {credentials: 'include', signal: controller.signal});
    if (!r.ok) return {error: r.status === 429 ? 'http_rate_limited' : r.status >= 500 ? 'http_server_error' : 'http_error'};
    if (!r.body) return null;
    const reader = r.body.getReader(); const chunks = []; let size = 0;
    while (true) {
      const {done, value} = await reader.read(); if (done) break;
      size += value.length;
      if (size > limit) {await reader.cancel(); return null;}
      chunks.push(value);
    }
    const bytes = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) {bytes.set(chunk, offset); offset += chunk.length;}
    if (String.fromCharCode(...bytes.slice(0, 5)) !== '%PDF-') return {error: 'pdf_response_not_pdf'};
    let binary = '';
    for (let i = 0; i < bytes.length; i += 16384)
      binary += String.fromCharCode(...bytes.subarray(i, i + 16384));
    return {data: btoa(binary), url: r.url};
  } catch (e) {return {error: e.name === 'AbortError' ? 'navigation_timeout' : 'network_error'};}
  finally {clearTimeout(timer);}
}"""


def _public_reference(url):
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


def _same_article(requested, current):
    a, b = urlsplit(requested), urlsplit(current)
    if publisher(requested) != publisher(current):
        return False
    # PDF representations are the same article, not a neighbouring article redirect.
    path_a = _article_path(a.path)
    path_b = _article_path(b.path)
    return path_a == path_b


def _article_path(path):
    return (path.rstrip("/").removesuffix(".pdf").replace("/content/pdf/", "/article/")
            .replace("/doi/pdf/", "/doi/").replace("/doi/full/", "/doi/"))


def _landing_url(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, _article_path(parts.path), parts.query, ""))


def _elsevier_article_url(url):
    """Only rewrite the exact HTTPS bridge and a compact journal PII, never an SSO URL."""
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != "linkinghub.elsevier.com"
            or parts.username or parts.password or parts.port not in (None, 443)):
        return None
    match = re.fullmatch(r"/retrieve/pii/(S[0-9X]{16})/?", parts.path)
    return "https://www.sciencedirect.com/science/article/pii/" + match[1] if match else None


def _transit_page(url, info):
    return (urlsplit(url).hostname in {"doi.org", "linkinghub.elsevier.com"}
            or info.get("redirecting", False))


def _navigate(page, target):
    # CDP can point at another machine: cancel download paths, fetch bytes in-page.
    def cancel_download(download):
        download.cancel()
    page.on("download", cancel_download)
    try:
        response = page.goto(target, wait_until="domcontentloaded", timeout=25000)
        status = response.status if response is not None else 200
        return ("http_rate_limited" if status == 429 else "http_server_error" if status >= 500
                else "http_error" if status >= 400 else None)
    except Exception as exc:
        return _navigation_reason(exc)
    finally:
        page.remove_listener("download", cancel_download)


def _navigation_reason(error):
    # Browser exceptions often include SSO URLs. Export only a fixed reason code.
    text = str(error)
    if isinstance(error, TimeoutError) or type(error).__name__ == "TimeoutError":
        return "navigation_timeout"
    if "Execution context was destroyed" in text or "Cannot find context with specified id" in text:
        return "redirect_unresolved"
    for marker, reason in (("ERR_NETWORK_CHANGED", "network_changed"),
            ("ERR_ABORTED", "redirect_unresolved"),
            ("ERR_NAME_NOT_RESOLVED", "dns_failed"), ("ERR_CONNECTION_", "connection_failed"),
            ("ERR_BLOCKED_", "request_blocked"), ("ERR_TIMED_OUT", "navigation_timeout")):
        if marker in text:
            return reason
    return "network_error"


def _login_url(url):
    # An intermediate resolver/redirect is not evidence of an expired session.
    path = urlsplit(url).path.lower()
    return bool(re.search(r"/(?:login|signin|sign-in|sso|saml2?|shibboleth)(?:/|$|[.;])", path)
                or re.search(r"/(?:oauth2?|oidc)/authorize(?:/|$)", path))


class PlaywrightInstitutionalBrowser:
    def __init__(self, endpoint, *, library_url=None, ui_url=None, extractor=None):
        parts = urlsplit(endpoint)
        if (parts.scheme != "http" or parts.hostname not in {"localhost", "127.0.0.1", "::1"}
                or not parts.port or parts.username or parts.password or parts.query or parts.fragment
                or parts.path not in {"", "/"}):
            raise ValueError("institutional browser endpoint must be an HTTP loopback CDP port")
        if library_url:
            library = urlsplit(library_url)
            if library.scheme != "https" or not library.hostname or library.username or library.password or library.fragment:
                raise ValueError("institutional library entry must be an HTTPS URL without credentials")
        if ui_url is not None:
            ui = urlsplit(ui_url)
            if (ui.scheme not in {"http", "https"} or ui.hostname not in {"localhost", "127.0.0.1", "::1"}
                    or not ui.port or ui.username or ui.password or ui.query or ui.fragment
                    or ui.path not in {"", "/"}):
                raise ValueError("institutional browser UI must be a loopback HTTP(S) origin with an explicit port")
        self.endpoint, self.library_url = endpoint, library_url
        self.ui_url = ui_url
        self.extractor = extractor or PopplerPdfExtractor()
        self._playwright = self._browser = None
        self._tabs = {}
        self._pdf_pending = {}

    @property
    def configuration(self):
        return {"browser_endpoint": self.endpoint, "library_url": self.library_url, "browser_ui_url": self.ui_url}

    def _connect(self):
        if self._browser is not None and self._browser.is_connected():
            return self._browser.contexts[0]
        self.close()
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp(self.endpoint, timeout=5000)
        if not self._browser.contexts:
            raise ValueError("authenticated browser context is unavailable")
        return self._browser.contexts[0]

    def _pdf(self, content, requested, final, title, identity=""):
        if (len(content) > MAX_DOCUMENT_BYTES or not content.startswith(b"%PDF-")
                or not content.rstrip().endswith(b"%%EOF")):
            return {"state": "failed"}
        pages = self.extractor.extract_document(content, max_pages=2000, timeout_seconds=15).pages
        return {"state": "available", "document": {"requested_url": requested,
            "url": _public_reference(final), "title": title[:512], "identity": identity,
            "content": content, "media_type": "application/pdf", "pages": pages}}

    def import_pdf(self, path, url, title):
        source = Path(path)
        if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_DOCUMENT_BYTES:
            raise ValueError("import requires a bounded regular PDF file")
        with source.open("rb") as stream:
            content = stream.read(MAX_DOCUMENT_BYTES + 1)
        return self._pdf(content, url, url, title)

    def _follow_pdf_page(self, provider, page, requested, *, navigate=None):
        # Keep the already-identified article while its owned tab executes the
        # publisher's PDF interstitial. Auth waits retain this same tab, not a reload.
        article, title, identity = self._pdf_pending[provider]
        pii = urlsplit(article).path.rsplit('/', 1)[-1]

        def matching_pdf(target):
            parts = urlsplit(target)
            if (parts.scheme != 'https' or parts.username or parts.password
                    or parts.port not in (None, 443)):
                return False
            return ((publisher(target) == provider and parts.path == urlsplit(article).path + '/pdfft')
                    or (parts.hostname == 'pdf.sciencedirectassets.com'
                        and f'/1-s2.0-{pii}/' in parts.path and parts.path.endswith('/main.pdf')))

        def finish(result):
            waiting = result['state'] in {'login_required', 'verification_required'}
            self._tabs[provider] = (page, requested if waiting else None, waiting)
            if not waiting:
                self._pdf_pending.pop(provider, None)
            return result

        reason = _navigate(page, navigate) if navigate else None
        for attempt in range(51):
            try:
                info = page.evaluate(_INSPECT)
                if info.get('network_error') or page.url.startswith('chrome-error:'):
                    reason = _navigation_reason(info.get('network_error') or 'network_error')
                    break
                if info.get('content_type') == 'application/pdf':
                    if not matching_pdf(page.url):
                        return finish({'state': 'identity_unconfirmed'})
                    data = page.evaluate(_FETCH, {'url': page.url, 'limit': MAX_DOCUMENT_BYTES})
                    if data and data.get('data'):
                        if not matching_pdf(data['url']):
                            return finish({'state': 'identity_unconfirmed'})
                        return finish(self._pdf(base64.b64decode(data['data'], validate=True),
                            requested, data['url'], title, identity))
                    reason = (data or {}).get('error') or 'pdf_response_not_pdf'
                    break
                if info['verification']:
                    return finish({'state': 'verification_required'})
                if info['login'] or _login_url(page.url):
                    return finish({'state': 'login_required'})
                if info['denied']:
                    return finish({'state': 'access_denied'})
                if reason and reason not in {'redirect_unresolved', 'navigation_timeout'}:
                    break
            except Exception as exc:
                reason = _navigation_reason(exc)
                if reason != 'redirect_unresolved':
                    break
            if attempt < 50:
                page.wait_for_timeout(500)
        return finish({'state': 'fetch_unavailable', 'reason': reason or 'pdf_response_not_pdf'})

    def fetch(self, url, *, force=False, focus=False):
        provider = publisher(url)
        if provider is None:
            return {"state": "failed"}
        context = self._connect()
        prior = self._tabs.get(provider)
        if prior is None or prior[0].is_closed():
            if len(self._tabs) >= 8:
                return {"state": "failed"}
            page = context.new_page()
            page.set_default_timeout(5000)
            prior = (page, None, False)
        page, selected, waiting = prior
        if provider in self._pdf_pending:
            if selected == url and not force:
                if focus:
                    page.bring_to_front()
                return self._follow_pdf_page(provider, page, url)
            self._pdf_pending.pop(provider, None)
        bridge_used = False
        def unavailable(reason):
            # A failed navigation must never admit the previous article's DOM.
            # Clear the selected URL so the next poll performs a real navigation.
            self._tabs[provider] = (page, None, False)
            return {"state": "fetch_unavailable", "reason": reason}

        def inspect():
            if page.url.startswith("chrome-error:"):
                return None, "network_error"
            try:
                info = page.evaluate(_INSPECT)
            except Exception as exc:
                return None, _navigation_reason(exc)
            if info.get("network_error"):
                return None, _navigation_reason(info["network_error"])
            return info, None

        def auth_state(info):
            if info["verification"]:
                state = "verification_required"
            elif _login_url(page.url) or (info["login"] and not (info["full"] or info["pdf"])):
                state = "login_required"
            else:
                return None
            self._tabs[provider] = (page, url, True)
            return {"state": state}

        def inspect_settled():
            # DOI and linkinghub can run JS redirects after DOMContentLoaded.
            # A recognized PII bridge can be replaced once; other transit pages wait.
            nonlocal bridge_used
            for attempt in range(17):
                info, error = inspect()
                if error and error != "redirect_unresolved":
                    return info, error
                if info is not None:
                    if info["verification"] or info["login"] or _login_url(page.url):
                        return info, None
                    direct = _elsevier_article_url(page.url) if provider == "ScienceDirect" and not bridge_used else None
                    if direct:
                        bridge_used = True
                        error = _navigate(page, direct)
                        if error and error != "redirect_unresolved":
                            # A slow destination can itself have arrived at SSO/MFA.
                            arrived, inspect_error = inspect()
                            if not inspect_error and auth_state(arrived):
                                return arrived, None
                            return None, error
                        continue
                    if publisher(page.url) == provider and not _transit_page(page.url, info) and not info.get("loading"):
                        # ScienceDirect renders PDF controls after DOMContentLoaded.
                        # An initial abstract with DOI metadata is not yet a missing-PDF verdict.
                        if (provider != "ScienceDirect" or attempt == 16
                                or any(info[key] for key in ("pdf", "full", "denied", "paywall"))):
                            return info, None
                if attempt < 16:
                    page.wait_for_timeout(500)
            reason = "navigation_timeout" if info and info.get("loading") else "redirect_unresolved"
            return None, reason

        if focus:
            page.bring_to_front()
        if waiting and not force:
            inspected, error = inspect_settled()
            if error:
                return unavailable(error)
            authentication = auth_state(inspected)
            if authentication:
                return authentication
            # A successful SSO may return to the publisher home page. Revisit
            # the selected article once, only after leaving the sign-in page.
        if selected != url or force or (waiting and not _same_article(url, page.url)):
            self._tabs[provider] = (page, url, False)
            target = self.library_url if focus and self.library_url else _landing_url(url)
            for attempt in range(2):
                reason = _navigate(page, target)
                if reason is None:
                    break
                # A timed-out navigation may already have reached SSO/MFA.
                # Inspect only for authentication here, never admit stale text.
                inspected, error = inspect()
                if not error:
                    authentication = auth_state(inspected)
                    if authentication:
                        return authentication
                if attempt or reason not in TRANSIENT_FETCH_REASONS or reason == "http_rate_limited":
                    return unavailable(reason)
                page.wait_for_timeout(500)
                inspected, error = inspect()
                if not error:
                    authentication = auth_state(inspected)
                    if authentication:
                        return authentication
                    direct = _elsevier_article_url(page.url) if provider == "ScienceDirect" and not bridge_used else None
                    if direct:
                        target, bridge_used = direct, True
        info, error = inspect_settled()
        if error:
            return unavailable(error)
        current = page.url
        authentication = auth_state(info)
        if authentication:
            return authentication
        if publisher(current) != provider or _transit_page(current, info):
            return unavailable("redirect_unresolved")
        doi_request = urlsplit(url).path.lstrip("/").lower() if urlsplit(url).hostname == "doi.org" else None
        if not _same_article(url, current) and (not doi_request or (info["doi"] or "").lower() != doi_request):
            return {"state": "identity_unconfirmed"}
        if info["denied"]:
            return {"state": "access_denied"}
        pdf_url = urljoin(current, info["pdf"]) if info["pdf"] else (url if urlsplit(url).path.endswith(".pdf") else "")
        pdf_error = None
        if pdf_url and urlsplit(pdf_url).scheme == "https":
            try:
                data = page.evaluate(_FETCH, {"url": pdf_url, "limit": MAX_DOCUMENT_BYTES})
            except Exception as exc:
                data = {"error": _navigation_reason(exc)}
            pdf_error = (data or {}).get("error")
            if data and data.get("data"):
                return self._pdf(base64.b64decode(data["data"], validate=True), url, data["url"], info["title"], info["doi"] or "")
        if info["full"] and info["doi"]:
            content = info["text"].encode("utf-8")
            return {"state": "available", "document": {"requested_url": url,
                "url": _public_reference(current), "title": info["title"][:512],
                "identity": info["doi"], "media_type": "text/plain", "content": content,
                "pages": (info["text"],)}}
        if pdf_error:
            if (pdf_error == 'pdf_response_not_pdf' and provider == 'ScienceDirect'
                    and publisher(pdf_url) == provider
                    and re.fullmatch(r'/science/article/pii/S[0-9X]{16}', urlsplit(current).path)
                    and urlsplit(pdf_url).path == urlsplit(current).path + '/pdfft'):
                self._pdf_pending[provider] = (current, info['title'], info['doi'] or '')
                return self._follow_pdf_page(provider, page, url, navigate=pdf_url)
            return unavailable(pdf_error)
        if info["login"]:
            self._tabs[provider] = (page, url, True)
            return {"state": "login_required"}
        if info["paywall"]:
            return {"state": "access_required"}
        return {"state": "no_full_text"}

    def close(self):
        for page, _, _ in self._tabs.values():
            try:
                if not page.is_closed():
                    page.close(run_before_unload=False)
            except Exception:
                pass
        self._tabs.clear()
        self._pdf_pending.clear()
        if self._playwright is not None:
            self._playwright.stop()
        self._playwright = self._browser = None
