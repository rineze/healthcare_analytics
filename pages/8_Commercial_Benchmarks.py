"""
Commercial vs Medicare Benchmarks

Compare hospital price transparency data against Medicare Physician Fee Schedule
to calculate commercial rates as % of Medicare.
"""
import streamlit as st
import pandas as pd
import altair as alt
from pathlib import Path

# Add parent to path for utils
import sys
sys.path.append(str(Path(__file__).parent.parent))
from utils import get_connection, COLORS, format_currency, format_percent

st.set_page_config(page_title="Commercial Benchmarks", page_icon="", layout="wide")

st.title("Commercial vs Medicare Benchmarks")
st.markdown("Compare hospital commercial rates against Medicare fee schedule")

# -----------------------------------------------------------------------------
# Data Loading
# -----------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def get_hospitals():
    """Get list of hospitals with price transparency data."""
    conn = get_connection()
    query = """
        SELECT DISTINCT
            h.hospital_id,
            h.hospital_name,
            h.state,
            h.hospital_system,
            r.data_year,
            r.load_date
        FROM drinf.pt_hospitals h
        JOIN drinf.pt_rates r ON r.hospital_id = h.hospital_id
        ORDER BY h.hospital_name, r.data_year DESC
    """
    return pd.read_sql(query, conn)


@st.cache_data(ttl=3600)
def get_hospital_rates(hospital_id, data_year):
    """Get price transparency rates for a hospital."""
    conn = get_connection()
    query = f"""
        SELECT
            cpt_code as cpt,
            description,
            payer_name,
            plan_name,
            negotiated_rate,
            gross_charge,
            setting
        FROM drinf.pt_rates
        WHERE hospital_id = {hospital_id}
          AND data_year = {data_year}
          AND negotiated_rate IS NOT NULL
    """
    return pd.read_sql(query, conn)


@st.cache_data(ttl=3600)
def get_payers_for_hospital(hospital_id, data_year):
    """Get list of payers for a hospital."""
    conn = get_connection()
    query = f"""
        SELECT DISTINCT payer_name
        FROM drinf.pt_rates
        WHERE hospital_id = {hospital_id}
          AND data_year = {data_year}
          AND payer_name IS NOT NULL
        ORDER BY payer_name
    """
    df = pd.read_sql(query, conn)
    return df['payer_name'].tolist()


@st.cache_data(ttl=3600)
def get_localities(year):
    """Get list of Medicare localities for a year."""
    conn = get_connection()
    query = f"""
        SELECT DISTINCT locality_id, locality_name, state
        FROM drinf.v_mpfs_allowed
        WHERE year = {year}
        ORDER BY state, locality_name
    """
    return pd.read_sql(query, conn)


@st.cache_data(ttl=3600)
def get_medicare_rates(year, locality_id):
    """Get Medicare allowed amounts for a locality."""
    conn = get_connection()

    query = f"""
        SELECT
            hcpcs,
            modifier,
            description,
            allowed_nonfacility,
            allowed_facility,
            w_rvu,
            pe_rvu_nonfacility,
            pe_rvu_facility
        FROM drinf.v_mpfs_allowed
        WHERE year = {year}
          AND locality_id = '{locality_id}'
          AND modifier IS NULL
          AND allowed_nonfacility IS NOT NULL
    """
    return pd.read_sql(query, conn)


@st.cache_data(ttl=3600)
def get_available_years():
    """Get available MPFS years."""
    conn = get_connection()
    query = "SELECT DISTINCT year FROM drinf.v_mpfs_allowed ORDER BY year"
    df = pd.read_sql(query, conn)
    return df['year'].tolist()


# -----------------------------------------------------------------------------
# Load Data
# -----------------------------------------------------------------------------

hospitals_df = get_hospitals()

if len(hospitals_df) == 0:
    st.error("No hospital price transparency data found. Please run the loader script first.")
    st.stop()

years = get_available_years()

# -----------------------------------------------------------------------------
# Sidebar Controls
# -----------------------------------------------------------------------------

st.sidebar.header("Settings")

# Hospital selector
hospital_options = hospitals_df.drop_duplicates(['hospital_id', 'hospital_name', 'state'])
hospital_labels = [f"{row['hospital_name']} ({row['state']})" for _, row in hospital_options.iterrows()]
selected_hospital_idx = st.sidebar.selectbox(
    "Hospital",
    options=range(len(hospital_labels)),
    format_func=lambda x: hospital_labels[x],
    help="Select hospital with price transparency data"
)
selected_hospital = hospital_options.iloc[selected_hospital_idx]
hospital_id = int(selected_hospital['hospital_id'])
hospital_name = selected_hospital['hospital_name']

# Data year for this hospital
hospital_years = hospitals_df[hospitals_df['hospital_id'] == hospital_id]['data_year'].unique()
selected_data_year = st.sidebar.selectbox(
    "Hospital Data Year",
    options=sorted(hospital_years, reverse=True),
    help="Year of hospital price transparency data"
)

# Load hospital rates
hospital_rates = get_hospital_rates(hospital_id, selected_data_year)

selected_year = st.sidebar.selectbox(
    "Medicare Fee Schedule Year",
    options=years,
    index=len(years) - 2 if len(years) > 1 else 0,  # Default to second-to-last
    help="Select MPFS year to compare against"
)

# Locality selector
localities_df = get_localities(selected_year)
# Find default (CT or first available)
ct_localities = localities_df[localities_df['state'] == 'CT']
default_idx = ct_localities.index[0] if len(ct_localities) > 0 else 0

locality_labels = [f"{row['locality_name']} ({row['locality_id']})" for _, row in localities_df.iterrows()]
selected_locality_idx = st.sidebar.selectbox(
    "Medicare Locality",
    options=range(len(locality_labels)),
    format_func=lambda x: locality_labels[x],
    index=int(default_idx),
    help="Select Medicare locality for rate comparison"
)
selected_locality = localities_df.iloc[selected_locality_idx]
locality_id = selected_locality['locality_id']
locality_name = selected_locality['locality_name']

# Get unique payers for this hospital
payers = get_payers_for_hospital(hospital_id, selected_data_year)
selected_payer = st.sidebar.selectbox(
    "Commercial Payer",
    options=payers,
    index=0
)

# Setting for Medicare comparison
setting = st.sidebar.radio(
    "Medicare Setting",
    options=["Non-Facility", "Facility"],
    index=0,
    help="Compare against Medicare non-facility or facility rates"
)
setting_col = "allowed_nonfacility" if setting == "Non-Facility" else "allowed_facility"

# Optional CPT filter
cpt_filter = st.sidebar.text_input(
    "Filter CPT codes (optional)",
    placeholder="e.g., 99214 or 992",
    help="Filter to specific CPT codes or prefixes"
)

# Aggregation method for duplicate CPT/payer combinations
agg_method = st.sidebar.radio(
    "Rate Aggregation",
    options=["Median", "Mean", "Max", "Min"],
    index=0,
    help="How to aggregate when a payer has multiple rates for same CPT"
)

# -----------------------------------------------------------------------------
# Data Processing
# -----------------------------------------------------------------------------

# Filter to selected payer
payer_df = hospital_rates[hospital_rates['payer_name'] == selected_payer].copy()

# Apply CPT filter if provided
if cpt_filter:
    payer_df = payer_df[payer_df['cpt'].str.startswith(cpt_filter)]

# Aggregate rates by CPT (handle multiple rates per code)
agg_func = {'Median': 'median', 'Mean': 'mean', 'Max': 'max', 'Min': 'min'}[agg_method]
payer_agg = payer_df.groupby('cpt').agg({
    'negotiated_rate': agg_func,
    'gross_charge': 'first',
    'description': 'first'
}).reset_index()

# Get Medicare rates for selected year and locality
medicare_df = get_medicare_rates(selected_year, locality_id)

# Merge on CPT code
merged = payer_agg.merge(
    medicare_df,
    left_on='cpt',
    right_on='hcpcs',
    how='inner',
    suffixes=('_echn', '_medicare')
)

# Calculate % of Medicare
merged['medicare_rate'] = merged[setting_col]
merged['pct_of_medicare'] = (merged['negotiated_rate'] / merged['medicare_rate'] * 100).round(1)

# Filter out invalid comparisons
merged = merged[
    (merged['negotiated_rate'] > 0) &
    (merged['medicare_rate'] > 0) &
    (merged['pct_of_medicare'] < 1000)  # Filter outliers (likely errors)
]

# -----------------------------------------------------------------------------
# Display Results
# -----------------------------------------------------------------------------

if len(merged) == 0:
    st.warning("No matching CPT codes found between ECHN and Medicare data.")
    st.stop()

# KPIs
st.markdown("---")
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        "CPT Codes Matched",
        f"{len(merged):,}"
    )

with col2:
    median_pct = merged['pct_of_medicare'].median()
    st.metric(
        "Median % of Medicare",
        f"{median_pct:.0f}%"
    )

with col3:
    avg_pct = merged['pct_of_medicare'].mean()
    st.metric(
        "Mean % of Medicare",
        f"{avg_pct:.0f}%"
    )

with col4:
    range_pct = f"{merged['pct_of_medicare'].min():.0f}% - {merged['pct_of_medicare'].max():.0f}%"
    st.metric(
        "Range",
        range_pct
    )

st.markdown("---")

# Distribution chart
st.subheader("Distribution: Commercial as % of Medicare")

hist_chart = alt.Chart(merged).mark_bar(
    color=COLORS['accent'],
    opacity=0.7
).encode(
    alt.X('pct_of_medicare:Q',
          bin=alt.Bin(step=25),
          title='% of Medicare'),
    alt.Y('count():Q', title='Number of CPT Codes')
).properties(
    height=300
)

# Add median line
median_rule = alt.Chart(pd.DataFrame({'median': [median_pct]})).mark_rule(
    color=COLORS['negative'],
    strokeWidth=2,
    strokeDash=[5, 5]
).encode(
    x='median:Q'
)

st.altair_chart(hist_chart + median_rule, use_container_width=True)

st.caption(f"Dashed line = median ({median_pct:.0f}%)")

# Two columns: scatter plot and top codes table
col_left, col_right = st.columns([1.2, 1])

with col_left:
    st.subheader("Commercial vs Medicare Rates")

    scatter = alt.Chart(merged).mark_circle(
        size=60,
        opacity=0.6
    ).encode(
        x=alt.X('medicare_rate:Q', title=f'Medicare {setting} Rate ($)'),
        y=alt.Y('negotiated_rate:Q', title=f'{selected_payer} Rate ($)'),
        tooltip=[
            alt.Tooltip('cpt:N', title='CPT'),
            alt.Tooltip('description_medicare:N', title='Description'),
            alt.Tooltip('medicare_rate:Q', title='Medicare', format='$,.2f'),
            alt.Tooltip('negotiated_rate:Q', title='Commercial', format='$,.2f'),
            alt.Tooltip('pct_of_medicare:Q', title='% of Medicare', format='.0f')
        ],
        color=alt.condition(
            alt.datum.pct_of_medicare > 200,
            alt.value(COLORS['negative']),
            alt.value(COLORS['accent'])
        )
    ).properties(
        height=400
    )

    # Add reference lines
    max_val = max(merged['medicare_rate'].max(), merged['negotiated_rate'].max())

    # 100% line (parity)
    line_100 = alt.Chart(pd.DataFrame({
        'x': [0, max_val],
        'y': [0, max_val]
    })).mark_line(
        strokeDash=[5, 5],
        color='gray',
        opacity=0.5
    ).encode(x='x:Q', y='y:Q')

    # 150% line
    line_150 = alt.Chart(pd.DataFrame({
        'x': [0, max_val],
        'y': [0, max_val * 1.5]
    })).mark_line(
        strokeDash=[2, 2],
        color='gray',
        opacity=0.3
    ).encode(x='x:Q', y='y:Q')

    st.altair_chart(scatter + line_100 + line_150, use_container_width=True)
    st.caption("Dashed line = 100% (parity). Dotted = 150%. Red = >200% of Medicare.")

with col_right:
    st.subheader("Highest % of Medicare")

    top_codes = merged.nlargest(15, 'pct_of_medicare')[
        ['cpt', 'description_medicare', 'medicare_rate', 'negotiated_rate', 'pct_of_medicare']
    ].copy()

    top_codes.columns = ['CPT', 'Description', 'Medicare', 'Commercial', '% Medicare']
    top_codes['Description'] = top_codes['Description'].str[:30]
    top_codes['Medicare'] = top_codes['Medicare'].apply(lambda x: f"${x:,.2f}")
    top_codes['Commercial'] = top_codes['Commercial'].apply(lambda x: f"${x:,.2f}")
    top_codes['% Medicare'] = top_codes['% Medicare'].apply(lambda x: f"{x:.0f}%")

    st.dataframe(top_codes, hide_index=True, use_container_width=True)

# Full data table
st.markdown("---")
st.subheader("Full Comparison Data")

# Prepare display table
display_df = merged[['cpt', 'description_medicare', 'medicare_rate', 'negotiated_rate', 'pct_of_medicare']].copy()
display_df.columns = ['CPT', 'Description', 'Medicare Rate', 'Commercial Rate', '% of Medicare']
display_df = display_df.sort_values('% of Medicare', ascending=False)

# Format for display
display_df['Medicare Rate'] = display_df['Medicare Rate'].apply(lambda x: f"${x:,.2f}")
display_df['Commercial Rate'] = display_df['Commercial Rate'].apply(lambda x: f"${x:,.2f}")
display_df['% of Medicare'] = display_df['% of Medicare'].apply(lambda x: f"{x:.0f}%")

st.dataframe(display_df, hide_index=True, use_container_width=True, height=400)

# Download button
csv_export = merged[['cpt', 'description_medicare', 'medicare_rate', 'negotiated_rate', 'pct_of_medicare']].copy()
csv_export.columns = ['CPT', 'Description', 'Medicare_Rate', 'Commercial_Rate', 'Pct_of_Medicare']

hospital_slug = hospital_name.lower().replace(' ', '_').replace('.', '')
st.download_button(
    label="Download as CSV",
    data=csv_export.to_csv(index=False),
    file_name=f"{hospital_slug}_{selected_payer.lower().replace(' ', '_')}_vs_medicare_{selected_year}.csv",
    mime="text/csv"
)

# -----------------------------------------------------------------------------
# Insights Section
# -----------------------------------------------------------------------------

st.markdown("---")
st.subheader("Key Insights")

# Calculate some insights
high_codes = merged[merged['pct_of_medicare'] > 200]
low_codes = merged[merged['pct_of_medicare'] < 120]
em_codes = merged[merged['cpt'].str.startswith('992')]
surgical_codes = merged[(merged['cpt'].str.len() == 5) & (merged['cpt'].str[0].isin(['1', '2', '3', '4', '5', '6']))]

col1, col2 = st.columns(2)

with col1:
    st.markdown(f"""
    **Rate Summary for {selected_payer} at {hospital_name}:**
    - {len(high_codes)} codes at **>200%** of Medicare (potential outliers or high-margin services)
    - {len(low_codes)} codes at **<120%** of Medicare (near-Medicare or below)
    - Median commercial rate is **{median_pct:.0f}%** of Medicare {setting.lower()}
    """)

with col2:
    if len(em_codes) > 0:
        em_median = em_codes['pct_of_medicare'].median()
        st.markdown(f"""
        **E/M Codes (992xx):**
        - {len(em_codes)} E/M codes found
        - Median: **{em_median:.0f}%** of Medicare
        """)

    if len(surgical_codes) > 0:
        surg_median = surgical_codes['pct_of_medicare'].median()
        st.markdown(f"""
        **Surgical Codes:**
        - {len(surgical_codes)} surgical codes found
        - Median: **{surg_median:.0f}%** of Medicare
        """)

st.markdown("---")
st.caption(f"Data: {hospital_name} Price Transparency ({selected_data_year}) vs Medicare PFS {selected_year} {locality_name} ({locality_id})")
