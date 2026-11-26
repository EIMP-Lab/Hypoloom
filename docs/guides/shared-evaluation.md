# Reusing a shared GOAL evaluator

English · [简体中文](shared-evaluation.zh-CN.md)

## Known answers for a LOCAL measurement

From ACI 45, each non-null local measurement needs a small example with a known result and at least one variant with different expected observations. Put the manifest in `/workspace/work/measurement-cases.json`. For a staged metric `score` with unit `dev` and comparison `DELTA_FROM_BASELINE`, this manifest checks both directions.

```json
{
  "baseline": {"observation": {"metric": "score", "observations": {"dev": {"value": 0.75, "baseline": 0.50}}}, "metric_value": 0.25},
  "lower":    {"observation": {"metric": "score", "observations": {"dev": {"value": 0.25, "baseline": 0.50}}}, "metric_value": -0.25}
}
```

Supply every declared raw file under `/workspace/smoke/` for the case named `baseline` and `/workspace/work/cases/<name>/` for each variant. Include 2–8 cases in total. Use observations with independently known readings. For delta comparisons, `value` and `baseline` are absolute readings. The Host applies the comparison and the declared reduction to compute `metric_value`. Design checks the draft. Experiment submission repeats the check on the captured parser and examples before preregistration. Each check shares a 15-second CPU limit across its cases. GOAL-only submissions with a null local measurement use their shared evaluator.

## Shared GOAL criteria and diagnostics

In ACI 43, Grounding can publish report-only fields alongside the shared criteria and validity checks. A diagnostic can be false or nonzero while a candidate meets the goal. The parser emits each declared field using its exact name and type. Oracle replays the same parser from the published raw evidence.

| Declaration field | Parser output | Effect |
| --- | --- | --- |
| Numeric criterion | `measurements` | Compared with the declared target and operator |
| `IS_TRUE` criterion | `checks` | Combined with the other criteria by `ALL` or `ANY` |
| `validity_checks` | `checks` | Every check must hold for a qualifying observation |
| `diagnostics: {"cache_matches": "BOOLEAN", "offset": "DECIMAL"}` | `diagnostics` | Recorded for review |

### Run paired examples during Grounding

In ACI 44, Grounding and Design can check a complete draft before submitting it. Save the draft in the authoring workspace and run `python3 /runtime/native_output.py --check-draft PATH`. The Host checks the same schema, references and applicable CPU fixtures used at submission. A failed check reports the field or case and leaves the formal correction allowance available. Submit the final JSON through the usual Stop hook after editing.

For a stage with a progress window, the first successful schema check, candidate check, additional passing parser cases and completed draft can renew the window. Each milestone counts once per attempt. The stage and run deadlines bound all renewals. The attempt details show the effective limits, their source, the last verified check and outstanding validation error. API request and training timeouts are independent. [Budget settings](../../config/README.md)

To compare runs, use `overview.totals.experiments.first_started_at` and `time_to_first_start_seconds`. Both stay null until a preregistered execution has a bound Process. The elapsed time uses the start of that Process attempt. Each authoring attempt also records `authoring.repeated_errors` when the same validation error recurs on consecutive checks.

Use small synthetic or public examples with known answers. The default fixture is `/acceptance/smoke/<raw_artifact>`. Add an optional `/acceptance/work/acceptance-cases.json` containing the full expected parser observation and expected verdict for each case. This example requires a positive score and a complete sample set.

```json
{
  "baseline": {"observation": {"measurements": {"score": 1}, "checks": {"complete": true}, "diagnostics": {"cache_matches": true}}, "goal_met": true},
  "cache_only": {"observation": {"measurements": {"score": 1}, "checks": {"complete": true}, "diagnostics": {"cache_matches": false}}, "goal_met": true},
  "missing_sample": {"observation": {"measurements": {"score": 1}, "checks": {"complete": false}, "diagnostics": {"cache_matches": true}}, "goal_met": false}
}
```

Provide every declared raw artifact for each variant under `/acceptance/work/cases/<case>/`. The Host runs the parser in a fresh CPU sandbox per case, with a shared 15-second ceiling and the Grounding byte budget. A mismatch identifies the failing case during the existing correction phase. Keep fixtures outside `parser_files`.

### Reserve data for the formal GOAL Process

In a v4 Profile with `evaluation: grounding`, list training and development directories under `safety.yaml`'s `allow_read_paths`. Use `evaluation_only_read_paths` for separate held-out files or directories.

```yaml
allow_read_paths: [/datasets/study/development]
evaluation_only_read_paths: [/datasets/study/holdout]
```

The formal GOAL raw command gets the held-out inputs at `/hypoloom-evaluation/1`, `/hypoloom-evaluation/2` and so on, in declaration order. It also receives these aliases in `HYPOLOOM_READ_ROOTS`. Design, LOCAL, implementation probes and their entrypoint samples use development inputs. Parsers receive their declared raw artifacts. Admission checks canonical paths and overlaps with other mounted inputs, the target checkout, runtime and private files. When one file contains multiple splits, prepare separate split-specific files or views before admission.

### Seed a later run from a reviewed protocol

Export the source from an accepted Grounding Attempt and supply its labeled example directory. The fixtures directory contains `smoke/`, `work/acceptance-cases.json` and `work/cases/`. The exporter replays the examples before creating a new directory. Use a protocol identifier that covers the reference data, split, rules and evidence format. Represent candidate identity in each candidate's raw evidence.

```bash
python -m hypoloom.evaluator_examples --state-root "$PRIOR_STATE" --run-id "$PRIOR_RUN" \
  --fixtures "$EXAMPLES" --protocol study-v1 --output "$TARGET/protocol-v1"
```

Review and commit the exported directory in the research target, then set the next Profile's `goal.yaml` fields.

```yaml
evaluation: grounding
evaluator_seed:
  path: protocol-v1
  protocol: study-v1
```

Admission reads the selected files from the target's bound Git commit. The new Grounding workspace starts with these source files and examples. Retries preserve draft edits. A final submission must preserve the selected protocol's criteria, validity checks, diagnostics and raw input names, and pass its pinned examples even if the draft examples change. Grounding then publishes the evaluator with a new source Attempt. For a changed scientific question or split, select a matching protocol version or start Grounding with a fresh evaluator.

### Run a pinned development sample before GOAL evaluation

A v4 Profile using Grounding can bind a Python entrypoint from the research target. Compute its digest with `git show HEAD:scripts/goal_experiment.py | sha256sum` in that repository and record the result in `goal.yaml`.

```yaml
evaluation: grounding
preflight:
  path: scripts/goal_experiment.py
  sha256: "<64 lowercase hexadecimal digits>"
  timeout_seconds: 120
```

The Host verifies the file against the bound Git commit on admission. In design and submission, use `python /input/scripts/goal_experiment.py` followed by the candidate's arguments and capture the entrypoint bytes along with its inputs. The Host appends `--preflight` only for the development sample. The template replaces the author-supplied `entrypoint_smoke` for these GOAL submissions.

The sample uses the same candidate capture, interpreter and resource grant in a fresh sandbox with a separate writable `/output`. It receives public and declared reuse inputs. The Host reserves `evaluation_only_read_paths` for formal execution. The sample shares the outer Attempt deadline and has its own ceiling of 1–300 seconds. A template should exercise the candidate on bounded development data, write the GOAL parser's declared raw filenames and finish with a regular `/output/preflight.json` containing `{"passed":true}` and any small engineering diagnostics (64 KiB total). Sample files stay in the sample directory. Only the formal raw outputs go to the scoring parsers.

On success the publication contains `preflight/preflight.json` and the stage logs. On failure the Host skips formal execution, records the engineering failure `goal_preflight_failed` (driver exit 74) and retains `preflight/stderr.log` for repair. LOCAL experiments and profiles without a preflight keep their existing execution path. The stage runs independently of the optional implementation verifier.
