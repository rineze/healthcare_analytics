"""
data_loader.py — Load v_plan_master and manage payor lookups.

Connection priority: USE_LOCAL -> Streamlit secrets -> .env -> local defaults.
"""

import os
import pandas as pd
import psycopg2
import streamlit as st
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

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
            "password": os.getenv("LOCAL_PASSWORD", ""),
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
        "host": os.getenv("LOCAL_HOST", "127.0.0.1"),
        "database": os.getenv("LOCAL_DATABASE", "postgres"),
        "user": os.getenv("LOCAL_USER", "postgres"),
        "password": os.getenv("LOCAL_PASSWORD", ""),
        "port": int(os.getenv("LOCAL_PORT", 5432)),
    }


@st.cache_resource(validate=lambda conn: not conn.closed)
def _get_conn():
    return psycopg2.connect(**get_db_config())


@st.cache_data(ttl=3600)
def load_plan_master():
    """Load the full v_plan_master view."""
    conn = _get_conn()
    df = pd.read_sql("""
        SELECT v.lob, v.plan_id, v.carrier_id, v.plan_year,
               v.carrier_name, v.plan_name, v.plan_type, v.plan_sub_type,
               v.metal_level, v.benefit_category, v.state,
               CASE
                   WHEN v.lob = 'MA' THEN d.parent_organization
                   WHEN v.lob = 'Medicaid' THEN v.carrier_name
                   ELSE NULL
               END AS parent_organization,
               CASE
                   WHEN v.lob = 'MA' THEN ma_enroll.enrollment
                   WHEN v.lob = 'Medicaid' THEN med.total_enrollment
                   ELSE NULL
               END AS membership
        FROM drinf.v_plan_master v
        LEFT JOIN drinf.ma_plan_directory d
            ON v.lob = 'MA' AND v.carrier_id = d.contract_id
        LEFT JOIN (
            SELECT contract_id, LPAD(plan_id, 3, '0') AS pbp_id, SUM(enrollment) AS enrollment
            FROM drinf.ma_cpsc_enrollment
            WHERE report_month = (SELECT MAX(report_month) FROM drinf.ma_cpsc_enrollment)
            GROUP BY contract_id, LPAD(plan_id, 3, '0')
        ) ma_enroll
            ON v.lob = 'MA'
            AND v.carrier_id = ma_enroll.contract_id
            AND LPAD(SPLIT_PART(v.plan_id, '-', 2), 3, '0') = ma_enroll.pbp_id
        LEFT JOIN drinf.ref_medicaid_landscape med
            ON v.lob = 'Medicaid'
            AND v.state = med.state
            AND v.plan_name = med.plan_name
            AND v.plan_year = med.plan_year
        ORDER BY v.lob, v.state, v.plan_name
    """, conn)
    df["parent_organization"] = df["parent_organization"].fillna("")
    df["membership"] = pd.to_numeric(df["membership"], errors="coerce")
    return df


# ---------------------------------------------------------------------------
# Payor lookups — confirmed mappings
# ---------------------------------------------------------------------------

def find_saved_lookup(query: str) -> Optional[pd.Series]:
    """Check if this exact query (case-insensitive) has a saved match."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM drinf.payor_lookups WHERE lookup_lower = %s",
        (query.lower().strip(),)
    )
    row = cur.fetchone()
    if row is None:
        return None
    cols = [desc[0] for desc in cur.description]
    return pd.Series(dict(zip(cols, row)))


def load_saved_lookups() -> pd.DataFrame:
    """Load all confirmed payor lookups."""
    conn = _get_conn()
    return pd.read_sql(
        "SELECT * FROM drinf.payor_lookups ORDER BY created_at DESC", conn
    )


def save_lookup(lookup_value: str, plan_row: pd.Series, match_score: float, notes: str = ""):
    """Insert or update a confirmed lookup mapping."""
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO drinf.payor_lookups
                (lookup_value, lookup_lower, plan_id, plan_name, carrier_name,
                 lob, state, plan_type, plan_sub_type, metal_level,
                 benefit_category, plan_year, match_score, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (lookup_lower) DO UPDATE SET
                lookup_value = EXCLUDED.lookup_value,
                plan_id = EXCLUDED.plan_id,
                plan_name = EXCLUDED.plan_name,
                carrier_name = EXCLUDED.carrier_name,
                lob = EXCLUDED.lob,
                state = EXCLUDED.state,
                plan_type = EXCLUDED.plan_type,
                plan_sub_type = EXCLUDED.plan_sub_type,
                metal_level = EXCLUDED.metal_level,
                benefit_category = EXCLUDED.benefit_category,
                plan_year = EXCLUDED.plan_year,
                match_score = EXCLUDED.match_score,
                notes = EXCLUDED.notes,
                created_at = NOW()
        """, (
            lookup_value, lookup_value.lower().strip(),
            plan_row.get("plan_id", ""), plan_row.get("plan_name", ""),
            plan_row.get("carrier_name", ""), plan_row.get("lob", ""),
            plan_row.get("state", ""), plan_row.get("plan_type", ""),
            plan_row.get("plan_sub_type", ""), plan_row.get("metal_level", ""),
            plan_row.get("benefit_category", ""), plan_row.get("plan_year"),
            match_score, notes
        ))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def delete_lookup(lookup_id: int):
    """Delete a saved lookup by ID."""
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM drinf.payor_lookups WHERE id = %s", (lookup_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
