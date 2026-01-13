"""
Shared utilities for Radiology wRVU Dashboard
"""
import streamlit as st
import pandas as pd
import psycopg2

# Database configuration
DB_CONFIG = {
    "host": "127.0.0.1",
    "database": "postgres",
    "user": "postgres",
    "password": "lolsk8s"
}

# Color palette (Stephen Few - muted, semantic consistency)
COLORS = {
    "positive": "#2e7d32",      # Muted green - increase/good
    "negative": "#c62828",      # Muted red - decrease/bad
    "neutral": "#616161",       # Gray - neutral/context
    "neutral_light": "#9e9e9e", # Light gray
    "background": "#fafafa",    # Off-white
    "accent": "#1565c0",        # Muted blue - highlight/selection
}

# Radiology category definitions (CPT 70000-79999)
RADIOLOGY_CATEGORIES = [
    {"category": "Head & Neck Imaging", "code_start": 70010, "code_end": 70559},
    {"category": "Chest Imaging", "code_start": 71010, "code_end": 71555},
    {"category": "Spine Imaging", "code_start": 72010, "code_end": 72295},
    {"category": "Upper Extremity Imaging", "code_start": 73000, "code_end": 73225},
    {"category": "Lower Extremity Imaging", "code_start": 73500, "code_end": 73725},
    {"category": "Abdomen & Pelvis Imaging", "code_start": 74000, "code_end": 74485},
    {"category": "GI/GU Studies", "code_start": 74710, "code_end": 74775},
    {"category": "Vascular Imaging", "code_start": 75600, "code_end": 75989},
    {"category": "Diagnostic Ultrasound", "code_start": 76000, "code_end": 76999},
    {"category": "Radiologic Guidance", "code_start": 77001, "code_end": 77022},
    {"category": "Mammography", "code_start": 77046, "code_end": 77067},
    {"category": "Bone Density/DXA", "code_start": 77071, "code_end": 77092},
    {"category": "Radiation Oncology", "code_start": 77261, "code_end": 77799},
    {"category": "Nuclear Medicine", "code_start": 78000, "code_end": 79999},
]


@st.cache_resource
def get_connection():
    """Get database connection (cached)."""
    return psycopg2.connect(**DB_CONFIG)


def assign_category(code_num):
    """Assign radiology category based on CPT code number."""
    for cat in RADIOLOGY_CATEGORIES:
        if cat["code_start"] <= code_num <= cat["code_end"]:
            return cat["category"]
    return "Other Radiology"


@st.cache_data(ttl=3600)
def load_radiology_data(exclude_rad_onc=True):
    """Load all radiology data with category assignments.

    Args:
        exclude_rad_onc: If True, excludes Radiation Oncology codes (77261-77799)
    """
    conn = get_connection()

    query = """
        SELECT
            mpfs_year,
            hcpcs,
            modifier,
            description,
            status_code,
            work_rvu,
            non_fac_pe_rvu,
            facility_pe_rvu,
            mp_rvu,
            non_facility_total,
            facility_total,
            conversion_factor
        FROM drinf.mpfs_rvu
        WHERE hcpcs ~ '^7[0-9]'
        ORDER BY mpfs_year, hcpcs
    """
    df = pd.read_sql(query, conn)

    # Convert HCPCS to numeric for category assignment
    df["code_num"] = pd.to_numeric(df["hcpcs"], errors="coerce")

    # Assign categories
    df["category"] = df["code_num"].apply(assign_category)

    # Optionally exclude Radiation Oncology
    if exclude_rad_onc:
        df = df[df["category"] != "Radiation Oncology"]

    # Calculate payment amounts
    df["work_payment"] = df["work_rvu"] * df["conversion_factor"]
    df["total_payment_nonfac"] = df["non_facility_total"] * df["conversion_factor"]
    df["total_payment_fac"] = df["facility_total"] * df["conversion_factor"]

    return df


@st.cache_data(ttl=3600)
def get_available_years():
    """Get list of available years in the data."""
    conn = get_connection()
    query = "SELECT DISTINCT mpfs_year FROM drinf.mpfs_rvu ORDER BY mpfs_year"
    df = pd.read_sql(query, conn)
    return df["mpfs_year"].tolist()


def calculate_yoy_changes(df, year_from, year_to, metric="work_rvu"):
    """Calculate year-over-year changes for a given metric.

    Returns DataFrame with columns: hcpcs, description, category,
    value_from, value_to, change, pct_change
    """
    # Get data for both years
    df_from = df[df["mpfs_year"] == year_from][["hcpcs", "description", "category", metric]].copy()
    df_from = df_from.rename(columns={metric: "value_from"})

    df_to = df[df["mpfs_year"] == year_to][["hcpcs", "description", "category", metric]].copy()
    df_to = df_to.rename(columns={metric: "value_to"})

    # Merge
    merged = df_from.merge(df_to[["hcpcs", "value_to"]], on="hcpcs", how="inner")

    # Calculate changes
    merged["change"] = merged["value_to"] - merged["value_from"]
    merged["pct_change"] = (merged["change"] / merged["value_from"].replace(0, pd.NA)) * 100

    # Drop rows where we can't calculate change
    merged = merged.dropna(subset=["change"])

    return merged


def format_change(value, is_percent=False):
    """Format a change value with color indication."""
    if pd.isna(value):
        return "-"
    if is_percent:
        return f"{value:+.1f}%"
    return f"{value:+.2f}"


def get_sparkline_data(df, hcpcs_code, metric="work_rvu"):
    """Get time series data for sparkline visualization."""
    code_data = df[df["hcpcs"] == hcpcs_code].sort_values("mpfs_year")
    return code_data[["mpfs_year", metric]].values.tolist()


def create_category_summary(df, year_from, year_to, metric="work_rvu"):
    """Create summary statistics by category for YoY comparison."""
    # Calculate averages by category and year
    cat_from = df[df["mpfs_year"] == year_from].groupby("category")[metric].mean().reset_index()
    cat_from = cat_from.rename(columns={metric: "avg_from"})

    cat_to = df[df["mpfs_year"] == year_to].groupby("category")[metric].mean().reset_index()
    cat_to = cat_to.rename(columns={metric: "avg_to"})

    # Merge and calculate changes
    cat_summary = cat_from.merge(cat_to, on="category", how="outer")
    cat_summary["change"] = cat_summary["avg_to"] - cat_summary["avg_from"]
    cat_summary["pct_change"] = (cat_summary["change"] / cat_summary["avg_from"].replace(0, pd.NA)) * 100

    # Add code counts
    code_counts = df[df["mpfs_year"] == year_to].groupby("category")["hcpcs"].nunique().reset_index()
    code_counts = code_counts.rename(columns={"hcpcs": "code_count"})
    cat_summary = cat_summary.merge(code_counts, on="category", how="left")

    return cat_summary.sort_values("change", ascending=False)
