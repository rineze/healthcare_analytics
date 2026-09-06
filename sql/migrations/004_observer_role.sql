-- ============================================================================
-- 004_observer_role.sql
--
-- A read-only role for the scheduled observer.
--
-- The observer runs in GitHub Actions, which means its credential lives in a
-- repository secret. That credential should not be able to write anything, so
-- this role gets SELECT on the meta schema and nothing else. It cannot read
-- drinf, cms or payor_tracker, and it cannot write anywhere at all.
--
-- The role is created WITHOUT a password and WITHOUT login on purpose, so no
-- credential ever passes through a migration file or a chat transcript. Set the
-- password yourself, once, then put it in the GitHub secret:
--
--     ALTER ROLE db_observer LOGIN PASSWORD '<pick something long>';
--
-- Connecting through the Supabase session pooler, the username is the role name
-- plus the project ref, same pattern as the postgres user:
--
--     db_observer.numdlqsfydtypeurijae
--
-- Idempotent. Safe to re-run.
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'db_observer') THEN
        CREATE ROLE db_observer NOLOGIN;
    END IF;
END $$;

COMMENT ON ROLE db_observer IS
    'Read-only role for the scheduled freshness observer. SELECT on meta only.';

GRANT USAGE ON SCHEMA meta TO db_observer;
GRANT SELECT ON ALL TABLES IN SCHEMA meta TO db_observer;

-- Future tables and views in meta should be readable too, without another
-- migration each time.
ALTER DEFAULT PRIVILEGES IN SCHEMA meta
    GRANT SELECT ON TABLES TO db_observer;

-- Explicitly keep it out of the analytics schemas. These are already ungranted,
-- but stating it means a later blanket GRANT does not quietly widen the
-- observer's reach.
REVOKE ALL ON SCHEMA drinf, cms, payor_tracker FROM db_observer;
