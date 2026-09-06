"""
manual.py — Parse a hand-maintained holdings CSV.

Not every account can be exported. Empower's own download carries no usable cost
basis, an old account may never be exported at all, and sometimes the only
record of a position is something written down by hand. This parser reads a
normalized CSV so those positions are first-class rather than missing.

This is permanent infrastructure, not a stopgap. Expect to keep using it.

Format (header required, column order irrelevant, extra columns ignored):

    account_id,symbol,quantity,price,market_value,cost_basis,acquired_date,value_as_of,note

Rules:
  - account_id must match config.yaml exactly. Unlike the broker parsers there is
    no account-number matching to fall back on, so a typo is a hard error rather
    than a silent drop.
  - **Give a share count.** It is the field that matters. Market value is a
    price times a quantity and the price changes daily, so a recorded market
    value is frozen at the moment it was typed. A position with a share count
    gets repriced on every run; a position with only a dollar amount cannot be
    repriced at all, ever, because there is nothing to multiply.
    A market value is still accepted, as a fallback for anything whose share
    count is genuinely unavailable, and for reconciliation.
  - ONE ROW PER TAX LOT. Repeat the same account_id and symbol for each lot with
    its own acquired_date, quantity and cost_basis. The rows are summed into a
    single position and each becomes its own lot, which is what a broker's
    purchase history actually looks like and what any holding-period or
    wash-sale question needs.
  - value_as_of records when the number was actually true. Leave it blank and it
    defaults to the snapshot date. Fill it in when an account has not been
    refreshed, so the database never claims a stale number is current.
  - A row with a blank symbol and market_value set is an ACCOUNT TOTAL. It flows
    through the same reconciliation gate the broker parsers use, so a hand-typed
    file still has to add up before anything is written.

The snapshot date comes from --as-of, an as_of_date column, or the filename.
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
    holding_term,
    infer_as_of_date,
    is_null,
    pick_column,
    read_broker_csv,
    sniff_lines,
)

PLATFORM = "manual"

CANDIDATES = {
    "account_id": ["account_id", "account", "account id"],
    "symbol": ["symbol", "ticker"],
    "quantity": ["quantity", "shares", "qty"],
    "price": ["price", "last price"],
    "market_value": ["market_value", "value", "market value", "current value"],
    "cost_basis": ["cost_basis", "cost basis", "basis", "total cost"],
    "acquired_date": ["acquired_date", "acquired", "date acquired", "purchase date"],
    "value_as_of": ["value_as_of", "value as of", "valued", "as of"],
    "note": ["note", "notes", "comment"],
}

REQUIRED = {"account_id", "symbol"}

# A marker in the symbol column also works, for people who would rather write it
# than leave a cell blank.
TOTAL_MARKERS = {"ACCOUNT_TOTAL", "ACCOUNTTOTAL", "TOTAL", "SUBTOTAL"}


TXN_CANDIDATES = {
    "account_id": ["account_id", "account", "account id"],
    "trade_date": ["trade_date", "date", "trade date", "activity date"],
    "action": ["action", "type", "transaction_type", "trans code"],
    "symbol": ["symbol", "ticker"],
    "quantity": ["quantity", "shares", "qty"],
    "price": ["price"],
    "amount": ["amount", "net_amount", "total"],
    "note": ["note", "notes", "description"],
}

TXN_MARKERS = {"trade_date", "trade date", "action", "transaction_type"}


def detect(path: Path) -> str | None:
    """Identify by the account_id column, which no broker export has.

    A hand-written file is either positions (what is held) or transactions (what
    happened). Both carry account_id; a trade date or an action column is what
    distinguishes the second.
    """
    cells = {c.lower().strip() for row in sniff_lines(path, 10) for c in row if c.strip()}
    if not ("account_id" in cells or "account id" in cells):
        return None
    if cells & TXN_MARKERS:
        return "transactions"
    return "positions"


def parse(path: Path, cfg: dict, kind: str, as_of: date | None = None) -> ParseResult:
    if kind == "transactions":
        return _parse_transactions(path, cfg)
    if kind != "positions":
        raise ValueError(f"manual parser does not handle kind={kind!r}")

    expected = {c for group in CANDIDATES.values() for c in group}
    df, _, notes = read_broker_csv(path, expected)

    result = ParseResult(source_file=path.name, platform=PLATFORM, kind="positions")
    for n in notes:
        result.warn(n)
    result.rows_read = len(df)
    result.as_of_date = as_of or infer_as_of_date(path, df)

    if result.as_of_date is None:
        result.warn("Could not determine the snapshot date. Pass --as-of YYYY-MM-DD.")
        return result

    cols = {k: pick_column(df, v) for k, v in CANDIDATES.items()}
    missing = [k for k in REQUIRED if cols[k] is None]
    if missing:
        result.warn(
            f"Missing required column(s) {missing}. Columns seen: {list(df.columns)}"
        )
        return result

    known_accounts = {a["account_id"] for a in cfg["accounts"]}
    positions: dict[tuple, dict] = {}
    valued_without_qty = 0

    for idx, row in df.iterrows():
        account_id = get(row, cols["account_id"])
        if is_null(account_id):
            result.drop(idx, "no account_id")
            continue
        account_id = str(account_id).strip()

        if account_id not in known_accounts:
            # No account-number fallback exists for a hand-written file, so this
            # is almost always a typo and worth stopping for.
            result.unknown_accounts.add(f"{account_id} (manual)")
            result.drop(idx, f"account_id '{account_id}' is not in config.yaml")
            continue

        raw_symbol = get(row, cols["symbol"])
        symbol = clean_symbol(raw_symbol)
        market_value = clean_decimal(get(row, cols["market_value"]))

        # Account total row: blank symbol, or an explicit marker.
        if symbol is None or symbol in TOTAL_MARKERS:
            if market_value is not None:
                result.stated_totals[(result.as_of_date, account_id)] = market_value
            else:
                result.drop(idx, "blank symbol and no market value, cannot interpret")
            continue

        quantity = clean_decimal(get(row, cols["quantity"]))
        if quantity is None and market_value is None:
            result.drop(idx, f"{symbol}: needs quantity or market_value, has neither")
            continue

        if market_value is None:
            valued_without_qty += 1

        price = clean_decimal(get(row, cols["price"]))
        if price is None and market_value is not None and quantity:
            price = market_value / quantity

        cost_basis = clean_decimal(get(row, cols["cost_basis"]))
        unrealized = (
            market_value - cost_basis
            if market_value is not None and cost_basis is not None
            else None
        )

        value_as_of = clean_date(get(row, cols["value_as_of"])) or result.as_of_date

        # Several rows can describe one position, one per tax lot. Accumulate
        # into a single holding so the position table stays one row per symbol
        # while the lot detail below stays intact.
        key = (account_id, symbol)
        pos = positions.get(key)
        if pos is None:
            positions[key] = {
                "as_of_date": result.as_of_date,
                "account_id": account_id,
                "symbol": symbol,
                "quantity": quantity,
                "price": price,
                "market_value": market_value,
                "cost_basis_total": cost_basis,
                "unrealized_gl": unrealized,
                "source": "manual",
                "source_file": path.name,
                "value_as_of": value_as_of,
            }
        else:
            for field, value in (("quantity", quantity),
                                 ("market_value", market_value),
                                 ("cost_basis_total", cost_basis)):
                if value is not None:
                    pos[field] = value if pos[field] is None else pos[field] + value
            if price is not None and pos["price"] is None:
                pos["price"] = price
            # Oldest observation wins: a position is only as current as its
            # least current input.
            pos["value_as_of"] = min(pos["value_as_of"], value_as_of)
            if pos["market_value"] is not None and pos["cost_basis_total"] is not None:
                pos["unrealized_gl"] = pos["market_value"] - pos["cost_basis_total"]

        # An acquisition date on a manual row makes it a usable tax lot too,
        # which is the only way a hand-entered position ever gets one.
        acquired = clean_date(get(row, cols["acquired_date"]))
        if acquired is not None:
            result.lots.append({
                "as_of_date": result.as_of_date,
                "account_id": account_id,
                "symbol": symbol,
                "acquired_date": acquired,
                "quantity": quantity,
                "cost_basis": cost_basis,
                "cost_per_share": (cost_basis / quantity)
                                  if cost_basis is not None and quantity else None,
                "market_value": market_value,
                "unrealized_gl": unrealized,
                "term": holding_term(acquired, result.as_of_date),
                "source_file": path.name,
                "value_as_of": value_as_of,
            })

    result.holdings.extend(positions.values())

    multi = [f"{a}/{sym}" for (a, sym), _ in positions.items()
             if sum(1 for l in result.lots
                    if l["account_id"] == a and l["symbol"] == sym) > 1]
    if multi:
        result.warn(
            f"{len(multi)} position(s) built from multiple tax lots: "
            + ", ".join(sorted(multi))
        )

    # Only genuinely old values are worth flagging. A position priced at the
    # prior close is not stale, and lumping it in with one that is months old
    # buries the signal.
    tolerance = (cfg.get("thresholds") or {}).get("stale_after_days", 7)
    stale = [
        h for h in result.holdings
        if (result.as_of_date - h["value_as_of"]).days > tolerance
    ]
    if stale:
        oldest = min(h["value_as_of"] for h in stale)
        total = sum(h["market_value"] or 0 for h in stale)
        accounts = sorted({h["account_id"] for h in stale})
        result.warn(
            f"{len(stale)} position(s) worth ${total:,.0f} carry values more than "
            f"{tolerance} days older than the snapshot, back to {oldest} "
            f"({', '.join(accounts)}). They are recorded with their real "
            "value_as_of date rather than stamped as current."
        )

    if valued_without_qty:
        result.warn(
            f"{valued_without_qty} position(s) have quantity but no market value. "
            "enrich.py will price them from the latest close."
        )

    return result


def _parse_transactions(path: Path, cfg: dict) -> ParseResult:
    """Parse a hand-written transactions file.

    This is what the ledger agent writes to. Every row is something that
    happened: a purchase, a sale, a contribution, a dividend. Unlike a positions
    file it is append-only, so an entry recorded once is never rewritten.
    """
    expected = {c for group in TXN_CANDIDATES.values() for c in group}
    df, _, notes = read_broker_csv(path, expected)

    result = ParseResult(source_file=path.name, platform=PLATFORM, kind="transactions")
    for n in notes:
        result.warn(n)
    result.rows_read = len(df)

    cols = {k: pick_column(df, v) for k, v in TXN_CANDIDATES.items()}
    for required in ("account_id", "trade_date"):
        if cols[required] is None:
            result.warn(
                f"Required column '{required}' not found. Columns seen: {list(df.columns)}"
            )
            return result

    known_accounts = {a["account_id"] for a in cfg["accounts"]}

    for idx, row in df.iterrows():
        account_id = get(row, cols["account_id"])
        if is_null(account_id):
            result.drop(idx, "no account_id")
            continue
        account_id = str(account_id).strip()

        if account_id not in known_accounts:
            result.unknown_accounts.add(f"{account_id} (manual)")
            result.drop(idx, f"account_id '{account_id}' is not in config.yaml")
            continue

        trade_date = clean_date(get(row, cols["trade_date"]))
        if trade_date is None:
            result.drop(idx, "no parseable trade date")
            continue

        note = get(row, cols["note"])
        action = classify_action(note, get(row, cols["action"]))

        result.transactions.append({
            "account_id": account_id,
            "trade_date": trade_date,
            "settle_date": None,
            "action": action,
            "symbol": clean_symbol(get(row, cols["symbol"])),
            "quantity": clean_decimal(get(row, cols["quantity"])),
            "price": clean_decimal(get(row, cols["price"])),
            "amount": clean_decimal(get(row, cols["amount"])),
            "description": None if is_null(note) else str(note).strip()[:500],
            "source_file": path.name,
        })

    unknown = [t for t in result.transactions if t["action"] == "other"]
    if unknown:
        result.warn(
            f"{len(unknown)} row(s) could not be classified into a known action "
            "and loaded as 'other'. They are excluded from realized gain/loss and "
            "from wash-sale detection, so an unclassified buy is an invisible buy."
        )

    buys_without_detail = [
        t for t in result.transactions
        if t["action"] == "buy" and (t["symbol"] is None or t["quantity"] is None)
    ]
    if buys_without_detail:
        result.warn(
            f"{len(buys_without_detail)} purchase(s) are missing a symbol or share "
            "count. A purchase without both cannot establish a tax lot and will "
            "not be seen by wash-sale detection."
        )

    return result
