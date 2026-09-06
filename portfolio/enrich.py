"""
enrich.py — Attach prices, sector/industry, and fundamentals to held securities.

    python portfolio/enrich.py              refresh anything stale or missing
    python portfolio/enrich.py --full       refresh everything
    python portfolio/enrich.py --prices     prices only, skip fundamentals

Uses yfinance: free, no key, accurate enough for portfolio reporting. Runs
independently of ingestion, which matters. Holdings change slowly and prices
change daily, so this can refresh against last month's snapshot and keep the
report current without a new export.

Every lookup fails soft. A symbol yfinance does not recognize degrades that one
position and gets recorded as enrich_status='not_found' so the report can say
which numbers are missing instead of quietly showing zero.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta

import pandas as pd

import config as config_mod
import db

STALE_AFTER_DAYS = 7

# yfinance quoteType -> our security_type
QUOTE_TYPE_MAP = {
    "EQUITY": "equity",
    "ETF": "etf",
    "MUTUALFUND": "mutual_fund",
    "MONEYMARKET": "money_market",
    "CURRENCY": "cash",
    "INDEX": "other",
    "CRYPTOCURRENCY": "alt",
}

# Name fragments that reliably indicate what a fund actually holds. This is a
# heuristic, not truth: a fund's real composition is not in the export. Anything
# it gets wrong can be pinned in config.yaml under asset_class_overrides.
BOND_HINTS = ("bond", "treasury", "aggregate", "fixed income", "tips", "municipal",
              "corporate debt", "government securities", "income fund")
INTL_HINTS = ("international", "global", "emerging", "ex-us", "ex us", "developed markets",
              "world", "eafe", "europe", "pacific", "china", "japan", "foreign")
CASH_HINTS = ("money market", "cash reserves", "treasury only", "government cash")


def classify_asset_class(info: dict, symbol: str, overrides: dict) -> str:
    """Best-effort asset class for allocation drift reporting.

    A user declaration always wins over inference. The heuristic below is a
    guess; config.asset_class_overrides is a statement of fact.
    """
    if symbol in overrides:
        return overrides[symbol]

    quote_type = (info.get("quoteType") or "").upper()
    name = f"{info.get('longName') or ''} {info.get('shortName') or ''} {info.get('category') or ''}".lower()

    if quote_type in ("MONEYMARKET", "CURRENCY") or any(h in name for h in CASH_HINTS):
        return "cash"
    if quote_type == "CRYPTOCURRENCY":
        return "alt"
    if any(h in name for h in BOND_HINTS):
        return "bond"
    if any(h in name for h in INTL_HINTS):
        return "intl_equity"

    country = (info.get("country") or "").strip()
    if country and country != "United States":
        return "intl_equity"

    if quote_type in ("EQUITY", "ETF", "MUTUALFUND"):
        return "us_equity"
    return "unknown"


def held_symbols() -> list[str]:
    """Symbols in the most recent holdings snapshot, plus anything with open lots."""
    df = db.query(f"""
        SELECT DISTINCT symbol FROM {db.SCHEMA}.holdings
        WHERE as_of_date = (SELECT max(as_of_date) FROM {db.SCHEMA}.holdings)
        UNION
        SELECT DISTINCT symbol FROM {db.SCHEMA}.lots
        WHERE as_of_date = (SELECT max(as_of_date) FROM {db.SCHEMA}.lots)
    """)
    return sorted(df["symbol"].dropna().tolist())


def stale_symbols(symbols: list[str], full: bool) -> list[str]:
    if full:
        return symbols
    cutoff = date.today() - timedelta(days=STALE_AFTER_DAYS)
    df = db.query(
        f"SELECT symbol FROM {db.SCHEMA}.securities "
        f"WHERE updated_at::date >= %s AND enrich_status = 'ok'",
        (cutoff,),
    )
    fresh = set(df["symbol"].tolist())
    return [s for s in symbols if s not in fresh]


def _num(value):
    """yfinance returns numpy scalars, None, and occasional NaN. Normalize."""
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


def next_earnings(ticker) -> date | None:
    """Next scheduled earnings date, or None. yfinance is inconsistent here."""
    try:
        cal = ticker.calendar
    except Exception:
        return None

    values = None
    if isinstance(cal, dict):
        values = cal.get("Earnings Date")
    elif isinstance(cal, pd.DataFrame) and "Earnings Date" in cal.index:
        values = cal.loc["Earnings Date"].tolist()

    if not values:
        return None
    if not isinstance(values, (list, tuple)):
        values = [values]

    today = date.today()
    for v in values:
        try:
            d = pd.Timestamp(v).date()
        except Exception:
            continue
        if d >= today:
            return d
    return None


def enrich_securities(symbols: list[str], overrides: dict, cfg: dict) -> tuple[int, int]:
    """Fetch profile + fundamentals for each symbol. Returns (ok, failed).

    Fetches using the provider's ticker but stores under the portfolio's symbol,
    so a 401k pool with no public ticker and a crypto position both land in the
    same tables as everything else and every downstream join stays simple.
    """
    import yfinance as yf

    sec_rows, fund_rows = [], []
    today = date.today()
    ok = failed = 0

    for symbol in symbols:
        provider = config_mod.resolve_symbol(symbol, cfg)
        try:
            ticker = yf.Ticker(provider)
            info = ticker.info or {}
        except Exception as exc:
            print(f"    ! {symbol}: {type(exc).__name__}: {exc}")
            info = {}

        # yfinance returns a near-empty dict for symbols it cannot resolve.
        if not info.get("quoteType") and not info.get("longName"):
            failed += 1
            via = f" (as {provider})" if provider != symbol else ""
            print(f"    ? {symbol}{via}: not found")
            sec_rows.append({
                "symbol": symbol, "name": None, "security_type": None,
                "sector": None, "industry": None, "asset_class": "unknown",
                "enrich_status": "not_found", "updated_at": datetime.now(),
            })
            continue

        ok += 1
        quote_type = (info.get("quoteType") or "").upper()
        sec_rows.append({
            "symbol": symbol,
            "name": info.get("longName") or info.get("shortName"),
            "security_type": QUOTE_TYPE_MAP.get(quote_type, "other"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "asset_class": classify_asset_class(info, symbol, overrides),
            "enrich_status": "ok",
            "updated_at": datetime.now(),
        })

        fund_rows.append({
            "symbol": symbol,
            "as_of_date": today,
            "pe": _num(info.get("trailingPE")),
            "forward_pe": _num(info.get("forwardPE")),
            "market_cap": _num(info.get("marketCap")),
            "div_yield": _num(info.get("dividendYield")),
            "beta": _num(info.get("beta")),
            "revenue_growth": _num(info.get("revenueGrowth")),
            "profit_margin": _num(info.get("profitMargins")),
            "fifty_two_week_high": _num(info.get("fiftyTwoWeekHigh")),
            "fifty_two_week_low": _num(info.get("fiftyTwoWeekLow")),
            "next_earnings_date": next_earnings(ticker),
        })

    db.upsert("securities", sec_rows, ["symbol"])
    db.upsert("fundamentals", fund_rows, ["symbol", "as_of_date"])
    return ok, failed


def refresh_prices(symbols: list[str], cfg: dict, days: int = 400) -> int:
    """Pull daily closes. 400 days covers a full year of trading plus a buffer.

    Downloads by provider ticker and writes back under the portfolio symbol. Two
    portfolio symbols may resolve to the same provider ticker (a 401k pool priced
    off its retail equivalent, say), so one download can fan out to several rows.
    """
    import yfinance as yf

    if not symbols:
        return 0

    # provider ticker -> the portfolio symbols that should receive its prices
    fanout: dict[str, list[str]] = {}
    for sym in symbols:
        fanout.setdefault(config_mod.resolve_symbol(sym, cfg), []).append(sym)

    providers = sorted(fanout)

    try:
        data = yf.download(
            providers, period=f"{days}d", interval="1d",
            auto_adjust=False, progress=False, group_by="column", threads=True,
        )
    except Exception as exc:
        print(f"    ! price download failed: {type(exc).__name__}: {exc}")
        return 0

    if data is None or data.empty:
        print("    ! price download returned nothing")
        return 0

    close = data["Close"] if "Close" in data else data
    if isinstance(close, pd.Series):
        close = close.to_frame(providers[0])

    rows = []
    for provider in close.columns:
        series = close[provider].dropna()
        for portfolio_symbol in fanout.get(str(provider), [str(provider)]):
            for ts, value in series.items():
                rows.append({
                    "symbol": portfolio_symbol,
                    "price_date": pd.Timestamp(ts).date(),
                    "close": float(value),
                })

    return db.upsert("prices", rows, ["symbol", "price_date"])


def backfill_holding_values() -> int:
    """Price positions that arrived with a share count but no market value.

    Robinhood reports crypto and fractional shares as a quantity with no dollar
    figure. Rather than drop those positions or guess, they load unpriced and get
    valued here off the latest close.
    """
    sql = f"""
        WITH latest AS (
            SELECT DISTINCT ON (symbol) symbol, close, price_date
            FROM {db.SCHEMA}.prices
            ORDER BY symbol, price_date DESC
        )
        UPDATE {db.SCHEMA}.holdings h
        SET price         = latest.close,
            market_value  = round(h.quantity * latest.close, 2),
            unrealized_gl = CASE
                WHEN h.cost_basis_total IS NOT NULL
                THEN round(h.quantity * latest.close - h.cost_basis_total, 2)
                ELSE h.unrealized_gl END,
            -- The value is now as of the price date, not the old snapshot date.
            value_as_of   = latest.price_date
        FROM latest
        WHERE h.symbol = latest.symbol
          AND h.market_value IS NULL
          AND h.quantity IS NOT NULL
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.rowcount


def backfill_lot_values() -> int:
    """Fill market_value and unrealized_gl on derived lots.

    Lots reconstructed from transaction history have basis but no market value,
    because the transaction file does not know today's price. Now that prices are
    loaded, close the loop.
    """
    sql = f"""
        WITH latest AS (
            SELECT DISTINCT ON (symbol) symbol, close
            FROM {db.SCHEMA}.prices
            ORDER BY symbol, price_date DESC
        )
        UPDATE {db.SCHEMA}.lots l
        SET market_value  = round(l.quantity * latest.close, 2),
            unrealized_gl = round(l.quantity * latest.close - l.cost_basis, 2)
        FROM latest
        WHERE l.symbol = latest.symbol
          AND l.market_value IS NULL
          AND l.quantity IS NOT NULL
          AND l.cost_basis IS NOT NULL
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.rowcount


def main() -> int:
    ap = argparse.ArgumentParser(description="Enrich held securities with market data.")
    ap.add_argument("--full", action="store_true", help="Refresh every symbol, not just stale ones")
    ap.add_argument("--prices", action="store_true", help="Prices only, skip profile and fundamentals")
    args = ap.parse_args()

    db.init_schema()
    cfg = config_mod.load_config()
    overrides = cfg.get("asset_class_overrides") or {}

    symbols = held_symbols()
    if not symbols:
        print("\n  No holdings loaded yet. Run load.py first.\n")
        return 0

    print(f"\n  {len(symbols)} held symbol(s)")

    if not args.prices:
        targets = stale_symbols(symbols, args.full)
        if targets:
            print(f"  Enriching {len(targets)} symbol(s)")
            ok, failed = enrich_securities(targets, overrides, cfg)
            print(f"  Profile and fundamentals: {ok} ok, {failed} not found")
        else:
            print(f"  All profiles fresh (updated within {STALE_AFTER_DAYS} days)")

    n = refresh_prices(symbols, cfg)
    print(f"  Loaded {n} daily price row(s)")

    priced = backfill_holding_values()
    if priced:
        print(f"  Priced {priced} position(s) that had quantity but no value")

    filled = backfill_lot_values()
    if filled:
        print(f"  Filled market value on {filled} derived lot(s)")

    unresolved = db.query(
        f"SELECT symbol FROM {db.SCHEMA}.securities "
        f"WHERE enrich_status = 'not_found' AND symbol = ANY(%s)",
        (symbols,),
    )
    if not unresolved.empty:
        print(
            "\n  These symbols could not be resolved and will show as gaps in the "
            "report:\n    " + ", ".join(unresolved["symbol"].tolist())
        )

    print("\n  Next: python portfolio/metrics.py --json\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
