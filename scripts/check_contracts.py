#!/usr/bin/env python3
"""Every contract loads, and the set holds together.

The runner for `manifest.contracts.loader`: `make contracts-validate`, a CI step and a
preflight check. The refusals it can produce are documented in the loader — a rule naming a
field nobody declared, a rule comparing two things that cannot disagree, a tolerance in the
wrong dimension. Each of those is otherwise discovered at run time, on a document, in front of
a customer.
"""

from __future__ import annotations

import sys

from manifest.contracts.loader import ContractError, default_root, load


def main() -> int:
    try:
        contracts = load(default_root())
    except ContractError as exc:
        print(f"contracts: {exc}", file=sys.stderr)
        return 1

    fields = sum(len(contract.fields) for contract in contracts.documents.values())
    always_review = contracts.always_review_fields
    print(
        f"contracts: {len(contracts.documents)} document types, {fields} fields, "
        f"{len(contracts.reconciliation.rules)} agreement rules, "
        f"{len(contracts.entities.rules)} match rules"
    )
    print(
        f"  always-review: {', '.join(f'{d}.{f}' for d, f in always_review) or 'none'} "
        f"— these consume 100% of their volume from the review queue (ADR-0001)"
    )
    print(f"  review capacity: {round(contracts.review.decisions_per_day)} decisions/day, declared")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
