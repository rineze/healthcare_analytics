"""
Page 4: Locality Map
Interactive county-level map showing Medicare payment localities
Filter by locality to see which counties are included
"""
import streamlit as st
import pandas as pd
import plotly.express as px
from urllib.request import urlopen
import json
from utils import (
    get_available_years,
    get_code_list,
    get_locality_spread,
    get_gpci_rankings,
    get_localities,
    COLORS,
    format_currency
)

st.set_page_config(page_title="Locality Map", page_icon="$", layout="wide")

st.title("Medicare Locality Map")
st.caption("Visualize Medicare payment localities by county")

# =============================================================================
# County FIPS to Locality Mapping
# Based on CMS 2024 County-to-Locality Crosswalk (pfslocco)
# =============================================================================

# Maps state -> county_name -> locality_id (in database format like "CA-18")
COUNTY_TO_LOCALITY = {
    # Alabama - Statewide
    "AL": {"_default": "AL-00"},

    # Alaska - Statewide
    "AK": {"_default": "AK-01"},

    # Arizona - Statewide
    "AZ": {"_default": "AZ-00"},

    # Arkansas - Statewide
    "AR": {"_default": "AR-13"},

    # California - 29 localities
    "CA": {
        "LOS ANGELES": "CA-18", "ORANGE": "CA-18",
        "MARIN": "CA-52",
        "SAN FRANCISCO": "CA-05", "ALAMEDA": "CA-05", "CONTRA COSTA": "CA-05", "SAN MATEO": "CA-05",
        "SANTA CLARA": "CA-09",
        "NAPA": "CA-51",
        "SOLANO": "CA-53",
        "KERN": "CA-54",
        "BUTTE": "CA-55",
        "FRESNO": "CA-56",
        "KINGS": "CA-57",
        "MADERA": "CA-58",
        "MERCED": "CA-59",
        "STANISLAUS": "CA-60",
        "SHASTA": "CA-61",
        "SAN BERNARDINO": "CA-62", "RIVERSIDE": "CA-62",
        "SACRAMENTO": "CA-63", "PLACER": "CA-63", "YOLO": "CA-63", "EL DORADO": "CA-63",
        "MONTEREY": "CA-64",
        "SAN BENITO": "CA-65",
        "SANTA CRUZ": "CA-66",
        "SONOMA": "CA-67",
        "SAN JOAQUIN": "CA-68",
        "TULARE": "CA-69",
        "SUTTER": "CA-70", "YUBA": "CA-70",
        "IMPERIAL": "CA-71",
        "SAN DIEGO": "CA-72",
        "SAN LUIS OBISPO": "CA-73",
        "SANTA BARBARA": "CA-74",
        "VENTURA": "CA-17",
        "_default": "CA-75"  # Rest of State
    },

    # Colorado - Statewide
    "CO": {"_default": "CO-01"},

    # Connecticut - Statewide
    "CT": {"_default": "CT-00"},

    # Delaware - Statewide
    "DE": {"_default": "DE-01"},

    # DC - DC/MD/VA Suburbs
    "DC": {"_default": "DC-01"},

    # Florida - 3 localities
    "FL": {
        "BROWARD": "FL-03", "COLLIER": "FL-03", "INDIAN RIVER": "FL-03",
        "LEE": "FL-03", "MARTIN": "FL-03", "PALM BEACH": "FL-03", "ST. LUCIE": "FL-03",
        "MIAMI-DADE": "FL-04", "DADE": "FL-04", "MONROE": "FL-04",
        "_default": "FL-99"  # Rest of State
    },

    # Georgia - 2 localities
    "GA": {
        "BUTTS": "GA-01", "CHEROKEE": "GA-01", "CLAYTON": "GA-01", "COBB": "GA-01",
        "DEKALB": "GA-01", "DOUGLAS": "GA-01", "FAYETTE": "GA-01", "FULTON": "GA-01",
        "GWINNETT": "GA-01", "HENRY": "GA-01", "NEWTON": "GA-01", "PAULDING": "GA-01",
        "ROCKDALE": "GA-01", "SPALDING": "GA-01",
        "_default": "GA-99"  # Rest of State
    },

    # Hawaii - Statewide
    "HI": {"_default": "HI-01"},

    # Idaho - Statewide
    "ID": {"_default": "ID-00"},

    # Illinois - 4 localities
    "IL": {
        "COOK": "IL-16",  # Chicago
        "DUPAGE": "IL-15", "KANE": "IL-15", "LAKE": "IL-15", "WILL": "IL-15",  # Suburban Chicago
        "BOND": "IL-12", "CALHOUN": "IL-12", "CLINTON": "IL-12", "JERSEY": "IL-12",
        "MACOUPIN": "IL-12", "MADISON": "IL-12", "MONROE": "IL-12", "RANDOLPH": "IL-12",
        "ST. CLAIR": "IL-12", "WASHINGTON": "IL-12",  # East St. Louis
        "_default": "IL-99"  # Rest of State
    },

    # Indiana - Statewide
    "IN": {"_default": "IN-00"},

    # Iowa - Statewide
    "IA": {"_default": "IA-00"},

    # Kansas - Statewide
    "KS": {"_default": "KS-00"},

    # Kentucky - Statewide
    "KY": {"_default": "KY-00"},

    # Louisiana - 2 localities
    "LA": {
        "JEFFERSON": "LA-01", "ORLEANS": "LA-01", "PLAQUEMINES": "LA-01", "ST. BERNARD": "LA-01",
        "_default": "LA-99"  # Rest of State
    },

    # Maine - 2 localities
    "ME": {
        "CUMBERLAND": "ME-03", "YORK": "ME-03",  # Southern Maine
        "_default": "ME-99"  # Rest of State
    },

    # Maryland - 2 localities (plus DC suburbs handled via DC)
    "MD": {
        "ANNE ARUNDEL": "MD-01", "BALTIMORE": "MD-01", "BALTIMORE CITY": "MD-01",
        "CARROLL": "MD-01", "HARFORD": "MD-01", "HOWARD": "MD-01",
        "MONTGOMERY": "DC-01", "PRINCE GEORGE'S": "DC-01",  # DC suburbs
        "_default": "MD-99"  # Rest of State
    },

    # Massachusetts - 2 localities
    "MA": {
        "MIDDLESEX": "MA-01", "NORFOLK": "MA-01", "SUFFOLK": "MA-01",  # Metro Boston
        "_default": "MA-99"  # Rest of State
    },

    # Michigan - 2 localities
    "MI": {
        "MACOMB": "MI-01", "OAKLAND": "MI-01", "WASHTENAW": "MI-01", "WAYNE": "MI-01",  # Detroit
        "_default": "MI-99"  # Rest of State
    },

    # Minnesota - Statewide
    "MN": {"_default": "MN-00"},

    # Mississippi - Statewide
    "MS": {"_default": "MS-00"},

    # Missouri - 3 localities
    "MO": {
        "CLAY": "MO-02", "JACKSON": "MO-02", "PLATTE": "MO-02",  # Kansas City
        "JEFFERSON": "MO-01", "ST. CHARLES": "MO-01", "ST. LOUIS": "MO-01", "ST. LOUIS CITY": "MO-01",  # St. Louis
        "_default": "MO-99"  # Rest of State
    },

    # Montana - Statewide
    "MT": {"_default": "MT-01"},

    # Nebraska - Statewide
    "NE": {"_default": "NE-00"},

    # Nevada - Statewide
    "NV": {"_default": "NV-00"},

    # New Hampshire - Statewide
    "NH": {"_default": "NH-40"},

    # New Jersey - 2 localities
    "NJ": {
        "BERGEN": "NJ-01", "ESSEX": "NJ-01", "HUDSON": "NJ-01", "HUNTERDON": "NJ-01",
        "MIDDLESEX": "NJ-01", "MORRIS": "NJ-01", "PASSAIC": "NJ-01", "SOMERSET": "NJ-01",
        "SUSSEX": "NJ-01", "UNION": "NJ-01", "WARREN": "NJ-01",  # Northern NJ
        "_default": "NJ-99"  # Rest of State
    },

    # New Mexico - Statewide
    "NM": {"_default": "NM-05"},

    # New York - 5 localities
    "NY": {
        "NEW YORK": "NY-01",  # Manhattan
        "BRONX": "NY-02", "KINGS": "NY-02", "NASSAU": "NY-02", "RICHMOND": "NY-02",
        "ROCKLAND": "NY-02", "SUFFOLK": "NY-02", "WESTCHESTER": "NY-02",  # NYC Suburbs/Long Island
        "COLUMBIA": "NY-03", "DELAWARE": "NY-03", "DUTCHESS": "NY-03", "GREENE": "NY-03",
        "ORANGE": "NY-03", "PUTNAM": "NY-03", "SULLIVAN": "NY-03", "ULSTER": "NY-03",  # Poughkeepsie/N NYC Suburbs
        "QUEENS": "NY-04",
        "_default": "NY-99"  # Rest of State
    },

    # North Carolina - Statewide
    "NC": {"_default": "NC-00"},

    # North Dakota - Statewide
    "ND": {"_default": "ND-01"},

    # Ohio - Statewide
    "OH": {"_default": "OH-00"},

    # Oklahoma - Statewide
    "OK": {"_default": "OK-00"},

    # Oregon - 2 localities
    "OR": {
        "CLACKAMAS": "OR-01", "MULTNOMAH": "OR-01", "WASHINGTON": "OR-01",  # Portland
        "_default": "OR-99"  # Rest of State
    },

    # Pennsylvania - 2 localities
    "PA": {
        "BUCKS": "PA-01", "CHESTER": "PA-01", "DELAWARE": "PA-01",
        "MONTGOMERY": "PA-01", "PHILADELPHIA": "PA-01",  # Metro Philadelphia
        "_default": "PA-99"  # Rest of State
    },

    # Rhode Island - Statewide
    "RI": {"_default": "RI-01"},

    # South Carolina - Statewide
    "SC": {"_default": "SC-01"},

    # South Dakota - Statewide
    "SD": {"_default": "SD-02"},

    # Tennessee - Statewide
    "TN": {"_default": "TN-35"},

    # Texas - 8 localities
    "TX": {
        "TRAVIS": "TX-31",  # Austin
        "JEFFERSON": "TX-20",  # Beaumont
        "BRAZORIA": "TX-09",
        "DALLAS": "TX-11",
        "TARRANT": "TX-28",  # Fort Worth
        "GALVESTON": "TX-15",
        "HARRIS": "TX-18",  # Houston
        "_default": "TX-99"  # Rest of State
    },

    # Utah - Statewide
    "UT": {"_default": "UT-09"},

    # Vermont - Statewide
    "VT": {"_default": "VT-50"},

    # Virginia - Statewide (except DC suburbs)
    "VA": {
        "ALEXANDRIA": "DC-01", "ARLINGTON": "DC-01", "FAIRFAX": "DC-01",
        "FALLS CHURCH": "DC-01", "LOUDOUN": "DC-01", "PRINCE WILLIAM": "DC-01",
        "_default": "VA-00"  # Statewide
    },

    # Washington - 2 localities
    "WA": {
        "KING": "WA-02",  # Seattle
        "_default": "WA-99"  # Rest of State
    },

    # West Virginia - Statewide
    "WV": {"_default": "WV-16"},

    # Wisconsin - Statewide
    "WI": {"_default": "WI-00"},

    # Wyoming - Statewide
    "WY": {"_default": "WY-21"},

    # Puerto Rico
    "PR": {"_default": "PR-20"},

    # Virgin Islands
    "VI": {"_default": "VI-50"}
}


@st.cache_data(ttl=86400)  # Cache for 24 hours
def load_county_fips():
    """Load county FIPS codes from GitHub (cached heavily)."""
    url = "https://raw.githubusercontent.com/kjhealy/fips-codes/master/state_and_county_fips_master.csv"
    df = pd.read_csv(url)
    df = df[df['state'].notna()].copy()
    df['county_clean'] = df['name'].str.upper().str.replace(' COUNTY', '', regex=False).str.replace(' PARISH', '', regex=False).str.strip()
    df['fips'] = df['fips'].astype(str).str.zfill(5)
    return df


def get_locality_for_county(state, county_name):
    """Get the CMS locality ID for a given state and county."""
    state_map = COUNTY_TO_LOCALITY.get(state, {})
    county_upper = county_name.upper().replace(' COUNTY', '').replace(' PARISH', '').strip()

    locality = state_map.get(county_upper)
    if locality:
        return locality
    return state_map.get('_default', 'UNKNOWN')


@st.cache_data(ttl=3600)
def build_county_locality_mapping():
    """Build complete county FIPS to locality mapping."""
    fips_df = load_county_fips()
    fips_df['locality_id'] = fips_df.apply(
        lambda row: get_locality_for_county(row['state'], row['county_clean']),
        axis=1
    )
    return fips_df[['fips', 'name', 'state', 'county_clean', 'locality_id']]


@st.cache_data(ttl=86400)  # Cache for 24 hours
def load_counties_geojson():
    """Load US counties GeoJSON from Plotly's GitHub (cached heavily)."""
    with urlopen('https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json') as response:
        return json.load(response)


# =============================================================================
# Main App
# =============================================================================

try:
    years = get_available_years()
    latest_year = max(years)

    # Get localities from database (fast - just DB query)
    localities_df = get_localities()
    locality_options = ["All Localities"] + sorted(localities_df['locality_id'].tolist())
    locality_names = dict(zip(localities_df['locality_id'], localities_df['locality_name']))

    # ==========================================================================
    # Sidebar
    # ==========================================================================

    st.sidebar.header("Filters")

    selected_year = st.sidebar.selectbox(
        "Year",
        options=sorted(years, reverse=True),
        index=0
    )

    selected_locality = st.sidebar.selectbox(
        "Locality",
        options=locality_options,
        index=0,
        format_func=lambda x: f"{x} - {locality_names.get(x, 'All')}" if x != "All Localities" else "All Localities"
    )

    st.sidebar.markdown("---")

    # Code selection for payment view
    st.sidebar.header("Payment Analysis")

    codes_df = get_code_list(year=latest_year, payable_only=True)
    code_options = ["None"] + codes_df['hcpcs_mod'].tolist()
    code_descriptions = dict(zip(codes_df['hcpcs_mod'], codes_df['description']))

    default_idx = code_options.index('70553') if '70553' in code_options else 0

    selected_code = st.sidebar.selectbox(
        "CPT Code (optional)",
        options=code_options,
        index=default_idx,
        format_func=lambda x: f"{x} - {code_descriptions.get(x, '')[:30]}" if x != "None" else "None - Show GPCI only"
    )

    if selected_code != "None":
        setting = st.sidebar.radio(
            "Payment Setting",
            options=['nonfacility', 'facility'],
            format_func=lambda x: x.replace('nonfacility', 'Non-Facility').replace('facility', 'Facility')
        )

    st.sidebar.markdown("---")
    st.sidebar.caption("Data: CMS Medicare Physician Fee Schedule")
    st.sidebar.caption("Locality mapping: CMS 2024 pfslocco crosswalk")

    # ==========================================================================
    # Get GPCI Data
    # ==========================================================================

    gpci_data = get_gpci_rankings(selected_year)

    # ==========================================================================
    # Filter county mapping based on selected locality
    # Only load heavy geographic data when a specific locality is selected
    # ==========================================================================

    show_county_map = selected_locality != "All Localities"

    if show_county_map:
        # Load geographic data only when needed
        with st.spinner("Loading county boundaries..."):
            counties_geojson = load_counties_geojson()
            county_mapping = build_county_locality_mapping()

        filtered_counties = county_mapping[county_mapping['locality_id'] == selected_locality].copy()
        locality_name = locality_names.get(selected_locality, selected_locality)
        map_title = f"{locality_name} ({selected_locality})"

        # Merge with GPCI data for hover info
        filtered_counties = filtered_counties.merge(
            gpci_data[['locality_id', 'locality_name', 'gpci_work', 'gpci_pe', 'gpci_mp']],
            on='locality_id',
            how='left'
        )
    else:
        map_title = f"All Medicare Payment Localities ({selected_year})"

    # ==========================================================================
    # Display Metrics
    # ==========================================================================

    if selected_locality != "All Localities":
        # Show locality-specific metrics
        loc_gpci = gpci_data[gpci_data['locality_id'] == selected_locality]

        if len(loc_gpci) > 0:
            row = loc_gpci.iloc[0]
            st.subheader(f"{row['locality_name']}")

            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Work GPCI", f"{row['gpci_work']:.4f}")
            with col2:
                st.metric("PE GPCI", f"{row['gpci_pe']:.4f}")
            with col3:
                st.metric("MP GPCI", f"{row['gpci_mp']:.4f}")
            with col4:
                composite = (row['gpci_work'] + row['gpci_pe'] + row['gpci_mp']) / 3
                st.metric("Composite", f"{composite:.4f}")

            # County count
            county_count = len(filtered_counties) if show_county_map else 0
            st.caption(f"**{county_count} counties** in this locality")
    else:
        st.subheader("All Medicare Payment Localities")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Localities", len(gpci_data))
        with col2:
            # Count states with multiple localities
            multi_loc_states = gpci_data.groupby('state').size()
            multi_count = (multi_loc_states > 1).sum()
            st.metric("States w/ Multiple Localities", multi_count)
        with col3:
            single_count = (multi_loc_states == 1).sum()
            st.metric("Statewide Localities", single_count)

    st.markdown("---")

    # ==========================================================================
    # Map
    # ==========================================================================

    st.subheader(map_title)

    if show_county_map and len(filtered_counties) > 0:
        # County-level choropleth for selected locality
        fig = px.choropleth(
            filtered_counties,
            geojson=counties_geojson,
            locations='fips',
            color='locality_id',
            scope="usa",
            hover_data={
                'fips': False,
                'name': True,
                'state': True,
                'locality_id': True,
                'locality_name': True,
                'gpci_work': ':.4f',
                'gpci_pe': ':.4f',
                'gpci_mp': ':.4f'
            },
            color_discrete_sequence=[COLORS.get('primary', '#1f77b4')]
        )

        fig.update_layout(
            geo=dict(
                showlakes=True,
                lakecolor='rgb(255, 255, 255)',
                showland=True,
                landcolor='rgb(240, 240, 240)'
            ),
            margin={"r": 0, "t": 10, "l": 0, "b": 0},
            height=500,
            showlegend=False
        )

        fig.update_geos(fitbounds="locations")
        st.plotly_chart(fig, use_container_width=True)

    elif not show_county_map:
        # State-level view for "All Localities" (much faster)
        # Show average GPCI by state
        state_gpci = gpci_data.groupby('state').agg({
            'gpci_work': 'mean',
            'gpci_pe': 'mean',
            'gpci_mp': 'mean',
            'locality_id': 'count'
        }).reset_index()
        state_gpci.columns = ['state', 'gpci_work', 'gpci_pe', 'gpci_mp', 'num_localities']
        state_gpci['gpci_composite'] = (state_gpci['gpci_work'] + state_gpci['gpci_pe'] + state_gpci['gpci_mp']) / 3

        fig = px.choropleth(
            state_gpci,
            locations='state',
            locationmode='USA-states',
            color='gpci_composite',
            color_continuous_scale='RdYlBu_r',
            scope="usa",
            labels={'gpci_composite': 'Avg GPCI'},
            hover_data={
                'state': True,
                'gpci_work': ':.4f',
                'gpci_pe': ':.4f',
                'gpci_mp': ':.4f',
                'num_localities': True
            }
        )

        fig.update_layout(
            geo=dict(
                showlakes=True,
                lakecolor='rgb(255, 255, 255)'
            ),
            margin={"r": 0, "t": 10, "l": 0, "b": 0},
            height=450
        )

        st.plotly_chart(fig, use_container_width=True)
        st.caption("*State-level view shows average GPCI. Select a specific locality to see county boundaries.*")

    else:
        st.warning("No counties found for selected locality")

    # ==========================================================================
    # Payment Analysis (if code selected)
    # ==========================================================================

    if selected_code != "None":
        st.markdown("---")
        st.subheader(f"Payment Analysis: {selected_code}")
        st.caption(code_descriptions.get(selected_code, ''))

        spread_data = get_locality_spread(selected_code, selected_year, setting)

        if len(spread_data) > 0:
            # Filter to selected locality if applicable
            if show_county_map:
                loc_payment = spread_data[spread_data['locality_id'] == selected_locality]

                if len(loc_payment) > 0:
                    row = loc_payment.iloc[0]
                    col1, col2 = st.columns(2)
                    with col1:
                        st.metric(
                            f"Allowed ({setting.title()})",
                            format_currency(row['allowed'])
                        )
                    with col2:
                        # Compare to national average
                        national_avg = spread_data['allowed'].mean()
                        diff_pct = ((row['allowed'] - national_avg) / national_avg) * 100
                        st.metric(
                            "vs National Average",
                            f"{diff_pct:+.1f}%",
                            delta=f"{diff_pct:+.1f}%"
                        )
                else:
                    st.info("No payment data for this code in selected locality")
            else:
                # Show all localities
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Highest", format_currency(spread_data['allowed'].max()))
                with col2:
                    st.metric("Lowest", format_currency(spread_data['allowed'].min()))
                with col3:
                    st.metric("Average", format_currency(spread_data['allowed'].mean()))
                with col4:
                    spread_val = spread_data['allowed'].max() - spread_data['allowed'].min()
                    st.metric("Spread", format_currency(spread_val))

                # Payment table
                st.markdown("**Payment by Locality:**")
                display_df = spread_data[['locality_name', 'state', 'allowed', 'gpci_work', 'gpci_pe', 'gpci_mp']].copy()
                display_df.columns = ['Locality', 'State', 'Allowed', 'Work GPCI', 'PE GPCI', 'MP GPCI']
                display_df = display_df.sort_values('Allowed', ascending=False)
                st.dataframe(display_df, hide_index=True, use_container_width=True, height=300)
        else:
            st.warning("No payment data available for this code")

    # ==========================================================================
    # County List (when locality selected)
    # ==========================================================================

    if show_county_map and len(filtered_counties) > 0:
        st.markdown("---")
        st.subheader("Counties in this Locality")

        county_list = filtered_counties[['name', 'state']].copy()
        county_list.columns = ['County', 'State']
        county_list = county_list.sort_values(['State', 'County'])

        st.dataframe(county_list, hide_index=True, use_container_width=True)

        # Export
        csv = county_list.to_csv(index=False)
        st.download_button(
            label="Download County List (CSV)",
            data=csv,
            file_name=f"{selected_locality}_counties.csv",
            mime="text/csv"
        )

except Exception as e:
    st.error(f"Error loading data: {e}")
    st.info("Ensure the database is running and analytics views are created.")
    import traceback
    st.code(traceback.format_exc())

# =============================================================================
# Footer
# =============================================================================

st.markdown("---")
st.caption("""
**About Medicare Payment Localities:**
CMS divides the country into 109 payment localities, each with unique Geographic Practice Cost Indices (GPCIs).
- **37 states** have a single statewide locality
- **16 states** have multiple localities (typically metro area vs. rest of state)
- **California** has the most with 29 distinct localities

Select a specific locality above to see which counties are included and view locality-specific payment data.
""")
