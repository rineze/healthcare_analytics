"""
fidelity.py — Parse Fidelity exports.

Fidelity gives you three files worth having, and you want all three:

  1. Positions       Accounts > Portfolio > Positions > download.
                     Current holdings with total cost basis per position.
  2. Cost basis      Accounts > Tax Info (or Cost Basis) > download.
                     LOT LEVEL detail with acquisition dates. This is the file
                     that makes the tax agent possible. Do not skip it.
  3. History         Accounts > Activity & Orders > download.
                     Transactions, needed for realized gain/loss and the
                     plus/minus 30 day wash-sale window.

Column mappings live in the CANDIDATES dicts so a format change on Fidelity's
end is a one-line edit rather than a rewrite.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from parsers.common import (
    ParseResult,
    classify_action,
    classify_security_type,
    clean_date,
    clean_decimal,
    clean_symbol,
    get,
    holding_term,
    infer_as_of_date,
    is_null,
    pick_column,
    read_broker_csv,
    resolve_account,
)

PLATFORM = "fidelity"

POSITION_CANDIDATES = {
    "account": ["Account Number", "Account", "Account Name"],
    "account_name": ["Account Name"],
    "symbol": ["Symbol", "Ticker"],
    "description": ["Description", "Security Description"],
    "quantity": ["Quantity", "Shares"],
    "price": ["Last Price", "Price", "Current Price"],
    "market_value": ["Current Value", "Value", "Market Value"],
    "cost_basis": ["Cost Basis Total", "Cost Basis", "Total Cost Basis"],
    "unrealized": ["Total Gain/Loss Dollar", "Total Gain/Loss $", "Unrealized Gain/Loss"],
}

LOT_CANDIDATES = {
    "account": ["Account Number", "Account", "Account Name"],
    "symbol": ["Symbol", "Ticker"],
    "description": ["Description", "Security Description"],
    "quantity": ["Quantity", "Shares", "Quantity Held"],
    "acquired": ["Date Acquired", "Acquisition Date", "Purchase Date", "Acquired"],
    "cost_basis": ["Cost Basis", "Total Cost Basis", "Cost Basis Total"],
    "cost_per_share": ["Cost Per Share", "Average Cost Basis", "Unit Cost"],
    "market_value": ["Current Value", "Market Value", "Value"],
    "unrealized": ["Unrealized Gain/Loss", "Total Gain/Loss Dollar", "Gain/Loss"],
    "term": ["Term", "Holding Period"],
}

TXN_CANDIDATES = {
    "account": ["Account Number", "Account"],
    "trade_date": ["Run Date", "Trade Date", "Date"],
    "settle_date": ["Settlement Date", "Settle Date"],
    "action": ["Action", "Transaction Type", "Type"],
    "symbol": ["Symbol", "Ticker"],
    "description": ["Description", "Security Description"],
    "quantity": ["Quantity", "Shares"],
    "price": ["Price", "Price ($)"],
    "amount": ["Amount", "Amount ($)", "Net Amount"],
}

# Header fingerprints used to tell the three file types apart.
_POSITION_MARKERS = {"symbol", "current value", "cost basis total", "last price"}
_LOT_MARKERS = {"date acquired", "cost per share", "acquisition date"}
_TXN_MARKERS = {"run date", "action", "settlement date"}


def detect(path: Path) -> str | None:
    """Return 'positions', 'lots', or 'transactions' if this looks like a Fidelity export."""
    from parsers.common import sniff_lines

    cells = {c.lower().strip() for row in sniff_lines(path, 30) for c in row if c.strip()}

    if cells & _LOT_MARKERS and "symbol" in cells:
        return "lots"
    if cells & _TXN_MARKERS:
        return "transactions"
    if len(cells & _POSITION_MARKERS) >= 2:
        return "positions"
    return None


def parse(path: Path, cfg: dict, kind: str, as_of: date | None = None) -> ParseResult:
    if kind == "positions":
        return _parse_positions(path, cfg, as_of)
    if kind == "lots":
        return _parse_lots(path, cfg, as_of)
    if kind == "transactions":
        return _parse_transactions(path, cfg)
    raise ValueError(f"fidelity parser does not handle kind={kind!r}")


def _parse_positions(path: Path, cfg: dict, as_of: date | None) -> ParseResult:
    expected = {c for group in POSITION_CANDIDATES.values() for c in group}
    df, _, notes = read_broker_csv(path, expected)

    result = ParseResult(source_file=path.name, platform=PLATFORM, kind="positions")
    for n in notes:
        result.warn(n)
    result.rows_read = len(df)
    result.as_of_date = as_of or infer_as_of_date(path, df)

    if result.as_of_date is None:
        result.warn(
            "Could not determine the snapshot date from the file. "
            "Pass --as-of YYYY-MM-DD."
        )
        return result

    cols = {k: pick_column(df, v) for k, v in POSITION_CANDIDATES.items()}
    for required in ("symbol", "market_value"):
        if cols[required] is None:
            result.warn(f"Required column '{required}' not found. Columns seen: {list(df.columns)}")
            return result

    for idx, row in df.iterrows():
        symbol = clean_symbol(get(row, cols["symbol"]))
        description = get(row, cols["description"])

        # Fidelity emits per-account subtotal rows with no symbol. Capture the
        # stated total instead of dropping it: it is what load.py reconciles against.
        if symbol is None:
            mv = clean_decimal(get(row, cols["market_value"]))
            acct = resolve_account(get(row, cols["account"]), PLATFORM, cfg, result, idx)
            if acct and mv is not None:
                result.stated_totals[(result.as_of_date, acct)] = mv
                # Not a dropped position, it is a subtotal row we consumed.
                result.drops = [d for d in result.drops if d[0] != idx]
            else:
                result.drop(idx, "no symbol and not a recognizable account subtotal")
            continue

        account_id = resolve_account(get(row, cols["account"]), PLATFORM, cfg, result, idx)
        if account_id is None:
            continue

        market_value = clean_decimal(get(row, cols["market_value"]))
        if market_value is None:
            result.drop(idx, f"{symbol}: no market value")
            continue

        cost_basis = clean_decimal(get(row, cols["cost_basis"]))
        if cost_basis is None and classify_security_type(symbol, description) == "equity":
            result.warn(
                f"{symbol} in {account_id} has no cost basis in this export. "
                "Unrealized gain/loss and any tax analysis on it will be incomplete."
            )

        result.holdings.append({
            "as_of_date": result.as_of_date,
            "account_id": account_id,
            "symbol": symbol,
            "quantity": clean_decimal(get(row, cols["quantity"])),
            "price": clean_decimal(get(row, cols["price"])),
            "market_value": market_value,
            "cost_basis_total": cost_basis,
            "unrealized_gl": clean_decimal(get(row, cols["unrealized"])),
            "source": "broker",
            "source_file": path.name,
        })

    return result


def _parse_lots(path: Path, cfg: dict, as_of: date | None) -> ParseResult:
    expected = {c for group in LOT_CANDIDATES.values() for c in group}
    df, _, notes = read_broker_csv(path, expected)

    result = ParseResult(source_file=path.name, platform=PLATFORM, kind="lots")
    for n in notes:
        result.warn(n)
    result.rows_read = len(df)
    result.as_of_date = as_of or infer_as_of_date(path, df)

    if result.as_of_date is None:
        result.warn("Could not determine the snapshot date. Pass --as-of YYYY-MM-DD.")
        return result

    cols = {k: pick_column(df, v) for k, v in LOT_CANDIDATES.items()}
    if cols["symbol"] is None or cols["quantity"] is None:
        result.warn(f"Lot file missing symbol or quantity. Columns seen: {list(df.columns)}")
        return result

    if cols["acquired"] is None:
        result.warn(
            "No acquisition date column in the lot file. Holding period and "
            "wash-sale analysis cannot be done without it, so this file adds "
            "little over the positions export."
        )

    no_date_count = 0

    for idx, row in df.iterrows():
        symbol = clean_symbol(get(row, cols["symbol"]))
        if symbol is None:
            result.drop(idx, "no symbol")
            continue

        account_id = resolve_account(get(row, cols["account"]), PLATFORM, cfg, result, idx)
        if account_id is None:
            continue

        quantity = clean_decimal(get(row, cols["quantity"]))
        if quantity is None:
            result.drop(idx, f"{symbol}: no quantity")
            continue

        acquired = clean_date(get(row, cols["acquired"]))
        if acquired is None:
            no_date_count += 1

        cost_basis = clean_decimal(get(row, cols["cost_basis"]))
        cost_per_share = clean_decimal(get(row, cols["cost_per_share"]))
        if cost_per_share is None and cost_basis is not None and quantity:
            cost_per_share = cost_basis / quantity

        market_value = clean_decimal(get(row, cols["market_value"]))
        unrealized = clean_decimal(get(row, cols["unrealized"]))
        if unrealized is None and market_value is not None and cost_basis is not None:
            unrealized = market_value - cost_basis

        # Term is derived, never taken from the file. Brokers disagree on the
        # boundary and we want one consistent rule we can explain.
        result.lots.append({
            "as_of_date": result.as_of_date,
            "account_id": account_id,
            "symbol": symbol,
            "acquired_date": acquired,
            "quantity": quantity,
            "cost_basis": cost_basis,
            "cost_per_share": cost_per_share,
            "market_value": market_value,
            "unrealized_gl": unrealized,
            "term": holding_term(acquired, result.as_of_date),
            "source_file": path.name,
        })

    if no_date_count:
        result.warn(
            f"{no_date_count} of {len(result.lots)} lots have no acquisition date. "
            "Those lots cannot be classified short vs long term."
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
        result.warn(f"No trade date column found. Columns seen: {list(df.columns)}")
        return result

    for idx, row in df.iterrows():
        trade_date = clean_date(get(row, cols["trade_date"]))
        if trade_date is None:
            result.drop(idx, "no parseable trade date")
            continue

        account_id = resolve_account(get(row, cols["account"]), PLATFORM, cfg, result, idx)
        if account_id is None:
            continue

        description = get(row, cols["description"])
        raw_action = get(row, cols["action"])

        result.transactions.append({
            "account_id": account_id,
            "trade_date": trade_date,
            "settle_date": clean_date(get(row, cols["settle_date"])),
            "action": classify_action(description, raw_action),
            "symbol": clean_symbol(get(row, cols["symbol"])),
            "quantity": clean_decimal(get(row, cols["quantity"])),
            "price": clean_decimal(get(row, cols["price"])),
            "amount": clean_decimal(get(row, cols["amount"])),
            "description": None if is_null(description) else str(description).strip()[:500],
            "source_file": path.name,
        })

    unknown = sum(1 for t in result.transactions if t["action"] == "other")
    if unknown:
        result.warn(
            f"{unknown} transaction(s) could not be classified into a known action. "
            "They are loaded as 'other' and excluded from realized gain/loss."
        )

    return result
