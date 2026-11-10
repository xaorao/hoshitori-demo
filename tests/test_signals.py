"""signals: the settings are checked, a collection lists every source that failed, and nothing is invented."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hoshitori import fetch_types, signals

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
RSS_A = b"<rss><channel><item><title>Exchange lists SOL with trading starting tomorrow for all users</title><guid>1</guid><pubDate>Wed, 04 Nov 2026 09:00:00 GMT</pubDate></item></channel></rss>"


def settings(**doc: object) -> signals.Settings:
    return signals.read_settings(json.dumps(doc).encode(), Path("/x"))


def test_settings_are_checked_and_deduplicated() -> None:
    s = settings(
        accounts=["@a.bsky.social", "a.bsky.social"], wallets=["0x" + "AB" * 20], feeds=[{"name": "wire", "url": "https://e.example/rss"}]
    )
    assert s.accounts == ("a.bsky.social",) and s.wallets == ("0x" + "ab" * 20,) and s.feeds == (("wire", "https://e.example/rss"),)
    assert s.symbols == ("BTC", "ETH", "SOL") and s.inbox is None


@pytest.mark.parametrize(
    "doc",
    [
        {"accounts": ["a b"]},
        {"accounts": [f"a{i}.x" for i in range(21)]},
        {"wallets": ["0x12"]},
        {"symbols": ["DOGE"]},
        {"symbols": []},
        {"feeds": [{"name": "w", "url": "http://insecure.example/rss"}]},
        {"feeds": [{"name": "w", "url": "https://e.example", "x": 1}]},
        {"feeds": [{"name": "bad/name", "url": "https://e.example"}]},
        {"inbox": 3},
        {"other": 1},
    ],
)
def test_settings_that_are_not_what_the_module_reads_are_refused(doc: dict) -> None:
    with pytest.raises(signals.SignalsError):
        signals.read_settings(json.dumps(doc).encode(), Path("/x"))
    with pytest.raises(signals.SignalsError):
        signals.read_settings(b"nope", Path("/x"))


def test_a_feed_that_fails_is_listed_and_one_that_reads_is_counted() -> None:
    def fetch(url: str) -> bytes:
        if "bad" in url:
            raise fetch_types.FetchError(fetch_types.FetchCode.TOO_LARGE, "big")
        if "junk" in url:
            return b"not xml"
        return RSS_A

    s = settings(
        feeds=[
            {"name": "good", "url": "https://good.example/rss"},
            {"name": "bad", "url": "https://bad.example/rss"},
            {"name": "junk", "url": "https://junk.example/rss"},
        ]
    )
    doc = signals.collect(s, now=lambda: NOW, fetch_feed=fetch)
    states = {a["actor"]: (a["state"], a["posts"], a["reason"]) for a in doc["accounts"]}
    assert states == {
        "good (news feed)": ("READ", 1, None),
        "bad (news feed)": ("FAILED", 0, "TOO_LARGE"),
        "junk (news feed)": ("FAILED", 0, "MALFORMED"),
    }
    sol = next(t for t in doc["tokens"] if t["symbol"] == "SOL")
    assert sol["voices"]["mentions"] == 1 and sol["voices"]["independent"] == 1
    btc = next(t for t in doc["tokens"] if t["symbol"] == "BTC")
    assert btc["voices"]["mentions"] == 0 and btc["whales"]["wallets"] == 0
    assert doc["collected_at"].endswith("Z") and doc["not_known"] and doc["format"] == 1


def test_the_document_is_canonical_bytes_and_stable() -> None:
    s = settings(feeds=[{"name": "good", "url": "https://good.example/rss"}])
    a = signals.encode(signals.collect(s, now=lambda: NOW, fetch_feed=lambda u: RSS_A))
    b = signals.encode(signals.collect(s, now=lambda: NOW, fetch_feed=lambda u: RSS_A))
    assert a == b and json.loads(a)["tokens"]


def test_a_telegram_channel_is_read_through_the_fetcher_and_a_failure_is_listed() -> None:
    page = (
        b'<div class="tgme_widget_message_wrap"><div data-post="good_channel/1"><div class="tgme_widget_message_text" dir="auto">SOL up</div>'
        b'<time datetime="2026-11-04T09:00:00+00:00"></time></div></div>'
    )
    asked: list[str] = []

    def fetch(url: str) -> bytes:
        asked.append(url)
        if "bad_channel" in url:
            raise fetch_types.FetchError(fetch_types.FetchCode.HTTP_STATUS, "404")
        return page

    doc = signals.collect(settings(channels=["good_channel", "bad_channel"]), now=lambda: NOW, fetch_feed=fetch)
    assert asked == ["https://t.me/s/good_channel", "https://t.me/s/bad_channel"]
    assert {a["actor"]: (a["state"], a["reason"]) for a in doc["accounts"]} == {
        "good_channel (Telegram)": ("READ", None),
        "bad_channel (Telegram)": ("FAILED", "HTTP_STATUS"),
    }


def test_channels_must_be_names() -> None:
    with pytest.raises(signals.SignalsError):
        settings(channels=["bad name"])


def test_x_accounts_need_a_token_and_are_listed_without_one() -> None:
    doc = signals.collect(settings(x_accounts=["@jack", "bob"]), now=lambda: NOW, environ={})
    assert [(a["actor"], a["state"], a["reason"]) for a in doc["accounts"]] == [
        ("jack (X)", "FAILED", "NO_KEY"),
        ("bob (X)", "FAILED", "NO_KEY"),
    ]


def test_x_accounts_are_read_with_the_token_from_the_environment_only() -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen: list[str | None] = []
    body = json.dumps(
        {"data": [{"id": "1", "text": "SOL up", "created_at": "2026-11-04T09:00:00.000Z"}], "meta": {"result_count": 1}}
    ).encode()

    class H(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append(self.headers.get("Authorization"))
            status = 401 if "bad" in self.path else 200
            out = b'{"title":"Unauthorized"}' if status == 401 else body
            self.send_response(status)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        doc = signals.collect(
            settings(x_accounts=["good", "bad"]),
            now=lambda: NOW,
            x_url=f"http://127.0.0.1:{server.server_address[1]}",
            environ={signals.X_TOKEN_ENV: "secret-xyz-123"},
        )
    finally:
        server.shutdown()
        server.server_close()
    states = {a["actor"]: (a["state"], a["posts"]) for a in doc["accounts"]}
    assert (
        states["good (X)"] == ("READ", 1)
        and states["bad (X)"][0] == "FAILED"
        and seen == ["Bearer secret-xyz-123", "Bearer secret-xyz-123"]
    )
    assert "secret-xyz-123" not in json.dumps(doc)


def test_x_settings_are_checked() -> None:
    for bad in (["bad name"], [f"u{i}" for i in range(21)], ["a" * 16], [3]):
        with pytest.raises(signals.SignalsError):
            settings(x_accounts=bad)
