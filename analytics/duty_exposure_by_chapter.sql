-- Duty exposure by HS chapter, and how much of it a human actually decided.
--
-- **The question a single document cannot answer**, and the first of the four that
-- `docs/DECISIONS.md` 6 says must exist or Redshift leaves the project.
--
-- A chapter is the first two digits of the heading. Grouping there rather than at the full
-- six is deliberate: exposure concentrates by chapter, and a broker who wants to know where
-- their risk is does not want four hundred rows of headings each with one declaration on it.
--
-- The `human_decided` column is why this mart is worth more than a duty total. `hs_code` is
-- always-review, so every row should carry a recorded decision — and `undecided_value` is a
-- count of the control not having run. On a healthy system it is zero, and the query is
-- written so that a non-zero figure is impossible to miss rather than buried in a ratio.
SELECT
    LEFT(hs_code, 2)                                   AS hs_chapter,
    COUNT(*)                                           AS lines_declared,
    COUNT(DISTINCT shipment_id)                        AS shipments,
    SUM(declared_value)                                AS declared_value,
    SUM(COALESCE(duty_amount, 0))                      AS duty,
    -- Value classified without a recorded human decision. Should be zero.
    SUM(CASE WHEN human_decided THEN 0 ELSE declared_value END) AS undecided_value,
    SUM(CASE WHEN human_decided THEN 0 ELSE 1 END)     AS undecided_lines,
    MIN(declared_on)                                   AS first_declared_on,
    MAX(declared_on)                                   AS last_declared_on
FROM gold.declaration_line
GROUP BY LEFT(hs_code, 2)
ORDER BY SUM(COALESCE(duty_amount, 0)) DESC;
