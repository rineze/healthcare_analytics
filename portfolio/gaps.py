"""
gaps.py — What the system does not know, ranked by whether it is worth asking.

    python portfolio/gaps.py            human-readable
    python portfolio/gaps.py --json     the payload the inventory agent reads
    python portfolio/gaps.py --notify   push a nudge, ONLY if one is warranted

Detection was never the hard part: the system already knows what is missing.
Prioritisation is the hard part. A prompt that surfaces every gap trains its
reader to ignore all of them, so most of this module is about deciding what NOT
to ask.

Two kinds of gap are tracked and they behave differently.

OVERDUE is about time, and only for accounts whose holdings change on a known
rhythm. Payroll contributions land every pay period, so such an account is
reliably out of date a known number of days after it was last confirmed. An
account that only changes when its owner trades has no such rhythm, and nudging
it on a calendar is pure noise: silence there is correct, not a failure.

MISSING is about facts. Every missing cost basis falls into one of four classes
and only one of them is a question worth asking:

  never_ask   Basis in a tax-advantaged account. Gains there are never taxed, so
              the figure is permanently irrelevant. Asking would feel diligent
              and accomplish nothing.
  derivable   A money market at a $1.00 NAV has basis equal to value by
              construction. State it and invite correction; do not ask.
  ask         Taxable, material, and known only to the holder.
  skip        Taxable but too small to be worth the attention the question costs.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime

import pandas as pd

import config as config_mod
import db
from metrics import Encoder, _f

SCHEMA = db.SCHEMA

TAX_ADVANTAGED = {"traditional_ira", "roth_ira", "401k", "roth_401k", "hsa", "529"}

# Below this, the question costs more attention than the answer is worth.
MATERIALITY_FLOOR_USD = 250.0

# Symbols whose basis equals their value by construction rather than by luck.
NAV_ONE_TYPES = {"money_market"}


# ---------------------------------------------------------------------------
# Overdue: accounts whose holdings have moved since they were last confirmed
# ---------------------------------------------------------------------------

def overdue_accounts(cfg: dict, as_of: date | None = None) -> list[dict]:
    """Accounts whose expected rhythm says they have changed since last confirmed.

    Reports periods elapsed, not days elapsed. "Two contributions have landed"
    is actionable in a way "18 days" is not, and it is the difference between a
    nudge that says something happened and one that says a date passed.
    """
    as_of = as_of or date.today()

    last_seen = db.query(
        f"""
        SELECT h.account_id, max(h.value_as_of) AS last_confirmed,
               count(*) AS position_count,
               sum(h.market_value) AS market_value
        FROM {SCHEMA}.holdings h
        WHERE h.as_of_date = (SELECT max(as_of_date) FROM {SCHEMA}.holdings)
        GROUP BY h.account_id
        """
    )
    if last_seen.empty:
        return []

    by_account = {a["account_id"]: a for a in cfg["accounts"]}
    rows = []

    for _, r in last_seen.iterrows():
        acct = by_account.get(r["account_id"])
        if acct is None or pd.isna(r["last_confirmed"]):
            continue

        period = config_mod.cadence_days(acct)
        confirmed = pd.Timestamp(r["last_confirmed"]).date()
        days = (as_of - confirmed).days

        if period is None:
            # No rhythm to be overdue against. Silence here is the right answer.
            continue

        periods = days // period
        if periods < 1:
            continue

        rows.append({
            "account_id": r["account_id"],
            "label": acct.get("label"),
            "tax_type": acct["tax_type"],
            "cadence": acct.get("update_cadence"),
            "last_confirmed": confirmed,
            "days_since": days,
            "periods_elapsed": int(periods),
            "next_period_in_days": period - (days % period),
            "market_value": _f(r["market_value"]),
            "position_count": int(r["position_count"]),
        })

    return sorted(rows, key=lambda x: -x["periods_elapsed"])


# ---------------------------------------------------------------------------
# Missing: cost basis, classified by whether the question is worth asking
# ---------------------------------------------------------------------------

def _classify(tax_type: str, security_type: str | None, market_value: float | None) -> tuple[str, str]:
    """Return (class, why). The 'why' is what makes the ranking auditable."""
    if tax_type in TAX_ADVANTAGED:
        return "never_ask", (
            "Gains in this account type are never taxed, so cost basis is "
            "permanently irrelevant. This is not a gap to close."
        )
    if (security_type or "").lower() in NAV_ONE_TYPES:
        return "derivable", (
            "Money market at a $1.00 NAV, so basis equals value by construction. "
            "State it and invite correction rather than asking."
        )
    if market_value is None:
        return "ask", "Taxable, and neither basis nor market value is known."
    if market_value < MATERIALITY_FLOOR_USD:
        return "skip", (
            f"Taxable but worth less than ${MATERIALITY_FLOOR_USD:,.0f}. The "
            "question costs more attention than the answer is worth."
        )
    return "ask", "Taxable and material. Only the holder knows this."


def missing_basis(cfg: dict) -> dict:
    """Positions with no cost basis, sorted into what to ask and what to leave."""
    h = db.query(
        f"""
        SELECT h.account_id, a.tax_type, h.symbol, h.market_value, h.quantity,
               s.security_type
        FROM {SCHEMA}.holdings h
        JOIN {SCHEMA}.accounts a ON a.account_id = h.account_id
        LEFT JOIN {SCHEMA}.securities s ON s.symbol = h.symbol
        WHERE h.as_of_date = (SELECT max(as_of_date) FROM {SCHEMA}.holdings)
          AND h.cost_basis_total IS NULL
        """
    )

    buckets: dict[str, list] = {"ask": [], "derivable": [], "skip": [], "never_ask": []}
    for _, r in h.iterrows():
        mv = _f(r["market_value"])
        klass, why = _classify(r["tax_type"], r["security_type"], mv)
        buckets[klass].append({
            "account_id": r["account_id"],
            "symbol": r["symbol"],
            "tax_type": r["tax_type"],
            "market_value": mv,
            "quantity": _f(r["quantity"]),
            "why": why,
        })

    for k in buckets:
        buckets[k].sort(key=lambda x: -(x["market_value"] or 0))

    return {
        "total_positions_missing_basis": int(len(h)),
        "worth_asking": len(buckets["ask"]),
        "ask": buckets["ask"],
        "derivable": buckets["derivable"],
        "skip": buckets["skip"],
        "never_ask": buckets["never_ask"],
        "filter_note": (
            f"{len(h)} positions have no cost basis; {len(buckets['ask'])} are "
            f"worth a question. {len(buckets['never_ask'])} sit in tax-advantaged "
            "accounts where basis can never matter."
        ),
    }


# ---------------------------------------------------------------------------
# Assembly and the nudge
# ---------------------------------------------------------------------------

def build(as_of: date | None = None) -> dict:
    cfg = config_mod.load_config()
    overdue = overdue_accounts(cfg, as_of)
    basis = missing_basis(cfg)

    return {
        "generated_at": datetime.now(),
        "as_of": as_of or date.today(),
        "overdue_accounts": overdue,
        "missing_basis": basis,
        "nudge_warranted": bool(overdue or basis["worth_asking"]),
    }


def nudge_text(g: dict) -> str | None:
    """The message, or None when there is genuinely nothing worth sending.

    Returning None is the most important behaviour here. A nudge that arrives on
    a schedule regardless of whether anything happened is one the reader learns
    to dismiss, and then the one that mattered gets dismissed too.
    """
    if not g["nudge_warranted"]:
        return None

    lines = []

    for a in g["overdue_accounts"]:
        n = a["periods_elapsed"]
        what = "contribution" if a["cadence"] in ("biweekly", "semimonthly", "weekly") else "update"
        lines.append(
            f"{a['label'] or a['account_id']}: {n} {what}{'s' if n > 1 else ''} "
            f"since you last confirmed on {a['last_confirmed']}. "
            f"Share count has moved."
        )

    ask = g["missing_basis"]["ask"]
    if ask:
        named = ", ".join(
            f"{x['symbol']}" + (f" (${x['market_value']:,.0f})" if x["market_value"] else "")
            for x in ask[:4]
        )
        lines.append(f"Still no cost basis for: {named}.")

    return "\n\n".join(lines) if lines else None


def print_summary(g: dict) -> None:
    od, mb = g["overdue_accounts"], g["missing_basis"]

    print(f"\n  Gap check as of {g['as_of']}\n")

    if od:
        print("  OVERDUE (holdings have changed since last confirmed)")
        for a in od:
            print(f"    {a['account_id']:<18}{a['periods_elapsed']} x {a['cadence']:<12}"
                  f"last confirmed {a['last_confirmed']} ({a['days_since']}d)")
    else:
        print("  OVERDUE   none. Accounts on a known rhythm are current;")
        print("            accounts without one are never overdue on time alone.")

    print(f"\n  MISSING COST BASIS   {mb['filter_note']}")
    if mb["ask"]:
        print("\n    worth asking:")
        for x in mb["ask"]:
            mv = f"${x['market_value']:,.2f}" if x["market_value"] else "value unknown"
            print(f"      {x['account_id']:<18}{x['symbol']:<9}{mv}")
    for label in ("derivable", "skip", "never_ask"):
        if mb[label]:
            syms = ", ".join(x["symbol"] for x in mb[label])
            print(f"    {label:<12}{syms}")
            print(f"      -> {mb[label][0]['why']}")

    nudge = nudge_text(g)
    print(f"\n  NUDGE: {'would send' if nudge else 'nothing worth sending'}")
    if nudge:
        for line in nudge.split("\n\n"):
            print(f"    {line}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="What the system does not know.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--notify", action="store_true",
                    help="Push a nudge, only if one is warranted. Silent otherwise.")
    ap.add_argument("--as-of", help="Evaluate as of this date (YYYY-MM-DD)")
    args = ap.parse_args()

    as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date() if args.as_of else None
    g = build(as_of)

    if args.notify:
        text = nudge_text(g)
        if text is None:
            print("Nothing worth sending.")
            return 0
        import notify
        try:
            notify.get_channel().send_text("Portfolio check-in\n\n" + text)
            print("Nudge sent.")
        except notify.NotifyError as exc:
            print(f"Could not send: {exc}")
            return 1
        return 0

    if args.json:
        print(json.dumps(g, cls=Encoder, indent=2, default=str))
    else:
        print_summary(g)
    return 0


if __name__ == "__main__":
    sys.exit(main())
