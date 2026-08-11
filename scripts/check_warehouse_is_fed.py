#!/usr/bin/env python3
"""Every column the marts read has something that writes it.

**The gap this exists to make impossible to forget.** `infra/lakehouse` creates a Glue catalogue
over the records zone and `infra/analytics` creates a Redshift workgroup with a role that can
read that catalogue. Both applied cleanly on 2026-08-11. Neither has ever contained a row.

Nothing in this repository writes the Iceberg table. The pipeline publishes a JSON record per
document to `records/{id}/{version}.json`, and no step converts those into the lake's location
or loads them into the warehouse. `scripts/check_marts.py` proves the marts only read columns
`analytics/schema.sql` declares — which is true, useful, and says nothing about whether a single
one of them has a source.

So this asks the next question: for every column `gold.published_field` declares, is there a
column in the Glue catalogue it could come from? Three answers, and they are worth telling apart.

**Present in the lake.** It can be loaded; nothing more is needed than the load itself.

**Derivable.** `reader_tier` is not in the lake, but the lake's `reader` string carries the
identity a tier maps to. Named here so that "derivable" is a claim somebody wrote down rather
than an assumption a loader made silently.

**Unsourced.** Nothing anywhere produces it. `carrier` and `client_id` are facts about a
shipment that the extraction pipeline never sees — they belong to a customer system this project
does not have. A loader that filled them would be inventing them, which is doctrine rule 3 with
a warehouse attached.

The failure this prevents is specific and expensive: standing the analytics layer up, running
the marts, getting rows back, and believing them — because a mart that reads an invented column
returns a number with the shape of an answer.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "analytics" / "schema.sql"
LAKE = ROOT / "infra" / "lakehouse" / "main.tf"
ACCEPTANCE = ROOT / "contracts" / "analytics" / "acceptance.yaml"


def _warehouse_columns() -> dict[str, set[str]]:
    """Each `CREATE TABLE` in the warehouse schema, and the columns it declares."""
    text = SCHEMA.read_text(encoding="utf-8")
    tables: dict[str, set[str]] = {}
    for match in re.finditer(
        r"CREATE TABLE IF NOT EXISTS (\S+) \((.*?)\n\);", text, re.DOTALL | re.IGNORECASE
    ):
        name = match.group(1)
        columns = {
            line.strip().split()[0]
            for line in match.group(2).splitlines()
            if line.strip()
            and not line.strip().startswith("--")
            and not line.strip().upper().startswith(("PRIMARY", "FOREIGN", "UNIQUE", "CONSTRAINT"))
        }
        tables[name] = columns
    return tables


def _lake_columns() -> set[str]:
    """Every column the Glue catalogue declares, across its tables."""
    text = LAKE.read_text(encoding="utf-8")
    return set(re.findall(r'columns \{\s*name\s+= "([^"]+)"', text))


def _classify(
    table: str, column: str, lake: set[str], derivable: dict[str, str], accepted: dict[str, str]
) -> tuple[str | None, str | None]:
    """Where this column would come from, or the complaint about it. One or the other."""
    if column in lake:
        return "in the lake", None
    if column in derivable:
        # **A derivability claim must name the lake column it comes from.**
        #
        # Without this the two lists are the same list: anything moved into `derivable` passes,
        # and `gate-proof` proved it by moving `carrier` there with the description "the bill of
        # lading, probably". Every entry must name at least one real lake column in backticks, so
        # the arithmetic is something a reader can check rather than a word somebody chose.
        if set(re.findall(r"`([a-z_]+)", derivable[column])) & lake:
            return "derivable", None
        return None, (
            f"{table}.{column} is declared derivable and its description names no column that "
            f"exists in the lake ({derivable[column]!r}). Derivable means 'computed from "
            f"something that is there' — without a source named, it is the unsourced list with "
            f"a friendlier heading"
        )
    if column in accepted:
        return "unsourced and declared", None
    return None, (
        f"{table}.{column} has no column in the Glue catalogue and is not declared in "
        f"{ACCEPTANCE.relative_to(ROOT)}. Either something must write it, or it is a fact this "
        f"pipeline never sees and saying so is the honest answer — but a loader that filled it "
        f"would be inventing it"
    )


def main() -> int:
    if not SCHEMA.exists():
        print(f"{SCHEMA} does not exist", file=sys.stderr)
        return 1

    accepted: dict[str, str] = {}
    derivable: dict[str, str] = {}
    if ACCEPTANCE.exists():
        loaded = yaml.safe_load(ACCEPTANCE.read_text(encoding="utf-8")) or {}
        accepted = dict(loaded.get("unsourced") or {})
        derivable = dict(loaded.get("derivable") or {})

    lake = _lake_columns()
    problems: list[str] = []
    counts = {"in the lake": 0, "derivable": 0, "unsourced and declared": 0}

    for table, columns in _warehouse_columns().items():
        for column in sorted(columns):
            where, complaint = _classify(table, column, lake, derivable, accepted)
            if where:
                counts[where] += 1
            if complaint:
                problems.append(complaint)

    print("warehouse columns, by where they would come from:")
    for label, count in counts.items():
        print(f"  {count:3}  {label}")

    if problems:
        print(f"\nwarehouse: {len(problems)} column(s) with no source\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    if counts["unsourced and declared"]:
        print(
            f"\n  {counts['unsourced and declared']} column(s) have no source anywhere and are "
            f"declared as such. The marts that read them return a shape, not an answer, until a "
            f"system that knows those facts is connected — see "
            f"{ACCEPTANCE.relative_to(ROOT)}."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
