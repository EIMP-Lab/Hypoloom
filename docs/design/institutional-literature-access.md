# Institutional literature access

The [follow-up preparation and bounded recovery design](institutional-access-preparation.md)
specifies publisher preparation before a run, durable operator skip/preferences
through existing controls, finite acquisition windows and shared OA fallback.
It is a proposal dated October 1, 2026, not implemented behavior. The architecture
below describes the current integration; [qualification](../qualification.md#real-institutional-subscription-and-ieee-viewer-repair-2026-10-01)
records the completed one-paper real subscription check.

The public PDF downloader cannot use a researcher's institutional browser
session. Returning another publisher URL to the same anonymous downloader does
not repair this failure. Login pages must not become scientific source text.

Keep public discovery/download first. Add optional browser acquisition, using
Playwright over a loopback CDP connection (an SSH reverse tunnel can connect a
Linux Host to a researcher's Windows browser). The researcher signs in in the
browser. Passwords, cookies and browser storage never enter tools, prompts or
the database. Only explicitly selected scholarly URLs use this capability.

Server independence uses the same CDP adapter with a separately deployed
Chromium service, using jlesage/docker-chromium's existing GUI and persistent
`/config` volume. This supersedes the laptop-only storage constraint when the
operator selects server mode: credentials remain inside the browser's server
profile, outside the repository, research archive and Agent context. No custom
cookie vault, password collection, browser launcher or authentication proxy is
added to Hypoloom. Docker owns browser lifetime and storage; Host shutdown only
disconnects its client and closes its own tabs. A GUI origin option selects the
server login instructions; SSH local forwarding is needed only for interaction.
Browser state validity remains subject to institutional policy and network access.

The existing Host owns acquisition and the existing web document tool owns
source admission. A bounded, disposable queue/cache holds pending acquisition
work; no new SQL table, workflow state, schema version, receipt or paper registry
is needed. Requests derive from frozen LiteraturePackets and actual web tool
requests. Complete bytes enter the existing PaperWorkspace and Agent archive
only through a successful document tool call, with ordinary source references.
Available bytes are not evidence of reading. Finished requests/reports are not
rewritten when a login later succeeds.

One browser worker serializes access. Login/verification waits are coalesced by
publisher, polled without repeated navigation, and never block the Host or
ordinary research dispatch. A TUI screen shows the whole pending group and can
focus its browser tab or request another bounded attempt. The Web observer
shows the same group in its Workbench and permits the same open/retry/import
actions only with a separately granted operator literature link; read links
stay read-only, and the panel never collects credentials. STOPPING/terminal runs
discard their pending acquisitions; shutdown drains the bounded browser call.
After restart the next preparation/tool request reconstructs pending work from
existing inputs; an unobserved in-memory download is fetched again, not promoted
as retained evidence. Existing archive recovery remains unchanged.

Publisher adapters recognize article/PDF links, positive full-text HTML and
login/denial/verification states separately. Anonymous HTTP 403 alone is not
proof that login is required. Automatic acquisition requires article identity
evidence; unrelated redirects and abstract-only pages remain unavailable.
Manual PDF import goes through the same bounds, identity checks and admission.
Navigation exceptions and Chromium error pages discard the attempted selection;
they never admit the previous DOM. Transient load failures retry independently
of publisher login waits. A publisher mismatch alone is an unresolved redirect,
and paywall copy alone is an access requirement, not proof of a login redirect.

The September 29 CDP repair handles observed DOMContentLoaded-to-JS-redirect races:
wait up to about eight seconds without navigation; DOI/linkinghub remain transit
pages, not admitted article origins. Retry transient navigation at most once in
the same check, preserving any arrived login/verification page. Per-acquisition
consecutive transient failures reuse the existing in-memory `next_check` scheduling
with 30/60/120/300-second delays; success, non-transient outcomes and operator retry
reset the count. Authentication cadence, terminal outcomes, source identity and
public-downloader safety rules remain unchanged. Status derives a retry countdown
from `next_check`; existing reason codes/events remain the sole diagnostic path.
The browser-side PDF fetch retains its existing 15-second abort timer.

Missing acquisition diagnostics caused operators to repeat valid logins and
made stalled work invisible. Reuse the existing notification log writer for live
state transitions. An in-memory bounded Queue moves the existing Host's events
to ObserverServices, keeping filesystem/network work off the writer thread.
Git/database transactions cannot describe disposable browser navigation; no
second research journal or replay cursor is added. Log delivery is best effort,
local only, with explicit loss diagnostics, and never governs source admission.
Only canonical request URLs and allowlisted reason codes cross this boundary;
raw exceptions, authentication redirects and browser state remain private.
Requests, documents and browser tabs are bounded; provider waits do not grant
extra Agent budget. The runtime remains opt-in and existing runs stay read-only.

Validation covers public-first routing, group deduplication, login recovery,
denied access, identity mismatch, PDF/HTML validation, source publication and
scope, terminal/late-result cleanup, TUI controls, scoped Web-panel transport
and snapshot passthrough.
Fixture/browser tests do not qualify a school's real subscription or guarantee
unattended access. Real institutional verification needs a user-provided entry
URL and an entitled article.
