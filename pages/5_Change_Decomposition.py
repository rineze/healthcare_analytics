"""
Page 5: Change Decomposition
Isolate the drivers of payment change (RVU, GPCI, CF) for a specific code and locality
"""
import streamlit as st
import pandas as pd
import altair as alt
from utils import (
    get_available_years,
    get_code_list,
    get_localities,
    get_decomposition,
    get_decomposition_history,
    COLORS,
    format_currency,
    format_percent
)

st.set_page_config(page_title="Change Decomposition", page_icon="$", layout="wide")

st.title("Payment Change Decomposition")
st.caption("Waterfall analysis: Isolate CF, GPCI, and RVU effects on payment changes")

try:
    years = get_available_years()
    latest_year = max(years)
    localities = get_localities()

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

    st.sidebar.header("Locality Selection")

    locality_options = localities['locality_id'].tolist()
    locality_names = dict(zip(localities['locality_id'], localities['locality_name']))

    default_loc_idx = locality_options.index('CA-18') if 'CA-18' in locality_options else 0

    selected_locality = st.sidebar.selectbox(
        "Select Locality",
        options=locality_options,
        index=default_loc_idx,
        format_func=lambda x: f"{locality_names.get(x, x)} ({x})"
    )

    st.sidebar.header("Year")

    # Exclude first year (no prior year for comparison)
    year_options = [y for y in sorted(years, reverse=True) if y > min(years)]

    selected_year = st.sidebar.selectbox(
        "Select Year",
        options=year_options,
        index=0
    )

    setting = st.sidebar.radio(
        "Payment Setting",
        options=['nonfacility', 'facility'],
        format_func=lambda x: x.replace('nonfacility', 'Non-Facility').replace('facility', 'Facility')
    )

    st.sidebar.markdown("---")
    st.sidebar.caption(f"Code: {selected_code}")
    st.sidebar.caption(f"Locality: {locality_names.get(selected_locality, selected_locality)}")

    # =========================================================================
    # Code Header
    # =========================================================================
    st.subheader(f"{selected_code}")
    st.caption(code_descriptions.get(selected_code, 'No description available'))
    st.caption(f"Locality: {locality_names.get(selected_locality, selected_locality)} | Year: {selected_year}")

    # =========================================================================
    # Get Decomposition Data
    # =========================================================================
    decomp = get_decomposition(selected_code, selected_locality, selected_year, setting)

    if decomp is not None:
        # =====================================================================
        # Waterfall Chart
        # =====================================================================
        st.subheader(f"Payment Change Waterfall ({setting.title()})")

        # Build waterfall data
        waterfall_data = pd.DataFrame([
            {'category': f'Prior Year ({selected_year - 1})', 'value': decomp['prior_allowed'],
             'type': 'start', 'running_total': decomp['prior_allowed']},
            {'category': 'CF Effect', 'value': decomp['cf_effect'],
             'type': 'delta', 'running_total': decomp['prior_allowed'] + decomp['cf_effect']},
            {'category': 'GPCI Effect', 'value': decomp['gpci_effect'],
             'type': 'delta', 'running_total': decomp['prior_allowed'] + decomp['cf_effect'] + decomp['gpci_effect']},
            {'category': 'RVU Effect', 'value': decomp['rvu_effect'],
             'type': 'delta', 'running_total': decomp['prior_allowed'] + decomp['cf_effect'] + decomp['gpci_effect'] + decomp['rvu_effect']},
            {'category': f'Current Year ({selected_year})', 'value': decomp['current_allowed'],
             'type': 'end', 'running_total': decomp['current_allowed']}
        ])

        # Calculate bar positions for waterfall
        waterfall_data['start'] = waterfall_data['running_total'] - waterfall_data['value'].abs()
        waterfall_data.loc[waterfall_data['type'] == 'start', 'start'] = 0
        waterfall_data.loc[waterfall_data['type'] == 'end', 'start'] = 0

        # For deltas, calculate proper start position
        for i in range(1, len(waterfall_data) - 1):
            if waterfall_data.loc[i, 'value'] >= 0:
                waterfall_data.loc[i, 'start'] = waterfall_data.loc[i-1, 'running_total']
            else:
                waterfall_data.loc[i, 'start'] = waterfall_data.loc[i, 'running_total']

        waterfall_data['end'] = waterfall_data['running_total']
        waterfall_data.loc[waterfall_data['type'] == 'start', 'end'] = waterfall_data.loc[waterfall_data['type'] == 'start', 'value']
        waterfall_data.loc[waterfall_data['type'] == 'end', 'end'] = waterfall_data.loc[waterfall_data['type'] == 'end', 'value']

        # Color coding
        def get_color(row):
            if row['type'] in ['start', 'end']:
                return COLORS['neutral']
            elif row['value'] >= 0:
                return COLORS['positive']
            else:
                return COLORS['negative']

        waterfall_data['color'] = waterfall_data.apply(get_color, axis=1)

        # Create waterfall chart using bars
        base = alt.Chart(waterfall_data).encode(
            y=alt.Y('category:N', title='', sort=None)
        )

        bars = base.mark_bar(size=30).encode(
            x=alt.X('start:Q', title='Allowed Amount ($)'),
            x2='end:Q',
            color=alt.Color('color:N', scale=None),
            tooltip=[
                alt.Tooltip('category:N', title='Component'),
                alt.Tooltip('value:Q', title='Amount', format='$.2f'),
                alt.Tooltip('running_total:Q', title='Running Total', format='$.2f')
            ]
        )

        # Data labels
        labels = base.mark_text(align='left', dx=5).encode(
            x='end:Q',
            text=alt.Text('value:Q', format='$.2f')
        )

        st.altair_chart(bars + labels, use_container_width=True)

        # Summary metrics
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric(f"Prior Year ({selected_year - 1})", format_currency(decomp['prior_allowed']))
        with col2:
            st.metric(f"Current Year ({selected_year})", format_currency(decomp['current_allowed']))
        with col3:
            total_change = decomp['current_allowed'] - decomp['prior_allowed']
            pct_change = (total_change / decomp['prior_allowed'] * 100) if decomp['prior_allowed'] else None
            st.metric("Total Change", format_currency(total_change),
                     f"{pct_change:+.1f}%" if pct_change else None)
        with col4:
            sum_components = decomp['cf_effect'] + decomp['gpci_effect'] + decomp['rvu_effect']
            interaction = total_change - sum_components
            st.metric("Interaction Effect", format_currency(interaction),
                     help="Difference between total change and sum of components (due to multiplicative interactions)")

        st.divider()

        # =====================================================================
        # Component Breakdown
        # =====================================================================
        st.subheader("Component Breakdown")

        comp_col1, comp_col2, comp_col3 = st.columns(3)

        with comp_col1:
            cf_pct = (decomp['cf_effect'] / decomp['prior_allowed'] * 100) if decomp['prior_allowed'] else None
            st.metric(
                "CF Effect",
                format_currency(decomp['cf_effect']),
                f"{cf_pct:+.1f}%" if cf_pct else None,
                help=f"CF changed from ${decomp['cf_py']:.4f} to ${decomp['conversion_factor']:.4f}"
            )

        with comp_col2:
            gpci_pct = (decomp['gpci_effect'] / decomp['prior_allowed'] * 100) if decomp['prior_allowed'] else None
            st.metric(
                "GPCI Effect",
                format_currency(decomp['gpci_effect']),
                f"{gpci_pct:+.1f}%" if gpci_pct else None,
                help=f"Work GPCI changed from {decomp['gpci_work_py']:.4f} to {decomp['gpci_work']:.4f}"
            )

        with comp_col3:
            rvu_pct = (decomp['rvu_effect'] / decomp['prior_allowed'] * 100) if decomp['prior_allowed'] else None
            st.metric(
                "RVU Effect",
                format_currency(decomp['rvu_effect']),
                f"{rvu_pct:+.1f}%" if rvu_pct else None,
                help=f"Work RVU changed from {decomp['w_rvu_py']:.2f} to {decomp['w_rvu']:.2f}"
            )

        st.divider()

        # =====================================================================
        # Input Comparison Table
        # =====================================================================
        st.subheader("Input Values (Prior vs Current)")

        input_data = pd.DataFrame({
            'Component': ['Work RVU', 'Work GPCI', 'Conversion Factor'],
            f'Prior ({selected_year - 1})': [
                f"{decomp['w_rvu_py']:.2f}" if pd.notna(decomp['w_rvu_py']) else '-',
                f"{decomp['gpci_work_py']:.4f}" if pd.notna(decomp['gpci_work_py']) else '-',
                f"${decomp['cf_py']:.4f}" if pd.notna(decomp['cf_py']) else '-'
            ],
            f'Current ({selected_year})': [
                f"{decomp['w_rvu']:.2f}" if pd.notna(decomp['w_rvu']) else '-',
                f"{decomp['gpci_work']:.4f}" if pd.notna(decomp['gpci_work']) else '-',
                f"${decomp['conversion_factor']:.4f}" if pd.notna(decomp['conversion_factor']) else '-'
            ],
            'Change': [
                f"{decomp['w_rvu'] - decomp['w_rvu_py']:+.2f}" if pd.notna(decomp['w_rvu']) and pd.notna(decomp['w_rvu_py']) else '-',
                f"{decomp['gpci_work'] - decomp['gpci_work_py']:+.4f}" if pd.notna(decomp['gpci_work']) and pd.notna(decomp['gpci_work_py']) else '-',
                f"${decomp['conversion_factor'] - decomp['cf_py']:+.4f}" if pd.notna(decomp['conversion_factor']) and pd.notna(decomp['cf_py']) else '-'
            ]
        })

        st.dataframe(input_data, use_container_width=True, hide_index=True)

        st.divider()

        # =====================================================================
        # Historical Decomposition Table
        # =====================================================================
        st.subheader("Historical Decomposition")

        history = get_decomposition_history(selected_code, selected_locality, setting)

        if len(history) > 0:
            display_df = history[['year', 'prior_allowed', 'current_allowed', 'total_change',
                                  'cf_effect', 'gpci_effect', 'rvu_effect']].copy()
            display_df['sum_check'] = display_df['cf_effect'] + display_df['gpci_effect'] + display_df['rvu_effect']
            display_df['interaction'] = display_df['total_change'] - display_df['sum_check']

            display_df.columns = ['Year', 'Prior $', 'Current $', 'Total Chg',
                                  'CF Effect', 'GPCI Effect', 'RVU Effect', 'Sum Check', 'Interaction']

            # Format
            for col in ['Prior $', 'Current $', 'Total Chg', 'CF Effect', 'GPCI Effect', 'RVU Effect', 'Sum Check', 'Interaction']:
                display_df[col] = display_df[col].apply(lambda x: format_currency(x))

            st.dataframe(display_df, use_container_width=True, hide_index=True)

            # Download
            csv = history.to_csv(index=False)
            st.download_button(
                label="Download Decomposition History (CSV)",
                data=csv,
                file_name=f"{selected_code}_{selected_locality}_decomposition.csv",
                mime="text/csv"
            )
        else:
            st.info("No historical data available")

    else:
        st.warning(f"No decomposition data available for {selected_code} in {locality_names.get(selected_locality, selected_locality)} for {selected_year}")
        st.info("This may occur if the code didn't exist in the prior year or has no valid RVU values.")

except Exception as e:
    st.error(f"Error loading data: {e}")
    st.info("Ensure the database is running and analytics views are created.")
