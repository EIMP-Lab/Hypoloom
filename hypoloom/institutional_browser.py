"""Playwright adapter for an operator-authenticated Chromium session.

Only this adapter's worker uses Playwright. It neither reads cookie/storage APIs
nor types credentials. It disconnects without closing the researcher's browser.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path
from time import monotonic
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from .institutional_access import (MAX_DOCUMENT_BYTES, PROVIDERS, TRANSIENT_FETCH_REASONS, publisher,
    pdf_identity, identity_from_url, validate_document)
from .literature import PopplerPdfExtractor


def windows_browser_command(port):
    paths = (r"$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
             r"$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
             r"${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
             r"${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
             r"$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe")
    return ('$researchBrowser = @(\n' + '\n'.join('  "' + path + '"' for path in paths)
        + '\n) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1\n'
        + "if (-not $researchBrowser) { throw 'Chrome/Edge not found; set the actual executable path.' }\n"
        + r'$researchProfile = Join-Path $env:LOCALAPPDATA ("Hypoloom\Institutional-" + [IO.Path]::GetFileNameWithoutExtension($researchBrowser))'
        + f'\n& $researchBrowser --remote-debugging-address=127.0.0.1 --remote-debugging-port={port} "--user-data-dir=$researchProfile" about:blank')


_INSPECT = r"""() => {
  const meta = name => document.querySelector(`meta[name="${name}"]`)?.content || '';
  const ieeeDoi = location.hostname === 'ieeexplore.ieee.org' ? window.xplGlobal?.document?.metadata?.doi : '';
  const body = (document.body?.innerText || '').slice(0, 120000);
  const network_error = document.querySelector('#main-frame-error .error-code')?.textContent?.trim() || '';
  const main = document.querySelector('.c-article-body, .article-sections, .article-body, #body, #bodymatter, article');
  const mainText = main?.innerText || '';
  const headings = [...(main?.querySelectorAll('h2,h3') || [])].map(x => x.innerText).join(' ');
  const pdf = meta('citation_pdf_url') ||
    [...document.querySelectorAll('a[href], iframe[src]')].map(x => ({
      url: x.href || x.src, text: x.innerText || '',
    })).find(x => /\.pdf(?:[?#]|$)|\/doi\/pdf\/|\/pdfft(?:[?#]|$)|\/stamp\/stamp\.jsp|\/stampPDF\/getPDF\.jsp/i.test(x.url))?.url || '';
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
  return {title: meta('citation_title') || document.title, doi: meta('citation_doi') || meta('dc.Identifier') || (typeof ieeeDoi === 'string' ? ieeeDoi : ''),
    pdf, login, verification, denied, paywall, full, network_error,
    loading: document.readyState === 'loading', content_type: document.contentType,
    redirecting, text: full ? mainText : ''};
}"""

_FETCH = r"""async ({url, limit, timeout = 15000}) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
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


def _ieee_pdf_url(article, target, *, viewer=False):
    """Bind IEEE's viewer/PDF representation to the arrived article number."""
    source, destination = urlsplit(article), urlsplit(target)
    number = re.fullmatch(r"/document/([0-9]+)", source.path.rstrip('/'))
    try:
        return bool(number and source.hostname == destination.hostname == 'ieeexplore.ieee.org'
            and destination.scheme == 'https' and destination.port in (None, 443)
            and not destination.username and not destination.password
            and destination.path == ('/stamp/stamp.jsp' if viewer else '/stampPDF/getPDF.jsp')
            and parse_qs(destination.query).get('arnumber') == [number[1]])
    except ValueError:
        return False


def _navigate(page, target, *, timeout=25000):
    # CDP can point at another machine: cancel download paths, fetch bytes in-page.
    def cancel_download(download):
        download.cancel()
    page.on("download", cancel_download)
    try:
        response = page.goto(target, wait_until="domcontentloaded", timeout=timeout)
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


def _connection_reason(error):
    # CDP errors can contain authorization headers or private callback URLs.
    # Classify locally; only these fixed reason codes leave the browser worker.
    if isinstance(error, TimeoutError) or type(error).__name__ == "TimeoutError":
        return "cdp_timeout"
    if isinstance(error, ModuleNotFoundError): return "browser_driver_unavailable"
    if type(error).__name__ == "TargetClosedError": return "cdp_connection_closed"
    text = str(error)
    if text == "authenticated browser context is unavailable": return "cdp_context_unavailable"
    if "ECONNREFUSED" in text: return "cdp_connection_refused"
    if "ECONNRESET" in text or "WebSocket was closed" in text: return "cdp_connection_closed"
    if re.search(r"(?:[Ss]tatus|response:)\s*(?:code:?\s*)?(401|403)\b", text): return "cdp_authorization_failed"
    if re.search(r"(?:[Ss]tatus|response:)\s*(?:code:?\s*)?5\d\d\b", text): return "cdp_endpoint_unavailable"
    return "cdp_connection_failed"


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
            if (library.scheme != "https" or not library.hostname or library.username or library.password
                    or library.fragment or library.query or library.path.rstrip('/').endswith('/idp/shibboleth')):
                raise ValueError("institutional library entry must be a stable HTTPS interactive URL without query/credentials, not SAML metadata")
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
        self._deadline = None

    @property
    def configuration(self):
        return {"browser_endpoint": self.endpoint, "library_url": self.library_url, "browser_ui_url": self.ui_url}

    def _remaining(self, cap):
        remaining = cap if self._deadline is None else min(cap, self._deadline - monotonic())
        if remaining <= 0:
            raise TimeoutError("institutional acquisition deadline")
        return remaining

    def _navigate(self, page, target):
        return _navigate(page, target, timeout=max(1, self._remaining(25) * 1000))

    def _wait(self, page):
        page.wait_for_timeout(self._remaining(.5) * 1000)

    def _connect(self):
        if self._browser is not None and self._browser.is_connected():
            return self._browser.contexts[0]
        self.close()
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp(self.endpoint, timeout=self._remaining(5) * 1000,
            **({"headers": self._headers} if getattr(self, "_headers", None) else {}))
        if not self._browser.contexts:
            raise ValueError("authenticated browser context is unavailable")
        return self._browser.contexts[0]

    def check_connection(self, *, timeout_seconds):
        """Use the real CDP/context connection without opening a publisher page."""
        self._deadline = monotonic() + timeout_seconds
        reason = "cdp_timeout"
        try:
            while monotonic() < self._deadline:
                try:
                    self._connect()
                    return {"state": "ready"}
                except Exception as exc:
                    # Startup/cleanup can exhaust the local budget before another
                    # CDP request is made. Keep the last actual failure in that case;
                    # a real network timeout still supersedes it below.
                    if (isinstance(exc, TimeoutError) and str(exc) == "institutional acquisition deadline"
                            and monotonic() >= self._deadline):
                        break
                    reason = _connection_reason(exc)
                    if getattr(self, "stopping", None) is not None and self.stopping.is_set(): break
                    from time import sleep
                    sleep(min(.25, max(0, self._deadline - monotonic())))
            return {"state": "browser_unavailable", "reason_code": reason}
        finally:
            self._deadline = None

    def _pdf(self, content, requested, final, title, identity=""):
        if (len(content) > MAX_DOCUMENT_BYTES or not content.startswith(b"%PDF-")
                or not content.rstrip().endswith(b"%%EOF")):
            return {"state": "failed"}
        pages = self.extractor.extract_document(content, max_pages=2000, timeout_seconds=self._remaining(15)).pages
        document = {"requested_url": requested,
            "url": _public_reference(final), "title": title[:512], "identity": pdf_identity(pages),
            "content": content, "media_type": "application/pdf", "pages": pages}
        expected = (identity if identity.startswith("doi:") else "doi:" + identity) if identity else identity_from_url(requested)
        try:
            validate_document(document, title=title, identity=expected)
        except ValueError:
            return {"state": "identity_unconfirmed"}
        return {"state": "available", "document": document}

    def _room_for_tab(self):
        for provider, (page, _, _) in list(self._tabs.items()):
            if page.is_closed():
                self._tabs.pop(provider)
                self._pdf_pending.pop(provider, None)
        if len(self._tabs) < 8: return True
        finished = next((name for name, (_, _, waiting) in self._tabs.items() if not waiting), None)
        if finished is None: return False
        page, _, _ = self._tabs.pop(finished)
        self._pdf_pending.pop(finished, None)
        page.close(run_before_unload=False)
        return True

    def prepare(self, provider, *, focus=True, timeout_seconds=120):
        """Open a public platform entry without treating its homepage as a paper."""
        entry = next(("https://" + domain + "/" for domain, name in PROVIDERS.items() if name == provider), None)
        if entry is None: raise ValueError("unsupported institutional provider")
        self._deadline = monotonic() + timeout_seconds
        try:
            try:
                context = self._connect()
            except Exception as exc:
                return {"state": "browser_unavailable", "reason": _connection_reason(exc)}
            prior = self._tabs.get(provider)
            if prior is None or prior[0].is_closed():
                if not self._room_for_tab(): return {"state": "tab_limit"}
                page = context.new_page()
                page.set_default_timeout(self._remaining(5) * 1000)
                self._tabs[provider] = (page, None, True)
                reason = self._navigate(page, self.library_url or entry)
                if reason: return {"state": "fetch_unavailable", "reason": reason}
            else:
                page = prior[0]
            if focus: page.bring_to_front()
            return {"state": "opened"}
        except Exception as exc:
            # A page/context failure is not proof that the browser or SSH exited.
            # Keep the queue's existing bounded retry, and export no exception text.
            reason = ("browser_target_closed" if type(exc).__name__ == "TargetClosedError" else
                      "browser_operation_timeout" if isinstance(exc, TimeoutError) or type(exc).__name__ == "TimeoutError"
                      else "browser_operation_failed")
            return {"state": "browser_unavailable", "reason": reason}
        finally:
            self._deadline = None

    def import_pdf(self, path, url, title, *, timeout_seconds=120):
        self._deadline = monotonic() + timeout_seconds
        try:
            return self._import_pdf(path, url, title)
        finally:
            self._deadline = None

    def _import_pdf(self, path, url, title):
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
            if provider == 'IEEE':
                return _ieee_pdf_url(article, target)
            parts = urlsplit(target)
            if (parts.scheme != 'https' or parts.username or parts.password
                    or parts.port not in (None, 443)):
                return False
            return ((publisher(target) == provider and parts.path == urlsplit(article).path + '/pdfft')
                    or (parts.hostname == 'pdf.sciencedirectassets.com'
                        and f'/1-s2.0-{pii}/' in parts.path and parts.path.endswith('/main.pdf')))

        def read_pdf(target):
            if not matching_pdf(target):
                return {'state': 'identity_unconfirmed'}
            data = page.evaluate(_FETCH, {'url': target, 'limit': MAX_DOCUMENT_BYTES, 'timeout': self._remaining(15) * 1000})
            if data and data.get('data'):
                if not matching_pdf(data['url']):
                    return {'state': 'identity_unconfirmed'}
                return self._pdf(base64.b64decode(data['data'], validate=True),
                    requested, data['url'], title, identity)
            return {'state': 'fetch_unavailable', 'reason': (data or {}).get('error') or 'pdf_response_not_pdf'}

        def finish(result):
            waiting = result['state'] in {'login_required', 'verification_required'}
            self._tabs[provider] = (page, requested if waiting else None, waiting)
            if not waiting:
                self._pdf_pending.pop(provider, None)
            return result

        reason = self._navigate(page, navigate) if navigate else None
        for attempt in range(51):
            try:
                self._remaining(5)
                info = page.evaluate(_INSPECT)
                if info.get('network_error') or page.url.startswith('chrome-error:'):
                    reason = _navigation_reason(info.get('network_error') or 'network_error')
                    break
                if info.get('content_type') == 'application/pdf':
                    return finish(read_pdf(page.url))
                if info['verification']:
                    return finish({'state': 'verification_required'})
                if info['login'] or _login_url(page.url):
                    return finish({'state': 'login_required'})
                if info['denied']:
                    return finish({'state': 'access_denied'})
                if reason and reason not in {'redirect_unresolved', 'navigation_timeout'}:
                    break
                if provider == 'IEEE' and info['pdf']:
                    return finish(read_pdf(urljoin(page.url, info['pdf'])))
            except Exception as exc:
                reason = _navigation_reason(exc)
                if reason != 'redirect_unresolved':
                    break
            if attempt < 50:
                self._wait(page)
        return finish({'state': 'fetch_unavailable', 'reason': reason or 'pdf_response_not_pdf'})

    def fetch(self, url, *, force=False, focus=False, timeout_seconds=120):
        self._deadline = monotonic() + timeout_seconds
        try:
            return self._fetch(url, force=force, focus=focus)
        except TimeoutError:
            return {"state": "fetch_unavailable", "reason": "navigation_timeout"}
        finally:
            self._deadline = None

    def _fetch(self, url, *, force=False, focus=False):
        provider = publisher(url)
        if provider is None:
            return {"state": "failed"}
        context = self._connect()
        prior = self._tabs.get(provider)
        if prior is None or prior[0].is_closed():
            if not self._room_for_tab():
                return {"state": "tab_limit"}
            page = context.new_page()
            page.set_default_timeout(self._remaining(5) * 1000)
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
                self._remaining(5)
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
                        error = self._navigate(page, direct)
                        if error and error != "redirect_unresolved":
                            # A slow destination can itself have arrived at SSO/MFA.
                            arrived, inspect_error = inspect()
                            if not inspect_error and auth_state(arrived):
                                return arrived, None
                            return None, error
                        continue
                    if publisher(page.url) == provider and not _transit_page(page.url, info) and not info.get("loading"):
                        # These publishers render PDF controls after DOMContentLoaded.
                        # An initial abstract with DOI metadata is not yet a missing-PDF verdict.
                        if (provider not in {"ScienceDirect", "IEEE"} or attempt == 16
                                or any(info[key] for key in ("pdf", "full", "denied", "paywall"))):
                            return info, None
                if attempt < 16:
                    self._wait(page)
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
                reason = self._navigate(page, target)
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
                self._wait(page)
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
        self._tabs[provider] = (page, url, False)
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
                data = page.evaluate(_FETCH, {"url": pdf_url, "limit": MAX_DOCUMENT_BYTES, "timeout": self._remaining(15) * 1000})
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
            ieee_viewer = provider == 'IEEE' and _ieee_pdf_url(current, pdf_url, viewer=True)
            sciencedirect_viewer = (provider == 'ScienceDirect'
                    and publisher(pdf_url) == provider
                    and re.fullmatch(r'/science/article/pii/S[0-9X]{16}', urlsplit(current).path)
                    and urlsplit(pdf_url).path == urlsplit(current).path + '/pdfft')
            if pdf_error == 'pdf_response_not_pdf' and (ieee_viewer or sciencedirect_viewer):
                self._pdf_pending[provider] = (current, info['title'], info['doi'] or '')
                return self._follow_pdf_page(provider, page, url, navigate=pdf_url)
            if pdf_error == 'pdf_response_not_pdf' and info['paywall']:
                return {"state": "access_required"}
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
