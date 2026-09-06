-- ============================================================================
-- 003_missing_unique_constraints.sql
--
-- Adds the unique indexes four loaders assume exist but which were never
-- created. Without them:
--
--   ma-dashboard/load_ma_data.py cannot run at all. All three of its INSERTs
--   use ON CONFLICT, and Postgres validates the ON CONFLICT specification at
--   PLAN time, not on conflict. With no matching unique index it raises
--   42P10 "there is no unique or exclusion constraint matching the ON CONFLICT
--   specification" even when zero rows would conflict. So the MA data, the
--   most overdue dataset in the ledger, could not be refreshed.
--
--   pfs-analysis/load_mpfs.py silently duplicates. It does a bare
--   INSERT ... VALUES with no ON CONFLICT and no prior DELETE, against a table
--   whose only key is a serial id. Re-running it for an already-loaded year
--   doubles that year's rows and every downstream RVU total with it.
--
-- Verified clean before adding (see the counts in each comment), so all four
-- indexes build without a dedupe step.
--
-- Idempotent. Safe to re-run.
-- ============================================================================

-- CPSC enrollment. Loader's ON CONFLICT target at load_ma_data.py:301.
-- Verified: 2,373,666 rows across 2,373,666 distinct keys, 0 duplicates,
-- and 0 NULLs in any key column.
CREATE UNIQUE INDEX IF NOT EXISTS uq_ma_cpsc_enrollment_key
    ON drinf.ma_cpsc_enrollment (report_month, contract_id, plan_id, state, county);

-- Plan directory. Loader's ON CONFLICT target at load_ma_data.py:361.
-- Verified: 921 rows, 921 distinct keys, 0 NULLs.
CREATE UNIQUE INDEX IF NOT EXISTS uq_ma_plan_directory_key
    ON drinf.ma_plan_directory (report_month, contract_id);

-- County penetration. Loader's ON CONFLICT target at load_ma_data.py:460.
-- Verified: 3,188 rows, 3,188 distinct keys, 0 NULL fips.
-- Note the loader's CREATE TABLE declares PRIMARY KEY (report_month, fips),
-- so this table was created outside that path at some point.
CREATE UNIQUE INDEX IF NOT EXISTS uq_ma_county_penetration_key
    ON drinf.ma_county_penetration (report_month, fips);

-- MPFS RVU natural key. mpfs_rvu.modifier is NULL on 141,571 of 160,360 rows
-- (88%), and a default unique index treats NULLs as distinct, which would let
-- duplicate (year, hcpcs) rows straight through. NULLS NOT DISTINCT (PG 15+,
-- this database is 17.6) makes NULL equal to NULL so the key actually holds.
-- Verified: 160,360 rows, 160,360 distinct keys under NULL-equal semantics.
CREATE UNIQUE INDEX IF NOT EXISTS uq_mpfs_rvu_key
    ON drinf.mpfs_rvu (mpfs_year, hcpcs, modifier) NULLS NOT DISTINCT;
