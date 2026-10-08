# Institutional full-text access

English · [简体中文](institutional-access.zh-CN.md)

Public discovery and PDF retrieval remain first. Optional institutional access
uses a researcher's authenticated Chromium browser. Managed connections retire
with each run, while dedicated profiles retain still-valid sessions. The TUI shows
read-only publisher policies and current acquisition observations. Other research continues;
available bytes do not establish that an Agent read a paper.

## Publisher challenges and DOI fallback

Selecting publishers during init permits their institutional routes; it does not
search each publisher or download every missing candidate. The Agent selects a
needed paper's URL with `web_fetch(mode="document")`. A batch `PDF_UNAVAILABLE`
result can still be followed by independent public discovery and enabled browser
access. A failed batch DOI locator no longer blocks untried public sources.

New source records retain `acquisition_source`: `public_http`, `metadata`,
`fallback` and `arxiv` describe public retrieval; `browser`, `manual_import` and
`elsevier_api` describe the other acquisition routes. Browser access does not by
itself prove institutional subscription, and public HTTP may benefit from network
entitlements. Older sources without this field remain unknown. Available full
text is not evidence of reading or understanding.

Public PDF requests can receive a Cloudflare challenge even when the article
landing page works. Hypoloom detects the explicit challenge response before
reading its body, records `PDF_CHALLENGE` / `CHALLENGE` in the existing download
trace, and tries the remaining allowed public sources within the same deadline.
An ordinary 403 stays a separate HTTP failure. No CAPTCHA is solved automatically,
and failed acquisition does not become proof that a paper was read.

Semantic Scholar uses exact DOI lookup to find a public copy. Init preparation
reuses its locator across samples; the Host shares one with its institutional
browser path. Requests are serialized at at most one per second per locator.
A 429 activates at least 60 seconds of cooldown, respecting a longer `Retry-After`;
other public sources can proceed without waiting through that cooldown. Independent
Hosts still share any account quota. Optional private key configuration is in
[Personal configuration](../../config/README.md). A key is not a subscription and
does not guarantee that Semantic Scholar or the publisher will serve full text.

For unattended runs, unavailable full text remains an explicit evidence gap;
the framework continues with other admissible sources. These changes take effect
in new Hosts and new preparation sessions; they do not change retained runs or
require reinstalling the Windows helper.

Direct HTTP requests now enforce one deadline across DNS, connection, TLS,
headers and body; redirects and retries use the remaining caller budget. At
expiry or cancellation the network worker is terminated and reaped, with up to
one additional second reserved for cleanup. Slow continuous responses cannot
keep refreshing an idle timeout. Partial PDFs are discarded; other successful
papers remain available. This does not require a proxy or an extra init step.
Search-provider outages use [automatic literature recovery](provider-recovery.md#literature-service-recovery);
they are distinct from a single unavailable publisher PDF.

## Search backup and optional server proxy

New online Hosts automatically try anonymous Crossref keyword discovery after
transient OpenAlex failures. Both share the search deadline. Existing metadata,
language, source-type and three-period coverage checks still apply; sparse backup
results can require query revision. OpenAlex authentication failures remain visible.
This backs up the initial literature packet search; it does not promise identical
ranking, publisher search, full text or a replacement for all web-search tools.
The observer identifies Crossref use without claiming OpenAlex recovered. To opt
out, pass `--literature-search-fallback none` at initialization.
Crossref's [public API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/)
requires no extra key.

Routing stays direct unless explicitly configured. If a tested server-side HTTP
CONNECT proxy improves a particular endpoint, add these fields to the existing
private `methodology.yaml` literature section (replace the example port):

```yaml
literature:
  # Keep the section's existing fields and credentials.
  proxy_domains: [ieeexplore.ieee.org]
  proxy_url: "http://127.0.0.1:7890"
```

Keep that file at mode `0600`. Legacy dotenv users can instead put
`HYPOLOOM_LITERATURE_PROXY` in their existing private credentials file and pass
`--literature-proxy-domain ieeexplore.ieee.org`; repeat for each exact hostname.
Domains do not include subdomains automatically. Init/retry/Host commands carry
the domain list and credential-file path, never the endpoint. A changed endpoint
is reread on the next selected request; changing domains requires a new run.

Selected domains use only this proxy, including metadata and public PDF requests;
each redirect is selected separately. Other domains stay direct. `HTTP_PROXY` and
`HTTPS_PROXY` are ignored. A failed configured proxy does not silently switch
routes, solve CAPTCHA, change nodes or establish subscription access. Browser
traffic on Windows is still governed by that machine. CONNECT uses an approved
destination IP and normal hostname TLS verification, so the proxy's domain rules
may not select the intended node: test its exit configuration separately. All
existing time, size, cancellation and private-address restrictions still apply.

## Elsevier API

ScienceDirect can use the official Article Retrieval API independently of the
Windows browser. An API key is separate from website SSO. New keys normally use
the requesting server's institutional IP; off-campus access may need an Insttoken
issued by Elsevier. A key alone does not prove full-text entitlement.
[Official authentication](https://dev.elsevier.com/tecdoc_api_authentication.html)

Edit the private `profiles/local/YOUR_PROFILE/methodology.yaml` locally. Preserve
existing fields and add `literature.elsevier_api_key`; optionally add
`literature.elsevier_insttoken` only when issued. Keep the regular, non-symlink file
mode 0600. Do not send secrets to chat, CC, argv or public templates; replace a key
already shared in chat. No second credentials file is needed.

Run this read-only sample check on the server, replacing the Profile and DOI:

```bash
./.conda/envs/hypoloom/bin/python -m hypoloom.elsevier \
  --credentials-file /data/Gray/Project/Hypoloom/profiles/local/YOUR_PROFILE/methodology.yaml \
  --url 'https://doi.org/REPLACE_WITH_YOUR_DOI'
```

It prints status/reason, a sample URL and page count, never keys or document bytes;
it creates or changes no research run. `available` proves only this document's
current access. `elsevier_authentication_failed`, `elsevier_access_denied` and
`elsevier_rate_limited` distinguish authentication, authorization and quota failures.
`identity_unconfirmed` / `elsevier_full_text_unconfirmed` mean source validation
did not succeed. For 403, check the server's IP, institutional API access and
Insttoken; do not copy browser cookies or repeat browser init as a remedy.

Repeat init's read-only inspection to review the configuration. New Hosts use
the key; init still asks you to confirm the browser preparation list. An API key
does not replace that answer or remove an explicitly selected ScienceDirect.
Legacy direct-launch previews without an explicit list omit ScienceDirect from
the default preparation set when this key is configured.
Preflight reports `full_text_access: not_checked` and consumes no API quota.
Existing runs retain their frozen version; no migration occurs.

Existing public PDF reads retain their behavior. For a selected ScienceDirect
article/Elsevier DOI, the API replaces the anonymous publisher request and automatic
browser fallback. DOI selections start with PDF; PII-only links use XML to verify
the returned article identity. PDF HTTP 403/406 permits one FULL XML representation fallback
within the same deadline/request budget; 401/429 are not retried. Metadata, abstracts,
challenges and identity mismatches never become full text. XML produces extracted
text, labeled with missing PDF pagination and possible figure/formula formatting
loss. Failure preserves exact-DOI OA fallback and manual PDF import without opening
another CAPTCHA page. `ScienceDirect=public_only` also disables the authenticated
API; retain `auto` to use it. Other platforms can still use the Windows browser.
[Official retrieval API](https://dev.elsevier.com/documentation/FullTextRetrievalAPI.wadl)
Optional Web PDF import still requires an enabled institutional panel (browser mode
other than `off`). Failed API papers appear there for manual import without
scheduling a browser fetch. The API itself also works with browser mode `off`.

## Prepare the connection during init

Select `--institutional-browser-mode` for new `bootstrap inspect/launch`, `serve`
or `institutional prepare` operations:

| Mode | Browser location | Lifetime |
| --- | --- | --- |
| `off` | No institutional browser | No institutional connection or panel operations; public/OA retrieval remains available. |
| `client` | Windows Edge/Chrome | One explicit init/run binding; preparation and that run share the helper, which exits at its lifetime boundary. |
| `local` | Native Linux desktop | A visible persistent Chrome/Chromium context uses a process pipe, without a CDP TCP port. |
| `server` | Headless Linux server | A separate container per run uses an installed image and private CDP/GUI relays. |
| `existing` | Explicit external loopback CDP | A protected run relay connects to the supplied endpoint; the external browser remains operator-owned. |

During init, an omitted choice appears as `institutional_browser_mode` in inspect's
`missing` list; the conversation asks using the returned choices. Supplied answers
are reused. Off is explicit, not an inferred default. See the
[initialization guide](initialization.md#aggregate-inspection-and-one-launch).
Off provides no preparation command and rejects conflicting institutional options;
choose an enabled mode to prepare browser logins. To retain institutional-panel
PDF import with public-only retrieval, keep an enabled mode and select `public_only`
for the relevant platforms.

New managed listeners bind port **0** and use the OS-assigned port, just like the
Web observer. `--institutional-port N` explicitly fixes the run relay port for
client/server/existing; a conflict makes only that connection unavailable without
adopting the existing listener or stopping other runs. Once the port is freed,
the connection can retry within the original acquisition deadline.
Only existing mode accepts `--institutional-browser-url` and `--institutional-browser-ui-url`.
The endpoint-only form without a mode keeps its previous unmanaged behavior.

Preview has no effects. `institutional_connection` shows policy,
`institutional_connection_argv` reads active endpoints, and
`institutional_preparation_argv` starts a separate preparation operation.
Init's command uses `--agent-session`: it first verifies the real browser connection
within the connection timeout (30 seconds by default), then opens platforms.
A missing connection enters one connection-wait window with platforms still
`not_checked`, instead of spending a separate 120-second wait on each. The program
automatically rechecks after five seconds between completed checks, through the same
browser worker. Each check is capped by the remaining window and connection timeout;
success opens the selected platform without another user reply. `waiting_for_client` does not prove
that a helper is absent or an institution lacks access. Output is flushed immediately.
The agent queries the temporary private control socket and returns your answers
from the init conversation; it never feeds a background process's stdin. Login,
verification codes and MFA stay in the dedicated browser. Answer waiting uses the
existing finite manual timeout. Explicit finish releases the temporary connection;
completed preparation retains it only for the bounded launch handoff described below.
This timeout also starts when entering connection waiting. Platform-answer waiting
has its own deadline. Status reads and `继续连接` never extend the connection window;
that reply only expedites the next check. The process reports remaining seconds.
An expired connection wait retains the latest connection result and remaining command.
Standalone terminal preparation remains supported; EOF returns `input_required`
and closes resources instead of raising or pretending login succeeded.

If the opened entry contains a human-verification challenge, fixed instructions
report `verification_required` as an observation made when opening the page. This
does not establish that verification or institutional sign-in succeeded. Complete
the challenge manually in the dedicated browser, or choose **跳过此平台** if it keeps
repeating. The program does not solve challenges, refresh or reopen the page, or
extend the existing answer deadline. A normal browser's verification and sign-in
do not transfer to the separate profile. Reinstalling the client cannot guarantee
recovery from a website challenge; supported provider APIs or manual PDF import
remain alternatives.

For manual diagnosis, use the first record's `status_argv` and `answer_argv`:

```bash
python -m hypoloom institutional preparation status --state-root /path/to/temporary-preparation
python -m hypoloom institutional preparation watch --state-root /path/to/temporary-preparation
python -m hypoloom institutional preparation complete --state-root /path/to/temporary-preparation --provider IEEE
python -m hypoloom institutional preparation verify --state-root /path/to/temporary-preparation --provider IEEE --sample 10.1109/example
python -m hypoloom institutional preparation retry_connection --state-root /path/to/temporary-preparation
python -m hypoloom institutional preparation finish --state-root /path/to/temporary-preparation
```

`complete` checks the samples displayed by the program; without a fixed or supplied
sample it remains unverified. `skip` and `public_only` also take
the current provider. These commands address only the live temporary preparation,
never a research run. For scoped Windows init, when status reports `state: awaiting_launch`, read its
`summary` immediately: preparation is complete even while the background process
remains alive. The same control root retains the connection for at most 600 seconds
while you review launch choices. **Start now** still requires your explicit choice.
For **Prepare only** or cancellation, execute the returned `finish_argv` to release
this authorization. Expiry also releases it; it never starts research. An explicit
finish/skip during preparation exits directly; read that background result instead.
Current Windows init uses `init_session_argv` to automate the preparation-to-launch
review. Its `launch_review` is generated before the 600-second choice clock starts:
CC relays it and submits the exact **Start now / Prepare only** reply. It need not
merge policies, reread notification configuration or run a separate launch command.
Only an accepted Start now enters `launching`; actual managed launch success appears
in `bootstrap_result`. Expired, early or duplicate Start now replies are rejected.
Preparation retry keeps the same owner and completed publishers. Standalone
`institutional prepare` retains its existing separate-launch behavior.

The final `preparation_lifecycle` event separately records `run_handoff`, `launch_failed`, `launch_uncertain`,
`launch_review_expired` or `operator_finished`. A publisher summary describes its
samples; it does not prove that the preparation controller or Windows helper is
still running. Preserve both observations when diagnosing an exit.

`identity_unconfirmed` concerns the **requested paper**, not the user's institutional
account. It does not prove a PDF was downloaded. CC relays the program's fixed result
explanations before the final JSON. `article_identity_unconfirmed` locates failure
at the landing-page identity check; `pdf_target_unconfirmed` at the download target;
`document_validation_failed` at validation of obtained content. Older results without
these reason codes cannot establish the stage. Repeated login/retry is not a proven
repair, and public/OA fallback remains an attempt rather than guaranteed retrieval.

During normal init, a sample that encounters verification or sign-in produces a
second user prompt for that **sample page**. Handle it in the dedicated browser and
reply 完成, skip, choose public-only or finish. Completion resumes the same pending
acquisition and retained tab. It does not reopen the institution entry or restart
the acquisition deadline (120 seconds by default); the prompt reports time remaining.
The program never operates CAPTCHA controls. Standalone noninteractive checks
continue to report the observed status without waiting for a person.

For fresh-init acceptance testing, start a new CC session and `/autoresearch init`
each round, stopping at launch review. Recovery of the old batch is not required.
Reuse the downloaded script and private login profile, but execute the new init
entry once on the Windows PC chosen for this run. A previous helper is not authorized
for the new run. First-install testing needs a separate test account and must be
recorded separately.

Normal init runs the generated `instructions_argv` and displays its output verbatim:
the Windows file location, fixed Setup entry, input values, remaining time and replies.
Connection replies are `继续连接` (retry connection) and `跳过准备` (skip preparation).
The program detects readiness automatically. Immediately after displaying connection
instructions, CC starts one generated `watch_argv` in the background instead of
waiting for a separate "connected" reply. It includes `--wait-seconds 3600`, exits
early at a user-action stage, `awaiting_launch` or batch end, and prints current instructions. Its task
completion wakes CC to show the current platform choices. After a platform reply,
CC arms one observer for the next stage; never run overlapping observers or custom
sleep/poll loops. This read-only limit does not extend connection, answer or sample
deadlines. A direct `watch` without the option still defaults to 30 seconds.
Do not wrap the generated command in a shorter timeout; empty interim output is
not evidence of unchanged state. If you already authenticated while checks advanced,
reply `完成` at the platform stage without logging in again. Follow the current
choices if a connection reply is now stale; do not infer platform completion.
**结束准备 / 跳过准备 cancels the batch**; it does not mean "connected" or restart
preparation automatically. A fresh batch does not need cancellation before testing.
Read status to refresh progress. A recovered connection query does not itself prove
browser readiness. The helper queries only its supplied preparation/run identity;
it does not wait indefinitely for any future run in the server directory.
Platform replies are `完成`, `验证样本`, `跳过此平台`, `仅公开`, `结束准备`
(complete, verify a separately supplied sample, skip this platform, public only, finish).
The agent passes the exact choice to `reply_argv --text`; the process validates
the current stage and provider. Background notifications are never answers.
At `awaiting_launch`, an offered `重新准备` uses the summary's generated
`remaining_preparation_argv` unchanged. It invokes `preparation retry` on the same
control root, preserving launch identity, completed results, choices and supplied
samples; successful platforms are not replayed. Do not create another preparation
root or extend authorization by reconstructing a command. Once the controller has
expired or been finished, follow the current generated instructions; an old retry
vector cannot revive it.

If a selected platform times out, init offers `重新准备` (retry the remaining platforms)
or `接受未验证状态，继续检查启动配置` (accept unverified access and continue to launch
review). Optional preparation failures do not block research, but do not mean success.
Sample acquisition preserves the same safe CDP/page failure reasons as platform
opening. `acquisition_timeout` does not establish readability, failed login or a
closed browser; only `sample_verified` establishes that sample's readability.
An empty retry vector is not proof of success. Follow the returned failure policy
and review the retained states; never invent a new batch to work around an expired
controller. Keep the current dedicated browser available while its helper is active.
Intermediate `browser_unavailable` / `fetch_unavailable` events include sanitized
reason codes; a final `acquisition_timeout` alone cannot identify the underlying fault.
Connection checks also report fixed `cdp_timeout`, `cdp_connection_refused`,
`cdp_connection_closed`, `cdp_authorization_failed`, `cdp_endpoint_unavailable`,
`cdp_context_unavailable`, `browser_driver_unavailable` or `cdp_connection_failed`
codes. They describe the control connection, not publisher login or subscription;
raw browser errors and private URLs are never printed.
Platform opening preserves the same connection reason codes. `browser_target_closed`
means a page/context/browser target closed, `browser_operation_timeout` means a page
operation timed out, and `browser_operation_failed` is an unclassified page operation
failure. Generic `browser_unavailable` does not prove that Edge or SSH exited. The
existing bounded queue retries; an open window alone does not prove a healthy control
connection.


Resources are allocated inside the Host once the run exists. A missing browser
can remain unavailable within the existing bounded acquisition/fallback policy;
it does not prevent other research. No mode is silently changed.

Completion, failure, cancellation and Host shutdown release owned listeners,
forwards and browser/container resources. Pausing a participant, waiting for a human
or closing TUI/Web does not terminate the run. Multiple runs have independent
endpoints. Same-version recovery creates fresh ephemeral addresses from the same
pinned policy; terminal runs get none. Tokens and actual ports are not saved into
frozen run configuration. Existing research runs are not migrated.

Client socket directories and containers use an EOF-owned user-systemd service,
so Host SIGKILL also triggers cleanup; native browser pipes close with their owner.
SSH watches poll the private Host control socket and verify CDP browser identity.
Normal online retirement is observed within a polling interval; network partitions
use SSH keepalive timeouts (15 seconds, three missed responses), not an instant
cleanup guarantee. Local work already in flight finishes within its existing bounded
browser deadline before graceful browser close. Docker cleanup requires a responsive
daemon and reports service failure rather than claiming success.

Profiles stay browser-owned and are never deleted by run cleanup. Local/server
runs cannot open the same profile concurrently; choose distinct dedicated directories
for parallel sessions. Windows helpers are bound to one explicit run and close their own dedicated
browser and SSH children on retirement, preserving the login profile. A live
browser owned by an older helper is not adopted for another run.

```bash
python -m hypoloom institutional prepare --institutional-browser-mode local --provider IEEE --provider "Springer Nature"
# For client mode, use init's generated preparation and Windows entry commands.
python -m hypoloom institutional connections --state-root /path/to/selected-run --run-id RUN-ID --request-id CREATE-REQUEST-ID
```

Preparation creates a temporary private control directory under `<repository>/state`
and retains its transport only through the bounded launch handoff. The generated
Windows entry carries the exact final `-StateRoot`, `-RunId`, `-RequestId` and,
during preparation, `-PreparationRoot`; the parent runs directory is not connection
authority. Preparation creates no research run or archive. Use `--repository` when
preparing from another working directory.

Local mode requires Chrome/Chromium and Playwright in the actual Linux desktop
user's session. Init forwards only the selected X11/Wayland session variables to
the managed Host. Confirm the visible window separately. The default dedicated
profile is `$XDG_DATA_HOME/hypoloom/Institutional-<executable>` (otherwise under
`~/.local/share`). Override with absolute `--institutional-browser-profile` and
`--institutional-browser-executable`. Profiles must be owned by the user with mode
0700; personal default profiles and busy browser directories are refused.

Client/server transport guardians require Linux user systemd. Server mode reads
only the image and supported environment from `--institutional-compose-file`
(default `<repository>/config/institutional-browser.compose.yaml`), not its static
published ports or volume. The profile is a private per-user directory. It never
pulls an image, installs dependencies, or changes Docker proxy settings. The image
must provide `nc`. Container services listen internally on loopback; no Docker port
is published. Obtain the temporary GUI URL and Basic credentials locally with:

```bash
python -m hypoloom institutional connections --state-root /path/to/run --run-id RUN-ID --request-id CREATE-REQUEST-ID --include-ui-credentials
```

Forward that GUI port with SSH `-L`, then enter its temporary GUI credentials.
School credentials are entered only inside the browser. The read-only Web panel
does not reveal GUI passwords. `--institutional-connect-timeout` is 30 seconds by
default (3–120). Browser connectivity does not establish subscription rights.

The old explicit `institutional connect status|start|stop` commands remain manual,
operator-owned diagnostics for local/existing/server. They are not invoked by new
run startup and do not acquire the run lifetime described above.

### One Windows terminal entry for init

**For each CC init**, execute the program-generated Windows entry once on the PC
that should supply its institutional browser. The entry binds the helper to the
reviewed `state_root`, `run_id` and create request ID. Another PC's old helper cannot
connect automatically. Setup does not install a logon trigger or discover future runs.

On first use or after changing Windows PC/account, complete the
[noninteractive SSH check](#first-setup-or-a-new-windows-pcaccount) below first.
Downloading the script with a password does not establish background authentication.

1. Keep the server's `/autoresearch init` open and select Windows client mode.
   Init shows `script_source`, `setup_inputs` and the exact `powershell` entry.
2. On Windows, download the original `institutional-connect.ps1` from that server
   path using SFTP/SCP or your file-transfer tool. Download again after an upgrade;
   do not save assistant-written source as the script. Windows needs no Hypoloom
   checkout or Python installation.
3. Open PowerShell 7.2+ in the download directory and run the **exact generated
   entry**, including its `-StateRoot`, `-RunId`, `-RequestId` and, when supplied,
   `-PreparationRoot`. If using the interactive entry below, fill its scope prompts
   with the values displayed by this init; do not invent new IDs or reuse old ones:

   ```powershell
   pwsh -NoProfile -File .\institutional-connect.ps1 -Action Setup
   ```

4. Supply your SSH target and any prompted server paths. Setup checks SSH/remote
   Python, installs the original file at
   `%LOCALAPPDATA%\Hypoloom\institutional-connect.ps1` and starts this connection.
   Its one-shot scheduled task runs hidden, with no logon trigger or automatic task
   restart. After Setup/Connect returns, its PowerShell window may close.
5. Return to the same CC init. The program checks actual browser readiness, opens
   the selected pages and validates samples after your fixed replies. At
   `awaiting_launch`, review `summary` without waiting for the background command to
   exit. Start now hands the same helper to that exact run; Prepare only/cancel
   executes `finish_argv`. The handoff expires after at most 600 seconds.

Without a graphical transfer tool, open SFTP in your Windows download directory:

```powershell
sftp research-server
```

At `sftp>`, download the `script_source` displayed by init and exit. Replace this
example path; surround paths containing spaces with double quotes:

```text
get /path/to/Hypoloom/hypoloom/connection_assets/institutional-connect.ps1
bye
```

File transfer preserves the original bytes. For old garbled or mistyped setup,
obtain the original file again instead of patching chat-generated source. Press
Ctrl+C first if an old command is stuck at `>>`. The SSH target is not a password;
Windows loopback refers to Windows. Verify `ssh -o BatchMode=yes <target> exit`
returns 0 before Setup. Task startup alone does not prove browser readiness.

**Upgrading an existing Windows client:** stop any old foreground Run with Ctrl+C.
New Setup checks ownership before removing the matching old logon/discovery task
and creating this one-shot task. It will not overwrite another account's or
program's task. If it reports `scheduled_task_ownership_mismatch`, inspect the
reported task/user rather than deleting tasks by wildcard. Windows may display a
short principal name; ownership is checked by SID. Old manually created fixed-port
or unrelated tasks remain operator-owned: stop those explicitly. Close the old
Hypoloom dedicated browser before this new connection; keep its login profile and
ordinary browser windows. The new helper must not adopt an old browser process.
Replacing the script does not update an already running helper or frozen Host.

If you switch to Windows B, retire the historical resident helper **on Windows A**;
upgrading the server cannot remove A's local task. First update the server checkout,
then run this whole block in A's PowerShell 7.2+. Enter the actual SSH target used
by the old task, including the same alias spelling, and the repository path shown
by init. No example server/path is prefilled. The block downloads the original
updated script before replacing the old installed file; download failure stops
without replacing it. No run scope is passed to `Uninstall`, so only the matching
ownership-validated legacy task is removed; new scoped tasks are left alone.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    if (-not $IsWindows -or $PSVersionTable.PSVersion -lt [version]'7.2') {
        throw 'Run this block in Windows PowerShell 7.2+ (pwsh).'
    }
    $researchServer = (Read-Host 'Actual SSH target used by the OLD helper (exact user@host or alias)').Trim()
    if ($researchServer -notmatch '^[A-Za-z0-9_][A-Za-z0-9_.@-]*$') {
        throw 'Enter the actual SSH target, not a password or an ssh command.'
    }
    $researchRepository = (Read-Host 'Absolute repository path on the UPDATED server, as shown by init').Trim().TrimEnd('/')
    if (-not $researchRepository.StartsWith('/') -or $researchRepository -match '[\r\n\x00]') {
        throw 'Enter the actual absolute Linux repository path.'
    }
    $researchConnector = Join-Path $env:LOCALAPPDATA 'Hypoloom\institutional-connect.ps1'
    $researchDirectory = Split-Path $researchConnector
    New-Item -ItemType Directory -Force $researchDirectory | Out-Null
    $researchTemporary = Join-Path $researchDirectory ([IO.Path]::GetRandomFileName())
    try {
        & scp.exe -o BatchMode=yes -o ConnectTimeout=10 "${researchServer}:$researchRepository/hypoloom/connection_assets/institutional-connect.ps1" $researchTemporary
        if ($LASTEXITCODE -ne 0) { throw 'Download failed; installed script unchanged. Keep the SSH error above.' }
        if ((Get-Item -LiteralPath $researchTemporary).Length -eq 0) { throw 'Empty download; installed script unchanged.' }
        Move-Item -LiteralPath $researchTemporary -Destination $researchConnector -Force
    } finally {
        if (Test-Path -LiteralPath $researchTemporary) { Remove-Item -LiteralPath $researchTemporary }
    }
    & pwsh -NoProfile -File $researchConnector -Action Uninstall -Server $researchServer -Repository $researchRepository
    if ($LASTEXITCODE -ne 0) { throw 'Cleanup failed; keep the error above. Do not delete tasks by wildcard.' }
    $researchSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $researchTaskName = "Hypoloom-Institutional-$researchServer-$researchSid"
    $researchRemaining = @(Get-ScheduledTask -ErrorAction Stop | Where-Object { $_.TaskName -ceq $researchTaskName })
    if ($researchRemaining.Count) { throw "The exact legacy task still exists; cleanup is not complete: $researchTaskName" }
    Write-Host "Verified absent legacy task: $researchTaskName"
}
```

Success confirms absence of the **printed exact task name**, not all tasks on A.
Close the old dedicated browser normally and retain its login profile. A printed
`task_stopped` alone is not proof that this legacy task was removed.

The helper follows only this preparation/run. On run termination or `STOPPING` it
closes its own SSH processes and dedicated browser, retaining the profile. Short
connection interruptions may recover only to the same run. After 300 seconds of
continuous lost contact it exits locally; after sleep it checks the deadline before
trying to reconnect. It never reconnects after expiry or discovers a different run.
A new attempt then requires an explicitly generated entry for the intended run.

Closing CC or its interactive SSH session does not stop an already started managed
research run. Before launch, preparation and the launch handoff remain time-limited.
The Windows PC and dedicated browser need to stay available only while supplying
institutional access; closing them makes that route unavailable, while bounded
public/OA fallback and other research continue. This does not guarantee every PDF.
Only a foreground `-Action Run` diagnostic requires keeping its terminal open.

Local execution, PowerShell 7.2+ and valid SSH authentication remain prerequisites.
Server-only init cannot start programs on an unconnected PC. Login/MFA stays in the
browser; retained runs are not migrated. Windows GUI/lifecycle behavior still needs
acceptance on the actual client, separately from automated test results.

### Client prerequisites and manual diagnostics

Copy `hypoloom/connection_assets/institutional-connect.ps1` to a stable Windows
location. PowerShell 7.2+ is required; Windows needs neither Python nor Hypoloom.
Use an existing `user@host` or SSH alias, establish host-key trust once, and configure
SSH key/ssh-agent authentication. The helper uses BatchMode and stores no passwords.
The Linux server must permit remote Unix-socket forwarding. It runs the installed
Hypoloom CLI using the displayed server paths and this init's exact scope.

#### First setup or a new Windows PC/account

Copying the connector does not transfer the old PC's SSH keys, host-key trust,
browser login state or authority for this run. Ensure the new PC can reach the server;
join the required VPN/private network if its address depends on one. The target
is still the Linux `user@host` or a locally configured SSH alias. A different
Windows username does not require changing the remote Linux username.

In the new PC's ordinary PowerShell, check both login modes:

```powershell
$researchServer = (Read-Host 'Server user@host or SSH alias (not a password)').Trim()
ssh -o ConnectTimeout=5 $researchServer exit
Write-Host "Interactive login exit code: $LASTEXITCODE"
ssh -o BatchMode=yes -o ConnectTimeout=5 $researchServer exit
Write-Host "Noninteractive login exit code: $LASTEXITCODE"
```

Verify the server fingerprint before accepting first-use host trust. Enter the
server password only in SSH's own prompt.

| Result | Action |
| --- | --- |
| Timeout, connection refused or unresolved hostname | Check network/VPN, server address, port and SSH alias configuration. |
| Host key verification failed | Verify the server identity and establish trust; do not disable host-key checking. |
| Interactive login returns `0`; BatchMode returns `255` with `Permission denied (publickey,password)` | Networking and password login work, but this user's noninteractive authentication does not; configure the key/agent below. |
| Both return `0` | SSH is ready. Retry Setup without generating or uploading another key. |

Setup's `init_ssh_connection_unavailable_check_key_and_known_host` is a connection
preflight error. It cannot by itself distinguish these causes, and is not an
Edge/publisher login failure. If both SSH checks pass but Setup still fails,
retain the original error and check its exact target, server paths and remote command.

If key setup is needed, enable ssh-agent in **Windows administrator PowerShell**:

```powershell
Set-Service ssh-agent -StartupType Automatic
Start-Service ssh-agent
```

Return to ordinary PowerShell as the **Windows user who will run the helper**.
The following uses the standard user-level `~/.ssh/id_ed25519`, not a project-specific
key, and preserves an existing private key. To use a key at another path, set
`$clientKey` to that path and load it into this user's agent. Choose a key passphrase
when generating a key; `ssh-add` may ask for it again. It differs from the server password.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $researchServer = (Read-Host 'Server user@host or SSH alias (not a password)').Trim()
    if ($researchServer -notmatch '^[A-Za-z0-9_][A-Za-z0-9_.@-]*$') {
        throw 'Enter an SSH target or alias, not a complete ssh command.'
    }
    $clientKey = Join-Path $env:USERPROFILE '.ssh\id_ed25519'
    New-Item -ItemType Directory -Force (Split-Path $clientKey) | Out-Null

    if (-not (Test-Path -LiteralPath $clientKey)) {
        & ssh-keygen -t ed25519 -f $clientKey
        if ($LASTEXITCODE -ne 0) { throw 'Key generation failed; stopped.' }
    }
    if (-not (Test-Path -LiteralPath "$clientKey.pub")) {
        $publicKey = & ssh-keygen -y -f $clientKey
        if ($LASTEXITCODE -ne 0) { throw 'Public key export failed; stopped.' }
        $publicKey | Set-Content -LiteralPath "$clientKey.pub" -Encoding ascii
    }

    & ssh-add $clientKey
    if ($LASTEXITCODE -ne 0) { throw 'Key loading failed; check the ssh-agent service.' }

    $installPublicKey = 'umask 077; mkdir -p ~/.ssh && chmod 700 ~/.ssh && { printf "\n"; tr -d "\r"; } >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'
    Get-Content -LiteralPath "$clientKey.pub" | & ssh $researchServer $installPublicKey
    if ($LASTEXITCODE -ne 0) { throw 'Public key upload failed; stopped.' }

    & ssh -o BatchMode=yes -o ConnectTimeout=5 $researchServer exit
    if ($LASTEXITCODE -ne 0) { throw 'Noninteractive authentication still failed; retain the SSH error above.' }
    Write-Host 'Noninteractive SSH authentication succeeded; rerun Hypoloom Setup.'
}
```

Only the public key is appended to the Linux user's `authorized_keys`; other
machines' authorizations remain. Another server can use the same user key: change
the SSH target and deploy the public key there, without replacing existing keys
or authorization lists. Keep the private key on Windows. See
[Windows OpenSSH key management](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement)
for agent setup. Once authentication succeeds, rerun this in the directory where
you downloaded the original connector:

```powershell
pwsh -NoProfile -File .\institutional-connect.ps1 -Action Setup
```

Enter the target, server paths and exact StateRoot/RunId/RequestId/PreparationRoot
shown by this init; standard installations may accept the Python default. Return
to the original CC session: waiting preparation detects the connection automatically;
at awaiting_launch choose `重新准备` only when offered. An expired controller cannot
be revived with its old entry. SSH setup does not require another research run.
Publisher login on the new PC happens separately in the dedicated browser.

#### Manual connection diagnostics

With the updated local script, first read **Status** on Windows. It reads the local
scheduled task and recent safe diagnostic events; it does not contact SSH, open a
browser, restart the helper or create a directory. There is no need to stop the
background task and reproduce the failure in foreground Run. Use the exact Server
and RequestId from this init's generated entry; RequestId is the create request ID,
not the run ID. This read does not require Repository, StateRoot or PreparationRoot.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $researchConnector = Join-Path $env:LOCALAPPDATA 'Hypoloom\institutional-connect.ps1'
    $researchServer = (Read-Host 'Exact Server value from this init entry (not a password)').Trim()
    $researchRequest = (Read-Host 'Exact RequestId value from this init entry').Trim()
    & pwsh -NoProfile -File $researchConnector -Action Status -Server $researchServer -RequestId $researchRequest
    if ($LASTEXITCODE -ne 0) { throw 'Status failed; retain the diagnostic above.' }
}
```

The result reports task state/last result, `diagnostic_path` and `recent_events`.
Events are under `%LOCALAPPDATA%\Hypoloom\ConnectionLogs-<Server>\<RequestId>.jsonl`;
retention is bounded. These are diagnostic observations, not connection authority
or a promise of current browser health. A Ready task or LastTaskResult alone cannot
identify the reason it stopped. Missing logs, an old helper or a failed local read
leave that reason unknown; inspect `local_status_reason` and any `diagnostic_reason`,
then retain the safe output.

| Status observation | Meaning and next step |
| --- | --- |
| `run_terminal` | The server retired this connection scope, including stopping/terminal research. Normal helper exit; it does not prove publisher verification. |
| `launch_review_expired` | The bounded pre-launch review expired. Normal helper exit; research was not started by this event. |
| `run_connection_expired` | Connection/control checks could not remain confirmed for 300 seconds, including elapsed sleep. It does not establish that an open publisher page stopped working. Read preceding safe reasons before reconnecting the same intended run. |
| `run_discovery_timeout` | The scoped server-status query did not return before its deadline. This does not establish a browser crash or failed institutional login. The helper discards that query session and retries within the existing outage window. |
| `cleanup_failed` event | Resource cleanup failed. Keep this event and the original exit/failure reason; neither should replace the other. Do not assume every child/browser closed. |

`run_terminal` and `launch_review_expired` are normal exits; connection expiry and
identity/privacy errors remain failures. A support check should begin with this
Status output, rather than repeatedly reinstalling or opening another foreground
helper. Foreground Run remains an explicit diagnostic only, with the original scope
and no matching background instance running. Never stop processes by name or adopt
another run's browser.

The current helper reuses one SSH session for status queries. Each query still
reads fresh server state and validates the same run identity; an open SSH process
or an old response cannot renew the connection deadline. A timed-out session is
closed before retrying, so delayed responses cannot be reused. Update the local
script through the next init's original-file download step and execute that
init's scoped command; no extra SSH flags or hand-edited script are required.

The helper finds Edge before Chrome. Default `-LocalPort 0` asks the helper to
allocate an available loopback port through the OS, then launch the browser with
that actual nonzero port. Passing debugging port 0 directly to Chromium enables
its webdriver flag, so new launches no longer use it. Listener ownership and
loopback checks still apply after allocation. Profiles remain under
`%LOCALAPPDATA%\Hypoloom\Institutional-<Server>-msedge` (or the corresponding
Chrome directory), separated by server account; use
`-BrowserExecutable` / `-BrowserProfile` to retain your previously authenticated
dedicated profile. Only its user, SYSTEM and administrators may access it. Cookies
are not exported or copied. The helper owns the browser it starts for this scope;
it does not adopt an already running manual or previous-run browser.

**Updating an older port-0 browser:** arrange a client connection interruption,
run the installed entry with `-Action Stop` and its original server/connection
arguments, then close all dedicated Hypoloom browser windows normally. Ordinary
browser windows and the dedicated login directory can stay. Download the original
script using the file workflow above and run `-Action Setup`. Replacing a file or
reopening CC does not reload an already running task or browser. If the entry
reports `browser_restart_required_close_dedicated_window`, the old browser process
still exists: exit that dedicated browser and Connect again. The program never
kills it or removes stored login data.

Use `继续连接` while preparation is waiting, or an offered `重新准备` during
its bounded awaiting_launch stage; a new research run is unnecessary. In the new dedicated window,
check Automation Check on [Cloudflare's official diagnostic](https://debug.challenges.cloudflare.com/),
then verify the actual ScienceDirect challenge and a paper sample. Removing the
known launch side effect does not guarantee website acceptance; login, challenges
and MFA remain manual browser actions.

Setup/Connect prepares profile permissions before starting the scoped helper, so a failure
appears in the calling terminal. For the current user's default dedicated profile,
extra **inherited** allow rules (for example, another local group's read access)
are removed after disabling parent inheritance while preserving existing allowed
identities and deny rules. The helper rereads the ACL using the same privacy check;
it does not change parent permissions, group membership or browser file contents.
Windows propagates inherited ACL changes normally; this is not a recursive reset
of independently protected child ACLs. New default profiles use the same check.
Custom locations, explicit extra grants, different owners and redirected directories
are not automatically repaired. If an older entry reports
`browser_profile_must_be_private` for inherited permissions, download the updated
original script and run Setup. A successful repair prints
`browser_profile_permissions_repaired; login_data_preserved`.

The initial `c254d5f` repair could leave an extra grant as explicit instead of
removing it. The corrected entry builds retained rules in memory and writes once.
If before/after ACL output confirms this older partial repair, remove only the
identified residual group's grant on that dedicated directory with
`icacls <dedicated-profile> /remove:g <confirmed-local-group>`, then reconnect.
The connector still refuses unknown explicit grants; merely downloading again
does not authorize their removal. Its errors now distinguish
`browser_profile_explicit_access_requires_manual_permissions`,
`browser_profile_custom_path_requires_manual_permissions`,
`browser_profile_permissions_write_failed` and `browser_profile_permissions_not_applied`.

The helper creates forwards only after confirming its exact preparation/run scope.
Remote targets are private per-run Unix sockets, so there is no `-RemotePort`.
A failed query cannot authorize a new forward from a cached entry. Short retries
remain bounded by the preparation/acquisition deadlines and the 300-second local
contact limit; a successful listing for another run is never a recovery target.
Ctrl+C, Stop and retirement close only this helper's SSH/browser resources, retaining
login data. No task logon trigger or task restart keeps it alive for future runs.
The PC cannot be woken by server init; publishers still control session expiry/MFA.

Visible Linux GUI, native Windows task APIs and actual container-image deployment
require checks on those machines; see [qualification](../qualification.md#run-scoped-institutional-connections-2026-10-02).

## Prepare platforms before research

For a Windows client, start server init, confirm the publisher list in the interactive
question, and use its generated scoped preparation and Windows entry commands.
IEEE, ACM, ScienceDirect and Springer Nature must be offered; selecting client mode alone
does not accept the recommended list. You can select other catalog entries or skip.
Until you answer, inspect reports `institutional_prepare_provider` as missing and
returns no preparation/launch command. An existing explicit answer is retained on
reinspection. Unselected institutional routes remain disabled; public/OA retrieval
is independent. For the catalog or standalone preparation on a native
Linux desktop, use:

```bash
python -m hypoloom institutional prepare --list
python -m hypoloom institutional prepare \
  --institutional-browser-mode local
```

The existing catalog shows four common platforms first:

| Platform | Standalone default / init suggestion | Behavior |
| --- | --- | --- |
| IEEE | Yes | Reuse valid sessions; authenticate in the browser if needed. |
| ACM | No, optional | Public full text first; login is not a reading prerequisite. |
| Elsevier / ScienceDirect | Yes | Both names are accepted by preparation and mode arguments. |
| Springer Nature | Yes | Open the official institution finder, then authenticate with the institution. |

With no platform/sample arguments the standalone command selects these three preparation
defaults, excluding platforms set to `public_only` or `disabled`. Repeated `--provider <platform>`
replaces the defaults and can include ACM or other supported platforms; `--provider none`
does not connect. Samples alone still select only their own platforms. Preparation
never restricts research discovery. ACM's default follows its
[2026 open-access announcement](https://www.acm.org/about-acm/acm-pres-on-oa-transition-and-dl-changes);
it does not guarantee successful automated downloads. Select ACM explicitly to
enable its institutional browser fallback.

Selected preparation platforms carry `auto` into the generated launch arguments,
unless an explicit `public_only` or `disabled` mode overrides them. All other
publishers show **Not enabled** under **Institutional route** (`disabled` internally);
the catalog is not a selection of every supported publisher. Public discovery and public/OA full-text
retrieval remain independent. With preparation set to `none`, only explicitly
configured provider modes remain enabled. Existing runs retain their frozen policy.

Choose one browser profile and optional `--institutional-library-url` for the batch.
Without a library URL, preparation opens Springer Nature's official institution finder
or the other publishers' homepages. Institution selection,
SSO and MFA remain browser operations; no account/password store is added. Supply
sample DOI/canonical article URLs interactively or using repeatable `--sample`.
Supplied samples are checked first; if all are readable, no login prompt appears.
A readable public sample is not proof of institutional authentication. At a manual
prompt, `s` skips, `p` selects public-only access, `q` ends the batch, and Enter
finishes or rechecks supplied samples. With no supplied sample, IEEE, ScienceDirect,
Springer Nature and optional ACM automatically check a fixed public DOI after Enter (or
`完成` in init). Defaults never bypass the manual login opportunity. Other platforms
without fixed samples retain `not_checked`.
Failed platforms do not prevent preparing others. Unresolved sample failures return
a nonzero exit code but are not a research-start gate. With
`--non-interactive --sample <DOI-or-URL>`, check the existing session without waiting
for terminal input. `--list` does not connect. Select any supported publishers in one preparation command. The program processes
them sequentially and makes completed owned tabs available for eviction, retaining
the limit of eight simultaneous owned tabs and leaving user tabs/cookies intact. No research Host/run,
Attempt, Profile, model call or source archive is created. Bootstrap review provides
`institutional_preparation` and a complete `institutional_preparation_argv`.
Repeated bootstrap `--institutional-prepare-provider` replaces the list; `none`
skips preparation, independently of the `off` browser mode. Ordinary preview never
opens a browser. Final `preparation_summary` distinguishes unverified, skipped,
readable samples, authentication/verification requirements and unavailable results.
Merge returned `provider_modes` / `provider_mode_argv` by platform into subsequent
launch choices and re-review; standalone users must pass these options themselves.

### Springer Nature institution access and PDF challenges

Springer Nature is one preparation and access-policy group covering both
`link.springer.com` and `nature.com`. Its institutional login URL is unchanged;
Nature's homepage is no longer a separate preparation entry. Old `Springer` and
`Nature` CLI names are aliases for `Springer Nature` and deduplicate to one entry.
Conflicting old modes (for example, Springer=auto and Nature=disabled) are rejected;
choose one explicit group mode. Existing runs retain their frozen runtime.


Springer Nature preparation opens the [official institution finder](https://wayf.springernature.com/?redirect_uri=https%3A%2F%2Flink.springer.com%2F).
Search **Find your institution**, select the institution and complete its login.
An existing batch on the homepage can use this URL in the same dedicated browser
tab. New batches use the updated entry; the Windows helper and init need no reinstall
or recreation. A supplied library URL still takes precedence; repeated open actions
preserve the existing tab. If no institution appears, use the school's library
database link and its supported campus/remote access. A personal Springer account
alone is not subscription proof. Springer documents both
[federated institution lookup](https://support.springernature.com/en/support/solutions/articles/6000083159-institution-not-displayed-on-institutional-login-page-for-athens-shibboleth-list)
and [IP/proxy access](https://support.springernature.com/en/support/solutions/articles/6000080116-user-access-to-springer-nature-link-and-springer-for-r-d-content).

ScienceDirect article access or school login does not establish PDF access.
Cloudflare's [challenge lifetime](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/challenge-passage/)
and [clearance levels](https://developers.cloudflare.com/cloudflare-challenges/concepts/clearance/)
are site-controlled; later requests may require another challenge. If manual
completion in the same dedicated Edge opens the PDF, retry the failed platform
after the batch ends and check for `sample_verified`. A persistent loop can prevent
institutional PDF retrieval. Positive detection returns `verification_required`,
never article content or a no-subscription claim. Positively identified full HTML
and same-article public/repository sources may still supply full text, or the user
can legally download/import a PDF; alternative availability is not guaranteed.
Preparation/acquisition deadlines remain finite, without automated challenge solving
or endless retries. Opening a page, authenticating, downloading and verifying the
paper's identity are separate outcomes.
The result is not persisted and does not change existing runs or store credentials.

### Fixed samples

After updating, select institutional preparation in the next init, connect the
Windows client, finish any needed authentication in the dedicated browser and
reply `完成` in the original conversation. No separate test command or DOI search
is needed. The program retrieves and validates one fixed full-text sample per
platform using the existing acquisition deadline (120 seconds by default), byte
limit and document identity validator. Skip, public-only and finish do not download
default samples.

After the login opportunity, the fixed probe tries the shared public PDF discovery
and download path first, then the existing browser within the original deadline.
A fixed sample that hits a challenge/sign-in page records failure and advances; it
does not ask for per-paper manual downloading. A different explicit sample still
allows optional manual recovery. Do not rerun init repeatedly to clear a CAPTCHA.

| Platform | Fixed DOI / publisher source |
| --- | --- |
| IEEE | [10.1109/ACCESS.2019.2902267](https://ieeexplore.ieee.org/document/8654603/) |
| ScienceDirect | [10.1016/j.softx.2024.101723](https://www.sciencedirect.com/science/article/pii/S2352711024000943) |
| Springer Nature | [10.1007/s10664-020-09851-6](https://link.springer.com/article/10.1007/s10664-020-09851-6) |
| ACM, when selected | [10.1145/945445.945450](https://dl.acm.org/doi/10.1145/945445.945450) |

These public full-text probes are independent of the institution and research topic.
[IEEE Access is fully open access](https://ieeeaccess.ieee.org/about/); the
ScienceDirect and Springer pages mark these articles Open access. ACM follows the
2026 announcement linked above; its sample's title/DOI is also registered in
[Crossref](https://api.crossref.org/works/10.1145/945445.945450).
The existing platform catalog owns the defaults (`institutional prepare --list`);
the model does not choose them during setup.

`sample_verified` with `sample_scope: public_full_text` proves that article's
current readability, **not institutional authentication or subscription coverage**.
Use `验证样本` with a platform DOI, or explicit `--sample` in a standalone command,
to replace that platform's default with an article covered by your institution.
Even a custom sample proves only its own readability. Challenge, access, transport
and identity failures retain their actual states; retry failed platforms or accept
unverified access before launch review. Sample bytes are released after checking
and never enter a research archive. Existing runs are unchanged.

`sample_verified` accepts an identity-checked PDF or positively identified HTML
full text. Results now include `media_type`, `acquisition_source`, `source_url` and
`page_count`: only `application/pdf` establishes a PDF transfer. `public_pdf` means
a public source; `browser` means the dedicated browser. A successful public PDF
needs no new browser PDF tab. Neither establishes unattended batch access; older
results without these fields do not establish PDF
downloading. PDF acceptance needs actual bytes, parsed pages and article identity;
an abstract/detail page or HTML/XML full text is not a PDF.

Unattended research uses existing public search, PDF candidates and exact-DOI OA
fallback before an available institutional session. Background login/challenge
outcomes return unavailable; research proceeds with an explicit full-text gap.
Manual intervention is an optional recovery path. A successful ScienceDirect
challenge does not guarantee later downloads: [clearance depends on the website](https://developers.cloudflare.com/cloudflare-challenges/concepts/clearance/).
Without publisher APIs, only currently accessible publisher files or legitimate
public copies can be downloaded automatically; per-paper success is not guaranteed.
The optional [Elsevier TDM channel](https://www.elsevier.com/about/policies-and-standards/text-and-data-mining/faq)
requires verified access, and XML full text is not the publisher PDF.

During a run, the TUI **i** panel is read-only: publisher routes, public-plus-institutional
public-only or disabled policies, current pending/ready/gap counts and latest observations.
Idle publishers remain visible; a configured route is not proof of login or subscription.
The panel refreshes with the main monitor. **Close** or **Esc** returns to research.
It has no PDF path input, login/retry/sample actions or policy controls. Optional manual
operations remain available through an authorized Web panel and are not required for
unattended operation. Avoid a second standalone manager against the same browser.
A timestamped verified sample proves that paper was readable at that time; its bytes
are released, not archived as research evidence. Reconnecting the same browser can
reuse a valid session, but institutional/publisher expiry can require another login.

Public documents and the configured exact-DOI OA locator run before institutional
access. Background requests that need login, verification or access intervention
finish unavailable so research can continue. Automatic acquisition has a **120-second**
deadline from enqueue, including queue time and retries. Explicit login/check opens a
**600-second** platform window. New papers never extend it. Timeouts need an operator
retry; denial of one paper never disables the entire platform automatically.

| Optional Web control | Scope |
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
--institutional-provider-mode "Springer Nature=public_only"
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

## Manual persistent server browser (compatibility mode)

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

The optional library-entry flag still applies. The TUI observes connection state;
optional Web login actions focus the server tab. Credentials are entered only
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

## Manual laptop browser (compatibility mode)

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

On a shared server where a private institutional browser is unsuitable, keep the
Windows client as an optional source. While it is offline, research can still use
archived originals and public/OA copies; new documents requiring its private
session are not guaranteed. One init login does not transfer continuing access
to the server or fetch every paper that future research might select. A server
desktop is not a prerequisite for this workflow. Qualify IEEE PDF access using
actual PDF bytes, paper-identity validation and an archive; readable HTML, a PDF
viewer or a successful public repository copy is not stable institutional access.

When a selected paper's existing public PDF candidates fail, the shared acquisition
path tries an **arXiv same-title fallback**. It queries the title from exact-DOI
metadata, checks the title/authors and any supplied DOI, then downloads the unique
versioned official PDF. Missing authors, ambiguity, conflicting identity, invalid
PDFs and timeouts remain gaps without human recovery requests. The archive records
the actual arXiv ID/version; a preprint is not the publisher's version of record.
This reuses public downloading and archiving, with no API key, institutional login,
Windows helper or additional init configuration. Hypoloom processes sharing a
Linux host's `/tmp` serialize arXiv API calls, waiting three seconds before each
request within the existing deadline. Separate hosts/containers still require
coordination of their combined traffic under the
[official limits](https://info.arxiv.org/help/api/tou.html).

A temporary public PDF timeout can retry once within the acquisition's remaining
time and HTTP budget, without extending its deadline, repeating discovery or
keeping a partial PDF. Rate limits, access denial, challenges, invalid PDFs and
identity conflicts do not trigger this retry. Download diagnostics retain the
DNS/TCP/TLS/request/header/body timeout phase and received-byte count where known.

Press **i** in the TUI to open **Institutional access** (English by default, with a Chinese
language option). Each publisher row shows its **Institutional route**, current
institutional pending/ready/gap counts and latest observations. The route labels are
**Allowed**, **Off (public only)** and **Not enabled**. They mean institutional browsing
is allowed, explicitly off by public-only choice, or not enabled, respectively.
The panel explains that public/OA retrieval remains available for all publishers,
including those outside the list. Route settings do not guarantee login, subscription
or successful downloads. Unselected publishers show **Not enabled** for their
institutional route; this does not exclude their publicly accessible papers.
The Web institutional panel uses the same route labels and public/OA explanation.
Reopening an updated monitor does not change an existing Host's publisher modes.
No institutional requests does not mean public
retrieval failed, and ready full text does not prove an Agent read it or obtained PDF.
Counts cover the current bounded queue, not historical totals or public downloads.
Where the Host exposes its existing local notification log, recent observations
also show successful document-validation events from its last 1 MiB. These are
best-effort events, not unique PDF downloads, archived sources or evidence of reading.
An empty cache does not erase past successes; absent log observations mean unknown.
Client endpoint presence is not browser readiness. Missing Host observations show
unknown, separately from explicitly disabled access. **Close** or **Esc** returns;
the panel requests no files, login, retry decisions or state changes.

Browser acquisition starts only for explicitly selected tool requests, after
public/OA retrieval. Merely receiving a metadata packet does not queue all missing
PDFs. `web_fetch` waits in the tool process within the existing acquisition
deadline, without extra model calls or blocking the Host. A successful
`web_fetch(mode="document", url=...)` admits exact originals and extracted
text through the existing `/papers` workspace and archive. Downloading does not
rewrite finished research conclusions. Denial, missing full text and uncertain
identity stop automatic retries and remain explicit evidence gaps.
The bounded in-memory cache can reuse automatically acquired bytes for the same
URL explicitly requested by another expert in this run. Each selection must pass
its own identity check; source references and archives remain separately owned.
Manual imports and other runs are excluded. Nothing assumes a continuing login,
subscription or successful CAPTCHA resolution.

For optional manual intervention, start a separate Web observer with
`--allow-literature-control` and use its operator link. Plain read links cannot
trigger actions. Its Workbench "机构全文" tab retains login, retry and PDF import;
these are no longer TUI actions. Imports require an absolute Host-side path and
the existing PDF, readable-text, DOI/title and source validation. Authentication
stays in browser pages. This is optional supplementation, not an unattended workflow
requirement.

## Legacy fixed ports and persistent sessions (compatibility mode)

An explicit unmanaged `--institutional-browser-url` retains its operator-owned
endpoint behavior; it is not the new single-run Windows helper. Existing frozen
Hosts keep their original implementation and are not migrated. New init should use
the scoped client entry above instead of configuring a persistent fixed-port tunnel
or Windows logon tasks. The Web observer's HTTP port is unrelated to browser CDP.

If you previously created browser/tunnel tasks manually, stop their automatic
startup explicitly and close the old dedicated browser before new Setup. Setup
only cleans matching legacy helper tasks after checking ownership; it does not
remove unrelated tasks or take over their listeners. Keep the private browser
profile to preserve login data. Cookies/storage are not copied to the server.

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

The TUI shows the latest brief reason; Web retains original codes, last-check time
and approximate time until the next eligible check (the serial queue can delay it). With the Profile log
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

## Fresh init acceptance

For an explicit unattended-stability priority, send this in a new CC session:

```text
/autoresearch init
Prioritize unattended stability with Windows client mode.
Use public_only for ScienceDirect and ACM; keep IEEE and Springer Nature on auto and prepare them.
Use --institutional-prepare-failure-mode public_only.
Convert failed or unfinished preparation platforms to public-only and retain the failure reasons.
Stop at final launch review and wait for my Start now decision.
```

ScienceDirect/ACM are examples from the reported failures, not a global blacklist.
Use your own observed platform results. The program returns existing `provider_modes`
for replacement in launch choices. Public-only keeps public search and exact-DOI
full-text fallback while disabling automatic institutional browser acquisition.
Failures, connection timeout and unvisited platforms become public-only; completed
or skipped choices remain. Actual failed states and nonzero probe exits stay visible.
There is no repeated retry/accept question or automatic research launch. The default
`review` policy still offers retry or continuation with unverified access.

Missing legitimate full text remains an explicit gap; this does not guarantee every
PDF. First client setup and preparation of platforms kept on auto may still require
authentication, but research does not require solving per-paper challenges. Use
the current scoped helper for new init; retained runs are not changed.

To specifically test every publisher, use the ordinary review policy:

1. Open a new CC session in the updated server checkout and run `/autoresearch init`.
2. Select Windows client mode and IEEE, ScienceDirect, Springer Nature and ACM (select ACM
   explicitly). On the chosen Windows PC, run this init's exact generated Setup/Connect
   entry once, using the current original script. Close any old dedicated browser first.
3. Finish authentication where needed and reply `完成` for each platform. Existing
   sessions need no repeated login. The program checks fixed samples automatically.
4. Review state, source, format and page count. Challenges, 403, identity failures
   and timeouts are failures. Choose the offered continuation with unverified access
   if desired, then stop at launch review (`awaiting_launch`). Only selecting **Start now**
   launches research. For Prepare only/cancel, CC executes the returned `finish_argv`.
5. Check that cleanup closes the helper-owned browser/SSH while retaining the profile.
   A subsequent init must require its own entry; an old PC/helper must not attach to it.
   Test brief disconnection, a contact loss over 300 seconds and sleep beyond the
   deadline separately on Windows; automated tests do not establish those GUI results.

This tests fresh init orchestration in the current environment. Reusing a login
profile is not first-install Windows qualification; one public sample does not
establish coverage for every paper from its publisher.

## Independent acquisition development

Develop and test acquisition before returning to CC/init or research integration:

```bash
python scripts/probe_literature.py --samples --repeat 2 \
  --output .local/qualification/literature-samples-01

python scripts/probe_literature.py \
  --query 'Publish or perish but do not forget your software artifacts' \
  --max-items 1 --output .local/qualification/literature-search-01
```

Use the project environment with Poppler. Each output directory must be new.
The script saves actual PDFs and `report.json`, including source, bytes, pages,
identity basis, failure stage and HTTP status. Exit 0 means every selected paper
yielded a validated PDF in this experiment; partial failures, empty search and
HTML-only results exit 2. It does not qualify a whole publisher or institution.
Public metadata availability and rate limits can also affect the result.

Repeat `--doi` for up to ten selections. Defaults are 45 seconds per paper and
300 seconds overall (`--seconds-per-paper`, `--total-seconds`). `--samples` reuses
the existing four-platform catalog. Repeated rounds fetch again and retain later
failures. The runner reuses public transport, PDF extraction, the DOI fallback and
identity validation without model calls, login collection or a research run.
The experiment, Host document fallback and init samples now share exact-DOI
OpenAlex/Crossref discovery and PDF validation. The ordinary OA parser tries the
exact HTTPS form of advertised HTTP PDF locations; HTTP itself remains rejected.

For one DOI, `--url` supplies an explicit candidate to isolate downloading from
discovery. Such results are labeled `supplied`, not automatic discovery success.
An explicitly supplied `--browser-endpoint http://127.0.0.1:PORT` tests the existing
CDP adapter without answering challenges. It creates and closes only its own tabs
and disconnects afterward, preserving the browser. It does not install a Windows
helper or create an SSH connection; avoid concurrent research on that browser.

Independent acquisition, actual Host document/archive closure and init interaction
are separate acceptance steps. Only newly launched runs use the updated runtime;
existing runs keep their pinned version.

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
