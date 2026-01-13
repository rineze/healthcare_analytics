"""
Page 3: Category Overview
Portfolio view of all radiology segments with sparklines
"""
import streamlit as st
import pandas as pd
import altair as alt
import sys
sys.path.insert(0, '..')

from utils import (
    load_radiology_data,
    get_available_years,
    COLORS,
    RADIOLOGY_CATEGORIES
)

st.set_page_config(page_title="Category Overview", layout="wide")

st.title("Category Overview")
st.caption("Portfolio view of radiology segments with historical trends")

# Sidebar
st.sidebar.header("Options")
exclude_rad_onc = st.sidebar.checkbox("Exclude Radiation Oncology", value=True)
metric_options = {
    "work_rvu": "Work RVU",
    "non_facility_total": "Total RVU (Non-Facility)",
}
metric = st.sidebar.selectbox("Metric", list(metric_options.keys()),
                               format_func=lambda x: metric_options[x])

# Load data
df = load_radiology_data(exclude_rad_onc=exclude_rad_onc)
years = get_available_years()
latest_year = years[-1]
prior_year = years[-2]

# Calculate category statistics
def get_category_stats(df, metric):
    """Calculate comprehensive stats for each category."""
    stats = []

    for cat in df["category"].unique():
        cat_data = df[df["category"] == cat]

        # Current year stats
        current = cat_data[cat_data["mpfs_year"] == latest_year]
        prior = cat_data[cat_data["mpfs_year"] == prior_year]
        baseline = cat_data[cat_data["mpfs_year"] == years[0]]

        # Trend data (average by year)
        trend = cat_data.groupby("mpfs_year")[metric].mean().reset_index()
        trend_values = trend[metric].tolist()

        # Calculate metrics
        current_avg = current[metric].mean() if len(current) > 0 else None
        prior_avg = prior[metric].mean() if len(prior) > 0 else None
        baseline_avg = baseline[metric].mean() if len(baseline) > 0 else None

        yoy_change = (current_avg - prior_avg) if (current_avg and prior_avg) else None
        yoy_pct = (yoy_change / prior_avg * 100) if (yoy_change and prior_avg) else None

        total_change = (current_avg - baseline_avg) if (current_avg and baseline_avg) else None
        total_pct = (total_change / baseline_avg * 100) if (total_change and baseline_avg) else None

        stats.append({
            "category": cat,
            "code_count": current["hcpcs"].nunique(),
            "current_avg": current_avg,
            "yoy_change": yoy_change,
            "yoy_pct": yoy_pct,
            "total_change": total_change,
            "total_pct": total_pct,
            "trend": trend_values,
            "trend_data": trend
        })

    return pd.DataFrame(stats).sort_values("current_avg", ascending=False)


category_stats = get_category_stats(df, metric)

# Summary metrics
st.markdown("---")
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric("Categories", len(category_stats))

with col2:
    total_codes = category_stats["code_count"].sum()
    st.metric("Total Codes", f"{total_codes:,}")

with col3:
    overall_avg = df[df["mpfs_year"] == latest_year][metric].mean()
    st.metric(f"Overall Avg ({latest_year})", f"{overall_avg:.3f}")

with col4:
    increasing = (category_stats["yoy_change"] > 0).sum()
    decreasing = (category_stats["yoy_change"] < 0).sum()
    st.metric("Categories Up/Down", f"{increasing} / {decreasing}")

st.markdown("---")

# Create sparkline function
def create_sparkline(trend_data, metric, height=40, width=120):
    """Create a small sparkline chart."""
    if len(trend_data) == 0:
        return None

    chart = alt.Chart(trend_data).mark_line(
        color=COLORS["accent"],
        strokeWidth=1.5
    ).encode(
        x=alt.X("mpfs_year:O", axis=None),
        y=alt.Y(f"{metric}:Q", scale=alt.Scale(zero=False), axis=None),
    ).properties(
        height=height,
        width=width
    ).configure_view(
        strokeWidth=0
    )
    return chart


# Category overview - compact table with sparklines
st.subheader("Category Performance Summary")

# Create the main display
for idx, row in category_stats.iterrows():
    with st.container():
        cols = st.columns([3, 2, 1.5, 1.5, 1.5, 1.5])

        with cols[0]:
            st.markdown(f"**{row['category']}**")
            st.caption(f"{row['code_count']} codes")

        with cols[1]:
            # Sparkline
            if len(row["trend_data"]) > 0:
                spark = create_sparkline(row["trend_data"], metric)
                if spark:
                    st.altair_chart(spark, use_container_width=False)

        with cols[2]:
            if pd.notna(row["current_avg"]):
                st.metric(f"Avg {latest_year}", f"{row['current_avg']:.3f}", label_visibility="collapsed")
            else:
                st.write("-")

        with cols[3]:
            if pd.notna(row["yoy_change"]):
                color = COLORS["positive"] if row["yoy_change"] > 0 else COLORS["negative"]
                st.markdown(f"<span style='color:{color}'>{row['yoy_change']:+.3f}</span>",
                           unsafe_allow_html=True)
                st.caption("YoY Δ")
            else:
                st.write("-")

        with cols[4]:
            if pd.notna(row["yoy_pct"]):
                color = COLORS["positive"] if row["yoy_pct"] > 0 else COLORS["negative"]
                st.markdown(f"<span style='color:{color}'>{row['yoy_pct']:+.1f}%</span>",
                           unsafe_allow_html=True)
                st.caption("YoY %")
            else:
                st.write("-")

        with cols[5]:
            if pd.notna(row["total_pct"]):
                color = COLORS["positive"] if row["total_pct"] > 0 else COLORS["negative"]
                st.markdown(f"<span style='color:{color}'>{row['total_pct']:+.1f}%</span>",
                           unsafe_allow_html=True)
                st.caption(f"Since {years[0]}")
            else:
                st.write("-")

        st.markdown("---")

# Trend comparison chart
st.subheader("Category Trends Over Time")

# Prepare data for multi-line chart
trend_data = df.groupby(["mpfs_year", "category"])[metric].mean().reset_index()

trend_chart = alt.Chart(trend_data).mark_line(point=True).encode(
    x=alt.X("mpfs_year:O", title="Year"),
    y=alt.Y(f"{metric}:Q", title=metric_options[metric], scale=alt.Scale(zero=False)),
    color=alt.Color("category:N",
                   legend=alt.Legend(title="Category", orient="right", columns=1)),
    strokeWidth=alt.value(2),
    tooltip=[
        alt.Tooltip("mpfs_year:O", title="Year"),
        alt.Tooltip("category:N", title="Category"),
        alt.Tooltip(f"{metric}:Q", title="Avg Value", format=".3f")
    ]
).properties(
    height=450
).configure_axis(
    labelFontSize=11,
    titleFontSize=12
).configure_view(
    strokeWidth=0
).interactive()

st.altair_chart(trend_chart, use_container_width=True)

# Heatmap view
st.subheader("Year-over-Year Change Heatmap")

# Calculate YoY changes for each category and year
heatmap_data = []
for cat in df["category"].unique():
    cat_data = df[df["category"] == cat]
    for i, year in enumerate(years[1:], 1):
        prior_year_val = years[i-1]
        current_avg = cat_data[cat_data["mpfs_year"] == year][metric].mean()
        prior_avg = cat_data[cat_data["mpfs_year"] == prior_year_val][metric].mean()
        if pd.notna(current_avg) and pd.notna(prior_avg) and prior_avg != 0:
            pct_change = (current_avg - prior_avg) / prior_avg * 100
            heatmap_data.append({
                "category": cat,
                "year": f"{prior_year_val}→{year}",
                "pct_change": pct_change
            })

heatmap_df = pd.DataFrame(heatmap_data)

if len(heatmap_df) > 0:
    heatmap = alt.Chart(heatmap_df).mark_rect().encode(
        x=alt.X("year:O", title="Year Transition"),
        y=alt.Y("category:N", title=None, sort=alt.EncodingSortField(field="pct_change", op="mean", order="descending")),
        color=alt.Color("pct_change:Q",
                       title="% Change",
                       scale=alt.Scale(scheme="redyellowgreen", domain=[-10, 10]),
                       legend=alt.Legend(orient="right")),
        tooltip=[
            alt.Tooltip("category:N", title="Category"),
            alt.Tooltip("year:O", title="Period"),
            alt.Tooltip("pct_change:Q", title="% Change", format="+.1f")
        ]
    ).properties(
        height=400
    ).configure_axis(
        labelFontSize=11
    ).configure_view(
        strokeWidth=0
    )

    st.altair_chart(heatmap, use_container_width=True)

# Top movers by category - expandable sections
st.subheader("Top Codes by Category")
st.caption(f"Highest {metric_options[metric]} codes in {latest_year}")

selected_cat = st.selectbox("Select Category", category_stats["category"].tolist())

if selected_cat:
    cat_codes = df[(df["category"] == selected_cat) & (df["mpfs_year"] == latest_year)]
    top_codes = cat_codes.nlargest(15, metric)

    display_df = top_codes[["hcpcs", "description", metric]].copy()
    display_df.columns = ["CPT", "Description", metric_options[metric]]
    display_df[metric_options[metric]] = display_df[metric_options[metric]].round(3)

    st.dataframe(display_df, use_container_width=True, hide_index=True)
