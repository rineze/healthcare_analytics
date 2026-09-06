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
    /run        what it is allowed to run for you
    /help       this list

Anything else you type is handed to Claude Code with the repo as context, so
you can ask "why is medicare_utilization untracked" and get a real answer.

Writes
------
It can write, but only what db_actions.ACTIONS allows and only after you tap
approve. Those are two separate limits: the allowlist decides what is possible
and lives in code, the approval decides what proceeds right now. There is no
path from a Telegram message to arbitrary SQL or an arbitrary shell command,
so the worst a hostile message can do is ask for something already on the list,
which still needs your tap.

Approvals are single use, expire after 15 minutes, and are bound to your chat.
Proactive alerts carry a "Fix:" button that issues a fresh approval prompt
rather than a live one, since an alert may sit unread for hours.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
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
from db_actions import (  # noqa: E402
    ACTIONS, APPROVAL_TTL_SECONDS, ApprovalGate, actions_for_source,
    audit, run_action,
)

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

    def send(self, text, buttons=None):
        """Send a message, optionally with an inline keyboard.

        buttons is a list of (label, callback_data) laid out in one row.
        """
        if len(text) > TELEGRAM_MAX_CHARS:
            text = text[: TELEGRAM_MAX_CHARS - 20].rstrip() + "\n<i>(truncated)</i>"

        if self.dry_run:
            print("-" * 60)
            print(text)
            if buttons:
                print("[buttons] " + "  ".join(f"[{l}]" for l, _ in buttons))
            print("-" * 60)
            return True

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": [
                [{"text": label, "callback_data": data} for label, data in buttons]
            ]}

        try:
            return self._call("sendMessage", payload).get("ok", False)
        except urllib.error.HTTPError as e:
            # Telegram explains itself in the body, not the status line. The
            # usual cause is malformed HTML in the message.
            print(f"send failed {e.code}: {e.read().decode()[:300]}", file=sys.stderr)
            return False
        except Exception as e:
            print(f"send failed: {e}", file=sys.stderr)
            return False

    def answer_callback(self, callback_id, text=""):
        """Clear the spinner on a tapped button."""
        if self.dry_run:
            return True
        try:
            return self._call("answerCallbackQuery", {
                "callback_query_id": callback_id, "text": text[:200],
            }).get("ok", False)
        except Exception as e:
            print(f"answerCallback failed: {e}", file=sys.stderr)
            return False

    def poll(self):
        """Long-poll for updates.

        Returns a list of dicts, each either
            {"kind": "message",  "chat": str, "text": str}
            {"kind": "callback", "chat": str, "data": str, "id": str}
        """
        try:
            params = {"timeout": POLL_TIMEOUT}
            if self.offset is not None:
                params["offset"] = self.offset
            result = self._call("getUpdates", params, timeout=POLL_TIMEOUT + 15)
        except Exception as e:
            print(f"poll failed: {e}", file=sys.stderr)
            time.sleep(5)
            return []

        events = []
        for update in result.get("result", []):
            self.offset = update["update_id"] + 1

            cb = update.get("callback_query")
            if cb:
                chat = str(((cb.get("message") or {}).get("chat") or {}).get("id", ""))
                events.append({"kind": "callback", "chat": chat,
                               "data": cb.get("data", ""), "id": cb.get("id", "")})
                continue

            msg = update.get("message") or {}
            text = msg.get("text")
            chat = str((msg.get("chat") or {}).get("id", ""))
            if text:
                events.append({"kind": "message", "chat": chat, "text": text.strip()})
        return events


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


def cmd_run():
    """List what can be run. Each is a button; nothing happens without a tap."""
    lines = ["<b>What I can run</b>", ""]
    for a in ACTIONS.values():
        lines.append(f"• <b>{_esc(a.label)}</b>")
        lines.append(f"  {_esc(a.detail)}")
        lines.append(f"  writes {_esc(a.writes)}")
    lines.append("")
    lines.append("<i>Tap /propose_&lt;name&gt; to get an approval button, "
                 "e.g. /propose_load_ma</i>")
    return "\n".join(lines)


HELP = (
    "<b>DB engineer</b>\n\n"
    "/stale - what needs loading\n"
    "/health - database vitals\n"
    "/sources - the full ledger\n"
    "/run - what I can run for you\n"
    "/help - this\n\n"
    "Ask me anything in plain English and I'll dig into the repo and the "
    "database to answer.\n\n"
    "I never write to the database without you tapping approve first."
)

COMMANDS = {
    "/stale": cmd_stale,
    "/health": cmd_health,
    "/sources": cmd_sources,
    "/run": cmd_run,
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


def handle(text, gate=None, chat_id=None):
    """Route one incoming message.

    Returns (reply_text, buttons). buttons is None for a plain reply.
    """
    cmd = text.split()[0].lower() if text.split() else ""
    # Telegram appends @botname when a command is used in a group.
    cmd = cmd.split("@")[0]

    if cmd in COMMANDS:
        try:
            return COMMANDS[cmd](), None
        except Exception as e:
            return f"That check failed: {_esc(e)}", None

    # /propose_<action> asks for approval. It never runs anything by itself.
    if cmd.startswith("/propose_"):
        key = cmd[len("/propose_"):]
        if key not in ACTIONS:
            known = ", ".join(sorted(ACTIONS))
            return f"I don't have an action called {_esc(key)}. I have: {_esc(known)}", None
        if gate is None:
            return "No approval gate available.", None
        return propose(gate, key, chat_id)

    return ask_claude(text), None


def propose(gate, action_key, chat_id):
    """Build an approval prompt for an allowlisted action."""
    action = ACTIONS[action_key]
    token = gate.propose(action_key, chat_id)
    audit("proposed", action=action_key, chat=str(chat_id), token=token)

    text = (
        f"<b>Approve: {_esc(action.label)}</b>\n\n"
        f"{_esc(action.detail)}\n\n"
        f"<b>Writes to:</b> {_esc(action.writes)}\n"
        f"<i>Expires in {APPROVAL_TTL_SECONDS // 60} minutes.</i>"
    )
    buttons = [("\u2705 Run it", f"ok:{token}"), ("\u2716 Cancel", f"no:{token}")]
    return text, buttons


def handle_callback(gate, data, chat_id, tg):
    """A tapped button. Returns the text to answer the callback with."""
    if ":" not in data:
        return "unrecognized"
    verb, token = data.split(":", 1)

    # A proactive alert may sit unread for hours, so its button issues a new
    # approval prompt rather than carrying a live one. Two taps, never one.
    if verb == "propose":
        if token not in ACTIONS:
            return "unknown action"
        text, buttons = propose(gate, token, chat_id)
        tg.send(text, buttons)
        return "confirm below"

    if verb == "no":
        gate.cancel(token, chat_id)
        audit("cancelled", chat=str(chat_id), token=token)
        tg.send("Cancelled, nothing ran.")
        return "cancelled"

    if verb != "ok":
        return "unrecognized"

    action, reason = gate.redeem(token, chat_id)
    if action is None:
        audit("approval_rejected", chat=str(chat_id), token=token, reason=reason)
        tg.send(f"Not run: {_esc(reason)}.")
        return reason

    audit("approved", action=action.key, chat=str(chat_id), token=token)
    tg.send(f"\u25b6 Running <b>{_esc(action.label)}</b>...")

    # Loaders take minutes. Run off the main loop so the bot keeps answering.
    def worker():
        ok, output = run_action(action)
        icon = "\u2705" if ok else "\u274c"
        verdict = "done" if ok else "failed"
        tg.send(f"{icon} <b>{_esc(action.label)} {verdict}</b>\n\n"
                f"<pre>{_esc(output)}</pre>")

    threading.Thread(target=worker, daemon=True).start()
    return "running"


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

    # Offer a one-tap route to the loader that fixes the worst thing, when one
    # of the allowlisted actions actually covers it.
    buttons = None
    worst = sorted(
        (r for r in rows if r["status"] in ("overdue", "due")),
        key=lambda r: -(r["days_overdue"] or 0),
    )
    for r in worst:
        action = actions_for_source(r["source_name"])
        if action:
            buttons = [(f"Fix: {action.label}", f"propose:{action.key}")]
            break

    tg.send(msg, buttons)
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
    gate = ApprovalGate()

    if args.once:
        sent = proactive_check(tg, state, force=True)
        print("sent" if sent else "nothing to report")
        return 0

    print(f"db engineer up. polling telegram, checking every {args.interval}h.")
    tg.send("\U0001f527 <b>DB engineer online</b>\nSend /help for what I can do.\n"
            "<i>I never write without your approval.</i>")

    next_check = 0.0
    while True:
        if time.time() >= next_check:
            try:
                proactive_check(tg, state)
            except Exception as e:
                print(f"proactive check failed: {e}", file=sys.stderr)
            next_check = time.time() + args.interval * 3600

        for event in tg.poll():
            # Anyone can message a Telegram bot once they find it. Only the
            # configured chat gets answers, and only it can approve anything.
            if event["chat"] != tg.chat_id:
                audit("rejected_sender", chat=event["chat"], kind=event["kind"])
                print(f"ignoring {event['kind']} from {event['chat']}", file=sys.stderr)
                continue

            if event["kind"] == "callback":
                print(f"< [tap] {event['data']}")
                try:
                    note = handle_callback(gate, event["data"], event["chat"], tg)
                except Exception as e:
                    note = "failed"
                    tg.send(f"That failed: {_esc(e)}")
                tg.answer_callback(event["id"], note)
                continue

            print(f"< {event['text']}")
            reply, buttons = handle(event["text"], gate, event["chat"])
            tg.send(reply, buttons)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped")
        sys.exit(0)
