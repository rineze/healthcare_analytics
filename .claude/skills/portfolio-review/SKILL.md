---
name: portfolio-review
description: Run the periodic portfolio review. Loads any broker exports or hand-written entries sitting in portfolio/inbox/, reconciles them, refreshes prices, then dispatches the specialist agents on their own cadences. Use when asked to run a portfolio review or portfolio update, or after dropping new exports in the inbox.
---

# Portfolio Review

Load, reconcile, enrich, then dispatch. Work from the repository root.

## 1. Load

```bash
python portfolio/load.py
```

**Read the reconciliation before continuing.**

- **Errors** mean nothing was written. Stop. Do not work around it, and never
  pass a flag that suppresses validation to make a red light go green. An account
  missing from config means positions were dropped, so every total downstream
  would be wrong.
- **Warnings** mean the load succeeded with known gaps. Carry them forward so
  they reach the reports.
- **Empty inbox** is fine. The last snapshot is still in the database and prices
  refresh against it. Say which snapshot you are running against.

A mismatch between parsed and stated totals is a parser bug, not a note to move
past.

## 2. Enrich

```bash
python portfolio/enrich.py
```

Prices, sector, fundamentals, and market value for any position that arrived as a
share count without a value. Symbols it cannot resolve are recorded rather than
dropped; note them.

## 3. Dispatch

Each agent has its own cadence, because their findings change at different
speeds. Running an agent that has nothing new to say trains the reader to skim.

| Agent | When | Why |
|---|---|---|
| investment-analyst | Every run | Positions and weights change continuously |
| market-analyst | Monthly | Research does not turn over week to week |
| tax-agent | Quarterly, plus November and December | Harvesting has a real deadline; the rest of the year it repeats itself |

Pass each agent any warnings from steps 1 and 2 so gaps land in their reports
rather than being rediscovered.

**When you skip an agent, say so and say when it next runs.** A silent omission
is indistinguishable from a report with nothing to say.

Override the cadence when asked, or when something in the load warrants it: a
large new position justifies research out of cycle, and a realized sale justifies
the tax agent regardless of month.

The **ledger** is never dispatched here. It runs when there is something to
record, driven by conversation.

## 4. Confirm

Report back: snapshot date and total value, which agents ran and which were
skipped with next run dates, where reports were written, whether notifications
sent, and anything needing action (an account to add to config, an export to
regenerate, a symbol that will not resolve).

## Notes

- Loads are idempotent. Re-running on the same files updates rather than
  duplicates.
- `--dry-run` parses and reconciles without writing. Use it when testing a new
  export format.
- For a question rather than a review, use `portfolio-ask` or talk to an agent
  directly.
