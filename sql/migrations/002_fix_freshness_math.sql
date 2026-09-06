-- ============================================================================
-- 002_fix_freshness_math.sql
--
-- Three fixes to the ledger from 001, all caught by reading its first output.
--
-- 1. days_overdue used EXTRACT(day FROM interval), which returns the interval's
--    DAYS FIELD, not total elapsed days. "236 days - 1 year" is stored as
--    (months: -12, days: 236), so EXTRACT(day) returned 236 when the true answer
--    is -130. Every row was reported overdue, including rows marked 'current'.
--    Fixed by going through epoch seconds.
--
-- 2. refresh_data_source_stats(false) used GREATEST(reltuples, 0). reltuples is
--    -1 for a table that has never been ANALYZEd, so those silently became 0.
--    drinf.pt_hospitals reported 0 rows when it holds 3. Now falls back to an
--    exact count when the estimate is unavailable.
--
-- 3. status called drinf.medicare_utilization 'never_loaded' when it holds
--    39,982 rows. It has no load timestamp column, which is a different problem
--    from being empty. Split into 'untracked' vs 'never_loaded'.
--
-- Idempotent. Safe to re-run.
-- ============================================================================

CREATE OR REPLACE FUNCTION meta.refresh_data_source_stats(exact boolean DEFAULT true)
RETURNS integer
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    r          record;
    n          bigint;
    ts         timestamptz;
    n_updated  integer := 0;
BEGIN
    FOR r IN
        SELECT source_name, target_schema, target_table, freshness_column
        FROM meta.data_sources
    LOOP
        IF to_regclass(format('%I.%I', r.target_schema, r.target_table)) IS NULL THEN
            CONTINUE;
        END IF;

        n := NULL;

        IF NOT exact THEN
            SELECT c.reltuples::bigint INTO n
            FROM pg_class c
            WHERE c.oid = to_regclass(format('%I.%I', r.target_schema, r.target_table));

            -- reltuples is -1 when the table has never been analyzed. Treating
            -- that as a row count reports a populated table as empty.
            IF n IS NULL OR n < 0 THEN
                n := NULL;
            END IF;
        END IF;

        -- Exact count when asked for, or when no usable estimate exists.
        IF n IS NULL THEN
            EXECUTE format('SELECT count(*) FROM %I.%I', r.target_schema, r.target_table)
            INTO n;
        END IF;

        ts := NULL;
        IF r.freshness_column IS NOT NULL THEN
            EXECUTE format('SELECT max(%I)::timestamptz FROM %I.%I',
                           r.freshness_column, r.target_schema, r.target_table)
            INTO ts;
        END IF;

        UPDATE meta.data_sources
           SET record_count   = n,
               last_loaded_at = COALESCE(ts, last_loaded_at),
               updated_at     = now()
         WHERE source_name = r.source_name;

        n_updated := n_updated + 1;
    END LOOP;

    RETURN n_updated;
END;
$$;


-- CREATE OR REPLACE cannot reorder or rename a view's columns, and this adds
-- days_since_load ahead of days_overdue. Nothing depends on the view.
DROP VIEW IF EXISTS meta.v_data_freshness;

CREATE VIEW meta.v_data_freshness AS
WITH expected AS (
    SELECT
        ds.*,
        CASE ds.refresh_cadence
            WHEN 'monthly'   THEN interval '1 month'
            WHEN 'quarterly' THEN interval '3 months'
            WHEN 'annual'    THEN interval '1 year'
        END AS expected_interval
    FROM meta.data_sources ds
),
scored AS (
    SELECT
        e.*,
        now() - e.last_loaded_at AS age,
        CASE
            WHEN e.refresh_cadence IN ('static', 'manual') THEN 'n/a'
            -- No load timestamp AND no rows: this has genuinely never run.
            WHEN e.last_loaded_at IS NULL
                 AND COALESCE(e.record_count, 0) = 0        THEN 'never_loaded'
            -- Rows present but the table records no load date, so we cannot
            -- judge staleness. Not the same as never having loaded.
            WHEN e.last_loaded_at IS NULL                   THEN 'untracked'
            WHEN e.expected_interval IS NULL                THEN 'unknown'
            WHEN now() - e.last_loaded_at <= e.expected_interval       THEN 'current'
            WHEN now() - e.last_loaded_at <= e.expected_interval * 1.5 THEN 'due'
            ELSE 'overdue'
        END AS status
    FROM expected e
)
SELECT
    source_name,
    target_schema || '.' || target_table AS target,
    status,
    refresh_cadence,
    url_stability,
    last_loaded_at,
    last_source_period,
    -- Total elapsed days since the load, via epoch seconds. EXTRACT(day FROM
    -- interval) would return only the interval's days component.
    CASE
        WHEN last_loaded_at IS NULL THEN NULL
        ELSE floor(EXTRACT(epoch FROM age) / 86400)::int
    END AS days_since_load,
    -- Days past the point the next refresh was expected. 0 means on time.
    CASE
        WHEN expected_interval IS NULL OR last_loaded_at IS NULL THEN NULL
        ELSE GREATEST(0, floor(EXTRACT(epoch FROM (age - expected_interval)) / 86400))::int
    END AS days_overdue,
    record_count,
    loader_script,
    source_url,
    notes
FROM scored
ORDER BY
    CASE status
        WHEN 'overdue'      THEN 1
        WHEN 'due'          THEN 2
        WHEN 'never_loaded' THEN 3
        WHEN 'untracked'    THEN 4
        WHEN 'unknown'      THEN 5
        WHEN 'current'      THEN 6
        ELSE 7
    END,
    days_overdue DESC NULLS LAST,
    source_name;

COMMENT ON VIEW meta.v_data_freshness IS
    'Load status per dataset, worst first. The observer''s main query.';

-- Recount with the fixed estimate handling.
SELECT meta.refresh_data_source_stats(false);
