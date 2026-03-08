-- ============================================================================
-- MPFS Analytics Views - Validation Queries
-- ============================================================================

-- ============================================================================
-- 1. ROW COUNTS BY VIEW
-- ============================================================================
SELECT 'v_rvu_clean' AS view_name, COUNT(*) AS row_count FROM drinf.v_rvu_clean
UNION ALL
SELECT 'v_gpci_clean', COUNT(*) FROM drinf.v_gpci_clean
UNION ALL
SELECT 'v_cf_clean', COUNT(*) FROM drinf.v_cf_clean
UNION ALL
SELECT 'v_gpci_yoy', COUNT(*) FROM drinf.v_gpci_yoy
ORDER BY view_name;

-- ============================================================================
-- 2. CHECK FOR DUPLICATES IN KEY VIEWS
-- ============================================================================
-- v_rvu_clean: should have unique year + hcpcs_mod
SELECT 'v_rvu_clean duplicates' AS check_name, COUNT(*) AS duplicate_count
FROM (
    SELECT year, hcpcs_mod, COUNT(*)
    FROM drinf.v_rvu_clean
    GROUP BY year, hcpcs_mod
    HAVING COUNT(*) > 1
) dups;

-- v_gpci_clean: should have unique year + locality_id
SELECT 'v_gpci_clean duplicates' AS check_name, COUNT(*) AS duplicate_count
FROM (
    SELECT year, locality_id, COUNT(*)
    FROM drinf.v_gpci_clean
    GROUP BY year, locality_id
    HAVING COUNT(*) > 1
) dups;

-- v_cf_clean: should have unique year
SELECT 'v_cf_clean duplicates' AS check_name, COUNT(*) AS duplicate_count
FROM (
    SELECT year, COUNT(*)
    FROM drinf.v_cf_clean
    GROUP BY year
    HAVING COUNT(*) > 1
) dups;

-- ============================================================================
-- 3. CONVERSION FACTOR BY YEAR (sanity check)
-- ============================================================================
SELECT year, conversion_factor
FROM drinf.v_cf_clean
ORDER BY year;

-- ============================================================================
-- 4. HAND-CHECK: Verify allowed calculation for a known code
-- Example: 70553 (MRI brain with/without contrast) in California locality 18
-- ============================================================================
SELECT
    year,
    hcpcs_mod,
    locality_id,
    w_rvu,
    pe_rvu_facility,
    mp_rvu,
    gpci_work,
    gpci_pe,
    gpci_mp,
    conversion_factor,
    ROUND(total_adj_facility::numeric, 4) AS total_adj_fac,
    ROUND(allowed_facility::numeric, 2) AS allowed_fac,
    -- Manual calculation for validation
    ROUND((COALESCE(w_rvu, 0) * COALESCE(gpci_work, 1) +
           COALESCE(pe_rvu_facility, 0) * COALESCE(gpci_pe, 1) +
           COALESCE(mp_rvu, 0) * COALESCE(gpci_mp, 1)) * conversion_factor::numeric, 2) AS manual_calc
FROM drinf.v_mpfs_allowed
WHERE hcpcs = '70553'
  AND modifier IS NULL
  AND locality_id = 'CA-18'
ORDER BY year;

-- ============================================================================
-- 5. YOY VIEW VALIDATION
-- Check that YoY calculations are correct
-- ============================================================================
SELECT
    year,
    hcpcs_mod,
    locality_id,
    ROUND(allowed_facility::numeric, 2) AS allowed_fac,
    ROUND(allowed_facility_py::numeric, 2) AS allowed_fac_py,
    ROUND(allowed_facility_change::numeric, 2) AS change,
    ROUND(allowed_facility_pct_change::numeric, 2) AS pct_change
FROM drinf.v_mpfs_allowed_yoy
WHERE hcpcs = '70553'
  AND modifier IS NULL
  AND locality_id = 'CA-18'
ORDER BY year;

-- ============================================================================
-- 6. DECOMPOSITION VALIDATION
-- Check that components are calculated (note: won't sum exactly due to interaction effects)
-- ============================================================================
SELECT
    year,
    hcpcs_mod,
    locality_id,
    ROUND(allowed_facility::numeric, 2) AS allowed_fac,
    ROUND(allowed_facility_py::numeric, 2) AS allowed_fac_py,
    ROUND(total_change_facility::numeric, 2) AS total_change,
    ROUND(cf_effect_facility::numeric, 2) AS cf_effect,
    ROUND(gpci_effect_facility::numeric, 2) AS gpci_effect,
    ROUND(rvu_effect_facility::numeric, 2) AS rvu_effect,
    -- Sum of components (for comparison to total)
    ROUND((cf_effect_facility + gpci_effect_facility + rvu_effect_facility)::numeric, 2) AS sum_of_components
FROM drinf.v_mpfs_decomp
WHERE hcpcs = '70553'
  AND modifier IS NULL
  AND locality_id = 'CA-18'
ORDER BY year;

-- ============================================================================
-- 7. GPCI YOY VALIDATION
-- ============================================================================
SELECT
    year,
    locality_id,
    locality_name,
    ROUND(gpci_work::numeric, 4) AS gpci_work,
    ROUND(gpci_work_py::numeric, 4) AS gpci_work_py,
    ROUND(gpci_work_change::numeric, 4) AS gpci_work_chg,
    ROUND(gpci_work_pct_change::numeric, 2) AS gpci_work_pct
FROM drinf.v_gpci_yoy
WHERE locality_id = 'CA-18'
ORDER BY year;

-- ============================================================================
-- 8. SAMPLE v_mpfs_allowed ROW COUNT (will be large due to cross join)
-- ============================================================================
SELECT
    year,
    COUNT(*) AS rows,
    COUNT(DISTINCT hcpcs_mod) AS unique_codes,
    COUNT(DISTINCT locality_id) AS localities
FROM drinf.v_mpfs_allowed
GROUP BY year
ORDER BY year;
