"""
db_export.py

Turns a question into a file you can open.

The engineer runs SELECTs freely, because a SELECT through a read-only role
cannot damage anything: a wrong query gives a wrong answer, which you can see
and correct. Writes are the dangerous half, and those stay allowlisted and
approval-gated in db_actions.py.

Free does not mean unchecked, though. Everything here still goes through
guard_select(), because the query text may have been written by a model from a
message typed on a phone, and defence in depth is cheap.
"""

import csv
import os
import re
import smtplib
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from healthcare_db import get_connection  # noqa: E402

# Where exports land when no folder is given.
EXPORT_DIR = Path(os.getenv("EXPORT_DIR", str(Path.home() / "db_exports")))

# Guard rails on result size. Telegram caps uploads at 50MB, and nobody opens a
# three million row spreadsheet anyway.
MAX_ROWS = 200_000
XLSX_MAX_ROWS = 100_000       # beyond this, xlsx gets slow and enormous

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|"
    r"copy|vacuum|reindex|call|do)\b",
    re.IGNORECASE,
)


class UnsafeQuery(ValueError):
    pass


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)     # /* block */
    sql = re.sub(r"--[^\n]*", " ", sql)                   # -- line
    return sql


def guard_select(sql: str) -> str:
    """Allow exactly one read-only statement. Raise UnsafeQuery otherwise.

    The database role cannot write regardless, so this is a second lock on the
    same door. It exists to fail loudly and early rather than let a malformed
    query reach the server at all.
    """
    if not sql or not sql.strip():
        raise UnsafeQuery("empty query")

    body = _strip_sql_comments(sql).strip().rstrip(";").strip()

    if not body:
        raise UnsafeQuery("query is only comments")

    # One statement. A second semicolon means something is being smuggled.
    if ";" in body:
        raise UnsafeQuery("only a single statement is allowed")

    if not re.match(r"^\s*(select|with)\b", body, re.IGNORECASE):
        raise UnsafeQuery("only SELECT and WITH queries are allowed")

    hit = _FORBIDDEN.search(body)
    if hit:
        raise UnsafeQuery(f"'{hit.group(0)}' is not allowed in a read query")

    return body


def run_select(sql: str, limit: int = MAX_ROWS):
    """Run a guarded SELECT. Returns (columns, rows, truncated)."""
    body = guard_select(sql)

    with get_connection(connect_timeout=30) as conn:
        # Belt and braces: even a read-only role should not sit in a write txn.
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM ({body}) _q LIMIT {int(limit) + 1}")
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()

    truncated = len(rows) > limit
    return cols, rows[:limit], truncated


def _stamped_path(name: str, ext: str, folder=None) -> Path:
    folder = Path(folder) if folder else EXPORT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "export"
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    return folder / f"{safe}_{stamp}.{ext}"


def write_csv(cols, rows, name, folder=None) -> Path:
    path = _stamped_path(name, "csv", folder)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerows(rows)
    return path


def write_xlsx(cols, rows, name, folder=None) -> Path:
    from openpyxl import Workbook

    path = _stamped_path(name, "xlsx", folder)
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("data")
    ws.append(list(cols))
    for r in rows:
        # openpyxl cannot serialize Decimal/date subclasses in write-only mode
        # for every type, so normalize anything exotic to str.
        ws.append([v if isinstance(v, (int, float, str, type(None))) else str(v)
                   for v in r])
    wb.save(path)
    return path


def export(sql: str, name: str, fmt: str = "auto", folder=None):
    """Run a query and write it to a file. Returns (path, rowcount, truncated)."""
    cols, rows, truncated = run_select(sql)

    if fmt == "auto":
        fmt = "xlsx" if len(rows) <= XLSX_MAX_ROWS else "csv"

    if fmt == "xlsx":
        path = write_xlsx(cols, rows, name, folder)
    elif fmt == "csv":
        path = write_csv(cols, rows, name, folder)
    else:
        raise ValueError(f"unknown format {fmt}")

    return path, len(rows), truncated


# ---------------------------------------------------------------------------
# Email delivery (optional; Telegram is the default and needs no setup)
# ---------------------------------------------------------------------------

def email_configured() -> bool:
    return all(os.getenv(v) for v in
               ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "EXPORT_EMAIL_TO"))


def email_file(path: Path, subject: str, body: str = "") -> str:
    """Mail an export. Returns a status string."""
    if not email_configured():
        missing = [v for v in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD",
                               "EXPORT_EMAIL_TO") if not os.getenv(v)]
        return f"email not configured, missing {', '.join(missing)}"

    msg = EmailMessage()
    msg["From"] = os.getenv("SMTP_USER")
    msg["To"] = os.getenv("EXPORT_EMAIL_TO")
    msg["Subject"] = subject
    msg.set_content(body or f"Attached: {path.name}")

    data = path.read_bytes()
    msg.add_attachment(data, maintype="application", subtype="octet-stream",
                       filename=path.name)

    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    try:
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.starttls()
            s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD"))
            s.send_message(msg)
        return f"emailed to {os.getenv('EXPORT_EMAIL_TO')}"
    except Exception as e:
        return f"email failed: {e}"
