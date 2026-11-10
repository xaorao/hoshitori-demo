"""The safe fetch client: how an address a person pastes, or a source cites, is read without the product becoming a way into its
own network.

``GATEWAY_DESIGN`` section 6 and ``SECURITY_MODEL`` section 5 give the rules; this module makes them true in code, not in a
prompt:

* **Only a name, only https.** The address is ASCII with no control characters, has no user information, names a host in
  letters, digits and hyphens whose last label is letters (so no numeric form of an address: ``127.1``, ``2130706433``,
  ``0x7f.0.0.1`` and ``0177.0.0.1`` are read differently by different resolvers), and uses the default port of its scheme.
  Plain ``http`` is only for hosts the caller named.
* **Resolve once, check every address, connect to the checked one.** The name is resolved by us. Each address it resolves to
  must be globally routable (an IPv4-mapped form is judged by the address inside it, and the transition forms that carry an
  address inside another are refused). One address that is not makes the whole name refused, so an answer that mixes public
  and private does not pass. The connection is made to the checked address, so a server that answers a second look-up
  differently is never asked.
* **Redirects are ours.** At most three, each one read, resolved and checked as a new address, always as ``GET``.
* **Nothing from the user goes out.** No cookie, no credential, no header of theirs; the answer's cookies are ignored.
* **Bounded.** One deadline for the whole fetch, resolution and redirects included; a body limit (2 MB by default) enforced
  while reading and on the announced length; only listed content types; a compressed answer is refused and never decoded, so
  it cannot expand.
* **The result is data.** ``Fetched`` holds bytes, what the server said about them and their digest. It is never an
  instruction.

The network is behind ``hop`` (``safe_fetch_net.PinnedHop``) and the name service behind ``resolver``, so every rule here is
tested without either.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urljoin, urlsplit

from hoshitori.capture import redact_url
from hoshitori.fetch_types import FetchCode, Fetched, FetchError, FetchEvent, Hop, Target
from hoshitori.safe_fetch_net import PinnedHop

DEFAULT_TYPES: Final = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "text/plain",
        "application/json",
        "application/xml",
        "text/xml",
        "application/rss+xml",
        "application/atom+xml",
    }
)
REDIRECTS: Final = frozenset({301, 302, 303, 307, 308})
MAX_URL: Final = 2048
LABEL: Final = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
TOP_LABEL: Final = re.compile(r"[a-z]{2,63}|xn--[a-z0-9-]{1,59}")
MEDIA: Final = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]{0,126}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}")
LENGTH: Final = re.compile(r"[0-9]{1,12}")
# IPv6 ranges that reach into a network of ours or a neighbour's, or carry an IPv4 address to be judged as one: the compatible
# and translation prefixes, 6to4 and Teredo, and the retired site-local (fec0::/10) and 6bone (3ffe::/16) ranges that Python calls global
TRANSITION: Final = tuple(
    ipaddress.ip_network(net)
    for net in (
        "::/96",
        "64:ff9b::/96",
        "64:ff9b:1::/48",
        "2002::/16",
        "2001::/32",
        "fec0::/10",
        "3ffe::/16",
    )
)


@dataclass(frozen=True)
class FetchPolicy:
    allow_http_hosts: frozenset[str] = frozenset()  # hosts that may be fetched over plain http (none, unless a connector needs one)
    allowed_hosts: frozenset[str] | None = None  # if set, a host must be one of these or a subdomain of one
    content_types: frozenset[str] = DEFAULT_TYPES
    https_ports: frozenset[int] = frozenset({443})
    http_ports: frozenset[int] = frozenset({80})


@dataclass(frozen=True)
class FetchLimits:
    max_body_bytes: int = 2 * 1024 * 1024
    timeout_s: float = 20.0
    max_redirects: int = 3
    max_addresses: int = 8  # how many of the checked addresses are tried
    max_resolved: int = 64  # a name that resolves to more than this is refused

    def __post_init__(self) -> None:
        if (
            self.max_body_bytes < 1024
            or self.timeout_s <= 0
            or not 0 <= self.max_redirects <= 10
            or self.max_addresses < 1
            or self.max_resolved < self.max_addresses
        ):
            raise ValueError("the fetch limits are not valid")


def is_public_address(address: str) -> bool:
    """Is this address one that anyone on the internet can reach, and not one that leads into a network of ours or of a neighbour?"""
    if "%" in address:
        return False
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif any(ip in net for net in TRANSITION):
            return False
    return ip.is_global and not ip.is_multicast


def parse_url(raw: str, policy: FetchPolicy) -> Target:
    """Read an address, or refuse it. Nothing is looked up and nothing is sent."""
    if not isinstance(raw, str) or not raw or len(raw) > MAX_URL:
        raise FetchError(FetchCode.INVALID_URL, "the address is empty or longer than 2048 characters")
    if _unclean(raw):
        raise FetchError(FetchCode.INVALID_URL, "the address has a space, a control or non-ASCII character or a backslash")
    try:
        parts = urlsplit(raw)
        port = parts.port
        host = parts.hostname
    except ValueError:
        raise FetchError(FetchCode.INVALID_URL, "the address cannot be read") from None
    if parts.scheme not in ("https", "http"):
        raise FetchError(FetchCode.SCHEME, "only https addresses are fetched")
    if "@" in parts.netloc:
        raise FetchError(FetchCode.CREDENTIALS, "the address carries user information")
    if not host:
        raise FetchError(FetchCode.INVALID_URL, "the address has no host")
    name = _host_name(host)
    secure = parts.scheme == "https"
    if not secure and name not in policy.allow_http_hosts:
        raise FetchError(FetchCode.HTTP_NOT_ALLOWED, "this host is not fetched over plain http")
    default = 443 if secure else 80
    if (default if port is None else port) not in (policy.https_ports if secure else policy.http_ports):
        raise FetchError(FetchCode.PORT, "the port is not allowed")
    if policy.allowed_hosts is not None and not any(name == allowed or name.endswith("." + allowed) for allowed in policy.allowed_hosts):
        raise FetchError(FetchCode.HOST_NOT_ALLOWED, "the host is not on the allow-list of this connector")
    path_query = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    shown_port = f":{port}" if port is not None and port != default else ""
    return Target(f"{parts.scheme}://{name}{shown_port}{path_query}", parts.scheme, name, default if port is None else port, path_query)


def _unclean(text: str) -> bool:
    """A space, a control or non-ASCII character or a backslash: not an address a browser sends, and read differently by parsers."""
    return any(not 0x20 < ord(char) < 0x7F or char == "\\" for char in text)


def _shown(url: str) -> str:
    try:
        return redact_url(url)
    except ValueError:
        return "(an address that cannot be read)"


def _host_name(host: str) -> str:
    name = host.removesuffix(".")
    labels = name.split(".")
    if len(name) > 253 or len(labels) < 2 or not all(LABEL.fullmatch(label) for label in labels) or TOP_LABEL.fullmatch(labels[-1]) is None:
        raise FetchError(FetchCode.HOST_INVALID, "the host is not a name in letters, digits and hyphens with a top-level label")
    return name


def parse_content_type(value: str | None) -> tuple[str, str | None]:
    """The media type and the declared charset of a ``Content-Type`` header; an answer without a readable one is refused."""
    if value is None:
        raise FetchError(FetchCode.CONTENT_TYPE, "the answer does not say what it is")
    media, _, rest = value.partition(";")
    media = media.strip().lower()
    if MEDIA.fullmatch(media) is None:
        raise FetchError(FetchCode.CONTENT_TYPE, "the content type cannot be read")
    charset = None
    for parameter in rest.split(";"):
        key, _, setting = parameter.partition("=")
        if key.strip().lower() == "charset":
            charset = setting.strip().strip('"').lower() or None
    return media, charset


def system_resolver(host: str, port: int, timeout_s: float) -> list[str]:
    """The addresses of a name from the operating system's resolver, within a time limit. A resolver that hangs is abandoned."""
    found: list[Any] = []
    failed: list[OSError] = []

    def look() -> None:
        try:
            found.extend(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError as exc:
            failed.append(exc)

    worker = threading.Thread(target=look, daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        raise FetchError(FetchCode.TIMEOUT, "the name did not resolve in time")
    if failed:
        raise FetchError(FetchCode.DNS, "the name did not resolve")
    addresses: list[str] = []
    for entry in found:
        address = entry[4][0]
        if isinstance(address, str) and address not in addresses:
            addresses.append(address)
    return addresses


class SafeFetcher:
    def __init__(
        self,
        policy: FetchPolicy | None = None,
        limits: FetchLimits | None = None,
        *,
        resolver: Callable[[str, int, float], Sequence[str]] = system_resolver,
        public: Callable[[str], bool] = is_public_address,
        hop: Hop | None = None,
        ca_file: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.policy = policy or FetchPolicy()
        self.limits = limits or FetchLimits()
        self._resolver = resolver
        self._public = public
        self._hop: Hop = hop or PinnedHop(ca_file=ca_file)
        self._clock = clock

    def fetch(self, url: str) -> Fetched:
        """Fetch an address, or raise ``FetchError`` with the reason and what happened before it."""
        deadline = self._clock() + self.limits.timeout_s
        events: list[FetchEvent] = []
        visited: list[str] = []
        current = url
        try:
            for hop in range(self.limits.max_redirects + 1):
                target = parse_url(current, self.policy)
                visited.append(target.url)
                events.append(FetchEvent(hop, "request", redact_url(target.url), ""))
                addresses = self._addresses(target, deadline)
                events.append(FetchEvent(hop, "resolved", redact_url(target.url), ",".join(addresses)))
                response = self._hop(target, addresses, self._remaining(deadline), self._screen, self.limits.max_body_bytes)
                if response.status in REDIRECTS:
                    current = self._next(target, response.headers)
                    events.append(FetchEvent(hop, "redirect", redact_url(target.url), _shown(current)))
                    continue
                self._screen(response.status, response.headers)  # the hop was told to; a hop that did not is caught here
                if len(response.body) > self.limits.max_body_bytes:
                    raise FetchError(FetchCode.TOO_LARGE, f"the answer is longer than {self.limits.max_body_bytes} bytes")
                media, charset = parse_content_type(self._one(response.headers, "content-type"))
                digest = hashlib.sha256(response.body).hexdigest()
                return Fetched(
                    target.url, response.status, media, charset, response.body, digest, tuple(visited[:-1]), response.address, tuple(events)
                )
            raise FetchError(FetchCode.TOO_MANY_REDIRECTS, f"more than {self.limits.max_redirects} redirects")
        except FetchError as error:
            events.append(FetchEvent(hop, "refused", _shown(current), f"{error.code.value}: {error.detail}"))
            raise FetchError(error.code, error.detail, status=error.status, events=events) from None

    def _next(self, target: Target, headers: Sequence[tuple[str, str]]) -> str:
        location = self._one(headers, "location")
        if location is None or not location.strip() or len(location.strip()) > MAX_URL or _unclean(location.strip()):
            raise FetchError(FetchCode.REDIRECT_INVALID, "the redirect has no usable Location")
        return urljoin(target.url, location.strip())

    def _remaining(self, deadline: float) -> float:
        left = deadline - self._clock()
        if left <= 0:
            raise FetchError(FetchCode.TIMEOUT, f"no complete answer within {self.limits.timeout_s:g} s")
        return left

    def _addresses(self, target: Target, deadline: float) -> tuple[str, ...]:
        found = list(self._resolver(target.host, target.port, self._remaining(deadline)))
        if not found or len(found) > self.limits.max_resolved:
            raise FetchError(FetchCode.DNS, "the name has no address, or too many")
        if not all(self._public(address) for address in found):
            raise FetchError(FetchCode.ADDRESS_NOT_PUBLIC, f"{target.host} resolves to an address that is not public")
        return tuple(found[: self.limits.max_addresses])

    @staticmethod
    def _one(headers: Sequence[tuple[str, str]], name: str) -> str | None:
        values = [value for key, value in headers if key.lower() == name]
        if len(values) > 1:
            raise FetchError(FetchCode.BAD_HEADERS, f"the answer repeats {name}")
        return values[0] if values else None

    def _screen(self, status: int, headers: tuple[tuple[str, str], ...]) -> bool:
        """Whether the body is worth reading, from the status and headers. A redirect has none we read; anything but 200 is refused."""
        if status in REDIRECTS:
            return False
        if status != 200:
            raise FetchError(FetchCode.HTTP_STATUS, f"the server answered {status}", status=status)
        encoding = self._one(headers, "content-encoding")
        if encoding is not None and encoding.strip().lower() != "identity":
            raise FetchError(FetchCode.CONTENT_ENCODING, "the answer is encoded; it is not decoded")
        media, _ = parse_content_type(self._one(headers, "content-type"))
        if media not in self.policy.content_types:
            raise FetchError(FetchCode.CONTENT_TYPE, f"{media} is not a type we read")
        declared = self._one(headers, "content-length")
        if declared is not None:
            if LENGTH.fullmatch(declared.strip()) is None:
                raise FetchError(FetchCode.BAD_HEADERS, "the announced length is not a number")
            if int(declared) > self.limits.max_body_bytes:
                raise FetchError(FetchCode.TOO_LARGE, f"the answer announces {declared} bytes")
        return True


__all__ = ["FetchLimits", "FetchPolicy", "SafeFetcher", "is_public_address", "parse_content_type", "parse_url", "system_resolver"]
