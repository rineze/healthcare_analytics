"""
MPFS RVU Loader
Loads CMS Physician Fee Schedule RVU files into PostgreSQL drinf.mpfs_rvu table.
Stacks multiple years with mpfs_year and load_date fields.
"""
import pandas as pd
import numpy as np
import psycopg2
from psycopg2.extras import execute_values
import os
from datetime import datetime

# Database connection
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")

DB_CONFIG = {
    "host":     os.getenv("LOCAL_HOST", "127.0.0.1"),
    "port":     int(os.getenv("LOCAL_PORT", 5432)),
    "database": os.getenv("LOCAL_DATABASE", "postgres"),
    "user":     os.getenv("LOCAL_USER", "postgres"),
    "password": os.getenv("LOCAL_PASSWORD", "lolsk8s"),
}

# Data directory
DATA_DIR = r"C:\Users\danie\OneDrive\Desktop\Informatics Tools & Files\pfs_analysis\pfs_data"

# Column mapping from CSV headers to database columns
COLUMN_MAP = {
    'HCPCS': 'hcpcs',
    'MOD': 'modifier',
    'DESCRIPTION': 'description',
    'CODE': 'status_code',
    'PAYMENT': 'not_used_for_medicare',
    'RVU': 'work_rvu',
    'PE RVU': 'non_fac_pe_rvu',
    'INDICATOR': 'non_fac_na_indicator',
    'PE RVU.1': 'facility_pe_rvu',
    'INDICATOR.1': 'facility_na_indicator',
    'RVU.1': 'mp_rvu',
    'TOTAL': 'non_facility_total',
    'TOTAL.1': 'facility_total',
    'IND': 'pctc_ind',
    'DAYS': 'global_days',
    'OP': 'pre_op',
    'OP.1': 'intra_op',
    'OP.2': 'post_op',
    'PROC': 'mult_proc',
    'SURG': 'bilat_surg',
    'SURG.1': 'asst_surg',
    'SURG.2': 'co_surg',
    'SURG.3': 'team_surg',
    'BASE': 'endo_base',
    'FACTOR': 'conversion_factor',
    'PROCEDURES': 'diag_procedures',
    'FLAG': 'calc_flag',
    'INDICATOR.2': 'family_indicator',
    'AMOUNT': 'non_fac_payment',
    'AMOUNT.1': 'facility_payment',
    'AMOUNT.2': 'mp_opps_payment'
}

# Database columns in order
DB_COLUMNS = [
    'mpfs_year', 'load_date', 'hcpcs', 'modifier', 'description', 'status_code',
    'not_used_for_medicare', 'work_rvu', 'non_fac_pe_rvu', 'non_fac_na_indicator',
    'facility_pe_rvu', 'facility_na_indicator', 'mp_rvu', 'non_facility_total',
    'facility_total', 'pctc_ind', 'global_days', 'pre_op', 'intra_op', 'post_op',
    'mult_proc', 'bilat_surg', 'asst_surg', 'co_surg', 'team_surg', 'endo_base',
    'conversion_factor', 'diag_procedures', 'calc_flag', 'family_indicator',
    'non_fac_payment', 'facility_payment', 'mp_opps_payment'
]


def find_rvu_file(year_dir):
    """Find the main PPRRVU CSV file in a year directory.

    For 2026+, CMS provides QPP and nonQPP versions - we use nonQPP (standard rate).
    """
    candidates = []
    for f in os.listdir(year_dir):
        if f.endswith('.csv') and 'PPRRVU' in f.upper():
            # Skip QPP version if nonQPP exists (2026+ has both)
            if 'QPP' in f.upper() and 'NONQPP' not in f.upper():
                continue
            candidates.append(os.path.join(year_dir, f))

    # Prefer nonQPP if available, otherwise take first match
    for c in candidates:
        if 'NONQPP' in c.upper():
            return c
    return candidates[0] if candidates else None


def parse_rvu_file(filepath, year):
    """Parse a PPRRVU CSV file into a DataFrame."""
    print(f"  Parsing {os.path.basename(filepath)}...", flush=True)

    # Read CSV, skipping the 9 header rows
    df = pd.read_csv(filepath, skiprows=9, low_memory=False)

    # Rename columns based on mapping
    df = df.rename(columns=COLUMN_MAP)

    # Add year and load_date
    load_date = datetime.now()
    df['mpfs_year'] = year
    df['load_date'] = load_date

    # Select only the columns we need (in order)
    available_cols = [c for c in DB_COLUMNS if c in df.columns]
    df = df[available_cols].copy()

    # Clean up data - remove empty/invalid hcpcs codes
    df = df.dropna(subset=['hcpcs'])
    df['hcpcs'] = df['hcpcs'].astype(str).str.strip()
    df = df[df['hcpcs'] != '']
    df = df[df['hcpcs'].str.match(r'^[A-Za-z0-9]+$', na=False)]  # Only valid alphanumeric codes

    # Convert numeric columns
    numeric_cols = [
        'work_rvu', 'non_fac_pe_rvu', 'facility_pe_rvu', 'mp_rvu',
        'non_facility_total', 'facility_total', 'pre_op', 'intra_op', 'post_op',
        'conversion_factor', 'non_fac_payment', 'facility_payment', 'mp_opps_payment'
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    print(f"  Parsed {len(df):,} records", flush=True)
    return df


def load_to_postgres(df, conn):
    """Load DataFrame into PostgreSQL using batch insert."""
    cursor = conn.cursor()

    # Get columns that exist in the dataframe
    cols = [c for c in DB_COLUMNS if c in df.columns]

    # Prepare data - convert NaN to None for proper NULL handling
    df_clean = df[cols].copy()
    df_clean = df_clean.replace({pd.NA: None, 'nan': None, 'NaN': None, np.nan: None})
    df_clean = df_clean.where(pd.notnull(df_clean), None)

    # Convert to records, explicitly handling numpy NaN -> None
    def clean_value(x):
        if x is None:
            return None
        if isinstance(x, float) and (x != x):  # NaN check (NaN != NaN is True)
            return None
        return x

    records = [tuple(clean_value(v) for v in row) for row in df_clean.to_numpy()]

    # Build insert statement
    cols_str = ', '.join(cols)
    template = '(' + ', '.join(['%s'] * len(cols)) + ')'

    # Use execute_values for faster bulk insert
    insert_sql = f"INSERT INTO drinf.mpfs_rvu ({cols_str}) VALUES %s"
    execute_values(cursor, insert_sql, records, template=template, page_size=1000)

    conn.commit()
    print(f"  Loaded {len(records):,} records to drinf.mpfs_rvu", flush=True)


def main():
    # Connect to PostgreSQL
    print("Connecting to PostgreSQL...", flush=True)
    conn = psycopg2.connect(**DB_CONFIG)

    # Get list of year directories
    years = sorted([d for d in os.listdir(DATA_DIR) if d.isdigit()])
    print(f"Found data for years: {', '.join(years)}", flush=True)

    # Process each year
    total_records = 0
    for year_str in years:
        year = int(year_str)
        year_dir = os.path.join(DATA_DIR, year_str)

        print(f"\n[{year}] Processing...", flush=True)

        rvu_file = find_rvu_file(year_dir)
        if rvu_file:
            df = parse_rvu_file(rvu_file, year)
            load_to_postgres(df, conn)
            total_records += len(df)
        else:
            print(f"  No PPRRVU file found", flush=True)

    # Summary
    cursor = conn.cursor()
    cursor.execute("""
        SELECT mpfs_year, COUNT(*) as records
        FROM drinf.mpfs_rvu
        GROUP BY mpfs_year
        ORDER BY mpfs_year
    """)

    print("\n" + "="*50)
    print("LOAD SUMMARY")
    print("="*50)
    for row in cursor.fetchall():
        print(f"  {row[0]}: {row[1]:,} records")
    print(f"\nTotal: {total_records:,} records loaded")

    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    main()
