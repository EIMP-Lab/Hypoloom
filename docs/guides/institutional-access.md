# Institutional full-text access

English · [简体中文](institutional-access.zh-CN.md)

Public discovery and PDF retrieval remain first. Optional institutional access
uses a researcher's authenticated Chromium browser. Managed connections retire
with each run, while dedicated profiles retain still-valid sessions. The TUI groups unresolved
papers by publisher without opening repeated prompts. Other research continues;
available bytes do not establish that an Agent read a paper.

## Prepare the connection during init

Select `--institutional-browser-mode` for new `bootstrap inspect/launch`, `serve`
or `institutional prepare` operations:

| Mode | Browser location | Lifetime |
| --- | --- | --- |
| `off` | No institutional browser | No institutional connection or panel operations; public/OA retrieval remains available. |
| `client` | Windows Edge/Chrome | The Windows helper discovers active runs and opens one private SSH forward for each run. |
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
existing finite manual timeout; finish/expiry cleans up temporary resources.
This timeout also starts when entering connection waiting. Platform-answer waiting
has its own deadline. Status reads and `继续连接` never extend the connection window;
that reply only expedites the next check. The process reports remaining seconds.
An expired connection wait retains the latest connection result and remaining command.
Standalone terminal preparation remains supported; EOF returns `input_required`
and closes resources instead of raising or pretending login succeeded.

For manual diagnosis, use the first record's `status_argv` and `answer_argv`:

```bash
python -m hypoloom institutional preparation status --state-root /path/to/temporary-preparation
python -m hypoloom institutional preparation watch --state-root /path/to/temporary-preparation
python -m hypoloom institutional preparation complete --state-root /path/to/temporary-preparation --provider IEEE
python -m hypoloom institutional preparation verify --state-root /path/to/temporary-preparation --provider IEEE --sample 10.1109/example
python -m hypoloom institutional preparation retry_connection --state-root /path/to/temporary-preparation
python -m hypoloom institutional preparation finish --state-root /path/to/temporary-preparation
```

`complete` without a sample remains unverified; `skip` and `public_only` also take
the current provider. These commands address only the live temporary preparation,
never a research run. Read the background command's final summary after it exits.

Normal init runs the generated `instructions_argv` and displays its output verbatim:
the Windows file location, fixed Setup entry, input values, remaining time and replies.
Connection replies are `继续连接` (retry connection) and `跳过准备` (skip preparation).
After local setup, the program detects connection readiness automatically. Its
generated `watch_argv` waits at most 30 seconds for a user-action stage or batch exit,
then prints current instructions. It is read-only and never answers for you; CC must
use this command instead of writing sleep/poll loops.
Platform replies are `完成`, `验证样本`, `跳过此平台`, `仅公开`, `结束准备`
(complete, verify a separately supplied sample, skip this platform, public only, finish).
The agent passes the exact choice to `reply_argv --text`; the process validates
the current stage and provider. Background notifications are never answers.
An ended batch explicitly requests `重新准备` (prepare again): read the prior final
summary and execute its generated `remaining_preparation_argv` unchanged. It includes
failed and unanswered platforms, retaining connection options, updated modes and
supplied samples; manual completion without a sample remains unverified and is not
repeated. No new CC session or research run is needed.

If a selected platform times out, init offers `重新准备` (retry the remaining platforms)
or `接受未验证状态，继续检查启动配置` (accept unverified access and continue to launch
review). Optional preparation failures do not block research, but do not mean success.
For an older summary with failures and an empty retry vector, init regenerates the
preparation command using read-only bootstrap inspection for just those platforms,
preserving the other choices and launch identity. Keep the dedicated browser open.
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
for parallel sessions. Windows runs may share the one user-owned browser through
independent forwards. Run retirement leaves that Windows browser open.

```bash
python -m hypoloom institutional prepare --institutional-browser-mode local --provider IEEE --provider Springer
# On a server, while the Windows helper is running:
python -m hypoloom institutional prepare --institutional-browser-mode client --provider Springer
python -m hypoloom institutional connections --runs-root /path/to/Hypoloom/state
```

Preparation creates a temporary private control directory under `<repository>/state`
and removes its transport when the operation ends. The helper's `-RunsRoot` must
match that parent. It creates no research run or archive. Use `--repository` when
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
python -m hypoloom institutional connections --state-root /path/to/run --include-ui-credentials
```

Forward that GUI port with SSH `-L`, then enter its temporary GUI credentials.
School credentials are entered only inside the browser. The read-only Web panel
does not reveal GUI passwords. `--institutional-connect-timeout` is 30 seconds by
default (3–120). Browser connectivity does not establish subscription rights.

The old explicit `institutional connect status|start|stop` commands remain manual,
operator-owned diagnostics for local/existing/server. They are not invoked by new
run startup and do not acquire the run lifetime described above.

### One Windows terminal entry for init

**Inside an existing CC init**, first setup and upgrades use an original script file,
not installation source reproduced in chat. Bootstrap and preparation expose the
same `script_source`, `setup_inputs` and fixed file-launch command.

1. On Windows, open a download directory. Use SFTP/SCP or your existing file transfer
   tool to download the original `institutional-connect.ps1` from the server path
   shown by init. Do not save assistant-written source as the script. A Windows
   Hypoloom checkout or Python installation is unnecessary.
2. Open PowerShell 7.2+ in that directory and run:

   ```powershell
   pwsh -NoProfile -File .\institutional-connect.ps1 -Action Setup
   ```

3. Answer the program's prompts for your SSH target, server repository, Python and
   runs directory. Init shows the actual three paths. Standard installations may
   accept the defaults for the last two; otherwise enter the displayed values
   without editing source. Setup checks SSH/remote Python, copies the original
   bytes to `%LOCALAPPDATA%\Hypoloom\institutional-connect.ps1` and invokes Connect.
4. After `client_discovery_started`, return to the same init; preparation checks
   actual readiness automatically. If the batch ended, reply `重新准备`. Later init
   sessions reuse the installed discovery task; a healthy task needs no reinstall.
   Setup never opens a second CC session.

Without a graphical transfer tool, use interactive OpenSSH SFTP from the Windows
download directory. Replace this example target with your existing SSH target:

```powershell
sftp research-server
```

At `sftp>`, download the `script_source` displayed by init and exit. This path is an
example; surround paths containing spaces with double quotes:

```text
get /path/to/Hypoloom/hypoloom/connection_assets/institutional-connect.ps1
bye
```

After a successful download, run the fixed Setup entry above. File transfer preserves
bytes without model transcription or local code-page decoding. For old garbled or
mistyped setup, obtain the original file again instead of patching chat-generated
source. Press Ctrl+C first if an old command is stuck at `>>`. The SSH target is not
a password; Windows loopback refers to Windows. Password login alone does not prove
background authentication works: follow the key/ssh-agent steps below and verify
`ssh -o BatchMode=yes <target> exit` returns 0. The server still checks the browser.

Background discovery tasks use a hidden window. Repeating Connect updates only an exact
matching prior task's window setting, preserving user/program/connection ownership
checks; a running prior visible task is restarted, briefly interrupting connections.
Already updated tasks are not restarted for this change. SSH keys and browser
profiles are retained; publisher login still uses a visible browser.
`client_discovery_started` means task startup was requested, not browser readiness.
Connect returns after starting hidden discovery; its PowerShell window may close.
Only a foreground `-Action Run` diagnostic needs an open terminal. Its
`waiting_for_preparation_or_run` message means no active connection target was found;
keep the server CC preparation active as well. It is not a connection success.

Windows may report a task principal as a short username while the current session
uses a computer-qualified name. The connector resolves account names through
Windows and compares SIDs. If an older entry rejects this with
`scheduled_task_ownership_mismatch`, download the original file and run Setup
again to obtain the update; do not rename or delete the task. Different accounts,
programs or connection arguments are still rejected.

**To start a new init session from Windows**, use the entry below:

Complete Setup through the file workflow above once. You can then start a new init
from Windows with the same SSH target and repository; standard Python and state
paths are derived, without another helper terminal or configuration file:

```powershell
$researchServer = 'research-server' # Your existing user@host or SSH alias
$researchRepository = '/path/to/Hypoloom'
$researchConnector = Join-Path $env:LOCALAPPDATA 'Hypoloom\institutional-connect.ps1'
pwsh -NoProfile -File $researchConnector -Action Init -Server $researchServer -Repository $researchRepository
```

Init checks SSH authentication and the remote Python/Claude commands first, then
installs or reuses the existing current-user discovery task, starts it immediately,
and opens remote `/autoresearch init`. The task also starts at user logon, retaining
connection capability for research and later ordinary SSH init sessions. It never
opens a browser without an active preparation/run. Nonstandard installations can
pass `-PythonExecutable` and `-RunsRoot`; mismatched task ownership/arguments are
not overwritten. Stop/Uninstall retain browser login data.

The local entry, PowerShell 7.2+ and valid SSH authentication remain real prerequisites.
Server-only init cannot install or start programs on an unconnected Windows PC;
it reports connection state and offers retry/finish. Institution login and MFA
remain human-operated. SSH setup and compatible manual commands follow below.

### Client prerequisites and manual diagnostics

Copy `hypoloom/connection_assets/institutional-connect.ps1` to a stable Windows
location. PowerShell 7.2+ is required; Windows needs neither Python nor Hypoloom.
Use an existing `user@host` or SSH alias, establish host-key trust once, and configure
SSH key/ssh-agent authentication. The helper uses BatchMode and stores no passwords.
The Linux server must permit remote Unix-socket forwarding. It runs the installed
Hypoloom CLI using the three explicit absolute server paths below.

If SSH currently asks for a server password, configure a key **on Windows** once.
In an administrator PowerShell, enable the Windows agent:

```powershell
Set-Service ssh-agent -StartupType Automatic
Start-Service ssh-agent
```

Return to an ordinary PowerShell under the desktop user. Choose a key passphrase
when prompted; only the public key is sent to the Linux server. Enter the server
password in SSH's prompt, never in a script or chat:

```powershell
$researchServer = 'research-server' # Replace with your user@host or SSH alias
$researchKey = Join-Path $env:USERPROFILE '.ssh\hypoloom_ed25519'
New-Item -ItemType Directory -Force (Split-Path $researchKey) | Out-Null
if (!(Test-Path $researchKey)) { ssh-keygen -t ed25519 -f $researchKey }
ssh-add $researchKey
Get-Content "$researchKey.pub" | ssh $researchServer 'umask 077; mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'
ssh -o BatchMode=yes $researchServer exit
$LASTEXITCODE # Must be 0 before using automatic reconnect
```

Keep the private key on Windows. See
[Windows OpenSSH key management](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement)
for agent setup. These steps prepare SSH authentication; publisher login still
happens in the dedicated browser.

```powershell
$researchServer = 'research-server' # user@host or SSH alias
$researchConnector = Join-Path $env:LOCALAPPDATA 'Hypoloom\institutional-connect.ps1'
New-Item -ItemType Directory -Force (Split-Path $researchConnector) | Out-Null
scp "${researchServer}:/path/to/Hypoloom/hypoloom/connection_assets/institutional-connect.ps1" $researchConnector
$researchConnection = @{
    Server = $researchServer
    Repository = '/path/to/Hypoloom'
    PythonExecutable = '/path/to/Hypoloom/.conda/envs/hypoloom/bin/python'
    RunsRoot = '/path/to/Hypoloom/state'
}
& $researchConnector @researchConnection -Action Run
# Optional current-user logon task; repeat these same paths and options:
& $researchConnector @researchConnection -Action Install
& $researchConnector @researchConnection -Action Status
& $researchConnector @researchConnection -Action Stop
& $researchConnector @researchConnection -Action Uninstall
```

The helper finds Edge before Chrome. The local browser uses port 0 by default and
reports its own allocated port. Profiles remain under
`%LOCALAPPDATA%\Hypoloom\Institutional-msedge` or `Institutional-chrome`; use
`-BrowserExecutable` / `-BrowserProfile` to retain your previously authenticated
dedicated profile. Only its user, SYSTEM and administrators may access it. Cookies
are not exported or copied. An existing browser with matching user, executable and
profile may be reused, including one manually started on 9222.

Connect/Init prepares profile permissions before starting discovery, so a failure
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

The helper starts forwards only for active runs; with no run it starts no browser
and holds no research port. Remote targets are private per-run Unix sockets, so
there is no `-RemotePort`. Before reconnecting it verifies the current run and
removes only a refused stale socket pathname, never a live listener. Forwarding
retries are limited to three, 30 seconds apart; discovery retries are also bounded.
Ctrl+C stops the helper's own SSH children. Remote watch exits when its run ends or
Host disappears; automatic reconnect cannot resurrect terminal runs.

Install takes effect at next user logon; use Run for the current session. Tasks
with mismatched ownership/options are never overwritten. Before changing installed
options, Uninstall using the old options. Operators should also stop any old fixed
port task explicitly; this helper does not adopt it. Profiles are preserved, but
schools/publishers still control session expiry and MFA. The PC must stay online;
server init cannot wake it or complete login.

Visible Linux GUI, native Windows task APIs and actual container-image deployment
require checks on those machines; see [qualification](../qualification.md#run-scoped-institutional-connections-2026-10-02).

## Prepare platforms before research

For a Windows client, start the helper described above, then run these commands in
the project's server environment. Use `local` instead on a native Linux desktop:

```bash
python -m hypoloom institutional prepare --list
python -m hypoloom institutional prepare \
  --institutional-browser-mode client
```

The existing catalog shows four common platforms first:

| Platform | Prepare by default | Behavior |
| --- | --- | --- |
| IEEE | Yes | Reuse valid sessions; authenticate in the browser if needed. |
| ACM | No, optional | Public full text first; login is not a reading prerequisite. |
| Elsevier / ScienceDirect | Yes | Both names are accepted by preparation and mode arguments. |
| Springer | Yes | Reuse valid sessions; authenticate in the browser if needed. |

With no platform/sample arguments the command selects these three preparation
defaults, excluding platforms set to `public_only`. Repeated `--provider <platform>`
replaces the defaults and can include ACM or other supported platforms; `--provider none`
does not connect. Samples alone still select only their own platforms. Preparation
never restricts research discovery. ACM's default follows its
[2026 open-access announcement](https://www.acm.org/about-acm/acm-pres-on-oa-transition-and-dl-changes);
it does not guarantee successful automated downloads or disable browser fallback.

Choose one browser profile and optional `--institutional-library-url` for the batch.
Without a library URL, preparation opens publisher homepages. Institution selection,
SSO and MFA remain browser operations; no account/password store is added. Supply
sample DOI/canonical article URLs interactively or using repeatable `--sample`.
Supplied samples are checked first; if all are readable, no login prompt appears.
A readable public sample is not proof of institutional authentication. At a manual
prompt, `s` skips, `p` selects public-only access, `q` ends the batch, and Enter
finishes or rechecks supplied samples. Enter without a sample remains `not_checked`.
Failed platforms do not prevent preparing others. Unresolved sample failures return
a nonzero exit code but are not a research-start gate. With
`--non-interactive --sample <DOI-or-URL>`, check the existing session without waiting
for terminal input. `--list` does not connect. Prepare at most eight platforms per
batch; close the command and start another batch as needed. No research Host/run,
Attempt, Profile, model call or source archive is created. Bootstrap review provides
`institutional_preparation` and a complete `institutional_preparation_argv`.
Repeated bootstrap `--institutional-prepare-provider` replaces the list; `none`
skips preparation, independently of the `off` browser mode. Ordinary preview never
opens a browser. Final `preparation_summary` distinguishes unverified, skipped,
readable samples, authentication/verification requirements and unavailable results.
Merge returned `provider_modes` / `provider_mode_argv` by platform into subsequent
launch choices and re-review; standalone users must pass these options themselves.
The result is not persisted and does not change existing runs or store credentials.

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

## Legacy fixed ports and persistent sessions (compatibility mode)

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
