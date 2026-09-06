"""
tax_metrics.py — Placeholder for the tax agent's math. NOT IMPLEMENTED YET.

Agent 3 is deliberately not built in this pass. The reason is sequencing, not
laziness: whether these functions can produce anything trustworthy depends
entirely on the quality of the lot data, and that is unknown until real exports
have been loaded and inspected. Specifically:

  - Fidelity lot detail should be clean and complete.
  - Robinhood lots are derived by FIFO replay in derive_lots.py, and derive_lots
    flags them unreliable when the history is short or a split is involved.
  - Empower positions typically carry no basis at all.

Building a wash-sale engine on top of lot data that turns out to be 60% reliable
would produce advice that is confidently wrong about money, which is the worst
possible failure mode for this project.

Everything these functions need is already in the schema. Fill them in once
`python portfolio/metrics.py` shows lot_detail_available true and the
data_quality block is clean.

Each signature and its algorithm is written out below so this is a fill-in-the-
blanks job rather than a design problem.
"""

from __future__ import annotations

from datetime import date


class NotBuiltYet(NotImplementedError):
    """Raised by the agent-3 stubs. Not an error, just scope."""


def tlh_candidates(as_of: date, min_loss: float) -> list[dict]:
    """Tax-loss harvesting candidates.

    Algorithm:
      1. Pull lots from taxable accounts only (tax_type = 'taxable'). Harvesting
         inside an IRA or 401k does nothing, and including them is the single
         most common way these tools mislead people.
      2. Keep lots where unrealized_gl <= -min_loss.
      3. Group by symbol, sum the harvestable loss, and note the term split:
         short-term losses offset short-term gains first, so they are worth more
         to someone with short-term gains to offset.
      4. Cross-reference wash_sale_check for each candidate before presenting it.
      5. Rank by loss size, but surface the wash-sale-blocked ones separately
         rather than dropping them silently.
    """
    raise NotBuiltYet("tlh_candidates is agent 3 scope. See module docstring.")


def wash_sale_check(symbol: str, sale_date: date) -> dict:
    """Whether selling `symbol` on `sale_date` would trigger a wash sale.

    This is the function the whole unified database exists for. The rule catches
    a purchase of substantially identical stock within 30 days BEFORE or AFTER
    the sale, and it applies across every account you own, including IRAs. The
    classic and expensive mistake is harvesting a loss in a taxable brokerage
    account while a dividend reinvestment quietly buys the same fund in a Roth
    eleven days later. Neither broker can see that. This database can.

    Algorithm:
      1. Window = sale_date +/- 30 days.
      2. Query portfolio.transactions for action='buy' on `symbol` across ALL
         account_ids in that window, IRAs and 401ks included.
      3. Include dividend reinvestments, which is why classify_action() in
         parsers/common.py maps 'reinvest' to 'buy' before it checks 'dividend'.
      4. Return the blocking purchases with account, date, and quantity, plus how
         many shares of the loss would be disallowed (the disallowed amount is
         proportional to the overlapping share count, not all-or-nothing).

    Known limitation to state plainly in any report: "substantially identical"
    is a legal test, not a ticker match. Two different S&P 500 index funds may
    well be substantially identical while having different symbols. Symbol
    matching catches the obvious cases and misses the judgment calls, and the
    report must say so rather than implying an all-clear.
    """
    raise NotBuiltYet("wash_sale_check is agent 3 scope. See module docstring.")


def realized_gains(year: int) -> dict:
    """Realized gain/loss year to date, split short vs long term.

    Algorithm: replay sells against lots under FIFO (derive_lots.py already has
    the machinery), match each disposal to its opening lot, and classify by
    holding period at the disposal date. Also surface any capital loss
    carryforward the prior year left behind, which has to be entered manually
    since it lives on the tax return rather than in any broker export.
    """
    raise NotBuiltYet("realized_gains is agent 3 scope. See module docstring.")


def asset_location_review(as_of: date) -> list[dict]:
    """Whether tax-inefficient holdings sit in the wrong account type.

    The general principle: assets throwing off ordinary income (bond funds, REITs,
    high-yield dividend payers) belong in tax-deferred accounts, while assets
    that mostly compound untaxed (broad equity index funds, growth names held for
    years) are fine in taxable and better in a Roth.

    Algorithm: join holdings to securities.asset_class and fundamentals.div_yield,
    flag high-yield or bond positions sitting in tax_type='taxable' while
    equivalent room exists in a tax-deferred account, and quantify the annual
    tax drag at an assumed marginal rate that Dan sets in config.yaml.

    Frame every output as a consideration, not an instruction. Moving assets
    between account types is not free and can realize gains.
    """
    raise NotBuiltYet("asset_location_review is agent 3 scope. See module docstring.")
