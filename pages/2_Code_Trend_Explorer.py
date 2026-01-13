"""
Page 2: Code Trend Explorer
Deep-dive into a specific code's reimbursement history and geographic comparison
"""
import streamlit as st
import pandas as pd
import altair as alt
from utils import (
    get_available_years,
    get_code_list,
    get_localities,
    get_code_trend,
    get_code_yoy_detail,
    get_locality_comparison,
    COLORS,
    format_currency,
    format_percent
)

st.set_page_config(page_title="Code Trend Explorer", page_icon="$", layout="wide")

st.title("Code Trend Explorer")
st.caption("Analyze specific code reimbursement across time and localities")

try:
    years = get_available_years()
    latest_year = max(years)
    localities = get_localities()

    # Sidebar controls
    st.sidebar.header("Code Selection")

    # Code search
    codes_df = get_code_list(year=latest_year, payable_only=True)
    code_options = codes_df['hcpcs_mod'].tolist()
    code_descriptions = dict(zip(codes_df['hcpcs_mod'], codes_df['description']))

    # Default to 70553 if available
    default_idx = code_options.index('70553') if '70553' in code_options else 0

    selected_code = st.sidebar.selectbox(
        "Select Code",
        options=code_options,
        index=default_idx,
        format_func=lambda x: f"{x} - {code_descriptions.get(x, '')[:40]}"
    )

    st.sidebar.header("Locality Comparison")

    # Locality multi-select
    locality_options = localities['locality_id'].tolist()
    locality_names = dict(zip(localities['locality_id'], localities['locality_name']))

    # Default localities
    default_localities = []
    for loc in ['CA-18', 'NY-01', 'AL-00']:
        if loc in locality_options:
            default_localities.append(loc)

    selected_localities = st.sidebar.multiselect(
        "Compare Localities (max 5)",
        options=locality_options,
        default=default_localities[:3],
        max_selections=5,
        format_func=lambda x: f"{locality_names.get(x, x)} ({x})"
    )

    # Setting toggle
    setting = st.sidebar.radio(
        "Payment Setting",
        options=['nonfacility', 'facility'],
        format_func=lambda x: x.replace('nonfacility', 'Non-Facility').replace('facility', 'Facility')
    )

    st.sidebar.markdown("---")
    st.sidebar.caption(f"Selected: {selected_code}")
    st.sidebar.caption(code_descriptions.get(selected_code, ''))

    # =========================================================================
    # Code Header
    # =========================================================================
    st.subheader(f"{selected_code}")
    st.caption(code_descriptions.get(selected_code, 'No description available'))

    if not selected_localities:
        st.warning("Please select at least one locality for comparison")
        st.stop()

    # =========================================================================
    # Allowed Amount Trend Chart
    # =========================================================================
    st.subheader("Allowed Amount Trend")

    trend_data = get_code_trend(selected_code, selected_localities, setting)

    if len(trend_data) > 0:
        # Multi-line chart
        trend_chart = alt.Chart(trend_data).mark_line(point=True).encode(
            x=alt.X('year:O', title='Year'),
            y=alt.Y('allowed:Q', title=f'Allowed Amount ({setting.title()})'),
            color=alt.Color('locality_name:N', title='Locality',
                           scale=alt.Scale(scheme='tableau10')),
            tooltip=[
                alt.Tooltip('locality_name:N', title='Locality'),
                alt.Tooltip('year:O', title='Year'),
                alt.Tooltip('allowed:Q', title='Allowed', format='$.2f'),
                alt.Tooltip('w_rvu:Q', title='Work RVU', format='.2f'),
                alt.Tooltip('conversion_factor:Q', title='CF', format='$.4f')
            ]
        ).properties(height=350)

        st.altair_chart(trend_chart, use_container_width=True)
    else:
        st.info("No trend data available for selected code and localities")

    st.divider()

    # =========================================================================
    # YoY Detail Table
    # =========================================================================
    st.subheader("Year-over-Year Detail")

    yoy_data = get_code_yoy_detail(selected_code, selected_localities, setting)

    if len(yoy_data) > 0:
        display_df = yoy_data[['year', 'locality_name', 'current_allowed', 'prior_allowed',
                               'change', 'pct_change', 'w_rvu', 'conversion_factor']].copy()
        display_df.columns = ['Year', 'Locality', 'Current $', 'Prior $', '$ Chg', '% Chg', 'Work RVU', 'CF']

        display_df['Current $'] = display_df['Current $'].apply(lambda x: format_currency(x))
        display_df['Prior $'] = display_df['Prior $'].apply(lambda x: format_currency(x))
        display_df['$ Chg'] = display_df['$ Chg'].apply(lambda x: format_currency(x))
        display_df['% Chg'] = display_df['% Chg'].apply(lambda x: format_percent(x))
        display_df['CF'] = display_df['CF'].apply(lambda x: format_currency(x, 4))

        st.dataframe(display_df, use_container_width=True, hide_index=True)

        # Download button
        csv = yoy_data.to_csv(index=False)
        st.download_button(
            label="Download YoY Data (CSV)",
            data=csv,
            file_name=f"{selected_code}_yoy_detail.csv",
            mime="text/csv"
        )
    else:
        st.info("No YoY data available")

    st.divider()

    # =========================================================================
    # Locality Comparison Bar Chart (Latest Year)
    # =========================================================================
    st.subheader(f"Locality Comparison ({latest_year})")

    comparison_data = get_locality_comparison(selected_code, latest_year, setting, top_n=25)

    if len(comparison_data) > 0:
        # Calculate national average
        avg_allowed = comparison_data['allowed'].mean()

        # Highlight selected localities
        comparison_data['is_selected'] = comparison_data['locality_id'].isin(selected_localities)

        bar_chart = alt.Chart(comparison_data).mark_bar().encode(
            y=alt.Y('locality_name:N', title='Locality', sort='-x'),
            x=alt.X('allowed:Q', title=f'Allowed Amount ({setting.title()})'),
            color=alt.condition(
                alt.datum.is_selected,
                alt.value(COLORS['accent']),
                alt.value(COLORS['neutral_light'])
            ),
            tooltip=[
                alt.Tooltip('locality_name:N', title='Locality'),
                alt.Tooltip('allowed:Q', title='Allowed', format='$.2f'),
                alt.Tooltip('gpci_work:Q', title='GPCI Work', format='.4f'),
                alt.Tooltip('gpci_pe:Q', title='GPCI PE', format='.4f'),
                alt.Tooltip('gpci_mp:Q', title='GPCI MP', format='.4f')
            ]
        ).properties(height=500)

        # Add average reference line
        avg_line = alt.Chart(pd.DataFrame({'x': [avg_allowed]})).mark_rule(
            color=COLORS['negative'], strokeDash=[5, 5], strokeWidth=2
        ).encode(x='x:Q')

        avg_label = alt.Chart(pd.DataFrame({
            'x': [avg_allowed],
            'y': [comparison_data['locality_name'].iloc[-1]],
            'label': [f'Avg: {format_currency(avg_allowed)}']
        })).mark_text(
            align='left', dx=5, color=COLORS['negative']
        ).encode(
            x='x:Q',
            y='y:N',
            text='label:N'
        )

        st.altair_chart(bar_chart + avg_line, use_container_width=True)

        # Stats row
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Highest", format_currency(comparison_data['allowed'].max()))
        with col2:
            st.metric("Lowest", format_currency(comparison_data['allowed'].min()))
        with col3:
            st.metric("Average", format_currency(avg_allowed))
        with col4:
            spread = comparison_data['allowed'].max() - comparison_data['allowed'].min()
            st.metric("Spread", format_currency(spread))
    else:
        st.info("No comparison data available")

except Exception as e:
    st.error(f"Error loading data: {e}")
    st.info("Ensure the database is running and analytics views are created.")
