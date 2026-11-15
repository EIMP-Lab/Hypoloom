# Readable engineering inputs and retained evidence

The November 15 live audit reproduced three independent problems: verifier
inputs containing 116–152 KB single-line JSON exceed native Read limits;
four valid engineering records of 6,196–7,986 bytes are all discarded by a
6 KiB document cap; training retains repeated feature reads and scalar losses.
The user authorizes design and implementation. Retained research remains frozen.

## Boundary and smallest change

ACI 38 selects a new presentation policy, with existing ACI 37 requests still
readable. No database, approval authority, digest or scientific gate is added.
Existing captured inputs, source identities, Process observations and independent
review remain authoritative. Git and ordinary tests preserve code, but cannot
make an escaped single-line source accessible to a line-oriented reader.

Generate deterministic, read-only text pages beside each original input in the
existing per-attempt input directory. Advertise an index containing source paths,
JSON field names and original line offsets. Extract source/log strings rather
than only pretty-printing their JSON envelope. Each page is at most 12,000 bytes;
the original bytes and evidence references remain unchanged. Source paths are
labels, never filesystem destinations. Existing exclusive writes and byte
comparison enforce retries; no second persistence owner is introduced.

Separate engineering evidence storage (64 KiB, at most four complete records)
from inline reading (6 KiB). Larger documents use the same readable pages.
New-policy requests explicitly carry omission counts even when all records
exceed the storage bound. No partial check, timing or coverage becomes a claim.
Old-policy requests retain the 6 KiB limit and original empty behavior. The
Oracle continues resolving every included claim against the actual records.

The cost is a small stdlib projection module used by both materialization and
prompt rendering. It can be removed when native tools provide bounded JSON
field/source reads. It has no independent authority or model-generated summary.

## Training work

Use an isolated ERC checkout. Preserve loss formulas while vectorizing SWFC,
cache deterministic feature arrays under an explicit per-dataset memory bound,
and defer scalar/metric transfers to epoch boundaries. For model-owned training
loss with logits-only evaluation, report cross-entropy as the validation loss
instead of the zero training placeholder. This corrects scheduler input and
must be applied symmetrically in a new experiment revision. Current paired
experiments and their recorded results are not modified.

## Validation

Cover long/multibyte lines, lossless page reconstruction, deterministic retries,
original references, native read-only mounts, prompt size, retained ACI behavior,
oversized valid memory and explicit omission. Replay the private captured inputs
without publishing their contents. Training checks compare values, gradients,
optimizer updates, cached batches and mutation isolation; measure real feature
loader costs separately from training. Use offline and native CPU lanes; no new
paid-model qualification or running-GPU interference is claimed.
