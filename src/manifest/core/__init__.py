"""The engine-free core.

Pure functions over plain data structures. No cloud SDK, no extraction engine, no clock, no
filesystem, no randomness — and no engine named anywhere, not even in a comment. The rule and
the reasoning are in `manifest.gates.core_purity`; the gate that enforces it runs in CI, in
`make preflight`, and is attacked by three mutations in `scripts/gate_proof.py`.

This is the only reason claims 1 to 6 can be checked on a laptop by a stranger with no
account. Every line that leaves this package is a line of that evidence given back.
"""
