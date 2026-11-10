"""What a fixed cohort of large Hyperliquid accounts holds in one token.

The cohort is chosen before anything is read and does not depend on how its members performed (contract section 21: a
leaderboard of winners would build survivorship in before a model runs); this module only reads what each member holds now. A
wallet's state is Hyperliquid's own ``clearinghouseState`` answer, a public, keyless call: its open perpetual positions with
their signed size and value. For one token the cohort is summarised as counts and notional (long, short, the net, and the
largest position's share of the gross), never as a name or a call to copy.

Limits, said wherever this is shown: one venue; a wallet is not a person (a vault, a market maker and a directional trader look
alike here, and no role is inferred); an account that holds no position in the token says nothing about its view; the cohort is
a sample of the largest accounts, not of the market. The evidence that carries these numbers says it has not been shown to help
a decision.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

MAX_POSITIONS: Final = 500
_WALLET: Final = re.compile(r"0x[0-9a-fA-F]{40}")
COINS: Final = {"BTC": "BTC", "ETH": "ETH", "SOL": "SOL"}  # a candidate's symbol to Hyperliquid's name of its perpetual


class WhalesError(ValueError):
    """A wallet answer that is not Hyperliquid's."""


@dataclass(frozen=True)
class Wallet:
    address: str
    account_value: Decimal  # in USD
    positions: Mapping[str, Decimal]  # coin to signed notional in USD: long positive, short negative


@dataclass(frozen=True)
class Cohort:
    coin: str
    wallets: int  # polled
    holding: int  # of them, with a position in the coin
    long: int
    short: int
    gross_long: Decimal
    gross_short: Decimal
    net: Decimal
    top_share: Decimal  # the largest position's share of the gross, 0 to 1; zero when no one holds it


def valid_wallet(address: str) -> bool:
    return _WALLET.fullmatch(address) is not None


def _dec(value: object, what: str) -> Decimal:
    try:
        out = Decimal(str(value))
    except InvalidOperation:
        raise WhalesError(f"{what} is a decimal number") from None
    if not out.is_finite():
        raise WhalesError(f"{what} is a finite number")
    return out


def parse_state(address: str, body: bytes) -> Wallet:
    """One wallet's ``clearinghouseState``: its account value and its open positions as signed notional."""
    if not valid_wallet(address):
        raise WhalesError("a wallet is a 0x address of 40 hexadecimal digits")
    try:
        doc = json.loads(body.decode("utf-8"))
        value = doc["marginSummary"]["accountValue"]
        entries = doc["assetPositions"]
    except (UnicodeDecodeError, ValueError, KeyError, TypeError):
        raise WhalesError("the answer is not a Hyperliquid clearinghouse state") from None
    if not isinstance(entries, list) or len(entries) > MAX_POSITIONS:
        raise WhalesError("the positions are a list of at most 500")
    positions: dict[str, Decimal] = {}
    for entry in entries:
        try:
            p = entry["position"]
            coin, size, notional = p["coin"], _dec(p["szi"], "a size"), _dec(p["positionValue"], "a position value")
        except (KeyError, TypeError):
            raise WhalesError("a position has a coin, a size and a value") from None
        if not isinstance(coin, str):
            raise WhalesError("a coin is a name")
        positions[coin] = positions.get(coin, Decimal(0)) + (notional if size >= 0 else -notional)
    return Wallet(address.lower(), _dec(value, "the account value"), positions)


def cohort(wallets: Iterable[Wallet], coin: str) -> Cohort:
    """What the wallets hold in ``coin``: counts and notional."""
    polled = list(wallets)
    held = [w.positions[coin] for w in polled if w.positions.get(coin, Decimal(0)) != 0]
    longs = [x for x in held if x > 0]
    shorts = [-x for x in held if x < 0]
    gross_long, gross_short = sum(longs, Decimal(0)), sum(shorts, Decimal(0))
    gross = gross_long + gross_short
    top = max((abs(x) for x in held), default=Decimal(0))
    return Cohort(
        coin,
        len(polled),
        len(held),
        len(longs),
        len(shorts),
        gross_long,
        gross_short,
        gross_long - gross_short,
        (top / gross) if gross > 0 else Decimal(0),
    )
