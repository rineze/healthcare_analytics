"""
Page 4: Locality Spread
Quantify geographic payment variation for specific codes
"""
import streamlit as st
import pandas as pd
import altair as alt
from utils import (
    get_available_years,
    get_code_list,
    get_locality_spread,
    get_spread_stats,
    COLORS,
    format_currency
)

st.set_page_config(page_title="Locality Spread", page_icon="$", layout="wide")

st.title("Locality Spread Analysis")
st.caption("Analyze geographic payment variation for specific codes")

try:
    years = get_available_years()
    latest_year = max(years)

    # Sidebar controls
    st.sidebar.header("Code Selection")

    codes_df = get_code_list(year=latest_year, payable_only=True)
    code_options = codes_df['hcpcs_mod'].tolist()
    code_descriptions = dict(zip(codes_df['hcpcs_mod'], codes_df['description']))

    default_idx = code_options.index('70553') if '70553' in code_options else 0

    selected_code = st.sidebar.selectbox(
        "Select Code",
        options=code_options,
        index=default_idx,
        format_func=lambda x: f"{x} - {code_descriptions.get(x, '')[:40]}"
    )

    st.sidebar.header("Filters")

    selected_year = st.sidebar.selectbox(
        "Year",
        options=sorted(years, reverse=True),
        index=0
    )

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

    # =========================================================================
    # Spread KPI Cards
    # =========================================================================
    stats = get_spread_stats(selected_code, selected_year, setting)

    if stats is not None and pd.notna(stats['max_allowed']):
        spread = stats['max_allowed'] - stats['min_allowed']
        ratio = stats['max_allowed'] / stats['min_allowed'] if stats['min_allowed'] > 0 else None
        cv = (stats['std_dev'] / stats['avg_allowed'] * 100) if stats['avg_allowed'] > 0 else None

        col1, col2, col3 = st.columns(3)
        col4, col5, col6 = st.columns(3)

        with col1:
            st.metric("Maximum", format_currency(stats['max_allowed']))
        with col2:
            st.metric("Minimum", format_currency(stats['min_allowed']))
        with col3:
            st.metric("Spread (Max - Min)", format_currency(spread))
        with col4:
            st.metric("Max/Min Ratio", f"{ratio:.2f}x" if ratio else "-")
        with col5:
            st.metric("Std Deviation", format_currency(stats['std_dev']))
        with col6:
            st.metric("Coefficient of Variation", f"{cv:.1f}%" if cv else "-")

        st.divider()

        # =====================================================================
        # Locality Payment Bar Chart
        # =====================================================================
        st.subheader(f"Payment by Locality ({selected_year})")

        spread_data = get_locality_spread(selected_code, selected_year, setting)

        if len(spread_data) > 0:
            avg_allowed = spread_data['allowed'].mean()
            median_allowed = spread_data['allowed'].median()

            bar_chart = alt.Chart(spread_data).mark_bar().encode(
                y=alt.Y('locality_name:N', title='Locality', sort='-x'),
                x=alt.X('allowed:Q', title=f'Allowed Amount ({setting.title()})'),
                color=alt.Color('allowed:Q',
                               scale=alt.Scale(scheme='blues'),
                               legend=None),
                tooltip=[
                    alt.Tooltip('locality_name:N', title='Locality'),
                    alt.Tooltip('state:N', title='State'),
                    alt.Tooltip('allowed:Q', title='Allowed', format='$.2f'),
                    alt.Tooltip('gpci_work:Q', title='GPCI Work', format='.4f'),
                    alt.Tooltip('gpci_pe:Q', title='GPCI PE', format='.4f'),
                    alt.Tooltip('gpci_mp:Q', title='GPCI MP', format='.4f')
                ]
            ).properties(height=max(400, len(spread_data) * 8))

            # Reference lines
            avg_line = alt.Chart(pd.DataFrame({'x': [avg_allowed]})).mark_rule(
                color=COLORS['accent'], strokeWidth=2
            ).encode(x='x:Q')

            median_line = alt.Chart(pd.DataFrame({'x': [median_allowed]})).mark_rule(
                color=COLORS['positive'], strokeDash=[5, 5], strokeWidth=2
            ).encode(x='x:Q')

            min_line = alt.Chart(pd.DataFrame({'x': [stats['min_allowed']]})).mark_rule(
                color=COLORS['negative'], strokeDash=[2, 2], strokeWidth=1
            ).encode(x='x:Q')

            max_line = alt.Chart(pd.DataFrame({'x': [stats['max_allowed']]})).mark_rule(
                color=COLORS['negative'], strokeDash=[2, 2], strokeWidth=1
            ).encode(x='x:Q')

            st.altair_chart(bar_chart + avg_line + median_line, use_container_width=True)

            # Legend
            st.caption(f"Blue line: Mean ({format_currency(avg_allowed)}) | Green dashed: Median ({format_currency(median_allowed)})")

            st.divider()

            # =================================================================
            # Box Plot / Distribution
            # =================================================================
            st.subheader("Payment Distribution")

            # Create box plot data
            percentiles = {
                'min': spread_data['allowed'].min(),
                'q1': spread_data['allowed'].quantile(0.25),
                'median': spread_data['allowed'].median(),
                'q3': spread_data['allowed'].quantile(0.75),
                'max': spread_data['allowed'].max()
            }

            # Display percentile stats
            col1, col2, col3, col4, col5 = st.columns(5)
            with col1:
                st.metric("Min (0%)", format_currency(percentiles['min']))
            with col2:
                st.metric("25th Percentile", format_currency(percentiles['q1']))
            with col3:
                st.metric("Median (50%)", format_currency(percentiles['median']))
            with col4:
                st.metric("75th Percentile", format_currency(percentiles['q3']))
            with col5:
                st.metric("Max (100%)", format_currency(percentiles['max']))

            # IQR
            iqr = percentiles['q3'] - percentiles['q1']
            st.caption(f"Interquartile Range (IQR): {format_currency(iqr)}")

            st.divider()

            # =================================================================
            # Data Export
            # =================================================================
            st.subheader("Export Data")

            display_df = spread_data[['locality_id', 'locality_name', 'state', 'allowed',
                                      'gpci_work', 'gpci_pe', 'gpci_mp']].copy()
            display_df.columns = ['Locality ID', 'Locality', 'State', 'Allowed',
                                  'GPCI Work', 'GPCI PE', 'GPCI MP']

            with st.expander("View Full Table"):
                st.dataframe(display_df, use_container_width=True, hide_index=True)

            csv = spread_data.to_csv(index=False)
            st.download_button(
                label="Download Locality Spread (CSV)",
                data=csv,
                file_name=f"{selected_code}_{selected_year}_locality_spread.csv",
                mime="text/csv"
            )
        else:
            st.info("No locality data available for selected code")
    else:
        st.warning("No data available for selected code and year")

except Exception as e:
    st.error(f"Error loading data: {e}")
    st.info("Ensure the database is running and analytics views are created.")
