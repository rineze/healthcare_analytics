"""Broker export parsers. Each returns the same normalized ParseResult."""

from parsers import empower, fidelity, manual, robinhood
from parsers.common import ParseResult

# Registry keyed by platform. load.py routes files here after sniffing.
# `manual` is checked first: it identifies on an account_id column that no broker
# export has, so it never steals a file from a real parser.
PARSERS = {
    "manual": manual,
    "fidelity": fidelity,
    "empower": empower,
    "robinhood": robinhood,
}

__all__ = ["PARSERS", "ParseResult", "fidelity", "empower", "robinhood", "manual"]
