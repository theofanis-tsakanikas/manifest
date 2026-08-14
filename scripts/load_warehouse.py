#!/usr/bin/env python3
"""Load the lake into the warehouse, so the marts read rows instead of an empty schema.

**The gap this closes, and how it was found.** `apply_warehouse_schema.py` creates the tables
and runs every mart against them, and its own closing line said what was wrong: *"no rows are
asserted — the warehouse has no data yet, and a mart returning nothing over an empty schema is
the honest answer"*. Redshift stood up, four marts executed, and every one of them answered
about nothing. Nothing in the estate copied a single row out of Iceberg.

**What it loads, and what it does not.**

- `gold.published_field` — one row per field per version, from the lake. The grain everything
  else is built on.
- `gold.page_read` — one row per page per tier, derived from the same rows and priced from
  `contracts/scale/`. Every figure is **modelled**: no page in this repository has been billed
  by a real invoice, and the column names say so.
- `gold.review_item` — the abstentions, from the same rows, joined to the decision a human
  recorded against each. NULL where none was recorded, which stays a different fact from a
  reviewer who disagreed: this loaded every column NULL until `handlers/decide.py` gave the
  estate a way to record one, and leaving them NULL afterwards would under-report oversight
  that actually happened. It is the denominator claim 5's agreement rate is computed over.
- `gold.declaration_line` — **nothing**, and the reason is printed rather than left to be
  inferred. A declaration line needs an HS code with a declared value against it, and this
  system produces classification *proposals* that no human has decided. Loading a proposal as a
  declaration would put a number nobody approved into a duty-exposure figure.

**`INSERT`, not `COPY`, at this volume and with that stated.** A warehouse load at scale
unloads from Athena to storage and `COPY`s, in one command per table, because a million rows do
not go through a statement API. This estate holds tens. What is being demonstrated is that the
marts read real rows; batching them through `redshift-data` keeps the whole path in one file a
reader can follow, and the shape a real one takes is this paragraph.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"

#: Rows per `INSERT`. Redshift's statement API takes a size limit rather than a row count, and a
#: hundred rows of this width is comfortably inside it.
BATCH = 100

#: How long any one statement may take.
STATEMENT_TIMEOUT_SECONDS = 300


def _client(name: str):
    import boto3  # noqa: PLC0415 - the offline suite imports this module without AWS

    return boto3.client(name)


def _athena(query: str, database: str, workgroup: str) -> list[list[str]]:
    """Run one query and return its rows, without the header."""
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
            break
        if state in {"FAILED", "CANCELLED"}:
            raise SystemExit(
                f"the lake query {state.lower()}: "
                f"{described['Status'].get('StateChangeReason', 'no reason given')}"
            )
        time.sleep(2)
    else:
        raise SystemExit(f"the lake query did not finish within {STATEMENT_TIMEOUT_SECONDS}s")

    rows: list[list[str]] = []
    token = None
    while True:
        page = (
            athena.get_query_results(QueryExecutionId=started, NextToken=token)
            if token
            else athena.get_query_results(QueryExecutionId=started)
        )
        for row in page["ResultSet"]["Rows"]:
            rows.append([cell.get("VarCharValue") for cell in row["Data"]])
        token = page.get("NextToken")
        if not token:
            break
    return rows[1:]


def _redshift(statements: list[str], workgroup: str, secret: str) -> None:
    """Run statements in order, stopping at the first that fails."""
    data = _client("redshift-data")
    for statement in statements:
        started = data.execute_statement(
            WorkgroupName=workgroup, SecretArn=secret, Database="dev", Sql=statement
        )["Id"]
        deadline = time.monotonic() + STATEMENT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            described = data.describe_statement(Id=started)
            if described["Status"] == "FINISHED":
                break
            if described["Status"] in {"FAILED", "ABORTED"}:
                raise SystemExit(
                    f"the warehouse refused a statement: {described.get('Error', 'no error given')}"
                )
            time.sleep(1)
        else:
            raise SystemExit(
                f"a warehouse statement did not finish within {STATEMENT_TIMEOUT_SECONDS}s"
            )


def _literal(value: str | None) -> str:
    """One SQL literal. Quotes doubled, exactly as `core.lake` argues at length.

    A value that reaches SQL by concatenation is a value that can end the statement it is in,
    and these values came off a page a counterparty wrote.
    """
    if value is None or value == "":
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


def _number(value: str | None) -> str:
    return "NULL" if value in (None, "") else str(float(value))


def _boolean(value: str | None) -> str:
    return "TRUE" if str(value).lower() == "true" else "FALSE"


def _decisions(table: str) -> dict[tuple[str, str], dict[str, str]]:
    """Every recorded human decision, keyed by the version and field it was made against.

    A scan, and it should be one: this table holds one row per decision a human made, which is
    bounded by how many people are looking at documents rather than by how many documents there
    are. The load is a full projection of current truth — the same reason the tables above it are
    truncated and rewritten — so reading all of it is the operation, not a shortcut.

    A missing table is not an empty table, and this refuses rather than returning `{}`: an estate
    deployed without the decisions table would otherwise load every review item with NULL
    decisions and print a number that looked like *nobody has reviewed anything*.
    """
    rows: dict[tuple[str, str], dict[str, str]] = {}
    paginator = _client("dynamodb").get_paginator("scan")
    for page in paginator.paginate(TableName=table, ConsistentRead=True):
        for item in page.get("Items", []):
            key = (
                str(item.get("document_version", {}).get("S", "")),
                str(item.get("field", {}).get("S", "")),
            )
            rows[key] = {
                "reviewer": item.get("reviewer", {}).get("S"),
                "decision": item.get("decision", {}).get("S"),
                "seconds_on_task": item.get("seconds_on_task", {}).get("N"),
                "decided_on": item.get("decided_on", {}).get("S"),
                # Kept as the tri-state it is: `None` means no decision was recorded, which is a
                # different fact from a decision that disagreed with the model.
                "agreed_with_model": item.get("agreed_with_model", {}).get("BOOL"),
            }
    return rows


def _agreement(decision: dict[str, str] | None) -> str:
    """`agreed_with_model`, and NULL when nobody decided.

    Not `_boolean`, which answers FALSE to everything that is not the string `true` — including
    the absence of a decision. That would report every unreviewed abstention as a reviewer who
    disagreed with the model, inflating exactly the denominator doctrine rule 2 measures.
    """
    if decision is None or decision.get("agreed_with_model") is None:
        return "NULL"
    return "TRUE" if decision["agreed_with_model"] else "FALSE"


def _batched(table: str, columns: str, values: list[str]) -> list[str]:
    """One `INSERT` per batch of already-encoded value tuples.

    `table` and `columns` are literals in this file and never come from data — the only thing
    that arrives from outside is a *value*, and every value went through `_literal`, which
    doubles quotes for the reason `core.lake` argues at length. The linter cannot see that
    distinction, so it is written here instead of waved at.
    """
    return [
        f"INSERT INTO {table} ({columns}) VALUES {', '.join(values[start : start + BATCH])}"  # noqa: S608
        for start in range(0, len(values), BATCH)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="manifest")
    parser.add_argument("--workgroup", required=True, help="The Redshift Serverless workgroup.")
    parser.add_argument("--secret-arn", required=True, help="Its admin secret.")
    arguments = parser.parse_args(argv)

    ssm = _client("ssm")

    def reference(path: str) -> str:
        return ssm.get_parameter(Name=f"/{arguments.project}/{path}")["Parameter"]["Value"]

    database = reference("lakehouse/glue_database")
    athena_workgroup = reference("lakehouse/athena_workgroup")

    # **Only the current version of each document.** The lake holds every version — doctrine
    # rule 4 — and a warehouse that summed across them would count a corrected document twice
    # and report an error rate over readings that were superseded on purpose.
    lake = _athena(
        """
        WITH current AS (
            SELECT document_id, MAX(extracted_on) AS latest
            FROM document_version GROUP BY document_id
        )
        SELECT v.version, v.document_id, v.document_type, v.field, v.value, v.confidence,
               v.threshold, v.reader, v.reader_tier, v.language, v.page,
               v.provenance_verified, v.published, CAST(v.extraction_date AS VARCHAR)
        FROM document_version v
        JOIN current c ON c.document_id = v.document_id AND c.latest = v.extracted_on
        """,
        database,
        athena_workgroup,
    )
    # **Rows written before a column existed do not have it, and that is the table's history
    # rather than a defect.** `document_type`, `language`, `reader_tier` and `published` were
    # added to the lake on 2026-08-13; every row landed before that carries NULL for them, which
    # is exactly what Iceberg's schema evolution means. The warehouse declares `document_type`
    # NOT NULL because a fact table grouped by a column that can be absent is a fact table with
    # an "unknown" bucket nobody can act on.
    #
    # So they are excluded and **counted out loud**. Dropping rows quietly is how a load reports
    # success over half the data; the number below is what makes the difference visible, and it
    # goes to zero on its own as documents are landed again.
    pre_evolution = [row for row in lake if not row[2]]
    lake = [row for row in lake if row[2]]
    if pre_evolution:
        print(
            f"{DIM}{len(pre_evolution)} row(s) predate the document_type column and are not "
            f"loaded — they are still in the lake, which is the record{RESET}"
        )
    print(f"{DIM}{len(lake)} field row(s) at the current version of each document{RESET}")
    if not lake:
        # **A fact, not a failure, and the difference decides whether a deploy goes red.** An
        # empty lake is the normal state of a freshly applied estate: no document has arrived
        # yet. Exiting non-zero here made the analytics layer fail every first deploy, which
        # trains a reader to ignore the one signal this step has.
        #
        # What proves rows actually flow is `scripts/e2e_verify.py`, which sends a document and
        # asserts it reaches the lake. This step's job is to project whatever is there.
        print(
            f"{RED}nothing to load{RESET} — the lake holds no row at the current schema, so the "
            f"warehouse stays empty. That is the state of a fresh estate rather than a failure; "
            f"scripts/e2e_verify.py is what proves a document reaches the lake at all."
        )
        return 0

    statements = [
        "TRUNCATE gold.published_field",
        "TRUNCATE gold.page_read",
        "TRUNCATE gold.review_item",
    ]

    # **Truncated and rewritten, not appended.** The warehouse is a *view* of the lake, and the
    # lake is the record — so a load is a projection of current truth rather than a history of
    # loads. Appending would double every figure on the second run, which is the same defect the
    # landing step had against Iceberg and the reason that one is now idempotent.
    fields = [
        "("
        + ", ".join(
            (
                _literal(version),
                "NULL",  # shipment_id — no source; see analytics/schema.sql
                _literal(document_type),
                _literal(field),
                _literal(value),
                _number(confidence) if confidence not in (None, "") else "0",
                _number(threshold),
                "TRUE" if threshold in (None, "") else "FALSE",  # always_review
                _boolean(published),
                str(int(tier or 0)),
                _literal(reader),
                _literal(language),
                str(int(page or 0)) if page not in (None, "") else "NULL",
                _boolean(verified),
                "NULL",  # source_channel — no source
                "NULL",  # carrier — no source
                "NULL",  # client_id — no source
                _literal(extraction_date),
            )
        )
        + ")"
        for (
            version,
            _document_id,
            document_type,
            field,
            value,
            confidence,
            threshold,
            reader,
            tier,
            language,
            page,
            verified,
            published,
            extraction_date,
        ) in lake
    ]
    statements += _batched(
        "gold.published_field",
        "document_version, shipment_id, document_type, field_name, field_value, confidence, "
        "threshold, always_review, published, reader_tier, reader_identity, language, page, "
        "provenance_verified, source_channel, carrier, client_id, extracted_on",
        fields,
    )

    # One row per page per tier, from the fields that were read there. The cost is modelled and
    # every name carrying it says so — `docs/DECISIONS.md` 15.
    pages = {
        (row[0], int(row[10] or 0), int(row[8] or 0), row[9], row[13])
        for row in lake
        if row[10] not in (None, "")
    }
    reads = [
        "("
        + ", ".join(
            (
                _literal(version),
                str(page),
                str(tier),
                _literal(language),
                f"{_MODELLED_EUR_PER_PAGE.get(tier, 0.0):.6f}",
                "'EUR'",
                "NULL",  # client_id — no source
                _literal(read_on),
            )
        )
        + ")"
        for version, page, tier, language, read_on in sorted(pages)
    ]
    statements += _batched(
        "gold.page_read",
        "document_version, page, reader_tier, language, modelled_cost, modelled_currency, "
        "client_id, read_on",
        reads,
    )

    # The abstentions, each carrying the human decision made against it — and NULL where none
    # was, which is a different fact and stays visibly different.
    #
    # **This join is claim 5's denominator, and until today there was no numerator to join to.**
    # `gold.review_item` loaded every abstention with `reviewer`, `decision`, `seconds_on_task`
    # and `agreed_with_model` hard-coded NULL, because nothing in the estate had ever recorded a
    # decision. `handlers/decide.py` now does, so the honest thing changed: leaving the columns
    # NULL while the table holds rows would be the mart under-reporting oversight that happened.
    #
    # Keyed on `(document_version, field)` — the table's own key, and the right one. A decision
    # is made against the version the reviewer was looking at; approving it publishes a *new*
    # version, so the row it belongs to is the one that says `published = FALSE`, which is
    # exactly the row this list is built from. A join on document id would attach a decision to
    # every version of the document including the ones that came after it.
    #
    # `agreed_with_model` is carried through rather than recomputed. It is computed in the
    # handler, from the decision, and a loader that derived it a second time would be a second
    # place for doctrine rule 2's numerator to be decided.
    decisions = _decisions(f"{arguments.project}-review-decisions")
    queued = [
        "("
        + ", ".join(
            (
                _literal(f"{row[0][:32]}:{row[3]}"),
                _literal(row[0]),
                _literal(row[3]),
                "'below_threshold'" if row[6] not in (None, "") else "'always_review'",
                _literal(decisions.get((row[0], row[3]), {}).get("reviewer")),
                _literal(decisions.get((row[0], row[3]), {}).get("decision")),
                _number(decisions.get((row[0], row[3]), {}).get("seconds_on_task")),
                _agreement(decisions.get((row[0], row[3]))),
                "NULL",  # client_id — no source
                _literal(row[13]),
                _literal(decisions.get((row[0], row[3]), {}).get("decided_on")),
            )
        )
        + ")"
        for row in lake
        if _boolean(row[12]) != "TRUE"
    ]
    decided = sum(1 for row in lake if (row[0], row[3]) in decisions)
    if queued:
        statements += _batched(
            "gold.review_item",
            "item_id, document_version, field_name, queued_reason, reviewer, decision, "
            "seconds_on_task, agreed_with_model, client_id, queued_on, decided_on",
            queued,
        )

    _redshift(statements, arguments.workgroup, arguments.secret_arn)
    print(f"  {GREEN}ok{RESET}    gold.published_field  {len(fields)} row(s)")
    print(f"  {GREEN}ok{RESET}    gold.page_read        {len(reads)} row(s)")
    print(
        f"  {GREEN}ok{RESET}    gold.review_item      {len(queued)} row(s), {decided} with a "
        f"recorded decision"
    )
    print(
        f"  {DIM}gold.declaration_line 0 rows, deliberately: a declaration line needs an HS "
        f"code with a declared value, and this system produces proposals no human has decided. "
        f"Loading one as a declaration would put a number nobody approved into a duty figure."
        f"{RESET}"
    )
    return 0


#: The modelled euro cost of reading one page at each tier. Published list prices, not a bill:
#: no page in this repository has been invoiced, and `docs/DECISIONS.md` 15 requires every
#: figure that is a model to say so wherever it appears. Tier 0 is the local reader and is free
#: at the margin.
_MODELLED_EUR_PER_PAGE = {0: 0.0, 1: 0.0015, 2: 0.010, 3: 0.004}


if __name__ == "__main__":
    raise SystemExit(main())
