-- What the review queue actually costs, and whether the people in it were looking.
--
-- ADR-0001 made the capacity argument as arithmetic over declared parameters. This is the same
-- argument asked of the record instead of the model: how many items, from which reason, decided
-- by whom, how fast, and agreeing how often.
--
-- **Both tails of the agreement rate are reported.** A reviewer at 100% is a rubber stamp; one
-- at 0% is not reading either, and it is the tail nobody alerts on. Reporting a single
-- "agreement rate" column and leaving the reader to notice which end is which is how an
-- integrity metric becomes a number in a dashboard.
--
-- `unexamined` counts decisions faster than the declared floor. It is a count and not a
-- percentage on purpose: "reviewer 3 decided 84 items in under four seconds" is a finding, and
-- "6% of decisions were fast" is a sentence somebody scrolls past.
SELECT
    queued_on,
    client_id,
    queued_reason,
    reviewer,
    COUNT(*)                                                  AS items,
    COUNT(decision)                                           AS decided,
    COUNT(*) - COUNT(decision)                                AS still_waiting,
    AVG(seconds_on_task)                                      AS mean_seconds,
    MEDIAN(seconds_on_task)                                   AS median_seconds,
    SUM(CASE WHEN seconds_on_task < 4 THEN 1 ELSE 0 END)      AS unexamined,
    SUM(CASE WHEN agreed_with_model THEN 1 ELSE 0 END)        AS agreed,
    SUM(CASE WHEN agreed_with_model = FALSE THEN 1 ELSE 0 END) AS disagreed,
    -- Both tails, named. A NULL where nothing was decided, rather than a zero that reads as
    -- total disagreement.
    CASE
        WHEN COUNT(decision) = 0 THEN NULL
        ELSE 1.0 * SUM(CASE WHEN agreed_with_model THEN 1 ELSE 0 END) / COUNT(decision)
    END                                                       AS agreement_rate
FROM gold.review_item
GROUP BY queued_on, client_id, queued_reason, reviewer
ORDER BY queued_on DESC, items DESC;
