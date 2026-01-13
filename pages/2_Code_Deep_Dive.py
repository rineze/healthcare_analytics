"""
Page 2: Code Deep Dive
Full historical analysis of specific CPT codes
"""
import streamlit as st
import pandas as pd
import altair as alt
import sys
sys.path.insert(0, '..')

from utils import (
    load_radiology_data,
    get_available_years,
    COLORS
)

st.set_page_config(page_title="Code Deep Dive", layout="wide")

st.title("Code Deep Dive")
st.caption("Full historical analysis of specific radiology CPT codes")

# Load data
df = load_radiology_data(exclude_rad_onc=False)  # Include all for lookup
years = get_available_years()

# Get unique codes for search
code_list = df[["hcpcs", "description"]].drop_duplicates()
code_list["search_label"] = code_list["hcpcs"] + " - " + code_list["description"].str[:50]
code_options = code_list.set_index("hcpcs")["search_label"].to_dict()

# Sidebar - Code selection
st.sidebar.header("Code Selection")

# Search box
search_input = st.sidebar.text_input("Search CPT Code", placeholder="e.g., 70553")

# Filter options based on search
if search_input:
    filtered_codes = {k: v for k, v in code_options.items()
                      if search_input.lower() in k.lower() or search_input.lower() in v.lower()}
else:
    # Show common high-volume codes as default options
    common_codes = ["70553", "74177", "71260", "72148", "76700", "77067", "70551", "74176", "73721"]
    filtered_codes = {k: v for k, v in code_options.items() if k in common_codes}

if filtered_codes:
    selected_code = st.sidebar.selectbox(
        "Select Code",
        list(filtered_codes.keys()),
        format_func=lambda x: filtered_codes[x]
    )
else:
    selected_code = None
    st.sidebar.warning("No codes match your search")

# Compare to category average
show_category_avg = st.sidebar.checkbox("Show Category Average", value=True)

# Main content
if selected_code:
    # Get data for selected code
    code_data = df[df["hcpcs"] == selected_code].sort_values("mpfs_year")

    if len(code_data) > 0:
        # Header info
        latest = code_data.iloc[-1]
        st.markdown(f"### {selected_code}: {latest['description']}")
        st.markdown(f"**Category:** {latest['category']}")

        st.markdown("---")

        # Key metrics
        first_year_data = code_data.iloc[0]
        last_year_data = code_data.iloc[-1]

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            current_wrvu = last_year_data["work_rvu"]
            st.metric("Current Work RVU", f"{current_wrvu:.2f}" if pd.notna(current_wrvu) else "N/A")

        with col2:
            if pd.notna(first_year_data["work_rvu"]) and pd.notna(last_year_data["work_rvu"]):
                total_change = last_year_data["work_rvu"] - first_year_data["work_rvu"]
                st.metric(
                    f"Change Since {int(first_year_data['mpfs_year'])}",
                    f"{total_change:+.2f}",
                    delta=f"{total_change:+.2f}",
                    delta_color="normal" if total_change >= 0 else "inverse"
                )
            else:
                st.metric(f"Change Since {int(first_year_data['mpfs_year'])}", "N/A")

        with col3:
            current_payment = last_year_data["work_payment"]
            st.metric("Work Payment", f"${current_payment:.2f}" if pd.notna(current_payment) else "N/A")

        with col4:
            st.metric("Years of Data", f"{len(code_data)}")

        st.markdown("---")

        # Trend chart - Work RVU
        st.subheader("Work RVU Trend")

        # Prepare chart data
        chart_data = code_data[["mpfs_year", "work_rvu"]].copy()
        chart_data = chart_data.rename(columns={"mpfs_year": "Year", "work_rvu": "Work RVU"})
        chart_data["Type"] = selected_code

        # Add category average if requested
        if show_category_avg:
            category = latest["category"]
            cat_avg = df[df["category"] == category].groupby("mpfs_year")["work_rvu"].mean().reset_index()
            cat_avg = cat_avg.rename(columns={"mpfs_year": "Year", "work_rvu": "Work RVU"})
            cat_avg["Type"] = f"{category} Avg"
            chart_data = pd.concat([chart_data, cat_avg])

        # Create line chart
        line_chart = alt.Chart(chart_data).mark_line(point=True).encode(
            x=alt.X("Year:O", title="Year"),
            y=alt.Y("Work RVU:Q", title="Work RVU", scale=alt.Scale(zero=False)),
            color=alt.Color("Type:N",
                           scale=alt.Scale(
                               domain=[selected_code, f"{latest['category']} Avg"] if show_category_avg else [selected_code],
                               range=[COLORS["accent"], COLORS["neutral_light"]] if show_category_avg else [COLORS["accent"]]
                           ),
                           legend=alt.Legend(title=None, orient="top")),
            strokeWidth=alt.condition(
                alt.datum.Type == selected_code,
                alt.value(3),
                alt.value(1.5)
            ),
            strokeDash=alt.condition(
                alt.datum.Type == selected_code,
                alt.value([0]),
                alt.value([4, 4])
            ),
            tooltip=[
                alt.Tooltip("Year:O"),
                alt.Tooltip("Type:N"),
                alt.Tooltip("Work RVU:Q", format=".2f")
            ]
        ).properties(
            height=350
        ).configure_axis(
            labelFontSize=12,
            titleFontSize=13
        ).configure_view(
            strokeWidth=0
        )

        st.altair_chart(line_chart, use_container_width=True)

        # Payment trend
        st.subheader("Payment Trend (Work RVU × Conversion Factor)")

        payment_data = code_data[["mpfs_year", "work_payment", "total_payment_nonfac", "total_payment_fac"]].copy()
        payment_data = payment_data.melt(id_vars=["mpfs_year"], var_name="Payment Type", value_name="Amount")
        payment_data["Payment Type"] = payment_data["Payment Type"].map({
            "work_payment": "Work Payment",
            "total_payment_nonfac": "Total (Non-Facility)",
            "total_payment_fac": "Total (Facility)"
        })

        payment_chart = alt.Chart(payment_data).mark_line(point=True).encode(
            x=alt.X("mpfs_year:O", title="Year"),
            y=alt.Y("Amount:Q", title="Payment ($)", scale=alt.Scale(zero=False)),
            color=alt.Color("Payment Type:N",
                           scale=alt.Scale(
                               domain=["Work Payment", "Total (Non-Facility)", "Total (Facility)"],
                               range=[COLORS["accent"], COLORS["positive"], COLORS["neutral"]]
                           ),
                           legend=alt.Legend(title=None, orient="top")),
            tooltip=[
                alt.Tooltip("mpfs_year:O", title="Year"),
                alt.Tooltip("Payment Type:N"),
                alt.Tooltip("Amount:Q", title="Payment", format="$,.2f")
            ]
        ).properties(
            height=300
        ).configure_view(
            strokeWidth=0
        )

        st.altair_chart(payment_chart, use_container_width=True)

        # Detail table
        st.subheader("Year-by-Year Detail")

        detail_df = code_data[[
            "mpfs_year", "work_rvu", "non_fac_pe_rvu", "facility_pe_rvu",
            "mp_rvu", "non_facility_total", "facility_total", "conversion_factor"
        ]].copy()

        detail_df.columns = [
            "Year", "Work RVU", "PE RVU (NF)", "PE RVU (Fac)",
            "MP RVU", "Total (NF)", "Total (Fac)", "Conv Factor"
        ]

        # Round values
        for col in detail_df.columns[1:]:
            detail_df[col] = detail_df[col].round(4)

        # Calculate YoY change column
        detail_df["Work YoY"] = detail_df["Work RVU"].diff()
        cols = detail_df.columns.tolist()
        cols.insert(2, cols.pop(-1))  # Move Work YoY after Work RVU
        detail_df = detail_df[cols]
        detail_df["Work YoY"] = detail_df["Work YoY"].apply(lambda x: f"{x:+.4f}" if pd.notna(x) else "-")

        st.dataframe(detail_df, use_container_width=True, hide_index=True)

        # Similar codes in category
        st.markdown("---")
        st.subheader(f"Other Codes in {latest['category']}")

        category_codes = df[(df["category"] == latest["category"]) &
                           (df["mpfs_year"] == years[-1]) &
                           (df["hcpcs"] != selected_code)]

        if len(category_codes) > 0:
            category_codes = category_codes.nlargest(10, "work_rvu")
            display_cat = category_codes[["hcpcs", "description", "work_rvu"]].copy()
            display_cat.columns = ["CPT", "Description", "Work RVU"]
            display_cat["Work RVU"] = display_cat["Work RVU"].round(2)
            st.dataframe(display_cat, use_container_width=True, hide_index=True)
        else:
            st.info("No other codes in this category")

    else:
        st.warning(f"No data found for code {selected_code}")

else:
    st.info("Enter a CPT code in the sidebar to begin analysis")

    # Show some example codes
    st.markdown("### Common Radiology Codes")
    st.markdown("""
    **Diagnostic Imaging:**
    - `70553` - MRI brain with/without contrast
    - `74177` - CT abdomen/pelvis with contrast
    - `71260` - CT chest with contrast

    **Ultrasound:**
    - `76700` - Ultrasound abdominal complete
    - `76805` - OB ultrasound

    **Mammography:**
    - `77067` - Screening mammography bilateral
    """)
