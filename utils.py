"""
Shared utilities for MPFS Analytics Dashboard
"""
import streamlit as st
import pandas as pd
import psycopg2

# Database configuration - uses Streamlit secrets in production
def get_db_config():
    """Get database config from Streamlit secrets or fallback to local."""
    try:
        return {
            "host": st.secrets["database"]["host"],
            "database": st.secrets["database"]["database"],
            "user": st.secrets["database"]["user"],
            "password": st.secrets["database"]["password"],
            "port": st.secrets["database"]["port"],
        }
    except Exception:
        # Fallback for local development
        return {
            "host": "127.0.0.1",
            "database": "postgres",
            "user": "postgres",
            "password": "lolsk8s",
            "port": 5432,
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

    year_filter = f"WHERE year = {year}" if year else ""
    if payable_only:
        status_filter = f"status_code NOT IN ({','.join(repr(s) for s in NON_PAYABLE_STATUS)})"
        if year_filter:
            year_filter += f" AND {status_filter}"
        else:
            year_filter = f"WHERE {status_filter}"

    query = f"""
        SELECT DISTINCT hcpcs_mod, hcpcs, modifier, description
        FROM drinf.v_rvu_clean
        {year_filter}
        ORDER BY hcpcs_mod
    """
    return pd.read_sql(query, conn)


# ============================================================================
# Page 1: Baseline Monitor
# ============================================================================

@st.cache_data(ttl=3600)
def get_summary_stats(year, payable_only=True):
    """Get summary statistics for a given year."""
    conn = get_connection()

    status_filter = ""
    if payable_only:
        status_filter = f"AND status_code NOT IN ({','.join(repr(s) for s in NON_PAYABLE_STATUS)})"

    query = f"""
        SELECT
            COUNT(DISTINCT hcpcs_mod) as total_codes,
            COUNT(DISTINCT hcpcs) as unique_hcpcs
        FROM drinf.v_rvu_clean
        WHERE year = {year} {status_filter}
    """
    return pd.read_sql(query, conn).iloc[0]


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

    status_filter = ""
    if payable_only:
        status_filter = f"AND r.status_code NOT IN ({','.join(repr(s) for s in NON_PAYABLE_STATUS)})"

    allowed_col = f"allowed_{setting}"
    allowed_py_col = f"allowed_{setting}_py"
    change_col = f"allowed_{setting}_change"
    pct_change_col = f"allowed_{setting}_pct_change"

    sort_order = "DESC" if direction == 'increase' else "ASC"

    query = f"""
        SELECT
            y.hcpcs,
            y.modifier,
            r.description,
            y.{allowed_py_col} as prior_year,
            y.{allowed_col} as current_year,
            y.{change_col} as change,
            y.{pct_change_col} as pct_change,
            y.w_rvu,
            y.pe_rvu_{setting},
            y.mp_rvu
        FROM drinf.v_mpfs_allowed_yoy y
        JOIN drinf.v_rvu_clean r ON r.year = y.year AND r.hcpcs_mod = y.hcpcs_mod
        WHERE y.year = {year}
          AND y.locality_id = '{locality_id}'
          AND y.{change_col} IS NOT NULL
          {status_filter}
        ORDER BY y.{change_col} {sort_order}
        LIMIT {n}
    """
    return pd.read_sql(query, conn)


@st.cache_data(ttl=3600)
def get_payment_change_distribution(year, locality_id, setting='nonfacility', payable_only=True):
    """Get distribution of payment changes for histogram."""
    conn = get_connection()

    status_filter = ""
    if payable_only:
        status_filter = f"AND r.status_code NOT IN ({','.join(repr(s) for s in NON_PAYABLE_STATUS)})"

    pct_change_col = f"allowed_{setting}_pct_change"

    query = f"""
        SELECT y.{pct_change_col} as pct_change
        FROM drinf.v_mpfs_allowed_yoy y
        JOIN drinf.v_rvu_clean r ON r.year = y.year AND r.hcpcs_mod = y.hcpcs_mod
        WHERE y.year = {year}
          AND y.locality_id = '{locality_id}'
          AND y.{pct_change_col} IS NOT NULL
          AND y.{pct_change_col} BETWEEN -50 AND 50
          {status_filter}
    """
    return pd.read_sql(query, conn)


@st.cache_data(ttl=3600)
def get_codes_with_cuts(year, locality_id, setting='nonfacility', payable_only=True):
    """Get count of codes with payment decreases."""
    conn = get_connection()

    status_filter = ""
    if payable_only:
        status_filter = f"AND r.status_code NOT IN ({','.join(repr(s) for s in NON_PAYABLE_STATUS)})"

    change_col = f"allowed_{setting}_change"

    query = f"""
        SELECT COUNT(DISTINCT y.hcpcs_mod) as cut_count
        FROM drinf.v_mpfs_allowed_yoy y
        JOIN drinf.v_rvu_clean r ON r.year = y.year AND r.hcpcs_mod = y.hcpcs_mod
        WHERE y.year = {year}
          AND y.locality_id = '{locality_id}'
          AND y.{change_col} < 0
          {status_filter}
    """
    return pd.read_sql(query, conn).iloc[0]['cut_count']


# ============================================================================
# Page 2: Code Trend Explorer
# ============================================================================

@st.cache_data(ttl=3600)
def get_code_trend(hcpcs_mod, locality_ids, setting='nonfacility'):
    """Get allowed amount trend for a code across localities."""
    conn = get_connection()

    locality_filter = ",".join(f"'{loc}'" for loc in locality_ids)
    allowed_col = f"allowed_{setting}"

    query = f"""
        SELECT
            y.year,
            y.locality_id,
            g.locality_name,
            y.{allowed_col} as allowed,
            y.w_rvu,
            y.conversion_factor
        FROM drinf.v_mpfs_allowed_yoy y
        JOIN drinf.v_gpci_clean g ON g.year = y.year AND g.locality_id = y.locality_id
        WHERE y.hcpcs_mod = '{hcpcs_mod}'
          AND y.locality_id IN ({locality_filter})
        ORDER BY y.year, y.locality_id
    """
    return pd.read_sql(query, conn)


@st.cache_data(ttl=3600)
def get_code_yoy_detail(hcpcs_mod, locality_ids, setting='nonfacility'):
    """Get YoY detail table for a code across localities."""
    conn = get_connection()

    locality_filter = ",".join(f"'{loc}'" for loc in locality_ids)
    allowed_col = f"allowed_{setting}"
    allowed_py_col = f"allowed_{setting}_py"
    change_col = f"allowed_{setting}_change"
    pct_change_col = f"allowed_{setting}_pct_change"

    query = f"""
        SELECT
            y.year,
            y.locality_id,
            g.locality_name,
            y.{allowed_col} as current_allowed,
            y.{allowed_py_col} as prior_allowed,
            y.{change_col} as change,
            y.{pct_change_col} as pct_change,
            y.w_rvu,
            y.conversion_factor
        FROM drinf.v_mpfs_allowed_yoy y
        JOIN drinf.v_gpci_clean g ON g.year = y.year AND g.locality_id = y.locality_id
        WHERE y.hcpcs_mod = '{hcpcs_mod}'
          AND y.locality_id IN ({locality_filter})
        ORDER BY y.year DESC, g.locality_name
    """
    return pd.read_sql(query, conn)


@st.cache_data(ttl=3600)
def get_locality_comparison(hcpcs_mod, year, setting='nonfacility', top_n=20):
    """Get payment by locality for bar chart comparison."""
    conn = get_connection()

    allowed_col = f"allowed_{setting}"

    query = f"""
        SELECT
            a.locality_id,
            g.locality_name,
            a.{allowed_col} as allowed,
            a.gpci_work,
            a.gpci_pe,
            a.gpci_mp
        FROM drinf.v_mpfs_allowed a
        JOIN drinf.v_gpci_clean g ON g.year = a.year AND g.locality_id = a.locality_id
        WHERE a.hcpcs_mod = '{hcpcs_mod}'
          AND a.year = {year}
        ORDER BY a.{allowed_col} DESC
        LIMIT {top_n}
    """
    return pd.read_sql(query, conn)


# ============================================================================
# Page 3: GPCI Locality Explorer
# ============================================================================

@st.cache_data(ttl=3600)
def get_gpci_rankings(year):
    """Get GPCI rankings by locality."""
    conn = get_connection()

    query = f"""
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
        WHERE year = {year}
        ORDER BY (gpci_work + gpci_pe + gpci_mp) / 3.0 DESC
    """
    return pd.read_sql(query, conn)


@st.cache_data(ttl=3600)
def get_gpci_yoy_changes(year, component='work', n=15):
    """Get largest GPCI YoY changes."""
    conn = get_connection()

    gpci_col = f"gpci_{component}"
    gpci_py_col = f"gpci_{component}_py"
    change_col = f"gpci_{component}_change"
    pct_change_col = f"gpci_{component}_pct_change"

    query = f"""
        SELECT
            locality_id,
            locality_name,
            state,
            {gpci_py_col} as prior_value,
            {gpci_col} as current_value,
            {change_col} as change,
            {pct_change_col} as pct_change
        FROM drinf.v_gpci_yoy
        WHERE year = {year}
          AND {gpci_py_col} IS NOT NULL
        ORDER BY ABS({change_col}) DESC
        LIMIT {n}
    """
    return pd.read_sql(query, conn)


@st.cache_data(ttl=3600)
def get_gpci_trend(locality_id):
    """Get GPCI component trends for a locality."""
    conn = get_connection()

    query = f"""
        SELECT
            year,
            gpci_work,
            gpci_pe,
            gpci_mp
        FROM drinf.v_gpci_yoy
        WHERE locality_id = '{locality_id}'
        ORDER BY year
    """
    return pd.read_sql(query, conn)


# ============================================================================
# Page 4: Locality Spread
# ============================================================================

@st.cache_data(ttl=3600)
def get_locality_spread(hcpcs_mod, year, setting='nonfacility'):
    """Get all localities for spread analysis."""
    conn = get_connection()

    allowed_col = f"allowed_{setting}"

    query = f"""
        SELECT
            a.locality_id,
            g.locality_name,
            g.state,
            a.{allowed_col} as allowed,
            a.gpci_work,
            a.gpci_pe,
            a.gpci_mp
        FROM drinf.v_mpfs_allowed a
        JOIN drinf.v_gpci_clean g ON g.year = a.year AND g.locality_id = a.locality_id
        WHERE a.hcpcs_mod = '{hcpcs_mod}'
          AND a.year = {year}
        ORDER BY a.{allowed_col} DESC
    """
    return pd.read_sql(query, conn)


@st.cache_data(ttl=3600)
def get_spread_stats(hcpcs_mod, year, setting='nonfacility'):
    """Calculate spread statistics for a code."""
    conn = get_connection()

    allowed_col = f"allowed_{setting}"

    query = f"""
        SELECT
            MAX({allowed_col}) as max_allowed,
            MIN({allowed_col}) as min_allowed,
            AVG({allowed_col}) as avg_allowed,
            STDDEV({allowed_col}) as std_dev,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {allowed_col}) as median
        FROM drinf.v_mpfs_allowed
        WHERE hcpcs_mod = '{hcpcs_mod}'
          AND year = {year}
    """
    return pd.read_sql(query, conn).iloc[0]


# ============================================================================
# Page 5: Change Decomposition
# ============================================================================

@st.cache_data(ttl=3600)
def get_decomposition(hcpcs_mod, locality_id, year, setting='nonfacility'):
    """Get decomposition data for waterfall chart."""
    conn = get_connection()

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
        WHERE hcpcs_mod = '{hcpcs_mod}'
          AND locality_id = '{locality_id}'
          AND year = {year}
    """
    df = pd.read_sql(query, conn)
    return df.iloc[0] if len(df) > 0 else None


@st.cache_data(ttl=3600)
def get_decomposition_history(hcpcs_mod, locality_id, setting='nonfacility'):
    """Get full decomposition history for a code/locality."""
    conn = get_connection()

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
        WHERE hcpcs_mod = '{hcpcs_mod}'
          AND locality_id = '{locality_id}'
        ORDER BY year DESC
    """
    return pd.read_sql(query, conn)


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
    conn = get_connection()

    codes_str = ",".join(f"'{c}'" for c in hcpcs_codes)
    allowed_col = f"allowed_{setting}"
    allowed_py_col = f"allowed_{setting}_py"
    change_col = f"allowed_{setting}_change"
    pct_change_col = f"allowed_{setting}_pct_change"

    # Get YoY data for selected codes
    query = f"""
        SELECT
            y.hcpcs,
            y.modifier,
            y.hcpcs_mod,
            r.description,
            y.{allowed_py_col} as prior_allowed,
            y.{allowed_col} as current_allowed,
            y.{change_col} as change,
            y.{pct_change_col} as pct_change,
            y.w_rvu,
            y.conversion_factor
        FROM drinf.v_mpfs_allowed_yoy y
        JOIN drinf.v_rvu_clean r ON r.year = y.year AND r.hcpcs_mod = y.hcpcs_mod
        WHERE y.year = {year}
          AND y.locality_id = '{locality_id}'
          AND y.hcpcs IN ({codes_str})
        ORDER BY y.{change_col} DESC
    """
    codes_df = pd.read_sql(query, conn)

    if len(codes_df) == 0:
        return None

    # Calculate summary stats
    avg_change = codes_df['pct_change'].mean()
    total_codes = len(codes_df)
    codes_increased = (codes_df['change'] > 0).sum()
    codes_decreased = (codes_df['change'] < 0).sum()

    # Get geographic variation for these codes
    geo_query = f"""
        SELECT
            a.hcpcs,
            g.locality_name,
            a.{allowed_col} as allowed
        FROM drinf.v_mpfs_allowed a
        JOIN drinf.v_gpci_clean g ON g.year = a.year AND g.locality_id = a.locality_id
        WHERE a.year = {year}
          AND a.hcpcs IN ({codes_str})
          AND a.modifier IS NULL
    """
    geo_df = pd.read_sql(query, conn)

    # Find highest paying locality for primary code
    primary_code = hcpcs_codes[0]
    geo_query_primary = f"""
        SELECT
            g.locality_name,
            g.locality_id,
            a.{allowed_col} as allowed
        FROM drinf.v_mpfs_allowed a
        JOIN drinf.v_gpci_clean g ON g.year = a.year AND g.locality_id = a.locality_id
        WHERE a.year = {year}
          AND a.hcpcs = '{primary_code}'
          AND a.modifier IS NULL
        ORDER BY a.{allowed_col} DESC
        LIMIT 5
    """
    top_localities = pd.read_sql(geo_query_primary, conn)

    # Get national average
    avg_query = f"""
        SELECT AVG({allowed_col}) as avg_allowed
        FROM drinf.v_mpfs_allowed
        WHERE year = {year}
          AND hcpcs = '{primary_code}'
          AND modifier IS NULL
    """
    national_avg = pd.read_sql(avg_query, conn).iloc[0]['avg_allowed']

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
    conn = get_connection()

    codes_str = ",".join(f"'{c}'" for c in hcpcs_codes)
    year_filter = f"AND year = {year}" if year else ""

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
        WHERE hcpcs IN ({codes_str})
          AND geo_level = 'National'
          {year_filter}
        ORDER BY year DESC, total_services DESC
    """
    return pd.read_sql(query, conn)


def get_utilization_summary(hcpcs_codes, year):
    """Get summary utilization stats for a list of codes.

    Returns dict with total services, beneficiaries, payments.
    """
    conn = get_connection()

    codes_str = ",".join(f"'{c}'" for c in hcpcs_codes)

    query = f"""
        SELECT
            SUM(total_services) as total_services,
            SUM(total_beneficiaries) as total_beneficiaries,
            SUM(total_services * avg_payment_amt) as total_medicare_payment
        FROM drinf.medicare_utilization
        WHERE hcpcs IN ({codes_str})
          AND geo_level = 'National'
          AND year = {year}
    """
    result = pd.read_sql(query, conn).iloc[0]

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

### Geographic Insight

"""

    if len(top_localities) > 0:
        top_loc = top_localities.iloc[0]
        pct_above = ((top_loc['allowed'] - national_avg) / national_avg * 100) if national_avg else 0
        email += f"**{top_loc['locality_name']}** ({top_loc['locality_id']}) pays **{pct_above:.0f}% above** the national average for these codes.\n\n"
        email += "**Top paying localities:**\n"
        for _, loc in top_localities.iterrows():
            email += f"- {loc['locality_name']}: {format_currency(loc['allowed'])}\n"

    email += f"""
---

### Methodology

- **Data Source:** CMS Physician Fee Schedule Relative Value Files + Medicare Utilization Data
- **Reference Locality:** Alabama (AL-00) used for national baseline
- **Setting:** Non-Facility
- **Utilization Year:** 2023 (most recent available)

"""

    if dashboard_url:
        email += f"\n**[View detailed analysis in dashboard]({dashboard_url})**\n"

    return email
