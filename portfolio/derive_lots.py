"""
derive_lots.py — Back-derive tax lots from transaction history.

Fidelity hands you lot detail directly. Robinhood does not, so for those accounts
lots are reconstructed by replaying the buy/sell history under FIFO, which is
both the IRS default and Robinhood's default disposal method.

Two things make derived lots trustworthy or not, and both are checked here:

  1. The transaction history must cover the entire life of the account. If it
     starts partway through, the opening position is invisible and every derived
     basis after it is wrong.
  2. The derived share count must match the position snapshot. If they disagree,
     something was missed (a split, a transfer in, an option assignment) and the
     lots are reported as unreliable rather than presented as fact.

When either check fails, the lots are still written but flagged, and the tax
metrics treat flagged lots as unusable. Better a gap the report names out loud
than a confident wrong number.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from parsers.common import holding_term

# Actions that move share count. Everything else (fees, transfers of cash,
# interest) does not affect a lot.
OPENING = {"buy"}
CLOSING = {"sell"}


@dataclass
class DerivedLots:
    lots: list[dict]
    warnings: list[str]
    reliable: bool


def derive(
    transactions: list[dict],
    as_of: date,
    snapshot_quantities: dict[tuple[str, str], Decimal] | None = None,
    account_open_dates: dict[str, date] | None = None,
) -> DerivedLots:
    """Replay transactions under FIFO to produce open lots as of `as_of`.

    transactions: normalized transaction dicts (account_id, trade_date, action,
        symbol, quantity, price, amount).
    snapshot_quantities: (account_id, symbol) -> quantity from the positions
        export, used to verify the replay landed in the right place.
    account_open_dates: account_id -> the date the account was opened, if known,
        used to check that history is complete.
    """
    warnings: list[str] = []
    reliable = True

    relevant = [
        t for t in transactions
        if t.get("symbol") and t.get("quantity") and t.get("trade_date")
        and t.get("action") in OPENING | CLOSING
    ]
    if not relevant:
        return DerivedLots([], ["No buy or sell transactions to derive lots from."], False)

    relevant.sort(key=lambda t: (t["trade_date"], 0 if t["action"] in OPENING else 1))

    # Splits are not replayed. Robinhood's split rows are inconsistent enough
    # that guessing would do more harm than leaving this visible.
    split_symbols = {
        t["symbol"] for t in transactions
        if t.get("action") == "split" and t.get("symbol")
    }
    if split_symbols:
        warnings.append(
            f"Split activity found in {', '.join(sorted(split_symbols))}. Derived "
            "lots do not replay splits, so share counts and per-share basis for "
            "these symbols are unreliable. Verify against the broker before "
            "acting on any tax analysis involving them."
        )
        reliable = False

    books: dict[tuple[str, str], deque] = defaultdict(deque)
    oversold: set[tuple[str, str]] = set()

    for txn in relevant:
        key = (txn["account_id"], txn["symbol"])
        qty = abs(Decimal(str(txn["quantity"])))
        if qty == 0:
            continue

        if txn["action"] in OPENING:
            price = txn.get("price")
            if price is None and txn.get("amount") is not None:
                price = abs(Decimal(str(txn["amount"]))) / qty
            books[key].append({
                "acquired_date": txn["trade_date"],
                "quantity": qty,
                "cost_per_share": Decimal(str(price)) if price is not None else None,
            })
            continue

        # Sell: consume oldest lots first.
        remaining = qty
        while remaining > 0 and books[key]:
            lot = books[key][0]
            take = min(remaining, lot["quantity"])
            lot["quantity"] -= take
            remaining -= take
            if lot["quantity"] == 0:
                books[key].popleft()

        if remaining > 0:
            oversold.add(key)

    if oversold:
        names = ", ".join(f"{a}/{s}" for a, s in sorted(oversold))
        warnings.append(
            f"Sold more shares than the history accounts for in: {names}. The "
            "transaction export almost certainly does not go back far enough. "
            "Regenerate it covering the full life of the account."
        )
        reliable = False

    lots: list[dict] = []
    derived_qty: dict[tuple[str, str], Decimal] = defaultdict(Decimal)

    for (account_id, symbol), open_lots in books.items():
        for lot in open_lots:
            if lot["quantity"] <= 0:
                continue
            cps = lot["cost_per_share"]
            basis = (cps * lot["quantity"]) if cps is not None else None
            derived_qty[(account_id, symbol)] += lot["quantity"]
            lots.append({
                "as_of_date": as_of,
                "account_id": account_id,
                "symbol": symbol,
                "acquired_date": lot["acquired_date"],
                "quantity": lot["quantity"],
                "cost_basis": basis,
                "cost_per_share": cps,
                "market_value": None,   # filled by enrich.py from current price
                "unrealized_gl": None,  # derived once market value is known
                "term": holding_term(lot["acquired_date"], as_of),
                "source_file": "derived:fifo",
            })

    if snapshot_quantities:
        mismatches = []
        for key, snap_qty in snapshot_quantities.items():
            got = derived_qty.get(key, Decimal(0))
            snap = Decimal(str(snap_qty))
            # Fractional shares make exact equality too strict; a hundredth of a
            # share is rounding, anything larger is a real gap.
            if abs(got - snap) > Decimal("0.01"):
                mismatches.append(f"{key[0]}/{key[1]}: derived {got}, snapshot {snap}")

        if mismatches:
            warnings.append(
                "Derived share counts do not match the position snapshot for "
                f"{len(mismatches)} position(s): " + "; ".join(mismatches[:10])
                + ". Lots for these are not trustworthy."
            )
            reliable = False

    if account_open_dates:
        first_txn: dict[str, date] = {}
        for txn in relevant:
            aid = txn["account_id"]
            d = txn["trade_date"]
            first_txn[aid] = min(first_txn.get(aid, d), d)
        for aid, opened in account_open_dates.items():
            if aid in first_txn and first_txn[aid] > opened:
                warnings.append(
                    f"{aid}: history starts {first_txn[aid]} but the account opened "
                    f"{opened}. Earlier activity is missing from the export."
                )
                reliable = False

    return DerivedLots(lots, warnings, reliable)
