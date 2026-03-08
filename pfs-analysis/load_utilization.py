"""
Medicare Utilization Data Loader
Downloads and loads Medicare Physician by Geography and Service data
"""
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
import requests
from io import StringIO

# Database configuration
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
    "password": os.getenv("LOCAL_PASSWORD", "lolsk8s"),
}

# CSV URLs by year (National + State level data)
UTILIZATION_URLS = {
    2021: "https://data.cms.gov/sites/default/files/2023-05/0a47308e-812e-42cb-8ecf-0c1f457ab849/MUP_PHY_R23_P05_V10_D21_Geo.csv",
    2022: "https://data.cms.gov/sites/default/files/2024-05/3167b4d9-10c0-48f0-a680-1165f4eec064/MUP_PHY_R24_P05_V10_D22_Geo.csv",
    2023: "https://data.cms.gov/sites/default/files/2025-04/3b718a11-a28d-4c38-a13b-2c6eeb649980/MUP_PHY_R25_P05_V20_D23_Geo.csv",
}

# Column mapping (CMS names -> our names)
COLUMN_MAP = {
    "Rndrng_Prvdr_Geo_Lvl": "geo_level",
    "Rndrng_Prvdr_Geo_Cd": "geo_code",
    "Rndrng_Prvdr_Geo_Desc": "geo_desc",
    "HCPCS_Cd": "hcpcs",
    "HCPCS_Desc": "hcpcs_desc",
    "HCPCS_Drug_Ind": "drug_ind",
    "Place_Of_Srvc": "place_of_service",
    "Tot_Rndrng_Prvdrs": "total_providers",
    "Tot_Benes": "total_beneficiaries",
    "Tot_Srvcs": "total_services",
    "Tot_Bene_Day_Srvcs": "total_bene_day_services",
    "Avg_Sbmtd_Chrg": "avg_submitted_charge",
    "Avg_Mdcr_Alowd_Amt": "avg_allowed_amt",
    "Avg_Mdcr_Pymt_Amt": "avg_payment_amt",
    "Avg_Mdcr_Stdzd_Amt": "avg_standardized_amt",
}


def create_table(conn):
    """Create the utilization table if it doesn't exist."""
    cursor = conn.cursor()

    cursor.execute("""
        DROP TABLE IF EXISTS drinf.medicare_utilization CASCADE;

        CREATE TABLE drinf.medicare_utilization (
            year INTEGER NOT NULL,
            geo_level VARCHAR(20),
            geo_code VARCHAR(10),
            geo_desc VARCHAR(100),
            hcpcs VARCHAR(10) NOT NULL,
            hcpcs_desc VARCHAR(500),
            drug_ind VARCHAR(1),
            place_of_service VARCHAR(1),
            total_providers INTEGER,
            total_beneficiaries INTEGER,
            total_services BIGINT,
            total_bene_day_services BIGINT,
            avg_submitted_charge NUMERIC(12,2),
            avg_allowed_amt NUMERIC(12,2),
            avg_payment_amt NUMERIC(12,2),
            avg_standardized_amt NUMERIC(12,2),
            PRIMARY KEY (year, geo_level, geo_code, hcpcs, place_of_service)
        );

        CREATE INDEX idx_util_hcpcs ON drinf.medicare_utilization(hcpcs);
        CREATE INDEX idx_util_year ON drinf.medicare_utilization(year);
        CREATE INDEX idx_util_geo ON drinf.medicare_utilization(geo_level, geo_code);

        COMMENT ON TABLE drinf.medicare_utilization IS
            'Medicare Physician & Other Practitioners by Geography and Service - utilization data from CMS';
    """)

    conn.commit()
    print("Created table drinf.medicare_utilization")


def download_and_load(conn, year, url, national_only=True):
    """Download CSV and load into database.

    Args:
        conn: Database connection
        year: Data year
        url: CSV download URL
        national_only: If True, only load National-level data (much smaller)
    """
    print(f"\nDownloading {year} data...")
    print(f"URL: {url}")

    # Download CSV
    response = requests.get(url, timeout=120)
    response.raise_for_status()

    print(f"Downloaded {len(response.content) / 1024 / 1024:.1f} MB")

    # Parse CSV
    df = pd.read_csv(StringIO(response.text))
    print(f"Parsed {len(df):,} rows")

    # Filter to National only if requested
    if national_only:
        df = df[df["Rndrng_Prvdr_Geo_Lvl"] == "National"]
        print(f"Filtered to National level: {len(df):,} rows")

    # Rename columns
    df = df.rename(columns=COLUMN_MAP)

    # Add year
    df["year"] = year

    # Clean up data
    df["geo_code"] = df["geo_code"].fillna("")

    # Convert numeric columns
    numeric_cols = ["total_providers", "total_beneficiaries", "total_services",
                    "total_bene_day_services", "avg_submitted_charge",
                    "avg_allowed_amt", "avg_payment_amt", "avg_standardized_amt"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Insert into database
    cursor = conn.cursor()

    columns = ["year", "geo_level", "geo_code", "geo_desc", "hcpcs", "hcpcs_desc",
               "drug_ind", "place_of_service", "total_providers", "total_beneficiaries",
               "total_services", "total_bene_day_services", "avg_submitted_charge",
               "avg_allowed_amt", "avg_payment_amt", "avg_standardized_amt"]

    # Prepare values
    values = []
    for _, row in df.iterrows():
        values.append(tuple(
            None if pd.isna(row[col]) else row[col]
            for col in columns
        ))

    # Batch insert
    insert_sql = f"""
        INSERT INTO drinf.medicare_utilization ({", ".join(columns)})
        VALUES %s
        ON CONFLICT (year, geo_level, geo_code, hcpcs, place_of_service)
        DO UPDATE SET
            hcpcs_desc = EXCLUDED.hcpcs_desc,
            total_providers = EXCLUDED.total_providers,
            total_beneficiaries = EXCLUDED.total_beneficiaries,
            total_services = EXCLUDED.total_services,
            total_bene_day_services = EXCLUDED.total_bene_day_services,
            avg_submitted_charge = EXCLUDED.avg_submitted_charge,
            avg_allowed_amt = EXCLUDED.avg_allowed_amt,
            avg_payment_amt = EXCLUDED.avg_payment_amt,
            avg_standardized_amt = EXCLUDED.avg_standardized_amt
    """

    execute_values(cursor, insert_sql, values, page_size=1000)
    conn.commit()

    print(f"Loaded {len(values):,} rows for {year}")


def main():
    """Main loader function."""
    print("=" * 60)
    print("Medicare Utilization Data Loader")
    print("=" * 60)

    # Connect to database
    conn = psycopg2.connect(**DB_CONFIG)

    # Create table
    create_table(conn)

    # Load each year
    for year, url in sorted(UTILIZATION_URLS.items()):
        try:
            download_and_load(conn, year, url, national_only=True)
        except Exception as e:
            print(f"Error loading {year}: {e}")
            continue

    # Summary
    cursor = conn.cursor()
    cursor.execute("""
        SELECT year, COUNT(*) as rows, SUM(total_services) as total_srvcs
        FROM drinf.medicare_utilization
        GROUP BY year
        ORDER BY year
    """)

    print("\n" + "=" * 60)
    print("Summary:")
    print("=" * 60)
    for row in cursor.fetchall():
        print(f"  {row[0]}: {row[1]:,} codes, {row[2]:,} total services")

    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    main()
