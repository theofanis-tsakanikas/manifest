"""The managed reader adapters — written, schema-tested, never called.

`docs/DECISIONS.md` 14: nothing in this repository is ever applied to AWS, and no call is made
to any of these services. What is claimed is that the response mapping is written against the
**documented** schema and tested against fixtures **authored from that schema** — labelled as
authored, in the fixture, in the test name and in the README, never described as captured.

The value of that is not the adapter. It is the evidence that the normalised representation is
real: `manifest.core` cannot tell which of these produced a value, and
`scripts/check_core_is_pure.py` fails if any of their names appears there.
"""
