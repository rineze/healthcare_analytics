"""
Page 1: Year-over-Year Changes
Compare radiology wRVU changes between any two years
"""
import streamlit as st
import pandas as pd
import altair as alt
import sys
sys.path.insert(0, '..')

from utils import (
    load_radiology_data,
    get_available_years,
    calculate_yoy_changes,
    create_category_summary,
    COLORS
)

st.set_page_config(page_title="YoY Changes", layout="wide")

st.title("Year-over-Year Changes")
st.caption("Identify the biggest wRVU movers between any two years")

# Sidebar controls
st.sidebar.header("Filters")

# Load data
years = get_available_years()

# Year selection
col1, col2 = st.sidebar.columns(2)
with col1:
    year_from = st.selectbox("From Year", years[:-1], index=len(years) - 2)
with col2:
    year_to_options = [y for y in years if y > year_from]
    year_to = st.selectbox("To Year", year_to_options, index=len(year_to_options) - 1 if year_to_options else 0)

# Radiation Oncology toggle
exclude_rad_onc = st.sidebar.checkbox("Exclude Radiation Oncology", value=True,
                                       help="Radiation Oncology (77261-77799) is typically a separate specialty")

# Metric selection
metric_options = {
    "work_rvu": "Work RVU",
    "non_facility_total": "Total RVU (Non-Facility)",
    "facility_total": "Total RVU (Facility)",
}
metric = st.sidebar.selectbox(
    "Metric",
    list(metric_options.keys()),
    format_func=lambda x: metric_options[x]
)

# Load data
df = load_radiology_data(exclude_rad_onc=exclude_rad_onc)

# Category filter
categories = ["All Categories"] + sorted(df["category"].unique().tolist())
selected_category = st.sidebar.selectbox("Category", categories)

if selected_category != "All Categories":
    df = df[df["category"] == selected_category]

# Calculate YoY changes
changes = calculate_yoy_changes(df, year_from, year_to, metric)

# Summary metrics
st.markdown("---")
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric("Codes Compared", f"{len(changes):,}")

with col2:
    avg_change = changes["change"].mean()
    st.metric(
        "Avg Change",
        f"{avg_change:+.3f}",
        delta=f"{avg_change:+.3f}",
        delta_color="normal" if avg_change >= 0 else "inverse"
    )

with col3:
    increasing = (changes["change"] > 0).sum()
    st.metric("Codes Increasing", f"{increasing:,}")

with col4:
    decreasing = (changes["change"] < 0).sum()
    st.metric("Codes Decreasing", f"{decreasing:,}")

st.markdown("---")

# Main visualization: Diverging bar chart
st.subheader(f"Top Movers: {year_from} → {year_to}")

# Get top gainers and losers
n_show = 15
top_gainers = changes.nlargest(n_show, "change").copy()
top_losers = changes.nsmallest(n_show, "change").copy()

# Combine for visualization
top_gainers["direction"] = "Increase"
top_losers["direction"] = "Decrease"
top_movers = pd.concat([top_gainers, top_losers])
top_movers["display_label"] = top_movers["hcpcs"] + " - " + top_movers["description"].str[:40]

# Create diverging bar chart
chart = alt.Chart(top_movers).mark_bar().encode(
    x=alt.X("change:Q",
            title=f"Change in {metric_options[metric]}",
            axis=alt.Axis(format="+.2f")),
    y=alt.Y("display_label:N",
            sort=alt.EncodingSortField(field="change", order="descending"),
            title=None,
            axis=alt.Axis(labelLimit=300)),
    color=alt.condition(
        alt.datum.change > 0,
        alt.value(COLORS["positive"]),
        alt.value(COLORS["negative"])
    ),
    tooltip=[
        alt.Tooltip("hcpcs:N", title="CPT Code"),
        alt.Tooltip("description:N", title="Description"),
        alt.Tooltip("category:N", title="Category"),
        alt.Tooltip("value_from:Q", title=f"{year_from} Value", format=".2f"),
        alt.Tooltip("value_to:Q", title=f"{year_to} Value", format=".2f"),
        alt.Tooltip("change:Q", title="Change", format="+.2f"),
        alt.Tooltip("pct_change:Q", title="% Change", format="+.1f"),
    ]
).properties(
    height=600
).configure_axis(
    labelFontSize=11,
    titleFontSize=12
).configure_view(
    strokeWidth=0
)

st.altair_chart(chart, use_container_width=True)

# Tabbed detail view
st.subheader("Detail Tables")
tab1, tab2 = st.tabs(["Top Increases", "Top Decreases"])

with tab1:
    display_df = top_gainers[["hcpcs", "description", "category", "value_from", "value_to", "change", "pct_change"]].copy()
    display_df.columns = ["CPT", "Description", "Category", f"{year_from}", f"{year_to}", "Change", "% Change"]
    display_df[f"{year_from}"] = display_df[f"{year_from}"].round(2)
    display_df[f"{year_to}"] = display_df[f"{year_to}"].round(2)
    display_df["Change"] = display_df["Change"].round(3)
    display_df["% Change"] = display_df["% Change"].round(1)
    st.dataframe(display_df, use_container_width=True, hide_index=True)

with tab2:
    display_df = top_losers[["hcpcs", "description", "category", "value_from", "value_to", "change", "pct_change"]].copy()
    display_df.columns = ["CPT", "Description", "Category", f"{year_from}", f"{year_to}", "Change", "% Change"]
    display_df[f"{year_from}"] = display_df[f"{year_from}"].round(2)
    display_df[f"{year_to}"] = display_df[f"{year_to}"].round(2)
    display_df["Change"] = display_df["Change"].round(3)
    display_df["% Change"] = display_df["% Change"].round(1)
    st.dataframe(display_df, use_container_width=True, hide_index=True)

# Category summary
st.markdown("---")
st.subheader("Category Summary")

cat_summary = create_category_summary(df, year_from, year_to, metric)

# Category bar chart
cat_chart = alt.Chart(cat_summary).mark_bar().encode(
    x=alt.X("change:Q",
            title=f"Avg Change in {metric_options[metric]}",
            axis=alt.Axis(format="+.3f")),
    y=alt.Y("category:N",
            sort=alt.EncodingSortField(field="change", order="descending"),
            title=None),
    color=alt.condition(
        alt.datum.change > 0,
        alt.value(COLORS["positive"]),
        alt.value(COLORS["negative"])
    ),
    tooltip=[
        alt.Tooltip("category:N", title="Category"),
        alt.Tooltip("code_count:Q", title="# Codes"),
        alt.Tooltip("avg_from:Q", title=f"Avg {year_from}", format=".3f"),
        alt.Tooltip("avg_to:Q", title=f"Avg {year_to}", format=".3f"),
        alt.Tooltip("change:Q", title="Change", format="+.3f"),
        alt.Tooltip("pct_change:Q", title="% Change", format="+.1f"),
    ]
).properties(
    height=350
).configure_axis(
    labelFontSize=11
).configure_view(
    strokeWidth=0
)

st.altair_chart(cat_chart, use_container_width=True)

# Category detail table
cat_display = cat_summary[["category", "code_count", "avg_from", "avg_to", "change", "pct_change"]].copy()
cat_display.columns = ["Category", "# Codes", f"Avg {year_from}", f"Avg {year_to}", "Change", "% Change"]
cat_display[f"Avg {year_from}"] = cat_display[f"Avg {year_from}"].round(3)
cat_display[f"Avg {year_to}"] = cat_display[f"Avg {year_to}"].round(3)
cat_display["Change"] = cat_display["Change"].round(3)
cat_display["% Change"] = cat_display["% Change"].round(1)
st.dataframe(cat_display, use_container_width=True, hide_index=True)
