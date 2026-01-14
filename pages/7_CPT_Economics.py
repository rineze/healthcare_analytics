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
    format_percent
)

st.set_page_config(page_title="CPT Economics", page_icon="$", layout="wide")

# Initialize session state for selected CPT
if 'selected_cpt' not in st.session_state:
    st.session_state.selected_cpt = None

st.title("CPT Economics & Site-of-Service Signals")
st.caption("Where does Medicare see the value — physician work or practice expense?")

try:
    years = get_available_years()
    localities = get_localities()
    latest_year = max(years)

    # =========================================================================
    # FILTERS (top bar)
    # =========================================================================
    filter_cols = st.columns([1, 1.5, 2, 1, 1])

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

    with filter_cols[4]:
        pe_threshold = st.slider(
            "PE Threshold %",
            min_value=40,
            max_value=80,
            value=60,
            help="Filter for PE-dominant codes"
        )

    st.divider()

    # =========================================================================
    # LOAD DATA
    # =========================================================================
    if data_mode == 'util':
        df = get_cpt_economics_with_util(selected_year, selected_locality)
        # Filter to top 300 by util for performance
        df = df.nlargest(300, 'total_medicare_dollars')
    else:
        df = get_cpt_economics_data(selected_year, selected_locality)
        # Filter to top 500 by total RVU for performance
        df = df.nlargest(500, 'total_rvu_nf')

    # =========================================================================
    # CPT SEARCH
    # =========================================================================
    search_col, spacer = st.columns([2, 3])
    with search_col:
        # Create searchable options
        df['search_label'] = df['hcpcs'] + ' - ' + df['description'].fillna('').str[:45]
        search_options = [''] + df['search_label'].tolist()

        selected_search = st.selectbox(
            "Find CPT",
            options=search_options,
            index=0,
            placeholder="Search by code or keyword..."
        )

        if selected_search:
            st.session_state.selected_cpt = selected_search.split(' - ')[0]

    # Get selected CPT data
    selected_cpt_data = None
    if st.session_state.selected_cpt:
        mask = df['hcpcs'] == st.session_state.selected_cpt
        if mask.any():
            selected_cpt_data = df[mask].iloc[0]

    # =========================================================================
    # KPI TILES
    # =========================================================================
    st.subheader("Selected Code Metrics" if selected_cpt_data is not None else "Select a CPT to see metrics")

    kpi_cols = st.columns(5)

    if selected_cpt_data is not None:
        with kpi_cols[0]:
            st.metric(
                "Office Allowed",
                format_currency(selected_cpt_data['allowed_nonfacility']),
                help="Payment when performed in physician office"
            )

        with kpi_cols[1]:
            st.metric(
                "Facility Allowed",
                format_currency(selected_cpt_data['allowed_facility']),
                help="Payment when performed in hospital/ASC"
            )

        with kpi_cols[2]:
            gap = selected_cpt_data['site_gap']
            gap_pct = selected_cpt_data['site_gap_pct']
            st.metric(
                "Site Gap",
                format_currency(gap),
                f"{gap_pct:+.1f}%" if pd.notna(gap_pct) else None,
                help="Revenue shift if moved to office setting"
            )

        with kpi_cols[3]:
            pe_share = selected_cpt_data['pe_share_nf'] if setting_focus == 'nonfacility' else selected_cpt_data['pe_share_f']
            st.metric(
                "PE Intensity",
                f"{pe_share:.1f}%",
                help="Practice expense share — higher = more equipment/staff cost"
            )

        with kpi_cols[4]:
            geo_sens = selected_cpt_data['geo_sensitivity']
            st.metric(
                "Geo Sensitivity",
                geo_sens if pd.notna(geo_sens) else "—",
                help="How much this code swings by market (based on PE share)"
            )
    else:
        for col in kpi_cols:
            with col:
                st.metric("—", "—")

    st.divider()

    # =========================================================================
    # MAIN VISUAL: CPT ECONOMICS MAP (Scatter)
    # =========================================================================
    scatter_col, leaderboard_col = st.columns([1.2, 1])

    with scatter_col:
        st.subheader("CPT Economics Map")
        st.caption("Click a point to select that CPT")

        # Prepare scatter data
        work_col = 'work_share_nf' if setting_focus == 'nonfacility' else 'work_share_f'
        pe_col = 'pe_share_nf' if setting_focus == 'nonfacility' else 'pe_share_f'
        size_col = 'total_medicare_dollars' if data_mode == 'util' else 'total_rvu_nf'

        scatter_df = df[[
            'hcpcs', 'description', work_col, pe_col, size_col,
            'allowed_nonfacility', 'allowed_facility', 'site_gap', 'econ_category'
        ]].copy()
        scatter_df = scatter_df.dropna(subset=[work_col, pe_col])

        # Color mapping
        category_colors = {
            'Work-Heavy': COLORS['accent'],
            'PE-Heavy': '#e65100',
            'Balanced': COLORS['neutral_light']
        }

        # Create scatter with selection
        selection = alt.selection_point(fields=['hcpcs'], name='select')

        scatter = alt.Chart(scatter_df).mark_circle(opacity=0.7).encode(
            x=alt.X(f'{work_col}:Q',
                    title='Work Share % →',
                    scale=alt.Scale(domain=[0, 100])),
            y=alt.Y(f'{pe_col}:Q',
                    title='↑ PE Share %',
                    scale=alt.Scale(domain=[0, 100])),
            size=alt.Size(f'{size_col}:Q',
                          title='Total RVU' if data_mode == 'mpfs' else '2023 Medicare $',
                          scale=alt.Scale(range=[20, 400]),
                          legend=None),
            color=alt.Color('econ_category:N',
                            title='Category',
                            scale=alt.Scale(
                                domain=list(category_colors.keys()),
                                range=list(category_colors.values())
                            )),
            tooltip=[
                alt.Tooltip('hcpcs:N', title='CPT'),
                alt.Tooltip('description:N', title='Description'),
                alt.Tooltip('allowed_nonfacility:Q', title='Office $', format='$.2f'),
                alt.Tooltip('allowed_facility:Q', title='Facility $', format='$.2f'),
                alt.Tooltip('site_gap:Q', title='Gap $', format='$.2f'),
                alt.Tooltip(f'{pe_col}:Q', title='PE Share %', format='.1f'),
                alt.Tooltip(f'{work_col}:Q', title='Work Share %', format='.1f'),
            ],
            strokeWidth=alt.condition(selection, alt.value(2), alt.value(0)),
            stroke=alt.condition(selection, alt.value('black'), alt.value(None))
        ).properties(
            height=400
        ).add_params(selection)

        # Reference lines for quadrants
        hline = alt.Chart(pd.DataFrame({'y': [50]})).mark_rule(
            strokeDash=[4, 4], color=COLORS['neutral_light']
        ).encode(y='y:Q')

        vline = alt.Chart(pd.DataFrame({'x': [50]})).mark_rule(
            strokeDash=[4, 4], color=COLORS['neutral_light']
        ).encode(x='x:Q')

        chart = scatter + hline + vline
        event = st.altair_chart(chart, use_container_width=True, on_select="rerun")

        # Handle selection from chart
        if event and event.selection and 'select' in event.selection:
            selected_points = event.selection['select']
            if selected_points and len(selected_points) > 0:
                selected_hcpcs = selected_points[0].get('hcpcs')
                if selected_hcpcs:
                    st.session_state.selected_cpt = selected_hcpcs
                    st.rerun()

    # =========================================================================
    # SITE-OF-SERVICE GAP LEADERBOARD
    # =========================================================================
    with leaderboard_col:
        st.subheader("Site-of-Service Gap Leaderboard")

        sort_col = 'util_gap_impact' if data_mode == 'util' and 'util_gap_impact' in df.columns else 'site_gap'
        leaderboard = df.nlargest(25, sort_col)[[
            'hcpcs', 'description', 'allowed_nonfacility', 'allowed_facility',
            'site_gap', 'site_gap_pct', 'pe_share_nf'
        ]].copy()

        leaderboard.columns = ['CPT', 'Description', 'Office $', 'Facility $', 'Gap $', 'Gap %', 'PE %']
        leaderboard['Description'] = leaderboard['Description'].str[:30]

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
    # SECONDARY: PE-DOMINANT CODES + TRENDS
    # =========================================================================
    pe_col_display, trend_col = st.columns([1, 1.2])

    with pe_col_display:
        st.subheader(f"PE-Dominant Codes (≥{pe_threshold}%)")

        pe_filter_col = 'pe_share_nf' if setting_focus == 'nonfacility' else 'pe_share_f'
        pe_dominant = df[df[pe_filter_col] >= pe_threshold].nlargest(20, pe_filter_col)[[
            'hcpcs', 'description', pe_filter_col, 'site_gap', 'geo_sensitivity'
        ]].copy()

        pe_dominant.columns = ['CPT', 'Description', 'PE %', 'Gap $', 'Geo Sens']
        pe_dominant['Description'] = pe_dominant['Description'].str[:30]

        st.dataframe(
            pe_dominant,
            column_config={
                'PE %': st.column_config.NumberColumn(format="%.1f%%"),
                'Gap $': st.column_config.NumberColumn(format="$%.2f"),
            },
            hide_index=True,
            height=350
        )

    # =========================================================================
    # TREND PANEL (for selected CPT)
    # =========================================================================
    with trend_col:
        st.subheader("Selected CPT Trends")

        if st.session_state.selected_cpt:
            trend_df = get_cpt_trend_data(st.session_state.selected_cpt, selected_locality)

            if len(trend_df) > 0:
                st.caption(f"**{st.session_state.selected_cpt}** — {selected_cpt_data['description'][:50] if selected_cpt_data is not None else ''}")

                trend_tab1, trend_tab2, trend_tab3 = st.tabs(["Allowed $", "PE RVU", "Mix %"])

                with trend_tab1:
                    # Allowed $ trend
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
                    ).properties(height=250)

                    st.altair_chart(allowed_chart, use_container_width=True)
                    st.caption("Look for: widening or narrowing gap over time")

                with trend_tab2:
                    # PE RVU trend
                    pe_long = trend_df.melt(
                        id_vars=['year'],
                        value_vars=['pe_rvu_nonfacility', 'pe_rvu_facility'],
                        var_name='Setting',
                        value_name='PE RVU'
                    )
                    pe_long['Setting'] = pe_long['Setting'].map({
                        'pe_rvu_nonfacility': 'Non-Facility PE',
                        'pe_rvu_facility': 'Facility PE'
                    })

                    pe_chart = alt.Chart(pe_long).mark_line(point=True).encode(
                        x=alt.X('year:O', title='Year'),
                        y=alt.Y('PE RVU:Q', title='PE RVU'),
                        color=alt.Color('Setting:N', scale=alt.Scale(
                            domain=['Non-Facility PE', 'Facility PE'],
                            range=[COLORS['positive'], COLORS['accent']]
                        )),
                        tooltip=['year:O', 'Setting:N', alt.Tooltip('PE RVU:Q', format='.2f')]
                    ).properties(height=250)

                    st.altair_chart(pe_chart, use_container_width=True)

                with trend_tab3:
                    # Mix trend
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
                    ).properties(height=250)

                    st.altair_chart(mix_chart, use_container_width=True)
                    st.caption("Look for: shifts in how Medicare values work vs PE over time")
            else:
                st.info("No trend data available for this code")
        else:
            st.info("Select a CPT from the search box or click a point on the scatter plot")

    # =========================================================================
    # UTIL-WEIGHTED KPI (if applicable)
    # =========================================================================
    if data_mode == 'util' and 'util_gap_impact' in df.columns:
        st.divider()
        total_gap_impact = df['util_gap_impact'].sum()
        st.metric(
            "Volume-Weighted Gap Impact (displayed codes)",
            f"${total_gap_impact:,.0f}",
            help="Sum of (2023 units × site gap) — total $ at stake if all moved to office setting"
        )

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

**Example insights:**

- A code with 70% PE share and a $50 site gap → strong candidate for office-based
  delivery if clinically appropriate
- A code shifting from 60% PE to 50% PE over 3 years → Medicare is revaluing
  toward physician work
- A high-PE code in a high-GPCI market → expect above-average reimbursement

---

**Important caveats:**

- PE share is **not** the same as Technical Component (TC) billing — 26/TC modifiers have specific rules
- Facility vs. non-facility reflects Medicare payment methodology, not clinical appropriateness
- These are Medicare rates — commercial payers may structure differently
        """)

except Exception as e:
    st.error(f"Error loading data: {e}")
    st.info("Ensure the database is running and analytics views are created.")
