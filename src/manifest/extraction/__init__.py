"""Engine adapters — the only place that knows which reader produced a value.

`local/` holds the tier-0 reader, which actually runs. `aws/` holds the managed adapters, which
are written against the documented response schema, tested against authored fixtures, and never
called (`docs/DECISIONS.md` 14).

An adapter's whole job is to produce a `manifest.core.document.ReadDocument` and to refuse
loudly when it cannot. Everything a reader does that is peculiar to it is handled here or
declared as a field in the normalised representation; there is no third option, and
`manifest.gates.core_purity` is what makes that true rather than aspirational.
"""
