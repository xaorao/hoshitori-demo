"""Checks of the safe fetch client's rules, runnable against a mutant of ``hoshitori.safe_fetch``.

The network and the name service are replaced by scripts, so every rule is exercised alone. The real network side has its own tests.
"""

from __future__ import annotations

import hashlib
import types
from collections.abc import Callable, Sequence
from typing import Any

from hoshitori.connector import ErrorClass
from hoshitori.fetch_types import FetchCode, FetchError, HopResponse, Target

PUBLIC = "93.184.216.34"
HTML = (("Content-Type", "text/html; charset=utf-8"),)


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refused(self, label: str, call: Callable[[], object], code: FetchCode, status: int | None = None) -> FetchError | None:
        try:
            call()
        except FetchError as exc:
            if exc.code is not code or (status is not None and exc.status != status):
                self.bad.append(f"{label}: refused with {exc.code.value} ({exc.status}), expected {code.value}")
            return exc
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return None
        self.bad.append(f"{label}: was not refused (expected {code.value})")
        return None


class Net:
    """A scripted name service, network and clock."""

    def __init__(self, mod: types.ModuleType, names: dict[str, list[str]] | None = None, **kwargs: Any) -> None:
        self.names = names or {}
        self.script: list[HopResponse | FetchError] = []
        self.resolved: list[tuple[str, int, float]] = []
        self.hops: list[tuple[Target, tuple[str, ...], float, int]] = []
        self.now = 100.0
        self.step = 0.0
        policy = kwargs.pop("policy", None)
        limits = kwargs.pop("limits", None)
        self.fetcher = mod.SafeFetcher(policy, limits, resolver=self._resolve, hop=self._hop, clock=self._clock, **kwargs)

    def _clock(self) -> float:
        return self.now

    def _resolve(self, host: str, port: int, timeout_s: float) -> list[str]:
        self.resolved.append((host, port, timeout_s))
        self.now += self.step
        return list(self.names.get(host, [PUBLIC]))

    def _hop(self, target: Target, addresses: Sequence[str], timeout_s: float, screen: Callable[..., bool], max_body: int) -> HopResponse:
        self.hops.append((target, tuple(addresses), timeout_s, max_body))
        step = self.script.pop(0) if self.script else resp()
        if isinstance(step, FetchError):
            raise step
        return step


def resp(status: int = 200, headers: Sequence[tuple[str, str]] = HTML, body: bytes = b"<p>hello</p>", address: str = PUBLIC) -> HopResponse:
    return HopResponse(status, tuple(headers), body, address)


def redirect(location: str, status: int = 302) -> HopResponse:
    return resp(status, (("Location", location),), b"")


# --- addresses ---------------------------------------------------------------------------------------------------------------------------
def check_addresses(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    public = (
        "8.8.8.8",
        "1.1.1.1",
        "93.184.216.34",
        "100.63.255.255",
        "100.128.0.0",
        "172.15.255.255",
        "172.32.0.0",
        "192.167.255.255",
        "192.169.0.0",
        "11.0.0.0",
        "9.255.255.255",
        "126.255.255.255",
        "128.0.0.0",
        "169.253.255.255",
        "169.255.0.0",
        "223.255.255.255",
        "2606:4700:4700::1111",
        "2a00:1450:4009:81f::200e",
        "::ffff:8.8.8.8",
        "::ffff:93.184.216.34",
    )
    private = (
        "127.0.0.1",
        "127.1.2.3",
        "127.255.255.255",
        "10.0.0.1",
        "10.255.255.255",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.0.1",
        "192.168.255.255",
        "169.254.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "100.127.255.255",
        "0.0.0.0",  # noqa: S104
        "0.1.2.3",
        "255.255.255.255",
        "224.0.0.1",
        "239.255.255.250",
        "233.252.0.1",
        "198.18.0.1",
        "198.19.255.255",
        "192.0.2.1",
        "198.51.100.1",
        "203.0.113.1",
        "240.0.0.1",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "fd00:ec2::254",
        "ff02::1",
        "ff0e::1",
        "2001:db8::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "::ffff:169.254.169.254",
        "::ffff:100.64.0.1",
        "::ffff:0.0.0.0",
        "::7f00:1",
        "::127.0.0.1",
        "::808:808",
        "64:ff9b::7f00:1",
        "64:ff9b::808:808",
        "64:ff9b:1::1",
        "64:ff9b:1:ffff::1",
        "2002:7f00:1::",
        "2002:808:808::",
        "2002::1",
        "2001:0:4136:e378:8000:63bf:3fff:fdd2",
        "2001:0:0:0:0:0:0:1",
        "100::1",
        "fec0::1",
        "feff:ffff:ffff:ffff:ffff:ffff:ffff:ffff",
        "3ffe::1",
        "192.0.0.8",
        "192.0.0.1",
        "fe80::1%en0",
        "fe80::1%1",
        "8.8.8.8%en0",
    )
    junk = (
        "",
        " ",
        "not an address",
        "8.8.8.8 ",
        " 8.8.8.8",
        "1.1",
        "127.1",
        "2130706433",
        "0x7f.0.0.1",
        "0177.0.0.1",
        "8.8.8",
        "256.1.1.1",
        "1.2.3.4.5",
        "::g",
        "[::1]",
        "8.8.8.8/32",
    )
    for address in public:
        if mod.is_public_address(address) is not True:
            c.bad.append(f"{address} is public but was refused")
    for address in (*private, "2606:4700:4700::1111%1", "2606:4700:4700::1111%en0"):
        if mod.is_public_address(address) is not False:
            c.bad.append(f"{address} is not public but was accepted")
    for address in junk:
        if mod.is_public_address(address) is not False:
            c.bad.append(f"{address!r} is not an address but was accepted")
    return c.bad


# --- addresses written as URLs ----------------------------------------------------------------------------------------------------------
def check_parse_url(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    P = mod.FetchPolicy
    default = P()
    base = "https://example.com/"
    valid: list[tuple[str, Any, tuple[str, str, str, int, str]]] = [
        ("https://example.com", default, ("https://example.com/", "https", "example.com", 443, "/")),
        ("HTTPS://Example.COM/Path?Q=1#frag", default, ("https://example.com/Path?Q=1", "https", "example.com", 443, "/Path?Q=1")),
        ("https://example.com:443/a", default, ("https://example.com/a", "https", "example.com", 443, "/a")),
        ("https://example.com./x", default, ("https://example.com/x", "https", "example.com", 443, "/x")),
        (
            "https://sub.example.co.uk/a/b?c=d&e=f",
            default,
            ("https://sub.example.co.uk/a/b?c=d&e=f", "https", "sub.example.co.uk", 443, "/a/b?c=d&e=f"),
        ),
        ("https://xn--bcher-kva.example/", default, ("https://xn--bcher-kva.example/", "https", "xn--bcher-kva.example", 443, "/")),
        (
            "https://a-b.example.org/%E2%9C%93",
            default,
            ("https://a-b.example.org/%E2%9C%93", "https", "a-b.example.org", 443, "/%E2%9C%93"),
        ),
        ("https://example.com?x=1", default, ("https://example.com/?x=1", "https", "example.com", 443, "/?x=1")),
        ("https://example.com/#frag", default, ("https://example.com/", "https", "example.com", 443, "/")),
        ("https://example.com:/", default, ("https://example.com/", "https", "example.com", 443, "/")),
        ("https://example.xn--p1ai/", default, ("https://example.xn--p1ai/", "https", "example.xn--p1ai", 443, "/")),
        ("https://a.bc/", default, ("https://a.bc/", "https", "a.bc", 443, "/")),
        ("https://example.com/a!b", default, ("https://example.com/a!b", "https", "example.com", 443, "/a!b")),
        ("https://example.com/~user/%7E", default, ("https://example.com/~user/%7E", "https", "example.com", 443, "/~user/%7E")),
        ("https://1.example.com/", default, ("https://1.example.com/", "https", "1.example.com", 443, "/")),
        ("https://0x.example.com/", default, ("https://0x.example.com/", "https", "0x.example.com", 443, "/")),
        (
            "http://allowed.example/",
            P(allow_http_hosts=frozenset({"allowed.example"})),
            ("http://allowed.example/", "http", "allowed.example", 80, "/"),
        ),
        (
            "http://allowed.example:80/x",
            P(allow_http_hosts=frozenset({"allowed.example"})),
            ("http://allowed.example/x", "http", "allowed.example", 80, "/x"),
        ),
        (
            "https://example.com:8443/",
            P(https_ports=frozenset({443, 8443})),
            ("https://example.com:8443/", "https", "example.com", 8443, "/"),
        ),
        (
            "http://allowed.example:8080/",
            P(allow_http_hosts=frozenset({"allowed.example"}), http_ports=frozenset({8080})),
            ("http://allowed.example:8080/", "http", "allowed.example", 8080, "/"),
        ),
        (
            "https://sub.example.com/",
            P(allowed_hosts=frozenset({"example.com"})),
            ("https://sub.example.com/", "https", "sub.example.com", 443, "/"),
        ),
        ("https://example.com/", P(allowed_hosts=frozenset({"example.com"})), ("https://example.com/", "https", "example.com", 443, "/")),
        (
            "https://a.b.example.com/",
            P(allowed_hosts=frozenset({"x.org", "example.com"})),
            ("https://a.b.example.com/", "https", "a.b.example.com", 443, "/"),
        ),
        (
            base + "a" * (2048 - len(base)),
            default,
            (base + "a" * (2048 - len(base)), "https", "example.com", 443, "/" + "a" * (2048 - len(base))),
        ),
        (
            "https://" + ".".join(["a" * 63] * 3 + ["b" * 61]) + "/",
            default,
            ("https://" + ".".join(["a" * 63] * 3 + ["b" * 61]) + "/", "https", ".".join(["a" * 63] * 3 + ["b" * 61]), 443, "/"),
        ),
        ("https://example." + "c" * 63 + "/", default, ("https://example." + "c" * 63 + "/", "https", "example." + "c" * 63, 443, "/")),
        ("https://" + "a" * 63 + ".com/", default, ("https://" + "a" * 63 + ".com/", "https", "a" * 63 + ".com", 443, "/")),
    ]
    for raw, policy, want in valid:
        try:
            got = mod.parse_url(raw, policy)
        except FetchError as exc:
            c.bad.append(f"{raw[:70]!r} was refused: {exc}")
            continue
        c.eq(f"parse_url({raw[:70]!r})", (got.url, got.scheme, got.host, got.port, got.path_query), want)
    R = FetchCode
    refused: list[tuple[str, Any, FetchCode]] = [
        ("", default, R.INVALID_URL),
        ("https://", default, R.INVALID_URL),
        ("https:///x", default, R.INVALID_URL),
        ("https:example.com", default, R.INVALID_URL),
        ("//example.com/", default, R.SCHEME),
        ("/path", default, R.SCHEME),
        ("example.com", default, R.SCHEME),
        (base + "a" * (2049 - len(base)), default, R.INVALID_URL),
        ("https://example.com/a b", default, R.INVALID_URL),
        ("https://example.com/\ta", default, R.INVALID_URL),
        ("https://exa mple.com", default, R.INVALID_URL),
        ("https://example.com/\n", default, R.INVALID_URL),
        ("https://example.com/\r\nHost: evil", default, R.INVALID_URL),
        ("https://example.com/\x00", default, R.INVALID_URL),
        ("https://example.com/\x7f", default, R.INVALID_URL),
        ("https://example.com/é", default, R.INVALID_URL),
        ("https://exämple.com/", default, R.INVALID_URL),
        ("https://example.com\\@evil.com/", default, R.INVALID_URL),
        ("https://example.com/\\", default, R.INVALID_URL),
        ("https://example.com:99999/", default, R.INVALID_URL),
        ("https://example.com:abc/", default, R.INVALID_URL),
        ("https://[::1/", default, R.INVALID_URL),
        ("ht\ttps://example.com/", default, R.INVALID_URL),
        ("ftp://example.com/", default, R.SCHEME),
        ("file:///etc/passwd", default, R.SCHEME),
        ("gopher://example.com/", default, R.SCHEME),
        ("javascript:alert(1)", default, R.SCHEME),
        ("data:text/html,x", default, R.SCHEME),
        ("ws://example.com/", default, R.SCHEME),
        ("wss://example.com/", default, R.SCHEME),
        ("httpss://example.com/", default, R.SCHEME),
        ("http://example.com/", default, R.HTTP_NOT_ALLOWED),
        ("HTTP://example.com/", default, R.HTTP_NOT_ALLOWED),
        ("http://other.example/", P(allow_http_hosts=frozenset({"allowed.example"})), R.HTTP_NOT_ALLOWED),
        ("http://allowed.example.evil.com/", P(allow_http_hosts=frozenset({"allowed.example"})), R.HTTP_NOT_ALLOWED),
        ("https://user:pw@example.com/", default, R.CREDENTIALS),
        ("https://user@example.com/", default, R.CREDENTIALS),
        ("https://@example.com/", default, R.CREDENTIALS),
        ("https://example.com:443@evil.com/", default, R.CREDENTIALS),
        ("https://example.com@evil.com/", default, R.CREDENTIALS),
        ("https://example.com:80/", default, R.PORT),
        ("https://example.com:8443/", default, R.PORT),
        ("https://example.com:0/", default, R.PORT),
        ("https://example.com:22/", default, R.PORT),
        ("http://allowed.example:8080/", P(allow_http_hosts=frozenset({"allowed.example"})), R.PORT),
        ("http://allowed.example:443/", P(allow_http_hosts=frozenset({"allowed.example"})), R.PORT),
        ("https://example.com:80/", P(http_ports=frozenset({80})), R.PORT),
        ("https://127.0.0.1/", default, R.HOST_INVALID),
        ("https://127.1/", default, R.HOST_INVALID),
        ("https://2130706433/", default, R.HOST_INVALID),
        ("https://0x7f.0.0.1/", default, R.HOST_INVALID),
        ("https://0177.0.0.1/", default, R.HOST_INVALID),
        ("https://0x7f.1/", default, R.HOST_INVALID),
        ("https://0x7f.0x0.0x0.0x1/", default, R.HOST_INVALID),
        ("https://[::1]/", default, R.HOST_INVALID),
        ("https://[2606:4700:4700::1111]/", default, R.HOST_INVALID),
        ("https://8.8.8.8/", default, R.HOST_INVALID),
        ("https://localhost/", default, R.HOST_INVALID),
        ("https://intranet/", default, R.HOST_INVALID),
        ("https://example/", default, R.HOST_INVALID),
        ("https://example.com../", default, R.HOST_INVALID),
        ("https://.example.com/", default, R.HOST_INVALID),
        ("https://a..example.com/", default, R.HOST_INVALID),
        ("https://-a.example.com/", default, R.HOST_INVALID),
        ("https://a-.example.com/", default, R.HOST_INVALID),
        ("https://a_b.example.com/", default, R.HOST_INVALID),
        ("https://example.c/", default, R.HOST_INVALID),
        ("https://example.com1/", default, R.HOST_INVALID),
        ("https://example.123/", default, R.HOST_INVALID),
        ("https://example.c0m/", default, R.HOST_INVALID),
        ("https://example.-com/", default, R.HOST_INVALID),
        ("https://example%2ecom/", default, R.HOST_INVALID),
        ("https://exa%6dple.com/", default, R.HOST_INVALID),
        ("https://example.com%00.evil.com/", default, R.HOST_INVALID),
        ("https://" + "a" * 64 + ".com/", default, R.HOST_INVALID),
        ("https://example." + "c" * 64 + "/", default, R.HOST_INVALID),
        ("https://" + ".".join(["a" * 63] * 3 + ["b" * 62]) + "/", default, R.HOST_INVALID),
        ("https://example.xn--/", default, R.HOST_INVALID),
        ("https://evil.com/", P(allowed_hosts=frozenset({"example.com"})), R.HOST_NOT_ALLOWED),
        ("https://notexample.com/", P(allowed_hosts=frozenset({"example.com"})), R.HOST_NOT_ALLOWED),
        ("https://example.com.evil.com/", P(allowed_hosts=frozenset({"example.com"})), R.HOST_NOT_ALLOWED),
        ("https://xexample.com/", P(allowed_hosts=frozenset({"example.com"})), R.HOST_NOT_ALLOWED),
        ("https://example.com/", P(allowed_hosts=frozenset()), R.HOST_NOT_ALLOWED),
    ]
    for raw, policy, code in refused:
        c.refused(f"parse_url({raw[:60]!r})", lambda r=raw, p=policy: mod.parse_url(r, p), code)
    c.refused("a non-string address", lambda: mod.parse_url(None, default), R.INVALID_URL)  # type: ignore[arg-type]
    c.refused("a bytes address", lambda: mod.parse_url(b"https://example.com/", default), R.INVALID_URL)  # type: ignore[arg-type]
    return c.bad


def check_content_types(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    good = [
        ("text/html", ("text/html", None)),
        ("TEXT/HTML; Charset=UTF-8", ("text/html", "utf-8")),
        ('text/html; charset="iso-8859-1"', ("text/html", "iso-8859-1")),
        ("application/json;charset=utf-8", ("application/json", "utf-8")),
        ("text/html; boundary=x; charset=utf-8", ("text/html", "utf-8")),
        ("text/html;", ("text/html", None)),
        ("text/html; charset=", ("text/html", None)),
        ('text/html; charset=""', ("text/html", None)),
        ("application/vnd.api+json", ("application/vnd.api+json", None)),
        ("  text/plain  ; charset = us-ascii ", ("text/plain", "us-ascii")),
        ("a/b", ("a/b", None)),
        ("text/html; format=flowed", ("text/html", None)),
        ("text/html; charset=~x~", ("text/html", "~x~")),
        ("text/html; charset=utf-8; charset=latin1", ("text/html", "latin1")),
        ("x" * 127 + "/" + "y" * 127, ("x" * 127 + "/" + "y" * 127, None)),
    ]
    for value, want in good:
        try:
            c.eq(f"parse_content_type({value[:40]!r})", mod.parse_content_type(value), want)
        except FetchError as exc:
            c.bad.append(f"{value[:40]!r} was refused: {exc}")
    for value in (
        None,
        "",
        " ",
        "html",
        "text/",
        "/html",
        "text html",
        "text/ht ml",
        "text/html, text/plain",
        ";charset=utf-8",
        "text//html",
        "-/x",
        "x/-",
        "x" * 128 + "/y",
        "x/" + "y" * 128,
    ):
        c.refused(f"parse_content_type({value!r})", lambda v=value: mod.parse_content_type(v), FetchCode.CONTENT_TYPE)
    return c.bad


# --- the driver: one fetch, redirects, names --------------------------------------------------------------------------------------------
def check_fetch(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    R = FetchCode
    net = Net(mod)
    net.script = [resp()]
    got = net.fetcher.fetch("https://example.com/a?b=1#c")
    c.eq(
        "a plain fetch",
        (got.url, got.status, got.content_type, got.charset, got.body, got.address),
        ("https://example.com/a?b=1", 200, "text/html", "utf-8", b"<p>hello</p>", PUBLIC),
    )
    c.eq("its digest and chain", (got.sha256, got.redirects), (hashlib.sha256(b"<p>hello</p>").hexdigest(), ()))
    c.eq("its events", [(e.hop, e.kind) for e in got.events], [(0, "request"), (0, "resolved")])
    c.eq("the resolver was asked once, for the host, the port and the time left", net.resolved, [("example.com", 443, 20.0)])
    target, addresses, timeout, max_body = net.hops[0]
    c.eq(
        "the hop got the checked target, the addresses, the time left and the limit",
        (target.url, target.path_query, addresses, timeout, max_body),
        ("https://example.com/a?b=1", "/a?b=1", (PUBLIC,), 20.0, 2 * 1024 * 1024),
    )
    c.eq("the result is immutable", type(got).__dataclass_params__.frozen, True)  # type: ignore[attr-defined]

    # redirects
    for status in (301, 302, 303, 307, 308):
        net = Net(mod)
        net.script = [redirect("/b?c=d", status), resp()]
        got = net.fetcher.fetch("https://example.com/a")
        c.eq(
            f"a {status} is followed",
            (got.url, got.redirects, [t[0].path_query for t in net.hops]),
            ("https://example.com/b?c=d", ("https://example.com/a",), ["/a", "/b?c=d"]),
        )
        c.eq(
            f"a {status}: events",
            [(e.hop, e.kind) for e in got.events],
            [(0, "request"), (0, "resolved"), (0, "redirect"), (1, "request"), (1, "resolved")],
        )
    net = Net(mod, {"other.example": ["8.8.4.4"]})
    net.script = [redirect("https://other.example:443/x#frag"), redirect("//example.com/y"), resp(address="8.8.4.4")]
    got = net.fetcher.fetch("https://example.com/a")
    c.eq(
        "absolute and scheme-relative redirects",
        (got.url, got.redirects),
        ("https://example.com/y", ("https://example.com/a", "https://other.example/x")),
    )
    c.eq(
        "the resolved addresses are listed in the event",
        [e.detail for e in got.events if e.kind == "resolved"],
        [PUBLIC, "8.8.4.4", PUBLIC],
    )
    c.eq(
        "each hop is resolved on its own",
        [h[:2] for h in net.resolved],
        [("example.com", 443), ("other.example", 443), ("example.com", 443)],
    )
    c.eq("and gets its own addresses", [t[1] for t in net.hops], [(PUBLIC,), ("8.8.4.4",), (PUBLIC,)])
    net = Net(mod)
    net.script = [redirect("  /b  "), resp()]
    c.eq("a location is trimmed", net.fetcher.fetch("https://example.com/a").url, "https://example.com/b")
    net = Net(mod)
    net.script = [redirect("b/c"), redirect("../d"), redirect("?q=1"), resp()]
    c.eq(
        "relative locations are read against the address that sent them",
        net.fetcher.fetch("https://example.com/x/y/z").url,
        "https://example.com/x/y/d?q=1",
    )
    net = Net(mod)
    net.script = [redirect("/1"), redirect("/2"), redirect("/3"), resp()]
    c.eq(
        "three redirects are followed",
        net.fetcher.fetch("https://example.com/a").redirects,
        ("https://example.com/a", "https://example.com/1", "https://example.com/2", "https://example.com/3")[:3],
    )
    c.eq("and four requests are made", len(net.hops), 4)
    net = Net(mod)
    net.script = [redirect("/1"), redirect("/2"), redirect("/3"), redirect("/4"), resp()]
    c.refused("four redirects", lambda: net.fetcher.fetch("https://example.com/a"), R.TOO_MANY_REDIRECTS)
    c.eq("four redirects: no fifth request", len(net.hops), 4)
    net = Net(mod, limits=mod.FetchLimits(max_redirects=0))
    net.script = [redirect("/1"), resp()]
    c.refused("no redirects allowed", lambda: net.fetcher.fetch("https://example.com/a"), R.TOO_MANY_REDIRECTS)
    net = Net(mod, limits=mod.FetchLimits(max_redirects=0))
    net.script = [resp()]
    c.eq("no redirects allowed, none needed", net.fetcher.fetch("https://example.com/a").status, 200)
    net = Net(mod)
    net.script = [redirect("/b"), redirect("/a")] * 3
    c.refused("a loop", lambda: net.fetcher.fetch("https://example.com/a"), R.TOO_MANY_REDIRECTS)
    for label, step, code in (
        ("no location", resp(302, (), b""), R.REDIRECT_INVALID),
        ("an empty location", redirect(""), R.REDIRECT_INVALID),
        ("a blank location", redirect("   "), R.REDIRECT_INVALID),
        ("two locations", resp(302, (("Location", "/a"), ("location", "/b")), b""), R.BAD_HEADERS),
        ("a long location", redirect("/" + "a" * 2048), R.REDIRECT_INVALID),
        ("a location with a space", redirect("/a b"), R.REDIRECT_INVALID),
        ("a location with a tab", redirect("/a\tb"), R.REDIRECT_INVALID),
        ("a location with a newline", redirect("/a\nb"), R.REDIRECT_INVALID),
        ("a location with a backslash", redirect("/a\\b"), R.REDIRECT_INVALID),
        ("a location with a non-ASCII character", redirect("/é"), R.REDIRECT_INVALID),
        ("a downgrade to http", redirect("http://example.com/"), R.HTTP_NOT_ALLOWED),
        ("a redirect to ftp", redirect("ftp://example.com/"), R.SCHEME),
        ("a redirect to javascript", redirect("javascript:alert(1)"), R.SCHEME),
        ("a redirect to a file", redirect("file:///etc/passwd"), R.SCHEME),
        ("a redirect to a loopback number", redirect("https://127.0.0.1/"), R.HOST_INVALID),
        ("a redirect to 127.1", redirect("https://127.1/"), R.HOST_INVALID),
        ("a redirect to a decimal address", redirect("https://2130706433/"), R.HOST_INVALID),
        ("a redirect to a hexadecimal address", redirect("https://0x7f.0.0.1/"), R.HOST_INVALID),
        ("a redirect to an octal address", redirect("https://0177.0.0.1/"), R.HOST_INVALID),
        ("a redirect to an IPv6 loopback", redirect("https://[::1]/"), R.HOST_INVALID),
        ("a scheme-relative redirect to an address", redirect("//169.254.169.254/latest/meta-data/"), R.HOST_INVALID),
        ("a redirect to localhost", redirect("https://localhost/"), R.HOST_INVALID),
        ("a redirect with credentials", redirect("https://user:pw@example.com/"), R.CREDENTIALS),
        ("a redirect to another port", redirect("https://example.com:8443/"), R.PORT),
    ):
        net = Net(mod)
        net.script = [step, resp()]
        c.refused(label, lambda n=net: n.fetcher.fetch("https://example.com/a"), code)
        c.eq(f"{label}: nothing was fetched after it", len(net.hops), 1)
    longest = "https://example.com/" + "a" * (2048 - len("https://example.com/"))
    net = Net(mod)
    net.script = [redirect(longest), resp()]
    c.eq("a location of 2048 characters is followed", net.fetcher.fetch("https://example.com/a").url, longest)
    net = Net(mod)
    net.script = [redirect("/" + "a" * 2047), resp()]
    c.refused(
        "a relative location of 2048 characters leads to an address that is too long",
        lambda: net.fetcher.fetch("https://example.com/a"),
        R.INVALID_URL,
    )
    net = Net(
        mod,
        {
            "internal.example": ["10.0.0.5"],
            "mixed.example": [PUBLIC, "192.168.0.1"],
            "meta.example": ["169.254.169.254"],
            "mapped.example": ["::ffff:127.0.0.1"],
        },
    )
    for name in ("internal.example", "mixed.example", "meta.example", "mapped.example"):
        net.script = [redirect(f"https://{name}/x"), resp()]
        before = len(net.hops)
        c.refused(f"a redirect to {name}", lambda: net.fetcher.fetch("https://example.com/a"), R.ADDRESS_NOT_PUBLIC)
        c.eq(f"{name}: only the first hop was made", len(net.hops) - before, 1)
    return c.bad


def check_names(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    R = FetchCode
    for label, answer, code in (
        ("no address", [], R.DNS),
        ("a private address", ["10.0.0.1"], R.ADDRESS_NOT_PUBLIC),
        ("a loopback address", ["127.0.0.1"], R.ADDRESS_NOT_PUBLIC),
        ("public then private", [PUBLIC, "10.0.0.1"], R.ADDRESS_NOT_PUBLIC),
        ("private then public", ["10.0.0.1", PUBLIC], R.ADDRESS_NOT_PUBLIC),
        ("public, public, private", [PUBLIC, "8.8.8.8", "127.0.0.1"], R.ADDRESS_NOT_PUBLIC),
        ("a mapped loopback", ["::ffff:127.0.0.1"], R.ADDRESS_NOT_PUBLIC),
        ("a scoped address", ["fe80::1%en0"], R.ADDRESS_NOT_PUBLIC),
        ("not an address", ["example.com"], R.ADDRESS_NOT_PUBLIC),
        ("65 addresses", ["8.8.8.8"] * 65, R.DNS),
    ):
        net = Net(mod, {"example.com": answer})
        c.refused(label, lambda n=net: n.fetcher.fetch("https://example.com/"), code)
        c.eq(f"{label}: nothing was fetched", net.hops, [])
    net = Net(mod, {"example.com": [f"8.8.8.{k}" for k in range(1, 65)]})
    got = net.fetcher.fetch("https://example.com/")
    c.eq(
        "64 public addresses are accepted, 8 are tried",
        (len(net.hops[0][1]), net.hops[0][1] == tuple(f"8.8.8.{k}" for k in range(1, 9)), got.address),
        (8, True, PUBLIC),
    )
    net = Net(mod, {"example.com": ["8.8.8.8", "8.8.4.4", "2606:4700:4700::1111"]})
    net.fetcher.fetch("https://example.com/")
    c.eq("addresses are handed over in the order given", net.hops[0][1], ("8.8.8.8", "8.8.4.4", "2606:4700:4700::1111"))
    net = Net(mod, limits=mod.FetchLimits(max_addresses=1, max_resolved=2))
    net.names["example.com"] = ["8.8.8.8", "8.8.4.4"]
    net.fetcher.fetch("https://example.com/")
    c.eq("the number of addresses tried is a limit", net.hops[0][1], ("8.8.8.8",))
    net.names["example.com"] = ["8.8.8.8", "8.8.4.4", "1.1.1.1"]
    c.refused("one more than the resolved limit", lambda: net.fetcher.fetch("https://example.com/"), R.DNS)

    # a resolver that fails stays failed; the default policy and resolver are the strict ones
    def failing(host: str, port: int, timeout_s: float) -> list[str]:
        raise FetchError(R.DNS, "the name did not resolve")

    fetcher = mod.SafeFetcher(resolver=failing, hop=lambda *a: resp())
    c.refused("a name that does not resolve", lambda: fetcher.fetch("https://example.com/"), R.DNS)
    default = mod.SafeFetcher(resolver=lambda h, p, t: ["10.1.2.3"], hop=lambda *a: resp())
    c.refused("the default policy refuses a private address", lambda: default.fetch("https://example.com/"), R.ADDRESS_NOT_PUBLIC)
    net = Net(mod, {"example.com": ["8.8.8.8", "8.8.4.4"]})
    net.fetcher.fetch("https://example.com/")
    c.eq(
        "the resolved addresses are listed in the event, in order",
        net.fetcher.fetch("https://example.com/").events[1].detail,
        "8.8.8.8,8.8.4.4",
    )
    allowing = mod.SafeFetcher(resolver=lambda h, p, t: ["10.1.2.3"], public=lambda a: True, hop=lambda *a: resp())
    c.eq("a policy can be replaced (tests do)", allowing.fetch("https://example.com/").status, 200)
    return c.bad


def check_answers(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    R = FetchCode
    limit = mod.FetchLimits().max_body_bytes
    for type_ in (
        "text/html",
        "application/xhtml+xml",
        "text/plain",
        "application/json",
        "application/xml",
        "text/xml",
        "application/rss+xml",
        "application/atom+xml",
        "TEXT/HTML; charset=iso-8859-1",
    ):
        net = Net(mod)
        net.script = [resp(headers=(("Content-Type", type_),))]
        c.eq(f"{type_} is read", net.fetcher.fetch("https://example.com/").content_type, type_.split(";")[0].lower())
    for type_ in (
        "image/png",
        "application/octet-stream",
        "application/pdf",
        "text/css",
        "application/javascript",
        "text/javascript",
        "video/mp4",
        "application/zip",
        "multipart/form-data",
        "text/html/x",
    ):
        net = Net(mod)
        net.script = [resp(headers=(("Content-Type", type_),))]
        c.refused(f"{type_} is not read", lambda n=net: n.fetcher.fetch("https://example.com/"), R.CONTENT_TYPE)
    net = Net(mod)
    net.script = [resp(headers=())]
    c.refused("no content type", lambda: net.fetcher.fetch("https://example.com/"), R.CONTENT_TYPE)
    net = Net(mod)
    net.script = [resp(headers=(("Content-Type", "text/html"), ("content-type", "text/plain")))]
    c.refused("two content types", lambda: net.fetcher.fetch("https://example.com/"), R.BAD_HEADERS)
    net = Net(mod, policy=mod.FetchPolicy(content_types=frozenset({"image/png"})))
    net.script = [resp(headers=(("Content-Type", "image/png"),)), resp(headers=HTML)]
    c.eq("the allowed types are the policy's", net.fetcher.fetch("https://example.com/").content_type, "image/png")
    c.refused("and nothing else", lambda: net.fetcher.fetch("https://example.com/"), R.CONTENT_TYPE)
    for encoding in ("gzip", "br", "deflate", "compress", "gzip, br", "x-gzip", "identity, gzip", "zstd", ""):
        net = Net(mod)
        net.script = [resp(headers=(*HTML, ("Content-Encoding", encoding)))]
        c.refused(f"encoding {encoding!r}", lambda n=net: n.fetcher.fetch("https://example.com/"), R.CONTENT_ENCODING)
    for encoding in ("identity", "Identity", " identity "):
        net = Net(mod)
        net.script = [resp(headers=(*HTML, ("content-encoding", encoding)))]
        c.eq(f"encoding {encoding!r} is no encoding", net.fetcher.fetch("https://example.com/").status, 200)
    net = Net(mod)
    net.script = [resp(headers=(*HTML, ("Content-Encoding", "identity"), ("Content-Encoding", "identity")))]
    c.refused("two encodings", lambda: net.fetcher.fetch("https://example.com/"), R.BAD_HEADERS)
    for status in (201, 202, 204, 206, 300, 304, 305, 400, 401, 403, 404, 410, 429, 451, 500, 502, 503):
        net = Net(mod)
        net.script = [resp(status)]
        error = c.refused(f"status {status}", lambda n=net: n.fetcher.fetch("https://example.com/"), R.HTTP_STATUS, status)
        want = {
            404: ErrorClass.NOT_FOUND,
            410: ErrorClass.NOT_FOUND,
            429: ErrorClass.RATE_LIMIT,
            401: ErrorClass.AUTH,
            403: ErrorClass.AUTH,
            451: ErrorClass.GEO_BLOCKED,
        }.get(status, ErrorClass.UPSTREAM)
        if error is not None:
            c.eq(f"status {status} is class", error.error_class, want)
    for announced, ok in (
        (str(limit), True),
        (str(limit - 1), True),
        (" 10 ", True),
        ("0", True),
        (str(limit + 1), False),
        ("99999999999", False),
    ):
        net = Net(mod)
        net.script = [resp(headers=(*HTML, ("Content-Length", announced)), body=b"x" * 10)]
        if ok:
            c.eq(f"announced length {announced!r}", net.fetcher.fetch("https://example.com/").status, 200)
        else:
            c.refused(f"announced length {announced!r}", lambda n=net: n.fetcher.fetch("https://example.com/"), R.TOO_LARGE)
    for announced in ("abc", "-1", "1e3", "12 34", "", "1.5", "+5", "1234567890123", "０"):  # noqa: RUF001
        net = Net(mod)
        net.script = [resp(headers=(*HTML, ("Content-Length", announced)))]
        c.refused(f"announced length {announced!r} is not a length", lambda n=net: n.fetcher.fetch("https://example.com/"), R.BAD_HEADERS)
    net = Net(mod)
    net.script = [resp(headers=(*HTML, ("Content-Length", "5"), ("Content-Length", "5")))]
    c.refused("two lengths", lambda: net.fetcher.fetch("https://example.com/"), R.BAD_HEADERS)
    net = Net(mod)
    net.script = [resp(body=b"x" * limit)]
    c.eq("a body of exactly the limit", len(net.fetcher.fetch("https://example.com/").body), limit)
    net = Net(mod)
    net.script = [resp(body=b"x" * (limit + 1))]
    c.refused("a body one byte over the limit", lambda: net.fetcher.fetch("https://example.com/"), R.TOO_LARGE)
    net = Net(mod, limits=mod.FetchLimits(max_body_bytes=2048))
    net.script = [resp(body=b"x" * 2049)]
    c.refused("a lower limit", lambda: net.fetcher.fetch("https://example.com/"), R.TOO_LARGE)
    c.eq("and the hop was told it", net.hops[0][3], 2048)
    net = Net(mod)
    net.script = [resp(body=b"")]
    c.eq("an empty body is a body", (net.fetcher.fetch("https://example.com/").body,), (b"",))
    net = Net(mod)
    net.script = [FetchError(R.TIMEOUT, "slow")]
    c.refused("a hop that fails", lambda: net.fetcher.fetch("https://example.com/"), R.TIMEOUT)
    # a hop that is given the screen uses it for what it is for
    seen: list[tuple[int, tuple[tuple[str, str], ...]]] = []
    screens: dict[str, Any] = {}

    def probing(target: Target, addresses: Sequence[str], timeout_s: float, screen: Callable[..., bool], max_body: int) -> HopResponse:
        screens["screen"] = screen
        return resp()

    fetcher = mod.SafeFetcher(resolver=lambda h, p, t: [PUBLIC], hop=probing)
    fetcher.fetch("https://example.com/")
    screen = screens["screen"]
    c.eq("the screen reads a redirect without its body", (screen(301, (("Location", "/x"),)), screen(308, ())), (False, False))
    c.eq("the screen reads a good answer", screen(200, HTML), True)
    for label, args in (
        ("a 404", (404, HTML)),
        ("a gzip", (200, (*HTML, ("Content-Encoding", "gzip")))),
        ("a png", (200, (("Content-Type", "image/png"),))),
        ("a huge length", (200, (*HTML, ("Content-Length", str(limit + 1))))),
    ):
        c.refused(
            f"the screen refuses {label}",
            lambda a=args: screen(*a),
            {"a 404": R.HTTP_STATUS, "a gzip": R.CONTENT_ENCODING, "a png": R.CONTENT_TYPE, "a huge length": R.TOO_LARGE}[label],
        )
    seen.clear()
    return c.bad


def check_events_and_limits(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    R = FetchCode
    net = Net(mod)
    net.script = [redirect("https://example.com/b?api_key=hunter2&q=1"), resp()]
    got = net.fetcher.fetch("https://user.example.com/a?token=abc&x=1".replace("user.", ""))
    c.eq("the address that was asked for is kept whole", got.url, "https://example.com/b?api_key=hunter2&q=1")
    text = " ".join(f"{e.url} {e.detail}" for e in got.events)
    c.eq("the events show no secret", ("hunter2" in text, "abc" in text), (False, False))
    c.eq("but the redirect is there", [e.kind for e in got.events if e.kind == "redirect"], ["redirect"])
    net = Net(mod, {"internal.example": ["10.0.0.5"]})
    net.script = [redirect("https://internal.example/x")]
    error = c.refused("a refusal", lambda: net.fetcher.fetch("https://example.com/a"), R.ADDRESS_NOT_PUBLIC)
    if error is not None:
        c.eq(
            "the events of a refusal",
            [(e.hop, e.kind) for e in error.events],
            [(0, "request"), (0, "resolved"), (0, "redirect"), (1, "request"), (1, "refused")],
        )
        c.eq(
            "the last one says why and where",
            (error.events[-1].url, error.events[-1].detail.startswith("ADDRESS_NOT_PUBLIC")),
            ("https://internal.example/x", True),
        )
        c.eq(
            "the error says what it is",
            (error.code, error.status, error.error_class, str(error).startswith("ADDRESS_NOT_PUBLIC: ")),
            (R.ADDRESS_NOT_PUBLIC, None, ErrorClass.INVALID_ARGS, True),
        )
    error = c.refused("an unreadable address", lambda: net.fetcher.fetch("https://example.com:abc/"), R.INVALID_URL)
    if error is not None:
        c.eq(
            "the event of an address that cannot be read",
            [(e.kind, e.url) for e in error.events],
            [("refused", "(an address that cannot be read)")],
        )
    net = Net(mod)
    error = c.refused("a refusal before anything", lambda: net.fetcher.fetch("ftp://example.com/"), R.SCHEME)
    if error is not None:
        c.eq("its events", [(e.hop, e.kind, e.url) for e in error.events], [(0, "refused", "ftp://example.com/")])

    # time: one deadline for the whole fetch
    net = Net(mod, limits=mod.FetchLimits(timeout_s=10.0))
    net.step = 4.0
    net.script = [redirect("/b"), resp()]
    got = net.fetcher.fetch("https://example.com/a")
    c.eq("time left shrinks with every step", [r[2] for r in net.resolved], [10.0, 6.0])
    c.eq("and the hop gets what is left after the name", [round(h[2], 3) for h in net.hops], [6.0, 2.0])
    net = Net(mod, limits=mod.FetchLimits(timeout_s=10.0))
    net.step = 5.0
    net.script = [redirect("/b"), resp()]
    c.refused("a deadline that passes in the second look-up", lambda: net.fetcher.fetch("https://example.com/a"), R.TIMEOUT)
    c.eq("no second request", len(net.hops), 1)
    net = Net(mod, limits=mod.FetchLimits(timeout_s=10.0))
    net.step = 10.0
    c.refused("a deadline that passes in the first look-up", lambda: net.fetcher.fetch("https://example.com/a"), R.TIMEOUT)
    c.eq("no request", net.hops, [])
    net = Net(mod, limits=mod.FetchLimits(timeout_s=10.0))
    net.step = 9.999
    c.eq("a look-up that leaves a millisecond", net.fetcher.fetch("https://example.com/a").status, 200)

    # limits
    L = mod.FetchLimits
    d = L()
    c.eq(
        "default limits",
        (d.max_body_bytes, d.timeout_s, d.max_redirects, d.max_addresses, d.max_resolved),
        (2 * 1024 * 1024, 20.0, 3, 8, 64),
    )
    for kwargs in (
        {"max_body_bytes": 1023},
        {"timeout_s": 0},
        {"timeout_s": -1.0},
        {"max_redirects": -1},
        {"max_redirects": 11},
        {"max_addresses": 0},
        {"max_addresses": 9, "max_resolved": 8},
    ):
        try:
            L(**kwargs)
            c.bad.append(f"limits {kwargs} are accepted")
        except ValueError:
            pass
    for kwargs in (
        {"max_body_bytes": 1024},
        {"timeout_s": 0.001},
        {"max_redirects": 0},
        {"max_redirects": 10},
        {"max_addresses": 8, "max_resolved": 8},
        {"max_addresses": 1},
    ):
        try:
            L(**kwargs)
        except ValueError:
            c.bad.append(f"limits {kwargs} are refused")
    p = mod.FetchPolicy()
    c.eq(
        "default policy",
        (p.allow_http_hosts, p.allowed_hosts, p.https_ports, p.http_ports, sorted(p.content_types)),
        (frozenset(), None, frozenset({443}), frozenset({80}), sorted(mod.DEFAULT_TYPES)),
    )
    c.eq("redirect statuses", sorted(mod.REDIRECTS), [301, 302, 303, 307, 308])
    c.eq(
        "default content types",
        sorted(mod.DEFAULT_TYPES),
        [
            "application/atom+xml",
            "application/json",
            "application/rss+xml",
            "application/xhtml+xml",
            "application/xml",
            "text/html",
            "text/plain",
            "text/xml",
        ],
    )
    return c.bad


def check_resolver(mod: types.ModuleType) -> list[str]:
    """The operating system's resolver, replaced by a script: what it returns, how it fails and what a hang costs."""
    import socket
    import threading
    import time

    c = Case(mod)
    R = FetchCode
    real = socket.getaddrinfo
    calls: list[tuple[Any, ...]] = []

    def answering(*args: Any, **kwargs: Any) -> list[Any]:
        time.sleep(0.05)  # a resolver takes a moment: a thread that was not waited for is still working
        calls.append((args, kwargs))
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700:4700::1111", 443, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
            (socket.AF_UNIX, socket.SOCK_STREAM, 0, "", (b"\x00", 1)),
        ]

    def failing(*args: Any, **kwargs: Any) -> list[Any]:
        raise socket.gaierror(8, "no such name")

    def hanging(*args: Any, **kwargs: Any) -> list[Any]:
        time.sleep(1.0)
        return []

    def empty(*args: Any, **kwargs: Any) -> list[Any]:
        return []

    try:
        socket.getaddrinfo = answering  # type: ignore[assignment]
        c.eq(
            "the addresses, once each, in order, only text",
            mod.system_resolver("example.com", 443, 5.0),
            ["93.184.216.34", "2606:4700:4700::1111", "8.8.8.8"],
        )
        c.eq("the resolver was asked for the name and port, for streams", calls, [(("example.com", 443), {"type": socket.SOCK_STREAM})])
        socket.getaddrinfo = empty  # type: ignore[assignment]
        c.eq("a name with no address", mod.system_resolver("example.com", 443, 5.0), [])
        socket.getaddrinfo = failing  # type: ignore[assignment]
        c.refused("a name that does not resolve", lambda: mod.system_resolver("example.com", 443, 5.0), R.DNS)
        socket.getaddrinfo = hanging  # type: ignore[assignment]
        before = set(threading.enumerate())
        started = time.monotonic()
        c.refused("a resolver that hangs", lambda: mod.system_resolver("example.com", 443, 0.1), R.TIMEOUT)
        c.eq("it is given up on at the limit, not waited for", time.monotonic() - started < 0.6, True)
        c.eq(
            "and the thread it leaves cannot hold the process",
            [t.daemon for t in threading.enumerate() if t not in before and t.is_alive()],
            [True],
        )
    finally:
        socket.getaddrinfo = real  # type: ignore[assignment]
    c.eq(
        "the policy and limits are frozen",
        (mod.FetchPolicy.__dataclass_params__.frozen, mod.FetchLimits.__dataclass_params__.frozen),
        (True, True),
    )  # type: ignore[attr-defined]
    return c.bad


def _no_network(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("a check reached the network")


def failures(mod: types.ModuleType) -> list[str]:
    import socket

    bad: list[str] = []
    real = socket.create_connection
    socket.create_connection = _no_network  # type: ignore[assignment]  # nothing in these checks may send a packet, even in a broken copy of the code
    try:
        for check in (
            check_addresses,
            check_parse_url,
            check_content_types,
            check_fetch,
            check_names,
            check_answers,
            check_events_and_limits,
            check_resolver,
        ):
            try:
                bad += [f"{check.__name__}: {message}" for message in check(mod)]
            except Exception as exc:
                bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    finally:
        socket.create_connection = real  # type: ignore[assignment]
    return bad
