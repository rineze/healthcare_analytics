"""
metrics.py — All portfolio math. Deterministic, no LLM, emits JSON.

    python portfolio/metrics.py --json          full metrics payload
    python portfolio/metrics.py                 human-readable summary
    python portfolio/metrics.py --record <path> save this run's snapshot

This module is the contract between the data and the agents. Agents receive this
JSON and narrate it; they never compute a weight, a cost basis, or a holding
period themselves. Every number in every report traces back to a function here
and, through it, to a SQL query Dan can run by hand.

The data_quality block is deliberately prominent. Where a number is missing it
says so and says why, so an agent can report the gap rather than paper over it.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import pandas as pd

import config as config_mod
import db

SCHEMA = db.SCHEMA


class Encoder(json.JSONEncoder):
    """Decimal, date, and NaN all need help on the way to JSON."""

    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, pd.Timestamp):
            return o.date().isoformat()
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        # numpy scalars (bool_, int64, float64) leak out of pandas at every turn
        # and json does not know any of them. .item() unwraps to a native type.
        if hasattr(o, "item"):
            try:
                return o.item()
            except (ValueError, AttributeError):
                pass
        try:
            if pd.isna(o):
                return None
        except (TypeError, ValueError):
            pass
        return super().default(o)


def _f(value) -> float | None:
    """Coerce to a plain float, mapping NaN and None to None."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Base frames
# ---------------------------------------------------------------------------

def snapshot_dates(limit: int = 2) -> list[date]:
    """The most recent holdings snapshot dates, newest first."""
    df = db.query(
        f"SELECT DISTINCT as_of_date FROM {SCHEMA}.holdings "
        f"ORDER BY as_of_date DESC LIMIT %s",
        (limit,),
    )
    return [pd.Timestamp(d).date() for d in df["as_of_date"]]


def holdings_frame(as_of: date) -> pd.DataFrame:
    """Positions on a given date, valued at the latest price wherever possible.

    Cost basis is a historical fact and is stored. Market value is not: it is a
    price times a share count, and the price changes every trading day. Storing a
    market value freezes it at whatever it was when someone typed it in.

    So market value is COMPUTED here whenever a share count and a price both
    exist, and only falls back to the recorded figure when one of them is
    missing. `valuation_method` says which happened for every row, and the
    recorded figure is kept alongside so the two can be compared.

    A repriced position is current as of its price date no matter how long ago
    the share count was recorded, because share counts change far more slowly
    than prices. That is why value_as_of follows the price when one is available.
    """
    return db.query(
        f"""
        WITH latest AS (
            SELECT DISTINCT ON (symbol) symbol, close, price_date
            FROM {SCHEMA}.prices
            ORDER BY symbol, price_date DESC
        )
        SELECT h.account_id, a.tax_type, a.platform, a.account_label,
               h.symbol, s.name, s.security_type, s.sector, s.industry,
               s.asset_class, s.enrich_status,
               h.quantity,
               COALESCE(latest.close, h.price) AS price,
               CASE WHEN h.quantity IS NOT NULL AND latest.close IS NOT NULL
                    THEN round(h.quantity * latest.close, 2)
                    ELSE h.market_value END AS market_value,
               h.cost_basis_total,
               CASE WHEN h.quantity IS NOT NULL AND latest.close IS NOT NULL
                         AND h.cost_basis_total IS NOT NULL
                    THEN round(h.quantity * latest.close - h.cost_basis_total, 2)
                    ELSE h.unrealized_gl END AS unrealized_gl,
               h.source,
               CASE WHEN h.quantity IS NOT NULL AND latest.close IS NOT NULL
                    THEN latest.price_date ELSE h.value_as_of END AS value_as_of,
               CASE WHEN h.quantity IS NOT NULL AND latest.close IS NOT NULL
                    THEN 'live_price' ELSE 'recorded' END AS valuation_method,
               h.market_value AS recorded_market_value,
               latest.price_date AS price_as_of
        FROM {SCHEMA}.holdings h
        JOIN {SCHEMA}.accounts a ON a.account_id = h.account_id
        LEFT JOIN {SCHEMA}.securities s ON s.symbol = h.symbol
        LEFT JOIN latest ON latest.symbol = h.symbol
        WHERE h.as_of_date = %s
        """,
        (as_of,),
    )


def valuation_coverage(h: pd.DataFrame) -> dict:
    """How much of the book carries a live price versus a frozen figure.

    A position recorded as a dollar amount with no share count can never be
    repriced: there is nothing to multiply. It is frozen at whatever it was worth
    when it was written down, and no amount of price refreshing will move it.
    That is a data problem to fix at the source, not a limitation to work around,
    so it is reported prominently enough to act on.
    """
    if h.empty or "valuation_method" not in h.columns:
        return {"available": False}

    total = float(h["market_value"].astype(float).sum())
    live = h[h["valuation_method"] == "live_price"]
    frozen = h[h["valuation_method"] == "recorded"]
    live_mv = float(live["market_value"].astype(float).sum())

    unpriceable = frozen[frozen["quantity"].isna()]
    stale_priced = frozen[frozen["quantity"].notna()]

    # Where both a live price and a recorded figure exist, disagreement is a
    # signal: either the recorded value went stale or the share count is wrong.
    drift = []
    for _, r in live.iterrows():
        rec = _f(r["recorded_market_value"])
        now = _f(r["market_value"])
        if rec and now and abs(now - rec) / rec > 0.02:
            drift.append({
                "symbol": r["symbol"], "account_id": r["account_id"],
                "recorded": round(rec, 2), "repriced": round(now, 2),
                "change_pct": round((now - rec) / rec, 4),
            })

    return {
        "available": True,
        "live_priced_market_value": round(live_mv, 2),
        "live_priced_share": round(live_mv / total, 4) if total else None,
        "positions_live_priced": int(len(live)),
        "positions_frozen": int(len(frozen)),
        "frozen_market_value": round(float(frozen["market_value"].astype(float).sum()), 2),
        "no_share_count": [
            {"account_id": r["account_id"], "symbol": r["symbol"],
             "market_value": _f(r["market_value"])}
            for _, r in unpriceable.iterrows()
        ],
        "no_price_available": sorted(stale_priced["symbol"].unique().tolist()),
        "repricing_drift": sorted(drift, key=lambda d: -abs(d["change_pct"])),
        "note": (
            "Positions recorded as a dollar amount with no share count cannot be "
            "repriced and are frozen at the value that was written down. Adding a "
            "share count for them makes them live."
        ) if len(unpriceable) else None,
    }


def lots_frame(as_of: date) -> pd.DataFrame:
    return db.query(
        f"""
        SELECT l.lot_id, l.account_id, a.tax_type, l.symbol, l.acquired_date,
               l.quantity, l.cost_basis, l.cost_per_share, l.market_value,
               l.unrealized_gl, l.term, l.source_file, l.value_as_of
        FROM {SCHEMA}.lots l
        JOIN {SCHEMA}.accounts a ON a.account_id = l.account_id
        WHERE l.as_of_date = %s
        """,
        (as_of,),
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def totals(h: pd.DataFrame) -> dict:
    mv = h["market_value"].astype(float).sum()
    basis = h["cost_basis_total"].astype(float).sum(skipna=True)
    covered = h[h["cost_basis_total"].notna()]["market_value"].astype(float).sum()

    return {
        "market_value": round(mv, 2),
        "cost_basis": round(basis, 2) if basis else None,
        # Only meaningful across the positions that actually have a basis, so
        # report the covered market value alongside it.
        "cost_basis_covers_market_value": round(covered, 2),
        "unrealized_gl": round(covered - basis, 2) if basis else None,
        "unrealized_pct": round((covered - basis) / basis, 4) if basis else None,
        "position_count": int(len(h)),
        "account_count": int(h["account_id"].nunique()),
    }


def positions(h: pd.DataFrame) -> list[dict]:
    """Consolidated by symbol across accounts, with per-account detail kept."""
    total_mv = h["market_value"].astype(float).sum()
    out = []

    for symbol, grp in h.groupby("symbol", dropna=False):
        mv = grp["market_value"].astype(float).sum()
        basis = grp["cost_basis_total"].astype(float).sum(skipna=True)
        has_basis = grp["cost_basis_total"].notna().all()
        qty = grp["quantity"].astype(float).sum(skipna=True)
        first = grp.iloc[0]

        out.append({
            "symbol": symbol,
            "name": first["name"],
            "sector": first["sector"],
            "asset_class": first["asset_class"],
            "security_type": first["security_type"],
            "quantity": round(qty, 6) if qty else None,
            "price": _f(first["price"]),
            "market_value": round(mv, 2),
            "weight": round(mv / total_mv, 4) if total_mv else None,
            "cost_basis": round(basis, 2) if has_basis and basis else None,
            "unrealized_gl": round(mv - basis, 2) if has_basis and basis else None,
            "unrealized_pct": round((mv - basis) / basis, 4) if has_basis and basis else None,
            "cost_basis_complete": bool(has_basis),
            "accounts": [
                {
                    "account_id": r["account_id"],
                    "tax_type": r["tax_type"],
                    "market_value": round(float(r["market_value"]), 2),
                    "quantity": _f(r["quantity"]),
                }
                for _, r in grp.iterrows()
            ],
        })

    return sorted(out, key=lambda p: p["market_value"], reverse=True)


def _concentration_stats(weights: list[float]) -> dict:
    """Top-N weights and Herfindahl index for a set of weights.

    HHI is the sum of squared weights: 1.0 is everything in one thing, and 1/N is
    perfectly equal-weighted. Its reciprocal is the "effective" number of
    independent positions, which catches concentration a top-5 list hides.
    """
    ranked = sorted(weights, reverse=True)
    hhi = sum(w * w for w in ranked)
    return {
        "top_1_weight": round(ranked[0], 4) if ranked else None,
        "top_5_weight": round(sum(ranked[:5]), 4),
        "top_10_weight": round(sum(ranked[:10]), 4),
        "hhi": round(hhi, 4),
        "effective_positions": round(1 / hhi, 1) if hhi else None,
    }


def concentration(pos: list[dict], threshold: float, cfg: dict) -> dict:
    """Concentration at two levels: what you hold, and what you are exposed to.

    Symbol level alone is misleading whenever the same underlying exposure is
    held through several tickers. Three separate S&P 500 funds across three
    accounts look like a comfortable spread and are in fact one bet. The
    exposure level collapses them via config.exposure_groups and is the number
    that should drive any decision.
    """
    weights = [p["weight"] for p in pos if p["weight"] is not None]

    by_symbol = _concentration_stats(weights)
    by_symbol["over_threshold"] = [
        {"symbol": p["symbol"], "weight": p["weight"], "market_value": p["market_value"]}
        for p in pos
        if p["weight"] is not None and p["weight"] > threshold
    ]

    grouped: dict[str, dict] = {}
    for p in pos:
        exposure = config_mod.exposure_for(p["symbol"], cfg)
        slot = grouped.setdefault(exposure, {
            "exposure": exposure,
            "market_value": 0.0,
            "weight": 0.0,
            "symbols": [],
            "is_group": exposure in cfg.get("exposure_groups", {}),
        })
        slot["market_value"] += p["market_value"]
        slot["weight"] += p["weight"] or 0.0
        slot["symbols"].append(p["symbol"])

    exposures = sorted(grouped.values(), key=lambda e: e["market_value"], reverse=True)
    for e in exposures:
        e["market_value"] = round(e["market_value"], 2)
        e["weight"] = round(e["weight"], 4)
        e["symbols"] = sorted(set(e["symbols"]))

    by_exposure = _concentration_stats([e["weight"] for e in exposures])
    by_exposure["over_threshold"] = [
        {"exposure": e["exposure"], "weight": e["weight"],
         "market_value": e["market_value"], "symbols": e["symbols"]}
        for e in exposures if e["weight"] > threshold
    ]
    by_exposure["exposures"] = exposures

    return {
        "threshold": threshold,
        "by_symbol": by_symbol,
        "by_exposure": by_exposure,
        # Exposure grouping only collapses tickers named in config.yaml. It does
        # NOT look through a fund to its underlying holdings, so overlap between
        # an S&P 500 fund and a Nasdaq 100 fund in names like NVDA and AAPL is
        # still invisible. Real single-name exposure is higher than reported.
        "look_through_limitation": (
            "Exposure groups collapse configured tickers only. Overlapping "
            "holdings inside different funds are not looked through, so true "
            "single-name exposure is understated."
        ),
        # Backwards compatible with the flat shape the first version emitted.
        "top_5_weight": by_symbol["top_5_weight"],
        "top_10_weight": by_symbol["top_10_weight"],
        "hhi": by_symbol["hhi"],
        "effective_positions": by_symbol["effective_positions"],
        "over_threshold": by_symbol["over_threshold"],
    }


def allocation(h: pd.DataFrame, targets: dict) -> dict:
    """Actual asset class mix and drift from target."""
    total_mv = h["market_value"].astype(float).sum()
    by_class = (
        h.assign(asset_class=h["asset_class"].fillna("unknown"))
        .groupby("asset_class")["market_value"]
        .apply(lambda s: s.astype(float).sum())
    )

    rows = []
    for asset_class, mv in by_class.items():
        actual = mv / total_mv if total_mv else 0.0
        target = float(targets.get(asset_class)) if targets.get(asset_class) is not None else None
        rows.append({
            "asset_class": asset_class,
            "market_value": round(mv, 2),
            "actual_weight": round(actual, 4),
            "target_weight": target,
            "drift": round(actual - target, 4) if target is not None else None,
            "drift_dollars": round((actual - target) * total_mv, 2) if target is not None else None,
        })

    for asset_class, target in (targets or {}).items():
        if asset_class not in by_class.index:
            rows.append({
                "asset_class": asset_class,
                "market_value": 0.0,
                "actual_weight": 0.0,
                "target_weight": float(target),
                "drift": round(-float(target), 4),
                "drift_dollars": round(-float(target) * total_mv, 2),
            })

    # Asset class comes from enrichment. Before enrich.py has run everything is
    # 'unknown', and reporting drift off that would tell Dan he is 70 points
    # underweight US equity when really nothing has been classified yet.
    classified = float(by_class.drop("unknown", errors="ignore").sum())
    coverage = classified / float(total_mv) if total_mv else 0.0

    return {
        "has_targets": bool(targets),
        "classified_pct": round(coverage, 4),
        "drift_is_meaningful": bool(targets and coverage >= 0.95),
        "drift_caveat": None if coverage >= 0.95 else (
            f"Only {coverage:.0%} of market value has a known asset class, so drift "
            "against target is not meaningful yet. Run enrich.py, and pin anything "
            "it cannot classify in config.yaml under asset_class_overrides."
        ),
        "by_asset_class": sorted(rows, key=lambda r: r["market_value"], reverse=True),
    }


def sector_exposure(h: pd.DataFrame) -> list[dict]:
    """Sector mix of the equity sleeve.

    Agent 2 compares this against benchmark weights. Agent 1 just reports it.
    Note that ETF and fund holdings carry no sector of their own, so they land in
    'Fund / Unclassified' rather than being spread across sectors they hold.
    """
    total_mv = h["market_value"].astype(float).sum()
    labeled = h.assign(sector=h["sector"].fillna("Fund / Unclassified"))

    rows = [
        {
            "sector": sector,
            "market_value": round(mv, 2),
            "weight": round(mv / total_mv, 4) if total_mv else None,
        }
        for sector, mv in labeled.groupby("sector")["market_value"]
        .apply(lambda s: s.astype(float).sum()).items()
    ]
    return sorted(rows, key=lambda r: r["market_value"], reverse=True)


def contributors(current: date, prior: date | None) -> dict:
    """Decompose the change in market value since the prior snapshot.

    Exact decomposition, no approximation:

        MV_now - MV_prior = qty_prior * (p_now - p_prior)   <- price effect
                          + (qty_now - qty_prior) * p_now   <- flow effect

    Keeping those separate matters. A position that grew because Dan bought more
    is a different fact from one that grew because it went up, and lumping them
    together is how portfolio reports become misleading.
    """
    if prior is None:
        return {"available": False, "reason": "only one snapshot loaded so far"}

    now = db.query(
        f"SELECT symbol, sum(quantity) qty, sum(market_value) mv, "
        f"max(price) px FROM {SCHEMA}.holdings WHERE as_of_date = %s GROUP BY symbol",
        (current,),
    ).set_index("symbol")

    was = db.query(
        f"SELECT symbol, sum(quantity) qty, sum(market_value) mv, "
        f"max(price) px FROM {SCHEMA}.holdings WHERE as_of_date = %s GROUP BY symbol",
        (prior,),
    ).set_index("symbol")

    rows = []
    for symbol in sorted(set(now.index) | set(was.index)):
        qty_now = _f(now["qty"].get(symbol)) or 0.0
        qty_was = _f(was["qty"].get(symbol)) or 0.0
        px_now = _f(now["px"].get(symbol))
        px_was = _f(was["px"].get(symbol))
        mv_now = _f(now["mv"].get(symbol)) or 0.0
        mv_was = _f(was["mv"].get(symbol)) or 0.0

        status = "held"
        if symbol not in was.index:
            status = "new"
        elif symbol not in now.index or qty_now == 0:
            status = "exited"

        if status == "new":
            # No prior price, so the period's price move inside this position is
            # unknowable from snapshots alone. Attributing the whole thing to
            # flow is the honest read: money came in, and we cannot claim any of
            # it was appreciation.
            price_effect, flow_effect = 0.0, round(mv_now, 2)
        elif status == "exited":
            price_effect, flow_effect = 0.0, round(-mv_was, 2)
        elif px_now is None or px_was is None:
            price_effect = flow_effect = None
        else:
            price_effect = round(qty_was * (px_now - px_was), 2)
            flow_effect = round((qty_now - qty_was) * px_now, 2)

        rows.append({
            "symbol": symbol,
            "status": status,
            "market_value_change": round(mv_now - mv_was, 2),
            "price_effect": price_effect,
            "flow_effect": flow_effect,
            "price_change_pct": round((px_now - px_was) / px_was, 4)
            if px_now is not None and px_was else None,
        })

    ranked = [r for r in rows if r["price_effect"] is not None]
    ranked.sort(key=lambda r: r["price_effect"], reverse=True)

    total_change = round(sum(r["market_value_change"] for r in rows), 2)
    total_price = round(sum(r["price_effect"] or 0 for r in rows), 2)
    total_flow = round(sum(r["flow_effect"] or 0 for r in rows), 2)

    return {
        "available": True,
        "prior_date": prior,
        "current_date": current,
        "days_elapsed": (current - prior).days,
        "total_change": total_change,
        "total_price_effect": total_price,
        "total_flow_effect": total_flow,
        # Price plus flow must equal the total change. If this goes false, a
        # position is missing a price and the attribution below is incomplete.
        "decomposition_ties": abs(total_price + total_flow - total_change) < 0.01,
        "positions_missing_price": [
            r["symbol"] for r in rows if r["price_effect"] is None
        ],
        "top_contributors": ranked[:5],
        "top_detractors": ranked[-5:][::-1],
        "new_positions": [r["symbol"] for r in rows if r["status"] == "new"],
        "exited_positions": [r["symbol"] for r in rows if r["status"] == "exited"],
    }


def unrealized(lots: pd.DataFrame) -> dict:
    """Unrealized gain/loss split short vs long term."""
    if lots.empty:
        return {"available": False, "reason": "no lot detail loaded"}

    usable = lots[lots["unrealized_gl"].notna()]
    by_term = {}
    for term in ("short", "long", "unknown"):
        sub = usable[usable["term"] == term]
        by_term[term] = {
            "lot_count": int(len(sub)),
            "market_value": round(sub["market_value"].astype(float).sum(), 2),
            "cost_basis": round(sub["cost_basis"].astype(float).sum(), 2),
            "unrealized_gl": round(sub["unrealized_gl"].astype(float).sum(), 2),
        }

    return {
        "available": True,
        "lot_count": int(len(lots)),
        "lots_missing_gl": int(lots["unrealized_gl"].isna().sum()),
        "by_term": by_term,
        "total_unrealized_gl": round(usable["unrealized_gl"].astype(float).sum(), 2),
    }


def holding_period_watch(lots: pd.DataFrame, as_of: date, window_days: int) -> list[dict]:
    """Short-term lots that cross into long-term treatment soon.

    Selling one day early turns a long-term rate into an ordinary-income rate on
    the whole gain, which is an expensive way to be impatient. Worth a heads up.
    """
    if lots.empty:
        return []

    rows = []
    for _, lot in lots.iterrows():
        if lot["term"] != "short" or pd.isna(lot["acquired_date"]):
            continue
        acquired = pd.Timestamp(lot["acquired_date"]).date()
        crosses = acquired + timedelta(days=366)
        days_out = (crosses - as_of).days
        if 0 <= days_out <= window_days:
            rows.append({
                "symbol": lot["symbol"],
                "account_id": lot["account_id"],
                "tax_type": lot["tax_type"],
                "acquired_date": acquired,
                "long_term_date": crosses,
                "days_until_long_term": days_out,
                "quantity": _f(lot["quantity"]),
                "unrealized_gl": _f(lot["unrealized_gl"]),
            })

    return sorted(rows, key=lambda r: r["days_until_long_term"])


def earnings_watch(symbols: list[str], window_days: int) -> list[dict]:
    if not symbols:
        return []
    df = db.query(
        f"""
        SELECT DISTINCT ON (symbol) symbol, next_earnings_date
        FROM {SCHEMA}.fundamentals
        WHERE symbol = ANY(%s) AND next_earnings_date IS NOT NULL
          AND next_earnings_date BETWEEN current_date AND current_date + %s
        ORDER BY symbol, as_of_date DESC
        """,
        (symbols, window_days),
    )
    return [
        {"symbol": r["symbol"], "next_earnings_date": pd.Timestamp(r["next_earnings_date"]).date()}
        for _, r in df.iterrows()
    ]


def valuation(symbols: list[str]) -> list[dict]:
    if not symbols:
        return []
    df = db.query(
        f"""
        SELECT DISTINCT ON (f.symbol) f.symbol, s.sector, f.pe, f.forward_pe,
               f.market_cap, f.div_yield, f.beta, f.revenue_growth,
               f.profit_margin, f.fifty_two_week_high, f.fifty_two_week_low
        FROM {SCHEMA}.fundamentals f
        LEFT JOIN {SCHEMA}.securities s ON s.symbol = f.symbol
        WHERE f.symbol = ANY(%s)
        ORDER BY f.symbol, f.as_of_date DESC
        """,
        (symbols,),
    )
    return [{k: _f(v) if k not in ("symbol", "sector") else v
             for k, v in row.items()} for _, row in df.iterrows()]


def income(h: pd.DataFrame, as_of: date) -> dict:
    """Trailing dividend income and forward yield estimate."""
    trailing = db.query(
        f"""
        SELECT coalesce(sum(amount), 0) AS received, count(*) AS payments
        FROM {SCHEMA}.transactions
        WHERE action IN ('dividend', 'interest') AND trade_date > %s
        """,
        (as_of - timedelta(days=365),),
    )

    yields = db.query(
        f"""
        SELECT DISTINCT ON (symbol) symbol, div_yield
        FROM {SCHEMA}.fundamentals WHERE div_yield IS NOT NULL
        ORDER BY symbol, as_of_date DESC
        """
    ).set_index("symbol")["div_yield"].to_dict()

    forward = 0.0
    covered = 0.0
    for _, row in h.iterrows():
        y = yields.get(row["symbol"])
        if y is None:
            continue
        mv = float(row["market_value"])
        # yfinance reports dividendYield inconsistently: sometimes 0.0182,
        # sometimes 1.82. Anything above 1 is being quoted as a percent.
        rate = float(y) / 100 if float(y) > 1 else float(y)
        forward += mv * rate
        covered += mv

    total_mv = h["market_value"].astype(float).sum()

    return {
        "trailing_12m_received": round(float(trailing.iloc[0]["received"]), 2),
        "trailing_12m_payments": int(trailing.iloc[0]["payments"]),
        "forward_annual_estimate": round(forward, 2),
        "forward_yield_on_market": round(forward / total_mv, 4) if total_mv else None,
        "yield_coverage_pct": round(covered / total_mv, 4) if total_mv else None,
    }


def since_last_run(agent: str, pos: list[dict], t: dict) -> dict:
    """Diff against the last recorded run of this agent.

    This is what makes the report worth opening. Without it every run restates
    the same portfolio and Dan stops reading by month three.
    """
    df = db.query(
        f"SELECT run_at, as_of_date, summary_json FROM {SCHEMA}.report_runs "
        f"WHERE agent = %s ORDER BY run_at DESC LIMIT 1",
        (agent,),
    )
    if df.empty:
        return {"available": False, "reason": "no prior run recorded"}

    prev = df.iloc[0]["summary_json"] or {}
    if isinstance(prev, str):
        prev = json.loads(prev)

    prev_weights = {p["symbol"]: p.get("weight") for p in prev.get("positions", [])}
    prev_total = (prev.get("totals") or {}).get("market_value")

    moves = []
    for p in pos:
        was = prev_weights.get(p["symbol"])
        if was is None or p["weight"] is None:
            continue
        delta = p["weight"] - was
        if abs(delta) >= 0.01:  # a full point of portfolio weight
            moves.append({
                "symbol": p["symbol"],
                "weight_now": p["weight"],
                "weight_prior": round(was, 4),
                "weight_change": round(delta, 4),
            })

    return {
        "available": True,
        "prior_run_at": df.iloc[0]["run_at"],
        "prior_as_of_date": df.iloc[0]["as_of_date"],
        "market_value_prior": prev_total,
        "market_value_change": round(t["market_value"] - prev_total, 2) if prev_total else None,
        "weight_moves": sorted(moves, key=lambda m: abs(m["weight_change"]), reverse=True),
        "added": [p["symbol"] for p in pos if p["symbol"] not in prev_weights],
        "removed": [s for s in prev_weights if s not in {p["symbol"] for p in pos}],
    }


def staleness(h: pd.DataFrame, as_of: date, tolerance_days: int) -> dict:
    """How much of the book is carrying values older than the snapshot.

    A position priced at the prior close is not stale, so a tolerance applies.
    What matters is the account nobody refreshed for three months, whose value is
    being read as if it were current.
    """
    if "value_as_of" not in h.columns:
        return {"available": False, "reason": "value_as_of not populated"}

    total_mv = h["market_value"].astype(float).sum()
    rows, stale_mv, oldest = [], 0.0, None

    for _, r in h.iterrows():
        if pd.isna(r["value_as_of"]):
            continue
        observed = pd.Timestamp(r["value_as_of"]).date()
        age = (as_of - observed).days
        if age <= tolerance_days:
            continue
        mv = float(r["market_value"]) if not pd.isna(r["market_value"]) else 0.0
        stale_mv += mv
        oldest = observed if oldest is None else min(oldest, observed)
        rows.append({
            "account_id": r["account_id"],
            "symbol": r["symbol"],
            "market_value": round(mv, 2),
            "value_as_of": observed,
            "days_old": age,
        })

    return {
        "available": True,
        "tolerance_days": tolerance_days,
        "stale_position_count": len(rows),
        "stale_market_value": round(stale_mv, 2),
        "stale_pct_of_portfolio": round(stale_mv / total_mv, 4) if total_mv else 0.0,
        "oldest_value_as_of": oldest,
        "stale_accounts": sorted({r["account_id"] for r in rows}),
        "positions": sorted(rows, key=lambda r: -r["market_value"]),
    }


def data_quality(h: pd.DataFrame, lots: pd.DataFrame, cfg: dict) -> dict:
    """What the report cannot say, and why.

    An agent that knows exactly where the gaps are can name them. An agent that
    does not will fill them with something plausible.
    """
    no_basis = h[h["cost_basis_total"].isna()]
    unenriched = h[h["enrich_status"].isna() | (h["enrich_status"] != "ok")]
    no_sector = h[h["sector"].isna() & (h["security_type"] == "equity")]

    derived_lots = lots[lots["source_file"] == "derived:fifo"] if not lots.empty else pd.DataFrame()
    accounts_with_lots = set(lots["account_id"]) if not lots.empty else set()
    accounts_all = set(h["account_id"])

    return {
        "positions_missing_cost_basis": sorted(no_basis["symbol"].tolist()),
        "market_value_missing_cost_basis": round(
            no_basis["market_value"].astype(float).sum(), 2
        ),
        "symbols_not_enriched": sorted(unenriched["symbol"].unique().tolist()),
        "equities_missing_sector": sorted(no_sector["symbol"].unique().tolist()),
        "accounts_without_lot_detail": sorted(accounts_all - accounts_with_lots),
        "positions_missing_market_value": sorted(
            h[h["market_value"].isna()]["symbol"].tolist()
        ),
        "derived_lot_count": int(len(derived_lots)),
        "lot_detail_available": bool(len(lots)),
        "targets_configured": bool(cfg.get("targets")),
    }


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def build(agent: str = "investment-analyst") -> dict:
    cfg = config_mod.load_config()
    th = cfg["thresholds"]

    dates = snapshot_dates(2)
    if not dates:
        return {"error": "No holdings loaded. Run load.py first."}

    current = dates[0]
    prior = dates[1] if len(dates) > 1 else None

    h = holdings_frame(current)
    if h.empty:
        return {"error": f"No holdings on {current}."}

    lots = lots_frame(current)
    pos = positions(h)
    t = totals(h)
    symbols = [p["symbol"] for p in pos]

    return {
        "as_of_date": current,
        "prior_snapshot_date": prior,
        "generated_at": datetime.now(),
        "totals": t,
        "positions": pos,
        "by_account": [
            {
                "account_id": aid,
                "tax_type": grp.iloc[0]["tax_type"],
                "platform": grp.iloc[0]["platform"],
                "market_value": round(grp["market_value"].astype(float).sum(), 2),
                "position_count": int(len(grp)),
            }
            for aid, grp in h.groupby("account_id")
        ],
        "concentration": concentration(pos, th["concentration_warn_pct"], cfg),
        "allocation": allocation(h, cfg.get("targets") or {}),
        "sector_exposure": sector_exposure(h),
        "contributors": contributors(current, prior),
        "unrealized": unrealized(lots),
        "income": income(h, current),
        "valuation": valuation(symbols),
        "watchlist": {
            "approaching_long_term": holding_period_watch(
                lots, current, th["long_term_watch_days"]
            ),
            "upcoming_earnings": earnings_watch(symbols, th["earnings_watch_days"]),
        },
        "since_last_run": since_last_run(agent, pos, t),
        "data_quality": {
            **data_quality(h, lots, cfg),
            "staleness": staleness(h, current, th["stale_after_days"]),
            "valuation": valuation_coverage(h),
        },
        "thresholds": th,
    }


def record_run(agent: str, payload: dict, report_path: str | None) -> str:
    """Persist this run's snapshot so the next run can diff against it."""
    run_id = uuid.uuid4().hex[:16]
    # Positions and totals are all the next run needs; storing the whole payload
    # would bloat the table with fundamentals that go stale anyway.
    summary = {
        "totals": payload.get("totals"),
        "positions": [
            {"symbol": p["symbol"], "weight": p["weight"], "market_value": p["market_value"]}
            for p in payload.get("positions", [])
        ],
        "concentration": payload.get("concentration"),
        "allocation": payload.get("allocation"),
    }

    db.upsert("report_runs", [{
        "run_id": run_id,
        "agent": agent,
        "run_at": datetime.now(),
        "as_of_date": payload.get("as_of_date"),
        "report_path": report_path,
        "summary_json": json.dumps(summary, cls=Encoder),
    }], ["run_id"])

    return run_id


def print_summary(p: dict) -> None:
    if "error" in p:
        print(f"\n  {p['error']}\n")
        return

    t = p["totals"]
    print(f"\n  Portfolio as of {p['as_of_date']}")
    print(f"  Market value    ${t['market_value']:,.2f} across "
          f"{t['position_count']} positions in {t['account_count']} accounts")
    if t["unrealized_gl"] is not None:
        print(f"  Unrealized      ${t['unrealized_gl']:,.2f} "
              f"({t['unrealized_pct']:+.1%} on covered basis)")

    print(f"\n  Top positions")
    for pos in p["positions"][:10]:
        gl = f"{pos['unrealized_pct']:+.1%}" if pos["unrealized_pct"] is not None else "  n/a"
        print(f"    {pos['symbol']:<8}{pos['weight']:>7.1%}  ${pos['market_value']:>12,.2f}  {gl:>8}")

    c = p["concentration"]
    bs, be = c["by_symbol"], c["by_exposure"]
    print(f"\n  Concentration   by symbol:   top 5 {bs['top_5_weight']:.1%}, "
          f"HHI {bs['hhi']:.3f} ({bs['effective_positions']} effective)")
    print(f"                  by exposure: top 5 {be['top_5_weight']:.1%}, "
          f"HHI {be['hhi']:.3f} ({be['effective_positions']} effective)")
    for e in be["exposures"][:6]:
        if e["is_group"]:
            print(f"    {e['exposure']:<16}{e['weight']:>7.1%}  ${e['market_value']:>12,.0f}"
                  f"  [{', '.join(e['symbols'])}]")
    for over in be["over_threshold"]:
        print(f"    ! {over['exposure']} at {over['weight']:.1%}, "
              f"over the {c['threshold']:.0%} threshold")

    st = p["data_quality"].get("staleness") or {}
    if st.get("available") and st["stale_position_count"]:
        print(f"\n  Stale values    ${st['stale_market_value']:,.0f} "
              f"({st['stale_pct_of_portfolio']:.0%}) back to {st['oldest_value_as_of']} "
              f"in {', '.join(st['stale_accounts'])}")

    vc = p["data_quality"].get("valuation") or {}
    if vc.get("available"):
        print(f"\n  Valuation      {vc['live_priced_share']:.0%} of value carries a live price "
              f"({vc['positions_live_priced']} positions)")
        if vc["no_share_count"]:
            frozen = sum(x["market_value"] or 0 for x in vc["no_share_count"])
            print(f"                  ${frozen:,.0f} frozen: no share count, cannot reprice")
            for x in vc["no_share_count"][:5]:
                print(f"                    {x['account_id']:<18}{x['symbol']:<8}"
                      f"${(x['market_value'] or 0):>11,.2f}")

    dq = p["data_quality"]
    gaps = []
    if dq["positions_missing_cost_basis"]:
        gaps.append(f"{len(dq['positions_missing_cost_basis'])} positions without cost basis")
    if dq["accounts_without_lot_detail"]:
        gaps.append(f"no lot detail for {', '.join(dq['accounts_without_lot_detail'])}")
    if dq["symbols_not_enriched"]:
        gaps.append(f"{len(dq['symbols_not_enriched'])} symbols not enriched")
    if gaps:
        print(f"\n  Data gaps       {'; '.join(gaps)}")

    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="Compute portfolio metrics.")
    ap.add_argument("--json", action="store_true", help="Emit the full JSON payload")
    ap.add_argument("--agent", default="investment-analyst", help="Agent name for run diffing")
    ap.add_argument("--record", metavar="REPORT_PATH", nargs="?", const="",
                    help="Record this run's snapshot, optionally with the report path")
    args = ap.parse_args()

    payload = build(args.agent)

    if args.json:
        print(json.dumps(payload, cls=Encoder, indent=2, default=str))
    else:
        print_summary(payload)

    if args.record is not None and "error" not in payload:
        run_id = record_run(args.agent, payload, args.record or None)
        if not args.json:
            print(f"  Recorded run {run_id}\n")

    return 1 if "error" in payload else 0


if __name__ == "__main__":
    sys.exit(main())
