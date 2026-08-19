"""The managed reader adapters — written, schema-tested, and now called.

**All three have been called, and the limit that matters survived it.** Textract read 2,336
eligible pages on 2026-08-15 and its normalised output is committed to `recordings/textract/`;
Bedrock Data Automation answered on 2026-08-13 and returned a per-word confidence its published
schema says it does not return; the multilingual model was called on 2026-08-15 and reports no
confidence at all.

So the sentence that used to say "never called" is gone, and the one that mattered is unchanged:
**there is still no accuracy figure for the fraction of pages the cascade escalates.** A call is
not a measurement. Nothing has scored what an upper tier read against ground truth, so
*"accuracy held at X for Y% of the cost"* remains unavailable here and does not appear.

The fixtures stay **authored** from the documented schema — deliberately, now that the choice is
one rather than a fact about what has happened. A recording carries real confidences for
threshold derivation; a fixture tests the adapter against the shape the documentation declares,
and replacing it with a capture would delete the only check that the documentation was read
correctly. `tests/extraction/fixtures/AUTHORED.md` states which is which.

The value of that is not the adapter. It is the evidence that the normalised representation is
real: `manifest.core` cannot tell which of these produced a value, and
`scripts/check_core_is_pure.py` fails if any of their names appears there.
"""
