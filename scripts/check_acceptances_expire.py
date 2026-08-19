#!/usr/bin/env python3
"""Doctrine rule 6, applied to every acceptance in the repository rather than to some of them.

**Eight files carry an `expires_on`. On 2026-08-19, three of them were checked.** The other
five — `contracts/core/reachable.yaml`, `contracts/deploy/data_bearing.yaml`,
`contracts/deploy/budget.yaml`, `contracts/review/acceptance.yaml` and
`contracts/analytics/acceptance.yaml` — declared a date that nothing read. Each would have
passed it in silence, and the finding it defers would have stayed deferred, permanently, with
the file still saying otherwise.

The reason is worth stating because it is structural rather than careless. Each expiry was
enforced by whichever check happened to read that file for its own purpose: the deploy gate
checks the deploy acceptance, the envelope gate checks the corpus one. A file no check needed to
read got no enforcement, and the enforcement was never anybody's subject — it was a side effect
of being needed for something else.

Rule 6 belongs to the repository, not to whichever gate opens the file. So this walks every
`contracts/**/*.yaml`, finds every mapping that declares an expiry at any depth, and refuses:

  - an expiry that has passed — the exception is over and the finding returns;
  - an expiry that precedes its own acceptance date — a decision that was never in force;
  - an acceptance missing the fields that make it one: who, when, why, and what ends it.

**An acceptance nobody has to renew is not an exception; it is a change of policy that was
written as a temporary measure.** That is the move this file exists to make expensive.

    python3 scripts/check_acceptances_expire.py
"""

from __future__ import annotations

import datetime
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "contracts"

GREEN, RED, YELLOW, DIM, RESET = (
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[2m",
    "\033[0m",
)

#: What every acceptance must carry. `ends_when` is the one that stops an expiry being a
#: reminder to type a new date: it names the condition under which the exception is no longer
#: needed, so renewing it is a decision about the condition rather than about the calendar.
REQUIRED = ("accepted_by", "accepted_on", "expires_on", "ends_when")

#: An acceptance expiring within this many days is reported and does not fail. A rule that only
#: speaks on the day it breaks is a rule that breaks a build somebody needed that morning.
NOTICE_DAYS = 21


def _acceptances(node, source: pathlib.Path, path: str = ""):
    """Every mapping anywhere in the file that declares an expiry, with where it sits.

    Walked rather than looked up: the eight files put it in three different places — at the
    root, under `acceptance:`, and inside an `acceptances:` list — and a check that knew about
    two of those shapes would silently pass the third.
    """
    if isinstance(node, dict):
        if "expires_on" in node:
            yield node, f"{source.relative_to(ROOT)}{path}"
        for key, value in node.items():
            yield from _acceptances(value, source, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _acceptances(value, source, f"{path}[{index}]")


def _date(value) -> datetime.date | None:
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        return None


def main() -> int:
    today = datetime.date.today()
    problems: list[str] = []
    notices: list[str] = []
    found = 0

    for source in sorted(CONTRACTS.rglob("*.yaml")):
        loaded = yaml.safe_load(source.read_text(encoding="utf-8"))
        for entry, where in _acceptances(loaded, source):
            found += 1
            missing = [field for field in REQUIRED if not entry.get(field)]
            if missing:
                problems.append(
                    f"{where} declares an expiry and is missing {missing}. An acceptance "
                    f"without a name on it is a decision nobody made"
                )
                continue

            expires, accepted = _date(entry["expires_on"]), _date(entry["accepted_on"])
            if expires is None or accepted is None:
                problems.append(f"{where} has a date that is not a date (YYYY-MM-DD required)")
                continue
            if expires <= accepted:
                problems.append(
                    f"{where} expires on {expires}, on or before the {accepted} it was accepted. "
                    f"An exception that was never in force is not an exception"
                )
                continue
            if expires < today:
                problems.append(
                    f"{where} expired on {expires}. Doctrine rule 6: on expiry the finding "
                    f"returns and CI goes red. Either what it defers is done, or somebody "
                    f"accepts it again — by name, with a new date, having read `ends_when`"
                )
                continue
            if (expires - today).days <= NOTICE_DAYS:
                notices.append(f"{where} expires on {expires}, in {(expires - today).days} days")

    contracts = len(list(CONTRACTS.rglob("*.yaml")))
    print(f"acceptances: {found} declared across {contracts} contract(s)")
    for notice in notices:
        print(f"  {YELLOW}soon{RESET}  {notice}")
    if problems:
        for problem in problems:
            print(f"  {RED}refused{RESET}  {problem}")
        print(
            f"\n{DIM}  Rule 6 belongs to the repository, not to whichever gate happens to open "
            f"the file. Five of these eight were enforced by nobody until 2026-08-19.{RESET}"
        )
        return 1

    print(f"  {GREEN}ok{RESET}    every acceptance names who, when, why it ends, and is in date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
