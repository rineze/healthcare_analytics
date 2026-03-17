"""
data_loader.py — Load v_plan_master from PostgreSQL (drinf schema).

Connection priority: Streamlit secrets -> .env -> local defaults.
"""

import os
import pandas as pd
import psycopg2
import streamlit as st
from pathlib import Path
from dotenv import load_dotenv

# Search for .env walking up the directory tree
for _env in [Path(__file__).parent / ".env",
             Path(__file__).parent.parent / ".env",
             Path(__file__).parent.parent.parent / ".env"]:
    if _env.exists():
        load_dotenv(_env)
        break


def get_db_config():
    """Get database config with fallback chain: Streamlit secrets -> .env -> local."""
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

    # Local dev fallback — v_plan_master only exists locally for now
    return {
        "host": os.getenv("LOCAL_HOST", "127.0.0.1"),
        "database": os.getenv("LOCAL_DATABASE", "postgres"),
        "user": os.getenv("LOCAL_USER", "postgres"),
        "password": os.getenv("LOCAL_PASSWORD", "lolsk8s"),
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
        SELECT lob, plan_id, carrier_id, plan_year,
               carrier_name, plan_name, plan_type, plan_sub_type,
               metal_level, benefit_category, state
        FROM drinf.v_plan_master
        ORDER BY lob, state, plan_name
    """, conn)
    return df
