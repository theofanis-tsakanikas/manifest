#!/usr/bin/env python3
"""Add to the Iceberg table the columns `core.lake.Row` declares and the table does not have.

**For a Glue-managed Iceberg table, the catalogue's column list is not the schema**, and editing
it is a no-op that looks exactly like a schema change. `aws_glue_catalog_table` accepted four new
`columns` blocks, `terraform apply` reported success, `aws glue get-table` listed them — and
every Athena query went on using the old schema, because Iceberg keeps its own metadata and that
is what the engine reads. The failure was `COLUMN_NOT_FOUND` in a loader, two layers and twenty
minutes later, about a column the catalogue said existed.

So the columns in `infra/lakehouse/main.tf` are the schema the table is **created** with, and
everything after that is evolution — which is what Iceberg is for and which goes through
`ALTER TABLE ADD COLUMNS`. This runs after the lakehouse applies and before anything reads.

**Idempotent by comparison, not by catching an error.** It asks the table what it has, adds what
is missing, and says so. A version that ran the `ALTER` and swallowed "column already exists"
would be indistinguishable from one that could not connect.

**It only ever adds.** Dropping or retyping a column is a re-extraction event under
`CLAUDE.md`'s contract rule — it needs a version bump and a diff report, not a deploy step. A
column this script would have to remove is a change that should stop and be looked at.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import fields as dataclass_fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

GREEN, DIM, RESET = "\033[32m", "\033[2m", "\033[0m"

#: How the row's Python types are written in the table. Only the ones this file may add; a type
#: missing here fails by name rather than by guessing, because guessing `string` for a number is
#: a column that accepts everything and sorts wrongly.
SQL_TYPE = {
    "str": "string",
    "str | None": "string",
    "int": "int",
    "int | None": "int",
    "float | None": "double",
    "bool": "boolean",
    "tuple[float, ...] | None": "array<double>",
}

#: Columns the table has that the row does not, and legitimately. `extraction_date` is derived in
#: the insert from `extracted_on` and partitions the table; it is not a field of a row.
DERIVED = frozenset({"extraction_date"})

STATEMENT_TIMEOUT_SECONDS = 180


def _client(name: str):
    import boto3  # noqa: PLC0415 - the offline suite imports this module without AWS

    return boto3.client(name)


def _run(query: str, database: str, workgroup: str) -> list[list[str]]:
    athena = _client("athena")
    started = athena.start_query_execution(
        QueryString=query,
        WorkGroup=workgroup,
        QueryExecutionContext={"Database": database},
    )["QueryExecutionId"]

    deadline = time.monotonic() + STATEMENT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        described = athena.get_query_execution(QueryExecutionId=started)["QueryExecution"]
        state = described["Status"]["State"]
        if state == "SUCCEEDED":
            answered = athena.get_query_results(QueryExecutionId=started)["ResultSet"]["Rows"]
            return [[cell.get("VarCharValue", "") for cell in row["Data"]] for row in answered]
        if state in {"FAILED", "CANCELLED"}:
            raise SystemExit(
                f"the schema query {state.lower()}: "
                f"{described['Status'].get('StateChangeReason', 'no reason given')}"
            )
        time.sleep(2)
    raise SystemExit(f"the schema query did not finish within {STATEMENT_TIMEOUT_SECONDS}s")


def _in_the_table(database: str, workgroup: str, table: str) -> set[str]:
    """The columns Iceberg actually has, from `DESCRIBE` rather than from the catalogue.

    **`DESCRIBE` in Athena returns one cell per row, tab-separated inside it.** Not one column
    per row, and not one value per row either — both of which this parser assumed in turn. Each
    row is a single string: `name\ttype\tcomment`, preceded by a `# Table schema:` banner and a
    `# col_name\tdata_type\tcomment` heading.

    The AWS CLI prints those tabs as column separators, which is why reading its output made the
    layout look like something it is not. Two wrong parsers came out of that: one that took every
    row as a name and returned the types as well, and one that stepped in threes through a list
    that was never flattened. The first reported zero columns and the `ALTER` then tried to add
    one that already existed.
    """
    columns: set[str] = set()
    for row in _run(f"DESCRIBE {table}", database, workgroup):
        cell = row[0] if row else ""
        name = cell.split("\t")[0].strip()
        if name and not name.startswith("#"):
            columns.add(name)
    if not columns:
        raise SystemExit(
            "DESCRIBE returned no columns, which is not a thing a table can have. Refused "
            "rather than treated as empty: an empty answer here makes the next step add every "
            "column again"
        )
    return columns


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="manifest")
    parser.add_argument("--table", default="document_version")
    arguments = parser.parse_args(argv)

    from manifest.core.lake import Row  # noqa: PLC0415

    ssm = _client("ssm")

    def reference(path: str) -> str:
        return ssm.get_parameter(Name=f"/{arguments.project}/{path}")["Parameter"]["Value"]

    database = reference("lakehouse/glue_database")
    workgroup = reference("lakehouse/athena_workgroup")

    wanted = {field.name: str(field.type) for field in dataclass_fields(Row)}
    present = _in_the_table(database, workgroup, arguments.table)
    missing = [name for name in wanted if name not in present]

    print(
        f"{DIM}{len(present)} column(s) in the table, {len(wanted)} declared by "
        f"core.lake.Row{RESET}"
    )
    if not missing:
        print(f"  {GREEN}ok{RESET}    the table already has every column the row declares")
        return 0

    unknown = [name for name in missing if wanted[name] not in SQL_TYPE]
    if unknown:
        raise SystemExit(
            f"no SQL type is declared for {unknown} ({[wanted[n] for n in unknown]}). Refused "
            f"rather than guessed: `string` for a number is a column that accepts everything and "
            f"sorts wrongly, and nothing downstream would say so"
        )

    added = ", ".join(f"{name} {SQL_TYPE[wanted[name]]}" for name in missing)
    _run(f"ALTER TABLE {arguments.table} ADD COLUMNS ({added})", database, workgroup)
    print(f"  {GREEN}ok{RESET}    added {added}")

    # Read back. An `ALTER` that reported success and changed nothing is exactly the failure this
    # script exists for — the catalogue said the columns were there for twenty minutes.
    now = _in_the_table(database, workgroup, arguments.table)
    still = [name for name in missing if name not in now]
    if still:
        raise SystemExit(
            f"the ALTER succeeded and {still} are still absent. That is the shape of the defect "
            f"this script was written for, one level down"
        )
    print(f"  {GREEN}ok{RESET}    read back: the table now has all {len(wanted)} of them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
