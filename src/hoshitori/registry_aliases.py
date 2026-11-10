"""Curated spellings: where one venue writes a token differently from the rest, by operator.

Every entry was read from a venue's own data, never guessed (``ASSET_REGISTRY_SPEC`` section 8: the alias table is curated by hand and
reviewed). Kraken's legacy asset codes are the ``base`` values of the recorded ``AssetPairs`` answer that carry a prefix. Each pair's
``wsname`` spells the same token without it (``XETH`` and ``ETH/AUD``), and for Bitcoin and Dogecoin spells it ``XBT`` and ``XDG``. That
``XXBT`` is Bitcoin and ``XXDG`` is Dogecoin was checked on 2026-10-29 against Binance (``XXBTZUSD`` 78,760 against ``BTCUSDT`` 78,930;
``XDGUSD`` 0.07609 against ``DOGEUSDT`` 0.07626). The plain ``XBT`` and ``XDG`` never appear as a ``base`` in the recording, so they are not
listed.

A rule on the first letter would be wrong: ``XION``, ``XAUT`` and ``ZAMA`` are tokens of their own. A wrapped or bridged token
(``WBTC``, ``UBTC``, ``cbBTC``) is never listed here: it keeps its own symbol and so its own token.

The table is by operator (the part of a venue name before the first hyphen), because a code that means one thing on one venue means nothing
on another.
"""

from __future__ import annotations

from typing import Final

ALIASES: Final[dict[str, dict[str, str]]] = {
    "kraken": {
        "XXBT": "BTC",
        "XXDG": "DOGE",
        "XETH": "ETH",
        "XETC": "ETC",
        "XLTC": "LTC",
        "XMLN": "MLN",
        "XXLM": "XLM",
        "XXMR": "XMR",
        "XXRP": "XRP",
        "XZEC": "ZEC",
    },
}
