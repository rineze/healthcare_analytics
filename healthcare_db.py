"""
healthcare_db.py

The one place database connections are defined for this repo.

Every app and loader imports from here instead of building its own DB_CONFIG
dict. See docs/DATABASE_CONNECTIONS.md for what Supabase, local Postgres, and
psql each actually are.

Usage
-----
    from healthcare_db import get_connection

    conn = get_connection()             # follow the standard fallback chain
    conn = get_connection("local")      # force local Postgres
    conn = get_connection("supabase")   # force Supabase

Resolution order when no target is given:

    1. USE_LOCAL=true in .env (and LOCAL_HOST set)  -> local Postgres
    2. Streamlit secrets [database]                 -> whatever they point at
    3. SUPABASE_* environment variables             -> Supabase
    4. local defaults (127.0.0.1)                   -> local Postgres

Run it directly to test connectivity without starting an app:

    python healthcare_db.py
    python healthcare_db.py supabase
"""

import os
from pathlib import Path

import psycopg2

__all__ = [
    "loader_connection",
    "DatabaseConfigError",
    "get_db_config",
    "get_connection",
    "describe_target",
    "resolve_target",
    "REPO_ROOT",
]

REPO_ROOT = Path(__file__).resolve().parent

# Connection keys use libpq's real keyword, "dbname". psycopg2 also accepts
# "database" as an alias, which is why the old copies drifted between the two.
# Standardizing here so they stop drifting.
_LIBPQ_KEYS = ("host", "port", "dbname", "user", "password")


class DatabaseConfigError(RuntimeError):
    """Raised when a connection target is asked for but isn't configured."""


# ---------------------------------------------------------------------------
# .env loading (done once, here, instead of eight times across the repo)
# ---------------------------------------------------------------------------

def _load_dotenv_once():
    """Load the repo-root .env, falling back to one in the working directory."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv missing: env vars may still be set
        return None

    for candidate in (REPO_ROOT / ".env", Path.cwd() / ".env"):
        if candidate.is_file():
            load_dotenv(candidate)
            return candidate
    return None


_DOTENV_PATH = _load_dotenv_once()


# ---------------------------------------------------------------------------
# Config builders
# ---------------------------------------------------------------------------

def _local_config():
    return {
        "host": os.getenv("LOCAL_HOST", "127.0.0.1"),
        "port": int(os.getenv("LOCAL_PORT", "5432")),
        "dbname": os.getenv("LOCAL_DATABASE", "postgres"),
        "user": os.getenv("LOCAL_USER", "postgres"),
        "password": os.getenv("LOCAL_PASSWORD", ""),
    }


def _supabase_config():
    """Build the Supabase config, or explain clearly what's missing.

    The old load_price_transparency.py read these vars with no defaults and no
    checks, so a missing password surfaced as a confusing libpq error much
    later. Fail here instead, with the variable name in the message.
    """
    missing = [v for v in ("SUPABASE_HOST", "SUPABASE_PASSWORD") if not os.getenv(v)]
    if missing:
        raise DatabaseConfigError(
            "Supabase connection requested but {} not set.\n"
            "Add it to {}/.env (see docs/DATABASE_CONNECTIONS.md section 3).".format(
                " and ".join(missing), REPO_ROOT
            )
        )

    return {
        "host": os.getenv("SUPABASE_HOST"),
        "port": int(os.getenv("SUPABASE_PORT", "5432")),
        "dbname": os.getenv("SUPABASE_DATABASE", "postgres"),
        "user": os.getenv("SUPABASE_USER", "postgres"),
        "password": os.getenv("SUPABASE_PASSWORD"),
    }


def _streamlit_secrets_config():
    """Read st.secrets["database"] if we're running under Streamlit.

    Returns None when Streamlit isn't installed, isn't running, or has no
    [database] block. Loaders run as plain scripts, so this has to stay
    optional rather than an import-time dependency.
    """
    try:
        import streamlit as st
    except ImportError:
        return None

    try:
        section = st.secrets["database"]
        return {
            "host": section["host"],
            "port": int(section["port"]),
            # secrets.toml uses "database"; map it onto the libpq keyword.
            "dbname": section["database"],
            "user": section["user"],
            "password": section["password"],
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def _resolve(target=None):
    """Return (source_label, config) for the requested or auto-detected target."""
    if target == "local":
        return "local (explicit)", _local_config()
    if target == "supabase":
        return "supabase (explicit)", _supabase_config()
    if target is not None:
        raise ValueError(
            "Unknown target {!r}. Use None (auto), 'local', or 'supabase'.".format(target)
        )

    # Auto: the four-step chain documented above.
    if os.getenv("USE_LOCAL", "false").strip().lower() == "true" and os.getenv("LOCAL_HOST"):
        return "local (USE_LOCAL=true)", _local_config()

    secrets = _streamlit_secrets_config()
    if secrets is not None:
        return "streamlit secrets", secrets

    if os.getenv("SUPABASE_HOST"):
        return "supabase (SUPABASE_* env)", _supabase_config()

    return "local (default fallback)", _local_config()


def resolve_target(target=None):
    """Name of the source that would be used, e.g. 'supabase (SUPABASE_* env)'."""
    return _resolve(target)[0]


def get_db_config(target=None):
    """Connection kwargs for psycopg2.connect(). Keys are libpq keywords."""
    return _resolve(target)[1]


def describe_target(target=None):
    """One-line, password-free description of where a connection would go."""
    label, cfg = _resolve(target)
    return "{} -> {}@{}:{}/{}".format(
        label, cfg["user"], cfg["host"], cfg["port"], cfg["dbname"]
    )


def get_connection(target=None, **overrides):
    """Open a psycopg2 connection.

    target:    None for the standard chain, or 'local' / 'supabase' to force one.
    overrides: any libpq keyword, e.g. connect_timeout=10.
    """
    cfg = dict(get_db_config(target))
    cfg.update(overrides)
    return psycopg2.connect(**cfg)


def loader_connection(target=None, **overrides):
    """Connection for a bulk loader, with a `--target` flag and a loud banner.

    Loaders write millions of rows, so before they do anything they should say
    out loud which database they are about to write to. Accepts an optional
    `--target local` / `--target supabase` on the command line, which beats the
    resolution chain, so you never have to edit a file to redirect a load.
    """
    import sys

    argv = sys.argv[1:]
    if "--target" in argv:
        idx = argv.index("--target")
        if idx + 1 >= len(argv):
            raise DatabaseConfigError("--target needs a value: local or supabase")
        target = argv[idx + 1]

    print("=" * 70)
    print("WRITING TO: {}".format(describe_target(target)))
    print("=" * 70)
    return get_connection(target, **overrides)


# ---------------------------------------------------------------------------
# Connectivity check: python healthcare_db.py [local|supabase]
# ---------------------------------------------------------------------------

def _main(argv):
    target = argv[1] if len(argv) > 1 else None

    print(".env:    {}".format(_DOTENV_PATH or "not found (using environment only)"))
    try:
        print("target:  {}".format(describe_target(target)))
    except DatabaseConfigError as exc:
        print("ERROR:   {}".format(exc))
        return 1

    try:
        with get_connection(target, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select current_database(), current_user, "
                    "split_part(version(), ' on ', 1), "
                    "pg_size_pretty(pg_database_size(current_database()))"
                )
                db, user, version, size = cur.fetchone()
    except Exception as exc:
        print("ERROR:   could not connect: {}".format(exc))
        return 1

    print("ok:      connected to {} as {}".format(db, user))
    print("         {}, {} on disk".format(version, size))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv))
