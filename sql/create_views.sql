-- ============================================================================
-- MPFS Analytics Views
-- Schema: drinf
-- Grain: year × locality × hcpcs_mod (for allowed views)
-- ============================================================================

-- ============================================================================
-- V_RVU_CLEAN
-- Clean RVU data with composite hcpcs_mod key
-- Grain: year × hcpcs_mod (unique)
-- ============================================================================
DROP VIEW IF EXISTS drinf.v_rvu_clean CASCADE;

CREATE VIEW drinf.v_rvu_clean AS
SELECT
    mpfs_year AS year,
    hcpcs,
    modifier,
    hcpcs_mod,
    description,
    status_code,
    -- RVU components (COALESCE to 0 for math, but track nulls)
    work_rvu AS w_rvu,
    facility_pe_rvu AS pe_rvu_facility,
    non_fac_pe_rvu AS pe_rvu_nonfacility,
    mp_rvu,
    -- Pre-computed totals from CMS (for validation)
    facility_total AS total_rvu_facility,
    non_facility_total AS total_rvu_nonfacility,
    -- Flags for null tracking
    CASE WHEN work_rvu IS NULL THEN 1 ELSE 0 END AS w_rvu_null_flag,
    CASE WHEN facility_pe_rvu IS NULL THEN 1 ELSE 0 END AS pe_fac_null_flag,
    CASE WHEN non_fac_pe_rvu IS NULL THEN 1 ELSE 0 END AS pe_nonfac_null_flag,
    CASE WHEN mp_rvu IS NULL THEN 1 ELSE 0 END AS mp_null_flag
FROM drinf.mpfs_rvu
WHERE hcpcs IS NOT NULL
  AND hcpcs_mod IS NOT NULL;

COMMENT ON VIEW drinf.v_rvu_clean IS 'Clean RVU data by year and hcpcs_mod. Grain: year × hcpcs_mod.';

-- ============================================================================
-- V_GPCI_CLEAN
-- Clean GPCI data by locality
-- Grain: year × state × locality_number (unique)
-- ============================================================================
DROP VIEW IF EXISTS drinf.v_gpci_clean CASCADE;

CREATE VIEW drinf.v_gpci_clean AS
SELECT
    mpfs_year AS year,
    state,
    locality_number,
    locality_name,
    -- Composite locality ID for joins
    state || '-' || locality_number AS locality_id,
    -- GPCI components
    gpci_work,
    gpci_pe,
    gpci_mp,
    -- MAC for reference
    mac
FROM drinf.mpfs_gpci
WHERE state IS NOT NULL
  AND locality_number IS NOT NULL;

COMMENT ON VIEW drinf.v_gpci_clean IS 'Clean GPCI data by year and locality. Grain: year × locality_id.';

-- ============================================================================
-- V_CF_CLEAN
-- Conversion factor by year (one value per year)
-- Grain: year (unique)
-- ============================================================================
DROP VIEW IF EXISTS drinf.v_cf_clean CASCADE;

CREATE VIEW drinf.v_cf_clean AS
SELECT DISTINCT
    mpfs_year AS year,
    -- Take the modal (most common) CF for the year, handling any row-level variations
    FIRST_VALUE(conversion_factor) OVER (
        PARTITION BY mpfs_year
        ORDER BY conversion_factor DESC NULLS LAST
    ) AS conversion_factor
FROM drinf.mpfs_rvu
WHERE conversion_factor IS NOT NULL
GROUP BY mpfs_year, conversion_factor;

-- Simpler version - just get distinct year/CF pairs
DROP VIEW IF EXISTS drinf.v_cf_clean CASCADE;

CREATE VIEW drinf.v_cf_clean AS
SELECT
    year,
    conversion_factor
FROM (
    SELECT DISTINCT
        mpfs_year AS year,
        conversion_factor
    FROM drinf.mpfs_rvu
    WHERE conversion_factor IS NOT NULL
) sub
WHERE conversion_factor = (
    SELECT MAX(conversion_factor)
    FROM drinf.mpfs_rvu r2
    WHERE r2.mpfs_year = sub.year
);

COMMENT ON VIEW drinf.v_cf_clean IS 'Conversion factor by year. Grain: year (unique).';

-- ============================================================================
-- V_MPFS_ALLOWED
-- Fully computed allowed amounts by year × locality × hcpcs_mod
-- This is the canonical grain for MPFS analysis
-- ============================================================================
DROP VIEW IF EXISTS drinf.v_mpfs_allowed CASCADE;

CREATE VIEW drinf.v_mpfs_allowed AS
SELECT
    r.year,
    g.locality_id,
    g.state,
    g.locality_number,
    g.locality_name,
    r.hcpcs,
    r.modifier,
    r.hcpcs_mod,
    r.description,
    r.status_code,

    -- Raw RVU inputs
    r.w_rvu,
    r.pe_rvu_facility,
    r.pe_rvu_nonfacility,
    r.mp_rvu,

    -- GPCI inputs
    g.gpci_work,
    g.gpci_pe,
    g.gpci_mp,

    -- Conversion factor
    cf.conversion_factor,

    -- Adjusted RVUs (RVU × GPCI)
    COALESCE(r.w_rvu, 0) * COALESCE(g.gpci_work, 1) AS adj_work,
    COALESCE(r.pe_rvu_facility, 0) * COALESCE(g.gpci_pe, 1) AS adj_pe_facility,
    COALESCE(r.pe_rvu_nonfacility, 0) * COALESCE(g.gpci_pe, 1) AS adj_pe_nonfacility,
    COALESCE(r.mp_rvu, 0) * COALESCE(g.gpci_mp, 1) AS adj_mp,

    -- Total adjusted RVUs
    (COALESCE(r.w_rvu, 0) * COALESCE(g.gpci_work, 1) +
     COALESCE(r.pe_rvu_facility, 0) * COALESCE(g.gpci_pe, 1) +
     COALESCE(r.mp_rvu, 0) * COALESCE(g.gpci_mp, 1)) AS total_adj_facility,

    (COALESCE(r.w_rvu, 0) * COALESCE(g.gpci_work, 1) +
     COALESCE(r.pe_rvu_nonfacility, 0) * COALESCE(g.gpci_pe, 1) +
     COALESCE(r.mp_rvu, 0) * COALESCE(g.gpci_mp, 1)) AS total_adj_nonfacility,

    -- Final allowed amounts (Total Adjusted RVU × CF)
    (COALESCE(r.w_rvu, 0) * COALESCE(g.gpci_work, 1) +
     COALESCE(r.pe_rvu_facility, 0) * COALESCE(g.gpci_pe, 1) +
     COALESCE(r.mp_rvu, 0) * COALESCE(g.gpci_mp, 1)) * COALESCE(cf.conversion_factor, 0) AS allowed_facility,

    (COALESCE(r.w_rvu, 0) * COALESCE(g.gpci_work, 1) +
     COALESCE(r.pe_rvu_nonfacility, 0) * COALESCE(g.gpci_pe, 1) +
     COALESCE(r.mp_rvu, 0) * COALESCE(g.gpci_mp, 1)) * COALESCE(cf.conversion_factor, 0) AS allowed_nonfacility

FROM drinf.v_rvu_clean r
CROSS JOIN drinf.v_gpci_clean g
INNER JOIN drinf.v_cf_clean cf ON cf.year = r.year
WHERE r.year = g.year;

COMMENT ON VIEW drinf.v_mpfs_allowed IS 'Fully computed MPFS allowed amounts. Grain: year × locality_id × hcpcs_mod.';

-- ============================================================================
-- V_MPFS_ALLOWED_YOY
-- Allowed amounts with year-over-year comparisons
-- ============================================================================
DROP VIEW IF EXISTS drinf.v_mpfs_allowed_yoy CASCADE;

CREATE VIEW drinf.v_mpfs_allowed_yoy AS
SELECT
    *,
    -- Prior year values
    LAG(allowed_facility) OVER w AS allowed_facility_py,
    LAG(allowed_nonfacility) OVER w AS allowed_nonfacility_py,
    LAG(w_rvu) OVER w AS w_rvu_py,
    LAG(conversion_factor) OVER w AS conversion_factor_py,

    -- Absolute changes
    allowed_facility - LAG(allowed_facility) OVER w AS allowed_facility_change,
    allowed_nonfacility - LAG(allowed_nonfacility) OVER w AS allowed_nonfacility_change,

    -- Percent changes (handle division by zero)
    CASE
        WHEN LAG(allowed_facility) OVER w > 0
        THEN (allowed_facility - LAG(allowed_facility) OVER w) / LAG(allowed_facility) OVER w * 100
        ELSE NULL
    END AS allowed_facility_pct_change,

    CASE
        WHEN LAG(allowed_nonfacility) OVER w > 0
        THEN (allowed_nonfacility - LAG(allowed_nonfacility) OVER w) / LAG(allowed_nonfacility) OVER w * 100
        ELSE NULL
    END AS allowed_nonfacility_pct_change

FROM drinf.v_mpfs_allowed
WINDOW w AS (PARTITION BY hcpcs_mod, locality_id ORDER BY year);

COMMENT ON VIEW drinf.v_mpfs_allowed_yoy IS 'Allowed amounts with YoY changes. Grain: year × locality_id × hcpcs_mod.';

-- ============================================================================
-- V_GPCI_YOY
-- GPCI values with year-over-year comparisons
-- ============================================================================
DROP VIEW IF EXISTS drinf.v_gpci_yoy CASCADE;

CREATE VIEW drinf.v_gpci_yoy AS
SELECT
    *,
    -- Prior year values
    LAG(gpci_work) OVER w AS gpci_work_py,
    LAG(gpci_pe) OVER w AS gpci_pe_py,
    LAG(gpci_mp) OVER w AS gpci_mp_py,

    -- Absolute changes
    gpci_work - LAG(gpci_work) OVER w AS gpci_work_change,
    gpci_pe - LAG(gpci_pe) OVER w AS gpci_pe_change,
    gpci_mp - LAG(gpci_mp) OVER w AS gpci_mp_change,

    -- Percent changes
    CASE
        WHEN LAG(gpci_work) OVER w > 0
        THEN (gpci_work - LAG(gpci_work) OVER w) / LAG(gpci_work) OVER w * 100
        ELSE NULL
    END AS gpci_work_pct_change,

    CASE
        WHEN LAG(gpci_pe) OVER w > 0
        THEN (gpci_pe - LAG(gpci_pe) OVER w) / LAG(gpci_pe) OVER w * 100
        ELSE NULL
    END AS gpci_pe_pct_change,

    CASE
        WHEN LAG(gpci_mp) OVER w > 0
        THEN (gpci_mp - LAG(gpci_mp) OVER w) / LAG(gpci_mp) OVER w * 100
        ELSE NULL
    END AS gpci_mp_pct_change

FROM drinf.v_gpci_clean
WINDOW w AS (PARTITION BY locality_id ORDER BY year);

COMMENT ON VIEW drinf.v_gpci_yoy IS 'GPCI values with YoY changes. Grain: year × locality_id.';

-- ============================================================================
-- V_MPFS_DECOMP
-- Change decomposition: isolate CF, GPCI, and RVU effects
-- For each year transition, compute counterfactual scenarios:
--   1. CF-only effect: current CF with prior year RVUs and GPCIs
--   2. GPCI-only effect: current GPCIs with prior year RVUs and CF
--   3. RVU-only effect: current RVUs with prior year GPCIs and CF
-- ============================================================================
DROP VIEW IF EXISTS drinf.v_mpfs_decomp CASCADE;

CREATE VIEW drinf.v_mpfs_decomp AS
WITH base AS (
    SELECT
        year,
        locality_id,
        state,
        locality_name,
        hcpcs,
        modifier,
        hcpcs_mod,
        description,

        -- Current year values
        w_rvu,
        pe_rvu_facility,
        pe_rvu_nonfacility,
        mp_rvu,
        gpci_work,
        gpci_pe,
        gpci_mp,
        conversion_factor,
        allowed_facility,
        allowed_nonfacility,

        -- Prior year values via LAG
        LAG(w_rvu) OVER w AS w_rvu_py,
        LAG(pe_rvu_facility) OVER w AS pe_rvu_facility_py,
        LAG(pe_rvu_nonfacility) OVER w AS pe_rvu_nonfacility_py,
        LAG(mp_rvu) OVER w AS mp_rvu_py,
        LAG(gpci_work) OVER w AS gpci_work_py,
        LAG(gpci_pe) OVER w AS gpci_pe_py,
        LAG(gpci_mp) OVER w AS gpci_mp_py,
        LAG(conversion_factor) OVER w AS cf_py,
        LAG(allowed_facility) OVER w AS allowed_facility_py,
        LAG(allowed_nonfacility) OVER w AS allowed_nonfacility_py

    FROM drinf.v_mpfs_allowed
    WINDOW w AS (PARTITION BY hcpcs_mod, locality_id ORDER BY year)
)
SELECT
    year,
    locality_id,
    state,
    locality_name,
    hcpcs,
    modifier,
    hcpcs_mod,
    description,

    -- Current and prior year allowed
    allowed_facility,
    allowed_facility_py,
    allowed_facility - COALESCE(allowed_facility_py, 0) AS total_change_facility,

    allowed_nonfacility,
    allowed_nonfacility_py,
    allowed_nonfacility - COALESCE(allowed_nonfacility_py, 0) AS total_change_nonfacility,

    -- FACILITY decomposition:
    -- CF-only effect: hold RVUs and GPCIs at PY, apply CY CF
    (COALESCE(w_rvu_py, 0) * COALESCE(gpci_work_py, 1) +
     COALESCE(pe_rvu_facility_py, 0) * COALESCE(gpci_pe_py, 1) +
     COALESCE(mp_rvu_py, 0) * COALESCE(gpci_mp_py, 1)) * COALESCE(conversion_factor, 0)
    - COALESCE(allowed_facility_py, 0) AS cf_effect_facility,

    -- GPCI-only effect: hold RVUs and CF at PY, apply CY GPCIs
    (COALESCE(w_rvu_py, 0) * COALESCE(gpci_work, 1) +
     COALESCE(pe_rvu_facility_py, 0) * COALESCE(gpci_pe, 1) +
     COALESCE(mp_rvu_py, 0) * COALESCE(gpci_mp, 1)) * COALESCE(cf_py, 0)
    - COALESCE(allowed_facility_py, 0) AS gpci_effect_facility,

    -- RVU-only effect: hold GPCIs and CF at PY, apply CY RVUs
    (COALESCE(w_rvu, 0) * COALESCE(gpci_work_py, 1) +
     COALESCE(pe_rvu_facility, 0) * COALESCE(gpci_pe_py, 1) +
     COALESCE(mp_rvu, 0) * COALESCE(gpci_mp_py, 1)) * COALESCE(cf_py, 0)
    - COALESCE(allowed_facility_py, 0) AS rvu_effect_facility,

    -- NON-FACILITY decomposition:
    -- CF-only effect
    (COALESCE(w_rvu_py, 0) * COALESCE(gpci_work_py, 1) +
     COALESCE(pe_rvu_nonfacility_py, 0) * COALESCE(gpci_pe_py, 1) +
     COALESCE(mp_rvu_py, 0) * COALESCE(gpci_mp_py, 1)) * COALESCE(conversion_factor, 0)
    - COALESCE(allowed_nonfacility_py, 0) AS cf_effect_nonfacility,

    -- GPCI-only effect
    (COALESCE(w_rvu_py, 0) * COALESCE(gpci_work, 1) +
     COALESCE(pe_rvu_nonfacility_py, 0) * COALESCE(gpci_pe, 1) +
     COALESCE(mp_rvu_py, 0) * COALESCE(gpci_mp, 1)) * COALESCE(cf_py, 0)
    - COALESCE(allowed_nonfacility_py, 0) AS gpci_effect_nonfacility,

    -- RVU-only effect
    (COALESCE(w_rvu, 0) * COALESCE(gpci_work_py, 1) +
     COALESCE(pe_rvu_nonfacility, 0) * COALESCE(gpci_pe_py, 1) +
     COALESCE(mp_rvu, 0) * COALESCE(gpci_mp_py, 1)) * COALESCE(cf_py, 0)
    - COALESCE(allowed_nonfacility_py, 0) AS rvu_effect_nonfacility,

    -- Raw inputs for auditability
    w_rvu, w_rvu_py,
    pe_rvu_facility, pe_rvu_facility_py,
    pe_rvu_nonfacility, pe_rvu_nonfacility_py,
    mp_rvu, mp_rvu_py,
    gpci_work, gpci_work_py,
    gpci_pe, gpci_pe_py,
    gpci_mp, gpci_mp_py,
    conversion_factor, cf_py

FROM base
WHERE allowed_facility_py IS NOT NULL;  -- Only show rows with prior year data

COMMENT ON VIEW drinf.v_mpfs_decomp IS 'Change decomposition: CF, GPCI, and RVU effects. Note: components are first-order approximations and may not sum exactly to total change due to interaction effects.';
