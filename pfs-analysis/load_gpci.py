"""
MPFS GPCI Loader
Loads CMS Geographic Practice Cost Index files into PostgreSQL drinf.mpfs_gpci table.
Handles varying file formats across years (2018 vs 2022+ structure).
"""
import pandas as pd
import numpy as np
import psycopg2
import os
import re
from datetime import datetime

from dotenv import load_dotenv
from pathlib import Path
# Search for .env walking up the directory tree
for _env in [Path(__file__).parent / ".env",
             Path(__file__).parent.parent / ".env",
             Path(__file__).parent.parent.parent / ".env"]:
    if _env.exists():
        load_dotenv(_env)
        break

DB_CONFIG = {
    "host":     os.getenv("LOCAL_HOST", "127.0.0.1"),
    "port":     int(os.getenv("LOCAL_PORT", 5432)),
    "database": os.getenv("LOCAL_DATABASE", "postgres"),
    "user":     os.getenv("LOCAL_USER", "postgres"),
    "password": os.getenv("LOCAL_PASSWORD", ""),
}

DATA_DIR = r"C:\dev\healthcare_analytics\pfs_data"

# State abbreviation mapping for 2018 format (state embedded in locality name)
STATE_FROM_MAC = {
    "10102": "AL", "10112": "AL",
    "02102": "AK",
    "03102": "AZ",
    "07102": "AR",
    "01112": "CA", "01182": "CA",
    "04112": "CO",
    "13102": "CT",
    "12102": "DE",
    "12302": "DC",
    "09102": "FL", "09202": "FL",
    "10202": "GA", "10212": "GA",
    "01202": "HI",
    "05102": "ID",
    "06102": "IL",
    "08102": "IN",
    "05202": "IA",
    "05302": "KS",
    "15102": "KY", "15202": "KY",
    "07202": "LA",
    "14112": "ME",
    "12202": "MD",
    "14212": "MA",
    "08202": "MI",
    "06202": "MN",
    "07302": "MS",
    "05402": "MO",
    "03202": "MT",
    "05502": "NE",
    "01302": "NV",
    "14312": "NH",
    "12402": "NJ",
    "04212": "NM",
    "13202": "NY", "13292": "NY",
    "11202": "NC", "11502": "NC",
    "03302": "ND",
    "15302": "OH", "15402": "OH",
    "04312": "OK",
    "02202": "OR",
    "12502": "PA", "12512": "PA",
    "14412": "RI",
    "11302": "SC",
    "03402": "SD",
    "10302": "TN", "10312": "TN",
    "04402": "TX", "04412": "TX",
    "03502": "UT",
    "14512": "VT",
    "11402": "VA", "11502": "VA",
    "02302": "WA",
    "16102": "WV",
    "06302": "WI",
    "03602": "WY",
    "09302": "PR", "09402": "VI",
}


def find_gpci_file(year_dir):
    """Find the GPCI CSV file in a year directory."""
    for f in os.listdir(year_dir):
        if f.upper().startswith('GPCI') and f.endswith('.csv'):
            return os.path.join(year_dir, f)
    return None


def detect_format(filepath):
    """Detect which file format we're dealing with."""
    df_peek = pd.read_csv(filepath, nrows=5, header=None)

    # Check row 3 (index 2) for column headers
    row3 = df_peek.iloc[2].astype(str).str.upper()

    if 'STATE' in ' '.join(row3.values):
        # 2022+ format with State column
        if 'WITHOUT' in ' '.join(row3.values):
            return '2026'  # Has both with/without floor
        return '2022'  # Standard 2022+ format
    else:
        return '2018'  # Old format without State column


def parse_state_from_locality(locality_name):
    """Extract state abbreviation from locality name (2018 format)."""
    if pd.isna(locality_name):
        return None
    # Pattern: "CITY NAME, ST" or just state name
    match = re.search(r',\s*([A-Z]{2})$', str(locality_name))
    if match:
        return match.group(1)
    # Direct state name matching
    state_names = {
        'ALABAMA': 'AL', 'ALASKA': 'AK', 'ARIZONA': 'AZ', 'ARKANSAS': 'AR',
        'CALIFORNIA': 'CA', 'COLORADO': 'CO', 'CONNECTICUT': 'CT', 'DELAWARE': 'DE',
        'FLORIDA': 'FL', 'GEORGIA': 'GA', 'HAWAII': 'HI', 'IDAHO': 'ID',
        'ILLINOIS': 'IL', 'INDIANA': 'IN', 'IOWA': 'IA', 'KANSAS': 'KS',
        'KENTUCKY': 'KY', 'LOUISIANA': 'LA', 'MAINE': 'ME', 'MARYLAND': 'MD',
        'MASSACHUSETTS': 'MA', 'MICHIGAN': 'MI', 'MINNESOTA': 'MN', 'MISSISSIPPI': 'MS',
        'MISSOURI': 'MO', 'MONTANA': 'MT', 'NEBRASKA': 'NE', 'NEVADA': 'NV',
        'NEW HAMPSHIRE': 'NH', 'NEW JERSEY': 'NJ', 'NEW MEXICO': 'NM', 'NEW YORK': 'NY',
        'NORTH CAROLINA': 'NC', 'NORTH DAKOTA': 'ND', 'OHIO': 'OH', 'OKLAHOMA': 'OK',
        'OREGON': 'OR', 'PENNSYLVANIA': 'PA', 'RHODE ISLAND': 'RI', 'SOUTH CAROLINA': 'SC',
        'SOUTH DAKOTA': 'SD', 'TENNESSEE': 'TN', 'TEXAS': 'TX', 'UTAH': 'UT',
        'VERMONT': 'VT', 'VIRGINIA': 'VA', 'WASHINGTON': 'WA', 'WEST VIRGINIA': 'WV',
        'WISCONSIN': 'WI', 'WYOMING': 'WY', 'DISTRICT OF COLUMBIA': 'DC',
        'PUERTO RICO': 'PR', 'VIRGIN ISLANDS': 'VI',
    }
    loc_upper = str(locality_name).upper()
    for name, abbr in state_names.items():
        if loc_upper.startswith(name):
            return abbr
    return None


def parse_gpci_2018(filepath, year):
    """Parse 2018-format GPCI file (no State column)."""
    print(f"  Parsing {os.path.basename(filepath)} (2018 format)...", flush=True)

    # Read with flexible column handling - some years have trailing empty columns
    df = pd.read_csv(filepath, skiprows=2, header=None, skip_blank_lines=True)

    # Drop empty trailing columns
    df = df.dropna(axis=1, how='all')

    # First row after skip might be header - check and skip if needed
    if str(df.iloc[0, 0]).upper().startswith('MEDICARE') or 'MAC' in str(df.iloc[0, 0]).upper():
        df = df.iloc[1:].reset_index(drop=True)

    # Keep only first 6 columns
    df = df.iloc[:, :6]
    df.columns = ['mac', 'locality_number', 'locality_name', 'gpci_work', 'gpci_pe', 'gpci_mp']

    # Extract state from locality name or MAC
    df['state'] = df.apply(
        lambda row: parse_state_from_locality(row['locality_name']) or
                    STATE_FROM_MAC.get(str(row['mac']).zfill(5), None),
        axis=1
    )

    return df


def parse_gpci_2022(filepath, year):
    """Parse 2022+ format GPCI file (with State column)."""
    print(f"  Parsing {os.path.basename(filepath)} (2022 format)...", flush=True)

    df = pd.read_csv(filepath, skiprows=2, header=None, skip_blank_lines=True)

    # Drop empty trailing columns
    df = df.dropna(axis=1, how='all')

    # First row after skip might be header - check and skip if needed
    if str(df.iloc[0, 0]).upper().startswith('MEDICARE') or 'MAC' in str(df.iloc[0, 0]).upper():
        df = df.iloc[1:].reset_index(drop=True)

    # Keep only first 7 columns
    df = df.iloc[:, :7]
    df.columns = ['mac', 'state', 'locality_number', 'locality_name', 'gpci_work', 'gpci_pe', 'gpci_mp']

    return df


def parse_gpci_2026(filepath, year):
    """Parse 2026 format GPCI file (with both floor versions)."""
    print(f"  Parsing {os.path.basename(filepath)} (2026 format)...", flush=True)

    df = pd.read_csv(filepath, skiprows=2, header=None, skip_blank_lines=True)

    # Drop empty trailing columns
    df = df.dropna(axis=1, how='all')

    # First row after skip might be header - check and skip if needed
    if str(df.iloc[0, 0]).upper().startswith('MEDICARE') or 'MAC' in str(df.iloc[0, 0]).upper():
        df = df.iloc[1:].reset_index(drop=True)

    # Keep first 8 columns: MAC, State, Locality Number, Locality Name, PW without floor, PW with floor, PE, MP
    df = df.iloc[:, :8]
    df.columns = ['mac', 'state', 'locality_number', 'locality_name',
                  'gpci_work_nofloor', 'gpci_work', 'gpci_pe', 'gpci_mp']

    # Drop the no-floor column, keep the with-floor version
    df = df.drop(columns=['gpci_work_nofloor'])

    return df


def parse_gpci_file(filepath, year):
    """Parse GPCI file based on detected format."""
    fmt = detect_format(filepath)

    if fmt == '2018':
        df = parse_gpci_2018(filepath, year)
    elif fmt == '2026':
        df = parse_gpci_2026(filepath, year)
    else:
        df = parse_gpci_2022(filepath, year)

    # Common cleanup
    df['mpfs_year'] = year
    df['load_date'] = datetime.now()

    # Normalize locality_number to zero-padded string
    df['locality_number'] = df['locality_number'].astype(str).str.strip().str.zfill(2)

    # Clean state
    df['state'] = df['state'].astype(str).str.strip().str.upper()

    # Clean numeric columns
    for col in ['gpci_work', 'gpci_pe', 'gpci_mp']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    # Remove rows with missing critical fields
    df = df.dropna(subset=['state', 'locality_number'])
    df = df[df['state'].str.len() == 2]  # Valid state abbreviation

    # Clean MAC
    df['mac'] = df['mac'].astype(str).str.strip()

    print(f"  Parsed {len(df)} localities", flush=True)
    return df


def clean_value(x):
    """Convert NaN to None for database insert."""
    if x is None:
        return None
    if isinstance(x, float) and (x != x):
        return None
    return x


def load_to_postgres(df, conn):
    """Load DataFrame into PostgreSQL."""
    cursor = conn.cursor()

    cols = ['mpfs_year', 'load_date', 'mac', 'state', 'locality_number',
            'locality_name', 'gpci_work', 'gpci_pe', 'gpci_mp']

    records = [
        tuple(clean_value(row[c]) for c in cols)
        for _, row in df.iterrows()
    ]

    # Use INSERT ... ON CONFLICT to handle duplicates
    insert_sql = """
        INSERT INTO drinf.mpfs_gpci (mpfs_year, load_date, mac, state, locality_number,
                                      locality_name, gpci_work, gpci_pe, gpci_mp)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (mpfs_year, state, locality_number) DO UPDATE SET
            load_date = EXCLUDED.load_date,
            mac = EXCLUDED.mac,
            locality_name = EXCLUDED.locality_name,
            gpci_work = EXCLUDED.gpci_work,
            gpci_pe = EXCLUDED.gpci_pe,
            gpci_mp = EXCLUDED.gpci_mp
    """

    cursor.executemany(insert_sql, records)
    conn.commit()
    print(f"  Loaded {len(records)} records to drinf.mpfs_gpci", flush=True)


def main():
    print("Connecting to PostgreSQL...", flush=True)
    conn = psycopg2.connect(**DB_CONFIG)

    # Get list of year directories
    years = sorted([d for d in os.listdir(DATA_DIR) if d.isdigit()])
    print(f"Found data for years: {', '.join(years)}", flush=True)

    total_records = 0
    for year_str in years:
        year = int(year_str)
        year_dir = os.path.join(DATA_DIR, year_str)

        print(f"\n[{year}] Processing...", flush=True)

        gpci_file = find_gpci_file(year_dir)
        if gpci_file:
            df = parse_gpci_file(gpci_file, year)
            load_to_postgres(df, conn)
            total_records += len(df)
        else:
            print(f"  No GPCI file found", flush=True)

    # Summary
    cursor = conn.cursor()
    cursor.execute("""
        SELECT mpfs_year, COUNT(*) as localities,
               ROUND(AVG(gpci_work)::numeric, 4) as avg_pw,
               ROUND(AVG(gpci_pe)::numeric, 4) as avg_pe,
               ROUND(AVG(gpci_mp)::numeric, 4) as avg_mp
        FROM drinf.mpfs_gpci
        GROUP BY mpfs_year
        ORDER BY mpfs_year
    """)

    print("\n" + "="*60)
    print("GPCI LOAD SUMMARY")
    print("="*60)
    print(f"{'Year':<6} {'Localities':<12} {'Avg PW':<10} {'Avg PE':<10} {'Avg MP':<10}")
    print("-"*60)
    for row in cursor.fetchall():
        print(f"{row[0]:<6} {row[1]:<12} {row[2]:<10} {row[3]:<10} {row[4]:<10}")

    print(f"\nTotal: {total_records} locality-year records loaded")

    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    main()
