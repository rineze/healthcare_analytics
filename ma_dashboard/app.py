"""
MA Market Share Dashboard — MVP

Single-page layout:
- Top: County choropleth map colored by MA enrollment
- Bottom: KPI row + org treemap + detail table

Data sourced from PostgreSQL (drinf schema).
"""

import streamlit as st
import pandas as pd
import plotly.express as px
from urllib.request import urlopen
import json

from data_loader import (
    load_all_data,
    get_county_map_data,
    get_available_months,
    STATE_ABBREV,
)

# Reverse lookup: abbreviation -> full name
ABBREV_STATE = {v: k for k, v in STATE_ABBREV.items()}

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="MA Market Share Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Load GeoJSON for county choropleth (cached)
# ---------------------------------------------------------------------------

@st.cache_data
def load_counties_geojson():
    with urlopen(
        "https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json"
    ) as response:
        return json.load(response)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def format_number(n):
    if pd.isna(n):
        return "N/A"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{int(n):,}"


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

st.sidebar.title("MA Market Share")

# Report month (use most recent by default)
months = get_available_months()
if not months:
    st.error("No data loaded. Run load_ma_data.py first.")
    st.stop()

# Plan type filter
plan_type = st.sidebar.radio("Plan Type", ["All", "Individual", "Group"], index=0)

# Load data
county_map_df = get_county_map_data(months[0])
enrollment_by_org, penetration = load_all_data(months[0])

# Filter by plan type for enrollment_by_org
if plan_type != "All":
    enrollment_by_org = enrollment_by_org[enrollment_by_org["plan_category"] == plan_type]

# Plan name filter (populated from loaded data)
all_org_names = sorted(enrollment_by_org["org_name"].dropna().unique())
selected_plans = st.sidebar.multiselect(
    "Plan Name (optional)",
    all_org_names,
    placeholder="Search by plan name...",
)
if selected_plans:
    enrollment_by_org = enrollment_by_org[enrollment_by_org["org_name"].isin(selected_plans)]

# State multi-select
all_states = sorted(county_map_df["state"].dropna().unique())
selected_states = st.sidebar.multiselect(
    "States (leave empty for all US)",
    all_states,
)

# Filter map data by states
if selected_states:
    map_data = county_map_df[county_map_df["state"].isin(selected_states)]
    org_data = enrollment_by_org[enrollment_by_org["state"].isin(selected_states)]
else:
    map_data = county_map_df
    org_data = enrollment_by_org

# County multi-select (populated after state selection)
available_counties = sorted(map_data["county"].dropna().unique())
selected_counties = st.sidebar.multiselect(
    "Counties (optional)",
    available_counties,
)

# Track selected county for treemap focus
if selected_counties:
    org_data = org_data[org_data["county"].isin(selected_counties)]

# Geography summary
st.sidebar.markdown("---")
if selected_counties:
    geo_label = ", ".join(selected_counties)
elif selected_states:
    geo_label = ", ".join(selected_states)
else:
    geo_label = "All US"
st.sidebar.markdown(f"**Selected:** {geo_label}")
st.sidebar.caption(
    "Data source: CMS Medicare Advantage enrollment files. Updated monthly."
)


# ---------------------------------------------------------------------------
# SECTION 1: County Choropleth Map
# ---------------------------------------------------------------------------

st.title("MA Market Share Dashboard")

# Clean FIPS for map
map_display = map_data.dropna(subset=["fips"]).copy()
map_display = map_display[map_display["fips"].str.len() == 5]

if not map_display.empty:
    counties_geojson = load_counties_geojson()

    # Build hover text
    map_display["hover_text"] = (
        map_display["county"] + ", " + map_display["state"]
        + "<br>Enrollment: " + map_display["enrollment"].apply(lambda x: f"{x:,}")
        + "<br>Penetration: " + map_display["penetration_rate"].apply(
            lambda x: f"{x:.1f}%" if pd.notna(x) else "N/A"
        )
        + "<br>Top Org: " + map_display["top_org"].fillna("N/A")
        + " (" + map_display["top_org_share"].apply(
            lambda x: f"{x:.1f}%" if pd.notna(x) else "N/A"
        ) + ")"
    )

    fig_map = px.choropleth(
        map_display,
        geojson=counties_geojson,
        locations="fips",
        color="enrollment",
        color_continuous_scale="YlOrRd",
        scope="usa",
        hover_name="hover_text",
        labels={"enrollment": "MA Enrollment"},
    )
    fig_map.update_traces(
        hovertemplate="%{hovertext}<extra></extra>",
        hovertext=map_display["hover_text"],
        marker_line_width=0.3,
        marker_line_color="white",
    )

    # Zoom to selected states if any
    if selected_states:
        fig_map.update_geos(fitbounds="locations", visible=False)
    else:
        fig_map.update_geos(visible=False)

    fig_map.update_layout(
        height=520,
        margin=dict(l=0, r=0, t=0, b=0),
        coloraxis_colorbar=dict(
            title="Enrollment",
            thickness=15,
            len=0.6,
        ),
    )

    # Use on_select for county clicking (Streamlit 1.37+)
    event = st.plotly_chart(
        fig_map,
        use_container_width=True,
        on_select="rerun",
        key="county_map",
    )

    # Handle map click -> update county selection
    if event and event.selection and event.selection.points:
        clicked_idx = event.selection.points[0].get("point_index")
        if clicked_idx is not None and clicked_idx < len(map_display):
            clicked_row = map_display.iloc[clicked_idx]
            clicked_county = clicked_row["county"]
            clicked_state = clicked_row["state"]
            # Filter org_data to clicked county
            if clicked_county not in (selected_counties or []):
                org_data = enrollment_by_org[
                    (enrollment_by_org["state"] == clicked_state)
                    & (enrollment_by_org["county"] == clicked_county)
                ]
                if plan_type != "All":
                    org_data = org_data[org_data["plan_category"] == plan_type]
                geo_label = f"{clicked_county}, {clicked_state}"

else:
    st.warning("No county FIPS data available for map display.")


# ---------------------------------------------------------------------------
# SECTION 2: KPI Row + Treemap + Detail Table
# ---------------------------------------------------------------------------

st.markdown("---")

# --- KPI Row ---
total_enrollment = org_data["enrollment"].sum()
num_orgs = org_data["org_name"].nunique()

# Top org
org_totals = org_data.groupby("org_name")["enrollment"].sum()
if len(org_totals) > 0:
    top_org_name = org_totals.idxmax()
    top_org_enrollment = org_totals.max()
    top_org_share = (top_org_enrollment / total_enrollment * 100) if total_enrollment > 0 else 0
else:
    top_org_name = "N/A"
    top_org_share = 0

# Penetration rate (weighted average across selected geography)
if selected_counties or (selected_states and len(selected_states) < len(all_states)):
    # Use map_data which has penetration info
    if selected_counties:
        pen_subset = map_data[map_data["county"].isin(selected_counties)]
    else:
        pen_subset = map_data
    valid_pen = pen_subset.dropna(subset=["penetration_rate", "eligibles"])
    if len(valid_pen) > 0 and valid_pen["eligibles"].sum() > 0:
        avg_penetration = (
            (valid_pen["penetration_rate"] * valid_pen["eligibles"]).sum()
            / valid_pen["eligibles"].sum()
        )
    else:
        avg_penetration = None
else:
    valid_pen = map_data.dropna(subset=["penetration_rate", "eligibles"])
    if len(valid_pen) > 0 and valid_pen["eligibles"].sum() > 0:
        avg_penetration = (
            (valid_pen["penetration_rate"] * valid_pen["eligibles"]).sum()
            / valid_pen["eligibles"].sum()
        )
    else:
        avg_penetration = None

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total MA Enrollment", format_number(total_enrollment))
col2.metric(
    "MA Penetration Rate",
    f"{avg_penetration:.1f}%" if avg_penetration is not None else "N/A",
)
col3.metric("Organizations", f"{num_orgs:,}")
col4.metric(
    "Top Organization",
    top_org_name if top_org_name != "N/A" else "N/A",
    delta=f"{top_org_share:.1f}% share" if top_org_name != "N/A" else None,
    delta_color="off",
)

st.caption(f"Geography: **{geo_label}** | Plan Type: **{plan_type}**")

# --- Treemap ---
if selected_plans:
    plan_label = selected_plans[0] if len(selected_plans) == 1 else f"{len(selected_plans)} Plans"
    st.subheader(f"Geographic Breakdown — {plan_label}")
    treemap_data = (
        org_data.groupby(["state", "county"])["enrollment"]
        .sum()
        .reset_index()
        .sort_values("enrollment", ascending=False)
    )
    treemap_data["org_name"] = treemap_data["county"] + ", " + treemap_data["state"]
else:
    st.subheader("Organization Market Share")
    treemap_data = (
        org_data.groupby("org_name")["enrollment"]
        .sum()
        .reset_index()
        .sort_values("enrollment", ascending=False)
    )
treemap_data = treemap_data[treemap_data["enrollment"] > 0]

if not treemap_data.empty:
    treemap_total = treemap_data["enrollment"].sum()
    treemap_data["share_pct"] = (treemap_data["enrollment"] / treemap_total * 100).round(1)
    treemap_data["label"] = (
        treemap_data["org_name"] + "<br>"
        + treemap_data["share_pct"].astype(str) + "%"
    )

    fig_tree = px.treemap(
        treemap_data,
        path=["org_name"],
        values="enrollment",
        color="enrollment",
        color_continuous_scale="Blues",
        custom_data=["share_pct"],
    )
    fig_tree.update_traces(
        textinfo="label+value",
        texttemplate="%{label}<br>%{customdata[0]:.1f}%<br>%{value:,}",
        hovertemplate=(
            "<b>%{label}</b><br>"
            "Enrollment: %{value:,}<br>"
            "Share: %{customdata[0]:.1f}%"
            "<extra></extra>"
        ),
    )
    fig_tree.update_layout(
        height=500,
        margin=dict(l=10, r=10, t=30, b=10),
        coloraxis_showscale=False,
    )
    st.plotly_chart(fig_tree, use_container_width=True)
else:
    st.info("No enrollment data for the selected geography and plan type.")

# --- Detail Table ---
if not treemap_data.empty:
    st.subheader("Organization Details")

    # Build detail table
    detail = treemap_data.copy()
    org_col_label = "County" if selected_plans else "Organization"
    detail = detail.rename(columns={
        "org_name": org_col_label,
        "enrollment": "MA Enrollment",
        "share_pct": "% of MA",
    })

    # Add % of total eligible if we can compute it
    if selected_counties:
        eligible_subset = map_data[map_data["county"].isin(selected_counties)]
    elif selected_states:
        eligible_subset = map_data
    else:
        eligible_subset = map_data
    total_eligible = eligible_subset["eligibles"].sum() if "eligibles" in eligible_subset.columns else 0

    if total_eligible > 0:
        detail["% of Total Eligible"] = (detail["MA Enrollment"] / total_eligible * 100).round(2)

    display_cols = [org_col_label, "MA Enrollment", "% of MA"]
    if "% of Total Eligible" in detail.columns:
        display_cols.append("% of Total Eligible")

    # Drop helper columns
    detail = detail[display_cols]

    st.dataframe(
        detail,
        use_container_width=True,
        hide_index=True,
    )
