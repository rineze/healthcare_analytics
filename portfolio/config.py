"""
config.py — Load and validate portfolio/config.yaml.

config.yaml holds the things no broker export contains: which account is taxable
vs Roth vs 401k, what your target allocation is, and where the reporting
thresholds sit. It is gitignored. config.example.yaml is the committed template.
"""

from __future__ import annotations

from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).parent / "config.yaml"
EXAMPLE_PATH = Path(__file__).parent / "config.example.yaml"

VALID_PLATFORMS = {"fidelity", "empower", "robinhood"}
VALID_TAX_TYPES = {"taxable", "traditional_ira", "roth_ira", "401k", "hsa", "529"}

DEFAULT_THRESHOLDS = {
    "concentration_warn_pct": 0.10,
    "tlh_min_loss_usd": 500,
    "long_term_watch_days": 60,
    "earnings_watch_days": 30,
}


class ConfigError(RuntimeError):
    """Raised when config.yaml is missing or invalid."""


def load_config(path: Path | None = None) -> dict:
    """Read config.yaml, validate it, and fill in threshold defaults."""
    path = path or CONFIG_PATH
    if not path.exists():
        raise ConfigError(
            f"{path} not found.\n"
            f"Copy {EXAMPLE_PATH.name} to config.yaml and fill in your accounts.\n"
            "The tax_type on each account is the one field nothing else can supply."
        )

    with open(path) as f:
        cfg = yaml.safe_load(f) or {}

    accounts = cfg.get("accounts") or []
    if not accounts:
        raise ConfigError(f"{path} has no accounts defined.")

    seen = set()
    for i, acct in enumerate(accounts):
        where = f"accounts[{i}]"
        for field in ("account_id", "platform", "tax_type"):
            if not acct.get(field):
                raise ConfigError(f"{where} is missing required field '{field}'.")

        aid = acct["account_id"]
        if aid in seen:
            raise ConfigError(f"Duplicate account_id '{aid}'.")
        seen.add(aid)

        if acct["platform"] not in VALID_PLATFORMS:
            raise ConfigError(
                f"{where} platform '{acct['platform']}' is not one of "
                f"{sorted(VALID_PLATFORMS)}."
            )
        if acct["tax_type"] not in VALID_TAX_TYPES:
            raise ConfigError(
                f"{where} tax_type '{acct['tax_type']}' is not one of "
                f"{sorted(VALID_TAX_TYPES)}."
            )

        mask = acct.get("mask4")
        if mask is not None:
            mask = str(mask).strip()
            if len(mask) != 4 or not mask.isdigit():
                raise ConfigError(
                    f"{where} mask4 must be exactly the last 4 digits of the account "
                    f"number, got '{mask}'. Never put the full number in this file."
                )
            acct["mask4"] = mask

    targets = cfg.get("targets") or {}
    if targets:
        total = sum(float(v) for v in targets.values())
        if abs(total - 1.0) > 0.001:
            raise ConfigError(
                f"targets must sum to 1.0, got {total:.4f}. "
                "Drift reporting is meaningless otherwise."
            )

    cfg["accounts"] = accounts
    cfg["targets"] = targets
    cfg["thresholds"] = {**DEFAULT_THRESHOLDS, **(cfg.get("thresholds") or {})}
    return cfg


def accounts_by_mask(cfg: dict) -> dict[str, dict]:
    """Map last-four -> account dict, for resolving accounts seen in export files."""
    return {a["mask4"]: a for a in cfg["accounts"] if a.get("mask4")}


def account_ids(cfg: dict) -> set[str]:
    return {a["account_id"] for a in cfg["accounts"]}


def sync_accounts_to_db(cfg: dict) -> int:
    """Push config.yaml accounts into portfolio.accounts.

    config.yaml is the source of truth; the table exists so foreign keys and SQL
    joins work.
    """
    from db import upsert

    rows = [
        {
            "account_id": a["account_id"],
            "platform": a["platform"],
            "account_label": a.get("label"),
            "tax_type": a["tax_type"],
            "mask4": a.get("mask4"),
        }
        for a in cfg["accounts"]
    ]
    return upsert("accounts", rows, ["account_id"])


if __name__ == "__main__":
    c = load_config()
    print(f"{len(c['accounts'])} accounts configured:")
    for a in c["accounts"]:
        label = a.get("label") or a["account_id"]
        mask = f" (...{a['mask4']})" if a.get("mask4") else ""
        print(f"  {a['account_id']:<20} {a['platform']:<10} {a['tax_type']:<16} {label}{mask}")
    print(f"\ntargets:    {c['targets'] or '(none set)'}")
    print(f"thresholds: {c['thresholds']}")
