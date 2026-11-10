"""Candle sources: Binance, Coinbase, Kraken, Bybit (all keyless public market data).

Each adapter turns (symbol, timeframe) into a request and parses the venue's reply into candles, oldest
first. Failures are raised as VenueError with a stable `kind` and a FIXED message: upstream response text
is untrusted and is never copied into an error. Nothing here guesses a similar symbol.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import random
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import ClassVar

from .errors import VenueError

USER_AGENT = "Mozilla/5.0 (compatible; indicator-condition-check/0.2)"
INTERVAL_MS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
STABLECOINS = frozenset({"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "USDD", "PYUSD", "USDS", "BUSD", "USDP", "GUSD", "LUSD", "FRAX"})
MAX_BODY_BYTES = 8_000_000
MAX_RETRY_AFTER_S = 3600.0  # a hostile or buggy Retry-After is capped at an hour; the call itself never sleeps past its own deadline
BAN_COOLDOWN_S = 30.0

# (status, lowercase headers, body bytes)
HttpGet = Callable[[str, float], tuple[int, dict, bytes]]


@dataclass(frozen=True)
class Candle:
    ts: int  # open time, ms since epoch (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class CallRecord:
    """Provenance of one upstream request. The body is represented by its hash only."""

    venue: str
    url: str
    attempt: int
    http_status: int | None
    latency_ms: int
    bytes: int
    sha256: str | None
    retry_after_s: float | None
    fetched_at: str
    server_date: str | None = None
    error_kind: str | None = None

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass
class FetchResult:
    candles: list[Candle]
    calls: list[CallRecord] = field(default_factory=list)


def default_http_get(url: str, timeout: float) -> tuple[int, dict, bytes]:
    """GET without raising on HTTP error statuses. `timeout` bounds the WHOLE exchange, not each socket read,
    so a server that trickles bytes cannot outlive it. Timeouts and DNS failures raise OSError."""
    deadline = time.monotonic() + timeout
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - hosts are fixed constants
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, headers, _read_bounded(resp, deadline)
    except urllib.error.HTTPError as e:
        try:
            body = _read_bounded(e, deadline, limit=200_000)
        except OSError:
            body = b""
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, body
    except urllib.error.URLError as e:
        raise OSError(str(e.reason)) from e


def _read_bounded(resp, deadline: float, limit: int = MAX_BODY_BYTES) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        if time.monotonic() > deadline:
            raise OSError("timed out reading the response")
        chunk = resp.read(65536)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            raise OSError("response larger than the allowed size")
        chunks.append(chunk)


def _to_float(x) -> float:
    v = float(x)
    if not math.isfinite(v):
        raise ValueError("non-finite number")
    return v


def _iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_candles(venue: str, rows: list[Candle]) -> list[Candle]:
    """Sort, drop duplicate timestamps, reject implausible prices (high below open/close/low, non-positive
    price, negative volume). Raises VenueError(bad_response)."""
    rows = sorted({c.ts: c for c in rows}.values(), key=lambda c: c.ts)
    for c in rows:
        top, bottom = max(c.open, c.close), min(c.open, c.close)
        if bottom <= 0 or c.high < c.low or c.high < top or c.low > bottom or c.volume < 0:
            raise VenueError(venue, "bad_response")
    return rows


def find_gaps(candles: list[Candle], interval_ms: int) -> list[dict]:
    """Missing candles between consecutive rows (any step larger than one interval is a gap)."""
    gaps = []
    for a, b in zip(candles, candles[1:], strict=False):
        step = b.ts - a.ts
        if step > interval_ms:
            gaps.append({"after_ts": _iso(a.ts), "before_ts": _iso(b.ts), "missing": int(step // interval_ms - 1)})
    return gaps


class Adapter:
    name = ""
    intervals: ClassVar[dict[str, str]] = {}
    max_bars = 300
    hosts: ClassVar[tuple[str, ...]] = ()
    paginated = False

    def pair(self, symbol: str) -> str:
        raise NotImplementedError

    def unified(self, symbol: str) -> tuple[str, str]:
        """(base, quote) as the venue quotes it."""
        raise NotImplementedError

    def url(self, host: str, pair: str, interval: str, limit: int, end_ms: int | None = None) -> str:
        raise NotImplementedError

    def parse(self, status: int, body: bytes, interval: str) -> list[Candle]:
        raise NotImplementedError

    def classify(self, status: int, body: bytes) -> VenueError | None:
        if status == 200:
            return None
        text = body[:300].decode("utf-8", "replace")
        if status in (429, 418):
            return VenueError(self.name, "rate_limited", http_status=status)
        if status in (403, 451):
            return VenueError(self.name, "geo_blocked", http_status=status)
        if status >= 500:
            return VenueError(self.name, "unavailable", http_status=status)
        if self.is_not_listed(status, text):
            return VenueError(self.name, "not_listed", http_status=status)
        return VenueError(self.name, "bad_response", http_status=status)

    def is_not_listed(self, status: int, text: str) -> bool:
        return status in (400, 404)


class Binance(Adapter):
    name = "binance"
    intervals: ClassVar[dict[str, str]] = {"1h": "1h", "4h": "4h", "1d": "1d"}
    max_bars = 1000
    hosts: ClassVar[tuple[str, ...]] = ("https://api.binance.com", "https://data-api.binance.vision")

    def pair(self, symbol):
        return f"{symbol}USDT"

    def unified(self, symbol):
        return symbol, "USDT"

    def url(self, host, pair, interval, limit, end_ms=None):
        return f"{host}/api/v3/klines?symbol={pair}&interval={self.intervals[interval]}&limit={limit}"

    def is_not_listed(self, status, text):
        return status == 400 and "-1121" in text

    def parse(self, status, body, interval):
        rows = json.loads(body)
        return [Candle(int(r[0]), _to_float(r[1]), _to_float(r[2]), _to_float(r[3]), _to_float(r[4]), _to_float(r[5])) for r in rows]


class Coinbase(Adapter):
    name = "coinbase"
    intervals: ClassVar[dict[str, str]] = {"1h": "3600", "1d": "86400"}  # no 4h granularity exists
    max_bars = 300  # documented page size; replies were observed holding 350, so windows are requested in 300-bar spans
    hosts: ClassVar[tuple[str, ...]] = ("https://api.exchange.coinbase.com",)
    paginated = True

    def pair(self, symbol):
        return f"{symbol}-USD"

    def unified(self, symbol):
        return symbol, "USD"

    def url(self, host, pair, interval, limit, end_ms=None):
        base = f"{host}/products/{pair}/candles?granularity={self.intervals[interval]}"
        if end_ms is not None:
            span = INTERVAL_MS[interval] * 300
            return f"{base}&start={_iso(end_ms - span)}&end={_iso(end_ms)}"
        return base

    def is_not_listed(self, status, text):
        return status == 404

    def parse(self, status, body, interval):
        rows = json.loads(body)
        # [time(s), low, high, open, close, volume], newest first
        return [Candle(int(r[0]) * 1000, _to_float(r[3]), _to_float(r[2]), _to_float(r[1]), _to_float(r[4]), _to_float(r[5])) for r in rows]


class Kraken(Adapter):
    name = "kraken"
    intervals: ClassVar[dict[str, str]] = {"1h": "60", "4h": "240", "1d": "1440"}
    max_bars = 720
    hosts: ClassVar[tuple[str, ...]] = ("https://api.kraken.com",)
    _ALIASES: ClassVar[dict[str, str]] = {"BTC": "XBT", "DOGE": "XDG"}

    def pair(self, symbol):
        return f"{self._ALIASES.get(symbol, symbol)}USD"

    def unified(self, symbol):
        return symbol, "USD"

    def url(self, host, pair, interval, limit, end_ms=None):
        return f"{host}/0/public/OHLC?pair={pair}&interval={self.intervals[interval]}"

    def classify(self, status, body):
        err = super().classify(status, body)
        if err or status != 200:
            return err
        try:
            doc = json.loads(body)
        except ValueError:
            return VenueError(self.name, "bad_response", http_status=status)
        errors = doc.get("error") or [] if isinstance(doc, dict) else None
        if not isinstance(errors, list):
            return VenueError(self.name, "bad_response", http_status=status)
        joined = " ".join(str(e) for e in errors)
        if "Unknown asset pair" in joined:
            return VenueError(self.name, "not_listed", http_status=status)
        if "Rate limit" in joined or "Too many requests" in joined:
            return VenueError(self.name, "rate_limited", http_status=status)
        if errors:
            return VenueError(self.name, "bad_response", http_status=status)
        return None

    def parse(self, status, body, interval):
        result = json.loads(body)["result"]
        key = next(k for k in result if k != "last")
        # [time(s), open, high, low, close, vwap, volume, count]
        return [Candle(int(r[0]) * 1000, _to_float(r[1]), _to_float(r[2]), _to_float(r[3]), _to_float(r[4]), _to_float(r[6])) for r in result[key]]


class Bybit(Adapter):
    name = "bybit"
    intervals: ClassVar[dict[str, str]] = {"1h": "60", "4h": "240", "1d": "D"}
    max_bars = 1000
    hosts: ClassVar[tuple[str, ...]] = ("https://api.bybit.com",)

    def pair(self, symbol):
        return f"{symbol}USDT"

    def unified(self, symbol):
        return symbol, "USDT"

    def url(self, host, pair, interval, limit, end_ms=None):
        return f"{host}/v5/market/kline?category=spot&symbol={pair}&interval={self.intervals[interval]}&limit={limit}"

    def classify(self, status, body):
        err = super().classify(status, body)
        if err or status != 200:
            return err
        try:
            doc = json.loads(body)
        except ValueError:
            return VenueError(self.name, "bad_response", http_status=status)
        code = doc.get("retCode") if isinstance(doc, dict) else None
        if code == 0:
            return None
        if code == 10001:
            return VenueError(self.name, "not_listed", http_status=status, venue_code=str(code))
        if code in (10006, 10018):
            return VenueError(self.name, "rate_limited", http_status=status, venue_code=str(code))
        return VenueError(self.name, "bad_response", http_status=status, venue_code=str(code) if isinstance(code, int) else None)

    def parse(self, status, body, interval):
        rows = json.loads(body)["result"]["list"]
        # [start(ms), open, high, low, close, volume, turnover], newest first
        return [Candle(int(r[0]), _to_float(r[1]), _to_float(r[2]), _to_float(r[3]), _to_float(r[4]), _to_float(r[5])) for r in rows]


ADAPTERS: dict[str, Adapter] = {a.name: a for a in (Binance(), Coinbase(), Kraken(), Bybit())}
AUTO_ORDER = ("binance", "coinbase", "kraken", "bybit")

# Per-process memory of venues that told us to back off: venue -> monotonic time before which we do not call.
_NOT_BEFORE: dict[str, float] = {}


def reset_cooldowns() -> None:
    _NOT_BEFORE.clear()


def parse_retry_after(value: str | None, now: dt.datetime) -> float | None:
    """Retry-After as seconds or an HTTP date, clamped to [0, MAX_RETRY_AFTER_S] (an hour). Unparseable -> None."""
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.UTC)
        seconds = (when - now).total_seconds()
    if not math.isfinite(seconds):
        return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_S)


def fetch_candles(
    adapter: Adapter,
    symbol: str,
    interval: str,
    want: int,
    http_get: HttpGet,
    *,
    deadline: float,
    request_timeout: float = 8.0,
    retries: int = 2,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    clock: Callable[[], float] = time.monotonic,
    now_fn: Callable[[], dt.datetime] | None = None,
    max_pages: int = 6,
    calls: list[CallRecord] | None = None,
) -> FetchResult:
    """Fetch up to `want` candles (the forming bar may be included), oldest first, with call provenance.

    Retries 429, 5xx and network errors with full-jitter backoff, honouring Retry-After (an absurd value is capped at an hour; a wait
    longer than the call's deadline is not slept: the venue is left alone for that long and the next venue is tried). Never
    retries a geo block, an unlisted symbol, a 418 ban or a malformed reply. After a 429 or 418 the venue is
    left alone for a cool-down, so a loop of calls cannot turn a rate limit into a ban. Venues whose single
    reply is capped below `want` (Coinbase) are paged backwards, up to `max_pages` requests."""
    if interval not in adapter.intervals:
        raise VenueError(adapter.name, "unsupported_interval")
    now_fn = now_fn or (lambda: dt.datetime.now(dt.UTC))
    rng = rng or random.Random()
    cool_until = _NOT_BEFORE.get(adapter.name, 0.0)
    if clock() < cool_until:
        raise VenueError(adapter.name, "cooling_down", retry_after_s=round(cool_until - clock(), 1))
    pair = adapter.pair(symbol)
    calls = calls if calls is not None else []  # filled even when the fetch ends in an exception
    seen: dict[int, Candle] = {}
    end_ms: int | None = None
    for _ in range(max_pages if adapter.paginated else 1):
        page = _fetch_page(adapter, pair, interval, min(want, adapter.max_bars), end_ms, http_get, deadline, request_timeout, retries, sleep, rng, clock, now_fn, calls)
        before = len(seen)
        for c in page:
            seen.setdefault(c.ts, c)
        if len(seen) >= want or not page or len(seen) == before:
            break
        end_ms = min(c.ts for c in page)  # the next window ends where this one began
    return FetchResult(candles=validate_candles(adapter.name, list(seen.values())), calls=calls)


def _fetch_page(adapter, pair, interval, limit, end_ms, http_get, deadline, request_timeout, retries, sleep, rng, clock, now_fn, calls):
    last: VenueError | None = None
    for host in adapter.hosts:
        url = adapter.url(host, pair, interval, limit, end_ms)
        for attempt in range(retries + 1):
            remaining = deadline - clock()
            if remaining <= 0.2:
                raise last or VenueError(adapter.name, "timeout")
            t0 = clock()
            status = headers = body = None
            err: VenueError | None = None
            try:
                status, headers, body = http_get(url, min(request_timeout, remaining))
            except OSError as e:
                err = VenueError(adapter.name, "timeout" if "time" in str(e).lower() else "unavailable")
            latency = int((clock() - t0) * 1000)
            retry_after = None
            if err is None:
                err = adapter.classify(status, body)
                retry_after = parse_retry_after((headers or {}).get("retry-after"), now_fn())
                if err is not None:
                    err.retry_after_s = retry_after
            calls.append(
                CallRecord(
                    venue=adapter.name,
                    url=url,
                    attempt=attempt + 1,
                    http_status=status,
                    latency_ms=latency,
                    bytes=len(body or b""),
                    sha256=hashlib.sha256(body).hexdigest() if body else None,
                    retry_after_s=retry_after,
                    fetched_at=now_fn().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    server_date=(headers or {}).get("date"),
                    error_kind=err.kind if err else None,
                )
            )
            if err is None:
                try:
                    return adapter.parse(status, body, interval)
                except (ValueError, KeyError, IndexError, TypeError, StopIteration) as e:
                    raise VenueError(adapter.name, "bad_response", http_status=status) from e
            last = err
            if err.kind == "rate_limited":
                _NOT_BEFORE[adapter.name] = clock() + (BAN_COOLDOWN_S if status == 418 else max(retry_after or 0.0, 1.0))
                if retry_after is not None and retry_after > max(0.0, deadline - clock() - 0.2):
                    raise err  # the venue asked for longer than this call can wait: leave it alone for that long, not five seconds
            if err.kind == "geo_blocked":
                break  # try the next host (mirror) if any
            if not err.retryable or status == 418:
                raise err
            if attempt < retries:
                backoff = rng.uniform(0, min(4.0, 0.5 * 2**attempt))
                sleep(min(max(retry_after or 0.0, backoff), max(0.0, deadline - clock() - 0.2)))
    raise last or VenueError(adapter.name, "unavailable")


def clock_skew_s(calls: list[CallRecord], now: dt.datetime) -> float | None:
    """Local clock minus the venue's `Date` header, in seconds, from the most recent call that carried one."""
    for c in reversed(calls):
        if c.server_date:
            try:
                server = parsedate_to_datetime(c.server_date)
            except (TypeError, ValueError):
                continue
            if server.tzinfo is None:
                server = server.replace(tzinfo=dt.UTC)
            return (now - server).total_seconds()
    return None
