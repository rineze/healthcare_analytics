"""
Hospital Price Transparency Data Loader

Loads hospital MRF (Machine Readable Files) into Supabase for analysis.
Designed to scale across multiple hospitals.
"""
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime, date
from pathlib import Path
import os
from dotenv import load_dotenv

# Load environment
load_dotenv(Path(__file__).parent.parent / ".env")

DB_CONFIG = {
    "host": os.getenv("SUPABASE_HOST"),
    "database": os.getenv("SUPABASE_DATABASE"),
    "user": os.getenv("SUPABASE_USER"),
    "password": os.getenv("SUPABASE_PASSWORD"),
    "port": int(os.getenv("SUPABASE_PORT", 5432)),
}


def create_tables(conn):
    """Create price transparency tables if they don't exist."""
    cursor = conn.cursor()

    cursor.execute("""
        -- Hospital registry
        CREATE TABLE IF NOT EXISTS drinf.pt_hospitals (
            hospital_id SERIAL PRIMARY KEY,
            hospital_name VARCHAR(200) NOT NULL,
            hospital_system VARCHAR(200),
            ein VARCHAR(20),
            state VARCHAR(2),
            city VARCHAR(100),
            address VARCHAR(300),
            file_source VARCHAR(500),
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(hospital_name, state)
        );

        -- Price transparency rates (main table)
        CREATE TABLE IF NOT EXISTS drinf.pt_rates (
            id SERIAL PRIMARY KEY,
            hospital_id INTEGER REFERENCES drinf.pt_hospitals(hospital_id),
            data_year INTEGER NOT NULL,
            load_date DATE NOT NULL,
            cpt_code VARCHAR(10) NOT NULL,
            description VARCHAR(500),
            payer_name VARCHAR(200),
            plan_name VARCHAR(200),
            negotiated_rate NUMERIC(12,2),
            gross_charge NUMERIC(12,2),
            discounted_cash NUMERIC(12,2),
            setting VARCHAR(50),
            billing_class VARCHAR(50),
            UNIQUE(hospital_id, data_year, cpt_code, payer_name, plan_name, setting)
        );

        -- Indexes for performance
        CREATE INDEX IF NOT EXISTS idx_pt_rates_hospital ON drinf.pt_rates(hospital_id);
        CREATE INDEX IF NOT EXISTS idx_pt_rates_cpt ON drinf.pt_rates(cpt_code);
        CREATE INDEX IF NOT EXISTS idx_pt_rates_payer ON drinf.pt_rates(payer_name);
        CREATE INDEX IF NOT EXISTS idx_pt_rates_year ON drinf.pt_rates(data_year);

        COMMENT ON TABLE drinf.pt_hospitals IS 'Hospital registry for price transparency data';
        COMMENT ON TABLE drinf.pt_rates IS 'Hospital price transparency negotiated rates by CPT/payer';
    """)

    conn.commit()
    print("Tables created/verified: drinf.pt_hospitals, drinf.pt_rates")


def register_hospital(conn, hospital_name, state, hospital_system=None, ein=None,
                      city=None, address=None, file_source=None):
    """Register a hospital and return its ID."""
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO drinf.pt_hospitals
            (hospital_name, state, hospital_system, ein, city, address, file_source)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (hospital_name, state)
        DO UPDATE SET
            hospital_system = EXCLUDED.hospital_system,
            file_source = EXCLUDED.file_source
        RETURNING hospital_id
    """, (hospital_name, state, hospital_system, ein, city, address, file_source))

    hospital_id = cursor.fetchone()[0]
    conn.commit()
    print(f"Registered hospital: {hospital_name} (ID: {hospital_id})")
    return hospital_id


def load_echn_file(conn, file_path, data_year=2024):
    """Load ECHN price transparency file.

    ECHN has good CPT-level payer rates in their MRF.
    """
    print(f"\nLoading ECHN file: {file_path}")

    # Register hospital
    hospital_id = register_hospital(
        conn,
        hospital_name="Manchester Memorial Hospital",
        state="CT",
        hospital_system="Prospect Medical Holdings (ECHN)",
        ein="812216981",
        city="Manchester",
        file_source=str(file_path)
    )

    # Load CSV
    df = pd.read_csv(file_path, skiprows=2, low_memory=False)
    print(f"Raw rows: {len(df):,}")

    # Filter to rows with CPT codes (code|2) and negotiated rates
    df = df[df['code|2'].notna()].copy()
    print(f"Rows with CPT codes: {len(df):,}")

    # Clean up columns
    df['cpt_code'] = df['code|2'].astype(str).str.replace('.0', '', regex=False)
    df['negotiated_rate'] = pd.to_numeric(df['standard_charge|negotiated_dollar'], errors='coerce')
    df['gross_charge'] = pd.to_numeric(df['standard_charge|gross'], errors='coerce')
    df['discounted_cash'] = pd.to_numeric(df.get('standard_charge|discounted_cash', pd.Series()), errors='coerce')

    # Filter to rows with payer rates (the useful data)
    df = df[df['payer_name'].notna() & df['negotiated_rate'].notna()].copy()
    print(f"Rows with payer rates: {len(df):,}")

    # Deduplicate by aggregating - take median rate for duplicates
    df['setting'] = df.get('setting', pd.Series()).fillna('unknown')
    df['plan_name'] = df.get('plan_name', pd.Series()).fillna('')

    agg_df = df.groupby(['cpt_code', 'payer_name', 'plan_name', 'setting']).agg({
        'description': 'first',
        'negotiated_rate': 'median',
        'gross_charge': 'first',
        'discounted_cash': 'first',
        'billing_class': 'first'
    }).reset_index()

    print(f"After deduplication: {len(agg_df):,} unique rate records")

    # Prepare for insert
    load_date = date.today()

    records = []
    for _, row in agg_df.iterrows():
        records.append((
            hospital_id,
            data_year,
            load_date,
            row['cpt_code'],
            str(row.get('description', ''))[:500] if pd.notna(row.get('description')) else None,
            str(row['payer_name'])[:200] if pd.notna(row['payer_name']) else None,
            str(row.get('plan_name', ''))[:200] if pd.notna(row.get('plan_name')) else None,
            float(row['negotiated_rate']) if pd.notna(row['negotiated_rate']) else None,
            float(row['gross_charge']) if pd.notna(row['gross_charge']) else None,
            float(row['discounted_cash']) if pd.notna(row['discounted_cash']) else None,
            str(row.get('setting', ''))[:50] if pd.notna(row.get('setting')) else None,
            str(row.get('billing_class', ''))[:50] if pd.notna(row.get('billing_class')) else None,
        ))

    # Clear existing data for this hospital/year
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM drinf.pt_rates
        WHERE hospital_id = %s AND data_year = %s
    """, (hospital_id, data_year))
    deleted = cursor.rowcount
    if deleted > 0:
        print(f"Cleared {deleted:,} existing records for hospital_id={hospital_id}, year={data_year}")

    # Batch insert
    insert_sql = """
        INSERT INTO drinf.pt_rates
            (hospital_id, data_year, load_date, cpt_code, description, payer_name,
             plan_name, negotiated_rate, gross_charge, discounted_cash, setting, billing_class)
        VALUES %s
        ON CONFLICT (hospital_id, data_year, cpt_code, payer_name, plan_name, setting)
        DO UPDATE SET
            negotiated_rate = EXCLUDED.negotiated_rate,
            gross_charge = EXCLUDED.gross_charge,
            load_date = EXCLUDED.load_date
    """

    execute_values(cursor, insert_sql, records, page_size=1000)
    conn.commit()

    print(f"Loaded {len(records):,} rate records for ECHN")
    return len(records)


def load_generic_mrf(conn, file_path, hospital_name, state, data_year,
                     hospital_system=None, cpt_column='code|2', skiprows=2):
    """Generic loader for MRF files with CPT-level payer rates.

    Use this for hospitals with ECHN-style granular disclosure.

    Args:
        conn: Database connection
        file_path: Path to CSV file
        hospital_name: Name of hospital
        state: 2-letter state code
        data_year: Year the data represents
        hospital_system: Parent health system name
        cpt_column: Column containing CPT codes (varies by file)
        skiprows: Header rows to skip
    """
    print(f"\nLoading: {hospital_name}")

    # Register hospital
    hospital_id = register_hospital(
        conn,
        hospital_name=hospital_name,
        state=state,
        hospital_system=hospital_system,
        file_source=str(file_path)
    )

    # Load CSV
    df = pd.read_csv(file_path, skiprows=skiprows, low_memory=False, encoding='utf-8-sig')
    print(f"Raw rows: {len(df):,}")

    # Check if this file has CPT-level payer rates
    if cpt_column not in df.columns:
        print(f"ERROR: Column '{cpt_column}' not found. Available: {list(df.columns)[:10]}")
        return 0

    # Filter to rows with CPT codes
    df = df[df[cpt_column].notna()].copy()
    df['cpt_code'] = df[cpt_column].astype(str).str.replace('.0', '', regex=False)

    # Check for payer rates
    if 'payer_name' not in df.columns:
        print("ERROR: No 'payer_name' column found. This file may not have CPT-level payer rates.")
        return 0

    df = df[df['payer_name'].notna()].copy()

    # Parse rate columns (try common names)
    rate_col = None
    for col in ['standard_charge|negotiated_dollar', 'negotiated_rate', 'negotiated_dollar']:
        if col in df.columns:
            rate_col = col
            break

    if rate_col is None:
        print("ERROR: No negotiated rate column found.")
        return 0

    df['negotiated_rate'] = pd.to_numeric(df[rate_col], errors='coerce')
    df = df[df['negotiated_rate'].notna()].copy()

    print(f"Rows with CPT + payer + rate: {len(df):,}")

    if len(df) == 0:
        print("No usable data found. This hospital may use APC-level disclosure.")
        return 0

    # Parse other columns
    df['gross_charge'] = pd.to_numeric(df.get('standard_charge|gross', pd.Series()), errors='coerce')
    df['discounted_cash'] = pd.to_numeric(df.get('standard_charge|discounted_cash', pd.Series()), errors='coerce')

    # Prepare records
    load_date = date.today()
    records = []
    for _, row in df.iterrows():
        records.append((
            hospital_id,
            data_year,
            load_date,
            row['cpt_code'],
            str(row.get('description', ''))[:500] if pd.notna(row.get('description')) else None,
            str(row['payer_name'])[:200],
            str(row.get('plan_name', ''))[:200] if pd.notna(row.get('plan_name')) else None,
            float(row['negotiated_rate']) if pd.notna(row['negotiated_rate']) else None,
            float(row['gross_charge']) if pd.notna(row['gross_charge']) else None,
            float(row['discounted_cash']) if pd.notna(row['discounted_cash']) else None,
            str(row.get('setting', ''))[:50] if pd.notna(row.get('setting')) else None,
            str(row.get('billing_class', ''))[:50] if pd.notna(row.get('billing_class')) else None,
        ))

    # Clear and insert
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM drinf.pt_rates
        WHERE hospital_id = %s AND data_year = %s
    """, (hospital_id, data_year))

    insert_sql = """
        INSERT INTO drinf.pt_rates
            (hospital_id, data_year, load_date, cpt_code, description, payer_name,
             plan_name, negotiated_rate, gross_charge, discounted_cash, setting, billing_class)
        VALUES %s
        ON CONFLICT (hospital_id, data_year, cpt_code, payer_name, plan_name, setting)
        DO UPDATE SET
            negotiated_rate = EXCLUDED.negotiated_rate,
            gross_charge = EXCLUDED.gross_charge,
            load_date = EXCLUDED.load_date
    """

    execute_values(cursor, insert_sql, records, page_size=1000)
    conn.commit()

    print(f"Loaded {len(records):,} rate records")
    return len(records)


def show_summary(conn):
    """Show summary of loaded data."""
    cursor = conn.cursor()

    print("\n" + "=" * 60)
    print("PRICE TRANSPARENCY DATA SUMMARY")
    print("=" * 60)

    cursor.execute("""
        SELECT
            h.hospital_name,
            h.state,
            r.data_year,
            r.load_date,
            COUNT(DISTINCT r.cpt_code) as cpt_codes,
            COUNT(DISTINCT r.payer_name) as payers,
            COUNT(*) as total_rates
        FROM drinf.pt_rates r
        JOIN drinf.pt_hospitals h ON h.hospital_id = r.hospital_id
        GROUP BY h.hospital_name, h.state, r.data_year, r.load_date
        ORDER BY h.hospital_name, r.data_year
    """)

    for row in cursor.fetchall():
        print(f"\n{row[0]} ({row[1]}) - {row[2]}")
        print(f"  Loaded: {row[3]}")
        print(f"  CPT codes: {row[4]:,}")
        print(f"  Payers: {row[5]}")
        print(f"  Total rates: {row[6]:,}")


def main():
    """Main loader function."""
    print("=" * 60)
    print("Hospital Price Transparency Loader")
    print("=" * 60)

    conn = psycopg2.connect(**DB_CONFIG)

    # Create tables
    create_tables(conn)

    # Load ECHN
    echn_file = Path(__file__).parent / "812216981_prospect-manchester-hospital,-inc_standardcharges.csv"
    if echn_file.exists():
        load_echn_file(conn, echn_file, data_year=2024)
    else:
        print(f"ECHN file not found: {echn_file}")

    # Show summary
    show_summary(conn)

    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    main()
