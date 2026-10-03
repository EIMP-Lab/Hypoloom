# Institutional literature access

The [follow-up preparation and bounded recovery design](institutional-access-preparation.md)
specifies publisher preparation before a run, durable operator skip/preferences
through existing controls, finite acquisition windows and shared OA fallback.
It is implemented for new lifecycle v11 runs. The architecture below includes
the original integration; the follow-up design specifies its bounded lifecycle changes; [qualification](../qualification.md#real-institutional-subscription-and-ieee-viewer-repair-2026-10-01)
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
work; no new SQL table, workflow state, SQL schema version, receipt or paper registry
is needed. Requests derive from frozen LiteraturePackets and actual web tool
requests. Complete bytes enter the existing PaperWorkspace and Agent archive
only through a successful document tool call, with ordinary source references.
Available bytes are not evidence of reading. Finished requests/reports are not
rewritten when a login later succeeds.

One browser worker serializes access. Background authentication requests finish
unavailable; explicit operator login/check opens a finite publisher window, polled
without repeated navigation. These requests never block the Host or
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
reset the count. Authentication polling now requires an explicit manual window; the finite queue
deadline includes all retries. Terminal outcomes, source identity and public-downloader
safety rules remain unchanged. Status derives a retry countdown
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

## Elsevier official article API (2026-10-04)

Observed failure: the operator completes a Cloudflare challenge in an ordinary
browser, while the dedicated controlled Edge repeats it. The user obtained an
Elsevier API key. Browser retries cannot establish API entitlement, and public
HTTP retrieval alone cannot consume the key. This adds an optional retrieval
adapter, not a new schema, credential store, marker, gate or access-proof registry.
Existing private Profile projection/reader, HTTPS DNS and peer validation, PDF
extraction, institutional document validation and PaperWorkspace remain authoritative.

`literature.elsevier_api_key` and optional `elsevier_insttoken` are authoring-only
credentials. They are removed before admission projection, use the existing
`credentials_file` reference and are reread only by the Host. CLI literals are
not accepted. Preflight is local and explicitly leaves full-text access unchecked.
New Host configuration records the adapter/bounds, never the secrets; retained
runs remain on their frozen source. Default init browser selection excludes
ScienceDirect when configured, but respects an explicit browser selection.

Existing public/OA PDF discovery stays intact. Agent-selected canonical
ScienceDirect/Elsevier DOI document access uses the official API instead of an
anonymous publisher request or automatic browser queue entry. Only the exact
generated HTTPS API URL receives authentication headers. Redirects reuse public
URL/DNS/peer/size checks and receive no authentication headers. Signed redirect
locations and raw error responses never enter source metadata or tool errors.
HTTP 401/403/404/429 remain distinguishable; errors do not request CAPTCHA retries.

Request `view=FULL`, initially as PDF for DOI selections. PII-only links use XML
to bind the returned core PII rather than relying on an unassociated PDF DOI.
A PDF HTTP 403 or 406 permits one XML
representation attempt within the same deadline and HTTP-call budget. This is
useful when the text-mining representation is accessible but PDF is not; it is
not a retry of invalid credentials or quota errors. PDF bytes need first-page
DOI/title evidence. XML requires the official response envelope, matching
core DOI/PII and a nonempty article body beneath originalText; META_ABS, a 200
challenge, malformed XML and entity declarations are rejected. Extracted XML text
enters the existing plain-text document path with explicit format/figure limits;
PDF retains its exact bytes. The ordinary source, reader and publication paths
decide admission and reuse; availability does not imply that an Agent read it.

The same existing PDF worker slots, work deadline and request budget bound the
operation. There is no new worker, background login wait, persisted access claim
or automatic browser fallback after an API failure. Exact-DOI OA fallback and
manual import remain available: with institutional controls enabled, API failure
registers a terminal item in the existing bounded acquisition queue, so the paper
remains selectable without scheduling browser work. Explicit operator import can
replace it and the next document call consumes the imported result. Existing skip/public-only choices also apply to
the API; public-only preserves ordinary anonymous reads. A separate read-only
module command verifies a selected sample without creating or altering a run.

Primary protocol sources: [authentication](https://dev.elsevier.com/tecdoc_api_authentication.html),
[Article Retrieval](https://dev.elsevier.com/documentation/FullTextRetrievalAPI.wadl),
[FULL versus metadata](https://dev.elsevier.com/tecdoc_text_mining.html).
The public official XML example was inspected for response/body namespaces;
it does not verify the user's key, server IP or institutional subscription.
