#!/usr/bin/env python3
"""
db_observer.py

The read-only observer. Checks what data is stale and messages you about it.

This is the piece that runs OUTSIDE any Claude session, on a schedule, forever.
It reads meta.v_data_freshness, groups overdue datasets by the loader that owns
them, and sends a Telegram message. It never writes to the database.

Usage
-----
    python scripts/db_observer.py --dry-run     # print, don't send
    python scripts/db_observer.py               # send if anything is overdue
    python scripts/db_observer.py --always      # send even when all clear

Environment
-----------
    Database:  the usual SUPABASE_* vars (see docs/DATABASE_CONNECTIONS.md).
               Use the read-only db_observer role, not the postgres superuser.
    Telegram:  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

Exit codes are always 0 unless the check itself failed, so a stale dataset does
not show up as a red build. Staleness is news, not an error.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from healthcare_db import get_connection, describe_target  # noqa: E402

TELEGRAM_MAX_CHARS = 4096

FRESHNESS_SQL = """
SELECT source_name, target, status, refresh_cadence, url_stability,
       days_overdue, days_since_load, record_count, loader_script
FROM meta.v_data_freshness
WHERE status IN ('overdue', 'due', 'never_loaded', 'untracked')
"""

# Cheap liveness facts. Deliberately not pg_stat_activity, which needs
# pg_read_all_stats to report anything useful for a non-superuser role.
HEALTH_SQL = """
SELECT pg_size_pretty(pg_database_size(current_database())),
       (SELECT count(*) FROM meta.data_sources)
"""


def collect():
    """Read freshness and health. Returns (rows, db_size, total_sources)."""
    with get_connection(connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute(FRESHNESS_SQL)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

            cur.execute(HEALTH_SQL)
            db_size, total_sources = cur.fetchone()

    return rows, db_size, total_sources


def group_by_loader(rows):
    """Group datasets by the loader that owns them.

    The three MA tables share one loader, so they are one action item, not
    three near-identical lines on a phone screen.
    """
    groups = {}
    for r in rows:
        key = r["loader_script"] or "(no loader in repo)"
        groups.setdefault(key, []).append(r)
    return groups


def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def build_message(rows, db_size, total_sources):
    """Format for a phone. Returns None when there is nothing worth sending."""
    overdue = [r for r in rows if r["status"] == "overdue"]
    due = [r for r in rows if r["status"] == "due"]
    other = [r for r in rows if r["status"] in ("never_loaded", "untracked")]

    if not overdue and not due and not other:
        return None

    if overdue:
        head = f"\U0001f534 <b>{_plural(len(overdue), 'dataset')} overdue</b>"
    elif due:
        head = f"\U0001f7e1 <b>{_plural(len(due), 'dataset')} due</b>"
    else:
        head = "\U0001f7e1 <b>Data needs attention</b>"

    lines = [head, ""]

    for status_rows, label in ((overdue, None), (due, "Due soon"), (other, "Not tracked")):
        if not status_rows:
            continue
        if label:
            lines.append(f"<b>{label}</b>")

        groups = group_by_loader(status_rows)
        # Worst first. Sorting by loader name would bury the most overdue item
        # under whatever happened to sort alphabetically first.
        ordered = sorted(
            groups.items(),
            key=lambda kv: (-max((i["days_overdue"] or 0) for i in kv[1]), kv[0]),
        )

        for loader, items in ordered:
            names = ", ".join(sorted(i["source_name"] for i in items))
            worst = max((i["days_overdue"] or 0) for i in items)
            cadence = items[0]["refresh_cadence"]

            lines.append(f"• <b>{names}</b>")
            if worst:
                lines.append(f"  {worst}d overdue, {cadence}")
            else:
                lines.append(f"  {items[0]['status']}, {cadence}")

            # url_stability is the automation hint: unstable means the file has
            # to be hunted down by hand rather than fetched by a script. Only
            # flag the members that are actually unstable, since a group can
            # mix stabilities (the PPEF sub-files do).
            unstable = sorted(i["source_name"] for i in items
                              if i["url_stability"] == "unstable")
            if unstable:
                who = "" if len(unstable) == len(items) else f" ({', '.join(unstable)})"
                lines.append(f"  ⚠ URL changes each release{who}, needs a lookup")

            lines.append(f"  <code>{loader}</code>")
        lines.append("")

    lines.append(f"<i>{total_sources} sources tracked, db {db_size}</i>")

    msg = "\n".join(lines)
    if len(msg) > TELEGRAM_MAX_CHARS:
        msg = msg[: TELEGRAM_MAX_CHARS - 20].rstrip() + "\n<i>(truncated)</i>"
    return msg


def send_telegram(text):
    """POST to the Telegram bot API. Returns True on success."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must both be set",
              file=sys.stderr)
        return False

    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read()).get("ok", False)
    except urllib.error.HTTPError as e:
        # Telegram puts the useful reason in the body, not the status line.
        print(f"ERROR: Telegram returned {e.code}: {e.read().decode()[:300]}",
              file=sys.stderr)
        return False
    except Exception as e:
        print(f"ERROR: Telegram request failed: {e}", file=sys.stderr)
        return False


def main():
    ap = argparse.ArgumentParser(description="Report stale datasets to Telegram.")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the message instead of sending it")
    ap.add_argument("--always", action="store_true",
                    help="send a message even when nothing needs attention")
    args = ap.parse_args()

    print(f"reading: {describe_target()}", file=sys.stderr)

    rows, db_size, total_sources = collect()
    msg = build_message(rows, db_size, total_sources)

    if msg is None:
        if not args.always:
            print("all clear, nothing to send", file=sys.stderr)
            return 0
        msg = f"✅ <b>All data current</b>\n\n<i>{total_sources} sources tracked, db {db_size}</i>"

    if args.dry_run:
        print(msg)
        return 0

    return 0 if send_telegram(msg) else 1


if __name__ == "__main__":
    sys.exit(main())
