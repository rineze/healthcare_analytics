"""
market_context.py — Ground truth for the market analyst.

Research is the agent's job. Knowing what is actually held is not, and must not
be recalled or assumed. This module answers "what am I researching, and how much
does it matter" so the agent spends its judgment on analysis rather than on
reconstructing the portfolio.

The central split is fund versus single company, because they need completely
different treatment. A single company can be researched: it has filings,
guidance, a competitive position. A broad fund cannot be researched the same way;
what matters about it is the index it tracks and that index's own composition,
which the holder inherits whether or not they chose it.

No new math. Everything numeric comes from metrics.py.

    python portfolio/market_context.py
    python portfolio/market_context.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime

import pandas as pd

import config as config_mod
import db
import metrics
from metrics import Encoder, _f

SCHEMA = db.SCHEMA

# Security types that represent a pooled vehicle rather than one business.
FUND_TYPES = {"etf", "mutual_fund", "money_market"}

# Asset classes whose holder is exposed to a market rather than a company.
NON_RESEARCHABLE_CLASSES = {"cash", "bond"}

TAX_ADVANTAGED = {"traditional_ira", "roth_ira", "401k", "roth_401k", "hsa", "529"}


def _text(value) -> str:
    """Lowercased string from a pandas cell, treating null as empty.

    `value or ""` is wrong here: pandas missing values come back as float NaN,
    and NaN is truthy in Python, so the fallback never fires and a float reaches
    .lower().
    """
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip().lower()


def _classify(row) -> str:
    """fund | company | cash_like | unknown.

    Errs toward 'unknown' rather than guessing. An agent told a holding is a
    company will go research a company, and being wrong about that wastes the
    research and produces confident nonsense about a fund.
    """
    stype = _text(row.get("security_type"))
    aclass = _text(row.get("asset_class"))

    if stype in FUND_TYPES or aclass in NON_RESEARCHABLE_CLASSES:
        return "cash_like" if aclass in ("cash",) else "fund"
    if stype == "equity":
        return "company"
    if stype in ("option", "other", "alt") or aclass == "alt":
        return "other"
    return "unknown"


def build(as_of: date | None = None) -> dict:
    cfg = config_mod.load_config()
    dates = metrics.snapshot_dates(1)
    if not dates:
        return {"error": "No holdings loaded. Run load.py first."}
    as_of = as_of or dates[0]

    h = metrics.holdings_frame(as_of)
    if h.empty:
        return {"error": f"No holdings on {as_of}."}

    pos = metrics.positions(h)
    total_mv = h["market_value"].astype(float).sum()

    # Per-symbol tax placement, which decides whether research has any decision
    # attached to it. A position held only in a Roth cannot be tax-loss harvested
    # and rebalancing it costs nothing, so the stakes of research differ.
    placement: dict[str, dict] = {}
    for _, r in h.iterrows():
        slot = placement.setdefault(r["symbol"], {"taxable": 0.0, "tax_advantaged": 0.0})
        mv = _f(r["market_value"]) or 0.0
        if r["tax_type"] in TAX_ADVANTAGED:
            slot["tax_advantaged"] += mv
        else:
            slot["taxable"] += mv

    by_kind: dict[str, list] = {}
    for p in pos:
        src = h[h["symbol"] == p["symbol"]].iloc[0]
        kind = _classify(src)
        place = placement.get(p["symbol"], {})
        by_kind.setdefault(kind, []).append({
            "symbol": p["symbol"],
            "name": p["name"],
            "weight": p["weight"],
            "market_value": p["market_value"],
            "sector": p["sector"],
            "asset_class": p["asset_class"],
            "security_type": p["security_type"],
            "unrealized_pct": p["unrealized_pct"],
            "exposure_group": config_mod.exposure_for(p["symbol"], cfg),
            "market_value_taxable": round(place.get("taxable", 0.0), 2),
            "market_value_tax_advantaged": round(place.get("tax_advantaged", 0.0), 2),
        })

    for k in by_kind:
        by_kind[k].sort(key=lambda x: -x["market_value"])

    companies = by_kind.get("company", [])
    funds = by_kind.get("fund", [])
    unknown = by_kind.get("unknown", [])

    conc = metrics.concentration(pos, cfg["thresholds"]["concentration_warn_pct"], cfg)

    fund_mv = sum(f["market_value"] for f in funds)
    company_mv = sum(c["market_value"] for c in companies)

    return {
        "as_of": as_of,
        "generated_at": datetime.now(),
        "total_market_value": round(float(total_mv), 2),

        "research_targets": {
            "companies": companies,
            "company_count": len(companies),
            "company_market_value": round(company_mv, 2),
            "company_share_of_portfolio": round(company_mv / total_mv, 4) if total_mv else None,
        },

        "index_exposure": {
            "funds": funds,
            "fund_market_value": round(fund_mv, 2),
            "fund_share_of_portfolio": round(fund_mv / total_mv, 4) if total_mv else None,
            "by_exposure_group": conc["by_exposure"]["exposures"],
        },

        "cash_like": by_kind.get("cash_like", []),
        "other": by_kind.get("other", []),
        "unclassified": unknown,

        "concentration": {
            "by_symbol": conc["by_symbol"],
            "by_exposure": {k: v for k, v in conc["by_exposure"].items() if k != "exposures"},
            "look_through_limitation": conc["look_through_limitation"],
        },

        "upcoming_earnings": metrics.earnings_watch(
            [c["symbol"] for c in companies], cfg["thresholds"]["earnings_watch_days"]
        ),

        "coverage": {
            "classified_share": round(
                1 - (sum(u["market_value"] for u in unknown) / total_mv), 4
            ) if total_mv else None,
            "unclassified_market_value": round(sum(u["market_value"] for u in unknown), 2),
            "warning": (
                "Some holdings could not be classified as fund or company, which "
                "usually means enrichment has not run for them. Treat any count or "
                "share below as a floor."
            ) if unknown else None,
        },
    }


def print_summary(p: dict) -> None:
    if "error" in p:
        print(f"\n  {p['error']}\n")
        return

    rt, ix = p["research_targets"], p["index_exposure"]
    print(f"\n  Market context as of {p['as_of']}")
    print(f"  Total ${p['total_market_value']:,.2f}\n")

    print(f"  Researchable companies: {rt['company_count']} "
          f"(${rt['company_market_value']:,.2f}, "
          f"{rt['company_share_of_portfolio']:.1%} of portfolio)"
          if rt["company_share_of_portfolio"] is not None else "")
    for c in rt["companies"]:
        tax = f" taxable ${c['market_value_taxable']:,.0f}" if c["market_value_taxable"] else ""
        print(f"    {c['symbol']:<8}{c['weight']:>7.1%}  ${c['market_value']:>11,.2f}{tax}")

    print(f"\n  Fund / index exposure: ${ix['fund_market_value']:,.2f} "
          f"({ix['fund_share_of_portfolio']:.1%})")
    for e in ix["by_exposure_group"][:6]:
        if e["is_group"]:
            print(f"    {e['exposure']:<16}{e['weight']:>7.1%}  {', '.join(e['symbols'])}")

    if p["cash_like"]:
        cash = sum(c["market_value"] for c in p["cash_like"])
        print(f"\n  Cash-like: ${cash:,.2f}")

    if p["unclassified"]:
        print(f"\n  Unclassified: {[u['symbol'] for u in p['unclassified']]}")
        print(f"    {p['coverage']['warning']}")

    print(f"\n  {p['concentration']['look_through_limitation']}\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Ground truth for market research.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--as-of")
    args = ap.parse_args()

    as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date() if args.as_of else None
    p = build(as_of)

    if args.json:
        print(json.dumps(p, cls=Encoder, indent=2, default=str))
    else:
        print_summary(p)
    return 1 if "error" in p else 0


if __name__ == "__main__":
    sys.exit(main())
