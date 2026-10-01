# Institutional full-text access

English · [简体中文](institutional-access.zh-CN.md)

Public discovery and PDF retrieval remain first. Optional institutional access
uses a researcher's authenticated Chromium browser. Prefer a persistent server
browser so a laptop can disconnect after login. The TUI groups unresolved
papers by publisher without opening repeated prompts. Other research continues;
available bytes do not establish that an Agent read a paper.

## Configure a new run

In the Linux Host's Python environment, from the repository root:

```bash
python -m pip install -e '.[institutional]'
command -v pdftotext
command -v pdfinfo
```

## Independent server browser (recommended)

Use the existing [jlesage/docker-chromium](https://github.com/jlesage/docker-chromium/tree/v26.09.1)
image for its remote GUI, CDP support and persistent profile. This explicitly
stores authenticated browser state on the server instead of the laptop; it is
never imported into Hypoloom's database, prompts, Profile, notifications or Git.

From the Linux server checkout, deploy the browser once:

```bash
docker compose -f config/institutional-browser.compose.yaml up -d
docker compose -f config/institutional-browser.compose.yaml ps
curl --fail http://127.0.0.1:9222/json/version
```

Docker and ports 5800/9222 must be available; check for old reverse tunnels
without stopping unrelated processes. Both published ports bind to server
loopback only. The image is pinned to `v26.09.1`; qualify upgrades separately.

For first login or expired sessions, temporarily forward the GUI from your PC:

```text
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:5800:127.0.0.1:5800 user@SERVER
```

Visit `http://127.0.0.1:5800/` to operate the **server's** Chromium. Sign in at
the institution/publisher and confirm one entitled full-text article. Closing
this page or SSH connection does not stop server acquisition. No laptop Chrome
debugging or reverse CDP tunnel is needed. For each new `serve` / `bootstrap launch`:

```text
--institutional-browser-url http://127.0.0.1:9222
--institutional-browser-ui-url http://127.0.0.1:5800
```

The optional library-entry flag still applies. TUI/Web display the server GUI
instructions. Login actions focus the server tab; credentials are entered only
there. Ending a Host closes its owned tabs, leaving the browser service alive.
Subsequent Hosts reuse valid sessions for each publisher.

Docker volume `hypoloom-institutional-browser_browser-profile` holds `/config`,
separately from code and research output. Never commit/export it or use `down -v`
for routine restarts. The container restarts with Docker unless explicitly
stopped; `KEEP_APP_RUNNING` restarts Chromium after it exits. Persistent cookies
can survive a browser restart, but session-only cookies, expiry or revoked access
may require reauthentication. Each publisher still needs its own entitlement.

The server needs its own institutional network route, VPN/proxy or supported
remote SSO. A laptop entitlement does not establish server access when a site
binds it to an IP/device. Qualify with article A, disconnect the GUI, then retrieve
article B from a new Host; synthetic session tests are not a subscription check.

## Laptop browser (optional)

Start a visible Chrome browser on the Windows laptop, using a dedicated profile:

```powershell
$researchChrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$researchProfile = Join-Path $env:LOCALAPPDATA 'Hypoloom\InstitutionalChrome'
& $researchChrome '--remote-debugging-address=127.0.0.1' '--remote-debugging-port=9222' "--user-data-dir=$researchProfile" 'about:blank'
```

Adjust the executable path if necessary. Sign in yourself in this browser.
[Chrome requires a non-default data directory for remote debugging](https://developer.chrome.com/blog/remote-debugging-port).
The browser keeps its own session; Hypoloom does not export cookies or storage.

For a Linux server with a Windows browser, leave this SSH tunnel running on the
laptop (replace the SSH destination):

```powershell
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -R 127.0.0.1:9222:127.0.0.1:9222 user@SERVER
```

CDP grants browser control. Bind it only to loopback, never a public interface.
No tunnel is needed when Host and browser share a machine. Add this option to
the new run's `python -m hypoloom serve ...` or
`python -m hypoloom.bootstrap launch ...` command:

```text
--institutional-browser-url http://127.0.0.1:9222
```

Optionally add `--institutional-library-url https://your-library-entry/` for a
stable public library entry, not a temporary authentication callback. Without
it, the TUI opens the publisher article. Do not change an existing run's frozen
configuration. Literature MAIN needs `web_fetch` in its frozen
`methodology.literature.tools` to consume recovered documents; the separate
`read_papers` model capability is optional.

## During research

Press `i` in the TUI, select a publisher and choose the login button. Complete
school selection, SSO or MFA in the browser. If the library leaves you on its
home page, navigate to the listed article in that same tab. The Host checks
waiting tabs approximately every 30 seconds without restarting login. Retry
is also available explicitly. A valid browser session serves later papers;
expired sessions or required verification still need the operator.

The Agent subsequently consumes ready documents through
`web_fetch(mode="document", url=...)`, admitting exact originals and extracted
text through the existing `/papers` workspace and archive. Downloading does not
rewrite finished research conclusions. Denial, missing full text and uncertain
identity stop automatic retries and remain explicit evidence gaps.

For manual import, download an entitled PDF yourself, transfer it to the Host,
select the matching paper in the TUI and enter its absolute **Host-side** path.
PDF, readable-text and DOI/title identity checks still apply. Requests without
a DOI or a reliable title may need to use a DOI URL before import.

The Web panel mirrors the TUI. Obtain an operator link from the TUI `i`
screen ("Web panel link"; plain read links cannot trigger actions) or start a
standalone `python -m hypoloom.web_observer --allow-literature-control`. The
Workbench "机构全文" tab lists pending papers by publisher and triggers the
same open-login, retry and PDF-import actions. Sign-in and verification stay
in your own browser pages; the panel never collects credentials.

## Laptop mode: fixed ports and persistent sessions

Reuse the browser and reverse tunnel across runs. Use port 9222 by convention,
and pass `--institutional-browser-url http://127.0.0.1:9222` to each new Host.
The Host preserves this endpoint; it does not allocate another CDP port.
The Web observer's dynamically assigned HTTP port is independent of CDP.
If 9222 is occupied, change the browser, tunnel and Host configuration together.

For Windows logon startup, create two Task Scheduler tasks triggered at user
logon. Run Chrome with the absolute executable path and the same dedicated
profile/9222 arguments above. Select "Run only when user is logged on" so SSO
and MFA stay visible. Use an absolute profile path in Task Scheduler arguments.
For the tunnel, use `C:\Windows\System32\OpenSSH\ssh.exe` with arguments:

```text
-N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -R 127.0.0.1:9222:127.0.0.1:9222 user@SERVER
```

First verify the SSH host key and noninteractive key/ssh-agent authentication
manually. Configure restart on failure and do not start another task instance
while one is running. Never store a password in task arguments. The laptop
must stay awake and connected; session expiry/MFA still requires the operator.
In laptop mode, browser cookies/storage stay in the laptop profile, never in the server.

## Diagnostics and development checks

`fetch_unavailable` is a retriable load failure, not a login request. Fixed reason
codes distinguish network changes, DNS/connection failure, timeouts, blocked
requests, HTTP errors and unresolved redirects. It does not reserve the whole
publisher queue. `access_required` means the page offers subscription or
institutional access; `login_required` needs a login form or login destination.
DNS safety errors include the hostname, without query strings or credentials.

After DOMContentLoaded, the adapter waits up to about eight seconds for asynchronous
redirects (including DOI/Elsevier linkinghub). These transit pages are never accepted
as articles. A transient navigation error gets at most one in-check retry; an already
visible login/MFA page is preserved. Repeated network/timeout/unresolved-redirect,
HTTP 5xx (`http_server_error`) or 429 (`http_rate_limited`) failures back off per paper
at 30, 60, 120, then 300 seconds between checks. A 429 does not trigger an immediate
retry. Success, leaving the transient state, or operator retry resets the backoff.
Auth checks retain their original cadence without restarting the login flow;
identity mismatch and denied access remain terminal. PDF streaming retains its
15-second abort timer. Unsupported mirror domains still need an adapter or PDF import.
New Hosts use this repair; existing frozen runs are not migrated or hot-patched.

For a recognized `https://linkinghub.elsevier.com/retrieve/pii/S...` journal route,
the adapter can navigate once to the corresponding ScienceDirect article, without
waiting for the bridge's JavaScript. The rewrite drops query/fragment values and
does not waive DOI identity checks. Login/MFA pages take priority over this hop.
Navigation now allows 25 seconds per attempt; PDF streaming still allows 15 seconds.
A page explicitly saying `Redirecting` remains retryable even on an article URL;
it must not become terminal `no_full_text`. This does not guarantee that an actual
article page exposes a readable PDF or that its institutional entitlement is valid.
ScienceDirect PDF controls may appear after the DOM is complete. An article without
PDF/full text or an explicit access outcome is inspected for up to about eight
seconds before returning `no_full_text`. If the bridge's own JS races the direct
hop (`ERR_ABORTED`), follow the resulting navigation within that bound instead of
immediately declaring a network failure; normal origin/DOI checks still apply.
If a PDF endpoint returns HTML or other non-PDF bytes, the queue reports retryable
`pdf_response_not_pdf`, not terminal missing full text. For an identified ScienceDirect
article's `/pdfft` link, the adapter navigates its owned tab to execute interstitial
scripts, waiting up to 25 seconds after navigation. Login/verification retains that
tab for the operator; subsequent checks observe it without restarting the navigation.
Only the same article's PDF endpoint or matching PII on `pdf.sciencedirectassets.com`
is accepted; bytes still pass the normal bounds, PDF extraction and admission checks.
Signed query parameters are stripped from recorded source URLs and diagnostics.
Other proprietary viewers/native downloads may still require manual PDF import.
HTML bytes are never admitted as a PDF or evidence of missing access.

TUI/Web show translated reasons, original codes, last-check time and approximate
time until the next eligible check (the serial queue can delay it). With the Profile log
sink enabled, the Host writes `literature_access` transitions to the existing
`events.jsonl`, including provider, canonical paper URL, from/to state and reason.
These are live local diagnostics, respect the log level, and do not enter
Telegram/webhook delivery, research facts or notification cursors. The handoff
queue is bounded at 256; a crash can lose unflushed events, and log failure or
overflow is reported in Host output. No browser redirect URL, cookie, document
text or import path is logged.

Final Agent failures remain visible in TUI/Web after run completion. A per-call
cost refusal displays `BUDGET LIMIT` and `error_max_budget_usd`; other final
failures display their code. Adjust frozen limits for a new run, not a retained one.

For browser tests without system Chrome, explicitly install Playwright Chromium
in the development environment and select its executable:

```bash
python -m playwright install chromium
export HYPOLOOM_TEST_CHROME="$(python -c 'from playwright.sync_api import sync_playwright; p = sync_playwright().start(); print(p.chromium.executable_path); p.stop()')"
HYPOLOOM_INSTITUTIONAL_BROWSER_TEST=1 python -m pytest tests/runtime/test_institutional_navigation.py tests/runtime/test_institutional_browser.py tests/runtime/test_literature_access_browser.py -q
```

Tests never install a browser automatically; they use disposable headless profiles
and synthetic pages, not institutional subscriptions.

## Coverage and limits

Recognized routes include common Nature, Springer, IEEE, ScienceDirect, ACM,
Wiley, SAGE, Oxford and Taylor & Francis article domains and DOI prefixes.
These routes use common PDF metadata and positive full-text structure; they
are not a claim of live subscription qualification for every publisher.
Use canonical article/DOI URLs without query parameters; for example, use
IEEE `/document/...` instead of `stamp.jsp?arnumber=...`.

PDF bytes travel through the browser, using its network/session, then Poppler
parses them on the Host. Cross-origin PDF restrictions, school WebVPN rewritten
domains, proprietary viewers, image-only PDFs and site changes can still fail.
Retain the evidence gap or import a PDF; there is no automated captcha or
access-control bypass. Missing standard Abstract/Conclusions headings no longer
discard readable PDFs; absent excerpts remain empty.

Limits per Host: 32 requests/cache entries, 64MiB cache, eight publisher tabs;
24MiB and 2000 pages per file. The queue/cache are disposable memory. Restart
reconstructs work from existing inputs/tool calls. Stopping discards queued and
late results; clearing all work or shutting down closes only Hypoloom-created
tabs, preserving the operator browser/profile.

The adapter uses [Playwright CDP](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)
and supports Chromium browsers. Tests use real Chromium with synthetic
publisher responses. A school's real entitlement and Windows-to-Linux tunnel
require an environment-specific check. See the [design](../design/institutional-literature-access.md)
and [qualification](../qualification.md).
