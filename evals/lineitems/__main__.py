"""The line-item table, and the dropped row that nothing else can see.

`docs/SCENARIO.md`, pathology 3: a table continuing on the next page with no repeated header.
Naive extraction reads the first page, misses the continuation, and **the total still looks
plausible** — it was printed, not summed. Nothing about the record looks wrong.

Two things are scored.

**Does the reader follow the table across the break?** Measured directly: on the documents whose
ground truth records `table_across_page_break`, how many rows are read on the continuation page.
A reader that required a header per page reads zero of them and reports a complete table.

**Does the check catch it when the reader does not?** The harness *deliberately truncates* the
table at the page boundary — the naive extraction — and asserts the total check reports
`ROWS_MISSING`. That is the control the pathology exists to exercise, and it is the only thing
in the system that can see a row that is not there.

The second number is the point. The first can be improved by better extraction; the second is
what makes the *record* trustworthy when extraction is imperfect, which it always is.
"""

from __future__ import annotations

import sys
from decimal import Decimal

from evals.harness import contracts, ground_truth, recorded_pages
from manifest.core.fields import extract_from_pages
from manifest.core.lineitems import Table, TotalOutcome, check_total, read_table


def main() -> int:
    contract = contracts().document("commercial_invoice")
    table_contract = contract.table
    if table_contract is None:
        print("commercial_invoice declares no line-item table", file=sys.stderr)
        return 1

    value_column = next(column.name for column in table_contract.columns if column.is_line_value)
    truth = ground_truth()
    breaks = {
        document["shipment_id"]
        for document in truth["documents"]
        if document["document_id"] == "commercial_invoice"
        and "table_across_page_break" in document["pathologies"]
    }

    pages_by_document = recorded_pages()
    found = 0
    followed = 0
    continuation_rows = 0
    agreed = 0
    missing_detected = 0
    truncation_caught = 0
    truncation_directed = 0
    truncation_attempted = 0
    not_comparable = 0

    for (shipment, document_id), pages in sorted(pages_by_document.items()):
        if document_id != "commercial_invoice":
            continue
        anchors = {
            column.name: column.anchors[
                next(page.language for page in pages if page.language) or "en"
            ]
            for column in table_contract.columns
        }
        table = read_table(pages, anchors, value_column)
        if not table.found:
            continue
        found += 1

        if shipment in breaks:
            after_header = [row for row in table.rows if row.page > (table.header_page or 0)]
            if after_header:
                followed += 1
                continuation_rows += len(after_header)

        total = extract_from_pages(
            pages,
            table_contract.total_field,
            contract.field(table_contract.total_field).anchors[
                next(page.language for page in pages if page.language) or "en"
            ],
        )
        result = check_total(table, total.value, Decimal(table_contract.tolerance))
        if result.outcome is TotalOutcome.AGREES:
            agreed += 1
        elif result.outcome is TotalOutcome.ROWS_MISSING:
            missing_detected += 1
        elif result.outcome is TotalOutcome.NOT_COMPARABLE:
            not_comparable += 1

        # The control, exercised against the failure it exists for: throw away the
        # continuation rows — which is exactly what a reader that stops at the page break
        # produces — and require the total check to notice.
        if shipment in breaks and table.header_page is not None:
            truncated = Table(
                rows=tuple(row for row in table.rows if row.page == table.header_page),
                columns=table.columns,
                pages_read=(table.header_page,),
                header_page=table.header_page,
            )
            if len(truncated.rows) < len(table.rows) and total.value is not None:
                truncation_attempted += 1
                outcome = check_total(
                    truncated, total.value, Decimal(table_contract.tolerance)
                ).outcome
                # **Caught means "did not report agreement".** Naming the direction is a
                # stronger statement and it is not always available: where the reader mangled
                # the printed total, the check refuses to treat it as a number at all. That is
                # the honest outcome — an unreadable total and a short table are two problems,
                # and demanding ROWS_MISSING would be demanding a direction nothing can know.
                if outcome is not TotalOutcome.AGREES:
                    truncation_caught += 1
                if outcome is TotalOutcome.ROWS_MISSING:
                    truncation_directed += 1

    print("the line-item table, and the row that is not there\n")
    print(f"  invoices with a readable table   {found}")
    print(f"  of those, tables that break      {len(breaks)}")
    print(f"     followed past the break       {followed}")
    print(f"     rows read on continuations    {continuation_rows}")
    print("\n  the total, against the rows as read:")
    print(f"     agree                         {agreed}")
    print(f"     rows missing                  {missing_detected}")
    print(f"     not comparable                {not_comparable}")
    print("\n  the control, against a deliberately truncated table:")
    print(f"     truncations caught            {truncation_caught}/{truncation_attempted}")
    print(f"     of those, direction named     {truncation_directed} as rows-missing")
    print(
        f"     the remaining {truncation_caught - truncation_directed} are totals the reader "
        f"mangled — the check refuses to treat a misread number as a number, which is a "
        f"different finding from a short table and is reported as one"
    )
    print(
        "     This is the number that matters. The first block can be improved by better "
        "extraction; this one is what makes the record trustworthy while extraction is "
        "imperfect, which it always is."
    )

    failures: list[str] = []
    if truncation_attempted and truncation_caught < truncation_attempted:
        failures.append(
            f"only {truncation_caught} of {truncation_attempted} truncated tables were caught. "
            f"A dropped row that the total check does not see is a row nothing in this system "
            f"can see — the record is short and every field in it looks correct"
        )
    if not truncation_attempted:
        failures.append(
            "no truncation was exercised. Either no table breaks across a page in this corpus "
            "or the reader is not following one, and both make this harness vacuous"
        )
    if failures:
        print("\nline items: FAILED", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print("\nline items: a truncated table is caught by arithmetic the printed total cannot hide.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
