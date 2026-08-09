"""The contract layer — the source of truth, as data.

Contracts are YAML under `contracts/`. Nothing imports one by name; the set is loaded and
cross-checked together, which is what makes adding a document type a change to a directory
rather than to a code path.
"""
