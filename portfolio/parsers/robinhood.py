"""
robinhood.py — Parse Robinhood exports.

Robinhood is the awkward one. There is no positions export with cost basis the
way Fidelity has. What you can get is:

  Account > Menu > Reports and Statements > Reports > Generate a CSV
  covering the full account history.

That file is transactions only. So for Robinhood, lots are back-derived from the
buy/sell history by derive_lots.py rather than read from the file. That works as
long as the export covers the ENTIRE account history: if it starts partway
through, derived cost basis will be wrong, and load.py flags that case rather
than reporting a confident wrong number.

If you also have a positions CSV (some users grab one from the app or a
third-party sync), this parser will read it, but the transaction file is the one
that matters.
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

PLATFORM = "robinhood"

# Robinhood's "Trans Code" column uses short codes rather than prose.
TRANS_CODES = {
    "buy": "buy", "sell": "sell",
    "bto": "buy", "stc": "sell", "sto": "sell", "btc": "buy",
    "cdiv": "dividend", "div": "dividend", "mdiv": "dividend",
    "int": "interest", "gold": "fee", "dfee": "fee", "aftf": "fee",
    "dtax": "fee", "ftax": "fee",
    "ach": "transfer", "rtp": "transfer", "acat": "transfer", "otcp": "transfer",
    "spl": "split", "spr": "split", "rec": "split", "spo": "split",
    "sofi": "transfer", "mrgc": "fee",
}

TXN_CANDIDATES = {
    "account": ["Account Number", "Account"],
    "trade_date": ["Activity Date", "Trade Date", "Date"],
    "settle_date": ["Settle Date", "Settlement Date", "Process Date"],
    "action": ["Trans Code", "Transaction Code", "Action", "Type"],
    "symbol": ["Instrument", "Symbol", "Ticker"],
    "description": ["Description", "Security Name"],
    "quantity": ["Quantity", "Shares"],
    "price": ["Price", "Average Price"],
    "amount": ["Amount", "Net Amount", "Total"],
}

POSITION_CANDIDATES = {
    "account": ["Account Number", "Account"],
    "symbol": ["Symbol", "Instrument", "Ticker"],
    "description": ["Name", "Description", "Security Name"],
    "quantity": ["Quantity", "Shares"],
    "price": ["Price", "Last Price", "Current Price"],
    "market_value": ["Market Value", "Value", "Equity", "Current Value"],
    "cost_basis": ["Cost Basis", "Total Cost", "Average Cost"],
    "unrealized": ["Total Return", "Unrealized Gain/Loss", "Gain/Loss"],
}

_TXN_MARKERS = {"trans code", "activity date", "instrument"}
_POSITION_MARKERS = {"market value", "average cost", "equity"}


def detect(path: Path) -> str | None:
    cells = {c.lower().strip() for row in sniff_lines(path, 30) for c in row if c.strip()}
    if len(cells & _TXN_MARKERS) >= 2:
        return "transactions"
    if cells & _POSITION_MARKERS and ("symbol" in cells or "instrument" in cells):
        return "positions"
    return None


def _robinhood_action(code, description) -> str:
    """Map a Robinhood Trans Code to a normalized action, falling back to prose."""
    if not is_null(code):
        mapped = TRANS_CODES.get(str(code).strip().lower())
        if mapped:
            return mapped
    return classify_action(description, code)


def parse(path: Path, cfg: dict, kind: str, as_of: date | None = None) -> ParseResult:
    if kind == "transactions":
        return _parse_transactions(path, cfg)
    if kind == "positions":
        return _parse_positions(path, cfg, as_of)
    raise ValueError(f"robinhood parser does not handle kind={kind!r}")


def _parse_transactions(path: Path, cfg: dict) -> ParseResult:
    expected = {c for group in TXN_CANDIDATES.values() for c in group}
    df, _, notes = read_broker_csv(path, expected)

    result = ParseResult(source_file=path.name, platform=PLATFORM, kind="transactions")
    for n in notes:
        result.warn(n)
    result.rows_read = len(df)

    cols = {k: pick_column(df, v) for k, v in TXN_CANDIDATES.items()}
    if cols["trade_date"] is None:
        result.warn(f"No activity date column found. Columns seen: {list(df.columns)}")
        return result

    earliest = None

    for idx, row in df.iterrows():
        trade_date = clean_date(get(row, cols["trade_date"]))
        if trade_date is None:
            result.drop(idx, "no parseable activity date")
            continue

        account_id = resolve_account(get(row, cols["account"]), PLATFORM, cfg, result, idx)
        if account_id is None:
            continue

        description = get(row, cols["description"])
        code = get(row, cols["action"])

        earliest = trade_date if earliest is None else min(earliest, trade_date)

        result.transactions.append({
            "account_id": account_id,
            "trade_date": trade_date,
            "settle_date": clean_date(get(row, cols["settle_date"])),
            "action": _robinhood_action(code, description),
            "symbol": clean_symbol(get(row, cols["symbol"])),
            "quantity": clean_decimal(get(row, cols["quantity"])),
            "price": clean_decimal(get(row, cols["price"])),
            "amount": clean_decimal(get(row, cols["amount"])),
            "description": None if is_null(description) else str(description).strip()[:500],
            "source_file": path.name,
        })

    if earliest:
        result.warn(
            f"Earliest transaction in this file is {earliest}. Robinhood lots are "
            "derived from this history, so it must cover the ENTIRE life of the "
            "account. If you opened it before that date, regenerate the report "
            "with a wider range or derived cost basis will be wrong."
        )

    return result


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
        result.warn(f"Missing symbol or market value. Columns seen: {list(df.columns)}")
        return result

    for idx, row in df.iterrows():
        symbol = clean_symbol(get(row, cols["symbol"]))
        if symbol is None:
            result.drop(idx, "no symbol")
            continue

        account_id = resolve_account(get(row, cols["account"]), PLATFORM, cfg, result, idx)
        if account_id is None:
            continue

        market_value = clean_decimal(get(row, cols["market_value"]))
        if market_value is None:
            result.drop(idx, f"{symbol}: no market value")
            continue

        result.holdings.append({
            "as_of_date": result.as_of_date,
            "account_id": account_id,
            "symbol": symbol,
            "quantity": clean_decimal(get(row, cols["quantity"])),
            "price": clean_decimal(get(row, cols["price"])),
            "market_value": market_value,
            "cost_basis_total": clean_decimal(get(row, cols["cost_basis"])),
            "unrealized_gl": clean_decimal(get(row, cols["unrealized"])),
            "source": "broker",
            "source_file": path.name,
        })

    return result
