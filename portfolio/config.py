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

VALID_PLATFORMS = {"fidelity", "empower", "robinhood", "manual"}
# roth_401k is distinct from 401k: a plan can hold both, and which is which
# decides whether the money is ever taxed again.
VALID_TAX_TYPES = {"taxable", "traditional_ira", "roth_ira",
                   "401k", "roth_401k", "hsa", "529"}
VALID_ASSET_CLASSES = {"us_equity", "intl_equity", "bond", "cash", "alt", "unknown"}

DEFAULT_THRESHOLDS = {
    "concentration_warn_pct": 0.10,
    "tlh_min_loss_usd": 500,
    "long_term_watch_days": 60,
    "earnings_watch_days": 30,
    # A value from the prior close is not stale. Only flag a position when its
    # value predates the snapshot by more than this, or a routine one-day lag
    # between a Friday close and a Saturday snapshot gets reported as a data
    # quality problem and drowns out the accounts that really are months old.
    "stale_after_days": 7,
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

    # Exposure groups collapse several tickers that track the same thing into one
    # exposure. Without them, holding the S&P 500 through a 401k pool, VOO, and
    # FXAIX reads as three comfortable-looking positions instead of one large one.
    groups = cfg.get("exposure_groups") or {}
    seen_symbols: dict[str, str] = {}
    for group, symbols in groups.items():
        if not isinstance(symbols, list) or not symbols:
            raise ConfigError(f"exposure_groups['{group}'] must be a non-empty list.")
        for sym in symbols:
            sym = str(sym).strip().upper()
            if sym in seen_symbols and seen_symbols[sym] != group:
                raise ConfigError(
                    f"Symbol {sym} appears in two exposure groups "
                    f"('{seen_symbols[sym]}' and '{group}'). A symbol maps to one "
                    "exposure, or the weights would double count."
                )
            seen_symbols[sym] = group

    overrides = {
        str(k).strip().upper(): str(v).strip()
        for k, v in (cfg.get("symbol_overrides") or {}).items()
    }

    bad = {
        s: c for s, c in (cfg.get("asset_class_overrides") or {}).items()
        if c not in VALID_ASSET_CLASSES
    }
    if bad:
        raise ConfigError(
            f"asset_class_overrides has invalid classes: {bad}. "
            f"Valid: {sorted(VALID_ASSET_CLASSES)}"
        )

    cfg["accounts"] = accounts
    cfg["targets"] = targets
    cfg["exposure_groups"] = {g: [str(x).strip().upper() for x in syms]
                              for g, syms in groups.items()}
    cfg["symbol_to_exposure"] = seen_symbols
    cfg["symbol_overrides"] = overrides
    cfg["thresholds"] = {**DEFAULT_THRESHOLDS, **(cfg.get("thresholds") or {})}
    return cfg


def exposure_for(symbol: str, cfg: dict) -> str:
    """Exposure group a symbol belongs to, or the symbol itself if ungrouped."""
    return cfg.get("symbol_to_exposure", {}).get(str(symbol).upper(), symbol)


def resolve_symbol(symbol: str, cfg: dict) -> str:
    """Ticker to send to the market data provider.

    Some symbols in a portfolio are not the symbol a data provider knows. Crypto
    on Robinhood shows as BTC but yfinance wants BTC-USD, and a 401k collective
    trust has no public ticker at all so it has to be priced off its retail
    equivalent.
    """
    return cfg.get("symbol_overrides", {}).get(str(symbol).upper(), symbol)


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
    if c["exposure_groups"]:
        print("exposure groups:")
        for g, syms in c["exposure_groups"].items():
            print(f"  {g:<14} {', '.join(syms)}")
    if c["symbol_overrides"]:
        print(f"symbol overrides: {c['symbol_overrides']}")
    print(f"thresholds: {c['thresholds']}")
