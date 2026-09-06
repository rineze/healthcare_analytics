"""
db_actions.py

The fixed set of things the engineer is allowed to do, and the approval gate
in front of them.

Two separate ideas, deliberately kept apart:

    the allowlist   what CAN run. Defined here, in code, reviewed in a PR.
    the approval    whether a specific run happens right now. Yours, over
                    Telegram, one tap.

The approval gate does not decide what is possible, only what proceeds. So a
compromised Telegram account, or a message that is not really from you, cannot
make the engineer run something that is not already on this list. There is no
path from a chat message to an arbitrary shell command or arbitrary SQL.

Every approval is single use and expires.
"""

import json
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIT_LOG = Path.home() / ".db_engineer_audit.jsonl"

# How long an approval button stays live. Long enough to walk back to your desk,
# short enough that yesterday's unanswered prompt cannot fire today.
APPROVAL_TTL_SECONDS = 900

# Loaders move millions of rows. Well past this and something is wrong.
ACTION_TIMEOUT_SECONDS = 1800


@dataclass(frozen=True)
class Action:
    key: str
    label: str            # button text
    detail: str           # what it does, in one line
    writes: str           # what it touches, shown before you approve
    argv: tuple           # the exact command, no shell, no interpolation
    trigger_sources: tuple = ()   # ledger sources this action refreshes


# The allowlist. Adding an entry is a code change.
#
# argv is a fixed tuple run without a shell, so nothing from a Telegram message
# is ever interpolated into a command.
ACTIONS: Dict[str, Action] = {
    "refresh_stats": Action(
        key="refresh_stats",
        label="Refresh ledger stats",
        detail="Recount rows and load timestamps from the source tables",
        writes="meta.data_sources (counts and timestamps only)",
        argv=(sys.executable, "-c",
              "import sys; sys.path.insert(0, '.');"
              "from healthcare_db import get_connection;"
              "c = get_connection();"
              "cur = c.cursor();"
              "cur.execute('SELECT meta.refresh_data_source_stats(true)');"
              "print('refreshed', cur.fetchone()[0], 'sources');"
              "c.commit()"),
    ),
    "load_ma": Action(
        key="load_ma",
        label="Load MA enrollment",
        detail="Pull the current CMS monthly MA files and upsert them",
        writes="drinf.ma_cpsc_enrollment, ma_plan_directory, ma_county_penetration",
        argv=(sys.executable, "ma-dashboard/load_ma_data.py"),
        trigger_sources=("ma_cpsc_enrollment", "ma_county_penetration",
                         "ma_plan_directory"),
    ),
    "load_mpfs": Action(
        key="load_mpfs",
        label="Load MPFS RVU",
        detail="Load PFS relative value files from PFS_DATA_DIR",
        writes="drinf.mpfs_rvu",
        argv=(sys.executable, "pfs-analysis/load_mpfs.py"),
        trigger_sources=("mpfs_rvu",),
    ),
    "load_gpci": Action(
        key="load_gpci",
        label="Load MPFS GPCI",
        detail="Load geographic practice cost indices from PFS_DATA_DIR",
        writes="drinf.mpfs_gpci",
        argv=(sys.executable, "pfs-analysis/load_gpci.py"),
        trigger_sources=("mpfs_gpci",),
    ),
    "load_utilization": Action(
        key="load_utilization",
        label="Load Medicare utilization",
        detail="Download and load the utilization-by-geography files",
        writes="drinf.medicare_utilization",
        argv=(sys.executable, "pfs-analysis/load_utilization.py"),
        trigger_sources=("medicare_utilization",),
    ),
}


def actions_for_source(source_name: str):
    """Which allowlisted action, if any, refreshes a given ledger source."""
    for action in ACTIONS.values():
        if source_name in action.trigger_sources:
            return action
    return None


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------

@dataclass
class Pending:
    token: str
    action_key: str
    chat_id: str
    created_at: float = field(default_factory=time.time)

    def expired(self, now=None):
        return (now or time.time()) - self.created_at > APPROVAL_TTL_SECONDS


class ApprovalGate:
    """Issues one-shot, expiring approval tokens bound to a chat id."""

    def __init__(self):
        self._pending: Dict[str, Pending] = {}

    def propose(self, action_key: str, chat_id: str) -> str:
        if action_key not in ACTIONS:
            raise KeyError(f"{action_key} is not an allowlisted action")
        self._sweep()
        # 16 hex chars keeps callback_data well under Telegram's 64 byte cap.
        token = secrets.token_hex(8)
        self._pending[token] = Pending(token, action_key, str(chat_id))
        return token

    def redeem(self, token: str, chat_id: str):
        """Consume a token. Returns (action, None) or (None, reason).

        Ownership is checked BEFORE the token is consumed. Popping first would
        mean a redeem attempt from the wrong chat destroys a valid pending
        approval, which is a denial of service on your own approvals even
        though it could never run the action.
        """
        self._sweep()

        pending = self._pending.get(token)
        if pending is None:
            return None, "that approval is no longer valid"

        if pending.chat_id != str(chat_id):
            # Leave it in place. It is not this caller's to consume.
            return None, "that approval was not issued to you"

        # From here the token is spent either way.
        del self._pending[token]

        if pending.expired():
            return None, "that approval expired"

        return ACTIONS[pending.action_key], None

    def cancel(self, token: str, chat_id: str) -> bool:
        pending = self._pending.get(token)
        if pending and pending.chat_id == str(chat_id):
            del self._pending[token]
            return True
        return False

    def _sweep(self):
        now = time.time()
        for tok in [t for t, p in self._pending.items() if p.expired(now)]:
            del self._pending[tok]

    def __len__(self):
        self._sweep()
        return len(self._pending)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def audit(event: str, **fields):
    """Append-only local record of every proposal, approval and run."""
    row = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    try:
        with AUDIT_LOG.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception as e:
        print(f"audit write failed: {e}", file=sys.stderr)
    print(f"[audit] {row}", file=sys.stderr)


def run_action(action: Action):
    """Run an allowlisted action. Returns (ok, output)."""
    audit("run_start", action=action.key, argv=list(action.argv))
    started = time.time()
    try:
        proc = subprocess.run(
            list(action.argv),
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=ACTION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        audit("run_timeout", action=action.key)
        return False, f"timed out after {ACTION_TIMEOUT_SECONDS}s"
    except Exception as e:
        audit("run_error", action=action.key, error=str(e))
        return False, f"could not start: {e}"

    elapsed = int(time.time() - started)
    ok = proc.returncode == 0
    audit("run_finish", action=action.key, ok=ok,
          returncode=proc.returncode, seconds=elapsed)

    stream = proc.stdout if ok else (proc.stderr or proc.stdout)
    tail = "\n".join(stream.strip().splitlines()[-15:]) or "(no output)"
    return ok, f"{tail}\n\n({elapsed}s)"
