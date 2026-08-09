-- The **modelled** extraction cost per client, per tier.
--
-- Every column name in this file that carries money says `modelled`, and so does the table it
-- reads. That is not decoration: `docs/DECISIONS.md` 15 requires a cost figure to announce
-- itself as a model everywhere it appears, and a warehouse column called `cost_eur` is read as
-- a measurement by the first person to open it — long after whoever knew better has moved on.
--
-- The tier split is the point rather than a breakdown. Tier 0 runs locally and costs nothing
-- marginal; every euro in this mart is a page that escalated. A client whose modelled cost is
-- high is a client whose documents are hard to read, which is a different conversation from a
-- client who sends a lot of them — and `pages` beside `modelled_cost` is what lets somebody
-- have the right one.
SELECT
    read_on,
    client_id,
    reader_tier,
    language,
    COUNT(*)                    AS pages,
    SUM(modelled_cost)          AS modelled_cost,
    MAX(modelled_currency)      AS modelled_currency,
    AVG(modelled_cost)          AS modelled_cost_per_page,
    1000.0 * SUM(modelled_cost) / NULLIF(COUNT(*), 0) AS modelled_cost_per_1000_pages
FROM gold.page_read
GROUP BY read_on, client_id, reader_tier, language
ORDER BY read_on DESC, SUM(modelled_cost) DESC;
