"""
db.py — Local Postgres connection and schema management for the portfolio project.

Deliberately different from pfs-analysis/utils.py and payor-lookup/data_loader.py
in two ways:

1. No Streamlit import. These are CLI scripts, not Streamlit apps, so config must
   not be coupled to st.secrets.
2. LOCAL ONLY, with a hard fail. The other modules fall through to Supabase when
   USE_LOCAL is unset. That behavior is wrong here: a missing env var must never
   quietly route brokerage data to a cloud database. If LOCAL_HOST is not set,
   this raises.

Everything lives in the `portfolio` schema, kept separate from `drinf` so
personal financial data and work data never share a namespace.
"""

from __future__ import annotations

import hashlib
import os
import warnings
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

SCHEMA = "portfolio"

# pandas warns on every read_sql with a raw DBAPI connection and suggests
# SQLAlchemy. psycopg2 is what the rest of this repo uses and it works fine, so
# silence the nag rather than print it on every query. Agents read this output.
warnings.filterwarnings(
    "ignore",
    message="pandas only supports SQLAlchemy connectable",
    category=UserWarning,
)

# Search for .env walking up the directory tree (same convention as the other apps).
for _env in [
    Path(__file__).parent / ".env",
    Path(__file__).parent.parent / ".env",
    Path(__file__).parent.parent.parent / ".env",
]:
    if _env.exists():
        load_dotenv(_env)
        break


class LocalDatabaseNotConfigured(RuntimeError):
    """Raised when the local Postgres connection is not configured."""


def get_db_config() -> dict:
    """Return local Postgres connection settings.

    Local only, on purpose. There is no cloud fallback: if LOCAL_HOST is missing
    we raise rather than reaching for SUPABASE_* and shipping brokerage data
    somewhere it does not belong.
    """
    host = os.getenv("LOCAL_HOST")
    if not host:
        raise LocalDatabaseNotConfigured(
            "LOCAL_HOST is not set. The portfolio project connects only to your "
            "local Postgres instance and will not fall back to a cloud database.\n"
            "Add these to your .env:\n"
            "  LOCAL_HOST=127.0.0.1\n"
            "  LOCAL_PORT=5432\n"
            "  LOCAL_DATABASE=postgres\n"
            "  LOCAL_USER=postgres\n"
            "  LOCAL_PASSWORD=..."
        )

    return {
        "host": host,
        "database": os.getenv("LOCAL_DATABASE", "postgres"),
        "user": os.getenv("LOCAL_USER", "postgres"),
        "password": os.getenv("LOCAL_PASSWORD", ""),
        "port": int(os.getenv("LOCAL_PORT", 5432)),
    }


@contextmanager
def get_conn():
    """Yield a psycopg2 connection, committing on success and rolling back on error."""
    conn = psycopg2.connect(**get_db_config())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query(sql: str, params: tuple | dict | None = None) -> pd.DataFrame:
    """Run a SELECT and return a DataFrame."""
    with get_conn() as conn:
        return pd.read_sql(sql, conn, params=params)


def execute(sql: str, params: tuple | dict | None = None) -> None:
    """Run a single statement."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)


def upsert(table: str, rows: list[dict], conflict_cols: list[str]) -> int:
    """Insert rows into portfolio.<table>, updating on conflict.

    Re-running a load with the same source file is a normal thing to do, so every
    write path here is idempotent. Returns the number of rows sent.
    """
    if not rows:
        return 0

    # Union of keys, not rows[0].keys(). Different parsers populate different
    # optional fields, so a batch can be non-uniform. Keying off the first row
    # would silently drop any column that row happens not to carry.
    cols, seen = [], set()
    for row in rows:
        for c in row:
            if c not in seen:
                seen.add(c)
                cols.append(c)

    update_cols = [c for c in cols if c not in conflict_cols]

    col_list = ", ".join(f'"{c}"' for c in cols)
    conflict_list = ", ".join(f'"{c}"' for c in conflict_cols)

    if update_cols:
        set_clause = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in update_cols)
        action = f"DO UPDATE SET {set_clause}"
    else:
        action = "DO NOTHING"

    sql = (
        f"INSERT INTO {SCHEMA}.{table} ({col_list}) VALUES %s "
        f"ON CONFLICT ({conflict_list}) {action}"
    )

    values = [tuple(r.get(c) for c in cols) for r in rows]
    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, sql, values, page_size=500)
    return len(values)


def stable_id(*parts) -> str:
    """Deterministic id from natural-key parts.

    Used for lot_id and txn_id so that re-loading the same export updates rows
    instead of duplicating them. Brokers do not give us stable identifiers, so
    we build our own from the values that define the record.
    """
    joined = "|".join("" if p is None else str(p).strip().lower() for p in parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:24]


SCHEMA_DDL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};

-- Accounts are declared by hand in config.yaml. Broker exports do not tell you
-- whether an account is taxable, a Roth, or a 401k, and the tax agent is inert
-- without that. Full account numbers are never stored, only the last four.
CREATE TABLE IF NOT EXISTS {SCHEMA}.accounts (
    account_id      text PRIMARY KEY,
    platform        text NOT NULL,
    account_label   text,
    tax_type        text NOT NULL,
    mask4           text,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT accounts_platform_chk
        CHECK (platform IN ('fidelity', 'empower', 'robinhood', 'manual')),
    -- roth_401k is separate from 401k on purpose. A 401k plan can hold both Roth
    -- and pre-tax money, and which is which is the single most consequential
    -- fact about the balance: Roth is never taxed again, pre-tax is ordinary
    -- income on withdrawal. Collapsing them throws that away.
    CONSTRAINT accounts_tax_type_chk
        CHECK (tax_type IN ('taxable', 'traditional_ira', 'roth_ira',
                            '401k', 'roth_401k', 'hsa', '529'))
);

CREATE TABLE IF NOT EXISTS {SCHEMA}.securities (
    symbol          text PRIMARY KEY,
    name            text,
    security_type   text,   -- equity | etf | mutual_fund | money_market | cash | option | other
    sector          text,
    industry        text,
    asset_class     text,   -- us_equity | intl_equity | bond | cash | alt | unknown
    enrich_status   text,   -- ok | not_found | error
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Point-in-time position snapshot. One row per account/symbol/date, so history
-- accumulates naturally and month-over-month comparison is just a self-join.
CREATE TABLE IF NOT EXISTS {SCHEMA}.holdings (
    as_of_date          date NOT NULL,
    account_id          text NOT NULL REFERENCES {SCHEMA}.accounts(account_id),
    symbol              text NOT NULL,
    quantity            numeric(20, 6),
    price               numeric(20, 6),
    market_value        numeric(20, 2),
    cost_basis_total    numeric(20, 2),
    unrealized_gl       numeric(20, 2),
    source              text,   -- 'broker', 'aggregator' or 'manual', drives dedupe precedence
    source_file         text,
    -- as_of_date is the snapshot this row belongs to. value_as_of is when the
    -- number was actually observed. They differ whenever an account was not
    -- refreshed for a snapshot, and without the distinction the database would
    -- assert something untrue about real money.
    value_as_of         date,
    loaded_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (as_of_date, account_id, symbol)
);

-- The tax-critical table. Without lot detail there is no wash-sale check, no
-- holding-period countdown, and no credible TLH candidate list.
CREATE TABLE IF NOT EXISTS {SCHEMA}.lots (
    lot_id          text PRIMARY KEY,
    as_of_date      date NOT NULL,
    account_id      text NOT NULL REFERENCES {SCHEMA}.accounts(account_id),
    symbol          text NOT NULL,
    acquired_date   date,
    quantity        numeric(20, 6),
    cost_basis      numeric(20, 2),
    cost_per_share  numeric(20, 6),
    market_value    numeric(20, 2),
    unrealized_gl   numeric(20, 2),
    term            text,   -- short | long | unknown, derived from acquired_date not parsed
    source_file     text,
    value_as_of     date,
    loaded_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS lots_symbol_idx ON {SCHEMA}.lots (symbol, as_of_date);
CREATE INDEX IF NOT EXISTS lots_account_idx ON {SCHEMA}.lots (account_id, as_of_date);

-- Transactions drive realized gain/loss and the plus/minus 30 day wash-sale window.
CREATE TABLE IF NOT EXISTS {SCHEMA}.transactions (
    txn_id          text PRIMARY KEY,
    account_id      text NOT NULL REFERENCES {SCHEMA}.accounts(account_id),
    trade_date      date,
    settle_date     date,
    action          text,   -- buy | sell | dividend | interest | fee | transfer | split | other
    symbol          text,
    quantity        numeric(20, 6),
    price           numeric(20, 6),
    amount          numeric(20, 2),
    description     text,
    source_file     text,
    loaded_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS txn_symbol_date_idx ON {SCHEMA}.transactions (symbol, trade_date);
CREATE INDEX IF NOT EXISTS txn_account_date_idx ON {SCHEMA}.transactions (account_id, trade_date);

CREATE TABLE IF NOT EXISTS {SCHEMA}.prices (
    symbol      text NOT NULL,
    price_date  date NOT NULL,
    close       numeric(20, 6),
    PRIMARY KEY (symbol, price_date)
);

CREATE TABLE IF NOT EXISTS {SCHEMA}.fundamentals (
    symbol              text NOT NULL,
    as_of_date          date NOT NULL,
    pe                  numeric(20, 4),
    forward_pe          numeric(20, 4),
    market_cap          numeric(24, 2),
    div_yield           numeric(10, 6),
    beta                numeric(10, 4),
    revenue_growth      numeric(10, 6),
    profit_margin       numeric(10, 6),
    fifty_two_week_high numeric(20, 6),
    fifty_two_week_low  numeric(20, 6),
    next_earnings_date  date,
    PRIMARY KEY (symbol, as_of_date)
);

-- Every agent run records its metrics snapshot here. This is what lets the next
-- run report what CHANGED rather than restating the portfolio from scratch.
CREATE TABLE IF NOT EXISTS {SCHEMA}.report_runs (
    run_id          text PRIMARY KEY,
    agent           text NOT NULL,
    run_at          timestamptz NOT NULL DEFAULT now(),
    as_of_date      date,
    report_path     text,
    summary_json    jsonb
);
CREATE INDEX IF NOT EXISTS report_runs_agent_idx ON {SCHEMA}.report_runs (agent, run_at DESC);
"""


# SCHEMA_DDL uses CREATE TABLE IF NOT EXISTS, which silently does nothing on a
# database that already has the table. Anything added after the first release has
# to arrive as an explicit ALTER or existing installs never get it. Every
# statement here is idempotent and safe to re-run.
MIGRATIONS_DDL = f"""
ALTER TABLE {SCHEMA}.holdings ADD COLUMN IF NOT EXISTS value_as_of date;
UPDATE {SCHEMA}.holdings SET value_as_of = as_of_date WHERE value_as_of IS NULL;

ALTER TABLE {SCHEMA}.lots ADD COLUMN IF NOT EXISTS value_as_of date;
UPDATE {SCHEMA}.lots SET value_as_of = as_of_date WHERE value_as_of IS NULL;

-- CHECK constraints cannot be altered in place, so drop and recreate.
ALTER TABLE {SCHEMA}.accounts DROP CONSTRAINT IF EXISTS accounts_platform_chk;
ALTER TABLE {SCHEMA}.accounts ADD CONSTRAINT accounts_platform_chk
    CHECK (platform IN ('fidelity', 'empower', 'robinhood', 'manual'));

ALTER TABLE {SCHEMA}.accounts DROP CONSTRAINT IF EXISTS accounts_tax_type_chk;
ALTER TABLE {SCHEMA}.accounts ADD CONSTRAINT accounts_tax_type_chk
    CHECK (tax_type IN ('taxable', 'traditional_ira', 'roth_ira',
                        '401k', 'roth_401k', 'hsa', '529'));
"""


def init_schema() -> None:
    """Create the schema, all tables, and apply migrations. Safe to re-run."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_DDL)
            cur.execute(MIGRATIONS_DDL)


def latest_as_of_date() -> str | None:
    """Most recent holdings snapshot date, or None if nothing is loaded."""
    df = query(f"SELECT max(as_of_date) AS d FROM {SCHEMA}.holdings")
    if df.empty or pd.isna(df.iloc[0]["d"]):
        return None
    return str(df.iloc[0]["d"])


if __name__ == "__main__":
    cfg = get_db_config()
    print(f"Connecting to {cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['database']}")
    init_schema()
    print(f"Schema '{SCHEMA}' is ready.")
    d = latest_as_of_date()
    print(f"Latest holdings snapshot: {d or '(none loaded yet)'}")
