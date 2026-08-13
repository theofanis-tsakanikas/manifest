-- The warehouse schema the marts read.
--
-- Declared here rather than inferred from the marts, because a query that invents a column is a
-- query that fails at minute forty of a deploy. `scripts/check_marts.py` reads this file and
-- refuses any mart referencing a table or column it does not declare — which is the only check
-- available offline, and it catches the whole class of failure that a warehouse would otherwise
-- catch for the first time in production.
--
-- **Dropped and recreated, not `CREATE TABLE IF NOT EXISTS`.** That form is idempotent and is
-- not evolutionary: a column that changes type or loses `NOT NULL` is silently ignored, the
-- statement succeeds, and the table keeps the shape it was first created with. Making four
-- columns nullable here changed nothing on a warehouse that already existed, and the loader
-- failed on `Cannot insert a NULL value into column shipment_id` — a rejection by a constraint
-- this file no longer declares.
--
-- It is the same defect as the Iceberg one a layer below, where editing the catalogue's column
-- list left the table's real schema alone. Both look exactly like a schema change and both are
-- no-ops.
--
-- Dropping is safe here and would not be in most warehouses: **nothing in `gold` is a record.**
-- The lake is the record and this is a projection of it, rebuilt from scratch by
-- `scripts/load_warehouse.py` on every run — which is also why that script truncates rather than
-- appends. A warehouse holding anything of its own would need a migration instead.
--
-- **Four columns are nullable and nothing in this system fills them**, which is stated here
-- rather than papered over. `shipment_id`, `source_channel`, `carrier` and `client_id` are
-- business metadata a broker's own systems hold: which desk scanned the paper, which carrier
-- moved the box, which client is billed. The pipeline reads a page. It never sees any of them,
-- and `scripts/load_warehouse.py` leaves them NULL.
--
-- That is doctrine rule 3 working rather than a gap: *missing is missing, and it is stated*. The
-- marts group by these columns and will show one NULL group until something upstream supplies
-- them — visibly absent, which is what a reader needs, instead of a plausible value invented by
-- a loader. Filling them with "unknown" or the document id would make every figure in
-- `quality_by_source` look like a finding about a carrier.

CREATE SCHEMA IF NOT EXISTS gold;

-- One row per published field. The grain of everything below: a fact about *one value on one
-- document*, which is the only grain at which "error rate by carrier" and "cost per client"
-- are the same question asked twice.
DROP TABLE IF EXISTS gold.published_field;
CREATE TABLE gold.published_field (
    document_version   VARCHAR(64)   NOT NULL,
    shipment_id        VARCHAR(32),          -- no source; see the header
    document_type      VARCHAR(64)   NOT NULL,
    field_name         VARCHAR(64)   NOT NULL,
    field_value        VARCHAR(512),
    confidence         DECIMAL(6, 5) NOT NULL,
    threshold          DECIMAL(6, 5),
    always_review      BOOLEAN       NOT NULL,
    published          BOOLEAN       NOT NULL,
    -- Which tier read it. The join key between quality and cost, and the reason those two
    -- questions can be asked of one table instead of reconciled across two.
    reader_tier        SMALLINT      NOT NULL,
    reader_identity    VARCHAR(128)  NOT NULL,
    language           VARCHAR(8)    NOT NULL,
    page               SMALLINT,
    provenance_verified BOOLEAN      NOT NULL,
    source_channel     VARCHAR(32),            -- no source; see the header
    carrier            VARCHAR(32),
    client_id          VARCHAR(32),
    extracted_on       DATE          NOT NULL
);

-- One row per review item. Separate from the field table because an item may be re-queued, and
-- collapsing the two would make "decisions per day" count a field twice or an item once.
DROP TABLE IF EXISTS gold.review_item;
CREATE TABLE gold.review_item (
    item_id            VARCHAR(64)   NOT NULL,
    document_version   VARCHAR(64)   NOT NULL,
    field_name         VARCHAR(64)   NOT NULL,
    queued_reason      VARCHAR(32)   NOT NULL,
    reviewer           VARCHAR(64),
    decision           VARCHAR(16),
    seconds_on_task    DECIMAL(8, 2),
    agreed_with_model  BOOLEAN,
    client_id          VARCHAR(32),
    queued_on          DATE          NOT NULL,
    decided_on         DATE
);

-- One row per declaration line, for duty exposure. The HS code is here and not on the field
-- table because a declaration carries several, and putting them on the field grain would make
-- every duty figure a sum over duplicates.
DROP TABLE IF EXISTS gold.declaration_line;
CREATE TABLE gold.declaration_line (
    document_version   VARCHAR(64)   NOT NULL,
    shipment_id        VARCHAR(32),          -- no source; see the header
    hs_code            VARCHAR(10)   NOT NULL,
    declared_value     DECIMAL(14, 2) NOT NULL,
    duty_amount        DECIMAL(14, 2),
    currency           VARCHAR(3)    NOT NULL,
    country_of_origin  VARCHAR(2),
    -- Whether a human decided this classification. Every one of them should have — `hs_code` is
    -- always-review — and a row where this is false is a control that did not run.
    human_decided      BOOLEAN       NOT NULL,
    client_id          VARCHAR(32),
    declared_on        DATE          NOT NULL
);

-- One row per page read, per tier. The **modelled** cost lives here, and the column name says
-- so: no page in this repository has been sent to a billed API, and a column called `cost_eur`
-- would be read as a measurement by the first person to open the warehouse.
DROP TABLE IF EXISTS gold.page_read;
CREATE TABLE gold.page_read (
    document_version   VARCHAR(64)   NOT NULL,
    page               SMALLINT      NOT NULL,
    reader_tier        SMALLINT      NOT NULL,
    language           VARCHAR(8)    NOT NULL,
    modelled_cost      DECIMAL(12, 6) NOT NULL,
    modelled_currency  VARCHAR(3)    NOT NULL,
    client_id          VARCHAR(32),
    read_on            DATE          NOT NULL
);

-- One row per reconciliation finding.
DROP TABLE IF EXISTS gold.reconciliation_finding;
CREATE TABLE gold.reconciliation_finding (
    shipment_id        VARCHAR(32),          -- no source; see the header
    rule_id            VARCHAR(64)   NOT NULL,
    outcome            VARCHAR(20)   NOT NULL,
    severity           VARCHAR(10)   NOT NULL,
    left_document      VARCHAR(64)   NOT NULL,
    right_document     VARCHAR(64)   NOT NULL,
    client_id          VARCHAR(32),
    found_on           DATE          NOT NULL
);
