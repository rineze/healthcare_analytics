"""
load.py — Ingest broker exports from inbox/ into the portfolio schema.

    python portfolio/load.py                 load everything in inbox/
    python portfolio/load.py --dry-run       parse and reconcile, write nothing
    python portfolio/load.py --as-of 2026-09-05
    python portfolio/load.py --archive       move loaded files to inbox/processed/

The validation gate is the point of this script. It parses everything, reconciles
what it parsed against what the files claim, prints the result, and only then
writes. If the numbers do not tie, it refuses to write and says why.

That strictness is deliberate. A parser that silently drops rows produces a
report that is wrong in a way nobody can see, and every agent downstream repeats
the error with total confidence. Failing loudly at load time is the only place
this is cheap to catch.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import config as config_mod
import db
import derive_lots
from parsers import PARSERS
from parsers.common import ParseResult

INBOX = Path(__file__).parent / "inbox"
PROCESSED = INBOX / "processed"

# Aggregated rows never overwrite rows that came straight from the broker.
SOURCE_PRECEDENCE = {"broker": 2, "aggregator": 1}

RECONCILE_TOLERANCE = Decimal("0.01")


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def route(path: Path) -> tuple[str, str] | None:
    """Work out which platform and file type this is.

    Asks every parser to identify the file by its header, then uses the filename
    as a tiebreaker when more than one claims it.
    """
    claims = []
    for platform, module in PARSERS.items():
        try:
            kind = module.detect(path)
        except Exception as exc:  # a malformed file should not kill the run
            print(f"  ! {platform}.detect failed on {path.name}: {exc}")
            continue
        if kind:
            claims.append((platform, kind))

    if not claims:
        return None
    if len(claims) == 1:
        return claims[0]

    stem = path.stem.lower()
    for platform, kind in claims:
        if platform in stem:
            return platform, kind

    return claims[0]


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

class Reconciliation:
    """Collects everything that has to be true before we write."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.notes: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors


def dedupe_holdings(holdings: list[dict], rec: Reconciliation) -> list[dict]:
    """Collapse duplicate positions, preferring the broker's own export.

    Empower reports the same Fidelity position Fidelity already reported. Keeping
    both would double the portfolio.
    """
    best: dict[tuple, dict] = {}
    overlaps = 0

    for h in holdings:
        key = (h["as_of_date"], h["account_id"], h["symbol"])
        current = best.get(key)
        if current is None:
            best[key] = h
            continue

        overlaps += 1
        new_rank = SOURCE_PRECEDENCE.get(h.get("source"), 0)
        old_rank = SOURCE_PRECEDENCE.get(current.get("source"), 0)
        if new_rank > old_rank:
            best[key] = h

    if overlaps:
        rec.note(
            f"Resolved {overlaps} duplicate position row(s) across files "
            "(broker export wins over aggregator)."
        )

    return list(best.values())


def reconcile_totals(
    holdings: list[dict],
    stated: dict[tuple, Decimal],
    rec: Reconciliation,
) -> None:
    """Compare parsed market value against what each file claimed.

    Keyed by (as_of_date, account_id), not account alone. Dropping several
    months of exports in the inbox at once is normal, and pooling them by
    account would compare August's stated total against September's positions
    and fail for no reason.
    """
    computed: dict[tuple, Decimal] = defaultdict(Decimal)
    for h in holdings:
        if h.get("market_value") is not None:
            computed[(h["as_of_date"], h["account_id"])] += Decimal(str(h["market_value"]))

    print("\n  Account totals")
    print("  " + "-" * 76)
    print(f"  {'snapshot':<13}{'account':<22}{'parsed':>15}{'stated':>15}{'diff':>11}")

    for key in sorted(computed, key=lambda k: (str(k[0]), k[1])):
        as_of, account_id = key
        got = computed[key]
        claim = stated.get(key)

        if claim is None:
            print(f"  {str(as_of):<13}{account_id:<22}{got:>15,.2f}{'(none)':>15}{'':>11}")
            rec.warn(
                f"{account_id} on {as_of}: the export does not state an account "
                f"total, so the parsed value of ${got:,.2f} could not be verified. "
                "Eyeball it against your statement."
            )
            continue

        diff = got - claim
        flag = "" if abs(diff) <= RECONCILE_TOLERANCE else "  <-- MISMATCH"
        print(f"  {str(as_of):<13}{account_id:<22}{got:>15,.2f}{claim:>15,.2f}"
              f"{diff:>11,.2f}{flag}")

        if abs(diff) > RECONCILE_TOLERANCE:
            rec.error(
                f"{account_id} on {as_of}: parsed ${got:,.2f} but the file states "
                f"${claim:,.2f}, off by ${diff:,.2f}. Rows are being dropped or "
                "misread. Nothing was written."
            )

    print("  " + "-" * 76)
    by_date: dict = defaultdict(Decimal)
    for (as_of, _), value in computed.items():
        by_date[as_of] += value
    for as_of in sorted(by_date, key=str):
        print(f"  {str(as_of):<13}{'TOTAL':<22}{by_date[as_of]:>15,.2f}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def collect(cfg: dict, as_of: date | None, rec: Reconciliation) -> list[ParseResult]:
    files = sorted(
        p for p in INBOX.glob("*")
        if p.is_file() and p.suffix.lower() in {".csv", ".txt"}
    )

    if not files:
        print(f"  inbox is empty ({INBOX})")
        return []

    results: list[ParseResult] = []

    for path in files:
        routed = route(path)
        if routed is None:
            rec.warn(
                f"{path.name}: could not identify which broker or file type this is. "
                "Skipped. If it is a real export, its column names may have changed."
            )
            print(f"  ? {path.name:<44} unrecognized, skipped")
            continue

        platform, kind = routed
        try:
            result = PARSERS[platform].parse(path, cfg, kind, as_of)
        except Exception as exc:
            rec.error(f"{path.name}: parser raised {type(exc).__name__}: {exc}")
            print(f"  ! {path.name:<44} FAILED")
            continue

        results.append(result)
        print(
            f"  + {path.name:<44} {platform}/{kind:<13} "
            f"{result.rows_loaded}/{result.rows_read} rows"
        )

    return results


def load(as_of: date | None, dry_run: bool, archive: bool, allow_unknown: bool) -> int:
    rec = Reconciliation()

    cfg = config_mod.load_config()
    db.init_schema()
    config_mod.sync_accounts_to_db(cfg)

    print(f"\nScanning {INBOX}")
    results = collect(cfg, as_of, rec)
    if not results:
        print("\nNothing to load.\n")
        return 0

    holdings: list[dict] = []
    lots: list[dict] = []
    transactions: list[dict] = []
    stated: dict[str, Decimal] = {}
    unknown_accounts: set[str] = set()

    for r in results:
        holdings.extend(r.holdings)
        lots.extend(r.lots)
        transactions.extend(r.transactions)
        stated.update(r.stated_totals)
        unknown_accounts |= r.unknown_accounts
        for w in r.warnings:
            rec.warn(f"{r.source_file}: {w}")
        for row_idx, reason in r.drops:
            rec.note(f"{r.source_file} row {row_idx}: {reason}")

    # Broker parsers do not set value_as_of; their numbers are current as of the
    # snapshot by definition. Only the manual parser can say otherwise.
    for h in holdings:
        h.setdefault("value_as_of", h.get("as_of_date"))
    for lot in lots:
        lot.setdefault("value_as_of", lot.get("as_of_date"))

    holdings = dedupe_holdings(holdings, rec)

    snapshot_date = as_of or max(
        (h["as_of_date"] for h in holdings if h.get("as_of_date")),
        default=None,
    )

    # Accounts with transactions but no broker-supplied lots get theirs derived.
    accounts_with_lots = {l["account_id"] for l in lots}
    accounts_with_txns = {t["account_id"] for t in transactions}
    needs_derivation = accounts_with_txns - accounts_with_lots

    if needs_derivation and snapshot_date:
        snapshot_qty = {
            (h["account_id"], h["symbol"]): h["quantity"]
            for h in holdings
            if h["account_id"] in needs_derivation and h.get("quantity") is not None
        }
        derived = derive_lots.derive(
            [t for t in transactions if t["account_id"] in needs_derivation],
            snapshot_date,
            snapshot_quantities=snapshot_qty or None,
        )
        lots.extend(derived.lots)
        for w in derived.warnings:
            rec.warn(f"derived lots: {w}")
        print(
            f"\n  Derived {len(derived.lots)} lot(s) via FIFO for "
            f"{', '.join(sorted(needs_derivation))} "
            f"({'reliable' if derived.reliable else 'FLAGGED UNRELIABLE'})"
        )

    reconcile_totals(holdings, stated, rec)

    unpriced = [h for h in holdings if h.get("market_value") is None]
    if unpriced:
        rec.warn(
            f"{len(unpriced)} position(s) have no market value yet: "
            + ", ".join(sorted(h["symbol"] for h in unpriced))
            + ". Account totals above exclude them. Run enrich.py to price them, "
            "then re-check."
        )

    # Cost basis coverage drives whether the tax agent can do anything at all.
    no_basis = [h for h in holdings if h.get("cost_basis_total") is None]
    if no_basis:
        rec.warn(
            f"{len(no_basis)} of {len(holdings)} positions have no cost basis: "
            + ", ".join(sorted({h["symbol"] for h in no_basis})[:15])
            + ". Tax analysis on these is not possible until basis is loaded."
        )

    if unknown_accounts:
        msg = (
            "Accounts appear in your exports that are not in config.yaml: "
            + ", ".join(sorted(unknown_accounts))
            + ". Their positions were dropped, so the totals above are incomplete."
        )
        if allow_unknown:
            rec.warn(msg)
        else:
            rec.error(msg + " Add them to config.yaml, or pass --allow-unknown-accounts.")

    # ---- report -----------------------------------------------------------
    print(f"\n  Parsed: {len(holdings)} holdings, {len(lots)} lots, "
          f"{len(transactions)} transactions")

    if rec.notes:
        print(f"\n  Row-level notes ({len(rec.notes)}):")
        for n in rec.notes[:25]:
            print(f"    - {n}")
        if len(rec.notes) > 25:
            print(f"    ... and {len(rec.notes) - 25} more")

    if rec.warnings:
        print(f"\n  WARNINGS ({len(rec.warnings)}):")
        for w in rec.warnings:
            print(f"    * {w}")

    if rec.errors:
        print(f"\n  ERRORS ({len(rec.errors)}) — nothing was written:")
        for e in rec.errors:
            print(f"    X {e}")
        print()
        return 1

    if dry_run:
        print("\n  Dry run, nothing written.\n")
        return 0

    # ---- write ------------------------------------------------------------
    for lot in lots:
        lot["lot_id"] = db.stable_id(
            lot["account_id"], lot["symbol"], lot["acquired_date"],
            lot["quantity"], lot["cost_basis"], lot["as_of_date"],
        )
    for txn in transactions:
        txn["txn_id"] = db.stable_id(
            txn["account_id"], txn["trade_date"], txn["action"],
            txn["symbol"], txn["quantity"], txn["amount"],
        )

    symbols = sorted({h["symbol"] for h in holdings} | {l["symbol"] for l in lots})
    db.upsert("securities", [{"symbol": s} for s in symbols], ["symbol"])

    n_h = db.upsert("holdings", holdings, ["as_of_date", "account_id", "symbol"])
    n_l = db.upsert("lots", lots, ["lot_id"])
    n_t = db.upsert("transactions", transactions, ["txn_id"])

    print(f"\n  Wrote {n_h} holdings, {n_l} lots, {n_t} transactions "
          f"(snapshot {snapshot_date}).")

    if archive:
        PROCESSED.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        moved = 0
        for r in results:
            src = INBOX / r.source_file
            if src.exists():
                shutil.move(str(src), str(PROCESSED / f"{stamp}-{r.source_file}"))
                moved += 1
        print(f"  Archived {moved} file(s) to {PROCESSED}")

    print("\n  Next: python portfolio/enrich.py\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Load broker exports into the portfolio schema.")
    ap.add_argument("--as-of", type=str, help="Snapshot date (YYYY-MM-DD) if files do not carry one")
    ap.add_argument("--dry-run", action="store_true", help="Parse and reconcile without writing")
    ap.add_argument("--archive", action="store_true", help="Move loaded files to inbox/processed/")
    ap.add_argument("--allow-unknown-accounts", action="store_true",
                    help="Load anyway when an export references an account missing from config.yaml")
    args = ap.parse_args()

    as_of = None
    if args.as_of:
        as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date()

    INBOX.mkdir(exist_ok=True)
    return load(as_of, args.dry_run, args.archive, args.allow_unknown_accounts)


if __name__ == "__main__":
    sys.exit(main())
