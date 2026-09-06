#!/usr/bin/env python3
"""
db_engineer.py

The database engineer you text. Same shape as Jeff E: runs on your machine,
long-polls Telegram, flags things at you proactively, and answers when you ask.

    proactive   notices a dataset went stale and texts you, unprompted
    reactive    you text it a question, it answers

Long polling means no webhook, no hosting, no public URL, and it works fine
behind a home router. The tradeoff is that it only runs while your machine is
on, which is the same tradeoff Jeff E already makes.

Run it
------
    python scripts/db_engineer.py                 # foreground
    python scripts/db_engineer.py --once          # single proactive check, exit
    python scripts/db_engineer.py --dry-run       # print instead of sending

Environment
-----------
    TELEGRAM_BOT_TOKEN     from @BotFather
    TELEGRAM_CHAT_ID       your chat id. The bot ignores every other sender.
    SUPABASE_*             read-only db_observer role. See docs/TELEGRAM_AGENTS.md

Commands it understands
-----------------------
    /stale      what needs loading
    /health     database vitals
    /sources    the full ledger
    /help       this list

Anything else you type is handed to Claude Code with the repo as context, so
you can ask "why is medicare_utilization untracked" and get a real answer.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_HERE = Path(__file__).resolve().parent
for _p in (str(_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from healthcare_db import get_connection  # noqa: E402
from db_observer import build_message, collect  # noqa: E402

TELEGRAM_MAX_CHARS = 4096
POLL_TIMEOUT = 30          # seconds Telegram holds the long poll open
CLAUDE_TIMEOUT = 300       # seconds to let Claude think before giving up

# Remembers which datasets we already nagged about, so a stale dataset is
# reported when it goes stale rather than every single cycle forever.
STATE_FILE = Path.home() / ".db_engineer_state.json"


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

class Telegram:
    def __init__(self, token, chat_id, dry_run=False):
        self.token = token
        self.chat_id = str(chat_id)
        self.dry_run = dry_run
        self.offset = None

    def _call(self, method, params=None, timeout=60):
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        data = json.dumps(params or {}).encode()
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())

    def send(self, text):
        if len(text) > TELEGRAM_MAX_CHARS:
            text = text[: TELEGRAM_MAX_CHARS - 20].rstrip() + "\n<i>(truncated)</i>"

        if self.dry_run:
            print("-" * 60)
            print(text)
            print("-" * 60)
            return True

        try:
            return self._call("sendMessage", {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }).get("ok", False)
        except urllib.error.HTTPError as e:
            # Telegram explains itself in the body, not the status line. The
            # usual cause is malformed HTML in the message.
            print(f"send failed {e.code}: {e.read().decode()[:300]}", file=sys.stderr)
            return False
        except Exception as e:
            print(f"send failed: {e}", file=sys.stderr)
            return False

    def poll(self):
        """Long-poll for new messages. Returns a list of (chat_id, text)."""
        try:
            params = {"timeout": POLL_TIMEOUT}
            if self.offset is not None:
                params["offset"] = self.offset
            result = self._call("getUpdates", params, timeout=POLL_TIMEOUT + 15)
        except Exception as e:
            print(f"poll failed: {e}", file=sys.stderr)
            time.sleep(5)
            return []

        messages = []
        for update in result.get("result", []):
            self.offset = update["update_id"] + 1
            msg = update.get("message") or {}
            text = msg.get("text")
            chat = str((msg.get("chat") or {}).get("id", ""))
            if text:
                messages.append((chat, text.strip()))
        return messages


# ---------------------------------------------------------------------------
# Answers it can give without waking Claude
# ---------------------------------------------------------------------------

def _esc(s):
    """Escape for Telegram HTML parse mode."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def cmd_stale():
    rows, db_size, total = collect()
    msg = build_message(rows, db_size, total)
    return msg or f"✅ <b>All data current</b>\n\n<i>{total} sources tracked, db {db_size}</i>"


def cmd_health():
    with get_connection(connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT pg_size_pretty(pg_database_size(current_database())),
                       current_setting('server_version'),
                       date_trunc('second', now() - pg_postmaster_start_time())::text
            """)
            size, version, uptime = cur.fetchone()
            cur.execute("SELECT count(*) FROM meta.data_sources")
            (sources,) = cur.fetchone()

    return (
        "<b>Database health</b>\n\n"
        f"Postgres {_esc(version)}\n"
        f"Size {_esc(size)}\n"
        f"Uptime {_esc(uptime)}\n"
        f"{sources} sources tracked"
    )


def cmd_sources():
    with get_connection(connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT source_name, status, COALESCE(days_overdue, 0), record_count
                FROM meta.v_data_freshness
            """)
            rows = cur.fetchall()

    icon = {"overdue": "🔴", "due": "🟡", "current": "🟢",
            "untracked": "⚪", "never_loaded": "⚫", "n/a": "·"}
    lines = ["<b>All tracked sources</b>", ""]
    for name, status, overdue, count in rows:
        tail = f" {overdue}d" if overdue else ""
        lines.append(f"{icon.get(status, '·')} {_esc(name)} "
                     f"<i>{_esc(status)}{tail}</i> {count:,}")
    return "\n".join(lines)


HELP = (
    "<b>DB engineer</b>\n\n"
    "/stale - what needs loading\n"
    "/health - database vitals\n"
    "/sources - the full ledger\n"
    "/help - this\n\n"
    "Or just ask me something in plain English and I'll dig into the repo "
    "and the database to answer."
)

COMMANDS = {
    "/stale": cmd_stale,
    "/health": cmd_health,
    "/sources": cmd_sources,
    "/help": lambda: HELP,
    "/start": lambda: HELP,
}


# ---------------------------------------------------------------------------
# Anything else goes to Claude
# ---------------------------------------------------------------------------

CLAUDE_PREAMBLE = """You are a database engineer answering a question over \
Telegram, so keep the reply under 250 words, plain text, no markdown headers \
or code fences.

Context: this repo is healthcare analytics on a Supabase Postgres database. \
meta.v_data_freshness tracks which datasets are stale. docs/DATABASE_CONNECTIONS.md \
explains the setup. You have read-only database access.

Do not modify the database or push commits. If the answer requires a write or a \
data load, describe what you would run and say it needs approval.

Question: """


def ask_claude(question):
    """Hand a free-form question to Claude Code headless, with the repo as cwd."""
    if not shutil.which("claude"):
        return ("I can answer /stale, /health and /sources, but free-form "
                "questions need the Claude Code CLI on PATH.")

    try:
        proc = subprocess.run(
            ["claude", "-p", CLAUDE_PREAMBLE + question],
            capture_output=True, text=True, timeout=CLAUDE_TIMEOUT, cwd=str(_ROOT),
        )
    except subprocess.TimeoutExpired:
        return f"That took longer than {CLAUDE_TIMEOUT}s and I gave up."

    if proc.returncode != 0:
        return f"Claude exited {proc.returncode}: {_esc(proc.stderr[:300])}"

    answer = proc.stdout.strip()
    return _esc(answer) if answer else "Claude came back with nothing."


def handle(text):
    """Route one incoming message to an answer."""
    cmd = text.split()[0].lower() if text.split() else ""
    # Telegram appends @botname when a command is used in a group.
    cmd = cmd.split("@")[0]

    if cmd in COMMANDS:
        try:
            return COMMANDS[cmd]()
        except Exception as e:
            return f"That check failed: {_esc(e)}"
    return ask_claude(text)


# ---------------------------------------------------------------------------
# Proactive checking
# ---------------------------------------------------------------------------

def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"alerted": [], "last_check": None}


def save_state(state):
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        print(f"could not save state: {e}", file=sys.stderr)


def proactive_check(tg, state, force=False):
    """Alert on datasets that have newly gone stale.

    Only newly stale sources trigger a message. Re-sending the same list every
    cycle is how a useful alert turns into one you swipe away without reading.
    """
    rows, db_size, total = collect()
    flagged = sorted(r["source_name"] for r in rows if r["status"] in ("overdue", "due"))
    already = set(state.get("alerted", []))
    new = [s for s in flagged if s not in already]

    state["alerted"] = flagged
    state["last_check"] = datetime.now(timezone.utc).isoformat()
    save_state(state)

    if not new and not force:
        return False

    msg = build_message(rows, db_size, total)
    if msg is None:
        return False
    if new and not force:
        names = ", ".join(_esc(n) for n in new)
        msg = f"<b>New since last check:</b> {names}\n\n" + msg
    tg.send(msg)
    return True


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Telegram DB engineer.")
    ap.add_argument("--once", action="store_true",
                    help="run one proactive check and exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="print messages instead of sending them")
    ap.add_argument("--interval", type=int, default=6,
                    help="hours between proactive checks (default 6)")
    args = ap.parse_args()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not args.dry_run and not (token and chat_id):
        print("ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set",
              file=sys.stderr)
        return 1

    tg = Telegram(token, chat_id, dry_run=args.dry_run)
    state = load_state()

    if args.once:
        sent = proactive_check(tg, state, force=True)
        print("sent" if sent else "nothing to report")
        return 0

    print(f"db engineer up. polling telegram, checking every {args.interval}h.")
    tg.send("🔧 <b>DB engineer online</b>\nSend /help for what I can do.")

    next_check = 0.0
    while True:
        if time.time() >= next_check:
            try:
                proactive_check(tg, state)
            except Exception as e:
                print(f"proactive check failed: {e}", file=sys.stderr)
            next_check = time.time() + args.interval * 3600

        for sender, text in tg.poll():
            # The bot token is guessable-adjacent and anyone can message a bot.
            # Only the configured chat gets answers.
            if sender != tg.chat_id:
                print(f"ignoring message from {sender}", file=sys.stderr)
                continue
            print(f"< {text}")
            tg.send(handle(text))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped")
        sys.exit(0)
