# Verifier completion and evidence inputs

Native Verifier sessions sometimes submitted an absent probe as the string
`"null"` or serialized a complete probe object as a tool argument string.
After the last permitted correction the CLI could keep requesting model turns
until its deadline. Review inputs also embedded the current probe twice.

ACI 41 changes the existing native tool projection. A verdict that requests
a probe requires an object. Other verdicts default to null. At preparation the
Host can unwrap one strictly decoded object string or the literal `"null"` for
a verdict that takes no probe. An actual probe attached to an incompatible
verdict is rejected. The full response schema, semantic validation, session
binding and final Stop check continue to enforce admission. ACI 40 retains
its previous tool interface.

The validator already owns the correction counter and permanent endpoint error.
The process owner checks them during poll and wait. Exhaustion closes the attempt
as an invalid candidate and preserves the last field error. A permanently
unavailable endpoint retains its transport failure classification. Normal
process cleanup owns descendants, relay shutdown and available accounting.
The implementation uses the existing attempt lifecycle and correction budget.

The evidence overview shows the recorded status for each planned check. Its
full observation supplies details, errors and logs. The bound report reference
identifies exactly which historical probe already appears in the current plan.
Other reports keep their findings and distinct plans. This uses the existing
capture and references, with no new durable state. The tool projection can be
retired for future runs if native completion reliably supplies the typed fields.

The workflow-core budget stays at 27,550 physical lines. The review contract
and input views each receive 25 unused lines from expert memory.
