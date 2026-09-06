-- ============================================================================
-- 001_meta_data_sources.sql
--
-- Creates meta.data_sources: one row per external dataset this repo loads,
-- so "what is stale?" is a query instead of tribal knowledge.
--
-- cms.data_sources already did this for the six CMS provider datasets. This
-- promotes the idea to cover every schema and adds the two columns that
-- actually drive automation:
--
--   url_stability    can a script fetch this, or does a human/agent have to
--                    go find the new file? CMS puts UUIDs in some paths that
--                    change every publication, so no script can guess them.
--   freshness_column which timestamp column marks a load. The loaders drifted
--                    across four names for this: load_date, loaded_at,
--                    created_at, created_date.
--
-- Idempotent. Safe to re-run.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS meta;

COMMENT ON SCHEMA meta IS
    'Operational metadata about the repo''s datasets. Not analytics data.';

-- Not an API-facing schema. Keep it off the anon/authenticated surface.
REVOKE ALL ON SCHEMA meta FROM anon, authenticated;


-- ----------------------------------------------------------------------------
-- The ledger
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS meta.data_sources (
    source_name         text PRIMARY KEY,
    target_schema       text NOT NULL,
    target_table        text NOT NULL,
    source_url          text,

    url_stability       text NOT NULL DEFAULT 'unknown'
        CHECK (url_stability IN ('stable', 'predictable', 'unstable', 'none', 'unknown')),

    refresh_cadence     text NOT NULL DEFAULT 'unknown'
        CHECK (refresh_cadence IN ('monthly', 'quarterly', 'annual', 'static', 'manual', 'unknown')),

    loader_script       text,
    freshness_column    text,

    last_loaded_at      timestamptz,
    last_source_period  text,
    record_count        bigint,
    notes               text,
    updated_at          timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE meta.data_sources IS
    'One row per external dataset. Drives freshness checks and load planning.';

COMMENT ON COLUMN meta.data_sources.url_stability IS
    'stable = fixed URL, safe to cron. predictable = derivable from a date '
    'pattern. unstable = path contains a UUID or similar that changes each '
    'publication, so someone has to go look it up. none = no URL, file arrives '
    'by hand (e.g. hospital MRFs).';

COMMENT ON COLUMN meta.data_sources.freshness_column IS
    'Name of the timestamp column in the target table that marks a load. NULL '
    'when the table has none, in which case last_loaded_at is maintained by '
    'whatever runs the load.';

COMMENT ON COLUMN meta.data_sources.last_source_period IS
    'The period the loaded data covers (e.g. 2026-02, Q1 2026), which is not '
    'the same as when it was loaded.';


-- ----------------------------------------------------------------------------
-- Recompute record_count and last_loaded_at from the real tables
--
-- exact = false uses pg_class.reltuples, which is instant but approximate and
-- only as fresh as the last ANALYZE. Use it for interactive checks against the
-- multi-million row cms tables. exact = true does a real count(*).
-- ----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION meta.refresh_data_source_stats(exact boolean DEFAULT true)
RETURNS integer
LANGUAGE plpgsql
-- Pinned search_path: an unpinned one trips the function_search_path_mutable
-- security lint.
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
        -- Skip rows pointing at a table that doesn't exist (yet).
        IF to_regclass(format('%I.%I', r.target_schema, r.target_table)) IS NULL THEN
            CONTINUE;
        END IF;

        IF exact THEN
            EXECUTE format('SELECT count(*) FROM %I.%I', r.target_schema, r.target_table)
            INTO n;
        ELSE
            SELECT GREATEST(c.reltuples, 0)::bigint INTO n
            FROM pg_class c
            WHERE c.oid = to_regclass(format('%I.%I', r.target_schema, r.target_table));
        END IF;

        ts := NULL;
        IF r.freshness_column IS NOT NULL THEN
            EXECUTE format('SELECT max(%I)::timestamptz FROM %I.%I',
                           r.freshness_column, r.target_schema, r.target_table)
            INTO ts;
        END IF;

        UPDATE meta.data_sources
           SET record_count   = n,
               -- Keep a seeded timestamp if the table has no freshness column.
               last_loaded_at = COALESCE(ts, last_loaded_at),
               updated_at     = now()
         WHERE source_name = r.source_name;

        n_updated := n_updated + 1;
    END LOOP;

    RETURN n_updated;
END;
$$;

COMMENT ON FUNCTION meta.refresh_data_source_stats(boolean) IS
    'Repopulate record_count and last_loaded_at from the target tables. '
    'Returns the number of rows refreshed.';


-- ----------------------------------------------------------------------------
-- What is stale?
-- ----------------------------------------------------------------------------

CREATE OR REPLACE VIEW meta.v_data_freshness AS
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
            WHEN e.refresh_cadence IN ('static', 'manual')       THEN 'n/a'
            WHEN e.last_loaded_at IS NULL                        THEN 'never_loaded'
            WHEN e.expected_interval IS NULL                     THEN 'unknown'
            WHEN now() - e.last_loaded_at <= e.expected_interval THEN 'current'
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
    -- Whole days past the point the next refresh was expected.
    CASE
        WHEN expected_interval IS NULL OR last_loaded_at IS NULL THEN NULL
        ELSE GREATEST(0, EXTRACT(day FROM age - expected_interval))::int
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
        WHEN 'unknown'      THEN 4
        WHEN 'current'      THEN 5
        ELSE 6
    END,
    days_overdue DESC NULLS LAST,
    source_name;

COMMENT ON VIEW meta.v_data_freshness IS
    'Load status per dataset, worst first. The observer''s main query.';


-- ----------------------------------------------------------------------------
-- Seed: the six CMS datasets already tracked in cms.data_sources
-- ----------------------------------------------------------------------------

INSERT INTO meta.data_sources (
    source_name, target_schema, target_table, source_url,
    url_stability, refresh_cadence, loader_script, freshness_column,
    last_loaded_at, record_count, notes
)
SELECT
    ds.source_name,
    'cms',
    ds.source_name,           -- table name matches source name for all six
    ds.source_url,
    CASE
        -- A UUID in the path means the URL changes every publication.
        WHEN ds.source_url ~ '/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-' THEN 'unstable'
        WHEN ds.source_url LIKE '%data.cms.gov%'                      THEN 'predictable'
        ELSE 'stable'
    END,
    ds.refresh_cadence,
    NULL,                     -- no loader for these lives in this repo
    NULL,                     -- cms tables carry no load timestamp column
    ds.last_loaded_at,
    ds.record_count,
    ds.notes
FROM cms.data_sources ds
ON CONFLICT (source_name) DO NOTHING;


-- ----------------------------------------------------------------------------
-- Seed: everything loaded by this repo
-- ----------------------------------------------------------------------------

INSERT INTO meta.data_sources (
    source_name, target_schema, target_table, source_url,
    url_stability, refresh_cadence, loader_script, freshness_column, notes
) VALUES

-- MPFS. Published annually in the PFS final rule, with quarterly corrections.
-- Distributed as zips off a CMS landing page, so the file has to be fetched
-- by hand before the loader runs.
('mpfs_rvu', 'drinf', 'mpfs_rvu',
 'https://www.cms.gov/medicare/payment/fee-schedules/physician/pfs-relative-value-files',
 'unstable', 'annual', 'pfs-analysis/load_mpfs.py', 'load_date',
 'Manual zip download. Loader reads from PFS_DATA_DIR.'),

('mpfs_gpci', 'drinf', 'mpfs_gpci',
 'https://www.cms.gov/medicare/payment/fee-schedules/physician/pfs-relative-value-files',
 'unstable', 'annual', 'pfs-analysis/load_gpci.py', 'load_date',
 'Same source zip as mpfs_rvu. File layout differs 2018 vs 2022+.'),

-- Utilization. The loader downloads directly, but each year is a distinct
-- UUID URL hardcoded in the script, so a new year needs a code edit.
('medicare_utilization', 'drinf', 'medicare_utilization',
 'https://data.cms.gov/provider-summary-by-type-of-service/medicare-physician-other-practitioners',
 'unstable', 'annual', 'pfs-analysis/load_utilization.py', NULL,
 'Per-year UUID URLs hardcoded in the loader. No load timestamp column; '
 'last_loaded_at must be set by whatever runs the load.'),

-- MA enrollment. Monthly CMS release, all three tables from one loader.
('ma_cpsc_enrollment', 'drinf', 'ma_cpsc_enrollment',
 'https://www.cms.gov/data-research/statistics-trends-and-reports/medicare-advantagepart-d-contract-and-enrollment-data',
 'predictable', 'monthly', 'ma-dashboard/load_ma_data.py', 'load_date',
 'Monthly CPSC enrollment by contract/plan/county.'),

('ma_county_penetration', 'drinf', 'ma_county_penetration',
 'https://www.cms.gov/data-research/statistics-trends-and-reports/medicare-advantagepart-d-contract-and-enrollment-data',
 'predictable', 'monthly', 'ma-dashboard/load_ma_data.py', 'load_date',
 'Monthly MA penetration by county.'),

('ma_plan_directory', 'drinf', 'ma_plan_directory',
 'https://www.cms.gov/data-research/statistics-trends-and-reports/medicare-advantagepart-d-contract-and-enrollment-data',
 'predictable', 'monthly', 'ma-dashboard/load_ma_data.py', 'load_date',
 'Contract-level plan directory.'),

-- Hospital price transparency. Files come from each hospital, not a feed.
('pt_rates', 'drinf', 'pt_rates', NULL,
 'none', 'annual', 'pfs-analysis/load_price_transparency.py', 'load_date',
 'Hospital MRF files, collected per hospital. Hospitals must republish at '
 'least annually but do so on their own schedules.'),

('pt_hospitals', 'drinf', 'pt_hospitals', NULL,
 'none', 'manual', 'pfs-analysis/load_price_transparency.py', 'created_at',
 'Hospital registry, grows as MRFs are added.'),

-- Reference data that changes only when we change it.
('county_to_market', 'drinf', 'county_to_market',
 'https://www.census.gov/geographies/reference-files/time-series/demo/metro-micro/delineation-files.html',
 'predictable', 'static', 'pfs-analysis/load_cbsa_markets.py', 'created_date',
 'Census CBSA delineation plus hand-curated TN markets from '
 'load_market_definitions.py. Only changes when Census redelineates.'),

-- Reference tables with no loader in this repo. Recorded so they are not
-- invisible; someone maintains these by hand.
('plan_lob_reference', 'drinf', 'plan_lob_reference', NULL,
 'none', 'manual', NULL, 'loaded_at',
 'No loader in this repo. Maintained manually.'),

('ref_issuer', 'drinf', 'ref_issuer', NULL,
 'none', 'manual', NULL, 'loaded_at',
 'No loader in this repo. Maintained manually.'),

('ref_ma_landscape', 'drinf', 'ref_ma_landscape', NULL,
 'none', 'manual', NULL, 'loaded_at',
 'No loader in this repo. Maintained manually.'),

('ref_hix_landscape', 'drinf', 'ref_hix_landscape', NULL,
 'none', 'manual', NULL, 'loaded_at',
 'No loader in this repo. Maintained manually.'),

('ref_medicaid_landscape', 'drinf', 'ref_medicaid_landscape', NULL,
 'none', 'manual', NULL, 'loaded_at',
 'No loader in this repo. Maintained manually.')

ON CONFLICT (source_name) DO NOTHING;


-- Populate counts and timestamps from the tables themselves. Estimates here so
-- the migration stays fast against the multi-million row cms tables; call
-- meta.refresh_data_source_stats(true) for exact counts.
SELECT meta.refresh_data_source_stats(false);
