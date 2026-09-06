"""
tax_metrics.py — Deterministic tax math for the tax agent.

Every function here returns FACTS. None of them return a verdict, a
recommendation, or a decision. The agent reasons over this output; it never
computes any of it itself.

Authority this module implements:

  IRC 1091      Wash sale. A loss is disallowed when substantially identical
                securities are acquired within 30 days before through 30 days
                after the sale (a 61-day window including the sale date).
  IRC 1091(d)   In a taxable account the disallowed loss is added to the
                replacement shares' basis AND the replacement inherits the
                original holding period. Deferred, not destroyed.
  Rev.Rul.2008-5 When the replacement is purchased inside an IRA or Roth IRA the
                loss is disallowed AND basis is not increased, because there is
                no basis in the IRA for 1091(d) to adjust. The loss is
                PERMANENTLY LOST. This asymmetry is why a cross-account view
                exists at all.
  IRC 1211      Losses deductible against gains, plus the lesser of $3,000
                ($1,500 married filing separately) or the excess of losses over
                gains.
  IRC 1212      Carryforward is indefinite for individuals and retains its
                short-term or long-term character.

Order of operations matters and is encoded here: 1091 runs UPSTREAM of 1211 and
1212. Only losses that survive the wash-sale test enter the netting that decides
the $3,000 deduction and the carryforward.

    python portfolio/tax_metrics.py --tlh
    python portfolio/tax_metrics.py --wash-sale UNH --date 2026-09-15
    python portfolio/tax_metrics.py --location
    python portfolio/tax_metrics.py --realized 2026
    python portfolio/tax_metrics.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta

import pandas as pd

import config as config_mod
import db
from metrics import Encoder, _f

SCHEMA = db.SCHEMA

# Harvesting a loss only does something in an account whose gains are taxed.
TAXABLE = {"taxable"}

# Rev. Rul. 2008-5 addresses IRAs and Roth IRAs directly. Employer plans are not
# covered by that ruling, so authority there is weaker. The distinction is kept
# rather than flattened, because overstating the law is its own failure.
REV_RUL_2008_5_DIRECT = {"traditional_ira", "roth_ira"}
TAX_ADVANTAGED = {"traditional_ira", "roth_ira", "401k", "roth_401k", "hsa", "529"}

WASH_SALE_WINDOW_DAYS = 30
SECTION_1211_ORDINARY_CAP = 3000       # $1,500 married filing separately


# ---------------------------------------------------------------------------
# Shared frames
# ---------------------------------------------------------------------------

def _lots(as_of: date) -> pd.DataFrame:
    return db.query(
        f"""
        SELECT l.lot_id, l.account_id, a.tax_type, l.symbol, l.acquired_date,
               l.quantity, l.cost_basis, l.cost_per_share, l.market_value,
               l.unrealized_gl, l.term, l.source_file
        FROM {SCHEMA}.lots l
        JOIN {SCHEMA}.accounts a ON a.account_id = l.account_id
        WHERE l.as_of_date = %s
        """,
        (as_of,),
    )


def _latest_lot_date() -> date | None:
    df = db.query(f"SELECT max(as_of_date) d FROM {SCHEMA}.lots")
    if df.empty or pd.isna(df.iloc[0]["d"]):
        return None
    return pd.Timestamp(df.iloc[0]["d"]).date()


def _transaction_coverage() -> dict:
    """What the transaction history actually covers.

    Existence is not coverage. A handful of rows is not a complete trade history,
    and treating "some rows exist" as "detection is complete" produces a false
    all-clear on exactly the question this module exists to answer: a position
    bought and sold entirely inside the window leaves no open lot and is visible
    only in transactions.

    There is no way to prove a history is complete, so this reports what is there
    and lets the caller phrase the limitation honestly rather than claiming one
    of two binary states.
    """
    df = db.query(
        f"""SELECT count(*) n, count(*) FILTER (WHERE action IN ('buy','sell')) trades,
                   min(trade_date) earliest, max(trade_date) latest
            FROM {SCHEMA}.transactions"""
    )
    r = df.iloc[0]
    n, trades = int(r["n"]), int(r["trades"])
    return {
        "row_count": n,
        "trade_count": trades,
        "earliest": pd.Timestamp(r["earliest"]).date() if not pd.isna(r["earliest"]) else None,
        "latest": pd.Timestamp(r["latest"]).date() if not pd.isna(r["latest"]) else None,
        "limitation": (
            "No transaction history is loaded. Detection runs off open lot "
            "acquisition dates, so a position bought and closed inside the window "
            "leaves no trace and cannot be seen."
            if trades == 0 else
            f"Transaction history holds {trades} trade record(s)"
            + (f" spanning {pd.Timestamp(r['earliest']).date()} to "
               f"{pd.Timestamp(r['latest']).date()}" if not pd.isna(r["earliest"]) else "")
            + ". Completeness cannot be verified from inside this system. Where the "
              "history is partial, a position bought and closed inside the window "
              "leaves no open lot and stays invisible."
        ),
    }


# ---------------------------------------------------------------------------
# IRC 1091 — wash sale
# ---------------------------------------------------------------------------

def wash_sale_check(symbol: str, sale_date: date, cfg: dict) -> dict:
    """Facts bearing on whether selling `symbol` on `sale_date` triggers 1091.

    Returns evidence. Never a verdict. Two categories are kept strictly apart:

      unambiguous   Same ticker acquired in the window. Substantially identical
                    by any reading.
      contested     A different ticker tracking the same thing (per
                    config.exposure_groups). Whether two funds from different
                    issuers following one index are "substantially identical" is
                    genuinely unsettled; the IRS has never ruled on it. This
                    module reports the fact and declines to decide.

    Purchases come from lot acquisition dates, which ARE purchase events, plus
    the transactions table when it is populated, plus any scheduled recurring
    purchases declared in config. Scheduled buys matter because a contribution
    that has not settled yet will not appear as a lot until the next snapshot,
    so nothing else in the system would catch it.
    """
    symbol = symbol.upper()
    start = sale_date - timedelta(days=WASH_SALE_WINDOW_DAYS)
    end = sale_date + timedelta(days=WASH_SALE_WINDOW_DAYS)

    group = config_mod.exposure_for(symbol, cfg)
    siblings = [
        s for s in cfg.get("exposure_groups", {}).get(group, [])
        if s != symbol
    ] if group in cfg.get("exposure_groups", {}) else []

    watch = [symbol] + siblings

    lots = db.query(
        f"""
        SELECT DISTINCT ON (l.account_id, l.symbol, l.acquired_date)
               l.account_id, a.tax_type, l.symbol, l.acquired_date, l.quantity
        FROM {SCHEMA}.lots l
        JOIN {SCHEMA}.accounts a ON a.account_id = l.account_id
        WHERE l.symbol = ANY(%s) AND l.acquired_date BETWEEN %s AND %s
        ORDER BY l.account_id, l.symbol, l.acquired_date
        """,
        (watch, start, end),
    )

    txns = db.query(
        f"""
        SELECT t.account_id, a.tax_type, t.symbol, t.trade_date, t.quantity
        FROM {SCHEMA}.transactions t
        JOIN {SCHEMA}.accounts a ON a.account_id = t.account_id
        WHERE t.symbol = ANY(%s) AND t.action = 'buy'
          AND t.trade_date BETWEEN %s AND %s
        """,
        (watch, start, end),
    )

    acquisitions = []
    for _, r in lots.iterrows():
        acquisitions.append({
            "source": "lot",
            "account_id": r["account_id"],
            "tax_type": r["tax_type"],
            "symbol": r["symbol"],
            "acquired_date": pd.Timestamp(r["acquired_date"]).date(),
            "quantity": _f(r["quantity"]),
        })
    for _, r in txns.iterrows():
        acquisitions.append({
            "source": "transaction",
            "account_id": r["account_id"],
            "tax_type": r["tax_type"],
            "symbol": r["symbol"],
            "acquired_date": pd.Timestamp(r["trade_date"]).date(),
            "quantity": _f(r["quantity"]),
        })

    for a in acquisitions:
        a["match_type"] = "same_ticker" if a["symbol"] == symbol else "same_exposure_group"
        a["in_tax_advantaged_account"] = a["tax_type"] in TAX_ADVANTAGED
        a["rev_rul_2008_5_applies_directly"] = a["tax_type"] in REV_RUL_2008_5_DIRECT

    unambiguous = [a for a in acquisitions if a["match_type"] == "same_ticker"]
    contested = [a for a in acquisitions if a["match_type"] == "same_exposure_group"]

    # Scheduled buys that have not appeared as lots yet. These carry the same
    # 1091 weight as a settled purchase, so they are evaluated for account type
    # exactly like one. A recurring contribution into a retirement account is the
    # textbook Rev. Rul. 2008-5 setup and missing it would defeat the point.
    tax_types = {a["account_id"]: a["tax_type"] for a in cfg["accounts"]}
    recurring = []
    for entry in (cfg.get("recurring_purchases") or []):
        if str(entry.get("symbol", "")).upper() in watch:
            sym = str(entry["symbol"]).upper()
            acct = entry.get("account_id")
            tt = tax_types.get(acct)
            recurring.append({
                "symbol": sym,
                "account_id": acct,
                "tax_type": tt,
                "cadence": entry.get("cadence"),
                "match_type": "same_ticker" if sym == symbol else "same_exposure_group",
                "in_tax_advantaged_account": tt in TAX_ADVANTAGED,
                "rev_rul_2008_5_applies_directly": tt in REV_RUL_2008_5_DIRECT,
                "note": "Scheduled purchase. Will not appear as a lot until the "
                        "next snapshot, so it is invisible to lot-based detection.",
            })

    # Settled and scheduled acquisitions both count toward the permanence risk.
    permanent_loss_risk = [
        a for a in acquisitions + recurring if a["in_tax_advantaged_account"]
    ]

    return {
        "symbol": symbol,
        "sale_date": sale_date,
        "window": {"start": start, "end": end, "days": WASH_SALE_WINDOW_DAYS * 2 + 1},
        "exposure_group": group if siblings else None,
        "symbols_watched": watch,

        "unambiguous_acquisitions": unambiguous,
        "contested_acquisitions": contested,
        "scheduled_purchases": recurring,

        "permanent_loss_risk": permanent_loss_risk,
        "has_any_evidence": bool(acquisitions or recurring),

        "contested_note": (
            "Acquisitions listed as contested are a DIFFERENT ticker tracking the "
            "same underlying as the security being sold. Whether two funds from "
            "different issuers following one index are 'substantially identical' "
            "under IRC 1091 is genuinely unsettled; the IRS has issued no ruling "
            "on index funds. This is reported as a fact and is not decided here."
        ) if contested or any(r["match_type"] == "same_exposure_group" for r in recurring) else None,

        "scheduled_purchase_note": (
            "One or more scheduled purchases fall in or around this window. A "
            "recurring contribution is an acquisition like any other for IRC 1091 "
            "purposes; the rule does not care that it was automatic. Because it "
            "has not settled into a lot yet, no amount of position data would "
            "reveal it."
        ) if recurring else None,

        "permanence_note": (
            "At least one acquisition sits in a tax-advantaged account. Under "
            "IRC 1091(d) a disallowed loss normally moves to the replacement "
            "shares' basis and the replacement inherits the original holding "
            "period, so the loss is deferred. Rev. Rul. 2008-5 holds that when "
            "the replacement is bought in an IRA or Roth IRA there is no basis "
            "to adjust, so the loss is PERMANENTLY LOST rather than deferred. "
            "That ruling addresses IRAs specifically; employer plans are not "
            "covered by it and the authority there is weaker."
        ) if permanent_loss_risk else None,

        # Always present. Completeness is unprovable, so the caveat never
        # disappears; it only changes shape.
        "transaction_coverage": _transaction_coverage(),
        "detection_limitation": _transaction_coverage()["limitation"],
    }


# ---------------------------------------------------------------------------
# Tax-loss harvesting candidates
# ---------------------------------------------------------------------------

def tlh_candidates(as_of: date, min_loss: float, cfg: dict) -> dict:
    """Loss positions in TAXABLE accounts, aggregated by symbol.

    Two design points that change the answer:

    Taxable only. A loss inside a Roth or a 401k produces no deduction, so
    including those accounts would offer opportunities that do not exist.

    The threshold applies at the SYMBOL level, not per lot. Several small loss
    lots in one name can clear a bar that none of them clears alone, and per-lot
    thresholding would hide exactly those.
    """
    lots = _lots(as_of)
    if lots.empty:
        return {"available": False, "reason": f"no lot detail on {as_of}"}

    taxable = lots[lots["tax_type"].isin(TAXABLE)]
    excluded_accounts = sorted(set(lots["account_id"]) - set(taxable["account_id"]))

    if taxable.empty:
        return {
            "available": True,
            "candidates": [],
            "excluded_non_taxable_accounts": excluded_accounts,
            "note": "No taxable accounts hold lot detail, so nothing is harvestable.",
        }

    losses = taxable[taxable["unrealized_gl"].notna() & (taxable["unrealized_gl"] < 0)]

    # A lot with no gain/loss figure cannot be judged either way. Dropping it
    # silently would let "could not be evaluated" read as "no loss here".
    unevaluable_lots = taxable[taxable["unrealized_gl"].isna()]

    # Positions with no basis never became lots at all, so they are invisible to
    # everything above and have to be surfaced separately.
    no_basis = db.query(
        f"""SELECT h.account_id, h.symbol, h.market_value
            FROM {SCHEMA}.holdings h JOIN {SCHEMA}.accounts a ON a.account_id = h.account_id
            WHERE h.as_of_date = %s AND a.tax_type = ANY(%s) AND h.cost_basis_total IS NULL""",
        (as_of, list(TAXABLE)),
    )

    candidates = []
    for symbol, grp in losses.groupby("symbol"):
        total = float(grp["unrealized_gl"].astype(float).sum())
        if abs(total) < min_loss:
            continue

        by_lot = [
            {
                "account_id": r["account_id"],
                "acquired_date": pd.Timestamp(r["acquired_date"]).date()
                                 if not pd.isna(r["acquired_date"]) else None,
                "quantity": _f(r["quantity"]),
                "cost_basis": _f(r["cost_basis"]),
                "market_value": _f(r["market_value"]),
                "unrealized_gl": _f(r["unrealized_gl"]),
                "term": r["term"],
            }
            for _, r in grp.iterrows()
        ]

        short = sum(l["unrealized_gl"] for l in by_lot if l["term"] == "short")
        long_ = sum(l["unrealized_gl"] for l in by_lot if l["term"] == "long")

        candidates.append({
            "symbol": symbol,
            "harvestable_loss": round(total, 2),
            "short_term_loss": round(short, 2),
            "long_term_loss": round(long_, 2),
            "lot_count": len(by_lot),
            "accounts": sorted({l["account_id"] for l in by_lot}),
            "spans_multiple_accounts": len({l["account_id"] for l in by_lot}) > 1,
            "lots": sorted(by_lot, key=lambda l: l["unrealized_gl"]),
            "wash_sale": wash_sale_check(symbol, as_of, cfg),
        })

    candidates.sort(key=lambda c: c["harvestable_loss"])

    total_short = sum(c["short_term_loss"] for c in candidates)
    total_long = sum(c["long_term_loss"] for c in candidates)

    below = sorted({
        s for s, g in losses.groupby("symbol")
        if abs(float(g["unrealized_gl"].astype(float).sum())) < min_loss
    })

    return {
        "available": True,
        "as_of": as_of,
        "min_loss_threshold": min_loss,
        "threshold_applied_at": "symbol",
        "candidates": candidates,
        "total_harvestable": round(sum(c["harvestable_loss"] for c in candidates), 2),
        "total_short_term": round(total_short, 2),
        "total_long_term": round(total_long, 2),
        "below_threshold_symbols": below,
        "excluded_non_taxable_accounts": excluded_accounts,

        "unevaluable": {
            "lots_missing_gain_loss": int(len(unevaluable_lots)),
            "taxable_positions_without_cost_basis": [
                {"account_id": r["account_id"], "symbol": r["symbol"],
                 "market_value": _f(r["market_value"])}
                for _, r in no_basis.iterrows()
            ],
            "market_value_unevaluable": round(
                float(no_basis["market_value"].astype(float).sum()), 2
            ) if not no_basis.empty else 0.0,
            "note": (
                "These taxable positions have no cost basis on file, so they never "
                "produced a tax lot and no gain or loss could be computed for them. "
                "They are absent from the candidate list because they could not be "
                "evaluated, NOT because they hold no loss."
            ) if not no_basis.empty or len(unevaluable_lots) else None,
        },

        "section_1211_ordinary_cap": SECTION_1211_ORDINARY_CAP,
        "netting_note": (
            "Losses net against gains of the same character first (short against "
            "short, long against long), then cross over. Any remainder offsets "
            "ordinary income up to the IRC 1211 cap, and what is left carries "
            "forward indefinitely under IRC 1212 retaining its character. Note "
            "that IRC 1091 runs upstream of all of this: only losses surviving "
            "the wash-sale test enter the netting at all."
        ),
    }


# ---------------------------------------------------------------------------
# Realized gains
# ---------------------------------------------------------------------------

def realized_gains(year: int) -> dict:
    """Realized gain/loss for a tax year, from transaction history.

    Returns unavailable rather than a guess when there is nothing to compute
    from. An estimated realized gain is worse than no number, because it looks
    like a real one.
    """
    cov = _transaction_coverage()
    if cov["trade_count"] == 0:
        return {
            "available": False,
            "year": year,
            "transaction_coverage": cov,
            "reason": (
                "No transaction history is loaded. Realized gain/loss requires the "
                "actual sale records; it cannot be derived from position snapshots. "
                "Load broker transaction history to populate this."
            ),
        }

    sells = db.query(
        f"""
        SELECT t.account_id, a.tax_type, t.symbol, t.trade_date,
               t.quantity, t.price, t.amount
        FROM {SCHEMA}.transactions t
        JOIN {SCHEMA}.accounts a ON a.account_id = t.account_id
        WHERE t.action = 'sell'
          AND extract(year FROM t.trade_date) = %s
          AND a.tax_type = ANY(%s)
        ORDER BY t.trade_date
        """,
        (year, list(TAXABLE)),
    )

    if sells.empty:
        return {
            "available": True, "year": year, "sale_count": 0,
            "note": "No sales in taxable accounts this year, so nothing realized.",
        }

    return {
        "available": True,
        "year": year,
        "sale_count": int(len(sells)),
        "proceeds": round(float(sells["amount"].astype(float).sum()), 2),
        "sales": [
            {
                "account_id": r["account_id"], "symbol": r["symbol"],
                "trade_date": pd.Timestamp(r["trade_date"]).date(),
                "quantity": _f(r["quantity"]), "amount": _f(r["amount"]),
            }
            for _, r in sells.iterrows()
        ],
        "basis_matching_limitation": (
            "Proceeds are reported. Matching each disposal to its opening lot "
            "under the account's disposal method is required to turn these into "
            "realized gain/loss, and is not implemented. Treat proceeds as an "
            "activity indicator, not as a gain figure."
        ),
    }


# ---------------------------------------------------------------------------
# Asset location
# ---------------------------------------------------------------------------

def asset_location_review(as_of: date, cfg: dict) -> dict:
    """Ordinary-income-producing assets held in taxable accounts.

    The principle: assets throwing off income taxed at ordinary rates belong in
    tax-deferred space, while assets that compound largely untaxed are fine in a
    taxable account and best in a Roth. The cost of getting it backwards is
    recurring and quantifiable.
    """
    # Use the shared frame so market values here are repriced identically to
    # everywhere else. A tax module quoting a different value for the same
    # position than the portfolio report does is its own kind of wrong.
    import metrics
    h = metrics.holdings_frame(as_of)
    if h.empty:
        return {"available": False, "reason": f"no holdings on {as_of}"}

    yields = db.query(
        f"""SELECT DISTINCT ON (symbol) symbol, div_yield FROM {SCHEMA}.fundamentals
            WHERE div_yield IS NOT NULL ORDER BY symbol, as_of_date DESC"""
    ).set_index("symbol")["div_yield"].to_dict() if True else {}
    h = h.assign(div_yield=h["symbol"].map(yields))

    # Asset classes whose income is generally ordinary rather than qualified.
    ORDINARY = {"bond", "cash"}

    rows = []
    for _, r in h.iterrows():
        if r["tax_type"] not in TAXABLE:
            continue
        if r["asset_class"] not in ORDINARY:
            continue
        mv = _f(r["market_value"]) or 0.0
        y = _f(r["div_yield"])
        # yfinance reports yield inconsistently; anything above 1 is a percent.
        rate = (y / 100 if y and y > 1 else y) if y else None
        rows.append({
            "account_id": r["account_id"],
            "symbol": r["symbol"],
            "asset_class": r["asset_class"],
            "market_value": round(mv, 2),
            "yield": round(rate, 4) if rate else None,
            "estimated_annual_income": round(mv * rate, 2) if rate else None,
        })

    deferred_capacity = float(
        h[h["tax_type"].isin(TAX_ADVANTAGED)]["market_value"].astype(float).sum()
    )
    income = sum(r["estimated_annual_income"] or 0 for r in rows)

    # Coverage guards against the worst failure here: nothing classified looks
    # identical to nothing misplaced. An empty result is only meaningful if the
    # holdings were actually classified in the first place.
    taxable_rows = h[h["tax_type"].isin(TAXABLE)]
    taxable_mv = float(taxable_rows["market_value"].astype(float).sum())
    classified_mv = float(
        taxable_rows[taxable_rows["asset_class"].notna()
                     & (taxable_rows["asset_class"] != "unknown")]
        ["market_value"].astype(float).sum()
    )
    coverage = classified_mv / taxable_mv if taxable_mv else 0.0

    return {
        "available": True,
        "as_of": as_of,
        "classification_coverage": round(coverage, 4),
        "unclassified_market_value": round(taxable_mv - classified_mv, 2),
        "result_is_meaningful": bool(coverage >= 0.95),
        "coverage_warning": None if coverage >= 0.95 else (
            f"Only {coverage:.0%} of taxable market value has a known asset class. "
            "An empty or short result here means holdings are unclassified, NOT "
            "that nothing is misplaced. Run enrich.py, or declare the asset class "
            "in config.yaml under asset_class_overrides."
        ),
        "ordinary_income_assets_in_taxable": sorted(
            rows, key=lambda r: -(r["market_value"])
        ),
        "market_value_affected": round(sum(r["market_value"] for r in rows), 2),
        "estimated_annual_ordinary_income": round(income, 2),
        "tax_advantaged_capacity": round(deferred_capacity, 2),
        "yield_coverage_note": (
            "Income estimates depend on enrichment having a yield for each symbol. "
            "Positions with no yield contribute nothing to the estimate, so the "
            "figure is a floor."
        ),
        "relocation_caveat": (
            "Moving assets between account types is not free. Selling in a taxable "
            "account to relocate realizes gains, and contribution limits constrain "
            "how fast tax-advantaged space can absorb anything. The drag figure is "
            "the recurring cost of the current arrangement, not the value of "
            "changing it."
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build(cfg: dict, as_of: date | None = None) -> dict:
    as_of = as_of or _latest_lot_date()
    if as_of is None:
        return {"error": "No lot detail loaded. Run load.py first."}
    th = cfg["thresholds"]
    return {
        "as_of": as_of,
        "generated_at": datetime.now(),
        "tlh": tlh_candidates(as_of, th["tlh_min_loss_usd"], cfg),
        "asset_location": asset_location_review(as_of, cfg),
        "realized_gains": realized_gains(as_of.year),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic tax facts. No verdicts.")
    ap.add_argument("--tlh", action="store_true", help="Harvesting candidates")
    ap.add_argument("--wash-sale", metavar="SYMBOL", help="Wash-sale evidence for a symbol")
    ap.add_argument("--date", help="Proposed sale date (YYYY-MM-DD), defaults to today")
    ap.add_argument("--location", action="store_true", help="Asset location review")
    ap.add_argument("--realized", type=int, metavar="YEAR", help="Realized gains for a year")
    ap.add_argument("--as-of", help="Snapshot date, defaults to latest")
    ap.add_argument("--json", action="store_true", help="Full payload")
    args = ap.parse_args()

    cfg = config_mod.load_config()
    as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date() if args.as_of else _latest_lot_date()

    if as_of is None:
        print("\n  No lot detail loaded. Run load.py first.\n")
        return 1

    out: dict
    if args.wash_sale:
        d = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
        out = wash_sale_check(args.wash_sale, d, cfg)
    elif args.tlh:
        out = tlh_candidates(as_of, cfg["thresholds"]["tlh_min_loss_usd"], cfg)
    elif args.location:
        out = asset_location_review(as_of, cfg)
    elif args.realized is not None:
        out = realized_gains(args.realized)
    else:
        out = build(cfg, as_of)

    print(json.dumps(out, cls=Encoder, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
