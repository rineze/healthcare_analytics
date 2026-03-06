"""
Medicare Advantage Data Loader
Downloads CMS MA files and loads into PostgreSQL (drinf schema).

Three tables:
- drinf.ma_cpsc_enrollment: Contract/Plan/State/County enrollment (raw, stacked by report_month)
- drinf.ma_plan_directory: Organization name mapping (stacked by report_month)
- drinf.ma_county_penetration: Total Medicare eligible + penetration (stacked by report_month)
"""

import os
import io
import zipfile
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
import requests

# Database configuration (local development)
DB_CONFIG = {
    "host": "127.0.0.1",
    "database": "postgres",
    "user": "postgres",
    "password": "lolsk8s",
}

# CMS download URLs — keyed by (year, month) tuple
CPSC_URLS = {
    (2026, 2): "https://www.cms.gov/files/zip/monthly-enrollment-cpsc-february-2026.zip",
    (2026, 1): "https://www.cms.gov/files/zip/monthly-enrollment-cpsc-january-2026.zip",
}

PENETRATION_URLS = {
    (2026, 2): "https://www.cms.gov/files/zip/ma-state-county-penetration-february-2026.zip",
    (2026, 1): "https://www.cms.gov/files/zip/ma-state-county-penetration-january-2026.zip",
}

# Plan directory is always the latest snapshot
PLAN_DIRECTORY_URL = "https://www.cms.gov/files/zip/ma-plan-directory.zip"

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def download_zip(url, extract_name, force=False):
    """Download a ZIP from CMS and extract. Returns path to extracted folder."""
    os.makedirs(DATA_DIR, exist_ok=True)
    extract_dir = os.path.join(DATA_DIR, extract_name)

    # Skip download if already extracted (unless forced)
    if not force and os.path.exists(extract_dir) and os.listdir(extract_dir):
        print(f"Using cached extraction: {extract_dir}")
        return extract_dir

    print(f"Downloading {url}...")
    resp = requests.get(url, timeout=180)
    resp.raise_for_status()
    print(f"Downloaded {len(resp.content) / 1024 / 1024:.1f} MB")

    os.makedirs(extract_dir, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        zf.extractall(extract_dir)

    return extract_dir


def find_file(directory, keyword=None, largest=False):
    """Find a data file in extracted directory."""
    results = []
    for root, dirs, files in os.walk(directory):
        for f in files:
            if f.lower().endswith((".csv", ".xlsx")) and not f.startswith("~"):
                results.append(os.path.join(root, f))

    if keyword:
        matches = [f for f in results if keyword.lower() in os.path.basename(f).lower()]
        if matches:
            return matches[0]

    if largest and results:
        return max(results, key=os.path.getsize)

    return results[0] if results else None


def read_csv(path):
    """Read CSV with encoding fallback."""
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            return pd.read_csv(path, encoding=enc, low_memory=False)
        except (UnicodeDecodeError, pd.errors.ParserError):
            continue
    return pd.read_csv(path, encoding="latin-1", low_memory=False, on_bad_lines="skip")


def normalize_columns(df):
    """Lowercase, strip, underscorify column names."""
    df.columns = [c.strip().lower().replace(" ", "_").replace("-", "_") for c in df.columns]
    return df


def to_sql_value(val):
    """Convert pandas value to SQL-safe value (None for NaN)."""
    if pd.isna(val):
        return None
    return val


# ---------------------------------------------------------------------------
# Table creation
# ---------------------------------------------------------------------------

def create_tables(conn):
    """Create all MA tables in drinf schema."""
    cursor = conn.cursor()

    cursor.execute("""
        -- CPSC Enrollment: raw contract/plan/state/county enrollment
        CREATE TABLE IF NOT EXISTS drinf.ma_cpsc_enrollment (
            report_month DATE NOT NULL,
            contract_id VARCHAR(10) NOT NULL,
            plan_id VARCHAR(5) NOT NULL,
            ssa_code VARCHAR(10),
            fips VARCHAR(5),
            state VARCHAR(2),
            county VARCHAR(100),
            enrollment INTEGER NOT NULL DEFAULT 0,
            plan_category VARCHAR(20),
            load_date TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (report_month, contract_id, plan_id, state, county)
        );

        CREATE INDEX IF NOT EXISTS idx_ma_cpsc_state
            ON drinf.ma_cpsc_enrollment(state);
        CREATE INDEX IF NOT EXISTS idx_ma_cpsc_contract
            ON drinf.ma_cpsc_enrollment(contract_id);
        CREATE INDEX IF NOT EXISTS idx_ma_cpsc_month
            ON drinf.ma_cpsc_enrollment(report_month);
        CREATE INDEX IF NOT EXISTS idx_ma_cpsc_fips
            ON drinf.ma_cpsc_enrollment(fips);

        COMMENT ON TABLE drinf.ma_cpsc_enrollment IS
            'CMS Monthly Enrollment by Contract/Plan/State/County — raw data stacked by report_month';

        -- Plan Directory: organization name mapping
        CREATE TABLE IF NOT EXISTS drinf.ma_plan_directory (
            report_month DATE NOT NULL,
            contract_id VARCHAR(10) NOT NULL,
            legal_entity_name VARCHAR(200),
            org_marketing_name VARCHAR(200),
            organization_type VARCHAR(50),
            plan_type VARCHAR(50),
            parent_organization VARCHAR(200),
            contract_effective_date VARCHAR(20),
            load_date TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (report_month, contract_id)
        );

        CREATE INDEX IF NOT EXISTS idx_ma_plandir_contract
            ON drinf.ma_plan_directory(contract_id);
        CREATE INDEX IF NOT EXISTS idx_ma_plandir_org
            ON drinf.ma_plan_directory(org_marketing_name);

        COMMENT ON TABLE drinf.ma_plan_directory IS
            'CMS MA Plan Directory — organization name mapping stacked by report_month';

        -- County Penetration: total Medicare eligible population + MA penetration
        CREATE TABLE IF NOT EXISTS drinf.ma_county_penetration (
            report_month DATE NOT NULL,
            state_name VARCHAR(50),
            county_name VARCHAR(100),
            fips_state VARCHAR(2),
            fips_county VARCHAR(3),
            fips VARCHAR(5),
            ssa_state VARCHAR(2),
            ssa_county VARCHAR(3),
            ssa VARCHAR(5),
            eligibles INTEGER,
            enrolled INTEGER,
            penetration NUMERIC(6,2),
            load_date TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (report_month, fips)
        );

        CREATE INDEX IF NOT EXISTS idx_ma_pen_state
            ON drinf.ma_county_penetration(state_name);
        CREATE INDEX IF NOT EXISTS idx_ma_pen_fips
            ON drinf.ma_county_penetration(fips);
        CREATE INDEX IF NOT EXISTS idx_ma_pen_month
            ON drinf.ma_county_penetration(report_month);

        COMMENT ON TABLE drinf.ma_county_penetration IS
            'CMS MA State/County Penetration — total Medicare eligible + MA enrollment stacked by report_month';
    """)

    conn.commit()
    print("Tables created (or already exist) in drinf schema.")


# ---------------------------------------------------------------------------
# CPSC Enrollment loader
# ---------------------------------------------------------------------------

def load_cpsc(conn, year, month):
    """Download and load CPSC enrollment data for a given month."""
    url = CPSC_URLS.get((year, month))
    if not url:
        print(f"No CPSC URL configured for {year}-{month:02d}")
        return

    report_month = f"{year}-{month:02d}-01"
    extract_dir = download_zip(url, f"cpsc_{year}_{month:02d}")
    csv_path = find_file(extract_dir, keyword="enrollment_info", largest=True)

    if not csv_path:
        raise FileNotFoundError(f"No enrollment file found in {extract_dir}")

    print(f"Reading CPSC from: {csv_path}")
    df = read_csv(csv_path)
    df = normalize_columns(df)
    print(f"CPSC columns: {list(df.columns)}")
    print(f"Raw rows: {len(df):,}")

    # Rename to standard
    df = df.rename(columns={
        "contract_number": "contract_id",
        "fips_state_county_code": "fips",
        "ssa_state_county_code": "ssa_code",
    })

    # Clean types
    df["contract_id"] = df["contract_id"].astype(str).str.strip()
    df["plan_id"] = df["plan_id"].astype(str).str.strip()
    df["state"] = df["state"].astype(str).str.strip().str.upper()
    df["county"] = df["county"].astype(str).str.strip()
    df["fips"] = df["fips"].astype(str).str.strip().str.zfill(5)
    df["ssa_code"] = df["ssa_code"].astype(str).str.strip()

    # Exclude S-contracts (PDP/standalone drug plans)
    before = len(df)
    df = df[~df["contract_id"].str.upper().str.startswith("S")].copy()
    print(f"Excluded {before - len(df):,} S-contract rows")

    # Tag group plans (PBP starting with 8)
    df["plan_category"] = df["plan_id"].apply(
        lambda x: "Group" if str(x).startswith("8") else "Individual"
    )

    # Clean enrollment — "*" means 0
    df["enrollment"] = df["enrollment"].astype(str).str.replace(",", "").str.replace("*", "0").str.strip()
    df["enrollment"] = pd.to_numeric(df["enrollment"], errors="coerce").fillna(0).astype(int)

    # Drop rows with no state
    df = df[df["state"].str.len() == 2].copy()
    print(f"Rows after cleaning: {len(df):,}")

    # Aggregate duplicates — sum enrollment for same contract/plan/state/county
    # Keep first ssa_code and fips per group
    agg = df.groupby(["contract_id", "plan_id", "state", "county", "plan_category"], as_index=False).agg(
        enrollment=("enrollment", "sum"),
        ssa_code=("ssa_code", "first"),
        fips=("fips", "first"),
    )
    print(f"Rows after dedup/aggregation: {len(agg):,}")

    # Delete existing data for this month (idempotent reload)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM drinf.ma_cpsc_enrollment WHERE report_month = %s", (report_month,))
    deleted = cursor.rowcount
    if deleted:
        print(f"Deleted {deleted:,} existing rows for {report_month}")

    # Prepare and insert
    columns = ["report_month", "contract_id", "plan_id", "ssa_code", "fips",
               "state", "county", "enrollment", "plan_category"]
    values = []
    for _, row in agg.iterrows():
        fips_val = row["fips"] if isinstance(row["fips"], str) and len(row["fips"]) == 5 else None
        values.append((
            report_month,
            row["contract_id"],
            row["plan_id"],
            to_sql_value(row["ssa_code"]),
            fips_val,
            row["state"],
            row["county"],
            to_sql_value(row["enrollment"]),
            row["plan_category"],
        ))

    insert_sql = f"""
        INSERT INTO drinf.ma_cpsc_enrollment ({", ".join(columns)})
        VALUES %s
        ON CONFLICT (report_month, contract_id, plan_id, state, county)
        DO UPDATE SET
            ssa_code = EXCLUDED.ssa_code,
            fips = EXCLUDED.fips,
            enrollment = EXCLUDED.enrollment,
            plan_category = EXCLUDED.plan_category,
            load_date = NOW()
    """
    execute_values(cursor, insert_sql, values, page_size=1000)
    conn.commit()
    print(f"Loaded {len(values):,} CPSC rows for {report_month}")


# ---------------------------------------------------------------------------
# Plan Directory loader
# ---------------------------------------------------------------------------

def load_plan_directory(conn, report_month_str):
    """Download and load MA Plan Directory."""
    extract_dir = download_zip(PLAN_DIRECTORY_URL, "plan_directory_load")
    csv_path = find_file(extract_dir, keyword="contract_directory", largest=True)

    if not csv_path:
        raise FileNotFoundError(f"No plan directory file found in {extract_dir}")

    print(f"Reading Plan Directory from: {csv_path}")
    df = read_csv(csv_path)
    df = normalize_columns(df)
    print(f"Plan Directory columns: {list(df.columns)}")
    print(f"Raw rows: {len(df):,}")

    # Delete existing data for this month
    cursor = conn.cursor()
    cursor.execute("DELETE FROM drinf.ma_plan_directory WHERE report_month = %s", (report_month_str,))
    deleted = cursor.rowcount
    if deleted:
        print(f"Deleted {deleted:,} existing rows for {report_month_str}")

    # Prepare and insert
    columns = ["report_month", "contract_id", "legal_entity_name", "org_marketing_name",
               "organization_type", "plan_type", "parent_organization", "contract_effective_date"]
    values = []
    for _, row in df.iterrows():
        contract_id = str(row.get("contract_number", "")).strip()
        if not contract_id:
            continue
        values.append((
            report_month_str,
            contract_id,
            to_sql_value(row.get("legal_entity_name")),
            to_sql_value(row.get("organization_marketing_name")),
            to_sql_value(row.get("organization_type")),
            to_sql_value(row.get("plan_type")),
            to_sql_value(row.get("parent_organization")),
            to_sql_value(row.get("contract_effective_date")),
        ))

    insert_sql = f"""
        INSERT INTO drinf.ma_plan_directory ({", ".join(columns)})
        VALUES %s
        ON CONFLICT (report_month, contract_id)
        DO UPDATE SET
            legal_entity_name = EXCLUDED.legal_entity_name,
            org_marketing_name = EXCLUDED.org_marketing_name,
            organization_type = EXCLUDED.organization_type,
            plan_type = EXCLUDED.plan_type,
            parent_organization = EXCLUDED.parent_organization,
            contract_effective_date = EXCLUDED.contract_effective_date,
            load_date = NOW()
    """
    execute_values(cursor, insert_sql, values, page_size=1000)
    conn.commit()
    print(f"Loaded {len(values):,} Plan Directory rows for {report_month_str}")


# ---------------------------------------------------------------------------
# Penetration loader
# ---------------------------------------------------------------------------

def load_penetration(conn, year, month):
    """Download and load MA State/County Penetration data."""
    url = PENETRATION_URLS.get((year, month))
    if not url:
        print(f"No Penetration URL configured for {year}-{month:02d}")
        return

    report_month = f"{year}-{month:02d}-01"
    extract_dir = download_zip(url, f"penetration_{year}_{month:02d}")
    csv_path = find_file(extract_dir, largest=True)

    if not csv_path:
        raise FileNotFoundError(f"No penetration file found in {extract_dir}")

    print(f"Reading Penetration from: {csv_path}")
    df = read_csv(csv_path)
    df = normalize_columns(df)
    print(f"Penetration columns: {list(df.columns)}")
    print(f"Raw rows: {len(df):,}")

    # Standardize column names
    df = df.rename(columns={
        "state_name": "state_name_raw",
        "county_name": "county_name_raw",
        "fipsst": "fips_state",
        "fipscnty": "fips_county",
        "ssast": "ssa_state",
        "ssacnty": "ssa_county",
    })

    # Clean FIPS
    df["fips"] = df["fips"].astype(str).str.strip().str.zfill(5)
    df["fips_state"] = df["fips_state"].astype(str).str.strip().str.zfill(2)
    df["fips_county"] = df["fips_county"].astype(str).str.strip().str.zfill(3)
    df["ssa_state"] = df["ssa_state"].astype(str).str.strip()
    df["ssa_county"] = df["ssa_county"].astype(str).str.strip()
    df["ssa"] = df["ssa"].astype(str).str.strip()

    # Clean numeric
    for col in ["eligibles", "enrolled", "penetration"]:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", "").str.replace("%", "").str.strip(),
                errors="coerce"
            )

    # Drop rows with bad FIPS
    df = df[df["fips"].str.len() == 5].copy()
    print(f"Rows after cleaning: {len(df):,}")

    # Delete existing data for this month
    cursor = conn.cursor()
    cursor.execute("DELETE FROM drinf.ma_county_penetration WHERE report_month = %s", (report_month,))
    deleted = cursor.rowcount
    if deleted:
        print(f"Deleted {deleted:,} existing rows for {report_month}")

    # Prepare and insert
    columns = ["report_month", "state_name", "county_name", "fips_state", "fips_county",
               "fips", "ssa_state", "ssa_county", "ssa", "eligibles", "enrolled", "penetration"]
    values = []
    for _, row in df.iterrows():
        values.append((
            report_month,
            to_sql_value(row.get("state_name_raw")),
            to_sql_value(row.get("county_name_raw")),
            to_sql_value(row.get("fips_state")),
            to_sql_value(row.get("fips_county")),
            row["fips"],
            to_sql_value(row.get("ssa_state")),
            to_sql_value(row.get("ssa_county")),
            to_sql_value(row.get("ssa")),
            to_sql_value(row.get("eligibles")),
            to_sql_value(row.get("enrolled")),
            to_sql_value(row.get("penetration")),
        ))

    insert_sql = f"""
        INSERT INTO drinf.ma_county_penetration ({", ".join(columns)})
        VALUES %s
        ON CONFLICT (report_month, fips)
        DO UPDATE SET
            state_name = EXCLUDED.state_name,
            county_name = EXCLUDED.county_name,
            eligibles = EXCLUDED.eligibles,
            enrolled = EXCLUDED.enrolled,
            penetration = EXCLUDED.penetration,
            load_date = NOW()
    """
    execute_values(cursor, insert_sql, values, page_size=1000)
    conn.commit()
    print(f"Loaded {len(values):,} Penetration rows for {report_month}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Medicare Advantage Data Loader")
    print("=" * 60)

    conn = psycopg2.connect(**DB_CONFIG)

    # 1. Create tables
    create_tables(conn)

    # 2. Load February 2026 data (most recent)
    year, month = 2026, 2
    report_month_str = f"{year}-{month:02d}-01"

    print("\n" + "-" * 60)
    print(f"Loading CPSC Enrollment for {year}-{month:02d}")
    print("-" * 60)
    load_cpsc(conn, year, month)

    print("\n" + "-" * 60)
    print(f"Loading Plan Directory (snapshot as of {report_month_str})")
    print("-" * 60)
    load_plan_directory(conn, report_month_str)

    print("\n" + "-" * 60)
    print(f"Loading County Penetration for {year}-{month:02d}")
    print("-" * 60)
    load_penetration(conn, year, month)

    # 3. Summary
    cursor = conn.cursor()
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)

    cursor.execute("""
        SELECT report_month, COUNT(*) as rows, SUM(COALESCE(enrollment, 0)) as total_enrollment
        FROM drinf.ma_cpsc_enrollment
        GROUP BY report_month ORDER BY report_month
    """)
    for row in cursor.fetchall():
        print(f"  CPSC {row[0]}: {row[1]:,} rows, {row[2]:,} total enrollment")

    cursor.execute("""
        SELECT report_month, COUNT(*) as rows
        FROM drinf.ma_plan_directory
        GROUP BY report_month ORDER BY report_month
    """)
    for row in cursor.fetchall():
        print(f"  Plan Directory {row[0]}: {row[1]:,} contracts")

    cursor.execute("""
        SELECT report_month, COUNT(*) as rows, SUM(COALESCE(eligibles, 0)) as total_eligible
        FROM drinf.ma_county_penetration
        GROUP BY report_month ORDER BY report_month
    """)
    for row in cursor.fetchall():
        print(f"  Penetration {row[0]}: {row[1]:,} counties, {row[2]:,} total eligible")

    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    main()
