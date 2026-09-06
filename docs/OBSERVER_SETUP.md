# Observer Setup

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

## Two-way

Telegram bots receive messages either by webhook (Telegram POSTs to a URL you
host) or long polling (something you run stays connected and asks for updates).
Both need a process that is always on. GitHub Actions is not that: it runs on a
schedule and exits.

So texting the bot and getting an answer needs one more piece, a small always-on
host. That is a separate build from this one, and worth doing only once the
one-way reminders have proven they are the thing you actually want.
