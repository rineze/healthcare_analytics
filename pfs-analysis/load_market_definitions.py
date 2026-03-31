"""
load_market_definitions.py

Loads the county_to_market reference table into drinf schema.
This is a generic reference table — not MA-specific.
Join it to any county-level dataset (MA, price transparency, etc.)

Table: drinf.county_to_market
- One row per county
- PK: (state, county)
- Idempotent: safe to re-run (upserts)

Usage:
    python load_market_definitions.py

To add a new state, append rows to MARKET_DATA and re-run.
"""

import os
import psycopg2
from psycopg2.extras import execute_values
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
    "host":   os.getenv("LOCAL_HOST", "127.0.0.1"),
    "port":   int(os.getenv("LOCAL_PORT", 5432)),
    "dbname": os.getenv("LOCAL_DATABASE", "postgres"),
    "user":   os.getenv("LOCAL_USER", "postgres"),
    "password": os.getenv("LOCAL_PASSWORD", ""),
}

# ---------------------------------------------------------------------------
# Market definitions
# Format: (state, county, fips, market_name, market_key, market_state)
# ---------------------------------------------------------------------------

MARKET_DATA = [

    # -------------------------------------------------------------------------
    # TENNESSEE — 95 counties across 9 markets
    # -------------------------------------------------------------------------

    # Nashville (MSA core + contiguous Middle TN)
    ("TN", "Davidson",    "47037", "Nashville",         "TN-NASHVILLE",         "TN"),
    ("TN", "Williamson",  "47187", "Nashville",         "TN-NASHVILLE",         "TN"),
    ("TN", "Rutherford",  "47149", "Nashville",         "TN-NASHVILLE",         "TN"),
    ("TN", "Wilson",      "47189", "Nashville",         "TN-NASHVILLE",         "TN"),
    ("TN", "Sumner",      "47165", "Nashville",         "TN-NASHVILLE",         "TN"),
    ("TN", "Robertson",   "47147", "Nashville",         "TN-NASHVILLE",         "TN"),
    ("TN", "Cheatham",    "47021", "Nashville",         "TN-NASHVILLE",         "TN"),
    ("TN", "Dickson",     "47043", "Nashville",         "TN-NASHVILLE",         "TN"),
    ("TN", "Houston",     "47083", "Nashville",         "TN-NASHVILLE",         "TN"),
    ("TN", "Humphreys",   "47085", "Nashville",         "TN-NASHVILLE",         "TN"),
    ("TN", "Macon",       "47111", "Nashville",         "TN-NASHVILLE",         "TN"),
    ("TN", "Maury",       "47119", "Nashville",         "TN-NASHVILLE",         "TN"),
    ("TN", "Marshall",    "47117", "Nashville",         "TN-NASHVILLE",         "TN"),
    ("TN", "Smith",       "47159", "Nashville",         "TN-NASHVILLE",         "TN"),
    ("TN", "Trousdale",   "47169", "Nashville",         "TN-NASHVILLE",         "TN"),
    ("TN", "Cannon",      "47015", "Nashville",         "TN-NASHVILLE",         "TN"),
    ("TN", "Hickman",     "47081", "Nashville",         "TN-NASHVILLE",         "TN"),
    ("TN", "Lewis",       "47101", "Nashville",         "TN-NASHVILLE",         "TN"),

    # Memphis TN (TN counties only — MSA also includes DeSoto/Marshall MS, Crittenden AR)
    ("TN", "Shelby",      "47157", "Memphis TN",        "TN-MEMPHIS",           "TN"),
    ("TN", "Tipton",      "47167", "Memphis TN",        "TN-MEMPHIS",           "TN"),
    ("TN", "Fayette",     "47047", "Memphis TN",        "TN-MEMPHIS",           "TN"),
    ("TN", "Haywood",     "47075", "Memphis TN",        "TN-MEMPHIS",           "TN"),
    ("TN", "Lauderdale",  "47097", "Memphis TN",        "TN-MEMPHIS",           "TN"),
    ("TN", "Hardeman",    "47069", "Memphis TN",        "TN-MEMPHIS",           "TN"),
    ("TN", "McNairy",     "47109", "Memphis TN",        "TN-MEMPHIS",           "TN"),

    # Tri Cities TN (Kingsport-Bristol + Johnson City MSAs combined)
    ("TN", "Sullivan",    "47163", "Tri Cities TN",     "TN-TRI-CITIES",        "TN"),
    ("TN", "Washington",  "47179", "Tri Cities TN",     "TN-TRI-CITIES",        "TN"),
    ("TN", "Carter",      "47019", "Tri Cities TN",     "TN-TRI-CITIES",        "TN"),
    ("TN", "Unicoi",      "47171", "Tri Cities TN",     "TN-TRI-CITIES",        "TN"),
    ("TN", "Hawkins",     "47073", "Tri Cities TN",     "TN-TRI-CITIES",        "TN"),
    ("TN", "Johnson",     "47091", "Tri Cities TN",     "TN-TRI-CITIES",        "TN"),
    ("TN", "Greene",      "47059", "Tri Cities TN",     "TN-TRI-CITIES",        "TN"),

    # Knoxville (MSA + contiguous East TN)
    ("TN", "Knox",        "47093", "Knoxville",         "TN-KNOXVILLE",         "TN"),
    ("TN", "Anderson",    "47001", "Knoxville",         "TN-KNOXVILLE",         "TN"),
    ("TN", "Blount",      "47009", "Knoxville",         "TN-KNOXVILLE",         "TN"),
    ("TN", "Loudon",      "47105", "Knoxville",         "TN-KNOXVILLE",         "TN"),
    ("TN", "Morgan",      "47129", "Knoxville",         "TN-KNOXVILLE",         "TN"),
    ("TN", "Roane",       "47145", "Knoxville",         "TN-KNOXVILLE",         "TN"),
    ("TN", "Union",       "47173", "Knoxville",         "TN-KNOXVILLE",         "TN"),
    ("TN", "Grainger",    "47057", "Knoxville",         "TN-KNOXVILLE",         "TN"),
    ("TN", "Jefferson",   "47089", "Knoxville",         "TN-KNOXVILLE",         "TN"),
    ("TN", "Sevier",      "47155", "Knoxville",         "TN-KNOXVILLE",         "TN"),
    ("TN", "Monroe",      "47123", "Knoxville",         "TN-KNOXVILLE",         "TN"),
    ("TN", "Cocke",       "47029", "Knoxville",         "TN-KNOXVILLE",         "TN"),
    ("TN", "Hamblen",     "47063", "Knoxville",         "TN-KNOXVILLE",         "TN"),

    # Chattanooga TN (MSA + contiguous SE TN)
    ("TN", "Hamilton",    "47065", "Chattanooga TN",    "TN-CHATTANOOGA",       "TN"),
    ("TN", "Bradley",     "47011", "Chattanooga TN",    "TN-CHATTANOOGA",       "TN"),
    ("TN", "Rhea",        "47143", "Chattanooga TN",    "TN-CHATTANOOGA",       "TN"),
    ("TN", "Sequatchie",  "47153", "Chattanooga TN",    "TN-CHATTANOOGA",       "TN"),
    ("TN", "Marion",      "47115", "Chattanooga TN",    "TN-CHATTANOOGA",       "TN"),
    ("TN", "Bledsoe",     "47007", "Chattanooga TN",    "TN-CHATTANOOGA",       "TN"),
    ("TN", "McMinn",      "47107", "Chattanooga TN",    "TN-CHATTANOOGA",       "TN"),
    ("TN", "Meigs",       "47121", "Chattanooga TN",    "TN-CHATTANOOGA",       "TN"),
    ("TN", "Polk",        "47139", "Chattanooga TN",    "TN-CHATTANOOGA",       "TN"),
    ("TN", "Grundy",      "47061", "Chattanooga TN",    "TN-CHATTANOOGA",       "TN"),

    # Clarksville TN (MSA — also includes Christian/Trigg KY)
    ("TN", "Montgomery",  "47125", "Clarksville TN",    "TN-CLARKSVILLE",       "TN"),
    ("TN", "Stewart",     "47161", "Clarksville TN",    "TN-CLARKSVILLE",       "TN"),

    # Upper Cumberland (Cookeville MSA + rural Plateau)
    ("TN", "Putnam",      "47141", "Upper Cumberland",  "TN-UPPER-CUMBERLAND",  "TN"),
    ("TN", "Overton",     "47133", "Upper Cumberland",  "TN-UPPER-CUMBERLAND",  "TN"),
    ("TN", "White",       "47185", "Upper Cumberland",  "TN-UPPER-CUMBERLAND",  "TN"),
    ("TN", "Cumberland",  "47035", "Upper Cumberland",  "TN-UPPER-CUMBERLAND",  "TN"),
    ("TN", "Warren",      "47177", "Upper Cumberland",  "TN-UPPER-CUMBERLAND",  "TN"),
    ("TN", "Fentress",    "47049", "Upper Cumberland",  "TN-UPPER-CUMBERLAND",  "TN"),
    ("TN", "Van Buren",   "47175", "Upper Cumberland",  "TN-UPPER-CUMBERLAND",  "TN"),
    ("TN", "Clay",        "47027", "Upper Cumberland",  "TN-UPPER-CUMBERLAND",  "TN"),
    ("TN", "Pickett",     "47137", "Upper Cumberland",  "TN-UPPER-CUMBERLAND",  "TN"),
    ("TN", "Scott",       "47151", "Upper Cumberland",  "TN-UPPER-CUMBERLAND",  "TN"),
    ("TN", "Jackson",     "47087", "Upper Cumberland",  "TN-UPPER-CUMBERLAND",  "TN"),
    ("TN", "DeKalb",      "47041", "Upper Cumberland",  "TN-UPPER-CUMBERLAND",  "TN"),

    # Jackson / West TN (Jackson MSA + rural West TN)
    ("TN", "Madison",     "47113", "Jackson / West TN", "TN-JACKSON-WEST",      "TN"),
    ("TN", "Chester",     "47023", "Jackson / West TN", "TN-JACKSON-WEST",      "TN"),
    ("TN", "Henderson",   "47077", "Jackson / West TN", "TN-JACKSON-WEST",      "TN"),
    ("TN", "Crockett",    "47033", "Jackson / West TN", "TN-JACKSON-WEST",      "TN"),
    ("TN", "Gibson",      "47053", "Jackson / West TN", "TN-JACKSON-WEST",      "TN"),
    ("TN", "Carroll",     "47017", "Jackson / West TN", "TN-JACKSON-WEST",      "TN"),
    ("TN", "Weakley",     "47183", "Jackson / West TN", "TN-JACKSON-WEST",      "TN"),
    ("TN", "Obion",       "47131", "Jackson / West TN", "TN-JACKSON-WEST",      "TN"),
    ("TN", "Lake",        "47095", "Jackson / West TN", "TN-JACKSON-WEST",      "TN"),
    ("TN", "Dyer",        "47045", "Jackson / West TN", "TN-JACKSON-WEST",      "TN"),
    ("TN", "Benton",      "47005", "Jackson / West TN", "TN-JACKSON-WEST",      "TN"),
    ("TN", "Decatur",     "47039", "Jackson / West TN", "TN-JACKSON-WEST",      "TN"),
    ("TN", "Perry",       "47135", "Jackson / West TN", "TN-JACKSON-WEST",      "TN"),
    ("TN", "Hardin",      "47071", "Jackson / West TN", "TN-JACKSON-WEST",      "TN"),
    ("TN", "Henry",       "47079", "Jackson / West TN", "TN-JACKSON-WEST",      "TN"),

    # Rural South TN (south of Nashville/Chattanooga corridor, contiguous, borders AL)
    ("TN", "Bedford",     "47003", "Rural South TN",    "TN-RURAL-SOUTH",       "TN"),
    ("TN", "Coffee",      "47031", "Rural South TN",    "TN-RURAL-SOUTH",       "TN"),
    ("TN", "Lincoln",     "47103", "Rural South TN",    "TN-RURAL-SOUTH",       "TN"),
    ("TN", "Giles",       "47055", "Rural South TN",    "TN-RURAL-SOUTH",       "TN"),
    ("TN", "Lawrence",    "47099", "Rural South TN",    "TN-RURAL-SOUTH",       "TN"),
    ("TN", "Wayne",       "47181", "Rural South TN",    "TN-RURAL-SOUTH",       "TN"),
    ("TN", "Moore",       "47127", "Rural South TN",    "TN-RURAL-SOUTH",       "TN"),
    ("TN", "Franklin",    "47051", "Rural South TN",    "TN-RURAL-SOUTH",       "TN"),

    # Rural Northeast TN (NE corner bordering VA/KY, contiguous, adjacent to Knoxville/Tri Cities)
    ("TN", "Campbell",    "47013", "Rural Northeast TN", "TN-RURAL-NORTHEAST",  "TN"),
    ("TN", "Claiborne",   "47025", "Rural Northeast TN", "TN-RURAL-NORTHEAST",  "TN"),
    ("TN", "Hancock",     "47067", "Rural Northeast TN", "TN-RURAL-NORTHEAST",  "TN"),

]


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS drinf.county_to_market (
    state           VARCHAR(2)   NOT NULL,
    county          VARCHAR(100) NOT NULL,
    fips            VARCHAR(5),
    market_name     VARCHAR(100) NOT NULL,
    market_key      VARCHAR(50)  NOT NULL,
    market_state    VARCHAR(2)   NOT NULL,
    notes           TEXT,
    created_date    DATE         NOT NULL DEFAULT CURRENT_DATE,
    PRIMARY KEY (state, county)
);

COMMENT ON TABLE drinf.county_to_market IS
    'Generic county-to-market reference table. Not MA-specific — join to any county-level dataset. '
    'Markets are loosely based on MSAs with contiguous county adjustments. '
    'Cross-state markets (e.g. Memphis, Chattanooga) are split by state suffix in market_name.';

CREATE INDEX IF NOT EXISTS idx_county_to_market_key
    ON drinf.county_to_market (market_key);

CREATE INDEX IF NOT EXISTS idx_county_to_market_state
    ON drinf.county_to_market (market_state);
"""

UPSERT_SQL = """
INSERT INTO drinf.county_to_market
    (state, county, fips, market_name, market_key, market_state)
VALUES %s
ON CONFLICT (state, county)
DO UPDATE SET
    fips         = EXCLUDED.fips,
    market_name  = EXCLUDED.market_name,
    market_key   = EXCLUDED.market_key,
    market_state = EXCLUDED.market_state;
"""


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    print("Creating table if not exists...")
    cur.execute(CREATE_TABLE)

    print(f"Upserting {len(MARKET_DATA)} county-market rows...")
    execute_values(cur, UPSERT_SQL, MARKET_DATA, page_size=500)

    conn.commit()

    # Validation
    cur.execute("""
        SELECT market_name, COUNT(*) as counties
        FROM drinf.county_to_market
        WHERE market_state = 'TN'
        GROUP BY market_name
        ORDER BY market_name
    """)
    rows = cur.fetchall()
    print("\nTN Markets loaded:")
    total = 0
    for market, cnt in rows:
        print(f"  {market:<30} {cnt} counties")
        total += cnt
    print(f"  {'TOTAL':<30} {total} counties")

    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
