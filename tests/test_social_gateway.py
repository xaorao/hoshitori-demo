"""The Bluesky feed and Hyperliquid wallet providers: arguments, requests and the classification of answers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from hoshitori import social_gateway as sg
from hoshitori.capture import RawCapture
from hoshitori.connector import ConnectorError, ErrorClass
from hoshitori.governor import RateModel

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
ADDR = "0x" + "ab" * 20


def capture(
    provider: str, operation: str, args: dict[str, Any], status: int | None, body: bytes, headers: dict[str, str] | None = None
) -> RawCapture:
    return RawCapture("cap-" + "1" * 32, provider, {"operation": operation, "arguments": args}, status, headers or {}, body, NOW, NOW)


def test_feed_arguments_are_checked_and_canonical() -> None:
    a = sg.BlueskyFeedAdapter()
    assert a.normalise("feed", {"actor": "@bsky.app", "limit": 20}) == {"actor": "bsky.app", "limit": 20}
    for bad in (
        {"actor": "bsky.app"},
        {"actor": "a b", "limit": 5},
        {"actor": "x.y", "limit": 0},
        {"actor": "x.y", "limit": 101},
        {"actor": "x.y", "limit": True},
    ):
        with pytest.raises(ConnectorError):
            a.normalise("feed", bad)
    with pytest.raises(ConnectorError):
        a.normalise("search", {})
    path = a.request_path({"actor": "bsky.app", "limit": 20})
    assert path.startswith("/xrpc/app.bsky.feed.getAuthorFeed?actor=bsky.app&limit=20")


def test_wallet_arguments_and_body() -> None:
    a = sg.HyperliquidWalletAdapter()
    assert a.normalise("state", {"address": ADDR.upper().replace("0X", "0x")}) == {"address": ADDR}
    for bad in ({}, {"address": "0x1"}, {"address": ADDR, "x": 1}):
        with pytest.raises(ConnectorError):
            a.normalise("state", bad)
    assert json.loads(a.request_body({"address": ADDR})) == {"type": "clearinghouseState", "user": ADDR}
    assert a.plan("state", {}) == 2.0


def test_classification_of_answers() -> None:
    f, w = sg.BlueskyFeedAdapter(), sg.HyperliquidWalletAdapter()
    ok_feed = json.dumps({"feed": []}).encode()
    args = {"actor": "bsky.app", "limit": 5}
    assert f.classify(capture("bluesky", "feed", args, 200, ok_feed), NOW) is None
    err = f.classify(capture("bluesky", "feed", args, 200, b"<html>"), NOW)
    assert err is not None and err.error_class is ErrorClass.MALFORMED
    err = f.classify(capture("bluesky", "feed", args, 429, b"slow", {"retry-after": "7"}), NOW)
    assert err is not None and err.error_class is ErrorClass.RATE_LIMIT and err.retry_after_s == 7
    err = f.classify(capture("bluesky", "feed", args, None, b""), NOW)
    assert err is not None and err.error_class is ErrorClass.UPSTREAM
    err = f.classify(capture("bluesky", "state", args, 200, ok_feed), NOW)
    assert err is not None and err.error_class is ErrorClass.MALFORMED
    good = json.dumps({"marginSummary": {"accountValue": "5"}, "assetPositions": []}).encode()
    assert w.classify(capture("hyperliquid-wallet", "state", {"address": ADDR}, 200, good), NOW) is None
    err = w.classify(capture("hyperliquid-wallet", "state", {"address": ADDR}, 200, b"null"), NOW)
    assert err is not None and err.error_class is ErrorClass.MALFORMED
    err = w.classify(capture("hyperliquid-wallet", "state", {"address": ADDR}, 500, b"boom"), NOW)
    assert err is not None and err.error_class is not ErrorClass.MALFORMED


def test_operations_and_limits() -> None:
    for a in (sg.BlueskyFeedAdapter(), sg.HyperliquidWalletAdapter()):
        assert (
            len(a.operations()) == 1
            and a.attempt_cap_s("x") >= a.min_attempt_s("x") > 0
            and a.rate_observation(capture(a.provider, "x", {}, 200, b"")) is None
        )


def test_the_constants_are_what_the_adapters_need() -> None:
    assert (sg.BLUESKY_URL, sg.HYPERLIQUID_URL) == ("https://public.api.bsky.app", "https://api.hyperliquid.xyz")
    assert (sg.MAX_BODY_BYTES, sg.MESSAGE_CUT, sg.FEED_LIMIT_MAX) == (2 * 1024 * 1024, 160, 100)
    assert sg.BlueskyFeedAdapter.rate == (RateModel.FIXED_WINDOW, 60.0, 60.0)
    assert sg.HyperliquidWalletAdapter.rate == (RateModel.FIXED_WINDOW, 600.0, 60.0)
    for a in (sg.BlueskyFeedAdapter(), sg.HyperliquidWalletAdapter()):
        assert (a.attempt_cap_s("x"), a.min_attempt_s("x"), a.plan("x", {}) in (1.0, 2.0)) == (20.0, 1.0, True)


def test_the_transport_is_bounded_and_builds_the_right_request() -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen: list[tuple[str, str, bytes]] = []

    class H(BaseHTTPRequestHandler):
        def _answer(self, body: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            seen.append(("GET", self.path, b""))
            self._answer(json.dumps({"feed": []}).encode())

        def do_POST(self) -> None:
            seen.append(("POST", self.path, self.rfile.read(int(self.headers["Content-Length"]))))
            self._answer(json.dumps({"marginSummary": {"accountValue": "1"}, "assetPositions": []}).encode())

        def log_message(self, *a: Any) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        feed = sg.BlueskyFeedAdapter()
        t = sg.SocialTransport(base, feed, lambda: NOW, allow_insecure_loopback=True)
        assert t._max_body == 2 * 1024 * 1024
        cap = t.fetch("feed", {"actor": "bsky.app", "limit": 5}, 5.0, "cap-" + "2" * 32, NOW)
        assert (cap.status, cap.source) == (200, "bluesky") and seen[0][:2] == (
            "GET",
            "/xrpc/app.bsky.feed.getAuthorFeed?actor=bsky.app&limit=5&filter=posts_with_replies",
        )
        wallet = sg.HyperliquidWalletAdapter()
        t2 = sg.SocialTransport(base, wallet, lambda: NOW, allow_insecure_loopback=True)
        cap = t2.fetch("state", {"address": ADDR}, 5.0, "cap-" + "3" * 32, NOW)
        assert (
            cap.source == "hyperliquid-wallet"
            and seen[1][:2] == ("POST", "/info")
            and json.loads(seen[1][2]) == {"type": "clearinghouseState", "user": ADDR}
        )
        with pytest.raises(Exception, match="could not be built"):
            t.fetch("feed", {"nope": 1}, 5.0, "cap-" + "4" * 32, NOW)
    finally:
        server.shutdown()
        server.server_close()


def test_an_error_body_is_cut_to_the_limit_in_the_message() -> None:
    err = sg.BlueskyFeedAdapter().classify(capture("bluesky", "feed", {"actor": "a.b", "limit": 5}, 500, b"x" * 1000), NOW)
    assert err is not None and err.error_class is ErrorClass.UPSTREAM and str(err).count("x") == 160


def test_the_limit_of_a_feed_read_is_from_one_to_one_hundred_inclusive() -> None:
    a = sg.BlueskyFeedAdapter()
    assert (
        a.normalise("feed", {"actor": "a.b", "limit": 1})["limit"] == 1
        and a.normalise("feed", {"actor": "a.b", "limit": 100})["limit"] == 100
    )


def test_an_operation_that_is_not_the_adapters_is_refused_even_with_good_arguments() -> None:
    with pytest.raises(ConnectorError, match="no operation"):
        sg.BlueskyFeedAdapter().normalise("state", {"actor": "a.b", "limit": 5})
    with pytest.raises(ConnectorError, match="no operation"):
        sg.HyperliquidWalletAdapter().normalise("feed", {"address": ADDR})


def test_a_transport_does_not_talk_to_plain_http_unless_told_to() -> None:
    from hoshitori.http_transport import TransportConfigError

    with pytest.raises(TransportConfigError):
        sg.SocialTransport("http://127.0.0.1:9", sg.BlueskyFeedAdapter(), lambda: NOW)


def test_the_messages_of_a_failed_answer_are_plain_and_exact() -> None:
    f = sg.BlueskyFeedAdapter()
    args = {"actor": "a.b", "limit": 5}
    none = f.classify(capture("bluesky", "feed", args, None, b""), NOW)
    assert none is not None and str(none) == "UPSTREAM: no response"
    err = f.classify(capture("bluesky", "feed", args, 500, b"  a\nb  "), NOW)
    assert err is not None and str(err) == "UPSTREAM: HTTP 500: a b"
    err = f.classify(capture("bluesky", "feed", args, 500, b"a\xffb"), NOW)
    assert err is not None and str(err) == "UPSTREAM: HTTP 500: a�b"


def test_an_answer_that_breaks_the_reader_is_malformed_whatever_it_raises() -> None:
    class Odd(sg.BlueskyFeedAdapter):
        def _parse(self, args: Any, body: bytes) -> object:
            raise KeyError("boom")

    err = Odd().classify(capture("bluesky", "feed", {"actor": "a.b", "limit": 5}, 200, b"{}"), NOW)
    assert err is not None and err.error_class is ErrorClass.MALFORMED and str(err) == "MALFORMED: bluesky: unreadable body (KeyError)"


def test_the_x_adapter_checks_its_arguments_and_builds_the_search() -> None:
    a = sg.XSearchAdapter()
    assert (
        a.normalise("search", {"handle": "jack", "limit": 10}) == {"handle": "jack", "limit": 10}
        and a.normalise("search", {"handle": "jack", "limit": 100})["limit"] == 100
    )
    for bad in (
        {"handle": "jack"},
        {"handle": "a b", "limit": 20},
        {"handle": "jack", "limit": 9},
        {"handle": "jack", "limit": 101},
        {"handle": "jack", "limit": True},
        {"handle": "@jack", "limit": 20},
    ):
        with pytest.raises(ConnectorError):
            a.normalise("search", bad)
    with pytest.raises(ConnectorError, match="no operation"):
        a.normalise("feed", {"handle": "jack", "limit": 20})
    path = a.request_path({"handle": "jack", "limit": 20})
    assert path == "/2/tweets/search/recent?query=from%3Ajack&max_results=20&tweet.fields=created_at%2Creferenced_tweets"
    ok = capture("x", "search", {"handle": "jack", "limit": 20}, 200, json.dumps({"meta": {}}).encode())
    assert a.classify(ok, NOW) is None
    err = a.classify(capture("x", "search", {"handle": "jack", "limit": 20}, 401, b'{"title":"Unauthorized"}'), NOW)
    assert err is not None and err.error_class is not ErrorClass.MALFORMED


def test_the_bearer_goes_out_on_the_request_and_is_not_in_the_capture() -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen: list[tuple[str, str | None]] = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append((self.path, self.headers.get("Authorization")))
            body = json.dumps({"meta": {"result_count": 0}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a: Any) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        t = sg.SocialTransport(base, sg.XSearchAdapter(), lambda: NOW, bearer=lambda: "SECRET-TOKEN", allow_insecure_loopback=True)
        cap = t.fetch("search", {"handle": "jack", "limit": 20}, 5.0, "cap-" + "5" * 32, NOW)
        assert seen == [
            ("/2/tweets/search/recent?query=from%3Ajack&max_results=20&tweet.fields=created_at%2Creferenced_tweets", "Bearer SECRET-TOKEN")
        ]
        assert "SECRET-TOKEN" not in json.dumps(dict(cap.request)) and "SECRET-TOKEN" not in json.dumps(dict(cap.headers))
        for none in (None, lambda: None, lambda: ""):
            t = sg.SocialTransport(base, sg.XSearchAdapter(), lambda: NOW, bearer=none, allow_insecure_loopback=True)
            with pytest.raises(Exception, match="no X bearer token"):
                t.fetch("search", {"handle": "jack", "limit": 20}, 5.0, "cap-" + "6" * 32, NOW)
        assert len(seen) == 1
    finally:
        server.shutdown()
        server.server_close()


def test_the_x_constants() -> None:
    assert sg.X_URL == "https://api.x.com" and sg.XSearchAdapter.rate == (RateModel.FIXED_WINDOW, 60.0, 900.0) and sg.X == "x"
