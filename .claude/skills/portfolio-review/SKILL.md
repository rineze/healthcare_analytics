---
name: portfolio-review
description: Run the monthly portfolio review. Loads any broker exports sitting in portfolio/inbox/, reconciles them against the file totals, refreshes prices and fundamentals, then dispatches the investment-analyst agent to write and send the report. Use when Dan says "run my portfolio review", "portfolio update", or drops new broker exports in the inbox.
---

# Portfolio Review

The whole ritual: export, drop in `inbox/`, run this.

Work from the repository root. Every step prints something worth reading, so do
not run them blind.

## 1. Load

```bash
python portfolio/load.py
```

Then **read the reconciliation output before continuing.**

- **Errors** mean nothing was written. Do not proceed, do not work around it,
  and do not pass `--allow-unknown-accounts` to make an error go away. Report
  what failed and stop. An account missing from `config.yaml` means positions
  were dropped, so every total downstream would be wrong.
- **Warnings** mean the load succeeded with known gaps. Note them, continue, and
  make sure they reach the report.
- **Empty inbox** is fine. The last snapshot is still in the database and prices
  will refresh against it, so the review still works. Say that you are running
  against the existing snapshot and give its date.

If the reconciliation shows a mismatch between parsed and stated totals, the
parser is dropping rows. That is a code fix in `portfolio/parsers/`, not
something to note and move past.

## 2. Enrich

```bash
python portfolio/enrich.py
```

Pulls prices, sector, industry, and fundamentals from yfinance, and fills market
value on any lots that were derived from transaction history.

Symbols it cannot resolve are recorded rather than dropped. Note them.

## 3. Report

Dispatch the `investment-analyst` subagent. Give it:

- The date of the snapshot being reported on
- Any warnings from steps 1 and 2, so they land in the report's data gaps section
  rather than being discovered independently

The agent reads `python portfolio/metrics.py --json`, writes
`portfolio/reports/YYYY-MM-DD-investment.md`, records the run, and sends the
digest through `notify.py`.

## 4. Confirm

Report back to Dan:

- Snapshot date and total portfolio value
- Where the report was written
- Whether the Telegram send succeeded
- Any warnings that need him to do something (an account to add to config, a
  broker export to regenerate, a symbol that will not resolve)

## Notes

- Loads are idempotent. Re-running on the same files updates rather than
  duplicates, so there is no harm in running it twice.
- `--dry-run` on `load.py` parses and reconciles without writing. Use it when
  testing a new export format.
- The market analyst and tax agent are not built yet. If Dan asks for sector
  research or tax-loss harvesting, say so rather than improvising it here. The
  scaffolding is in `portfolio/tax_metrics.py`.
