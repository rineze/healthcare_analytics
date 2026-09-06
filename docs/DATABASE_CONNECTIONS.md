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

`healthcare_db.py` at the repo root is the only place this chain is defined.
Every app and loader imports from it. Do not write another `DB_CONFIG` dict.

Because the projects are sibling directories rather than an installed package,
consumers add the repo root to `sys.path` first. That works both locally and on
Streamlit Cloud, which clones the whole repo:

```python
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from healthcare_db import get_connection
```

Then:

```python
conn = get_connection()             # follow the chain
conn = get_connection("local")      # force local
conn = get_connection("supabase")   # force Supabase
```

**Loaders use `loader_connection()` instead.** It does the same resolution, but
prints the resolved target before handing back a connection and accepts
`--target local|supabase` on the command line. A script about to write two
million rows should say out loud where they are going, and you should be able to
redirect it without editing code:

```bash
python ma-dashboard/load_ma_data.py                     # follow the chain
python ma-dashboard/load_ma_data.py --target local      # force local
```

To check connectivity without starting an app:

```bash
python healthcare_db.py             # test whatever the chain resolves to
python healthcare_db.py supabase    # test Supabase specifically
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

## 6. The freshness ledger

`meta.data_sources` is one row per external dataset, and it is the answer to
"what needs loading?" It replaces knowing this by heart.

`cms.data_sources` had the right idea but only covered the six CMS provider
datasets and only recorded what had already happened. `meta.data_sources` covers
every schema and adds the two columns that actually drive decisions:

| Column | Why it matters |
|---|---|
| `url_stability` | `stable` means a fixed URL you can cron. `predictable` means derivable from a date pattern. `unstable` means the path contains a UUID that changes every publication, so a human or an agent has to go find it. `none` means the file arrives by hand. |
| `freshness_column` | Which timestamp column marks a load. The loaders drifted across four names for this: `load_date`, `loaded_at`, `created_at`, `created_date`. |

Query it through the view, which sorts worst-first:

```sql
SELECT source_name, status, days_overdue, url_stability, loader_script
FROM meta.v_data_freshness;
```

`status` is one of:

| Status | Meaning |
|---|---|
| `overdue` | Past 1.5x its cadence. Load it. |
| `due` | Past its cadence but inside the grace window. |
| `never_loaded` | No load timestamp and no rows. |
| `untracked` | Rows present but the table has no load timestamp, so staleness is unknowable. `drinf.medicare_utilization` is the current example. |
| `current` | Inside its cadence. |
| `n/a` | Static or manually maintained, no cadence to miss. |

To recompute counts and timestamps from the tables themselves:

```sql
SELECT meta.refresh_data_source_stats();       -- exact counts
SELECT meta.refresh_data_source_stats(false);  -- fast estimates
```

`cms.data_sources` still exists and is still written by the CMS loaders, which
live outside this repo. Treat `meta.data_sources` as the read surface.

---

## 7. Data integrity audit

Run 2026-09-06. Checked for duplicates, NULLs in key columns, and value sanity.

**Values are clean.** No negative RVUs, no non-positive negotiated rates, no bad
GPCI factors, no negative enrollment, penetration all within 0-100, and the 2026
conversion factor is a single consistent value ($33.4009). Two cosmetic oddities
worth knowing but not fixing: 23 rows in `ma_county_penetration` have NULL
penetration, and 4 rows show `enrolled > eligibles`, both straight from the CMS
file.

**No duplicates anywhere**, on any table's natural key.

**The problems were structural.** Four loaders assumed unique constraints that
did not exist:

| Table | Consequence |
|---|---|
| `ma_cpsc_enrollment`, `ma_plan_directory`, `ma_county_penetration` | `load_ma_data.py` could not run at all. Postgres validates an `ON CONFLICT` spec at plan time, so with no matching unique index it raises `42P10` even when zero rows would conflict. The most overdue dataset was also the one that could not be refreshed. |
| `mpfs_rvu` | `load_mpfs.py` did a bare `INSERT` with no `ON CONFLICT` and no prior `DELETE`, against a table keyed only on a serial. Re-running it for an already-loaded year appended a second full copy and doubled every downstream RVU total. |

Fixed in `sql/migrations/003_missing_unique_constraints.sql` plus an upsert in
`load_mpfs.py`. Note `uq_mpfs_rvu_key` uses `NULLS NOT DISTINCT`, because
`modifier` is NULL on 88% of rows and a default unique index treats NULLs as
distinct, which would have let duplicates through anyway.

Every other loader's `ON CONFLICT` target was already backed by a real
constraint and needed no change.

### Open

- **`ma_cpsc_enrollment.fips` is 100% NULL** across all 2.37M rows. The loader
  creates the column and an index on it but never populates it. The dashboard
  already works around this by sourcing FIPS from `ma_county_penetration`
  instead, so nothing is visibly broken, but the column and its index are dead
  weight and a trap for anyone who tries to join on them. Populate it during the
  next MA reload, or drop both.

---

## 8. Known drift

Being honest about the current state, because a standard nobody follows isn't a standard.

- **Three live views have no DDL in the repo**: `drinf.v_plan_master`,
  `drinf.v_rvu_mix_metrics`, `cms.provider_enrollment_map`. If the database is
  ever rebuilt from source, they don't come back. This is the one worth fixing
  next.
- **The CMS loaders are not in this repo.** Six datasets in the `cms` schema are
  loaded by something else, so `meta.data_sources.loader_script` is null for
  them and nothing here can refresh them.
- **`drinf.medicare_utilization` has no load timestamp column**, so its
  freshness cannot be computed. Its loader also hardcodes a separate UUID URL
  per year, so adding a year needs a code edit.
- **Four names for one concept**: `load_date`, `loaded_at`, `created_at`,
  `created_date`. Recorded per-table in `meta.data_sources.freshness_column`
  rather than renamed, since renaming columns would break the loaders.

### Fixed

- ~~`get_db_config()` copy-pasted three times~~. Now only in `healthcare_db.py`.
- ~~None of the seven loaders use the chain~~. All seven now do.
- ~~`load_utilization.py` used `os.getenv` with no `import os`~~. It raised
  `NameError` on import and could never have run. Fixed.
- ~~`load_cbsa_markets.py` hardcodes pooler host, port, and username~~. Now
  resolves through the shared module. It still opens two connections, which is
  correct: it reads from local and upserts to both.
- ~~Hardcoded `C:\dev\...` paths~~. Now `PFS_DATA_DIR` and `CBSA_FILE` env vars
  with repo-relative defaults.
- ~~`database` vs `dbname` inconsistency~~. Standardized on `dbname`.
- ~~`create_views.sql` defines `v_cf_clean` twice~~. Dead first definition
  removed, after confirming against `pg_get_viewdef` that the live view is the
  second one.

---

## 9. Quick reference

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

-- what needs loading, worst first
SELECT source_name, status, days_overdue, url_stability, loader_script
FROM meta.v_data_freshness;
```
