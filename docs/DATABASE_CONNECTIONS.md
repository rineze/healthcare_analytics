# Database Connections: The Standard

One page, referenceable from any project in this repo. If you're wiring up a new
script or dashboard, copy the pattern in [Section 3](#3-the-standard-pattern) and stop there.

---

## 1. There is only one database

This is the part that trips everyone up, so it goes first.

**Supabase IS the Postgres database.** It is not a separate system, not a layer
on top, not an alternative to Postgres. Supabase is managed Postgres (currently
17.6) with some extras bolted on (auth, storage, an auto-generated REST/GraphQL
API). When you connect to Supabase with `psycopg2`, you are connecting to plain
Postgres.

**`psql` is a command-line client.** It is not a database. It is the terminal
program you use to talk to a Postgres server, and it can point at any of them.
"Checking the psql database" means "checking Postgres."

So the real inventory is:

| Name | What it actually is | When it's used |
|---|---|---|
| **Supabase** | The production Postgres. Hosted, us-east-1, project ref `numdlqsfydtypeurijae`. | Everything deployed. Streamlit Cloud dashboards read from here. |
| **Local Postgres** | A Postgres instance on your own machine at `127.0.0.1:5432`. | Local dev, and staging bulk loads before pushing to Supabase. |
| `psql` / `psycopg2` / `pg_isready` | Clients. Tools that talk to either of the above. | Both. |

Two environments, one engine. That's the whole picture.

---

## 2. Connection targets

### Supabase: three ways in

Supabase gives you three endpoints and they are not interchangeable.

| Mode | Host | Port | User | Use it for |
|---|---|---|---|---|
| **Direct** | `db.<ref>.supabase.co` | 5432 | `postgres` | Admin work, migrations, `pg_dump`. IPv6-only unless you have the IPv4 add-on, which is why it often "just hangs" from corporate networks. |
| **Session pooler** | `aws-1-us-east-1.pooler.supabase.com` | 5432 | `postgres.<ref>` | **Our default.** Behaves like a normal Postgres connection. Supports prepared statements, temp tables, `COPY`. This is what the bulk loaders use. |
| **Transaction pooler** | `aws-1-us-east-1.pooler.supabase.com` | 6543 | `postgres.<ref>` | Many short-lived connections (serverless, high-concurrency web apps). No prepared statements, no session state. |

Note the username changes. Pooler connections need `postgres.<project_ref>`, not
plain `postgres`. Getting a "Tenant or user not found" error almost always means
you used the pooler host with the direct-connection username.

We use **session pooler on 5432** as the standard. It is the fewest surprises
with `psycopg2`, and our workload (a handful of Streamlit apps, batch loaders)
doesn't need transaction mode.

### Local Postgres

`127.0.0.1:5432`, database `postgres`, user `postgres`. Password comes from
`LOCAL_PASSWORD`. Nothing exotic.

---

## 3. The standard pattern

Every app and loader resolves its connection through the same four-step
fallback chain. Highest priority first:

1. **`USE_LOCAL=true` in `.env`** plus `LOCAL_HOST` set → use local Postgres.
   This is the dev override. It wins over everything.
2. **Streamlit secrets** (`st.secrets["database"]`) → use those. This is how
   Streamlit Cloud deployments get credentials. Fails silently when not running
   under Streamlit, which is intentional.
3. **`SUPABASE_*` env vars** → use Supabase. This is the normal path for
   scripts and loaders run from a terminal.
4. **Fallback** → local defaults, so a fresh clone with no config still tries
   `127.0.0.1` instead of blowing up with a cryptic error.

### Environment variables

Put these in a `.env` at the repo root. It is gitignored. It should stay that way.

```bash
# Toggle: flip to true to force local Postgres for development
USE_LOCAL=false

# Local Postgres
LOCAL_HOST=127.0.0.1
LOCAL_PORT=5432
LOCAL_DATABASE=postgres
LOCAL_USER=postgres
LOCAL_PASSWORD=your_local_password

# Supabase (session pooler)
SUPABASE_HOST=aws-1-us-east-1.pooler.supabase.com
SUPABASE_PORT=5432
SUPABASE_DATABASE=postgres
SUPABASE_USER=postgres.numdlqsfydtypeurijae
SUPABASE_PASSWORD=your_supabase_password
```

For Streamlit Cloud, the same values go in `.streamlit/secrets.toml` (also
gitignored) under a `[database]` block:

```toml
[database]
host = "aws-1-us-east-1.pooler.supabase.com"
port = 5432
database = "postgres"
user = "postgres.numdlqsfydtypeurijae"
password = "your_supabase_password"
```

### The code

`pfs-analysis/utils.py` holds the reference implementation of `get_db_config()`
and `get_connection()`. New code should import from there rather than
re-implementing the chain. See [Section 6](#6-known-drift) for where that isn't
true yet.

```python
from utils import get_connection

conn = get_connection()
```

---

## 4. Schema map

| Schema | Contents | Consumed by |
|---|---|---|
| `drinf` | The analytics core. MPFS RVUs and GPCI, Medicare utilization, MA enrollment and penetration, price transparency rates, payor and plan reference tables, plus the `v_*` views. | `pfs-analysis`, `ma-dashboard`, `payor-lookup` |
| `cms` | Raw CMS provider data. PPEF enrollment, practice locations, reassignments, revalidation, OIG exclusions. | Provider-level lookups |
| `payor_tracker` | Contract tracking. Payors, TINs, contracts, contract links. | Contract modeling |
| `public` | Observation and bulk-record tables. The only schema with RLS enabled and `anon`/`authenticated` grants, because it's reachable through the Supabase API. | Supabase-API-facing app |

**Views live in `drinf`** and carry the `v_` prefix: `v_rvu_clean`,
`v_gpci_clean`, `v_cf_clean`, `v_mpfs_allowed`, `v_mpfs_allowed_yoy`,
`v_gpci_yoy`, `v_mpfs_decomp`, `v_plan_master`, `v_rvu_mix_metrics`. Plus
`cms.provider_enrollment_map`.

DDL for most of these is in `pfs-analysis/sql/create_views.sql`.

---

## 5. Access model, and why `public` is different

`drinf`, `cms`, and `payor_tracker` have **RLS off and no `anon`/`authenticated`
grants**. That is correct and deliberate. Those schemas are reachable only with
the Postgres password over a direct/pooled connection, which is what the
Streamlit apps and loaders use. The Supabase anon key cannot see them at all.

`public` is the exception: RLS is **on**, every table has policies, and both
`anon` and `authenticated` hold `SELECT`. That's the right shape for
API-facing tables, but it does mean anything you put in `public` is reachable
by anyone holding the publishable anon key. **Default to putting new analytics
tables in `drinf`, not `public`.**

---

## 6. Known drift

Being honest about the current state, because a standard nobody follows isn't a standard.

- **`get_db_config()` is copy-pasted three times**: `pfs-analysis/utils.py`,
  `ma-dashboard/data_loader.py`, `payor-lookup/data_loader.py`. Three copies
  means three things to fix when the pattern changes.
- **None of the seven loaders use the chain.** `load_gpci.py`, `load_mpfs.py`,
  `load_utilization.py`, `load_market_definitions.py`, and `load_ma_data.py`
  read `LOCAL_*` only, so they cannot target Supabase without editing code.
  `load_price_transparency.py` reads `SUPABASE_*` only, with no local fallback
  and no defaults, so it fails with a confusing error if the vars are missing.
- **`load_cbsa_markets.py` hardcodes** the pooler host, port, and the
  `postgres.numdlqsfydtypeurijae` username. It also hardcodes a Windows path
  (`C:\dev\healthcare_analytics\...`) for its input file.
- **Key naming is inconsistent**: some configs use `database`, others `dbname`.
  Both work (`psycopg2` accepts either), but pick one. `dbname` is the actual
  libpq keyword.
- **`create_views.sql` defines `v_cf_clean` twice** (lines 75 and 90). The
  second definition drops and replaces the first, so the file works, but the
  dead first version should go.
- **Three live views have no DDL in the repo**: `drinf.v_plan_master`,
  `drinf.v_rvu_mix_metrics`, `cms.provider_enrollment_map`. If the database is
  ever rebuilt from source, they don't come back.

---

## 7. Quick reference

```bash
# Is it up?
pg_isready -h aws-1-us-east-1.pooler.supabase.com -p 5432 \
           -U postgres.numdlqsfydtypeurijae

# Connect
psql "postgresql://postgres.numdlqsfydtypeurijae:$SUPABASE_PASSWORD@aws-1-us-east-1.pooler.supabase.com:5432/postgres"

# Connect to local instead
psql "postgresql://postgres:$LOCAL_PASSWORD@127.0.0.1:5432/postgres"
```

Useful once you're in:

```sql
\dn                          -- list schemas
\dt drinf.*                  -- list tables in drinf
\dv drinf.*                  -- list views in drinf
\d drinf.mpfs_rvu            -- describe a table

-- size and connection load
SELECT pg_size_pretty(pg_database_size(current_database()));
SELECT count(*), state FROM pg_stat_activity GROUP BY state;

-- cache hit ratio, want > 99%
SELECT round(100.0*sum(blks_hit)/nullif(sum(blks_hit)+sum(blks_read),0), 2)
FROM pg_stat_database WHERE datname = current_database();

-- data freshness for the cms schema
SELECT source_name, refresh_cadence, last_loaded_at, record_count
FROM cms.data_sources ORDER BY last_loaded_at;
```
