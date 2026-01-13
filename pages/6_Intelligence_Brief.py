"""
Page 6: Intelligence Brief Generator
Scan web for healthcare news and generate MPFS-informed email briefs
"""
import streamlit as st
import pandas as pd
import altair as alt
import requests
from datetime import datetime
from utils import (
    get_available_years,
    get_code_list,
    get_codes_analysis,
    get_utilization_summary,
    generate_brief_email,
    COLORS,
    format_currency,
    format_percent
)

st.set_page_config(page_title="Intelligence Brief", page_icon="$", layout="wide")

st.title("Intelligence Brief Generator")
st.caption("Scan healthcare news and generate MPFS-informed email briefs")

# ============================================================================
# Web Search Function
# ============================================================================

def search_healthcare_news(query, num_results=5):
    """Search for healthcare news using DuckDuckGo.

    Returns list of dicts with title, snippet, url.
    """
    try:
        # Use DuckDuckGo HTML search (no API key needed)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        url = f"https://html.duckduckgo.com/html/?q={query}+healthcare+news"
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code != 200:
            return []

        # Parse results (simple extraction)
        from html.parser import HTMLParser
        results = []

        # Simple regex-based extraction for MVP
        import re
        # Find result links and snippets
        link_pattern = r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>([^<]+)</a>'
        snippet_pattern = r'<a[^>]+class="result__snippet"[^>]*>([^<]+)</a>'

        links = re.findall(link_pattern, response.text)
        snippets = re.findall(snippet_pattern, response.text)

        for i, (url, title) in enumerate(links[:num_results]):
            snippet = snippets[i] if i < len(snippets) else ""
            # Clean up DuckDuckGo redirect URL
            if 'uddg=' in url:
                import urllib.parse
                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                url = parsed.get('uddg', [url])[0]
            results.append({
                'title': title.strip(),
                'snippet': snippet.strip(),
                'url': url
            })

        return results

    except Exception as e:
        st.warning(f"Web search error: {e}")
        return []


# ============================================================================
# Predefined Code Groups (Radiology Focus)
# ============================================================================

CODE_GROUPS = {
    "MRI Brain": ["70551", "70552", "70553"],
    "MRI Spine": ["72141", "72142", "72146", "72147", "72148", "72149", "72156", "72157", "72158"],
    "CT Head": ["70450", "70460", "70470"],
    "CT Chest": ["71250", "71260", "71270"],
    "CT Abdomen/Pelvis": ["74150", "74160", "74170", "74176", "74177", "74178"],
    "Mammography": ["77065", "77066", "77067"],
    "X-Ray Chest": ["71045", "71046", "71047", "71048"],
    "Ultrasound Abdomen": ["76700", "76705", "76770", "76775"],
    "PET Scan": ["78811", "78812", "78813", "78814", "78815", "78816"],
    "Nuclear Cardiology": ["78451", "78452", "78453", "78454"],
}

# ============================================================================
# Sidebar - Configuration
# ============================================================================

st.sidebar.header("1. Search for News")

search_query = st.sidebar.text_input(
    "Search Topic",
    value="MRI imaging reimbursement",
    help="Enter a healthcare topic to search for recent news"
)

if st.sidebar.button("Search Web", type="primary"):
    with st.spinner("Searching..."):
        st.session_state['news_results'] = search_healthcare_news(search_query)

st.sidebar.markdown("---")

st.sidebar.header("2. Select Codes")

# Code group selector
selected_group = st.sidebar.selectbox(
    "Code Group",
    options=list(CODE_GROUPS.keys()),
    index=0
)

# Show selected codes
selected_codes = CODE_GROUPS[selected_group]
st.sidebar.caption(f"Codes: {', '.join(selected_codes)}")

# Allow custom codes
custom_codes = st.sidebar.text_input(
    "Or enter custom codes (comma-separated)",
    placeholder="70553, 70552, 70551"
)

if custom_codes:
    selected_codes = [c.strip() for c in custom_codes.split(",") if c.strip()]

st.sidebar.markdown("---")

st.sidebar.header("3. Configure Brief")

try:
    years = get_available_years()
    selected_year = st.sidebar.selectbox("Year", options=sorted(years, reverse=True), index=0)
except:
    selected_year = 2026
    st.sidebar.warning("Could not load years from database")

topic_name = st.sidebar.text_input("Brief Topic Name", value=selected_group)

# ============================================================================
# Main Content - Three Columns
# ============================================================================

col_news, col_analysis = st.columns([1, 2])

# ============================================================================
# Column 1: News Results
# ============================================================================

with col_news:
    st.subheader("Recent News")

    if 'news_results' in st.session_state and st.session_state['news_results']:
        for i, result in enumerate(st.session_state['news_results']):
            with st.container():
                st.markdown(f"**[{result['title']}]({result['url']})**")
                st.caption(result['snippet'][:200] + "..." if len(result['snippet']) > 200 else result['snippet'])
                st.markdown("---")
    else:
        st.info("Click 'Search Web' to find recent healthcare news on your topic.")

        # Show example/placeholder
        st.markdown("**Example news topics:**")
        st.markdown("- CMS reimbursement changes")
        st.markdown("- Advanced imaging utilization")
        st.markdown("- Radiology payment cuts")
        st.markdown("- Medicare fee schedule updates")

# ============================================================================
# Column 2: Analysis & Email Generation
# ============================================================================

with col_analysis:
    st.subheader("Generate Intelligence Brief")

    if st.button("Analyze & Generate Brief", type="primary", use_container_width=True):
        with st.spinner("Analyzing MPFS data..."):
            try:
                analysis = get_codes_analysis(selected_codes, selected_year)

                # Get utilization data (use 2023 as most recent)
                try:
                    utilization = get_utilization_summary(selected_codes, 2023)
                except:
                    utilization = None

                if analysis:
                    st.session_state['analysis'] = analysis
                    st.session_state['utilization'] = utilization
                    st.session_state['topic'] = topic_name
                    st.session_state['year'] = selected_year
                    st.success("Analysis complete!")
                else:
                    st.error("No data found for selected codes")
            except Exception as e:
                st.error(f"Analysis error: {e}")

    # Display analysis results
    if 'analysis' in st.session_state:
        analysis = st.session_state['analysis']
        topic = st.session_state.get('topic', topic_name)
        year = st.session_state.get('year', selected_year)

        st.markdown("---")

        # Summary metrics
        summary = analysis['summary']
        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.metric("Avg Change", format_percent(summary['avg_pct_change']))
        with m2:
            st.metric("Total Codes", summary['total_codes'])
        with m3:
            st.metric("Increased", summary['codes_increased'])
        with m4:
            st.metric("Decreased", summary['codes_decreased'])

        # Utilization metrics (if available)
        utilization = st.session_state.get('utilization')
        if utilization and utilization.get('total_services', 0) > 0:
            st.markdown("**Medicare Utilization (2023):**")
            u1, u2, u3 = st.columns(3)
            with u1:
                st.metric("Total Services", f"{utilization['total_services']:,.0f}")
            with u2:
                st.metric("Beneficiaries", f"{utilization['total_beneficiaries']:,.0f}")
            with u3:
                st.metric("Total Medicare $", f"${utilization['total_medicare_payment']/1e6:,.1f}M")

        st.markdown("---")

        # Trend Chart
        st.subheader("Payment Trend")

        codes_df = analysis['codes_df']
        if len(codes_df) > 0:
            # Create comparison bar chart
            chart_data = codes_df.head(10).copy()
            chart_data['change_color'] = chart_data['change'].apply(
                lambda x: 'Increase' if x > 0 else 'Decrease'
            )

            chart = alt.Chart(chart_data).mark_bar().encode(
                y=alt.Y('hcpcs:N', title='CPT Code', sort='-x'),
                x=alt.X('pct_change:Q', title='% Change'),
                color=alt.Color('change_color:N',
                               scale=alt.Scale(
                                   domain=['Increase', 'Decrease'],
                                   range=[COLORS['positive'], COLORS['negative']]
                               ),
                               legend=alt.Legend(title='Direction')),
                tooltip=[
                    alt.Tooltip('hcpcs:N', title='CPT'),
                    alt.Tooltip('description:N', title='Description'),
                    alt.Tooltip('prior_allowed:Q', title='Prior $', format='$.2f'),
                    alt.Tooltip('current_allowed:Q', title='Current $', format='$.2f'),
                    alt.Tooltip('pct_change:Q', title='% Change', format='.1f')
                ]
            ).properties(height=300)

            st.altair_chart(chart, use_container_width=True)

            # Store chart for export
            st.session_state['chart'] = chart

        st.markdown("---")

        # Generate Email
        st.subheader("Email Brief")

        utilization = st.session_state.get('utilization')
        email_content = generate_brief_email(topic, analysis, year, utilization=utilization)
        st.session_state['email_content'] = email_content

        # Display in expandable container
        with st.expander("Preview Email", expanded=True):
            st.markdown(email_content)

        # Copy/Export options
        col_copy, col_export = st.columns(2)

        with col_copy:
            st.download_button(
                "Download as Markdown",
                data=email_content,
                file_name=f"mpfs_brief_{topic.replace(' ', '_')}_{year}.md",
                mime="text/markdown",
                use_container_width=True
            )

        with col_export:
            # Convert to HTML for email
            html_content = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }}
                    table {{ border-collapse: collapse; width: 100%; }}
                    th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                    th {{ background-color: #f5f5f5; }}
                </style>
            </head>
            <body>
            {email_content.replace('|', '</td><td>').replace('---', '<hr>')}
            </body>
            </html>
            """
            st.download_button(
                "Download as HTML",
                data=html_content,
                file_name=f"mpfs_brief_{topic.replace(' ', '_')}_{year}.html",
                mime="text/html",
                use_container_width=True
            )

        # Chart export
        st.markdown("---")
        st.subheader("Export Chart")
        st.info("To save the chart: Right-click on the chart above and select 'Save image as...'")

# ============================================================================
# Footer
# ============================================================================

st.markdown("---")
st.caption(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} | Data: CMS MPFS {selected_year}")
