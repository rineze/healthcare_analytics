"""
Page 7: CPT Economics & Site-of-Service Signals
Understand how Medicare values CPTs across settings and cost components
"""
import streamlit as st
import pandas as pd
import altair as alt
from utils import (
    get_available_years,
    get_localities,
    get_cpt_economics_data,
    get_cpt_economics_with_util,
    get_cpt_trend_data,
    COLORS,
    format_currency,
    format_percent,
    CPT_CATEGORY_RANGES
)

st.set_page_config(page_title="CPT Economics", page_icon="$", layout="wide")

# Use shared category definitions
CPT_CATEGORIES = CPT_CATEGORY_RANGES

# Initialize session state
if 'selected_cpts' not in st.session_state:
    st.session_state.selected_cpts = []
if 'trend_cpt' not in st.session_state:
    st.session_state.trend_cpt = None

st.title("CPT Economics & Site-of-Service Signals")
st.caption("Analyze Medicare cost structure for contract negotiations")

try:
    years = get_available_years()
    localities = get_localities()
    latest_year = max(years)

    # =========================================================================
    # FILTERS ROW 1: Year, Locality, Setting, Data Mode
    # =========================================================================
    filter_cols = st.columns([1, 1.5, 1.2, 1.2])

    with filter_cols[0]:
        selected_year = st.selectbox(
            "Year",
            options=sorted(years, reverse=True),
            index=0
        )

    with filter_cols[1]:
        locality_options = localities['locality_id'].tolist()
        locality_names = dict(zip(localities['locality_id'], localities['locality_name']))
        default_loc = 'AL-00' if 'AL-00' in locality_options else locality_options[0]

        selected_locality = st.selectbox(
            "Locality",
            options=locality_options,
            index=locality_options.index(default_loc),
            format_func=lambda x: f"{x} - {locality_names.get(x, '')[:25]}",
            help="AL-00 approximates national baseline"
        )

    with filter_cols[2]:
        setting_focus = st.radio(
            "Setting Focus",
            options=['nonfacility', 'facility'],
            format_func=lambda x: 'Non-Facility' if x == 'nonfacility' else 'Facility',
            horizontal=True
        )

    with filter_cols[3]:
        data_mode = st.radio(
            "Data Mode",
            options=['mpfs', 'util'],
            format_func=lambda x: 'MPFS Only' if x == 'mpfs' else 'Util-Weighted',
            horizontal=True,
            help="Util-weighted prioritizes codes by 2023 Medicare volume"
        )

    # =========================================================================
    # LOAD DATA
    # =========================================================================
    if data_mode == 'util':
        df_full = get_cpt_economics_with_util(selected_year, selected_locality)
    else:
        df_full = get_cpt_economics_data(selected_year, selected_locality)

    # =========================================================================
    # FILTERS ROW 2: Category Preset + Multi-Select CPT
    # =========================================================================
    st.divider()

    cat_col, mod_col, cpt_col = st.columns([1, 0.8, 2])

    with cat_col:
        selected_category = st.selectbox(
            "Code Category",
            options=list(CPT_CATEGORIES.keys()),
            index=0,
            help="Quick filter by CPT range"
        )

    with mod_col:
        base_codes_only = st.checkbox(
            "Base codes only",
            value=True,
            help="Exclude modifier variants (26, TC, etc.)"
        )

    # Apply filters to get working dataset
    df = df_full.copy()

    # Filter modifiers
    if base_codes_only:
        df = df[df['modifier'].isna()].copy()

    # Filter category
    cat_range = CPT_CATEGORIES[selected_category]
    if cat_range:
        df = df[(df['hcpcs'] >= cat_range[0]) & (df['hcpcs'] <= cat_range[1])].copy()

    # Limit for performance
    size_col = 'total_medicare_dollars' if data_mode == 'util' and 'total_medicare_dollars' in df.columns else 'total_rvu_nf'
    if len(df) > 500:
        df = df.nlargest(500, size_col)

    with cpt_col:
        # Create searchable options
        df['search_label'] = df['hcpcs'] + ' - ' + df['description'].fillna('').str[:40]
        cpt_options = df['search_label'].tolist()

        selected_labels = st.multiselect(
            "Select CPT Codes (multi-select)",
            options=cpt_options,
            default=None,
            placeholder="Search and select codes to analyze...",
            help="Select multiple codes to see portfolio-level metrics"
        )

        # Extract just the CPT codes
        selected_cpts = [label.split(' - ')[0] for label in selected_labels] if selected_labels else []

    # =========================================================================
    # FILTER DATA BY SELECTION
    # =========================================================================
    if selected_cpts:
        df_selected = df[df['hcpcs'].isin(selected_cpts)].copy()
    else:
        df_selected = pd.DataFrame()  # Empty until selection made

    # =========================================================================
    # PORTFOLIO KPI TILES
    # =========================================================================
    st.divider()

    if len(df_selected) > 0:
        st.subheader(f"Portfolio Summary ({len(df_selected)} codes selected)")

        kpi_cols = st.columns(6)

        # Calculate portfolio metrics
        pe_col_name = 'pe_share_nf' if setting_focus == 'nonfacility' else 'pe_share_f'
        work_col_name = 'work_share_nf' if setting_focus == 'nonfacility' else 'work_share_f'

        avg_pe_share = df_selected[pe_col_name].mean()
        avg_work_share = df_selected[work_col_name].mean()
        total_site_gap = df_selected['site_gap'].sum()
        avg_site_gap = df_selected['site_gap'].mean()
        avg_office = df_selected['allowed_nonfacility'].mean()
        avg_facility = df_selected['allowed_facility'].mean()

        # Count by geo sensitivity
        high_geo = (df_selected['geo_sensitivity'] == 'High').sum()

        with kpi_cols[0]:
            st.metric(
                "Avg Office $",
                format_currency(avg_office),
                help="Average non-facility allowed across selected codes"
            )

        with kpi_cols[1]:
            st.metric(
                "Avg Facility $",
                format_currency(avg_facility),
                help="Average facility allowed across selected codes"
            )

        with kpi_cols[2]:
            st.metric(
                "Avg Site Gap",
                format_currency(avg_site_gap),
                help="Average payment difference (office - facility)"
            )

        with kpi_cols[3]:
            st.metric(
                "Avg PE Intensity",
                f"{avg_pe_share:.1f}%",
                help="Average PE share — higher = more overhead-driven"
            )

        with kpi_cols[4]:
            st.metric(
                "Avg Work Intensity",
                f"{avg_work_share:.1f}%",
                help="Average work share — higher = more physician-driven"
            )

        with kpi_cols[5]:
            st.metric(
                "High Geo Sensitivity",
                f"{high_geo} / {len(df_selected)}",
                help="Codes where payment varies significantly by market"
            )

        # Util-weighted impact if available
        if data_mode == 'util' and 'util_gap_impact' in df_selected.columns:
            total_util_impact = df_selected['util_gap_impact'].sum()
            st.info(f"**Volume-Weighted Site Gap Impact:** ${total_util_impact:,.0f} — total annual $ difference between office and facility payment for these codes")

    else:
        st.subheader("Select CPT codes above to see portfolio metrics")
        st.caption("Use the category filter for quick specialty selection, then pick individual codes")

    st.divider()

    # =========================================================================
    # MAIN VISUALS: Scatter + Selected Codes Table
    # =========================================================================
    scatter_col, table_col = st.columns([1.2, 1])

    with scatter_col:
        st.subheader("CPT Economics Map")

        work_col = 'work_share_nf' if setting_focus == 'nonfacility' else 'work_share_f'
        pe_col = 'pe_share_nf' if setting_focus == 'nonfacility' else 'pe_share_f'
        point_size_col = 'total_medicare_dollars' if data_mode == 'util' and 'total_medicare_dollars' in df.columns else 'total_rvu_nf'

        scatter_df = df[[
            'hcpcs', 'description', work_col, pe_col, point_size_col,
            'allowed_nonfacility', 'allowed_facility', 'site_gap', 'econ_category'
        ]].copy()
        scatter_df = scatter_df.dropna(subset=[work_col, pe_col])

        # Mark selected codes
        scatter_df['selected'] = scatter_df['hcpcs'].isin(selected_cpts)

        # Color mapping
        category_colors = {
            'Work-Heavy': COLORS['accent'],
            'PE-Heavy': '#e65100',
            'Balanced': COLORS['neutral_light']
        }

        # Base scatter (unselected = faded)
        scatter_base = alt.Chart(scatter_df[~scatter_df['selected']]).mark_circle(opacity=0.3).encode(
            x=alt.X(f'{work_col}:Q', title='Work Share % →', scale=alt.Scale(domain=[0, 100])),
            y=alt.Y(f'{pe_col}:Q', title='↑ PE Share %', scale=alt.Scale(domain=[0, 100])),
            size=alt.Size(f'{point_size_col}:Q', scale=alt.Scale(range=[15, 200]), legend=None),
            color=alt.value(COLORS['neutral_light']),
            tooltip=[
                alt.Tooltip('hcpcs:N', title='CPT'),
                alt.Tooltip('description:N', title='Description'),
                alt.Tooltip('allowed_nonfacility:Q', title='Office $', format='$.2f'),
                alt.Tooltip('allowed_facility:Q', title='Facility $', format='$.2f'),
                alt.Tooltip('site_gap:Q', title='Gap $', format='$.2f'),
                alt.Tooltip(f'{pe_col}:Q', title='PE Share %', format='.1f'),
            ]
        )

        # Selected codes (highlighted)
        if len(selected_cpts) > 0:
            scatter_selected = alt.Chart(scatter_df[scatter_df['selected']]).mark_circle(
                opacity=0.9, stroke='black', strokeWidth=1
            ).encode(
                x=alt.X(f'{work_col}:Q'),
                y=alt.Y(f'{pe_col}:Q'),
                size=alt.Size(f'{point_size_col}:Q', scale=alt.Scale(range=[50, 400]), legend=None),
                color=alt.Color('econ_category:N', title='Category',
                    scale=alt.Scale(domain=list(category_colors.keys()), range=list(category_colors.values()))),
                tooltip=[
                    alt.Tooltip('hcpcs:N', title='CPT'),
                    alt.Tooltip('description:N', title='Description'),
                    alt.Tooltip('allowed_nonfacility:Q', title='Office $', format='$.2f'),
                    alt.Tooltip('allowed_facility:Q', title='Facility $', format='$.2f'),
                    alt.Tooltip('site_gap:Q', title='Gap $', format='$.2f'),
                    alt.Tooltip(f'{pe_col}:Q', title='PE Share %', format='.1f'),
                    alt.Tooltip(f'{work_col}:Q', title='Work Share %', format='.1f'),
                ]
            )
            chart = scatter_base + scatter_selected
            st.caption(f"**{len(selected_cpts)} codes highlighted** — unselected codes shown faded")
        else:
            chart = scatter_base
            st.caption("Select codes above to highlight them on the map")

        # Reference lines
        hline = alt.Chart(pd.DataFrame({'y': [50]})).mark_rule(strokeDash=[4, 4], color=COLORS['neutral_light']).encode(y='y:Q')
        vline = alt.Chart(pd.DataFrame({'x': [50]})).mark_rule(strokeDash=[4, 4], color=COLORS['neutral_light']).encode(x='x:Q')

        st.altair_chart(chart + hline + vline, use_container_width=True)

    # =========================================================================
    # SELECTED CODES TABLE
    # =========================================================================
    with table_col:
        if len(df_selected) > 0:
            st.subheader("Selected Codes Detail")

            detail_df = df_selected[[
                'hcpcs', 'description', 'allowed_nonfacility', 'allowed_facility',
                'site_gap', 'site_gap_pct', pe_col, 'geo_sensitivity'
            ]].copy()
            detail_df.columns = ['CPT', 'Description', 'Office $', 'Facility $', 'Gap $', 'Gap %', 'PE %', 'Geo Sens']
            detail_df['Description'] = detail_df['Description'].str[:28]
            detail_df = detail_df.sort_values('Gap $', ascending=False)

            st.dataframe(
                detail_df,
                column_config={
                    'Office $': st.column_config.NumberColumn(format="$%.2f"),
                    'Facility $': st.column_config.NumberColumn(format="$%.2f"),
                    'Gap $': st.column_config.NumberColumn(format="$%.2f"),
                    'Gap %': st.column_config.NumberColumn(format="%.1f%%"),
                    'PE %': st.column_config.NumberColumn(format="%.1f%%"),
                },
                hide_index=True,
                height=420
            )
        else:
            st.subheader("Site-of-Service Gap Leaderboard")
            st.caption("Top codes by payment gap (select codes above to filter)")

            sort_col = 'util_gap_impact' if data_mode == 'util' and 'util_gap_impact' in df.columns else 'site_gap'
            leaderboard = df.nlargest(25, sort_col)[[
                'hcpcs', 'description', 'allowed_nonfacility', 'allowed_facility',
                'site_gap', 'site_gap_pct', pe_col
            ]].copy()
            leaderboard.columns = ['CPT', 'Description', 'Office $', 'Facility $', 'Gap $', 'Gap %', 'PE %']
            leaderboard['Description'] = leaderboard['Description'].str[:28]

            st.dataframe(
                leaderboard,
                column_config={
                    'Office $': st.column_config.NumberColumn(format="$%.2f"),
                    'Facility $': st.column_config.NumberColumn(format="$%.2f"),
                    'Gap $': st.column_config.NumberColumn(format="$%.2f"),
                    'Gap %': st.column_config.NumberColumn(format="%.1f%%"),
                    'PE %': st.column_config.NumberColumn(format="%.1f%%"),
                },
                hide_index=True,
                height=420
            )

    st.divider()

    # =========================================================================
    # TREND PANEL (for a specific code from selection)
    # =========================================================================
    if len(df_selected) > 0:
        trend_col1, trend_col2 = st.columns([1, 2])

        with trend_col1:
            st.subheader("Code Trends")
            # Let user pick which code to see trends for
            trend_options = df_selected['hcpcs'].tolist()
            trend_cpt = st.selectbox(
                "Select code for trend analysis",
                options=trend_options,
                index=0
            )

        with trend_col2:
            if trend_cpt:
                trend_df = get_cpt_trend_data(trend_cpt, selected_locality)

                if len(trend_df) > 0:
                    code_desc = df_selected[df_selected['hcpcs'] == trend_cpt]['description'].iloc[0]
                    st.caption(f"**{trend_cpt}** — {code_desc[:50]}")

                    trend_tab1, trend_tab2 = st.tabs(["Allowed $ Trend", "PE vs Work Mix"])

                    with trend_tab1:
                        allowed_long = trend_df.melt(
                            id_vars=['year'],
                            value_vars=['allowed_nonfacility', 'allowed_facility'],
                            var_name='Setting',
                            value_name='Allowed'
                        )
                        allowed_long['Setting'] = allowed_long['Setting'].map({
                            'allowed_nonfacility': 'Office',
                            'allowed_facility': 'Facility'
                        })

                        allowed_chart = alt.Chart(allowed_long).mark_line(point=True).encode(
                            x=alt.X('year:O', title='Year'),
                            y=alt.Y('Allowed:Q', title='Allowed $'),
                            color=alt.Color('Setting:N', scale=alt.Scale(
                                domain=['Office', 'Facility'],
                                range=[COLORS['positive'], COLORS['accent']]
                            )),
                            tooltip=['year:O', 'Setting:N', alt.Tooltip('Allowed:Q', format='$.2f')]
                        ).properties(height=220)

                        st.altair_chart(allowed_chart, use_container_width=True)

                    with trend_tab2:
                        mix_long = trend_df.melt(
                            id_vars=['year'],
                            value_vars=['work_share_nf', 'pe_share_nf'],
                            var_name='Component',
                            value_name='Share %'
                        )
                        mix_long['Component'] = mix_long['Component'].map({
                            'work_share_nf': 'Work Share',
                            'pe_share_nf': 'PE Share'
                        })

                        mix_chart = alt.Chart(mix_long).mark_line(point=True).encode(
                            x=alt.X('year:O', title='Year'),
                            y=alt.Y('Share %:Q', title='Share %', scale=alt.Scale(domain=[0, 100])),
                            color=alt.Color('Component:N', scale=alt.Scale(
                                domain=['Work Share', 'PE Share'],
                                range=[COLORS['accent'], '#e65100']
                            )),
                            tooltip=['year:O', 'Component:N', alt.Tooltip('Share %:Q', format='.1f')]
                        ).properties(height=220)

                        st.altair_chart(mix_chart, use_container_width=True)
                else:
                    st.info("No trend data available for this code")

    # =========================================================================
    # NEGOTIATION INSIGHT SUMMARY
    # =========================================================================
    if len(df_selected) > 0:
        st.divider()
        with st.expander("Negotiation Insights for Selected Codes", expanded=True):
            pe_col_name = 'pe_share_nf' if setting_focus == 'nonfacility' else 'pe_share_f'
            avg_pe = df_selected[pe_col_name].mean()
            high_pe_count = (df_selected[pe_col_name] >= 60).sum()
            high_geo_count = (df_selected['geo_sensitivity'] == 'High').sum()
            total_gap = df_selected['site_gap'].sum()
            avg_gap_pct = df_selected['site_gap_pct'].mean()

            col1, col2 = st.columns(2)

            with col1:
                st.markdown("**Portfolio Profile**")
                st.markdown(f"""
- **{len(df_selected)}** codes selected
- **{avg_pe:.0f}%** average PE intensity
- **{high_pe_count}** codes are PE-dominant (≥60%)
- **{high_geo_count}** codes with high geographic sensitivity
                """)

            with col2:
                st.markdown("**Site-of-Service Economics**")
                st.markdown(f"""
- **{format_currency(total_gap)}** total site gap across codes
- **{avg_gap_pct:.0f}%** average premium for office-based delivery
- Office setting captures more revenue on **{(df_selected['site_gap'] > 0).sum()}** of {len(df_selected)} codes
                """)

            st.markdown("---")
            st.markdown("**Key Talking Points:**")

            if avg_pe >= 60:
                st.markdown(f"""
1. **These codes are PE-intensive ({avg_pe:.0f}% average).** Medicare's payment assumes significant overhead for equipment, supplies, and staff.
2. **{high_geo_count} of {len(df_selected)} codes have high geographic sensitivity.** Your market's PE GPCI should be reflected in rates.
3. **Office-based delivery commands a {avg_gap_pct:.0f}% premium.** You're absorbing overhead that facilities bill separately.
                """)
            elif avg_pe >= 40:
                st.markdown(f"""
1. **These codes are balanced between work and PE ({avg_pe:.0f}% PE average).**
2. **Site-of-service gap averages {format_currency(df_selected['site_gap'].mean())}.** Office-based delivery still captures meaningful additional revenue.
3. **{high_geo_count} codes are geo-sensitive.** Market-adjusted rates are appropriate.
                """)
            else:
                st.markdown(f"""
1. **These codes are work-intensive ({100-avg_pe:.0f}% work share average).** Payment is primarily for physician expertise.
2. **Geographic sensitivity is lower** — PE GPCI matters less for these codes.
3. **Site gap is smaller** — less differentiation between settings.
                """)

    # =========================================================================
    # INTERPRETATION HELP
    # =========================================================================
    with st.expander("How to Read This Page"):
        st.markdown("""
**What is "Site-of-Service Gap"?**

When a procedure is done in a physician's office, Medicare pays one amount (non-facility).
When done in a hospital outpatient department, Medicare pays a lower amount to the physician
(facility) because the hospital bills separately. The "gap" is the difference.

**What does PE Share tell me?**

Practice Expense (PE) captures equipment, supplies, clinical staff, and overhead.
A high PE share means the code is "resource-intensive" — think imaging, infusions,
or procedures with expensive supplies. These codes are also more sensitive to
geographic adjustments (PE GPCI varies more than Work GPCI).

**What does Work Share tell me?**

Work RVU reflects physician time, skill, and mental effort. A high work share
means Medicare values the code primarily for the clinician's expertise, not the
resources consumed.

**Using this for negotiations:**

1. Select your key codes using the category filter and multi-select
2. Review the portfolio-level metrics to understand your cost structure
3. Use the scatter plot to see where your codes fall in the economics map
4. Reference the "Negotiation Insights" section for talking points

---

**Important caveats:**

- PE share is **not** the same as Technical Component (TC) billing — 26/TC modifiers have specific rules
- Facility vs. non-facility reflects Medicare payment methodology, not clinical appropriateness
- These are Medicare rates — commercial payers may structure differently
        """)

except Exception as e:
    st.error(f"Error loading data: {e}")
    st.info("Ensure the database is running and analytics views are created.")
