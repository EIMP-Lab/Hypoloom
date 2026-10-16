# Feedback and contributions

English · [简体中文](CONTRIBUTING.zh-CN.md)

Report reproducible problems or suggest improvements through
[GitHub Issues](https://github.com/EIMP-Lab/Hypoloom/issues). Documentation fixes
and pull requests are also welcome. Keep both language versions consistent when
changing user guides or templates.

## Describe the issue

Include the Hypoloom version or commit, operating system, expected behavior,
actual result and the smallest steps that reproduce the problem. For a run-related
issue, state whether it is active or finished and which interface you used.
A screenshot or a minimal example is useful after you remove private information.

Do not include API keys, real Profiles, private datasets or raw model transcripts.
Use placeholders for private paths and research content.

## Collect diagnostics

Inspect locally, then preview a support export:

```bash
hypoloom debug inspect --state-root /absolute/state/run --run-id RUN_ID
hypoloom debug export --state-root /absolute/state/run --run-id RUN_ID \
  --support-bundle --preview
```

Use the control environment from [Setup](SETUP.md). Replace the path and Run ID
with your own values. Web **Report issue** and TUI **b** offer the same export flow.

| Material | Sharing scope |
| --- | --- |
| Local diagnosis | Keep local; `--include-payloads` can expose exact research inputs. |
| Default support bundle | Suitable for a public issue only after inspecting the actual archive. It filters research text, local paths and free-form errors, and pseudonymizes run/Attempt IDs. |
| Export with `--include-private-data` | Contains additional research content, including sanitized prompts and transcripts. Use only a private channel agreed with the maintainers. |

To save an archive, replace `--preview` with
`--archive --output /absolute/new-support.zip`. Open it before attaching it.
User-written issue descriptions are included in public exports. Exports are never
uploaded automatically, and filtering does not replace your review.

For private support, agree on a channel with the maintainers first. Submitted
material is used only for diagnosis, shared only with involved maintainers and
deleted after resolution or on request. Never post private exports in a public issue.
If export fails, include that failure in the issue without attaching unfiltered files.

See [Usage](USAGE.md) for export scopes and [API recovery](docs/guides/provider-recovery.md)
for connection problems. Do not modify an existing run while gathering diagnostics.

## Suggest or contribute a change

Explain the user need and the intended behavior. Keep changes focused and show
how they were checked. Use disposable examples without credentials or private
research data, and do not attach checks to other users' running Hosts or GPU pools.
