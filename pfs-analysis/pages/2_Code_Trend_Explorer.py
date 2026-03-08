"""
Page 2: Code Trend Explorer
Deep-dive into code reimbursement history with grouping and utilization-weighted analysis
"""
import streamlit as st
import pandas as pd
import altair as alt
from utils import (
    get_connection,
    get_available_years,
    get_code_list,
    get_localities,
    get_code_trend,
    get_code_yoy_detail,
    get_locality_comparison,
    CODE_GROUPS,
    CPT_CATEGORY_RANGES,
    COLORS,
    format_currency,
    format_percent
)

st.set_page_config(page_title="Code Trend Explorer", page_icon="$", layout="wide")

st.title("Code Trend Explorer")
st.caption("Analyze code reimbursement across time and localities with grouping support")


def get_group_trend_data(hcpcs_codes, locality_id, setting="nonfacility"):
    conn = get_connection()
    # Validate setting to prevent SQL injection via column name
    if setting not in ('nonfacility', 'facility'):
        setting = 'nonfacility'
    allowed_col = f"allowed_{setting}"
    placeholders = ','.join(['%s'] * len(hcpcs_codes))
    params = [locality_id] + list(hcpcs_codes)
    query = f"""
        SELECT y.year, y.hcpcs, y.hcpcs_mod, r.description, y.{allowed_col} as allowed, y.w_rvu, y.conversion_factor
        FROM drinf.v_mpfs_allowed_yoy y
        JOIN drinf.v_rvu_clean r ON r.year = y.year AND r.hcpcs_mod = y.hcpcs_mod
        WHERE y.locality_id = %s AND y.hcpcs IN ({placeholders}) AND y.modifier IS NULL
        ORDER BY y.year, y.hcpcs
    """
    return pd.read_sql(query, conn, params=params)

def get_group_yoy_detail(hcpcs_codes, locality_id, setting="nonfacility"):
    conn = get_connection()
    # Validate setting to prevent SQL injection via column name
    if setting not in ('nonfacility', 'facility'):
        setting = 'nonfacility'
    placeholders = ','.join(['%s'] * len(hcpcs_codes))
    params = [locality_id] + list(hcpcs_codes)
    query = f"""
        SELECT y.year, y.hcpcs, r.description, y.allowed_{setting} as current_allowed,
               y.allowed_{setting}_py as prior_allowed, y.allowed_{setting}_change as change,
               y.allowed_{setting}_pct_change as pct_change, y.w_rvu, y.conversion_factor
        FROM drinf.v_mpfs_allowed_yoy y
        JOIN drinf.v_rvu_clean r ON r.year = y.year AND r.hcpcs_mod = y.hcpcs_mod
        WHERE y.locality_id = %s AND y.hcpcs IN ({placeholders}) AND y.modifier IS NULL
        ORDER BY y.year DESC, y.hcpcs
    """
    return pd.read_sql(query, conn, params=params)

def get_utilization_weights(hcpcs_codes, year=2023):
    conn = get_connection()
    placeholders = ','.join(['%s'] * len(hcpcs_codes))
    params = list(hcpcs_codes) + [year]
    query = f"""
        SELECT hcpcs, SUM(total_services) as total_services
        FROM drinf.medicare_utilization
        WHERE hcpcs IN ({placeholders}) AND geo_level = 'National' AND year = %s
        GROUP BY hcpcs
    """
    return pd.read_sql(query, conn, params=params)

def calculate_weighted_yoy(yoy_df, util_df):
    merged = yoy_df.merge(util_df, on="hcpcs", how="left")
    merged["total_services"] = merged["total_services"].fillna(0)
    results = []
    for year in merged["year"].unique():
        year_data = merged[merged["year"] == year].copy()
        total_util = year_data["total_services"].sum()
        if total_util > 0:
            year_data["weight"] = year_data["total_services"] / total_util
            weighted_current = (year_data["current_allowed"] * year_data["weight"]).sum()
            weighted_prior = (year_data["prior_allowed"] * year_data["weight"]).sum()
            weighted_change = (year_data["change"] * year_data["weight"]).sum()
            weighted_pct_change = (year_data["pct_change"] * year_data["weight"]).sum()
        else:
            weighted_current = year_data["current_allowed"].mean()
            weighted_prior = year_data["prior_allowed"].mean()
            weighted_change = year_data["change"].mean()
            weighted_pct_change = year_data["pct_change"].mean()
        results.append({"year": year, "weighted_current": weighted_current, "weighted_prior": weighted_prior,
                       "weighted_change": weighted_change, "weighted_pct_change": weighted_pct_change,
                       "total_services": total_util, "code_count": len(year_data)})
    return pd.DataFrame(results)


try:
    years = get_available_years()
    latest_year = max(years)
    localities = get_localities()

    st.sidebar.header("Selection Mode")
    selection_mode = st.sidebar.radio("Mode", options=["Single Code", "Code Groups", "CPT Category"], index=0)

    if selection_mode == "Single Code":
        st.sidebar.header("Code Selection")
        codes_df = get_code_list(year=latest_year, payable_only=True)
        code_options = codes_df["hcpcs_mod"].tolist()
        code_descriptions = dict(zip(codes_df["hcpcs_mod"], codes_df["description"]))
        default_idx = code_options.index("70553") if "70553" in code_options else 0
        selected_code = st.sidebar.selectbox("Select Code", options=code_options, index=default_idx,
            format_func=lambda x: f"{x} - {code_descriptions.get(x, '')[:40]}")
        selected_codes = [selected_code.split("-")[0]]
        selected_groups = []
        use_groups = False

    elif selection_mode == "Code Groups":
        st.sidebar.header("Radiology Groupings")
        selected_groups = st.sidebar.multiselect("Select Code Groups", options=list(CODE_GROUPS.keys()), default=["MRI Brain"])
        selected_codes = []
        for group in selected_groups:
            selected_codes.extend(CODE_GROUPS[group])
        selected_codes = list(set(selected_codes))
        st.sidebar.caption(f"Total codes: {len(selected_codes)}")
        with st.sidebar.expander("View Selected Codes"):
            for group in selected_groups:
                codes_list = ", ".join(CODE_GROUPS[group])
                st.markdown(f"**{group}:** {codes_list}")
        use_groups = True

    else:  # CPT Category
        st.sidebar.header("CPT Category")
        selected_category = st.sidebar.selectbox(
            "Select Category",
            options=[k for k in CPT_CATEGORY_RANGES.keys() if k != "All Codes"],
            index=8  # Default to Radiology
        )
        cat_range = CPT_CATEGORY_RANGES.get(selected_category)
        if cat_range:
            st.sidebar.caption(f"Range: {cat_range[0]} - {cat_range[1]}")
            codes_df = get_code_list(year=latest_year, payable_only=True)
            all_codes = codes_df["hcpcs"].tolist()
            selected_codes = [c for c in all_codes if cat_range[0] <= c <= cat_range[1]][:20]
            st.sidebar.caption(f"Using top 20 codes in range")
        else:
            selected_codes = []
        selected_groups = [selected_category]
        use_groups = True

    st.sidebar.markdown("---")
    st.sidebar.header("Locality")
    locality_options = localities["locality_id"].tolist()
    locality_names = dict(zip(localities["locality_id"], localities["locality_name"]))

    if use_groups:
        selected_locality = st.sidebar.selectbox("Reference Locality", options=locality_options,
            index=locality_options.index("AL-00") if "AL-00" in locality_options else 0,
            format_func=lambda x: f"{locality_names.get(x, x)} ({x})")
        selected_localities = [selected_locality]
    else:
        default_localities = [loc for loc in ["CA-18", "NY-01", "AL-00"] if loc in locality_options]
        selected_localities = st.sidebar.multiselect("Compare Localities (max 5)", options=locality_options,
            default=default_localities[:3], max_selections=5, format_func=lambda x: f"{locality_names.get(x, x)} ({x})")
        selected_locality = selected_localities[0] if selected_localities else "AL-00"

    setting = st.sidebar.radio("Payment Setting", options=["nonfacility", "facility"],
        format_func=lambda x: "Non-Facility" if x == "nonfacility" else "Facility")

    st.sidebar.markdown("---")
    if use_groups:
        groups_str = ", ".join(selected_groups)
        st.sidebar.caption(f"Groups: {groups_str}")
    else:
        st.sidebar.caption(f"Selected: {selected_code}")

    if not selected_codes:
        st.warning("Please select at least one code or code group")
        st.stop()


    if use_groups:
        groups_str = ", ".join(selected_groups)
        st.subheader(f"Code Group Analysis: {groups_str}")
        st.caption(f"Locality: {locality_names.get(selected_locality, selected_locality)} | {len(selected_codes)} codes")

        trend_data = get_group_trend_data(selected_codes, selected_locality, setting)
        yoy_data = get_group_yoy_detail(selected_codes, selected_locality, setting)

        try:
            util_data = get_utilization_weights(selected_codes, 2023)
            has_utilization = len(util_data) > 0
        except:
            util_data = pd.DataFrame()
            has_utilization = False

        if len(yoy_data) == 0:
            st.warning("No data found for selected codes")
            st.stop()

        st.subheader("Utilization-Weighted Trend")
        if has_utilization:
            trend_merged = trend_data.merge(util_data, on="hcpcs", how="left")
            trend_merged["total_services"] = trend_merged["total_services"].fillna(0)
            weighted_trend = []
            for year in trend_merged["year"].unique():
                year_data = trend_merged[trend_merged["year"] == year]
                total_util = year_data["total_services"].sum()
                if total_util > 0:
                    year_data = year_data.copy()
                    year_data["weight"] = year_data["total_services"] / total_util
                    weighted_allowed = (year_data["allowed"] * year_data["weight"]).sum()
                else:
                    weighted_allowed = year_data["allowed"].mean()
                weighted_trend.append({"year": year, "weighted_allowed": weighted_allowed})
            weighted_trend_df = pd.DataFrame(weighted_trend)
            trend_chart = alt.Chart(weighted_trend_df).mark_line(point=True, color=COLORS["accent"]).encode(
                x=alt.X("year:O", title="Year"),
                y=alt.Y("weighted_allowed:Q", title="Weighted Avg Allowed ($)", scale=alt.Scale(zero=False)),
                tooltip=[alt.Tooltip("year:O", title="Year"), alt.Tooltip("weighted_allowed:Q", title="Weighted Avg", format="$.2f")]
            ).properties(height=300)
            st.altair_chart(trend_chart, use_container_width=True)
            st.caption("Weighted by 2023 Medicare utilization volume")
        else:
            avg_trend = trend_data.groupby("year").agg({"allowed": "mean"}).reset_index()
            trend_chart = alt.Chart(avg_trend).mark_line(point=True, color=COLORS["accent"]).encode(
                x=alt.X("year:O", title="Year"),
                y=alt.Y("allowed:Q", title="Avg Allowed ($)", scale=alt.Scale(zero=False)),
                tooltip=[alt.Tooltip("year:O", title="Year"), alt.Tooltip("allowed:Q", title="Avg Allowed", format="$.2f")]
            ).properties(height=300)
            st.altair_chart(trend_chart, use_container_width=True)
            st.caption("Simple average (no utilization data available)")

        st.divider()
        st.subheader("Year-over-Year Summary (Utilization-Weighted)")
        if has_utilization:
            weighted_yoy = calculate_weighted_yoy(yoy_data, util_data)
            display_weighted = weighted_yoy[["year", "weighted_prior", "weighted_current", "weighted_change", "weighted_pct_change", "total_services", "code_count"]].copy()
            display_weighted.columns = ["Year", "Prior $ (Wtd)", "Current $ (Wtd)", "$ Chg (Wtd)", "% Chg (Wtd)", "2023 Services", "Codes"]
            display_weighted["Prior $ (Wtd)"] = display_weighted["Prior $ (Wtd)"].apply(format_currency)
            display_weighted["Current $ (Wtd)"] = display_weighted["Current $ (Wtd)"].apply(format_currency)
            display_weighted["$ Chg (Wtd)"] = display_weighted["$ Chg (Wtd)"].apply(format_currency)
            display_weighted["% Chg (Wtd)"] = display_weighted["% Chg (Wtd)"].apply(format_percent)
            display_weighted["2023 Services"] = display_weighted["2023 Services"].apply(lambda x: f"{x:,.0f}")
            st.dataframe(display_weighted, use_container_width=True, hide_index=True)
        else:
            simple_yoy = yoy_data.groupby("year").agg({"prior_allowed": "mean", "current_allowed": "mean", "change": "mean", "pct_change": "mean", "hcpcs": "count"}).reset_index()
            simple_yoy.columns = ["Year", "Avg Prior $", "Avg Current $", "Avg $ Chg", "Avg % Chg", "Codes"]
            simple_yoy["Avg Prior $"] = simple_yoy["Avg Prior $"].apply(format_currency)
            simple_yoy["Avg Current $"] = simple_yoy["Avg Current $"].apply(format_currency)
            simple_yoy["Avg $ Chg"] = simple_yoy["Avg $ Chg"].apply(format_currency)
            simple_yoy["Avg % Chg"] = simple_yoy["Avg % Chg"].apply(format_percent)
            st.dataframe(simple_yoy, use_container_width=True, hide_index=True)
            st.caption("Simple average (no utilization data available)")


        st.divider()
        st.subheader("CPT Code Level Detail")
        latest_yoy = yoy_data[yoy_data["year"] == yoy_data["year"].max()].copy()
        if has_utilization:
            latest_yoy = latest_yoy.merge(util_data, on="hcpcs", how="left")
            latest_yoy["total_services"] = latest_yoy["total_services"].fillna(0)
        else:
            latest_yoy["total_services"] = 0
        latest_yoy = latest_yoy.sort_values("total_services", ascending=False)
        display_codes = latest_yoy[["hcpcs", "description", "prior_allowed", "current_allowed", "change", "pct_change", "total_services"]].copy()
        display_codes.columns = ["CPT", "Description", "Prior $", "Current $", "$ Chg", "% Chg", "2023 Services"]
        display_codes["Prior $"] = display_codes["Prior $"].apply(format_currency)
        display_codes["Current $"] = display_codes["Current $"].apply(format_currency)
        display_codes["$ Chg"] = display_codes["$ Chg"].apply(format_currency)
        display_codes["% Chg"] = display_codes["% Chg"].apply(format_percent)
        display_codes["2023 Services"] = display_codes["2023 Services"].apply(lambda x: f"{x:,.0f}" if x > 0 else "-")
        display_codes["Description"] = display_codes["Description"].apply(lambda x: x[:45] + "..." if pd.notna(x) and len(str(x)) > 45 else x)
        st.dataframe(display_codes, use_container_width=True, hide_index=True)
        csv = latest_yoy.to_csv(index=False)
        filename = "_".join(selected_groups) + "_code_detail.csv"
        st.download_button(label="Download Code Detail (CSV)", data=csv, file_name=filename, mime="text/csv")

    else:
        codes_df = get_code_list(year=latest_year, payable_only=True)
        code_descriptions = dict(zip(codes_df["hcpcs_mod"], codes_df["description"]))
        st.subheader(f"{selected_code}")
        st.caption(code_descriptions.get(selected_code, "No description available"))
        if not selected_localities:
            st.warning("Please select at least one locality for comparison")
            st.stop()

        st.subheader("Allowed Amount Trend")
        trend_data = get_code_trend(selected_code, selected_localities, setting)
        if len(trend_data) > 0:
            trend_chart = alt.Chart(trend_data).mark_line(point=True).encode(
                x=alt.X("year:O", title="Year"),
                y=alt.Y("allowed:Q", title=f"Allowed Amount ({setting.title()})"),
                color=alt.Color("locality_name:N", title="Locality", scale=alt.Scale(scheme="tableau10")),
                tooltip=[alt.Tooltip("locality_name:N", title="Locality"), alt.Tooltip("year:O", title="Year"),
                         alt.Tooltip("allowed:Q", title="Allowed", format="$.2f"), alt.Tooltip("w_rvu:Q", title="Work RVU", format=".2f"),
                         alt.Tooltip("conversion_factor:Q", title="CF", format="$.4f")]
            ).properties(height=350)
            st.altair_chart(trend_chart, use_container_width=True)
        else:
            st.info("No trend data available for selected code and localities")

        st.divider()
        st.subheader("Year-over-Year Detail")
        yoy_data = get_code_yoy_detail(selected_code, selected_localities, setting)
        if len(yoy_data) > 0:
            display_df = yoy_data[["year", "locality_name", "current_allowed", "prior_allowed", "change", "pct_change", "w_rvu", "conversion_factor"]].copy()
            display_df.columns = ["Year", "Locality", "Current $", "Prior $", "$ Chg", "% Chg", "Work RVU", "CF"]
            display_df["Current $"] = display_df["Current $"].apply(format_currency)
            display_df["Prior $"] = display_df["Prior $"].apply(format_currency)
            display_df["$ Chg"] = display_df["$ Chg"].apply(format_currency)
            display_df["% Chg"] = display_df["% Chg"].apply(format_percent)
            display_df["CF"] = display_df["CF"].apply(lambda x: format_currency(x, 4))
            st.dataframe(display_df, use_container_width=True, hide_index=True)
            csv = yoy_data.to_csv(index=False)
            st.download_button(label="Download YoY Data (CSV)", data=csv, file_name=f"{selected_code}_yoy_detail.csv", mime="text/csv")
        else:
            st.info("No YoY data available")


        st.divider()
        st.subheader(f"Locality Comparison ({latest_year})")
        comparison_data = get_locality_comparison(selected_code, latest_year, setting, top_n=25)
        if len(comparison_data) > 0:
            avg_allowed = comparison_data["allowed"].mean()
            comparison_data["is_selected"] = comparison_data["locality_id"].isin(selected_localities)
            bar_chart = alt.Chart(comparison_data).mark_bar().encode(
                y=alt.Y("locality_name:N", title="Locality", sort="-x"),
                x=alt.X("allowed:Q", title=f"Allowed Amount ({setting.title()})"),
                color=alt.condition(alt.datum.is_selected, alt.value(COLORS["accent"]), alt.value(COLORS["neutral_light"])),
                tooltip=[alt.Tooltip("locality_name:N", title="Locality"), alt.Tooltip("allowed:Q", title="Allowed", format="$.2f"),
                         alt.Tooltip("gpci_work:Q", title="GPCI Work", format=".4f"), alt.Tooltip("gpci_pe:Q", title="GPCI PE", format=".4f"),
                         alt.Tooltip("gpci_mp:Q", title="GPCI MP", format=".4f")]
            ).properties(height=500)
            avg_line = alt.Chart(pd.DataFrame({"x": [avg_allowed]})).mark_rule(color=COLORS["negative"], strokeDash=[5, 5], strokeWidth=2).encode(x="x:Q")
            st.altair_chart(bar_chart + avg_line, use_container_width=True)
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Highest", format_currency(comparison_data["allowed"].max()))
            with col2:
                st.metric("Lowest", format_currency(comparison_data["allowed"].min()))
            with col3:
                st.metric("Average", format_currency(avg_allowed))
            with col4:
                spread = comparison_data["allowed"].max() - comparison_data["allowed"].min()
                st.metric("Spread", format_currency(spread))
        else:
            st.info("No comparison data available")

except Exception as e:
    st.error(f"Error loading data: {e}")
    st.info("Ensure the database is running and analytics views are created.")
    st.code(str(e))

# Footer with data source footnote
st.markdown("---")
st.caption("Medicare utilization data: CMS Medicare Physician & Other Practitioners Public Use File (2023, National)")
