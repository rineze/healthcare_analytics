-- ============================================================================
-- 005_reader_access.sql
--
-- Widens db_observer from "can read the freshness ledger" to "can read the
-- data", still without being able to change anything.
--
-- Migration 004 scoped this role to the meta schema and explicitly revoked
-- drinf, cms and payor_tracker. That was right for a freshness checker, which
-- only ever reads meta.v_data_freshness. It is too narrow for an agent you ask
-- questions of: "what does our exchange data look like" needs
-- drinf.ref_hix_landscape, and the role could not see it.
--
-- The correction is not "give it write access". It is recognising that reads
-- and writes deserve different rules:
--
--   SELECT   cannot damage anything. A wrong query returns a wrong answer,
--            which you can see and correct. No allowlist, no approval.
--   WRITE    can destroy or corrupt. Allowlisted in db_actions.py, and gated
--            behind an explicit approval tap.
--
-- So this grants broad read and no write at all. The engineer can answer any
-- question and produce any export, while still being unable to modify a single
-- row without you approving a named action.
--
-- Idempotent. Safe to re-run.
-- ============================================================================

-- 004 revoked these. Put them back as read-only.
GRANT USAGE ON SCHEMA drinf, cms, payor_tracker TO db_observer;

GRANT SELECT ON ALL TABLES IN SCHEMA drinf, cms, payor_tracker TO db_observer;

-- New tables and views should be readable without another migration.
ALTER DEFAULT PRIVILEGES IN SCHEMA drinf, cms, payor_tracker
    GRANT SELECT ON TABLES TO db_observer;

COMMENT ON ROLE db_observer IS
    'Read-only role for the Telegram agents. SELECT across meta, drinf, cms '
    'and payor_tracker. No INSERT, UPDATE, DELETE or DDL anywhere: writes go '
    'through the approval-gated allowlist in scripts/db_actions.py, which runs '
    'the loaders under separate credentials.';

-- The public schema is deliberately excluded. It is the Supabase API-facing
-- schema with RLS and anon grants, and nothing the agent answers comes from it.
