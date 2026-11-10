"""The shared words of the safe fetch client: the reasons a fetch is refused or fails, and what a hop and a fetch hand back."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from hoshitori.connector import ErrorClass


class FetchCode(StrEnum):
    INVALID_URL = "INVALID_URL"  # not an address we can read as one URL (control characters, too long, not ASCII, no host)
    SCHEME = "SCHEME"  # not https (or http to a host that was allowed to be plain)
    HTTP_NOT_ALLOWED = "HTTP_NOT_ALLOWED"
    CREDENTIALS = "CREDENTIALS"  # user information in the address
    PORT = "PORT"
    HOST_INVALID = "HOST_INVALID"  # not a name in letters, digits and hyphens with a real top-level label (no numeric form of an address)
    HOST_NOT_ALLOWED = "HOST_NOT_ALLOWED"  # outside the allow-list of the connector
    DNS = "DNS"
    ADDRESS_NOT_PUBLIC = "ADDRESS_NOT_PUBLIC"  # the name resolves, in whole or in part, to an address that is not globally routable
    TOO_MANY_REDIRECTS = "TOO_MANY_REDIRECTS"
    REDIRECT_INVALID = "REDIRECT_INVALID"
    HTTP_STATUS = "HTTP_STATUS"
    CONTENT_TYPE = "CONTENT_TYPE"
    CONTENT_ENCODING = "CONTENT_ENCODING"  # a compressed answer: never decoded, so never expanded
    BAD_HEADERS = "BAD_HEADERS"
    TOO_LARGE = "TOO_LARGE"
    TRUNCATED = "TRUNCATED"
    TIMEOUT = "TIMEOUT"
    CONNECT = "CONNECT"
    TLS = "TLS"
    PROTOCOL = "PROTOCOL"


# The class a refusal or failure takes in the connector taxonomy (what the caller may do about it).
CLASS_OF: dict[FetchCode, ErrorClass] = {
    FetchCode.INVALID_URL: ErrorClass.INVALID_ARGS,
    FetchCode.SCHEME: ErrorClass.INVALID_ARGS,
    FetchCode.HTTP_NOT_ALLOWED: ErrorClass.INVALID_ARGS,
    FetchCode.CREDENTIALS: ErrorClass.INVALID_ARGS,
    FetchCode.PORT: ErrorClass.INVALID_ARGS,
    FetchCode.HOST_INVALID: ErrorClass.INVALID_ARGS,
    FetchCode.HOST_NOT_ALLOWED: ErrorClass.INVALID_ARGS,
    FetchCode.ADDRESS_NOT_PUBLIC: ErrorClass.INVALID_ARGS,
    FetchCode.REDIRECT_INVALID: ErrorClass.MALFORMED,
    FetchCode.TOO_MANY_REDIRECTS: ErrorClass.MALFORMED,
    FetchCode.DNS: ErrorClass.NOT_FOUND,
    FetchCode.HTTP_STATUS: ErrorClass.UPSTREAM,
    FetchCode.CONTENT_TYPE: ErrorClass.MALFORMED,
    FetchCode.CONTENT_ENCODING: ErrorClass.MALFORMED,
    FetchCode.BAD_HEADERS: ErrorClass.MALFORMED,
    FetchCode.TOO_LARGE: ErrorClass.MALFORMED,
    FetchCode.TRUNCATED: ErrorClass.UPSTREAM,
    FetchCode.TIMEOUT: ErrorClass.TIMEOUT,
    FetchCode.CONNECT: ErrorClass.UPSTREAM,
    FetchCode.TLS: ErrorClass.UPSTREAM,
    FetchCode.PROTOCOL: ErrorClass.MALFORMED,
}


@dataclass(frozen=True)
class FetchEvent:
    """One thing that happened: ``request``, ``resolved``, ``redirect`` or ``refused``. URLs are redacted; addresses are for operators."""

    hop: int
    kind: str
    url: str
    detail: str


class FetchError(Exception):
    """A fetch that was refused before it was made, or that failed. ``events`` is what happened before it."""

    def __init__(self, code: FetchCode, detail: str, *, status: int | None = None, events: Sequence[FetchEvent] = ()) -> None:
        super().__init__(f"{code.value}: {detail}")
        self.code = code
        self.detail = detail
        self.status = status
        self.events = tuple(events)

    @property
    def error_class(self) -> ErrorClass:
        if self.code is FetchCode.HTTP_STATUS and self.status is not None:
            if self.status == 404 or self.status == 410:
                return ErrorClass.NOT_FOUND
            if self.status == 429:
                return ErrorClass.RATE_LIMIT
            if self.status in (401, 403):
                return ErrorClass.AUTH
            if self.status == 451:
                return ErrorClass.GEO_BLOCKED
        return CLASS_OF[self.code]


@dataclass(frozen=True)
class Target:
    """An address that passed the checks: a name, a port from the allowed list, a path; never an address literal or credentials."""

    url: str  # normalised: scheme, lower-case host, the port only when it is not the default, path and query, no fragment
    scheme: str
    host: str
    port: int
    path_query: str


@dataclass(frozen=True)
class HopResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes
    address: str  # the checked address the connection was made to


Screen = Callable[[int, tuple[tuple[str, str], ...]], bool]
Hop = Callable[[Target, Sequence[str], float, Screen, int], HopResponse]


@dataclass(frozen=True)
class Fetched:
    """An answer from the outside, as untrusted as anything gets: bytes and what the server said of them. Nothing here is an instruction."""

    url: str  # the address of the last hop
    status: int
    content_type: str  # the media type, lower case, without parameters
    charset: str | None
    body: bytes
    sha256: str
    redirects: tuple[str, ...]  # every address asked for before this one, in order
    address: str
    events: tuple[FetchEvent, ...]
