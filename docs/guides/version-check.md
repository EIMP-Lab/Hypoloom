# Version update reminders

English · [简体中文](version-check.zh-CN.md)

`/autoresearch init` shows an advisory version result from its initial
`bootstrap paths` check. It compares the local Git HEAD with the current tip of
the tracked remote branch. The package version alone (currently `0.0.1`) cannot
distinguish source updates. The reminder does not block initialization.

## Manual and offline checks

Run with the Hypoloom control environment:

```bash
python -B -m hypoloom.cli version-check
python -B -m hypoloom.cli version-check --json
python -B -m hypoloom.cli version-check --remote origin --branch main
python -B -m hypoloom.cli version-check --offline
```

The default checkout is the installed Hypoloom source, independent of your
current directory. `--repository /absolute/checkout` explicitly selects another
checkout. The default target is the current branch's configured upstream remote
and branch; without an upstream, it uses `origin` and the current branch name.
Forks and renamed upstream branches retain their configured meaning. This is
freshness relative to that branch, not a claim about every branch, the canonical
project repository or the newest published release. Use `--remote` with an
existing Git remote name and `--branch` to select a different target. Detached
HEAD requires an explicit branch. No repository URL or auth error text is printed.

The entire check has a five-second deadline. `--timeout 10` changes it (maximum
30 seconds). Git authentication is noninteractive and uses existing credentials;
failure does not open a login flow. Timeout terminates the owned Git/helper process
group. For init's path check, use `bootstrap paths --offline` or
`bootstrap paths --version-timeout 10`; an offline init request is forwarded to
the helper. Offline mode still shows local identity, without contacting a remote.

## Interpreting results

| Status | Meaning |
| --- | --- |
| `up_to_date` | HEAD matches the selected remote tip at check time. |
| `behind` | Available local history proves that remote has newer commits. |
| `ahead` | Available local history proves that local commits extend the remote tip. |
| `diverged` | Complete local history proves that neither tip contains the other. |
| `remote_differs` | The remote tip differs, but ordering cannot be proved from available local history. Review updates. |
| `offline` | Remote lookup was explicitly skipped; freshness is unknown. |
| `unavailable` | Missing Git/remote/branch, detached HEAD without a target, timeout or connection/authentication failure prevents checking. |

Uncommitted modifications and untracked files are reported separately. Even
`up_to_date` only describes HEAD, not the complete working tree. Partial clones
report working-tree state as unknown and skip ancestry checks to avoid implicit
object downloads on older Git. Shallow history cannot prove divergence. A wheel
or archive without a Git checkout shows its installed package version and cannot
prove remote source freshness; a surrounding unrelated repository is not used.

JSON includes the local/remote revisions, target, timestamp and reason. The
`update_available` value is `true` only for proven `behind`, `false` for
`up_to_date`/`ahead`, and `null` when unknown or divergent. It is not a release or
compatibility verdict. All advisory results exit with code 0; invalid arguments
are errors. Scripts should inspect `status`, rather than treating exit 0 as latest.

## Scope and updating

The check reads advertised refs with [Git ls-remote](https://git-scm.com/docs/git-ls-remote).
It does not run fetch/pull, refresh local remote-tracking refs, write the index,
store a version cache, install code or change Profiles/runs. Thus a remote update
not downloaded locally normally produces `remote_differs`, not a guessed
`behind`. Initialization shows the result once; Host scheduling, ordinary status
views and issue exports do not perform remote version checks. Issue exports reuse
the same local identity reader only.

Review the target and preserve local changes before manually updating through
your normal Git workflow. Existing research retains its recorded runtime;
updating a checkout does not migrate or resume a run. This feature adds no automatic
restart, migration or periodic notification service.
