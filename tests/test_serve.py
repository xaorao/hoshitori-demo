"""serve: the site is served with its headers, and a pasted claim is read once and bounded before a model is asked."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from hoshitori import serve


class Stub:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def read(self, text: str) -> dict[str, Any]:
        self.asked.append(text)
        return {"id": "live-x", "text": text, "page": None, "message": "read"}


@contextmanager
def running(tmp: Path, reader: serve.Reader | None, limits: serve.Limits | None = None) -> Iterator[str]:
    (tmp / "index.html").write_text("<html><head></head><p>site</p></html>", encoding="utf-8")
    server = serve.make_server(tmp, reader, ("127.0.0.1", 0), limits, headers={"X-Test": "yes"})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def post(base: str, body: object, raw: bytes | None = None) -> tuple[int, dict[str, Any], dict[str, str]]:
    data = raw if raw is not None else json.dumps(body).encode()
    request = urllib.request.Request(base + "/api/claim", data=data, headers={"Content-Type": "application/json"})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=10) as reply:  # noqa: S310
            return reply.status, json.loads(reply.read()), dict(reply.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read()), dict(exc.headers)


def test_the_site_is_served_with_the_headers_and_says_whether_it_reads_claims(tmp_path: Path) -> None:
    with running(tmp_path, Stub()) as base:
        with urllib.request.urlopen(base + "/index.html", timeout=10) as reply:  # noqa: S310
            body = reply.read().decode()
            assert (reply.status, "<p>site</p>" in body, reply.headers["X-Test"]) == (200, True, "yes")
        with urllib.request.urlopen(base + "/api/health", timeout=10) as reply:  # noqa: S310
            assert json.loads(reply.read()) == {"claims": True, "max_chars": serve.MAX_CHARS, "challenges": False}
    with running(tmp_path, None) as base:
        with urllib.request.urlopen(base + "/api/health", timeout=10) as reply:  # noqa: S310
            assert json.loads(reply.read())["claims"] is False
        assert post(base, {"text": "SOL will rise"})[0] == 503


def test_a_claim_is_read_once_and_returned_whole(tmp_path: Path) -> None:
    stub = Stub()
    with running(tmp_path, stub) as base:
        status, body, headers = post(base, {"text": "  SOL will rise 15% this week.  "})
    assert (status, body["text"], stub.asked) == (200, "SOL will rise 15% this week.", ["SOL will rise 15% this week."])
    assert headers["Cache-Control"] == "no-store"


def test_what_cannot_be_a_claim_is_refused_before_a_model_is_asked(tmp_path: Path) -> None:
    stub = Stub()
    with running(tmp_path, stub) as base:
        assert post(base, {"text": ""})[0] == 400
        assert post(base, {"text": "x" * (serve.MAX_CHARS + 1)})[0] == 400
        assert post(base, {"claim": "wrong member"})[0] == 400
        assert post(base, {"text": 5})[0] == 400
        assert post(base, None, raw=b"not json")[0] == 400
        assert post(base, None, raw=b"")[0] == 400
        assert post(base, None, raw=b"{" + b" " * serve.BODY_LIMIT + b"}")[0] == 400
    assert stub.asked == []


def test_one_address_has_a_limit_and_is_told_when_to_ask_again(tmp_path: Path) -> None:
    stub = Stub()
    clock = [0.0]
    limits = serve.Limits(per_client=2, per_day=10, now=lambda: clock[0])
    with running(tmp_path, stub, limits) as base:
        assert [post(base, {"text": f"claim {i}"})[0] for i in range(2)] == [200, 200]
        status, body, headers = post(base, {"text": "claim 3"})
        assert (status, int(headers["Retry-After"]) > 0, "limit" in body["error"]) == (429, True, True)
        clock[0] += serve.HOUR_S + 1
        assert post(base, {"text": "claim 4"})[0] == 200
    assert len(stub.asked) == 3


def test_the_days_budget_is_shared_by_every_address(tmp_path: Path) -> None:
    limits = serve.Limits(per_client=5, per_day=1)
    ok, why, wait = limits.take("a")
    assert (ok, why, wait) == (True, "", 0)
    ok, why, wait = limits.take("b")
    assert (ok, "budget" in why, wait > 0) == (False, True, True)


def test_a_reader_that_fails_is_said_to_have_failed_and_records_nothing(tmp_path: Path) -> None:
    class Broken:
        def read(self, text: str) -> dict[str, Any]:
            raise RuntimeError("the secret detail must not reach the person")

    with running(tmp_path, Broken()) as base:
        status, body, _ = post(base, {"text": "SOL will rise"})
    assert status == 502 and "secret" not in json.dumps(body) and "nothing was recorded" in body["error"]


def test_the_page_served_carries_the_live_mark_and_other_files_do_not(tmp_path: Path) -> None:
    (tmp_path / "other.html").write_text("<html><head></head></html>", encoding="utf-8")
    with running(tmp_path, None) as base:
        for path in ("/", "/index.html"):
            with urllib.request.urlopen(base + path, timeout=10) as r:  # noqa: S310
                assert f'name="{serve.LIVE_MARK}"' in r.read().decode() and r.headers["X-Test"] == "yes"
        with urllib.request.urlopen(base + "/other.html", timeout=10) as r:  # noqa: S310
            assert serve.LIVE_MARK not in r.read().decode()


# --- evidence challenges -------------------------------------------------------------------------------------------------------------

from hoshitori import challenge_store  # noqa: E402
from hoshitori.fetch_types import FetchCode, Fetched, FetchError  # noqa: E402

RECEIPT = "fc" + "a" * 26
QUOTE = "Trading in SOL will resume at 14:00 UTC"


@contextmanager
def challenging(tmp: Path, fetch: Any, limits: serve.Limits | None = None) -> Iterator[str]:
    (tmp / "index.html").write_text("<html><head></head></html>", encoding="utf-8")
    store = challenge_store.ChallengeStore(tmp / "challenges.jsonl")
    server = serve.make_server(tmp, None, ("127.0.0.1", 0), challenges=store, fetch=fetch, challenge_limits=limits)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def send(base: str, path: str, body: object = None, raw: bytes | None = None, method: str = "POST") -> tuple[int, Any]:
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    request = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=10) as reply:  # noqa: S310
            return reply.status, json.loads(reply.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def source(text: str) -> Any:
    return lambda url: Fetched(url, 200, "text/html", "utf-8", text.encode(), "e" * 64, (), "93.184.216.34", ())


def challenge(**over: object) -> dict[str, object]:
    return {"receipt": RECEIPT, "kind": "DISPUTE_CLAIM", "url": "https://example.org/n", "quote": QUOTE, "note": "", **over}


def test_a_challenge_is_checked_against_the_source_and_then_listed(tmp_path: Path) -> None:
    with challenging(tmp_path, source(f"<p>{QUOTE}</p>")) as base:
        status, body = send(base, "/api/challenge", challenge())
        assert status == 201 and body["new"] is True and body["challenge"]["state"] == "QUOTE_FOUND"
        assert body["challenge"]["origin"] == "ORIGIN_FETCHED_BY_US" and "client" not in json.dumps(body)
        status, again = send(base, "/api/challenge", challenge())
        assert (status, again["new"], again["challenge"]["id"] == body["challenge"]["id"]) == (200, False, True)
        status, listed = send(base, f"/api/challenges?receipt={RECEIPT}", method="GET")
        assert status == 200 and [c["id"] for c in listed["challenges"]] == [body["challenge"]["id"]]
        assert send(base, f"/api/challenges?receipt=fc{'b' * 26}", method="GET")[1] == {"challenges": []}
        with urllib.request.urlopen(base + "/api/health", timeout=10) as reply:  # noqa: S310
            assert json.loads(reply.read())["challenges"] is True


def test_a_quote_the_source_does_not_hold_and_a_source_that_cannot_be_read_are_said_so(tmp_path: Path) -> None:
    with challenging(tmp_path, source("<p>nothing of the kind</p>")) as base:
        assert send(base, "/api/challenge", challenge())[1]["challenge"]["state"] == "QUOTE_NOT_FOUND"

    def refuse(url: str) -> Fetched:
        raise FetchError(FetchCode.ADDRESS_NOT_PUBLIC, "private")

    (tmp_path / "two").mkdir()
    with challenging(tmp_path / "two", refuse) as base:
        got = send(base, "/api/challenge", challenge())[1]["challenge"]
        assert (got["state"], got["origin"], got["reason"]) == ("SOURCE_UNAVAILABLE", "SUBMITTER_ASSERTED", "ADDRESS_NOT_PUBLIC")


def test_what_is_not_a_challenge_is_refused_and_the_endpoint_off_says_so(tmp_path: Path) -> None:
    with challenging(tmp_path, source(QUOTE)) as base:
        assert send(base, "/api/challenge", challenge(kind="X"))[0] == 400
        assert send(base, "/api/challenge", raw=b"not json")[0] == 400
        assert send(base, "/api/challenge", raw=b"null")[0] == 400
        assert send(base, "/api/challenge", raw=b"")[0] == 400
        assert send(base, "/api/challenge", raw=b"x" * 5000)[0] == 400
        assert send(base, "/api/challenges?receipt=bad", method="GET")[0] == 400
        assert send(base, "/api/challenges", method="GET")[0] == 400
        assert send(base, "/api/challenges?receipt=fc" + "a" * 26, method="GET")[0] == 200
    (tmp_path / "off").mkdir()
    with running(tmp_path / "off", None) as base:
        assert (
            send(base, "/api/challenge", challenge())[0] == 503 and send(base, "/api/challenges?receipt=" + RECEIPT, method="GET")[0] == 503
        )


def test_one_address_has_a_limit_on_challenges_and_a_refused_one_costs_no_fetch(tmp_path: Path) -> None:
    fetched: list[str] = []

    def fetch(url: str) -> Fetched:
        fetched.append(url)
        return source(QUOTE)(url)

    with challenging(tmp_path, fetch, serve.Limits(2, 100)) as base:
        assert send(base, "/api/challenge", challenge(url="https://example.org/1"))[0] == 201
        assert send(base, "/api/challenge", challenge(url="https://example.org/2"))[0] == 201
        status, body = send(base, "/api/challenge", challenge(url="https://example.org/3"))
        assert status == 429 and "2 claims an hour" in body["error"] and len(fetched) == 2
        assert send(base, "/api/challenge", challenge(kind="X"))[0] == 400  # a refused submission takes no place in the count


def test_a_request_that_stops_sending_does_not_hold_its_thread(tmp_path: Path) -> None:
    """A body promised and never sent is dropped after the request timeout, so a few open sockets cannot use up every thread."""
    import socket
    import time

    previous = serve.REQUEST_TIMEOUT_S
    serve.REQUEST_TIMEOUT_S = 0.5
    try:
        with running(tmp_path, Stub()) as base:
            port = int(base.rsplit(":", 1)[1])
            with socket.create_connection(("127.0.0.1", port), timeout=10) as stalled:
                stalled.sendall(b"POST /api/claim HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{")
                started = time.monotonic()
                stalled.settimeout(8)
                try:
                    data = stalled.recv(4096)  # closed (empty) or answered with an error: either way the server let go
                except (ConnectionResetError, TimeoutError) as exc:
                    assert not isinstance(exc, TimeoutError), "the server held the connection open"
                    data = b""
                assert time.monotonic() - started < 5
                assert data == b"" or data.startswith(b"HTTP/1.")
            assert post(base, {"text": "SOL will rise 15% this week."})[0] == 200  # and the server still answers others
    finally:
        serve.REQUEST_TIMEOUT_S = previous


def test_the_table_of_addresses_is_bounded(tmp_path: Path) -> None:
    clock = [0.0]
    limits = serve.Limits(per_client=5, per_day=10**9, now=lambda: clock[0], max_clients=50)
    for n in range(50):
        assert limits.take(f"10.0.0.{n}")[0]
    ok, why, wait = limits.take("10.9.9.9")  # a new address when 50 are already counted this hour
    assert not ok and "addresses" in why and 0 < wait <= 3601
    assert limits.take("10.0.0.1")[0], "an address already counted is still served"
    clock[0] = 3601.0  # an hour on: the old addresses are forgotten, so the table does not keep them
    assert limits.take("10.9.9.9")[0]
    assert len(limits._clients) <= 1


def test_the_reader_leaves_no_directory_behind(monkeypatch: Any) -> None:
    import tempfile

    made: list[str] = []
    real = tempfile.mkdtemp

    def record(*args: Any, **kwargs: Any) -> str:
        path = real(*args, **kwargs)
        made.append(path)
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", record)
    with serve.open_reader("https://models.example.invalid/v1", lambda: "k", "m") as reader:
        assert isinstance(reader, serve.ModelReader)
        assert len(made) == 1 and Path(made[0]).is_dir()
    assert not Path(made[0]).exists()


def test_a_burst_of_connections_does_not_overflow_the_listen_queue(tmp_path: Path) -> None:
    """The default queue holds five: a burst of two hundred clients then waits for a TCP retry of seconds. The queue is sized for a burst."""
    (tmp_path / "index.html").write_text("<html><head></head><p>site</p></html>", encoding="utf-8")
    server = serve.make_server(tmp_path, None, ("127.0.0.1", 0))
    try:
        assert server.request_queue_size >= 128
    finally:
        server.server_close()
