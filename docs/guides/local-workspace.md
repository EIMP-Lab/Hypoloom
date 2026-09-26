# Organizing the local workspace

English · [简体中文](local-workspace.zh-CN.md)

`.local/` is an ignored, machine-local workspace. It may contain development
evidence, private issue reports, build environments and service state accumulated
across versions. The tracked source, runtime instructions and Profile templates
define the implementation; the contents of `.local/` do not collectively describe
the current version. Personal Profiles live separately in `profiles/local/`, and
research state normally lives in `state/`.

This guide describes an optional organization for an existing development
workspace. A fresh clone does not contain these private files and does not need
to create the directories below to run Hypoloom. Commit the explanatory docs,
not the local evidence or data.

## Current evidence and development history

Keep original evidence paths and add small browsing directories:

```text
.local/
  README.md / README.zh-CN.md
  current/
    README.md / README.zh-CN.md
    qualification -> ../qualification/<integrated-validation>
  history/
    README.md / README.zh-CN.md
    qualification/        # links to earlier validation stages
    analysis/             # links to retained diagnoses
    reviews/              # links to review material
    ui-reviews/
    run-reviews/
    worktrees/
    audit/
    split/
    builds/               # links to retained build/install directories
    notes/                # unreferenced personal drafts moved here
  qualification/          # original evidence remains here
  analysis/               # original diagnoses remain here
  ...                     # existing private and operational directories
```

The material entries under `current/` and `history/` use relative symbolic links;
category directories, explanatory documents and relocated loose notes are ordinary
directories/files. The links provide
browsing paths to the same files, without copying evidence. Existing citations
continue to use the original paths. Use these links only to browse material:
**do not pass them as run/state roots, source workspaces or interpreter prefixes**.

`current/qualification` points to the integrated validation selected for the
current committed runtime-code baseline. Its local README explains the Git
revision, the original evidence path, coverage and limitations. For example,
the September 26 integrated submission retains its evidence at
`.local/qualification/dev-submit-20260926/`.

The name `current` does not automatically prove that the evidence matches a
future HEAD or uncommitted changes. Git and the existing
[qualification record](../qualification.md) remain the sources for that
judgment; the browsing directories add no version manifest or validation gate.
After code changes, update the local explanation and selected link according
to the existing qualification results. Do not infer a version from a directory's
date or rename older evidence to imply it validates a new revision. Put material
whose version is unknown in the history listing with an explicit “undetermined”
description until it can be identified.

History includes earlier stages of features still present in the current
implementation. It does not mean that those features were removed, that the
evidence passed, or that the material is safe to delete. Keep failed checks and
their successful follow-ups distinguishable.

## What stays at its original location

| Material | Reason to retain the original path |
| --- | --- |
| Run state, sessions, runtime bundles and their evidence | Stored absolute paths, canonical-path checks and original citations may depend on the location. Moving the files and leaving a compatibility link can still invalidate these assumptions. |
| Git worktrees | Git records their locations. Browse existing worktrees from history; any later relocation or removal must use Git and separately account for unmerged work. |
| Python/Conda and installed-package environments | Interpreter prefixes, installed scripts and recorded runtime inputs may contain absolute paths. |
| `gpu-pools/` | It can contain a live service's socket and durable resource state. File existence alone does not establish whether the service is live. |
| `credentials/` | Retained runs may still reference legacy credential files even after a Profile has adopted inline credentials. |
| `claude/` | It may be the active destination of the project's Claude Code session-storage link. It is not necessarily a disposable test cache. |
| `issue-reports/` | Keep submitted originals and their working/analysis material in the existing private layout. Follow the agreed retention and disclosure terms. |

Apply the same rule to original analysis, audit and validation files referenced
by documentation. Archive loose personal drafts physically only after checking
that no recorded path or ongoing work depends on their original names. Keep the
other historical materials in place and list them through the history links.

For actual run state, inspect durable records and process/service liveness
separately. Organizing local files does not authorize starting, resuming,
migrating, settling or deleting a research run. It also does not change the
current control environment or replace a recorded runtime with a historical one.

## Keeping the layout useful

1. Write new evidence to the existing canonical category, for example
   `.local/qualification/<task>/` or `.local/analysis/<task>/`, rather than
   through `current/` or `history/`. Keep research and service paths unchanged.
2. Follow the existing qualification workflow and retain the original outcomes,
   including failures. When native Unix sockets require a short path, use a short
   disposable test root and record its actual location in the evidence; do not
   deepen the socket path through a navigation directory.
3. Once results establish their scope, update the local bilingual explanation
   and browsing links. Select integrated evidence for the current baseline and
   keep earlier stages accessible from history. A documentation-only commit does
   not by itself create new runtime validation; describe the unchanged baseline
   accurately.
4. Review possible cleanup separately. Check references, retained evidence,
   worktree changes and actual process ownership before proposing deletions.
   Neither age nor absence of an obvious foreground process proves that a
   directory is disposable. This layout introduces no automatic cleanup.

All private contents and browsing links remain under the existing `.local/`
Git ignore rule. A link does not sanitize its target. Do not attach the whole
workspace, its private index or personal data to an issue; follow the
[issue-reporting guidance](../../CONTRIBUTING.md#reporting-issues) and inspect
the actual export before sharing it. Credential locations and model
authentication are described in [personal configuration](../../config/README.md).
