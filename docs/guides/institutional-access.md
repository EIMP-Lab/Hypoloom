# Institutional full-text access

English · [简体中文](institutional-access.zh-CN.md)

Public discovery and PDF retrieval remain first. Optional institutional access
uses a researcher's authenticated Chromium browser. Prefer a persistent server
browser so a laptop can disconnect after login. The TUI groups unresolved
papers by publisher without opening repeated prompts. Other research continues;
available bytes do not establish that an Agent read a paper.

## Prepare the connection during init

Choose the browser location explicitly with `--institutional-browser-mode` on
`bootstrap inspect/launch` or `institutional prepare`:

| Mode | Placement | Preparation |
| --- | --- | --- |
| `local` | Native Linux desktop running the project | Start/reuse the user's visible Chrome/Chromium through user systemd; no SSH or Docker required. |
| `existing` | External browser or client tunnel | Check existing loopback CDP; leave browser and tunnel ownership with their operator. |
| `server` | Server without a desktop | Use the repository Compose service and an already installed image, with loopback GUI 5800 and CDP 9222. |

```bash
python -m hypoloom institutional connect status --institutional-browser-mode local
python -m hypoloom institutional connect start --institutional-browser-mode local
python -m hypoloom institutional prepare --institutional-browser-mode local --provider IEEE --provider Springer
```

The default endpoint is `http://127.0.0.1:9222`. Local/existing can use another
`--institutional-browser-url`. Preview reports `institutional_connection`,
`institutional_connection_argv` and `institutional_preparation_argv` without
launching, connecting, creating a directory or creating research. An explicit mode
prepares the connection before `launch --start` creates a Host. Failure returns
`institutional_connection_failed`: retry, revise placement, or explicitly remove
institutional options to use public/OA sources only. No silent fallback is selected.
The old endpoint-only option remains compatible and does not manage startup.

Run local mode from the actual Linux desktop user's terminal with Chrome/Chromium,
user systemd/busctl and the project's optional Playwright dependency installed.
X11 uses DISPLAY/XAUTHORITY; Wayland checks the current user's session socket.
An SSH shell without a desktop cannot borrow another user's GUI permissions.
Confirm that the window is visible: successful CDP alone cannot establish this.
Missing dependencies, occupied ports and busy profiles return separate diagnostics;
the launcher never installs a browser or removes browser locks.

The default dedicated profile is `$XDG_DATA_HOME/hypoloom/Institutional-<executable>`,
or `~/.local/share/hypoloom/` when XDG is unset. Override it with an absolute
`--institutional-browser-profile`, and optionally select
`--institutional-browser-executable`. The directory must belong to the current user
with mode 0700; personal default profiles are rejected. Repeated startup reuses the
selected unit. Changing its port requires stopping the old connection first.
Ending a research Host does not close the browser.

```bash
# Repeat the same profile, executable and endpoint options used for start.
python -m hypoloom institutional connect stop --institutional-browser-mode local
python -m hypoloom institutional connect start --institutional-browser-mode server --repository /path/to/Hypoloom
```

Local stop asks Chromium to close normally and flush its session, then confirms
termination. Managed stop preserves profiles; existing mode never closes an external
browser. Readiness waits default to 30 seconds (`--institutional-connect-timeout`,
3–120); dependency, handshake and stop checks have separate finite limits. Server
mode validates the expanded Compose loopback ports, uses no implicit image pull, and
requires the selected checkout even when running an installed wheel. Docker/proxy
failures remain explicit and do not switch browser placement.

### Windows connector

Copy the standalone `hypoloom/connection_assets/institutional-connect.ps1` to a stable
Windows path. It requires PowerShell 7.2+, not Python or the project runtime. First
use normal SSH to establish host trust and configure your existing key/ssh-agent;
the helper uses BatchMode and stores no password. The server needs curl. A target is
`user@host` or an SSH config alias, never an account password.

```powershell
$researchServer = 'research-server' # Your SSH target or config alias
$researchConnector = Join-Path $env:LOCALAPPDATA 'Hypoloom\institutional-connect.ps1'
New-Item -ItemType Directory -Force (Split-Path $researchConnector) | Out-Null
scp "${researchServer}:/path/to/Hypoloom/hypoloom/connection_assets/institutional-connect.ps1" $researchConnector
& $researchConnector -Server $researchServer -Action Run
```

The helper discovers Edge before Chrome, using the persistent
`%LOCALAPPDATA%\Hypoloom\Institutional-msedge` or `Institutional-chrome` profile.
Override with `-BrowserExecutable` and `-BrowserProfile`; permissions must restrict
the profile to its user, SYSTEM and administrators. Both ports default to 9222;
`-LocalPort` and `-RemotePort` can override them. Readiness checks the local owning
process, loopback listener and the same CDP browser identity through the server.
Server init additionally performs a real CDP handshake.

Keep Run open; Ctrl+C cleans up its own SSH child. An existing healthy tunnel is
monitored without being terminated. Forwarding failures retry at most three times,
30 seconds apart; authentication/host-trust preflight failure returns immediately.
An optional current-user interactive logon task requires no stored Windows password:

```powershell
& $researchConnector -Server $researchServer -Action Install
& $researchConnector -Server $researchServer -Action Status
& $researchConnector -Server $researchServer -Action Stop
& $researchConnector -Server $researchServer -Action Uninstall
```

Install applies at next user logon; use Run for the current session. Repeat the same
script path and options for task management. A conflicting task is never overwritten;
duplicate task instances are suppressed, with up to three task restarts one minute
apart after failure. Stop preserves the profile, although Task Scheduler may terminate
children it launched. Foreground Run is stopped with its own Ctrl+C. A Linux remote
client can start local mode and keep the existing `ssh -N -o ExitOnForwardFailure=yes
-R 127.0.0.1:9222:127.0.0.1:9222 research-server` running; this batch adds no Linux
tunnel service installer.

Server init cannot wake a client PC or complete SSO/MFA. Browser profiles retain
sessions, while schools/publishers control expiry and access rights. Isolated
headless lifecycle tests do not prove visible desktop, Windows logon-task or
container deployment behavior; see [qualification](../qualification.md#institutional-connection-startup-2026-10-02).

## Prepare platforms before research

After connecting the dedicated browser, run these commands in the project's server environment:

```bash
python -m hypoloom institutional prepare --list
python -m hypoloom institutional prepare \
  --institutional-browser-url http://127.0.0.1:9222 \
  --provider IEEE --provider Springer
```

Complete SSO/MFA only in the browser and follow the terminal prompt. Supply a sample
DOI/canonical article URL interactively or using repeatable `--sample`. With
`--non-interactive --sample <DOI-or-URL>`, check the existing session without waiting
for terminal input. `--list` does not connect. Prepare at most eight platforms per
batch; close the command and start another batch as needed. No research Host/run,
Attempt, Profile, model call or source archive is created. Bootstrap review provides
`institutional_preparation_argv`; ordinary preview never opens the browser.

During a run, use the TUI **i** panel or an authorized Web literature panel. Both show
the complete catalog even without pending papers. Open login, verify a sample, or
check/retry. Avoid running a second standalone manager against the same browser.
A timestamped verified sample proves that paper was readable at that time; its bytes
are released, not archived as research evidence. Reconnecting the same browser can
reuse a valid session, but institutional/publisher expiry can require another login.

Public documents and the configured exact-DOI OA locator run before institutional
access. Background requests that need login, verification or access intervention
finish unavailable so research can continue. Automatic acquisition has a **120-second**
deadline from enqueue, including queue time and retries. Explicit login/check opens a
**600-second** platform window. New papers never extend it. Timeouts need an operator
retry; denial of one paper never disables the entire platform automatically.

| Control | Scope |
| --- | --- |
| Skip / restore paper | Across experts in this run, keyed by known DOI or canonical link; existing admitted originals remain available. |
| `public_only` platform | Suppresses automatic institutional browsing; public/OA retrieval and validated manual PDF import remain available. |
| `auto` platform | Permits existing-session access; explicitly open login/check if authentication is needed. |
| Import PDF | Requires a selected paper and an absolute Host-side path; normal identity/source validation remains mandatory. |

Skip/restore and platform choices use the existing run command journal and survive
same-version Host restart. Manual login windows are not replayed. These controls apply
to new lifecycle v11 runs; retained frozen runs are not migrated. Repeat reviewed
launch arguments for future runs without adding preferences to research Profiles:

```text
--institutional-provider-mode IEEE=auto
--institutional-provider-mode Springer=public_only
--institutional-acquisition-seconds 120
--institutional-manual-seconds 600
```

Modes may repeat for different platforms; conflicting values fail validation. Deadlines
must be finite, respectively 30–600 and 60–1800 seconds. Public/OA work retains existing
HTTP, shared PDF-worker and time limits. Dynamic document tools reuse the selected
Semantic Scholar or Unpaywall DOI locator without replaying a whole literature batch.
OA copies retain their actual source and version limitation; they are not automatically
claimed to be the publisher's version of record. A replacement paper needs its own citation.

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

Start a visible browser on the Windows laptop, using a dedicated profile. This
finds common Chrome installations, falling back to Edge:

```powershell
$researchBrowser = @(
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $researchBrowser) { throw 'Chrome/Edge not found; set the actual executable path.' }
$researchProfile = Join-Path $env:LOCALAPPDATA ("Hypoloom\Institutional-" + [IO.Path]::GetFileNameWithoutExtension($researchBrowser))
& $researchBrowser '--remote-debugging-address=127.0.0.1' '--remote-debugging-port=9222' "--user-data-dir=$researchProfile" 'about:blank'
Start-Sleep -Seconds 3
(Invoke-RestMethod 'http://127.0.0.1:9222/json/version').Browser
```

The last line should print the browser version before you start the SSH tunnel.
For a custom installation, set `$researchBrowser` to its actual executable path.
Sign in yourself in this browser.
[Chrome requires a non-default data directory for remote debugging](https://developer.chrome.com/blog/remote-debugging-port).
[Edge supports the same CDP interface](https://learn.microsoft.com/en-us/microsoft-edge/devtools/protocol/).
The browser keeps its own session; Hypoloom does not export cookies or storage.

For a Linux server with a Windows browser, leave this SSH tunnel running on the
laptop (replace `user@SERVER` with your existing SSH destination or alias):

```powershell
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -R 127.0.0.1:9222:127.0.0.1:9222 user@SERVER
```

The destination is the server address/alias, not a password. If SSH asks for
authentication, enter it in the SSH terminal. Keep that terminal open.

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

A SAML entity/metadata URL such as `https://institution.example/idp/shibboleth`
may return XML rather than an interactive login page. Do not use it as the
library entry. Omit `--institutional-library-url` and choose **Institutional
Sign In** on the publisher page; select your institution there to start SSO.

## During research

Press `i` in the TUI, select a publisher and choose the login button. Complete
school selection, SSO or MFA in the browser. If the library leaves you on its
home page, navigate to the listed article in that same tab. The Host checks
waiting tabs approximately every 30 seconds within an explicit manual window, without restarting login. Retry
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
logon. Use the actual values of `$researchBrowser` and `$researchProfile` from
the successful manual setup, with the same port 9222 arguments. Select
"Run only when user is logged on" so SSO
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
at 30, 60, 120, then 300 seconds within the remaining deadline; expiry ends the request. A 429 does not trigger an immediate
retry. Success, leaving the transient state, or operator retry resets the backoff.
Auth checks run only in an explicit manual window without restarting the login flow;
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
ScienceDirect and IEEE PDF controls may appear after the DOM is complete. An article without
PDF/full text or an explicit access outcome is inspected for up to about eight
seconds before returning `no_full_text`. If the bridge's own JS races the direct
hop (`ERR_ABORTED`), follow the resulting navigation within that bound instead of
immediately declaring a network failure; normal origin/DOI checks still apply.
If a PDF endpoint returns HTML or other non-PDF bytes, the queue reports retryable
`pdf_response_not_pdf`, not terminal missing full text. After supported viewer handling,
a non-PDF response combined with an explicit article paywall instead returns
`access_required`, ending background waiting without claiming denied entitlement.
HTTP server errors and ambiguous non-PDF responses remain retryable. For an identified ScienceDirect
article's `/pdfft` link, the adapter navigates its owned tab to execute interstitial
scripts, waiting up to 25 seconds after navigation. Login/verification retains that
tab for the operator; subsequent checks observe it without restarting the navigation.
Only the same article's PDF endpoint or matching PII on `pdf.sciencedirectassets.com`
is accepted; bytes still pass the normal bounds, PDF extraction and admission checks.
Signed query parameters are stripped from recorded source URLs and diagnostics.
IEEE's HTML PDF viewer is also followed when its article number matches the
identified article. Its iframe must resolve to the same IEEE article's HTTPS
PDF endpoint; the downloaded PDF still needs a matching content identity.
Article-page and DOI entries, plus reuse of a valid institutional session, have
been [verified with one real IEEE paper](../qualification.md#real-institutional-subscription-and-ieee-viewer-repair-2026-10-01).
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

Publisher preparation, durable skip/preferences and bounded authentication waits
follow the [framework design](../design/institutional-access-preparation.md).

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
publisher responses. Actual IEEE and Springer subscription samples and Windows-to-Linux tunnel
have been verified; other institutions/publishers still require an
environment-specific check. See the [design](../design/institutional-literature-access.md)
and [qualification](../qualification.md).

## Retrieval rejection and document identity

A candidate rejected by public-retrieval validation is not automatically sent to
the institutional browser. The Host derives this restriction from the exact
packet's existing download trace, including retries and cached browser results;
unrelated valid sources retain their normal route. Institution login remains
a separate operator action and does not override a rejected paper source.

PDF identity comes from the downloaded document's first physical page. A landing
page DOI is an expectation to check, not the downloaded PDF's identity. A conflicting
DOI is rejected even when the expected DOI appears in later references. If no DOI
is readable, the existing title check requires the supplied title on the first
page; insufficient identity remains unconfirmed. Scanned or unusual layouts may
therefore require further verification rather than being silently accepted.
