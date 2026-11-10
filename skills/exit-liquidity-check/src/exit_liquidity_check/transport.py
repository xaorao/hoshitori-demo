"""HTTP for the remote checks: one real transport (urllib, no redirect followed, size and time bounded) and a caller that adds the time
budget, retries with full-jitter backoff, Retry-After (capped), a cool-down after a rate limit, fixed failure reasons and a provenance
record per attempt.

The rules follow the indicator skill's (``venues.py``): retry 429, 5xx, network errors and time-outs; never retry another 4xx or an unusable
reply; after a 429 leave the host alone for a while so a polling loop cannot turn a rate limit into a ban. Upstream text never leaves here:
a failure is a ``SourceError`` whose message is the fixed text of its reason.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import math
import random
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

from . import __version__
from .errors import SourceError

USER_AGENT = f"exit-liquidity-check/{__version__} (read-only research skill)"
MAX_BODY_BYTES = 16_000_000
MAX_RETRY_AFTER_S = 5.0
COOLDOWN_MIN_S = 1.0
MIN_LEFT_S = 0.2
RETRYABLE = frozenset({"rate_limited", "server_error", "network_error", "timeout"})
LOCAL_HOSTS = ("127.0.0.1", "localhost")

# Per-process memory of hosts that asked us to back off: host -> monotonic time before which nothing is sent to it.
_NOT_BEFORE: dict[str, float] = {}


def reset_cooldowns() -> None:
    _NOT_BEFORE.clear()


@dataclass(frozen=True)
class HttpReply:
    status: int
    headers: dict[str, str]  # names in lower case
    body: bytes


class Transport(Protocol):
    live: bool

    def __call__(self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> HttpReply: ...


class BodyTooLarge(OSError):
    """The reply was larger than the transport accepts."""


class CertificateError(OSError):
    """The source's TLS certificate could not be verified with this machine's trust store (see the README: SSL_CERT_FILE)."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


class UrllibTransport:
    """The real transport. A 3xx comes back as a reply; following it is the caller's decision. Raises TimeoutError or OSError."""

    live = True

    def __init__(self, max_body: int = MAX_BODY_BYTES, context: ssl.SSLContext | None = None) -> None:
        self.max_body = max_body
        self.context = context  # the TLS trust to verify with; none means the platform's default

    def __call__(self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> HttpReply:
        parts = urlsplit(url)
        if not (parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOCAL_HOSTS)):
            raise ValueError("only https URLs (or http to this machine) are fetched")
        deadline = time.monotonic() + timeout
        request = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT, **headers}, method=method)  # noqa: S310 - checked above
        handlers: list[Any] = [_NoRedirect]
        if self.context is not None:
            handlers.append(urllib.request.HTTPSHandler(context=self.context))
        opener = urllib.request.build_opener(*handlers)
        try:
            with opener.open(request, timeout=timeout) as resp:
                return HttpReply(resp.status, {k.lower(): v for k, v in resp.headers.items()}, self._read(resp, deadline))
        except urllib.error.HTTPError as err:
            with err:
                return HttpReply(err.code, {k.lower(): v for k, v in (err.headers or {}).items()}, self._read(err, deadline))
        except urllib.error.URLError as err:
            if isinstance(err.reason, ssl.SSLCertVerificationError):
                raise CertificateError("the source's certificate could not be verified") from err
            if isinstance(err.reason, TimeoutError):
                raise TimeoutError("the source did not answer in time") from err
            raise OSError("the source could not be reached") from err

    def _read(self, resp: Any, deadline: float) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError("the reply took too long")
            chunk = resp.read(65536)
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > self.max_body:
                raise BodyTooLarge("the reply is larger than the allowed size")
            chunks.append(chunk)


@dataclass
class CallRecord:
    """Provenance of one upstream attempt (``source_calls``). The body is represented by its size and hash only."""

    source: str
    url: str
    method: str
    attempt: int
    http_status: int | None
    latency_ms: int
    bytes: int
    sha256: str | None
    fetched_at: str
    error_kind: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_retry_after(value: str | None, now: dt.datetime) -> float | None:
    """Retry-After as seconds or an HTTP date, clamped to [0, MAX_RETRY_AFTER_S]; None when missing or unreadable."""
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        try:
            when = parsedate_to_datetime(value.strip())
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:  # a date written "-0000" carries no zone; it is UTC
            when = when.replace(tzinfo=dt.UTC)
        seconds = (when - now).total_seconds()
    if not math.isfinite(seconds):
        return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_S)


def _classify(reply: HttpReply, now: dt.datetime) -> SourceError | None:
    if 200 <= reply.status < 300:  # a redirect is not followed and is not an answer
        return None
    if reply.status == 429:
        return SourceError("rate_limited", reply.status, parse_retry_after(reply.headers.get("retry-after"), now))
    if reply.status >= 500:
        return SourceError("server_error", reply.status, parse_retry_after(reply.headers.get("retry-after"), now))
    return SourceError("bad_response", reply.status)


class Caller:
    """One call's view of the network: a shared deadline, the records of every attempt, and the retry rules."""

    def __init__(
        self,
        transport: Transport,
        *,
        now: dt.datetime,
        deadline: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float], float] | None = None,
        cooldowns: dict[str, float] | None = None,
    ) -> None:
        self.transport = transport
        self.now = now
        self.deadline = deadline
        self.clock = clock
        self.sleep = sleep
        self.jitter = jitter if jitter is not None else (lambda cap: random.uniform(0.0, cap))  # noqa: S311 - backoff jitter, not security
        self.cooldowns = _NOT_BEFORE if cooldowns is None else cooldowns
        self.started = clock()
        self.records: list[CallRecord] = []

    def remaining(self) -> float:
        return self.deadline - self.clock()

    def _stamp(self, at: float) -> str:
        return (self.now + dt.timedelta(seconds=at - self.started)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def request(
        self,
        source: str,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout_s: float = 8.0,
        retries: int = 2,
        shown_url: str | None = None,
    ) -> HttpReply:
        host = urlsplit(url).hostname or ""
        if retries < 0:
            raise ValueError("retries must be 0 or more")
        until = self.cooldowns.get(host)
        if until is not None and self.clock() < until:
            raise SourceError("cooling_down", retry_after_s=round(until - self.clock(), 1))
        last: SourceError | None = None
        for attempt in range(retries + 1):
            left = self.remaining()
            if left <= MIN_LEFT_S + 1e-9:  # the wait below leaves exactly MIN_LEFT_S; the tolerance keeps float noise from one more try
                raise last or SourceError("budget_exhausted")
            started = self.clock()
            reply: HttpReply | None = None
            err: SourceError | None
            try:
                reply = self.transport(method, url, dict(headers or {}), body, min(timeout_s, left))
            except BodyTooLarge:
                err = SourceError("bad_response")
            except CertificateError:
                err = SourceError("tls_error")
            except TimeoutError:
                err = SourceError("timeout")
            except OSError:
                err = SourceError("network_error")
            else:
                err = _classify(reply, self.now)
            data = reply.body if reply is not None else b""
            self.records.append(
                CallRecord(
                    source=source,
                    url=shown_url or url,
                    method=method,
                    attempt=attempt + 1,
                    http_status=None if reply is None else reply.status,
                    latency_ms=round((self.clock() - started) * 1000),
                    bytes=len(data),
                    sha256=hashlib.sha256(data).hexdigest() if data else None,
                    fetched_at=self._stamp(started),
                    error_kind=None if err is None else err.reason,
                )
            )
            if err is None and reply is not None:
                return reply
            assert err is not None
            last = err
            if err.reason == "rate_limited":
                self.cooldowns[host] = self.clock() + max(err.retry_after_s or 0.0, COOLDOWN_MIN_S)
            if err.reason not in RETRYABLE or attempt == retries:
                raise err
            backoff = self.jitter(min(4.0, 0.5 * 2**attempt))
            self.sleep(min(max(err.retry_after_s or 0.0, backoff), max(0.0, self.deadline - self.clock() - MIN_LEFT_S)))
        raise last or SourceError("budget_exhausted")  # pragma: no cover - the loop always returns or raises
