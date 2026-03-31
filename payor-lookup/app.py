"""
Government Plan Lookup Tool

Fuzzy-match a payor name against CMS-registered plans across
Medicare Advantage, Medicaid Managed Care, and ACA/HIX.
Data source: drinf.v_plan_master (~13,450 plans).
Alias expansion: payor_aliases.csv bridges common abbreviations
(BCBS, UHC, etc.) to CMS-registered names.
Confirmed matches are saved to drinf.payor_lookups for instant recall.
"""

import streamlit as st
import pandas as pd
import numpy as np
import io
from typing import Optional, List, Tuple
from rapidfuzz import fuzz
from pathlib import Path
from data_loader import (
    load_plan_master, find_saved_lookup, save_lookup,
    load_saved_lookups, delete_lookup,
)

# Path to alias file in healthcare_data repo
ALIAS_PATH = Path(__file__).parent.parent.parent / "healthcare_data" / "loaders" / "data" / "payor_aliases.csv"

# State name -> abbreviation for detecting state mentions in queries
STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}


@st.cache_data
def load_aliases() -> pd.DataFrame:
    """Load the payor alias CSV."""
    if not ALIAS_PATH.exists():
        return pd.DataFrame(columns=["alias", "canonical_carrier", "canonical_plan", "state", "notes", "alias_lower"])
    df = pd.read_csv(ALIAS_PATH, comment="#", skipinitialspace=True)
    df = df.dropna(subset=["alias"])
    df["alias_lower"] = df["alias"].str.lower().str.strip()
    return df


def detect_state(query: str) -> Optional[str]:
    """Try to extract a state from the query string."""
    q = query.lower()
    # Check full state names first (longest match wins)
    for name, abbrev in sorted(STATE_NAMES.items(), key=lambda x: -len(x[0])):
        if name in q:
            return abbrev
    # Check 2-letter abbreviations at word boundaries
    words = q.upper().split()
    for w in words:
        if w in STATE_NAMES.values():
            return w
    return None


def expand_aliases(query: str, aliases: pd.DataFrame) -> Tuple[str, List[str]]:
    """
    Check query against alias table. Returns:
      - expanded_query: the query with alias terms replaced by canonical names
      - matched_aliases: list of human-readable alias match descriptions
    """
    q_lower = query.lower().strip()
    matched = []
    expanded_terms = []

    # Sort aliases longest-first so "BCBS TN" matches before "BCBS"
    sorted_aliases = aliases.sort_values(
        by="alias_lower", key=lambda s: s.str.len(), ascending=False
    )

    remaining = q_lower
    for _, row in sorted_aliases.iterrows():
        alias = row["alias_lower"]
        if alias in remaining:
            # Build the canonical replacement
            parts = []
            if pd.notna(row.get("canonical_carrier")) and row["canonical_carrier"]:
                parts.append(row["canonical_carrier"])
            if pd.notna(row.get("canonical_plan")) and row["canonical_plan"]:
                parts.append(row["canonical_plan"])
            canonical = " ".join(parts) if parts else ""

            if canonical:
                remaining = remaining.replace(alias, canonical.lower())
                matched.append(f'"{row["alias"]}" \u2192 {canonical}')

    expanded_query = remaining if remaining != q_lower else query
    return expanded_query, matched


# Generic healthcare terms that dilute fuzzy matching — removed from both
# the query and candidate strings so distinctive tokens carry more weight.
STOP_WORDS = {
    "HEALTH", "HEALTHCARE", "PLAN", "PLANS", "INSURANCE", "COMPANY",
    "GROUP", "INC", "LLC", "CORP", "CORPORATION", "OF", "THE", "AND",
    "MANAGED", "CARE", "MEDICAL", "SERVICES", "NETWORK", "COMMUNITY",
    "ADVANTAGE", "COMPLETE", "TOTAL", "PLUS", "SELECT", "CHOICE",
    "PREFERRED", "PREMIUM", "BENEFITS", "PROGRAM", "SOLUTIONS",
}


def _remove_stop_words(text: str) -> str:
    """Remove generic healthcare stop words, keeping distinctive tokens."""
    tokens = text.upper().split()
    filtered = [t for t in tokens if t not in STOP_WORDS]
    # Fall back to original if everything was stripped
    return " ".join(filtered) if filtered else text.upper()


def score_matches(df: pd.DataFrame, query: str, aliases: pd.DataFrame,
                  state_filter: Optional[str], top_n: int) -> Tuple[pd.DataFrame, str, List[str]]:
    """Score every plan against the query (with alias expansion), return top_n results."""

    # Step 1: Expand aliases
    expanded_query, alias_matches = expand_aliases(query, aliases)

    q_raw = expanded_query.upper()
    q_filtered = _remove_stop_words(expanded_query)

    # Build combined search fields: "plan_name carrier_name"
    combined_raw = (
        df["plan_name"].fillna("") + " " + df["carrier_name"].fillna("")
    ).str.upper().tolist()

    # Stop-word-filtered versions for primary scoring
    combined_filtered = [_remove_stop_words(c) for c in combined_raw]

    # Primary score: token_set_ratio on filtered text (distinctive tokens)
    scores = np.array([fuzz.token_set_ratio(q_filtered, c) for c in combined_filtered], dtype=float)

    # Secondary score: WRatio on raw text (catches partial/substring matches)
    wratios = np.array([fuzz.WRatio(q_raw, c) for c in combined_raw], dtype=float)

    # Blend: 70% token_set (filtered), 30% WRatio (raw)
    blended = 0.7 * scores + 0.3 * wratios

    # Parent organization boost: if query matches a parent org, boost all its plans
    parent_orgs = df["parent_organization"].fillna("").str.upper().tolist()
    parent_filtered = [_remove_stop_words(p) for p in parent_orgs]
    parent_scores = np.array([fuzz.token_set_ratio(q_filtered, p) for p in parent_filtered], dtype=float)
    # Apply a boost (up to +10) when the query strongly matches the parent org
    blended = blended + np.clip((parent_scores - 70) * 0.33, 0, 10)

    # Determine effective state: explicit filter takes priority, then query detection, then alias
    effective_state = state_filter
    if not effective_state:
        effective_state = detect_state(query)
    if not effective_state:
        for _, row in aliases.iterrows():
            if row["alias_lower"] in query.lower() and pd.notna(row.get("state")) and row["state"]:
                effective_state = str(row["state"]).strip()
                break

    # State boost
    if effective_state:
        state_match = (df["state"].fillna("") == effective_state).astype(float)
        blended = blended + (state_match * 15)  # strong boost when state is known

    results = df.copy()
    results["Match Score"] = blended
    return results.sort_values("Match Score", ascending=False).head(top_n), expanded_query, alias_matches


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Government Plan Lookup",
    page_icon="\U0001f50d",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

df = load_plan_master()
aliases = load_aliases()

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("Government Plan Lookup")
st.markdown(
    "Enter a payor name \u2014 however it appears on a claim, denial, or roster \u2014 "
    "and this tool will find the closest matching CMS-registered government plan."
)

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_single, tab_bulk = st.tabs(["Single Lookup", "Bulk Lookup"])

# ---------------------------------------------------------------------------
# Tab 1: Single Lookup
# ---------------------------------------------------------------------------

with tab_single:
    col_query, col_state = st.columns([3, 1])
    with col_query:
        query = st.text_input(
            "Payor name",
            placeholder='e.g. "BCBS", "UHC Community Plan", "Molina"',
        )
    with col_state:
        state_options = ["All states"] + sorted(
            [s for s in df["state"].dropna().unique() if s]
        )
        state_selection = st.selectbox("State", state_options)

    state_filter = None if state_selection == "All states" else state_selection

    col_opts, _ = st.columns([1, 2])
    with col_opts:
        top_n = st.slider("Results to show", min_value=3, max_value=25, value=10)

    if query:
        # --- Check for a saved/verified match first ---
        saved = find_saved_lookup(query)
        if saved is not None:
            st.success(f'**Verified match** for "{query}"')
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Plan Name", saved["plan_name"])
            c2.metric("Carrier", saved["carrier_name"] or "\u2014")
            c3.metric("LOB / State", f"{saved['lob']} \u2014 {saved['state'] or 'National'}")
            c4.metric("Confirmed Score", f"{saved['match_score']}%")
            if saved.get("notes"):
                st.caption(f"Note: {saved['notes']}")
            st.markdown("---")
            st.caption("Fuzzy results shown below for comparison \u2014 confirm a different match to update.")

        # --- Fuzzy matching ---
        results, expanded_query, alias_matches = score_matches(df, query, aliases, state_filter, top_n)

        # Show alias expansion if it happened
        if alias_matches:
            with st.expander("Alias expansion applied", expanded=True):
                for m in alias_matches:
                    st.markdown(f"- {m}")
                if expanded_query.lower() != query.lower():
                    st.caption(f'Searching for: **{expanded_query}**')

        st.markdown(f"### Top {len(results)} matches")

        # Render each result row with a confirm button
        # Header row
        hdr = st.columns([1, 3, 2, 1, 1, 1, 1])
        hdr[0].markdown("**Score**")
        hdr[1].markdown("**Plan Name**")
        hdr[2].markdown("**Carrier**")
        hdr[3].markdown("**LOB**")
        hdr[4].markdown("**State**")
        hdr[5].markdown("**Members**")
        hdr[6].markdown("")

        for idx, (_, row) in enumerate(results.iterrows()):
            cols = st.columns([1, 3, 2, 1, 1, 1, 1])
            cols[0].write(f"{row['Match Score']:.1f}%")
            cols[1].write(row["plan_name"] or "")
            cols[2].write(row["carrier_name"] or "")
            cols[3].write(row["lob"] or "")
            cols[4].write(row["state"] or "\u2014")
            cols[5].write(f"{row['membership']:,.0f}" if pd.notna(row["membership"]) else "\u2014")
            if cols[6].button("Confirm", key=f"confirm_{idx}"):
                save_lookup(query, row, row["Match Score"])
                st.rerun()

        # Best match summary
        top_match = results.iloc[0]
        st.markdown("---")
        st.markdown("**Best match details:**")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Plan Name", top_match["plan_name"])
        c2.metric("LOB", top_match["lob"])
        c3.metric("State", top_match["state"] or "National")
        c4.metric("Match Score", f"{top_match['Match Score']:.1f}%")

    else:
        # Show summary stats when no search
        st.markdown("---")
        st.markdown("### Dataset Summary")
        c1, c2, c3 = st.columns(3)

        lob_counts = df["lob"].value_counts()
        c1.metric("Medicare Advantage Plans", f"{lob_counts.get('MA', 0):,}")
        c2.metric("Medicaid Managed Care Plans", f"{lob_counts.get('Medicaid', 0):,}")
        c3.metric("ACA / HIX Plans", f"{lob_counts.get('HIX', 0):,}")

        st.caption(
            "Data sources: CMS MA Landscape (2026), CMS Medicaid MCO Enrollment Report (2024), "
            "CMS HIX PUF (2025). Total: {:,} plans.".format(len(df))
        )
        st.caption(f"Alias table: {len(aliases)} entries loaded from payor_aliases.csv")

    # Saved lookups viewer
    st.markdown("---")
    with st.expander("Saved Lookups"):
        saved_df = load_saved_lookups()
        if saved_df.empty:
            st.info("No confirmed matches yet. Search for a payor and click Confirm to save a mapping.")
        else:
            st.caption(f"{len(saved_df)} saved mappings")
            for _, srow in saved_df.iterrows():
                cols = st.columns([3, 3, 1, 1, 1])
                cols[0].write(f"**{srow['lookup_value']}**")
                cols[1].write(f"{srow['plan_name']} ({srow['lob']})")
                cols[2].write(srow["state"] or "\u2014")
                cols[3].write(f"{srow['match_score']}%")
                if cols[4].button("Delete", key=f"del_{srow['id']}"):
                    delete_lookup(srow["id"])
                    st.rerun()

# ---------------------------------------------------------------------------
# Tab 2: Bulk Lookup
# ---------------------------------------------------------------------------

with tab_bulk:
    st.markdown(
        "Paste up to **25 rows** from a spreadsheet. Each row should have a **payor name** "
        "in the first column, and optionally a **state abbreviation** in the second column."
    )

    bulk_input = st.text_area(
        "Paste payor names (one per line, tab-separated for state column)",
        height=200,
        placeholder="Superior HealthPlan\tTX\nMolina Healthcare\nBCBS\tIL",
    )

    if bulk_input.strip():
        # Parse pasted input
        lines = [l for l in bulk_input.strip().splitlines() if l.strip()]

        if len(lines) > 25:
            st.error(f"Please paste 25 rows or fewer (you pasted {len(lines)}).")
        else:
            rows_parsed = []
            for line in lines:
                parts = line.split("\t")
                name = parts[0].strip()
                state = parts[1].strip().upper() if len(parts) > 1 and parts[1].strip() else None
                if name:
                    rows_parsed.append({"payor_name": name, "state": state})

            if rows_parsed and st.button("Run Bulk Lookup", type="primary"):
                bulk_results = []
                progress = st.progress(0)

                for i, entry in enumerate(rows_parsed):
                    state_f = entry["state"]
                    matches, _, _ = score_matches(df, entry["payor_name"], aliases, state_f, 1)
                    top = matches.iloc[0]
                    bulk_results.append({
                        "Input Payor": entry["payor_name"],
                        "Input State": entry["state"] or "",
                        "Top Match": top["plan_name"],
                        "Carrier": top["carrier_name"] or "",
                        "LOB": top["lob"] or "",
                        "State": top["state"] or "",
                        "Members": f"{top['membership']:,.0f}" if pd.notna(top["membership"]) else "",
                        "Match Score": f"{top['Match Score']:.1f}%",
                    })
                    progress.progress((i + 1) / len(rows_parsed))

                progress.empty()
                results_df = pd.DataFrame(bulk_results)

                st.markdown(f"### Results ({len(results_df)} rows)")
                st.dataframe(results_df, use_container_width=True, hide_index=True)

                # Download button
                csv_data = results_df.to_csv(index=False)
                st.download_button(
                    label="Download CSV",
                    data=csv_data,
                    file_name="payor_lookup_results.csv",
                    mime="text/csv",
                )
