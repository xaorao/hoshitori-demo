"""The exchange adapters' shared part: three operations, canonical arguments, and one way to classify an answer.

Every venue offers the same three read-only operations to the gateway:

* ``time``        the venue's own clock (the unfinished-bar rule and the settlement oracle need it);
* ``candles``     the bars of one instrument that open in ``[start, end)``, for an interval the venue supports;
* ``instruments`` the venue's list of tradable instruments.

A venue is a subclass that says how to *ask* (``build_request``), how to *read* (``parse_*_body``) and what its own errors look like
(``error_in_body``); this class does the rest: the arguments are checked and made canonical (so identical calls share a fetch), an answer is
classified from its status, its headers and the venue's own error body, and a 200 whose body does not fit is ``MALFORMED``. Adapters never
retry, hold no socket and read no clock; the gateway and ``VenueTransport`` do those.

Two rules that no venue is trusted to keep for us:

* the interval asked for is the interval of every bar that comes back (``check_bars`` holds each bar to the grid and the length of the
  interval asked for): OKX accepted ``bar=2m`` and answered with one-minute bars, which then fall off a two-minute grid;
* a bar is complete only if the venue's clock says its end has passed (``venue_data.build_series``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, ClassVar, Final

from hoshitori import jsonnum
from hoshitori.capture import RawCapture
from hoshitori.connector import ConnectorError, ErrorClass, classify_status, parse_retry_after
from hoshitori.gateway import RateObservation
from hoshitori.governor import RateModel
from hoshitori.timeutil import TimeError, format_rfc3339_ms, parse_rfc3339
from hoshitori.venue_data import Candle, CandleSeries, InstrumentList, VenueDataError, build_series, check_bars

OPERATIONS: Final = ("time", "candles", "instruments")
MAX_WINDOW_BARS: Final = 300  # the smallest page any of the venues serves, so one call is one page everywhere
MESSAGE_CUT: Final = 200


@dataclass(frozen=True)
class VenueRequest:
    method: str  # GET or POST
    path: str  # the path with its query string, below the host the transport is configured with
    body: bytes | None = None  # canonical JSON, for POST


@dataclass(frozen=True)
class VenueRate:
    """The limit a venue documents for public requests, in the units of the adapter's ``cost``, as the governor models it: ``FIXED_WINDOW``
    is ``limit`` per window; ``TOKEN_BUCKET`` holds ``limit`` and refills ``limit / window_s`` a second (the documented sustained rate, with
    the documented burst as the capacity)."""

    model: RateModel
    limit: float
    window_s: float

    def __post_init__(self) -> None:
        if not isinstance(self.model, RateModel) or not self.limit > 0 or not self.window_s > 0:
            raise ValueError("a rate has a model, a positive limit and a positive window")


@dataclass(frozen=True)
class VenueConfig:
    attempt_cap_s: float = 20.0
    min_attempt_s: float = 1.0
    max_window_bars: int = MAX_WINDOW_BARS

    def __post_init__(self) -> None:
        if not 0 < self.min_attempt_s <= self.attempt_cap_s:
            raise ValueError("the minimum attempt must be positive and no longer than the cap")
        if not 1 <= self.max_window_bars <= MAX_WINDOW_BARS:
            raise ValueError(f"a window holds from 1 to {MAX_WINDOW_BARS} bars")


def _invalid(message: str) -> ConnectorError:
    return ConnectorError(ErrorClass.INVALID_ARGS, message)


def parse_time(text: object, what: str) -> datetime:
    if not isinstance(text, str):
        raise _invalid(f"{what} must be an RFC 3339 time with an offset")
    try:
        return parse_rfc3339(text)
    except TimeError as exc:
        raise _invalid(f"{what} is not an RFC 3339 time with an offset: {text!r}") from exc


def _interval_of(arguments: Mapping[str, Any]) -> int:
    interval = arguments.get("interval_s")
    if isinstance(interval, bool) or not isinstance(interval, int):
        raise VenueDataError("the capture does not hold a whole-second interval")
    return interval


def date_header(headers: Mapping[str, str]) -> datetime | None:
    """The server's clock from the ``Date`` header (to the second), or None."""
    value = headers.get("date")
    if value is None:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


class VenueAdapter:
    """``gateway.Adapter`` for one venue. Subclasses fill in the venue's own ways."""

    venue: ClassVar[str]
    intervals: ClassVar[Mapping[int, Any]]  # interval in seconds -> the venue's spelling of it
    instrument_kinds: ClassVar[tuple[str, ...]] = ()  # the values of the optional ``kind`` argument of ``instruments``
    rate: ClassVar[VenueRate]  # the documented public limit; the gateway's governor for this venue is built from it
    max_answer_bytes: ClassVar[int] = (
        4 * 1024 * 1024
    )  # the largest answer the transport reads; a venue whose instrument list is larger says so

    def __init__(self, config: VenueConfig | None = None, provider: str | None = None) -> None:
        self.provider = provider or self.venue
        self.config = config or VenueConfig()

    # --- what a subclass says -------------------------------------------------------------------------------------------------------
    def valid_instrument(self, raw_id: str) -> bool:
        raise NotImplementedError

    def build_request(self, operation: str, args: Mapping[str, Any]) -> VenueRequest:
        raise NotImplementedError

    def parse_time_body(self, body: bytes) -> datetime:
        raise NotImplementedError

    def parse_candles_body(self, body: bytes, args: Mapping[str, Any]) -> list[Candle]:
        raise NotImplementedError

    def parse_instruments_body(self, body: bytes, args: Mapping[str, Any]) -> InstrumentList:
        raise NotImplementedError

    def error_in_body(self, status: int, body: bytes, headers: Mapping[str, str], now: datetime) -> ConnectorError | None:
        """The venue's own error, if its body says so (even with a 200). None when the body carries none."""
        return None

    def cost(self, operation: str, args: Mapping[str, Any]) -> float:
        return 1.0

    def observe_rate(self, headers: Mapping[str, str]) -> RateObservation | None:
        return None

    # --- gateway.Adapter -----------------------------------------------------------------------------------------------------------
    def operations(self) -> frozenset[str]:
        return frozenset(OPERATIONS)

    def normalise(self, operation: str, args: Mapping[str, Any]) -> dict[str, Any]:
        if operation == "time":
            if args:
                raise _invalid("time takes no arguments")
            return {}
        if operation == "instruments":
            return self._normalise_instruments(args)
        if operation != "candles":
            raise _invalid(f"{self.venue} has no operation {operation!r}")
        unknown = set(args) - {"instrument", "interval_s", "start", "end"}
        missing = {"instrument", "interval_s", "start", "end"} - set(args)
        if unknown or missing:
            raise _invalid(f"candles takes instrument, interval_s, start and end (unknown {sorted(unknown)}, missing {sorted(missing)})")
        instrument, interval = args["instrument"], args["interval_s"]
        if not isinstance(instrument, str) or not self.valid_instrument(instrument):
            raise _invalid(f"{instrument!r} is not an instrument id of {self.venue}")
        if isinstance(interval, bool) or not isinstance(interval, int) or interval not in self.intervals:
            raise _invalid(f"{self.venue} has no {interval!r} s interval (it has {sorted(self.intervals)})")
        start, end = parse_time(args["start"], "start"), parse_time(args["end"], "end")
        if not start < end:
            raise _invalid("start must be before end")
        bars = (end - start) / timedelta(seconds=interval)
        if bars > self.config.max_window_bars:
            raise _invalid(f"the window holds {bars:.0f} bars; one call serves at most {self.config.max_window_bars}")
        return {"end": format_rfc3339_ms(end), "instrument": instrument, "interval_s": interval, "start": format_rfc3339_ms(start)}

    def _normalise_instruments(self, args: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(args) - {"kind"}
        if unknown:
            raise _invalid(f"instruments takes only kind (unknown {sorted(unknown)})")
        if "kind" not in args:
            return {}
        kind = args["kind"]
        if kind not in self.instrument_kinds:
            raise _invalid(f"{self.venue} lists instruments of kind {list(self.instrument_kinds)}, not {kind!r}")
        return {"kind": kind}

    def plan(self, operation: str, args: Mapping[str, Any]) -> float:
        return self.cost(operation, args)

    def attempt_cap_s(self, operation: str) -> float:
        return self.config.attempt_cap_s

    def min_attempt_s(self, operation: str) -> float:
        return self.config.min_attempt_s

    def classify(self, capture: RawCapture, now: datetime) -> ConnectorError | None:
        if capture.status is None:
            return ConnectorError(ErrorClass.UPSTREAM, "no response")
        operation, arguments = capture.request.get("operation"), capture.request.get("arguments")
        if operation not in OPERATIONS or not isinstance(arguments, dict):
            return ConnectorError(ErrorClass.MALFORMED, "the capture does not name a known operation and its arguments")
        own = self.error_in_body(capture.status, capture.body, capture.headers, now)
        if own is not None:
            return own
        error_class = classify_status(capture.status)
        if error_class is not None:
            wait = parse_retry_after(capture.headers.get("retry-after"), now)
            return ConnectorError(error_class, f"HTTP {capture.status}: {self._snippet(capture.body)}", retry_after_s=wait)
        try:
            self._read(str(operation), arguments, capture.body)
        except (VenueDataError, jsonnum.JsonError, ConnectorError) as exc:
            return ConnectorError(ErrorClass.MALFORMED, f"{self.venue} {operation}: {exc}"[:MESSAGE_CUT])
        except Exception as exc:  # a body is hostile input: whatever shape it has, reading it must not take the gateway down
            return ConnectorError(ErrorClass.MALFORMED, f"{self.venue} {operation}: unreadable body ({type(exc).__name__})"[:MESSAGE_CUT])
        return None

    def rate_observation(self, capture: RawCapture) -> RateObservation | None:
        return self.observe_rate(capture.headers)

    # --- reading ---------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _snippet(body: bytes) -> str:
        return body[:MESSAGE_CUT].decode("utf-8", "replace").strip().replace("\n", " ")

    def _read(self, operation: str, arguments: Mapping[str, Any], body: bytes) -> object:
        if operation == "time":
            return self.parse_time_body(body)
        if operation == "candles":
            bars = self.parse_candles_body(body, arguments)
            check_bars(bars, _interval_of(arguments))
            return bars
        return self.parse_instruments_body(body, arguments)

    def parse(self, capture: RawCapture, server_time: datetime | None = None) -> datetime | CandleSeries | InstrumentList:
        """The meaning of a good capture. ``candles`` need the venue's server time (from a ``time`` capture, or ``date_header``)."""
        operation, arguments = capture.request["operation"], capture.request["arguments"]
        assert isinstance(operation, str) and isinstance(arguments, dict)
        if operation == "time":
            return self.parse_time_body(capture.body)
        if operation == "instruments":
            return self.parse_instruments_body(capture.body, arguments)
        if server_time is None:
            raise ValueError("candles can be read only against the venue's server time")
        bars = self.parse_candles_body(capture.body, arguments)
        start, end = parse_rfc3339(str(arguments["start"])), parse_rfc3339(str(arguments["end"]))
        return build_series(self.venue, str(arguments["instrument"]), _interval_of(arguments), bars, server_time, start, end)
