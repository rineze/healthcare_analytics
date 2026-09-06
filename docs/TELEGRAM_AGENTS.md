# Telegram Agents Setup

Two agents watch this database, and they cover each other's weak spot.

| | Where it runs | Alive when | Talks back |
|---|---|---|---|
| **Observer** | GitHub Actions | always | no |
| **Engineer** | your machine | while your machine is on | yes |

The observer is the smoke alarm. It cannot die, it cannot think, and it sends
one message a week. The engineer is the one you actually talk to, in the same
shape as Jeff E: long-polls Telegram, flags things unprompted, and answers
questions.

Set up the observer first, since the engineer reuses its Telegram bot and its
read-only database role.

---

# Part 1: Observer

Five steps, about ten minutes, and then the database checks itself every Monday
and messages you when something goes stale. This runs in GitHub Actions, so it
does not depend on any Claude session being open.

## 1. Give the observer role a password

`sql/migrations/004_observer_role.sql` already created a `db_observer` role with
SELECT on the `meta` schema and nothing else. It has no password and cannot log
in yet, deliberately, so no credential ever passed through a migration file.

In the Supabase SQL editor:

```sql
ALTER ROLE db_observer LOGIN PASSWORD 'pick-something-long-and-random';
```

That credential can read the freshness ledger. It cannot write anything, and it
cannot see `drinf`, `cms`, or `payor_tracker` at all. Worst case if it leaks,
someone learns when you last loaded MA data.

## 2. Create the Telegram bot

In Telegram, message **@BotFather**:

- Send `/newbot`
- Give it a name and a username ending in `bot`
- It replies with a token like `8123456789:AAH...`. That is `TELEGRAM_BOT_TOKEN`.

## 3. Get your chat id

Message your new bot once (anything, "hi" is fine). Bots cannot start
conversations, so this first message is what makes the chat exist. Then open:

```
https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
```

Find `"chat":{"id":123456789`. That number is `TELEGRAM_CHAT_ID`.

## 4. Add the repository secrets

GitHub repo, **Settings > Secrets and variables > Actions > New repository
secret**. Five of them:

| Secret | Value |
|---|---|
| `SUPABASE_HOST` | `aws-1-us-east-1.pooler.supabase.com` |
| `SUPABASE_USER` | `db_observer.numdlqsfydtypeurijae` |
| `SUPABASE_PASSWORD` | the password from step 1 |
| `TELEGRAM_BOT_TOKEN` | the token from step 2 |
| `TELEGRAM_CHAT_ID` | the id from step 3 |

The username is the role name plus the project ref. That is how the Supabase
session pooler addresses any role, not just `postgres`. Getting this wrong is
the usual cause of "Tenant or user not found."

## 5. Test it

GitHub repo, **Actions > DB Observer > Run workflow**. Tick **dry_run** the
first time: it connects and prints the message to the job log without sending
anything. Once that looks right, run it again with **always** ticked to confirm
the message actually lands in Telegram.

After that it runs itself, Mondays at 13:00 UTC.

## What it does and does not do

**Does:** reads `meta.v_data_freshness`, groups overdue datasets by the loader
that owns them, and sends one message. Stays silent when nothing is overdue, so
it is not noise. Fails the workflow run (and emails you) if it cannot connect,
because an observer that has quietly stopped working is worse than none.

**Does not:** load anything, fix anything, or reply to you. It is one-way. See
"Two-way" below.

## Changing the schedule

Edit the cron in `.github/workflows/db-observer.yml`. It is UTC and does not
follow DST, so the local time shifts by an hour twice a year.

```yaml
- cron: "0 13 * * 1"    # Mondays 13:00 UTC
```

---

# Part 2: Engineer

The one you text. Runs on your machine, same as Jeff E.

## Add the Telegram vars to .env

It reuses the bot and chat id from Part 1. Put them in the repo-root `.env`
alongside the database vars, since `healthcare_db.py` loads that file at import:

```bash
TELEGRAM_BOT_TOKEN=8123456789:AAH...
TELEGRAM_CHAT_ID=123456789
```

## Run it

```bash
python scripts/db_engineer.py --dry-run --once   # check, print, exit
python scripts/db_engineer.py --once             # check, send, exit
python scripts/db_engineer.py                    # stay up and listen
```

That last one is the real mode. It long-polls Telegram, so no webhook, no
public URL, and it works behind a home router.

## What you can text it

| | |
|---|---|
| `/stale` | what needs loading |
| `/health` | database vitals |
| `/sources` | the full ledger |
| `/run` | what it is allowed to run |
| `/propose_<name>` | ask for an approval button, e.g. `/propose_load_ma` |
| `/help` | the list |

Anything else goes to Claude Code headless with the repo as working directory,
so "why is medicare_utilization untracked" gets a real answer rather than a
canned one. That path needs the `claude` CLI on PATH; without it the slash
commands still work and it says so.

## How it behaves

**It does not nag.** A dataset is reported when it *becomes* stale, not every
cycle forever. State lives in `~/.db_engineer_state.json`. A daily message
repeating the same five items is one you stop reading, which defeats the point.

**It only answers you.** Anyone can message a Telegram bot if they find it, so
every sender other than `TELEGRAM_CHAT_ID` is ignored.

**It writes only with your approval.** Two separate limits:

- **The allowlist** decides what is *possible*. It lives in
  `scripts/db_actions.py` as fixed `argv` tuples, run without a shell. Adding
  an entry is a code change. There is no path from a Telegram message to
  arbitrary SQL or an arbitrary command, so `/propose_load_ma; rm -rf /` is
  just an unknown action name.
- **Your tap** decides what *proceeds*. Approvals are single use, expire after
  15 minutes, and are bound to your chat id.

Current allowlist: refresh ledger stats, load MA enrollment, load MPFS RVU,
load MPFS GPCI, load Medicare utilization.

The flow: `/propose_load_ma` gets you a prompt naming exactly what it writes to,
with Run it / Cancel buttons. Nothing happens until you tap. A proactive alert
carries a "Fix:" button that opens that same prompt rather than running
anything, because an alert you read six hours later should not carry a live
approval.

Long loads run on a background thread, so the bot keeps answering while MA
enrollment is churning through 2.4M rows. Every proposal, approval, rejection
and run is appended to `~/.db_engineer_audit.jsonl`.

## Keeping it alive

Simplest is a terminal you leave open, which is probably what Jeff E does. To
survive reboots on macOS, a launchd agent at
`~/Library/LaunchAgents/com.rineze.dbengineer.plist`:

```xml
<key>ProgramArguments</key>
<array>
  <string>/usr/bin/python3</string>
  <string>/path/to/healthcare_analytics/scripts/db_engineer.py</string>
</array>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
```

Then `launchctl load ~/Library/LaunchAgents/com.rineze.dbengineer.plist`.

## Why both

Your Jeff E session sat idle for 18 days. That is the honest failure mode of
anything running on a laptop: it stops and nothing tells you it stopped. The
GitHub Action is there so that when the engineer is asleep, you still find out
the MA data went stale. Neither one alone is enough.
