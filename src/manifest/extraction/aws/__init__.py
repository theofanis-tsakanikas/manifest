"""The managed reader adapters — written, schema-tested, never called.

**Still never called, and now that is a narrower statement than it used to be.** The estate has
been applied and documents have been through the deployed pipeline (`docs/DECISIONS.md` 14) —
but every page of it was read by the tier-0 local reader. Not one Textract, Bedrock Data
Automation or LLM request has been made from this repository, by CI or by hand.

So the limit stands exactly where it did: the response mapping is written against the
**documented** schema and tested against fixtures **authored from that schema** — labelled as
authored, in the fixture, in the test name and in the README, never described as captured. And
there is no accuracy figure for the fraction of pages the cascade escalates, because nothing
has ever read one.

The value of that is not the adapter. It is the evidence that the normalised representation is
real: `manifest.core` cannot tell which of these produced a value, and
`scripts/check_core_is_pure.py` fails if any of their names appears there.
"""
