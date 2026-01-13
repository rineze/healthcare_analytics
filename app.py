"""
Radiology wRVU Analysis Dashboard
Analyzes Medicare Physician Fee Schedule changes for radiology codes (70000-79999)
"""
import streamlit as st

st.set_page_config(
    page_title="Radiology wRVU Analysis",
    page_icon=":chart_with_upwards_trend:",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Shared color palette (Stephen Few - muted, semantic)
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

# Main page content
st.title("Radiology wRVU Analysis")
st.caption("Medicare Physician Fee Schedule | CPT 70000-79999 | 2018-2026")

st.markdown("""
This dashboard analyzes Work RVU trends for radiology services across the Medicare Physician Fee Schedule.

**Navigate using the sidebar:**
- **YoY Changes**: Compare any two years, identify biggest movers
- **Code Deep Dive**: Full historical analysis of specific CPT codes
- **Category Overview**: Portfolio view of all radiology segments

---

**Data Source**: CMS Physician Fee Schedule Relative Value Files
**Scope**: 9 years (2018-2026) | ~160,000 records | CPT 70000-79999
""")

# Quick stats
import psycopg2
import pandas as pd

@st.cache_resource
def get_connection():
    return psycopg2.connect(
        host="127.0.0.1",
        database="postgres",
        user="postgres",
        password="lolsk8s"
    )

@st.cache_data
def get_summary_stats():
    conn = get_connection()
    query = """
        SELECT
            COUNT(*) as total_records,
            COUNT(DISTINCT hcpcs) as unique_codes,
            COUNT(DISTINCT mpfs_year) as years,
            MIN(mpfs_year) as min_year,
            MAX(mpfs_year) as max_year
        FROM drinf.mpfs_rvu
        WHERE hcpcs ~ '^7[0-9]'
    """
    return pd.read_sql(query, conn).iloc[0]

try:
    stats = get_summary_stats()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Records", f"{stats['total_records']:,}")
    with col2:
        st.metric("Unique CPT Codes", f"{stats['unique_codes']:,}")
    with col3:
        st.metric("Years Covered", f"{stats['min_year']}-{stats['max_year']}")
    with col4:
        st.metric("Data Refresh", "Jan 2026")
except Exception as e:
    st.error(f"Database connection error: {e}")
    st.info("Ensure PostgreSQL is running and the drinf.mpfs_rvu table exists.")
