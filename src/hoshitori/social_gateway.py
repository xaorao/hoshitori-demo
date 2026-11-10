"""Two keyless public reads shaped as gateway adapters: a Bluesky author feed (voices) and a Hyperliquid wallet state (whales).

They are not registered with a gateway: the signals collection calls them directly, one read at a time. Each has one operation, checked
arguments, and a classification of every answer. The adapters never retry and hold no socket; the transport is the shared bounded HTTP
client. What an answer must look like is decided by ``voices.parse_feed`` and ``whales.parse_state``: an answer that does not parse is
``MALFORMED`` and never reaches the page.

The Bluesky AppView read follows Hanko's keyless ``getAuthorFeed`` use (MIT, credited in ``NOTICE.md``); the Hyperliquid read is the public
``/info`` ``clearinghouseState`` call documented by the venue (weight 2 of the 1,200 a minute allowed per address).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, ClassVar, Final
from urllib.parse import urlencode

from hoshitori import voices, whales
from hoshitori.capture import RawCapture
from hoshitori.connector import ConnectorError, ErrorClass, classify_status, parse_retry_after
from hoshitori.gateway import RateObservation, TransportFailure
from hoshitori.governor import RateModel
from hoshitori.http_transport import HttpTransport

BLUESKY: Final = "bluesky"
BLUESKY_URL: Final = "https://public.api.bsky.app"
FEED_OPERATION: Final = "feed"
FEED_LIMIT_MAX: Final = 100
X: Final = "x"
X_URL: Final = "https://api.x.com"
SEARCH_OPERATION: Final = "search"
HYPERLIQUID_WALLET: Final = "hyperliquid-wallet"
HYPERLIQUID_URL: Final = "https://api.hyperliquid.xyz"
STATE_OPERATION: Final = "state"
MAX_BODY_BYTES: Final = 2 * 1024 * 1024
MESSAGE_CUT: Final = 160


def _invalid(message: str) -> ConnectorError:
    return ConnectorError(ErrorClass.INVALID_ARGS, message)


class _Adapter:
    provider: str
    operation: str
    rate: ClassVar[tuple[RateModel, float, float]]  # model, limit, window seconds
    attempt_cap: ClassVar[float] = 20.0
    min_attempt: ClassVar[float] = 1.0

    def operations(self) -> frozenset[str]:
        return frozenset({self.operation})

    def plan(self, operation: str, args: Mapping[str, Any]) -> float:
        return 1.0

    def attempt_cap_s(self, operation: str) -> float:
        return self.attempt_cap

    def min_attempt_s(self, operation: str) -> float:
        return self.min_attempt

    def rate_observation(self, capture: RawCapture) -> RateObservation | None:
        return None

    def _parse(self, args: Mapping[str, Any], body: bytes) -> object:
        raise NotImplementedError

    def classify(self, capture: RawCapture, now: datetime) -> ConnectorError | None:
        if capture.status is None:
            return ConnectorError(ErrorClass.UPSTREAM, "no response")
        asked = capture.request.get("arguments")
        if capture.request.get("operation") != self.operation or not isinstance(asked, dict):
            return ConnectorError(ErrorClass.MALFORMED, f"the capture is not a {self.provider} {self.operation}")
        failed = classify_status(capture.status)
        if failed is not None:
            wait = parse_retry_after(capture.headers.get("retry-after"), now)
            snippet = capture.body[:MESSAGE_CUT].decode("utf-8", "replace").strip().replace("\n", " ")
            return ConnectorError(failed, f"HTTP {capture.status}: {snippet}", retry_after_s=wait)
        try:
            self._parse(asked, capture.body)
        except (voices.VoicesError, whales.WhalesError) as exc:
            return ConnectorError(ErrorClass.MALFORMED, f"{self.provider}: {exc}"[:MESSAGE_CUT])
        except Exception as exc:  # a body is hostile input
            return ConnectorError(ErrorClass.MALFORMED, f"{self.provider}: unreadable body ({type(exc).__name__})")
        return None


class BlueskyFeedAdapter(_Adapter):
    provider = BLUESKY
    operation = FEED_OPERATION
    rate = (RateModel.FIXED_WINDOW, 60.0, 60.0)  # a prior far under the AppView's public limit

    def normalise(self, operation: str, args: Mapping[str, Any]) -> dict[str, Any]:
        if operation != self.operation:
            raise _invalid(f"{self.provider} has no operation {operation!r}")
        if set(args) != {"actor", "limit"}:
            raise _invalid("feed takes an actor and a limit")
        actor, limit = args["actor"], args["limit"]
        if not isinstance(actor, str) or not voices.valid_actor(actor):
            raise _invalid("the actor is a handle or a did:plc identifier")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= FEED_LIMIT_MAX:
            raise _invalid(f"the limit is a whole number from 1 to {FEED_LIMIT_MAX}")
        return {"actor": actor.lstrip("@"), "limit": limit}

    def request_path(self, args: Mapping[str, Any]) -> str:
        query = urlencode({"actor": str(args["actor"]), "limit": int(args["limit"]), "filter": "posts_with_replies"})
        return f"/xrpc/app.bsky.feed.getAuthorFeed?{query}"

    def _parse(self, args: Mapping[str, Any], body: bytes) -> object:
        return voices.parse_feed(body)


class XSearchAdapter(_Adapter):
    """One account's recent posts from X's recent-search endpoint, with the application's bearer token (read at the call, never stored)."""

    provider = X
    operation = SEARCH_OPERATION
    rate = (RateModel.FIXED_WINDOW, 60.0, 900.0)  # a prior under the lowest documented limit for the endpoint (it differs by plan)

    def normalise(self, operation: str, args: Mapping[str, Any]) -> dict[str, Any]:
        if operation != self.operation:
            raise _invalid(f"{self.provider} has no operation {operation!r}")
        if set(args) != {"handle", "limit"}:
            raise _invalid("search takes a handle and a limit")
        handle, limit = args["handle"], args["limit"]
        if not isinstance(handle, str) or not voices.valid_handle(handle):
            raise _invalid("the handle is 1 to 15 letters, digits and underscores")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 10 <= limit <= 100:
            raise _invalid("the limit is a whole number from 10 to 100")
        return {"handle": handle, "limit": limit}

    def request_path(self, args: Mapping[str, Any]) -> str:
        query = urlencode(
            {
                "query": f"from:{args['handle']}",
                "max_results": int(args["limit"]),
                "tweet.fields": "created_at,referenced_tweets",
            }
        )
        return f"/2/tweets/search/recent?{query}"

    def _parse(self, args: Mapping[str, Any], body: bytes) -> object:
        return voices.parse_x_search(body, str(args["handle"]))


class HyperliquidWalletAdapter(_Adapter):
    provider = HYPERLIQUID_WALLET
    operation = STATE_OPERATION
    rate = (RateModel.FIXED_WINDOW, 600.0, 60.0)  # weight 2 each of the venue's 1,200 a minute, shared with the candle venue: half of it

    def normalise(self, operation: str, args: Mapping[str, Any]) -> dict[str, Any]:
        if operation != self.operation:
            raise _invalid(f"{self.provider} has no operation {operation!r}")
        if set(args) != {"address"}:
            raise _invalid("state takes an address and nothing else")
        address = args["address"]
        if not isinstance(address, str) or not whales.valid_wallet(address):
            raise _invalid("the address is 0x and 40 hexadecimal digits")
        return {"address": address.lower()}

    def plan(self, operation: str, args: Mapping[str, Any]) -> float:
        return 2.0

    def request_body(self, args: Mapping[str, Any]) -> bytes:
        return json.dumps({"type": "clearinghouseState", "user": str(args["address"])}, separators=(",", ":"), sort_keys=True).encode()

    def _parse(self, args: Mapping[str, Any], body: bytes) -> object:
        return whales.parse_state(str(args["address"]), body)


class SocialTransport(HttpTransport):
    def __init__(
        self,
        base_url: str,
        adapter: BlueskyFeedAdapter | HyperliquidWalletAdapter | XSearchAdapter,
        clock: Callable[[], datetime],
        *,
        bearer: Callable[[], str | None] | None = None,
        allow_insecure_loopback: bool = False,
    ) -> None:
        super().__init__(
            base_url, clock, provider=adapter.provider, allow_insecure_loopback=allow_insecure_loopback, max_body_bytes=MAX_BODY_BYTES
        )
        self._adapter = adapter
        self._bearer = bearer

    def fetch(self, operation: str, args: Mapping[str, Any], timeout_s: float, capture_id: str, sent_at: datetime) -> RawCapture:
        try:
            if isinstance(self._adapter, BlueskyFeedAdapter | XSearchAdapter):
                method, path, body = "GET", self._prefix + self._adapter.request_path(args), None
            else:
                method, path, body = "POST", self._prefix + "/info", self._adapter.request_body(args)
        except (KeyError, ValueError, TypeError):
            raise TransportFailure(ErrorClass.INVALID_ARGS, "the request could not be built") from None
        headers: dict[str, str] = {}
        if isinstance(self._adapter, XSearchAdapter):
            token = self._bearer() if self._bearer is not None else None
            if not token:
                raise TransportFailure(ErrorClass.INVALID_ARGS, "no X bearer token is set")
            headers["Authorization"] = f"Bearer {token}"  # sent on this request only; the capture records the address without it
        return self._exchange(operation, args, method, path, body, headers, timeout_s, capture_id, sent_at)
