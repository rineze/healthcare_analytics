"""
Shared utilities for MPFS Analytics Dashboard
"""
import streamlit as st
import pandas as pd
import psycopg2
import os
from pathlib import Path
from dotenv import load_dotenv

# Load shared .env from parent directory (Informatics Tools & Files)
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)


def get_db_config():
    """Get database config with fallback chain: Streamlit secrets -> .env -> local."""
    # 1. Try Streamlit secrets (for Streamlit Cloud deployment)
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

    # 2. Check USE_LOCAL toggle in .env
    use_local = os.getenv("USE_LOCAL", "false").lower() == "true"

    if use_local and os.getenv("LOCAL_HOST"):
        # Use local database for development
        return {
            "host": os.getenv("LOCAL_HOST", "127.0.0.1"),
            "database": os.getenv("LOCAL_DATABASE", "postgres"),
            "user": os.getenv("LOCAL_USER", "postgres"),
            "password": os.getenv("LOCAL_PASSWORD", "lolsk8s"),
            "port": int(os.getenv("LOCAL_PORT", 5432)),
        }

    # 3. Try Supabase environment variables
    if os.getenv("SUPABASE_HOST"):
        return {
            "host": os.getenv("SUPABASE_HOST"),
            "database": os.getenv("SUPABASE_DATABASE", "postgres"),
            "user": os.getenv("SUPABASE_USER", "postgres"),
            "password": os.getenv("SUPABASE_PASSWORD"),
            "port": int(os.getenv("SUPABASE_PORT", 5432)),
        }

    # 4. Fallback to local development defaults
    return {
        "host":     os.getenv("LOCAL_HOST", "127.0.0.1"),
        "database": os.getenv("LOCAL_DATABASE", "postgres"),
        "user":     os.getenv("LOCAL_USER", "postgres"),
        "password": os.getenv("LOCAL_PASSWORD", "lolsk8s"),
        "port":     int(os.getenv("LOCAL_PORT", 5432)),
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

# Status codes that indicate non-payable codes
NON_PAYABLE_STATUS = ['B', 'I', 'N', 'X', 'E', 'P']

# SQL clause for filtering non-payable codes (safe - no user input)
STATUS_FILTER_CLAUSE = f"status_code NOT IN ({','.join(repr(s) for s in NON_PAYABLE_STATUS)})"

# Predefined Code Groups (Radiology Focus + Common E&M)
CODE_GROUPS = {
    "MRI Brain": ["70551", "70552", "70553"],
    "MRI Spine": ["72141", "72142", "72146", "72147", "72148", "72149", "72156", "72157", "72158"],
    "CT Head": ["70450", "70460", "70470"],
    "CT Chest": ["71250", "71260", "71270"],
    "CT Abdomen/Pelvis": ["74150", "74160", "74170", "74176", "74177", "74178"],
    "Mammography": ["77065", "77066", "77067"],
    "X-Ray Chest": ["71045", "71046", "71047", "71048"],
    "Ultrasound Abdomen": ["76700", "76705", "76770", "76775"],
    "PET Scan": ["78811", "78812", "78813", "78814", "78815", "78816"],
    "Nuclear Cardiology": ["78451", "78452", "78453", "78454"],
    "Colonoscopy": ["45378", "45380", "45381", "45382", "45384", "45385"],
    "Office Visits (Est)": ["99211", "99212", "99213", "99214", "99215"],
    "Office Visits (New)": ["99202", "99203", "99204", "99205"],
}


# ============================================================================
# CPT Category Definitions (shared across pages)
# ============================================================================

CPT_CATEGORIES = {
    'E/M': {'range': (99201, 99499), 'description': 'Evaluation & Management'},
    'Anesthesia': {'range': (100, 1999), 'description': 'Anesthesia Services'},
    'Surgery - Integumentary': {'range': (10000, 19999), 'description': 'Skin, Breast'},
    'Surgery - Musculoskeletal': {'range': (20000, 29999), 'description': 'Bones, Joints, Muscles'},
    'Surgery - Respiratory/Cardio': {'range': (30000, 39999), 'description': 'Respiratory & Cardiovascular'},
    'Surgery - Digestive': {'range': (40000, 49999), 'description': 'Digestive System'},
    'Surgery - Urinary/Reproductive': {'range': (50000, 59999), 'description': 'Urinary & Reproductive'},
    'Surgery - Nervous System': {'range': (60000, 69999), 'description': 'Nervous System, Eye, Ear'},
    'Radiology': {'range': (70000, 79999), 'description': 'Imaging & Radiation'},
    'Pathology/Lab': {'range': (80000, 89999), 'description': 'Laboratory & Pathology'},
    'Medicine': {'range': (90000, 99199), 'description': 'Medicine (non-E/M)'},
}

# Simple range-based category lookup for dropdowns
CPT_CATEGORY_RANGES = {
    "All Codes": None,
    "E/M (99201-99499)": ("99201", "99499"),
    "Anesthesia (00100-01999)": ("00100", "01999"),
    "Surgery - Integumentary (10000-19999)": ("10000", "19999"),
    "Surgery - Musculoskeletal (20000-29999)": ("20000", "29999"),
    "Surgery - Respiratory/Cardio (30000-39999)": ("30000", "39999"),
    "Surgery - Digestive (40000-49999)": ("40000", "49999"),
    "Surgery - Urinary/Reproductive (50000-59999)": ("50000", "59999"),
    "Surgery - Nervous System (60000-69999)": ("60000", "69999"),
    "Radiology (70000-79999)": ("70000", "79999"),
    "Pathology/Lab (80000-89999)": ("80000", "89999"),
    "Medicine (90000-99199)": ("90000", "99199"),
}


def classify_cpt(cpt_code):
    """Classify a CPT code into a category.

    Args:
        cpt_code: CPT code as string or int

    Returns:
        Category name string (e.g., 'E/M', 'Radiology', 'Other')
    """
    try:
        code_num = int(cpt_code)

        # E/M codes (99201-99499) - check first as they overlap with Medicine range
        if 99201 <= code_num <= 99499:
            return 'E/M'
        # Anesthesia
        elif 100 <= code_num <= 1999:
            return 'Anesthesia'
        # Surgery categories
        elif 10000 <= code_num <= 19999:
            return 'Surgery - Integumentary'
        elif 20000 <= code_num <= 29999:
            return 'Surgery - Musculoskeletal'
        elif 30000 <= code_num <= 39999:
            return 'Surgery - Respiratory/Cardio'
        elif 40000 <= code_num <= 49999:
            return 'Surgery - Digestive'
        elif 50000 <= code_num <= 59999:
            return 'Surgery - Urinary/Reproductive'
        elif 60000 <= code_num <= 69999:
            return 'Surgery - Nervous System'
        # Radiology
        elif 70000 <= code_num <= 79999:
            return 'Radiology'
        # Pathology/Lab
        elif 80000 <= code_num <= 89999:
            return 'Pathology/Lab'
        # Medicine (non E/M)
        elif 90000 <= code_num <= 99199:
            return 'Medicine'
        else:
            return 'Other'
    except (ValueError, TypeError):
        return 'Other'


def get_cpt_category_list():
    """Get list of CPT category names for UI dropdowns."""
    return list(CPT_CATEGORIES.keys()) + ['Other']


@st.cache_resource
def get_connection():
    """Get database connection (cached)."""
    return psycopg2.connect(**get_db_config())


# ============================================================================
# Core Data Functions (using analytics views)
# ============================================================================

@st.cache_data(ttl=3600)
def get_available_years():
    """Get list of available years in the data."""
    conn = get_connection()
    query = "SELECT DISTINCT year FROM drinf.v_cf_clean ORDER BY year"
    df = pd.read_sql(query, conn)
    return df["year"].tolist()


@st.cache_data(ttl=3600)
def get_conversion_factors():
    """Get conversion factors by year."""
    conn = get_connection()
    query = """
        SELECT year, conversion_factor
        FROM drinf.v_cf_clean
        ORDER BY year
    """
    return pd.read_sql(query, conn)


@st.cache_data(ttl=3600)
def get_localities():
    """Get list of all localities with their names."""
    conn = get_connection()
    query = """
        SELECT DISTINCT locality_id, locality_name, state
        FROM drinf.v_gpci_clean
        WHERE year = (SELECT MAX(year) FROM drinf.v_gpci_clean)
        ORDER BY state, locality_name
    """
    return pd.read_sql(query, conn)


@st.cache_data(ttl=3600)
def get_code_list(year=None, payable_only=True):
    """Get list of codes with descriptions for dropdown."""
    conn = get_connection()
    params = []
    conditions = []

    if year is not None:
        conditions.append("year = %s")
        params.append(year)
    if payable_only:
        conditions.append(STATUS_FILTER_CLAUSE)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    query = f"""
        SELECT DISTINCT hcpcs_mod, hcpcs, modifier, description
        FROM drinf.v_rvu_clean
        {where_clause}
        ORDER BY hcpcs_mod
    """
    return pd.read_sql(query, conn, params=params if params else None)


# ============================================================================
# Page 1: Baseline Monitor
# ============================================================================

@st.cache_data(ttl=3600)
def get_summary_stats(year, payable_only=True):
    """Get summary statistics for a given year."""
    conn = get_connection()

    status_filter = f"AND {STATUS_FILTER_CLAUSE}" if payable_only else ""

    query = f"""
        SELECT
            COUNT(DISTINCT hcpcs_mod) as total_codes,
            COUNT(DISTINCT hcpcs) as unique_hcpcs
        FROM drinf.v_rvu_clean
        WHERE year = %s {status_filter}
    """
    return pd.read_sql(query, conn, params=[year]).iloc[0]


@st.cache_data(ttl=3600)
def get_top_movers(year, locality_id, n=15, direction='increase', setting='nonfacility', payable_only=True):
    """Get top payment increases or decreases.

    Args:
        year: The year to analyze
        locality_id: The locality for comparison
        n: Number of results to return
        direction: 'increase' or 'decrease'
        setting: 'nonfacility' or 'facility'
        payable_only: Filter to payable codes only
    """
    conn = get_connection()

    # Validate setting to prevent SQL injection via column names
    if setting not in ('nonfacility', 'facility'):
        setting = 'nonfacility'

    status_filter = f"AND r.{STATUS_FILTER_CLAUSE}" if payable_only else ""
    sort_order = "DESC" if direction == 'increase' else "ASC"

    query = f"""
        SELECT
            y.hcpcs,
            y.modifier,
            r.description,
            y.allowed_{setting}_py as prior_year,
            y.allowed_{setting} as current_year,
            y.allowed_{setting}_change as change,
            y.allowed_{setting}_pct_change as pct_change,
            y.w_rvu,
            y.pe_rvu_{setting},
            y.mp_rvu
        FROM drinf.v_mpfs_allowed_yoy y
        JOIN drinf.v_rvu_clean r ON r.year = y.year AND r.hcpcs_mod = y.hcpcs_mod
        WHERE y.year = %s
          AND y.locality_id = %s
          AND y.allowed_{setting}_change IS NOT NULL
          {status_filter}
        ORDER BY y.allowed_{setting}_change {sort_order}
        LIMIT %s
    """
    return pd.read_sql(query, conn, params=[year, locality_id, n])


@st.cache_data(ttl=3600)
def get_payment_change_distribution(year, locality_id, setting='nonfacility', payable_only=True):
    """Get distribution of payment changes for histogram."""
    conn = get_connection()

    if setting not in ('nonfacility', 'facility'):
        setting = 'nonfacility'

    status_filter = f"AND r.{STATUS_FILTER_CLAUSE}" if payable_only else ""

    query = f"""
        SELECT y.allowed_{setting}_pct_change as pct_change
        FROM drinf.v_mpfs_allowed_yoy y
        JOIN drinf.v_rvu_clean r ON r.year = y.year AND r.hcpcs_mod = y.hcpcs_mod
        WHERE y.year = %s
          AND y.locality_id = %s
          AND y.allowed_{setting}_pct_change IS NOT NULL
          AND y.allowed_{setting}_pct_change BETWEEN -50 AND 50
          {status_filter}
    """
    return pd.read_sql(query, conn, params=[year, locality_id])


@st.cache_data(ttl=3600)
def get_codes_with_cuts(year, locality_id, setting='nonfacility', payable_only=True):
    """Get count of codes with payment decreases."""
    conn = get_connection()

    if setting not in ('nonfacility', 'facility'):
        setting = 'nonfacility'

    status_filter = f"AND r.{STATUS_FILTER_CLAUSE}" if payable_only else ""

    query = f"""
        SELECT COUNT(DISTINCT y.hcpcs_mod) as cut_count
        FROM drinf.v_mpfs_allowed_yoy y
        JOIN drinf.v_rvu_clean r ON r.year = y.year AND r.hcpcs_mod = y.hcpcs_mod
        WHERE y.year = %s
          AND y.locality_id = %s
          AND y.allowed_{setting}_change < 0
          {status_filter}
    """
    return pd.read_sql(query, conn, params=[year, locality_id]).iloc[0]['cut_count']


# ============================================================================
# Page 2: Code Trend Explorer
# ============================================================================

@st.cache_data(ttl=3600)
def get_code_trend(hcpcs_mod, locality_ids, setting='nonfacility'):
    """Get allowed amount trend for a code across localities."""
    conn = get_connection()

    if setting not in ('nonfacility', 'facility'):
        setting = 'nonfacility'

    # Build parameterized IN clause
    placeholders = ','.join(['%s'] * len(locality_ids))
    params = [hcpcs_mod] + list(locality_ids)

    query = f"""
        SELECT
            y.year,
            y.locality_id,
            g.locality_name,
            y.allowed_{setting} as allowed,
            y.w_rvu,
            y.conversion_factor
        FROM drinf.v_mpfs_allowed_yoy y
        JOIN drinf.v_gpci_clean g ON g.year = y.year AND g.locality_id = y.locality_id
        WHERE y.hcpcs_mod = %s
          AND y.locality_id IN ({placeholders})
        ORDER BY y.year, y.locality_id
    """
    return pd.read_sql(query, conn, params=params)


@st.cache_data(ttl=3600)
def get_code_yoy_detail(hcpcs_mod, locality_ids, setting='nonfacility'):
    """Get YoY detail table for a code across localities."""
    conn = get_connection()

    if setting not in ('nonfacility', 'facility'):
        setting = 'nonfacility'

    placeholders = ','.join(['%s'] * len(locality_ids))
    params = [hcpcs_mod] + list(locality_ids)

    query = f"""
        SELECT
            y.year,
            y.locality_id,
            g.locality_name,
            y.allowed_{setting} as current_allowed,
            y.allowed_{setting}_py as prior_allowed,
            y.allowed_{setting}_change as change,
            y.allowed_{setting}_pct_change as pct_change,
            y.w_rvu,
            y.conversion_factor
        FROM drinf.v_mpfs_allowed_yoy y
        JOIN drinf.v_gpci_clean g ON g.year = y.year AND g.locality_id = y.locality_id
        WHERE y.hcpcs_mod = %s
          AND y.locality_id IN ({placeholders})
        ORDER BY y.year DESC, g.locality_name
    """
    return pd.read_sql(query, conn, params=params)


@st.cache_data(ttl=3600)
def get_locality_comparison(hcpcs_mod, year, setting='nonfacility', top_n=20):
    """Get payment by locality for bar chart comparison."""
    conn = get_connection()

    if setting not in ('nonfacility', 'facility'):
        setting = 'nonfacility'

    query = f"""
        SELECT
            a.locality_id,
            g.locality_name,
            a.allowed_{setting} as allowed,
            a.gpci_work,
            a.gpci_pe,
            a.gpci_mp
        FROM drinf.v_mpfs_allowed a
        JOIN drinf.v_gpci_clean g ON g.year = a.year AND g.locality_id = a.locality_id
        WHERE a.hcpcs_mod = %s
          AND a.year = %s
        ORDER BY a.allowed_{setting} DESC
        LIMIT %s
    """
    return pd.read_sql(query, conn, params=[hcpcs_mod, year, top_n])


# ============================================================================
# Page 3: GPCI Locality Explorer
# ============================================================================

@st.cache_data(ttl=3600)
def get_gpci_rankings(year):
    """Get GPCI rankings by locality."""
    conn = get_connection()

    query = """
        SELECT
            locality_id,
            locality_name,
            state,
            gpci_work,
            gpci_pe,
            gpci_mp,
            (gpci_work + gpci_pe + gpci_mp) / 3.0 as gpci_composite,
            gpci_work_change,
            gpci_pe_change,
            gpci_mp_change
        FROM drinf.v_gpci_yoy
        WHERE year = %s
        ORDER BY (gpci_work + gpci_pe + gpci_mp) / 3.0 DESC
    """
    return pd.read_sql(query, conn, params=[year])


@st.cache_data(ttl=3600)
def get_gpci_yoy_changes(year, component='work', n=15):
    """Get largest GPCI YoY changes."""
    conn = get_connection()

    # Validate component
    if component not in ('work', 'pe', 'mp'):
        component = 'work'

    query = f"""
        SELECT
            locality_id,
            locality_name,
            state,
            gpci_{component}_py as prior_value,
            gpci_{component} as current_value,
            gpci_{component}_change as change,
            gpci_{component}_pct_change as pct_change
        FROM drinf.v_gpci_yoy
        WHERE year = %s
          AND gpci_{component}_py IS NOT NULL
        ORDER BY ABS(gpci_{component}_change) DESC
        LIMIT %s
    """
    return pd.read_sql(query, conn, params=[year, n])


@st.cache_data(ttl=3600)
def get_gpci_trend(locality_id):
    """Get GPCI component trends for a locality."""
    conn = get_connection()

    query = """
        SELECT
            year,
            gpci_work,
            gpci_pe,
            gpci_mp
        FROM drinf.v_gpci_yoy
        WHERE locality_id = %s
        ORDER BY year
    """
    return pd.read_sql(query, conn, params=[locality_id])


# ============================================================================
# Page 4: Locality Spread
# ============================================================================

@st.cache_data(ttl=3600)
def get_locality_spread(hcpcs_mod, year, setting='nonfacility'):
    """Get all localities for spread analysis."""
    conn = get_connection()

    if setting not in ('nonfacility', 'facility'):
        setting = 'nonfacility'

    query = f"""
        SELECT
            a.locality_id,
            g.locality_name,
            g.state,
            a.allowed_{setting} as allowed,
            a.gpci_work,
            a.gpci_pe,
            a.gpci_mp
        FROM drinf.v_mpfs_allowed a
        JOIN drinf.v_gpci_clean g ON g.year = a.year AND g.locality_id = a.locality_id
        WHERE a.hcpcs_mod = %s
          AND a.year = %s
        ORDER BY a.allowed_{setting} DESC
    """
    return pd.read_sql(query, conn, params=[hcpcs_mod, year])


@st.cache_data(ttl=3600)
def get_spread_stats(hcpcs_mod, year, setting='nonfacility'):
    """Calculate spread statistics for a code."""
    conn = get_connection()

    if setting not in ('nonfacility', 'facility'):
        setting = 'nonfacility'

    query = f"""
        SELECT
            MAX(allowed_{setting}) as max_allowed,
            MIN(allowed_{setting}) as min_allowed,
            AVG(allowed_{setting}) as avg_allowed,
            STDDEV(allowed_{setting}) as std_dev,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY allowed_{setting}) as median
        FROM drinf.v_mpfs_allowed
        WHERE hcpcs_mod = %s
          AND year = %s
    """
    return pd.read_sql(query, conn, params=[hcpcs_mod, year]).iloc[0]


# ============================================================================
# Page 5: Change Decomposition
# ============================================================================

@st.cache_data(ttl=3600)
def get_decomposition(hcpcs_mod, locality_id, year, setting='nonfacility'):
    """Get decomposition data for waterfall chart."""
    conn = get_connection()

    if setting not in ('nonfacility', 'facility'):
        setting = 'nonfacility'

    query = f"""
        SELECT
            year,
            allowed_{setting}_py as prior_allowed,
            allowed_{setting} as current_allowed,
            total_change_{setting} as total_change,
            cf_effect_{setting} as cf_effect,
            gpci_effect_{setting} as gpci_effect,
            rvu_effect_{setting} as rvu_effect,
            w_rvu_py,
            w_rvu,
            gpci_work_py,
            gpci_work,
            cf_py,
            conversion_factor
        FROM drinf.v_mpfs_decomp
        WHERE hcpcs_mod = %s
          AND locality_id = %s
          AND year = %s
    """
    df = pd.read_sql(query, conn, params=[hcpcs_mod, locality_id, year])
    return df.iloc[0] if len(df) > 0 else None


@st.cache_data(ttl=3600)
def get_decomposition_history(hcpcs_mod, locality_id, setting='nonfacility'):
    """Get full decomposition history for a code/locality."""
    conn = get_connection()

    if setting not in ('nonfacility', 'facility'):
        setting = 'nonfacility'

    query = f"""
        SELECT
            year,
            allowed_{setting}_py as prior_allowed,
            allowed_{setting} as current_allowed,
            total_change_{setting} as total_change,
            cf_effect_{setting} as cf_effect,
            gpci_effect_{setting} as gpci_effect,
            rvu_effect_{setting} as rvu_effect,
            w_rvu_py,
            w_rvu,
            gpci_work_py,
            gpci_work,
            cf_py,
            conversion_factor
        FROM drinf.v_mpfs_decomp
        WHERE hcpcs_mod = %s
          AND locality_id = %s
        ORDER BY year DESC
    """
    return pd.read_sql(query, conn, params=[hcpcs_mod, locality_id])


# ============================================================================
# Formatting Utilities
# ============================================================================

def format_currency(value, decimals=2):
    """Format a value as currency."""
    if pd.isna(value):
        return "-"
    return f"${value:,.{decimals}f}"


def format_percent(value, decimals=1):
    """Format a value as percentage."""
    if pd.isna(value):
        return "-"
    return f"{value:+.{decimals}f}%"


def format_change(value, decimals=2):
    """Format a change value with sign."""
    if pd.isna(value):
        return "-"
    return f"{value:+,.{decimals}f}"


def get_change_color(value):
    """Get color based on positive/negative change."""
    if pd.isna(value) or value == 0:
        return COLORS["neutral"]
    return COLORS["positive"] if value > 0 else COLORS["negative"]


# ============================================================================
# Intelligence Brief Functions
# ============================================================================

def get_codes_analysis(hcpcs_codes, year, locality_id='AL-00', setting='nonfacility'):
    """Get comprehensive analysis for a list of HCPCS codes.

    Returns dict with summary stats, individual code details, and insights.
    """
    if not hcpcs_codes:
        return None

    conn = get_connection()

    if setting not in ('nonfacility', 'facility'):
        setting = 'nonfacility'

    # Build parameterized IN clause
    placeholders = ','.join(['%s'] * len(hcpcs_codes))
    base_params = [year, locality_id] + list(hcpcs_codes)

    # Get YoY data for selected codes
    query = f"""
        SELECT
            y.hcpcs,
            y.modifier,
            y.hcpcs_mod,
            r.description,
            y.allowed_{setting}_py as prior_allowed,
            y.allowed_{setting} as current_allowed,
            y.allowed_{setting}_change as change,
            y.allowed_{setting}_pct_change as pct_change,
            y.w_rvu,
            y.conversion_factor
        FROM drinf.v_mpfs_allowed_yoy y
        JOIN drinf.v_rvu_clean r ON r.year = y.year AND r.hcpcs_mod = y.hcpcs_mod
        WHERE y.year = %s
          AND y.locality_id = %s
          AND y.hcpcs IN ({placeholders})
        ORDER BY y.allowed_{setting}_change DESC
    """
    codes_df = pd.read_sql(query, conn, params=base_params)

    if len(codes_df) == 0:
        return None

    # Calculate summary stats
    avg_change = codes_df['pct_change'].mean()
    total_codes = len(codes_df)
    codes_increased = (codes_df['change'] > 0).sum()
    codes_decreased = (codes_df['change'] < 0).sum()

    # Find highest paying locality for primary code
    primary_code = hcpcs_codes[0]
    geo_query_primary = f"""
        SELECT
            g.locality_name,
            g.locality_id,
            a.allowed_{setting} as allowed
        FROM drinf.v_mpfs_allowed a
        JOIN drinf.v_gpci_clean g ON g.year = a.year AND g.locality_id = a.locality_id
        WHERE a.year = %s
          AND a.hcpcs = %s
          AND a.modifier IS NULL
        ORDER BY a.allowed_{setting} DESC
        LIMIT 5
    """
    top_localities = pd.read_sql(geo_query_primary, conn, params=[year, primary_code])

    # Get national average
    avg_query = f"""
        SELECT AVG(allowed_{setting}) as avg_allowed
        FROM drinf.v_mpfs_allowed
        WHERE year = %s
          AND hcpcs = %s
          AND modifier IS NULL
    """
    national_avg = pd.read_sql(avg_query, conn, params=[year, primary_code]).iloc[0]['avg_allowed']

    return {
        'codes_df': codes_df,
        'summary': {
            'avg_pct_change': avg_change,
            'total_codes': total_codes,
            'codes_increased': codes_increased,
            'codes_decreased': codes_decreased,
        },
        'top_localities': top_localities,
        'national_avg': national_avg
    }


def get_utilization_data(hcpcs_codes, year=None):
    """Get utilization data for a list of HCPCS codes.

    Returns DataFrame with services, beneficiaries, payments by code.
    """
    if not hcpcs_codes:
        return pd.DataFrame()

    conn = get_connection()

    placeholders = ','.join(['%s'] * len(hcpcs_codes))
    params = list(hcpcs_codes)

    year_filter = ""
    if year:
        year_filter = "AND year = %s"
        params.append(year)

    query = f"""
        SELECT
            year,
            hcpcs,
            hcpcs_desc,
            place_of_service,
            total_services,
            total_beneficiaries,
            avg_payment_amt,
            (total_services * avg_payment_amt) as total_medicare_payment
        FROM drinf.medicare_utilization
        WHERE hcpcs IN ({placeholders})
          AND geo_level = 'National'
          {year_filter}
        ORDER BY year DESC, total_services DESC
    """
    return pd.read_sql(query, conn, params=params)


def get_utilization_summary(hcpcs_codes, year):
    """Get summary utilization stats for a list of codes.

    Returns dict with total services, beneficiaries, payments.
    """
    if not hcpcs_codes:
        return {'total_services': 0, 'total_beneficiaries': 0, 'total_medicare_payment': 0}

    conn = get_connection()

    placeholders = ','.join(['%s'] * len(hcpcs_codes))
    params = list(hcpcs_codes) + [year]

    query = f"""
        SELECT
            SUM(total_services) as total_services,
            SUM(total_beneficiaries) as total_beneficiaries,
            SUM(total_services * avg_payment_amt) as total_medicare_payment
        FROM drinf.medicare_utilization
        WHERE hcpcs IN ({placeholders})
          AND geo_level = 'National'
          AND year = %s
    """
    result = pd.read_sql(query, conn, params=params).iloc[0]

    return {
        'total_services': int(result['total_services']) if pd.notna(result['total_services']) else 0,
        'total_beneficiaries': int(result['total_beneficiaries']) if pd.notna(result['total_beneficiaries']) else 0,
        'total_medicare_payment': float(result['total_medicare_payment']) if pd.notna(result['total_medicare_payment']) else 0,
    }


def generate_brief_email(topic, analysis, year, chart_path=None, dashboard_url=None, utilization=None):
    """Generate formatted email content for intelligence brief.

    Returns markdown string ready for display or export.
    """
    if analysis is None:
        return "No data available for the selected codes."

    codes_df = analysis['codes_df']
    summary = analysis['summary']
    top_localities = analysis['top_localities']
    national_avg = analysis['national_avg']

    # Determine overall trend direction
    if summary['avg_pct_change'] > 0.5:
        trend_word = "increase"
        trend_emoji = ""
    elif summary['avg_pct_change'] < -0.5:
        trend_word = "decrease"
        trend_emoji = ""
    else:
        trend_word = "remain relatively flat"
        trend_emoji = ""

    # Build the email
    email = f"""## MPFS Intelligence Brief: {topic}

*Analysis of Medicare Physician Fee Schedule data for {year}*

---

### Key Finding

{topic} codes saw payments **{trend_word} by {abs(summary['avg_pct_change']):.1f}%** on average in {year}, driven primarily by conversion factor changes.

- **{summary['codes_increased']}** codes with payment increases
- **{summary['codes_decreased']}** codes with payment decreases

---

### Code-Level Detail

| CPT | Description | Prior Year | Current | Change |
|-----|-------------|------------|---------|--------|
"""

    # Add top 5-10 codes to table
    for _, row in codes_df.head(10).iterrows():
        desc = row['description'][:35] + "..." if len(str(row['description'])) > 35 else row['description']
        prior = format_currency(row['prior_allowed'])
        current = format_currency(row['current_allowed'])
        change = format_percent(row['pct_change'])
        email += f"| {row['hcpcs']} | {desc} | {prior} | {current} | {change} |\n"

    # Add utilization section if data available
    if utilization and utilization.get('total_services', 0) > 0:
        total_svc = utilization['total_services']
        total_bene = utilization['total_beneficiaries']
        total_pay = utilization['total_medicare_payment']

        email += f"""
---

### Medicare Utilization Context

These codes represent significant Medicare volume:

- **{total_svc:,.0f}** total services per year
- **{total_bene:,.0f}** unique beneficiaries
- **${total_pay/1e6:,.1f}M** total Medicare payments

"""
        # Calculate budget impact if we have payment change data
        if summary['avg_pct_change'] != 0:
            impact = total_pay * (summary['avg_pct_change'] / 100)
            impact_word = "increase" if impact > 0 else "decrease"
            email += f"**Estimated budget impact:** ${abs(impact)/1e6:,.1f}M {impact_word} based on payment rate changes.\n"

    email += f"""
---

### Geographic Variation

"""

    if len(top_localities) > 0 and national_avg:
        # Calculate geographic spread
        max_allowed = top_localities['allowed'].max()
        min_allowed = top_localities['allowed'].min() if len(top_localities) > 1 else max_allowed * 0.85
        spread_pct = ((max_allowed - min_allowed) / national_avg * 100) if national_avg else 0

        if spread_pct > 15:
            email += f"**Significant geographic variation detected** ({spread_pct:.0f}% spread across localities).\n\n"
            email += f"- **Highest paying:** {top_localities.iloc[0]['locality_name']} ({format_currency(max_allowed)})\n"
            email += f"- **National baseline:** {format_currency(national_avg)}\n"
            email += "\n*Consider locality-specific contract strategies for practices in high-GPCI areas.*\n"
        else:
            email += f"Geographic variation is **modest** ({spread_pct:.0f}% spread). National rates are reasonably consistent.\n"
    else:
        email += "Geographic data not available for these codes.\n"

    email += f"""
---

### Methodology

- **Fee Schedule Data:** CMS Physician Fee Schedule Relative Value Files (PPRRVU)
- **Utilization Data:** CMS Medicare Physician & Other Practitioners Public Use File (2023)
- **Reference Locality:** Alabama (AL-00) used for national baseline
- **Setting:** Non-Facility

"""

    if dashboard_url:
        email += f"\n**[View detailed analysis in dashboard]({dashboard_url})**\n"

    return email


# ============================================================================
# Page 7: CPT Economics & Site-of-Service Signals
# ============================================================================

@st.cache_data(ttl=3600)
def get_cpt_economics_data(year, locality_id):
    """Get CPT economics data with RVU shares and site-of-service gaps."""
    conn = get_connection()

    query = """
        SELECT
            a.hcpcs,
            a.modifier,
            a.hcpcs_mod,
            a.description,
            a.status_code,
            a.w_rvu,
            a.pe_rvu_facility,
            a.pe_rvu_nonfacility,
            a.mp_rvu,
            a.allowed_facility,
            a.allowed_nonfacility,
            a.gpci_pe,
            -- Calculate totals
            COALESCE(a.w_rvu, 0) + COALESCE(a.pe_rvu_nonfacility, 0) + COALESCE(a.mp_rvu, 0) as total_rvu_nf,
            COALESCE(a.w_rvu, 0) + COALESCE(a.pe_rvu_facility, 0) + COALESCE(a.mp_rvu, 0) as total_rvu_f,
            -- Site gap
            a.allowed_nonfacility - a.allowed_facility as site_gap,
            CASE WHEN a.allowed_facility > 0
                THEN (a.allowed_nonfacility - a.allowed_facility) / a.allowed_facility * 100
                ELSE NULL END as site_gap_pct
        FROM drinf.v_mpfs_allowed a
        WHERE a.year = %s
          AND a.locality_id = %s
          AND a.status_code NOT IN ('B', 'I', 'N', 'X', 'E', 'P')
          AND a.allowed_nonfacility IS NOT NULL
          AND a.allowed_facility IS NOT NULL
          AND (COALESCE(a.w_rvu, 0) + COALESCE(a.pe_rvu_nonfacility, 0) + COALESCE(a.mp_rvu, 0)) > 0
    """
    df = pd.read_sql(query, conn, params=[year, locality_id])

    # Calculate shares
    df['work_share_nf'] = df['w_rvu'] / df['total_rvu_nf'] * 100
    df['pe_share_nf'] = df['pe_rvu_nonfacility'] / df['total_rvu_nf'] * 100
    df['mp_share_nf'] = df['mp_rvu'] / df['total_rvu_nf'] * 100

    df['work_share_f'] = df['w_rvu'] / df['total_rvu_f'] * 100
    df['pe_share_f'] = df['pe_rvu_facility'] / df['total_rvu_f'] * 100
    df['mp_share_f'] = df['mp_rvu'] / df['total_rvu_f'] * 100

    # Geo sensitivity category based on PE share (non-facility)
    df['geo_sensitivity'] = pd.cut(
        df['pe_share_nf'],
        bins=[0, 40, 60, 100],
        labels=['Low', 'Medium', 'High']
    )

    # Economics category
    def categorize(row):
        if row['work_share_nf'] > 50 and row['pe_share_nf'] < 40:
            return 'Work-Heavy'
        elif row['pe_share_nf'] > 50 and row['work_share_nf'] < 40:
            return 'PE-Heavy'
        else:
            return 'Balanced'

    df['econ_category'] = df.apply(categorize, axis=1)

    return df


@st.cache_data(ttl=3600)
def get_cpt_economics_with_util(year, locality_id, util_year=2023):
    """Get CPT economics joined with utilization data."""
    conn = get_connection()

    query = """
        SELECT
            a.hcpcs,
            a.modifier,
            a.hcpcs_mod,
            a.description,
            a.status_code,
            a.w_rvu,
            a.pe_rvu_facility,
            a.pe_rvu_nonfacility,
            a.mp_rvu,
            a.allowed_facility,
            a.allowed_nonfacility,
            a.gpci_pe,
            COALESCE(a.w_rvu, 0) + COALESCE(a.pe_rvu_nonfacility, 0) + COALESCE(a.mp_rvu, 0) as total_rvu_nf,
            COALESCE(a.w_rvu, 0) + COALESCE(a.pe_rvu_facility, 0) + COALESCE(a.mp_rvu, 0) as total_rvu_f,
            a.allowed_nonfacility - a.allowed_facility as site_gap,
            CASE WHEN a.allowed_facility > 0
                THEN (a.allowed_nonfacility - a.allowed_facility) / a.allowed_facility * 100
                ELSE NULL END as site_gap_pct,
            -- Utilization
            COALESCE(u.total_services, 0) as total_services,
            COALESCE(u.total_beneficiaries, 0) as total_beneficiaries,
            COALESCE(u.avg_payment_amt, 0) as util_avg_payment,
            COALESCE(u.total_services * u.avg_payment_amt, 0) as total_medicare_dollars
        FROM drinf.v_mpfs_allowed a
        LEFT JOIN (
            SELECT hcpcs,
                   SUM(total_services) as total_services,
                   SUM(total_beneficiaries) as total_beneficiaries,
                   AVG(avg_payment_amt) as avg_payment_amt
            FROM drinf.medicare_utilization
            WHERE year = %s AND geo_level = 'National'
            GROUP BY hcpcs
        ) u ON a.hcpcs = u.hcpcs
        WHERE a.year = %s
          AND a.locality_id = %s
          AND a.status_code NOT IN ('B', 'I', 'N', 'X', 'E', 'P')
          AND a.allowed_nonfacility IS NOT NULL
          AND a.allowed_facility IS NOT NULL
          AND (COALESCE(a.w_rvu, 0) + COALESCE(a.pe_rvu_nonfacility, 0) + COALESCE(a.mp_rvu, 0)) > 0
    """
    df = pd.read_sql(query, conn, params=[util_year, year, locality_id])

    # Calculate shares
    df['work_share_nf'] = df['w_rvu'] / df['total_rvu_nf'] * 100
    df['pe_share_nf'] = df['pe_rvu_nonfacility'] / df['total_rvu_nf'] * 100
    df['mp_share_nf'] = df['mp_rvu'] / df['total_rvu_nf'] * 100

    df['work_share_f'] = df['w_rvu'] / df['total_rvu_f'] * 100
    df['pe_share_f'] = df['pe_rvu_facility'] / df['total_rvu_f'] * 100
    df['mp_share_f'] = df['mp_rvu'] / df['total_rvu_f'] * 100

    # Util-weighted gap impact
    df['util_gap_impact'] = df['total_services'] * df['site_gap']

    # Geo sensitivity
    df['geo_sensitivity'] = pd.cut(
        df['pe_share_nf'],
        bins=[0, 40, 60, 100],
        labels=['Low', 'Medium', 'High']
    )

    # Economics category
    def categorize(row):
        if row['work_share_nf'] > 50 and row['pe_share_nf'] < 40:
            return 'Work-Heavy'
        elif row['pe_share_nf'] > 50 and row['work_share_nf'] < 40:
            return 'PE-Heavy'
        else:
            return 'Balanced'

    df['econ_category'] = df.apply(categorize, axis=1)

    return df


@st.cache_data(ttl=3600)
def get_cpt_trend_data(hcpcs, locality_id):
    """Get trend data for a specific CPT across years."""
    conn = get_connection()

    query = """
        SELECT
            a.year,
            a.w_rvu,
            a.pe_rvu_facility,
            a.pe_rvu_nonfacility,
            a.mp_rvu,
            a.allowed_facility,
            a.allowed_nonfacility,
            COALESCE(a.w_rvu, 0) + COALESCE(a.pe_rvu_nonfacility, 0) + COALESCE(a.mp_rvu, 0) as total_rvu_nf,
            COALESCE(a.w_rvu, 0) + COALESCE(a.pe_rvu_facility, 0) + COALESCE(a.mp_rvu, 0) as total_rvu_f
        FROM drinf.v_mpfs_allowed a
        WHERE a.hcpcs = %s
          AND a.locality_id = %s
          AND a.modifier IS NULL
        ORDER BY a.year
    """
    df = pd.read_sql(query, conn, params=[hcpcs, locality_id])

    if len(df) > 0:
        df['work_share_nf'] = df['w_rvu'] / df['total_rvu_nf'] * 100
        df['pe_share_nf'] = df['pe_rvu_nonfacility'] / df['total_rvu_nf'] * 100
        df['work_share_f'] = df['w_rvu'] / df['total_rvu_f'] * 100
        df['pe_share_f'] = df['pe_rvu_facility'] / df['total_rvu_f'] * 100
        df['site_gap'] = df['allowed_nonfacility'] - df['allowed_facility']

    return df
