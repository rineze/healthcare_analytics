"""
empower.py — Parse Empower (formerly Personal Capital) exports.

Empower is an aggregator, not a broker. If Fidelity and Robinhood are linked in
it, its holdings export will contain those same positions a second time. Every
row from here is tagged source='aggregator', and load.py resolves the overlap
with a fixed precedence: a direct broker export always wins.

Where Empower earns its place is coverage. It sees accounts you did not export
directly (an old 401k, an HSA), so it fills gaps rather than duplicating work.

Its weakness is cost basis. Aggregated holdings usually carry market value and
quantity but thin or absent basis, and no lot detail at all. Treat Empower as
the completeness check, not as the tax agent's source of truth.

Export from: Holdings view > the download icon.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from parsers.common import (
    ParseResult,
    classify_action,
    clean_date,
    clean_decimal,
    clean_symbol,
    get,
    infer_as_of_date,
    is_null,
    pick_column,
    read_broker_csv,
    resolve_account,
    sniff_lines,
)

PLATFORM = "empower"

POSITION_CANDIDATES = {
    "account": ["Account Number", "Account", "Account Name"],
    "symbol": ["Ticker", "Symbol", "Holding"],
    "description": ["Description", "Name", "Security Name", "Holding Name"],
    "quantity": ["Quantity", "Shares"],
    "price": ["Price", "Last Price", "Current Price"],
    "market_value": ["Value", "Market Value", "Current Value", "Balance"],
    "cost_basis": ["Cost Basis", "Total Cost", "Basis"],
    "unrealized": ["Gain/Loss", "Unrealized Gain/Loss", "Total Gain"],
}

TXN_CANDIDATES = {
    "account": ["Account Number", "Account", "Account Name"],
    "trade_date": ["Date", "Transaction Date", "Posted Date"],
    "action": ["Category", "Type", "Transaction Type"],
    "symbol": ["Ticker", "Symbol"],
    "description": ["Description", "Merchant", "Payee"],
    "quantity": ["Quantity", "Shares"],
    "price": ["Price"],
    "amount": ["Amount", "Net Amount"],
}

_POSITION_MARKERS = {"ticker", "holding", "quantity", "value"}
_TXN_MARKERS = {"category", "merchant", "payee"}


def detect(path: Path) -> str | None:
    cells = {c.lower().strip() for row in sniff_lines(path, 30) for c in row if c.strip()}
    if cells & _TXN_MARKERS and ("date" in cells or "transaction date" in cells):
        return "transactions"
    if len(cells & _POSITION_MARKERS) >= 3:
        return "positions"
    return None


def parse(path: Path, cfg: dict, kind: str, as_of: date | None = None) -> ParseResult:
    if kind == "positions":
        return _parse_positions(path, cfg, as_of)
    if kind == "transactions":
        return _parse_transactions(path, cfg)
    raise ValueError(f"empower parser does not handle kind={kind!r}")


def _parse_positions(path: Path, cfg: dict, as_of: date | None) -> ParseResult:
    expected = {c for group in POSITION_CANDIDATES.values() for c in group}
    df, _, notes = read_broker_csv(path, expected)

    result = ParseResult(source_file=path.name, platform=PLATFORM, kind="positions")
    for n in notes:
        result.warn(n)
    result.rows_read = len(df)
    result.as_of_date = as_of or infer_as_of_date(path, df)

    if result.as_of_date is None:
        result.warn("Could not determine the snapshot date. Pass --as-of YYYY-MM-DD.")
        return result

    cols = {k: pick_column(df, v) for k, v in POSITION_CANDIDATES.items()}
    if cols["symbol"] is None or cols["market_value"] is None:
        result.warn(f"Missing ticker or value column. Columns seen: {list(df.columns)}")
        return result

    if cols["cost_basis"] is None:
        result.warn(
            "This Empower export has no cost basis column, which is normal for an "
            "aggregated view. These positions will show market value only."
        )

    missing_basis = 0

    for idx, row in df.iterrows():
        symbol = clean_symbol(get(row, cols["symbol"]))
        if symbol is None:
            result.drop(idx, "no ticker (aggregators emit blank tickers for cash lines)")
            continue

        account_id = resolve_account(get(row, cols["account"]), PLATFORM, cfg, result, idx)
        if account_id is None:
            continue

        market_value = clean_decimal(get(row, cols["market_value"]))
        if market_value is None:
            result.drop(idx, f"{symbol}: no market value")
            continue

        cost_basis = clean_decimal(get(row, cols["cost_basis"]))
        if cost_basis is None:
            missing_basis += 1

        result.holdings.append({
            "as_of_date": result.as_of_date,
            "account_id": account_id,
            "symbol": symbol,
            "quantity": clean_decimal(get(row, cols["quantity"])),
            "price": clean_decimal(get(row, cols["price"])),
            "market_value": market_value,
            "cost_basis_total": cost_basis,
            "unrealized_gl": clean_decimal(get(row, cols["unrealized"])),
            "source": "aggregator",
            "source_file": path.name,
        })

    if missing_basis:
        result.warn(
            f"{missing_basis} of {len(result.holdings)} Empower positions have no "
            "cost basis. Where these accounts are also exported directly from the "
            "broker, the broker rows win and this does not matter. Where they are "
            "not, tax analysis on them is not possible."
        )

    return result


def _parse_transactions(path: Path, cfg: dict) -> ParseResult:
    expected = {c for group in TXN_CANDIDATES.values() for c in group}
    df, _, notes = read_broker_csv(path, expected)

    result = ParseResult(source_file=path.name, platform=PLATFORM, kind="transactions")
    for n in notes:
        result.warn(n)
    result.rows_read = len(df)

    cols = {k: pick_column(df, v) for k, v in TXN_CANDIDATES.items()}
    if cols["trade_date"] is None:
        result.warn(f"No date column found. Columns seen: {list(df.columns)}")
        return result

    for idx, row in df.iterrows():
        trade_date = clean_date(get(row, cols["trade_date"]))
        if trade_date is None:
            result.drop(idx, "no parseable date")
            continue

        account_id = resolve_account(get(row, cols["account"]), PLATFORM, cfg, result, idx)
        if account_id is None:
            continue

        description = get(row, cols["description"])

        result.transactions.append({
            "account_id": account_id,
            "trade_date": trade_date,
            "settle_date": None,
            "action": classify_action(description, get(row, cols["action"])),
            "symbol": clean_symbol(get(row, cols["symbol"])),
            "quantity": clean_decimal(get(row, cols["quantity"])),
            "price": clean_decimal(get(row, cols["price"])),
            "amount": clean_decimal(get(row, cols["amount"])),
            "description": None if is_null(description) else str(description).strip()[:500],
            "source_file": path.name,
        })

    return result
