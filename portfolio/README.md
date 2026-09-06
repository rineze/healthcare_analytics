# Portfolio Agents

Consolidated reporting across Fidelity, Empower, and Robinhood. No single
platform sees the whole picture, so concentration, sector exposure, and
cross-account wash sales are invisible until the data lives in one place.

Three agents were planned. **One is built.**

| Agent | Status | What it does |
|---|---|---|
| Investment analyst | Built | Bottom-up. Positions, weights, concentration, contributors, valuation, what changed |
| Market analyst | Not built | Top-down. Sector vs benchmark, macro, research on what he owns |
| Tax agent | Not built | TLH candidates, cross-account wash sales, holding periods, asset location |

Agent 3 waits on purpose. Whether it can say anything trustworthy depends on how
clean the lot data turns out to be, and that is unknowable until real exports are
loaded. See `tax_metrics.py` for the algorithms, written out and ready to fill in.

## The design principle

**Python computes, agents interpret.**

No LLM calculates a cost basis, a holding period, a weight, or a wash-sale
window. `metrics.py` produces every number and agents narrate what it means.
Every figure in every report traces back to a function and a SQL query you can
run by hand.

---

## Setup

Once, and then never again.

### 1. Dependencies

```bash
pip install -r portfolio/requirements.txt
```

### 2. Database

Uses your local Postgres. Add to `.env` at the repo root:

```
LOCAL_HOST=127.0.0.1
LOCAL_PORT=5432
LOCAL_DATABASE=postgres
LOCAL_USER=postgres
LOCAL_PASSWORD=...
```

Everything lands in a `portfolio` schema, kept separate from `drinf`.

This connection is **local only and fails hard**, unlike `pfs-analysis/utils.py`
which falls back to Supabase when `USE_LOCAL` is unset. That fallback is wrong
here: a missing env var must never quietly ship brokerage data to a cloud
database.

```bash
python portfolio/db.py     # creates the schema
```

### 3. Config

```bash
cp portfolio/config.example.yaml portfolio/config.yaml
```

Fill in your accounts. **`tax_type` is the field that matters.** No broker export
tells you whether an account is taxable, a Roth, or a 401k, and the tax agent is
inert without it. `mask4` is the last four digits only, never the full number.

```bash
python portfolio/config.py     # validates and prints what it read
```

### 4. Telegram

Three minutes:

1. Message `@BotFather` in Telegram, send `/newbot`, follow the prompts
2. Put the token in `.env` as `TELEGRAM_BOT_TOKEN`
3. Send your new bot any message (a bot cannot start the conversation, so this
   step is required)
4. `python portfolio/notify.py --whoami` reads your chat id off that message
5. Put it in `.env` as `TELEGRAM_CHAT_ID`
6. `python portfolio/notify.py --test`

iMessage is stubbed and cannot work on Windows. It needs macOS and AppleScript
against Messages.app. The channel interface is there if this ever runs from a Mac.

---

## The monthly ritual

Export, drop, run. That is the whole thing, and it stays that short on purpose.

### Fidelity

Grab **both** files:

- **Positions**: Accounts > Portfolio > Positions > download
- **Cost basis**: Accounts > Tax Info (or Cost Basis) > download

The cost basis file has lot-level detail with acquisition dates. It is the only
file in this project that makes the tax agent possible. Do not skip it.

Optionally also **History** (Activity & Orders > download) for realized gains and
the wash-sale window.

### Robinhood

Account > Menu > Reports and Statements > Reports > generate a CSV.

**Set the date range to cover the entire life of the account.** Robinhood gives
no cost basis export, so lots are reconstructed by replaying buy/sell history
under FIFO. If the history starts partway through, the opening position is
invisible and every derived basis after it is wrong. `load.py` checks this and
flags it, but a full-history export avoids the problem.

### Empower

Holdings view > download icon.

Empower is an aggregator, so it will re-report positions Fidelity and Robinhood
already covered. Those are deduped automatically with the broker's own numbers
winning. Where Empower earns its place is coverage: an old 401k or an HSA you do
not export directly.

Its weakness is cost basis, which aggregated views usually lack. Treat it as the
completeness check, not the tax agent's source of truth.

### Then

```
Drop the files in portfolio/inbox/
```

```bash
/portfolio-review
```

Report lands in `portfolio/reports/`, digest lands on your phone.

---

## Running the pieces by hand

```bash
python portfolio/load.py                      # parse, reconcile, write
python portfolio/load.py --dry-run            # parse and reconcile only
python portfolio/load.py --as-of 2026-09-05   # if the files carry no date
python portfolio/load.py --archive            # move loaded files to inbox/processed/

python portfolio/enrich.py                    # prices, sector, fundamentals
python portfolio/enrich.py --full             # refresh everything, not just stale
python portfolio/enrich.py --prices           # prices only

python portfolio/metrics.py                   # human-readable summary
python portfolio/metrics.py --json            # the payload agents read
python portfolio/metrics.py --record path.md  # save this run's snapshot

python portfolio/notify.py --test
```

Loads are idempotent. `lot_id` and `txn_id` are deterministic hashes of their
natural keys, so re-dropping the same export updates rather than duplicates.

---

## The validation gate

`load.py` parses everything, reconciles it against the totals the files claim,
prints the result, and only then writes. If the numbers do not tie to the penny,
it refuses to write and says why.

That strictness is the most important thing in this project. A parser that
silently drops rows produces a report that is wrong in a way nobody can see, and
every agent downstream repeats the error with total confidence. Load time is the
only place this is cheap to catch.

What it checks:

- Parsed market value per account vs the total stated in the file
- Rows read vs rows loaded, with a reason recorded for every drop
- Accounts appearing in exports but missing from `config.yaml` (fatal, because
  their positions get dropped and every total goes wrong)
- Positions with no cost basis (warning, and it names them)
- Derived lots whose share count disagrees with the position snapshot

**If it errors, fix the parser.** Do not pass `--allow-unknown-accounts` to make
a red light go green.

---

## Schema

```
portfolio.accounts       from config.yaml, the only source of tax_type
portfolio.securities     symbol reference, enriched by yfinance
portfolio.holdings       position snapshot per account/symbol/date
portfolio.lots           tax lots, from Fidelity directly or derived FIFO
portfolio.transactions   buys, sells, dividends, for realized G/L and wash sales
portfolio.prices         daily closes
portfolio.fundamentals   P/E, margins, growth, earnings dates
portfolio.report_runs    each run's metrics snapshot, so the next one can diff
```

`report_runs` is what makes reports worth opening. Without it, every month
restates the same portfolio and you stop reading by month three.

---

## Known limitations

Stated plainly, because a tool that hides these is worse than one that does not
exist.

- **No broker APIs.** Fidelity and Empower offer none for retail, and
  Robinhood's is unofficial and breaks on MFA. Manual export is not laziness,
  it is the only reliable option.
- **Robinhood lots are derived, not reported.** FIFO replay of transaction
  history. Correct when the history is complete, wrong when it is not, and
  `derive_lots.py` flags which case you are in.
- **Splits are not replayed.** Symbols with split activity get their derived
  lots flagged unreliable rather than silently adjusted.
- **Asset class is a heuristic.** A fund's real composition is not in the
  export, so it is inferred from name and metadata. Pin anything wrong in
  `config.yaml` under `asset_class_overrides`.
- **ETF sector exposure is not looked through.** A fund's holdings land under
  "Fund / Unclassified" rather than being spread across the sectors it actually
  holds.
- **"Substantially identical" is a legal test, not a ticker match.** When the
  tax agent lands, its wash-sale check matches on symbol. Two different S&P 500
  funds may be substantially identical with different tickers. It catches the
  obvious cases and misses the judgment calls, and the report has to say so.
- **None of this is tax advice.** It is a organized view of your own data.

## Privacy

- Account numbers are masked to the last four at parse time. Full numbers are
  never written to disk or database.
- `inbox/`, `reports/`, and `config.yaml` are gitignored.
- The database connection is local only and raises rather than falling back to
  anything hosted.
- Telegram credentials live in `.env`, which is already gitignored.

One thing worth a beat of thought: this is a healthcare/RCM work repo, and
personal brokerage data is a slightly odd houseguest even with everything
gitignored. Everything here is scoped to `portfolio/` and `.claude/` so it lifts
into its own repo without untangling.
