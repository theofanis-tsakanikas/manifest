#!/usr/bin/env python3
"""Create the warehouse schema, and prove each mart runs against it.

**The warehouse stood up and was empty of everything, including its own tables.** The Redshift
workgroup applied, answered `select 1`, and held nothing but Redshift's internal catalogue —
`analytics/schema.sql` had never been executed by anything. `scripts/check_marts.py` proves
offline that no mart reads a column the schema does not declare, which is exactly the check that
cannot notice the schema is not there.

Two things happen here and the second is the point.

**The schema is created**, idempotently, from the one file that declares it. `IF NOT EXISTS`
throughout, so a second deploy is a no-op rather than a failure.

**Every mart is then executed.** Not as a view and not as a smoke test with `LIMIT 0` — the real
query, against the real schema. A mart that parses and a mart that runs are different claims, and
the gap between them is every function name, cast and join Redshift disagrees with. The rows
returned are not checked and must not be: the warehouse has no data yet, and a mart returning
nothing over an empty schema is the honest answer rather than a failure.

**It reads the admin credential, and that sentence started life as its opposite.** The first
version of this said no credential is read, because the Data API can authenticate a caller's IAM
identity against the workgroup — and it does, and that identity maps to a database user with no
privileges on `dev`. `analytics/schema.sql` came back `permission denied for database dev`. DDL
needs the privileged session, and the only privileged credential is the one Redshift minted.

So the trade is taken and written down, in `infra/bootstrap/deploy_permissions.tf` beside the
grant. What is bought is a warehouse whose schema is created by the deploy that creates the
warehouse. What is given up is that the deploy role can now read the warehouse admin password —
which was, until this file existed, deliberately impossible.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANALYTICS = ROOT / "analytics"

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"

#: How long one statement may take. Creating a table is instant; a mart over an empty warehouse
#: is nearly so. The ceiling is here so a queue behind a stuck statement fails rather than holds
#: a workflow open until GitHub's own timeout, where the error would name the job.
STATEMENT_TIMEOUT_SECONDS = 180


def _client():
    import boto3  # noqa: PLC0415 - the offline suite imports nothing from AWS

    return boto3.client("redshift-data")


def _statements(sql: str) -> list[str]:
    """The file split into statements, with comments removed first.

    **The docstring here used to describe a different function.** It said the split was on a `;`
    at the *end of a line* "because a comment or a string literal may contain one" — a correct
    argument for an implementation that split on every `;` anyway. And the comment stripping only
    matched whole-line comments, so a trailing `-- no source; see the header` survived, took its
    semicolon with it, and cut a `CREATE TABLE` in half: *"syntax error at end of input"*, on
    statement two, four minutes into an analytics apply.

    Comments are removed from `--` to the end of the line wherever they start, and then the split
    is on every `;`. A `--` inside a string literal would break this and there is none in the
    files it reads; a mart that needed one would be a mart worth simplifying.
    """
    without_comments = re.sub(r"--[^\n]*", "", sql)
    return [part.strip() for part in without_comments.split(";") if part.strip()]


def _run(
    data,
    *,
    workgroup: str,
    database: str,
    secret: str,
    statements: list[str],
    label: str,
) -> str | None:
    """Run one batch. Returns the failure, or `None` when every statement succeeded."""
    submitted = data.batch_execute_statement(
        WorkgroupName=workgroup,
        Database=database,
        SecretArn=secret,
        Sqls=statements,
        StatementName=label,
    )["Id"]

    deadline = time.monotonic() + STATEMENT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        described = data.describe_statement(Id=submitted)
        status = described["Status"]
        if status == "FINISHED":
            return None
        if status in {"FAILED", "ABORTED"}:
            return described.get("Error", "no error text returned")
        time.sleep(2)
    return f"did not finish within {STATEMENT_TIMEOUT_SECONDS}s (statement {submitted})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workgroup", required=True)
    parser.add_argument("--database", default="dev")
    parser.add_argument(
        "--secret-arn",
        required=True,
        help=(
            "The admin credential Redshift minted. Required, and the docstring above says what "
            "it cost to need it: an IAM caller maps to a database user with no privileges on "
            "`dev`, so DDL needs the privileged session and there is only one."
        ),
    )
    arguments = parser.parse_args()

    data = _client()
    failures: list[str] = []

    schema = _statements((ANALYTICS / "schema.sql").read_text(encoding="utf-8"))
    print(f"{DIM}schema.sql — {len(schema)} statement(s){RESET}")
    failure = _run(
        data,
        workgroup=arguments.workgroup,
        database=arguments.database,
        secret=arguments.secret_arn,
        statements=schema,
        label="manifest-schema",
    )
    if failure:
        print(f"  {RED}FAIL{RESET}  the schema was not created: {failure}", file=sys.stderr)
        return 1
    print(f"  {GREEN}ok{RESET}    the warehouse has its tables")

    for mart in sorted(path for path in ANALYTICS.glob("*.sql") if path.name != "schema.sql"):
        # One at a time, named, so a failure says which mart rather than which batch.
        failure = _run(
            data,
            workgroup=arguments.workgroup,
            database=arguments.database,
            secret=arguments.secret_arn,
            statements=_statements(mart.read_text(encoding="utf-8")),
            label=f"manifest-mart-{mart.stem}",
        )
        if failure:
            failures.append(f"{mart.name}: {failure}")
            print(f"  {RED}FAIL{RESET}  {mart.name} — {failure}", file=sys.stderr)
        else:
            print(f"  {GREEN}ok{RESET}    {mart.name} runs against the schema")

    if failures:
        print(
            f"\n{len(failures)} mart(s) parse offline and do not run. `check_marts.py` compares "
            f"them against the declared columns and cannot see a function name, a cast or a join "
            f"Redshift disagrees with.",
            file=sys.stderr,
        )
        return 1

    print(
        f"\nwarehouse: schema created and {len(list(ANALYTICS.glob('*.sql'))) - 1} mart(s) ran. "
        f"No rows are asserted — the warehouse has no data yet, and a mart returning nothing "
        f"over an empty schema is the honest answer."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
