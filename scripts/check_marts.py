#!/usr/bin/env python3
"""Every mart reads columns the warehouse actually declares.

A query that invents a column is a query that fails at minute forty of a deploy, and the only
place to catch it without a warehouse is here. `analytics/schema.sql` is the declaration;
every `.sql` beside it is checked against it.

**What this is and is not.** It is a reference check: every `schema.table` a mart names exists,
and every bare identifier it uses is a column of one of the tables it reads. It is not a SQL
parser and it does not know Redshift's dialect — `terraform validate`'s limitation, one layer
up, and stated for the same reason. What it catches is the whole class of failure that
otherwise arrives in production: a renamed column, a table that moved schema, a mart written
against a design that changed.

It errs toward silence on things it cannot resolve rather than toward noise. A checker that
reports a false finding on every alias is a checker somebody stops running, and then the real
finding goes with it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANALYTICS = ROOT / "analytics"
SCHEMA = ANALYTICS / "schema.sql"

#: SQL keywords and functions a mart uses. Matched as identifiers by the naive scan below and
#: excluded here, because the alternative is a SQL grammar and this is a reference check.
#:
#: A block of words rather than a list of quoted strings: the point of this set is that a reader
#: can scan it for something that should not be there, and seventy quoted strings on one line is
#: not scannable. `noqa` rather than a rewrite for the same reason.
_NOT_COLUMNS = frozenset(
    """
    select from where group by order having as on and or not in is null case when then else
    end count sum avg min max median distinct left right coalesce nullif cast desc asc with
    union all join inner outer full cross limit offset over partition between like true
    false boolean integer decimal varchar date smallint create schema if exists table
    primary key references default now current_date interval extract to_char date_trunc
    """.split()  # noqa: SIM905
)

_TABLE = re.compile(r"\b(\w+)\.(\w+)\b")
_IDENTIFIER = re.compile(r"\b[a-z_][a-z0-9_]*\b")


def _declared() -> dict[str, set[str]]:
    """`schema.table -> columns`, read from the DDL."""
    text = SCHEMA.read_text(encoding="utf-8")
    tables: dict[str, set[str]] = {}
    for match in re.finditer(
        r"CREATE TABLE IF NOT EXISTS\s+(\w+\.\w+)\s*\((.*?)\n\);", text, re.DOTALL
    ):
        name, body = match.group(1), match.group(2)
        columns = set()
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            first = stripped.split()[0].strip(",")
            if first.lower() not in _NOT_COLUMNS:
                columns.add(first.lower())
        tables[name.lower()] = columns
    return tables


def _strip_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def check(path: Path, tables: dict[str, set[str]]) -> list[str]:
    sql = _strip_comments(path.read_text(encoding="utf-8"))
    problems: list[str] = []

    referenced = {
        f"{schema}.{table}".lower()
        for schema, table in _TABLE.findall(sql)
        if f"{schema}.{table}".lower() in tables or schema.lower() in {"gold", "silver", "bronze"}
    }
    unknown = referenced - set(tables)
    for name in sorted(unknown):
        problems.append(f"reads {name}, which analytics/schema.sql does not declare")

    known = referenced & set(tables)
    if not known:
        problems.append("reads no declared table at all; this mart cannot be checked")
        return problems

    available = set().union(*(tables[name] for name in known))
    # Aliases a mart introduces with `AS`. They are outputs, not columns of a source, and
    # flagging them is the false finding that would get this script switched off.
    aliases = {match.lower() for match in re.findall(r"\bAS\s+(\w+)", sql, re.IGNORECASE)}

    for identifier in sorted(set(_IDENTIFIER.findall(sql))):
        if (
            identifier in _NOT_COLUMNS
            or identifier in available
            or identifier in aliases
            or identifier in {name.split(".")[0] for name in known}
            or identifier in {name.split(".")[1] for name in known}
            or identifier.isdigit()
        ):
            continue
        problems.append(
            f"uses {identifier!r}, which is not a column of {sorted(known)} and not an alias "
            f"this query defines"
        )
    return problems


def main() -> int:
    if not SCHEMA.exists():
        print(f"{SCHEMA} does not exist", file=sys.stderr)
        return 1
    tables = _declared()
    marts = sorted(path for path in ANALYTICS.glob("*.sql") if path.name != "schema.sql")
    if not marts:
        print(
            "no marts. docs/DECISIONS.md 6: if these are not built, Redshift leaves the "
            "project rather than staying as a keyword",
            file=sys.stderr,
        )
        return 1

    failures = 0
    for mart in marts:
        problems = check(mart, tables)
        print(f"  {mart.name:<34} {'ok' if not problems else 'FAIL'}")
        for problem in problems:
            print(f"     {problem}", file=sys.stderr)
        failures += bool(problems)

    print(
        f"marts: {len(marts) - failures}/{len(marts)} read only columns "
        f"analytics/schema.sql declares, across {len(tables)} tables"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
