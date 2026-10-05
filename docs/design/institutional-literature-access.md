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
is needed. Requests derive from actual web tool selections, with identities from
frozen LiteraturePackets where available. Complete bytes enter the existing PaperWorkspace and Agent archive
only through a successful document tool call, with ordinary source references.
Available bytes are not evidence of reading. Finished requests/reports are not
rewritten when a login later succeeds.

One browser worker serializes access. Background authentication requests finish
unavailable; explicit operator login/check opens a finite publisher window, polled
without repeated navigation. These requests never block the Host or
ordinary research dispatch. The TUI observes publisher routes and current work
without issuing controls. The Web observer
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

## Shared PDF acquisition through Host and init (2026-10-04)

The operator authorized continuing from the independent experiment through Host
integration and fresh-init acceptance. A focused red regression reproduces a
runtime gap: the independent probe downloads an exact-DOI public PDF while the
Host goes directly to browser waiting without querying those metadata sources.
Reuse `document_fallback.py` for exact OpenAlex/Crossref DOI discovery and bounded
PDF admission; both the development runner and production callers use it. The
existing OA parser now tries HTTPS for exact HTTP PDF locations. No HTTP admission,
new registry, schema, access receipt or independent validator is introduced.

Host document requests retain existing slots, work/HTTP bounds, rejected-source
rules, DOI/title checks, locator cooldown and ordinary source/archive admission.
A readable publisher HTML response may still have an accessible PDF: try the same
public PDF path within the remaining budget, preserving valid HTML if it fails.
The returned media type always records what was actually obtained. Metadata URLs,
OA flags and successful page navigation do not establish a downloaded document.

Preparation still offers the requested manual login opportunity first. On sample
completion, the same public PDF path gets at most 25 seconds and no more than half
the remaining acquisition time; then the existing browser uses the remainder.
Only the first acquisition attempts public discovery; browser polling does not
repeat it. Ordinary transient status adds source URL without query/fragment, media
type, source route and page count before discarding sample bytes. These observations
are not persisted access proof and never change provider preferences or admission.

Fixed catalog samples measure automatic acquisition: challenge/sign-in results
finish that sample and remove it from the disposable queue before progressing.
They do not silently continue background CAPTCHA retries or ask for a per-paper
manual download. A different explicitly selected sample keeps optional manual
recovery within its original deadline. Program-rendered instructions and runtime
init guidance distinguish public PDF, browser PDF and HTML; public success cannot
be interpreted as verified login or platform-wide subscription.

The integration does not solve publisher access restrictions. Live ACM 403 and
Semantic Scholar 429 remain failures, and the ScienceDirect sample lacks an
accessible public PDF in these checks. No hardcoded author-copy URL, API key,
CAPTCHA bypass or modified paper identity is used to turn the cohort green.
Existing research runs, preparation replies, Windows helper and user browser
remain untouched. See [qualification](../qualification.md#shared-pdf-acquisition-through-host-and-init-2026-10-04)
for actual transfer, Host/reader/archive and fresh preparation evidence.

## Unattended publisher acquisition review (2026-10-04)

This records the earlier standalone stage, superseded by the integration above.
The operator initially changed the development sequence: isolate acquisition
development and tests before returning to init or full research integration. The
repository-only `scripts/probe_literature.py` is the independent experiment entry.
It runs without a model, preparation batch or research database, using existing
GroundingWeb, OA location parsing, Semantic Scholar DOI lookup, optional browser
adapter, Poppler and `validate_document`. Exact DOI metadata discovery and HTTPS
forms of advertised HTTP PDF locations are exercised in the script only. Missing
OpenAlex PDF candidates also permit exact-DOI Crossref PDF links. No production
Host routing or init instructions are changed by this experiment.

Inputs are existing catalog samples, explicit DOIs or a bounded public OpenAlex
keyword search. A supplied URL is labeled separately from discovery. The new
output directory holds ordinary experiment PDFs and a JSON observation report;
it has no admission, recovery or release authority. No schema, digest, access
registry or new research state is added. Repeated rounds preserve both success
and failure. Existing PDF/identity validation controls success; HTML/XML, empty
searches and challenge pages cannot pass the PDF check. Request counts and
existing call deadlines bound the work; explicit CDP checks do not prompt, solve
challenges or navigate user-owned tabs. Browser/helper setup remains separate.

Acceptance proceeds through independent acquisition, actual Host document/source
closure, and finally init orchestration. A real site's restriction is a recorded
availability gap, not a reason to relax the validator or claim the whole platform
works. Broader integration remains pending until those results justify it.

The requested outcome is setup-time authentication followed by automatic search
and PDF retrieval, without per-paper CAPTCHA interaction. The normal background
queue already ends authentication/challenge requests as unavailable and shares
the provider's intervention-needed state; the explicit init/manual dialogue is
not an unattended acquisition guarantee. Do not promote the recent manual sample
continuation repair as a solution to publisher automation restrictions.

OpenAlex discovery already supplies multiple OA PDF locations. The configured
Semantic Scholar or Unpaywall locator supplies exact-DOI fallbacks. Keep those
existing paths and ordinary source admission; a second resolver registry, access
receipt or platform-wide success marker would not solve missing bytes. Metadata
OA flags and URLs are leads, not evidence of a successful transfer. The ACM sample
has a public Google Research copy even though the observed OpenAlex record says
closed; that record alone cannot establish that no public copy exists.

The bounded anonymous checks used all four fixed preparation DOIs. Actual PDFs
passed the existing extractor/identity validator for an IEEE university-repository
copy (six pages), an ACM author-institution copy (15 pages, existing first-page
title fallback) and Springer publisher content (32 pages, DOI match). Springer
also returned HTTP 400 on the second request. IEEE publisher and ACM/ScienceDirect
public requests failed with HTTP errors. These are targeted server downloads,
not proof of automatic discovery-to-archive closure, institutional entitlement,
continuous availability or the user's authenticated Windows acquisition path.
IEEE's working repository URL used HTTPS for a metadata location advertised as
HTTP; this experiment did not change production URL discovery or admit HTTP.

One reproducible framework defect blocked valid public downloads before HTTP:
GroundingWeb allowed only eight DNS addresses although the literature downloader's
existing limit is 32. IEEE and Google Storage returned 12 public A/AAAA records.
Reuse `MAX_ADDRESSES_PER_HOST` for GroundingWeb. Every address must still be public;
the connected peer must match the approved set. The finite address count, total
request deadline, redirects, byte bounds and identity checks remain. No new
assurance mechanism or exception for a specific publisher is needed. After this
repair, the Google Research PDF passed the real document validator. This is not
the cause established for the historical Windows `identity_unconfirmed` failures.

Next acceptance should exercise multiple DOI selections through the actual Host
document/reader path, retaining source URL, PDF versus HTML/XML representation,
identity result, unavailable reason and whether intervention occurred. Reuse
existing traces and archives, and include publisher and public-copy successes,
repeated acquisition, and a challenge that ends unavailable without repeated
manual prompts. The current one-paper preparation sample establishes readability
only; it is not that broader qualification. Fresh init acceptance remains the
operator's preference; existing runs and browser state remain untouched.

Primary constraints: [IEEE separates metadata/OA/full-text access](https://developer.ieee.org/docs);
[ACM's OA announcement](https://www.acm.org/diversity-inclusion/equity-through-oa)
does not establish unrestricted automated transport;
[Springer separates metadata and available full text](https://support.springernature.com/en/support/solutions/articles/6000195668-springer-nature-link-api-details).
For ScienceDirect, [clearance can expire or be reevaluated](https://developers.cloudflare.com/cloudflare-challenges/concepts/clearance/)
and [Elsevier directs TDM retrieval to its API](https://www.elsevier.com/about/policies-and-standards/text-and-data-mining/faq).
The existing optional API adapter still needs actual entitlement validation;
an API key or XML response does not prove a PDF can be downloaded. Under the
operator's no-publisher-key preference, retain legitimate public copies and
explicit gaps; do not promise comprehensive unattended ScienceDirect coverage.

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

## Demand-driven acquisition and delivery repair — 2026-10-04

Read-only inspection of the October 4 research run found 49 IEEE queue requests
for 25 URLs, including 15 queue timeouts. Four earlier `document_verified`
events existed even when the current cache showed zero available documents;
those events did not establish PDF media, Agent receipt or archived sources.
Two existing IEEE article tabs exposed a JavaScript PDF control and
`xplGlobal.document.metadata.pdfUrl`, which the adapter ignored. A synthetic
Chromium reproduction returned `no_full_text` before the repair.

Reuse existing authorities and validators:

- Metadata packets supply identities and publicly retrieved originals, without
  scheduling browser work for every missing PDF. Explicit tool selections start
  the existing public/OA/browser path.
- IEEE metadata supplies a viewer candidate only when its origin and article
  number match the selected article. Existing PDF-byte, readable-text and DOI
  checks still decide admission; wrong targets and challenges remain failures.
- Same-run explicit requests for the same URL may reuse automatically acquired
  cached bytes after validating the requesting paper's identity. No cross-run,
  preparation or manual-import sharing; each Attempt retains its own references
  and archive. Both existing cache limits still apply. Eviction prefers finished
  failures over verified documents, without guaranteeing permanent retention.
- The existing MCP wait loop handles pending `web_fetch` results inside one
  native tool call. It reads the Host's remaining acquisition deadline; later
  observations cannot extend the initial wait. Ordinary retrieval returns
  immediately, and old Hosts without this field keep their existing behavior.
  The Host remains responsive, and failed authentication/challenges return gaps.
- Native transcript pairing still determines actual delivery. Host-only download
  diagnostics do not change response identity; intermediate internal pending
  observations are collapsed into the delivered call, retaining lookup diagnostics.
  An unobserved available response remains an error.
- The observer optionally reads the last 1 MiB of the Host's existing local
  notification log outside the Host thread. TUI labels distinguish current
  queue/cache counts from logged validation events. Logs are best effort, may be
  incomplete and do not establish PDF download, unique papers, archives or reads.

No new ledger, durable counter, schema, digest or authority is introduced.
Existing research runs, client helpers and browser sessions are not modified.
Qualification and boundaries are recorded in
[the validation report](../qualification.md#demand-driven-institutional-acquisition-2026-10-04).

## PDF viewer precedence follow-up — 2026-10-04

The user rejects a server graphical desktop as a practical requirement on a
shared machine and questions IEEE PDF readiness. The cancelled research run has
no archived IEEE source; its selected TASLP DOI ends in `no_full_text`. Historical
`document_verified` events omit media and delivery, so they cannot settle this
question. Earlier public IEEE sample successes include repository copies rather
than publisher institutional PDFs.

A new isolated Chromium reproduction exposes a remaining ordering bug: positive
article HTML returns before an identified IEEE HTML PDF viewer is followed. Move
that existing viewer step before the text fallback. Preserve the same deadline,
origin/article-number/DOI validators and challenge behavior; no new retrieval
service or validator. PDF bytes reach the ordinary archive, a challenge remains
unavailable, conflicting DOI fails, and HTML without a PDF remains readable text.
The shared ScienceDirect viewer branch follows the same ordering.

This repairs the reproduced adapter defect, not proof of current Windows
institutional access. The current client connection ended with the research run.
Independent public probing of `10.1109/TASLP.2024.3434495` and
`10.1109/TAFFC.2025.3558222` obtains no PDF this round; the first also encounters
locator HTTP 429. Client-offline operation must retain explicit gaps rather than
promise unrestricted institutional downloading. Server desktop/container
deployment is optional, and no user browser, helper or retained run was changed.

## Same-title arXiv fallback — 2026-10-04

The operator requests an automatic arXiv PDF fallback for the same paper. Reuse
the shared public acquisition function, exact-DOI metadata, existing document
validator, source rejection, work budgets and archive. Search the official Atom
API only after existing PDF candidates fail. Require a normalized exact title,
matching first author and another author where available, no conflicting supplied
DOI, one unambiguous versioned arXiv result and its official PDF link. Downloaded
bytes still pass the ordinary PDF/identity checks. Preserve the actual arXiv
version in the existing version note; this does not establish the publisher's
version of record. Missing metadata, ambiguity, rate limits and timeouts are gaps,
not manual controls or retries beyond the current work window.

Concurrency rationale: independent Host/probe processes can otherwise issue API
requests concurrently, exceeding the official single-connection/three-second
limit and losing the public fallback. The affected boundary is HTTP admission to
one external service, not research state or source authority. Git, atomic writes,
types and per-Host semaphores cannot serialize independent live processes; ordinary
OS advisory locking is sufficient. Use one empty, readable Linux `/tmp` lock file,
hold the lock across each API request and wait three seconds before sending. This
adds no ledger, receipt, schema or stored timing authority. Kernel lock release
handles process exit; never unlink the shared inode. Maintenance is one bounded
lock helper, removable when requests use a shared outbound scheduler. Coordination
covers processes/users sharing that host's `/tmp`, not separate hosts/containers;
operators deploying those must coordinate their combined arXiv traffic as required
by the [official terms](https://info.arxiv.org/help/api/tou.html).

## Public PDF timeout recovery — 2026-10-04

The preceding TASLP probe found the correct arXiv version but twice timed out.
Before changing code, a staged production-transport observation now resolves DNS
in 0.047 seconds or less, completes TLS and receives headers in under 0.4 seconds,
and downloads all 4,260,599 bytes in about one second. The unchanged full DOI path
also succeeds in 9.363 seconds and validates 15 pages. These observations support
a transient network/service condition, not a permanent PDF or identity defect;
the old generic timeout record cannot identify its historical network phase.

Two regressions reproduce the framework limitation: one temporary PDF timeout
discards the candidate despite remaining time/HTTP budget, and DNS timeout loses
its phase. Reuse the existing exception and download trace to retain fixed network
phase labels and received-byte counts without exporting exception messages or
partial content. A selected-paper public acquisition may retry a timed-out PDF
once in total, after 0.2 seconds, only with remaining HTTP budget and more than
one second of its original deadline. Metadata lookup is not replayed. Known HTTP
errors, rate limits, challenges, invalid PDFs and identity failures are not retried.
Each retry starts a fresh response; partial bytes never reach validation/archive.
No overall timeout, browser wait, source validator or persistent authority changes.

## Publisher challenges and optional Semantic Scholar credentials — 2026-10-05

Saved anonymous requests to ACM and ScienceDirect both returned HTTP 403 with
`Cf-Mitigated: challenge`. The ScienceDirect response was approximately 1.2 MB;
its verification text appeared beyond the first 100 KB inspected by the original
experiment. Offline replay through the production reader confirmed the response
header was discarded and the complete challenge body was read before reporting
a generic HTTP error. Separately, Semantic Scholar returned five complete 429
responses despite low-frequency requests, including on the selected proxy node.
These establish challenge handling and locator availability problems, not absent
subscription rights or a publisher-wide PDF failure.

The repair keeps ordinary source admission, publication and recovery boundaries:

- Recognize the explicit Cloudflare challenge header in both public HTTP readers
  before reading the response body. Close the connection, retain the original
  HTTP status and a fixed challenge reason in the existing exception/download
  trace. Plain 403 remains an HTTP failure. Do not store arbitrary HTTP headers,
  replay a challenge, import cookies or change the DOI/PDF validator. Other public
  sources and an independently authenticated browser retain their existing paths.
- Keep one Semantic Scholar locator for a browser preparation backend instead
  of constructing one per sample. The locator owns its bounded request spacing
  and server cooldown; init and runtime use the same implementation. Preserve
  the runtime's batch caps and 60-second fallback cooldown, honoring a longer
  Retry-After through the existing response/error fields. Cooldown returns an
  unavailable lookup promptly; it does not spend the acquisition deadline waiting
  for quota or extend it. Short request-spacing waits use the remaining budget.
- Support an optional `literature.semantic_scholar_api_key` in the existing
  private Profile and `SEMANTIC_SCHOLAR_API_KEY` in the existing legacy credentials
  file. Reuse projection, mode-0600 validation and Host-only rereads. Never expose
  the literal in generated commands, run configurations, diagnostics, Agents or
  client helpers. Send `x-api-key` only to the canonical Semantic Scholar API;
  absence of a configured source keeps anonymous compatibility. A configured
  source that becomes unreadable or loses its key fails that lookup safely.
- Space locator requests at one request per second per instance, including
  concurrent callers. An external key shared across Hosts or machines can still
  be limited by the provider; handle its 429 and continue existing fallbacks.
  Do not add a cross-process credential registry, persistent negative cache or
  new recovery authority to address a failure not demonstrated here.

No new schema, receipt, hash, marker or admission gate is required. Existing
exception types, volatile locks/timing and private credential references are
sufficient. This change does not introduce literature proxy routing or a broad
author-site crawler. The measured IEEE proxy benefit and automatic discovery of
additional author copies remain separate work. ACM/ScienceDirect challenges can
still prevent publisher downloads; under unattended operation these are explicit
full-text gaps with bounded public fallback, never a request for recurring human
CAPTCHA input. The existing optional Elsevier API remains unchanged and still
requires article entitlement; XML does not become a PDF claim.

Validation covers early response closure, ordinary 403, downstream OA success,
quota cooldown/recovery, deadlines and concurrency, canonical-host credentials,
private projection and init/runtime wiring. Native Host/CC/reader/CPU closure
and ordinary archives must preserve real retrieved bytes and failed-source
observations. Use isolated fixtures; retained research runs and Windows browsers
remain untouched. Qualification records distinguish these repairs from actual
publisher authorization or long-term API availability.
