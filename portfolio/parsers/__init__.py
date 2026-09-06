"""Broker export parsers. Each returns the same normalized ParseResult."""

from parsers import empower, fidelity, robinhood
from parsers.common import ParseResult

# Registry keyed by platform. load.py routes files here after sniffing.
PARSERS = {
    "fidelity": fidelity,
    "empower": empower,
    "robinhood": robinhood,
}

__all__ = ["PARSERS", "ParseResult", "fidelity", "empower", "robinhood"]
