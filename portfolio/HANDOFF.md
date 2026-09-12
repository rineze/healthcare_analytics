# Handoff: getting this running on your machine

Everything in this repo is code. **None of your data is here**, and that is
deliberate: the repo is public, so holdings, cost basis and account config are
all gitignored. They exist only where you put them.

That is why step 1 matters more than it looks.

---

## 1. Put your two data files back

You were sent these in chat. Save them to:

```
portfolio/config.yaml                          <- accounts, tax types, cadences
portfolio/inbox/manual_holdings_2026-09-05.csv <- share counts, cost basis, ESPP flags
```

Both are gitignored. If you lose them you re-enter everything by hand, so back
them up somewhere outside the repo as well.

## 2. Dependencies and database

```bash
pip install -r portfolio/requirements.txt
```

Create `.env` at the repo root:

```
LOCAL_HOST=127.0.0.1
LOCAL_PORT=5432
LOCAL_DATABASE=postgres
LOCAL_USER=postgres
LOCAL_PASSWORD=<your local postgres password>
```

Then:

```bash
python portfolio/db.py        # creates the portfolio schema
python portfolio/config.py    # validates config, prints what it read
```

`db.py` connects to your **local Postgres only** and raises if `LOCAL_HOST` is
missing. It will not fall back to anything hosted. That is intentional: a missing
env var must never quietly route brokerage data to a cloud database.

## 3. Load and enrich

```bash
python portfolio/load.py      # reconciles every account before writing anything
python portfolio/enrich.py    # prices, sector, fundamentals
```

The load **refuses to write** if any account fails to reconcile. If it errors,
read the error rather than working around it; a mismatch means rows are being
dropped and every downstream number would be wrong.

`enrich.py` is where this gets meaningfully better than it is in the dev sandbox.
Yahoo is blocked there, so most of the book has never carried a real price. On
your machine it will, and then:

- BTC and BLK stop reading as `$0` and get real values
- Every position with a share count reprices on each run instead of sitting frozen
- The market analyst gets fundamentals it has never actually had

Expect the first real run to shift totals. That is the system working, not a bug.

## 4. Telegram (three minutes, and the check-in needs it)

1. Message `@BotFather` in Telegram, send `/newbot`, follow the prompts
2. Put the token in `.env` as `TELEGRAM_BOT_TOKEN`
3. Send your new bot any message. A bot cannot open a conversation with you, so
   this step is required.
4. `python portfolio/notify.py --whoami` reads your chat id off that message
5. Put it in `.env` as `TELEGRAM_CHAT_ID`
6. `python portfolio/notify.py --test`

## 5. The scheduled check-in

This is the proactive piece. It runs **on your machine**, because that is where
your database is — a cloud-scheduled job has nothing to query.

Run it once by hand first:

```bash
python portfolio/gaps.py             # see what it thinks
python portfolio/gaps.py --notify    # send the nudge, if one is warranted
```

Then register it. PowerShell, as your normal user:

```powershell
$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
             -Argument "-NoProfile -ExecutionPolicy Bypass -File C:\dev\healthcare_analytics\portfolio\check_in.ps1"
$trigger = New-ScheduledTaskTrigger -Daily -At 8am
Register-ScheduledTask -TaskName "Portfolio check-in" -Action $action -Trigger $trigger
```

Adjust the path if the repo lives elsewhere. Remove it with
`Unregister-ScheduledTask -TaskName "Portfolio check-in"`.

**It runs daily and stays silent most days.** That is the design. It speaks when
a 401k contribution has actually landed since you last confirmed, or when a cost
basis worth asking about is missing. A nudge that fires on a schedule regardless
of whether anything happened is one you learn to ignore, and then you ignore the
one that mattered.

---

## What it will say, and when

Your 401k is set to `biweekly` because contributions land every two weeks. The
check counts **pay periods elapsed, not days**, so the message is about something
that happened rather than a date that passed:

> Empower 401k - Roth sources: 1 contribution since you last confirmed on
> 2026-09-04. Share count has moved.

Your brokerage and Robinhood are `on_activity`: they only change when you trade,
so they are never overdue on time alone and will never be nudged on a calendar.

When it does nudge you, open Claude Code and talk to the **inventory** agent. It
will ask for what it needs, show you the exact rows before writing, and push
everything through the same reconciliation gate a broker export passes.

---

## What is still open

**Data you owe the system.** The Individual #2 cash balance, and Form 3922 for
the ESPP shares. That second one matters: your reported basis on those almost
certainly omits the discount already taxed as ordinary income, which means the
harvestable loss the tax agent shows is a **floor, not an answer**. PSTH is
parked until you want to get into it.

**Fund look-through is the biggest analytical gap.** You are 86% funds, so your
true single-name exposure is invisible to the tooling. The market analyst worked
out by hand that your largest single name is likely Amazon at around 6.9%, not
UNH at 3.3%, and that roughly a quarter of the book sits in seven names levered
to the same cycle. Every concentration figure the system reports today is a
floor. Closing this needs a fund-holdings data source.

**The repo is public.** Nothing of yours is exposed and nothing personal has ever
been committed, verified. But a personal finance system living in a public work
repo is one `git add -f` from a bad day. Everything is scoped to `portfolio/` and
`.claude/`, so moving it is about twenty minutes.

---

## Daily driver, once set up

| You want to | Do this |
|---|---|
| Record a trade or contribution | Talk to the `inventory` agent |
| Update holdings after a nudge | Talk to the `inventory` agent |
| Full periodic review | `/portfolio-review` |
| A question spanning domains | `/portfolio-ask` |
| One specialist's view | Talk to `investment-analyst`, `market-analyst` or `tax-agent` by name |
| See what the system is missing | `python portfolio/gaps.py` |
