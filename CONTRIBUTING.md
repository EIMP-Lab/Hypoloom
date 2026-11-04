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

## Validation status

The November 4 round-limit follow-up removes
`goal.yaml.stop_conditions.max_rounds_per_expert` from new runs and both Profile
templates. Round numbers and reports remain; experiment, time, cost and repair
budgets still apply. Delete the old field from local Profiles before starting a
new run. Existing runs keep their admitted policy. See
[configuration](config/README.md) and [runtime recovery](docs/guides/runtime-recovery.md).

The [corresponding dev record](https://github.com/EIMP-Lab/Hypoloom/blob/6a90cc2e99ef9ed12079d2cca20b87f898539193/docs/qualification.md#research-round-limit-removal-2026-11-04)
reconciles **8,683 offline passes / 242 skips / zero unresolved failures** from
one full run and affected rechecks. The 24 original failures and their test-only
fixes remain documented. Actual Host/CPU cases complete five research rounds and
stop at a separate experiment budget; native runs close with clean audits and
no live recorded process groups. Public checks pass **131 tests / one CUDA skip**.
Five retained roots and 3,072 artifact files are unchanged. Both packages match
206 runtime files across source, wheel, isolated installation and fresh bundles.
This follow-up includes no new GPU or paid-model qualification.

The earlier November 4 GPU update lets new workloads share by admitted memory budget,
preserves confirmed use within that budget during device pressure, and separates
attributed device recovery from ordinary execution repairs. At ACI 30, expert
inputs and tools exposed remaining repair and research-round allowances. Existing runs keep
their frozen policy. See [GPU resources](docs/guides/gpu-resource-planning.md)
and [runtime recovery](docs/guides/runtime-recovery.md).

The [corresponding dev record](https://github.com/EIMP-Lab/Hypoloom/blob/f1ae480593c27cc3c6988a23806778d2488b7bac/docs/qualification.md#gpu-sharing-and-execution-recovery-2026-11-04)
reconciles **8,657 offline passes / 242 skips**, with all eight original failures
resolved by affected rechecks. Seven native Host/CPU scenarios pass. Real CUDA
sharing and capacity-wait scenarios pass after recorded fixture corrections and
an unchanged strict sampling recheck; original failures remain visible. Five
retained roots and 3,072 artifact files are unchanged. Public checks pass **123
tests / one explicit CUDA skip**; real CUDA evidence is recorded separately.
Both packages match 206 runtime files across source, wheel, isolated installation
and fresh bundles. No paid-model test, GPU reset or driver change was performed;
these checks do not establish hardware health or continuous peak coverage.

The October 27 run-audit and feedback update aligns shared-goal closing with
accepted measurements, checks explicit Process claims in final reports, and
preserves complete evolution summaries within the existing input budget. Native
rejection stages, explicit execution repair links and observations for each plugin
version make follow-up evidence easier to inspect. See [Research workflow](docs/guides/research-workflow.md)
and [Runtime recovery](docs/guides/runtime-recovery.md) for the user-facing behavior.

The corresponding [dev qualification record](https://github.com/EIMP-Lab/Hypoloom/blob/c9aae262246ab6c7f85d1550b23ee210a04d1cde/docs/qualification.md#framework-follow-up-2026-10-27)
reconciles **8,574 passes and 159 explicit skips** after full offline testing and
focused repairs; this is not one uninterrupted green full run. All seven native
Host/CPU scenarios close, and all 15 retained-run audits keep their original
findings. Original failures and their follow-up checks remain in that record.
This update's validation covers offline and CPU paths; the earlier GPU/model
results below retain their original runtime and configuration boundaries.

Separate main publication checks pass **411 tests**: 400 focused runtime/public
checks, five browser cases and six native checks. The five Host scenarios close
133 Agents and 11 Processes with 11 outcomes and one accepted terminal command
per run; all 149 recorded test process groups are absent after cleanup. Runtime
logic matches dev, retaining only the two existing documentation-string differences.
All 199 public runtime files match across source, wheel, isolated installation and
fresh runtime bundles. Installed CLI/MCP and two CPU Process closures pass; three
retained expert-feedback projections match their read-only baselines. These are
bounded publication checks, not a new full G0–G5 qualification.

The preceding init offline-closure follow-up passes **303 focused runtime tests and four
documentation checks**. Disposable overlays of two retained runs also pass their
original Oracles and independent closure/residue counts. These targeted checks
do not extend the earlier full G0–G5 qualification to the updated runtime. See
[Initialization](docs/guides/initialization.md) for its authorization and report limits.
Publication verification also fixes a controller mount failure in source checkouts
under `/tmp`; all **82 public-overlay checks** pass after the dedicated regression.

Before that follow-up, the corresponding dev version completed bounded **G0–G5 qualification on
2026-10-24**. The [qualification record](https://github.com/EIMP-Lab/Hypoloom/blob/a8909545e38be9f2da61f83bcd7ed632e58754aa/docs/qualification.md#qualification-completion-2026-10-24)
retains the original failures, test-only repairs, commands and coverage limits.
The full offline selection plus focused rechecks covers **8,524 passes and 159
explicit skips**; this is multiple invocations, not one uninterrupted green run.

Evidence includes real Host recovery and CPU closure, GPU sharing and capacity
waiting, and three bounded `glm-5.3-flash` compatibility samples. Paid usage totals
**10.8409512 USD as reported by the CLI**, with no unknown reservations. Retained
research runs were inspected without changes; their known historical findings
remain visible.

At that qualification, public runtime logic matched the tested dev version. Its separate wheel passed
installation, CLI/MCP, CPU closure and frozen-bundle checks. These results cover
the recorded configurations; they do not establish scientific effectiveness,
skill-quality improvement, every model/topology or long-duration reliability.
