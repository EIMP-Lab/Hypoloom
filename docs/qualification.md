# Qualification: G0–G5

English · [简体中文](qualification.zh-CN.md)

## Publisher challenges and Semantic Scholar recovery (2026-10-05)

The preceding direct/proxy observations retained eight ACM/ScienceDirect challenge
responses and five Semantic Scholar 429s under
`.local/analysis/literature-proxy-20261005/`. The ScienceDirect challenge body was
1,207,846 bytes, with verification text beyond the original 100 KB inspection.
The explicit `Cf-Mitigated` header provides a deterministic early failure boundary;
this does not establish missing subscription rights or universal publisher failure.

Both public HTTP readers now close challenge responses before reading the body.
Existing download events retain `PDF_CHALLENGE` and HTTP status, including the
initial explicit URL when a later OA copy succeeds. Plain 403 remains separate.
No same-call challenge retry or new persistent failure cache is added. The existing
public-copy identity checks, arXiv fallback, archive and bounded acquisition remain.

Semantic Scholar serializes requests at one per second per locator, with deadline
bounded queue/spacing waits and no hidden HTTP retry. Init reuses its locator;
the Host shares its literature locator with browser acquisition. Both reuse
429 cooldown and honor a longer Retry-After. Optional S2 credentials use the
existing private Profile/dotenv source and admission projection. First/retry
preparation commands carry paths only; a configured key is reread and source loss
fails safely. Anonymous startup and explicit Unpaywall selection remain supported.
Independent Hosts sharing a key are still subject to the provider's total quota.

The isolated native CC/Host sample combines two challenged URLs, one S2 429 and
a successful matching arXiv PDF. Three real readers receive the document and the
parent completes a CPU experiment. Independent SQL/process/archive inspection
counts **18 successful Agent Attempts and one successful Process**, zero open
Attempts/live groups/paper staging, and four identical readable archived PDF
copies (parent plus readers). No institutional human control is submitted; the
same locator performs no second request during cooldown. The existing Oracle passes.

Focused compatibility selection: **656 passed, 76 optional-environment skips**.
The separate documentation/Profile/operator selection passes **44 tests**.
The original Skill entry had 261 lines against its existing `<260` test; preserve
all instructions and compact two lines rather than relaxing the assertion.
New red fixtures also initially selected a non-DOI record and a sub-minimum work
window; corrected fixtures retain the DOI-only and minimum-deadline validators.
Wheel build, outside-checkout install and fresh bundle compare **178 files** byte
for byte; installed CLI/MCP help and the new API/enum import successfully.

The initial serial full-suite invocation was deliberately interrupted after 939
passes in 2,913 seconds to reschedule the same full selection with the repository's
existing eight-worker approach and a short temporary root. Its record remains
`g1.log`/`g1.xml`; no test process tied to that temporary root remained after
normal interruption cleanup. This partial invocation is not a full G1 pass and
its repeated identities are not added to the final full-suite count.

The first parallel attempt (`g1-parallel.*`, 1,349 passed / five failed before
rescheduling) exposed five existing Elsevier test failures, reproduced unchanged
on a separate `f628678` baseline export (30 passed / five failed). Its imported
fixture selected Nature only, and its public-call expectations predated exact-DOI
OpenAlex/Crossref discovery. The local Elsevier fixture now explicitly selects
ScienceDirect and asserts those exact metadata URLs. API call counts, zero-browser
behavior, public-only's zero API calls, full-text validation/archive and reuse
assertions are retained. Elsevier plus public fallback passes **47 tests**; no
production access policy changed. The final full invocation uses eight workers,
`--maxschedchunk=2` and a short temporary root, retaining the complete selection.

That full invocation (`g1-balanced.*`) completed all **7,180 identities** in
1,666.53 seconds: **7,059 passed, 11 failed, 110 skipped**. Ten failures also
reproduced on the unchanged `f628678` baseline: seven institutional Web tests
expected Chinese despite the current English default, and three extracted-renderer
Node tests omitted the existing translation helper. Test-only corrections retain
authorization, read-only behavior, payload identity, forwarding and raw-value
assertions; focused reruns pass all ten. Reconciliation by test identity therefore
gives **7,069 passing, 110 skipped and one unresolved baseline failure**, not a
green full G1 invocation. No process tied to the full-suite temporary root remains.

The remaining native model-usage test fails with `KeyError:
provider_usage_normalization` on both candidate and unchanged `f628678`, with the
five relevant source/test files identical. Claude Code 2.1.289 reports outer input
54 versus model/wire input 29, plus output 11; the existing adapter conservatively
retains 65 total tokens rather than 40. This is the previously recorded fallback
accounting compatibility issue. Both fixtures reach COMPLETED/revision 18 with
11 successful Agents, one expected rejected Agent and one successful CPU Process;
all 13 recorded process groups are gone. It is disclosed and left outside this
publisher repair; no accounting assertion or production behavior was weakened.
`accounting-*.json`, baseline XML/logs, `literature-ui-*.xml`, `web-*.xml` and
`g1-reconciled.json` retain the comparison and focused resolution evidence.

Evidence: `.local/worktrees/literature-access-repair-20261005/.local/qualification/`
`literature-access-repair-20261005/`, with earlier red/green S2 evidence in sibling
`semantic-scholar-recovery-20261005/`. Native inputs use isolated HTTP fixtures
and a local scripted model provider. No paid model, live publisher authorization,
Windows/SSO, GPU or long-duration service guarantee is claimed. Existing runs,
client/browser, local secrets and Clash configuration remain unchanged. The
candidate is developed separately from pre-existing GPU/Web/Telegram edits.

## Publisher selection and English TUI (2026-10-05)

The full routing catalog previously defaulted every omitted publisher to auto,
while preparation selection was not carried into Host provider modes. Reuse the
existing mode map and validators: selected platforms carry auto unless overridden,
unselected publishers default to disabled, and explicit public_only remains
distinct. Public/OA retrieval is independent of institutional browsing. Preparation,
retry, generated launch arguments, durable controls and Web observations agree.
No new storage, authority or run migration is introduced.

All institutional TUI labels, status text and help are English. Read-only refresh,
Close and Esc remain. Visual inspection found English column expansion crowding
the last column at 80 columns; proportional columns and a compact queue heading
repair it. Installed panels were checked at 80x24, 100x34 and 237x52.

Final targeted selections cover **380 distinct passing tests**, with **18 optional
environment skips**, including two native CC/Host/three-reader/CPU closures.
Each native closure independently has 18 successful Agent Attempts and one
successful Process, zero open Attempts, live process groups or staged papers.
The public-route fixture explicitly submits one public_only setup control; the
browser-route fixture submits none. Neither needs a human answer. These are
isolated providers/documents, not new Windows/SSO or real publisher qualification.

Initial fixture failures exposed old implicit opt-ins/empty-mode assertions and
a missing test-helper import; fixtures now declare their chosen routes. The new
Web test initially read headers from the generic request ledger instead of its
body; scoped request-body capture verifies explicit enablement without relaxing
the fixture's read-only traffic check. Final reruns pass.

Wheel, isolated installation and a fresh bundle match **176 package files**.
Evidence, initial failures, final reruns, native census and screenshots remain in
`.local/qualification/institutional-selection-20261005/`; `final-test-selection.txt`
lists the 398 selected identities. No retained research/preparation root, client,
browser or credentials changed. Updated monitors preserve old Host observations;
new routing defaults apply to new runs. No full G1/G4, paid or GPU promotion claim.

## PR 4 report and patch instructions (2026-10-05)

The user authorizes merging PR #4 (`ab6369c`) into dev (`0e8c488`) after review.
Current-cycle accepted facts already own report admission; Host source capture
already generates `implementation.patch`. The PR clarifies these instructions
and adds cycle/count context to rejected references without changing acceptance.

Review reproduced a mismatch in the proposed Skill: copying its input assessment
list verbatim copies full fact records, but report references allow only
`fact_id`, `hypothesis_id` and `assessment`. Two real Host cycles reject literal
copies. The integration instead projects every current row once, taking the
assessment from `payload.assessment`, and retains the empty-cycle rule. A small
executable example in the packaged Skill demonstrates the existing format;
there is no new production converter, schema or source of authority.

Focused validation: **157 passed** across report admission, rendered instructions,
plugin publication, source/patch capture and Host cycle progression. Eight new
cases cover the packaged example with zero/one/two assessments, historical facts,
full-record copying, omissions and duplicates. The first three fail before the
example is supplied; existing validators already reject the other invalid inputs.
Original review evidence remains in `.local/reviews/pr-4-20261005/`.

Native validation: **three passed**, including installed CC with a local scripted
provider, two CPU experiments, Host restart, next-cycle memory consumption and
final-report publication. Independent census finds 27 successful Agent Attempts,
two successful Processes and one deliberately lost/recovered Agent; both cycle
reports contain only their own assessment and match their Git/archive bytes.
There are no open Attempts or live process groups; Oracle passes. The initial
native run failed a new test assertion that inspected the Skill launch reply
instead of the separate delivered body. Inspecting the actual provider message
corrects that observation without changing runtime behavior or admission.
Documentation/Profile checks: **seven passed**.

Wheel/source/isolated installation/fresh runtime bundle match **176 package
files**; an installed plugin's example passes ordinary report validation for
empty and nonempty inputs. No existing research run or plugin revision is
rewritten. This is focused integration validation, not a full release, paid-model
behavior or GPU qualification. Evidence: `.local/qualification/pr-4-20261005/`.

## Read-only institutional TUI (2026-10-04)

The operator requests a concise observation panel instead of login, retries,
sample checks, PDF import and policy actions. The i panel now formats existing
Host publisher routes, connection observations and bounded queue counts/results.
It does not probe a browser or send controls. Missing Host state clears live rows
and remains unknown; endpoint presence and configured routes are not entitlement.
Public downloads and historical totals are explicitly outside these queue counts.
Automatic refresh, a fixed Close button and Esc remain; optional authorized Web
controls and their validators are unchanged.

**171 distinct related tests pass** across the final selection and rerun. The
final selection had 170 passes and one AF_UNIX pathname-length failure caused by
placing fixtures under the long qualification directory. That unchanged test
passes under normal short pytest paths; a targeted rerun also checks the final
nine-provider layouts. Evidence preserves both outcomes. Native Textual pilots
cover 80x24, 100x34 and 237x52, scrolling, both exit actions, live/offline updates
and former login/retry keys. Actual Host STATUS supplies configured modes and a
verified document; observing/closing leaves database row counts and browser calls
unchanged, with no LITERATURE_ACCESS command.

Wheel/source/install/fresh runtime bundle match **175 package files**. Installed
read-only panel and offline clearing/return smoke pass, along with static checks.
No retained Host/run/client/browser changes, real Windows login, paid/GPU or full
G1 qualification are claimed for this UI change.
Evidence: `.local/qualification/institutional-tui-status-20261004/`.
[Operator guide](guides/institutional-access.md#during-research).

## Unattended preparation failure policy (2026-10-04)

The user prioritizes unattended stability. Explicit
`--institutional-prepare-failure-mode public_only` converts failed/unvisited
platforms into existing launch preferences. Actual failures, sample observations
and nonzero exits remain; default review and final launch confirmation remain.
Known failed platforms can be public-only before preparation. No publisher blacklist,
new persistence or changes to retained research/preparation/browser state.

Related selections: **358 passed, 18 skipped** (optional PowerShell, browser and
SSH environment checks); separate native CC/Host case: **1 passed**. Tests cover
mixed results, connection failure, interruption, socket replies and cleanup,
inspect/prepare/launch/serve argument handoff, public fallback, identity rejection
and existing finite queues/deadlines. Initial test defects in compact-command
inspection were corrected without weakening the production checks.

The native case feeds a real preparation challenge result's modes into a new
isolated Host. Public metadata/PDF acquisition, three concurrent reader processes,
original-source archives and CPU experiment collection complete without browser
calls or a runtime policy answer. Independent read-only counting finds **18 Agent
and one Process** attempts closed successfully, **19 process groups no longer live**,
zero staging residue and zero `LITERATURE_ACCESS` commands. The existing Oracle is
clean. It uses the installed CC executable and a local scripted provider with
synthetic documents, not paid models or live publisher entitlement.

Wheel/source/install/fresh bundle match **175 package files**. Installed agent
preparation applies the policy to unfinished platforms and cleans its private
control/connection resources; MCP initialization passes. Static checks pass.
No full G1/release, long soak, Windows SSO or complete publisher coverage is claimed.
Evidence: `.local/qualification/unattended-preparation-20261004/`.
[Fresh-init instructions](guides/institutional-access.md#fresh-init-acceptance).

## Shared PDF acquisition through Host and init (2026-10-04)

The operator authorized proceeding beyond the standalone experiment to fresh init
acceptance. The runner, Host public fallback and init probes now use one production
exact-DOI/PDF acquisition path. Red cases reproduced a missing Host metadata route
and readable publisher HTML preventing an available PDF from being fetched. The
same validators, source rejection, HTTP/work bounds and source/archive ownership
remain; valid HTML survives a failed attempt to prefer PDF. Default sample
challenges terminate automatically; other explicit samples retain optional manual
recovery. New sample observations distinguish public/browser source and PDF/HTML.

Actual anonymous network observations, without publisher keys or a user browser:

| Check | Observed result |
| --- | --- |
| Shared acquisition, four catalog DOIs, two fresh rounds | IEEE repository and Springer publisher each 2/2 validated PDFs; ACM and ScienceDirect each 0/2; 4/8 overall, exit 2 |
| Shared keyword search | Springer DOI resolved and 32-page PDF saved, exit 0 |
| First real Host source/archive check | IEEE 6-page PDF; Springer readable HTML; ACM/ScienceDirect unavailable |
| Host after PDF-preference repair | Springer 32-page PDF archived with original bytes and source_ref reuse; IEEE unavailable on repeat; ACM/ScienceDirect unavailable |

IEEE's repeated Host failure retains an HTTP 202 from the initial publisher path;
that outward error alone does not establish which later fallback failed. Do not
claim continuous availability from the standalone successes. ACM publisher 403,
missing ScienceDirect public PDF and Semantic Scholar 429 remain live gaps.
Neither CAPTCHA automation nor complete unattended publisher coverage is claimed.

The native isolated Host test uses the real installed CC executable, local scripted
provider, broker/source tools, three concurrent reader processes, original-byte
archives, CPU experiment/collection and the existing Oracle. Both the established
locator path and new public metadata path close normally without a browser call.
It checks terminal Attempts, reader/Process counts, empty staging and process-group
cleanup. Synthetic source bytes do not prove real publisher entitlement. A prior
run of this test correctly rejected source edits made during execution; subsequent
checks keep runtime code fixed. No retained research run or user browser was changed.

Focused tests also cover wrong-paper rejection, source/redirect rejection, HTTPS
candidate conversion, budget expiry before admission, failure preservation, format
reporting and disposal of challenged fixed samples before the next platform.
Installed wheel, CLI preparation/control cleanup, MCP initialization and a fresh
runtime bundle match all 175 package files. These prepare the new-init acceptance
path; native Windows/SSO behavior and paid/GPU/full release qualification are not
claimed. Final related selection: **455 passed**; separate native Host/CC selection:
**2 passed**. Seven saved PDFs from the shared probe/query and Host archives were
independently re-extracted and identity-checked. Static checks and compilation pass.
Evidence: `.local/qualification/literature-init-integration-20261004/`.

[Fresh-init steps](guides/institutional-access.md#fresh-init-acceptance) ·
[Design](design/institutional-literature-access.md#shared-pdf-acquisition-through-host-and-init-2026-10-04).

## Standalone literature acquisition experiment (2026-10-04)

The user now prefers independent acquisition development before Host/init
integration, superseding repeated fresh-init debugging. The repository-only
`scripts/probe_literature.py` reuses the production transport, OA parser, exact-DOI
locator, PDF extractor/validator and optional CDP adapter. It creates only a new
experiment directory, PDFs and an observation report. It does not create a
research run or change init/Host routing. Exact metadata discovery and HTTPS
versions of advertised HTTP PDF locations remain experiment-only behavior.

Actual anonymous network checks, without publisher keys or user browser:

| Selection | Result |
| --- | --- |
| Four catalog DOIs, final two rounds | IEEE repository 2/2 and Springer publisher 2/2 PDFs; ACM 0/2 and ScienceDirect 0/2; overall 4/8, exit 2 |
| Keyword query for the Springer sample, one result | Search → DOI → discovered PDF → saved 32-page PDF with matching DOI, exit 0 |
| ACM DOI with explicit Google Research URL | Saved 15-page PDF with existing first-page title check, labeled `supplied`; not automatic discovery, exit 0 |

ACM automatic discovery reaches its publisher PDF link via Crossref but receives
HTTP 403. ScienceDirect metadata provides no supported public PDF candidate for
this sample. Semantic Scholar returns HTTP 429, with no successful fallback.
A separate bounded public web-search probe timed out; no speculative search
fallback was integrated. An earlier two-round cohort saved only 3/8 PDFs after
an OpenAlex timeout removed IEEE's second-round repository discovery; that failure
is preserved. The final cohort does not establish continuous availability or
comprehensive publisher coverage. No production XML/HTML result is counted as PDF.

Tests exercise discovery, exact downloaded bytes, actual Poppler extraction,
wrong-paper rejection, alternate sources, HTTP/total-work bounds, late results,
repeat-round failure visibility, output preservation and empty/error searches.
Native Chromium/CDP cases verify exact PDF saving, immediate challenge failure,
owned-tab cleanup and preservation of the user's original tab. They use synthetic
publisher responses, not real subscriptions or a Windows client. Broader Host
archive and init integration acceptance remains pending; this experiment is not
a new release gate or a substitute for G0–G5.

Final focused, native browser and documentation/Profile selection: **117 passed**.
Two red cases caught empty queries starting unintended sample/network work; input
validation now rejects them before output creation. Reopening the six final
experiment PDFs independently confirmed actual file counts, byte lengths, parsed
page counts and article identity; no research database was created. Static checks
and compilation pass. No full G1 or paid/resource qualification is claimed.

Evidence: `.local/qualification/standalone-literature-20261004/`.
[Usage](guides/institutional-access.md#independent-acquisition-development).

## Unattended publisher acquisition review (2026-10-04)

Read-only public checks queried Crossref and OpenAlex for the four existing init
DOIs without publisher credentials. Both services returned all four metadata
records. Targeted downloads used the production GroundingWeb transport, Poppler
and institutional document validator, without a retained run or user browser.

| Provider/source | Observed result |
| --- | --- |
| IEEE publisher | 12 public DNS addresses rejected before HTTP; after repair HTTP 502 |
| IEEE Manchester repository | HTTP 403 |
| IEEE Glasgow repository | 6,647,378-byte PDF, six pages, exact first-page DOI |
| ACM publisher | HTTP 403 |
| ACM Google Research copy | 12 public DNS addresses rejected before HTTP; after repair 275,941-byte PDF, 15 pages, existing first-page title validation |
| Springer publisher | First check: 1,477,135-byte PDF, 32 pages, exact DOI; second check: HTTP 400 |
| ScienceDirect publisher | HTTP 403; no automatic PDF success observed |

IEEE's working repository URL was an HTTPS form of an HTTP location in metadata;
ACM's author copy was selected from the official Google Research publication page.
Thus these establish targeted public PDF acquisition, not automatic discovery
coverage, publisher-version equivalence or Host-to-archive closure. No recent
Windows browser identity failure is attributed to these server observations.
Springer's mixed responses are preserved; one success is not stable availability.

The reproduced framework fault was a stale eight-address limit in GroundingWeb,
while literature already allows 32. Both now use the existing constant. Three red
regressions cover rejection of valid sets and failure to reach the peer check;
negative cases preserve rejection of mixed/private DNS, excess addresses and
unapproved peers. Focused downloader/OA/API checks: **206 passed**. Host/document,
institutional access, runtime bundle and documentation/Profile checks:
**65 passed**, disjoint from the first set. An initial selector named a nonexistent
test file and collected no tests; its error is preserved separately. This is
focused qualification, not full G1, native Windows or subscription acceptance.

Evidence: `.local/qualification/publisher-automation-feasibility-20261004/`.
[Design and source constraints](design/institutional-literature-access.md#unattended-publisher-acquisition-review-2026-10-04).

## Preparation sample challenge and identity diagnostics (2026-10-04)

The fresh 14:19 init first timed out waiting for a client. Its operator-requested
retry opened all four platforms: IEEE/ACM identity_unconfirmed, ScienceDirect
verification_required, Springer sample_verified. The operator observes an IEEE PDF,
ACM detail, ScienceDirect PDF after manually passing verification, and Springer
detail. These observations do not establish program acquisition or institutional
entitlement. CC incorrectly explained paper identity as institutional account
identity and assumed full text had been fetched; historical logs cannot locate
the failed identity stage.

Three red tests reproduce lost identity stages and missing fixed result explanations.
Existing safe reasons now distinguish article identity, PDF target and obtained
document validation. The final human-readable explanation precedes the unchanged
JSON summary. Identity admission checks stay strict; no new saved state or schema.

A separate red test reproduces sample verification returning immediately on a
challenge and abandoning the later manual completion. Normal init now uses its
existing dialogue for the retained sample page, capped by the remaining original
acquisition time. Complete resumes the same queue item; skip/public-only/finish
and both timeout bounds remain explicit. Two additional red cases catch abandoned
samples being fetched by later-platform ticks; existing preparation-item removal
now retires them. Earlier sample failures remain visible when a later explicit
platform choice takes precedence. Noninteractive checks retain immediate reporting.

Native Chromium, the private relay/control socket and separate generated CLI watches
exercise four-platform progression including manual completion of a synthetic
challenge, actual PDF bytes/identity validation and resource cleanup. This tests
the handoff, not Cloudflare acceptance. The Windows IEEE/ACM identity failure stage
and real publisher recovery still need a fresh-init test. No retained research,
preparation answer or operator browser was mutated; fresh-init acceptance is now
the operator's requested workflow, separate from first-install evidence.
Final focused tests **246 pass** (including native four-platform/control/watch,
PowerShell fixtures, operator instructions and docs/Profile). The unchanged browser
adapter selection contributes **48 passing tests**, disjoint from that final set.
The preceding combined native/browser run exposed a fixture CDP-event synchronization
error; its corrected challenge case passes in the final set, without weakening
sample or cleanup assertions. Wheel, isolated installation and fresh bundle match
**175 package files**; installed CLI preparation/cleanup and MCP initialize pass.
No full G1, paid, GPU or native Windows publisher qualification is claimed.
Evidence: `.local/qualification/institutional-result-diagnostics-20261004/`.

## Late-client init handoff (2026-10-04)

New MERC-causal-202610041222 preparation opened IEEE at 12:31:18 local time.
The later read-only status showed waiting_for_user with 543 seconds remaining,
but CC had not armed an observer after its 12:28 connection instructions. User
finish at 12:31:53 was accepted at 12:33:07, explicitly ending the batch with all
four platforms not_checked. Earlier maintenance advice to end before retrying
failed to distinguish an old batch from an already fresh one. No automated login
skip or discovery-caused batch termination is established.

The Windows log narrows discovery failure to its 15-second command timeout,
followed by recovery and an empty target list. Current local queries return in
~0.286 seconds; historical Windows SSH/network/remote execution timing cannot be
separated. Existing retained state was inspected only; no restart or deletion.

A red delayed-attachment test demonstrates the short observation gap. The existing
watch now accepts a finite positive wait limit up to 3600 seconds; generated argv
uses that limit for one background observer, exiting early at a user-action stage
or batch end. Direct watch still defaults to 30 seconds. Runtime instructions arm
the observer immediately after connection instructions and after platform replies;
explicit finish is still cancellation, never an implicit restart. No new state
authority, notification channel or preparation deadline is introduced.

Tests cover late attachment without a connection reply, observation expiry with
unchanged manual state, invalid limits before socket access and explicit finish
cleanup. Real Chromium/relay/private-control preparation uses separate CLI observer
processes for all four platform prompts, with actual sample checks, late connection,
user-tab retention and owned resource cleanup. This validates program delivery,
not a guarantee that every model follows its runtime instructions. Real Windows
latency and publisher subscription remain outside these synthetic tests.
Related focused regressions **129 pass**, operator skill contracts **37 pass** and
docs/Profile checks **7 pass**. The final wheel, isolated installation and fresh
bundle match **175 package files**; installed CLI preparation/control cleanup and
MCP initialization pass. This focused repair did not run full G1, paid or GPU
qualification and does not claim those gates.
Evidence: `.local/qualification/institutional-init-handoff-20261004/`.

## Publisher preparation entry (2026-10-04)

The operator confirms Windows now opens successive publisher pages after the
discovery recovery update. Retry3 records IEEE identity_unconfirmed, ScienceDirect
verification_required and Springer opened: flow progression is not full-text
verification. The reported Springer login-entry problem is reproduced by two red
tests showing that both catalog and browser still selected the generic homepage.
The official support page links directly to the institution finder.

Catalog, queue, status and browser now share one entry resolver; Springer uses
that official public URL. Library overrides and existing tabs retain precedence.
The authentication domain is not added to article admission; samples pointing to
it are rejected. Fixed instructions explain institution search and the distinction
between ScienceDirect article access and a PDF challenge. Existing challenge
preservation, manual recovery, full-text identity and acquisition limits remain.

Focused preparation/browser/access/instructions/native selection: **156 passed**;
documentation/Profile: **7 passed**. Real Chromium tests cover default and library entries, exact navigation counts,
existing-tab reuse and no false login result. Native four-platform tests keep
authentication and article origins separate. Installed wheel/CLI preparation
cleanup/MCP/fresh bundle checks pass with **175 equal package files**. Operator
Windows confirmation applies to the prior discovery fix, not this new entry or
publisher entitlement. No live preparation answer, retained run or browser is
mutated; the IEEE identity gap and the actual PDF challenge cause remain unresolved.
This is focused repair qualification, not full G1 or paid/GPU qualification.
Evidence: `.local/qualification/institutional-publisher-entry-20261004/`.

## Windows discovery recovery (2026-10-04)

The operator's foreground helper printed run_connection_started followed by
run_discovery_unavailable and exit 2. A red real-PowerShell regression reproduces
the old three-listing-failure exit, which closes all forwards in finally. Edge
can remain usable throughout. Three current server-local discovery queries return
normally in approximately 0.28 seconds; the historical underlying query failure
cannot be distinguished from the old aggregated error.

The long-lived helper now backs off at 5/10/20/30 seconds while preserving existing
independently watched forwards. Failed listings authorize no new connections;
fresh successful lists reset the backoff and retire ended targets. Per-query limits,
platform deadlines, per-forward retry limits and ownership checks remain. Safe
codes distinguish timeout, SSH exit 255, remote command failure and invalid
response; unchanged capped failures do not flood output. Unknown faults still
exit with owned-child cleanup. No new persistent connection authority is added.

Focused connection/runtime/instructions/preparation/dialogue/native selection:
**136 passed, no skips**. PowerShell tests cover six consecutive failures, capped
backoff/reset, fresh retirement, an independently ended forward, initial outage,
unknown-fault cleanup, valid/invalid responses and safe diagnostics. An isolated
real SSH service with ephemeral keys exercises the shipped discovery loop: after
four injected list failures, CDP still responds through the original forward;
recovery creates no duplicate, and stopping cleans the owned SSH connection.
Existing real SSH reconnect/terminal-watch and native Chromium preparation checks
also pass. Windows browser APIs are substituted in these Linux PowerShell tests.

Documentation/Profile: **7 passed**. Wheel/isolated install/CLI preparation cleanup,
MCP initialize and fresh bundle checks match **175 package files**. The operator
subsequently confirms Windows publisher-page progression (above); actual publisher
full-text success remains separately unverified.
No retained research run, live preparation reply, browser profile or Windows task
was changed here. This is focused repair qualification, not full G1/promotion.
Evidence: `.local/qualification/institutional-discovery-recovery-20261004/`.

## Sample-stage disconnect diagnostics (2026-10-04)

Fresh init MERC-causal-202610041002 opened IEEE at 10:11 local time. Its first
sample check returned generic browser_unavailable; IEEE, ScienceDirect, Springer
and ACM eventually timed out. The operator confirms Edge/IEEE remained usable
but the Windows task (last started 10:10:50) is Ready with LastTaskResult 2.
This initially established helper exit, not its specific cause. Subsequent foreground
discovery evidence and repair are recorded above; this earlier change did not modify
the browser, helper, research run or preparation answers.

Ten red cases reproduce lost connection/page reasons in fetch. The adapter now
reuses the existing connection and page classifiers; the queue still filters safe
codes and applies its original finite retry/deadline behavior. An integration
regression verifies the default DOI check emits cdp_endpoint_unavailable, remains
acquisition_timeout and stays in the generated retry command, without raw exception
text or a readability claim. Fixed text tells an already authenticated user to
complete directly. Runtime guidance addresses observed stale connection replies
and CC's incorrect `timeout 30` wrapper around a watch that emits at its own deadline.

Focused navigation/preparation/instructions/browser/access/native/runtime tests:
**227 passed / 1 skipped**. This includes native Chromium/PDF/relay progression and
cleanup, along with actual PowerShell instruction checks. Documentation/Profile:
**7 passed**. Isolated wheel, CLI preparation/cleanup, MCP and fresh runtime bundle
checks pass with **175 equal package files**. No new state authority, schema or
recovery gate; no full G1/promotion claim. This is a diagnostic/interaction repair,
not evidence that the operator's Windows helper exit has been fixed.

Evidence: `.local/qualification/institutional-fetch-diagnostics-20261004/` retains
the source-log observations, Windows facts, red/green tests and installation checks.
The original preparation log remains at
`.local/institutional-prepare-MERC-causal-202610041002.log`.

## Automatic preparation samples (2026-10-04)

Preparation without an explicit DOI previously recorded manual completion as
not_checked, leaving full-text acquisition untested in the ordinary init path.
The existing catalog now supplies one fixed public DOI for IEEE, ScienceDirect,
Springer and optional ACM. Completion invokes the existing browser/document
verifier; it cannot replace the manual authentication opportunity. Explicit samples
override defaults. Skip/public-only/finish avoid default downloads; unrelated
platforms without samples retain unverified completion. No new persistence,
authentication proof, archive, schema or admission requirement is introduced.

Focused preparation/dialogue/browser/control/deadline/navigation selection:
**232 passed**, including native Chromium, PDF extraction, private CDP relay,
four-platform progression after idle and delayed attachment, inter-platform
disconnect recovery, exact sample-download counts and owned resource cleanup.
One additional cancellation regression passes: ending during a default acquisition
prevents opening the next platform and cleans control resources. Final core checks
after the last source adjustment: **69 passed**, overlapping that selection.
Bootstrap inspection: **16 passed / 64 deselected**; docs/Profile: **7 passed**.

Wrong-document identity, access/challenge failures, explicit sample overrides,
retrying failures only, public-only/skip/no-sample behavior and no research archive
are covered. Native responses are synthetic; the fixture uses intercepted JS
navigation, since fulfilling an HTTP redirect did not intercept its destination.
The initial fixture failures are not evidence of a production-browser regression.
The final wheel, isolated install and fresh bundle match **175 package files**;
installed catalog, private preparation/finish cleanup and MCP initialize pass.

Sample DOI/publication identity and public-access selection are supported by the
[publisher sources](guides/institutional-access.md#fixed-samples); ACM's DOI/title
was checked against Crossref. A subsequent parallel metadata lookup returned 429
and was not retried. These source checks are not tests of the user's authenticated
Windows browser or school entitlement. Existing runs, client tasks, login profiles
and credentials were not modified. This is focused feature qualification, not full
G1/promotion or paid/GPU qualification. Evidence:
`.local/qualification/institutional-default-samples-20261004/` (focused XML/output,
final core, cancellation, bootstrap/docs and build/install checks).

## Windows browser automatic port selection (2026-10-04)

The user's Windows Edge compatibility report identifies webdriver=true and
automated_browser, with Turnstile 600010; ordinary Edge works. Combined with the
retained headed comparison and browser documentation, this establishes an unwanted
launch side effect of passing debugging port 0. Those implementation checks alone
do not establish every reason for the ScienceDirect challenge; the subsequent
operator confirmation is recorded below.

The connector now resolves automatic port selection through an OS loopback socket
and passes its nonzero port to Chromium. Existing ownership checks still reject a
competing listener after release. Live process command lines supply reusable ports;
the obsolete DevToolsActivePort reader is removed. Owned old port-0 browsers report
that the dedicated window needs a manual restart, preserving login files and open
windows. No research/preparation state, credentials, browser DOM properties or
site verification scripts are changed. No additional persistent authority is added.

A red PowerShell regression reproduces the old zero-port launch. Final focused
selection: **154 passed**, including the real PowerShell parser/runtime, substituted
Windows process/ownership APIs, actual OS socket allocation/release, explicit and
automatic ports, connector restart/browser reuse, browser relaunch, old-process
rejection, unrelated/foreign process and bind-race rejection, bounded failures,
native CDP/private relays, preparation dialogue and multi-platform transitions.

A headed Linux Chrome comparison uses argv emitted by the shipped Ensure-Browser
function. The old launch reports webdriver=true before/after CDP and in a new tab;
the new launch reports false at all three points, with test cookies available.
Both browsers survive CDP detachment. Disposable browser/display/server processes
and profiles are cleaned. The native comparison visits only a local test page;
it neither automates nor establishes passage of a production CAPTCHA.

Documentation/Profile checks: **7 passed**. The final wheel, isolated installation
and fresh runtime bundle match **175 package files**. Installed preparation/control
cleanup, MCP initialization and runtime identity pass without creating a research
database. Whitespace checks pass; native PowerShell execution parses the shipped
script and fixtures. These checks reuse existing package/runtime validators.

Evidence: `.local/qualification/institutional-browser-port-20261004/` retains the
red result, focused output, native argv generation/comparison and cleanup, and
build/install checks. This is focused repair validation, not full G1,
formal promotion or paid/GPU qualification.

After receiving the update/restart steps for commit `397f151`, the user reports
“这次正常通过” (verification passes this time). This closes the reported verification
loop for that operator's environment at this check. No new diagnostic export or
paper-download result was supplied, so this does not independently establish the
post-update webdriver value, institutional SSO/subscription entitlement, PDF
retrieval, or reliability across other browsers/sites and later sessions.
[Design](design/institutional-access-preparation.md#windows-自动调试端口的启动副作用2026-10-04).

## Elsevier official article API (2026-10-04)

The user supplied an API key after repeated ScienceDirect browser challenges.
This work neither persists nor sends that credential. The optional Host adapter
reuses private Profile projection, existing bounded HTTPS/PDF validation and source
admission. Init omits default ScienceDirect browser preparation when configured,
without claiming subscription access; explicit browser choices stay intact.
API failures retain OA fallback and a non-running acquisition item for manual PDF
import, rather than automatically opening the challenge page again. Retained
research/preparation state and user Profiles are unchanged.

Related regression selection: **400 passed**. Final changed-boundary selection:
**113 passed**, covering PDF/XML API responses, real Poppler extraction, private
Profile projection, original credential rereads, authentication/entitlement/quota
failures, redirect header isolation, unsafe DNS, size/deadline/request bounds,
DOI/PII mismatch, abstract/challenge rejection, OA recovery, public-only choices
and API denial → operator import → document reuse. Counts overlap. Tests use
synthetic credentials; a real Host/broker call admits a document, reuses it and
completes a paper-reader child. Existing fixture cleanup closes those isolated
Hosts and unfinished fixture Attempts. No paid/GPU/scientific-efficacy claim.

The official public full-text XML example parses through the real adapter's
identity/body checks and yields 25,823 characters of extracted text. It is
protocol evidence, not a test of the user's key or institution. API-provided
XML is labeled as extracted text with figure/formatting limits, not physical
PDF pages. No real entitlement or CAPTCHA-resolution claim is made.

Documentation/operator checks: **41 passed**. Compile, undefined-name/duplicate-key
lint and whitespace checks pass. Final wheel, isolated installation and fresh
runtime bundle match **175 package files**. Installed preparation/control cleanup,
MCP initialization and bundle identity pass; no research database is created by
that installation check. This is focused feature validation, not full G1.

Evidence: `.local/qualification/elsevier-api-20261004/` includes focused and related
test outputs, the initial invalid test-path invocation and corrected selection,
the initial import-fixture path-type failure and corrected 113-case closure,
`official-example.json`, build/install logs and final installation verification.
[Design](design/institutional-literature-access.md#elsevier-official-article-api-2026-10-04).

## Preparation challenge instructions (2026-10-03)

The user reports repeated ScienceDirect verification in dedicated Windows Edge,
one successful challenge in an ordinary browser, and confirms actual launch with
`--remote-debugging-port=0`. Isolated headed Chrome 149 under locally extracted
Xvfb shows webdriver false for ordinary/nonzero-port launches and true for port 0,
already before Playwright 1.63 attachment. Attachment preserves that value and
test cookies. This does not reproduce the Windows CAPTCHA outcome or establish
the publisher's reason; no launch flags, credentials or retained preparations were
changed. Evidence stays in `.local/qualification/institutional-browser-comparison-20261003/`.
An initial synthetic-challenge route matched only the www hostname and did not
isolate the actual entry; its invalid result is retained in `challenge-first-attempt.json`.
The corrected all-request interception observes exactly one navigation to a 403
challenge, then same-page opened without a challenge reason despite the existing
inspector detecting verification. Isolated browser/display/server processes and
temporary profiles are cleaned; `cleanup.json` records no matching live processes.

Six red regressions reproduce missing browser and dialogue observations. The fix
reuses the existing inspector and safe reason whitelist: HTTP 200/403/429/503 with
positive challenge evidence retain the same page and report verification_required
at opening; negative HTTP evidence keeps its original failure. Reinspection after
synthetic challenge removal clears the reason without another navigation. Fixed
instructions offer manual handling/skip; skip advances to Springer, while complete
without a sample stays not_checked. No new state authority or timeout is added.

Focused selection: **143 passed / nine PowerShell skips**; an additional browser,
native dialogue, access and deadline selection: **64 passed**. All nine skipped
PowerShell cases subsequently pass using the available Linux PowerShell runtime
and existing Windows API substitutes. Documentation/operator checks: **41 passed**.
Real Chrome/CDP/private-control dialogue verifies challenge instructions, explicit
sample verification and preservation of the user's test tab; owned resources are
closed without a research database. The wheel, isolated installation and fresh
runtime bundle match **174 package files**; installed CLI/control cleanup and MCP
initialization pass. Repair evidence stays in
`.local/qualification/institutional-challenge-instructions-20261003/`.
This is focused repair validation, not full G1, Windows desktop/SSO qualification
or evidence that ScienceDirect verification loops have been resolved.

## Platform browser failure diagnostics (2026-10-03)

Read-only evidence from CC session `90fcf773-1338-40b2-a06b-fc5fd5ec094c`, batch
`institutional-prepare-opu2ja9s`, shows IEEE opened/manual completion (not_checked,
no sample), then ScienceDirect and Springer browser_unavailable/acquisition_timeout.
The user confirms dedicated Edge remained open. The original output has no detailed
exception cause; it cannot establish browser/SSH exit or a subscription failure.
Remaining preparation correctly selects only the failed two platforms. No retained
preparation or research run was answered, started, stopped or otherwise changed.

Ten red regressions reproduce lost platform connection/page failures. Prepare now
uses the existing CDP classifier and fixed page-operation classifications, filtered
through the existing queue whitelist. Unknown raw reasons and private exception
text remain excluded. One controlled real Chromium/CDP relay test drops active
control sockets after IEEE and temporarily rejects reconnects, then restores the
transport; ScienceDirect/Springer automatically proceed with original deadlines.
The external browser/user page survives, owned tabs/control/relay are cleaned and
no research database is created. This is not a reproduction of the Windows fault.

The first expanded selection produced **205 pass / one failure**: a prior native
late-connection test expected the actual 503 but received cdp_timeout. An unchanged
isolated recheck reproduced it. A deterministic regression then demonstrated local
budget exhaustion before the next request overwriting the prior endpoint cause.
That explicit local-deadline case now preserves the last actual failure. A paired
case still requires a real request timeout to return cdp_timeout. No timeout,
assertion or admission rule was relaxed. Final related selection: **208 passed**,
covering navigation, preparation, access/deadlines, dialogue/instructions, native
browser/subprocess, documentation and operator guidance; no full G1 claim.

The final wheel, isolated install and fresh runtime bundle match **174 package
files**. Installed CLI preparation/control cleanup and MCP initialization pass.
No native Windows desktop/Task Scheduler/SSO, paid/GPU or underlying incident-fix
claim: the next user-selected retry must supply the newly available reason codes.

Evidence: `.local/qualification/institutional-platform-diagnostics-20261003/` keeps
`observed.json`, `red.txt`, `first-fixed.txt`, `focused.txt`, `native-recheck.txt`,
`deadline-red.txt`, `final-focused.txt`, build/install logs, and installation
verification scripts/results. Original private CC evidence remains at its source.
Design: [platform-stage diagnostics](design/institutional-access-preparation.md#平台阶段的控制连接与页面错误分类2026-10-03).

## File-based Windows client setup (2026-10-03)

Retained CC session `e7e522c0-f827-4d08-8ab2-ed0df5f5ae6b` contains two correct tool
outputs followed by assistant text changing `-ErrorAction SilentlyContinue` to
`-ErrorAction -SilentlyContinue`. Actual PowerShell execution returns 0 for the
original and 1 for the transcription. Existing verbatim instructions did not prevent
this failure. Only sanitized parameter/timestamp evidence is copied into local results.

The existing connector now owns interactive Setup, path derivation, prerequisite
checks, original-byte installation and Connect dispatch. Bootstrap/live views expose
file location, fixed launch and values; an old live inline payload is never rendered
as installation source. No new installer authority, digest or saved configuration.

Initial focused **99 passed**; expanded related **213 passed / four opt-in browser
skips**, covering instructions, connection, dialogue, preparation/native subprocess,
bootstrap, documentation and operator guidance. A preceding collection attempt used
an incorrect documentation-test path and ran no tests; its output is retained.
The final prompt-validation adjustment was rechecked through the instruction,
PowerShell, documentation and operator selections. Counts overlap, not a full G1.

Real PowerShell executes the shipped Setup functions with only input and external
connection effects substituted. Cases cover default/custom/supplied paths, metacharacter
preservation, failed authentication, invalid target/path, interrupted replacement,
Connect failure and execution from the installed path. Failures preserve the old entry
where required and remove temporary installation files; success invokes Connect only.
A disposable real OpenSSH SFTP server transfers the actual source file; the download
and installed copy match the source bytes. Ephemeral keys/configuration are removed
and sshd exits; the user's SSH keys and Windows/browser state are not touched.

The final wheel, isolated install and fresh bundle match **174 package files**. The
installed entry points at its actual installed script; installed CLI preparation,
private control answer/cleanup and MCP initialization pass. The disposable preparation
creates no research database and leaves no owned control/forward resources. No retained
preparation or research run was answered, restarted, migrated or otherwise changed.
Actual Windows Task Scheduler/ACL/visible desktop/SSO still need native verification;
Linux PowerShell with substituted Windows effects is not that claim. No paid/GPU or
frozen-candidate qualification was performed.

Evidence: `.local/qualification/institutional-file-setup-20261003/` contains
`transcription.json`, `focused-initial.txt`, the collection-error `focused.txt`,
`focused-final.txt`, `final-checks.txt`, `final-transfer.txt`, `verify_transfer.py`,
final build/install logs and `final-installation.json` with its verifier.
Design: [original-file client setup](design/institutional-access-preparation.md#原始文件接入移除模型转贴程序正文2026-10-03).

## Program-owned institutional connection waiting (2026-10-03)

Read-only inspection of CC session c807d820-dc5c-4583-bd41-388f4ae0611a confirms a
waiting_for_client check, a user-triggered browser_unavailable check, then approximately
six minutes of agent-written status/sleep polling before input_timeout. Those status
calls could not cause another check. The result correctly retains all four platforms
in its retry vector; no platform opened and no research root was created. A subsequent
batch was inspected through its existing control socket only: its client forwarding
socket was absent at that observation. No retained preparation was answered/stopped,
no browser page was controlled, and no existing research run was changed.

Eight initial regressions reproduce the inability to progress when a client becomes
ready without a reply, the lack of a fixed automatic window, and six missing safe
connection classifications. The existing dialogue condition/deadline and single
browser worker now drive retries. Coverage checks that replies/status cannot extend
the deadline, only one check is in flight, timeout preserves the last reason, and a
finish wins over late readiness without opening a platform. The generated watch uses
a wall-clock bound, issues only status reads and never submits an answer. A final
boundary correction avoids starting a new watch request after its deadline. The
initial repaired test caught a test-observation race (backend called before the stage
changed); the fixture now observes the actual user-action stage without changing the
production transition. No validator/deadline assertion was weakened.

Focused selection: **322 passed / one isolated-SSH opt-in skipped** across preparation,
access/deadlines, dialogue/instructions, navigation, native runtime/connection,
bootstrap, docs and runtime skill checks. The skipped real SSH case then **passed**
with its opt-in enabled, using ephemeral keys/sshd only. Final watch/entry checks:
**three passed**; final dialogue checks include both initial and automatic-check
cancellation with a late successful Future. Counts overlap and are not a full G1.

Native Chromium/CDP relay preparation covers both immediate and delayed connectivity:
a real 503 first produces cdp_endpoint_unavailable, recovery advances without a retry
reply, and all three synthetic publisher stages proceed after an idle answer wait.
External browser/user tab survive; owned tabs, control root and relay are closed;
no research SQLite database is created. Native systemd/CDP and isolated SSH lifetime
tests also pass in the focused selections. PowerShell runs generated setup and owned
task orchestration with Windows APIs substituted. Actual Windows desktop cold start,
publisher SSO and the original user's underlying connection fault still need client
verification; no paid/GPU or frozen candidate claim.

Evidence: `.local/qualification/institutional-connection-loop-20261003/` contains
`red.txt`, `initial-fixed.txt`, `fixed-dialogue.txt`, `native.txt`, `focused.txt`,
`watch-final.txt`, `final-targeted.txt`, `final-dialogue.txt` and `ssh.txt`.
Design: [program-owned connection waiting](design/institutional-access-preparation.md#程序内连接等待与有界观察2026-10-03).

## Institutional preparation failure retry (2026-10-03)

Retained CC output for institutional-prepare-sv09l416 records IEEE opened/completed,
then ScienceDirect and Springer acquisition_timeout, exit 1 and an empty remaining
vector. The user confirms the dedicated Edge window stayed open. Read-only checks
found neither the proposed MERC-causal-202610031307 research root nor its process;
the preparation root was already cleaned. No retained process/run was changed.

The retry bug reproduces when normal iteration, EOF, finish or input timeout follows
an earlier failure. Six corrected red cases also expose the missing intermediate
diagnostics. An initial test fixture used durations below the public option bounds;
that original failure is retained, then corrected by scaling only the test clock.
Production timing validation and deadlines are unchanged. Fixed tests execute the
generated remaining command and verify only ScienceDirect/Springer reopen, while
IEEE completion, modes/options and supplied failed samples survive. Sanitized error
events distinguish browser and navigation failures without exception text or SSO URLs.

Final focused **224 passed**: preparation/access/deadlines, init dialogue/instructions,
native preparation, bootstrap, documentation and runtime skill checks. Real PowerShell
executes setup fixtures; isolated real Chromium/CDP relay stays idle beyond its I/O
timeout, then advances through IEEE/ScienceDirect/Springer, preserves the external
browser/user tab and closes its owned tabs, relay and temporary control root. No
research SQLite database is created. This native scenario also passed before the fix:
it does **not** reproduce or establish the cause of the user's Windows popup failure.
The old output contains no intermediate fault code; next Windows attempt is still
needed to identify that fault. No real publisher/SSO, Windows desktop, paid/GPU or new
full G1 claim. This is a focused repair, not a frozen release candidate.

Evidence: `.local/qualification/institutional-preparation-retry-20261003/`:
`red.txt` (initial fixture error), `red-corrected.txt` (six expected failures),
`native-before.txt` (one native pass), `initial-fixed.txt` (35 pass / ten missing-pwsh
skips), `focused.txt` (224 pass with browser and PowerShell enabled). Counts overlap.
Design and current operator behavior: [preparation repair](design/institutional-access-preparation.md#准备失败平台的重试与可见性2026-10-03).

## Windows ACL API semantics correction (2026-10-03)

After running c254d5f, the user's native icacls output loses all inherited `(I)`
flags but retains the same unwanted group's read/execute grant. This confirms a
partial permission repair, not a new SSH or school-authentication failure. The
previous mock cleared IsInherited in memory during SetAccessRuleProtection(true,true).
The .NET implementation only marks the DACL protected; RemoveAccessRuleSpecific
skips inherited ACEs. Correcting those two simulated semantics reproduces the
same browser_profile_must_be_private failure against the unchanged prior code.

The fix removes inherited rules in memory and recreates retained ones with their
original SID, rights, inheritance/propagation flags and allow/deny type. Existing
explicit allowed/deny entries survive. Only the completed ACL is persisted once,
then the original validator rereads it. Tests reject intermediate persistence with
extra access or missing retained rules, preserve mixed explicit/inherited rules,
reject arbitrary explicit access and distinguish custom path, explicit access,
write failure and failed readback. Previously converted explicit residuals are not
automatically adopted as inherited; user-confirmed before/after evidence permits
a one-off grant removal for that identified group on that dedicated directory.

Focused result: **106 passed / one skipped in 50.74 seconds**; the skip requires
real-browser opt-in. Evidence: `.local/qualification/institutional-acl-api-20261003/`
contains `red.xml`, `initial-fixed.xml` and `focused.xml`. PowerShell is real;
final rule-factory and documentation recheck: **five passed in 1.56 seconds**
(`final-focused.xml`). The ACL's own AccessRuleFactory preserves raw rights masks;
no constructor-added access bits are introduced. Native fixture syntax also passes.
Windows APIs remain substituted on this Linux host. A new native Windows fixture,
called by the connector test only on Windows, uses an isolated temporary directory
to check inherited grants, retained deny, child propagation, unchanged parent/data
and repeat idempotence. It has **not been executed here**, so native ACL/GUI/SSO
success is not claimed. No full G1/package promotion/paid run or research mutation.
Earlier evidence remains unchanged; its simulated ACL pass did not establish
Windows API compatibility. API sources and recovery scope are linked in the design.

## Windows browser profile inherited permissions (2026-10-03)

The native follow-up and correction above supersede this initial implementation's
ACL compatibility assumption; the original test evidence is retained below.

The user's foreground Run reports browser_profile_must_be_private. Native icacls
output confirms an additional local group's inherited read/execute grant alongside
the current user, SYSTEM and Administrators. Baseline `b580f04` correctly rejects
that grant, but neither directory creation nor Connect prepares the required ACL.
Repeated task startup cannot repair it. A synthetic ACL fixture executes the real
PowerShell privacy function and reproduces the same rejection before the repair.

Only the owned default browser directory's extra inherited allow rules are repaired.
Existing allowed identities and denies survive inheritance protection; the original
validator rereads the OS ACL after the write. Custom paths, explicit extra grants,
other owners and directory redirects remain rejected. Write failure or an unchanged
readback cannot permit startup. Connect/Init performs preparation after task ownership
validation and before task start. Foreground Run emits idle discovery transitions;
program instructions and bilingual guides explain hidden versus foreground operation.

Final focused result: **106 passed / one skipped in 49.40 seconds**, covering
connection, instructions, init dialogue, operator skills and documentation structure.
The skip requires explicit real-browser opt-in. PowerShell execution covers the
reported inherited grant, new-directory preparation, preserved users/deny, repeat
idempotence, six rejection/write-failure cases, unpolluted CDP return data, preflight
failure without task start, existing 14 task rejection cases, bounded idle messages
and independent forward retirement. Windows filesystem/ACL/scheduler APIs are
substituted on Linux; native ACL propagation, desktop and real SSO still require
client confirmation. Static undefined-name checks and whitespace checks pass.

Evidence: `.local/qualification/institutional-profile-acl-20261003/` retains
`red.xml`, `initial-fixed.xml`, `focused.xml` and the program-generated Windows
setup block. No full G1, package promotion, paid/GPU or native Windows claim. No
retained research run, SSH authorization or real browser profile was changed here;
the user-executed updated connector applies the scoped ACL repair on Windows.
No persistent permission registry, marker or additional source of authority is added.

## Windows task principal normalization (2026-10-03)

The user's Windows readback shows a Ready task whose principal is a short username,
while WindowsIdentity reports the current computer-qualified username. Its program
and old invocation otherwise match the supplied connection. The `c99588e` string
comparison rejects the short form after successful initial registration by SID.
The previous fixture returned a SID unchanged and missed this Windows behavior.

The fix uses NTAccount.Translate to compare the task account with the existing
current-user SID. A raw matching SID still succeeds directly. Lookup errors fail
closed; another domain's same-named account is not accepted by suffix matching.
Executable, action count and complete connection arguments retain their checks.
No identity cache, persistent marker, task deletion or credential change is added.

A real PowerShell fixture first reproduces the old ownership_mismatch. The repaired
fixture accepts SID/short/qualified forms for the same synthetic identity, upgrades
an idle prior task without rewriting its principal, and rejects 14 cases covering
different/unmapped/missing users, another domain's same username, wrong executable,
changed arguments and extra actions under both supported window forms. Final
related validation: **138 passed in 36.49 seconds**; whitespace checks pass.
Evidence: `.local/qualification/institutional-task-user-20261003/red.xml` and
`focused.xml`. OS account lookup and Task Scheduler APIs are substituted on Linux;
native Windows account resolution/GUI/SSO still need client confirmation. No new
full G1, package promotion, paid/GPU or retained-run validation is claimed. Existing
research state, SSH authorization and browser login data remain unchanged.

## Windows client byte transfer and hidden discovery (2026-10-03)

The user observed garbled Chinese with literal Write-Output fragments after
client_discovery_started and a blank PowerShell window. On `7b1e776`, actual
PowerShell native stdout capture under CP936 and Windows-1252 changes a Chinese
script's bytes before Set-Content saves it as UTF-8; the UTF-8 control succeeds.
The baseline rerun records **two failures / four passes**. An earlier test harness
incorrectly decoded all captured console output as UTF-8; its three failures are
retained separately, and the harness now uses the deliberately selected encoding.

Setup transports Base64 through existing SSH and writes decoded bytes, retaining
empty/failure rejection and atomic replacement. Real PowerShell verifies exact
download bytes and executes the downloaded Chinese statements under all three
code pages. Invalid Base64 leaves the existing connector untouched. No hash,
credential record or parallel readiness authority is added.

The task action now requests WindowStyle Hidden. Tests execute the actual task
orchestration with substituted Windows scheduler APIs: create/start; update only
the exact prior invocation; stop/start an old running visible task; repeated
Connect without rewriting/stopping the updated task; preserved triggers/settings;
uninstall; and eight owner/executable/arguments/action-count rejection cases.
An initial mock declared AtLogOn as a value rather than a switch; that fixture
error is fixed without changing the production transition or weakening assertions.

Final related checks: **138 passed in 34.21 seconds**. Undefined-name/duplicate-key
lint and whitespace checks pass. Evidence is retained in
`.local/qualification/institutional-encoding-20261003/`, including `red.xml`,
`baseline.xml`, `initial-fixed.xml`, `focused.xml` and generated `windows-setup.ps1`.
The code-page reproduction is real; Windows scheduler and GUI effects still need
native client confirmation. No new full G1, installed-package promotion, paid/GPU
or SSO claim. Existing research runs, SSH authorization and browser profiles were
not changed on the development host. A user-executed Connect upgrade may briefly
interrupt that old task's connections while restarting it with a hidden window.

## Windows pasted client setup (2026-10-03)

The supplied Windows transcript exposed a command-boundary defect in `0aa190a`:
after a top-level download error, subsequent pasted commands could still write or
launch the connector. The previous file-mode test's outer try block hid this
behavior. Real PowerShell `-Command -` reproduces three failing cases: partial
failed download, empty successful download and malformed target. The partial
download case actually launches the mock connector after the error; empty data
reaches Move-Item, and the shell's ErrorActionPreference is changed globally.

The shared renderer now emits one `& { ... }` command with local preference scope,
keeps download validation ahead of replacement, rejects empty content, and uses
short argv lines. Failed cases preserve an existing connector byte-for-byte, leave
no temporary file, do not launch it, and return to the original shell preference.
SSH prompt/error guidance distinguishes a server target from client loopback and
password login from BatchMode readiness. No automatic authentication changes.

Final related validation: **133 passed in 31.98 seconds**, covering the actual
generated script in PowerShell file/command-input modes, quoted remote paths,
existing connector execution fixtures, bootstrap, runtime instructions and docs.
The three original red results remain in
`.local/qualification/institutional-paste-20261003/`, alongside `instructions.xml`,
`focused.xml` and the generated `windows-setup.ps1`. Windows APIs remain mocked on
Linux; native desktop/Task Scheduler, real SSO and this user's SSH authentication
are not verified. No new full G1, package promotion or paid/GPU qualification;
existing research, profiles and authorization files remain untouched.

## Program-generated institutional setup and replies (2026-10-02)

From `0c5a73a`, the user requested a programmed client setup procedure, fixed
instructions and an executable user-facing block. One renderer now supplies both
bootstrap and live preparation with the actual package path, a complete Windows
download/Connect block and stage-specific replies. Connect starts the existing
owned discovery task and returns to the current CC session. The existing deadline
supplies remaining seconds for connection and platform waiting; status polling
does not extend it. Unknown/stale replies are rejected by the existing control
process. Ended batches are reported explicitly; the final summary generates the
remaining preparation argv with updated modes, original connection options and
unfinished samples. No persistent marker, credential store or research migration.

Final related validation passes **344 tests in 174.30 seconds**, including actual
Chromium and isolated SSH, closed-stdin subprocess cleanup, existing private
control boundaries, fixed replies and deadline observation. Real PowerShell runs
the complete generated block with substituted SSH/desktop operations; actual Bash
reads a source path containing spaces, quotes and shell metacharacters. Assertions
verify literal arguments, no second init, full download before execution, download
failure without replacement/execution, and preserved modes after a timed-out batch.
The original focused result (187 pass / two skip / one failed assertion) is retained:
the assertion confused “remaining platforms” with “remaining waiting time”; it now
matches the timer label specifically, without changing the behavior under test.

The wheel, isolated install and fresh runtime bundle match **174 package files**
byte-for-byte. Installed setup points to the installed script; actual installed
preparation, private finish, remaining-command parsing, cleanup and MCP startup
pass. No research database is created by preparation. Compile, undefined-name/
duplicate-key lint and whitespace checks pass. Existing runs/Profiles/browser
login data remain unchanged. No new full G1, paid/GPU, actual Windows Task Scheduler
or visible SSO qualification is claimed. The baseline accounting issue recorded
below remains open. Program validation constrains executable actions; it does not
guarantee that a model can never add inaccurate natural-language commentary.

Evidence: `.local/qualification/institutional-fixed-flow-20261002/`, including
`initial-focused.xml`, `recovery.xml`, `final-focused.xml`, `installation.json`
and the exact generated `windows-setup.ps1` block. See the
[design](design/institutional-access-preparation.md#程序生成接入步骤与固定回复2026-10-02)
and [client guide](guides/institutional-access.md#one-windows-terminal-entry-for-init).

## Init-owned institutional connection and dialogue (2026-10-02)

From `78cc3de`, the user authorized design, implementation and commit to dev,
confirming a Windows terminal SSH entry. The failed preparation never created its
proposed research run: one unavailable client connection consumed three separate
120-second platform windows. A separate reproduction confirms that the background
terminal path raises EOF after a browser opens but stdin is unavailable.

Preparation now checks the actual CDP/context connection on the existing browser
worker before platform operations, using the existing connection timeout. Init's
generated command uses a private conversation session: answers travel through the
existing temporary control socket, progress flushes immediately, and finite answer
waits, finish and EOF close owned resources. Opened pages and unverified completion
never become login/subscription proof. The Windows `Init` entry derives standard
paths, checks SSH and remote tools, starts its existing owned discovery task, then
enters remote init; existing ownership checks and browser profiles are preserved.
The task remains available for research/later sessions. First local script access
and valid SSH authentication remain necessary; a server cannot launch programs on
an unconnected Windows machine. No new durable authority or research migration.

Six new regressions fail on the baseline (`red.xml`). Final related validation
passes **336 tests in 205.72 seconds**, including isolated real SSH, actual Chromium,
PowerShell parser/runtime checks, private answers, sample identity, platform modes,
source/preview boundaries and existing Host lifecycle cases. An actual subprocess
with closed stdin emits progress before exit, reports one missing-client connection,
never starts a platform action, and releases its private socket, relay port and
temporary directory after finish. A real Chromium case verifies connection/open/
sample work stays on one worker, sample bytes pass the original validator, and the
operator's existing tab survives cleanup. No research database is created.

Original intermediate results are retained: an implementation indentation error
caused repeat prompts and a deliberately interrupted focused test; the old EOF
assertion was updated for explicit input_required handling. An early readiness
check affected ordinary runtime connection behavior, so it was moved into the
preparation-only check without weakening the existing runtime test. A browser
fixture initially pointed at an absent Playwright cache; explicitly selecting the
installed Chrome resolves it. Privacy fixture setup was updated for readiness,
while secret-filtering assertions remain intact.

An additional isolated actual Host/CPU sample starts with waiting_for_client and
reaches COMPLETED revision 18, closing **12 Agents / one Process**. Independent
SQLite inspection counts one experiment, accepted outcome and goal verdict;
the existing Oracle is clean. No open Attempt, live recorded process group,
socket, connection directory or occupied relay port remains after Host close.
Its target/data are retained for repeatable read-only audit. The audit launcher
first lacked the test-helper import path and failed before creating any state;
the corrected invocation and original failure are both retained.

An isolated wheel install matches **173 package files** byte-for-byte with source
and a fresh runtime bundle. Installed background preparation, private finish,
resource cleanup, CLI parsing, MCP initialization and bundle identity pass.
Compile, undefined-name/duplicate-key lint and whitespace checks pass.

Full offline G1 covers all **6,825 collected identities exactly once: 6,753 passed,
71 skipped, one failed, zero errors**, in eight disjoint file groups. The sole
failure is the existing native model-usage accounting case under Claude Code
2.1.287: the fallback result's outer usage reports 54 input tokens while the
per-Attempt model ledger reports 29. The unchanged adapter conservatively retains
the larger aggregate, so the test's expected modelUsage normalization is absent.
Research reaches COMPLETED with all 12 Agents / one CPU Process closed; independent
inspection finds no live recorded process group or socket. An unchanged focused
recheck and an isolated checkout of baseline **78cc3de both reproduce the same
failure**. No accounting implementation or assertion was changed. This separate
accounting compatibility issue remains open; full G1 is not claimed green.

Actual Windows Task Scheduler/visible desktop and institution SSO still require
that machine; Linux PowerShell exercises parsing/orchestration with substituted
Windows APIs, not those native facilities. No paid/GPU claim.

Evidence: `.local/qualification/institutional-init-20261002/`, including `red.xml`,
`final-focused.xml`, original intermediate logs, `native-fixed.xml`,
`installation.json`, `retained-cpu-closure.json`, the complete G1 collection/results,
and `accounting-{original-audit,recheck,baseline}` evidence.
See the [design](design/institutional-access-preparation.md#init-内完成连接和交互2026-10-02)
and [client entry guide](guides/institutional-access.md#one-windows-terminal-entry-for-init).

## Issue report evidence completeness (2026-10-02)

From `e85b452`, the user authorized design, implementation and commit/push to dev.
Support selection expands the first unrecovered failure's retry chain and keeps
recorded final-report Attempts plus goal/outcome/verdict sources. Compact terminal
fact/artifact identities and the existing full-run charge/usage projection survive
narrow selection. Public role/fact identities are aliased; current budget fields
and decimal USD strings remain visible without Profile prose. Input and output
fidelity are classified separately, and pre-model accounting distinguishes an
unlaunched effect from lost capture.

Metadata across selected Attempts precedes large provider/transcript bodies.
The existing 15-second traversal and inspection limits are disclosed; unvisited
scopes are inventoried without inventing file counts. Debug detail space scales
with the package limit. README and Web preview explain effective limits, selection,
omissions and next actions. A reproduced small-package inventory overflow now
reports its omitted entries consistently in README and JSON. No new authority,
schema, digest, persistent cache, run migration or automatic upload is introduced.

Nine new cases first fail on unchanged implementation (`red.xml`). Final focused
coverage passes **117 tests in 18.23 seconds** (`final-focused.xml`), including
credential filtering, source immutability, accounting reservations, exact selectors,
time/inspection/file/byte limits, public/private terminal evidence, real HTTP/TUI
and browser download flows. Original intermediate failures are retained: an old
fixed-size test now explicitly exceeds the proportional detail budget, a fixture
phrase collided with new explanatory text, and a new assertion assumed every
inventory row had an Attempt identity. No privacy assertion was weakened.

Read-only replay of the supplied 63-Attempt index selects recent ordinals 60–62,
supporting retry ordinals 30–40 and final report 63: **15 detail targets instead of
four**. This validates selection over the available index; missing old bodies are
not reconstructed. Private source archives remain unchanged and ignored.

An isolated actual Host/CPU run closes **12 Agents / one Process**, with one
accepted outcome and one verdict, at COMPLETED revision 18. After Host close,
independent SQLite/process/socket inspection and the existing Oracle find no open
Attempt, live recorded process group, socket or Oracle issue. Public and private
exports preserve all authority rows and three terminal fact identities plus the
final-report source. Target/data remain in this disposable evidence root so the
audit stays reproducible. Earlier post-pytest inspection correctly reported nine
missing-source findings after fixture teardown removed its target repository;
two probe SQL table-name mistakes are retained, then corrected without rerunning
or changing the closed sample.

Independent inspection of two completed native G1 recovery roots counts **44
terminal Agents / four terminal CPU Processes**, with CREATE_EXPERIMENT and
ACCEPT_OUTCOME each recorded four times. Both existing Oracles are clean, with
no open Attempts, live recorded process groups or sockets.

The wheel installed outside the checkout matches **172 package files** exactly.
Installed budget projection, CLI support preview, MCP initialization and fresh
bundle identity/bytes pass; the absent startup source root stays absent. Compile,
undefined-name/duplicate-key lint and whitespace checks pass.

Full offline G1 covers all **6,814 collected identities: 6,743 passed / 71 skipped,
zero failures or errors**. After 851 passing cases, the serial invocation was
interrupted to finish the remainder in eight disjoint file groups. Its in-flight
case was rerun in a fresh root; the aborted disposable root retains one OPEN Agent
record but no live recorded process group or socket, and is not counted as a pass.
One group initially could not launch because a parameterized node exceeded the
OS argument limit; selecting the same complete files resolved the launcher error.
All eight groups then passed. JUnit identities exactly match the original
collection, with no omission or duplicate; source remained frozen throughout.
Original interruption and launcher outputs remain alongside `g1-coverage.json`.
Final documentation checks pass four tests. No paid-model/GPU compatibility or
latency guarantee is claimed. Evidence: `.local/qualification/issue-report-20261002/`, including
`retained-index-selection.json`, `retained-cpu-closure.json`, `installation.json`,
`independent-g1-native.json`, the focused/G1 JUnit results and original probe outputs.
See the [design](design/issue-report-evidence.md) and [usage guide](../USAGE.md).

## Implementation output feedback before native exit (2026-10-02)

From `6c32ed0`, bwrap70 reuses native Stop, actual workspace capture and the
canonical output-byte validator to return implementation capture/size errors
while the same call can still repair its files. A synthetic 52 KB source passes
the raw 128 KiB bound but exceeds it after source/patch encoding; 70 KB reproduces
the Git bound. Committed and untracked changes both reproduce the problem.
Feedback names the remaining patch allowance, encoded size and complete response
limit. The preview is a lower bound: final accounting/diagnostics are unavailable
until exit and still undergo unchanged full postflight. Correction caps, path
protections, budgets, publication and frozen old runtimes remain authoritative.
No new schema, acceptance record or persistent authority is introduced.

The isolated baseline records **12 failing new cases**: eight size/capture cases,
three correction/drift/path cases, and one final-validation case that cannot call
the absent design callback. The final focused selection passes **241 tests**;
the expanded source/session/ACI/instruction/document selection passes **262**,
including all twelve new cases. These selections overlap. Earlier fixture errors
(missing declared component configuration and fake response identity) and the
initial error-message compatibility regression are retained with their repairs.

The real installed CLI with a local scripted provider completes an entire Host
workflow and CPU publication in **39.23 seconds**. One implementation Attempt
receives two native rejections (patch capture, then encoded response), repairs
actual files and passes the third Stop. Independent read-only inspection counts
**11 successful terminal Agents / one successful CPU Process**, one
CREATE_EXPERIMENT and one ACCEPT_OUTCOME, a clean existing Oracle and no recorded
live process groups or sockets. Accounting uses the single terminal response per
Attempt. This validates mechanics, not autonomous model quality or measured
monetary savings. No paid model, GPU or submitted research run is executed.

Full G1 records **6,724 passed, three failed and 71 skipped** in 2,516.75 seconds.
All three failures are stale CLI expectations for bwrap69; the isolated CLI
selection reproduces exactly those three failures, then all **45** tests pass
after updating only the expected version to bwrap70. Combining final outcomes
covers **6,727 passing test identities and 71 skips**, with no unresolved failure.
The full suite was not repeated after that test-only assertion correction;
production source remained frozen throughout qualification.
Independent inspection of five completed native G1 roots counts **79 terminal
Agents** (78 succeeded, one lost) and **nine terminal CPU Processes** (six
succeeded, three failed in failure/recovery fixtures). CREATE_EXPERIMENT and
ACCEPT_OUTCOME each occur nine times. All five existing Oracles are clean, with
no open Attempts, live recorded process groups or sockets.

The wheel installed outside the checkout matches **172 package files** exactly.
Installed size validation, CLI help, MCP initialization and fresh runtime-bundle
identity pass. Compile, undefined-name lint and whitespace checks pass. These
checks make no G4/G5 or real-model savings claim.
Evidence stays in `.local/qualification/agent-output-preflight-20261002/`, including
`baseline-red.xml`, `focused-final.xml`, `extended.xml`, `native.xml` and
`independent-native.json`, plus `g1.xml`, `cli-red.xml`, `cli-final.xml`,
`coverage.json`, `independent-g1-native.json` and `installation.json`.
Original support archives and research state are
read-only; private case details remain under the ignored issue-report directory.
See the [design](design/agent-output-preflight.md) and
[runtime guide](guides/runtime-recovery.md#completion-and-final-report-evidence).

## Common publisher preparation during init (2026-10-02)

The user authorized design, implementation and commit/push to dev. The preceding
explicit-mode change is committed as `91cb855`; this follow-up reuses the platform
catalog, preparation queue, browser sessions and original document validator.
IEEE, ACM, Elsevier / ScienceDirect and Springer appear first. Three platforms are
selected for preparation by default; ACM is public-first with optional login.
Bootstrap provides a complete command, custom/none selections round-trip, and
preparation does not limit research discovery or change runtime defaults. Samples
are checked before login prompts; only gaps are rechecked after operator action.
Skip/public-only/finish choices return a transient summary and launch arguments,
not an authentication store. Unchecked pages never become verified access. Existing
research state, profiles, logged-in browsers and services remain untouched.

Six new regressions first failed, then passed. The first related selection passed
**140 tests**, with two skips and three deselections. Expanded coverage initially
recorded **256 passed, one failed, two skipped and six deselected**. The failure was
the existing init-instruction length limit: details were moved into its established
reference without relaxing the limit. All **44** instruction/document/profile
checks then passed. Combining final outcomes covers **257 distinct passing tests
and two skips**, not a second full invocation of the expanded suite. Coverage
includes inspect-to-launch-to-prepare parsing, default/custom/off choices, Elsevier
aliasing, public-only suppression, partial sample recovery, failure continuation,
interruption cleanup and existing routing/admission behavior.

The two skips require PowerShell and opt-in real systemd/browser testing. Six
deselections are three previously environment-blocked native bootstrap cases and
three real Host/UI/socket cases; the current loopback bind probe still fails with
EPERM. No new live browser/SSO/subscription test, native closure, full G1 or paid/GPU
qualification is claimed. The wheel installed outside the checkout matches **172
package files** exactly; installed catalog, alias/skip behavior, all five mode
previews and fresh runtime-bundle identity pass. Compile, undefined-name lint and
diff checks pass. Evidence: `.local/qualification/common-publisher-preparation-20261002/`
(`red.xml`, `focused.xml`, `final-focused.xml`, `final-docs.xml`, `coverage.json`,
`installation.json`, wheel/build/install output).

## Explicit institutional choice during init (2026-10-02)

The user authorized design, implementation and commit/push to dev from `bf73e24`.
Init now reports an unanswered `institutional_browser_mode` through its existing
`missing`/`next_actions` flow. CLI choices and conversation descriptions share one
mode definition; explicit `off` disables institutional access while public/OA
retrieval continues. Inspection does not produce launch arguments until the choice
is supplied, and reinspection retains the proposed run/state/request identity.
Partially supplied library settings survive the question. Direct launch/serve
omission and URL-only compatibility remain unchanged; no new configuration store,
confirmation record, runtime schema or migration.

Red regressions exposed six failures across the missing choice and the full
inspect-to-launch path. The old inspector invoked the manual connection planner,
rejecting client mode and injecting fixed endpoints into local/server. Inspect and
launch now share the existing validated preview; all seven initial cases pass.
Final coverage adds partial-choice continuation, conflict rejection, disabled-mode
zero browser/queue construction and exact Host argument preservation. Instructions
and bilingual guides now describe the required init question. Off disables the
institutional panel as well; panel PDF import remains an enabled-mode operation,
with per-platform `public_only` available when automatic access is unwanted.

The initial focused selection records **153 passed, four failed and two skipped**.
All four failures reproduce on unchanged baseline `bf73e24`: sandbox restrictions
prevent socket/Host readiness. An extended selection was interrupted after a
restricted fixture and a stalled UI test; its partial log is preserved, not counted
as a pass. The final bounded selection records **169 passed, two skipped, one failed
and two setup errors**, with five deselected cases. The remaining failure cannot
create an HTTP socket; the setup errors cannot create fixture directories in the
read-only `/var/tmp`. Four deselections are the baseline-confirmed socket failures;
the other is a retained Host-restart fixture requiring `/var/tmp`. No assertion or
product validator was relaxed. This is focused follow-up evidence, not a new full
G1, native Host/CPU closure, real browser/SSO or interactive Claude acceptance.

The installed wheel outside the checkout matches **172 package files** exactly.
Actual read-only CLI inspection covers unanswered/off/client/local/server/existing;
fresh runtime-bundle identity, compile, undefined-name and documentation checks
pass. Existing research runs, Profiles, browser logins, frozen bundles and production
services are unchanged. No paid-model, GPU or subscription call is made.
Evidence: `.local/qualification/init-institutional-choice-20261002/`, including
`red.xml`, `focused.xml`, `baseline-environment.xml`, `final-focused.xml`,
`extended-sandbox-interrupted.log` and `installation.json`.

## Run-scoped institutional connections (2026-10-02)

The user authorized implementation and phased commits/push to dev from `8f6b39e`.
Core `d5eb0d5` assigns institutional transports to individual runs using actual
OS listeners, existing run heads and system-service ownership. Completion,
cancellation, startup failure and Host exit retire owned resources; pause and
monitor exit do not. Browser profiles persist. New client/local/server/existing
modes separate Windows forwards, native persistent browser pipes, private containers
and external browsers. Fixed-port allocation failure stays local to the affected
connection and retries within existing acquisition limits after the port is freed.
No port registry, terminal marker, credential export or retained-run migration.

Real isolated Chrome checks cover authenticated HTTP/WebSocket CDP, external-browser
ownership, native pipe startup/restart, synthetic-cookie persistence, concurrent
profile refusal and owner SIGKILL. Actual Linux OpenSSH with disposable keys covers
private Unix forwarding, reconnect and terminal-run watch exit. It reproduced stale
socket paths after disconnect; reconnection now rechecks the current Host/run and
removes only a same-user refused socket, never an active listener. Actual user-systemd
EOF services release listeners and socket directories after owner death. Container
lifecycle checks use a substituted Docker executable with real services/process
death; the selected Chromium image is absent, so actual container deployment is not
qualified. PowerShell 7.6.6 checks parsing, literal arguments, actual child cleanup
and independent run retirement; Windows CIM/ACL/Task Scheduler APIs are substituted.

The initial focused selection passed **240** cases. Final allocation-isolation and
related institutional/bootstrap/control/browser coverage passed **211** cases in
109.38 seconds, including the native browser and SSH opt-ins. Red/green regressions
also reproduce and repair a private GUI password in default preparation output
(`3186db2`) and a fixed-port conflict escaping into the shared Host (`195bd22`).
Private GUI credentials now require explicit private-control discovery; partial
allocations are cleaned without stopping another run.

Full G1 collected **6,746** identities: initially **6,668 passed, eight failed and
70 skipped**. Five native failures were caused by this maintenance session editing
package source during the active run; the existing runtime-pin protection correctly
rejected that drift. Those cases passed unchanged after source stabilization. Two
CPU closure assertions also passed unchanged in the bounded recheck. The remaining
batch-reading failure reproduced on baseline `8f6b39e`: the scripted test provider
assumed both documents were available immediately despite one PDF worker. Fixture
repair `360f7d9` retries the exact batch once, reuses the successful source and still
requires both sources, three real readers and the unchanged CPU/publication checks.
Production acquisition policy, slots and validators are unchanged. Original failures
and baseline reproduction are retained. Full-suite plus focused repair/browser
coverage totals **6,683 passing identities and 65 skips**, with no unresolved failure;
this is not a second complete G1 run after the repairs.

Independent read-only inspection of **14** successful final native roots counts
**496 terminal Agents** (487 succeeded, four cancelled, one failed, four lost) and
**37 successful CPU Processes**, with 37 CREATE_EXPERIMENT and 37 ACCEPT_OUTCOME
effects. Existing Oracles are clean; there are no open Attempts, live recorded process
groups or control sockets. Coverage includes multichain science, source readers/OA
fallback, model retry, Leader/Debate, manager/proposal restart and final reports.
Provider responses are locally scripted; Host, CLI, broker, CPU and publication are
real. Failed initial qualification roots remain separate evidence, not settled into
this clean cohort. No institutional connection service remains active.

Installed baseline `8f6b39e` and core `d5eb0d5` produce identical existing-Oracle
results for all **25** retained roots, preserving two historical findings. Logical
SQLite rows and artifact metadata are unchanged, with zero open Attempts or live
recorded process groups. Later repairs do not change the Oracle or retained readers.
The final-code wheel installed outside the checkout matches **172** package files
byte-for-byte; nine platform entries, two MCP tools, CLI preview and fresh runtime
bundle identity pass. An initial final-install check reused an older disposable
bundle and correctly failed identity comparison; a fresh fixture passes without
changing retained bundles. Documentation, compile, undefined-name and whitespace
checks pass.

Evidence: `.local/qualification/institutional-run-connections-20261002/`, including
`g1-*.xml`, `recheck-*.xml`, `isolation-native.xml`, `batch-fixed.xml`,
`final-coverage.json`, `native-closure.json`, `retained-final.json` and
`installation-final.json`. Existing research runs, Profiles, frozen bundles and the
production pool remain unchanged; the user's browser/tunnel on 9222 is untouched.
No real subscription, paid-model or GPU run is launched. Visible Linux GUI, Windows
native APIs/logon tasks and actual Docker deployment still require corresponding
machine acceptance. Linux SSH coverage does not establish Windows SSH integration.
Earlier IEEE/Springer login results do not prove permanent sessions or all-publisher
entitlement. No G4/G5 claim. See the [current guide](guides/institutional-access.md).

## Institutional connection startup (2026-10-02)

The user authorized implementation and phased commits/push to dev from `158b792`.
The candidate adds explicit existing/local/server connection startup, native Linux
user-systemd browser management, a standalone Windows PowerShell connector with an
optional user-logon task, and read-only init review followed by connection readiness
before Host creation. It reuses CDP validation, browser profiles, systemd ExecStart,
Task Scheduler and Compose metadata. No credential database or parallel run state is
introduced. Endpoint-only usage and public/OA research remain compatible.

The first real Chrome/systemd lifecycle check reproduced Chrome rewriting its
process argv; subsequent reuse/stop incorrectly rejected the owned process. Ownership
now uses systemd's structured ExecStart argv plus actual current-user process liveness.
The restart check then reproduced both a stale browser lock and a recently set cookie
lost by signal-based shutdown. Chrome handles its own demonstrably stale local lock;
the launcher never removes it. Explicit local stop sends Chromium `Browser.close`
and confirms service termination, letting Chromium flush sessions normally.

A later real concurrent-start run reproduced a second caller rejecting the first
caller's newly created endpoint/profile after a stale preflight observation. Four
red/green regressions cover ownership changing during handshake, port/profile checks,
and an activating unit before MainPID appears. Startup now refreshes the existing
systemd authority and waits within the original deadline; it neither adopts foreign
resources nor stops the winning caller. An init regression also preserves an explicitly
relative browser executable across the launch working-directory change.

Final focused validation: **113 passed in 26.77 s**, including actual systemd/Chrome
concurrent startup, CDP reuse, graceful stop/restart and persistence of a synthetic
cookie. The repaired real-browser scenario also passed in the preceding 98-case
selection. Headless/no-sandbox/basic-storage flags belong only to its disposable test
wrapper. Portable PowerShell 7.6.6 checks syntax, literal arguments, browser-identity
comparison and actual child-process cleanup. An actual failed connection through
bootstrap verifies that no managed Host or run is created.

Frozen candidate `c75c451` collected **6,720** G1 identities. The initial eight-group
run recorded 6,322 passes, 247 failures, 85 errors and 66 skips. The qualification
harness placed fixtures under paths too long for Unix sockets; one CPU overlap
observation also missed its expected interval under concurrent load. All **332**
failing/error identities passed unchanged with short fixture paths, without weakening
product validators or assertions. Initial failures and rechecks are preserved.
Combining G1, those rechecks and the final startup regressions/browser opt-in covers
**6,661 passing identities and 65 skips**. This is full-suite plus focused repair
coverage, not a second complete G1 run after the startup fixes. Compile, undefined-name
and whitespace checks pass.

Independent read-only inspection of **11** disposable native roots closes **327 Agents**
(321 succeeded, four cancelled, one failed, one lost) and **24 successful CPU Processes**.
There are 24 CREATE_EXPERIMENT and 24 ACCEPT_OUTCOME effects, clean existing Oracles,
zero open attempts, zero live recorded process groups and no control-socket residue.
The roots cover native source readers including institutional public-only/OA fallback,
model-retry recovery, multi-chain science, Leader/Debate and final reports. Provider
responses are locally scripted; the Host, CLI, tools, CPU execution and publication
paths are real.

Installed baseline `158b792` and candidate `c75c451` produce identical existing-Oracle
results for all **25** retained roots, including the same two historical findings.
Read-only before/after comparisons preserve logical SQLite rows and artifact metadata;
there are no open Attempts, live recorded process groups or retained Hosts. Initial
reader timeouts under G1 load are retained alongside successful bounded rechecks.
The subsequent startup fixes do not change the Oracle or retained reader. A final-code
wheel installed outside the checkout verifies **167** package files byte-for-byte,
nine preparation platforms, CLI/MCP entry points and fresh runtime-bundle identity.

Private evidence: `.local/qualification/institutional-connection-20261002/`, including
`g1-*.xml`, `g1-recheck-*.xml`, `final-focused.xml`, `final-coverage.json`,
`native-closure.json`, `retained-final.json` and `installation-qualified.json`.
No retained run, Profile or production pool is started or changed. User browser/tunnel
port 9222 is untouched; no subscription sample, paid model or GPU run is launched.
Visible X11/Wayland windows, Windows CIM/Task Scheduler/real SSH reconnect, and Docker
deployment are **not yet qualified**. The operator uses Windows and password SSH;
one-time local key/ssh-agent setup is documented, with no password stored or used by
the connector. Prior IEEE/Springer subscriptions do not establish these new platform
behaviors. No G4/G5 or all-publisher entitlement claim.

## Real Springer subscription and paywall classification (2026-10-01)

The operator restored the Windows Edge 154.0.4258.48 loopback CDP tunnel and
completed institutional sign-in for DOI `10.1007/s10489-023-04932-7`. Before login,
the identified article displayed an explicit access paywall while its PDF link
returned HTML. The adapter classified that as retryable `pdf_response_not_pdf`,
so standalone preparation exhausted its 120-second acquisition window.

The generic adapter now returns `access_required` for that combination, after
preserving the existing supported PDF-viewer routes. This ends background waiting
through the existing queue policy; it does not infer that the institution lacks
entitlement. An HTTP server error or a non-PDF response without positive paywall
evidence remains retryable. No source identity, authentication or origin check is
relaxed, and no additional persistent state is introduced.

The real Chrome regression first reproduces the incorrect retry; its two negative
controls already pass. All **166** final institutional/browser/UI/document/reading
and documentation checks pass in 124.94 seconds, including all three new browser
cases and queue coverage of login, verification and access-required states.
Undefined-name and whitespace checks pass. This is focused follow-up validation;
the full G1 and retained-run cohort below predate this two-line adapter repair.

After the operator's login, the standalone non-interactive preparation command
reports `sample_verified`. Two further independent browser/queue connections each
obtain a **17-page PDF, 2,627,769 bytes**, with the expected content DOI and exact
original bytes preserved in each private archive. Neither connection opens a
manual login window or requires another login. Adapter cleanup leaves the user's
browser connected. No research Host, run, model call, cookie/storage export or
retained-state mutation is involved.

Evidence stays under
`.local/qualification/institutional-preparation-20261001/springer-live/`:
`initial.log`, `initial-diagnosis.json`, `paywall-red.xml`, `paywall-final.xml`,
`after-login.log`, `reuse/result.json` and `browser-after.json`. PDFs stay private
and ignored. This adds one real Springer paper and current-session reuse to the
previous IEEE evidence. It does not establish a second paper on the same platform,
future session validity, browser-restart persistence or universal platform access.

## Institutional preparation and bounded access (2026-10-01)

The four [design batches](design/institutional-access-preparation.md) are implemented
for new lifecycle v11 runs. Background acquisition ends after a finite window
(default 120 seconds); authentication ends background waiting immediately unless
the operator explicitly opens a manual window (default 600 seconds). New papers
do not extend it. Late results cannot replace a retry or a skipped request.
Run-scoped skips, restores and platform modes use the existing transactional command
journal and survive same-version Host restart. No new table or credential store.

The standalone `python -m hypoloom institutional prepare` command and full Web/TUI
platform catalogs allow preparation before any paper request. Sample verification
uses the normal PDF identity validator, then releases its bytes without admitting
research evidence. Bootstrap's full and compact review preserve the preparation
command and selected options. Public-only platforms still use exact-DOI open-access
fallback; dynamic tools share the existing locators, rate limits, PDF slots and
archive validators. The actual retrieved version/origin remains visible. Cookies
and storage stay in the dedicated browser; no indefinite session validity is claimed.

Qualification used frozen candidate `74492c5` for the full offline invocation:
**6,620 passed, one failed, 62 skipped** in 2,976.92 seconds. The failure was an
existing relay test client writing a body after a header-level rejection had
already closed its Unix socket. The fixture now reads the real error response
when that write raises BrokenPipe; the expected HTTP status and zero upstream
calls are still required. Installation checks separately found the missing
`python -m hypoloom` entry point and omitted institutional fields in compact init
review. Both are fixed through the existing CLI/projection, with regressions.

Final-source results:

- 108 relay, bootstrap, preparation and frozen-runtime checks pass, including the
  original failure and the new installed-command/compact-review cases.
- 214 institutional, real Chrome/Poppler, Web interaction, dynamic document,
  open-access, reading and documentation checks pass in 122.76 seconds.
- The native institutional/OA case passes again in 58.40 seconds: a real Host,
  broker and native CC readers obtain the validated OA source under public-only
  mode, three readers inspect it, and one actual CPU experiment is accepted.
  There is one durable platform-choice command and no institutional browser fetch.
- Combined test-identity coverage is **6,659 passed / 26 opt-in skips**, with no
  unresolved failure. This combines the initial full invocation and focused
  repairs/opt-ins; it is not a claim that the initial invocation passed.
- Wheel and sdist build; an isolated wheel installation outside the checkout
  matches all 165 packaged files, lists nine platforms through the documented
  module entry, exposes both MCP tools, and creates a fresh runtime bundle with
  the same installed runtime identity. Compilation, undefined-name and whitespace
  checks pass.

Independent closure inspection covers seven completed native roots from full G1
and the final OA rerun: **171 Agent Attempts / 11 CPU Process Attempts**, all
closed, with 11 experiment-create and 11 outcome-accept effects. All eight Oracles
pass; there are no open Attempts, live recorded process groups or control sockets.
The separate control regression restarts an actual Host and preserves preferences
without replaying the manual login window. Scripted local providers exercise the
native topology without paid model calls; these are not scientific-quality G5 results.

G2 retained cohort: all **25** top-level existing research stores, ACI 19–29 and
lifecycle v9/v10, were read by isolated baseline (`8ec9dd5`) and candidate wheels.
All complete Oracle outputs match exactly. One larger archive timed out at 90
seconds in both readers; a bounded 300-second retry completed in about 87 seconds
per reader and matched. Two roots retain identical pre-existing Oracle findings;
no historical repair is implied. Logical database dumps and artifact metadata are
unchanged. No OPEN Attempts or live recorded process groups remain; an independent
Host process scan also found no retained Host. No old run was resumed or migrated.

Evidence stays in `.local/qualification/institutional-preparation-20261001/`:
`g1.xml`, `g1-repairs.xml`, `final-focused.xml`, `native-final.xml`,
`g1-identity-coverage.json`, `native-closure-summary.json`,
`retained-summary.json`, per-root audits and `installation-final.json`.
The supplied Springer DOI `10.1007/s10489-023-04932-7` is a second-platform sample,
not a second IEEE paper. At submission the operator CDP tunnel disconnects; the
user plans to restore it, so no new Springer subscription result is claimed.
Earlier real IEEE evidence remains valid only within its stated scope below.
Server Docker deployment, browser restart/session expiry, another entitled IEEE
paper, real denied entitlement and paid/GPU G4/G5 remain unqualified. Existing
research runs, Profiles, frozen bundles and the production pool are unchanged.

## Real institutional subscription and IEEE viewer repair (2026-10-01)

The user completed Xidian University sign-in in a dedicated Windows Edge
154.0.4258.48 profile, reached over a loopback reverse SSH/CDP connection.
The supplied Shibboleth entity URL serves SAML metadata; interactive sign-in
starts from IEEE's institutional selector. The Windows guide now discovers
installed Chrome/Edge and checks local CDP before opening the tunnel.

Real access reproduced two publisher compatibility faults: IEEE inserts its PDF
control after the initial DOM, and `stamp.jsp` serves an HTML viewer containing
the PDF iframe. The adapter now uses the existing bounded settling interval,
reads the publisher's public DOI metadata when standard metadata is absent, and
follows the viewer through the existing PDF-navigation lifecycle. The requested
and final PDF endpoints must use IEEE HTTPS and the arrived article's number;
actual PDF content still passes the existing first-page identity validator.
Login and verification retain the operator's tab. No new persistent authority,
authentication automation or topic-specific behavior was added.

All six new Chrome/Poppler regressions first failed and then passed: late controls
from article/DOI requests, wrong article number, foreign iframe origin, conflicting
PDF DOI and operator verification without repeated navigation. The complete
institutional access, navigation, browser/UI and documentation selection passes
**118 tests**, including 35 browser-adapter cases, in 97.73 seconds. This is
focused follow-up qualification; the full G1 coverage below predates this repair.

Real retrieval used DOI `10.1109/TBC.2025.3565888`, IEEE article `11006507`.
Two independent adapter/queue connections obtained a **16-page PDF** each,
3,678,651 and 3,678,649 bytes, with the expected content DOI and exact original
bytes preserved by each archive. The second connection required no further
login. A third single-phase check starting at the DOI resolver also passed
(16 pages, 3,678,658 bytes). Each archive matches its own download; the separate
downloads are not byte-identical. The probe uses the actual browser, queue,
Poppler and paper archive without starting a research Host or calling a model.
It closes its owned tabs and disconnects, leaving the user's browser alive.

Evidence stays in `.local/qualification/runtime-literature-20261001/`:
`ieee-red.log`, `ieee-fix.xml`, `ieee-final.xml`,
`subscription-ieee-20261001-170234/result.json` and
`subscription-doi-entry/result.json`. Entitled PDFs remain private and ignored;
no cookies, storage, credentials or SSO callback URLs were exported. This proves
one institution's access to one paper and reuse of the currently valid browser
session. It does not qualify another paper, publisher, browser restart, expiry,
server Docker deployment or full Host/model integration. Existing research
runs, Profiles, frozen bundles and the production pool remain unchanged.

## Current integration — runtime and literature (2026-10-01)

The user authorizes topic-independent framework fixes, corrected integration of
PR #3/#2, and phased commits/merges pushed to dev.
[Design](design/runtime-and-literature-integration.md). Existing research state,
Profiles, frozen runtimes and the production pool remain read-only.

Read-only stopped-run analysis: research ran for about 12 h 4 min, followed by
7 min 37 s of report closure. All 83 Agent Attempts (77 successful, four failed,
one timed out, one cancelled) and nine Process Attempts (six successful, one
failed, two cancelled) are closed. Recorded process groups and the Host exited;
the control socket/lock closed, and all nine pool requests were released.
Six hypotheses yielded nine LOCAL outcomes: five CLEAN_REFUTATION, one SUCCESS,
three EXECUTION_FAILURE, including two operator cancellations. No GOAL verdict
was accepted. Reported tokens total 162,692,126 including cache; four closed
calls have incomplete usage. These numbers are not a provider bill. Independent
read-only Oracle passes; its structural checks do not validate every narrative
claim in the old final report. Original evidence remains under
`.local/analysis/stopped-run-and-prs-20261001/`.

Batch 1 uses bwrap69 and the existing native validator/accounting. Same-session
identical task-notification completions use the last consistent cumulative ledger
once; ordinary duplicate or changed results still fail. Repeated Stop rechecks
current sources/report/Host state. Invalid stream classification survives before
workspace capture. Existing workflow guidance prioritizes actual selected-arm
dispatch/lineage paths and bounds configuration-versus-mechanism attribution.
No additional persistent authority or scientific gate is introduced.

335 distinct focused checks pass: 267 native-output/adapter/accounting cases
(including the final corrected-budget fixture rerun) and 68 CLI/workflow/native
Host cases. The real native same-task retry test closes one CPU experiment,
checks resumed accounting, accepted effects, Oracle and process residue. The
retained stream now parses with two completion results and charges the final
ledger once: 6,936,435 tokens; CLI cost 3.594386399999999 USD with unknown model
pricing. This is read-only replay, not retroactive repair or settlement of the
old Attempt. The first new fixture failed due to its own default turn/cost and
legacy-shape limits; those inputs were corrected without weakening production
checks. This batch is focused qualification, not full G1 or paid-model quality.
Evidence: `.local/qualification/runtime-literature-20261001/`.



Batch 2 merges PR #3 with corrected recovery/accounting. Real pre-model Grounding
Attempts carry the failed retrieval and exact consumed query; later query plans
receive this evidence. Existing model/retrieval budgets bound replanning across
Host restart. Exhaustion records the actual failed Grounding node; no synthetic
plan Attempt or copied response is created. The existing accounting fold derives
zero model consumption from the Host's literature-retrieval failure stage.

Final-report input now separates all unsuccessful Agent attempts from exhausted
nodes, requires their available failure references, and delivers bounded declared
raw text measurements through the same publication verifier as logical summaries.
Included/omitted files remain tied to their canonical outcomes and preregistration;
raw data does not establish a new verdict. PR #3's implementation-lineage checks
and exact query-plan causation are retained.

331 distinct focused checks pass: 259 report/Oracle/literature/Host-tool cases,
68 budget/measurement/native report cases and four documentation checks. Four
replanning paths cover recovery, persistent gaps and Host restart with independently
checked nonzero plan charges and zero pre-model charges. Real CPU publication
passes task-defined CSV bytes to final reports with exact artifact/outcome identity.
Native retry closure independently checks one CPU Process, recovered Agent failure
citations, cumulative session charging, Oracle and absent process residue. The
scripted report fixture initially omitted the newly required recovered-failure
citations and correctly failed; its generated narrative was updated to read the
new source fields. Persistent-gap tests use the fixture's configured six-attempt
limit, not an assumed three. No production run mutation or paid-model call.

Batch 3 integrates PR #2's optional institutional browser, grouped operator
queue, reusable browser sessions and PDF import. Exact-packet retrieval traces
now prevent rejected sources from reentering the browser/cache route. PDF identity
comes from the first physical page; a conflicting DOI or unconfirmed identity is
rejected even when the landing page names the expected article. The title fallback
requires a sufficiently specific title on that page, not a later reference list.

401 distinct focused checks pass before the full suite: 108 institutional/access
UI cases and 293 literature/Host/observer/notification/CLI/documentation cases.
The browser selection uses actual local Chrome and Poppler, including a wrong
PDF behind a correct article landing page. Synthetic login/session reuse,
operator authorization, cancellation, source rejection and exact-byte archive
publication are covered. This does not establish real institutional entitlement.

Initial full G1: 6,579 passed, 16 failed, 55 skipped in 1,022 seconds. Six native
failures were caused by editing a runtime hint during qualification: the existing
runtime pin correctly rejected changed source. Further repairs preserve legacy
packet-only instructions and update stale ACI/token/UI/service fixtures. A native
Skill assertion now checks delivered body instructions instead of stripped
frontmatter. Request/fact-context construction and transcript usage observation
move to existing modules; workflow size limits and validators are unchanged.
The final repair selection passes all **389** cases against unchanged runtime
source, including every original failure. Final G1 test-identity coverage is
**6,595 passed / 55 skipped**; this combines the full invocation with its focused
repairs, not a claim that the initial invocation passed. The skips include 29
institutional browser cases already exercised separately with local Chrome, and
26 opt-in lanes. Four final documentation checks, compilation, undefined-name
checks and whitespace validation pass.

Independent closure inspection covers 13 completed native/CPU roots: **410 Agent
Attempts and 34 CPU Process Attempts**, all closed. There are 34 experiment-create
and 34 outcome-accept effects, clean Oracles, no open Attempts, live recorded
process groups or control sockets. Injected failure/restart cases retain their
failed/lost Attempts rather than hiding recovery. The stopped research run's
absolute-path read-only Oracle also passes. Evidence: `g1.xml`, `g1-repairs.xml`,
`g1-identity-coverage.json`, `g1-closure.json`, `repair-closure.json` and
`retained-oracle.json` in the integration evidence directory. No G4/G5 promotion
or paid-model quality claim follows from these scripted native checks.

At this integration point, real subscription validation awaited the user's
entry URL, entitled DOI and browser connection. Docker image startup was blocked
by the server daemon's unreachable configured proxy; direct registry
TLS also failed. No institutional browser container started and no shared proxy
configuration was changed. The subsequent Windows Edge/SSH check and publisher
repair are recorded [above](#real-institutional-subscription-and-ieee-viewer-repair-2026-10-01).
The server container remains unqualified. Existing research runs remain unchanged.

## Institutional-access PR regressions (2026-10-01)

The initial PR selections recorded 567 passes and four failures. A focused
rerun reproduced the same four failures. Three native notification cases
terminated the Host before the existing 30-second cooldown expired, then
incorrectly expected an immediate HTTP retry. They now wait for the actual
retry and terminal delivery before shutdown, retaining the exact event replay,
credential-isolation, cursor and listener-cleanup assertions. Production
backoff and shutdown behavior are unchanged.

The literature test treated a roughly 0.4-second total Host tick on WSL's
mounted checkout as proof that background retrieval blocked the writer.
It now keeps retrieval blocked until the real socket tool reply and accepted
cancellation complete, asserts independent worker ownership, then releases
the worker and checks terminal Attempts, Oracle and worker cleanup. A watchdog
bounds failures; this tests concurrency, not a filesystem-dependent 250ms SLA.

Final validation: **84 passed** across `test_observer_services.py`,
`test_literature_workflow_host.py` and `test_service_notifications.py`, including
all four original failures. Documentation checks: **4 passed**; focused Ruff
undefined-name checks and `git diff --check` pass. The complete offline suite,
paid-model lanes and real subscriptions were not rerun for this test-only fix.

Evidence: `.local/qualification/institutional-pr-20261001/reproduce.xml`,
`repair.xml` and `repair-docs.xml`. This follow-up changes tests and documentation only. Existing
research runs, deployed browser sessions and frozen bundles are untouched;
no public notification, publisher request or paid model call is made.

## Current repair — literature recovery and notification health (2026-10-01)

The user authorizes framework implementation after an upstream OpenAlex outage
and undelivered Telegram notices. OpenAlex service failures now share a bounded
run-local wait derived from existing retrieval Attempts and operator transitions;
they do not exhaust normal node/model retries. Authentication requires an
operator immediately; transient failures use six failed waves with increasing
waits, respecting Retry-After. An actual successful retrieval clears the wait,
independently of the subsequent model outcome. The selected original credential
source is reread before search. Query validity, coverage, PDF/private-address
checks, model controls and run budgets retain their existing validators.

Notification health comes from the owning delivery service, with pending counts,
last success, safe failure phase/class and next retry. Telegram has explicit
access/test-message commands, bounded backoff, operator retry and ordered routine
backlog summaries. Important events remain separate. Existing per-source cursors
advance only after acknowledgment and durable replacement. Web/TUI reuse the
existing scoped recovery permission; retries never claim actual recovery. Health
is disposable, unknown after restart until observed; no second event queue or
persistent status store is added. Both language templates/guides and init wording
are updated. Existing research runs, frozen bundles and private Profiles remain
unchanged. No production service operation, public message, model call, commit
or push is performed in this implementation turn. The user subsequently authorizes
commit/push to dev; submission reuses the completed focused checks and retains
the native/public-delivery qualification limits below.

The extension of the existing dispatch wait has this bounded justification:

1. Retained pre-model retrieval failures exhausted MAIN Attempts during upstream
   500/504 responses; later successful authenticated requests could not reopen
   the failed node. Notification OSError labels hid the delivery failure phase.
2. The failed boundaries are remote retrieval versus scientific invocation, and
   remote acknowledgment versus the existing local delivery cursor.
3. Git, atomic cursor writes, command deduplication and tests already protect
   identity/persistence; they cannot make an unavailable remote service respond
   or distinguish its waiting policy from a model output retry at runtime.
4. The minimum addition is a derived availability reducer over existing Attempt
   observations, the existing retry command, and in-memory delivery health.
   Maintenance is limited to failure classification/recovery tests; merge the
   reducer into shared service recovery if another literature adapter needs it.
   There is no new schema, durable gate, hash, queue or authority to migrate.

Final focused validation: **427 passed** across four disjoint selections:
206 service/notification/literature checks, 200 CLI/init/budget/documentation
checks, 20 ordinary literature Host/Oracle regressions and one Web asset check.
The final 26-test rerun covers the last changes and overlaps these counts. Four real WorkflowHost
cases cover 500/401, with and without Host restart, idempotent operator requests,
next accepted hypothesis, explicit test cancellation, closed Attempts and clean
Oracles. These use injected literature/model responses, not public APIs or CPU
experiments. HTTP-handler tests preserve bearer/operator grants, origin, run
scope and exact request retry; they do not qualify a native listener. Python
compilation, JavaScript syntax and diff whitespace checks pass.

Native loopback/TLS tests are blocked by socket EPERM. Broader Process selections
also fail before expected execution closure in this environment; no native CPU
pass is claimed. Textual request assertions finish in a diagnostic run, but
asyncio executor shutdown stalls; the same stall reproduces with
`asyncio.run(asyncio.to_thread(lambda: 42))`, without Hypoloom. Native TUI teardown,
browser interaction and real Telegram receipt remain unqualified. No assertion
or production safeguard is weakened to bypass these limits. Full G1/G3/G5 is not
claimed. Evidence and original failures remain in
`.local/qualification/service-notifications-20261001/`; original diagnoses remain
in `.local/analysis/literature-service-20260930/` and
`.local/analysis/telegram-notification-20261001/`.

## Current repair — bounded Debate and research efficiency (2026-09-30)

The user authorizes the four-part implementation and submission to dev after
manually stopping the research run. New ACI 29 / bwrap68 gives every Debate round
and retry a fresh session; original expert ownership, source checkout, accepted
statements, actual session IDs, six-output judging and winner continuation remain
validated. Retained ACI 14–28 keep their fork semantics. Exact prior own/opponent
statements and a source-derived topic/original-statement view avoid recursive
conversation input. Existing attempt budgets and irreducible-refusal handling
remain in force; no new persistent authority or threshold policy is added.

The existing research-workflow references now guide task-specific small actual
implementation/save/reload/evaluation checks before costly work, corrected-path
verification after technical failure, meaningful LOCAL comparisons and evidence-
based next experiments. These are agent methods, not universal execution gates
or a guarantee of scientific superiority. Goal/LOCAL/Process observations stay
separate: numeric criteria have waiting panels, checks are per experiment,
LOCAL displays its declared criterion, and the exact published artifact identifies
its producing Process. Stage token totals reuse the existing charge fold;
failed/recovery subsets can overlap and reserves are not consumption. Web expert
lights distinguish Host contact; stage details and the TUI share the same totals.
Bilingual context, workflow and usage guides are updated.

Validation evidence: `.local/qualification/research-efficiency-20260930/`.
Read-only replay retains all 877510 input bytes, removes inherited native history
and renders approximately 31 KB of prompt; this excludes CLI system/tools and
is not a measured provider token reduction or model-quality result. Original
private evidence stays at `.local/analysis/research-progress-20260930-0925/`.

The final bounded checks pass **564 distinct tests** (560 in the closure selection
and four additional offline cases excluded by its name filter). They cover Debate
sessions, Host dispatch, exact-source and
promotion oracles, interaction admission, actual Node metric rendering, observer
accounting, workflow/plugin ownership, context continuation, CLI identity and
documentation. An overlapping final source/UI selection passes 105 tests.
Syntax, focused undefined-name lint and whitespace checks pass. Both wheel and
sdist build; isolated wheel installation matches all 156 runtime files and reports
ACI 29 / bwrap68. Packaging does not establish native execution compatibility.

The broader G1 attempt stops after 20 failures with 709 passing cases. One was
an outdated adapter-version assertion, subsequently corrected with the current
identity checks; the remainder depend on native sockets, isolated parser execution
or Host/CPU readiness unavailable here. Representative analysis-closure and
candidate-publication failures reproduce on published dev (25d3c7e). The actual
native ACI-29 Debate fixture cannot create its local provider socket (EPERM),
Chrome exits during startup, and three domain parser probes are denied
NETLINK_ROUTE by bwrap. These remain limitations, not passes or full G1/G3/G5
qualification. A private launcher relocates test-only /var/tmp directories to
writable /tmp; it does not replace sockets, parsers, CLI or validators.
An additional asynchronous TUI selection was interrupted while waiting in the
snapshot-reader test; that unfinished selection is not counted as passing.
No production run, pool, Profile or paid provider call is changed by this work.


## Current repair — Compact LOCAL screening results, 2026-09-29

The latest retained run initially had one published LOCAL value and no GOAL
measurements, so the observer drew a large single-point chart. LOCAL groups now
use compact result cards for one numeric observation and trends for two or more.
Grouping still requires the same hypothesis and recorded measurement definition;
GOAL curves, legend toggles and target lines are unchanged. Local headings show
the metric and a short hypothesis description, with full identity, measurement
rule and original numeric precision available on hover and in retained records.
This is a display change using existing accepted facts, with no new authority,
schema, backend logic or scientific acceptance rule.

Focused observation/Node/documentation checks: **15 passed**. Two actual Chrome
interaction cases pass, covering single-to-trend refresh, separate hypotheses
and baselines, missing versus zero/negative values, original precision, long
labels, GOAL waiting/arrival and existing legend controls. Screenshots cover
single cards, mixed local results and GOAL trends. The first Node-harness check
miscounted SVG classes because its stub stores them as attributes; that test-only
reader is corrected and the initial failure retained. Syntax, focused lint and
whitespace checks pass. Evidence: `.local/qualification/local-metric-cards-20260929/`.
No research run, Profile or frozen bundle was changed; no model/CPU/GPU workload
was performed. The user subsequently authorizes commit/push to dev, reusing these
completed checks. These focused UI checks do not claim full G1/G5.

## Current repair — Isolated literature address rejection, 2026-09-29

A retained expert completed query planning but lost all three MAIN preparation
Attempts to `landing_uri is not globally routable`, before model invocation.
Offline one/two-worker controls reproduce a single non-public DNS answer
discarding the whole packet and being classified as a retryable FETCH_FAILED.

The repair classifies address validation using existing VALIDATION_FAILED and
isolates that known rejection at the paper boundary. It preserves the denied
paper as PDF_UNAVAILABLE without full-text claims, delivers other valid papers,
and records the identity/field/hostname/code in existing bounded download traces.
No request, cached-PDF reuse or fallback lookup is allowed for the rejected
candidate. Private/mixed addresses, peer matching, redirect checks, coverage and
scientific acceptance remain strict; unknown resolver implementation failures
still fail the operation. No new schema, ledger or acceptance authority is added.

Web/TUI explain exhausted preparation using existing final-failure evidence.
Before an experiment exists, component progress follows the failed prerequisite
when all proposers exhausted recovery, instead of suggesting a pending proposal.
Later work and eligible peers are not suppressed. Existing Attempt details retain
pre-model diagnostics even without a transcript; source links to hosts with a
recorded metadata-address rejection are withheld while metadata stays inspectable.

Final bounded selection: **231 passed**, one native pool transport deselected;
monitor dependency/phase/Textual selection: **10 passed**. Node syntax and smoke
checks exercise the actual renderer functions, failure-detail action, later-work
refresh, download diagnostics and rejected-host links. One scripted Host root
reaches an accepted hypothesis through Grounding and expert review, then closes
through ordinary CANCEL: independent census finds **8 CLOSED/SUCCEEDED Agents**,
one hypothesis, no Processes/open effects and a clean Oracle. No paid calls,
external literature lookup, actual CPU/GPU experiment or scientific-quality
claim. Read-only retained-run projection confirms the corrected failed phase.

Limits and initial results are retained: an early broad catch hid two unknown
resolver errors and was narrowed; the first scripted Host test waited for a
tool-driven experiment that its stub cannot submit, so the final bounded test
asserts accepted proposal then cancellation. A native pool socket is denied by
EPERM. Four Chrome cases cannot start the browser. A wider monitor selection was
interrupted after native-environment failures; a separate native compile/Process
case fails identically on published dev (`7e74c72`) because bwrap cannot create a
NETLINK_ROUTE socket. These are not passing native/browser checks or full G1/G5.
Evidence: `.local/qualification/literature-isolation-20260929/`; original diagnosis:
`.local/analysis/affective-literature-failure-20260929/`.
Production runs, Profiles and frozen bundles remain unchanged. Monitoring can be
reopened from new code; runtime fixes apply to new runs and do not reopen a
retained terminal node.

## Current repair — Global task ownership in observers, 2026-09-29

Grounding carried an expert transport role, so Web/TUI attributed its activity,
component phase, sessions and usage to that expert. The shared observer projection
now derives task ownership from existing task kinds and reader parent references.
Run-wide preparation, management and judging have a compact global area; experts
retain their research and advice. Unknown historical ownership remains explicit.
Execution roles, selected models, admission budgets and frozen bundles are unchanged.

Existing Attempt accounting partitions global, expert and unknown observations
without changing run totals. Session and reliability filters distinguish ownership
from execution role. Grounding sharing uses accepted output and actual subsequent
request references; it never claims the model read, understood or adopted an input.
No persistent authority, schema migration or new runtime task is introduced.

The combined backend, HTTP, Textual, observation and documentation selection passes
**194 tests**. A final ownership/component-phase/documentation selection passes
**17 tests**, overlapping the first selection. Browser coverage initially passes
**25 of 27** cases; two existing assertions expected the former direct-history
shortcut and execution-role filter. Updated assertions exercise the expert drawer's
history action and the owning-expert filter. All **five** affected final browser
cases pass, including global sharing refresh, filtered history, ordinary expert
activity and mobile reliability controls. JavaScript syntax and diff checks pass.
Earlier sandbox socket failures and an interrupted monitor selection are not counted
as passes; subsequent complete selections succeed.

Read-only inspection of a retained run confirms global Grounding separated from
three experts and the same accepted response actually supplied to all three. No
production operation or model call was made. Evidence stays in
`.local/qualification/global-task-observation-20260929/`; this is observer-focused
validation, not a new scientific-quality or full G0–G5 promotion claim.

## Current repair — Dispatch, context retries and token observation, 2026-09-29

Read-only inspection of `MERC-causal-202609282242` found a ready analysis node
without an Attempt, while an execution controller waited for its owned Process.
`_expert_agent_step` counted that waiting visit before actual admission. Once the
tick deadline passed, ready peers could be skipped on every tick. Counting only
committed Agent admission fixes this without changing concurrency or budgets.
An isolated ACI-28 Host regression fails with the original counter placement
and passes after the repair; the waiting experiment is not submitted twice.

Two retained timeout continuations had 268324 / 270967 request bytes despite
measured token headroom. Their new relays lacked the previous usage anchors and
refused the restored requests. Read-only decision replay confirms both formerly
resumed and now reconstruct directly, retaining code and archived progress.
A third Observer refusal followed a large new thinking block; its subsequent
retry succeeded. Conservative admission remains in force. One active single-GPU
Process explains the idle second GPU; no resource-placement defect was established.

Web/TUI, final-report guidance and the comparison table use token usage.
Historical monetary metadata and explicit financial budget enforcement remain
intact. The trend panel now explains absent numeric GOAL observations and plots
LOCAL measurements separately by hypothesis and recorded declaration. It never
turns missing values into zero or assigns common GOAL targets to local screening.
Boolean-only evaluations remain inspectable without suggesting a pending score.

Focused selections pass: **135** session/context/projection/TUI checks, **62**
report/comparison/support/reliability checks, **26** browser interactions, **5**
Host tests (including four existing CPU closure/iteration/restart cases), and
**15** documentation/projection checks. These selections overlap. Later focused
browser checks cover token-only command details and boolean-only measurements.
Early test assertions needed terminal-wrap normalization and asynchronous refresh
waits. A separate CPU census rerun was blocked at fixture creation by read-only
`/var/tmp`; no independent final CPU residue census is claimed. An isolated
Chrome startup also failed; subsequent normal browser launches succeeded.
This is focused repair validation, not full G1/G5, CUDA or paid model evaluation.
Evidence stays in `.local/qualification/dispatch-context-ui-20260929/`.
Production runs, Profiles, pool bindings and frozen bundles were not changed.
Reopen monitoring from the updated checkout to load the new Python projection;
Host behavior repairs apply to newly created runtime bundles.

## Current repair — Partial context telemetry in TUI, 2026-09-29

The retained run's existing `diagnostics/host.jsonl` records two TUI crashes at
2026-09-28 23:59:16 and 2026-09-29 01:17:55 +08, both `KeyError: 'read_calls'`.
CLI stream observations can contain compaction counters before a readable
transcript supplies Read counters. The renderer incorrectly treated the first
as proof of the second. Textual then exited, closing its owned Web listener.
The independent research Host remained active; its service had zero restarts.

The fix reads optional telemetry independently through the existing display
helpers: missing counts show `?`, observed zero remains `0`, and available
compaction information stays visible. It does not fabricate counters, change
context management, modify the running Host or add a diagnostic store.
An isolated regression using the real stream/transcript projection reproduces
the original exception before the fix. Afterward, alternating missing, measured
zero and stream-only observations pass through the real Textual refresh loop
while authenticated loopback HTTP requests continue returning 200 from the same
Web server. Normal exit releases the port; research database row counts remain
unchanged. This is bounded UI/HTTP qualification, not a long-running browser soak.
Final focused validation passes **94 tests** across monitor, live-status,
context-observation and Web suites. A first post-fix assertion needed whitespace
normalization for terminal line wrapping; production code was unchanged by that
fixture correction. The original crash reproduction is retained separately.
Evidence stays in `.local/qualification/tui-partial-context-20260929/`; original
production diagnostics remain in place. Production runs and Profiles are unchanged.

## Current development — Research workflow skill, 2026-09-28

ACI 28 / `claude-code-bwrap/67` implements the
[shared workflow design](design/research-workflow-skill.md). One public source
seeds owner-local Grounding and expert plugins; native instructions select the
actual bound name and current stage. Initial Grounding gains read-only access
without changing its execution role, model or budget. Existing Git evolution,
pin/revert/unpin, validators and scientific acceptance remain authoritative.
No new memory service, timing instrumentation or retained-run migration is added.
The [guide](guides/research-workflow.md) explains usage and evolution boundaries.

Final G1 coverage is **6403 passed / 26 opt-in skips across all 6429 collected
test identities**. The frozen full run stopped at 6263 passes, one failure and
26 skips: a native permission fixture exceeded the Unix socket path limit under
the default long pytest root. Using a short temporary root, all 142 selected
failed/unexecuted cases pass; their union covers every collected identity.
Original failures remain in the private evidence. An earlier full run exposed
a real monitor regression: initial Grounding's plugin owner can differ from its
execution role. The repaired observer recognizes only the precise initial task,
preserves other ownership checks, and passes the final full coverage above.
Sandbox-denied preliminary native invocations are not counted as passes.

Focused qualification exercises common seed contents and byte limits, custom and
retained plugins, stage routing, clarification/retry bindings, original model
selection, read-only mounts, unchanged/changed evolution, interrupted updates,
owner isolation and existing version controls. A native CLI sample actually
loads an evolved common entry and its Grounding reference. Three task-specific
parsers execute in isolation with three semantic cases each: classification
maximization, constrained numerical minimization, and artifact reproduction.
These are scripted compatibility cases, not evidence of model scientific quality.
Final source and wheel/sdist contain the same seven common skill files.

Independent final census covers seven real Host/native CLI roots: **211 Agent
Attempts and 18 real CPU Processes**, all CLOSED. Creation, preregistration,
canonical outcome and publication counts agree, with clean Oracles and no
duplicate outcomes, pending effects, live owned groups or sockets. This includes
Host restart, plugin evolution, Grounding reconstruction, same-task continuation,
resource-skill learning and multi-expert advice. Native Agents use the local
scripted provider; CPU execution and Host lifecycle are real. Original failure
records and the final native roots are preserved separately.

The finite real-model comparison uses the existing CC connection and selected
`mimo-v2.6-pro` only for Grounding. It compares current code with common guidance
disabled against the same code with the skill, not a complete old-release replay.
Task, supplied evidence, model and budgets match: at most two Grounding calls per
arm, 40 turns, 3000 seconds and CLI USD 2 per call, with USD 8/9300 seconds allocated
per arm. Other Agents use the native scripted provider and one experiment per
arm executes on CPU. Quality requires four independent parser cases and a
0.75/0.75 CPU result through collection/publication, plus clean closure. CLI
estimates have unknown model pricing and are not verified vendor billing.

Both arms pass in one Grounding invocation each, including all four semantic
cases and the expected CPU result. The skill arm actually invokes
`hypoloom-grounding:research-workflow` and reads its Grounding reference.

| Arm | Grounding seconds | Native tool calls | Reported tokens | CLI estimate USD |
|---|---:|---:|---:|---:|
| Common guidance disabled | 999.91 | 23 | 602504 | 0.8140256 |
| Common skill enabled | 609.37 | 15 | 384627 | 0.7271608 |

The skill arm has one failed directory listing of an original target path that
is not mounted there; it proceeds using the mounted workspace and succeeds.
Both arms have zero observed native compactions, local context refusals and
provider context failures. Tool-error counts do not count every scientific
correction, and exact reread counters miss Bash reads. This single pair is a
positive observation, not a causal or repeatable speed/cost claim. Total
Grounding usage is **987131 reported tokens / USD 1.5411864 CLI estimate**.
Independent paid-root census closes **22 Agents and two real CPU Processes**,
with matching publication counts, clean Oracles and no process/socket residue;
only two Grounding Agents contact the real provider. No extra paid retries or
model samples are added after this comparison.

Evidence: `.local/qualification/research-workflow-20260928/`; original run
diagnosis: `.local/analysis/grounding-review-20260928/`. Existing research runs,
private Profiles and production GPU pools are unchanged. This does not establish
CUDA, cross-provider generalization, repeatable speed/cost gains or full G5
scientific quality.

## Current development — Monitor re-entry, 2026-09-28

The `/autoresearch-monitor` shortcut and retained `/autoresearch monitor` share
read-only discovery, the existing source validator and tmux/terminal launcher.
Confirmed init starts return complete observer commands. No run registry,
CC history injection or changes to existing research runs are introduced.

114 focused checks pass, covering real Host-created SQLite state, a fresh helper
process, duplicate IDs, corrupt stores, external roots, quoting, launcher behavior,
bootstrap output, runtime bundles and bilingual documentation links. Two native
Host socket tests are excluded because this sandbox denies Unix socket bind;
they are not counted as passing. tmux dispatch uses the existing test executable,
and native CC completion UI has not been exercised. This is not a full G1/G5 claim.
Local read-only discovery found 20 stores without warnings in one 0.2302-second
sample; Host liveness remains explicitly unchecked. No provider calls or real
monitor launches were made. Evidence: `.local/qualification/monitor-entry-20260928/`.
Usage is in the [monitoring guide](../USAGE.md#tui-web-and-human-decisions).

## Current development — Init pool lifecycle and latency, 2026-09-28

The [init lifecycle design](design/init-managed-gpu-pool-lifecycle.md) is implemented
with aggregate inspection, generated launch arguments and reviewed managed-pool
preparation. Existing validators, databases, runtime pinning and controller locks
remain authoritative. Directory flock coordinates service maintenance with new
Host admission; its concrete race and minimal-mechanism justification are in the design.

The focused set passes 232 checks, including real SQLite history, process groups,
cross-process locking and the real Host CREATE_RUN path. Service supervision and
pool transport use test adapters. Nine native socket cases cannot complete in this
sandbox: Unix socket bind is denied (EPERM), and the user systemd manager is not
reachable. Those checks were not weakened or counted as passing. This is not a
complete G1 or native service/CUDA/paid-model qualification.

A synthetic CPU Profile sample reduces prepare output from 13,535 to 1,947 bytes
with `--summary`. Aggregate inspection reports its supervisor blocker in 0.0904 s
in-process / 5,593 bytes without creating a state root or pool. Single sequential
samples include cache differences and do not establish end-to-end CC speedup.
The skill entry shrinks from 1,795 to 191 lines, with detailed references loaded
on demand. Evidence: `.local/qualification/init-lifecycle-20260928/`.
Existing production pools, research runs and private Profiles remain unchanged;
verification made no provider calls. The user subsequently authorized commit/push to dev.

## Current development — Runtime readiness and context recovery, 2026-09-28

ACI 28 / `claude-code-bwrap/66` / lifecycle v10 implements the
[readiness and recovery design](design/runtime-readiness-and-context-recovery.md).
Init and Host creation share pool capability/membership validation before Agent
launch. Compatible pools may cover a selected subset. Managed creation reuses
the existing service authority and refuses automatic replacement, including a
stopped pool with an existing database or an unresolved named-pool conflict.

Pre-submit infrastructure faults use existing command transitions and Attempt
diagnostics. The Host closes the affected Agent technically, prevents CPU bypass
and waits for an authenticated operator resource recheck. Stop, cleanup failures,
remaining budgets and already submitted experiments retain their precedence.
Restart recovery, idempotent controls, native Stop, Web/TUI and the independent
Oracle are covered. No extra fault ledger or allocation authority is introduced.
The [operator guide](guides/runtime-recovery.md) describes recovery and limits.

Context estimates retain complete provider usage across narrowly matched text
appends and known cache/text representation changes. Six original local refusals
replay from **364635–668006** conservative whole-request estimates to
**96507–173044** usage-plus-delta estimates and fit the unchanged policy. Five
original requests were CLI summary requests; this replay establishes structural
admission, not their unobserved provider outcome or exact token counts. Unknown
changes still fall back conservatively. Native compaction starts/completions,
local refusals, provider failures and incomplete responses remain distinct.
Process observation reports actual grants, owned liveness and log activity with
unknown scientific progress. Unknown CLI prices are unverified estimates.

Evidence remains in `.local/qualification/runtime-readiness-20260928/`.
The initial full G1 was interrupted after **6075 passes, 46 failures and 26
skips**: source edits during that run triggered the pinned-runtime protection.
Its incomplete report is retained. Actual fixture compatibility corrections
update version expectations, status inputs and a pool-outage test to inject the
fault after successful creation; **135 follow-up checks pass**. The candidate
is frozen for the complete G1 and subsequent finite model evaluation.
That G1 completes with **6321 passes, two failures, 26 opt-in skips and 30
warnings in 1272.28 seconds**. One TUI fixture supplied CREATED for a control
that correctly requires ACTIVE. The other exposed a real regression: dynamic
log ages in completed experiment records changed frozen CYCLE_REPORT inputs and
prevented eligible same-session retry. Live activity now applies only to OPEN
Processes; closed records retain stable scientific inputs. The original native
failure remains, and focused final-code validation follows rather than repeating
the whole suite. Static identity/lifecycle checks pass **20 tests**.

Independent initial census also caught a fixture that selected a CPU grant but
still generated a CUDA script; its missing-torch execution failure was correctly
collected. The fixture now generates an actual CPU implementation and explicitly
requires Process and outcome success. Its original root is retained. The
resource-recovery/doc follow-up passes **18 tests**. Native CLI recovery already
demonstrated successful CPU collection independently of that fixture.
Final-code native/control/context/documentation validation passes **93 tests in
111.75 seconds**, including both G1 failures. The native report repair again
resumes the same session and publishes canonical memory. An independent census
of three final-code roots closes **56 Agent Attempts and four successful real
CPU Processes**: two native CLI roots account for 40 Agents/three CPU Processes;
the scripted-Agent root accounts for 16/one. Creation, preregistration, canonical
outcome and publication counts agree; all three Oracles are clean, with no OPEN
Attempts, duplicate outcomes, pending effects, live owned groups or sockets.
The resource-fault native root records one technical Agent failure, one block,
one recheck, one accepted CPU result and one hypothesis assessment. Synthetic
GPU pool coverage does not execute CUDA. Original and final censuses retain
their own root paths.

The real-model protocol uses the existing CC connection and selected MiMo model
only for Grounding: at most two invocations per arm, 40 turns, 3000 seconds and
CLI USD 2 per invocation, USD 8/9300 seconds allocated per arm. Other Agents use
the native local-provider fixture and the experiment executes on CPU. A normal
arm is compared with an arm reading a synthetic reference file and requesting
earlier native compaction through an inspected CLI-only evaluation option.
Compaction coverage requires an actual completed native event; quality requires
four semantic counterexamples and one candidate scoring 0.75 in both fixed
suites through Host collection, publication and assessment. Allocations and CLI
estimates do not establish vendor billing or repeatable savings.

Both **mimo-v2.6-pro** samples pass in one Grounding invocation each, including
all four semantic cases, the 0.75/0.75 CPU result, collection/publication and
Oracle closure:

| Arm | Grounding seconds | Completed provider messages | Native compactions | Reported tokens | CLI estimate USD |
|---|---:|---:|---:|---:|---:|
| Normal | 419.09 | 22 | 0 | 767520 | 0.8499168 |
| Earlier native compaction | 742.81 | 19 | 3 | 432588 | 1.3669376 |

The summary arm actually uses `message_text_appended` for three requests. Both
arms have zero local capacity refusals and zero incomplete forwarded message
requests. Three compactions take 332194 ms in total. Additional reference input
and the evaluation-only earlier threshold make these different workloads;
neither the token difference nor the higher estimate establishes a general
efficiency result. Keep production defaults unchanged. Combined Grounding usage
is **1200108 tokens / USD 2.2168544 CLI estimate**, with unknown model pricing.
The paid-root census closes **22 Agents and two successful CPU Processes** with
clean Oracles and no process/socket residue; only two Grounding Agents use the
real provider, while the other 20 use the local scripted provider.

The real sample also exposes bounded-observation limits: the complete transcript
contains three compactions while the stdout tail retains only two start events.
A final observation-only repair matches pending starts to completions in the
same stream window, preserves unknown earlier coverage and shows transcript/start
coverage separately in Web. It does not change generation, admission or recovery.
Raw paid states remain unchanged; read-only replay and focused browser/context
checks validate that final display/counting change: **63 tests pass in 68.34
seconds**, including an explicit truncated-window counterexample and the shipped
browser display. No further behavioral change follows these checks.

Existing research runs, personal Profiles and the production pool remain
unchanged. This scope does not authorize CUDA, service replacement, cross-version
run migration, broad G4/G5 promotion or a claim of long-horizon scientific quality.

## Current development — GPU capacity and resource replanning, 2026-09-27

ACI 27 / `claude-code-bwrap/65` implements the
[capacity and replanning design](design/gpu-capacity-and-resource-replanning.md).
The existing measured-budget calculation now explains admission and selects
25%, 15% or 10% headroom from comparable completed Attempt evidence. Calibrated
margins have a 512 MiB minimum; actual OOM budget growth remains independently
25%. Unknown workloads request exclusive pool admission. The existing pool
transaction enforces that condition in both directions and prefers empty GPUs.
No additional measurement store or calibration marker is introduced.

Read-only previews and submission use the same capacity arithmetic. A reliable
capacity upper bound distinguishes impossible requests from temporary pressure;
unknown telemetry does not become a guessed fixed reserve. Impossible queued
work releases its existing pool request, closes as a technical execution failure
and wakes EXECUTION through the existing handoff. Replacement submissions retain
the original scientific constraints and budgets. Web/TUI explanations and the
existing failure event expose the reason. See the [guide](guides/gpu-resource-planning.md).

Evidence is retained in `.local/qualification/gpu-replanning-20260927/`.
Focused tests exercise the 20288/20024 MiB reproduction, multi-device and unknown
capacity, queue progress, measured calibration, concurrent exclusive admission,
NVML reserve fallback, technical closure, replacement, and two Host crash/restart
points. A separate pre-exec capacity-loss case covers a prior unstarted Attempt
before resource revision. Tests count the single failure event, closed Attempts,
released pool requests and accepted replacement result with the existing Oracle.
Browser checks use the shipped JavaScript and distinguish absent measurement
from measured headroom. Native local-provider tests execute real CLI/CPU work;
synthetic GPU tests do not execute CUDA kernels.

Independent read-only censuses verify five new simulated-GPU Host roots:
**79 Agent Attempts, 11 Process Attempts and 11 released pool requests**. Two
native resource roots close **42 Agent Attempts and four actual CPU Processes**,
including resource-tool use and next-cycle Skill evolution. All seven roots have
matching experiment creation/outcome/publication counts, clean Oracles, no open
Attempts, duplicate outcomes, pending effects, live owned groups or sockets.
The synthetic Process count includes technical/pre-exec closures and is not a
count of GPU executions. Census outputs retain the original test-root paths.

The initial focused run retains **577 passes, two failures and two skips**.
Both failures were old uncalibrated co-location expectations: those scenarios now
explicitly retain ACI 26, while ACI 27 independently requires exclusivity and
release before another uncalibrated job starts. The follow-up has **268 passes**.
The first browser fixture lacked resident-Host status; the corrected fixture
passes without changing the observer's offline behavior.
An accidentally serial full invocation was deliberately interrupted after
715 passing tests and 787.85 seconds, then restarted with eight workers using
the same selection. Its partial log, XML and interruption residue check remain;
the interrupted test has no live owned process group or kernel socket.

Full G1 then completes with **6288 passes, four failures, 26 opt-in skips and
30 warnings in 1209.28 seconds**. The original failures remain. One exposes a
duplicated node-projection version allowlist missing ACI 27; it now reuses the
existing readable-version set, with current/legacy/unknown-version regressions.
Another rejects a direct domain-validator import into the Oracle. The final
calculation separates typed admission from pure numeric policy, keeping the
Oracle's independent raw-source validation and the original import prohibition.
Two fixture assertions now expect the additive wait message and the actual
current Grounding version. No assertion was weakened to accept a bad outcome.
The final unit selection passes **152 tests**; the final Host/Oracle/observation
and native-CLI follow-up passes **149 tests in 151.29 seconds**, including the
original Grounding failure. All four G1 failures are resolved. This bounded
repair follows G1; the full suite was not repeated on the final bytes.
The final-code census repeats the five simulated-GPU roots and includes three
native roots: **79/11 simulated Agent/Process Attempts, 53 native Agent Attempts
and five real CPU Processes**. All eight roots close cleanly with matching
creation/outcome/publication counts. Seven isolated pools are clean; all 11 GPU
requests are released, and both native CPU pools have no requests. Independent
process-group and kernel-socket checks find no residue. Final evidence is in
`final-followup.xml`, `final-census.json` and `final-census-summary.json` under the
same qualification directory.

Existing research runs, personal Profiles and the running shared pool remain
unchanged. New GPU runs require a compatible pool advertising
`resource_replanning/1`; old pools cannot silently ignore exclusivity. No paid
model or actual CUDA experiment was run for this repair. These results establish
framework behavior, not an OOM-rate improvement, an optimal headroom percentage,
model repair quality or full G4/G5 qualification.

## Current development — reading tools and parallel proposals, 2026-09-27

ACI-26 / `claude-code-bwrap/64` implements the
[reading/proposal plan](design/tool-guided-reading-and-parallel-proposals.md).
Document preparation and reader admission share the caller's existing source
lookup. Tool errors identify unsupported fields and explain the `source_ref` to
`paper_ref` handoff. Submission still validates the whole batch before admitting
any readers. No separate reference tool or implicit download is introduced.

The Host can run the exact same proposal's FORWARD and BACKWARD tasks together,
subject to ordinary capacity and budgets. Both retain the original FORWARD-bound
Leader advice. SYNTHESIS requires both accepted responses; retries retain a
successful sibling, while final failure cancels the remaining native process.
Other stages, duplicate tasks and unrelated cycles do not gain this overlap.
One slot and retained ACI-25 proposals remain serial. Guidance explains independent
mechanisms and legal single-candidate choices without increasing fork defaults.

The existing read-only summary distinguishes proposal tasks, admitted reading
commands, reader Attempts and scientific chains. It uses the actual request's
candidate limit, shared reading-state logic and already-read tool observations.
Incomplete tool traces remain lower bounds; wait reasons are historical, and
unknown CLI prices remain estimates. No additional state ledger or transcript
scan is required. See the [user guide](guides/reading-and-proposals.md).

Evidence remains in `.local/qualification/parallel-proposals-20260927/`.
The final full G1 invocation passes **6275 tests, with 26 opt-in skips and
30 warnings, in 1095.45 seconds**. The initial 6261-pass/13-failure invocation
is retained: it exposed an omitted ACI-26 reader registration, nullable review
contexts in the new join Oracle, stale version/error-message assertions, an
incomplete mock Host and an invalid fixture assumption that sequential retries
cannot resume their own session. The 289-test follow-up and actual native retry
pass before the final full run. A separate failing counterexample also repairs
active-chain deduplication using CREATE_EXPERIMENT's actual `expert_cycle` field.
Neither strict join/source checks nor session isolation is weakened.
After the full invocation, the only runtime edit changes the Web's zero-candidate
wording to `0 accepted`, because a rejected submitted proposal is not proof of
no submission. The final browser/summary/document selection passes **61 tests**;
the realistic closed-wings/rejected-SYNTHESIS case separately passes. The full
G1 was not repeated after this presentation-only change.

Focused Host tests cover either completion order, one slot, retained semantics,
retry, successful-wing recovery, late output after failure, other experts and
two CPU cycles. Native CLI/local-provider tests cover actual process overlap,
different sessions, complete/retry/exhausted/stop/restart paths and the two
original malformed reading calls followed by three readers. The independent
six-root native census counts **80 Agent Attempts and four CPU Processes**,
all closed, with exact creation/outcome/publication counts, clean Oracles and
no duplicate outcomes, pending effects, live owned groups or sockets. Original
fixture failures remain separate, including the terminal-state expectation,
missing stop cause and source editing during a pinned native invocation.

G2 compares five stopped retained ACI-19/21/22/23/25 roots against the Git baseline.
Selected monitoring/count/accounting values and full Oracle output are identical;
database size and modification time remain unchanged. Four Oracles are clean.
The ACI-21 root's existing `session_archive_missing` issue is identical before
and after. This comparison does not migrate or qualify a restart of old runs.

Finite real-model probes use the existing CC MiMo connection, scripted
prerequisites and isolated roots; no existing research run, personal Profile,
GPU or public notification is used. Each selected invocation allows 24 turns,
150000 enforced tokens, 600 seconds and USD 2; each root has a 2100-second deadline
and zero model/infrastructure retries. Reading permits five selected calls;
each proposal arm permits three. Reading deliberately elicits the known malformed
forms against synthetic original documents, so it measures compatibility rather
than a natural error rate or public-web reliability. Successful probes stop
through CANCEL after the selected phase, before experiment design; they are not
whole-topic G5 qualification.

The initial serial sample correctly fails budget admission: its private driver
retained a fixture role cap of 200000 tokens, leaving BACKWARD only 107303 tokens;
that invocation reports 116588. FORWARD succeeds with 92693. There is no SYNTHESIS,
no context refusal and no leaked process. The failed root is retained and excluded
from matched timing comparisons. A documented protocol correction explicitly sets
750000 role tokens for a fresh finite cohort while keeping invocation limits and
production defaults unchanged. Zero-paid routing/closure controls pass. Declared
allocations are USD 6 for the original sample and USD 22 for the corrected cohort,
not vendor billing; no corrected failure is automatically rerun.

The corrected serial sample accepts FORWARD and BACKWARD, but rejects SYNTHESIS
as `invalid_structured_output`: its candidate omits the required
`parent_hypothesis_id` and `relation` fields (both must be explicit null here).
The proposed single mechanism is not an accepted hypothesis. With zero
model retries, the sample ends without a scientific fact; 261412 reported tokens
and CLI-estimated USD 0.5590144 are retained, with a clean Oracle and no OPEN
Attempts or owned process residue. No acceptance rule is relaxed to pass this
sample. The operational `COMPLETED` state in this explicit-signals fixture does
not assert goal success or scientific completion.

The corrected parallel sample accepts both wings and one SYNTHESIS hypothesis
in **426.39 seconds** (319686 reported tokens; CLI-estimated USD 0.6633936).
Both native process groups are observed alive together with different sessions,
scopes and branches; durable Attempt intervals overlap for **109.14 seconds**.
The accepted claim links the forward invariance argument to a backward paired
baseline test, preserves the frozen split/seeds/threshold, and explains the
single-candidate choice by the lack of evidence for distinct mechanisms. Its
CNN and augmentation assumptions are explicitly unverified; the combined recipe
does not isolate individual factors. No actual scientific experiment runs in
this probe. One accepted sample establishes bounded compatibility, not stronger
scientific quality, repeated diversity, or speed/cost improvement over the failed
serial sample. Both corrected roots have clean Oracles and no OPEN or live residue.

The first real reading sample corrects both malformed calls, prepares both
originals and admits two readers; the parent's tool replies contain both results.
Both readers report
the exact **0.83 coefficient / 5-turn window** and the stated limitations.
However, MAIN consumes 165377 tokens, and the next request is locally refused
by its 150000-token invocation limit before further model synthesis. Its outer failure is `nonzero_exit`, with
the specific `provider_failure.code=token_budget_exceeded`; the current input
estimate fits the context window. MAIN is not accepted. The private objective
also incorrectly demanded physical pages for HTML: those page claims do not
qualify source-location accuracy. All four selected invocations close cleanly;
this is partial tool compatibility, not a completed literature phase.

One separately declared reading follow-up keeps the USD 10/five-call/2100-second
root and USD 2/24-turn/600-second invocation bounds, raises only its private
invocation/role/run token limits to 300000/1500000/2000000, and requests HTML lines
or PDF physical pages as appropriate. Total declared allocation across original
and follow-up real roots is USD 38. Original failures remain unchanged; this is
not a matched performance experiment or a product/Profile budget change.

The follow-up completes the literature phase in **383.83 seconds**: both readers
and MAIN are accepted, with accurate 0.83/5-turn values, limitations and HTML
extraction line references. MAIN separates the synthetic evidence from the
untested pilot goal. It uses 153023 tokens; all four real invocations total
228323 reported tokens and CLI-estimated USD 0.4868184. Across both real reading
samples, the model emits an empty `readings` array and a URL-as-reference error;
it does **not** emit the requested unsupported `wait_for_completion` field. The
second sample additionally places `max_wait_seconds` in its empty submit. Thus
the exact unsupported-field replay is covered by native scripted-provider tests,
while real-model evidence covers empty-batch and URL-reference correction,
delegation, original reading and successful synthesis. It is not a natural error
rate measurement or evidence that every malformed form will be repaired.

The five real roots independently close **37 Agent Attempts**, with clean Oracles,
no OPEN Attempts, duplicate/pending effects, live owned groups or sockets. They
run no scientific CPU/GPU experiments; that coverage is in the native/CPU tests.
All real samples together report **1259537 tokens** and CLI-estimated
**USD 2.6614696**. MiMo pricing is unknown to this CLI, so these dollars are
fallback estimates, not vendor billing or a spend guarantee. The first reading
root also retains one locally refused, zero-forwarded request as an unmeasured
observation. No cross-provider, sustained-run, GPU, scientific superiority or
repeatable speed/cost claim follows from these finite probes.

## Current development — measured context and task continuation, 2026-09-26

ACI-25 / `claude-code-bwrap/63` repairs the reproduced ACI-24 Grounding
regression. Unchanged request history uses complete observed usage plus bounded
new-text estimates, or an already available matching provider count. Unknown
input remains explicitly estimated; independent byte/time/request/total-token
and cost limits remain. Current-request estimates and last successful measured
input have separate exchange identities in monitoring and support exports.

The existing OPEN session binding admits eligible same-task correction or
transport retries, with complete archives, unchanged sources and capacity
headroom. Capacity/compaction failure, new tasks and connection reloads use fresh
sessions. Existing Git, source publications and tool transcripts provide bounded
continuation observations and Grounding draft locations. The optional capacity
advisory reuses the native output socket without granting tool permission or
accepting output. No new fact store, checkpoint or mandatory paid summary exists.

Evidence stays in `.local/qualification/context-regression-20260926/` and the
capacity-specific `.local/qualification/context-capacity-20260926/`. The original
report ZIPs and historical runs remain at their original paths, read-only.
The four complete request/usage pairs recoverable from the two private reports
all fit the unchanged 205568-token input allowance after measured-prefix
projection. A read-only replay of 17 successful historical GLM/MiMo Attempts
covers 235 requests: 82 formerly refused low-usage requests project within the
limit, but only 38 are reachable before the first refusal latch in that replay.
All 81 genuinely above-limit observations remain refused. This is not evidence
that 82 whole requests or 17 new-model tasks would now complete. Four historical
count responses do not match the following complete input and are not borrowed.

The native tests use the real Host/CLI and a scripted local provider; their
synthetic usage proves the mechanism, not real-provider token accuracy. The CLI
also moves known ephemeral cache annotations across multi-text and tool-result
blocks. Four failing measurement regressions preceded the narrow normalization fix; actual wire content, order, tool identities and thinking
signatures remain unchanged. The original long-discovery ninth request had
214575 input bytes; the repaired projection uses 16786 estimated input tokens
and forwards it. Actual Grounding performs 14 source reads, preserves three
drafts and 12 bounded tool observations, then reconstructs, reads and repairs
the evaluator without repeating discovery. The accepted evaluator produces
0.75 on each suite in a real CPU Process. Ordinary MODEL correction separately
resumes its complete archived session, charges only the new invocation's usage,
and starts subsequent scientific stages with fresh sessions.

The analysis fixture explicitly covers MODEL correction, a resumed first request
refused without a new relay measurement, and one allowed INFRASTRUCTURE fresh
rebuild. It reads the original correction source and completes the existing CPU
result path. Zero infrastructure retries still disables that reconstruction;
no default window, retry allowance or user Profile is enlarged.

Initial focused verification passes 324 checks. Six selected native roots are
independently counted as 67 closed Agent Attempts and six actually spawned CPU
Processes, with clean Oracles and no duplicate outcomes, pending effects, live
owned groups or sockets. Original fixture assertion failures are retained and
identified separately: a wrong terminal-state field, an offloaded diagnostic
incorrectly expected inline, and native CLI outer-usage aggregation. Strict
wire/model-ledger totals, fees, parser checks and scientific acceptance remain.
The final full G1 invocation passes **6,229 tests, with 26 opt-in skips and
30 warnings, in 916.90 seconds** using eight workers and a short socket base.
The original full invocation remains as evidence: 6,199 passed / 25 failed /
26 skipped. Its failures identify the cache-marker regression, old version and
always-fresh expectations, the analysis reconstruction budget boundary, the
existing authority LOC allocation, and two fixture/process-observation races.
The LOC allocation stays unchanged; context observation formatting reuses the
existing context module and Host imports are consolidated without removing
binding checks. All initial non-opt-in identities have passing final evidence.

After the full G1 and paid Hosts exit, the final source change removes one new
same-task Oracle assertion that incorrectly orders Attempts by wall-clock time.
The red regression reproduces a legal CLOSED-to-OPEN retry after clock rollback;
existing transactions, immediate Attempt ordinals, frozen requests and published
archives establish the order. OPEN predecessors and skipped ordinals remain
rejected. This is a focused final Oracle repair after the full-suite invocation,
not a claim that the full suite ran after that last line changed. The final
focused selection passes **137 tests in 27.80 seconds**, including the new
clock-step counterexample and the OPEN-predecessor/ordinal negative cases.
Fifteen earlier native roots are re-audited read-only using the final Oracle:
193 Agent Attempts / 16 CPU Processes close cleanly, with no pending or duplicate
effects, owned groups or sockets; original databases are unchanged.
The final independent selection separately audits ten full-G1 end-to-end roots:
312 Agent Attempts / 25 actually spawned CPU Processes, all closed with clean
Oracles and no OPEN Attempts, pending effects, duplicate outcomes, live groups
or sockets. Together with the two paid roots below, the final census is 12 roots,
320 Agents and 25 CPU Processes. See `final-independent-audit.json`; earlier
fixture failures and deliberately OPEN unit-test roots are excluded explicitly.
Four documentation checks, undefined-name checks and Git whitespace also pass.

Two finite real-model arms use the existing CC `mimo-v2.6-pro` connection, high
effort/adaptive thinking, the same two-suite Grounding task, USD 8 / 900 seconds
per arm and USD 2 / 300 seconds / 40 turns per invocation. Total allocation is
USD 16 / 1800 seconds; only Grounding calls a real model. The baseline has no
injected fault; the rebuild arm would inject one capacity refusal only after an
authored evaluator passes its self-test. Other stages use a scripted local
provider. No configured GLM connection is available; GLM evidence is historical
request replay only. The protocol and results remain in
`GROUNDING-PROTOCOL.md`, `model-cohort-analysis.json` and each arm's
`quality-output.json` under the evidence directory.

| Real Grounding arm | Outcome | Completed request tokens | Unmeasured requests | Accepted evaluator / CPU |
|---|---|---:|---:|---|
| Baseline | Two 300-second timeouts; second invocation resumes | 59292 | 2 | No / 0 |
| Rebuild | Two 300-second timeouts; planned injection never reached | 57069 | 2 | No / 0 |

All ten real generation requests were forwarded and returned HTTP 200; none was
locally refused for capacity. The resumed first requests use byte estimates
156517 / 148863, within the unchanged 205568 input allowance. Each arm completes
seven initial investigation tool calls, then reaches its deadline during a long
thinking stream; the retry produces no new tool action. This establishes bounded
timeout closure and an admitted same-task resume for this configuration. **It
does not establish successful real-model Grounding, post-draft reconstruction
quality, fewer rereads or cost savings.** Both quality checks fail, with no
accepted evaluator, parser semantic checks or CPU result. The long-discovery and
post-draft scientific closure evidence above comes from scripted-provider native
tests, not these paid arms. These original samples retain their failed quality
results; the later user-authorized 3000-second follow-up is recorded separately below.

The four Grounding charges each retain a declared USD 2 / 200000-token allowance:
USD 8 / 800000 tokens is conservative accounting, not observed vendor spend or
actual total tokens. Six completed responses measure 116361 tokens; four
interrupted streams lack complete usage. CLI pricing is unknown in every paid
Attempt and terminal provider accounting is unavailable. Complete native archive
observation does not imply complete provider billing. The two trial runs reach
durable COMPLETED with scientific verdict INCONCLUSIVE, closing eight Agent
Attempts with clean Oracles and no owned process/socket residue.
An initial driver model-routing assertion failed before any provider request;
its original root remains separately identified with one durable OPEN Attempt
and no spawned group/socket, excluded from passing closure counts. A real-CLI,
all-local model-name probe passes before correcting that test driver and running
the same two planned science arms. No personal Profile, existing research run,
GPU resource or public notification is changed. These are bounded development
samples, not whole-topic G5 or long-horizon scientific-quality qualification.

The historical replay is a capacity-only counterfactual, not a full new-session
replay. Some large initial requests lack an anchor, and large appended text can
still be conservatively refused. Earlier complete successful provider usage
does not establish the next request's exact count. Native compaction remains
optional; budgets, archive integrity and scientific acceptance remain binding.
Existing ACI-24 and older runs keep their pinned behavior; no migration or
restart of a retained run is performed.

Qualification belongs to an exact candidate and environment. Hypoloom retains
the existing G0–G5 promotion model, runtime oracles and regression fixtures.
The [test selector](../scripts/qualify.py) runs ordinary pytest selections; its
exit code is a test result, not an aggregate release decision. A skipped lane,
missing retained cohort or unfinished cleanup cannot be recorded as passed.

## Current development — 3000-second Grounding follow-up, 2026-09-26

The user authorizes reevaluation with **3000 seconds per Agent invocation**.
New isolated trials use 9300 seconds per arm, including retry and cleanup
allowance; an external watchdog is 9360 seconds. Existing CC MiMo selection,
adaptive/high effort, USD 2 per invocation / USD 8 per arm, 40 turns and
200000-token observe mode remain. Production defaults, personal Profiles and
retained research runs are unchanged.

The first time-only sample completes Grounding in **1100.23 seconds**, with no
capacity refusal or compaction. It then exposes two private-driver defects:
the task never specified the scripted experiment's per-suite raw label layout,
so the model chose top-level labels and the actual CPU returned zero/zero with
`complete=false`; a report fixture also incorrectly required the nonexistent
ancestor of an explicitly allowed data mount. The root retains its original
failure and one OPEN report Attempt, with no live OS resources. Consistency is
not full closure. Evidence: `.local/qualification/grounding-3000s-20260926/`.

The corrected driver states the existing raw shape in TASK and tests the exact
allowed directory children and hidden private assets while preserving all
original read-only, role and hidden-path protections. Accepted evaluator bytes
are compared directly at the expert mount; no new hash or authority is added.
The old CPU/semantic expectations remain unchanged. A zero-paid positive control
passes; a negative control actually executes report protection and correctly
rejects two 0.5 measurements. Together they close 26 Agents and two real CPUs.
These protocol corrections mean the new pair is not a time-only comparison.

| Corrected real-model arm | Grounding time | Independent semantics / CPU | Completed response tokens |
|---|---:|---|---:|
| Baseline | 888.76 s, one invocation | Four cases pass; 0.75 on both suites | 514987 |
| Rebuild | 387.91 s before injection + 162.72 s after reconstruction | Four cases pass; 0.75 on both suites | 627177 |

The rebuild arm actually injects one capacity refusal after an authored evaluator
passes its self-test. The new session reads the retained evaluator and smoke
input before completing. Both real-model quality checks pass, with 23 Agent
Attempts and two actual CPU Processes closed. All 28 forwarded generation
responses have complete usage, totaling 1142164 tokens including cache reads;
the extra locally refused exchange forwards zero bytes. The observation fold
still labels that local refusal as one unmeasured request, which is not an
unmetered vendor generation. No native compaction is observed.

The independent four-root census closes **49 Agents / four actual CPUs**: paid
23/2, local controls 26/2, without OPEN Attempts, pending/duplicate effects,
owned groups or sockets. Reconstruction uses a distinct session; its third tool
successfully reads the evaluator, and no subsequent Write/Edit or inspected
shell command changes it. The first invocation's complete successful Write/Edit
history reconstructs the same 18358 bytes as the accepted source and actual CPU
parser. Four documentation checks and Git whitespace checks pass. The original
protocol-failure root is explicitly excluded from these clean closure totals.

The corrected pair's CLI estimates total approximately USD 2.5063584; every paid
Attempt reports unknown model pricing. The earlier protocol-failure sample
separately reports USD 1.1803776 and 606728 tokens. These estimates are not vendor
bills. One successful recovery sample establishes bounded draft continuity for
this task, not repeatable cost savings, cross-model quality or long-running
scientific readiness. GLM has no new live sample. Evidence and original failures
remain under `.local/qualification/grounding-3000s-contract-20260926/` and the
preceding directory. No production code is changed by this follow-up.

## Current development — integrated dev submission, 2026-09-26

The user requests commit/push of the accumulated context/recovery/observability,
init consistency, Profile-local credentials and advisory version-check work.
Evidence stays in `.local/qualification/dev-submit-20260926/`.

The complete G1 selection using eight pytest workers records **6,153 passed /
2 failed / 26 opt-in skips / 30 warnings**, in **928.04 seconds**. The native
Bash-policy fixture exceeds the AF_UNIX socket-path limit under pytest's long
default base; the tracked-template test still expects blank authoring credential
fields in its admitted product. The latter assertion now explicitly removes and
checks the four blank fields while retaining exact comparison for all other
template controls. It does not reuse the production projection as its oracle.
With a short disposable base, the affected selection passes **79 checks in
17.56 seconds**, including both original failures, the actual template CPU run,
native Bash denial/allow and credential/admission cases. Production code did not
change after the full G1. Thus all **6,155 non-opt-in identities** have passing
evidence; this is full coverage plus focused resolution, not an originally
all-green full invocation. Initial failures remain in `g1.log`/`g1.xml`.

An initial serial invocation was interrupted after 688 passes to use the existing
parallel testing approach; no live fixture processes remained before restarting.
That partial run is retained separately and is not counted as extra coverage.
Independent read-only audits of 14 completed full-suite roots plus the corrected
template root close **380 Agent Attempts / 32 CPU Processes**. All 15 full Oracles
pass; there are no OPEN attempts, pending effects, duplicate canonical outcomes,
live owned groups or sockets. Actual CPU effects, native local-provider cases,
early capacity refusal, recovery, Leader sharing, managed bootstrap and local
notification lifetime are represented. Raw counters remain in the two census
files; no production reducer is duplicated.

Static compilation, Ruff undefined-name checks and Git whitespace pass. The wheel
and sdist build without dependency downloads; **140 runtime files** match the
checkout and the sdist excludes private Profiles, settings, state and local data.
An isolated no-dependency installation runs its CLI outside the checkout and
correctly reports unknown source freshness for a wheel without Git metadata.
The development commit adds no new paid/GPU or public-notification qualification,
does not edit existing research runs and does not claim formal G0–G5 promotion.

## Current development — version reminders, 2026-09-26

The init path helper now includes an advisory version result; the same checker is
available as `hypoloom version-check`. It compares Git HEAD with an actual
ls-remote response for the configured upstream, with explicit target/offline
options. Package metadata and local source identity are shared with the existing
support export. The authority remains Git; no new manifest, digest, cache, launch
gate or update service is introduced. There is no fetch/pull or research-state
write. Partial clones avoid object inspection, including on older Git that lacks
the no-lazy-fetch environment option. Missing history never proves behind or
divergence. Dirty working trees are independent of matching HEADs.

Evidence: `.local/qualification/version-check-20260926/`. Final focused selection:
**190 passed in 22.13 seconds**, including real temporary local/bare Git repos,
stale tracking refs, known ancestry, fork/renamed branches, detached HEAD, shallow
and partial history, missing Git/remote/branch, private-error suppression, offline
mode, init, CLI and support export. Ordinary before/after observations verify
that refs, objects, index and working files do not change. Real child processes
verify timeout and SIGINT cleanup. The initial SIGINT test reproduced a surviving
Git/helper group; its failing log is retained and the final cleanup regression
passes. Documentation/public-template checks: **7 passed**.

The first actual SSH checks exceeded three seconds and returned unavailable
without blocking either entry. A bounded ten-second diagnostic completed in
4.18 seconds, so the default total deadline was adjusted to five seconds. Final
default CLI/init checks completed in **4.252 / 4.209 seconds** and observed both
tips at `17375953e1577f7a241fbdccba4ec1dfde8456e8`, while correctly reporting
uncommitted changes. Offline inspection completed in 0.340 seconds. All exited
zero; Git metadata and tracked source files were unchanged. These observations
refer to the checked remote branch at that time, not a claim of current global
freshness, release readiness or cross-version run compatibility.

The [bilingual guide](guides/version-check.md) describes status meanings and
manual checks. No full G1, paid/GPU qualification, existing-run migration,
public notification, automatic update or commit/push was performed for this
advisory feature. Normal Host/status/issue-export paths do not query a remote.

## Current development — Profile-local credentials, 2026-09-26

The user asks to keep literature keys and Telegram tokens directly in the local
Profile, without separate env maintenance. Public bilingual templates now have
empty fields in methodology/notify; filled local YAML remains mode 0600 and
Git-ignored. Optional webhook/proxy values follow the same authoring path. Model
authentication remains the existing selected CC configuration. Existing Profiles,
credentials and research runs are not edited or migrated.

The relevant boundary is the existing whole-dictionary product projection into
GoalDefined and Agent inputs. Disk permissions and Git cannot prevent copying
literal values into that projection. Admission removes the fields and derives
the original YAML path through existing `credentials_file` references. The same
private reader handles YAML and legacy dotenv; no copied secret store, new file,
schema version, manifest or authority is added. Existing runtime validators and
private-file protections remain. Mixed service sources, exposed credential data
mounts and populated template/`--set` secrets are rejected; YAML parse errors do
not echo source lines. Actual secrets never enter review command arrays.

Evidence: `.local/qualification/profile-credentials-20260926/`. Final focused
regression: **455 passed** in **47.80 seconds**, including admission, CLI,
bootstrap, private readers, notifications/proxy, runtime consumers and support
export; documentation/public-template checks: **7 passed**. Initial test failures
and corrections remain in the first two logs. These were old wording/field
expectations and new test command-selection/path mistakes; production protections
were not weakened. This follow-up has not rerun full G1 or paid/GPU qualification.

Independent census of the actual bootstrap/CPU and inline-notification CLI roots
closes **11 Agent Attempts / 2 CPU Processes**, with one experiment creation,
one canonical outcome and one publication. Both full Oracles pass; no OPEN
attempts, pending effects, duplicate outcomes, live owned groups or sockets.
Agents are scripted fixtures. The loopback notification test authenticates using
private Profile values despite conflicting ambient values, observes 503 retry,
final delivery and listener release, and scans actual DB/artifacts plus public
and private issue exports for absence of its synthetic token. No real credentials
were copied, no public notification or paid call was made, and no commit/push
was requested. Bilingual README/Setup, Profile/config/notification/init guides
and runtime init instructions describe the same authoring flow.

## Current development — init configuration consistency, 2026-09-26

A provider-free audit reproduced 12 cases of misleading or incomplete init
configuration: Profile models overriding the displayed Host fallback, duplicate
budget declarations replacing 1 USD / 1 hour with template 45 USD / 6 hours,
GPU runtime/selector/pool incompatibilities, lost notification environment,
missing notification credentials, unsupported dashboard/provider settings and
Host notification options absent from bootstrap. Original evidence stays in
`.local/analysis/init-audit-20260926/`.

The failed boundary is prospective configuration compilation and managed launch.
Existing Profile validation, consumer validators, explicit private-file inputs
and ordinary regression tests are sufficient. No hash, launch manifest, receipt,
new Profile version or persistent authority is added. Runtime guards remain.
New templates consolidate time/financial budgets in `methodology.budget`; old
single declarations still compile with a migration warning, while duplicates
are rejected even if equal. Runtime model/effort resolution also supplies the
init explanation. GPU checks reuse Profile selection and Host pool constraints;
API and notification preflight reuse their actual local consumer validators.

Managed notification variables are read from an explicitly selected existing
0600 dotenv file (`--notification-credentials-file`), or Telegram's existing
per-sink file. This is the existing credential-file input mechanism, not a
copied secret store or environment authority. The file replaces only notification
values; direct serve/notify compatibility remains. Output and proxy options are
preserved in the Host argv. Preflight performs no delivery or provider probe.
Bilingual templates, guides and the runtime init instructions are updated.

Focused regression: **346 passed**; initial failures concerned an intentionally
new legacy-field warning and a previously incomplete pooled-GPU fixture, and
are retained. Full G1 records **6,102 passed / 7 failed / 26 opt-in skips /
30 warnings** in **1,162.00 seconds** (`g1.xml`). All seven failures were old
fixtures asserting no warnings while declaring legacy budgets. Only those
unrelated configuration tests switched to canonical-budget fixtures; their
assertions remain unchanged and dedicated legacy compatibility coverage remains.
The two affected modules plus Profile-v4 checks then pass **128**
(`profile-final.xml`), including every initially failing case. Production code
did not change during this qualification. This is full-suite coverage plus
targeted follow-up, not a single clean full-suite execution; counts overlap.
Documentation/public-template checks pass **7**, and static/diff checks pass.

Independent bootstrap census closes **2 Agent Attempts / 2 CPU Processes**, with
one experiment creation, one canonical outcome and one publication; no duplicate
outcomes, pending effects, OPEN attempts, live owned groups or sockets, and the
full Oracle passes. This exercises the actual CLI Host with a scripted Agent,
not a paid model. Both actual-CLI notification cases complete: explicit private
file and direct-serve environment compatibility, receiver authentication,
503 retry, final delivery, cursor progress and shutdown/port release. No test
credential appears in observed logs. Evidence, including original failures,
stays in `.local/qualification/init-consistency-20260926/`.

Read-only MERC-causal admission passes with only legacy wall-clock/timeout
migration warnings. Existing runs, local Profile contents and user settings
are unchanged. No paid/GPU qualification, public notification, commit or push
is requested for this repair.

## Current development — context observations and measured recovery costs, 2026-09-26

The follow-up fills gaps in the core context/recovery delivery: native CLI
compaction events, successful exact-range frozen-input rereads, prompt material
statistics and disjoint initial-failure/recovery costs. These are read-only
projections of existing transcripts, diagnostic captures and `fold_agent_attempts`;
they are not another ledger or scientific authority. Missing/partial coverage and
unknown billing remain visible. Byte estimates and provider readings are shown
together, with admission reservations. Internal CLI cost/latch records are not
misclassified as incomplete reading events; timestamped events are scoped to the
current invocation, including resumed legacy sessions.

A reproduced complete first generation request, locally rejected at the existing
minimum inline allowance with no forwarded generation, cannot be improved by
unchanged relaunches. Its existing provider diagnostic now ends that retry class
with an actionable explanation. Missing capture, provider-side overflow and
capacity after tool progress retain bounded recovery. Archive, stop and budget
precedence is preserved. No new recovery state machine, receipt or run migration.

Evidence lives in `.local/qualification/context-observability-20260926/`.
Focused/browser checks pass **176**; native CLI/CPU and new observation regressions
pass **12** after retaining an initial partial-coverage counterexample. An
additional native first-request refusal case passes with nine infrastructure
retries configured: each node ends at its third Attempt/minimum inline allowance,
no generation reaches upstream, and the failed run closes with a clean Oracle.
Static,
physical-budget and documentation checks pass **35**. Three retained GLM archives
replay actual pre/post compaction inputs and durations without changing the files.
The first frozen-runtime G1 passes **6,089 / 26 opt-in skips / 30 warnings** in
2,045.68 seconds (`g1.xml`). The real-model observations then justified exposing
the existing CLI unknown-pricing flag in Web/TUI/support metadata without
rewriting charges. A separate counterexample showed wall-clock ordering losing
a later recovery charge after a clock step; logical stages and cost aggregation
now use existing Attempt ordinals. Both initial red cases remain in `cost-red.log`;
**104** focused/browser checks pass after repair. Final frozen-runtime G1 passes
**6,092 / 26 opt-in skips / 30 warnings** in **1,160.60 seconds**, using eight
workers with work stealing (`g1-final.xml`). Warnings are the retained Python
multiprocessing/fork deprecations. Final documentation/Profile/physical-budget
checks pass **24**; undefined-name, JS syntax and diff checks pass. These focused
counts overlap G1 and are not additive. No additional paid call was made for the
read-only pricing/order projections.

Independent final census of ten native/local-provider roots
(`census-g1-final.json`) closes **318 Agent Attempts / 26 CPU Processes**, with
**26 creations / 26 canonical outcomes / 26 publications**, no duplicate outcomes,
pending effects, OPEN attempts, live owned process groups or sockets. All ten full
Oracles pass, including the expected FAILED first-refusal run and CANCELLED Leader
stop case. Earlier partial/failing checks and the first full G1 are retained.

The user authorizes existing CC settings without a global spend cap. The first
sample allocates **24 USD across four independent fresh roots**, with 6 USD per
root and 2 USD / 32 turns / 300 seconds per invocation. One replacement trial adds
6 USD (30 USD total allocation): the original inherited fixture had explicitly
disabled infrastructure retries, so its interrupted analysis correctly exhausted
before run completion. That original root and its clean closure remain as a
disabled-retry control; replacement Profiles enable one infrastructure retry,
without bypassing a runtime rule. Token mode is explicitly
`observe`; the 150,000-token declaration is not a hard token cutoff. Only
RESULT_ANALYSIS calls the configured `mimo-v2.6-pro`; native scripted prerequisites
produce real CPU results. The arms compare 49,152 versus 1,536 inline bytes, each
with and without one controlled capacity refusal after actual model tool work.
The prewritten protocol, original responses, billing, fault evidence and Oracles
are retained per arm. This is a small scientific interpretation/recovery sample,
not whole-topic G5 or proof of long-term savings. Existing research runs remain
read-only; no GPU qualification, commit or push is requested.

The five real-model trials have now closed. Normal inline and small inline both
preserve the local refutation (0), common-goal measurements (0.75/0.75), complete
single-candidate constraints and the correct next-action scope. Default inline
with one enabled reconstruction also preserves these, with unchanged frozen
inputs and one experiment. Small inline plus reconstruction times out at the
300-second invocation boundary without accepted analysis; its last request fits
the local allowance and has no new capacity or 429 failure. This is not evidence
of lost scientific memory, nor a passing quality result. The disabled-retry
control above retains its original failure.

| Main condition | Accepted analysis | Tokens / completed generation calls | CLI-reported USD |
|---|---|---|---|
| 49,152-byte inline allowance | Yes | 215,058 / 5 | 0.379813 |
| 1,536-byte inline allowance | Yes | 243,046 / 7 | 0.395605 |
| Default inline + one reconstruction | Yes | 141,078 / 4 | 0.465506 |
| Small inline + one reconstruction | No: timeout | 19,741 reported + at least 113,179 unsettled / 5 | 0.066247 + one unknown invocation |

The disabled-retry control reports another 0.160455 USD. Across all five trials,
the CLI reports **1.467626 USD**, with one unclosed billing observation represented
by a **2 USD reservation, not actual spend**. Every inspected native cost-state
marks `hasUnknownModelCost=true`: these are CLI fallback estimates, not verified
vendor invoices. Invocation dollar caps also use existing CLI accounting; they
are not a promise about a third-party bill. Prompt bytes fall 74,002→30,901 in the
small-inline control, but Bash calls rise 4→8 and tokens about 13%. Keep the
49,152-byte default; this sample does not establish a benefit from more aggressive
offloading. Raw Bash rereads are excluded from exact native Read counts, which
are zero here. Cache distribution and source scope remain separately visible.

`census-model-final.json` independently closes **57 Agent Attempts / 5 CPU
Processes**: five creations, five canonical outcomes, five publications, no
duplicates, pending effects, OPEN attempts, live owned groups or sockets; all
five full Oracles pass. Closure is distinct from analysis quality. The complete
Chinese interpretation and numeric aggregation are `report.md` and
`model-summary.json`; original responses and failures remain per trial.

## Current development — bounded context and recovery, 2026-09-26

The user authorizes implementing the [context/recovery plan](design/context-management-and-recovery.md).
New ACI-24 ordinary Attempts start fresh model sessions while retaining research
identity, Git workspaces, frozen input bytes, ownership and accumulated charges.
Three-round Debate keeps its existing bounded fork protocol and closes an
oversized auxiliary task without repeating that session. The prompt renderer
and existing provider relay share an explicitly estimated working allowance;
neither model names nor cumulative invocation budgets establish provider capacity.
No vector database, parallel memory authority or mandatory summary Agent is added.

Capacity failures close and archive through existing Attempt machinery and use
bounded infrastructure retries. API authentication/quota recovery remains a
separate shared condition. Operational analysis failure cannot itself override
otherwise valid revision, while adjudication, update, reports, budget and stop
requirements still apply. Early native source feedback reuses final admission's
validator. Measurement guidance exposes parser/baseline/comparison values without
guessing or rewriting scientific meaning. Bilingual guides document these limits.

Verification also reproduced a preexisting controller-exhaustion defect on the
unchanged baseline: a completed valid experiment followed by accepted ITERATE
was stopped by the controller's timeout marker. New ACI-24 ignores that marker
only for operational controller failures with completed owned work and a valid
canonical result; ordinary analysis, ownership and budget controls remain.
A native semantic rejection now spends the existing corrective retry allowance.
Promoted Git source reaches new inner chains independently of transcript parents.
Archive/stop failures take precedence over any attached capacity diagnostic;
three failing counterexamples were preserved before repair. Existing provider
settings and session-evidence helpers were consolidated without increasing the
accepted physical code budget or adding another state authority.

Local evidence is retained under `.local/qualification/context-management-20260925/`.
Four original captured prompts were replayed read-only: 202,805/231,432/279,595/
238,111 bytes become 79,328/77,131/82,857/78,250 bytes, with 48,816/48,834/48,712/
48,864 inline input bytes and unchanged frozen inputs. These are byte measurements,
not tokenizer accuracy or scientific-quality claims. The GLM first-prompt overflow
sample independently drops from 793,660 to 68,786 bytes (40,437 inline); all
original sources remain addressable and original file size/mtime are unchanged
(`glm-prompt-replay.json`).

Current and original bundled Oracles both accept retained GLM/MiMo roots
`MERC-causal-202609200023` and `MERC-causal-202609240242`: no open Attempts,
duplicate effects or owned process residue; database size/mtime unchanged.
The read-only reports are in `retained-readers.json`.
The separate retained context sample reports 3,722,840 cumulative tokens and
472,337 tokens for the last input, including 469,632 cache-read tokens; these
remain distinct in the monitor (`retained-context-metric.json`).

Native CLI/local-provider tests cover real CPU closure, capacity refusal after
committed submission, and Host restart after a lost submit reply. The latter
two pass with fresh sessions, identical frozen inputs, one experiment per
submission and clean Oracles. A separate two-cycle CPU case preserves valid
revision after exhausted analysis, with four actual outcomes and clean Oracle.
Initial failed evidence remains: an extra recovery input violated frozen retry
inputs and was removed; an edit during a native test correctly triggered pinned
runtime drift; several old fixtures still assumed inherited native history.

Final independent census of nine representative native CLI/local-provider roots
(`census-final.json`) passes: **311 Agent Attempts and 26 CPU Processes**, all
closed; **26 experiments created, 26 canonical outcomes and 26 outcome
publications**, with no duplicated outcome subjects, pending effects, live owned
process groups or sockets. All nine full Oracles pass. The cohort includes
legacy/current manager reconstruction, capacity-after-submit, lost-submit reply,
same-Attempt source correction, rejected-source archive, report repair and both
Leader completion/stop paths. An earlier census performed while the suite was
still active remains explicitly non-final in `census-during-g1.json`.

Final G1 on the frozen runtime passes: **6,078 passed / 26 opt-in skips / 30
warnings in 2,038.74 s**, using eight workers and short disposable paths
(`g1-final.log`, `g1-final.xml`). Compile, undefined-name checks and `git diff
--check` pass. The warnings are Python multiprocessing/fork deprecations retained
in the log. Final focused closure/precedence/legacy-and-current manager checks
pass **44**, and documentation/public Profile checks **7**; these overlap G1 and
must not be summed as unique tests. Earlier full G1 (6,004 pass / 59 fail / 26
skip), baseline reproductions and intermediate failed refreshes remain intact;
no check was removed or physical code budget increased to obtain the final pass.
The 26 skipped paid/GPU/notification/disposable-filesystem cases are not qualified.

No retained run was resumed, rewritten or migrated. No paid-model/GPU
qualification, commit or push is authorized by this implementation request. Native scripted success establishes
framework mechanics, not GLM/MiMo scientific recovery quality or G4–G5 promotion.

## Current development — nonblocking Leader experience sharing, 2026-09-25

The user authorizes implementing the [experience-sharing design](design/leader-experience-sharing.md)
and committing/pushing to dev. New ACI-23 runs reuse the existing LEADER
auxiliary task, ExpertLoopCandidate, command IDs and accepted outputs. Observer
selection no longer pauses peers, waits for their work to drain, reserves an
extra research round, changes parents or grants execution priority. The author
receives actual published reports and accepted facts. Advice enters the first
FORWARD request; the other wings and retries reuse that request's exact advice
set, including absence. Adoption, adaptation and disagreement remain independent
scientific choices. Existing budgets, provider recovery and operator controls
remain in effect. No new database, message ledger, receipt, candidate schema or
assurance mechanism is introduced.

Failed advice closes locally; unavailable Agent budget does not fabricate an
Attempt. Reopening SQLite before dispatch and after successful output acceptance
recovers the same task and one publication. Operator stop and shared-goal stop
end advice through the existing CLEANUP_OBSERVED transition, without clearing
participant pause reasons. A cancellation regression exposed that accepting a
new interaction is not a declared STOPPING edge; the repair reuses the existing
cleanup edge instead of adding a lifecycle transition. Late advice cannot be
published after its coordination ends.

The complete native rehearsal also exposed a report-coalescing race: an older
report's SELF_EVOLVE can close after a newer report's Observer has already run.
The later accepted coalesced report then hid that admitted Observer from pending
work. All nine CPU outcomes were published, but independent Oracle correctly
rejected the missing accepted Observer effect. The deterministic regression
fails before repair; coalescing now skips only unstarted tasks and every admitted
Observer still settles. The failed native-complete directory and unchanged
Oracle rejection remain available, alongside the final rerun.

Web details show author, accepted text, source Attempts and actual proposal
input recipients. They explicitly distinguish input inclusion from adoption.
The activity crown ends on completion/failure/abort; retained exclusive rounds
remain labelled as such. English/Chinese Usage, architecture and Profile notes
explain the new-run boundary.

Validation uses disposable state and the native Claude CLI against a local
scripted provider, with real CPU experiments and no paid or GPU calls. Focused
selections passed **386**, **171**, and **103** checks; these overlap and must
not be summed as unique cases. The final closure/interaction/observation
selection passed **122 checks**, provider cooldown **7**, final browser
leader/advice/crown/detail coverage **6**, and documentation **4**. The separately
selected retained exclusive-Leader CPU Host case also passed. Independent counterexamples
reject changed advice authors/content, missing or premature inputs, duplicated
publication, changed source facts and extra scheduling authority. The final native completion case passes in **360.02 s**: three experts each
complete three ordinary cycles and **nine CPU experiments**, both peers start
ordinary work while LEADER is open, and all three consume the accepted advice
in FORWARD/BACKWARD/SYNTHESIS. All **128 Attempts** close successfully; one
selection, one start and one advice publication occur, with no participant
controls. The final operator-stop case passes in **103.22 s**: **45 Attempts**
close (44 successful, one cancelled LEADER), including three CPU outcomes,
with one coordination abort and no advice publication. Both full Oracles pass.

Post-test independent census (`census.json`) confirms **173 closed native
Attempts**, clean Oracles, zero pending effects, no live recorded process groups
and no sockets. The retained exclusive-round CPU case separately has **59
closed successful Attempts / four CPU outcomes** and a clean Oracle, preserving
its actual pause/resume semantics. The census initially passed relative paths
to readers requiring canonical paths; that rejection is retained separately.
Rerunning against absolute paths passes without changing any reader checks or
persisted research state.

Evidence is retained under ignored
`.local/qualification/leader-guidance-20260925/`. Initial runs encountered a missing pytest parent directory, an invalid
SUPPORTED continuation and an incorrect negative-contribution fixture version.
The failed native/browser fixture directories, successful preliminary nine-CPU
run, cancellation red/green results and interrupted pre-final native directory
remain separate from the final passing results. Focused undefined/unused-name checks
pass in the new code; an existing unused `fold_agent_attempts` import in
WorkflowHost is unchanged. Existing research runs and their pinned bundles
remain untouched. This is bounded development validation, not full G0–G5
release promotion or proof that a paid model will use advice well.

## Current development — PR 1 patch binding and report evidence, 2026-09-24

The user authorizes organizing and merging PR #1. Its original head is `785f95b`;
the integration includes current dev `27ebc4e`. Submitted target patches are
checked against the clean bound commit, with exact executed/published bytes in
the existing unified-diff apply-check path. Formal submission rejects direct
`/workspace` argv paths. Report cost summaries reuse `fold_agent_attempts`,
separating reported charges, reservations and unknown closed Attempts.

Review reproduced three report projection defects. An execution-level success
was attached to a LOST predecessor as well as its successful retry; device
sampling was labelled task GPU execution; and the real
`gpu_minimum_free_memory_mib` field was read under a nonexistent name. The
repair joins outcomes through `execution_artifacts` to the publishing Attempt,
labels device sampling explicitly, preserves existing owned-memory observations
and reads the actual summary field. Report instructions retain raw execution
without turning it into accepted science or proof of CUDA computation. The cost
summary is explicitly frozen before the first report attempt. No new persistent
fact, validator authority, schema, hash or GPU attribution mechanism is added.

The initial review passed 106 PR-head checks and 42 merge-preview checks, yet
independent counterexamples exposed the projection errors. Evidence is under
ignored `.local/reviews/pr-1-*`. Repair development retains its initial 13 passes
and seven failures (including one test import mistake); after correcting that
import, the real Host restart test independently fails on LOST evidence ownership
in `pr-1-fix-host-red.xml`. After repair, all 20 targeted checks pass (5.56 s),
including the same actual Host/CPU restart and report-input assertions.

The broader selection passes **290 checks** (124.94 s) with no skips, covering
execution workspaces, submitted patches, actual CPU Host tools/restart, report
acceptance/retries/Oracle, canonical accounting, GPU attribution, provider
recovery and documentation (`pr-1-fix-regression.xml`). The installed native
Claude Code/local-provider/CPU final-report path also passes **one check**
(51.66 s, `pr-1-fix-native.xml`), including the exact publishing Attempt and
canonical outcome in the model's report context, actual report archival and
full Oracle. An independent post-test census counts **16 CLOSED/SUCCEEDED
Attempts: 15 Agents and one CPU Process**, one published result, no live
recorded process groups and no control socket (`pr-1-fix-native-census.json`).
These selections overlap; their counts are not summed as unique cases.

Existing research runs are unchanged. Tests use disposable CPU state and a
scripted local API, with no paid-model calls or actual GPU qualification.
This is bounded development validation, not a full release promotion.

## Current development — provider availability and operator recovery, 2026-09-24

The user authorized general framework repair and retired the earlier research
run. No retained run was started, resumed, migrated, modified or deleted.
Candidate/scientific-method changes remain out of scope.

The existing provider reducer now distinguishes cooldown, operator wait and a
single pending recovery invocation. It uses durable Attempt/control records,
the existing infrastructure attempt allowance and observed provider responses;
no new run state, credential digest, registry or parallel health authority is
introduced. Concurrent failures form one recovery wave. API-unavailable calls
remain closed/accounted without consuming each node's retry allowance; stopping
reports remain bounded. Preexisting parent work cannot occupy the recovery slot,
and its late response cannot falsely establish restored access. Submitted CPU
Processes were collected during API wait and were not rerun after recovery.

Monitor now preserves Host `provider_retry`. Web/TUI expose reason, wait/retry
state and explicit recovery controls. Notifications include 429 and quota waits,
deduplicate state/reason changes and distinguish an operator retry request from
an observed recovery. Legacy auth-event subscriptions map to the new events.
Read-only and human-answer Web grants cannot rotate API connections; the existing
same-origin/scoped-token mechanism supplies a separate operator API grant.

Connection selection extends `PROVIDER_RETRY_CLEAR` with an optional Host-side
0600 settings path. The existing control journal selects that private credential
input; it is loaded on a fresh control or Host restart, never copied into facts
or a new private snapshot. Original settings/version checks remain intact.
Only key/token/base URL can vary; model/runtime settings are refused. Missing
selected input waits explicitly, without fallback to the old credential. Lost
replies retry one command identity; late old-connection failures cannot reblock
the selected connection. See [operation and restore semantics](guides/provider-recovery.md).

The post-result design-failure repair completes the actual old tip's assessment
before closing/adjudicating the chain. It never creates an outcome for the
unsubmitted successor. Independent batch audit requires a closed failed next
design/execution, correct ordinal/owner/chain, prior analysis timing and exact
iteration-context references. Removing one chain's failure invalidates the
adjudication even while another chain's failure remains.

Validation evidence is under ignored
`.local/qualification/provider-recovery-20260924/`. The initial broad selection
had **329 passes / 56 failures** caused by a too-long AF_UNIX test directory;
the retained setup failures are documented in `initial-checks.txt`. Reusing a
short temporary directory resolves the affected transport selection: **60 pass**
(33.44 s), without changing relay behavior or weakening assertions. Together
these cover all 385 cases in the initial selection. The complete expert-batch
selection plus native recovery also passes: **35 checks** (348.53 s), recorded
in `host.xml`. After the final reducer changes, **87 recovery/concurrency/UI/reader
checks pass** (62.99 s), recorded in `final-recovery.xml`. The separately repeated
native scenario passes (**one check**, 55.42 s); its evidence is in `native-final/`
and `native-final.xml`. That native repetition preceded the final reducer boundary
adjustment; the 87 final checks cover that adjustment.

The native local-provider scenario exercises installed Claude Code, the real
relay, Host/MCP, collection/publication and one CPU experiment after real HTTP
429 responses and endpoint/key selection. The repeated sample closed **14
Attempts**, including two failed API Agents and one successful CPU Process;
one old invocation failed after the switch without reblocking recovery. Its
full Oracle passed with no open Attempts or pending effects. An independent
census found no live recorded process groups or remaining control socket;
see `native-final/closure-census.json`.
These are scripted local providers, not paid-model, GPU, long-soak or release
qualification. Public notification delivery was mocked; no external messages
were sent. The user subsequently authorized updating the tutorials and committing
and pushing this repair to dev. The bilingual recovery guides add state/action
tables, exact-request retry examples, Host-side file preparation and
troubleshooting; Setup, Usage, personal configuration and notification guides
link or explain the relevant operations. All four documentation checks pass,
CLI help confirms the documented recovery/Web flags, and diff whitespace checks
pass. No additional runtime changes or existing-run operations were needed for
this documentation follow-up.

## Current development — lineage relationships and contextual legend, 2026-09-23

The lineage legend mixed root-node labels, inferred revision types, workflow
states and expert colors. Real pruning edges lacked a line sample, and pruning
also moved root hypotheses behind their winners in the ancestry layout. The
text classifier missed explicit architecture changes and interpreted a screening
step that retained the mechanism as a scientific change.

The Web now lays out only recorded parent relationships. Solid arrows mean
parent to derived hypothesis; dashed arrows mean winner to pruned hypothesis.
The pruning overlay can be hidden without moving nodes, changing assessments,
clearing selection or resetting pan/zoom. Dashed node borders mark pruning
independently of scientific status. The legend groups visible relations, status,
controls and experts, including after filtering or an empty result. Component
dependencies keep their own view. The keyword classifier and its redundant node
bands are removed; details retain exact proposal text, parent/child claims,
method families, assessments and available pruning source evidence. No new
persistent field, validator, protocol or model invocation is added.

Validation: all **80 Web/browser/summary/support checks pass** (193.43 s).
After visual review, backward pruning links were routed through column gaps
and above the graph to avoid intervening nodes. **Six final graph browser
regressions pass** (41.06 s), including sampled SVG path/node collision checks,
ancestry/control separation, overlay toggle and polling, filtered/empty legends,
component-mode compatibility, scientific status, missing-parent fallback,
literal text safety, keyboard access and fitted labels. Four documentation
checks, JavaScript syntax and diff whitespace checks also pass. A read-only
replay of the retained run at r213 renders all 11 nodes, seven parent links and
two pruning links; all four roots return to the first column. Legend items fall
from 15 to nine. Desktop and 390-pixel mobile captures and observed DOM values
are retained in `.local/ui-reviews/lineage-legend-20260923/repaired/final/`;
the final replay observes no pruning-path intersections with node interiors.
The original diagnosis and text misclassification examples remain alongside it.
Existing research state, Profiles, fixed bundles and running Hosts are unchanged.
This display repair does not claim CPU/GPU, paid-model or full release qualification.

Follow-up pruning direction repair: a backward pruning origin shared the winner's
left midpoint with an incoming parent arrow, making the dashed link appear to
have two arrowheads. Pruning now uses a lower exit and upper entry on node sides,
including same-column links, leaving the midpoint ports for ancestry. An actual
SVG endpoint regression fails on the previous routing and passes after repair;
three focused browser cases pass (24.99 s). The r213 replay keeps all nine links,
with one arrow per pruning link, no arrow overlapping a pruning origin and no
pruning path crossing a node interior. The screenshot and measured endpoints
are retained in `.local/ui-reviews/lineage-legend-20260923/pruning-ports/`.

The pruning display button now reads `Hide pruning links (N)` or `Show pruning
links (N)` for the next action. Its tooltip identifies N as a link count and
explains that hypothesis nodes and their statuses remain visible. The action
button no longer carries a pressed-state label or highlight. The existing
overlay browser case passes (13.29 s), checking both labels across toggles and
polling, preserved node positions/control legend, filters and mobile width.
Read-only HTTP checks confirm the checkout observer on port 45991 serves all
three updated assets; already open pages need a reload.

## Current development — Leader status icon, 2026-09-23

The Web expert card looked for an OPEN Agent command named LEADER to display
its crown. Current accepted Leader rounds run ordinary research nodes, so a
valid active round could have no crown. The UI now reads the existing per-expert
`leader.active` projection. The crown remains across task changes and idle gaps;
finished, aborted or failed rounds remove it, while history remains available.
Pending selection alone does not claim a started round. The tooltip describes
working/waiting, and the accessible label names the active Leader. Ordinary
task labels remain unchanged. No new field, counter, state reducer or runtime
authority is introduced.

Validation: four real Chrome regressions pass (70.33 s), covering ordinary
hypothesis/report nodes, no OPEN task, absent Host observation, role isolation,
all three terminal projections, and a legacy LEADER command without accepted
tenure. Test summaries come from the production accepted-event projection.
Another 46 Web, observer-summary and support tests pass (5.48 s). The initial
Web selection exposed a pre-existing stale assertion expecting one POST call;
committed HEAD already had two, for human answers and read-only issue-description
export. The assertion now names both known routes and retains answer-token
checks; endpoint authorization tests still pass. Documentation links also pass.

Read-only live static requests confirm a checkout-backed observer serves the
new asset immediately; already open clients need a page reload. An observer
inside a retained run bundle still serves its original asset. Existing research
state, fixed bundles, Profiles and running Hosts were not changed or restarted.
No additional CPU/GPU or paid qualification is needed for this display-only fix.

Follow-up lineage styling: hover, focus, selection and connected highlighting
previously replaced expert border colors with blue/cyan; selection also added a
blue glow. Nodes now retain the existing expert palette, distinguish interaction
states by border width, and use the expert color for the keyboard focus outline.
Three existing graph browser regressions pass (16.24 s), covering selection
through polling, fitted text and failure symbols. Ad hoc Chrome checks also pass
for three expert colors under mouse hover/click, connected highlighting,
keyboard focus and Enter selection; computed styles and screenshots are retained
in `.local/ui-reviews/lineage-colors-20260923/final-check/`. Read-only static
requests confirm port 45991 serves the updated checkout CSS, while the retained
bundle on 32991 still serves its original CSS.

## Current development — Observer references and native feedback, 2026-09-23

The accepted reference domain already includes both fact IDs and entity IDs,
but Leader qualification matched only entity IDs. This rejected valid evidence
citations and left the semantic error until postflight, after the native call
had ended. This repair reuses the existing accepted fact/entity field mapping
and extracts the existing Observer postflight validator for native Stop reuse.
No new schema, digest, state record, qualification rule or approval boundary is
introduced. Private source reports remain ignored; regression fixtures use
invented roles, components and scientific facts.

Reference interpretation expands a cited fact to its own entities only, never
foreign-key dependencies. Responses and accepted events retain the original
citations. Observer admission, Host component selection, interaction and batch audits
interpret IDs consistently; the Oracle still independently verifies qualification,
closed cycles, ownership, budgets and terminal effects. Stop canonicalizes the
existing display aliases and validates the frozen request without accessing the
state store. Correction limits, deadlines, session identity, postflight checks
and sole Host admission remain in force.

Screening guidance uses existing LOCAL/GOAL behavior: declare local C5 in
SYNTHESIS for partial screening, retain it when later evaluating GOAL, preserve
an accepted null measurement during operational repair, and supply genuine
common evidence on GOAL branches. It does not relax the common evaluator or
turn missing evidence into scientific refutation. These are generic instructions,
with no special cases for a user, Profile, dataset or model.

Validation and limits:

- **656 focused checks passed** across interaction admission/Host/Oracle, batch
  audit, Observer postflight and native semantic correction, native output
  lifecycle, adapter, stage prompts, packaged skills and documentation.
  Both retained ACI-14 and current ACI-22
  references are covered. Foreign evidence, missing/ambiguous parents, execution
  failures, ineligible triggers and exhausted correction budgets still reject.
- **One real Claude Code/local-provider test passed**: ACI-22 aliases carry a
  schema-valid foreign contribution, Stop rejects it, and the same Attempt/session
  corrects it using fact IDs. Two validations, one rejection, complete transcript,
  postflight success, no remaining process group or socket. This is an adapter
  test; the provider is scripted, not a paid model-quality sample.
- **Two final real CPU integration cases passed** on fixed source: supported
  contributions (147.90 s) and clean refutations with null ordinary selection
  (109.80 s). Both complete three-expert Leader/three-round Debate/research
  closure; the negative case reconstructs Host at two accepted control boundaries.
  They use scripted Agents through real sessions, Host, broker, Processes and
  publication. Each reaches COMPLETED r130 with 90 Agent and six CPU Process
  Attempts, all CLOSED/SUCCEEDED, six canonical outcomes and a clean full Oracle.
  Independent post-test reads recount 192 closed Attempts, 12 exited Process
  groups, zero sockets, exactly one Leader selection/start/finish and one completed
  Debate per run. Evidence: `final-supported/`, `final-refuted/` and
  `final-closure.json` under `.local/analysis/observer-repair-20260923/`.
- Six original diagnostic outputs replayed unchanged through the repaired
  native and postflight semantic checks: five pass; the one citing two own
  hypotheses still rejects. This is read-only replay, not retroactive acceptance
  of failed Attempts or a modification to their recorded outcomes.
- Initial CPU rehearsals are retained under ignored
  `.local/analysis/observer-repair-20260923/`: one crossed a source edit and
  correctly rejected two Processes before spawn on runtime drift. Subsequent
  complete positive/negative flows exposed the same entity-only parent assumption
  in the batch audit. That audit now uses the shared reference interpretation;
  read-only Oracle rechecks of both completed records pass without changing their
  facts, Attempts or accepted controls. No protection or assertion was weakened.
- No existing research run or local Profile was changed, restarted or migrated.
  Pinned live bundles retain their original code. No paid/GPU or full release
  qualification was performed; remaining scientific/model-quality limitations
  are not claimed fixed by this repair.

## Current development — support evidence, Oracle and target admission, 2026-09-23

This development repair addresses generic reproduced failures: numerically equal
common targets were compared as raw strings, prestart integrity rejection was
mistaken for failed parser execution, recent support selection followed command
ordering rather than time, and valid WorkspaceArtifact text was omitted because
its envelope has no media_type. Execution-only target validation also deferred a
deterministic incompatibility until after Agent work. These are interpretation,
diagnostic-copy and admission lifecycle problems. The fix reuses existing Decimal
validation, recorded Attempt/command/fact identities, Git tree materialization and
the execution validator. No new digest, persistent validation cache, checkpoint,
schema authority or run migration is introduced. Private source reports stay in
ignored local storage; tracked tests use generic invented fixtures.

The independent Oracle normalizes only validated numeric targets at expert-input,
preregistration and outcome-source comparisons. Other fields and parser bytes
remain exact. Evidenced synthetic prestart integrity failure requires the matching
integrity explanation and cannot carry scientific metrics or a goal result;
actual parser failures still require diagnostics. A real Host regression closes
and publishes a prestart rejection, passes Oracle, and exports its accepted
Grounding, preregistration, source Agent and ACCEPT_OUTCOME records.

Support copies retain a lightweight run summary alongside selected details and
direct supporting records. Recent failures use end time (start time fallback)
with stable IDs for ties; the first unrecovered failure remains available.
Large duplicate bodies are omitted individually with field/record identities,
while typed bounded UTF-8 WorkspaceArtifact content uses the existing path/type
validator and credential redaction. Source availability and exported fidelity
are distinct, including already sanitized historical evidence. Public/private
selection boundaries and source immutability remain covered by regression tests.
README, Web and TUI explain scope, observation time/state/revision, failure groups,
omissions and exporter versus recorded Attempt environment. Contract schema
version is not presented as the Profile authoring version. Optional issue text
travels in a bounded authenticated POST body in Web or explicit CLI flags, is
redacted, previewed and included even in public copies; nothing is uploaded.

Profile admission validates the exact clean bound commit in a disposable tree
through the same helper used by execution, before Host submission. Execution
validation remains in place. Explicit read roots must be readable/searchable;
the admission view uses the existing generic read-root projection and does not
claim arbitrary data/model completeness. A 100-file, 100 MiB local target sample
took **1.02 s** to bind and validate, used a 100 MiB temporary tree, left the Git
index unchanged and removed the temporary tree. This is a local sample, not a
large-repository latency guarantee.

Final focused selection: **380 passed in 92.65 s** (`combined.txt`), covering
shared acceptance Oracle/Host/execution, runtime diagnostics, debug/support
public/private export, HTTP/TUI/browser flows, execution workspace, Profile
admission, bootstrap and CLI. Native CLI + local scripted provider tests:
**2 passed in 110.59 s** (`native-final.txt`), covering admitted runtime/data
mounts, actual CPU failure/repair/publication and committed-submit crash recovery
with same-session resume. Independent read-only census finds **35 closed Attempts**
(30 successful Agents, one lost/recovered Agent, two failed and two successful
Processes), four preregistrations and four canonical outcomes across two roots.
Both recorded Oracles pass; no recorded process group or socket remains. A
subsequent private support copy from each test root also resolves the recorded
Claude CLI version from its actual transcript. No retained research run was used.

Profiling a synthetic ~3.5 MB provider-context export identified repeated
credential-pattern scanning, rather than Oracle or ZIP compression, as its main
cost. The existing assignment pattern is now skipped only when its mandatory
`:`/`=` delimiter is absent. The measured build time moved from **2.50–3.42 s**
to **0.27–0.28 s** in the final local sample (ZIP ~0.02 s); provider wire remains
available within existing limits. Preview/download still read fresh evidence;
no export cache or frozen preview is introduced. These timings include profiling
and do not establish a production latency bound.

Evidence is retained under `.local/qualification/support-repair-20260923/`,
including the original three failing Oracle regressions, interim privacy and
test-fixture failures, profiling, admission measurement and independent census.
An initial native sample overlapped source edits and correctly stopped on pinned
runtime drift; its failures are retained, its groups are gone, and the final
native sample ran with unchanged code. A later credential regression exposed an
authorization-scheme extraction mistake; the existing negative checks caught it
and all final privacy tests pass. No checks were weakened to accept these failures.

This is focused development/G0 and bounded native CPU evidence. Full G1 offline
and installed delivery, retained-run G2, paid/GPU G4–G5 and long soaks were not
rerun; there is no new release promotion, paid/GPU work or existing-run mutation.
The user subsequently authorized committing and pushing this repair to `dev`.

## Current development — deterministic init and configuration entry, 2026-09-22

The reproduced failures were (1) init requiring admission of an unbound public
template before allowing its placeholders to be filled, and (2) a synthetic
mode-0644 dotenv file passing the shared reader and literature configuration
preflight despite the documented 0600 requirement. Init also relied on model
copying of the same budget/literature/resource options into several commands.
These are preparation and credential-read boundary problems. Ordinary Profile
validation, open-file metadata checks and exclusive creation are sufficient;
there is no new hash, manifest, receipt, protocol version or persisted authority.

`hypoloom.bootstrap` now provides metadata-only path discovery, an in-memory
filled-template preview through `admit_profile`, exclusive six-file preparation,
and launch-argument projection from the admitted budget and one literature
argument vector. Optional `--start` uses existing managed Host/CLI lifecycle
functions, checks the actual Profile before and after readiness, submits one
stable CREATE_RUN request and reconciles ambiguous replies with STATUS.
Only a conclusive absent run permits failed-creation cleanup; an uncertain or
existing run is not stopped or resubmitted. Existing Profiles cannot be changed
by preparation; a present state root is rejected. The skill retains scientific
choices, exact GPU/sharing approval and final launch authorization, with the
deterministic preparation moved out of prose. No provider-availability claim
follows from configuration checks.

The credentials reader checks the opened descriptor's mode before reading any
values, preserving absolute-path, non-symlink, regular-file and parsing checks.
The sole public template is now `config/hypoloom.env.example`; config/README,
root guides and init show the private `.local/credentials/hypoloom.env` location
and distinguish it from model authentication. Public Profile comments change,
not their numerical or scientific controls. The real local credentials file
remains 0600 in a 0700 directory, and MERC-causal passes read-only admission
without edits. No credential contents were printed or copied into reports.

Validation: **369 passed in 22.12 s**, no failures/errors/skips, in
`.local/qualification/init-repair-20260922/final.xml`. This covers bootstrap,
credentials, notification delivery/proxy, Profile admission/v4, operator skill,
managed Host helper, CLI, Unpaywall, public templates and documentation. New
behavior tests cover bilingual preview/write equivalence, no overwrite, invalid
inputs, retained local adoption, secret suppression, real auth-source resolver,
null invocation cost, literature/PDF propagation, GPU-pool argument separation,
pre-create cleanup and ambiguous-create reconciliation. The bootstrap CLI/Host
test uses real subprocesses, control socket and CPU execution; only its systemd
transport is substituted. It verifies normal Host exit, clean Oracle and absent
owned process groups/socket.

The existing tracked-v4 three-expert/two-chain CPU closure additionally passed
in **11.80 s** (`template-cpu.xml`), using FakeAgent and blocked public web, with
the original public controls preserved and a clean independent Oracle. An
independent retained-file census finds 33 Agent and four Process Attempts,
all closed (36 SUCCEEDED, one CANCELLED), and zero remaining owned groups. An
offline sdist build succeeded (`build.log`, `dist/`), and its archive contains
the new module, configuration guide, single relocated dotenv template and both
Profile templates; the obsolete template path is absent.

Preserved development failures: the first selection had 174 passes and nine
failures, consisting of two new test-fixture mistakes (CLI vector offset and
incorrect YAML budget key) plus obsolete shell-text assertions replaced by
behavior tests. The expanded selection had 368 passes and one stale CLI identity
assertion expecting ACI 21 even though prior commit 491666a already uses 22;
the assertion now explicitly expects 22, retaining the other identity checks.
The failed expanded sample remains in `focused.xml`; final results are separate.

Final pre-commit review reproduced malformed nested credential sections raising
AttributeError during path discovery/preview, and module lookup following the
caller's directory instead of the selected checkout. Credential-reference
traversal now reports ordinary structured input errors; all helper/CLI calls
receive the selected working directory. No extra Profile schema or authority
is introduced. Five initially failing regressions are retained in
`review-red.xml`; the selected-checkout case first exposed the missing cwd
argument, and a separate subprocess probe confirmed transport failure when the
module existed only in the selected directory. The repaired bootstrap selection
passed 29/29 (`review-bootstrap.xml`). Four additional lifecycle cases cover a
confirmed create followed by unavailable STATUS, explicit create rejection,
known Host launch failure and uncertain launch transport.

The final combined focused and tracked-v4 CPU selection passes **379/379 in
36.08 s**, no failures/errors/skips (`review-final.xml`). Source-distribution
contents are rechecked after the review fixes. The remote dev branch was fetched
and had no divergence before this user's explicitly authorized commit/push.

Limits: no real-provider/cross-model comparison, GPU allocation, live user-systemd
or formal G1–G5 promotion was performed. Existing research runs, private
credentials and local Profile files were not changed. Commit/push authorization
does not authorize starting or mutating a research run.

## Current development — execution handoff and session repair, 2026-09-22

Scope: fresh runs only; retained research runs and local Profile choices remain
unchanged. The observed long-experiment failure is a controller reaching its
wall deadline while its separately owned Process continues. Existing ownership,
submission IDs, transactions and budgets already protect the experiment; they
cannot express a successful controller handoff under the old terminal-only
output rule. Extend the existing ACI to version 22 with one optional
`waiting_for_experiment_id` on EXECUTION output. This is an existing experiment
primary key, not a second journal, receipt or scheduler. A successful handoff
closes and charges that invocation but does not complete the research node.
Host derives readiness from the existing experiment state and permits only one
accepted handoff per experiment. The maintenance cost is one versioned output
case in existing validation, scheduling and audit; retained versions keep their
old rule; that compatibility branch can be removed when retained ACI <=21 is
no longer supported. No new Profile switch, timer exemption or increased budget
is needed.

The same candidate fixes deletion of a tracked directory being rejected as an
unavailable parent during patch capture or session commit. Git's existing deleted
path list authorizes only those absent paths; existing ancestor/symlink and
single-link checks remain. Session closure attempts transcript publication even
when Git commit fails, and carries partial metadata and the discarded provider
response into the failed Attempt. Missing commit/archive evidence remains an
Oracle finding; failed authoring is never promoted to accepted science.

GPU attribution now uses the reserve counterfactual correctly (`local-compute/7`):
free 1,911 MiB plus owned 770 MiB is still below the 4,096 MiB reserve, so other
occupancy alone suffices; terminating this workload cannot restore reserve.
When releasing owned memory would restore reserve, the existing self-pressure
termination applies. Unknown ownership still fails closed. Reserve, sampling
and terminate grace, process ownership, GPU grants and pool enforcement are
unchanged. The real-GPU fixture now places external growth in the external
case; this changed candidate has **not** rerun G4.

The existing Leader/Debate skills now compare actual evaluator/split/aggregation
and audit protocols instead of assuming different expert components imply
incomparable measurements. They preserve LOCAL/GOAL distinctions, joint-candidate
requirements and honest no-Debate decisions. No MERC metric, threshold, seed
count, device identity or budget is embedded in framework defaults. Profile
Leader thresholds, budgets and capacities remain unchanged; bilingual guidance
explains their separate roles. A short experiment can still finish in one
invocation after a bounded wait; handoff is available for pending work.

Development verification (groups overlap; these counts are not a summed suite):
245 execution/ACI/budget/loop/GPU-host/Oracle checks passed; 202 GPU/protocol,
operator-skill and documentation checks passed; the cross-Profile/session/batch
selection had 225 passes and one batch-limit assertion failure, followed by a
passing unchanged single-case recheck. The original sample is retained as a
failure; its precise Process-level cause was not recovered. Nine handoff
regressions cover collection, same-version Host reconstruction, cancellation,
ordinary budget charging, retained-protocol rejection and negative independent
audit. Native CC with a local provider completed two actual CPU Processes
(failure then repaired success), three successful controller invocations and
one shared session; original terminal-only Stop correction also passed.
The existing oracle and process-group census are clean in these native samples.

Initial sandbox-only socket/`/var/tmp` checks failed before Host execution and
were rerun with the required Host permissions. Development also exposed and
fixed a continuation-context insertion error; the new budget and cancellation
fixtures initially used zero-cost fake usage and an incomplete cancel payload,
then passed with explicit usage and a valid request. One native run correctly
rejected code changes made during its execution (`PinnedRuntimeError`); the
fixed-tree recheck passed. These samples are not erased or claimed as passes.
Final fixed-candidate selection: **134 passed in 148.97 s**, recorded in
`.local/qualification/aci22-repair-20260922/focused.xml`. A strengthened native
handoff case additionally passed in 70.25 s (`native-timing.xml`): the repaired
CPU Process runs for 12 seconds, and independent durable timestamps prove its
submitting controller closed before Process exit, with collection starting
after exit. All three controller calls share the same session. This refreshes
one identity in the 134-case selection, not an additional distinct test.

Limits: this is repair/G0 plus bounded native CPU evidence, not a frozen G1–G5
promotion. No paid provider request, real GPU experiment, performance percentile
qualification, retained-run migration or live-run mutation was performed.
The user subsequently reported terminating the retained run and authorized
committing/pushing this repair to dev. Scientific decision quality and the
changed GPU policy still require their appropriate future bounded qualification.

## Current development — dynamic Web ports, 2026-09-22

The approved repair addresses shared-server listener collisions: TUI and
standalone Web previously defaulted to 8770, while automatic dashboards and
public templates defaulted to 8765 (local MERC used 8766). Profile-v4 admission
rejected port 0 even though the Web server already supported it. Telegram
messages derived addresses from configuration rather than the owned listener.
Pre-fix regressions reproduced the Profile rejection and both CLI defaults.

TUI, standalone Web and automatic dashboards now default to 0; the existing
server binds directly to `127.0.0.1:0`, with the OS choosing the port atomically.
The existing validator accepts integer ports 0–65535 and rejects booleans and
invalid bounds/types. Explicit positive ports retain fixed binding and visible
conflicts; automatic services retry that same port, and TUI recovery suggests
`--web-port 0`. Startup URLs and TUI tunnel commands use the bound socket.
Notifications obtain the port from the owning service's existing dashboard
objects; an unavailable listener or standalone dynamic configuration produces
local-access guidance. Standalone fixed addresses are labeled as configured,
not checked for liveness. Bearer tokens stay out of notifications. No port
scanner, reservation, persisted address or new registry was introduced.

Both public templates and the ignored local `MERC-causal/notify.yaml` now use
port 0. Existing research runs and their admitted settings were not changed.
Local MERC's six-document v4 projection has zero issues. Raw public templates
still intentionally reject the unbound model placeholder; their tests bind
model/runtime paths and admit both languages successfully.

Focused regression: **273 passed in 42.88 s**, with this selection:

```bash
.conda/envs/hypoloom/bin/python -B -m pytest -q -p no:cacheprovider \
  tests/runtime/test_profile_v4.py tests/runtime/test_observer_services.py \
  tests/runtime/test_web_observer.py tests/runtime/test_monitor.py \
  tests/runtime/test_notification_tailer.py tests/test_public_profiles.py \
  tests/runtime/test_cli.py tests/runtime/test_monitor_launcher.py \
  tests/runtime/test_operator_skills.py tests/test_documentation_structure.py
```

Tests cover simultaneous independent roots with explicit 0 and omitted ports,
distinct listeners/tokens, cross-token rejection, one owner's exit while the
other stays available, source-state census/byte preservation, port release,
fixed-port conflict/recovery, a real CLI Host lifecycle using the printed
dynamic address, actual-port Telegram content through a fake transport, and
TUI/CLI access controls. The initial socket selection was blocked by sandbox
`EPERM` (7 failures / 18 passes); the unchanged selection passed outside the
sandbox (25 passes), followed by the complete selection above. No real remote
notification, paid-model/GPU experiment or existing-run mutation was performed.
This is focused development evidence, not a new G0–G5 candidate freeze.

## Current development — responsive control and evidence-directed research, 2026-09-21

Uncommitted development repair, using the existing ACI version mechanism
(new runs: ACI 21). This is not a frozen or fully qualified release. Origin:
the closed ACI-20 run `MERC-causal-202609200023` exposed repeated decoding of
large historical observations on the control thread, two completed debates
with empty restart plans, and no eligible Leader. Its 44 outcomes included
35 clean refutations and 9 execution failures; all eight adjudications left
`best_chain` empty. The user selected coordination from verified intermediate
contributions as the intended Leader responsibility.

Implementation:

- Attempt metadata and command identities narrow status, retry, report and
  participant reads. Budget admission and usage share compact cached results
  of the original charge fold; OPEN attempts are reread and transaction-local
  closes cannot poison the cache on rollback. General StateStore copy
  isolation remains intact.
- Human view rendering and exact-node private view preparation use one bounded
  worker, independent read transactions and existing atomic publication.
  Refresh requests coalesce; OPEN attempts alone no longer rebuild historical
  files. The Host rejects a completed ACTIVE view after terminal closure and
  waits for a terminal view or records its failure. Private task admission is
  rechecked before dispatch. Component rotation and a 250 ms soft dispatch
  budget introduce control-service opportunities between agent dispatches;
  they do not preempt transactions or bound every collection/postflight step.
- ACI-21 Debate plans are accepted separately for each participant after its
  own admitted cycle closes. The scientific parent is its exact proposed
  hypothesis, including an earlier-cycle hypothesis or a null ordinary
  selection. Existing verdict, plan, command IDs and source-cycle boundaries
  authorize the restart. Public Judge guidance reaches FORWARD/BACKWARD/
  SYNTHESIS; private winner-session promotion retains its original source and
  owner checks. Failure and unavailable budgets produce explicit reasons.
- Leader eligibility includes verified clean refutations with matching
  evidence and assessment, excludes execution failure, and requires the
  contribution's own cycle closure and research budget. An Observer must cite
  one own hypothesis plus its qualifying evidence. That exact contribution
  authorizes the bounded Leader round; ordinary termination, peer-pause
  ownership and goal-completion priority remain enforced.
- Interaction detail distinguishes waiting, designated research, experiment
  creation, actual outcomes and not-started reasons. An empty finished
  interaction does not imply a completed experiment. Independent batch and
  interaction audits check parent authorization, ordering, participant
  coverage, budgets and actual closure. Downstream nodes are checked against
  the admitted batch's budget time, not against budget consumed by its own
  subsequent experiment.
- End-run rejection details survive background refresh. Unexpected TUI
  exceptions enter the existing sanitized `diagnostics/host.jsonl`, and the
  CLI preserves a nonzero application exit code. Missing final-report outcome
  citations are fed back through the native Stop validator within the same
  invocation; Host acceptance reuses the same complete-citation validator.
  The mandatory final report still gates run closure.

Assurance scope: the failed assets were control-thread service time, the
accepted-interaction-to-scientific-parent relationship, and the eligibility
definition. Existing primary keys, transactions, version binding, atomic
publication and validators suffice. No new persistent queue, contribution
ledger, hash, schema, checkpoint or parallel source of authority was added.
Caches, scheduling cursors and worker jobs are disposable; restart rebuilds
them from existing records. Closed charges rely on the existing immutable
CLOSED-Attempt lifecycle.

Read-only profiling of the retained 320-Attempt run, using the current reader
and no Host initialization (`.local/analysis/run-repairs-20260921/`):

| Operation | Earlier observation | Repaired observation |
| --- | --- | --- |
| Interaction trigger lookup | 3.26 s; about 845 MB decoded | 0.424 s; 5.06 MB decoded |
| Full research projection | 5.92 s; about 867 MB decoded | 3.255 s; 160 MB decoded, now prepared off the control thread |
| Warm Host STATUS | See original diagnostic for the queued live failures | 0.107–0.116 s in two local samples; 0.713 MB decoded |
| First Host STATUS, empty charge cache | Not sampled equivalently | 1.539 s; 119.5 MB decoded to reconstruct closed charges |

These are individual local read measurements with profiling overhead, not
socket p95/p99 under concurrent CPU/GPU research. The profiler's `status_cold`
label was already charge-cache warm because `agent_usage` ran first; the
separate `status-repeat/profile-summary.json` measures a genuinely empty
charge cache. Generic standalone status construction
still performs a full historical read. The proposed two-client load latency
targets remain unqualified.

Final focused selection: 859 passed in 184.33 s across 28 test files
(`focused.xml`), covering ACI, accounting, interaction scheduling and audits,
control sockets, End-run/TUI, native output, final reports, views, monitoring,
seed skills and documentation. Compilation and `git diff --check` passed.
This selection is G0 repair evidence, not a full G1 run.

The two cases in `test_combined_interaction_g3.py` passed in
196.70 s. Each has 90 successful closed Attempts, six actual CPU Process
executions and six canonical outcomes, one bounded Leader round, six Debate
statements, a Judge, two participant restart plans and one final report. The
negative case keeps every outcome CLEAN_REFUTATION and every best_chain null.
Both independent Oracles passed; recorded process groups were checked absent
again after Host cleanup. Copies of Attempts, interaction events and audits
are in `combined-g3/`, with counts in `combined-g3-summary.json`. The transport
is explicitly fake-agent/4 through real AgentSessions, Host, sandboxed CPU
execution and publication; this is not native model-choice evidence.

The negative case then passed with actual same-version Host close/reinitialize
after both `leader_round_started` and `debate_restarts_designated` (108.72 s,
`recovery-g3.xml` and retained root `recovery-g3/`). Three Host generations
jointly produced the same 90 successful closed Attempts and six CPU outcomes,
with exactly one Leader round and two participant plans. The independent
Oracle and process-residue checks passed. These are quiescent committed-boundary
restarts, not injected SIGKILL while an experiment is running.

The installed native Claude CLI final-report G3 test passed (50.02 s), using a
local scripted provider with no paid model. The native-output regression
separately demonstrates missing outcome citations rejected and repaired within
one invocation. Audit counterexamples reject a foreign/uncited Leader parent,
wrong Debate parent, duplicate participant plans and premature restart finish.
Focused slow-view tests accept status/cancel while rendering is blocked and
publish only the final terminal view. Source-path/private-view isolation and
budget rollback protections remain covered by their existing tests.

During development the first CPU sample rejected runtime drift after source
was edited while the test was active. A subsequent fixture selected a Leader
before its own contribution cycle closed; it now respects the frozen eligible
contribution list. The negative CPU sample also exposed the audit's incorrect
late-node budget time, fixed by reusing actual batch admission. These failures
were not turned into accepted runtime behavior. Final successful samples used
unchanged runtime sources throughout each run.

No paid model/GPU run or existing research run was started or resumed
for this repair. The older run had already reached CANCELLED r356 under its
own frozen bundle after the user's explicit shutdown instruction; all 320
Attempts were closed, recorded process groups absent and its Host unit/socket
gone. Its current-reader read-only Oracle passed with 44 canonical outcomes
and no issues (`retained-oracle.json`). The original bundle and scientific
records remain unchanged.

Limits: the original TUI disappearance was not reproduced. An observed
revision-conflict rejection and overwritten error message are established;
they are not a proven cause of the historical process exit. Diagnostics close
that evidence gap for future failures without swallowing exceptions. New
Leader/parent semantics apply only to fresh ACI-21 runs. Full G1, retained
cohort G2, GPU G4, paid-model G5 and soak qualification were not performed for
this unfrozen candidate.

### Readiness qualification continuation, 2026-09-21

The user subsequently authorized the existing bounded qualification plan:
`glm-5.3-flash`, a fresh 13 USD cohort (7 USD common CPU, 6 USD resource CUDA),
G4 on GPU 2 `GPU-91a3b0a9-d48b-1503-2893-33153c3fe405` and GPU 3
`GPU-20ca7d4a-89d1-ec85-9bee-690afdd3283a`, and G5 resource on GPU 3 only.
Use the existing `/home/gray/miniconda3/envs/erc-cu128` environment and wait
for capacity. No additional paid retry, budget increase, old-run mutation or
long scientific-topic launch is included. Limits and the reviewed invocation
are retained in `.local/qualification/aci21-20260921/live-validation-plan.md`.

The user subsequently reported GPU 0 and 1 free. Metadata confirms 24,121 and
24,118 MiB available, while GPU 2 and 3 still have about 1,448 MiB each. The
waiting service stopped before either GPU lane started; G4 now uses GPU 0
`GPU-d469e765-ad52-350d-2cd4-9b12e5e5bc84` and GPU 1
`GPU-e96b09e9-0bcc-d031-acce-bb705dabfa31`, with G5 resource on GPU 1.
The original invocation/log remain retained; model, budget and test requirements
are unchanged.

G1 now covers all 5,893 collected identities.
The initial sequential invocation (689 passed / six failed, 1,606.72 s) was
interrupted and its remaining 310 files ran in three ordinary pytest batches.
Combined initial results are 5,818 passed, 40 failed, nine fixture setup errors
and 26 explicit opt-in skips, with no missing or extra test identity. Every
failure is retained and passed focused follow-up. The second complete G1 ran
in four ordinary pytest batches against the repaired candidate in an isolated
Git worktree (1,444.02 / 1,569.53 / 1,554.10 / 1,319.01 s). Its raw result is
5,866 passed, one failed and 26 opt-in skips, covering exactly the collection.
The remaining failure was an output-cap fixture's throughput assumption:
it expected a flood producer to reach one MiB within 0.2 s, but under load the
correct timeout closed it after 485,376 bytes. The fixture now waits for the
actual live producer to reach the cap before advancing the adapter's existing
test clock; byte limits, typed timeout and bounded shutdown assertions remain.
The entire adapter file passes 192 cases (11.00 s). The later browser click
fixture also passes its whole 17-case file (120.64 s). Final current identity
coverage is **5,867 passed / 26 explicit opt-in skips**, with no unresolved
failure; the initial failures and final raw failure remain in the JUnit logs.
All 135 runtime files match the tested worktree byte for byte and were unchanged
through the final qualification. Existing compiler/Ruff/import checks pass.

The real compatibility defect was in the pinned pre-batch ACI-13 path:
`_drive_ready_expert_update` returned false when asynchronous view preparation
or bounded admission had not yet created an Attempt. The ordinary policy could
then record a premature verdict, omit a required update, and skip a dependent
expert. It now treats a required unfinished update as pending, using existing
task facts. The complete parallel/dependency and single-lineage files pass
unchanged (eight and seven cases, 175.06 s and 24.31 s); current ACI >=14 uses
the separate batch driver. No old run is migrated or resumed by this repair.

Fixture refreshes align tests with the existing runtime semantics: current ACI
assertions use 21; bounded waits replace tight tick counts; manually controlled
submission tests hold the local execution adapter until all submitters exist;
transition fixtures include their durable revision; and native reading tests
hold provider replies until all three actual readers are admitted. The native
common-goal test uses the existing PAUSE/RESUME command to enforce its intended
local-failure-then-global-success order despite fair component rotation.
Browser automation waits for animation-frame graph fitting before choosing
mouse coordinates. No production browser change was made for that fixture.

The unchanged public template stops at its first qualified success. Its test
counts each Process record against its preregistration and canonical outcome,
separately counts actual spawned CPU effects and cancellations before spawn,
retains all six hypotheses, and forbids new execution after stop. The separate
native interaction case still requires six actual CPU experiments. The physical
core limit remains 27,550 lines with the same counted files: 150 lines transfer
from StateStore's allocation (550 to 400; 361 actual) to WorkflowHost (4,550 to
4,700), reflecting bounded view scheduling in the sole writer. No total limit
or lifecycle validation was relaxed.

Completed evidence in the same directory:

- Installed wheel smoke: an independently installed CLI/Host completed five
  Attempts, including two real CPU Processes, and exited with no recorded
  process group or control socket remaining. The Oracle and installed MCP
  initialize/list/inspect checks pass. Wheel and source package runtime files
  are byte-identical; the interpreter path and source-only optional adjacent
  `environment.yml` explain their different existing runtime identities. No private roots were
  packaged. The final sdist audit also caught six omitted public guide assets:
  the dotenv example and five README screenshots. MANIFEST now includes those
  exact existing files; extraction is compared byte for byte against source.
  The repaired wheel repeats this closure successfully. Its first
  retry accidentally used a symlinked-venv interpreter outside its sandbox
  prefix; that original timeout/quarantine remains recorded. Using interpreter
  copies fixes the test environment. The failed disposable smoke was cancelled
  with its exact original interpreter/bundle, both Attempts closed, Oracle clean.
- Retained G2 cohort: all ten existing MERC runs are unchanged before/after
  current installed-reader and original bundled-reader audits. Nine current
  audits are clean; `MERC-causal-202609161513` retains exactly its known
  `session_archive_missing` finding. The original 150044 bundle still reports
  its two historical archive-validation issues; the current reader is clean,
  consistent with its previously qualified behavior. Repeating all ten with the
  repaired wheel gives the same result and unchanged retained bytes. This is a matched known
  finding, not a claim that every historical run is fault-free.
- Native ACI-21 three-expert interaction: 90 successful closed Attempts,
  six actual CPU Processes, one Leader round, six Debate statements, two
  participant restart plans and a final report. The complete test passes in
  445.51 s; its Oracle and independent post-close process-group census pass.
  Native Grounding dialogue/restart/CPU closure also passes (45.99 s).
  Resource-learning native CPU closure passes in 91.18 s under a short
  temporary path. Its first refresh's chosen evidence path exceeded the Unix
  socket limit before Host creation; the failed invocation remains recorded.
- Resident Host control sample: 82 actual socket STATUS requests while two CPU
  cycles and background projection complete, maximum 0.205421 s; the final
  concurrent qualification repeats 83 requests with maximum 0.995769 s. Host exits
  zero, socket disappears, final projection and Oracle are correct. This is a
  bounded single-client observation, not a concurrent-client p95 qualification.
- Native paper-reading completion, End run and quarantine: five cases pass in
  311.48 s; independent census confirms closed Attempts and absent recorded
  process groups. Common acceptance/source-read native checks pass four cases
  in 387.76 s, including both exact source-reading variants. The final independent
  census covers 11 native/template databases: 295 CLOSED Attempts and 263
  recorded groups, all groups absent and all control sockets gone
  (`final-native-cleanup.json`).

The approved G5 common CPU sample passes in 720.45 s with `glm-5.3-flash`
and CC 2.1.278: COMPLETED r18 / GOAL_MET, 11 successful closed Agent Attempts
and one actual successful CPU Process. Native tools show the seeded test fail,
a code edit, then a passing test; the generated shared evaluator, exact source
reads, accepted outcome and final-report artifact satisfy the existing canary
checks. Independent post-close Oracle and process-group checks pass; all 12
recorded groups are absent. Recorded charge is `4.28304699999999993` USD
(4.283047 USD rounded), with no unknown reservations. This is a one-expert
compatibility sample, not a scientific-topic or real-model Leader quality claim.
Evidence: `g5-common/current-g5-evidence.json`, `g5-common-result.json` and
`g5-common-postclose.json` under the continuation evidence directory.

After CPU, the fresh cohort had approximately 8.716953 USD unspent; the resource
sample retained its original 6 USD allocation. No additional paid retry is
authorized. The GPU wait preserves
existing test requirements: G5 needs at least 4 GiB free; G4 initially checks
6 GiB, and its 16,384 MiB estimate case additionally needs the 512 MiB reserve.
The one-shot service `hypoloom-qualification-aci21-gpu01-20260921.service`
checked both UUIDs for at least 16,896 MiB free and invoked the existing ten-case
G4 selection once. Its runtime-version check reused the existing source identity;
no retry or budget expansion was configured. It is an ignored local invocation,
not a product scheduler or a new qualification authority. Original invocation,
log and result: `gpu01-queue-invocation.json`, `gpu-wait.log`, `g4.xml`,
`g4-cleanup.json`.

The first GPU 0/1 invocation finished with **nine passes / one failure in
301.98 s**. The shared-learning case retained one unknown final attribution
sample per card (`process_exited`, 1/44) for one Process; its existing strict
complete-measurement assertion failed. The initial-memory waiting/calibration
case passed. All eight CUDA Processes succeeded; independent post-close
inspection confirms four clean run Oracles, 80 CLOSED successful Attempts,
two clean pools with eight released requests and no recorded process residue.
Subsequent resource queries correctly mark the incomplete sample `unknown`
and expose no measured peak. The failed service did not launch G5. One fresh
unpaid recheck of only the failed case used unchanged source and assertions;
the original failure is retained (`g4-initial-finding.md`, `g4-postclose.json`,
`g4-refresh-invocation.json`). This recheck **passed in 140.54 s** with complete
owned measurements, two clean run Oracles, 40 CLOSED successful Attempts
(36 Agent / four CUDA Process), four released pool requests and no recorded
process residue (`g4-refresh.xml`, `g4-refresh-postclose.json`). The ten G4
identities therefore have current passing coverage, while the original failed
sample remains explicitly excluded from complete-measurement qualification.
No runtime or test assertion was changed, and no retry loop was introduced.

The first paid resource sample **passes in 743.60 s** with `glm-5.3-flash`
on GPU 1. It reaches COMPLETED r18 with 11 successful closed Agent Attempts
and one successful real CUDA Process. Native traces prove resource Skill and
resource-query use, wrapper code authoring, the exact CPU local-check command
and result read, ordinary submission/wait, and the subsequent query of the
actual Process measurement. The accepted checksum is 6; 13 complete owned
samples have zero unknowns and a 362 MiB peak, against the admitted 2,048 MiB
workload estimate and unchanged 512 MiB pool reserve. The supplied probe/data
remain unchanged, and final-report/source/transcript assertions pass.

Independent post-close Oracle and pool audits pass: 12 CLOSED successful
Attempts, all 12 recorded groups absent, one released pool request, no pending
commitment, and both control/pool sockets absent. The transient service
`hypoloom-qualification-aci21-g5-resource-20260921.service` exited zero;
GPU 0/1 returned to about 24 GiB free. Its original invocation/progress/result
are in `g5-resource-invocation.json`, `g5-resource.log`,
`g5-resource-result.json`; detailed existing checks and independent closure
are in `g5-resource/resource-g5-evidence.json` and `g5-resource-postclose.json`.
The first ad-hoc independent audit incorrectly supplied relative paths to
absolute session/context bindings; rerunning the same Oracle with resolved
paths passes without modifying the run. That invocation and explanation are
retained in `g5-resource-postclose-relative-path.json` and
`g5-postclose-path-note.md`; no product validator was changed.

GPU recorded charge is `3.63389499999999950` USD; together with CPU the fresh
cohort totals `7.91694199999999943` USD (**7.916942 USD rounded**) against the
approved 13 USD, with no unknown reservations. No paid sample was retried.
These are CLI/Host usage records, not a provider invoice. Runtime source
identity remained unchanged throughout G4, its unpaid recheck and GPU G5.

The current offline, retained-run, installed/native closure and real-model
CPU/GPU evidence supports a **fresh bounded scientific-topic run**. Real-model
multi-expert Leader/Debate decision quality remains to be observed in that
topic; deterministic native interaction already covers scheduling and directed
restart closure. Historical TUI-crash attribution and concurrent-client control
latency remain separate limits. No long scientific run, old-run mutation,
commit or push is part of this continuation.

## Current development — prompt offload, termination honesty, tick cost, and attribution-aware GPU pressure, 2026-09-19

Candidate: uncommitted working tree on dev @ f2de65a carrying eight reviewed
repair directives plus their audit follow-ups (44+ files; adapter
`claude-code-bwrap/62`, compute `local-compute/6`, runner
`local-process-group/3`; all changes apply to new runs only through the
existing version pinning). Origin: read-only analysis of the terminated run
`MERC-causal-202609172306` (its own evidence is untouched), which found four
defect classes — monotonically growing prompts killing agent tasks (the
OBSERVER interaction trigger died every cycle; zero DEBATE/LEADER tasks in
the whole run), a TERMINATE_LINEAGE teach-vs-enforce semantic gap that
permanently collapsed three-way expert parallelism to one, a GPU-pressure
kill that discarded 4.2 h of completed scan work, and control-channel
starvation that made the operator's (durably accepted) End Run look failed.
Two local audit records drove the work: findings/fix plan and the directive
audit with execution order (both under `.local/`, git-ignored); a second
independent implementation audit (same place) adjudicated the eight
directives and set the must-dos below.

Changes. ①a: oversized prompt inputs (four context suffixes at any size, or
any input ≥ 64 KB) render as `{ref, media_type, path, size_bytes}`
advertisements under the existing DEBATE_JUDGE precedent; request bytes and
`input_digest` are unchanged, so every oracle is unaffected; the common
instruction teaches reading the advertised read-only `/inputs` source.
①b: CLI-side uncompactable-single-turn failures classify as
`input_too_long` only for the stateless commands (OBSERVER, DEBATE_JUDGE),
whose byte-identical retries are futile; stateful `--resume` retries (which
succeed through provider-side compaction) keep their existing
classification. ①c: attempt diagnostics gain `prompt_bytes`,
`rendered_input_bytes`, `offloaded_refs` (additive, not oracle-consumed).
②c: the TERMINATE_LINEAGE schema description and system prompt now state
the enforced semantics (the Host never schedules another cycle; only a
Debate/Leader-designated restart reopens; untested sibling chains are
silently abandoned); the SUPPORTED⇒TERMINATE ACI rule is unchanged.
①d: the per-tick adjudicated-worktree retirement is gated on the accepted
batch history actually moving (process-local memory, replayed once after a
Host restart), lists OPEN attempts only, and memorizes the
permanently-dirty retained outcome instead of re-running git every tick —
the audited run's ~20 s/tick (~170 MB full-table load plus repeated git
cleanup on three permanently-dirty worktrees) becomes flat ~5 ms.
②e: the End-Run modal's socket timeout is 30 s, and on timeout it
reconciles against the durable command row (idempotent request id) instead
of declaring failure; the monitor's STATUS polling backs off exponentially
instead of hammering a wedged socket.
③c/③d: the device-level GPU pressure backstop now consults the already
sampled pool ownership attribution — `free + owned ≥ reserve` means the
pressure is external and the process is spared; `owned_memory_mib_at_decision`
and `attribution` are recorded, and the payload is renamed with explicit
`gpu_` fields. The oracle (single- and multi-GPU branches) accepts exactly
the old or the new key set (version lives only in `configuration_id`, so
shape is the evidence, per the oracle's existing idiom); the real run's
old-shape payload re-audits clean. ③b: only the pressure-cancel path uses a
30 s terminate grace (recorded in the payload); operator/timeout cancels
keep 0.5 s. En route the real lane exposed that the grace loop never
reaped the SIGTERMed zombie root, so a 30 s cancel blocked the Host tick
for the full grace; one line (`runner.py` polls the child inside the loop)
makes the cancel return once the group actually exits.

Audit must-dos landed: ①d error-result memory (dirty worktrees no longer
re-run git per tick; the test that codified the repetition was rewritten),
③b applied grace in the payload (oracle both branches), ③c real-GPU
evidence, the CC-carryover `clamped` marker, autouse clearing of the
file-backed request registry, and direct attribute access with test
doubles declaring fields (no production getattr tolerance).

Evidence (archived under `.local/qualification/final-20260919/` unless
named). Replays of the terminated run's real requests: OBSERVER
775→93 KB, EXPERIMENT_EXECUTION 1766→345 KB, FINAL_REPORT 885→178 KB,
expert nodes ~610→~300 KB (`.local/replay_offload_sizes.py`); failure
classification replay: stateless → terminal, stateful → unchanged
(`.local/replay-20260919-cli-oversize-classification.py`). Tick timing:
549.6 ms → 19.9 ms full/OPEN at the audited 214-row shape, dirty case flat
~5 ms after the memory fix. G1 full offline: `g1.xml` 5,843 passed / 26
skipped with 3 native failures caused by a test-harness patch point lost
in the file-backed fixture (not production); fixed and refreshed as
`g1-refresh.xml` (49 passed including the three identities). G2 retained
cohort: 8/8 runs audited with the current candidate oracle — 7 clean plus
one verbatim-known historical finding — with database bytes proven
unchanged (`g2-retained.json` and per-run reports); the portable fixture
selection is 39/39 (independent audit run). G3 lane: the four scenarios
this round had not covered pass (three inside `g1.xml`, the native
learning scenario inside `g1-refresh.xml`). G4 shared CUDA on the two
authorized UUIDs: the pressure-attribution lane
(`.local/qualification/g4-pressure-20260919.xml` + retained roots) covers
unknown-kill (exclusive mode), external-spare and self-kill (pool mode)
on real workloads, with oracle validation of the new payload end-to-end;
the main lane (`g4.xml` + `g4-roots/`) is 9/10 — the two-host case failed
the closure's zero-unknown-tolerance owned-sample assertion, the same
pre-existing flake seen in the 20260917 batch at the same assertion (the
sampler records a bounded unknown when the device's CUDA process set
changes mid-scan, a teardown race under concurrent workloads; the
assertion is documented here as a known flaky surface and was not
weakened — the same test passes on targeted reruns, 2/2 today). G5:
one paid g5-local sample (`g5-local.xml` + `g5-local-roots/`, run root
archived) passed on first execution — the real run reached
COMPLETED/GOAL_MET (canonical SUCCESS, metric 0.875) on adapter /62
with CC 2.1.277 and glm-5.3-flash, charging 2.827766 USD exactly equal
to reported (zero unknown attempts; declared cohort 5 USD with zero
prior, 2.172234 USD remaining). The offload contract held end-to-end
with a real model: the FINAL_REPORT prompt advertises its 22,435-byte
`--final-report-context` as a 342-rendered-byte manifest
(`offloaded_refs` of record), the transcript shows the model issuing
`Read /inputs/0002.txt` and consuming the offloaded source, and every
attempt closed with exact accounting under the CC 2.1.277 carryover
repair.

AGENTS.md justification for the ③c mechanism (the round's one new
decision rule): (1) reproducible failure and impact — run
`MERC-causal-202609172306` attempt `…--component-3--experiment-10--process-1`:
device free pinned at 3,901 MiB against the 4,096 MiB reserve (sustained,
minimum == latest) while the process's own attributed resident peak was
552 MiB of 20,675 MiB used; the device-level rule cancelled it, freeing
almost nothing, and discarded 4.2 h of completed scan work
(`EXECUTION_FAILURE` of record). (2) Failed asset and boundary — the
Host-side pressure-cancel decision in `WorkflowHost._poll_processes`, at
the boundary between the device-level tracker (documented to never
signal or attribute) and the pool ownership sampler that already sampled
every poll for evidence but was never consulted. (3) Why existing
machinery is insufficient — the tracker deliberately never attributes;
pool admission accounts capacity only at dispatch time, so a foreign
tenant's in-run consumption is indistinguishable from self-consumption
at cancel time. (4) Smallest mechanism — reuse the existing `sample_owned`
accumulator: one read accessor and one pure decision rule
(`free + owned_latest ≥ reserve` spares), recorded per device with the
decision evidence in the renamed payload; no new sampler, no durable
state, no parallel authority; removal is reverting the decision line.
Old runs keep their frozen versions and audit against the old payload
shape; nothing is migrated.

CC version crossover of session transcripts (accepted risk). The
per-Attempt billing repair below keys off `cost-state` transcript records
that only CC ≥ 2.1.277 writes; session preparation records the newest
such record as the deduction baseline whenever one exists. If a
transcript carrying these records is later resumed or forked by an older
CLI (< 2.1.277) — a binary downgrade mid-run, which auto-update never
performs and only a manual version change can cause — the older CLI
ignores the records and reports a zero-based ledger while the Host still
deducts the recorded baseline: an over-deduction that under-accounts the
attempt, with underflowed fields flooring at zero and named in
`provider_session_carryover.clamped`. This direction stays fail-closed,
not silently under-billing: once a deducted input total falls below the
observed assistant/wire input floor, the existing lower-bound guard
withholds `provider_accounting` and the attempt closes as
`invalid_provider_metadata`. Upgrades are safe in both directions.

Known residuals (recorded, not blocking): pressure decisions made on a
single incomplete attribution sample stay conservative (a sustained-two-
poll or last-complete-sample refinement is a follow-up); a pressure
cancel blocks the serve tick only for the workload's actual remaining
life after the zombie fix (a non-blocking cancel is a separate optional
change); the twelve other unfiltered `list_attempts()` full-table scans
in interaction/debate/workflow hosts are filed as a follow-up; the
offload gate is the boolean `tool_workspace` (no legacy workspace is
paired with schema ≥ 14 in practice; tightening it to the session
workspace type is optional); the file-backed request registry clears
per-test in its own module (suite-wide bounding would move the fixture
to conftest); the G4 closure zero-unknown tolerance is a known flaky
surface under concurrent CUDA churn (see above).

## Current development — per-Attempt billing under CC 2.1.277 cumulative session ledgers, 2026-09-19

The local Claude Code auto-updated to 2.1.277 (`~/.local/bin/claude` replaced
2026-09-19 10:07; `~/.local/share/claude/versions/` shows daily builds
2.1.273 → 2.1.274 → 2.1.275 → 2.1.277). Its changelog entry "Fixed a headless
resume (`claude -p --resume`, the SDK, a VS Code extension window reload)
starting the session's cost and usage totals at zero; headless sessions now
save their totals at exit" changed the result-event contract Hypoloom bills
from: CC ≥ 2.1.277 appends a cumulative `cost-state` record
(`totalCostUSD` + per-model `modelUsage`) to the session transcript at each
headless exit, and a `--resume`/`--fork-session` launch seeds its reported
`modelUsage`/`total_cost_usd` from that record instead of starting at zero.
CC ≤ 2.1.275 writes no such record (verified: zero `cost-state` occurrences in
a 2.1.275 fixture's session files, 14 in the same fixture under 2.1.277).

Four native G3 tests failed on the unmodified tree under 2.1.277 and all
passed under 2.1.275 without any tree change, exonerating the five in-flight
workstreams:

- `test_model_usage_accounting_g3`: a same-session FINAL_REPORT retry
  reported cumulative modelUsage 35 (invocation 29 + failed attempt 6) and
  cumulative cost; `assert 35 == 29`.
- `test_partial_cli_accounting_g3`: the same mechanism recorded retry
  `token_usage.input_tokens` 11 (5 + 6); `assert 11 == 5`.
- `test_execution_completion_g3`: a chain fork inherited the synthesis
  parent's saved totals under the parent's different model identity, so the
  design attempt's ledger held two model keys and failed
  `multiple_model_identities`; the run COMPLETED with zero outcomes.
- `test_structured_output_recovery_g3` (3 cases, found during diagnosis):
  forked design accounting recorded cap+2 (cap+1 + seed 1).

Repair (no assertion weakened): `agent_sessions.prepare` records the newest
`cost-state` record of the exact transcript bytes it hands CC as
`PreparedSessionWorkspace.session_usage_baseline` (`_session_cost_state`;
absent for older CC/fresh sessions, so behavior there is unchanged).
`claude_agent_host` deducts that recorded carry from the result ledger
(`_session_usage_view`) before every per-Attempt accounting path; models the
invocation did not use drop out of the ledger, cost is deducted in Decimal,
and `provider_session_carryover` is recorded in diagnostics. The two
harnesses that assert on the raw native ledger now derive the same
invocation view from the archived prior transcript (which is byte-identical
to what preparation handed CC); every asserted quantity (29/11, the
17/3/5/7 fallback delta, cap+1, cost equality) is unchanged.

Focused evidence: 3 new unit cases (resumed cumulative ledger deducts carry
and drops the unused carried model, with and without structured-output
failure; resume/fork preparation records the newest cost-state, supersedes
older records, skips an unterminated partial tail, and records none for
pre-2.1.277 transcripts); 306 focused cases pass. Native evidence: all four
failing files plus `test_full_cc_host_g3.py` green under CC 2.1.277
(1+1+1+3+3) and under CC 2.1.275 (1+1+1+3+3). No paid, GPU or online lane
ran; existing runs, Profiles and credentials are unchanged.

## Current development — project GPU-pool detection and managed creation in init, 2026-09-17

Under the user's direction after a new run's init exited (the intake could
neither find this project's pool nor create one): the `/autoresearch init`
pool step now detects and — only through the reviewed plan — deploys the
node-local project pool instead of relying on an operator-supplied external
endpoint. The triggering mis-inference is preserved as context: the intake
labeled the wedged `MERC-causal-202609170117` nonpooled from socket absence,
while its durable CREATED payload carries the `gpu_pool` binding (socket under
the Prometheus project's `.local/gpu-pools/shared-3090x4/`, namespace = run
ID, server UID 1001). That pool is foreign-owned and stays untouched.

New bounded helper `.claude/skills/autoresearch/scripts/manage_gpu_pool.py`
mirrors `manage_host.py`'s user-systemd discipline. `status --repository`
(read-only) reports each conventional project endpoint under
`.local/gpu-pools/<name>/control.sock` (live/stale/unauthorized/absent with
served UUIDs) and recalls prior runs' stored pool bindings newest-first from
mode-ro state databases with current liveness probed; against this checkout
it reports the project pool absent and both September 16–17 pooled bindings
live. `ensure` deploys only beneath the repository's `.local/gpu-pools/` and
only for the invoking UID, deriving `hypoloom-gpu-pool@<escaped-root>.service`;
an active unit serving the exact reviewed device set is reused (`reused`),
while a live unmanaged socket, a conflicting unit and a foreign root are
refused (`service_busy`, `pool_conflict`, `foreign_pool_root`); a failed
readiness stops the new unit. There is no stop/replace subcommand: pool
teardown stays a separate operator action. The skill text keeps every existing
pool caution and states the new boundary: deployment only through the reviewed
plan and helper, after **Start now** approval, before the managed Host start;
**Prepare only** never runs it.

Focused evidence: 13 new `tests/runtime/test_manage_gpu_pool_script.py` cases
(FakeSystemd plus a real pool-protocol server on a short AF_UNIX path):
absent/live/stale detection, newest-first deduped recall with the source
database byte-identical, exact unit/argv/property creation with no ambient-key
leakage and a mode-0700 pool root, idempotent reuse, and foreign-root /
busy-socket / conflict / ready-timeout refusals with bounded unit cleanup.
Adjacent suites pass: 36 operator-skill + 23 manage-host + 4 documentation
+ 3 public-profile + 27 shared-GPU closure/integration cases. Default-ruff
findings on the two new files are the same classes the sibling helper already
carries; skill scripts are not held to default ruff. No GPU, paid or online
lane ran; the first real project-pool creation happens at the next actual
init as the operator-approved action. Existing runs, the Prometheus pool,
Profiles and credentials are unchanged.

## Current development — wire-final input floor for bill completeness, 2026-09-17

Implemented under the user's repair authorization after the read-only
adjudication recorded below: the k3 endpoint's stream-start usage runs a few
tokens above its final per-request usage, and CC records that start snapshot
in transcript assistant events, so the transcript-derived lower bound could
exceed the exact billing ledger and fail completed attempts as
`invalid_provider_metadata` (15 of 62 Agent attempts in the wedged run; both
9-17 paid samples). The guarded quantity was right; its proxy was not.

The repair reuses the existing exchange capture, no parallel authority:
`provider_observation.observed_input_floor` reduces the already-retained wire
exchanges to the per-request final input sum only when every `/v1/messages`
exchange is complete, measured and single-model (otherwise None).
`_provider_accounting` accepts an optional `observed_wire_input_floor`; when
present it replaces the transcript bound as the floor the CLI ledger must
cover (diagnostics label `provider_accounting_floor="wire_exchange"`), so the
start/final skew cancels while a ledger below the wire still fails closed.
The full-CC finalize path reads the capture once before response parsing and
reuses it for the existing observed-usage/failure diagnostics; without a
complete single-model capture the transcript guard is unchanged. The legacy
direct-provider branch has no capture and keeps the transcript guard. No
schema, identity or budget change.

Focused evidence: 14 new `tests/runtime/test_provider_wire_floor.py` cases —
floor reduction (complete/unmeasured/multi-model/non-generation), response
level (skew accepted, below-wire ledger still rejected, non-integral floor
keeps the transcript guard), and a real-CLI full-CC session whose scripted
upstream emits start=2/final=1 usage per request: the design attempt closed
SUCCEEDED with transcript bound 12 > ledger 6 == wire floor 6, while the same
captured stdout replayed without the floor still fails
`invalid_provider_metadata`. Adjacent suites pass: 165 (accounting guard
files) + 196 (full-CC adapter, file-tool CLI, session CLI, full CC host,
agent host). Full G1 after the repair: **5,760 passed / 25 opt-in skips / 0
failed**, 4,999.79 s (`g1-wirefloor.xml`; pre-checks compileall, ruff,
`git diff --check` pass). No paid/GPU lane was rerun; the k3 skew samples and
the foreign GPU tenancy remain as recorded below.

## Supplementary qualification — provider-auth-cooldown repair candidate, 2026-09-17

The user requested a complete G0–G5 batch for the uncommitted provider auth
cooldown repair, then a commit to dev. The offline lanes pass in full. The
GPU lane and both executed paid samples failed on independent environmental
and provider-side causes, preserved with their roots below; none involve the
repair. The third paid sample was not launched once two consecutive samples
established a systematic provider-metadata wall, so its allocation survives.
Detailed evidence stays in ignored `.local/qualification/full-20260917/`.

### Candidate and environment

HEAD `3f3bbd4a7f53b421dd6c118435ec2371ae4b9b08` (dev) plus the uncommitted
repair tree; runtime identity
`sha256:25f500a5090733520fe6e861bd3be98c73e4ca82005752430db5b36a88207303`,
ACI 20 (`candidate-run-version.json`). Control Python is the project Conda
3.12.14 / SQLite 3.53.4. Native CC pinned to the current
`/home/gray/.local/share/claude/versions/2.1.274` (upgraded the same morning);
model `k3-256k` through the existing mode-0600 settings file. Approved GPU
UUIDs `GPU-d469e765-ad52-350d-2cd4-9b12e5e5bc84` and
`GPU-20ca7d4a-89d1-ec85-9bee-690afdd3283a`; CUDA runtime
`/home/gray/miniconda3/envs/erc-cu128`. Production `profile-check
MERC-causal` admits with the existing credentials file (exit 0). A fresh
cohort was declared: prior 0, ceiling 15 USD, 5 USD per paid sample, no
automatic paid retry (`cohort-accounting.json`).

### Executed evidence and limits

| Stage | Actual result | Evidence and boundary |
| --- | --- | --- |
| G0 | Ready | `g0-environment.json`, `g0-gpu-inventory.txt`, `merc-profile-check.json` |
| G1 | **5,746 passed / 25 opt-in skips / 0 failed**, 4,999.28 s | `g1.xml`; compileall, ruff F821/2/3 and `git diff --check` passed. An earlier invocation killed by a 3,600 s harness cap at ~61% is not evidence. The suite ran ~4× slower than the 9-16 invocation under foreign tenant load; per-test integrity is unaffected. The 25 skips are the documented paid/GPU/real-Telegram/tmpfs opt-ins. |
| G2 | 174/174 portable; retained cohort unchanged | `g2-retained.json`: six terminal roots plus the wedged run kept byte-identical databases, identical logical rows, artifact inventories and bundle readers across the lane. Readers: 0044's bundle keeps its two known archive errors; 161513 shows one pre-existing `session_archive_missing` (candidate and bundled readers agree; data-carried, unrelated to the repair). |
| G3 | 7/7, 470.30 s | `g3.xml` |
| G4 | 8/10, 680.18 s | `g4-environmental-analysis.json`: foreign tenant PID 2833485 held 15.2 GiB on GPU 0 and 254 MiB on GPU 3 throughout. `…_initial_memory_waits_then_calibrates…` could never grant 16,384 MiB per card (9,356 MiB free on GPU 0; pool requests stayed QUEUED `capacity_unavailable`/`fairness_wait` to the 540 s deadline). `…_two_hosts_share_real_cuda…` failed `unknown_sample_count == 0` with exactly one unattributed sample per card (1/41). Both failed roots show zero provider failures, zero `auth_failed` classifications and zero rate-limit gates, and the repair changes no GPU/pool/attribution bytes. Roots retained at `/tmp/pytest-of-gray/pytest-4511/`. Not rerun: the foreign allocation persists. |
| G5-local | **FAIL**, 774.17 s, 3.780730 USD known + 1 unknown attempt | `g5-local-result.json`. The run COMPLETED with GOAL_MET and the real CPU process succeeded, but the EXPERIMENT_EXECUTION agent attempt failed post-completion as `invalid_provider_metadata`: CC 2.1.274 reported `input_tokens` 415,342, five below the platform-observed assistant lower bound 415,347 (turn count also normalized 7→6). The strict accounting guard (`claude_agent_host.py:1768`) is untouched by the repair; the sample shows zero auth/rate-limit events. Closure requires a SUCCEEDED execution agent, so the sample stays failed. |
| G5-common | **FAIL**, 903.60 s, 7.302222 USD known + 3 unknown attempts | `g5-common-result.json`. Same signature struck twice (EXPERIMENT_DESIGN_IMPLEMENT, CYCLE_REPORT), SELF_EVOLVE timed out and FINAL_REPORT hit `profile_budget_exhausted`; the run FAILED. |
| G5-resource | Not executed | Two consecutive samples established the provider-metadata wall (4 events; the five 9-16 samples on CC 2.1.272/273 had none), and GPU 3 still carries foreign memory against the lane's identical `unknown_sample_count == 0` assertion. The 5 USD allocation survives. |

### Findings and smallest next steps

1. **k3 provider start/final usage skew trips the accounting guard —
   adjudicated after the batch (read-only, wire-level).** Four agent attempts
   across two paid runs reported aggregate `input_tokens` five below the
   platform's assistant-message lower bound, failing closed as
   `invalid_provider_metadata`. Adjudication: the failed sample's retained
   `provider/NNNNNNN` wire captures show the k3 endpoint's stream-start usage
   one token above its final per-request usage on 5 of 6 responses; the CLI
   billing ledger (`modelUsage`, costUSD) exactly matches the per-request
   final sums (415,342), while the transcript assistant events carry the
   start snapshot (input-only, cache fields zero), summing to 415,347 — the
   platform's max-per-message-id lower bound reproduces both exactly, so the
   platform arithmetic is correct and the bill is exact; the skew is
   provider-side. This is **not a 2.1.274 regression**: the wedged run on
   2.1.273 + k3[1m] already recorded 15 such failures among 62 Agent attempts
   (interleaved with successes — retries land on unskewed responses), while
   2.1.272 + k3-256k on 9-16 recorded final split usage (sum == aggregate on
   all 12 attempts) and 2.1.273 + deepseek-v4-pro shows start == final on its
   endpoint. Pinning 2.1.273 is therefore **not** a remedy for k3. Real-run
   implication: any CC version with k3 currently fails ~15–25% of agent
   attempts post-completion, burning node retry budget but not corrupting
   bills; a platform-side tolerance for the start/final skew pattern (or a
   non-skewing endpoint) is a separate decision. Redacted captures retained
   under each sample's `state/diagnostics/`.
2. **Foreign GPU tenancy blocks the approved pair.** All four cards carry
   PID 2833485's allocations (15.2/15.8 GiB on 0/1, 254 MiB on 2/3). G4's
   two failures and the G5-resource deferral follow from it. One G4 rerun
   plus the deferred G5-resource remain admissible once the cards clear.
3. **The repair's own evidence is fully green.** G1's 5,746 passes include
   the new `tests/runtime/test_provider_auth_cooldown.py` and the extended
   observation/budget/lifecycle suites; G2/G3 confirm durable and real-Host
   behavior unchanged. No paid/GPU failure above involves repair code paths.

### Costs and cleanup

Known charged model usage is **11.082952 USD** (3.780730 local + 7.302222
common). The four unknown-reserved attempts carry the standing 1 USD each
convention, a conservative upper bound of **15.082952 USD** — marginally
above the declared 15 USD cohort ceiling on that convention; the in-run
guard on known charges (12.082952 peak) never exceeded it. The unlaunched
resource sample's 5 USD was not spent. Cleanup census
(`cleanup-census.json`): 693 databases across today's four pytest roots,
6,582 CLOSED attempts, 74 OPEN attempts that are intentional
crash/cancel/priority/tamper fixture states, 1,362 recorded process groups
with **zero live**, one intentionally corrupt fixture unreadable. The wedged
run `MERC-causal-202609170117` was never mutated and stayed byte-identical
across G2. No real-Telegram, online-literature or soak lane ran.

## Current development — provider auth cooldown and operator recovery, 2026-09-17

Implemented under the user's repair request after a real incident: run
`MERC-causal-202609170117` hit a provider 403 window (five-hour rolling quota)
at 04:14–05:27 +08; each infrastructure failure retried on the next tick, so
fifteen rapid rejections exhausted all three expert components' node budgets
within seconds each and permanently closed their control loops, leaving the
run ACTIVE with no dispatch, no escalation and a busy-poll Host. The user
acknowledged five-hour-limit and provider-instability 403s are
indistinguishable and asked for both unattended recovery and notified manual
recovery.

The repair reuses existing machinery, no parallel authority: exchange capture
now classifies upstream 401/403 as `provider_failure.code="auth_failed"`
(`provider_observation.py`; status-based, no message sniffing), retry class
stays INFRASTRUCTURE. The existing durable dispatch gate
(`_provider_retry_pending`, previously rate-limit only) now also gates on the
latest auth failure until `budget.provider_auth_cooldown_seconds` (default
18000, zero disables, cap 604800; validated in `agent_budget.py` and
`component_contract.py`), computed read-only from durable rows via
`provider_retry_gate`, so a Host reopen neither clears nor resets it. The new
operator control `PROVIDER_RETRY_CLEAR` (lifecycle CREATED/ACTIVE self-loop,
reason="operator") records a durable clear transition that supersedes only
earlier failures; a still-broken credential re-engages the gate on the next
failure. STATUS exposes `provider_retry`; the TUI shows a PROVIDER COOLDOWN
banner with a `p` confirmation modal (`provider_resume_tui.py`, mirroring the
end-run form); CLI `provider-retry-clear` mirrors pause/resume plumbing.
Derived product events `provider_auth_cooldown` (one per episode, not per
attempt) and `provider_auth_resumed` feed the existing notification tailer and
templates/local Profile event lists; a disabled cooldown emits no events.

Focused evidence: 80 passed across the new
`tests/runtime/test_provider_auth_cooldown.py` (real Host gate blocking,
reopen durability, operator clear, re-engage, expiry, event episode dedup,
Telegram text, TUI form) plus extended `test_provider_observation.py`,
`test_agent_budget_accounting.py` and `test_lifecycle_table.py`; adjacent
suites pass: 162 (batch host, tailer, end-run UI, events, paper reading,
public profiles), 179 (profile admission/v4, observer UI/reliability/services,
human answer), 43 (live status, runtime diagnostics, projection, execution
completion). The physical-LOC budget test's allocation table was explicitly
reallocated for the added workflow-core authority (workflow_host 4500→4550,
lifecycle 550→500; total unchanged), per its designed mechanism. The wedged
run was not mutated, cancelled or restarted; no paid/GPU/online call, full
G1–G5 lane, commit or push. The repair does not revive components whose node
budgets were already exhausted in that run.

## Current development — project-local research-runtime session records, 2026-09-16

Implemented under the user's relocation request after the credentials
consolidation. The Claude Code research runtime stored this project's session
transcripts in the user directory (`~/.claude/projects/-data-Gray-Project-Hypoloom`).
All 9 files (7,121,623 bytes; aggregate checksum verified identical before
and after) now live in the ignored `.local/claude/projects/` directory
(mode 0700), with a compatibility symlink left at the original
`~/.claude/projects/` path so existing and future sessions for this project
keep writing into the project tree. A write probe through the symlink landed
in the project copy, and `git check-ignore` confirms the location is ignored.
Hypoloom's own run records needed no move: Host state already lives in
`<repo>/state/`, and sandboxed agent session transcripts already live under
each run's `sessions/` tree (`agent_sessions.py`), never in the user
directory. Deliberately not moved: shared user-level configuration
(`~/.claude/settings.json` provider settings, `~/.claude.json`), the
cross-project empty `session-env/` residue and history, and the other
project's transcript slugs. Full `CLAUDE_CONFIG_DIR` isolation remains an
optional operator choice; it would duplicate provider settings into the
project and only takes effect for launches that set it, so the symlink
layout stays the single mechanism. No run, Profile or settings mutation; no
commit or push.

## Current development — single dotenv credentials file, 2026-09-16

Implemented under the user's configuration request after the stopped run's
Telegram source drift, then consolidated from per-key files into one file
under the user's follow-up. Local credentials now live in one ignored dotenv
file (`.local/credentials/hypoloom.env`; directory 0700, file 0600): it
holds `OPENALEX_API_KEY` and `UNPAYWALL_EMAIL` (migrated from the
`~/.config/prometheus/` originals, which are unchanged) plus the
`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` lines the user fills in. The format
is a strict subset (`KEY=VALUE` lines and `#` comments; no quoting or
expansion) parsed by `hypoloom/credentials.py` with absolute-path,
O_NOFOLLOW, regular-file, UTF-8, ≤8 KiB, duplicate-key and malformed-line
checks; errors name keys and line numbers, never values. The Profile declares
the one path explicitly, and the path string (never secret bytes) is
projected into the durable contract like any other control:

- `methodology.yaml` gains optional `literature.credentials_file`;
  profile_v4 validates it as an absolute, non-literal path. The autoresearch
  SKILL's literature argv guidance passes the same file as
  `--credentials-file` to serve, profile-check and start; the legacy
  single-secret flags `--openalex-api-key-file` / `--unpaywall-email-file`
  remain accepted, never mixed with it.
- `notify.yaml` Telegram gains `credentials_file`; the tailer reads
  `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` from it. `bot_token_env` /
  `chat_id_env` remain supported; exactly one source is enforced at
  validation and delivery.
- Public templates (en/zh), the new `profiles/templates/hypoloom.env.example`,
  a rewritten bilingual profiles/README credential section, the
  `docs/guides/notifications.md` and `SETUP.md` credential guidance, and the
  local MERC-causal Profile follow the convention.

Focused evidence: 294 cases across credentials, tailer, profile, operator-skill,
public-template, CLI, Unpaywall, admission and documentation suites pass, plus
69 admission cases standalone and 218 literature-module cases; the local Profile
admits clean through production admission and the offline literature preflight
builds from the real file without network I/O. Two `component_batch_profile`
fixture errors appear only when one pytest invocation mixes `tests/` root
modules with `tests/runtime/` modules in a particular order; the same
selection built from modules this change never touched reproduces them, every
module passes standalone and pairwise, and the standard `scripts/qualify.py`
lanes never mix those roots — a pre-existing pytest-9 collection quirk, not a
regression. The stopped run's frozen contract keeps the old names. Telegram
delivery is unverified until the user fills the two Telegram lines; no paid,
GPU or online call was made.

## Current repairs — provider-stream envelope tolerance, 2026-09-16

Implemented under the user's repair request after they stopped
`MERC-causal-202609161513` (CANCELLED r106 at 20:04:15 +08, all 102 recorded
groups absent, no socket; 85 Agent + 17 Process Attempts durable and
unchanged). Evidence: `.local/qualification/envelope-repair-20260916/`.

- Repaired platform defect: four EXPERIMENT_DESIGN_IMPLEMENT attempts failed
  as `invalid_tool_workspace` ("declared-source capture requires a structured
  candidate") although the Host Stop hook had accepted each candidate.
  `_unpack_provider_stream` required the single result event to be the last
  NDJSON record, but Claude Code 2.1.273 also emits (a) a marked synthetic
  zero-turn result (`origin.kind == "task-notification"`) when a resumed
  session inherits an orphaned background task, and (b) system lifecycle
  notices after the terminal result. The parser now takes the last result as
  authority, tolerates only the exactly marked resume artifact before it and
  only `system` events after it; duplicate real results, unmarked or non-zero
  earlier results, stream_event records and post-terminal provider content
  remain `invalid_provider_envelope`. The original failed Attempts and bills
  are retained unchanged.
- Focused evidence: 15 envelope cases, 210 module-suite cases and 4
  session-CLI cases pass; read-only replay of the four actual failed streams
  now selects the true terminal result with its candidate text. No full
  G1/G4/G5, GPU, paid or online qualification was run; the repair is
  uncommitted.
- Classified but not repaired (by design or model-side): five PROCESS LOST
  `capacity_unavailable` are the designed bounded capacity wait
  (LOST-before-exec plus Host retry, Oracle-validated) amplified by
  `max_active_processes` 6 exceeding the ~3-experiment effective GPU supply
  (two cards foreign-occupied); five EXPERIMENT_EXECUTION Agent 3000 s
  timeouts are the designed long-experiment handoff; two same-chain
  measurement declaration/authorship mismatches (10.637398 USD and
  5.263 USD) are model-side contract violations correctly rejected with
  retry=MODEL; one invalid_structured_output and two invalid_candidate are
  the expected retryable model-output classes. The 3 -> 6 agent-slot change
  caused no concurrency race; its real effects are a ~0.9 USD/min peak cost
  rate (169.8 USD in 3.7 h, null USD caps) and capacity-wait churn. Per the
  user's confirmed packing policy, per-card multi-experiment packing is
  intended and slots are deliberately not matched to card count: admission
  accounts `min_vram_per_experiment` 8192 + `gpu_vram_reserve_mb` 4096 =
  12288 MiB per slot (2 per 24 GiB card, 8 theoretical), while measured
  owned resident peaks of all 8 succeeded Processes are only 364-786 MiB
  (card-level peaks 3.2-7.7 GiB; one 15.6 GiB paired_ceiling_control
  outlier). The declaration paces admission only; lowering it in
  profiles/local/MERC-causal for a future run raises packing density at the
  price of admitting more heavy outliers per card.

## Supplementary qualification — approved repair recheck, 2026-09-16

**The approved supplementary work is complete.** G4, G5-resource and
G5-reading pass; the single public PDF fetch fails. The user approved the
complete bounded proposal below and raised the same cohort threshold to
55 USD. Evidence is in `.local/qualification/qualification-recheck-20260916/`.
The initial 13 source databases still account for 42.434221 USD, including
1.20 USD retained unknown reservations. No old failure, charge or reservation
is reset. The runtime remains `38d80533...` / ACI 20; 518 shipped code, test and
operator files match the repaired source package. Control Python 3.12.14,
SQLite 3.53.4, CC 2.1.273, deepseek-v4-pro/settings-file authentication and the
approved Torch 2.8.0+cu128 environment have been rechecked.

| Approved sample | Current observation |
| --- | --- |
| G4 | **10 passed / zero skips**, 301.71 s. Four Hosts close 72 Agents/eight CUDA Processes; all 80 groups are absent, both private-pool Oracles pass and no socket remains. Real output confirms each Process holds 64 MiB total, with 256 MiB configured allocator ceilings total; two-card Processes use 32/128 MiB per card, contexts additional. Maximum sum of observed per-device allocator peaks is 68 MiB. A real capacity wait precedes later calibrated same-card overlap. |
| G5-resource | **PASS**, 653.22 s / **4.992342 USD**, GPU 3 only. The independent wrapper check, native resource Skill/query, actual CUDA, measured history, publication and report pass. All 11 Agents/one Process succeed and close; all 12 groups are absent, run/pool Oracles pass, no socket or paper staging remains. |
| G5-reading | **PASS**, 603.95 s / **3.946811 USD**, CPU only. Two successful native readers overlap for **1.718 s**, read the exact `0.83` and `5 turns` method details from the synthetic originals, and are collected into the accepted parent synthesis. The CPU result, analysis, final-report Skill, publication and terminal Oracle pass. Fourteen Agents/one Process close; all 15 actual groups are absent. One failed reader is retained beside its successful allowed retry; see below. |
| Public PDF | **FAIL**, `GroundingWebError: timed_out` after **15.003 s** on the same IJCAI URL. One fetch only; no new metadata/model call, retry or timeout increase. This does not invalidate the earlier successful OpenAlex/Unpaywall metadata observations, nor establish full-text availability. |

The initial supplemental G4 census included pytest's symlink aliases in its
pool count and failed its own count assertion. The corrected read-only census
excludes those aliases and records two actual pools. Both observations are
retained; the G4 pytest invocation itself passes without a rerun. The existing
research pool is only queried and reports no active commitments. No formal
Host/topic or notification is authorized by this recheck.

### Failure preservation, cost and cleanup

The reading sample's first method-A reader is rejected as
`invalid_structured_output` at `provider_output`: the diagnostic is
`/: additionalProperties constraint failed`. Its **0.078368 USD** charge and
FAILED Attempt are retained. The existing in-run retry succeeds; this is not a
second qualification invocation. Grounding succeeds without the earlier
uncommissioned-evaluator failure, and this sample reaches analysis/report
closure without a reported threshold overrun. One successful retry does not
guarantee every model response will follow the schema.

The resource sample exposes the actual Process's **379,584,512-byte** owned
resident peak, including context, through the native post-wait resource query.
The CUDA allocator, context and pool admission amounts are distinct; its pool
admission is 2,048 MiB, not a measurement of allocation. Its final report is
6,378 bytes; reading's is 6,264 bytes. All 25 paid native archives identify
`deepseek-v4-pro` in actual assistant messages, with 1,670 archived events and
no observed compaction boundary. Unique tool counts use session/tool-use IDs,
not repeated occurrences in resumed transcripts.

The same cohort now conservatively accounts for **51.373374 / 55 USD**, leaving
**3.626626 USD**. This includes **50.164374 USD** reported model usage,
**0.009 USD** previously metered literature, **1 USD** retained unknown model
usage and **0.20 USD** retained unknown OpenAlex usage. The two new samples add
**8.939153 USD** with no new unknown model charge. All 15 original/new databases
are folded once through existing accounting. This is CLI/Host accounting, not
an upstream invoice or verified credit balance. No further paid retry or GPU
sample is authorized by the remaining balance.

Independent final inspection finds all **107 recorded process groups absent**:
80 from G4 and 27 from G5. The six test Hosts have 97 Agent Attempts and ten
Processes, all CLOSED; the one failed reader remains a failed Attempt. All three
private pools pass their existing Oracles, all nine reservations are RELEASED,
and sockets/paper staging are absent. No live process command belongs to these
test roots, and NVML reports no GPU compute process. The existing research pool
still has zero active commitments and was not reconfigured or replaced. G4's
disposable targets are removed by its fixtures; paid state/source/data and
original failures are intentionally retained read-only. Final live-effect
cleanup is recorded **2,928.82 s (48.81 minutes)** after G4 launch, within the
approved 60-minute window.

### Current evidence scope and readiness

- G0 and the launch-input repairs have their focused reproductions and fixes;
  this invocation verifies the exact candidate, environment, selected provider,
  GPU amounts and unchanged existing-pool binding. There is no new runtime edit.
- G1–G3 reuse the preceding full offline, retained read-only and native CPU
  evidence for unchanged behavior, plus the 949 passing repair identities.
  No full expensive suite was rerun as a debugging loop. The historical
  real-research ACI-20 reader cohort is still missing; fixture/replay coverage
  does not establish unsupported cross-version recovery.
- G4 now has compliant real allocation, waiting, measured calibration,
  same-card overlap and cleanup evidence on the repaired workload. The old
  per-device/per-Process approval discrepancy remains part of the history.
- G5-local's old false negative is closed by the earlier read-only replay;
  G5-common's compatible DeepSeek result is reused. The two newly approved
  resource/reading samples pass. The original local/resource/reading FAIL
  invocations and their costs remain at their original locations.

**The repaired platform has evidence for its bounded native/GPU/reading
closure and can prepare a fresh real topic in CC. It is not an unqualified
“everything passed” conclusion.** The current IJCAI PDF route still fails:
OpenAlex metadata and Unpaywall DOI lookup success do not supply the missing
full text. A topic relying on that source needs reviewed existing local text
or another verified accessible source before relying on its methods/details.
No general-network, long-context-compaction, paid multi-expert debate or long
soak result is inferred from these one-expert canaries and synthetic papers.
Existing native multi-expert CPU/debate evidence retains its narrower scope.

MERC-causal remains admitted with its user-confirmed budgets, language and
experts; it currently allows `web_search`/`web_fetch`, without opting into
`read_papers`. Enabling that optional tool is a Profile choice, not something
this successful canary silently changes. A later CC launch must carry the
already reviewed formal Host's existing-pool socket, independent namespace,
authorized GPU inventory, model/settings and retrieval arguments. Qualification
GPU authorization and this 55 USD cohort are not a new research-run resource
or spending allocation. No formal Host/topic, existing-run mutation, external
notification, commit, push or publication occurred.

## Current repairs — qualification boundaries and launch inputs, 2026-09-16

This section records the earlier repair stage. The supplementary result above
resolves its then-pending approval and live samples; original failures remain.

The user-authorized repairs are implemented and checked with focused offline
tests, one native CC/local-provider CPU closure and read-only replay of the
saved G5-local run. **This is not a new full G0–G5 promotion.** The original
qualification invocations below, including every failure and charge, remain
unchanged. Evidence and the ordinary before/after diff are under
`.local/qualification/qualification-repair-20260916/`.

### Changes and evidence

- Candidate remains HEAD `6bd5bb910c8b9385f828f530cf634f3bbd19bbda` plus the
  imported, uncommitted tree. Four runtime files change: `claude_agent_host.py`,
  `cli.py`, `literature.py` and `profile_v4.py`. The existing runtime identity is
  now `sha256:38d805336b645c47ce3d7e7d620b1e50983e4661b8c7272de6882abb2cefb2b8`,
  ACI 20. The operator manager, qualification fixtures/tests and documentation
  also change. There is no new schema, migration, budget authority or recovery
  mechanism. `workflow_host.py` remains unchanged at 4,500 lines.
- **G4 amount repair:** the existing generated CUDA fixture now divides a
  per-Process total of 64 MiB held tensor and 256 MiB configured allocator
  ceilings over its visible devices. One GPU gets 64/256 MiB; each of two GPUs
  gets 32/128 MiB. CUDA contexts remain additional. Output and closure assertions
  distinguish per-device amounts, per-Process totals and observed per-device
  allocator peaks. Offline execution of the generated source with an injected
  Torch double checks both device counts; this is not real CUDA evidence. The
  original G4 scope deviation is retained and requires a fresh authorized sample.
- **G5-local recognizer repair:** `_has_seeded_repair` now accepts the retained
  validation-file display and `/workspace` prefix before the exact unittest.
  It still requires the real named failure, immediate printed nonzero status,
  matching native tool result and subsequent ordered edit/pass. Regression
  cases reject a different test, intervening command, zero status, echo-only
  text, unrelated tool result and wrong order. Actual local shell execution also
  proves red/edit/green despite echo returning zero.
  The existing `_assert_current_canary_closure` now passes on the saved
  `/var/tmp/hq-g5-local-ljlqtau_/trial`, including native tools, publication,
  report, charges and Oracle. Its 12 Attempts remain CLOSED, all 12 groups
  absent, and database/WAL bytes, original FAIL result and accounting file are
  unchanged. This resolves the old assertion by read-only replay, with no new
  provider call; it does not pretend to be a new-candidate paid invocation.
- **G5-resource fixture clarification:** its instruction supplies an absolute
  `/workspace/experiment.py --local-check` command on a separate line, followed
  by native Read in a separate call. Prior output cleanup also uses a separate
  call. The strict recognizer is unchanged; both retained cleanup-chain forms
  still fail negative tests. The corrected sample has not run on a paid model.
- **Reading commission and budgeting:** the local/reading fixture now explicitly
  requires Grounding's `acceptance: null`, since its declared metric already
  defines evaluation. This targets the observed uncommissioned-evaluator error;
  common-acceptance fixtures still commission their evaluator normally. Existing
  Host/CLI cost accounting and no-further-launch behavior are unchanged.
  Documentation now states response-time overshoot and the need to fund retries,
  analysis and reporting. No code change can establish that 4 USD suffices for
  this model; the original FAILED run and 4.052769 USD charge remain failed.
- **Previously reproduced launch-input defects:** environment authentication now
  forwards the selected `ANTHROPIC_BASE_URL` with its API key through both CLI
  and managed scope. Settings-file transport still excludes ambient credentials
  and gateway settings. The OpenAlex key reader enforces its documented regular,
  non-symlink mode-0600 requirement before reading. Profile defaults, experts,
  Grounding and stage overrides reuse the existing exact-model validator;
  documented `REPLACE_WITH_PINNED_MODEL` and Grounding placeholders are rejected,
  including at the direct CLI. Existing real credentials/Profile are unchanged.

### Verification, retained failures and cleanup

The selected current identities have **949 passing results / five opt-in skips /
zero unresolved failures**, 954 identities total. This is a scoped union, not a
new full-suite count. `selected-test-results.json` preserves each invocation:
the initial reproduction has 19 failures/51 passes; the first focused run has
834 passes/two failures/five skips. The latter failures were a test assertion
using `experts.defaults.model` instead of the actual
`experts.yaml.defaults.model`, and an old dummy OpenAlex key fixture left at
0644. Corrected tests retain the production checks; follow-ups have 217 passes/
three skips and 359 passes. Native/settings/bundle checks have 27 passes,
including actual CC 2.1.273 with a loopback scripted provider, CPU execution,
final report and clean Oracle after the source settings file is removed.
The later addition of the second documented placeholder only rejects that
sentinel; the native fixture's valid selector and execution path are unchanged.
The five skips are two real G4 cases and three paid G5 alternatives, not passes.

Compile/static checks and Git whitespace checks pass. The fresh wheel's 133
runtime/resource files match checkout and its isolated installation; installed
CLI/MCP imports resolve to that installation. The source package includes the
changed operator manager. Public bilingual controls and fully bound admission
pass. Production `profile-check` with the actual OpenAlex/Unpaywall files admits
MERC-causal read-only, with no network probe. An initial maintenance invocation
omitted required `--run-id`/resource-mode arguments and exited at argument
parsing; its stderr is retained separately from the corrected admitted result.

The native CC closure has 12 CLOSED Attempts/12 absent groups; the separate
bundle-restart CPU scenario adds four CLOSED Attempts/two absent process groups.
Together they have 16 CLOSED Attempts and all 14 actual groups absent.
All available group IDs across 11 test databases are absent; no socket or
paper-staging residue remains. Three deliberately invalid database fixtures
are listed separately. The process-command census finds no live test owner.
Twenty-five external preparation fixtures created by these tests are removed;
test logs/state and the original paid source/data remain for inspection.
No GPU workload, public-network retrieval, paid call, notification, formal
Host/topic, old-run mutation, existing-pool change, commit or push occurred.

### Readiness and next bounded verification

The topic can continue preparation in CC. **Formal Start has not gained full
qualification clearance.** The preceding full G1–G3 and G5-common evidence is
retained for unchanged behavior; the launch-input changes have focused evidence
above, not a fabricated full requalification. G5-local's false negative is
resolved by replay. Real G4 with corrected amounts, G5-resource adherence,
reading analysis/report completion and online full-text availability remain
unverified. The PDF timeout has no reproduced code cause; no speculative retry
or timeout increase was added.

No new charge was incurred. The same accounting file remains at
**42.434221 / 50 USD**, including 1.20 USD of retained unknown reservations;
**7.565779 USD** remains. No failed charge or unknown reserve was reset.

One concrete proposal, **pending user approval**, is:

| Sample | Binding and limits |
| --- | --- |
| G4, two existing live cases | Fresh private pools; GPU 0 `GPU-d469e765-ad52-350d-2cd4-9b12e5e5bc84` and GPU 3 `GPU-20ca7d4a-89d1-ec85-9bee-690afdd3283a`. Existing CUDA prefix `/home/gray/miniconda3/envs/erc-cu128`, Torch 2.8.0+cu128. Each Process totals 64 MiB held tensor / 256 MiB allocator ceilings, split over visible cards, context additional. Each case at most 540 s; at most 20 minutes including cleanup. No paid provider. |
| G5-resource, once | GPU 3 only, fresh private pool, same CUDA prefix; one Process with 64 MiB tensor / 256 MiB allocator ceiling, context additional. 6 USD dispatch threshold; each Agent at most 2 USD / 300 s; sample at most 1,200 s plus 30 s cleanup. |
| G5-reading, once | CPU only, at most three Agents concurrently, each at most 1 USD / 300 s; 6 USD sample dispatch threshold, at most 900 s plus 30 s cleanup. Two existing synthetic method documents; no extra OpenAlex query. |
| PDF route, once | Existing public IJCAI PDF for DOI `10.24963/ijcai.2022/628`, through the existing document fetcher; unchanged 15 s fetch / 4 MiB / 5 s extraction bounds, at most 60 s overall. No model, new metadata query, automatic retry or timeout escalation. |

G5 control is project Conda Python 3.12.14, CC 2.1.273, exact
`deepseek-v4-pro` and the existing mode-0600
`/home/gray/.claude/settings.json` binding to `api.deepseek.com`. The current
read-only preflight still validates that selection. The complete proposal is
at most 60 minutes including cleanup and reuses no existing research pool.
It needs a cumulative dispatch ceiling of **55 USD**: current conservative
42.434221 plus 12 USD proposed samples is 54.434221, leaving 0.565779 USD for
response-time overrun. This is not a guaranteed upstream invoice cap; actual
overshoot/unknown charges remain accounted and prevent further unaffordable
launches. No G5-local/common paid repeat is proposed. Approval for this proposal
does not authorize starting the real research topic.

## Current qualification — repaired dispatch candidate, 2026-09-16

**No full G0–G5 promotion.** The full offline suite and all approved paid
samples have finished. G1–G3 pass within the scopes below; G5-common passes. G5-local and
G5-resource retain failed qualification assertions, and G5-reading exhausts
its allocation before analysis/report closure. G4's eight functional checks
pass, but the resource amount used by its dual-GPU Processes differs from the
approval wording; this is a qualification preflight error, not an unqualified
G4 pass. No real research run was started, resumed, migrated or settled.

Evidence is in `.local/qualification/full-20260916-post-dispatch/`. This section
supersedes the earlier current-status claims below, without overwriting their
original invocations, failures or charges. The existing cumulative ledger is
`.local/qualification/full-20260916/cohort-accounting.json`; no new billing or
qualification authority was introduced. Runtime and test code were not changed
during this qualification.

### Candidate, environment and reused evidence

- HEAD is `6bd5bb910c8b9385f828f530cf634f3bbd19bbda` **plus the imported,
  uncommitted tree**, not that commit alone. The existing runtime identity is
  `sha256:68f89d42805cd3db85182baf1d5539b6b6dbbc515815eb7854d5a5c3ddd7b2e7`,
  ACI 20. Seven runtime files differ from the earlier `96d0f3f9...` candidate;
  the focused repair evidence below remains applicable, while old k3 results
  do not establish this model binding's compatibility.
- Control Python is project Conda 3.12.14, SQLite 3.53.4, with imports from this
  checkout. Native tests pin CC 2.1.273. The separate installed reader uses a
  complete Conda environment and a fresh wheel target outside checkout imports.
  Bubblewrap, user-systemd, Chrome and Poppler are present; `Linger=yes`.
  The user manager's existing failed legacy/desktop units were left unchanged.
- Actual paid responses identify **deepseek-v4-pro**, through
  `api.deepseek.com` and the existing mode-0600
  `/home/gray/.claude/settings.json`. This is real-call evidence, not login or
  `--help` evidence. No credential value was printed. GPU execution uses
  `/home/gray/miniconda3/envs/erc-cu128`, Python 3.12.13 / Torch 2.8.0+cu128.
- Public profiles remain exactly `multi-expert-en` and `multi-expert-zh`, each
  with six YAML files. The public-source audit finds no machine-private runtime
  path dependency; the selected legacy-named credential files and existing
  Prometheus pool are explicit operator bindings. No minimum public template
  was introduced.
- Fresh production `profile-check MERC-causal --profiles-root profiles/local`
  with the selected OpenAlex/Unpaywall inputs admits the existing real Profile.
  Its target remains clean at ERC `b4590893f756b9f09e2ff2bcbdcf1b784322801c`.
  Port 8766 can currently bind; this is not a reservation. The confirmed topic
  limits, omitted USD caps, language and experts remain unchanged. It still
  admits `web_search`/`web_fetch`, without opting into `read_papers`.

### Results from this invocation

| Stage | Actual result and limits |
| --- | --- |
| G0 | Current repair reproductions are covered by the retained focused tests and the new full regression selection. Current environment, imports, model/authentication, Profile and existing shared-pool status are checked. The G4 amount-review defect below prevents calling the resource preflight fully correct. |
| G1 | **5,665 passed / 25 skipped / zero failures**, 4,997.54 s, current collection 5,690. Compile/static checks and packaging pass. All 133 runtime/resources, including three migrations, runtime Skills/hooks and Web assets, match checkout in the wheel and fresh installation. The 557-file source package also includes operator Skills and public Profiles, excluding private Profiles/settings/state. Actual installed CLI/MCP, managed user-systemd Host using this candidate's manager script, CPU Process, publication, terminal Oracle and Host exit pass. |
| G2 | **174/174 selected portable checks pass in G1**, without a duplicate run. Five retained Hypoloom MERC roots pass both the current installed reader and the prior accepted reader with matching findings; database/WAL bytes, logical state and artifact inventories are unchanged. The original 0044 bundle retains its two known archive findings. There is still no historical real-research ACI-20 paper-reader cohort; fixtures do not replace that gap. |
| G3 | **7/7 selected scenarios pass in G1**. Seven roots have 183 CLOSED Attempts / 154 absent groups and seven clean Oracles, including current-ACI submit-commit crash/recovery, actual TUI leave/reentry, explicit End run and Host exit. The additional current-ACI three-expert case has 84 Agents plus six CPU Processes, six debate statements and one Judge, all CLOSED and absent groups. The separate debate-restart scenario uses ACI 14; do not conflate the two scopes. |
| G4 | **8 functional checks pass, zero skips, 302.37 s; approval-scope deviation remains.** Two fresh private pools and four Hosts exercise actual CUDA, same-card overlap, one pre-Process capacity wait, measured calibration, later dispatch and release. 72 Agents/eight Processes are CLOSED, all 80 groups absent, both pool Oracles pass and all eight reservations are RELEASED. Existing shared-pool configuration is untouched. See the amount discrepancy below. |
| G5-local | **FAIL**, 576.24 s, **3.485287 USD**. Run COMPLETED, 11 Agents/one CPU Process successful, Oracle clean. The seeded-repair recognizer misses the real red/edit/green sequence because the red command starts with a data display and `cd`; the exact trace is retained. No paid rerun. |
| G5-common | **PASS**, 767.48 s, **5.110453 USD**. Grounding authors/tests the common evaluator; the actual CPU result is independently parsed, accepted and followed by a final report. All 12 Attempts succeed and close; Oracle and cleanup pass. |
| G5-resource | **FAIL**, 719.85 s, **4.278484 USD**. Run COMPLETED and all 12 Attempts succeed/close, but the model combines `rm -f` with the wrapper check despite the sample's separate-call requirement. Actual GPU 3 CUDA, publication and report finish. Independent read-only use of the existing resource trace/history validators passes, as do run/pool Oracles; this does not change the original failed assertion. |
| G5-reading | **FAIL**, 565.68 s, **4.052769 USD** against a 4 USD sample threshold. Two successful readers overlap for 13.85 s, read exact method details and are collected into the accepted parent synthesis. The existing reading-evidence validator passes on those archives, and a CPU experiment succeeds. RESULT_ANALYSIS exceeds its remaining USD threshold; the Host then rejects FINAL_REPORT before launch and ends FAILED. The missing report is not a pass. |

The resource sample's actual CUDA output identifies only
`GPU-20ca7d4a-89d1-ec85-9bee-690afdd3283a`, one device, a 64 MiB tensor and
69,206,016 allocator-reserved bytes. Its owned peak is 379,584,512 bytes,
including context, with 13 complete attribution samples and no unknown samples.
The native resource queries expose that same completed measurement. G4 also
uses `GPU-d469e765-ad52-350d-2cd4-9b12e5e5bc84`; both UUIDs were approved.

The four paid samples retain 46 native transcripts / 3,052 archived events.
No native compaction boundary appears in them, or in the separately inspected
current three-expert and debate-restart archives. This does not qualify
long-context compression quality. Tool counts use existing session/tool-use
identities when deduplicating resumed transcripts; archival occurrences are not
additional experiment effects. No paid debate/Judge or long soak was added.

The 25 G1 skips remain skips: explicit GPU/paid alternatives, the optional
real tmpfs ENOSPC case and real Telegram notification. The approved G4/G5
selections have their own results above; unrelated skipped probes were not
silently promoted. `g1.xml` and `g2-g3-g1-coverage.json` map current test
identities to their actual results. New quarantine tests also pass with real
CC: live affected calls end, submitted CPU work is retained without fabricated
acceptance, no new readers dispatch, and explicit cancellation cleans up.
Five native reading/quarantine roots have 65 CLOSED Attempts / 65 absent groups
and clean Oracles.

### Findings and smallest next steps

1. **Resource preflight deviation — G4 cannot be marked compliant with the
   stated amount.** The approval wording specified a 64 MiB tensor and 256 MiB
   allocator limit per Process. `test_shared_gpu_native_g4.py:102–105` applies
   both values per visible GPU. Four observed dual-GPU Processes therefore each
   hold 128 MiB of tensors and configure allocator ceilings summing to 512 MiB,
   plus contexts. The total allocator peak was not separately recorded; the
   configured ceiling is not a measurement of actual use. This was the audit's
   incorrect preflight wording. All effects have ended and been reclaimed.
   Future review must state per-device and per-Process totals accurately, or
   adjust the existing test workload to the intended total before seeking the
   corresponding approval. No further GPU work or retroactive approval is
   assumed. See `g4-allocation-observations.json`.
2. **Reading budget failure and response-time overshoot.** The first Grounding
   Attempt is rejected as `invalid_candidate` because it invents uncommissioned
   common acceptance; its **0.432200 USD** charge is retained beside the allowed
   successful retry. Later RESULT_ANALYSIS receives a remaining cap of
   **0.534581 USD**, reports **0.587350 USD** and exits with
   `error_max_budget_usd`. The sample exceeds 4 USD by **0.052769 USD**.
   `workflow_host.py:2276` / `2457` reject further dispatch, including the report;
   the no-process report Attempt is not an unknown model bill.
   The existing qualifier explicitly handles post-response overshoot
   (`test_real_current_host_g5.py:198`, `847`). Future proposals must describe
   this threshold honestly and fund recovery/report work within the existing
   budget mechanism; do not invent a hard upstream currency cap or relabel the
   failed run. No additional reading sample is authorized here.
3. **Local qualifier false negative.** At
   `test_real_current_host_g5.py:682` / `1075`, `re.match` requires the failing
   unittest command to start with Python. The retained native call instead
   displays validation data, changes to `/workspace`, runs the exact test and
   immediately prints `exit=1`; a native Write and successful rerun follow.
   Extend that existing recognizer only for evidenced harmless prefixes while
   preserving test identity, failure summary, exit status, tool pairing and
   edit order. Add focused positive/negative cases and replay the saved closed
   canary read-only; a new paid local run is unnecessary for this defect.
4. **Resource sample's explicit call-shape requirement is unmet.**
   `test_real_shared_gpu_g5.py:137` requires setup/cleanup to be separate;
   lines 399/624 reject the actual `cd /tmp && rm -f ... && ... experiment.py
   --local-check` call. The wrapper really runs successfully, but that does not
   satisfy the stated requirement. Keep the original result. Any further
   qualification instruction change belongs to this bounded fixture; there is
   no evidence requiring a generic executor change. A new paid sample would
   require its own approval.
5. **Online full-text availability remains unproven in this invocation.**
   The one approved explicit academic query succeeds through the actual CLI
   wiring with general-web fallback disabled. Unpaywall successfully locates
   DOI `10.24963/ijcai.2022/628`; the single allowed PDF fetch then times out.
   The complete probe ends in 17.56 s, leaves credentials unchanged and creates
   no Host. No retry was made. Keep the successful metadata/DOI evidence separate
   from the failed PDF route; recheck that route or use reviewed existing local
   material before depending on online full text. Current DDG reachability was
   not independently retested. See `online-retrieval.json`.

Earlier documented alternative-path defects remain open: environment-only
gateway URL forwarding (`cli.py:490–497` and the manager's minimal environment),
OpenAlex reader permission enforcement (`literature.py:2896`), and unresolved
model placeholders (`profile_v4.py:72–73`). Their unchanged code and retained
reproductions remain evidence; the reviewed settings-file/0600-key/explicit-model
route avoids those conditions. They were not silently fixed or counted as
negative checks passing in this qualification.

### Costs, cleanup and readiness

The same cohort ceiling is now **50 USD**. Its conservative total is
**42.434221 USD**, leaving **7.565779 USD**: **41.225221 USD** model-reported
cost, **0.009 USD** previously metered literature, **1 USD** retained unknown
model reservation and **0.20 USD** unknown OpenAlex reservations (old/new
0.10 USD each). This round adds 16.926993 USD of reported model usage and the
new 0.10 USD online reserve. The ledger folds all 13 original/new database
sources through the existing accounting code; directory changes do not reset
spend. This is CLI/Host accounting, not an upstream invoice or verified credit
balance. There are no new unknown model charges and no further paid retries.

All four paid roots have **51 CLOSED Attempts / 50 absent process groups**;
one failed report is rejected before a process exists. There are no sockets,
paper-staging entries or live GPU reservations. Failed states and source/data
fixtures are deliberately retained for read-only replay. Four unused
preparation fixtures were removed. G4's 80 groups and installed delivery's two
groups are absent; its managed unit is collected with MainPID 0 and exit 0.
The complete G1 census reads 979 SQLite files; 939 contain the relevant Attempt
PGID columns. All 1,719 distinct available group IDs are absent. Fifteen
intentional non-database negative/display fixtures are listed separately in
the census, not treated as failed production runs. Synthetic unfinished unit
states are not relabeled completed. G3's seven roots independently have 183
CLOSED Attempts / 154 absent groups and clean Oracles. External G1 preparation
fixture directories are removed; internal fixture evidence, paid source/data,
state and bundles remain available for read-only review.

The supplied real topic can continue preparation in CC, but **this is not a
full qualification approval for formal Start**. Close or explicitly review the
G4 scope discrepancy, repair the local evidence recognizer, resolve the two
other failed G5 samples within a separately authorized bounded plan, and
establish the selected full-text source. At the later CC launch, carry the
reviewed existing-pool socket, independent namespace, authorized UUIDs, exact
model/settings and retrieval arguments into the actual Host and verify STATUS.
The topic's previously confirmed configuration does not need to be invented or
reconfirmed. No formal Host, topic, notification, commit or push was created by
this audit.

## Current repairs — dispatch faults and academic binding, 2026-09-16

The paused-topic findings below are repaired in the checkout, with focused
offline and real native CPU verification. **This is a repaired development
candidate, not another full G0–G5 promotion or a paid DeepSeek compatibility
result.** Evidence, original failures and the ordinary before/after patch are
in `.local/qualification/dispatch-repair-20260916/`. No existing run/bundle,
Profile, credentials, shared pool, GPU allocation or external service was
modified; no paid call, notification, commit or push was made.

Candidate: HEAD `6bd5bb910c8b9385f828f530cf634f3bbd19bbda` plus the imported,
uncommitted tree. Seven of 133 runtime/resources differ from the previously
qualified `96d0f3f9...` bundle: domain, workflow_host, host_tools_server,
full_cc_runtime, paper_reading, grounding_web and cli. The existing
`installed_run_version()` runtime identity is
`sha256:68f89d42805cd3db85182baf1d5539b6b6dbbc515815eb7854d5a5c3ddd7b2e7`;
ACI remains 20, with no new schema, migration or evidence authority. Control
Python remains project Conda 3.12.14 / SQLite 3.53.4. Actual native tests pin
CC 2.1.273 and use local scripted model/document transports, CPU only.

Changes and observed behavior:

- `domain.ExecutionContract` validates argv as an ordered, nonempty tuple of
  text while allowing repeated tokens. Data-split and environment uniqueness
  remain strict. Tests submit both array and shell-string commands with repeated
  `32` and `2`, preregister them, execute the exact argv in a real CPU Process,
  collect accepted output and pass the existing Oracle.
- `WorkflowHost._poll_agents` ends live model calls belonging to the
  quarantined run and records non-retryable `dispatch_paused` failures without
  a fabricated scientific outcome or owner workflow transition. Existing
  session archival and accounting retain observations, including unknown cost.
  Fake execution owners close consistently; queued paper readers cannot start.
  `host_tools_server.call_tool` keeps experiment waits bounded while awaiting
  Host shutdown, instead of immediately returning the same blocked state.
  Reader capacity blocks retain their immediate return for serial fallback.
  Submitted Processes still finish; native verification observes a successful
  Process remaining EVALUATING with no accepted outcome until the test explicitly
  cancels. Control, peer-run isolation and STOPPING/report cleanup remain covered.
- `cli` supplies the selected OpenAlex client to explicit academic search even
  with web fallback disabled. `GroundingWeb` records academic-provider and
  general-web-fallback configuration separately, reuses the existing client,
  and preserves labeled errors, deadlines and public HTTPS/DNS restrictions.
  General-web fallback remains opt-in; Unpaywall still means DOI-to-OA lookup.
  This fixes missing client wiring, not the observed external DDG route/DNS.
- CC init now displays one complete literal reviewed Host argv and reuses it,
  including model override, pool/UUID/runtime, web fallback and PDF/contact-file
  choices. Its existing actual-argv/STATUS checks cover all those bindings and
  stop only its newly created Host on a pre-run mismatch. The manager already
  transported argv literally; its regression now passes pool and retrieval
  options together, including paths containing spaces. There is no new launch
  file, automatic pool discovery/join, machine-private default or Profile edit.

| Verification | Actual result and retained limits |
| --- | --- |
| Red reproductions | Eight failures / one pass in `red.xml`, demonstrating the original defects before runtime edits. |
| Focused runtime/configuration/Skill/Profile checks | Original `regression.xml`: 268 passed / ten failed. All ten failures were Unix socket paths exceeding the platform limit under the long evidence-directory prefix; the unchanged endpoint file passes all 21 tests under a short temporary path (`socket-recheck.xml`). Another 62 accounting, retrieval, retry, bundle/restart and wait tests pass (`adjacent.xml`). |
| Native real CC/MCP/CPU | Original `native.xml`: two passed / one failed. The failed reader test counted calls before the dispatch tick, including readers correctly accepted before the fault; its assertion was corrected to count calls still live at the actual quarantine boundary. Only that case was rerun: pass in 21.85 seconds (`native-readers-recheck.xml`). Execution fault/retained CPU result, parent/reader fault cleanup, and unaffected three-reader/CPU/report completion all pass the existing Oracle after their explicit completion/cancel paths. |
| Current focused coverage | 343 distinct selected test identities have passing current evidence, zero skips or remaining failures. Original red invocations are not relabeled green. An intermediate spy used a fabricated PGID inconsistent with its Attempt; its Oracle failure is retained in `focused.xml`, and the corrected spy is covered by the larger regression. |
| Static and structure | Ruff F601/F602/F821/F822/F823 and the existing lifecycle/physical-size check pass; workflow_host remains at its existing 4,500-line limit. No validator, Oracle invariant or size allowance was relaxed. |

Cleanup census: 74 readable test databases contain 75 distinct recorded process
groups; none is live. All four native roots, including the failed original
reader assertion, have **45 CLOSED Attempts / 45 absent groups**, no tool socket
or paper-staging residue. Three successful native roots are COMPLETED/CANCELLED;
the original failed reader root remains ACTIVE with all seven Attempts CLOSED
and its processes absent, retaining the failure instead of being silently
settled. All local provider servers and fixture targets were closed/removed by
their test cleanup. Synthetic unfinished states in ordinary unit fixtures are
not presented as completed research runs.

A separate read-only observation at **10:33 +08:00** finds the old real topic
`MERC-causal-202609160508` has become **CANCELLED r40**: 27 Agents and four
Processes CLOSED, all 31 recorded groups absent, previous Host PID 3344509
absent. This change was outside this repair's actions. Its saved runtime stays
unchanged. The earlier paused-state diagnosis remains historical evidence;
changing checkout code does not upgrade or recover that run.

New-topic preparation may use this repaired candidate and the previously
reviewed Profile/bindings. Before formal use, CC must carry the actual reviewed
launch vector, verify the existing pool binding and selected current model/auth,
and distinguish current online retrieval health from these fixture results.
This repair did not execute GPU/G5/online OpenAlex validation; prior GPU/k3
results apply to their documented candidate and binding, not a new DeepSeek
compatibility claim. Full promotion belongs to a candidate freeze, not this
debugging loop. Existing null real-topic USD caps and reader opt-in choices
were not changed, and no old-run recovery is authorized or implemented.

## Latest observation — paused DeepSeek topic, 2026-09-16

Read-only inspection at **09:20 +08:00** identifies the newest topic as
`MERC-causal-202609160508`. The managed Host is alive (PID 3344509), but the run
is **ACTIVE r37 with a quarantined dispatch error**, not doing research. All
26 Agent Attempts and four Process Attempts are CLOSED; none of their 30
recorded process groups exists. There are no resource waits or open human
decisions. All four GPUs are idle. No cancel, restart, recovery, settlement,
cleanup of this run, provider call or code repair was performed by this review.
Evidence is in `.local/qualification/run-analysis-20260916-0914/`.

All 133 saved runtime/resources and `environment.yml` match the checkout bytes
qualified below. This launch nevertheless selects **CC 2.1.273 and
deepseek-v4-flash via api.deepseek.com**, confirmed by actual served-model
observations. The Host selector overrides the settings file's
`ANTHROPIC_MODEL=deepseek-v4-pro`; the previous k3/CC-2.1.272 paid evidence does
not establish compatibility of this different model binding. No secret value
was printed. The existing topic also exposes a runtime defect absent from the
earlier bounded samples; their historical passing results are not erased.

The main findings, in operational priority order:

1. **Run-wide dispatch is blocked by legal repeated command arguments.** At
   06:35:09, the engineering forward chain's third experiment was accepted as
   DRAFTED, then preregistration raised `ValueError: argv must not contain
   duplicates`. Its exact argv includes `--epochs 32 --batch-size 32`, and `2`
   occurs both in the seed list and `--audit-batch 2`. `domain.py:669` applies
   the set-like `_require_text_tuple` validator to argv; `domain.py:66` rejects
   these normal repeated values. The exact persisted argv reproduces that
   error offline. `workflow_host.py:3477` constructs the contract only during
   dispatch, and `workflow_host.py:692` then skips the quarantined run. This is
   a platform bug independent of model quality. The minimal correction is
   argv-specific validation that permits repeated tokens while retaining string,
   command/sandbox and other collection-uniqueness checks; cover actual submit
   through preregistration with this command shape. Do not deduplicate argv or
   globally relax the shared tuple validator. Editing the checkout cannot repair
   this running saved bundle; simply restarting the unchanged run will encounter
   the same pending command.
2. **Quarantine caused billable polling and stalled result publication.** Two
   previously running experiments finished at 06:40:17 and 06:43:46, but remain
   EVALUATING/ACCEPT_OUTCOME with their output workspaces retained. One engineering
   Agent received 189 immediate `blocked` wait replies and hit 240 model turns
   (`error_max_turns`, 16.087055 USD for that entire Attempt). Two other execution
   Agents timed out at 07:17:34 and 07:21:34, each after about 3,001 seconds.
   `host_tools_server.py:483` returns immediately on blocked, while
   `full_cc_runtime.py:589` directs further waiting and line 609 forbids completion
   in a dispatch-blocked state. This needs a focused review of the existing
   wait/Agent exit path, preserving pending experiments and terminal acceptance;
   it is not a reason to invent successful completion or a second scheduler.
3. **Actual startup omitted the selected shared-pool and retrieval bindings.**
   STATUS reports `gpu_pool=null`; argv contains all four GPU UUIDs, but no pool
   socket/namespace. This is the nonpooled path. Three experiments were bound to
   GPU 3 and one to GPU 2; recorded CUDA resource samples prove actual usage
   before the pause. The shared pool is not causing today's idle GPUs.
   Startup also omitted `--web-search-fallback openalex` and the Unpaywall option.
   The background OpenAlex pipeline supplied retained paper material, but the
   native academic-search client was absent: `cli.py:538` injects it only when
   the web fallback flag is set, while `grounding_web.py:297` uses that same
   client for explicit academic scope. The 15 actual searches failed: six
   academic-client-unavailable, five timeout, four unsafe-DNS refusals. No key
   rejection is established. Carry the already selected bindings into the actual
   CC launch and verify returned STATUS; clarify/test the academic-client wiring
   independently of the general-web fallback choice. Do not weaken DNS checks.
4. **A research implementation error was successfully repaired locally.** The
   engineering backward chain's first Process exited 70: all ten trials reported
   CUDA/CPU tensor-device mismatch. Its replacement Process succeeded. This
   experimental-code error is distinct from the platform quarantine above.

Observed scientific output is still negative against the frozen thresholds
MELD >66.74% and IEMOCAP >73.05%, each a five-seed mean:

| Candidate | MELD weighted F1 | IEMOCAP weighted F1 | Durable result status |
| --- | --- | --- | --- |
| ML prefix/speaker-state core | 64.0182% | 62.2875% | Accepted CLEAN_REFUTATION; hypothesis assessed REFUTED |
| ML supervision-geometry core | 64.2557% | 61.6554% | Process successful and parser output present; not yet accepted/published |
| Engineering backward prefix core, repaired | 63.8330% | 66.6460% | Process successful and parser output present; not yet accepted/published |

The latter two values are read from their existing parser outputs, not promoted
to accepted outcomes by this review. The parser reports its validity checks
true; this is not an independent scientific audit of every causal measurement.
The affective expert finished literature and a hypothesis but has no design or
Process Attempt. No cycle report, Observer, Debate, Judge or final report has
executed. The bundled Oracle currently reports two `owned_work_residue` issues
for the uncollected outputs and no live process residue; it does not pass.

There is real selective reading by the parent experts: Bash/sed/grep traces read
method sections, comparisons and limitations from retained `/papers/.../pages.txt`,
and Grounding checks source text in the target's existing paper collection.
Two direct document fetches succeed; one earlier fetch fails schema validation.
The frozen Profile only admits `web_search`/`web_fetch`, so **zero PAPER_READ
Agents and no parallel reader sample** are expected here. Twenty-six archived
native transcripts contain 18 distinct compaction events, unlike the earlier
short canaries; this observes compaction, not the quality of information retained.

Host/CLI known model accounting is **78.614773 USD plus two unknown-cost timed-out
calls**. Per-role known totals are ML 33.867578, engineering 38.863343, affective
5.883852 USD. The known total includes the failed max-turns call. USD invocation,
run and per-expert caps are absent, and token enforcement is observation-only;
unknown calls cannot be assigned zero or a finite USD ceiling. This is separate
from the 35 USD qualification cohort and is not an upstream invoice. No Agent
is currently active. The immediate platform priority is the argv defect and
quarantine polling behavior; another stronger model or idle GPU cannot advance
this quarantined run on its own.

## Current full qualification — 2026-09-16

The requested G0–G5 batch has completed its authorized selections. **The tested
settings-file / pinned-model route is ready for real-topic preparation in Claude
Code, within the restrictions below.** The additional reading run completed;
its postflight checker then failed on a query-plan field. A minimal test-only
correction passes read-only verification of that same completed sample, without
another model call. Both original failures and their bills remain. This is
bounded compatibility evidence, not a claim that every lane or scientific topic
passes. No formal research Start, existing-run recovery/mutation, external
notification, commit or push occurred.
Detailed evidence stays in ignored `.local/qualification/full-20260916/`.

### Candidate and environment

HEAD is `6bd5bb910c8b9385f828f530cf634f3bbd19bbda` **plus the imported and
uncommitted working tree**. HEAD alone does not identify this candidate. At
entry all 133 runtime/resources matched the final selective-reading wheel.
The full G1 invocation kept those bytes fixed, with ACI 20 and runtime identity
`sha256:30d6d241f7dfe52756ad6e58cde93ae8bd8b1442ba1f8d9e6fee2a986e506727`.
Fixing ACI-20 node projection produced
`sha256:7450c3f4711912eb9a38d36b24b0b660b79a21a299c663c3acfd9cb6d4ae1f84`,
used by G4 and the initial four paid samples. The subsequent batch-citation fix changes
only `grounding_background.py` relative to that candidate. Current identity is
`sha256:96d0f3f99006de9f516812a5f3a8d77f082b4c86388c3de836e26177bce9487f`.
These are existing `installed_run_version()` values, not a new identity scheme.
The additional reading run used the final runtime bytes; the subsequent
postflight correction changes tests only. Wheel/install/bundle resources are
compared byte-for-byte; environment identities also reflect environment files,
interpreter paths and explicitly bound runtime inputs, and need not be identical
between a checkout, installed package and configured Host.

Control Python is project Conda 3.12.14 / SQLite 3.53.4; imports resolve to this
checkout or the explicitly tested independent wheel installation. Native CC is
pinned to `/home/gray/.local/share/claude/versions/2.1.272`. Model `k3-256k` uses
the existing mode-0600 `/home/gray/.claude/settings.json` and `api.kimi.com`;
actual paid responses establish authentication beyond preflight. No key was
printed. CUDA uses `/home/gray/miniconda3/envs/erc-cu128`, Python 3.12.13,
Torch 2.8.0+cu128 / CUDA 12.8. Bubblewrap 0.4.0, Chrome 149 and Poppler are
available. user-systemd responds, with `Linger=yes`; its pre-existing failed
units remain untouched.

The shipped runtime, native assets and two public templates contain no old
package imports, `PROMETHEUS_*` variables or machine-private default paths
(`source-path-audit.json`). Public Profiles remain exactly `multi-expert-en`
and `multi-expert-zh`, six files each. Current wheel, independent installation
and fresh CLI/CPU bundle match all 133 resources; the 557-file source archive
includes hooks, operator Skills and current tests, and excludes private Profiles,
settings, state and local evidence. Installed CLI/MCP, CPU closure and Oracle
pass after both corrections (`installed-post-batch.json`).

### Executed evidence and limits

| Stage | Actual result | Evidence and boundary |
| --- | --- | --- |
| G0 | Scoped readiness established, with two existing entry-point restrictions below | Actual environment/imports, native tools, public layout, Profile admission, installed delivery and read-only pool status. No generic assertion that every authentication route works. |
| G1 | Original full invocation: **5,641 passed / five failed / 25 skipped**, 1,295.52 s | `g1.xml`. One actual ACI-20 projection omission and four incomplete old test doubles were corrected; affected selection: 136 passes. Final collection is 5,675 identities, with **5,650 passing evidence / 25 opt-in skips / no missing identities** after the batch and reading-checker corrections. This is evidence reuse plus focused verification, not an all-green original full invocation. |
| G2 | 174/174 selected portable checks pass; current Oracle reads four retained Hypoloom MERC roots | `g2-g3-g1-coverage.json`, `g2-retained.json`. Retained databases are byte-identical before/after, with unchanged logical rows, heads and artifact inventories. Original 0044 bundle still reports its two known archive errors; other original readers pass. There is no historical ACI-20 paper-reader research cohort. |
| G3 | 7/7 selected cases pass in G1; reader completion and End run also execute | Actual Host/broker/native CC/CPU, same-bundle recovery, subsequent actions, debate/Judge delivery, TUI leave/reentry and cancel cleanup. Nine independently read states: 198 CLOSED Attempts, 169 absent recorded groups. After the batch repair, all three native reader cases pass: single-source closure, batch closure and End run; 46 CLOSED Attempts/46 absent groups. Provider/documents in these native checks are local fixtures. |
| G4 | 8/8 checks pass, including both real CUDA cases, 299.02 s | Exact approved GPU 0 + 3, two fresh private pools, two concurrent Hosts. Actual same-card overlap, one capacity wait, measured recalibration, next dispatch and release are observed. Four roots: 72 CLOSED Agents/eight CLOSED Processes, 80 absent groups and two clean pool audits. This does not claim every multi-expert/GPU topology or long soak. |
| G5-local | **PASS**, 355.18 s, 2.351091 USD | 12 CLOSED Agents/one CLOSED CPU Process, clean Oracle and 13 absent groups. Actual native tools, seeded failing unittest then repair, measured 0.875 result and final-report Skill. One original Grounding failure remains: it proposed common acceptance in the explicit-metric case, then corrected it within the existing allowance. |
| G5-common | **PASS**, 356.85 s, 3.049820 USD | 11 Agents/one CPU Process, all CLOSED and successful; 12 absent groups. Grounding authored and tested the common evaluator; the expert read the actual source; shared-goal result/report and Oracle pass. |
| G5-resource | **PASS**, 342.23 s, 2.591910 USD | GPU 3 only, fresh private pool. Actual resource Skill, two resource queries, independent successful wrapper CPU check, accepted estimate, real CUDA, collection, post-wait own measurements and release pass unchanged strict checks. Admitted 2,048 MiB; observed own peak 379,584,512 bytes, including CUDA context. 11 Agents/one Process all successful and CLOSED; 12 absent groups; request RELEASED and pool/Run Oracles pass. The older failed resource sample and its cost remain in the cohort. |
| G5-reading, original | **FAIL / deliberately stopped after diagnosis**, 198.88 s | Four actual readers (two per parent attempt) read the two exact documents and returned method details; both parent summaries were rejected because batch-document URLs were omitted from citation eligibility. The original state is preserved. Known 0.911025 USD plus **1 USD unknown final-report reservation**; no CPU experiment reached. Later evidence does not turn this sample into a pass. |
| G5-reading, approved recheck | **Runtime COMPLETED; original postflight FAIL; corrected read-only verification PASS**, 350.89 s, 2.398124 USD | 13 Agents/one CPU Process all successful and CLOSED; 14 absent groups and clean Oracle. Two actual readers independently read exact original documents, return 0.83 / 5-turn method details, and are collected by the parent's accepted synthesis; then the actual repair, CPU result and final report close. Peak three Agent Attempts; reader intervals overlap 16.44 s. The checker incorrectly expected `summary` on QUERY_PLAN. Its red regression and original exception remain; MAIN-specific checking passes 29 offline cases and the unchanged completed state's archives, with no new paid call (`g5-reading-recheck-postflight.json`). |

G4 UUIDs were `GPU-d469e765-ad52-350d-2cd4-9b12e5e5bc84` and
`GPU-20ca7d4a-89d1-ec85-9bee-690afdd3283a`; resource G5 used only the latter.
All CUDA payloads retained the 64 MiB tensor / 256 MiB allocator ceiling,
with context memory counted separately. Existing shared pools were not replaced
or modified. The final citation change is inactive in the already passed G4
and three G5 scenarios; their original identities/results remain recorded and
are reused for those unchanged paths (`post-batch-evidence-scope.json`).
The paid recheck fetched two documents individually, so it does not claim a
second real-model batch-fetch trace. The exact batch failure is covered by the
retained real replies/candidates and the passing native batch scenario. Both
reading samples use synthetic documents and real k3 responses; they establish
tool/reading/collection compatibility, not literature quality or a throughput
benchmark.

Current G1 also passes the ACI-20 three-expert native interaction case: six
Observer calls, six debate statements, one Judge, six real CPU experiments and
the subsequent actions/report. All 90 Attempts are CLOSED and their 90 groups
absent. Native checks verify the complete Judge statement input and bounded
predecessor projection; existing cooldown/coalescing regressions cover interaction
frequency. The G3 selector's separate three-round restart case explicitly uses
ACI 14; its result is not substituted for this ACI-20 evidence. A read-only scan
of current G3, reader, combined-interaction and five paid samples finds 292 native
transcripts / 14,703 parsed events / **zero observed native compaction events**.
Automatic compaction remains unobserved in these bounded samples; historical
topic compactions are separate evidence (`interaction-context-observations.json`).

Current regressions distinguish `DISPATCH PAUSED (Host error)` from resource
waiting and completion, retain its original error and allow peer/control service
and cancellation. TUI leave/reentry preserves the Host and Process; End run
drains the owned work. Success still requires terminal effects and cleanup,
as observed separately in G3, native reading and the paid closure.

The 25 offline skips include additional/legacy paid or GPU probes, real Telegram
and the opt-in tmpfs fault lane. They are not passes. This batch did not execute
unapproved notifications, universal scientific evaluations, or 24/72-hour soaks.

### Findings and minimal corrections

1. **Batch reading citation omission — repaired; scoped verification complete.**
   `grounding_background.py:124` collected only top-level fetch URLs. A real
   `web_fetch(mode=document, sources=[...])` succeeded and real readers returned
   both exact method excerpts, but legitimate parent `web_sources` failed.
   This consumed repeated model calls and prevented the experiment. The existing
   collector now includes only `available` document members; native pairing,
   source uniqueness, failed-member exclusion and the Oracle remain intact.
   `batch-citations-red` preserves the failing reproduction; `batch-citations-green`
   passes 180 cases. Both retained rejected candidates now validate against their
   exact native replies in a read-only check; their stored failures are unchanged.
   Native batch closure passes, followed by both existing reader scenarios.
   The final runtime's paid reading closure is independently verified as above;
   its two individual fetches do not replace the original batch reproduction.
2. **ACI-20 node visibility — repaired.** `expert_loop.py:663` omitted 20 from
   the read-only projection's supported versions, hiding new research-node views.
   Add 20 to that existing set; keep future-version rejection. The existing
   current-version regression reproduced it and the 136-case affected selection
   passes. No scheduler, schema or persisted node-status mirror was added.
3. **Environment-auth provider URL — existing restriction, unresolved.**
   `cli.py:489` and native `manage_host.py:256` forward the API key but omit
   `ANTHROPIC_BASE_URL`. Offline dummy-credential/captured-serve probes reproduce
   both omissions. Minimal follow-up is forwarding the explicitly selected URL
   at both existing boundaries. The approved settings-file route works and does
   not use this affected path.
4. **Public model placeholder admission — existing restriction, unresolved.**
   A copied Profile with `REPLACE_WITH_PINNED_MODEL` is admitted. Reject that
   sentinel through the existing model validator/admission path
   (`claude_agent_host.py:3427`), without adding another model registry or gate.
   Current paid samples use the actual pinned selector, so this finding did not
   affect them.
5. **Reading postflight phase confusion — test-only defect, repaired.**
   `tests/runtime/test_real_current_host_g5.py:523` treated QUERY_PLAN as a
   Literature MAIN response and accessed a nonexistent synthesis `summary`.
   The paid runtime had already completed and passed its main closure checks.
   Select MAIN through the existing request subkind, keeping the exact-source,
   native-Read, collected-attempt and synthesis-detail checks. The negative
   regression still rejects a plan-only sample with no readers. Original
   `g5-reading-recheck-result.json` remains failed; the separate read-only
   postflight result verifies the unchanged state and runtime without relaunch.

These corrections change no existing live run or response. No full qualification
was used as a debugging loop and no failed paid sample was automatically rerun.
The initial empty pytest selection for reading preparation and a NULL-observation
progress-probe error also remain recorded; neither is platform passing evidence.

### Costs, cleanup and real-topic preparation

The same cohort ceiling is **35 USD**. Earlier model usage remains
12.996258 USD, including the original failed resource sample; metered literature
adds 0.009 USD. The three new passing model samples cost 7.992821 USD. The failed
reading sample has 0.911025 USD known and 1 USD reserved unknown. The explicitly
approved additional reading run adds 2.398124 USD, with no unknown new usage.
Together with the approved 0.10 USD OpenAlex reservation, final conservative
accounting is **25.407228 USD**, leaving **9.592772 USD**. This comprises
24.298228 known model USD, 0.009 metered literature and 1.10 reserved unknown.
It is existing Host/CLI accounting, not an upstream invoice. Directory/model
changes do not reset it. The single additional CPU reading run stayed within
4 USD/900 s plus 30 s cleanup, 1 USD/300 s per Agent and three Agent slots.
Its checker repair/replay added no model charge. No further paid retry is
authorized by that approval.

All completed test Hosts, native Agents, Processes and private pool servers have
stopped. Independent G1 liveness scanning covers 889 readable test databases and
1,678 distinct recorded group IDs (including synthetic fixture IDs), none live;
43 intentionally corrupt/old-schema fixtures are separately listed. G3, G4,
G5 and final native checks have their own cleanup censuses. The stopped paid
reading sample preserves **one OPEN durable FINAL_REPORT Attempt with no live
process group** and unknown cost reserved at 1 USD. It is failure evidence,
not an active task or a closed-run pass. Its paper staging and sockets are empty.
Twenty-eight G1-owned and eight paid/preparation fixture roots were removed;
retained test state, bundles, published source archives and logs remain evidence.
The final installed CPU probe also has four CLOSED Attempts/two absent groups.
Existing research roots/data/credentials,
shared services and local Profiles remain unchanged.

Current `profile-check MERC-causal` passes with the explicitly selected OpenAlex
and Unpaywall files. Same-day bounded online OpenAlex and Unpaywall/PDF evidence
is reused after confirming unchanged provider classes, paths and credential
file times (`online-evidence-scope.json`); unknown billing remains unknown.
General DuckDuckGo health and universal full-text access are not established.
A current real-Poppler offline two-page document extraction also passes.

The actual six-file MERC Profile still targets `/data_ssd/Gray/Project/ERC`,
the admitted MELD/IEMOCAP data and erc-cu128; its three experts inherit the Host
model. It has three Agent/Process slots, 3,000-second Agent and 1,800-minute
Process limits, and invocation/total/per-expert USD caps remain `null` as
previously confirmed. **The qualification's 35 USD cap is not a real-topic cap.**
It currently enables `web_search`/`web_fetch`, not `read_papers`. Using the new
reader mechanism requires reviewing that opt-in and capacity: waiting parent
experts still occupy Agent slots, so three parents can leave no reader slot.
The existing blocked/serial-fallback behavior is tested; this is a configuration
constraint, not evidence that more model concurrency has been provisioned.

Read-only status of the existing Prometheus-rooted shared pool succeeds with
UID 1001. The next actual CC initialization must carry the reviewed socket,
independent namespace and formal GPU UUID set in Host arguments. Qualification's
GPU 0 + 3 approval is scoped to these tests. Formal preparation/Start belongs
in the existing `/autoresearch init` review in CC; no additional Codex Start now
instruction is required. Platform validation for the authorized bounded reading
sample is complete with the explicit original failures and coverage limits above.
Using the already confirmed MERC settings does not require reapproval here;
enabling its new optional readers or changing its real-topic limits remains a
Profile choice to review in CC.

## Current development — selective paper reading, 2026-09-16

The user authorized implementing the consolidated reading design. This local
working candidate advances fresh runs to ACI 20 and adds exact document access,
explicit academic search, own-role reading history and optional Host-managed
paper readers. Existing runs, local Profiles, credentials and shared services
are unchanged. No paid provider, public-network retrieval, GPU or notification
is used in this development. HEAD remains `6bd5bb910c8b9385f828f530cf634f3bbd19bbda`
and does not identify the imported/uncommitted candidate by itself.

Evidence is in ignored `.local/qualification/paper-reading-20260916/`.
`environment.log` records Python 3.12.14, SQLite 3.53.4 and actual checkout import
paths; at entry all 110 runtime Python files matched the preceding repair wheel.
The older G1/G3 results below apply to their earlier bytes, not this new task.
Native checks use installed CC and a local scripted provider with synthetic
credentials; they are tool/topology evidence, not scientific model evaluation.

Implementation uses existing AuxiliaryTask/AgentAttempt records, per-invocation
reservations and expert/run accounting. `/papers` is read-only; source archives
reuse ordinary Agent publications. Published sources are mounted by verified
reference; children reading an unpublished parent retain their own exact copy.
Recovery reads only surviving admitted material and existing native observations.
There is no new paper registry, SQL table, reader daemon or scientific gate.

The implementation and bounded development verification are complete. This is
not a new full G0–G5 promotion. Compared with the preceding repair wheel, 21
runtime/resource files differ (19 Python files, including two additions, and two
existing native Skills); `candidate-changes.json` records the ordinary byte
comparison. Public templates and the user's MERC-causal Profile are unchanged.

Final evidence and its scope:

| Stage | Actual evidence | Result and boundary |
| --- | --- | --- |
| G1, affected regression | `focused-final-2.xml`, `readers-stop-final.xml`, `backup-context-final-2.xml` | 663 passed; the subsequent stop correction passes 11 reader cases, and the subsequent backup correction passes 59 backup/bundle/CLI cases. These overlap and are not added as distinct coverage. No new full G1 invocation is claimed. |
| G1, public admission/package | `profile-check.json`, `package-verification.json`, `installed-delivery.json` | Both isolated bilingual six-file Profiles admit `read_papers` through the actual offline CLI. All 133 runtime/resources (112 Python files) match the wheel, isolated installation and final CPU bundle. CLI Host, MCP stdio/inspection and Oracle pass from outside the checkout. |
| G2, persistence/source reuse | Reader regressions, `paper-backup.json`, backup/bundle regressions | Exact source reuse, child publication before parent, bounded I/O, same-candidate loss handling and surviving-source recovery pass. The completed native sample's backup and offline restore pass the full Oracle; all 12 original/text/index attachments retain exact bytes. No restored Host was started. |
| G3, normal closure | The `cpu-completion` case in `native-4.xml` | Installed CC observed three simultaneous readers and exact fixture document text; the parent collected them, then hypothesis/CPU experiment/report closed. Eighteen Agent Attempts and one Process succeeded; Oracle passes. The other case in this invocation failed, as recorded below. |
| G3, stop/cleanup | `native-5.xml` | One End-run case passed, one normal-completion case was deselected. Parent plus three readers cancel, the final report succeeds, all eight Agent Attempts close and Oracle passes. No Process was started in this cancellation case. |
| G4/G5 | Not executed | No real GPU or paid-provider evaluation for this feature. Local scripted model responses do not establish scientific comprehension, higher-model compatibility, throughput or monetary cost. |

The 663-case selection includes existing capability, memory/session, budget,
literature, interaction and Oracle checks. A selected method/source/page excerpt
reaches the Judge through its ordinary statement input; this tests delivery,
not whether a real model will select or interpret the right passage. Normal
native closure is reused after the separately tested stop-only and backup
corrections; it was not rerun with every final byte. Final installation/bundle
and CPU smoke checks use the completed runtime candidate. Full candidate
promotion and paid scientific comparison remain separate work.
Final documentation/public-Profile/lifecycle checks pass 23 cases
(`docs-delivery.xml`); critical Ruff checks, compilation and imports pass in the
recorded Conda environment. The Host remains within its existing 4,500-line
limit. The source archive includes current tests, guides, hooks and both public
Profiles, while excluding private Profiles, credentials and local test state.

There is no historical ACI-20 paper-reader research cohort. Same-candidate
fixtures and newly created native samples do not establish historical research
compatibility. Existing research runs were not started, resumed, migrated,
settled or modified.

Retained failures and their corrections:

- `source-red`: two reproduced missing-interface failures; `source-green`: 155
  passes covering document extraction, academic scope and existing retrieval.
- `integration-1`: 188 passed / 30 failed / two errors (reader module import
  integration). Follow-up first-error probes retain each import failure;
  `integration-host-2` passes both actual Host literature/CPU cases.
- `integration-2`: 225 passed / two failed (old closed command/capability
  expectations). The expectations now explicitly retain the reader's narrow
  tools; `components-3` passes 307 relevant cases.
- Reader development preserves its original fixture failures and the Host
  acceptance routing failure. `readers-7`: ten passes covering more-than-two
  readers, refill, distribution between experts, exact reuse, bounded I/O,
  duplicate commands, ownership, blocked waits, backoff/budget, parent failure,
  unknown reservations and same-candidate recovery. `lifecycle-5` retains 127
  passes / one two-expert fixture error; that fixture now uses a complete
  multi-component goal/protocol as required by existing admission.
- `native-1`: three native readers received exact source text, then failed
  archival because transport cleanup removed paper inputs too early. The
  implementation now preserves inputs until Host publication and then removes
  them. This original failure remains evidence, not a pass.
- `native-2`: three readers and the CPU experiment completed, but concurrent
  development changed pinned runtime bytes before the final report. The drift
  guard rejected continuation. This attempt remains failed and is not candidate
  qualification.
- `native-3`: two failures. Normal execution completed, but the Oracle's existing
  auxiliary command matrix lacked PAPER_READ. Adding its explicit packet-only
  and parent/source bindings makes the retained sample's read-only recheck pass;
  the original failed invocation remains. The stop fixture used the nonexistent
  `CANCEL_RUN` command and now uses existing `CANCEL` with a cause.
- `native-4`: one passed / one failed. Cancellation appended a new
  REQUEST_AUXILIARY record after STOPPING, violating the existing final-report
  stop boundary. The implementation now uses the accepted run cancellation for
  those readers. The Oracle rule is unchanged; `native-5` passes the affected
  End-run path.
- `paper-backup.log`: the native sample reproduced a pre-existing backup omission:
  database/artifacts were copied without the expert plugin/memory Git repositories
  and request views required by the Oracle. The existing backup/restore path now
  copies those existing run-owned directories and preserves executable modes,
  excluding native work/settings; it adds no manifest, database or cross-version
  recovery. Five initial regressions fail in `backup-context-red`. The first
  implementation retains seven failures: SQLite reader sidecars crossed the
  exact backup-file boundary, and a context parent directory was missing. The
  next invocation retains only that directory failure. Copied standalone
  databases are now read without WAL sidecars and
  parent directories are created through the existing file-copy path. The final
  59-case selection and full native backup/restore audit pass. The original and
  intermediate failure logs remain.
- Early Judge-excerpt and Profile probes used a wrong fixture field/order and
  a full path where CLI requires a Profile component plus `--profiles-root`.
  Corrected probes pass. Initial census/bundle probes also preserve their
  incorrect column/path assumptions; they did not mutate any run.

Cleanup is independently recorded in `native-cleanup-census.json`,
`cleanup-final.json` and installed smoke records. All 86 recorded groups across
the seven native roots are absent; paper staging is empty, admitted temporary
targets are removed, and no owned mounts or sockets remain. The final successful
native cases have 26 CLOSED Agent Attempts
and one CLOSED Process. The earliest failed native sample deliberately retains
one OPEN database Attempt with an absent process group, and aborted run states
remain unchanged as failure evidence. This is not an active Host/Agent and is
not counted as a terminal test pass. Installed CPU probes also close their own
Agents/Processes and remove their control sockets: three installed probes have
12 CLOSED Attempts and six absent groups, for 92 absent recorded groups across
native and installed probes. No existing research Host,
service, pool or data was altered.

The detailed [implementation and limits](architecture.md#selective-paper-reading--2026-09-16)
include opt-in configuration, source bounds and the separation between I/O and
model capacity. G4/G5 are not run for this change. Reading throughput, scientific
quality, visual PDF understanding and account-specific provider limits remain
unmeasured; a fresh bounded paid-model comparison requires confirmed inputs and
must preserve the existing qualification cohort's spend and unknown reservations.

## Current repairs — responsive retrieval and explicit pool binding, 2026-09-16

The user authorized repairing the stopped topic's pool-review and retrieval
findings, then selected Unpaywall for DOI-to-full-text fallback while retaining
OpenAlex academic search. The repair's offline, native CPU and bounded online
checks are complete, with the limitations below. Existing
research runs, Profiles, credentials and the shared pool remain unchanged;
no research Host, paid model or real GPU experiment is started by this repair.
Evidence is in ignored `.local/qualification/retrieval-repairs-20260916/`.
HEAD remains `6bd5bb910c8b9385f828f530cf634f3bbd19bbda`; the candidate includes
the imported, uncommitted working tree and these repairs. The six changed
runtime files are `workflow_host.py`, `literature.py`, `grounding_web.py`,
`host_tools_server.py`, `cli.py` and `full_cc_runtime.py`.

### Changes and observed boundaries

- **Pool review:** the native operator Skill now distinguishes GPU-sharing
  permission from actual pool participation. It carries a supplied known
  socket into the next review and does not treat a limited `/tmp` or
  `/run/user` search as proof that no pool exists. Existing Host argv remain
  the binding source; there is no new Profile field, automatic pool selection,
  replacement service or silent override of the latest approved UUID set.
- **Host responsiveness:** literature retrieval and query-coverage admission
  run on one disposable I/O worker, preserving serialization of mutable adapter
  caches/traces. Only the owning Host thread validates and commits results.
  Existing task/Attempt identities reject stale or stopped completions; no
  persistence schema or cross-version recovery mechanism was added. Backup
  remains busy while a read is in flight. Cancellation checks stop subsequent
  requests/retries; a transport already in progress retains its existing timeout.
  Host shutdown cancels owned Agents/Processes before draining that I/O worker.
  A completed model response discarded during stop still enters the existing
  usage fold through `discarded_response`.
- **Search delivery:** approved public IP attempts share one transport time
  window rather than receiving fresh per-address windows. HTTPS hostname and
  peer checks remain. Expired queued MCP calls are rejected before starting
  their read/effects. Already completed reads still reach the ordinary finish
  path: native transcript delivery determines whether the model observed them;
  an undelivered source remains `response_not_observed`. DNS resolution and PDF extraction still have their
  existing behavior; this is not proof of a universal end-to-end hard deadline.
- **Academic fallback:** explicit Host-only `--web-search-fallback openalex`
  reuses the reviewed OpenAlex transport. It defaults off, performs one bounded
  metadata query without retries/PDF downloads, and preserves the DuckDuckGo
  error with `search_scope=academic_literature`. It neither claims general web
  recovery nor sends the key to another provider. Runtime instructions describe
  these distinctions. No existing run's configuration was changed.
- **PDF fallback:** explicit `--literature-pdf-fallback unpaywall` and
  `--unpaywall-email-file` select one DOI-only fallback in the existing literature
  vector. The response DOI must match; up to four HTTPS OA PDF URLs enter the
  existing download/format/Poppler checks. No title search, fuzzy substitution,
  extra fallback chain or OpenAlex-key forwarding occurs. The existing bounded
  HTTP reader is shared with Semantic Scholar. Both selected fallbacks use the
  same 60-second cross-batch 429 cooldown; direct OA downloads remain available.

### Verification and retained failures

- The complete local Conda environment imports this checkout: Python 3.12.14,
  SQLite 3.53.4, actual native Claude Code 2.1.272. Pytest-xdist 3.8.0 was added
  only to this isolated environment for the existing offline selection.
- Preserve initial red results: two Host-thread blocking failures, two
  cross-batch 429 failures and one expired queued-tool failure. The first Host
  regression run also found two next-action/restart timing failures; moving
  completion processing after dispatch restores the existing next-tick boundary,
  and all four selected restart checks pass. The combined focused selection
  records **382 passes** before the later shutdown/accounting boundary tests.
- The additional shutdown/accounting probes both failed in the initial repair:
  waiting for the I/O worker delays cancelling an owned Agent, and discarding a
  completed model response during cancellation loses its response-based usage
  record. These original failures are retained in `close-accounting-red.log`;
  both corrections pass in the final focused selection.
- Full G1, with four pytest workers and live resource/model opt-ins disabled,
  records **5,599 passed / 5 failed / 25 skipped** in 1,271.02 seconds. Preserve
  `g1.xml`/`g1.log`; it was not a green invocation. Failures were: a Host-file
  physical-line limit (4,531 versus 4,500); two request-capture fixtures assuming
  synchronous literature completion; missing late-web observation in the native
  timeout scenario; and an overlong Unix socket path in a native Bash-policy
  fixture beneath the xdist temporary directory. No permission-policy failure
  was established by that last pre-launch socket error.
- The worker read/trace code now lives in the existing `literature` module;
  Host state transitions remain Host-owned and the existing 4,500-line limit is
  unchanged. Fixtures await actual async completion. A subsequent 74-case run
  had **72 passes / two failures** because its injected clock covered the Host
  but not the relocated I/O clock. Both injected clocks now agree; retain this
  intermediate result in `closure-recheck.xml`. No production clock was changed
  to accommodate the test. The late-read fix preserves undelivered evidence;
  the native permission test runs with an explicitly short disposable root.
- Final affected selection: **432/432 passed** (`focused-final.xml`), including
  16 Unpaywall cases, unchanged line/security checks, pool argv round-trip,
  cancellation/accounting, timing, Profile/Skill/docs and a real native Grounding
  selection. Final native lane: **5/5 passed** (`native-final.xml`), covering
  web sources, real client timeout/late source rejection, literature-to-hypothesis
  CPU closure, TUI leave/re-entry/End run and actual Bash deny/allow behavior.
- Current collection is **347 files / 5,646 distinct cases**. Matching the full
  G1 with affected final regressions gives **5,621 identities with passing
  evidence, 25 explicit skips, no missing identities** (`g1-coverage.json`).
  This reuses unaffected coverage and retains every original failure; it is
  not a claim that all final runtime bytes received another full G1 invocation.
  G2 fixture files and all seven existing G3 selector scenarios passed within
  G1. No real GPU or paid-model lane was rerun; older G4/G5 results do not
  promote these changed runtime files. Compile/import, Ruff and dependency
  consistency checks pass in the recorded complete environment.
- One bounded online search at **00:27:29 +08** returned OpenAlex academic
  metadata in **14.611 seconds**, retaining primary `timed_out`. Its result is
  CauAIN (2022), DOI `10.24963/ijcai.2022/628`; full text was not fetched.
  The key was read only from the already selected private file and was not
  logged. Provider billing was **not returned and remains unknown**, not zero;
  this does not reset earlier qualification charges or permit new paid tests.
- Four retained Hypoloom roots pass current-code read-only Oracle audits:
  `202609150044` CANCELLED r108, `202609150849` CANCELLED r63,
  `202609151555` FAILED r9, `202609151803` CANCELLED r60. Their database
  timestamps/sizes, run heads and Attempt counts are unchanged across the reads.
  All **231 Agent and 35 Process Attempts are CLOSED**. Earlier bundled-reader
  results and original failures remain at their original locations. These are
  retained current-format samples, not a historical release compatibility claim.
- The selected contact source is
  `/home/gray/.config/prometheus/unpaywall.mail`, read only. At **01:00:44 +08**,
  one real Unpaywall lookup of that previously found CauAIN DOI returned
  `https://www.ijcai.org/proceedings/2022/0628.pdf`; one bounded fetch and Poppler
  extraction succeeded in **4.649 seconds total**, returning 2,235 characters
  of abstract/conclusion text. This is one working DOI/PDF sample, not universal
  coverage. No model, GPU or research Host was involved. The contact value was
  not logged and its file content/permissions were not changed. No provider bill
  was returned or inferred (`online-unpaywall.json`).
- Read-only `profile-check MERC-causal --profiles-root .../profiles/local` with
  the selected OpenAlex and Unpaywall files **admits with no blockers**. It reports
  configuration-only preflight, not network success. Preserve the initial
  manual CLI error that passed the entire Profile path where a safe component
  was required (`profile-check.err`); the corrected invocation is a separate
  record. No Profile or prospective run was written.
- The wheel, fresh target installation and installed-wheel CPU bundle all match
  **131 runtime files**. Installed CLI/MCP load outside the checkout; the real
  CPU smoke reaches COMPLETED r9, with **two Agent and two Process Attempts
  CLOSED/SUCCEEDED**, one canonical outcome, clean Oracle, Host exit 0 and no
  socket or recorded live group. Its temporary root is removed. The source
  archive contains native assets, bilingual public Profiles and tests, including
  Unpaywall; private state, local Profiles and environment roots are excluded.
- **Design-only follow-up:** [selective paper reading](architecture.md#selective-paper-reading--2026-09-16)
  now distinguishes bounded document I/O, existing expert concurrency and
  optional Host-managed model readers with owner synthesis. Runtime code is
  unchanged by this design. The focused check records **nine passes** in
  **0.19 seconds**: four existing documentation cases and five existing PDF
  concurrency, reference/cache, fallback-bound, factory and deadline cases
  (`parallel-reading-design-check.log`/`.xml` in the same evidence directory).
  This is evidence for current documentation and adapter behavior, not an
  implementation or qualification of document mode or parallel model reading.
  No model, GPU, network retrieval or research run was started for this check.
  A subsequent design revision removes the fixed per-expert two-reader ceiling
  in favor of dynamic admission within existing run capacity, distribution
  across experts and completion-driven refill. Its four documentation and two
  existing capacity/cost-reservation regressions record **six passes in 0.14
  seconds** (`dynamic-reading-design-check.log`/`.xml`). Those accounting checks
  cover existing independent Agent calls; parent/child budget subdivision,
  dynamic reader dispatch and throughput remain unimplemented and untested.
  The consolidated design now uses ordinary per-invocation ceilings and the
  shared expert/run accounting fold; it does not propose parent/child allowance
  subdivision. It joins question-driven follow-up, explicit academic search,
  original method-page access, source/round-memory reuse and optional managed
  readers into four delivery stages. The final documentation-only selection
  records **four passes in 0.03 seconds** (`connected-reading-design-check.log`/
  `.xml`), with local-link/whitespace checks also passing. The earlier nine/six
  case records keep their original scope and are not counted as validation of
  these new interfaces, model behavior or performance. No runtime code, actual
  Profile, run, model, network retrieval or shared resource was changed or
  exercised by the consolidated design work.

### Cleanup and next-run scope

Five final native/CPU state roots were independently reread: **59 Agent + nine
Process Attempts CLOSED**, clean Oracles, **66 distinct recorded groups absent**
and control sockets gone (`native-durable-counts.json`). This includes the
intentionally CANCELLED TUI run; it is not relabeled COMPLETED. The separate
wheel smoke is described above. A separate process scan found no worker bound
to the recorded test roots. Twenty recorded disposable source mounts and three
remaining test roots were removed; the earlier G1 root had already been collected
by pytest. No independent all-Attempt count is claimed for those already removed
G1 fixtures (`cleanup.json`). Research runs and shared services were not altered.

**The repaired platform supports preparing a fresh CC run with the reviewed
academic-search/full-text routes.** General DuckDuckGo search remains unavailable;
neither OpenAlex nor Unpaywall establishes general-web coverage. The next actual
CC initialization must carry the chosen existing pool socket, independent
namespace and latest reviewed GPU UUIDs in its Host argv, along with the selected
literature options, current model authentication and budgets. There is no new
Codex-only Start now requirement. GPU/paid compatibility for this changed
candidate and the next model remains untested here; no scientific success or
unrestricted readiness is claimed.

## Latest observation — stopped Kimi topic, 2026-09-15

**`state/MERC-causal-202609151803` is CANCELLED r60, its managed Host has
exited, and no owned live work remains. It produced four complete scientific
negative results, three failed executions and one operator-cancelled execution;
it did not achieve the MERC goal.** This began as a read-only review after the
user's manual stop. No run was resumed, changed or deleted; no runtime code, Profile,
key or shared service was changed and no new model/GPU/web call was made in
that initial review. The September 15–16 follow-up below adds bounded,
credential-free connectivity probes and an isolated offline reproduction.

### Durable state, actual candidate and cleanup

All times below are September 15, UTC+08:00. Actual creation was **18:28:27**,
not the 18:03 suffix in the root name. The first TUI cancellation at **23:16:24**
was rejected because the form submitted revision 56 after the Run had advanced
to 57. The next cancellation was accepted at **23:18:10** as r58. Outstanding
Process/Agent work closed by **23:18:13**; the final report took two Attempts,
and `FINISH_STOP` reached CANCELLED r60 at **23:27:19**. Thus the run lasted
about 4 h 59 min, with about 9 min 8 s between accepted cancellation and terminal
closure. Most of that closing interval was report generation, not training.

- All **57 Agent Attempts are CLOSED**: 38 SUCCEEDED, 15 FAILED, 3 TIMED_OUT,
  1 CANCELLED. All **8 Process Attempts are CLOSED**: 4 SUCCEEDED, 3 FAILED,
  1 CANCELLED. All eight Processes actually launched; all eight Experiment
  workflows are TERMINAL. No `GoalVerdictRecorded` fact exists.
- The actual served model was **`kimi-for-coding`** in all 57 diagnostic
  records; native transcripts report Claude Code **2.1.272**. The Host imported
  its saved bundle with local Conda Python; experiments used
  `/home/gray/miniconda3/envs/erc-cu128` and target commit
  `b4590893f756b9f09e2ff2bcbdcf1b784322801c`. All **131 bundled runtime files**
  byte-matched the checkout at that read-only review, including the previous
  repairs; the six September 16 runtime changes above now differ. Git HEAD
  remains `6bd5bb910c8b9385f828f530cf634f3bbd19bbda`; the uncommitted worktree
  still defines the candidate. This is distinct from the earlier 15:55 run that
  failed before experiments following the user's reported API exhaustion.
- The run's own bundled Oracle, imported outside the checkout, **passes with
  no issues**: eight valid publications, 57 archived transcripts, zero open
  Attempts/decisions, pending effects, nonterminal experiments, duplicate effects
  or owned process residue. An initial manual probe supplied the state root
  instead of its `artifacts/` child and failed reader path checks. Its result is
  retained as `oracle-wrong-artifact-root-probe.json`; the correctly addressed
  audit is `oracle-bundle.json`. The probe is not a runtime failure or a pass.
- At **23:38:48**, all **65 distinct recorded process groups were absent**, a
  separate process scan found no process bound to the run root, `control.sock`
  was absent, disposable `artifacts/work/` was empty and port 8766 had no
  listener. The exact derived managed service and scope were inactive/not
  loaded, with no queued job; service MainPID was 0. Two remaining GPU processes
  belonged to UID 1023 and were not operated on. Retained bundles, Agent
  repositories, reports, logs and checkpoints are research history, not live
  residue.

### Scientific work and resource behavior

These are the four canonical CLEAN_REFUTATION measurements, shown as weighted
F1 percentages. Each complete publication retains ten checkpoint files and
five-seed results for both datasets. The shared parser reported all eight
validity checks true and maximum future-perturbation difference zero. This
review did not independently rerun those models/checkpoint audits or test the
new generated parser with counterexamples; structural Oracle success does not
certify their scientific implementation.

| Expert / experiment | Device and Process duration | MELD | IEMOCAP |
|---|---|---:|---:|
| ML, experiment 1 | CPU, 38 min 49 s | 63.2148% | 65.8232% |
| Affective, experiment 1 | GPU 1, 27 min 59 s | 63.8767% | 63.1090% |
| Affective, experiment 2 | GPU 1, 52 min 53 s | 63.6153% | 63.1795% |
| Engineering, experiment 4 | CPU, 87 min 24 s | 64.1180% | 53.4999% |

No row meets the required pair of thresholds, MELD **>66.74%** and
IEMOCAP **>73.05%**. The best values from different rows cannot be combined
into a successful candidate. Affective completed one cycle and its two-result
hypothesis was assessed REFUTED. ML's second experiment ran on CPU for
**2 h 32 min 18 s** before cancellation and retained three partial checkpoint
files; it is not a fifth complete measurement. Engineering repaired three
roughly 4–5-second startup failures before experiment 4 completed; its next
design finished, but cancellation interrupted the next execution Agent before
another Experiment was registered. This is a partial first-cycle exploration,
not evidence that stronger models or longer runs cannot improve the methods.

Actual Host argv, recovered from its systemd journal, names all four GPU UUIDs
and has **no `--gpu-pool-socket`, `--gpu-pool-namespace` or pool UID argument**.
The initial RUN record has no pool binding and uses research contract v10.
The earlier Codex-session shared-pool/two-GPU setup was not used. **The follow-up
recovered the actual CC approval: all four GPUs and nonpooled were explicitly
selected in that later session.** The launch matches that approval; missing
flags are not evidence that the helper dropped a reviewed binding. The two
actual GPU Processes used
only **`GPU-e96b09e9-0bcc-d031-acce-bb705dabfa31`** (GPU 1); the other six
Processes received CPU grants.

The reasons for CPU selection differ. ML explicitly submitted **`gpus: 0`**
twice. Engineering submitted AUTO without an explicit count. Its frozen
execution capabilities marked GPU 1 busy; GPU 0 had **11,751 MiB** free,
below the configured **8,192 + 4,096 = 12,288 MiB** admission requirement,
and the other GPUs had less. Existing nonpooled AUTO selection therefore fell
back to CPU. The later Agent snapshot at 23:16 saw GPU 0 with 24,120 MiB free;
that does not retroactively change an already committed CPU grant. These
observations do not demonstrate a shared-pool queue failing to use an idle
authorized card: this Host was not participating in the pool.

### Concrete problems and bounded follow-up

1. **Cross-session pool intent and actual CC approval must be distinguished.**
   Comparing only the earlier pool discussion with `host-journal-commands.json`
   and RUN r1 omitted the later explicit nonpooled choice. The follow-up below
   corrects that inference using `operator-binding-review.json`. For a future
   pooled run, carry the existing socket, independent namespace and selected
   UUIDs into the actual CC review and reuse the managed argv/stored-binding
   display. No launcher forwarding failure was found. Separately review whether
   these training submissions
   should request GPU capacity instead of explicitly selecting CPU or accepting
   AUTO fallback. Do not silently replace the pool, mutate an existing grant,
   reduce the confirmed memory reserve or force every scientific workload onto
   GPU. Relevant implementation: `workflow_host.py:_submission_execution_plan`,
   `compute_resources.py:LocalComputeResources.select`, and the operator Skill.
2. **Long Process waiting still outlives the execution Agent.** Three Agents
   expired at approximately **3,002 seconds**, while the Process deadline was
   108,000 seconds. Affective's second Process continued to successful collection
   after its Agent timed out; Engineering's long Process also completed before
   a subsequent Agent collected/reported it. ML's second Process continued until
   manual cancellation. Thus Agent timeout is not the experiment's terminal
   result. The earlier elapsed repair is observed in five real terminal MCP
   replies: each elapsed value equals stored Process end minus start, including
   all three short failures. Repeated waiting still consumes model sessions;
   any Host-managed model-free waiting redesign remains a separate lifecycle
   change, rather than silently changing deadlines or retries.
3. **Debate paused experts for over an hour without starting a Debate Agent.**
   At **22:10:49**, the accepted OBSERVER proposal selected ML and Affective
   to discuss speaker-state memory; r52/r53 paused those exact participants.
   `DebateHost._drained` / `drive` waits for their existing Attempts and
   Experiment workflows to finish. ML's long CPU experiment remained active
   until 23:18, so the proposal produced **zero Debate statements, zero Judge
   Attempts and zero Leader Attempts**. Engineering continued working during
   that interval. This is an observed drain wait, not proof of a deadlock or
   completed peer review. The smallest next change to assess is deferring the
   pause/proposal until participants reach a suitable boundary, while retaining
   single ownership and safe draining; do not arbitrarily kill training to
   accelerate a debate. Native archives contain 23 distinct compaction UUIDs;
   their presence does not establish context quality or completed interaction.
4. **Generated experiments and responses still need correction.** Engineering
   experiment 1 searches for an original-path suffix in the aliased
   `HYPOLOOM_READ_ROOTS`; experiment 2 treats the code-only candidate checkout
   as the data checkout; experiment 3 looks for an untracked cache beneath that
   code checkout. Published stderr gives the exact missing paths. Experiment 4
   resolves the authorized data alias and runs. Reuse the documented
   `HYPOLOOM_READ_ROOT_MAP` for data and `HYPOLOOM_TARGET_ROOT` for candidate
   imports, with a small real sandbox training/data probe. Do not widen mounts.
   There were also **10 rejected native submit calls alongside eight successful
   registrations**, four malformed structured hypothesis outputs, numeric
   measurement-shape errors and three rejected adjudications. The enhanced
   exact-reference feedback was actually delivered on the third adjudication
   failure; its fourth Attempt succeeded. These are narrower generated-output
   problems, not justification for a new generic validation framework.
5. **The accepted final report is not factually reliable in every sentence.**
   Its table lists the correct four complete negative results, but the prose
   says five; it incorrectly says ML completed a successful repair after its
   failure, although its cancelled second experiment was its last one. It also
   says “3 process / 8 expert” Attempts where SQLite records 8 / 57, and treats
   an empty `final_failures` collection as absence of all terminal failures.
   The first report was rejected with all eight missing outcome IDs named;
   the second included those references and was accepted. Existing
   `final_report.py:run_outcome_context` and `validate_final_report` validate
   context/required references, not every free-text statement. Keep this report
   unchanged as evidence. A minimal future improvement should reuse the existing
   computed counts in its factual table and exercise these exact contradictions
   in focused report tests/instructions, not add another source of run truth.
6. **External retrieval and cancellation have visible limitations.** The 10
   distinct native `web_search` calls all returned transport errors/timeouts;
   two of 17 `web_fetch` calls failed (429 and timeout). Literature attempts
   also encountered unavailable PDFs/rate-limited fallbacks and missing
   requested strata. The follow-up below separates direct network failure,
   PDF fallback limits and a reproduced Host-thread stall; these are not
   evidence of model-key exhaustion. The initial stale TUI CANCEL was safely
   rejected by the existing revision check (`end_run_tui.py`,
   `workflow_host.py:_cancel_run`); a subsequent reviewed request succeeded.
   Preserve this concurrency behavior and make any future usability correction
   reuse the existing conflict/refresh path, rather than remove revision checks.

### Follow-up — pool approval and retrieval, September 15–16

**The pool discrepancy is an operator-context/review issue, not a reproduced
argument-forwarding bug. Retrieval combines a reproducible platform stall with
independent external transport and PDF-availability failures. No repair was
implemented in this follow-up.** Existing runs, bundles, Profile, credentials
and shared services remain unchanged.

The actual operator transcript is
`/home/gray/.claude/projects/-data-Gray-Project-Hypoloom/7b59c74c-016b-4548-9be3-af8869a93774.jsonl`.
On September 15, CC recorded these answers (UTC+08): **18:20:56**, all four
GPUs and sharing of occupied cards; **18:22:56**, “Nonpooled, as before
(Recommended)”; **18:27:18**, Start now after a final review explicitly saying
no pool socket/namespace. Its discovery command searched only `/run/user/UID`
and `/tmp`, at depth two. That search did not cover the previously confirmed
`/data/Gray/Project/Prometheus/.local/gpu-pools/shared-3090x4/control.sock`.
The `manage_host.py start` input already lacked pool flags before the helper
ran. Sharing an occupied device and participating in the coordinating pool
were separate questions; the first answer does not imply the second.
The pool socket's Prometheus directory is the previously authorized external
service location, not evidence of a hidden legacy import or a need to replace
that service. A pool binding also does not turn explicit `gpus: 0` submissions
or existing CPU grants into GPU work.

`CLAUDE.md` separates Codex maintenance memory from CC runtime context. The
six-file Profile has no pool socket/namespace fields; these are explicit Host
`serve` options. The current Skill requires preservation of a pool binding
confirmed **in that session**. `_launch_service` forwards `*host_argv` unchanged,
and the existing managed-launcher literal-argument regression covers that
path. Thus strengthening the helper again would not address this observed
choice. The smallest operator improvement is to supply the known binding to
CC's next review and avoid recommending nonpooled based on a search that never
covered the known endpoint. Do not auto-bind a pool or silently restore an
older GPU subset over a later explicit selection.

| Retrieval path | Observed evidence | Interpretation |
|---|---|---|
| Native `web_search` → DuckDuckGo HTML | All 10 distinct calls failed. Each retained Host exception chain ends in `socket.connect` / `OSError: [Errno 101] Network is unreachable`; eight errors reached the Agent, two Agent replies timed out first. | Failure before an HTTP search response. This path is credential-free and does not use the OpenAlex or model key. |
| Native `web_fetch` → supplied URL | 15/17 succeeded, including arxiv.org, ACL Anthology and GitHub. Both failures used export.arxiv.org: HTTP 429 and a read timeout. | Selected sites work; this is not total Internet unavailability. A different arxiv.org fetch later succeeded. |
| OpenAlex metadata → literature packets | Four packets contain 32/32/29/32 entries: 125 entries, 102 distinct bibliographic identities. No OpenAlex authentication failure is observed. | The metadata route using the configured OpenAlex key worked during this run. Missing requested time strata in earlier narrow queries are coverage errors, not key failures. No new live-key test was made here. |
| Full PDFs and DOI fallback | 24/125 packet entries have retrieved text: 15 direct retrievals and nine cache hits; 101 are PDF_UNAVAILABLE. The unauthenticated Semantic Scholar fallback received eight actual HTTP 429 responses, two per batch; 88 later fallback entries were locally skipped after rate limiting. | Do not count skipped entries as 88 more HTTP requests, or metadata as full-text retrieval. Direct PDFs also encountered absent OA URLs, 403/406 responses and validation/extraction failures. The OpenAlex key is not used by the Semantic Scholar locator. |

The platform defect is in `workflow_host.py:_expert_agent_step`: synchronous
`self.literature_io.retrieve(query)` runs on the same thread that services
Agent MCP endpoints, polls Processes, reconciles the pool and handles control
requests. Query coverage admission also performs synchronous literature I/O
in `_accept_expert_packet`. Existing PDF workers operate inside that blocking
call; they do not free the Host loop. In contrast, native web reads already
use `WebToolOperation` to run network I/O outside that thread, with authorization
and completion serviced by the Host.

The run's four recorded prefetch durations are **102.87, 12.32, 64.83 and
49.08 seconds**. Engineering's prefetch occupied approximately
**18:52:21.869–18:53:26.703**, followed by Affective's at
**18:53:29.432–18:54:18.512**. ML emitted two web-search calls at **18:52:39**;
its first timed out after 30.016 seconds and the second reply arrived 60.025
seconds after emission because the MCP stdio bridge services calls serially.
That second value is not one 60-second HTTP request. Host errors and two
`BrokenPipeError` response writes arrived only at **18:54:36**. The direct
DuckDuckGo failure is real, while delayed Host servicing independently explains
why two callers timed out before receiving that failure. Delayed control,
collection and pool reconciliation under the same conditions are code-level
consequences; this review does not claim an observed cancellation during those
particular prefetch windows.

A fresh offline diagnostic reuses `WorkflowHost`, `AgentToolServer`, the
existing fake catalog and admitted fixture Profile. An immediate web fixture
responded in **0.0047 seconds** with normal owner-thread servicing. Injecting
**0.5 seconds** into literature retrieval made the actual Host tick take
**0.543 seconds**; an otherwise identical socket call with a deliberately
shortened **0.2-second** deadline timed out and its late reply raised
`BrokenPipeError`. This reproduces the servicing defect without external
network failure. It is not native CC, scientific validation or full G3 closure.
Two fake Agent Attempts were CLOSED; no Process was created. All client/server
threads, sockets, handles and the temporary root were cleaned up. Existing
`test_web_tool_delivery.py` covers a slow web read, but not simultaneous slow
literature prefetch; this is the missing regression boundary.

Bounded credential-free checks at **23:55 on September 15** independently
reproduced direct DuckDuckGo failure using the current Conda process and the
existing `PublicHttpsTransport`: resolved IPv4 timed out, IPv6 returned errno
101; arxiv.org and ACL Anthology TCP connections succeeded. Historical DNS and
the specific network/DNS fault are not established by this current probe.
`PublicHttpsTransport` uses `http.client.HTTPSConnection` directly and does not
read HTTP(S)_PROXY. The current shell has proxy variables pointing to a
loopback service at port 7891, but an unauthenticated proxy request and a
separate SOCKS5 greeting also timed out around midnight. The first curl probe
additionally used an unsupported JSON statistics formatter; preserve its
measurement error and timeout, not a successful proxy verdict. No proxy/service
configuration was changed and no working alternate route was established.

Prioritized minimal follow-up:

1. Move the reproduced literature I/O off the Host's servicing thread, reusing
   the existing worker-read / owner-thread-completion pattern. Preserve owner
   checks, cancellation, retries, budgets and late-result rejection. The
   adapter's mutable per-batch traces/cache handling must not be shared across
   unsynchronized concurrent retrievals; simply wrapping each call in a new
   thread is insufficient. Add the slow-prefetch/concurrent-tool regression to
   the existing tests rather than a new qualification mechanism.
2. Establish an actually working bounded search route. Merely changing keys,
   raising retry counts, disabling IPv6 or setting proxy environment variables
   does not fix the reproduced direct IPv4 failure. Any explicit proxy/backend
   support must preserve URL, credential, peer and response bounds. Reuse the
   existing deadline machinery to bound queue plus network plus delivery;
   increasing the MCP timeout alone leaves the Host stall intact.
3. For the next pooled topic, review the known endpoint and actual `serve`
   arguments in CC; reuse the stored binding display. PDF fallback cooldown
   across batches is a lower-priority improvement to assess with the existing
   rate-limit diagnostics. Keep metadata-only entries honest and preserve the
   current per-batch stop-on-429 behavior.

Additional evidence in the same ignored review directory:
`operator-binding-review.json`, `retrieval-exception-chains.json`,
`literature-batches.json`, `web-call-timeline.json`,
`retrieval-prefetch-windows.json`, `retrieval-network-probe.json`,
`retrieval-proxy-probe.json`, `retrieval-proxy-protocol-probe.json`, and
`prefetch-blocking-probe.py` / `.json`. No new paid model, OpenAlex-key or GPU
test was run; these diagnostics do not promote a G4/G5 lane.

### Spend and retained evidence

The existing `fold_agent_attempts` / `usage_projection` reports **68.139247 USD
known model cost plus four unknown calls** (the three timeouts and one cancelled
Agent). **14.745211 USD** of the known amount belongs to failed Agent Attempts;
the two final-report Attempts together report **1.822911 USD**. These are
provider-reported amounts, not an invoice total; unknown calls are not free.
This research run has no invocation USD cap or cumulative USD cap in its frozen
contract and uses token `observe` mode. It is separate from the earlier 20 USD
qualification cohort; cancellation was an operator action, not a recorded
budget-exhaustion termination.

Evidence is retained under ignored
`.local/qualification/merc-1803-review-20260915/`: `snapshot.json`,
`attempts.json`, `research-facts.json`, `oracle-bundle.json`,
`managed-host-and-bundle.json`, `host-journal-commands.json`,
`execution-capabilities.json`, `experiment-grants.json`, `published-results.json`,
`validation-errors.json`, `native-summary.json`, `native-tool-results.json`,
`terminal-elapsed-observations.json`, `usage.json`, `cost-summary.json`,
`final-report.json` and `cleanup-final.json`. Native tool counts above are
deduplicated by existing tool-use IDs across resumed transcript archives;
raw line/occurrence counts are not added as new calls. The initial snapshot's
empty `configuration` field reflects an incomplete extraction; use
`configuration.json`, RUN r1, journal argv and the actual grants for binding
evidence. The accepted original
report remains at
`state/MERC-causal-202609151803/artifacts/MERC-causal-202609151803/agent/agent-de4edb873a4047d5a6cd3cc9fb1e7a4b-2/transcript/FINAL_REPORT.md`.
This review adds actual post-repair CPU/GPU/model observations within this
topic's scope; it does not promote an unexercised shared-pool G4 lane or replace
the public G0–G5 qualification selections.

## Current repairs — dynamic resources and truthful observations, 2026-09-15

**The bounded framework repairs and current offline/CPU verification are
complete. A fresh, bounded higher-model test can now be prepared in Claude
Code with the actual reviewed pool binding. Scientific acceptance quality,
real GPU/model behavior of these changed instructions and a long unattended
topic are not certified by this repair batch.**

The user authorized repairs after the two-topic review, with framework changes
limited to model-independent defects or consequential, reusable improvements.
The runtime changes below use existing SQL facts, pool dispatch, validators and
native instructions. They do not encode MERC thresholds, introduce another
scheduler or change the confirmed Profile. Existing topics, their bundles and
the shared pool remain read-only. This repair batch makes no real GPU or paid
model calls and does not start a research topic.

### Implemented scope

| Reproduced problem | Current repair and evidence |
|---|---|
| Completed experiment duration keeps growing and contaminates reports/SELF_EVOLVE | `_experiment_tool_view` stops elapsed time at the recorded last Process end when all its Attempts are CLOSED; exposes Process start/end timestamps. The focused test advances the observation clock by 60 and 3,600 seconds and observes the same duration. |
| MCP bounded wait changes queued work to `running`, hiding capacity waits | Preserve the actual returned status and expose the existing Host `resource_wait` reason to the owning Agent. No Process means elapsed zero; blocked/terminal semantics and ownership checks remain intact. |
| Pool capacity changes after selection | Reuse existing reserve/activate/release and alternate-device selection. A new regression changes capacity twice between reservation and activation: A → B → A, two pre-exec LOST Attempts, exactly one execution and one canonical outcome, all pool requests RELEASED, both Oracles clean. Existing wait/recovery, same-card overlap and repeated pre-exec loss cases also pass. Observations and execution are simulated in these pool tests; this is not real G4. |
| Reviewed pool parameters were absent from actual research Hosts | Strengthen the existing operator Skill's transport and pre-start inspection of actual `serve` argv. STATUS and the monitor now show the binding from the existing RUN creation transition. The existing managed-launcher argument round-trip test passes; no new launcher, default pool, automatic binding or configuration authority was added. |
| Repeated adjudication/final-report failures give generic reference errors | Existing validators now report exact missing outcome/reference IDs and inadmissible IDs. Native instructions distinguish `outcome_id` from fact/artifact IDs and explain single-chain keep/prune behavior. Accepted reference domains and complete-partition rules are unchanged. |
| Failed Grounding archives and active Process workspaces confuse Oracle | Session Oracle accepts only the already published, bounded evaluator files for the reproduced invalid-provider-envelope failure with no response. Publication identity, digest, file tree and transcript checks remain. Manager Oracle restricts Agent-consumer lookup to Agents; workspace residue checking applies after closure. Negative archive and CLOSED-residue tests still reject corruption. |
| Generated acceptance/training/SELF_EVOLVE mistakes | Improve existing Grounding and expert Skill instructions: isolate each mandatory validity violation with otherwise qualifying evidence, derive checks from raw records, run small admitted training/validation and checkpoint/audit probes, and verify learning premises against original Process facts. GPU probes remain Host-owned and authorized. No topic-specific parser or evaluator in a retained run was modified. Prompt improvements are not evidence that a model now follows them. |

The dynamic-capacity correction depends on actually launching the new Host
with the reviewed existing pool socket, independent namespace and allowed
UUIDs. Nonpooled grants remain fixed as designed; an idle alternate GPU alone
does not authorize rebinding. Pool activation rechecks current capacity, but
cannot prevent an unrelated external workload from growing after activation.
Existing failure/retry and cleanup rules still apply.

### Candidate and verification

Git HEAD remains `6bd5bb910c8b9385f828f530cf634f3bbd19bbda`; the candidate is the
uncommitted working tree, not that initial-import commit alone. Twelve files
under `hypoloom/` now differ from the retained topic bundles, alongside operator
Skill, focused test and documentation edits. The repair uses the complete local
Conda environment (`.conda/envs/hypoloom/bin/python`, Python 3.12.14,
SQLite 3.53.4) and imports this checkout. The locally resolved Claude Code is
now 2.1.272; the earlier topics used 2.1.270/2.1.271. Ordinary Git comparison and pytest
logs are retained in ignored `.local/qualification/merc-repairs-20260915/`.

- Initial `red.xml`: seven intended old-code failures and one erroneous new
  manager-test fixture (`StopIteration`); do not count the fixture error as a
  reproduced runtime defect. `focused-1.xml` then records 170 passes and that
  one fixture failure. Its correction is included in subsequent passing tests;
  neither original result is overwritten.
- `dynamic-pool.xml`: 10 passes, including the corrected manager test and all
  shared-pool closure regressions. `focused-2.xml`: 133 passes across dispatch,
  native prompts, monitor, managed launch and documentation. Counts overlap
  subsequent qualification and must not be added to it.
- Current-code, read-only Oracle audits of both retained Hypoloom topics pass
  with no issues (`current-oracle-first.json`, `current-oracle-second.json`).
  The first topic's original bundled Oracle still has its recorded two failures;
  the new reader result does not rewrite that evidence or authorize recovery.
- A newly built wheel contains all 131 current runtime files, byte-for-byte;
  the source archive contains all 544 checked runtime/source/guide files and
  excludes private state, local Profiles and environment roots. CLI/MCP entry
  points load from a fresh isolated wheel installation using the complete Conda
  interpreter. A fresh installed-wheel CPU demo reaches COMPLETED with clean
  Oracle, zero OPEN Attempts and no owned process residue; its Host exits and
  its temporary state directory is removed (`wheel-cpu.json`, `wheel-cpu.log`).
  This sample uses the fake Agent and proves package/CPU plumbing, not model
  behavior or Claude authentication. The final source archive is rebuilt after
  the verification guide and memory updates.
- Full current-candidate G1 coverage is complete. The initial serial invocation retains
  631 completed passes and an explicit KeyboardInterrupt after 914.70 seconds;
  it is not an overall pass. The unfinished leader scenario's 36 recorded
  process groups are absent after fixture cleanup. Its unfinished JUnit element
  has no test identity and is not counted. Following the earlier eight-group
  pytest practice, the remaining 4,989 distinct cases ran in eight disjoint
  groups, including that unfinished scenario. Collection is 5,620 cases;
  `g1.xml`/`g1.log` preserve the partial invocation, and `g1-remaining-*.xml`
  preserve the continuation. Their combined first result is **5,584 passed,
  11 failed, 25 skipped**. All eleven failures share the old status-test double
  in `test_shared_acceptance_observation.py`, which allowed only the dialogue
  query and rejected the new stored-binding query. Updating this test double
  preserves its read-only/reused-evidence assertions. The affected module then
  passes **11/11** (`observation-recheck.xml`); runtime bytes did not change
  during or after G1. The original failures remain recorded.
- Final evidence covers **346 files / 5,620 distinct cases: 5,595 with passing
  evidence and 25 explicit opt-in skips**, with no missing or double-counted
  identities (`g1-coverage.json`). This is the full selection plus its targeted
  test-fixture correction, not a claim that the first full selection was green.
  Existing G2 portable persistence/migration/backup/bundle selections pass
  **169/169**, supplemented by both actual retained-topic reader audits above.
  The seven selected G3 cases all pass, including real CLI/local provider,
  broker, CPU Process, publication, same-bundle recovery, next action,
  TUI leave/re-entry and End run. Responses/literature are deterministic local
  fixtures; these are not paid scientific-model results.
- Five G3 states were additionally read before pytest removed temporary roots:
  **150 CLOSED Attempts / 150 absent recorded process groups**, with clean
  current Oracles (`g3-durable-counts.json`). The other two cases retain their
  JUnit and existing teardown assertions; no extra durable-row count is claimed
  for their already removed fixtures. The installed-wheel CPU sample is
  additional. Earlier 5,581 passes and historical G4/G5 results remain scoped
  to their earlier runtime bytes, not substituted for this verification.
- A manual G3 artifact-reader probe incorrectly supplied a relative artifact
  root and failed path/identity checks (`g3-relative-path-probe.json`). Both
  original and retained-copy reads pass when given their absolute paths
  (`g3-serial-reader-locations.json`). Preserve the probe failure separately;
  it is not a passing test, a runtime regression or a recovery operation.

At final cleanup all eight pytest subprocesses have exited and a separate
process scan finds no Host/Agent/worker bound to this batch's temporary roots.
The dynamic-pool fixture, installed-wheel CPU state and remaining G1 temporary
roots were removed; retained logs and selected fixture evidence stay under the
ignored batch directory (`cleanup.json`). A fresh read at **15:20:54 +08:00**
still finds the two real topics CANCELLED at r108/r63, zero OPEN Attempts,
all 193 recorded process groups absent and both control sockets gone
(`retained-liveness.json`). No real shared-pool service was changed.

A Host-managed, model-free waiting redesign remains a separate lifecycle
change. This repair makes waiting truthful without changing the independent
Agent/Process deadlines, increasing retries or silently editing topic budgets.
The generated MERC parser's earlier counterexamples and second parser's seven
CPU cases remain evidence about those retained evaluators, not a universal
scientific validator. A subsequent higher-model comparison must test the new
instructions and generated artifacts through the real bound Host under explicit
resource and cost limits. No real G4/G5 pass is claimed for these changed bytes.

## Earlier synthesis — two completed Hypoloom topics, 2026-09-15

**Both Hypoloom topics are now CANCELLED and their Hosts have exited. Neither
achieved the MERC scientific goal. The platform can execute research, but these
runs expose acceptance, launch-binding and observation defects that should be
resolved before another long unattended topic. A stronger model is a subsequent
bounded comparison, not a substitute for those repairs.** This review changes
documentation only; it does not repair runtime code, operate a retained run,
launch research or make new GPU/model calls.

This section compares `state/MERC-causal-202609150044` and
`state/MERC-causal-202609150849`. The older Prometheus-versus-Hypoloom comparison
below remains historical; its counts are not either of this pair's totals.
All times here are September 15, **UTC+08:00**.

### Final facts and evidence scope

| Observation | First Hypoloom topic, `202609150044` | Second Hypoloom topic, `202609150849` |
|---|---|---|
| Actual provider-reported model / Claude CLI | `mimo-v2.5` / 2.1.270 | `glm-5.3-flash` / 2.1.271 |
| Created / operator CANCEL / terminal | 00:59:01 / 08:22:02 / 08:32:15 | 09:02:21 / 12:52:31 / 12:58:42 |
| Final durable state | CANCELLED r108 | CANCELLED r63 |
| Agent Attempts, all CLOSED | 112: 64 SUCCEEDED, 38 FAILED, 8 TIMED_OUT, 2 CANCELLED | 55: 42 SUCCEEDED, 9 FAILED, 2 TIMED_OUT, 2 CANCELLED |
| Process Attempts, all CLOSED | 21: 9 SUCCEEDED, 11 FAILED, 1 CANCELLED | 6: 4 SUCCEEDED, 1 FAILED, 1 CANCELLED before spawn; only five actual launches |
| Canonical outcomes | 8 CLEAN_REFUTATION, 13 EXECUTION_FAILURE | 4 CLEAN_REFUTATION, 2 EXECUTION_FAILURE; one represents cancellation before spawn |
| Accepted scientific success / goal verdict | None / none | None / none |
| Reported known Agent cost | 151.916606 USD **plus 12 unknown calls** | 84.265526 USD **plus 4 unknown calls** |
| Known cost on FAILED Agent Attempts | 69.547501 USD | 10.262597 USD |
| Final report | Two rejected attempts, third accepted; 3.370622 USD known | First attempt accepted; 1.281467 USD known |
| Distinct native compactions | 57 UUIDs across 112 retained transcripts | 21 UUIDs across 55 retained transcripts |
| Retained-bundle Oracle | FAIL: two `session_archive_invalid` issues | PASS: no issues |

Total known reported model spend is **236.182132 USD plus 16 unknown calls**;
this is not an invoice total and is separate from the earlier 20 USD
qualification cohort. Unknown timed-out/cancelled/failed calls are not free.
The two models ran for different durations, with different generated evaluators,
experiment implementations and CLI versions. Their success fractions or F1
values do not constitute a controlled model ranking.

At the final liveness inspection **12:59:36**, each run's 131 bundled runtime
files byte-matched the then-current `hypoloom/` tree, before the repairs above. Git HEAD remained
`6bd5bb910c8b9385f828f530cf634f3bbd19bbda`; the imported package, tests and docs
are still uncommitted/untracked, with tracked README/.gitignore changes. HEAD
alone is not this candidate. Inspection used the complete local Conda Python
and SQLite 3.53.4. Both actual topic Hosts selected all four local GPU UUIDs
without shared-pool flags, differing from the earlier reviewed two-GPU/pool
preparation. Pool liveness or the isolated preparation test did not bind these
Hosts. The initiating Claude Code approval conversations were not audited here;
this observation does not infer who omitted arguments or a separate permission
violation.

### Scientific progress and reliability

The first topic's engineering experiment 7 recorded IEMOCAP **0.67415719** and
MELD **0.65141933**, still below the strict five-seed targets **>0.7305** and
**>0.6674**. Its generated common evaluator admitted all four previously
reproduced invalid inputs: excessive causal drift, duplicate checkpoint audits,
empty shared-core configuration and missing future-speaker perturbation. Some
other CLEAN_REFUTATION records contain zero results or failed causal checks;
the affective expert's final MELD trials all have NaN test losses. These labels
cannot all be treated as scientifically sound negative experiments.

The second topic's four completed evidence bundles each contain ten retained
checkpoint files and ten audit records with the three named perturbations.
Their shared parser reports all 13 checks true and measured maximum drift 0.
Representative same-experiment IEMOCAP/MELD pairs are **0.63910284/0.63505094**
(ML first experiment), **0.63002001/0.64131610** (its revision),
**0.63632407/0.63384390** (affective first experiment), and
**0.34820477/0.63500501** (repaired engineering experiment). None meets the
joint goal. The engineering IEMOCAP validation/test gap is observed; attributing
it conclusively to session generalization still needs a controlled experiment.

This review ran the second topic's unchanged accepted parser through the
existing `SharedAcceptance` evaluator on seven CPU-only cases: retained actual
evidence, a synthetic high-score control, the four earlier counterexamples and
threshold equality. All seven behaved as expected: only the synthetic control
qualified. This is a material improvement in this generated evaluator, **not a
platform repair or proof that the next Grounding will be equally correct**.
Checkpoint existence and published audit contents were inspected; the forty
checkpoints were not loaded to independently repeat their model-level audits.
Parser aggregation success is not a fresh scientific replication.

### Reproduced issues and development priorities

1. **P0 — generated acceptance can omit the scientific requirements.**
   First-run engineering experiment 7's retained `goal/parser/evaluate.py`
   declares `AUDIT_MAX_DIFF` at line 23 without enforcing it, trusts `passed`
   at lines 111–123, counts rows rather than distinct checkpoints near line
   196, and treats configuration existence as shared-core validity near line
   201. The ordinary counterexamples already reproduce false acceptance;
   neither real run actually produced a false successful goal verdict.
   [Native smoke validation](../hypoloom/native_output.py) executes the parser
   and validates its output shape; it cannot by itself prove commission
   completeness. Minimum next work: use the existing accepted evaluator and
   ordinary goal-specific positive/negative tests for strict thresholds,
   checkpoint coverage, all three perturbations and actual shared-core inputs;
   review the raw audit producer too. Extend the existing Grounding/test path,
   without adding a second scientific authority or replacing retained parsers.

2. **P1 — actual Host binding missed the intended shared-pool path.**
   Second-run affective experiment 2 froze GPU 3 from the 11:38 capability
   input. An external UID 1023 workload occupied that GPU at 11:44; its
   approximately 1,450 MiB free was below the 12,288 MiB requirement while
   GPUs 0/1 were idle. The experiment stayed READY from 11:44:45 until the
   operator stopped it; it never launched. The waiting Agent timed out at
   12:28. [Submission/dispatch](../hypoloom/workflow_host.py), lines 3316–3332
   and 3647–3661, and [selection](../hypoloom/compute_resources.py), lines
   487–573, implement this fixed nonpooled grant. The existing shared-pool
   alternate-device regression passes; no replacement scheduler is needed.
   Minimum next work: carry one reviewed `serve` argv through the existing
   [managed launcher](../.claude/skills/autoresearch/scripts/manage_host.py),
   verifying socket, namespace and allowed UUIDs in its actual launched
   process/status. The [operator Skill](../.claude/skills/autoresearch/SKILL.md),
   lines 781–784, already requires that transport. Surface the existing resource
   wait reason to the experiment Agent as well as the monitor. Do not hot-rebind
   these ended runs or auto-discover/replace a pool.

3. **P1 — terminal experiment elapsed time is wrong and contaminates learning.**
   [WorkflowHost._experiment_tool_view](../hypoloom/workflow_host.py), line
   3074, always subtracts start time from the current clock, even after CLOSED.
   The second engineering failure actually lasted **4.737855 seconds**. Calling
   the unchanged method with its retained SQL rows and a mocked clock 60 or
   3,600 seconds after completion returns **64.737855** or **3604.737855**.
   Its accepted SELF_EVOLVE text instead cites approximately 3,482 seconds;
   the final report cites approximately 2,912 seconds. These are not actual
   training durations. The resulting plugin commit
   `47719839add99fc197fbb247af27dc06bf7c6838` was supplied to seven subsequent
   Agent Attempts. Minimum fix: stop elapsed time at the stored terminal time,
   keep queue age distinct, and test repeated terminal reads using the existing
   Host-tool fixture. Reports/self-evolution should cite existing Process
   timestamps, not turn growing observation age into an optimization premise.

4. **P1 — repeated structured-reference errors spend calls without new science.**
   ADJUDICATE failed nine times in the first run and six in the second; the
   latter includes `pruning requires a selected winner` and three consecutive
   ML evidence-domain rejections before cancellation. A concrete engineering
   retry cited `--fact-outcome-2-2` but omitted the required
   `--component-2--outcome-2`; its next retry included the latter and succeeded.
   [admit_adjudication](../hypoloom/expert_loop.py), lines 994–996, currently
   combines missing and inadmissible references into one message. First-run
   Judge output failed twice, and two final reports omitted exact references
   despite receiving the required context.
   Minimum fix: expose the exact missing/disallowed IDs from the existing
   validator to the existing correction path; supply a compact, explicit
   reference map in the existing request. Improve single-chain keep/prune
   examples and [final-report validation](../hypoloom/final_report.py), lines
   65–78, diagnostics. Preserve reference-domain and complete-partition checks;
   do not weaken them, invent aliases or merely increase retry limits.

5. **P1 — waiting and inference budgets are poorly matched.**
   Both profiles use a 3,000-second Agent limit against a 108,000-second
   experiment limit; first/second runs had eight/two execution-Agent timeouts.
   The second queued Agent spent its lifetime without starting a Process.
   The current per-call USD cap is null and token enforcement is `observe`.
   First expose truthful wait state, selected resource and budget use. A later
   change separating Host-owned waiting from model invocation needs a bounded
   same-bundle submit/wait/timeout/re-entry test. It must preserve exactly-once
   submission and the rule that pending execution is not a successful Agent
   terminal effect. Do not disguise this larger lifecycle change as a timeout
   increase or alter the user-confirmed topic budgets in this review.

6. **P2 — retained-history Oracle coverage has real defects.**
   First-run final Oracle still rejects the two failed Grounding evaluator
   archives documented below. [Session Oracle](../hypoloom/session_oracle.py),
   lines 233–258, derives permitted captured evaluator files from a response
   that may not survive an invalid provider envelope. Earlier active-run
   inspection also reproduced an absent OPEN-Process observation lookup in
   [manager Oracle](../hypoloom/manager_oracle.py), lines 201–205, and active
   work-directory misclassification in [Oracle](../hypoloom/oracle.py), lines
   3635–3657. Minimum fixes belong in these existing validators, with ordinary
   failed-Grounding and OPEN/CLOSED Process regressions; retain identity,
   publication and byte-integrity checks. The second final Oracle passing does
   not erase the first failure.

7. **P2 — generated experiment and self-evolution quality need tighter scope.**
   First-run import/path/shape errors and NaN results, and the second-run
   CUDA/CPU device mismatch, justify small real-data training/validation,
   checkpoint reload and audit probes before five-seed expansion. CPU success
   alone cannot certify GPU placement. GPU probes must still use authorized
   Host-owned Processes. The second evolved `experiment-preflight` Skill also
   recommends design-time full training plus test-set feedback; do not copy it
   into the product unchanged. Reuse validation data for development, keep the
   confirmed test protocol intact, and verify advice against original facts.
   No claim is made that its proposed design-time GPU/test probe actually ran.

First-run interaction did occur: two cycle reports, six Debate statements,
two rejected Judges and one accepted Judge, followed by next-cycle work.
The earlier native-read inspection confirms all six statement inputs reached
the Judge. The second run had one cycle report and an OBSERVER decision of
`none`; it had no Debate/Judge/Leader attempt before cancellation. Different
scientific proposals alone did not force a debate under the current policy.
This is observed trigger behavior, not evidence of a missing input. Compaction
counts are deduplicated native UUIDs, with zero JSONL parse errors; they do not
prove preserved reasoning or explain the model failures. Measure citation
accuracy, correction of factual mistakes and useful subsequent experiments
before increasing interaction frequency or claiming a benefit from compression.

### Recommended bounded development sequence

1. Repair terminal elapsed reporting, explicit managed pool-argument transport
   and exact validation-error feedback with focused regressions. These are
   deterministic platform issues; a paid full-topic rerun is not their debugger.
2. Exercise the existing Grounding/common acceptance path with the reproduced
   scientific counterexamples; audit generated training/audit code on bounded
   real data. Improve existing instructions from verified facts, not the
   erroneous duration-based self-evolution. Preserve retained runs as evidence.
3. Fix the existing Oracle's failed-archive and active-Process cases. Then
   freeze a candidate and use G1/G2 plus bounded G3 to verify the combined
   changes, including timeout/re-entry, report retries and End run cleanup.
   A redesigned model-free waiting path is a separately scoped follow-up.
4. Only then run an explicitly bounded higher-model G4/G5 comparison through
   the actual shared-pool Host, with the same goal, target revision, caches,
   acceptance tests and comparable work allowance. Observe actual provider
   model identity, native tools, meaningful hypothesis changes, cross-expert
   evidence use, final reports and cost. Model choice, reviewed UUIDs/runtime,
   total/per-call bounds and unknown-cost reservations remain explicit inputs;
   no new allowance or zero previous spend is inferred here.

### Verification and final cleanup

This review reused both retained bundles' Oracle and accounting fold, existing
SQL/Agent/result evidence and the previous parser/scheduling reproductions.
It added seven CPU parser cases and the terminal elapsed reproduction, without
starting a Host. The earlier fixed-grant and shared-pool alternate-device tests
remain applicable because their runtime bytes match; retain their initial
overlong-socket fixture ERROR separately from the corrected-path pass. G1's
earlier 5,581 passes/25 opt-in skips remain scoped to that candidate; no full
G1, new paid/GPU qualification or scientific replication ran in this review.
Final documentation navigation/boundary checks passed **4/4**; whitespace checks
passed. Neither real research failure nor a skipped lane becomes a G5 pass.

At 12:59:36, **all 193 recorded process groups (133 first, 60 second) were
absent**, both managed units were inactive/absent with MainPID 0, both control
sockets were gone, no Attempts were OPEN, and both disposable `artifacts/work`
directories were empty. GPU processes on physical cards 2/3 belonged to UID
1023, not either run. GPU 0/1 were idle. Retained result-staging contains only
stdout/stderr logs (42 files /386,428 bytes first; 10 /29,518 bytes second),
which the existing log reader uses; bundles, published results, logs, Git
sessions and checkpoints were preserved. This is complete live-process cleanup,
not deletion of research history. The existing shared pool was untouched.
All seven review-created temporary parser directories were removed.

Detailed observations, accounting, counterexamples, original inspection error,
elapsed reproduction, checkpoint inventory and cleanup evidence are under
`.local/qualification/merc-two-hypoloom-synthesis-20260915/`; earlier logs remain
at their original locations below. An initial inventory probe assumed the
second run's checkpoint JSON layout in a first-run result and failed; the
corrected read follows each published layout. That is an inspection error,
not a product test failure. No commit, push, runtime/Profile change, external
notification, GPU reservation or new model invocation was made.

## Earlier observation — second Hypoloom topic GPU wait, 2026-09-15

At **12:35–12:40 +08:00**, the second Hypoloom topic
`state/MERC-causal-202609150849` is ACTIVE (observed revisions 55–56), with a
responsive Host and `dispatch_error: null`. Its actual model is
`glm-5.3-flash`, Claude Code 2.1.271. This focused read-only scheduling diagnosis
does not reassess its generated scientific evaluator or promote a new G5 lane.
The earlier Mimo topic `202609150044` is now durably CANCELLED r108 at 08:32:15;
the preceding 08:31 stopping observation remains historical.

**The empty GPUs cannot serve the queued experiment because this nonpooled
run already bound that experiment to a different, occupied GPU.** The waiting
work is affective-computing expert experiment 2,
`MERC-causal-202609150849--component-3--experiment-2`. It has been READY since
**11:44:45**, with no Process Attempt created. Its accepted execution contract
contains `resource_grant.gpu_uuid = GPU-20ca7d4a-89d1-ec85-9bee-690afdd3283a`
(physical GPU 3), and the same UUID in `CUDA_VISIBLE_DEVICES`.

| Physical GPU | Observed free memory | Current ownership / effect |
|---|---|---|
| 0 / `GPU-d469e765-ad52-350d-2cd4-9b12e5e5bc84` | 24,121 MiB | Idle; permitted by the Host inventory but not this experiment's fixed grant |
| 1 / `GPU-e96b09e9-0bcc-d031-acce-bb705dabfa31` | 24,118 MiB | Idle; likewise outside this fixed grant |
| 2 / `GPU-91a3b0a9-d48b-1503-2893-33153c3fe405` | Approximately 1,450 MiB | Other user's SGLang process, UID 1023 / PID 1434986 |
| 3 / `GPU-20ca7d4a-89d1-ec85-9bee-690afdd3283a` | Approximately 1,449–1,450 MiB | Other user's SGLang process, UID 1023 / PID 1434985; this is the queued experiment's selected device |

The exact Host wait reason is `granted GPU GPU-20ca7d4a-89d1-ec85-9bee-690afdd3283a
has 1449 MiB free; 12288 MiB is required`. The 12,288 MiB floor is the confirmed
8,192 MiB workload minimum plus 4,096 MiB reserve. At the 12:38 observation,
all five preceding Process Attempts are CLOSED (four SUCCEEDED, one FAILED),
and only two Agents are OPEN. The configured three-Process concurrency cap is
not exhausted. A GPU's instantaneous 0% utilization does not make its occupied
memory available. No other user's process or service was modified.

The submitted Agent's original compute input was captured at **11:38:01**:
GPUs 2/3 each had 24,117 MiB free, GPU 0 was marked Host-owned busy, and GPU 1
had 22,954 MiB free. Existing `compute_resources.select` ranks free memory then
UUID, selecting GPU 3. The external SGLang processes started at **11:44:02**;
the experiment was submitted at 11:44:43 and preregistered at 11:44:44 using
the original request input. `workflow_host.py:3316–3332` deliberately reuses
that input; `workflow_host.py:3647–3661` only rechecks the fixed grant at launch
and retains a resource wait when its memory floor fails. Repeated dispatch
does not select another GPU. Replaying the existing selection function on
the original bytes chooses GPU 3; a hypothetical fresh sample chooses GPU 0.
That pure-function comparison made no reservation or Process.

The actual managed Host argv contains all four GPU UUIDs but **no
`--gpu-pool-socket` or `--gpu-pool-namespace`**. The live shared pool has zero
run/request rows for this Run ID. Its existence does not enroll new Hosts.
The operator Skill already specifies forwarding those reviewed flags at
`.claude/skills/autoresearch/SKILL.md:781–784`; the earlier confirmed pool plan
was not used by this launch. All 131 retained runtime files still match the
checkout, so this is not an unobserved alternate scheduler implementation.

The source Agent `agent-5f05798bc0c74a2faefa830e08e1e76a-1` repeatedly waited on
the READY experiment, then TIMED_OUT at **12:28:04** after 3000 seconds. Thus
resource waiting has already outlived an Agent call without starting training.
Its timeout does not remove the registered experiment or free the occupied
external device. Host liveness and progressing peer Agents do not imply that
this waiting experiment can advance.

Two existing focused regressions establish the intended distinction:
`test_mcp_peer_chooses_less_free_unoccupied_gpu_and_replay_keeps_frozen_grant`
passes; `test_queued_work_can_use_a_different_gpu_when_only_that_device_recovers`
passes with a fresh short temporary root and the unchanged shared-pool code.
These use synthetic inventories/jobs, not real CUDA or paid Agents. The initial
second fixture had an **AF_UNIX path too long setup ERROR** under the long log
directory; preserve `focused.xml` (one pass /one error) separately from
`pool-focused-recheck.xml` (one pass, 0.81 s). No test or validator was weakened.
The private test pool request is RELEASED, sockets are removed, and no recorded
test process group remains. The existing production pool was only read.

The smallest supported direction for **fresh runs** is the existing shared-pool
path with an explicitly reviewed namespace and allowed UUID set: it stores
resource alternatives and selects live capacity at dispatch. Using idle GPUs
0/1 requires them to be included in that reviewed set; a pool limited to 2/3
would still wait. Adding pool flags to this already-created nonpooled run is
not an approved migration, and editing its accepted GPU grant is not a
read-only fix. This diagnosis makes neither change and starts no replacement
run. A future visibility improvement can reuse the existing wait reason/UUID
in the current experiment-tool view; it does not require another queue store.

Read-only status, original capability bytes/contract excerpts, GPU ownership,
selection replay, test errors/passes and cleanup evidence are under
`.local/qualification/merc-second-resource-review-20260915/`. The first SQL
inspection used a nonexistent transition `command_type` column; its correction
uses `command_id`. An initial selection probe supplied noncanonical JSON and
was rejected before selection; the corrected probe consumes the original
stored input bytes. These inspection errors are separate from the live wait.
No runtime/Profile, existing run, shared pool or GPU workload was changed.

## Earlier two-run review — 2026-09-15 morning

Historical scope: the following morning comparison pairs the older Prometheus
topic with the first Hypoloom topic, while that Hypoloom topic was still closing.
The two completed Hypoloom topics are compared in the current synthesis above.

**The platform has run real research, but the latest generated common evaluator
does not enforce the confirmed scientific commission. Preparing a new test with
a stronger model is reasonable; another long MERC run must not be presented as
scientifically qualified until this acceptance defect is addressed.** No runtime
repair or new research/model/GPU launch was made during this review. The earlier
bounded qualification remains scoped to its candidate, model and configuration.

### Two-run comparison and evidence scope

This comparison uses the two most recent MERC topic runs, not two qualification
canaries:

- Previous: `/data/Gray/Project/Prometheus/state-vnext/MERC-causal-sota-v16-202609141626`.
- Current: `/data/Gray/Project/Hypoloom/state/MERC-causal-202609150044`.

Both databases were read with SQLite `mode=ro` / `query_only`. Each run was
audited using **its own retained bundle's Oracle and `fold_agent_attempts`**;
the old run was neither migrated nor promoted as Hypoloom qualification.
The Hypoloom candidate is still HEAD
`6bd5bb910c8b9385f828f530cf634f3bbd19bbda` **plus the dirty/untracked working
tree**. Ordinary byte comparison finds all 131 files in its running bundle
equal to the current package. Control Python is the project's complete Conda
environment, SQLite 3.53.4; both topics bind the ERC target and `erc-cu128`
experiment environment already reviewed below. Their lifecycle/research
contract versions and generated acceptance sources differ.

The retained observations below were taken at **08:24 +08:00 on September 15**.
The current run had received an actual TUI CANCEL at **08:22:02**, revision 106;
revision 107 was STOPPING while FINAL_REPORT ran. The previous run was already
CANCELLED at revision 82, September 15 **01:01:08**, after a TUI CANCEL at
00:34:40. These are operator cancellations, not scientific completion.

| Observation | Previous Prometheus topic | Current Hypoloom topic, 08:24 |
|---|---|---|
| Actual model | `glm-5.3-flash`, provider-response identity on 71 calls | `mimo-v2.5`, provider-response identity on 109 closed calls |
| Agent Attempts | 71 CLOSED: 53 SUCCEEDED, 6 FAILED, 11 TIMED_OUT, 1 CANCELLED | 110: 63 SUCCEEDED, 36 FAILED, 8 TIMED_OUT, 2 CANCELLED, 1 OPEN FINAL_REPORT |
| Process Attempts | 14 CLOSED: 11 SUCCEEDED, 1 FAILED, 2 CANCELLED | 21 CLOSED: 9 SUCCEEDED, 11 FAILED, 1 CANCELLED |
| Canonical outcomes | 11 CLEAN_REFUTATION, 3 EXECUTION_FAILURE | 8 CLEAN_REFUTATION, 13 EXECUTION_FAILURE |
| Accepted scientific success | No GoalVerdictRecorded; no candidate exceeds both targets | No GoalVerdictRecorded; no candidate exceeds both targets |
| Research interaction | No completed CYCLE_REPORT, Observer, Debate or Judge | Two cycle reports/graph evolution/self-evolution closures; one Observer; six Debate statements; Judge FAILED twice then SUCCEEDED; designated next-round work actually starts |
| Native compaction | 60 distinct `compact_boundary` UUIDs | 55 distinct UUIDs at this observation |
| CLI/Host reported known model cost | **144.606664 USD**, plus **13 unknown-cost calls** | **148.545984 USD**, plus **12 closed unknown-cost calls and one open call** |
| Known cost on FAILED calls | 7.507903 USD | 67.456295 USD |
| Own bundle Oracle | PASS, zero OPEN/residue | FAIL: two `session_archive_invalid` findings; no Process remains OPEN |

These are observed call charges, not verified provider invoices. Unknown costs
remain unknown. Both topic Profiles omit invocation/total/per-expert USD caps;
the 1,000,000-token setting is `observe`, not a hard spend limit. Their research
costs are separate from the earlier **13.005258 USD /20 USD qualification
cohort**. The user has not yet selected a new model or supplied the next test's
time/cost/resource bounds in this follow-up. Changing directories does not
reset a test cohort or reconcile unknown usage.

The earlier **07:35** read-only observation remains a historical point:
81 closed Agents, 20 Processes including two live GPU trainings, known reported
cost 118.491042 USD plus ten unknown calls, 41 distinct compactions, and a
proposed debate waiting for the ML experiment. Later progress supersedes its
liveness/counts without erasing failures. In particular, debate was proposed at
06:46:25, started at **07:37:47** after the participant drained, reached a verdict
at **08:13:06**, and designated follow-up work at 08:13:09. This wait was not
`DISPATCH PAUSED`, a dead Host, or a completed run.

The actual Hypoloom Host used Claude Code 2.1.270, `mimo-v2.5`, settings-file
authentication and **all four local GPU UUIDs**, with **no shared-pool flags**:
`GPU-d469e765-ad52-350d-2cd4-9b12e5e5bc84`,
`GPU-e96b09e9-0bcc-d031-acce-bb705dabfa31`,
`GPU-91a3b0a9-d48b-1503-2893-33153c3fe405`, and
`GPU-20ca7d4a-89d1-ec85-9bee-690afdd3283a`.
This differs from the prospective `glm-5.3` / two-GPU shared-pool binding
reviewed earlier. The old topic has 14 RELEASED requests in the existing pool;
the new topic has no request/run rows there. This is an observed configuration
difference; this audit did not inspect the separate Claude Code authorization
conversation or alter either binding. Authentication contents were not printed.

### Scientific results and reproduced acceptance failure

The completed five-seed pairs below are canonical measurements, not accepted
scientific successes. Dataset columns must stay paired within one candidate.

| Run / candidate | IEMOCAP mean weighted-F1 | MELD mean weighted-F1 | Qualification limit |
|---|---|---|---|
| Previous engineering experiment 1 | 0.6739669820660348 | 0.6440127627163108 | Best old IEMOCAP; both targets missed |
| Previous engineering experiment 2 | 0.6461062942435032 | 0.654213289985976 | Best old MELD; a different candidate |
| Previous engineering experiment 3 | 0.6730438698278683 | 0.6527241940737264 | Both targets missed |
| Current engineering experiment 7, SWAG | 0.6741571949186657 | 0.6514193329909388 | Both targets missed; generated checks do not prove the full causal contract |
| Current ML experiment 5, ECDS | 0.66115365999209 | 0.6500094646041381 | Now published with all five seeds; causal audit false |
| Current affective experiment 8, CEID | 0.6877828339080602 | 0.3126849060381001 | All five MELD test losses are NaN; degenerate prediction and failed audit |

The target remains IEMOCAP **>0.7305** and MELD **>0.6674**, with the same
core candidate and all ten final checkpoints individually measured under all
three future perturbations. The old report's all-zero audit statements were
not independently retrained or remeasured here. Its parser enforces the named
measurements and checkpoint/seed coverage; that structural result alone does
not establish scientific correctness of every producer. The current eight
CLEAN_REFUTATION rows likewise must not be called eight valid scientific
experiments: they include zero-output and failed-audit cases. Process exit 0,
typed acceptance and scientific validity are different observations.

For a concrete comparison, the **unchanged accepted parsers** from both runs
were each executed on five tiny isolated CPU inputs. The controls deliberately
use synthetic 0.9 scores; they are not research results. Existing
`SharedAcceptance.from_dict` / `evaluate_shared_acceptance` decide the outcome:

| Synthetic parser input | Previous acceptance | Current acceptance |
|---|---|---|
| Control | Admits synthetic control | Admits synthetic control |
| Measured future-logit drift 0.001, exceeding 1e-6 | Rejects | **Incorrectly admits** |
| Ten audit entries repeat the same checkpoint | Rejects | **Incorrectly admits** |
| Empty core configuration | Rejects | **Incorrectly admits** |
| Missing future-speaker perturbation | Rejects | **Incorrectly admits**; the current input needs none of the three perturbation records |

All ten parser processes exited; their ten temporary directories were removed.
No GPU, model call, new fact, real-run write or network request was involved.
Inputs, outputs, unchanged parser paths and actual existing-validator results
are retained in `parser-counterexamples.json`. This extends the previous
three-counterexample review with a paired old/new parser comparison. It proves
a **potential false scientific success**, not an already recorded false
GoalVerdict in either topic.

### Combined findings and smallest next steps

| Priority | Evidence, impact and proposed correction; none implemented in this review |
|---|---|
| High: generated common acceptance weakens the goal | Current accepted `goal/parser/evaluate.py` at lines 103, 111–123 and 196–203 counts entries, trusts `passed`, and treats configuration-file existence as shared-core validity. `AUDIT_MAX_DIFF` at line 23 is unused, and no max-drift criterion exists in the accepted contract. The four counterexamples above pass through the actual shared evaluator. Keep the existing Grounding/acceptance path; add topic-derived negative examples to its ordinary local checks and ensure the next generated parser checks the confirmed three perturbations, threshold, exact ten checkpoint identities and actual shared configuration. Do not patch this retained run's accepted source or introduce a second evaluator authority. |
| High: invalid audit evidence propagates into reasoning | The current ERC `full_audit` feature permutation at `src/merc/causal/audit.py:136` explicitly excludes speaker inputs. Current ML's `scripts/ecds_run_all.py:228–233` also contains a missing-checkpoint branch emitting `passed=True`; this review does not claim that branch executed. The real Judge then describes gradient/feature checks as complete causal verification and uses that claim to rank CDF above ECDS. A stronger Judge cannot recover missing measurements from prose. Future experiment code must reload the actual final checkpoints and measure all three required perturbations; missing checkpoints or nonfinite outputs must fail. Reuse existing checkpoint/output paths and tests, with no new digest scheme. |
| High: execution reliability and result validity | The new topic has 11 failed Processes versus one in the old topic. Published failures include missing `merc`/`tqdm`, wrong cache/read-root paths, split-manifest mismatch, non-executable script, missing argument attributes and tensor-shape/empty-list errors. CEID repeats MELD weighted-F1 0.3126849060381001 with NaN loss across all five seeds. Fix the reproduced import/path/shape/numerical problem in the new candidate's existing wrapper, then require a bounded real-data train/validation/output/parser check before expanding the run. Do not count a NaN workaround or a zero-score placeholder as a clean scientific refutation. |
| High before another paid test: unbounded/unknown spend | Both runs have null USD caps and unreported interrupted calls. The current run spent 67.456295 known USD on FAILED calls by 08:24, before final-report retries. Higher model capability alone supplies no cost bound. Select an exact provider model/auth source and explicit invocation, aggregate/per-expert, wall-time and resource limits for the new test; use the existing accounting fold and retain unknowns and original failures. No fee or new authorization is inferred here. |
| Medium: repeated adjudication/reference errors | Old ADJUDICATE fails three times and never completes; new ADJUDICATE fails nine times before two successes. Both repeatedly hit `expert_loop.py:994–996`: the exact outcome reference is missing or references lie outside the admitted domain. Other failures violate keep/prune/winner partitions; both first FINAL_REPORT attempts omit an accepted outcome/verdict reference. The new Judge additionally fails twice before success. Keep these validators. Reuse their existing batch/tip inputs and report exact missing/disallowed IDs in the existing correction diagnostic, with focused regressions; do not solve this by relaxing evidence identity or adding blanket retries. |
| Medium: long execution waits consume Agent lifetimes | Old run: ten EXECUTION timeouts plus one DESIGN timeout; new run: eight EXECUTION timeouts. They occur around 3001–3005 s against a 3000 s Agent limit, while an experiment may run for 108000 s. Ordinary retries and native compactions do not make those calls free. First verify existing submit/wait and same-experiment reuse with a short workload, then review the actual long-training/Agent budget combination. No duplicate-effect finding was observed; the data do not prove a scheduler deadlock or justify a new asynchronous lifecycle. |
| Medium: Oracle does not cover all observed lifecycle states correctly | At 07:35, `manager_oracle.py:201–205` dereferenced absent observations for OPEN Processes, and `oracle.py:3635–3657` classified active work directories as residue. Those three findings disappeared after the Processes closed. Two failed Grounding archives still fail `session_oracle.py:233–258`: evaluator files were preserved without a response/rejected_response, so the expected-path calculation omits them. Both existing archive digests match and 440/500 JSONL records parse. Repair the existing observation/path handling with focused OPEN and failed-capture regressions; retain all identity/digest checks and do not relabel the current Oracle as clean. |
| Medium: actual launch differs from reviewed resource plan | The preceding prepared shared-pool binding was not used by the actual Hypoloom Host. For the next test, review the actual managed-launch argv and pool namespace together with the selected GPU UUIDs. Reuse the user's approved existing pool and independent namespace if that remains the selected plan; never infer a binding from pool liveness or replace the shared service. |

Current parser evidence is the published engineering experiment-7 result under
the current root's `artifacts/<run>/<run>--component-2--experiment-7/` subtree;
the previous parser is the published component-1 experiment-1 result under its
own root. `result_sources` in the ignored observations retains their full paths.
The current commissioned requirements remain in the user-local
`profiles/local/MERC-causal/goal.yaml`.
The source-level findings and proposed fixes are not authorization to change
existing research state, runtime bundles, data or shared services.

The final-report failure is independently reproduced by each retained bundle's
`report_sources` / `validate_final_report`, without another model call. The old
first report abbreviates required outcome identities and fails; its second
report contains all 14 and passes. The current first two reports omit exact
outcome identities; the first also omits `NO_ACCEPTED_VERDICT`. The accepted
source context contains all 21 outcomes, so these failures are not evidence
that the Host lost the report inputs. `final_report.py:65–67` currently reports
the generic omission rather than listing missing IDs. Extend that existing
correction message if repaired; keep the complete-reference requirement.

The real interaction does establish new **observational** coverage: the Judge
received a 108,750-byte context containing exactly six canonical statements;
its native Read result contains all six source Attempt IDs. The existing
interaction Oracle accepts the statement/verdict/restart linkage. It chose
engineering 0.55 versus ML 0.45, and ML reached cycle-2 DESIGN before TUI
cancellation. This closes the earlier "not yet observed" statement for this
Mimo topic, but does not qualify judging quality or the old `glm-5.3` canary.
Compaction UUIDs are deduplicated across resumed transcript archives; event
occurrence does not establish retention of every scientific constraint.

### Recorded state and next model test

At **08:27–08:28**, the old run has no socket, no OPEN Attempts, all 85 recorded
process groups absent and all 14 pool requests RELEASED. The current run has
no OPEN Process or GPU compute process; Host PID 772177 remains active with
zero restarts, and the second FINAL_REPORT Agent is running after the first
failed with `invalid_workspace` / omitted outcome reference. Thus **current
Host/Agent shutdown is not yet complete at this observation**. The existing
shared pool PID 3785437 stays active. This review issued no CANCEL/stop/restart
and did not clean or rewrite either research run.

At the final recorded **08:31:47** observation, the current run is still
STOPPING r107: **112 Agent Attempts**, comprising 63 SUCCEEDED, 38 FAILED,
8 TIMED_OUT, 2 CANCELLED and one OPEN third FINAL_REPORT attempt. The first
two reports remain failed. Known reported cost has risen to **150.637190 USD**
(69.547501 USD on FAILED calls), with the same **12 closed unknown-cost calls
plus one open call**. There are 56 distinct compaction UUIDs. All 21 Processes
remain CLOSED, and the two original Grounding archive Oracle failures remain.
This later observation is saved separately from the 08:24 record; neither
unfinished report generation nor future fees are counted as complete.

Prepare the next model test in this order: resolve the reproduced acceptance
and output-validity defects with focused offline checks; select the exact
model and bounded budget/resource binding; run an authorized bounded native
sample covering Grounding, an actual experiment, result collection, report and
exit; then exercise the existing multi-expert debate/next-action path. Only
after these pass should training duration and search scope expand. Retain the
confirmed MERC targets/data/protocol and count valid candidate pairs, failed
calls, timeouts, unknown cost, real three-perturbation audit and terminal cleanup
as separate outcomes. The observed two runs are not a controlled model ranking:
model, generated code/evaluator, package and GPU-pool topology all differ.

Detailed evidence lives only in the existing ignored qualification tree,
`.local/qualification/merc-two-run-review-20260915/`: per-run observations using
their retained validators, original failure excerpts, parser counterexamples,
Judge input/Read evidence, scientific output excerpts, candidate/binding and
liveness observations. These are review logs, not a new qualification system.
Historical run bytes remain in their original roots. This follow-up changes
only this document, compact Codex memory/INDEX and ignored logs; it does not
start the user's proposed higher-model test, change the Profile or initiate a
new model/GPU call. Existing report-generation calls can continue charging
while this read-only review runs. Formal operations remain in Claude Code.
The existing documentation-structure checks pass **4/4** (`docs.xml`); Git
whitespace checks pass. No full or paid qualification was rerun for these
documentation changes.

## Earlier startup qualification — selected binding ready, 2026-09-15

The following is the **pre-launch audit record**, before the two-run review
above. Its selected `glm-5.3` / shared-pool readiness conclusion must not be
applied to the later Mimo topic's generated scientific evaluator.

**The current candidate is ready to start the user-confirmed MERC-causal topic
with the reviewed Host binding. Formal startup belongs in Claude Code through
`/autoresearch init`; this audit has not started the topic.** No additional
Start now instruction is required in the Codex audit conversation. The final
startup choice belongs to the existing Claude Code flow. The full repaired candidate's offline qualification now
passes **5,581 tests, with 25 explicit opt-in skips and zero failures/errors**.
The approved shared-GPU G4, G5-local, G5-common and G5-resource recheck evidence
remains applicable. The original G5-resource FAIL and its charge are retained.
This is readiness for a fresh run on the selected configuration, not a claim
that every optional compatibility lane or historical-release recovery passed.

The user confirmed the six-file `profiles/local/MERC-causal` configuration,
including its omitted USD caps and existing cadence/time limits. Its only
authorized edit remains Web port **8766**. Production `profile-check` again
admits it without blockers or warnings. A disposable empty managed Host has
now actually started with the reviewed model/authentication, literature,
GPU inventory and existing-pool settings, then stopped cleanly. Only its
state root and namespace differed from the formal arguments. No Agent,
experiment or GPU reservation was created by that smoke test.

On explicit user approval, `loginctl --no-ask-password enable-linger gray`
succeeded and `Linger=yes` was read back. The user manager, shared pool and
existing research Host retained their original PIDs and active sessions.
This establishes the user-manager lifetime policy beyond the last logout;
an actual logout/reboot or a 70-day soak was not performed. The confirmed
shared-pool binding and independent namespace remain unchanged.

The supplied OpenAlex credential has real HTTP-200 authentication and metadata
evidence. A new bounded sample also completes the production search-to-PDF-to-
Poppler path; inaccessible PDFs and the initial overly early query's coverage
error remain recorded. Deep ERC cache checks find no alignment failure but
retain **WARN: source is empty**. Two bounded CPU forwards consume real cached
inputs successfully; they do not establish scientific accuracy or causality.

All four actual model samples still total **12.996258 USD**, including the
original failed resource sample. Total metered OpenAlex usage is now
**0.009 USD**, drawn from free allowance with zero prepaid/cash drawdown.
Conservatively including it gives **13.005258 USD** against the unchanged
**20 USD** cohort ceiling, leaving **6.994742 USD**, with no unknown
reservations. The new directory does not reset the cohort. These are existing
Host/provider reports, not an independently verified upstream invoice.

The authorized interaction repair changes one runtime file and three existing
test files. This final verification changes only current documentation and
ignored logs, plus the separately approved linger policy. Existing runs,
source-project services, target/data and saved bundles remain untouched.
Nothing was committed, pushed or released.

### Candidate and evidence reuse

HEAD is `6bd5bb910c8b9385f828f530cf634f3bbd19bbda`, on `main`. The candidate is
**that HEAD plus the current working tree**, not HEAD alone: `.gitignore` and
`README.md` were modified, and the imported package, tests, Profiles, hooks,
Skills, packaging and guides were untracked. The initial read-only audit changed
only this qualification document outside ignored validation outputs. The later
authorized repair changes `hypoloom/interaction_host.py`, three existing test
files, the ignored local Profile's Web port, and current documentation. This
working tree, rather than HEAD alone or the pre-repair wheel, is the candidate.

Before the repair, ordinary byte comparison found all 131 packaged
runtime files equal across the current wheel and checkout, with no omissions;
the earlier installed CPU run's private bundle also had the same 131 files.
All 527 relevant files in the current source archive matched the checkout.
The earlier extracted source archive differed only in the removed minimal
starters and related Profile test/guide/operator-Skill changes. Runtime/test
file timestamps, the original commands, JUnit case identities, environment
metadata and current collection were checked together. Timestamps alone were
not treated as candidate identity. No source-project qualification was reused.
The archived pre-repair wheel and earlier saved bundles do not contain this
fix; their passes remain evidence for unchanged paths, not repaired binaries.
The disposable wheel-test Conda environment was subsequently updated from the
new wheel. Its installed files and new managed run bundle match all 131 current
runtime files; the new installed CPU closure is recorded below.

The initial Hypoloom full suite recorded **5,562 passed / 43 skipped** in 5,605
cases, followed by an 18-case browser supplement. Pre-repair collection had
5,602 cases after public-template parameter changes. Those original records,
commands and identity comparisons remain retained at their original paths;
their totals are not a new run of the repaired tree.

After focused repair checks and the paid follow-up, the final frozen runtime
candidate was tested once through the existing eight G1 groups. All **346
current test files** occur exactly once; JUnit contains **5,606 distinct case
identities: 5,581 passed / 25 skipped / zero failed or errored**, in 642.3 s
wall time. This freshly includes the browser cases and four new regressions.
The six opt-in cases with separately valid passing evidence are the rootless
ENOSPC case, two shared-native G4 cases, and G5-local/common/resource. Thus
**5,587 current distinct cases have passing evidence and 19 remain unexecuted**.
This union does not erase the original failed resource sample or turn skips
into passes. It is not a substitute for the stage-specific limits below.

The old 63-failure initial focused probe and ordinary-venv CPU timeout remain
in `focused.xml`, `focused.log` and `wheel-smoke.log`; later passing samples did
not replace them. The complete Conda installation addresses the documented
venv interpreter-binding limitation. This audit's initial sandbox-denied
socket/bwrap/NVML probes and incorrectly formed absolute-path `profile-check`
command are retained separately from their successful host/corrected-command
checks. None is silently counted as a pass.

### Observed stages

| Stage/check | Current evidence and limits |
|---|---|
| G0 | The common-acceptance/interaction repair is now authorized and implemented. Four new real CPU cases reproduce both directions of the old predicate error and pass after repair. The original read-only reproduction, four regression failures and a subsequent test-import failure remain retained. The corrected focused selection passes 89/89. Other admission/configuration findings remain separately documented. |
| G1 static/import/configuration | Final full repaired candidate: **5,581 passed / 25 skipped / 0 failures/errors**, 642.3 s, all 346 current test files and 5,606 distinct cases. This includes real native CPU and installed-browser scenarios with remote/GPU opt-ins disabled. Current compileall, Ruff `F601,F602,F821,F822,F823` and Git whitespace pass; the unchanged environment's `pip check` remains applicable. The earlier 89 focused and 156 adjacent passes remain repair evidence, not extra distinct cases. |
| G1 package/install | The repaired wheel is built without dependency downloads and installed into the existing disposable complete Conda environment. All **131 runtime files** match checkout, wheel, installation and the fresh managed bundle. New installed CLI/MCP/user-systemd CPU closure passes in 4.62 s: **2 Agent +2 Process**, one outcome, clean Oracle, COMPLETED, Host exit 0, socket removed and transient unit collected. The smoke Profile is a component fixture, not a production topic. Source distribution details are retained with the package comparison below. |
| G2 | All six existing G2 test files execute again in the final G1. The earlier **169-case selector** and enabled rootless **16 MiB tmpfs ENOSPC test** remain separately retained; persistence, Oracle, migration and bundle code is unchanged. Same-source publication and bundle/session recovery are exercised. There is **no retained Hypoloom release cohort**; fixtures and disposable runs do not establish historical compatibility. |
| G3 | All seven existing selector scenarios pass again within final G1, as does native three-expert combined interaction. Independent inspection of these eight real runs finds **251 Agent +22 Process Attempts all CLOSED**, 21 canonical outcomes and clean Oracles; all 244 distinct recorded process groups are absent. The two LOST, two FAILED and one CANCELLED Attempt are intended fault/cancel observations. The TUI's cancelled run is not labelled COMPLETED. Earlier seven-scenario and repaired native results remain retained. See protocol and interaction limits below. |
| G4 | After explicit authorization, the existing selector passed **8/8, zero skipped**, 306.18 s: two real CUDA cases plus six offline helpers. Four Hosts completed **72 Agent + 8 real GPU Process Attempts**, all 80 CLOSED/SUCCEEDED, with eight canonical outcomes and clean run/pool Oracles. Actual native resource Skills, measurements carried into subsequent cycles, same-card overlap, pre-Process capacity waiting and reclamation were observed. See scope and cleanup below. The earlier CPU-only resource-Skill rehearsal remains separate evidence. |
| G5-local | **1 passed**, 483.00 s: approved `glm-5.3` / settings-file authentication, 11 successful Agent Attempts, one successful real CPU Process, canonical metric **0.875**, published final report and clean Oracle/cleanup. Reported/accounted usage **2.693327 USD**, 44 turns, no unknown reservations, within its 14 USD allocation. |
| G5-common | **1 passed**, 540.59 s, on the repaired working tree: Grounding authored and tested the actual common parser, the expert read that accepted source, and the real CPU outcome satisfies the common accuracy criterion **0.875** and both validity checks. Eleven Agent Attempts and one Process are CLOSED/SUCCEEDED, with an accepted final report and clean Oracle/cleanup. Reported/accounted usage **3.831384 USD**, 49 turns, no unknown reservations, within the 7 USD allocation. This one-expert sample does not cover paid cross-expert interaction or the MERC scientific result. |
| G5-resource | **Original sample: 1 failed**, 604.63 s, at `test_real_shared_gpu_g5.py:624`; cleanup was chained into the wrapper CPU check. Its **3.553527 USD** charge, 52 turns and original failure remain retained despite successful CUDA execution and independent cleanup. **Authorized recheck: 1 passed**, 485.22 s, with unchanged code/instructions/strict recognizer. The standalone wrapper check passes; native resource queries, exact UUID binding, CUDA checksum **6**, measured-history reuse and an 8,957-byte final report are observed. Eleven Agent Attempts and one Process are CLOSED/SUCCEEDED, the run is COMPLETED, both Oracles are clean and the private pool is released/closed. New reported/accounted usage **2.918020 USD**, 33 turns, no unknown reservations, within its 6 USD allocation. No further paid retry or notification occurred. |

### Environment and startup conditions

Control Python is `.conda/envs/hypoloom/bin/python`: complete Conda Python
**3.12.14**, SQLite **3.53.4**, PyYAML 6.0.3, filelock 3.32.4, wsproto 1.3.2,
jsonschema 4.26.0, pytest 9.1.1, Textual 8.2.8, websockets 16.1.1, Ruff 0.16.7
and build 1.6.1. Its resolved interpreter stays inside the prefix and imports
`hypoloom` from this checkout. The separate `.local/wheel-runtime` has the same
Conda Python/SQLite builds and imports the installed package from site-packages
with `-I`, outside the checkout. No extra dependency installation was needed.

The shell's unqualified `python` instead resolves to `/opt/miniconda3/bin/python`
with Python 3.12.4 / SQLite 3.45.3: **do not use it to launch Hypoloom**. Linux
5.15, Claude Code **2.1.270**, bubblewrap **0.4.0**, Google Chrome
**149.0.7827.155**, and Poppler `pdfinfo`/`pdftotext` **0.86.1** were observed.
The native CPU tests prove actual Claude/bwrap/local-broker execution, beyond
version/help output. All 18 browser cases pass again in the final G1 with the
installed browser. Actual online OpenAlex and production Poppler retrieval
results, including failures, are recorded below.

User-systemd is reachable and a real transient Hypoloom service was observed
active, then inactive/collected with MainPID 0 and ExecMainStatus 0 after CPU
completion. The user manager reports `degraded` due to pre-existing unrelated
failed units, which were left untouched. The original `Linger=no` condition
was resolved at 2026-09-15 00:17 +08:00 by the explicitly approved enable-linger
command. `Linger=yes` and unchanged existing service PIDs are verified. The
actual managed empty-Host test and the scope of the lifetime evidence are
recorded in the final startup section below.

There are no inherited Hypoloom live opt-ins or model-auth environment keys.
The conventional user settings file `~/.claude/settings.json` is a regular,
non-symlink mode-0600 file. The existing resolver reports gateway auth, default
`glm-5.3`, valid mappings `haiku -> glm-5.3-flash` and `fable -> glm-5.3`, and
invalid `sonnet`/`opus` mappings. No credential bytes were printed. The real
managed launcher makes its intended private mode-0600 settings copy; the
disposable Host's copy was removed after shutdown, leaving the source intact.
This source and `glm-5.3` were subsequently **approved for the bounded G5
samples below**; actual local/common/resource closures succeeded. The original
resource sample remains failed alongside its successful recheck. The supplied
OpenAlex file is absolute, regular, non-symlink, owned by UID 1001 (the caller),
mode 0600, with a mode-0700 parent. The actual reader accepted its format and
both Poppler executables passed preflight. That original admission-only check
reported `network_probe_performed=false`; the later real network results
establish the selected remote path separately. Interactive OAuth, help output
and local fixtures were not substituted for actual authenticated calls.

The migrated package/launcher/public-template scan found no old repository imports, old
`PROMETHEUS_*` configuration, `state-vnext` dependency or machine-specific
runtime paths in the migrated source. Machine paths in this audit describe
observations only; they are not defaults installed into configuration.
The user-supplied ignored local Profile does retain explicit
`PROMETHEUS_TELEGRAM_BOT_TOKEN` / `PROMETHEUS_TELEGRAM_CHAT_ID` references, with
Telegram disabled. The explicitly selected OpenAlex key also resides in a
`prometheus`-named user configuration directory. These are local user bindings,
not automatic imports or discovery of an old repository; neither was renamed.

### Interaction, context and completion evidence

- The selected three-round debate/restart test explicitly writes **ACI 14**;
  it must not be described as the default ACI 19 path. Separately, the unchanged
  `test_three_experts_native_leader_then_debate_six_cpu_and_final_report` has
  applicable passing evidence under the current default: three experts, six
  stateless Observer sessions, one Leader, six debate statements, one Judge,
  six real CPU experiments (five successes and one clean refutation), both
  designated next actions and the final report. This is scripted-provider
  compatibility evidence, not paid scientific judging quality. Existing
  CPU tests also cover concurrent experts without a global round barrier.
  The now-passing shared-GPU selection exercises two Hosts; it does not by itself
  prove a single Host running the exact future three-expert CUDA Profile.
- The Judge receives the exact file containing all six statements. Native
  reads and tests verify complete statement/source identities; predecessor
  debate inputs project the actual typed response without recursively copying
  earlier requests and remain below the native 256 KiB Read limit in this
  scenario. Large Judge inputs stay complete on disk without duplicate prompt
  inlining. Existing validators and source records remain the authority.
- Observer coalescing includes only closed reports present in its captured
  input. Existing tests cover reopening, late reports and failed Observers.
  Debate throttling requires both the configured **1,800 s** cooldown and
  **two new outcomes** after a completed debate, using durable event times;
  those regression cases passed in the applicable full suite. The first
  debate is not artificially required to wait 1,800 s.
- **Automatic native session compaction was unobserved in these bounded
  qualification samples.** The later real-topic observations above are
  separate evidence. The fresh G3
  artifacts contain 138 transcripts / 6,774 parsed records with no observed
  compaction event. The two paid G5 samples add 22 complete transcript
  archives / 1,594 records, also with no observed compaction marker. Input
  projection, persistent memory and transcript byte
  caps are not evidence that native compaction ran successfully. Existing G5
  local/common canaries also explicitly exclude Observer/Debate and do not
  guarantee a compaction event.
- `DISPATCH PAUSED (Host error)` is a dispatch exception quarantine, not
  completion or an ordinary resource wait. Current tests verify preserved
  original diagnostics, continued peer/control service and cancellation
  cleanup; database/integrity failures still halt. Durable ACTIVE and a live
  Host are separate facts. Terminal completion requires actual terminal state
  and cleanup; the TUI hides the pause badge for terminal states.
- The fresh TUI test proves `q` leaves the real CPU process and Host running
  without a CANCEL, reentry works, and `x / End run` submits exactly one
  accepted CANCEL. The CPU Attempt becomes CANCELLED, every Attempt closes,
  the Host exits 0 and `control.sock` disappears.
- The paid G5-local native archives contain actual Read, Bash, Write, Skill,
  resource-query, experiment-list, submit and wait calls, including the
  literature-summary, resource-planning and final-report Skills. The existing
  seeded-repair validator observed the initial failing local test, repair and
  subsequent pass in the DESIGN session. The real CPU result is 0.875 and the
  published `FINAL_REPORT.md` is 6,378 bytes. This is the existing one-expert,
  one-cycle local-metric fixture; paid debate/Judge and common-goal acceptance
  were not exercised. Its token setting is `observe`, not a hard per-node
  token ceiling; the existing aggregate accounting reports 1,424,199 tokens
  across 44 turns.
- The resource sample's failed assertion precedes some later closure assertions
  in that test. Separate read-only inspection reused the existing resource-trace,
  measured-history, Agent-identity, charge-threshold and Oracle validators. It
  verified the native resource Skill/query results, the post-wait measurement's
  exact own Attempt identity, 2,048 MiB admission, 362 MiB owned resident peak,
  the approved UUID, CUDA checksum 6 and the 8,583-byte submitted final report.
  These are supplemental observations; the original paid pytest result remains
  FAIL. No assertion was bypassed or changed to claim a passed canary.

### Findings and remaining inputs

| Severity | Reproduction, impact and smallest correction |
|---|---|
| High / repaired: common acceptance prematurely closes interaction | The original `interaction_host.py:144` predicate treated any local SUPPORTED assessment as closing, suppressing Observer/Leader/Debate even when common measurements failed a strict threshold. It also missed common success when the local assessment was REFUTED. The retained read-only prefix reproduction and four new real CPU regressions fail on the original code. The authorized fix at `interaction_host.py:144–155` reuses `SharedAcceptanceHost.assessment_qualifies`, the same accepted-evidence decision used by workflow goal handling; ordinary local-metric behavior remains covered. The 89-case corrected focused run passes, including local-only evidence, equal threshold, invalid evidence and common success despite local refutation. Nonqualifying cases observe an actual Observer Attempt. No terminal GOAL_MET reducer, schema or new authority was introduced. Native common acceptance and combined interaction remain separate scenarios; see repair evidence below. |
| Launch preparation resolved; formal Start authorization outstanding | The user confirmed the existing Profile, including invocation `max_cost: null`, observe-only tokens, omitted total/per-expert USD caps, 70-day wall limit, 30-hour Process limit and omitted interaction cooldowns. Do not invent caps or repeatedly request confirmation. Nodes inherit the explicit Host model (`workflow_host.py:2175`). The concrete glm-5.3/settings-file binding, exact two-UUID inventory, existing-pool socket/UID and independent namespace pass the existing argument and budget checks and the actual disposable managed-Host smoke. User-approved linger is now enabled. `safety.yaml:7` uses auto only within Host-authorized GPUs and does not select a pool. The 20 USD approval is the qualification cohort ceiling, not an implicit topic cap. Formal Start remains unauthorized. |
| Original qualification failure / recheck resolved: resource wrapper-check command shape | Retained DESIGN transcript `agent-996de133b4c741b2b73944ce679e3228-1` first records a real `json.dumps(..., flush=True)` TypeError, then the model's repair and successful wrapper CPU checks. Its successful command starts `rm -f /tmp/test_result.json && ... python /workspace/experiment.py --local-check`. The fixture at `test_real_shared_gpu_g5.py:134–137` requires a separate wrapper-test Bash call, while `_has_wrapper_local_check` at lines 399–449 rejects this extra cleanup chain; line 624 fails. The separately authorized recheck uses `HYPOLOOM_TARGET_ROOT=/workspace HYPOLOOM_RESULT_PATH=/tmp/test_result.json python experiment.py --local-check` in a successful standalone call and passes the unchanged qualifier. No runtime/validator edit was needed. Keep the first FAIL and its 3.553527 USD charge; the new PASS and 2.918020 USD are separate observations, not a guarantee that every future model call follows the format. |
| Medium / resolved for preparation: occupied Web port | The original `127.0.0.1:8765` is occupied by existing Python PID 3786571. With the user's authorization, only `MERC-causal/notify.yaml:13` changes to port **8766**. Read-only listener inspection, a temporary successful bind/close and a fresh production `profile-check` pass. No Web server or formal Host was started, and the old listener was untouched. Availability is a preparation observation, not a reservation until a future Host binds. |
| Medium: environment gateway address is dropped | With dummy `ANTHROPIC_API_KEY` and `ANTHROPIC_BASE_URL` set, intercept `workflow_host.serve` while calling the ordinary Claude `serve` CLI without a settings file: only the key reaches `provider_environment`. The managed helper's `_minimal_environment(include_api_key=True)` also omits the address. See `cli.py:490–497`, `.claude/skills/autoresearch/scripts/manage_host.py:256–276`, and the default endpoint in `claude_agent_host.py:563`. Contrary to `SETUP.md:34–35`, this environment-auth path cannot select the intended gateway and falls back to the default Anthropic endpoint. Preserve the explicit transport separation and carry the selected allowlisted base URL through both existing paths, with focused forwarding regressions. The approved settings-file path carries its own address and is unaffected. No runtime change or external request was made. |
| Medium: OpenAlex file-mode requirement is not enforced by its reader | A fresh dummy key file with mode 0644 is accepted by `_explicit_api_key(None, path)` at `literature.py:2701–2735`. The reader checks regular-file/symlink/type/size constraints but does not check the documented 0600 permission requirement. This must not be represented as a runtime-enforced permission guard; the init/operator check remains necessary. Add the existing required permission predicate to this reader and a focused permissive-mode rejection test. No real credential was read or exposed by this reproduction. |
| Medium: model placeholder passes admission | Copy a public template, bind valid target/runtime/read roots, leave `experts.defaults.model: REPLACE_WITH_PINNED_MODEL`, then run `profile-check NAME --profiles-root PARENT --cpu-only --run-id ID`. Observed exit 0, `admitted=true`, no blockers/warnings. `profile_v4.py:72–73` only validates model text; `claude_agent_host.py:3422` accepts this non-alias string. A partially prepared Profile can appear ready and later attempt an invalid remote selector. Reject the documented unresolved sentinels through the existing model/Profile validator and add a focused negative regression; do not add a separate readiness mechanism. No code fix was made. |
| Coverage limits | No retained Hypoloom release cohort; no paid debate/Judge sample or qualified automatic compaction. Common-goal and resource canaries pass with one expert each; the first resource sample remains failed. Shared G4 and native three-expert CPU closure provide complementary evidence, not a single-Host three-expert CUDA run of the supplied Profile. ACI 14 debate restart and ACI 19 combined interaction are distinct. These remain unexecuted compatibility scopes; the existing promotion rules do not require every optional sample for a fresh run on this selected binding. Scientific acceptance must still be earned during research. |
| Operational conditions addressed for the selected launch | The shell's default Python/SQLite is unsuitable; the prepared launcher explicitly uses the complete project Conda environment. The user-approved linger command changed only gray's lifetime policy to `Linger=yes`, with unchanged active service PIDs. Full logout/reboot and long-duration endurance were not tested. |
| Low: stale test-module prose | `test_real_shared_gpu_g5.py:10` says the allocation defaults to 6 USD, but its live entry at line 896 requires `HYPOLOOM_RESOURCE_G5_ALLOCATION_USD`. Missing input has no default there. Align the docstring with the existing explicit-input behavior; retain the fail-closed allocation check. The qualification guide already requires the explicit input. |

Only `multi-expert-en` and `multi-expert-zh` are public. Both contain exactly
six regular YAML files and preserve budget/permission/expert-ID controls across
languages. Production `profile-check` accepted the two disposable fully bound
copies without starting research; it rejected both untouched templates for
unbound target/runtime/read-root paths. The separate unresolved-model probe
above is retained as an admission defect, not counted as a successful negative
check.

The supplied topic does not need a newly invented objective, threshold, expert
roster or another A/B/C feature-boundary decision. Its existing configuration
values are user-confirmed. Concrete Host model/authentication/resource bindings
are now prepared, existing-pool reuse is confirmed and linger is enabled.
At this preparation stage, the final startup choice belonged to Claude Code.
The later real-run review above supersedes this stage's current-status claims. The default
public `metric: null` form can stop on
one accepted SUPPORTED result, but MERC-causal instead uses
`evaluation: grounding`: one candidate must satisfy the complete common
acceptance. The interaction repair preserves that distinction.

### Supplied MERC-causal Profile and OpenAlex follow-up

Production command (read-only; `--allow-gpu` here checks the admission branch,
does not launch or reserve a GPU):

```bash
./.conda/envs/hypoloom/bin/python -B -m hypoloom.cli profile-check MERC-causal \
  --profiles-root /data/Gray/Project/Hypoloom/profiles/local \
  --run-id MERC-causal-preflight-20260914 --allow-gpu \
  --literature-adapter openalex \
  --openalex-api-key-file /home/gray/.config/prometheus/openalex.key \
  --literature-pdfinfo /usr/bin/pdfinfo --literature-pdftotext /usr/bin/pdftotext \
  --literature-timeout 15 --literature-max-response-bytes 2097152 \
  --literature-max-pdf-bytes 16777216 --literature-max-pdf-pages 2000
```

Observed exit **0**, `admitted=true`, no blockers or warnings, in 0.30 s.
At that read-only check, the six regular YAML files were unchanged and
Git-ignored. After the user's configuration confirmation, only `notify.yaml`
port 8765 changes to 8766; the same production check passes again, exit 0 in
0.29 s, with a fresh preflight-only run ID and no blockers or warnings. The first
operator command incorrectly used `-m hypoloom`, which has no `__main__`; its
exit-1 stderr and command are retained separately from the corrected CLI call.
The literature preflight accepts the file credential and Poppler configuration
only. It reports 15 s request timeout, 60 s search wall bound, two retries,
2 MiB metadata/text and 16 MiB PDF bounds, plus an unauthenticated Semantic
Scholar DOI fallback preflight (5 s, 64 KiB). That earlier admission check made
no network requests. The authorized online follow-up below now establishes key
validity, remote quota/cost and real metadata retrieval, with explicit PDF
coverage limits. No key contents were printed, logged, copied or moved.

| Item | Observed configuration and evidence |
|---|---|
| Goal and completion | MELD five-seed mean weighted-F1 strictly >0.6674 and IEMOCAP strictly >0.7305, seeds 0–4, one shared core candidate; incomplete/duplicate seeds and combined best results from different candidates are excluded. All ten final checkpoints require future-feature, future-speaker and future-participant-set perturbation checks with maximum logit difference <=1e-6. The commission asks Grounding to implement and locally test common acceptance, rather than accepting self-reported success. |
| Scientific reference scope | Local Poppler extraction succeeds on both supplied papers. MultiEMO Table 2 (PDF page 8) reports MELD 66.74; AO-FL Table II (PDF page 7) reports IEMOCAP w-F1 73.05. The ERC table agrees. These are reference thresholds, not verified equivalent causal-protocol baselines, statistical significance or current public SOTA. Bounded online retrieval checks are recorded separately; no training or scientific qualification was performed. |
| Target and data | `/data_ssd/Gray/Project/ERC` is a clean Git checkout at `b4590893f756b9f09e2ff2bcbdcf1b784322801c`; admission binds that commit. Both raw-data roots, C_multiemo caches, split manifests, IEMOCAP validation list and referenced papers exist and are readable. Existing `SplitAssignment.validate` and `FeatureCacheReader.validate_against` pass; split IDs exactly cover cache IDs without duplicates. IEMOCAP validation IDs and Session1–4/Session5 partition agree. MELD train/val/test dialogue counts are 1038/114/280; IEMOCAP 108/12/31. |
| Cache limits | Existing `audit_cache_for_method(..., deep=True)` now reads all 1,432 MELD and 151 IEMOCAP dialogues and finds no alignment/schema failure. Both retain **WARN: source is empty**, as in the earlier shallow check. This does not establish feature causality. The Profile expressly accepts fixed cached inputs and their provenance/context uncertainty; no cache regeneration or new feature-level acceptance condition is introduced. |
| Experiment environment and integration | `/home/gray/miniconda3/envs/erc-cu128` is complete Conda Python 3.12.13 / Torch 2.8.0+cu128, NumPy 2.5.2, h5py 3.16.0; `pip check` passes. Plain environment discovery finds no installed `merc`. With the reviewed checkout's `src` explicitly on PYTHONPATH, real imports of `merc.causal.train_causal`, its audit, splits, Torch and h5py succeed; CUDA remains uninitialized. Future experiment wrappers must import the private candidate's source and map read-only cache paths through the existing workspace read-root projection, not silently import an old/global checkout. |
| Acceptance integration still to be done during research | Existing ERC `src/merc/causal/audit.py:133–155` excludes speaker from perturbed inputs and only randomizes floating feature tensors. `train_causal.py:243–276` writes single-seed metrics and checkpoints; it does not supply this complete ten-checkpoint/three-perturbation acceptance evidence. Grounding must adapt the existing interfaces in its private workspace and test the common evaluator before any success verdict. This is a declared research task, not a requirement to prewrite a topic evaluator during this audit or modify the read-only target. |
| Experts and language | Three independent full-goal experts: machine learning, experiment engineering and affective computing. Shared effort high; up to two forks and two hypotheses per round. Prompts/reports zh-CN, code comments en. No exact model is present in the Profile; omission inherits the chosen Host model, and admission does not prove a particular remote model binding. |
| User-confirmed limits | Run wall limit **1680 hours (70 days)**; each Process **1800 minutes (30 hours)**. Each Agent invocation: 240 turns, 3000 s, 1,000,000 observed tokens, 1,310,720 output bytes, no USD ceiling. Total experiments 360; role caps 20 Grounding +110 ML +110 engineering +100 affective. Concurrent Agent/Process/review limits each 3. No total/per-expert dollar caps or run-wide turn/token caps are supplied. These values are confirmed and preserved. Linger is now enabled for the desired user-manager lifetime; no 70-day endurance claim follows. |
| User-confirmed interaction and context | Leader is enabled after one closed expert round; Debate is enabled for three rounds. This local Profile omits `min_interval_seconds` and `min_new_outcomes`; `interaction_host.py:157–160` applies **0 / 0**, with no cooldown/new-outcome deferral beyond ordinary eligibility checks. These values are confirmed; template settings are not inherited. Retention is retain, transcript/memory/plugin bounds 128/8/1 MiB. These settings and token observe mode do not establish automatic context compaction. The applicable interaction predicate bug is repaired and separately verified below. |
| GPU and notifications | Up to one GPU per experiment, 8192 MiB workload floor and 4096 MiB reserve. `gpus:auto` only narrows to the future Host's explicitly authorized inventory; no topic pool binding was created or changed. Telegram is disabled and its two legacy-named environment references are unset in this audit process; no token/chat value is in the Profile. Web is enabled on the user-authorized replacement **127.0.0.1:8766**, with a successful temporary bind and admission check. No notification was sent. |

The original common-interaction probe is a production-predicate reproduction over a
prefix of retained current-ACI G3 evidence, with the supplied interaction flags
selected only in memory. It is **not** a new end-to-end research run or an extra
case in the 5,602-case collection. Its original failed assertion and all earlier
qualification failures remain retained. The authorized minimal repair and its
focused/native CPU regressions are now complete. The subsequently authorized
bounded `g5-common` and separately recorded `g5-resource` recheck both passed
within their concrete allocations and the same cohort accounting. A common
one-expert canary still does not prove paid multi-expert Debate/Judge or the
exact MERC scientific outcome.

### Authorized interaction repair and verification

The runtime change is limited to `InteractionHost._closing`. It retains the
shared-goal guard and passes accepted assessments, linked evidence and canonical
outcomes to the existing `SharedAcceptanceHost.assessment_qualifies` decision.
There is no new reducer, schema, migration or compatibility layer. The tests
extend the existing shared-acceptance fixture and the existing native common
scenario, and give the existing stored Host seam its production acceptance
helper.

All commands use the explicit complete Conda Python from this checkout, fresh
isolated roots, and cleared `HYPOLOOM_REAL_*` flags. The two native scenarios
use the installed Claude CLI, bwrap, production Host/broker/Process and loopback
scripted providers; they make no paid model or external literature requests.

| Check | Observed result |
|---|---|
| Original-code regression | **4 failed**, 11.93 s. Real CPU outcomes reproduce local-only support, an equal strict threshold, invalid common evidence, and common success despite local refutation. The original failures remain in `red.log/xml` and `red-evidence/`. |
| First focused run after runtime repair | **88 passed, 1 failed**, 49.11 s. A new top-level test-fixture import broke the existing recovery test's plain-Python child import. This was a test-only regression; move that import into the applicable fixture branch. Preserve `focused.log/xml`. |
| Corrected focused run | **89 passed**, 51.55 s; shared acceptance, interaction, grounded goal policy, recovery and control Oracle. The three nonqualifying new cases produce real Observer Attempts; qualifying common evidence closes interaction despite local REFUTED. |
| Adjacent regression | **156 passed**, 22.47 s; debate Host, Profile runtime consumers, expert interaction, interaction Oracle, public Profiles and documentation. |
| Native CLI CPU integration | **2 passed**, 341.01 s. Common Grounding authors/tests one accepted evaluator; the first locally supported but nonqualifying result does not close interaction. Separately, three experts complete Leader, six Debate turns, Judge, six CPU experiments, designated follow-up work and the final report. The existing native common fixture disables interaction; these are complementary scenarios, not a claim of native common acceptance plus a full debate in one run. |
| Static/environment/collection | compileall, Ruff `F601,F602,F821,F822,F823`, `pip check` and Git whitespace pass. **5,606 cases collected**; collection does not execute opt-in lanes. |
| Local Profile | User-confirmed six-file contents retained except Web port **8766**. Temporary bind/close and production `profile-check` pass; no Host/run or network key probe is started by admission. |
| Installed package | Fresh wheel built and installed with no dependency downloads. Reuse the existing managed-wheel smoke with only its log destination changed: installed CLI/MCP and actual user-systemd CPU closure pass, **2 Agent +2 Process CLOSED/SUCCEEDED**, one outcome, clean Oracle, COMPLETED, MainPID 0, ExecMainStatus 0, unit collected and socket gone. Ordinary byte comparison checks all 131 runtime files against checkout, installation and this new bundle. `package.json` also records source-distribution contents. |

Independent read-only inspection uses the existing Oracle on the four corrected
CPU regression runs and two native runs: **six COMPLETED runs, 166 Agent +
12 Process Attempts, all 178 CLOSED/SUCCEEDED**, with clean Oracles. Across
all five fresh test roots, all **149 recorded process groups** are absent;
the native common test's six additional parser-smoke process groups are also
absent. No matching live process, socket or private mount remains. No existing
run, GPU pool, research target/data or service was modified.

The additional installed-wheel run also closes cleanly: both recorded real
process groups are absent; the two fake-Agent Attempts have no owned process
group. Its actual managed Host has exited and its transient unit is collected.
This brings the seven inspected completed runs to **168 Agent +14 Process
Attempts, all 182 CLOSED/SUCCEEDED**. The existing disposable wheel-test
environment is the only updated environment; old run bundles remain untouched.

An initial package inspection incorrectly assumed the in-process native fixture
Hosts had `state/bundle` directories. Its failed path comparison is retained
in `package-initial-path-probe.json`; those fixtures import the checkout.
The corrected comparison uses the actual managed CLI-created bundle and passes.
No fixture was rerun or package requirement weakened to hide that inspection
error.

Detailed commands, original red/failed/corrected JUnit logs, static checks,
cleanup inspection and retained native/regression evidence are under
`.local/qualification/interaction-fix-20260914/`. `repair.diff` is an ordinary
review diff against the existing preflight source archive. No new qualification
authority or frozen-candidate mechanism was added. This repair adds **0 USD**
in model charges; cohort reported/accounted usage remains **6.246854 USD**.

### Additional paid batch — completed within the existing ceiling

The earlier approval and 20 USD ceiling remain in force for the qualification
cohort. The user explicitly authorized the additional common sample and resource
recheck below. Both samples passed; the original resource failure is retained.
Profile values do not need confirmation again.
Use the same complete Hypoloom Conda Python, Claude CLI 2.1.270, **glm-5.3** and
regular mode-0600 `/home/gray/.claude/settings.json` gateway authentication.
Key contents must never enter commands, logs or the Profile.

| Order | Concrete test and limits |
|---|---|
| 1 | Existing `scripts/qualify.py g5-common`, once, CPU only: **7 USD allocation**, 900 s run +30 s drain, 240 aggregate turns, at most 24 turns /300 s /2 USD per Agent invocation. Carry exact prior 6.24685399999999991 USD and use the existing canary cumulative-limit input 13.24685399999999991 USD to bound this sample to 7 USD within the outer 20 USD cohort. |
| 2 | Existing `scripts/qualify.py g5-resource`, once after re-reading prior charges/reservations: **at most 6 USD**, 1200 s run +30 s drain, 384 turns, 32 turns /300 s /2 USD per Agent; one 30 s CUDA experiment, 64 MiB tensor and 256 MiB allocator cap (context excluded). Exact UUID **GPU-91a3b0a9-d48b-1503-2893-33153c3fe405**, runtime `/home/gray/miniconda3/envs/erc-cu128`, fresh private pool, existing 512 MiB reserve and at least 6144 MiB available at admission. No existing pool replacement. Keep the existing standalone wrapper-check instruction and strict recognizer; preserve the first failed sample. |

The two allocations total 13 USD; together with the pre-batch accounted usage
their planned ceiling was **19.246854 USD**, below the unchanged 20 USD limit.
At the end of that paid follow-up, model usage was **12.996258 USD**, or
**12.999258 USD** including the then-observed free OpenAlex usage. The final
startup verification below updates literature usage to **13.005258 USD**
conservatively accounted overall; it adds no model charges. Current
unknown reservations are empty; if new usage is unreported, retain it as
unknown/reserved and stop dependent launches until it is reconciled. The
existing accounting is CLI/Host reported usage, not an independently verified
upstream invoice; an in-flight response may overshoot, never funding another
launch. No directory change resets the budget, and no automatic further retry
is included. These model canaries do not validate paid multi-expert debate,
automatic compaction or the actual MERC scientific result. OpenAlex key/quota
are checked separately below.

### Online literature and prospective formal Host binding — 2026-09-14

Outputs from this earlier follow-up are in ignored
`.local/qualification/readiness-20260914/`. Before the common launch, ordinary
byte comparison found no differences from the repaired source archive. The
resource launch separately checks all runtime, test, Skill and script bytes;
no code or fixture instruction was changed for either paid sample. Current HEAD
still denotes a dirty/untracked working-tree candidate, not a committed release.

**Common acceptance:** the existing `g5-common` selector passed **1/1**,
540.59 s. Eleven Agent Attempts and one CPU Process are CLOSED/SUCCEEDED,
the run is COMPLETED, the existing Oracle is clean, and all twelve recorded
process groups are absent. Grounding authored and tested the actual common
parser; the expert read the same accepted source and submitted GOAL evidence.
Observed accuracy is **0.875**, both `complete_pairs` and
`computed_accuracy_matches` are true, and the final report is accepted.
Native transcripts contain actual Read/Bash/Write/Edit/Skill, resource-query,
submit and wait calls; the accepted report has 8,059 bytes. No compaction event
was observed in 776 parsed transcript records, with no parse errors. This is
an observation, not a passed automatic-compaction test.
Reported/accounted usage is **3.83138399999999992 USD**, 49 turns and
2,088,612 observed tokens, with no unknown reservations. This remains the
existing one-expert compatibility fixture, not MERC training or paid debate.
Original command, JUnit, full retained state/transcripts and independent cleanup
are `g5-common-command.json`, `g5-common.log/xml`, `g5-common-evidence/` and
`g5-common-cleanup.json`.

**Resource recheck:** the unchanged `g5-resource` selector passed **1/1**,
485.22 s. The model's standalone wrapper CPU check satisfies the existing
recognizer. Its admitted workload is 2,048 MiB, with a measured own peak of
362 MiB on **GPU-91a3b0a9-d48b-1503-2893-33153c3fe405**. The actual CUDA probe
reports checksum **6**, exactly one visible device and a held 64 MiB tensor;
the original supplied probe is unchanged. Native resource-planning Skill/query
results carry the exact completed Attempt's measurement into the execution
session. The accepted final report is 8,957 bytes. Eleven Agent Attempts and
one GPU Process are CLOSED/SUCCEEDED and the run is COMPLETED.

Reported/accounted usage is **2.91801999999999990 USD**, 33 turns and 1,509,000
observed tokens; unknown reservations are empty. Independent run/pool Oracles
are clean, all twelve recorded process groups are absent, the private pool's
only request is RELEASED with no queued/reserved/launching/bound requests, and
its socket is gone. The fixture records no cleanup error. There are no observed
compaction events in 606 parsed native transcript records, with no parse errors.
See `g5-resource-recheck-command.json`, `g5-resource-recheck.log/xml`,
`g5-resource-recheck-evidence/`, `g5-resource-recheck-cleanup.json` and the native
observation file. The earlier resource FAIL/log/transcript/bill under
`live-20260914/` remains intact; no third resource sample was launched.

The existing `fold_agent_attempts` / `charged_cost` functions were applied to
all four distinct original sample databases, including the failed sample.
`cohort-accounting.json` records **12.99625799999999973 USD** model usage plus
0.003 USD free literature usage, leaving **7.00074200000000027 USD** below the
20 USD cohort ceiling. No copied database is counted again, and no old research
cost is imported. Charges are Host/CLI reporting, not a verified upstream invoice.

Both new test Hosts have exited, all **22 Agent +2 Process Attempts** are
closed, and all **24 recorded process groups** are absent. Retained fixture
files and transcripts are evidence, not running services. After the tests,
three remaining GPU processes were traced through their ancestors to the
existing Prometheus Host PID 3786571 and its pool requests; none belongs to the
qualification. Those processes and their shared service were left untouched.
The first direct process-group lookup found no match because bubblewrap uses
nested process groups; `unrelated-gpu-ownership.json` retains that lookup and
the complete ancestor/pool mapping instead of treating an empty lookup as proof.

**OpenAlex:** a real Bearer-authenticated `/rate-limit` request returned HTTP
200. No key was put in a URL or retained response. The service reported a
1 USD daily allowance, 8,840 credits remaining before the probe and 8,810
afterward, with prepaid balance/drawdown zero. The current official
[authentication reference](https://help.openalex.org/api/authentication/) and
[operation prices](https://help.openalex.org/access/example-costs/) describe the
same API and free daily allowance. The production OpenAlex/Poppler adapter
performed three stratified searches for `emotion recognition in conversation`,
returning three valid records and three HTTP-200 responses, each reporting
`meta.cost_usd=0.001`. The 30-credit delta agrees with this 0.003 USD metered
usage; unrelated earlier daily usage is not imported into this cohort.

The live search retained **three PDF_UNAVAILABLE results**: no open-access URL
for the first record, `POPPLER_INVALID` for a JAIR PDF, and the bounded work
deadline for the remaining direct/fallback work. These are not full-text
passes. The local logging wrapper then accessed the nonexistent `artifacts`
attribute instead of `LiteratureRetrieval.pdf_artifacts`; that AttributeError
is preserved in `openalex-live.json`. The returned packet passes the existing
`LiteraturePacket.from_dict` validator in a separate read-only review, without
repeating the API queries. See `openalex-packet-review.json` and both quota
responses. No failure was relabelled as a successful PDF retrieval.

A separate check used the actual publisher link for the
[MELD paper](https://aclanthology.org/P19-1050/). The existing guarded PDF fetch
path downloaded 2,950,595 bytes in one HTTP call; the production Poppler
extractor read ten pages, an abstract, conclusions and two supported-claim
records in 3.86 s. The PDF and `publisher-pdf.json` are retained. This proves a
real public PDF path while preserving the unavailable original three results.

**Prospective Host:** `formal-host-binding.json` contains complete argument
vectors for the existing managed launcher and CLI, validated through
`_validate_host_argv`, the actual `serve` argument projection intercepted before
Host construction, `validate_new_run_budget` and `profile_agent_budget`.
There is no second launcher or readiness authority. The prospective root
`state/MERC-causal-202609142341` does not exist; no formal Host, unit or run was
created. The real Profile again passes production admission.

| Binding | Checked value and scope |
|---|---|
| Control/runtime | `/data/Gray/Project/Hypoloom/.conda/envs/hypoloom/bin/python`, Python 3.12.14 / SQLite 3.53.4; current checkout import. Experiment prefix `/home/gray/miniconda3/envs/erc-cu128`. |
| Model/auth | Exact `glm-5.3`, resolved Claude 2.1.270 and `/usr/bin/bwrap`; settings authentication from regular mode-0600 `/home/gray/.claude/settings.json`. Actual paid common execution proves this selected path. Unused invalid sonnet/opus mappings are not selected. |
| Agent budget | Exact Profile projection: 240 turns, 1,000,000 observed tokens, 1,310,720 output bytes, 3000 s, `max_cost: null`. The transport explicitly includes `--agent-max-cost-usd null`; the effective Profile budget is observe mode despite the transport object's default enforce mode. Existing budget validators pass. |
| Target/data | `/data_ssd/Gray/Project/ERC` remains clean at `b4590893f756b9f09e2ff2bcbdcf1b784322801c`; the admitted target/runtime/read roots remain the user-confirmed values. No target or data write occurred. |
| Candidate GPU subset | `GPU-91a3b0a9-d48b-1503-2893-33153c3fe405` and `GPU-20ca7d4a-89d1-ec85-9bee-690afdd3283a`; actual `LocalComputeResources.snapshot` returns exactly this subset. This is a prospective launch binding, not a formal GPU reservation. |
| Confirmed existing shared pool | `/data/Gray/Project/Prometheus/.local/gpu-pools/shared-3090x4/control.sock`, expected server UID 1001. The user explicitly confirmed reuse with an independent Hypoloom namespace and only the reviewed UUID pair. Hypoloom's existing `gpu-pool-status` succeeds read-only. Each candidate GPU initially had 20,024 MiB allocatable and no commitment. At the final observation each has one original-research commitment and **11,823 MiB allocatable**, with a 4096 MiB pool reserve and zero unknown attribution. These are time-specific capacity observations, not MERC memory estimates or reservations. This external service deliberately remains rooted in the old directory and serves existing research. No pool was replaced, started or joined by a formal Hypoloom run. |
| Namespace / observer / lifetime | Prospective namespace `hypoloom-MERC-causal-202609142341` has zero existing pool run/request rows. Web bind/close on 127.0.0.1:8766 succeeds. Telegram remains disabled. This initial follow-up observed `Linger=no`; the later authorized change and actual managed-Host test resolve that preparation condition, as recorded below. |

The resource launch's first local path lookup mistakenly excluded every fixture
directory containing `current`; its StopIteration is recorded in
`resource-launch-path-probe-failure.json`. It occurred before any resource/model
launch. Correcting that lookup and independently auditing the completed common
run established exact prior model cost **10.07823799999999983 USD**. The live
resource recheck started with a conservative prior **10.08123799999999983 USD**
including free OpenAlex usage, and a fresh allocation of at most 6 USD; this
does not reset the original cohort or overwrite its first resource failure.

Final source comparison finds no runtime, test, Skill or script drift from the
repaired source archive; only this document and Codex CURRENT memory changed
during the follow-up. The existing four documentation tests pass, and Git
whitespace checks report no errors. Current checkout imports, the clean ERC
target, Web bind/close on 8766 and the absence of the formal state root were
rechecked. Detailed final checks are retained with the same batch evidence.
No runtime code, local Profile, shared service or system setting was changed
in this follow-up, and no commit, push or formal Start occurred.

### Final startup verification — 2026-09-15

Evidence is in the existing ignored tree
`.local/qualification/start-readiness-20260915/`. This is a continuation of
the same qualification cohort. No runtime, test, Skill, script or local Profile
changed. Ordinary comparison against the repaired source archive checks 544
source files with none missing; only this document and Codex CURRENT memory
differ. HEAD plus the reviewed working tree remains the candidate.

**Final G1 and closure:** reuse the existing eight file groups with a fresh
`/tmp/hl-final-g1-ownv8bk2` root, complete project Conda Python and no inherited
`HYPOLOOM_REAL_*` opt-ins. All groups return 0. The 5,606 distinct JUnit cases
give **5,581 passed / 25 skipped / 0 failures/errors**; static checks also pass.
No complete qualification was used as a repair debugging loop. The paid and
real-GPU cases are reused only where their unchanged bytes, environment and
explicit configuration match. `g1-command.json`, `g1-[0-7]-command.json`,
logs/XML, `g1-result.json` and `g1-skips.json` retain the exact execution.

The 19 remaining opt-in cases include older/alternative native contracts,
stage-specific paid probes, exclusive-GPU and two-GPU-per-experiment scenarios,
and the unapproved Telegram notification test. They remain unexecuted. In
particular, `test_native_gpu_g4.py` and the real two-GPU goal-stop case require
exclusive idle devices outside the selected shared-pool topology; the plural
experiment also differs from MERC's maximum one GPU per experiment. Existing
research occupies the reviewed inventory intermittently. No old process was
stopped, idle check weakened or different GPU substituted to clear these skips.
The already passed shared-pool G4 scenarios are the applicable resource lane.

Independent read-only Oracle/count inspection of the seven existing G3 cases
plus native combined interaction finds **251 Agent +22 Process Attempts all
CLOSED**, **21 canonical outcomes**, and eight clean Oracles. The five intended
non-success terminal reasons are two LOST, two FAILED and one CANCELLED; the
TUI cancellation remains distinct from successful completion. All **244
distinct recorded process groups** are absent. No related process is found
by `/proc` command/cwd/root/fd inspection, no selected control socket remains,
and no related private mount remains. These checks do not reinterpret the full
suite's deliberately corrupt/open unit-test fixtures as historical runs.
`g1-cleanup.json` retains the results. The first supplemental SQL probe used
a nonexistent `workflow_runs.status` column and failed before any mutation;
`cleanup-probe-error.json` retains it. The corrected inspection uses the
current schema and existing Oracle; this probe error is not a G1 test failure.

**Actual managed Host and lifetime:** the existing launcher starts an empty
Host with all reviewed formal model/authentication/literature/GPU/pool flags,
using only a fresh state root and `hypoloom-standby-d01fj66j` namespace. Its
user-systemd service stays active after the launcher exits. The private bundle
matches all **131 current runtime files**, the settings copy is mode 0600 and
UMask is 0077. Durable counts are zero runs, zero Attempts and zero research
facts; pool namespace run/request counts remain zero before, during and after.
The existing stop command succeeds, Host PID 558577 disappears, the control
socket is removed and the transient unit is collected with MainPID 0. Only
this test's private settings copy is removed afterward. See
`managed-binding-smoke-command.json` and `managed-binding-smoke.json`.

The user then explicitly authorized `enable-linger gray`. The noninteractive
command exits 0 and subsequent `loginctl show-user gray` reports `Linger=yes`.
The user manager PID **1355633**, shared-pool PID **3785437**, original-research
Host PID **3786571** and login-session list are unchanged. The
[installed-generation loginctl manual](https://manpages.ubuntu.com/manpages/focal/man1/loginctl.1.html)
documents the user-manager lifetime beyond logout. This verifies the setting
and actual managed service topology; it does not simulate last-session logout,
reboot, automatic research restart after reboot or 70-day endurance. Existing
unrelated failed units remain untouched. See `linger-change.json` and
`long-running-preflight.json`.

**Read-only ERC integration:** the existing deep cache auditor reads **1,432
MELD dialogues /13,708 utterances** and **151 IEMOCAP dialogues /7,433
utterances** in 3.97 s. Both results retain `WARN: source is empty`; there is
no alignment/schema failure. The existing dataset/dataloader and causal
MultiEMOStrict wrapper then perform one CPU inference per dataset using at
most eight real cached utterances. Logit shapes are `[1,8,7]` and `[1,8,6]`,
with finite logits/auxiliary loss and CUDA uninitialized, in 3.48 s. No
optimizer, checkpoint, training, scientific score or source/data write occurs.
The commands retain 180/90 s bounds, two CPU threads and temporary working
directories. See `erc-deep-cache*` and `erc-cpu-forward*`. Provenance and causal
acceptance still require the user-confirmed research work; no new feature
boundary or scientific metric was invented.

**Production OpenAlex/PDF path:** the first targeted MELD query spans
1990–2026. All three search requests return HTTP 200, but the two early strata
have zero records and the existing validator raises `LiteratureCoverageError`
at `literature.py:3366–3375`. The sample remains failed in
`openalex-topical-live.json`; its 0.003 USD metered usage is counted. This is
an unsuitable query range for that dataset, not an authentication failure.
Existing `test_literature.py` covers empty-stratum rejection and subsequent
query adjustment; no validator was weakened.

One corrected sample changes only the test query's first year to 2019, keeping
the production transport, 15 s request timeout, three one-paper strata, two
retry maximum, 60 s PDF work window and existing byte/page bounds. It returns
three HTTP-200 search results and a valid packet in **43.84 s**, with no retry.
One result, DOI **10.1109/tmm.2023.3271019**, is retrieved from arxiv.org and
parsed by the actual Poppler extractor: **2,925,625 bytes, 13 pages and two
supported-claim records**. IEEE/MDPI access failures and Semantic Scholar 429s
leave the other two records `PDF_UNAVAILABLE`. They remain unavailable, as do
the original earlier sample's three PDF results. This corrected packet proves
one complete production retrieval path, not universal publisher access. See
`openalex-topical-corrected.json` and `topical-literature-pdf-002.pdf`.

The two new three-request samples report **0.006 USD** total metered free
usage; `/rate-limit` changes from 0.119 to 0.125 USD used, leaving 0.875 USD
daily allowance at observation. Prepaid/cash drawdown is zero and no charge
is unknown. Reapplying existing `fold_agent_attempts` / `charged_cost` to the
four distinct original paid databases yields unchanged model usage
**12.99625799999999973 USD**. Including all three literature samples gives
**13.00525799999999973 USD**, leaving **6.99474200000000027 USD** below the same
20 USD ceiling. `cohort-accounting.json` retains each sample, including
failures; simulated G1 provider costs and unrelated research spend are excluded.

**Final admission:** `final-readiness.json` records another successful
production Profile check, unchanged clean ERC HEAD, correct checkout import,
regular mode-0600 credential files, `Linger=yes`, successful Web bind/close on
8766, and absent formal state root. At 00:21 +08:00, the existing pool reports
**11,823 MiB allocatable** on `GPU-91a3b0a9-d48b-1503-2893-33153c3fe405` with
one old-research commitment, and **20,024 MiB** on
`GPU-20ca7d4a-89d1-ec85-9bee-690afdd3283a` with none. Both have zero unknown
attribution. Capacity is a live observation, not a reservation or a model
memory estimate. The formal namespace still has zero run/request rows.
Actual Start must use the already reviewed arguments in
`readiness-20260914/formal-host-binding.json` and ordinary live pool admission.
No further configuration input is missing. Continue in Claude Code with
`/autoresearch init`, adopting the existing MERC-causal Profile and reviewed
bindings; the existing flow's final `Start now` choice initiates the formal
topic there. This is not an additional approval step in the Codex audit
conversation. The user's intended operating interface is Claude Code.

### Approved bounded resource/model validation

The user confirmed the following resources and limits after the initial audit
on 2026-09-14. This authorizes these disposable tests, not a formal topic run.
All use control Python `.conda/envs/hypoloom/bin/python` and the resolved Claude
binary `/home/gray/.local/share/claude/versions/2.1.270`. No existing GPU pool
will be replaced or used. The approved pair is devices 2 and 3. The fresh
inventory found 24,121 / 23,404 MiB free respectively, above the required
6,144 MiB; the existing PID 216670 on device 3 is left untouched. G4 uses
fresh private test pools. The later explicit prior accounted usage declaration
was 0 USD; it applies to this qualification cohort, not to old research bills.

Both G4 cases completed on their first execution within the approved 20-minute
total: 306.18 s pytest / 306.72 s including the launcher. The initial-estimate
case observed `capacity_unavailable` before a Process existed. The ordinary
case captured 33 pool snapshots with two same-card commitments and fully known
owned attribution/credit; the initial-wait case captured four such snapshots
after calibration. Earlier transient snapshots with unknown attribution were
not counted as established owned credit. Each of the eight distinct Process
Attempts has its own CUDA output with the exact visible UUIDs and checksum 6
per device; projected copies of those logs are not extra experiments.

| Conditional test | Confirmed resources and limits |
|---|---|
| G4 | Existing `test_shared_gpu_native_g4.py` two live cases; fresh private pool and local scripted providers. Approved RTX 3090 UUIDs: `GPU-91a3b0a9-d48b-1503-2893-33153c3fe405` and `GPU-20ca7d4a-89d1-ec85-9bee-690afdd3283a`. CUDA prefix `/home/gray/miniconda3/envs/erc-cu128`, complete Conda / Torch 2.8.0+cu128. Require at least 6,144 MiB free per device at admission, preserve the test's 512 MiB reserve, 540 s drive bound per case and allow up to 20 min total including cleanup. The cases include multi-Host sharing, same-card overlap, initial 16,384 MiB estimate waiting and calibrated follow-up work. No paid model/auth source is used. |
| G5 CPU | Selected the existing `g5-local` platform fixture because no topic completion semantics were supplied. Approved selector `glm-5.3` and reviewed `/home/gray/.claude/settings.json`. CPU only; 900 s run bound plus 30 s drain margin, 240 run turns, at most 24 turns / 300 s / 2 USD per invocation. Overall cohort ceiling **20 USD**, explicit prior **0 USD**, CPU allocation **14 USD** reserving up to 6 USD for the resource sample. Passed on its first execution. |
| G5 resource | Existing `g5-resource` sample on the first approved UUID above, same CUDA prefix/model/auth source, fresh private pool. 1,200 s run bound plus 30 s drain margin, 384 run turns, 32 turns / 300 s / 2 USD per invocation, 30 s GPU experiment bound; allocation **at most 6 USD within the same overall 20 USD ceiling**, not an additional allowance. |

G5 was executed sequentially. The GPU launch re-read the CPU Attempt rows and
used the existing accounting fold to carry forward 2.69332700000000004 USD;
its 6 USD allocation did not reset the cohort. The resource sample reported
3.55352699999999987 USD. The exact accumulated amount is
6.24685399999999991 USD (about **6.246854 USD**), with no declared/unknown
reservations, leaving about **13.753146 USD** under the approved ceiling.
This unused amount did not authorize a retry. Intra-session tool failures in
both samples, including the repaired CPU-check error, remain in their transcripts.

The cohort's prior charged amount including unknown reservations was initially
unknown and was not treated as zero; the user subsequently explicitly supplied
**0 USD**. New actual model fees are learned from the existing accounting,
while unreported Attempts retain their declared reservations. Every later
sample's allowance must be reduced by all
prior accounted usage; changing directories does not reset that amount. The
existing runtime limits admission using declared/reported charges; a provider's
already billed final response can exceed its requested invocation ceiling, so
this is not a guarantee about external billing. Do not automatically retry a
failed paid sample. Real notifications are excluded. The OpenAlex key-file
source was supplied and locally checked at that initial stage; the later online
sections record actual authentication, retrieval, quota and usage. The initial
preflight alone was not a network result.

### Cleanup and local evidence

After the fresh G3 and installed-wheel runs, separate host-level inspection
and read-only SQLite counts found **all 156 recorded process groups absent**,
no related live Hypoloom process, no remaining socket and no private tmpfs
mount. All eight run Oracles and the CPU-only pool Oracle were clean, with no
open Attempts, pending effects or owned process residue. The managed service
was collected automatically. Test directories, transcripts and the debate's
26-file disposable target under `/var/tmp` are retained as evidence; they are
not live processes or reserved compute resources. No pre-existing run was
started, resumed, cancelled, migrated, settled or deleted.

After the authorized G4 run, independent read-only inspection found four
`COMPLETED` runs, all 80 recorded process groups absent, and no related process,
socket or private mount. Both private pools have exactly four RELEASED
requests and zero QUEUED/RESERVED/LAUNCHING/BOUND requests; their services
closed. All four disposable G4 target repositories were removed by the tests.
The pre-existing PID 216670 remained present on device 3, and no test CUDA
process remained in NVML. No existing pool or research run was changed.

Both paid G5 Hosts have also closed. Their combined **22 Agent + 2 Process
Attempts** are CLOSED/SUCCEEDED, both run Oracles are clean, and all **24
recorded process groups** are absent. The resource pool has one RELEASED
request and zero pending/bound requests, a clean pool Oracle, no socket and
no live server or test CUDA process. The pre-existing process 291734 on the
selected GPU remained present in the after-inventory. Temporary target/data
fixtures remain as evidence at the paths recorded by the existing tests; they
hold no live processes or reserved GPU capacity.

Detailed audit outputs are under ignored
`.local/qualification/preflight-20260914/`: `environment.json`,
`prior-evidence-compare.json`, `evidence-reuse.json`, `current-coverage-map.json`,
`static.json`, `g2.xml`, `g3.xml`, `focused.xml`, `enospc.xml`,
`managed-wheel.json`, `g3-durable-counts.json`, `cleanup.json`,
`compaction-observation.json`, `profile-checks-corrected.json`,
`placeholder-model-probe.json`, auth/resource metadata, command logs and
`g3-evidence/`. Original import/build/browser/failure logs remain beside this
subdirectory. These are local outputs, not published artifacts or a new
qualification authority.

The authorized follow-up is retained under
`.local/qualification/live-20260914/`: `g4-command.json`, `g4.log`, `g4.xml`,
`g4-cleanup.json`, complete `g4-evidence/`, before/after GPU inventories,
`candidate-before.json` and the credential-free approved-auth preflight.
Source comparison against the previous final archive found no checkout
candidate differences before this follow-up's documentation edits; the only
raw absent file was the build-generated `setup.cfg`, not a runtime source.
No new runtime or test dependency was installed for G4.

Paid evidence is in the same `live-20260914/` directory: `g5-local.log/xml`,
`g5-resource.log/xml`, both command/cleanup JSON files, complete
`g5-local-evidence/` and `g5-resource-evidence/`,
`g5-local-native-observations.json`, and `g5-resource-wrapper-replay.json`.
The GPU failure's original JUnit/log was not replaced. The existing focused
wrapper selection then passed **19 tests, 48 deselected**, 1.22 s, offline;
it verifies the current recognizer, not the paid canary's promotion. See
`g5-wrapper-focused.log/xml`. No test/runtime code or dependency was changed.

The later configuration-source question was checked against the actual CLI,
managed launcher, private-settings lifecycle and OpenAlex reader. The local
credential-free reproductions are in `live-20260914/config-source-probes.json`:
the Host call was intercepted before startup and the OpenAlex probe used a
temporary dummy key. Neither probe made a network request; the temporary root
was removed. The two resulting configuration findings above remain unfixed.

At the end of the initial preflight, the Profile/Skill/documentation selection
passed **43/43**. Wheel and source distribution builds succeeded without
build dependency downloads or dependency installation. The rebuilt wheel's 131
runtime files match both the checkout and installed environment; the source
archive has 552 files, all required public assets, and only the two multi-expert
template directories. See `docs-final.xml`, `build-final.log` and
`package-final.json` in the same ignored evidence directory.

After the authorized G4 follow-up, the documentation checks passed **4/4**
and Git whitespace checks passed. The final ordinary source comparison found
only `docs/qualification.md` changed from that earlier source archive; runtime
and test code stayed unchanged. See `live-20260914/docs-final.xml` and
`live-20260914/candidate-after.json` under the ignored qualification directory.

The final G5 report update also passed all four documentation checks and Git
whitespace checks. The final source comparison still differs from the initial
preflight archive only in this document; see `g5-docs-final.xml` and
`g5-candidate-after.json` in `live-20260914/`.

The supplied Profile/key follow-up is retained in
`.local/qualification/merc-causal-20260914/`: the original and corrected
`profile-check` commands/stdout/stderr, `candidate-profile-environment.json`,
`erc-runtime.json`, `erc-source-import.json`, `erc-data-check.log` and its
command, shallow cache/split metadata, local PDF extraction logs,
`paper-reference-check.json`, and `read-only-resource-inventory.json`.
`repro_common_interaction.py`, `common-interaction-defect.json`, its original
failed assertion log and command reproduce the applicable high-priority defect
without creating a Host or acquiring writer authority on the retained G3 DB.
The follow-up made **zero model calls, zero network literature requests and
zero notification deliveries**, and created no Host/Agent/experiment/pool,
listener or GPU allocation. Its ordinary inspection subprocesses exited.
Prior test cleanup remains as recorded above; the occupied Web listener and
other existing services, original Profile, key, target/data and runs were left
unchanged. This follow-up adds no charge to the **6.246854 USD** cohort usage.

## Promotion rules

| Stage | Required outcome |
|---|---|
| G0 | Close each reproduced repair failure with focused regression and an observable outcome. |
| G1 | Freeze the intended code/test candidate; compile, inspect imports/schemas/contracts/Skills/docs, run the full offline suite, and validate installation/package assets. Unexpected failure is NO-GO. |
| G2 | Audit every retained run in the declared supported cohort without mutation; compare expected findings, logical rows and artifacts, and replay applicable persistence/recovery fixtures. Lost or new unexplained findings are NO-GO. |
| G3 | Close actions through the real resident Host, private runtime bundle, broker, Agent, Process, collection, publication, recovery and next action. Include injected exits and complete descendant cleanup. |
| G4 | Exercise several bounded hypothesis-evolution cycles with real CPU/GPU jobs when applicable, including sharing, waits, failures and recovery. Count externally observed effects and require no silent stall or owned residue. |
| G5 | Run conditional, short real-model compatibility canaries under explicit wall/turn/cost/resource limits. These sample native prompt/tool/adapter compatibility, not scientific quality or deterministic endurance. |

G3/G4 closure is the principal engineering signal. A long scientific run or
24/72-hour soak is optional and risk-triggered, not the default integration
test. A later gate cannot compensate for an earlier failure. Preserve original
failures and unknown charges; a retry is a separate sample. Source changes
require refreshing the affected evidence and final candidate checks.

Record the Git candidate, environment, commands, test results/skips, action
counts, resource/cost limits, observed cleanup and remaining gaps in the current
qualification report. Use ordinary Git, pytest JUnit output and the existing
run/pool Oracles; do not create a parallel authority or checksum protocol.

## Install and offline checks

Use the pinned [environment](../environment.yml), then install the project
with the development extras shown in [SETUP](../SETUP.md):

```bash
python -m pip install -e '.[dev,monitor]'
python scripts/qualify.py g1 --junitxml .local/qualification/g1.xml
python -m build --outdir .local/dist
```

Also install the wheel into a separate environment and run its CLI/MCP and a
fresh disposable bundle from outside the checkout. Check migrations, native
Skills/plugins and Web assets are present. Source tests deliberately remain
outside the wheel. Import-cycle, Schema, tool, Skill and documentation tests
are part of the offline suite. Missing optional facilities are explicit skips.

## G2: fixtures and retained runs

```bash
python scripts/qualify.py g2 --junitxml .local/qualification/g2-fixtures.xml
```

This replays existing Oracle, transaction, schema migration, backup/restore and
bundle tests. **It does not replace the retained-run cohort.** Public parser
fixtures live under `tests/fixtures/profiles/`; private research data is not
distributed.

For every retained Hypoloom run in the candidate's supported format, use an
isolated installed reader and the accepted baseline reader to perform the same
read-only audit, for example:

```bash
/absolute/reader/bin/python -B -m hypoloom.cli oracle \
  --state-root /absolute/retained/root --run-id EXACT_RUN_ID
```

Compare existing audit results and expected historical findings; independently
check that logical state and artifact contents did not change. An existing
OPEN Attempt is a historical fact, not permission to settle it. Do not start a
Host to perform G2. If the format requires its original bundle, use that reader
and record this narrower scope. Old-project runs remain in the source project
with their own readers; Hypoloom does not claim to migrate or resume them.

The first import has no retained Hypoloom release cohort. Record that coverage
gap explicitly; do not manufacture a legacy compatibility pass or treat fixture
replay as full G2. Future retained Hypoloom controls use their existing stable
run identities and outcomes, with failures retained as well as successes.

## G3: production-process closure

```bash
python scripts/qualify.py g3 --junitxml .local/qualification/g3.xml
```

The seven selected scenarios cover two CPU cycles and observation, committed
submit crash/reentry, three-round debate restart, common acceptance and goal
stop, premature execution completion, resource-skill learning, and TUI
leave/reentry/cancel/Host exit. They use temporary run roots and local scripted
providers, with actual native CLI/bwrap/Host and CPU processes where applicable.
Install Claude Code and bubblewrap first; absent dependencies and resulting
skips mean the affected production boundary is unproven.

## G4: shared CUDA loops

First authorize exact GPU UUIDs and a runtime with PyTorch/CUDA/NVML support.
The existing shared tests need two selected UUIDs and enough available memory;
they prove concurrent work on an individual card as part of their cross-Host
cohort. No permanent resource service is installed by the tests.

```bash
export HYPOLOOM_REAL_GPU=1
export HYPOLOOM_REAL_NATIVE_GPU=1
export HYPOLOOM_REAL_SHARED_GPU=1
export HYPOLOOM_REAL_GPU_UUID=GPU-REPLACE_WITH_AUTHORIZED_UUID
export HYPOLOOM_REAL_GPU_SECOND_UUID=GPU-REPLACE_WITH_SECOND_AUTHORIZED_UUID
export HYPOLOOM_REAL_GPU_RUNTIME=/absolute/approved/cuda-environment
python scripts/qualify.py g4 --junitxml .local/qualification/g4.xml
```

The cases cover ordinary measured learning and an initial memory estimate that
waits for capacity, then calibrates into same-card overlap. Require terminal
run/pool Oracles, actual CUDA results, complete attributable samples and zero
owned process/resource residue. Existing research pools are not test resources
unless that exact use was separately authorized.

## G5: real-model compatibility

All live tests remain disabled by default. Select an exact executable, existing
regular mode-0600 settings file and model. Resource availability is not spending
authorization. Review each cohort's caps first, including prior charged costs
and unknown reservations; missing cost is never zero.

Shared authentication inputs are `HYPOLOOM_CLAUDE_EXECUTABLE`,
`HYPOLOOM_CLAUDE_SETTINGS`, `HYPOLOOM_CLAUDE_MODEL` and the explicit
`HYPOLOOM_REAL_CLAUDE=1` opt-in. For the local or common cohort, provide
`HYPOLOOM_G5_CUMULATIVE_LIMIT_USD` (positive and no more than the existing 20 USD
canary ceiling) and `HYPOLOOM_G5_PRIOR_CHARGED_USD` (nonnegative and below that
limit). Declare zero only for a genuinely unspent cohort. The remaining amount
becomes the run budget, with existing invocation, turn and wall limits retained.

USD values are CLI/Host dispatch thresholds, not guaranteed upstream billing
caps. An in-flight response can report more than its remaining threshold; retain
that charge and stop further dispatch. Fund retries, analysis and the final
report when reviewing each sample, and keep unknown costs reserved in the same
cohort. A failed 4 USD reading sample is not evidence that another 4 USD will
complete; increasing its allocation requires explicit approval, not a changed
directory or a weaker closure check.

Select exactly one mode per invocation:

| Selection | Additional opt-in | Test |
|---|---|---|
| `g5-local` | `HYPOLOOM_REAL_CURRENT_G5=1` | Native file tools, CPU execution, sessions and final report |
| `g5-common` | `HYPOLOOM_REAL_CURRENT_COMMON_G5=1` | Grounding-authored common parser, goal evidence and final report |
| `g5-resource` | `HYPOLOOM_REAL_SHARED_RESOURCE_G5=1`, GPU/native GPU opt-ins, exact UUID/runtime and explicit `HYPOLOOM_RESOURCE_G5_ALLOCATION_USD` | Native resource Skill, real CUDA execution and learning |

The local/common flags are mutually exclusive. Run `python scripts/qualify.py
g5-local`, `g5-common` or `g5-resource` after reviewing and setting the inputs.
Each reports its original outcomes and actual charges; preserve reservations
for unknown usage. Do not automatically rerun a failed paid sample or reset its
cost when choosing a new directory. After success or failure, verify all owned
Hosts, Agents, experiments and pool requests are closed.

## Listing tests without effects

```bash
python scripts/qualify.py g3 --collect-only
python scripts/qualify.py g4 --collect-only
python scripts/qualify.py g5-local --collect-only
```

Collection does not enable resource/paid flags. Clear live opt-ins before G1,
G2 fixture replay or G3; the selector rejects those accidental inherited flags.
For a focused repair, invoke pytest directly on its relevant tests before
freezing a candidate for the full funnel.


## Demand-driven institutional acquisition (2026-10-04)

Authorized repair follows read-only examination of MERC-causal-202610041801.
The user subsequently closed that research run; no restart, migration, browser
navigation, client-helper change or retained-run control was performed here.
Evidence: `.local/qualification/institutional-demand-20261004/`.

- IEEE's JavaScript PDF control and metadata viewer reproduce the old
  `no_full_text` failure. Real isolated Chromium checks now retrieve and validate
  the exact PDF; different article numbers, unrelated origins, JavaScript and
  credential-bearing targets are rejected before downloading.
- Browser requests are selected by tools rather than queued for every missing
  packet PDF. Same-run explicit peer requests reuse validated automatic bytes,
  preserving count/byte bounds, identity validation, import/run isolation and
  per-Attempt source ownership. One MCP call waits within the original deadline;
  challenges return gaps without operator intervention or blocking Host service.
- Final related selection: 277 passed / five opt-in cases skipped. Real Chromium
  and navigation selection: 114 passed. Final delivery regressions: 38 passed
  (overlapping); log service: one passed; documentation/Profile: seven passed.
  Including the two native scenarios, the final outcome for 402 distinct test
  identities is pass, with five skipped. The skips cover isolated SSH and browser
  runtime lifecycle tests; this repair does not change those launchers.
- The real installed CC, local scripted provider and actual Host complete both
  preparation-failure/public-PDF fallback and automatic-browser-document paths.
  Each closes 18 Agent and one CPU Process Attempts successfully, three readers,
  collection and Oracle. Independent census finds exactly one native web fetch
  and one delivered metadata record, no web errors, zero runtime literature
  controls, no live process groups and empty paper staging. Six reader PDF copies
  were independently parsed again with Poppler and contain the sample identity
  and per-scenario method text.
- The new native browser case initially exposed an invalid intermediate `pending`
  tool-observation status. Internal waits are now folded into the actual native
  call, preserving lookup diagnostics; Oracle and scope/source checks remain
  unchanged. The initial failure remains in `native.xml`; the corrected browser
  result is `native-final.xml`. A separate earlier development test correctly
  rejected source edits during execution; final tests used stable runtime source.
- `independent-census.json`, `test-counts.json` and JUnit files record closure and
  distinct outcomes. `retained-log-observation.json` reads the existing log without
  changing it and confirms four historical IEEE validation events. These events
  do not prove PDF media, archive delivery or institutional subscription.
- Wheel, isolated installation and fresh runtime bundle match all 175 package
  files; installed read-only TUI/refresh/Esc smoke passes. The previously created
  bundle remains unchanged, as intended; the final comparison uses a fresh root.

This qualifies the repaired framework paths and bounded fallback, not Windows
SSO, uninterrupted publisher availability or unattended Cloudflare resolution.
No full offline-suite, paid-model, GPU or soak promotion is claimed. Existing
runs retain their original implementation. The next user acceptance step is a
new init from this version; the Windows helper does not need an update for this
server-side repair.

## IEEE PDF viewer precedence (2026-10-04)

Evidence: `.local/qualification/ieee-pdf-preference-20261004/`. The user clarifies
that a server graphical desktop is unsuitable on a shared server. Inspection of
the cancelled MERC-causal-202610041801 run finds no archived IEEE source and a
selected TASLP fetch ending in `no_full_text`. Its historical validation events
do not record PDF media or archive delivery. No retained run or client was changed.

- Three isolated Chromium cases fail before the repair (`red.xml`): readable
  article HTML prevents following the identified PDF viewer, masking either the
  available PDF, a challenge or conflicting PDF identity. Move the existing viewer
  step before text fallback. All three now preserve the actual viewer outcome;
  the fourth case retains HTML-only behavior when no PDF is identified.
- The successful case validates actual PDF bytes and checks their ordinary
  PaperWorkspace archive. The initial archive assertion used an invalid source
  ownership reference; correct the test fixture, retaining the validator.
- Final browser/navigation/access/deadline/demand/fallback selection: 191 pass
  (`final.xml`); documentation/Profile checks: seven pass (`docs.xml`). These use
  isolated scripted publisher routes with real Chromium, not the Windows session.
- Wheel, isolated installation and fresh runtime bundle match all 175 package
  files; installed read-only TUI/refresh/Esc smoke passes (`installation.json`).
- A fresh bounded public-only probe of `10.1109/TASLP.2024.3434495` and
  `10.1109/TAFFC.2025.3558222` obtains zero PDFs. The first locator returns HTTP 429;
  both retain `no_verified_pdf` and the probe's nonzero result. This is a limit of
  this automatic public lookup, not evidence that no legitimate copy exists.

No new Windows institutional download, native CC closure, full offline-suite,
paid-model, GPU or soak qualification is claimed. Previous native Host closure
evidence remains separate above. Current IEEE institutional readiness still needs
an actual authorized PDF download, identity validation and archive; opening its
viewer or finding a repository sample is insufficient. That bounded acceptance
check can run independently of a fresh research init.

## TUI toolbar alignment (2026-10-05)

Evidence: `.local/qualification/tui-toolbar-alignment-20261005/`. The toolbar's
fixed height of three rows included its bottom border, clipping the last row
of Help, Report Issue and End run. Automatic toolbar height now accommodates
the three-row buttons and divider. Before/after rendered cells and screenshots
cover 50×16, 80×24, 120×40 and 237×52; every label has a blank row above and below.
The existing headless monitor and narrow-layout tests both pass (`focused.xml`).
This is a display-only change; retained research runs remain untouched.

## Observer language switch (2026-10-05)

Evidence: `.local/qualification/observer-language-20261005/`. TUI and Web now
share a packaged English-to-Chinese interface catalog and expose a toolbar
switch. Initial language is English regardless of browser locale. TUI choice
lasts for the monitor session; Web remembers an explicit choice in the browser's
origin-local storage. Denied storage still permits switching in the current
page; an unavailable catalog leaves English observation operational.

Translation applies to interface templates before inserting values. Research
content, identifiers, commands, logs and durable records retain their original
values. Switching redraws the existing observation without issuing controls or
new refresh requests. Tests preserve filters, human drafts, stale-connection
indicators, institutional observations and exact operator command semantics.
TUI rendering covers 50×16, 80×24, 120×40 and 237×52; browser checks include a
390-pixel viewport and repeated switching of cached workbench panels.

The final results contain **166 distinct passing test identities**, zero failures
and zero skips, including 143 existing monitor/Web/support regressions. Original
red results remain in the evidence directory: a missing resource-modal import
was repaired and an incorrect Chinese-label expectation was corrected. Focused
reruns pass; no acceptance check was weakened. JavaScript syntax, Python 3.10
parsing, changed-module undefined names and diff whitespace checks pass.

An isolated wheel installation contains all 1,248 catalog entries and serves
the catalog through the existing static-asset route/security headers. Catalog,
translation helper, TUI and Web script bytes match source, wheel and installation.
English/Chinese usage and operator guides describe preference lifetime and
unchanged research content. Retained runs, Hosts and Windows helpers were not
changed; no native research closure, paid-model or GPU qualification is claimed
for this presentation-only change. Reopen the monitor (including the standalone
Web listener) to load the new code; research does not require reinitialization.

Button-style follow-up: the default TUI focus style reversed the label against
white, and the Chinese label wrapped because button padding left only two text
cells. The compact label now uses CN/EN with bold, non-reversed focus text;
the existing whole-button focus color remains. Web uses the theme's blue accent
instead of inheriting the amber Help style. Twelve existing language/docs cases
pass. Rendered checks cover both labels at four TUI sizes and normal/hover/focus
Web states at 1440/390 pixels; TUI button dimensions remain constant, text is
centered on one line, and Web colors match the existing accent. An initial manual
probe incorrectly required an unchanged horizontal position when neighboring
translated labels resized; the final probe checks button dimensions instead.
Evidence: `.local/qualification/language-button-style-20261005/`. This styling
follow-up was checked from source; the earlier wheel predates these cosmetic edits.

## Same-title arXiv fallback (2026-10-04)

Evidence: `.local/qualification/arxiv-fallback-20261004/`. The operator requests
same-title arXiv PDF fallback without depending on a local browser. This extends
the selected-paper public acquisition shared by Host, preparation samples and
the standalone probe; it does not add broad arXiv topic search or bulk prefetch.
Existing research runs, Windows helper/browser and credentials remain unchanged.

- Match exact normalized title, first author and another author when present,
  non-conflicting publisher DOI, a unique versioned ID and its official PDF link.
  Existing PDF validation, source rejection, byte/time/HTTP limits and archive
  ownership still apply. Actual arXiv version notes survive delivery and archive.
- Same-host processes serialize API calls using an ordinary empty `/tmp` lock
  file, waiting three seconds before each request. Independent-process exclusion,
  abrupt holder exit, request spacing and deadline-without-send checks pass.
  This does not coordinate isolated containers or multiple machines.
- Final related selection: 403 pass (`final-green.xml`); documentation/Profile:
  seven pass. Negative cases cover author/title/DOI conflicts, ambiguous entries,
  absent metadata, hostile XML declarations, wrong PDF, challenge pages, rejected
  sources, version-changing redirects and bounded HTTP failures. An earlier
  browser-probe fixture still returned the old discovery tuple; updating its
  author field restores both cases without changing their assertions (`final.xml`).
- The installed native CC, actual Host, local scripted model provider and CPU
  process complete automatic arXiv acquisition, three readers, collection and
  Oracle (`native-final.xml`). Independent census: 18 Agent + one Process terminal
  successes, one delivered PDF record, four byte-identical original archives,
  zero runtime literature controls, live process groups or paper-staging residue.
  All four PDFs are independently reread with Poppler.
- Live API discovery exposed token-AND title queries missing the known ResNet
  paper; the complete title phrase returns its correct entry. The final production
  query/parser plus real PDF validation and ordinary archive obtain
  `1512.03385v1` for `10.1109/CVPR.2016.90`: 819,383 bytes, 12 pages
  (`live-resnet/`). This directly tests arXiv lookup/download; the broader public
  candidate ordering is separately covered by the Host test.
- For actual research DOI `10.1109/TASLP.2024.3434495`, the unmodified standalone
  acquisition now finds `2407.16714v1`, but PDF retrieval times out in both the
  initial phrase-query run and one bounded retry. `10.1109/TAFFC.2025.3558222`
  has no admitted match. Keep the nonzero probe results and Semantic Scholar 429
  observations; these documents are not reported downloaded (`live-ieee-phrase/`,
  `live-ieee-retry/`). Initial empty token-AND results remain in `live-ieee/` and
  `live-direct/`, with the query comparison XML retained.
- Wheel, isolated install and fresh runtime bundle match 176 package files.
  Installed arXiv parser reads the actual saved feed; existing TUI/refresh/Esc
  smoke also passes (`installation.json`).

No Windows subscription, universal IEEE coverage, full offline-suite, paid-model,
GPU or soak qualification is claimed. The new default fallback needs no new init
configuration; existing runs keep their original runtime version.

## Public PDF timeout recovery (2026-10-04)

Evidence: `.local/qualification/arxiv-timeout-20261004/`. The user requests
diagnosis and repair of the previous arXiv PDF timeout. No retained research run,
Windows helper/browser or credentials are changed.

- Before modifying production code, instrument the existing HTTPS transport for
  `https://arxiv.org/pdf/2407.16714v1`: DNS 0.033 s, TLS by 0.261 s, headers by
  0.366 s, full 4,260,599 bytes by 1.064 s. The unchanged full DOI probe succeeds
  in 9.363 s and verifies 15 pages (`transport-trace.json`, `baseline/`). ResNet
  remains a successful control. This supports a transient network/service problem;
  old generic timeout events cannot identify the historical phase. Do not claim
  this spontaneous recovery as the effect of the patch.
- Red regressions show a temporary PDF timeout drops the candidate with remaining
  budget, and DNS timeout loses its phase (`red.xml`). Retain safe phase labels
  and byte counts in the existing exception and download trace. One public
  acquisition may retry a timeout once, using its remaining HTTP/time budget;
  no discovery replay, partial-content admission or fresh deadline. Known HTTP
  errors/rate limits, invalid PDFs and identity failures do not qualify.
- Final browser/navigation/public acquisition/deadline/demand/transport selection:
  291 pass (`final.xml`); documentation/Profile: seven pass (`docs.xml`). Checks
  distinguish TCP/TLS/request/header/body timeouts, hide exception text, recover
  once, preserve archive bytes and stop at repeated failure or exhausted budgets.
- A real download with a controlled first-response interruption after 1,378 bytes
  recovers on the single retry, validates the same 15-page PDF and completes in
  12.687 s with six public requests (`live-injected/`, `injected-fault.json`). The
  retained event identifies `response_body`, byte count and retry. Separately
  reread with Poppler, validate and archive through PaperWorkspace; resulting
  bytes equal both baseline downloads (`live-archive-check.json`). The injected
  fault proves recovery behavior, not the cause of the earlier natural timeouts.
- Native installed CC + actual Host + local scripted model provider closes the
  timeout/recovery, three-reader, CPU experiment, collection and Oracle path
  (`native.xml`). Independent census finds 18 Agent + one Process successes, one
  delivered web PDF with both timeout and retrieved events, four identical original
  archives, zero runtime literature controls, live process groups or paper-staging
  residue (`independent-census.json`). All originals are independently parsed.
- Wheel, isolated installation and fresh runtime bundle match 176 package files;
  installed phase/byte diagnostics pass (`installation.json`). No full offline,
  paid-model, GPU, soak or perpetual publisher-access qualification is claimed.

This resolves the selected-paper recovery gap and demonstrates the formerly
timed-out paper's current arXiv availability. It is an author/preprint source,
not proof that the IEEE publisher subscription PDF channel is available.
