"""
Page 1: Medicare Baseline Monitor
Executive overview of MPFS trends and significant YoY changes
"""
import streamlit as st
import pandas as pd
import altair as alt
from utils import (
    get_available_years,
    get_conversion_factors,
    get_localities,
    get_summary_stats,
    get_top_movers,
    get_payment_change_distribution,
    get_codes_with_cuts,
    COLORS,
    format_currency,
    format_percent,
    get_change_color
)

st.set_page_config(page_title="Baseline Monitor", page_icon="$", layout="wide")

st.title("Medicare Baseline Monitor")
st.caption("Overview of MPFS reimbursement trends and top movers")

# Sidebar controls
st.sidebar.header("Filters")

try:
    years = get_available_years()
    localities = get_localities()

    # Year selector
    selected_year = st.sidebar.selectbox(
        "Year",
        options=sorted(years, reverse=True),
        index=0
    )

    # Locality selector for comparison
    locality_options = localities['locality_id'].tolist()
    locality_names = dict(zip(localities['locality_id'], localities['locality_name']))

    selected_locality = st.sidebar.selectbox(
        "Reference Locality",
        options=locality_options,
        index=locality_options.index('AL-00') if 'AL-00' in locality_options else 0,
        format_func=lambda x: f"{locality_names.get(x, x)} ({x})"
    )

    # Setting toggle
    setting = st.sidebar.radio(
        "Payment Setting",
        options=['nonfacility', 'facility'],
        format_func=lambda x: x.replace('nonfacility', 'Non-Facility').replace('facility', 'Facility')
    )

    # Payable codes filter
    payable_only = st.sidebar.checkbox("Payable Codes Only", value=True)

    st.sidebar.markdown("---")
    st.sidebar.caption(f"Reference: {locality_names.get(selected_locality, selected_locality)}")

    # =========================================================================
    # KPI Cards
    # =========================================================================
    cf_data = get_conversion_factors()
    current_cf = cf_data[cf_data['year'] == selected_year]['conversion_factor'].values[0]

    prior_year = selected_year - 1
    prior_cf_data = cf_data[cf_data['year'] == prior_year]
    cf_yoy_change = None
    if len(prior_cf_data) > 0:
        prior_cf = prior_cf_data['conversion_factor'].values[0]
        cf_yoy_change = ((current_cf - prior_cf) / prior_cf) * 100

    stats = get_summary_stats(selected_year, payable_only)
    codes_with_cuts = get_codes_with_cuts(selected_year, selected_locality, setting, payable_only)

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            "Conversion Factor",
            format_currency(current_cf, 4),
            f"{cf_yoy_change:+.2f}%" if cf_yoy_change else None
        )

    with col2:
        if cf_yoy_change:
            delta_color = "normal" if cf_yoy_change >= 0 else "inverse"
            st.metric("CF YoY Change", f"{cf_yoy_change:+.2f}%")
        else:
            st.metric("CF YoY Change", "N/A")

    with col3:
        st.metric("Payable Codes", f"{stats['total_codes']:,}")

    with col4:
        st.metric("Codes with Payment Cut", f"{codes_with_cuts:,}")

    st.divider()

    # =========================================================================
    # Conversion Factor Trend
    # =========================================================================
    st.subheader("Conversion Factor Trend")

    cf_chart = alt.Chart(cf_data).mark_line(point=True, color=COLORS['accent']).encode(
        x=alt.X('year:O', title='Year'),
        y=alt.Y('conversion_factor:Q', title='Conversion Factor ($)',
                scale=alt.Scale(zero=False)),
        tooltip=[
            alt.Tooltip('year:O', title='Year'),
            alt.Tooltip('conversion_factor:Q', title='CF', format='$.4f')
        ]
    ).properties(height=250)

    # Add current year highlight
    current_point = alt.Chart(cf_data[cf_data['year'] == selected_year]).mark_point(
        color=COLORS['positive'], size=150, filled=True
    ).encode(
        x='year:O',
        y='conversion_factor:Q'
    )

    st.altair_chart(cf_chart + current_point, use_container_width=True)

    st.divider()

    # =========================================================================
    # Top Movers Tables
    # =========================================================================
    col_inc, col_dec = st.columns(2)

    with col_inc:
        st.subheader("Top Payment Increases")
        increases = get_top_movers(selected_year, selected_locality, n=15,
                                   direction='increase', setting=setting, payable_only=payable_only)

        if len(increases) > 0:
            display_df = increases[['hcpcs', 'modifier', 'description', 'prior_year',
                                    'current_year', 'change', 'pct_change']].copy()
            display_df.columns = ['CPT', 'Mod', 'Description', 'Prior $', 'Current $', '$ Chg', '% Chg']

            # Format columns
            display_df['Prior $'] = display_df['Prior $'].apply(lambda x: format_currency(x))
            display_df['Current $'] = display_df['Current $'].apply(lambda x: format_currency(x))
            display_df['$ Chg'] = display_df['$ Chg'].apply(lambda x: format_currency(x))
            display_df['% Chg'] = display_df['% Chg'].apply(lambda x: format_percent(x))

            st.dataframe(display_df, use_container_width=True, hide_index=True)
        else:
            st.info("No data available for selected filters")

    with col_dec:
        st.subheader("Top Payment Decreases")
        decreases = get_top_movers(selected_year, selected_locality, n=15,
                                   direction='decrease', setting=setting, payable_only=payable_only)

        if len(decreases) > 0:
            display_df = decreases[['hcpcs', 'modifier', 'description', 'prior_year',
                                    'current_year', 'change', 'pct_change']].copy()
            display_df.columns = ['CPT', 'Mod', 'Description', 'Prior $', 'Current $', '$ Chg', '% Chg']

            display_df['Prior $'] = display_df['Prior $'].apply(lambda x: format_currency(x))
            display_df['Current $'] = display_df['Current $'].apply(lambda x: format_currency(x))
            display_df['$ Chg'] = display_df['$ Chg'].apply(lambda x: format_currency(x))
            display_df['% Chg'] = display_df['% Chg'].apply(lambda x: format_percent(x))

            st.dataframe(display_df, use_container_width=True, hide_index=True)
        else:
            st.info("No data available for selected filters")

    st.divider()

    # =========================================================================
    # Payment Change Distribution
    # =========================================================================
    st.subheader("Payment Change Distribution")

    dist_data = get_payment_change_distribution(selected_year, selected_locality, setting, payable_only)

    if len(dist_data) > 0:
        # Create histogram
        hist = alt.Chart(dist_data).mark_bar().encode(
            x=alt.X('pct_change:Q', bin=alt.Bin(maxbins=40), title='% Change'),
            y=alt.Y('count()', title='Number of Codes'),
            color=alt.condition(
                alt.datum.pct_change >= 0,
                alt.value(COLORS['positive']),
                alt.value(COLORS['negative'])
            ),
            tooltip=[
                alt.Tooltip('pct_change:Q', bin=True, title='% Change Range'),
                alt.Tooltip('count()', title='Code Count')
            ]
        ).properties(height=300)

        # Add reference line at 0
        rule = alt.Chart(pd.DataFrame({'x': [0]})).mark_rule(
            color=COLORS['neutral'], strokeDash=[5, 5], strokeWidth=2
        ).encode(x='x:Q')

        st.altair_chart(hist + rule, use_container_width=True)

        # Summary stats
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Median Change", f"{dist_data['pct_change'].median():+.1f}%")
        with col2:
            positive_pct = (dist_data['pct_change'] > 0).mean() * 100
            st.metric("% Codes Increased", f"{positive_pct:.1f}%")
        with col3:
            negative_pct = (dist_data['pct_change'] < 0).mean() * 100
            st.metric("% Codes Decreased", f"{negative_pct:.1f}%")
    else:
        st.info("No distribution data available for selected filters")

except Exception as e:
    st.error(f"Error loading data: {e}")
    st.info("Ensure the database is running and analytics views are created.")
