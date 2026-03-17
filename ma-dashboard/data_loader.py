"""
data_loader.py — Load MA market share data from PostgreSQL (drinf schema).

Reads from three tables:
- drinf.ma_cpsc_enrollment
- drinf.ma_plan_directory
- drinf.ma_county_penetration
"""

import os
import pandas as pd
import psycopg2
import streamlit as st
from pathlib import Path
from dotenv import load_dotenv

from dotenv import load_dotenv
from pathlib import Path
# Search for .env walking up the directory tree
for _env in [Path(__file__).parent / ".env",
             Path(__file__).parent.parent / ".env",
             Path(__file__).parent.parent.parent / ".env"]:
    if _env.exists():
        load_dotenv(_env)
        break


def get_db_config():
    """Get database config with fallback chain: USE_LOCAL -> Streamlit secrets -> .env -> local."""
    use_local = os.getenv("USE_LOCAL", "false").lower() == "true"
    if use_local and os.getenv("LOCAL_HOST"):
        return {
            "host": os.getenv("LOCAL_HOST", "127.0.0.1"),
            "database": os.getenv("LOCAL_DATABASE", "postgres"),
            "user": os.getenv("LOCAL_USER", "postgres"),
            "password": os.getenv("LOCAL_PASSWORD", "lolsk8s"),
            "port": int(os.getenv("LOCAL_PORT", 5432)),
        }

    try:
        return {
            "host": st.secrets["database"]["host"],
            "database": st.secrets["database"]["database"],
            "user": st.secrets["database"]["user"],
            "password": st.secrets["database"]["password"],
            "port": st.secrets["database"]["port"],
        }
    except Exception:
        pass

    if os.getenv("SUPABASE_HOST"):
        return {
            "host": os.getenv("SUPABASE_HOST"),
            "database": os.getenv("SUPABASE_DATABASE", "postgres"),
            "user": os.getenv("SUPABASE_USER", "postgres"),
            "password": os.getenv("SUPABASE_PASSWORD"),
            "port": int(os.getenv("SUPABASE_PORT", 5432)),
        }

    return {
        "host":     os.getenv("LOCAL_HOST", "127.0.0.1"),
        "database": os.getenv("LOCAL_DATABASE", "postgres"),
        "user":     os.getenv("LOCAL_USER", "postgres"),
        "password": os.getenv("LOCAL_PASSWORD", "lolsk8s"),
        "port":     int(os.getenv("LOCAL_PORT", 5432)),
    }


@st.cache_resource(validate=lambda conn: not conn.closed)
def get_connection():
    """Get database connection (cached, auto-reconnects if closed)."""
    return psycopg2.connect(**get_db_config())


# ---------------------------------------------------------------------------
# State abbreviation mapping (for penetration join — penetration stores full names)
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
# Data queries
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def get_available_months():
    """Get available report months."""
    conn = get_connection()
    df = pd.read_sql("""
        SELECT DISTINCT report_month
        FROM drinf.ma_cpsc_enrollment
        ORDER BY report_month DESC
    """, conn)
    return df["report_month"].tolist()



@st.cache_data(ttl=3600, show_spinner="Loading MA enrollment data...")
def load_all_data(report_month=None):
    """Load aggregated enrollment data from PostgreSQL.

    Returns: (enrollment_by_org, county_penetration)
    """
    conn = get_connection()

    # Default to most recent month
    if report_month is None:
        months = get_available_months()
        if not months:
            raise RuntimeError("No data loaded. Run load_ma_data.py first.")
        report_month = months[0]

    # Main query: CPSC joined to plan directory, aggregated by org x county x plan_category
    enrollment_by_org = pd.read_sql("""
        SELECT
            e.state,
            e.county,
            COALESCE(p.org_marketing_name, e.contract_id) AS org_name,
            e.plan_category,
            SUM(COALESCE(e.enrollment, 0)) AS enrollment,
            MAX(e.fips) AS fips
        FROM drinf.ma_cpsc_enrollment e
        LEFT JOIN drinf.ma_plan_directory p
            ON p.contract_id = e.contract_id
            AND p.report_month = e.report_month
        WHERE e.report_month = %s
        GROUP BY e.state, e.county, COALESCE(p.org_marketing_name, e.contract_id), e.plan_category
    """, conn, params=[report_month])

    # Calculate county totals and market share
    county_totals = enrollment_by_org.groupby(["state", "county"])["enrollment"].sum().reset_index()
    county_totals = county_totals.rename(columns={"enrollment": "county_ma_enrollment"})
    enrollment_by_org = enrollment_by_org.merge(county_totals, on=["state", "county"], how="left")
    enrollment_by_org["market_share_pct"] = (
        enrollment_by_org["enrollment"] / enrollment_by_org["county_ma_enrollment"] * 100
    ).round(2)

    # Load penetration data
    penetration = pd.read_sql("""
        SELECT
            state_name, county_name, fips,
            eligibles AS total_eligible,
            enrolled AS total_ma_enrolled,
            penetration AS penetration_rate
        FROM drinf.ma_county_penetration
        WHERE report_month = %s
    """, conn, params=[report_month])

    # Map state names to abbreviations for joining
    penetration["state_abbrev"] = penetration["state_name"].map(STATE_ABBREV)

    # Join penetration to enrollment
    pen_dedup = penetration.dropna(subset=["state_abbrev"]).drop_duplicates(
        subset=["state_abbrev", "county_name"], keep="first"
    )
    enrollment_by_org = enrollment_by_org.merge(
        pen_dedup[["state_abbrev", "county_name", "total_eligible", "total_ma_enrolled", "penetration_rate"]],
        left_on=["state", "county"],
        right_on=["state_abbrev", "county_name"],
        how="left"
    )
    if "state_abbrev" in enrollment_by_org.columns:
        enrollment_by_org = enrollment_by_org.drop(columns=["state_abbrev", "county_name"])

    # Pct of total eligible
    if "total_eligible" in enrollment_by_org.columns:
        enrollment_by_org["pct_of_total_eligible"] = (
            enrollment_by_org["enrollment"] / enrollment_by_org["total_eligible"] * 100
        ).round(2)

    return enrollment_by_org, penetration


@st.cache_data(ttl=3600)
def get_contract_detail(state, county, report_month=None):
    """Get contract-level detail for a specific county."""
    conn = get_connection()

    if report_month is None:
        months = get_available_months()
        report_month = months[0] if months else None

    return pd.read_sql("""
        SELECT
            COALESCE(p.org_marketing_name, e.contract_id) AS org_name,
            e.contract_id,
            e.plan_id,
            e.plan_category,
            COALESCE(e.enrollment, 0) AS enrollment
        FROM drinf.ma_cpsc_enrollment e
        LEFT JOIN drinf.ma_plan_directory p
            ON p.contract_id = e.contract_id
            AND p.report_month = e.report_month
        WHERE e.report_month = %s
            AND e.state = %s
            AND e.county = %s
        ORDER BY COALESCE(e.enrollment, 0) DESC
    """, conn, params=[report_month, state, county])


@st.cache_data(ttl=3600, show_spinner="Loading county map data...")
def get_county_map_data(report_month=None):
    """Get county-level summary for choropleth map.

    Returns one row per county with FIPS (from penetration table),
    total enrollment, penetration, and top org name/share.

    CPSC enrollment has no FIPS — we get FIPS by joining to
    ma_county_penetration on state abbreviation + county name.
    """
    conn = get_connection()

    if report_month is None:
        months = get_available_months()
        if not months:
            return pd.DataFrame()
        report_month = months[0]

    # Build a VALUES clause for state abbreviation mapping
    state_values = ", ".join(
        "('{}', '{}')".format(full.replace("'", "''"), abbr)
        for full, abbr in STATE_ABBREV.items()
    )

    county_summary = pd.read_sql("""
        WITH state_map(state_full, state_abbr) AS (
            VALUES {state_values}
        ),
        county_totals AS (
            SELECT
                e.state,
                e.county,
                SUM(COALESCE(e.enrollment, 0)) AS enrollment
            FROM drinf.ma_cpsc_enrollment e
            WHERE e.report_month = %(rm)s
            GROUP BY e.state, e.county
        ),
        org_ranked AS (
            SELECT
                e.state,
                e.county,
                COALESCE(p.org_marketing_name, e.contract_id) AS org_name,
                SUM(COALESCE(e.enrollment, 0)) AS org_enrollment,
                ROW_NUMBER() OVER (
                    PARTITION BY e.state, e.county
                    ORDER BY SUM(COALESCE(e.enrollment, 0)) DESC
                ) AS rn
            FROM drinf.ma_cpsc_enrollment e
            LEFT JOIN drinf.ma_plan_directory p
                ON p.contract_id = e.contract_id
                AND p.report_month = e.report_month
            WHERE e.report_month = %(rm)s
            GROUP BY e.state, e.county, COALESCE(p.org_marketing_name, e.contract_id)
        )
        SELECT
            pen.fips,
            ct.state,
            ct.county,
            ct.enrollment,
            pen.eligibles,
            pen.penetration AS penetration_rate,
            o.org_name AS top_org,
            CASE WHEN ct.enrollment > 0
                 THEN ROUND(o.org_enrollment::numeric / ct.enrollment * 100, 1)
                 ELSE 0 END AS top_org_share,
            COALESCE(mkt.market_name, 'Unassigned') AS market_name,
            mkt.market_key
        FROM county_totals ct
        LEFT JOIN org_ranked o
            ON o.state = ct.state AND o.county = ct.county AND o.rn = 1
        LEFT JOIN state_map sm
            ON sm.state_abbr = ct.state
        LEFT JOIN drinf.ma_county_penetration pen
            ON pen.state_name = sm.state_full
            AND UPPER(pen.county_name) = UPPER(ct.county)
            AND pen.report_month = %(rm)s
        LEFT JOIN drinf.county_to_market mkt
            ON mkt.state = ct.state AND UPPER(mkt.county) = UPPER(ct.county)
    """.format(state_values=state_values), conn, params={"rm": report_month})

    return county_summary
