"""
MPFS Analytics Dashboard
Medicare Physician Fee Schedule Analysis Tool
"""
import streamlit as st
import pandas as pd
from utils import (
    get_connection,
    get_available_years,
    get_conversion_factors,
    get_summary_stats,
    COLORS,
    format_currency
)

st.set_page_config(
    page_title="MPFS Analytics",
    page_icon="$",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Main page content
st.title("MPFS Analytics Dashboard")
st.caption("Medicare Physician Fee Schedule | 2018-2026")

st.markdown("""
This dashboard provides insights into Medicare Physician Fee Schedule (MPFS) reimbursement trends,
geographic variation, and change drivers.

**Navigate using the sidebar to explore:**

| Page | Purpose |
|------|---------|
| **Baseline Monitor** | Current CF, top movers, overall payment distribution |
| **Code Trend Explorer** | Deep-dive into specific codes across localities |
| **GPCI Locality Explorer** | Geographic payment adjustments by locality |
| **Locality Spread** | Payment variation analysis across geographies |
| **Change Decomposition** | Waterfall analysis: CF vs GPCI vs RVU effects |

---
""")

# Load summary data
try:
    years = get_available_years()
    latest_year = max(years)
    cf_data = get_conversion_factors()

    # KPI Cards
    col1, col2, col3, col4 = st.columns(4)

    current_cf = cf_data[cf_data['year'] == latest_year]['conversion_factor'].values[0]
    prior_cf = cf_data[cf_data['year'] == latest_year - 1]['conversion_factor'].values[0]
    cf_change = ((current_cf - prior_cf) / prior_cf) * 100

    stats = get_summary_stats(latest_year, payable_only=True)

    with col1:
        st.metric(
            f"{latest_year} Conversion Factor",
            format_currency(current_cf, 4),
            f"{cf_change:+.2f}%"
        )

    with col2:
        st.metric("Years Covered", f"{min(years)} - {max(years)}")

    with col3:
        st.metric("Payable Codes", f"{stats['total_codes']:,}")

    with col4:
        st.metric("Localities", "~110")

    st.divider()

    # CF Trend Chart
    st.subheader("Conversion Factor Trend")

    import altair as alt

    cf_chart = alt.Chart(cf_data).mark_line(point=True, color=COLORS['accent']).encode(
        x=alt.X('year:O', title='Year'),
        y=alt.Y('conversion_factor:Q', title='Conversion Factor ($)',
                scale=alt.Scale(domain=[30, 40])),
        tooltip=[
            alt.Tooltip('year:O', title='Year'),
            alt.Tooltip('conversion_factor:Q', title='CF', format='$.4f')
        ]
    ).properties(
        height=300
    )

    st.altair_chart(cf_chart, use_container_width=True)

    st.markdown("""
    ---
    **Data Source:** CMS Physician Fee Schedule Relative Value Files
    **Coverage:** 9 years (2018-2026) | ~160,000 code records | ~110 localities
    """)

except Exception as e:
    st.error(f"Database connection error: {e}")
    st.info("Ensure PostgreSQL is running and the analytics views exist in the drinf schema.")
    st.code("""
    -- Required views:
    -- drinf.v_cf_clean
    -- drinf.v_rvu_clean
    -- drinf.v_gpci_clean
    -- drinf.v_mpfs_allowed
    -- drinf.v_mpfs_allowed_yoy
    -- drinf.v_gpci_yoy
    -- drinf.v_mpfs_decomp
    """)
