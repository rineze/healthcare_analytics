import os
"""
load_cbsa_markets.py

Loads county-to-market mappings for ALL US states (except TN, which uses
hand-curated markets in load_market_definitions.py) using the Census Bureau
CBSA county delineation file as the source of truth.

Methodology:
  - MSA/Micropolitan counties  → CBSA market (e.g., "Nashville TN")
  - Non-CBSA rural counties    → "Rural {State}" catch-all per state
  - TN                         → SKIPPED (custom markets already loaded)
  - Multi-state CBSAs          → kept within state lines; each state's
                                  counties get their own market_key/name
                                  e.g., Memphis CBSA → "Memphis TN" (TN) and
                                        "Memphis MS" (MS), "Memphis AR" (AR)

Source file: cbsa_delineation_2023.xlsx (Census Bureau, July 2023)

Usage:
    python load_cbsa_markets.py

To refresh: re-download the xlsx and re-run. All non-TN rows are upserted.
"""

import re
import openpyxl
import psycopg2
from psycopg2.extras import execute_values

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CBSA_FILE = r"C:\Users\danie\OneDrive\Desktop\Informatics Tools & Files\pfs_analysis\cbsa_delineation_2023.xlsx"

DB_LOCAL = {
    "host": "127.0.0.1", "port": 5432,
    "dbname": "postgres", "user": "postgres", "password": "lolsk8s",
}

DB_SUPABASE = {
    "host": "aws-1-us-east-1.pooler.supabase.com", "port": 5432,
    "dbname": "postgres",
    "user": "postgres.numdlqsfydtypeurijae",
    "password": os.getenv("SUPABASE_PASSWORD", ""),
}

# States with hand-curated markets — skip in this loader
SKIP_STATES = {"TN"}

# ---------------------------------------------------------------------------
# State-specific county overrides
# Connecticut: Census 2023 CBSA file uses planning regions (new county-equivalents
# adopted 2022), but CMS MA enrollment still uses the old 8 county names.
# Map old CT county names -> (market_name, market_key, cbsa_code, source)
# ---------------------------------------------------------------------------

CT_COUNTY_MAP = {
    "Fairfield":  ("Bridgeport CT", "CT-BRIDGEPORT", "14860", "cbsa-msa"),
    "Hartford":   ("Hartford CT",   "CT-HARTFORD",   "25540", "cbsa-msa"),
    "Litchfield": ("New Haven CT",  "CT-NEW-HAVEN",  "35300", "cbsa-msa"),
    "Middlesex":  ("Hartford CT",   "CT-HARTFORD",   "25540", "cbsa-msa"),
    "New Haven":  ("New Haven CT",  "CT-NEW-HAVEN",  "35300", "cbsa-msa"),
    "New London": ("New London CT", "CT-NEW-LONDON", "35980", "cbsa-msa"),
    "Tolland":    ("Hartford CT",   "CT-HARTFORD",   "25540", "cbsa-msa"),
    "Windham":    ("New London CT", "CT-NEW-LONDON", "35980", "cbsa-msa"),
}

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

STATE_ABBREV = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
    "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME",
    "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE",
    "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH",
    "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
    "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
    "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "Puerto Rico": "PR", "Guam": "GU", "Virgin Islands": "VI",
    "American Samoa": "AS", "Northern Mariana Islands": "MP",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slugify(text):
    return re.sub(r"[^A-Z0-9]+", "-", text.upper()).strip("-")


def parse_cbsa_title(title):
    """Extract (primary_city, primary_state_abbr) from CBSA title string.

    Examples:
      "Nashville-Davidson--Murfreesboro--Franklin, TN"  → ("Nashville", "TN")
      "Memphis, TN-MS-AR"                               → ("Memphis", "TN")
      "New York-Newark-Jersey City, NY-NJ-PA"           → ("New York", "NY")
      "Aberdeen, SD"                                    → ("Aberdeen", "SD")
    """
    if "," in title:
        city_part, state_part = title.rsplit(",", 1)
        primary_state = state_part.strip().split("-")[0].strip()
    else:
        city_part = title
        primary_state = ""

    # Normalize double dashes before splitting on single dash
    city_part = city_part.replace("--", "\x00")
    first_city = city_part.split("-")[0].replace("\x00", "-").strip()
    return first_city, primary_state


def clean_county_name(raw):
    """Strip 'County', 'Parish', 'Borough', etc. from county name."""
    for suffix in [
        " County", " Parish", " Borough", " Census Area",
        " Municipality", " City and Borough", " and Borough",
        " city",
    ]:
        if raw.endswith(suffix):
            return raw[: -len(suffix)].strip()
    return raw.strip()


# ---------------------------------------------------------------------------
# Step 1: Parse CBSA delineation file
# ---------------------------------------------------------------------------

def load_cbsa_file(path):
    """Returns dict: (state_abbr, fips_5) -> (market_name, market_key, cbsa_code, source)
    and dict:        (state_abbr, county_upper) -> same, as fallback.
    """
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    by_fips = {}
    by_name = {}

    for row in rows[3:]:  # first 3 rows are header/metadata
        (cbsa_code, _, _, cbsa_title, metro_micro,
         _, _, county_raw, state_name, fips_st, fips_co, _) = row

        if not cbsa_code or not state_name or not county_raw:
            continue

        state_abbr = STATE_ABBREV.get(str(state_name).strip(), "")
        if not state_abbr or state_abbr in SKIP_STATES:
            continue

        fips_5 = None
        if fips_st and fips_co:
            fips_5 = str(int(fips_st)).zfill(2) + str(int(fips_co)).zfill(3)

        county_clean = clean_county_name(str(county_raw).strip())
        first_city, _ = parse_cbsa_title(str(cbsa_title).strip())

        # Market name = primary city + this county's state (within-state-lines)
        market_name = f"{first_city} {state_abbr}"
        market_key = f"{state_abbr}-{slugify(first_city)}"
        source = "cbsa-msa" if "Metropolitan Statistical Area" in str(metro_micro) else "cbsa-micro"

        info = (market_name, market_key, str(int(cbsa_code)), source)

        if fips_5:
            by_fips[(state_abbr, fips_5)] = info
        by_name[(state_abbr, county_clean.upper())] = info

    return by_fips, by_name


# ---------------------------------------------------------------------------
# Step 2: Get all non-TN counties from MA enrollment table
# ---------------------------------------------------------------------------

def get_enrollment_counties(conn):
    """Returns list of (state, county, fips) from ma_cpsc_enrollment."""
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT state, county, fips
        FROM drinf.ma_cpsc_enrollment
        WHERE state NOT IN %s
        ORDER BY state, county
    """, (tuple(SKIP_STATES),))
    rows = cur.fetchall()
    cur.close()
    return rows


# ---------------------------------------------------------------------------
# Step 3: Build market rows
# ---------------------------------------------------------------------------

def build_market_rows(enrollment_counties, by_fips, by_name):
    rows = []
    unmatched = []

    for state, county, fips in enrollment_counties:
        info = None

        # State-specific overrides (e.g. CT old county names → planning region CBSAs)
        if state == "CT":
            info = CT_COUNTY_MAP.get(county)

        # Try FIPS match first
        if not info and fips and len(str(fips)) == 5:
            info = by_fips.get((state, str(fips)))

        # Fall back to name match
        if not info:
            info = by_name.get((state, county.upper()))

        if info:
            market_name, market_key, cbsa_code, source = info
        else:
            # Rural catch-all
            market_name = f"Rural {state}"
            market_key = f"{state}-RURAL"
            cbsa_code = None
            source = "rural"
            unmatched.append((state, county, fips))

        rows.append((
            state,
            county,
            fips,
            market_name,
            market_key,
            state,          # market_state = county's state (within-state-lines)
            f"{source}|cbsa={cbsa_code}" if cbsa_code else source,
        ))

    return rows, unmatched


# ---------------------------------------------------------------------------
# Step 4: Upsert to database
# ---------------------------------------------------------------------------

UPSERT_SQL = """
INSERT INTO drinf.county_to_market
    (state, county, fips, market_name, market_key, market_state, notes)
VALUES %s
ON CONFLICT (state, county)
DO UPDATE SET
    fips         = EXCLUDED.fips,
    market_name  = EXCLUDED.market_name,
    market_key   = EXCLUDED.market_key,
    market_state = EXCLUDED.market_state,
    notes        = EXCLUDED.notes
"""


def upsert(conn, rows, label):
    execute_values(conn.cursor(), UPSERT_SQL, rows, page_size=500)
    conn.commit()
    print(f"  [{label}] Upserted {len(rows)} rows")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Parsing CBSA delineation file...")
    by_fips, by_name = load_cbsa_file(CBSA_FILE)
    print(f"  CBSA FIPS entries: {len(by_fips):,}")
    print(f"  CBSA name entries: {len(by_name):,}")

    print("\nFetching MA enrollment counties from local DB...")
    local = psycopg2.connect(**DB_LOCAL)
    enrollment_counties = get_enrollment_counties(local)
    print(f"  Counties to process: {len(enrollment_counties):,} (excl. {', '.join(SKIP_STATES)})")

    print("\nBuilding market assignments...")
    rows, unmatched = build_market_rows(enrollment_counties, by_fips, by_name)

    cbsa_msa   = sum(1 for r in rows if "cbsa-msa"   in (r[6] or ""))
    cbsa_micro = sum(1 for r in rows if "cbsa-micro" in (r[6] or ""))
    rural      = sum(1 for r in rows if r[6] == "rural")
    print(f"  MSA markets:        {cbsa_msa:,} counties")
    print(f"  Micro markets:      {cbsa_micro:,} counties")
    print(f"  Rural (unmatched):  {rural:,} counties")

    if unmatched:
        print(f"\n  Unmatched counties assigned to Rural:")
        for s, c, f in unmatched[:20]:
            print(f"    {s} — {c} (fips={f})")
        if len(unmatched) > 20:
            print(f"    ... and {len(unmatched) - 20} more")

    print("\nUpserting to local DB...")
    upsert(local, rows, "local")
    local.close()

    print("Upserting to Supabase...")
    supa = psycopg2.connect(**DB_SUPABASE)
    upsert(supa, rows, "supabase")

    # Summary by state
    cur = supa.cursor()
    cur.execute("""
        SELECT market_state,
               COUNT(DISTINCT market_key) as markets,
               COUNT(*) as counties
        FROM drinf.county_to_market
        WHERE market_state NOT IN %s
        GROUP BY market_state
        ORDER BY market_state
    """, (tuple(SKIP_STATES),))
    print(f"\n{'State':<8} {'Markets':>8} {'Counties':>10}")
    print("-" * 28)
    total_m, total_c = 0, 0
    for st, m, c in cur.fetchall():
        print(f"{st:<8} {m:>8} {c:>10}")
        total_m += m
        total_c += c
    print("-" * 28)
    print(f"{'TOTAL':<8} {total_m:>8} {total_c:>10}")

    supa.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
