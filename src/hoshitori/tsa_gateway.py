"""Time-stamp authorities as gateway providers: the attestation class reaches them through the one door every outside call uses.

``GATEWAY_DESIGN`` makes the gateway the only network egress, the attestation requests of the ledger writer included. A time-stamp
authority is a provider with one operation, ``stamp``, whose arguments are the SHA-256 digest to be stamped (64 lower-case hexadecimal
characters) and the nonce of the request. The transport sends the RFC 3161 query as it is (``application/timestamp-query``) and keeps the
answer as it came; the adapter reads the answer only far enough to say whether the authority granted a token. Whether the token proves
anything (imprint, nonce, chain, key usage, time) is ``tsa.verify_token``'s question, asked later on the stored bytes.

The authority is public and keyless: nothing here sends a credential. The rate prior (6 requests a minute) is far above what a cycle
asks (one request per authority per commitment) and below anything an authority is known to refuse.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from urllib.parse import urlsplit

from asn1crypto import parser, tsp

from hoshitori import tsa
from hoshitori.breaker import Breaker, BreakerConfig, BreakerSet
from hoshitori.capture import RawCapture
from hoshitori.clock import Clock
from hoshitori.connector import ConnectorError, ErrorClass, classify_status, parse_retry_after
from hoshitori.gateway import Gateway, RateObservation, TransportFailure
from hoshitori.governor import Governor, RateModel
from hoshitori.http_transport import HttpTransport

OPERATION: Final = "stamp"
RATE_LIMIT: Final = 6.0
RATE_WINDOW_S: Final = 60.0
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_GRANTED: Final = ("granted", "granted_with_mods")
_MALFORMED_REQUEST: Final = ("bad_alg", "bad_request", "bad_data_format")


class TsaConfigError(ValueError):
    """An authority that cannot be used."""


@dataclass(frozen=True)
class Authority:
    """A time-stamp authority: a short name (it names the provider and the token in a bundle) and its endpoint."""

    name: str
    url: str

    def __post_init__(self) -> None:
        parts = urlsplit(self.url)
        if _NAME.fullmatch(self.name) is None:
            raise TsaConfigError("an authority's name is 1 to 32 characters of a-z, 0-9, - and _")
        if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise TsaConfigError("an authority's URL is an http(s) URL with a host and no credentials, query or fragment")

    @property
    def provider(self) -> str:
        return f"tsa-{self.name}"


def status_of(body: bytes) -> tsp.PKIStatusInfo:
    """The status of a response, read without its token (a refusal has none, and the library's response type insists on one)."""
    _, _, tag, _, content, _ = parser.parse(body, strict=True)
    if tag != 16:
        raise ValueError("a response is a sequence")
    _, _, _, header, first, trailer = parser.parse(content)
    return tsp.PKIStatusInfo.load(header + first + trailer)


class TsaAdapter:
    """What the gateway needs of an authority."""

    def __init__(self, authority: Authority, *, attempt_cap_s: float = 10.0) -> None:
        self.authority = authority
        self.provider = authority.provider
        self._cap = attempt_cap_s

    def operations(self) -> frozenset[str]:
        return frozenset({OPERATION})

    def normalise(self, operation: str, args: Mapping[str, Any]) -> dict[str, Any]:
        if operation != OPERATION:
            raise ConnectorError(ErrorClass.INVALID_ARGS, f"{self.provider} has no operation {operation!r}")
        digest, nonce = args.get("digest"), args.get("nonce")
        if set(args) != {"digest", "nonce"}:
            raise ConnectorError(ErrorClass.INVALID_ARGS, "stamp takes a digest and a nonce")
        if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
            raise ConnectorError(ErrorClass.INVALID_ARGS, "the digest is 64 lower-case hexadecimal characters")
        if isinstance(nonce, bool) or not isinstance(nonce, int) or not 1 <= nonce <= 2**53 - 1:
            raise ConnectorError(ErrorClass.INVALID_ARGS, "the nonce is a whole number from 1 to 2^53 - 1")
        return {"digest": digest, "nonce": nonce}

    def plan(self, operation: str, args: Mapping[str, Any]) -> float:
        return 1.0

    def attempt_cap_s(self, operation: str) -> float:
        return self._cap

    def min_attempt_s(self, operation: str) -> float:
        return 1.0

    def classify(self, capture: RawCapture, now: datetime) -> ConnectorError | None:
        if capture.status is None:
            return ConnectorError(ErrorClass.UPSTREAM, "no response")
        failed = classify_status(capture.status)
        if failed is not None:
            wait = parse_retry_after(capture.headers.get("retry-after"), now)
            return ConnectorError(failed, f"HTTP {capture.status}", retry_after_s=wait)
        try:
            status = status_of(capture.body)
            word, failure = status["status"].native, status["fail_info"].native
        except Exception:  # any shape of hostile body: it is not a response
            return ConnectorError(ErrorClass.MALFORMED, f"{self.provider}: the answer is not a time-stamp response")
        if word in _GRANTED:
            return None
        kind = ErrorClass.INVALID_ARGS if failure and any(name in _MALFORMED_REQUEST for name in failure) else ErrorClass.UPSTREAM
        return ConnectorError(kind, f"{self.provider}: the authority answered {word}")

    def rate_observation(self, capture: RawCapture) -> RateObservation | None:
        return None


class TsaTransport(HttpTransport):
    """Sends an RFC 3161 query to the authority's URL and returns what came back."""

    def __init__(
        self,
        authority: Authority,
        clock: Callable[[], datetime],
        *,
        allow_insecure_loopback: bool = False,
        allow_plain_http: bool = False,
        ca_file: str | None = None,
    ) -> None:
        super().__init__(
            authority.url,
            clock,
            provider=authority.provider,
            allow_insecure_loopback=allow_insecure_loopback,
            allow_plain_http=allow_plain_http,
            ca_file=ca_file,
        )

    def fetch(self, operation: str, args: Mapping[str, Any], timeout_s: float, capture_id: str, sent_at: datetime) -> RawCapture:
        try:
            query, _ = tsa.build_request(bytes.fromhex(str(args["digest"])), int(args["nonce"]))
        except (tsa.TokenError, KeyError, ValueError, TypeError) as exc:
            raise TransportFailure(ErrorClass.INVALID_ARGS, f"the query could not be built: {type(exc).__name__}") from None
        headers = {"Content-Type": "application/timestamp-query", "Accept": "application/timestamp-reply"}
        return self._exchange(operation, args, "POST", self._prefix, query, headers, timeout_s, capture_id, sent_at)


@dataclass(frozen=True)
class TsaAttached:
    adapter: TsaAdapter
    governor: Governor
    breakers: BreakerSet
    transport: TsaTransport


def attach_tsa(
    gateway: Gateway,
    authority: Authority,
    clock: Clock,
    *,
    breaker: BreakerConfig | None = None,
    allow_insecure_loopback: bool = False,
    allow_plain_http: bool = False,
    ca_file: str | None = None,
) -> TsaAttached:
    """Register an authority with ``gateway`` and return its parts; the transport goes in the runner's table under its provider."""
    adapter = TsaAdapter(authority)
    now = clock.now()
    governor = Governor(adapter.provider, RateModel.FIXED_WINDOW, RATE_LIMIT, RATE_WINDOW_S, now)
    settings = breaker or BreakerConfig()
    breakers = BreakerSet(Breaker(adapter.provider, settings, now), settings)
    gateway.register(adapter, governor, breakers, concurrency=1)
    transport = TsaTransport(
        authority, clock.now, allow_insecure_loopback=allow_insecure_loopback, allow_plain_http=allow_plain_http, ca_file=ca_file
    )
    return TsaAttached(adapter, governor, breakers, transport)
