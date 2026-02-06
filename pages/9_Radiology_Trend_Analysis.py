"""
Page 9: Radiology Trend Analysis
Utilization-weighted analysis of radiology reimbursement trends 2021-2026
"""
import streamlit as st
import pandas as pd
import altair as alt
from utils import (
    get_connection,
    get_available_years,
    COLORS,
    format_currency,
    format_percent
)

st.set_page_config(page_title="Radiology Trend Analysis", page_icon="$", layout="wide")

st.title("Radiology Reimbursement Trend Analysis")
st.caption("Utilization-weighted analysis of 7-series CPT code reimbursement trends (2021-2026)")

# =============================================================================
# Data Functions
# =============================================================================

@st.cache_data(ttl=3600)
def get_radiology_trend_summary():
    """Get year-over-year radiology reimbursement with utilization weighting."""
    conn = get_connection()

    query = '''
    WITH util AS (
        SELECT hcpcs, SUM(total_services) as total_services
        FROM drinf.medicare_utilization
        WHERE hcpcs >= '70000' AND hcpcs < '80000'
          AND geo_level = 'National'
        GROUP BY hcpcs
    ),
    allowed_by_year AS (
        SELECT
            a.year,
            a.hcpcs,
            a.allowed_nonfacility,
            a.w_rvu,
            a.pe_rvu_nonfacility,
            a.conversion_factor,
            COALESCE(u.total_services, 0) as services
        FROM drinf.v_mpfs_allowed a
        LEFT JOIN util u ON a.hcpcs = u.hcpcs
        WHERE a.hcpcs >= '70000' AND a.hcpcs < '80000'
          AND a.locality_id = 'AL-00'
          AND a.modifier IS NULL
          AND a.status_code NOT IN ('B', 'I', 'N', 'X', 'E', 'P')
          AND a.allowed_nonfacility IS NOT NULL
    )
    SELECT
        year,
        COUNT(*) as code_count,
        SUM(services) as total_services,
        AVG(allowed_nonfacility) as simple_avg,
        SUM(allowed_nonfacility * services) / NULLIF(SUM(services), 0) as weighted_avg,
        AVG(w_rvu) as avg_work_rvu,
        AVG(pe_rvu_nonfacility) as avg_pe_rvu,
        MAX(conversion_factor) as conversion_factor,
        SUM(allowed_nonfacility * services) as total_medicare_dollars
    FROM allowed_by_year
    GROUP BY year
    ORDER BY year
    '''
    return pd.read_sql(query, conn)


@st.cache_data(ttl=3600)
def get_radiology_by_category():
    """Get trends broken down by diagnostic vs therapeutic."""
    conn = get_connection()

    query = '''
    WITH util AS (
        SELECT hcpcs, SUM(total_services) as total_services
        FROM drinf.medicare_utilization
        WHERE hcpcs >= '70000' AND hcpcs < '80000'
          AND geo_level = 'National'
        GROUP BY hcpcs
    )
    SELECT
        a.year,
        CASE
            WHEN a.hcpcs < '77000' THEN 'Diagnostic (70xxx-76xxx)'
            ELSE 'Therapeutic (77xxx-79xxx)'
        END as category,
        COUNT(*) as code_count,
        SUM(COALESCE(u.total_services, 0)) as total_services,
        AVG(a.allowed_nonfacility) as simple_avg,
        SUM(a.allowed_nonfacility * COALESCE(u.total_services, 0)) /
            NULLIF(SUM(COALESCE(u.total_services, 0)), 0) as weighted_avg
    FROM drinf.v_mpfs_allowed a
    LEFT JOIN util u ON a.hcpcs = u.hcpcs
    WHERE a.hcpcs >= '70000' AND a.hcpcs < '80000'
      AND a.locality_id = 'AL-00'
      AND a.modifier IS NULL
      AND a.status_code NOT IN ('B', 'I', 'N', 'X', 'E', 'P')
      AND a.allowed_nonfacility IS NOT NULL
    GROUP BY a.year, CASE WHEN a.hcpcs < '77000' THEN 'Diagnostic (70xxx-76xxx)' ELSE 'Therapeutic (77xxx-79xxx)' END
    ORDER BY category, a.year
    '''
    return pd.read_sql(query, conn)


@st.cache_data(ttl=3600)
def get_top_volume_codes(n=20):
    """Get top N codes by volume with 2021 vs 2026 comparison."""
    conn = get_connection()

    query = f'''
    WITH util AS (
        SELECT hcpcs, hcpcs_desc, SUM(total_services) as total_services
        FROM drinf.medicare_utilization
        WHERE hcpcs >= '70000' AND hcpcs < '80000'
          AND geo_level = 'National'
        GROUP BY hcpcs, hcpcs_desc
        ORDER BY total_services DESC
        LIMIT {n}
    ),
    r2021 AS (
        SELECT hcpcs, allowed_nonfacility as rate_2021
        FROM drinf.v_mpfs_allowed
        WHERE year = 2021 AND locality_id = 'AL-00' AND modifier IS NULL
    ),
    r2026 AS (
        SELECT hcpcs, allowed_nonfacility as rate_2026
        FROM drinf.v_mpfs_allowed
        WHERE year = 2026 AND locality_id = 'AL-00' AND modifier IS NULL
    )
    SELECT
        u.hcpcs,
        u.hcpcs_desc as description,
        u.total_services,
        r21.rate_2021,
        r26.rate_2026,
        (r26.rate_2026 - r21.rate_2021) as dollar_change,
        CASE WHEN r21.rate_2021 > 0 THEN
            ((r26.rate_2026 - r21.rate_2021) / r21.rate_2021 * 100)
        END as pct_change,
        (r26.rate_2026 - r21.rate_2021) * u.total_services as total_impact
    FROM util u
    LEFT JOIN r2021 r21 ON u.hcpcs = r21.hcpcs
    LEFT JOIN r2026 r26 ON u.hcpcs = r26.hcpcs
    ORDER BY u.total_services DESC
    '''
    return pd.read_sql(query, conn)


@st.cache_data(ttl=3600)
def get_biggest_changes(direction='decrease', n=15):
    """Get codes with biggest increases or decreases."""
    conn = get_connection()

    order = 'ASC' if direction == 'decrease' else 'DESC'

    query = f'''
    WITH util AS (
        SELECT hcpcs, SUM(total_services) as total_services
        FROM drinf.medicare_utilization
        WHERE hcpcs >= '70000' AND hcpcs < '80000'
          AND geo_level = 'National'
        GROUP BY hcpcs
    ),
    r2021 AS (
        SELECT hcpcs, allowed_nonfacility as rate_2021
        FROM drinf.v_mpfs_allowed
        WHERE year = 2021 AND locality_id = 'AL-00' AND modifier IS NULL
          AND allowed_nonfacility > 5
    ),
    r2026 AS (
        SELECT hcpcs, allowed_nonfacility as rate_2026
        FROM drinf.v_mpfs_allowed
        WHERE year = 2026 AND locality_id = 'AL-00' AND modifier IS NULL
    ),
    descriptions AS (
        SELECT DISTINCT hcpcs, description
        FROM drinf.v_rvu_clean
        WHERE year = 2026 AND modifier IS NULL
    )
    SELECT
        r21.hcpcs,
        d.description,
        COALESCE(u.total_services, 0) as total_services,
        r21.rate_2021,
        r26.rate_2026,
        (r26.rate_2026 - r21.rate_2021) as dollar_change,
        ((r26.rate_2026 - r21.rate_2021) / r21.rate_2021 * 100) as pct_change
    FROM r2021 r21
    JOIN r2026 r26 ON r21.hcpcs = r26.hcpcs
    LEFT JOIN util u ON r21.hcpcs = u.hcpcs
    LEFT JOIN descriptions d ON r21.hcpcs = d.hcpcs
    ORDER BY pct_change {order}
    LIMIT {n}
    '''
    return pd.read_sql(query, conn)


# =============================================================================
# Main Dashboard
# =============================================================================

try:
    # Load data
    summary_df = get_radiology_trend_summary()
    category_df = get_radiology_by_category()
    top_codes_df = get_top_volume_codes(20)

    # Calculate baseline comparisons
    base_year = 2021
    base_simple = summary_df[summary_df['year'] == base_year]['simple_avg'].values[0]
    base_weighted = summary_df[summary_df['year'] == base_year]['weighted_avg'].values[0]

    current_year = summary_df['year'].max()
    current_simple = summary_df[summary_df['year'] == current_year]['simple_avg'].values[0]
    current_weighted = summary_df[summary_df['year'] == current_year]['weighted_avg'].values[0]

    simple_change = (current_simple - base_simple) / base_simple * 100
    weighted_change = (current_weighted - base_weighted) / base_weighted * 100

    # ==========================================================================
    # Key Metrics
    # ==========================================================================

    st.subheader("Key Findings: 2021 → 2026")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            "Simple Average Change",
            f"{simple_change:+.1f}%",
            delta=f"{simple_change:+.1f}%",
            delta_color="inverse"
        )
    with col2:
        st.metric(
            "Utilization-Weighted Change",
            f"{weighted_change:+.1f}%",
            delta=f"{weighted_change:+.1f}%",
            delta_color="inverse"
        )
    with col3:
        # Calculate dollar impact
        base_dollars = summary_df[summary_df['year'] == base_year]['total_medicare_dollars'].values[0]
        current_dollars = summary_df[summary_df['year'] == current_year]['total_medicare_dollars'].values[0]
        dollar_change = (current_dollars - base_dollars) / 1e9
        st.metric(
            "Medicare $ Impact",
            f"${dollar_change:+.1f}B",
            delta=f"{(current_dollars/base_dollars - 1)*100:+.1f}%",
            delta_color="inverse"
        )
    with col4:
        code_count = summary_df[summary_df['year'] == current_year]['code_count'].values[0]
        st.metric("Radiology Codes", f"{code_count:,.0f}")

    st.caption("*Utilization-weighted average reflects actual Medicare billing volume - higher-volume codes have more impact*")

    st.markdown("---")

    # ==========================================================================
    # Trend Charts
    # ==========================================================================

    col_chart1, col_chart2 = st.columns(2)

    with col_chart1:
        st.subheader("Reimbursement Trend")

        # Prepare data for chart
        chart_data = summary_df[['year', 'simple_avg', 'weighted_avg']].melt(
            id_vars=['year'],
            value_vars=['simple_avg', 'weighted_avg'],
            var_name='metric',
            value_name='amount'
        )
        chart_data['metric'] = chart_data['metric'].map({
            'simple_avg': 'Simple Average',
            'weighted_avg': 'Utilization-Weighted'
        })

        line_chart = alt.Chart(chart_data).mark_line(point=True).encode(
            x=alt.X('year:O', title='Year'),
            y=alt.Y('amount:Q', title='Average Allowed ($)', scale=alt.Scale(zero=False)),
            color=alt.Color('metric:N', title='Metric',
                          scale=alt.Scale(range=[COLORS['neutral'], COLORS['primary']])),
            strokeWidth=alt.value(3)
        ).properties(height=300)

        st.altair_chart(line_chart, use_container_width=True)

    with col_chart2:
        st.subheader("Year-over-Year Change")

        summary_df['yoy_change'] = summary_df['weighted_avg'].pct_change() * 100
        yoy_data = summary_df[summary_df['year'] > base_year].copy()
        yoy_data['color'] = yoy_data['yoy_change'].apply(lambda x: 'Increase' if x > 0 else 'Decrease')

        bar_chart = alt.Chart(yoy_data).mark_bar().encode(
            x=alt.X('year:O', title='Year'),
            y=alt.Y('yoy_change:Q', title='YoY Change (%)'),
            color=alt.Color('color:N',
                          scale=alt.Scale(domain=['Increase', 'Decrease'],
                                        range=[COLORS['positive'], COLORS['negative']]),
                          legend=None)
        ).properties(height=300)

        st.altair_chart(bar_chart, use_container_width=True)

    st.markdown("---")

    # ==========================================================================
    # Category Breakdown
    # ==========================================================================

    st.subheader("Diagnostic vs Therapeutic Radiology")

    col_diag, col_ther = st.columns(2)

    for col, cat_name in [(col_diag, 'Diagnostic (70xxx-76xxx)'), (col_ther, 'Therapeutic (77xxx-79xxx)')]:
        with col:
            cat_data = category_df[category_df['category'] == cat_name].copy()

            if len(cat_data) > 0:
                cat_base = cat_data[cat_data['year'] == base_year]['weighted_avg'].values[0]
                cat_current = cat_data[cat_data['year'] == current_year]['weighted_avg'].values[0]
                cat_change = (cat_current - cat_base) / cat_base * 100

                st.markdown(f"**{cat_name.split(' ')[0]}**")

                m1, m2 = st.columns(2)
                with m1:
                    st.metric("2021 Wtd Avg", format_currency(cat_base))
                with m2:
                    st.metric("2026 Wtd Avg", format_currency(cat_current),
                             delta=f"{cat_change:+.1f}%", delta_color="inverse")

                # Mini trend
                cat_chart = alt.Chart(cat_data).mark_area(
                    opacity=0.3, line=True
                ).encode(
                    x=alt.X('year:O', title=None),
                    y=alt.Y('weighted_avg:Q', title='Wtd Avg ($)', scale=alt.Scale(zero=False)),
                    color=alt.value(COLORS['primary'])
                ).properties(height=150)

                st.altair_chart(cat_chart, use_container_width=True)

    st.markdown("---")

    # ==========================================================================
    # Top Volume Codes Impact
    # ==========================================================================

    st.subheader("High-Volume Code Impact")
    st.caption("Top 20 radiology codes by Medicare utilization")

    if len(top_codes_df) > 0:
        # Summary metrics
        total_impact = top_codes_df['total_impact'].sum()
        codes_decreased = (top_codes_df['pct_change'] < 0).sum()

        m1, m2, m3 = st.columns(3)
        with m1:
            st.metric("Total Dollar Impact (Top 20)", f"${total_impact/1e6:,.0f}M")
        with m2:
            st.metric("Codes with Decreases", f"{codes_decreased} of 20")
        with m3:
            avg_change = top_codes_df['pct_change'].mean()
            st.metric("Average % Change", f"{avg_change:+.1f}%")

        # Impact chart
        top_codes_df['impact_color'] = top_codes_df['total_impact'].apply(
            lambda x: 'Positive' if x > 0 else 'Negative'
        )
        top_codes_df['short_desc'] = top_codes_df['description'].str[:30]

        impact_chart = alt.Chart(top_codes_df.head(15)).mark_bar().encode(
            y=alt.Y('hcpcs:N', title='CPT Code', sort=alt.EncodingSortField(field='total_impact', order='ascending')),
            x=alt.X('total_impact:Q', title='Dollar Impact ($)'),
            color=alt.Color('impact_color:N',
                          scale=alt.Scale(domain=['Positive', 'Negative'],
                                        range=[COLORS['positive'], COLORS['negative']]),
                          legend=None),
            tooltip=[
                alt.Tooltip('hcpcs:N', title='CPT'),
                alt.Tooltip('description:N', title='Description'),
                alt.Tooltip('total_services:Q', title='Services', format=',.0f'),
                alt.Tooltip('rate_2021:Q', title='2021 Rate', format='$.2f'),
                alt.Tooltip('rate_2026:Q', title='2026 Rate', format='$.2f'),
                alt.Tooltip('pct_change:Q', title='% Change', format='+.1f'),
                alt.Tooltip('total_impact:Q', title='Total Impact', format='$,.0f')
            ]
        ).properties(height=400)

        st.altair_chart(impact_chart, use_container_width=True)

        # Data table
        with st.expander("View Full Table"):
            display_df = top_codes_df[['hcpcs', 'description', 'total_services',
                                       'rate_2021', 'rate_2026', 'pct_change', 'total_impact']].copy()
            display_df.columns = ['CPT', 'Description', 'Services', '2021 Rate', '2026 Rate', '% Change', 'Total Impact']
            display_df['Services'] = display_df['Services'].apply(lambda x: f"{x/1e6:.1f}M")
            display_df['2021 Rate'] = display_df['2021 Rate'].apply(lambda x: f"${x:.2f}" if pd.notna(x) else '-')
            display_df['2026 Rate'] = display_df['2026 Rate'].apply(lambda x: f"${x:.2f}" if pd.notna(x) else '-')
            display_df['% Change'] = display_df['% Change'].apply(lambda x: f"{x:+.1f}%" if pd.notna(x) else '-')
            display_df['Total Impact'] = display_df['Total Impact'].apply(lambda x: f"${x/1e6:.1f}M" if pd.notna(x) else '-')
            st.dataframe(display_df, hide_index=True, use_container_width=True)

    st.markdown("---")

    # ==========================================================================
    # Biggest Winners & Losers
    # ==========================================================================

    st.subheader("Biggest Changes (2021 → 2026)")

    col_losers, col_winners = st.columns(2)

    with col_losers:
        st.markdown("**Biggest Decreases**")
        losers = get_biggest_changes('decrease', 10)
        if len(losers) > 0:
            for _, row in losers.iterrows():
                desc = row['description'][:35] if row['description'] else row['hcpcs']
                st.markdown(f"- **{row['hcpcs']}** {desc}: {row['pct_change']:+.1f}%")

    with col_winners:
        st.markdown("**Biggest Increases**")
        winners = get_biggest_changes('increase', 10)
        if len(winners) > 0:
            for _, row in winners.iterrows():
                desc = row['description'][:35] if row['description'] else row['hcpcs']
                st.markdown(f"- **{row['hcpcs']}** {desc}: {row['pct_change']:+.1f}%")

    st.markdown("---")

    # ==========================================================================
    # Drivers Analysis
    # ==========================================================================

    st.subheader("What's Driving the Changes?")

    # CF trend
    cf_2021 = summary_df[summary_df['year'] == 2021]['conversion_factor'].values[0]
    cf_2026 = summary_df[summary_df['year'] == 2026]['conversion_factor'].values[0]
    cf_change = (cf_2026 - cf_2021) / cf_2021 * 100

    # RVU trend
    rvu_2021 = summary_df[summary_df['year'] == 2021]['avg_work_rvu'].values[0]
    rvu_2026 = summary_df[summary_df['year'] == 2026]['avg_work_rvu'].values[0]
    rvu_change = (rvu_2026 - rvu_2021) / rvu_2021 * 100

    pe_2021 = summary_df[summary_df['year'] == 2021]['avg_pe_rvu'].values[0]
    pe_2026 = summary_df[summary_df['year'] == 2026]['avg_pe_rvu'].values[0]
    pe_change = (pe_2026 - pe_2021) / pe_2021 * 100

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("Conversion Factor", f"${cf_2026:.4f}",
                 delta=f"{cf_change:+.1f}% vs 2021", delta_color="normal")
        st.caption(f"2021: ${cf_2021:.4f}")

    with col2:
        st.metric("Avg Work RVU", f"{rvu_2026:.3f}",
                 delta=f"{rvu_change:+.1f}% vs 2021", delta_color="inverse")
        st.caption(f"2021: {rvu_2021:.3f}")

    with col3:
        st.metric("Avg PE RVU", f"{pe_2026:.3f}",
                 delta=f"{pe_change:+.1f}% vs 2021", delta_color="inverse")
        st.caption(f"2021: {pe_2021:.3f}")

    st.info("""
    **Key Insight:** While the Conversion Factor increased in 2026 (+3.3% from 2025),
    it's still below 2021 levels. Combined with RVU reductions, radiology remains
    approximately 9% below 2021 reimbursement on a utilization-weighted basis.
    """)

except Exception as e:
    st.error(f"Error loading data: {e}")
    st.info("Ensure the database is running and contains radiology data.")
    import traceback
    st.code(traceback.format_exc())

# =============================================================================
# Footer
# =============================================================================

st.markdown("---")
st.caption("Analysis based on Medicare Physician Fee Schedule (MPFS) data, locality AL-00 (national baseline)")
st.caption("Utilization data: CMS Medicare Physician & Other Practitioners Public Use File (2023, National)")
