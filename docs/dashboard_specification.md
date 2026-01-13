# MPFS Analytics Dashboard — BI Specification

**Version:** 1.0
**Date:** January 2026
**Scope:** Medicare Physician Fee Schedule only (no claims, no contract rates)

---

## 1. Dashboard Overview

### 1.1 Purpose
Provide actionable insights into Medicare Physician Fee Schedule (MPFS) reimbursement trends, geographic variation, and change drivers. Enable RCM, payor contracting, and finance teams to:
- Monitor baseline Medicare rates for budgeting and contract benchmarking
- Identify codes with significant reimbursement changes
- Understand geographic payment variation by locality
- Decompose payment changes into RVU, GPCI, and conversion factor components

### 1.2 Target Users

| User Group | Primary Use Case |
|------------|------------------|
| **RCM (Revenue Cycle)** | Track reimbursement trends for high-volume codes; identify codes at risk of payment cuts |
| **Payor Contracting** | Benchmark commercial rates against Medicare; understand locality-based variation |
| **Finance** | Budget projections based on CF trends; model impact of CMS policy changes |

### 1.3 Questions Answered by Page

| Page | Key Questions |
|------|---------------|
| **1. Baseline Monitor** | What is the current CF? How have overall MPFS rates trended? Which codes changed most this year? |
| **2. Code Trend Explorer** | How has a specific code's reimbursement changed over time? How does it compare across localities? |
| **3. GPCI Locality Explorer** | Which localities have the highest/lowest GPCIs? Where are GPCIs changing most? |
| **4. Locality Spread** | How much does payment vary by geography for a given code? Which localities pay highest/lowest? |
| **5. Change Decomposition** | Why did a code's payment change? Was it driven by RVU, GPCI, or CF? |

### 1.4 Global Filters (All Pages)

| Filter | Type | Source | Default |
|--------|------|--------|---------|
| **Year** | Single-select dropdown | `v_cf_clean.year` | Latest year (2026) |
| **Facility/Non-Facility** | Toggle or radio | N/A (controls which `allowed_*` field to display) | Non-Facility |
| **Payable Codes Only** | Checkbox | `v_rvu_clean.status_code NOT IN ('B', 'I', 'N', 'X')` | Checked (ON) |

**Payable Codes Logic:**
Status codes to EXCLUDE when "Payable Codes Only" is checked:
- `B` = Bundled
- `I` = Invalid for Medicare
- `N` = Non-covered
- `X` = Excluded from fee schedule

---

## 2. Data Sources

### 2.1 View Definitions

| View | Grain | Row Count | Primary Use |
|------|-------|-----------|-------------|
| `drinf.v_rvu_clean` | year × hcpcs_mod | ~160K | Code-level RVU data, descriptions, status codes |
| `drinf.v_gpci_clean` | year × locality_id | ~1K | Locality GPCI values |
| `drinf.v_gpci_yoy` | year × locality_id | ~1K | GPCI with YoY changes |
| `drinf.v_cf_clean` | year | 9 | Conversion factor by year |
| `drinf.v_mpfs_allowed` | year × locality_id × hcpcs_mod | ~17M | Full allowed calculation |
| `drinf.v_mpfs_allowed_yoy` | year × locality_id × hcpcs_mod | ~17M | Allowed with YoY changes |
| `drinf.v_mpfs_decomp` | year × locality_id × hcpcs_mod | ~15M | Change decomposition |

### 2.2 Join Strategy

**Preferred approach:** Use a single view per page to avoid joins in the BI tool.

If joins are required:
- `v_rvu_clean` to `v_mpfs_allowed`: JOIN ON `year = year AND hcpcs_mod = hcpcs_mod`
- `v_gpci_clean` to `v_mpfs_allowed`: JOIN ON `year = year AND locality_id = locality_id`

**Performance note:** For large views (v_mpfs_allowed*), consider creating aggregated extracts or materialized views filtered to relevant codes/localities.

---

## 3. Page Specifications

---

### Page 1 — Medicare Baseline Monitor

**Purpose:** Executive overview of MPFS trends and significant YoY changes.

**Source Views:**
- `v_cf_clean` (CF trend)
- `v_mpfs_allowed_yoy` (top movers)
- `v_rvu_clean` (code counts)

---

#### Visual 1.1: Conversion Factor Trend (Line Chart)

| Attribute | Value |
|-----------|-------|
| **Source** | `v_cf_clean` |
| **Chart Type** | Line chart with data points |
| **X-Axis** | `year` (categorical) |
| **Y-Axis** | `conversion_factor` |
| **Filters** | None |
| **Sort** | `year` ascending |
| **Default** | Show all years |
| **Formatting** | Y-axis: currency format $##.####; show data labels |
| **Tooltip** | Year: {year}, Conversion Factor: ${conversion_factor} |

---

#### Visual 1.2: KPI Cards (4 cards in row)

| KPI | Source | Measure | Filter |
|-----|--------|---------|--------|
| **Current CF** | `v_cf_clean` | `conversion_factor` WHERE `year = [Selected Year]` | Selected year |
| **CF YoY Change** | `v_cf_clean` | `(CF[current] - CF[prior]) / CF[prior] * 100` | Calculate from two years |
| **Total Payable Codes** | `v_rvu_clean` | `COUNT(DISTINCT hcpcs_mod)` WHERE status_code payable | Selected year, payable filter |
| **Codes with Payment Cut** | `v_mpfs_allowed_yoy` | `COUNT(DISTINCT hcpcs_mod)` WHERE `allowed_nonfacility_pct_change < 0` | Selected year, single locality (national/CA-00) |

**Display format:** Large number with subtitle label. YoY Change: show as percentage with up/down arrow indicator.

---

#### Visual 1.3: Top Payment Increases (Table)

| Attribute | Value |
|-----------|-------|
| **Source** | `v_mpfs_allowed_yoy` |
| **Chart Type** | Table |
| **Columns** | `hcpcs`, `modifier`, `description`, `allowed_nonfacility_py`, `allowed_nonfacility`, `allowed_nonfacility_change`, `allowed_nonfacility_pct_change` |
| **Column Headers** | CPT, Mod, Description, Prior Year $, Current $, $ Change, % Change |
| **Filters** | `year = [Selected Year]`, `locality_id = 'AL-00'` (use Alabama as national proxy or pick one), Payable codes only |
| **Sort** | `allowed_nonfacility_change` DESCENDING |
| **Row Limit** | TOP 15 |
| **Formatting** | Currency for $ columns; % Change: conditional color (green >0, red <0) |
| **Tooltip** | Work RVU: {w_rvu}, PE RVU: {pe_rvu_nonfacility}, MP RVU: {mp_rvu} |

---

#### Visual 1.4: Top Payment Decreases (Table)

| Attribute | Value |
|-----------|-------|
| **Source** | `v_mpfs_allowed_yoy` |
| **Chart Type** | Table |
| **Columns** | Same as Visual 1.3 |
| **Filters** | Same as Visual 1.3 |
| **Sort** | `allowed_nonfacility_change` ASCENDING |
| **Row Limit** | TOP 15 |
| **Formatting** | Same as Visual 1.3 |
| **Tooltip** | Same as Visual 1.3 |

---

#### Visual 1.5: Payment Change Distribution (Histogram)

| Attribute | Value |
|-----------|-------|
| **Source** | `v_mpfs_allowed_yoy` |
| **Chart Type** | Histogram |
| **X-Axis** | `allowed_nonfacility_pct_change` (binned: -20 to +20 in 2% increments) |
| **Y-Axis** | Count of `hcpcs_mod` |
| **Filters** | `year = [Selected Year]`, single locality, Payable codes only, exclude nulls |
| **Color** | Bins < 0: muted red (#c62828); Bins >= 0: muted green (#2e7d32) |
| **Reference Line** | Vertical line at 0% |
| **Tooltip** | Range: {bin_start}% to {bin_end}%, Code Count: {count} |

---

### Page 2 — Code Trend Explorer

**Purpose:** Deep-dive into a specific code's reimbursement history and geographic comparison.

**Source Views:**
- `v_mpfs_allowed_yoy` (primary)
- `v_rvu_clean` (for code search/descriptions)

---

#### Control 2.1: Code Search

| Attribute | Value |
|-----------|-------|
| **Type** | Searchable dropdown / typeahead |
| **Source** | `v_rvu_clean` |
| **Display** | `hcpcs_mod` + ` - ` + `description` (e.g., "70553 - MRI brain w/wo contrast") |
| **Value** | `hcpcs_mod` |
| **Default** | None (require selection) or "70553" as example |

---

#### Control 2.2: Locality Multi-Select

| Attribute | Value |
|-----------|-------|
| **Type** | Multi-select dropdown with search |
| **Source** | `v_gpci_clean` |
| **Display** | `locality_name` + ` (` + `locality_id` + `)` |
| **Value** | `locality_id` |
| **Default** | "CA-18" (Los Angeles), "NY-01" (Manhattan), "AL-00" (Alabama - rural baseline) |
| **Limit** | Max 5 selections for performance |

---

#### Control 2.3: Facility Toggle

| Attribute | Value |
|-----------|-------|
| **Type** | Radio buttons or toggle |
| **Options** | "Non-Facility" / "Facility" |
| **Effect** | Switches between `allowed_nonfacility*` and `allowed_facility*` fields |
| **Default** | Non-Facility |

---

#### Visual 2.1: Allowed Amount Trend (Line Chart)

| Attribute | Value |
|-----------|-------|
| **Source** | `v_mpfs_allowed_yoy` |
| **Chart Type** | Multi-series line chart |
| **X-Axis** | `year` |
| **Y-Axis** | `allowed_nonfacility` (or `allowed_facility` per toggle) |
| **Series** | One line per selected `locality_id` |
| **Filters** | `hcpcs_mod = [Selected Code]`, `locality_id IN [Selected Localities]` |
| **Sort** | `year` ascending |
| **Formatting** | Y-axis: currency; distinct colors per locality |
| **Tooltip** | Locality: {locality_name}, Year: {year}, Allowed: ${allowed}, Work RVU: {w_rvu}, CF: ${conversion_factor} |

---

#### Visual 2.2: YoY Detail Table

| Attribute | Value |
|-----------|-------|
| **Source** | `v_mpfs_allowed_yoy` |
| **Chart Type** | Table |
| **Columns** | `year`, `locality_name`, `allowed_nonfacility`, `allowed_nonfacility_py`, `allowed_nonfacility_change`, `allowed_nonfacility_pct_change`, `w_rvu`, `conversion_factor` |
| **Column Headers** | Year, Locality, Current $, Prior $, $ Chg, % Chg, Work RVU, CF |
| **Filters** | `hcpcs_mod = [Selected Code]`, `locality_id IN [Selected Localities]` |
| **Sort** | `year` descending, then `locality_name` |
| **Formatting** | Conditional color on % Chg |
| **Export** | Enable CSV/Excel export |

---

#### Visual 2.3: Locality Comparison Bar Chart (Latest Year)

| Attribute | Value |
|-----------|-------|
| **Source** | `v_mpfs_allowed` |
| **Chart Type** | Horizontal bar chart |
| **Y-Axis** | `locality_name` |
| **X-Axis** | `allowed_nonfacility` |
| **Filters** | `hcpcs_mod = [Selected Code]`, `year = [Latest Year]`, Payable codes only |
| **Sort** | `allowed_nonfacility` descending |
| **Row Limit** | TOP 20 localities |
| **Color** | Single color (accent blue #1565c0); highlight selected localities |
| **Reference Line** | National average (mean of all localities) |
| **Tooltip** | Locality: {locality_name}, Allowed: ${allowed_nonfacility}, GPCI Work: {gpci_work}, GPCI PE: {gpci_pe} |

---

### Page 3 — GPCI Locality Explorer

**Purpose:** Analyze geographic payment adjustments and identify localities with significant GPCI changes.

**Source Views:**
- `v_gpci_yoy` (primary)
- `v_gpci_clean` (for simple lookups)

---

#### Visual 3.1: Locality GPCI Rank Table (Latest Year)

| Attribute | Value |
|-----------|-------|
| **Source** | `v_gpci_yoy` |
| **Chart Type** | Table with conditional formatting |
| **Columns** | `locality_name`, `state`, `gpci_work`, `gpci_pe`, `gpci_mp`, calculated `gpci_composite` |
| **Column Headers** | Locality, State, Work GPCI, PE GPCI, MP GPCI, Composite |
| **Calculated Field** | `gpci_composite = (gpci_work + gpci_pe + gpci_mp) / 3` (simple average for ranking) |
| **Filters** | `year = [Selected Year]` |
| **Sort** | `gpci_composite` descending |
| **Formatting** | Heatmap coloring on GPCI columns (higher = darker blue) |
| **Tooltip** | YoY Change - Work: {gpci_work_change}, PE: {gpci_pe_change}, MP: {gpci_mp_change} |

---

#### Visual 3.2: Largest GPCI YoY Changes (Table)

| Attribute | Value |
|-----------|-------|
| **Source** | `v_gpci_yoy` |
| **Chart Type** | Table |
| **Columns** | `locality_name`, `state`, `gpci_work_py`, `gpci_work`, `gpci_work_change`, `gpci_work_pct_change` |
| **Column Headers** | Locality, State, Prior Work, Current Work, Change, % Change |
| **Filters** | `year = [Selected Year]`, exclude nulls on `gpci_work_py` |
| **Sort** | `ABS(gpci_work_change)` descending |
| **Row Limit** | TOP 15 |
| **Formatting** | Conditional color on Change column |
| **Note** | Replicate for PE and MP GPCI as separate tables or tabs |

---

#### Visual 3.3: GPCI Component Trends (Line Chart)

| Attribute | Value |
|-----------|-------|
| **Source** | `v_gpci_yoy` |
| **Chart Type** | Multi-series line chart |
| **X-Axis** | `year` |
| **Y-Axis** | GPCI value |
| **Series** | Three lines: `gpci_work`, `gpci_pe`, `gpci_mp` |
| **Filters** | `locality_id = [Selected Locality]` (single-select control) |
| **Sort** | `year` ascending |
| **Formatting** | Distinct colors per component; Y-axis scale 0.5 to 1.5 |
| **Tooltip** | Year: {year}, Work: {gpci_work}, PE: {gpci_pe}, MP: {gpci_mp} |

---

#### Control 3.1: Locality Single-Select (for trend chart)

| Attribute | Value |
|-----------|-------|
| **Type** | Searchable dropdown |
| **Source** | `v_gpci_clean` WHERE `year = [Latest Year]` |
| **Display** | `locality_name` + ` (` + `state` + `)` |
| **Value** | `locality_id` |
| **Default** | "CA-18" |

---

### Page 4 — Locality Spread

**Purpose:** Quantify geographic payment variation for specific codes or code baskets.

**Source Views:**
- `v_mpfs_allowed` (primary)

---

#### Mode A: Single Code Analysis

---

#### Control 4.1: Code Select

| Attribute | Value |
|-----------|-------|
| **Type** | Searchable dropdown |
| **Source** | `v_rvu_clean` WHERE `year = [Latest Year]` |
| **Display** | `hcpcs_mod` + ` - ` + `description` |
| **Value** | `hcpcs_mod` |
| **Default** | "70553" |

---

#### Visual 4.1: Locality Payment Bar Chart

| Attribute | Value |
|-----------|-------|
| **Source** | `v_mpfs_allowed` |
| **Chart Type** | Horizontal bar chart |
| **Y-Axis** | `locality_name` |
| **X-Axis** | `allowed_nonfacility` |
| **Filters** | `hcpcs_mod = [Selected Code]`, `year = [Selected Year]` |
| **Sort** | `allowed_nonfacility` descending |
| **Color** | Gradient based on value (low = light, high = dark) |
| **Reference Lines** | Mean (solid), Median (dashed), Min/Max (dotted) |
| **Tooltip** | Locality: {locality_name}, Allowed: ${allowed_nonfacility}, GPCI Work: {gpci_work}, GPCI PE: {gpci_pe}, GPCI MP: {gpci_mp} |

---

#### Visual 4.2: Spread KPI Cards

| KPI | Calculation |
|-----|-------------|
| **Maximum** | `MAX(allowed_nonfacility)` |
| **Minimum** | `MIN(allowed_nonfacility)` |
| **Spread (Max - Min)** | `MAX - MIN` |
| **Max/Min Ratio** | `MAX / MIN` |
| **Std Dev** | `STDEV(allowed_nonfacility)` |
| **Coefficient of Variation** | `STDEV / AVG * 100` |

**Display:** 6 KPI cards showing geographic variation metrics.

---

#### Visual 4.3: Spread Percentile Distribution

| Attribute | Value |
|-----------|-------|
| **Source** | `v_mpfs_allowed` |
| **Chart Type** | Box plot or violin plot |
| **X-Axis** | Single category (the selected code) |
| **Y-Axis** | `allowed_nonfacility` |
| **Statistics** | Min, 25th percentile, Median, 75th percentile, Max |
| **Filters** | `hcpcs_mod = [Selected Code]`, `year = [Selected Year]` |

---

#### Mode B: Basket Index (Optional Enhancement)

**Basket Table Structure:**

```sql
CREATE TABLE drinf.code_basket (
    basket_id VARCHAR(20) PRIMARY KEY,
    basket_name VARCHAR(100),
    hcpcs VARCHAR(10),
    modifier VARCHAR(5),
    weight NUMERIC(5,4) DEFAULT 1.0,
    UNIQUE(basket_id, hcpcs, modifier)
);

-- Example basket: "Advanced Imaging"
INSERT INTO drinf.code_basket VALUES
('ADV_IMG', 'Advanced Imaging', '70553', NULL, 1.0),
('ADV_IMG', 'Advanced Imaging', '74177', NULL, 1.0),
('ADV_IMG', 'Advanced Imaging', '71260', NULL, 1.0),
('ADV_IMG', 'Advanced Imaging', '72148', NULL, 1.0);
```

**Basket Index Calculation:**

```sql
-- Weighted average allowed by locality
SELECT
    b.basket_name,
    a.year,
    a.locality_id,
    a.locality_name,
    SUM(a.allowed_nonfacility * b.weight) / SUM(b.weight) AS basket_index
FROM v_mpfs_allowed a
JOIN drinf.code_basket b ON a.hcpcs = b.hcpcs
    AND COALESCE(a.modifier, '') = COALESCE(b.modifier, '')
GROUP BY b.basket_name, a.year, a.locality_id, a.locality_name;
```

**Visual 4.4: Basket Index by Locality**

| Attribute | Value |
|-----------|-------|
| **Source** | Basket index query above |
| **Chart Type** | Horizontal bar chart |
| **Y-Axis** | `locality_name` |
| **X-Axis** | `basket_index` |
| **Filters** | `basket_name = [Selected Basket]`, `year = [Selected Year]` |
| **Sort** | `basket_index` descending |

---

### Page 5 — Change Decomposition

**Purpose:** Isolate the drivers of payment change (RVU, GPCI, CF) for a specific code and locality.

**Source Views:**
- `v_mpfs_decomp` (primary)

---

#### Control 5.1: Code Select

| Attribute | Value |
|-----------|-------|
| **Type** | Searchable dropdown |
| **Source** | `v_rvu_clean` |
| **Display** | `hcpcs_mod` + ` - ` + `description` |
| **Value** | `hcpcs_mod` |
| **Default** | "70553" |

---

#### Control 5.2: Locality Select

| Attribute | Value |
|-----------|-------|
| **Type** | Searchable dropdown |
| **Source** | `v_gpci_clean` WHERE `year = [Latest Year]` |
| **Display** | `locality_name` |
| **Value** | `locality_id` |
| **Default** | "CA-18" |

---

#### Control 5.3: Year Select

| Attribute | Value |
|-----------|-------|
| **Type** | Dropdown |
| **Source** | `v_cf_clean` WHERE `year > MIN(year)` (exclude first year - no prior) |
| **Value** | `year` |
| **Default** | Latest year (2026) |

---

#### Visual 5.1: Waterfall Chart (Non-Facility)

| Attribute | Value |
|-----------|-------|
| **Source** | `v_mpfs_decomp` |
| **Chart Type** | Waterfall chart |
| **Categories** | Prior Year Allowed → CF Effect → GPCI Effect → RVU Effect → Current Year Allowed |
| **Values** | `allowed_nonfacility_py`, `cf_effect_nonfacility`, `gpci_effect_nonfacility`, `rvu_effect_nonfacility`, `allowed_nonfacility` |
| **Filters** | `hcpcs_mod = [Selected Code]`, `locality_id = [Selected Locality]`, `year = [Selected Year]` |
| **Color** | Start/End bars: gray; Positive changes: green; Negative changes: red |
| **Data Labels** | Show values on each bar |
| **Tooltip** | See tooltip spec below |

**Waterfall Category Mapping:**

| Bar | Value | Type |
|-----|-------|------|
| Prior Year | `allowed_nonfacility_py` | Start |
| CF Effect | `cf_effect_nonfacility` | Delta (+ or -) |
| GPCI Effect | `gpci_effect_nonfacility` | Delta |
| RVU Effect | `rvu_effect_nonfacility` | Delta |
| Current Year | `allowed_nonfacility` | End |

---

#### Visual 5.2: Waterfall Chart (Facility)

Same structure as Visual 5.1, using `*_facility` fields.

---

#### Visual 5.3: Decomposition Detail Table

| Attribute | Value |
|-----------|-------|
| **Source** | `v_mpfs_decomp` |
| **Chart Type** | Table |
| **Columns** | `year`, `allowed_nonfacility_py`, `allowed_nonfacility`, `total_change_nonfacility`, `cf_effect_nonfacility`, `gpci_effect_nonfacility`, `rvu_effect_nonfacility`, `sum_check` |
| **Calculated** | `sum_check = cf_effect + gpci_effect + rvu_effect` |
| **Column Headers** | Year, Prior $, Current $, Total Δ, CF Effect, GPCI Effect, RVU Effect, Sum Check |
| **Filters** | `hcpcs_mod = [Selected Code]`, `locality_id = [Selected Locality]` |
| **Sort** | `year` descending |
| **Formatting** | Currency; conditional color on effects |
| **QA Highlight** | Flag rows where `ABS(total_change - sum_check) > 1.00` (interaction effect threshold) |

---

#### Visual 5.4: Component Input Comparison Table

| Attribute | Value |
|-----------|-------|
| **Source** | `v_mpfs_decomp` |
| **Chart Type** | Table |
| **Columns** | `year`, `w_rvu_py`, `w_rvu`, `gpci_work_py`, `gpci_work`, `cf_py`, `conversion_factor` |
| **Column Headers** | Year, Work RVU (PY), Work RVU (CY), GPCI Work (PY), GPCI Work (CY), CF (PY), CF (CY) |
| **Filters** | Same as Visual 5.3 |
| **Purpose** | Audit trail showing raw inputs used in decomposition |

---

#### Tooltip Specification for Waterfall

| Bar | Tooltip Content |
|-----|-----------------|
| **Prior Year** | Prior Year Allowed: ${allowed_py}<br>Year: {year - 1} |
| **CF Effect** | CF Effect: ${cf_effect}<br>Calculation: Prior RVUs × Prior GPCIs × Current CF - Prior Allowed<br>Prior CF: ${cf_py}, Current CF: ${conversion_factor} |
| **GPCI Effect** | GPCI Effect: ${gpci_effect}<br>Calculation: Prior RVUs × Current GPCIs × Prior CF - Prior Allowed<br>Work GPCI: {gpci_work_py} → {gpci_work} |
| **RVU Effect** | RVU Effect: ${rvu_effect}<br>Calculation: Current RVUs × Prior GPCIs × Prior CF - Prior Allowed<br>Work RVU: {w_rvu_py} → {w_rvu} |
| **Current Year** | Current Year Allowed: ${allowed}<br>Year: {year} |

---

## 4. Defaults & UX

### 4.1 Default Selections

| Control | Default Value |
|---------|---------------|
| Year | Latest available (2026) |
| Facility/Non-Facility | Non-Facility |
| Payable Codes Only | Checked (ON) |
| Default Code | 70553 (MRI brain) |
| Default Locality | CA-18 (Los Angeles) |
| Top N tables | 15-25 rows |

### 4.2 Performance Recommendations

| Issue | Mitigation |
|-------|------------|
| `v_mpfs_allowed` is ~17M rows | Create filtered extracts by specialty/code range; or materialize to `mv_mpfs_allowed` |
| Locality dropdown lag | Limit to ~110 localities; use search instead of full list |
| Waterfall chart single-row | Pre-filter to single row in data source before visualization |
| YoY calculations | Use pre-computed fields from views; avoid calculating in BI tool |

### 4.3 Export Requirements

| Page | Exportable Elements |
|------|---------------------|
| Page 1 | Top Movers tables (CSV/Excel) |
| Page 2 | YoY Detail table, Locality comparison table |
| Page 3 | GPCI Rank table, YoY Changes table |
| Page 4 | Full locality spread table |
| Page 5 | Decomposition detail table, Input comparison table |

**Export format:** CSV and Excel (.xlsx) with headers matching display names.

---

## 5. Appendix

### 5.1 Status Code Reference

| Code | Meaning | Include in Payable? |
|------|---------|---------------------|
| A | Active | Yes |
| C | Carrier judgment | Yes |
| E | Excluded | No |
| I | Invalid | No |
| M | Measurement | Yes |
| N | Non-covered | No |
| P | Bundled | No |
| R | Restricted | Yes |
| T | Injection | Yes |
| X | Excluded | No |

### 5.2 Color Palette (Stephen Few)

| Use | Color | Hex |
|-----|-------|-----|
| Positive/Increase | Muted Green | #2e7d32 |
| Negative/Decrease | Muted Red | #c62828 |
| Neutral | Gray | #616161 |
| Accent/Selection | Blue | #1565c0 |
| Background | Off-white | #fafafa |

### 5.3 Glossary

| Term | Definition |
|------|------------|
| **RVU** | Relative Value Unit - measure of resource intensity |
| **Work RVU** | Physician work component |
| **PE RVU** | Practice expense component (facility or non-facility) |
| **MP RVU** | Malpractice expense component |
| **GPCI** | Geographic Practice Cost Index - locality adjustment |
| **CF** | Conversion Factor - dollar multiplier applied to adjusted RVUs |
| **Allowed** | Total adjusted RVU × CF = Medicare payment amount |
| **hcpcs_mod** | Composite key: HCPCS code + modifier (e.g., "70553-26") |
| **locality_id** | Composite key: State + locality number (e.g., "CA-18") |
