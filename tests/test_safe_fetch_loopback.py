"""The safe fetch client against real servers on loopback: the whole path, TLS and sockets included, with nothing replaced.

The name ``files.example.test`` never resolves anywhere; a scripted resolver maps it to 127.0.0.1 and the policy lets loopback through (as a
test must), so a fetch that succeeds proves the connection went to the *checked address* and not to the name. Everything the client
must refuse is refused before or without a connection, or by the limits, and the server's log shows what actually reached it.
"""

from __future__ import annotations

import gzip
import socket
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from hoshitori.connector import ErrorClass
from hoshitori.fetch_types import FetchCode, FetchError
from hoshitori.safe_fetch import FetchLimits, FetchPolicy, SafeFetcher, is_public_address
from tests.ryo_fake_server import Behaviour, FakeRyo, Seen

NAME = "files.example.test"
HTML = {"content-type": "text/html; charset=utf-8"}


class World:
    def __init__(self) -> None:
        self.next: Any = None
        self.server = FakeRyo(script=self._play, tls=True, cert_name=NAME).start()
        self.port = self.server._server.server_address[1]
        self.resolved: list[tuple[str, int]] = []
        self.names: dict[str, list[str]] = {NAME: ["127.0.0.1"]}

    def _play(self, seen: Seen) -> Behaviour | None:
        step = self.next
        return step(seen) if callable(step) else step

    def resolver(self, host: str, port: int, timeout_s: float) -> list[str]:
        self.resolved.append((host, port))
        return list(self.names.get(host, []))

    def fetcher(self, *, limits: FetchLimits | None = None, public: Callable[[str], bool] | None = None, ca: bool = True) -> SafeFetcher:
        loopback_ok = public or (lambda address: address == "127.0.0.1" or is_public_address(address))
        policy = FetchPolicy(https_ports=frozenset({self.port}))
        return SafeFetcher(policy, limits, resolver=self.resolver, public=loopback_ok, ca_file=self.server.cert_path if ca else None)

    def url(self, path: str = "/page", host: str = NAME) -> str:
        return f"https://{host}:{self.port}{path}"

    def reset(self) -> None:
        self.next = None
        self.resolved.clear()
        self.names = {NAME: ["127.0.0.1"]}
        self.server.seen.clear()


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    w = World()
    try:
        yield w
    finally:
        w.server.stop()


@pytest.fixture(autouse=True)
def fresh(world: World) -> None:
    world.reset()


def chunked(pieces: list[bytes], content_type: bytes = b"text/html") -> bytes:
    head = b"HTTP/1.1 200 OK\r\nContent-Type: " + content_type + b"\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
    return head + b"".join(f"{len(p):x}\r\n".encode() + p + b"\r\n" for p in pieces) + b"0\r\n\r\n"


def refused(call: Callable[[], object], code: FetchCode) -> FetchError:
    with pytest.raises(FetchError) as caught:
        call()
    assert caught.value.code is code, (caught.value.code, caught.value.detail)
    return caught.value


# --- the request and the connection ----------------------------------------------------------------------------------------------------
def test_a_page_is_fetched_by_the_checked_address_and_nothing_of_ours_goes_with_it(world: World) -> None:
    world.next = Behaviour(200, b"<p>hello</p>", HTML)
    got = world.fetcher().fetch(world.url("/page?a=1#frag"))
    assert (got.status, got.content_type, got.charset, got.body, got.address) == (200, "text/html", "utf-8", b"<p>hello</p>", "127.0.0.1")
    assert got.url == world.url("/page?a=1") and got.redirects == ()
    assert world.resolved == [(NAME, world.port)]
    request = world.server.seen[0]
    assert (request.method, request.path) == ("GET", "/page?a=1")
    assert request.headers["host"] == f"{NAME}:{world.port}"
    assert (
        request.headers["user-agent"] == "hoshitori-fetch/0"
        and request.headers["accept-encoding"] == "identity"
        and request.headers["connection"] == "close"
    )
    assert not {"cookie", "authorization", "referer", "proxy-authorization", "x-forwarded-for"} & set(request.headers)
    assert len(world.server.seen) == 1


def test_plain_http_works_for_a_host_that_was_named() -> None:
    with FakeRyo(script=lambda seen: Behaviour(200, b"plain", HTML)) as server:
        port = server._server.server_address[1]
        fetcher = SafeFetcher(
            FetchPolicy(allow_http_hosts=frozenset({NAME}), http_ports=frozenset({port})),
            resolver=lambda h, p, t: ["127.0.0.1"],
            public=lambda a: a == "127.0.0.1",
        )
        got = fetcher.fetch(f"http://{NAME}:{port}/x")
        assert (got.body, got.address) == (b"plain", "127.0.0.1")
        assert server.seen[0].headers["host"] == f"{NAME}:{port}"


def test_the_default_policy_refuses_a_name_that_resolves_to_loopback(world: World) -> None:
    fetcher = SafeFetcher(FetchPolicy(https_ports=frozenset({world.port})), resolver=world.resolver, ca_file=world.server.cert_path)
    error = refused(lambda: fetcher.fetch(world.url()), FetchCode.ADDRESS_NOT_PUBLIC)
    assert error.error_class is ErrorClass.INVALID_ARGS
    assert world.server.seen == []


# --- rebinding ---------------------------------------------------------------------------------------------------------------------------
def test_a_name_that_answers_differently_the_second_time_is_never_asked_again(world: World) -> None:
    answers = [["127.0.0.1"], ["10.0.0.5"], ["10.0.0.5"]]

    def flipping(host: str, port: int, timeout_s: float) -> list[str]:
        world.resolved.append((host, port))
        return answers.pop(0)

    world.next = Behaviour(200, b"<p>once</p>", HTML)
    fetcher = SafeFetcher(
        FetchPolicy(https_ports=frozenset({world.port})),
        resolver=flipping,
        public=lambda a: a == "127.0.0.1",
        ca_file=world.server.cert_path,
    )
    got = fetcher.fetch(world.url())
    assert got.body == b"<p>once</p>" and got.address == "127.0.0.1"
    assert len(world.resolved) == 1, "the name is looked up once for the connection, never again"
    assert len(world.server.seen) == 1


def test_a_redirect_back_to_the_same_name_is_checked_again(world: World) -> None:
    answers = [["127.0.0.1"], ["10.0.0.5"]]

    def flipping(host: str, port: int, timeout_s: float) -> list[str]:
        return answers.pop(0)

    world.next = Behaviour(302, b"", {"location": "/second"})
    fetcher = SafeFetcher(
        FetchPolicy(https_ports=frozenset({world.port})),
        resolver=flipping,
        public=lambda a: a == "127.0.0.1",
        ca_file=world.server.cert_path,
    )
    error = refused(lambda: fetcher.fetch(world.url("/first")), FetchCode.ADDRESS_NOT_PUBLIC)
    assert [e.kind for e in error.events] == ["request", "resolved", "redirect", "request", "refused"]
    assert [s.path for s in world.server.seen] == ["/first"]


def test_a_redirect_to_a_private_name_is_refused_without_a_connection(world: World) -> None:
    world.names["internal.example.test"] = ["10.0.0.5"]
    world.next = Behaviour(302, b"", {"location": f"https://internal.example.test:{world.port}/secret"})
    refused(lambda: world.fetcher().fetch(world.url("/start")), FetchCode.ADDRESS_NOT_PUBLIC)
    assert [s.path for s in world.server.seen] == ["/start"]


@pytest.mark.parametrize(
    "location",
    [
        "https://127.0.0.1/",
        "https://127.1/",
        "https://2130706433/",
        "https://0x7f.0.0.1/",
        "https://0177.0.0.1/",
        "https://[::1]/",
        "//169.254.169.254/latest/meta-data/",
        "https://localhost/",
    ],
)
def test_a_redirect_to_a_numeric_or_single_label_host_is_refused(world: World, location: str) -> None:
    world.next = Behaviour(302, b"", {"location": location})
    refused(lambda: world.fetcher().fetch(world.url("/start")), FetchCode.HOST_INVALID)
    assert len(world.server.seen) == 1


def test_redirects_are_followed_a_few_times_and_no_cookie_follows(world: World) -> None:
    def play(seen: Seen) -> Behaviour:
        if seen.path == "/a":
            return Behaviour(301, b"", {"location": "/b", "set-cookie": "sid=1; Path=/"})
        if seen.path == "/b":
            return Behaviour(302, b"", {"location": f"https://{NAME}:{world.port}/c", "set-cookie": "sid=2"})
        return Behaviour(200, b"<p>end</p>", HTML)

    world.next = play
    got = world.fetcher().fetch(world.url("/a"))
    assert got.url == world.url("/c") and got.redirects == (world.url("/a"), world.url("/b"))
    assert [s.path for s in world.server.seen] == ["/a", "/b", "/c"]
    assert all("cookie" not in s.headers for s in world.server.seen)


def test_too_many_redirects_and_loops_stop(world: World) -> None:
    world.next = Behaviour(302, b"", {"location": "/again"})
    error = refused(lambda: world.fetcher().fetch(world.url("/start")), FetchCode.TOO_MANY_REDIRECTS)
    assert len(world.server.seen) == 4 and error.events[-1].kind == "refused"


# --- TLS ---------------------------------------------------------------------------------------------------------------------------------
def test_a_certificate_for_another_name_is_refused(world: World) -> None:
    with FakeRyo(script=lambda seen: Behaviour(200, b"x", HTML), tls=True, cert_for_ip=False) as other:
        port = other._server.server_address[1]
        fetcher = SafeFetcher(
            FetchPolicy(https_ports=frozenset({port})),
            resolver=lambda h, p, t: ["127.0.0.1"],
            public=lambda a: a == "127.0.0.1",
            ca_file=other.cert_path,
        )
        error = refused(lambda: fetcher.fetch(f"https://{NAME}:{port}/"), FetchCode.TLS)
        assert error.error_class is ErrorClass.UPSTREAM


def test_a_certificate_nobody_trusts_is_refused(world: World) -> None:
    refused(lambda: world.fetcher(ca=False).fetch(world.url()), FetchCode.TLS)


def test_a_server_that_does_not_speak_tls_is_refused(world: World) -> None:
    with FakeRyo(script=lambda seen: Behaviour(200, b"x", HTML)) as plain:
        port = plain._server.server_address[1]
        fetcher = SafeFetcher(
            FetchPolicy(https_ports=frozenset({port})),
            resolver=lambda h, p, t: ["127.0.0.1"],
            public=lambda a: a == "127.0.0.1",
            ca_file=world.server.cert_path,
        )
        refused(lambda: fetcher.fetch(f"https://{NAME}:{port}/"), FetchCode.TLS)


# --- hostile servers ------------------------------------------------------------------------------------------------------------------------
def test_status_and_type_refusals(world: World) -> None:
    world.next = Behaviour(404, b"gone", HTML)
    error = refused(lambda: world.fetcher().fetch(world.url()), FetchCode.HTTP_STATUS)
    assert (error.status, error.error_class) == (404, ErrorClass.NOT_FOUND)
    world.next = Behaviour(200, b"\x89PNG", {"content-type": "image/png"})
    refused(lambda: world.fetcher().fetch(world.url()), FetchCode.CONTENT_TYPE)
    world.next = Behaviour(200, b"x", {})
    refused(lambda: world.fetcher().fetch(world.url()), FetchCode.CONTENT_TYPE)


def test_a_compressed_answer_is_never_decoded(world: World) -> None:
    bomb = gzip.compress(b"\x00" * 50_000_000)
    assert len(bomb) < 100_000
    world.next = Behaviour(200, bomb, {**HTML, "content-encoding": "gzip"})
    started = time.monotonic()
    refused(lambda: world.fetcher().fetch(world.url()), FetchCode.CONTENT_ENCODING)
    assert time.monotonic() - started < 2.0


def test_an_answer_that_announces_too_much_is_refused_before_it_is_read(world: World) -> None:
    world.next = Behaviour(200, b"x" * 4096, HTML)
    refused(lambda: world.fetcher(limits=FetchLimits(max_body_bytes=2048)).fetch(world.url()), FetchCode.TOO_LARGE)


def test_an_answer_that_says_nothing_of_its_length_is_cut_at_the_limit(world: World) -> None:
    limits = FetchLimits(max_body_bytes=2048)
    world.next = Behaviour(raw=chunked([b"x" * 1024] * 3))
    refused(lambda: world.fetcher(limits=limits).fetch(world.url()), FetchCode.TOO_LARGE)
    world.next = Behaviour(raw=chunked([b"x" * 1024] * 2))
    assert len(world.fetcher(limits=limits).fetch(world.url()).body) == 2048
    world.next = Behaviour(raw=chunked([b"x" * 1024] * 2 + [b"y"]))
    refused(lambda: world.fetcher(limits=limits).fetch(world.url()), FetchCode.TOO_LARGE)


def test_a_body_of_exactly_the_limit_is_read_and_one_byte_more_is_not(world: World) -> None:
    limits = FetchLimits(max_body_bytes=2048)
    world.next = Behaviour(200, b"x" * 2048, HTML)
    assert len(world.fetcher(limits=limits).fetch(world.url()).body) == 2048
    world.next = Behaviour(200, b"x" * 2049, HTML)
    refused(lambda: world.fetcher(limits=limits).fetch(world.url()), FetchCode.TOO_LARGE)


def test_a_server_that_drips_one_byte_at_a_time_is_cut_off_at_the_deadline(world: World) -> None:
    world.next = Behaviour(200, b"x" * 60, HTML, drip_s=0.2)
    started = time.monotonic()
    refused(lambda: world.fetcher(limits=FetchLimits(timeout_s=1.0)).fetch(world.url()), FetchCode.TIMEOUT)
    assert time.monotonic() - started < 4.0


def test_a_server_that_is_slow_to_answer_is_cut_off_at_the_deadline(world: World) -> None:
    world.next = Behaviour(200, b"x", HTML, delay_s=3.0)
    started = time.monotonic()
    refused(lambda: world.fetcher(limits=FetchLimits(timeout_s=0.5)).fetch(world.url()), FetchCode.TIMEOUT)
    assert time.monotonic() - started < 2.5


def test_a_body_shorter_than_announced_is_a_failure(world: World) -> None:
    world.next = Behaviour(200, b"x" * 10, HTML, announce=100)
    refused(lambda: world.fetcher().fetch(world.url()), FetchCode.TRUNCATED)


def test_junk_and_silence(world: World) -> None:
    world.next = Behaviour(raw=b"this is not http\r\n\r\n")
    refused(lambda: world.fetcher().fetch(world.url()), FetchCode.PROTOCOL)
    world.next = Behaviour(hang_up=True)
    refused(lambda: world.fetcher().fetch(world.url()), FetchCode.CONNECT)
    junk = "".join(f"X-{k}: v\r\n" for k in range(150)).encode()
    world.next = Behaviour(raw=b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 1\r\n" + junk + b"\r\nx")
    refused(lambda: world.fetcher().fetch(world.url()), FetchCode.PROTOCOL)


def test_a_port_nobody_listens_on_is_a_connection_failure(world: World) -> None:
    with socket.socket() as spare:
        spare.bind(("127.0.0.1", 0))
        port = spare.getsockname()[1]
    fetcher = SafeFetcher(
        FetchPolicy(https_ports=frozenset({port})),
        resolver=lambda h, p, t: ["127.0.0.1"],
        public=lambda a: a == "127.0.0.1",
        ca_file=world.server.cert_path,
    )
    error = refused(lambda: fetcher.fetch(f"https://{NAME}:{port}/"), FetchCode.CONNECT)
    assert error.error_class is ErrorClass.UPSTREAM


def test_the_next_address_is_tried_when_the_first_is_dead(world: World) -> None:
    world.names[NAME] = ["127.0.0.2", "127.0.0.1"]
    world.next = Behaviour(200, b"<p>second</p>", HTML)
    fetcher = world.fetcher(public=lambda a: a in ("127.0.0.1", "127.0.0.2"))
    got = fetcher.fetch(world.url())
    assert got.body == b"<p>second</p>" and got.address in ("127.0.0.1", "127.0.0.2")
