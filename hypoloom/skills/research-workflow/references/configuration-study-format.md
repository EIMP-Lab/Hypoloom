# Finite study input format

Use `configuration_study.py --help` for arguments. All files are JSON; unknown
fields, duplicate JSON keys and nonfinite values are errors. Exit 0 means the
documents are consistent, including an incomplete study. Inspect coverage and
selection status; exit 0 is not scientific acceptance. Exit 2 reports invalid
input or inconsistent evidence on stderr. Input files are never changed.

The plan is ordinary admitted source, not a new Host contract. These example
values illustrate the format; they are not recommended hyperparameters:

```json
{
  "schema_version": 1,
  "datasets": ["corpus"],
  "seeds": [0, 1],
  "metric": "accuracy",
  "direction": "maximize",
  "dataset_weights": {"corpus": 1},
  "max_trials": 3,
  "shared_architecture": true,
  "shared_training": true,
  "context": {
    "implementation_ref": "git:accepted-implementation",
    "data_refs": {"corpus": "artifact:accepted-split-manifest"}
  },
  "candidates": [{
    "id": "reference",
    "rationale": "Reproduce the documented baseline before changing settings.",
    "configs": {"corpus": {
      "architecture": {"layers": 2},
      "training": {"batch_size": 16, "learning_rate": 0.001},
      "adaptation": {"classes": 4},
      "runtime": {"num_workers": 0}
    }}
  }],
  "evaluation": {"split": "test", "seeds": [0, 1, 2]}
}
```

Replace references with original evidence identities; the reader compares them
but does not fetch or authenticate their contents. Candidate IDs and seeds must
be unique. `max_trials` covers all search attempts, including failed/interrupted
retries, and must fit the full declared matrix. The existing Host resource and
wall budgets still apply; final evaluation is separate from this search ceiling.
All datasets have the same search seeds. Every candidate declares every dataset
and all four configuration dictionaries. Include all relevant resolved settings,
not only tuned ones. Dictionaries can contain nested JSON values. Sharing flags
compare whole architecture/training dictionaries within each candidate. Adaptation
and runtime may differ across datasets. Types matter recursively (`true`, `1`
and `1.0` differ); dictionary key order does not. These categories are declarations,
not an automatic classification of the training code's parameters.

`trials.json` is an array of observations. One completed item looks like:

```json
{
  "candidate_id": "reference", "dataset": "corpus", "seed": 0,
  "attempt": 1, "status": "completed",
  "evidence_ref": "artifact:original-trial-output",
  "resolved_config": {
    "architecture": {"layers": 2},
    "training": {"batch_size": 16, "learning_rate": 0.001},
    "adaptation": {"classes": 4},
    "runtime": {"num_workers": 0}
  },
  "implementation_ref": "git:accepted-implementation",
  "data_ref": "artifact:accepted-split-manifest",
  "metric": "accuracy", "split": "validation", "value": 0.7,
  "duration_seconds": 12.5
}
```

Failed/interrupted items require the six identity/status/reference fields and
a nonempty `reason`. They may retain any already observed configuration,
implementation/data reference, metric/split and duration; they cannot supply a
ranking `value`. Duration is optional and nonnegative. Unrun work has no row and
appears in `missing`. Attempt numbers start at 1 for each candidate/dataset/seed;
retries retain contiguous attempt history, including earlier failures. A completed
trial cannot be repeated or counted twice. Array order does not affect validation.

Only candidates completing every declared seed on every dataset enter `ranking`.
The score is the weighted mean of per-dataset seed means, with positive explicit
weights normalized by their sum. Choose commensurate metrics/weights before
running; the reader cannot establish their scientific appropriateness. Direction
is `maximize` or `minimize`, and exact ties retain candidate order in the plan.
Per-dataset coverage and means are always shown, including incomplete candidates.
The tool does not infer per-goal success from this ranking.

`selected.json` contains exactly `candidate_id` and `configs`, copied from the
recommended candidate. The reader verifies both. A complete matrix yields
`selection.status = "frozen"`; otherwise it is `"provisional"`, best only among
completed candidates. The status describes supplied evidence, not an atomic
write or attestation that selection predates evaluation. Preserve plan and
selection using the existing admitted source/Git and artifact path.

`evaluation.json` uses the same observation format with only the selected
candidate, `plan.evaluation.seeds` and `plan.evaluation.split` (`test` or
`validation`). It requires both the optional plan evaluation section and a
selection. Configurations must match exactly. The report shows final missing
cells and counts; final values never enter the ranking. Existing task parsers
own final metric aggregation and acceptance. This helper does not implement
adaptive pruning, a search scheduler, provenance attestation or a goal verdict.

## Ordered finite stages

`--stages stages.json` replaces the separate CLI inputs. Supply an array of
objects with `name`, `basis`, `plan`, `trials`, and optional `selection` and
`evaluation`, each using the formats above. A later stage can add screening seeds
and retain a subset of complete prior candidates. Scientific configs, source/data,
metrics, sharing rules and final protocol must match. Final observations cannot
be followed by another selection stage. Reused observations still need their
original evidence references and matching context; this reader does not execute
or authenticate reuse. See [incremental execution](incremental-execution.md).
