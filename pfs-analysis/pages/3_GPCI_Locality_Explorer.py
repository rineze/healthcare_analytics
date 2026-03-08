"""
Page 3: GPCI Locality Explorer
Analyze geographic payment adjustments and identify localities with significant GPCI changes
"""
import streamlit as st
import pandas as pd
import altair as alt
from utils import (
    get_available_years,
    get_localities,
    get_gpci_rankings,
    get_gpci_yoy_changes,
    get_gpci_trend,
    COLORS,
    format_percent
)

st.set_page_config(page_title="GPCI Locality Explorer", page_icon="$", layout="wide")

st.title("GPCI Locality Explorer")
st.caption("Geographic Practice Cost Index analysis by locality")

try:
    years = get_available_years()
    latest_year = max(years)
    localities = get_localities()

    # Sidebar controls
    st.sidebar.header("Filters")

    selected_year = st.sidebar.selectbox(
        "Year",
        options=sorted(years, reverse=True),
        index=0
    )

    st.sidebar.markdown("---")

    # Locality for trend chart
    st.sidebar.header("Trend Analysis")
    locality_options = localities['locality_id'].tolist()
    locality_names = dict(zip(localities['locality_id'], localities['locality_name']))

    default_idx = locality_options.index('CA-18') if 'CA-18' in locality_options else 0

    trend_locality = st.sidebar.selectbox(
        "Locality for Trend",
        options=locality_options,
        index=default_idx,
        format_func=lambda x: f"{locality_names.get(x, x)} ({x})"
    )

    # =========================================================================
    # GPCI Rank Table
    # =========================================================================
    st.subheader(f"GPCI Rankings ({selected_year})")

    rankings = get_gpci_rankings(selected_year)

    if len(rankings) > 0:
        display_df = rankings[['locality_name', 'state', 'gpci_work', 'gpci_pe',
                               'gpci_mp', 'gpci_composite']].copy()
        display_df.columns = ['Locality', 'State', 'Work GPCI', 'PE GPCI', 'MP GPCI', 'Composite']

        # Format GPCI columns
        for col in ['Work GPCI', 'PE GPCI', 'MP GPCI', 'Composite']:
            display_df[col] = display_df[col].apply(lambda x: f"{x:.4f}" if pd.notna(x) else "-")

        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            height=400
        )

        # Summary stats
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Highest Composite", f"{rankings['gpci_composite'].max():.4f}",
                     help=rankings[rankings['gpci_composite'] == rankings['gpci_composite'].max()]['locality_name'].values[0])
        with col2:
            st.metric("Lowest Composite", f"{rankings['gpci_composite'].min():.4f}",
                     help=rankings[rankings['gpci_composite'] == rankings['gpci_composite'].min()]['locality_name'].values[0])
        with col3:
            st.metric("Localities", f"{len(rankings)}")
    else:
        st.info("No GPCI data available for selected year")

    st.divider()

    # =========================================================================
    # Largest GPCI YoY Changes
    # =========================================================================
    st.subheader("Largest GPCI Changes (Year-over-Year)")

    tab_work, tab_pe, tab_mp = st.tabs(["Work GPCI", "PE GPCI", "MP GPCI"])

    with tab_work:
        work_changes = get_gpci_yoy_changes(selected_year, 'work', n=15)
        if len(work_changes) > 0:
            display_df = work_changes[['locality_name', 'state', 'prior_value',
                                       'current_value', 'change', 'pct_change']].copy()
            display_df.columns = ['Locality', 'State', 'Prior', 'Current', 'Change', '% Change']
            display_df['Prior'] = display_df['Prior'].apply(lambda x: f"{x:.4f}" if pd.notna(x) else "-")
            display_df['Current'] = display_df['Current'].apply(lambda x: f"{x:.4f}" if pd.notna(x) else "-")
            display_df['Change'] = display_df['Change'].apply(lambda x: f"{x:+.4f}" if pd.notna(x) else "-")
            display_df['% Change'] = display_df['% Change'].apply(lambda x: format_percent(x))
            st.dataframe(display_df, use_container_width=True, hide_index=True)
        else:
            st.info("No YoY change data available")

    with tab_pe:
        pe_changes = get_gpci_yoy_changes(selected_year, 'pe', n=15)
        if len(pe_changes) > 0:
            display_df = pe_changes[['locality_name', 'state', 'prior_value',
                                     'current_value', 'change', 'pct_change']].copy()
            display_df.columns = ['Locality', 'State', 'Prior', 'Current', 'Change', '% Change']
            display_df['Prior'] = display_df['Prior'].apply(lambda x: f"{x:.4f}" if pd.notna(x) else "-")
            display_df['Current'] = display_df['Current'].apply(lambda x: f"{x:.4f}" if pd.notna(x) else "-")
            display_df['Change'] = display_df['Change'].apply(lambda x: f"{x:+.4f}" if pd.notna(x) else "-")
            display_df['% Change'] = display_df['% Change'].apply(lambda x: format_percent(x))
            st.dataframe(display_df, use_container_width=True, hide_index=True)
        else:
            st.info("No YoY change data available")

    with tab_mp:
        mp_changes = get_gpci_yoy_changes(selected_year, 'mp', n=15)
        if len(mp_changes) > 0:
            display_df = mp_changes[['locality_name', 'state', 'prior_value',
                                     'current_value', 'change', 'pct_change']].copy()
            display_df.columns = ['Locality', 'State', 'Prior', 'Current', 'Change', '% Change']
            display_df['Prior'] = display_df['Prior'].apply(lambda x: f"{x:.4f}" if pd.notna(x) else "-")
            display_df['Current'] = display_df['Current'].apply(lambda x: f"{x:.4f}" if pd.notna(x) else "-")
            display_df['Change'] = display_df['Change'].apply(lambda x: f"{x:+.4f}" if pd.notna(x) else "-")
            display_df['% Change'] = display_df['% Change'].apply(lambda x: format_percent(x))
            st.dataframe(display_df, use_container_width=True, hide_index=True)
        else:
            st.info("No YoY change data available")

    st.divider()

    # =========================================================================
    # GPCI Component Trends
    # =========================================================================
    st.subheader(f"GPCI Trends: {locality_names.get(trend_locality, trend_locality)}")

    trend_data = get_gpci_trend(trend_locality)

    if len(trend_data) > 0:
        # Melt for multi-line chart
        trend_melted = trend_data.melt(
            id_vars=['year'],
            value_vars=['gpci_work', 'gpci_pe', 'gpci_mp'],
            var_name='component',
            value_name='gpci_value'
        )
        trend_melted['component'] = trend_melted['component'].map({
            'gpci_work': 'Work GPCI',
            'gpci_pe': 'PE GPCI',
            'gpci_mp': 'MP GPCI'
        })

        trend_chart = alt.Chart(trend_melted).mark_line(point=True).encode(
            x=alt.X('year:O', title='Year'),
            y=alt.Y('gpci_value:Q', title='GPCI Value',
                   scale=alt.Scale(domain=[0.5, 1.5])),
            color=alt.Color('component:N', title='Component',
                           scale=alt.Scale(
                               domain=['Work GPCI', 'PE GPCI', 'MP GPCI'],
                               range=[COLORS['accent'], COLORS['positive'], COLORS['negative']]
                           )),
            tooltip=[
                alt.Tooltip('year:O', title='Year'),
                alt.Tooltip('component:N', title='Component'),
                alt.Tooltip('gpci_value:Q', title='GPCI', format='.4f')
            ]
        ).properties(height=350)

        # Reference line at 1.0
        ref_line = alt.Chart(pd.DataFrame({'y': [1.0]})).mark_rule(
            color=COLORS['neutral'], strokeDash=[5, 5], strokeWidth=1
        ).encode(y='y:Q')

        st.altair_chart(trend_chart + ref_line, use_container_width=True)

        # Current year values
        latest_trend = trend_data[trend_data['year'] == trend_data['year'].max()].iloc[0]
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Work GPCI", f"{latest_trend['gpci_work']:.4f}")
        with col2:
            st.metric("PE GPCI", f"{latest_trend['gpci_pe']:.4f}")
        with col3:
            st.metric("MP GPCI", f"{latest_trend['gpci_mp']:.4f}")
    else:
        st.info("No trend data available for selected locality")

except Exception as e:
    st.error(f"Error loading data: {e}")
    st.info("Ensure the database is running and analytics views are created.")
