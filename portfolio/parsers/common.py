"""
common.py — Shared parsing machinery for broker CSV exports.

Broker exports are not clean CSVs. They have preamble rows above the real header,
free-text disclaimer paragraphs at the bottom, dollar signs and thousands
separators inside numeric columns, parentheses for negatives, "--" and "n/a" for
nulls, full account numbers, and cash lines that are not really securities.

Everything in here exists to handle one of those, and to make sure that when a
row cannot be parsed we record WHY instead of silently dropping it. A parser that
quietly loses rows makes every downstream agent lie with total confidence, and
there would be no way to tell from the report.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

# Values brokers use to mean "no value". Checked case-insensitively.
NULL_TOKENS = {
    "", "-", "--", "---", "n/a", "na", "none", "null", "not applicable",
    "unavailable", "not available", "pending", "--%", "$--",
}

# Rows whose symbol/description matches these are not positions we track as
# securities. Kept as cash rather than dropped, so account totals still tie.
CASH_PATTERNS = re.compile(
    r"^(cash|cash\s*&?\s*(sweep|equivalents?|balance)|core\*+|pending\s+activity|"
    r"settled\s+cash|buying\s+power|brokerage\s+cash)",
    re.IGNORECASE,
)

MONEY_MARKET_PATTERNS = re.compile(
    r"(money\s*market|govt?\s+cash\s+reserves|treasury\s+(only|obligations)\s+money)",
    re.IGNORECASE,
)

DATE_FORMATS = [
    "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%b-%d-%Y", "%d-%b-%Y",
    "%b %d, %Y", "%Y/%m/%d", "%m-%d-%Y",
]

# Transaction description keywords -> normalized action.
# Order matters: first match wins, so more specific patterns go first.
ACTION_PATTERNS = [
    (re.compile(r"reinvest", re.I), "buy"),
    (re.compile(r"\b(dividend|div\b|qualified div)", re.I), "dividend"),
    (re.compile(r"\b(interest|int earned)", re.I), "interest"),
    (re.compile(r"\b(you bought|bought|buy|purchase)\b", re.I), "buy"),
    (re.compile(r"\b(you sold|sold|sell|redemption|redeemed)\b", re.I), "sell"),
    (re.compile(r"\b(split|stock split)\b", re.I), "split"),
    (re.compile(r"\b(fee|commission|adr fee|foreign tax|tax withheld)\b", re.I), "fee"),
    (re.compile(r"\b(transfer|journal|deposit|withdrawal|contribution|ach)\b", re.I), "transfer"),
]


@dataclass
class ParseResult:
    """Normalized output of one broker export file.

    holdings / lots / transactions are lists of dicts matching the columns of the
    corresponding portfolio.* tables.

    stated_totals carries whatever account-level total the file itself claims, so
    load.py can reconcile against it. It is keyed by (as_of_date, account_id),
    NOT by account alone: dropping two months of exports in the inbox at once is
    a normal thing to do, and keying by account would let August's total be
    compared against September's positions.
    """

    source_file: str
    platform: str
    kind: str  # positions | lots | transactions | unknown
    as_of_date: date | None = None
    holdings: list[dict] = field(default_factory=list)
    lots: list[dict] = field(default_factory=list)
    transactions: list[dict] = field(default_factory=list)
    stated_totals: dict[tuple[date, str], Decimal] = field(default_factory=dict)
    rows_read: int = 0
    drops: list[tuple[int, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unknown_accounts: set[str] = field(default_factory=set)

    def drop(self, row_idx: int, reason: str) -> None:
        self.drops.append((row_idx, reason))

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    @property
    def rows_loaded(self) -> int:
        return len(self.holdings) + len(self.lots) + len(self.transactions)


# ---------------------------------------------------------------------------
# Value cleaning
# ---------------------------------------------------------------------------

def is_null(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    return str(value).strip().lower() in NULL_TOKENS


def clean_decimal(value) -> Decimal | None:
    """Parse a broker-formatted number.

    Handles $1,234.56, (45.00) for negatives, trailing %, +/- prefixes, and the
    whole family of null tokens. Returns None rather than 0 when there is no
    value, because "no cost basis on file" and "cost basis of zero" mean very
    different things to the tax agent.
    """
    if is_null(value):
        return None

    s = str(value).strip()
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]

    s = s.replace("$", "").replace(",", "").replace("%", "").replace("+", "").strip()
    if s.startswith("-"):
        negative = True
        s = s[1:].strip()

    if not s or s in NULL_TOKENS:
        return None

    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return None

    return -d if negative else d


def clean_percent(value) -> Decimal | None:
    """Parse a percentage into a decimal fraction (12.5% -> 0.125)."""
    d = clean_decimal(value)
    return None if d is None else d / Decimal(100)


def clean_date(value) -> date | None:
    """Parse a date in any of the formats brokers actually emit."""
    if is_null(value):
        return None
    s = str(value).strip()

    # Fidelity marks fully-covered long-term lots with text instead of a date.
    if re.search(r"unknown|various|multiple", s, re.I):
        return None

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def clean_symbol(value) -> str | None:
    """Normalize a ticker.

    Brokers decorate symbols: Fidelity appends ** to money markets, some exports
    wrap them in quotes, mutual fund classes carry suffixes. Strip the decoration
    but leave the actual ticker alone.
    """
    if is_null(value):
        return None
    s = str(value).strip().upper()
    s = s.strip('"\'')
    s = s.rstrip("*").strip()
    s = re.sub(r"\s+", "", s)
    return s or None


def mask_account(value) -> str | None:
    """Reduce an account number to its last four digits.

    Called on read, before anything touches disk or the database. Full account
    numbers are never persisted anywhere in this project.
    """
    if is_null(value):
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits[-4:] if len(digits) >= 4 else None


def classify_security_type(symbol: str | None, description: str | None) -> str:
    """Best-effort security type from the export alone.

    enrich.py refines this from yfinance quoteType. This exists so that cash and
    money market lines are handled correctly even if enrichment never runs.
    """
    desc = description or ""
    if symbol is None or CASH_PATTERNS.match(desc) or CASH_PATTERNS.match(symbol or ""):
        return "cash"
    if MONEY_MARKET_PATTERNS.search(desc):
        return "money_market"
    if len(symbol) == 5 and symbol.endswith("X"):
        return "mutual_fund"
    if re.match(r"^[A-Z]{1,5}\d{6}[CP]\d+$", symbol):
        return "option"
    return "equity"


def classify_action(description: str | None, explicit: str | None = None) -> str:
    """Normalize a transaction into buy/sell/dividend/interest/fee/transfer/split."""
    for text in (explicit, description):
        if is_null(text):
            continue
        for pattern, action in ACTION_PATTERNS:
            if pattern.search(str(text)):
                return action
    return "other"


def holding_term(acquired: date | None, as_of: date | None) -> str:
    """Short vs long term holding period.

    The IRS holding period starts the day AFTER acquisition, and long-term means
    held more than one year. So a position acquired on 2024-01-01 becomes
    long-term on 2025-01-02, which is 367 days by naive subtraction in a leap
    year. Using "more than 365 days elapsed" is the standard practical test and
    is what this uses.
    """
    if acquired is None or as_of is None:
        return "unknown"
    return "long" if (as_of - acquired).days > 365 else "short"


# ---------------------------------------------------------------------------
# File reading
# ---------------------------------------------------------------------------

def sniff_lines(path: Path, limit: int = 60) -> list[list[str]]:
    """Read the first N rows of a CSV as raw field lists, tolerating junk."""
    rows = []
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        for i, row in enumerate(csv.reader(f)):
            if i >= limit:
                break
            rows.append([c.strip() for c in row])
    return rows


def find_header_row(path: Path, expected_cols: set[str], max_scan: int = 60) -> tuple[int, int]:
    """Locate the real header row in a file with a preamble.

    Fidelity and Empower both put title/date lines above the header. Scores each
    of the first max_scan rows by how many expected column names it contains and
    returns the best match. Returns (row_index, columns_matched).
    """
    wanted = {c.lower() for c in expected_cols}
    best_idx, best_score = 0, 0

    for idx, row in enumerate(sniff_lines(path, max_scan)):
        cells = {c.lower().strip() for c in row if c.strip()}
        if len(cells) < 2:
            continue
        score = len(cells & wanted)
        if score > best_score:
            best_idx, best_score = idx, score

    return best_idx, best_score


def read_broker_csv(path: Path, expected_cols: set[str]) -> tuple[pd.DataFrame, int, list[str]]:
    """Read a broker CSV into an all-string DataFrame.

    Returns (df, header_row_index, notes). Everything comes back as a string so
    that cleaning is explicit and pandas never guesses a dtype from a column that
    contains both "$1,234.56" and "--".
    """
    notes: list[str] = []
    header_idx, matched = find_header_row(path, expected_cols)

    if matched == 0:
        notes.append(
            f"No expected column names found in the first rows of {path.name}. "
            "The export format may have changed, or this may not be the file type "
            "it was routed as."
        )

    if header_idx > 0:
        notes.append(f"Skipped {header_idx} preamble row(s) above the header.")

    df = pd.read_csv(
        path,
        skiprows=header_idx,
        dtype=str,
        keep_default_na=False,
        engine="python",
        on_bad_lines="skip",
        encoding="utf-8-sig",
        encoding_errors="replace",
    )

    df.columns = [str(c).strip() for c in df.columns]
    df = df.loc[:, [c for c in df.columns if c and not c.startswith("Unnamed")]]

    # Rows with fewer fields than the header get padded with NA, not skipped.
    # Footer disclaimer lines are exactly that shape, so normalize the padding to
    # empty strings before looking for them or str(NA) becomes the literal "nan"
    # and the footer sails straight through as a data row.
    df = df.fillna("")

    before = len(df)

    # Fully blank rows: separators inside the export.
    blank = df.apply(lambda r: all(str(v).strip() == "" for v in r), axis=1)

    # Footer disclaimers: free text lands in the first column with everything
    # after it empty. A real position row always populates more than one field.
    if len(df.columns) > 1:
        first, rest = df.columns[0], df.columns[1:]
        footer = (
            df[first].astype(str).str.strip().ne("")
            & df[rest].apply(lambda r: all(str(v).strip() == "" for v in r), axis=1)
        )
    else:
        footer = pd.Series(False, index=df.index)

    df = df[~(blank | footer)].reset_index(drop=True)

    dropped = before - len(df)
    if dropped:
        notes.append(f"Removed {dropped} blank or footer-disclaimer row(s).")

    return df, header_idx, notes


def pick_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Return the first candidate column present in df, matched loosely.

    Broker column names drift ("Current Value" vs "Current value" vs "Value"),
    so match case-insensitively and ignore punctuation and whitespace.
    """
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(s).lower())

    lookup = {norm(c): c for c in df.columns}
    for cand in candidates:
        hit = lookup.get(norm(cand))
        if hit:
            return hit
    return None


def get(row, col: str | None):
    """Safe cell access for an optional column."""
    if col is None:
        return None
    value = row.get(col)
    return None if is_null(value) else value


def resolve_account(
    raw_account,
    platform: str,
    cfg: dict,
    result: ParseResult,
    row_idx: int,
) -> str | None:
    """Map a raw account value from an export to a configured account_id.

    Matches on the last four digits declared in config.yaml. If the file has no
    account column and only one account is configured for this platform, that one
    is assumed, since a single-account export is unambiguous. Anything else is
    recorded in unknown_accounts so load.py can tell Dan exactly which account to
    add to config.yaml rather than silently dropping his money.
    """
    candidates = [a for a in cfg["accounts"] if a["platform"] == platform]

    mask = mask_account(raw_account)
    if mask:
        for acct in candidates:
            if acct.get("mask4") == mask:
                return acct["account_id"]
        result.unknown_accounts.add(f"...{mask} ({platform})")
        result.drop(row_idx, f"account ...{mask} is not in config.yaml")
        return None

    if len(candidates) == 1:
        return candidates[0]["account_id"]

    if not candidates:
        result.unknown_accounts.add(f"(any {platform} account)")
        result.drop(row_idx, f"no {platform} account configured in config.yaml")
    else:
        result.drop(
            row_idx,
            f"no account number in file and {len(candidates)} {platform} accounts "
            "configured, cannot tell which one this row belongs to",
        )
    return None


def infer_as_of_date(path: Path, df: pd.DataFrame) -> date | None:
    """Find the snapshot date for a positions export.

    Tries, in order: a date column in the data, a date in the preamble text, a
    date in the filename. Falls back to None so load.py can require --as-of
    rather than silently stamping today onto last month's data.
    """
    col = pick_column(df, ["As of Date", "Date", "As Of", "Statement Date"])
    if col is not None:
        for value in df[col]:
            d = clean_date(value)
            if d:
                return d

    for row in sniff_lines(path, 15):
        for cell in row:
            m = re.search(r"\b(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})\b", cell)
            if m:
                d = clean_date(m.group(1))
                if d:
                    return d

    m = re.search(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})", path.stem)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    return None
