"""
Page 1b: Code Group Monitor
Analyze CPT code groupings with utilization context and reimbursement component walkthrough
"""
import streamlit as st
import pandas as pd
import altair as alt
from utils import (
    get_connection,
    get_available_years,
    get_localities,
    get_conversion_factors,
    get_utilization_summary,
    get_code_list,
    CODE_GROUPS,
    CPT_CATEGORY_RANGES,
    COLORS,
    format_currency,
    format_percent
)

st.set_page_config(page_title="Code Group Monitor", page_icon="$", layout="wide")

st.title("Code Group Monitor")
st.caption("Analyze CPT groupings with utilization context and reimbursement component walkthrough")

# ============================================================================
# Local Data Functions
# ============================================================================

def get_code_group_decomposition(hcpcs_codes, year, locality_id='AL-00', setting='nonfacility'):
    """Get decomposition data for a group of HCPCS codes."""
    if not hcpcs_codes:
        return pd.DataFrame()
    conn = get_connection()
    # Validate setting to prevent SQL injection via column name
    if setting not in ('nonfacility', 'facility'):
        setting = 'nonfacility'
    placeholders = ','.join(['%s'] * len(hcpcs_codes))
    params = [year, locality_id] + list(hcpcs_codes)

    query = f"""
        SELECT
            d.hcpcs,
            d.modifier,
            d.hcpcs_mod,
            r.description,
            d.allowed_{setting}_py as prior_allowed,
            d.allowed_{setting} as current_allowed,
            d.total_change_{setting} as total_change,
            d.cf_effect_{setting} as cf_effect,
            d.gpci_effect_{setting} as gpci_effect,
            d.rvu_effect_{setting} as rvu_effect,
            d.w_rvu_py,
            d.w_rvu,
            d.pe_rvu_{setting}_py as pe_rvu_py,
            d.pe_rvu_{setting} as pe_rvu,
            d.mp_rvu_py,
            d.mp_rvu,
            d.cf_py,
            d.conversion_factor,
            d.gpci_work_py,
            d.gpci_work,
            d.gpci_pe_py,
            d.gpci_pe,
            d.gpci_mp_py,
            d.gpci_mp
        FROM drinf.v_mpfs_decomp d
        JOIN drinf.v_rvu_clean r ON r.year = d.year AND r.hcpcs_mod = d.hcpcs_mod
        WHERE d.year = %s
          AND d.locality_id = %s
          AND d.hcpcs IN ({placeholders})
          AND d.modifier IS NULL
        ORDER BY d.allowed_{setting} DESC
    """
    return pd.read_sql(query, conn, params=params)


def get_utilization_by_code(hcpcs_codes, year=2023):
    """Get utilization data for each code in a list."""
    if not hcpcs_codes:
        return pd.DataFrame()
    conn = get_connection()
    placeholders = ','.join(['%s'] * len(hcpcs_codes))
    params = list(hcpcs_codes) + [year]

    query = f"""
        SELECT
            hcpcs,
            hcpcs_desc,
            SUM(total_services) as total_services,
            SUM(total_beneficiaries) as total_beneficiaries,
            AVG(avg_payment_amt) as avg_payment,
            SUM(total_services * avg_payment_amt) as total_medicare_payment
        FROM drinf.medicare_utilization
        WHERE hcpcs IN ({placeholders})
          AND geo_level = 'National'
          AND year = %s
        GROUP BY hcpcs, hcpcs_desc
        ORDER BY total_services DESC
    """
    return pd.read_sql(query, conn, params=params)


# ============================================================================
# Sidebar - Configuration
# ============================================================================

st.sidebar.header("Code Group Selection")

# Selection method
selection_method = st.sidebar.radio(
    "Selection Method",
    options=["Radiology Groupings", "CPT Category", "Custom Codes"],
    horizontal=True
)

selected_codes = []
selected_group = "Custom"

if selection_method == "Radiology Groupings":
    selected_group = st.sidebar.selectbox(
        "Select Code Group",
        options=list(CODE_GROUPS.keys()),
        index=0
    )
    selected_codes = CODE_GROUPS[selected_group]
    st.sidebar.caption(f"Codes: {', '.join(selected_codes)}")

elif selection_method == "CPT Category":
    selected_category = st.sidebar.selectbox(
        "CPT Category",
        options=[k for k in CPT_CATEGORY_RANGES.keys() if k != "All Codes"],
        index=8  # Default to Radiology
    )
    selected_group = selected_category
    cat_range = CPT_CATEGORY_RANGES.get(selected_category)
    if cat_range:
        st.sidebar.caption(f"Range: {cat_range[0]} - {cat_range[1]}")
        try:
            all_codes = get_code_list()
            selected_codes = [c for c in all_codes if cat_range[0] <= c <= cat_range[1]][:20]
            st.sidebar.caption(f"Using top 20 codes in range")
        except:
            selected_codes = []

else:  # Custom Codes
    custom_codes = st.sidebar.text_input(
        "Enter codes (comma-separated)",
        placeholder="70553, 70552, 70551"
    )
    if custom_codes:
        selected_codes = [c.strip() for c in custom_codes.split(",") if c.strip()]

st.sidebar.markdown("---")
st.sidebar.header("Analysis Settings")

try:
    years = get_available_years()
    localities = get_localities()

    selected_year = st.sidebar.selectbox(
        "Year",
        options=sorted(years, reverse=True),
        index=0
    )

    locality_options = localities['locality_id'].tolist()
    locality_names = dict(zip(localities['locality_id'], localities['locality_name']))

    selected_locality = st.sidebar.selectbox(
        "Reference Locality",
        options=locality_options,
        index=locality_options.index('AL-00') if 'AL-00' in locality_options else 0,
        format_func=lambda x: f"{locality_names.get(x, x)} ({x})"
    )

    setting = st.sidebar.radio(
        "Payment Setting",
        options=['nonfacility', 'facility'],
        format_func=lambda x: 'Non-Facility' if x == 'nonfacility' else 'Facility'
    )

    st.sidebar.markdown("---")
    st.sidebar.caption(f"Group: {selected_group}")
    st.sidebar.caption(f"Locality: {locality_names.get(selected_locality, selected_locality)}")

    # =========================================================================
    # Main Content
    # =========================================================================

    # Get data
    decomp_df = get_code_group_decomposition(selected_codes, selected_year, selected_locality, setting)

    # Get utilization data (2023)
    try:
        util_df = get_utilization_by_code(selected_codes, 2023)
        util_summary = get_utilization_summary(selected_codes, 2023)
        has_utilization = len(util_df) > 0
    except Exception as e:
        util_df = pd.DataFrame()
        util_summary = {'total_services': 0, 'total_beneficiaries': 0, 'total_medicare_payment': 0}
        has_utilization = False

    if len(decomp_df) == 0:
        st.warning(f"No data found for selected codes in {selected_year}")
    else:
        # =====================================================================
        # Section 1: Summary KPIs
        # =====================================================================
        st.subheader(f"{selected_group} Overview")

        # Calculate summary stats
        avg_prior = decomp_df['prior_allowed'].mean()
        avg_current = decomp_df['current_allowed'].mean()
        avg_change = decomp_df['total_change'].mean()
        avg_pct_change = ((avg_current - avg_prior) / avg_prior * 100) if avg_prior else 0
        codes_increased = (decomp_df['total_change'] > 0).sum()
        codes_decreased = (decomp_df['total_change'] < 0).sum()

        # KPI row 1 - Payment Stats
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric("Codes in Group", len(decomp_df))
        with col2:
            st.metric("Avg Payment", format_currency(avg_current), f"{avg_pct_change:+.1f}%")
        with col3:
            st.metric("Codes Increased", codes_increased)
        with col4:
            st.metric("Codes Decreased", codes_decreased)

        # KPI row 2 - Utilization Context (2023)
        if has_utilization:
            st.markdown("**2023 Medicare Utilization Context:**")
            u1, u2, u3 = st.columns(3)
            with u1:
                st.metric("Total Services", f"{util_summary['total_services']:,.0f}")
            with u2:
                st.metric("Beneficiaries", f"{util_summary['total_beneficiaries']:,.0f}")
            with u3:
                st.metric("Total Medicare $", f"${util_summary['total_medicare_payment']/1e6:,.1f}M")

            # Budget impact estimate
            if avg_pct_change != 0:
                impact = util_summary['total_medicare_payment'] * (avg_pct_change / 100)
                impact_word = "increase" if impact > 0 else "decrease"
                st.info(f"**Estimated Budget Impact:** ${abs(impact)/1e6:,.1f}M {impact_word} based on {avg_pct_change:+.1f}% avg payment change")

        st.divider()

        # =====================================================================
        # Section 2: Reimbursement Component Walkthrough
        # =====================================================================
        st.subheader("Reimbursement Component Walkthrough")

        st.markdown("""
        Medicare physician payments are calculated as: **Payment = (Work RVU × Work GPCI + PE RVU × PE GPCI + MP RVU × MP GPCI) × Conversion Factor**

        The change in payment from year to year can be decomposed into three effects:
        """)

        # Calculate aggregate component effects
        avg_cf_effect = decomp_df['cf_effect'].mean()
        avg_gpci_effect = decomp_df['gpci_effect'].mean()
        avg_rvu_effect = decomp_df['rvu_effect'].mean()

        # Component breakdown
        comp_col1, comp_col2, comp_col3 = st.columns(3)

        cf_pct = (avg_cf_effect / avg_prior * 100) if avg_prior else 0
        gpci_pct = (avg_gpci_effect / avg_prior * 100) if avg_prior else 0
        rvu_pct = (avg_rvu_effect / avg_prior * 100) if avg_prior else 0

        # Get CF values
        cf_prior = decomp_df['cf_py'].iloc[0] if len(decomp_df) > 0 else 0
        cf_current = decomp_df['conversion_factor'].iloc[0] if len(decomp_df) > 0 else 0

        with comp_col1:
            st.metric(
                "1. Conversion Factor Effect",
                format_currency(avg_cf_effect),
                f"{cf_pct:+.1f}% of payment",
                help="Impact from CF change"
            )
            st.caption(f"${cf_prior:.4f} → ${cf_current:.4f}")

        with comp_col2:
            st.metric(
                "2. GPCI Effect",
                format_currency(avg_gpci_effect),
                f"{gpci_pct:+.1f}% of payment",
                help="Impact from geographic adjustment changes"
            )
            gpci_work_prior = decomp_df['gpci_work_py'].mean()
            gpci_work_current = decomp_df['gpci_work'].mean()
            st.caption(f"Work GPCI: {gpci_work_prior:.4f} → {gpci_work_current:.4f}")

        with comp_col3:
            st.metric(
                "3. RVU Effect",
                format_currency(avg_rvu_effect),
                f"{rvu_pct:+.1f}% of payment",
                help="Impact from relative value unit changes"
            )
            w_rvu_prior = decomp_df['w_rvu_py'].mean()
            w_rvu_current = decomp_df['w_rvu'].mean()
            st.caption(f"Avg Work RVU: {w_rvu_prior:.2f} → {w_rvu_current:.2f}")

        # Waterfall summary chart
        st.markdown("**Average Payment Change Waterfall:**")

        waterfall_data = pd.DataFrame([
            {'Component': f'Prior Year ({selected_year - 1})', 'Value': avg_prior, 'Type': 'Total'},
            {'Component': 'CF Effect', 'Value': avg_cf_effect, 'Type': 'Positive' if avg_cf_effect >= 0 else 'Negative'},
            {'Component': 'GPCI Effect', 'Value': avg_gpci_effect, 'Type': 'Positive' if avg_gpci_effect >= 0 else 'Negative'},
            {'Component': 'RVU Effect', 'Value': avg_rvu_effect, 'Type': 'Positive' if avg_rvu_effect >= 0 else 'Negative'},
            {'Component': f'Current Year ({selected_year})', 'Value': avg_current, 'Type': 'Total'},
        ])

        chart = alt.Chart(waterfall_data).mark_bar().encode(
            x=alt.X('Component:N', sort=None, title=''),
            y=alt.Y('Value:Q', title='Allowed Amount ($)'),
            color=alt.Color('Type:N',
                           scale=alt.Scale(
                               domain=['Total', 'Positive', 'Negative'],
                               range=[COLORS['neutral'], COLORS['positive'], COLORS['negative']]
                           ),
                           legend=None),
            tooltip=[
                alt.Tooltip('Component:N', title='Component'),
                alt.Tooltip('Value:Q', title='Amount', format='$.2f')
            ]
        ).properties(height=300)

        st.altair_chart(chart, use_container_width=True)

        st.divider()

        # =====================================================================
        # Section 3: Code-Level Detail Table
        # =====================================================================
        st.subheader("Code-Level Detail")

        # Merge utilization data if available
        if has_utilization:
            display_df = decomp_df.merge(
                util_df[['hcpcs', 'total_services', 'total_medicare_payment']],
                on='hcpcs',
                how='left'
            )
        else:
            display_df = decomp_df.copy()
            display_df['total_services'] = None
            display_df['total_medicare_payment'] = None

        # Prepare display columns
        table_df = display_df[[
            'hcpcs', 'description', 'prior_allowed', 'current_allowed', 'total_change',
            'cf_effect', 'gpci_effect', 'rvu_effect', 'total_services', 'total_medicare_payment'
        ]].copy()

        table_df.columns = [
            'CPT', 'Description', 'Prior $', 'Current $', 'Change',
            'CF Effect', 'GPCI Effect', 'RVU Effect', '2023 Services', '2023 Medicare $'
        ]

        # Format columns
        for col in ['Prior $', 'Current $', 'Change', 'CF Effect', 'GPCI Effect', 'RVU Effect']:
            table_df[col] = table_df[col].apply(lambda x: format_currency(x) if pd.notna(x) else '-')

        table_df['2023 Services'] = table_df['2023 Services'].apply(
            lambda x: f"{x:,.0f}" if pd.notna(x) else '-'
        )
        table_df['2023 Medicare $'] = table_df['2023 Medicare $'].apply(
            lambda x: f"${x/1e6:.1f}M" if pd.notna(x) and x > 0 else '-'
        )

        # Truncate description
        table_df['Description'] = table_df['Description'].apply(
            lambda x: x[:40] + '...' if pd.notna(x) and len(str(x)) > 40 else x
        )

        st.dataframe(table_df, use_container_width=True, hide_index=True)

        # Download button
        csv_df = decomp_df.copy()
        if has_utilization:
            csv_df = csv_df.merge(util_df, on='hcpcs', how='left')
        csv = csv_df.to_csv(index=False)
        st.download_button(
            label="Download Data (CSV)",
            data=csv,
            file_name=f"{selected_group.replace(' ', '_')}_{selected_year}_{selected_locality}.csv",
            mime="text/csv"
        )

        st.divider()

        # =====================================================================
        # Section 4: Detailed RVU Breakdown
        # =====================================================================
        with st.expander("Detailed RVU Components"):
            st.markdown("**RVU Breakdown by Code:**")

            rvu_df = decomp_df[[
                'hcpcs', 'description',
                'w_rvu_py', 'w_rvu',
                'pe_rvu_py', 'pe_rvu',
                'mp_rvu_py', 'mp_rvu'
            ]].copy()

            rvu_df['w_rvu_change'] = rvu_df['w_rvu'] - rvu_df['w_rvu_py']
            rvu_df['pe_rvu_change'] = rvu_df['pe_rvu'] - rvu_df['pe_rvu_py']
            rvu_df['mp_rvu_change'] = rvu_df['mp_rvu'] - rvu_df['mp_rvu_py']

            rvu_df.columns = [
                'CPT', 'Description',
                'Work RVU (Prior)', 'Work RVU (Current)',
                'PE RVU (Prior)', 'PE RVU (Current)',
                'MP RVU (Prior)', 'MP RVU (Current)',
                'Work Chg', 'PE Chg', 'MP Chg'
            ]

            # Format
            for col in rvu_df.columns[2:]:
                rvu_df[col] = rvu_df[col].apply(lambda x: f"{x:.2f}" if pd.notna(x) else '-')

            rvu_df['Description'] = rvu_df['Description'].apply(
                lambda x: x[:30] + '...' if pd.notna(x) and len(str(x)) > 30 else x
            )

            st.dataframe(rvu_df, use_container_width=True, hide_index=True)

        # =====================================================================
        # Section 5: GPCI Components
        # =====================================================================
        with st.expander("GPCI Components for Locality"):
            st.markdown(f"**GPCI Values for {locality_names.get(selected_locality, selected_locality)}:**")

            gpci_data = pd.DataFrame({
                'Component': ['Work GPCI', 'PE GPCI', 'MP GPCI'],
                f'Prior ({selected_year - 1})': [
                    f"{decomp_df['gpci_work_py'].iloc[0]:.4f}" if len(decomp_df) > 0 else '-',
                    f"{decomp_df['gpci_pe_py'].iloc[0]:.4f}" if len(decomp_df) > 0 else '-',
                    f"{decomp_df['gpci_mp_py'].iloc[0]:.4f}" if len(decomp_df) > 0 else '-',
                ],
                f'Current ({selected_year})': [
                    f"{decomp_df['gpci_work'].iloc[0]:.4f}" if len(decomp_df) > 0 else '-',
                    f"{decomp_df['gpci_pe'].iloc[0]:.4f}" if len(decomp_df) > 0 else '-',
                    f"{decomp_df['gpci_mp'].iloc[0]:.4f}" if len(decomp_df) > 0 else '-',
                ],
            })

            st.dataframe(gpci_data, use_container_width=True, hide_index=True)

            st.markdown("""
            **GPCI Interpretation:**
            - Values > 1.0 indicate higher costs of practicing in this area vs national average
            - Values < 1.0 indicate lower costs
            - Work GPCI affects physician work component
            - PE GPCI affects practice expense (staff, supplies, equipment)
            - MP GPCI affects malpractice insurance costs
            """)

except Exception as e:
    st.error(f"Error loading data: {e}")
    st.info("Ensure the database is running and analytics views are created.")
    st.code(str(e))

# Footer with data source footnote
st.markdown("---")
st.caption("Medicare utilization data: CMS Medicare Physician & Other Practitioners Public Use File (2023, National)")
