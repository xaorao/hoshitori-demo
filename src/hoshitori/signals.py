"""The social signals page's data: what public voices and a fixed cohort of large accounts say and hold, collected once and written
as a document.

This is a view beside the league, not evidence of any agent: nothing here enters a cycle, a forecast or a score. The page that
shows it says so, and says what is not known (stance is a word list; a wallet is not a person; the cohort is a sample). Every
account and wallet that could not be read is listed with the reason, and an empty result is shown as empty and never as zero
conviction.

The settings file names the accounts and the wallets before anything is read (a cohort chosen after seeing performance would
build survivorship in), plus the tokens to report on, and optionally a connector inbox of posts a person collected with their
own tools (kept apart as ``local``).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from hoshitori import canonical, fetch_types, safe_fetch, social_gateway, voices, whales
from hoshitori.connector import ConnectorError
from hoshitori.gateway import TransportFailure

FORMAT: Final = 1
MAX_ACCOUNTS: Final = 20
MAX_WALLETS: Final = 50
MAX_FEEDS: Final = 20
X_TOKEN_ENV: Final = "X_BEARER_TOKEN"  # noqa: S105  # the variable that holds the application's bearer token; the file never does
POSTS_PER_ACCOUNT: Final = 50
SYMBOLS: Final = ("BTC", "ETH", "SOL")
NOT_KNOWN: Final = (
    "Stance is read from a word list, not from meaning, so irony, quotes and conditions are missed.",
    "A repost, a quote or a near-copy of an earlier post by another account is counted as an echo, not as a second voice.",
    "A wallet is not a person: a vault, a market maker and a directional trader look alike here.",
    "The wallets are a fixed sample chosen before reading, not the market.",
    "None of this has been shown to help a decision, and no agent of the league reads it.",
)


class SignalsError(ValueError):
    """A settings file that is not what this module reads."""


@dataclass(frozen=True)
class Settings:
    accounts: tuple[str, ...]
    feeds: tuple[tuple[str, str], ...]  # name and address of a news feed
    channels: tuple[str, ...]  # public Telegram channels
    x_handles: tuple[str, ...]  # X accounts, read with the application's bearer token from the environment
    wallets: tuple[str, ...]
    symbols: tuple[str, ...]
    inbox: Path | None


def read_settings(raw: bytes, base: Path) -> Settings:
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise SignalsError("the settings are not JSON") from None
    if not isinstance(doc, dict) or set(doc) - {
        "accounts",
        "wallets",
        "symbols",
        "inbox",
        "feeds",
        "channels",
        "cohort_rule",
        "x_accounts",
    }:
        raise SignalsError("the settings have accounts, wallets, symbols and an optional inbox")
    accounts, wallets = doc.get("accounts", []), doc.get("wallets", [])
    symbols = doc.get("symbols", list(SYMBOLS))
    if (
        not (isinstance(accounts, list) and all(isinstance(a, str) and voices.valid_actor(a) for a in accounts))
        or len(accounts) > MAX_ACCOUNTS
    ):
        raise SignalsError(f"accounts are at most {MAX_ACCOUNTS} handles or did:plc identifiers")
    if (
        not (isinstance(wallets, list) and all(isinstance(w, str) and whales.valid_wallet(w) for w in wallets))
        or len(wallets) > MAX_WALLETS
    ):
        raise SignalsError(f"wallets are at most {MAX_WALLETS} addresses of 0x and 40 hexadecimal digits")
    if not (isinstance(symbols, list) and symbols and all(isinstance(s, str) and s in whales.COINS for s in symbols)):
        raise SignalsError(f"symbols are among {sorted(whales.COINS)}")
    channels = doc.get("channels", [])
    if not (
        isinstance(channels, list) and len(channels) <= MAX_FEEDS and all(isinstance(c, str) and voices.valid_channel(c) for c in channels)
    ):
        raise SignalsError(f"channels are at most {MAX_FEEDS} public Telegram channel names")
    x_accounts = doc.get("x_accounts", [])
    if not (
        isinstance(x_accounts, list)
        and len(x_accounts) <= MAX_ACCOUNTS
        and all(isinstance(h, str) and voices.valid_handle(h.lstrip("@")) for h in x_accounts)
    ):
        raise SignalsError(f"x_accounts are at most {MAX_ACCOUNTS} X handles")
    feeds = doc.get("feeds", [])
    if not (
        isinstance(feeds, list)
        and len(feeds) <= MAX_FEEDS
        and all(
            isinstance(f, dict) and set(f) == {"name", "url"} and isinstance(f["name"], str) and isinstance(f["url"], str) for f in feeds
        )
        and all(re.fullmatch(r"[A-Za-z0-9 ._-]{1,40}", f["name"]) and f["url"].startswith("https://") for f in feeds)
    ):
        raise SignalsError(f"feeds are at most {MAX_FEEDS} of a name and an https address")
    inbox = doc.get("inbox")
    if inbox is not None and not isinstance(inbox, str):
        raise SignalsError("the inbox is a path")
    return Settings(
        tuple(dict.fromkeys(a.lstrip("@") for a in accounts)),
        tuple((f["name"], f["url"]) for f in feeds),
        tuple(dict.fromkeys(channels)),
        tuple(dict.fromkeys(h.lstrip("@") for h in x_accounts)),
        tuple(dict.fromkeys(w.lower() for w in wallets)),
        tuple(dict.fromkeys(symbols)),
        None if inbox is None else (base / inbox),
    )


Fetch = Callable[[Any, str, dict[str, Any], int], bytes]


def _capture_id(n: int) -> str:
    return f"cap-{n:032x}"


def _fetch(transport: Any, adapter: Any, operation: str, args: dict[str, Any], n: int, now: Callable[[], datetime]) -> bytes:
    """One read through the adapter's own argument and answer checks; a failure raises with a fixed sentence."""
    canonical_args = adapter.normalise(operation, args)
    capture = transport.fetch(operation, canonical_args, 20.0, _capture_id(n), now())
    failed = adapter.classify(capture, now())
    if failed is not None:
        raise failed
    body: bytes = capture.body
    return body


def collect(
    settings: Settings,
    now: Callable[[], datetime] | None = None,
    bluesky_url: str | None = None,
    hyperliquid_url: str | None = None,
    fetch_feed: Callable[[str], bytes] | None = None,
    x_url: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Read every account and wallet once and build the document. A source that fails is listed with its reason; nothing is invented."""
    clock = now or (lambda: datetime.now(UTC))
    feed, state = social_gateway.BlueskyFeedAdapter(), social_gateway.HyperliquidWalletAdapter()
    feed_t = social_gateway.SocialTransport(
        bluesky_url or social_gateway.BLUESKY_URL, feed, clock, allow_insecure_loopback=bluesky_url is not None
    )
    state_t = social_gateway.SocialTransport(
        hyperliquid_url or social_gateway.HYPERLIQUID_URL, state, clock, allow_insecure_loopback=hyperliquid_url is not None
    )
    started = clock()
    posts: list[voices.Post] = []
    accounts_out: list[dict[str, Any]] = []
    n = 0
    for actor in settings.accounts:
        n += 1
        try:
            read = voices.parse_feed(_fetch(feed_t, feed, "feed", {"actor": actor, "limit": POSTS_PER_ACCOUNT}, n, clock))
            posts.extend(read)
            accounts_out.append({"actor": actor, "posts": len(read), "state": "READ", "reason": None})
        except (ConnectorError, TransportFailure) as exc:
            accounts_out.append({"actor": actor, "posts": 0, "state": "FAILED", "reason": f"{exc.error_class.name}"})
    for name, url in settings.feeds:
        try:
            reader = fetch_feed or (lambda u: safe_fetch.SafeFetcher().fetch(u).body)
            read = voices.parse_feed_xml(reader(url), name)
            posts.extend(read)
            accounts_out.append({"actor": f"{name} (news feed)", "posts": len(read), "state": "READ", "reason": None})
        except voices.VoicesError:
            accounts_out.append({"actor": f"{name} (news feed)", "posts": 0, "state": "FAILED", "reason": "MALFORMED"})
        except fetch_types.FetchError as exc:
            accounts_out.append({"actor": f"{name} (news feed)", "posts": 0, "state": "FAILED", "reason": exc.code.name})
    token = (environ if environ is not None else os.environ).get(X_TOKEN_ENV)
    if settings.x_handles:
        x_adapter = social_gateway.XSearchAdapter()
        x_t = social_gateway.SocialTransport(
            x_url or social_gateway.X_URL, x_adapter, clock, bearer=lambda: token, allow_insecure_loopback=x_url is not None
        )
    for handle in settings.x_handles:
        label = f"{handle} (X)"
        if not token:
            accounts_out.append({"actor": label, "posts": 0, "state": "FAILED", "reason": "NO_KEY"})
            continue
        n += 1
        try:
            read = voices.parse_x_search(_fetch(x_t, x_adapter, "search", {"handle": handle, "limit": 100}, n, clock), handle)
            posts.extend(read)
            accounts_out.append({"actor": label, "posts": len(read), "state": "READ", "reason": None})
        except (ConnectorError, TransportFailure) as exc:
            accounts_out.append({"actor": label, "posts": 0, "state": "FAILED", "reason": exc.error_class.name})
        except voices.VoicesError:
            accounts_out.append({"actor": label, "posts": 0, "state": "FAILED", "reason": "MALFORMED"})
    for channel in settings.channels:
        try:
            reader = fetch_feed or (lambda u: safe_fetch.SafeFetcher().fetch(u).body)
            read = voices.parse_telegram_page(reader(f"https://t.me/s/{channel}"), channel)
            posts.extend(read)
            accounts_out.append({"actor": f"{channel} (Telegram)", "posts": len(read), "state": "READ", "reason": None})
        except voices.VoicesError:
            accounts_out.append({"actor": f"{channel} (Telegram)", "posts": 0, "state": "FAILED", "reason": "MALFORMED"})
        except fetch_types.FetchError as exc:
            accounts_out.append({"actor": f"{channel} (Telegram)", "posts": 0, "state": "FAILED", "reason": exc.code.name})
    if settings.inbox is not None:
        try:
            local = voices.parse_inbox(settings.inbox.read_bytes())
            posts.extend(local)
            accounts_out.append({"actor": "connector inbox", "posts": len(local), "state": "READ", "reason": None})
        except (OSError, voices.VoicesError):
            accounts_out.append({"actor": "connector inbox", "posts": 0, "state": "FAILED", "reason": "UNREADABLE"})
    wallets_read: list[whales.Wallet] = []
    wallets_out: list[dict[str, Any]] = []
    for address in settings.wallets:
        n += 1
        try:
            wallet = whales.parse_state(address, _fetch(state_t, state, "state", {"address": address}, n, clock))
            wallets_read.append(wallet)
            wallets_out.append({"address": address, "state": "READ", "reason": None, "positions": len(wallet.positions)})
        except (ConnectorError, TransportFailure, whales.WhalesError) as exc:
            reason = exc.error_class.name if isinstance(exc, ConnectorError | TransportFailure) else "MALFORMED"
            wallets_out.append({"address": address, "state": "FAILED", "reason": reason, "positions": 0})
    at = clock()
    now_ms = int(at.timestamp() * 1000)
    tokens = []
    for symbol in settings.symbols:
        c = voices.convergence(posts, symbol, now_ms)
        h = whales.cohort(wallets_read, whales.COINS[symbol])
        tokens.append(
            {
                "symbol": symbol,
                "voices": {
                    "mentions": c.mentions,
                    "authors": c.authors,
                    "independent": c.independent,
                    "up": c.up,
                    "down": c.down,
                    "neutral": c.neutral,
                    "local_mentions": c.local_mentions,
                    "echoes": [{"uri": e.uri, "author": e.author, "echoes": e.echoes, "reason": e.reason} for e in c.echoes],
                    "posts": list(c.posts),
                },
                "whales": {
                    "wallets": h.wallets,
                    "holding": h.holding,
                    "long": h.long,
                    "short": h.short,
                    "gross_long": format(h.gross_long, "f"),
                    "gross_short": format(h.gross_short, "f"),
                    "net": format(h.net, "f"),
                    "top_share": format(h.top_share.quantize(Decimal("0.0001")), "f"),
                },
            }
        )
    return {
        "format": FORMAT,
        "collected_at": at.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "took_ms": int((at - started).total_seconds() * 1000),
        "window_hours": voices.WINDOW_H,
        "accounts": accounts_out,
        "wallets": wallets_out,
        "tokens": tokens,
        "not_known": list(NOT_KNOWN),
    }


def encode(document: dict[str, Any]) -> bytes:
    return canonical.dumps(document)
