-- Error rate by document source and by carrier — where the bad paper comes from.
--
-- The fourth question `docs/DECISIONS.md` 6 requires, and the one with the most operational
-- value: a scanning desk producing twice the abstention rate of the SFTP feed is a scanner
-- that needs cleaning, and nobody finds that by looking at documents one at a time.
--
-- **Abstention and failed provenance are separate columns and never summed.** They are
-- different failures with different fixes: an abstention is the system refusing to publish
-- (which is it working), and a failed provenance check is a value that was read and could not
-- be located (which is it catching something). A single "quality score" folding both together
-- would move in the right direction for the wrong reason and would be optimised by publishing
-- more.
SELECT
    extracted_on,
    source_channel,
    carrier,
    document_type,
    language,
    reader_tier,
    COUNT(*)                                                       AS fields,
    SUM(CASE WHEN published THEN 1 ELSE 0 END)                     AS published,
    SUM(CASE WHEN field_value IS NULL THEN 1 ELSE 0 END)           AS abstained,
    SUM(CASE WHEN provenance_verified = FALSE THEN 1 ELSE 0 END)   AS provenance_refused,
    SUM(CASE WHEN always_review THEN 1 ELSE 0 END)                 AS always_review,
    AVG(confidence)                                                AS mean_confidence,
    -- The share the reader could not locate at all. Rising here on one channel while flat on
    -- the others is a scanner, not a model.
    1.0 * SUM(CASE WHEN field_value IS NULL THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0)
                                                                   AS abstention_rate
FROM gold.published_field
GROUP BY extracted_on, source_channel, carrier, document_type, language, reader_tier
ORDER BY extracted_on DESC, abstention_rate DESC;
