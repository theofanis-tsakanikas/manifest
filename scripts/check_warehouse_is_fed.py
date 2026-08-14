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

So this asks the next question: for every column the warehouse declares, is there something that
could produce it? Four answers, and they are worth telling apart.

**Present in the lake.** It can be loaded; nothing more is needed than the load itself.

**Derivable.** `reader_tier` is not in the lake, but the lake's `reader` string carries the
identity a tier maps to. Named here so that "derivable" is a claim somebody wrote down rather
than an assumption a loader made silently.

**Sourced outside the lake.** The lake is not the only thing this estate records. A human's
decision lives in DynamoDB, written by `handlers/decide.py` and read back by the loader — so the
column has a source, and naming the file that writes it is what keeps that from being a word
somebody chose.

**Unsourced.** Nothing anywhere produces it. `carrier` and `client_id` are facts about a
shipment that the extraction pipeline never sees — they belong to a customer system this project
does not have. A loader that filled them would be inventing them, which is doctrine rule 3 with
a warehouse attached.

The failure this prevents is specific and expensive: standing the analytics layer up, running
the marts, getting rows back, and believing them — because a mart that reads an invented column
returns a number with the shape of an answer.

**And the same failure in reverse, which is the one that happened.** Everything above reads the
schema, the catalogue and the acceptance, and for a long time none of it read the *loader*. So
ten columns sat in the unsourced list while the loader filled them, the marts reported that
nobody had reviewed anything, and the contract agreed. `_loader_writes` closes that by parsing
the loader itself: a column declared unsourced that the loader writes a value for is a
disagreement, and the loader is the one that runs.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "analytics" / "schema.sql"
LAKE = ROOT / "infra" / "lakehouse" / "main.tf"
LOADER = ROOT / "scripts" / "load_warehouse.py"

#: `_batched(table, columns, values)`. Named so the arity check below is a statement about that
#: signature rather than a number somebody has to go and look up.
_BATCHED_ARGUMENTS = 3
ACCEPTANCE = ROOT / "contracts" / "analytics" / "acceptance.yaml"


def _warehouse_columns() -> dict[str, set[str]]:
    """Each `CREATE TABLE` in the warehouse schema, and the columns it declares."""
    text = SCHEMA.read_text(encoding="utf-8")
    tables: dict[str, set[str]] = {}
    for match in re.finditer(
        # `IF NOT EXISTS` optional: the schema stopped using it, because that form is idempotent
        # and not evolutionary — a column change is silently ignored on a warehouse that already
        # exists. This check then found **zero** tables and printed `0 in the lake, 0 derivable,
        # 0 unsourced`, which reads as a clean bill and is a parser that matched nothing.
        r"CREATE TABLE (?:IF NOT EXISTS )?(\S+) \((.*?)\n\);",
        text,
        re.DOTALL | re.IGNORECASE,
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


def _loader_writes() -> dict[str, set[str]]:
    """Each warehouse table the loader inserts into, and the columns it writes a value for.

    **The question this file was not asking.** Everything above compares the *schema* against the
    *lake* against the *acceptance*, and never once reads the loader — so a column could be
    declared "nothing anywhere produces it" while the loader had been filling it for weeks, and
    nothing would go red. That is what happened: `item_id`, `queued_on`, `queued_reason` and the
    two modelled-cost columns are loaded and were still listed as unsourced, and the drift was
    invisible because the check read two files and the truth was in a third.

    `docs/DECISIONS.md` 24 by name — *the failure this project produces most is a check reading
    the wrong thing* — and the habit it prescribes: parse the thing, do not match its shape. So
    the loader is parsed. Each `_batched(table, "a, b, c", values)` call is paired with the list
    comprehension that built `values`, whose element is `"(" + ", ".join((...)) + ")"` — a tuple
    of one expression per column, positionally. A column whose expression is the bare literal
    `"NULL"` is one the loader deliberately does not write; anything else is a value.
    """
    tree = ast.parse(LOADER.read_text(encoding="utf-8"))

    # The value tuples, by the name each comprehension was assigned to.
    tuples: dict[str, list[ast.expr]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.ListComp):
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        for inner in ast.walk(node.value.elt):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "join"
                and inner.args
                and isinstance(inner.args[0], ast.Tuple)
            ):
                tuples[target.id] = list(inner.args[0].elts)
                break

    written: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_batched"
            and len(node.args) == _BATCHED_ARGUMENTS
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[2], ast.Name)
        ):
            continue
        columns = [name.strip() for name in _joined(node.args[1]).split(",") if name.strip()]
        values = tuples.get(node.args[2].id)
        if values is None or len(values) != len(columns):
            # Refused rather than skipped. A column list this cannot pair with its values is a
            # loader shape this parser no longer understands, and returning what it managed to
            # read would report a clean warehouse because it stopped looking.
            raise SystemExit(
                f"{LOADER.relative_to(ROOT)}: could not pair {node.args[0].value}'s "
                f"{len(columns)} column(s) with the values that fill them. This check parses the "
                f"loader rather than trusting it, and it has stopped being able to"
            )
        written[str(node.args[0].value)] = {
            column
            for column, value in zip(columns, values, strict=True)
            if not (isinstance(value, ast.Constant) and value.value == "NULL")
        }
    return written


def _joined(node: ast.expr) -> str:
    """A string literal that may have been written across several adjacent lines."""
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _joined(node.left) + _joined(node.right)
    raise SystemExit(f"{LOADER.relative_to(ROOT)}: a column list this check cannot read")


def _classify(
    table: str,
    column: str,
    *,
    lake: set[str],
    derivable: dict[str, str],
    elsewhere: dict[str, str],
    accepted: dict[str, str],
) -> tuple[str | None, str | None]:
    """Where this column would come from, or the complaint about it. One or the other."""
    if column in lake:
        return "in the lake", None
    if column in elsewhere:
        # **The same guard `derivable` has, pointed at a different kind of source.** The lake is
        # not the only thing this estate writes — the decisions table holds what a human decided,
        # and that is the record claim 5 is computed from. Saying so has to be as checkable as
        # saying a column is derivable, or "sourced elsewhere" becomes the unsourced list with a
        # third friendly heading: the description must name a file in this repository that
        # produces it, and the file must exist.
        named = [Path(ROOT, found) for found in re.findall(r"`([\w./-]+\.\w+)`", elsewhere[column])]
        if any(path.exists() for path in named):
            return "sourced outside the lake", None
        return None, (
            f"{table}.{column} is declared as sourced outside the lake and its description names "
            f"no file in this repository that exists ({elsewhere[column]!r}). A source nobody "
            f"can open is a source nobody can check"
        )
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
    elsewhere: dict[str, str] = {}
    if ACCEPTANCE.exists():
        loaded = yaml.safe_load(ACCEPTANCE.read_text(encoding="utf-8")) or {}
        accepted = dict(loaded.get("unsourced") or {})
        derivable = dict(loaded.get("derivable") or {})
        elsewhere = dict(loaded.get("sourced_elsewhere") or {})

    lake = _lake_columns()
    problems: list[str] = []
    counts = {
        "in the lake": 0,
        "derivable": 0,
        "sourced outside the lake": 0,
        "unsourced and declared": 0,
    }

    for table, columns in _warehouse_columns().items():
        for column in sorted(columns):
            where, complaint = _classify(
                table,
                column,
                lake=lake,
                derivable=derivable,
                elsewhere=elsewhere,
                accepted=accepted,
            )
            if where:
                counts[where] += 1
            if complaint:
                problems.append(complaint)

    # **The reverse question, asked of the loader itself.** A column declared unsourced and then
    # quietly filled is the acceptance under-claiming what the system does — the same defect as
    # over-claiming, pointed the other way, and the one that makes a mart look emptier than it is.
    fed = _loader_writes()
    for table, columns in sorted(fed.items()):
        for column in sorted(columns & set(accepted)):
            problems.append(
                f"{table}.{column} is declared unsourced in "
                f"{ACCEPTANCE.relative_to(ROOT)} — {accepted[column]!r} — and "
                f"{LOADER.relative_to(ROOT)} writes a value for it. One of the two is wrong, and "
                f"the loader is the one that runs"
            )

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
